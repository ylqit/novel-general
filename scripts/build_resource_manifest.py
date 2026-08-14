#!/usr/bin/env python
"""Build or verify hashes for assets shipped inside the engine wheel."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from longform_engine.resources import RESOURCE_HASH_POLICY, resource_integrity_bytes  # noqa: E402


MANIFEST = ROOT / "resource-manifest.json"
RESOURCE_DIRS = (
    ROOT / "config",
    ROOT / "templates",
    ROOT / "longform-novel-codex",
    ROOT / "longform-novel-claude",
    ROOT / "shared",
)
RESOURCE_FILES = (ROOT / "pyproject.toml",)


def build_payload() -> dict[str, object]:
    assets: list[dict[str, object]] = []
    for path in RESOURCE_FILES:
        data = resource_integrity_bytes(path)
        assets.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "sha256": sha256(data).hexdigest(),
                "size": len(data),
            }
        )
    for directory in RESOURCE_DIRS:
        candidates = (candidate for candidate in directory.rglob("*") if candidate.is_file())
        for path in sorted(candidates, key=lambda item: item.relative_to(ROOT).as_posix().casefold()):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            data = resource_integrity_bytes(path)
            assets.append(
                {
                    "path": path.relative_to(ROOT).as_posix(),
                    "sha256": sha256(data).hexdigest(),
                    "size": len(data),
                }
            )
    assets.sort(key=lambda item: str(item["path"]).casefold())
    return {
        "schema": "longform_resource_manifest_v1",
        "engine_version": project_version(),
        "hash_policy": RESOURCE_HASH_POLICY,
        "assets": assets,
    }


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml is missing project.version")
    return match.group(1)


def serialized(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail when the committed manifest is stale.")
    mode.add_argument("--write", action="store_true", help="Write the current resource manifest.")
    args = parser.parse_args()

    expected = serialized(build_payload())
    if args.write:
        MANIFEST.write_text(expected, encoding="utf-8", newline="\n")
        print(f"Wrote {MANIFEST.relative_to(ROOT)}")
        return 0

    if not MANIFEST.is_file() or MANIFEST.read_text(encoding="utf-8") != expected:
        print("resource-manifest.json is missing or stale; run:", file=sys.stderr)
        print("  python scripts/build_resource_manifest.py --write", file=sys.stderr)
        return 1
    print("Resource manifest is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
