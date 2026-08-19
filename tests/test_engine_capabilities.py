import json
import subprocess
import sys
from pathlib import Path

from longform_engine.config import ConfigError, load_project_config
from longform_engine.db import query_table, rebuild_database
from longform_engine.editorial import editorial_batch_review, editorial_review, editorial_status
from longform_engine.gates import gate_check
from longform_engine.graph import apply_graph_updates, cascade_graph, extract_graph_updates, update_graph
from longform_engine.orchestration import (
    WorkflowError,
    batch_write,
    continue_write,
    finalize_chapter,
    open_book,
    plan_chapter,
    submit_agent_draft,
)
from longform_engine.planning import revise_outline
from longform_engine.rag import build_chunks, build_context, query
from longform_engine.research import detect_knowledge_gaps, search_research
from longform_engine.revision import rollback
from longform_engine.storage import init_project
from tests.project_fixtures import complete_unified_semantic_lifecycle, mark_project_ready


def test_rag_metadata_context_and_source_safety(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "30_state" / "story_graph.json").write_text(
        json.dumps(
            {
                "entities": [{"id": "character:ari", "name": "Ari", "type": "character"}],
                "relationships": [],
                "events": [{"id": "event:ch001:choice", "chapter_number": 1, "title": "Ari opens the gate"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# Chapter 1\n\nAri finds the black gate. A strange omen marks the future choice.\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "summaries" / "ch001.md").write_text("Ari opens the gate.\n", encoding="utf-8")
    (root / "50_workbench" / "research_inbox" / "note.md").write_text("INBOXLEAK", encoding="utf-8")

    build_chunks(config, max_chars=200, overlap_chars=0)
    result = query(config, "Ari gate", top_k=3)
    context = build_context(config, chapter_number=2, query_text="Ari gate", top_k=3)
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")

    assert result.hits
    assert result.hits[0].entities == ("Ari",)
    assert "Relationship Snippets" in context_text
    assert "Graph Facts" in context_text
    assert "Forbidden Repeats" in context_text
    assert "INBOXLEAK" not in context_text
    assert context.hit_count >= 1


def test_graph_suggestions_low_confidence_and_cascade(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}]),
        encoding="utf-8",
    )
    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# Chapter 1\n\nAri sees an omen before the duel.\n",
        encoding="utf-8",
    )

    update = update_graph(config, chapter_number=1)
    extracted = extract_graph_updates(config, chapter_number=1, source="final")
    applied = apply_graph_updates(config, chapter_number=1)
    cascade = cascade_graph(config, from_chapter=1, change_description="move duel later")
    graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))

    assert update.update_file.endswith("ch001.json")
    assert extracted.low_confidence >= 1
    assert applied.skipped_low_confidence >= 1
    assert not any(entity.get("type") == "foreshadowing" for entity in graph["entities"])
    assert cascade.marked_events >= 1
    assert any(event.get("cascade_pending") for event in graph["events"])


def test_revise_outline_blocks_until_db_rebuild(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "20_outline" / "chapter_plan.json").write_text(
        json.dumps([{"chapter_number": 1, "title": "Old start", "chapter_duty": "old duty"}]),
        encoding="utf-8",
    )

    result = revise_outline(config, from_chapter=1, change_description="change opening promise")

    assert (root / "20_outline" / "outline_anchors.json").exists()
    assert (root / "60_rag" / "stale.json").exists()
    assert result.report_file.endswith(".md")
    try:
        continue_write(config, chapter_number=1)
    except WorkflowError as exc:
        assert "Stale outline/RAG artifacts" in str(exc)
    else:
        raise AssertionError("Expected stale blocker")

    rebuild_database(config)
    open_book(config)
    mark_project_ready(root, config)
    ready = continue_write(config, chapter_number=1)
    assert ready.status == "task_ready"


def test_gate_writes_style_humanizer_copyedit_and_memory_artifacts(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    paragraph = "Ari repeats the same tactical beat and never changes the scene. "
    text = "# Chapter 1\n\n" + "\n\n".join([paragraph * 10 for _ in range(5)])
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(text, encoding="utf-8")

    result = gate_check(config, chapter_number=1)
    artifact_dir = root / "50_workbench" / "gate_artifacts" / "ch001"

    assert result.passed is False
    assert any(failure["code"] == "duplicate_paragraphs" for failure in result.failures)
    assert (artifact_dir / "style_review.md").exists()
    assert (artifact_dir / "humanize_report.md").exists()
    assert (artifact_dir / "copyedit_report.md").exists()
    assert (artifact_dir / "memory_update.md").exists()
    gate_payload = json.loads((artifact_dir / "gate_result.json").read_text(encoding="utf-8"))
    assert gate_payload["next_command"] == "longform-engine production next project.yaml"
    assert not (artifact_dir / "repair_plan.md").exists()


def test_editorial_research_gap_and_batch_agent_mode(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(config, chapter_number=1)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(
        "# Chapter 1\n\nTODO verify: medieval gate tax. Ari enters the city.\n",
        encoding="utf-8",
    )
    review = editorial_review(config, chapter_number=1)
    status = editorial_status(config)
    gaps = detect_knowledge_gaps(config, chapter_number=1, text="needs research: medieval gate tax")
    open_book(config)
    mark_project_ready(root, config)
    batch = batch_write(config, chapters=2, stop_on_gate_failure=True)

    assert review.unresolved_items >= 1
    review_payload = json.loads(Path(review.review_file).read_text(encoding="utf-8"))
    assert review_payload["agent_task_files"]
    assert all((root / path).exists() for path in review_payload["agent_task_files"])
    role_ids = {role["id"] for role in review_payload["editorial_team"]}
    assert role_ids == {"scene_prose_editor", "anti_ai_editor"}
    assert review_payload["severity_counts"]["P0"] == 1
    assert review_payload["review_round"] == 1
    assert "unresolved_P0" in review_payload["need_human_reasons"]
    assert (root / "50_workbench" / "editorial_reviews" / "agent_tasks" / "ch001" / "scene_prose_editor.md").exists()
    assert status.need_human is True
    assert "medieval gate tax" in " ".join(gaps.gaps)
    assert batch.status == "awaiting_agent_draft"
    assert batch.chapters_attempted == 1
    assert not (root / "40_manuscript" / "draft" / "ch002.md").exists()


def test_editorial_batch_review_generates_editorial_team_health_reports(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    draft_dir = root / "40_manuscript" / "draft"
    for chapter in range(1, 11):
        plan_chapter(config, chapter_number=chapter)
        draft_dir.joinpath(f"ch{chapter:03d}.md").write_text(
            (
                f"# Chapter {chapter}\n\n"
                "Ari studies the gate and 不禁 feels the 仿佛 distant pressure. "
                "Ari studies the gate and 不禁 feels the 仿佛 distant pressure. "
                "The scene remains brief but keeps the serial question open?\n"
            ),
            encoding="utf-8",
        )

    batch = editorial_batch_review(config, chapter_start=1, chapter_end=10)
    status = editorial_status(config)
    batch_payload = json.loads(Path(batch.batch_file).read_text(encoding="utf-8"))

    assert batch.reviews == 10
    assert batch.need_human is True
    assert status.conditional_pass_streak == 10
    assert any(reason.startswith("conditional_pass_streak") for reason in status.need_human_reasons)
    assert set(batch.health_report_files) == {"pacing", "logic", "ai_taste"}
    for path in batch.health_report_files.values():
        assert (root / path).exists()
    report_text = "\n".join((root / path).read_text(encoding="utf-8") for path in batch.health_report_files.values())
    assert "Pacing Health Report" in report_text
    assert "Logic Health Report" in report_text
    assert "AI Taste Report" in report_text
    finding_codes = {item["code"] for item in batch_payload["cross_chapter_findings"]}
    assert "repeated_conditional_pass" in finding_codes
    assert "batch_ai_taste_cluster" in finding_codes


def test_baseline_fixture_markers_and_relationship_extraction(tmp_path):
    expected = load_expected_markers()
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "10_bible" / "characters.json").write_text(
        json.dumps(
            [
                {"id": "character:ari", "name": "Ari", "type": "character"},
                {"id": "character:bo", "name": "Bo", "type": "character"},
            ]
        ),
        encoding="utf-8",
    )
    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# Chapter 1\n\nAri and Bo form an alliance beside the black gate. Ari trusts Bo with a clue.\n",
        encoding="utf-8",
    )

    update_graph(config, chapter_number=1)
    suggestions = json.loads((root / "50_workbench" / "graph_updates" / "ch001.json").read_text(encoding="utf-8"))
    kinds = {item["kind"] for item in suggestions["suggestions"]}
    graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))

    assert set(expected["graph_suggestion_kinds"]).issubset(kinds)
    assert any(rel.get("type") == "alliance" for rel in graph["relationships"])

    build_chunks(config)
    build_context(config, chapter_number=2, query_text="Ari Bo alliance", top_k=3)
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")
    for section in expected["rag_context_sections"]:
        assert section in context_text


def test_anchor_anti_resolution_blocks_forbidden_reveal(tmp_path):
    expected = load_expected_markers()
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    (root / "20_outline" / "outline_anchors.json").write_text(
        json.dumps(
            [
                {
                    "chapter_number": 1,
                    "status": "rising",
                    "forbidden_reveals": ["Dragon Crown"],
                    "resolution_markers": ["ultimate secret"],
                    "requires_tail_suspense": True,
                }
            ]
        ),
        encoding="utf-8",
    )
    plan_chapter(config, chapter_number=1)
    draft = "# Chapter 1\n\n" + ("Ari reveals the Dragon Crown and the ultimate secret. " * 30)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(draft, encoding="utf-8")

    result = gate_check(config, chapter_number=1)
    codes = {failure["code"] for failure in result.failures}

    assert set(expected["gate_failure_codes"]).issubset(codes)


def test_api_provider_mode_is_rejected_during_config_validation(tmp_path):
    try:
        seed_project(tmp_path, cli_overrides={"writing": {"mode": "api_provider"}})
    except ConfigError as exc:
        assert "api_provider" in str(exc)
    else:
        raise AssertionError("Expected api_provider to be rejected by config validation")


def test_research_static_provider_writes_inbox_only(tmp_path):
    config = seed_project(tmp_path, cli_overrides={"research": {"search_provider": "static_fallback"}})
    root = tmp_path / "novel"

    result = search_research(config, "medieval gate tax", limit=2)
    item = json.loads(Path(result.item_file).read_text(encoding="utf-8"))

    assert item["provider"] == "static_fallback"
    assert result.status == "inbox"
    assert Path(result.item_file).exists()
    canon = root / "10_bible" / "research_canon.jsonl"
    assert not (canon.read_text(encoding="utf-8").strip() if canon.exists() else "")


def test_full_baseline_e2e_no_failed_pollution_and_rebuild(tmp_path):
    config = seed_project(tmp_path)
    config.data.setdefault("editorial", {})["review_mode"] = "off"
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    continue_write(config, chapter_number=1)
    ch1 = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    ch1.write_text(passing_text("SAFECH1"), encoding="utf-8")
    submit_agent_draft(config, chapter_number=1, file_path=ch1, agent="codex")
    finalize_chapter(config, chapter_number=1, approved_by="human")
    complete_unified_semantic_lifecycle(root, config, 1)

    continue_write(config, chapter_number=2)
    ch2 = root / "50_workbench" / "agent_drafts" / "ch002.codex.md"
    ch2.write_text("# Chapter 2\n\nTODO failing draft should never become canon.\n", encoding="utf-8")
    failed = submit_agent_draft(config, chapter_number=2, file_path=ch2, agent="codex")
    rollback(config, to_chapter=1)
    rebuild_database(config)

    graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    chunks = query_table(config, "chapter_chunks", limit=100)

    assert failed.passed is False
    assert (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (root / "40_manuscript" / "final" / "ch002.md").exists()
    assert not any(event.get("chapter_number") == 2 for event in graph["events"])
    assert not any(row["chapter_number"] == 2 for row in chunks)
    assert all("failing draft" not in str(row) for row in chunks)


def test_release_surface_guards_script():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/release_surface_guards.py"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def seed_project(tmp_path, *, cli_overrides=None):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    return load_project_config(project.project_config, cli_overrides=cli_overrides)


def load_expected_markers():
    path = Path(__file__).parent / "fixtures" / "engine_baseline" / "expected_baseline_markers.json"
    return json.loads(path.read_text(encoding="utf-8"))


def passing_text(marker: str) -> str:
    sentence = f"{marker} Ari keeps the promise, pays a cost, and leaves one unresolved clue at the gate? "
    return "# Chapter\n\n" + sentence * 45 + "\n"
