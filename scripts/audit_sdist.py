#!/usr/bin/env python
"""Audit a built sdist for source, documentation, Skill, and release assets."""

from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import sys
import tarfile


REQUIRED_SUFFIXES = (
    "AGENTS.md",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    ".github/workflows/ci.yml",
    ".github/workflows/release.yml",
    "config/default.engine.yaml",
    "config/agent_data_pipeline_authorization.json",
    "config/quality_profiles/markets/qidian_male.yaml",
    "config/quality_profiles/genres/xuanhuan.yaml",
    "config/quality_profiles/phases/opening.yaml",
    "docs/AGENT_FIRST_DOCUMENT_PROTOCOL_AND_DATA_PIPELINE_CHECKLIST.md",
    "docs/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.md",
    "docs/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_PRODUCTION_PIPELINE.md",
    "docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_EVIDENCE.json",
    "docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.json",
    "docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_EVIDENCE.json",
    "docs/PUBLIC_DISTRIBUTION_PRODUCTIZATION_CHECKLIST.md",
    "docs/PHASE6_QUALITY_PROOF_RUNBOOK.md",
    "docs/QUALITY_BENCHMARK_RUNBOOK.md",
    "docs/RELEASE_RUNBOOK.md",
    "docs/benchmark_scenarios/PHASE6_ORIGINAL_COMPARISON_V1.json",
    "docs/benchmarks/PHASE6_EXECUTION_STATUS.md",
    "longform-novel-codex/SKILL.md",
    "longform-novel-codex/references/command_protocol.md",
    "longform-novel-claude/SKILL.md",
    "longform-novel-claude/references/command_protocol.md",
    "resource-manifest.json",
    "scripts/audit_wheel.py",
    "scripts/check_agent_data_pipeline_readiness.py",
    "scripts/release_surface_guards.py",
    "src/longform_engine/agent_protocol_readiness.py",
    "src/longform_engine/agent_pipeline.py",
    "src/longform_engine/benchmark.py",
    "src/longform_engine/blind_review.py",
    "src/longform_engine/quality/contracts.py",
    "src/longform_engine/rag/production_benchmark.py",
    "src/longform_engine/release_readiness.py",
    "tests/test_release_readiness.py",
    "tests/test_blind_review_phase6.py",
    "tests/test_rag_production_phase6.py",
    "tests/test_agent_document_protocol_phase6.py",
    "tests/test_agent_document_protocol_phase7.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sdist", type=Path, nargs="?", help="sdist path; omit to auto-discover one tar.gz in --dist-dir.")
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    sdist = args.sdist
    if sdist is None:
        candidates = sorted(args.dist_dir.glob("*.tar.gz"))
        if len(candidates) != 1:
            print(f"Expected exactly one sdist in {args.dist_dir}, found {len(candidates)}.", file=sys.stderr)
            return 1
        sdist = candidates[0]
    if not sdist.is_file():
        print(f"sdist does not exist: {sdist}", file=sys.stderr)
        return 1

    with tarfile.open(sdist, mode="r:gz") as archive:
        names = {PurePosixPath(name).as_posix() for name in archive.getnames() if name and not name.endswith("/")}
    missing = [suffix for suffix in REQUIRED_SUFFIXES if not any(name.endswith("/" + suffix) for name in names)]
    if missing:
        print("sdist audit failed:", file=sys.stderr)
        for suffix in missing:
            print(f"- missing {suffix}", file=sys.stderr)
        return 1
    print(f"OK: sdist audit passed ({len(names)} entries): {sdist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
