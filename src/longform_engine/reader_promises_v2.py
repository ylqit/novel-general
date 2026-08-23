"""Explicit, evidence-bound reader promises for the v0.10 protocol."""

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
PROMISE_ACTIONS = frozenset({"setup", "escalate", "partial_payoff", "payoff", "defer"})
TERMINAL_STATES = frozenset({"paid", "retired", "breached"})
STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$")


@dataclass(frozen=True)
class PromiseEvidenceApplyResult:
    chapter_number: int
    actions: int
    ledger_file: str
    transaction_report: str


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
) -> dict[str, Any]:
    """Create the ledger from only the promises explicitly present in the approved plan."""

    if approved_by != "human":
        raise ValueError("reader promises must be explicitly selected by human")
    errors = validate_reader_promise_candidates(candidates)
    if errors:
        raise ValueError("reader promises are invalid: " + "; ".join(errors))
    return {
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


def validate_promise_actions_v2(
    actions: Any,
    ledger: dict[str, Any],
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
        elif promise.get("status") in TERMINAL_STATES:
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
            copy["actual_evidence"] = [
                *copy["actual_evidence"],
                {
                    **evidence,
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
    actions = payload.get("actions") if isinstance(payload.get("actions"), list) else []
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
    if (
        any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in (earliest, target, latest))
        or not earliest <= target <= latest
    ):
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
