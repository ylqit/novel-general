"""Workflow orchestration for opening books and drafting chapters."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.agent_tasks import build_manifest, list_manifests, mark_tasks_for_chapter_type, mark_tasks_for_output, status_summary, write_manifest
from longform_engine.config import ConfigDocument
from longform_engine.creative import (
    humanizer_rules,
    init_creative_brief,
    load_creative_brief,
    validate_creative_brief,
    writer_craft_brief,
)
from longform_engine.db import sync_database
from longform_engine.editorial import editorial_finalization_blockers
from longform_engine.gates import gate_check
from longform_engine.graph import update_graph, validate_graph
from longform_engine.memory import build_style_memory, build_tcs
from longform_engine.planning import event_tier_for_types, recommend_event_types, record_event_usage
from longform_engine.rag import build_chunks, build_context
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root


class WorkflowError(ValueError):
    """Raised when a workflow command cannot safely proceed."""


@dataclass(frozen=True)
class OpenBookResult:
    """Files written by open-book."""

    idea_seed: str
    reader_contract: str
    book_outline: str
    state_file: str
    creative_brief: str = ""


@dataclass(frozen=True)
class ChapterPlanResult:
    """Generated chapter card paths."""

    chapter_number: int
    json_file: str
    markdown_file: str


@dataclass(frozen=True)
class BeatSheetResult:
    """Generated beat sheet paths."""

    chapter_number: int
    json_file: str
    markdown_file: str


@dataclass(frozen=True)
class ContinueWriteResult:
    """Artifacts produced by the continue-write pipeline."""

    chapter_number: int
    context_file: str
    chapter_card: str
    beat_sheet: str
    draft_file: str
    writing_task_json: str
    writing_task_markdown: str
    recommended_agent_draft: str
    next_command: str
    run_report: str
    status: str


@dataclass(frozen=True)
class DraftSubmitResult:
    """Artifacts produced when an Agent draft is submitted into the draft lane."""

    chapter_number: int
    draft_file: str
    submission_file: str
    gate_result: str
    pacing_review: str
    run_report: str
    passed: bool
    severity: str
    next_command: str
    db_synced: bool


@dataclass(frozen=True)
class ChapterFinalizeResult:
    """Artifacts produced when a gate-approved draft becomes final manuscript."""

    chapter_number: int
    final_file: str
    finalization_file: str
    summary_file: str
    gate_result: str
    graph_file: str
    rag_chunks_dir: str
    context_file: str
    run_report: str
    approved_by: str
    finalized_at: str
    next_command: str
    db_synced: bool


@dataclass(frozen=True)
class BatchWriteResult:
    """Safe batch scheduler result."""

    chapters_requested: int
    chapters_attempted: int
    finalized: int
    failed: int
    repaired: int
    skipped: int
    status: str
    run_report: str
    stopped_reason: str
    next_command: str


@dataclass(frozen=True)
class AutoWriteResult:
    """Persistent auto-write scheduler result."""

    action: str
    status: str
    state_file: str
    report_file: str
    target_chapters: int
    target_words: int
    current_chapter: int
    last_finalized_chapter: int
    chapters_attempted: int
    failure_count: int
    pause_reason: str
    next_command: str
    summary: str


def open_book(config: ConfigDocument, confirmations: dict[str, Any] | None = None) -> OpenBookResult:
    """Confirm the five opening items and write first project governance files."""

    root = resolve_project_root(config)
    confirmations = confirmations or {}
    resolved = resolve_confirmations(config, confirmations)

    idea_seed = root / "00_governance" / "idea_seed.md"
    reader_contract = root / "00_governance" / "reader_contract.md"
    book_outline = root / "20_outline" / "book_outline.md"
    state_file = root / "30_state" / "novel_state.json"

    novel = config.data.get("novel", {})
    length = config.data.get("length", {})
    forbidden = as_list(resolved["core_forbidden_zone"])

    atomic_write_text(
        idea_seed,
        "\n".join(
            [
                "# Idea Seed",
                "",
                "## Required Confirmations",
                "",
                f"- Target audience: {resolved['target_audience']}",
                f"- Writing style: {resolved['writing_style']}",
                f"- Core forbidden zone: {', '.join(forbidden)}",
                f"- Automation level: {resolved['automation_level']}",
                f"- Target scale: {resolved['target_scale']}",
                "",
                f"Confirmed at: {utc_now()}",
                "",
            ]
        ),
    )
    atomic_write_text(
        reader_contract,
        "\n".join(
            [
                "# Reader Contract",
                "",
                f"- Platform: {novel.get('target_platform', 'unknown')}",
                f"- Genre: {novel.get('genre', 'unknown')}",
                f"- Audience: {resolved['target_audience']}",
                f"- Core promise: {novel.get('core_promise', '待补充')}",
                f"- Main question: {novel.get('main_question', '待补充')}",
                "",
                "## Forbidden Experience",
                "",
                *[f"- {item}" for item in forbidden],
                "",
            ]
        ),
    )
    atomic_write_text(
        book_outline,
        "\n".join(
            [
                "# Book Outline",
                "",
                f"- Title: {config.data['project']['title']}",
                f"- Target chapters: {length.get('total_chapters')}",
                f"- Target total words: {length.get('target_total_words')}",
                f"- Volume count: {length.get('volume_count')}",
                f"- Main question: {novel.get('main_question', '待补充')}",
                f"- Ending direction: {novel.get('ending_direction', '待补充')}",
                "",
                "## Longform Guardrails",
                "",
                "- 不提前解决核心矛盾。",
                "- 每章必须服务读者契约、阶段目标或伏笔账本。",
                "- 改纲必须通过 revise-outline 和影响分析流程。",
                "",
            ]
        ),
    )

    state = load_json(state_file, default={})
    state.update(
        {
            "status": "open_book_confirmed",
            "current_chapter": int(state.get("current_chapter") or 0),
            "last_finalized_chapter": int(state.get("last_finalized_chapter") or 0),
            "required_confirmations": resolved,
            "updated_at": utc_now(),
        }
    )
    write_json(state_file, state)
    creative_result = init_creative_brief(
        config,
        confirmations={
            "target_audience": resolved["target_audience"],
            "writing_style": resolved["writing_style"],
            "core_taboo": forbidden,
            "automation_level": resolved["automation_level"],
            "target_scale": resolved["target_scale"],
        },
        overwrite=True,
    )
    return OpenBookResult(
        idea_seed=str(idea_seed),
        reader_contract=str(reader_contract),
        book_outline=str(book_outline),
        state_file=str(state_file),
        creative_brief=creative_result.brief_file,
    )


def plan_chapter(config: ConfigDocument, *, chapter_number: int, overwrite: bool = False) -> ChapterPlanResult:
    """Generate a deterministic chapter card for a target chapter."""

    if chapter_number <= 0:
        raise WorkflowError("chapter_number must be positive.")
    root = resolve_project_root(config)
    card_dir = root / "20_outline" / "chapter_cards"
    json_path = card_dir / f"ch{chapter_number:03d}.json"
    md_path = card_dir / f"ch{chapter_number:03d}.md"
    if json_path.exists() and md_path.exists() and not overwrite:
        return ChapterPlanResult(chapter_number=chapter_number, json_file=str(json_path), markdown_file=str(md_path))

    novel = config.data.get("novel", {})
    forbidden = as_list(novel.get("forbidden_experience")) + ["正文不得包含 TODO、写作说明、角色标签或 AI 自述。"]
    volume = infer_volume(config, chapter_number)
    anchor = current_outline_anchor(root, chapter_number)
    event_recommendation = asdict(recommend_event_types(config, chapter_number=chapter_number))
    graph_constraints = summarize_graph_constraints(root, chapter_number)
    title = f"第{chapter_number}章 待定章节"
    duty = "建立读者契约并打开第一层悬念。" if chapter_number == 1 else "承接上一章状态，推进一个明确的局部矛盾。"
    card = {
        "chapter_number": chapter_number,
        "title": title,
        "volume": volume,
        "status": "planned",
        "duty": duty,
        "conflict": "让主角在当前目标与外部阻力之间做出选择。",
        "information": "只释放与本章目标相关的一层信息，保留核心秘密。",
        "hook": "章末留下危机升级、收益未兑现或新信息反转。",
        "outline_anchor": anchor,
        "event_recommendation": event_recommendation,
        "reader_payoff": "Pay off one local promise while preserving the core longform mystery.",
        "forbidden_reveals": anchor.get("forbidden_reveals", []) if isinstance(anchor, dict) else [],
        "graph_constraints": graph_constraints,
        "rag_facts": ["Use next_plot_context.md as the only formal RAG packet."],
        "forbidden": forbidden,
        "required_context_files": [
            "60_rag/context/next_plot_context.md",
            "30_state/story_graph.json",
            "20_outline/outline_anchors.json",
        ],
        "created_at": utc_now(),
    }
    reverse_brake = build_reverse_brake_contract(config, chapter_number, anchor, card=card)
    card["reverse_brake"] = reverse_brake
    card["forbidden_reveals"] = reverse_brake["forbidden_reveals"]
    card["resolution_markers"] = reverse_brake["do_not_resolve"]
    card["requires_tail_suspense"] = reverse_brake["requires_tail_suspense"]
    card["allowed_reveal_level"] = reverse_brake["allowed_reveal_level"]
    card["must_preserve_suspense"] = reverse_brake["must_preserve_suspense"]
    write_json(json_path, card)
    atomic_write_text(
        md_path,
        "\n".join(
            [
                f"# {title}",
                "",
                f"- Chapter: {chapter_number}",
                f"- Volume: {volume}",
                f"- Duty: {card['duty']}",
                f"- Conflict: {card['conflict']}",
                f"- Information: {card['information']}",
                f"- Hook: {card['hook']}",
                f"- Outline anchor: {json.dumps(anchor, ensure_ascii=False)}",
                f"- Event recommendation: {', '.join(event_recommendation.get('recommended', []))}",
                f"- Event blocked: {', '.join(event_recommendation.get('blocked', [])) or 'none'}",
                f"- Event constraints: {', '.join(event_recommendation.get('constraints', [])) or 'none'}",
                f"- Soft event required: {event_recommendation.get('soft_event_required', False)}",
                f"- Reverse brake allowed reveal level: {reverse_brake['allowed_reveal_level']}",
                f"- Do not resolve: {', '.join(reverse_brake['do_not_resolve']) or 'none'}",
                f"- Must preserve suspense: {', '.join(reverse_brake['must_preserve_suspense']) or 'none'}",
                f"- Reader payoff: {card['reader_payoff']}",
                "",
                "## Forbidden",
                "",
                *[f"- {item}" for item in forbidden],
                "",
            ]
        ),
    )
    upsert_chapter_plan(root, card)
    return ChapterPlanResult(chapter_number=chapter_number, json_file=str(json_path), markdown_file=str(md_path))


def generate_beat_sheet(
    config: ConfigDocument,
    *,
    chapter_number: int,
    overwrite: bool = False,
    auto_plan: bool = False,
) -> BeatSheetResult:
    """Generate a beat sheet from an existing chapter card."""

    root = resolve_project_root(config)
    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    if not card_path.exists():
        if auto_plan:
            plan_chapter(config, chapter_number=chapter_number)
        else:
            raise WorkflowError(f"Chapter card missing: {card_path}")
    card = load_json(card_path, default={})
    beat_dir = root / "50_workbench" / "beats"
    json_path = beat_dir / f"ch{chapter_number:03d}.json"
    md_path = beat_dir / f"ch{chapter_number:03d}.md"
    if json_path.exists() and md_path.exists() and not overwrite:
        return BeatSheetResult(chapter_number=chapter_number, json_file=str(json_path), markdown_file=str(md_path))

    pacing_mode = str(config.data.get("pacing", {}).get("default_mode") or "balanced")
    event_types = card.get("event_recommendation", {}).get("recommended", []) if isinstance(card.get("event_recommendation"), dict) else []
    beats = [
        {
            "order": 1,
            "name": "Opening image",
            "pacing_mode": pacing_mode,
            "chapter_duty": card.get("duty"),
            "scene_purpose": "re-anchor current state, promise, and location",
            "conflict": "latent pressure",
            "turn": "goal becomes concrete",
            "hook": "micro question",
            "event_type": event_types[0] if event_types else "setup",
            "expansion_notes": "Start in-scene and avoid summary-only setup.",
            "purpose": "承接上一章状态，明确本章场景和目标。",
        },
        {
            "order": 2,
            "name": "Pressure",
            "pacing_mode": pacing_mode,
            "chapter_duty": card.get("duty"),
            "scene_purpose": "apply external resistance",
            "conflict": card.get("conflict", "制造外部阻力。"),
            "turn": "cost appears",
            "hook": "pressure escalates",
            "event_type": event_types[1] if len(event_types) > 1 else "conflict",
            "expansion_notes": "Use action, dialogue, or concrete consequence before exposition.",
            "purpose": card.get("conflict", "制造外部阻力。"),
        },
        {
            "order": 3,
            "name": "Choice",
            "pacing_mode": pacing_mode,
            "chapter_duty": card.get("duty"),
            "scene_purpose": "force a non-free decision",
            "conflict": "goal versus cost",
            "turn": "protagonist commits",
            "hook": "choice creates a new risk",
            "event_type": "choice",
            "expansion_notes": "Make the decision visible through behavior, not explanation.",
            "purpose": "让主角做出带代价的选择。",
        },
        {
            "order": 4,
            "name": "Turn",
            "pacing_mode": pacing_mode,
            "chapter_duty": card.get("duty"),
            "scene_purpose": "release one controlled information layer",
            "conflict": "new fact changes the problem",
            "turn": card.get("information", "释放一层新信息。"),
            "hook": "meaning reframed",
            "event_type": "reveal",
            "expansion_notes": "Do not solve the core conflict unless the anchor marks closure.",
            "purpose": card.get("information", "释放一层新信息。"),
        },
        {
            "order": 5,
            "name": "Hook",
            "pacing_mode": pacing_mode,
            "chapter_duty": card.get("duty"),
            "scene_purpose": "deliver tail suspense",
            "conflict": "payoff withheld or inverted",
            "turn": "chapter meaning sharpens",
            "hook": card.get("hook", "章末保留期待。"),
            "event_type": "tail_hook",
            "expansion_notes": "End with a concrete unanswered image, decision, threat, or reveal.",
            "purpose": card.get("hook", "章末保留期待。"),
        },
    ]
    for beat in beats:
        beat.setdefault("scene_tension", "make pressure visible through action, cost, or withheld information")
        beat.setdefault("reader_payoff", card.get("reader_payoff", "one local payoff without core-resolution leakage"))
        beat.setdefault("dialogue_intent", "each exchange must reveal pressure, status, concealment, or relationship movement")
        beat.setdefault("sensory_anchor", "ground this beat in one concrete sensory or body detail")
        beat.setdefault("ending_hook", card.get("hook", "close the beat on a changed problem"))
        beat.setdefault("scene_goal", beat.get("scene_purpose") or beat.get("purpose") or "advance the chapter duty in-scene")
        beat.setdefault("conflict_point", beat.get("conflict") or card.get("conflict") or "visible pressure against the current goal")
        beat.setdefault("information_release", beat.get("turn") or card.get("information") or "release only what this beat needs")
        beat.setdefault(
            "expansion_requirements",
            {
                "scene": "write this beat as concrete scene material, not synopsis",
                "dialogue": beat.get("dialogue_intent"),
                "psychology": "show inner pressure through action, hesitation, body cost, or subtext before naming emotion",
                "action": "include one visible choice, movement, or consequence",
                "transition": "exit with a changed problem, location pressure, or relationship state",
            },
        )
        beat.setdefault(
            "avoid_repetition",
            [
                "do not repeat the previous beat's scene shape",
                "do not restate the chapter duty as exposition",
                "do not solve the core mystery inside this beat",
            ],
        )
        beat.setdefault("forbidden_reveals", as_list(card.get("forbidden_reveals")))
        beat.setdefault("must_preserve_suspense", card.get("hook", "preserve the chapter tail question"))
    payload = {
        "chapter_number": chapter_number,
        "title": card.get("title", f"第{chapter_number}章"),
        "source_card": relative_path(root, card_path),
        "beats": beats,
        "created_at": utc_now(),
    }
    write_json(json_path, payload)
    atomic_write_text(
        md_path,
        "\n".join(
            [
                f"# Beat Sheet ch{chapter_number:03d}",
                "",
                f"- Source card: `{relative_path(root, card_path)}`",
                "",
                *[f"## Beat {beat['order']}: {beat['name']}\n\n{beat['purpose']}\n" for beat in beats],
            ]
        ),
    )
    return BeatSheetResult(chapter_number=chapter_number, json_file=str(json_path), markdown_file=str(md_path))


def continue_write(config: ConfigDocument, *, chapter_number: int | None = None, overwrite: bool = False) -> ContinueWriteResult:
    """Prepare the next chapter according to the configured writing mode."""

    root = resolve_project_root(config)
    state_path = root / "30_state" / "novel_state.json"
    state = load_json(state_path, default={})
    if chapter_number is None:
        chapter_number = int(state.get("last_finalized_chapter") or 0) + 1
    if chapter_number <= 0:
        raise WorkflowError("chapter_number must be positive.")

    verify_stale_indexes(root, chapter_number)
    verify_previous_gate(root, chapter_number)
    creative_brief = validate_creative_brief(config)
    if not creative_brief.ok and any("missing 10_bible/creative_brief.json" in error for error in creative_brief.errors):
        creative_brief = init_creative_brief(config)
    if not creative_brief.ok:
        raise WorkflowError(
            "Creative brief is missing or incomplete; review "
            f"{creative_brief.task_file} and run creative brief --init before continue-write."
        )
    graph_validation = validate_graph(config)
    if graph_validation.errors:
        raise WorkflowError("Story graph has validation errors; run graph validate/check before continue-write.")

    tcs = build_tcs(config, chapter_number=chapter_number)
    context = build_context(config, chapter_number=chapter_number)
    card = plan_chapter(config, chapter_number=chapter_number, overwrite=overwrite)
    beat = generate_beat_sheet(config, chapter_number=chapter_number, overwrite=overwrite)
    writing_mode = str(config.data.get("writing", {}).get("mode", "agent_skill"))

    if writing_mode == "agent_skill":
        task = write_writing_task(
            config,
            chapter_number=chapter_number,
            context_file=Path(context.context_file),
            chapter_card_file=Path(card.json_file),
            beat_sheet_file=Path(beat.json_file),
            overwrite=overwrite,
        )
        state.update(
            {
                "status": "task_ready",
                "current_chapter": chapter_number,
                "pending_task_chapter": chapter_number,
                "last_pipeline": "continue-write",
                "writing_mode": "agent_skill",
                "last_writing_task": task["task_markdown"],
                "updated_at": utc_now(),
            }
        )
        state.pop("pending_gate_chapter", None)
        write_json(state_path, state)
        sync_database(config)

        report_path = root / "70_runtime" / "run_reports" / f"continue_write_ch{chapter_number:03d}.json"
        report = {
            "command": "continue-write",
            "chapter_number": chapter_number,
            "status": "task_ready",
            "writing_mode": "agent_skill",
            "stages": [
                "load_config",
                "verify_previous_gate",
                "query_rag_context",
                "validate_graph",
                "build_tcs",
                "make_chapter_card",
                "generate_beat_sheet",
                "write_agent_task",
                "sync_indexes",
            ],
            "artifacts": {
                "context": context.context_file,
                "tcs": tcs.tcs_file,
                "chapter_card": card.json_file,
                "beat_sheet": beat.markdown_file,
                "writing_task_json": task["task_json"],
                "writing_task_markdown": task["task_markdown"],
                "recommended_agent_draft": task["recommended_agent_draft"],
            },
            "next_command": task["next_command"],
            "created_at": utc_now(),
        }
        write_json(report_path, report)
        return ContinueWriteResult(
            chapter_number=chapter_number,
            context_file=context.context_file,
            chapter_card=card.json_file,
            beat_sheet=beat.markdown_file,
            draft_file="",
            writing_task_json=task["task_json"],
            writing_task_markdown=task["task_markdown"],
            recommended_agent_draft=task["recommended_agent_draft"],
            next_command=task["next_command"],
            run_report=str(report_path),
            status="task_ready",
        )

    if writing_mode == "api_provider":
        raise WorkflowError("writing.mode api_provider is reserved for a future provider implementation.")

    draft_path = write_draft(config, chapter_number=chapter_number, overwrite=overwrite)
    gate = gate_check(config, chapter_number=chapter_number)

    state.update(
        {
            "status": "draft_ready",
            "current_chapter": chapter_number,
            "pending_gate_chapter": chapter_number,
            "last_pipeline": "continue-write",
            "writing_mode": "template_dry_run",
            "updated_at": utc_now(),
        }
    )
    state.pop("pending_task_chapter", None)
    write_json(state_path, state)
    sync_database(config)

    report_path = root / "70_runtime" / "run_reports" / f"continue_write_ch{chapter_number:03d}.json"
    report = {
        "command": "continue-write",
        "chapter_number": chapter_number,
        "status": "draft_ready_pending_gates",
        "writing_mode": "template_dry_run",
        "stages": [
            "load_config",
            "verify_previous_gate",
            "query_rag_context",
            "validate_graph",
            "build_tcs",
            "make_chapter_card",
            "generate_beat_sheet",
            "draft_chapter",
            "sync_indexes",
        ],
        "artifacts": {
            "context": context.context_file,
            "tcs": tcs.tcs_file,
            "chapter_card": card.json_file,
            "beat_sheet": beat.markdown_file,
            "draft": str(draft_path),
            "gate_result": gate.gate_result,
        },
        "created_at": utc_now(),
    }
    write_json(report_path, report)
    return ContinueWriteResult(
        chapter_number=chapter_number,
        context_file=context.context_file,
        chapter_card=card.json_file,
        beat_sheet=beat.markdown_file,
        draft_file=str(draft_path),
        writing_task_json="",
        writing_task_markdown="",
        recommended_agent_draft="",
        next_command="continue-write",
        run_report=str(report_path),
        status="draft_ready_gate_passed" if gate.passed else "draft_ready_gate_failed",
    )


def submit_agent_draft(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
    agent: str = "codex",
    overwrite: bool = False,
) -> DraftSubmitResult:
    """Submit an Agent-authored draft through the controlled draft lane."""

    if chapter_number <= 0:
        raise WorkflowError("chapter_number must be positive.")
    agent = normalize_agent(agent)
    root = resolve_project_root(config)
    source_path = resolve_agent_draft_source(root, config, file_path)
    if not source_path.exists() or not source_path.is_file():
        raise WorkflowError(f"Agent draft not found: {source_path}")
    ensure_agent_draft_source(config, root, source_path)
    text = safe_read_text(source_path).strip()
    if not text:
        raise WorkflowError("Agent draft is empty.")

    draft_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    if draft_path.exists() and not overwrite:
        raise WorkflowError(f"Draft already exists for ch{chapter_number:03d}; pass --overwrite to replace it.")

    atomic_write_text(draft_path, text + "\n")
    submitted_at = utc_now()
    submission_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.submission.json"
    task_path = root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json"
    submission = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "agent": agent,
        "source_file": relative_path(root, source_path),
        "draft_file": relative_path(root, draft_path),
        "writing_task": relative_path(root, task_path) if task_path.exists() else None,
        "source_sha256": sha256_bytes(source_path.read_bytes()),
        "draft_sha256": sha256_text(text + "\n"),
        "word_count": estimate_words(text),
        "submitted_at": submitted_at,
    }
    write_json(submission_path, submission)

    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=source_path,
        to_status="submitted",
        command="draft submit",
        result=draft_path,
        from_statuses=("awaiting_agent",),
    )
    gate = gate_check(config, chapter_number=chapter_number, source="draft")
    gate_path = Path(gate.gate_result)
    pacing_path = gate_path.parent / "pacing_review.md"
    next_command = (
        f"chapter finalize --chapter {chapter_number} --approved-by human"
        if gate.passed
        else f"repair-chapter --chapter {chapter_number} --plan-only"
    )
    normalize_agent_gate_result(gate_path, gate.passed, next_command)

    upsert_chapter_meta(
        root,
        {
            "chapter_number": chapter_number,
            "title": extract_title(text, chapter_number),
            "path": relative_path(root, draft_path),
            "status": "gate_passed" if gate.passed else "gate_failed",
            "word_count": estimate_words(text),
            "agent": agent,
            "submission_file": relative_path(root, submission_path),
            "submitted_at": submitted_at,
            "gate_result": relative_path(root, gate_path),
        },
    )

    state_path = root / "30_state" / "novel_state.json"
    state = load_json(state_path, default={})
    state.update(
        {
            "status": "gate_passed_pending_finalize" if gate.passed else "gate_failed",
            "current_chapter": chapter_number,
            "pending_gate_chapter": chapter_number,
            "last_pipeline": "draft submit",
            "writing_mode": "agent_skill",
            "last_agent_draft": relative_path(root, source_path),
            "last_draft_submission": relative_path(root, submission_path),
            "last_gate_result": relative_path(root, gate_path),
            "updated_at": utc_now(),
        }
    )
    if gate.passed:
        state["pending_final_chapter"] = chapter_number
    else:
        state.pop("pending_final_chapter", None)
    state.pop("pending_task_chapter", None)
    write_json(state_path, state)

    stats = sync_database(config)
    run_report = root / "70_runtime" / "run_reports" / f"draft_submit_ch{chapter_number:03d}.json"
    report = {
        "command": "draft submit",
        "chapter_number": chapter_number,
        "status": state["status"],
        "agent": agent,
        "artifacts": {
            "source": relative_path(root, source_path),
            "draft": relative_path(root, draft_path),
            "submission": relative_path(root, submission_path),
            "gate_result": relative_path(root, gate_path),
            "pacing_review": relative_path(root, pacing_path),
        },
        "gate": {
            "passed": gate.passed,
            "severity": gate.severity,
            "failures": list(gate.failures),
            "allowed_actions": list(gate.allowed_actions),
        },
        "db_sync": asdict(stats),
        "next_command": next_command,
        "created_at": utc_now(),
    }
    write_json(run_report, report)
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=source_path,
        to_status="validated" if gate.passed else "invalid",
        command="gate-check",
        result=gate_path,
        from_statuses=("submitted",),
    )

    return DraftSubmitResult(
        chapter_number=chapter_number,
        draft_file=str(draft_path),
        submission_file=str(submission_path),
        gate_result=str(gate_path),
        pacing_review=str(pacing_path),
        run_report=str(run_report),
        passed=gate.passed,
        severity=gate.severity,
        next_command=next_command,
        db_synced=True,
    )


def finalize_chapter(
    config: ConfigDocument,
    *,
    chapter_number: int,
    approved_by: str = "human",
    overwrite: bool = False,
) -> ChapterFinalizeResult:
    """Finalize a gate-approved draft and refresh long-term indexes."""

    if chapter_number <= 0:
        raise WorkflowError("chapter_number must be positive.")
    approved_by = str(approved_by or "").strip()
    if not approved_by:
        raise WorkflowError("approved_by is required.")

    root = resolve_project_root(config)
    draft_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    if not draft_path.exists():
        raise WorkflowError(f"Draft not found for ch{chapter_number:03d}; run draft submit first.")

    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    gate = require_finalizable_gate(gate_path, chapter_number)
    editorial_blockers = editorial_finalization_blockers(config, chapter_number=chapter_number)
    if editorial_blockers:
        raise WorkflowError(
            f"Chapter ch{chapter_number:03d} is not finalizable: editorial aggregate requires human review "
            f"({', '.join(editorial_blockers)})."
        )

    text = safe_read_text(draft_path).strip()
    if not text:
        raise WorkflowError(f"Draft is empty for ch{chapter_number:03d}.")
    final_path = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    if final_path.exists() and not overwrite:
        raise WorkflowError(f"Final manuscript already exists for ch{chapter_number:03d}; pass --overwrite to replace it.")

    finalization_path = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.finalization.json"
    summary_path = root / "40_manuscript" / "summaries" / f"ch{chapter_number:03d}.md"
    state_path = root / "30_state" / "novel_state.json"
    run_report = root / "70_runtime" / "run_reports" / f"chapter_finalize_ch{chapter_number:03d}.json"
    next_command = f"continue-write --chapter {chapter_number + 1}"
    with apply_transaction(
        root,
        command="chapter finalize",
        chapter_number=chapter_number,
        source_paths=[draft_path, gate_path],
        touched_paths=[
            final_path,
            finalization_path,
            summary_path,
            root / "40_manuscript" / "chapter_meta.jsonl",
            root / "30_state",
            root / "60_rag",
            state_path,
            run_report,
            root / "70_runtime" / "db",
        ],
        metadata={
            "approved_by": approved_by,
            "rebuild_boundaries": ["RAG rebuild/sync", "SQLite sync"],
        },
    ) as transaction:
        final_text = text + "\n"
        atomic_write_text(final_path, final_text)
        finalized_at = utc_now()
        summary_path = write_chapter_summary(root, chapter_number, final_text, overwrite=overwrite)
        finalization = {
            "schema_version": 1,
            "chapter_number": chapter_number,
            "approved_by": approved_by,
            "finalized_at": finalized_at,
            "draft_file": relative_path(root, draft_path),
            "final_file": relative_path(root, final_path),
            "summary_file": relative_path(root, summary_path),
            "gate_result": relative_path(root, gate_path),
            "gate_passed": bool(gate.get("passed")),
            "gate_waived": gate_has_waiver(gate),
            "draft_sha256": sha256_text(final_text),
            "final_sha256": sha256_text(final_text),
        }
        write_json(finalization_path, finalization)

        upsert_chapter_meta(
            root,
            {
                "chapter_number": chapter_number,
                "title": extract_title(final_text, chapter_number),
                "path": relative_path(root, final_path),
                "summary": safe_read_text(summary_path).strip(),
                "volume": infer_volume(config, chapter_number),
                "status": "final",
                "word_count": estimate_words(final_text),
                "approved_by": approved_by,
                "finalization_file": relative_path(root, finalization_path),
                "finalized_at": finalized_at,
                "gate_result": relative_path(root, gate_path),
            },
        )

        graph = update_graph(config, chapter_number=chapter_number)
        style_memory = build_style_memory(config)
        record_finalized_event_usage(config, root, chapter_number)
        rag = build_chunks(config)
        context = build_context(config, chapter_number=chapter_number + 1)

        state = load_json(state_path, default={})
        last_finalized = max(int(state.get("last_finalized_chapter") or 0), chapter_number)
        state.update(
            {
                "status": "chapter_finalized",
                "current_chapter": chapter_number,
                "last_finalized_chapter": last_finalized,
                "last_pipeline": "chapter finalize",
                "last_finalized_file": relative_path(root, final_path),
                "last_finalization": relative_path(root, finalization_path),
                "updated_at": utc_now(),
            }
        )
        for key in ("pending_task_chapter", "pending_gate_chapter", "pending_final_chapter"):
            if int(state.get(key) or 0) == chapter_number:
                state.pop(key, None)
        write_json(state_path, state)

        stats = sync_database(config)
        report = {
            "command": "chapter finalize",
            "chapter_number": chapter_number,
            "status": "chapter_finalized",
            "approved_by": approved_by,
            "artifacts": {
                "draft": relative_path(root, draft_path),
                "final": relative_path(root, final_path),
                "summary": relative_path(root, summary_path),
                "finalization": relative_path(root, finalization_path),
                "gate_result": relative_path(root, gate_path),
                "graph": relative_path(root, Path(graph.graph_file)),
                "style_memory": relative_path(root, Path(style_memory.style_file)),
                "rag_chunks_dir": relative_path(root, Path(rag.output_dir)),
                "next_plot_context": relative_path(root, Path(context.context_file)),
            },
            "gate": {
                "passed": bool(gate.get("passed")),
                "waived": gate_has_waiver(gate),
                "severity": gate.get("severity"),
            },
            "graph": asdict(graph),
            "style_memory": asdict(style_memory),
            "rag": asdict(rag),
            "context": asdict(context),
            "db_sync": asdict(stats),
            "next_command": next_command,
            "created_at": utc_now(),
        }
        write_json(run_report, report)
        transaction.update_metadata(
            gate_passed=bool(gate.get("passed")),
            gate_waived=gate_has_waiver(gate),
            db_synced=True,
            run_report=relative_path(root, run_report),
            rag_chunks_dir=relative_path(root, Path(rag.output_dir)),
            db_sync=asdict(stats),
        )
    mark_tasks_for_chapter_type(
        root,
        chapter_number=chapter_number,
        task_types=("chapter_write", "repair", "humanize", "content_expand"),
        to_status="applied",
        command="chapter finalize",
        artifact=draft_path,
        result=final_path,
        from_statuses=("validated",),
    )

    return ChapterFinalizeResult(
        chapter_number=chapter_number,
        final_file=str(final_path),
        finalization_file=str(finalization_path),
        summary_file=str(summary_path),
        gate_result=str(gate_path),
        graph_file=graph.graph_file,
        rag_chunks_dir=rag.output_dir,
        context_file=context.context_file,
        run_report=str(run_report),
        approved_by=approved_by,
        finalized_at=finalized_at,
        next_command=next_command,
        db_synced=True,
    )


def require_finalizable_gate(gate_path: Path, chapter_number: int) -> dict[str, Any]:
    if not gate_path.exists():
        raise WorkflowError(f"gate_result.json not found for ch{chapter_number:03d}; run draft submit or gate-check first.")
    gate = load_json(gate_path, default={})
    if not isinstance(gate, dict):
        raise WorkflowError(f"Invalid gate_result.json for ch{chapter_number:03d}.")
    if gate.get("passed") is True:
        return gate
    if gate_has_waiver(gate):
        return gate
    raise WorkflowError(f"Chapter ch{chapter_number:03d} is not finalizable: gate failed and no waiver was recorded.")


def gate_has_waiver(gate: dict[str, Any]) -> bool:
    waiver = gate.get("waiver")
    if isinstance(waiver, dict) and waiver.get("allowed") is True:
        return True
    return gate.get("waived") is True


def write_chapter_summary(root: Path, chapter_number: int, text: str, *, overwrite: bool) -> Path:
    summary_path = root / "40_manuscript" / "summaries" / f"ch{chapter_number:03d}.md"
    if summary_path.exists() and not overwrite:
        return summary_path
    summary = summarize_final_text(text)
    atomic_write_text(
        summary_path,
        "\n".join(
            [
                f"# Summary ch{chapter_number:03d}",
                "",
                summary,
                "",
            ]
        ),
    )
    return summary_path


def summarize_final_text(text: str) -> str:
    body = "\n".join(line for line in text.splitlines() if not line.strip().startswith("#"))
    compact = re.sub(r"\s+", " ", body).strip()
    if not compact:
        return "No summary available."
    return compact[:240].rstrip() + ("..." if len(compact) > 240 else "")


def write_writing_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    context_file: Path,
    chapter_card_file: Path,
    beat_sheet_file: Path,
    overwrite: bool,
) -> dict[str, str]:
    root = resolve_project_root(config)
    writing = config.data.get("writing", {})
    agent = writing.get("agent", {}) if isinstance(writing.get("agent"), dict) else {}
    task_dir = root / str(agent.get("task_dir") or "50_workbench/writing_tasks")
    draft_dir = root / str(agent.get("draft_dir") or "50_workbench/agent_drafts")
    default_agent = str(agent.get("default_agent") or "codex")
    task_json = task_dir / f"ch{chapter_number:03d}.json"
    task_markdown = task_dir / f"ch{chapter_number:03d}.md"
    manifest_file = task_dir / f"ch{chapter_number:03d}.agent_task.json"
    recommended_draft = draft_dir / f"ch{chapter_number:03d}.{default_agent}.md"
    feedback_carryover = build_feedback_carryover(root, chapter_number)
    feedback_files = feedback_source_paths(root, feedback_carryover)
    if task_json.exists() and task_markdown.exists() and not overwrite:
        if not manifest_file.exists():
            write_manifest(
                root,
                build_manifest(
                    root,
                    task_type="chapter_write",
                    chapter_number=chapter_number,
                    input_files=chapter_write_manifest_inputs(
                        root,
                        chapter_number,
                        task_json,
                        task_markdown,
                        context_file,
                        chapter_card_file,
                        beat_sheet_file,
                        extra_files=feedback_files,
                    ),
                    allowed_output_paths=[recommended_draft],
                    output_schema="markdown_chapter_only",
                    validate_command=draft_submit_command(root, chapter_number, recommended_draft, default_agent),
                    apply_command=f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
                    failure_next_command=f"longform-engine repair-chapter project.yaml --chapter {chapter_number} --plan-only",
                ),
                manifest_file,
            )
        return {
            "task_json": str(task_json),
            "task_markdown": str(task_markdown),
            "recommended_agent_draft": str(recommended_draft),
            "next_command": draft_submit_command(root, chapter_number, recommended_draft, default_agent),
        }

    card = load_json(chapter_card_file, default={})
    beat = load_json(beat_sheet_file, default={})
    story_graph_path = root / "30_state" / "story_graph.json"
    story_graph = load_json(story_graph_path, default={})
    length = config.data.get("length", {})
    target_words = int(length.get("chapter_word_count", {}).get("target") or 3000)
    context_text = context_file.read_text(encoding="utf-8") if context_file.exists() else ""
    graph_summary = summarize_story_graph(story_graph)
    canon_research = load_recent_research_canon(root)
    style_context = load_style_context(root)
    gate_history = load_gate_history(root, limit=5)
    creative_brief = load_creative_brief(root)
    tcs_path = root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json"
    tcs_payload = load_json(tcs_path, default={})
    if not isinstance(tcs_payload, dict):
        tcs_payload = {}
    graph_constraints = card.get("graph_constraints") if isinstance(card.get("graph_constraints"), dict) else {}
    outline_anchor = card.get("outline_anchor") if isinstance(card.get("outline_anchor"), dict) else {}
    event_recommendation = card.get("event_recommendation") if isinstance(card.get("event_recommendation"), dict) else {}
    craft_brief = writer_craft_brief(
        config,
        chapter_number=chapter_number,
        card=card if isinstance(card, dict) else {},
        beat=beat if isinstance(beat, dict) else {},
        tcs=tcs_payload,
        style_context=style_context,
    )
    writing_brief = build_writable_brief(
        config,
        root,
        chapter_number=chapter_number,
        card=card if isinstance(card, dict) else {},
        beat=beat if isinstance(beat, dict) else {},
        tcs=tcs_payload,
        outline_anchor=outline_anchor,
        event_recommendation=event_recommendation,
        style_context=style_context,
        craft_brief=craft_brief,
    )
    beat_requirements = build_beat_expansion_requirements(
        beat if isinstance(beat, dict) else {},
        card=card if isinstance(card, dict) else {},
        writing_brief=writing_brief,
    )
    constraint_packet = build_constraint_packet(
        root,
        context_file=context_file,
        context_text=context_text,
        story_graph_path=story_graph_path,
        story_graph=story_graph,
        graph_summary=graph_summary,
        graph_constraints=graph_constraints,
        tcs_path=tcs_path,
        tcs_payload=tcs_payload,
        outline_anchor=outline_anchor,
        event_recommendation=event_recommendation,
        style_context=style_context,
        card=card if isinstance(card, dict) else {},
        canon_research=canon_research,
    )
    next_command = draft_submit_command(root, chapter_number, recommended_draft, default_agent)
    payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "title": card.get("title", f"第{chapter_number}章"),
        "status": "task_ready",
        "writing_mode": "agent_skill",
        "target_word_count": target_words,
        "chapter_card": {
            "path": relative_path(root, chapter_card_file),
            "data": card,
        },
        "beat_sheet": {
            "path": relative_path(root, beat_sheet_file),
            "data": beat,
        },
        "rag_context": {
            "path": relative_path(root, context_file),
            "text": context_text,
        },
        "story_graph": {
            "path": relative_path(root, story_graph_path),
            "summary": graph_summary,
            "constraints": graph_constraints,
        },
        "temporal_context_state": {
            "path": relative_path(root, tcs_path) if tcs_path.exists() else "",
            "data": tcs_payload,
        },
        "outline_anchor": outline_anchor,
        "event_recommendation": event_recommendation,
        "style_context": style_context,
        "creative_brief": creative_brief,
        "writing_brief": writing_brief,
        "beat_expansion_requirements": beat_requirements,
        "constraint_packet": constraint_packet,
        "writer_craft_brief": craft_brief,
        "humanizer_rules": humanizer_rules(),
        "gate_history": gate_history,
        "feedback_carryover": feedback_carryover,
        "canon_research": canon_research,
        "forbidden": as_list(card.get("forbidden")),
        "forbidden_reveals": as_list(card.get("forbidden_reveals")),
        "output_contract": {
            "format": "markdown_chapter_only",
            "target_word_count": target_words,
            "write_to": relative_path(root, recommended_draft),
            "must_not_include": [
                "TODO",
                "写作说明",
                "AI 自述",
                "角色定位标签",
                "prompt 残留",
            ],
            "must_follow": [
                "只输出小说正文和章节标题。",
                "遵守章节卡职责、Beat Sheet 顺序和 RAG 上下文。",
                "不得直接修改 final、RAG、story_graph 或 SQLite。",
                "遵守 Creative Brief、Writer Craft Brief 和 Humanizer v2 自查规则。",
            ],
        },
        "draft_submission_path": relative_path(root, recommended_draft),
        "next_command": next_command,
        "created_at": utc_now(),
    }
    manifest = build_manifest(
        root,
        task_type="chapter_write",
        chapter_number=chapter_number,
        input_files=chapter_write_manifest_inputs(
            root,
            chapter_number,
            task_json,
            task_markdown,
            context_file,
            chapter_card_file,
            beat_sheet_file,
            extra_files=feedback_files,
        ),
        allowed_output_paths=[recommended_draft],
        output_schema="markdown_chapter_only",
        validate_command=next_command,
        apply_command=f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
        failure_next_command=f"longform-engine repair-chapter project.yaml --chapter {chapter_number} --plan-only",
    )
    payload["agent_task_manifest"] = relative_path(root, manifest_file)
    write_json(task_json, payload)
    atomic_write_text(task_markdown, format_writing_task_markdown(root, payload))
    write_manifest(root, manifest, manifest_file)
    return {
        "task_json": str(task_json),
        "task_markdown": str(task_markdown),
        "recommended_agent_draft": str(recommended_draft),
        "next_command": next_command,
    }


def draft_submit_command(root: Path, chapter_number: int, draft_path: Path, agent: str) -> str:
    return (
        f"longform-engine draft submit project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, draft_path)} --agent {agent}"
    )


def chapter_write_manifest_inputs(
    root: Path,
    chapter_number: int,
    task_json: Path,
    task_markdown: Path,
    context_file: Path,
    chapter_card_file: Path,
    beat_sheet_file: Path,
    *,
    extra_files: list[Path] | tuple[Path, ...] | None = None,
) -> list[Path]:
    inputs = [
        task_json,
        task_markdown,
        context_file,
        chapter_card_file,
        beat_sheet_file,
        root / "30_state" / "story_graph.json",
        root / "30_state" / "event_matrix.json",
        root / "30_state" / "pacing_history.json",
        root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json",
        root / "10_bible" / "style_bible.md",
        root / "10_bible" / "creative_brief.json",
        root / "60_rag" / "context" / "next_plot_context.md",
    ]
    inputs.extend(extra_files or [])
    return [path for path in inputs if path.exists() or path in {task_json, task_markdown}]


def batch_write(
    config: ConfigDocument,
    *,
    chapters: int,
    stop_on_gate_failure: bool = True,
) -> BatchWriteResult:
    """Safe scheduler over the existing chapter transaction loop."""

    if chapters <= 0:
        raise WorkflowError("chapters must be positive.")
    root = resolve_project_root(config)
    state = load_json(root / "30_state" / "novel_state.json", default={})
    next_chapter = int(state.get("last_finalized_chapter") or 0) + 1
    writing_mode = str(config.data.get("writing", {}).get("mode", "agent_skill"))
    attempted = finalized = failed = repaired = skipped = 0
    status = "completed"
    stopped_reason = ""
    next_command = ""
    started = utc_now()

    for offset in range(chapters):
        chapter_number = next_chapter + offset
        try:
            result = continue_write(config, chapter_number=chapter_number)
            attempted += 1
        except WorkflowError as exc:
            failed += 1
            status = "blocked"
            stopped_reason = str(exc)
            next_command = f"repair-chapter --chapter {chapter_number} --plan-only"
            break

        if writing_mode == "agent_skill":
            status = "awaiting_agent_draft"
            stopped_reason = "agent_skill mode generated a task package and waits for draft submit"
            next_command = result.next_command
            break

        if result.status.endswith("gate_failed"):
            failed += 1
            status = "gate_failed"
            stopped_reason = f"gate failed for ch{chapter_number:03d}"
            next_command = f"repair-chapter --chapter {chapter_number} --plan-only"
            if stop_on_gate_failure:
                break
        else:
            skipped += 1

    report_path = root / "70_runtime" / "run_reports" / f"batch_write_{safe_timestamp(started)}.json"
    payload = {
        "command": "batch-write",
        "chapters_requested": chapters,
        "chapters_attempted": attempted,
        "finalized": finalized,
        "failed": failed,
        "repaired": repaired,
        "skipped": skipped,
        "status": status,
        "stopped_reason": stopped_reason,
        "next_command": next_command,
        "runtime": {"started_at": started, "ended_at": utc_now()},
    }
    write_json(report_path, payload)
    return BatchWriteResult(
        chapters_requested=chapters,
        chapters_attempted=attempted,
        finalized=finalized,
        failed=failed,
        repaired=repaired,
        skipped=skipped,
        status=status,
        run_report=str(report_path),
        stopped_reason=stopped_reason,
        next_command=next_command,
    )


def auto_write_plan(
    config: ConfigDocument,
    *,
    target_chapters: int | None = None,
    target_words: int | None = None,
    start_chapter: int | None = None,
    overwrite: bool = False,
) -> AutoWriteResult:
    """Create the persistent auto-write scheduler state."""

    root = resolve_project_root(config)
    state_path = auto_write_state_path(root)
    if state_path.exists() and not overwrite:
        state = reconcile_auto_write_state(config, root, load_auto_write_state(root))
        state["updated_at"] = utc_now()
        write_json(state_path, state)
        return auto_write_result(
            root,
            state,
            action="plan",
            report_file="",
            summary="Auto-write plan already exists. Use --overwrite to reset it.",
        )

    novel_state = load_json(root / "30_state" / "novel_state.json", default={})
    last_finalized = highest_finalized_chapter(root, novel_state)
    length = config.data.get("length", {})
    planned_target_chapters = int(target_chapters or length.get("total_chapters") or max(1, last_finalized + 1))
    planned_target_words = int(target_words or length.get("target_total_words") or 0)
    planned_start = int(start_chapter or last_finalized + 1)
    if planned_target_chapters <= 0:
        raise WorkflowError("target_chapters must be positive.")
    if planned_target_words < 0:
        raise WorkflowError("target_words cannot be negative.")
    if planned_start <= 0:
        raise WorkflowError("start_chapter must be positive.")

    now = utc_now()
    state = {
        "schema_version": 1,
        "status": "planned",
        "mode": "agent_skill_scheduler",
        "target_words": planned_target_words,
        "target_chapters": planned_target_chapters,
        "start_chapter": planned_start,
        "current_chapter": planned_start,
        "last_finalized_chapter": last_finalized,
        "chapters_attempted": 0,
        "failure_count": 0,
        "pause_reason": "",
        "next_command": "longform-engine auto-write run project.yaml",
        "created_at": now,
        "updated_at": now,
    }
    state["agent_task_status"] = auto_write_agent_task_status(root, planned_start)
    write_json(state_path, state)
    report_path = root / "70_runtime" / "run_reports" / "auto_write_plan.json"
    write_json(report_path, {"command": "auto-write plan", **state})
    state["agent_task_status"] = auto_write_agent_task_status(root, int(state.get("current_chapter") or 0))
    state["last_report"] = relative_path(root, report_path)
    write_json(state_path, state)
    return auto_write_result(root, state, action="plan", report_file=str(report_path), summary="Auto-write plan ready.")


def auto_write_run(config: ConfigDocument, *, chapters: int | None = None) -> AutoWriteResult:
    """Run the auto-write scheduler until it reaches the next safe pause."""

    if chapters is not None and chapters <= 0:
        raise WorkflowError("chapters must be positive.")
    root = resolve_project_root(config)
    state_path = auto_write_state_path(root)
    if not state_path.exists():
        auto_write_plan(config)
    state = load_auto_write_state(root)
    state = reconcile_auto_write_state(config, root, state)
    max_steps = chapters or 1
    report_path = root / "70_runtime" / "run_reports" / f"auto_write_run_{safe_timestamp(utc_now())}.json"
    run_events: list[dict[str, Any]] = []

    for _ in range(max_steps):
        state = reconcile_auto_write_state(config, root, state)
        if auto_write_completed(config, root, state):
            state.update(
                {
                    "status": "completed",
                    "pause_reason": "",
                    "next_command": "",
                    "updated_at": utc_now(),
                }
            )
            run_events.append({"status": "completed", "chapter": state.get("current_chapter")})
            break

        manual_pause = root / "70_runtime" / "auto_write.pause"
        if manual_pause.exists():
            state.update(
                {
                    "status": "paused",
                    "pause_reason": f"manual pause marker exists: {relative_path(root, manual_pause)}",
                    "next_command": "remove 70_runtime/auto_write.pause then run longform-engine auto-write run project.yaml",
                    "updated_at": utc_now(),
                }
            )
            run_events.append({"status": "paused", "reason": state["pause_reason"]})
            break

        current = int(state.get("current_chapter") or 1)
        blocker = auto_write_blocker(root, current)
        if blocker:
            status, reason, next_command, failure = blocker
            state.update(
                {
                    "status": status,
                    "pause_reason": reason,
                    "next_command": next_command,
                    "failure_count": int(state.get("failure_count") or 0) + (1 if failure else 0),
                    "updated_at": utc_now(),
                }
            )
            run_events.append({"status": status, "chapter": current, "reason": reason, "next_command": next_command})
            break

        try:
            result = continue_write(config, chapter_number=current)
        except WorkflowError as exc:
            reason = str(exc)
            state.update(
                {
                    "status": "blocked",
                    "pause_reason": reason,
                    "next_command": auto_write_next_command_from_error(current, reason),
                    "failure_count": int(state.get("failure_count") or 0) + 1,
                    "updated_at": utc_now(),
                }
            )
            run_events.append({"status": "blocked", "chapter": current, "reason": reason})
            break

        state["chapters_attempted"] = int(state.get("chapters_attempted") or 0) + 1
        if result.status == "task_ready":
            state.update(
                {
                    "status": "awaiting_agent_draft",
                    "current_chapter": current,
                    "pause_reason": "continue-write generated an Agent writing task and waits for draft submit.",
                    "next_command": result.next_command,
                    "last_writing_task": relative_path(root, Path(result.writing_task_markdown)),
                    "last_recommended_agent_draft": relative_path(root, Path(result.recommended_agent_draft)),
                    "updated_at": utc_now(),
                }
            )
            run_events.append({"status": "awaiting_agent_draft", "chapter": current, "next_command": result.next_command})
            break
        if result.status.endswith("gate_failed"):
            state.update(
                {
                    "status": "paused_gate_failed",
                    "current_chapter": current,
                    "pause_reason": f"gate failed for ch{current:03d}.",
                    "next_command": f"longform-engine repair-chapter project.yaml --chapter {current} --plan-only",
                    "failure_count": int(state.get("failure_count") or 0) + 1,
                    "updated_at": utc_now(),
                }
            )
            run_events.append({"status": "paused_gate_failed", "chapter": current})
            break

        state.update(
            {
                "status": "awaiting_finalize",
                "current_chapter": current,
                "pause_reason": "draft is gate-approved but must be finalized by chapter finalize.",
                "next_command": f"longform-engine chapter finalize project.yaml --chapter {current} --approved-by human",
                "updated_at": utc_now(),
            }
        )
        run_events.append({"status": "awaiting_finalize", "chapter": current})
        break

    state["last_report"] = relative_path(root, report_path)
    write_json(state_path, state)
    write_json(
        report_path,
        {
            "command": "auto-write run",
            "status": state.get("status"),
            "events": run_events,
            "state": state,
            "runtime": {"ended_at": utc_now()},
        },
    )
    return auto_write_result(root, state, action="run", report_file=str(report_path), summary=auto_write_summary(config, root, state))


def auto_write_progress(config: ConfigDocument) -> AutoWriteResult:
    """Read the current auto-write scheduler progress without mutating project state."""

    root = resolve_project_root(config)
    state = reconcile_auto_write_state(config, root, load_auto_write_state(root))
    return auto_write_result(root, state, action="progress", report_file="", summary=auto_write_summary(config, root, state))


def auto_write_report(config: ConfigDocument) -> AutoWriteResult:
    """Write a readable Markdown report for the current auto-write state."""

    root = resolve_project_root(config)
    state = reconcile_auto_write_state(config, root, load_auto_write_state(root))
    report_path = root / "70_runtime" / "run_reports" / "auto_write_report.md"
    atomic_write_text(report_path, render_auto_write_report(config, root, state))
    state["last_report"] = relative_path(root, report_path)
    state["updated_at"] = utc_now()
    write_json(auto_write_state_path(root), state)
    return auto_write_result(root, state, action="report", report_file=str(report_path), summary=auto_write_summary(config, root, state))


def auto_write_state_path(root: Path) -> Path:
    return root / "70_runtime" / "auto_write_state.json"


def load_auto_write_state(root: Path) -> dict[str, Any]:
    state = load_json(auto_write_state_path(root), default={})
    return state if isinstance(state, dict) else {}


def reconcile_auto_write_state(config: ConfigDocument, root: Path, state: dict[str, Any]) -> dict[str, Any]:
    novel_state = load_json(root / "30_state" / "novel_state.json", default={})
    last_finalized = highest_finalized_chapter(root, novel_state)
    length = config.data.get("length", {})
    if not state:
        state = {
            "schema_version": 1,
            "status": "unplanned",
            "mode": "agent_skill_scheduler",
            "target_words": int(length.get("target_total_words") or 0),
            "target_chapters": int(length.get("total_chapters") or max(1, last_finalized + 1)),
            "start_chapter": last_finalized + 1,
            "current_chapter": last_finalized + 1,
            "chapters_attempted": 0,
            "failure_count": 0,
            "pause_reason": "auto-write plan has not been created yet.",
            "next_command": "longform-engine auto-write plan project.yaml",
        }
    current = int(state.get("current_chapter") or last_finalized + 1)
    state["last_finalized_chapter"] = last_finalized
    state["current_chapter"] = max(current, last_finalized + 1)
    state.setdefault("target_words", int(length.get("target_total_words") or 0))
    state.setdefault("target_chapters", int(length.get("total_chapters") or state["current_chapter"]))
    state.setdefault("chapters_attempted", 0)
    state.setdefault("failure_count", 0)
    state.setdefault("pause_reason", "")
    state.setdefault("next_command", "longform-engine auto-write run project.yaml")
    state["agent_task_status"] = auto_write_agent_task_status(root, int(state.get("current_chapter") or 0))
    return state


def auto_write_completed(config: ConfigDocument, root: Path, state: dict[str, Any]) -> bool:
    target_chapters = int(state.get("target_chapters") or 0)
    target_words = int(state.get("target_words") or 0)
    last_finalized = int(state.get("last_finalized_chapter") or 0)
    if target_chapters and last_finalized >= target_chapters:
        return True
    if target_words and total_final_words(root) >= target_words:
        return True
    return False


def auto_write_blocker(root: Path, chapter_number: int) -> tuple[str, str, str, bool] | None:
    if final_chapter_exists(root, chapter_number):
        return None
    agent_blocker = auto_write_agent_task_blocker(root, chapter_number)
    if agent_blocker is not None:
        return agent_blocker
    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    gate = load_json(gate_path, default={}) if gate_path.exists() else {}
    if isinstance(gate, dict) and gate.get("passed") is False and not gate_has_waiver(gate):
        return (
            "paused_gate_failed",
            f"ch{chapter_number:03d} failed gate; repair before auto-write can continue.",
            f"longform-engine repair-chapter project.yaml --chapter {chapter_number} --plan-only",
            True,
        )
    if isinstance(gate, dict) and (gate.get("passed") is True or gate_has_waiver(gate)):
        return (
            "awaiting_finalize",
            f"ch{chapter_number:03d} is gate-approved but not finalized.",
            f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
            False,
        )
    if draft_chapter_exists(root, chapter_number):
        return (
            "awaiting_gate",
            f"ch{chapter_number:03d} draft exists but has no finalizable gate result.",
            f"longform-engine gate-check project.yaml --chapter {chapter_number}",
            False,
        )
    task_path = root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json"
    if task_path.exists():
        task = load_json(task_path, default={})
        next_command = (
            str(task.get("next_command"))
            if isinstance(task, dict) and task.get("next_command")
            else f"longform-engine draft submit project.yaml --chapter {chapter_number} --file 50_workbench/agent_drafts/ch{chapter_number:03d}.codex.md --agent codex"
        )
        return (
            "awaiting_agent_draft",
            f"ch{chapter_number:03d} writing task exists and waits for Agent draft submission.",
            next_command,
            False,
        )
    return None


AUTO_WRITE_TASK_WAIT_STATUS = {
    "chapter_write": "awaiting_agent_draft",
    "repair": "awaiting_repair_candidate",
    "humanize": "awaiting_repair_candidate",
    "content_expand": "awaiting_repair_candidate",
    "graph_extract": "awaiting_semantic_output",
    "memory_extract": "awaiting_semantic_output",
    "character_memory": "awaiting_semantic_output",
    "pacing_review": "awaiting_semantic_output",
    "editorial_review": "awaiting_editorial_result",
}

AUTO_WRITE_TASK_WAIT_PRIORITY = {
    "chapter_write": 10,
    "repair": 20,
    "humanize": 21,
    "content_expand": 22,
    "pacing_review": 30,
    "graph_extract": 31,
    "memory_extract": 32,
    "character_memory": 33,
    "editorial_review": 40,
}


def auto_write_agent_task_blocker(root: Path, chapter_number: int) -> tuple[str, str, str, bool] | None:
    waiting = auto_write_waiting_agent_tasks(root, chapter_number)
    if not waiting:
        return None
    task = waiting[0]
    task_type = str(task.get("task_type") or "agent_task")
    status = AUTO_WRITE_TASK_WAIT_STATUS.get(task_type, "awaiting_agent_output")
    next_command = str(task.get("validate_command") or task.get("apply_command") or task.get("failure_next_command") or "")
    reason = (
        f"ch{chapter_number:03d} has awaiting Agent task {task.get('task_id')} "
        f"({task_type}); scheduler paused until the declared output is written and validated."
    )
    return status, reason, next_command, False


def auto_write_waiting_agent_tasks(root: Path, chapter_number: int) -> list[dict[str, Any]]:
    tasks = [
        task
        for task in list_manifests(root, chapter_number=chapter_number)
        if str(task.get("status") or "") == "awaiting_agent"
        and str(task.get("task_type") or "") in AUTO_WRITE_TASK_WAIT_STATUS
    ]
    return sorted(
        tasks,
        key=lambda task: (
            AUTO_WRITE_TASK_WAIT_PRIORITY.get(str(task.get("task_type") or ""), 999),
            str(task.get("updated_at") or task.get("created_at") or ""),
            str(task.get("task_id") or ""),
        ),
    )


def auto_write_agent_task_status(root: Path, chapter_number: int) -> dict[str, Any]:
    current = status_summary(root, chapter_number=chapter_number)
    project = status_summary(root)
    current_items = list_manifests(root, chapter_number=chapter_number)
    project_items = list_manifests(root)
    latest = latest_agent_task(project_items)
    waiting = auto_write_waiting_agent_tasks(root, chapter_number)
    return {
        "schema_version": 1,
        "current_chapter": chapter_number,
        "current": {
            "tasks": current.get("tasks", 0),
            "by_status": current.get("by_status", {}),
            "by_type": current.get("by_type", {}),
        },
        "project": {
            "tasks": project.get("tasks", 0),
            "by_status": project.get("by_status", {}),
            "by_type": project.get("by_type", {}),
        },
        "latest": compact_agent_task(latest) if latest else {},
        "waiting": [compact_agent_task(task) for task in waiting],
        "waiting_kinds": sorted({AUTO_WRITE_TASK_WAIT_STATUS.get(str(task.get("task_type") or ""), "awaiting_agent_output") for task in waiting}),
        "current_task_ids": [str(task.get("task_id") or "") for task in current_items if task.get("task_id")],
    }


def latest_agent_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not tasks:
        return None
    return sorted(
        tasks,
        key=lambda task: (
            str(task.get("updated_at") or task.get("created_at") or ""),
            str(task.get("task_id") or ""),
        ),
    )[-1]


def compact_agent_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": str(task.get("task_id") or ""),
        "task_type": str(task.get("task_type") or ""),
        "status": str(task.get("status") or ""),
        "chapter_number": int(task.get("chapter_number") or 0),
        "manifest_file": str(task.get("manifest_file") or ""),
        "validate_command": str(task.get("validate_command") or ""),
        "apply_command": str(task.get("apply_command") or ""),
        "failure_next_command": str(task.get("failure_next_command") or ""),
        "updated_at": str(task.get("updated_at") or ""),
    }


def auto_write_next_command_from_error(chapter_number: int, reason: str) -> str:
    reason_lower = reason.lower()
    if "failed gate" in reason_lower:
        return f"longform-engine repair-chapter project.yaml --chapter {chapter_number - 1 if chapter_number > 1 else chapter_number} --plan-only"
    if "not finalized" in reason_lower or "gate-approved" in reason_lower:
        return f"longform-engine chapter finalize project.yaml --chapter {chapter_number - 1 if chapter_number > 1 else chapter_number} --approved-by human"
    if "stale" in reason_lower:
        return "longform-engine db rebuild project.yaml"
    return f"longform-engine continue-write project.yaml --chapter {chapter_number}"


def auto_write_result(root: Path, state: dict[str, Any], *, action: str, report_file: str, summary: str) -> AutoWriteResult:
    return AutoWriteResult(
        action=action,
        status=str(state.get("status") or "unknown"),
        state_file=str(auto_write_state_path(root)),
        report_file=report_file,
        target_chapters=int(state.get("target_chapters") or 0),
        target_words=int(state.get("target_words") or 0),
        current_chapter=int(state.get("current_chapter") or 0),
        last_finalized_chapter=int(state.get("last_finalized_chapter") or 0),
        chapters_attempted=int(state.get("chapters_attempted") or 0),
        failure_count=int(state.get("failure_count") or 0),
        pause_reason=str(state.get("pause_reason") or ""),
        next_command=str(state.get("next_command") or ""),
        summary=summary,
    )


def auto_write_summary(config: ConfigDocument, root: Path, state: dict[str, Any]) -> str:
    target_chapters = int(state.get("target_chapters") or 0)
    target_words = int(state.get("target_words") or 0)
    last_finalized = int(state.get("last_finalized_chapter") or 0)
    total_words = total_final_words(root)
    return (
        f"Auto-write {state.get('status', 'unknown')}: "
        f"finalized {last_finalized}/{target_chapters or '?'} chapters, "
        f"{total_words}/{target_words or '?'} words, "
        f"current ch{int(state.get('current_chapter') or 0):03d}."
    )


def render_auto_write_report(config: ConfigDocument, root: Path, state: dict[str, Any]) -> str:
    summary = auto_write_summary(config, root, state)
    agent_tasks = state.get("agent_task_status") if isinstance(state.get("agent_task_status"), dict) else {}
    current_agent = agent_tasks.get("current") if isinstance(agent_tasks.get("current"), dict) else {}
    latest_agent = agent_tasks.get("latest") if isinstance(agent_tasks.get("latest"), dict) else {}
    waiting_agent = agent_tasks.get("waiting") if isinstance(agent_tasks.get("waiting"), list) else []
    lines = [
        "# Auto-Write Progress Report",
        "",
        summary,
        "",
        "## State",
        "",
        f"- Status: {state.get('status', 'unknown')}",
        f"- Target chapters: {state.get('target_chapters', 0)}",
        f"- Target words: {state.get('target_words', 0)}",
        f"- Current chapter: {state.get('current_chapter', 0)}",
        f"- Last finalized chapter: {state.get('last_finalized_chapter', 0)}",
        f"- Chapters attempted: {state.get('chapters_attempted', 0)}",
        f"- Failure count: {state.get('failure_count', 0)}",
        f"- Pause reason: {state.get('pause_reason') or 'none'}",
        f"- Next command: {state.get('next_command') or 'none'}",
        "",
        "## Agent Tasks",
        "",
        f"- Current chapter task count: {current_agent.get('tasks', 0)}",
        f"- Current chapter by status: {json.dumps(current_agent.get('by_status', {}), ensure_ascii=False)}",
        f"- Current chapter by type: {json.dumps(current_agent.get('by_type', {}), ensure_ascii=False)}",
        f"- Waiting kinds: {', '.join(agent_tasks.get('waiting_kinds') or []) or 'none'}",
        f"- Latest task: {latest_agent.get('task_id') or 'none'} ({latest_agent.get('task_type') or 'n/a'} / {latest_agent.get('status') or 'n/a'})",
        "",
        "## Waiting Agent Outputs",
        "",
    ]
    if waiting_agent:
        for task in waiting_agent:
            if isinstance(task, dict):
                lines.append(f"- `{task.get('task_id')}` -> `{task.get('validate_command') or task.get('apply_command') or 'no command'}`")
    else:
        lines.append("- None")
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Auto-write only schedules `continue-write` and Agent draft tasks.",
            "- It does not write final manuscripts, RAG chunks, story graph updates, memory, or SQLite final chapter rows.",
            "- Candidate prose must still pass `draft submit -> gate-check -> chapter finalize`.",
            "",
        ]
    )
    return "\n".join(lines)


def highest_finalized_chapter(root: Path, novel_state: Any) -> int:
    state_last = int(novel_state.get("last_finalized_chapter") or 0) if isinstance(novel_state, dict) else 0
    final_dir = root / "40_manuscript" / "final"
    found = state_last
    for path in final_dir.glob("ch*.md"):
        match = re.match(r"ch(\d+)\.md$", path.name)
        if match:
            found = max(found, int(match.group(1)))
    return found


def total_final_words(root: Path) -> int:
    final_dir = root / "40_manuscript" / "final"
    total = 0
    for path in sorted(final_dir.glob("ch*.md")):
        total += estimate_words(safe_read_text(path))
    return total


def normalize_agent(agent: str) -> str:
    value = str(agent or "").strip().lower().replace("_", "-")
    aliases = {
        "claudecode": "claude",
        "claude-code": "claude",
        "claude_code": "claude",
    }
    value = aliases.get(value, value)
    if not value:
        raise WorkflowError("agent is required.")
    return value


def resolve_agent_draft_source(root: Path, config: ConfigDocument, file_path: str | Path) -> Path:
    raw = Path(file_path).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    cwd_candidate = (Path.cwd() / raw).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    root_candidate = (root / raw).resolve()
    if root_candidate.exists():
        return root_candidate
    draft_dir = agent_draft_dir(root, config)
    draft_candidate = (draft_dir / raw).resolve()
    if draft_candidate.exists():
        return draft_candidate
    return root_candidate


def ensure_agent_draft_source(config: ConfigDocument, root: Path, source_path: Path) -> None:
    allowed_dirs = [path.resolve() for path in agent_submission_dirs(root, config)]
    for directory in allowed_dirs:
        try:
            source_path.resolve().relative_to(directory)
            return
        except ValueError:
            continue
    allowed = ", ".join(relative_path(root, directory) for directory in allowed_dirs)
    raise WorkflowError(
        "Agent drafts/candidates must be submitted from the configured draft_dir or controlled candidate lanes: "
        f"{allowed}."
    )


def agent_draft_dir(root: Path, config: ConfigDocument) -> Path:
    writing = config.data.get("writing", {})
    agent_config = writing.get("agent", {}) if isinstance(writing.get("agent"), dict) else {}
    configured = Path(str(agent_config.get("draft_dir") or "50_workbench/agent_drafts"))
    if configured.is_absolute():
        return configured
    return root / configured


def agent_submission_dirs(root: Path, config: ConfigDocument) -> tuple[Path, ...]:
    return (
        agent_draft_dir(root, config),
        root / "50_workbench" / "repair_candidates",
    )


def normalize_agent_gate_result(gate_path: Path, passed: bool, next_command: str) -> None:
    payload = load_json(gate_path, default={})
    if not isinstance(payload, dict):
        return
    actions = list(payload.get("allowed_actions") or [])
    if passed and "chapter_finalize" not in actions:
        actions.append("chapter_finalize")
    payload["allowed_actions"] = actions
    payload["next_command"] = next_command
    payload["updated_at"] = utc_now()
    write_json(gate_path, payload)


def upsert_chapter_meta(root: Path, record: dict[str, Any]) -> None:
    path = root / "40_manuscript" / "chapter_meta.jsonl"
    records = []
    target = int(record.get("chapter_number") or 0)
    for item in read_jsonl(path):
        number = int(item.get("chapter_number") or item.get("chapter") or item.get("number") or 0)
        if number != target:
            records.append(item)
    records.append({key: value for key, value in record.items() if value is not None})
    atomic_write_text(path, "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def extract_title(text: str, chapter_number: int) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or f"ch{chapter_number:03d}"
        if stripped:
            return stripped[:40]
    return f"ch{chapter_number:03d}"


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def summarize_story_graph(graph: Any) -> dict[str, int]:
    if not isinstance(graph, dict):
        return {"entities": 0, "relationships": 0, "events": 0}
    return {
        "entities": len(graph.get("entities", [])) if isinstance(graph.get("entities"), list) else 0,
        "relationships": len(graph.get("relationships", [])) if isinstance(graph.get("relationships"), list) else 0,
        "events": len(graph.get("events", [])) if isinstance(graph.get("events"), list) else 0,
    }


def load_recent_research_canon(root: Path, *, limit: int = 5) -> list[dict[str, str]]:
    path = root / "10_bible" / "research_canon.jsonl"
    records = []
    for item in read_jsonl(path)[-limit:]:
        records.append(
            {
                "id": str(item.get("id") or ""),
                "title": str(item.get("title") or ""),
                "summary": str(item.get("summary") or ""),
                "source_url": str(item.get("source_url") or ""),
                "canon_file": relative_path(root, path),
            }
        )
    return records


def build_reverse_brake_contract(
    config: ConfigDocument,
    chapter_number: int,
    outline_anchor: dict[str, Any],
    *,
    card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    card = card or {}
    anchor = outline_anchor if isinstance(outline_anchor, dict) else {}
    status = str(anchor.get("status") or card.get("status") or "planned").lower()
    closure_allowed = bool(anchor.get("closure_allowed") or status in {"closure", "closing", "finale", "final", "resolved"})
    allowed_reveal_level = str(anchor.get("allowed_reveal_level") or card.get("allowed_reveal_level") or ("full" if closure_allowed else "hint")).lower()
    if allowed_reveal_level not in {"none", "hint", "partial", "full"}:
        allowed_reveal_level = "hint"
    forbidden_reveals = dedupe_strings(
        as_list(anchor.get("forbidden_reveals"))
        + as_list(card.get("forbidden_reveals"))
        + as_list(config.data.get("gates", {}).get("forbidden_reveals"))
    )
    do_not_resolve = dedupe_strings(
        as_list(anchor.get("resolution_markers"))
        + as_list(card.get("resolution_markers"))
    ) or ["core longform mystery", "main volume conflict"]
    hook = str(card.get("hook") or anchor.get("hook") or "preserve one concrete unresolved pressure")
    must_preserve = dedupe_strings(as_list(anchor.get("must_preserve_suspense")) + as_list(card.get("must_preserve_suspense")) + [hook])
    quota_limit = int(config.data.get("pacing", {}).get("max_major_quota_triggers_per_chapter") or 1)
    return {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "source": "20_outline/outline_anchors.json",
        "closure_allowed": closure_allowed,
        "allowed_reveal_level": allowed_reveal_level,
        "requires_tail_suspense": bool(anchor.get("requires_tail_suspense") or card.get("requires_tail_suspense")),
        "forbidden_reveals": forbidden_reveals,
        "do_not_resolve": do_not_resolve,
        "must_preserve_suspense": must_preserve,
        "this_chapter_must_not_solve": do_not_resolve,
        "must_keep_suspense": must_preserve,
        "abc_quota_limit": quota_limit,
        "mainline_information_release": {
            "allowed_level": allowed_reveal_level,
            "instruction": "hint or partial evidence only unless closure_allowed is true",
        },
        "instruction": "Do not close core conflicts, reveal forbidden secrets, or spend multiple A/B/C acceleration lanes before gate approval.",
    }


def build_writable_brief(
    config: ConfigDocument,
    root: Path,
    *,
    chapter_number: int,
    card: dict[str, Any],
    beat: dict[str, Any],
    tcs: dict[str, Any],
    outline_anchor: dict[str, Any],
    event_recommendation: dict[str, Any],
    style_context: dict[str, Any],
    craft_brief: dict[str, Any],
) -> dict[str, Any]:
    stage = chapter_stage(config, chapter_number)
    beats = beat.get("beats") if isinstance(beat.get("beats"), list) else []
    first_beat = next((item for item in beats if isinstance(item, dict)), {})
    reverse_brake = build_reverse_brake_contract(config, chapter_number, outline_anchor, card=card)
    forbidden_reveals = dedupe_strings(as_list(card.get("forbidden_reveals")) + as_list(outline_anchor.get("forbidden_reveals")) + as_list(reverse_brake.get("forbidden_reveals")))
    resolution_markers = dedupe_strings(as_list(reverse_brake.get("do_not_resolve")) + as_list(outline_anchor.get("resolution_markers")) + as_list(card.get("resolution_markers")))
    hook = str(card.get("hook") or craft_brief.get("ending_hook") or "preserve one concrete unresolved pressure")
    scene_entry = {
        "mode": "in_scene",
        "entry_point": first_beat.get("scene_goal") or first_beat.get("scene_purpose") or "open on concrete pressure, not recap",
        "location_hint": ", ".join(str(item) for item in as_list(tcs.get("locations"))) or "use the latest TCS location if known",
        "character_hint": ", ".join(str(item) for item in as_list(tcs.get("current_characters"))) or "use the current POV cast from TCS/graph",
        "sensory_anchor": first_beat.get("sensory_anchor") or "one concrete sensory or body-cost detail",
    }
    return {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "stage": stage,
        "chapter_duty": outline_anchor.get("duty") or card.get("duty") or "advance one clear longform promise",
        "pacing_tier": infer_task_pacing_tier(config, event_recommendation),
        "scene_entry": scene_entry,
        "chapter_hook": hook,
        "forbidden_reveals": forbidden_reveals,
        "do_not_resolve": resolution_markers or forbidden_reveals or ["core longform mystery", "main volume conflict"],
        "must_preserve_suspense": dedupe_strings(as_list(reverse_brake.get("must_preserve_suspense")) + as_list(outline_anchor.get("must_preserve_suspense")) + [hook]),
        "this_chapter_must_not_solve": as_list(reverse_brake.get("this_chapter_must_not_solve")),
        "must_keep_suspense": as_list(reverse_brake.get("must_keep_suspense")),
        "reverse_brake": reverse_brake,
        "beat_expansion_policy": {
            "expand_by_scene_material": True,
            "minimum_function_per_beat": "each beat must change pressure, knowledge, relationship, or risk",
            "no_padding": "do not add static exposition only to reach word count",
            "style_source": style_context.get("source", ""),
        },
        "next_safe_action": "write only the Agent draft, then run draft submit",
    }


def build_beat_expansion_requirements(
    beat: dict[str, Any],
    *,
    card: dict[str, Any],
    writing_brief: dict[str, Any],
) -> list[dict[str, Any]]:
    beats = beat.get("beats") if isinstance(beat.get("beats"), list) else []
    forbidden_reveals = as_list(writing_brief.get("forbidden_reveals"))
    preserve_suspense = as_list(writing_brief.get("must_preserve_suspense"))
    requirements: list[dict[str, Any]] = []
    for raw in beats:
        if not isinstance(raw, dict):
            continue
        requirements.append(
            {
                "order": raw.get("order"),
                "name": raw.get("name"),
                "scene_goal": raw.get("scene_goal") or raw.get("scene_purpose") or raw.get("purpose"),
                "conflict_point": raw.get("conflict_point") or raw.get("conflict") or card.get("conflict"),
                "information_release": raw.get("information_release") or raw.get("turn") or card.get("information"),
                "expansion_requirements": raw.get("expansion_requirements")
                or {
                    "scene": "write concrete scene action",
                    "dialogue": raw.get("dialogue_intent"),
                    "psychology": "carry emotion through behavior before explanation",
                    "action": "include a visible decision or consequence",
                    "transition": "leave a changed problem for the next beat",
                },
                "avoid_repetition": as_list(raw.get("avoid_repetition"))
                or [
                    "same scene rhythm as previous beat",
                    "summary-only exposition",
                    "repeating the same pressure without escalation",
                ],
                "forbidden_reveals": forbidden_reveals,
                "must_preserve_suspense": preserve_suspense,
            }
        )
    return requirements


def build_constraint_packet(
    root: Path,
    *,
    context_file: Path,
    context_text: str,
    story_graph_path: Path,
    story_graph: Any,
    graph_summary: dict[str, int],
    graph_constraints: dict[str, Any],
    tcs_path: Path,
    tcs_payload: dict[str, Any],
    outline_anchor: dict[str, Any],
    event_recommendation: dict[str, Any],
    style_context: dict[str, Any],
    card: dict[str, Any],
    canon_research: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "rag": {
            "source": relative_path(root, context_file),
            "required_use": "use as the only formal plot context packet",
            "summary": trim_text(context_text, 700),
        },
        "story_graph": {
            "source": relative_path(root, story_graph_path),
            "summary": graph_summary,
            "facts": summarize_story_graph_facts(story_graph),
            "constraints": graph_constraints,
        },
        "tcs": {
            "source": relative_path(root, tcs_path) if tcs_path.exists() else "",
            "current_characters": as_list(tcs_payload.get("current_characters")),
            "locations": as_list(tcs_payload.get("locations")),
            "recent_events": as_list(tcs_payload.get("recent_events")),
            "unresolved_conflicts": as_list(tcs_payload.get("unresolved_conflicts")),
            "open_foreshadows": as_list(tcs_payload.get("open_foreshadows")),
            "active_constraints": as_list(tcs_payload.get("active_constraints")),
        },
        "character_memory": load_character_memory_context(root, tcs_payload),
        "outline_anchor": outline_anchor,
        "reverse_brake": card.get("reverse_brake") if isinstance(card.get("reverse_brake"), dict) else {
            "forbidden_reveals": as_list(card.get("forbidden_reveals")) + as_list(outline_anchor.get("forbidden_reveals")),
            "do_not_resolve": as_list(card.get("resolution_markers")) + as_list(outline_anchor.get("resolution_markers")),
            "must_preserve_suspense": as_list(card.get("must_preserve_suspense")) + as_list(outline_anchor.get("must_preserve_suspense")),
            "allowed_reveal_level": outline_anchor.get("allowed_reveal_level") or card.get("allowed_reveal_level") or "hint",
            "requires_tail_suspense": bool(outline_anchor.get("requires_tail_suspense") or card.get("requires_tail_suspense")),
            "instruction": "do not resolve core conflicts or reveal forbidden secrets before closure is explicitly allowed",
        },
        "event_matrix": {
            "source": event_recommendation.get("source_file", "30_state/event_matrix.json"),
            "recommended": as_list(event_recommendation.get("recommended")),
            "blocked": as_list(event_recommendation.get("blocked")),
            "constraints": as_list(event_recommendation.get("constraints")),
            "soft_event_required": bool(event_recommendation.get("soft_event_required")),
            "recent_summary": as_list(event_recommendation.get("recent_summary")),
            "fast_quota": event_recommendation.get("fast_quota") if isinstance(event_recommendation.get("fast_quota"), dict) else {},
            "event_types": as_list(event_recommendation.get("event_types")),
            "instruction": "prefer recommended event types and avoid blocked cooldown types unless the chapter card explicitly overrides them",
        },
        "style_profile": style_context,
        "research_canon": canon_research,
        "forbidden": {
            "general": as_list(card.get("forbidden")),
            "reveals": as_list(card.get("forbidden_reveals")),
        },
    }


def chapter_stage(config: ConfigDocument, chapter_number: int) -> dict[str, Any]:
    length = config.data.get("length", {})
    total = max(1, int(length.get("total_chapters") or chapter_number or 1))
    ratio = min(1.0, max(0.0, chapter_number / total))
    if ratio <= 0.08:
        label = "opening"
        strategy = "establish promise, POV pressure, rules, and first unresolved hook"
    elif ratio <= 0.35:
        label = "early_build"
        strategy = "compound goals, costs, factions, and relationship leverage without core resolution"
    elif ratio <= 0.65:
        label = "midgame"
        strategy = "turn prior promises into consequences and deepen the central contradiction"
    elif ratio <= 0.85:
        label = "late_escalation"
        strategy = "tighten payoffs, expose costs, and preserve final-answer suspense"
    else:
        label = "climax_resolution"
        strategy = "pay off planted promises while keeping only approved residual hooks"
    return {
        "label": label,
        "chapter_number": chapter_number,
        "total_chapters": total,
        "progress_ratio": round(ratio, 4),
        "strategy": strategy,
    }


def infer_task_pacing_tier(config: ConfigDocument, event_recommendation: dict[str, Any]) -> str:
    recommended = {str(item) for item in as_list(event_recommendation.get("recommended"))}
    if {"conflict_thrill", "tension_escalation"} & recommended:
        return "fast"
    if {"bond_deepening", "faction_building", "world_painting"} & recommended:
        return "measured"
    return str(config.data.get("pacing", {}).get("default_mode") or "balanced")


def summarize_story_graph_facts(story_graph: Any, *, limit: int = 8) -> list[str]:
    if not isinstance(story_graph, dict):
        return []
    facts: list[str] = []
    for entity in story_graph.get("entities", []) if isinstance(story_graph.get("entities"), list) else []:
        if not isinstance(entity, dict):
            continue
        label = entity.get("name") or entity.get("id")
        state = entity.get("status") or entity.get("state") or entity.get("type")
        facts.append(f"{label}: {state}")
    for event in story_graph.get("events", []) if isinstance(story_graph.get("events"), list) else []:
        if not isinstance(event, dict):
            continue
        label = event.get("title") or event.get("id")
        chapter = event.get("chapter_number") or event.get("chapter")
        facts.append(f"ch{chapter}: {label}")
    return [item for item in facts if item and item != "None: None"][:limit]


def load_character_memory_context(root: Path, tcs_payload: dict[str, Any], *, limit: int = 6) -> dict[str, Any]:
    current_names = {str(item).strip().lower() for item in as_list(tcs_payload.get("current_characters")) if str(item).strip()}
    records: list[dict[str, Any]] = []
    sources: list[str] = []
    character_dir = root / "60_rag" / "memory" / "characters"
    for path in sorted(character_dir.glob("*.json")):
        payload = load_json(path, default={})
        if not isinstance(payload, dict):
            continue
        label = str(payload.get("name") or payload.get("character_name") or payload.get("character_id") or path.stem)
        identity = {label.lower(), str(payload.get("character_id") or "").lower(), str(payload.get("id") or "").lower()}
        if current_names and not (current_names & identity):
            continue
        records.append(
            {
                "character": label,
                "character_id": payload.get("character_id") or payload.get("id"),
                "relationship_stage": payload.get("relationship_stage") or payload.get("relationship_status"),
                "current_goal": payload.get("current_goal") or payload.get("motivation"),
                "constraints": as_list(payload.get("constraints")) + as_list(payload.get("forbidden_actions")),
                "source": relative_path(root, path),
            }
        )
        sources.append(relative_path(root, path))
        if len(records) >= limit:
            break
    if not records:
        records.extend(character_state_records(root, limit=limit))
        sources.extend(record.get("source", "") for record in records if record.get("source"))
    return {
        "status": "available" if records else "empty",
        "sources": dedupe_strings(sources),
        "characters": records[:limit],
        "instruction": "keep knowledge, speech stage, ability limits, relationship state, and forbidden actions consistent",
    }


def character_state_records(root: Path, *, limit: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in (root / "30_state" / "character_state.json", root / "10_bible" / "characters.json"):
        payload = load_json(path, default=[])
        for item in normalize_records(payload):
            if not isinstance(item, dict):
                continue
            records.append(
                {
                    "character": item.get("name") or item.get("id"),
                    "character_id": item.get("id"),
                    "relationship_stage": item.get("relationship_stage") or item.get("status"),
                    "current_goal": item.get("current_goal") or item.get("motivation"),
                    "constraints": as_list(item.get("constraints")) + as_list(item.get("forbidden_actions")),
                    "source": relative_path(root, path),
                }
            )
            if len(records) >= limit:
                return records
    return records


def dedupe_strings(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def trim_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


def format_writing_task_markdown(root: Path, payload: dict[str, Any]) -> str:
    card = payload.get("chapter_card", {}).get("data", {})
    beat_sheet = payload.get("beat_sheet", {}).get("data", {})
    beats = beat_sheet.get("beats", []) if isinstance(beat_sheet, dict) else []
    rag_context = payload.get("rag_context", {})
    story_graph = payload.get("story_graph", {})
    output_contract = payload.get("output_contract", {})
    must_not_include = output_contract.get("must_not_include", []) if isinstance(output_contract, dict) else []
    must_follow = output_contract.get("must_follow", []) if isinstance(output_contract, dict) else []
    outline_anchor = payload.get("outline_anchor", {}) if isinstance(payload.get("outline_anchor"), dict) else {}
    event_recommendation = payload.get("event_recommendation", {}) if isinstance(payload.get("event_recommendation"), dict) else {}
    style_context = payload.get("style_context", {}) if isinstance(payload.get("style_context"), dict) else {}
    creative_brief = payload.get("creative_brief", {}) if isinstance(payload.get("creative_brief"), dict) else {}
    craft_brief = payload.get("writer_craft_brief", {}) if isinstance(payload.get("writer_craft_brief"), dict) else {}
    humanizer = payload.get("humanizer_rules", {}) if isinstance(payload.get("humanizer_rules"), dict) else {}
    gate_history = payload.get("gate_history", []) if isinstance(payload.get("gate_history"), list) else []
    tcs = payload.get("temporal_context_state", {}) if isinstance(payload.get("temporal_context_state"), dict) else {}
    tcs_data = tcs.get("data", {}) if isinstance(tcs.get("data"), dict) else {}
    writing_brief = payload.get("writing_brief", {}) if isinstance(payload.get("writing_brief"), dict) else {}
    stage = writing_brief.get("stage", {}) if isinstance(writing_brief.get("stage"), dict) else {}
    reverse_brake = writing_brief.get("reverse_brake") if isinstance(writing_brief.get("reverse_brake"), dict) else {}
    beat_requirements = payload.get("beat_expansion_requirements", []) if isinstance(payload.get("beat_expansion_requirements"), list) else []
    constraint_packet = payload.get("constraint_packet", {}) if isinstance(payload.get("constraint_packet"), dict) else {}
    feedback = payload.get("feedback_carryover", {}) if isinstance(payload.get("feedback_carryover"), dict) else {}

    lines = [
        f"# Writing Task ch{payload['chapter_number']:03d}",
        "",
        f"- Status: `{payload['status']}`",
        f"- Writing mode: `{payload['writing_mode']}`",
        f"- Title: {payload['title']}",
        f"- Target word count: {payload['target_word_count']}",
        f"- Recommended draft path: `{payload['draft_submission_path']}`",
        f"- Next command: `{payload['next_command']}`",
        "",
        "## Writable Brief",
        "",
        f"- Stage: {stage.get('label', '')} ({stage.get('progress_ratio', '')})",
        f"- Stage strategy: {stage.get('strategy', '')}",
        f"- Chapter duty: {writing_brief.get('chapter_duty', '')}",
        f"- Pacing tier: {writing_brief.get('pacing_tier', '')}",
        f"- Scene entry: {json.dumps(writing_brief.get('scene_entry', {}), ensure_ascii=False)}",
        f"- Chapter hook: {writing_brief.get('chapter_hook', '')}",
        f"- Forbidden reveals: {', '.join(as_list(writing_brief.get('forbidden_reveals'))) or 'none'}",
        f"- Do not resolve: {', '.join(as_list(writing_brief.get('do_not_resolve'))) or 'none'}",
        f"- Must preserve suspense: {', '.join(as_list(writing_brief.get('must_preserve_suspense'))) or 'none'}",
        "",
        "## Reverse Brake",
        "",
        f"- Allowed reveal level: {reverse_brake.get('allowed_reveal_level', '')}",
        f"- Requires tail suspense: {reverse_brake.get('requires_tail_suspense', False)}",
        f"- This chapter must not solve: {', '.join(as_list(writing_brief.get('this_chapter_must_not_solve'))) or 'none'}",
        f"- Must keep suspense: {', '.join(as_list(writing_brief.get('must_keep_suspense'))) or 'none'}",
        f"- A/B/C quota limit: {reverse_brake.get('abc_quota_limit', '')}",
        f"- Instruction: {reverse_brake.get('instruction', '')}",
        "",
        "## Beat Expansion Requirements",
        "",
    ]
    if beat_requirements:
        for item in beat_requirements:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"### Beat {item.get('order')}: {item.get('name')}",
                    "",
                    f"- Scene goal: {item.get('scene_goal', '')}",
                    f"- Conflict point: {item.get('conflict_point', '')}",
                    f"- Information release: {item.get('information_release', '')}",
                    f"- Expansion requirements: {json.dumps(item.get('expansion_requirements', {}), ensure_ascii=False)}",
                    f"- Avoid repetition: {', '.join(as_list(item.get('avoid_repetition')))}",
                    f"- Forbidden reveals: {', '.join(as_list(item.get('forbidden_reveals'))) or 'none'}",
                    f"- Preserve suspense: {', '.join(as_list(item.get('must_preserve_suspense'))) or 'none'}",
                    "",
                ]
            )
    else:
        lines.extend(["- No beat expansion requirements available.", ""])
    lines.extend(
        [
            "## Constraint Packet",
            "",
            f"- RAG: `{constraint_packet.get('rag', {}).get('source', '') if isinstance(constraint_packet.get('rag'), dict) else ''}`",
            f"- Story graph facts: {json.dumps(constraint_packet.get('story_graph', {}).get('facts', []) if isinstance(constraint_packet.get('story_graph'), dict) else [], ensure_ascii=False)}",
            f"- TCS constraints: {json.dumps(constraint_packet.get('tcs', {}), ensure_ascii=False)}",
            f"- Character memory: {json.dumps(constraint_packet.get('character_memory', {}), ensure_ascii=False)}",
            f"- Reverse brake: {json.dumps(constraint_packet.get('reverse_brake', {}), ensure_ascii=False)}",
            f"- Event matrix: {json.dumps(constraint_packet.get('event_matrix', {}), ensure_ascii=False)}",
            f"- Style profile: {json.dumps(constraint_packet.get('style_profile', {}), ensure_ascii=False)}",
            "",
        ]
    )
    lines.extend(
        [
        "## Chapter Card",
        "",
        f"- Source: `{payload['chapter_card']['path']}`",
        f"- Duty: {card.get('duty', '')}",
        f"- Conflict: {card.get('conflict', '')}",
        f"- Information: {card.get('information', '')}",
        f"- Hook: {card.get('hook', '')}",
        "",
        ]
    )
    lines.extend(
        [
        "## Outline Anchor",
        "",
        f"- Chapter: {outline_anchor.get('chapter_number', 'unknown')}",
        f"- Duty: {outline_anchor.get('duty', '')}",
        f"- Status: {outline_anchor.get('status', '')}",
        "",
        "## Event Recommendation",
        "",
        f"- Recommended: {', '.join(event_recommendation.get('recommended', [])) if event_recommendation else 'none'}",
        f"- Blocked by cooldown: {', '.join(event_recommendation.get('blocked', [])) if event_recommendation else 'none'}",
        f"- Constraints: {', '.join(event_recommendation.get('constraints', [])) if event_recommendation else 'none'}",
        f"- Soft event required: {event_recommendation.get('soft_event_required', False) if event_recommendation else False}",
        f"- Fast quota: {json.dumps(event_recommendation.get('fast_quota', {}) if event_recommendation else {}, ensure_ascii=False)}",
        "",
        "## Beat Sheet",
        "",
        f"- Source: `{payload['beat_sheet']['path']}`",
        ]
    )
    if beats:
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            lines.append(f"- Beat {beat.get('order')}: {beat.get('name')} — {beat.get('purpose')}")
    else:
        lines.append("- No beats available.")

    lines.extend(
        [
            "",
            "## RAG Context",
            "",
            f"- Source: `{rag_context.get('path', '')}`",
            "",
            str(rag_context.get("text", "")).strip() or "No context available.",
            "",
            "## Story Graph Summary",
            "",
            f"- Source: `{story_graph.get('path', '')}`",
        ]
    )
    summary = story_graph.get("summary", {}) if isinstance(story_graph, dict) else {}
    lines.extend(
        [
            f"- Entities: {summary.get('entities', 0)}",
            f"- Relationships: {summary.get('relationships', 0)}",
            f"- Events: {summary.get('events', 0)}",
            f"- Constraints: {json.dumps(story_graph.get('constraints', {}), ensure_ascii=False)}",
            "",
            "## Temporal Context State",
            "",
            f"- Source: `{tcs.get('path', '')}`",
            f"- Current characters: {', '.join(as_list(tcs_data.get('current_characters')))}",
            f"- Locations: {', '.join(as_list(tcs_data.get('locations')))}",
            f"- Emotion state: {tcs_data.get('emotion_state', 'unknown')}",
            f"- Recent events: {', '.join(as_list(tcs_data.get('recent_events')))}",
            f"- Unresolved conflicts: {', '.join(as_list(tcs_data.get('unresolved_conflicts')))}",
            f"- Open foreshadows: {', '.join(as_list(tcs_data.get('open_foreshadows')))}",
            f"- Active constraints: {', '.join(as_list(tcs_data.get('active_constraints')))}",
            "",
            "## Creative Brief",
            "",
            f"- Status: {creative_brief.get('status', 'missing')}",
            f"- Target audience: {creative_brief.get('target_audience', '')}",
            f"- Writing style: {creative_brief.get('writing_style', '')}",
            f"- Target scale: {creative_brief.get('target_scale', '')}",
            f"- Core taboo: {', '.join(str(item) for item in as_list(creative_brief.get('core_taboo')))}",
            f"- Reader contract: {json.dumps(creative_brief.get('reader_contract', {}), ensure_ascii=False)}",
            "",
            "## Writer Craft Brief",
            "",
            f"- Reader payoff: {craft_brief.get('reader_payoff', '')}",
            f"- Ending hook: {craft_brief.get('ending_hook', '')}",
            f"- Emotion progression: {json.dumps(craft_brief.get('emotion_progression', {}), ensure_ascii=False)}",
            "- Dialogue strategy:",
            *[f"  - {item}" for item in as_list(craft_brief.get("dialogue_strategy"))],
            "- Scene texture:",
            *[f"  - {item}" for item in as_list(craft_brief.get("scene_texture"))],
            "- AI voice forbidden zone:",
            *[f"  - {item}" for item in as_list(craft_brief.get("ai_voice_forbidden_zone"))],
            "",
            "## Style Context",
            "",
            f"- Source: `{style_context.get('source', '')}`",
            f"- Fingerprint: {json.dumps(style_context.get('fingerprint', {}), ensure_ascii=False)}",
            f"- Notes: {style_context.get('notes', '')}",
            "",
            "## Humanizer v2 Self-Check",
            "",
            "Pass 1 remove:",
            *[f"- {item}" for item in as_list(humanizer.get("two_pass_workflow", {}).get("pass_1_remove_ai_templates") if isinstance(humanizer.get("two_pass_workflow"), dict) else [])],
            "",
            "Pass 2 strengthen:",
            *[f"- {item}" for item in as_list(humanizer.get("two_pass_workflow", {}).get("pass_2_strengthen_voice") if isinstance(humanizer.get("two_pass_workflow"), dict) else [])],
            "",
            "## Recent Gate History",
            "",
        ]
    )
    if gate_history:
        for item in gate_history:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- ch{int(item.get('chapter_number') or 0):03d}: "
                f"{item.get('severity', 'UNKNOWN')} passed={item.get('passed')}"
            )
    else:
        lines.append("- No recent gate history.")
    lines.extend(
        [
            "",
            "## Feedback Carryover",
            "",
            f"- Status: {feedback.get('status', 'missing')}",
            f"- Source chapter: ch{int(feedback.get('source_chapter') or 0):03d}",
            f"- Boundary: {feedback.get('hard_boundary', '')}",
            "- Source files:",
        ]
    )
    for source in as_list(feedback.get("source_files")):
        lines.append(f"  - `{source}`")
    if not as_list(feedback.get("source_files")):
        lines.append("  - none")
    lines.append("- Carryover items:")
    feedback_items = feedback.get("items") if isinstance(feedback.get("items"), list) else []
    if feedback_items:
        for item in feedback_items:
            if not isinstance(item, dict):
                continue
            label = item.get("kind", "feedback")
            source = item.get("source", "")
            summary_text = item.get("summary", "")
            lines.append(f"  - {label} (`{source}`): {summary_text}")
    else:
        lines.append("  - none")
    notes = as_list(feedback.get("notes"))
    if notes:
        lines.append("- Notes:")
        for note in notes:
            lines.append(f"  - {note}")
    lines.extend(
        [
            "",
            "## Canon Research",
            "",
        ]
    )
    canon_research = payload.get("canon_research", []) if isinstance(payload.get("canon_research"), list) else []
    if canon_research:
        for item in canon_research:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {item.get('title') or item.get('id')}: {item.get('summary', '')} (`{item.get('canon_file', '')}`)")
    else:
        lines.append("- No promoted research canon available.")
    lines.extend(
        [
            "",
            "## Forbidden",
            "",
        ]
    )
    for item in payload.get("forbidden", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Output Contract", ""])
    for item in must_follow:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("Must not include:")
    for item in must_not_include:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Agent Write Target",
            "",
            f"Write the chapter draft to `{relative_path(root, root / payload['draft_submission_path'])}`.",
            "Do not write directly to `40_manuscript/final/`, `60_rag/`, `30_state/story_graph.json`, or `70_runtime/db/`.",
            "",
        ]
    )
    return "\n".join(lines)


def current_outline_anchor(root: Path, chapter_number: int) -> dict[str, Any]:
    anchors = normalize_records(load_json(root / "20_outline" / "outline_anchors.json", default=[]))
    selected: dict[str, Any] = {}
    for anchor in anchors:
        if not isinstance(anchor, dict):
            continue
        anchor_chapter = int(anchor.get("chapter_number") or anchor.get("chapter") or 0)
        if anchor_chapter <= chapter_number:
            selected = anchor
        elif not selected:
            selected = anchor
            break
    if selected:
        return selected
    return {
        "chapter_number": chapter_number,
        "duty": "maintain longform promise and avoid premature resolution",
        "status": "synthetic",
        "forbidden_reveals": [],
        "resolution_markers": ["core longform mystery", "main volume conflict"],
        "requires_tail_suspense": False,
        "allowed_reveal_level": "hint",
        "must_preserve_suspense": ["core longform mystery", "main volume conflict"],
    }


def summarize_graph_constraints(root: Path, chapter_number: int) -> dict[str, Any]:
    graph = load_json(root / "30_state" / "story_graph.json", default={})
    if not isinstance(graph, dict):
        return {"entities": 0, "events": 0, "constraints": []}
    constraints: list[str] = []
    for entity in graph.get("entities", []):
        if not isinstance(entity, dict):
            continue
        status = entity.get("status")
        if status in {"dead", "injured", "betrayed", "revealed"}:
            constraints.append(f"{entity.get('name') or entity.get('id')} status is {status}")
    for event in graph.get("events", []):
        if not isinstance(event, dict):
            continue
        event_chapter = int(event.get("chapter_number") or event.get("chapter") or 0)
        if event_chapter >= chapter_number and event.get("cascade_pending"):
            constraints.append(f"future event {event.get('id')} is cascade pending")
    return {
        "entities": len(graph.get("entities", [])) if isinstance(graph.get("entities"), list) else 0,
        "events": len(graph.get("events", [])) if isinstance(graph.get("events"), list) else 0,
        "constraints": constraints[:12],
    }


def load_style_context(root: Path) -> dict[str, Any]:
    active_profile = root / "10_bible" / "style_profiles" / "current_style_profile.json"
    if active_profile.exists():
        payload = load_json(active_profile, default={})
        if isinstance(payload, dict):
            profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
            fingerprint = profile.get("fingerprint") if isinstance(profile.get("fingerprint"), dict) else profile
            notes = str(profile.get("summary") or json.dumps(profile, ensure_ascii=False))[:400]
            return {
                "source": relative_path(root, active_profile),
                "notes": notes,
                "fingerprint": fingerprint,
                "canonical": False,
                "active_profile": True,
                "profile_type": payload.get("profile_type", ""),
                "sample_sources": payload.get("sample_sources", []),
                "library_source": payload.get("library_source", ""),
            }
    canonical = root / "60_rag" / "memory" / "style" / "style_fingerprint.json"
    if canonical.exists():
        payload = load_json(canonical, default={})
        if isinstance(payload, dict):
            return {
                "source": relative_path(root, canonical),
                "notes": str(payload.get("notes") or "")[:400],
                "fingerprint": payload.get("fingerprint") if isinstance(payload.get("fingerprint"), dict) else {},
                "canonical": True,
            }
    candidates = [
        root / "10_bible" / "style_bible.md",
        root / "10_bible" / "style.md",
        root / "00_governance" / "reader_contract.md",
    ]
    source = next((path for path in candidates if path.exists()), None)
    text = safe_read_text(source) if source else ""
    return {
        "source": relative_path(root, source) if source else "",
        "notes": text[:400],
        "fingerprint": simple_style_fingerprint(text),
    }


def load_gate_history(root: Path, *, limit: int) -> list[dict[str, Any]]:
    gate_root = root / "50_workbench" / "gate_artifacts"
    records: list[dict[str, Any]] = []
    for path in sorted(gate_root.glob("ch*/gate_result.json")):
        payload = load_json(path, default={})
        if isinstance(payload, dict):
            records.append(
                {
                    "chapter_number": payload.get("chapter_number"),
                    "passed": payload.get("passed"),
                    "severity": payload.get("severity"),
                    "failures": payload.get("failures", []),
                    "warnings": payload.get("warnings", []),
                }
            )
    return records[-limit:]


def build_feedback_carryover(root: Path, chapter_number: int) -> dict[str, Any]:
    """Summarize previous controlled review artifacts for the next writing task."""

    source_chapter = chapter_number - 1
    if source_chapter <= 0:
        return {
            "status": "none",
            "source_chapter": 0,
            "source_files": [],
            "items": [],
            "notes": ["No previous chapter feedback is available for the first chapter."],
            "hard_boundary": "feedback is guidance only; it does not mutate final/RAG/graph/SQLite",
        }

    items: list[dict[str, Any]] = []
    source_files: list[str] = []
    gate_dir = root / "50_workbench" / "gate_artifacts" / f"ch{source_chapter:03d}"

    gate_file = gate_dir / "gate_result.json"
    gate_payload = load_json(gate_file, default={})
    if isinstance(gate_payload, dict) and gate_file.exists():
        source_files.append(relative_path(root, gate_file))
        failures = gate_payload.get("failures") if isinstance(gate_payload.get("failures"), list) else []
        warnings = gate_payload.get("warnings") if isinstance(gate_payload.get("warnings"), list) else []
        items.append(
            {
                "kind": "gate_result",
                "source": relative_path(root, gate_file),
                "severity": gate_payload.get("severity", ""),
                "passed": gate_payload.get("passed"),
                "summary": summarize_feedback_list(failures, warnings, fallback="Gate passed without blocking feedback."),
                "next_command": gate_payload.get("next_command", ""),
            }
        )

    repair_file = gate_dir / "repair_plan.md"
    if repair_file.exists():
        source_files.append(relative_path(root, repair_file))
        items.append(
            {
                "kind": "repair_plan",
                "source": relative_path(root, repair_file),
                "summary": trim_text(safe_read_text(repair_file), 700),
            }
        )

    humanize_file = root / "50_workbench" / "humanizer_tasks" / f"ch{source_chapter:03d}.humanize_check.json"
    humanize_payload = load_json(humanize_file, default={})
    if isinstance(humanize_payload, dict) and humanize_file.exists():
        source_files.append(relative_path(root, humanize_file))
        issues = humanize_payload.get("issues") if isinstance(humanize_payload.get("issues"), list) else []
        warnings = humanize_payload.get("warnings") if isinstance(humanize_payload.get("warnings"), list) else []
        items.append(
            {
                "kind": "humanize_check",
                "source": relative_path(root, humanize_file),
                "passed": humanize_payload.get("passed"),
                "summary": summarize_feedback_list(issues, warnings, fallback="Humanizer check passed."),
                "next_command": humanize_payload.get("next_command", ""),
            }
        )

    semantic_pacing_file = gate_dir / "semantic_pacing_result.json"
    semantic_pacing_payload = load_json(semantic_pacing_file, default={})
    if isinstance(semantic_pacing_payload, dict) and semantic_pacing_file.exists():
        source_files.append(relative_path(root, semantic_pacing_file))
        issues = semantic_pacing_payload.get("issues") if isinstance(semantic_pacing_payload.get("issues"), list) else []
        warnings = semantic_pacing_payload.get("warnings") if isinstance(semantic_pacing_payload.get("warnings"), list) else []
        items.append(
            {
                "kind": "semantic_pacing",
                "source": relative_path(root, semantic_pacing_file),
                "verdict": semantic_pacing_payload.get("verdict", ""),
                "tier": semantic_pacing_payload.get("tier", ""),
                "summary": summarize_feedback_list(issues, warnings, fallback="No semantic pacing blocker."),
            }
        )

    editorial_file = root / "50_workbench" / "editorial_reviews" / f"ch{source_chapter:03d}.aggregate.json"
    editorial_payload = load_json(editorial_file, default={})
    if isinstance(editorial_payload, dict) and editorial_file.exists():
        source_files.append(relative_path(root, editorial_file))
        unresolved = editorial_payload.get("unresolved_items") if isinstance(editorial_payload.get("unresolved_items"), list) else []
        reasons = editorial_payload.get("need_human_reasons") if isinstance(editorial_payload.get("need_human_reasons"), list) else []
        items.append(
            {
                "kind": "editorial_aggregate",
                "source": relative_path(root, editorial_file),
                "need_human": editorial_payload.get("need_human", False),
                "severity_counts": editorial_payload.get("severity_counts", {}),
                "summary": summarize_feedback_list(unresolved, reasons, fallback="Editorial aggregate has no unresolved blocker."),
                "next_command": editorial_payload.get("next_command", ""),
            }
        )

    return {
        "status": "available" if items else "empty",
        "source_chapter": source_chapter,
        "source_files": dedupe_strings(source_files),
        "items": items,
        "notes": [
            "Use this feedback to avoid repeating prior gate, pacing, humanizer, or editorial issues.",
            "Feedback is guidance only; official state still changes only through validate/apply/finalize commands.",
        ],
        "hard_boundary": "feedback is guidance only; it does not mutate final/RAG/graph/SQLite",
    }


def feedback_source_paths(root: Path, feedback: dict[str, Any]) -> list[Path]:
    return [root / str(path) for path in feedback.get("source_files", []) if str(path).strip()]


def summarize_feedback_list(primary: list[Any], secondary: list[Any], *, fallback: str) -> str:
    fragments: list[str] = []
    for item in [*primary[:4], *secondary[:4]]:
        if isinstance(item, dict):
            code = str(item.get("code") or item.get("severity") or item.get("status") or "").strip()
            message = str(item.get("message") or item.get("summary") or item.get("reason") or item.get("code") or "").strip()
            text = ": ".join(part for part in (code, message) if part)
        else:
            text = str(item).strip()
        if text:
            fragments.append(text)
    return "; ".join(fragments) if fragments else fallback


def dedupe_strings(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def simple_style_fingerprint(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    sentences = [part for part in re.split(r"[.!?。！？]+", text) if part.strip()]
    length_sum = sum(len(re.sub(r"\s+", "", item)) for item in sentences)
    return {
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "avg_sentence_chars": round(length_sum / max(1, len(sentences)), 2),
        "dialogue_ratio": round((text.count('"') + text.count("“") + text.count("”")) / max(1, len(text)), 4),
    }


def verify_stale_indexes(root: Path, chapter_number: int) -> None:
    stale_path = root / "30_state" / "stale_indexes.json"
    payload = load_json(stale_path, default={})
    if not isinstance(payload, dict) or not payload.get("unsafe_continuation_blocker"):
        return
    from_chapter = int(payload.get("from_chapter") or 0)
    if from_chapter and chapter_number >= from_chapter:
        next_command = payload.get("next_command") or "db rebuild project.yaml"
        raise WorkflowError(f"Stale outline/RAG artifacts block continuation from ch{from_chapter:03d}; run {next_command}.")


def record_finalized_event_usage(config: ConfigDocument, root: Path, chapter_number: int) -> None:
    card = load_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json", default={})
    event_recommendation = card.get("event_recommendation") if isinstance(card, dict) else {}
    event_types = event_recommendation.get("recommended", []) if isinstance(event_recommendation, dict) else []
    gate = load_json(root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json", default={})
    tier = event_tier_for_types(tuple(event_types[:2]), "medium")
    if isinstance(gate, dict):
        for warning in gate.get("warnings", []):
            if isinstance(warning, str) and "fast" in warning.lower():
                tier = "fast"
                break
    record_event_usage(config, chapter_number=chapter_number, event_types=event_types[:2], tier=tier)


def write_draft(config: ConfigDocument, *, chapter_number: int, overwrite: bool) -> Path:
    root = resolve_project_root(config)
    draft_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    if draft_path.exists() and not overwrite:
        return draft_path
    card = load_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json", default={})
    title = card.get("title") or f"第{chapter_number}章 待定章节"
    text = "\n".join(
        [
            f"# {title}",
            "",
            "风从尚未命名的道路尽头吹来，旧有的矛盾没有退去，新的阻力已经逼近。",
            "",
            "主角在压力之下重新确认目标，必须在保守退让和继续前行之间做出选择。",
            "",
            "远处传来的变化打断了短暂平静，也把下一层悬念推到众人面前。",
            "",
        ]
    )
    atomic_write_text(draft_path, text)
    return draft_path


def verify_previous_gate(root: Path, chapter_number: int) -> None:
    if chapter_number <= 1:
        return
    previous = chapter_number - 1
    if final_chapter_exists(root, previous):
        return

    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{previous:03d}" / "gate_result.json"
    payload = load_json(gate_path, default={}) if gate_path.exists() else {}
    if isinstance(payload, dict) and payload.get("passed") is False and not gate_has_waiver(payload):
        raise WorkflowError(f"Previous chapter ch{previous:03d} failed gate; run repair-chapter before continue-write.")
    if isinstance(payload, dict) and (payload.get("passed") is True or gate_has_waiver(payload)):
        raise WorkflowError(f"Previous chapter ch{previous:03d} is gate-approved but not finalized; run chapter finalize before continue-write.")
    if draft_chapter_exists(root, previous):
        raise WorkflowError(f"Previous chapter ch{previous:03d} has a draft but is not finalized; run draft submit and chapter finalize before continue-write.")
    raise WorkflowError(f"Previous chapter ch{previous:03d} is not finalized; finish it before continue-write.")


def final_chapter_exists(root: Path, chapter_number: int) -> bool:
    return manuscript_chapter_exists(root / "40_manuscript" / "final", chapter_number)


def draft_chapter_exists(root: Path, chapter_number: int) -> bool:
    return manuscript_chapter_exists(root / "40_manuscript" / "draft", chapter_number)


def manuscript_chapter_exists(directory: Path, chapter_number: int) -> bool:
    for name in (
        f"ch{chapter_number:03d}.md",
        f"ch{chapter_number:03d}.txt",
        f"chapter_{chapter_number:03d}.md",
        f"chapter_{chapter_number:03d}.txt",
        f"{chapter_number}.md",
        f"{chapter_number}.txt",
    ):
        if (directory / name).exists():
            return True
    return False


def resolve_confirmations(config: ConfigDocument, provided: dict[str, Any]) -> dict[str, Any]:
    novel = config.data.get("novel", {})
    length = config.data.get("length", {})
    codex = config.data.get("codex", {})
    style = provided.get("writing_style") or novel.get("style") or novel.get("subgenre") or novel.get("genre")
    if isinstance(style, list):
        style = ", ".join(str(item) for item in style)
    forbidden = provided.get("core_forbidden_zone") or novel.get("forbidden_experience")
    target_scale = provided.get("target_scale") or (
        f"{length.get('total_chapters')} chapters / "
        f"{length.get('target_total_words')} words / "
        f"{length.get('chapter_word_count', {}).get('target')} words per chapter"
    )
    resolved = {
        "target_audience": provided.get("target_audience") or novel.get("audience"),
        "writing_style": style,
        "core_forbidden_zone": as_list(forbidden),
        "automation_level": provided.get("automation_level") or codex.get("default_workflow") or "command_driven",
        "target_scale": target_scale,
    }
    missing = [key for key, value in resolved.items() if not value]
    if missing:
        raise WorkflowError(f"open-book missing required confirmations: {', '.join(missing)}")
    if not resolved["core_forbidden_zone"]:
        raise WorkflowError("open-book missing required confirmations: core_forbidden_zone")
    return resolved


def infer_volume(config: ConfigDocument, chapter_number: int) -> int:
    length = config.data.get("length", {})
    volume_count = int(length.get("volume_count") or 1)
    total_chapters = int(length.get("total_chapters") or chapter_number)
    per_volume = max(1, total_chapters // max(1, volume_count))
    return min(volume_count, ((chapter_number - 1) // per_volume) + 1)


def upsert_chapter_plan(root: Path, card: dict[str, Any]) -> None:
    path = root / "20_outline" / "chapter_plan.json"
    payload = load_json(path, default=[])
    if not isinstance(payload, list):
        payload = []
    updated = False
    for index, item in enumerate(payload):
        if isinstance(item, dict) and item.get("chapter_number") == card["chapter_number"]:
            payload[index] = {
                "chapter_number": card["chapter_number"],
                "title": card["title"],
                "status": card["status"],
                "duty": card["duty"],
            }
            updated = True
            break
    if not updated:
        payload.append(
            {
                "chapter_number": card["chapter_number"],
                "title": card["title"],
                "status": card["status"],
                "duty": card["duty"],
            }
        )
    payload.sort(key=lambda item: item.get("chapter_number", 0) if isinstance(item, dict) else 0)
    write_json(path, payload)


def load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    return [value]


def normalize_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "records", "chapters", "anchors", "data"):
            if isinstance(value.get(key), list):
                return value[key]
        return list(value.values())
    return []


def estimate_words(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def safe_timestamp(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)[:14] or "run"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
