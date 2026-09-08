"""Explicit, evidence-bound reader promises for the current protocol."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


LEDGER_SCHEMA = "reader_promise_ledger_v2"
PROMISE_SCHEMA = "reader_promise_v2"
EVIDENCE_APPLICATION_SCHEMA = "reader_promise_evidence_application_v1"
LEDGER_PATH = Path("30_state/reader_promise_ledger.json")
PROMISE_ACTIONS = frozenset({"setup", "escalate", "partial_payoff", "payoff", "defer"})
TERMINAL_STATES = frozenset({"paid", "retired", "breached"})
PROMISE_STATES = frozenset(
    {"planned", "open", "escalated", "partially_paid", "paid", "breached", "retired"}
)
PLANNING_DEFERRAL_FIELDS = frozenset({"promise_id", "extended_latest", "reason"})
STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$")


@dataclass(frozen=True)
class PromiseEvidenceApplyResult:
    chapter_number: int
    actions: int
    ledger_file: str
    transaction_report: str


class ReaderPromiseError(ValueError):
    """Raised when the current reader-promise ledger is missing or invalid."""


def empty_reader_promise_ledger() -> dict[str, Any]:
    return materialize_explicit_reader_promises([], approved_by="human")


def load_reader_promise_ledger(root: Path, *, required: bool = True) -> dict[str, Any]:
    path = root / LEDGER_PATH
    if not path.is_file():
        if required:
            raise ReaderPromiseError("reader_promise_ledger_missing")
        return empty_reader_promise_ledger()
    try:
        payload = _read_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ReaderPromiseError(f"reader_promise_ledger_invalid:{exc}") from exc
    errors = validate_reader_promise_ledger(payload)
    if errors:
        raise ReaderPromiseError("reader_promise_ledger_invalid:" + ";".join(errors))
    return payload


def write_reader_promise_ledger(root: Path, payload: dict[str, Any]) -> None:
    errors = validate_reader_promise_ledger(payload)
    if errors:
        raise ReaderPromiseError("reader_promise_ledger_invalid:" + ";".join(errors))
    _write_json(root / LEDGER_PATH, payload)


def validate_reader_promise_ledger(payload: Any) -> list[str]:
    if not isinstance(payload, dict) or set(payload) != {"schema", "items"}:
        return ["ledger must contain schema and items only"]
    errors: list[str] = []
    if payload.get("schema") != LEDGER_SCHEMA:
        errors.append(f"schema must be {LEDGER_SCHEMA}")
    items = payload.get("items")
    if not isinstance(items, list):
        return [*errors, "items must be a list"]
    candidate_fields = {
        "schema",
        "promise_id",
        "reader_expectation",
        "owner_ref",
        "payoff_window",
        "staged_payoffs",
        "selected_by",
    }
    runtime_fields = {
        "status",
        "completed_stage_ids",
        "actual_evidence",
        "deferrals",
    }
    candidates: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(item, dict) or set(item) != candidate_fields | runtime_fields:
            errors.append(f"{prefix} fields are invalid")
            continue
        candidates.append({field: item[field] for field in candidate_fields})
        if item.get("selected_by") != "human":
            errors.append(f"{prefix}.selected_by must be human")
        if item.get("status") not in PROMISE_STATES:
            errors.append(f"{prefix}.status is invalid")
        completed = item.get("completed_stage_ids")
        stage_ids = {
            str(stage.get("stage_id"))
            for stage in item.get("staged_payoffs", [])
            if isinstance(stage, dict)
        }
        if (
            not isinstance(completed, list)
            or any(not isinstance(stage_id, str) or stage_id not in stage_ids for stage_id in completed)
            or len(set(completed)) != len(completed)
        ):
            errors.append(f"{prefix}.completed_stage_ids is invalid")
        for field in ("actual_evidence", "deferrals"):
            if not isinstance(item.get(field), list) or any(
                not isinstance(row, dict) for row in item.get(field, [])
            ):
                errors.append(f"{prefix}.{field} must be an object list")
    candidate_errors = validate_reader_promise_candidates(candidates, label="items")
    errors.extend(error for error in candidate_errors if ".selected_by" not in error)
    return errors


def reader_promise_planning_hash(root: Path) -> str:
    """Hash planning authority while excluding routine evidence-only changes."""

    ledger = load_reader_promise_ledger(root)
    projection = {
        "schema": LEDGER_SCHEMA,
        "items": [
            {
                "schema": item["schema"],
                "promise_id": item["promise_id"],
                "reader_expectation": item["reader_expectation"],
                "owner_ref": item["owner_ref"],
                "payoff_window": item["payoff_window"],
                "staged_payoffs": item["staged_payoffs"],
                "selected_by": item["selected_by"],
                "terminal_status": item["status"] if item["status"] in TERMINAL_STATES else "active",
                "deferrals": item["deferrals"],
            }
            for item in ledger["items"]
        ],
    }
    return sha256(_canonical_bytes(projection)).hexdigest()


def promise_deadline_status(root: Path, *, chapter_number: int) -> dict[str, list[str]]:
    ledger = load_reader_promise_ledger(root)
    warnings: list[str] = []
    blockers: list[str] = []
    for item in ledger["items"]:
        if item["status"] in TERMINAL_STATES:
            continue
        target = int(item["payoff_window"]["target"])
        latest = int(item["payoff_window"]["latest"])
        if chapter_number > latest:
            blockers.append(f"promise_breached:{item['promise_id']}")
        elif chapter_number >= target:
            warnings.append(f"promise_target_due:{item['promise_id']}")
    return {"warnings": warnings, "blockers": blockers}


def validate_planning_deferrals(values: Any, ledger: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(values, list) or not values:
        return ["reader_promise_deferrals must be a non-empty list"]
    by_id = {
        str(item.get("promise_id")): item
        for item in ledger.get("items", [])
        if isinstance(item, dict) and item.get("promise_id")
    }
    seen: set[str] = set()
    for index, value in enumerate(values):
        prefix = f"reader_promise_deferrals[{index}]"
        if not isinstance(value, dict) or set(value) != PLANNING_DEFERRAL_FIELDS:
            errors.append(f"{prefix} fields are invalid")
            continue
        promise_id = value.get("promise_id")
        item = by_id.get(str(promise_id))
        if item is None:
            errors.append(f"{prefix}.promise_id is unresolved")
        elif promise_id in seen:
            errors.append(f"{prefix}.promise_id is duplicated")
        elif item.get("status") in TERMINAL_STATES:
            errors.append(f"{prefix}.promise_id is terminal")
        seen.add(str(promise_id))
        extended = value.get("extended_latest")
        current_latest = int((item or {}).get("payoff_window", {}).get("latest") or 0)
        if (
            not isinstance(extended, int)
            or isinstance(extended, bool)
            or extended <= current_latest
        ):
            errors.append(f"{prefix}.extended_latest must extend the current latest chapter")
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
    if approved_by != "human":
        raise ReaderPromiseError("reader_promise_deferrals_require_human")
    errors = validate_planning_deferrals(values, ledger)
    if errors:
        raise ReaderPromiseError("reader_promise_deferrals_invalid:" + ";".join(errors))
    by_id = {str(item["promise_id"]): item for item in ledger["items"]}
    for value in values:
        item = by_id[str(value["promise_id"])]
        previous_latest = int(item["payoff_window"]["latest"])
        extended_latest = int(value["extended_latest"])
        item["payoff_window"]["latest"] = extended_latest
        item["deferrals"].append(
            {
                "chapter_number": chapter_number,
                "reason": str(value["reason"]),
                "approved_by": "human",
                "previous_latest": previous_latest,
                "extended_latest": extended_latest,
            }
        )
    validation_errors = validate_reader_promise_ledger(ledger)
    if validation_errors:
        raise ReaderPromiseError("reader_promise_ledger_invalid:" + ";".join(validation_errors))
    return ledger


def truncate_reader_promise_ledger(root: Path, *, to_chapter: int) -> str:
    """Discard reader-promise lifecycle effects after a rollback boundary."""

    ledger = load_reader_promise_ledger(root)
    state_by_action = {
        "setup": "open",
        "escalate": "escalated",
        "partial_payoff": "partially_paid",
        "payoff": "paid",
    }
    for item in ledger["items"]:
        all_deferrals = [row for row in item["deferrals"] if isinstance(row, dict)]
        extending = [row for row in all_deferrals if "extended_latest" in row]
        original_latest = (
            int(extending[0]["previous_latest"])
            if extending
            else int(item["payoff_window"]["latest"])
        )
        evidence = [
            row
            for row in item["actual_evidence"]
            if int(row.get("chapter_number") or 0) <= to_chapter
        ]
        deferrals = [
            row
            for row in all_deferrals
            if int(row.get("chapter_number") or 0) <= to_chapter
        ]
        retained_extensions = [row for row in deferrals if "extended_latest" in row]
        item["actual_evidence"] = evidence
        item["deferrals"] = deferrals
        item["payoff_window"]["latest"] = (
            int(retained_extensions[-1]["extended_latest"])
            if retained_extensions
            else original_latest
        )
        completed = [
            str(row["stage_id"])
            for row in evidence
            if row.get("action") in {"partial_payoff", "payoff"} and row.get("stage_id")
        ]
        item["completed_stage_ids"] = list(dict.fromkeys(completed))
        lifecycle = [row for row in evidence if row.get("action") in state_by_action]
        item["status"] = state_by_action[str(lifecycle[-1]["action"])] if lifecycle else "planned"
    write_reader_promise_ledger(root, ledger)
    return LEDGER_PATH.as_posix()


def validate_reader_promise_candidates(value: Any, *, label: str = "reader_promises") -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list):
        return [f"{label} must be a list"]
    seen: set[str] = set()
    fields = {
        "schema",
        "promise_id",
        "reader_expectation",
        "owner_ref",
        "payoff_window",
        "staged_payoffs",
        "selected_by",
    }
    for index, item in enumerate(value):
        prefix = f"{label}[{index}]"
        if not isinstance(item, dict) or set(item) != fields:
            errors.append(f"{prefix} fields are invalid")
            continue
        if item.get("schema") != PROMISE_SCHEMA:
            errors.append(f"{prefix}.schema must be {PROMISE_SCHEMA}")
        promise_id = item.get("promise_id")
        if not isinstance(promise_id, str) or not STABLE_ID.fullmatch(promise_id):
            errors.append(f"{prefix}.promise_id must be a stable ID")
        elif promise_id in seen:
            errors.append(f"duplicate promise_id: {promise_id}")
        seen.add(str(promise_id))
        for field in ("reader_expectation", "owner_ref"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        _validate_window(item.get("payoff_window"), f"{prefix}.payoff_window", errors)
        stages = item.get("staged_payoffs")
        if not isinstance(stages, list):
            errors.append(f"{prefix}.staged_payoffs must be a list")
            stages = []
        stage_ids: set[str] = set()
        for stage_index, stage in enumerate(stages):
            stage_prefix = f"{prefix}.staged_payoffs[{stage_index}]"
            if not isinstance(stage, dict) or set(stage) != {
                "stage_id",
                "description",
                "window",
            }:
                errors.append(f"{stage_prefix} fields are invalid")
                continue
            stage_id = stage.get("stage_id")
            if not isinstance(stage_id, str) or not STABLE_ID.fullmatch(stage_id):
                errors.append(f"{stage_prefix}.stage_id must be stable")
            elif stage_id in stage_ids:
                errors.append(f"duplicate staged payoff ID: {stage_id}")
            stage_ids.add(str(stage_id))
            if not isinstance(stage.get("description"), str) or not stage["description"].strip():
                errors.append(f"{stage_prefix}.description must be non-empty")
            _validate_range(stage.get("window"), f"{stage_prefix}.window", errors)
        if item.get("selected_by") is not None:
            errors.append(f"{prefix}.selected_by is CLI-owned and must be null in a candidate")
    return errors


def materialize_explicit_reader_promises(
    candidates: list[dict[str, Any]],
    *,
    approved_by: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply approved planning while retaining previously confirmed actual progress.

    Omission from a rolling window does not retire a promise. Changing an already
    observed promise's meaning needs the explicit revision workflow.
    """

    if approved_by != "human":
        raise ValueError("reader promises must be explicitly selected by human")
    errors = validate_reader_promise_candidates(candidates)
    if errors:
        raise ValueError("reader promises are invalid: " + "; ".join(errors))
    ledger: dict[str, Any] = {
        "schema": LEDGER_SCHEMA,
        "items": [
            {
                **item,
                "selected_by": "human",
                "status": "planned",
                "completed_stage_ids": [],
                "actual_evidence": [],
                "deferrals": [],
            }
            for item in candidates
        ],
    }
    if existing is None:
        return ledger
    errors = validate_reader_promise_ledger(existing)
    if errors:
        raise ReaderPromiseError("existing reader promises are invalid: " + "; ".join(errors))
    previous = {item["promise_id"]: item for item in existing["items"]}
    selected = {item["promise_id"] for item in ledger["items"]}
    for item in ledger["items"]:
        old = previous.get(item["promise_id"])
        if old is None:
            continue
        if old["actual_evidence"] or old["status"] in TERMINAL_STATES:
            for field in ("reader_expectation", "owner_ref", "staged_payoffs"):
                if item[field] != old[field]:
                    raise ReaderPromiseError(f"observed_promise_requires_revision:{item['promise_id']}:{field}")
        if old["deferrals"] and item["payoff_window"]["latest"] < old["payoff_window"]["latest"]:
            raise ReaderPromiseError(f"promise_deferral_cannot_be_erased:{item['promise_id']}")
        for field in ("status", "completed_stage_ids", "actual_evidence", "deferrals"):
            item[field] = old[field]
    ledger["items"].extend(item for key, item in previous.items() if key not in selected)
    errors = validate_reader_promise_ledger(ledger)
    if errors:
        raise ReaderPromiseError("refreshed reader promises are invalid: " + "; ".join(errors))
    return ledger


def validate_promise_actions_v2(
    actions: Any,
    ledger: dict[str, Any],
    *,
    chapter_number: int | None = None,
) -> list[str]:
    """Validate actions; an ordinary chapter may intentionally declare an empty list."""

    errors: list[str] = []
    if not isinstance(actions, list):
        return ["reader promise actions must be a list"]
    by_id = {
        str(item.get("promise_id")): item
        for item in ledger.get("items", [])
        if isinstance(item, dict) and item.get("promise_id")
    }
    seen: set[str] = set()
    fields = {
        "promise_id",
        "action",
        "stage_id",
        "intended_reader_gain",
        "evidence_requirement",
        "defer_reason",
    }
    for index, item in enumerate(actions):
        prefix = f"reader_promise_actions[{index}]"
        if not isinstance(item, dict) or set(item) != fields:
            errors.append(f"{prefix} fields are invalid")
            continue
        promise_id = item.get("promise_id")
        promise = by_id.get(str(promise_id))
        if promise is None:
            errors.append(f"{prefix}.promise_id is unresolved")
        elif promise_id in seen:
            errors.append(f"duplicate promise action: {promise_id}")
        elif promise.get("status") in TERMINAL_STATES and not (
            chapter_number is not None
            and any(
                isinstance(evidence, dict)
                and evidence.get("chapter_number") == chapter_number
                and evidence.get("action") == item.get("action")
                and evidence.get("confirmed_by") == "human"
                for evidence in promise.get("actual_evidence", [])
            )
        ):
            errors.append(f"{prefix}.promise_id is terminal")
        seen.add(str(promise_id))
        action = item.get("action")
        if action not in PROMISE_ACTIONS:
            errors.append(f"{prefix}.action is invalid")
        stage_id = item.get("stage_id")
        if action in {"partial_payoff", "payoff"}:
            stages = {
                str(stage.get("stage_id"))
                for stage in (promise or {}).get("staged_payoffs", [])
                if isinstance(stage, dict)
            }
            if not isinstance(stage_id, str) or stage_id not in stages:
                errors.append(f"{prefix}.stage_id must reference an explicit staged payoff")
        elif stage_id not in {None, ""}:
            errors.append(f"{prefix}.stage_id is only allowed for payoff actions")
        for field in ("intended_reader_gain", "evidence_requirement"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        if action == "defer":
            if not isinstance(item.get("defer_reason"), str) or not item["defer_reason"].strip():
                errors.append(f"{prefix}.defer_reason is required")
        elif item.get("defer_reason") not in {None, ""}:
            errors.append(f"{prefix}.defer_reason is only allowed for defer")
    return errors


def build_promise_evidence_application(
    root: Path,
    *,
    chapter_number: int,
    actions: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    confirmed_by: str,
) -> dict[str, Any]:
    if confirmed_by != "human":
        raise ValueError("reader promise evidence must be human-confirmed")
    final = manuscript_chapter_path(root, chapter_number, lane="final")
    semantic = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    ledger = root / "30_state" / "reader_promise_ledger.json"
    for path in (final, semantic, ledger):
        if not path.is_file():
            raise ValueError(f"reader promise evidence source is missing: {path.relative_to(root)}")
    return {
        "schema": EVIDENCE_APPLICATION_SCHEMA,
        "chapter_number": chapter_number,
        "ledger": {"path": ledger.relative_to(root).as_posix(), "sha256": _file_hash(ledger)},
        "final": {"path": final.relative_to(root).as_posix(), "sha256": _file_hash(final)},
        "semantic_ledger": {
            "path": semantic.relative_to(root).as_posix(),
            "sha256": _file_hash(semantic),
        },
        "actions": actions,
        "evidence": evidence,
        "confirmed_by": "human",
    }


def apply_promise_evidence(
    config: ConfigDocument,
    *,
    application_path: str | Path,
) -> PromiseEvidenceApplyResult:
    root = resolve_project_root(config)
    application_file = _resolve_file(root, application_path)
    payload = _read_json(application_file)
    errors = validate_promise_evidence_application(root, payload)
    if errors:
        raise ValueError("reader promise evidence is invalid: " + "; ".join(errors))
    ledger_file = _resolve_file(root, payload["ledger"]["path"])
    ledger = _read_json(ledger_file)
    evidence_by_id = {item["promise_id"]: item for item in payload["evidence"]}
    actions_by_id = {item["promise_id"]: item for item in payload["actions"]}
    updated = []
    for promise in ledger["items"]:
        promise_id = promise["promise_id"]
        action = actions_by_id.get(promise_id)
        if action is None:
            updated.append(promise)
            continue
        evidence = evidence_by_id.get(promise_id)
        if action["action"] != "defer" and evidence is None:
            raise ReaderPromiseError(
                f"validated reader promise evidence is missing for {promise_id}"
            )
        copy = {**promise}
        if action["action"] == "defer":
            copy["deferrals"] = [
                *copy["deferrals"],
                {
                    "chapter_number": payload["chapter_number"],
                    "reason": action["defer_reason"],
                    "approved_by": "human",
                },
            ]
        else:
            if action["action"] == "setup":
                copy["status"] = "open"
            elif action["action"] == "escalate":
                copy["status"] = "escalated"
            elif action["action"] == "partial_payoff":
                copy["status"] = "partially_paid"
                copy["completed_stage_ids"] = list(
                    dict.fromkeys([*copy["completed_stage_ids"], action["stage_id"]])
                )
            elif action["action"] == "payoff":
                copy["status"] = "paid"
                copy["completed_stage_ids"] = list(
                    dict.fromkeys([*copy["completed_stage_ids"], action["stage_id"]])
                )
            evidence_record = dict(evidence or {})
            copy["actual_evidence"] = [
                *copy["actual_evidence"],
                {
                    **evidence_record,
                    "chapter_number": payload["chapter_number"],
                    "action": action["action"],
                    "stage_id": action["stage_id"],
                    "final_path": payload["final"]["path"],
                    "final_sha256": payload["final"]["sha256"],
                    "semantic_ledger_path": payload["semantic_ledger"]["path"],
                    "semantic_ledger_sha256": payload["semantic_ledger"]["sha256"],
                    "confirmed_by": "human",
                },
            ]
        updated.append(copy)
    next_ledger = {**ledger, "items": updated}
    with apply_transaction(
        root,
        command="chapter reader-promise-evidence-apply",
        chapter_number=int(payload["chapter_number"]),
        source_paths=[application_file, payload["final"]["path"], payload["semantic_ledger"]["path"]],
        touched_paths=[ledger_file],
        metadata={"actions": len(payload["actions"]), "human_confirmed": True},
    ) as transaction:
        _write_json(ledger_file, next_ledger)
    return PromiseEvidenceApplyResult(
        chapter_number=int(payload["chapter_number"]),
        actions=len(payload["actions"]),
        ledger_file=ledger_file.relative_to(root).as_posix(),
        transaction_report=transaction.report_file.relative_to(root).as_posix(),
    )


def validate_promise_evidence_application(root: Path, payload: Any) -> list[str]:
    errors: list[str] = []
    fields = {
        "schema",
        "chapter_number",
        "ledger",
        "final",
        "semantic_ledger",
        "actions",
        "evidence",
        "confirmed_by",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        return ["reader promise evidence application fields are invalid"]
    if payload.get("schema") != EVIDENCE_APPLICATION_SCHEMA:
        errors.append(f"schema must be {EVIDENCE_APPLICATION_SCHEMA}")
    chapter = payload.get("chapter_number")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
        errors.append("chapter_number must be positive")
        chapter = 0
    if payload.get("confirmed_by") != "human":
        errors.append("reader promise evidence must be human-confirmed")
    sources: dict[str, Path] = {}
    for label in ("ledger", "final", "semantic_ledger"):
        reference = payload.get(label)
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
            errors.append(f"{label} reference fields are invalid")
            continue
        try:
            path = _resolve_file(root, reference.get("path"))
            if reference.get("sha256") != _file_hash(path):
                errors.append(f"{label} SHA-256 is stale")
            sources[label] = path
        except (OSError, ValueError) as exc:
            errors.append(f"{label} cannot be read: {exc}")
    expected_sources = {
        "ledger": root / "30_state" / "reader_promise_ledger.json",
        "final": manuscript_chapter_path(root, chapter, lane="final") if chapter else None,
        "semantic_ledger": (
            root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json"
            if chapter
            else None
        ),
    }
    for label, expected in expected_sources.items():
        actual = sources.get(label)
        if expected is not None and actual is not None and actual.resolve() != expected.resolve():
            errors.append(f"{label} must reference the canonical chapter source")
    ledger = _read_json(sources["ledger"]) if "ledger" in sources else {}
    if ledger.get("schema") != LEDGER_SCHEMA:
        errors.append(f"ledger schema must be {LEDGER_SCHEMA}")
    errors.extend(validate_promise_actions_v2(payload.get("actions"), ledger))
    raw_actions = payload.get("actions")
    actions: list[Any] = raw_actions if isinstance(raw_actions, list) else []
    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        errors.append("evidence must be a list")
        evidence = []
    final_text = sources.get("final", Path()).read_text(encoding="utf-8") if "final" in sources else ""
    required_evidence = {
        item["promise_id"] for item in actions if isinstance(item, dict) and item.get("action") != "defer"
    }
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        prefix = f"evidence[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "promise_id",
            "start",
            "end",
            "excerpt",
            "semantic_reason",
        }:
            errors.append(f"{prefix} fields are invalid")
            continue
        promise_id = item.get("promise_id")
        if not isinstance(promise_id, str) or promise_id in evidence_ids:
            errors.append(f"{prefix}.promise_id is invalid or duplicated")
        evidence_ids.add(str(promise_id))
        start, end = item.get("start"), item.get("end")
        if (
            not isinstance(start, int)
            or isinstance(start, bool)
            or not isinstance(end, int)
            or isinstance(end, bool)
            or start < 0
            or end <= start
            or end > len(final_text)
            or final_text[start:end] != item.get("excerpt")
        ):
            errors.append(f"{prefix} must cite an exact final-text Unicode span")
        if not isinstance(item.get("semantic_reason"), str) or not item["semantic_reason"].strip():
            errors.append(f"{prefix}.semantic_reason must be non-empty")
    if evidence_ids != required_evidence:
        missing = sorted(required_evidence - evidence_ids)
        extra = sorted(evidence_ids - required_evidence)
        if missing:
            errors.append("promise actions are missing exact evidence: " + ", ".join(missing))
        if extra:
            errors.append("evidence exists without a non-defer action: " + ", ".join(extra))
    return errors


def _validate_window(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"earliest", "target", "latest"}:
        errors.append(f"{label} fields are invalid")
        return
    earliest, target, latest = value.get("earliest"), value.get("target"), value.get("latest")
    if not all(
        isinstance(item, int) and not isinstance(item, bool) and item > 0
        for item in (earliest, target, latest)
    ):
        errors.append(f"{label} must satisfy positive earliest <= target <= latest")
        return
    assert isinstance(earliest, int) and isinstance(target, int) and isinstance(latest, int)
    if not earliest <= target <= latest:
        errors.append(f"{label} must satisfy positive earliest <= target <= latest")


def _validate_range(value: Any, label: str, errors: list[str]) -> None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in value)
        or value[1] < value[0]
    ):
        errors.append(f"{label} must be a positive [start, end] range")


def _resolve_file(root: Path, raw: str | Path | None) -> Path:
    if not isinstance(raw, (str, Path)) or not str(raw):
        raise ValueError("path must be non-empty")
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escaped project root: {raw}") from exc
    if not path.is_file():
        raise ValueError(f"file does not exist: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
