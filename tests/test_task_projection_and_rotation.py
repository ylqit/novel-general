import gzip
import json
from pathlib import Path

from longform_engine.agent_tasks import (
    compact_task_projection,
    record_task_event,
)
from longform_engine.artifacts import verify_event_segments


def write_index(root: Path, tasks: list[dict]) -> Path:
    path = root / "50_workbench" / "agent_tasks" / "agent_task_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "tasks": tasks}) + "\n", encoding="utf-8")
    return path


def test_two_million_character_projection_keeps_only_project_and_recent_two_chapters(tmp_path):
    root = tmp_path / "novel"
    tasks = [
        {
            "task_id": "book_design:project:v1",
            "task_type": "book_design",
            "chapter_number": 0,
            "status": "applied",
            "manifest_file": "50_workbench/intelligence_tasks/book_design.agent_task.json",
        }
    ]
    for chapter in range(1, 668):
        for lane in ("chapter_write", "semantic_review", "chapter_semantic"):
            tasks.append(
                {
                    "task_id": f"{lane}:ch{chapter:03d}:v1",
                    "task_type": lane,
                    "chapter_number": chapter,
                    "status": "applied",
                    "manifest_file": f"50_workbench/tasks/ch{chapter:03d}.{lane}.json",
                }
            )
    index = write_index(root, tasks)
    events = root / "50_workbench" / "agent_tasks" / "events.jsonl"
    events.write_text(
        "".join(
            json.dumps(
                {
                    "schema_version": 1,
                    "task_id": f"chapter_write:ch{chapter:03d}:v1",
                    "from_status": "validated",
                    "to_status": "applied",
                }
            )
            + "\n"
            for chapter in range(1, 668)
        ),
        encoding="utf-8",
    )

    result = compact_task_projection(
        root,
        through=665,
        archive_refs={chapter: f"70_runtime/artifacts/chapters/ch{chapter:03d}.zip" for chapter in range(1, 666)},
    )
    payload = json.loads(index.read_text(encoding="utf-8"))

    assert payload["schema"] == "agent_task_index_v2"
    assert result["archived_tasks"] == 665 * 3
    assert {int(item["chapter_number"]) for item in payload["tasks"]} == {0, 666, 667}
    assert payload["terminal_counts"]["total"] == 665 * 3
    assert index.stat().st_size < 1_000_000
    assert events.stat().st_size < 5 * 1024 * 1024


def test_project_event_rotation_writes_gzip_hash_manifest(monkeypatch, tmp_path):
    root = tmp_path / "novel"
    write_index(
        root,
        [
            {
                "task_id": "book_design:project:v1",
                "task_type": "book_design",
                "chapter_number": 0,
                "status": "applied",
                "manifest_file": "book.json",
            }
        ],
    )
    monkeypatch.setattr("longform_engine.agent_tasks.EVENT_ROTATE_LINES", 3)
    monkeypatch.setattr("longform_engine.agent_tasks.EVENT_ROTATE_BYTES", 10**9)

    for _ in range(3):
        record_task_event(
            root,
            task_id="book_design:project:v1",
            from_status="validated",
            to_status="applied",
            command="test",
        )

    manifest = json.loads(
        (root / "70_runtime" / "artifacts" / "events" / "segments.json").read_text(encoding="utf-8")
    )
    segment = root / manifest["segments"][0]["path"]
    with gzip.open(segment, "rt", encoding="utf-8") as handle:
        assert len(handle.read().splitlines()) == 3
    assert verify_event_segments(root) == []
