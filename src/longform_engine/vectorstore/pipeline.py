"""Local SQLite and HNSW vector-store implementations."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable
from contextlib import contextmanager
import importlib.util
import json
import sqlite3
import uuid

from longform_engine.config import ConfigDocument
from longform_engine.models import cosine_similarity
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.vector_backends import IMPLEMENTED_VECTOR_BACKENDS


SUPPORTED_BACKENDS = IMPLEMENTED_VECTOR_BACKENDS


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
    record_count: int = 0
    stale_count: int = 0
    index_path: str = ""
    dirty: bool = False
    dependency_available: bool = True
    recommendation: str = ""


@dataclass(frozen=True)
class VectorWriteResult:
    backend: str
    records: int
    store_path: str
    index_path: str = ""


@dataclass(frozen=True)
class VectorSyncResult:
    backend: str
    received: int
    upserted: int
    unchanged: int
    stale: int
    store_path: str
    index_path: str = ""


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
    if backend in SUPPORTED_BACKENDS:
        path = local_store_path(config)
        index_path = local_index_path(config) if backend == "local_hnsw" else None
        path.parent.mkdir(parents=True, exist_ok=True)
        with connect(path) as conn:
            create_schema(conn)
            record_count, stale_count = vector_counts(conn)
            dirty = state_value(conn, "hnsw_dirty") == "1"
        threshold = int(cfg["hnsw_threshold"])
        recommendation = ""
        if backend == "local_sqlite" and record_count >= threshold:
            recommendation = (
                f"{record_count} active vectors reached the {threshold} linear-scan threshold; "
                "set semantic.vector_store.backend=local_hnsw and run vector-store rebuild."
            )
        if backend == "local_sqlite":
            return VectorHealth(
                backend=backend,
                ok=True,
                url=str(path),
                collection=cfg["collection"],
                message="local SQLite linear vector store ready",
                record_count=record_count,
                stale_count=stale_count,
                recommendation=recommendation,
            )

        dependency_available = hnsw_dependency_available()
        if not dependency_available:
            return VectorHealth(
                backend=backend,
                ok=False,
                url=str(path),
                collection=cfg["collection"],
                message="hnswlib is not installed; install the semantic extra",
                record_count=record_count,
                stale_count=stale_count,
                index_path=str(index_path),
                dirty=dirty,
                dependency_available=False,
                recommendation='python -m pip install "longform-novel-engine[semantic]"',
            )
        manifest = read_hnsw_manifest(index_path)
        index_missing = record_count > 0 and not index_path.is_file()
        manifest_mismatch = bool(
            record_count > 0
            and (
                not manifest
                or int(manifest.get("active_records") or -1) != record_count
                or int(manifest.get("dimension") or 0) <= 0
                or str(manifest.get("metric") or "") != str(cfg["metric"])
            )
        )
        ok = not dirty and not index_missing and not manifest_mismatch
        message = "local HNSW vector store ready"
        if dirty:
            message = "HNSW index is dirty after an interrupted mutation; rebuild required"
        elif index_missing:
            message = "HNSW index file is missing; rebuild required"
        elif manifest_mismatch:
            message = "HNSW manifest does not match SQLite metadata; rebuild required"
        return VectorHealth(
            backend=backend,
            ok=ok,
            url=str(path),
            collection=cfg["collection"],
            message=message,
            record_count=record_count,
            stale_count=stale_count,
            index_path=str(index_path),
            dirty=dirty,
            dependency_available=True,
            recommendation="" if ok else "longform-engine vector-store rebuild project.yaml",
        )
    return VectorHealth(
        backend=backend,
        ok=False,
        url=cfg["url"],
        collection=cfg["collection"],
        message="unsupported backend",
    )


def active_source_record_count(config: ConfigDocument, source_path: str) -> int:
    """Count active vectors bound to one canonical project-relative source."""

    path = local_store_path(config)
    if not path.is_file():
        return 0
    with connect(path) as conn:
        create_schema(conn)
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM vectors
            WHERE source_path = ? AND stale = 0 AND status != 'stale'
            """,
            (str(source_path),),
        ).fetchone()
    return int(row["count"] or 0)


def active_source_hash_count(config: ConfigDocument, source_path: str, source_sha256: str) -> int:
    """Count active vectors whose metadata is bound to an exact canonical source hash."""

    path = local_store_path(config)
    if not path.is_file():
        return 0
    with connect(path) as conn:
        create_schema(conn)
        rows = conn.execute(
            """
            SELECT metadata_json
            FROM vectors
            WHERE source_path = ? AND stale = 0 AND status != 'stale'
            """,
            (str(source_path),),
        ).fetchall()
    return sum(
        1
        for row in rows
        if loads_json(row["metadata_json"], default={}).get("source_sha256") == source_sha256
    )


def upsert(config: ConfigDocument, records: Iterable[VectorRecord]) -> VectorWriteResult:
    cfg = vector_config(config)
    backend = cfg["backend"]
    materialized = list(records)
    if backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {backend}")
    validate_records(materialized)
    path = local_store_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    changed_labels: list[tuple[int, tuple[float, ...], bool]] = []
    with connect(path) as conn:
        create_schema(conn)
        if backend == "local_hnsw":
            set_state(conn, "hnsw_dirty", "1")
        for record in materialized:
            label = upsert_record(conn, record)
            changed_labels.append((label, record.vector, record.stale or record.status == "stale"))
    index_path = ""
    if backend == "local_hnsw" and materialized:
        index = local_index_path(config)
        update_hnsw_index(config, changed_labels)
        index_path = str(index)
    return VectorWriteResult(
        backend=backend,
        records=len(materialized),
        store_path=str(path),
        index_path=index_path,
    )


def sync_records(config: ConfigDocument, records: Iterable[VectorRecord]) -> VectorSyncResult:
    """Synchronize canonical vectors by content hash without rebuilding unchanged rows."""

    cfg = vector_config(config)
    if cfg["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {cfg['backend']}")
    materialized = list(records)
    validate_records(materialized)
    by_id = {record.id: record for record in materialized}
    if len(by_id) != len(materialized):
        raise ValueError("Vector sync input contains duplicate record ids.")

    path = local_store_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        create_schema(conn)
        existing_rows = conn.execute(
            "SELECT id, stale, status, metadata_json FROM vectors"
        ).fetchall()
    existing = {str(row["id"]): row for row in existing_rows}
    stale_ids = sorted(set(existing) - set(by_id))
    changed: list[VectorRecord] = []
    unchanged = 0
    for record in materialized:
        row = existing.get(record.id)
        old_metadata = loads_json(row["metadata_json"], default={}) if row is not None else {}
        new_metadata = record.metadata or {}
        same_hash = bool(
            row is not None
            and str(old_metadata.get("content_hash") or "")
            and old_metadata.get("content_hash") == new_metadata.get("content_hash")
            and old_metadata.get("model") == new_metadata.get("model")
            and not bool(row["stale"])
            and str(row["status"]) != "stale"
        )
        if same_hash:
            unchanged += 1
        else:
            changed.append(record)
    stale_count = mark_stale_ids(config, stale_ids)
    result = upsert(config, changed) if changed else VectorWriteResult(
        backend=cfg["backend"],
        records=0,
        store_path=str(path),
        index_path=str(local_index_path(config)) if cfg["backend"] == "local_hnsw" else "",
    )
    return VectorSyncResult(
        backend=cfg["backend"],
        received=len(materialized),
        upserted=result.records,
        unchanged=unchanged,
        stale=stale_count,
        store_path=result.store_path,
        index_path=result.index_path,
    )


def source_records(config: ConfigDocument, source_paths: Iterable[str]) -> dict[str, VectorRecord]:
    """Load active records for a bounded set of canonical source paths."""

    sources = sorted({str(item) for item in source_paths if str(item)})
    if not sources:
        return {}
    cfg = vector_config(config)
    if cfg["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {cfg['backend']}")
    path = local_store_path(config)
    if not path.is_file():
        return {}
    placeholders = ",".join("?" for _ in sources)
    with connect(path) as conn:
        create_schema(conn)
        rows = conn.execute(
            f"""
            SELECT id, owner_type, owner_id, vector_json, source_path, chapter_number,
                   scene_number, status, stale, metadata_json
            FROM vectors
            WHERE source_path IN ({placeholders}) AND stale = 0 AND status != 'stale'
            """,
            sources,
        ).fetchall()
    return {
        str(row["id"]): VectorRecord(
            id=str(row["id"]),
            owner_type=str(row["owner_type"]),
            owner_id=str(row["owner_id"]),
            vector=tuple(parse_vector(row["vector_json"])),
            source_path=str(row["source_path"]),
            chapter_number=as_optional_int(row["chapter_number"]),
            scene_number=as_optional_int(row["scene_number"]),
            status=str(row["status"]),
            stale=bool(row["stale"]),
            metadata=loads_json(row["metadata_json"], default={}),
        )
        for row in rows
    }


def sync_source_records(
    config: ConfigDocument,
    records: Iterable[VectorRecord],
    *,
    source_paths: Iterable[str],
) -> VectorSyncResult:
    """Replace only the vector rows owned by explicitly named canonical sources."""

    cfg = vector_config(config)
    if cfg["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {cfg['backend']}")
    sources = sorted({str(item) for item in source_paths if str(item)})
    if not sources:
        raise ValueError("Incremental vector sync requires at least one source path.")
    materialized = list(records)
    validate_records(materialized)
    if any(record.source_path not in sources for record in materialized):
        raise ValueError("Incremental vector records must belong to the declared source paths.")
    by_id = {record.id: record for record in materialized}
    if len(by_id) != len(materialized):
        raise ValueError("Incremental vector input contains duplicate record ids.")

    existing = source_records(config, sources)
    stale_ids = sorted(set(existing) - set(by_id))
    changed: list[VectorRecord] = []
    unchanged = 0
    for record in materialized:
        old = existing.get(record.id)
        old_metadata = (old.metadata or {}) if old is not None else {}
        new_metadata = record.metadata or {}
        if (
            old is not None
            and old_metadata.get("content_hash") == new_metadata.get("content_hash")
            and old_metadata.get("model") == new_metadata.get("model")
            and old_metadata.get("source_sha256") == new_metadata.get("source_sha256")
        ):
            unchanged += 1
        else:
            changed.append(record)
    stale_count = mark_stale_ids(config, stale_ids)
    result = upsert(config, changed) if changed else VectorWriteResult(
        backend=cfg["backend"],
        records=0,
        store_path=str(local_store_path(config)),
        index_path=str(local_index_path(config)) if cfg["backend"] == "local_hnsw" else "",
    )
    return VectorSyncResult(
        backend=cfg["backend"],
        received=len(materialized),
        upserted=result.records,
        unchanged=unchanged,
        stale=stale_count,
        store_path=result.store_path,
        index_path=result.index_path,
    )


def query(config: ConfigDocument, request: VectorQuery) -> list[VectorHit]:
    cfg = vector_config(config)
    if cfg["backend"] == "local_hnsw":
        return query_hnsw(config, request)
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
    if cfg["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {cfg['backend']}")
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
        create_schema(conn)
        rows = conn.execute(
            f"""
            SELECT v.id
            FROM vectors v
            WHERE ({where}) AND v.stale = 0 AND v.status != 'stale'
            """,
            params,
        ).fetchall()
    return mark_stale_ids(config, [str(row["id"]) for row in rows])


def rebuild_from_files(config: ConfigDocument) -> VectorRebuildResult:
    root = resolve_project_root(config)
    source = root / "60_rag" / "metadata" / "embeddings.jsonl"
    records = [record_from_embedding(item) for item in iter_jsonl(source)]
    records = [item for item in records if item is not None]
    cfg = vector_config(config)
    if cfg["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {cfg['backend']}")
    result = replace_records(config, records)
    return VectorRebuildResult(
        backend=result.backend,
        records=result.records,
        source_file=str(source),
        store_path=result.store_path,
    )


def replace_records(config: ConfigDocument, records: Iterable[VectorRecord]) -> VectorWriteResult:
    """Replace one local index from a trusted canonical snapshot."""

    materialized = list(records)
    validate_records(materialized)
    cfg = vector_config(config)
    if cfg["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {cfg['backend']}")
    reset_local_store(config)
    return upsert(config, materialized)


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


def vector_config(config: ConfigDocument) -> dict[str, Any]:
    semantic = config.data.get("semantic", {}) if isinstance(config.data.get("semantic"), dict) else {}
    raw = semantic.get("vector_store") if isinstance(semantic.get("vector_store"), dict) else {}
    return {
        "backend": str(raw.get("backend") or "local_sqlite"),
        "url": str(raw.get("url") or ""),
        "index_url": str(raw.get("index_url") or ""),
        "collection": "longform_vectors",
        "metric": str(raw.get("metric") or "cosine"),
        "dim": int(raw.get("dim") or 1024),
        "hnsw_threshold": int(raw.get("hnsw_threshold") or 10_000),
        "hnsw_m": int(raw.get("hnsw_m") or 16),
        "hnsw_ef_construction": int(raw.get("hnsw_ef_construction") or 200),
        "hnsw_ef_search": int(raw.get("hnsw_ef_search") or 80),
        "hnsw_candidate_multiplier": int(raw.get("hnsw_candidate_multiplier") or 8),
    }


def local_store_path(config: ConfigDocument) -> Path:
    root = resolve_project_root(config)
    configured = vector_config(config)["url"]
    path = Path(configured) if configured else Path("70_runtime/db/vector_store.sqlite")
    return resolve_local_vector_path(root, path)


def local_index_path(config: ConfigDocument) -> Path:
    root = resolve_project_root(config)
    configured = vector_config(config)["index_url"]
    if configured:
        return resolve_local_vector_path(root, Path(configured))
    return local_store_path(config).with_suffix(".hnsw")


def resolve_local_vector_path(root: Path, path: Path) -> Path:
    """Confine mutable local vector files to the owning novel project."""

    resolved = path.expanduser().resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Local vector path escaped the project root: {path}") from exc
    return resolved


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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_labels (
            id TEXT PRIMARY KEY REFERENCES vectors(id) ON DELETE CASCADE,
            label INTEGER NOT NULL UNIQUE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_store_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_active ON vectors(stale, status, chapter_number)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vectors_owner ON vectors(owner_type, owner_id)")


def validate_records(records: list[VectorRecord]) -> None:
    dimensions = {len(record.vector) for record in records}
    if 0 in dimensions:
        raise ValueError("Vector records cannot contain empty vectors.")
    if len(dimensions) > 1:
        raise ValueError("All vector records in one mutation must have the same dimension.")
    if any(not record.id.strip() for record in records):
        raise ValueError("Vector record ids cannot be empty.")


def upsert_record(conn: sqlite3.Connection, record: VectorRecord) -> int:
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
    row = conn.execute("SELECT label FROM vector_labels WHERE id = ?", (record.id,)).fetchone()
    if row is not None:
        return int(row["label"])
    next_row = conn.execute("SELECT COALESCE(MAX(label), -1) + 1 AS next_label FROM vector_labels").fetchone()
    label = int(next_row["next_label"])
    conn.execute("INSERT INTO vector_labels (id, label) VALUES (?, ?)", (record.id, label))
    return label


def query_hnsw(config: ConfigDocument, request: VectorQuery) -> list[VectorHit]:
    if not request.vector or not hnsw_dependency_available():
        return []
    path = local_store_path(config)
    index_path = local_index_path(config)
    if not path.is_file() or not index_path.is_file():
        return []
    with connect(path) as conn:
        create_schema(conn)
        if state_value(conn, "hnsw_dirty") == "1":
            return []
        active_count = int(
            conn.execute(
                "SELECT COUNT(*) AS count FROM vectors WHERE stale = 0 AND status != 'stale'"
            ).fetchone()["count"]
        )
    if active_count == 0:
        return []

    cfg = vector_config(config)
    manifest = read_hnsw_manifest(index_path)
    dimension = int(manifest.get("dimension") or 0)
    if (
        manifest.get("schema") != "local_hnsw_index_v1"
        or int(manifest.get("active_records") or -1) != active_count
        or dimension <= 0
        or len(request.vector) != dimension
    ):
        return []
    hnswlib, numpy = load_hnsw_dependencies()
    index = hnswlib.Index(space=hnsw_space(str(cfg["metric"])), dim=dimension)
    try:
        index.load_index(str(index_path))
    except (OSError, RuntimeError):
        return []
    index.set_ef(max(int(cfg["hnsw_ef_search"]), request.top_k))
    probe = min(
        active_count,
        max(request.top_k * int(cfg["hnsw_candidate_multiplier"]), request.top_k, 32),
    )
    labels, distances = index.knn_query(
        numpy.asarray([request.vector], dtype=numpy.float32),
        k=probe,
    )
    ranked = [(int(label), float(distance)) for label, distance in zip(labels[0], distances[0])]
    if not ranked:
        return []
    placeholders = ",".join("?" for _ in ranked)
    with connect(path) as conn:
        rows = conn.execute(
            f"""
            SELECT l.label, v.id, v.owner_type, v.owner_id, v.source_path,
                   v.chapter_number, v.metadata_json
            FROM vector_labels l
            JOIN vectors v ON v.id = l.id
            WHERE l.label IN ({placeholders})
              AND v.stale = 0
              AND v.status != 'stale'
            """,
            [label for label, _distance in ranked],
        ).fetchall()
    rows_by_label = {int(row["label"]): row for row in rows}
    hits: list[VectorHit] = []
    for label, distance in ranked:
        row = rows_by_label.get(label)
        if row is None:
            continue
        owner_type = str(row["owner_type"])
        if request.owner_types and owner_type not in request.owner_types:
            continue
        chapter = row["chapter_number"]
        if request.max_chapter is not None and chapter is not None and int(chapter) > request.max_chapter:
            continue
        score = 1.0 - distance if str(cfg["metric"]) == "cosine" else -distance
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
        if len(hits) >= request.top_k:
            break
    return hits


def mark_stale_ids(config: ConfigDocument, record_ids: Iterable[str]) -> int:
    ids = sorted({str(record_id) for record_id in record_ids if str(record_id)})
    if not ids:
        return 0
    cfg = vector_config(config)
    if cfg["backend"] not in SUPPORTED_BACKENDS:
        raise ValueError(f"Unsupported vector backend: {cfg['backend']}")
    path = local_store_path(config)
    if not path.exists():
        return 0
    placeholders = ",".join("?" for _ in ids)
    labels: list[int] = []
    with connect(path) as conn:
        create_schema(conn)
        rows = conn.execute(
            f"""
            SELECT l.label
            FROM vector_labels l
            JOIN vectors v ON v.id = l.id
            WHERE v.id IN ({placeholders}) AND v.stale = 0 AND v.status != 'stale'
            """,
            ids,
        ).fetchall()
        labels = [int(row["label"]) for row in rows]
        if cfg["backend"] == "local_hnsw" and labels:
            set_state(conn, "hnsw_dirty", "1")
        cur = conn.execute(
            f"UPDATE vectors SET stale = 1, status = 'stale', updated_at = datetime('now') WHERE id IN ({placeholders}) AND stale = 0",
            ids,
        )
        changed = int(cur.rowcount or 0)
    if cfg["backend"] == "local_hnsw" and labels:
        update_hnsw_index(config, [(label, (), True) for label in labels])
    return changed


def update_hnsw_index(
    config: ConfigDocument,
    mutations: list[tuple[int, tuple[float, ...], bool]],
) -> None:
    if not mutations:
        return
    hnswlib, numpy = load_hnsw_dependencies()
    cfg = vector_config(config)
    store_path = local_store_path(config)
    index_path = local_index_path(config)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = read_hnsw_manifest(index_path)
    active_mutations = [(label, vector) for label, vector, stale in mutations if not stale]
    dimension = len(active_mutations[0][1]) if active_mutations else int(manifest.get("dimension") or 0)
    if dimension <= 0:
        rebuild_hnsw_from_sqlite(config)
        return
    if any(vector and len(vector) != dimension for _label, vector, _stale in mutations):
        raise ValueError("HNSW mutation dimension does not match the active index.")

    with connect(store_path) as conn:
        create_schema(conn)
        active_count, _stale_count = vector_counts(conn)
        total_labels = int(conn.execute("SELECT COUNT(*) AS count FROM vector_labels").fetchone()["count"])
    capacity = max(total_labels + 64, active_count + 64, 128)
    index = hnswlib.Index(space=hnsw_space(str(cfg["metric"])), dim=dimension)
    if index_path.is_file() and int(manifest.get("dimension") or 0) == dimension:
        index.load_index(str(index_path), max_elements=capacity, allow_replace_deleted=True)
        if index.get_max_elements() < capacity:
            index.resize_index(capacity)
    else:
        index.init_index(
            max_elements=capacity,
            ef_construction=int(cfg["hnsw_ef_construction"]),
            M=int(cfg["hnsw_m"]),
            allow_replace_deleted=True,
        )
    index.set_ef(int(cfg["hnsw_ef_search"]))
    for label, _vector, stale in mutations:
        if not stale:
            continue
        try:
            index.mark_deleted(label)
        except RuntimeError:
            pass
    if active_mutations:
        index.add_items(
            numpy.asarray([vector for _label, vector in active_mutations], dtype=numpy.float32),
            numpy.asarray([label for label, _vector in active_mutations], dtype=numpy.int64),
            replace_deleted=True,
        )
    persist_hnsw_index(config, index, dimension=dimension, active_records=active_count)


def rebuild_hnsw_from_sqlite(config: ConfigDocument) -> None:
    hnswlib, numpy = load_hnsw_dependencies()
    cfg = vector_config(config)
    store_path = local_store_path(config)
    index_path = local_index_path(config)
    with connect(store_path) as conn:
        create_schema(conn)
        rows = conn.execute(
            """
            SELECT l.label, v.vector_json
            FROM vector_labels l
            JOIN vectors v ON v.id = l.id
            WHERE v.stale = 0 AND v.status != 'stale'
            ORDER BY l.label
            """
        ).fetchall()
    if not rows:
        with connect(store_path) as conn:
            set_state(conn, "hnsw_dirty", "0")
        if index_path.exists():
            index_path.unlink()
        manifest_path = hnsw_manifest_path(index_path)
        if manifest_path.exists():
            manifest_path.unlink()
        return
    vectors = [parse_vector(row["vector_json"]) for row in rows]
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or 0 in dimensions:
        raise ValueError("Cannot rebuild HNSW from mixed or empty vector dimensions.")
    dimension = dimensions.pop()
    capacity = max(len(rows) + 64, 128)
    index = hnswlib.Index(space=hnsw_space(str(cfg["metric"])), dim=dimension)
    index.init_index(
        max_elements=capacity,
        ef_construction=int(cfg["hnsw_ef_construction"]),
        M=int(cfg["hnsw_m"]),
        allow_replace_deleted=True,
    )
    index.add_items(
        numpy.asarray(vectors, dtype=numpy.float32),
        numpy.asarray([int(row["label"]) for row in rows], dtype=numpy.int64),
    )
    index.set_ef(int(cfg["hnsw_ef_search"]))
    persist_hnsw_index(config, index, dimension=dimension, active_records=len(rows))


def persist_hnsw_index(config: ConfigDocument, index: Any, *, dimension: int, active_records: int) -> None:
    index_path = local_index_path(config)
    staging = index_path.with_name(f".{index_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        index.save_index(str(staging))
        staging.replace(index_path)
    finally:
        if staging.exists():
            staging.unlink()
    cfg = vector_config(config)
    payload = {
        "schema": "local_hnsw_index_v1",
        "dimension": dimension,
        "metric": cfg["metric"],
        "active_records": active_records,
        "collection": cfg["collection"],
    }
    atomic_write_text(
        hnsw_manifest_path(index_path),
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    with connect(local_store_path(config)) as conn:
        create_schema(conn)
        set_state(conn, "hnsw_dirty", "0")


def reset_local_store(config: ConfigDocument) -> None:
    path = local_store_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        create_schema(conn)
        conn.execute("DELETE FROM vector_labels")
        conn.execute("DELETE FROM vectors")
        set_state(conn, "hnsw_dirty", "1" if vector_config(config)["backend"] == "local_hnsw" else "0")
    if vector_config(config)["backend"] == "local_hnsw":
        index_path = local_index_path(config)
        if index_path.exists():
            index_path.unlink()
        manifest = hnsw_manifest_path(index_path)
        if manifest.exists():
            manifest.unlink()
        with connect(path) as conn:
            set_state(conn, "hnsw_dirty", "0")


def vector_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN stale = 0 AND status != 'stale' THEN 1 ELSE 0 END) AS active_count,
            SUM(CASE WHEN stale != 0 OR status = 'stale' THEN 1 ELSE 0 END) AS stale_count
        FROM vectors
        """
    ).fetchone()
    return int(row["active_count"] or 0), int(row["stale_count"] or 0)


def state_value(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM vector_store_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else ""


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO vector_store_state (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def hnsw_manifest_path(index_path: Path) -> Path:
    return index_path.with_suffix(index_path.suffix + ".json")


def read_hnsw_manifest(index_path: Path) -> dict[str, Any]:
    path = hnsw_manifest_path(index_path)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def hnsw_dependency_available() -> bool:
    return importlib.util.find_spec("hnswlib") is not None and importlib.util.find_spec("numpy") is not None


def load_hnsw_dependencies() -> tuple[Any, Any]:
    if not hnsw_dependency_available():
        raise ValueError('local_hnsw requires the "semantic" extra with hnswlib and numpy.')
    import hnswlib
    import numpy

    return hnswlib, numpy


def hnsw_space(metric: str) -> str:
    if metric not in {"cosine", "l2", "ip"}:
        raise ValueError("HNSW metric must be one of: cosine, l2, ip.")
    return metric


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
