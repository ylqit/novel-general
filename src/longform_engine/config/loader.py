"""Load and validate longform novel project configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import copy
import re

import yaml

from longform_engine.resources import resource_path, resource_root


class ConfigError(ValueError):
    """Raised when a project configuration cannot be loaded or validated."""


LEGACY_PATH_PREFIXES = (
    "00_bible",
    "01_outline",
    "02_memory",
    "03_manuscript",
    "04_editing",
    "05_rag",
    "06_runtime",
)


BUILTIN_DEFAULTS: dict[str, Any] = {
    "schema_version": 1,
    "creation": {
        "mode": "original",
    },
    "fanfiction": {
        "continuity_mode": "canon_compliant",
        "sources": [],
    },
    "project": {
        "slug": "untitled_longform",
        "title": "未命名长篇小说",
        "root_dir": "novels/untitled_longform",
        "language": "zh-CN",
        "timezone": "Asia/Hong_Kong",
    },
    "length": {
        "total_chapters": 500,
        "target_total_words": 1_500_000,
        "volume_count": 6,
        "chapter_word_count": {
            "target": 3000,
            "min": 2400,
            "max": 3600,
            "hard_min": 2000,
            "hard_max": 4200,
        },
    },
    "storage": {
        "layout_version": 2,
        "filesystem_primary": True,
        "runtime_database": "70_runtime/db/longform_engine.sqlite",
        "directories": {
            "governance": "00_governance",
            "bible": "10_bible",
            "outline": "20_outline",
            "state": "30_state",
            "manuscript": "40_manuscript",
            "workbench": "50_workbench",
            "rag": "60_rag",
            "runtime": "70_runtime",
            "exports": "80_exports",
        },
    },
    "writing": {
        "mode": "agent_skill",
        "agent": {
            "task_dir": "50_workbench/writing_tasks",
            "draft_dir": "50_workbench/agent_drafts",
            "require_submit_command": True,
            "default_agent": "codex",
        },
        "template_dry_run": {
            "enabled": False,
        },
    },
    "quality": {
        "assurance_mode": "balanced",
        "profile": {
            "market": "qidian_male",
            "compatibility_markets": ["fanqie_free"],
            "genre": "xuanhuan",
            "phase": "auto",
            "strictness": "balanced",
            "overrides": {},
        },
        "semantic_review_milestones": [1, 3, 10, 30],
        "semantic_review_boundaries": True,
        "reader_payoff": {
            "review_mode": "risk_based",
            "structure_window": 20,
            "language_similarity_threshold": 0.72,
        },
        "humanizer": {
            "changed_character_warning_ratio": 0.35,
            "changed_character_human_ratio": 0.60,
            "semantic_review_mode": "risk_based",
            "semantic_review_change_ratio": 0.15,
        },
        "approved_style_baseline": {
            "chapters": [],
            "update_requires_human": True,
        },
        "creative_guidance": {
            "mode": "automatic",
        },
    },
}

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
GENRE_PROFILES = {
    "history",
    "romance",
    "suspense",
    "urban",
    "xuanhuan",
}
QUALITY_PHASES = {
    "auto",
    "aftermath",
    "early_serial",
    "opening",
    "stable_serial",
    "volume_climax",
}
VECTOR_STORE_BACKENDS = {
    "local_sqlite",
    "local_hnsw",
    "milvus",
    "pgvector",
    "elasticsearch",
}


@dataclass(frozen=True)
class ConfigDocument:
    """A loaded project config plus provenance useful for Agent reporting."""

    data: dict[str, Any]
    path: Path | None
    sources: tuple[str, ...]


def repo_root() -> Path:
    """Return the active resource root for compatibility with older callers."""

    return resource_root()


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
    """Load config with built-in defaults, engine defaults, template/project config, and overrides."""

    data = copy.deepcopy(BUILTIN_DEFAULTS)
    sources: list[str] = ["builtin defaults"]

    default_config = resource_path("config", "default.engine.yaml")
    deep_merge(data, read_yaml(default_config))
    sources.append(str(default_config))

    resolved_path: Path | None = None
    if template and config_path:
        raise ConfigError("Use either config_path or template, not both.")
    if template:
        resolved_path = template_path(template)
    elif config_path:
        resolved_path = Path(config_path).expanduser().resolve()

    if resolved_path:
        deep_merge(data, read_yaml(resolved_path))
        sources.append(str(resolved_path))

    if cli_overrides:
        deep_merge(data, cli_overrides)
        sources.append("cli overrides")

    validate_config(data)
    return ConfigDocument(data=data, path=resolved_path, sources=tuple(sources))


def validate_config(data: dict[str, Any]) -> None:
    """Validate the minimal contract needed by the engine bootstrap."""

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
    total_chapters = _require_positive_int(length, "total_chapters", "length")
    volume_count = _require_positive_int(length, "volume_count", "length")
    if volume_count > total_chapters:
        raise ConfigError("length.volume_count cannot exceed length.total_chapters")

    word_count = _require_mapping(length, "chapter_word_count", "length")
    target = _require_positive_int(word_count, "target", "length.chapter_word_count")
    minimum = _require_positive_int(word_count, "min", "length.chapter_word_count")
    maximum = _require_positive_int(word_count, "max", "length.chapter_word_count")
    if not minimum <= target <= maximum:
        raise ConfigError("chapter_word_count target must be between min and max")

    storage = _require_mapping(data, "storage")
    directories = _require_mapping(storage, "directories", "storage")
    required_dirs = {
        "governance",
        "bible",
        "outline",
        "state",
        "manuscript",
        "workbench",
        "rag",
        "runtime",
        "exports",
    }
    missing = sorted(name for name in required_dirs if not directories.get(name))
    if missing:
        raise ConfigError(f"storage.directories missing: {', '.join(missing)}")
    _reject_legacy_paths(data)

    writing = _require_mapping(data, "writing")
    mode = str(writing.get("mode", "")).strip()
    allowed_modes = {"agent_skill", "template_dry_run"}
    if mode not in allowed_modes:
        raise ConfigError(f"writing.mode `{mode}` must be one of: {', '.join(sorted(allowed_modes))}")

    semantic = _require_mapping(data, "semantic")
    vector_store = _require_mapping(semantic, "vector_store", "semantic")
    vector_backend = str(vector_store.get("backend") or "").strip()
    if vector_backend not in VECTOR_STORE_BACKENDS:
        raise ConfigError(
            "semantic.vector_store.backend must be one of: "
            + ", ".join(sorted(VECTOR_STORE_BACKENDS))
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
    market_profile = str(profile.get("market") or quality.get("market_profile") or "").strip()
    if market_profile not in MARKET_PROFILES:
        raise ConfigError(f"quality.profile.market must be one of: {', '.join(sorted(MARKET_PROFILES))}")
    compatibility_markets = profile.get("compatibility_markets", [])
    if not isinstance(compatibility_markets, list):
        raise ConfigError("quality.profile.compatibility_markets must be a list")
    for item in compatibility_markets:
        compatibility_market = str(item).strip() if isinstance(item, str) else ""
        if compatibility_market not in MARKET_PROFILES:
            raise ConfigError(
                f"quality.profile.compatibility_markets must contain only: {', '.join(sorted(MARKET_PROFILES))}"
            )
    genre_profile = str(profile.get("genre") or quality.get("genre_profile") or "").strip()
    if genre_profile not in GENRE_PROFILES:
        raise ConfigError(f"quality.profile.genre must be one of: {', '.join(sorted(GENRE_PROFILES))}")
    phase = str(profile.get("phase") or "auto").strip()
    if phase not in QUALITY_PHASES:
        raise ConfigError(f"quality.profile.phase must be one of: {', '.join(sorted(QUALITY_PHASES))}")
    strictness = str(profile.get("strictness") or quality.get("assurance_mode") or "").strip()
    if strictness not in {"light", "balanced", "strict"}:
        raise ConfigError("quality.profile.strictness must be one of: light, balanced, strict")
    overrides = profile.get("overrides", {})
    if not isinstance(overrides, dict):
        raise ConfigError("quality.profile.overrides must be a mapping")
    platform_policy = overrides.get("platform_policy", {})
    if platform_policy is not None and not isinstance(platform_policy, dict):
        raise ConfigError("quality.profile.overrides.platform_policy must be a mapping")
    primary_deviation = str(platform_policy.get("primary_deviation") or "P2_advisory")
    if primary_deviation not in {"P2_advisory", "P1_blocking"}:
        raise ConfigError(
            "quality.profile.overrides.platform_policy.primary_deviation must be one of: "
            "P2_advisory, P1_blocking"
        )
    assurance_mode = str(quality.get("assurance_mode") or "").strip()
    if assurance_mode not in {"light", "balanced", "strict"}:
        raise ConfigError("quality.assurance_mode must be one of: light, balanced, strict")
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
    structure_window = reader_payoff.get("structure_window")
    if (
        not isinstance(structure_window, int)
        or isinstance(structure_window, bool)
        or not 10 <= structure_window <= 20
    ):
        raise ConfigError("quality.reader_payoff.structure_window must be an integer between 10 and 20")
    _require_ratio(reader_payoff, "language_similarity_threshold", "quality.reader_payoff")
    humanizer = _require_mapping(quality, "humanizer", "quality")
    warning_ratio = _require_ratio(humanizer, "changed_character_warning_ratio", "quality.humanizer")
    human_ratio = _require_ratio(humanizer, "changed_character_human_ratio", "quality.humanizer")
    semantic_ratio = _require_ratio(humanizer, "semantic_review_change_ratio", "quality.humanizer")
    semantic_mode = str(humanizer.get("semantic_review_mode") or "").strip()
    if semantic_mode not in {"risk_based", "always"}:
        raise ConfigError("quality.humanizer.semantic_review_mode must be one of: risk_based, always")
    if warning_ratio >= human_ratio:
        raise ConfigError(
            "quality.humanizer.changed_character_warning_ratio must be lower than changed_character_human_ratio"
        )
    if semantic_ratio >= human_ratio:
        raise ConfigError(
            "quality.humanizer.semantic_review_change_ratio must be lower than changed_character_human_ratio"
        )
    approved_baseline = _require_mapping(quality, "approved_style_baseline", "quality")
    approved_chapters = approved_baseline.get("chapters")
    if (
        not isinstance(approved_chapters, list)
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in approved_chapters)
        or len(set(approved_chapters)) != len(approved_chapters)
    ):
        raise ConfigError("quality.approved_style_baseline.chapters must be a unique list of positive integers")
    if not isinstance(approved_baseline.get("update_requires_human"), bool):
        raise ConfigError("quality.approved_style_baseline.update_requires_human must be boolean")
    creative_guidance = _require_mapping(quality, "creative_guidance", "quality")
    guidance_mode = str(creative_guidance.get("mode") or "").strip()
    if guidance_mode not in {"automatic", "guided", "off"}:
        raise ConfigError("quality.creative_guidance.mode must be one of: automatic, guided, off")


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


def _reject_legacy_paths(data: Any, path: str = "config") -> None:
    """Reject retired path families inside longform project configs."""

    if isinstance(data, dict):
        for key, value in data.items():
            _reject_legacy_paths(value, f"{path}.{key}")
        return
    if isinstance(data, list):
        for index, value in enumerate(data):
            _reject_legacy_paths(value, f"{path}[{index}]")
        return
    if not isinstance(data, str):
        return

    normalized = data.replace("\\", "/").strip()
    for prefix in LEGACY_PATH_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            raise ConfigError(
                f"{path} uses legacy path '{data}'. "
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
