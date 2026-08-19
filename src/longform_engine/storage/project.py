"""Initialize and inspect longform novel project directories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Sequence
import json
import os
import secrets
import shutil
import socket
import sqlite3
import tempfile

import yaml

from longform_engine.config import ConfigDocument

from .layout import BASE_DIRECTORIES, INITIAL_JSON_FILES, INITIAL_JSONL_FILES, INITIAL_TEXT_FILES, SUBDIRECTORIES


@dataclass(frozen=True)
class ProjectInitResult:
    """Result returned after creating or refreshing a project layout."""

    root: Path
    created_dirs: tuple[Path, ...]
    created_files: tuple[Path, ...]
    project_config: Path


class StorageError(ValueError):
    """Raised when a storage operation cannot safely complete."""


@dataclass(frozen=True)
class SnapshotResult:
    """Result returned after creating a project snapshot."""

    snapshot_dir: Path
    copied_paths: tuple[Path, ...]


@dataclass(frozen=True)
class TransactionReportResult:
    """Result returned after writing a canonical write transaction report."""

    report_file: Path


class ApplyTransaction:
    """Canonical transaction with file snapshots and SQLite backup participants."""

    def __init__(
        self,
        root: Path,
        *,
        command: str,
        chapter_number: int | None = None,
        source_paths: tuple[str | Path, ...] | list[str | Path] = (),
        touched_paths: tuple[str | Path, ...] | list[str | Path] = (),
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.command = command
        self.chapter_number = chapter_number
        self.source_paths = list(source_paths)
        self.touched_paths = dedupe_project_paths(self.root, touched_paths)
        self.metadata = dict(metadata or {})
        base_parts = [utc_transaction_timestamp(), safe_file_token(command)]
        if chapter_number is not None:
            base_parts.append(f"ch{chapter_number:03d}")
        self.base_name = "_".join(base_parts)
        self.report_dir = self.root / "70_runtime" / "transactions"
        self.report_file = unique_report_path(self.report_dir / f"{self.base_name}.json")
        self.rollback_file = self.report_file.with_name(f"{self.report_file.stem}.rollback.json")
        snapshot_id = sha256(self.report_file.stem.encode("utf-8")).hexdigest()[:12]
        self.snapshot_dir = self.root / "70_runtime" / "tx" / snapshot_id
        self._snapshots: list[dict[str, Any]] = []
        self._sqlite_backups: list[dict[str, Any]] = []
        self._before_state: list[dict[str, Any]] = []
        self._filesystem_paths, self._sqlite_paths = partition_transaction_paths(self.root, self.touched_paths)
        self.created_at = utc_now()
        self._active = False
        self._finished = False

    def __enter__(self) -> "ApplyTransaction":
        self.begin()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> Literal[False]:
        if exc_type is not None:
            self.rollback(exc)
            return False
        self.commit()
        return False

    def begin(self) -> "ApplyTransaction":
        if self._active:
            return self
        self.report_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self._write_report(
            self._payload(
                status="preparing",
                report_type="canonical_write_transaction_report_v3",
                extra=self._snapshot_payload(cleanup_complete=False),
            )
        )
        for path in self._filesystem_paths:
            self._snapshots.append(snapshot_transaction_path(self.root, self.snapshot_dir, path))
            self._write_report(
                self._payload(
                    status="preparing",
                    report_type="canonical_write_transaction_report_v3",
                    extra=self._snapshot_payload(cleanup_complete=False),
                )
            )
        for path in self._sqlite_paths:
            self._sqlite_backups.append(snapshot_sqlite_database(self.root, self.snapshot_dir, path))
            self._write_report(
                self._payload(
                    status="preparing",
                    report_type="canonical_write_transaction_report_v3",
                    extra=self._snapshot_payload(cleanup_complete=False),
                )
            )
        self._before_state = transaction_paths_state(self.root, self.touched_paths)
        self._write_report(
            self._payload(
                status="prepared",
                report_type="canonical_write_transaction_report_v3",
                extra={
                    **self._snapshot_payload(cleanup_complete=False),
                    "prepared_at": utc_now(),
                    "before_state": self._before_state,
                },
            )
        )
        self._active = True
        return self

    def update_metadata(self, metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if metadata:
            self.metadata.update(metadata)
        self.metadata.update(kwargs)

    def commit(self) -> TransactionReportResult:
        if self._finished:
            return TransactionReportResult(report_file=self.report_file)
        committed_at = utc_now()
        payload = self._payload(
            status="applied",
            report_type="canonical_write_transaction_report_v3",
            extra={
                **self._snapshot_payload(cleanup_complete=False),
                "snapshots_retained": True,
                "cleanup_errors": [],
                "committed_at": committed_at,
                "before_state": self._before_state,
                "after_state": transaction_paths_state(self.root, self.touched_paths),
            },
        )
        self._write_report(payload)
        cleanup_errors = cleanup_transaction_snapshot(self.snapshot_dir)
        payload.update(
            {
                "snapshots_retained": bool(cleanup_errors),
                "cleanup_complete": not cleanup_errors,
                "cleanup_errors": cleanup_errors,
                "cleanup_finished_at": utc_now(),
            }
        )
        self._write_report(payload)
        self._finished = True
        return TransactionReportResult(report_file=self.report_file)

    def rollback(self, exc: object | None = None) -> TransactionReportResult:
        if self._finished:
            return TransactionReportResult(report_file=self.rollback_file)
        restored: list[str] = []
        restored_databases: list[str] = []
        restore_errors: list[str] = []
        for item in reversed(self._snapshots):
            try:
                restore_transaction_path(self.root, self.snapshot_dir, item)
                restored.append(str(item.get("path") or ""))
            except (OSError, StorageError) as restore_exc:
                restore_errors.append(f"{item.get('path')}: {restore_exc}")
        for item in reversed(self._sqlite_backups):
            try:
                restore_sqlite_database(self.root, self.snapshot_dir, item)
                restored_databases.append(str(item.get("path") or ""))
            except (OSError, sqlite3.Error, StorageError) as restore_exc:
                restore_errors.append(f"{item.get('path')}: {restore_exc}")
        cleanup_errors: list[str] = []
        if not restore_errors:
            cleanup_errors = cleanup_transaction_snapshot(self.snapshot_dir)
        cleanup_complete = not restore_errors and not cleanup_errors
        error_payload = {
            "type": exc.__class__.__name__ if exc is not None else "",
            "message": str(exc) if exc is not None else "",
        }
        payload = self._payload(
            status="rolled_back" if cleanup_complete else "recovery_failed",
            report_type="canonical_write_transaction_rollback_v3",
            extra={
                "snapshot_dir": project_relative_path(self.root, self.snapshot_dir),
                "snapshots": self._snapshots,
                "sqlite_backups": self._sqlite_backups,
                "restored_paths": restored,
                "restored_databases": restored_databases,
                "restore_errors": restore_errors,
                "cleanup_complete": cleanup_complete,
                "cleanup_errors": cleanup_errors,
                "snapshots_retained": not cleanup_complete,
                "error": error_payload,
                "before_state": self._before_state,
                "after_state": transaction_paths_state(self.root, self.touched_paths),
                "rolled_back_at": utc_now(),
            },
        )
        self._write_report(payload)
        atomic_write_text(self.rollback_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self._finished = True
        return TransactionReportResult(report_file=self.rollback_file)

    def _payload(self, *, status: str, report_type: str, extra: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "schema": "canonical_write_transaction_report_v3",
            "schema_version": 3,
            "report_type": report_type,
            "status": status,
            "command": self.command,
            "chapter_number": self.chapter_number,
            "source_paths": [project_relative_path(self.root, path) for path in self.source_paths],
            "touched_paths": [project_relative_path(self.root, path) for path in self.touched_paths],
            "boundary": {
                "agent_outputs_directly_applied": False,
                "canonical_mutation_requires_apply_or_finalize": True,
                "rollback_restores_touched_paths": True,
                "sqlite_uses_backup_participant": True,
            },
            "metadata": self.metadata,
            "created_at": self.created_at,
        }
        payload.update(extra)
        return payload

    def _snapshot_payload(self, *, cleanup_complete: bool) -> dict[str, Any]:
        return {
            "snapshot_dir": project_relative_path(self.root, self.snapshot_dir),
            "inventory_targets": {
                "filesystem": [
                    project_relative_path(self.root, path) for path in self._filesystem_paths
                ],
                "sqlite": [
                    project_relative_path(self.root, path) for path in self._sqlite_paths
                ],
            },
            "snapshots": self._snapshots,
            "sqlite_backups": self._sqlite_backups,
            "snapshots_retained": self.snapshot_dir.exists(),
            "cleanup_complete": cleanup_complete,
        }

    def _write_report(self, payload: dict[str, Any]) -> None:
        atomic_write_text(self.report_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class ProjectLock:
    """Simple filesystem lock for project-mutating commands."""

    def __init__(self, path: Path, metadata: dict[str, Any]) -> None:
        self.path = path
        self.metadata = metadata
        self._acquired = False

    def acquire(self) -> "ProjectLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.metadata, ensure_ascii=False, indent=2) + "\n"
        try:
            descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            existing = _read_lock_metadata(self.path)
            raise StorageError(f"Project lock already exists: {self.path} ({existing.get('owner', 'unknown')})") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        self._acquired = True
        return self

    def release(self) -> None:
        if self._acquired and self.path.exists():
            existing = _read_lock_metadata(self.path)
            if existing.get("owner_token") == self.metadata.get("owner_token"):
                self.path.unlink()
        self._acquired = False

    def __enter__(self) -> "ProjectLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release()


def resolve_project_root(config: ConfigDocument, output: str | Path | None = None) -> Path:
    """Resolve the novel project root from CLI output or project.root_dir."""

    if output:
        return Path(output).expanduser().resolve()
    root_dir = Path(str(config.data["project"]["root_dir"])).expanduser()
    if root_dir.is_absolute():
        return root_dir
    base = config.path.parent if config.path else Path.cwd()
    return (base / root_dir).resolve()


def init_project(
    config: ConfigDocument,
    *,
    output: str | Path | None = None,
    force: bool = False,
) -> ProjectInitResult:
    """Create the canonical project layout and seed files."""

    root = resolve_project_root(config, output)
    created_dirs: list[Path] = []
    created_files: list[Path] = []

    for directory in [*BASE_DIRECTORIES, *SUBDIRECTORIES]:
        path = root / directory
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created_dirs.append(path)

    project_data = _config_for_project_file(config.data, root)
    project_config = root / "project.yaml"
    if force or not project_config.exists():
        atomic_write_text(project_config, yaml.safe_dump(project_data, allow_unicode=True, sort_keys=False))
        created_files.append(project_config)

    for relative_path, text_content in INITIAL_TEXT_FILES.items():
        path = root / relative_path
        if force or not path.exists():
            atomic_write_text(path, text_content)
            created_files.append(path)

    for relative_path, json_payload in INITIAL_JSON_FILES.items():
        path = root / relative_path
        if force or not path.exists():
            atomic_write_text(path, json.dumps(json_payload, ensure_ascii=False, indent=2) + "\n")
            created_files.append(path)

    for relative_path, jsonl_content in INITIAL_JSONL_FILES.items():
        path = root / relative_path
        if force or not path.exists():
            atomic_write_text(path, jsonl_content)
            created_files.append(path)

    return ProjectInitResult(
        root=root,
        created_dirs=tuple(created_dirs),
        created_files=tuple(created_files),
        project_config=project_config,
    )


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write text to a file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(text)
        temp_path = Path(handle.name)
    temp_path.replace(path)


def apply_transaction(
    root: Path,
    *,
    command: str,
    chapter_number: int | None = None,
    source_paths: tuple[str | Path, ...] | list[str | Path] = (),
    touched_paths: tuple[str | Path, ...] | list[str | Path] = (),
    metadata: dict[str, Any] | None = None,
) -> ApplyTransaction:
    """Create a canonical apply transaction with rollback-on-exception semantics."""

    return ApplyTransaction(
        root,
        command=command,
        chapter_number=chapter_number,
        source_paths=source_paths,
        touched_paths=touched_paths,
        metadata=metadata,
    )


def acquire_project_lock(
    config: ConfigDocument,
    *,
    owner: str = "longform-engine",
    command: str = "unknown",
    output: str | Path | None = None,
) -> ProjectLock:
    """Create a project-scoped lock that is removed when released."""

    root = resolve_project_root(config, output)
    return acquire_named_lock(root, "project.lock", owner=owner, command=command)


def acquire_named_lock(root: Path, name: str, *, owner: str, command: str) -> ProjectLock:
    if Path(name).name != name or not name.endswith(".lock"):
        raise StorageError(f"Invalid project lock name: {name}")
    metadata = {
        "schema": "project_lock_v2",
        "schema_version": 2,
        "owner": owner,
        "owner_token": secrets.token_hex(16),
        "command": command,
        "created_at": utc_now(),
        "root": str(root),
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "process_identity": process_start_identity(os.getpid()),
    }
    return ProjectLock(root / "70_runtime" / "locks" / name, metadata)


def snapshot_project(
    config: ConfigDocument,
    *,
    label: str = "manual",
    include: tuple[str, ...] | None = None,
) -> SnapshotResult:
    """Create a lightweight snapshot of project state and manuscript files."""

    root = resolve_project_root(config)
    safe_label = _safe_label(label)
    snapshot_dir = root / "70_runtime" / "snapshots" / f"{utc_timestamp()}_{safe_label}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    include = include or (
        "00_governance",
        "10_bible",
        "20_outline",
        "30_state",
        "40_manuscript",
        "50_workbench/gate_artifacts",
        "60_rag/context",
    )
    copied: list[Path] = []
    for relative in include:
        source = root / relative
        if not source.exists():
            continue
        target = snapshot_dir / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        copied.append(target)
    manifest = {
        "label": label,
        "root": str(root),
        "include": list(include),
        "copied_paths": [str(path.relative_to(snapshot_dir)).replace("\\", "/") for path in copied],
        "created_at": utc_now(),
    }
    atomic_write_text(snapshot_dir / "snapshot_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return SnapshotResult(snapshot_dir=snapshot_dir, copied_paths=tuple(copied))


def _config_for_project_file(data: dict[str, Any], root: Path) -> dict[str, Any]:
    cloned = json.loads(json.dumps(data, ensure_ascii=False))
    cloned.setdefault("project", {})["root_dir"] = str(root)
    return cloned


def _read_lock_metadata(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def project_relative_path(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path).replace("\\", "/")


def dedupe_project_paths(root: Path, paths: Sequence[str | Path]) -> list[Path]:
    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        path = resolve_project_transaction_path(root, raw)
        key = path.as_posix().lower()
        if key not in seen:
            seen.add(key)
            resolved.append(path)

    # A parent directory snapshot already owns rollback for every child path.
    result: list[Path] = []
    for path in sorted(resolved, key=lambda item: len(item.parts)):
        if any(path == parent or parent in path.parents for parent in result):
            continue
        result.append(path)
    return result


def resolve_project_transaction_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.expanduser().resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise StorageError(f"Transaction path must live under project root: {path}") from exc
    return resolved


def snapshot_transaction_path(root: Path, snapshot_dir: Path, path: Path) -> dict[str, Any]:
    relative = project_relative_path(root, path)
    object_name = sha256(relative.encode("utf-8")).hexdigest()[:20]
    snapshot = snapshot_dir / "objects" / object_name
    item = {
        "path": relative,
        "existed": path.exists(),
        "kind": "missing",
        "snapshot_path": project_relative_path(root, snapshot),
    }
    if not path.exists():
        return item
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        shutil.copytree(path, snapshot, dirs_exist_ok=True)
        item["kind"] = "dir"
    else:
        shutil.copy2(path, snapshot)
        item["kind"] = "file"
    return item


def restore_transaction_path(root: Path, snapshot_dir: Path, item: dict[str, Any]) -> None:
    relative = str(item.get("path") or "")
    if not relative:
        return
    target = root / relative
    existed = bool(item.get("existed"))
    kind = str(item.get("kind") or "missing")
    snapshot_relative = str(item.get("snapshot_path") or "")
    snapshot = root / snapshot_relative if snapshot_relative else snapshot_dir / relative
    try:
        snapshot.resolve().relative_to(snapshot_dir.resolve())
    except ValueError as exc:
        raise StorageError(f"Transaction snapshot escaped its snapshot directory: {snapshot}") from exc
    remove_path(target)
    if not existed:
        return
    if kind == "dir":
        if snapshot.exists():
            shutil.copytree(snapshot, target, dirs_exist_ok=True)
        return
    if snapshot.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snapshot, target)


def partition_transaction_paths(root: Path, paths: list[Path]) -> tuple[list[Path], list[Path]]:
    """Separate ordinary files from SQLite participants without snapshotting the DB directory."""

    filesystem_paths: list[Path] = []
    sqlite_paths: list[Path] = []
    runtime_db_dir = (root / "70_runtime" / "db").resolve()
    for path in paths:
        resolved = path.resolve()
        if resolved == runtime_db_dir:
            for pattern in ("*.sqlite", "*.sqlite3", "*.db"):
                sqlite_paths.extend(sorted(runtime_db_dir.glob(pattern)))
            sqlite_paths.extend((runtime_db_dir / "longform_engine.sqlite", runtime_db_dir / "vector_store.sqlite"))
        elif resolved.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
            sqlite_paths.append(resolved)
        else:
            filesystem_paths.append(resolved)
    return filesystem_paths, dedupe_project_paths(root, sqlite_paths)


def snapshot_sqlite_database(root: Path, snapshot_dir: Path, path: Path) -> dict[str, Any]:
    relative = project_relative_path(root, path)
    backup = snapshot_dir / "sqlite" / f"{sha256(relative.encode('utf-8')).hexdigest()[:20]}.sqlite"
    item = {
        "path": relative,
        "existed": path.is_file(),
        "kind": "sqlite_backup",
        "backup_path": project_relative_path(root, backup),
        "pages_per_step": 256,
    }
    if not path.is_file():
        return item
    backup.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(path)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination, pages=256)
    finally:
        destination.close()
        source.close()
    item["backup_bytes"] = backup.stat().st_size
    return item


def restore_sqlite_database(root: Path, snapshot_dir: Path, item: dict[str, Any]) -> None:
    relative = str(item.get("path") or "")
    if not relative:
        raise StorageError("SQLite transaction participant is missing its project path.")
    target = (root / relative).resolve()
    target.relative_to(root.resolve())
    if not item.get("existed"):
        for suffix in ("", "-wal", "-shm", "-journal"):
            candidate = Path(str(target) + suffix)
            if candidate.exists():
                candidate.unlink()
        return
    backup_relative = str(item.get("backup_path") or "")
    backup = (root / backup_relative).resolve()
    try:
        backup.relative_to(snapshot_dir.resolve())
    except ValueError as exc:
        raise StorageError(f"SQLite backup escaped its transaction directory: {backup}") from exc
    if not backup.is_file():
        raise StorageError(f"SQLite transaction backup is missing: {backup}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Backup into the live database rather than replacing it. In particular, do
    # not unlink WAL/SHM before opening the destination: Windows may briefly keep
    # those handles alive after a committed connection closes, while SQLite can
    # still checkpoint and restore the database safely through its own API.
    source = sqlite3.connect(backup)
    destination = sqlite3.connect(target)
    try:
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        source.backup(destination, pages=256)
        destination.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        journal_mode = destination.execute("PRAGMA journal_mode = DELETE").fetchone()
        if journal_mode is None or str(journal_mode[0]).lower() != "delete":
            raise StorageError(f"Restored SQLite database could not leave WAL mode: {target}")
        integrity = destination.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or str(integrity[0]).lower() != "ok":
            raise StorageError(f"Restored SQLite database failed integrity_check: {target}")
    finally:
        destination.close()
        source.close()
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def cleanup_transaction_snapshot(snapshot_dir: Path) -> list[str]:
    if not snapshot_dir.exists():
        return []
    try:
        shutil.rmtree(snapshot_dir)
    except OSError as exc:
        return [str(exc)]
    return []


def transaction_paths_state(root: Path, paths: list[Path]) -> list[dict[str, Any]]:
    return [transaction_path_state(root, path) for path in paths]


def transaction_path_state(root: Path, path: Path) -> dict[str, Any]:
    relative = project_relative_path(root, path)
    if not path.exists():
        return {"path": relative, "kind": "missing", "sha256": "", "bytes": 0}
    if path.is_file():
        return {
            "path": relative,
            "kind": "file",
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }
    records = []
    total_bytes = 0
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        digest = sha256(child.read_bytes()).hexdigest()
        size = child.stat().st_size
        total_bytes += size
        records.append(f"{child.relative_to(path).as_posix()}:{size}:{digest}")
    return {
        "path": relative,
        "kind": "dir",
        "sha256": sha256("\n".join(records).encode("utf-8")).hexdigest(),
        "bytes": total_bytes,
        "files": len(records),
    }


def process_start_identity(pid: int) -> str:
    if pid <= 0:
        return ""
    if os.name == "nt":
        return windows_process_start_identity(pid)
    stat_path = Path("/proc") / str(pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    command_end = raw.rfind(")")
    if command_end < 0:
        return ""
    fields_after_command = raw[command_end + 1 :].split()
    return fields_after_command[19] if len(fields_after_command) > 19 else ""


def windows_process_start_identity(pid: int) -> str:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        )
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        process = kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return ""
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        try:
            ok = kernel32.GetProcessTimes(
                process,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not ok:
                return ""
            return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        finally:
            kernel32.CloseHandle(process)
    except (AttributeError, OSError, TypeError, ValueError):
        return ""


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def unique_report_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index:03d}{suffix}")
        if not candidate.exists():
            return candidate
    raise StorageError(f"Could not allocate transaction report path under {path.parent}")


def safe_file_token(value: str) -> str:
    token = "".join(char.lower() if char.isalnum() else "_" for char in value.strip())
    token = "_".join(part for part in token.split("_") if part)
    return token or "transaction"


def _safe_label(label: str) -> str:
    safe = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in label.strip())
    return safe or "snapshot"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def utc_transaction_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
