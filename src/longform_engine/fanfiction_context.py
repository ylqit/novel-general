"""Compile approved fanfiction semantics into one chapter-scoped context bundle."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable

from longform_engine.config import ConfigDocument
from longform_engine.prompting import estimate_text_units, resolve_context_budget_contract
from longform_engine.rag import query as rag_query
from longform_engine.semantic_protocols import build_workflow_record, validate_semantic_document
from longform_engine.storage import atomic_write_text, resolve_project_root


FANFICTION_CONTEXT_BUNDLE_SCHEMA = "fanfiction_context_bundle_v1"


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

MANDATORY_ROUTE_TYPES = frozenset(
    {
        "初始分歧",
        "分歧后果",
        "故事切入点",
        "人物知识边界",
        "人物阶段与知识边界",
        "未来知识可靠性",
        "原著事件命运",
        "原著人物职责",
        "原著人物自主性",
        "能力条件",
        "能力代价",
        "能力反制",
        "跨界宪法",
        "跨界兼容规则",
        "主世界适配器",
        "保密信息",
        "禁止提前揭示",
        "保护揭示",
        "原创贡献",
        "本章原创贡献",
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
) -> dict[str, Any]:
    """Compile explicit dependencies first, then optional project-Canon retrieval."""

    if chapter_number <= 0:
        raise FanfictionContextError("fanfiction context requires a positive chapter number")
    root = resolve_project_root(config)
    paths = {
        "source_canon": root / "10_bible" / "fanfiction" / "source_canon.json",
        "story_engine": root / "10_bible" / "fanfiction" / "story_engine.json",
        "route_design": root / "10_bible" / "fanfiction" / "fanfiction_bible.json",
    }
    documents = {
        name: _load_approved_document(path, name=name)
        for name, path in paths.items()
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
            record = _claim_record(namespace, raw_claim)
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

    explicit_ids, missing_explicit = _stable_claim_references(
        (chapter_contract, chapter_card, character_packet), set(all_claims)
    )
    if missing_explicit:
        raise FanfictionContextError(
            "fanfiction_context_missing_claims: " + ", ".join(sorted(missing_explicit))
        )
    out_of_scope = {
        claim_id
        for claim_id in explicit_ids
        if not _claim_applies(
            all_claims[claim_id],
            chapter_number=chapter_number,
            chapter_card=chapter_card,
        )
    }
    if out_of_scope:
        raise FanfictionContextError(
            "fanfiction_context_claim_out_of_scope: " + ", ".join(sorted(out_of_scope))
        )
    mandatory_ids = set(explicit_ids)
    for claim_id, claim in all_claims.items():
        semantic_type = str(claim.get("semantic_type") or "")
        if not _claim_applies(claim, chapter_number=chapter_number, chapter_card=chapter_card):
            continue
        if claim["namespace"] == "story_engine" and semantic_type in MANDATORY_STORY_TYPES:
            mandatory_ids.add(claim_id)
        if claim["namespace"] == "route_design" and semantic_type in MANDATORY_ROUTE_TYPES:
            mandatory_ids.add(claim_id)
    mandatory_ids = _dependency_closure(mandatory_ids, all_claims)

    budget = resolve_context_budget_contract(root)
    bundle_budget = max(1_200, int(budget.capacity_units * 0.42))
    mandatory_records = [all_claims[item] for item in sorted(mandatory_ids)]
    mandatory_units = sum(_claim_units(item, budget.estimator) for item in mandatory_records)
    if mandatory_units > bundle_budget:
        raise FanfictionContextError(
            "prompt_budget_exceeded: required fanfiction claims use "
            f"{mandatory_units} units but the chapter bundle budget is {bundle_budget}; "
            "required knowledge, divergence, event fate, ability, or crossover rules cannot be truncated"
        )

    optional_ids, retrieval_diagnostics = _optional_project_canon_claims(
        config,
        chapter_number=chapter_number,
        chapter_contract=chapter_contract,
        chapter_card=chapter_card,
        all_claims=all_claims,
        excluded_ids=mandatory_ids,
        token_budget=max(256, bundle_budget - mandatory_units),
    )
    included_ids = list(sorted(mandatory_ids))
    omitted: list[dict[str, Any]] = []
    used_units = mandatory_units
    for claim_id in optional_ids:
        record = all_claims[claim_id]
        units = _claim_units(record, budget.estimator)
        if used_units + units > bundle_budget:
            omitted.append(
                {
                    "claim_id": claim_id,
                    "reason": "token_budget_optional",
                    "required": False,
                }
            )
            continue
        included_ids.append(claim_id)
        used_units += units

    included = [all_claims[item] for item in included_ids]
    stale = _stale_diagnostics(documents, paths)
    bundle: dict[str, Any] = {
        "schema": FANFICTION_CONTEXT_BUNDLE_SCHEMA,
        "chapter_number": chapter_number,
        "continuity_mode": str(
            config.data.get("fanfiction", {}).get("continuity_mode") or ""
        ),
        "source_files": [
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for path in paths.values()
        ],
        "required_claim_ids": sorted(mandatory_ids),
        "included_claim_ids": included_ids,
        "omitted_claims": omitted,
        "claims": included,
        "author_projection": _author_projection(included, chapter_card),
        "diagnostics": {
            "selection_strategy": (
                "explicit stable claim references, dependency closure, current semantic scope, "
                "then optional project_canon hybrid retrieval"
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
    chapter_number = int(bundle.get("chapter_number") or 0)
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
            "next_command": "longform-engine production next project.yaml",
        }
    stale_reasons = []
    for item in payload.get("source_files") or []:
        if not isinstance(item, dict):
            continue
        source = root / str(item.get("path") or "")
        if not source.is_file() or sha256(source.read_bytes()).hexdigest() != item.get("sha256"):
            stale_reasons.append(str(item.get("path") or ""))
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
        "diagnostics": payload.get("diagnostics") or {},
    }


def event_disposition_status(config: ConfigDocument) -> dict[str, Any]:
    root = resolve_project_root(config)
    route = _read_json(root / "10_bible" / "fanfiction" / "fanfiction_bible.json")
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
        "route_status": "approved" if isinstance(route, dict) and not validate_semantic_document(
            route, require_approved=True
        ) else "missing_or_stale",
        "events": rows,
        "pending_count": sum(item["disposition"] == "待决定" for item in rows),
    }


def future_knowledge_impact_workflow(
    config: ConfigDocument,
    *,
    chapter_number: int,
    event_ledger: dict[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    """Open exactly one future-knowledge reassessment after the first realized divergence."""

    root = resolve_project_root(config)
    if str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        return None
    directory = root / "50_workbench" / "fanfiction_knowledge_impacts"
    if any(directory.glob("ch*.workflow.json")):
        return None
    bundle_path = root / "50_workbench" / "fanfiction_context" / f"ch{chapter_number:03d}.json"
    bundle = _read_json(bundle_path)
    if not isinstance(bundle, dict) or bundle.get("schema") != FANFICTION_CONTEXT_BUNDLE_SCHEMA:
        return None
    claims = {
        str(item.get("claim_id") or ""): item
        for item in bundle.get("claims") or []
        if isinstance(item, dict) and item.get("claim_id")
    }
    triggers: set[str] = set()
    for event in event_ledger.get("events") or []:
        if not isinstance(event, dict) or event.get("state") != "realized":
            continue
        for dependency in event.get("dependency_refs") or []:
            claim = claims.get(str(dependency))
            if not claim:
                continue
            semantic_type = str(claim.get("semantic_type") or "")
            disposition = str(claim.get("extensions", {}).get("disposition") or "")
            if semantic_type == "初始分歧" or (
                semantic_type == "原著事件命运" and disposition not in {"", "保留"}
            ):
                triggers.add(str(dependency))
    if not triggers:
        return None
    knowledge_claims = [
        str(item.get("claim_id") or "")
        for item in bundle.get("claims") or []
        if isinstance(item, dict)
        and item.get("semantic_type") in {"人物知识边界", "人物阶段与知识边界", "未来知识可靠性"}
    ]
    target = directory / f"ch{chapter_number:03d}.workflow.json"
    workflow = build_workflow_record(
        workflow_id=f"future_knowledge_impact_ch{chapter_number:03d}",
        workflow_kind="fanfiction_future_knowledge_impact",
        scope={"kind": "chapter", "chapter_number": chapter_number},
        state="awaiting_human",
        inputs=[
            {
                "path": bundle_path.relative_to(root).as_posix(),
                "sha256": sha256(bundle_path.read_bytes()).hexdigest(),
                "kind": "fanfiction_context_bundle",
            }
        ],
        outputs=[],
        authorization={"canonical_mutation": False, "requires_human_decision": True},
        diagnostics=[
            {
                "code": "first_major_divergence_realized",
                "trigger_claim_ids": sorted(triggers),
                "knowledge_claim_ids": knowledge_claims,
            }
        ],
        extensions={
            "allowed_reliability_states": [
                "仍可靠",
                "部分可靠",
                "仅可作为线索",
                "已失效",
                "与新连续性冲突",
            ],
            "instruction": (
                "由独立语义任务评估每条未来知识在分歧后的可靠性；LLM 只提出候选，"
                "人工批准后才可更新路线和人物知识边界。"
            ),
            "next_command": "longform-engine fanfiction design-task project.yaml",
        },
    )
    return target, workflow


def _load_approved_document(path: Path, *, name: str) -> dict[str, Any]:
    payload = _read_json(path)
    errors = validate_semantic_document(payload, require_approved=True)
    if errors:
        raise FanfictionContextError(f"{name} is not an approved semantic document: {'; '.join(errors)}")
    return payload


def _claim_record(namespace: str, claim: dict[str, Any]) -> dict[str, Any]:
    extensions_value = claim.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    return {
        "claim_id": str(claim.get("claim_id") or ""),
        "namespace": namespace,
        "source_id": str(extensions.get("source_id") or ""),
        "semantic_type": str(extensions.get("semantic_type") or "语义主张"),
        "statement": str(claim.get("statement") or ""),
        "applicability": claim.get("applicability"),
        "uncertainty": str(claim.get("uncertainty") or ""),
        "depends_on_claims": [
            str(item) for item in extensions.get("depends_on_claims") or [] if str(item)
        ],
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


def _stable_claim_references(
    values: Iterable[Any], known_ids: set[str]
) -> tuple[set[str], set[str]]:
    references: set[str] = set()
    missing: set[str] = set()
    explicit_fields = {
        "fanfiction_claim_refs",
        "fanfiction_claim_ids",
        "fanfiction_context_refs",
        "canon_claim_refs",
    }

    def register_explicit(value: Any) -> None:
        candidates = value if isinstance(value, list) else [value]
        for candidate in candidates:
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            if candidate in known_ids:
                references.add(candidate)
            else:
                missing.add(candidate)

    def walk(value: Any, *, fanfiction_projection: bool = False) -> None:
        if isinstance(value, str) and value in known_ids:
            references.add(value)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in explicit_fields or (fanfiction_projection and key == "claim_refs"):
                    register_explicit(child)
                else:
                    walk(child, fanfiction_projection=key == "fanfiction_projection")
        elif isinstance(value, list):
            for child in value:
                walk(child, fanfiction_projection=fanfiction_projection)

    for value in values:
        walk(value)
    return references, missing


def _dependency_closure(seed: set[str], claims: dict[str, dict[str, Any]]) -> set[str]:
    closure = set(seed)
    pending = list(seed)
    while pending:
        claim_id = pending.pop()
        claim = claims.get(claim_id)
        if claim is None:
            continue
        for dependency in claim.get("depends_on_claims") or []:
            if dependency not in claims:
                raise FanfictionContextError(
                    f"fanfiction_context_missing_dependency:{claim_id}->{dependency}"
                )
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return closure


def _claim_applies(
    claim: dict[str, Any],
    *,
    chapter_number: int,
    chapter_card: dict[str, Any],
) -> bool:
    extensions_value = claim.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    chapters = extensions.get("chapter_numbers")
    if isinstance(chapters, list) and chapters:
        return chapter_number in {int(item) for item in chapters if str(item).isdigit()}
    start = int(extensions.get("from_chapter") or 0)
    end = int(extensions.get("to_chapter") or 0)
    if start and chapter_number < start:
        return False
    if end and chapter_number > end:
        return False
    volume_ids = {str(item) for item in extensions.get("volume_ids") or []}
    if volume_ids and str(chapter_card.get("volume_id") or "") not in volume_ids:
        return False
    return True


def _optional_project_canon_claims(
    config: ConfigDocument,
    *,
    chapter_number: int,
    chapter_contract: dict[str, Any],
    chapter_card: dict[str, Any],
    all_claims: dict[str, dict[str, Any]],
    excluded_ids: set[str],
    token_budget: int,
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
    claims: list[dict[str, Any]], chapter_card: dict[str, Any]
) -> dict[str, Any]:
    grouped: dict[str, list[str]] = {}
    for claim in claims:
        grouped.setdefault(str(claim.get("semantic_type") or "其他"), []).append(
            str(claim.get("statement") or "")
        )

    def collect(*types: str) -> list[str]:
        return _dedupe(
            statement
            for semantic_type in types
            for statement in grouped.get(semantic_type, [])
            if statement
        )

    event_rows = [
        {
            "description": str(claim.get("statement") or ""),
            "disposition": str(claim.get("extensions", {}).get("disposition") or ""),
        }
        for claim in claims
        if claim.get("semantic_type") == "原著事件命运"
    ]
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
        "free_play": str(
            chapter_card.get("local_freedom")
            or "在已批准分歧、人物知识、事件命运和能力边界内自由设计微观动作、对话与场景细节。"
        ),
        "ending_state": str(chapter_card.get("observable_change") or ""),
    }


def _claim_units(claim: dict[str, Any], estimator: str) -> int:
    projected = {
        "statement": claim.get("statement"),
        "applicability": claim.get("applicability"),
        "uncertainty": claim.get("uncertainty"),
        "semantic_type": claim.get("semantic_type"),
    }
    return estimate_text_units(json.dumps(projected, ensure_ascii=False), estimator)


def _stale_diagnostics(
    documents: dict[str, dict[str, Any]], paths: dict[str, Path]
) -> list[dict[str, str]]:
    route = documents["route_design"]
    engine = documents["story_engine"]
    diagnostics: list[dict[str, str]] = []
    if route.get("extensions", {}).get("source_canon_sha256") != sha256(
        paths["source_canon"].read_bytes()
    ).hexdigest():
        diagnostics.append({"document": "route_design", "reason": "source_canon_changed"})
    if route.get("extensions", {}).get("story_engine_sha256") != sha256(
        paths["story_engine"].read_bytes()
    ).hexdigest():
        diagnostics.append({"document": "route_design", "reason": "story_engine_changed"})
    if engine.get("extensions", {}).get("source_canon_sha256") != sha256(
        paths["source_canon"].read_bytes()
    ).hexdigest():
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
    "future_knowledge_impact_workflow",
    "write_fanfiction_context_bundle",
]
