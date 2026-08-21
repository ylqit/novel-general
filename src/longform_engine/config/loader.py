"""Load and validate longform novel project configuration."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any
import copy
import re

import yaml

from longform_engine.lengths import LengthContractError, validate_length_contract
from longform_engine.resources import resource_path
from longform_engine.story_profiles import StoryProfileError, validate_story_profile
from longform_engine.vector_backends import IMPLEMENTED_VECTOR_BACKENDS


class ConfigError(ValueError):
    """Raised when a project configuration cannot be loaded or validated."""


RETIRED_PATH_PREFIXES = (
    "00_bible",
    "01_outline",
    "02_memory",
    "03_manuscript",
    "04_editing",
    "05_rag",
    "06_runtime",
)


CREATION_MODES = {
    "original",
    "fanfiction",
    "adaptation_study",
    "inspired_original",
}
FANFICTION_CONTINUITY_MODES = {
    "canon_compliant",
    "canon_divergent",
    "alternate_universe",
    "continuation",
    "prequel",
    "crossover",
}
FANFICTION_RIGHTS_STATUSES = {
    "user_claimed_authorized",
    "public_domain_claimed",
    "platform_permitted_claimed",
    "unverified",
}
MARKET_PROFILES = {
    "general_cn",
    "qidian_male",
    "fanqie_free",
    "jinjiang_female",
}
QUALITY_PHASES = {
    "auto",
    "aftermath",
    "early_serial",
    "opening",
    "stable_serial",
    "volume_climax",
}

OPEN_MAPPING_PATHS = {"quality.profile.overrides"}
REMOVED_CONFIG_FIELDS = {
    "engine": "Engine identity and filesystem mode are runtime invariants; remove the engine section.",
    "workflow": "Workflow gates are enforced by the production state machine; remove the workflow section.",
    "storage": "The canonical storage layout is fixed by storage.layout; remove the storage section.",
    "memory": "Memory paths and backend are canonical runtime ownership; remove the memory section.",
    "graph": "Graph storage and SQLite materialization are canonical runtime ownership; remove the graph section.",
    "revision": "Rollback always uses the transaction and snapshot policy; remove the revision section.",
    "codex": "Host-neutral workflow settings belong under writing.agent; remove the codex section.",
    "project.language": "The engine currently supports the zh-CN project contract only.",
    "project.timezone": "Runtime timestamps are canonical UTC and do not use a project timezone.",
    "writing.agent.require_submit_command": "Draft submission is mandatory and no longer configurable.",
    "writing.template_dry_run": "Select template dry-run with writing.mode; remove this no-op section.",
    "quality.assurance_mode": "Use quality.profile.strictness.",
    "quality.approved_style_baseline": "Manage approved style samples with the quality baseline CLI.",
    "quality.creative_guidance": "Guided creative interaction is a schema v2 invariant.",
    "quality.reader_payoff.structure_window": "The retired structure-pattern analyzer no longer consumes this field.",
    "quality.reader_payoff.language_similarity_threshold": "The retired structure-pattern analyzer no longer consumes this field.",
    "quality.repair.max_content_rounds": "The repair budget is fixed at two content rounds.",
    "rag.enabled": "RAG is part of the production pipeline and is not optional.",
    "rag.backend": "Use semantic.vector_store.backend for the implemented storage backend.",
    "rag.small_context_top_k": "Use rag.top_k or an explicit command argument.",
    "rag.embedding": "Use semantic.profile for the model pair.",
    "rag.reranker": "Use semantic.profile for the model pair.",
    "rag.retrieval": "Hybrid retrieval and fusion are runtime invariants.",
    "rag.write_next_plot_context": "Next-context materialization is a runtime invariant.",
    "rag.query_cache": "Query-cache lifecycle is managed by the RAG pipeline.",
    "semantic.fallback_profile": "Fallback behavior is controlled by semantic.allow_fallback.",
    "semantic.vector_store.api_key_env": "Only local vector backends are implemented.",
    "semantic.vector_store.collection": "Local vector storage uses one fixed project-owned collection.",
    "gates.block_on_previous_failure": "Previous gate failure always blocks progression.",
    "gates.artifact_dir": "Gate artifacts use the canonical 50_workbench/gate_artifacts path.",
    "gates.required_files": "Required gate artifacts are enforced by the gate schema.",
    "gates.allowed_actions_after_failure": "Failure actions are determined by the production state machine.",
    "gates.mainline_info_release_warning_hits": (
        "v0.5 uses gates.mainline_reveal_warning_hits and does not migrate v0.4 project configuration."
    ),
    "pacing.event_quota_window_chapters": "Use pacing.soft_event_window_chapters.",
    "pacing.quota_types": "Event types are configured by pacing.event_types.",
    "research.enabled": "Research commands are explicitly invoked and do not use an enable switch.",
    "research.default_ingestion": "Research ingestion is always reviewed-inbox first.",
    "research.promote_requires_approval": "Research promotion always requires explicit approval.",
}
CONFIG_OWNER_PREFIXES = {
    "schema_version": "config.loader",
    "creation": "creative.pipeline",
    "fanfiction": "intelligence.pipeline",
    "project": "storage.project",
    "novel": "orchestration.pipeline",
    "length": "lengths",
    "story_profile": "story_profiles",
    "writing": "orchestration.pipeline",
    "quality": "quality.contracts",
    "editorial": "editorial.pipeline",
    "rag": "rag.pipeline",
    "semantic": "models.pipeline/vectorstore.pipeline",
    "gates": "gates.pipeline",
    "pacing": "planning.pipeline/gates.pipeline",
    "research": "research.pipeline",
    "quality.semantic_pacing": "gates.pipeline",
    "quality.humanizer": "creative.pipeline",
    "quality.reader_payoff": "quality.review",
    "quality.repair": "repair_coordination",
}


@dataclass(frozen=True)
class ConfigDocument:
    """A loaded project config plus provenance useful for Agent reporting."""

    data: dict[str, Any]
    path: Path | None
    sources: tuple[str, ...]
    field_sources: dict[str, str] = dataclass_field(default_factory=dict)
    defaults: dict[str, Any] = dataclass_field(default_factory=dict)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base and return base."""

    for key, value in override.items():
        if (
            isinstance(value, dict)
            and isinstance(base.get(key), dict)
        ):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def _validate_overlay_keys(
    overlay: dict[str, Any],
    reference: dict[str, Any],
    *,
    prefix: str = "",
) -> None:
    """Reject misspelled and retired fields before they can disappear into a deep merge."""

    for key, value in overlay.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if path in REMOVED_CONFIG_FIELDS:
            raise ConfigError(f"Removed config field {path}: {REMOVED_CONFIG_FIELDS[path]}")
        if key not in reference:
            raise ConfigError(f"Unknown config field: {path}")
        expected = reference[key]
        if isinstance(value, dict):
            if not isinstance(expected, dict):
                raise ConfigError(f"Config field {path} must not be a mapping")
            if path not in OPEN_MAPPING_PATHS:
                _validate_overlay_keys(value, expected, prefix=path)


def _flatten_fields(data: dict[str, Any], *, prefix: str = "") -> list[tuple[str, Any]]:
    fields: list[tuple[str, Any]] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            fields.extend(_flatten_fields(value, prefix=path))
        else:
            fields.append((path, value))
    return fields


def _mark_field_sources(data: dict[str, Any], source: str, result: dict[str, str], *, prefix: str = "") -> None:
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and value:
            _mark_field_sources(value, source, result, prefix=path)
        else:
            result[path] = source


def config_field_registry(config: ConfigDocument) -> tuple[dict[str, Any], ...]:
    """Describe every effective public field with its default, source, type, and runtime owner."""

    defaults = dict(_flatten_fields(config.defaults))
    rows: list[dict[str, Any]] = []
    for path, value in _flatten_fields(config.data):
        owner_prefix = max(
            (prefix for prefix in CONFIG_OWNER_PREFIXES if path == prefix or path.startswith(prefix + ".")),
            key=len,
        )
        rows.append(
            {
                "path": path,
                "type": _config_type(value),
                "default": defaults.get(path),
                "value": value,
                "source": config.field_sources.get(path, config.sources[0] if config.sources else ""),
                "owner": CONFIG_OWNER_PREFIXES[owner_prefix],
            }
        )
    return tuple(rows)


def _config_type(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "mapping"
    if value is None:
        return "null"
    return type(value).__name__


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file as a dictionary."""

    if not path.exists():
        raise ConfigError(f"Config file does not exist: {path}")
    raw = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(raw) or {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"Config file must contain a YAML mapping: {path}")
    return loaded


def template_path(template: str) -> Path:
    """Resolve a named project template."""

    try:
        return resource_path("templates", template, "project.yaml")
    except FileNotFoundError as exc:
        raise ConfigError(f"Unknown template '{template}'.") from exc


def load_project_config(
    config_path: str | Path | None = None,
    *,
    template: str | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ConfigDocument:
    """Load config from the packaged engine defaults, then apply project and CLI overrides."""

    try:
        default_config = resource_path("config", "default.engine.yaml")
    except (FileNotFoundError, RuntimeError) as exc:
        raise ConfigError("Packaged config/default.engine.yaml is unavailable or incomplete.") from exc
    defaults = read_yaml(default_config)
    data = copy.deepcopy(defaults)
    sources: list[str] = [str(default_config)]
    field_sources = {path: str(default_config) for path, _value in _flatten_fields(defaults)}

    resolved_path: Path | None = None
    if template and config_path:
        raise ConfigError("Use either config_path or template, not both.")
    if template:
        resolved_path = template_path(template)
    elif config_path:
        resolved_path = Path(config_path).expanduser().resolve()

    if resolved_path:
        overlay = read_yaml(resolved_path)
        _validate_overlay_keys(overlay, defaults)
        deep_merge(data, overlay)
        sources.append(str(resolved_path))
        _mark_field_sources(overlay, str(resolved_path), field_sources)

    if cli_overrides:
        _validate_overlay_keys(cli_overrides, defaults)
        deep_merge(data, cli_overrides)
        sources.append("cli overrides")
        _mark_field_sources(cli_overrides, "cli overrides", field_sources)

    validate_config(data)
    return ConfigDocument(
        data=data,
        path=resolved_path,
        sources=tuple(sources),
        field_sources=field_sources,
        defaults=copy.deepcopy(defaults),
    )


def validate_config(data: dict[str, Any]) -> None:
    """Validate the minimal contract needed by the engine bootstrap."""

    if data.get("schema_version") != 2:
        raise ConfigError("schema_version must be 2; non-current project configs are not loaded")

    creation = _require_mapping(data, "creation")
    creation_mode = str(creation.get("mode") or "").strip()
    if creation_mode not in CREATION_MODES:
        raise ConfigError(f"creation.mode must be one of: {', '.join(sorted(CREATION_MODES))}")

    fanfiction = _require_mapping(data, "fanfiction")
    continuity_mode = str(fanfiction.get("continuity_mode") or "").strip()
    if continuity_mode not in FANFICTION_CONTINUITY_MODES:
        raise ConfigError(
            "fanfiction.continuity_mode must be one of: "
            + ", ".join(sorted(FANFICTION_CONTINUITY_MODES))
        )
    sources = fanfiction.get("sources")
    if not isinstance(sources, list):
        raise ConfigError("fanfiction.sources must be a list")
    if creation_mode == "fanfiction" and not sources:
        raise ConfigError("fanfiction.sources must contain at least one source when creation.mode is fanfiction")
    source_ids: set[str] = set()
    for index, source in enumerate(sources):
        _validate_fanfiction_source(source, index=index, source_ids=source_ids)
    if creation_mode == "fanfiction" and continuity_mode == "crossover" and len(sources) < 2:
        raise ConfigError("fanfiction crossover mode requires at least two sources")

    project = _require_mapping(data, "project")
    for field in ("slug", "title", "root_dir"):
        if not str(project.get(field, "")).strip():
            raise ConfigError(f"project.{field} is required")

    length = _require_mapping(data, "length")
    try:
        validate_length_contract(length)
    except LengthContractError as exc:
        raise ConfigError(str(exc)) from exc

    try:
        validate_story_profile(data.get("story_profile"), market_ids=MARKET_PROFILES)
    except StoryProfileError as exc:
        raise ConfigError(str(exc)) from exc

    _reject_retired_paths(data)

    writing = _require_mapping(data, "writing")
    mode = str(writing.get("mode", "")).strip()
    allowed_modes = {"agent_skill", "template_dry_run"}
    if mode not in allowed_modes:
        raise ConfigError(f"writing.mode `{mode}` must be one of: {', '.join(sorted(allowed_modes))}")
    agent = _require_mapping(writing, "agent", "writing")
    context = _require_mapping(agent, "context", "writing.agent")
    if str(context.get("mode") or "") != "adaptive":
        raise ConfigError("writing.agent.context.mode must be adaptive")
    if str(context.get("host_profile") or "") not in {"compact", "standard", "large"}:
        raise ConfigError(
            "writing.agent.context.host_profile must be one of: compact, standard, large"
        )
    override = context.get("capacity_override_units")
    context_registry = yaml.safe_load(
        resource_path("config", "agent_context_profiles.yaml").read_text(encoding="utf-8")
    ) or {}
    minimum_capacity = int(context_registry.get("minimum_capacity_units") or 1)
    if override is not None and (
        not isinstance(override, int) or isinstance(override, bool) or override < minimum_capacity
    ):
        raise ConfigError(
            "writing.agent.context.capacity_override_units must be null or meet the resource-defined minimum"
        )
    if str(context.get("overflow_policy") or "") != "split_context":
        raise ConfigError("writing.agent.context.overflow_policy must be split_context")

    semantic = _require_mapping(data, "semantic")
    vector_store = _require_mapping(semantic, "vector_store", "semantic")
    vector_backend = str(vector_store.get("backend") or "").strip()
    if vector_backend not in IMPLEMENTED_VECTOR_BACKENDS:
        raise ConfigError(
            "semantic.vector_store.backend must be one of: "
            + ", ".join(sorted(IMPLEMENTED_VECTOR_BACKENDS))
        )
    metric = str(vector_store.get("metric") or "").strip()
    if metric not in {"cosine", "l2", "ip"}:
        raise ConfigError("semantic.vector_store.metric must be one of: cosine, l2, ip")
    for field in (
        "dim",
        "hnsw_threshold",
        "hnsw_m",
        "hnsw_ef_construction",
        "hnsw_ef_search",
        "hnsw_candidate_multiplier",
    ):
        _require_positive_int(vector_store, field, "semantic.vector_store")

    quality = _require_mapping(data, "quality")
    profile = quality.get("profile")
    if profile is not None and not isinstance(profile, dict):
        raise ConfigError("quality.profile must be a mapping")
    profile = profile if isinstance(profile, dict) else {}
    removed_profile_fields = {"market", "compatibility_markets", "genre"} & set(profile)
    if removed_profile_fields or "market_profile" in quality or "genre_profile" in quality:
        raise ConfigError(
            "market and genre composition belong to story_profile; remove: "
            + ", ".join(sorted(removed_profile_fields | ({"quality.market_profile"} if "market_profile" in quality else set()) | ({"quality.genre_profile"} if "genre_profile" in quality else set())))
        )
    phase = str(profile.get("phase") or "auto").strip()
    if phase not in QUALITY_PHASES:
        raise ConfigError(f"quality.profile.phase must be one of: {', '.join(sorted(QUALITY_PHASES))}")
    strictness = str(profile.get("strictness") or "").strip()
    if strictness not in {"light", "balanced", "strict"}:
        raise ConfigError("quality.profile.strictness must be one of: light, balanced, strict")
    overrides = profile.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ConfigError("quality.profile.overrides must be a mapping")
    platform_policy = overrides.get("platform_policy", {})
    if platform_policy is None:
        platform_policy = {}
    elif not isinstance(platform_policy, dict):
        raise ConfigError("quality.profile.overrides.platform_policy must be a mapping")
    primary_deviation = str(platform_policy.get("primary_deviation") or "P2_advisory")
    if primary_deviation not in {"P2_advisory", "P1_blocking"}:
        raise ConfigError(
            "quality.profile.overrides.platform_policy.primary_deviation must be one of: "
            "P2_advisory, P1_blocking"
        )
    milestones = quality.get("semantic_review_milestones")
    if (
        not isinstance(milestones, list)
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in milestones)
    ):
        raise ConfigError("quality.semantic_review_milestones must be a list of positive integers")
    if not isinstance(quality.get("semantic_review_boundaries"), bool):
        raise ConfigError("quality.semantic_review_boundaries must be boolean")
    reader_payoff = _require_mapping(quality, "reader_payoff", "quality")
    payoff_mode = str(reader_payoff.get("review_mode") or "").strip()
    if payoff_mode not in {"risk_based", "always"}:
        raise ConfigError("quality.reader_payoff.review_mode must be one of: risk_based, always")
    repair = _require_mapping(quality, "repair", "quality")
    selected_p2_codes = repair.get("selected_p2_codes")
    if (
        not isinstance(selected_p2_codes, list)
        or any(not isinstance(item, str) or not item.strip() for item in selected_p2_codes)
        or len(set(selected_p2_codes)) != len(selected_p2_codes)
    ):
        raise ConfigError("quality.repair.selected_p2_codes must be a unique list of non-empty strings")
    humanizer = _require_mapping(quality, "humanizer", "quality")
    semantic_mode = str(humanizer.get("semantic_review_mode") or "").strip()
    if semantic_mode not in {"risk_based", "always"}:
        raise ConfigError("quality.humanizer.semantic_review_mode must be one of: risk_based, always")
    semantic_pacing = _require_mapping(quality, "semantic_pacing", "quality")
    pacing_review_mode = str(semantic_pacing.get("review_mode") or "").strip()
    if pacing_review_mode not in {"off", "risk_based", "required"}:
        raise ConfigError("quality.semantic_pacing.review_mode must be one of: off, required, risk_based")
    rag = _require_mapping(data, "rag")
    candidate_pool_size = _require_positive_int(rag, "candidate_pool_size", "rag")
    top_k = _require_positive_int(rag, "top_k", "rag")
    if candidate_pool_size < top_k:
        raise ConfigError("rag.candidate_pool_size must be greater than or equal to rag.top_k")
    chunk_max = _require_positive_int(rag, "chunk_max_chars", "rag")
    chunk_overlap = rag.get("chunk_overlap_chars")
    if not isinstance(chunk_overlap, int) or isinstance(chunk_overlap, bool) or chunk_overlap < 0:
        raise ConfigError("rag.chunk_overlap_chars must be a non-negative integer")
    if chunk_overlap >= chunk_max:
        raise ConfigError("rag.chunk_overlap_chars must be lower than rag.chunk_max_chars")
    weights = [
        _require_ratio(rag, field, "rag")
        for field in ("semantic_weight", "keyword_weight", "metadata_weight")
    ]
    if abs(sum(weights) - 1.0) > 1e-9:
        raise ConfigError("rag semantic_weight, keyword_weight, and metadata_weight must sum to 1.0")

    pacing = _require_mapping(data, "pacing")
    if str(pacing.get("default_mode") or "") not in {"balanced", "fast", "measured"}:
        raise ConfigError("pacing.default_mode must be one of: balanced, fast, measured")
    for field in (
        "fast_chapter_cooldown",
        "max_major_quota_triggers_per_chapter",
        "soft_event_window_chapters",
        "max_consecutive_fast_chapters",
        "fast_chapter_quota_per_volume",
    ):
        _require_positive_int(pacing, field, "pacing")
    event_types = pacing.get("event_types")
    if not isinstance(event_types, list) or not event_types or any(not str(item).strip() for item in event_types):
        raise ConfigError("pacing.event_types must be a non-empty list")
    cooldown = _require_mapping(pacing, "event_cooldown", "pacing")
    if set(cooldown) != set(event_types):
        raise ConfigError("pacing.event_cooldown must define every pacing.event_types item exactly once")
    for event_type in event_types:
        _require_positive_int(cooldown, str(event_type), "pacing.event_cooldown")
    volume_distribution = pacing.get("volume_distribution")
    if (
        not isinstance(volume_distribution, list)
        or not volume_distribution
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in volume_distribution)
    ):
        raise ConfigError("pacing.volume_distribution must be a non-empty list of positive integers")

    editorial = _require_mapping(data, "editorial")
    if str(editorial.get("review_mode") or "") not in {"off", "risk_based", "always"}:
        raise ConfigError("editorial.review_mode must be one of: off, risk_based, always")
    roles = editorial.get("review_roles")
    if not isinstance(roles, list) or any(not isinstance(item, str) or not item.strip() for item in roles):
        raise ConfigError("editorial.review_roles must be a list of non-empty strings")
    _require_positive_int(editorial, "conditional_pass_limit", "editorial")

    gates = _require_mapping(data, "gates")
    forbidden_reveals = gates.get("forbidden_reveals")
    if not isinstance(forbidden_reveals, list) or any(
        not isinstance(item, str) or not item.strip() for item in forbidden_reveals
    ):
        raise ConfigError("gates.forbidden_reveals must be a list of non-empty strings")
    _require_positive_int(gates, "mainline_reveal_warning_hits", "gates")
    p0_patterns = gates.get("p0_meta_pollution_patterns")
    if not isinstance(p0_patterns, list) or not p0_patterns or any(
        not isinstance(item, str) or not item.strip() for item in p0_patterns
    ):
        raise ConfigError("gates.p0_meta_pollution_patterns must be a non-empty list of strings")

    research = _require_mapping(data, "research")
    if not isinstance(research.get("web_search_enabled"), bool):
        raise ConfigError("research.web_search_enabled must be boolean")
    if str(research.get("search_provider") or "") not in {
        "zh.wikipedia",
        "static_fallback",
        "duckduckgo_html",
    }:
        raise ConfigError(
            "research.search_provider must be one of: duckduckgo_html, static_fallback, zh.wikipedia"
        )
    _require_positive_int(research, "search_limit", "research")
    _require_positive_int(research, "network_timeout_seconds", "research")
    for field in ("inbox_dir", "impact_report_dir", "canon_file", "impact_ledger"):
        if not isinstance(research.get(field), str) or not str(research[field]).strip():
            raise ConfigError(f"research.{field} must be a non-empty path string")


def _validate_fanfiction_source(source: Any, *, index: int, source_ids: set[str]) -> None:
    prefix = f"fanfiction.sources[{index}]"
    if not isinstance(source, dict):
        raise ConfigError(f"{prefix} must be a mapping")
    for field in ("source_id", "title", "creator", "canon_cutoff", "rights_status"):
        if not isinstance(source.get(field), str) or not source[field].strip():
            raise ConfigError(f"{prefix}.{field} is required")
    source_id = str(source["source_id"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,79}", source_id):
        raise ConfigError(f"{prefix}.source_id must be a stable 2-80 character id")
    if source_id in source_ids:
        raise ConfigError(f"{prefix}.source_id is duplicated: {source_id}")
    source_ids.add(source_id)
    if source["rights_status"] not in FANFICTION_RIGHTS_STATUSES:
        raise ConfigError(
            f"{prefix}.rights_status must be one of: {', '.join(sorted(FANFICTION_RIGHTS_STATUSES))}"
        )
    if not isinstance(source.get("commercial_intent"), bool):
        raise ConfigError(f"{prefix}.commercial_intent must be boolean")
    allowed_elements = source.get("allowed_elements")
    if not isinstance(allowed_elements, list) or any(
        not isinstance(item, str) or not item.strip() for item in allowed_elements
    ):
        raise ConfigError(f"{prefix}.allowed_elements must be a list of non-empty strings")
    platform_policy_url = source.get("platform_policy_url", "")
    if not isinstance(platform_policy_url, str):
        raise ConfigError(f"{prefix}.platform_policy_url must be a string")


def _reject_retired_paths(data: Any, path: str = "config") -> None:
    """Reject retired path families inside longform project configs."""

    if isinstance(data, dict):
        for key, value in data.items():
            _reject_retired_paths(value, f"{path}.{key}")
        return
    if isinstance(data, list):
        for index, value in enumerate(data):
            _reject_retired_paths(value, f"{path}[{index}]")
        return
    if not isinstance(data, str):
        return

    normalized = data.replace("\\", "/").strip()
    for prefix in RETIRED_PATH_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            raise ConfigError(
                f"{path} uses retired path '{data}'. "
                "longform-novel-engine projects must use 10_bible/20_outline/30_state/"
                "40_manuscript/50_workbench/60_rag/70_runtime."
            )


def _require_mapping(data: dict[str, Any], key: str, prefix: str | None = None) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        dotted = f"{prefix}.{key}" if prefix else key
        raise ConfigError(f"{dotted} must be a mapping")
    return value


def _require_positive_int(data: dict[str, Any], key: str, prefix: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{prefix}.{key} must be a positive integer")
    return value


def _require_ratio(data: dict[str, Any], key: str, prefix: str) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1:
        raise ConfigError(f"{prefix}.{key} must be a number between 0 and 1")
    return float(value)
