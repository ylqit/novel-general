"""Locate immutable engine assets in a checkout or an installed wheel."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path
import json
import os
from typing import Any


RESOURCE_ENV = "LONGFORM_ENGINE_RESOURCE_ROOT"
RESOURCE_HASH_POLICY = "text-lf-v1"
TEXT_RESOURCE_SUFFIXES = frozenset({".json", ".md", ".toml", ".txt", ".yaml", ".yml"})


def resource_integrity_bytes(path: Path) -> bytes:
    """Return stable bytes for a resource integrity check on every platform."""

    data = path.read_bytes()
    if path.suffix.casefold() in TEXT_RESOURCE_SUFFIXES:
        return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return data


def _checkout_root() -> Path | None:
    override = os.environ.get(RESOURCE_ENV)
    if override:
        candidate = Path(override).expanduser().resolve()
        if _looks_like_resource_root(candidate):
            return candidate

    module_path = Path(__file__).resolve()
    for candidate in module_path.parents:
        if (candidate / "pyproject.toml").is_file() and _looks_like_resource_root(candidate):
            return candidate
    return None


def _looks_like_resource_root(path: Path) -> bool:
    return (
        (path / "config" / "default.engine.yaml").is_file()
        and (path / "templates" / "qidian-longform" / "project.yaml").is_file()
    )


def resource_root() -> Path:
    """Return the checkout root or the unpacked wheel's bundled resource root."""

    checkout = _checkout_root()
    if checkout is not None:
        return checkout

    bundled = files("longform_engine").joinpath("resources")
    path = Path(str(bundled)).resolve()
    if not _looks_like_resource_root(path):
        raise RuntimeError(f"longform-engine bundled resources are incomplete: {path}")
    return path


def resource_path(*parts: str) -> Path:
    path = resource_root().joinpath(*parts)
    if not path.exists():
        raise FileNotFoundError(f"longform-engine resource does not exist: {path}")
    return path


def load_resource_manifest() -> dict[str, Any]:
    manifest_path = resource_path("resource-manifest.json")
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "longform_resource_manifest_v1":
        raise RuntimeError(f"Invalid longform-engine resource manifest: {manifest_path}")
    return payload
