from hashlib import sha256
import json
from pathlib import Path
import zipfile

import pytest

from longform_engine.benchmark import (
    CHAPTER_ARTIFACT_PATHS,
    compare_benchmarks,
    init_benchmark,
    record_benchmark_chapter,
    record_rag_benchmark,
    report_benchmark,
    validate_benchmark,
)
from longform_engine.config import load_project_config
from longform_engine.storage import init_project


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def test_benchmark_init_validate_and_report_without_manuscript_body(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    initialized = init_benchmark(
        config,
        run_id="codex-smoke-5",
        agent_product="codex",
        chapters=5,
    )
    run_payload = json.loads((root / initialized.run_file).read_text(encoding="utf-8"))
    source_state = run_payload["source_state"]
    assert source_state["schema"] == "benchmark_source_state_v1"
    assert len(source_state["dirty_tree_sha256"]) == 64
    assert len(source_state["skill_sha256"]) == 64
    assert len(source_state["project_config_sha256"]) == 64

    validation = validate_benchmark(config, run_id=initialized.run_id)
    assert validation.ok
    assert not validation.complete
    assert not validation.acceptance_passed
    assert any("0/5" in warning for warning in validation.warnings)

    records_path = root / initialized.records_file
    records = json.loads(records_path.read_text(encoding="utf-8"))
    for record in records:
        record["generated"] = True
        record["scores"] = {
            "continuity": 4,
            "character_consistency": 4,
            "foreshadowing_control": 5,
            "pacing": 3,
            "reader_payoff": 4,
            "ai_taste": 2,
        }
        record["gate_passed"] = record["chapter_number"] != 3
        record["repair_count"] = 1 if record["chapter_number"] == 3 else 0
        record["need_human_count"] = 0
        record["context_file_count"] = 6
        record["context_character_count"] = 18000
        record["judge_ids"] = ["judge-a", "judge-b", "judge-c"]
    records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    completed = validate_benchmark(config, run_id=initialized.run_id)
    report = report_benchmark(config, run_id=initialized.run_id)
    payload = json.loads((root / report.report_json).read_text(encoding="utf-8"))

    assert completed.complete
    assert not completed.acceptance_passed
    assert any("chapters: 3" in failure for failure in completed.acceptance_failures)
    assert report.complete
    assert not report.acceptance_passed
    assert payload["chapters_recorded"] == 5
    assert payload["gate_failure_rate"] == 0.2
    assert payload["repair_count"] == 1
    assert payload["acceptance_passed"] is False
    assert payload["manuscript_bodies_included"] is False


def test_benchmark_rejects_manuscript_body_fields(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    initialized = init_benchmark(
        config,
        run_id="claude-quality-10",
        agent_product="claude-code",
        chapters=10,
    )
    records_path = root / initialized.records_file
    records = json.loads(records_path.read_text(encoding="utf-8"))
    records[0]["chapter_body"] = "This must never be stored in benchmark records."
    records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")

    validation = validate_benchmark(config, run_id=initialized.run_id)

    assert not validation.ok
    assert any("must not contain manuscript bodies" in error for error in validation.errors)


def test_technical_record_captures_and_revalidates_chapter_artifact_hashes(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    initialized = init_benchmark(
        config,
        run_id="codex-artifact-smoke",
        agent_product="codex",
        chapters=1,
    )
    artifact_files = (
        root / "50_workbench" / "writing_tasks" / "ch001.md",
        root / "50_workbench" / "writing_tasks" / "ch001.agent_task.json",
        root / "40_manuscript" / "draft" / "ch001.md",
        root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json",
    )
    for path in artifact_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"artifact:{path.name}", encoding="utf-8")

    record_benchmark_chapter(
        config,
        run_id=initialized.run_id,
        chapter_number=1,
        scores=None,
        gate_passed=True,
        repair_count=0,
        need_human_count=0,
        context_file_count=4,
        context_character_count=100,
        review_status="technical_pending",
        require_artifact_hashes=True,
    )
    records = json.loads((root / initialized.records_file).read_text(encoding="utf-8"))
    run = json.loads((root / initialized.run_file).read_text(encoding="utf-8"))

    assert set(records[0]["artifact_hashes"]) == {
        "work_order",
        "manifest",
        "reviewed_manuscript",
        "gate_result",
    }
    assert run["chapter_artifact_hashes_required"] is True
    assert validate_benchmark(config, run_id=initialized.run_id).acceptance_passed

    artifact_files[-1].write_text("changed gate", encoding="utf-8")
    validation = validate_benchmark(config, run_id=initialized.run_id)
    assert not validation.ok
    assert any("gate_result SHA-256 does not match" in error for error in validation.errors)


def test_technical_record_reads_compacted_chapter_artifacts_without_restore(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    initialized = init_benchmark(
        config,
        run_id="codex-compacted-smoke",
        agent_product="codex",
        chapters=1,
    )
    paths = {
        name: root / template.format(chapter=1)
        for name, template in CHAPTER_ARTIFACT_PATHS.items()
    }
    for name, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"artifact:{name}", encoding="utf-8")
    final = root / "40_manuscript" / "final" / "ch001.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(paths["reviewed_manuscript"].read_bytes())

    entries = []
    archive = root / "70_runtime" / "artifacts" / "chapters" / "ch001.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as handle:
        for name, path in paths.items():
            digest = sha256(path.read_bytes()).hexdigest()
            entry = {
                "path": path.relative_to(root).as_posix(),
                "sha256": digest,
                "size": path.stat().st_size,
            }
            if name == "reviewed_manuscript":
                entry["retained_role"] = "final"
            else:
                member = f"_audit/blobs/{digest}"
                entry["member"] = member
                handle.writestr(member, path.read_bytes())
            entries.append(entry)
        handle.writestr(
            "_audit/manifest.json",
            json.dumps(
                {
                    "schema": "chapter_artifact_archive_v3",
                    "chapter_number": 1,
                    "entries": entries,
                    "retained_evidence": [
                        {
                            "role": "final",
                            "path": "40_manuscript/final/ch001.md",
                            "sha256": sha256(final.read_bytes()).hexdigest(),
                            "size": final.stat().st_size,
                        }
                    ],
                }
            ),
        )
    for path in paths.values():
        path.unlink()

    record_benchmark_chapter(
        config,
        run_id=initialized.run_id,
        chapter_number=1,
        scores=None,
        gate_passed=True,
        repair_count=0,
        need_human_count=0,
        context_file_count=1,
        context_character_count=100,
        review_status="technical_pending",
        require_artifact_hashes=True,
    )

    validation = validate_benchmark(config, run_id=initialized.run_id)
    assert validation.ok, validation.errors
    assert validation.acceptance_passed


def test_fanfiction_benchmark_requires_all_quality_dimensions_before_recording(tmp_path):
    source = {
        "source_id": "source_a",
        "title": "Source A",
        "creator": "Creator A",
        "canon_cutoff": "volume-1",
        "allowed_elements": ["characters", "world"],
        "rights_status": "unverified",
        "commercial_intent": True,
        "platform_policy_url": "",
    }
    template = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "creation": {"mode": "fanfiction"},
            "fanfiction": {
                "continuity_mode": "continuation",
                "sources": [source],
            },
        },
    )
    project = init_project(template, output=tmp_path / "fanfiction")
    config = load_project_config(project.project_config)
    initialized = init_benchmark(
        config,
        run_id="fanfiction-smoke",
        agent_product="codex",
        chapters=1,
    )
    scores = {
        "continuity": 8,
        "character_consistency": 8,
        "foreshadowing_control": 8,
        "pacing": 8,
        "reader_payoff": 8,
        "ai_taste": 2,
    }

    with pytest.raises(ValueError, match="all six"):
        record_benchmark_chapter(
            config,
            run_id=initialized.run_id,
            chapter_number=1,
            scores=scores,
            fanfiction_scores={"canon_fidelity": 8},
            gate_passed=True,
            repair_count=0,
            need_human_count=0,
            context_file_count=6,
        )

    result = record_benchmark_chapter(
        config,
        run_id=initialized.run_id,
        chapter_number=1,
        scores=scores,
        fanfiction_scores={
            "canon_fidelity": 8,
            "ooc_control": 8,
            "original_contribution": 8,
            "divergence_causality": 8,
            "source_prose_originality": 10,
            "crossover_consistency": 8,
        },
        gate_passed=True,
        repair_count=0,
        need_human_count=0,
        context_file_count=6,
    )
    assert result.complete


def test_benchmark_record_and_compare_same_scenario(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    for run_id, product, continuity, ai_taste in (
        ("codex-quality-2", "codex", 5, 2),
        ("claude-quality-2", "claude-code", 4, 3),
    ):
        init_benchmark(
            config,
            run_id=run_id,
            agent_product=product,
            chapters=2,
            scenario_id="shared-setting-v1",
            agent_model=f"{product}-model",
            host_version=f"{product}-host",
        )
        for chapter in (1, 2):
            result = record_benchmark_chapter(
                config,
                run_id=run_id,
                chapter_number=chapter,
                scores={
                    "continuity": continuity,
                    "character_consistency": 4,
                    "foreshadowing_control": 4,
                    "pacing": 4,
                    "reader_payoff": 4,
                    "ai_taste": ai_taste,
                },
                gate_passed=True,
                repair_count=0,
                need_human_count=0,
                context_file_count=6,
                context_character_count=18000,
                judge_ids=["judge-a", "judge-b", "judge-c"],
                notes="Human evaluator record.",
            )
        assert result.complete
        assert validate_benchmark(config, run_id=run_id).acceptance_passed

    comparison = compare_benchmarks(
        config,
        comparison_id="codex-vs-claude-2",
        run_ids=["codex-quality-2", "claude-quality-2"],
    )
    payload = json.loads((root / comparison.comparison_json).read_text(encoding="utf-8"))

    assert payload["schema"] == "quality_benchmark_comparison_v2"
    assert payload["claim_eligible"] is False
    assert payload["claim_reasons"]
    assert payload["scenario_id"] == "shared-setting-v1"
    assert payload["manuscript_bodies_included"] is False
    assert payload["best_by_metric"]["continuity"] == "codex-quality-2"
    assert payload["best_by_metric"]["ai_taste"] == "codex-quality-2"


def test_benchmark_compare_rejects_incomplete_or_mismatched_runs(tmp_path):
    config = seed_project(tmp_path)
    init_benchmark(
        config,
        run_id="codex-incomplete",
        agent_product="codex",
        chapters=5,
        scenario_id="setting-a",
    )
    init_benchmark(
        config,
        run_id="claude-incomplete",
        agent_product="claude-code",
        chapters=5,
        scenario_id="setting-b",
    )

    with pytest.raises(ValueError, match="incomplete"):
        compare_benchmarks(
            config,
            comparison_id="strict-comparison",
            run_ids=["codex-incomplete", "claude-incomplete"],
        )
    with pytest.raises(ValueError, match="same scenario_id"):
        compare_benchmarks(
            config,
            comparison_id="provisional-comparison",
            run_ids=["codex-incomplete", "claude-incomplete"],
            allow_incomplete=True,
        )


def test_benchmark_record_rejects_oversized_annotations_without_writing(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    initialized = init_benchmark(
        config,
        run_id="safe-record",
        agent_product="codex",
        chapters=1,
    )
    records_path = root / initialized.records_file
    before = records_path.read_bytes()

    with pytest.raises(ValueError, match="notes"):
        record_benchmark_chapter(
            config,
            run_id=initialized.run_id,
            chapter_number=1,
            scores={
                "continuity": 4,
                "character_consistency": 4,
                "foreshadowing_control": 4,
                "pacing": 4,
                "reader_payoff": 4,
                "ai_taste": 2,
            },
            gate_passed=True,
            repair_count=0,
            need_human_count=0,
            context_file_count=5,
            notes="x" * 1001,
        )

    assert records_path.read_bytes() == before


def test_formal_superiority_claim_rejects_self_declared_judges_and_edited_rag_evidence(tmp_path):
    config = seed_project(tmp_path)
    scenario = tmp_path / "formal-scenario.json"
    scenario.write_text('{"schema":"quality_scenario_v1","id":"formal-setting-v1"}', encoding="utf-8")
    for run_id, product, score, ai_taste in (
        ("longform-formal-10", "codex", 9, 2),
        ("novel-skill-formal-10", "novel-skill", 8, 3),
    ):
        init_benchmark(
            config,
            run_id=run_id,
            agent_product=product,
            chapters=10,
            scenario_id="formal-setting-v1",
            scenario_file=scenario,
            agent_model="same-model-version",
            host_product="codex",
            host_version="same-host-version",
            workflow_version=f"{product}-workflow-v1",
        )
        for chapter in range(1, 11):
            record_benchmark_chapter(
                config,
                run_id=run_id,
                chapter_number=chapter,
                scores={
                    "continuity": score,
                    "character_consistency": score,
                    "foreshadowing_control": score,
                    "pacing": score,
                    "reader_payoff": score,
                    "ai_taste": ai_taste,
                },
                gate_passed=True,
                repair_count=0,
                need_human_count=0,
                context_file_count=6,
                context_character_count=18000,
                judge_ids=["judge-a", "judge-b", "judge-c"],
            )
    rag = record_rag_benchmark(
        config,
        run_id="longform-formal-10",
        scale_chapters=500,
        recall_at_k=0.9,
        fact_error_rate=0.01,
        p95_query_ms=500,
        incremental_index_ms=100,
    )
    comparison = compare_benchmarks(
        config,
        comparison_id="formal-claim",
        run_ids=["longform-formal-10", "novel-skill-formal-10"],
    )

    assert rag.meets_thresholds
    assert not comparison.claim_eligible
    assert any("manually recorded" in reason for reason in comparison.claim_reasons)

    evidence_path = (
        tmp_path
        / "novel"
        / "70_runtime"
        / "benchmarks"
        / "longform-formal-10"
        / "rag_scale_evidence.json"
    )
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["measurement_source"] = "engine_runner"
    evidence["evidence_grade"] = "production_model"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
    measured = compare_benchmarks(
        config,
        comparison_id="formal-claim-measured",
        run_ids=["longform-formal-10", "novel-skill-formal-10"],
    )

    assert not measured.claim_eligible
    assert any("blind-review aggregation" in reason for reason in measured.claim_reasons)
