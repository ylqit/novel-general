"""SQLite derived index for longform novel projects."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator
import hashlib
import json
import re
import sqlite3
import uuid

from longform_engine.config import ConfigDocument
from longform_engine.storage import resolve_project_root
from longform_engine.storage.layout import (
    existing_manuscript_chapter_path,
    list_finalized_chapter_files,
    manuscript_chapter_path,
    manuscript_chapter_relative_path,
)
from longform_engine.text_metrics import content_character_count


SCHEMA_VERSION = "1"

INDEX_TABLES = (
    "chapter_chunks",
    "draft_submissions",
    "chapters",
    "entities",
    "entity_mentions",
    "events",
    "outline_anchors",
    "gate_results",
    "pacing_history",
    "rag_queries",
    "embeddings",
    "memory_units",
    "scene_memories",
    "chapter_memories",
    "arc_memories",
    "character_memories",
    "style_memories",
    "tcs_snapshots",
    "tcs_transitions",
)

QUERYABLE_TABLES = (*INDEX_TABLES, "schema_meta", "audit_events")


@dataclass(frozen=True)
class DbStatus:
    """Small status payload used by CLI and tests."""

    db_path: str
    exists: bool
    schema_version: str | None
    chapters: int
    chapter_chunks: int
    draft_submissions: int
    entities: int
    events: int
    gate_results: int
    stale: tuple[str, ...]


@dataclass(frozen=True)
class SyncStats:
    """Counts written during a file-to-SQLite sync."""

    chapters: int = 0
    chapter_chunks: int = 0
    draft_submissions: int = 0
    entities: int = 0
    entity_mentions: int = 0
    events: int = 0
    outline_anchors: int = 0
    gate_results: int = 0
    pacing_history: int = 0
    rag_queries: int = 0
    embeddings: int = 0
    memory_units: int = 0
    scene_memories: int = 0
    chapter_memories: int = 0
    arc_memories: int = 0
    character_memories: int = 0
    style_memories: int = 0
    tcs_snapshots: int = 0
    tcs_transitions: int = 0


def database_path(config: ConfigDocument) -> Path:
    """Resolve the canonical runtime SQLite database path."""

    return resolve_project_root(config) / "70_runtime" / "db" / "longform_engine.sqlite"


def resolve_project_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Derived index source escaped the project root: {value}") from exc
    return resolved


def init_database(config: ConfigDocument) -> Path:
    """Create the SQLite file and all derived-index tables."""

    db_path = database_path(config)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        create_schema(conn)
        record_audit(conn, "db.init", {"db_path": str(db_path)})
    return db_path


def sync_database(config: ConfigDocument) -> SyncStats:
    """Rebuild derived rows from the current file facts without deleting the database file."""

    root = resolve_project_root(config)
    db_path = init_database(config)
    with connect(db_path) as conn:
        clear_index_tables(conn)
        memory_stats = sync_memory_mirrors(conn, root)
        stats = SyncStats(
            chapters=sync_chapters(conn, root),
            chapter_chunks=sync_chunks(conn, root),
            draft_submissions=sync_draft_submissions(conn, root),
            entities=sync_graph(conn, root)[0],
            entity_mentions=sync_graph.last_mentions,
            events=sync_graph.last_events,
            outline_anchors=sync_outline_anchors(conn, root),
            gate_results=sync_gate_results(conn, root),
            pacing_history=sync_pacing_history(conn, root),
            rag_queries=sync_rag_queries(conn, root),
            embeddings=sync_embeddings(conn, root),
            memory_units=memory_stats["memory_units"],
            scene_memories=memory_stats["scene_memories"],
            chapter_memories=memory_stats["chapter_memories"],
            arc_memories=memory_stats["arc_memories"],
            character_memories=memory_stats["character_memories"],
            style_memories=memory_stats["style_memories"],
            tcs_snapshots=memory_stats["tcs_snapshots"],
            tcs_transitions=memory_stats["tcs_transitions"],
        )
        record_audit(conn, "db.sync", asdict(stats))
    return stats


def sync_semantic_delta(
    config: ConfigDocument,
    *,
    chapter_number: int,
    memory_paths: Iterable[str | Path] = (),
    refresh_graph: bool = True,
    tcs_path: str | Path | None = None,
) -> SyncStats:
    """Synchronize only the file owners changed by one chapter semantic apply."""

    if chapter_number <= 0:
        raise ValueError("Semantic database delta requires a positive chapter number.")
    root = resolve_project_root(config)
    db_path = database_path(config)
    if not db_path.is_file():
        raise ValueError("Semantic database delta requires an initialized index; run db rebuild first.")
    resolved_memory = [resolve_project_path(root, path) for path in memory_paths]
    resolved_tcs = resolve_project_path(root, tcs_path) if tcs_path is not None else None
    with connect(db_path) as conn:
        create_schema(conn)
        if get_schema_version(conn) != SCHEMA_VERSION:
            raise ValueError("Semantic database delta requires the current schema; run db rebuild first.")
        require_continuous_prior_chapters(conn, chapter_number)
        chapter_count = sync_chapter_number(conn, root, chapter_number)
        chunk_count = sync_chunk_number(conn, root, chapter_number)
        entity_count = 0
        mention_count = 0
        event_count = 0
        if refresh_graph:
            conn.execute("DELETE FROM entity_mentions")
            conn.execute("DELETE FROM entities")
            conn.execute("DELETE FROM events")
            entity_count, mention_count, event_count = sync_graph(conn, root)
        memory_counts = empty_memory_counts()
        stale_payload = read_json(root / "60_rag" / "memory" / "stale.json", default={})
        stale_global = bool(stale_payload.get("stale")) if isinstance(stale_payload, dict) else False
        for path in resolved_memory:
            memory_type = memory_type_for_path(root, path)
            synced_type = sync_memory_file(
                conn,
                root,
                path,
                memory_type=memory_type,
                stale_global=stale_global,
            )
            if synced_type:
                increment_memory_counts(memory_counts, synced_type)
        if resolved_tcs is not None:
            sync_tcs_snapshot_file(conn, root, resolved_tcs, stale=stale_global)
            memory_counts["tcs_snapshots"] = 1
        stats = SyncStats(
            chapters=chapter_count,
            chapter_chunks=chunk_count,
            entities=entity_count,
            entity_mentions=mention_count,
            events=event_count,
            memory_units=memory_counts["memory_units"],
            scene_memories=memory_counts["scene_memories"],
            chapter_memories=memory_counts["chapter_memories"],
            arc_memories=memory_counts["arc_memories"],
            character_memories=memory_counts["character_memories"],
            style_memories=memory_counts["style_memories"],
            tcs_snapshots=memory_counts["tcs_snapshots"],
        )
        record_audit(conn, "db.semantic_delta", {"chapter_number": chapter_number, **asdict(stats)})
    return stats


def rebuild_database(config: ConfigDocument) -> SyncStats:
    """Delete and fully recreate the derived SQLite index from files."""

    db_path = database_path(config)
    delete_database_files(db_path)
    stats = sync_database(config)
    clear_stale_index_markers(config)
    with connect(db_path) as conn:
        record_audit(conn, "db.rebuild", {"db_path": str(db_path), **asdict(stats)})
    return stats


def status(config: ConfigDocument) -> DbStatus:
    """Return current SQLite and project stale status."""

    root = resolve_project_root(config)
    db_path = database_path(config)
    stale = tuple(_load_novel_stale(root))
    if not db_path.exists():
        return DbStatus(
            db_path=str(db_path),
            exists=False,
            schema_version=None,
            chapters=0,
            chapter_chunks=0,
            draft_submissions=0,
            entities=0,
            events=0,
            gate_results=0,
            stale=stale,
        )

    with connect(db_path) as conn:
        return DbStatus(
            db_path=str(db_path),
            exists=True,
            schema_version=get_schema_version(conn),
            chapters=count_rows(conn, "chapters"),
            chapter_chunks=count_rows(conn, "chapter_chunks"),
            draft_submissions=count_rows(conn, "draft_submissions"),
            entities=count_rows(conn, "entities"),
            events=count_rows(conn, "events"),
            gate_results=count_rows(conn, "gate_results"),
            stale=stale,
        )


def query_table(config: ConfigDocument, table: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Query a whitelisted derived-index table."""

    if table not in QUERYABLE_TABLES:
        raise ValueError(f"Unknown or unsafe table '{table}'.")
    db_path = database_path(config)
    if not db_path.exists():
        init_database(config)
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]


def chapter_chunk_integrity_counts(
    config: ConfigDocument,
    *,
    chapter_number: int,
    source_path: str,
    source_sha256: str,
) -> tuple[int, int]:
    """Return total and exact-source chunk counts for one chapter close boundary."""

    db_path = database_path(config)
    if not db_path.is_file():
        return 0, 0
    with connect(db_path) as conn:
        create_schema(conn)
        rows = conn.execute(
            "SELECT source_path, metadata_json FROM chapter_chunks WHERE chapter_number = ?",
            (chapter_number,),
        ).fetchall()
    exact = 0
    for row in rows:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if (
            str(row["source_path"] or "") == source_path
            and isinstance(metadata, dict)
            and str(metadata.get("source_sha256") or "") == source_sha256
        ):
            exact += 1
    return len(rows), exact


def clear_stale_index_markers(config: ConfigDocument) -> None:
    """Mark revise-outline stale blockers handled after a full DB rebuild."""

    root = resolve_project_root(config)
    stale_index_path = root / "30_state" / "stale_indexes.json"
    if stale_index_path.exists():
        payload = read_json(stale_index_path, default={})
        if isinstance(payload, dict):
            payload["unsafe_continuation_blocker"] = False
            payload["handled_by"] = "db rebuild"
            payload["handled_at"] = utc_now()
            stale_index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rag_stale_path = root / "60_rag" / "stale.json"
    if rag_stale_path.exists():
        payload = read_json(rag_stale_path, default={})
        if isinstance(payload, dict):
            payload["stale"] = False
            payload["handled_by"] = "db rebuild"
            payload["handled_at"] = utc_now()
            rag_stale_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a configured SQLite connection."""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_database_files(db_path: Path) -> None:
    """Remove SQLite database and common sidecar files."""

    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if path.exists():
            path.unlink()


def create_schema(conn: sqlite3.Connection) -> None:
    """Create all schema-v1 tables."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chapters (
            chapter_number INTEGER PRIMARY KEY,
            title TEXT,
            path TEXT,
            summary TEXT,
            volume TEXT,
            status TEXT NOT NULL DEFAULT 'unknown',
            word_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chapter_chunks (
            id TEXT PRIMARY KEY,
            chapter_number INTEGER,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            text TEXT NOT NULL,
            keywords_json TEXT NOT NULL DEFAULT '[]',
            source_path TEXT,
            token_estimate INTEGER NOT NULL DEFAULT 0,
            word_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            FOREIGN KEY (chapter_number) REFERENCES chapters(chapter_number) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS draft_submissions (
            chapter_number INTEGER PRIMARY KEY,
            agent TEXT,
            source_file TEXT,
            draft_file TEXT,
            writing_task TEXT,
            source_sha256 TEXT,
            draft_sha256 TEXT,
            word_count INTEGER NOT NULL DEFAULT 0,
            submitted_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL,
            FOREIGN KEY (chapter_number) REFERENCES chapters(chapter_number) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            description TEXT,
            source_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entity_mentions (
            entity_id TEXT NOT NULL,
            chapter_number INTEGER NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            source_path TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (entity_id, chapter_number, reason),
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            chapter_number INTEGER,
            title TEXT NOT NULL,
            participants_json TEXT NOT NULL DEFAULT '[]',
            consequences TEXT,
            opens_threads_json TEXT NOT NULL DEFAULT '[]',
            closes_threads_json TEXT NOT NULL DEFAULT '[]',
            source_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS outline_anchors (
            id TEXT PRIMARY KEY,
            anchor_type TEXT,
            chapter_number INTEGER,
            description TEXT NOT NULL,
            status TEXT,
            source_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS gate_results (
            chapter_number INTEGER PRIMARY KEY,
            passed INTEGER NOT NULL,
            severity TEXT,
            failures_json TEXT NOT NULL DEFAULT '[]',
            allowed_actions_json TEXT NOT NULL DEFAULT '[]',
            next_command TEXT,
            artifact_dir TEXT,
            result_path TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS pacing_history (
            id TEXT PRIMARY KEY,
            chapter_number INTEGER,
            tier TEXT,
            event_types_json TEXT NOT NULL DEFAULT '[]',
            quota_used_json TEXT NOT NULL DEFAULT '{}',
            source_path TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rag_queries (
            id TEXT PRIMARY KEY,
            query TEXT NOT NULL,
            hits_json TEXT NOT NULL DEFAULT '[]',
            cache_signature TEXT,
            context_word_count INTEGER NOT NULL DEFAULT 0,
            source_path TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS embeddings (
            id TEXT PRIMARY KEY,
            owner_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            model TEXT,
            source_path TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory_units (
            id TEXT PRIMARY KEY,
            memory_type TEXT NOT NULL,
            chapter_number INTEGER,
            from_chapter INTEGER,
            to_chapter INTEGER,
            source_path TEXT,
            source_hash TEXT,
            status TEXT NOT NULL DEFAULT 'canonical',
            stale INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scene_memories (
            id TEXT PRIMARY KEY,
            chapter_number INTEGER,
            scene_number INTEGER,
            summary TEXT,
            characters_json TEXT NOT NULL DEFAULT '[]',
            events_json TEXT NOT NULL DEFAULT '[]',
            location TEXT,
            emotion_state TEXT,
            conflict_state TEXT,
            source_path TEXT,
            source_hash TEXT,
            status TEXT NOT NULL DEFAULT 'canonical',
            stale INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chapter_memories (
            id TEXT PRIMARY KEY,
            chapter_number INTEGER,
            summary TEXT,
            characters_json TEXT NOT NULL DEFAULT '[]',
            events_json TEXT NOT NULL DEFAULT '[]',
            locations_json TEXT NOT NULL DEFAULT '[]',
            emotion_state TEXT,
            conflict_state TEXT,
            source_path TEXT,
            source_hash TEXT,
            status TEXT NOT NULL DEFAULT 'canonical',
            stale INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS arc_memories (
            id TEXT PRIMARY KEY,
            memory_type TEXT NOT NULL DEFAULT 'arc',
            from_chapter INTEGER,
            to_chapter INTEGER,
            summary TEXT,
            main_event_chain_json TEXT NOT NULL DEFAULT '[]',
            emotion_curve_json TEXT NOT NULL DEFAULT '[]',
            conflict_progress_json TEXT NOT NULL DEFAULT '[]',
            source_path TEXT,
            source_hash TEXT,
            status TEXT NOT NULL DEFAULT 'canonical',
            stale INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS character_memories (
            id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            name TEXT,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            current_beliefs_json TEXT NOT NULL DEFAULT '[]',
            knowledge_scope_json TEXT NOT NULL DEFAULT '[]',
            relationship_map_json TEXT NOT NULL DEFAULT '[]',
            forbidden_actions_json TEXT NOT NULL DEFAULT '[]',
            source_chapters_json TEXT NOT NULL DEFAULT '[]',
            source_path TEXT,
            source_hash TEXT,
            status TEXT NOT NULL DEFAULT 'canonical',
            stale INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS style_memories (
            id TEXT PRIMARY KEY,
            source_path TEXT,
            source_hash TEXT,
            fingerprint_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'canonical',
            stale INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tcs_snapshots (
            id TEXT PRIMARY KEY,
            chapter_number INTEGER,
            source_path TEXT,
            source_hash TEXT,
            current_characters_json TEXT NOT NULL DEFAULT '[]',
            locations_json TEXT NOT NULL DEFAULT '[]',
            recent_events_json TEXT NOT NULL DEFAULT '[]',
            unresolved_conflicts_json TEXT NOT NULL DEFAULT '[]',
            open_foreshadows_json TEXT NOT NULL DEFAULT '[]',
            active_constraints_json TEXT NOT NULL DEFAULT '[]',
            stale INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tcs_transitions (
            id TEXT PRIMARY KEY,
            chapter_number INTEGER,
            from_chapter INTEGER,
            to_chapter INTEGER,
            source_path TEXT,
            source_hash TEXT,
            known_facts_json TEXT NOT NULL DEFAULT '[]',
            relationship_state_json TEXT NOT NULL DEFAULT '[]',
            state_transitions_json TEXT NOT NULL DEFAULT '[]',
            stale INTEGER NOT NULL DEFAULT 0,
            payload_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        """
    )
    set_schema_meta(conn, "schema_version", SCHEMA_VERSION)


def clear_index_tables(conn: sqlite3.Connection) -> None:
    """Clear derived rows while preserving schema and audit history."""

    for table in INDEX_TABLES:
        conn.execute(f"DELETE FROM {table}")


def sync_chapters(conn: sqlite3.Connection, root: Path) -> int:
    """Sync final manuscript files and chapter metadata."""

    chapters: dict[int, dict[str, Any]] = {}
    for chapter_number, path in list_finalized_chapter_files(root):
        text = safe_read_text(path)
        chapters[chapter_number] = {
            "chapter_number": chapter_number,
            "title": extract_title(text, path),
            "path": relative_path(root, path),
            "summary": read_summary(root, chapter_number),
            "volume": None,
            "status": "final",
            "word_count": content_character_count(text),
        }

    meta_path = root / "40_manuscript" / "chapter_meta.jsonl"
    for item in iter_jsonl(meta_path):
        chapter_number = int(item.get("chapter_number") or item.get("chapter") or item.get("number") or 0)
        if chapter_number <= 0:
            continue
        existing = chapters.get(chapter_number, {"chapter_number": chapter_number})
        existing.update(
            {
                "title": item.get("title", existing.get("title")),
                "path": item.get("path", existing.get("path")),
                "summary": item.get("summary", existing.get("summary")),
                "volume": item.get("volume", existing.get("volume")),
                "status": item.get("status", existing.get("status", "metadata_only")),
                "word_count": int(item.get("word_count", existing.get("word_count", 0)) or 0),
            }
        )
        chapters[chapter_number] = existing

    now = utc_now()
    for chapter in chapters.values():
        conn.execute(
            """
            INSERT INTO chapters (chapter_number, title, path, summary, volume, status, word_count, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chapter_number) DO UPDATE SET
                title=excluded.title,
                path=excluded.path,
                summary=excluded.summary,
                volume=excluded.volume,
                status=excluded.status,
                word_count=excluded.word_count,
                updated_at=excluded.updated_at
            """,
            (
                chapter["chapter_number"],
                chapter.get("title"),
                chapter.get("path"),
                chapter.get("summary"),
                chapter.get("volume"),
                chapter.get("status", "unknown"),
                chapter.get("word_count", 0),
                now,
            ),
        )
    return len(chapters)


def sync_chapter_number(conn: sqlite3.Connection, root: Path, chapter_number: int) -> int:
    """Upsert one finalized chapter without enumerating historical manuscript files."""

    path = existing_manuscript_chapter_path(root, chapter_number, lane="final")
    if path is None:
        raise ValueError(
            f"Semantic database delta requires canonical final source ch{chapter_number:03d}.md."
        )
    text = safe_read_text(path)
    meta = next(
        (
            item
            for item in reversed(list(iter_jsonl(root / "40_manuscript" / "chapter_meta.jsonl")))
            if int(item.get("chapter_number") or item.get("chapter") or 0) == chapter_number
        ),
        {},
    )
    conn.execute(
        """
        INSERT INTO chapters (chapter_number, title, path, summary, volume, status, word_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chapter_number) DO UPDATE SET
            title=excluded.title,
            path=excluded.path,
            summary=excluded.summary,
            volume=excluded.volume,
            status=excluded.status,
            word_count=excluded.word_count,
            updated_at=excluded.updated_at
        """,
        (
            chapter_number,
            meta.get("title") or extract_title(text, path),
            relative_path(root, path),
            meta.get("summary") or read_summary(root, chapter_number),
            meta.get("volume"),
            "final",
            int(meta.get("content_character_count") or meta.get("word_count") or content_character_count(text)),
            utc_now(),
        ),
    )
    return 1


def require_continuous_prior_chapters(conn: sqlite3.Connection, chapter_number: int) -> None:
    rows = conn.execute(
        "SELECT chapter_number FROM chapters WHERE chapter_number < ? AND status = 'final' ORDER BY chapter_number",
        (chapter_number,),
    ).fetchall()
    actual = [int(row["chapter_number"]) for row in rows]
    expected = list(range(1, chapter_number))
    if actual != expected:
        raise ValueError(
            "Semantic database delta requires a continuous prior final index; run db rebuild first."
        )


def sync_draft_submissions(conn: sqlite3.Connection, root: Path) -> int:
    """Sync Agent draft submission ledgers from the draft lane."""

    count = 0
    draft_dir = root / "40_manuscript" / "draft"
    for path in sorted(draft_dir.glob("*.submission.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            continue
        chapter_number = as_optional_int(payload.get("chapter_number") or payload.get("chapter") or parse_chapter_number(path))
        if not chapter_number:
            continue
        ensure_chapter_placeholder(conn, chapter_number)
        conn.execute(
            """
            INSERT INTO draft_submissions
                (chapter_number, agent, source_file, draft_file, writing_task, source_sha256, draft_sha256,
                 word_count, submitted_at, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chapter_number) DO UPDATE SET
                agent=excluded.agent,
                source_file=excluded.source_file,
                draft_file=excluded.draft_file,
                writing_task=excluded.writing_task,
                source_sha256=excluded.source_sha256,
                draft_sha256=excluded.draft_sha256,
                word_count=excluded.word_count,
                submitted_at=excluded.submitted_at,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                chapter_number,
                payload.get("agent"),
                payload.get("source_file"),
                payload.get("draft_file"),
                payload.get("writing_task"),
                payload.get("source_sha256"),
                payload.get("draft_sha256"),
                int(payload.get("word_count") or 0),
                payload.get("submitted_at"),
                dumps(payload),
                utc_now(),
            ),
        )
        count += 1
    return count


def sync_chunks(conn: sqlite3.Connection, root: Path) -> int:
    """Sync RAG chunk files from 60_rag/chunks."""

    chunks_dir = root / "60_rag" / "chunks"
    return sync_chunk_files(
        conn,
        root,
        sorted([*chunks_dir.glob("*.json"), *chunks_dir.glob("*.jsonl")]),
    )


def sync_chunk_number(conn: sqlite3.Connection, root: Path, chapter_number: int) -> int:
    """Replace the SQLite chunk rows owned by one chapter chunk file."""

    chunks_dir = root / "60_rag" / "chunks"
    candidates = (
        chunks_dir / f"ch{chapter_number:03d}.json",
        chunks_dir / f"ch{chapter_number:03d}.jsonl",
    )
    paths = [path for path in candidates if path.is_file()]
    if len(paths) != 1:
        raise ValueError(
            f"Semantic database delta requires exactly one chunk source for chapter {chapter_number}."
        )
    expected_source = manuscript_chapter_relative_path(chapter_number, lane="final")
    final_file = manuscript_chapter_path(root, chapter_number, lane="final")
    if not final_file.is_file():
        raise ValueError(
            f"Semantic database delta requires canonical final source {expected_source}."
        )
    expected_sha256 = file_sha256(final_file)
    records = list(iter_records(paths[0]))
    if not records:
        raise ValueError(f"Semantic database delta chunk source is empty for chapter {chapter_number}.")
    for record in records:
        chunks = record.get("chunks") if isinstance(record.get("chunks"), list) else [record]
        if (
            int(record.get("chapter_number") or 0) != chapter_number
            or str(record.get("source_path") or "").replace("\\", "/") != expected_source
            or str(record.get("source_sha256") or "") != expected_sha256
            or not chunks
        ):
            raise ValueError(
                f"Semantic database delta chunk owner is stale or inconsistent for chapter {chapter_number}."
            )
        for chunk in chunks:
            metadata = chunk.get("metadata") if isinstance(chunk, dict) else None
            if (
                not isinstance(chunk, dict)
                or int(chunk.get("chapter_number") or 0) != chapter_number
                or not isinstance(metadata, dict)
                or str(metadata.get("source") or "").replace("\\", "/") != expected_source
                or str(metadata.get("source_sha256") or "") != expected_sha256
            ):
                raise ValueError(
                    f"Semantic database delta contains a chunk outside chapter {chapter_number} ownership."
                )
    conn.execute("DELETE FROM chapter_chunks WHERE chapter_number = ?", (chapter_number,))
    return sync_chunk_files(conn, root, paths)


def sync_chunk_files(
    conn: sqlite3.Connection,
    root: Path,
    paths: Iterable[Path],
) -> int:
    count = 0
    for path in paths:
        records = list(iter_records(path))
        for index, record in enumerate(records):
            record_source = normalize_chunk_source(root, record.get("source_path"), path)
            if "chunks" in record and isinstance(record["chunks"], list):
                nested = record["chunks"]
            else:
                nested = [record]
            for nested_index, chunk in enumerate(nested):
                text = str(chunk.get("text") or chunk.get("content") or "")
                if not text.strip():
                    continue
                chapter_number = as_optional_int(chunk.get("chapter_number") or chunk.get("chapter"))
                if chapter_number:
                    ensure_chapter_placeholder(conn, chapter_number)
                chunk_index = int(chunk.get("chunk_index", nested_index if len(nested) > 1 else index) or 0)
                chunk_id = str(chunk.get("id") or f"{path.stem}:{chapter_number or 'na'}:{chunk_index}")
                keywords = normalize_list(chunk.get("keywords"))
                metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
                source_path = (
                    normalize_chunk_source(root, chunk.get("source_path"), path)
                    or normalize_chunk_source(root, metadata.get("source"), path)
                    or record_source
                    or infer_final_source(root, chapter_number)
                )
                if not is_allowed_chunk_source(root, source_path, metadata):
                    continue
                conn.execute(
                    """
                    INSERT INTO chapter_chunks
                        (id, chapter_number, chunk_index, text, keywords_json, source_path, token_estimate,
                         word_count, metadata_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        chapter_number=excluded.chapter_number,
                        chunk_index=excluded.chunk_index,
                        text=excluded.text,
                        keywords_json=excluded.keywords_json,
                        source_path=excluded.source_path,
                        token_estimate=excluded.token_estimate,
                        word_count=excluded.word_count,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        chunk_id,
                        chapter_number,
                        chunk_index,
                        text,
                        dumps(keywords),
                        source_path,
                        int(chunk.get("token_estimate") or chunk.get("tokens") or 0),
                        int(chunk.get("word_count") or content_character_count(text)),
                        dumps(metadata),
                        utc_now(),
                    ),
                )
                count += 1
    return count


def sync_graph(conn: sqlite3.Connection, root: Path) -> tuple[int, int, int]:
    """Sync story graph entities, mentions, and events."""

    graph_path = root / "30_state" / "story_graph.json"
    graph = read_json(graph_path, default={})
    entity_count = 0
    mention_count = 0
    event_count = 0

    for entity in normalize_collection(graph.get("entities")):
        normalized = normalize_entity(entity, graph_path, root)
        if not normalized:
            continue
        upsert_entity(conn, normalized)
        entity_count += 1
        for mention in normalize_mentions(entity.get("mentions")):
            if upsert_mention(conn, normalized["id"], mention, relative_path(root, graph_path)):
                mention_count += 1

    for mention in normalize_collection(graph.get("entity_mentions")):
        entity_id = str(mention.get("entity_id") or mention.get("id") or "")
        chapter_number = as_optional_int(mention.get("chapter_number") or mention.get("chapter"))
        if entity_id and chapter_number:
            if upsert_mention(conn, entity_id, {"chapter_number": chapter_number, "reason": mention.get("reason", "")}, relative_path(root, graph_path)):
                mention_count += 1

    for event in normalize_collection(graph.get("events")):
        normalized = normalize_event(event, graph_path, root)
        if not normalized:
            continue
        upsert_event(conn, normalized)
        event_count += 1

    sync_graph.last_mentions = mention_count
    sync_graph.last_events = event_count
    return entity_count, mention_count, event_count


sync_graph.last_mentions = 0
sync_graph.last_events = 0


def normalize_chunk_source(root: Path, value: Any, _chunk_file: Path) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_absolute():
        return relative_path(root, path)
    return raw.replace("\\", "/")


def infer_final_source(root: Path, chapter_number: int | None) -> str | None:
    if not chapter_number:
        return None
    path = existing_manuscript_chapter_path(root, chapter_number, lane="final")
    return relative_path(root, path) if path is not None else None


def is_allowed_chunk_source(root: Path, source_path: str | None, metadata: dict[str, Any]) -> bool:
    if not source_path:
        return False
    normalized = source_path.replace("\\", "/")
    if metadata.get("canon") is True:
        return normalized == "10_bible/research_canon.jsonl"
    if not normalized.startswith("40_manuscript/final/"):
        return False
    return (root / normalized).exists()


def sync_outline_anchors(conn: sqlite3.Connection, root: Path) -> int:
    path = root / "20_outline" / "outline_anchors.json"
    payload = read_json(path, default=[])
    anchors = normalize_collection(payload.get("anchors") if isinstance(payload, dict) else payload)
    count = 0
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, dict):
            continue
        description = str(anchor.get("description") or anchor.get("title") or anchor.get("name") or "")
        if not description:
            continue
        anchor_id = str(anchor.get("id") or f"anchor:{index}")
        conn.execute(
            """
            INSERT INTO outline_anchors
                (id, anchor_type, chapter_number, description, status, source_path, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                anchor_type=excluded.anchor_type,
                chapter_number=excluded.chapter_number,
                description=excluded.description,
                status=excluded.status,
                source_path=excluded.source_path,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                anchor_id,
                anchor.get("type") or anchor.get("anchor_type"),
                as_optional_int(anchor.get("chapter_number") or anchor.get("chapter")),
                description,
                anchor.get("status"),
                relative_path(root, path),
                dumps(anchor),
                utc_now(),
            ),
        )
        count += 1
    return count


def sync_gate_results(conn: sqlite3.Connection, root: Path) -> int:
    count = 0
    artifacts = root / "50_workbench" / "gate_artifacts"
    for path in sorted(artifacts.glob("**/gate_result.json")):
        payload = read_json(path, default={})
        chapter_number = as_optional_int(payload.get("chapter_number") or payload.get("chapter") or parse_chapter_number(path.parent))
        if not chapter_number:
            continue
        failures = payload.get("failures") or payload.get("failure_reasons") or []
        allowed = payload.get("allowed_actions") or []
        passed = bool(payload.get("passed", False))
        conn.execute(
            """
            INSERT INTO gate_results
                (chapter_number, passed, severity, failures_json, allowed_actions_json, next_command,
                 artifact_dir, result_path, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chapter_number) DO UPDATE SET
                passed=excluded.passed,
                severity=excluded.severity,
                failures_json=excluded.failures_json,
                allowed_actions_json=excluded.allowed_actions_json,
                next_command=excluded.next_command,
                artifact_dir=excluded.artifact_dir,
                result_path=excluded.result_path,
                updated_at=excluded.updated_at
            """,
            (
                chapter_number,
                1 if passed else 0,
                payload.get("severity"),
                dumps(normalize_list(failures)),
                dumps(normalize_list(allowed)),
                payload.get("next_command"),
                relative_path(root, path.parent),
                relative_path(root, path),
                utc_now(),
            ),
        )
        count += 1
    return count


def sync_pacing_history(conn: sqlite3.Connection, root: Path) -> int:
    path = root / "30_state" / "pacing_history.json"
    payload = read_json(path, default=[])
    records = normalize_collection(payload.get("history") if isinstance(payload, dict) else payload)
    count = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        chapter_number = as_optional_int(record.get("chapter_number") or record.get("chapter"))
        record_id = str(record.get("id") or f"pacing:{chapter_number or 'na'}:{index}")
        conn.execute(
            """
            INSERT INTO pacing_history
                (id, chapter_number, tier, event_types_json, quota_used_json, source_path, metadata_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                chapter_number=excluded.chapter_number,
                tier=excluded.tier,
                event_types_json=excluded.event_types_json,
                quota_used_json=excluded.quota_used_json,
                source_path=excluded.source_path,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                record_id,
                chapter_number,
                record.get("tier"),
                dumps(normalize_list(record.get("event_types") or record.get("events"))),
                dumps(record.get("quota_used") if isinstance(record.get("quota_used"), dict) else {}),
                relative_path(root, path),
                dumps(record),
                utc_now(),
            ),
        )
        count += 1
    return count


def sync_rag_queries(conn: sqlite3.Connection, root: Path) -> int:
    count = 0
    cache_dir = root / "60_rag" / "query_cache"
    for path in sorted([*cache_dir.glob("*.json"), *cache_dir.glob("*.jsonl")]):
        for index, record in enumerate(iter_records(path)):
            query = str(record.get("query") or record.get("q") or "")
            if not query:
                continue
            query_id = str(record.get("id") or record.get("cache_signature") or f"{path.stem}:{index}")
            hits = record.get("hits") or record.get("results") or []
            conn.execute(
                """
                INSERT INTO rag_queries
                    (id, query, hits_json, cache_signature, context_word_count, source_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    query=excluded.query,
                    hits_json=excluded.hits_json,
                    cache_signature=excluded.cache_signature,
                    context_word_count=excluded.context_word_count,
                    source_path=excluded.source_path,
                    updated_at=excluded.updated_at
                """,
                (
                    query_id,
                    query,
                    dumps(hits),
                    record.get("cache_signature"),
                    int(record.get("context_word_count") or 0),
                    relative_path(root, path),
                    utc_now(),
                ),
            )
            count += 1
    return count


def empty_memory_counts() -> dict[str, int]:
    return {
        "memory_units": 0,
        "scene_memories": 0,
        "chapter_memories": 0,
        "arc_memories": 0,
        "character_memories": 0,
        "style_memories": 0,
        "tcs_snapshots": 0,
        "tcs_transitions": 0,
    }


def increment_memory_counts(counts: dict[str, int], memory_type: str) -> None:
    counts["memory_units"] += 1
    if memory_type == "scene":
        counts["scene_memories"] += 1
    elif memory_type == "chapter":
        counts["chapter_memories"] += 1
    elif memory_type in {"arc", "volume"}:
        counts["arc_memories"] += 1
    elif memory_type == "character":
        counts["character_memories"] += 1
    elif memory_type == "style":
        counts["style_memories"] += 1


def memory_type_for_path(root: Path, path: Path) -> str:
    memory_root = (root / "60_rag" / "memory").resolve()
    try:
        relative = path.resolve().relative_to(memory_root)
    except ValueError as exc:
        raise ValueError(f"Semantic memory delta escaped 60_rag/memory: {path}") from exc
    if len(relative.parts) != 2 or path.suffix.lower() != ".json":
        raise ValueError(f"Semantic memory delta path is not a direct JSON memory owner: {path}")
    mapping = {
        "scenes": "scene",
        "chapters": "chapter",
        "arcs": "arc",
        "characters": "character",
        "style": "style",
    }
    memory_type = mapping.get(relative.parts[0])
    if memory_type is None:
        raise ValueError(f"Semantic memory delta uses an unsupported owner directory: {path}")
    return memory_type


def sync_memory_file(
    conn: sqlite3.Connection,
    root: Path,
    path: Path,
    *,
    memory_type: str,
    stale_global: bool,
) -> str:
    if not path.is_file():
        raise ValueError(f"Semantic memory delta source is missing: {relative_path(root, path)}")
    payload = read_json(path, default=None)
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"Semantic memory source is empty or invalid: {relative_path(root, path)}")
    declared_type = str(payload.get("memory_type") or memory_type)
    normalized_type = {
        "character_current_view": "character",
    }.get(declared_type, declared_type)
    if normalized_type != memory_type and not (memory_type == "arc" and normalized_type == "volume"):
        raise ValueError(
            "Semantic memory type does not match its owner directory: "
            f"declared={declared_type}, owner={memory_type}, path={relative_path(root, path)}"
        )
    chapter = as_optional_int(payload.get("chapter") or payload.get("chapter_number")) or parse_chapter_number(path)
    from_chapter = as_optional_int(payload.get("from_chapter")) or chapter
    to_chapter = as_optional_int(payload.get("to_chapter")) or chapter
    stale = stale_global or str(payload.get("status") or "canonical").lower() == "stale"
    unit_id = f"{normalized_type}:{path.stem}"
    source_hash = file_sha256(path)
    conn.execute(
        """
        INSERT INTO memory_units
            (id, memory_type, chapter_number, from_chapter, to_chapter, source_path, source_hash, status, stale, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            memory_type=excluded.memory_type,
            chapter_number=excluded.chapter_number,
            from_chapter=excluded.from_chapter,
            to_chapter=excluded.to_chapter,
            source_path=excluded.source_path,
            source_hash=excluded.source_hash,
            status=excluded.status,
            stale=excluded.stale,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (
            unit_id,
            normalized_type,
            chapter,
            from_chapter,
            to_chapter,
            relative_path(root, path),
            source_hash,
            str(payload.get("status") or "canonical"),
            1 if stale else 0,
            dumps(payload),
            payload.get("updated_at") or utc_now(),
        ),
    )
    if normalized_type == "scene":
        sync_scene_memory(conn, root, path, payload, unit_id, source_hash, stale)
    elif normalized_type == "chapter":
        sync_chapter_memory(conn, root, path, payload, unit_id, source_hash, stale)
    elif normalized_type in {"arc", "volume"}:
        sync_arc_memory(conn, root, path, payload, unit_id, source_hash, stale)
    elif normalized_type == "character":
        sync_character_memory(conn, root, path, payload, unit_id, source_hash, stale)
    elif normalized_type == "style":
        sync_style_memory(conn, root, path, payload, unit_id, source_hash, stale)
    else:
        raise ValueError(f"Unsupported semantic memory type: {normalized_type}")
    return normalized_type


def sync_tcs_snapshot_file(
    conn: sqlite3.Connection,
    root: Path,
    path: Path,
    *,
    stale: bool,
) -> None:
    expected_root = (root / "30_state" / "tcs").resolve()
    try:
        relative = path.resolve().relative_to(expected_root)
    except ValueError as exc:
        raise ValueError(f"Semantic TCS delta escaped 30_state/tcs: {path}") from exc
    if len(relative.parts) != 1 or path.suffix.lower() != ".json":
        raise ValueError(f"Semantic TCS delta path is not a direct JSON snapshot: {path}")
    payload = read_json(path, default=None)
    if not isinstance(payload, dict) or not payload:
        raise ValueError(f"Semantic TCS delta is missing or invalid: {relative_path(root, path)}")
    snapshot_id = f"tcs:{path.stem}"
    chapter = as_optional_int(payload.get("chapter_number")) or parse_chapter_number(path)
    conn.execute(
        """
        INSERT INTO tcs_snapshots
            (id, chapter_number, source_path, source_hash, current_characters_json, locations_json, recent_events_json,
             unresolved_conflicts_json, open_foreshadows_json, active_constraints_json, stale, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            chapter_number=excluded.chapter_number,
            source_path=excluded.source_path,
            source_hash=excluded.source_hash,
            current_characters_json=excluded.current_characters_json,
            locations_json=excluded.locations_json,
            recent_events_json=excluded.recent_events_json,
            unresolved_conflicts_json=excluded.unresolved_conflicts_json,
            open_foreshadows_json=excluded.open_foreshadows_json,
            active_constraints_json=excluded.active_constraints_json,
            stale=excluded.stale,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (
            snapshot_id,
            chapter,
            relative_path(root, path),
            file_sha256(path),
            dumps(normalize_list(payload.get("current_characters"))),
            dumps(normalize_list(payload.get("locations"))),
            dumps(normalize_list(payload.get("recent_events"))),
            dumps(normalize_list(payload.get("unresolved_conflicts"))),
            dumps(normalize_list(payload.get("open_foreshadows"))),
            dumps(normalize_list(payload.get("active_constraints"))),
            1 if stale else 0,
            dumps(payload),
            payload.get("updated_at") or utc_now(),
        ),
    )


def sync_memory_mirrors(conn: sqlite3.Connection, root: Path) -> dict[str, int]:
    """Sync canonical Memory v2 files and TCS snapshots into SQLite mirrors."""

    counts = empty_memory_counts()
    stale_payload = read_json(root / "60_rag" / "memory" / "stale.json", default={})
    stale_global = bool(stale_payload.get("stale")) if isinstance(stale_payload, dict) else False
    for memory_type, directory in (
        ("scene", root / "60_rag" / "memory" / "scenes"),
        ("chapter", root / "60_rag" / "memory" / "chapters"),
        ("arc", root / "60_rag" / "memory" / "arcs"),
        ("character", root / "60_rag" / "memory" / "characters"),
        ("style", root / "60_rag" / "memory" / "style"),
    ):
        for path in sorted(directory.glob("*.json")):
            normalized_type = sync_memory_file(
                conn,
                root,
                path,
                memory_type=memory_type,
                stale_global=stale_global,
            )
            if normalized_type:
                increment_memory_counts(counts, normalized_type)

    tcs_global_stale = stale_global
    for path in sorted((root / "30_state" / "tcs").glob("ch*.json")):
        sync_tcs_snapshot_file(conn, root, path, stale=tcs_global_stale)
        counts["tcs_snapshots"] += 1
    for path in sorted((root / "30_state" / "tcs" / "transitions").glob("ch*.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            continue
        transition_id = f"tcs_transition:{path.stem}"
        conn.execute(
            """
            INSERT INTO tcs_transitions
                (id, chapter_number, from_chapter, to_chapter, source_path, source_hash, known_facts_json,
                 relationship_state_json, state_transitions_json, stale, payload_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                chapter_number=excluded.chapter_number,
                from_chapter=excluded.from_chapter,
                to_chapter=excluded.to_chapter,
                source_path=excluded.source_path,
                source_hash=excluded.source_hash,
                known_facts_json=excluded.known_facts_json,
                relationship_state_json=excluded.relationship_state_json,
                state_transitions_json=excluded.state_transitions_json,
                stale=excluded.stale,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                transition_id,
                as_optional_int(payload.get("chapter_number")) or parse_chapter_number(path),
                as_optional_int(payload.get("from_chapter")),
                as_optional_int(payload.get("to_chapter")),
                relative_path(root, path),
                file_sha256(path),
                dumps(normalize_collection(payload.get("known_facts_added"))),
                dumps(normalize_collection(payload.get("relationship_state_added"))),
                dumps(normalize_collection(payload.get("state_transitions"))),
                1 if tcs_global_stale else 0,
                dumps(payload),
                payload.get("updated_at") or utc_now(),
            ),
        )
        counts["tcs_transitions"] += 1
    return counts


def sync_scene_memory(conn: sqlite3.Connection, root: Path, path: Path, payload: dict[str, Any], unit_id: str, source_hash: str, stale: bool) -> None:
    conn.execute(
        """
        INSERT INTO scene_memories
            (id, chapter_number, scene_number, summary, characters_json, events_json, location, emotion_state, conflict_state,
             source_path, source_hash, status, stale, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            chapter_number=excluded.chapter_number,
            scene_number=excluded.scene_number,
            summary=excluded.summary,
            characters_json=excluded.characters_json,
            events_json=excluded.events_json,
            location=excluded.location,
            emotion_state=excluded.emotion_state,
            conflict_state=excluded.conflict_state,
            source_path=excluded.source_path,
            source_hash=excluded.source_hash,
            status=excluded.status,
            stale=excluded.stale,
            updated_at=excluded.updated_at
        """,
        (
            unit_id,
            as_optional_int(payload.get("chapter")) or parse_chapter_number(path),
            as_optional_int(payload.get("scene")),
            payload.get("summary"),
            dumps(normalize_list(payload.get("characters"))),
            dumps(normalize_list(payload.get("events"))),
            payload.get("location"),
            payload.get("emotion_state"),
            payload.get("conflict_state"),
            relative_path(root, path),
            source_hash,
            str(payload.get("status") or "canonical"),
            1 if stale else 0,
            payload.get("updated_at") or utc_now(),
        ),
    )


def sync_chapter_memory(conn: sqlite3.Connection, root: Path, path: Path, payload: dict[str, Any], unit_id: str, source_hash: str, stale: bool) -> None:
    conn.execute(
        """
        INSERT INTO chapter_memories
            (id, chapter_number, summary, characters_json, events_json, locations_json, emotion_state, conflict_state,
             source_path, source_hash, status, stale, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            chapter_number=excluded.chapter_number,
            summary=excluded.summary,
            characters_json=excluded.characters_json,
            events_json=excluded.events_json,
            locations_json=excluded.locations_json,
            emotion_state=excluded.emotion_state,
            conflict_state=excluded.conflict_state,
            source_path=excluded.source_path,
            source_hash=excluded.source_hash,
            status=excluded.status,
            stale=excluded.stale,
            updated_at=excluded.updated_at
        """,
        (
            unit_id,
            as_optional_int(payload.get("chapter")) or parse_chapter_number(path),
            payload.get("summary"),
            dumps(normalize_list(payload.get("characters"))),
            dumps(normalize_list(payload.get("events"))),
            dumps(normalize_list(payload.get("locations"))),
            payload.get("emotion_state"),
            payload.get("conflict_state"),
            relative_path(root, path),
            source_hash,
            str(payload.get("status") or "canonical"),
            1 if stale else 0,
            payload.get("updated_at") or utc_now(),
        ),
    )


def sync_arc_memory(conn: sqlite3.Connection, root: Path, path: Path, payload: dict[str, Any], unit_id: str, source_hash: str, stale: bool) -> None:
    conn.execute(
        """
        INSERT INTO arc_memories
            (id, memory_type, from_chapter, to_chapter, summary, main_event_chain_json, emotion_curve_json,
             conflict_progress_json, source_path, source_hash, status, stale, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            memory_type=excluded.memory_type,
            from_chapter=excluded.from_chapter,
            to_chapter=excluded.to_chapter,
            summary=excluded.summary,
            main_event_chain_json=excluded.main_event_chain_json,
            emotion_curve_json=excluded.emotion_curve_json,
            conflict_progress_json=excluded.conflict_progress_json,
            source_path=excluded.source_path,
            source_hash=excluded.source_hash,
            status=excluded.status,
            stale=excluded.stale,
            updated_at=excluded.updated_at
        """,
        (
            unit_id,
            str(payload.get("memory_type") or "arc"),
            as_optional_int(payload.get("from_chapter")) or as_optional_int(payload.get("chapter")) or parse_chapter_number(path),
            as_optional_int(payload.get("to_chapter")) or as_optional_int(payload.get("chapter")) or parse_chapter_number(path),
            payload.get("summary"),
            dumps(normalize_list(payload.get("main_event_chain") or payload.get("events"))),
            dumps(normalize_list(payload.get("emotion_curve"))),
            dumps(normalize_list(payload.get("conflict_progress"))),
            relative_path(root, path),
            source_hash,
            str(payload.get("status") or "canonical"),
            1 if stale else 0,
            payload.get("updated_at") or utc_now(),
        ),
    )


def sync_character_memory(conn: sqlite3.Connection, root: Path, path: Path, payload: dict[str, Any], unit_id: str, source_hash: str, stale: bool) -> None:
    conn.execute(
        """
        INSERT INTO character_memories
            (id, character_id, name, aliases_json, current_beliefs_json, knowledge_scope_json, relationship_map_json,
             forbidden_actions_json, source_chapters_json, source_path, source_hash, status, stale, payload_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            character_id=excluded.character_id,
            name=excluded.name,
            aliases_json=excluded.aliases_json,
            current_beliefs_json=excluded.current_beliefs_json,
            knowledge_scope_json=excluded.knowledge_scope_json,
            relationship_map_json=excluded.relationship_map_json,
            forbidden_actions_json=excluded.forbidden_actions_json,
            source_chapters_json=excluded.source_chapters_json,
            source_path=excluded.source_path,
            source_hash=excluded.source_hash,
            status=excluded.status,
            stale=excluded.stale,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (
            unit_id,
            str(payload.get("character_id") or unit_id),
            payload.get("name"),
            dumps(normalize_list(payload.get("aliases"))),
            dumps(normalize_list(payload.get("current_beliefs"))),
            dumps(normalize_list(payload.get("knowledge_scope"))),
            dumps(normalize_collection(payload.get("relationship_map"))),
            dumps(normalize_list(payload.get("forbidden_actions"))),
            dumps(normalize_list(payload.get("source_chapters"))),
            relative_path(root, path),
            source_hash,
            str(payload.get("status") or "canonical"),
            1 if stale else 0,
            dumps(payload),
            payload.get("updated_at") or utc_now(),
        ),
    )


def sync_style_memory(conn: sqlite3.Connection, root: Path, path: Path, payload: dict[str, Any], unit_id: str, source_hash: str, stale: bool) -> None:
    conn.execute(
        """
        INSERT INTO style_memories
            (id, source_path, source_hash, fingerprint_json, status, stale, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source_path=excluded.source_path,
            source_hash=excluded.source_hash,
            fingerprint_json=excluded.fingerprint_json,
            status=excluded.status,
            stale=excluded.stale,
            updated_at=excluded.updated_at
        """,
        (
            unit_id,
            relative_path(root, path),
            source_hash,
            dumps(payload.get("fingerprint") if isinstance(payload.get("fingerprint"), dict) else {}),
            str(payload.get("status") or "canonical"),
            1 if stale else 0,
            payload.get("updated_at") or utc_now(),
        ),
    )


def sync_embeddings(conn: sqlite3.Connection, root: Path) -> int:
    """Sync semantic embedding rows from file facts."""

    count = 0
    metadata_dir = root / "60_rag" / "metadata"
    for path in sorted([metadata_dir / "embeddings.jsonl", *metadata_dir.glob("embeddings_*.jsonl")]):
        if not path.exists():
            continue
        for index, record in enumerate(iter_records(path)):
            owner_id = str(record.get("owner_id") or record.get("id") or "")
            owner_type = str(record.get("owner_type") or "")
            vector = record.get("vector") or record.get("embedding")
            source_path = normalize_chunk_source(root, record.get("source_path"), path)
            if not owner_id or not owner_type or not isinstance(vector, list):
                continue
            if not is_allowed_embedding_source(root, source_path):
                continue
            row_id = str(record.get("id") or f"{owner_type}:{owner_id}")
            conn.execute(
                """
                INSERT INTO embeddings (id, owner_type, owner_id, vector_json, model, source_path, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    owner_type=excluded.owner_type,
                    owner_id=excluded.owner_id,
                    vector_json=excluded.vector_json,
                    model=excluded.model,
                    source_path=excluded.source_path,
                    updated_at=excluded.updated_at
                """,
                (
                    row_id,
                    owner_type,
                    owner_id,
                    dumps(vector),
                    record.get("model"),
                    source_path,
                    record.get("updated_at") or utc_now(),
                ),
            )
            count += 1
    return count


def is_allowed_embedding_source(root: Path, source_path: str | None) -> bool:
    if not source_path:
        return False
    normalized = source_path.replace("\\", "/")
    if any(part in normalized for part in ("50_workbench/agent_drafts", "40_manuscript/draft", "50_workbench/research_inbox")):
        return False
    if normalized == "10_bible/research_canon.jsonl":
        return True
    if normalized.startswith("40_manuscript/final/"):
        return (root / normalized).exists()
    if normalized.startswith("60_rag/memory/"):
        memory = read_json(root / normalized, default={})
        source = str(memory.get("source_path") or "").replace("\\", "/") if isinstance(memory, dict) else ""
        return source.startswith("40_manuscript/final/") and (root / source).exists()
    return False


def set_schema_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO schema_meta (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (key, value, utc_now()),
    )


def get_schema_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    return str(row["value"]) if row else None


def record_audit(conn: sqlite3.Connection, event_type: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO audit_events (id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), event_type, dumps(payload), utc_now()),
    )


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def upsert_entity(conn: sqlite3.Connection, entity: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO entities (id, name, type, aliases_json, description, source_path, metadata_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            type=excluded.type,
            aliases_json=excluded.aliases_json,
            description=excluded.description,
            source_path=excluded.source_path,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            entity["id"],
            entity["name"],
            entity["type"],
            dumps(entity.get("aliases", [])),
            entity.get("description"),
            entity.get("source_path"),
            dumps(entity.get("metadata", {})),
            utc_now(),
        ),
    )


def upsert_mention(conn: sqlite3.Connection, entity_id: str, mention: dict[str, Any], source_path: str) -> bool:
    chapter_number = as_optional_int(mention.get("chapter_number") or mention.get("chapter"))
    if not chapter_number:
        return False
    if not row_exists(conn, "entities", "id", entity_id):
        return False
    conn.execute(
        """
        INSERT INTO entity_mentions (entity_id, chapter_number, reason, source_path, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(entity_id, chapter_number, reason) DO UPDATE SET
            source_path=excluded.source_path,
            updated_at=excluded.updated_at
        """,
        (entity_id, chapter_number, str(mention.get("reason") or ""), source_path, utc_now()),
    )
    return True


def ensure_chapter_placeholder(conn: sqlite3.Connection, chapter_number: int) -> None:
    """Create a metadata-only chapter row when a derived artifact references it first."""

    conn.execute(
        """
        INSERT INTO chapters (chapter_number, title, path, summary, volume, status, word_count, updated_at)
        VALUES (?, ?, NULL, NULL, NULL, 'metadata_only', 0, ?)
        ON CONFLICT(chapter_number) DO NOTHING
        """,
        (chapter_number, f"Chapter {chapter_number}", utc_now()),
    )


def row_exists(conn: sqlite3.Connection, table: str, column: str, value: Any) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (value,)).fetchone()
    return row is not None


def upsert_event(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO events
            (id, chapter_number, title, participants_json, consequences, opens_threads_json,
             closes_threads_json, source_path, metadata_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            chapter_number=excluded.chapter_number,
            title=excluded.title,
            participants_json=excluded.participants_json,
            consequences=excluded.consequences,
            opens_threads_json=excluded.opens_threads_json,
            closes_threads_json=excluded.closes_threads_json,
            source_path=excluded.source_path,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            event["id"],
            event.get("chapter_number"),
            event["title"],
            dumps(event.get("participants", [])),
            event.get("consequences"),
            dumps(event.get("opens_threads", [])),
            dumps(event.get("closes_threads", [])),
            event.get("source_path"),
            dumps(event.get("metadata", {})),
            utc_now(),
        ),
    )


def normalize_entity(entity: dict[str, Any], source: Path, root: Path) -> dict[str, Any] | None:
    if not isinstance(entity, dict):
        return None
    name = str(entity.get("name") or entity.get("title") or "").strip()
    entity_type = str(entity.get("type") or entity.get("entity_type") or "unknown").strip()
    if not name:
        return None
    entity_id = str(entity.get("id") or f"{entity_type}:{slugify(name)}")
    return {
        "id": entity_id,
        "name": name,
        "type": entity_type,
        "aliases": normalize_list(entity.get("aliases")),
        "description": entity.get("description") or entity.get("summary"),
        "source_path": relative_path(root, source),
        "metadata": entity,
    }


def normalize_event(event: dict[str, Any], source: Path, root: Path) -> dict[str, Any] | None:
    if not isinstance(event, dict):
        return None
    title = str(event.get("title") or event.get("name") or event.get("description") or "").strip()
    if not title:
        return None
    chapter_number = as_optional_int(event.get("chapter_number") or event.get("chapter"))
    event_id = str(event.get("id") or f"event:{chapter_number or 'na'}:{slugify(title)}")
    return {
        "id": event_id,
        "chapter_number": chapter_number,
        "title": title,
        "participants": normalize_list(event.get("participants")),
        "consequences": event.get("consequences") or event.get("result"),
        "opens_threads": normalize_list(event.get("opens_threads")),
        "closes_threads": normalize_list(event.get("closes_threads")),
        "source_path": relative_path(root, source),
        "metadata": event,
    }


def normalize_mentions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, int):
        return [{"chapter_number": value}]
    if isinstance(value, list):
        mentions: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, int):
                mentions.append({"chapter_number": item})
            elif isinstance(item, dict):
                mentions.append(item)
        return mentions
    return []


def normalize_collection(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def iter_records(path: Path) -> Iterable[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        yield from iter_jsonl(path)
        return
    payload = read_json(path, default=[])
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
    elif isinstance(payload, dict):
        if "queries" in payload and isinstance(payload["queries"], list):
            for item in payload["queries"]:
                if isinstance(item, dict):
                    yield item
        elif "chunks" in payload and isinstance(payload["chunks"], list):
            yield payload
        else:
            yield payload


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").lstrip("\ufeff").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            yield item


def _load_novel_stale(root: Path) -> list[str]:
    state = read_json(root / "30_state" / "novel_state.json", default={})
    stale = state.get("stale") if isinstance(state, dict) else []
    return [str(item) for item in normalize_list(stale)]


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def read_summary(root: Path, chapter_number: int) -> str | None:
    path = manuscript_chapter_path(root, chapter_number, lane="summaries")
    return safe_read_text(path).strip() if path.is_file() else None


def extract_title(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem


def parse_chapter_number(path: Path) -> int | None:
    numeric = re.search(r"(\d{1,5})", path.stem)
    return int(numeric.group(1)) if numeric else None


def as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    return [value]


def slugify(value: str) -> str:
    slug = re.sub(r"\s+", "_", value.strip())
    slug = re.sub(r"[^\w\u4e00-\u9fff.-]+", "", slug)
    return slug or "unnamed"


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
