import json
from pathlib import Path

from longform_engine.config import load_project_config
from longform_engine.db import query_table, rebuild_database
from longform_engine.graph import check_graph
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.rag import build_chunks, build_context, query
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


ROOT = Path(__file__).resolve().parents[1]


def test_release_guard_covers_agent_collaboration_hardening_contracts():
    guard = (ROOT / "scripts" / "release_surface_guards.py").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "AGENT_COLLABORATION_HARDENING_CHECKLIST.md").read_text(encoding="utf-8")

    for marker in (
        "REQUIRED_RELEASE_CONTRACT_MARKERS",
        "test_strict_manifest_validation_rejects_unknown_type_and_canonical_output",
        "content_expand",
        "AGENT_TASK_STATUSES",
        "canonical_write_transaction_rollback",
        "rollback_restores_touched_paths",
    ):
        assert marker in guard
    for item in (
        "release guard 增加 strict manifest validation 文档/测试入口",
        "release guard 增加 `content_expand` manifest 覆盖检查",
        "release guard 增加 lifecycle states 覆盖检查",
        "release guard 增加 transaction rollback 覆盖检查",
    ):
        assert f"- [x] {item}" in checklist


def test_release_guard_covers_experience_orchestration_contracts():
    guard = (ROOT / "scripts" / "release_surface_guards.py").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "AGENT_EXPERIENCE_ORCHESTRATION.md").read_text(encoding="utf-8")
    production = (ROOT / "src" / "longform_engine" / "production.py").read_text(encoding="utf-8")

    for marker in (
        "check_experience_layer_guards",
        "DIRECT_WRITER_PATTERNS",
        "production_status_cmd",
        "production_loop_cmd",
        "agent_task_brief_cmd",
        "function_body",
    ):
        assert marker in guard
    for item in (
        "release guard 增加体验层命令 guard marker。",
        "release guard 检查 `production loop` 不 import OpenAI/Anthropic。",
        "release guard 检查 `production loop` 不直接写 final/RAG/graph/SQLite。",
        "release guard 检查 `agent-task brief` 是只读渲染。",
        "no-pollution E2E 覆盖 production loop 暂停路径。",
    ):
        assert f"- [x] {item}" in checklist
    for marker in (
        "production_status_v1",
        "experience layer release guard",
        "no LLM in Python CLI",
        "no automatic chapter finalize",
    ):
        assert marker in docs
    for marker in (
        "def production_loop",
        "def agent_task_brief",
        '"read_only": True',
        "normalize_contract_json",
    ):
        assert marker in production


def test_release_guard_covers_benchmark_and_readiness_contracts():
    guard = (ROOT / "scripts" / "release_surface_guards.py").read_text(encoding="utf-8")

    for marker in (
        "check_public_distribution_guards",
        "BENCHMARK_RECORD_SCHEMA",
        "BENCHMARK_COMPARISON_SCHEMA",
        "stores_manuscript_body",
        "forbidden_git_mutation",
        "cmd_release_check",
        "cmd_benchmark_record",
        "cmd_benchmark_compare",
    ):
        assert marker in guard


def test_failed_agent_draft_does_not_pollute_long_term_memory_or_indexes(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    root = project.root

    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )

    open_book(project_config)
    mark_project_ready(root, project_config, preserve_existing_characters=True)
    continue_write(project_config, chapter_number=1)
    graph_path = root / "30_state" / "story_graph.json"
    graph_before = graph_path.read_text(encoding="utf-8")

    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(
        "# Chapter 1: Failed Draft\n\n"
        "TODO DRAFTLEAKPHRASE Ari should not become canon from a failed draft.\n",
        encoding="utf-8",
    )

    result = submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    stale_chunk = root / "60_rag" / "chunks" / "ch001.json"
    stale_chunk.write_text(
        json.dumps(
            {
                "source_path": "40_manuscript/draft/ch001.md",
                "chunks": [
                    {
                        "id": "ch001:draft-leak",
                        "chapter_number": 1,
                        "chunk_index": 0,
                        "text": "DRAFTLEAKPHRASE must not enter final RAG.",
                        "keywords": ["DRAFTLEAKPHRASE"],
                        "metadata": {"source": "40_manuscript/draft/ch001.md"},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    graph_check = check_graph(project_config)
    graph_after_check = graph_path.read_text(encoding="utf-8")
    rag_stats = build_chunks(project_config)
    rag_result = query(project_config, "DRAFTLEAKPHRASE", top_k=3)
    context = build_context(project_config, chapter_number=2, query_text="safe context", top_k=3)
    rebuild = rebuild_database(project_config)

    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    gate_result = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))
    graph_after_rebuild = json.loads(graph_path.read_text(encoding="utf-8"))
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")
    chapters = query_table(project_config, "chapters", limit=20)
    chunks = query_table(project_config, "chapter_chunks", limit=20)
    events = query_table(project_config, "events", limit=20)
    mentions = query_table(project_config, "entity_mentions", limit=20)
    gates = query_table(project_config, "gate_results", limit=20)

    assert result.passed is False
    assert gate_result["passed"] is False
    assert state["status"] == "gate_failed"
    assert state["last_finalized_chapter"] == 0
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()

    assert graph_after_check == graph_before
    assert any("Agent draft timeline risk ch001" in warning for warning in graph_check.warnings)
    assert graph_after_rebuild["events"] == []
    assert all(not entity.get("mentions") for entity in graph_after_rebuild.get("entities", []))

    assert rag_stats.chapters == 0
    assert not stale_chunk.exists()
    assert rag_result.hits == ()
    assert context.hit_count == 0
    assert "DRAFTLEAKPHRASE" not in context_text

    assert rebuild.chapters == 1
    assert rebuild.chapter_chunks == 0
    assert rebuild.events == 0
    assert any(row["chapter_number"] == 1 and row["status"] == "gate_failed" for row in chapters)
    assert not any(row["chapter_number"] == 1 and row["status"] == "final" for row in chapters)
    assert chunks == []
    assert events == []
    assert mentions == []
    assert gates[0]["passed"] == 0
