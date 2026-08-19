from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from longform_engine.agent_protocols import (
    DESIGN_DOCUMENT_SCHEMA,
    EVIDENCE_REVIEW_SCHEMA,
    output_protocol_for_task,
    validate_evidence_review,
)
from longform_engine.agent_tasks import (
    build_manifest,
    list_manifests,
    load_manifest,
    task_reconciliation_status,
    update_task_status,
    write_manifest,
)
from longform_engine.artifacts import compact_artifacts, verify_artifacts
from longform_engine.chapter_contract import (
    ChapterContractError,
    load_verified_chapter_contract,
    resolve_chapter_contract_refs,
    stamp_chapter_contract,
)
from longform_engine.orchestration import (
    continue_write,
    finalize_chapter,
    open_book,
    plan_chapter,
    submit_agent_draft,
)
from longform_engine.production import production_next
from longform_engine.repair_coordination import (
    RepairCoordinationError,
    create_repair_candidate_task,
    create_repair_synthesis_task,
    record_repair_submission,
    validate_repair_plan,
)
from longform_engine.semantic import chapter_close, semantic_apply
from tests.project_fixtures import mark_project_ready, prepare_unified_semantic_bundle
from tests.test_agent_task_protocol import (
    passing_text,
    repair_plan_markdown,
    seed_project,
    write_blocking_gate,
)


def test_repair_parent_child_commit_is_idempotent(tmp_path):
    config, root, synthesis, candidate = prepare_repair_round(tmp_path)

    manifest = next(item for item in list_manifests(root, chapter_number=1) if item["task_id"] == synthesis["task_id"])
    context_input = next(
        item for item in manifest["io"]["inputs"] if str(item["path"]).endswith(".constraints.json")
    )
    context = read_json(root / context_input["path"])
    assert context["schema"] == "repair_synthesis_context_v2"
    assert sum(int(item["characters"]) for item in manifest["io"]["inputs"]) < 18000
    projected = {item["path"]: item["projection"] for item in context["constraints"]}
    assert "effective_quality_contract" not in projected["20_outline/chapter_cards/ch001.json"]
    assert "state_transitions" not in projected["30_state/tcs/ch001.json"]

    tasks = {item["task_id"]: item for item in list_manifests(root, chapter_number=1)}
    assert tasks[synthesis["task_id"]]["status"] == "applied"
    repeated = create_repair_candidate_task(config, chapter_number=1, agent="codex")
    assert repeated["task_id"] == candidate["task_id"]
    assert repeated["parent_plan_status"] == "applied"

    candidate_path = root / candidate["candidate_draft"]
    candidate_path.write_text(passing_text("REPAIR_R01"), encoding="utf-8")
    record_repair_submission(
        config,
        chapter_number=1,
        task_id=candidate["task_id"],
        source_path=candidate_path,
    )
    assert not any(item["task_id"].endswith(":r02:v4") for item in list_manifests(root, chapter_number=1))


def test_v042_dangling_parent_reconciles_then_chapter_closes(tmp_path):
    config, root, synthesis, candidate = prepare_repair_round(tmp_path)
    update_task_status(
        root,
        synthesis["task_id"],
        to_status="validated",
        command="fixture retired split",
    )
    action = production_next(config)
    assert action["status"] == "agent_task_lifecycle_reconciliation_required"

    reconciled = create_repair_candidate_task(config, chapter_number=1, agent="codex")
    assert reconciled["lifecycle_reconciled"] is True
    candidate_path = root / candidate["candidate_draft"]
    candidate_path.write_text(passing_text("RECOVERED_R01"), encoding="utf-8")
    submitted = submit_agent_draft(
        config,
        chapter_number=1,
        file_path=candidate_path,
        agent="codex",
        overwrite=True,
    )
    assert submitted.passed is True
    finalize_chapter(config, chapter_number=1, approved_by="test-owner")
    output = prepare_unified_semantic_bundle(root, config, 1)
    semantic_apply(config, chapter_number=1, file_path=output)
    closed = chapter_close(config, chapter_number=1, approved_by="test-owner")
    assert Path(closed.closure_file).is_file()


def test_repair_reconciliation_rejects_wrong_lineage_without_pollution(tmp_path):
    config, root, synthesis, _candidate = prepare_repair_round(tmp_path)
    update_task_status(
        root,
        synthesis["task_id"],
        to_status="validated",
        command="fixture retired split",
    )
    plan = root / "50_workbench" / "repair_plans" / "ch001" / "r01.plan.md"
    plan.write_text(plan.read_text(encoding="utf-8") + "\n篡改。\n", encoding="utf-8")
    before = protected_tree_hash(root)

    with pytest.raises(RepairCoordinationError, match="lineage is invalid"):
        create_repair_candidate_task(config, chapter_number=1, agent="codex")
    assert protected_tree_hash(root) == before
    assert production_next(config)["blocked_by"] == "agent_task_lineage_ambiguous"


def test_semantic_rag_materialization_and_vector_failure_rollback(tmp_path, monkeypatch):
    import longform_engine.rag.pipeline as rag_pipeline

    ready = SimpleNamespace(
        status="ready",
        embedding_model="fixture-embedding",
        fallback="",
        fallback_active=False,
        embedding_loadable=True,
        reranker_loadable=True,
        profile="fixture",
    )
    monkeypatch.setattr(rag_pipeline, "ensure_models_ready", lambda *args, **kwargs: ready)
    monkeypatch.setattr(rag_pipeline, "embed_text_with_provider", fixture_embedding)
    monkeypatch.setattr(rag_pipeline, "rerank_pair", lambda *args, **kwargs: 0.8)

    config, root, output = prepare_finalized_semantic_project(tmp_path / "success")
    semantic_context = read_json(root / "50_workbench" / "semantic_tasks" / "ch001.semantic_context.json")
    assert semantic_context["schema"] == "chapter_semantic_context_v2"
    assert "previous_state" not in semantic_context
    applied = semantic_apply(config, chapter_number=1, file_path=output)
    assert applied.embedding_records > 0
    assert applied.active_vectors > 0
    assert applied.semantic_hits > 0
    assert applied.fallback_active is False
    context = root / "60_rag" / "context" / "next_plot_context.md"
    assert "- Semantic mode: enabled" in context.read_text(encoding="utf-8")
    chapter_close(config, chapter_number=1, approved_by="test-owner")
    continue_write(config, chapter_number=2)
    next_context = context.read_text(encoding="utf-8")
    assert "- Semantic mode: enabled" in next_context
    assert "## Retrieval Hits\n\nNo retrieval hits yet." not in next_context

    failing_config, failing_root, failing_output = prepare_finalized_semantic_project(tmp_path / "failure")
    before = protected_tree_hash(failing_root)
    db_before = database_state(failing_root)
    original_sync = rag_pipeline.sync_source_records

    def sync_then_fail(config, records, *, source_paths):
        original_sync(config, records, source_paths=source_paths)
        raise RuntimeError("simulated vector commit failure")

    monkeypatch.setattr(rag_pipeline, "sync_source_records", sync_then_fail)
    with pytest.raises(RuntimeError, match="simulated vector commit failure"):
        semantic_apply(failing_config, chapter_number=1, file_path=failing_output)
    assert protected_tree_hash(failing_root) == before
    assert database_state(failing_root) == db_before
    assert not (failing_root / "30_state" / "semantic_ledger" / "ch001.json").exists()
    assert not (failing_root / "60_rag" / "chunks" / "ch001.json").exists()


def test_review_pass_requires_positive_coverage_evidence():
    errors = validate_evidence_review(
        {
            "schema": EVIDENCE_REVIEW_SCHEMA,
            "verdict": "pass",
            "coverage": {
                "canonical_fact": {
                    "status": "checked",
                    "evidence_ids": [],
                    "canonical_refs": [],
                }
            },
            "findings": [],
        },
        required_dimensions=("canonical_fact",),
        canonical_ref_dimensions=("canonical_fact",),
    )
    assert any("requires one or two evidence IDs" in item for item in errors)
    assert any("requires at least one canonical ref" in item for item in errors)


def test_core_contract_ref_rejects_depth_limited_content(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    source = root / "10_bible" / "core_rule.md"
    source.write_text("# Core rule\n\n[depth-limited]\n", encoding="utf-8")
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["canon_refs"] = ["10_bible/core_rule.md"]
    stamp_chapter_contract(card)
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contract, _digest = load_verified_chapter_contract(root, 1)
    with pytest.raises(ChapterContractError, match="context_evidence_incomplete:depth_limited"):
        resolve_chapter_contract_refs(root, contract)


def test_chapter_contract_rejects_removed_alias_instead_of_normalizing_it(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["duty"] = card["chapter_duty"]

    with pytest.raises(ChapterContractError, match="chapter_contract_inconsistent:removed_alias_present:duty"):
        stamp_chapter_contract(card)


def test_parent_child_consumption_is_generic_and_hash_bound(tmp_path):
    _config = seed_project(tmp_path)
    root = tmp_path / "novel"
    parent_brief = root / "50_workbench" / "parent.md"
    parent_result = root / "50_workbench" / "gate_artifacts" / "ch001" / "pacing_result.json"
    child_brief = root / "50_workbench" / "child.md"
    child_result = root / "50_workbench" / "repair_candidates" / "ch001.r01.codex.md"
    parent_brief.write_text("# Parent\n", encoding="utf-8")
    parent_result.parent.mkdir(parents=True, exist_ok=True)
    parent_result.write_text("{}\n", encoding="utf-8")
    child_brief.write_text("# Child\n", encoding="utf-8")
    parent = build_manifest(
        root,
        task_type="pacing_review",
        chapter_number=1,
        input_files=[parent_brief],
        allowed_output_paths=[parent_result],
        output_schema=output_protocol_for_task("pacing_review"),
        validate_command="longform-engine pacing semantic-validate project.yaml --chapter 1",
        apply_command="longform-engine pacing semantic-apply project.yaml --chapter 1",
        failure_next_command="longform-engine pacing semantic-task project.yaml --chapter 1",
    )
    write_manifest(root, parent, "50_workbench/parent.agent_task.json")
    digest = sha256(parent_result.read_bytes()).hexdigest()
    update_task_status(
        root,
        parent["task_id"],
        to_status="validated",
        command="validate parent",
        current_result={
            "ok": True,
            "path": "50_workbench/gate_artifacts/ch001/pacing_result.json",
            "sha256": digest,
            "diagnostic_file": "",
            "source_schema": EVIDENCE_REVIEW_SCHEMA,
            "validated_at": "2026-08-18T00:00:00Z",
        },
    )
    child = build_manifest(
        root,
        task_type="repair",
        chapter_number=1,
        input_files=[child_brief, parent_result],
        allowed_output_paths=[child_result],
        output_schema=output_protocol_for_task("repair"),
        validate_command="longform-engine draft submit project.yaml --chapter 1 --file 50_workbench/repair_candidates/ch001.r01.codex.md --agent codex --overwrite",
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine repair candidate-task project.yaml --chapter 1 --agent codex",
    )
    write_manifest(
        root,
        child,
        "50_workbench/child.agent_task.json",
        consumes_task_id=parent["task_id"],
    )
    assert load_manifest(root, parent["task_id"])["status"] == "applied"
    parent_result.write_text('{"drift": true}\n', encoding="utf-8")
    status = task_reconciliation_status(root, chapter_number=1)
    assert status["status"] == "need_human"
    assert status["errors"]


def test_project_setup_compaction_archives_only_noncanonical_workbench(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    brief = root / "50_workbench" / "intelligence_tasks" / "book_design.project.md"
    candidate = root / "50_workbench" / "intelligence_candidates" / "book_design.project.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("# Book design task\n", encoding="utf-8")
    candidate.write_text("# Approved book design\n\n## 主线\nA causal promise.\n", encoding="utf-8")
    manifest = build_manifest(
        root,
        task_type="book_design",
        chapter_number=None,
        scope={"kind": "project"},
        input_files=[brief],
        allowed_output_paths=[candidate],
        output_schema=DESIGN_DOCUMENT_SCHEMA,
        validate_command="longform-engine intelligence validate project.yaml --task-type book_design --file 50_workbench/intelligence_candidates/book_design.project.md",
        apply_command="longform-engine intelligence apply project.yaml --task-type book_design --file 50_workbench/intelligence_candidates/book_design.project.md --approved-by human",
        failure_next_command="longform-engine intelligence task project.yaml --task-type book_design",
    )
    write_manifest(root, manifest, "50_workbench/intelligence_tasks/book_design.project.agent_task.json")
    update_task_status(root, manifest["task_id"], to_status="applied", command="apply book")
    dry_run = compact_artifacts(config, scope="project-setup", dry_run=True)
    assert dry_run.eligible and dry_run.candidate_files == 3
    result = compact_artifacts(config, scope="project-setup", dry_run=False)
    assert result.candidate_bytes == dry_run.candidate_bytes > 0
    assert not brief.exists() and not candidate.exists()
    assert (root / "70_runtime" / "artifacts" / "project-setup.zip").is_file()
    assert verify_artifacts(config).ok


def prepare_repair_round(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    tcs = root / "30_state" / "tcs" / "ch001.json"
    tcs.parent.mkdir(parents=True, exist_ok=True)
    tcs.write_text(
        json.dumps(
            {
                "schema": "tcs_v1",
                "active_constraints": ["治疗规则不得在救援动作中无依据改变"],
                "state_transitions": [{"historical_detail": "x" * 30000}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\n治疗规则与救援动作冲突。\n", encoding="utf-8")
    write_blocking_gate(root, draft, chapter_number=1)
    synthesis = create_repair_synthesis_task(config, chapter_number=1)
    bundle = read_json(root / synthesis["review_bundle"])
    finding_id = bundle["blocking_finding_ids"][0]
    plan = root / synthesis["plan_file"]
    plan.write_text(repair_plan_markdown(bundle, finding_id), encoding="utf-8")
    assert validate_repair_plan(config, chapter_number=1, file_path=plan)["ok"] is True
    candidate = create_repair_candidate_task(config, chapter_number=1, agent="codex")
    return config, root, synthesis, candidate


def prepare_finalized_semantic_project(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    continue_write(config, chapter_number=1)
    candidate = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    candidate.write_text(passing_text("SEMANTIC_VECTOR"), encoding="utf-8")
    assert submit_agent_draft(config, chapter_number=1, file_path=candidate, agent="codex").passed is True
    finalize_chapter(config, chapter_number=1, approved_by="test-owner")
    return config, root, prepare_unified_semantic_bundle(root, config, 1)


def fixture_embedding(_config, text, *, dims=96):
    digest = sha256(text.encode("utf-8")).digest()
    return [float(digest[index % len(digest)]) / 255.0 for index in range(16)]


def protected_tree_hash(root: Path) -> dict[str, str]:
    protected = ("10_bible", "20_outline", "30_state", "40_manuscript/final", "60_rag")
    return {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for prefix in protected
        for path in sorted((root / prefix).rglob("*"))
        if path.is_file()
    }


def database_state(root: Path) -> dict[str, str]:
    import sqlite3

    result = {}
    for path in sorted((root / "70_runtime" / "db").glob("*.sqlite")):
        with sqlite3.connect(path) as connection:
            logical_dump = "\n".join(connection.iterdump())
        result[path.name] = sha256(logical_dump.encode("utf-8")).hexdigest()
    return result


def read_json(path: Path):
    import json

    return json.loads(path.read_text(encoding="utf-8"))
