#!/usr/bin/env python
"""Synchronize shared protocol documents into each distributable Skill."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "shared"
SKILLS = (ROOT / "longform-novel-codex", ROOT / "longform-novel-claude")


def differences() -> list[str]:
    problems: list[str] = []
    sources = sorted(path for path in SHARED.glob("*.md") if path.is_file())
    for skill in SKILLS:
        target_dir = skill / "references"
        expected_names = {path.name for path in sources}
        actual_names = {path.name for path in target_dir.glob("*.md")} if target_dir.exists() else set()
        for source in sources:
            target = target_dir / source.name
            if not target.is_file():
                problems.append(f"missing: {target.relative_to(ROOT)}")
            elif target.read_bytes() != source.read_bytes():
                problems.append(f"drifted: {target.relative_to(ROOT)}")
        for extra in sorted(actual_names - expected_names):
            problems.append(f"unexpected: {(target_dir / extra).relative_to(ROOT)}")
    return problems


def sync() -> None:
    sources = sorted(path for path in SHARED.glob("*.md") if path.is_file())
    for skill in SKILLS:
        target_dir = skill / "references"
        target_dir.mkdir(parents=True, exist_ok=True)
        expected_names = {path.name for path in sources}
        for target in target_dir.glob("*.md"):
            if target.name not in expected_names:
                target.unlink()
        for source in sources:
            shutil.copy2(source, target_dir / source.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail when Skill references differ from shared sources.")
    mode.add_argument("--write", action="store_true", help="Refresh Skill reference copies.")
    args = parser.parse_args()

    if args.write:
        sync()
    problems = differences()
    if problems:
        print("Skill reference synchronization failed:", file=sys.stderr)
        for problem in problems:
            print(f"- {problem}", file=sys.stderr)
        return 1
    print("Skill references are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
