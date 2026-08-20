"""Compile market, composable-story, phase, and approved-style quality contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import copy
import json
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

import yaml

from longform_engine.config import ConfigDocument
from longform_engine.lengths import compile_length_forecast
from longform_engine.resources import resource_path
from longform_engine.story_profiles import active_story_facets, compile_story_profile
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root


MARKET_PROFILE_IDS = ("general_cn", "qidian_male", "fanqie_free", "jinjiang_female")
STORY_PHASE_IDS = ("opening", "early_serial", "stable_serial", "volume_climax", "aftermath")
QUALITY_STRICTNESS = ("light", "balanced", "strict")
APPROVED_BASELINE_PATH = "10_bible/style_profiles/approved_style_baseline.json"
PLATFORM_DEVIATION_POLICIES = ("P2_advisory", "P1_blocking")
MARKET_EVIDENCE_SCHEMA = "market_evidence_registry_v1"
COMPACT_CONTRACT_FIELDS = (
    "platform_promise",
    "phase_focus",
    "opening_promise_window",
    "chapter_duty_distribution",
    "payoff_cadence",
    "upgrade_cost",
    "scene_entry_friction",
    "exposition_density",
    "dialogue",
    "relationship_change_cadence",
    "foreshadow_release",
    "ending_distribution",
    "slow_chapter_policy",
    "platform_policy",
)


@dataclass(frozen=True)
class StyleBaselineApprovalResult:
    chapter_number: int
    baseline_file: str
    approved_by: str
    transaction_report: str
    next_command: str


def compile_effective_quality_contract(
    config: ConfigDocument,
    *,
    chapter_number: int,
    compare_markets: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Return the deterministic quality contract for one chapter."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    quality = config.data.get("quality")
    quality = quality if isinstance(quality, dict) else {}
    profile = quality.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    story_profile = config.data.get("story_profile")
    compiled_story = compile_story_profile(story_profile, market_ids=set(MARKET_PROFILE_IDS))
    if not compiled_story["ready"]:
        conflict_ids = ", ".join(item["conflict_id"] for item in compiled_story["unresolved_conflicts"])
        raise ValueError(f"Story profile has unresolved human conflicts: {conflict_ids}")
    market = str(compiled_story["market"]["primary"])
    configured_phase = str(profile.get("phase") or "auto")
    strictness = str(profile.get("strictness") or "balanced")
    if market not in MARKET_PROFILE_IDS:
        raise ValueError(f"Unknown quality market profile: {market}")
    if strictness not in QUALITY_STRICTNESS:
        raise ValueError(f"Unknown quality strictness: {strictness}")
    phase = (
        infer_story_phase(config, chapter_number)
        if configured_phase == "auto"
        else configured_phase
    )
    if phase not in STORY_PHASE_IDS:
        raise ValueError(f"Unknown quality story phase: {phase}")

    market_source = load_quality_profile("markets", market)
    phase_source = load_quality_profile("phases", phase)
    root = resolve_project_root(config)
    plan = read_json(root / "20_outline" / "chapter_plan.json", [])
    plan_row = next(
        (
            item
            for item in plan if isinstance(plan, list) and isinstance(item, dict)
            and int(item.get("chapter_number") or 0) == chapter_number
        ),
        {},
    )
    arc_id = str(plan_row.get("arc_id") or "") if isinstance(plan_row, dict) else ""
    arcs = read_json(root / "20_outline" / "story_arcs.json", [])
    current_arc = next(
        (
            item for item in arcs
            if isinstance(arcs, list) and isinstance(item, dict) and str(item.get("id") or "") == arc_id
        ),
        {},
    )
    contract: dict[str, Any] = {}
    source_records: list[dict[str, Any]] = []
    merge_trace: list[dict[str, Any]] = []
    overridden_fields: list[str] = []
    for payload, path, digest in (market_source,):
        merge_contract_layer(
            contract,
            payload["contract"],
            layer=str(payload["kind"]),
            source=path,
            digest=digest,
            merge_trace=merge_trace,
            overridden_fields=overridden_fields,
        )
        source_records.append(profile_source_record(payload, path, digest))

    merge_contract_layer(
        contract,
        phase_source[0]["contract"],
        layer="story_phase",
        source=phase_source[1],
        digest=phase_source[2],
        merge_trace=merge_trace,
        overridden_fields=overridden_fields,
    )
    source_records.append(profile_source_record(phase_source[0], phase_source[1], phase_source[2]))

    market_phase_contract = market_source[0].get("phase_overrides", {}).get(phase, {})
    market_phase_record = {
        "id": f"{market}:{phase}",
        "applied": bool(market_phase_contract),
        "source": market_source[1],
        "sha256": market_source[2],
    }
    if market_phase_contract:
        merge_contract_layer(
            contract,
            market_phase_contract,
            layer="market_phase",
            source=market_source[1],
            digest=market_source[2],
            merge_trace=merge_trace,
            overridden_fields=overridden_fields,
        )
        source_records.append(
            {
                "kind": "market_phase",
                "id": f"{market}:{phase}",
                "path": market_source[1],
                "sha256": market_source[2],
            }
        )

    for facet in compiled_story["selected_facets"]:
        facet_key = f"{facet['kind']}:{facet['id']}"
        facet_contract = {
            "story_facets": {
                facet_key: {
                    "level": facet["level"],
                    "requirements": copy.deepcopy(facet.get("requirements") or []),
                    "preferences": copy.deepcopy(facet.get("preferences") or []),
                    "risks": copy.deepcopy(facet.get("risks") or []),
                    "review_questions": copy.deepcopy(facet.get("review_questions") or []),
                }
            }
        }
        merge_contract_layer(
            contract,
            facet_contract,
            layer=str(facet["kind"]),
            source=str(facet["source"]),
            digest=str(facet["sha256"]),
            merge_trace=merge_trace,
            overridden_fields=overridden_fields,
        )
        source_records.append(
            {
                "kind": str(facet["kind"]),
                "id": str(facet["id"]),
                "path": str(facet["source"]),
                "sha256": str(facet["sha256"]),
                "level": str(facet["level"]),
            }
        )

    arc_focus = current_arc.get("quality_focus") if isinstance(current_arc, dict) else None
    if isinstance(arc_focus, dict):
        arc_contract = {
            "current_story_arc": {
                "arc_id": arc_id,
                "goal": str(current_arc.get("goal") or ""),
                "active_facets": copy.deepcopy(current_arc.get("active_facets") or []),
                **copy.deepcopy(arc_focus),
            }
        }
        arc_source = "20_outline/story_arcs.json"
        arc_path = root / arc_source
        arc_digest = file_sha256(arc_path)
        merge_contract_layer(
            contract,
            arc_contract,
            layer="current_story_arc",
            source=arc_source,
            digest=arc_digest,
            merge_trace=merge_trace,
            overridden_fields=overridden_fields,
        )
        source_records.append(
            {
                "kind": "current_story_arc",
                "id": arc_id,
                "path": arc_source,
                "sha256": arc_digest,
            }
        )

    baseline = load_approved_style_baseline(root)
    baseline_contract = baseline.get("contract_overrides")
    if isinstance(baseline_contract, dict):
        baseline_path = root / APPROVED_BASELINE_PATH
        merge_contract_layer(
            contract,
            baseline_contract,
            layer="user_approved_style_baseline",
            source=APPROVED_BASELINE_PATH,
            digest=file_sha256(baseline_path),
            merge_trace=merge_trace,
            overridden_fields=overridden_fields,
        )
    project_overrides = profile.get("overrides")
    if isinstance(project_overrides, dict):
        merge_contract_layer(
            contract,
            project_overrides,
            layer="project_overrides",
            source="project.yaml#quality.profile.overrides",
            digest=json_sha256(project_overrides),
            merge_trace=merge_trace,
            overridden_fields=overridden_fields,
        )

    blocking_policy = resolve_blocking_policy(contract)
    requested_compatibility = normalize_compatibility_markets(
        compiled_story["market"].get("compatibility"),
        compare_markets,
        primary_market=market,
    )
    compatibility_observations = build_compatibility_observations(
        market=market,
        target_markets=requested_compatibility,
        phase_source=phase_source,
        phase=phase,
        primary_contract=contract,
        baseline_contract=baseline_contract if isinstance(baseline_contract, dict) else {},
        project_overrides=project_overrides if isinstance(project_overrides, dict) else {},
    )

    approved_records = baseline.get("approved_chapters")
    approved_records = approved_records if isinstance(approved_records, list) else []
    requested_facets = list(plan_row.get("active_facets") or []) if isinstance(plan_row, dict) else []
    return {
        "schema": "effective_quality_contract_v1",
        "chapter_number": chapter_number,
        "primary_market": market,
        "market": market,
        "story_profile": compiled_story,
        "active_facets": active_story_facets(compiled_story, requested_facets, limit=3),
        "phase": phase,
        "market_phase": market_phase_record,
        "strictness": strictness,
        "contract": contract,
        "approved_style_baseline": {
            "source": APPROVED_BASELINE_PATH if approved_records else "",
            "approved_chapter_count": len(approved_records),
            "approved_chapters": [
                int(item["chapter_number"])
                for item in approved_records
                if isinstance(item, dict) and isinstance(item.get("chapter_number"), int)
            ],
            "observations": compact_baseline_observations(approved_records),
            "auto_expand": False,
        },
        "sources": source_records,
        "merge_trace": merge_trace,
        "overridden_fields": list(dict.fromkeys(overridden_fields)),
        "compatibility_observations": compatibility_observations[:3],
        "blocking_policy": blocking_policy,
        "merge_order": [
            "fact_and_safety_boundaries",
            "market",
            "story_facets",
            "current_story_arc",
            "phase",
            "market_phase",
            "user_approved_style_baseline",
            "project_overrides",
        ],
    }


def compact_effective_quality_contract(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded contract view embedded in Agent-facing work orders."""

    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}
    approved = (
        payload.get("approved_style_baseline")
        if isinstance(payload.get("approved_style_baseline"), dict)
        else {}
    )
    return {
        "schema": str(payload.get("schema") or "effective_quality_contract_v1"),
        "chapter_number": int(payload.get("chapter_number") or 0),
        "primary_market": str(payload.get("primary_market") or payload.get("market") or ""),
        "market": str(payload.get("market") or ""),
        "active_facets": [
            {
                key: copy.deepcopy(item.get(key))
                for key in ("kind", "id", "level", "requirements", "preferences", "risks", "review_questions")
            }
            for item in list(payload.get("active_facets") or [])[:3]
            if isinstance(item, dict)
        ],
        "phase": str(payload.get("phase") or ""),
        "market_phase": copy.deepcopy(payload.get("market_phase") or {}),
        "strictness": str(payload.get("strictness") or ""),
        "contract": {
            key: copy.deepcopy(contract[key])
            for key in COMPACT_CONTRACT_FIELDS
            if key in contract
        },
        "approved_style_baseline": {
            "approved_chapter_count": int(approved.get("approved_chapter_count") or 0),
            "approved_chapters": list(approved.get("approved_chapters") or [])[-8:],
            "observations": copy.deepcopy(list(approved.get("observations") or [])[-4:]),
            "auto_expand": False,
        },
        "compatibility_observations": copy.deepcopy(
            list(payload.get("compatibility_observations") or [])[:3]
        ),
        "blocking_policy": copy.deepcopy(payload.get("blocking_policy") or {}),
    }


def approve_style_baseline(
    config: ConfigDocument,
    *,
    chapter_number: int,
    approved_by: str,
) -> StyleBaselineApprovalResult:
    """Explicitly add one finalized chapter's prose-free craft fingerprint to the baseline."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    approved_by = str(approved_by or "").strip()
    if not approved_by:
        raise ValueError("approved_by is required.")
    root = resolve_project_root(config)
    finalization_path = root / "40_manuscript" / ("fin" + "al") / f"ch{chapter_number:03d}.finalization.json"
    finalization = read_json(finalization_path, {})
    if not isinstance(finalization, dict) or not finalization.get("final_sha256"):
        raise ValueError(f"Chapter ch{chapter_number:03d} must be explicitly finalized before baseline approval.")
    observation = finalized_structure_observation(root, chapter_number)
    if observation is None:
        raise ValueError(
            f"Chapter ch{chapter_number:03d} has no finalized structure observation; finalize it with quality history enabled."
        )

    baseline_path = root / APPROVED_BASELINE_PATH
    baseline = load_approved_style_baseline(root)
    records = baseline.get("approved_chapters")
    records = list(records) if isinstance(records, list) else []
    record = {
        "chapter_number": chapter_number,
        "final_sha256": str(finalization["final_sha256"]),
        "approved_by": approved_by,
        "approved_at": utc_now(),
        "observation": prose_free_observation(observation),
    }
    records = [
        item
        for item in records
        if not isinstance(item, dict) or int(item.get("chapter_number") or 0) != chapter_number
    ]
    records.append(record)
    records.sort(key=lambda item: int(item.get("chapter_number") or 0))
    payload = {
        "schema": "approved_style_baseline_v1",
        "auto_expand": False,
        "update_requires_human": True,
        "approved_chapters": records,
        "contract_overrides": baseline.get("contract_overrides") if isinstance(baseline.get("contract_overrides"), dict) else {},
        "updated_at": utc_now(),
    }
    with apply_transaction(
        root,
        command="quality baseline-approve",
        chapter_number=chapter_number,
        source_paths=(finalization_path, root / "30_state" / "quality" / "structure_history.jsonl"),
        touched_paths=(baseline_path,),
        metadata={"approved_by": approved_by, "auto_expand": False},
    ) as transaction:
        write_json(baseline_path, payload)
    return StyleBaselineApprovalResult(
        chapter_number=chapter_number,
        baseline_file=APPROVED_BASELINE_PATH,
        approved_by=approved_by,
        transaction_report=relative(root, transaction.report_file),
        next_command="longform-engine production next project.yaml",
    )


def infer_story_phase(config: ConfigDocument, chapter_number: int) -> str:
    root = resolve_project_root(config)
    plan = read_json(root / "20_outline" / "chapter_plan.json", [])
    plan_row = next(
        (
            item
            for item in plan
            if isinstance(plan, list) and isinstance(item, dict)
            and int(item.get("chapter_number") or 0) == chapter_number
        ),
        {},
    )
    arc_id = str(plan_row.get("arc_id") or "") if isinstance(plan_row, dict) else ""
    arcs = read_json(root / "20_outline" / "story_arcs.json", [])
    if arc_id and isinstance(arcs, list):
        arc = next((item for item in arcs if isinstance(item, dict) and item.get("id") == arc_id), {})
        declared_phase = str(arc.get("phase") or "") if isinstance(arc, dict) else ""
        if declared_phase in STORY_PHASE_IDS:
            return declared_phase
    forecast = compile_length_forecast(config.data["length"])
    metrics = read_json(root / "30_state" / "manuscript_metrics.json", {})
    completed = (
        int(metrics.get("total_content_characters") or 0)
        if isinstance(metrics, dict) and metrics.get("schema") == "manuscript_metrics_v1"
        else 0
    )
    progress = completed / max(1, forecast.target_total_characters)
    if chapter_number <= 3 or progress <= 0.06:
        return "opening"
    if progress <= 0.25:
        return "early_serial"
    return "stable_serial"


def load_quality_profile(kind: str, profile_id: str) -> tuple[dict[str, Any], str, str]:
    path = resource_path("config", "quality_profiles", kind, f"{profile_id}.yaml")
    raw = path.read_bytes()
    payload = yaml.safe_load(raw.decode("utf-8")) or {}
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "quality_profile_v1"
        or payload.get("kind") != kind.rstrip("s")
        or payload.get("id") != profile_id
        or not isinstance(payload.get("contract"), dict)
    ):
        raise ValueError(f"Invalid quality profile resource: {path}")
    validate_profile_extensions(payload, path)
    return payload, f"config/quality_profiles/{kind}/{profile_id}.yaml", sha256(raw).hexdigest()


def validate_profile_extensions(payload: dict[str, Any], path: Path) -> None:
    """Validate optional provenance, phase, and compatibility metadata."""

    for field in ("updated_at", "evidence_level", "heuristic_notes"):
        if field in payload and not isinstance(payload[field], str):
            raise ValueError(f"Invalid quality profile resource: {path} ({field} must be a string)")
    source_refs = payload.get("source_refs")
    if source_refs is not None and (
        not isinstance(source_refs, list)
        or any(not isinstance(item, str) or not item.strip() for item in source_refs)
    ):
        raise ValueError(f"Invalid quality profile resource: {path} (source_refs must be non-empty strings)")
    evidence_registry = load_market_evidence_registry()
    evidence_bindings = payload.get("evidence_bindings")
    if payload.get("kind") == "market" and payload.get("id") in {"qidian_male", "fanqie_free"}:
        required_binding_keys = {
            *(f"contract.{key}" for key in payload.get("contract", {})),
            *(f"phase_overrides.{key}" for key in payload.get("phase_overrides", {})),
        }
        if not isinstance(evidence_bindings, dict) or set(evidence_bindings) != required_binding_keys:
            raise ValueError(
                f"Invalid quality profile resource: {path} (every platform contract/phase field requires evidence_bindings)"
            )
        for field, refs in evidence_bindings.items():
            validate_market_evidence_refs(
                refs,
                market_id=str(payload["id"]),
                registry=evidence_registry,
                label=f"{path}:{field}",
            )
        bound_ids = {
            str(evidence_id)
            for refs in evidence_bindings.values()
            for evidence_id in refs
        }
        bound_urls = {str(evidence_registry[evidence_id]["source_url"]) for evidence_id in bound_ids}
        if set(source_refs or []) != bound_urls:
            raise ValueError(
                f"Invalid quality profile resource: {path} "
                "(source_refs must exactly match bound market evidence URLs)"
            )
    phase_overrides = payload.get("phase_overrides")
    if phase_overrides is not None:
        if not isinstance(phase_overrides, dict):
            raise ValueError(f"Invalid quality profile resource: {path} (phase_overrides must be an object)")
        for phase, contract in phase_overrides.items():
            if phase not in STORY_PHASE_IDS or not isinstance(contract, dict):
                raise ValueError(f"Invalid quality profile resource: {path} (invalid phase override {phase})")
    guidance = payload.get("compatibility_guidance")
    if guidance is not None:
        if not isinstance(guidance, list):
            raise ValueError(f"Invalid quality profile resource: {path} (compatibility_guidance must be a list)")
        required = {"field", "code", "message", "evidence_refs", "execution_level"}
        for index, item in enumerate(guidance):
            if not isinstance(item, dict) or not required.issubset(item):
                raise ValueError(
                    f"Invalid quality profile resource: {path} "
                    f"(compatibility_guidance[{index}] missing required fields)"
                )
            if any(
                not isinstance(item[field], str) or not item[field].strip()
                for field in required - {"evidence_refs"}
            ):
                raise ValueError(
                    f"Invalid quality profile resource: {path} "
                    f"(compatibility_guidance[{index}] fields must be strings)"
                )
            if item.get("execution_level") != "P2_advisory":
                raise ValueError(
                    f"Invalid quality profile resource: {path} "
                    f"(compatibility_guidance[{index}] execution_level must be P2_advisory)"
                )
            validate_market_evidence_refs(
                item.get("evidence_refs"),
                market_id=str(payload["id"]),
                registry=evidence_registry,
                label=f"{path}:compatibility_guidance[{index}]",
            )
            phases = item.get("phases")
            if phases is not None and (
                not isinstance(phases, list)
                or any(phase not in STORY_PHASE_IDS for phase in phases)
            ):
                raise ValueError(
                    f"Invalid quality profile resource: {path} "
                    f"(compatibility_guidance[{index}].phases is invalid)"
                )


def profile_source_record(payload: dict[str, Any], path: str, digest: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "kind": str(payload["kind"]),
        "id": str(payload["id"]),
        "path": path,
        "sha256": digest,
    }
    for field in ("updated_at", "evidence_level", "source_refs", "evidence_bindings", "heuristic_notes"):
        if field in payload:
            record[field] = copy.deepcopy(payload[field])
    return record


def load_market_evidence_registry() -> dict[str, dict[str, Any]]:
    path = resource_path("config", "quality_profiles", "market_evidence_registry.yaml")
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or payload.get("schema") != MARKET_EVIDENCE_SCHEMA:
        raise ValueError(f"Invalid market evidence registry: {path}")
    items = payload.get("items")
    required = {
        "evidence_id", "market_id", "source_url", "source_date",
        "evidence_grade", "execution_level", "claim_boundary",
    }
    if not isinstance(items, list):
        raise ValueError(f"Invalid market evidence registry: {path} (items must be a list)")
    registry: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if (
            not isinstance(item, dict)
            or set(item) != required
            or any(not isinstance(item[field], str) or not item[field].strip() for field in required)
        ):
            raise ValueError(f"Invalid market evidence registry: {path} (items[{index}])")
        evidence_id = str(item["evidence_id"])
        if evidence_id in registry:
            raise ValueError(f"Invalid market evidence registry: {path} (duplicate {evidence_id})")
        parsed_url = urlsplit(str(item["source_url"]))
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError(f"Invalid market evidence registry: {path} (source_url)")
        try:
            date.fromisoformat(str(item["source_date"]))
        except ValueError as exc:
            raise ValueError(
                f"Invalid market evidence registry: {path} (source_date must be YYYY-MM-DD)"
            ) from exc
        if item["execution_level"] not in {"P2_advisory", "contract_required"}:
            raise ValueError(f"Invalid market evidence registry: {path} (execution_level)")
        registry[evidence_id] = item
    return registry


def validate_market_evidence_refs(
    refs: Any,
    *,
    market_id: str,
    registry: dict[str, dict[str, Any]],
    label: str,
) -> None:
    if not isinstance(refs, list) or not refs:
        raise ValueError(f"{label} requires non-empty market evidence refs")
    if any(str(ref) not in registry or registry[str(ref)]["market_id"] != market_id for ref in refs):
        raise ValueError(f"{label} contains unresolved or cross-market evidence refs")


def merge_contract_layer(
    contract: dict[str, Any],
    override: dict[str, Any],
    *,
    layer: str,
    source: str,
    digest: str,
    merge_trace: list[dict[str, Any]],
    overridden_fields: list[str],
) -> None:
    changed: list[str] = []
    overridden: list[str] = []
    merge_with_audit(contract, override, changed=changed, overridden=overridden)
    overridden_fields.extend(overridden)
    merge_trace.append(
        {
            "layer": layer,
            "source": source,
            "sha256": digest,
            "changed_fields": list(dict.fromkeys(changed)),
            "overridden_fields": list(dict.fromkeys(overridden)),
        }
    )


def merge_with_audit(
    base: dict[str, Any],
    override: dict[str, Any],
    *,
    changed: list[str],
    overridden: list[str],
    prefix: str = "",
) -> None:
    for key, value in override.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_with_audit(
                base[key],
                value,
                changed=changed,
                overridden=overridden,
                prefix=path,
            )
            continue
        if key not in base:
            changed.extend(leaf_paths(value, path))
            base[key] = copy.deepcopy(value)
            continue
        if base[key] != value:
            changed.extend(leaf_paths(value, path))
            overridden.extend(leaf_paths(value, path))
            base[key] = copy.deepcopy(value)


def leaf_paths(value: Any, prefix: str) -> list[str]:
    if isinstance(value, dict) and value:
        result: list[str] = []
        for key, child in value.items():
            result.extend(leaf_paths(child, f"{prefix}.{key}"))
        return result
    return [prefix]


def normalize_compatibility_markets(
    configured: Any,
    requested: Iterable[str] | None,
    *,
    primary_market: str,
) -> list[str]:
    values: list[str] = []
    if isinstance(configured, list):
        values.extend(str(item).strip() for item in configured)
    if requested is not None:
        values.extend(str(item).strip() for item in requested)
    result: list[str] = []
    for market in values:
        if not market or market == primary_market or market in result:
            continue
        if market not in MARKET_PROFILE_IDS:
            raise ValueError(f"Unknown compatibility market profile: {market}")
        result.append(market)
    return result


def build_compatibility_observations(
    *,
    market: str,
    target_markets: list[str],
    phase_source: tuple[dict[str, Any], str, str],
    phase: str,
    primary_contract: dict[str, Any],
    baseline_contract: dict[str, Any],
    project_overrides: dict[str, Any],
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for target_market in target_markets:
        target_source = load_quality_profile("markets", target_market)
        target_contract: dict[str, Any] = {}
        for layer in (target_source[0]["contract"], phase_source[0]["contract"]):
            deep_merge(target_contract, layer)
        target_phase = target_source[0].get("phase_overrides", {}).get(phase, {})
        if isinstance(target_phase, dict):
            deep_merge(target_contract, target_phase)
        deep_merge(target_contract, baseline_contract)
        deep_merge(target_contract, project_overrides)
        for guidance in target_source[0].get("compatibility_guidance", []):
            phases = guidance.get("phases")
            if isinstance(phases, list) and phase not in phases:
                continue
            field = str(guidance["field"])
            primary_value = dotted_get(primary_contract, field)
            comparison_value = dotted_get(target_contract, field)
            if primary_value == comparison_value:
                continue
            observations.append(
                {
                    "market": target_market,
                    "compared_from": market,
                    "field": field,
                    "code": str(guidance["code"]),
                    "severity": "P2",
                    "blocking": False,
                    "message": str(guidance["message"]),
                    "market_evidence_refs": list(guidance["evidence_refs"]),
                    "execution_level": str(guidance["execution_level"]),
                    "primary_value": copy.deepcopy(primary_value),
                    "comparison_value": copy.deepcopy(comparison_value),
                    "source": target_source[1],
                    "sha256": target_source[2],
                }
            )
            if len(observations) >= 3:
                return observations
    return observations


def dotted_get(value: dict[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def resolve_blocking_policy(contract: dict[str, Any]) -> dict[str, Any]:
    policy = contract.get("platform_policy") if isinstance(contract.get("platform_policy"), dict) else {}
    primary = str(policy.get("primary_deviation") or "P2_advisory")
    if primary not in PLATFORM_DEVIATION_POLICIES:
        raise ValueError(
            "quality.profile.overrides.platform_policy.primary_deviation must be "
            f"one of: {', '.join(PLATFORM_DEVIATION_POLICIES)}"
        )
    return {
        "primary_deviation": primary,
        "primary_can_block": primary == "P1_blocking",
        "compatibility_deviation": "P2_advisory",
        "compatibility_can_block": False,
        "deterministic_P0_P1_unchanged": True,
    }


def json_sha256(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def load_approved_style_baseline(root: Path) -> dict[str, Any]:
    payload = read_json(root / APPROVED_BASELINE_PATH, {})
    if not isinstance(payload, dict) or payload.get("schema") != "approved_style_baseline_v1":
        return {
            "schema": "approved_style_baseline_v1",
            "auto_expand": False,
            "update_requires_human": True,
            "approved_chapters": [],
            "contract_overrides": {},
        }
    return payload


def finalized_structure_observation(root: Path, chapter_number: int) -> dict[str, Any] | None:
    path = root / "30_state" / "quality" / "structure_history.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and int(payload.get("chapter_number") or 0) == chapter_number:
            return payload
    return None


def prose_free_observation(observation: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "opening_mode",
        "topology_id",
        "ending_mode",
        "scene_count",
        "scene_types",
        "payoff_position",
        "emotional_curve",
        "dialogue_acts",
        "sentence_length",
        "paragraph_length",
        "dialogue_ratio",
        "paragraph_shape",
    }
    return {
        key: copy.deepcopy(value)
        for key, value in observation.items()
        if key in allowed
    }


def compact_baseline_observations(records: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in records[-8:]:
        if not isinstance(item, dict) or not isinstance(item.get("observation"), dict):
            continue
        result.append(
            {
                "chapter_number": int(item.get("chapter_number") or 0),
                "observation": prose_free_observation(item["observation"]),
            }
        )
    return result


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
