"""Semantic-only realization of human-approved v0.10 narrative events."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from longform_engine.config import ConfigDocument
from longform_engine.fanfiction_context import (
    FanfictionContextError,
    require_current_fanfiction_context_bundle,
)
from longform_engine.fanfiction_divergence import (
    derive_major_divergence_trigger_id,
    realized_major_divergence_errors,
)
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
    config: ConfigDocument,
    *,
    chapter_number: int,
    observations: list[dict[str, Any]],
    discovered_causal_nodes: list[dict[str, Any]],
    confirmed_by: str,
    realized_major_divergences: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bind semantic observations to exact final and semantic-ledger hashes."""

    if confirmed_by != "human":
        raise ValueError("event realization evidence must be confirmed by human")
    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive")
    root = resolve_project_root(config)
    final = manuscript_chapter_path(root, chapter_number, lane="final")
    semantic = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    if not final.is_file() or not semantic.is_file():
        raise ValueError("event realization requires current final prose and semantic ledger")
    event_ledger = root / "30_state" / "narrative_events" / f"ch{chapter_number:03d}.json"
    if not event_ledger.is_file():
        raise ValueError("event realization requires an approved narrative event ledger")
    fanfiction_context: dict[str, str] | None = None
    if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction":
        try:
            context_path, _context = require_current_fanfiction_context_bundle(
                config,
                chapter_number=chapter_number,
            )
        except FanfictionContextError as exc:
            raise ValueError(str(exc)) from exc
        fanfiction_context = {
            "path": context_path.relative_to(root).as_posix(),
            "sha256": _file_hash(context_path),
        }
    normalized_divergences = [
        _normalize_major_divergence_declaration(item, chapter_number=chapter_number)
        for item in realized_major_divergences or []
    ]
    if normalized_divergences and fanfiction_context is None:
        raise ValueError("realized major divergences require a fanfiction project context")
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
        "fanfiction_context": fanfiction_context,
        "observations": observations,
        "discovered_causal_nodes": discovered_causal_nodes,
        "realized_major_divergences": normalized_divergences,
        "confirmed_by": "human",
    }


def validate_event_realization_application(
    config: ConfigDocument,
    payload: Any,
) -> EventRealizationValidation:
    root = resolve_project_root(config)
    errors: list[str] = []
    warnings: list[str] = []
    fields = {
        "schema",
        "chapter_number",
        "event_ledger",
        "final",
        "semantic_ledger",
        "fanfiction_context",
        "observations",
        "discovered_causal_nodes",
        "realized_major_divergences",
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
    expected_owned_paths = {
        "event_ledger": root / "30_state" / "narrative_events" / f"ch{chapter:03d}.json",
        "semantic_ledger": root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json",
    }
    for label in ("event_ledger", "semantic_ledger"):
        reference = payload.get(label)
        if not isinstance(reference, dict) or set(reference) != {"path", "sha256"}:
            errors.append(f"{label} reference fields are invalid")
            continue
        try:
            path = _resolve_file(root, reference.get("path"))
            if path != expected_owned_paths[label].resolve():
                errors.append(f"{label} path must be the canonical ch{chapter:03d} ledger")
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

    review_claims: dict[str, dict[str, Any]] = {}
    context_reference = payload.get("fanfiction_context")
    if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction":
        if not isinstance(context_reference, dict) or set(context_reference) != {
            "path",
            "sha256",
        }:
            errors.append("fanfiction_context reference fields are invalid")
        elif chapter:
            try:
                context_path, context_payload = require_current_fanfiction_context_bundle(
                    config,
                    chapter_number=chapter,
                )
                if context_reference.get("path") != context_path.relative_to(root).as_posix():
                    errors.append("fanfiction_context path is not current")
                if context_reference.get("sha256") != _file_hash(context_path):
                    errors.append("fanfiction_context SHA-256 is stale")
                review_claims = {
                    str(item.get("claim_id") or ""): item
                    for item in context_payload.get("review_projection", {}).get("claims") or []
                    if isinstance(item, dict) and item.get("claim_id")
                }
            except (FanfictionContextError, OSError, ValueError) as exc:
                errors.append(f"fanfiction_context is not current: {exc}")
    elif context_reference is not None:
        errors.append("original projects must not bind a fanfiction_context")

    event_payload = loaded.get("event_ledger", (Path(), {}))[1]
    if event_payload.get("schema") != "narrative_event_ledger_v1":
        errors.append("event_ledger schema is invalid")
    if event_payload.get("chapter_number") != chapter:
        errors.append("event_ledger chapter_number does not match")
    plot_table = root / "20_outline" / "plot_nodes" / f"ch{chapter:03d}.json"
    plot_payload = _read_json(plot_table) if plot_table.is_file() else {}
    if not isinstance(plot_payload, dict) or plot_payload.get("schema") != "plot_node_table_v1":
        errors.append("event_ledger planning source is missing or invalid")
    else:
        if event_payload.get("source_plot_node_table_sha256") != _file_hash(plot_table):
            errors.append("event_ledger planning source hash is stale")
    semantic_payload = loaded.get("semantic_ledger", (Path(), {}))[1]
    semantic_source_value = semantic_payload.get("source")
    semantic_source: dict[str, Any] = (
        dict(semantic_source_value) if isinstance(semantic_source_value, dict) else {}
    )
    expected_final_path = manuscript_chapter_path(root, chapter, lane="final") if chapter else Path()
    if semantic_payload.get("schema") != "chapter_semantic_bundle_v1":
        errors.append("semantic_ledger schema is invalid")
    if semantic_payload.get("chapter_number") != chapter:
        errors.append("semantic_ledger chapter_number does not match")
    if semantic_payload.get("canonical") is not True:
        errors.append("semantic_ledger is not canonically approved")
    if chapter and (
        semantic_source.get("path") != expected_final_path.relative_to(root).as_posix()
        or semantic_source.get("sha256") != _file_hash(expected_final_path)
    ):
        errors.append("semantic_ledger final binding is stale")
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

    divergences = payload.get("realized_major_divergences")
    existing_logical_identities = {
        (
            str(item.get("source_event_id") or ""),
            str(item.get("source_claim_id") or ""),
            int(item.get("realized_chapter") or 0),
            str(item.get("declaration_id") or ""),
        )
        for item in event_payload.get("realized_major_divergences") or []
        if isinstance(item, dict)
    }
    projected_states = {
        str(item.get("event_id") or ""): str(item.get("state") or "")
        for item in observations
        if isinstance(item, dict)
    }
    if chapter and expected_final_path.is_file():
        context_path = root / "50_workbench" / "fanfiction_context" / f"ch{chapter:03d}.json"
        errors.extend(
            realized_major_divergence_errors(
                root=root,
                chapter_number=chapter,
                divergences=divergences,
                event_payload=event_payload,
                review_claims=review_claims,
                final_path=expected_final_path,
                semantic_path=expected_owned_paths["semantic_ledger"],
                context_path=context_path,
                projected_states=projected_states,
                require_stored_bindings=False,
                existing_logical_identities=existing_logical_identities,
            )
        )

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
    validation = validate_event_realization_application(config, payload)
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
    application_sha256 = _file_hash(application_file)
    realized_major_divergences = [
        {
            **item,
            "final_path": payload["final"]["path"],
            "final_sha256": payload["final"]["sha256"],
            "semantic_ledger_path": payload["semantic_ledger"]["path"],
            "semantic_ledger_sha256": payload["semantic_ledger"]["sha256"],
            "fanfiction_context_path": payload["fanfiction_context"]["path"],
            "fanfiction_context_sha256": payload["fanfiction_context"]["sha256"],
            "realization_application_sha256": application_sha256,
        }
        for item in payload["realized_major_divergences"]
    ]
    updated = {
        **ledger,
        "events": events,
        "realized_major_divergences": realized_major_divergences,
        "realization_application_sha256": application_sha256,
    }
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


def _normalize_major_divergence_declaration(
    value: Any,
    *,
    chapter_number: int,
) -> dict[str, Any]:
    fields = {
        "declaration_id",
        "source_event_id",
        "source_claim_id",
        "realized_chapter",
        "impact_level",
        "knowledge_scope_refs",
        "human_confirmation",
        "evidence",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(
            "realized major divergence declaration fields are invalid; trigger_id is engine-owned"
        )
    declaration_id = value.get("declaration_id")
    source_event_id = value.get("source_event_id")
    source_claim_id = value.get("source_claim_id")
    realized_chapter = value.get("realized_chapter")
    if not all(
        isinstance(item, str) and item.strip()
        for item in (declaration_id, source_event_id, source_claim_id)
    ):
        raise ValueError("realized major divergence identity fields must be non-empty")
    if realized_chapter != chapter_number:
        raise ValueError("realized major divergence chapter must match application chapter")
    return {
        "trigger_id": derive_major_divergence_trigger_id(
            str(source_event_id),
            str(source_claim_id),
            int(realized_chapter),
            str(declaration_id),
        ),
        **value,
    }


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
