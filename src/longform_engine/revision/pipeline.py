"""Chapter transaction state, rewrite branches, and rollback handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re
import shutil

from longform_engine.agent_tasks import agent_task_events_file, agent_task_index_file, mark_tasks_rolled_back
from longform_engine.config import ConfigDocument
from longform_engine.db import database_path, sync_database
from longform_engine.memory import mark_memory_stale
from longform_engine.quality import truncate_editorial_pattern_registry, truncate_quality_history
from longform_engine.arc_simulation import SIMULATION_DIR, mark_overlapping_arc_simulations_stale
from longform_engine.reader_promises import LEDGER_PATH, truncate_reader_promise_ledger
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import (
    existing_manuscript_chapter_path,
    list_canonical_chapter_files,
    manuscript_chapter_path,
)
from longform_engine.vectorstore import hnsw_manifest_path, local_index_path, local_store_path


class RevisionError(ValueError):
    """Raised when a revision command cannot safely complete."""


@dataclass(frozen=True)
class ChapterTransactionState:
    """Chapter state derived from files and the chapter metadata ledger."""

    chapter_number: int
    status: str
    statuses: tuple[str, ...]
    paths: tuple[str, ...]
    stale: bool


@dataclass(frozen=True)
class ProjectRevisionStatus:
    """Project status with chapter transaction state."""

    root: str
    current_chapter: int
    last_finalized_chapter: int
    state_status: str
    stale: tuple[str, ...]
    stale_chapters: tuple[int, ...]
    chapters: tuple[ChapterTransactionState, ...]


@dataclass(frozen=True)
class RevisionBranchResult:
    """Result of creating a rewrite candidate."""

    chapter_number: int
    source_path: str
    candidate_path: str
    report_file: str
    status: str


@dataclass(frozen=True)
class RevisionRollbackResult:
    """Result of rolling back to an earlier chapter."""

    to_chapter: int
    detached_dir: str
    detached_files: tuple[str, ...]
    stale_chapters: tuple[int, ...]
    stale_report: str
    impact_report: str
    state_file: str
    transaction_report: str


@dataclass(frozen=True)
class _RollbackPlan:
    """Complete mutation inventory for one transaction-backed rollback."""

    timestamp: str
    detached_dir: Path
    moves: tuple[tuple[Path, Path], ...]
    affected_chapters: tuple[int, ...]
    source_paths: tuple[Path, ...]
    touched_paths: tuple[Path, ...]


@dataclass(frozen=True)
class RollbackImpactResult:
    """Impact report after a rollback."""

    report_file: str
    report_json: str
    to_chapter: int
    affected_chapters: tuple[int, ...]
    affected_settings: tuple[str, ...]
    affected_foreshadowing: tuple[str, ...]
    affected_character_state: tuple[str, ...]
    affected_summaries: tuple[str, ...]


STATUS_ORDER = ("detached", "rewrite_candidate", "final", "reviewed", "draft", "stale")


def project_status(config: ConfigDocument) -> ProjectRevisionStatus:
    """Return chapter transaction states from file facts."""

    root = resolve_project_root(config)
    state = read_json(root / "30_state" / "novel_state.json", default={})
    if not isinstance(state, dict):
        state = {}
    chapters = tuple(chapter_transaction_states(config))
    return ProjectRevisionStatus(
        root=str(root),
        current_chapter=as_int(state.get("current_chapter")),
        last_finalized_chapter=as_int(state.get("last_finalized_chapter")),
        state_status=str(state.get("status") or "unknown"),
        stale=tuple(str(item) for item in normalize_list(state.get("stale"))),
        stale_chapters=tuple(sorted({as_int(item) for item in normalize_list(state.get("stale_chapters")) if as_int(item) > 0})),
        chapters=chapters,
    )


def chapter_transaction_states(config: ConfigDocument) -> list[ChapterTransactionState]:
    """Derive per-chapter transaction state from manuscript files, gates, and metadata."""

    root = resolve_project_root(config)
    records: dict[int, dict[str, Any]] = {}

    def add(chapter_number: int | None, status: str, path: Path | None = None) -> None:
        if not chapter_number or chapter_number <= 0:
            return
        record = records.setdefault(chapter_number, {"statuses": set(), "paths": []})
        record["statuses"].add(status)
        if path is not None:
            rel = relative_path(root, path)
            if rel not in record["paths"]:
                record["paths"].append(rel)

    for status, directory, recursive in (
        ("draft", root / "40_manuscript" / "draft", False),
        ("final", root / "40_manuscript" / "final", False),
        ("rewrite_candidate", root / "40_manuscript" / "rewrite", True),
        ("detached", root / "40_manuscript" / "detached", True),
    ):
        pattern = "**/*" if recursive else "*"
        for path in sorted(directory.glob(pattern)):
            if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
                add(parse_chapter_number(path), status, path)

    for path in sorted((root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json")):
        payload = read_json(path, default={})
        if isinstance(payload, dict) and payload.get("passed") is True:
            add(as_int(payload.get("chapter_number")) or parse_chapter_number(path.parent), "reviewed", path)

    stale_chapters = stale_chapter_numbers(root)
    for path in sorted((root / "20_outline" / "chapter_cards").glob("ch*.json")):
        payload = read_json(path, default={})
        number = parse_chapter_number(path)
        if number and isinstance(payload, dict) and payload.get("status") == "stale":
            stale_chapters.add(number)
            add(number, "stale", path)

    for record in read_jsonl(root / "40_manuscript" / "chapter_meta.jsonl"):
        number = as_int(record.get("chapter_number") or record.get("chapter") or record.get("number"))
        status = str(record.get("status") or "").strip()
        if number and status:
            add(number, status, root / str(record["path"]) if record.get("path") else None)

    for number in stale_chapters:
        add(number, "stale", None)

    states = []
    for number in sorted(records):
        statuses = tuple(sorted(records[number]["statuses"], key=lambda item: STATUS_ORDER.index(item) if item in STATUS_ORDER else 99))
        states.append(
            ChapterTransactionState(
                chapter_number=number,
                status=primary_status(statuses),
                statuses=statuses,
                paths=tuple(records[number]["paths"]),
                stale="stale" in statuses,
            )
        )
    return states


def create_revision_branch(config: ConfigDocument, *, chapter_number: int, overwrite: bool = False) -> RevisionBranchResult:
    """Create a rewrite candidate without overwriting final/draft files."""

    if chapter_number <= 0:
        raise RevisionError("chapter_number must be positive.")
    root = resolve_project_root(config)
    source = find_chapter_source(root, chapter_number)
    if source is None:
        raise RevisionError(f"No draft or final manuscript found for ch{chapter_number:03d}.")

    candidate = root / "40_manuscript" / "rewrite" / f"ch{chapter_number:03d}_rewrite_candidate.md"
    if candidate.exists() and not overwrite:
        candidate_path = candidate
    else:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, candidate)
        candidate_path = candidate

    report_dir = root / "50_workbench" / "revision_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = report_dir / f"branch_ch{chapter_number:03d}.json"
    payload = {
        "command": "revision branch",
        "chapter_number": chapter_number,
        "status": "rewrite_candidate",
        "source_path": relative_path(root, source),
        "candidate_path": relative_path(root, candidate_path),
        "updated_at": utc_now(),
    }
    write_json(report, payload)
    upsert_chapter_meta(
        root,
        {
            "chapter_number": chapter_number,
            "status": "rewrite_candidate",
            "path": relative_path(root, candidate_path),
            "title": extract_title(safe_read_text(candidate_path), candidate_path),
            "updated_at": utc_now(),
        },
    )
    sync_database(config)
    return RevisionBranchResult(
        chapter_number=chapter_number,
        source_path=str(source),
        candidate_path=str(candidate_path),
        report_file=str(report),
        status="rewrite_candidate",
    )


def rollback(config: ConfigDocument, *, to_chapter: int) -> RevisionRollbackResult:
    """Roll back manuscript state and preserve later material as detached drafts."""

    if to_chapter < 0:
        raise RevisionError("to_chapter must be zero or positive.")
    root = resolve_project_root(config)
    plan = _build_rollback_plan(config, to_chapter=to_chapter)
    with apply_transaction(
        root,
        command="revision rollback",
        chapter_number=to_chapter,
        source_paths=plan.source_paths,
        touched_paths=plan.touched_paths,
        metadata={
            "to_chapter": to_chapter,
            "affected_chapters": list(plan.affected_chapters),
            "detached_dir": relative_path(root, plan.detached_dir),
            "write_boundary": "detach_future_and_invalidate_derived_state",
        },
    ) as transaction:
        for path, target in plan.moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)

        mark_future_chapter_cards_stale(root, to_chapter)
        mark_future_writing_tasks_stale(root, to_chapter)
        mark_chapter_plan_stale(root, to_chapter)
        rebuilt_quality_indexes = (
            *truncate_quality_history(root, to_chapter=to_chapter),
            *truncate_editorial_pattern_registry(root, to_chapter=to_chapter),
            truncate_reader_promise_ledger(root, to_chapter=to_chapter),
            *mark_overlapping_arc_simulations_stale(
                root, from_chapter=to_chapter + 1, to_chapter=10**9
            ),
        )
        stale_report = write_stale_markers(
            root,
            to_chapter=to_chapter,
            stale_chapters=list(plan.affected_chapters),
            timestamp=plan.timestamp,
            rebuilt_quality_indexes=rebuilt_quality_indexes,
        )
        mark_memory_stale(
            config,
            from_chapter=to_chapter + 1,
            reason=f"rollback_to_ch{to_chapter:03d}",
        )
        state_file = update_rollback_state(
            root,
            to_chapter=to_chapter,
            stale_chapters=list(plan.affected_chapters),
            timestamp=plan.timestamp,
            detached_dir=plan.detached_dir,
        )

        for number in plan.affected_chapters:
            detached_path = first_detached_file_for_chapter(plan.detached_dir, number)
            upsert_chapter_meta(
                root,
                {
                    "chapter_number": number,
                    "status": "detached" if detached_path else "stale",
                    "path": relative_path(root, detached_path) if detached_path else None,
                    "title": extract_title(safe_read_text(detached_path), detached_path) if detached_path else f"Chapter {number}",
                    "updated_at": utc_now(),
                },
            )

        impact = write_rollback_impact_report(config)
        for number in plan.affected_chapters:
            mark_tasks_rolled_back(
                root,
                chapter_number=number,
                command="revision rollback",
                artifact=plan.detached_dir,
                result=stale_report,
            )
        sync_database(config)
    return RevisionRollbackResult(
        to_chapter=to_chapter,
        detached_dir=str(plan.detached_dir),
        detached_files=tuple(relative_path(root, target) for _source, target in plan.moves),
        stale_chapters=plan.affected_chapters,
        stale_report=str(stale_report),
        impact_report=impact.report_file,
        state_file=str(state_file),
        transaction_report=relative_path(root, transaction.report_file),
    )


def rollback_impact(config: ConfigDocument) -> RollbackImpactResult:
    """Write or refresh the latest rollback impact report."""

    return write_rollback_impact_report(config)


def _build_rollback_plan(config: ConfigDocument, *, to_chapter: int) -> _RollbackPlan:
    """Inventory every path a rollback may mutate before opening the transaction."""

    root = resolve_project_root(config)
    timestamp = timestamp_slug()
    detached_dir = root / "40_manuscript" / "detached" / f"rollback_to_ch{to_chapter:03d}_{timestamp}"
    moves: list[tuple[Path, Path]] = []
    affected: set[int] = set()
    for source_group in ("final", "draft", "summaries"):
        source_dir = root / "40_manuscript" / source_group
        for number, path in list_canonical_chapter_files(source_dir):
            if number <= to_chapter:
                continue
            moves.append((path, unique_path(detached_dir / source_group / path.name)))
            affected.add(number)

    future_cards = [
        path
        for path in sorted((root / "20_outline" / "chapter_cards").glob("ch*.json"))
        if (parse_chapter_number(path) or 0) > to_chapter
    ]
    future_tasks = [
        path
        for path in sorted((root / "50_workbench" / "writing_tasks").glob("ch*.json"))
        if (parse_chapter_number(path) or 0) > to_chapter
    ]
    affected.update(parse_chapter_number(path) or 0 for path in (*future_cards, *future_tasks))
    affected.discard(0)

    vector_index = local_index_path(config)
    report_base = root / "50_workbench" / "impact_reports" / f"rollback_to_ch{to_chapter:03d}"
    touched_paths = [
        *(source for source, _target in moves),
        detached_dir,
        *future_cards,
        *future_tasks,
        root / "20_outline" / "chapter_plan.json",
        root / "30_state" / "reward_ledger.jsonl",
        root / "30_state" / "quality" / "structure_history.jsonl",
        root / "50_workbench" / "editorial_patterns" / "registry.jsonl",
        root / LEDGER_PATH,
        *sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")),
        root / "30_state" / "stale_indexes.json",
        root / "60_rag" / "stale.json",
        root / "30_state" / "story_graph_stale.json",
        root / "30_state" / "event_matrix_stale.json",
        root / "50_workbench" / "writing_tasks" / "stale.json",
        root / "60_rag" / "memory" / "stale.json",
        root / "30_state" / "novel_state.json",
        root / "40_manuscript" / "chapter_meta.jsonl",
        report_base.with_suffix(".md"),
        report_base.with_suffix(".json"),
        agent_task_index_file(root),
        agent_task_events_file(root),
        root / "70_runtime" / "artifacts" / "events",
        database_path(config),
        local_store_path(config),
        vector_index,
        hnsw_manifest_path(vector_index),
    ]
    return _RollbackPlan(
        timestamp=timestamp,
        detached_dir=detached_dir,
        moves=tuple(moves),
        affected_chapters=tuple(sorted(affected)),
        source_paths=tuple(source for source, _target in moves),
        touched_paths=tuple(touched_paths),
    )


def write_rollback_impact_report(config: ConfigDocument) -> RollbackImpactResult:
    root = resolve_project_root(config)
    state = read_json(root / "30_state" / "novel_state.json", default={})
    if not isinstance(state, dict) or not isinstance(state.get("last_rollback"), dict):
        raise RevisionError("No rollback metadata found; run revision rollback first.")
    rollback_info = state["last_rollback"]
    to_chapter = as_int(rollback_info.get("to_chapter"))
    affected = tuple(sorted({as_int(item) for item in normalize_list(rollback_info.get("stale_chapters")) if as_int(item) > 0}))
    report_dir = root / "50_workbench" / "impact_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_base = f"rollback_to_ch{to_chapter:03d}"
    report_md = report_dir / f"{report_base}.md"
    report_json = report_dir / f"{report_base}.json"
    impacted = {
        "settings": existing_paths(root, ("10_bible/world.md", "10_bible/power_system.md", "10_bible/style_bible.md")),
        "foreshadowing": existing_paths(root, ("20_outline/foreshadowing_ledger.json", "20_outline/outline_anchors.json")),
        "character_state": existing_paths(root, ("30_state/character_state.json", "10_bible/characters.json", "10_bible/relationships.json")),
        "summaries": [relative_path(root, path) for number in affected for path in summary_candidates(root, number) if path.exists()],
        "chapter_cards": [relative_path(root, root / "20_outline" / "chapter_cards" / f"ch{number:03d}.json") for number in affected if (root / "20_outline" / "chapter_cards" / f"ch{number:03d}.json").exists()],
        "graph_state": existing_paths(root, ("30_state/story_graph.json", "30_state/event_matrix.json", "30_state/timeline.json")),
    }
    payload = {
        "type": "rollback_impact",
        "to_chapter": to_chapter,
        "affected_chapters": list(affected),
        "impacted": impacted,
        "detached_dir": rollback_info.get("detached_dir"),
        "stale": state.get("stale", []),
        "generated_at": utc_now(),
    }
    write_json(report_json, payload)
    atomic_write_text(report_md, format_rollback_impact_markdown(payload))
    return RollbackImpactResult(
        report_file=str(report_md),
        report_json=str(report_json),
        to_chapter=to_chapter,
        affected_chapters=affected,
        affected_settings=tuple(impacted["settings"]),
        affected_foreshadowing=tuple(impacted["foreshadowing"]),
        affected_character_state=tuple(impacted["character_state"]),
        affected_summaries=tuple(impacted["summaries"]),
    )


def find_chapter_source(root: Path, chapter_number: int) -> Path | None:
    for lane in ("final", "draft"):
        if path := existing_manuscript_chapter_path(root, chapter_number, lane=lane):
            return path
    return None


def first_detached_file_for_chapter(detached_dir: Path, chapter_number: int) -> Path | None:
    for source_group in ("final", "draft", "summaries"):
        for number, path in list_canonical_chapter_files(detached_dir / source_group):
            if number == chapter_number:
                return path
    return None


def mark_future_chapter_cards_stale(root: Path, to_chapter: int) -> set[int]:
    affected: set[int] = set()
    for path in sorted((root / "20_outline" / "chapter_cards").glob("ch*.json")):
        number = parse_chapter_number(path)
        if number is None or number <= to_chapter:
            continue
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            payload = {}
        payload["chapter_number"] = payload.get("chapter_number") or number
        payload["status"] = "stale"
        payload["stale_reason"] = f"rollback_to_ch{to_chapter:03d}"
        payload["stale_at"] = utc_now()
        write_json(path, payload)
        affected.add(number)
    return affected


def mark_chapter_plan_stale(root: Path, to_chapter: int) -> None:
    path = root / "20_outline" / "chapter_plan.json"
    payload = read_json(path, default=[])
    if not isinstance(payload, list):
        return
    changed = False
    for item in payload:
        if not isinstance(item, dict):
            continue
        number = as_int(item.get("chapter_number") or item.get("chapter"))
        if number > to_chapter:
            item["status"] = "stale"
            item["stale_reason"] = f"rollback_to_ch{to_chapter:03d}"
            changed = True
    if changed:
        write_json(path, payload)


def mark_future_writing_tasks_stale(root: Path, to_chapter: int) -> set[int]:
    affected: set[int] = set()
    task_dir = root / "50_workbench" / "writing_tasks"
    for path in sorted(task_dir.glob("ch*.json")):
        number = parse_chapter_number(path)
        if number is None or number <= to_chapter:
            continue
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            payload = {}
        payload["chapter_number"] = payload.get("chapter_number") or number
        payload["status"] = "stale"
        payload["stale_reason"] = f"rollback_to_ch{to_chapter:03d}"
        payload["stale_at"] = utc_now()
        write_json(path, payload)
        affected.add(number)
    return affected


def stale_paths(root: Path, stale_chapters: list[int]) -> dict[str, list[str]]:
    return {
        "chapter_cards": [
            relative_path(root, root / "20_outline" / "chapter_cards" / f"ch{number:03d}.json")
            for number in stale_chapters
            if (root / "20_outline" / "chapter_cards" / f"ch{number:03d}.json").exists()
        ],
        "writing_tasks": [
            relative_path(root, root / "50_workbench" / "writing_tasks" / f"ch{number:03d}.json")
            for number in stale_chapters
            if (root / "50_workbench" / "writing_tasks" / f"ch{number:03d}.json").exists()
        ],
        "rag_chunks": [
            relative_path(root, root / "60_rag" / "chunks" / f"ch{number:03d}.json")
            for number in stale_chapters
            if (root / "60_rag" / "chunks" / f"ch{number:03d}.json").exists()
        ],
        "graph": existing_paths(root, ("30_state/story_graph.json", "30_state/event_matrix.json")),
    }


def write_stale_markers(
    root: Path,
    *,
    to_chapter: int,
    stale_chapters: list[int],
    timestamp: str,
    rebuilt_quality_indexes: tuple[str, ...] = (),
) -> Path:
    payload = {
        "reason": f"rollback_to_ch{to_chapter:03d}",
        "to_chapter": to_chapter,
        "stale_chapters": stale_chapters,
        "indexes": [
            "chapter_cards_after_rollback",
            "writing_tasks_after_rollback",
            "rag_chunks",
            "story_graph",
            "event_matrix",
            "chapter_summaries_after_rollback",
        ],
        "rebuilt_quality_indexes": list(rebuilt_quality_indexes),
        "stale_paths": stale_paths(root, stale_chapters),
        "created_at": utc_now(),
    }
    state_report = root / "30_state" / "stale_indexes.json"
    rag_report = root / "60_rag" / "stale.json"
    graph_report = root / "30_state" / "story_graph_stale.json"
    event_report = root / "30_state" / "event_matrix_stale.json"
    task_report = root / "50_workbench" / "writing_tasks" / "stale.json"
    write_json(state_report, payload)
    write_json(rag_report, payload)
    write_json(graph_report, payload)
    write_json(event_report, payload)
    write_json(task_report, payload)
    return state_report


def update_rollback_state(root: Path, *, to_chapter: int, stale_chapters: list[int], timestamp: str, detached_dir: Path) -> Path:
    state_file = root / "30_state" / "novel_state.json"
    state = read_json(state_file, default={})
    if not isinstance(state, dict):
        state = {}
    stale = set(str(item) for item in normalize_list(state.get("stale")))
    stale.update(
        {
            "chapter_cards_after_rollback",
            "writing_tasks_after_rollback",
            "rag_chunks",
            "story_graph",
            "event_matrix",
            "chapter_summaries_after_rollback",
        }
    )
    state.update(
        {
            "status": f"rollback_to_ch{to_chapter:03d}",
            "current_chapter": to_chapter,
            "last_finalized_chapter": min(as_int(state.get("last_finalized_chapter")), to_chapter),
            "stale": sorted(stale),
            "stale_chapters": stale_chapters,
            "last_rollback": {
                "to_chapter": to_chapter,
                "stale_chapters": stale_chapters,
                "detached_dir": relative_path(root, detached_dir),
                "created_at": utc_now(),
                "id": timestamp,
            },
            "updated_at": utc_now(),
        }
    )
    write_json(state_file, state)
    return state_file


def upsert_chapter_meta(root: Path, record: dict[str, Any]) -> None:
    path = root / "40_manuscript" / "chapter_meta.jsonl"
    records = []
    number = as_int(record.get("chapter_number"))
    for item in read_jsonl(path):
        if as_int(item.get("chapter_number") or item.get("chapter") or item.get("number")) != number:
            records.append(item)
    records.append({key: value for key, value in record.items() if value is not None})
    atomic_write_text(path, "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records))


def stale_chapter_numbers(root: Path) -> set[int]:
    state = read_json(root / "30_state" / "novel_state.json", default={})
    if not isinstance(state, dict):
        return set()
    return {as_int(item) for item in normalize_list(state.get("stale_chapters")) if as_int(item) > 0}


def primary_status(statuses: tuple[str, ...]) -> str:
    for status in STATUS_ORDER:
        if status in statuses:
            return status
    return statuses[0] if statuses else "unknown"


def existing_paths(root: Path, relatives: tuple[str, ...]) -> list[str]:
    return [relative for relative in relatives if (root / relative).exists()]


def summary_candidates(root: Path, chapter_number: int) -> list[Path]:
    candidates = [manuscript_chapter_path(root, chapter_number, lane="summaries")]
    detached = root / "40_manuscript" / "detached"
    if detached.exists():
        for rollback_dir in sorted(path for path in detached.iterdir() if path.is_dir()):
            candidates.extend(
                path
                for number, path in list_canonical_chapter_files(rollback_dir / "summaries")
                if number == chapter_number
            )
    return candidates


def format_rollback_impact_markdown(payload: dict[str, Any]) -> str:
    impacted = payload.get("impacted", {})
    lines = [
        f"# Rollback Impact: ch{payload['to_chapter']:03d}",
        "",
        f"- To chapter: {payload['to_chapter']}",
        f"- Affected chapters: {', '.join(str(item) for item in payload.get('affected_chapters', [])) or 'none'}",
        f"- Detached dir: {payload.get('detached_dir')}",
        f"- Generated at: {payload.get('generated_at')}",
        "",
    ]
    labels = {
        "settings": "Affected Settings",
        "foreshadowing": "Affected Foreshadowing",
        "character_state": "Affected Character State",
        "summaries": "Affected Chapter Summaries",
        "chapter_cards": "Affected Chapter Cards",
        "graph_state": "Affected Graph/Event State",
    }
    for key, title in labels.items():
        lines.extend([f"## {title}", ""])
        lines.extend([f"- {item}" for item in impacted.get(key, [])] or ["- None"])
        lines.append("")
    lines.extend(
        [
            "## Required Follow-up",
            "",
            "- Review detached drafts before rewriting.",
            "- Rebuild RAG after accepting new chapter direction.",
            "- Run graph check before continuing long-range plot.",
            "",
        ]
    )
    return "\n".join(lines)


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RevisionError(f"Could not create unique path for {path}")


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").lstrip("\ufeff").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def extract_title(text: str, path: Path) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return path.stem


def parse_chapter_number(path: Path) -> int | None:
    numeric = re.search(r"(\d{1,5})", path.stem)
    return int(numeric.group(1)) if numeric else None


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    return [value]


def timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
