import json
import sqlite3
from pathlib import Path

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import CANONICAL_DELTA_SCHEMA
from longform_engine.agent_tasks import load_manifest
from longform_engine.config import load_project_config
from longform_engine.db import database_path, init_database, query_table, rebuild_database, status, sync_database
from longform_engine.orchestration import continue_write, finalize_chapter, open_book, submit_agent_draft
from longform_engine.semantic import semantic_apply, semantic_task
from longform_engine.semantic.pipeline import active_planned_thread_ids, foreshadow_state_threads, planned_threads
from longform_engine.storage import init_project
from tests.project_fixtures import approve_story_candidate, mark_project_ready


def test_db_init_creates_schema(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)

    db_path = init_database(project_config)

    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "schema_meta" in tables
        assert "chapters" in tables
        assert "chapter_chunks" in tables
        assert "draft_submissions" in tables
        assert "entities" in tables
        assert "gate_results" in tables
        schema_version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0]
        assert schema_version == "1"


def test_db_sync_is_idempotent_and_queries_files(tmp_path):
    project_config = seed_project(tmp_path)

    first = sync_database(project_config)
    second = sync_database(project_config)
    current = status(project_config)

    assert first == second
    assert current.exists is True
    assert current.schema_version == "1"
    assert current.chapters == 1
    assert current.chapter_chunks == 1
    assert current.entities == 1
    assert current.events == 1
    assert current.gate_results == 1

    chapters = query_table(project_config, "chapters")
    assert chapters[0]["chapter_number"] == 1
    assert chapters[0]["title"] == "第一章 山门之前"

    gates = query_table(project_config, "gate_results")
    assert gates[0]["passed"] == 1


def test_db_rebuild_recovers_after_delete(tmp_path):
    project_config = seed_project(tmp_path)
    sync_database(project_config)
    db_path = database_path(project_config)
    db_path.unlink()

    stats = rebuild_database(project_config)
    current = status(project_config)

    assert stats.chapters == 1
    assert current.exists is True
    assert current.chapters == 1
    assert current.chapter_chunks == 1


def test_db_rebuild_recovers_agent_skill_state(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    root = project.root
    draft_text = passing_agent_draft_text()
    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:lin", "name": "Ari", "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )

    open_book(project_config)
    mark_project_ready(root, project_config, preserve_existing_characters=True)
    continue_write(project_config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(draft_text, encoding="utf-8")
    submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    approve_story_candidate(root, project_config)
    finalize_chapter(project_config, chapter_number=1, approved_by="human")
    final = root / "40_manuscript" / "final" / "ch001.md"
    final_text = final.read_text(encoding="utf-8")
    start = final_text.index("Ari")
    end = min(len(final_text), start + 80)
    evidence_id = f"ch001.md@{start}:{end}"
    task = semantic_task(project_config, chapter_number=1)
    active_threads = sorted(active_planned_thread_ids(planned_threads(root), foreshadow_state_threads(root), 1))
    Path(task.output_file).write_text(
        json.dumps(
            {
                "schema": CANONICAL_DELTA_SCHEMA,
                "delta_type": "chapter_semantic",
                "coverage": {
                    "chapter_digest": "changed",
                    "scenes": "changed",
                    "events": "changed",
                    "relationships": "unchanged",
                    "characters": "unchanged",
                    "foreshadowing": "unchanged",
                    "world": "unchanged",
                    "timeline": "unchanged",
                },
                "evidence": {
                    "/changes/chapter_digest": [evidence_id],
                    "/changes/scenes/0": [evidence_id],
                    "/changes/events/0": [evidence_id],
                },
                "changes": {
                    "chapter_digest": {
                        "summary": "Ari protects a witness and commits to the next investigation step.",
                        "causal_change": "Protecting the witness opens a new investigation route.",
                        "reader_payoff": "The witness survives and the immediate threat is answered.",
                        "cost": "Ari becomes visible to the opposing force.",
                    },
                    "scenes": [
                        {
                            "scene_id": "ch001:scene:1",
                            "participants": ["character:lin"],
                            "location_id": "",
                            "goal": "Protect the witness.",
                            "outcome": "The witness survives.",
                        }
                    ],
                    "events": [
                        {
                            "event_id": "event:ch001:witness",
                            "title": "Ari protects the witness",
                            "participants": ["character:lin"],
                            "locations": [],
                            "consequences": "The investigation continues with Ari exposed.",
                        }
                    ],
                    "relationship_deltas": [],
                    "character_deltas": [],
                    "foreshadow_deltas": [],
                    "world_deltas": [],
                    "timeline_deltas": [],
                    "retrieval": {
                        "tags": ["witness", "investigation"],
                        "entity_ids": ["character:lin"],
                        "focus": ["Ari protects the witness"],
                    },
                    "entity_coverage": {
                        "featured_character_ids": ["character:lin"],
                        "unchanged_character_ids": ["character:lin"],
                        "active_thread_ids": active_threads,
                        "unchanged_thread_ids": active_threads,
                    },
                },
                "uncertainties": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, task.manifest_file),
        result_file=task.output_file,
    )
    assert control.ok, control.normalization.errors
    semantic_apply(project_config, chapter_number=1, file_path=task.output_file)

    db_path = database_path(project_config)
    for path in (db_path, db_path.with_name(f"{db_path.name}-wal"), db_path.with_name(f"{db_path.name}-shm")):
        if path.exists():
            path.unlink()

    stats = rebuild_database(project_config)
    current = status(project_config)
    chapters = query_table(project_config, "chapters", limit=20)
    submissions = query_table(project_config, "draft_submissions", limit=20)
    gates = query_table(project_config, "gate_results", limit=20)
    chunks = query_table(project_config, "chapter_chunks", limit=20)
    entities = query_table(project_config, "entities", limit=20)
    events = query_table(project_config, "events", limit=20)

    assert current.exists is True
    assert stats.chapters == 1
    assert stats.draft_submissions == 1
    assert stats.gate_results == 1
    assert stats.chapter_chunks >= 1
    assert stats.entities >= 1
    assert stats.events >= 1
    assert any(row["chapter_number"] == 1 and row["status"] == "final" for row in chapters)
    assert submissions[0]["agent"] == "codex"
    assert submissions[0]["draft_file"] == "40_manuscript/draft/ch001.md"
    assert gates[0]["passed"] == 1
    assert all(str(row["source_path"]).startswith("40_manuscript/final/") for row in chunks)
    assert any(row["id"] == "character:lin" for row in entities)
    assert any(row["chapter_number"] == 1 for row in events)


def seed_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root

    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# 第一章 山门之前\n\n少年站在山门外，第一次听见旧钟声。\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "summaries" / "ch001.md").write_text(
        "主角抵达山门，旧钟声引出第一个秘密。\n",
        encoding="utf-8",
    )
    (root / "60_rag" / "chunks" / "ch001.json").write_text(
        json.dumps(
            {
                "chunks": [
                    {
                        "id": "ch001:0",
                        "chapter_number": 1,
                        "chunk_index": 0,
                        "text": "少年站在山门外，第一次听见旧钟声。",
                        "keywords": ["山门", "旧钟声"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "60_rag" / "query_cache" / "latest.json").write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "id": "q1",
                        "query": "旧钟声",
                        "hits": ["ch001:0"],
                        "cache_signature": "sig1",
                        "context_word_count": 18,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "30_state" / "story_graph.json").write_text(
        json.dumps(
            {
                "entities": [
                    {
                        "id": "character:lin",
                        "name": "林迟",
                        "type": "character",
                        "description": "山门外的少年。",
                        "mentions": [{"chapter_number": 1, "reason": "首次登场"}],
                    }
                ],
                "events": [
                    {
                        "id": "event:ch001:bell",
                        "chapter_number": 1,
                        "title": "旧钟声响起",
                        "participants": ["character:lin"],
                        "consequences": "主线秘密被轻微打开。",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "20_outline" / "outline_anchors.json").write_text(
        json.dumps(
            [
                {
                    "id": "anchor:bell",
                    "type": "secret",
                    "chapter_number": 1,
                    "description": "旧钟声对应核心秘密的第一层暗示。",
                    "status": "opened",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gate_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "gate_result.json").write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "passed": True,
                "severity": "PASS",
                "failures": [],
                "allowed_actions": ["continue_write"],
                "next_command": "continue-write",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "30_state" / "pacing_history.json").write_text(
        json.dumps(
            [
                {
                    "id": "pacing:1",
                    "chapter_number": 1,
                    "tier": "medium",
                    "event_types": ["hook"],
                    "quota_used": {"A": 0, "B": 0, "C": 0},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = load_project_config(project.project_config)
    config.data["semantic"]["profile"] = "local-hash"
    config.data["semantic"]["allow_fallback"] = True
    config.data["semantic"]["vector_store"]["backend"] = "local_sqlite"
    return config


def passing_agent_draft_text() -> str:
    sentence = (
        "Ari climbs the old stone road toward the north gate, hears the bronze bell answer from the mist, "
        "and chooses to protect the caravan instead of taking the easy path alone. "
    )
    return "# Chapter 1: North Gate\n\n" + sentence * 25 + "\n\nBut another bell answers from beyond the gate.\n"


def passing_draft_text() -> str:
    sentence = "鏋楄繜娌跨潃灞遍棬鐭抽樁鍚戜笂锛屾棫閽熷０鍦ㄩ浘閲屽洖鑽★紝浠栬浣忓笀鐖剁暀涓嬬殑瑙勭煩锛屼篃鐪嬭灞变笅鐏伀涓€姝ユ閫艰繎銆?"
    return "# 绗竴绔?灞遍棬\n\n" + sentence * 80 + "\n"
