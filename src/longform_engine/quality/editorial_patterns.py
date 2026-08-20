"""Derived, non-canonical recurrence memory for structured editorial findings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


PATTERN_SCHEMA = "editorial_pattern_item_v1"
PATTERN_REGISTRY = Path("50_workbench/editorial_patterns/registry.jsonl")
ACTIVE_STATUSES = {"monitoring"}
TERMINAL_STATUSES = {"resolved", "suppressed", "expired"}
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}
PATTERN_TASK_TYPES = {"editorial_review", "repair", "repair_plan_synthesis"}


@dataclass(frozen=True)
class EditorialPatternRegistryResult:
    registry_file: str
    total: int
    monitoring: int
    resolved: int
    suppressed: int
    expired: int
    updated_pattern_id: str = ""
    next_command: str = ""


def refresh_editorial_pattern_registry(
    root: Path,
    *,
    chapter_number: int,
    observations: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Merge explicit role/finding pairs; never infer a pattern from prose or summaries."""

    records = read_pattern_registry(root)
    now = utc_now()
    for observation in observations:
        role_id = normalize_role_id(observation.get("role_id"))
        finding_code = normalize_finding_code(observation.get("finding_code"))
        if not role_id or not finding_code:
            continue
        severity = normalize_severity(observation.get("severity"), fallback="P2")
        source_path = str(observation.get("source_path") or "").strip()
        source_sha256 = normalize_hash(observation.get("source_sha256"))
        candidate_sha256 = normalize_hash(observation.get("candidate_sha256"))
        evidence_hash = normalize_hash(observation.get("evidence_hash"))
        if not source_path or not source_sha256 or not candidate_sha256 or not evidence_hash:
            continue
        occurrence = {
            "chapter_number": chapter_number,
            "severity": severity,
            "candidate_sha256": candidate_sha256,
            "source_path": source_path,
            "source_sha256": source_sha256,
            "evidence_hash": evidence_hash,
        }
        pattern_id = f"pattern:{role_id}:{finding_code}"
        existing = next((item for item in records if item.get("pattern_id") == pattern_id), None)
        if existing is None:
            records.append(
                {
                    "schema": PATTERN_SCHEMA,
                    "pattern_id": pattern_id,
                    "role_id": role_id,
                    "finding_code": finding_code,
                    "severity": severity,
                    "first_seen_chapter": chapter_number,
                    "last_seen_chapter": chapter_number,
                    "recurrence_count": 1,
                    "status": "monitoring",
                    "expires_after_chapter": chapter_number + 3 if severity == "P2" else None,
                    "occurrences": [occurrence],
                    "resolution_evidence": [],
                    "created_at": now,
                    "updated_at": now,
                }
            )
            continue
        if existing.get("status") == "suppressed":
            continue
        occurrences = existing.get("occurrences")
        if not isinstance(occurrences, list):
            occurrences = []
            existing["occurrences"] = occurrences
        occurrence_key = tuple(occurrence.values())
        if any(tuple(item.get(key) for key in occurrence) == occurrence_key for item in occurrences if isinstance(item, dict)):
            continue
        occurrences.append(occurrence)
        existing["severity"] = more_severe(str(existing.get("severity") or "P2"), severity)
        existing["last_seen_chapter"] = max(int(existing.get("last_seen_chapter") or 0), chapter_number)
        existing["recurrence_count"] = len(occurrences)
        existing["status"] = "monitoring"
        existing["expires_after_chapter"] = (
            int(existing["last_seen_chapter"]) + 3 if existing["severity"] == "P2" else None
        )
        existing["updated_at"] = now
    records.sort(key=pattern_sort_key)
    write_pattern_registry(root, records)
    return tuple(records)


def editorial_patterns_for_task(
    root: Path,
    *,
    task_type: str,
    role_id: str = "",
    limit: int = 5,
) -> tuple[dict[str, Any], ...]:
    """Return bounded advisories only for repair and editorial-review roles."""

    if str(task_type or "") not in PATTERN_TASK_TYPES:
        return ()
    normalized_role = normalize_role_id(role_id)
    records = [
        item
        for item in read_pattern_registry(root)
        if item.get("status") in ACTIVE_STATUSES
        and (not normalized_role or item.get("role_id") == normalized_role or task_type != "editorial_review")
    ]
    records.sort(key=pattern_priority_key)
    return tuple(public_pattern_item(item) for item in records[: max(1, min(5, int(limit or 5)))])


def pattern_registry_status(
    config: ConfigDocument,
    *,
    target_chapter: int | None = None,
) -> EditorialPatternRegistryResult:
    root = resolve_project_root(config)
    records = read_pattern_registry(root)
    if target_chapter is not None and advance_pattern_lifecycle(
        records,
        target_chapter=target_chapter,
        completed_chapters=completed_chapters_before(root, target_chapter=target_chapter),
    ):
        write_pattern_registry(root, records)
    return summarize_pattern_registry(root, records)


def transition_editorial_pattern(
    config: ConfigDocument,
    *,
    pattern_id: str,
    status: str,
    evidence: str,
) -> EditorialPatternRegistryResult:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"resolved", "suppressed"}:
        raise ValueError("editorial pattern status must be resolved or suppressed")
    root = resolve_project_root(config)
    evidence_path = Path(str(evidence or "").strip())
    evidence_path = evidence_path if evidence_path.is_absolute() else root / evidence_path
    evidence_path = evidence_path.resolve()
    try:
        evidence_path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("editorial pattern evidence must stay inside the project") from exc
    if not evidence_path.is_file():
        raise ValueError("editorial pattern transition requires an existing evidence file")
    records = read_pattern_registry(root)
    target = next((item for item in records if item.get("pattern_id") == pattern_id), None)
    if target is None:
        raise ValueError(f"Unknown pattern_id: {pattern_id}")
    target["status"] = normalized_status
    target.setdefault("resolution_evidence", []).append(
        {
            "path": relative_path(root, evidence_path),
            "sha256": sha256(evidence_path.read_bytes()).hexdigest(),
            "recorded_at": utc_now(),
        }
    )
    target["updated_at"] = utc_now()
    write_pattern_registry(root, records)
    return summarize_pattern_registry(
        root,
        records,
        updated_pattern_id=pattern_id,
        next_command="longform-engine production next project.yaml",
    )


def rebuild_editorial_pattern_registry(config: ConfigDocument) -> EditorialPatternRegistryResult:
    """Rebuild the derived registry from immutable structured editorial aggregates."""

    root = resolve_project_root(config)
    write_pattern_registry(root, [])
    review_dir = root / "50_workbench" / "editorial_reviews"
    for path in sorted(review_dir.glob("ch*.aggregate.json")):
        payload = read_json(path)
        chapter_number = int(payload.get("chapter_number") or 0)
        if chapter_number <= 0:
            continue
        refresh_editorial_pattern_registry(
            root,
            chapter_number=chapter_number,
            observations=editorial_pattern_observations(
                payload.get("unresolved_items"),
                source_path=relative_path(root, path),
                source_sha256=sha256(path.read_bytes()).hexdigest(),
                candidate_sha256=payload.get("source_sha256"),
            ),
        )
    return summarize_pattern_registry(
        root,
        read_pattern_registry(root),
        next_command="longform-engine production next project.yaml",
    )


def truncate_editorial_pattern_registry(root: Path, *, to_chapter: int) -> tuple[str, ...]:
    records = read_pattern_registry(root)
    changed = False
    kept: list[dict[str, Any]] = []
    for item in records:
        occurrences = [
            occurrence
            for occurrence in item.get("occurrences") or []
            if isinstance(occurrence, dict) and int(occurrence.get("chapter_number") or 0) <= to_chapter
        ]
        if not occurrences:
            changed = True
            continue
        if len(occurrences) != len(item.get("occurrences") or []):
            changed = True
            item = dict(item)
            item["occurrences"] = occurrences
            chapters = [int(occurrence["chapter_number"]) for occurrence in occurrences]
            item["first_seen_chapter"] = min(chapters)
            item["last_seen_chapter"] = max(chapters)
            item["recurrence_count"] = len(occurrences)
            item["status"] = "monitoring"
            item["resolution_evidence"] = []
            item["expires_after_chapter"] = max(chapters) + 3 if item.get("severity") == "P2" else None
            item["updated_at"] = utc_now()
        kept.append(item)
    if changed:
        write_pattern_registry(root, kept)
        return (PATTERN_REGISTRY.as_posix(),)
    return ()


def editorial_pattern_observations(
    values: Any,
    *,
    source_path: str,
    source_sha256: Any,
    candidate_sha256: Any,
) -> list[dict[str, Any]]:
    """Normalize only findings that already carry an explicit role and code."""

    if not isinstance(values, list):
        return []
    observations: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        role_id = normalize_role_id(value.get("role_id"))
        finding_code = normalize_finding_code(value.get("code"))
        evidence_ids = value.get("evidence_ids") or value.get("evidence")
        if not role_id or not finding_code or not evidence_ids:
            continue
        observations.append(
            {
                "role_id": role_id,
                "finding_code": finding_code,
                "severity": normalize_severity(value.get("severity"), fallback="P2"),
                "source_path": str(value.get("source_result_file") or source_path),
                "source_sha256": normalize_hash(value.get("source_result_sha256") or source_sha256),
                "candidate_sha256": normalize_hash(value.get("candidate_sha256") or candidate_sha256),
                "evidence_hash": sha256(
                    json.dumps(evidence_ids, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest(),
            }
        )
    return observations


def advance_pattern_lifecycle(
    records: list[dict[str, Any]],
    *,
    target_chapter: int,
    completed_chapters: set[int],
) -> bool:
    changed = False
    for item in records:
        if item.get("status") not in ACTIVE_STATUSES or item.get("severity") != "P2":
            continue
        last_seen = int(item.get("last_seen_chapter") or 0)
        completed_after = sorted(
            chapter
            for chapter in completed_chapters
            if last_seen < chapter < target_chapter
        )
        if (
            target_chapter > int(item.get("expires_after_chapter") or 0)
            and len(completed_after) >= 3
        ):
            item["status"] = "expired"
            item.setdefault("resolution_evidence", []).append(
                {
                    "kind": "automatic_p2_expiry",
                    "after_complete_chapter": completed_after[-1],
                    "recorded_at": utc_now(),
                }
            )
            item["updated_at"] = utc_now()
            changed = True
    return changed


def completed_chapters_before(root: Path, *, target_chapter: int) -> set[int]:
    """Return explicitly closed chapters; a requested number alone is not completion evidence."""

    completed: set[int] = set()
    for path in (root / "30_state" / "chapter_closures").glob("ch*.json"):
        match = re.fullmatch(r"ch(\d+)\.json", path.name)
        if match and int(match.group(1)) < target_chapter:
            completed.add(int(match.group(1)))
    return completed


def public_pattern_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": PATTERN_SCHEMA,
        "pattern_id": str(item.get("pattern_id") or ""),
        "role_id": str(item.get("role_id") or ""),
        "finding_code": str(item.get("finding_code") or ""),
        "severity": str(item.get("severity") or "P2"),
        "first_seen_chapter": int(item.get("first_seen_chapter") or 0),
        "last_seen_chapter": int(item.get("last_seen_chapter") or 0),
        "recurrence_count": int(item.get("recurrence_count") or 0),
        "status": str(item.get("status") or ""),
    }


def read_pattern_registry(root: Path) -> list[dict[str, Any]]:
    path = root / PATTERN_REGISTRY
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid editorial pattern JSONL at {path}:{line_number}: {exc}") from exc
        errors = validate_pattern_item(payload)
        if isinstance(payload, dict):
            errors.extend(validate_resolution_evidence(root, payload))
        if errors:
            raise ValueError(
                f"Invalid editorial pattern item at {path}:{line_number}: {'; '.join(errors)}"
            )
        records.append(payload)
    return records


def write_pattern_registry(root: Path, records: list[dict[str, Any]]) -> None:
    for index, item in enumerate(records, start=1):
        errors = validate_pattern_item(item)
        if errors:
            raise ValueError(f"Cannot write editorial pattern item {index}: {'; '.join(errors)}")
    content = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records)
    atomic_write_text(root / PATTERN_REGISTRY, content)


def validate_pattern_item(payload: Any) -> list[str]:
    required = {
        "schema", "pattern_id", "role_id", "finding_code", "severity",
        "first_seen_chapter", "last_seen_chapter", "recurrence_count", "status",
        "expires_after_chapter", "occurrences", "resolution_evidence", "created_at", "updated_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        return ["fields do not match editorial_pattern_item_v1"]
    errors: list[str] = []
    if payload.get("schema") != PATTERN_SCHEMA:
        errors.append(f"schema must be {PATTERN_SCHEMA}")
    role_id = normalize_role_id(payload.get("role_id"))
    finding_code = normalize_finding_code(payload.get("finding_code"))
    if not role_id or payload.get("role_id") != role_id:
        errors.append("role_id must be a normalized structured role id")
    if not finding_code or payload.get("finding_code") != finding_code:
        errors.append("finding_code must be a normalized structured finding code")
    if payload.get("pattern_id") != f"pattern:{role_id}:{finding_code}":
        errors.append("pattern_id must be derived from role_id and finding_code")
    severity = payload.get("severity")
    if severity not in SEVERITY_ORDER:
        errors.append("severity must be P0, P1, or P2")
    status = payload.get("status")
    if status not in ACTIVE_STATUSES | TERMINAL_STATUSES:
        errors.append("status is invalid")
    occurrences = payload.get("occurrences")
    if not isinstance(occurrences, list) or not occurrences:
        errors.append("occurrences must be a non-empty list")
        occurrences = []
    chapters: list[int] = []
    for occurrence in occurrences:
        if not isinstance(occurrence, dict) or set(occurrence) != {
            "chapter_number", "severity", "candidate_sha256", "source_path",
            "source_sha256", "evidence_hash",
        }:
            errors.append("each occurrence must contain the exact structured evidence fields")
            continue
        chapter_number = occurrence.get("chapter_number")
        if not isinstance(chapter_number, int) or isinstance(chapter_number, bool) or chapter_number < 1:
            errors.append("occurrence.chapter_number must be positive")
        else:
            chapters.append(chapter_number)
        if occurrence.get("severity") not in SEVERITY_ORDER:
            errors.append("occurrence.severity is invalid")
        if not str(occurrence.get("source_path") or "").strip():
            errors.append("occurrence.source_path is required")
        for field in ("candidate_sha256", "source_sha256", "evidence_hash"):
            if not normalize_hash(occurrence.get(field)):
                errors.append(f"occurrence.{field} must be SHA-256")
    if chapters:
        if payload.get("first_seen_chapter") != min(chapters):
            errors.append("first_seen_chapter does not match occurrences")
        if payload.get("last_seen_chapter") != max(chapters):
            errors.append("last_seen_chapter does not match occurrences")
        if payload.get("recurrence_count") != len(occurrences):
            errors.append("recurrence_count does not match recorded occurrences")
    expires = payload.get("expires_after_chapter")
    if severity == "P2":
        if not isinstance(expires, int) or isinstance(expires, bool) or expires < int(payload.get("last_seen_chapter") or 0) + 3:
            errors.append("P2 expires_after_chapter must allow three complete chapters")
    elif expires is not None:
        errors.append("P0/P1 patterns must not auto-expire")
    resolution_evidence = payload.get("resolution_evidence")
    if not isinstance(resolution_evidence, list):
        errors.append("resolution_evidence must be a list")
        resolution_evidence = []
    for record in resolution_evidence:
        if not isinstance(record, dict):
            errors.append("resolution_evidence entries must be objects")
            continue
        if set(record) == {"path", "sha256", "recorded_at"}:
            if not str(record.get("path") or "").strip() or not normalize_hash(record.get("sha256")):
                errors.append("manual resolution evidence requires path and SHA-256")
        elif set(record) == {"kind", "after_complete_chapter", "recorded_at"}:
            if (
                record.get("kind") != "automatic_p2_expiry"
                or severity != "P2"
                or not isinstance(record.get("after_complete_chapter"), int)
                or isinstance(record.get("after_complete_chapter"), bool)
            ):
                errors.append("automatic resolution evidence is valid only for P2 expiry")
        else:
            errors.append("resolution_evidence entry fields are invalid")
        if not isinstance(record.get("recorded_at"), str) or not record["recorded_at"].strip():
            errors.append("resolution_evidence.recorded_at is required")
    if status in {"resolved", "suppressed"} and not any(
        isinstance(record, dict) and set(record) == {"path", "sha256", "recorded_at"}
        for record in resolution_evidence
    ):
        errors.append(f"{status} patterns require manual evidence")
    if status == "expired" and not any(
        isinstance(record, dict) and record.get("kind") == "automatic_p2_expiry"
        for record in resolution_evidence
    ):
        errors.append("expired patterns require automatic P2 expiry evidence")
    for field in ("created_at", "updated_at"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"{field} is required")
    return errors


def validate_resolution_evidence(root: Path, payload: dict[str, Any]) -> list[str]:
    """Keep manual P0/P1 resolution bound to an existing project-local artifact."""

    errors: list[str] = []
    project_root = root.resolve()
    for index, record in enumerate(payload.get("resolution_evidence") or []):
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "recorded_at"}:
            continue
        path = (root / str(record.get("path") or "")).resolve()
        try:
            path.relative_to(project_root)
        except ValueError:
            errors.append(f"resolution_evidence[{index}] escapes the project")
            continue
        if not path.is_file():
            errors.append(f"resolution_evidence[{index}] file is missing")
        elif sha256(path.read_bytes()).hexdigest() != record.get("sha256"):
            errors.append(f"resolution_evidence[{index}] hash is stale")
    return errors


def summarize_pattern_registry(
    root: Path,
    records: list[dict[str, Any]],
    *,
    updated_pattern_id: str = "",
    next_command: str = "",
) -> EditorialPatternRegistryResult:
    counts = {status: 0 for status in (*ACTIVE_STATUSES, *TERMINAL_STATUSES)}
    for item in records:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
    return EditorialPatternRegistryResult(
        registry_file=str(root / PATTERN_REGISTRY),
        total=len(records),
        monitoring=counts["monitoring"],
        resolved=counts["resolved"],
        suppressed=counts["suppressed"],
        expired=counts["expired"],
        updated_pattern_id=updated_pattern_id,
        next_command=next_command,
    )


def normalize_role_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")[:80]


def normalize_finding_code(value: Any) -> str:
    return re.sub(r"[^A-Z0-9_]+", "_", str(value or "").strip().upper()).strip("_")[:80]


def normalize_hash(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token if re.fullmatch(r"[0-9a-f]{64}", token) else ""


def normalize_severity(value: Any, *, fallback: str) -> str:
    token = str(value or "").strip().upper()
    return token if token in SEVERITY_ORDER else fallback


def more_severe(left: str, right: str) -> str:
    return min((normalize_severity(left, fallback="P2"), normalize_severity(right, fallback="P2")), key=SEVERITY_ORDER.get)


def pattern_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(item.get("first_seen_chapter") or 0),
        SEVERITY_ORDER.get(str(item.get("severity") or "P2"), 2),
        str(item.get("pattern_id") or ""),
    )


def pattern_priority_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        SEVERITY_ORDER.get(str(item.get("severity") or "P2"), 2),
        -int(item.get("recurrence_count") or 0),
        -int(item.get("last_seen_chapter") or 0),
        str(item.get("pattern_id") or ""),
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
