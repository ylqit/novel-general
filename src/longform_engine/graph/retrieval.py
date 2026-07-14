"""Local graph traversal retrieval for narrative Graph-RAG."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.storage import resolve_project_root


@dataclass(frozen=True)
class GraphTraversalHit:
    """Explainable graph retrieval hit."""

    id: str
    kind: str
    label: str
    graph_score: float
    hop_distance: int
    path_reason: str
    evidence_span: str
    source_path: str
    related_chapters: tuple[int, ...]
    payload: dict[str, Any]


@dataclass(frozen=True)
class GraphTraversalResult:
    """Graph traversal query result."""

    query: str
    chapter_number: int
    hits: tuple[GraphTraversalHit, ...]


def retrieve_graph(config: ConfigDocument, *, query_text: str, chapter_number: int, max_hops: int = 2, top_k: int = 12) -> GraphTraversalResult:
    """Retrieve local graph facts with deterministic 1-2 hop traversal."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    if not query_text.strip():
        raise ValueError("graph retrieve query cannot be empty.")
    root = resolve_project_root(config)
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    if not isinstance(graph, dict) or is_graph_stale(root):
        return GraphTraversalResult(query=query_text, chapter_number=chapter_number, hits=())
    entities = [item for item in normalize_records(graph.get("entities")) if isinstance(item, dict)]
    relationships = [item for item in normalize_records(graph.get("relationships")) if isinstance(item, dict) and edge_active(item, chapter_number)]
    events = [item for item in normalize_records(graph.get("events")) if isinstance(item, dict) and event_visible(item, chapter_number)]
    entity_index = {str(entity.get("id")): entity for entity in entities if entity.get("id")}
    seeds = seed_entities(query_text, entities)
    if not seeds:
        seeds = fallback_seed_entities(query_text, entities, relationships)
    adjacency = build_adjacency(relationships)
    reached = traverse(seeds, adjacency, max_hops=max_hops)

    hits: list[GraphTraversalHit] = []
    seen: set[str] = set()
    query_lower = query_text.lower()
    for relation in relationships:
        source = str(relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target") or relation.get("to") or "")
        hop = min(reached.get(source, 99), reached.get(target, 99))
        relation_label = str(relation.get("type") or relation.get("relation") or "")
        if hop > max_hops and not relation_matches_query(query_lower, relation_label):
            continue
        hit_id = str(relation.get("id") or f"relationship:{source}:{target}:{relation_label}")
        if hit_id in seen:
            continue
        seen.add(hit_id)
        source_name = entity_name(entity_index.get(source), source)
        target_name = entity_name(entity_index.get(target), target)
        hits.append(
            GraphTraversalHit(
                id=hit_id,
                kind="relationship",
                label=f"{source_name} -> {target_name}: {relation_label}",
                graph_score=round(graph_score(query_lower, relation_label, hop), 6),
                hop_distance=hop if hop <= max_hops else max_hops + 1,
                path_reason=f"{source_name} --{relation_label}--> {target_name}",
                evidence_span=str(relation.get("evidence_span") or relation.get("evidence") or ""),
                source_path=str(relation.get("source_path") or ""),
                related_chapters=related_chapters(relation),
                payload=relation,
            )
        )

    reached_ids = set(reached)
    for event in events:
        participants = set(normalize_list(event.get("participants")))
        if participants and not participants.intersection(reached_ids) and not event_matches_query(query_lower, event):
            continue
        hit_id = str(event.get("id") or event.get("title") or "")
        if not hit_id or hit_id in seen:
            continue
        seen.add(hit_id)
        hop = min((reached.get(participant, max_hops + 1) for participant in participants), default=max_hops + 1)
        label = str(event.get("title") or event.get("name") or hit_id)
        hits.append(
            GraphTraversalHit(
                id=hit_id,
                kind="event",
                label=label,
                graph_score=round(graph_score(query_lower, label, hop), 6),
                hop_distance=hop,
                path_reason=f"event participates: {', '.join(sorted(participants)) or 'unknown'}",
                evidence_span=str(event.get("evidence_span") or event.get("consequences") or ""),
                source_path=str(event.get("source_path") or ""),
                related_chapters=related_chapters(event),
                payload=event,
            )
        )

    for entity in entities:
        entity_type = str(entity.get("type") or "")
        if not entity_visible(entity, chapter_number):
            continue
        if entity_type == "foreshadowing" and query_requests_foreshadow(query_lower):
            hits.append(entity_hit(entity, kind="foreshadowing", query_lower=query_lower, hop=max_hops + 1))
        elif entity_type == "ability" and query_requests_ability(query_lower):
            hits.append(entity_hit(entity, kind="ability", query_lower=query_lower, hop=max_hops + 1))

    hits.sort(key=lambda item: (-item.graph_score, item.hop_distance, item.kind, item.id))
    return GraphTraversalResult(query=query_text, chapter_number=chapter_number, hits=tuple(hits[:top_k]))


def seed_entities(query_text: str, entities: list[dict[str, Any]]) -> set[str]:
    seeds: set[str] = set()
    query_lower = query_text.lower()
    for entity in entities:
        entity_id = str(entity.get("id") or "")
        names = [entity_id, str(entity.get("name") or ""), *normalize_list(entity.get("aliases"))]
        if any(name and name.lower() in query_lower for name in names):
            seeds.add(entity_id)
    return seeds


def fallback_seed_entities(query_text: str, entities: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> set[str]:
    query_lower = query_text.lower()
    if query_requests_relationship(query_lower):
        seeds: set[str] = set()
        for relation in relationships:
            if len(seeds) >= 4:
                break
            seeds.add(str(relation.get("source") or relation.get("from") or ""))
            seeds.add(str(relation.get("target") or relation.get("to") or ""))
        return {item for item in seeds if item}
    return {str(entity.get("id")) for entity in entities if str(entity.get("type") or "") == "character"} if query_requests_character(query_lower) else set()


def build_adjacency(relationships: list[dict[str, Any]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = {}
    for relation in relationships:
        source = str(relation.get("source") or relation.get("from") or "")
        target = str(relation.get("target") or relation.get("to") or "")
        if not source or not target:
            continue
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    return adjacency


def traverse(seeds: set[str], adjacency: dict[str, set[str]], *, max_hops: int) -> dict[str, int]:
    reached = {seed: 0 for seed in seeds if seed}
    frontier = set(reached)
    for hop in range(1, max_hops + 1):
        next_frontier: set[str] = set()
        for node in frontier:
            for neighbor in adjacency.get(node, set()):
                if neighbor not in reached:
                    reached[neighbor] = hop
                    next_frontier.add(neighbor)
        frontier = next_frontier
        if not frontier:
            break
    return reached


def entity_hit(entity: dict[str, Any], *, kind: str, query_lower: str, hop: int) -> GraphTraversalHit:
    label = str(entity.get("name") or entity.get("id") or kind)
    extra = " ".join(str(entity.get(key) or "") for key in ("status", "cost", "limit", "cooldown", "description"))
    return GraphTraversalHit(
        id=str(entity.get("id") or label),
        kind=kind,
        label=label,
        graph_score=round(graph_score(query_lower, f"{label} {extra}", hop), 6),
        hop_distance=hop,
        path_reason=f"{kind} metadata match",
        evidence_span=str(entity.get("evidence_span") or extra),
        source_path=str(entity.get("source_path") or ""),
        related_chapters=related_chapters(entity),
        payload=entity,
    )


def graph_score(query_lower: str, label: str, hop: int) -> float:
    label_lower = label.lower()
    overlap = sum(1 for term in query_terms(query_lower) if term in label_lower)
    return max(0.05, 1.0 / (1 + max(0, hop))) + overlap * 0.15


def relation_matches_query(query_lower: str, relation_label: str) -> bool:
    return any(term in query_lower for term in ("信任", "原谅", "关系", "背叛", "trust", "forgive", "relationship", "betray")) and relation_label


def event_matches_query(query_lower: str, event: dict[str, Any]) -> bool:
    haystack = json.dumps(event, ensure_ascii=False).lower()
    return any(term in haystack for term in query_terms(query_lower))


def query_requests_relationship(query_lower: str) -> bool:
    return any(term in query_lower for term in ("信任", "原谅", "关系", "背叛", "trust", "forgive", "relationship", "betray"))


def query_requests_character(query_lower: str) -> bool:
    return any(term in query_lower for term in ("她", "他", "人物", "character", "why"))


def query_requests_foreshadow(query_lower: str) -> bool:
    return any(term in query_lower for term in ("伏笔", "埋", "线索", "foreshadow", "clue"))


def query_requests_ability(query_lower: str) -> bool:
    return any(term in query_lower for term in ("能力", "这招", "不能用", "代价", "冷却", "ability", "cost", "cooldown", "limit"))


def query_terms(query_lower: str) -> list[str]:
    return [term for term in re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query_lower) if term]


def edge_active(edge: dict[str, Any], chapter_number: int) -> bool:
    start = as_int(edge.get("from_chapter")) or 1
    end = as_int(edge.get("to_chapter"))
    status = str(edge.get("status") or "active").lower()
    if status in {"stale", "expired"}:
        return False
    return start <= chapter_number and (not end or chapter_number <= end)


def event_visible(event: dict[str, Any], chapter_number: int) -> bool:
    number = as_int(event.get("chapter_number") or event.get("chapter") or event.get("from_chapter"))
    return not number or number <= chapter_number


def entity_visible(entity: dict[str, Any], chapter_number: int) -> bool:
    start = as_int(entity.get("from_chapter") or entity.get("chapter_number") or entity.get("chapter"))
    end = as_int(entity.get("to_chapter"))
    status = str(entity.get("status") or "active").lower()
    if status in {"stale", "expired"}:
        return False
    return (not start or start <= chapter_number) and (not end or chapter_number <= end)


def related_chapters(payload: dict[str, Any]) -> tuple[int, ...]:
    values = [as_int(payload.get("chapter_number") or payload.get("chapter")), as_int(payload.get("from_chapter")), as_int(payload.get("to_chapter"))]
    return tuple(item for item in values if item)


def entity_name(entity: dict[str, Any] | None, fallback: str) -> str:
    if not entity:
        return fallback
    return str(entity.get("name") or entity.get("id") or fallback)


def is_graph_stale(root: Path) -> bool:
    for path in (root / "30_state" / "graph_cascade_pending.json", root / "30_state" / "stale_indexes.json"):
        payload = read_json(path, default={})
        if isinstance(payload, dict) and (payload.get("stale") or payload.get("unsafe_continuation_blocker")):
            return True
    return False


def normalize_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "records", "data", "entities", "relationships", "events"):
            if isinstance(value.get(key), list):
                return value[key]
    return []


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def as_int(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default
