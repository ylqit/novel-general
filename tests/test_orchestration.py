import json

from longform_engine.agent_tasks import build_manifest, write_manifest
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
    open_book,
    plan_chapter,
    submit_agent_draft,
)
from longform_engine.db import query_table
from longform_engine.storage import init_project


def test_open_book_writes_five_confirmations(tmp_path):
    project_config = seed_project(tmp_path)

    result = open_book(project_config)

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
    assert card_payload["duty"]
    assert card_payload["conflict"]
    assert card_payload["information"]
    assert card_payload["hook"]
    assert beat.chapter_number == 12
    assert len(beat_payload["beats"]) == 5


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
    assert task["feedback_carryover"]["status"] == "none"
    assert task["feedback_carryover"]["source_files"] == []
    assert "Writing Task ch001" in task_md
    assert "## Feedback Carryover" in task_md
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
        assert "failed gate" in str(exc)
    else:
        raise AssertionError("Expected WorkflowError")


def test_auto_write_plan_and_run_waits_for_agent_draft(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    plan = auto_write_plan(project_config, target_chapters=3, target_words=9000)
    run = auto_write_run(project_config)
    progress = auto_write_progress(project_config)
    report = auto_write_report(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))
    report_text = (root / "70_runtime" / "run_reports" / "auto_write_report.md").read_text(encoding="utf-8")

    assert plan.status == "planned"
    assert run.status == "awaiting_agent_draft"
    assert progress.status == "awaiting_agent_draft"
    assert report.report_file.endswith("auto_write_report.md")
    assert state["target_words"] == 9000
    assert state["target_chapters"] == 3
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


def test_auto_write_resume_after_finalize_schedules_next_chapter(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    auto_write_plan(project_config, target_chapters=2, target_words=6000)
    first = auto_write_run(project_config)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    finalize_chapter(project_config, chapter_number=1, approved_by="human")
    second = auto_write_run(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert first.status == "awaiting_agent_draft"
    assert second.status == "awaiting_agent_draft"
    assert state["last_finalized_chapter"] == 1
    assert state["current_chapter"] == 2
    assert "ch002.codex.md" in state["next_command"]
    assert (root / "50_workbench" / "writing_tasks" / "ch002.md").exists()


def test_auto_write_pauses_on_gate_failure(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    auto_write_plan(project_config, target_chapters=2, target_words=6000)
    auto_write_run(project_config)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text("# Chapter 1\n\nTODO: unfinished draft.\n", encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    paused = auto_write_run(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert paused.status == "paused_gate_failed"
    assert state["failure_count"] >= 1
    assert "failed gate" in state["pause_reason"]
    assert "repair-chapter" in state["next_command"]
    assert not (root / "50_workbench" / "writing_tasks" / "ch002.md").exists()
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_auto_write_pauses_when_gate_passed_but_not_final(tmp_path):
    project_config = seed_project(tmp_path)
    open_book(project_config)
    root = tmp_path / "novel"

    auto_write_plan(project_config, target_chapters=2, target_words=6000)
    auto_write_run(project_config)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    paused = auto_write_run(project_config)
    state = json.loads((root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert paused.status == "awaiting_finalize"
    assert "not finalized" in paused.pause_reason
    assert "chapter finalize" in state["next_command"]
    assert not (root / "50_workbench" / "writing_tasks" / "ch002.md").exists()


def test_auto_write_recognizes_repair_semantic_and_editorial_agent_waits(tmp_path):
    repair_config = seed_project(tmp_path / "repair")
    repair_root = tmp_path / "repair" / "novel"
    write_repair_manifest(repair_root)
    auto_write_plan(repair_config, target_chapters=1, target_words=3000)
    repair = auto_write_run(repair_config)
    repair_state = json.loads((repair_root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    semantic_config = seed_project(tmp_path / "semantic")
    semantic_root = tmp_path / "semantic" / "novel"
    (semantic_root / "40_manuscript" / "draft" / "ch001.md").write_text(passing_draft_text(), encoding="utf-8")
    semantic_pacing_task(semantic_config, chapter_number=1)
    auto_write_plan(semantic_config, target_chapters=1, target_words=3000)
    semantic = auto_write_run(semantic_config)
    semantic_state = json.loads((semantic_root / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    editorial_config = seed_project(tmp_path / "editorial")
    editorial_root = tmp_path / "editorial" / "novel"
    (editorial_root / "40_manuscript" / "draft" / "ch001.md").write_text(passing_draft_text(), encoding="utf-8")
    editorial_review(editorial_config, chapter_number=1)
    auto_write_plan(editorial_config, target_chapters=1, target_words=3000)
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
    assert result.next_command == "chapter finalize --chapter 1 --approved-by human"
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
    assert gate_payload["next_command"] == "chapter finalize --chapter 1 --approved-by human"
    assert "chapter_finalize" in gate_payload["allowed_actions"]
    assert state["status"] == "gate_passed_pending_finalize"
    assert state["pending_final_chapter"] == 1
    assert any(row["chapter_number"] == 1 and row["status"] == "gate_passed" for row in chapters)


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
    assert state["status"] == "gate_failed"
    assert after.get("events", []) == before.get("events", [])
    assert all(not entity.get("mentions") for entity in after.get("entities", []))
    assert any(row["chapter_number"] == 1 and row["status"] == "gate_failed" for row in chapters)
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

    result = finalize_chapter(project_config, chapter_number=1, approved_by="human")

    final_path = root / "40_manuscript" / "final" / "ch001.md"
    finalization_path = root / "40_manuscript" / "final" / "ch001.finalization.json"
    summary_path = root / "40_manuscript" / "summaries" / "ch001.md"
    story_graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    chunks = query_table(project_config, "chapter_chunks", limit=20)
    chapters = query_table(project_config, "chapters", limit=20)

    assert result.next_command == "continue-write --chapter 2"
    assert final_path.exists()
    assert finalization_path.exists()
    assert summary_path.exists()
    assert (root / "60_rag" / "chunks" / "ch001.json").exists()
    assert (root / "60_rag" / "context" / "next_plot_context.md").exists()
    assert any(event.get("chapter_number") == 1 for event in story_graph["events"])
    assert any(entity.get("id") == "character:lin" and entity.get("mentions") for entity in story_graph["entities"])
    assert state["status"] == "chapter_finalized"
    assert state["last_finalized_chapter"] == 1
    assert any(row["chapter_number"] == 1 and row["status"] == "final" for row in chapters)
    assert any(row["chapter_number"] == 1 for row in chunks)
    transaction_reports = list((root / "70_runtime" / "transactions").glob("*chapter_finalize_ch001*.json"))
    assert transaction_reports
    transaction = json.loads(transaction_reports[-1].read_text(encoding="utf-8"))
    assert transaction["command"] == "chapter finalize"
    assert "40_manuscript/final/ch001.md" in transaction["touched_paths"]
    assert "RAG rebuild/sync" in transaction["metadata"]["rebuild_boundaries"]
    assert "SQLite sync" in transaction["metadata"]["rebuild_boundaries"]
    assert transaction["boundary"]["agent_outputs_directly_applied"] is False
    assert transaction["boundary"]["rollback_restores_touched_paths"] is True


def test_finalize_chapter_rolls_back_touched_paths_on_apply_failure(tmp_path, monkeypatch):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    graph_before = (root / "30_state" / "story_graph.json").read_text(encoding="utf-8")
    state_before = (root / "30_state" / "novel_state.json").read_text(encoding="utf-8")

    def fail_build_chunks(*args, **kwargs):
        raise RuntimeError("simulated rag rebuild failure")

    monkeypatch.setattr("longform_engine.orchestration.pipeline.build_chunks", fail_build_chunks)

    try:
        finalize_chapter(project_config, chapter_number=1, approved_by="human")
    except RuntimeError as exc:
        assert "simulated rag rebuild failure" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError")

    rollback_reports = list((root / "70_runtime" / "transactions").glob("*chapter_finalize_ch001.rollback.json"))
    assert rollback_reports
    rollback = json.loads(rollback_reports[-1].read_text(encoding="utf-8"))
    assert rollback["status"] == "rolled_back"
    assert rollback["error"]["message"] == "simulated rag rebuild failure"
    assert "40_manuscript/final/ch001.md" in rollback["touched_paths"]
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (root / "40_manuscript" / "summaries" / "ch001.md").exists()
    assert (root / "30_state" / "story_graph.json").read_text(encoding="utf-8") == graph_before
    assert (root / "30_state" / "novel_state.json").read_text(encoding="utf-8") == state_before
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


def test_continue_write_carries_previous_controlled_feedback_forward(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
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
                "need_human": True,
                "severity_counts": {"P0": 0, "P1": 1, "P2": 0},
                "unresolved_items": [{"code": "motive_gap", "severity": "P1", "message": "motive needs evidence"}],
                "need_human_reasons": ["unresolved_P1"],
                "next_command": "longform-engine editorial need-human project.yaml --chapter 1 --reason unresolved_P1",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = continue_write(project_config, chapter_number=2)

    assert result.status == "task_ready"
    task_path = root / "50_workbench" / "writing_tasks" / "ch002.json"
    task = json.loads(task_path.read_text(encoding="utf-8"))
    task_md = (root / "50_workbench" / "writing_tasks" / "ch002.md").read_text(encoding="utf-8")
    manifest = json.loads((root / "50_workbench" / "writing_tasks" / "ch002.agent_task.json").read_text(encoding="utf-8"))
    feedback = task["feedback_carryover"]

    assert feedback["status"] == "available"
    assert feedback["source_chapter"] == 1
    assert "50_workbench/gate_artifacts/ch001/gate_result.json" in feedback["source_files"]
    assert "50_workbench/gate_artifacts/ch001/repair_plan.md" in feedback["source_files"]
    assert "50_workbench/humanizer_tasks/ch001.humanize_check.json" in feedback["source_files"]
    assert "50_workbench/gate_artifacts/ch001/semantic_pacing_result.json" in feedback["source_files"]
    assert "50_workbench/editorial_reviews/ch001.aggregate.json" in feedback["source_files"]
    assert any(item["kind"] == "humanize_check" and "summary" in item for item in feedback["items"])
    assert "## Feedback Carryover" in task_md
    assert "humanizer_summary_voice" in task_md
    for source in feedback["source_files"]:
        assert source in manifest["input_files"]
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
    return load_project_config(project.project_config, cli_overrides={"writing": {"mode": writing_mode}})


def passing_draft_text() -> str:
    sentence = "林迟沿着山门石阶向上，旧钟声在雾里回荡，他记住师父留下的规矩，也看见山下灯火一步步逼近。"
    return "# 第一章 山门\n\n" + sentence * 80 + "\n"


def write_repair_manifest(root):
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.codex.repair_candidate.md"
    manifest = build_manifest(
        root,
        task_type="repair",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[candidate],
        output_schema="markdown_repair_candidate",
        validate_command=(
            "longform-engine draft submit project.yaml --chapter 1 "
            "--file 50_workbench/repair_candidates/ch001.codex.repair_candidate.md --agent codex --overwrite"
        ),
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
        task_id="repair:ch001:v1",
    )
    write_manifest(root, manifest, root / "50_workbench" / "repair_candidates" / "ch001.repair_task.agent_task.json")
