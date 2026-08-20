import json
from hashlib import sha256

from longform_engine.agent_protocols import PROSE_MARKDOWN_SCHEMA
from longform_engine.agent_tasks import build_manifest, list_manifests, write_manifest
from longform_engine.chapter_contract import stamp_chapter_contract
from longform_engine.config import load_project_config
from longform_engine.editorial import editorial_review
from longform_engine.gates import semantic_pacing_task
from longform_engine.orchestration import (
    WorkflowError,
    auto_write_plan,
    auto_write_progress,
    auto_write_report,
    auto_write_run,
    continue_write,
    finalize_chapter,
    generate_beat_sheet,
    open_book as engine_open_book,
    plan_chapter,
    submit_agent_draft,
)
from longform_engine.db import query_table
from longform_engine.production import production_next
from longform_engine.semantic import semantic_apply
from longform_engine.storage import init_project
from tests.project_fixtures import (
    approve_story_candidate,
    complete_unified_semantic_lifecycle,
    mark_project_ready,
    prepare_unified_semantic_bundle,
    write_arc_simulation_fixture,
)


def open_book(config):
    result = engine_open_book(config)
    mark_project_ready(config.path.parent, config, preserve_existing_characters=True)
    return result


def test_open_book_writes_five_confirmations(tmp_path):
    project_config = seed_project(tmp_path)

    result = engine_open_book(project_config)

    idea_seed = (tmp_path / "novel" / "00_governance" / "idea_seed.md").read_text(encoding="utf-8")
    state = json.loads((tmp_path / "novel" / "30_state" / "novel_state.json").read_text(encoding="utf-8"))

    assert "Target audience" in idea_seed
    assert "Writing style" in idea_seed
    assert "Core forbidden zone" in idea_seed
    assert "Automation level" in idea_seed
    assert "Target scale" in idea_seed
    assert state["status"] == "open_book_confirmed"
    assert result.reader_contract.endswith("reader_contract.md")


def test_plan_chapter_and_beat_sheet(tmp_path):
    project_config = seed_project(tmp_path)

    card = plan_chapter(project_config, chapter_number=12)
    beat = generate_beat_sheet(project_config, chapter_number=12)

    card_payload = json.loads((tmp_path / "novel" / "20_outline" / "chapter_cards" / "ch012.json").read_text(encoding="utf-8"))
    beat_payload = json.loads((tmp_path / "novel" / "50_workbench" / "beats" / "ch012.json").read_text(encoding="utf-8"))

    assert card.chapter_number == 12
    assert card_payload["chapter_duty"]
    assert card_payload["conflict"]
    assert card_payload["chapter_turn"]
    assert not {"duty", "information", "reader_payoff"} & set(card_payload)
    assert card_payload["hook"]
    assert beat.chapter_number == 12
    assert len(beat_payload["beats"]) == 5
    assert all(item["chapter_duty"] == card_payload["chapter_duty"] for item in beat_payload["beats"])
    assert all(item["reader_gain"] == card_payload["reader_gain"] for item in beat_payload["beats"])
    assert all(item["chapter_turn"] for item in beat_payload["beats"])
    assert all(not {"duty", "information", "reader_payoff"} & set(item) for item in beat_payload["beats"])


def test_plan_chapter_event_matrix_requires_soft_event_after_fast_gap(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "30_state" / "pacing_history.json").write_text(
        json.dumps(
            [
                {"chapter_number": 1, "tier": "fast", "event_types": ["conflict_thrill"]},
                {"chapter_number": 2, "tier": "fast", "event_types": ["tension_escalation"]},
                {"chapter_number": 3, "tier": "fast", "event_types": ["conflict_thrill"]},
                {"chapter_number": 4, "tier": "fast", "event_types": ["tension_escalation"]},
                {"chapter_number": 5, "tier": "fast", "event_types": ["conflict_thrill"]},
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    plan_chapter(project_config, chapter_number=6)

    card = json.loads((root / "20_outline" / "chapter_cards" / "ch006.json").read_text(encoding="utf-8"))
    matrix = json.loads((root / "30_state" / "event_matrix.json").read_text(encoding="utf-8"))
    recommendation = card["event_recommendation"]

    assert recommendation["soft_event_required"] is True
    assert recommendation["recommended"][0] in {"bond_deepening", "faction_building", "world_painting"}
    assert any("soft event required" in item for item in recommendation["constraints"])
    assert matrix["latest_recommendation"]["chapter_number"] == 6
    assert matrix["recent_5"][-1]["chapter_number"] == 5


def test_continue_write_creates_agent_writing_task_by_default(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)

    result = continue_write(project_config, chapter_number=1)

    root = tmp_path / "novel"
    assert result.status == "task_ready"
    assert (root / "20_outline" / "chapter_cards" / "ch001.json").exists()
    assert (root / "50_workbench" / "beats" / "ch001.md").exists()
    assert (root / "50_workbench" / "writing_tasks" / "ch001.json").exists()
    assert (root / "50_workbench" / "writing_tasks" / "ch001.md").exists()
    assert not (root / "40_manuscript" / "draft" / "ch001.md").exists()
    assert (root / "70_runtime" / "run_reports" / "continue_write_ch001.json").exists()
    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8"))
    task_md = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")
    report = json.loads((root / "70_runtime" / "run_reports" / "continue_write_ch001.json").read_text(encoding="utf-8"))

    assert task["status"] == "task_ready"
    assert task["writing_mode"] == "agent_skill"
    assert task["draft_submission_path"] == "50_workbench/agent_drafts/ch001.codex.md"
    assert "draft submit" in task["next_command"]
    assert task["fact_inventory_summary"]["categories"].get("feedback", 0) == 0
    assert "第 001 章故事工作单" in task_md
    assert "## 未解决反馈" not in task_md
    assert "## 唯一章节合同" not in task_md
    assert "## 当前写作方法" not in task_md
    assert "## 逐场行动" in task_md
    assert "## 演出边界" in task_md
    assert report["artifacts"]["writing_task_markdown"].endswith("ch001.md")

    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "task_ready"
    assert state["current_chapter"] == 1
    assert state["pending_task_chapter"] == 1
    assert state["last_finalized_chapter"] == 0


def test_continue_write_template_dry_run_still_creates_draft_and_gate(tmp_path):
    project_config = seed_project(tmp_path, writing_mode="template_dry_run")
    open_book(project_config)

    result = continue_write(project_config, chapter_number=1)

    root = tmp_path / "novel"
    assert result.status.startswith("draft_ready_gate_")
    assert (root / "40_manuscript" / "draft" / "ch001.md").exists()
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").exists()


def test_continue_write_blocks_after_previous_gate_failure(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    gate_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "gate_result.json").write_text(
        json.dumps({"chapter_number": 1, "passed": False, "failures": ["continuity"]}, ensure_ascii=False),
        encoding="utf-8",
    )

    try:
        continue_write(project_config, chapter_number=2)
    except WorkflowError as exc:
        assert "review-and-repair workflow" in str(exc)
        assert "production next" in str(exc)
    else:
        raise AssertionError("Expected WorkflowError")


def test_auto_write_plan_and_run_waits_for_agent_draft(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    plan = auto_write_plan(project_config)
    run = auto_write_run(project_config)
    progress = auto_write_progress(project_config)
    report = auto_write_report(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))
    report_text = (root / "70_runtime" / "run_reports" / "auto_write_report.md").read_text(encoding="utf-8")

    assert plan.status == "planned"
    assert run.status == "awaiting_agent_draft"
    assert progress.status == "awaiting_agent_draft"
    assert report.report_file.endswith("auto_write_report.md")
    assert state["target_characters"] == 2_000_000
    assert state["forecast_chapters"] == 667
    assert state["current_chapter"] == 1
    assert state["failure_count"] == 0
    assert "draft submit" in state["next_command"]
    assert state["agent_task_status"]["current"]["by_type"]["chapter_write"] == 1
    assert state["agent_task_status"]["waiting_kinds"] == ["awaiting_agent_draft"]
    assert state["agent_task_status"]["latest"]["task_type"] == "chapter_write"
    assert "Auto-Write Progress Report" in report_text
    assert "## Agent Tasks" in report_text
    assert "awaiting_agent_draft" in report_text
    assert "Candidate prose must still pass" in report_text
    assert (root / "50_workbench" / "writing_tasks" / "ch001.md").exists()
    assert not (root / "40_manuscript" / "draft" / "ch001.md").exists()
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_auto_write_resume_after_finalize_pauses_for_next_causal_window(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    auto_write_plan(project_config)
    first = auto_write_run(project_config)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    approve_story_candidate(root, project_config)
    finalize_chapter(project_config, chapter_number=1, approved_by="human")
    second = auto_write_run(project_config)
    complete_unified_semantic_lifecycle(root, project_config, 1)
    third = auto_write_run(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert first.status == "awaiting_agent_draft"
    assert second.status == "blocked"
    assert "semantic" in second.next_command
    assert third.status == "blocked"
    assert "arc_simulation" in third.next_command
    assert state["last_finalized_chapter"] == 1
    assert state["current_chapter"] == 2
    assert "arc_simulation" in state["next_command"]
    assert not (root / "50_workbench" / "writing_tasks" / "ch002.md").exists()


def test_auto_write_pauses_on_gate_failure(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    auto_write_plan(project_config)
    auto_write_run(project_config)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text("# Chapter 1\n\nTODO: unfinished draft.\n", encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    paused = auto_write_run(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert paused.status == "paused_gate_failed"
    assert state["failure_count"] >= 1
    assert "failed gate" in state["pause_reason"]
    assert state["next_command"] == "longform-engine production next project.yaml"
    assert not (root / "50_workbench" / "writing_tasks" / "ch002.md").exists()
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_auto_write_pauses_when_gate_passed_but_not_final(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    auto_write_plan(project_config)
    auto_write_run(project_config)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    paused = auto_write_run(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert paused.status == "reviews_pending"
    assert "review pipeline" in paused.pause_reason
    assert state["next_command"] == "longform-engine production next project.yaml"
    assert not (root / "50_workbench" / "writing_tasks" / "ch002.md").exists()


def test_auto_write_recognizes_repair_semantic_and_editorial_agent_waits(tmp_path):
    repair_config = seed_project(tmp_path / "repair")
    repair_root = tmp_path / "repair" / "novel"
    write_repair_manifest(repair_root)
    auto_write_plan(repair_config)
    repair = auto_write_run(repair_config)
    repair_state = json.loads((repair_root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    semantic_config = seed_project(tmp_path / "semantic")
    semantic_root = tmp_path / "semantic" / "novel"
    mark_project_ready(semantic_root, semantic_config)
    (semantic_root / "40_manuscript" / "draft" / "ch001.md").write_text(passing_draft_text(), encoding="utf-8")
    semantic_pacing_task(semantic_config, chapter_number=1)
    auto_write_plan(semantic_config)
    semantic = auto_write_run(semantic_config)
    semantic_state = json.loads((semantic_root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    editorial_config = seed_project(tmp_path / "editorial")
    editorial_root = tmp_path / "editorial" / "novel"
    mark_project_ready(editorial_root, editorial_config)
    (editorial_root / "40_manuscript" / "draft" / "ch001.md").write_text(passing_draft_text(), encoding="utf-8")
    editorial_review(editorial_config, chapter_number=1)
    auto_write_plan(editorial_config)
    editorial = auto_write_run(editorial_config)
    editorial_state = json.loads((editorial_root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert repair.status == "awaiting_repair_candidate"
    assert repair_state["agent_task_status"]["waiting_kinds"] == ["awaiting_repair_candidate"]
    assert "draft submit" in repair_state["next_command"]
    assert "repair" in repair_state["agent_task_status"]["latest"]["task_type"]

    assert semantic.status == "awaiting_semantic_output"
    assert semantic_state["agent_task_status"]["waiting_kinds"] == ["awaiting_semantic_output"]
    assert "pacing semantic-validate" in semantic_state["next_command"]
    assert semantic_state["agent_task_status"]["latest"]["task_type"] == "pacing_review"

    assert editorial.status == "awaiting_editorial_result"
    assert editorial_state["agent_task_status"]["waiting_kinds"] == ["awaiting_editorial_result"]
    assert "editorial submit-review" in editorial_state["next_command"]
    assert editorial_state["agent_task_status"]["latest"]["task_type"] == "editorial_review"


def test_submit_agent_draft_records_submission_and_runs_gate(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    root = tmp_path / "novel"
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")

    result = submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")

    draft_path = root / "40_manuscript" / "draft" / "ch001.md"
    submission_path = root / "40_manuscript" / "draft" / "ch001.submission.json"
    gate_result = root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json"
    pacing_review = root / "50_workbench" / "gate_artifacts" / "ch001" / "pacing_review.md"
    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    submission = json.loads(submission_path.read_text(encoding="utf-8"))
    gate_payload = json.loads(gate_result.read_text(encoding="utf-8"))
    chapters = query_table(project_config, "chapters", limit=10)

    assert result.passed is True
    assert result.next_command == "longform-engine production next project.yaml"
    assert draft_path.exists()
    assert submission_path.exists()
    assert gate_result.exists()
    assert pacing_review.exists()
    assert submission["agent"] == "codex"
    assert submission["source_file"] == "50_workbench/agent_drafts/ch001.codex.md"
    assert submission["draft_file"] == "40_manuscript/draft/ch001.md"
    assert submission["source_sha256"]
    assert submission["draft_sha256"]
    assert submission["submitted_at"]
    assert gate_payload["next_command"] == "longform-engine production next project.yaml"
    assert gate_payload["workflow_stage"] == "reviews_pending"
    assert state["status"] == "reviews_pending"
    assert state["pending_gate_chapter"] == 1
    assert any(row["chapter_number"] == 1 and row["status"] == "reviews_pending" for row in chapters)


def test_submit_agent_draft_waits_for_required_semantic_review_without_invalidating_prose(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    project_config.data["quality"]["semantic_review_milestones"] = [1]
    project_config.data["quality"]["semantic_review_boundaries"] = True
    continue_write(project_config, chapter_number=1)
    root = tmp_path / "novel"
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")

    result = submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    tasks = {task["task_id"]: task for task in list_manifests(root)}
    next_action = production_next(project_config)
    gate = json.loads(
        (root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8")
    )

    assert result.passed is True
    assert result.next_command == "longform-engine production next project.yaml"
    assert tasks["chapter_write:ch001:v4"]["status"] == "submitted"
    assert tasks["semantic_review:ch001:v4"]["status"] == "awaiting_agent"
    assert next_action["task_type"] == "semantic_review"
    assert next_action["status"] == "agent_task_awaiting_agent"
    assert "agent_semantic_review" in gate["allowed_actions"]
    assert gate["workflow_stage"] == "reviews_pending"


def test_submit_agent_draft_requires_agent_draft_directory(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    outside = tmp_path / "outside.md"
    outside.write_text(passing_draft_text(), encoding="utf-8")

    try:
        submit_agent_draft(project_config, chapter_number=1, file_path=outside, agent="codex")
    except WorkflowError as exc:
        assert "configured draft_dir" in str(exc)
    else:
        raise AssertionError("Expected WorkflowError")


def test_failed_agent_draft_does_not_update_story_graph(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    before = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(
        "# Chapter 1\n\nTODO: write this chapter later. Author note, outline, and meta instructions remain here.\n",
        encoding="utf-8",
    )

    result = submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")

    after = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    chapters = query_table(project_config, "chapters", limit=20)
    chunks = query_table(project_config, "chapter_chunks", limit=20)
    assert result.passed is False
    assert state["last_finalized_chapter"] == 0
    assert state["status"] == "reviews_pending"
    assert after.get("events", []) == before.get("events", [])
    assert all(not entity.get("mentions") for entity in after.get("entities", []))
    assert any(row["chapter_number"] == 1 and row["status"] == "reviews_pending" for row in chapters)
    assert not any(row["chapter_number"] == 1 and row["status"] == "final" for row in chapters)
    assert not chunks


def test_finalize_chapter_requires_gate_and_refreshes_memory(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    draft_text = passing_draft_text()
    entity_name = draft_text.split("\n\n", 1)[1][:2]
    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:lin", "name": entity_name, "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(draft_text, encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    pending_state_path = root / "30_state" / "novel_state.json"
    pending_state = json.loads(pending_state_path.read_text(encoding="utf-8"))
    pending_state["pending_semantic_review_chapter"] = 1
    pending_state_path.write_text(json.dumps(pending_state, ensure_ascii=False, indent=2), encoding="utf-8")

    approve_story_candidate(root, project_config)
    result = finalize_chapter(project_config, chapter_number=1, approved_by="human")
    finalize_chapter(project_config, chapter_number=1, approved_by="human", overwrite=True)
    semantic_ledger = root / "30_state" / "semantic_ledger" / "ch001.json"
    semantic_ledger.parent.mkdir(parents=True, exist_ok=True)
    semantic_ledger.write_text('{"canonical": true}\n', encoding="utf-8")
    try:
        finalize_chapter(project_config, chapter_number=1, approved_by="human", overwrite=True)
    except WorkflowError as exc:
        assert "immutable after semantic apply" in str(exc)
    else:
        raise AssertionError("Expected semantic evidence immutability to block final overwrite")
    semantic_ledger.unlink()

    final_path = root / "40_manuscript" / "final" / "ch001.md"
    finalization_path = root / "40_manuscript" / "final" / "ch001.finalization.json"
    summary_path = root / "40_manuscript" / "summaries" / "ch001.md"
    story_graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    chunks = query_table(project_config, "chapter_chunks", limit=20)
    chapters = query_table(project_config, "chapters", limit=20)
    reward_entries = [
        json.loads(line)
        for line in (root / "30_state" / "reward_ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert result.next_command == "longform-engine chapter semantic-task project.yaml --chapter 1"
    assert final_path.exists()
    assert finalization_path.exists()
    assert summary_path.exists()
    assert not (root / "60_rag" / "chunks" / "ch001.json").exists()
    assert (root / "60_rag" / "context" / "next_plot_context.md").exists()
    assert not any(event.get("chapter_number") == 1 for event in story_graph["events"])
    assert state["status"] == "chapter_finalized_pending_semantics"
    assert state["last_finalized_chapter"] == 1
    assert "pending_semantic_review_chapter" not in state
    assert state["pending_semantic_chapter"] == 1
    assert not any(row["chapter_number"] == 1 and row["status"] == "final" for row in chapters)
    assert not any(row["chapter_number"] == 1 for row in chunks)
    assert reward_entries[-1]["schema"] == "reader_reward_entry_v2"
    assert reward_entries[-1]["chapter_number"] == 1
    assert reward_entries[-1]["chapter_duty"]
    assert reward_entries[-1]["planned_gain"]
    assert reward_entries[-1]["observed_gain"] == ""
    assert reward_entries[-1]["observation_status"] == "not_required"
    assert len([item for item in reward_entries if item["chapter_number"] == 1]) == 1
    transaction_reports = list((root / "70_runtime" / "transactions").glob("*chapter_finalize_ch001*.json"))
    assert transaction_reports
    transaction = json.loads(transaction_reports[-1].read_text(encoding="utf-8"))
    assert transaction["command"] == "chapter finalize"
    assert "40_manuscript/final/ch001.md" in transaction["touched_paths"]
    assert "chapter semantic-apply" in transaction["metadata"]["rebuild_boundaries"]
    assert "SQLite sync" in transaction["metadata"]["rebuild_boundaries"]
    assert transaction["boundary"]["agent_outputs_directly_applied"] is False
    assert transaction["boundary"]["rollback_restores_touched_paths"] is True


def test_semantic_apply_rolls_back_touched_paths_on_index_failure(tmp_path, monkeypatch):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    approve_story_candidate(root, project_config)
    finalize_chapter(project_config, chapter_number=1, approved_by="human")
    semantic_output = prepare_unified_semantic_bundle(root, project_config, 1)
    graph_before = (root / "30_state" / "story_graph.json").read_text(encoding="utf-8")
    state_before = (root / "30_state" / "novel_state.json").read_text(encoding="utf-8")
    promise_ledger = root / "30_state" / "reader_promise_ledger.json"
    promise_before = promise_ledger.read_bytes()
    simulations_before = {
        path: path.read_bytes()
        for path in (root / "20_outline" / "arc_simulations").glob("ch*-ch*.json")
    }

    def fail_build_chunks(*args, **kwargs):
        raise RuntimeError("simulated rag rebuild failure")

    monkeypatch.setattr("longform_engine.semantic.pipeline.build_chunks", fail_build_chunks)

    try:
        semantic_apply(project_config, chapter_number=1, file_path=semantic_output)
    except RuntimeError as exc:
        assert "simulated rag rebuild failure" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")

    rollback_reports = list((root / "70_runtime" / "transactions").glob("*chapter_semantic_apply*rollback.json"))
    assert rollback_reports
    rollback = json.loads(rollback_reports[-1].read_text(encoding="utf-8"))
    assert rollback["status"] == "rolled_back"
    assert rollback["error"]["message"] == "simulated rag rebuild failure"
    assert "30_state/semantic_ledger/ch001.json" in rollback["touched_paths"]
    assert (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (root / "30_state" / "semantic_ledger" / "ch001.json").exists()
    assert (root / "30_state" / "story_graph.json").read_text(encoding="utf-8") == graph_before
    assert (root / "30_state" / "novel_state.json").read_text(encoding="utf-8") == state_before
    assert promise_ledger.read_bytes() == promise_before
    assert all(path.read_bytes() == content for path, content in simulations_before.items())
    assert not query_table(project_config, "chapter_chunks", limit=20)


def test_continue_write_blocks_when_previous_chapter_is_not_final(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")

    try:
        continue_write(project_config, chapter_number=2)
    except WorkflowError as exc:
        assert "not finalized" in str(exc)
    else:
        raise AssertionError("Expected WorkflowError")
    assert not (root / "50_workbench" / "writing_tasks" / "ch002.md").exists()


def test_continue_write_does_not_leak_previous_editorial_findings_to_author(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    approve_story_candidate(root, project_config)
    finalize_chapter(project_config, chapter_number=1, approved_by="human")

    gate_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    (gate_dir / "repair_plan.md").write_text(
        "# Repair Plan\n\n- Strengthen motive before the next conflict.\n",
        encoding="utf-8",
    )
    humanizer_dir = root / "50_workbench" / "humanizer_tasks"
    humanizer_dir.mkdir(parents=True, exist_ok=True)
    (humanizer_dir / "ch001.humanize_check.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "passed": False,
                "issues": [{"code": "humanizer_summary_voice", "severity": "P1", "message": "too much summary"}],
                "warnings": [],
                "next_command": "longform-engine creative humanize-task project.yaml --chapter 1 --source draft",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (gate_dir / "semantic_pacing_result.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "verdict": "warning",
                "tier": "slow",
                "issues": [{"code": "tail_hook_weak", "severity": "P2", "message": "hook needs sharper pressure"}],
                "warnings": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    editorial_dir = root / "50_workbench" / "editorial_reviews"
    editorial_dir.mkdir(parents=True, exist_ok=True)
    (editorial_dir / "ch001.aggregate.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "need_human": False,
                "severity_counts": {"P0": 0, "P1": 0, "P2": 1},
                "unresolved_items": [{"code": "motive_gap", "severity": "P2", "message": "motive could use more evidence"}],
                "need_human_reasons": [],
                "next_command": "longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    complete_unified_semantic_lifecycle(root, project_config, 1)
    simulation_path = write_arc_simulation_fixture(root, from_chapter=1, to_chapter=20)
    chapter_two_card_path = root / "20_outline" / "chapter_cards" / "ch002.json"
    chapter_two_card = json.loads(chapter_two_card_path.read_text(encoding="utf-8"))
    chapter_two_card["arc_simulation_ref"] = {
        "path": simulation_path.relative_to(root).as_posix(),
        "sha256": sha256(simulation_path.read_bytes()).hexdigest(),
        "from_chapter": 1,
        "to_chapter": 20,
    }
    stamp_chapter_contract(chapter_two_card)
    chapter_two_card_path.write_text(
        json.dumps(chapter_two_card, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    result = continue_write(project_config, chapter_number=2)

    assert result.status == "task_ready"
    task_path = root / "50_workbench" / "writing_tasks" / "ch002.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task_md = (root / "50_workbench" / "writing_tasks" / "ch002.md").read_text(encoding="utf-8")
    manifest = json.loads((root / "50_workbench" / "writing_tasks" / "ch002.agent_task.json").read_text(encoding="utf-8"))
    assert "feedback" not in task["fact_inventory_summary"]["categories"]
    assert "pattern" not in task["fact_inventory_summary"]["categories"]
    assert "未解决反馈" not in task_md
    assert "humanizer_summary_voice" not in task_md
    assert "motive_gap" not in task_md
    assert "story graph must remain frozen" not in task_md
    assert "graph update waits for chapter finalize" not in task_md
    manifest_inputs = {item["path"] for item in manifest["io"]["inputs"]}
    assert len(manifest_inputs) <= 7
    assert manifest_inputs == {"50_workbench/writing_tasks/ch002.md"}
    assert not (root / "40_manuscript" / "final" / "ch002.md").exists()
    assert not (root / "60_rag" / "chunks" / "ch002.json").exists()
    story_graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    assert not any(event.get("chapter_number") == 2 for event in story_graph.get("events", []))


def test_finalize_chapter_blocks_failed_gate_without_waiver(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text("# 第一章\n\nTODO 写作说明：这一章还不能定稿。\n", encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")

    try:
        finalize_chapter(project_config, chapter_number=1, approved_by="human")
    except WorkflowError as exc:
        assert "not finalizable" in str(exc)
    else:
        raise AssertionError("Expected WorkflowError")
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def seed_project(tmp_path, *, writing_mode: str = "agent_skill"):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    return load_project_config(
        project.project_config,
        cli_overrides={
            "writing": {"mode": writing_mode},
            "editorial": {"review_mode": "off"},
        },
    )


def passing_draft_text() -> str:
    sentence = "林迟沿着山门石阶向上，旧钟声在雾里回荡，他记住师父留下的规矩，也看见山下灯火一步步逼近。"
    return "# 第一章 山门\n\n" + sentence * 80 + "\n\n然而，门外又响起了第二个人的脚步。\n"


def write_repair_manifest(root):
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.r01.codex.md"
    manifest = build_manifest(
        root,
        task_type="repair",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[candidate],
        output_schema=PROSE_MARKDOWN_SCHEMA,
        validate_command=(
            "longform-engine draft submit project.yaml --chapter 1 "
            "--file 50_workbench/repair_candidates/ch001.r01.codex.md --agent codex --overwrite"
        ),
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine agent-task brief project.yaml repair:ch001:r01:v4",
        task_id="repair:ch001:r01:v4",
    )
    write_manifest(root, manifest, root / "50_workbench" / "repair_candidates" / "ch001.repair_task.agent_task.json")
