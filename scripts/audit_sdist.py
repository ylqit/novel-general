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
    "config/agent_context_profiles.yaml",
    "config/agent_roles/registry.json",
    "config/agent_roles/playbooks/opening_and_mainline.md",
    "config/quality_profiles/markets/qidian_male.yaml",
    "config/quality_profiles/phases/opening.yaml",
    "config/story_facets/setting.yaml",
    "config/story_facets/plot_engines.yaml",
    "config/story_facets/narrative_forms.yaml",
    "config/story_facets/premise_devices.yaml",
    "config/story_facets/relationship_modes.yaml",
    "config/story_facets/tone.yaml",
    "config/story_profile_fixtures.yaml",
    "config/v041_release_acceptance_fixtures.yaml",
    "docs/AGENT_FIRST_DOCUMENT_PROTOCOL_AND_DATA_PIPELINE_CHECKLIST.md",
    "docs/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_PRODUCTION_PIPELINE.md",
    "docs/PUBLIC_DISTRIBUTION_PRODUCTIZATION_CHECKLIST.md",
    "docs/PHASE6_QUALITY_PROOF_RUNBOOK.md",
    "docs/QUALITY_BENCHMARK_RUNBOOK.md",
    "docs/RELEASE_RUNBOOK.md",
    "docs/V0_4_0_WORD_BUDGET_AND_COMPOSABLE_PROFILE_CHECKLIST.md",
    "docs/V0_4_1_PROMPT_PROFESSIONAL_DEEPENING_CHECKLIST.md",
    "docs/V0_4_1_RUNTIME_CLEANUP_AND_CHINESE_PROMPT_CHECKLIST.md",
    "docs/V0_4_2_REPAIR_COORDINATION_CHECKLIST.md",
    "docs/V0_4_3_PROTOCOL_HOTFIX_CHECKLIST.md",
    "docs/releases/v0.4.0.md",
    "docs/releases/v0.4.1.md",
    "docs/releases/v0.4.2.md",
    "docs/releases/v0.4.3.md",
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
    "src/longform_engine/agent_protocols.py",
    "src/longform_engine/agent_pipeline.py",
    "src/longform_engine/benchmark.py",
    "src/longform_engine/blind_review.py",
    "src/longform_engine/quality/contracts.py",
    "src/longform_engine/rag/production_benchmark.py",
    "src/longform_engine/release_readiness.py",
    "tests/test_release_readiness.py",
    "tests/test_blind_review_phase6.py",
    "tests/test_rag_production_phase6.py",
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
