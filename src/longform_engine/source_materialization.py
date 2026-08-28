"""Materialize human-approved fanfiction Canon into project retrieval domains."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.storage import atomic_write_text


SOURCE_CANON_RELATIVE_PATH = "10_bible/fanfiction/source_canon.json"
SOURCE_CANON_CHUNK_RELATIVE_PATH = "60_rag/chunks/fanfiction_source_canon.json"
SOURCE_CANON_OWNER = "project_source_canon_semantic_v1"


def materialize_fanfiction_source_canon(root: Path, canon: dict[str, Any]) -> dict[str, Any]:
    """Replace only graph/RAG records owned by the current source Canon."""

    graph_path = root / "30_state" / "story_graph.json"
    graph = _read_json(graph_path, {"entities": [], "relationships": [], "events": []})
    if not isinstance(graph, dict):
        graph = {"entities": [], "relationships": [], "events": []}
    for field in ("entities", "relationships", "events"):
        values = graph.get(field)
        graph[field] = [
            item
            for item in (values if isinstance(values, list) else [])
            if isinstance(item, dict)
            if item.get("materialization_owner") != SOURCE_CANON_OWNER
        ]
    occupied_entity_ids = {
        str(item.get("id") or "") for item in graph["entities"] if item.get("id")
    }
    occupied_relationship_ids = {
        str(item.get("id") or "") for item in graph["relationships"] if item.get("id")
    }
    occupied_event_ids = {
        str(item.get("id") or "") for item in graph["events"] if item.get("id")
    }

    chunks: list[dict[str, Any]] = []
    entity_ids: set[str] = set()
    fact_count = 0
    evidence = {
        str(item.get("evidence_id") or ""): item
        for item in canon.get("evidence_references") or []
        if isinstance(item, dict)
    }
    for fact in canon.get("claims") or []:
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("claim_id") or "")
        if not fact_id:
            continue
        if fact_id in occupied_entity_ids or fact_id in entity_ids:
            raise ValueError(
                f"fanfiction source Canon claim ID collides with an existing graph entity: {fact_id}"
            )
        fact_count += 1
        entity_ids.add(fact_id)
        raw_attributes = fact.get("extensions")
        attributes: dict[str, Any] = raw_attributes if isinstance(raw_attributes, dict) else {}
        source_id = str(attributes.get("source_id") or "")
        fact_type = str(attributes.get("semantic_type") or "语义主张")
        fact_name = str(attributes.get("display_name") or fact.get("statement") or fact_id)[:160]
        fact_summary = str(fact.get("statement") or "")
        graph["entities"].append(
            {
                "id": fact_id,
                "name": fact_name,
                "type": _graph_entity_type(fact_type),
                "aliases": [],
                "description": fact_summary,
                "source_path": SOURCE_CANON_RELATIVE_PATH,
                "materialization_owner": SOURCE_CANON_OWNER,
                "source_id": source_id,
                "metadata": {
                    "fact_type": fact_type,
                    "attributes": attributes,
                    "evidence_refs": fact.get("evidence_refs") or [],
                    "applicability": fact.get("applicability"),
                    "uncertainty": fact.get("uncertainty", ""),
                },
            }
        )
        relation_source = str(attributes.get("source_entity_id") or "")
        relation_target = str(attributes.get("target_entity_id") or "")
        relation_type = str(attributes.get("relation_type") or "")
        if fact_type in {"relationship", "关系"} and all(
            (relation_source, relation_target, relation_type)
        ):
            relationship_id = f"relation:{fact_id}"
            if relationship_id in occupied_relationship_ids:
                raise ValueError(
                    "fanfiction source Canon relation ID collides with an existing graph "
                    f"relationship: {relationship_id}"
                )
            graph["relationships"].append(
                {
                    "id": relationship_id,
                    "source": relation_source,
                    "target": relation_target,
                    "type": relation_type,
                    "description": fact_summary,
                    "source_path": SOURCE_CANON_RELATIVE_PATH,
                    "materialization_owner": SOURCE_CANON_OWNER,
                    "fact_id": fact_id,
                }
            )
        if fact_type in {"event", "事件"}:
            event_id = f"source-event:{fact_id}"
            if event_id in occupied_event_ids:
                raise ValueError(
                    "fanfiction source Canon event ID collides with an existing graph event: "
                    f"{event_id}"
                )
            graph["events"].append(
                {
                    "id": event_id,
                    "title": fact_name,
                    "consequences": fact_summary,
                    "participants": [
                        str(item) for item in attributes.get("participant_ids") or []
                    ],
                    "source_path": SOURCE_CANON_RELATIVE_PATH,
                    "materialization_owner": SOURCE_CANON_OWNER,
                    "fact_id": fact_id,
                }
            )
        excerpts = [
                str(evidence.get(str(reference), {}).get("excerpt") or "")
                for reference in fact.get("evidence_refs") or []
        ]
        text = "\n".join(
            value
            for value in (
                fact_name,
                fact_summary,
                *[excerpt[:400] for excerpt in excerpts if excerpt],
            )
            if value
        )
        chunks.append(
            {
                "id": f"source-canon:{fact_id}",
                "chapter_number": None,
                "chunk_index": len(chunks),
                "title": fact_name,
                "text": text,
                "keywords": [fact_name, fact_type, source_id],
                "word_count": len(text),
                "token_estimate": max(1, len(text) // 2),
                "metadata": {
                    "canon": True,
                    "retrieval_domain": "project_canon",
                    "canon_type": SOURCE_CANON_OWNER,
                    "source": SOURCE_CANON_RELATIVE_PATH,
                    "source_sha256": sha256(
                        json.dumps(fact, ensure_ascii=False, sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                    "source_id": source_id,
                    "fact_id": fact_id,
                    "evidence_refs": fact.get("evidence_refs") or [],
                    "source_eligibility": "project_approved_fanfiction_canon",
                },
            }
        )
    graph["updated_at"] = datetime.now(timezone.utc).isoformat()
    atomic_write_text(graph_path, json.dumps(graph, ensure_ascii=False, indent=2) + "\n")
    chunk_path = root / SOURCE_CANON_CHUNK_RELATIVE_PATH
    chunk_payload = {
        "schema": "fanfiction_source_canon_rag_v1",
        "source_path": SOURCE_CANON_RELATIVE_PATH,
        "source_sha256": sha256(
            json.dumps(canon, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "chunks": chunks,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(chunk_path, json.dumps(chunk_payload, ensure_ascii=False, indent=2) + "\n")
    return {
        "facts": fact_count,
        "entities": len(entity_ids),
        "relationships": sum(
            item.get("materialization_owner") == SOURCE_CANON_OWNER
            for item in graph["relationships"]
            if isinstance(item, dict)
        ),
        "events": sum(
            item.get("materialization_owner") == SOURCE_CANON_OWNER
            for item in graph["events"]
            if isinstance(item, dict)
        ),
        "rag_chunks": len(chunks),
    }


def _graph_entity_type(value: str) -> str:
    aliases = {
        "人物": "character",
        "地点": "location",
        "组织": "organization",
        "能力": "ability",
        "物品": "item",
        "事件": "event",
        "关系": "relationship",
        "世界规则": "world_rule",
        "时间线": "timeline",
        "术语": "terminology",
        "未解决问题": "unresolved_question",
        "版本冲突": "version_conflict",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized else "canonical_fact"


def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


__all__ = [
    "SOURCE_CANON_CHUNK_RELATIVE_PATH",
    "SOURCE_CANON_OWNER",
    "SOURCE_CANON_RELATIVE_PATH",
    "materialize_fanfiction_source_canon",
]
