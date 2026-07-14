import json
from pathlib import Path

from longform_engine.agent_tasks import (
    AGENT_TASK_STATUSES,
    build_manifest,
    list_manifests,
    load_manifest,
    status_summary,
    update_task_status,
    validate_manifest_strict,
    write_manifest,
)
from longform_engine.cli import write_repair_candidate_task
from longform_engine.config import load_project_config
from longform_engine.creative import expand_task, humanize_task
from longform_engine.editorial import editorial_aggregate, editorial_review, editorial_submit_review
from longform_engine.gates import gate_check, semantic_pacing_apply, semantic_pacing_task, semantic_pacing_validate
from longform_engine.graph import semantic_graph_task
from longform_engine.memory import character_task, semantic_task as memory_semantic_task
from longform_engine.orchestration import WorkflowError, continue_write, finalize_chapter, open_book, plan_chapter, submit_agent_draft
from longform_engine.storage import init_project


def test_no_key_agent_task_chapter_loop_and_manifest_index(tmp_path, monkeypatch):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MOONSHOT_API_KEY", "GLM_API_KEY", "MINIMAX_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)

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
    assert json.loads(manifest.read_text(encoding="utf-8"))["status"] == "applied"
    events = event_payloads(root)
    assert [item["to_status"] for item in events if item["task_id"] == "chapter_write:ch001:v1"] == [
        "awaiting_agent",
        "submitted",
        "validated",
        "applied",
    ]
    assert events[-1]["command"] == "chapter finalize"
    strict = validate_manifest_strict(root, json.loads(manifest.read_text(encoding="utf-8")))
    assert strict.ok, strict.errors
    assert strict.warnings


def test_agent_task_manifests_for_repair_humanizer_graph_and_character_memory(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text("# Chapter 1\n\nTODO short draft.\n", encoding="utf-8")
    gate_check(config, chapter_number=1)
    repair = write_repair_candidate_task(config, chapter_number=1, agent="codex")
    humanizer = humanize_task(config, chapter_number=1, source="draft")
    expand = expand_task(config, chapter_number=1, source="draft")

    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# Chapter 1\n\nAri sees the gate and keeps one unresolved clue alive.\n",
        encoding="utf-8",
    )
    graph = semantic_graph_task(config, chapter_number=1)
    memory = memory_semantic_task(config, chapter_number=1)
    character = character_task(config, chapter_number=1)
    task_types = {item["task_type"] for item in list_manifests(root, chapter_number=1)}

    assert Path(repair["manifest_file"]).exists()
    assert (root / "50_workbench" / "humanizer_tasks" / "ch001.draft.humanize_task.agent_task.json").exists()
    assert (root / "50_workbench" / "repair_candidates" / "ch001.expand_task.agent_task.json").exists()
    assert (root / "50_workbench" / "graph_updates" / "ch001.semantic_graph.agent_task.json").exists()
    assert (root / "50_workbench" / "memory_tasks" / "ch001.semantic_memory.agent_task.json").exists()
    assert (root / "50_workbench" / "memory_tasks" / "ch001.character_memory.agent_task.json").exists()
    assert graph.manifest_file.endswith("ch001.semantic_graph.agent_task.json")
    assert memory.manifest_file.endswith("ch001.semantic_memory.agent_task.json")
    assert character.manifest_file.endswith("ch001.character_memory.agent_task.json")
    assert graph.output_file.endswith("ch001.semantic.json")
    assert memory.output_file.endswith("ch001.semantic.codex.json")
    assert character.output_file.endswith("ch001.character.codex.json")
    assert humanizer.candidate_file.endswith("ch001.humanized_candidate.md")
    assert expand.candidate_file.endswith("ch001.expanded_candidate.md")
    assert {"repair", "humanize", "content_expand", "graph_extract", "memory_extract", "character_memory"}.issubset(task_types)
    expand_manifest = load_manifest(root, expand.manifest_file)
    assert expand_manifest["task_type"] == "content_expand"
    assert expand_manifest["input_files"]
    assert expand_manifest["allowed_output_paths"] == ["50_workbench/repair_candidates/ch001.expanded_candidate.md"]
    assert expand_manifest["validate_command"].startswith("longform-engine creative expand-check ")
    assert expand_manifest["apply_command"].startswith("longform-engine draft submit ")
    assert "--overwrite" in expand_manifest["apply_command"]
    assert expand_manifest["failure_next_command"].startswith("longform-engine creative expand-task ")
    humanize_manifest = load_manifest(root, "humanize:ch001:v1")
    repair_manifest = load_manifest(root, "repair:ch001:v1")
    assert humanize_manifest["failure_next_command"].startswith("longform-engine creative humanize-task ")
    assert repair_manifest["failure_next_command"].startswith("longform-engine editorial need-human ")
    for item in list_manifests(root, chapter_number=1):
        result = validate_manifest_strict(root, load_manifest(root, item["task_id"]))
        assert result.ok, (item["task_id"], result.errors)


def test_editorial_submit_review_aggregates_need_human_without_canon_pollution(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(
        "# Chapter 1\n\nAri enters the gate, but logic break remains unresolved.\n",
        encoding="utf-8",
    )
    review = editorial_review(config, chapter_number=1)
    result_file = root / "50_workbench" / "editorial_reviews" / "results" / "ch001.serial_verifier.json"
    result_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "role_id": "serial_verifier",
                "verdict": "needs_revision",
                "items": [
                    {
                        "code": "logic_continuity_risk",
                        "severity": "P1",
                        "message": "Relationship stage changes without evidence.",
                        "evidence": ["logic break remains unresolved"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    submitted = editorial_submit_review(config, chapter_number=1, role="serial_verifier", file_path=result_file)
    aggregate = editorial_aggregate(config, chapter_number=1)
    editorial_manifests = [item for item in list_manifests(root, chapter_number=1) if item["task_type"] == "editorial_review"]

    assert review.review_file.endswith("ch001.review.json")
    assert submitted.need_human is True
    assert aggregate.severity_counts["P1"] == 1
    assert "unresolved_P1" in aggregate.need_human_reasons
    assert "missing_editorial_roles" in aggregate.need_human_reasons
    assert "planning_chief_editor" in aggregate.missing_roles
    assert not aggregate.duplicate_role_results
    assert not aggregate.invalid_results
    assert editorial_manifests
    serial_task = next(item for item in editorial_manifests if item["task_id"] == "editorial_review:serial_verifier:ch001:v1")
    assert serial_task["status"] == "applied"
    for item in editorial_manifests:
        result = validate_manifest_strict(root, load_manifest(root, item["task_id"]))
        assert result.ok, (item["task_id"], result.errors)
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not any((root / "60_rag").rglob("ch001*.json"))


def test_editorial_aggregate_reports_missing_duplicate_invalid_repeated_and_lifecycle(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
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
                "code": "ai_diction_risk",
                "severity": "P1",
                "message": "Pass verdict still contains an unresolved P1 item.",
                "evidence": ["editor wants more scene pressure"],
            }
        ],
    )
    try:
        editorial_submit_review(config, chapter_number=1, role="anti_ai_editor", file_path=anti_ai)
    except ValueError as exc:
        assert "pass verdict" in str(exc)
    else:
        raise AssertionError("Expected invalid editorial result")

    serial = write_editorial_role_result(
        result_dir,
        chapter_number=1,
        role="serial_verifier",
        verdict="conditional_pass",
        items=[{"code": "continuity_watch", "severity": "P2", "message": "Track the gate clue.", "evidence": ["gate clue"]}],
    )
    write_editorial_role_result(
        result_dir,
        chapter_number=1,
        role="serial_verifier",
        verdict="conditional_pass",
        suffix="retry",
        items=[{"code": "continuity_watch_retry", "severity": "P2", "message": "Duplicate role result.", "evidence": ["gate clue"]}],
    )
    writing = write_editorial_role_result(
        result_dir,
        chapter_number=1,
        role="writing_agent",
        verdict="conditional_pass",
        items=[{"code": "scene_pressure_watch", "severity": "P2", "message": "Scene pressure is acceptable.", "evidence": ["scene pressure"]}],
    )
    editorial_submit_review(config, chapter_number=1, role="serial_verifier", file_path=serial)
    editorial_submit_review(config, chapter_number=1, role="writing_agent", file_path=writing)

    aggregate = editorial_aggregate(config, chapter_number=1)
    payload = json.loads(Path(aggregate.aggregate_file).read_text(encoding="utf-8"))
    markdown = Path(aggregate.markdown_file).read_text(encoding="utf-8")
    summary = status_summary(root, chapter_number=1)

    assert aggregate.need_human is True
    assert aggregate.conditional_passes == 2
    assert "repeated_conditional_pass" in aggregate.need_human_reasons
    assert "missing_editorial_roles" in aggregate.need_human_reasons
    assert "duplicate_role_results" in aggregate.need_human_reasons
    assert "invalid_role_results" in aggregate.need_human_reasons
    assert {"planning_chief_editor", "anti_ai_editor", "executive_editor"}.issubset(set(aggregate.missing_roles))
    assert aggregate.duplicate_role_results[0]["role_id"] == "serial_verifier"
    assert aggregate.invalid_results[0]["role_id"] == "anti_ai_editor"
    assert payload["missing_roles"]
    assert payload["duplicate_role_results"]
    assert payload["invalid_results"]
    assert "Team Completeness" in markdown
    assert "Duplicate Role Results" in markdown
    assert "Invalid Role Results" in markdown
    assert summary["by_status"]["applied"] >= 2
    assert summary["by_status"]["invalid"] >= 1
    assert summary["by_status"]["awaiting_agent"] >= 2


def test_editorial_unresolved_p1_blocks_chapter_finalize(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    continue_write(config, chapter_number=1)
    draft_path = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    draft_path.write_text(passing_text("EDITORIAL_BLOCK"), encoding="utf-8")
    submitted = submit_agent_draft(config, chapter_number=1, file_path=draft_path, agent="codex")
    assert submitted.passed is True

    editorial_review(config, chapter_number=1)
    result_file = write_editorial_role_result(
        root / "50_workbench" / "editorial_reviews" / "results",
        chapter_number=1,
        role="serial_verifier",
        verdict="needs_revision",
        items=[
            {
                "code": "relationship_stage_jump",
                "severity": "P1",
                "message": "Relationship stage changes without evidence.",
                "evidence": ["keeps the promise"],
            }
        ],
    )
    aggregate = editorial_submit_review(config, chapter_number=1, role="serial_verifier", file_path=result_file)
    assert "unresolved_P1" in aggregate.need_human_reasons

    try:
        finalize_chapter(config, chapter_number=1, approved_by="human")
    except WorkflowError as exc:
        assert "editorial aggregate requires human review" in str(exc)
        assert "unresolved_P1" in str(exc)
    else:
        raise AssertionError("Expected editorial aggregate to block finalization")
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_semantic_pacing_apply_updates_gate_only_and_blocks_on_p1(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(passing_text("PACING_AGENT"), encoding="utf-8")
    gate_check(config, chapter_number=1)
    task = semantic_pacing_task(config, chapter_number=1)
    result_file = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_pacing_result.json"
    result_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "verdict": "fail",
                "tier": "fast",
                "tail_hook_quality": "weak",
                "issues": [
                    {
                        "code": "tail_hook_collapses",
                        "severity": "P1",
                        "message": "The ending answers the pressure instead of raising a new one.",
                        "evidence": "unresolved clue alive",
                    }
                ],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validation = semantic_pacing_validate(config, chapter_number=1, file_path=result_file)
    status_after_validate = status_summary(root, chapter_number=1)
    applied = semantic_pacing_apply(config, chapter_number=1, file_path=result_file)
    gate = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))
    strict = validate_manifest_strict(root, load_manifest(root, task.manifest_file))

    assert task.output_file == str(result_file)
    assert strict.ok, strict.errors
    assert validation.ok is True
    assert status_after_validate["by_status"]["validated"] >= 1
    assert load_manifest(root, task.manifest_file)["status"] == "applied"
    assert applied.escalated_failures == 1
    assert status_summary(root, chapter_number=1)["by_status"]["applied"] >= 1
    assert gate["passed"] is False
    assert any(item["code"] == "semantic_pacing:tail_hook_collapses" for item in gate["failures"])
    reports = transaction_payloads(root, "pacing semantic-apply")
    assert reports
    assert "50_workbench/gate_artifacts/ch001/gate_result.json" in reports[-1]["touched_paths"]
    assert reports[-1]["metadata"]["gate_artifact_only"] is True
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not any(json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8")).get(key) for key in ("entities", "events"))


def test_semantic_pacing_invalid_validate_updates_lifecycle_without_gate_pollution(tmp_path):
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
                "schema_version": 1,
                "chapter_number": 1,
                "verdict": "maybe",
                "tier": "too_fast",
                "issues": [{"code": "", "severity": "P9", "message": ""}],
                "warnings": "not-a-list",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validation = semantic_pacing_validate(config, chapter_number=1, file_path=result_file)
    gate_after = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))

    assert validation.ok is False
    assert status_summary(root, chapter_number=1)["by_status"]["invalid"] >= 1
    assert load_manifest(root, task.manifest_file)["status"] == "invalid"
    assert "verdict must be pass" in "; ".join(validation.errors)
    assert gate_after == gate_before
    assert not transaction_payloads(root, "pacing semantic-apply")
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_strict_manifest_validation_rejects_unknown_type_and_canonical_output(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    bad_type = build_manifest(
        root,
        task_type="mystery_task",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[root / "50_workbench" / "agent_drafts" / "ch001.codex.md"],
        output_schema="markdown_chapter_only",
        validate_command="longform-engine draft submit project.yaml --chapter 1 --file 50_workbench/agent_drafts/ch001.codex.md --agent codex",
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
    )
    canonical_output = build_manifest(
        root,
        task_type="graph_extract",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[root / "30_state" / "story_graph.json"],
        output_schema="semantic_graph_update_v1",
        validate_command="longform-engine graph semantic-validate project.yaml --chapter 1 --file 30_state/story_graph.json",
        apply_command="longform-engine graph semantic-apply project.yaml --chapter 1 --file 30_state/story_graph.json",
        failure_next_command="longform-engine graph semantic-task project.yaml --chapter 1",
    )

    unknown_result = validate_manifest_strict(root, bad_type)
    canonical_result = validate_manifest_strict(root, canonical_output)

    assert unknown_result.ok is False
    assert any("task_type must be one of" in item for item in unknown_result.errors)
    assert canonical_result.ok is False
    assert any("canonical state" in item for item in canonical_result.errors)
    assert any("50_workbench/graph_updates/" in item for item in canonical_result.errors)


def test_agent_task_lifecycle_supports_superseded_and_rolled_back_events(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    manifest = build_manifest(
        root,
        task_type="chapter_write",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[root / "50_workbench" / "agent_drafts" / "ch001.codex.md"],
        output_schema="markdown_chapter_only",
        validate_command="longform-engine draft submit project.yaml --chapter 1 --file 50_workbench/agent_drafts/ch001.codex.md --agent codex",
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
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

    assert set(AGENT_TASK_STATUSES) == {"awaiting_agent", "submitted", "validated", "invalid", "applied", "superseded", "rolled_back"}
    assert superseded is not None
    assert rolled_back is not None
    assert summary["by_status"]["rolled_back"] == 1
    assert [item["to_status"] for item in events] == ["awaiting_agent", "superseded", "rolled_back"]


def seed_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def passing_text(marker: str) -> str:
    sentence = f"{marker} Ari keeps the promise, pays a cost, and leaves one unresolved clue at the gate? "
    return "# Chapter\n\n" + sentence * 45 + "\n"


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
    name = f"ch{chapter_number:03d}.{role}{'.' + suffix if suffix else ''}.json"
    path = result_dir / name
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": chapter_number,
                "role_id": role,
                "verdict": verdict,
                "items": items,
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
