"""Compile approved fanfiction semantics into one chapter-scoped context bundle."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping
import unicodedata

from longform_engine.config import ConfigDocument, load_project_config
from longform_engine.chapter_contract import validate_chapter_contract
from longform_engine.fanfiction_contracts import (
    CurrentFanfictionDocuments,
    EVENT_CAUSAL_REFERENCE_FIELDS,
    FanfictionContractError,
    load_current_fanfiction_documents,
    load_current_fanfiction_route,
)
from longform_engine.fanfiction_divergence import realized_major_divergence_errors
from longform_engine.future_knowledge_provenance import (
    FutureKnowledgeProvenanceError,
    future_knowledge_pin_applies,
    future_knowledge_pin_for_approved,
    future_knowledge_pin_retains,
)
from longform_engine.future_knowledge_current import (
    FutureKnowledgeCurrentError,
    require_current_approved_future_knowledge,
)
from longform_engine.prompting import estimate_text_units, resolve_context_budget_contract
from longform_engine.rag import query as rag_query
from longform_engine.semantic_protocols import (
    build_workflow_record,
    validate_semantic_document,
)
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


FANFICTION_CONTEXT_BUNDLE_SCHEMA = "fanfiction_context_bundle_v2"


class FanfictionContextError(ValueError):
    """The formal fanfiction context cannot be compiled without semantic loss."""


MANDATORY_STORY_TYPES = frozenset(
    {
        "唯一初始变量",
        "独立长期目标",
        "可持续阻力",
        "原著人物自主性",
        "原作后续故事来源",
        "主角与原著关系",
        "读者识别承诺",
        "原创主线承诺",
    }
)

SEMANTIC_ELEMENT_ALIASES: dict[str, str] = {
    "人物": "characters",
    "character": "characters",
    "人物理解": "characters",
    "人物声音": "characters",
    "关系": "relationships",
    "relationship": "relationships",
    "世界规则": "world",
    "world_rule": "world",
    "地点": "world",
    "组织": "world",
    "能力": "abilities",
    "ability": "abilities",
    "能力规则": "abilities",
    "事件": "timeline",
    "event": "timeline",
    "时间线": "timeline",
}


def compile_fanfiction_context(
    config: ConfigDocument,
    *,
    chapter_number: int,
    chapter_contract: dict[str, Any],
    chapter_card: dict[str, Any],
    character_packet: dict[str, Any],
    write_rag_cache: bool = True,
) -> dict[str, Any]:
    """Compile one v2 chapter bundle by explicit semantic precedence.

    Selection is observable and fixed: global story promises/invariants, explicit
    chapter references, their recursive dependency closure, structured current
    scope, and finally optional RAG.  Chapter/range applicability alone never
    promotes every route claim into the bundle.
    """

    if chapter_number <= 0:
        raise FanfictionContextError("fanfiction context requires a positive chapter number")
    # This parameter remains in the author-context API, but is deliberately not a
    # semantic selector: the packet is transient and has no canonical persisted owner.
    _ = character_packet
    contract_errors = validate_chapter_contract(chapter_contract)
    if contract_errors:
        raise FanfictionContextError(
            "fanfiction_context_requires_current_chapter_contract_v5: "
            + "; ".join(contract_errors)
        )
    if chapter_contract.get("chapter_number") != chapter_number:
        raise FanfictionContextError("fanfiction_context_chapter_contract_mismatch")
    root = resolve_project_root(config)
    chapter_contract_path, persisted_contract, chapter_card_path, persisted_card = (
        _load_persisted_chapter_inputs(root, chapter_number)
    )
    if persisted_contract != chapter_contract:
        raise FanfictionContextError("fanfiction_context_chapter_contract_stale")
    if persisted_card != chapter_card:
        raise FanfictionContextError("fanfiction_context_chapter_card_stale")
    try:
        current = load_current_fanfiction_documents(config, root)
    except FanfictionContractError as exc:
        raise FanfictionContextError(str(exc)) from exc
    knowledge_documents, knowledge_paths, knowledge_sha256 = _current_future_knowledge_documents(
        config, root, target_chapter=chapter_number
    )
    paths = {**current.paths, **knowledge_paths}
    source_sha256 = {**current.sha256, **knowledge_sha256}
    documents = {
        "source_canon": current.source_canon,
        "story_engine": current.story_engine,
        "route_design": current.route,
        **knowledge_documents,
    }
    all_claims: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for namespace, document in documents.items():
        for raw_claim in document.get("claims") or []:
            if not isinstance(raw_claim, dict):
                continue
            claim_id = str(raw_claim.get("claim_id") or "")
            if not claim_id:
                continue
            if claim_id in all_claims:
                raise FanfictionContextError(f"duplicate fanfiction claim ID: {claim_id}")
            record = _claim_record(namespace, raw_claim, document)
            if namespace == "source_canon" and not _source_claim_allowed(config, record):
                conflicts.append(
                    {
                        "claim_id": claim_id,
                        "code": "outside_allowed_elements",
                        "message": "原著主张超出该来源在项目中批准的 allowed_elements。",
                    }
                )
                continue
            all_claims[claim_id] = record

    for update_claim in list(all_claims.values()):
        if update_claim.get("namespace", "").startswith("future_knowledge:"):
            knowledge_claim_id = str(
                update_claim.get("extensions", {}).get("knowledge_claim_id") or ""
            )
            knowledge_claim = all_claims.get(knowledge_claim_id)
            if knowledge_claim is None:
                raise FanfictionContextError(
                    f"future_knowledge_update_missing_base_claim:{knowledge_claim_id}"
                )
            if not _claim_applies(
                update_claim,
                chapter_number=chapter_number,
                chapter_card=chapter_card,
            ):
                continue
            knowledge_claim["dependency_edges"].append(
                {
                    "field": "approved_future_knowledge_update",
                    "claim_id": update_claim["claim_id"],
                }
            )
            knowledge_claim["dependency_claim_ids"] = _dedupe(
                [
                    *knowledge_claim.get("dependency_claim_ids", []),
                    update_claim["claim_id"],
                ]
            )

    explicit_ids, missing_explicit = _chapter_explicit_claim_references(
        chapter_contract=chapter_contract,
        known_ids=set(all_claims),
    )
    if missing_explicit:
        raise FanfictionContextError(
            "fanfiction_context_missing_claims: " + ", ".join(sorted(missing_explicit))
        )
    global_ids = {
        claim_id
        for claim_id, claim in all_claims.items()
        if (
            claim["namespace"] == "story_engine"
            and str(claim.get("semantic_type") or "") in MANDATORY_STORY_TYPES
        )
        or claim.get("extensions", {}).get("global_invariant") is True
    }
    current_scope = _current_scope(
        config,
        chapter_contract=chapter_contract,
        chapter_card=chapter_card,
    )
    explicit_id_set = set(explicit_ids)
    out_of_scope = {
        claim_id
        for claim_id in explicit_id_set
        if claim_id not in global_ids
        if not _claim_applies(
            all_claims[claim_id],
            chapter_number=chapter_number,
            chapter_card=chapter_card,
            current_scope=current_scope,
        )
    }
    if out_of_scope:
        raise FanfictionContextError(
            "fanfiction_context_claim_out_of_scope: " + ", ".join(sorted(out_of_scope))
        )
    required_ids = explicit_id_set
    closure_seed_ids = global_ids | required_ids
    closure_ids, dependency_edges = _dependency_closure(closure_seed_ids, all_claims)
    dependency_ids = closure_ids - closure_seed_ids
    dependency_out_of_scope = {
        claim_id
        for claim_id in dependency_ids
        if claim_id not in global_ids
        if not _claim_applies(
            all_claims[claim_id],
            chapter_number=chapter_number,
            chapter_card=chapter_card,
            current_scope=current_scope,
        )
    }
    if dependency_out_of_scope:
        raise FanfictionContextError(
            "fanfiction_context_dependency_out_of_scope: "
            + ", ".join(sorted(dependency_out_of_scope))
        )

    relevant_ids = {
        claim_id
        for claim_id, claim in all_claims.items()
        if claim_id not in closure_ids
        and _has_structured_scope(claim)
        and _claim_applies(
            claim,
            chapter_number=chapter_number,
            chapter_card=chapter_card,
            current_scope=current_scope,
        )
    }
    selection_reasons: dict[str, list[str]] = {}
    for claim_id in sorted(global_ids):
        selection_reasons.setdefault(claim_id, []).append(
            "global_story_promise"
            if all_claims[claim_id]["namespace"] == "story_engine"
            else "global_invariant"
        )
    for claim_id in sorted(required_ids):
        selection_reasons.setdefault(claim_id, []).append("chapter_explicit_ref")
    for edge in dependency_edges:
        selection_reasons.setdefault(edge["to_claim_id"], []).append(
            f"dependency:{edge['field']}:{edge['from_claim_id']}"
        )
    for claim_id in sorted(relevant_ids):
        selection_reasons.setdefault(claim_id, []).append("current_structured_scope")

    budget = resolve_context_budget_contract(root)
    bundle_budget = max(1_200, int(budget.capacity_units * 0.42))
    hard_ids = closure_ids | relevant_ids
    units_by_claim = {
        claim_id: _claim_units(all_claims[claim_id], budget.estimator)
        for claim_id in hard_ids
    }
    hard_units = sum(units_by_claim.values())
    if hard_units > bundle_budget:
        _raise_required_overflow(
            all_claims,
            units_by_claim=units_by_claim,
            budget_units=bundle_budget,
            used_units=hard_units,
        )

    optional_ids, retrieval_diagnostics = _optional_project_canon_claims(
        config,
        chapter_number=chapter_number,
        chapter_contract=chapter_contract,
        chapter_card=chapter_card,
        all_claims=all_claims,
        excluded_ids=hard_ids,
        token_budget=max(256, bundle_budget - hard_units),
        current_scope=current_scope,
        write_rag_cache=write_rag_cache,
    )
    included_ids = _dedupe([
        *sorted(global_ids),
        *explicit_ids,
        *sorted(dependency_ids),
        *sorted(relevant_ids),
    ])
    omitted: list[dict[str, Any]] = []
    used_units = hard_units
    included_optional_ids: list[str] = []
    for claim_id in optional_ids:
        record = all_claims[claim_id]
        units = _claim_units(record, budget.estimator)
        if used_units + units > bundle_budget:
            omitted.append(
                {
                    "claim_id": claim_id,
                    "reason": "optional_budget_omitted",
                    "required": False,
                    "units": units,
                }
            )
            continue
        included_ids.append(claim_id)
        included_optional_ids.append(claim_id)
        selection_reasons.setdefault(claim_id, []).append("optional_rag")
        units_by_claim[claim_id] = units
        used_units += units

    included = [all_claims[item] for item in included_ids]
    collisions = _namespace_collisions(included)
    partitions = _source_partitions(included)
    stale = _stale_diagnostics(documents, current.sha256)
    bundle: dict[str, Any] = {
        "schema": FANFICTION_CONTEXT_BUNDLE_SCHEMA,
        "chapter_number": chapter_number,
        "continuity_mode": str(
            config.data.get("fanfiction", {}).get("continuity_mode") or ""
        ),
        "source_files": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": source_sha256[name],
            }
            for name, path in paths.items()
        ],
        "required_claim_ids": explicit_ids,
        "global_claim_ids": sorted(global_ids),
        "dependency_claim_ids": sorted(dependency_ids),
        "relevant_claim_ids": sorted(relevant_ids),
        "dependency_closure": dependency_edges,
        "optional_claim_ids": included_optional_ids,
        "included_claim_ids": included_ids,
        "omitted_claims": omitted,
        "claims": included,
        "selection_reasons": {
            claim_id: _dedupe(reasons)
            for claim_id, reasons in sorted(selection_reasons.items())
            if claim_id in included_ids
        },
        "source_partitions": partitions,
        "namespace_collisions": collisions,
        "chapter_provenance": {
            "chapter_number": chapter_number,
            "chapter_contract_path": chapter_contract_path.relative_to(root).as_posix(),
            "chapter_contract_sha256": _canonical_json_hash(chapter_contract),
            "chapter_card_path": chapter_card_path.relative_to(root).as_posix(),
            "chapter_card_sha256": _canonical_json_hash(chapter_card),
        },
        "projection_inputs": {
            "show_source_labels": (
                len(config.data.get("fanfiction", {}).get("sources") or []) > 1
                or bool(collisions)
            ),
            "chapter_card": {
                "local_freedom": str(chapter_card.get("local_freedom") or ""),
                "observable_change": str(chapter_card.get("observable_change") or ""),
            },
            "chapter_claim_channel": dict(chapter_contract["fanfiction_claim_refs"]),
        },
        "author_projection": _author_projection(
            included,
            chapter_card,
            show_source_labels=(
                len(config.data.get("fanfiction", {}).get("sources") or []) > 1
                or bool(collisions)
            ),
        ),
        "review_projection": _review_projection(
            included,
            selection_reasons=selection_reasons,
            dependency_edges=dependency_edges,
            namespace_collisions=collisions,
        ),
        "budget_usage": _budget_usage(
            included,
            units_by_claim=units_by_claim,
            partitions=partitions,
            required_ids=required_ids,
            global_ids=global_ids,
            dependency_ids=dependency_ids,
            relevant_ids=relevant_ids,
            optional_ids=set(included_optional_ids),
            budget_units=bundle_budget,
            used_units=used_units,
            estimator=budget.estimator,
        ),
        "diagnostics": {
            "selection_precedence": [
                "global_non_negotiable",
                "chapter_explicit_refs",
                "recursive_dependency_closure",
                "current_structured_scope",
                "optional_rag",
            ],
            "global_contract": (
                "Only mandatory story promises and claims declaring global_invariant=true are "
                "global; chapter/range applicability alone never selects a route claim."
            ),
            "used_units": used_units,
            "budget_units": bundle_budget,
            "budget_profile": budget.profile,
            "estimator": budget.estimator,
            "retrieval": retrieval_diagnostics,
            "conflicts": conflicts,
            "stale": stale,
        },
    }
    bundle["bundle_sha256"] = _bundle_hash(bundle)
    return bundle


def write_fanfiction_context_bundle(root: Path, bundle: dict[str, Any]) -> Path:
    if bundle.get("schema") != FANFICTION_CONTEXT_BUNDLE_SCHEMA:
        raise FanfictionContextError(
            f"fanfiction context writer accepts {FANFICTION_CONTEXT_BUNDLE_SCHEMA} only"
        )
    chapter_number = int(bundle.get("chapter_number") or 0)
    if chapter_number <= 0:
        raise FanfictionContextError("fanfiction context writer requires a positive chapter number")
    bundle_errors = _validate_bundle_v2(bundle)
    if bundle_errors:
        raise FanfictionContextError("fanfiction_context_invalid: " + "; ".join(bundle_errors))
    project_config = root / "project.yaml"
    if project_config.is_file():
        _require_bundle_matches_persisted_inputs(load_project_config(project_config), bundle)
    elif _chapter_provenance_stale(root, bundle, chapter_number=chapter_number):
        raise FanfictionContextError("fanfiction_context_stale: persisted chapter inputs drifted")
    target = root / "50_workbench" / "fanfiction_context" / f"ch{chapter_number:03d}.json"
    atomic_write_text(target, json.dumps(bundle, ensure_ascii=False, indent=2) + "\n")
    return target


def fanfiction_context_status(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> dict[str, Any]:
    root = resolve_project_root(config)
    path = root / "50_workbench" / "fanfiction_context" / f"ch{chapter_number:03d}.json"
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != FANFICTION_CONTEXT_BUNDLE_SCHEMA:
        return {
            "schema": "fanfiction_context_status_v1",
            "chapter_number": chapter_number,
            "status": "missing",
            "bundle_path": path.relative_to(root).as_posix(),
            "found_schema": str(payload.get("schema") or "") if isinstance(payload, dict) else "",
            "required_schema": FANFICTION_CONTEXT_BUNDLE_SCHEMA,
            "next_command": "longform-engine production next project.yaml",
        }
    bundle_errors = _validate_bundle_v2(payload)
    if bundle_errors:
        return {
            "schema": "fanfiction_context_status_v1",
            "chapter_number": chapter_number,
            "status": "invalid",
            "bundle_path": path.relative_to(root).as_posix(),
            "bundle_sha256": str(payload.get("bundle_sha256") or ""),
            "required_claim_count": len(payload.get("required_claim_ids") or []),
            "included_claim_count": len(payload.get("included_claim_ids") or []),
            "omitted_claims": payload.get("omitted_claims") or [],
            "stale_sources": [],
            "diagnostics": {"bundle_errors": bundle_errors, "contract_errors": []},
        }
    bundle_diagnostics_value = payload.get("diagnostics")
    bundle_diagnostics: dict[str, Any] = (
        bundle_diagnostics_value if isinstance(bundle_diagnostics_value, dict) else {}
    )
    try:
        current = load_current_fanfiction_documents(config, root)
    except FanfictionContractError as exc:
        status = _contract_error_status(exc)
        return {
            "schema": "fanfiction_context_status_v1",
            "chapter_number": chapter_number,
            "status": status,
            "bundle_path": path.relative_to(root).as_posix(),
            "bundle_sha256": str(payload.get("bundle_sha256") or ""),
            "required_claim_count": len(payload.get("required_claim_ids") or []),
            "included_claim_count": len(payload.get("included_claim_ids") or []),
            "omitted_claims": payload.get("omitted_claims") or [],
            "stale_sources": [],
            "diagnostics": {
                **bundle_diagnostics,
                "contract_errors": [str(exc)],
            },
        }
    _documents, knowledge_paths, knowledge_sha256 = _current_future_knowledge_documents(
        config, root, target_chapter=chapter_number
    )
    stale_reasons = _bundle_stale_sources(
        root,
        payload,
        {**current.paths, **knowledge_paths},
        {**current.sha256, **knowledge_sha256},
    )
    stale_reasons = sorted(
        set(stale_reasons)
        | set(_chapter_provenance_stale(root, payload, chapter_number=chapter_number))
    )
    status = "stale" if stale_reasons else "current"
    return {
        "schema": "fanfiction_context_status_v1",
        "chapter_number": chapter_number,
        "status": status,
        "bundle_path": path.relative_to(root).as_posix(),
        "bundle_sha256": str(payload.get("bundle_sha256") or ""),
        "required_claim_count": len(payload.get("required_claim_ids") or []),
        "included_claim_count": len(payload.get("included_claim_ids") or []),
        "omitted_claims": payload.get("omitted_claims") or [],
        "stale_sources": stale_reasons,
        "diagnostics": {
            **bundle_diagnostics,
            "contract_errors": [],
        },
    }


def event_disposition_status(config: ConfigDocument) -> dict[str, Any]:
    root = resolve_project_root(config)
    diagnostics: list[str] = []
    route_status = "approved"
    try:
        route: dict[str, Any] | None = load_current_fanfiction_route(config, root)
    except FanfictionContractError as exc:
        route = None
        route_status = _contract_error_status(exc)
        diagnostics.append(str(exc))
    rows = []
    if isinstance(route, dict):
        for claim in route.get("claims") or []:
            if not isinstance(claim, dict):
                continue
            extensions_value = claim.get("extensions")
            extensions: dict[str, Any] = (
                extensions_value if isinstance(extensions_value, dict) else {}
            )
            if extensions.get("semantic_type") != "原著事件命运":
                continue
            rows.append(
                {
                    "claim_id": str(claim.get("claim_id") or ""),
                    "statement": str(claim.get("statement") or ""),
                    "disposition": str(extensions.get("disposition") or ""),
                    "depends_on_claims": list(extensions.get("depends_on_claims") or []),
                    "responsibility_owner_ids": list(
                        extensions.get("responsibility_owner_ids") or []
                    ),
                    "first_order_effect_claim_ids": list(
                        extensions.get("first_order_effect_claim_ids") or []
                    ),
                    "second_order_effect_claim_ids": list(
                        extensions.get("second_order_effect_claim_ids") or []
                    ),
                    "uncertainty": str(claim.get("uncertainty") or ""),
                }
            )
    return {
        "schema": "fanfiction_event_disposition_status_v1",
        "route_status": route_status,
        "events": rows,
        "pending_count": sum(item["disposition"] == "待决定" for item in rows),
        "diagnostics": diagnostics,
    }


def _contract_error_status(error: FanfictionContractError) -> str:
    return error.code if error.code in {"missing", "stale"} else "invalid"


def _validate_bundle_v2(payload: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_fields = {
        "schema",
        "chapter_number",
        "continuity_mode",
        "source_files",
        "global_claim_ids",
        "required_claim_ids",
        "dependency_claim_ids",
        "relevant_claim_ids",
        "dependency_closure",
        "optional_claim_ids",
        "included_claim_ids",
        "omitted_claims",
        "claims",
        "selection_reasons",
        "source_partitions",
        "namespace_collisions",
        "chapter_provenance",
        "projection_inputs",
        "author_projection",
        "review_projection",
        "budget_usage",
        "diagnostics",
        "bundle_sha256",
    }
    if set(payload) != expected_fields:
        missing = sorted(expected_fields - set(payload))
        extra = sorted(set(payload) - expected_fields)
        if missing:
            errors.append("bundle is missing fields: " + ", ".join(missing))
        if extra:
            errors.append("bundle has unexpected fields: " + ", ".join(extra))
    list_fields = (
        "source_files",
        "global_claim_ids",
        "required_claim_ids",
        "dependency_claim_ids",
        "relevant_claim_ids",
        "dependency_closure",
        "optional_claim_ids",
        "included_claim_ids",
        "omitted_claims",
        "claims",
        "namespace_collisions",
    )
    dict_fields = (
        "selection_reasons",
        "source_partitions",
        "chapter_provenance",
        "projection_inputs",
        "author_projection",
        "review_projection",
        "budget_usage",
        "diagnostics",
    )
    for field in list_fields:
        if not isinstance(payload.get(field), list):
            errors.append(f"{field} must be a list")
    for field in dict_fields:
        if not isinstance(payload.get(field), dict):
            errors.append(f"{field} must be an object")
    chapter = payload.get("chapter_number")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
        errors.append("chapter_number must be positive")
    provenance = payload.get("chapter_provenance")
    if not isinstance(provenance, dict) or set(provenance) != {
        "chapter_number",
        "chapter_contract_path",
        "chapter_contract_sha256",
        "chapter_card_path",
        "chapter_card_sha256",
    }:
        errors.append("chapter_provenance fields are invalid")
    elif provenance.get("chapter_number") != chapter:
        errors.append("chapter_provenance chapter_number does not match bundle")
    elif any(
        not _is_sha256(provenance.get(field))
        for field in (
            "chapter_contract_sha256",
            "chapter_card_sha256",
        )
    ):
        errors.append("chapter_provenance hashes must be SHA-256")
    elif any(
        not isinstance(provenance.get(field), str)
        for field in ("chapter_contract_path", "chapter_card_path")
    ):
        errors.append("chapter_provenance paths must be text")

    id_fields = (
        "global_claim_ids",
        "required_claim_ids",
        "dependency_claim_ids",
        "relevant_claim_ids",
        "optional_claim_ids",
        "included_claim_ids",
    )
    id_lists: dict[str, list[str]] = {}
    for field in id_fields:
        raw = payload.get(field)
        if not isinstance(raw, list):
            continue
        if any(not isinstance(item, str) or not item for item in raw):
            errors.append(f"{field} must contain stable non-empty IDs")
            continue
        if len(raw) != len(set(raw)):
            errors.append(f"{field} must not contain duplicates")
        id_lists[field] = list(raw)
    category_fields = id_fields[:-1]
    for index, left in enumerate(category_fields):
        for right in category_fields[index + 1 :]:
            overlap = set(id_lists.get(left, ())) & set(id_lists.get(right, ()))
            if overlap and {left, right} != {"global_claim_ids", "required_claim_ids"}:
                errors.append(f"{left} and {right} must be disjoint: {', '.join(sorted(overlap))}")
    expected_included = _dedupe([
        *id_lists.get("global_claim_ids", []),
        *id_lists.get("required_claim_ids", []),
        *id_lists.get("dependency_claim_ids", []),
        *id_lists.get("relevant_claim_ids", []),
        *id_lists.get("optional_claim_ids", []),
    ])
    if id_lists.get("included_claim_ids") != expected_included:
        errors.append("included_claim_ids must equal the ordered category union")

    claims_value = payload.get("claims")
    claims = claims_value if isinstance(claims_value, list) else []
    claim_ids = [
        str(item.get("claim_id") or "") for item in claims if isinstance(item, dict)
    ]
    if len(claim_ids) != len(claims) or claim_ids != id_lists.get("included_claim_ids"):
        errors.append("claims must match included_claim_ids in order")
    claims_by_id = {
        str(item.get("claim_id")): item
        for item in claims
        if isinstance(item, dict) and item.get("claim_id")
    }
    try:
        closure_ids, expected_edges = _dependency_closure(
            set(id_lists.get("global_claim_ids", ()))
            | set(id_lists.get("required_claim_ids", ())),
            claims_by_id,
        )
    except FanfictionContextError as exc:
        errors.append(str(exc))
        closure_ids, expected_edges = set(), []
    expected_dependency_ids = sorted(
        closure_ids
        - set(id_lists.get("global_claim_ids", ()))
        - set(id_lists.get("required_claim_ids", ()))
    )
    if id_lists.get("dependency_claim_ids") != expected_dependency_ids:
        errors.append("dependency_claim_ids do not match the recursive closure")
    if payload.get("dependency_closure") != expected_edges:
        errors.append("dependency_closure edges or reasons are not exact")

    expected_reasons: dict[str, list[str]] = {}
    for claim_id in id_lists.get("global_claim_ids", []):
        claim = claims_by_id.get(claim_id, {})
        expected_reasons[claim_id] = [
            "global_story_promise"
            if claim.get("namespace") == "story_engine"
            else "global_invariant"
        ]
    for claim_id in id_lists.get("required_claim_ids", []):
        expected_reasons.setdefault(claim_id, []).append("chapter_explicit_ref")
    for edge in expected_edges:
        expected_reasons.setdefault(edge["to_claim_id"], []).append(
            f"dependency:{edge['field']}:{edge['from_claim_id']}"
        )
    for claim_id in id_lists.get("relevant_claim_ids", []):
        expected_reasons.setdefault(claim_id, []).append("current_structured_scope")
    for claim_id in id_lists.get("optional_claim_ids", []):
        expected_reasons.setdefault(claim_id, []).append("optional_rag")
    expected_reasons = {
        claim_id: _dedupe(reasons) for claim_id, reasons in sorted(expected_reasons.items())
    }
    if payload.get("selection_reasons") != expected_reasons:
        errors.append("selection_reasons do not match semantic precedence")

    expected_partitions = _source_partitions(claims)
    if payload.get("source_partitions") != expected_partitions:
        errors.append("source_partitions are not an exact projection of selected claims")
    expected_collisions = _namespace_collisions(claims)
    if payload.get("namespace_collisions") != expected_collisions:
        errors.append("namespace_collisions are not an exact projection of selected claims")
    review = payload.get("review_projection")
    expected_review = _review_projection(
        claims,
        selection_reasons=expected_reasons,
        dependency_edges=expected_edges,
        namespace_collisions=expected_collisions,
    )
    if review != expected_review:
        errors.append("review_projection is not the exact selected claim/evidence projection")

    projection_inputs = payload.get("projection_inputs")
    if not isinstance(projection_inputs, dict) or set(projection_inputs) != {
        "show_source_labels",
        "chapter_card",
        "chapter_claim_channel",
    }:
        errors.append("projection_inputs fields are invalid")
    else:
        channel = projection_inputs.get("chapter_claim_channel")
        claim_channel_fields = {
            "schema",
            "active_volume_claim_refs",
            "semantic_obligation_claim_refs",
            "plot_node_claim_refs",
            "chapter_claim_refs",
            "all_claim_refs",
        }
        if not isinstance(channel, dict) or set(channel) != claim_channel_fields:
            errors.append("projection_inputs.chapter_claim_channel fields are invalid")
        else:
            claim_channel_list_fields = (
                "active_volume_claim_refs",
                "semantic_obligation_claim_refs",
                "plot_node_claim_refs",
                "chapter_claim_refs",
                "all_claim_refs",
            )
            for field in claim_channel_list_fields:
                values = channel.get(field)
                if (
                    not isinstance(values, list)
                    or any(not isinstance(item, str) or not item.strip() for item in values)
                    or len(values) != len(set(values or []))
                ):
                    errors.append(
                        f"projection_inputs.chapter_claim_channel.{field} must be a unique string list"
                    )
            contributors = [
                *list(channel.get("active_volume_claim_refs") or []),
                *list(channel.get("semantic_obligation_claim_refs") or []),
                *list(channel.get("plot_node_claim_refs") or []),
                *list(channel.get("chapter_claim_refs") or []),
            ]
            if channel.get("schema") != "fanfiction_chapter_claim_channel_v1":
                errors.append("projection_inputs.chapter_claim_channel schema is invalid")
            if channel.get("all_claim_refs") != _dedupe(contributors):
                errors.append("projection_inputs.chapter_claim_channel union is invalid")
            if id_lists.get("required_claim_ids") != channel.get("all_claim_refs"):
                errors.append("required_claim_ids must equal the formal chapter claim channel")
        projection_card = projection_inputs.get("chapter_card")
        if not isinstance(projection_card, dict) or set(projection_card) != {
            "local_freedom",
            "observable_change",
        }:
            errors.append("projection_inputs.chapter_card fields are invalid")
        elif not isinstance(projection_inputs.get("show_source_labels"), bool):
            errors.append("projection_inputs.show_source_labels must be boolean")
        else:
            expected_author = _author_projection(
                claims,
                projection_card,
                show_source_labels=projection_inputs["show_source_labels"],
            )
            if payload.get("author_projection") != expected_author:
                errors.append("author_projection is not exact or has incorrect source labels")

    budget = payload.get("budget_usage")
    if isinstance(budget, dict):
        estimator = budget.get("estimator")
        units_by_claim = {
            claim_id: _claim_units(claims_by_id[claim_id], estimator)
            for claim_id in claim_ids
            if claim_id in claims_by_id
        }
        expected_budget = _budget_usage(
            claims,
            units_by_claim=units_by_claim,
            partitions=expected_partitions,
            global_ids=set(id_lists.get("global_claim_ids", ())),
            required_ids=set(id_lists.get("required_claim_ids", ())),
            dependency_ids=set(id_lists.get("dependency_claim_ids", ())),
            relevant_ids=set(id_lists.get("relevant_claim_ids", ())),
            optional_ids=set(id_lists.get("optional_claim_ids", ())),
            budget_units=int(budget.get("budget_units") or 0),
            used_units=sum(units_by_claim.values()),
            estimator=estimator,
        )
        if budget != expected_budget:
            errors.append("budget_usage totals, categories, or partitions are not exact")
    source_files = payload.get("source_files")
    if isinstance(source_files, list):
        paths: list[str] = []
        for index, item in enumerate(source_files):
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "sha256"}
                or not isinstance(item.get("path"), str)
                or not item["path"]
                or not _is_sha256(item.get("sha256"))
            ):
                errors.append(f"source_files[{index}] is invalid")
                continue
            paths.append(item["path"])
        if len(paths) != len(set(paths)):
            errors.append("source_files paths must be unique")
    if not isinstance(payload.get("bundle_sha256"), str) or not payload.get("bundle_sha256"):
        errors.append("bundle_sha256 must be non-empty")
    elif payload.get("bundle_sha256") != _bundle_hash(dict(payload)):
        errors.append("bundle_sha256 is stale")
    return errors


def _bundle_stale_sources(
    root: Path,
    payload: Mapping[str, Any],
    current_paths: Mapping[str, Path],
    current_sha256: Mapping[str, str],
) -> list[str]:
    expected_sources = {
        source_path.relative_to(root).as_posix(): current_sha256[name]
        for name, source_path in current_paths.items()
    }
    declared_sources: dict[str, str] = {}
    malformed_sources: list[str] = []
    source_files_value = payload.get("source_files")
    source_files: list[Any] = source_files_value if isinstance(source_files_value, list) else []
    for index, item in enumerate(source_files):
        if not isinstance(item, dict):
            malformed_sources.append(f"source_files[{index}]")
            continue
        source_path = item.get("path")
        digest = item.get("sha256")
        if not isinstance(source_path, str) or not source_path or not isinstance(digest, str):
            malformed_sources.append(f"source_files[{index}]")
            continue
        if source_path in declared_sources:
            malformed_sources.append(source_path)
            continue
        declared_sources[source_path] = digest
    stale_reasons = set(malformed_sources) | {
        source_path
        for source_path in set(expected_sources) | set(declared_sources)
        if expected_sources.get(source_path) != declared_sources.get(source_path)
    }
    if payload.get("bundle_sha256") != _bundle_hash(dict(payload)):
        stale_reasons.add("bundle_sha256")
    return sorted(stale_reasons)


def _chapter_provenance_stale(
    root: Path,
    payload: Mapping[str, Any],
    *,
    chapter_number: int,
) -> list[str]:
    provenance = payload.get("chapter_provenance")
    if not isinstance(provenance, dict):
        return ["chapter_provenance"]
    stale: list[str] = []
    expected = {
        "chapter_contract": (
            root / "20_outline" / "chapter_contracts" / f"ch{chapter_number:03d}.json"
        ),
        "chapter_card": (
            root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
        ),
    }
    for kind, path in expected.items():
        declared_path = str(provenance.get(f"{kind}_path") or "")
        expected_relative = path.relative_to(root).as_posix()
        if path.is_file():
            if declared_path != expected_relative:
                stale.append(f"{kind}_path")
                continue
            current = _read_json(path)
            if not isinstance(current, dict):
                stale.append(kind)
                continue
            if kind == "chapter_contract":
                current = {
                    key: value
                    for key, value in current.items()
                    if key != "chapter_contract_hash"
                }
            if provenance.get(f"{kind}_sha256") != _canonical_json_hash(current):
                stale.append(kind)
        else:
            stale.append(f"{kind}_path")
    return stale


def _load_persisted_chapter_inputs(
    root: Path,
    chapter_number: int,
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    contract_path = root / "20_outline" / "chapter_contracts" / f"ch{chapter_number:03d}.json"
    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    if not contract_path.is_file():
        raise FanfictionContextError("fanfiction_context_chapter_contract_missing")
    if not card_path.is_file():
        raise FanfictionContextError("fanfiction_context_chapter_card_missing")
    raw_contract = _read_json(contract_path)
    card = _read_json(card_path)
    if not isinstance(raw_contract, dict):
        raise FanfictionContextError("fanfiction_context_chapter_contract_unreadable")
    if not isinstance(card, dict):
        raise FanfictionContextError("fanfiction_context_chapter_card_unreadable")
    contract = {key: value for key, value in raw_contract.items() if key != "chapter_contract_hash"}
    contract_errors = validate_chapter_contract(contract)
    if contract_errors:
        raise FanfictionContextError(
            "fanfiction_context_chapter_contract_invalid: " + "; ".join(contract_errors)
        )
    if contract.get("chapter_number") != chapter_number:
        raise FanfictionContextError("fanfiction_context_chapter_contract_mismatch")
    return contract_path, contract, card_path, card


def _require_bundle_matches_persisted_inputs(
    config: ConfigDocument,
    payload: Mapping[str, Any],
) -> None:
    chapter_number = int(payload.get("chapter_number") or 0)
    root = resolve_project_root(config)
    _contract_path, contract, _card_path, card = _load_persisted_chapter_inputs(
        root, chapter_number
    )
    expected = compile_fanfiction_context(
        config,
        chapter_number=chapter_number,
        chapter_contract=contract,
        chapter_card=card,
        character_packet={},
        write_rag_cache=False,
    )
    volatile = {"bundle_sha256", "diagnostics"}
    if any(
        payload.get(key) != expected.get(key)
        for key in (set(payload) | set(expected)) - volatile
    ):
        divergent = sorted(
            key
            for key in set(payload) | set(expected)
            if key not in volatile and payload.get(key) != expected.get(key)
        )
        raise FanfictionContextError(
            "fanfiction_context_stale: deterministic persisted-input recompilation differs: "
            + ", ".join(divergent)
        )


def require_current_fanfiction_context_bundle(
    config: ConfigDocument,
    *,
    chapter_number: int,
    current_documents: CurrentFanfictionDocuments | None = None,
) -> tuple[Path, dict[str, Any]]:
    """Load the only supported v2 bundle and revalidate its complete source chain."""

    root = resolve_project_root(config)
    if current_documents is None:
        try:
            current = load_current_fanfiction_documents(config, root)
        except FanfictionContractError as exc:
            raise FanfictionContextError(str(exc)) from exc
    else:
        current = current_documents
    path = root / "50_workbench" / "fanfiction_context" / f"ch{chapter_number:03d}.json"
    payload = _read_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != FANFICTION_CONTEXT_BUNDLE_SCHEMA:
        found = str(payload.get("schema") or "") if isinstance(payload, dict) else ""
        raise FanfictionContextError(
            "fanfiction_context_missing: current chapter requires "
            f"{FANFICTION_CONTEXT_BUNDLE_SCHEMA}; found {found or 'no readable bundle'}"
        )
    if payload.get("chapter_number") != chapter_number:
        raise FanfictionContextError("fanfiction_context_invalid: requested chapter mismatch")
    bundle_errors = _validate_bundle_v2(payload)
    if bundle_errors:
        raise FanfictionContextError("fanfiction_context_invalid: " + "; ".join(bundle_errors))
    knowledge_documents, knowledge_paths, knowledge_sha256 = _current_future_knowledge_documents(
        config, root, target_chapter=chapter_number
    )
    stale_sources = _bundle_stale_sources(
        root,
        payload,
        {**current.paths, **knowledge_paths},
        {**current.sha256, **knowledge_sha256},
    )
    if stale_sources:
        raise FanfictionContextError(
            "fanfiction_context_stale: "
            + ", ".join(stale_sources)
        )
    provenance_stale = _chapter_provenance_stale(
        root,
        payload,
        chapter_number=chapter_number,
    )
    if provenance_stale:
        raise FanfictionContextError(
            "fanfiction_context_stale: " + ", ".join(provenance_stale)
        )
    _require_bundle_matches_persisted_inputs(config, payload)
    return path, payload


def future_knowledge_impact_workflows(
    config: ConfigDocument,
    *,
    chapter_number: int,
    event_ledger: dict[str, Any],
    event_ledger_path: Path,
) -> tuple[tuple[Path, dict[str, Any]], ...]:
    """Build one idempotent human workflow for every realized major divergence trigger."""

    root = resolve_project_root(config)
    if str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        return ()
    event_ledger_path = event_ledger_path.resolve()
    try:
        event_ledger_path.relative_to(root.resolve())
    except ValueError as exc:
        raise FanfictionContextError("event ledger path must stay inside the project") from exc
    expected_event_path = (
        root / "30_state" / "narrative_events" / f"ch{chapter_number:03d}.json"
    ).resolve()
    if event_ledger_path != expected_event_path:
        raise FanfictionContextError("future_knowledge_event_ledger_path_is_not_canonical")
    disk_event_ledger = _read_json(event_ledger_path)
    if disk_event_ledger != event_ledger:
        raise FanfictionContextError("future_knowledge_event_ledger_stale: payload differs from disk")
    if (event_ledger.get("events") or []) and not str(
        event_ledger.get("realization_application_sha256") or ""
    ):
        raise FanfictionContextError(
            "future_knowledge_event_ledger_not_human_approved: "
            "realization_application_sha256 is required"
        )
    directory = root / "50_workbench" / "fanfiction_knowledge_impacts"
    bundle_path, bundle = require_current_fanfiction_context_bundle(
        config,
        chapter_number=chapter_number,
    )
    claims = {
        str(item.get("claim_id") or ""): item
        for item in bundle.get("review_projection", {}).get("claims") or []
        if isinstance(item, dict) and item.get("claim_id")
    }
    final_path = manuscript_chapter_path(root, chapter_number, lane="final")
    semantic_path = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    trigger_errors = realized_major_divergence_errors(
        root=root,
        chapter_number=chapter_number,
        divergences=event_ledger.get("realized_major_divergences"),
        event_payload=event_ledger,
        review_claims=claims,
        final_path=final_path,
        semantic_path=semantic_path,
        context_path=bundle_path,
        require_stored_bindings=True,
    )
    if trigger_errors:
        raise FanfictionContextError(
            "future_knowledge_trigger_invalid:" + ";".join(trigger_errors)
        )
    triggers: list[dict[str, Any]] = []
    for raw_trigger in event_ledger.get("realized_major_divergences") or []:
        if not isinstance(raw_trigger, dict):
            continue
        trigger = dict(raw_trigger)
        claim = claims.get(str(trigger.get("source_claim_id") or ""))
        if claim is None:
            raise FanfictionContextError(
                "future_knowledge_trigger_claim_missing:"
                + str(trigger.get("source_claim_id") or "")
            )
        semantic_type = str(claim.get("semantic_type") or "")
        disposition = str(claim.get("extensions", {}).get("disposition") or "")
        if semantic_type != "初始分歧" and not (
            semantic_type == "原著事件命运" and disposition not in {"", "保留"}
        ):
            raise FanfictionContextError(
                "future_knowledge_trigger_is_not_major_divergence:"
                + str(trigger.get("source_claim_id") or "")
            )
        if trigger.get("realized_chapter") != chapter_number or trigger.get(
            "impact_level"
        ) != "major":
            raise FanfictionContextError("future_knowledge_trigger_scope_invalid")
        confirmation = trigger.get("human_confirmation")
        if not isinstance(confirmation, dict) or confirmation.get("confirmed_by") != "human":
            raise FanfictionContextError("future_knowledge_trigger_not_human_confirmed")
        knowledge_scope_refs = trigger.get("knowledge_scope_refs")
        if not isinstance(knowledge_scope_refs, list) or not knowledge_scope_refs:
            raise FanfictionContextError("future_knowledge_scope_missing")
        for knowledge_claim_id in knowledge_scope_refs:
            knowledge_claim = claims.get(str(knowledge_claim_id))
            if knowledge_claim is None or knowledge_claim.get("semantic_type") not in {
                "人物知识边界",
                "人物阶段与知识边界",
                "未来知识可靠性",
            }:
                raise FanfictionContextError(
                    f"future_knowledge_scope_claim_invalid:{knowledge_claim_id}"
                )
        triggers.append(trigger)
    if not triggers:
        return ()
    bundle_hash = sha256(bundle_path.read_bytes()).hexdigest()
    event_hash = sha256(event_ledger_path.read_bytes()).hexdigest()
    workflows: list[tuple[Path, dict[str, Any]]] = []
    seen_trigger_ids: set[str] = set()
    for trigger in triggers:
        trigger_id = str(trigger.get("trigger_id") or "")
        if not trigger_id:
            raise FanfictionContextError("future_knowledge_trigger_id_missing")
        if trigger_id in seen_trigger_ids:
            raise FanfictionContextError(f"future_knowledge_trigger_id_duplicate:{trigger_id}")
        seen_trigger_ids.add(trigger_id)
        # File/workflow identity is intentionally derived from the formal stable trigger ID,
        # not from the mutable evidence or confirmation record.  Any later record drift then
        # resolves to this same path and is reported as stale instead of creating a second task.
        trigger_identity = sha256(trigger_id.encode("utf-8")).hexdigest()
        target = directory / f"ch{chapter_number:03d}.{trigger_identity[:16]}.workflow.json"
        workflow_id = f"future_knowledge_impact_ch{chapter_number:03d}_{trigger_identity[:16]}"
        if target.exists():
            existing = _read_json(target)
            existing_trigger = (
                existing.get("extensions", {}).get("trigger")
                if isinstance(existing, dict)
                else None
            )
            inputs = existing.get("inputs") if isinstance(existing, dict) else []
            bound = {
                str(item.get("kind") or ""): str(item.get("sha256") or "")
                for item in inputs or []
                if isinstance(item, dict)
            }
            if (
                existing.get("workflow_id") == workflow_id
                and existing_trigger == trigger
                and bound.get("fanfiction_context_bundle") == bundle_hash
                and bound.get("narrative_event_ledger") == event_hash
            ):
                continue
            raise FanfictionContextError(
                f"future_knowledge_workflow_stale:{target.relative_to(root).as_posix()}"
            )
        workflow = build_workflow_record(
            workflow_id=workflow_id,
            workflow_kind="fanfiction_future_knowledge_impact",
            scope={"kind": "chapter", "chapter_number": chapter_number},
            state="awaiting_human",
            inputs=[
                {
                    "path": bundle_path.relative_to(root).as_posix(),
                    "sha256": bundle_hash,
                    "kind": "fanfiction_context_bundle",
                },
                {
                    "path": event_ledger_path.relative_to(root).as_posix(),
                    "sha256": event_hash,
                    "kind": "narrative_event_ledger",
                },
            ],
            outputs=[],
            authorization={"canonical_mutation": False, "requires_human_decision": True},
            diagnostics=[
                {
                    "code": "major_divergence_realized",
                    "trigger_claim_ids": [trigger["source_claim_id"]],
                    "source_event_id": trigger["source_event_id"],
                    "knowledge_claim_ids": trigger["knowledge_scope_refs"],
                }
            ],
            extensions={
                "trigger": trigger,
                "knowledge_scope_refs": trigger["knowledge_scope_refs"],
                "allowed_reliability_states": ["仍可靠", "部分可靠", "已失效", "反向误导"],
                "instruction": (
                    "由独立语义任务评估每条未来知识在本次分歧后的可靠性；只生成候选。"
                    "结果必须经过人工批准并通过既有同人路线/知识语义 apply，才可进入后续章节依赖。"
                ),
                "next_command": "longform-engine fanfiction design-task project.yaml",
            },
        )
        workflows.append((target, workflow))
    return tuple(workflows)


def _claim_record(
    namespace: str,
    claim: dict[str, Any],
    document: Mapping[str, Any],
) -> dict[str, Any]:
    extensions_value = claim.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    dependency_edges: list[dict[str, str]] = []
    for field in ("depends_on_claims", *EVENT_CAUSAL_REFERENCE_FIELDS):
        values = extensions.get(field)
        if isinstance(values, list):
            dependency_edges.extend(
                {"field": field, "claim_id": item}
                for item in values
                if isinstance(item, str) and item
            )
    depends_on_value = extensions.get("depends_on_claims")
    depends_on_claims: list[Any] = (
        depends_on_value if isinstance(depends_on_value, list) else []
    )
    evidence_refs = [
        item for item in claim.get("evidence_refs") or [] if isinstance(item, str) and item
    ]
    evidence_by_id = {
        str(item.get("evidence_id") or ""): item
        for item in document.get("evidence_references") or []
        if isinstance(item, dict) and item.get("evidence_id")
    }
    return {
        "claim_id": str(claim.get("claim_id") or ""),
        "namespace": namespace,
        "source_id": str(
            extensions.get("source_id")
            or (
                extensions.get("identity", {}).get("source_id")
                if isinstance(extensions.get("identity"), dict)
                else ""
            )
            or ""
        ),
        "semantic_type": str(extensions.get("semantic_type") or "语义主张"),
        "statement": str(claim.get("statement") or ""),
        "applicability": claim.get("applicability"),
        "uncertainty": str(claim.get("uncertainty") or ""),
        "depends_on_claims": [
            item
            for item in depends_on_claims
            if isinstance(item, str) and item
        ],
        "dependency_claim_ids": _dedupe(item["claim_id"] for item in dependency_edges),
        "dependency_edges": dependency_edges,
        "evidence_refs": evidence_refs,
        "evidence_records": [evidence_by_id[item] for item in evidence_refs if item in evidence_by_id],
        "extensions": extensions,
    }


def _source_claim_allowed(config: ConfigDocument, claim: dict[str, Any]) -> bool:
    source_id = str(claim.get("source_id") or "")
    sources = config.data.get("fanfiction", {}).get("sources") or []
    source = next(
        (item for item in sources if isinstance(item, dict) and item.get("source_id") == source_id),
        None,
    )
    if source is None:
        return False
    semantic_type = str(claim.get("semantic_type") or "")
    element = SEMANTIC_ELEMENT_ALIASES.get(semantic_type)
    allowed = {str(item).strip().lower() for item in source.get("allowed_elements") or []}
    if "*" in allowed or "all" in allowed:
        return True
    if element is None:
        return bool(semantic_type) and semantic_type.lower() in allowed
    return element.lower() in allowed or semantic_type.lower() in allowed


def _chapter_explicit_claim_references(
    *,
    chapter_contract: Mapping[str, Any],
    known_ids: set[str],
) -> tuple[list[str], set[str]]:
    references: list[str] = []
    missing: set[str] = set()
    channel = chapter_contract.get("fanfiction_claim_refs")
    candidates: list[Any] = []
    if isinstance(channel, dict):
        candidates.extend(channel.get("all_claim_refs") or [])
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate.strip():
            continue
        if candidate in known_ids:
            if candidate not in references:
                references.append(candidate)
        else:
            missing.add(candidate)
    return references, missing


def _dependency_closure(
    seed: set[str],
    claims: dict[str, dict[str, Any]],
) -> tuple[set[str], list[dict[str, str]]]:
    closure = set(seed)
    pending = list(seed)
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    while pending:
        claim_id = pending.pop()
        claim = claims.get(claim_id)
        if claim is None:
            continue
        for dependency_edge in claim.get("dependency_edges") or []:
            dependency = str(dependency_edge.get("claim_id") or "")
            field = str(dependency_edge.get("field") or "depends_on_claims")
            if dependency not in claims:
                raise FanfictionContextError(
                    f"fanfiction_context_missing_dependency:{claim_id}->{dependency}"
                )
            edge_key = (claim_id, dependency, field)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges.append(
                    {
                        "from_claim_id": claim_id,
                        "to_claim_id": dependency,
                        "field": field,
                        "reason": f"{claim_id}.{field} requires {dependency}",
                    }
                )
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure, sorted(
        edges,
        key=lambda item: (item["from_claim_id"], item["field"], item["to_claim_id"]),
    )


def _claim_applies(
    claim: dict[str, Any],
    *,
    chapter_number: int,
    chapter_card: dict[str, Any],
    current_scope: Mapping[str, set[str]] | None = None,
) -> bool:
    extensions_value = claim.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    chapters = extensions.get("chapter_numbers")
    if isinstance(chapters, list) and chapters:
        if chapter_number not in {int(item) for item in chapters if str(item).isdigit()}:
            return False
    knowledge_range = extensions.get("knowledge_range")
    knowledge_range = knowledge_range if isinstance(knowledge_range, dict) else {}
    start = int(extensions.get("from_chapter") or knowledge_range.get("from_chapter") or 0)
    end = int(extensions.get("to_chapter") or knowledge_range.get("to_chapter") or 0)
    if start and chapter_number < start:
        return False
    if end and chapter_number > end:
        return False
    volume_ids = {str(item) for item in extensions.get("volume_ids") or []}
    if volume_ids and str(chapter_card.get("volume_id") or "") not in volume_ids:
        return False
    arc_ids = {str(item) for item in extensions.get("arc_ids") or []}
    if arc_ids and str(chapter_card.get("arc_id") or "") not in arc_ids:
        return False
    if current_scope is not None:
        for field, scope_key in (
            ("source_ids", "source"),
            ("character_ids", "character"),
            ("event_ids", "event"),
            ("volume_ids", "volume"),
            ("arc_ids", "arc"),
        ):
            declared = {str(item) for item in extensions.get(field) or [] if str(item)}
            active = current_scope.get(scope_key, set())
            if declared and (not active or declared.isdisjoint(active)):
                return False
    return True


def _current_scope(
    config: ConfigDocument,
    *,
    chapter_contract: Mapping[str, Any],
    chapter_card: Mapping[str, Any],
) -> dict[str, set[str]]:
    # Character-expression packets are transient author aids and have no canonical
    # persisted owner.  They therefore cannot influence semantic claim selection.
    values = (chapter_contract, chapter_card)

    def collect(*fields: str) -> set[str]:
        result: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in fields:
                        candidates = child if isinstance(child, list) else [child]
                        result.update(
                            str(item)
                            for item in candidates
                            if isinstance(item, (str, int)) and str(item).strip()
                        )
                    else:
                        walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        for value in values:
            walk(value)
        return result

    configured_sources = {
        str(item.get("source_id") or "")
        for item in config.data.get("fanfiction", {}).get("sources") or []
        if isinstance(item, dict) and item.get("source_id")
    }
    source_scope = collect("source_id", "source_ids")
    if not source_scope and len(configured_sources) == 1:
        source_scope = configured_sources
    return {
        "source": source_scope,
        "character": collect(
            "character_id", "character_ids", "featured_character_ids", "pov_character_id"
        ),
        "event": collect("event_id", "event_ids", "source_event_id", "source_event_ids"),
        "volume": collect("volume_id", "volume_ids"),
        "arc": collect("arc_id", "arc_ids"),
    }


def _has_structured_scope(claim: Mapping[str, Any]) -> bool:
    extensions = claim.get("extensions")
    if not isinstance(extensions, dict):
        return False
    return any(
        isinstance(extensions.get(field), list) and bool(extensions.get(field))
        for field in ("source_ids", "character_ids", "event_ids", "volume_ids", "arc_ids")
    )


def _raise_required_overflow(
    claims: Mapping[str, Mapping[str, Any]],
    *,
    units_by_claim: Mapping[str, int],
    budget_units: int,
    used_units: int,
) -> None:
    contributors: list[dict[str, Any]] = [
        {
                "claim_id": claim_id,
                "units": units,
                "namespace": str(claims[claim_id].get("namespace") or ""),
                "source_id": str(claims[claim_id].get("source_id") or ""),
                "partitions": {
                    kind: _claim_partition_values(claims[claim_id], kind)
                    for kind in ("source", "character", "event", "volume", "arc")
                },
        }
        for claim_id, units in units_by_claim.items()
    ]
    contributors.sort(key=lambda item: (-int(str(item["units"])), str(item["claim_id"])))
    contributors = contributors[:8]
    raise FanfictionContextError(
        "prompt_budget_exceeded: required/global/explicit/dependency/current-scope fanfiction "
        f"evidence uses {used_units} units but budget is {budget_units}; "
        f"top_contributors={json.dumps(contributors, ensure_ascii=False, separators=(',', ':'))}; "
        "scope-reduction suggestions: remove unrelated explicit refs, narrow character/event/volume/arc/source "
        "scope, or split the chapter task. Required evidence was not truncated and no artifact was written."
    )


def _claim_partition_values(claim: Mapping[str, Any], kind: str) -> list[str]:
    extensions = claim.get("extensions")
    extensions = extensions if isinstance(extensions, dict) else {}
    if kind == "source":
        values = [claim.get("source_id"), *(extensions.get("source_ids") or [])]
        if not any(str(item or "").strip() for item in values):
            values = [claim.get("namespace")]
    else:
        fields = {
            "character": ("character_id", "character_ids"),
            "event": ("event_id", "event_ids", "source_event_id", "source_event_ids"),
            "volume": ("volume_id", "volume_ids"),
            "arc": ("arc_id", "arc_ids"),
        }[kind]
        values = []
        for field in fields:
            raw = extensions.get(field)
            values.extend(raw if isinstance(raw, list) else [raw])
        identity = extensions.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        identity_kind = str(identity.get("kind") or "")
        if kind == "character" and identity_kind == "character":
            values.append(identity.get("identity_id") or identity.get("display_name"))
        if kind == "event" and claim.get("semantic_type") == "原著事件命运":
            values.append(claim.get("claim_id"))
    return _dedupe(str(item) for item in values if isinstance(item, (str, int)) and str(item))


def _source_partitions(claims: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {
        "source": {},
        "character": {},
        "event": {},
        "volume": {},
        "arc": {},
    }
    for claim in claims:
        claim_id = str(claim.get("claim_id") or "")
        for kind in result:
            for value in _claim_partition_values(claim, kind):
                result[kind].setdefault(value, []).append(claim_id)
    return {
        kind: {key: _dedupe(ids) for key, ids in sorted(values.items())}
        for kind, values in result.items()
    }


def _normalized_identity_name(value: str, *, kind: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = normalized.replace("炁", "气")
    normalized = re.sub(r"[^\w\u3400-\u9fff]+", "", normalized)
    if kind == "energy":
        normalized = normalized.removesuffix("energy")
    return normalized


def _namespace_collisions(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, str]]] = {}
    allowed_kinds = {"character", "ability", "location", "organization", "energy"}
    for claim in claims:
        extensions = claim.get("extensions")
        extensions = extensions if isinstance(extensions, dict) else {}
        identity = extensions.get("identity")
        identity = identity if isinstance(identity, dict) else {}
        kind = str(identity.get("kind") or "")
        name = str(identity.get("display_name") or "")
        source_id = str(claim.get("source_id") or identity.get("source_id") or "")
        if kind not in allowed_kinds or not name or not source_id:
            continue
        key = (kind, _normalized_identity_name(name, kind=kind))
        groups.setdefault(key, []).append(
            {"claim_id": str(claim.get("claim_id") or ""), "source_id": source_id, "name": name}
        )
    collisions: list[dict[str, Any]] = []
    for (kind, normalized_name), records in sorted(groups.items()):
        if len({item["source_id"] for item in records}) < 2:
            continue
        collisions.append(
            {
                "kind": kind,
                "normalized_name": normalized_name,
                "records": records,
                "reason": (
                    "normalized_energy_term_collision"
                    if kind == "energy"
                    else "same_display_name_across_sources"
                ),
            }
        )
    return collisions


def _review_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": str(claim.get("claim_id") or ""),
        "namespace": str(claim.get("namespace") or ""),
        "source_id": str(claim.get("source_id") or ""),
        "semantic_type": str(claim.get("semantic_type") or ""),
        "statement": str(claim.get("statement") or ""),
        "applicability": claim.get("applicability"),
        "uncertainty": str(claim.get("uncertainty") or ""),
        "depends_on_claims": list(claim.get("depends_on_claims") or []),
        "dependency_claim_ids": list(claim.get("dependency_claim_ids") or []),
        "evidence_refs": list(claim.get("evidence_refs") or []),
        "evidence_records": list(claim.get("evidence_records") or []),
        "extensions": dict(claim.get("extensions") or {}),
    }


def _review_projection(
    claims: list[dict[str, Any]],
    *,
    selection_reasons: Mapping[str, list[str]],
    dependency_edges: list[dict[str, str]],
    namespace_collisions: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence: dict[str, dict[str, Any]] = {}
    for claim in claims:
        for record in claim.get("evidence_records") or []:
            if isinstance(record, dict) and record.get("evidence_id"):
                evidence[str(record["evidence_id"])] = record
    return {
        "schema": "fanfiction_review_projection_v2",
        "claims": [_review_claim(claim) for claim in claims],
        "evidence_closure": [evidence[key] for key in sorted(evidence)],
        "dependency_edges": dependency_edges,
        "selection_reasons": {
            claim["claim_id"]: _dedupe(selection_reasons.get(claim["claim_id"], []))
            for claim in claims
        },
        "namespace_collisions": namespace_collisions,
    }


def _budget_usage(
    claims: list[dict[str, Any]],
    *,
    units_by_claim: Mapping[str, int],
    partitions: Mapping[str, Mapping[str, list[str]]],
    required_ids: set[str],
    global_ids: set[str],
    dependency_ids: set[str],
    relevant_ids: set[str],
    optional_ids: set[str],
    budget_units: int,
    used_units: int,
    estimator: Any,
) -> dict[str, Any]:
    def category(ids: set[str]) -> dict[str, Any]:
        selected = sorted(ids)
        return {
            "claim_ids": selected,
            "units": sum(units_by_claim.get(claim_id, 0) for claim_id in selected),
        }

    partition_usage: dict[str, dict[str, Any]] = {}
    for kind, values in partitions.items():
        partition_usage[kind] = {
            key: {
                "claim_ids": ids,
                "units": sum(units_by_claim.get(claim_id, 0) for claim_id in ids),
            }
            for key, ids in values.items()
        }
    return {
        "estimator": estimator,
        "units": "estimated_text_units",
        "budget_units": budget_units,
        "used_units": used_units,
        "required": category(required_ids),
        "global": category(global_ids),
        "dependency": category(dependency_ids),
        "relevant": category(relevant_ids),
        "optional": category(optional_ids),
        "partitions": partition_usage,
        "overflow_reason": "",
        "top_contributors": [],
    }


def _optional_project_canon_claims(
    config: ConfigDocument,
    *,
    chapter_number: int,
    chapter_contract: dict[str, Any],
    chapter_card: dict[str, Any],
    all_claims: dict[str, dict[str, Any]],
    excluded_ids: set[str],
    token_budget: int,
    current_scope: Mapping[str, set[str]],
    write_rag_cache: bool,
) -> tuple[list[str], dict[str, Any]]:
    query_text = " ".join(
        str(value)
        for value in (
            chapter_contract.get("chapter_duty"),
            chapter_contract.get("conflict"),
            chapter_contract.get("chapter_turn"),
            chapter_card.get("reader_value"),
            chapter_card.get("observable_change"),
        )
        if str(value or "").strip()
    )
    if not query_text:
        return [], {"status": "skipped", "reason": "empty semantic query", "hit_ids": []}
    try:
        result = rag_query(
            config,
            query_text,
            top_k=32,
            candidate_pool=64,
            semantic=True,
            chapter_number=chapter_number,
            token_budget=token_budget,
            write_cache=write_rag_cache,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        return [], {
            "status": "unavailable",
            "reason": str(exc),
            "hit_ids": [],
        }
    selected: list[str] = []
    hit_ids: list[str] = []
    for hit in result.hits:
        hit_ids.append(hit.id)
        if not hit.id.startswith("source-canon:"):
            continue
        claim_id = hit.id.removeprefix("source-canon:")
        claim = all_claims.get(claim_id)
        if (
            claim is not None
            and claim_id not in excluded_ids
            and claim_id not in selected
            and _claim_applies(
                claim,
                chapter_number=chapter_number,
                chapter_card=chapter_card,
                current_scope=current_scope,
            )
        ):
            selected.append(claim_id)
    return selected, {
        "status": "completed",
        "query": query_text,
        "hit_ids": hit_ids,
        "selected_claim_ids": selected,
        "omitted_hit_ids": list(result.omitted_hit_ids),
        "used_units": result.used_units,
    }


def _author_projection(
    claims: list[dict[str, Any]],
    chapter_card: dict[str, Any],
    *,
    show_source_labels: bool,
) -> dict[str, Any]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for claim in claims:
        grouped.setdefault(str(claim.get("semantic_type") or "其他"), []).append(
            (str(claim.get("statement") or ""), _author_source_label(claim))
        )

    def collect(*types: str) -> list[str]:
        return _dedupe(
            (f"【{label}】{statement}" if show_source_labels else statement)
            for semantic_type in types
            for statement, label in grouped.get(semantic_type, [])
            if statement
        )

    event_rows = [
        {
            "description": (
                f"【{_author_source_label(claim)}】{str(claim.get('statement') or '')}"
                if show_source_labels
                else str(claim.get("statement") or "")
            ),
            "disposition": str(claim.get("extensions", {}).get("disposition") or ""),
        }
        for claim in claims
        if claim.get("semantic_type") == "原著事件命运"
    ]
    identity_notes = _dedupe(
        (
            f"【{_author_source_label(claim)}】{str(claim.get('statement') or '')}"
            if show_source_labels
            else str(claim.get("statement") or "")
        )
        for claim in claims
        if isinstance(claim.get("extensions", {}).get("identity"), dict)
        and str(claim.get("statement") or "")
    )
    return {
        "current_canon_time_and_scene": collect("故事切入点", "时间线", "地点", "世界规则"),
        "approved_divergences": collect("初始分歧", "分歧后果", "蝴蝶效应"),
        "character_knowledge_boundaries": collect(
            "人物知识边界", "人物阶段与知识边界", "未来知识可靠性"
        ),
        "relationship_stage": collect("关系阶段", "关系"),
        "event_dispositions": event_rows,
        "ability_and_crossover_rules": collect(
            "能力条件", "能力代价", "能力反制", "跨界宪法", "跨界兼容规则", "主世界适配器"
        ),
        "canon_character_agency": collect("原著人物职责", "原著人物自主性"),
        "original_contribution": collect("本章原创贡献", "原创贡献", "独立长期目标", "原作后续故事来源"),
        "protected_reveals": collect("保密信息", "禁止提前揭示", "保护揭示"),
        "source_identity_notes": identity_notes,
        "free_play": str(
            chapter_card.get("local_freedom")
            or "在已批准分歧、人物知识、事件命运和能力边界内自由设计微观动作、对话与场景细节。"
        ),
        "ending_state": str(chapter_card.get("observable_change") or ""),
    }


def _author_source_label(claim: Mapping[str, Any]) -> str:
    source_id = str(claim.get("source_id") or "")
    if source_id:
        return source_id
    namespace = str(claim.get("namespace") or "")
    return {
        "story_engine": "故事发动机",
        "route_design": "项目路线",
        "source_canon": "原著来源",
    }.get(namespace, "项目规则")


def _claim_units(claim: dict[str, Any], estimator: Any) -> int:
    projected = _review_claim(claim)
    return estimate_text_units(json.dumps(projected, ensure_ascii=False), estimator)


def _stale_diagnostics(
    documents: Mapping[str, Mapping[str, Any]], digests: Mapping[str, str]
) -> list[dict[str, str]]:
    route = documents["route_design"]
    engine = documents["story_engine"]
    diagnostics: list[dict[str, str]] = []
    if route.get("extensions", {}).get("source_canon_sha256") != digests["source_canon"]:
        diagnostics.append({"document": "route_design", "reason": "source_canon_changed"})
    if route.get("extensions", {}).get("story_engine_sha256") != digests["story_engine"]:
        diagnostics.append({"document": "route_design", "reason": "story_engine_changed"})
    if engine.get("extensions", {}).get("source_canon_sha256") != digests["source_canon"]:
        diagnostics.append({"document": "story_engine", "reason": "source_canon_changed"})
    if diagnostics:
        raise FanfictionContextError(
            "fanfiction_context_stale: "
            + ", ".join(f"{item['document']}:{item['reason']}" for item in diagnostics)
        )
    return diagnostics


def _bundle_hash(bundle: dict[str, Any]) -> str:
    payload = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _current_future_knowledge_documents(
    config: ConfigDocument,
    root: Path,
    *,
    target_chapter: int,
) -> tuple[dict[str, dict[str, Any]], dict[str, Path], dict[str, str]]:
    documents: dict[str, dict[str, Any]] = {}
    paths: dict[str, Path] = {}
    digests: dict[str, str] = {}
    directory = root / "10_bible" / "fanfiction" / "future_knowledge"
    for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        payload = _read_json(path)
        if not isinstance(payload, dict):
            raise FanfictionContextError(
                f"future_knowledge_document_invalid:{path.relative_to(root).as_posix()}"
            )
        semantic_errors = validate_semantic_document(payload, require_approved=True)
        extensions = payload.get("extensions")
        extensions = extensions if isinstance(extensions, dict) else {}
        if extensions.get("task_type") != "fanfiction_future_knowledge_reassessment":
            semantic_errors.append(
                "extensions.task_type must be fanfiction_future_knowledge_reassessment"
            )
        for claim in payload.get("claims") or []:
            claim_extensions: dict[str, Any] = {}
            if isinstance(claim, dict) and isinstance(claim.get("extensions"), dict):
                claim_extensions = dict(claim["extensions"])
            if claim_extensions.get("semantic_type") != "未来知识可靠性" or claim_extensions.get(
                "reliability"
            ) not in {"仍可靠", "部分可靠", "已失效", "反向误导"}:
                semantic_errors.append("future knowledge claim reliability contract is invalid")
        if semantic_errors:
            raise FanfictionContextError(
                f"future_knowledge_document_invalid:{path.relative_to(root).as_posix()}:"
                + ";".join(semantic_errors)
            )
        try:
            pin = future_knowledge_pin_for_approved(root, path)
        except FutureKnowledgeProvenanceError as exc:
            raise FanfictionContextError(
                f"future_knowledge_document_stale:{path.relative_to(root).as_posix()}:"
                f"provenance_pin:{exc}"
            ) from exc
        retention_chapter = _future_knowledge_retention_chapter(
            root, fallback=target_chapter
        )
        if future_knowledge_pin_retains(pin, retention_chapter):
            try:
                require_current_approved_future_knowledge(config, path)
            except FutureKnowledgeCurrentError as exc:
                raise FanfictionContextError(str(exc)) from exc
        if not future_knowledge_pin_applies(pin, target_chapter):
            continue
        key = f"future_knowledge:{path.stem}"
        documents[key] = payload
        paths[key] = path
        digests[key] = sha256(path.read_bytes()).hexdigest()
    return documents, paths, digests


def _future_knowledge_retention_chapter(root: Path, *, fallback: int) -> int:
    """Return the next production chapter, independent of a queried target chapter."""

    closure_dir = root / "30_state" / "chapter_closures"
    closed: list[int] = []
    for path in sorted(closure_dir.glob("ch*.json")) if closure_dir.is_dir() else []:
        match = re.fullmatch(r"ch0*(\d+)\.json", path.name)
        if match:
            closed.append(int(match.group(1)))
    return max(closed) + 1 if closed else fallback


def _canonical_json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _is_sha256(value: Any) -> bool:
    token = str(value or "")
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = [
    "FANFICTION_CONTEXT_BUNDLE_SCHEMA",
    "FanfictionContextError",
    "compile_fanfiction_context",
    "event_disposition_status",
    "fanfiction_context_status",
    "future_knowledge_impact_workflows",
    "require_current_fanfiction_context_bundle",
    "write_fanfiction_context_bundle",
]
