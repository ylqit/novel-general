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
    for run_id, product, workflow in (
        ("codex-longform-10", "codex", "longform-v0.3"),
        ("codex-novel-skill-10", "novel-skill", "novel-skill-v1"),
    ):
        init_benchmark(
            config,
            run_id=run_id,
            agent_product=product,
            chapters=10,
            scenario_id="phase6-shared-setting",
            scenario_file=scenario,
            agent_model="same-model",
            host_product="codex",
            host_version="same-host",
            workflow_version=workflow,
        )
        source_dir = tmp_path / run_id
        source_dir.mkdir()
        for chapter in range(1, 11):
            version_label = "甲稿" if product == "codex" else "乙稿"
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
            literary_score = 9 if candidate else 8
            chapter["scores"] = {
                "continuity": literary_score,
                "character_consistency": literary_score,
                "foreshadowing_control": literary_score,
                "pacing": literary_score,
                "reader_payoff": literary_score,
                "ai_taste": 2 if candidate else 3,
            }
            chapter["confidence"] = 0.9
            chapter["notes"] = "Independent blind score."
    template_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_claim_grade_rag_evidence(root: Path, run_id: str) -> None:
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
        comparison_id="codex-formal-phase6",
        run_ids=["codex-longform-10", "codex-novel-skill-10"],
        seed="fixed-private-seed",
    )
    public_manifest = (
        root / pack.public_dir / "manifest.json"
    ).read_text(encoding="utf-8")
    assert "codex-longform-10" not in public_manifest
    assert "codex-novel-skill-10" not in public_manifest
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
            candidate_id="codex-longform-10",
            judge_number=number,
        )
        submit_blind_review(
            config,
            comparison_id=pack.comparison_id,
            judge_id=f"judge-{number}",
            file_path=template_path,
        )

    aggregate = aggregate_blind_reviews(config, comparison_id=pack.comparison_id)
    write_claim_grade_rag_evidence(root, "codex-longform-10")
    comparison = compare_benchmarks(
        config,
        comparison_id="codex-formal-phase6-result",
        run_ids=["codex-longform-10", "codex-novel-skill-10"],
    )

    assert aggregate.judge_count == 3
    assert comparison.claim_eligible
    assert not comparison.claim_reasons
    records = json.loads(
        (
            root
            / "70_runtime"
            / "benchmarks"
            / "codex-longform-10"
            / "chapter_records.json"
        ).read_text(encoding="utf-8")
    )
    assert all(record["review_status"] == "blind_aggregated" for record in records)
    assert all(record["scores"]["continuity"] == 9 for record in records)

    (tmp_path / "codex-longform-10" / "ch001.md").write_text(
        "source changed after formal aggregation",
        encoding="utf-8",
    )
    changed = compare_benchmarks(
        config,
        comparison_id="codex-formal-phase6-source-changed",
        run_ids=["codex-longform-10", "codex-novel-skill-10"],
    )
    assert not changed.claim_eligible
    assert any("changed after attachment" in reason for reason in changed.claim_reasons)


def test_blind_aggregate_rejects_too_few_or_duplicate_reviewers(tmp_path):
    config, root = seed_formal_pair(tmp_path)
    pack = create_blind_review_pack(
        config,
        comparison_id="insufficient-panel",
        run_ids=["codex-longform-10", "codex-novel-skill-10"],
        seed="seed",
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
            candidate_id="codex-longform-10",
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
        candidate_id="codex-longform-10",
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
    changed = tmp_path / "codex-longform-10" / "ch010.md"
    changed.write_text("changed after source attachment", encoding="utf-8")
    with pytest.raises(ValueError, match="changed after attachment"):
        create_blind_review_pack(
            config,
            comparison_id="changed-source",
            run_ids=["codex-longform-10", "codex-novel-skill-10"],
            seed="seed",
        )

    attach_benchmark_source(
        config,
        run_id="codex-longform-10",
        source_dir=tmp_path / "codex-longform-10",
    )
    pack = create_blind_review_pack(
        config,
        comparison_id="identity-leak",
        run_ids=["codex-longform-10", "codex-novel-skill-10"],
        seed="seed-2",
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
        candidate_id="codex-longform-10",
        judge_number=1,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["run_id"] = "codex-longform-10"
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
