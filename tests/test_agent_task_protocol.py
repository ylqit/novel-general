import json
from hashlib import sha256
from pathlib import Path
import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import CANONICAL_DELTA_SCHEMA, EVIDENCE_REVIEW_SCHEMA, PROSE_MARKDOWN_SCHEMA
from longform_engine.agent_tasks import (
    AGENT_TASK_STATUSES,
    AgentTaskContractError,
    build_manifest,
    list_manifests,
    load_manifest,
    status_summary,
    update_task_status,
    validate_manifest_strict,
    write_manifest,
)
from longform_engine.config import load_project_config
from longform_engine.creative import expand_task, humanize_task
from longform_engine.editorial import editorial_aggregate, editorial_review, editorial_submit_review
from longform_engine.gates import GateError, gate_check, semantic_pacing_apply, semantic_pacing_task, semantic_pacing_validate
from longform_engine.orchestration import WorkflowError, continue_write, finalize_chapter, open_book, plan_chapter, submit_agent_draft
from longform_engine.production import production_next
from longform_engine.repair_coordination import (
    create_repair_candidate_task,
    create_repair_synthesis_task,
    next_repair_round,
    record_repair_submission,
    review_barrier_status,
    validate_repair_plan,
)
from longform_engine.roles import load_role_registry
from longform_engine.semantic import semantic_task
from longform_engine.storage import init_project, resolve_project_root
from tests.project_fixtures import mark_project_ready


def test_no_key_agent_task_chapter_loop_and_manifest_index(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "GLM_API_KEY", "MINIMAX_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)

    task = continue_write(config, chapter_number=1)
    draft_path = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    draft_path.write_text(passing_text("SAFE_AGENT_TASK"), encoding="utf-8")
    submitted = submit_agent_draft(config, chapter_number=1, file_path=draft_path, agent="codex")
    finalized = finalize_chapter(config, chapter_number=1, approved_by="human")

    manifest = root / "50_workbench" / "writing_tasks" / "ch001.agent_task.json"
    indexed = list_manifests(root, chapter_number=1)
    summary = status_summary(root, chapter_number=1)

    assert task.status == "task_ready"
    assert submitted.passed is True
    assert finalized.final_file.endswith("ch001.md")
    assert manifest.exists()
    assert json.loads(manifest.read_text(encoding="utf-8"))["task_type"] == "chapter_write"
    assert any(item["task_type"] == "chapter_write" for item in indexed)
    assert summary["by_status"]["applied"] >= 1
    assert "status" not in json.loads(manifest.read_text(encoding="utf-8"))
    assert next(item for item in indexed if item["task_type"] == "chapter_write")["status"] == "applied"
    events = event_payloads(root)
    assert [item["to_status"] for item in events if item["task_id"] == "chapter_write:ch001:v4"] == [
        "awaiting_agent",
        "submitted",
        "applied",
    ]
    assert events[-1]["command"] == "chapter finalize"
    strict = validate_manifest_strict(root, json.loads(manifest.read_text(encoding="utf-8")))
    assert strict.ok, strict.errors
    assert not strict.warnings


def test_finalize_applies_submitted_candidate_and_supersedes_unused_repair(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    continue_write(config, chapter_number=1)
    draft_path = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    draft_path.write_text(passing_text("FINALIZED_AFTER_SEMANTIC_GATE"), encoding="utf-8")
    submitted = submit_agent_draft(config, chapter_number=1, file_path=draft_path, agent="codex")
    assert submitted.passed is True

    repair_output = root / "50_workbench" / "repair_candidates" / "ch001.codex.repair_candidate.md"
    repair_manifest = build_manifest(
        root,
        task_type="repair",
        chapter_number=1,
        input_files=[root / "40_manuscript" / "draft" / "ch001.md"],
        allowed_output_paths=[repair_output],
        output_schema=PROSE_MARKDOWN_SCHEMA,
        validate_command=(
            "longform-engine draft submit project.yaml --chapter 1 "
            "--file 50_workbench/repair_candidates/ch001.codex.repair_candidate.md --agent codex --overwrite"
        ),
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine editorial need-human project.yaml --chapter 1 --reason repair_failed",
        task_id="repair:ch001:unused",
    )
    write_manifest(
        root,
        repair_manifest,
        root / "50_workbench" / "repair_candidates" / "ch001.codex.repair_task.agent_task.json",
    )

    finalize_chapter(config, chapter_number=1, approved_by="human")

    manifests = {item["task_id"]: item for item in list_manifests(root, chapter_number=1)}
    assert manifests["chapter_write:ch001:v4"]["status"] == "applied"
    assert manifests["repair:ch001:unused"]["status"] == "superseded"
    next_action = production_next(config)
    assert next_action["status"] == "ready_for_chapter_semantic_task"
    assert next_action["chapter_number"] == 1
    assert next_action["task_type"] == "chapter_semantic"


def test_agent_task_manifests_for_repair_humanizer_and_unified_semantic(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text("# Chapter 1\n\nTODO short draft.\n", encoding="utf-8")
    gate_check(config, chapter_number=1)
    repair_output = root / "50_workbench" / "repair_candidates" / "ch001.r01.codex.md"
    repair_task = root / "50_workbench" / "repair_plans" / "ch001" / "r01.repair_task.md"
    repair_task.parent.mkdir(parents=True, exist_ok=True)
    repair_task.write_text("# repair\n", encoding="utf-8")
    repair_manifest_path = repair_task.parent / "r01.repair.agent_task.json"
    repair_manifest_payload = build_manifest(
        root,
        task_type="repair",
        chapter_number=1,
        task_id="repair:ch001:r01:v4",
        input_files=[repair_task, root / "40_manuscript" / "draft" / "ch001.md"],
        allowed_output_paths=[repair_output],
        output_schema=PROSE_MARKDOWN_SCHEMA,
        validate_command=(
            "longform-engine draft submit project.yaml --chapter 1 "
            "--file 50_workbench/repair_candidates/ch001.r01.codex.md --agent codex --overwrite"
        ),
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine agent-task brief project.yaml repair:ch001:r01:v4",
        context_policy={
            "required_files": [repair_task, root / "40_manuscript" / "draft" / "ch001.md"],
            "optional_files": [],
            "compiled_brief": repair_task,
            "selection_report": repair_task,
        },
    )
    write_manifest(root, repair_manifest_payload, repair_manifest_path)
    repair = {"manifest_file": str(repair_manifest_path)}
    humanizer = humanize_task(config, chapter_number=1, source="draft")
    expand = expand_task(config, chapter_number=1, source="draft")

    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# Chapter 1\n\nAri sees the gate and keeps one unresolved clue alive.\n",
        encoding="utf-8",
    )
    semantic = semantic_task(config, chapter_number=1)
    task_types = {item["task_type"] for item in list_manifests(root, chapter_number=1)}

    assert Path(repair["manifest_file"]).exists()
    assert (root / "50_workbench" / "humanizer_tasks" / "ch001.draft.humanize_task.agent_task.json").exists()
    assert (root / "50_workbench" / "repair_candidates" / "ch001.expand_task.agent_task.json").exists()
    assert (root / "50_workbench" / "semantic_tasks" / "ch001.semantic.agent_task.json").exists()
    assert semantic.manifest_file.endswith("ch001.semantic.agent_task.json")
    assert semantic.output_file.endswith("ch001.semantic.json")
    assert humanizer.candidate_file.endswith("ch001.humanized_candidate.md")
    assert expand.candidate_file.endswith("ch001.expanded_candidate.md")
    assert {"repair", "humanize", "content_expand", "chapter_semantic"}.issubset(task_types)
    expand_manifest = load_manifest(root, expand.manifest_file)
    assert expand_manifest["task_type"] == "content_expand"
    assert expand_manifest["io"]["inputs"]
    assert expand_manifest["io"]["output"]["path"] == "50_workbench/repair_candidates/ch001.expanded_candidate.md"
    assert expand_manifest["commands"]["validate"].startswith("longform-engine creative expand-check ")
    assert expand_manifest["commands"]["apply"].startswith("longform-engine draft submit ")
    assert "--overwrite" in expand_manifest["commands"]["apply"]
    assert expand_manifest["commands"]["failure"].startswith("longform-engine creative expand-task ")
    humanize_manifest = load_manifest(root, "humanize:ch001:v4")
    repair_manifest = load_manifest(root, "repair:ch001:r01:v4")
    assert humanize_manifest["commands"]["failure"].startswith("longform-engine creative humanize-task ")
    assert repair_manifest["commands"]["failure"].startswith("longform-engine agent-task brief ")
    for item in list_manifests(root, chapter_number=1):
        result = validate_manifest_strict(root, load_manifest(root, item["task_id"]))
        assert result.ok, (item["task_id"], result.errors)


def test_failed_gate_completes_required_reviews_before_repair(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    config.data["quality"]["semantic_review_milestones"] = [1]

    continue_write(config, chapter_number=1)
    draft_path = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    draft_path.write_text(passing_text("REVIEW_BARRIER") + "\nTODO protocol-visible defect.\n", encoding="utf-8")
    submitted = submit_agent_draft(config, chapter_number=1, file_path=draft_path, agent="codex")
    gate = json.loads(Path(submitted.gate_result).read_text(encoding="utf-8"))
    action = production_next(config)

    assert submitted.passed is False
    assert all(item.get("code") != "semantic_review_required" for item in gate["failures"])
    assert gate["workflow_stage"] == "reviews_pending"
    assert action["task_type"] == "semantic_review"
    assert action["status"] == "agent_task_awaiting_agent"
    assert not any(item["task_type"] == "repair" for item in list_manifests(root, chapter_number=1))


def test_repair_coordinator_uses_immutable_rounds_and_counts_only_submitted_candidates(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    config.data["quality"]["assurance_mode"] = "light"
    config.data["quality"]["semantic_review_milestones"] = []
    config.data["quality"]["semantic_review_boundaries"] = False
    config.data.setdefault("editorial", {})["review_mode"] = "off"

    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\n药水必须接触瓶口并耗时饮用，随后药瓶破碎却直接恢复生命。\n", encoding="utf-8")
    write_blocking_gate(root, draft, chapter_number=1)

    first = create_repair_synthesis_task(config, chapter_number=1)
    first_bundle = json.loads((root / first["review_bundle"]).read_text(encoding="utf-8"))
    first_id = first_bundle["blocking_finding_ids"][0]
    first_plan = root / first["plan_file"]
    first_plan.write_text(repair_plan_markdown(first_bundle, first_id, conflict=True), encoding="utf-8")
    invalid = validate_repair_plan(config, chapter_number=1, file_path=first_plan)

    assert invalid["ok"] is False
    assert "conflicts with preserve ledger" in " ".join(invalid["errors"])
    assert invalid["provenance"]["need_human"] is True
    assert production_next(config)["status"] == "need_human"
    assert next_repair_round(config, chapter_number=1) == 1

    first_plan_text = repair_plan_markdown(first_bundle, first_id)
    first_plan.write_text(first_plan_text.replace(f"{first_id} P1", f"{first_id} P2"), encoding="utf-8")
    downgraded = validate_repair_plan(config, chapter_number=1, file_path=first_plan)
    assert downgraded["ok"] is False
    assert any(f"{first_id} must retain severity P1" in item for item in downgraded["errors"])
    assert next_repair_round(config, chapter_number=1) == 1

    first_plan.write_text(first_plan_text, encoding="utf-8")
    assert validate_repair_plan(config, chapter_number=1, file_path=first_plan)["ok"] is True
    assert validate_repair_plan(config, chapter_number=1, file_path=first_plan)["ok"] is True
    first_plan.write_text(first_plan_text + "\n不可变计划不允许追加。\n", encoding="utf-8")
    with pytest.raises(ValueError, match="immutable repair plan"):
        validate_repair_plan(config, chapter_number=1, file_path=first_plan)
    first_plan.write_text(first_plan_text, encoding="utf-8")
    first_candidate_task = create_repair_candidate_task(config, chapter_number=1, agent="codex")
    first_candidate = root / first_candidate_task["candidate_draft"]
    first_candidate.write_text("# Chapter 1\n\n他挡住攻击，再把瓶口送到林澄唇边，等待药液饮尽。\n", encoding="utf-8")
    record_repair_submission(
        config,
        chapter_number=1,
        task_id=first_candidate_task["task_id"],
        source_path=first_candidate,
    )

    draft.write_text(first_candidate.read_text(encoding="utf-8") + "仍有一处确认的规则冲突。\n", encoding="utf-8")
    write_blocking_gate(root, draft, chapter_number=1)
    second = create_repair_synthesis_task(config, chapter_number=1)
    second_bundle = json.loads((root / second["review_bundle"]).read_text(encoding="utf-8"))
    second_id = second_bundle["blocking_finding_ids"][0]
    second_plan = root / second["plan_file"]
    second_plan.write_text(repair_plan_markdown(second_bundle, second_id), encoding="utf-8")
    assert validate_repair_plan(config, chapter_number=1, file_path=second_plan)["ok"] is True
    second_candidate_task = create_repair_candidate_task(config, chapter_number=1, agent="codex")
    second_candidate = root / second_candidate_task["candidate_draft"]
    second_candidate.write_text("# Chapter 1\n\n第二轮完整替代稿仍等待全量复审。\n", encoding="utf-8")
    record_repair_submission(
        config,
        chapter_number=1,
        task_id=second_candidate_task["task_id"],
        source_path=second_candidate,
    )

    assert first["task_id"] == "repair_plan_synthesis:ch001:r01:v4"
    assert first_candidate_task["task_id"] == "repair:ch001:r01:v4"
    assert second["task_id"] == "repair_plan_synthesis:ch001:r02:v4"
    assert second_candidate_task["task_id"] == "repair:ch001:r02:v4"
    assert first["review_bundle"] != second["review_bundle"]
    assert first_candidate_task["candidate_draft"] != second_candidate_task["candidate_draft"]
    assert next_repair_round(config, chapter_number=1) is None
    with pytest.raises(ValueError, match="repair_budget_exhausted"):
        create_repair_synthesis_task(config, chapter_number=1)


def test_editorial_submit_review_aggregates_need_human_without_canon_pollution(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(
        "# Chapter 1\n\nAri enters the gate, but logic break remains unresolved.\n",
        encoding="utf-8",
    )
    review = editorial_review(config, chapter_number=1)
    result_file = write_editorial_role_result(
        root / "50_workbench" / "editorial_reviews" / "results",
        chapter_number=1,
        role="planning_chief_editor",
        verdict="needs_revision",
        items=[
            {
                "code": "MAINLINE_MISSING",
                "severity": "P1",
                "message": "Relationship stage changes without evidence.",
                "evidence": ["logic break remains unresolved"],
            }
        ],
    )

    submitted = submit_editorial_review(config, chapter_number=1, role="planning_chief_editor", file_path=result_file)
    aggregate = editorial_aggregate(config, chapter_number=1)
    editorial_manifests = [item for item in list_manifests(root, chapter_number=1) if item["task_type"] == "editorial_review"]

    assert review.review_file.endswith("ch001.review.json")
    assert submitted.need_human is True
    assert aggregate.severity_counts["P1"] == 1
    assert "unresolved_P1" in aggregate.need_human_reasons
    assert "missing_editorial_roles" in aggregate.need_human_reasons
    assert "scene_prose_editor" in aggregate.missing_roles
    assert not aggregate.duplicate_role_results
    assert not aggregate.invalid_results
    assert editorial_manifests
    serial_task = next(item for item in editorial_manifests if item["task_id"] == "editorial_review:planning_chief_editor:ch001:v4")
    assert serial_task["status"] == "applied"
    for item in editorial_manifests:
        result = validate_manifest_strict(root, load_manifest(root, item["task_id"]))
        assert result.ok, (item["task_id"], result.errors)
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not any((root / "60_rag").rglob("ch001*.json"))


def test_editorial_aggregate_reports_missing_duplicate_invalid_repeated_and_lifecycle(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    config.data.setdefault("editorial", {})["review_roles"] = [
        "scene_prose_editor",
        "planning_chief_editor",
        "anti_ai_editor",
    ]
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(
        "# Chapter 1\n\nAri keeps the gate clue alive, but the editor wants more scene pressure.\n",
        encoding="utf-8",
    )
    editorial_review(config, chapter_number=1)
    result_dir = root / "50_workbench" / "editorial_reviews" / "results"

    anti_ai = write_editorial_role_result(
        result_dir,
        chapter_number=1,
        role="anti_ai_editor",
        verdict="pass",
        items=[
            {
                "code": "AI_SUMMARY_LOOP",
                "severity": "P1",
                "message": "Pass verdict still contains an unresolved P1 item.",
                "evidence": ["editor wants more scene pressure"],
            }
        ],
    )
    try:
        submit_editorial_review(config, chapter_number=1, role="anti_ai_editor", file_path=anti_ai)
    except ValueError as exc:
        assert "verdict=pass cannot contain P0/P1 findings" in str(exc)
    else:
        raise AssertionError("Expected invalid editorial result")

    serial = write_editorial_role_result(
        result_dir,
        chapter_number=1,
        role="planning_chief_editor",
        verdict="conditional_pass",
        items=[{"code": "OUTLINE_DUTY_MISSED", "severity": "P2", "message": "Track the gate clue.", "evidence": ["gate clue"]}],
    )
    write_editorial_role_result(
        result_dir,
        chapter_number=1,
        role="planning_chief_editor",
        verdict="conditional_pass",
        suffix="retry",
        items=[{"code": "PROMISE_WINDOW_BROKEN", "severity": "P2", "message": "Duplicate role result.", "evidence": ["gate clue"]}],
    )
    writing = write_editorial_role_result(
        result_dir,
        chapter_number=1,
        role="scene_prose_editor",
        verdict="conditional_pass",
        items=[{"code": "SCENE_SUMMARIZED", "severity": "P2", "message": "Scene pressure is acceptable.", "evidence": ["scene pressure"]}],
    )
    submit_editorial_review(config, chapter_number=1, role="planning_chief_editor", file_path=serial)
    submit_editorial_review(config, chapter_number=1, role="scene_prose_editor", file_path=writing)

    aggregate = editorial_aggregate(config, chapter_number=1)
    payload = json.loads(Path(aggregate.aggregate_file).read_text(encoding="utf-8"))
    markdown = Path(aggregate.markdown_file).read_text(encoding="utf-8")
    summary = status_summary(root, chapter_number=1)

    assert aggregate.need_human is True
    assert aggregate.conditional_passes == 0
    assert "repeated_conditional_pass" not in aggregate.need_human_reasons
    assert "missing_editorial_roles" in aggregate.need_human_reasons
    assert "duplicate_role_results" in aggregate.need_human_reasons
    assert "invalid_role_results" in aggregate.need_human_reasons
    assert aggregate.missing_roles == ("anti_ai_editor",)
    assert aggregate.duplicate_role_results[0]["role_id"] == "planning_chief_editor"
    assert aggregate.invalid_results[0]["role_id"] == "anti_ai_editor"
    assert payload["missing_roles"] == ["anti_ai_editor"]
    assert payload["duplicate_role_results"]
    assert payload["invalid_results"]
    assert "Team Completeness" in markdown
    assert "Duplicate Role Results" in markdown
    assert "Invalid Role Results" in markdown
    assert summary["by_status"]["applied"] == 2
    assert summary["by_status"]["invalid"] == 1
    assert summary["by_status"].get("awaiting_agent", 0) == 0


def test_editorial_unresolved_p1_blocks_chapter_finalize(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    continue_write(config, chapter_number=1)
    draft_path = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    draft_path.write_text(passing_text("EDITORIAL_BLOCK"), encoding="utf-8")
    submitted = submit_agent_draft(config, chapter_number=1, file_path=draft_path, agent="codex")
    assert submitted.passed is True

    config.data.setdefault("editorial", {})["review_roles"] = ["planning_chief_editor"]
    editorial_review(config, chapter_number=1)
    result_file = write_editorial_role_result(
        root / "50_workbench" / "editorial_reviews" / "results",
        chapter_number=1,
        role="planning_chief_editor",
        verdict="needs_revision",
        items=[
            {
                "code": "MAINLINE_MISSING",
                "severity": "P1",
                "message": "Relationship stage changes without evidence.",
                "evidence": ["keeps the promise"],
            }
        ],
    )
    aggregate = submit_editorial_review(config, chapter_number=1, role="planning_chief_editor", file_path=result_file)
    assert "unresolved_P1" in aggregate.need_human_reasons

    try:
        finalize_chapter(config, chapter_number=1, approved_by="human")
    except WorkflowError as exc:
        assert "editorial aggregate requires human review" in str(exc)
        assert "unresolved_P1" in str(exc)
    else:
        raise AssertionError("Expected editorial aggregate to block finalization")
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_semantic_pacing_apply_updates_gate_only_and_blocks_on_p1(tmp_path, monkeypatch):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(passing_text("PACING_AGENT"), encoding="utf-8")
    gate_check(config, chapter_number=1)
    task = semantic_pacing_task(config, chapter_number=1)
    task_payload = json.loads(Path(task.task_json).read_text(encoding="utf-8"))
    manifest = load_manifest(root, task.manifest_file)
    assert "input_files" not in task_payload
    assert task_payload["planning_context"]["source_catalog"]
    assert [item["path"] for item in manifest["io"]["inputs"]] == [
        "50_workbench/gate_artifacts/ch001/semantic_pacing_task.md",
        "40_manuscript/draft/ch001.md",
        "50_workbench/gate_artifacts/ch001/semantic_pacing_task.json",
    ]
    result_file = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_pacing_result.json"
    output = json.loads(Path(task.task_json).read_text(encoding="utf-8"))["output_schema"]
    output.update(pacing_review_payload(blocking=True))
    result_file.write_text(
        json.dumps(output, ensure_ascii=False),
        encoding="utf-8",
    )

    protocol = validate_production_agent_result(root, manifest, result_file=result_file)
    validation = semantic_pacing_validate(config, chapter_number=1, file_path=result_file)
    status_after_validate = status_summary(root, chapter_number=1)
    applied = semantic_pacing_apply(config, chapter_number=1, file_path=result_file)
    gate = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))
    strict = validate_manifest_strict(root, load_manifest(root, task.manifest_file))

    assert task.output_file == str(result_file)
    assert strict.ok, strict.errors
    assert protocol.ok is True
    assert protocol.normalization.source_schema == EVIDENCE_REVIEW_SCHEMA
    assert validation.ok is True
    assert status_after_validate["by_status"]["validated"] >= 1
    assert load_manifest(root, task.manifest_file)["status"] == "applied"
    assert applied.escalated_failures == 1
    assert status_summary(root, chapter_number=1)["by_status"]["applied"] >= 1
    assert gate["passed"] is False
    assert any(item["code"] == "semantic_pacing:TURN_TOO_ABRUPT" for item in gate["failures"])
    reports = transaction_payloads(root, "pacing semantic-apply")
    assert reports
    assert "50_workbench/gate_artifacts/ch001" in reports[-1]["touched_paths"]
    assert reports[-1]["metadata"]["gate_artifact_only"] is True
    repeated = semantic_pacing_apply(config, chapter_number=1, file_path=result_file)
    assert repeated.applied is True
    assert len(transaction_payloads(root, "pacing semantic-apply")) == len(reports)
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not any(json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8")).get(key) for key in ("entities", "events"))


def test_semantic_pacing_invalid_validate_updates_lifecycle_without_gate_pollution(tmp_path, monkeypatch):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(passing_text("PACING_INVALID"), encoding="utf-8")
    gate_check(config, chapter_number=1)
    task = semantic_pacing_task(config, chapter_number=1)
    result_file = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_pacing_result.json"
    gate_before = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))
    result_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "chapter_number": 1,
                "source_path": "40_manuscript/draft/ch001.md",
                "source_sha256": "stale",
                "verdict": "maybe",
                "tier": "too_fast",
                "issues": [{"code": "", "severity": "P9", "message": ""}],
                "warnings": "not-a-list",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    protocol = validate_production_agent_result(
        root,
        load_manifest(root, task.manifest_file),
        result_file=result_file,
    )
    validation = semantic_pacing_validate(config, chapter_number=1, file_path=result_file)
    gate_after = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))

    assert validation.ok is False
    assert protocol.ok is False
    assert status_summary(root, chapter_number=1)["by_status"]["invalid"] >= 1
    assert load_manifest(root, task.manifest_file)["status"] == "invalid"
    assert "evidence review must contain exactly" in "; ".join(validation.errors)
    assert gate_after == gate_before
    assert not transaction_payloads(root, "pacing semantic-apply")
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_required_semantic_pacing_blocks_finalize_until_current_v2_result_is_applied(tmp_path, monkeypatch):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    config.data.setdefault("quality", {}).setdefault("semantic_pacing", {})["review_mode"] = "required"
    continue_write(config, chapter_number=1)
    draft_path = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    draft_path.write_text(passing_text("PACING_REQUIRED"), encoding="utf-8")
    submitted = submit_agent_draft(config, chapter_number=1, file_path=draft_path, agent="codex")
    assert submitted.passed is True

    action = production_next(config)
    assert action["status"] == "ready_for_pacing_review"
    assert action["next_command"] == "longform-engine pacing semantic-task project.yaml --chapter 1"
    with pytest.raises(WorkflowError, match="semantic pacing review is missing, failed, or stale"):
        finalize_chapter(config, chapter_number=1, approved_by="human")

    task = semantic_pacing_task(config, chapter_number=1)
    result_file = Path(task.output_file)
    output = json.loads(Path(task.task_json).read_text(encoding="utf-8"))["output_schema"]
    output.update(pacing_review_payload())
    result_file.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    assert validate_production_agent_result(
        root,
        load_manifest(root, task.manifest_file),
        result_file=result_file,
    ).ok is True
    assert semantic_pacing_validate(config, chapter_number=1, file_path=result_file).ok is True
    semantic_pacing_apply(config, chapter_number=1, file_path=result_file)

    action = production_next(config)
    assert action["status"] == "awaiting_finalize"
    finalized = finalize_chapter(config, chapter_number=1, approved_by="human")
    assert Path(finalized.final_file).is_file()


def test_semantic_pacing_domain_validation_requires_current_control_plane_binding(tmp_path, monkeypatch):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(passing_text("PACING_CONTROL_PLANE"), encoding="utf-8")
    gate_check(config, chapter_number=1)
    task = semantic_pacing_task(config, chapter_number=1)
    manifest = load_manifest(root, task.manifest_file)
    result_file = Path(task.output_file)
    output = json.loads(Path(task.task_json).read_text(encoding="utf-8"))["output_schema"]
    output.update(pacing_review_payload())
    result_file.write_text(json.dumps(output, ensure_ascii=False), encoding="utf-8")
    gate_file = root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json"
    gate_before = gate_file.read_bytes()

    direct = semantic_pacing_validate(config, chapter_number=1, file_path=result_file)
    assert direct.ok is False
    assert "Run `longform-engine agent-task result-validate ...` first" in "; ".join(direct.errors)
    assert load_manifest(root, task.manifest_file)["status"] == "awaiting_agent"
    assert gate_file.read_bytes() == gate_before

    protocol = validate_production_agent_result(root, manifest, result_file=result_file)
    assert protocol.ok is True
    bound = load_manifest(root, task.manifest_file)["current_result"]
    assert bound["path"] == "50_workbench/gate_artifacts/ch001/semantic_pacing_result.json"
    assert bound["sha256"] == protocol.normalization.result_sha256
    changed = dict(output)
    changed["findings"] = [*output["findings"], {
        "code": "AFTERMATH_MISSING",
        "severity": "P2",
        "certainty": "confirmed",
        "diagnosis": "changed after validation",
        "evidence_ids": ["ch001.md@0:9"],
        "reader_impact": "stale result must be rejected",
        "repair_target": "rerun validation",
        "preserve": [],
    }]
    result_file.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    tampered = semantic_pacing_validate(config, chapter_number=1, file_path=result_file)
    assert tampered.ok is False
    assert "changed after control-plane validation" in "; ".join(tampered.errors)
    with pytest.raises(GateError, match="control-plane lifecycle"):
        semantic_pacing_apply(config, chapter_number=1, file_path=result_file)
    assert gate_file.read_bytes() == gate_before


def test_strict_manifest_validation_rejects_unknown_type_and_canonical_output(tmp_path):
    seed_project(tmp_path)
    root = tmp_path / "novel"
    with pytest.raises(AgentTaskContractError, match="No Prompt role is registered"):
        build_manifest(
            root,
            task_type="mystery_task",
            chapter_number=1,
            input_files=[root / "project.yaml"],
            allowed_output_paths=[root / "50_workbench" / "agent_drafts" / "ch001.codex.md"],
            output_schema=PROSE_MARKDOWN_SCHEMA,
            validate_command="longform-engine draft submit project.yaml --chapter 1 --file 50_workbench/agent_drafts/ch001.codex.md --agent codex",
            apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
            failure_next_command="longform-engine production next project.yaml",
        )
    canonical_output = build_manifest(
        root,
        task_type="chapter_semantic",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[root / "30_state" / "story_graph.json"],
        output_schema=CANONICAL_DELTA_SCHEMA,
        validate_command="longform-engine chapter semantic-validate project.yaml --chapter 1 --file 30_state/story_graph.json",
        apply_command="longform-engine chapter semantic-apply project.yaml --chapter 1 --file 30_state/story_graph.json",
        failure_next_command="longform-engine chapter semantic-task project.yaml --chapter 1",
    )

    canonical_result = validate_manifest_strict(root, canonical_output)

    assert canonical_result.ok is False
    assert any("canonical state" in item for item in canonical_result.errors)
    assert any("50_workbench/semantic_tasks/" in item for item in canonical_result.errors)


def test_agent_task_lifecycle_supports_superseded_and_rolled_back_events(tmp_path):
    seed_project(tmp_path)
    root = tmp_path / "novel"
    manifest = build_manifest(
        root,
        task_type="chapter_write",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[root / "50_workbench" / "agent_drafts" / "ch001.codex.md"],
        output_schema=PROSE_MARKDOWN_SCHEMA,
        validate_command="longform-engine draft submit project.yaml --chapter 1 --file 50_workbench/agent_drafts/ch001.codex.md --agent codex",
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine production next project.yaml",
        task_id="chapter_write:ch001:lifecycle-test",
    )
    write_manifest(root, manifest, root / "50_workbench" / "writing_tasks" / "ch001.lifecycle.agent_task.json")

    superseded = update_task_status(
        root,
        "chapter_write:ch001:lifecycle-test",
        to_status="superseded",
        command="continue-write",
        artifact="50_workbench/writing_tasks/ch001.lifecycle.agent_task.json",
        result="chapter_write:ch001:v2",
    )
    rolled_back = update_task_status(
        root,
        "chapter_write:ch001:lifecycle-test",
        to_status="rolled_back",
        command="revision rollback",
        artifact="70_runtime/snapshots/demo",
        result="50_workbench/impact_reports/rollback_to_ch000.md",
    )

    summary = status_summary(root, chapter_number=1)
    events = [item for item in event_payloads(root) if item["task_id"] == "chapter_write:ch001:lifecycle-test"]

    assert set(AGENT_TASK_STATUSES) == {"awaiting_agent", "submitted", "validated", "approved", "invalid", "applied", "superseded", "rolled_back"}
    assert superseded is not None
    assert rolled_back is not None
    assert summary["by_status"]["rolled_back"] == 1
    assert [item["to_status"] for item in events] == ["awaiting_agent", "superseded", "rolled_back"]


def write_blocking_gate(root: Path, draft: Path, *, chapter_number: int) -> None:
    digest = sha256(draft.read_bytes()).hexdigest()
    gate_dir = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}"
    gate_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "chapter_number": chapter_number,
        "passed": False,
        "severity": "P1",
        "source_path": draft.relative_to(root).as_posix(),
        "source_sha256": digest,
        "failures": [
            {
                "code": "ABILITY_RULE_CONFLICT",
                "severity": "P1",
                "message": "治疗规则前置声明与救援动作冲突。",
                "repair_action": "重写药水接触、饮用和生效动作链。",
                "preserve": ["主角放弃追击并优先救人"],
            }
        ],
        "warnings": [],
        "agent_semantic_review": {"required": False, "status": "not_required"},
        "workflow_stage": "review_barrier",
    }
    (gate_dir / "gate_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def repair_plan_markdown(bundle: dict, finding_id: str, *, conflict: bool = False) -> str:
    round_token = f"r{int(bundle['repair_round']):02d}"
    mutable = "主角放弃追击并优先救人" if conflict else "药水接触、饮用和生效动作链"
    return "\n".join(
        [
            "# 修复计划",
            "",
            "## 候选 hash 与修复轮次",
            f"候选 {bundle['candidate_sha256']}，轮次 {round_token}。",
            "",
            "## 完整 blocking finding 清单",
            f"- {finding_id} P1：治疗规则前置声明与救援动作冲突。",
            "",
            "## 共同根因分组",
            f"- 机制根因：{finding_id} 指向未统一的治疗生效规则。",
            "",
            "## 修复依赖与执行顺序",
            "先统一接触规则，再重写动作，最后核对生存结果。",
            "",
            "## 每组最小修改范围",
            "从首次说明药水规则到林澄状态稳定的最后依赖句。",
            "",
            "## 必须保留内容",
            "- 主角放弃追击并优先救人",
            "",
            "## 允许改变内容",
            f"- {mutable}",
            "",
            "## 冲突与 need-human 判断",
            "need-human: no\n无冲突。" if not conflict else "need-human: yes\n存在保护项冲突，应拒绝计划。",
            "",
            "## 回归检查清单",
            "重新检查 continuity、character、payoff 和 scene causality。",
            "",
            "## 完成判据",
            "规则声明、救援动作和人物生存结果形成唯一可复核因果链。",
            "",
        ]
    )


def seed_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    return load_project_config(
        project.project_config,
        cli_overrides={"editorial": {"review_mode": "off"}},
    )


def passing_text(marker: str) -> str:
    sentence = f"{marker} Ari keeps the promise, pays a cost, and leaves one unresolved clue at the gate? "
    return "# Chapter\n\n" + sentence * 45 + "\n"


def pacing_review_payload(*, blocking: bool = False) -> dict:
    findings = []
    if blocking:
        findings.append(
            {
                "code": "TURN_TOO_ABRUPT",
                "severity": "P1",
                "certainty": "confirmed",
                "diagnosis": "The ending answers the pressure instead of raising a new one.",
                "evidence_ids": ["ch001.md@0:9"],
                "reader_impact": "The chapter loses forward pressure.",
                "repair_target": "Restore one concrete unresolved pressure at the ending.",
                "preserve": ["existing chapter outcome"],
            }
        )
    return {
        "schema": EVIDENCE_REVIEW_SCHEMA,
        "verdict": "repair" if blocking else "pass",
        "coverage": {"pressure_release": "checked", "beat_change": "checked", "aftermath": "checked"},
        "findings": findings,
    }


def submit_editorial_review(config, *, chapter_number: int, role: str, file_path: Path):
    root = resolve_project_root(config)
    manifest = load_manifest(root, f"editorial_review:{role}:ch{chapter_number:03d}:v4")
    validate_production_agent_result(root, manifest, result_file=file_path)
    return editorial_submit_review(
        config,
        chapter_number=chapter_number,
        role=role,
        file_path=file_path,
    )


def write_editorial_role_result(
    result_dir: Path,
    *,
    chapter_number: int,
    role: str,
    verdict: str,
    items: list[dict],
    suffix: str = "",
) -> Path:
    result_dir.mkdir(parents=True, exist_ok=True)
    root = result_dir.parents[2]
    name = f"ch{chapter_number:03d}.{role}{'.' + suffix if suffix else ''}.json"
    path = result_dir / name
    role_contract = load_role_registry().roles[role]
    dimensions = list(role_contract.review_dimensions)
    normalized_findings = []
    chapter = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    text = chapter.read_text(encoding="utf-8")
    for index, item in enumerate(items):
        fragments = [str(value) for value in item.get("evidence") or []]
        evidence_ids = []
        for fragment in fragments:
            start = text.find(fragment)
            if start >= 0:
                evidence_ids.append(f"ch{chapter_number:03d}.md@{start}:{start + len(fragment)}")
        normalized_findings.append(
            {
                "code": item["code"],
                "severity": item["severity"],
                "certainty": "confirmed",
                "diagnosis": item["message"],
                "evidence_ids": evidence_ids,
                "reader_impact": item["message"],
                "repair_target": "Repair only the cited editorial issue.",
                "preserve": ["existing valid chapter facts"],
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema": EVIDENCE_REVIEW_SCHEMA,
                "verdict": "repair" if verdict in {"needs_revision", "rewrite", "blocked"} else "pass",
                "coverage": {dimension: "checked" for dimension in dimensions},
                "findings": normalized_findings,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def transaction_payloads(root: Path, command: str) -> list[dict]:
    payloads = []
    for path in sorted((root / "70_runtime" / "transactions").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("command") == command:
            payloads.append(payload)
    return payloads


def event_payloads(root: Path) -> list[dict]:
    path = root / "50_workbench" / "agent_tasks" / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
