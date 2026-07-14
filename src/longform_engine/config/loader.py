"""Load and validate longform novel project configuration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import copy

import yaml


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
        "api": {
            "enabled": False,
        },
        "template_dry_run": {
            "enabled": False,
        },
    },
}


@dataclass(frozen=True)
class ConfigDocument:
    """A loaded project config plus provenance useful for Agent reporting."""

    data: dict[str, Any]
    path: Path | None
    sources: tuple[str, ...]


def repo_root() -> Path:
    """Return the repository root for this package checkout."""

    return Path(__file__).resolve().parents[3]


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

    path = repo_root() / "templates" / template / "project.yaml"
    if not path.exists():
        raise ConfigError(f"Unknown template '{template}': {path}")
    return path


def load_project_config(
    config_path: str | Path | None = None,
    *,
    template: str | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> ConfigDocument:
    """Load config with built-in defaults, engine defaults, template/project config, and overrides."""

    root = repo_root()
    data = copy.deepcopy(BUILTIN_DEFAULTS)
    sources: list[str] = ["builtin defaults"]

    default_config = root / "config" / "default.engine.yaml"
    if default_config.exists():
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
    allowed_modes = {"agent_skill", "api_provider", "template_dry_run"}
    if mode not in allowed_modes:
        raise ConfigError(f"writing.mode must be one of: {', '.join(sorted(allowed_modes))}")


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
