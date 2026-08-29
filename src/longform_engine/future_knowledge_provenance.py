"""Applicability, retention, and immutable audit for future-knowledge results."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable, Mapping
import zipfile

from longform_engine.storage import atomic_write_text


FUTURE_KNOWLEDGE_PROVENANCE_PINS_SCHEMA = "future_knowledge_provenance_pins_v1"
FUTURE_KNOWLEDGE_PROVENANCE_ARCHIVE_SCHEMA = (
    "future_knowledge_provenance_archive_v1"
)
PIN_REGISTRY_RELATIVE = "30_state/future_knowledge_provenance_pins.json"
PROVENANCE_ARCHIVE_ROOT = "70_runtime/artifacts/future_knowledge"
PROVENANCE_ARCHIVE_MANIFEST = "_audit/manifest.json"
PROVENANCE_ARCHIVE_BLOB_ROOT = "_audit/blobs"
PIN_EVIDENCE_KINDS = (
    "workflow",
    "fanfiction_context_bundle",
    "narrative_event_ledger",
    "final_chapter",
    "semantic_ledger",
    "task_manifest",
    "task_instruction",
    "candidate",
)
PIN_BASE_FIELDS = {
    "trigger_id",
    "task_id",
    "chapter_number",
    "from_chapter",
    "to_chapter",
    "approved_document",
    "evidence",
}
PIN_FIELDS = {*PIN_BASE_FIELDS, "provenance_archive"}


class FutureKnowledgeProvenanceError(ValueError):
    """Raised when a future-knowledge result loses its exact proof chain."""


@dataclass(frozen=True)
class FutureKnowledgeProvenanceSnapshot:
    """One fully verified registry view reused during one top-level read."""

    pins_by_trigger: Mapping[str, Mapping[str, Any]]
    pins_by_approved_path: Mapping[str, Mapping[str, Any]]


def provenance_pin_registry_path(root: Path) -> Path:
    return root / PIN_REGISTRY_RELATIVE


def future_knowledge_provenance_archive_path(root: Path, trigger_id: str) -> Path:
    digest = sha256(trigger_id.encode("utf-8")).hexdigest()[:24]
    return root / PROVENANCE_ARCHIVE_ROOT / f"{digest}.zip"


def build_future_knowledge_pin(
    root: Path,
    *,
    trigger_id: str,
    task_id: str,
    chapter_number: int,
    from_chapter: int,
    to_chapter: int | None,
    approved_path: Path,
    evidence_paths: Mapping[str, Path],
) -> dict[str, Any]:
    """Build the archive-independent part of one exact engine-owned pin."""

    if set(evidence_paths) != set(PIN_EVIDENCE_KINDS):
        raise FutureKnowledgeProvenanceError(
            "future knowledge pin must bind the exact provenance evidence set"
        )
    if chapter_number <= 0 or from_chapter <= chapter_number:
        raise FutureKnowledgeProvenanceError(
            "future knowledge pin applicability must begin after realization"
        )
    if to_chapter is not None and to_chapter < from_chapter:
        raise FutureKnowledgeProvenanceError("future knowledge pin range is invalid")
    return {
        "trigger_id": trigger_id,
        "task_id": task_id,
        "chapter_number": chapter_number,
        "from_chapter": from_chapter,
        "to_chapter": to_chapter,
        "approved_document": _file_binding(root, approved_path),
        "evidence": [
            {"kind": kind, **_file_binding(root, evidence_paths[kind])}
            for kind in PIN_EVIDENCE_KINDS
        ],
    }


def seal_future_knowledge_provenance_archive(
    root: Path,
    base_record: Mapping[str, Any],
    *,
    task_projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Write/verify an immutable content-addressed audit and return the final pin."""

    base_errors = _validate_pin_base(root, base_record, require_live=True)
    if base_errors:
        raise FutureKnowledgeProvenanceError(";".join(base_errors))
    projection = dict(task_projection)
    projection_errors = _task_projection_errors(base_record, projection)
    if projection_errors:
        raise FutureKnowledgeProvenanceError(";".join(projection_errors))
    archive = future_knowledge_provenance_archive_path(
        root, str(base_record["trigger_id"])
    )
    manifest = {
        "schema": FUTURE_KNOWLEDGE_PROVENANCE_ARCHIVE_SCHEMA,
        "pin": dict(base_record),
        "task_projection": projection,
    }
    bindings = [base_record["approved_document"], *base_record["evidence"]]
    blobs: dict[str, Path] = {}
    for binding in bindings:
        digest = str(binding["sha256"])
        blobs.setdefault(digest, root / str(binding["path"]))
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.is_file():
        errors = _provenance_archive_errors(root, archive, manifest)
        if errors:
            raise FutureKnowledgeProvenanceError(
                "existing future knowledge provenance archive differs: "
                + ";".join(errors)
            )
    else:
        descriptor, temp_name = tempfile.mkstemp(
            prefix=f"{archive.stem}.", suffix=".zip", dir=archive.parent
        )
        os.close(descriptor)
        temp = Path(temp_name)
        try:
            with zipfile.ZipFile(
                temp,
                "w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            ) as handle:
                handle.writestr(PROVENANCE_ARCHIVE_MANIFEST, manifest_bytes)
                for digest, path in sorted(blobs.items()):
                    handle.write(
                        path,
                        arcname=f"{PROVENANCE_ARCHIVE_BLOB_ROOT}/{digest}",
                    )
            temp.replace(archive)
        finally:
            temp.unlink(missing_ok=True)
        errors = _provenance_archive_errors(root, archive, manifest)
        if errors:
            archive.unlink(missing_ok=True)
            raise FutureKnowledgeProvenanceError(
                "future knowledge provenance archive verification failed: "
                + ";".join(errors)
            )
    return {
        **dict(base_record),
        "provenance_archive": _file_binding(root, archive),
    }


def upsert_future_knowledge_pin(root: Path, record: Mapping[str, Any]) -> Path:
    """Atomically register one archive-backed logical trigger inside apply."""

    errors = validate_future_knowledge_pin(root, record, require_live=True)
    if errors:
        raise FutureKnowledgeProvenanceError(";".join(errors))
    path = provenance_pin_registry_path(root)
    registry = _read_registry(path)
    pins = [dict(item) for item in registry["pins"]]
    trigger_id = str(record["trigger_id"])
    existing = [item for item in pins if item.get("trigger_id") == trigger_id]
    if existing and existing != [dict(record)]:
        raise FutureKnowledgeProvenanceError(
            f"future knowledge pin conflicts with existing trigger: {trigger_id}"
        )
    if not existing:
        pins.append(dict(record))
    pins.sort(key=lambda item: (int(item["chapter_number"]), str(item["trigger_id"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(
            {"schema": FUTURE_KNOWLEDGE_PROVENANCE_PINS_SCHEMA, "pins": pins},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return path


def require_exact_future_knowledge_pin(
    root: Path,
    expected_base: Mapping[str, Any],
    *,
    snapshot: FutureKnowledgeProvenanceSnapshot | None = None,
) -> dict[str, Any]:
    """Require an exact base projection, immutable archive, and current live evidence."""

    trigger_id = str(expected_base.get("trigger_id") or "")
    if snapshot is None:
        snapshot = load_future_knowledge_provenance_snapshot(root, require_exists=True)
        snapshot_owns_archive_validation = True
    else:
        snapshot_owns_archive_validation = False
    matched = snapshot.pins_by_trigger.get(trigger_id)
    matches = [matched] if matched is not None else []
    if len(matches) != 1 or {
        key: matches[0].get(key) for key in PIN_BASE_FIELDS
    } != dict(expected_base):
        raise FutureKnowledgeProvenanceError(
            f"future knowledge provenance pin differs from current chain: {trigger_id}"
        )
    # A supplied snapshot has already verified every immutable archive once for
    # this top-level read. Only live evidence bindings need rechecking here.
    errors = (
        validate_future_knowledge_pin(root, matches[0], require_live=True)
        if snapshot_owns_archive_validation
        else _validate_pin_base(root, matches[0], require_live=True)
    )
    if errors:
        raise FutureKnowledgeProvenanceError(";".join(errors))
    return dict(matches[0])


def future_knowledge_pin_for_approved(
    root: Path,
    approved_path: Path,
    *,
    snapshot: FutureKnowledgeProvenanceSnapshot | None = None,
) -> dict[str, Any]:
    """Resolve one structurally valid archive-backed pin for a canonical document."""

    if snapshot is None:
        snapshot = load_future_knowledge_provenance_snapshot(root, require_exists=True)
    relative = approved_path.relative_to(root).as_posix()
    matched = snapshot.pins_by_approved_path.get(relative)
    matches = [matched] if matched is not None else []
    if len(matches) != 1:
        raise FutureKnowledgeProvenanceError(
            f"canonical future knowledge document requires one exact pin: {relative}"
        )
    return dict(matches[0])


def load_future_knowledge_provenance_snapshot(
    root: Path,
    *,
    require_exists: bool = False,
) -> FutureKnowledgeProvenanceSnapshot:
    """Read and verify the registry and every immutable archive exactly once."""

    registry = _read_registry(
        provenance_pin_registry_path(root), require_exists=require_exists
    )
    by_trigger: dict[str, Mapping[str, Any]] = {}
    by_approved: dict[str, Mapping[str, Any]] = {}
    for raw in registry["pins"]:
        record = dict(raw)
        by_trigger[str(record["trigger_id"])] = record
        by_approved[str(record["approved_document"]["path"])] = record
    return FutureKnowledgeProvenanceSnapshot(
        pins_by_trigger=by_trigger,
        pins_by_approved_path=by_approved,
    )


def future_knowledge_pin_applies(record: Mapping[str, Any], target_chapter: int) -> bool:
    end = record.get("to_chapter")
    return target_chapter >= int(record["from_chapter"]) and (
        end is None or target_chapter <= int(end)
    )


def future_knowledge_pin_retains(record: Mapping[str, Any], next_chapter: int) -> bool:
    """Retain from approval through expiry, including delayed applicability."""

    end = record.get("to_chapter")
    return end is None or next_chapter <= int(end)


def require_future_knowledge_pin_coverage(
    root: Path,
    *,
    next_chapter: int,
) -> tuple[dict[str, Any], ...]:
    """Preflight every canonical result/pin/archive before compaction mutates anything."""

    directory = root / "10_bible" / "fanfiction" / "future_knowledge"
    approved = sorted(directory.glob("*.json")) if directory.is_dir() else []
    registry = _read_registry(
        provenance_pin_registry_path(root), require_exists=bool(approved)
    )
    pins = [dict(item) for item in registry["pins"]]
    approved_relatives = {path.relative_to(root).as_posix() for path in approved}
    pinned_relatives = {
        str(item.get("approved_document", {}).get("path") or "") for item in pins
    }
    if approved_relatives != pinned_relatives:
        raise FutureKnowledgeProvenanceError(
            "canonical future knowledge documents and provenance pins differ"
        )
    for record in pins:
        errors = validate_future_knowledge_pin(
            root,
            record,
            require_live=future_knowledge_pin_retains(record, next_chapter),
        )
        if errors:
            raise FutureKnowledgeProvenanceError(";".join(errors))
    return tuple(pins)


def retained_future_knowledge_pin_paths(
    root: Path,
    *,
    next_chapter: int,
) -> set[Path]:
    records = require_future_knowledge_pin_coverage(
        root, next_chapter=next_chapter
    )
    return {
        _resolve_binding(root, item)
        for record in records
        if future_knowledge_pin_retains(record, next_chapter)
        for item in record["evidence"]
    }


def retained_future_knowledge_task_ids(
    root: Path,
    *,
    next_chapter: int,
) -> set[str]:
    records = require_future_knowledge_pin_coverage(
        root, next_chapter=next_chapter
    )
    return {
        str(record["task_id"])
        for record in records
        if future_knowledge_pin_retains(record, next_chapter)
    }


def expired_archived_future_knowledge_paths(
    root: Path,
    *,
    next_chapter: int,
) -> set[Path]:
    """Return expired live evidence already secured in immutable provenance archives."""

    records = require_future_knowledge_pin_coverage(
        root, next_chapter=next_chapter
    )
    return {
        _resolve_binding(root, item)
        for record in records
        if not future_knowledge_pin_retains(record, next_chapter)
        for item in record["evidence"]
    }


def validate_future_knowledge_pin(
    root: Path,
    value: Mapping[str, Any] | Any,
    *,
    require_live: bool,
) -> list[str]:
    if not isinstance(value, Mapping) or set(value) != PIN_FIELDS:
        return ["future knowledge pin fields are invalid"]
    errors = _validate_pin_base(root, value, require_live=require_live)
    archive_binding = value.get("provenance_archive")
    errors.extend(_binding_errors(root, archive_binding, require_live=True))
    if errors or not isinstance(archive_binding, Mapping):
        return errors
    archive = _resolve_binding(root, archive_binding)
    try:
        expected_manifest = _read_provenance_archive_manifest(archive)
    except FutureKnowledgeProvenanceError as exc:
        return [str(exc)]
    if expected_manifest.get("pin") != {
        key: value[key] for key in PIN_BASE_FIELDS
    }:
        errors.append("future knowledge provenance archive pin projection differs")
        return errors
    errors.extend(_provenance_archive_errors(root, archive, expected_manifest))
    return errors


def _validate_pin_base(
    root: Path,
    value: Mapping[str, Any] | Any,
    *,
    require_live: bool,
) -> list[str]:
    if not isinstance(value, Mapping) or not PIN_BASE_FIELDS <= set(value):
        return ["future knowledge pin base fields are invalid"]
    errors: list[str] = []
    for field in ("trigger_id", "task_id"):
        if not isinstance(value.get(field), str) or not str(value[field]).strip():
            errors.append(f"future knowledge pin {field} is required")
    chapter = value.get("chapter_number")
    start = value.get("from_chapter")
    end = value.get("to_chapter")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
        errors.append("future knowledge pin chapter_number is invalid")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(chapter, int)
        or start <= chapter
    ):
        errors.append("future knowledge pin from_chapter is invalid")
    if end is not None and (
        not isinstance(end, int)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or end < start
    ):
        errors.append("future knowledge pin to_chapter is invalid")
    errors.extend(
        _binding_errors(root, value.get("approved_document"), require_live=True)
    )
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or [
        item.get("kind") if isinstance(item, Mapping) else None for item in evidence
    ] != list(PIN_EVIDENCE_KINDS):
        errors.append("future knowledge pin evidence kinds/order are invalid")
    else:
        for item in evidence:
            errors.extend(
                _binding_errors(
                    root,
                    item,
                    require_live=require_live,
                    allow_kind=True,
                )
            )
    return errors


def _read_registry(path: Path, *, require_exists: bool = False) -> dict[str, Any]:
    if not path.is_file():
        if require_exists:
            raise FutureKnowledgeProvenanceError(
                "future knowledge provenance pin registry is missing"
            )
        return {"schema": FUTURE_KNOWLEDGE_PROVENANCE_PINS_SCHEMA, "pins": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FutureKnowledgeProvenanceError(
            f"future knowledge provenance pin registry is unreadable: {exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "pins"}
        or payload.get("schema") != FUTURE_KNOWLEDGE_PROVENANCE_PINS_SCHEMA
        or not isinstance(payload.get("pins"), list)
    ):
        raise FutureKnowledgeProvenanceError(
            "future knowledge provenance pin registry schema is invalid"
        )
    trigger_ids: list[str] = []
    task_ids: list[str] = []
    for record in payload["pins"]:
        errors = validate_future_knowledge_pin(
            path.parents[1], record, require_live=False
        )
        if errors:
            raise FutureKnowledgeProvenanceError(";".join(errors))
        trigger_ids.append(str(record["trigger_id"]))
        task_ids.append(str(record["task_id"]))
    if len(trigger_ids) != len(set(trigger_ids)) or len(task_ids) != len(set(task_ids)):
        raise FutureKnowledgeProvenanceError(
            "future knowledge provenance pins must have unique trigger/task identities"
        )
    return payload


def _provenance_archive_errors(
    root: Path,
    archive: Path,
    expected_manifest: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(archive, "r") as handle:
            names = handle.namelist()
            if PROVENANCE_ARCHIVE_MANIFEST not in names:
                return ["future knowledge provenance archive manifest is missing"]
            embedded = json.loads(
                handle.read(PROVENANCE_ARCHIVE_MANIFEST).decode("utf-8")
            )
            if embedded != dict(expected_manifest):
                errors.append("future knowledge provenance archive manifest differs")
            pin = expected_manifest.get("pin")
            if not isinstance(pin, Mapping):
                return ["future knowledge provenance archive pin is invalid"]
            bindings = [pin.get("approved_document"), *(pin.get("evidence") or [])]
            expected_members = {PROVENANCE_ARCHIVE_MANIFEST}
            for binding in bindings:
                if not isinstance(binding, Mapping):
                    errors.append("future knowledge provenance archive binding is invalid")
                    continue
                digest = str(binding.get("sha256") or "")
                member = f"{PROVENANCE_ARCHIVE_BLOB_ROOT}/{digest}"
                expected_members.add(member)
                if member not in names:
                    errors.append(f"future knowledge provenance blob is missing: {digest}")
                elif sha256(handle.read(member)).hexdigest() != digest:
                    errors.append(f"future knowledge provenance blob hash differs: {digest}")
            if set(names) != expected_members:
                errors.append("future knowledge provenance archive members differ")
            projection = expected_manifest.get("task_projection")
            if not isinstance(projection, Mapping):
                errors.append("future knowledge provenance task projection is invalid")
            else:
                errors.extend(_task_projection_errors(pin, projection))
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        errors.append(f"future knowledge provenance archive is unreadable: {exc}")
    return errors


def _task_projection_errors(
    pin: Mapping[str, Any],
    projection: Mapping[str, Any],
) -> list[str]:
    errors: list[str] = []
    evidence = {
        str(item.get("kind") or ""): item
        for item in pin.get("evidence") or []
        if isinstance(item, Mapping)
    }
    manifest_value = evidence.get("task_manifest")
    manifest: Mapping[str, Any] = (
        manifest_value if isinstance(manifest_value, Mapping) else {}
    )
    candidate_value = evidence.get("candidate")
    candidate: Mapping[str, Any] = (
        candidate_value if isinstance(candidate_value, Mapping) else {}
    )
    result_value = projection.get("current_result")
    result: Mapping[str, Any] = (
        result_value if isinstance(result_value, Mapping) else {}
    )
    if projection.get("task_id") != pin.get("task_id"):
        errors.append("future knowledge provenance task_id differs")
    if projection.get("status") != "applied":
        errors.append("future knowledge provenance task is not applied")
    if projection.get("manifest_file") != manifest.get("path"):
        errors.append("future knowledge provenance manifest projection differs")
    if (
        result.get("path") != candidate.get("path")
        or result.get("sha256") != candidate.get("sha256")
        or result.get("ok") is not True
    ):
        errors.append("future knowledge provenance result projection differs")
    return errors


def _read_provenance_archive_manifest(archive: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(archive, "r") as handle:
            payload = json.loads(
                handle.read(PROVENANCE_ARCHIVE_MANIFEST).decode("utf-8")
            )
    except (OSError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, KeyError) as exc:
        raise FutureKnowledgeProvenanceError(
            f"future knowledge provenance archive is unreadable: {exc}"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "pin", "task_projection"}
        or payload.get("schema") != FUTURE_KNOWLEDGE_PROVENANCE_ARCHIVE_SCHEMA
    ):
        raise FutureKnowledgeProvenanceError(
            "future knowledge provenance archive schema is invalid"
        )
    return payload


def _file_binding(root: Path, path: Path) -> dict[str, str]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise FutureKnowledgeProvenanceError(
            f"future knowledge pin path escapes project root: {path}"
        ) from exc
    if not resolved.is_file():
        raise FutureKnowledgeProvenanceError(
            f"future knowledge pinned evidence is missing: {relative}"
        )
    return {"path": relative, "sha256": sha256(resolved.read_bytes()).hexdigest()}


def _binding_errors(
    root: Path,
    value: Any,
    *,
    require_live: bool,
    allow_kind: bool = False,
) -> list[str]:
    fields = {"path", "sha256", *(("kind",) if allow_kind else ())}
    if not isinstance(value, Mapping) or set(value) != fields:
        return ["future knowledge pin file binding fields are invalid"]
    relative = str(value.get("path") or "")
    digest = str(value.get("sha256") or "")
    if not relative or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return ["future knowledge pin file binding is invalid"]
    try:
        path = (root / relative).resolve()
        path.relative_to(root.resolve())
    except ValueError:
        return [f"future knowledge pin path escapes project root: {relative}"]
    if require_live and (
        not path.is_file() or sha256(path.read_bytes()).hexdigest() != digest
    ):
        return [f"future knowledge pinned evidence is missing or stale: {relative}"]
    return []


def _resolve_binding(root: Path, binding: Mapping[str, Any]) -> Path:
    return (root / str(binding["path"])).resolve()


def pin_applicability_from_claims(
    claims: Iterable[Mapping[str, Any]],
) -> tuple[int, int | None]:
    starts: list[int] = []
    ends: list[int | None] = []
    for claim in claims:
        extensions = claim.get("extensions")
        extensions = extensions if isinstance(extensions, Mapping) else {}
        knowledge_range = extensions.get("knowledge_range")
        if not isinstance(knowledge_range, Mapping):
            continue
        starts.append(int(knowledge_range["from_chapter"]))
        raw_end = knowledge_range.get("to_chapter")
        ends.append(int(raw_end) if raw_end is not None else None)
    if not starts:
        raise FutureKnowledgeProvenanceError(
            "future knowledge pin requires validated knowledge ranges"
        )
    finite_ends = [end for end in ends if end is not None]
    return min(starts), None if len(finite_ends) != len(ends) else max(finite_ends)


__all__ = [
    "FUTURE_KNOWLEDGE_PROVENANCE_ARCHIVE_SCHEMA",
    "FUTURE_KNOWLEDGE_PROVENANCE_PINS_SCHEMA",
    "FutureKnowledgeProvenanceError",
    "FutureKnowledgeProvenanceSnapshot",
    "build_future_knowledge_pin",
    "expired_archived_future_knowledge_paths",
    "future_knowledge_pin_applies",
    "future_knowledge_pin_for_approved",
    "future_knowledge_pin_retains",
    "future_knowledge_provenance_archive_path",
    "load_future_knowledge_provenance_snapshot",
    "pin_applicability_from_claims",
    "provenance_pin_registry_path",
    "require_exact_future_knowledge_pin",
    "require_future_knowledge_pin_coverage",
    "retained_future_knowledge_pin_paths",
    "retained_future_knowledge_task_ids",
    "seal_future_knowledge_provenance_archive",
    "upsert_future_knowledge_pin",
    "validate_future_knowledge_pin",
]
