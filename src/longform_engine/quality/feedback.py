"""Lifecycle-managed, non-canonical quality feedback for Agent work orders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import hashlib
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


FEEDBACK_REGISTRY = Path("50_workbench/quality_feedback/registry.jsonl")
ACTIVE_STATUSES = {"open", "carried"}
TERMINAL_STATUSES = {"resolved", "suppressed", "expired"}
SEVERITY_ORDER = {"P0": 0, "P1": 1, "P2": 2}


@dataclass(frozen=True)
class FeedbackRegistryResult:
    registry_file: str
    total: int
    active: int
    carried: int
    resolved: int
    suppressed: int
    expired: int
    updated_feedback_id: str = ""
    next_command: str = ""


def ingest_chapter_feedback(root: Path, *, chapter_number: int) -> tuple[dict[str, Any], ...]:
    """Upsert controlled chapter findings without storing source prose."""

    if chapter_number <= 0:
        return ()
    observations: list[dict[str, Any]] = []
    gate_dir = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}"
    gate_file = gate_dir / "gate_result.json"
    gate = read_json(gate_file)
    if gate:
        observations.extend(
            observations_from_values(
                gate.get("failures"),
                kind="gate_result",
                source_path=relative_path(root, gate_file),
                default_severity=normalize_severity(gate.get("severity"), fallback="P1"),
                owner_task=f"repair:ch{chapter_number:03d}",
            )
        )
        observations.extend(
            observations_from_values(
                gate.get("warnings"),
                kind="gate_result",
                source_path=relative_path(root, gate_file),
                default_severity="P2",
                owner_task=f"chapter_write:ch{chapter_number + 1:03d}",
            )
        )

    humanize_file = root / "50_workbench" / "humanizer_tasks" / f"ch{chapter_number:03d}.humanize_check.json"
    humanize = read_json(humanize_file)
    if humanize:
        observations.extend(
            observations_from_values(
                humanize.get("issues"),
                kind="humanize_check",
                source_path=relative_path(root, humanize_file),
                default_severity="P1" if humanize.get("passed") is False else "P2",
                owner_task=f"humanize:ch{chapter_number:03d}",
            )
        )
        observations.extend(
            observations_from_values(
                humanize.get("warnings"),
                kind="humanize_check",
                source_path=relative_path(root, humanize_file),
                default_severity="P2",
                owner_task=f"chapter_write:ch{chapter_number + 1:03d}",
            )
        )

    pacing_file = gate_dir / "semantic_pacing_result.json"
    pacing = read_json(pacing_file)
    if pacing:
        observations.extend(
            observations_from_values(
                pacing.get("issues"),
                kind="semantic_pacing",
                source_path=relative_path(root, pacing_file),
                default_severity="P1" if str(pacing.get("verdict") or "").lower() == "fail" else "P2",
                owner_task=f"pacing_review:ch{chapter_number:03d}",
            )
        )
        observations.extend(
            observations_from_values(
                pacing.get("warnings"),
                kind="semantic_pacing",
                source_path=relative_path(root, pacing_file),
                default_severity="P2",
                owner_task=f"chapter_write:ch{chapter_number + 1:03d}",
            )
        )

    editorial_file = root / "50_workbench" / "editorial_reviews" / f"ch{chapter_number:03d}.aggregate.json"
    editorial = read_json(editorial_file)
    if editorial:
        observations.extend(
            observations_from_values(
                editorial.get("unresolved_items"),
                kind="editorial_aggregate",
                source_path=relative_path(root, editorial_file),
                default_severity="P2",
                owner_task=f"editorial_review:ch{chapter_number:03d}",
            )
        )

    return refresh_feedback_registry(root, chapter_number=chapter_number, observations=observations)


def refresh_feedback_registry(
    root: Path,
    *,
    chapter_number: int,
    observations: Iterable[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Merge findings by issue code and retain recurrence as auditable state."""

    records = read_registry(root)
    now = utc_now()
    for observation in observations:
        issue_code = sanitize_issue_code(observation.get("issue_code"))
        if not issue_code:
            continue
        severity = normalize_severity(observation.get("severity"), fallback="P2")
        evidence_hash = str(observation.get("evidence_hash") or "").strip()
        existing = next(
            (
                item
                for item in records
                if str(item.get("issue_code") or "") == issue_code
                and str(item.get("scope") or "chapter_range") == "chapter_range"
            ),
            None,
        )
        if existing is None:
            fingerprint = hashlib.sha256(
                f"{issue_code}:{chapter_number}:{evidence_hash}".encode("utf-8")
            ).hexdigest()[:10]
            records.append(
                {
                    "schema": "quality_feedback_item_v1",
                    "feedback_id": f"feedback:{issue_code}:ch{chapter_number:03d}:{fingerprint}",
                    "issue_code": issue_code,
                    "severity": severity,
                    "scope": "chapter_range",
                    "source_chapter": chapter_number,
                    "first_seen_chapter": chapter_number,
                    "last_seen_chapter": chapter_number,
                    "recurrence_count": 1,
                    "status": "open",
                    "expires_after_chapter": chapter_number + 3 if severity == "P2" else None,
                    "evidence_hash": evidence_hash,
                    "resolution_evidence": [],
                    "owner_task": str(observation.get("owner_task") or ""),
                    "source_kind": str(observation.get("kind") or ""),
                    "source_path": str(observation.get("source_path") or ""),
                    "summary": trim_summary(observation.get("summary")),
                    "gate_gaming_risk": False,
                    "created_at": now,
                    "updated_at": now,
                }
            )
            continue

        previous_hash = str(existing.get("evidence_hash") or "")
        existing_status = str(existing.get("status") or "")
        if existing_status == "suppressed":
            continue
        if (
            existing_status in {"resolved", "expired"}
            and int(existing.get("last_seen_chapter") or 0) >= chapter_number
            and (not evidence_hash or evidence_hash == previous_hash)
        ):
            continue
        same_chapter = int(existing.get("last_seen_chapter") or 0) == chapter_number
        existing["severity"] = more_severe(str(existing.get("severity") or "P2"), severity)
        existing["source_chapter"] = chapter_number
        existing["last_seen_chapter"] = chapter_number
        if not same_chapter:
            existing["recurrence_count"] = int(existing.get("recurrence_count") or 1) + 1
        existing["status"] = "open"
        existing["evidence_hash"] = evidence_hash or previous_hash
        existing["owner_task"] = str(observation.get("owner_task") or existing.get("owner_task") or "")
        existing["source_kind"] = str(observation.get("kind") or existing.get("source_kind") or "")
        existing["source_path"] = str(observation.get("source_path") or existing.get("source_path") or "")
        existing["summary"] = trim_summary(observation.get("summary") or existing.get("summary"))
        existing["gate_gaming_risk"] = bool(
            int(existing.get("recurrence_count") or 0) > 1
            and previous_hash
            and evidence_hash
            and previous_hash != evidence_hash
        )
        if existing["severity"] == "P2":
            existing["expires_after_chapter"] = chapter_number + 3
        else:
            existing["expires_after_chapter"] = None
        existing["updated_at"] = now
    records.sort(key=feedback_sort_key)
    write_registry(root, records)
    return tuple(records)


def carry_feedback(
    root: Path,
    *,
    target_chapter: int,
    task_type: str = "chapter_write",
    chapter_role: str = "",
    limit: int = 5,
) -> tuple[dict[str, Any], ...]:
    """Advance lifecycle state and return no more than five relevant items."""

    if target_chapter > 1:
        ingest_chapter_feedback(root, chapter_number=target_chapter - 1)
    records = read_registry(root)
    changed = advance_feedback_lifecycle(records, target_chapter=target_chapter)
    active = [
        item
        for item in records
        if str(item.get("status") or "") in ACTIVE_STATUSES
        and feedback_relevant_to_task(item, task_type)
    ]
    active.sort(key=lambda item: feedback_priority_key(item, chapter_role=chapter_role))
    selected = active[: max(1, min(5, int(limit or 5)))]
    now = utc_now()
    for item in selected:
        if item.get("status") == "open":
            item["status"] = "carried"
            changed = True
        if int(item.get("last_carried_chapter") or 0) != target_chapter:
            item["last_carried_chapter"] = target_chapter
            item["updated_at"] = now
            changed = True
    if changed:
        write_registry(root, records)
    return tuple(public_feedback_item(item) for item in selected)


def feedback_registry_status(
    config: ConfigDocument,
    *,
    target_chapter: int | None = None,
) -> FeedbackRegistryResult:
    root = resolve_project_root(config)
    records = read_registry(root)
    if target_chapter is not None and advance_feedback_lifecycle(records, target_chapter=target_chapter):
        write_registry(root, records)
    return summarize_registry(root, records)


def transition_feedback(
    config: ConfigDocument,
    *,
    feedback_id: str,
    status: str,
    evidence: str,
) -> FeedbackRegistryResult:
    """Resolve or suppress one workbench feedback item explicitly."""

    normalized_status = str(status or "").strip().lower()
    if normalized_status not in {"resolved", "suppressed"}:
        raise ValueError("feedback status must be resolved or suppressed.")
    evidence = str(evidence or "").strip()
    if not evidence:
        raise ValueError("feedback transition requires non-empty evidence.")
    root = resolve_project_root(config)
    records = read_registry(root)
    target = next((item for item in records if item.get("feedback_id") == feedback_id), None)
    if target is None:
        raise ValueError(f"Unknown feedback_id: {feedback_id}")
    target["status"] = normalized_status
    target.setdefault("resolution_evidence", []).append(evidence[:500])
    target["updated_at"] = utc_now()
    write_registry(root, records)
    next_command = "longform-engine production next project.yaml"
    return summarize_registry(
        root,
        records,
        updated_feedback_id=feedback_id,
        next_command=next_command,
    )


def truncate_feedback_registry(root: Path, *, to_chapter: int) -> tuple[str, ...]:
    """Remove feedback sourced only from chapters detached by rollback."""

    path = root / FEEDBACK_REGISTRY
    records = read_registry(root)
    kept = [
        item
        for item in records
        if int(item.get("first_seen_chapter") or item.get("source_chapter") or 0) <= to_chapter
    ]
    changed = len(kept) != len(records)
    for item in kept:
        if int(item.get("last_seen_chapter") or 0) > to_chapter:
            item["last_seen_chapter"] = to_chapter
            item["source_chapter"] = min(int(item.get("source_chapter") or to_chapter), to_chapter)
            item["status"] = "open"
            item["updated_at"] = utc_now()
            changed = True
    if changed:
        write_registry(root, kept)
        return (FEEDBACK_REGISTRY.as_posix(),)
    return ()


def advance_feedback_lifecycle(records: list[dict[str, Any]], *, target_chapter: int) -> bool:
    changed = False
    now = utc_now()
    for item in records:
        status = str(item.get("status") or "")
        if status not in ACTIVE_STATUSES:
            continue
        severity = normalize_severity(item.get("severity"), fallback="P2")
        last_seen = int(item.get("last_seen_chapter") or 0)
        next_status = status
        resolution = ""
        if severity == "P2" and target_chapter > int(item.get("expires_after_chapter") or 0):
            next_status = "expired"
            resolution = "auto:ttl_elapsed"
        elif severity == "P1" and target_chapter - last_seen >= 3:
            next_status = "resolved"
            resolution = "auto:no_recurrence_for_two_completed_chapters"
        if next_status != status:
            item["status"] = next_status
            item.setdefault("resolution_evidence", []).append(resolution)
            item["updated_at"] = now
            changed = True
    return changed


def observations_from_values(
    values: Any,
    *,
    kind: str,
    source_path: str,
    default_severity: str,
    owner_task: str,
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    observations: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, dict):
            summary = str(value.get("message") or value.get("summary") or value.get("code") or "").strip()
            issue_code = (
                value.get("issue_code")
                or value.get("code")
                or value.get("rule")
                or value.get("pattern")
                or infer_issue_code(summary, kind)
            )
            evidence = value.get("evidence") or value.get("evidence_spans") or summary
            severity = normalize_severity(value.get("severity"), fallback=default_severity)
        else:
            summary = str(value or "").strip()
            issue_code = infer_issue_code(summary, kind)
            evidence = summary
            severity = default_severity
        if not summary:
            continue
        observations.append(
            {
                "issue_code": issue_code,
                "severity": severity,
                "kind": kind,
                "source_path": source_path,
                "owner_task": owner_task,
                "summary": summary,
                "evidence_hash": hash_evidence(evidence),
            }
        )
    return observations


def feedback_relevant_to_task(item: dict[str, Any], task_type: str) -> bool:
    issue = str(item.get("issue_code") or "")
    task = str(task_type or "chapter_write")
    if task in {"chapter_write", "repair", "chapter_direction"}:
        return True
    if task in {"humanize", "humanize_semantic_review"}:
        return any(token in issue for token in ("ai_", "diction", "dialogue", "repetition", "detail", "emotion"))
    if task == "pacing_review":
        return any(token in issue for token in ("pacing", "hook", "payoff", "scene", "short_chapter"))
    if task == "editorial_review":
        return True
    return False


def public_feedback_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "quality_feedback_item_v1",
        "feedback_id": str(item.get("feedback_id") or ""),
        "issue_code": str(item.get("issue_code") or ""),
        "severity": normalize_severity(item.get("severity"), fallback="P2"),
        "status": str(item.get("status") or ""),
        "source_chapter": int(item.get("source_chapter") or 0),
        "first_seen_chapter": int(item.get("first_seen_chapter") or 0),
        "last_seen_chapter": int(item.get("last_seen_chapter") or 0),
        "recurrence_count": int(item.get("recurrence_count") or 0),
        "expires_after_chapter": item.get("expires_after_chapter"),
        "evidence_hash": str(item.get("evidence_hash") or ""),
        "owner_task": str(item.get("owner_task") or ""),
        "kind": str(item.get("source_kind") or ""),
        "source": str(item.get("source_path") or ""),
        "summary": trim_summary(item.get("summary")),
        "gate_gaming_risk": bool(item.get("gate_gaming_risk")),
    }


def read_registry(root: Path) -> list[dict[str, Any]]:
    path = root / FEEDBACK_REGISTRY
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid feedback registry JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "quality_feedback_item_v1":
            raise ValueError(f"Invalid feedback registry item at {path}:{line_number}.")
        records.append(payload)
    return records


def write_registry(root: Path, records: list[dict[str, Any]]) -> None:
    path = root / FEEDBACK_REGISTRY
    content = "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records)
    atomic_write_text(path, content)


def summarize_registry(
    root: Path,
    records: list[dict[str, Any]],
    *,
    updated_feedback_id: str = "",
    next_command: str = "",
) -> FeedbackRegistryResult:
    counts = {status: 0 for status in (*ACTIVE_STATUSES, *TERMINAL_STATUSES)}
    for item in records:
        status = str(item.get("status") or "")
        if status in counts:
            counts[status] += 1
    return FeedbackRegistryResult(
        registry_file=str(root / FEEDBACK_REGISTRY),
        total=len(records),
        active=counts["open"] + counts["carried"],
        carried=counts["carried"],
        resolved=counts["resolved"],
        suppressed=counts["suppressed"],
        expired=counts["expired"],
        updated_feedback_id=updated_feedback_id,
        next_command=next_command,
    )


def infer_issue_code(summary: str, kind: str) -> str:
    lower = str(summary or "").lower()
    patterns = (
        ("dialogue_sameness", ("对白同质", "dialogue", "same voice")),
        ("ai_diction_cluster", ("ai 味", "ai味", "ai-flavored", "嘴角", "不禁", "仿佛")),
        ("formula_repetition", ("模板", "重复", "repetition", "formula")),
        ("continuity_risk", ("矛盾", "continuity", "logic", "关系阶段", "时间线")),
        ("pacing_risk", ("节奏", "pacing", "拖沓", "scene pressure")),
        ("fake_payoff", ("伪兑现", "fake payoff", "reader gain")),
        ("forced_hook", ("强制钩子", "cliffhanger", "hook")),
        ("short_chapter", ("字数", "short chapter", "too short")),
    )
    for code, markers in patterns:
        if any(marker in lower for marker in markers):
            return code
    return f"{sanitize_issue_code(kind) or 'quality'}_finding"


def sanitize_issue_code(value: Any) -> str:
    token = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower().replace("-", "_"))
    return re.sub(r"_+", "_", token).strip("_")[:80]


def hash_evidence(value: Any) -> str:
    normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_severity(value: Any, *, fallback: str) -> str:
    token = str(value or "").strip().upper()
    if token in {"CRITICAL", "BLOCKER"}:
        token = "P0"
    elif token in {"HIGH", "ERROR", "FAIL"}:
        token = "P1"
    elif token in {"MEDIUM", "LOW", "WARNING", "WARN"}:
        token = "P2"
    return token if token in SEVERITY_ORDER else fallback


def more_severe(left: str, right: str) -> str:
    return min((normalize_severity(left, fallback="P2"), normalize_severity(right, fallback="P2")), key=SEVERITY_ORDER.get)


def trim_summary(value: Any, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def feedback_sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(item.get("first_seen_chapter") or 0),
        SEVERITY_ORDER.get(normalize_severity(item.get("severity"), fallback="P2"), 2),
        str(item.get("feedback_id") or ""),
    )


def feedback_priority_key(item: dict[str, Any], *, chapter_role: str = "") -> tuple[int, int, int, int, str]:
    return (
        SEVERITY_ORDER.get(normalize_severity(item.get("severity"), fallback="P2"), 2),
        feedback_role_relevance(item, chapter_role),
        -int(item.get("recurrence_count") or 0),
        -int(item.get("last_seen_chapter") or 0),
        str(item.get("feedback_id") or ""),
    )


def feedback_role_relevance(item: dict[str, Any], chapter_role: str) -> int:
    role = str(chapter_role or "").lower()
    issue = str(item.get("issue_code") or "").lower()
    role_issue_markers = (
        (("兑现", "揭露", "reveal", "payoff"), ("payoff", "hook", "pacing", "reveal")),
        (("关系", "情感", "relationship"), ("relationship", "dialogue", "emotion", "voice")),
        (("调查", "线索", "investigation", "clue"), ("continuity", "logic", "timeline", "fact")),
        (("冲突", "行动", "action", "conflict"), ("pacing", "scene", "action", "short_chapter")),
    )
    for role_markers, issue_markers in role_issue_markers:
        if any(marker in role for marker in role_markers):
            return 0 if any(marker in issue for marker in issue_markers) else 1
    return 0


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
