"""Composable story-profile validation and contract compilation."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

import yaml

from longform_engine.resources import resource_path


STORY_PROFILE_SCHEMA = "story_profile_v1"
BUILTIN_MARKET_IDS = frozenset({"general_cn", "qidian_male", "fanqie_free", "jinjiang_female"})
FACET_KINDS = (
    "setting",
    "plot_engines",
    "narrative_forms",
    "premise_devices",
    "relationship_modes",
    "tone",
)
SELECTION_LEVELS = ("primary", "supporting", "accent")


class StoryProfileError(ValueError):
    """Raised when selected story facets are unknown or contradictory."""


def validate_story_profile(profile: Any, *, market_ids: set[str]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise StoryProfileError("story_profile must be a mapping")
    market = profile.get("market")
    if not isinstance(market, dict):
        raise StoryProfileError("story_profile.market must be a mapping")
    primary_market = str(market.get("primary") or "")
    if primary_market not in market_ids:
        raise StoryProfileError("story_profile.market.primary is unknown")
    compatibility = market.get("compatibility", [])
    if not isinstance(compatibility, list) or any(str(item) not in market_ids for item in compatibility):
        raise StoryProfileError("story_profile.market.compatibility contains an unknown market")
    if primary_market in compatibility:
        raise StoryProfileError("primary market must not also be a compatibility market")

    registries = load_facet_registries()
    for kind in FACET_KINDS:
        selections = normalize_facet_selection(kind, profile.get(kind), registries[kind])
        if kind in {"setting", "plot_engines"} and not selections:
            raise StoryProfileError(f"story_profile.{kind} must select at least one facet")
        if kind == "setting" and sum(item["level"] == "primary" for item in selections) != 1:
            raise StoryProfileError("story_profile.setting must contain exactly one primary facet")
    resolutions = profile.get("resolutions", [])
    if not isinstance(resolutions, list):
        raise StoryProfileError("story_profile.resolutions must be a list")
    for index, resolution in enumerate(resolutions):
        if not isinstance(resolution, dict) or set(resolution) != {"conflict_id", "decision", "rationale"}:
            raise StoryProfileError(
                f"story_profile.resolutions[{index}] must contain conflict_id, decision, and rationale only"
            )
        if any(not isinstance(resolution.get(key), str) or not resolution[key].strip() for key in resolution):
            raise StoryProfileError(f"story_profile.resolutions[{index}] fields must be non-empty strings")
    return profile


def compile_story_profile(profile: dict[str, Any], *, market_ids: set[str]) -> dict[str, Any]:
    validate_story_profile(profile, market_ids=market_ids)
    registries = load_facet_registries()
    selected: list[dict[str, Any]] = []
    for kind in FACET_KINDS:
        for selection in normalize_facet_selection(kind, profile.get(kind), registries[kind]):
            facet = deepcopy(registries[kind][selection["id"]])
            selected.append(
                {
                    "kind": kind,
                    "id": selection["id"],
                    "level": selection["level"],
                    "source": facet.pop("source"),
                    "sha256": facet.pop("sha256"),
                    **facet,
                }
            )
    conflicts = detect_conflicts(selected)
    resolutions = {
        str(item.get("conflict_id")): item
        for item in profile.get("resolutions", [])
        if isinstance(item, dict) and item.get("conflict_id")
    }
    unresolved = [item for item in conflicts if item["conflict_id"] not in resolutions]
    conflict_ids = {item["conflict_id"] for item in conflicts}
    unused_resolutions = sorted(set(resolutions) - conflict_ids)
    requirements = collect_entries(selected, "requirements")
    preferences = collect_entries(selected, "preferences")
    risks = collect_entries(selected, "risks")
    review_questions = collect_entries(selected, "review_questions")
    return {
        "schema": "compiled_story_profile_v1",
        "market": deepcopy(profile["market"]),
        "selected_facets": selected,
        "requirements": requirements,
        "preferences": preferences,
        "risks": risks,
        "review_questions": review_questions,
        "conflicts": conflicts,
        "unresolved_conflicts": unresolved,
        "unused_resolution_ids": unused_resolutions,
        "resolutions": list(resolutions.values()),
        "ready": not unresolved and not unused_resolutions,
        "profile_sha256": sha256(
            json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }


def active_story_facets(compiled: dict[str, Any], requested: list[str] | None = None, *, limit: int = 3) -> list[dict[str, Any]]:
    facets = list(compiled.get("selected_facets") or [])
    requested_ids = [str(item) for item in requested or []]
    level_order = {"primary": 0, "supporting": 1, "accent": 2}
    def request_position(item: dict[str, Any]) -> int:
        keys = (f"{item.get('kind')}:{item.get('id')}", str(item.get("id")))
        return min((requested_ids.index(key) for key in keys if key in requested_ids), default=999)

    facets.sort(
        key=lambda item: (
            0 if request_position(item) < 999 else 1,
            request_position(item),
            level_order.get(str(item.get("level")), 9),
            FACET_KINDS.index(str(item.get("kind"))),
            str(item.get("id")),
        )
    )
    return facets[:limit]


def load_facet_registries() -> dict[str, dict[str, dict[str, Any]]]:
    registries: dict[str, dict[str, dict[str, Any]]] = {}
    for kind in FACET_KINDS:
        path = resource_path("config", "story_facets", f"{kind}.yaml")
        raw = path.read_bytes()
        payload = yaml.safe_load(raw.decode("utf-8")) or {}
        profiles = payload.get("profiles") if isinstance(payload, dict) else None
        if payload.get("schema") != "story_facet_registry_v1" or payload.get("kind") != kind or not isinstance(profiles, dict):
            raise StoryProfileError(f"invalid story facet registry: {path}")
        normalized: dict[str, dict[str, Any]] = {}
        for facet_id, value in profiles.items():
            if not isinstance(value, dict):
                raise StoryProfileError(f"story facet {kind}:{facet_id} must be a mapping")
            for field in ("requirements", "preferences", "risks", "review_questions", "conflicts"):
                entries = value.get(field, [])
                if not isinstance(entries, list) or any(not isinstance(item, str) or not item.strip() for item in entries):
                    raise StoryProfileError(f"story facet {kind}:{facet_id}.{field} must be a string list")
            prompt_adapter = value.get("prompt_adapter")
            if not isinstance(prompt_adapter, str) or not prompt_adapter.strip():
                raise StoryProfileError(f"story facet {kind}:{facet_id}.prompt_adapter must be non-empty text")
            if not any("\u3400" <= char <= "\u9fff" for char in prompt_adapter):
                raise StoryProfileError(f"story facet {kind}:{facet_id}.prompt_adapter must contain Chinese guidance")
            normalized[str(facet_id)] = {
                **deepcopy(value),
                "source": f"config/story_facets/{kind}.yaml#{facet_id}",
                "sha256": sha256(
                    json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            }
        registries[kind] = normalized
    return registries


def project_active_facet_adapters(
    project_root: Path,
    *,
    chapter_number: int = 0,
    requested: list[str] | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Resolve at most three current Chinese facet adapters with stable provenance."""

    root = project_root.resolve()
    project_file = root / "project.yaml"
    if not project_file.is_file():
        return []
    try:
        project = yaml.safe_load(project_file.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError):
        return []
    profile = project.get("story_profile") if isinstance(project, dict) else None
    if not isinstance(profile, dict):
        return []
    chapter_requested = list(requested or [])
    if chapter_number > 0 and not chapter_requested:
        plan_file = root / "20_outline" / "chapter_plan.json"
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8")) if plan_file.is_file() else []
        except (OSError, UnicodeError, json.JSONDecodeError):
            plan = []
        rows = plan if isinstance(plan, list) else plan.get("chapters", []) if isinstance(plan, dict) else []
        row = next(
            (
                item for item in rows
                if isinstance(item, dict) and int(item.get("chapter_number") or 0) == chapter_number
            ),
            None,
        )
        if isinstance(row, dict):
            chapter_requested = [str(item) for item in row.get("active_facets") or []]
    try:
        compiled = compile_story_profile(profile, market_ids=set(BUILTIN_MARKET_IDS))
    except StoryProfileError:
        return []
    return [
        {
            "kind": str(item["kind"]),
            "id": str(item["id"]),
            "level": str(item["level"]),
            "source": str(item["source"]),
            "sha256": str(item["sha256"]),
            "prompt_adapter": str(item["prompt_adapter"]).strip(),
        }
        for item in active_story_facets(compiled, chapter_requested, limit=limit)
    ]


def normalize_facet_selection(kind: str, raw: Any, registry: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    if kind in {"setting", "plot_engines"}:
        if not isinstance(raw, dict):
            raise StoryProfileError(f"story_profile.{kind} must be a mapping")
        result: list[dict[str, str]] = []
        primary = str(raw.get("primary") or "")
        if primary:
            result.append({"id": primary, "level": "primary"})
        secondary_key = "secondary" if kind == "setting" else "supporting"
        for facet_id in raw.get(secondary_key, []) if isinstance(raw.get(secondary_key, []), list) else []:
            result.append({"id": str(facet_id), "level": "supporting"})
        for facet_id in raw.get("accent", []) if isinstance(raw.get("accent", []), list) else []:
            result.append({"id": str(facet_id), "level": "accent"})
    else:
        if not isinstance(raw, list):
            raise StoryProfileError(f"story_profile.{kind} must be a list")
        result = []
        for item in raw:
            if isinstance(item, str):
                result.append({"id": item, "level": "supporting"})
            elif isinstance(item, dict) and set(item) == {"id", "level"}:
                result.append({"id": str(item["id"]), "level": str(item["level"])})
            else:
                raise StoryProfileError(f"story_profile.{kind} entries must be ids or id/level mappings")
    seen: set[str] = set()
    for item in result:
        if item["id"] not in registry:
            raise StoryProfileError(f"unknown story facet: {kind}:{item['id']}")
        if item["level"] not in SELECTION_LEVELS:
            raise StoryProfileError(f"invalid story facet level: {item['level']}")
        if item["id"] in seen:
            raise StoryProfileError(f"duplicate story facet: {kind}:{item['id']}")
        seen.add(item["id"])
    return result


def detect_conflicts(selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = {f"{item['kind']}:{item['id']}" for item in selected}
    conflicts: dict[str, dict[str, Any]] = {}
    for item in selected:
        source_key = f"{item['kind']}:{item['id']}"
        for target_key in item.get("conflicts", []):
            if target_key not in keys:
                continue
            pair = sorted((source_key, target_key))
            conflict_id = "conflict:" + ":".join(pair)
            conflicts[conflict_id] = {
                "conflict_id": conflict_id,
                "facets": pair,
                "blocking": True,
                "requires_human_resolution": True,
            }
    return [conflicts[key] for key in sorted(conflicts)]


def collect_entries(selected: list[dict[str, Any]], field: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for facet in selected:
        source = f"{facet['kind']}:{facet['id']}"
        for value in facet.get(field, []):
            key = (source, value)
            if key in seen:
                continue
            seen.add(key)
            entries.append({"source": source, "level": str(facet["level"]), "text": value})
    return entries
