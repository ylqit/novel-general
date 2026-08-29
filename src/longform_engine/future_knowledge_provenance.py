"""Retention and integrity contract for approved future-knowledge evidence."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from longform_engine.storage import atomic_write_text


FUTURE_KNOWLEDGE_PROVENANCE_PINS_SCHEMA = "future_knowledge_provenance_pins_v1"
PIN_REGISTRY_RELATIVE = "30_state/future_knowledge_provenance_pins.json"
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


class FutureKnowledgeProvenanceError(ValueError):
    """Raised when an approved update no longer has its exact retained evidence."""


def provenance_pin_registry_path(root: Path) -> Path:
    return root / PIN_REGISTRY_RELATIVE


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
    """Build the exact engine-owned pin projection from live evidence bytes."""

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
    approved = _file_binding(root, approved_path)
    evidence = [
        {"kind": kind, **_file_binding(root, evidence_paths[kind])}
        for kind in PIN_EVIDENCE_KINDS
    ]
    return {
        "trigger_id": trigger_id,
        "task_id": task_id,
        "chapter_number": chapter_number,
        "from_chapter": from_chapter,
        "to_chapter": to_chapter,
        "approved_document": approved,
        "evidence": evidence,
    }


def upsert_future_knowledge_pin(root: Path, record: Mapping[str, Any]) -> Path:
    """Atomically record one immutable logical trigger pin inside an apply transaction."""

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


def require_exact_future_knowledge_pin(root: Path, expected: Mapping[str, Any]) -> None:
    """Require one exact pin and every retained byte it names."""

    registry = _read_registry(provenance_pin_registry_path(root), require_exists=True)
    trigger_id = str(expected.get("trigger_id") or "")
    matches = [item for item in registry["pins"] if item.get("trigger_id") == trigger_id]
    if matches != [dict(expected)]:
        raise FutureKnowledgeProvenanceError(
            f"future knowledge provenance pin differs from current chain: {trigger_id}"
        )
    errors = validate_future_knowledge_pin(root, matches[0], require_live=True)
    if errors:
        raise FutureKnowledgeProvenanceError(";".join(errors))


def active_future_knowledge_pin_paths(root: Path, *, for_chapter: int) -> set[Path]:
    """Return exact live paths retained for updates that still apply to a chapter."""

    registry = _read_registry(provenance_pin_registry_path(root))
    result: set[Path] = set()
    for record in registry["pins"]:
        if not _pin_active(record, for_chapter=for_chapter):
            continue
        errors = validate_future_knowledge_pin(root, record, require_live=True)
        if errors:
            raise FutureKnowledgeProvenanceError(";".join(errors))
        result.add(_resolve_binding(root, record["approved_document"]))
        result.update(_resolve_binding(root, item) for item in record["evidence"])
    return result


def active_future_knowledge_task_ids(root: Path, *, for_chapter: int) -> set[str]:
    registry = _read_registry(provenance_pin_registry_path(root))
    return {
        str(record["task_id"])
        for record in registry["pins"]
        if _pin_active(record, for_chapter=for_chapter)
    }


def validate_future_knowledge_pin(
    root: Path,
    value: Mapping[str, Any] | Any,
    *,
    require_live: bool,
) -> list[str]:
    required = {
        "trigger_id",
        "task_id",
        "chapter_number",
        "from_chapter",
        "to_chapter",
        "approved_document",
        "evidence",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        return ["future knowledge pin fields are invalid"]
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
    errors.extend(_binding_errors(root, value.get("approved_document"), require_live=require_live))
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or [
        item.get("kind") if isinstance(item, Mapping) else None for item in evidence
    ] != list(PIN_EVIDENCE_KINDS):
        errors.append("future knowledge pin evidence kinds/order are invalid")
    else:
        for item in evidence:
            errors.extend(_binding_errors(root, item, require_live=require_live, allow_kind=True))
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
        errors = validate_future_knowledge_pin(path.parents[1], record, require_live=False)
        if errors:
            raise FutureKnowledgeProvenanceError(";".join(errors))
        trigger_ids.append(str(record["trigger_id"]))
        task_ids.append(str(record["task_id"]))
    if len(trigger_ids) != len(set(trigger_ids)) or len(task_ids) != len(set(task_ids)):
        raise FutureKnowledgeProvenanceError(
            "future knowledge provenance pins must have unique trigger/task identities"
        )
    return payload


def _pin_active(record: Mapping[str, Any], *, for_chapter: int) -> bool:
    end = record.get("to_chapter")
    return for_chapter >= int(record["from_chapter"]) and (
        end is None or for_chapter <= int(end)
    )


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
    if not relative or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
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


def pin_applicability_from_claims(claims: Iterable[Mapping[str, Any]]) -> tuple[int, int | None]:
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
    "FUTURE_KNOWLEDGE_PROVENANCE_PINS_SCHEMA",
    "FutureKnowledgeProvenanceError",
    "active_future_knowledge_pin_paths",
    "active_future_knowledge_task_ids",
    "build_future_knowledge_pin",
    "pin_applicability_from_claims",
    "provenance_pin_registry_path",
    "require_exact_future_knowledge_pin",
    "upsert_future_knowledge_pin",
    "validate_future_knowledge_pin",
]
