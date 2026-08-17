#!/usr/bin/env python
"""Audit a built wheel for the public runtime resource contract."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path, PurePath
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from longform_engine.resources import RESOURCE_HASH_POLICY, TEXT_RESOURCE_SUFFIXES  # noqa: E402


REQUIRED = (
    "longform_engine/agent_pipeline.py",
    "longform_engine/agent_isolation.py",
    "longform_engine/agent_normalization.py",
    "longform_engine/agent_protocol_readiness.py",
    "longform_engine/agent_protocols.py",
    "longform_engine/agent_results.py",
    "longform_engine/benchmark.py",
    "longform_engine/blind_review.py",
    "longform_engine/distribution.py",
    "longform_engine/intelligence/pipeline.py",
    "longform_engine/quality/contracts.py",
    "longform_engine/rag/production_benchmark.py",
    "longform_engine/release_readiness.py",
    "longform_engine/prompting.py",
    "longform_engine/roles.py",
    "longform_engine/resources/config/default.engine.yaml",
    "longform_engine/resources/config/agent_context_profiles.yaml",
    "longform_engine/resources/config/agent_roles/registry.json",
    "longform_engine/resources/config/agent_roles/playbooks/opening_and_mainline.md",
    "longform_engine/resources/config/quality_profiles/markets/qidian_male.yaml",
    "longform_engine/resources/config/quality_profiles/phases/opening.yaml",
    "longform_engine/resources/config/story_facets/setting.yaml",
    "longform_engine/resources/config/story_facets/plot_engines.yaml",
    "longform_engine/resources/config/story_facets/narrative_forms.yaml",
    "longform_engine/resources/config/story_facets/premise_devices.yaml",
    "longform_engine/resources/config/story_facets/relationship_modes.yaml",
    "longform_engine/resources/config/story_facets/tone.yaml",
    "longform_engine/resources/config/story_profile_fixtures.yaml",
    "longform_engine/resources/config/v041_release_acceptance_fixtures.yaml",
    "longform_engine/resources/templates/qidian-longform/project.yaml",
    "longform_engine/resources/longform-novel-codex/SKILL.md",
    "longform_engine/resources/longform-novel-codex/references/command_protocol.md",
    "longform_engine/resources/longform-novel-claude/SKILL.md",
    "longform_engine/resources/longform-novel-claude/references/command_protocol.md",
    "longform_engine/resources/resource-manifest.json",
    "longform_engine/resources/pyproject.toml",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, nargs="?", help="Wheel path; omit to auto-discover one wheel in --dist-dir.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheel = args.wheel
    if wheel is None:
        candidates = sorted(args.dist_dir.glob("*.whl"))
        if len(candidates) != 1:
            print(f"Expected exactly one wheel in {args.dist_dir}, found {len(candidates)}.", file=sys.stderr)
            return 1
        wheel = candidates[0]
    if not wheel.is_file():
        print(f"Wheel does not exist: {wheel}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        manifest_member = "longform_engine/resources/resource-manifest.json"
        manifest = json.loads(archive.read(manifest_member).decode("utf-8")) if manifest_member in names else {}
        registry_member = "longform_engine/resources/config/agent_roles/registry.json"
        if registry_member in names:
            registry = json.loads(archive.read(registry_member).decode("utf-8"))
            role_prompts = {
                "longform_engine/resources/" + str(role.get("prompt_path") or "")
                for role in registry.get("roles", [])
                if isinstance(role, dict)
            }
        else:
            role_prompts = set()
    missing = [name for name in (*REQUIRED, *sorted(role_prompts)) if name not in names]
    if missing:
        print("Wheel resource audit failed:", file=sys.stderr)
        for name in missing:
            print(f"- missing {name}", file=sys.stderr)
        return 1
    integrity_errors: list[str] = []
    if manifest.get("hash_policy") != RESOURCE_HASH_POLICY:
        integrity_errors.append("resource manifest has the wrong hash policy")
    with zipfile.ZipFile(wheel) as archive:
        for entry in manifest.get("assets", []):
            if not isinstance(entry, dict):
                integrity_errors.append("resource manifest contains an invalid entry")
                continue
            relative = str(entry.get("path") or "")
            member = "longform_engine/resources/" + relative
            if member not in names:
                integrity_errors.append(f"missing resource {relative}")
                continue
            data = archive.read(member)
            if PurePath(relative).suffix.casefold() in TEXT_RESOURCE_SUFFIXES:
                data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            if sha256(data).hexdigest() != entry.get("sha256") or len(data) != entry.get("size"):
                integrity_errors.append(f"resource integrity mismatch {relative}")
    if integrity_errors:
        print("Wheel resource integrity audit failed:", file=sys.stderr)
        for error in integrity_errors[:20]:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"OK: wheel resource audit passed ({len(names)} entries): {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
