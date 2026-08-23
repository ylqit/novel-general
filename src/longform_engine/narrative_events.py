"""Semantic-only realization of human-approved v0.10 narrative events."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from longform_engine.config import ConfigDocument
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


EVENT_REALIZATION_APPLICATION_SCHEMA = "event_realization_application_v1"
EVENT_STATES = frozenset(
    {
        "proposed",
        "planned_approved",
        "rejected",
        "waiting_preconditions",
        "ready",
        "human_activated",
        "realized",
        "partially_realized",
        "not_realized",
        "contradicted",
        "deferred",
        "cancelled",
        "superseded",
    }
)
REALIZATION_STATES = frozenset(
    {"realized", "partially_realized", "not_realized", "contradicted", "deferred", "cancelled"}
)


@dataclass(frozen=True)
class EventRealizationValidation:
    ok: bool
    redirect_required: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class EventRealizationApplyResult:
    chapter_number: int
    events: int
    event_ledger: str
    transaction_report: str


def build_event_realization_application(
    root: Path,
    *,
    chapter_number: int,
    observations: list[dict[str, Any]],
    discovered_causal_nodes: list[dict[str, Any]],
    confirmed_by: str,
) -> dict[str, Any]:
    """Bind semantic observations to exact final and semantic-ledger hashes."""

    if confirmed_by != "human":
        raise ValueError("event realization evidence must be confirmed by human")
    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive")
    final = manuscript_chapter_path(root, chapter_number, lane="final")
    semantic = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    if not final.is_file() or not semantic.is_file():
        raise ValueError("event realization requires current final prose and semantic ledger")
    event_ledger = root / "30_state" / "narrative_events" / f"ch{chapter_number:03d}.json"
    if not event_ledger.is_file():
        raise ValueError("event realization requires an approved narrative event ledger")
    return {
        "schema": EVENT_REALIZATION_APPLICATION_SCHEMA,
        "chapter_number": chapter_number,
        "event_ledger": {
            "path": event_ledger.relative_to(root).as_posix(),
            "sha256": _file_hash(event_ledger),
        },
        "final": {
            "path": final.relative_to(root).as_posix(),
            "sha256": _file_hash(final),
        },
        "semantic_ledger": {
            "path": semantic.relative_to(root).as_posix(),
            "sha256": _file_hash(semantic),
        },
        "observations": observations,
        "discovered_causal_nodes": discovered_causal_nodes,
        "confirmed_by": "human",
    }


def validate_event_realization_application(
    root: Path,
    payload: Any,
) -> EventRealizationValidation:
    errors: list[str] = []
    warnings: list[str] = []
    fields = {
        "schema",
        "chapter_number",
        "event_ledger",
        "final",
        "semantic_ledger",
        "observations",
        "discovered_causal_nodes",
        "confirmed_by",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        return EventRealizationValidation(
            False, False, ("event realization application fields are invalid",), ()
        )
    if payload.get("schema") != EVENT_REALIZATION_APPLICATION_SCHEMA:
        errors.append(f"schema must be {EVENT_REALIZATION_APPLICATION_SCHEMA}")
    chapter = payload.get("chapter_number")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
        errors.append("chapter_number must be positive")
        chapter = 0
    if payload.get("confirmed_by") != "human":
        errors.append("event realization must be human-confirmed")

    loaded: dict[str, tuple[Path, dict[str, Any]]] = {}
    for label in ("event_ledger", "semantic_ledger"):
        reference = payload.get(label)
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
            errors.append(f"{label} reference fields are invalid")
            continue
        try:
            path = _resolve_file(root, reference.get("path"))
            if reference.get("sha256") != _file_hash(path):
                errors.append(f"{label} SHA-256 is stale")
            loaded[label] = (path, _read_json(path))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{label} cannot be read: {exc}")
    final_ref = payload.get("final")
    final_text = ""
    if not isinstance(final_ref, dict) or set(final_ref) != {"path", "sha256"}:
        errors.append("final reference fields are invalid")
    else:
        try:
            final = _resolve_file(root, final_ref.get("path"))
            expected = manuscript_chapter_path(root, chapter, lane="final") if chapter else final
            if final != expected.resolve():
                errors.append("final path must be the canonical chapter file")
            if final_ref.get("sha256") != _file_hash(final):
                errors.append("final SHA-256 is stale")
            final_text = final.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"final cannot be read: {exc}")

    event_payload = loaded.get("event_ledger", (Path(), {}))[1]
    if event_payload.get("schema") != "narrative_event_ledger_v1":
        errors.append("event_ledger schema is invalid")
    if event_payload.get("chapter_number") != chapter:
        errors.append("event_ledger chapter_number does not match")
    planned = {
        str(item.get("event_id")): item
        for item in event_payload.get("events", [])
        if isinstance(item, dict) and item.get("event_id")
    }
    observations = payload.get("observations")
    if not isinstance(observations, list):
        errors.append("observations must be a list")
        observations = []
    observed: set[str] = set()
    for index, observation in enumerate(observations):
        prefix = f"observations[{index}]"
        if not isinstance(observation, dict) or set(observation) != {
            "event_id",
            "state",
            "evidence",
            "semantic_reason",
        }:
            errors.append(f"{prefix} fields are invalid")
            continue
        event_id = observation.get("event_id")
        if not isinstance(event_id, str) or event_id not in planned:
            errors.append(f"{prefix}.event_id is not an approved planned event")
        elif event_id in observed:
            errors.append(f"duplicate event observation: {event_id}")
        observed.add(str(event_id))
        state = observation.get("state")
        if state not in REALIZATION_STATES:
            errors.append(f"{prefix}.state is invalid")
        if not isinstance(observation.get("semantic_reason"), str) or not observation[
            "semantic_reason"
        ].strip():
            errors.append(f"{prefix}.semantic_reason must be non-empty")
        evidence = observation.get("evidence")
        if state in {"realized", "partially_realized", "contradicted"}:
            errors.extend(_validate_exact_span(evidence, final_text, prefix))
        elif evidence is not None:
            errors.append(f"{prefix}.evidence must be null for {state}")
    if observed != set(planned):
        missing = sorted(set(planned) - observed)
        extra = sorted(observed - set(planned))
        if missing:
            errors.append("every approved event requires an observation; missing: " + ", ".join(missing))
        if extra:
            errors.append("observations include unknown events: " + ", ".join(extra))

    discovered = payload.get("discovered_causal_nodes")
    if not isinstance(discovered, list) or any(not isinstance(item, dict) for item in discovered):
        errors.append("discovered_causal_nodes must be a list of objects")
        discovered = []
    redirect_required = bool(discovered)
    if redirect_required:
        warnings.append(
            "draft introduced unapproved causal nodes; redirect to plot-node approval before apply"
        )
    return EventRealizationValidation(
        ok=not errors and not redirect_required,
        redirect_required=redirect_required,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def apply_event_realization(
    config: ConfigDocument,
    *,
    application_path: str | Path,
) -> EventRealizationApplyResult:
    root = resolve_project_root(config)
    application_file = _resolve_file(root, application_path)
    payload = _read_json(application_file)
    validation = validate_event_realization_application(root, payload)
    if not validation.ok:
        reasons = [*validation.errors, *validation.warnings]
        raise ValueError("event realization cannot apply: " + "; ".join(reasons))
    chapter = int(payload["chapter_number"])
    event_file = _resolve_file(root, payload["event_ledger"]["path"])
    ledger = _read_json(event_file)
    observations = {item["event_id"]: item for item in payload["observations"]}
    events = []
    for event in ledger["events"]:
        observation = observations[event["event_id"]]
        events.append(
            {
                **event,
                "state": observation["state"],
                "realization_evidence": (
                    {
                        **observation["evidence"],
                        "final_path": payload["final"]["path"],
                        "final_sha256": payload["final"]["sha256"],
                        "semantic_ledger_path": payload["semantic_ledger"]["path"],
                        "semantic_ledger_sha256": payload["semantic_ledger"]["sha256"],
                        "semantic_reason": observation["semantic_reason"],
                        "confirmed_by": "human",
                    }
                    if observation["evidence"] is not None
                    else {
                        "final_path": payload["final"]["path"],
                        "final_sha256": payload["final"]["sha256"],
                        "semantic_ledger_path": payload["semantic_ledger"]["path"],
                        "semantic_ledger_sha256": payload["semantic_ledger"]["sha256"],
                        "semantic_reason": observation["semantic_reason"],
                        "confirmed_by": "human",
                    }
                ),
            }
        )
    updated = {**ledger, "events": events, "realization_application_sha256": _file_hash(application_file)}
    with apply_transaction(
        root,
        command="chapter event-realization-apply",
        chapter_number=chapter,
        source_paths=[application_file, payload["final"]["path"], payload["semantic_ledger"]["path"]],
        touched_paths=[event_file],
        metadata={"event_count": len(events), "human_confirmed": True},
    ) as transaction:
        _write_json(event_file, updated)
    return EventRealizationApplyResult(
        chapter_number=chapter,
        events=len(events),
        event_ledger=event_file.relative_to(root).as_posix(),
        transaction_report=transaction.report_file.relative_to(root).as_posix(),
    )


def _validate_exact_span(value: Any, source: str, prefix: str) -> list[str]:
    if not isinstance(value, dict) or set(value) != {"start", "end", "excerpt"}:
        return [f"{prefix}.evidence must contain exactly start, end, excerpt"]
    start, end, excerpt = value.get("start"), value.get("end"), value.get("excerpt")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(source)
    ):
        return [f"{prefix}.evidence has invalid Unicode codepoint offsets"]
    if not isinstance(excerpt, str) or source[start:end] != excerpt:
        return [f"{prefix}.evidence excerpt does not match final text"]
    return []


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
