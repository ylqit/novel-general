"""Non-canonical human reader feedback batches for v0.10."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


BATCH_SCHEMA = "reader_feedback_batch_v1"
DECISION_SCHEMA = "human_reader_feedback_decision_v1"
PLANNING_PROPOSAL_SCHEMA = "planning_change_proposal_v1"
STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$")


@dataclass(frozen=True)
class ReaderFeedbackResult:
    batch_id: str
    artifact_file: str
    status: str


def record_reader_feedback_batch(
    config: ConfigDocument,
    *,
    payload: dict[str, Any],
) -> ReaderFeedbackResult:
    errors = validate_reader_feedback_batch(payload)
    if errors:
        raise ValueError("reader feedback batch is invalid: " + "; ".join(errors))
    root = resolve_project_root(config)
    path = root / "50_workbench" / "reader_feedback" / f"{payload['batch_id']}.json"
    if path.exists():
        existing = _read_json(path)
        if _json_hash(existing) != _json_hash(payload):
            raise ValueError("reader feedback batch IDs are immutable")
    else:
        _write_json(path, payload)
    return ReaderFeedbackResult(
        batch_id=str(payload["batch_id"]),
        artifact_file=path.relative_to(root).as_posix(),
        status="recorded_non_canonical",
    )


def record_reader_feedback_decision(
    config: ConfigDocument,
    *,
    batch_path: str | Path,
    decision: dict[str, Any],
) -> ReaderFeedbackResult:
    root = resolve_project_root(config)
    batch_file = _resolve_feedback_file(root, batch_path)
    batch = _read_json(batch_file)
    errors = validate_reader_feedback_decision(batch, decision)
    if errors:
        raise ValueError("reader feedback decision is invalid: " + "; ".join(errors))
    path = batch_file.with_name(batch_file.stem + ".decision.json")
    if path.exists() and _json_hash(_read_json(path)) != _json_hash(decision):
        raise ValueError("reader feedback decisions are immutable; create a new batch")
    _write_json(path, decision)
    return ReaderFeedbackResult(
        batch_id=str(batch["batch_id"]),
        artifact_file=path.relative_to(root).as_posix(),
        status="human_decision_recorded",
    )


def convert_reader_feedback_to_proposal(
    config: ConfigDocument,
    *,
    batch_path: str | Path,
    decision_path: str | Path,
    target: str,
) -> ReaderFeedbackResult:
    if target not in {"planning", "canon"}:
        raise ValueError("feedback target must be planning or canon")
    root = resolve_project_root(config)
    batch_file = _resolve_feedback_file(root, batch_path)
    decision_file = _resolve_feedback_file(root, decision_path)
    batch = _read_json(batch_file)
    decision = _read_json(decision_file)
    errors = validate_reader_feedback_decision(batch, decision)
    if errors:
        raise ValueError("reader feedback decision is invalid: " + "; ".join(errors))
    accepted = {
        item["hypothesis_id"]: item
        for item in decision["decisions"]
        if item["decision"] == "accept" and item["target"] == target
    }
    hypotheses = [
        item for item in batch["hypotheses"] if item["hypothesis_id"] in accepted
    ]
    if not hypotheses:
        raise ValueError(f"no accepted reader-feedback hypotheses target {target}")
    proposal_id = f"feedback.{batch['batch_id']}.{target}"
    if target == "planning":
        proposal = {
            "schema": PLANNING_PROPOSAL_SCHEMA,
            "proposal_id": proposal_id,
            "source_batch_sha256": _json_hash(batch),
            "source_decision_sha256": _json_hash(decision),
            "hypotheses": hypotheses,
            "status": "proposed",
            "created_by": "human",
            "boundary": "requires normal planning semantic review and per-node approval",
        }
    else:
        proposal = {
            "schema": "canon_change_feedback_seed_v1",
            "proposal_id": proposal_id,
            "source_batch_sha256": _json_hash(batch),
            "source_decision_sha256": _json_hash(decision),
            "hypotheses": hypotheses,
            "status": "proposal_seed_only",
            "created_by": "human",
            "boundary": "must be completed as canon_change_proposal_v1 and pass impact/review/decision",
        }
    output = root / "50_workbench" / "reader_feedback" / f"{proposal_id}.proposal.json"
    _write_json(output, proposal)
    return ReaderFeedbackResult(
        batch_id=str(batch["batch_id"]),
        artifact_file=output.relative_to(root).as_posix(),
        status=f"{target}_proposal_created",
    )


def validate_reader_feedback_batch(value: Any) -> list[str]:
    fields = {"schema", "batch_id", "scope", "observations", "hypotheses", "recorded_by"}
    if not isinstance(value, dict) or set(value) != fields:
        return ["reader_feedback_batch_v1 fields are invalid"]
    errors: list[str] = []
    if value.get("schema") != BATCH_SCHEMA:
        errors.append(f"schema must be {BATCH_SCHEMA}")
    _stable_id(value.get("batch_id"), "batch_id", errors)
    if value.get("recorded_by") != "human":
        errors.append("feedback batch must be human-recorded")
    scope = value.get("scope")
    if not isinstance(scope, dict) or set(scope) != {"from_chapter", "to_chapter"}:
        errors.append("scope fields are invalid")
    elif any(
        not isinstance(scope.get(field), int) or isinstance(scope.get(field), bool) or scope[field] <= 0
        for field in ("from_chapter", "to_chapter")
    ) or scope["to_chapter"] < scope["from_chapter"]:
        errors.append("scope must be a positive chapter range")
    observations = value.get("observations")
    if not isinstance(observations, list) or not observations or any(
        not isinstance(item, str) or not item.strip() for item in observations
    ):
        errors.append("observations must be a non-empty human text list")
    hypotheses = value.get("hypotheses")
    if not isinstance(hypotheses, list) or not hypotheses:
        errors.append("hypotheses must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, item in enumerate(hypotheses):
        prefix = f"hypotheses[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "hypothesis_id", "statement", "evidence_observation_indexes", "possible_targets"
        }:
            errors.append(f"{prefix} fields are invalid")
            continue
        _stable_id(item.get("hypothesis_id"), f"{prefix}.hypothesis_id", errors)
        if item.get("hypothesis_id") in seen:
            errors.append(f"duplicate hypothesis_id: {item.get('hypothesis_id')}")
        seen.add(str(item.get("hypothesis_id")))
        if not isinstance(item.get("statement"), str) or not item["statement"].strip():
            errors.append(f"{prefix}.statement must be non-empty")
        indexes = item.get("evidence_observation_indexes")
        if not isinstance(indexes, list) or not indexes or any(
            not isinstance(index, int) or isinstance(index, bool) or index < 0
            or not isinstance(observations, list) or index >= len(observations)
            for index in indexes
        ):
            errors.append(f"{prefix}.evidence_observation_indexes are invalid")
        targets = item.get("possible_targets")
        if not isinstance(targets, list) or not targets or not set(targets) <= {"planning", "canon"}:
            errors.append(f"{prefix}.possible_targets are invalid")
    return errors


def validate_reader_feedback_decision(batch: dict[str, Any], value: Any) -> list[str]:
    fields = {"schema", "batch_sha256", "decisions", "decided_by", "reason"}
    if not isinstance(value, dict) or set(value) != fields:
        return ["human_reader_feedback_decision_v1 fields are invalid"]
    errors: list[str] = []
    if value.get("schema") != DECISION_SCHEMA:
        errors.append(f"schema must be {DECISION_SCHEMA}")
    if value.get("batch_sha256") != _json_hash(batch):
        errors.append("batch_sha256 is stale")
    if value.get("decided_by") != "human":
        errors.append("feedback decision must be human-owned")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        errors.append("reason must be non-empty")
    expected = {
        str(item.get("hypothesis_id")): set(item.get("possible_targets") or [])
        for item in batch.get("hypotheses", [])
        if isinstance(item, dict)
    }
    decisions = value.get("decisions")
    if not isinstance(decisions, list):
        return [*errors, "decisions must be a list"]
    actual: set[str] = set()
    for index, item in enumerate(decisions):
        prefix = f"decisions[{index}]"
        if not isinstance(item, dict) or set(item) != {"hypothesis_id", "decision", "target", "reason"}:
            errors.append(f"{prefix} fields are invalid")
            continue
        hypothesis_id = str(item.get("hypothesis_id") or "")
        if hypothesis_id in actual:
            errors.append(f"duplicate hypothesis decision: {hypothesis_id}")
        actual.add(hypothesis_id)
        if item.get("decision") not in {"accept", "reject", "defer"}:
            errors.append(f"{prefix}.decision is invalid")
        target = item.get("target")
        if item.get("decision") == "accept" and target not in expected.get(hypothesis_id, set()):
            errors.append(f"{prefix}.target is not allowed by the hypothesis")
        if item.get("decision") != "accept" and target not in {None, ""}:
            errors.append(f"{prefix}.target is only allowed for accept")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            errors.append(f"{prefix}.reason must be non-empty")
    if actual != set(expected):
        errors.append("every feedback hypothesis requires one explicit human decision")
    return errors


def _resolve_feedback_file(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    directory = (root / "50_workbench" / "reader_feedback").resolve()
    try:
        resolved.relative_to(directory)
    except ValueError as exc:
        raise ValueError("reader feedback artifacts must remain under 50_workbench/reader_feedback") from exc
    if not resolved.is_file():
        raise ValueError(f"reader feedback artifact does not exist: {resolved}")
    return resolved


def _stable_id(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not STABLE_ID.fullmatch(value):
        errors.append(f"{label} must be a stable ID")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "BATCH_SCHEMA", "DECISION_SCHEMA", "PLANNING_PROPOSAL_SCHEMA", "ReaderFeedbackResult",
    "convert_reader_feedback_to_proposal", "record_reader_feedback_batch",
    "record_reader_feedback_decision", "validate_reader_feedback_batch",
    "validate_reader_feedback_decision",
]
