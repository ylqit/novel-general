#!/usr/bin/env python
"""Audit a built wheel for the public runtime resource contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile


REQUIRED = (
    "longform_engine/benchmark.py",
    "longform_engine/blind_review.py",
    "longform_engine/distribution.py",
    "longform_engine/intelligence/pipeline.py",
    "longform_engine/quality/contracts.py",
    "longform_engine/rag/production_benchmark.py",
    "longform_engine/release_readiness.py",
    "longform_engine/resources/config/default.engine.yaml",
    "longform_engine/resources/config/quality_profiles/markets/qidian_male.yaml",
    "longform_engine/resources/config/quality_profiles/genres/xuanhuan.yaml",
    "longform_engine/resources/config/quality_profiles/phases/opening.yaml",
    "longform_engine/resources/templates/qidian-longform/project.yaml",
    "longform_engine/resources/longform-novel-codex/SKILL.md",
    "longform_engine/resources/longform-novel-codex/references/command_protocol.md",
    "longform_engine/resources/longform-novel-claude/SKILL.md",
    "longform_engine/resources/longform-novel-claude/references/command_protocol.md",
    "longform_engine/resources/resource-manifest.json",
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
    missing = [name for name in REQUIRED if name not in names]
    if missing:
        print("Wheel resource audit failed:", file=sys.stderr)
        for name in missing:
            print(f"- missing {name}", file=sys.stderr)
        return 1
    print(f"OK: wheel resource audit passed ({len(names)} entries): {wheel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
