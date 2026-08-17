"""Chapter audit archives and successful transaction snapshot cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
import json
import gzip
import os
import re
import shutil
import tempfile
import zipfile

from longform_engine.config import ConfigDocument
from longform_engine.agent_tasks import compact_task_projection, task_archive_projection
from longform_engine.storage import atomic_write_text, resolve_project_root


CHAPTER_PATTERN = re.compile(r"(?:^|[._/-])ch0*(\d+)(?:[._/-]|$)", re.IGNORECASE)
ARCHIVE_ROOT = "70_runtime/artifacts/chapters"
SCAN_ROOTS = (
    "30_state/tcs",
    "40_manuscript/draft",
    "40_manuscript/final",
    "40_manuscript/submitted",
    "50_workbench",
    "70_runtime/run_reports",
)
TRANSACTION_ROOT = "70_runtime/transactions"
ARCHIVE_SCHEMA = "chapter_artifact_archive_v3"
LEGACY_ARCHIVE_SCHEMAS = {
    "chapter_artifact_archive_v1",
    "chapter_artifact_archive_v2",
}
AUDIT_PAYLOAD_SCHEMA = "chapter_artifact_payload_v1"
AUDIT_MANIFEST_MEMBER = "_audit/manifest.json"
AUDIT_BLOB_PREFIX = "_audit/blobs/"
AUDIT_TASKS_MEMBER = "_audit/agent_tasks.json"
AUDIT_EVENTS_MEMBER = "_audit/agent_events.jsonl"
RETAINED_EVIDENCE = (
    ("final", "40_manuscript/final/ch{chapter:03d}.md"),
    ("semantic_ledger", "30_state/semantic_ledger/ch{chapter:03d}.json"),
    ("closure", "30_state/chapter_closures/ch{chapter:03d}.json"),
)
ARCHIVABLE_PREFIXES = (
    "30_state/tcs/",
    "40_manuscript/draft/",
    "40_manuscript/submitted/",
    "50_workbench/",
    "70_runtime/run_reports/",
)


@dataclass(frozen=True)
class ArtifactStatusResult:
    loose_files: int
    loose_bytes: int
    archive_files: int
    archive_bytes: int
    committed_snapshot_dirs: int
    committed_snapshot_bytes: int
    pending_transactions: int
    retained_failure_snapshots: int
    reclaimable_snapshot_bytes: int
    orphan_task_artifacts: int
    orphan_task_files: tuple[str, ...]
    archived_loose_duplicates: int
    archived_loose_duplicate_files: tuple[str, ...]
    active_buffer_chapters: tuple[int, ...]
    compacted_through: int
    root: str


@dataclass(frozen=True)
class ArtifactCompactResult:
    through: int
    dry_run: bool
    eligible: bool
    blockers: tuple[str, ...]
    compact_through: int
    active_buffer: tuple[int, ...]
    candidate_files: int
    candidate_bytes: int
    unique_content_files: int
    unique_content_bytes: int
    deduplicated_files: int
    removed_files: int
    removed_bytes: int
    committed_snapshots: int
    committed_snapshot_bytes: int
    reclaimable_snapshot_bytes: int
    archive_files: tuple[str, ...]
    manifest_files: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactVerifyResult:
    ok: bool
    status: str
    archives: int
    entries: int
    errors: tuple[str, ...]
    pending_close_chapters: tuple[int, ...]
    migration_required_chapters: tuple[int, ...]


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
    transaction_diagnostics = transaction_snapshot_diagnostics(root)
    loose_paths = [path for path in root.rglob("*") if path.is_file() and archive_dir not in path.parents]
    orphans = orphan_agent_task_artifacts(root)
    duplicate_files = archived_loose_files(root, archive_paths)
    closed = closed_chapter_numbers(root)
    active = tuple(sorted(closed)[-2:])
    compacted_through = max((chapter_from_archive(path) for path in archive_paths), default=0)
    return ArtifactStatusResult(
        loose_files=len(loose_paths),
        loose_bytes=sum(path.stat().st_size for path in loose_paths),
        archive_files=len(archive_paths),
        archive_bytes=sum(path.stat().st_size for path in archive_paths),
        committed_snapshot_dirs=len(snapshots),
        committed_snapshot_bytes=sum(directory_size(path) for path in snapshots),
        pending_transactions=len(transaction_diagnostics["pending"]),
        retained_failure_snapshots=len(transaction_diagnostics["retained_failures"]),
        reclaimable_snapshot_bytes=int(transaction_diagnostics["reclaimable_bytes"]),
        orphan_task_artifacts=len(orphans),
        orphan_task_files=tuple(relative_path(root, path) for path in orphans),
        archived_loose_duplicates=len(duplicate_files),
        archived_loose_duplicate_files=tuple(duplicate_files),
        active_buffer_chapters=active,
        compacted_through=compacted_through,
        root=str(root),
    )


def orphan_agent_task_artifacts(root: Path) -> list[Path]:
    """Report work orders left behind when a manifest contract failed before registration."""

    declared_inputs: set[str] = set()
    workbench = root / "50_workbench"
    for manifest_file in workbench.rglob("*.json") if workbench.exists() else []:
        if not (
            manifest_file.name.endswith(".agent_task.json")
            or manifest_file.parent.name == "agent_tasks" and manifest_file.name.endswith(".manifest.json")
        ):
            continue
        manifest = read_json(manifest_file, {})
        io = manifest.get("io") if isinstance(manifest, dict) and isinstance(manifest.get("io"), dict) else {}
        inputs = io.get("inputs") if isinstance(io.get("inputs"), list) else []
        declared_inputs.update(
            str(value.get("path") or "").replace("\\", "/")
            for value in inputs
            if isinstance(value, dict) and value.get("path")
        )

    result: list[Path] = []
    for task_file in workbench.rglob("*.md"):
        is_work_order = (
            task_file.parent.name == "writing_tasks" and re.fullmatch(r"ch\d+\.md", task_file.name)
        ) or task_file.name.endswith("_task.md") or task_file.name.endswith(".repair_task.md")
        if not is_work_order:
            continue
        if relative_path(root, task_file) in declared_inputs:
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
    blockers = compaction_blockers(root, through)
    candidates = chapter_candidates(root, through)
    snapshots = committed_snapshot_paths(root)
    snapshot_bytes = sum(directory_size(path) for path in snapshots)
    unique_content: dict[tuple[int, str], int] = {}
    for chapter_number, path in candidates:
        unique_content.setdefault((chapter_number, file_hash(path)), path.stat().st_size)
    retained_hashes: dict[int, set[str]] = {}
    for chapter_number in {chapter for chapter, _path in candidates}:
        for _role, template in RETAINED_EVIDENCE:
            retained = root / template.format(chapter=chapter_number)
            if retained.is_file():
                retained_hashes.setdefault(chapter_number, set()).add(file_hash(retained))
    stored_content = {
        key: size
        for key, size in unique_content.items()
        if key[1] not in retained_hashes.get(key[0], set())
    }
    archive_files: list[str] = []
    manifest_files: list[str] = []
    removed_files = 0
    removed_bytes = 0

    if not dry_run:
        if blockers:
            raise ValueError("Cannot compact artifacts: " + "; ".join(blockers))
        by_chapter: dict[int, list[Path]] = {chapter: [] for chapter in range(1, through + 1)}
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
        compact_task_projection(
            root,
            through=through,
            archive_refs={
                chapter_from_archive(Path(path)): relative_path(root, Path(path))
                for path in archive_files
            },
        )
        for snapshot in snapshots:
            shutil.rmtree(snapshot)

    return ArtifactCompactResult(
        through=through,
        dry_run=dry_run,
        eligible=not blockers,
        blockers=tuple(blockers),
        compact_through=through,
        active_buffer=tuple(sorted(closed_chapter_numbers(root))[-2:]),
        candidate_files=len(candidates),
        candidate_bytes=sum(path.stat().st_size for _chapter, path in candidates if path.exists()),
        unique_content_files=len(stored_content),
        unique_content_bytes=sum(stored_content.values()),
        deduplicated_files=len(candidates) - len(stored_content),
        removed_files=removed_files,
        removed_bytes=removed_bytes,
        committed_snapshots=len(snapshots),
        committed_snapshot_bytes=snapshot_bytes,
        reclaimable_snapshot_bytes=snapshot_bytes,
        archive_files=tuple(archive_files),
        manifest_files=tuple(manifest_files),
    )


def ensure_compaction_boundary(root: Path, through: int) -> None:
    blockers = compaction_blockers(root, through)
    if blockers:
        raise ValueError("Cannot compact artifacts: " + "; ".join(blockers))


def compaction_blockers(root: Path, through: int) -> list[str]:
    if through == 0:
        return []
    blockers: list[str] = []
    closed_chapters = set(closed_chapter_numbers(root))
    missing = [chapter for chapter in range(1, through + 1) if chapter not in closed_chapters]
    if missing:
        rendered = ", ".join(f"ch{chapter:03d}" for chapter in missing[:5])
        blockers.append(f"cannot compact chapters without closure records: {rendered}")
    last_closed = max(closed_chapters, default=0)
    active_buffer_start = max(1, last_closed - 1)
    if closed_chapters and through >= active_buffer_start:
        blockers.append(
            f"ch{active_buffer_start:03d}-ch{last_closed:03d} are the two-chapter active buffer"
        )
    return blockers


def verify_artifacts(config: ConfigDocument) -> ArtifactVerifyResult:
    root = resolve_project_root(config)
    errors: list[str] = []
    entries = 0
    archives = sorted((root / ARCHIVE_ROOT).glob("ch*.zip"))
    for archive in archives:
        manifest_path = archive.with_suffix(".manifest.json")
        manifest = read_json(manifest_path, {})
        if not isinstance(manifest, dict) or manifest.get("schema") not in {
            *LEGACY_ARCHIVE_SCHEMAS,
            ARCHIVE_SCHEMA,
        }:
            errors.append(f"Missing or invalid manifest: {relative_path(root, manifest_path)}")
            continue
        chapter_number = chapter_from_archive(archive)
        if int(manifest.get("chapter_number") or 0) != chapter_number:
            errors.append(f"Archive chapter mismatch: {relative_path(root, archive)}")
        expected = manifest.get("entries") if isinstance(manifest.get("entries"), list) else []
        entries += len(expected)
        errors.extend(
            f"{relative_path(root, archive)}: {error}"
            for error in verify_single_archive(root, archive, manifest)
        )

    closed = closed_chapter_numbers(root)
    active = set(closed[-2:])
    expected_archived = set(closed) - active
    archived = {chapter_from_archive(path) for path in archives}
    for chapter_number in sorted(expected_archived - archived):
        errors.append(f"Closed chapter is missing its audit archive: ch{chapter_number:03d}")
    for chapter_number in sorted(active & archived):
        errors.append(f"Active-buffer chapter must not be archived: ch{chapter_number:03d}")
    for relative in archived_loose_files(root, archives):
        errors.append(f"Archived artifact still exists as a loose duplicate: {relative}")
    errors.extend(verify_task_projection_state(root, archives))
    errors.extend(verify_event_segments(root))
    final_chapters = chapter_numbers_in(root / "40_manuscript" / "final", "*.md")
    ledger_chapters = chapter_numbers_in(root / "30_state" / "semantic_ledger", "*.json")
    closure_chapters = set(closed)
    unclosed = sorted(final_chapters - closure_chapters)
    pending_close: list[int] = []
    migration_required: list[int] = []
    if unclosed:
        latest = max(final_chapters)
        expected_prior = set(range(1, latest)) & final_chapters
        if unclosed == [latest] and expected_prior <= closure_chapters and latest in ledger_chapters:
            pending_close = [latest]
        else:
            migration_required = unclosed
    for chapter_number in sorted(closure_chapters):
        if chapter_number not in final_chapters or chapter_number not in ledger_chapters:
            errors.append(f"Closed chapter is missing final or semantic ledger evidence: ch{chapter_number:03d}")
    if errors:
        status = "invalid"
    elif migration_required:
        status = "migration_required"
    elif pending_close:
        status = "pending_close"
    else:
        status = "ok"
    return ArtifactVerifyResult(
        ok=status in {"ok", "pending_close"},
        status=status,
        archives=len(archives),
        entries=entries,
        errors=tuple(errors),
        pending_close_chapters=tuple(pending_close),
        migration_required_chapters=tuple(migration_required),
    )


def verify_task_projection_state(root: Path, archives: list[Path]) -> list[str]:
    errors: list[str] = []
    index_file = root / "50_workbench" / "agent_tasks" / "agent_task_index.json"
    index = read_json(index_file, {})
    if not index_file.exists():
        return errors
    if not isinstance(index, dict) or int(index.get("schema_version") or 0) != 4:
        return ["Agent task index is unreadable or unsupported"]
    if index.get("schema") != "agent_task_index_v4":
        errors.append("Agent task index schema is invalid")
    archived_chapters = {chapter_from_archive(path): path for path in archives}
    for task in index.get("tasks", []):
        if not isinstance(task, dict):
            errors.append("Agent task index contains a non-object task")
            continue
        chapter = int(task.get("chapter_number") or 0)
        if chapter in archived_chapters:
            errors.append(f"Archived chapter task remains in active index: ch{chapter:03d}")
    refs = index.get("archived_chapters") if isinstance(index.get("archived_chapters"), dict) else {}
    for chapter_text, record in refs.items():
        if not str(chapter_text).isdigit() or not isinstance(record, dict):
            errors.append("Agent task archived_chapters projection is malformed")
            continue
        archive = root / str(record.get("archive") or "")
        if not archive.is_file():
            errors.append(f"Agent task projection references a missing archive: {record.get('archive')}")
            continue
        manifest = read_json(archive.with_suffix(".manifest.json"), {})
        projection = manifest.get("agent_task_projection") if isinstance(manifest, dict) else None
        if not isinstance(projection, dict):
            errors.append(f"Agent task projection is missing from archive: {relative_path(root, archive)}")
        elif int(projection.get("task_count") or 0) != int(record.get("task_count") or 0):
            errors.append(f"Agent task projection count differs from index: {relative_path(root, archive)}")
    return errors


def verify_event_segments(root: Path) -> list[str]:
    manifest_path = root / "70_runtime" / "artifacts" / "events" / "segments.json"
    if not manifest_path.exists():
        return []
    manifest = read_json(manifest_path, {})
    if not isinstance(manifest, dict) or manifest.get("schema") != "agent_task_event_segments_v1":
        return ["Agent task event segment manifest is invalid"]
    errors: list[str] = []
    for record in manifest.get("segments", []):
        if not isinstance(record, dict):
            errors.append("Agent task event segment descriptor is invalid")
            continue
        path = root / str(record.get("path") or "")
        if not path.is_file() or file_hash(path) != str(record.get("sha256") or ""):
            errors.append(f"Agent task event segment hash mismatch: {record.get('path')}")
            continue
        try:
            with gzip.open(path, "rb") as handle:
                content = handle.read()
        except OSError as exc:
            errors.append(f"Agent task event segment is unreadable: {exc}")
            continue
        if sha256(content).hexdigest() != str(record.get("content_sha256") or ""):
            errors.append(f"Agent task event segment content hash mismatch: {record.get('path')}")
        if len(content.decode("utf-8").splitlines()) != int(record.get("lines") or 0):
            errors.append(f"Agent task event segment line count mismatch: {record.get('path')}")
    return errors


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
            ensure_archivable_chapter_path(relative, chapter_number)
            target = (root / relative).resolve()
            target.relative_to(root.resolve())
            data = read_archived_entry(root, handle, item, manifest)
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
            if scan_root == "40_manuscript/final" and re.fullmatch(r"ch\d+\.md", path.name):
                continue
            if path.name in {"agent_task_index.json", "events.jsonl"}:
                continue
            chapter_number = chapter_from_path(relative_path(root, path))
            if chapter_number and chapter_number <= through:
                candidates.append((chapter_number, path))
    transaction_dir = root / TRANSACTION_ROOT
    if transaction_dir.exists():
        for path in transaction_dir.glob("*.json"):
            payload = read_json(path, {})
            chapter_number = int(payload.get("chapter_number") or 0) if isinstance(payload, dict) else 0
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

    retained_evidence = retained_evidence_entries(root, chapter_number)
    task_projection = task_archive_projection(root, chapter_number)
    task_bytes = (json.dumps(task_projection.get("tasks", []), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    event_bytes = "".join(
        json.dumps(item, ensure_ascii=False) + "\n"
        for item in task_projection.get("events", [])
    ).encode("utf-8")
    retained_by_hash = {
        str(item["sha256"]): item
        for item in retained_evidence
    }
    entries: list[dict[str, Any]] = []
    blobs: dict[str, Path] = {}
    for path in sorted(paths, key=lambda item: relative_path(root, item)):
        digest = file_hash(path)
        entry = {
            "path": relative_path(root, path),
            "sha256": digest,
            "size": path.stat().st_size,
        }
        retained = retained_by_hash.get(digest)
        if retained is not None:
            entry["retained_role"] = retained["role"]
        else:
            entry["member"] = f"{AUDIT_BLOB_PREFIX}{digest}"
            blobs.setdefault(digest, path)
        entries.append(entry)
    created_at = utc_now()
    manifest_payload = {
        "schema": ARCHIVE_SCHEMA,
        "payload_schema": AUDIT_PAYLOAD_SCHEMA,
        "chapter_number": chapter_number,
        "archive": relative_path(root, archive),
        "entries": entries,
        "entry_count": len(entries),
        "stored_blob_count": len(blobs),
        "deduplicated_entries": len(entries) - len(blobs),
        "uncompressed_bytes": sum(int(item["size"]) for item in entries),
        "stored_uncompressed_bytes": sum(path.stat().st_size for path in blobs.values()),
        "retained_evidence": retained_evidence,
        "agent_task_projection": {
            "schema": task_projection["schema"],
            "tasks_member": AUDIT_TASKS_MEMBER,
            "tasks_sha256": sha256(task_bytes).hexdigest(),
            "task_count": len(task_projection.get("tasks", [])),
            "events_member": AUDIT_EVENTS_MEMBER,
            "events_sha256": sha256(event_bytes).hexdigest(),
            "event_count": len(task_projection.get("events", [])),
        },
        "retention_policy": {
            "active_buffer_chapters": 2,
            "loose_evidence": [item["path"] for item in retained_evidence],
            "archived_materials": "non_canonical_chapter_artifacts",
            "duplicate_content": "retained_evidence_reference_or_one_content_addressed_blob",
        },
        "created_at": created_at,
    }
    descriptor, temp_name = tempfile.mkstemp(prefix=f"ch{chapter_number:03d}.", suffix=".zip", dir=archive_dir)
    os.close(descriptor)
    temp = Path(temp_name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as handle:
            handle.writestr(
                AUDIT_MANIFEST_MEMBER,
                json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
            )
            handle.writestr(AUDIT_TASKS_MEMBER, task_bytes)
            handle.writestr(AUDIT_EVENTS_MEMBER, event_bytes)
            for digest, path in sorted(blobs.items()):
                handle.write(path, arcname=f"{AUDIT_BLOB_PREFIX}{digest}")
        temp.replace(archive)
    finally:
        if temp.exists():
            temp.unlink()
    manifest = {
        **manifest_payload,
        "archive_sha256": file_hash(archive),
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
        if report.name.endswith(".rollback.json"):
            continue
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


def transaction_snapshot_diagnostics(root: Path) -> dict[str, Any]:
    report_dir = root / "70_runtime" / "transactions"
    pending: list[str] = []
    retained_failures: list[str] = []
    reclaimable: list[Path] = []
    seen: set[Path] = set()
    for report in report_dir.glob("*.json") if report_dir.exists() else []:
        payload = read_json(report, {})
        if not isinstance(payload, dict):
            continue
        snapshot_value = str(payload.get("snapshot_dir") or "")
        if not snapshot_value:
            continue
        snapshot = (root / snapshot_value).resolve()
        try:
            snapshot.relative_to((report_dir / "s").resolve())
        except ValueError:
            continue
        status = str(payload.get("status") or "")
        cleanup_complete = payload.get("cleanup_complete")
        if status == "pending":
            pending.append(relative_path(root, report))
        elif status == "applied" and cleanup_complete is not True and snapshot.is_dir():
            if snapshot not in seen:
                seen.add(snapshot)
                reclaimable.append(snapshot)
        elif status == "rolled_back" and cleanup_complete is not True and snapshot.is_dir():
            retained_failures.append(relative_path(root, report))
    return {
        "pending": tuple(sorted(set(pending))),
        "retained_failures": tuple(sorted(set(retained_failures))),
        "reclaimable_paths": tuple(relative_path(root, path) for path in reclaimable),
        "reclaimable_bytes": sum(directory_size(path) for path in reclaimable),
    }


def verify_single_archive(root: Path, archive: Path, manifest: dict[str, Any]) -> list[str]:
    try:
        return verify_single_archive_content(root, archive, manifest)
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        return [f"archive is unreadable: {exc}"]


def verify_single_archive_content(root: Path, archive: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if file_hash(archive) != str(manifest.get("archive_sha256") or ""):
        errors.append("archive hash mismatch")
        return errors
    expected_entries = manifest.get("entries")
    if not isinstance(expected_entries, list):
        return ["entries must be a list"]
    expected_paths: list[str] = []
    expected_members: list[str] = []
    with zipfile.ZipFile(archive, "r") as handle:
        names = handle.namelist()
        if manifest.get("schema") == ARCHIVE_SCHEMA:
            expected_members.append(AUDIT_MANIFEST_MEMBER)
            if AUDIT_MANIFEST_MEMBER not in names:
                errors.append("archive is missing its embedded audit manifest")
            else:
                try:
                    embedded = json.loads(handle.read(AUDIT_MANIFEST_MEMBER).decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    errors.append(f"embedded audit manifest is unreadable: {exc}")
                else:
                    external_payload = {
                        key: value
                        for key, value in manifest.items()
                        if key != "archive_sha256"
                    }
                    if embedded != external_payload:
                        errors.append("embedded audit manifest does not match the external manifest")
            if manifest.get("payload_schema") != AUDIT_PAYLOAD_SCHEMA:
                errors.append("payload_schema is invalid")
            projection = manifest.get("agent_task_projection")
            if isinstance(projection, dict):
                for member_key, hash_key, count_key, kind in (
                    ("tasks_member", "tasks_sha256", "task_count", "tasks"),
                    ("events_member", "events_sha256", "event_count", "events"),
                ):
                    member = str(projection.get(member_key) or "")
                    expected_members.append(member)
                    if member not in names:
                        errors.append(f"archive is missing its Agent {kind} projection")
                        continue
                    data = handle.read(member)
                    if sha256(data).hexdigest() != str(projection.get(hash_key) or ""):
                        errors.append(f"Agent {kind} projection hash mismatch")
                        continue
                    if kind == "tasks":
                        try:
                            values = json.loads(data.decode("utf-8"))
                        except (UnicodeError, json.JSONDecodeError):
                            values = None
                        actual_count = len(values) if isinstance(values, list) else -1
                    else:
                        actual_count = len(data.decode("utf-8").splitlines()) if data else 0
                    if actual_count != int(projection.get(count_key) or 0):
                        errors.append(f"Agent {kind} projection count mismatch")
        for item in expected_entries:
            if not isinstance(item, dict):
                errors.append("entry descriptor must be an object")
                continue
            path = str(item.get("path") or "")
            expected_paths.append(path)
            try:
                ensure_archivable_chapter_path(
                    path,
                    int(manifest.get("chapter_number") or 0),
                )
            except ValueError as exc:
                errors.append(str(exc))
                continue
            try:
                retained_source = retained_source_for_entry(item, manifest)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if retained_source is not None:
                retained_path = root / str(retained_source.get("path") or "")
                if not retained_path.is_file():
                    errors.append(f"missing retained source for {path}")
                    continue
                data = retained_path.read_bytes()
            else:
                try:
                    member = archive_member_for_entry(item, manifest)
                    ensure_safe_archive_path(member)
                except ValueError as exc:
                    errors.append(str(exc))
                    continue
                expected_members.append(member)
                if member not in names:
                    errors.append(f"missing blob for {path}")
                    continue
                data = handle.read(member)
            if sha256(data).hexdigest() != str(item.get("sha256") or ""):
                errors.append(f"hash mismatch {path}")
            if len(data) != int(item.get("size") or -1):
                errors.append(f"size mismatch {path}")
        if len(names) != len(set(names)):
            errors.append("archive contains duplicate member names")
        if set(names) != set(expected_members):
            extras = sorted(set(names) - set(expected_members))
            if extras:
                errors.append(f"undeclared archive members: {', '.join(extras)}")
        if len(expected_paths) != len(set(expected_paths)):
            errors.append("manifest contains duplicate entry paths")
        if int(manifest.get("entry_count") or 0) != len(expected_entries):
            errors.append("entry_count does not match entries")
        if manifest.get("schema") == ARCHIVE_SCHEMA:
            projection_members = {AUDIT_TASKS_MEMBER, AUDIT_EVENTS_MEMBER}
            unique_members = set(expected_members) - {AUDIT_MANIFEST_MEMBER, *projection_members}
            if int(manifest.get("stored_blob_count", -1)) != len(unique_members):
                errors.append("stored_blob_count does not match content-addressed members")
            if int(manifest.get("deduplicated_entries", -1)) != len(expected_entries) - len(unique_members):
                errors.append("deduplicated_entries does not match logical entries")
            logical_bytes = sum(int(item.get("size") or 0) for item in expected_entries if isinstance(item, dict))
            if int(manifest.get("uncompressed_bytes", -1)) != logical_bytes:
                errors.append("uncompressed_bytes does not match logical entries")
            stored_bytes = sum(
                len(handle.read(member))
                for member in unique_members
                if member in names
            )
            if int(manifest.get("stored_uncompressed_bytes", -1)) != stored_bytes:
                errors.append("stored_uncompressed_bytes does not match stored blobs")

    retained = manifest.get("retained_evidence", [])
    if manifest.get("schema") in {"chapter_artifact_archive_v2", ARCHIVE_SCHEMA}:
        if not isinstance(retained, list) or not retained:
            errors.append("retained_evidence must bind final, semantic ledger, and closure")
        roles = {str(item.get("role") or "") for item in retained if isinstance(item, dict)}
        if roles != {"final", "semantic_ledger", "closure"}:
            errors.append("retained_evidence roles are incomplete")
        for item in retained if isinstance(retained, list) else []:
            if not isinstance(item, dict):
                errors.append("retained evidence descriptor must be an object")
                continue
            relative = str(item.get("path") or "")
            try:
                ensure_safe_archive_path(relative)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            path = root / relative
            if not path.is_file():
                errors.append(f"retained evidence missing {relative}")
            elif file_hash(path) != str(item.get("sha256") or ""):
                errors.append(f"retained evidence hash mismatch {relative}")
        retained_by_role = {
            str(item.get("role") or ""): item
            for item in retained
            if isinstance(item, dict)
        }
        final_record = retained_by_role.get("final", {})
        ledger_record = retained_by_role.get("semantic_ledger", {})
        closure_record = retained_by_role.get("closure", {})
        closure = read_json(root / str(closure_record.get("path") or ""), {})
        ledger = read_json(root / str(ledger_record.get("path") or ""), {})
        final_hash = str(final_record.get("sha256") or "")
        ledger_hash = str(ledger_record.get("sha256") or "")
        if not isinstance(closure, dict) or int(closure.get("chapter_number") or 0) != int(
            manifest.get("chapter_number") or 0
        ):
            errors.append("closure evidence chapter_number does not match the archive")
        elif closure.get("final_sha256") != final_hash:
            errors.append("closure final_sha256 does not match retained final evidence")
        elif closure.get("semantic_ledger_sha256") != ledger_hash:
            errors.append("closure semantic_ledger_sha256 does not match retained semantic ledger evidence")
        ledger_source = ledger.get("source") if isinstance(ledger, dict) else None
        if (
            not isinstance(ledger, dict)
            or ledger.get("canonical") is not True
            or not isinstance(ledger_source, dict)
            or ledger_source.get("sha256") != final_hash
        ):
            errors.append("semantic ledger is not canonical evidence for the retained final manuscript")
    return errors


def archive_member_for_entry(item: dict[str, Any], manifest: dict[str, Any]) -> str:
    """Resolve one logical artifact to its immutable ZIP member."""

    path = str(item.get("path") or "")
    if manifest.get("schema") != ARCHIVE_SCHEMA:
        return path
    digest = str(item.get("sha256") or "")
    member = str(item.get("member") or "")
    expected = f"{AUDIT_BLOB_PREFIX}{digest}"
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or member != expected:
        raise ValueError(f"Invalid content-addressed archive member for {path}")
    return member


def retained_source_for_entry(item: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any] | None:
    if manifest.get("schema") != ARCHIVE_SCHEMA:
        return None
    role = str(item.get("retained_role") or "")
    if not role:
        return None
    if item.get("member"):
        raise ValueError(f"Archive entry cannot declare both member and retained_role: {item.get('path')}")
    retained = manifest.get("retained_evidence")
    matches = [
        record
        for record in retained if isinstance(record, dict) and str(record.get("role") or "") == role
    ] if isinstance(retained, list) else []
    if len(matches) != 1:
        raise ValueError(f"Invalid retained evidence reference for {item.get('path')}")
    record = matches[0]
    if (
        str(record.get("sha256") or "") != str(item.get("sha256") or "")
        or int(record.get("size") or -1) != int(item.get("size") or -1)
    ):
        raise ValueError(f"Retained evidence reference does not match {item.get('path')}")
    return record


def read_archived_entry(
    root: Path,
    handle: zipfile.ZipFile,
    item: dict[str, Any],
    manifest: dict[str, Any],
) -> bytes:
    retained = retained_source_for_entry(item, manifest)
    if retained is not None:
        path = root / str(retained.get("path") or "")
        if not path.is_file():
            raise ValueError(f"Retained evidence is missing for {item.get('path')}")
        return path.read_bytes()
    return handle.read(archive_member_for_entry(item, manifest))


def retained_evidence_entries(root: Path, chapter_number: int) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for role, template in RETAINED_EVIDENCE:
        path = root / template.format(chapter=chapter_number)
        if not path.is_file():
            raise ValueError(
                f"Cannot archive ch{chapter_number:03d}: missing retained {role} evidence "
                f"({relative_path(root, path)})."
            )
        entries.append(
            {
                "role": role,
                "path": relative_path(root, path),
                "sha256": file_hash(path),
                "size": path.stat().st_size,
            }
        )
    return entries


def closed_chapter_numbers(root: Path) -> list[int]:
    closure_dir = root / "30_state" / "chapter_closures"
    return sorted(
        chapter
        for path in closure_dir.glob("ch*.json") if (chapter := chapter_from_path(path.name)) > 0
    )


def chapter_numbers_in(directory: Path, pattern: str) -> set[int]:
    if not directory.is_dir():
        return set()
    return {
        chapter
        for path in directory.glob(pattern)
        if (chapter := chapter_from_path(path.name)) > 0
    }


def archived_loose_files(root: Path, archives: list[Path]) -> list[str]:
    duplicates: set[str] = set()
    for archive in archives:
        manifest = read_json(archive.with_suffix(".manifest.json"), {})
        entries = manifest.get("entries") if isinstance(manifest, dict) else []
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path") or "")
            try:
                ensure_safe_archive_path(relative)
            except ValueError:
                continue
            if (root / relative).is_file():
                duplicates.add(relative)
    return sorted(duplicates)


def ensure_safe_archive_path(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Unsafe archive entry path: {value}")


def ensure_archivable_chapter_path(value: str, chapter_number: int) -> None:
    ensure_safe_archive_path(value)
    normalized = value.replace("\\", "/")
    finalization = f"40_manuscript/final/ch{chapter_number:03d}.finalization.json"
    if normalized == finalization:
        return
    if normalized.startswith("70_runtime/transactions/"):
        relative = normalized.removeprefix("70_runtime/transactions/")
        if "/" not in relative and relative.endswith(".json"):
            return
        raise ValueError(f"Transaction archive entry must be one direct JSON report: {value}")
    if not normalized.startswith(ARCHIVABLE_PREFIXES):
        raise ValueError(f"Archive entry is outside non-canonical chapter artifact lanes: {value}")
    if chapter_number <= 0 or chapter_from_path(normalized) != chapter_number:
        raise ValueError(f"Archive entry does not belong to ch{chapter_number:03d}: {value}")


def chapter_from_path(value: str) -> int:
    match = CHAPTER_PATTERN.search(value.replace("\\", "/"))
    return int(match.group(1)) if match else 0


def chapter_from_archive(path: Path) -> int:
    return chapter_from_path(path.name)


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
