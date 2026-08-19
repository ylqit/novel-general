"""Story graph validation, deterministic update, and conflict reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.db import query_table, sync_database
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import (
    existing_manuscript_chapter_path,
    list_canonical_chapter_files,
    list_finalized_chapter_files,
)


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
    if status is not None and str(status) not in {"planned", "active", "inactive", "planted", "paid_off", "expired", "resolved", "open", "closed", "stale"}:
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
    suggestions = [item for item in payload.get("suggestions", []) if isinstance(item, dict)]
    suggested_relationship_ids: set[str] = set()
    for suggestion in suggestions:
        relationship = suggestion.get("relationship")
        if suggestion.get("kind") == "relationship" and isinstance(relationship, dict) and relationship.get("id"):
            suggested_relationship_ids.add(str(relationship["id"]))
    source_path = str(payload.get("source_path") or "")
    retained_relationships = []
    removed_relationships = 0
    for relationship in graph["relationships"]:
        if (
            isinstance(relationship, dict)
            and source_path
            and relationship.get("source_path") == source_path
            and as_optional_int(relationship.get("first_seen_chapter")) == chapter_number
            and str(relationship.get("id") or "") not in suggested_relationship_ids
        ):
            removed_relationships += 1
            continue
        retained_relationships.append(relationship)
    graph["relationships"] = retained_relationships
    entity_index = {entity["id"]: entity for entity in graph["entities"] if isinstance(entity, dict) and entity.get("id")}
    removed_statuses = remove_stale_deterministic_statuses(
        entity_index,
        chapter_number=chapter_number,
        source_path=source_path,
    )
    applied = removed_relationships + removed_statuses
    skipped_low = 0
    for suggestion in suggestions:
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
                        "source_path": suggestion.get("source_path") or source_path,
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
                "removed_stale_relationships": removed_relationships,
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
        status_match = detect_status_change_evidence(text, matched_label)
        if status_match:
            status, status_evidence = status_match
            suggestions.append(
                {
                    "kind": "status_change",
                    "entity_id": entity_id,
                    "entity_name": entity.get("name") or entity_id,
                    "new_status": status,
                    "chapter_number": chapter_number,
                    "confidence": "medium",
                    "evidence": trim_text(status_evidence, 200),
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
    typed_evidence = relation_clause(text, source_label, target_label)
    lowered = typed_evidence.lower()
    patterns = (
        ("alliance", "high", ("allies", "ally", "alliance", "swears with", "结盟", "同盟", "并肩", "盟友")),
        ("conflict", "high", ("enemy", "duel", "fight", "attacks", "confronts", "敌人", "决斗", "交手", "冲突", "追杀")),
        ("mentor", "medium", ("mentor", "teacher", "master", "teaches", "师父", "师尊", "教导")),
        ("betrayal", "high", ("betray", "traitor", "sells out", "背叛", "出卖", "叛变")),
        ("kinship", "medium", ("father", "mother", "brother", "sister", "family", "父亲", "母亲", "兄长", "姐姐", "弟弟", "妹妹", "家人", "亲属")),
        ("romantic_tension", "medium", ("love", "kiss", "jealous", "爱慕", "亲吻", "心动", "相思", "吃醋", "恋慕")),
        ("organization_membership", "medium", ("sect", "guild", "clan", "门派", "宗门", "公会", "家族")),
    )
    for relation_type, confidence, markers in patterns:
        if typed_evidence and any(relation_marker_present(marker, lowered, typed_evidence) for marker in markers):
            return relation_type, confidence, evidence
    if evidence:
        return "co_occurs", "medium", evidence
    return "co_occurs", "low", f"{source_label} / {target_label}"


def relation_window(text: str, source_label: str, target_label: str, *, radius: int = 140) -> str:
    if not source_label or not target_label:
        return ""
    source_positions = [match.start() for match in re.finditer(re.escape(source_label), text)]
    target_positions = [match.start() for match in re.finditer(re.escape(target_label), text)]
    if not source_positions or not target_positions:
        return ""
    source_index, target_index = min(
        ((source, target) for source in source_positions for target in target_positions),
        key=lambda pair: abs(pair[0] - pair[1]),
    )
    start = max(0, min(source_index, target_index) - radius)
    end = min(len(text), max(source_index + len(source_label), target_index + len(target_label)) + radius)
    return text[start:end]


def relation_clause(text: str, source_label: str, target_label: str) -> str:
    """Return the shortest prose clause that explicitly names both entities."""

    clauses = [
        clause.strip()
        for clause in re.split(r"(?<=[。！？!?；;])|\n+", text)
        if source_label in clause and target_label in clause
    ]
    return min(clauses, key=len) if clauses else ""


def relation_marker_present(marker: str, lowered: str, evidence: str) -> bool:
    if marker.isascii():
        return re.search(rf"\b{re.escape(marker.lower())}\b", lowered) is not None
    return marker in evidence


def detect_status_change(text: str, label: str) -> str | None:
    match = detect_status_change_evidence(text, label)
    return match[0] if match else None


def detect_status_change_evidence(text: str, label: str) -> tuple[str, str] | None:
    """Return only status statements grammatically bound to the named entity."""

    if not label:
        return None
    escaped = re.escape(label)
    patterns = (
        (
            "dead",
            (
                rf"{escaped}(?:已经|已|当场|最终|后来|忽然|突然)?(?:身亡|死亡|死去|陨落)",
                rf"{escaped}(?:已经|已|当场)?被杀",
                rf"(?:死亡|死去|陨落|被杀|身亡)的{escaped}",
                rf"\b{escaped}\b\s+(?:is|was|became|lay|lies)?\s*(?:dead|killed|dies)\b",
                rf"\b(?:dead|killed)\s+{escaped}\b",
            ),
        ),
        (
            "injured",
            (
                rf"{escaped}(?:已经|已|仍|正好|忽然|突然|也|又)?(?:受伤|重伤|负伤)",
                rf"{escaped}(?:身受|身负|受了|负了)(?:重伤|伤)",
                rf"(?:受伤|重伤|负伤|身受重伤)的{escaped}",
                rf"\b{escaped}\b\s+(?:is|was|became|remains)?\s*(?:badly\s+|seriously\s+)?(?:wounded|injured)\b",
                rf"\b(?:wounded|injured)\s+{escaped}\b",
            ),
        ),
        (
            "betrayed",
            (
                rf"{escaped}(?:已经|已|竟|忽然|突然)?(?:背叛|叛变)",
                rf"{escaped}(?:是|成了|成为)(?:叛徒|内奸)",
                rf"\b{escaped}\b\s+(?:betrays|betrayed|is\s+a\s+traitor)\b",
            ),
        ),
        (
            "revealed",
            (
                rf"{escaped}(?:的身份)?(?:已经|已|终于)?(?:被揭露|被暴露|揭露|暴露)",
                rf"\b{escaped}(?:'s)?\b[^.!?\n]{{0,12}}\b(?:revealed|exposed)\b",
            ),
        ),
    )
    for clause in re.split(r"(?<=[。！？!?；;\n])", text):
        if label not in clause:
            continue
        for status, status_patterns in patterns:
            for pattern in status_patterns:
                match = re.search(pattern, clause, flags=re.IGNORECASE)
                if match and not status_match_is_negated(clause, match):
                    return status, clause.strip()
    return None


def status_match_is_negated(clause: str, match: re.Match[str]) -> bool:
    start = max(0, match.start() - 5)
    end = min(len(clause), match.end() + 2)
    context = clause[start:end].lower()
    return any(
        marker in context
        for marker in (
            "没受伤",
            "没有受伤",
            "未受伤",
            "并未受伤",
            "并没有受伤",
            "不是受伤",
            "没有死亡",
            "并未死亡",
            "not injured",
            "not wounded",
            "not dead",
        )
    )


def remove_stale_deterministic_statuses(
    entity_index: dict[str, dict[str, Any]],
    *,
    chapter_number: int,
    source_path: str,
) -> int:
    removed = 0
    for entity in entity_index.values():
        history = entity.get("status_history")
        if not isinstance(history, list):
            continue
        retained: list[dict[str, Any]] = []
        removed_values: list[str] = []
        for entry in history:
            if not isinstance(entry, dict):
                continue
            same_deterministic_source = (
                as_optional_int(entry.get("chapter_number")) == chapter_number
                and str(entry.get("source_path") or "") == source_path
            )
            if same_deterministic_source:
                removed += 1
                removed_values.append(str(entry.get("status") or ""))
                continue
            retained.append(entry)
        if len(retained) == len(history):
            continue
        entity["status_history"] = retained
        if str(entity.get("status") or "") in removed_values:
            replacement = next(
                (str(entry.get("status")) for entry in reversed(retained) if entry.get("status")),
                "",
            )
            if replacement:
                entity["status"] = replacement
            else:
                entity.pop("status", None)
    return removed


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
    return existing_manuscript_chapter_path(root, chapter_number, lane="final")


def find_draft_chapter_file(root: Path, chapter_number: int) -> Path | None:
    return existing_manuscript_chapter_path(root, chapter_number, lane="draft")


def read_summary(root: Path, chapter_number: int) -> str | None:
    path = existing_manuscript_chapter_path(root, chapter_number, lane="summaries")
    return safe_read_text(path).strip() if path is not None else None


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
    active_states: dict[tuple[str, str, str], str] = {}
    for relationship in relationships:
        if not isinstance(relationship, dict):
            continue
        status = str(relationship.get("status") or "")
        if status != "active" or relationship.get("to_chapter"):
            continue
        source = str(relationship.get("source") or relationship.get("from") or "")
        target = str(relationship.get("target") or relationship.get("to") or "")
        relation_type = str(relationship.get("type") or relationship.get("relation") or "")
        state = str(relationship.get("state") or "")
        key = (source, target, relation_type)
        if key in active_states and state and active_states[key] and active_states[key] != state:
            issues.append(
                f"active relationship state conflict: {source}->{target} {relation_type} "
                f"({active_states[key]} vs {state})"
            )
        else:
            active_states[key] = state


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
                f"Agent draft timeline risk ch{chapter_number:03d}: draft is not final; semantic materialization waits for chapter finalize."
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
    return [path for _number, path in list_canonical_chapter_files(root / "40_manuscript" / "draft")]


def finalized_chapter_numbers(root: Path) -> set[int]:
    return {chapter_number for chapter_number, _path in list_finalized_chapter_files(root)}


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
