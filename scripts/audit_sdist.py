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
    "config/agent_roles/prompts/prose_revision_semantic_reviewer.md",
    "config/agent_roles/playbooks/opening_and_mainline.md",
    "config/platform_publication_policy_registry.json",
    "config/quality_profiles/market_evidence_registry.yaml",
    "config/quality_profiles/markets/fanqie_free.yaml",
    "config/quality_profiles/markets/qidian_male.yaml",
    "config/quality_profiles/phases/opening.yaml",
    "config/story_facets/setting.yaml",
    "config/story_facets/plot_engines.yaml",
    "config/story_facets/narrative_forms.yaml",
    "config/story_facets/premise_devices.yaml",
    "config/story_facets/relationship_modes.yaml",
    "config/story_facets/tone.yaml",
    "config/story_profile_fixtures.yaml",
    "config/agent_protocol_acceptance_fixtures.yaml",
    "docs/ARCHITECTURE.md",
    "docs/CONFIGURATION.md",
    "docs/GATE_MODEL.md",
    "docs/GRAPH_MODEL.md",
    "docs/OPERATOR_GUIDE.md",
    "docs/PIPELINE_MODEL.md",
    "docs/RAG_MODEL.md",
    "docs/RELEASE_RUNBOOK.md",
    "docs/RESEARCH_MODEL.md",
    "docs/REVISION_MODEL.md",
    "docs/SKILL_INSTALLATION.md",
    "docs/SQLITE_MODEL.md",
    "docs/STORAGE_MODEL.md",
    "docs/RELEASE_HISTORY.md",
    "docs/WEB_STUDIO.md",
    "docs/releases/v0.4.0.md",
    "docs/releases/v0.4.1.md",
    "docs/releases/v0.4.2.md",
    "docs/releases/v0.4.3.md",
    "docs/releases/v0.4.4.md",
    "docs/releases/v0.5.0.md",
    "docs/releases/v0.6.0.md",
    "docs/releases/v0.7.0.md",
    "docs/releases/v0.9.0.md",
    "docs/releases/v0.10.0.md",
    "docs/releases/v0.11.0.md",
    "docs/releases/v0.12.0.md",
    "docs/releases/v0.13.0.md",
    "docs/releases/v0.14.0.md",
    "longform-novel-codex/SKILL.md",
    "longform-novel-codex/references/command_protocol.md",
    "longform-novel-claude/SKILL.md",
    "longform-novel-claude/references/command_protocol.md",
    "resource-manifest.json",
    "scripts/audit_wheel.py",
    "scripts/build_release_checksums.py",
    "scripts/check_agent_data_pipeline_readiness.py",
    "scripts/check_markdown_links.py",
    "scripts/release_surface_guards.py",
    "src/longform_engine/agent_protocol_readiness.py",
    "src/longform_engine/agent_protocols.py",
    "src/longform_engine/agent_pipeline.py",
    "src/longform_engine/arc_simulation.py",
    "src/longform_engine/benchmark.py",
    "src/longform_engine/blind_review.py",
    "src/longform_engine/chapter_contract.py",
    "src/longform_engine/chapter_coedit.py",
    "src/longform_engine/fanfiction_sources.py",
    "src/longform_engine/local_web.py",
    "src/longform_engine/creative_sandbox.py",
    "src/longform_engine/migration_v012.py",
    "src/longform_engine/semantic_protocols.py",
    "src/longform_engine/source_materialization.py",
    "src/longform_engine/source_processing.py",
    "src/longform_engine/source_protocols.py",
    "src/longform_engine/studio_server.py",
    "src/longform_engine/canon_changes.py",
    "src/longform_engine/narrative_events.py",
    "src/longform_engine/planning/contracts.py",
    "src/longform_engine/planning/workflow.py",
    "src/longform_engine/reader_feedback.py",
    "src/longform_engine/reader_promises_v2.py",
    "src/longform_engine/revision/branches.py",
    "src/longform_engine/story_brief.py",
    "src/longform_engine/author_voice.py",
    "src/longform_engine/human_author_revision.py",
    "src/longform_engine/human_chapter_intent.py",
    "src/longform_engine/human_story_review.py",
    "src/longform_engine/human_review_consultation.py",
    "src/longform_engine/review_server.py",
    "src/longform_engine/quality/contracts.py",
    "src/longform_engine/quality/status.py",
    "src/longform_engine/quality/editorial_patterns.py",
    "src/longform_engine/publication.py",
    "src/longform_engine/rag/production_benchmark.py",
    "src/longform_engine/release_readiness.py",
    "src/longform_engine/storage/recovery.py",
    "src/longform_engine/vector_backends.py",
    "tests/test_release_readiness.py",
    "tests/test_blind_review.py",
    "tests/test_rag_production.py",
    "tests/test_storage.py",
    "tests/test_story_architecture_v050.py",
    "tests/test_human_review_consultation_v060.py",
    "tests/test_review_server_v060.py",
    "tests/test_v070_human_revision_and_publication.py",
    "tests/test_story_brief_v080.py",
    "tests/test_v090_human_intent_coedit.py",
    "tests/test_v010_event_realization.py",
    "tests/test_v010_canon_feedback.py",
    "tests/test_v010_planning.py",
    "tests/test_v010_reader_promises.py",
    "tests/test_v010_revision_branch.py",
    "tests/test_fanfiction_source_library.py",
    "tests/test_source_processing.py",
    "tests/test_studio_server.py",
)

FORBIDDEN_SUFFIXES = (
    "src/longform_engine/reader_promises.py",
    "docs/QUALITY_BENCHMARK_RUNBOOK.md",
    "docs/SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION.md",
    "docs/V0_10_0_IMPLEMENTATION.md",
    "docs/V0_11_0_IMPLEMENTATION.md",
    "docs/V0_12_SEMANTIC_ARCHITECTURE.md",
    "docs/V0_13_FANFICTION_ARCHITECTURE.md",
    "RELEASE_CHECKLIST.md",
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
    forbidden = [
        suffix
        for suffix in FORBIDDEN_SUFFIXES
        if any(name.endswith("/" + suffix) or name.endswith(suffix) for name in names)
    ]
    if forbidden:
        print("sdist audit failed:", file=sys.stderr)
        for suffix in forbidden:
            print(f"- forbidden {suffix}", file=sys.stderr)
        return 1
    print(f"OK: sdist audit passed ({len(names)} entries): {sdist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
