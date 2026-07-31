"""Initialize and inspect longform novel project directories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os
import shutil
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
    """Filesystem transaction for canonical apply/finalize commands."""

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
        self.snapshot_dir = self.report_dir / "s" / snapshot_id
        self._snapshots: list[dict[str, Any]] = []
        self._active = False
        self._finished = False

    def __enter__(self) -> "ApplyTransaction":
        self.begin()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
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
        for path in self.touched_paths:
            self._snapshots.append(snapshot_transaction_path(self.root, self.snapshot_dir, path))
        self._active = True
        return self

    def update_metadata(self, metadata: dict[str, Any] | None = None, **kwargs: Any) -> None:
        if metadata:
            self.metadata.update(metadata)
        self.metadata.update(kwargs)

    def commit(self) -> TransactionReportResult:
        if self._finished:
            return TransactionReportResult(report_file=self.report_file)
        payload = self._payload(
            status="applied",
            report_type="canonical_write_transaction_report",
            extra={"snapshot_dir": project_relative_path(self.root, self.snapshot_dir), "snapshots": self._snapshots},
        )
        atomic_write_text(self.report_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self._finished = True
        return TransactionReportResult(report_file=self.report_file)

    def rollback(self, exc: object | None = None) -> TransactionReportResult:
        if self._finished:
            return TransactionReportResult(report_file=self.rollback_file)
        restored: list[str] = []
        restore_errors: list[str] = []
        for item in reversed(self._snapshots):
            try:
                restore_transaction_path(self.root, self.snapshot_dir, item)
                restored.append(str(item.get("path") or ""))
            except OSError as restore_exc:
                restore_errors.append(f"{item.get('path')}: {restore_exc}")
        error_payload = {
            "type": exc.__class__.__name__ if exc is not None else "",
            "message": str(exc) if exc is not None else "",
        }
        payload = self._payload(
            status="rolled_back",
            report_type="canonical_write_transaction_rollback",
            extra={
                "snapshot_dir": project_relative_path(self.root, self.snapshot_dir),
                "snapshots": self._snapshots,
                "restored_paths": restored,
                "restore_errors": restore_errors,
                "error": error_payload,
            },
        )
        atomic_write_text(self.rollback_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        self._finished = True
        return TransactionReportResult(report_file=self.rollback_file)

    def _payload(self, *, status: str, report_type: str, extra: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "schema_version": 1,
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
            },
            "metadata": self.metadata,
            "created_at": utc_now(),
        }
        payload.update(extra)
        return payload


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

    for relative_path, content in INITIAL_TEXT_FILES.items():
        path = root / relative_path
        if force or not path.exists():
            atomic_write_text(path, content)
            created_files.append(path)

    for relative_path, content in INITIAL_JSON_FILES.items():
        path = root / relative_path
        if force or not path.exists():
            atomic_write_text(path, json.dumps(content, ensure_ascii=False, indent=2) + "\n")
            created_files.append(path)

    for relative_path, content in INITIAL_JSONL_FILES.items():
        path = root / relative_path
        if force or not path.exists():
            atomic_write_text(path, content)
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


def record_transaction_report(
    root: Path,
    *,
    command: str,
    chapter_number: int | None = None,
    source_paths: tuple[str | Path, ...] | list[str | Path] = (),
    touched_paths: tuple[str | Path, ...] | list[str | Path] = (),
    status: str = "applied",
    metadata: dict[str, Any] | None = None,
) -> TransactionReportResult:
    """Write a lightweight audit report for an apply/finalize canonical write."""

    report_dir = root / "70_runtime" / "transactions"
    report_dir.mkdir(parents=True, exist_ok=True)
    name_parts = [utc_transaction_timestamp(), safe_file_token(command)]
    if chapter_number is not None:
        name_parts.append(f"ch{chapter_number:03d}")
    report_file = unique_report_path(report_dir / ("_".join(name_parts) + ".json"))
    payload = {
        "schema_version": 1,
        "report_type": "canonical_write_transaction_report",
        "status": status,
        "command": command,
        "chapter_number": chapter_number,
        "source_paths": [project_relative_path(root, path) for path in source_paths],
        "touched_paths": [project_relative_path(root, path) for path in touched_paths],
        "boundary": {
            "agent_outputs_directly_applied": False,
            "canonical_mutation_requires_apply_or_finalize": True,
        },
        "metadata": metadata or {},
        "created_at": utc_now(),
    }
    atomic_write_text(report_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return TransactionReportResult(report_file=report_file)


def acquire_project_lock(
    config: ConfigDocument,
    *,
    owner: str = "longform-engine",
    command: str = "unknown",
    output: str | Path | None = None,
) -> ProjectLock:
    """Create a project-scoped lock that is removed when released."""

    root = resolve_project_root(config, output)
    metadata = {
        "owner": owner,
        "command": command,
        "created_at": utc_now(),
        "root": str(root),
    }
    return ProjectLock(root / "70_runtime" / "locks" / "project.lock", metadata)


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


def dedupe_project_paths(root: Path, paths: tuple[str | Path, ...] | list[str | Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        path = resolve_project_transaction_path(root, raw)
        key = path.as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
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
    object_name = f"{sha256(relative.encode('utf-8')).hexdigest()[:20]}_{path.name}"
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
