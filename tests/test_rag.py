import json

from longform_engine.config import load_project_config
from longform_engine.db import query_table, status
from longform_engine.orchestration import continue_write, finalize_chapter, open_book, submit_agent_draft
from longform_engine.rag import build_chunks, build_context, query
from longform_engine.storage import init_project


def test_rag_build_query_and_context(tmp_path):
    project_config = seed_rag_project(tmp_path)
    stats = build_chunks(project_config, max_chars=80, overlap_chars=10)

    assert stats.chapters == 2
    assert stats.chunks >= 2

    result = query(project_config, "旧钟声 山门", top_k=3)
    assert result.hits
    assert result.hits[0].chapter_number == 1
    assert "旧钟声" in result.hits[0].text

    context = build_context(project_config, chapter_number=3, query_text="旧钟声 山门", top_k=2)
    context_text = (tmp_path / "novel" / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")

    assert context.hit_count >= 1
    assert context.context_file.endswith("next_plot_context.md")
    assert "Retrieval Hits" in context_text
    assert "旧钟声" in context_text
    assert "Recent Chapters" in context_text

    db_status = status(project_config)
    assert db_status.chapter_chunks >= 2


def test_rag_query_cache_is_reused_as_file_fact(tmp_path):
    project_config = seed_rag_project(tmp_path)
    build_chunks(project_config, max_chars=120, overlap_chars=0)

    first = query(project_config, "青铜铃", top_k=2)
    second = query(project_config, "青铜铃", top_k=2)

    assert first.cache_file == second.cache_file
    cache_path = tmp_path / "novel" / "60_rag" / "query_cache" / first.cache_file.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    assert cache_path.exists()
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload["query"] == "青铜铃"
    assert payload["hits"]


def test_failed_agent_draft_and_draft_chunk_do_not_enter_rag(tmp_path):
    project_config = seed_agent_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text("# Chapter One\n\nTODO DRAFTLEAKPHRASE cannot be final.\n", encoding="utf-8")

    submitted = submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    stale_chunk = root / "60_rag" / "chunks" / "ch001.json"
    stale_chunk.write_text(
        json.dumps(
            {
                "source_path": "40_manuscript/draft/ch001.md",
                "chunks": [
                    {
                        "id": "ch001:bad",
                        "chapter_number": 1,
                        "chunk_index": 0,
                        "text": "DRAFTLEAKPHRASE should not be indexed.",
                        "keywords": ["DRAFTLEAKPHRASE"],
                        "metadata": {"source": "40_manuscript/draft/ch001.md"},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    stats = build_chunks(project_config)
    result = query(project_config, "DRAFTLEAKPHRASE", top_k=3)
    context = build_context(project_config, chapter_number=2, query_text="safe query", top_k=3)
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")
    chunks = query_table(project_config, "chapter_chunks", limit=20)

    assert submitted.passed is False
    assert stats.chapters == 0
    assert not stale_chunk.exists()
    assert result.hits == ()
    assert "DRAFTLEAKPHRASE" not in context_text
    assert context.hit_count == 0
    assert chunks == []


def test_finalized_chapter_enters_rag_and_context_excludes_inbox(tmp_path):
    project_config = seed_agent_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_agent_text("FINALONLYPHRASE"), encoding="utf-8")
    inbox_note = root / "50_workbench" / "research_inbox" / "manual.md"
    inbox_note.write_text("# Inbox\n\nINBOXONLYPHRASE is not canon.\n", encoding="utf-8")

    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    finalize_chapter(project_config, chapter_number=1, approved_by="human")
    build_chunks(project_config)
    result = query(project_config, "FINALONLYPHRASE", top_k=3)
    context = build_context(project_config, chapter_number=2, query_text="FINALONLYPHRASE", top_k=3)
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")
    chunks = query_table(project_config, "chapter_chunks", limit=20)

    assert result.hits
    assert result.hits[0].chapter_number == 1
    assert result.hits[0].source_path == "40_manuscript/final/ch001.md"
    assert context.hit_count >= 1
    assert "FINALONLYPHRASE" in context_text
    assert "INBOXONLYPHRASE" not in context_text
    assert any(row["chapter_number"] == 1 and row["source_path"] == "40_manuscript/final/ch001.md" for row in chunks)


def seed_rag_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root

    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# 第一章 山门之前\n\n少年林迟站在山门外，听见旧钟声从云雾深处传来。那声音像是在唤醒某个被封存的秘密。\n\n他握紧青铜铃，知道自己不能回头。\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "final" / "ch002.md").write_text(
        "# 第二章 云阶试炼\n\n云阶上的试炼开始后，林迟发现青铜铃会在危机前轻轻震动。这个能力有代价，每次使用都会让他失去一段短暂记忆。\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "summaries" / "ch001.md").write_text(
        "林迟抵达山门，旧钟声和青铜铃共同指向核心秘密。\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "summaries" / "ch002.md").write_text(
        "林迟通过云阶试炼，发现青铜铃预警能力和失忆代价。\n",
        encoding="utf-8",
    )
    return load_project_config(project.project_config)


def seed_agent_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def passing_agent_text(marker: str) -> str:
    sentence = f"{marker} Lin chooses the harder road, keeps the promise, and moves the chapter conflict forward. "
    return "# Chapter One: Mountain Gate\n\n" + sentence * 32 + "\n"
