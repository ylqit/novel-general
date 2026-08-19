"""Canonical longform novel project layout."""

from __future__ import annotations

from pathlib import Path
import re

FINAL_MANUSCRIPT_DIRECTORY = "40_manuscript/final"
MANUSCRIPT_LANES = {
    "draft": "40_manuscript/draft",
    "final": FINAL_MANUSCRIPT_DIRECTORY,
    "summaries": "40_manuscript/summaries",
}
CANONICAL_CHAPTER_PATTERN = re.compile(r"ch(\d{3}|[1-9]\d{3,})\.md")


def chapter_filename(chapter_number: int) -> str:
    """Return the only supported manuscript filename for a positive chapter number."""

    if not isinstance(chapter_number, int) or isinstance(chapter_number, bool) or chapter_number <= 0:
        raise ValueError("chapter_number must be a positive integer.")
    return f"ch{chapter_number:03d}.md"


def parse_canonical_chapter_number(path: str | Path) -> int | None:
    """Parse a canonical chapter filename, returning None for non-chapter artifacts."""

    match = CANONICAL_CHAPTER_PATTERN.fullmatch(Path(path).name)
    if not match:
        return None
    chapter_number = int(match.group(1))
    return chapter_number if chapter_number > 0 else None


def manuscript_chapter_path(root: Path, chapter_number: int, *, lane: str) -> Path:
    """Resolve a canonical draft, final, or summary path."""

    return root / manuscript_chapter_relative_path(chapter_number, lane=lane)


def manuscript_chapter_relative_path(chapter_number: int, *, lane: str) -> str:
    """Return the project-relative canonical path for a manuscript chapter."""

    try:
        directory = MANUSCRIPT_LANES[lane]
    except KeyError as exc:
        raise ValueError(f"lane must be one of: {', '.join(MANUSCRIPT_LANES)}") from exc
    return f"{directory}/{chapter_filename(chapter_number)}"


def existing_manuscript_chapter_path(root: Path, chapter_number: int, *, lane: str) -> Path | None:
    """Return the canonical manuscript path when it exists, without alias lookup."""

    expected = manuscript_chapter_path(root, chapter_number, lane=lane)
    for number, path in list_canonical_chapter_files(expected.parent):
        if number == chapter_number:
            return path
    return None


def list_canonical_chapter_files(directory: Path) -> tuple[tuple[int, Path], ...]:
    """List canonical chapter Markdown files and reject manuscript-shaped aliases."""

    result: list[tuple[int, Path]] = []
    paths = sorted(item for item in directory.iterdir() if item.is_file()) if directory.is_dir() else ()
    for path in paths:
        if path.suffix == ".json":
            continue
        chapter_number = parse_canonical_chapter_number(path)
        if chapter_number is None:
            raise ValueError(
                f"Non-canonical manuscript filename: {path}. Expected {chapter_filename(1)}-style names."
            )
        result.append((chapter_number, path))
    return tuple(result)


def list_finalized_chapter_files(root: Path) -> tuple[tuple[int, Path], ...]:
    """List one unambiguous final file per chapter under the storage contract."""

    return list_canonical_chapter_files(root / FINAL_MANUSCRIPT_DIRECTORY)

BASE_DIRECTORIES = [
    "00_governance",
    "10_bible",
    "20_outline",
    "30_state",
    "40_manuscript",
    "50_workbench",
    "60_rag",
    "70_runtime",
    "80_exports",
]

SUBDIRECTORIES = [
    "10_bible/fanfiction",
    "20_outline/chapter_cards",
    "20_outline/revise_reports",
    "30_state/quality",
    "30_state/semantic_ledger",
    "30_state/chapter_closures",
    "40_manuscript/draft",
    FINAL_MANUSCRIPT_DIRECTORY,
    "40_manuscript/summaries",
    "40_manuscript/snapshots",
    "40_manuscript/rewrite",
    "40_manuscript/detached",
    "50_workbench/beats",
    "50_workbench/chapter_context",
    "50_workbench/editorial_reviews",
    "50_workbench/gate_artifacts",
    "50_workbench/graph_updates",
    "50_workbench/graph_reports",
    "50_workbench/impact_reports",
    "50_workbench/memory_tasks",
    "50_workbench/semantic_tasks",
    "50_workbench/quality_reviews",
    "50_workbench/repair_plans",
    "50_workbench/repair_candidates",
    "50_workbench/humanizer_tasks",
    "50_workbench/intelligence_tasks",
    "50_workbench/intelligence_candidates",
    "50_workbench/intelligence_validations",
    "50_workbench/fanfiction_sources",
    "50_workbench/research_inbox",
    "50_workbench/writing_tasks",
    "50_workbench/agent_drafts",
    "50_workbench/agent_tasks",
    "10_bible/style_profiles",
    "30_state/tcs",
    "60_rag/chunks",
    "60_rag/context",
    "60_rag/entities",
    "60_rag/memory",
    "60_rag/memory/arcs",
    "60_rag/memory/chapters",
    "60_rag/memory/scenes",
    "60_rag/memory/style",
    "60_rag/metadata",
    "60_rag/query_cache",
    "70_runtime/cache",
    "70_runtime/db",
    "70_runtime/locks",
    "70_runtime/logs",
    "70_runtime/models",
    "70_runtime/provenance",
    "70_runtime/benchmarks",
    "70_runtime/artifacts/chapters",
    "70_runtime/run_reports",
    "70_runtime/snapshots",
    "70_runtime/transactions",
    "70_runtime/tx",
    "70_runtime/tmp",
    "80_exports/bundles",
    "80_exports/platform",
    "80_exports/publication_reports",
]

INITIAL_TEXT_FILES = {
    "00_governance/idea_seed.md": "# Idea Seed\n\n待确认：目标读者、文风、核心禁区、自动化程度、目标规模。\n",
    "00_governance/reader_contract.md": "# Reader Contract\n\n记录读者承诺、核心爽点、禁区和平台策略。\n",
    "00_governance/automation_policy.md": "# Automation Policy\n\n默认采用命令驱动工作流，门禁失败后暂停续写。\n",
    "10_bible/world.md": "# World Bible\n\n记录世界规则、历史背景、地图层级和关键限制。\n",
    "10_bible/power_system.md": "# Power System\n\n记录能力体系、升级成本、边界和代价。\n",
    "10_bible/style_bible.md": "# Style Bible\n\n记录文风、叙事视角、常用表达禁区和样章参考。\n",
    "20_outline/book_outline.md": "# Book Outline\n\n记录主线问题、终局方向、卷结构和关键高潮。\n",
    "60_rag/context/next_plot_context.md": "# Next Plot Context\n\n尚未构建。\n",
}

INITIAL_JSON_FILES = {
    "10_bible/creative_brief.json": {
        "schema_version": 1,
        "target_audience": "longform novel readers",
        "writing_style": "immersive serialized prose",
        "reader_contract": {
            "platform": "unknown",
            "core_promise": "",
            "main_question": "",
            "ending_direction": "",
        },
        "core_taboo": [
            "do not prematurely resolve the core conflict",
            "do not leave meta/prompt/AI residue in manuscript prose",
        ],
        "automation_level": "agent_skill with human approval for finalization",
        "target_scale": "pending confirmation",
        "story_profile": {},
        "status": "pending_confirmation",
    },
    "10_bible/characters.json": [],
    "10_bible/relationships.json": [],
    "10_bible/factions.json": [],
    "10_bible/locations.json": [],
    "20_outline/volumes.json": [],
    "20_outline/story_arcs.json": [],
    "20_outline/chapter_plan.json": [],
    "20_outline/planning_window.json": {},
    "20_outline/outline_anchors.json": [],
    "20_outline/foreshadowing_ledger.json": [],
    "30_state/novel_state.json": {
        "current_chapter": 0,
        "last_finalized_chapter": 0,
        "status": "initialized",
        "stale": [],
    },
    "30_state/manuscript_metrics.json": {
        "schema": "manuscript_metrics_v1",
        "metric": "content_characters_v1",
        "finalized_chapter_count": 0,
        "latest_finalized_chapter": 0,
        "total_content_characters": 0,
        "total_display_characters": 0,
        "average_content_characters": 0,
    },
    "30_state/character_state.json": [],
    "30_state/story_graph.json": {
        "entities": [],
        "relationships": [],
        "events": [],
    },
    "30_state/foreshadowing_state.json": {
        "schema": "foreshadowing_state_v1",
        "threads": {},
    },
    "30_state/world_state.json": {
        "schema": "world_state_v1",
        "facts": {},
    },
    "30_state/unresolved_threads.json": [],
    "30_state/timeline.json": [],
    "30_state/event_matrix.json": [],
    "30_state/pacing_history.json": [],
}

INITIAL_JSONL_FILES = {
    "30_state/reward_ledger.jsonl": "",
    "30_state/quality/structure_history.jsonl": "",
    "40_manuscript/chapter_meta.jsonl": "",
    "70_runtime/logs/generation_log.jsonl": "",
}
