import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.benchmark import (
    RAG_REQUIRED_CATEGORIES,
    compare_benchmarks,
    init_benchmark,
    record_benchmark_chapter,
)
from longform_engine.blind_review import (
    aggregate_blind_reviews,
    attach_benchmark_source,
    create_blind_review_pack,
    create_blind_review_template,
    literary_evidence_status,
    submit_blind_review,
)
from longform_engine.config import load_project_config
from longform_engine.storage import init_project


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def seed_formal_pair(tmp_path: Path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    scenario = tmp_path / "scenario.json"
    scenario.write_text('{"schema":"quality_scenario_v1","id":"shared"}\n', encoding="utf-8")
    for run_id, host in (
        ("codex-current-3", "codex"),
        ("codex-baseline-3", "codex"),
    ):
        init_benchmark(
            config,
            run_id=run_id,
            host_product=host,
            chapters=3,
            scenario_id="shared-setting-v1",
            scenario_file=scenario,
            agent_model="same-model",
            host_version="same-host",
            workflow_version="same-generation-conditions",
        )
        if run_id == "codex-baseline-3":
            run_file = root / "70_runtime" / "benchmarks" / run_id / "run.json"
            run_payload = json.loads(run_file.read_text(encoding="utf-8"))
            run_payload["engine_version"] = "0.5.0"
            run_file.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        source_dir = tmp_path / run_id
        source_dir.mkdir()
        for chapter in range(1, 4):
            version_label = "甲稿" if run_id == "codex-current-3" else "乙稿"
            (source_dir / f"ch{chapter:03d}.md").write_text(
                f"# 第{chapter}章\n\n{version_label}的独立正文证据，第 {chapter} 章。",
                encoding="utf-8",
            )
            record_benchmark_chapter(
                config,
                run_id=run_id,
                chapter_number=chapter,
                scores=None,
                gate_passed=True,
                repair_count=0,
                need_human_count=0,
                context_file_count=6,
                context_character_count=18000,
                review_status="technical_pending",
            )
        attach_benchmark_source(config, run_id=run_id, source_dir=source_dir)
    return config, root


def add_literary_scope_pair(
    config,
    root: Path,
    tmp_path: Path,
    *,
    review_scope: str,
    chapters: int,
    market: str,
    confirmed_failure: str = "",
):
    config.data["story_profile"]["market"]["primary"] = market
    scenario = tmp_path / f"{review_scope}.scenario.json"
    scenario.write_text(
        json.dumps({"schema": "quality_scenario_v1", "id": review_scope}) + "\n",
        encoding="utf-8",
    )
    candidate_id = f"candidate-{review_scope.replace('_', '-')}"
    baseline_id = f"baseline-{review_scope.replace('_', '-')}"
    for run_id in (candidate_id, baseline_id):
        init_benchmark(
            config,
            run_id=run_id,
            host_product="codex",
            chapters=chapters,
            scenario_id=f"shared-{review_scope}",
            scenario_file=scenario,
            agent_model="same-model",
            host_version="same-host",
            workflow_version="same-generation-conditions",
        )
        if run_id == baseline_id:
            run_file = root / "70_runtime" / "benchmarks" / run_id / "run.json"
            run_payload = json.loads(run_file.read_text(encoding="utf-8"))
            run_payload["engine_version"] = "0.5.0"
            run_file.write_text(json.dumps(run_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        source_dir = tmp_path / f"source-{run_id}"
        source_dir.mkdir()
        for chapter in range(1, chapters + 1):
            (source_dir / f"ch{chapter:03d}.md").write_text(
                f"# 第{chapter}章\n\n{run_id} 在相同场景中的独立正文证据，第 {chapter} 章。",
                encoding="utf-8",
            )
            record_benchmark_chapter(
                config,
                run_id=run_id,
                chapter_number=chapter,
                scores=None,
                gate_passed=True,
                repair_count=0,
                need_human_count=0,
                context_file_count=6,
                context_character_count=18000,
                review_status="technical_pending",
            )
        attach_benchmark_source(config, run_id=run_id, source_dir=source_dir)
    comparison_id = f"comparison-{review_scope.replace('_', '-')}"
    pack = create_blind_review_pack(
        config,
        comparison_id=comparison_id,
        run_ids=[candidate_id, baseline_id],
        seed=f"seed-{review_scope}",
        review_scope=review_scope,
    )
    mapping = json.loads((root / pack.private_mapping_file).read_text(encoding="utf-8"))["mapping"]
    for number in range(1, 4):
        template = create_blind_review_template(
            config,
            comparison_id=comparison_id,
            judge_id=f"{review_scope}-judge-{number}",
        )
        template_path = root / template.template_file
        complete_submission(
            template_path,
            private_mapping=mapping,
            candidate_id=candidate_id,
            judge_number=number,
        )
        if confirmed_failure and number <= 2:
            payload = json.loads(template_path.read_text(encoding="utf-8"))
            candidate_blind_id = next(
                blind_id for blind_id, run_id in mapping.items() if run_id == candidate_id
            )
            payload["long_term_failure_modes"] = [
                {
                    "blind_id": candidate_blind_id,
                    "code": confirmed_failure,
                    "note": "The same long-term failure is independently visible across the serial scope.",
                }
            ]
            template_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        submit_blind_review(
            config,
            comparison_id=comparison_id,
            judge_id=f"{review_scope}-judge-{number}",
            file_path=template_path,
        )
    aggregate_blind_reviews(config, comparison_id=comparison_id)
    return candidate_id, baseline_id


def complete_submission(
    template_path: Path,
    *,
    private_mapping: dict[str, str],
    candidate_id: str,
    judge_number: int,
) -> None:
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    payload["reviewer"] = {
        "kind": "human",
        "product": "human",
        "version": "",
        "instance_id": f"independent-reviewer-{judge_number}",
        "session_id": f"independent-session-{judge_number}",
    }
    for entry in payload["entries"]:
        candidate = private_mapping[entry["blind_id"]] == candidate_id
        for chapter in entry["chapters"]:
            literary_score = 9 if candidate else 7
            chapter["scores"] = {
                metric: (2 if candidate else 3) if metric == "ai_taste" else literary_score
                for metric in chapter["scores"]
            }
            chapter["confidence"] = 0.9
            chapter["notes"] = "Independent blind score."
        if candidate:
            payload["overall_preference"] = {
                "blind_id": entry["blind_id"],
                "reason": "关键转折、人物主动性和读者收益更清楚。",
            }
    template_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_production_rag_evidence(root: Path, run_id: str) -> None:
    evidence = {
        "schema": "rag_scale_evidence_v1",
        "run_id": run_id,
        "measurement_source": "engine_runner",
        "evidence_grade": "production_model",
        "scale_chapters": 500,
        "source_chapter_count": 500,
        "source_merkle_root": "a" * 64,
        "dataset_id": "production-rag-v1",
        "dataset_sha256": "b" * 64,
        "query_count": 50,
        "category_counts": {category: 1 for category in RAG_REQUIRED_CATEGORIES},
        "top_k": 10,
        "recall_at_k": 0.9,
        "fact_error_rate": 0.01,
        "p95_query_ms": 500,
        "incremental_index_ms": 100,
        "incremental_index_mode": "test engine-runner fixture",
        "embedding_model": "production-embedding",
        "reranker_model": "production-reranker",
        "fallback_active": False,
        "vector_backend": "local_hnsw",
        "backend_config_hash": "c" * 64,
    }
    path = root / "70_runtime" / "benchmarks" / run_id / "rag_scale_evidence.json"
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")


def canonical_hashes(root: Path) -> dict[str, str]:
    result = {}
    for directory in ("10_bible", "20_outline", "30_state", "40_manuscript/final", "60_rag", "70_runtime/db"):
        for path in sorted((root / directory).rglob("*")):
            if path.is_file():
                result[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return result


def test_formal_blind_review_aggregates_three_independent_judges(tmp_path):
    config, root = seed_formal_pair(tmp_path)
    pack = create_blind_review_pack(
        config,
        comparison_id="engine-formal-run",
        run_ids=["codex-current-3", "codex-baseline-3"],
        seed="fixed-private-seed",
        review_scope="qidian_opening_3",
    )
    public_manifest = (
        root / pack.public_dir / "manifest.json"
    ).read_text(encoding="utf-8")
    assert "codex-current-3" not in public_manifest
    assert "codex-baseline-3" not in public_manifest
    private_payload = json.loads((root / pack.private_mapping_file).read_text(encoding="utf-8"))

    for number in range(1, 4):
        template = create_blind_review_template(
            config,
            comparison_id=pack.comparison_id,
            judge_id=f"judge-{number}",
        )
        template_path = root / template.template_file
        complete_submission(
            template_path,
            private_mapping=private_payload["mapping"],
            candidate_id="codex-current-3",
            judge_number=number,
        )
        submit_blind_review(
            config,
            comparison_id=pack.comparison_id,
            judge_id=f"judge-{number}",
            file_path=template_path,
        )

    aggregate = aggregate_blind_reviews(config, comparison_id=pack.comparison_id)
    write_production_rag_evidence(root, "codex-current-3")
    write_production_rag_evidence(root, "codex-baseline-3")
    comparison = compare_benchmarks(
        config,
        comparison_id="engine-formal-result",
        run_ids=["codex-current-3", "codex-baseline-3"],
    )

    assert aggregate.judge_count == 3
    assert not comparison.quality_evidence_complete
    assert any("at least 10 chapters" in reason for reason in comparison.evidence_gaps)
    scope_evidence = json.loads(
        (root / "70_runtime" / "literary_evidence" / "qidian_opening_3.json").read_text(
            encoding="utf-8"
        )
    )
    assert scope_evidence["conclusion"] == "pass"
    assert scope_evidence["stores_manuscript_body"] is False
    assert len(scope_evidence["assessment_sha256"]) == 64
    records = json.loads(
        (
            root
            / "70_runtime"
            / "benchmarks"
            / "codex-current-3"
            / "chapter_records.json"
        ).read_text(encoding="utf-8")
    )
    assert all(record["review_status"] == "blind_aggregated" for record in records)
    assert all(record["scores"]["continuity"] == 9 for record in records)

    (tmp_path / "codex-current-3" / "ch001.md").write_text(
        "source changed after formal aggregation",
        encoding="utf-8",
    )
    changed = compare_benchmarks(
        config,
        comparison_id="engine-formal-source-changed",
        run_ids=["codex-current-3", "codex-baseline-3"],
    )
    assert not changed.quality_evidence_complete
    assert any("changed after attachment" in reason for reason in changed.evidence_gaps)


def test_blind_aggregate_rejects_too_few_or_duplicate_reviewers(tmp_path):
    config, root = seed_formal_pair(tmp_path)
    pack = create_blind_review_pack(
        config,
        comparison_id="insufficient-panel",
        run_ids=["codex-current-3", "codex-baseline-3"],
        seed="seed",
        review_scope="qidian_opening_3",
    )
    mapping = json.loads((root / pack.private_mapping_file).read_text(encoding="utf-8"))["mapping"]
    for number in range(1, 3):
        template = create_blind_review_template(
            config,
            comparison_id=pack.comparison_id,
            judge_id=f"judge-{number}",
        )
        path = root / template.template_file
        complete_submission(
            path,
            private_mapping=mapping,
            candidate_id="codex-current-3",
            judge_number=number,
        )
        submit_blind_review(
            config,
            comparison_id=pack.comparison_id,
            judge_id=f"judge-{number}",
            file_path=path,
        )

    with pytest.raises(ValueError, match="at least three"):
        aggregate_blind_reviews(config, comparison_id=pack.comparison_id)

    third = create_blind_review_template(
        config,
        comparison_id=pack.comparison_id,
        judge_id="judge-3",
    )
    third_path = root / third.template_file
    complete_submission(
        third_path,
        private_mapping=mapping,
        candidate_id="codex-current-3",
        judge_number=2,
    )
    payload = json.loads(third_path.read_text(encoding="utf-8"))
    payload["reviewer"]["session_id"] = "independent-session-3"
    third_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    submit_blind_review(
        config,
        comparison_id=pack.comparison_id,
        judge_id="judge-3",
        file_path=third_path,
    )

    with pytest.raises(ValueError, match="instance_id"):
        aggregate_blind_reviews(config, comparison_id=pack.comparison_id)


def test_blind_pack_detects_source_change_and_submission_identity_leak(tmp_path):
    config, root = seed_formal_pair(tmp_path)
    changed = tmp_path / "codex-current-3" / "ch003.md"
    changed.write_text("changed after source attachment", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after attachment"):
        create_blind_review_pack(
            config,
            comparison_id="changed-source",
            run_ids=["codex-current-3", "codex-baseline-3"],
            seed="seed",
            review_scope="qidian_opening_3",
        )

    attach_benchmark_source(
        config,
        run_id="codex-current-3",
        source_dir=tmp_path / "codex-current-3",
    )
    pack = create_blind_review_pack(
        config,
        comparison_id="identity-leak",
        run_ids=["codex-current-3", "codex-baseline-3"],
        seed="seed-2",
        review_scope="qidian_opening_3",
    )
    public_chapter = root / pack.public_dir / pack.blind_ids[0] / "ch001.md"
    original_public_text = public_chapter.read_text(encoding="utf-8")
    public_chapter.write_text("tampered public review prose", encoding="utf-8")
    with pytest.raises(ValueError, match="public chapter hash"):
        create_blind_review_template(
            config,
            comparison_id=pack.comparison_id,
            judge_id="tamper-check",
        )
    public_chapter.write_text(original_public_text, encoding="utf-8")
    template = create_blind_review_template(
        config,
        comparison_id=pack.comparison_id,
        judge_id="judge-a",
    )
    path = root / template.template_file
    mapping = json.loads((root / pack.private_mapping_file).read_text(encoding="utf-8"))["mapping"]
    complete_submission(
        path,
        private_mapping=mapping,
        candidate_id="codex-current-3",
        judge_number=1,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["run_id"] = "codex-current-3"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = canonical_hashes(root)

    with pytest.raises(ValueError, match="engine identity"):
        submit_blind_review(
            config,
            comparison_id=pack.comparison_id,
            judge_id="judge-a",
            file_path=path,
        )
    assert canonical_hashes(root) == before


def test_three_literary_scopes_build_tamper_sensitive_manifest(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    source_pairs = []
    source_pairs.append(
        add_literary_scope_pair(
            config, root, tmp_path,
            review_scope="qidian_opening_3", chapters=3, market="qidian_male",
        )
    )
    source_pairs.append(
        add_literary_scope_pair(
            config, root, tmp_path,
            review_scope="fanqie_opening_3", chapters=3, market="fanqie_free",
        )
    )
    source_pairs.append(
        add_literary_scope_pair(
            config, root, tmp_path,
            review_scope="serial_arc_15", chapters=15, market="qidian_male",
        )
    )

    ready, blockers = literary_evidence_status(root)
    assert ready, blockers
    manifest_path = root / "70_runtime" / "literary_evidence" / "manifest.json"
    original_manifest = manifest_path.read_bytes()
    manifest = json.loads(original_manifest)
    assert manifest["schema"] == "literary_evidence_manifest_v1"
    assert {item["review_scope"] for item in manifest["scopes"]} == {
        "qidian_opening_3", "fanqie_opening_3", "serial_arc_15",
    }

    manifest["reviewer_count"] += 1
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    ready, blockers = literary_evidence_status(root)
    assert not ready
    assert "literary_evidence_manifest_hash_invalid" in blockers

    manifest_path.write_bytes(original_manifest)
    candidate_id, _baseline_id = source_pairs[0]
    source = tmp_path / f"source-{candidate_id}" / "ch001.md"
    source.write_text("tampered after literary evidence aggregation", encoding="utf-8")
    ready, blockers = literary_evidence_status(root)
    assert not ready
    assert "literary_evidence_live_provenance_invalid:qidian_opening_3" in blockers


def test_serial_scope_fails_when_two_reviewers_confirm_long_term_pattern(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    add_literary_scope_pair(
        config,
        root,
        tmp_path,
        review_scope="serial_arc_15",
        chapters=15,
        market="qidian_male",
        confirmed_failure="RESTART_LOOP",
    )
    evidence = json.loads(
        (root / "70_runtime" / "literary_evidence" / "serial_arc_15.json").read_text(
            encoding="utf-8"
        )
    )
    assert evidence["conclusion"] == "fail"
    ready, blockers = literary_evidence_status(root)
    assert not ready
    assert blockers == ["literary_evidence_manifest_missing"]
