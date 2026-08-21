import json
from hashlib import sha256

import pytest

from longform_engine.config import load_project_config
from longform_engine.db import query_table
from longform_engine.orchestration import continue_write
from longform_engine.rag import query
from longform_engine.research import add_research, impact_analyze, promote_research, search_research
from longform_engine.storage import init_project
from tests.project_fixtures import (
    complete_unified_semantic_lifecycle,
    mark_project_ready,
    refresh_arc_simulation_fixture,
)


def close_seed_research_chapters(root, config) -> None:
    for chapter_number in range(1, 5):
        refresh_arc_simulation_fixture(root)
        if chapter_number <= 3:
            seed_manual_human_revision_binding(root, chapter_number)
        complete_unified_semantic_lifecycle(root, config, chapter_number)


def seed_manual_human_revision_binding(root, chapter_number: int) -> None:
    final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    final_text = final.read_text(encoding="utf-8")
    after_text = "抵达"
    before_text = "走到"
    after_start = final_text.index(after_text)
    source_text = final_text[:after_start] + before_text + final_text[after_start + len(after_text):]
    before_start = source_text.index(before_text)
    evidence_dir = root / "50_workbench" / "human_author_revisions" / f"ch{chapter_number:03d}"
    source = evidence_dir / "manual-source.md"
    record = evidence_dir / "manual-record.json"
    validation = evidence_dir / "manual-validation.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(source_text, encoding="utf-8")
    record.write_text(
        json.dumps(
            {
                "changes": [
                    {
                        "before": {
                            "start": before_start,
                            "end": before_start + len(before_text),
                            "text": before_text,
                        },
                        "after": {
                            "start": after_start,
                            "end": after_start + len(after_text),
                            "text": after_text,
                        },
                    }
                ]
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    validation.write_text(
        json.dumps(
            {
                "source_file": source.relative_to(root).as_posix(),
                "record_file": record.relative_to(root).as_posix(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    final.with_suffix(".finalization.json").write_text(
        json.dumps(
            {
                "human_author_revision": {
                    "validation_file": validation.relative_to(root).as_posix(),
                    "validation_sha256": sha256(validation.read_bytes()).hexdigest(),
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_research_add_only_writes_inbox_and_does_not_pollute_rag(tmp_path):
    project_config = seed_research_project(tmp_path)
    root = tmp_path / "novel"
    note = tmp_path / "note.md"
    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )
    note.write_text("# 宋代市舶司\n\n市舶司资料只应先进入 inbox，不能直接污染 canon。\n", encoding="utf-8")

    note.write_text(
        "# Research Inbox Note\n\nINBOX_ONLY_MARKER should stay isolated in research_inbox until promotion.\n",
        encoding="utf-8",
    )

    result = add_research(project_config, file_path=note, tags=["history"])

    assert result.status == "inbox"
    assert (root / "50_workbench" / "research_inbox" / f"{result.item_id}.json").exists()
    assert not (root / "10_bible" / "research_canon.jsonl").exists()

    rag_result = query(project_config, "市舶司", top_k=3)
    assert rag_result.hits == ()

    mark_project_ready(root, project_config, preserve_existing_characters=True)
    close_seed_research_chapters(root, project_config)
    refresh_arc_simulation_fixture(root)
    continue_write(project_config, chapter_number=5)
    task = (root / "50_workbench" / "writing_tasks" / "ch005.md").read_text(encoding="utf-8")
    assert "INBOX_ONLY_MARKER" not in task


def test_research_search_writes_web_results_to_inbox(tmp_path):
    project_config = seed_research_project(tmp_path)

    result = search_research(
        project_config,
        "宋代市舶司",
        fetcher=lambda query_text, limit, timeout: [
            {
                "type": "web_search_result",
                "provider": "test",
                "title": "市舶司",
                "url": "https://example.test/shibosi",
                "summary": f"{query_text} 的制度资料摘要。",
                "credibility": "reference",
            }
        ],
    )

    payload = json.loads((tmp_path / "novel" / "50_workbench" / "research_inbox" / f"{result.item_id}.json").read_text(encoding="utf-8"))
    content = (tmp_path / "novel" / "50_workbench" / "research_inbox" / f"{result.item_id}.md").read_text(encoding="utf-8")

    assert payload["source_type"] == "web_search"
    assert payload["status"] == "inbox"
    assert payload["sources"][0]["url"] == "https://example.test/shibosi"
    assert "宋代市舶司" in content


def test_research_impact_promote_syncs_canon_rag_graph_and_sqlite(tmp_path):
    project_config = seed_research_project(tmp_path)
    root = tmp_path / "novel"
    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )
    note = tmp_path / "note.md"
    note.write_text(
        "# 市舶司制度\n\n林迟抵达云门后，可以借鉴宋代市舶司的抽分、勘合和商税制度设计外贸组织。\n",
        encoding="utf-8",
    )
    note.write_text(
        (
            "# Promoted Canon Note\n\n"
            "PROMOTED_CANON_MARKER should enter canon, RAG, context, and writing tasks after promotion. Ari and ch001 are impacted.\n\n"
            "鏋楒繜鎶佃揪浜戦棬鍚庯紝鍙互鍊熼壌瀹嬩唬甯傝埗鍙哥殑鎶藉垎銆佸嫎鍚堝拰鍟嗙◣鍒跺害璁捐澶栬锤缁勭粐銆俓n"
        ),
        encoding="utf-8",
    )
    item = add_research(project_config, file_path=note, source_url="https://example.test/source")

    impact = impact_analyze(project_config, research_item=item.item_id)
    assert "Ari" in impact.impacted_characters
    assert "ch001" in impact.impacted_chapters
    assert (root / "50_workbench" / "impact_reports" / f"{item.item_id}.md").exists()

    promoted = promote_research(project_config, research_item=item.item_id, approved_by="test")
    assert promoted.status == "promoted"
    assert (root / "10_bible" / "research_canon.jsonl").exists()
    assert (root / "20_outline" / "research_impact_ledger.jsonl").exists()
    assert (root / "60_rag" / "chunks" / f"{item.item_id}.json").exists()
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")
    assert promoted.context_file.endswith("next_plot_context.md")
    assert "PROMOTED_CANON_MARKER" in context_text
    transaction = json.loads((root / promoted.transaction_report).read_text(encoding="utf-8"))
    assert transaction["status"] == "applied"
    assert transaction["metadata"]["research_item_id"] == item.item_id

    inbox_payload = json.loads((root / "50_workbench" / "research_inbox" / f"{item.item_id}.json").read_text(encoding="utf-8"))
    assert inbox_payload["status"] == "promoted"
    assert "10_bible/research_canon.jsonl" in inbox_payload["canon_paths"]

    graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    assert any(event["id"] == f"research:{item.item_id}" for event in graph["events"])

    rag_result = query(project_config, "市舶司 抽分", top_k=3)
    rag_result = query(project_config, "PROMOTED_CANON_MARKER", top_k=3)
    assert rag_result.hits
    assert rag_result.hits[0].id.startswith("research:")

    chunks = query_table(project_config, "chapter_chunks", limit=20)
    events = query_table(project_config, "events", limit=20)
    assert any(row["id"].startswith("research:") for row in chunks)
    assert any(row["id"] == f"research:{item.item_id}" for row in events)

    mark_project_ready(root, project_config, preserve_existing_characters=True)
    close_seed_research_chapters(root, project_config)
    refresh_arc_simulation_fixture(root)
    continue_write(project_config, chapter_number=5)
    task_file = root / "50_workbench" / "writing_tasks" / "ch005.json"
    task = json.loads(task_file.read_text(encoding="utf-8"))
    task_markdown = task_file.with_suffix(".md").read_text(encoding="utf-8")
    inventory = (root / task["internal_fact_inventory"]["path"]).read_text(encoding="utf-8")
    assert "PROMOTED_CANON_MARKER" in inventory
    assert "research_canon.jsonl" in inventory
    assert "PROMOTED_CANON_MARKER" not in task_markdown
    assert "research_canon.jsonl" not in task_markdown


def test_research_promote_late_failure_restores_files_cache_and_sqlite(tmp_path, monkeypatch):
    project_config = seed_research_project(tmp_path)
    root = tmp_path / "novel"
    note = tmp_path / "note.md"
    note.write_text("# Rollback note\n\nTRANSACTION_ROLLBACK_MARKER must not survive failure.\n", encoding="utf-8")
    item = add_research(project_config, file_path=note)
    item_path = root / "50_workbench" / "research_inbox" / f"{item.item_id}.json"
    graph_path = root / "30_state" / "story_graph.json"
    context_path = root / "60_rag" / "context" / "next_plot_context.md"
    before_item = item_path.read_bytes()
    before_graph = graph_path.read_bytes()
    before_context = context_path.read_bytes()

    def fail_after_all_writes(*args, **kwargs):
        raise RuntimeError("injected late research promotion failure")

    monkeypatch.setattr("longform_engine.research.pipeline.query_table", fail_after_all_writes)
    with pytest.raises(RuntimeError, match="injected late research promotion failure"):
        promote_research(project_config, research_item=item.item_id, approved_by="test")

    assert item_path.read_bytes() == before_item
    assert graph_path.read_bytes() == before_graph
    assert not (root / "10_bible" / "research_canon.jsonl").exists()
    assert not (root / "20_outline" / "research_impact_ledger.jsonl").exists()
    assert not (root / "60_rag" / "chunks" / f"{item.item_id}.json").exists()
    assert context_path.read_bytes() == before_context
    assert not list((root / "60_rag" / "query_cache").glob("*.json"))
    assert not (root / "70_runtime" / "db" / "longform_engine.sqlite").exists()
    reports = sorted((root / "70_runtime" / "transactions").glob("*research_promote*.json"))
    assert reports
    assert json.loads(reports[0].read_text(encoding="utf-8"))["status"] == "rolled_back"


def seed_research_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root
    (root / "10_bible" / "characters.json").write_text(
        json.dumps(
            [
                {
                    "id": "character:lin_chi",
                    "name": "林迟",
                    "type": "character",
                    "description": "主角。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for chapter_number in range(1, 5):
        (root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md").write_text(
            f"# 第{chapter_number}章 云门\n\n林迟抵达云门，旧钟声打开第{chapter_number}层秘密。\n",
            encoding="utf-8",
        )
        (root / "40_manuscript" / "summaries" / f"ch{chapter_number:03d}.md").write_text(
            f"林迟抵达云门，准备接触第{chapter_number}条外部贸易线索。\n",
            encoding="utf-8",
        )
    return load_project_config(project.project_config)
