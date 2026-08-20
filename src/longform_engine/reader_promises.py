"""Canonical reader-promise planning state and chapter-bound transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from longform_engine.storage import atomic_write_text


LEDGER_SCHEMA = "reader_promise_ledger_v1"
LEDGER_PATH = Path("30_state/reader_promise_ledger.json")
PROMISE_STATES = {"planned", "open", "escalated", "partially_paid", "paid", "breached", "retired"}
PROMISE_ACTIONS = {"setup", "escalate", "partial_payoff", "payoff", "defer"}
PROMISE_TYPES = {"ability", "status", "mystery", "relationship", "emotion", "resource", "situation"}
ACTION_FIELDS = {"promise_id", "action", "intended_reader_gain", "evidence_requirement", "defer_reason"}
PLANNING_DEFERRAL_FIELDS = {"promise_id", "extended_latest", "reason"}


class ReaderPromiseError(ValueError):
    """Raised when promise planning or lifecycle state is incomplete or stale."""


def empty_reader_promise_ledger() -> dict[str, Any]:
    return {"schema": LEDGER_SCHEMA, "items": [], "updated_at": utc_now()}


def load_reader_promise_ledger(root: Path, *, required: bool = True) -> dict[str, Any]:
    path = root / LEDGER_PATH
    if not path.is_file():
        if required:
            raise ReaderPromiseError("reader_promise_ledger_missing")
        return empty_reader_promise_ledger()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReaderPromiseError(f"reader_promise_ledger_invalid:{exc}") from exc
    errors = validate_reader_promise_ledger(payload)
    if errors:
        raise ReaderPromiseError("reader_promise_ledger_invalid:" + ";".join(errors))
    return payload


def write_reader_promise_ledger(root: Path, payload: dict[str, Any]) -> None:
    errors = validate_reader_promise_ledger(payload)
    if errors:
        raise ReaderPromiseError("reader_promise_ledger_invalid:" + ";".join(errors))
    atomic_write_text(root / LEDGER_PATH, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def reader_promise_ledger_hash(root: Path) -> str:
    payload = load_reader_promise_ledger(root)
    return sha256(canonical_bytes(payload)).hexdigest()


def reader_promise_planning_hash(root: Path) -> str:
    """Hash planning-relevant promise shape without routine occurrence evidence."""

    ledger = load_reader_promise_ledger(root)
    projection = {
        "schema": LEDGER_SCHEMA,
        "items": [
            {
                "promise_id": item["promise_id"],
                "promise_type": item["promise_type"],
                "reader_expectation": item["reader_expectation"],
                "owner_story_engine": item["owner_story_engine"],
                "payoff_window": item["payoff_window"],
                "staged_payoffs": item["staged_payoffs"],
                "terminal_status": item["status"] if item["status"] in {"paid", "breached", "retired"} else "active",
                "deferrals": item["deferrals"],
            }
            for item in ledger["items"]
        ],
    }
    return sha256(canonical_bytes(projection)).hexdigest()


def validate_reader_promise_ledger(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict) or set(payload) != {"schema", "items", "updated_at"}:
        return ["ledger must contain schema, items, updated_at only"]
    if payload.get("schema") != LEDGER_SCHEMA:
        errors.append(f"schema must be {LEDGER_SCHEMA}")
    if not isinstance(payload.get("updated_at"), str) or not payload["updated_at"].strip():
        errors.append("updated_at must be non-empty")
    items = payload.get("items")
    if not isinstance(items, list):
        return [*errors, "items must be a list"]
    seen: set[str] = set()
    for index, item in enumerate(items):
        errors.extend(validate_reader_promise_item(item, index=index, seen=seen))
    return errors


def validate_reader_promise_item(item: Any, *, index: int, seen: set[str]) -> list[str]:
    prefix = f"items[{index}]"
    fields = {
        "promise_id", "promise_type", "reader_expectation", "owner_story_engine",
        "setup_chapter", "payoff_window", "staged_payoffs", "status", "actual_evidence",
        "deferrals", "created_at", "updated_at",
    }
    if not isinstance(item, dict) or set(item) != fields:
        return [f"{prefix} must contain exactly the reader promise fields"]
    errors: list[str] = []
    promise_id = str(item.get("promise_id") or "").strip()
    if not promise_id:
        errors.append(f"{prefix}.promise_id must be non-empty")
    elif promise_id in seen:
        errors.append(f"{prefix}.promise_id is duplicated")
    seen.add(promise_id)
    if item.get("promise_type") not in PROMISE_TYPES:
        errors.append(f"{prefix}.promise_type is unsupported")
    for field in ("reader_expectation", "owner_story_engine", "created_at", "updated_at"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            errors.append(f"{prefix}.{field} must be non-empty")
    setup = item.get("setup_chapter")
    if setup is not None and (not isinstance(setup, int) or isinstance(setup, bool) or setup <= 0):
        errors.append(f"{prefix}.setup_chapter must be null or positive integer")
    window = item.get("payoff_window")
    if (
        not isinstance(window, dict)
        or set(window) != {"earliest", "target", "latest"}
        or any(not isinstance(window.get(key), int) or isinstance(window.get(key), bool) for key in window)
        or not (0 < window.get("earliest", 0) <= window.get("target", 0) <= window.get("latest", 0))
    ):
        errors.append(f"{prefix}.payoff_window must be ordered earliest/target/latest chapters")
    if item.get("status") not in PROMISE_STATES:
        errors.append(f"{prefix}.status is unsupported")
    for field in ("staged_payoffs", "actual_evidence", "deferrals"):
        if not isinstance(item.get(field), list):
            errors.append(f"{prefix}.{field} must be a list")
    for evidence_index, evidence in enumerate(item.get("actual_evidence") or []):
        evidence_prefix = f"{prefix}.actual_evidence[{evidence_index}]"
        if not isinstance(evidence, dict) or set(evidence) != {
            "chapter_number", "action", "reader_gain", "source_path", "source_sha256",
        }:
            errors.append(f"{evidence_prefix} has invalid fields")
            continue
        if (
            not isinstance(evidence.get("chapter_number"), int)
            or isinstance(evidence.get("chapter_number"), bool)
            or evidence["chapter_number"] <= 0
        ):
            errors.append(f"{evidence_prefix}.chapter_number must be positive")
        if evidence.get("action") not in PROMISE_ACTIONS:
            errors.append(f"{evidence_prefix}.action is unsupported")
        for field in ("reader_gain", "source_path"):
            if not isinstance(evidence.get(field), str) or not evidence[field].strip():
                errors.append(f"{evidence_prefix}.{field} must be non-empty")
        if not is_sha256(evidence.get("source_sha256")):
            errors.append(f"{evidence_prefix}.source_sha256 must be SHA-256")
    for deferral_index, deferral in enumerate(item.get("deferrals") or []):
        deferral_prefix = f"{prefix}.deferrals[{deferral_index}]"
        if not isinstance(deferral, dict) or set(deferral) != {
            "chapter_number", "reason", "approved_by", "previous_latest", "extended_latest",
        }:
            errors.append(f"{deferral_prefix} has invalid fields")
            continue
        if (
            not isinstance(deferral.get("chapter_number"), int)
            or isinstance(deferral.get("chapter_number"), bool)
            or deferral["chapter_number"] <= 0
        ):
            errors.append(f"{deferral_prefix}.chapter_number must be positive")
        if not isinstance(deferral.get("reason"), str) or not deferral["reason"].strip():
            errors.append(f"{deferral_prefix}.reason must be non-empty")
        if deferral.get("approved_by") != "human":
            errors.append(f"{deferral_prefix}.approved_by must be human")
        previous_latest = deferral.get("previous_latest")
        extended_latest = deferral.get("extended_latest")
        if (
            not isinstance(previous_latest, int)
            or isinstance(previous_latest, bool)
            or not isinstance(extended_latest, int)
            or isinstance(extended_latest, bool)
            or extended_latest <= previous_latest
        ):
            errors.append(f"{deferral_prefix} must strictly extend an integer latest boundary")
    return errors


def validate_promise_actions(actions: Any, ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(actions, list) or not actions:
        return ["reader_promise_actions must be a non-empty list"]
    available = {str(item.get("promise_id")): item for item in ledger.get("items") or [] if isinstance(item, dict)}
    seen: set[str] = set()
    for index, action in enumerate(actions):
        prefix = f"reader_promise_actions[{index}]"
        if not isinstance(action, dict) or set(action) != ACTION_FIELDS:
            errors.append(f"{prefix} must contain exactly {sorted(ACTION_FIELDS)}")
            continue
        promise_id = str(action.get("promise_id") or "").strip()
        if promise_id not in available:
            errors.append(f"{prefix}.promise_id is unresolved")
        elif promise_id in seen:
            errors.append(f"{prefix}.promise_id is duplicated")
        seen.add(promise_id)
        if action.get("action") not in PROMISE_ACTIONS:
            errors.append(f"{prefix}.action is unsupported")
        for field in ("intended_reader_gain", "evidence_requirement"):
            if not isinstance(action.get(field), str) or not action[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        defer_reason = action.get("defer_reason")
        if action.get("action") == "defer":
            if not isinstance(defer_reason, str) or not defer_reason.strip():
                errors.append(f"{prefix}.defer_reason is required for defer")
        elif defer_reason not in {"", None}:
            errors.append(f"{prefix}.defer_reason is only allowed for defer")
    return errors


def promise_deadline_status(root: Path, *, chapter_number: int) -> dict[str, list[str]]:
    ledger = load_reader_promise_ledger(root)
    warnings: list[str] = []
    blockers: list[str] = []
    for item in ledger["items"]:
        if item["status"] in {"paid", "retired"}:
            continue
        target = int(item["payoff_window"]["target"])
        latest = int(item["payoff_window"]["latest"])
        if chapter_number > latest:
            blockers.append(f"promise_breached:{item['promise_id']}")
        elif chapter_number >= target:
            warnings.append(f"promise_target_due:{item['promise_id']}")
    return {"warnings": warnings, "blockers": blockers}


def apply_reader_promise_actions(
    root: Path,
    *,
    chapter_number: int,
    actions: Iterable[dict[str, Any]],
    final_path: str,
    final_sha256: str,
) -> dict[str, Any]:
    ledger = load_reader_promise_ledger(root)
    normalized_actions = list(actions)
    errors = validate_promise_actions(normalized_actions, ledger)
    if errors:
        raise ReaderPromiseError("reader_promise_actions_invalid:" + ";".join(errors))
    by_id = {str(item["promise_id"]): item for item in ledger["items"]}
    transitions = {
        "setup": "open",
        "escalate": "escalated",
        "partial_payoff": "partially_paid",
        "payoff": "paid",
    }
    allowed_from = {
        "setup": {"planned"},
        "escalate": {"open", "escalated"},
        "partial_payoff": {"open", "escalated", "partially_paid"},
        "payoff": {"open", "escalated", "partially_paid"},
        "defer": {"planned", "open", "escalated", "partially_paid"},
    }
    now = utc_now()
    for action in normalized_actions:
        item = by_id[str(action["promise_id"])]
        action_name = str(action["action"])
        current_status = str(item.get("status") or "planned")
        if current_status in {"paid", "breached", "retired"}:
            raise ReaderPromiseError(
                f"reader_promise_terminal:{item['promise_id']}:{current_status}"
            )
        if current_status not in allowed_from[action_name]:
            raise ReaderPromiseError(
                f"reader_promise_transition_invalid:{item['promise_id']}:{current_status}->{action_name}"
            )
        if action_name == "setup" and item.get("setup_chapter") is None:
            item["setup_chapter"] = chapter_number
        if action_name == "defer":
            previous_latest = int(item["payoff_window"]["latest"])
            extended_latest = chapter_number + 1
            if extended_latest <= previous_latest:
                raise ReaderPromiseError(
                    f"reader_promise_defer_not_extending:{item['promise_id']}:{previous_latest}"
                )
            item["payoff_window"]["latest"] = extended_latest
            item["deferrals"].append(
                {
                    "chapter_number": chapter_number,
                    "reason": str(action["defer_reason"]),
                    "approved_by": "human",
                    "previous_latest": previous_latest,
                    "extended_latest": extended_latest,
                }
            )
        else:
            next_status = transitions[action_name]
            item["status"] = next_status
        item["actual_evidence"].append(
            {
                "chapter_number": chapter_number,
                "action": action_name,
                "reader_gain": str(action["intended_reader_gain"]),
                "source_path": final_path,
                "source_sha256": final_sha256,
            }
        )
        item["updated_at"] = now
    for item in ledger["items"]:
        if item["status"] not in {"paid", "retired"} and chapter_number > int(item["payoff_window"]["latest"]):
            item["status"] = "breached"
            item["updated_at"] = now
    ledger["updated_at"] = now
    write_reader_promise_ledger(root, ledger)
    return ledger


def validate_planning_deferrals(
    values: Any,
    ledger: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(values, list) or not values:
        return ["reader_promise_deferrals must be a non-empty list"]
    by_id = {
        str(item.get("promise_id") or ""): item
        for item in ledger.get("items") or []
        if isinstance(item, dict)
    }
    seen: set[str] = set()
    for index, value in enumerate(values):
        prefix = f"reader_promise_deferrals[{index}]"
        if not isinstance(value, dict) or set(value) != PLANNING_DEFERRAL_FIELDS:
            errors.append(f"{prefix} must contain exactly {sorted(PLANNING_DEFERRAL_FIELDS)}")
            continue
        promise_id = str(value.get("promise_id") or "").strip()
        item = by_id.get(promise_id)
        if item is None:
            errors.append(f"{prefix}.promise_id is unresolved")
        elif promise_id in seen:
            errors.append(f"{prefix}.promise_id is duplicated")
        elif item.get("status") in {"paid", "breached", "retired"}:
            errors.append(f"{prefix}.promise_id is terminal")
        seen.add(promise_id)
        extended = value.get("extended_latest")
        current_latest = int((item or {}).get("payoff_window", {}).get("latest") or 0)
        if (
            not isinstance(extended, int)
            or isinstance(extended, bool)
            or extended <= current_latest
        ):
            errors.append(f"{prefix}.extended_latest must strictly extend the current latest chapter")
        if not isinstance(value.get("reason"), str) or not value["reason"].strip():
            errors.append(f"{prefix}.reason must be non-empty")
    return errors


def apply_planning_deferrals(
    ledger: dict[str, Any],
    *,
    values: Any,
    chapter_number: int,
    approved_by: str,
) -> dict[str, Any]:
    """Apply explicit human delay decisions inside the caller's planning transaction."""

    if approved_by != "human":
        raise ReaderPromiseError("reader_promise_deferrals_require_human")
    errors = validate_planning_deferrals(values, ledger)
    if errors:
        raise ReaderPromiseError("reader_promise_deferrals_invalid:" + ";".join(errors))
    by_id = {str(item["promise_id"]): item for item in ledger["items"]}
    now = utc_now()
    for value in values:
        item = by_id[str(value["promise_id"])]
        previous_latest = int(item["payoff_window"]["latest"])
        extended_latest = int(value["extended_latest"])
        item["payoff_window"]["latest"] = extended_latest
        item["deferrals"].append(
            {
                "chapter_number": chapter_number,
                "reason": str(value["reason"]),
                "approved_by": approved_by,
                "previous_latest": previous_latest,
                "extended_latest": extended_latest,
            }
        )
        item["updated_at"] = now
    ledger["updated_at"] = now
    validation_errors = validate_reader_promise_ledger(ledger)
    if validation_errors:
        raise ReaderPromiseError(
            "reader_promise_ledger_invalid:" + ";".join(validation_errors)
        )
    return ledger


def truncate_reader_promise_ledger(root: Path, *, to_chapter: int) -> str:
    """Remove lifecycle effects after a rollback boundary and recompute promise state."""

    ledger = load_reader_promise_ledger(root)
    state_by_action = {
        "setup": "open",
        "escalate": "escalated",
        "partial_payoff": "partially_paid",
        "payoff": "paid",
    }
    now = utc_now()
    for item in ledger["items"]:
        all_deferrals = [row for row in item["deferrals"] if isinstance(row, dict)]
        original_latest = (
            int(all_deferrals[0]["previous_latest"])
            if all_deferrals
            else int(item["payoff_window"]["latest"])
        )
        evidence = [
            row for row in item["actual_evidence"]
            if isinstance(row, dict) and int(row.get("chapter_number") or 0) <= to_chapter
        ]
        deferrals = [
            row for row in item["deferrals"]
            if isinstance(row, dict) and int(row.get("chapter_number") or 0) <= to_chapter
        ]
        item["actual_evidence"] = evidence
        item["deferrals"] = deferrals
        item["payoff_window"]["latest"] = (
            int(deferrals[-1]["extended_latest"])
            if deferrals
            else original_latest
        )
        item["setup_chapter"] = next(
            (
                int(row["chapter_number"])
                for row in evidence
                if row.get("action") == "setup"
            ),
            None,
        )
        lifecycle = [row for row in evidence if row.get("action") in state_by_action]
        item["status"] = state_by_action[str(lifecycle[-1]["action"])] if lifecycle else "planned"
        item["updated_at"] = now
    ledger["updated_at"] = now
    write_reader_promise_ledger(root, ledger)
    return LEDGER_PATH.as_posix()


def materialize_reader_promise_ledger(items: list[dict[str, Any]]) -> dict[str, Any]:
    now = utc_now()
    materialized = {
        "schema": LEDGER_SCHEMA,
        "items": [
            {
                "promise_id": str(item.get("promise_id") or ""),
                "promise_type": item.get("promise_type"),
                "reader_expectation": str(item.get("reader_expectation") or ""),
                "owner_story_engine": str(item.get("owner_story_engine") or ""),
                "setup_chapter": item.get("setup_chapter"),
                "payoff_window": item.get("payoff_window"),
                "staged_payoffs": list(item.get("staged_payoffs") or []),
                "status": str(item.get("status") or "planned"),
                "actual_evidence": list(item.get("actual_evidence") or []),
                "deferrals": list(item.get("deferrals") or []),
                "created_at": str(item.get("created_at") or now),
                "updated_at": str(item.get("updated_at") or now),
            }
            for item in items
        ],
        "updated_at": now,
    }
    errors = validate_reader_promise_ledger(materialized)
    if errors:
        raise ReaderPromiseError("reader_promise_ledger_invalid:" + ";".join(errors))
    return materialized


def merge_planned_reader_promises(
    root: Path,
    *,
    story_engine_contract: dict[str, Any],
    foreshadowing_ledger: Iterable[dict[str, Any]],
    estimated_chapters: int,
) -> dict[str, Any]:
    """Project approved story promises without overwriting lifecycle evidence."""

    current = load_reader_promise_ledger(root, required=False)
    existing = {
        str(item.get("promise_id")): item
        for item in current.get("items") or []
        if isinstance(item, dict) and item.get("promise_id")
    }
    raw_payoffs = story_engine_contract.get("expected_payoffs")
    payoffs: dict[str, Any] = raw_payoffs if isinstance(raw_payoffs, dict) else {}
    opening_latest = min(3, max(1, estimated_chapters))
    early_target = min(max(6, opening_latest + 1), max(1, estimated_chapters))
    early_latest = min(max(12, early_target), max(1, estimated_chapters))
    volume_target = min(max(early_latest + 1, estimated_chapters // 4), max(1, estimated_chapters))
    specs: list[dict[str, Any]] = [
        promise_spec(
            "story_engine:opening_three",
            "situation",
            str(payoffs.get("opening_three") or "前三章兑现核心行动循环与首次可见收益。"),
            "story_engine_contract",
            1,
            opening_latest,
            opening_latest,
        ),
        promise_spec(
            "story_engine:early_serial",
            "status",
            str(payoffs.get("early_serial") or "早期连载形成可辨认的升级与局势变化。"),
            "story_engine_contract",
            min(4, early_target),
            early_target,
            early_latest,
        ),
        promise_spec(
            "story_engine:volume_end",
            "emotion",
            str(payoffs.get("volume_end") or "卷末完成阶段冲突并支付主要代价。"),
            "story_engine_contract",
            max(1, volume_target - 3),
            volume_target,
            min(max(volume_target, volume_target + 3), max(1, estimated_chapters)),
        ),
    ]
    for thread in foreshadowing_ledger:
        if not isinstance(thread, dict) or not thread.get("id"):
            continue
        window = thread.get("payoff_window")
        if not isinstance(window, list) or len(window) != 2:
            continue
        earliest = max(1, int(window[0]))
        latest = max(earliest, int(window[1]))
        specs.append(
            promise_spec(
                f"foreshadow:{thread['id']}",
                "mystery",
                str(thread.get("question") or thread.get("reader_expectation") or thread.get("description") or thread["id"]),
                str(thread.get("arc_id") or "foreshadowing"),
                earliest,
                earliest + (latest - earliest) // 2,
                latest,
            )
        )
    merged: list[dict[str, Any]] = []
    planned_ids = {str(item["promise_id"]) for item in specs}
    for spec in specs:
        old = existing.get(str(spec["promise_id"]))
        if old is not None:
            old_deferrals = list(old.get("deferrals") or [])
            if old_deferrals:
                spec["payoff_window"]["latest"] = max(
                    int(spec["payoff_window"]["latest"]),
                    int(old_deferrals[-1].get("extended_latest") or 0),
                )
            spec.update(
                {
                    "setup_chapter": old.get("setup_chapter"),
                    "status": old.get("status", "planned"),
                    "actual_evidence": list(old.get("actual_evidence") or []),
                    "deferrals": old_deferrals,
                    "created_at": old.get("created_at") or spec["created_at"],
                }
            )
        merged.append(spec)
    for promise_id, old in existing.items():
        if promise_id not in planned_ids:
            retired = dict(old)
            if retired.get("status") not in {"paid", "retired"}:
                retired["status"] = "retired"
            retired["updated_at"] = utc_now()
            merged.append(retired)
    return materialize_reader_promise_ledger(merged)


def promise_spec(
    promise_id: str,
    promise_type: str,
    expectation: str,
    owner: str,
    earliest: int,
    target: int,
    latest: int,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "promise_id": promise_id,
        "promise_type": promise_type,
        "reader_expectation": expectation,
        "owner_story_engine": owner,
        "setup_chapter": None,
        "payoff_window": {"earliest": earliest, "target": target, "latest": latest},
        "staged_payoffs": [],
        "status": "planned",
        "actual_evidence": [],
        "deferrals": [],
        "created_at": now,
        "updated_at": now,
    }


def canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def is_sha256(value: Any) -> bool:
    token = str(value or "").strip().lower()
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
