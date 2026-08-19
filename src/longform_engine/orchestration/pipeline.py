"""Workflow orchestration for opening books and drafting chapters."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.agent_protocols import output_protocol_for_task
from longform_engine.agent_tasks import (
    AgentTaskContractError,
    CHAPTER_CANDIDATE_TASK_TYPES,
    build_manifest,
    list_manifests,
    manifest_chapter_number,
    manifest_commands,
    mark_tasks_for_chapter_type,
    mark_tasks_for_output,
    resolve_candidate_task,
    status_summary,
    supersede_other_candidate_tasks,
    update_task_status,
    write_manifest,
)
from longform_engine.character_expression import build_character_expression_packet, character_expression_diagnostics
from longform_engine.chapter_contract import (
    ChapterContractError,
    load_verified_chapter_contract,
    resolve_chapter_contract_refs,
    stamp_chapter_contract,
)
from longform_engine.completion import fast_completion_marker
from longform_engine.config import ConfigDocument
from longform_engine.creative import (
    humanize_candidate_submission_guard,
    humanizer_rules,
    init_creative_brief,
    validate_creative_brief,
    writer_craft_brief,
)
from longform_engine.db import sync_database
from longform_engine.editorial import editorial_finalization_blockers
from longform_engine.gates import gate_check, semantic_pacing_review_status
from longform_engine.graph import validate_graph
from longform_engine.intelligence import assess_chapter_direction, assess_project_readiness
from longform_engine.lengths import compile_length_forecast
from longform_engine.memory import build_tcs
from longform_engine.models import semantic_enabled
from longform_engine.planning import event_tier_for_types, recommend_event_types, record_event_usage
from longform_engine.prompting import estimate_text_units, resolve_context_budget_contract
from longform_engine.quality import (
    carry_feedback,
    compact_effective_quality_contract,
    compile_effective_quality_contract,
    infer_story_phase,
    reader_payoff_review_status,
    record_quality_history,
)
from longform_engine.rag import build_context
from longform_engine.repair_coordination import (
    RepairCoordinationError,
    ensure_candidate_snapshot,
    preflight_repair_submission,
    record_repair_submission,
    review_barrier_status,
)
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import existing_manuscript_chapter_path, manuscript_chapter_path
from longform_engine.text_metrics import content_character_count, display_character_count


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
    forecast_chapters: int
    target_characters: int
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
    forecast = compile_length_forecast(length)
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
                f"- Story profile: {json.dumps(config.data.get('story_profile', {}), ensure_ascii=False)}",
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
                f"- Target content characters: {forecast.target_total_characters}",
                f"- Forecast chapters: {forecast.estimated_chapters}",
                f"- Forecast volumes: {forecast.estimated_volumes}",
                f"- Support status: {forecast.support_status}",
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
    creation_mode = str(config.data.get("creation", {}).get("mode") or "original")
    project_intelligence = {
        "book_ideation": {"status": "required"},
        "book_design": {"status": "blocked_by_book_ideation"},
        "outline_design": {"status": "blocked_by_book_design"},
    }
    if creation_mode == "fanfiction":
        project_intelligence = {
            "fanfiction_canon": {"status": "required"},
            "book_ideation": {"status": "blocked_by_fanfiction_canon"},
            "fanfiction_design": {"status": "blocked_by_book_ideation"},
            "book_design": {"status": "blocked_by_fanfiction_design"},
            "outline_design": {"status": "blocked_by_fanfiction_design"},
        }
    state.update(
        {
            "status": "open_book_confirmed",
            "current_chapter": int(state.get("current_chapter") or 0),
            "last_finalized_chapter": int(state.get("last_finalized_chapter") or 0),
            "required_confirmations": resolved,
            "creation_mode": creation_mode,
            "project_intelligence": project_intelligence,
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
    planned_chapter = next(
        (
            item
            for item in normalize_records(load_json(root / "20_outline" / "chapter_plan.json", default=[]))
            if isinstance(item, dict) and int(item.get("chapter_number") or 0) == chapter_number
        ),
        {},
    )
    characters = normalize_records(load_json(root / "10_bible" / "characters.json", default=[]))
    character_ids = [str(item.get("id")) for item in characters if isinstance(item, dict) and item.get("id")]
    planned_featured = dedupe_strings(as_list(planned_chapter.get("featured_character_ids")))
    featured_character_ids = [item for item in planned_featured if item in character_ids]
    pov_character_id = str(planned_chapter.get("pov_character_id") or "")
    if pov_character_id not in character_ids:
        pov_character_id = character_ids[0] if character_ids else ""
    if pov_character_id and pov_character_id not in featured_character_ids:
        featured_character_ids.insert(0, pov_character_id)
    title = str(planned_chapter.get("title") or f"第{chapter_number}章 待定章节")
    chapter_duty = str(
        planned_chapter.get("chapter_duty")
        or (anchor.get("duty") if isinstance(anchor, dict) else "")
        or ("建立读者契约并打开第一层悬念。" if chapter_number == 1 else "承接上一章状态，推进一个明确的局部矛盾。")
    )
    reader_gain = str(
        planned_chapter.get("reader_gain")
        or "Pay off one local promise while preserving the core longform mystery."
    )
    topology_id = str(
        planned_chapter.get("topology_id")
        or infer_chapter_topology(chapter_duty, chapter_number)
    )
    effective_quality_contract = compile_effective_quality_contract(
        config,
        chapter_number=chapter_number,
    )
    quality_body = (
        effective_quality_contract.get("contract")
        if isinstance(effective_quality_contract.get("contract"), dict)
        else {}
    )
    book_goal = chapter_book_goal(root)
    volume_goal = chapter_volume_goal(root, volume)
    protagonist_goal = chapter_protagonist_goal(root, pov_character_id)
    scene_chain = planned_chapter.get("scene_chain")
    if not isinstance(scene_chain, list) or not scene_chain:
        scene_chain = [
            {
                "scene_id": f"ch{chapter_number:03d}:primary",
                "location": str(planned_chapter.get("location") or "由当前 TCS 确认的场景地点"),
                "participants": featured_character_ids,
                "desire_collision": str(
                    planned_chapter.get("conflict")
                    or "主角近期目标受到可观察阻力，必须作出选择。"
                ),
                "choice": str(
                    planned_chapter.get("choice")
                    or "主角采取会改变后续条件的行动。"
                ),
                "cost": str(
                    planned_chapter.get("cost")
                    or "本章收益带来义务、损失或更窄的后续选择。"
                ),
                "turn": str(
                    planned_chapter.get("information_release")
                    or "场景结束时至少一项事实、关系或行动条件发生变化。"
                ),
            }
        ]
    card = {
        "chapter_number": chapter_number,
        "title": title,
        "book_goal": book_goal,
        "volume_goal": volume_goal,
        "protagonist_goal": protagonist_goal,
        "volume": volume,
        "status": "planned",
        "chapter_duty": chapter_duty,
        "conflict": planned_chapter.get("conflict") or "让主角在当前目标与外部阻力之间做出选择。",
        "information_release": planned_chapter.get("information_release") or "只释放与本章目标相关的一层信息，保留核心秘密。",
        "hook": planned_chapter.get("hook") or "章末留下危机升级、收益未兑现或新信息反转。",
        "outline_anchor": anchor,
        "event_recommendation": event_recommendation,
        "reader_gain": reader_gain,
        "cost": str(planned_chapter.get("cost") or "本章收益必须带来可见代价、义务或更窄的后续选择。"),
        "platform_promise": str(
            planned_chapter.get("platform_promise")
            or quality_body.get("platform_promise")
            or "以可观察的因果变化推进可持续的连载承诺。"
        ),
        "plot_obligation": str(planned_chapter.get("plot_obligation") or chapter_duty),
        "dramatic_freedom": str(
            planned_chapter.get("dramatic_freedom")
            or "Choose scene actions, friction, and subtext freely while preserving plot obligation and canonical facts."
        ),
        "pov_character_id": pov_character_id,
        "featured_character_ids": featured_character_ids[:6],
        "characterization_focus": dedupe_strings(as_list(planned_chapter.get("characterization_focus"))),
        "scene_wants": planned_chapter.get("scene_wants") if isinstance(planned_chapter.get("scene_wants"), dict) else {},
        "opposing_wants": dedupe_strings(as_list(planned_chapter.get("opposing_wants"))),
        "hidden_agenda": dedupe_strings(as_list(planned_chapter.get("hidden_agenda"))),
        "relationship_move": str(
            planned_chapter.get("relationship_move")
            or "若无可见因果，不改变既有关系阶段。"
        ),
        "scene_chain": scene_chain,
        "canon_refs": dedupe_strings(as_list(planned_chapter.get("canon_refs"))),
        "world_rule_refs": dedupe_strings(as_list(planned_chapter.get("world_rule_refs"))),
        "foreshadow_refs": dedupe_strings(
            as_list(planned_chapter.get("foreshadow_refs"))
            + as_list(planned_chapter.get("promise_refs"))
        ),
        "voice_state": planned_chapter.get("voice_state") if isinstance(planned_chapter.get("voice_state"), dict) else {},
        "embodiment_strategy": str(planned_chapter.get("embodiment_strategy") or "selective scene-specific embodiment"),
        "summary_scene_policy": str(
            planned_chapter.get("summary_scene_policy")
            or "Dramatize choices, costs, and relationship turns; summarize connective travel and routine procedure."
        ),
        "irreversible_action": str(planned_chapter.get("irreversible_action") or ""),
        "emotional_aftereffect": str(planned_chapter.get("emotional_aftereffect") or ""),
        "topology_id": topology_id,
        "hook_mode": str(planned_chapter.get("hook_mode") or "changed_problem"),
        "promise_refs": dedupe_strings(as_list(planned_chapter.get("promise_refs"))),
        "forbidden_reveals": dedupe_strings(
            as_list(planned_chapter.get("forbidden_reveals"))
            + (anchor.get("forbidden_reveals", []) if isinstance(anchor, dict) else [])
        ),
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
    if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction":
        card.update(
            {
                "canon_refs": dedupe_strings(as_list(planned_chapter.get("canon_refs"))),
                "divergence_effects": dedupe_strings(as_list(planned_chapter.get("divergence_effects"))),
                "voice_refs": dedupe_strings(as_list(planned_chapter.get("voice_refs"))),
                "original_contribution": str(
                    planned_chapter.get("original_contribution")
                    or "Advance the declared original mainline without reducing canon characters to props."
                ),
                "protected_reveals": dedupe_strings(as_list(planned_chapter.get("protected_reveals"))),
            }
        )
    card["requires_semantic_review"] = requires_milestone_semantic_review(
        config,
        chapter_number,
        volume,
        planned_chapter,
    )
    reverse_brake = build_reverse_brake_contract(config, chapter_number, anchor, card=card)
    card["reverse_brake"] = reverse_brake
    card["forbidden_reveals"] = reverse_brake["forbidden_reveals"]
    card["resolution_markers"] = reverse_brake["do_not_resolve"]
    card["requires_tail_suspense"] = reverse_brake["requires_tail_suspense"]
    card["allowed_reveal_level"] = reverse_brake["allowed_reveal_level"]
    card["must_preserve_suspense"] = reverse_brake["must_preserve_suspense"]
    card["effective_quality_contract"] = compact_effective_quality_contract(
        effective_quality_contract
    )
    stamp_chapter_contract(card)
    write_chapter_card_artifacts(root, card)
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
            "chapter_duty": card.get("chapter_duty"),
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
            "chapter_duty": card.get("chapter_duty"),
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
            "chapter_duty": card.get("chapter_duty"),
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
            "chapter_duty": card.get("chapter_duty"),
            "scene_purpose": "release one controlled information layer",
            "conflict": "new fact changes the problem",
            "turn": card.get("information_release", "释放一层新信息。"),
            "hook": "meaning reframed",
            "event_type": "reveal",
            "expansion_notes": "Do not solve the core conflict unless the anchor marks closure.",
            "purpose": card.get("information_release", "释放一层新信息。"),
        },
        {
            "order": 5,
            "name": "Hook",
            "pacing_mode": pacing_mode,
            "chapter_duty": card.get("chapter_duty"),
            "scene_purpose": "deliver tail suspense",
            "conflict": "payoff withheld or inverted",
            "turn": "chapter meaning sharpens",
            "hook": card.get("hook", "章末保留期待。"),
            "event_type": "tail_hook",
            "expansion_notes": "End with a concrete unanswered image, decision, threat, or reveal.",
            "purpose": card.get("hook", "章末保留期待。"),
        },
    ]
    beats = apply_chapter_topology(beats, topology_id=str(card.get("topology_id") or "conflict_escalation"), card=card)
    for beat in beats:
        beat.setdefault("scene_tension", "make pressure visible through action, cost, or withheld information")
        beat.setdefault("reader_gain", card.get("reader_gain", "one local payoff without core-resolution leakage"))
        beat.setdefault("dialogue_intent", "each exchange must reveal pressure, status, concealment, or relationship movement")
        beat.setdefault("sensory_anchor", "ground this beat in one concrete sensory or body detail")
        beat.setdefault("ending_hook", card.get("hook", "close the beat on a changed problem"))
        beat.setdefault("scene_goal", beat.get("scene_purpose") or beat.get("purpose") or "advance the chapter duty in-scene")
        beat.setdefault("conflict_point", beat.get("conflict") or card.get("conflict") or "visible pressure against the current goal")
        beat.setdefault("information_release", beat.get("turn") or card.get("information_release") or "release only what this beat needs")
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


def infer_chapter_topology(chapter_duty: str, chapter_number: int) -> str:
    duty = str(chapter_duty or "").lower()
    if any(marker in duty for marker in ("关系", "感情", "信任", "和解", "背叛", "relationship")):
        return "relationship_turn"
    if any(marker in duty for marker in ("揭露", "真相", "线索", "发现", "reveal", "clue")):
        return "revelation"
    if any(marker in duty for marker in ("余波", "代价", "恢复", "aftermath", "recovery")):
        return "aftermath"
    if any(marker in duty for marker in ("探索", "调查", "世界", "explore", "investigate")):
        return "exploration"
    if any(marker in duty for marker in ("兑现", "胜利", "突破", "payoff", "victory")):
        return "payoff"
    return "opening_contract" if chapter_number == 1 else "conflict_escalation"


def apply_chapter_topology(
    beats: list[dict[str, Any]],
    *,
    topology_id: str,
    card: dict[str, Any],
) -> list[dict[str, Any]]:
    selections = {
        "opening_contract": (0, 1, 2, 3, 4),
        "conflict_escalation": (0, 1, 2, 3, 4),
        "relationship_turn": (0, 2, 3, 4),
        "revelation": (0, 1, 3, 4),
        "aftermath": (0, 2, 4),
        "exploration": (0, 1, 3, 4),
        "payoff": (1, 2, 3, 4),
    }
    selected = [dict(beats[index]) for index in selections.get(topology_id, selections["conflict_escalation"])]
    labels = {
        "opening_contract": ("Immediate promise", "First irreversible pressure"),
        "relationship_turn": ("Relationship baseline", "Changed relationship state"),
        "revelation": ("Question under pressure", "Meaning-changing evidence"),
        "aftermath": ("Visible consequence", "Cost-bearing choice"),
        "exploration": ("Unknown made concrete", "Discovery with a price"),
        "payoff": ("Promised pressure", "Earned local payoff"),
        "conflict_escalation": ("Current pressure", "Changed problem"),
    }
    start_label, end_label = labels.get(topology_id, labels["conflict_escalation"])
    selected[0]["name"] = start_label
    selected[-1]["name"] = end_label
    selected[-1]["reader_gain"] = card.get("reader_gain")
    for order, beat in enumerate(selected, start=1):
        beat["order"] = order
        beat["topology_id"] = topology_id
        beat["chapter_cost"] = card.get("cost")
    return selected


def requires_milestone_semantic_review(
    config: ConfigDocument,
    chapter_number: int,
    volume: int,
    planned_chapter: dict[str, Any],
) -> bool:
    quality = config.data.get("quality", {}) if isinstance(config.data.get("quality"), dict) else {}
    milestones = {
        int(item)
        for item in quality.get("semantic_review_milestones") or []
        if isinstance(item, int) and not isinstance(item, bool) and item > 0
    }
    volumes = normalize_records(load_json(resolve_project_root(config) / "20_outline" / "volumes.json", default=[]))
    volume_boundaries = {
        int(item.get(field))
        for item in volumes if isinstance(item, dict) and int(item.get("number") or 0) == volume
        for field in ("from_chapter", "to_chapter")
        if isinstance(item.get(field), int)
    }
    explicit = bool(
        planned_chapter.get("requires_semantic_review")
        or planned_chapter.get("major_reveal")
        or planned_chapter.get("relationship_turn")
    )
    milestone_boundaries = bool(quality.get("semantic_review_boundaries", True))
    return (
        explicit
        or str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        or (
            milestone_boundaries
            and (chapter_number in milestones or chapter_number in volume_boundaries)
        )
    )


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
    readiness = assess_project_readiness(config)
    if not readiness.ready:
        next_command = (
            "longform-engine open-book project.yaml"
            if readiness.stage == "open_book"
            else f"longform-engine intelligence task project.yaml --task-type {readiness.required_task_type}"
        )
        raise WorkflowError(
            f"Project is not ready for chapter writing ({readiness.stage}): "
            f"{'; '.join(readiness.errors[:3])} Run {next_command}."
        )
    direction = assess_chapter_direction(config, chapter_number)
    if direction["required"]:
        raise WorkflowError(
            f"Chapter ch{chapter_number:03d} requires an explicit direction choice "
            f"({', '.join(direction['reasons'])}). Run longform-engine intelligence task project.yaml "
            f"--task-type chapter_direction --chapter {chapter_number}."
        )

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
    context = build_context(
        config,
        chapter_number=chapter_number,
        semantic=semantic_enabled(config),
    )
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
    humanizer_guard = humanize_candidate_submission_guard(
        config,
        chapter_number=chapter_number,
        candidate_file=source_path,
    )
    if not humanizer_guard.get("allowed", False):
        raise WorkflowError(
            "Humanizer candidate cannot be submitted: "
            f"{humanizer_guard.get('reason') or 'required checks are missing or stale'}."
        )
    text = safe_read_text(source_path).strip()
    if not text:
        raise WorkflowError("Agent draft is empty.")
    try:
        candidate_task = resolve_candidate_task(
            root,
            chapter_number=chapter_number,
            output_path=source_path,
        )
    except AgentTaskContractError as exc:
        raise WorkflowError(str(exc)) from exc
    candidate_task_id = str(candidate_task.get("task_id") or "")
    candidate_task_type = str(candidate_task.get("task_type") or "")
    if candidate_task_type == "repair":
        try:
            preflight_repair_submission(
                config,
                chapter_number=chapter_number,
                task_id=candidate_task_id,
                source_path=source_path,
            )
        except RepairCoordinationError as exc:
            raise WorkflowError(str(exc)) from exc
    replaced_task_ids = [
        str(task.get("task_id") or "")
        for task in list_manifests(root, chapter_number=chapter_number)
        if str(task.get("task_id") or "") != candidate_task_id
        and str(task.get("task_type") or "") in CHAPTER_CANDIDATE_TASK_TYPES
        and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
    ]

    draft_path = manuscript_chapter_path(root, chapter_number, lane="draft")
    if draft_path.exists() and not overwrite:
        raise WorkflowError(f"Draft already exists for ch{chapter_number:03d}; pass --overwrite to replace it.")

    atomic_write_text(draft_path, text + "\n")
    candidate_snapshot = ensure_candidate_snapshot(root, chapter_number=chapter_number)
    submitted_at = utc_now()
    submission_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.submission.json"
    task_path = root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json"
    previous_submission = load_json(submission_path, default={})
    previous_revision = (
        int(previous_submission.get("candidate_revision") or 0)
        if isinstance(previous_submission, dict)
        else 0
    )
    candidate_source_hash = sha256_bytes(source_path.read_bytes())
    submission = {
        "schema_version": 2,
        "chapter_number": chapter_number,
        "agent": agent,
        "source_file": relative_path(root, source_path),
        "draft_file": relative_path(root, draft_path),
        "writing_task": relative_path(root, task_path) if task_path.exists() else None,
        "source_sha256": candidate_source_hash,
        "draft_sha256": sha256_text(text + "\n"),
        "candidate_task_id": candidate_task_id,
        "candidate_task_type": candidate_task_type,
        "candidate_revision": previous_revision + 1,
        "candidate_source_path": relative_path(root, source_path),
        "candidate_source_hash": candidate_source_hash,
        "candidate_snapshot_path": relative_path(root, candidate_snapshot),
        "candidate_snapshot_hash": sha256_bytes(candidate_snapshot.read_bytes()),
        "candidate_status": "submitted",
        "replaces_task_ids": replaced_task_ids,
        "word_count": content_character_count(text),
        "submitted_at": submitted_at,
    }
    write_json(submission_path, submission)

    update_task_status(
        root,
        candidate_task_id,
        to_status="submitted",
        command="draft submit",
        artifact=source_path,
        result=draft_path,
    )
    supersede_other_candidate_tasks(
        root,
        chapter_number=chapter_number,
        current_task_id=candidate_task_id,
        command="draft submit",
        artifact=source_path,
    )
    if candidate_task_type == "repair":
        record_repair_submission(
            config,
            chapter_number=chapter_number,
            task_id=candidate_task_id,
            source_path=source_path,
        )
    gate = gate_check(config, chapter_number=chapter_number, source="draft", semantic=True)
    gate_path = Path(gate.gate_result)
    pacing_path = gate_path.parent / "pacing_review.md"
    gate_payload = load_json(gate_path, default={})
    semantic_state = gate_payload.get("agent_semantic_review") if isinstance(gate_payload, dict) else {}
    semantic_review_pending = bool(
        isinstance(semantic_state, dict)
        and semantic_state.get("required") is True
        and str(semantic_state.get("status") or "") != "applied"
    )
    next_command = "longform-engine production next project.yaml"
    normalize_agent_gate_result(
        gate_path,
        gate.passed,
        next_command,
        semantic_review_pending=semantic_review_pending,
        candidate={
            "task_id": candidate_task_id,
            "task_type": candidate_task_type,
            "source_path": relative_path(root, source_path),
            "source_hash": candidate_source_hash,
            "revision": previous_revision + 1,
        },
    )
    if humanizer_guard.get("required"):
        mark_tasks_for_chapter_type(
            root,
            chapter_number=chapter_number,
            task_types=("humanize", "humanize_semantic_review"),
            to_status="applied",
            command="draft submit",
            artifact=source_path,
            result=submission_path,
            from_statuses=("submitted", "validated"),
        )

    upsert_chapter_meta(
        root,
        {
            "chapter_number": chapter_number,
            "title": extract_title(text, chapter_number),
            "path": relative_path(root, draft_path),
            "status": (
                "reviews_pending"
            ),
            "word_count": content_character_count(text),
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
            "status": (
                "reviews_pending"
            ),
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
    if semantic_review_pending:
        state["pending_semantic_review_chapter"] = chapter_number
    else:
        state.pop("pending_semantic_review_chapter", None)
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
    update_task_status(
        root,
        candidate_task_id,
        to_status="submitted",
        command="gate-check",
        artifact=source_path,
        result=gate_path,
    )
    submission["candidate_status"] = "submitted"
    submission["updated_at"] = utc_now()
    write_json(submission_path, submission)

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
    draft_path = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft_path.exists():
        raise WorkflowError(f"Draft not found for ch{chapter_number:03d}; run draft submit first.")

    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    gate = require_finalizable_gate(gate_path, chapter_number)
    payoff_status = reader_payoff_review_status(config, chapter_number=chapter_number)
    if payoff_status.get("required") and not payoff_status.get("passed"):
        raise WorkflowError(
            f"Chapter ch{chapter_number:03d} is not finalizable: reader payoff review is missing, "
            "failed, or stale; run "
            f"longform-engine quality payoff-task project.yaml --chapter {chapter_number}."
        )
    payoff_review = payoff_status.get("review") if payoff_status.get("passed") else None
    payoff_output = (
        root / str(payoff_status.get("output_file") or "")
        if payoff_status.get("output_file")
        else None
    )
    payoff_report = (
        root / str(payoff_status.get("report_file") or "")
        if payoff_status.get("report_file")
        else None
    )
    pacing_status = semantic_pacing_review_status(config, chapter_number=chapter_number)
    if pacing_status.get("required") and not pacing_status.get("passed"):
        raise WorkflowError(
            f"Chapter ch{chapter_number:03d} is not finalizable: semantic pacing review is missing, "
            "failed, or stale; run "
            f"longform-engine pacing semantic-task project.yaml --chapter {chapter_number}."
        )
    editorial_blockers = editorial_finalization_blockers(config, chapter_number=chapter_number)
    if editorial_blockers:
        raise WorkflowError(
            f"Chapter ch{chapter_number:03d} is not finalizable: editorial aggregate requires human review "
            f"({', '.join(editorial_blockers)})."
        )
    review_barrier = review_barrier_status(config, chapter_number=chapter_number)
    if review_barrier.get("status") != "ready_to_finalize":
        raise WorkflowError(
            f"Chapter ch{chapter_number:03d} is not finalizable: review barrier status is "
            f"{review_barrier.get('status') or 'unknown'}; run longform-engine production next project.yaml."
        )

    text = safe_read_text(draft_path).strip()
    if not text:
        raise WorkflowError(f"Draft is empty for ch{chapter_number:03d}.")
    final_path = manuscript_chapter_path(root, chapter_number, lane="final")
    if final_path.exists() and not overwrite:
        raise WorkflowError(f"Final manuscript already exists for ch{chapter_number:03d}; pass --overwrite to replace it.")
    if final_path.exists() and overwrite:
        ledger_path = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
        closure_path = root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json"
        if ledger_path.exists() or closure_path.exists():
            raise WorkflowError(
                f"Final manuscript ch{chapter_number:03d} is immutable after semantic apply or chapter close; "
                "create an explicit migration/revision task instead of overwriting its evidence source."
            )

    finalization_path = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.finalization.json"
    summary_path = manuscript_chapter_path(root, chapter_number, lane="summaries")
    submission_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.submission.json"
    submission = load_json(submission_path, default={})
    submitted_source = (
        root / str(submission.get("source_file"))
        if isinstance(submission, dict) and submission.get("source_file")
        else draft_path
    )
    state_path = root / "30_state" / "novel_state.json"
    metrics_path = root / "30_state" / "manuscript_metrics.json"
    previous_final_text = safe_read_text(final_path) if final_path.is_file() else ""
    run_report = root / "70_runtime" / "run_reports" / f"chapter_finalize_ch{chapter_number:03d}.json"
    next_command = f"longform-engine chapter semantic-task project.yaml --chapter {chapter_number}"
    with apply_transaction(
        root,
        command="chapter finalize",
        chapter_number=chapter_number,
        source_paths=[
            draft_path,
            gate_path,
            *([payoff_output, payoff_report] if payoff_output is not None and payoff_report is not None else []),
            *(
                [root / str(pacing_status.get("result_file"))]
                if pacing_status.get("required") and pacing_status.get("result_file")
                else []
            ),
        ],
        touched_paths=[
            final_path,
            finalization_path,
            summary_path,
            root / "40_manuscript" / "chapter_meta.jsonl",
            state_path,
            metrics_path,
            root / "30_state" / "event_matrix.json",
            root / "30_state" / "reward_ledger.jsonl",
            root / "30_state" / "quality" / "structure_history.jsonl",
            run_report,
        ],
        metadata={
            "approved_by": approved_by,
            "rebuild_boundaries": ["chapter semantic-apply", "RAG rebuild", "SQLite sync"],
        },
    ) as transaction:
        final_text = text + "\n"
        atomic_write_text(final_path, final_text)
        finalized_at = utc_now()
        summary_path = write_pending_chapter_summary(root, chapter_number)
        finalization = {
            "schema_version": 1,
            "chapter_number": chapter_number,
            "approved_by": approved_by,
            "finalized_at": finalized_at,
            "draft_file": relative_path(root, draft_path),
            "final_file": relative_path(root, final_path),
            "summary_file": relative_path(root, summary_path),
            "summary_status": "pending_semantic_extraction",
            "gate_result": relative_path(root, gate_path),
            "gate_passed": bool(gate.get("passed")),
            "gate_waived": gate_has_waiver(gate),
            "reader_payoff_review_required": bool(payoff_status.get("required")),
            "reader_payoff_review": (
                relative_path(root, payoff_output)
                if payoff_output is not None and payoff_status.get("passed")
                else ""
            ),
            "draft_sha256": sha256_text(final_text),
            "final_sha256": sha256_text(final_text),
        }
        write_json(finalization_path, finalization)

        metrics = load_json(metrics_path, default={})
        if not isinstance(metrics, dict) or metrics.get("schema") != "manuscript_metrics_v1":
            raise WorkflowError("30_state/manuscript_metrics.json is missing or invalid for schema v2.")
        previous_content = content_character_count(previous_final_text)
        previous_display = display_character_count(previous_final_text)
        current_content = content_character_count(final_text)
        current_display = display_character_count(final_text)
        previous_count = int(metrics.get("finalized_chapter_count") or 0)
        finalized_count = previous_count if previous_final_text else previous_count + 1
        total_content = int(metrics.get("total_content_characters") or 0) - previous_content + current_content
        total_display = int(metrics.get("total_display_characters") or 0) - previous_display + current_display
        metrics.update(
            {
                "metric": "content_characters_v1",
                "finalized_chapter_count": finalized_count,
                "latest_finalized_chapter": max(
                    int(metrics.get("latest_finalized_chapter") or 0), chapter_number
                ),
                "total_content_characters": total_content,
                "total_display_characters": total_display,
                "average_content_characters": round(total_content / max(1, finalized_count)),
                "updated_at": finalized_at,
            }
        )
        write_json(metrics_path, metrics)

        upsert_chapter_meta(
            root,
            {
                "chapter_number": chapter_number,
                "title": extract_title(final_text, chapter_number),
                "path": relative_path(root, final_path),
                "summary": "",
                "volume": infer_volume(config, chapter_number),
                "status": "final",
                "content_character_count": current_content,
                "display_character_count": current_display,
                "metric": "content_characters_v1",
                "approved_by": approved_by,
                "finalization_file": relative_path(root, finalization_path),
                "finalized_at": finalized_at,
                "gate_result": relative_path(root, gate_path),
            },
        )

        record_finalized_event_usage(config, root, chapter_number)
        card = load_json(
            root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
            default={},
        )
        quality_history = record_quality_history(
            root,
            chapter_number=chapter_number,
            final_text=final_text,
            card=card if isinstance(card, dict) else {},
            review=payoff_review if isinstance(payoff_review, dict) else None,
        )
        state = load_json(state_path, default={})
        last_finalized = max(int(state.get("last_finalized_chapter") or 0), chapter_number)
        state.update(
            {
                "status": "chapter_finalized_pending_semantics",
                "current_chapter": chapter_number,
                "last_finalized_chapter": last_finalized,
                "last_pipeline": "chapter finalize",
                "last_finalized_file": relative_path(root, final_path),
                "last_finalization": relative_path(root, finalization_path),
                "pending_semantic_chapter": chapter_number,
                "updated_at": utc_now(),
            }
        )
        for key in (
            "pending_task_chapter",
            "pending_gate_chapter",
            "pending_final_chapter",
            "pending_semantic_review_chapter",
        ):
            pending_chapter = int(state.get(key) or 0)
            if pending_chapter == chapter_number or (
                key == "pending_semantic_review_chapter"
                and 0 < pending_chapter <= last_finalized
            ):
                state.pop(key, None)
        write_json(state_path, state)

        report = {
            "command": "chapter finalize",
            "chapter_number": chapter_number,
            "status": "chapter_finalized_pending_semantics",
            "approved_by": approved_by,
            "artifacts": {
                "draft": relative_path(root, draft_path),
                "final": relative_path(root, final_path),
                "summary": relative_path(root, summary_path),
                "finalization": relative_path(root, finalization_path),
                "gate_result": relative_path(root, gate_path),
                "reward_ledger": quality_history["reward_ledger"],
                "structure_history": quality_history["structure_history"],
                "semantic_ledger": f"30_state/semantic_ledger/ch{chapter_number:03d}.json",
            },
            "gate": {
                "passed": bool(gate.get("passed")),
                "waived": gate_has_waiver(gate),
                "severity": gate.get("severity"),
            },
            "reader_payoff": {
                "required": bool(payoff_status.get("required")),
                "reviewed": isinstance(payoff_review, dict),
                "review_file": (
                    relative_path(root, payoff_output)
                    if payoff_output is not None and payoff_status.get("passed")
                    else ""
                ),
            },
            "semantic_status": "pending_agent_extraction",
            "next_command": next_command,
            "created_at": utc_now(),
        }
        write_json(run_report, report)
        transaction.update_metadata(
            gate_passed=bool(gate.get("passed")),
            gate_waived=gate_has_waiver(gate),
            db_synced=False,
            run_report=relative_path(root, run_report),
            semantic_next_command=next_command,
        )
    candidate_statuses = ("awaiting_agent", "submitted", "validated", "invalid")
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=submitted_source,
        to_status="applied",
        command="chapter finalize",
        result=final_path,
        from_statuses=candidate_statuses,
    )
    if payoff_output is not None and payoff_status.get("passed"):
        mark_tasks_for_output(
            root,
            chapter_number=chapter_number,
            output_path=payoff_output,
            to_status="applied",
            command="chapter finalize",
            result=final_path,
            from_statuses=("validated",),
        )
    mark_tasks_for_chapter_type(
        root,
        chapter_number=chapter_number,
        task_types=("chapter_write", "repair", "humanize", "content_expand"),
        to_status="superseded",
        command="chapter finalize",
        artifact=submitted_source,
        result=final_path,
        from_statuses=candidate_statuses,
    )

    return ChapterFinalizeResult(
        chapter_number=chapter_number,
        final_file=str(final_path),
        finalization_file=str(finalization_path),
        summary_file=str(summary_path),
        gate_result=str(gate_path),
        graph_file=str(root / "30_state" / "story_graph.json"),
        rag_chunks_dir=str(root / "60_rag" / "chunks"),
        context_file=str(root / "60_rag" / "context" / "next_plot_context.md"),
        run_report=str(run_report),
        approved_by=approved_by,
        finalized_at=finalized_at,
        next_command=next_command,
        db_synced=False,
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


def write_pending_chapter_summary(root: Path, chapter_number: int) -> Path:
    summary_path = manuscript_chapter_path(root, chapter_number, lane="summaries")
    atomic_write_text(
        summary_path,
        "\n".join(
            [
                f"# Summary ch{chapter_number:03d}",
                "",
                "Pending unified semantic extraction. This file is not a factual summary yet.",
                "",
            ]
        ),
    )
    return summary_path


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
    try:
        chapter_contract, chapter_contract_digest = load_verified_chapter_contract(root, chapter_number)
    except ChapterContractError as exc:
        raise WorkflowError(str(exc)) from exc
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
    if task_json.exists() and task_markdown.exists() and not overwrite:
        if not manifest_file.exists():
            write_manifest(
                root,
                build_manifest(
                    root,
                    task_type="chapter_write",
                    chapter_number=chapter_number,
                    input_files=[task_markdown],
                    allowed_output_paths=[recommended_draft],
                    output_schema=output_protocol_for_task("chapter_write"),
                    validate_command=draft_submit_command(root, chapter_number, recommended_draft, default_agent),
                    apply_command=f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
                    failure_next_command="longform-engine production next project.yaml",
                    context_policy=chapter_write_context_policy(task_json, task_markdown),
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
    target_characters = int(length.get("chapter", {}).get("target_characters") or 3000)
    context_text = context_file.read_text(encoding="utf-8") if context_file.exists() else ""
    graph_summary = summarize_story_graph(story_graph)
    canon_research = load_recent_research_canon(root)
    style_context = load_style_context(root)
    tcs_path = root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json"
    tcs_payload = load_json(tcs_path, default={})
    if not isinstance(tcs_payload, dict):
        tcs_payload = {}
    character_expression_packet = build_character_expression_packet(
        root,
        chapter_number=chapter_number,
        card=card if isinstance(card, dict) else {},
        tcs=tcs_payload,
    )
    fanfiction_contract = load_fanfiction_writing_contract(
        config,
        root,
        card=card if isinstance(card, dict) else {},
        character_packet=character_expression_packet,
    )
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
    core_context_coverage = build_writing_core_context_coverage(
        root,
        card=card if isinstance(card, dict) else {},
        tcs=tcs_payload,
        character_packet=character_expression_packet,
        constraint_packet=constraint_packet,
    )
    try:
        resolved_contract_refs = resolve_chapter_contract_refs(root, chapter_contract)
    except ChapterContractError as exc:
        raise WorkflowError(str(exc)) from exc
    fact_inventory = build_chapter_fact_inventory(
        root,
        chapter_contract=chapter_contract,
        chapter_card_file=chapter_card_file,
        character_packet=character_expression_packet,
        constraint_packet=constraint_packet,
        writing_brief=writing_brief,
        craft_brief=craft_brief,
        fanfiction_contract=fanfiction_contract,
        feedback=feedback_carryover,
        resolved_contract_refs=resolved_contract_refs,
        core_context_coverage=core_context_coverage,
        humanizer_policy=humanizer_rules().get("two_pass_workflow", {}),
    )
    next_command = draft_submit_command(root, chapter_number, recommended_draft, default_agent)
    payload = {
        "schema": "chapter_writing_task_v2",
        "chapter_number": chapter_number,
        "title": card.get("title", f"第{chapter_number}章"),
        "status": "task_ready",
        "writing_mode": "agent_skill",
        "target_character_count": target_characters,
        "chapter_contract_hash": chapter_contract_digest,
        "writer_craft_brief": craft_brief,
        "fact_inventory_summary": summarize_fact_inventory(fact_inventory),
        "output_contract": {
            "format": "markdown_chapter_only",
            "target_character_count": target_characters,
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
                "遵守 Creative Brief、Writer Craft Brief 和 Humanizer v4 自查规则。",
            ],
        },
        "draft_submission_path": relative_path(root, recommended_draft),
        "next_command": next_command,
        "context_plan": chapter_write_context_plan(
            root,
            chapter_number,
            task_json,
            task_markdown,
            context_file,
            chapter_card_file,
            beat_sheet_file,
        ),
        "created_at": utc_now(),
        "_fact_inventory": fact_inventory,
    }
    payload["agent_task_manifest"] = relative_path(root, manifest_file)
    markdown = format_writing_task_markdown(root, payload)
    payload["context_plan"]["compiled_characters"] = len(markdown)
    contract = resolve_context_budget_contract(root)
    payload["context_plan"]["estimated_units"] = estimate_text_units(markdown, contract.estimator)
    payload["context_plan"]["budget_profile"] = contract.profile
    payload["context_plan"]["capacity_units"] = contract.capacity_units
    payload["context_plan"]["budget_status"] = (
        "advisory" if payload["context_plan"]["estimated_units"] > contract.input_soft_units
        else "within_soft_target"
    )
    markdown = format_writing_task_markdown(root, payload)
    payload["context_plan"]["estimated_units"] = estimate_text_units(markdown, contract.estimator)
    markdown = format_writing_task_markdown(root, payload)
    payload.pop("_fact_inventory", None)
    write_json(task_json, payload)
    atomic_write_text(task_markdown, markdown)
    manifest = build_manifest(
        root,
        task_type="chapter_write",
        chapter_number=chapter_number,
        input_files=[task_markdown],
        allowed_output_paths=[recommended_draft],
        output_schema=output_protocol_for_task("chapter_write"),
        validate_command=next_command,
        apply_command=f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
        failure_next_command="longform-engine production next project.yaml",
        context_policy=chapter_write_context_policy(task_json, task_markdown),
    )
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


def chapter_write_context_policy(
    task_json: Path,
    task_markdown: Path,
) -> dict[str, Any]:
    return {
        "required_files": [task_markdown],
        "optional_files": [],
        "compiled_brief": task_markdown,
        "selection_report": task_json,
    }


def chapter_write_context_plan(
    root: Path,
    chapter_number: int,
    task_json: Path,
    task_markdown: Path,
    context_file: Path,
    chapter_card_file: Path,
    beat_sheet_file: Path,
) -> dict[str, Any]:
    policy = chapter_write_context_policy(task_json, task_markdown)
    source_reasons = {
        context_file: "bounded RAG evidence embedded into the compiled brief",
        chapter_card_file: "chapter contract embedded into the compiled brief",
        beat_sheet_file: "scene-entry method compiled into the brief; chapter contract remains authoritative",
    }
    return {
        "schema": "writing_context_plan_v1",
        "required_files": [relative_path(root, path) for path in policy["required_files"]],
        "optional_files": [relative_path(root, path) for path in policy["optional_files"]],
        "forbidden_context": [
            "undeclared workbench drafts",
            "research inbox unless explicitly promoted and declared",
            "query cache and runtime database",
        ],
        "budget_mode": "adaptive",
        "overflow_policy": "split_context",
        "selection_reasons": {
            relative_path(root, task_markdown): "single compiled writable brief",
            **{relative_path(root, path): reason for path, reason in source_reasons.items()},
        },
        "source_catalog": [
            {
                "path": relative_path(root, path),
                "sha256": sha256_bytes(path.read_bytes()),
                "selection_reason": reason,
                "truncation_reason": (
                    "bounded RAG digest; raw source remains optional evidence"
                    if path == context_file and len(safe_read_text(path)) > 1_200
                    else "none"
                ),
            }
            for path, reason in source_reasons.items()
            if path.is_file()
        ],
        "excluded_duplicates": [
            relative_path(root, task_json),
            *[
                relative_path(root, path)
                for path in (
                    root / "10_bible" / "fanfiction" / "source_canon.json",
                    root / "10_bible" / "fanfiction" / "fanfiction_bible.json",
                )
                if path.is_file()
            ],
            *feedback_carryover_raw_sources(root, chapter_number),
        ],
        "source_characters": {
            relative_path(root, context_file): len(safe_read_text(context_file)),
            relative_path(root, chapter_card_file): len(safe_read_text(chapter_card_file)),
            relative_path(root, beat_sheet_file): len(safe_read_text(beat_sheet_file)),
        },
        "truncations": [
            {
                "source": relative_path(root, context_file),
                "embedded_characters": 1_200,
                "reason": "raw RAG remains optional; the compiled brief embeds only a bounded digest",
            }
        ] if len(safe_read_text(context_file)) > 1_200 else [],
    }


def compact_chapter_card(card: Any) -> dict[str, Any]:
    if not isinstance(card, dict):
        return {}
    fields = (
        "chapter_number",
        "title",
        "volume",
        "chapter_duty",
        "conflict",
        "information_release",
        "hook",
        "reader_gain",
        "cost",
        "platform_promise",
        "topology_id",
        "hook_mode",
        "promise_refs",
        "canon_refs",
        "divergence_effects",
        "voice_refs",
        "original_contribution",
        "protected_reveals",
        "requires_semantic_review",
        "forbidden",
        "forbidden_reveals",
        "requires_tail_suspense",
        "allowed_reveal_level",
        "ending_mode",
        "longline_impact",
        "foreshadow_impact",
        "relationship_impact",
        "plot_obligation",
        "dramatic_freedom",
        "pov_character_id",
        "featured_character_ids",
        "ability_refs",
        "characterization_focus",
        "scene_wants",
        "opposing_wants",
        "hidden_agenda",
        "relationship_move",
        "voice_state",
        "embodiment_strategy",
        "summary_scene_policy",
        "irreversible_action",
        "emotional_aftereffect",
        "direction_risks",
        "direction_selection",
        "effective_quality_contract",
    )
    return {field: card.get(field) for field in fields if field in card}


def compact_beat_sheet(beat: Any) -> dict[str, Any]:
    if not isinstance(beat, dict):
        return {}
    beats = []
    for raw in beat.get("beats") if isinstance(beat.get("beats"), list) else []:
        if not isinstance(raw, dict):
            continue
        beats.append(
            {
                key: raw.get(key)
                for key in ("order", "name", "scene_goal", "scene_purpose", "conflict", "turn", "hook", "purpose")
                if raw.get(key) not in (None, "")
            }
        )
    return {
        "chapter_number": beat.get("chapter_number"),
        "source_card": beat.get("source_card"),
        "beats": beats,
    }


def compact_tcs(tcs: Any) -> dict[str, Any]:
    if not isinstance(tcs, dict):
        return {}
    fields = (
        "current_characters",
        "locations",
        "emotion_state",
        "recent_events",
        "unresolved_conflicts",
        "open_foreshadows",
        "active_constraints",
    )
    return {field: tcs.get(field) for field in fields if field in tcs}


def compact_creative_brief(brief: Any) -> dict[str, Any]:
    if not isinstance(brief, dict):
        return {}
    fields = (
        "status",
        "target_audience",
        "writing_style",
        "target_scale",
        "reader_contract",
        "core_taboo",
        "design_decisions",
    )
    return {field: brief.get(field) for field in fields if field in brief}


def feedback_carryover_raw_sources(root: Path, chapter_number: int) -> list[str]:
    feedback = build_feedback_carryover(root, chapter_number)
    return [str(item) for item in feedback.get("source_files") or []]


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
            next_command = "longform-engine production next project.yaml"
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
            next_command = "longform-engine production next project.yaml"
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
    forecast = compile_length_forecast(config.data["length"])
    planned_start = int(start_chapter or last_finalized + 1)
    if planned_start <= 0:
        raise WorkflowError("start_chapter must be positive.")

    now = utc_now()
    state = {
        "schema_version": 2,
        "status": "planned",
        "mode": "agent_skill_scheduler",
        "target_characters": forecast.target_total_characters,
        "forecast_chapters": forecast.estimated_chapters,
        "support_status": forecast.support_status,
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
                    "next_command": "longform-engine production next project.yaml",
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
        forecast = compile_length_forecast(length)
        state = {
            "schema_version": 2,
            "status": "unplanned",
            "mode": "agent_skill_scheduler",
            "target_characters": forecast.target_total_characters,
            "forecast_chapters": forecast.estimated_chapters,
            "support_status": forecast.support_status,
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
    forecast = compile_length_forecast(length)
    state["target_characters"] = forecast.target_total_characters
    state["forecast_chapters"] = forecast.estimated_chapters
    state["support_status"] = forecast.support_status
    state.setdefault("chapters_attempted", 0)
    state.setdefault("failure_count", 0)
    state.setdefault("pause_reason", "")
    state.setdefault("next_command", "longform-engine auto-write run project.yaml")
    state["agent_task_status"] = auto_write_agent_task_status(root, int(state.get("current_chapter") or 0))
    return state


def auto_write_completed(config: ConfigDocument, root: Path, state: dict[str, Any]) -> bool:
    del root, state
    return fast_completion_marker(config)[0] == "approved"


def auto_write_blocker(root: Path, chapter_number: int) -> tuple[str, str, str, bool] | None:
    if existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None:
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
            "longform-engine production next project.yaml",
            True,
        )
    if isinstance(gate, dict) and (gate.get("passed") is True or gate_has_waiver(gate)):
        return (
            "awaiting_finalize",
            f"ch{chapter_number:03d} is gate-approved but not finalized.",
            f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
            False,
        )
    if existing_manuscript_chapter_path(root, chapter_number, lane="draft") is not None:
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
    "chapter_semantic": "awaiting_semantic_output",
    "pacing_review": "awaiting_semantic_output",
    "editorial_review": "awaiting_editorial_result",
}

AUTO_WRITE_TASK_WAIT_PRIORITY = {
    "chapter_write": 10,
    "repair": 20,
    "humanize": 21,
    "content_expand": 22,
    "pacing_review": 30,
    "chapter_semantic": 31,
    "editorial_review": 40,
}


def auto_write_agent_task_blocker(root: Path, chapter_number: int) -> tuple[str, str, str, bool] | None:
    waiting = auto_write_waiting_agent_tasks(root, chapter_number)
    if not waiting:
        return None
    task = waiting[0]
    task_type = str(task.get("task_type") or "agent_task")
    status = AUTO_WRITE_TASK_WAIT_STATUS.get(task_type, "awaiting_agent_output")
    commands = manifest_commands(task)
    next_command = str(commands.get("validate") or commands.get("apply") or commands.get("failure") or "")
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
    commands = manifest_commands(task)
    return {
        "task_id": str(task.get("task_id") or ""),
        "task_type": str(task.get("task_type") or ""),
        "status": str(task.get("status") or ""),
        "chapter_number": manifest_chapter_number(task),
        "manifest_file": str(task.get("manifest_file") or ""),
        "validate_command": str(commands.get("validate") or ""),
        "apply_command": str(commands.get("apply") or ""),
        "failure_next_command": str(commands.get("failure") or ""),
        "updated_at": str(task.get("updated_at") or ""),
    }


def auto_write_next_command_from_error(chapter_number: int, reason: str) -> str:
    reason_lower = reason.lower()
    previous = chapter_number - 1 if chapter_number > 1 else chapter_number
    if "no semantic ledger" in reason_lower:
        return f"longform-engine chapter semantic-task project.yaml --chapter {previous}"
    if "not closed" in reason_lower or "chapter close" in reason_lower:
        return f"longform-engine chapter close project.yaml --chapter {previous} --approved-by human"
    if "failed gate" in reason_lower:
        return "longform-engine production next project.yaml"
    if "not finalized" in reason_lower or "gate-approved" in reason_lower:
        return f"longform-engine chapter finalize project.yaml --chapter {previous} --approved-by human"
    if "stale" in reason_lower:
        return "longform-engine db rebuild project.yaml"
    return f"longform-engine continue-write project.yaml --chapter {chapter_number}"


def auto_write_result(root: Path, state: dict[str, Any], *, action: str, report_file: str, summary: str) -> AutoWriteResult:
    return AutoWriteResult(
        action=action,
        status=str(state.get("status") or "unknown"),
        state_file=str(auto_write_state_path(root)),
        report_file=report_file,
        forecast_chapters=int(state.get("forecast_chapters") or 0),
        target_characters=int(state.get("target_characters") or 0),
        current_chapter=int(state.get("current_chapter") or 0),
        last_finalized_chapter=int(state.get("last_finalized_chapter") or 0),
        chapters_attempted=int(state.get("chapters_attempted") or 0),
        failure_count=int(state.get("failure_count") or 0),
        pause_reason=str(state.get("pause_reason") or ""),
        next_command=str(state.get("next_command") or ""),
        summary=summary,
    )


def auto_write_summary(config: ConfigDocument, root: Path, state: dict[str, Any]) -> str:
    forecast_chapters = int(state.get("forecast_chapters") or 0)
    target_characters = int(state.get("target_characters") or 0)
    last_finalized = int(state.get("last_finalized_chapter") or 0)
    total_characters = total_final_characters(root)
    return (
        f"Auto-write {state.get('status', 'unknown')}: "
        f"finalized {last_finalized} chapters (forecast {forecast_chapters or '?'}), "
        f"{total_characters}/{target_characters or '?'} content characters, "
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
        f"- Forecast chapters: {state.get('forecast_chapters', 0)}",
        f"- Target content characters: {state.get('target_characters', 0)}",
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
    metrics = manuscript_metrics_projection(root)
    return max(state_last, int(metrics.get("latest_finalized_chapter") or 0))


def total_final_characters(root: Path) -> int:
    return int(manuscript_metrics_projection(root).get("total_content_characters") or 0)


def manuscript_metrics_projection(root: Path) -> dict[str, Any]:
    payload = load_json(root / "30_state" / "manuscript_metrics.json", default={})
    if not isinstance(payload, dict) or payload.get("schema") != "manuscript_metrics_v1":
        raise WorkflowError(
            "30_state/manuscript_metrics.json is missing or invalid; production routing "
            "does not scan the full manuscript as a fallback."
        )
    return payload


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


def normalize_agent_gate_result(
    gate_path: Path,
    passed: bool,
    next_command: str,
    *,
    semantic_review_pending: bool = False,
    candidate: dict[str, Any] | None = None,
) -> None:
    payload = load_json(gate_path, default={})
    if not isinstance(payload, dict):
        return
    actions = list(payload.get("allowed_actions") or [])
    if semantic_review_pending and "agent_semantic_review" not in actions:
        actions.append("agent_semantic_review")
    payload["allowed_actions"] = actions
    payload["next_command"] = next_command
    payload["workflow_stage"] = "reviews_pending"
    if candidate:
        payload["candidate"] = dict(candidate)
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
        "chapter_duty": card.get("chapter_duty") or "advance one clear longform promise",
        "plot_obligation": card.get("plot_obligation") or card.get("chapter_duty"),
        "dramatic_freedom": card.get("dramatic_freedom") or "choose scene action and subtext within canonical constraints",
        "pacing_tier": infer_task_pacing_tier(config, event_recommendation),
        "scene_entry": scene_entry,
        "chapter_hook": hook,
        "forbidden_reveals": forbidden_reveals,
        "do_not_resolve": resolution_markers or forbidden_reveals or ["core longform mystery", "main volume conflict"],
        "must_preserve_suspense": dedupe_strings(as_list(reverse_brake.get("must_preserve_suspense")) + as_list(outline_anchor.get("must_preserve_suspense")) + [hook]),
        "this_chapter_must_not_solve": as_list(reverse_brake.get("this_chapter_must_not_solve")),
        "must_keep_suspense": as_list(reverse_brake.get("must_keep_suspense")),
        "reverse_brake": reverse_brake,
        "quality_contract": compact_effective_quality_contract(
            card.get("effective_quality_contract")
            if isinstance(card.get("effective_quality_contract"), dict)
            else compile_effective_quality_contract(config, chapter_number=chapter_number)
        ),
        "beat_expansion_policy": {
            "expand_by_scene_material": True,
            "minimum_function_per_beat": "each beat must change pressure, knowledge, relationship, or risk",
            "no_padding": "do not add static exposition only to reach word count",
            "style_source": style_context.get("source", ""),
            "scene_contract": {
                "opposing_wants": as_list(card.get("opposing_wants")),
                "hidden_agenda": as_list(card.get("hidden_agenda")),
                "irreversible_action": str(card.get("irreversible_action") or ""),
                "emotional_aftereffect": str(card.get("emotional_aftereffect") or ""),
                "summary_scene_policy": str(card.get("summary_scene_policy") or ""),
            },
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
        raw_expansion = raw.get("expansion_requirements")
        expansion = dict(raw_expansion) if isinstance(raw_expansion, dict) else {
            "scene": "write concrete scene action",
            "dialogue": raw.get("dialogue_intent"),
            "psychology": "carry emotion through behavior before explanation",
            "action": "include a visible decision or consequence",
            "transition": "leave a changed problem for the next beat",
        }
        expansion.update(
            {
                "opposing_wants": as_list(card.get("opposing_wants")),
                "hidden_agenda": as_list(card.get("hidden_agenda")),
                "irreversible_action": str(card.get("irreversible_action") or ""),
                "emotional_aftereffect": str(card.get("emotional_aftereffect") or ""),
            }
        )
        requirements.append(
            {
                "order": raw.get("order"),
                "name": raw.get("name"),
                "scene_goal": raw.get("scene_goal") or raw.get("scene_purpose") or raw.get("purpose"),
                "conflict_point": raw.get("conflict_point") or raw.get("conflict") or card.get("conflict"),
                "information_release": raw.get("information_release") or raw.get("turn") or card.get("information_release"),
                "expansion_requirements": expansion,
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
    required_abilities = select_required_abilities(
        root,
        card=card,
        tcs_payload=tcs_payload,
        graph_constraints=graph_constraints,
    )
    active_foreshadows = dedupe_strings(
        as_list(card.get("promise_refs")) + as_list(tcs_payload.get("open_foreshadows"))
    )
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
        "required_abilities": required_abilities,
        "active_foreshadows": active_foreshadows,
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


def select_required_abilities(
    root: Path,
    *,
    card: dict[str, Any],
    tcs_payload: dict[str, Any],
    graph_constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    """Select every chapter-relevant ability or fail on an unresolved explicit reference."""

    abilities = load_json(root / "10_bible" / "abilities.json", default=[])
    records = [item for item in abilities if isinstance(item, dict)] if isinstance(abilities, list) else []
    by_id = {str(item.get("id")): item for item in records if str(item.get("id") or "").strip()}
    explicit = dedupe_strings(as_list(card.get("ability_refs")))
    unresolved = [item for item in explicit if item not in by_id]
    if unresolved:
        raise WorkflowError(
            "Writing context references unknown abilities: "
            + ", ".join(unresolved)
            + "; repair the chapter card or ability Bible before regenerating."
        )
    searchable = json.dumps(
        {
            "card": card,
            "active_constraints": tcs_payload.get("active_constraints") or [],
            "graph_constraints": graph_constraints,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    selected_ids = list(explicit)
    for item in records:
        ability_id = str(item.get("id") or "")
        name = str(item.get("name") or "")
        if ability_id and (ability_id in searchable or (name and name in searchable)):
            selected_ids.append(ability_id)
    selected_ids = dedupe_strings(selected_ids)
    if len(selected_ids) > 8:
        raise WorkflowError(
            "Writing context cannot fit all required abilities: "
            + ", ".join(selected_ids)
            + "; narrow the chapter ability focus before regenerating."
        )
    return [
        {
            key: by_id[ability_id].get(key)
            for key in ("id", "name", "summary", "cost", "limit", "limits", "constraints")
            if by_id[ability_id].get(key) not in (None, "", [])
        }
        for ability_id in selected_ids
    ]


def build_writing_core_context_coverage(
    root: Path,
    *,
    card: dict[str, Any],
    tcs: dict[str, Any],
    character_packet: dict[str, Any],
    constraint_packet: dict[str, Any],
) -> dict[str, Any]:
    """Prove that core character, relationship, ability, and foreshadow facts were not cut."""

    required_characters = dedupe_strings(
        [card.get("pov_character_id")]
        + as_list(card.get("featured_character_ids"))
    )
    represented_characters = dedupe_strings(
        as_list(character_packet.get("featured_character_ids"))
    )
    missing_characters = sorted(set(required_characters) - set(represented_characters))

    relationships = load_json(root / "10_bible" / "relationships.json", default=[])
    required_relationships = sorted(
        {
            str(item.get("id") or "")
            for item in relationships if isinstance(relationships, list) and isinstance(item, dict)
            if str(item.get("source_id") or "") in represented_characters
            and str(item.get("target_id") or "") in represented_characters
            and str(item.get("id") or "")
        }
    )
    represented_relationships = sorted(
        {
            str(relation.get("relationship_id") or "")
            for contract in character_packet.get("contracts") or [] if isinstance(contract, dict)
            for relation in contract.get("relationship_context") or [] if isinstance(relation, dict)
            if str(relation.get("relationship_id") or "")
        }
    )
    missing_relationships = sorted(set(required_relationships) - set(represented_relationships))

    required_abilities = [
        str(item.get("id") or "")
        for item in constraint_packet.get("required_abilities") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    ]
    active_foreshadows = dedupe_strings(constraint_packet.get("active_foreshadows") or [])
    declared_foreshadows = dedupe_strings(
        as_list(card.get("promise_refs")) + as_list(tcs.get("open_foreshadows"))
    )
    missing_foreshadows = sorted(set(declared_foreshadows) - set(active_foreshadows))
    if missing_characters or missing_relationships or missing_foreshadows:
        details = []
        if missing_characters:
            details.append("characters=" + ",".join(missing_characters))
        if missing_relationships:
            details.append("relationships=" + ",".join(missing_relationships))
        if missing_foreshadows:
            details.append("foreshadows=" + ",".join(missing_foreshadows))
        raise WorkflowError(
            "Writing core context coverage is incomplete ("
            + "; ".join(details)
            + "); revise the chapter card/context before regenerating."
        )
    source_paths = (
        root / "10_bible" / "characters.json",
        root / "10_bible" / "relationships.json",
        root / "10_bible" / "abilities.json",
        root / "30_state" / "tcs" / f"ch{int(card.get('chapter_number') or 0):03d}.json",
    )
    return {
        "schema": "writing_core_context_coverage_v1",
        "required_characters": required_characters,
        "represented_characters": represented_characters,
        "required_relationships": required_relationships,
        "represented_relationships": represented_relationships,
        "required_abilities": required_abilities,
        "active_foreshadows": active_foreshadows,
        "complete": True,
        "sources": [
            {
                "path": relative_path(root, path),
                "sha256": sha256_bytes(path.read_bytes()),
                "selection_reason": "core writing fact coverage",
                "truncation_reason": "none",
            }
            for path in source_paths
            if path.is_file()
        ],
    }


def build_chapter_fact_inventory(
    root: Path,
    *,
    chapter_contract: dict[str, Any],
    chapter_card_file: Path,
    character_packet: dict[str, Any],
    constraint_packet: dict[str, Any],
    writing_brief: dict[str, Any],
    craft_brief: dict[str, Any],
    fanfiction_contract: dict[str, Any],
    feedback: dict[str, Any],
    resolved_contract_refs: list[dict[str, Any]],
    core_context_coverage: dict[str, Any],
    humanizer_policy: Any,
) -> list[dict[str, Any]]:
    """Compile one de-duplicated in-memory fact inventory for the chapter author."""

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_values: set[str] = set()

    def add(
        fact_id: str,
        category: str,
        value: Any,
        *,
        source: str,
        source_hash: str = "",
        priority: str,
        reason: str,
    ) -> None:
        if value in (None, "", [], {}):
            return
        if fact_id in seen_ids:
            raise WorkflowError(f"chapter_fact_inventory_duplicate_id:{fact_id}")
        fingerprint = hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        if fingerprint in seen_values:
            return
        seen_ids.add(fact_id)
        seen_values.add(fingerprint)
        records.append(
            {
                "id": fact_id,
                "category": category,
                "value": value,
                "source": source,
                "source_hash": source_hash,
                "priority": priority,
                "selection_reason": reason,
            }
        )

    card_relative = relative_path(root, chapter_card_file)
    card_hash = sha256_bytes(chapter_card_file.read_bytes())
    add(
        "chapter.contract",
        "chapter_contract",
        chapter_contract,
        source=card_relative,
        source_hash=card_hash,
        priority="required",
        reason="the single approved chapter contract",
    )
    add(
        "chapter.stage_method",
        "methods",
        {
            "stage": writing_brief.get("stage"),
            "pacing_tier": writing_brief.get("pacing_tier"),
            "scene_entry": writing_brief.get("scene_entry"),
            "do_not_resolve": writing_brief.get("do_not_resolve"),
            "must_preserve_suspense": writing_brief.get("must_preserve_suspense"),
        },
        source=card_relative,
        source_hash=card_hash,
        priority="required",
        reason="current story phase and reveal boundary",
    )
    add(
        "cast.performance",
        "cast",
        {
            "pov_character_id": character_packet.get("pov_character_id"),
            "featured_character_ids": character_packet.get("featured_character_ids"),
            "narrative_expression_profile": character_packet.get("narrative_expression_profile"),
            "contracts": character_packet.get("contracts"),
            "approved_voice_samples": character_packet.get("approved_voice_samples"),
            "avoid_repetition": character_packet.get("avoid_repetition"),
        },
        source=str(character_packet.get("source") or "10_bible/character_expression.json"),
        source_hash=source_hash(root, str(character_packet.get("source") or "10_bible/character_expression.json")),
        priority="required",
        reason="only the POV and featured cast expression contracts",
    )
    for index, item in enumerate(resolved_contract_refs, start=1):
        add(
            f"hard_ref.{index:02d}.{item['ref']}",
            "hard_rules",
            {"kind": item["kind"], "ref": item["ref"], "value": item["value"]},
            source=str(item["source"]),
            source_hash=str(item["sha256"]),
            priority="required",
            reason="explicit stable ref declared by the chapter contract",
        )
    add(
        "hard_rules.abilities",
        "hard_rules",
        constraint_packet.get("required_abilities"),
        source="10_bible/abilities.json",
        source_hash=source_hash(root, "10_bible/abilities.json"),
        priority="required",
        reason="chapter-relevant ability limits and costs",
    )
    add(
        "hard_rules.graph_constraints",
        "hard_rules",
        (constraint_packet.get("story_graph") or {}).get("constraints"),
        source="30_state/story_graph.json",
        source_hash=source_hash(root, "30_state/story_graph.json"),
        priority="required",
        reason="current graph constraints selected for this chapter",
    )
    if fanfiction_contract.get("enabled"):
        add(
            "hard_rules.fanfiction",
            "hard_rules",
            {
                key: fanfiction_contract.get(key)
                for key in (
                    "continuity_mode",
                    "canon_cutoff",
                    "divergence_point",
                    "ooc_tolerance",
                    "voice_contracts",
                    "world_rule_changes",
                    "butterfly_effects",
                    "protected_reveals",
                )
                if fanfiction_contract.get(key) not in (None, "", [], {})
            },
            source=str(fanfiction_contract.get("design_path") or "10_bible/fanfiction/fanfiction_bible.json"),
            source_hash=source_hash(root, str(fanfiction_contract.get("design_path") or "")),
            priority="required",
            reason="declared divergence and OOC boundaries",
        )
    for fact_id, value, source, reason in (
        (
            "history.rag",
            (constraint_packet.get("rag") or {}).get("summary"),
            str((constraint_packet.get("rag") or {}).get("source") or ""),
            "retrieved final-text evidence for current decisions",
        ),
        (
            "history.graph",
            (constraint_packet.get("story_graph") or {}).get("facts"),
            "30_state/story_graph.json",
            "current materialized relation and event facts",
        ),
        (
            "history.tcs",
            constraint_packet.get("tcs"),
            str((constraint_packet.get("tcs") or {}).get("source") or ""),
            "current temporal context only",
        ),
        (
            "history.character_memory",
            constraint_packet.get("character_memory"),
            "30_state/character_memory.json",
            "current character state and unresolved commitments",
        ),
        (
            "history.research",
            constraint_packet.get("research_canon"),
            "10_bible/research_canon.jsonl",
            "promoted research facts only",
        ),
    ):
        add(
            fact_id,
            "historical_evidence",
            value,
            source=source,
            source_hash=source_hash(root, source),
            priority="evidence",
            reason=reason,
        )
    add(
        "feedback.unresolved",
        "feedback",
        (feedback.get("items") or [])[:5],
        source=str(feedback.get("source") or "controlled feedback carryover"),
        priority="feedback",
        reason="only unresolved findings carried from prior validated reports",
    )
    add(
        "methods.quality",
        "methods",
        writing_brief.get("quality_contract"),
        source="compiled effective quality contract",
        priority="method",
        reason="primary market, active facets, and current story phase",
    )
    add(
        "methods.craft",
        "methods",
        craft_brief,
        source="selected role and playbook sections",
        priority="method",
        reason="task-specific craft methods only",
    )
    add(
        "methods.humanizer_self_check",
        "methods",
        humanizer_policy,
        source="config/humanizer_rules",
        priority="method",
        reason="final expression self-check without changing story facts",
    )
    add(
        "coverage.core",
        "provenance",
        core_context_coverage,
        source="CLI context compiler",
        priority="required",
        reason="proof that required cast, relations, abilities, and foreshadows were retained",
    )
    return records


def source_hash(root: Path, relative: str) -> str:
    if not relative:
        return ""
    path = root / relative
    return sha256_bytes(path.read_bytes()) if path.is_file() else ""


def summarize_fact_inventory(records: list[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    for record in records:
        category = str(record.get("category") or "unknown")
        categories[category] = categories.get(category, 0) + 1
    return {
        "schema": "chapter_fact_inventory_summary_v1",
        "fact_count": len(records),
        "categories": categories,
        "fact_ids": [str(record.get("id") or "") for record in records],
    }


def chapter_stage(config: ConfigDocument, chapter_number: int) -> dict[str, Any]:
    forecast = compile_length_forecast(config.data["length"])
    ratio = min(
        1.0,
        max(0.0, total_final_characters(resolve_project_root(config)) / max(1, forecast.target_total_characters)),
    )
    label = infer_story_phase(config, chapter_number)
    strategies = {
        "opening": "establish world pressure, immediate objective, longline motive, and a consequential choice",
        "early_serial": "prove the core promise can recur while relationships and costs accumulate",
        "stable_serial": "rotate active engines and turn prior promises into new consequences",
        "volume_climax": "pay off the approved volume promise and preserve causally earned continuation",
        "aftermath": "let consequences alter character state, relationships, goals, or reader knowledge",
    }
    return {
        "label": label,
        "chapter_number": chapter_number,
        "target_total_characters": forecast.target_total_characters,
        "forecast_chapters": forecast.estimated_chapters,
        "progress_ratio": round(ratio, 4),
        "strategy": strategies[label],
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


def trim_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max(0, max_chars - 3)].rstrip() + "..."


def format_writing_task_markdown(root: Path, payload: dict[str, Any]) -> str:
    """Render the single compiled brief; raw sources remain optional evidence only."""

    inventory = payload.get("_fact_inventory")
    if isinstance(inventory, list):
        return render_fact_inventory_markdown(payload, inventory)

    card = payload.get("chapter_card", {}).get("data", {})
    writing = payload.get("writing_brief") if isinstance(payload.get("writing_brief"), dict) else {}
    stage = writing.get("stage") if isinstance(writing.get("stage"), dict) else {}
    reverse = writing.get("reverse_brake") if isinstance(writing.get("reverse_brake"), dict) else {}
    constraints = payload.get("constraint_packet") if isinstance(payload.get("constraint_packet"), dict) else {}
    tcs = constraints.get("tcs") if isinstance(constraints.get("tcs"), dict) else {}
    graph = constraints.get("story_graph") if isinstance(constraints.get("story_graph"), dict) else {}
    memory = constraints.get("character_memory") if isinstance(constraints.get("character_memory"), dict) else {}
    events = constraints.get("event_matrix") if isinstance(constraints.get("event_matrix"), dict) else {}
    craft = payload.get("writer_craft_brief") if isinstance(payload.get("writer_craft_brief"), dict) else {}
    feedback = payload.get("feedback_carryover") if isinstance(payload.get("feedback_carryover"), dict) else {}
    context_plan = payload.get("context_plan") if isinstance(payload.get("context_plan"), dict) else {}
    output = payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {}
    fanfiction = payload.get("fanfiction_contract") if isinstance(payload.get("fanfiction_contract"), dict) else {}
    character_packet = (
        payload.get("character_expression_packet")
        if isinstance(payload.get("character_expression_packet"), dict)
        else {}
    )
    quality_contract = writing.get("quality_contract") if isinstance(writing.get("quality_contract"), dict) else {}
    character_contracts_text = json.dumps(character_packet.get("contracts", []), ensure_ascii=False)
    voice_samples_text = json.dumps(character_packet.get("approved_voice_samples", []), ensure_ascii=False)
    compatibility_observations = [
        item
        for item in quality_contract.get("compatibility_observations", [])
        if isinstance(item, dict)
    ][:3]
    lines = [
        f"# Writing Task ch{int(payload['chapter_number']):03d}",
        "",
        f"- Title: {payload.get('title', '')}",
        f"- Target content characters: {payload.get('target_character_count', '')}",
        f"- Write only: `{payload.get('draft_submission_path', '')}`",
        f"- Validate: `{payload.get('next_command', '')}`",
        "",
        "## Context Selection",
        "",
        f"- Required: {', '.join(f'`{item}`' for item in context_plan.get('required_files', [])) or 'none'}",
        f"- Optional evidence: {', '.join(f'`{item}`' for item in context_plan.get('optional_files', [])) or 'none'}",
        f"- Budget: adaptive `{context_plan.get('budget_profile', 'standard')}` profile; "
        f"estimated `{context_plan.get('estimated_units', 0)}` engine units",
        "- Do not scan the project. Do not read excluded duplicates or undeclared drafts/inbox/runtime data.",
        "",
        "## Writable Brief",
        "",
        f"- Stage: {stage.get('label', '')}; strategy: {stage.get('strategy', '')}",
        f"- Chapter duty: {writing.get('chapter_duty') or card.get('chapter_duty', '')}",
        f"- Plot obligation: {card.get('plot_obligation', '')}",
        f"- Dramatic freedom: {card.get('dramatic_freedom', '')}",
        f"- Conflict: {card.get('conflict', '')}",
        f"- Information release: {card.get('information_release', '')}",
        f"- Reader gain: {card.get('reader_gain', '')}",
        f"- Cost: {card.get('cost', '')}",
        f"- Platform promise: {card.get('platform_promise', '')}",
        f"- Chapter topology: {card.get('topology_id', '')}",
        f"- Pacing tier: {writing.get('pacing_tier', '')}",
        f"- Scene entry: {json.dumps(writing.get('scene_entry', {}), ensure_ascii=False)}",
        f"- Ending hook: {writing.get('chapter_hook') or card.get('hook', '')}",
        "",
        "## Effective Quality Contract",
        "",
        f"- Profile: {quality_contract.get('primary_market') or quality_contract.get('market', '')} + "
        f"{', '.join(str(item.get('kind')) + ':' + str(item.get('id')) for item in quality_contract.get('active_facets', []))} + "
        f"{quality_contract.get('phase', '')}",
        f"- Strictness: {quality_contract.get('strictness', '')}",
        f"- Contract: {trim_text(json.dumps(quality_contract.get('contract', {}), ensure_ascii=False), 2200)}",
        f"- Human-approved baseline: {json.dumps(quality_contract.get('approved_style_baseline', {}), ensure_ascii=False)}",
        f"- Blocking policy: {json.dumps(quality_contract.get('blocking_policy', {}), ensure_ascii=False)}",
        *[
            f"- Compatibility advisory only [{item.get('market', '')}/{item.get('code', '')}]: {item.get('message', '')}"
            for item in compatibility_observations
        ],
        "- Treat this as a flexible quality boundary, not a fixed sentence, dialogue, pace, or cliffhanger template.",
        "- Compatibility observations are P2 guidance only and cannot justify changing canon or forcing repair.",
        "",
        "## Reverse Brake",
        "",
        f"- Allowed reveal: {reverse.get('allowed_reveal_level', '')}",
        f"- Do not resolve: {', '.join(as_list(writing.get('do_not_resolve'))) or 'none'}",
        f"- Forbidden reveals: {', '.join(as_list(writing.get('forbidden_reveals'))) or 'none'}",
        f"- Preserve suspense: {', '.join(as_list(writing.get('must_preserve_suspense'))) or 'none'}",
        f"- Tail suspense required: {reverse.get('requires_tail_suspense', False)}",
        "",
        "## Character Performance Packet",
        "",
        f"- POV: {character_packet.get('pov_character_id', '')}",
        f"- Featured: {', '.join(as_list(character_packet.get('featured_character_ids'))) or 'none declared'}",
        f"- Characterization focus: {', '.join(as_list(character_packet.get('characterization_focus'))) or 'derive from scene pressure'}",
        f"- Relationship move: {character_packet.get('relationship_move', '') or 'preserve or change only through visible cause'}",
        f"- Embodiment: {character_packet.get('embodiment_strategy', '')}",
        f"- Summary/scene policy: {character_packet.get('summary_scene_policy', '')}",
        f"- Expression profile: {json.dumps(character_packet.get('narrative_expression_profile', {}), ensure_ascii=False)}",
        f"- Character contracts: {character_contracts_text}",
        f"- Approved samples (reference, never copy mechanically): {voice_samples_text}",
        f"- Avoid repeated leakage/gesture: {', '.join(as_list(character_packet.get('avoid_repetition'))) or 'none recorded'}",
        "- Distinguish characters by what they notice, conceal, demand, avoid, and physically do; dialogue volume alone is not characterization.",
        "",
        "## Beat Expansion Requirements",
        "",
    ]
    beats = payload.get("beat_expansion_requirements") if isinstance(payload.get("beat_expansion_requirements"), list) else []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        lines.extend(
            [
                f"### Beat {beat.get('order')}: {beat.get('name')}",
                f"- Goal: {beat.get('scene_goal', '')}",
                f"- Pressure: {beat.get('conflict_point', '')}",
                f"- Change: {beat.get('information_release', '')}",
                f"- Material: {json.dumps(beat.get('expansion_requirements', {}), ensure_ascii=False)}",
                f"- Avoid: {', '.join(as_list(beat.get('avoid_repetition'))) or 'none'}",
                "",
            ]
        )
    if not beats:
        lines.extend(["- No beat requirements available.", ""])
    if fanfiction.get("enabled"):
        lines.extend(
            [
                "## Fanfiction Contract",
                "",
                f"- Continuity mode: {fanfiction.get('continuity_mode', '')}",
                f"- Canon cutoff: {fanfiction.get('canon_cutoff', '')}",
                f"- Divergence point: {fanfiction.get('divergence_point', '')}",
                f"- OOC tolerance: {fanfiction.get('ooc_tolerance', '')}",
                f"- Canon references: {json.dumps(card.get('canon_refs', []), ensure_ascii=False)}",
                f"- Voice references: {json.dumps(card.get('voice_refs', []), ensure_ascii=False)}",
                f"- Declared divergence effects: {json.dumps(card.get('divergence_effects', []), ensure_ascii=False)}",
                f"- Original contribution: {card.get('original_contribution', '')}",
                f"- Character voice contracts: {json.dumps(fanfiction.get('voice_contracts', []), ensure_ascii=False)}",
                f"- World rule changes: {json.dumps(fanfiction.get('world_rule_changes', []), ensure_ascii=False)}",
                "- Canon differences are allowed when supported by the declared divergence and its consequences.",
                "- Preserve original character agency. Do not make all canon characters irrational or subordinate to one new lead.",
                "- Names, relationships, abilities, and world terms are allowed; do not reproduce continuous source prose.",
                "",
            ]
        )
    lines.extend(
        [
            "## Core Context Coverage",
            "",
            f"- Complete: {bool((payload.get('core_context_coverage') or {}).get('complete'))}",
            f"- Provenance: {json.dumps((payload.get('core_context_coverage') or {}).get('sources', []), ensure_ascii=False)}",
            "- If any required character, relationship, ability, or active foreshadow is missing, stop instead of inventing or silently dropping it.",
            "",
            "## Constraint Packet",
            "",
            f"- RAG digest: {trim_text(str((constraints.get('rag') or {}).get('summary', '')), 700)}",
            f"- Research canon: {trim_text(json.dumps(constraints.get('research_canon', [])[:3], ensure_ascii=False), 700)}",
            f"- Canonical graph facts: {json.dumps(graph.get('facts', []), ensure_ascii=False)}",
            f"- Graph constraints: {json.dumps(graph.get('constraints', {}), ensure_ascii=False)}",
            f"- TCS: {json.dumps(tcs, ensure_ascii=False)}",
            f"- Character memory: {json.dumps(memory, ensure_ascii=False)}",
            f"- Required abilities: {json.dumps(constraints.get('required_abilities', []), ensure_ascii=False)}",
            f"- Active foreshadows: {json.dumps(constraints.get('active_foreshadows', []), ensure_ascii=False)}",
            f"- Event recommendation: {json.dumps(events, ensure_ascii=False)}",
            f"- Style: {json.dumps(compact_style_context(constraints.get('style_profile')), ensure_ascii=False)}",
            "",
            "## Craft And Voice",
            "",
            f"- Dialogue: {', '.join(as_list(craft.get('dialogue_strategy'))) or 'use differentiated subtext'}",
            f"- Scene texture: {', '.join(as_list(craft.get('scene_texture'))) or 'concrete action and sensory cost'}",
            f"- Avoid AI voice: {', '.join(as_list(craft.get('ai_voice_forbidden_zone'))) or 'summary lecture and template phrasing'}",
            "",
            "## Feedback Carryover",
            "",
        ]
    )
    feedback_items = feedback.get("items") if isinstance(feedback.get("items"), list) else []
    if feedback_items:
        for item in feedback_items[:5]:
            if isinstance(item, dict):
                lines.append(
                    f"- [{item.get('severity', 'P2')}] {item.get('issue_code') or item.get('kind', 'feedback')}: "
                    f"{trim_text(str(item.get('summary', '')), 360)}"
                )
    else:
        lines.append("- No previous feedback. Establish the contract cleanly.")
    lines.extend(
        [
            "",
            "## Output Contract",
            "",
            *[f"- {item}" for item in output.get("must_follow", [])],
            *[f"- Must not include: {item}" for item in output.get("must_not_include", [])],
            "- Do not directly write final, RAG, graph, TCS, Bible, outline, research canon, or SQLite.",
            "- After writing the declared draft, run the validate command. Finalize requires explicit human approval.",
            "",
        ]
    )
    return "\n".join(lines)


def render_fact_inventory_markdown(payload: dict[str, Any], inventory: list[dict[str, Any]]) -> str:
    by_category: dict[str, list[dict[str, Any]]] = {}
    for record in inventory:
        if isinstance(record, dict):
            by_category.setdefault(str(record.get("category") or "other"), []).append(record)
    context_plan = payload.get("context_plan") if isinstance(payload.get("context_plan"), dict) else {}
    output = payload.get("output_contract") if isinstance(payload.get("output_contract"), dict) else {}
    lines = [
        f"# 第 {int(payload['chapter_number']):03d} 章写作工作单",
        "",
        f"- 标题：{payload.get('title', '')}",
        f"- 目标正文字符：{payload.get('target_character_count', '')}",
        f"- 唯一写入路径：`{payload.get('draft_submission_path', '')}`",
        f"- 章节合同 hash：`{payload.get('chapter_contract_hash', '')}`",
        f"- 校验命令：`{payload.get('next_command', '')}`",
        "",
        "## 上下文边界",
        "",
        f"- 只读：{', '.join(f'`{item}`' for item in context_plan.get('required_files', [])) or 'none'}",
        f"- 自适应预算：`{context_plan.get('budget_profile', 'standard')}`；预估 `{context_plan.get('estimated_units', 0)}` engine units。",
        "- 不扫描项目，不读取未声明草稿、research inbox、runtime DB 或其他审稿结果。",
        "- 下列事实已由 CLI 去重；同一事实只按其稳定 ID 执行一次。",
        "",
    ]
    headings = (
        ("chapter_contract", "唯一章节合同"),
        ("cast", "登场人物与声音"),
        ("hard_rules", "硬规则与受保护事实"),
        ("historical_evidence", "历史证据"),
        ("feedback", "未解决反馈"),
        ("methods", "当前写作方法"),
        ("provenance", "上下文完整性"),
    )
    for category, heading in headings:
        records = by_category.get(category, [])
        if not records:
            continue
        lines.extend([f"## {heading}", ""])
        for record in records:
            value = record.get("value")
            source = str(record.get("source") or "")
            source_note = f"；来源 `{source}`" if source else ""
            lines.append(
                f"- `{record.get('id', '')}`（{record.get('selection_reason', '')}{source_note}）："
                f"{json.dumps(value, ensure_ascii=False, separators=(',', ':')) if not isinstance(value, str) else value}"
            )
        lines.append("")
    lines.extend(
        [
            "## 输出与交接",
            "",
            *[f"- {item}" for item in output.get("must_follow", [])],
            *[f"- 禁止包含：{item}" for item in output.get("must_not_include", [])],
            "- 只写完整章节正文，不写提纲、分析、创作说明或自我评价。",
            "- 不直接写 final、Bible、outline、graph、RAG、TCS 或 SQLite。",
            f"- 写完运行：`{payload.get('next_command', '')}`",
            "",
        ]
    )
    return "\n".join(lines)


def load_fanfiction_writing_contract(
    config: ConfigDocument,
    root: Path,
    *,
    card: dict[str, Any] | None = None,
    character_packet: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if str(config.data.get("creation", {}).get("mode") or "original") != "fanfiction":
        return {"enabled": False}
    design_path = root / "10_bible" / "fanfiction" / "fanfiction_bible.json"
    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    design = load_json(design_path, default={})
    canon = load_json(canon_path, default={})
    card = card if isinstance(card, dict) else {}
    character_packet = character_packet if isinstance(character_packet, dict) else {}
    relevant_ids = {
        str(item)
        for item in (
            as_list(card.get("featured_character_ids"))
            + as_list(card.get("canon_refs"))
            + as_list(card.get("voice_refs"))
            + as_list(character_packet.get("featured_character_ids"))
        )
        if str(item).strip()
    }
    narrative_role_fields = {
        key: card.get(key)
        for key in (
            "title",
            "chapter_duty",
            "conflict",
            "information_release",
            "hook",
            "reader_gain",
            "plot_obligation",
            "scene_wants",
            "opposing_wants",
            "hidden_agenda",
            "relationship_move",
        )
        if card.get(key) not in (None, "", [], {})
    }
    card_text = json.dumps(narrative_role_fields, ensure_ascii=False).casefold()
    source_summaries: list[dict[str, Any]] = []
    if isinstance(canon, dict):
        for source in canon.get("sources") or []:
            if not isinstance(source, dict):
                continue
            for character in source.get("characters") or []:
                if not isinstance(character, dict):
                    continue
                character_id = str(character.get("id") or "")
                names = [
                    part.strip().casefold()
                    for part in re.split(r"[/／|]", str(character.get("name") or ""))
                    if part.strip()
                ]
                if any(name in card_text for name in names):
                    relevant_ids.add(character_id)
            source_summaries.append(
                {
                    "source_id": source.get("source_id"),
                    "title": source.get("title"),
                    "canon_cutoff": source.get("canon_cutoff"),
                    "character_ids": [
                        item.get("id")
                        for item in source.get("characters") or []
                        if isinstance(item, dict) and item.get("id")
                    ][:12],
                    "unresolved_questions": [
                        trim_text(str(item.get("summary") or ""), 120)
                        for item in source.get("unresolved_questions") or []
                        if isinstance(item, dict)
                    ][:5],
                }
            )
    return {
        "enabled": True,
        "source_canon_path": relative_path(root, canon_path),
        "design_path": relative_path(root, design_path),
        "continuity_mode": design.get("continuity_mode") if isinstance(design, dict) else "",
        "canon_cutoff": design.get("canon_cutoff") if isinstance(design, dict) else "",
        "divergence_point": design.get("divergence_point") if isinstance(design, dict) else "",
        "ooc_tolerance": design.get("ooc_tolerance") if isinstance(design, dict) else "",
        "voice_contracts": [
            item
            for item in (design.get("character_voice_contracts") or [])
            if isinstance(item, dict) and str(item.get("character_id") or "") in relevant_ids
        ][:8] if isinstance(design, dict) else [],
        "world_rule_changes": (design.get("world_rule_changes") or [])[:8] if isinstance(design, dict) else [],
        "butterfly_effects": (design.get("butterfly_effects") or [])[:8] if isinstance(design, dict) else [],
        "protected_reveals": (design.get("protected_reveals") or [])[:8] if isinstance(design, dict) else [],
        "sources": source_summaries,
    }


def compact_style_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "source": value.get("source", ""),
        "notes": trim_text(str(value.get("notes") or ""), 240),
        "fingerprint": value.get("fingerprint") if isinstance(value.get("fingerprint"), dict) else {},
    }


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
            "schema": "quality_feedback_carryover_v1",
            "status": "none",
            "source_chapter": 0,
            "source_files": [],
            "registry_file": "50_workbench/quality_feedback/registry.jsonl",
            "items": [],
            "notes": ["No previous chapter feedback is available for the first chapter."],
            "hard_boundary": "feedback is guidance only; it does not mutate final/RAG/graph/TCS/SQLite",
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
        gate_passed = gate_payload.get("passed") is True
        if gate_passed:
            failures = []
            ignored_warning_markers = (
                "gate failed; story graph must remain frozen",
                "draft is not final; semantic materialization waits for chapter finalize",
                "previous finalized chapter is",
            )
            warnings = [
                warning
                for warning in warnings
                if not any(marker in str(warning).lower() for marker in ignored_warning_markers)
            ]
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

    repair_plan_dir = root / "50_workbench" / "repair_plans" / f"ch{source_chapter:03d}"
    for validation_file in sorted(repair_plan_dir.glob("r*.validation.json"), reverse=True):
        validation = load_json(validation_file, default={})
        round_token = validation_file.name.split(".", 1)[0]
        repair_file = repair_plan_dir / f"{round_token}.plan.md"
        if not (
            isinstance(validation, dict)
            and validation.get("ok") is True
            and repair_file.is_file()
        ):
            continue
        source_files.append(relative_path(root, repair_file))
        items.append(
            {
                "kind": "validated_repair_plan",
                "source": relative_path(root, repair_file),
                "summary": trim_text(safe_read_text(repair_file), 700),
            }
        )
        break

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

    registry_warning = ""
    try:
        target_card = load_json(
            root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
            default={},
        )
        chapter_role = (
            str(target_card.get("chapter_duty") or "")
            if isinstance(target_card, dict)
            else ""
        )
        managed_items = list(
            carry_feedback(
                root,
                target_chapter=chapter_number,
                task_type="chapter_write",
                chapter_role=chapter_role,
                limit=5,
            )
        )
    except (OSError, ValueError) as exc:
        registry_warning = str(exc)
        managed_items = [
            {
                **item,
                "schema": "quality_feedback_fallback_v1",
                "feedback_id": (
                    f"fallback:{item.get('kind', 'quality')}:ch{source_chapter:03d}:{index:02d}"
                ),
                "status": "open",
            }
            for index, item in enumerate(items[:5], start=1)
        ]

    notes = [
        "Use this feedback to avoid repeating prior gate, pacing, humanizer, or editorial issues.",
        "At most five active, task-relevant registry items are carried; resolved/suppressed/expired items are omitted.",
        "Feedback is guidance only; official state still changes only through validate/apply/finalize commands.",
    ]
    if registry_warning:
        notes.append(
            f"Feedback registry warning; bounded artifact fallback was used without blocking production: {registry_warning}"
        )
    return {
        "schema": "quality_feedback_carryover_v1",
        "status": "available" if managed_items else "empty",
        "source_chapter": source_chapter,
        "source_files": dedupe_strings(source_files),
        "registry_file": "50_workbench/quality_feedback/registry.jsonl",
        "items": managed_items,
        "notes": notes,
        "hard_boundary": "feedback is guidance only; it does not mutate final/RAG/graph/TCS/SQLite",
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


def dedupe_strings(items: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        normalized = item.strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def simple_style_fingerprint(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    sentences = [part for part in re.split(r"[.!?。！？]+", text) if part.strip()]
    length_sum = sum(len(re.sub(r"\s+", "", item)) for item in sentences)
    expression = character_expression_diagnostics(text)
    return {
        "paragraphs": len(paragraphs),
        "sentences": len(sentences),
        "avg_sentence_chars": round(length_sum / max(1, len(sentences)), 2),
        "dialogue_ratio": expression["dialogue_char_ratio"],
        "dialogue_char_ratio": expression["dialogue_char_ratio"],
        "dialogue_mark_density": expression["dialogue_mark_density"],
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
    draft_path = manuscript_chapter_path(root, chapter_number, lane="draft")
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
    if existing_manuscript_chapter_path(root, previous, lane="final") is not None:
        closure = root / "30_state" / "chapter_closures" / f"ch{previous:03d}.json"
        if closure.exists():
            return
        ledger = root / "30_state" / "semantic_ledger" / f"ch{previous:03d}.json"
        if not ledger.exists():
            raise WorkflowError(
                f"Previous chapter ch{previous:03d} is finalized but has no semantic ledger; run "
                f"longform-engine chapter semantic-task project.yaml --chapter {previous}."
            )
        raise WorkflowError(
            f"Previous chapter ch{previous:03d} is not closed; run longform-engine chapter close "
            f"project.yaml --chapter {previous} --approved-by human."
        )

    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{previous:03d}" / "gate_result.json"
    payload = load_json(gate_path, default={}) if gate_path.exists() else {}
    if isinstance(payload, dict) and payload.get("passed") is False and not gate_has_waiver(payload):
        raise WorkflowError(
            f"Previous chapter ch{previous:03d} has not completed its review-and-repair workflow; "
            "run production next before continue-write."
        )
    if isinstance(payload, dict) and (payload.get("passed") is True or gate_has_waiver(payload)):
        raise WorkflowError(f"Previous chapter ch{previous:03d} is gate-approved but not finalized; run chapter finalize before continue-write.")
    if existing_manuscript_chapter_path(root, previous, lane="draft") is not None:
        raise WorkflowError(f"Previous chapter ch{previous:03d} has a draft but is not finalized; run draft submit and chapter finalize before continue-write.")
    raise WorkflowError(f"Previous chapter ch{previous:03d} is not finalized; finish it before continue-write.")


def resolve_confirmations(config: ConfigDocument, provided: dict[str, Any]) -> dict[str, Any]:
    novel = config.data.get("novel", {})
    length = config.data.get("length", {})
    writing = config.data.get("writing", {})
    agent = writing.get("agent", {}) if isinstance(writing, dict) else {}
    story_profile = config.data.get("story_profile", {})
    setting = story_profile.get("setting") if isinstance(story_profile, dict) and isinstance(story_profile.get("setting"), dict) else {}
    engines = story_profile.get("plot_engines") if isinstance(story_profile, dict) and isinstance(story_profile.get("plot_engines"), dict) else {}
    narrative_forms = story_profile.get("narrative_forms", []) if isinstance(story_profile, dict) else []
    tones = story_profile.get("tone", []) if isinstance(story_profile, dict) else []
    selected_style = [
        str(setting.get("primary") or ""),
        str(engines.get("primary") or ""),
        *[str(item) for item in narrative_forms],
        *[str(item) for item in tones],
    ]
    style = provided.get("writing_style") or novel.get("style") or ", ".join(item for item in selected_style if item)
    if isinstance(style, list):
        style = ", ".join(str(item) for item in style)
    forbidden = provided.get("core_forbidden_zone") or novel.get("forbidden_experience")
    forecast = compile_length_forecast(length)
    target_scale = provided.get("target_scale") or (
        f"{forecast.target_total_characters} content characters / "
        f"about {forecast.estimated_chapters} chapters / {forecast.support_status}"
    )
    resolved = {
        "target_audience": provided.get("target_audience") or novel.get("audience"),
        "writing_style": style,
        "core_forbidden_zone": as_list(forbidden),
        "automation_level": provided.get("automation_level") or agent.get("default_workflow") or "command_driven",
        "target_scale": target_scale,
    }
    missing = [key for key, value in resolved.items() if not value]
    if missing:
        raise WorkflowError(f"open-book missing required confirmations: {', '.join(missing)}")
    if not resolved["core_forbidden_zone"]:
        raise WorkflowError("open-book missing required confirmations: core_forbidden_zone")
    return resolved


def infer_volume(config: ConfigDocument, chapter_number: int) -> int:
    root = resolve_project_root(config)
    plan = load_json(root / "20_outline" / "chapter_plan.json", default=[])
    row = next(
        (
            item for item in plan
            if isinstance(plan, list) and isinstance(item, dict)
            and int(item.get("chapter_number") or 0) == chapter_number
        ),
        {},
    )
    volume_id = str(row.get("volume_id") or "") if isinstance(row, dict) else ""
    volumes = load_json(root / "20_outline" / "volumes.json", default=[])
    for index, volume in enumerate(volumes if isinstance(volumes, list) else [], start=1):
        if isinstance(volume, dict) and str(volume.get("id") or "") == volume_id:
            return int(volume.get("number") or index)
    length = config.data["length"]
    per_volume = max(
        1,
        round(int(length["volume"]["target_characters"]) / int(length["chapter"]["target_characters"])),
    )
    return ((chapter_number - 1) // per_volume) + 1


def chapter_book_goal(root: Path) -> str:
    brief = load_json(root / "10_bible" / "creative_brief.json", default={})
    if isinstance(brief, dict):
        decisions = brief.get("design_decisions")
        if isinstance(decisions, dict):
            for key in ("long_conflict", "core_hook", "ending_boundary"):
                value = str(decisions.get(key) or "").strip()
                if value:
                    return value
        reader_contract = brief.get("reader_contract")
        if isinstance(reader_contract, dict):
            value = str(reader_contract.get("core_promise") or "").strip()
            if value:
                return value
    return "推进已批准的全书核心冲突，并保护结局边界。"


def chapter_volume_goal(root: Path, volume_number: int) -> str:
    volumes = normalize_records(load_json(root / "20_outline" / "volumes.json", default=[]))
    for index, volume in enumerate(volumes, start=1):
        if not isinstance(volume, dict):
            continue
        number = int(volume.get("number") or index)
        if number != volume_number:
            continue
        for key in ("goal", "promise", "conflict_escalation", "title"):
            value = str(volume.get(key) or "").strip()
            if value:
                return value
    return f"推进第 {volume_number} 卷已批准的冲突与承诺。"


def chapter_protagonist_goal(root: Path, protagonist_id: str) -> str:
    characters = normalize_records(load_json(root / "10_bible" / "characters.json", default=[]))
    for character in characters:
        if not isinstance(character, dict):
            continue
        if protagonist_id and str(character.get("id") or "") != protagonist_id:
            continue
        value = str(character.get("goal") or character.get("desire") or "").strip()
        if value:
            return value
    return "在本章压力下作出会改变后续条件的主动选择。"


def write_chapter_card_artifacts(root: Path, card: dict[str, Any]) -> None:
    """Write the synchronized JSON and Markdown views of one CLI-owned chapter card."""

    chapter_number = int(card["chapter_number"])
    stamp_chapter_contract(card)
    card_dir = root / "20_outline" / "chapter_cards"
    write_json(card_dir / f"ch{chapter_number:03d}.json", card)
    anchor = card.get("outline_anchor") if isinstance(card.get("outline_anchor"), dict) else {}
    event = card.get("event_recommendation") if isinstance(card.get("event_recommendation"), dict) else {}
    reverse = card.get("reverse_brake") if isinstance(card.get("reverse_brake"), dict) else {}
    direction = card.get("direction_selection") if isinstance(card.get("direction_selection"), dict) else {}
    atomic_write_text(
        card_dir / f"ch{chapter_number:03d}.md",
        "\n".join(
            [
                f"# {card.get('title') or f'第{chapter_number}章'}",
                "",
                f"- Chapter: {chapter_number}",
                f"- Volume: {card.get('volume')}",
                f"- Duty: {card.get('chapter_duty')}",
                f"- Plot obligation: {card.get('plot_obligation')}",
                f"- Dramatic freedom: {card.get('dramatic_freedom')}",
                f"- Conflict: {card.get('conflict')}",
                f"- Information: {card.get('information_release')}",
                f"- Hook: {card.get('hook')}",
                f"- Outline anchor: {json.dumps(anchor, ensure_ascii=False)}",
                f"- Event recommendation: {', '.join(as_list(event.get('recommended'))) or 'none'}",
                f"- Event blocked: {', '.join(as_list(event.get('blocked'))) or 'none'}",
                f"- Event constraints: {', '.join(as_list(event.get('constraints'))) or 'none'}",
                f"- Soft event required: {bool(event.get('soft_event_required'))}",
                f"- Reverse brake allowed reveal level: {reverse.get('allowed_reveal_level', '')}",
                f"- Do not resolve: {', '.join(as_list(reverse.get('do_not_resolve'))) or 'none'}",
                f"- Must preserve suspense: {', '.join(as_list(reverse.get('must_preserve_suspense'))) or 'none'}",
                f"- Reader gain: {card.get('reader_gain')}",
                f"- Cost: {card.get('cost')}",
                f"- Platform promise: {card.get('platform_promise')}",
                f"- Topology: {card.get('topology_id')}",
                f"- Ending mode: {card.get('ending_mode') or card.get('hook_mode') or ''}",
                f"- Direction selection: {direction.get('direction_id') or 'not required'}",
                f"- POV character: {card.get('pov_character_id') or 'not declared'}",
                f"- Featured characters: {', '.join(as_list(card.get('featured_character_ids'))) or 'none declared'}",
                f"- Characterization focus: {', '.join(as_list(card.get('characterization_focus'))) or 'derive from scene pressure'}",
                f"- Opposing wants: {', '.join(as_list(card.get('opposing_wants'))) or 'not declared'}",
                f"- Hidden agenda: {', '.join(as_list(card.get('hidden_agenda'))) or 'not declared'}",
                f"- Relationship move: {card.get('relationship_move') or 'preserve current stage'}",
                f"- Irreversible action: {card.get('irreversible_action') or 'not declared'}",
                f"- Emotional aftereffect: {card.get('emotional_aftereffect') or 'not declared'}",
                f"- Summary/scene policy: {card.get('summary_scene_policy') or ''}",
                f"- Semantic review required: {bool(card.get('requires_semantic_review'))}",
                "",
                "## Forbidden",
                "",
                *[f"- {item}" for item in as_list(card.get("forbidden"))],
                "",
            ]
        ),
    )


def upsert_chapter_plan(root: Path, card: dict[str, Any]) -> None:
    path = root / "20_outline" / "chapter_plan.json"
    payload = load_json(path, default=[])
    if not isinstance(payload, list):
        payload = []
    updated = False
    for index, item in enumerate(payload):
        if isinstance(item, dict) and item.get("chapter_number") == card["chapter_number"]:
            payload[index] = {
                **item,
                "chapter_number": card["chapter_number"],
                "title": card["title"],
                "status": card["status"],
                "chapter_duty": card["chapter_duty"],
                "conflict": card.get("conflict") or "",
                "information_release": card.get("information_release") or "",
                "hook": card.get("hook") or "",
                "reader_gain": card.get("reader_gain") or "",
                "cost": card.get("cost") or "",
                "ending_mode": card.get("ending_mode") or card.get("hook_mode") or "",
            }
            updated = True
            break
    if not updated:
        payload.append(
            {
                "chapter_number": card["chapter_number"],
                "title": card["title"],
                "status": card["status"],
                "chapter_duty": card["chapter_duty"],
                "conflict": card.get("conflict") or "",
                "information_release": card.get("information_release") or "",
                "hook": card.get("hook") or "",
                "reader_gain": card.get("reader_gain") or "",
                "cost": card.get("cost") or "",
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


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def safe_timestamp(value: str) -> str:
    return re.sub(r"[^0-9]", "", value)[:14] or "run"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
