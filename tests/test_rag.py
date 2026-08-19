import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.config import load_project_config
from longform_engine.db import query_table, status, sync_semantic_delta
from longform_engine.memory import apply_style_memory_delta, build_style_memory
from longform_engine.orchestration import continue_write, finalize_chapter, open_book, submit_agent_draft
from longform_engine.rag import (
    apply_embedding_delta,
    build_chunks,
    build_context,
    query,
    rebuild_embedding_index,
)
from longform_engine.storage import init_project
from longform_engine.vectorstore import active_source_hash_count, active_source_record_count
from tests.project_fixtures import mark_project_ready


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


def test_embedding_delta_replaces_only_changed_source_and_preserves_full_snapshot(tmp_path):
    project_config = seed_rag_project(tmp_path)
    root = tmp_path / "novel"
    project_config.data["semantic"]["profile"] = "local-hash"
    project_config.data["semantic"]["allow_fallback"] = True
    project_config.data["semantic"]["require_real_model"] = False
    project_config.data["semantic"]["vector_store"]["backend"] = "local_sqlite"
    build_chunks(project_config)
    rebuilt = rebuild_embedding_index(project_config)
    snapshot = root / "60_rag" / "metadata" / "embeddings.jsonl"
    snapshot_hash = sha256(snapshot.read_bytes()).hexdigest()
    chapter_one_count = active_source_record_count(project_config, "40_manuscript/final/ch001.md")
    chapter_one_db_ids = {
        item["id"]
        for item in query_table(project_config, "chapter_chunks", limit=10000)
        if int(item.get("chapter_number") or 0) == 1
    }

    chapter_two = root / "40_manuscript" / "final" / "ch002.md"
    chapter_two.write_text(
        chapter_two.read_text(encoding="utf-8") + "\n\n新的代价证据迫使林迟改变下一步选择。\n",
        encoding="utf-8",
    )
    build_chunks(project_config, chapter_numbers=(2,), sync_index=False)
    db_delta = sync_semantic_delta(project_config, chapter_number=2, refresh_graph=False)
    delta = apply_embedding_delta(project_config, chapter_numbers=(2,))
    chapter_two_hash = sha256(chapter_two.read_bytes()).hexdigest()

    assert rebuilt.records > delta.records > 0
    assert db_delta.chapter_chunks > 0
    assert sha256(snapshot.read_bytes()).hexdigest() == snapshot_hash
    assert {
        item["id"]
        for item in query_table(project_config, "chapter_chunks", limit=10000)
        if int(item.get("chapter_number") or 0) == 1
    } == chapter_one_db_ids
    assert active_source_record_count(project_config, "40_manuscript/final/ch001.md") == chapter_one_count
    chapter_two_count = active_source_record_count(project_config, "40_manuscript/final/ch002.md")
    assert active_source_hash_count(
        project_config,
        "40_manuscript/final/ch002.md",
        chapter_two_hash,
    ) == chapter_two_count


def test_full_rebuild_rejects_noncanonical_final_source(tmp_path):
    project_config = seed_rag_project(tmp_path)
    root = tmp_path / "novel"
    (root / "40_manuscript" / "final" / "ch001.txt").write_text(
        "duplicate canonical source",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Non-canonical manuscript filename"):
        build_chunks(project_config)


def test_style_memory_delta_reads_only_declared_finalized_chapter(tmp_path, monkeypatch):
    import longform_engine.memory.pipeline as memory_pipeline

    project_config = seed_rag_project(tmp_path)
    root = tmp_path / "novel"
    apply_style_memory_delta(project_config, chapter_numbers=(1,))
    real_read = memory_pipeline.safe_read_text
    reads = []

    def tracked_read(path):
        reads.append(path.resolve())
        return real_read(path)

    monkeypatch.setattr(memory_pipeline, "safe_read_text", tracked_read)
    result = apply_style_memory_delta(project_config, chapter_numbers=(2,))
    payload = json.loads((root / result.style_file).read_text(encoding="utf-8"))

    assert (root / "40_manuscript" / "final" / "ch001.md").resolve() not in reads
    assert (root / "40_manuscript" / "final" / "ch002.md").resolve() in reads
    assert payload["source_chapters"] == [1, 2]
    assert payload["aggregation_mode"] == "per_source_incremental_v1"


def test_style_memory_delta_rejects_inconsistent_existing_provenance(tmp_path):
    project_config = seed_rag_project(tmp_path)
    result = apply_style_memory_delta(project_config, chapter_numbers=(1,))
    style_file = Path(result.style_file)
    payload = json.loads(style_file.read_text(encoding="utf-8"))
    payload["source_hash"] = "0" * 64
    style_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="source hash is inconsistent"):
        apply_style_memory_delta(project_config, chapter_numbers=(2,))


def test_style_memory_delta_rejects_sample_path_chapter_mismatch(tmp_path):
    project_config = seed_rag_project(tmp_path)
    root = tmp_path / "novel"
    build_style_memory(project_config)
    style_file = root / "60_rag" / "memory" / "style" / "style_fingerprint.json"
    payload = json.loads(style_file.read_text(encoding="utf-8"))
    chapter_sample = next(item for item in payload["style_samples"] if item["chapter"] > 0)
    chapter_sample["chapter"] += 1
    payload["source_hash"] = sha256(
        json.dumps(
            {item["source_path"]: item["source_sha256"] for item in payload["style_samples"]},
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    style_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="sample provenance is ambiguous"):
        apply_style_memory_delta(project_config, chapter_numbers=(2,))


def test_failed_agent_draft_and_draft_chunk_do_not_enter_rag(tmp_path):
    project_config = seed_agent_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    mark_project_ready(root, project_config)
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
    mark_project_ready(root, project_config)
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
    return "# Chapter One: Mountain Gate\n\n" + sentence * 32 + "\n\nBut a second seal breaks outside the archive.\n"
