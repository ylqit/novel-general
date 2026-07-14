"""Story graph validation, deterministic update, and conflict reporting."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.agent_tasks import build_manifest, mark_tasks_for_output, write_manifest
from longform_engine.config import ConfigDocument
from longform_engine.db import query_table, sync_database
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root


CANONICAL_ENTITY_TYPES = (
    "character",
    "location",
    "organization",
    "ability",
    "item",
    "secret",
    "foreshadowing",
    "event",
)

ENTITY_TYPE_ALIASES = {
    "faction": "organization",
    "org": "organization",
    "power": "ability",
    "artifact": "item",
}

BIBLE_ENTITY_SOURCES = (
    ("10_bible/characters.json", "character"),
    ("10_bible/locations.json", "location"),
    ("10_bible/factions.json", "organization"),
    ("10_bible/abilities.json", "ability"),
    ("10_bible/items.json", "item"),
    ("10_bible/secrets.json", "secret"),
    ("10_bible/foreshadowing.json", "foreshadowing"),
)


@dataclass(frozen=True)
class GraphValidationResult:
    """Validation outcome for story_graph.json."""

    graph_file: str
    entity_types: tuple[str, ...]
    entities: int
    relationships: int
    events: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class GraphUpdateResult:
    """Result for deterministic chapter-to-graph update."""

    chapter_number: int
    graph_file: str
    matched_entities: int
    mentions_added: int
    events_added: int
    db_entities: int
    db_events: int
    update_file: str = ""
    suggestions: int = 0
    review_required: int = 0


@dataclass(frozen=True)
class GraphExtractResult:
    """Reviewable graph update suggestions extracted from chapter text."""

    chapter_number: int
    source: str
    update_file: str
    suggestions: int
    low_confidence: int


@dataclass(frozen=True)
class GraphApplyResult:
    """Result for applying a reviewed graph update suggestion file."""

    chapter_number: int
    update_file: str
    graph_file: str
    applied: int
    skipped_low_confidence: int
    db_entities: int
    db_events: int


@dataclass(frozen=True)
class GraphCascadeResult:
    """Result for marking future graph facts stale after outline changes."""

    from_chapter: int
    graph_file: str
    report_file: str
    marked_entities: int
    marked_events: int


@dataclass(frozen=True)
class GraphCheckResult:
    """Conflict report result."""

    report_file: str
    issues: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class GraphSemanticTaskResult:
    """Codex semantic graph extraction task."""

    chapter_number: int
    task_file: str
    manifest_file: str
    output_file: str
    source_file: str
    next_command: str


@dataclass(frozen=True)
class GraphSemanticValidateResult:
    """Validation result for semantic graph update payloads."""

    chapter_number: int
    ok: bool
    file: str
    report_file: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    next_command: str


def validate_graph(config: ConfigDocument) -> GraphValidationResult:
    """Validate the story graph shape and canonical entity contract."""

    root = resolve_project_root(config)
    graph_path = graph_file(root)
    graph = load_graph(root)
    errors: list[str] = []
    warnings: list[str] = []

    for key in ("entities", "relationships", "events"):
        if not isinstance(graph.get(key), list):
            errors.append(f"`{key}` must be a list.")

    entities = graph.get("entities") if isinstance(graph.get("entities"), list) else []
    relationships = graph.get("relationships") if isinstance(graph.get("relationships"), list) else []
    events = graph.get("events") if isinstance(graph.get("events"), list) else []

    seen_ids: set[str] = set()
    entity_ids: set[str] = set()
    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            errors.append(f"entities[{index}] must be an object.")
            continue
        entity_id = str(entity.get("id") or "").strip()
        name = str(entity.get("name") or "").strip()
        entity_type = normalize_entity_type(entity.get("type"))
        if not entity_id:
            errors.append(f"entities[{index}] missing id.")
        elif entity_id in seen_ids:
            errors.append(f"duplicate entity id: {entity_id}")
        else:
            seen_ids.add(entity_id)
            entity_ids.add(entity_id)
        if not name:
            errors.append(f"entities[{index}] missing name.")
        if entity_type not in CANONICAL_ENTITY_TYPES:
            errors.append(f"entities[{index}] has unsupported type: {entity.get('type')}")
        if "mentions" in entity and not isinstance(entity["mentions"], list):
            errors.append(f"entities[{index}].mentions must be a list.")
        if entity_type == "foreshadowing":
            status = str(entity.get("status") or "active")
            if status not in {"planted", "active", "paid_off", "expired", "open", "closed"}:
                warnings.append(f"entities[{index}] foreshadowing status should be planted/active/paid_off/expired.")
        if entity_type == "ability" and not any(entity.get(field) for field in ("cost", "limit", "cooldown")):
            warnings.append(f"entities[{index}] ability is missing cost/limit/cooldown boundary metadata.")

    for index, relationship in enumerate(relationships):
        if not isinstance(relationship, dict):
            errors.append(f"relationships[{index}] must be an object.")
            continue
        source = relationship.get("source") or relationship.get("from")
        target = relationship.get("target") or relationship.get("to")
        relation_type = relationship.get("type") or relationship.get("relation")
        if not source or not target or not relation_type:
            errors.append(f"relationships[{index}] requires source, target, and type.")
            continue
        if source not in entity_ids:
            warnings.append(f"relationships[{index}] source not found in entities: {source}")
        if target not in entity_ids:
            warnings.append(f"relationships[{index}] target not found in entities: {target}")
        validate_temporal_edge(relationship, f"relationships[{index}]", errors, warnings)

    seen_event_ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"events[{index}] must be an object.")
            continue
        event_id = str(event.get("id") or "").strip()
        title = str(event.get("title") or event.get("name") or "").strip()
        if not event_id:
            errors.append(f"events[{index}] missing id.")
        elif event_id in seen_event_ids:
            errors.append(f"duplicate event id: {event_id}")
        else:
            seen_event_ids.add(event_id)
        if not title:
            errors.append(f"events[{index}] missing title.")
        for participant in normalize_list(event.get("participants")):
            if participant and participant not in entity_ids:
                warnings.append(f"events[{index}] participant not found in entities: {participant}")
        validate_temporal_edge(event, f"events[{index}]", errors, warnings)

    return GraphValidationResult(
        graph_file=str(graph_path),
        entity_types=CANONICAL_ENTITY_TYPES,
        entities=len(entities),
        relationships=len(relationships),
        events=len(events),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def semantic_graph_task(config: ConfigDocument, *, chapter_number: int) -> GraphSemanticTaskResult:
    """Write a Codex task for strong semantic graph extraction."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    source = find_chapter_file(root, chapter_number)
    if source is None:
        raise ValueError("semantic graph extraction requires a finalized chapter.")
    task_dir = root / "50_workbench" / "graph_updates"
    task_dir.mkdir(parents=True, exist_ok=True)
    output_file = task_dir / f"ch{chapter_number:03d}.semantic.json"
    task_file = task_dir / f"ch{chapter_number:03d}.semantic_task.md"
    manifest_file = task_dir / f"ch{chapter_number:03d}.semantic_graph.agent_task.json"
    template = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "source": "final",
        "source_path": relative_path(root, source),
        "updates": [
            {
                "type": "relationship_change",
                "source": "",
                "target": "",
                "relation": "",
                "status": "active",
                "from_chapter": chapter_number,
                "confidence": 0.8,
                "evidence_span": "",
            }
        ],
    }
    lines = [
        f"# Semantic Graph Extraction Task ch{chapter_number:03d}",
        "",
        f"- Source final chapter: `{relative_path(root, source)}`",
        f"- Output JSON: `{relative_path(root, output_file)}`",
        "",
        "Extract graph facts only. Do not edit `30_state/story_graph.json`, `60_rag/`, `40_manuscript/final/`, or SQLite.",
        "",
        "Required axes: character_status_changes, events, relationship_changes, foreshadow_planted, foreshadow_paid_off, conflict_escalation, location_transitions, ability_boundary_changes.",
        "",
        "```json",
        json.dumps(template, ensure_ascii=False, indent=2),
        "```",
        "",
        "## Source Excerpt",
        "",
        trim_text(safe_read_text(source), 5000),
        "",
    ]
    atomic_write_text(task_file, "\n".join(lines))
    manifest = build_manifest(
        root,
        task_type="graph_extract",
        chapter_number=chapter_number,
        input_files=[task_file, source, root / "30_state" / "story_graph.json"],
        allowed_output_paths=[output_file],
        output_schema="semantic_graph_update_v1",
        validate_command=f"longform-engine graph semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
        apply_command=f"longform-engine graph semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
        failure_next_command=f"longform-engine graph semantic-task project.yaml --chapter {chapter_number}",
    )
    write_manifest(root, manifest, manifest_file)
    return GraphSemanticTaskResult(
        chapter_number=chapter_number,
        task_file=str(task_file),
        manifest_file=str(manifest_file),
        output_file=str(output_file),
        source_file=str(source),
        next_command=f"longform-engine graph semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
    )


def semantic_graph_validate(config: ConfigDocument, *, chapter_number: int, file_path: str | Path) -> GraphSemanticValidateResult:
    """Validate semantic graph updates before canonical apply."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    path = resolve_under_root(root, file_path)
    errors: list[str] = []
    warnings: list[str] = []
    try:
        path.resolve().relative_to((root / "50_workbench").resolve())
    except ValueError:
        errors.append("semantic graph file must live under 50_workbench/.")
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        payload = {}
        errors.append("semantic graph file must be a JSON object.")
    if int(payload.get("chapter_number") or 0) != chapter_number:
        errors.append("payload chapter_number does not match command chapter.")
    if payload.get("source") != "final":
        errors.append("semantic graph source must be final.")
    source_path = str(payload.get("source_path") or "")
    source_file = root / source_path
    if not source_path.startswith("40_manuscript/final/") or not source_file.exists():
        errors.append("source_path must point to an existing final manuscript.")
    source_text = safe_read_text(source_file) if source_file.exists() else ""
    updates = payload.get("updates")
    if not isinstance(updates, list):
        errors.append("payload updates must be a list.")
        updates = []
    for index, update in enumerate(updates):
        if not isinstance(update, dict):
            errors.append(f"updates[{index}] must be an object.")
            continue
        for field in ("evidence_span", "confidence", "from_chapter"):
            if field not in update:
                errors.append(f"updates[{index}] missing {field}.")
        evidence = str(update.get("evidence_span") or "").strip()
        if not evidence:
            errors.append(f"updates[{index}] evidence_span is empty.")
        elif evidence not in source_text:
            warnings.append(f"updates[{index}] evidence_span was not found verbatim in source.")
        confidence = confidence_value(update.get("confidence"))
        if confidence is None:
            errors.append(f"updates[{index}] confidence must be numeric or low/medium/high.")
        if as_optional_int(update.get("from_chapter")) != chapter_number:
            errors.append(f"updates[{index}] from_chapter must equal command chapter.")
    report_file = root / "50_workbench" / "graph_updates" / f"ch{chapter_number:03d}.semantic_validate.json"
    ok = not errors
    atomic_write_text(
        report_file,
        json.dumps(
            {
                "chapter_number": chapter_number,
                "file": relative_path(root, path),
                "ok": ok,
                "errors": errors,
                "warnings": warnings,
                "next_command": (
                    f"longform-engine graph semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, path)}"
                    if ok
                    else f"longform-engine graph semantic-task project.yaml --chapter {chapter_number}"
                ),
                "updated_at": utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=path,
        to_status="validated" if ok else "invalid",
        command="graph semantic-validate",
        result=report_file,
        from_statuses=("awaiting_agent", "submitted"),
    )
    return GraphSemanticValidateResult(
        chapter_number=chapter_number,
        ok=ok,
        file=str(path),
        report_file=str(report_file),
        errors=tuple(errors),
        warnings=tuple(warnings),
        next_command=(
            f"longform-engine graph semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, path)}"
            if ok
            else f"longform-engine graph semantic-task project.yaml --chapter {chapter_number}"
        ),
    )


def semantic_graph_apply(config: ConfigDocument, *, chapter_number: int, file_path: str | Path) -> GraphApplyResult:
    """Apply validated semantic graph updates into canonical temporal graph."""

    validation = semantic_graph_validate(config, chapter_number=chapter_number, file_path=file_path)
    if not validation.ok:
        raise ValueError("semantic graph updates did not validate; canonical graph was not mutated.")
    root = resolve_project_root(config)
    payload = read_json(Path(validation.file), default={})
    graph = load_graph(root)
    ensure_graph_shape(graph)
    upsert_canon_entities(graph, root)
    entity_index = {str(entity.get("id")): entity for entity in graph["entities"] if isinstance(entity, dict) and entity.get("id")}
    applied = 0
    skipped_low = 0
    for update in payload.get("updates", []):
        if not isinstance(update, dict):
            continue
        confidence = confidence_value(update.get("confidence")) or 0.0
        if confidence < 0.55:
            skipped_low += 1
            continue
        update_type = str(update.get("type") or update.get("kind") or "")
        if update_type == "relationship_change":
            applied += apply_semantic_relationship(graph, update, chapter_number)
        elif update_type == "character_status_change":
            applied += apply_semantic_status(entity_index, update, chapter_number)
        elif update_type == "event":
            applied += apply_semantic_event(graph, update, chapter_number, str(payload.get("source_path") or ""))
        elif update_type in {"foreshadow_planted", "foreshadow_paid_off"}:
            applied += apply_semantic_foreshadow(graph, update, chapter_number, update_type)
        elif update_type == "ability_boundary_change":
            applied += apply_semantic_ability(graph, update, chapter_number)
        else:
            skipped_low += 1
    with apply_transaction(
        root,
        command="graph semantic-apply",
        chapter_number=chapter_number,
        source_paths=[validation.file],
        touched_paths=[graph_file(root), root / "70_runtime" / "db"],
        metadata={
            "rebuild_boundaries": ["SQLite sync"],
        },
    ) as transaction:
        graph["updated_at"] = utc_now()
        save_graph(root, graph)
        sync_database(config)
        entities = len(query_table(config, "entities", limit=10000))
        events = len(query_table(config, "events", limit=10000))
        transaction.update_metadata(
            applied=applied,
            skipped_low_confidence=skipped_low,
            db_entities=entities,
            db_events=events,
            db_synced=True,
        )
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=validation.file,
        to_status="applied",
        command="graph semantic-apply",
        result=graph_file(root),
        from_statuses=("validated",),
    )
    return GraphApplyResult(
        chapter_number=chapter_number,
        update_file=validation.file,
        graph_file=str(graph_file(root)),
        applied=applied,
        skipped_low_confidence=skipped_low,
        db_entities=entities,
        db_events=events,
    )


def validate_temporal_edge(item: dict[str, Any], label: str, errors: list[str], warnings: list[str]) -> None:
    """Validate optional Temporal KG fields when present."""

    from_chapter = as_optional_int(item.get("from_chapter"))
    to_chapter = as_optional_int(item.get("to_chapter"))
    if item.get("from_chapter") is not None and not from_chapter:
        errors.append(f"{label}.from_chapter must be a positive integer.")
    if item.get("to_chapter") is not None and not to_chapter:
        errors.append(f"{label}.to_chapter must be a positive integer.")
    if from_chapter and to_chapter and to_chapter < from_chapter:
        errors.append(f"{label}.to_chapter cannot be earlier than from_chapter.")
    status = item.get("status")
    if status is not None and str(status) not in {"active", "inactive", "planted", "paid_off", "expired", "resolved", "open", "closed", "stale"}:
        warnings.append(f"{label}.status is not a known temporal status: {status}")
    confidence = item.get("confidence")
    if confidence is not None:
        try:
            value = float(confidence)
        except (TypeError, ValueError):
            errors.append(f"{label}.confidence must be numeric when present.")
        else:
            if value < 0 or value > 1:
                errors.append(f"{label}.confidence must be between 0 and 1.")
            if value < 0.55 and not item.get("evidence_span"):
                warnings.append(f"{label} low-confidence update should include evidence_span.")
    if item.get("evidence_span") is not None and not str(item.get("evidence_span")).strip():
        warnings.append(f"{label}.evidence_span is empty.")


def update_graph(config: ConfigDocument, *, chapter_number: int) -> GraphUpdateResult:
    """Update story graph with mentions and a chapter event from finalized text."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    chapter_path = find_chapter_file(root, chapter_number)
    if chapter_path is None:
        raise ValueError(f"Final manuscript for chapter {chapter_number} was not found.")

    graph = load_graph(root)
    ensure_graph_shape(graph)
    text = safe_read_text(chapter_path)
    summary = read_summary(root, chapter_number)
    title = extract_title(text, chapter_path)

    upsert_canon_entities(graph, root)
    entity_index = {entity["id"]: entity for entity in graph["entities"] if isinstance(entity, dict) and entity.get("id")}

    matched_ids: list[str] = []
    mentions_added = 0
    for entity in entity_index.values():
        names = [str(entity.get("name") or ""), *[str(alias) for alias in normalize_list(entity.get("aliases"))]]
        if any(name and name in text for name in names):
            matched_ids.append(entity["id"])
            mentions = entity.setdefault("mentions", [])
            mention = {
                "chapter_number": chapter_number,
                "reason": "mentioned_in_final_manuscript",
                "source_path": relative_path(root, chapter_path),
            }
            if not has_mention(mentions, chapter_number, mention["reason"]):
                mentions.append(mention)
                mentions_added += 1

    event_id = f"event:ch{chapter_number:03d}:chapter_summary"
    event = {
        "id": event_id,
        "chapter_number": chapter_number,
        "title": title,
        "participants": matched_ids,
        "consequences": summary or trim_text(strip_markdown_heading(text), 240),
        "opens_threads": [],
        "closes_threads": [],
        "source_path": relative_path(root, chapter_path),
        "updated_at": utc_now(),
    }
    events_added = upsert_by_id(graph["events"], event)
    graph["updated_at"] = utc_now()
    save_graph(root, graph)

    extracted = extract_graph_updates(config, chapter_number=chapter_number, source="final")
    applied = apply_graph_updates(config, chapter_number=chapter_number)
    sync_database(config)
    entities = len(query_table(config, "entities", limit=10000))
    events = len(query_table(config, "events", limit=10000))
    return GraphUpdateResult(
        chapter_number=chapter_number,
        graph_file=str(graph_file(root)),
        matched_entities=len(matched_ids),
        mentions_added=mentions_added,
        events_added=events_added,
        db_entities=entities,
        db_events=events,
        update_file=extracted.update_file,
        suggestions=extracted.suggestions,
        review_required=applied.skipped_low_confidence,
    )


def extract_graph_updates(config: ConfigDocument, *, chapter_number: int, source: str = "final") -> GraphExtractResult:
    """Extract reviewable graph update suggestions without mutating canonical graph."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    if source not in {"final", "draft"}:
        raise ValueError("source must be final or draft.")
    root = resolve_project_root(config)
    chapter_path = find_chapter_file(root, chapter_number) if source == "final" else find_draft_chapter_file(root, chapter_number)
    if chapter_path is None:
        raise ValueError(f"{source} manuscript for chapter {chapter_number} was not found.")

    graph = load_graph(root)
    upsert_canon_entities(graph, root)
    text = safe_read_text(chapter_path)
    title = extract_title(text, chapter_path)
    summary = read_summary(root, chapter_number) or trim_text(strip_markdown_heading(text), 240)
    suggestions = build_graph_suggestions(root, graph, text, chapter_number, title, summary, source_path=chapter_path)

    update_dir = root / "50_workbench" / "graph_updates"
    update_dir.mkdir(parents=True, exist_ok=True)
    update_path = update_dir / f"ch{chapter_number:03d}.json"
    payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "source": source,
        "source_path": relative_path(root, chapter_path),
        "status": "review_required" if any(item.get("confidence") == "low" for item in suggestions) else "ready_to_apply",
        "suggestions": suggestions,
        "low_confidence": sum(1 for item in suggestions if item.get("confidence") == "low"),
        "created_at": utc_now(),
    }
    atomic_write_text(update_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return GraphExtractResult(
        chapter_number=chapter_number,
        source=source,
        update_file=str(update_path),
        suggestions=len(suggestions),
        low_confidence=payload["low_confidence"],
    )


def apply_graph_updates(
    config: ConfigDocument,
    *,
    chapter_number: int,
    update_file: str | Path | None = None,
    force_low_confidence: bool = False,
) -> GraphApplyResult:
    """Apply reviewed graph suggestions from a finalized chapter only."""

    root = resolve_project_root(config)
    if find_chapter_file(root, chapter_number) is None:
        raise ValueError("Canonical graph updates require a finalized chapter.")
    update_path = Path(update_file) if update_file else root / "50_workbench" / "graph_updates" / f"ch{chapter_number:03d}.json"
    if not update_path.is_absolute():
        update_path = root / update_path
    if not update_path.exists():
        extract_graph_updates(config, chapter_number=chapter_number, source="final")
    payload = read_json(update_path, default={})
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid graph update file: {update_path}")
    if payload.get("source") != "final":
        raise ValueError("Only final-source graph update files can be applied to canonical graph.")

    graph = load_graph(root)
    ensure_graph_shape(graph)
    upsert_canon_entities(graph, root)
    entity_index = {entity["id"]: entity for entity in graph["entities"] if isinstance(entity, dict) and entity.get("id")}
    applied = 0
    skipped_low = 0
    for suggestion in payload.get("suggestions", []):
        if not isinstance(suggestion, dict):
            continue
        if suggestion.get("confidence") == "low" and not force_low_confidence:
            skipped_low += 1
            continue
        kind = suggestion.get("kind")
        if kind == "mention":
            entity = entity_index.get(str(suggestion.get("entity_id")))
            if not entity:
                continue
            mentions = entity.setdefault("mentions", [])
            reason = str(suggestion.get("reason") or "suggested_from_final")
            if not has_mention(mentions, chapter_number, reason):
                mentions.append(
                    {
                        "chapter_number": chapter_number,
                        "reason": reason,
                        "source_path": suggestion.get("source_path"),
                        "confidence": suggestion.get("confidence"),
                    }
                )
                applied += 1
        elif kind == "status_change":
            entity = entity_index.get(str(suggestion.get("entity_id")))
            if not entity:
                continue
            new_status = suggestion.get("new_status")
            if new_status and entity.get("status") != new_status:
                entity["status"] = new_status
                history = entity.setdefault("status_history", [])
                history.append(
                    {
                        "chapter_number": chapter_number,
                        "status": new_status,
                        "evidence": suggestion.get("evidence"),
                        "confidence": suggestion.get("confidence"),
                        "updated_at": utc_now(),
                    }
                )
                applied += 1
        elif kind == "event":
            event = dict(suggestion.get("event") or {})
            if event:
                applied += upsert_by_id(graph["events"], event)
        elif kind == "relationship":
            relationship = dict(suggestion.get("relationship") or {})
            if relationship:
                applied += upsert_by_id(graph["relationships"], relationship)
        elif kind == "foreshadow":
            entity = dict(suggestion.get("entity") or {})
            if entity:
                applied += upsert_by_id(graph["entities"], entity)
                entity_index[entity["id"]] = entity

    graph["updated_at"] = utc_now()
    save_graph(root, graph)
    report_path = root / "50_workbench" / "graph_updates" / f"ch{chapter_number:03d}.applied.json"
    atomic_write_text(
        report_path,
        json.dumps(
            {
                "chapter_number": chapter_number,
                "update_file": relative_path(root, update_path),
                "applied": applied,
                "skipped_low_confidence": skipped_low,
                "updated_at": utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    sync_database(config)
    entities = len(query_table(config, "entities", limit=10000))
    events = len(query_table(config, "events", limit=10000))
    return GraphApplyResult(
        chapter_number=chapter_number,
        update_file=str(update_path),
        graph_file=str(graph_file(root)),
        applied=applied,
        skipped_low_confidence=skipped_low,
        db_entities=entities,
        db_events=events,
    )


def cascade_graph(config: ConfigDocument, *, from_chapter: int, change_description: str = "") -> GraphCascadeResult:
    """Mark future graph facts as cascade-pending after outline changes."""

    if from_chapter <= 0:
        raise ValueError("from_chapter must be positive.")
    root = resolve_project_root(config)
    graph = load_graph(root)
    marked_entities = 0
    marked_events = 0

    for entity in graph.get("entities", []):
        if not isinstance(entity, dict):
            continue
        mentions = entity.get("mentions") if isinstance(entity.get("mentions"), list) else []
        if any(as_optional_int(item.get("chapter_number") or item.get("chapter")) and as_optional_int(item.get("chapter_number") or item.get("chapter")) >= from_chapter for item in mentions if isinstance(item, dict)):
            entity["cascade_pending"] = True
            entity["cascade_from_chapter"] = from_chapter
            marked_entities += 1

    for event in graph.get("events", []):
        if not isinstance(event, dict):
            continue
        chapter = as_optional_int(event.get("chapter_number") or event.get("chapter"))
        if chapter and chapter >= from_chapter:
            event["cascade_pending"] = True
            event["cascade_from_chapter"] = from_chapter
            marked_events += 1

    graph["cascade"] = {
        "from_chapter": from_chapter,
        "change_description": change_description,
        "updated_at": utc_now(),
    }
    save_graph(root, graph)
    report_dir = root / "50_workbench" / "graph_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"cascade_from_ch{from_chapter:03d}.json"
    atomic_write_text(
        report_path,
        json.dumps(
            {
                "from_chapter": from_chapter,
                "change_description": change_description,
                "marked_entities": marked_entities,
                "marked_events": marked_events,
                "graph_file": relative_path(root, graph_file(root)),
                "updated_at": utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    sync_database(config)
    return GraphCascadeResult(
        from_chapter=from_chapter,
        graph_file=str(graph_file(root)),
        report_file=str(report_path),
        marked_entities=marked_entities,
        marked_events=marked_events,
    )


def check_graph(config: ConfigDocument) -> GraphCheckResult:
    """Generate a deterministic conflict report for graph consistency."""

    root = resolve_project_root(config)
    graph = load_graph(root)
    validation = validate_graph(config)
    issues = list(validation.errors)
    warnings = list(validation.warnings)

    entities = graph.get("entities") if isinstance(graph.get("entities"), list) else []
    relationships = graph.get("relationships") if isinstance(graph.get("relationships"), list) else []
    events = graph.get("events") if isinstance(graph.get("events"), list) else []

    check_duplicate_names(entities, warnings)
    check_relationship_status_conflicts(relationships, issues)
    check_ability_boundaries(entities, warnings)
    check_event_timeline(events, warnings)
    check_location_conflicts(events, warnings)
    draft_risks = collect_agent_draft_risks(root, graph)

    report_dir = root / "50_workbench" / "graph_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "graph_check.md"
    write_graph_report(report_path, validation, issues, warnings, draft_risks)
    return GraphCheckResult(report_file=str(report_path), issues=tuple(issues), warnings=tuple([*warnings, *draft_risks]))


def upsert_canon_entities(graph: dict[str, Any], root: Path) -> None:
    """Mirror simple Bible entity files into story graph without inventing new facts."""

    for relative, entity_type in BIBLE_ENTITY_SOURCES:
        path = root / relative
        payload = read_json(path, default=[])
        for item in normalize_entity_records(payload):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or "").strip()
            if not name:
                continue
            canonical_type = normalize_entity_type(item.get("type") or entity_type)
            entity_id = str(item.get("id") or f"{canonical_type}:{slugify(name)}")
            entity = {
                "id": entity_id,
                "name": name,
                "type": canonical_type,
                "aliases": normalize_list(item.get("aliases")),
                "description": item.get("description") or item.get("summary"),
                "source_path": relative,
                "metadata": item,
            }
            upsert_by_id(graph["entities"], entity)


def load_graph(root: Path) -> dict[str, Any]:
    graph = read_json(graph_file(root), default={})
    if not isinstance(graph, dict):
        graph = {}
    ensure_graph_shape(graph)
    return graph


def save_graph(root: Path, graph: dict[str, Any]) -> None:
    atomic_write_text(graph_file(root), json.dumps(graph, ensure_ascii=False, indent=2) + "\n")


def ensure_graph_shape(graph: dict[str, Any]) -> None:
    graph.setdefault("entities", [])
    graph.setdefault("relationships", [])
    graph.setdefault("events", [])
    for key in ("entities", "relationships", "events"):
        if not isinstance(graph[key], list):
            graph[key] = []


def graph_file(root: Path) -> Path:
    return root / "30_state" / "story_graph.json"


def build_graph_suggestions(
    root: Path,
    graph: dict[str, Any],
    text: str,
    chapter_number: int,
    title: str,
    summary: str,
    *,
    source_path: Path,
) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    source = relative_path(root, source_path)
    entity_index = {
        str(entity.get("id")): entity
        for entity in graph.get("entities", [])
        if isinstance(entity, dict) and entity.get("id")
    }
    matched_ids: list[str] = []
    for entity_id, entity in entity_index.items():
        labels = [str(entity.get("name") or ""), *[str(alias) for alias in normalize_list(entity.get("aliases"))]]
        matched_label = next((label for label in labels if label and label in text), "")
        if not matched_label:
            continue
        matched_ids.append(entity_id)
        suggestions.append(
            {
                "kind": "mention",
                "entity_id": entity_id,
                "entity_name": entity.get("name") or entity_id,
                "chapter_number": chapter_number,
                "reason": "mentioned_in_final_manuscript" if "final/" in source else "suggested_from_draft",
                "confidence": "high" if matched_label == str(entity.get("name") or "") else "medium",
                "evidence": trim_text(extract_evidence_window(text, matched_label), 160),
                "source_path": source,
            }
        )
        status = detect_status_change(text, matched_label)
        if status:
            suggestions.append(
                {
                    "kind": "status_change",
                    "entity_id": entity_id,
                    "entity_name": entity.get("name") or entity_id,
                    "new_status": status,
                    "chapter_number": chapter_number,
                    "confidence": "medium",
                    "evidence": trim_text(extract_evidence_window(text, matched_label), 200),
                    "source_path": source,
                }
            )

    suggestions.extend(
        infer_relationship_suggestions(
            graph,
            text,
            matched_ids=dedupe(matched_ids),
            chapter_number=chapter_number,
            source_path=source,
        )
    )

    suggestions.append(
        {
            "kind": "event",
            "confidence": "high",
            "event": {
                "id": f"event:ch{chapter_number:03d}:chapter_summary",
                "chapter_number": chapter_number,
                "title": title,
                "participants": dedupe(matched_ids),
                "consequences": summary,
                "opens_threads": detect_thread_markers(text, "open"),
                "closes_threads": detect_thread_markers(text, "close"),
                "source_path": source,
                "updated_at": utc_now(),
            },
        }
    )

    for index, marker in enumerate(detect_foreshadow_markers(text), start=1):
        suggestions.append(
            {
                "kind": "foreshadow",
                "confidence": "low",
                "entity": {
                    "id": f"foreshadowing:ch{chapter_number:03d}:{index}",
                    "name": marker,
                    "type": "foreshadowing",
                    "status": "pending_review",
                    "mentions": [
                        {
                            "chapter_number": chapter_number,
                            "reason": "possible_foreshadow_signal",
                            "source_path": source,
                        }
                    ],
                },
                "evidence": marker,
                "source_path": source,
            }
        )

    return suggestions


def apply_semantic_relationship(graph: dict[str, Any], update: dict[str, Any], chapter_number: int) -> int:
    source = str(update.get("source") or update.get("from") or "").strip()
    target = str(update.get("target") or update.get("to") or "").strip()
    relation = str(update.get("relation") or update.get("type_label") or update.get("relationship") or "related").strip()
    if not source or not target or not relation:
        return 0
    for item in graph.get("relationships", []):
        if not isinstance(item, dict):
            continue
        same_pair = str(item.get("source") or item.get("from")) == source and str(item.get("target") or item.get("to")) == target
        if same_pair and not item.get("to_chapter") and str(item.get("status") or "active") == "active":
            item["to_chapter"] = max(1, chapter_number - 1)
            item["status"] = "inactive"
    relationship = {
        "id": str(update.get("id") or f"rel:{source}:{target}:{relation}:ch{chapter_number:03d}").replace(" ", "_"),
        "source": source,
        "target": target,
        "type": relation,
        "status": str(update.get("status") or "active"),
        "from_chapter": chapter_number,
        "to_chapter": update.get("to_chapter"),
        "confidence": confidence_value(update.get("confidence")),
        "evidence_span": update.get("evidence_span"),
        "source_path": update.get("source_path"),
        "updated_at": utc_now(),
    }
    graph["relationships"].append(relationship)
    return 1


def apply_semantic_status(entity_index: dict[str, dict[str, Any]], update: dict[str, Any], chapter_number: int) -> int:
    entity_id = str(update.get("entity_id") or update.get("character") or update.get("id") or "").strip()
    if not entity_id:
        return 0
    entity = entity_index.get(entity_id)
    if not entity:
        return 0
    status = str(update.get("status") or update.get("new_status") or "").strip()
    if not status:
        return 0
    entity["status"] = status
    history = entity.setdefault("status_history", [])
    history.append(
        {
            "chapter_number": chapter_number,
            "status": status,
            "confidence": confidence_value(update.get("confidence")),
            "evidence_span": update.get("evidence_span"),
            "updated_at": utc_now(),
        }
    )
    return 1


def apply_semantic_event(graph: dict[str, Any], update: dict[str, Any], chapter_number: int, source_path: str) -> int:
    title = str(update.get("title") or update.get("name") or update.get("event") or "").strip()
    if not title:
        return 0
    event = {
        "id": str(update.get("id") or f"event:semantic:ch{chapter_number:03d}:{slugify(title)}"),
        "chapter_number": chapter_number,
        "title": title,
        "participants": normalize_list(update.get("participants")),
        "locations": normalize_list(update.get("locations") or update.get("location")),
        "consequences": update.get("consequences") or update.get("summary") or "",
        "from_chapter": chapter_number,
        "confidence": confidence_value(update.get("confidence")),
        "evidence_span": update.get("evidence_span"),
        "source_path": source_path,
        "updated_at": utc_now(),
    }
    return upsert_by_id(graph["events"], event)


def apply_semantic_foreshadow(graph: dict[str, Any], update: dict[str, Any], chapter_number: int, update_type: str) -> int:
    name = str(update.get("name") or update.get("foreshadow") or update.get("id") or "").strip()
    if not name:
        return 0
    status = "paid_off" if update_type == "foreshadow_paid_off" else "planted"
    entity = {
        "id": str(update.get("id") or f"foreshadowing:{slugify(name)}"),
        "name": name,
        "type": "foreshadowing",
        "status": status,
        "from_chapter": chapter_number,
        "confidence": confidence_value(update.get("confidence")),
        "evidence_span": update.get("evidence_span"),
        "mentions": [
            {
                "chapter_number": chapter_number,
                "reason": update_type,
                "source_path": update.get("source_path"),
            }
        ],
        "updated_at": utc_now(),
    }
    return upsert_by_id(graph["entities"], entity)


def apply_semantic_ability(graph: dict[str, Any], update: dict[str, Any], chapter_number: int) -> int:
    name = str(update.get("name") or update.get("ability") or update.get("id") or "").strip()
    if not name:
        return 0
    entity = {
        "id": str(update.get("id") or f"ability:{slugify(name)}"),
        "name": name,
        "type": "ability",
        "cost": update.get("cost"),
        "limit": update.get("limit"),
        "cooldown": update.get("cooldown"),
        "violation_risk": update.get("violation_risk"),
        "from_chapter": chapter_number,
        "confidence": confidence_value(update.get("confidence")),
        "evidence_span": update.get("evidence_span"),
        "updated_at": utc_now(),
    }
    return upsert_by_id(graph["entities"], entity)


def confidence_value(value: Any) -> float | None:
    if isinstance(value, str):
        lookup = {"low": 0.3, "medium": 0.7, "high": 0.9}
        if value.lower() in lookup:
            return lookup[value.lower()]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def resolve_under_root(root: Path, file_path: str | Path) -> Path:
    path = Path(file_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def infer_relationship_suggestions(
    graph: dict[str, Any],
    text: str,
    *,
    matched_ids: list[str],
    chapter_number: int,
    source_path: str,
) -> list[dict[str, Any]]:
    if len(matched_ids) < 2:
        return []
    entity_index = {
        str(entity.get("id")): entity
        for entity in graph.get("entities", [])
        if isinstance(entity, dict) and entity.get("id")
    }
    suggestions: list[dict[str, Any]] = []
    for index, source_id in enumerate(matched_ids):
        for target_id in matched_ids[index + 1 :]:
            source = entity_index.get(source_id)
            target = entity_index.get(target_id)
            if not source or not target:
                continue
            relation_type, confidence, evidence = infer_relation_type(text, source, target)
            relationship_id = f"rel:{source_id}:{target_id}:{relation_type}".replace(" ", "_")
            suggestions.append(
                {
                    "kind": "relationship",
                    "confidence": confidence,
                    "relationship": {
                        "id": relationship_id,
                        "source": source_id,
                        "target": target_id,
                        "type": relation_type,
                        "status": "active",
                        "evidence": trim_text(evidence, 220),
                        "first_seen_chapter": chapter_number,
                        "last_seen_chapter": chapter_number,
                        "source_path": source_path,
                        "updated_at": utc_now(),
                    },
                }
            )
    return suggestions[:8]


def infer_relation_type(text: str, source: dict[str, Any], target: dict[str, Any]) -> tuple[str, str, str]:
    source_label = str(source.get("name") or source.get("id") or "")
    target_label = str(target.get("name") or target.get("id") or "")
    evidence = relation_window(text, source_label, target_label)
    lowered = evidence.lower()
    patterns = (
        ("alliance", "high", ("allies", "ally", "alliance", "swears with", "结盟", "同盟", "并肩", "盟友")),
        ("conflict", "high", ("enemy", "duel", "fight", "attacks", "confronts", "敌", "决斗", "交手", "冲突", "追杀")),
        ("mentor", "medium", ("mentor", "teacher", "master", "teaches", "师父", "师尊", "教导")),
        ("betrayal", "high", ("betray", "traitor", "sells out", "背叛", "出卖", "叛变")),
        ("kinship", "medium", ("father", "mother", "brother", "sister", "family", "父", "母", "兄", "姐", "家族")),
        ("romantic_tension", "medium", ("love", "kiss", "jealous", "心动", "相思", "吻", "吃醋")),
        ("organization_membership", "medium", ("sect", "guild", "clan", "门派", "宗门", "公会", "家族")),
    )
    for relation_type, confidence, markers in patterns:
        if any(marker in lowered or marker in evidence for marker in markers):
            return relation_type, confidence, evidence
    if evidence:
        return "co_occurs", "medium", evidence
    return "co_occurs", "low", f"{source_label} / {target_label}"


def relation_window(text: str, source_label: str, target_label: str, *, radius: int = 140) -> str:
    if not source_label or not target_label:
        return ""
    source_index = text.find(source_label)
    target_index = text.find(target_label)
    if source_index < 0 or target_index < 0:
        return ""
    start = max(0, min(source_index, target_index) - radius)
    end = min(len(text), max(source_index + len(source_label), target_index + len(target_label)) + radius)
    return text[start:end]


def detect_status_change(text: str, label: str) -> str | None:
    window = extract_evidence_window(text, label)
    lowered = window.lower()
    if any(marker in lowered for marker in ("dead", "dies", "killed", "death")) or any(marker in window for marker in ("死亡", "死去", "陨落", "被杀")):
        return "dead"
    if any(marker in lowered for marker in ("wounded", "injured")) or any(marker in window for marker in ("受伤", "重伤")):
        return "injured"
    if any(marker in lowered for marker in ("betrays", "traitor")) or any(marker in window for marker in ("背叛", "叛变")):
        return "betrayed"
    if any(marker in lowered for marker in ("revealed", "exposed")) or any(marker in window for marker in ("揭露", "暴露")):
        return "revealed"
    return None


def extract_evidence_window(text: str, label: str, *, radius: int = 80) -> str:
    if not label:
        return ""
    index = text.find(label)
    if index < 0:
        return ""
    return text[max(0, index - radius) : index + len(label) + radius]


def detect_thread_markers(text: str, mode: str) -> list[str]:
    if mode == "close":
        markers = ("solved", "resolved", "closed", "真相", "解决", "了结")
    else:
        markers = ("mystery", "clue", "question", "秘密", "线索", "疑问")
    return [marker for marker in markers if marker in text or marker in text.lower()][:4]


def detect_foreshadow_markers(text: str) -> list[str]:
    markers = ("预感", "线索", "伏笔", "秘密", "不祥", "omen", "clue", "prophecy", "strange")
    found = [marker for marker in markers if marker in text or marker in text.lower()]
    return dedupe(found)[:4]


def find_chapter_file(root: Path, chapter_number: int) -> Path | None:
    final_dir = root / "40_manuscript" / "final"
    names = [
        f"ch{chapter_number:03d}.md",
        f"ch{chapter_number:03d}.txt",
        f"chapter_{chapter_number:03d}.md",
        f"chapter_{chapter_number:03d}.txt",
        f"{chapter_number}.md",
        f"{chapter_number}.txt",
    ]
    for name in names:
        path = final_dir / name
        if path.exists():
            return path
    for path in sorted([*final_dir.glob("*.md"), *final_dir.glob("*.txt")]):
        if parse_chapter_number(path) == chapter_number:
            return path
    return None


def find_draft_chapter_file(root: Path, chapter_number: int) -> Path | None:
    draft_dir = root / "40_manuscript" / "draft"
    names = [
        f"ch{chapter_number:03d}.md",
        f"ch{chapter_number:03d}.txt",
        f"chapter_{chapter_number:03d}.md",
        f"chapter_{chapter_number:03d}.txt",
        f"{chapter_number}.md",
        f"{chapter_number}.txt",
    ]
    for name in names:
        path = draft_dir / name
        if path.exists():
            return path
    for path in sorted([*draft_dir.glob("*.md"), *draft_dir.glob("*.txt")]):
        if parse_chapter_number(path) == chapter_number:
            return path
    return None


def read_summary(root: Path, chapter_number: int) -> str | None:
    summary_dir = root / "40_manuscript" / "summaries"
    for name in (f"ch{chapter_number:03d}.md", f"chapter_{chapter_number:03d}.md", f"{chapter_number}.md"):
        path = summary_dir / name
        if path.exists():
            return safe_read_text(path).strip()
    return None


def check_duplicate_names(entities: list[Any], warnings: list[str]) -> None:
    names: dict[tuple[str, str], str] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        key = (normalize_entity_type(entity.get("type")), str(entity.get("name") or ""))
        if not key[1]:
            continue
        if key in names:
            warnings.append(f"duplicate entity name/type: {key[1]} ({key[0]})")
        else:
            names[key] = str(entity.get("id"))


def check_relationship_status_conflicts(relationships: list[Any], issues: list[str]) -> None:
    seen: dict[tuple[str, str, str], str] = {}
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        source = str(relationship.get("source") or relationship.get("from") or "")
        target = str(relationship.get("target") or relationship.get("to") or "")
        relation_type = str(relationship.get("type") or relationship.get("relation") or "")
        status = str(relationship.get("status") or "")
        key = (source, target, relation_type)
        if key in seen and status and seen[key] and seen[key] != status:
            issues.append(f"relationship status conflict: {source}->{target} {relation_type} ({seen[key]} vs {status})")
        else:
            seen[key] = status


def check_ability_boundaries(entities: list[Any], warnings: list[str]) -> None:
    for entity in entities:
        if not isinstance(entity, dict) or normalize_entity_type(entity.get("type")) != "ability":
            continue
        level = entity.get("level")
        max_level = entity.get("max_level")
        if isinstance(level, int) and isinstance(max_level, int) and level > max_level:
            warnings.append(f"ability level exceeds max_level: {entity.get('id')}")
        if not entity.get("cost") and not entity.get("limitation"):
            warnings.append(f"ability missing cost/limitation: {entity.get('id')}")


def check_event_timeline(events: list[Any], warnings: list[str]) -> None:
    last = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        chapter_number = as_optional_int(event.get("chapter_number") or event.get("chapter"))
        if chapter_number is None:
            warnings.append(f"event missing chapter_number: {event.get('id')}")
            continue
        if chapter_number < last:
            warnings.append(f"event timeline order decreases at: {event.get('id')}")
        last = max(last, chapter_number)


def check_location_conflicts(events: list[Any], warnings: list[str]) -> None:
    seen: dict[tuple[int, str], str] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        chapter_number = as_optional_int(event.get("chapter_number") or event.get("chapter"))
        location = event.get("location")
        if not chapter_number or not location:
            continue
        for participant in normalize_list(event.get("participants")):
            key = (chapter_number, str(participant))
            if key in seen and seen[key] != location:
                warnings.append(f"possible location conflict in chapter {chapter_number}: {participant} at {seen[key]} and {location}")
            else:
                seen[key] = str(location)


def collect_agent_draft_risks(root: Path, graph: dict[str, Any]) -> list[str]:
    """Read draft manuscripts and report graph risks without mutating canon graph."""

    risks: list[str] = []
    canon_entities = collect_canon_entities(root, graph)
    final_chapters = finalized_chapter_numbers(root)
    last_finalized = max(final_chapters) if final_chapters else 0

    for draft_path in list_draft_files(root):
        chapter_number = parse_chapter_number(draft_path)
        if chapter_number is None or chapter_number in final_chapters:
            continue
        text = safe_read_text(draft_path)
        gate_result = read_gate_result(root, chapter_number)
        gate_passed = gate_result.get("passed") if isinstance(gate_result, dict) else None

        if gate_passed is False:
            risks.append(f"Agent draft timeline risk ch{chapter_number:03d}: gate failed; story graph must remain frozen.")
        elif chapter_number > last_finalized + 1:
            risks.append(
                f"Agent draft timeline risk ch{chapter_number:03d}: previous finalized chapter is ch{last_finalized:03d}."
            )
        else:
            risks.append(
                f"Agent draft timeline risk ch{chapter_number:03d}: draft is not final; graph update waits for chapter finalize."
            )

        character_matches = find_matching_entities(text, canon_entities, "character")
        if character_matches:
            risks.append(
                f"Agent draft character risk ch{chapter_number:03d}: verify state for {', '.join(character_matches)} before finalize."
            )

        location_matches = find_matching_entities(text, canon_entities, "location")
        if location_matches:
            risks.append(
                f"Agent draft location risk ch{chapter_number:03d}: verify placement for {', '.join(location_matches)} before finalize."
            )

        ability_matches = find_matching_entities(text, canon_entities, "ability")
        if ability_matches:
            risks.append(
                f"Agent draft ability boundary risk ch{chapter_number:03d}: verify cost/limit for {', '.join(ability_matches)}."
            )

    return dedupe(risks)


def collect_canon_entities(root: Path, graph: dict[str, Any]) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in graph.get("entities", []):
        if isinstance(item, dict):
            add_canon_entity(entities, seen, item)

    for relative, entity_type in BIBLE_ENTITY_SOURCES:
        payload = read_json(root / relative, default=[])
        for item in normalize_entity_records(payload):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("title") or "").strip()
            if not name:
                continue
            canonical_type = normalize_entity_type(item.get("type") or entity_type)
            entity = {
                "id": str(item.get("id") or f"{canonical_type}:{slugify(name)}"),
                "name": name,
                "type": canonical_type,
                "aliases": normalize_list(item.get("aliases")),
            }
            add_canon_entity(entities, seen, entity)

    return entities


def add_canon_entity(entities: list[dict[str, Any]], seen: set[str], entity: dict[str, Any]) -> None:
    entity_id = str(entity.get("id") or "").strip()
    if entity_id and entity_id in seen:
        return
    if entity_id:
        seen.add(entity_id)
    entities.append(entity)


def list_draft_files(root: Path) -> list[Path]:
    draft_dir = root / "40_manuscript" / "draft"
    if not draft_dir.exists():
        return []
    paths = [*draft_dir.glob("*.md"), *draft_dir.glob("*.txt")]
    return sorted(paths)


def finalized_chapter_numbers(root: Path) -> set[int]:
    final_dir = root / "40_manuscript" / "final"
    if not final_dir.exists():
        return set()
    numbers: set[int] = set()
    for path in [*final_dir.glob("*.md"), *final_dir.glob("*.txt")]:
        chapter_number = parse_chapter_number(path)
        if chapter_number is not None:
            numbers.add(chapter_number)
    return numbers


def read_gate_result(root: Path, chapter_number: int) -> dict[str, Any]:
    path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    payload = read_json(path, default={})
    return payload if isinstance(payload, dict) else {}


def find_matching_entities(text: str, entities: list[dict[str, Any]], entity_type: str, *, limit: int = 6) -> list[str]:
    matches: list[str] = []
    for entity in entities:
        if normalize_entity_type(entity.get("type")) != entity_type:
            continue
        labels = [str(entity.get("name") or ""), *[str(alias) for alias in normalize_list(entity.get("aliases"))]]
        if any(label and label in text for label in labels):
            name = str(entity.get("name") or entity.get("id") or "").strip()
            if name:
                matches.append(name)
        if len(matches) >= limit:
            break
    return dedupe(matches)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def write_graph_report(
    path: Path,
    validation: GraphValidationResult,
    issues: list[str],
    warnings: list[str],
    draft_risks: list[str] | None = None,
) -> None:
    lines = [
        "# Story Graph Check",
        "",
        f"- Generated at: {utc_now()}",
        f"- Graph file: {validation.graph_file}",
        f"- Entities: {validation.entities}",
        f"- Relationships: {validation.relationships}",
        f"- Events: {validation.events}",
        "",
        "## Issues",
        "",
    ]
    lines.extend([f"- {issue}" for issue in issues] or ["- None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in warnings] or ["- None"])
    lines.extend(["", "## Agent Draft Risks", ""])
    lines.extend([f"- {risk}" for risk in draft_risks or []] or ["- None"])
    lines.extend(["", "## Canonical Entity Types", ""])
    lines.extend([f"- {entity_type}" for entity_type in CANONICAL_ENTITY_TYPES])
    atomic_write_text(path, "\n".join(lines) + "\n")


def upsert_by_id(items: list[Any], new_item: dict[str, Any]) -> int:
    new_id = new_item.get("id")
    for index, item in enumerate(items):
        if isinstance(item, dict) and item.get("id") == new_id:
            merged = dict(item)
            merged.update({key: value for key, value in new_item.items() if value is not None})
            items[index] = merged
            return 0
    items.append(new_item)
    return 1


def has_mention(mentions: list[Any], chapter_number: int, reason: str) -> bool:
    for mention in mentions:
        if not isinstance(mention, dict):
            continue
        if as_optional_int(mention.get("chapter_number") or mention.get("chapter")) == chapter_number and mention.get("reason") == reason:
            return True
    return False


def normalize_entity_type(value: Any) -> str:
    raw = str(value or "unknown").strip().lower()
    return ENTITY_TYPE_ALIASES.get(raw, raw)


def normalize_collection(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def normalize_entity_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("entities", "items", "characters", "locations", "factions", "abilities", "records", "data"):
            records = value.get(key)
            if isinstance(records, list):
                return records
        return list(value.values())
    return []


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    return [value]


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def extract_title(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem


def strip_markdown_heading(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("#")).strip()


def trim_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def parse_chapter_number(path: Path) -> int | None:
    numeric = re.search(r"(\d{1,5})", path.stem)
    return int(numeric.group(1)) if numeric else None
    match = re.search(r"(?:ch|chapter[_-]?|第)?0*(\d{1,5})", path.stem, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def as_optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def slugify(value: str) -> str:
    slug = re.sub(r"\s+", "_", value.strip())
    slug = re.sub(r"[^\w\u4e00-\u9fff.-]+", "", slug)
    return slug or "unnamed"


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
