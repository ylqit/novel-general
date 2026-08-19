import json

import pytest

from longform_engine.config import load_project_config
from longform_engine.db import query_table, status as db_status
from longform_engine.revision import create_revision_branch, project_status, rollback, rollback_impact
from longform_engine.revision import pipeline as revision_pipeline
from longform_engine.storage import init_project
from longform_engine.vectorstore import VectorRecord, active_source_record_count, upsert


def test_revision_branch_creates_candidate_without_overwriting_final(tmp_path):
    project_config = seed_revision_project(tmp_path)
    root = tmp_path / "novel"
    original = (root / "40_manuscript" / "final" / "ch002.md").read_text(encoding="utf-8")

    result = create_revision_branch(project_config, chapter_number=2)

    assert result.status == "rewrite_candidate"
    assert (root / "40_manuscript" / "rewrite" / "ch002_rewrite_candidate.md").exists()
    assert (root / "40_manuscript" / "final" / "ch002.md").read_text(encoding="utf-8") == original
    assert (root / "50_workbench" / "revision_reports" / "branch_ch002.json").exists()

    status = project_status(project_config)
    chapter = next(item for item in status.chapters if item.chapter_number == 2)
    assert "rewrite_candidate" in chapter.statuses


def test_revision_rollback_detaches_future_files_marks_stale_and_reports(tmp_path):
    project_config = seed_revision_project(tmp_path)
    root = tmp_path / "novel"
    create_revision_branch(project_config, chapter_number=2)

    result = rollback(project_config, to_chapter=1)

    assert result.to_chapter == 1
    assert result.detached_files
    transaction = json.loads((root / result.transaction_report).read_text(encoding="utf-8"))
    assert transaction["status"] == "applied"
    assert transaction["metadata"]["affected_chapters"] == [2, 3, 4]
    assert (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (root / "40_manuscript" / "final" / "ch002.md").exists()
    assert not (root / "40_manuscript" / "draft" / "ch004.md").exists()
    assert any("ch002.md" in item for item in result.detached_files)
    assert any("ch004.md" in item for item in result.detached_files)

    card = json.loads((root / "20_outline" / "chapter_cards" / "ch002.json").read_text(encoding="utf-8"))
    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    rag_stale = json.loads((root / "60_rag" / "stale.json").read_text(encoding="utf-8"))
    graph_stale = json.loads((root / "30_state" / "story_graph_stale.json").read_text(encoding="utf-8"))
    task_stale = json.loads((root / "50_workbench" / "writing_tasks" / "stale.json").read_text(encoding="utf-8"))
    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch004.json").read_text(encoding="utf-8"))

    assert card["status"] == "stale"
    assert task["status"] == "stale"
    assert state["current_chapter"] == 1
    assert state["last_finalized_chapter"] == 1
    assert "rag_chunks" in state["stale"]
    assert "event_matrix" in state["stale"]
    assert "writing_tasks_after_rollback" in state["stale"]
    assert rag_stale["to_chapter"] == 1
    assert graph_stale["to_chapter"] == 1
    assert "50_workbench/writing_tasks/ch004.json" in task_stale["stale_paths"]["writing_tasks"]
    assert "60_rag/chunks/ch002.json" in rag_stale["stale_paths"]["rag_chunks"]

    impact = rollback_impact(project_config)
    assert "ch002" in "\n".join(impact.affected_summaries)
    assert impact.to_chapter == 1
    assert (root / "50_workbench" / "impact_reports" / "rollback_to_ch001.md").exists()

    status = project_status(project_config)
    by_number = {item.chapter_number: item for item in status.chapters}
    assert by_number[2].status == "detached"
    assert by_number[2].stale is True
    assert by_number[4].status == "detached"

    rows = query_table(project_config, "chapters", limit=20)
    row_by_number = {row["chapter_number"]: row for row in rows}
    assert row_by_number[2]["status"] == "detached"
    current_db_status = db_status(project_config)
    assert "story_graph" in current_db_status.stale
    assert "writing_tasks_after_rollback" in current_db_status.stale


def test_revision_rollback_late_failure_restores_files_vector_and_sqlite(tmp_path, monkeypatch):
    project_config = seed_revision_project(tmp_path)
    root = tmp_path / "novel"
    create_revision_branch(project_config, chapter_number=2)
    upsert(
        project_config,
        [
            VectorRecord(
                id="rollback:ch002",
                owner_type="chapter_memory",
                owner_id="ch002",
                vector=(1.0, 0.0),
                source_path="40_manuscript/final/ch002.md",
                chapter_number=2,
                metadata={"content_hash": "rollback:ch002", "model": "test-vector"},
            )
        ],
    )
    tracked_files = {
        path: path.read_bytes()
        for path in (
            root / "40_manuscript" / "final" / "ch002.md",
            root / "40_manuscript" / "draft" / "ch004.md",
            root / "20_outline" / "chapter_cards" / "ch002.json",
            root / "30_state" / "novel_state.json",
        )
    }
    database_rows = query_table(project_config, "chapters", limit=20)
    assert active_source_record_count(project_config, "40_manuscript/final/ch002.md") == 1
    detached_before = tuple((root / "40_manuscript" / "detached").iterdir())
    real_sync = revision_pipeline.sync_database

    def fail_after_database_sync(config):
        real_sync(config)
        raise RuntimeError("injected late revision rollback failure")

    monkeypatch.setattr(revision_pipeline, "sync_database", fail_after_database_sync)
    with pytest.raises(RuntimeError, match="injected late revision rollback failure"):
        rollback(project_config, to_chapter=1)

    for path, content in tracked_files.items():
        assert path.read_bytes() == content
    assert query_table(project_config, "chapters", limit=20) == database_rows
    assert active_source_record_count(project_config, "40_manuscript/final/ch002.md") == 1
    assert tuple((root / "40_manuscript" / "detached").iterdir()) == detached_before
    assert not (root / "60_rag" / "memory" / "stale.json").exists()
    assert not (root / "50_workbench" / "impact_reports" / "rollback_to_ch001.json").exists()
    reports = sorted((root / "70_runtime" / "transactions").glob("*revision_rollback*.json"))
    assert reports
    assert json.loads(reports[0].read_text(encoding="utf-8"))["status"] == "rolled_back"


def seed_revision_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root

    state = {
        "current_chapter": 4,
        "last_finalized_chapter": 3,
        "status": "draft_ready",
        "stale": [],
    }
    (root / "30_state" / "novel_state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    for number in (1, 2, 3):
        (root / "40_manuscript" / "final" / f"ch{number:03d}.md").write_text(
            f"# 第{number}章\n\n林迟在第{number}章推进云门主线。\n",
            encoding="utf-8",
        )
        (root / "40_manuscript" / "summaries" / f"ch{number:03d}.md").write_text(
            f"ch{number:03d} 摘要：林迟推进云门主线。\n",
            encoding="utf-8",
        )
        (root / "20_outline" / "chapter_cards" / f"ch{number:03d}.json").write_text(
            json.dumps({"chapter_number": number, "status": "planned", "title": f"第{number}章"}, ensure_ascii=False),
            encoding="utf-8",
        )

    (root / "40_manuscript" / "draft" / "ch004.md").write_text(
        "# 第4章\n\n林迟开始草拟新的商路冲突。\n",
        encoding="utf-8",
    )
    (root / "20_outline" / "chapter_cards" / "ch004.json").write_text(
        json.dumps({"chapter_number": 4, "status": "planned", "title": "第4章"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "50_workbench" / "writing_tasks" / "ch004.json").write_text(
        json.dumps({"chapter_number": 4, "status": "task_ready", "title": "Chapter 4"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "60_rag" / "chunks" / "ch002.json").write_text(
        json.dumps(
            {
                "chapter_number": 2,
                "source_path": "40_manuscript/final/ch002.md",
                "chunks": [
                    {
                        "id": "ch002:0",
                        "chapter_number": 2,
                        "chunk_index": 0,
                        "text": "ch002 stale chunk",
                        "keywords": ["stale"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "30_state" / "event_matrix.json").write_text(
        json.dumps([{"chapter_number": 3, "event": "云门主线推进"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "30_state" / "story_graph.json").write_text(
        json.dumps(
            {
                "entities": [{"id": "character:lin_chi", "name": "林迟", "type": "character"}],
                "relationships": [],
                "events": [{"id": "event:ch003", "chapter_number": 3, "title": "第三章事件"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return load_project_config(project.project_config)
