"""User-level source library and project-scoped fanfiction source contracts.

The library is deliberately non-canonical.  A novel project pins immutable item
and extraction hashes, then separately promotes paraphrased facts into its own
fanfiction canon through the intelligence workflow.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat
import sys
import threading
from typing import Any, Callable, Iterable, Iterator, ParamSpec, TypeVar

import yaml

from longform_engine.agent_protocols import output_protocol_for_task
from longform_engine.agent_results import AgentResultProtocolError, parse_agent_output_files
from longform_engine.agent_tasks import (
    build_manifest,
    load_manifest,
    manifest_input_records,
    manifest_input_paths,
    manifest_output,
    relative_path as agent_relative_path,
    update_task_status,
    write_manifest,
)
from longform_engine.config import ConfigDocument
from longform_engine.research import ResearchItemResult, search_research
from longform_engine.research.pipeline import WebFetcher, search_web_candidates
from longform_engine.source_processing import (
    SourceProcessingError,
    approve_remote_decision,
    create_processing_job_payload,
    create_remote_decision_payload,
    detect_asset_format,
    load_normalized_segments,
    normalize_source_item,
    processor_capabilities,
    resolve_asset_path,
    run_openai_processing,
)
from longform_engine.source_protocols import (
    ASSET_SCHEMA,
    CANON_SCHEMA,
    COVERAGE_SCHEMA,
    INGEST_BATCH_SCHEMA,
    LIBRARY_INDEX_SCHEMA,
    LIBRARY_ITEM_SCHEMA,
    LIBRARY_WORK_SCHEMA,
    PROCESSING_JOB_SCHEMA,
    PROJECT_BINDING_SCHEMA,
    STORAGE_MODES,
    canonical_json_hash,
)
from longform_engine.semantic_protocols import (
    HUMAN_DECISION_SCHEMA,
    SEMANTIC_DOCUMENT_SCHEMA,
    approved_semantic_document,
    build_human_decision,
    build_semantic_document,
    build_workflow_record,
    seal_semantic_document,
    validate_semantic_document,
    validate_workflow_record,
)
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import list_finalized_chapter_files


PROJECT_WORK_SCHEMA = "同人作品资料_第1版"
EXTERNAL_REQUEST_SCHEMA = "external_work_research_request_v1"
INCREMENTAL_REQUEST_SCHEMA = "fanfiction_incremental_source_request_v1"
SOURCE_UPGRADE_PROPOSAL_SCHEMA = "fanfiction_source_upgrade_proposal_v1"
SOURCE_UPGRADE_REVIEW_SCHEMA = "fanfiction_source_upgrade_semantic_review_v1"
SOURCE_UPGRADE_DECISION_SCHEMA = "human_fanfiction_source_upgrade_decision_v1"
VERSION_CONFLICT_DECISION_SCHEMA = "fanfiction_version_conflict_decision_v1"

PROJECT_PACK_ROOT = Path("50_workbench") / "同人原著资料"
EXTERNAL_REQUEST_ROOT = Path("50_workbench") / "research_inbox" / "外部作品研究申请"

FULL_RETENTION_RIGHTS = frozenset(
    {"user_claimed_authorized", "public_domain_claimed", "platform_permitted_claimed"}
)
RIGHTS_STATUSES = FULL_RETENTION_RIGHTS | {"unverified"}
RETENTION_MODES = frozenset({"full_text", "short_evidence", "metadata_only"})
ASSET_ROLES = frozenset({"original", "subtitle", "cover", "attachment", "human_note"})
MAX_INGEST_FILES = 2_000
MAX_INGEST_FILE_BYTES = 16 * 1024 * 1024 * 1024
MAX_INGEST_BATCH_BYTES = 64 * 1024 * 1024 * 1024
COVERAGE_MODES = frozenset({"分层按需", "全作到截止点"})
COVERAGE_LEVELS = frozenset(
    {"identity", "design_core", "volume_scope", "chapter_dependency", "whole_to_cutoff"}
)
COVERAGE_STATES = frozenset({"missing", "covered", "not_applicable", "conflict"})
EXTERNAL_PURPOSES = frozenset(
    {"factual_reference", "technique_analysis", "inspiration", "use_original_elements"}
)
FACT_TYPE_ALIASES = {
    "character": {"character", "人物"},
    "relationship": {"relationship", "关系"},
    "world_rule": {"world_rule", "世界规则"},
    "ability": {"ability", "能力"},
    "event": {"event", "事件"},
    "timeline": {"timeline", "时间线"},
    "location": {"location", "地点"},
    "organization": {"organization", "组织"},
    "item": {"item", "物品"},
    "terminology": {"terminology", "术语"},
    "unresolved_question": {"unresolved_question", "未解决问题"},
    "version_conflict": {"version_conflict", "版本冲突"},
}


class FanfictionSourceError(ValueError):
    """Raised when a source-library or project binding contract is invalid."""


_P = ParamSpec("_P")
_R = TypeVar("_R")
_SOURCE_LIBRARY_LOCK_STATE = threading.local()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_library_root() -> Path:
    """Return the current user's source library or an explicit absolute override."""

    override = os.environ.get("LONGFORM_SOURCE_LIBRARY")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise FanfictionSourceError("LONGFORM_SOURCE_LIBRARY must be an absolute path")
        return path.resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return (base / "longform-novel-engine" / "原著资料库").resolve()


@contextmanager
def _source_library_lock(command: str) -> Iterator[None]:
    """Own the cross-process mutation boundary for the user-level library.

    Nested library APIs share the lock only in the current thread. Another
    process or Studio request still has to acquire the exclusive lock, and a
    crashed writer leaves inspectable metadata instead of allowing a race.
    """

    root = source_library_root()
    held_roots: set[str] = getattr(_SOURCE_LIBRARY_LOCK_STATE, "held_roots", set())
    root_key = str(root).casefold()
    if root_key in held_roots:
        yield
        return

    lock_dir = root / "暂存区"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "资料库.lock"
    owner_token = secrets.token_hex(16)
    metadata = {
        "schema": "source_library_lock_v1",
        "owner_token": owner_token,
        "command": command,
        "pid": os.getpid(),
        "created_at": utc_now(),
        "library_root": str(root),
    }
    try:
        descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        current = _read_json(lock_path, {})
        raise FanfictionSourceError(
            "原著资料库正被另一项写入任务占用："
            f"{current.get('command', 'unknown')} (pid={current.get('pid', 'unknown')})"
        ) from exc
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(metadata, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    _SOURCE_LIBRARY_LOCK_STATE.held_roots = {*held_roots, root_key}
    try:
        yield
    finally:
        _SOURCE_LIBRARY_LOCK_STATE.held_roots = held_roots
        current = _read_json(lock_path, {})
        if isinstance(current, dict) and current.get("owner_token") == owner_token:
            lock_path.unlink(missing_ok=True)


def _source_library_mutation(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Apply the library lifecycle lock to a public mutating operation."""

    @wraps(function)
    def guarded(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        with _source_library_lock(function.__name__):
            return function(*args, **kwargs)

    return guarded


@_source_library_mutation
def initialize_source_library() -> dict[str, Any]:
    root = source_library_root()
    for name in ("作品", "原件对象", "暂存区", "派生索引"):
        (root / name).mkdir(parents=True, exist_ok=True)
    index_path = root / "资料库索引.json"
    if index_path.exists():
        index = _read_json(index_path, None)
        actual_schema = index.get("schema") if isinstance(index, dict) else "invalid_json_object"
        if actual_schema != LIBRARY_INDEX_SCHEMA:
            raise FanfictionSourceError(
                f"source library schema {actual_schema!r} is incompatible with {LIBRARY_INDEX_SCHEMA}; "
                "the existing library was preserved. Re-import into a new v2 source library"
            )
    else:
        index = {
            "schema": LIBRARY_INDEX_SCHEMA,
            "works": [],
            "items": [],
            "batches": [],
            "updated_at": utc_now(),
        }
        _write_json(index_path, index)
    return {
        "schema": LIBRARY_INDEX_SCHEMA,
        "library_root": str(root),
        "index_file": str(index_path),
        "work_count": len(index.get("works") or []),
        "item_count": len(index.get("items") or []),
        "batch_count": len(index.get("batches") or []),
    }


def source_library_status() -> dict[str, Any]:
    root = source_library_root()
    index = _library_index(create=False)
    return {
        "schema": "source_library_status_v1",
        "library_root": str(root),
        "exists": (root / "资料库索引.json").is_file(),
        "work_count": len(index.get("works") or []),
        "item_count": len(index.get("items") or []),
    }


def source_library_catalog() -> dict[str, Any]:
    """Return browser-safe source metadata without local absolute asset paths."""

    index = _library_index(create=False)
    return {
        "schema": "source_library_catalog_v1",
        "works": [dict(item) for item in index.get("works") or [] if isinstance(item, dict)],
        "items": [
            {
                key: value
                for key, value in item.items()
                if key
                in {
                    "item_id",
                    "work_id",
                    "name",
                    "path",
                    "bundle_sha256",
                    "normalization_sha256",
                    "extraction_sha256",
                    "processing_status",
                    "retention_mode",
                    "supersedes_item_id",
                }
            }
            for item in index.get("items") or []
            if isinstance(item, dict)
        ],
        "batches": [dict(item) for item in index.get("batches") or [] if isinstance(item, dict)],
    }


@_source_library_mutation
def register_source_work(
    *,
    name: str,
    creator: str,
    aliases: Iterable[str] = (),
    versions: Iterable[str] = (),
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("source work registration requires --approved-by human")
    visible_name = _visible_name(name, field="name")
    creator = str(creator).strip()
    if not creator:
        raise FanfictionSourceError("source work creator is required")
    normalized_aliases = _string_list(aliases, field="aliases")
    normalized_versions = _string_list(versions, field="versions")
    root = source_library_root()
    index = _library_index(create=True)
    for record in index["works"]:
        if not isinstance(record, dict):
            continue
        if str(record.get("name") or "").casefold() == visible_name.casefold() and str(
            record.get("creator") or ""
        ).casefold() == creator.casefold():
            current = library_work(str(record.get("work_id") or ""))
            return {**current, "library_root": str(root), "created": False}
    work_id = "work_" + sha256(f"{visible_name}\0{creator}".encode("utf-8")).hexdigest()[:16]
    directory = _unique_named_directory(root / "作品", visible_name, stable_id=work_id)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "资料项").mkdir()
    payload = {
        "协议版本": LIBRARY_WORK_SCHEMA,
        "作品ID": work_id,
        "作品名称": visible_name,
        "原作者": creator,
        "作品别名": normalized_aliases,
        "版本列表": normalized_versions,
        "人工批准": "human",
        "创建时间": utc_now(),
    }
    _write_yaml(directory / "作品身份.yaml", payload)
    record = {
        "work_id": work_id,
        "name": visible_name,
        "creator": creator,
        "path": directory.relative_to(root).as_posix(),
        "identity_sha256": _file_hash(directory / "作品身份.yaml"),
    }
    index["works"].append(record)
    _write_library_index(index)
    return {**record, "library_root": str(root), "created": True}


@_source_library_mutation
def import_source_item(
    *,
    work_id: str,
    name: str,
    source_type: str,
    version: str,
    unit_range: str,
    source_method: str,
    rights_status: str,
    retention_mode: str,
    approved_by: str,
    file_path: str | Path | None = None,
    source_locator: str = "",
    supersedes_item_id: str = "",
    storage_mode: str = "managed_copy",
    asset_role: str = "original",
) -> dict[str, Any]:
    """Import one file through the v2 multi-asset item boundary.

    The public single-file command remains useful for small documents, but the
    item it creates is the same bundle contract used by batch ingestion.
    """

    files = [] if file_path is None else [(Path(file_path), asset_role, "")]
    return import_source_item_assets(
        work_id=work_id,
        name=name,
        source_type=source_type,
        version=version,
        unit_range=unit_range,
        source_method=source_method,
        rights_status=rights_status,
        retention_mode=retention_mode,
        approved_by=approved_by,
        files=files,
        source_locator=source_locator,
        supersedes_item_id=supersedes_item_id,
        storage_mode=storage_mode,
    )


@_source_library_mutation
def import_source_item_assets(
    *,
    work_id: str,
    name: str,
    source_type: str,
    version: str,
    unit_range: str,
    source_method: str,
    rights_status: str,
    retention_mode: str,
    approved_by: str,
    files: Iterable[tuple[Path, str, str]],
    source_locator: str = "",
    supersedes_item_id: str = "",
    storage_mode: str = "managed_copy",
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("source item import requires --approved-by human")
    if rights_status not in RIGHTS_STATUSES:
        raise FanfictionSourceError("unknown source rights_status")
    if retention_mode not in RETENTION_MODES:
        raise FanfictionSourceError("retention_mode must be full_text, short_evidence, or metadata_only")
    if retention_mode == "full_text" and rights_status not in FULL_RETENTION_RIGHTS:
        raise FanfictionSourceError(
            "full_text retention requires a user-claimed authorized, public-domain, or platform-permitted source"
        )
    if storage_mode not in STORAGE_MODES:
        raise FanfictionSourceError("storage_mode must be managed_copy or external_reference")
    work = library_work(work_id)
    root = source_library_root()
    work_dir = root / str(work["path"])
    visible_name = _visible_name(name, field="name")
    source_type = str(source_type).strip()
    version = str(version).strip()
    unit_range = str(unit_range).strip()
    source_method = str(source_method).strip()
    if not all((source_type, version, unit_range, source_method)):
        raise FanfictionSourceError("source type, version, unit range, and source method are required")

    normalized_files: list[tuple[Path, str, str]] = []
    for raw_path, role, relative_path in files:
        source = Path(raw_path).expanduser().resolve()
        _validate_ingest_source(source)
        if role not in ASSET_ROLES:
            raise FanfictionSourceError(f"unsupported source asset role: {role}")
        normalized_files.append((source, role, _safe_relative_input(relative_path or source.name)))
    if not normalized_files and retention_mode != "metadata_only":
        raise FanfictionSourceError(f"{retention_mode} retention requires at least one file")
    if retention_mode == "metadata_only" and not source_locator.strip() and not normalized_files:
        raise FanfictionSourceError("metadata_only retention requires --source-locator or a file")
    if retention_mode == "short_evidence":
        _validate_short_evidence_files(normalized_files)

    assets = [
        _fix_source_asset(
            root=root,
            source=source,
            relative_path=relative_path,
            role=role,
            storage_mode=storage_mode,
            rights_status=rights_status,
            retention_mode=retention_mode,
            source_method=source_method,
        )
        for source, role, relative_path in normalized_files
    ]
    bundle_basis = [
        {
            "asset_id": asset["asset_id"],
            "sha256": asset["sha256"],
            "role": asset["role"],
            "relative_path": asset["relative_path"],
            "storage_mode": asset["storage_mode"],
        }
        for asset in sorted(assets, key=lambda value: (value["relative_path"], value["asset_id"]))
    ]
    if not bundle_basis:
        bundle_basis = [{"source_locator": source_locator.strip()}]
    bundle_sha = canonical_json_hash(bundle_basis)
    identity_basis = {
        "work_id": work_id,
        "bundle_sha256": bundle_sha,
        "source_type": source_type,
        "version": version,
        "unit_range": unit_range,
    }
    item_id = "item_" + canonical_json_hash(identity_basis)[:20]
    index = _library_index(create=True)
    existing = next(
        (item for item in index["items"] if isinstance(item, dict) and item.get("item_id") == item_id),
        None,
    )
    if existing is not None:
        current = library_item(str(existing.get("item_id") or ""))
        return {**current, "library_root": str(root), "created": False}
    if supersedes_item_id:
        old = library_item(supersedes_item_id)
        if old.get("work_id") != work_id:
            raise FanfictionSourceError("superseded source item must belong to the same work")

    item_dir = _unique_named_directory(work_dir / "资料项", visible_name, stable_id=item_id)
    pending_dir = item_dir.parent / f".{item_id}.partial"
    if pending_dir.exists():
        raise FanfictionSourceError(f"unfinished source item staging directory exists: {pending_dir}")
    pending_dir.mkdir(parents=True, exist_ok=False)
    payload = {
        "协议版本": LIBRARY_ITEM_SCHEMA,
        "资料项ID": item_id,
        "作品ID": work_id,
        "资料名称": visible_name,
        "资料类型": source_type,
        "版本名称": version,
        "覆盖范围": unit_range,
        "来源方式": source_method,
        "来源定位": source_locator.strip(),
        "权利状态": rights_status,
        "留存方式": retention_mode,
        "原件集合": assets,
        "组合哈希": bundle_sha,
        "规范化哈希": "",
        "提取哈希": "",
        "处理状态": "source_fixed",
        "替代资料项ID": supersedes_item_id,
        "人工批准": "human",
        "创建时间": utc_now(),
    }
    _write_yaml(pending_dir / "来源说明.yaml", payload)
    _write_json(
        pending_dir / "提取结果.json",
        build_semantic_document(
            document_id=f"sem_source_{item_id[5:]}",
            document_type="原著事实候选",
            title=f"{visible_name}语义提取",
            scope={"kind": "source_item", "item_id": item_id, "work_id": work_id},
            continuity="原著基线",
            body="",
            extensions={
                "task_type": "source_fact_extraction",
                "item_id": item_id,
                "bundle_sha256": bundle_sha,
                "normalization_sha256": "",
            },
            input_hashes=[bundle_sha],
        ),
    )
    _write_json(
        pending_dir / "证据索引.json",
        {
            "schema": "source_evidence_index_v2",
            "item_id": item_id,
            "bundle_sha256": bundle_sha,
            "normalization_sha256": "",
            "items": [],
        },
    )
    _write_json(
        pending_dir / "原件清单.json",
        {
            "schema": "source_asset_manifest_v1",
            "item_id": item_id,
            "bundle_sha256": bundle_sha,
            "assets": assets,
        },
    )
    record = {
        "item_id": item_id,
        "work_id": work_id,
        "name": visible_name,
        "path": item_dir.relative_to(root).as_posix(),
        "bundle_sha256": bundle_sha,
        "normalization_sha256": "",
        "extraction_sha256": "",
        "retention_mode": retention_mode,
        "supersedes_item_id": supersedes_item_id,
    }
    try:
        pending_dir.replace(item_dir)
        index["items"].append(record)
        _write_library_index(index)
    except Exception:
        if item_dir.is_dir():
            _remove_owned_directory(item_dir, owner=item_dir.parent)
        elif pending_dir.is_dir():
            _remove_owned_directory(pending_dir, owner=pending_dir.parent)
        raise
    return {**record, "asset_count": len(assets), "library_root": str(root), "created": True}


@_source_library_mutation
def create_source_ingest_plan(
    *,
    work_id: str,
    file_paths: Iterable[str | Path] = (),
    directory: str | Path | None = None,
    source_type: str,
    version: str,
    unit_range: str,
    source_method: str,
    rights_status: str,
    retention_mode: str,
    storage_mode: str = "managed_copy",
) -> dict[str, Any]:
    """Stage a bounded batch and produce editable grouping suggestions.

    Directory traversal never follows links or Windows reparse points.  The
    batch is non-authoritative until ``apply_source_ingest_batch`` receives an
    explicit human decision.
    """

    work = library_work(work_id)
    if rights_status not in RIGHTS_STATUSES or retention_mode not in RETENTION_MODES:
        raise FanfictionSourceError("ingest plan rights_status or retention_mode is invalid")
    if retention_mode == "full_text" and rights_status not in FULL_RETENTION_RIGHTS:
        raise FanfictionSourceError("full_text retention requires an eligible rights declaration")
    if storage_mode not in STORAGE_MODES:
        raise FanfictionSourceError("storage_mode must be managed_copy or external_reference")
    selected: list[tuple[Path, str]] = []
    for value in file_paths:
        path = Path(value).expanduser().resolve()
        _validate_ingest_source(path)
        selected.append((path, path.name))
    if directory is not None:
        source_dir = Path(directory).expanduser().resolve()
        _validate_ingest_directory(source_dir)
        for path in _walk_ingest_directory(source_dir):
            selected.append((path, path.relative_to(source_dir).as_posix()))
    by_path = {str(path).casefold(): (path, relative) for path, relative in selected}
    selected = list(by_path.values())
    if not selected:
        raise FanfictionSourceError("ingest plan requires at least one file")
    if len(selected) > MAX_INGEST_FILES:
        raise FanfictionSourceError(f"ingest batch exceeds the {MAX_INGEST_FILES}-file limit")
    sizes = [path.stat().st_size for path, _relative in selected]
    if any(size > MAX_INGEST_FILE_BYTES for size in sizes):
        raise FanfictionSourceError("an ingest file exceeds the 16 GiB per-file limit")
    if sum(sizes) > MAX_INGEST_BATCH_BYTES:
        raise FanfictionSourceError("ingest batch exceeds the 64 GiB batch limit")
    usage = shutil.disk_usage(source_library_root().parent)
    if storage_mode == "managed_copy" and usage.free < sum(sizes) + 256 * 1024 * 1024:
        raise FanfictionSourceError("insufficient disk space for managed source ingestion")

    basis = [
        {"path": str(path), "relative_path": relative, "size": size, "sha256": _file_hash(path)}
        for (path, relative), size in zip(selected, sizes, strict=True)
    ]
    batch_id = "ingest_" + canonical_json_hash(
        {"work_id": work_id, "storage_mode": storage_mode, "files": basis}
    )[:20]
    root = source_library_root()
    batch_dir = root / "暂存区" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for entry, (source, _relative) in zip(basis, selected, strict=True):
        detected = detect_asset_format(source)
        staged_path = ""
        if storage_mode == "managed_copy":
            target = batch_dir / "文件" / _safe_relative_input(str(entry["relative_path"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            _stream_copy(source, target)
            staged_path = target.relative_to(root).as_posix()
        records.append(
            {
                "file_id": "staged_" + canonical_json_hash(
                    {"sha256": entry["sha256"], "relative_path": entry["relative_path"]}
                )[:20],
                "original_name": source.name,
                "relative_path": entry["relative_path"],
                "source_path": str(source) if storage_mode == "external_reference" else "",
                "staged_path": staged_path,
                "size_bytes": entry["size"],
                "sha256": entry["sha256"],
                "detected_format": detected["detected_format"],
                "media_type": detected["media_type"],
                "suffix_matches": detected["suffix_matches"] == "true",
                "role": _suggest_asset_role(source.suffix),
            }
        )
    groups: dict[str, list[str]] = {}
    for record in records:
        relative = Path(str(record["relative_path"]))
        key = relative.parts[0] if len(relative.parts) > 1 else relative.stem
        groups.setdefault(key, []).append(str(record["file_id"]))
    payload = {
        "schema": INGEST_BATCH_SCHEMA,
        "batch_id": batch_id,
        "work_id": work_id,
        "work_name": work["name"],
        "status": "awaiting_import_approval",
        "storage_mode": storage_mode,
        "defaults": {
            "source_type": str(source_type).strip(),
            "version": str(version).strip(),
            "unit_range": str(unit_range).strip(),
            "source_method": str(source_method).strip(),
            "rights_status": rights_status,
            "retention_mode": retention_mode,
        },
        "files": records,
        "groups": [
            {"name": name, "file_ids": file_ids, "approved": False}
            for name, file_ids in sorted(groups.items())
        ],
        "created_at": utc_now(),
        "network_performed": False,
    }
    batch_file = batch_dir / "导入批次.json"
    _write_json(batch_file, payload)
    index = _library_index(create=True)
    index.setdefault("batches", [])
    index["batches"] = [
        item
        for item in index["batches"]
        if not isinstance(item, dict) or item.get("batch_id") != batch_id
    ]
    index["batches"].append(
        {
            "batch_id": batch_id,
            "work_id": work_id,
            "path": batch_file.relative_to(root).as_posix(),
            "status": payload["status"],
        }
    )
    _write_library_index(index)
    return {**payload, "batch_file": str(batch_file)}


def source_ingest_preview(batch_id: str) -> dict[str, Any]:
    payload, path = _source_ingest_batch(batch_id)
    return {
        **payload,
        "batch_file": str(path),
        "file_count": len(payload.get("files") or []),
        "total_bytes": sum(
            int(item.get("size_bytes") or 0)
            for item in payload.get("files") or []
            if isinstance(item, dict)
        ),
    }


@_source_library_mutation
def confirm_source_ingest_groups(
    *, batch_id: str, groups: list[dict[str, Any]], approved_by: str
) -> dict[str, Any]:
    """Persist one complete, human-owned grouping decision before import."""

    if approved_by != "human":
        raise FanfictionSourceError("资料分组确认需要 --approved-by human")
    batch, path = _source_ingest_batch(batch_id)
    if batch.get("status") != "awaiting_import_approval":
        raise FanfictionSourceError("当前导入批次不再等待资料分组确认")
    files = {
        str(item.get("file_id") or ""): item
        for item in batch.get("files") or []
        if isinstance(item, dict) and item.get("file_id")
    }
    if not groups or len(groups) > len(files):
        raise FanfictionSourceError("资料分组必须为非空列表，且不能多于暂存文件数")

    allowed_fields = {"name", "file_ids", "approved", "source_type", "version", "unit_range"}
    normalized: list[dict[str, Any]] = []
    assigned_file_ids: set[str] = set()
    group_names: set[str] = set()
    for index, group in enumerate(groups):
        if not isinstance(group, dict) or not set(group).issubset(allowed_fields):
            raise FanfictionSourceError(f"资料分组[{index}]包含未知字段")
        name = _visible_name(str(group.get("name") or ""), field=f"groups[{index}].name")
        name_key = name.casefold()
        if name_key in group_names:
            raise FanfictionSourceError("资料分组名称不能重复")
        group_names.add(name_key)
        approved = group.get("approved")
        if not isinstance(approved, bool):
            raise FanfictionSourceError(f"资料分组[{index}].approved 必须为布尔值")
        raw_file_ids = group.get("file_ids")
        if not isinstance(raw_file_ids, list) or not raw_file_ids:
            raise FanfictionSourceError(f"资料分组[{index}]必须至少包含一个文件")
        file_ids = [str(value or "") for value in raw_file_ids]
        if len(file_ids) != len(set(file_ids)) or any(value not in files for value in file_ids):
            raise FanfictionSourceError(f"资料分组[{index}]包含重复或未知的暂存文件")
        overlap = assigned_file_ids.intersection(file_ids)
        if overlap:
            raise FanfictionSourceError("同一暂存文件不能被分配到多个资料项")
        assigned_file_ids.update(file_ids)
        overrides: dict[str, str] = {}
        for field in ("source_type", "version", "unit_range"):
            value = str(group.get(field) or "").strip()
            if field in group and not value:
                raise FanfictionSourceError(f"资料分组[{index}].{field} 不能为空")
            overrides[field] = value
        normalized.append(
            {
                "name": name,
                "file_ids": file_ids,
                "approved": approved,
                **overrides,
            }
        )
    if assigned_file_ids != set(files):
        missing = sorted(set(files) - assigned_file_ids)
        raise FanfictionSourceError("资料分组必须逐一覆盖全部暂存文件；未分组：" + ", ".join(missing))
    if not any(group["approved"] for group in normalized):
        raise FanfictionSourceError("至少需要批准一个资料分组，未采用文件可放入 approved=false 的分组")

    grouping_sha = canonical_json_hash(normalized)
    batch["groups"] = normalized
    batch["grouping_sha256"] = grouping_sha
    batch["groups_confirmed_by"] = "human"
    batch["groups_confirmed_at"] = utc_now()
    _write_json(path, batch)
    return {
        "schema": INGEST_BATCH_SCHEMA,
        "batch_id": batch_id,
        "status": "awaiting_import_approval",
        "grouping_sha256": grouping_sha,
        "groups": normalized,
        "approved_group_count": sum(group["approved"] for group in normalized),
    }


@_source_library_mutation
def apply_source_ingest_batch(*, batch_id: str, approved_by: str) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("ingest batch apply requires --approved-by human")
    batch, path = _source_ingest_batch(batch_id)
    if batch.get("status") != "awaiting_import_approval":
        raise FanfictionSourceError("ingest batch is not awaiting import approval")
    groups = batch.get("groups")
    if not isinstance(groups, list) or not groups:
        raise FanfictionSourceError("ingest batch has no groups")
    if batch.get("groups_confirmed_by") != "human" or batch.get(
        "grouping_sha256"
    ) != canonical_json_hash(groups):
        raise FanfictionSourceError("资料分组尚未确认或确认后已经变化；请重新执行 ingest-confirm")
    files = {
        str(item.get("file_id")): item
        for item in batch.get("files") or []
        if isinstance(item, dict)
    }
    raw_defaults = batch.get("defaults")
    defaults: dict[str, Any] = raw_defaults if isinstance(raw_defaults, dict) else {}
    index_before = json.loads(json.dumps(_library_index(create=False), ensure_ascii=False))
    try:
        imported = _apply_source_ingest_groups(batch, groups, files, defaults)
    except Exception as exc:
        current = _library_index(create=False)
        before_ids = {
            str(item.get("item_id") or "")
            for item in index_before.get("items") or []
            if isinstance(item, dict)
        }
        for record in current.get("items") or []:
            if not isinstance(record, dict) or str(record.get("item_id") or "") in before_ids:
                continue
            directory = source_library_root() / str(record.get("path") or "")
            if directory.is_dir():
                _remove_owned_directory(directory, owner=source_library_root() / "作品")
        for record in index_before.get("batches") or []:
            if isinstance(record, dict) and record.get("batch_id") == batch_id:
                record["status"] = "failed"
        _write_library_index(index_before)
        batch["status"] = "failed"
        batch["diagnostic"] = str(exc)
        _write_json(path, batch)
        raise
    if not imported:
        raise FanfictionSourceError("no ingest group has approved=true")
    batch["status"] = "applied"
    batch["approved_by"] = "human"
    batch["applied_at"] = utc_now()
    batch["item_ids"] = [item["item_id"] for item in imported]
    _write_json(path, batch)
    index = _library_index(create=False)
    for record in index.get("batches") or []:
        if isinstance(record, dict) and record.get("batch_id") == batch_id:
            record["status"] = "applied"
    _write_library_index(index)
    return {
        "schema": INGEST_BATCH_SCHEMA,
        "batch_id": batch_id,
        "status": "applied",
        "items": imported,
    }


def source_processing_capabilities() -> dict[str, Any]:
    return {**processor_capabilities(), "library_root": str(source_library_root())}


@_source_library_mutation
def create_source_processing_job(
    *,
    item_id: str,
    asset_ids: Iterable[str] = (),
    execution: str = "local",
    processor_id: str = "auto",
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = library_item(item_id)
    selected = list(asset_ids) or [str(asset["asset_id"]) for asset in item.get("assets") or []]
    job = create_processing_job_payload(
        item_id=item_id,
        bundle_sha256=str(item["bundle_sha256"]),
        asset_ids=selected,
        execution=execution,
        processor_id=processor_id,
        parameters=parameters,
    )
    item_dir = _resolve_library_item_directory(item)
    path = item_dir / "处理任务" / f"{job['job_id']}.json"
    _write_json(path, job)
    return {**job, "job_file": str(path)}


@_source_library_mutation
def run_source_processing_job(*, item_id: str, job_id: str) -> dict[str, Any]:
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    job_path = item_dir / "处理任务" / f"{job_id}.json"
    job = _read_json(job_path, None)
    if not isinstance(job, dict) or job.get("schema") != PROCESSING_JOB_SCHEMA:
        raise FanfictionSourceError(f"unknown or invalid source processing job: {job_id}")
    try:
        result = normalize_source_item(
            library_root=source_library_root(), item_dir=item_dir, item=item, job=job
        )
    except SourceProcessingError as exc:
        job["status"] = "failed"
        job["diagnostic"] = str(exc)
        _write_json(job_path, job)
        raise FanfictionSourceError(str(exc)) from exc
    job["status"] = result["status"]
    job["normalization_sha256"] = result["normalization_sha256"]
    _write_json(job_path, job)
    _update_library_item_processing(
        item,
        normalization_sha256=result["normalization_sha256"],
        processing_status=result["status"],
    )
    return result


def source_item_status(item_id: str) -> dict[str, Any]:
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    manifest = _read_json(item_dir / "规范化" / "规范化清单.json", {})
    return {
        "schema": "source_item_status_v1",
        "item_id": item_id,
        "bundle_sha256": item.get("bundle_sha256", ""),
        "normalization_sha256": item.get("normalization_sha256", ""),
        "extraction_sha256": item.get("extraction_sha256", ""),
        "processing_status": item.get("processing_status", "source_fixed"),
        "asset_count": len(item.get("assets") or []),
        "normalization_manifest": manifest,
    }


def source_evidence_preview(
    item_id: str, *, offset: int = 0, limit: int = 40
) -> dict[str, Any]:
    """Return a bounded review projection without exposing complete normalized text."""

    if offset < 0 or limit <= 0 or limit > 100:
        raise FanfictionSourceError("evidence preview requires offset >= 0 and 1 <= limit <= 100")
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    try:
        segments = load_normalized_segments(
            item_dir, expected_sha256=str(item.get("normalization_sha256") or "")
        )
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    page = segments[offset : offset + limit]
    return {
        "schema": "source_evidence_preview_v1",
        "item_id": item_id,
        "normalization_sha256": item.get("normalization_sha256", ""),
        "offset": offset,
        "limit": limit,
        "total": len(segments),
        "has_more": offset + limit < len(segments),
        "segments": [
            {
                "segment_id": segment.get("segment_id", ""),
                "normalized_text": str(segment.get("normalized_text") or "")[:800],
                "text_truncated": len(str(segment.get("normalized_text") or "")) > 800,
                "origin_locator": segment.get("origin_locator", {}),
                "derivation": segment.get("derivation", {}),
                "review_status": segment.get("review_status", ""),
            }
            for segment in page
        ],
        "full_normalized_text_exposed": False,
        "canon_status": "non_canonical",
    }


@_source_library_mutation
def create_source_evidence_review_template(*, item_id: str) -> dict[str, Any]:
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    try:
        segments = load_normalized_segments(
            item_dir, expected_sha256=str(item.get("normalization_sha256") or "")
        )
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    target = item_dir / "证据审查" / "待审证据.json"
    created = not target.exists()
    if created:
        _write_json(
            target,
            build_semantic_document(
                document_id="sem_review_" + canonical_json_hash(
                    {"item_id": item_id, "normalization_sha256": item["normalization_sha256"]}
                )[:20],
                document_type="原著证据复核",
                title=f"{item.get('name', item_id)}证据复核",
                scope={"kind": "source_item", "item_id": item_id, "work_id": item.get("work_id", "")},
                continuity="原著基线",
                body="",
                extensions={
                    "task_type": "source_evidence_review",
                    "review_type": "segment_evidence",
                    "item_id": item_id,
                    "normalization_sha256": item["normalization_sha256"],
                    "decisions": [
                    {
                        "segment_id": segment["segment_id"],
                        "decision": "pending",
                        "corrected_text": "",
                        "reason": "",
                    }
                    for segment in segments
                    if segment.get("review_status") == "pending_human"
                    ],
                },
                input_hashes=[item["bundle_sha256"], item["normalization_sha256"]],
            ),
        )
    return {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "item_id": item_id,
        "review_file": str(target),
        "pending_count": sum(segment.get("review_status") == "pending_human" for segment in segments),
        "created": created,
    }


@_source_library_mutation
def apply_source_evidence_review(
    *, item_id: str, file_path: str | Path, approved_by: str
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("source evidence review requires --approved-by human")
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    review = _read_json(Path(file_path).expanduser().resolve(), None)
    if not isinstance(review, dict):
        raise FanfictionSourceError("source evidence review must be a JSON object")
    try:
        review = seal_semantic_document(review)
    except ValueError as exc:
        raise FanfictionSourceError(f"invalid semantic evidence review: {exc}") from exc
    review_errors = validate_semantic_document(review)
    if review_errors:
        raise FanfictionSourceError("invalid semantic evidence review: " + "; ".join(review_errors))
    raw_extensions = review.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    if review.get("document_type") != "原著证据复核" or extensions.get("review_type") != "segment_evidence":
        raise FanfictionSourceError("source evidence review must use review_type=segment_evidence")
    if extensions.get("item_id") != item_id or extensions.get("normalization_sha256") != item.get(
        "normalization_sha256"
    ):
        raise FanfictionSourceError("source evidence review basis is stale")
    try:
        segments = load_normalized_segments(
            item_dir, expected_sha256=str(item.get("normalization_sha256") or "")
        )
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    pending = {
        str(segment["segment_id"]): segment
        for segment in segments
        if segment.get("review_status") == "pending_human"
    }
    decisions = extensions.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise FanfictionSourceError("source evidence review decisions must be a non-empty list")
    by_id: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        fields = {"segment_id", "decision", "corrected_text", "reason"}
        if not isinstance(decision, dict) or set(decision) != fields:
            raise FanfictionSourceError("source evidence review decision fields are invalid")
        segment_id = str(decision.get("segment_id") or "")
        if segment_id in by_id or segment_id not in pending:
            raise FanfictionSourceError("source evidence review must cover current pending segments once")
        if decision.get("decision") not in {"approve", "reject"}:
            raise FanfictionSourceError("source evidence decision must be approve or reject")
        if not str(decision.get("reason") or "").strip():
            raise FanfictionSourceError("source evidence decision requires a reason")
        by_id[segment_id] = decision
    for segment in segments:
        decision = by_id.get(str(segment["segment_id"]))
        if decision is None:
            continue
        if decision["decision"] == "reject":
            segment["review_status"] = "rejected"
            continue
        corrected = str(decision.get("corrected_text") or "").strip()
        if corrected:
            segment["normalized_text"] = corrected
            segment["text_sha256"] = sha256(corrected.encode("utf-8")).hexdigest()
            segment["segment_id"] = "seg_" + canonical_json_hash(
                {
                    "previous_segment_id": decision["segment_id"],
                    "corrected_text": corrected,
                }
            )[:20]
        segment["review_status"] = "approved"
    segment_text = "".join(
        json.dumps(segment, ensure_ascii=False, sort_keys=True) + "\n" for segment in segments
    )
    review_record = _approve_noncanonical_semantic_review(review, item_id=item_id)
    review_sha = canonical_json_hash(review_record)
    review_file = item_dir / "证据审查" / f"{review_sha}.json"
    _write_json(review_file, review_record)
    old_manifest = _read_json(item_dir / "规范化" / "规范化清单.json", {})
    unresolved_codes = {
        str(item.get("code") or "")
        for item in old_manifest.get("diagnostics") or []
        if isinstance(item, dict)
    }
    remaining_pending = any(segment.get("review_status") == "pending_human" for segment in segments)
    review_status = (
        "reading_order_required"
        if "reading_order_required" in unresolved_codes
        else "partial_parse"
        if unresolved_codes & {"ocr_required", "asr_required", "partial_parse"}
        else "capability_missing"
        if "capability_missing" in unresolved_codes
        else "evidence_review_pending"
        if remaining_pending
        else "evidence_ready"
    )
    manifest = {
        **old_manifest,
        "segment_sha256": sha256(segment_text.encode("utf-8")).hexdigest(),
        "segment_count": len(segments),
        "status": review_status,
        "evidence_review_sha256": review_sha,
    }
    normalization_sha = canonical_json_hash(manifest)
    version_dir = item_dir / "规范化" / "版本" / normalization_sha
    version_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(version_dir / "证据分段.jsonl", segment_text)
    _write_json(version_dir / "规范化清单.json", manifest)
    atomic_write_text(item_dir / "规范化" / "证据分段.jsonl", segment_text)
    _write_json(item_dir / "规范化" / "规范化清单.json", manifest)
    _update_library_item_processing(
        item,
        normalization_sha256=normalization_sha,
        processing_status=review_status,
    )
    return {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "item_id": item_id,
        "normalization_sha256": normalization_sha,
        "review_sha256": review_sha,
        "review_file": str(review_file),
        "approved_segments": sum(segment["review_status"] == "approved" for segment in segments),
        "rejected_segments": sum(segment["review_status"] == "rejected" for segment in segments),
    }


SOURCE_AGENT_TASK_TYPES = frozenset(
    {
        "source_fact_extraction",
        "source_visual_observation",
        "source_evidence_review",
        "source_version_conflict_review",
        "source_coverage_gap_analysis",
        "source_timeline_alignment",
        "source_conflict_analysis",
    }
)
MAX_SOURCE_TASK_SEGMENTS = 80


@_source_library_mutation
def create_source_agent_task(
    *,
    item_id: str,
    task_type: str,
    segment_ids: Iterable[str] = (),
    review_type: str = "",
    candidate_file: str | Path | None = None,
) -> dict[str, Any]:
    """Create a bounded source-item AgentTaskManifest without exposing the library."""

    normalized_type = str(task_type or "").strip().lower().replace("-", "_")
    if normalized_type not in SOURCE_AGENT_TASK_TYPES:
        raise FanfictionSourceError(
            "source Agent task_type must be one of: " + ", ".join(sorted(SOURCE_AGENT_TASK_TYPES))
        )
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    normalization_sha = str(item.get("normalization_sha256") or "")
    if not normalization_sha:
        raise FanfictionSourceError("source Agent tasks require a current normalization")
    try:
        segments = load_normalized_segments(item_dir, expected_sha256=normalization_sha)
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    requested = {str(value).strip() for value in segment_ids if str(value).strip()}
    known_ids = {str(segment.get("segment_id") or "") for segment in segments}
    unknown = sorted(requested - known_ids)
    if unknown:
        raise FanfictionSourceError("unknown normalized segment ids: " + ", ".join(unknown))
    selected = [segment for segment in segments if not requested or segment.get("segment_id") in requested]
    if normalized_type == "source_fact_extraction":
        selected = [
            segment
            for segment in selected
            if segment.get("review_status") in {"source_exact", "approved"}
        ]
        if item.get("processing_status") != "evidence_ready":
            raise FanfictionSourceError("fact extraction tasks require evidence_ready")
    elif normalized_type == "source_visual_observation":
        selected = [
            segment
            for segment in selected
            if (segment.get("origin_locator") or {}).get("kind")
            in {"image_region", "video_time_range"}
        ]
    elif normalized_type == "source_evidence_review" and review_type != "visual_observation":
        selected = [segment for segment in selected if segment.get("review_status") == "pending_human"]
    if len(selected) > MAX_SOURCE_TASK_SEGMENTS:
        if requested:
            raise FanfictionSourceError(
                f"one source Agent task may declare at most {MAX_SOURCE_TASK_SEGMENTS} segments"
            )
        selected = selected[:MAX_SOURCE_TASK_SEGMENTS]
    source_media_assets = (
        _source_task_media_inputs(item, selected)
        if normalized_type in {"source_visual_observation", "source_timeline_alignment"}
        else []
    )
    if not selected and not source_media_assets and not (
        normalized_type == "source_evidence_review" and review_type == "visual_observation"
    ):
        raise FanfictionSourceError("no eligible normalized evidence segments for this task")

    candidate_path: Path | None = None
    candidate_payload: dict[str, Any] | None = None
    if candidate_file is not None:
        candidate_path = Path(candidate_file).expanduser().resolve()
        try:
            candidate_path.relative_to(item_dir)
        except ValueError as exc:
            raise FanfictionSourceError("source task candidate_file must live inside this source item") from exc
        candidate_payload = _read_json(candidate_path, None)
        if not isinstance(candidate_payload, dict):
            raise FanfictionSourceError("source task candidate_file must be a JSON object")
    if normalized_type == "source_evidence_review" and review_type == "visual_observation":
        if candidate_payload is None:
            raise FanfictionSourceError("visual observation review requires --candidate-file")
        observation_errors = validate_semantic_document(candidate_payload)
        if observation_errors:
            raise FanfictionSourceError("invalid visual observation candidate: " + "; ".join(observation_errors))
        raw_candidate_extensions = candidate_payload.get("extensions")
        candidate_extensions: dict[str, Any] = (
            raw_candidate_extensions if isinstance(raw_candidate_extensions, dict) else {}
        )
        if (
            candidate_payload.get("document_type") != "视觉直接观察候选"
            or candidate_extensions.get("item_id") != item_id
            or candidate_extensions.get("normalization_sha256") != normalization_sha
        ):
            raise FanfictionSourceError("visual observation candidate basis is stale")

    review_variant = review_type or {
        "source_evidence_review": "segment_evidence",
        "source_version_conflict_review": "version_conflict",
        "source_coverage_gap_analysis": "coverage_gap",
    }.get(normalized_type, "")
    if normalized_type == "source_evidence_review" and review_variant not in {
        "segment_evidence",
        "visual_observation",
    }:
        raise FanfictionSourceError(
            "source_evidence_review review_type must be segment_evidence or visual_observation"
        )

    basis = canonical_json_hash(
        {
            "task_type": normalized_type,
            "item_id": item_id,
            "normalization_sha256": normalization_sha,
            "segment_ids": [segment["segment_id"] for segment in selected],
            "candidate_sha256": (
                sha256(candidate_path.read_bytes()).hexdigest() if candidate_path else ""
            ),
            "review_type": review_variant,
        }
    )
    item_token = re.sub(r"[^a-z0-9_]+", "_", item_id.lower().replace("-", "_")).strip("_")
    task_id = f"{normalized_type}:source-{item_token[:48]}:v5:{basis[:12]}"
    file_token = re.sub(r"[^a-z0-9_.-]+", "_", task_id.lower().replace(":", "_"))
    input_dir = item_dir / "Agent工单输入"
    input_dir.mkdir(parents=True, exist_ok=True)
    pack_file = input_dir / f"{file_token}.json"
    brief_file = input_dir / f"{file_token}.md"
    pack_payload = {
        "schema": "source_agent_evidence_pack_v1",
        "item": {
            "item_id": item_id,
            "work_id": item.get("work_id", ""),
            "name": item.get("name", ""),
            "source_type": item.get("source_type", ""),
            "version": item.get("version", ""),
            "unit_range": item.get("unit_range", ""),
            "bundle_sha256": item.get("bundle_sha256", ""),
            "normalization_sha256": normalization_sha,
        },
        "task_type": normalized_type,
        "review_type": review_variant,
        "segments": selected,
        "boundaries": [
            "只允许使用本证据包与 manifest 中逐项声明的图片。",
            "资料结论保持非 Canon，必须经过 CLI 校验和人工 apply。",
            "禁止用模型记忆、其他作品或其他项目补全。",
            "禁止连续复现原著、字幕、剧本或漫画文字。",
        ],
    }
    _write_json(pack_file, pack_payload)
    brief_lines = [
        f"# 原著资料语义工单：{normalized_type}",
        "",
        f"- 资料项：{item.get('name', '')}（{item_id}）",
        f"- 版本与范围：{item.get('version', '')} / {item.get('unit_range', '')}",
        f"- 规范化哈希：{normalization_sha}",
        f"- 证据片段数：{len(selected)}",
        f"- 输出协议：{output_protocol_for_task(normalized_type)}",
        "",
        "只读取 manifest 的 io.inputs，一次只写声明的一个 JSON 输出。",
        "完整原件、其他资料项和任何小说项目都不在本任务权限内。",
    ]
    atomic_write_text(brief_file, "\n".join(brief_lines) + "\n")
    inputs: list[Path] = [brief_file, pack_file]
    if candidate_path is not None:
        inputs.append(candidate_path)
    output_file = item_dir / "Agent工单输出" / f"{file_token}.json"
    manifest_file = item_dir / "Agent工单" / f"{file_token}.manifest.json"
    validate_command = (
        f"longform-engine source-library task-validate --item-id {item_id} "
        f"--task-id {task_id}"
    )
    apply_command = (
        f"longform-engine source-library task-apply --item-id {item_id} "
        f"--task-id {task_id} --approved-by human"
    )
    failure_command = f"longform-engine source-library item-status --item-id {item_id}"
    manifest = build_manifest(
        item_dir,
        task_type=normalized_type,
        chapter_number=None,
        input_files=[agent_relative_path(item_dir, path) for path in inputs],
        allowed_output_paths=[agent_relative_path(item_dir, output_file)],
        output_schema=output_protocol_for_task(normalized_type),
        validate_command=validate_command,
        apply_command=apply_command,
        failure_next_command=failure_command,
        task_id=task_id,
        scope={
            "kind": "source_item",
            "item_id": item_id,
            "work_id": str(item.get("work_id") or ""),
            "bundle_sha256": str(item.get("bundle_sha256") or ""),
            "normalization_sha256": normalization_sha,
        },
        requires_human_apply=True,
        context_policy={
            "required_files": [
                agent_relative_path(item_dir, brief_file),
                agent_relative_path(item_dir, pack_file),
            ],
            "optional_files": [agent_relative_path(item_dir, path) for path in inputs[2:]],
            "compiled_brief": agent_relative_path(item_dir, brief_file),
            "forbidden_paths": [
                "原件清单.json",
                "来源说明.yaml",
                "../../",
                "50_workbench/agent_tasks/ (except the current manifest and event index)",
            ],
            "quality_focus": [review_variant or normalized_type],
        },
        media_inputs=source_media_assets,
        media_policy={
            "preference": "whole_preferred" if source_media_assets else "evidence_pack_only",
            "fallback": "evidence_pack",
            "capability_ref": "host_media_capability_v1",
        },
    )
    write_manifest(item_dir, manifest, agent_relative_path(item_dir, manifest_file))
    return {
        "schema": "source_agent_task_result_v1",
        "task_id": task_id,
        "task_type": normalized_type,
        "item_id": item_id,
        "manifest_file": str(manifest_file),
        "output_file": str(output_file),
        "segment_count": len(selected),
        "input_count": len(inputs),
        "review_type": review_variant,
        "non_canonical": True,
    }


@_source_library_mutation
def validate_source_agent_task(*, item_id: str, task_id: str) -> dict[str, Any]:
    """Validate one source-item Agent output and bind it to the immutable task."""

    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    try:
        manifest = load_manifest(item_dir, task_id)
    except ValueError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    if (manifest.get("scope") or {}).get("item_id") != item_id:
        raise FanfictionSourceError("source task belongs to another item")
    output_path = item_dir / str(manifest_output(manifest).get("path") or "")
    errors: list[str] = []
    payload: dict[str, Any] = {}
    try:
        parsed = parse_agent_output_files(item_dir, manifest, result_file=output_path)
    except AgentResultProtocolError as exc:
        errors.append(str(exc))
    else:
        payload = parsed.payload or {}
        errors.extend(_source_agent_payload_errors(item, manifest, payload))
    report = {
        "schema": "source_agent_task_validation_v1",
        "task_id": task_id,
        "item_id": item_id,
        "task_type": manifest.get("task_type", ""),
        "ok": not errors,
        "errors": errors,
        "result_path": agent_relative_path(item_dir, output_path),
        "result_sha256": sha256(output_path.read_bytes()).hexdigest() if output_path.is_file() else "",
        "source_schema": payload.get("schema", ""),
        "validated_at": utc_now(),
    }
    report_file = item_dir / "Agent工单验证" / f"{_safe_task_file_token(task_id)}.json"
    _write_json(report_file, report)
    update_task_status(
        item_dir,
        task_id,
        to_status="validated" if not errors else "invalid",
        command="source-library task-validate",
        artifact=report_file,
        result=output_path,
        current_result={
            "ok": not errors,
            "path": agent_relative_path(item_dir, output_path),
            "sha256": report["result_sha256"],
            "diagnostic_file": agent_relative_path(item_dir, report_file) if errors else "",
            "source_schema": str(report["source_schema"]),
            "validated_at": str(report["validated_at"]),
        },
    )
    return {**report, "report_file": str(report_file)}


@_source_library_mutation
def apply_source_agent_task(*, item_id: str, task_id: str, approved_by: str) -> dict[str, Any]:
    """Apply a validated source task only at the human-owned source-library boundary."""

    if approved_by != "human":
        raise FanfictionSourceError("source Agent task apply requires --approved-by human")
    validation = validate_source_agent_task(item_id=item_id, task_id=task_id)
    if validation.get("ok") is not True:
        raise FanfictionSourceError("source Agent task output is invalid: " + "; ".join(validation["errors"]))
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    manifest = load_manifest(item_dir, task_id)
    output_path = item_dir / str(manifest_output(manifest).get("path") or "")
    payload = _read_json(output_path, None)
    if not isinstance(payload, dict):
        raise FanfictionSourceError("validated source Agent output is unavailable")
    task_type = str(manifest.get("task_type") or "")
    payload = _hydrate_source_semantic_document(item, manifest, payload)
    if task_type == "source_fact_extraction":
        hydrated_path = item_dir / "Agent工单输出" / (
            _safe_task_file_token(str(manifest.get("task_id") or task_id)) + ".hydrated.json"
        )
        _write_json(hydrated_path, payload)
        applied = approve_source_extraction(item_id=item_id, file_path=hydrated_path, approved_by="human")
    elif task_type == "source_visual_observation":
        digest = sha256(output_path.read_bytes()).hexdigest()
        target = item_dir / "观察候选" / "版本" / f"{digest}.json"
        candidate = json.loads(json.dumps(payload, ensure_ascii=False))
        candidate["extensions"]["review_status"] = "independent_review_required"
        _write_json(target, candidate)
        applied = {
            "schema": SEMANTIC_DOCUMENT_SCHEMA,
            "candidate_sha256": digest,
            "candidate_file": str(target),
            "status": "independent_review_required",
            "evidence_available": False,
        }
    elif task_type == "source_evidence_review" and payload.get("extensions", {}).get("review_type") == "segment_evidence":
        applied = apply_source_evidence_review(
            item_id=item_id, file_path=output_path, approved_by="human"
        )
    elif task_type == "source_evidence_review" and payload.get("extensions", {}).get("review_type") == "visual_observation":
        applied = _apply_semantic_visual_observation_review(item, manifest, payload)
    elif task_type in {
        "source_version_conflict_review",
        "source_coverage_gap_analysis",
        "source_timeline_alignment",
        "source_conflict_analysis",
    }:
        approved_review = _approve_noncanonical_semantic_review(payload, item_id=item_id)
        digest = canonical_json_hash(approved_review)
        directory = {
            "source_version_conflict_review": "版本冲突审查",
            "source_coverage_gap_analysis": "覆盖缺口分析",
            "source_timeline_alignment": "时间线对齐",
            "source_conflict_analysis": "语义冲突分析",
        }[task_type]
        target = item_dir / directory / f"{digest}.json"
        _write_json(target, approved_review)
        applied = {
            "schema": SEMANTIC_DOCUMENT_SCHEMA,
            "review_file": str(target),
            "review_sha256": digest,
            "non_canonical": True,
            "project_materialized": False,
        }
    else:
        raise FanfictionSourceError(f"unsupported source Agent apply task: {task_type}")
    update_task_status(
        item_dir,
        task_id,
        to_status="applied",
        command="source-library task-apply --approved-by human",
        artifact=output_path,
        result=str(applied.get("candidate_file") or applied.get("review_file") or output_path),
    )
    return {"schema": "source_agent_task_apply_v1", "task_id": task_id, "task_type": task_type, **applied}


def _source_agent_payload_errors(
    item: dict[str, Any], manifest: dict[str, Any], payload: dict[str, Any]
) -> list[str]:
    payload = _hydrate_source_semantic_document(item, manifest, payload)
    try:
        payload = seal_semantic_document(payload)
    except ValueError as exc:
        return [str(exc)]
    task_type = str(manifest.get("task_type") or "")
    raw_scope = manifest.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    errors: list[str]
    if task_type == "source_fact_extraction":
        errors = validate_source_extraction(payload, item)
    else:
        errors = validate_semantic_document(payload)
    if errors:
        return errors
    raw_artifact = payload.get("artifact")
    artifact: dict[str, Any] = raw_artifact if isinstance(raw_artifact, dict) else {}
    raw_document_scope = artifact.get("scope")
    document_scope: dict[str, Any] = (
        raw_document_scope if isinstance(raw_document_scope, dict) else {}
    )
    raw_extensions = payload.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    expected_types = {
        "source_fact_extraction": "原著事实候选",
        "source_visual_observation": "视觉直接观察候选",
        "source_evidence_review": "原著证据复核",
        "source_version_conflict_review": "原著版本冲突分析",
        "source_coverage_gap_analysis": "原著资料覆盖分析",
        "source_timeline_alignment": "原著媒体时间线对齐",
        "source_conflict_analysis": "原著版本冲突分析",
    }
    if payload.get("document_type") != expected_types.get(task_type):
        errors.append(f"document_type must be {expected_types.get(task_type)}")
    if document_scope.get("item_id") != item.get("item_id"):
        errors.append("semantic document scope does not match task item_id")
    if extensions.get("task_type") != task_type:
        errors.append("extensions.task_type does not match the Agent task")
    if extensions.get("item_id") != item.get("item_id"):
        errors.append("extensions.item_id does not match task scope")
    if extensions.get("normalization_sha256") != scope.get("normalization_sha256"):
        errors.append("semantic document normalization basis is stale")
    pack_segments = _source_task_declared_segments(_resolve_library_item_directory(item), manifest)
    declared_segments = {
        str(segment.get("segment_id") or ""): segment for segment in pack_segments
    }
    declared_media_ids = {
        str(record.get("asset_id") or "")
        for record in manifest_input_records(manifest)
        if record.get("kind") == "media_asset"
    }
    for reference in payload.get("evidence_references") or []:
        if not isinstance(reference, dict):
            continue
        segment_id = str(reference.get("segment_id") or "")
        asset_id = str(reference.get("asset_id") or "")
        if segment_id:
            segment = declared_segments.get(segment_id)
            if segment is None:
                errors.append("semantic document references an undeclared source segment")
                continue
            expected_locator = segment.get("origin_locator") or {}
            if asset_id != str(expected_locator.get("asset_id") or ""):
                errors.append("evidence reference asset_id does not match its segment")
            if canonical_json_hash(reference.get("locator") or {}) != canonical_json_hash(expected_locator):
                errors.append("evidence reference locator does not match its segment")
            if str(reference.get("excerpt") or "") not in str(segment.get("normalized_text") or ""):
                errors.append("evidence reference excerpt is outside its segment")
        elif asset_id not in declared_media_ids:
            errors.append("whole-media evidence references an undeclared media asset")
    if task_type == "source_visual_observation":
        for claim in payload.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            raw_claim_extensions = claim.get("extensions")
            claim_extensions: dict[str, Any] = (
                raw_claim_extensions if isinstance(raw_claim_extensions, dict) else {}
            )
            observation_kind = claim_extensions.get("observation_kind")
            if observation_kind not in {"direct_observation", "interpretation_candidate"}:
                errors.append("visual claims must declare direct_observation or interpretation_candidate")
            if observation_kind == "interpretation_candidate" and not str(claim.get("uncertainty") or "").strip():
                errors.append("visual interpretation candidates must state uncertainty")
    if task_type in {
        "source_evidence_review",
        "source_version_conflict_review",
        "source_coverage_gap_analysis",
        "source_timeline_alignment",
        "source_conflict_analysis",
    } and not str(payload.get("body") or "").strip():
        errors.append("semantic review body must not be empty")
    return errors


def _source_task_declared_segments(item_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    for path_text in manifest_input_paths(manifest):
        payload = _read_json(item_dir / path_text, None)
        if isinstance(payload, dict) and payload.get("schema") == "source_agent_evidence_pack_v1":
            values = payload.get("segments")
            return [value for value in values or [] if isinstance(value, dict)]
    return []


def _source_task_observation_candidate(item_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    for path_text in manifest_input_paths(manifest):
        payload = _read_json(item_dir / path_text, None)
        if (
            isinstance(payload, dict)
            and payload.get("schema") == SEMANTIC_DOCUMENT_SCHEMA
            and payload.get("document_type") == "视觉直接观察候选"
        ):
            return payload
    return {}


def _source_task_media_inputs(
    item: dict[str, Any], segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Declare whole media when possible while retaining precise locator fallbacks."""

    locators_by_asset: dict[str, list[dict[str, Any]]] = {}
    for segment in segments:
        locator = segment.get("origin_locator")
        if not isinstance(locator, dict):
            continue
        asset_id = str(locator.get("asset_id") or "")
        if asset_id:
            locators_by_asset.setdefault(asset_id, []).append(dict(locator))
    media_formats = {"jpg", "jpeg", "png", "webp", "mp3", "wav", "mp4", "mkv", "pdf"}
    values: list[dict[str, Any]] = []
    for asset in item.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        asset_id = str(asset.get("asset_id") or "")
        detected = str(asset.get("detected_format") or "").casefold()
        if detected not in media_formats:
            continue
        if locators_by_asset and asset_id not in locators_by_asset:
            continue
        try:
            path = resolve_asset_path(source_library_root(), asset)
        except SourceProcessingError as exc:
            raise FanfictionSourceError(str(exc)) from exc
        values.append(
            {
                "asset_id": asset_id,
                "path": str(path),
                "requirement": "required",
                "sha256": str(asset.get("sha256") or ""),
                "bytes": int(asset.get("size_bytes") or 0),
                "media_type": str(asset.get("media_type") or "application/octet-stream"),
                "access": "whole_preferred",
                "ranges": locators_by_asset.get(asset_id, []),
                "reason": "source_media_evidence",
            }
        )
    return values


def _approve_noncanonical_semantic_review(
    payload: dict[str, Any], *, item_id: str
) -> dict[str, Any]:
    payload = json.loads(json.dumps(payload, ensure_ascii=False))
    raw_extensions = payload.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    extensions["non_canonical"] = True
    payload["extensions"] = extensions
    payload = seal_semantic_document(payload)
    raw_envelope = payload.get("artifact")
    envelope: dict[str, Any] = raw_envelope if isinstance(raw_envelope, dict) else {}
    decision = build_human_decision(
        decision_id="decision_" + canonical_json_hash(
            {"target": envelope.get("artifact_id"), "hash": envelope.get("content_sha256"), "item": item_id}
        )[:24],
        target_id=str(envelope.get("artifact_id") or ""),
        target_sha256=str(envelope.get("content_sha256") or ""),
        decision="approve",
        decided_by="human",
        reason="人工批准该非 Canon 语义复核结果。",
        scope={"kind": "source_item", "item_id": item_id},
    )
    return approved_semantic_document(payload, decision=decision)


def _hydrate_source_semantic_document(
    item: dict[str, Any], manifest: dict[str, Any], payload: dict[str, Any]
) -> dict[str, Any]:
    """Attach source hashes owned by the CLI without trusting Agent copies."""

    hydrated = json.loads(json.dumps(payload, ensure_ascii=False))
    extensions = hydrated.get("extensions")
    if not isinstance(extensions, dict):
        return hydrated
    raw_scope = manifest.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    extensions.update(
        {
            "task_type": str(manifest.get("task_type") or ""),
            "item_id": str(item.get("item_id") or ""),
            "bundle_sha256": str(item.get("bundle_sha256") or ""),
            "normalization_sha256": str(scope.get("normalization_sha256") or ""),
        }
    )
    return hydrated


def _apply_semantic_visual_observation_review(
    item: dict[str, Any], manifest: dict[str, Any], review: dict[str, Any]
) -> dict[str, Any]:
    item_dir = _resolve_library_item_directory(item)
    candidate = _source_task_observation_candidate(item_dir, manifest)
    if not candidate:
        raise FanfictionSourceError("visual review task has no declared semantic observation candidate")
    decisions = review.get("extensions", {}).get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise FanfictionSourceError("visual semantic review requires extensions.decisions")
    claim_ids = {
        str(value.get("claim_id") or "")
        for value in candidate.get("claims") or []
        if isinstance(value, dict)
    }
    decided_ids = {
        str(value.get("claim_id") or "")
        for value in decisions
        if isinstance(value, dict)
    }
    if not decided_ids or not decided_ids <= claim_ids:
        raise FanfictionSourceError("visual semantic review decisions reference undeclared claims")
    approved_review = _approve_noncanonical_semantic_review(
        review, item_id=str(item.get("item_id") or "")
    )
    digest = canonical_json_hash(approved_review)
    target = item_dir / "证据审查" / f"visual-{digest}.json"
    _write_json(target, approved_review)
    return {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "review_file": str(target),
        "review_sha256": digest,
        "approved_claims": sum(
            value.get("decision") == "approve" for value in decisions if isinstance(value, dict)
        ),
        "non_canonical": True,
        "evidence_available": True,
    }


def _safe_task_file_token(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(task_id).replace(":", "_"))


@_source_library_mutation
def create_source_remote_decision(
    *, item_id: str, job_id: str, provider: str, model: str, scopes: list[dict[str, Any]]
) -> dict[str, Any]:
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    job = _read_json(item_dir / "处理任务" / f"{job_id}.json", None)
    if not isinstance(job, dict) or job.get("schema") != PROCESSING_JOB_SCHEMA:
        raise FanfictionSourceError(f"unknown or invalid source processing job: {job_id}")
    try:
        decision = create_remote_decision_payload(
            job=job, item=item, provider=provider, model=model, scopes=scopes
        )
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    path = item_dir / "云端处理决定" / f"{job_id}.json"
    _write_json(path, decision)
    return {**decision, "decision_file": str(path)}


@_source_library_mutation
def approve_source_remote_decision(*, item_id: str, job_id: str, approved_by: str) -> dict[str, Any]:
    item = library_item(item_id)
    path = _resolve_library_item_directory(item) / "云端处理决定" / f"{job_id}.json"
    decision = _read_json(path, None)
    if not isinstance(decision, dict):
        raise FanfictionSourceError(f"unknown remote processing decision: {job_id}")
    try:
        approved = approve_remote_decision(decision, approved_by=approved_by)
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    approved["approved_at"] = utc_now()
    _write_json(path, approved)
    return {**approved, "decision_file": str(path)}


@_source_library_mutation
def run_source_remote_processing(
    config: ConfigDocument,
    *,
    item_id: str,
    job_id: str,
) -> dict[str, Any]:
    cloud = config.data.get("source_processing", {}).get("cloud", {})
    if not isinstance(cloud, dict) or cloud.get("enabled") is not True:
        raise FanfictionSourceError("OpenAI source processing is disabled in project configuration")
    if cloud.get("provider") != "openai" or cloud.get("allow_automatic_fallback") is not False:
        raise FanfictionSourceError("source cloud configuration must explicitly select openai without fallback")
    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    job = _read_json(item_dir / "处理任务" / f"{job_id}.json", None)
    decision = _read_json(item_dir / "云端处理决定" / f"{job_id}.json", None)
    if not isinstance(job, dict) or not isinstance(decision, dict):
        raise FanfictionSourceError("remote processing job or decision is unavailable")
    job_path = item_dir / "处理任务" / f"{job_id}.json"
    configured_models = {
        str(cloud.get("vision_model") or ""), str(cloud.get("transcription_model") or "")
    }
    if str(decision.get("model") or "") not in configured_models - {""}:
        raise FanfictionSourceError("approved remote processing model is not configured for this project")
    try:
        result = run_openai_processing(
            library_root=source_library_root(),
            item_dir=item_dir,
            item=item,
            job=job,
            decision=decision,
        )
    except SourceProcessingError as exc:
        job["status"] = "failed"
        job["diagnostic"] = str(exc)
        job["network_performed"] = False
        _write_json(job_path, job)
        raise FanfictionSourceError(str(exc)) from exc
    job["status"] = result["status"]
    job["normalization_sha256"] = result["normalization_sha256"]
    job["network_performed"] = True
    _write_json(job_path, job)
    _update_library_item_processing(
        item,
        normalization_sha256=result["normalization_sha256"],
        processing_status=result["status"],
    )
    return result


@_source_library_mutation
def approve_source_extraction(*, item_id: str, file_path: str | Path, approved_by: str) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("source extraction approval requires --approved-by human")
    candidate_path = Path(file_path).expanduser().resolve()
    candidate = _read_json(candidate_path, None)
    if not isinstance(candidate, dict):
        raise FanfictionSourceError("source extraction candidate must be a JSON object")
    item = library_item(item_id)
    if item.get("processing_status") not in {"evidence_ready", "extraction_approved"}:
        raise FanfictionSourceError(
            "source extraction requires evidence_ready; resolve partial parsing and evidence review first"
        )
    errors = validate_source_extraction(candidate, item)
    if errors:
        raise FanfictionSourceError("invalid source extraction: " + "; ".join(errors))
    candidate = seal_semantic_document(candidate)
    envelope = candidate["artifact"]
    decision = build_human_decision(
        decision_id="decision_" + canonical_json_hash(
            {"target": envelope["artifact_id"], "hash": envelope["content_sha256"], "item": item_id}
        )[:24],
        target_id=str(envelope["artifact_id"]),
        target_sha256=str(envelope["content_sha256"]),
        decision="approve",
        decided_by="human",
        reason="人工批准该资料项的证据绑定语义候选。",
        scope={"kind": "source_item", "item_id": item_id},
    )
    canonical = approved_semantic_document(candidate, decision=decision)
    extraction_bytes = (json.dumps(canonical, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    extraction_sha = sha256(extraction_bytes).hexdigest()
    root = source_library_root()
    item_dir = root / str(item["path"])
    version_dir = item_dir / "提取版本"
    version_dir.mkdir(parents=True, exist_ok=True)
    version_file = version_dir / f"{extraction_sha}.json"
    if not version_file.exists():
        version_file.write_bytes(extraction_bytes)
    atomic_write_text(item_dir / "提取结果.json", extraction_bytes.decode("utf-8"))
    evidence_index = {
        "schema": "source_evidence_index_v2",
        "item_id": item_id,
        "bundle_sha256": item["bundle_sha256"],
        "normalization_sha256": item["normalization_sha256"],
        "extraction_sha256": extraction_sha,
        "items": [dict(value) for value in canonical["evidence_references"]],
    }
    _write_json(item_dir / "证据索引.json", evidence_index)
    index = _library_index(create=False)
    for record in index["items"]:
        if isinstance(record, dict) and record.get("item_id") == item_id:
            record["extraction_sha256"] = extraction_sha
            record["extraction_path"] = version_file.relative_to(root).as_posix()
    _write_library_index(index)
    _update_library_item_processing(
        item,
        normalization_sha256=str(item["normalization_sha256"]),
        processing_status="extraction_approved",
        extraction_sha256=extraction_sha,
    )
    return {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "item_id": item_id,
        "bundle_sha256": item["bundle_sha256"],
        "normalization_sha256": item["normalization_sha256"],
        "extraction_sha256": extraction_sha,
        "extraction_file": str(version_file),
        "evidence_count": len(canonical["evidence_references"]),
        "claim_count": len(canonical["claims"]),
        "decision_schema": HUMAN_DECISION_SCHEMA,
    }


@_source_library_mutation
def create_source_extraction_template(*, item_id: str) -> dict[str, Any]:
    """Create a non-Canon extraction candidate without overwriting author work."""

    item = library_item(item_id)
    if item.get("processing_status") not in {"evidence_ready", "extraction_approved"}:
        raise FanfictionSourceError(
            "source extraction requires evidence_ready; process and review the source item first"
        )
    item_dir = _resolve_library_item_directory(item)
    target = item_dir / "提取候选.json"
    created = not target.exists()
    if created:
        _write_json(
            target,
            build_semantic_document(
                document_id=f"sem_source_{item_id[5:]}",
                document_type="原著事实候选",
                title=f"{item.get('name', item_id)}语义提取",
                scope={
                    "kind": "source_item",
                    "item_id": item_id,
                    "work_id": str(item.get("work_id") or ""),
                },
                continuity="原著基线",
                body="",
                extensions={
                    "task_type": "source_fact_extraction",
                    "item_id": item_id,
                    "bundle_sha256": item["bundle_sha256"],
                    "normalization_sha256": item["normalization_sha256"],
                },
                input_hashes=[item["bundle_sha256"], item["normalization_sha256"]],
            ),
        )
    return {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "item_id": item_id,
        "bundle_sha256": item["bundle_sha256"],
        "normalization_sha256": item["normalization_sha256"],
        "candidate_file": str(target),
        "segment_file": str(item_dir / "规范化" / "证据分段.jsonl"),
        "retention_mode": item.get("retention_mode"),
        "created": created,
        "non_canonical": True,
    }


def validate_source_extraction(candidate: dict[str, Any], item: dict[str, Any]) -> list[str]:
    try:
        candidate = seal_semantic_document(candidate)
    except ValueError as exc:
        return [str(exc)]
    errors = validate_semantic_document(candidate)
    if errors:
        return errors
    artifact = candidate["artifact"]
    raw_scope = artifact.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    raw_extensions = candidate.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    if candidate.get("document_type") != "原著事实候选":
        errors.append("document_type must be 原著事实候选")
    if scope.get("kind") != "source_item" or scope.get("item_id") != item.get("item_id"):
        errors.append("semantic document scope does not match the registered source item")
    if extensions.get("task_type") != "source_fact_extraction":
        errors.append("extensions.task_type must be source_fact_extraction")
    if extensions.get("item_id") != item.get("item_id"):
        errors.append("extensions.item_id does not match the registered source item")
    if extensions.get("bundle_sha256") != item.get("bundle_sha256"):
        errors.append("extensions.bundle_sha256 does not match the registered source item")
    if extensions.get("normalization_sha256") != item.get("normalization_sha256"):
        errors.append("extensions.normalization_sha256 does not match current normalized evidence")
    try:
        segments = load_normalized_segments(
            _resolve_library_item_directory(item),
            expected_sha256=str(item.get("normalization_sha256") or ""),
        )
    except SourceProcessingError as exc:
        errors.append(str(exc))
        segments = []
    segments_by_id = {str(segment["segment_id"]): segment for segment in segments}
    evidence = candidate.get("evidence_references")
    evidence_ids: set[str] = set()
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence_references must be a non-empty list")
        evidence = []
    for index, record in enumerate(evidence):
        prefix = f"evidence_references[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix} must be an object")
            continue
        evidence_id = str(record.get("evidence_id") or "")
        if not _stable_id(evidence_id) or evidence_id in evidence_ids:
            errors.append(f"{prefix}.evidence_id must be stable and unique")
        if not evidence_id.startswith(f"{item.get('item_id')}:"):
            errors.append(f"{prefix}.evidence_id must use the source item namespace")
        evidence_ids.add(evidence_id)
        segment_id = str(record.get("segment_id") or "")
        segment = segments_by_id.get(segment_id)
        if segment is None:
            errors.append(f"{prefix}.segment_id is not registered for this normalization")
            continue
        if segment.get("review_status") not in {"source_exact", "approved"}:
            errors.append(f"{prefix}.segment_id has not passed evidence review")
        if record.get("item_id") != item.get("item_id"):
            errors.append(f"{prefix}.item_id does not match the source item")
        raw_locator = segment.get("origin_locator")
        locator: dict[str, Any] = raw_locator if isinstance(raw_locator, dict) else {}
        if record.get("asset_id") != locator.get("asset_id"):
            errors.append(f"{prefix}.asset_id does not match the source segment")
        if canonical_json_hash(record.get("locator") or {}) != canonical_json_hash(locator):
            errors.append(f"{prefix}.locator does not match the normalized source segment")
        text = str(segment.get("normalized_text") or "")
        if str(record.get("excerpt") or "") not in text:
            errors.append(f"{prefix}.excerpt is not a substring of the source segment")
        if len(str(record.get("excerpt") or "")) > 400:
            errors.append(f"{prefix}.excerpt exceeds the 400-character evidence limit")
    claims = candidate.get("claims")
    if not claims:
        errors.append("claims must contain at least one evidence-backed semantic assertion")
    for index, claim in enumerate(claims or []):
        if not isinstance(claim, dict):
            continue
        if not str(claim.get("claim_id") or "").startswith(f"{item.get('item_id')}:"):
            errors.append(f"claims[{index}].claim_id must use the source item namespace")
    return errors


def initialize_project_source_packs(
    config: ConfigDocument,
    *,
    source_id: str | None = None,
) -> dict[str, Any]:
    _require_fanfiction_mode(config)
    root = resolve_project_root(config)
    sources = _configured_sources(config)
    if source_id and source_id not in sources:
        raise FanfictionSourceError(f"unknown configured fanfiction source_id: {source_id}")
    selected = [sources[source_id]] if source_id else list(sources.values())
    created: list[str] = []
    existing: list[str] = []
    for source in selected:
        directory = project_source_pack_dir(root, str(source["source_id"]), create=False, config=config)
        setting_path = directory / "作品资料设定.yaml"
        if setting_path.is_file():
            existing.append(directory.relative_to(root).as_posix())
            continue
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "资料项").mkdir(exist_ok=True)
        global_work = _find_library_work(str(source["title"]), str(source["creator"]))
        setting = {
            "协议版本": PROJECT_WORK_SCHEMA,
            "资料源ID": str(source["source_id"]),
            "作品ID": str(global_work.get("work_id") or "") if global_work else "",
            "作品名称": str(source["title"]),
            "原作者": str(source["creator"]),
            "创作连续性": str(config.data["fanfiction"]["continuity_mode"]),
            "Canon截止点": str(source["canon_cutoff"]),
            "覆盖模式": "分层按需",
            "创建时间": utc_now(),
        }
        _write_yaml(setting_path, setting)
        _write_yaml(
            directory / "资料绑定.yaml",
            _binding_to_chinese(
                {
                    "schema": PROJECT_BINDING_SCHEMA,
                    "source_id": str(source["source_id"]),
                    "work_id": setting["作品ID"],
                    "items": [],
                    "updated_at": utc_now(),
                }
            ),
        )
        _write_yaml(
            directory / "全作覆盖计划.yaml",
            _coverage_to_chinese(_empty_coverage_plan(source, work_id=str(setting["作品ID"]))),
        )
        atomic_write_text(directory / "版本冲突清单.md", "# 版本冲突清单\n\n当前没有已批准的版本冲突处理。\n")
        _write_coverage_report(config, str(source["source_id"]))
        created.append(directory.relative_to(root).as_posix())
    return {
        "schema": "fanfiction_source_pack_init_v1",
        "created": created,
        "existing": existing,
        "next_command": "longform-engine fanfiction coverage-apply project.yaml --source-id SOURCE --file PLAN --approved-by human",
    }


def apply_coverage_plan(
    config: ConfigDocument,
    *,
    source_id: str,
    file_path: str | Path,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("coverage plan apply requires --approved-by human")
    source = _configured_source(config, source_id)
    candidate = _read_yaml_or_json(Path(file_path).expanduser().resolve())
    if not isinstance(candidate, dict):
        raise FanfictionSourceError("coverage plan must be a YAML or JSON object")
    normalized = _normalize_coverage_plan(candidate, source)
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    if not pack.is_dir():
        raise FanfictionSourceError("create the project source pack before applying a coverage plan")
    setting = _read_yaml(pack / "作品资料设定.yaml", {})
    setting_work_id = str(setting.get("作品ID") or "") if isinstance(setting, dict) else ""
    raw_scope = normalized.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    if scope.get("work_id") and setting_work_id and scope["work_id"] != setting_work_id:
        raise FanfictionSourceError("coverage plan work_id does not match the project source pack")
    scope["work_id"] = scope.get("work_id") or setting_work_id
    normalized["scope"] = scope
    errors = validate_coverage_plan(normalized, source=source, require_complete=False)
    if errors:
        raise FanfictionSourceError("invalid coverage plan: " + "; ".join(errors))
    normalized["state"] = "approved"
    normalized["authorization"] = {
        "approved_by": "human",
        "approved_at": utc_now(),
        "scope": "coverage_requirements_and_mode",
    }
    normalized["updated_at"] = normalized["authorization"]["approved_at"]
    _write_yaml(pack / "全作覆盖计划.yaml", _coverage_to_chinese(normalized))
    _write_coverage_report(config, source_id)
    gaps = coverage_gaps(config, source_id=source_id)
    return {
        "schema": COVERAGE_SCHEMA,
        "source_id": source_id,
        "plan_file": (pack / "全作覆盖计划.yaml").relative_to(root).as_posix(),
        "requirement_count": len(normalized["extensions"]["requirements"]),
        "complete": gaps["complete"],
        "gaps": gaps["gaps"],
    }


def bind_library_item(
    config: ConfigDocument,
    *,
    source_id: str,
    item_id: str,
    approved_by: str,
    upgrade_authorization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("project source binding requires --approved-by human")
    source = _configured_source(config, source_id)
    item = library_item(item_id)
    work = library_work(str(item["work_id"]))
    if str(work.get("name") or "").casefold() != str(source["title"]).casefold() or str(
        work.get("creator") or ""
    ).casefold() != str(source["creator"]).casefold():
        raise FanfictionSourceError("library item work does not match the configured project source")
    if not str(item.get("extraction_sha256") or ""):
        raise FanfictionSourceError("source item must have an approved extraction candidate before binding")
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    if not pack.is_dir():
        raise FanfictionSourceError("create the project source pack before binding source items")
    setting_path = pack / "作品资料设定.yaml"
    setting = _read_yaml(setting_path, {})
    existing_work_id = str(setting.get("作品ID") or "") if isinstance(setting, dict) else ""
    if existing_work_id and existing_work_id != item["work_id"]:
        raise FanfictionSourceError("project source pack is already bound to another global work")
    setting["作品ID"] = item["work_id"]
    _write_yaml(setting_path, setting)

    binding_path = pack / "资料绑定.yaml"
    binding = _read_project_binding(binding_path)
    if not isinstance(binding, dict) or binding.get("schema") != PROJECT_BINDING_SCHEMA:
        raise FanfictionSourceError("project source binding file is invalid")
    binding["work_id"] = item["work_id"]
    entries = [entry for entry in binding.get("items") or [] if isinstance(entry, dict)]
    old = next((entry for entry in entries if entry.get("item_id") == item_id), None)
    entry = {
        "item_id": item_id,
        "bundle_sha256": item["bundle_sha256"],
        "normalization_sha256": item["normalization_sha256"],
        "extraction_sha256": item["extraction_sha256"],
        "library_uri": f"source-library://{item['work_id']}/{item_id}",
        "project_item_name": str(item["name"]),
        "approved_by": "human",
        "bound_at": utc_now(),
    }
    binding_changes = old is None or any(
        old.get(field) != entry.get(field)
        for field in (
            "bundle_sha256",
            "normalization_sha256",
            "extraction_sha256",
            "library_uri",
        )
    )
    applied_canon = root / "10_bible" / "fanfiction" / "source_canon.json"
    if binding_changes and applied_canon.is_file() and upgrade_authorization is None:
        raise FanfictionSourceError(
            "an applied project Canon cannot be rebound directly; use an approved source upgrade proposal, "
            "independent semantic review, and human upgrade decision"
        )
    if upgrade_authorization is not None and (
        upgrade_authorization.get("schema") != SOURCE_UPGRADE_PROPOSAL_SCHEMA
        or upgrade_authorization.get("source_id") != source_id
        or upgrade_authorization.get("change", {}).get("to_item_id") != item_id
    ):
        raise FanfictionSourceError("source upgrade authorization does not match this binding change")
    if old is None:
        entries.append(entry)
    else:
        entries[entries.index(old)] = entry
    binding["items"] = entries
    binding["updated_at"] = utc_now()
    _write_yaml(binding_path, _binding_to_chinese(binding))

    project_item_dir = _unique_project_item_directory(pack / "资料项", str(item["name"]), item_id)
    project_item_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        project_item_dir / "来源绑定.yaml",
        {
            "协议版本": PROJECT_BINDING_SCHEMA,
            "资料源ID": source_id,
            "作品ID": item["work_id"],
            "资料项ID": item_id,
            "组合哈希": entry["bundle_sha256"],
            "规范化哈希": entry["normalization_sha256"],
            "提取哈希": entry["extraction_sha256"],
            "资料库引用": entry["library_uri"],
            "项目资料名称": entry["project_item_name"],
            "人工批准": "human",
            "绑定时间": entry["bound_at"],
        },
    )
    library_item_dir = source_library_root() / str(item["path"])
    shutil.copy2(library_item_dir / "提取结果.json", project_item_dir / "提取结果.json")
    shutil.copy2(library_item_dir / "证据索引.json", project_item_dir / "证据索引.json")
    if binding_changes and applied_canon.is_file():
        _mark_source_binding_dependents_stale(
            root,
            source_id=source_id,
            item_id=item_id,
            artifact_paths=list((upgrade_authorization or {}).get("must_stale_artifacts") or []),
        )
    _write_coverage_report(config, source_id)
    return {
        "schema": PROJECT_BINDING_SCHEMA,
        "source_id": source_id,
        "work_id": item["work_id"],
        "item_id": item_id,
        "bundle_sha256": item["bundle_sha256"],
        "normalization_sha256": item["normalization_sha256"],
        "extraction_sha256": item["extraction_sha256"],
        "project_item_dir": project_item_dir.relative_to(root).as_posix(),
    }


def coverage_gaps(
    config: ConfigDocument,
    *,
    source_id: str | None = None,
    gate: str = "design_core",
    chapter_number: int | None = None,
) -> dict[str, Any]:
    """Report only the semantic coverage needed by the requested production gate."""

    if gate not in {"identity", "design_core", "volume_scope", "chapter_dependency", "all"}:
        raise FanfictionSourceError("unknown fanfiction coverage gate")
    sources = _configured_sources(config)
    selected = [source_id] if source_id else list(sources)
    work_results: list[dict[str, Any]] = []
    all_gaps: list[str] = []
    for selected_id in selected:
        source = _configured_source(config, selected_id)
        root = resolve_project_root(config)
        pack = project_source_pack_dir(root, selected_id, create=False, config=config)
        gaps: list[str] = []
        plan = _read_coverage_plan(pack / "全作覆盖计划.yaml", source)
        binding = _read_project_binding(pack / "资料绑定.yaml")
        if not pack.is_dir():
            gaps.append("作品资料包尚未创建")
        elif not isinstance(binding, dict) or binding.get("schema") != PROJECT_BINDING_SCHEMA:
            gaps.append("资料绑定合同缺失或无效")
        else:
            gaps.extend(_binding_errors(binding, require_available=True))
        if not isinstance(plan, dict):
            gaps.append("全作覆盖计划缺失")
        else:
            gaps.extend(validate_coverage_plan(plan, source=source, require_complete=True, binding=binding))
            if not gaps:
                gaps.extend(
                    _coverage_requirement_gaps(
                        plan,
                        gate=gate,
                        chapter_number=chapter_number,
                    )
                )
        if gate in {"design_core", "volume_scope", "chapter_dependency", "all"}:
            gaps.extend(_version_conflict_gaps(pack, selected_id))
        if gate in {"chapter_dependency", "all"}:
            gaps.extend(_incremental_request_gaps(pack, chapter_number=chapter_number))
        all_gaps.extend(f"{selected_id}: {gap}" for gap in gaps)
        work_results.append(
            {
                "source_id": selected_id,
                "title": source["title"],
                "complete": not gaps,
                "gaps": gaps,
            }
        )
    return {
        "schema": "fanfiction_coverage_status_v1",
        "gate": gate,
        "chapter_number": chapter_number,
        "complete": not all_gaps,
        "works": work_results,
        "gaps": all_gaps,
    }


def fanfiction_source_readiness(
    config: ConfigDocument,
    *,
    gate: str = "design_core",
    chapter_number: int | None = None,
) -> dict[str, Any]:
    if str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        return {"ready": True, "errors": [], "next_command": ""}
    status = coverage_gaps(config, gate=gate, chapter_number=chapter_number)
    next_command = "longform-engine fanfiction canon-task project.yaml"
    if status["gaps"]:
        if any("资料包尚未创建" in gap for gap in status["gaps"]):
            next_command = "longform-engine fanfiction pack-init project.yaml"
        else:
            next_command = "longform-engine fanfiction coverage-gaps project.yaml --json"
    return {
        "ready": status["complete"],
        "gate": gate,
        "chapter_number": chapter_number,
        "errors": status["gaps"],
        "next_command": next_command,
    }


def apply_version_conflict_decisions(
    config: ConfigDocument,
    *,
    source_id: str,
    file_path: str | Path,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("version conflict decisions require --approved-by human")
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    payload = _read_yaml_or_json(Path(file_path).expanduser().resolve())
    if not isinstance(payload, dict):
        raise FanfictionSourceError("version conflict decision must be a YAML or JSON object")
    normalized = _normalize_version_conflict_decisions(payload, source_id=source_id)
    expected_ids = _version_conflict_ids(pack)
    errors = _validate_version_conflict_decisions(
        normalized,
        expected_ids=expected_ids,
        require_approval=False,
    )
    if normalized.get("source_id") != source_id:
        errors.append("source_id 与目标资料包不一致")
    if errors:
        raise FanfictionSourceError("invalid version conflict decision: " + "; ".join(errors))
    normalized["approved_by"] = "human"
    normalized["approved_at"] = utc_now()
    _write_yaml(pack / "版本冲突决定.yaml", _version_conflict_decisions_to_chinese(normalized))
    _write_version_conflict_report(pack, normalized)
    _write_coverage_report(config, source_id)
    return {
        "schema": VERSION_CONFLICT_DECISION_SCHEMA,
        "source_id": source_id,
        "decision_count": len(normalized["decisions"]),
        "complete": not _version_conflict_gaps(pack, source_id),
        "decision_file": (pack / "版本冲突决定.yaml").relative_to(root).as_posix(),
    }


def create_incremental_source_request(
    config: ConfigDocument,
    *,
    source_id: str,
    need: str,
    reason: str,
    chapter_number: int | None = None,
) -> dict[str, Any]:
    _require_fanfiction_mode(config)
    _configured_source(config, source_id)
    if chapter_number is not None and chapter_number <= 0:
        raise FanfictionSourceError("chapter_number must be positive")
    need = str(need).strip()
    reason = str(reason).strip()
    if not need or not reason:
        raise FanfictionSourceError("incremental source need and reason are required")
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    request_id = "source_gap_" + sha256(
        f"{source_id}\0{chapter_number or 0}\0{need}\0{reason}".encode("utf-8")
    ).hexdigest()[:16]
    path = pack / "待审资料需求" / f"{request_id}.json"
    payload = {
        "schema": INCREMENTAL_REQUEST_SCHEMA,
        "request_id": request_id,
        "source_id": source_id,
        "chapter_number": chapter_number,
        "need": need,
        "reason": reason,
        "status": "pending_human_approval",
        "network_performed": False,
        "created_at": utc_now(),
    }
    _write_json(path, payload)
    _write_coverage_report(config, source_id)
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def approve_incremental_source_request(
    config: ConfigDocument,
    *,
    request_id: str,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("incremental source requests require --approved-by human")
    root = resolve_project_root(config)
    path, payload = _incremental_request(root, request_id)
    if payload.get("status") not in {"pending_human_approval", "approved_for_search"}:
        raise FanfictionSourceError("incremental source request is not awaiting approval")
    payload["status"] = "approved_for_search"
    payload["approved_by"] = "human"
    payload["approved_at"] = utc_now()
    _write_json(path, payload)
    _write_coverage_report(config, str(payload["source_id"]))
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def resolve_incremental_source_request(
    config: ConfigDocument,
    *,
    request_id: str,
    item_id: str,
    reason: str,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("incremental source resolution requires --approved-by human")
    reason = str(reason).strip()
    if not reason:
        raise FanfictionSourceError("incremental source resolution reason is required")
    root = resolve_project_root(config)
    path, payload = _incremental_request(root, request_id)
    if payload.get("status") not in {"approved_for_search", "candidates_recorded"}:
        raise FanfictionSourceError("incremental source request is not ready for resolution")
    contract = project_source_contract(config, str(payload["source_id"]))
    bound_ids = {
        str(item.get("item_id") or "")
        for item in contract["binding"].get("items") or []
        if isinstance(item, dict)
    }
    if item_id not in bound_ids:
        raise FanfictionSourceError("incremental source resolution must cite a currently bound item")
    payload["status"] = "resolved"
    payload["resolved_item_id"] = item_id
    payload["resolution_reason"] = reason
    payload["resolved_by"] = "human"
    payload["resolved_at"] = utc_now()
    _write_json(path, payload)
    _write_coverage_report(config, str(payload["source_id"]))
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def fanfiction_canon_input_files(config: ConfigDocument) -> list[Path]:
    readiness = fanfiction_source_readiness(config)
    if not readiness["ready"]:
        raise FanfictionSourceError("fanfiction source coverage is incomplete: " + "; ".join(readiness["errors"]))
    root = resolve_project_root(config)
    inputs: list[Path] = []
    for source_id in _configured_sources(config):
        pack = project_source_pack_dir(root, source_id, create=False, config=config)
        inputs.extend((pack / "作品资料设定.yaml", pack / "资料绑定.yaml", pack / "全作覆盖计划.yaml"))
        for item_dir in (pack / "资料项").iterdir():
            if not item_dir.is_dir():
                continue
            for filename in ("来源绑定.yaml", "提取结果.json", "证据索引.json"):
                path = item_dir / filename
                if path.is_file():
                    inputs.append(path)
    return inputs


def project_source_contract(config: ConfigDocument, source_id: str) -> dict[str, Any]:
    root = resolve_project_root(config)
    source = _configured_source(config, source_id)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    setting = _read_yaml(pack / "作品资料设定.yaml", {})
    binding = _read_project_binding(pack / "资料绑定.yaml")
    plan = _read_coverage_plan(pack / "全作覆盖计划.yaml", source)
    evidence: dict[str, dict[str, Any]] = {}
    for item_dir in (pack / "资料项").iterdir() if (pack / "资料项").is_dir() else ():
        index = _read_json(item_dir / "证据索引.json", {})
        for record in index.get("items") or [] if isinstance(index, dict) else []:
            if not isinstance(record, dict):
                continue
            evidence_id = str(record.get("evidence_id") or "")
            if evidence_id:
                evidence[evidence_id] = {
                    **record,
                    "item_id": str(index.get("item_id") or ""),
                    "bundle_sha256": str(index.get("bundle_sha256") or ""),
                    "normalization_sha256": str(index.get("normalization_sha256") or ""),
                }
    return {
        "source": source,
        "setting": setting,
        "binding": binding,
        "coverage": plan,
        "evidence": evidence,
        "binding_sha256": _file_hash(pack / "资料绑定.yaml"),
        "coverage_sha256": _file_hash(pack / "全作覆盖计划.yaml"),
    }


def source_upgrade_status(
    config: ConfigDocument,
    *,
    source_id: str | None = None,
) -> dict[str, Any]:
    """Report newer library candidates without changing pinned project bindings."""

    _require_fanfiction_mode(config)
    source_ids = [source_id] if source_id else list(_configured_sources(config))
    index = _library_index(create=False)
    indexed_items = [item for item in index.get("items") or [] if isinstance(item, dict)]
    works: list[dict[str, Any]] = []
    for selected_id in source_ids:
        contract = project_source_contract(config, selected_id)
        changes: list[dict[str, Any]] = []
        for binding in contract["binding"].get("items") or []:
            if not isinstance(binding, dict):
                continue
            pinned_id = str(binding.get("item_id") or "")
            current = library_item(pinned_id)
            if any(
                current.get(field) != binding.get(field)
                for field in ("bundle_sha256", "normalization_sha256", "extraction_sha256")
            ):
                changes.append(
                    {
                        "kind": "new_normalization_or_extraction",
                        "from_item_id": pinned_id,
                        "to_item_id": pinned_id,
                        "from_bundle_sha256": str(binding.get("bundle_sha256") or ""),
                        "to_bundle_sha256": str(current.get("bundle_sha256") or ""),
                        "from_normalization_sha256": str(
                            binding.get("normalization_sha256") or ""
                        ),
                        "to_normalization_sha256": str(
                            current.get("normalization_sha256") or ""
                        ),
                        "from_extraction_sha256": str(binding.get("extraction_sha256") or ""),
                        "to_extraction_sha256": str(current.get("extraction_sha256") or ""),
                    }
                )
            for candidate in indexed_items:
                if (
                    candidate.get("work_id") == current.get("work_id")
                    and candidate.get("supersedes_item_id") == pinned_id
                    and candidate.get("extraction_sha256")
                ):
                    changes.append(
                        {
                            "kind": "replacement_item",
                            "from_item_id": pinned_id,
                            "to_item_id": str(candidate.get("item_id") or ""),
                            "from_bundle_sha256": str(binding.get("bundle_sha256") or ""),
                            "to_bundle_sha256": str(candidate.get("bundle_sha256") or ""),
                            "from_normalization_sha256": str(
                                binding.get("normalization_sha256") or ""
                            ),
                            "to_normalization_sha256": str(
                                candidate.get("normalization_sha256") or ""
                            ),
                            "from_extraction_sha256": str(binding.get("extraction_sha256") or ""),
                            "to_extraction_sha256": str(candidate.get("extraction_sha256") or ""),
                        }
                    )
        works.append(
            {
                "source_id": selected_id,
                "work_id": str(contract["binding"].get("work_id") or ""),
                "binding_sha256": contract["binding_sha256"],
                "upgrade_available": bool(changes),
                "changes": changes,
            }
        )
    return {
        "schema": "fanfiction_source_upgrade_status_v1",
        "upgrade_available": any(item["upgrade_available"] for item in works),
        "works": works,
        "boundary": (
            "The project remains pinned to its current IDs and hashes. "
            "An upgrade proposal, independent semantic review, and human binding decision are required."
        ),
    }


def create_source_upgrade_proposal(
    config: ConfigDocument,
    *,
    source_id: str,
    created_by: str,
    target_item_id: str = "",
) -> dict[str, Any]:
    """Write a project-owned proposal; never update a binding or Canon here."""

    if created_by != "human":
        raise FanfictionSourceError("source upgrade proposals must be human-created")
    status = source_upgrade_status(config, source_id=source_id)
    work = status["works"][0]
    candidates = [
        item
        for item in work["changes"]
        if not target_item_id or item["to_item_id"] == target_item_id
    ]
    if not candidates and target_item_id:
        contract = project_source_contract(config, source_id)
        target = library_item(target_item_id)
        bound_ids = {
            str(item.get("item_id") or "")
            for item in contract["binding"].get("items") or []
            if isinstance(item, dict)
        }
        if (
            target.get("work_id") == work["work_id"]
            and target_item_id not in bound_ids
            and target.get("extraction_sha256")
        ):
            candidates = [
                {
                    "kind": "addition_item",
                    "from_item_id": "",
                    "to_item_id": target_item_id,
                    "from_bundle_sha256": "",
                    "to_bundle_sha256": str(target["bundle_sha256"]),
                    "from_normalization_sha256": "",
                    "to_normalization_sha256": str(target["normalization_sha256"]),
                    "from_extraction_sha256": "",
                    "to_extraction_sha256": str(target["extraction_sha256"]),
                }
            ]
    if not candidates:
        raise FanfictionSourceError("no matching source-library upgrade is available")
    if len(candidates) > 1 and not target_item_id:
        raise FanfictionSourceError("multiple upgrades are available; select --target-item-id")
    selected = candidates[0]
    root = resolve_project_root(config)
    canon = _read_json(root / "10_bible" / "fanfiction" / "source_canon.json", {})
    affected_fact_ids = _source_upgrade_fact_ids(
        canon,
        source_id=source_id,
        item_id=str(selected["from_item_id"]),
    )
    must_stale = _explicit_source_dependency_paths(root, affected_fact_ids)
    token = sha256(
        json.dumps(
            {
                "source_id": source_id,
                "binding_sha256": work["binding_sha256"],
                "change": selected,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    proposal = {
        "schema": SOURCE_UPGRADE_PROPOSAL_SCHEMA,
        "proposal_id": f"source_upgrade_{token}",
        "source_id": source_id,
        "work_id": work["work_id"],
        "current_binding_sha256": work["binding_sha256"],
        "change": selected,
        "affected_fact_ids": affected_fact_ids,
        "must_stale_artifacts": must_stale,
        "impact_method": "stable_fact_ids_and_explicit_refs_only",
        "status": "awaiting_independent_semantic_review_and_human_decision",
        "created_by": "human",
        "created_at": utc_now(),
        "binding_changed": False,
        "canon_changed": False,
    }
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    path = pack / "资料升级提案" / f"{proposal['proposal_id']}.json"
    _write_json(path, proposal)
    return {**proposal, "proposal_file": path.relative_to(root).as_posix()}


def apply_source_upgrade(
    config: ConfigDocument,
    *,
    proposal_path: str | Path,
    review_path: str | Path,
    decision_path: str | Path,
) -> dict[str, Any]:
    """Apply a future-only upgrade or route historical impact to revision_branch_v2."""

    root = resolve_project_root(config)
    proposal_file = _resolve_project_file(root, proposal_path)
    review_file = _resolve_project_file(root, review_path)
    decision_file = _resolve_project_file(root, decision_path)
    proposal = _read_json(proposal_file, None)
    review = _read_json(review_file, None)
    decision = _read_json(decision_file, None)
    errors = _validate_source_upgrade_bundle(config, proposal, review, decision)
    if errors:
        raise FanfictionSourceError("invalid source upgrade approval bundle: " + "; ".join(errors))
    source_id = str(proposal["source_id"])
    finalized = list_finalized_chapter_files(root)
    head = max((chapter for chapter, _path in finalized), default=0)
    earliest = review["earliest_affected_chapter"]
    if head and isinstance(earliest, int) and earliest <= head:
        from longform_engine.revision import create_versioned_revision_branch

        branch = create_versioned_revision_branch(
            config,
            from_chapter=earliest,
            to_chapter=head,
            reason=(
                f"source upgrade {proposal['proposal_id']}: "
                f"{decision['reason']}"
            ),
            created_by="human",
        )
        receipt = {
            "schema": "fanfiction_source_upgrade_receipt_v1",
            "proposal_id": proposal["proposal_id"],
            "status": "routed_to_revision_branch_v2",
            "revision_branch_id": branch.branch_id,
            "binding_changed": False,
            "canon_changed": False,
            "recorded_at": utc_now(),
        }
        receipt_file = proposal_file.with_name(proposal_file.stem + ".receipt.json")
        _write_json(receipt_file, receipt)
        return {**receipt, "receipt_file": receipt_file.relative_to(root).as_posix()}

    change = proposal["change"]
    result = bind_library_item(
        config,
        source_id=source_id,
        item_id=str(change["to_item_id"]),
        approved_by="human",
        upgrade_authorization=proposal,
    )
    if change["kind"] == "replacement_item" and change["from_item_id"] != change["to_item_id"]:
        _replace_project_binding_reference(
            config,
            source_id=source_id,
            from_item_id=str(change["from_item_id"]),
            to_item_id=str(change["to_item_id"]),
        )
    receipt = {
        "schema": "fanfiction_source_upgrade_receipt_v1",
        "proposal_id": proposal["proposal_id"],
        "status": "applied_future_source_upgrade",
        "source_id": source_id,
        "item_id": result["item_id"],
        "binding_changed": True,
        "canon_changed": False,
        "stale_artifacts": proposal["must_stale_artifacts"],
        "recorded_at": utc_now(),
    }
    receipt_file = proposal_file.with_name(proposal_file.stem + ".receipt.json")
    _write_json(receipt_file, receipt)
    return {**receipt, "receipt_file": receipt_file.relative_to(root).as_posix()}


def search_source_gap(
    config: ConfigDocument,
    *,
    source_id: str,
    gap: str,
    query: str,
    limit: int | None = None,
    fetcher: WebFetcher | None = None,
) -> dict[str, Any]:
    _configured_source(config, source_id)
    root = resolve_project_root(config)
    request_path: Path | None = None
    request_payload: dict[str, Any] | None = None
    if gap.startswith("source_gap_"):
        request_path, request_payload = _incremental_request(root, gap)
        if request_payload.get("source_id") != source_id:
            raise FanfictionSourceError("incremental source request belongs to another source pack")
        if request_payload.get("status") != "approved_for_search":
            raise FanfictionSourceError(
                "incremental source request requires human approval before network search"
            )
    status = coverage_gaps(
        config,
        source_id=source_id,
        gate="chapter_dependency" if request_payload is not None else "design_core",
        chapter_number=(
            int(request_payload["chapter_number"])
            if request_payload is not None and request_payload.get("chapter_number") is not None
            else None
        ),
    )
    if not any(gap in item for item in status["gaps"]):
        raise FanfictionSourceError("search must name a current approved coverage gap")
    result = search_web_candidates(config, query, limit=limit, fetcher=fetcher)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    token = sha256(f"{source_id}\0{gap}\0{query}".encode("utf-8")).hexdigest()[:16]
    path = pack / "搜索候选" / f"search_{token}.json"
    payload = {
        "schema": "fanfiction_source_search_candidates_v1",
        "source_id": source_id,
        "gap": gap,
        "query": query,
        "network_status": result["network_status"],
        "provider": result["provider"],
        "candidates": result["sources"],
        "status": "awaiting_human_source_selection",
        "created_at": utc_now(),
    }
    _write_json(path, payload)
    if request_path is not None and request_payload is not None:
        request_payload["status"] = "approved_for_search"
        request_payload["network_performed"] = True
        candidate_files = [
            str(item)
            for item in request_payload.get("candidate_files") or []
            if str(item)
        ]
        candidate_file = path.relative_to(root).as_posix()
        if candidate_file not in candidate_files:
            candidate_files.append(candidate_file)
        request_payload["candidate_files"] = candidate_files
        request_payload["search_count"] = int(request_payload.get("search_count") or 0) + 1
        request_payload["last_searched_at"] = utc_now()
        _write_json(request_path, request_payload)
    return {**payload, "candidate_file": path.relative_to(root).as_posix()}


def create_external_work_request(
    config: ConfigDocument,
    *,
    work_name: str,
    purpose: str,
    reason: str,
) -> dict[str, Any]:
    if purpose not in EXTERNAL_PURPOSES:
        raise FanfictionSourceError("unsupported external work research purpose")
    if str(config.data.get("creation", {}).get("mode") or "") == "fanfiction":
        raise FanfictionSourceError("fanfiction projects must use the fanfiction source package workflow")
    work_name = str(work_name).strip()
    reason = str(reason).strip()
    if not work_name or not reason:
        raise FanfictionSourceError("external work name and research reason are required")
    root = resolve_project_root(config)
    request_id = "external_" + sha256(f"{work_name}\0{purpose}\0{reason}".encode("utf-8")).hexdigest()[:16]
    path = root / EXTERNAL_REQUEST_ROOT / f"{request_id}.json"
    payload = {
        "schema": EXTERNAL_REQUEST_SCHEMA,
        "request_id": request_id,
        "work_name": work_name,
        "purpose": purpose,
        "reason": reason,
        "status": "pending_human_approval",
        "network_performed": False,
        "created_at": utc_now(),
    }
    _write_json(path, payload)
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def approve_external_work_request(
    config: ConfigDocument,
    *,
    request: str,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("external work research approval requires --approved-by human")
    root = resolve_project_root(config)
    path, payload = _external_request(root, request)
    if payload["status"] not in {"pending_human_approval", "approved"}:
        raise FanfictionSourceError("external work request is not awaiting approval")
    if payload["purpose"] == "use_original_elements":
        payload["status"] = "mode_change_required"
        next_command = "edit project.yaml: set creation.mode=fanfiction and declare fanfiction.sources"
    else:
        payload["status"] = "approved"
        next_command = (
            "longform-engine research external-search project.yaml "
            f"--request {payload['request_id']} --query QUERY"
        )
    payload["approved_by"] = "human"
    payload["approved_at"] = utc_now()
    payload["next_command"] = next_command
    _write_json(path, payload)
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def search_approved_external_work(
    config: ConfigDocument,
    *,
    request: str,
    query: str,
    limit: int | None = None,
    fetcher: WebFetcher | None = None,
) -> ResearchItemResult:
    root = resolve_project_root(config)
    request_path, payload = _external_request(root, request)
    if payload.get("status") != "approved":
        raise FanfictionSourceError("external work request requires explicit human approval before search")
    if payload.get("purpose") == "use_original_elements":
        raise FanfictionSourceError("original elements require creation.mode=fanfiction")
    result = search_research(config, query, limit=limit, fetcher=fetcher)
    item_path = Path(result.item_file)
    item = _read_json(item_path, {})
    item["external_work_request_id"] = payload["request_id"]
    item["external_work_purpose"] = payload["purpose"]
    _write_json(item_path, item)
    payload["status"] = "searched"
    payload["network_performed"] = True
    payload["research_item_id"] = result.item_id
    payload["searched_at"] = utc_now()
    _write_json(request_path, payload)
    return result


def validate_coverage_plan(
    plan: dict[str, Any],
    *,
    source: dict[str, Any],
    require_complete: bool,
    binding: dict[str, Any] | None = None,
) -> list[str]:
    errors = validate_workflow_record(plan)
    if errors:
        return errors
    if plan.get("workflow_kind") != "fanfiction_coverage":
        errors.append("workflow_kind 必须为 fanfiction_coverage")
    raw_scope = plan.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    raw_extensions = plan.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    if scope.get("source_id") != source.get("source_id"):
        errors.append("scope.source_id 与 project.yaml 不一致")
    work_id = str(scope.get("work_id") or "")
    if not work_id:
        errors.append("尚未选择并绑定全局作品ID")
    if binding and work_id != binding.get("work_id"):
        errors.append("覆盖计划作品ID与资料绑定不一致")
    if scope.get("canon_cutoff") != source.get("canon_cutoff"):
        errors.append("scope.canon_cutoff 与 project.yaml 不一致")
    mode = str(extensions.get("coverage_mode") or "")
    if mode not in COVERAGE_MODES:
        errors.append("coverage_mode 无效")
    versions = extensions.get("authoritative_versions")
    if not isinstance(versions, list) or not versions or any(
        not str(item).strip() for item in versions
    ):
        errors.append("authoritative_versions 必须包含至少一个权威版本")
    authorization = plan.get("authorization")
    if require_complete and (
        plan.get("state") != "approved"
        or not isinstance(authorization, dict)
        or authorization.get("approved_by") != "human"
    ):
        errors.append("覆盖需求尚未由人工批准")
    requirements = extensions.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("requirements 必须包含自然语言覆盖需求")
        requirements = []
    bound_ids = {
        str(item.get("item_id"))
        for item in (binding or {}).get("items") or []
        if isinstance(item, dict) and item.get("item_id")
    }
    need_ids: set[str] = set()
    identity_count = 0
    design_core_count = 0
    for index, need in enumerate(requirements):
        prefix = f"requirements[{index}]"
        fields = {
            "need_id",
            "requirement",
            "level",
            "applies_to",
            "status",
            "item_ids",
            "evidence_ids",
            "not_applicable_reason",
        }
        if not isinstance(need, dict) or set(need) != fields:
            errors.append(f"{prefix} 字段不完整或包含未知字段")
            continue
        need_id = str(need.get("need_id") or "")
        if not _stable_id(need_id) or need_id in need_ids:
            errors.append(f"{prefix}.need_id 必须稳定且唯一")
        need_ids.add(need_id)
        if not str(need.get("requirement") or "").strip():
            errors.append(f"{prefix}.requirement 不能为空")
        level = str(need.get("level") or "")
        if level not in COVERAGE_LEVELS:
            errors.append(f"{prefix}.level 无效")
        identity_count += level == "identity"
        design_core_count += level == "design_core"
        applies_to = need.get("applies_to")
        if not isinstance(applies_to, (str, dict)):
            errors.append(f"{prefix}.applies_to 必须是自然语言或对象")
        status = str(need.get("status") or "")
        if status not in COVERAGE_STATES:
            errors.append(f"{prefix}.status 无效")
        item_ids = need.get("item_ids")
        evidence_ids = need.get("evidence_ids")
        if not isinstance(item_ids, list) or any(not _stable_id(item) for item in item_ids):
            errors.append(f"{prefix}.item_ids 必须为列表")
            item_ids = []
        if not isinstance(evidence_ids, list) or any(not _stable_id(item) for item in evidence_ids):
            errors.append(f"{prefix}.evidence_ids 必须为稳定ID列表")
            evidence_ids = []
        if status == "covered":
            if not item_ids:
                errors.append(f"{prefix} 标记 covered 但没有资料项")
            if not evidence_ids:
                errors.append(f"{prefix} 标记 covered 但没有证据")
            if require_complete and any(str(item_id) not in bound_ids for item_id in item_ids):
                errors.append(f"{prefix} 引用了未绑定的资料项")
        elif status == "not_applicable":
            if not str(need.get("not_applicable_reason") or "").strip():
                errors.append(f"{prefix} 标记不适用但没有人工理由")
    if requirements and not identity_count:
        errors.append("覆盖需求必须至少包含一条 identity 需求")
    if requirements and not design_core_count:
        errors.append("覆盖需求必须至少包含一条 design_core 需求")
    if mode == "全作到截止点" and not any(
        isinstance(item, dict) and item.get("level") == "whole_to_cutoff"
        for item in requirements
    ):
        errors.append("全作到截止点模式必须包含 whole_to_cutoff 需求")
    return errors


def project_source_pack_dir(
    root: Path,
    source_id: str,
    *,
    create: bool,
    config: ConfigDocument,
) -> Path:
    source = _configured_source(config, source_id)
    base = root / PROJECT_PACK_ROOT
    if base.is_dir():
        for setting in base.glob("*/作品资料设定.yaml"):
            payload = _read_yaml(setting, {})
            if isinstance(payload, dict) and payload.get("资料源ID") == source_id:
                return setting.parent
    path = base / _visible_name(str(source["title"]), field="title")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def library_work(work_id: str) -> dict[str, Any]:
    index = _library_index(create=False)
    record = next(
        (item for item in index.get("works") or [] if isinstance(item, dict) and item.get("work_id") == work_id),
        None,
    )
    if record is None:
        raise FanfictionSourceError(f"unknown source library work_id: {work_id}")
    directory = _resolve_library_work_directory(record)
    raw_identity = _read_yaml(directory / "作品身份.yaml", {})
    identity = _normalize_library_work(raw_identity)
    if identity.get("schema") != LIBRARY_WORK_SCHEMA:
        raise FanfictionSourceError(f"source library work identity is invalid: {work_id}")
    return {
        **record,
        **identity,
        "path": directory.relative_to(source_library_root()).as_posix(),
    }


def library_item(item_id: str) -> dict[str, Any]:
    index = _library_index(create=False)
    record = next(
        (item for item in index.get("items") or [] if isinstance(item, dict) and item.get("item_id") == item_id),
        None,
    )
    if record is None:
        raise FanfictionSourceError(f"unknown source library item_id: {item_id}")
    directory = _resolve_library_item_directory(record)
    raw_source = _read_yaml(directory / "来源说明.yaml", {})
    source = _normalize_library_item(raw_source)
    if source.get("schema") != LIBRARY_ITEM_SCHEMA:
        actual = source.get("schema") or "missing"
        raise FanfictionSourceError(
            f"source library item schema {actual!r} is incompatible with {LIBRARY_ITEM_SCHEMA}; "
            "re-import and reprocess this source item"
        )
    item_errors = _validate_library_item_v2(source, expected_item_id=item_id)
    if item_errors:
        raise FanfictionSourceError(
            f"source library item metadata is invalid: {item_id}: " + "; ".join(item_errors)
        )
    return {
        **record,
        **source,
        "path": directory.relative_to(source_library_root()).as_posix(),
    }


def source_fact_records(source: dict[str, Any], *types: str) -> list[dict[str, Any]]:
    """Project approved semantic claims into compact read-only story records.

    This is deliberately a projection rather than a compatibility reader: the
    accepted input is ``semantic_document_v1`` and the open semantic type lives
    in each claim's extensions.  Callers that need a small deterministic view
    (for example, protected terms or chapter context) do not regain the former
    closed character/event schema.
    """

    accepted: set[str] = set()
    for fact_type in types:
        accepted.update(FACT_TYPE_ALIASES.get(fact_type, {fact_type}))
    if source.get("schema") != SEMANTIC_DOCUMENT_SCHEMA:
        return []
    records: list[dict[str, Any]] = []
    for claim in source.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        raw_extensions = claim.get("extensions")
        extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
        semantic_type = str(extensions.get("semantic_type") or "语义主张")
        if accepted and semantic_type not in accepted:
            continue
        statement = str(claim.get("statement") or "")
        records.append(
            {
                "id": str(claim.get("claim_id") or ""),
                "type": semantic_type,
                "name": str(
                    extensions.get("display_name")
                    or extensions.get("name")
                    or statement[:80]
                ),
                "summary": statement,
                "attributes": extensions,
                "evidence_refs": list(claim.get("evidence_refs") or []),
                "source_id": str(extensions.get("source_id") or ""),
                "applicability": claim.get("applicability"),
                "uncertainty": str(claim.get("uncertainty") or ""),
            }
        )
    return records


def library_item_texts(item_id: str) -> dict[str, str]:
    """Read approved normalized segments for deterministic similarity checks."""

    item = library_item(item_id)
    try:
        segments = load_normalized_segments(
            _resolve_library_item_directory(item),
            expected_sha256=str(item.get("normalization_sha256") or ""),
        )
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    return {
        str(segment["segment_id"]): str(segment["normalized_text"])
        for segment in segments
        if segment.get("review_status") in {"source_exact", "approved"}
    }


@_source_library_mutation
def rebuild_source_library_search_index() -> dict[str, Any]:
    """Rebuild the isolated user-library FTS index from approved evidence only."""

    root = source_library_root()
    index = _library_index(create=False)
    records = [value for value in index.get("items") or [] if isinstance(value, dict)]
    database = root / "派生索引" / "原著资料全文检索.sqlite"
    database.parent.mkdir(parents=True, exist_ok=True)
    temporary = database.with_suffix(".sqlite.tmp")
    temporary.unlink(missing_ok=True)
    segment_count = 0
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE evidence (
                segment_id TEXT PRIMARY KEY,
                work_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                normalization_sha256 TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                normalized_text TEXT NOT NULL,
                origin_locator_json TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE evidence_fts USING fts5(
                normalized_text,
                content='evidence',
                content_rowid='rowid',
                tokenize='unicode61'
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        for record in records:
            try:
                item = library_item(str(record.get("item_id") or ""))
                segments = load_normalized_segments(
                    _resolve_library_item_directory(item),
                    expected_sha256=str(item.get("normalization_sha256") or ""),
                )
            except (FanfictionSourceError, SourceProcessingError):
                continue
            for segment in segments:
                if segment.get("review_status") not in {"source_exact", "approved"}:
                    continue
                locator = segment.get("origin_locator") or {}
                cursor = connection.execute(
                    """
                    INSERT INTO evidence(
                        segment_id, work_id, item_id, asset_id, normalization_sha256,
                        text_sha256, normalized_text, origin_locator_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(segment["segment_id"]),
                        str(item["work_id"]),
                        str(item["item_id"]),
                        str(locator.get("asset_id") or ""),
                        str(item["normalization_sha256"]),
                        str(segment["text_sha256"]),
                        str(segment["normalized_text"]),
                        json.dumps(locator, ensure_ascii=False, sort_keys=True),
                    ),
                )
                connection.execute(
                    "INSERT INTO evidence_fts(rowid, normalized_text) VALUES (?, ?)",
                    (cursor.lastrowid, str(segment["normalized_text"])),
                )
                segment_count += 1
        manifest = {
            "schema": "source_library_search_index_v1",
            "library_index_sha256": canonical_json_hash(index),
            "segment_count": segment_count,
            "built_at": utc_now(),
            "scope": "approved_normalized_segments_only",
            "canon": False,
        }
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [(key, json.dumps(value, ensure_ascii=False)) for key, value in manifest.items()],
        )
        connection.commit()
    except sqlite3.Error as exc:
        raise FanfictionSourceError(f"source library FTS rebuild failed: {exc}") from exc
    finally:
        connection.close()
    os.replace(temporary, database)
    return {**manifest, "database": str(database)}


def search_source_library_evidence(
    query: str, *, work_id: str = "", item_id: str = "", limit: int = 20
) -> dict[str, Any]:
    """Search isolated non-Canon evidence and omit rows whose normalization drifted."""

    query = str(query).strip()
    if not query or limit <= 0 or limit > 100:
        raise FanfictionSourceError("source search requires a query and 1 <= limit <= 100")
    database = source_library_root() / "派生索引" / "原著资料全文检索.sqlite"
    if not database.is_file():
        raise FanfictionSourceError("source library FTS index is missing; run index-rebuild")
    match_query = '"' + query.replace('"', '""') + '"'
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT e.*, bm25(evidence_fts) AS rank
            FROM evidence_fts
            JOIN evidence e ON e.rowid = evidence_fts.rowid
            WHERE evidence_fts MATCH ?
              AND (? = '' OR e.work_id = ?)
              AND (? = '' OR e.item_id = ?)
            ORDER BY rank, e.item_id, e.segment_id
            LIMIT ?
            """,
            (match_query, work_id, work_id, item_id, item_id, limit * 3),
        ).fetchall()
    except sqlite3.Error as exc:
        raise FanfictionSourceError(f"source library FTS query failed: {exc}") from exc
    finally:
        connection.close()
    results: list[dict[str, Any]] = []
    for row in rows:
        try:
            current = library_item(str(row["item_id"]))
        except FanfictionSourceError:
            continue
        if current.get("normalization_sha256") != row["normalization_sha256"]:
            continue
        text = str(row["normalized_text"])
        results.append(
            {
                "work_id": row["work_id"],
                "item_id": row["item_id"],
                "asset_id": row["asset_id"],
                "segment_id": row["segment_id"],
                "normalization_sha256": row["normalization_sha256"],
                "excerpt": text[:400],
                "text_truncated": len(text) > 400,
                "origin_locator": json.loads(row["origin_locator_json"]),
                "rank": row["rank"],
            }
        )
        if len(results) >= limit:
            break
    return {
        "schema": "source_library_search_result_v1",
        "retrieval_domain": "source_evidence",
        "query": query,
        "results": results,
        "non_canonical": True,
        "project_materialized": False,
    }


def _normalize_coverage_plan(candidate: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("schema") == COVERAGE_SCHEMA:
        return dict(candidate)
    if candidate.get("协议版本") != COVERAGE_SCHEMA:
        actual = candidate.get("协议版本") or candidate.get("schema") or "missing"
        raise FanfictionSourceError(
            f"coverage plan schema {actual!r} is incompatible with {COVERAGE_SCHEMA}"
        )
    requirements = []
    for need in candidate.get("覆盖需求") or []:
        if not isinstance(need, dict):
            continue
        requirements.append(
            {
                "need_id": need.get("需求ID"),
                "requirement": need.get("需求"),
                "level": need.get("层级"),
                "applies_to": need.get("适用范围") or "当前项目",
                "status": need.get("状态") or "missing",
                "item_ids": need.get("资料项ID列表") or [],
                "evidence_ids": need.get("证据ID列表") or [],
                "not_applicable_reason": need.get("不适用理由") or "",
            }
        )
    raw_approval = candidate.get("人工批准")
    approval: dict[str, Any] = raw_approval if isinstance(raw_approval, dict) else {}
    now = utc_now()
    return {
        "schema": COVERAGE_SCHEMA,
        "workflow_id": candidate.get("工作流ID")
        or "coverage_" + sha256(str(source["source_id"]).encode("utf-8")).hexdigest()[:20],
        "workflow_kind": "fanfiction_coverage",
        "scope": {
            "source_id": candidate.get("资料源ID") or source["source_id"],
            "work_id": candidate.get("作品ID") or "",
            "canon_cutoff": candidate.get("截止点") or source["canon_cutoff"],
        },
        "state": approval.get("状态", "draft"),
        "inputs": [],
        "outputs": [],
        "authorization": {
            "approved_by": approval.get("批准人", ""),
            "approved_at": approval.get("批准时间", ""),
            "scope": "coverage_requirements_and_mode",
        }
        if approval
        else {},
        "diagnostics": [],
        "extensions": {
            "coverage_mode": candidate.get("覆盖模式") or "分层按需",
            "authoritative_versions": candidate.get("权威版本") or [],
            "requirements": requirements,
            "mode_change_reason": candidate.get("模式变更理由") or "",
        },
        "created_at": candidate.get("创建时间") or now,
        "updated_at": candidate.get("更新时间") or now,
    }


def _empty_coverage_plan(source: dict[str, Any], *, work_id: str) -> dict[str, Any]:
    return build_workflow_record(
        workflow_id="coverage_" + sha256(str(source["source_id"]).encode("utf-8")).hexdigest()[:20],
        workflow_kind="fanfiction_coverage",
        scope={
            "source_id": str(source["source_id"]),
            "work_id": work_id,
            "canon_cutoff": str(source["canon_cutoff"]),
        },
        state="draft",
        extensions={
            "coverage_mode": "分层按需",
            "authoritative_versions": [],
            "requirements": [],
            "mode_change_reason": "",
        },
    )


def _read_coverage_plan(path: Path, source: dict[str, Any]) -> dict[str, Any]:
    payload = _read_yaml(path, {})
    if not isinstance(payload, dict):
        return {}
    try:
        return _normalize_coverage_plan(payload, source)
    except FanfictionSourceError:
        return payload


def _coverage_to_chinese(plan: dict[str, Any]) -> dict[str, Any]:
    raw_scope = plan.get("scope")
    scope: dict[str, Any] = raw_scope if isinstance(raw_scope, dict) else {}
    raw_authorization = plan.get("authorization")
    authorization: dict[str, Any] = (
        raw_authorization if isinstance(raw_authorization, dict) else {}
    )
    raw_extensions = plan.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    return {
        "协议版本": COVERAGE_SCHEMA,
        "工作流ID": plan.get("workflow_id", ""),
        "资料源ID": scope.get("source_id", ""),
        "作品ID": scope.get("work_id", ""),
        "覆盖模式": extensions.get("coverage_mode", "分层按需"),
        "权威版本": extensions.get("authoritative_versions") or [],
        "截止点": scope.get("canon_cutoff", ""),
        "覆盖需求": [
            {
                "需求ID": need.get("need_id", ""),
                "需求": need.get("requirement", ""),
                "层级": need.get("level", ""),
                "适用范围": need.get("applies_to", "当前项目"),
                "状态": need.get("status", "missing"),
                "资料项ID列表": need.get("item_ids") or [],
                "证据ID列表": need.get("evidence_ids") or [],
                "不适用理由": need.get("not_applicable_reason", ""),
            }
            for need in extensions.get("requirements") or []
            if isinstance(need, dict)
        ],
        "模式变更理由": extensions.get("mode_change_reason", ""),
        "人工批准": {
            "状态": plan.get("state", "draft"),
            "批准人": authorization.get("approved_by", ""),
            "批准时间": authorization.get("approved_at", ""),
        },
        "创建时间": plan.get("created_at", ""),
        "更新时间": plan.get("updated_at", ""),
    }


def _read_project_binding(path: Path) -> dict[str, Any]:
    payload = _read_yaml(path, {})
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == PROJECT_BINDING_SCHEMA:
        return payload
    if payload.get("协议版本") != PROJECT_BINDING_SCHEMA:
        return payload
    items = []
    for entry in payload.get("固定资料") or []:
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                "item_id": entry.get("资料项ID", ""),
                "bundle_sha256": entry.get("组合哈希", ""),
                "normalization_sha256": entry.get("规范化哈希", ""),
                "extraction_sha256": entry.get("提取哈希", ""),
                "library_uri": entry.get("资料库引用", ""),
                "project_item_name": entry.get("项目资料名称", ""),
                "approved_by": entry.get("人工批准", ""),
                "bound_at": entry.get("绑定时间", ""),
            }
        )
    return {
        "schema": PROJECT_BINDING_SCHEMA,
        "source_id": payload.get("资料源ID", ""),
        "work_id": payload.get("作品ID", ""),
        "items": items,
        "updated_at": payload.get("更新时间", ""),
    }


def _version_conflict_ids(pack: Path) -> list[str]:
    conflict_ids: set[str] = set()
    items_dir = pack / "资料项"
    for extraction_file in items_dir.glob("*/提取结果.json") if items_dir.is_dir() else ():
        extraction = _read_json(extraction_file, {})
        if not isinstance(extraction, dict):
            continue
        for fact in source_fact_records(extraction, "version_conflict"):
            if fact.get("id"):
                conflict_ids.add(str(fact["id"]))
    return sorted(conflict_ids)


def _version_conflict_gaps(pack: Path, source_id: str) -> list[str]:
    conflict_ids = _version_conflict_ids(pack)
    if not conflict_ids:
        return []
    raw = _read_yaml(pack / "版本冲突决定.yaml", {})
    normalized = _normalize_version_conflict_decisions(raw, source_id=source_id)
    errors = _validate_version_conflict_decisions(
        normalized,
        expected_ids=conflict_ids,
        require_approval=True,
    )
    if normalized.get("source_id") != source_id:
        errors.append("source_id 与目标资料包不一致")
    return [f"版本冲突未解决：{error}" for error in errors]


def _normalize_version_conflict_decisions(payload: Any, *, source_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == VERSION_CONFLICT_DECISION_SCHEMA:
        return payload
    decisions = []
    for item in payload.get("决定") or []:
        if not isinstance(item, dict):
            continue
        decisions.append(
            {
                "conflict_fact_id": item.get("冲突事实ID", ""),
                "resolution": item.get("处理", ""),
                "selected_version": item.get("采用版本", ""),
                "reason": item.get("说明", ""),
            }
        )
    approval: dict[str, Any] = payload["人工批准"] if isinstance(payload.get("人工批准"), dict) else {}
    return {
        "schema": payload.get("协议版本", ""),
        "source_id": payload.get("资料源ID", source_id),
        "decisions": decisions,
        "approved_by": approval.get("批准人", ""),
        "approved_at": approval.get("批准时间", ""),
    }


def _validate_version_conflict_decisions(
    payload: dict[str, Any],
    *,
    expected_ids: list[str],
    require_approval: bool,
) -> list[str]:
    fields = {"schema", "source_id", "decisions", "approved_by", "approved_at"}
    if not isinstance(payload, dict) or set(payload) != fields:
        return ["决定文件字段无效"]
    errors: list[str] = []
    if payload.get("schema") != VERSION_CONFLICT_DECISION_SCHEMA:
        errors.append(f"schema 必须为 {VERSION_CONFLICT_DECISION_SCHEMA}")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return [*errors, "decisions 必须为列表"]
    ids: list[str] = []
    for index, item in enumerate(decisions):
        prefix = f"decisions[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "conflict_fact_id",
            "resolution",
            "selected_version",
            "reason",
        }:
            errors.append(f"{prefix} 字段无效")
            continue
        fact_id = str(item.get("conflict_fact_id") or "")
        ids.append(fact_id)
        if item.get("resolution") not in {"选择版本", "并存隔离", "排除"}:
            errors.append(f"{prefix}.resolution 必须为选择版本、并存隔离或排除")
        if item.get("resolution") == "选择版本" and not str(item.get("selected_version") or "").strip():
            errors.append(f"{prefix}.selected_version 不能为空")
        if not str(item.get("reason") or "").strip():
            errors.append(f"{prefix}.reason 不能为空")
    if len(ids) != len(set(ids)):
        errors.append("冲突事实决定不能重复")
    if set(ids) != set(expected_ids):
        errors.append("决定必须精确覆盖当前全部版本冲突事实")
    if require_approval and payload.get("approved_by") != "human":
        errors.append("版本冲突决定尚未由人工批准")
    return errors


def _version_conflict_decisions_to_chinese(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "协议版本": VERSION_CONFLICT_DECISION_SCHEMA,
        "资料源ID": payload.get("source_id", ""),
        "决定": [
            {
                "冲突事实ID": item.get("conflict_fact_id", ""),
                "处理": item.get("resolution", ""),
                "采用版本": item.get("selected_version", ""),
                "说明": item.get("reason", ""),
            }
            for item in payload.get("decisions") or []
            if isinstance(item, dict)
        ],
        "人工批准": {
            "批准人": payload.get("approved_by", ""),
            "批准时间": payload.get("approved_at", ""),
        },
    }


def _write_version_conflict_report(pack: Path, payload: dict[str, Any]) -> None:
    lines = ["# 版本冲突清单", ""]
    for item in payload.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"- 冲突事实：{item.get('conflict_fact_id', '')}",
                f"  - 人工处理：{item.get('resolution', '')}",
                f"  - 采用版本：{item.get('selected_version') or '不适用'}",
                f"  - 说明：{item.get('reason', '')}",
            ]
        )
    atomic_write_text(pack / "版本冲突清单.md", "\n".join(lines) + "\n")


def _binding_to_chinese(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "协议版本": PROJECT_BINDING_SCHEMA,
        "资料源ID": binding.get("source_id", ""),
        "作品ID": binding.get("work_id", ""),
        "固定资料": [
            {
                "资料项ID": entry.get("item_id", ""),
                "组合哈希": entry.get("bundle_sha256", ""),
                "规范化哈希": entry.get("normalization_sha256", ""),
                "提取哈希": entry.get("extraction_sha256", ""),
                "资料库引用": entry.get("library_uri", ""),
                "项目资料名称": entry.get("project_item_name", ""),
                "人工批准": entry.get("approved_by", ""),
                "绑定时间": entry.get("bound_at", ""),
            }
            for entry in binding.get("items") or []
            if isinstance(entry, dict)
        ],
        "更新时间": binding.get("updated_at", ""),
    }


def _normalize_library_work(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == LIBRARY_WORK_SCHEMA:
        return payload
    return {
        "schema": payload.get("协议版本"),
        "work_id": payload.get("作品ID"),
        "name": payload.get("作品名称"),
        "creator": payload.get("原作者"),
        "aliases": payload.get("作品别名") or [],
        "versions": payload.get("版本列表") or [],
        "approved_by": payload.get("人工批准"),
        "created_at": payload.get("创建时间"),
    }


def _normalize_library_item(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == LIBRARY_ITEM_SCHEMA:
        return payload
    return {
        "schema": payload.get("协议版本"),
        "item_id": payload.get("资料项ID"),
        "work_id": payload.get("作品ID"),
        "name": payload.get("资料名称"),
        "source_type": payload.get("资料类型"),
        "version": payload.get("版本名称"),
        "unit_range": payload.get("覆盖范围"),
        "source_method": payload.get("来源方式"),
        "source_locator": payload.get("来源定位"),
        "rights_status": payload.get("权利状态"),
        "retention_mode": payload.get("留存方式"),
        "assets": payload.get("原件集合") or [],
        "bundle_sha256": payload.get("组合哈希"),
        "normalization_sha256": payload.get("规范化哈希", ""),
        "extraction_sha256": payload.get("提取哈希", ""),
        "processing_status": payload.get("处理状态", "source_fixed"),
        "supersedes_item_id": payload.get("替代资料项ID", ""),
        "approved_by": payload.get("人工批准"),
        "created_at": payload.get("创建时间"),
    }


def _binding_errors(binding: dict[str, Any], *, require_available: bool) -> list[str]:
    errors: list[str] = []
    if binding.get("schema") != PROJECT_BINDING_SCHEMA:
        return ["资料绑定 schema 无效"]
    if not str(binding.get("work_id") or ""):
        errors.append("尚未绑定全局作品ID")
    items = binding.get("items")
    if not isinstance(items, list) or not items:
        errors.append("尚未绑定任何已批准资料项")
        return errors
    for index, entry in enumerate(items):
        if not isinstance(entry, dict):
            errors.append(f"资料绑定 items[{index}] 无效")
            continue
        try:
            item = library_item(str(entry.get("item_id") or ""))
        except FanfictionSourceError as exc:
            errors.append(str(exc))
            continue
        if entry.get("bundle_sha256") != item.get("bundle_sha256"):
            errors.append(f"资料项 {item['item_id']} 组合哈希漂移")
        if entry.get("normalization_sha256") != item.get("normalization_sha256"):
            errors.append(f"资料项 {item['item_id']} 规范化哈希漂移")
        pinned_extraction = str(entry.get("extraction_sha256") or "")
        item_dir = source_library_root() / str(item["path"])
        if not pinned_extraction or not (item_dir / "提取版本" / f"{pinned_extraction}.json").is_file():
            errors.append(f"资料项 {item['item_id']} 固定提取版本不可用")
        if require_available:
            errors.extend(_library_item_asset_errors(item))
            try:
                load_normalized_segments(
                    _resolve_library_item_directory(item),
                    expected_sha256=str(entry.get("normalization_sha256") or ""),
                )
            except SourceProcessingError as exc:
                errors.append(f"资料项 {item['item_id']} 规范化证据不可用：{exc}")
    return errors


def _source_upgrade_fact_ids(
    canon: Any,
    *,
    source_id: str,
    item_id: str,
) -> list[str]:
    if not isinstance(canon, dict) or canon.get("schema") != CANON_SCHEMA:
        return []
    evidence_ids = {
        str(item.get("evidence_id") or "")
        for item in canon.get("evidence_references") or []
        if isinstance(item, dict) and item.get("item_id") == item_id
    }
    return sorted(
        {
            str(claim.get("claim_id") or "")
            for claim in canon.get("claims") or []
            if isinstance(claim, dict)
            and (
                claim.get("extensions", {}).get("source_id") == source_id
                if isinstance(claim.get("extensions"), dict)
                else False
            )
            and evidence_ids.intersection(str(ref) for ref in claim.get("evidence_refs") or [])
            and claim.get("claim_id")
        }
    )


def _mark_source_binding_dependents_stale(
    root: Path,
    *,
    source_id: str,
    item_id: str,
    artifact_paths: list[str],
) -> None:
    state_path = root / "30_state" / "novel_state.json"
    state = _read_json(state_path, {})
    markers = state.get("project_intelligence") if isinstance(state, dict) else None
    if not isinstance(markers, dict):
        markers = {}
    now = utc_now()
    marker_keys = {"fanfiction_canon"}
    if "10_bible/fanfiction/fanfiction_bible.json" in artifact_paths:
        marker_keys.add("fanfiction_design")
    if any(
        path in artifact_paths
        for path in ("10_bible/characters.json", "10_bible/character_expression.json")
    ):
        marker_keys.update(("book_design", "character_expression_design"))
    if any(path.startswith("20_outline/") for path in artifact_paths):
        marker_keys.add("outline_design")
    for key in marker_keys:
        marker = markers.get(key)
        if not isinstance(marker, dict) or marker.get("status") != "applied":
            continue
        markers[key] = {
            **marker,
            "status": "stale",
            "stale_reason": "fanfiction_source_binding_changed",
            "stale_at": now,
            "source_id": source_id,
            "item_id": item_id,
        }
    state["project_intelligence"] = markers
    _write_json(state_path, state)
    stale_path = root / "30_state" / "stale_artifacts.json"
    stale = _read_json(stale_path, {"schema": "stale_artifact_registry_v1", "items": []})
    items = stale.get("items") if isinstance(stale, dict) else None
    if not isinstance(items, list):
        items = []
    by_path = {
        str(row.get("artifact_path") or ""): row
        for row in items
        if isinstance(row, dict) and row.get("artifact_path")
    }
    for path in artifact_paths:
        by_path[path] = {
            "artifact_path": path,
            "classification": "must_stale",
            "dependency_fact_ids": [],
            "source": "fanfiction_source_binding",
            "source_id": source_id,
            "item_id": item_id,
            "state": "stale",
            "stale_at": now,
        }
    _write_json(
        stale_path,
        {"schema": "stale_artifact_registry_v1", "items": list(by_path.values())},
    )


def _validate_source_upgrade_bundle(
    config: ConfigDocument,
    proposal: Any,
    review: Any,
    decision: Any,
) -> list[str]:
    errors: list[str] = []
    proposal_fields = {
        "schema",
        "proposal_id",
        "source_id",
        "work_id",
        "current_binding_sha256",
        "change",
        "affected_fact_ids",
        "must_stale_artifacts",
        "impact_method",
        "status",
        "created_by",
        "created_at",
        "binding_changed",
        "canon_changed",
    }
    if not isinstance(proposal, dict) or set(proposal) != proposal_fields:
        return ["source upgrade proposal fields are invalid"]
    if proposal.get("schema") != SOURCE_UPGRADE_PROPOSAL_SCHEMA:
        errors.append(f"proposal schema must be {SOURCE_UPGRADE_PROPOSAL_SCHEMA}")
    if proposal.get("created_by") != "human":
        errors.append("source upgrade proposal must be human-created")
    if proposal.get("binding_changed") is not False or proposal.get("canon_changed") is not False:
        errors.append("source upgrade proposal must be non-applying")
    if proposal.get("impact_method") != "stable_fact_ids_and_explicit_refs_only":
        errors.append("source upgrade impact must use stable fact IDs and explicit refs only")
    source_id = str(proposal.get("source_id") or "")
    try:
        contract = project_source_contract(config, source_id)
    except (FanfictionSourceError, KeyError, OSError) as exc:
        errors.append(f"current project source contract is unavailable: {exc}")
        contract = {}
    if proposal.get("current_binding_sha256") != contract.get("binding_sha256"):
        errors.append("source upgrade proposal binding is stale")
    change = proposal.get("change")
    change_fields = {
        "kind",
        "from_item_id",
        "to_item_id",
        "from_bundle_sha256",
        "to_bundle_sha256",
        "from_normalization_sha256",
        "to_normalization_sha256",
        "from_extraction_sha256",
        "to_extraction_sha256",
    }
    if not isinstance(change, dict) or set(change) != change_fields:
        errors.append("source upgrade change fields are invalid")
    else:
        try:
            target = library_item(str(change.get("to_item_id") or ""))
        except FanfictionSourceError as exc:
            errors.append(str(exc))
        else:
            if change.get("to_bundle_sha256") != target.get("bundle_sha256"):
                errors.append("source upgrade target asset bundle changed after proposal")
            if change.get("to_normalization_sha256") != target.get("normalization_sha256"):
                errors.append("source upgrade target normalization changed after proposal")
            if change.get("to_extraction_sha256") != target.get("extraction_sha256"):
                errors.append("source upgrade target extraction changed after proposal")
            if target.get("work_id") != proposal.get("work_id"):
                errors.append("source upgrade target belongs to another work")
    canon = _read_json(
        resolve_project_root(config) / "10_bible" / "fanfiction" / "source_canon.json",
        {},
    )
    expected_fact_ids = _source_upgrade_fact_ids(
        canon,
        source_id=source_id,
        item_id=str((change or {}).get("from_item_id") or ""),
    )
    expected_stale = _explicit_source_dependency_paths(resolve_project_root(config), expected_fact_ids)
    if proposal.get("affected_fact_ids") != expected_fact_ids:
        errors.append("affected_fact_ids do not match current project Canon evidence")
    if proposal.get("must_stale_artifacts") != expected_stale:
        errors.append("must_stale_artifacts cannot omit or downgrade explicit dependencies")

    review_fields = {
        "schema",
        "proposal_sha256",
        "independent_from_proposer",
        "verdict",
        "affected_fact_ids",
        "must_stale_artifacts",
        "earliest_affected_chapter",
        "reason",
        "reviewed_by",
    }
    if not isinstance(review, dict) or set(review) != review_fields:
        errors.append("source upgrade semantic review fields are invalid")
    else:
        if review.get("schema") != SOURCE_UPGRADE_REVIEW_SCHEMA:
            errors.append(f"review schema must be {SOURCE_UPGRADE_REVIEW_SCHEMA}")
        if review.get("proposal_sha256") != _json_hash(proposal):
            errors.append("source upgrade review proposal hash is stale")
        if review.get("independent_from_proposer") is not True:
            errors.append("source upgrade semantic review must be independent")
        if review.get("verdict") != "pass":
            errors.append("source upgrade apply requires a passing semantic review")
        if review.get("affected_fact_ids") != expected_fact_ids:
            errors.append("semantic review cannot omit deterministic affected facts")
        if review.get("must_stale_artifacts") != expected_stale:
            errors.append("semantic review cannot downgrade deterministic must_stale artifacts")
        earliest = review.get("earliest_affected_chapter")
        if earliest is not None and (
            not isinstance(earliest, int) or isinstance(earliest, bool) or earliest <= 0
        ):
            errors.append("earliest_affected_chapter must be positive or null")
        for field in ("reason", "reviewed_by"):
            if not isinstance(review.get(field), str) or not review[field].strip():
                errors.append(f"source upgrade review {field} must be non-empty")

    decision_fields = {
        "schema",
        "proposal_sha256",
        "semantic_review_sha256",
        "decision",
        "reason",
        "decided_by",
    }
    if not isinstance(decision, dict) or set(decision) != decision_fields:
        errors.append("source upgrade human decision fields are invalid")
    else:
        if decision.get("schema") != SOURCE_UPGRADE_DECISION_SCHEMA:
            errors.append(f"decision schema must be {SOURCE_UPGRADE_DECISION_SCHEMA}")
        if decision.get("proposal_sha256") != _json_hash(proposal):
            errors.append("source upgrade decision proposal hash is stale")
        if isinstance(review, dict) and decision.get("semantic_review_sha256") != _json_hash(review):
            errors.append("source upgrade decision semantic review hash is stale")
        if decision.get("decision") != "approve":
            errors.append("source upgrade is not human-approved")
        if decision.get("decided_by") != "human":
            errors.append("source upgrade decision must be human-owned")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            errors.append("source upgrade decision reason must be non-empty")
    return errors


def _replace_project_binding_reference(
    config: ConfigDocument,
    *,
    source_id: str,
    from_item_id: str,
    to_item_id: str,
) -> None:
    root = resolve_project_root(config)
    source = _configured_source(config, source_id)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    binding_path = pack / "资料绑定.yaml"
    binding = _read_project_binding(binding_path)
    binding["items"] = [
        item
        for item in binding.get("items") or []
        if isinstance(item, dict) and item.get("item_id") != from_item_id
    ]
    binding["updated_at"] = utc_now()
    _write_yaml(binding_path, _binding_to_chinese(binding))
    plan_path = pack / "全作覆盖计划.yaml"
    plan = _read_coverage_plan(plan_path, source)
    raw_extensions = plan.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    for need in extensions.get("requirements") or []:
        if not isinstance(need, dict):
            continue
        need["item_ids"] = list(
            dict.fromkeys(
                to_item_id if item_id == from_item_id else item_id
                for item_id in need.get("item_ids") or []
            )
        )
    _write_yaml(plan_path, _coverage_to_chinese(plan))
    _write_coverage_report(config, source_id)


def _resolve_project_file(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise FanfictionSourceError("source upgrade approval files must live under the project root") from exc
    if not resolved.is_file():
        raise FanfictionSourceError(f"source upgrade approval file does not exist: {resolved}")
    return resolved


def _json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _explicit_source_dependency_paths(root: Path, fact_ids: list[str]) -> list[str]:
    paths = {"10_bible/fanfiction/source_canon.json"}
    if not fact_ids:
        return sorted(paths)
    for relative_dir in ("10_bible", "20_outline", "30_state", "50_workbench/writing_tasks"):
        directory = root / relative_dir
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.json"):
            if path == root / "10_bible" / "fanfiction" / "source_canon.json":
                continue
            payload = _read_json(path, None)
            if any(_contains_exact_value(payload, fact_id) for fact_id in fact_ids):
                paths.add(path.relative_to(root).as_posix())
    return sorted(paths)


def _contains_exact_value(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return any(_contains_exact_value(item, expected) for item in value)
    if isinstance(value, dict):
        return any(_contains_exact_value(item, expected) for item in value.values())
    return False


def _library_item_asset_errors(item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    assets = item.get("assets")
    if not isinstance(assets, list):
        return [f"source item {item.get('item_id')} assets is invalid"]
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("schema") != ASSET_SCHEMA:
            errors.append(f"source item {item.get('item_id')} contains an invalid source asset")
            continue
        try:
            path = resolve_asset_path(source_library_root(), asset)
        except SourceProcessingError as exc:
            errors.append(str(exc))
            continue
        if _file_hash(path) != asset.get("sha256"):
            errors.append(f"source asset {asset.get('asset_id')} hash drift")
    return errors


def _validate_library_item_v2(item: dict[str, Any], *, expected_item_id: str) -> list[str]:
    errors: list[str] = []
    if item.get("item_id") != expected_item_id:
        errors.append("item_id does not match the library index")
    assets = item.get("assets")
    if not isinstance(assets, list):
        return [*errors, "assets must be a list"]
    asset_fields = {
        "schema", "asset_id", "original_name", "relative_path", "media_type",
        "detected_format", "size_bytes", "mtime_ns", "sha256", "storage_mode",
        "managed_path", "external_path", "source_method", "rights_status",
        "retention_mode", "availability", "role",
    }
    asset_ids: set[str] = set()
    for index, asset in enumerate(assets):
        prefix = f"assets[{index}]"
        if not isinstance(asset, dict) or set(asset) != asset_fields:
            errors.append(f"{prefix} fields are invalid")
            continue
        if asset.get("schema") != ASSET_SCHEMA:
            errors.append(f"{prefix}.schema must be {ASSET_SCHEMA}")
        asset_id = str(asset.get("asset_id") or "")
        if not _stable_id(asset_id) or asset_id in asset_ids:
            errors.append(f"{prefix}.asset_id must be stable and unique")
        asset_ids.add(asset_id)
        if asset.get("storage_mode") not in STORAGE_MODES:
            errors.append(f"{prefix}.storage_mode is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256") or "")):
            errors.append(f"{prefix}.sha256 is invalid")
        if asset.get("role") not in ASSET_ROLES:
            errors.append(f"{prefix}.role is invalid")
        if asset.get("rights_status") not in RIGHTS_STATUSES:
            errors.append(f"{prefix}.rights_status is invalid")
        if asset.get("retention_mode") not in RETENTION_MODES:
            errors.append(f"{prefix}.retention_mode is invalid")
    bundle_basis: list[dict[str, Any]] = [
        {
            "asset_id": asset["asset_id"],
            "sha256": asset["sha256"],
            "role": asset["role"],
            "relative_path": asset["relative_path"],
            "storage_mode": asset["storage_mode"],
        }
        for asset in sorted(
            (value for value in assets if isinstance(value, dict) and set(value) == asset_fields),
            key=lambda value: (value["relative_path"], value["asset_id"]),
        )
    ]
    if not bundle_basis:
        bundle_basis = [{"source_locator": str(item.get("source_locator") or "").strip()}]
    if canonical_json_hash(bundle_basis) != item.get("bundle_sha256"):
        errors.append("bundle_sha256 does not match the sorted asset manifest")
    return errors


def _validate_not_link_or_reparse(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FanfictionSourceError(f"source path cannot be inspected: {path}") from exc
    attributes = int(getattr(metadata, "st_file_attributes", 0))
    if path.is_symlink() or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise FanfictionSourceError(f"symlink, junction, or reparse-point source is not allowed: {path}")


def _validate_ingest_source(path: Path) -> None:
    _validate_not_link_or_reparse(path)
    if not path.is_file():
        raise FanfictionSourceError(f"source ingest file does not exist: {path}")
    if path.stat().st_size > MAX_INGEST_FILE_BYTES:
        raise FanfictionSourceError(f"source ingest file exceeds the size limit: {path}")


def _validate_ingest_directory(path: Path) -> None:
    _validate_not_link_or_reparse(path)
    if not path.is_dir():
        raise FanfictionSourceError(f"source ingest directory does not exist: {path}")


def _walk_ingest_directory(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, names, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(directory)
        _validate_not_link_or_reparse(current)
        retained: list[str] = []
        for name in sorted(names):
            child = current / name
            _validate_not_link_or_reparse(child)
            retained.append(name)
        names[:] = retained
        for name in sorted(filenames):
            path = current / name
            _validate_ingest_source(path)
            files.append(path)
            if len(files) > MAX_INGEST_FILES:
                raise FanfictionSourceError(f"ingest directory exceeds {MAX_INGEST_FILES} files")
    return files


def _safe_relative_input(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized:
        raise FanfictionSourceError("source relative path cannot be empty")
    parts = normalized.split("/")
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if (
        Path(normalized).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any(any(char in part for char in '<>:"|?*') for part in parts)
        or any(Path(part).stem.upper() in reserved for part in parts)
    ):
        raise FanfictionSourceError(f"unsafe source relative path: {value}")
    return "/".join(parts)


def _stream_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    with source.open("rb") as reader, temporary.open("wb") as writer:
        shutil.copyfileobj(reader, writer, length=1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    temporary.replace(target)


def _fix_source_asset(
    *,
    root: Path,
    source: Path,
    relative_path: str,
    role: str,
    storage_mode: str,
    rights_status: str,
    retention_mode: str,
    source_method: str,
) -> dict[str, Any]:
    content_sha = _file_hash(source)
    format_info = detect_asset_format(source)
    asset_id = "asset_" + canonical_json_hash(
        {"sha256": content_sha, "relative_path": relative_path, "role": role}
    )[:20]
    managed_path = ""
    external_path = ""
    if storage_mode == "managed_copy":
        target = root / "原件对象" / content_sha[:2] / content_sha / _visible_filename(source.name)
        if target.is_file() and _file_hash(target) != content_sha:
            raise FanfictionSourceError("managed source object hash collision")
        if not target.exists():
            _stream_copy(source, target)
        managed_path = target.relative_to(root).as_posix()
    else:
        external_path = str(source)
    return {
        "schema": ASSET_SCHEMA,
        "asset_id": asset_id,
        "original_name": _visible_filename(source.name),
        "relative_path": relative_path,
        "media_type": format_info["media_type"],
        "detected_format": format_info["detected_format"],
        "size_bytes": source.stat().st_size,
        "mtime_ns": source.stat().st_mtime_ns,
        "sha256": content_sha,
        "storage_mode": storage_mode,
        "managed_path": managed_path,
        "external_path": external_path,
        "source_method": source_method,
        "rights_status": rights_status,
        "retention_mode": retention_mode,
        "availability": "available",
        "role": role,
    }


def _validate_short_evidence_files(files: list[tuple[Path, str, str]]) -> None:
    if len(files) != 1:
        raise FanfictionSourceError("short_evidence retention accepts exactly one short text asset")
    path = files[0][0]
    if path.suffix.casefold() not in {".md", ".markdown", ".txt"}:
        raise FanfictionSourceError("short_evidence retention accepts Markdown or TXT only")
    content = path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FanfictionSourceError("short_evidence text must be UTF-8") from exc
    if len(text) > 2_000:
        raise FanfictionSourceError("short_evidence content must not exceed 2000 Unicode characters")


def _suggest_asset_role(suffix: str) -> str:
    normalized = suffix.casefold()
    if normalized in {".ass", ".ssa", ".srt"}:
        return "subtitle"
    if normalized in {".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}:
        return "human_note"
    return "original"


def _source_ingest_batch(batch_id: str) -> tuple[dict[str, Any], Path]:
    index = _library_index(create=False)
    record = next(
        (
            item
            for item in index.get("batches") or []
            if isinstance(item, dict) and item.get("batch_id") == batch_id
        ),
        None,
    )
    if record is None:
        raise FanfictionSourceError(f"unknown source ingest batch: {batch_id}")
    path = source_library_root() / str(record.get("path") or "")
    payload = _read_json(path, None)
    if not isinstance(payload, dict) or payload.get("schema") != INGEST_BATCH_SCHEMA:
        raise FanfictionSourceError(f"invalid source ingest batch: {batch_id}")
    return payload, path


def _apply_source_ingest_groups(
    batch: dict[str, Any],
    groups: list[Any],
    files: dict[str, dict[str, Any]],
    defaults: dict[str, Any],
) -> list[dict[str, Any]]:
    imported: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or group.get("approved") is not True:
            continue
        selected: list[tuple[Path, str, str]] = []
        for file_id in group.get("file_ids") or []:
            record = files.get(str(file_id))
            if record is None:
                raise FanfictionSourceError(f"ingest group references unknown staged file: {file_id}")
            stored = record.get("staged_path") or record.get("source_path")
            source = (
                source_library_root() / str(stored)
                if record.get("staged_path")
                else Path(str(stored))
            )
            if _file_hash(source) != record.get("sha256"):
                raise FanfictionSourceError(f"staged source hash drift: {record.get('relative_path')}")
            selected.append(
                (source, str(record.get("role") or "original"), str(record["relative_path"]))
            )
        if not selected:
            raise FanfictionSourceError("approved ingest group must contain at least one file")
        imported.append(
            import_source_item_assets(
                work_id=str(batch["work_id"]),
                name=str(group.get("name") or "未命名资料项"),
                source_type=str(
                    group.get("source_type") or defaults.get("source_type") or "未知资料"
                ),
                version=str(group.get("version") or defaults.get("version") or "未指定版本"),
                unit_range=str(
                    group.get("unit_range") or defaults.get("unit_range") or "待确认范围"
                ),
                source_method=str(defaults.get("source_method") or "用户批量导入"),
                rights_status=str(defaults.get("rights_status") or "unverified"),
                retention_mode=str(defaults.get("retention_mode") or "metadata_only"),
                approved_by="human",
                files=selected,
                storage_mode=str(batch.get("storage_mode") or "managed_copy"),
            )
        )
    return imported


def _remove_owned_directory(target: Path, *, owner: Path) -> None:
    resolved_target = target.resolve()
    resolved_owner = owner.resolve()
    try:
        relative = resolved_target.relative_to(resolved_owner)
    except ValueError as exc:
        raise FanfictionSourceError("refused to remove a directory outside its source owner") from exc
    if not relative.parts or resolved_target == resolved_owner:
        raise FanfictionSourceError("refused to remove a broad source directory")
    shutil.rmtree(resolved_target)


def _update_library_item_processing(
    item: dict[str, Any],
    *,
    normalization_sha256: str,
    processing_status: str,
    extraction_sha256: str | None = None,
) -> None:
    item_dir = _resolve_library_item_directory(item)
    raw = _read_yaml(item_dir / "来源说明.yaml", {})
    if not isinstance(raw, dict) or raw.get("协议版本") != LIBRARY_ITEM_SCHEMA:
        raise FanfictionSourceError("source item metadata changed during processing")
    raw["规范化哈希"] = normalization_sha256
    raw["处理状态"] = processing_status
    if extraction_sha256 is not None:
        raw["提取哈希"] = extraction_sha256
    _write_yaml(item_dir / "来源说明.yaml", raw)
    index = _library_index(create=False)
    for record in index.get("items") or []:
        if isinstance(record, dict) and record.get("item_id") == item.get("item_id"):
            record["normalization_sha256"] = normalization_sha256
            record["processing_status"] = processing_status
            if extraction_sha256 is not None:
                record["extraction_sha256"] = extraction_sha256
    _write_library_index(index)


def _expanded_extraction_evidence(
    item: dict[str, Any], evidence: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    try:
        segments = load_normalized_segments(
            _resolve_library_item_directory(item),
            expected_sha256=str(item.get("normalization_sha256") or ""),
        )
    except SourceProcessingError as exc:
        raise FanfictionSourceError(str(exc)) from exc
    by_id = {str(segment["segment_id"]): segment for segment in segments}
    expanded: list[dict[str, Any]] = []
    for record in evidence:
        segment = by_id[str(record["segment_id"])]
        expanded.append(
            {
                **record,
                "origin_locator": segment["origin_locator"],
                "segment_text_sha256": segment["text_sha256"],
            }
        )
    return expanded


def _resolve_library_work_directory(record: dict[str, Any]) -> Path:
    root = source_library_root()
    configured = root / str(record.get("path") or "")
    if configured.is_dir():
        return configured
    work_id = str(record.get("work_id") or "")
    for identity_file in (root / "作品").glob("*/作品身份.yaml"):
        identity = _normalize_library_work(_read_yaml(identity_file, {}))
        if identity.get("work_id") == work_id:
            return identity_file.parent
    raise FanfictionSourceError(f"source library work directory is unavailable: {work_id}")


def _resolve_library_item_directory(record: dict[str, Any]) -> Path:
    root = source_library_root()
    configured = root / str(record.get("path") or "")
    if configured.is_dir():
        return configured
    item_id = str(record.get("item_id") or "")
    for source_file in (root / "作品").glob("*/资料项/*/来源说明.yaml"):
        source = _normalize_library_item(_read_yaml(source_file, {}))
        if source.get("item_id") == item_id:
            return source_file.parent
    raise FanfictionSourceError(f"source library item directory is unavailable: {item_id}")


def _write_coverage_report(config: ConfigDocument, source_id: str) -> None:
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    if not pack.is_dir():
        return
    status = coverage_gaps(config, source_id=source_id)
    lines = ["# 资料覆盖情况", "", f"- 资料源ID：{source_id}", f"- 全量门禁：{'通过' if status['complete'] else '未通过'}", ""]
    if status["gaps"]:
        lines.extend(["## 当前缺口", "", *[f"- {item}" for item in status["gaps"]], ""])
    else:
        lines.extend(["全部人工批准单元已经由固定资料项覆盖。", ""])
    atomic_write_text(pack / "资料覆盖情况.md", "\n".join(lines))


def _external_request(root: Path, request: str) -> tuple[Path, dict[str, Any]]:
    candidate = Path(request)
    if candidate.suffix:
        path = candidate if candidate.is_absolute() else root / candidate
    else:
        path = root / EXTERNAL_REQUEST_ROOT / f"{request}.json"
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FanfictionSourceError("external work request must live under the project root") from exc
    payload = _read_json(path, None)
    if not isinstance(payload, dict) or payload.get("schema") != EXTERNAL_REQUEST_SCHEMA:
        raise FanfictionSourceError(f"invalid external work research request: {request}")
    return path, payload


def _coverage_requirement_gaps(
    plan: dict[str, Any],
    *,
    gate: str,
    chapter_number: int | None,
) -> list[str]:
    raw_extensions = plan.get("extensions")
    extensions: dict[str, Any] = raw_extensions if isinstance(raw_extensions, dict) else {}
    mode = str(extensions.get("coverage_mode") or "分层按需")
    required_levels = {
        "identity": {"identity"},
        "design_core": {"identity", "design_core"},
        "volume_scope": {"identity", "design_core", "volume_scope"},
        "chapter_dependency": {"identity", "design_core", "volume_scope", "chapter_dependency"},
        "all": set(COVERAGE_LEVELS),
    }[gate]
    if mode == "全作到截止点":
        required_levels.add("whole_to_cutoff")
    gaps: list[str] = []
    for need in extensions.get("requirements") or []:
        if not isinstance(need, dict) or need.get("level") not in required_levels:
            continue
        if need.get("level") == "chapter_dependency" and chapter_number is not None:
            applies_to = need.get("applies_to")
            if isinstance(applies_to, dict):
                applies_chapter = applies_to.get("chapter_number")
                if applies_chapter is not None:
                    try:
                        normalized_chapter = int(applies_chapter)
                    except (TypeError, ValueError):
                        gaps.append(
                            f"覆盖需求 {need.get('need_id', '未命名')}：章节适用范围无效"
                        )
                        continue
                    if normalized_chapter != chapter_number:
                        continue
        if need.get("status") not in {"covered", "not_applicable"}:
            gaps.append(
                f"覆盖需求 {need.get('need_id', '未命名')}："
                f"{need.get('requirement', '未说明')}（{need.get('status', 'missing')}）"
            )
    return gaps


def _incremental_request_gaps(
    pack: Path, *, chapter_number: int | None = None
) -> list[str]:
    gaps: list[str] = []
    directory = pack / "待审资料需求"
    for path in sorted(directory.glob("source_gap_*.json")) if directory.is_dir() else ():
        payload = _read_json(path, {})
        if not isinstance(payload, dict) or payload.get("schema") != INCREMENTAL_REQUEST_SCHEMA:
            gaps.append(f"增量资料需求文件无效：{path.name}")
            continue
        status = str(payload.get("status") or "")
        if status == "resolved":
            continue
        request_chapter = payload.get("chapter_number")
        if chapter_number is not None and request_chapter is not None:
            try:
                normalized_chapter = int(request_chapter)
            except (TypeError, ValueError):
                gaps.append(f"增量资料需求 {path.name} 的章节号无效")
                continue
            if normalized_chapter != chapter_number:
                continue
        gaps.append(
            f"增量资料需求 {payload.get('request_id', path.stem)}："
            f"{payload.get('need', '未说明')}（{status or '状态缺失'}）"
        )
    return gaps


def _incremental_request(root: Path, request_id: str) -> tuple[Path, dict[str, Any]]:
    matches = list((root / PROJECT_PACK_ROOT).glob(f"*/待审资料需求/{request_id}.json"))
    if len(matches) != 1:
        raise FanfictionSourceError(f"unknown or ambiguous incremental source request: {request_id}")
    payload = _read_json(matches[0], {})
    if not isinstance(payload, dict) or payload.get("schema") != INCREMENTAL_REQUEST_SCHEMA:
        raise FanfictionSourceError(f"invalid incremental source request: {request_id}")
    return matches[0], payload


def _configured_sources(config: ConfigDocument) -> dict[str, dict[str, Any]]:
    fanfiction: dict[str, Any] = (
        config.data["fanfiction"] if isinstance(config.data.get("fanfiction"), dict) else {}
    )
    return {
        str(item["source_id"]): item
        for item in fanfiction.get("sources") or []
        if isinstance(item, dict) and item.get("source_id")
    }


def _configured_source(config: ConfigDocument, source_id: str) -> dict[str, Any]:
    source = _configured_sources(config).get(source_id)
    if source is None:
        raise FanfictionSourceError(f"unknown configured fanfiction source_id: {source_id}")
    return source


def _require_fanfiction_mode(config: ConfigDocument) -> None:
    if str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        raise FanfictionSourceError("fanfiction source packages require creation.mode=fanfiction")


def _library_index(*, create: bool) -> dict[str, Any]:
    root = source_library_root()
    path = root / "资料库索引.json"
    if create and not path.is_file():
        initialize_source_library()
    payload = _read_json(
        path,
        {"schema": LIBRARY_INDEX_SCHEMA, "works": [], "items": [], "batches": []},
    )
    if not isinstance(payload, dict) or payload.get("schema") != LIBRARY_INDEX_SCHEMA:
        actual = payload.get("schema") if isinstance(payload, dict) else "invalid_json_object"
        raise FanfictionSourceError(
            f"source library schema {actual!r} is incompatible with {LIBRARY_INDEX_SCHEMA}; "
            "the existing library was preserved and must be re-imported into a v2 library"
        )
    if (
        not isinstance(payload.get("works"), list)
        or not isinstance(payload.get("items"), list)
        or not isinstance(payload.get("batches"), list)
    ):
        raise FanfictionSourceError(f"source library index collections are invalid: {path}")
    return payload


def _write_library_index(index: dict[str, Any]) -> None:
    index["updated_at"] = utc_now()
    _write_json(source_library_root() / "资料库索引.json", index)


def _find_library_work(name: str, creator: str) -> dict[str, Any] | None:
    for work in _library_index(create=False).get("works") or []:
        if not isinstance(work, dict):
            continue
        if str(work.get("name") or "").casefold() == name.casefold() and str(
            work.get("creator") or ""
        ).casefold() == creator.casefold():
            return work
    return None


def _unique_project_item_directory(base: Path, name: str, item_id: str) -> Path:
    if base.is_dir():
        for binding in base.glob("*/来源绑定.yaml"):
            payload = _read_yaml(binding, {})
            if isinstance(payload, dict) and (payload.get("item_id") or payload.get("资料项ID")) == item_id:
                return binding.parent
    return _unique_named_directory(base, _visible_name(name, field="item name"), stable_id=item_id)


def _unique_named_directory(base: Path, name: str, *, stable_id: str) -> Path:
    candidate = base / name
    if not candidate.exists():
        return candidate
    for identity_name in ("作品身份.yaml", "来源说明.yaml", "来源绑定.yaml"):
        identity = _read_yaml(candidate / identity_name, {})
        if isinstance(identity, dict) and stable_id in {
            identity.get("work_id"),
            identity.get("item_id"),
            identity.get("作品ID"),
            identity.get("资料项ID"),
        }:
            return candidate
    return base / f"{name}（{stable_id[-8:]}）"


def _visible_name(value: str, *, field: str) -> str:
    cleaned = " ".join(str(value).split()).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        raise FanfictionSourceError(f"{field} is required")
    if any(character in cleaned for character in '<>:"/\\|?*'):
        raise FanfictionSourceError(f"{field} contains characters that are unsafe in a directory name")
    return cleaned[:120]


def _visible_filename(value: str) -> str:
    name = _visible_name(value, field="filename")
    if name in {"作品身份.yaml", "来源说明.yaml", "提取结果.json", "证据索引.json"}:
        return "原件_" + name
    return name


def _string_list(values: Iterable[str], *, field: str) -> list[str]:
    result = [str(value).strip() for value in values]
    if any(not value for value in result):
        raise FanfictionSourceError(f"{field} must not contain empty values")
    return list(dict.fromkeys(result))


def _stable_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}", value))


def _read_yaml_or_json(path: Path) -> Any:
    if not path.is_file():
        raise FanfictionSourceError(f"input file does not exist: {path}")
    if path.suffix.lower() == ".json":
        return _read_json(path, None)
    return _read_yaml(path, None)


def _read_yaml(path: Path, default: Any) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, yaml.YAMLError):
        return default


def _write_yaml(path: Path, payload: Any) -> None:
    atomic_write_text(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_hash(path: Path) -> str:
    if not path.is_file():
        return ""
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
