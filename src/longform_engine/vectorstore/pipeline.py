"""Pluggable vector store layer.

The default backend is local SQLite so projects remain offline-first. Remote
backends are represented by a stable contract and healthcheck; actual network
drivers can be added behind this interface without changing RAG code.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable
from contextlib import contextmanager
import json
import os
import sqlite3

from longform_engine.config import ConfigDocument
from longform_engine.models import cosine_similarity
from longform_engine.storage import resolve_project_root


SUPPORTED_BACKENDS = ("local_sqlite", "milvus", "pgvector", "elasticsearch")


@dataclass(frozen=True)
class VectorRecord:
    id: str
    owner_type: str
    owner_id: str
    vector: tuple[float, ...]
    source_path: str
    chapter_number: int | None = None
    scene_number: int | None = None
    status: str = "canonical"
    stale: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class VectorQuery:
    vector: tuple[float, ...]
    top_k: int = 12
    owner_types: tuple[str, ...] = ()
    max_chapter: int | None = None


@dataclass(frozen=True)
class VectorHit:
    id: str
    owner_type: str
    owner_id: str
    score: float
    source_path: str
    chapter_number: int | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class VectorHealth:
    backend: str
    ok: bool
    url: str
    collection: str
    message: str


@dataclass(frozen=True)
class VectorWriteResult:
    backend: str
    records: int
    store_path: str


@dataclass(frozen=True)
class VectorRebuildResult:
    backend: str
    records: int
    source_file: str
    store_path: str


def healthcheck(config: ConfigDocument) -> VectorHealth:
    cfg = vector_config(config)
    backend = cfg["backend"]
    if backend not in SUPPORTED_BACKENDS:
        return VectorHealth(backend=backend, ok=False, url=cfg["url"], collection=cfg["collection"], message="unsupported backend")
    if backend == "local_sqlite":
        path = local_store_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(path) as conn:
            create_schema(conn)
        return VectorHealth(backend=backend, ok=True, url=str(path), collection=cfg["collection"], message="local vector store ready")
    if not cfg["url"]:
        return VectorHealth(backend=backend, ok=False, url="", collection=cfg["collection"], message="remote vector store url is missing")
    if cfg["api_key_env"] and not os.environ.get(cfg["api_key_env"]):
        return VectorHealth(backend=backend, ok=False, url=cfg["url"], collection=cfg["collection"], message=f"missing API key env {cfg['api_key_env']}")
    return VectorHealth(backend=backend, ok=True, url=cfg["url"], collection=cfg["collection"], message="remote vector store contract configured")


def upsert(config: ConfigDocument, records: Iterable[VectorRecord]) -> VectorWriteResult:
    cfg = vector_config(config)
    backend = cfg["backend"]
    materialized = list(records)
    if backend != "local_sqlite":
        return VectorWriteResult(backend=backend, records=len(materialized), store_path=cfg["url"])
    path = local_store_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        create_schema(conn)
        for record in materialized:
            conn.execute(
                """
                INSERT INTO vectors
                    (id, owner_type, owner_id, vector_json, source_path, chapter_number, scene_number, status, stale, metadata_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(id) DO UPDATE SET
                    owner_type=excluded.owner_type,
                    owner_id=excluded.owner_id,
                    vector_json=excluded.vector_json,
                    source_path=excluded.source_path,
                    chapter_number=excluded.chapter_number,
                    scene_number=excluded.scene_number,
                    status=excluded.status,
                    stale=excluded.stale,
                    metadata_json=excluded.metadata_json,
                    updated_at=excluded.updated_at
                """,
                (
                    record.id,
                    record.owner_type,
                    record.owner_id,
                    json.dumps(list(record.vector)),
                    record.source_path,
                    record.chapter_number,
                    record.scene_number,
                    record.status,
                    1 if record.stale else 0,
                    json.dumps(record.metadata or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
    return VectorWriteResult(backend=backend, records=len(materialized), store_path=str(path))


def query(config: ConfigDocument, request: VectorQuery) -> list[VectorHit]:
    cfg = vector_config(config)
    if cfg["backend"] != "local_sqlite":
        return []
    path = local_store_path(config)
    if not path.exists():
        return []
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT id, owner_type, owner_id, vector_json, source_path, chapter_number, metadata_json
            FROM vectors
            WHERE stale = 0 AND status != 'stale'
            """
        ).fetchall()
    hits: list[VectorHit] = []
    for row in rows:
        owner_type = str(row["owner_type"])
        if request.owner_types and owner_type not in request.owner_types:
            continue
        chapter = row["chapter_number"]
        if request.max_chapter is not None and chapter is not None and int(chapter) > request.max_chapter:
            continue
        vector = parse_vector(row["vector_json"])
        score = cosine_similarity(list(request.vector), vector)
        if score <= 0:
            continue
        hits.append(
            VectorHit(
                id=str(row["id"]),
                owner_type=owner_type,
                owner_id=str(row["owner_id"]),
                score=round(score, 6),
                source_path=str(row["source_path"]),
                chapter_number=int(chapter) if chapter is not None else None,
                metadata=loads_json(row["metadata_json"], default={}),
            )
        )
    hits.sort(key=lambda item: (-item.score, -(item.chapter_number or 0), item.id))
    return hits[: request.top_k]


def delete_by_filter(config: ConfigDocument, *, from_chapter: int | None = None, owner_type: str | None = None) -> int:
    cfg = vector_config(config)
    if cfg["backend"] != "local_sqlite":
        return 0
    path = local_store_path(config)
    if not path.exists():
        return 0
    clauses = []
    params: list[Any] = []
    if from_chapter is not None:
        clauses.append("chapter_number >= ?")
        params.append(from_chapter)
    if owner_type:
        clauses.append("owner_type = ?")
        params.append(owner_type)
    where = " AND ".join(clauses) if clauses else "1 = 1"
    with connect(path) as conn:
        cur = conn.execute(f"UPDATE vectors SET stale = 1 WHERE {where}", params)
        return int(cur.rowcount or 0)


def rebuild_from_files(config: ConfigDocument) -> VectorRebuildResult:
    root = resolve_project_root(config)
    source = root / "60_rag" / "metadata" / "embeddings.jsonl"
    records = [record_from_embedding(item) for item in iter_jsonl(source)]
    records = [item for item in records if item is not None]
    cfg = vector_config(config)
    store_path = cfg["url"]
    if cfg["backend"] == "local_sqlite":
        path = local_store_path(config)
        if path.exists():
            path.unlink()
        store_path = str(path)
    result = upsert(config, records)
    return VectorRebuildResult(backend=result.backend, records=result.records, source_file=str(source), store_path=store_path)


def record_from_embedding(payload: dict[str, Any]) -> VectorRecord | None:
    vector = payload.get("vector") or payload.get("embedding")
    if not isinstance(vector, list):
        return None
    source_path = str(payload.get("source_path") or "")
    if any(part in source_path.replace("\\", "/") for part in ("agent_drafts", "research_inbox", "40_manuscript/draft", "repair_candidates")):
        return None
    return VectorRecord(
        id=str(payload.get("id") or f"{payload.get('owner_type')}:{payload.get('owner_id')}"),
        owner_type=str(payload.get("owner_type") or ""),
        owner_id=str(payload.get("owner_id") or payload.get("id") or ""),
        vector=tuple(float(item) for item in vector),
        source_path=source_path,
        chapter_number=as_optional_int(payload.get("chapter_number")),
        scene_number=as_optional_int(payload.get("scene_number")),
        status=str(payload.get("status") or "canonical"),
        stale=bool(payload.get("stale")),
        metadata=payload,
    )


def vector_config(config: ConfigDocument) -> dict[str, str]:
    semantic = config.data.get("semantic", {}) if isinstance(config.data.get("semantic"), dict) else {}
    raw = semantic.get("vector_store") if isinstance(semantic.get("vector_store"), dict) else {}
    return {
        "backend": str(raw.get("backend") or "local_sqlite"),
        "url": str(raw.get("url") or ""),
        "collection": str(raw.get("collection") or "longform_vectors"),
        "api_key_env": str(raw.get("api_key_env") or "LONGFORM_VECTOR_API_KEY"),
        "metric": str(raw.get("metric") or "cosine"),
        "dim": str(raw.get("dim") or "1024"),
    }


def local_store_path(config: ConfigDocument) -> Path:
    root = resolve_project_root(config)
    configured = vector_config(config)["url"]
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else root / path
    return root / "70_runtime" / "db" / "vector_store.sqlite"


@contextmanager
def connect(path: Path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vectors (
            id TEXT PRIMARY KEY,
            owner_type TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            vector_json TEXT NOT NULL,
            source_path TEXT,
            chapter_number INTEGER,
            scene_number INTEGER,
            status TEXT NOT NULL DEFAULT 'canonical',
            stale INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
        """
    )


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def parse_vector(value: str) -> list[float]:
    data = loads_json(value, default=[])
    if not isinstance(data, list):
        return []
    return [float(item) for item in data]


def loads_json(value: Any, *, default: Any) -> Any:
    if not isinstance(value, str):
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def as_optional_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def asdict_records(records: list[VectorHit]) -> list[dict[str, Any]]:
    return [asdict(record) for record in records]
