"""Chapter audit archives and successful transaction snapshot cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
import json
import os
import re
import shutil
import tempfile
import zipfile

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


CHAPTER_PATTERN = re.compile(r"(?:^|[._/-])ch0*(\d+)(?:[._/-]|$)", re.IGNORECASE)
ARCHIVE_ROOT = "70_runtime/artifacts/chapters"
SCAN_ROOTS = ("50_workbench", "70_runtime/run_reports")


@dataclass(frozen=True)
class ArtifactStatusResult:
    loose_files: int
    loose_bytes: int
    archive_files: int
    archive_bytes: int
    committed_snapshot_dirs: int
    committed_snapshot_bytes: int
    orphan_task_artifacts: int
    orphan_task_files: tuple[str, ...]
    root: str


@dataclass(frozen=True)
class ArtifactCompactResult:
    through: int
    dry_run: bool
    candidate_files: int
    candidate_bytes: int
    removed_files: int
    removed_bytes: int
    committed_snapshots: int
    committed_snapshot_bytes: int
    archive_files: tuple[str, ...]
    manifest_files: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactVerifyResult:
    ok: bool
    archives: int
    entries: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactRestoreResult:
    chapter_number: int
    archive_file: str
    restored_files: tuple[str, ...]
    skipped_files: tuple[str, ...]


def artifact_status(config: ConfigDocument) -> ArtifactStatusResult:
    root = resolve_project_root(config)
    archive_dir = root / ARCHIVE_ROOT
    archive_paths = list(archive_dir.glob("ch*.zip")) if archive_dir.exists() else []
    snapshots = committed_snapshot_paths(root)
    loose_paths = [path for path in root.rglob("*") if path.is_file() and archive_dir not in path.parents]
    orphans = orphan_agent_task_artifacts(root)
    return ArtifactStatusResult(
        loose_files=len(loose_paths),
        loose_bytes=sum(path.stat().st_size for path in loose_paths),
        archive_files=len(archive_paths),
        archive_bytes=sum(path.stat().st_size for path in archive_paths),
        committed_snapshot_dirs=len(snapshots),
        committed_snapshot_bytes=sum(directory_size(path) for path in snapshots),
        orphan_task_artifacts=len(orphans),
        orphan_task_files=tuple(relative_path(root, path) for path in orphans),
        root=str(root),
    )


def orphan_agent_task_artifacts(root: Path) -> list[Path]:
    """Report work orders left behind when a manifest contract failed before registration."""

    result: list[Path] = []
    for task_file in (root / "50_workbench").rglob("*.md"):
        if task_file.parent.name == "writing_tasks" and re.fullmatch(r"ch\d+\.md", task_file.name):
            manifest = task_file.with_name(f"{task_file.stem}.agent_task.json")
        elif task_file.name.endswith("_task.md") or task_file.name.endswith(".repair_task.md"):
            manifest = task_file.with_name(f"{task_file.stem}.agent_task.json")
        else:
            continue
        if manifest.exists():
            continue
        result.append(task_file)
        context = task_file.with_name(task_file.name.replace("_task.md", "_context.json"))
        if context.exists():
            result.append(context)
    return sorted(set(result))


def compact_artifacts(config: ConfigDocument, *, through: int, dry_run: bool = True) -> ArtifactCompactResult:
    if through < 0:
        raise ValueError("through must be zero or positive.")
    root = resolve_project_root(config)
    candidates = chapter_candidates(root, through)
    snapshots = committed_snapshot_paths(root)
    snapshot_bytes = sum(directory_size(path) for path in snapshots)
    archive_files: list[str] = []
    manifest_files: list[str] = []
    removed_files = 0
    removed_bytes = 0

    if not dry_run:
        ensure_compaction_boundary(root, through)
        by_chapter: dict[int, list[Path]] = {}
        for chapter_number, path in candidates:
            by_chapter.setdefault(chapter_number, []).append(path)
        for chapter_number, paths in sorted(by_chapter.items()):
            archive_file, manifest_file = write_chapter_archive(root, chapter_number, paths)
            archive_files.append(str(archive_file))
            manifest_files.append(str(manifest_file))
            for path in paths:
                if path.exists():
                    removed_bytes += path.stat().st_size
                    path.unlink()
                    removed_files += 1
            remove_empty_workbench_dirs(root)
        for snapshot in snapshots:
            shutil.rmtree(snapshot)

    return ArtifactCompactResult(
        through=through,
        dry_run=dry_run,
        candidate_files=len(candidates),
        candidate_bytes=sum(path.stat().st_size for _chapter, path in candidates if path.exists()),
        removed_files=removed_files,
        removed_bytes=removed_bytes,
        committed_snapshots=len(snapshots),
        committed_snapshot_bytes=snapshot_bytes,
        archive_files=tuple(archive_files),
        manifest_files=tuple(manifest_files),
    )


def ensure_compaction_boundary(root: Path, through: int) -> None:
    if through == 0:
        return
    closure_dir = root / "30_state" / "chapter_closures"
    closed_chapters = {
        chapter_from_path(path.name)
        for path in closure_dir.glob("ch*.json")
        if chapter_from_path(path.name) > 0
    }
    missing = [chapter for chapter in range(1, through + 1) if chapter not in closed_chapters]
    if missing:
        rendered = ", ".join(f"ch{chapter:03d}" for chapter in missing[:5])
        raise ValueError(f"Cannot compact chapters without closure records: {rendered}.")
    last_closed = max(closed_chapters, default=0)
    active_buffer_start = max(1, last_closed - 1)
    if through >= active_buffer_start:
        raise ValueError(
            f"Cannot compact through ch{through:03d}: ch{active_buffer_start:03d}-ch{last_closed:03d} "
            "are the two-chapter active buffer."
        )


def verify_artifacts(config: ConfigDocument) -> ArtifactVerifyResult:
    root = resolve_project_root(config)
    errors: list[str] = []
    entries = 0
    archives = sorted((root / ARCHIVE_ROOT).glob("ch*.zip"))
    for archive in archives:
        manifest_path = archive.with_suffix(".manifest.json")
        manifest = read_json(manifest_path, {})
        if not isinstance(manifest, dict):
            errors.append(f"Missing or invalid manifest: {relative_path(root, manifest_path)}")
            continue
        if str(manifest.get("archive_sha256") or "") != file_hash(archive):
            errors.append(f"Archive hash mismatch: {relative_path(root, archive)}")
            continue
        expected = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
        with zipfile.ZipFile(archive, "r") as handle:
            names = set(handle.namelist())
            for item in expected:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "")
                entries += 1
                if path not in names:
                    errors.append(f"Archive entry missing: {path}")
                    continue
                if sha256(handle.read(path)).hexdigest() != str(item.get("sha256") or ""):
                    errors.append(f"Archive entry hash mismatch: {path}")
    return ArtifactVerifyResult(ok=not errors, archives=len(archives), entries=entries, errors=tuple(errors))


def restore_artifacts(config: ConfigDocument, *, chapter_number: int) -> ArtifactRestoreResult:
    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    archive = root / ARCHIVE_ROOT / f"ch{chapter_number:03d}.zip"
    manifest = read_json(archive.with_suffix(".manifest.json"), {})
    if not archive.exists() or not isinstance(manifest, dict):
        raise ValueError(f"No verified artifact archive exists for ch{chapter_number:03d}.")
    verification = verify_single_archive(root, archive, manifest)
    if verification:
        raise ValueError("Artifact archive verification failed: " + "; ".join(verification))
    restored: list[str] = []
    skipped: list[str] = []
    with zipfile.ZipFile(archive, "r") as handle:
        for item in manifest.get("entries", []):
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path") or "")
            ensure_safe_archive_path(relative)
            target = (root / relative).resolve()
            target.relative_to(root.resolve())
            data = handle.read(relative)
            if target.exists():
                if sha256(target.read_bytes()).hexdigest() == str(item.get("sha256") or ""):
                    skipped.append(relative)
                    continue
                raise ValueError(f"Restore would overwrite a different existing file: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target, data)
            restored.append(relative)
    return ArtifactRestoreResult(
        chapter_number=chapter_number,
        archive_file=str(archive),
        restored_files=tuple(restored),
        skipped_files=tuple(skipped),
    )


def chapter_candidates(root: Path, through: int) -> list[tuple[int, Path]]:
    candidates: list[tuple[int, Path]] = []
    for scan_root in SCAN_ROOTS:
        directory = root / scan_root
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.name in {"agent_task_index.json", "events.jsonl"}:
                continue
            chapter_number = chapter_from_path(relative_path(root, path))
            if chapter_number and chapter_number <= through:
                candidates.append((chapter_number, path))
    return sorted(candidates, key=lambda item: (item[0], relative_path(root, item[1])))


def write_chapter_archive(root: Path, chapter_number: int, paths: list[Path]) -> tuple[Path, Path]:
    archive_dir = root / ARCHIVE_ROOT
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / f"ch{chapter_number:03d}.zip"
    manifest_file = archive.with_suffix(".manifest.json")
    if archive.exists():
        existing = read_json(manifest_file, {})
        if not isinstance(existing, dict):
            raise ValueError(f"Archive ch{chapter_number:03d} exists without a valid manifest.")
        verification_errors = verify_single_archive(root, archive, existing)
        if verification_errors:
            raise ValueError(
                f"Archive ch{chapter_number:03d} failed verification before compaction: "
                + "; ".join(verification_errors)
            )
        existing_entries = existing.get("entries", []) if isinstance(existing, dict) else []
        existing_by_path = {
            str(item.get("path")): item
            for item in existing_entries
            if isinstance(item, dict)
        }
        new_paths = {relative_path(root, path) for path in paths}
        if not new_paths <= set(existing_by_path):
            raise ValueError(f"Archive ch{chapter_number:03d} is immutable and does not contain all new candidates.")
        for path in paths:
            relative = relative_path(root, path)
            if file_hash(path) != str(existing_by_path[relative].get("sha256") or ""):
                raise ValueError(
                    f"Archive ch{chapter_number:03d} contains an older version of loose artifact {relative}."
                )
        return archive, manifest_file

    entries = [
        {
            "path": relative_path(root, path),
            "sha256": file_hash(path),
            "size": path.stat().st_size,
        }
        for path in paths
    ]
    descriptor, temp_name = tempfile.mkstemp(prefix=f"ch{chapter_number:03d}.", suffix=".zip", dir=archive_dir)
    os.close(descriptor)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
            for path in paths:
                handle.write(path, arcname=relative_path(root, path))
        temp.replace(archive)
    finally:
        if temp.exists():
            temp.unlink()
    manifest = {
        "schema": "chapter_artifact_archive_v1",
        "chapter_number": chapter_number,
        "archive": relative_path(root, archive),
        "archive_sha256": file_hash(archive),
        "entries": entries,
        "entry_count": len(entries),
        "uncompressed_bytes": sum(int(item["size"]) for item in entries),
        "created_at": utc_now(),
    }
    atomic_write_text(manifest_file, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    verification_errors = verify_single_archive(root, archive, manifest)
    if verification_errors:
        archive.unlink(missing_ok=True)
        manifest_file.unlink(missing_ok=True)
        raise ValueError(
            f"Archive ch{chapter_number:03d} failed verification before loose files were removed: "
            + "; ".join(verification_errors)
        )
    return archive, manifest_file


def committed_snapshot_paths(root: Path) -> list[Path]:
    result: list[Path] = []
    report_dir = root / "70_runtime" / "transactions"
    for report in report_dir.glob("*.json") if report_dir.exists() else []:
        payload = read_json(report, {})
        if not isinstance(payload, dict) or payload.get("status") != "applied":
            continue
        snapshot_dir = str(payload.get("snapshot_dir") or "")
        if not snapshot_dir:
            continue
        path = (root / snapshot_dir).resolve()
        try:
            path.relative_to((report_dir / "s").resolve())
        except ValueError:
            continue
        if path.exists() and path.is_dir() and path not in result:
            result.append(path)
    return result


def verify_single_archive(root: Path, archive: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if file_hash(archive) != str(manifest.get("archive_sha256") or ""):
        errors.append("archive hash mismatch")
        return errors
    with zipfile.ZipFile(archive, "r") as handle:
        names = set(handle.namelist())
        for item in manifest.get("entries", []):
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            try:
                ensure_safe_archive_path(path)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if path not in names:
                errors.append(f"missing {path}")
            elif sha256(handle.read(path)).hexdigest() != str(item.get("sha256") or ""):
                errors.append(f"hash mismatch {path}")
    return errors


def ensure_safe_archive_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe archive entry path: {value}")


def chapter_from_path(value: str) -> int:
    match = CHAPTER_PATTERN.search(value.replace("\\", "/"))
    return int(match.group(1)) if match else 0


def remove_empty_workbench_dirs(root: Path) -> None:
    for scan_root in SCAN_ROOTS:
        directory = root / scan_root
        if not directory.exists():
            continue
        for path in sorted((item for item in directory.rglob("*") if item.is_dir()), key=lambda item: len(item.parts), reverse=True):
            try:
                path.rmdir()
            except OSError:
                pass


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        Path(temp_name).replace(path)
    finally:
        temp = Path(temp_name)
        if temp.exists():
            temp.unlink()


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
