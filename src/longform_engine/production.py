"""Production experience orchestration helpers."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import json
import re
from pathlib import Path
from typing import Any

from longform_engine.agent_tasks import HARD_BOUNDARIES, list_manifests, load_manifest, status_summary, validate_manifest_strict
from longform_engine.config import ConfigDocument
from longform_engine.creative import expand_check, humanize_check, humanize_semantic_validate
from longform_engine.editorial import (
    editorial_aggregate,
    editorial_review,
    editorial_review_required_reasons,
    editorial_submit_review,
)
from longform_engine.editorial.pipeline import role_definition
from longform_engine.gates import gate_check, semantic_pacing_validate, semantic_review_validate
from longform_engine.graph import semantic_graph_validate
from longform_engine.intelligence import (
    assess_chapter_direction,
    assess_project_readiness,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.memory import character_validate, semantic_validate
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.quality import reader_payoff_review_status, reader_payoff_task, reader_payoff_validate
from longform_engine.semantic import semantic_task as chapter_semantic_task
from longform_engine.semantic import semantic_validate as chapter_semantic_validate
from longform_engine.storage import resolve_project_root


TASK_WAITING_FOR = {
    "book_ideation": "human_selected_creative_decision_json",
    "fanfiction_canon": "fanfiction_source_canon_json",
    "fanfiction_design": "fanfiction_design_json",
    "book_design": "book_design_json",
    "outline_design": "outline_design_json",
    "chapter_direction": "human_selected_chapter_direction_json",
    "outline_revision": "outline_revision_json",
    "research_synthesis": "research_synthesis_json",
    "style_analysis": "style_analysis_json",
    "adaptation_analysis": "adaptation_analysis_json",
    "chapter_write": "agent_draft",
    "repair": "repair_candidate",
    "humanize": "humanized_candidate",
    "humanize_semantic_review": "humanizer_semantic_review_json",
    "reader_payoff_review": "reader_payoff_review_json",
    "content_expand": "expanded_candidate",
    "graph_extract": "semantic_graph_json",
    "memory_extract": "semantic_memory_json",
    "character_memory": "character_memory_json",
    "editorial_review": "editorial_role_json",
    "pacing_review": "semantic_pacing_json",
    "semantic_review": "semantic_review_json",
}

TASK_PRIORITY = {
    "book_ideation": 0,
    "fanfiction_canon": 1,
    "fanfiction_design": 2,
    "book_design": 3,
    "outline_design": 4,
    "chapter_direction": 9,
    "outline_revision": 5,
    "research_synthesis": 4,
    "style_analysis": 5,
    "adaptation_analysis": 6,
    "chapter_write": 10,
    "repair": 20,
    "humanize": 21,
    "humanize_semantic_review": 22,
    "content_expand": 23,
    "semantic_review": 29,
    "pacing_review": 30,
    "reader_payoff_review": 31,
    "graph_extract": 32,
    "memory_extract": 33,
    "character_memory": 34,
    "editorial_review": 40,
}

STATUS_PRIORITY = {
    "invalid": 1,
    "awaiting_agent": 2,
    "submitted": 3,
    "validated": 4,
}

MANUSCRIPT_DIR = "40_manuscript"
FINAL_LANE = "fin" + "al"

TASK_WORK_SCOPES = {
    "book_ideation": "Resolve exactly one human-approved project creative decision per round.",
    "fanfiction_canon": "Extract evidence-backed source canon as a candidate without storing source prose.",
    "fanfiction_design": "Design a canon-aware fanfiction contract and original mainline as a candidate only.",
    "book_design": "Design project-level book foundations as a candidate only.",
    "outline_design": "Design the book/volume/chapter outline as a candidate only.",
    "chapter_direction": "Offer causally distinct directions for only the declared chapter.",
    "outline_revision": "Revise only the declared chapter range and report stale impact.",
    "research_synthesis": "Synthesize only cited declared research inputs.",
    "style_analysis": "Extract a semantic style profile from declared samples.",
    "adaptation_analysis": "Extract reusable structure and craft methods without copying source prose.",
    "chapter_write": "Write the chapter draft only.",
    "repair": "Write one repair candidate only.",
    "humanize": "Write one humanized candidate only.",
    "humanize_semantic_review": "Review one Humanizer source/candidate pair for semantic preservation only.",
    "reader_payoff_review": "Verify the chapter's observed reader gain, cost, promise progress, and craft shape.",
    "content_expand": "Write one expanded candidate only.",
    "graph_extract": "Extract semantic graph updates as JSON only.",
    "memory_extract": "Extract semantic memory updates as JSON only.",
    "character_memory": "Extract character memory cards as JSON only.",
    "chapter_semantic": "Extract one evidence-bound chapter semantic bundle for every canonical fact lane.",
    "editorial_review": "Write one structured editorial role review only.",
    "pacing_review": "Write one semantic pacing review JSON only.",
    "semantic_review": "Review high-risk chapter semantics with exact prose spans and canonical references.",
}

TASK_ROLE_BRIEFS = {
    "book_ideation": "Creative facilitator. Ask one decisive question and present two or three options with honest tradeoffs.",
    "fanfiction_canon": "Canon archivist. Preserve source names, relationships, rules, voices, and timeline as cited paraphrased facts.",
    "fanfiction_design": "Fanfiction architect. Design continuity, divergence causality, voice contracts, original contribution, and crossover rules.",
    "book_design": "Book architect. Propose the reader contract, world, power system, characters, and relationships.",
    "character_expression_design": (
        "Character-performance architect. Define reusable perception, decision, speech, embodiment, mask, and contrast contracts."
    ),
    "character_expression_review": (
        "Independent character-performance reviewer. Audit voice fit, swapability, embodied presence, and expository dialogue with exact spans."
    ),
    "outline_design": "Longform outline architect. Build a coherent book, volume, chapter, and foreshadowing plan.",
    "chapter_direction": "Chapter story editor. Offer distinct causal directions without writing prose or changing the chapter card.",
    "outline_revision": "Continuity-aware outline editor. Revise the declared range and enumerate stale downstream artifacts.",
    "research_synthesis": "Research synthesizer. Return cited claims grounded only in declared source files.",
    "style_analysis": "Style analyst. Describe semantic craft choices while preserving the existing statistical fingerprint.",
    "adaptation_analysis": "Adaptation analyst. Extract structure and techniques without reproducing sample prose.",
    "chapter_write": "Chapter author. Turn the declared task package into publishable Chinese web-novel prose.",
    "repair": "Repair author. Rewrite only the failed chapter candidate according to gate and repair evidence.",
    "humanize": "Humanizer. Remove AI-flavored prose patterns while preserving canon facts and scene intent.",
    "humanize_semantic_review": (
        "Independent Humanizer semantic-preservation reviewer. Compare source and candidate; "
        "cite exact spans and declared canonical constraints."
    ),
    "reader_payoff_review": (
        "Independent reader-payoff reviewer. Verify what the reader actually gains and pays for, "
        "with exact spans and a non-prescriptive craft observation."
    ),
    "content_expand": "Expansion writer. Add scene substance, dialogue, action, and texture without filler.",
    "graph_extract": "Semantic graph extractor. Return evidence-backed graph update JSON only.",
    "memory_extract": "Semantic memory extractor. Return scene/chapter memory JSON only.",
    "character_memory": "Character memory curator. Return character state cards with evidence only.",
    "chapter_semantic": (
        "Chapter semantic archivist. Read the final chapter once and return exact-span facts for digest, scenes, "
        "relationships, character state, foreshadowing, world state, timeline, and retrieval routing."
    ),
    "editorial_review": "Editorial role reviewer. Return one role-specific structured review JSON only.",
    "pacing_review": "Semantic pacing reader. Judge reader pressure, escalation, tail hook, and reverse-brake risk.",
    "semantic_review": "Semantic continuity reviewer. Cite exact chapter spans and declared canonical state for every finding.",
}

TASK_OUTPUT_GUIDANCE = {
    "book_ideation": "Write JSON matching book_ideation_candidate_v1 with one explicit human selection.",
    "fanfiction_canon": "Write JSON matching fanfiction_source_canon_v1 with source hashes and evidence spans.",
    "fanfiction_design": "Write JSON matching fanfiction_design_candidate_v1, including a nested book_design_candidate_v1.",
    "book_design": "Write JSON matching book_design_candidate_v2 at the declared intelligence candidate path.",
    "character_expression_design": (
        "Write JSON matching character_expression_profile_v1 with one complete contract per declared character."
    ),
    "character_expression_review": (
        "Write JSON matching character_expression_review_v1 with hash-bound evidence for every reviewed chapter and character."
    ),
    "outline_design": "Write JSON matching outline_design_candidate_v1 at the declared intelligence candidate path.",
    "chapter_direction": "Write JSON matching chapter_direction_candidate_v1 with two or three directions and one human selection.",
    "outline_revision": "Write JSON matching outline_revision_candidate_v1, including impact and stale markers.",
    "research_synthesis": "Write JSON matching research_synthesis_v1 with evidence and declared source_path citations.",
    "style_analysis": "Write JSON matching semantic_style_profile_v1 with source hashes.",
    "adaptation_analysis": "Write JSON matching adaptation_analysis_v1; do not include excerpts or copied source body.",
    "chapter_write": "Write Markdown chapter prose at the allowed draft path; include only title and manuscript body.",
    "repair": "Write a complete replacement candidate at the allowed repair path; do not patch canonical draft/final files.",
    "humanize": "Write a full humanized candidate at the allowed repair candidate path.",
    "humanize_semantic_review": (
        "Write JSON matching humanizer_semantic_review_v1 with all seven preservation dimensions, "
        "chapter contract, voice checks, and exact source/candidate spans."
    ),
    "chapter_semantic": (
        "Write chapter_semantic_bundle_v1 JSON only. Every scene and delta must contain exact start/end/excerpt "
        "evidence, stable entity IDs, and complete changed-or-unchanged coverage."
    ),
    "reader_payoff_review": (
        "Write JSON matching reader_payoff_review_v1 with observed gain/cost, promise progress, "
        "evidence spans, fake-payoff flags, and craft observation."
    ),
    "content_expand": "Write a full expanded candidate at the allowed repair candidate path.",
    "graph_extract": "Write JSON matching semantic_graph_update_v1 at the allowed graph update path.",
    "memory_extract": "Write JSON matching semantic_memory_v1 at the allowed memory task path.",
    "character_memory": "Write JSON matching character_memory_cards_v1 at the allowed memory task path.",
    "editorial_review": (
        "Write JSON matching editorial_role_review_v2, including the isolated context digest, "
        "reviewer instance, Agent product/version, review round, independence mode, and confidence."
    ),
    "pacing_review": "Write JSON matching semantic_pacing_result_v1 at the gate artifact path.",
    "semantic_review": "Write JSON matching semantic_review_result_v1 at the declared gate artifact path.",
}

CONTEXT_BUDGET_RULES = (
    "Read only the manifest input_files and the rendered work order unless the user explicitly supplies more context.",
    "Do not scan the whole project, final manuscript corpus, runtime database, model cache, or unrelated workbench lanes.",
    "Treat draft, repair candidate, research inbox, and validation output as non-canonical unless this work order names them.",
    "When evidence is needed, quote or cite only from the declared input files.",
)

WORK_ORDER_FORBIDDEN_PATHS = (
    "10_bible/",
    "20_outline/",
    "10_bible/research_canon.jsonl",
    "40_manuscript/" + FINAL_LANE + "/",
    "60_" + "rag/",
    "30_state/" + "story_graph.json",
    "30_state/" + "tcs/",
    "70_runtime/" + "db/",
)

NEED_HUMAN_REASON_LABELS = {
    "unresolved_P0": "Unresolved P0 editorial issue requires human decision.",
    "unresolved_P1": "Unresolved P1 editorial issue requires repair or human decision.",
    "editorial_blocking_verdict": "At least one editorial role returned a blocking verdict.",
    "repeated_conditional_pass": "Conditional pass repeated across the editorial batch.",
    "missing_editorial_roles": "One or more required editorial roles have not submitted accepted results.",
    "duplicate_role_results": "One or more editorial roles have duplicate raw result files.",
    "invalid_role_results": "One or more editorial role result files failed validation.",
    "manual editorial escalation requested": "A human editorial escalation was requested manually.",
}


def production_next(config: ConfigDocument) -> dict[str, Any]:
    """Return the highest-priority safe next production action."""

    root = resolve_project_root(config)
    action = (
        first_need_human_action(root)
        or project_readiness_action(config, root)
        or chapter_direction_action(config, root)
        or chapter_semantic_lifecycle_action(root)
        or chapter_workflow_action(config, root)
        or first_active_agent_task(root)
        or first_draft_without_gate_action(root)
        or first_writing_task_action(root)
    )
    if action is not None:
        return action
    chapter_number = highest_finalized_chapter(root) + 1
    return base_action(
        status="ready_for_continue_write",
        chapter_number=chapter_number,
        blocked_by="none",
        waiting_for="cli",
        next_command=f"longform-engine continue-write project.yaml --chapter {chapter_number}",
        human_summary=f"No blocker found. Generate the ch{chapter_number:03d} writing task.",
    )


def production_status(config: ConfigDocument) -> dict[str, Any]:
    """Return a stable read-only JSON contract for GUI/API production status."""

    root = resolve_project_root(config)
    known_chapter = max_known_chapter(root)
    next_action = production_next(config)
    board = production_board(config, from_chapter=1, to_chapter=known_chapter)
    task_summary = status_summary(root)
    return normalize_contract_json(
        root,
        {
            "schema_version": 1,
            "status_version": "production_status_v1",
            "read_only": True,
            "path_style": "project_relative",
            "command_style": "longform-engine",
            "redaction": {
                "no_chapter_body": True,
                "no_api_keys": True,
                "no_full_prompt_logs": True,
            },
            "current": {
                "highest_finalized_chapter": highest_finalized_chapter(root),
                "max_known_chapter": known_chapter,
                "next_status": next_action.get("status"),
                "blocked_by": next_action.get("blocked_by"),
                "waiting_for": next_action.get("waiting_for"),
                "next_command": next_action.get("next_command"),
            },
            "next_action": next_action,
            "agent_tasks": {
                "schema_version": task_summary.get("schema_version"),
                "tasks": task_summary.get("tasks"),
                "by_status": task_summary.get("by_status") or {},
                "by_type": task_summary.get("by_type") or {},
                "event_file": task_summary.get("event_file") or "",
            },
            "board": {
                "board_version": board.get("board_version"),
                "from_chapter": board.get("from_chapter"),
                "to_chapter": board.get("to_chapter"),
                "totals": board.get("totals") or {},
            },
            "resources": {
                "production_status": "GET /production/status",
                "production_next": "GET /production/next",
                "production_board": "GET /production/board?from=N&to=M",
                "agent_task_brief": "GET /agent-tasks/{task_id}/brief",
                "production_loop": "POST /production/loop",
            },
            "sources": {
                "agent_task_index": "50_workbench/agent_tasks/agent_task_index.json",
                "agent_task_events": "50_workbench/agent_tasks/events.jsonl",
                "gate_artifacts": "50_workbench/gate_artifacts/",
                "validation_reports": "50_workbench/",
                "transaction_reports": "70_runtime/transactions/",
                "auto_write_state": "70_runtime/auto_write_state.json",
                "chapter_meta": "40_manuscript/chapter_meta.jsonl",
            },
        },
    )


def agent_task_brief(config: ConfigDocument, task: str | Path) -> dict[str, Any]:
    """Render one AgentTaskManifest as a read-only work order."""

    root = resolve_project_root(config)
    manifest = load_manifest(root, task)
    entry = manifest_entry(root, task, manifest)
    validation = validate_manifest_strict(root, manifest, strict=True)
    task_type = str(manifest.get("task_type") or "")
    chapter_number = int(manifest.get("chapter_number") or 0)
    payload = {
        "schema_version": 1,
        "renderer": "agent_task_brief_v1",
        "read_only": True,
        "manifest_file": str(entry.get("manifest_file") or manifest_file_from_task(root, task)),
        "task_id": str(manifest.get("task_id") or ""),
        "task_type": task_type,
        "chapter_number": chapter_number,
        "scope": manifest.get("scope") or {"kind": "chapter", "chapter_number": chapter_number},
        "canonical_targets": as_string_list(manifest.get("canonical_targets")),
        "requires_human_apply": bool(manifest.get("requires_human_apply")),
        "status": str(manifest.get("status") or ""),
        "work_scope": TASK_WORK_SCOPES.get(task_type, "Complete only the declared Agent task."),
        "input_files": as_string_list(manifest.get("input_files")),
        "context_policy": dict(manifest.get("context_policy") or {}),
        "allowed_output_paths": as_string_list(manifest.get("allowed_output_paths")),
        "output_schema": str(manifest.get("output_schema") or ""),
        "validate_command": ensure_longform_prefix(str(manifest.get("validate_command") or "")),
        "apply_command": ensure_longform_prefix(str(manifest.get("apply_command") or "")),
        "failure_next_command": ensure_longform_prefix(str(manifest.get("failure_next_command") or "")),
        "hard_boundaries": as_string_list(manifest.get("hard_boundaries")) or list(HARD_BOUNDARIES),
        "agent_role": TASK_ROLE_BRIEFS.get(task_type, "Host Agent. Complete only the declared task."),
        "output_guidance": TASK_OUTPUT_GUIDANCE.get(task_type, "Write only the declared output artifact."),
        "context_budget_rules": list(CONTEXT_BUDGET_RULES),
        "forbidden_paths": list(WORK_ORDER_FORBIDDEN_PATHS),
        "manifest_validation": {
            "strict": True,
            "ok": validation.ok,
            "errors": list(validation.errors),
            "warnings": list(validation.warnings),
        },
        "completion_report_template": [
            "Output written:",
            "Validation command run:",
            "Validation result:",
            "Next command:",
        ],
    }
    payload["work_order_markdown"] = render_agent_task_work_order(payload)
    return payload


def production_board(
    config: ConfigDocument,
    *,
    from_chapter: int | None = None,
    to_chapter: int | None = None,
) -> dict[str, Any]:
    """Return a read-only multi-chapter production board."""

    root = resolve_project_root(config)
    start = from_chapter or 1
    if start <= 0:
        raise ValueError("--from must be a positive chapter number.")
    end = to_chapter or max_known_chapter(root)
    if end < start:
        raise ValueError("--to must be greater than or equal to --from.")
    chapters = [chapter_board_row(root, chapter_number) for chapter_number in range(start, end + 1)]
    return {
        "schema_version": 1,
        "board_version": "production_board_v1",
        "read_only": True,
        "from_chapter": start,
        "to_chapter": end,
        "chapters": chapters,
        "totals": board_totals(chapters),
        "sources": {
            "agent_task_index": "50_workbench/agent_tasks/agent_task_index.json",
            "gate_artifacts": "50_workbench/gate_artifacts/",
            "editorial_reviews": "50_workbench/editorial_reviews/",
            "transactions": "70_runtime/transactions/",
            "run_reports": "70_runtime/run_reports/",
        },
    }


def production_loop(
    config: ConfigDocument,
    *,
    max_steps: int = 10,
    no_apply: bool = True,
) -> dict[str, Any]:
    """Advance deterministic production steps until the next safe blocker."""

    if max_steps <= 0:
        raise ValueError("--max-steps must be a positive integer.")
    root = resolve_project_root(config)
    steps: list[dict[str, Any]] = []
    for step_number in range(1, max_steps + 1):
        action = production_next(config)
        decision = loop_decision(root, action, no_apply=no_apply)
        if decision["kind"] == "pause":
            return loop_payload(
                status="paused",
                no_apply=no_apply,
                max_steps=max_steps,
                steps=steps,
                pause_reason=str(decision["reason"]),
                next_action=action,
            )
        try:
            result = execute_loop_decision(config, root, action, decision)
        except Exception as exc:
            steps.append(
                {
                    "step": step_number,
                    "action": decision.get("action"),
                    "task_type": action.get("task_type") or "",
                    "chapter_number": action.get("chapter_number"),
                    "command": decision.get("command") or action.get("next_command"),
                    "status": "failed",
                    "error": str(exc),
                }
            )
            return loop_payload(
                status="failed",
                no_apply=no_apply,
                max_steps=max_steps,
                steps=steps,
                pause_reason="deterministic_step_failed",
                next_action=production_next(config),
            )
        steps.append(
            {
                "step": step_number,
                "action": decision.get("action"),
                "task_type": action.get("task_type") or "",
                "chapter_number": action.get("chapter_number"),
                "command": decision.get("command") or action.get("next_command"),
                "status": "executed",
                "result": result,
            }
        )
    return loop_payload(
        status="max_steps_reached",
        no_apply=no_apply,
        max_steps=max_steps,
        steps=steps,
        pause_reason="max_steps_reached",
        next_action=production_next(config),
    )


def loop_decision(root: Path, action: dict[str, Any], *, no_apply: bool) -> dict[str, Any]:
    status = str(action.get("status") or "")
    task_type = str(action.get("task_type") or "")
    chapter_number = int(action.get("chapter_number") or 0)
    if status == "ready_for_open_book":
        return {"kind": "execute", "action": "open_book", "command": action.get("next_command")}
    if status == "ready_for_intelligence_task":
        if task_type == "fanfiction_canon":
            return {"kind": "pause", "reason": "declared_fanfiction_source_input_required"}
        return {"kind": "execute", "action": "intelligence_task", "command": action.get("next_command")}
    if status == "ready_for_continue_write":
        return {
            "kind": "execute",
            "action": "continue_write",
            "command": action.get("next_command"),
        }
    if status == "ready_for_chapter_semantic_task":
        return {
            "kind": "execute",
            "action": "chapter_semantic_task",
            "command": action.get("next_command"),
        }
    if status == "ready_for_reader_payoff_task":
        return {
            "kind": "execute",
            "action": "reader_payoff_task",
            "command": action.get("next_command"),
        }
    if status == "ready_for_editorial_review":
        return {
            "kind": "execute",
            "action": "editorial_review",
            "command": action.get("next_command"),
        }
    if status == "awaiting_gate":
        return {
            "kind": "execute",
            "action": "gate_check",
            "command": action.get("validate_command") or action.get("next_command"),
        }
    if status == "agent_task_awaiting_agent":
        output_path = first_existing_allowed_output(root, action)
        if output_path is None:
            return {"kind": "pause", "reason": "awaiting_agent_output"}
        if task_type in LOOP_OUTPUT_VALIDATORS:
            return {
                "kind": "execute",
                "action": LOOP_OUTPUT_VALIDATORS[task_type],
                "command": action.get("validate_command") or action.get("next_command"),
                "output_path": output_path,
            }
        return {"kind": "pause", "reason": "unsupported_agent_task_validation"}
    if status == "agent_task_submitted":
        output_path = first_existing_allowed_output(root, action)
        if output_path is not None and task_type in LOOP_OUTPUT_VALIDATORS:
            return {
                "kind": "execute",
                "action": LOOP_OUTPUT_VALIDATORS[task_type],
                "command": action.get("validate_command") or action.get("next_command"),
                "output_path": output_path,
            }
        return {"kind": "pause", "reason": "submitted_task_needs_validation"}
    if status == "agent_task_validated":
        if task_type == "editorial_review":
            return {
                "kind": "execute",
                "action": "editorial_aggregate",
                "command": action.get("apply_command") or action.get("next_command"),
            }
        return {
            "kind": "pause",
            "reason": "apply_or_finalize_required" if no_apply else "canonical_apply_requires_explicit_command",
        }
    if status == "agent_task_invalid":
        return {"kind": "pause", "reason": "agent_task_invalid"}
    if status == "gate_failed":
        return {"kind": "pause", "reason": "gate_failed"}
    if status == "awaiting_finalize":
        return {"kind": "pause", "reason": "human_finalize_required"}
    if status == "awaiting_chapter_close":
        return {"kind": "pause", "reason": "human_chapter_close_required"}
    if status == "need_human":
        return {"kind": "pause", "reason": "need_human"}
    return {"kind": "pause", "reason": f"unsupported_status:{status or 'unknown'}"}


LOOP_OUTPUT_VALIDATORS = {
    "chapter_write": "draft_submit_existing_agent_output",
    "repair": "draft_submit_existing_agent_output",
    "humanize": "humanize_check",
    "humanize_semantic_review": "humanize_semantic_validate",
    "reader_payoff_review": "reader_payoff_validate",
    "content_expand": "expand_check",
    "graph_extract": "graph_semantic_validate",
    "memory_extract": "memory_semantic_validate",
    "character_memory": "character_memory_validate",
    "chapter_semantic": "chapter_semantic_validate",
    "editorial_review": "editorial_submit_review",
    "pacing_review": "pacing_semantic_validate",
    "semantic_review": "gate_semantic_validate",
    "book_ideation": "intelligence_validate",
    "book_design": "intelligence_validate",
    "outline_design": "intelligence_validate",
    "chapter_direction": "intelligence_validate",
    "outline_revision": "intelligence_validate",
    "research_synthesis": "intelligence_validate",
    "style_analysis": "intelligence_validate",
    "adaptation_analysis": "intelligence_validate",
    "fanfiction_canon": "intelligence_validate",
    "fanfiction_design": "intelligence_validate",
}


def execute_loop_decision(
    config: ConfigDocument,
    root: Path,
    action: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    command = str(decision.get("action") or "")
    chapter_number = int(action.get("chapter_number") or 0)
    output_path = decision.get("output_path")
    if command == "open_book":
        return serialize_loop_result(root, open_book(config))
    if command == "intelligence_task":
        return serialize_loop_result(
            root,
            create_intelligence_task(
                config,
                task_type=str(action.get("task_type") or ""),
                chapter_number=chapter_number or None,
            ),
        )
    if command == "continue_write":
        return serialize_loop_result(root, continue_write(config, chapter_number=chapter_number))
    if command == "chapter_semantic_task":
        return serialize_loop_result(root, chapter_semantic_task(config, chapter_number=chapter_number))
    if command == "reader_payoff_task":
        return serialize_loop_result(
            root,
            reader_payoff_task(config, chapter_number=chapter_number),
        )
    if command == "editorial_review":
        return serialize_loop_result(
            root,
            editorial_review(config, chapter_number=chapter_number),
        )
    if command == "gate_check":
        return serialize_loop_result(root, gate_check(config, chapter_number=chapter_number))
    if command == "draft_submit_existing_agent_output":
        source = require_loop_output_path(output_path)
        agent = agent_from_output_path(source)
        overwrite = str(action.get("task_type") or "") == "repair"
        return serialize_loop_result(
            root,
            submit_agent_draft(
                config,
                chapter_number=chapter_number,
                file_path=source,
                agent=agent,
                overwrite=overwrite,
            )
        )
    if command == "humanize_check":
        return serialize_loop_result(root, humanize_check(config, chapter_number=chapter_number, file_path=require_loop_output_path(output_path)))
    if command == "humanize_semantic_validate":
        return serialize_loop_result(
            root,
            humanize_semantic_validate(
                config,
                chapter_number=chapter_number,
                file_path=require_loop_output_path(output_path),
            ),
        )
    if command == "reader_payoff_validate":
        return serialize_loop_result(
            root,
            reader_payoff_validate(
                config,
                chapter_number=chapter_number,
                file_path=require_loop_output_path(output_path),
            ),
        )
    if command == "expand_check":
        return serialize_loop_result(root, expand_check(config, chapter_number=chapter_number, file_path=require_loop_output_path(output_path)))
    if command == "graph_semantic_validate":
        return serialize_loop_result(root, semantic_graph_validate(config, chapter_number=chapter_number, file_path=require_loop_output_path(output_path)))
    if command == "memory_semantic_validate":
        return serialize_loop_result(root, semantic_validate(config, chapter_number=chapter_number, file_path=require_loop_output_path(output_path)))
    if command == "character_memory_validate":
        return serialize_loop_result(root, character_validate(config, chapter_number=chapter_number, file_path=require_loop_output_path(output_path)))
    if command == "chapter_semantic_validate":
        return serialize_loop_result(
            root,
            chapter_semantic_validate(
                config,
                chapter_number=chapter_number,
                file_path=require_loop_output_path(output_path),
            ),
        )
    if command == "editorial_submit_review":
        source = require_loop_output_path(output_path)
        role = role_from_editorial_output(source)
        return serialize_loop_result(
            root,
            editorial_submit_review(config, chapter_number=chapter_number, role=role, file_path=source)
        )
    if command == "pacing_semantic_validate":
        return serialize_loop_result(root, semantic_pacing_validate(config, chapter_number=chapter_number, file_path=require_loop_output_path(output_path)))
    if command == "gate_semantic_validate":
        return serialize_loop_result(root, semantic_review_validate(config, chapter_number=chapter_number, file_path=require_loop_output_path(output_path)))
    if command == "intelligence_validate":
        return serialize_loop_result(
            root,
            validate_intelligence_candidate(
                config,
                task_type=str(action.get("task_type") or ""),
                file_path=require_loop_output_path(output_path),
            ),
        )
    if command == "editorial_aggregate":
        return serialize_loop_result(root, editorial_aggregate(config, chapter_number=chapter_number))
    raise ValueError(f"Unsupported production loop action: {command}")


def loop_payload(
    *,
    status: str,
    no_apply: bool,
    max_steps: int,
    steps: list[dict[str, Any]],
    pause_reason: str,
    next_action: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "loop_version": "production_loop_v1",
        "safe_loop": True,
        "no_apply": no_apply,
        "max_steps": max_steps,
        "status": status,
        "pause_reason": pause_reason,
        "steps_executed": len(steps),
        "steps": steps,
        "next_action": next_action,
        "hard_boundaries": ["no LLM", "no generated prose", *HARD_BOUNDARIES],
    }


def first_existing_allowed_output(root: Path, action: dict[str, Any]) -> Path | None:
    for item in as_string_list(action.get("allowed_output_paths")):
        path = root / item if not Path(item).is_absolute() else Path(item)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.exists() and resolved.is_file() and root.resolve() in (resolved, *resolved.parents):
            return resolved
    return None


def require_loop_output_path(value: Any) -> Path:
    if isinstance(value, Path):
        return value
    raise ValueError("Production loop decision is missing an existing output path.")


def agent_from_output_path(path: Path) -> str:
    parts = path.name.split(".")
    if len(parts) >= 3 and parts[1]:
        return parts[1]
    return "codex"


def role_from_editorial_output(path: Path) -> str:
    parts = path.name.split(".")
    if len(parts) >= 3 and parts[1]:
        return parts[1]
    raise ValueError(f"Cannot infer editorial role from output file name: {path.name}")


def serialize_loop_result(root: Path, value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return normalize_contract_json(root, asdict(value))
    if isinstance(value, dict):
        return normalize_contract_json(root, value)
    return {"value": str(value)}


def normalize_contract_json(root: Path, value: Any) -> Any:
    if isinstance(value, Path):
        return relative_path(root, value)
    if isinstance(value, str):
        return normalize_contract_string(root, value)
    if isinstance(value, tuple):
        return [normalize_contract_json(root, item) for item in value]
    if isinstance(value, list):
        return [normalize_contract_json(root, item) for item in value]
    if isinstance(value, dict):
        return {str(key): normalize_contract_json(root, item) for key, item in value.items()}
    return value


def normalize_contract_string(root: Path, value: str) -> str:
    text = str(value)
    if not text:
        return text
    candidate = Path(text)
    if candidate.is_absolute():
        return relative_path(root, candidate)
    return text


def chapter_board_row(root: Path, chapter_number: int) -> dict[str, Any]:
    tasks = list_manifests(root, chapter_number=chapter_number)
    gate = gate_status(root, chapter_number)
    final_status = "finalized" if final_chapter_exists(root, chapter_number) else "missing"
    draft_status = chapter_draft_status(root, chapter_number, gate, tasks)
    return {
        "chapter_number": chapter_number,
        "draft_status": draft_status,
        "final_status": final_status,
        "gate_status": gate,
        "repair_status": task_lane_status(root, chapter_number, tasks, ("repair",), ("50_workbench/repair_candidates/ch{chapter}.repair*", "50_workbench/repair_candidates/ch{chapter}.*repair_candidate.md")),
        "humanize_status": task_lane_status(root, chapter_number, tasks, ("humanize",), ("50_workbench/repair_candidates/ch{chapter}.humanized_candidate.md", "50_workbench/humanizer_tasks/ch{chapter}*")),
        "humanize_semantic_status": task_lane_status(
            root,
            chapter_number,
            tasks,
            ("humanize_semantic_review",),
            (
                "50_workbench/humanizer_tasks/ch{chapter}.semantic_review.json",
                "50_workbench/humanizer_tasks/ch{chapter}.semantic_review.validation.json",
            ),
        ),
        "reader_payoff_status": task_lane_status(
            root,
            chapter_number,
            tasks,
            ("reader_payoff_review",),
            (
                "50_workbench/quality_reviews/ch{chapter}.reader_payoff.json",
                "50_workbench/quality_reviews/ch{chapter}.reader_payoff.validation.json",
            ),
        ),
        "expand_status": task_lane_status(root, chapter_number, tasks, ("content_expand",), ("50_workbench/repair_candidates/ch{chapter}.expanded_candidate.md",)),
        "graph_status": task_lane_status(root, chapter_number, tasks, ("graph_extract",), ("50_workbench/graph_updates/ch{chapter}*.json",)),
        "memory_status": task_lane_status(root, chapter_number, tasks, ("memory_extract",), ("50_workbench/memory_tasks/ch{chapter}.semantic*.json",)),
        "character_memory_status": task_lane_status(root, chapter_number, tasks, ("character_memory",), ("50_workbench/memory_tasks/ch{chapter}.character*.json",)),
        "semantic_pacing_status": task_lane_status(root, chapter_number, tasks, ("pacing_review",), ("50_workbench/gate_artifacts/ch{chapter}/semantic_pacing_result.json", "50_workbench/gate_artifacts/ch{chapter}/semantic_pacing_validation.json")),
        "editorial": editorial_board_status(root, chapter_number, tasks),
        "agent_tasks": agent_task_board_summary(tasks),
        "latest_transaction": latest_json_summary(root / "70_runtime" / "transactions", chapter_number),
        "latest_report": latest_json_summary(root / "70_runtime" / "run_reports", chapter_number),
    }


def manifest_entry(root: Path, task: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    task_text = str(task)
    task_id = str(manifest.get("task_id") or "")
    for entry in list_manifests(root, chapter_number=int(manifest.get("chapter_number") or 0) or None):
        if entry.get("task_id") == task_text or entry.get("task_id") == task_id:
            return entry
    return {}


def manifest_file_from_task(root: Path, task: str | Path) -> str:
    task_path = Path(task)
    if task_path.suffix.lower() != ".json":
        return ""
    return relative_path(root, task_path)


def render_agent_task_work_order(payload: dict[str, Any]) -> str:
    context_policy = payload.get("context_policy") if isinstance(payload.get("context_policy"), dict) else {}
    lines = [
        f"# Agent Work Order: {payload.get('task_id') or 'unknown'}",
        "",
        "## Role And Goal",
        "",
        f"- Agent role: {payload.get('agent_role') or ''}",
        f"- Work scope: {payload.get('work_scope') or ''}",
        f"- Output goal: {payload.get('output_guidance') or ''}",
        "",
        "## Task Contract",
        f"- Task id: `{payload.get('task_id') or ''}`",
        f"- Task type: `{payload.get('task_type') or ''}`",
        f"- Chapter: `{payload.get('chapter_number') or ''}`",
        f"- Scope: `{json.dumps(payload.get('scope') or {}, ensure_ascii=False)}`",
        f"- Requires human apply: `{bool(payload.get('requires_human_apply'))}`",
        f"- Status: `{payload.get('status') or ''}`",
        f"- Manifest: `{payload.get('manifest_file') or ''}`",
        "",
        "## Context Budget",
        *markdown_plain_list(payload.get("context_budget_rules") or []),
        f"- Maximum files: `{context_policy.get('max_files', '')}`",
        f"- Maximum compiled characters: `{context_policy.get('max_chars', '')}`",
        f"- Compiled brief: `{context_policy.get('compiled_brief', '')}`",
        f"- Selection report: `{context_policy.get('selection_report', '') or 'embedded in the compiled brief'}`",
        "",
        "## Required Input Files",
        *markdown_list(context_policy.get("required_files") or []),
        "",
        "## Optional Input Files",
        *markdown_list(context_policy.get("optional_files") or []),
        "",
        "## Forbidden Context Paths",
        *markdown_list(context_policy.get("forbidden_paths") or []),
        "",
        "## Allowed Output Paths",
        *markdown_list(payload.get("allowed_output_paths") or []),
        "",
        "## Canonical Targets (CLI Apply Only)",
        *markdown_list(payload.get("canonical_targets") or []),
        "",
        "## Output Schema",
        f"- `{payload.get('output_schema') or ''}`",
        "",
        "## Commands",
        f"- Validate command: `{payload.get('validate_command') or ''}`",
        f"- Apply command: `{payload.get('apply_command') or ''}`",
        f"- Failure next command: `{payload.get('failure_next_command') or ''}`",
        "",
        "## Hard Boundaries",
        *markdown_list(payload.get("hard_boundaries") or []),
        "",
        "## Forbidden Direct Writes",
        *markdown_list(payload.get("forbidden_paths") or []),
        "",
        "## Manifest Validation",
        f"- Strict validation ok: `{bool((payload.get('manifest_validation') or {}).get('ok'))}`",
    ]
    errors = (payload.get("manifest_validation") or {}).get("errors") or []
    warnings = (payload.get("manifest_validation") or {}).get("warnings") or []
    if errors:
        lines.extend(["- Errors:", *markdown_list(errors, indent="  ")])
    if warnings:
        lines.extend(["- Warnings:", *markdown_list(warnings, indent="  ")])
    lines.extend(
        [
            "",
            "## Completion Report",
            *markdown_list(payload.get("completion_report_template") or []),
            "",
            "Only write the declared output path. After writing, run the validate command and report the result.",
            "Do not run the apply/finalize command unless the user explicitly asks for that state transition.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def markdown_list(items: list[Any], *, indent: str = "") -> list[str]:
    if not items:
        return [f"{indent}- none"]
    return [f"{indent}- `{item}`" for item in items]


def markdown_plain_list(items: list[Any], *, indent: str = "") -> list[str]:
    if not items:
        return [f"{indent}- none"]
    return [f"{indent}- {item}" for item in items]


def max_known_chapter(root: Path) -> int:
    chapters: set[int] = {1}
    for pattern in (
        root / MANUSCRIPT_DIR / "draft",
        root / MANUSCRIPT_DIR / FINAL_LANE,
        root / "50_workbench" / "writing_tasks",
        root / "50_workbench" / "repair_candidates",
        root / "50_workbench" / "graph_updates",
        root / "50_workbench" / "memory_tasks",
    ):
        for path in pattern.glob("ch*"):
            chapter = chapter_from_name(path.name)
            if chapter > 0:
                chapters.add(chapter)
    for path in (root / "50_workbench" / "gate_artifacts").glob("ch*"):
        chapter = chapter_from_name(path.name)
        if chapter > 0:
            chapters.add(chapter)
    for path in (root / "50_workbench" / "editorial_reviews").glob("ch*.aggregate.json"):
        chapter = chapter_from_name(path.name)
        if chapter > 0:
            chapters.add(chapter)
    for task in list_manifests(root):
        chapter = int(task.get("chapter_number") or 0)
        if chapter > 0:
            chapters.add(chapter)
    for path in (root / "70_runtime" / "transactions").glob("*.json"):
        payload = read_json(path)
        chapter = int(payload.get("chapter_number") or 0) if isinstance(payload, dict) else 0
        if chapter > 0:
            chapters.add(chapter)
    return max(chapters)


def chapter_draft_status(root: Path, chapter_number: int, gate: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    if final_chapter_exists(root, chapter_number):
        return "finalized"
    gate_state = str(gate.get("status") or "none")
    if gate_state in {"passed", "failed", "waived"}:
        return "gate_passed" if gate_state in {"passed", "waived"} else "gate_failed"
    if draft_chapter_exists(root, chapter_number):
        return "draft_submitted"
    if any(str(task.get("task_type") or "") == "chapter_write" for task in tasks):
        return "agent_task"
    if (root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json").exists():
        return "agent_task"
    return "none"


def gate_status(root: Path, chapter_number: int) -> dict[str, Any]:
    path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    payload = read_json(path)
    if not isinstance(payload, dict) or not payload:
        return {
            "status": "none",
            "passed": None,
            "severity": "",
            "next_command": "",
            "failures": 0,
            "warnings": 0,
            "source": "",
        }
    if gate_has_waiver(payload):
        status = "waived"
    elif payload.get("passed") is True:
        status = "passed"
    elif payload.get("passed") is False:
        status = "failed"
    else:
        status = "unknown"
    return {
        "status": status,
        "passed": payload.get("passed"),
        "severity": str(payload.get("severity") or ""),
        "next_command": ensure_longform_prefix(str(payload.get("next_command") or "")),
        "failures": len(payload.get("failures") or []),
        "warnings": len(payload.get("warnings") or []),
        "source": relative_path(root, path),
    }


def task_lane_status(
    root: Path,
    chapter_number: int,
    tasks: list[dict[str, Any]],
    task_types: tuple[str, ...],
    artifact_patterns: tuple[str, ...],
) -> dict[str, Any]:
    selected = [task for task in tasks if str(task.get("task_type") or "") in task_types]
    artifacts = matching_artifacts(root, chapter_number, artifact_patterns)
    statuses = [str(task.get("status") or "unknown") for task in selected]
    return {
        "status": lane_status(statuses, artifacts),
        "task_count": len(selected),
        "by_status": count_values(statuses),
        "artifacts": artifacts,
        "active_task_ids": [
            str(task.get("task_id") or "")
            for task in selected
            if str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
        ],
    }


def matching_artifacts(root: Path, chapter_number: int, patterns: tuple[str, ...]) -> list[str]:
    token = f"{chapter_number:03d}"
    artifacts: list[str] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern.format(chapter=token))):
            if path.name.endswith(".agent_task.json"):
                continue
            artifacts.append(relative_path(root, path))
    return dedupe(artifacts)


def lane_status(statuses: list[str], artifacts: list[str]) -> str:
    priority = ("invalid", "awaiting_agent", "submitted", "validated", "applied", "superseded", "rolled_back")
    for status in priority:
        if status in statuses:
            return status
    if statuses:
        return sorted(statuses)[0]
    if artifacts:
        return "artifact_exists"
    return "none"


def editorial_board_status(root: Path, chapter_number: int, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    aggregate_path = root / "50_workbench" / "editorial_reviews" / f"ch{chapter_number:03d}.aggregate.json"
    aggregate = read_json(aggregate_path)
    if isinstance(aggregate, dict) and aggregate:
        need_human = bool(aggregate.get("need_human"))
        reasons = as_string_list(aggregate.get("need_human_reasons"))
        return {
            "status": "need_human" if need_human else "aggregated",
            "expected_roles": as_string_list(aggregate.get("expected_roles")),
            "accepted_roles": as_string_list(aggregate.get("accepted_roles")),
            "missing_roles": as_string_list(aggregate.get("missing_roles")),
            "duplicate_role_results": aggregate.get("duplicate_role_results") or [],
            "invalid_results": aggregate.get("invalid_results") or [],
            "role_statuses": editorial_role_statuses(root, chapter_number, tasks, aggregate),
            "severity_counts": aggregate.get("severity_counts") or {"P0": 0, "P1": 0, "P2": 0},
            "unresolved_items": int(aggregate.get("unresolved_items") or 0),
            "conditional_passes": int(aggregate.get("conditional_passes") or 0),
            "need_human": need_human,
            "need_human_reasons": reasons,
            "need_human_reasons_readable": readable_need_human_reasons(reasons),
            "result_count": int(aggregate.get("result_count") or 0),
            "next_command": ensure_longform_prefix(str(aggregate.get("next_command") or "")),
            "source": relative_path(root, aggregate_path),
        }
    editorial_tasks = [task for task in tasks if str(task.get("task_type") or "") == "editorial_review"]
    roles = sorted(filter(None, [role_from_editorial_task(task) for task in editorial_tasks]))
    accepted = sorted(
        filter(
            None,
            [
                role_from_editorial_task(task)
                for task in editorial_tasks
                if str(task.get("status") or "") in {"validated", "applied"}
            ],
        )
    )
    invalid = [
        {"role_id": role_from_editorial_task(task), "task_id": task.get("task_id")}
        for task in editorial_tasks
        if str(task.get("status") or "") == "invalid"
    ]
    return {
        "status": "awaiting_results" if editorial_tasks else "none",
        "expected_roles": roles,
        "accepted_roles": accepted,
        "missing_roles": [role for role in roles if role not in accepted],
        "duplicate_role_results": [],
        "invalid_results": invalid,
        "role_statuses": editorial_role_statuses(root, chapter_number, tasks, {}),
        "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
        "unresolved_items": 0,
        "conditional_passes": 0,
        "need_human": False,
        "need_human_reasons": [],
        "need_human_reasons_readable": [],
        "result_count": len(accepted),
        "next_command": "",
        "source": "",
    }


def editorial_role_statuses(
    root: Path,
    chapter_number: int,
    tasks: list[dict[str, Any]],
    aggregate: dict[str, Any],
) -> list[dict[str, Any]]:
    editorial_tasks = [task for task in tasks if str(task.get("task_type") or "") == "editorial_review"]
    tasks_by_role: dict[str, dict[str, Any]] = {}
    for task in editorial_tasks:
        role_id = role_from_editorial_task(task)
        if role_id and role_id not in tasks_by_role:
            tasks_by_role[role_id] = task
    accepted = set(as_string_list(aggregate.get("accepted_roles")))
    missing = set(as_string_list(aggregate.get("missing_roles")))
    invalid_roles = {str(item.get("role_id") or "") for item in aggregate.get("invalid_results") or [] if isinstance(item, dict)}
    duplicate_roles = {str(item.get("role_id") or "") for item in aggregate.get("duplicate_role_results") or [] if isinstance(item, dict)}
    roles = sorted(
        set(as_string_list(aggregate.get("expected_roles")))
        | set(tasks_by_role)
        | accepted
        | missing
        | invalid_roles
        | duplicate_roles
    )
    result: list[dict[str, Any]] = []
    for role_id in roles:
        task = tasks_by_role.get(role_id, {})
        manifest = editorial_task_manifest(root, task)
        task_status = str(task.get("status") or manifest.get("status") or "")
        role = role_definition(role_id)
        status = task_status or "unknown"
        if role_id in accepted:
            status = "accepted"
        elif role_id in invalid_roles or task_status == "invalid":
            status = "invalid"
        elif role_id in missing:
            status = "missing"
        result.append(
            {
                "role_id": role_id,
                "display_name": role.get("display_name", role_id),
                "focus": role.get("focus", ""),
                "status": status,
                "task_id": str(task.get("task_id") or manifest.get("task_id") or ""),
                "task_status": task_status,
                "work_order_file": first_editorial_work_order_file(manifest),
                "result_file": first_string(manifest.get("allowed_output_paths") or task.get("allowed_output_paths")),
                "validate_command": ensure_longform_prefix(str(manifest.get("validate_command") or "")),
                "duplicate_result": role_id in duplicate_roles,
                "invalid_result": role_id in invalid_roles or task_status == "invalid",
                "accepted": role_id in accepted,
                "missing": role_id in missing,
            }
        )
    return result


def editorial_task_manifest(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    task_key = str(task.get("task_id") or task.get("manifest_file") or "")
    if not task_key:
        return {}
    try:
        payload = load_manifest(root, task_key)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def editorial_next_work_order(
    root: Path,
    task: dict[str, Any],
    manifest: dict[str, Any],
    chapter_number: int,
    status: str,
) -> dict[str, Any]:
    role_id = role_from_editorial_task(task) or role_from_editorial_task(manifest)
    role = role_definition(role_id)
    work_order_file = first_editorial_work_order_file(manifest)
    context_file = first_editorial_context_file(manifest)
    context = read_json(root / context_file) if context_file else {}
    if not isinstance(context, dict):
        context = {}
    result_file = first_string(manifest.get("allowed_output_paths"))
    payload = {
        "role_id": role_id,
        "role_display_name": role.get("display_name", role_id),
        "role_focus": role.get("focus", ""),
        "editorial_role": {
            "role_id": role_id,
            "display_name": role.get("display_name", role_id),
            "focus": role.get("focus", ""),
            "chapter_number": chapter_number,
            "status": status,
            "task_id": str(manifest.get("task_id") or task.get("task_id") or ""),
            "work_order_file": work_order_file,
            "context_file": context_file,
            "result_file": result_file,
            "output_schema": str(manifest.get("output_schema") or ""),
            "reviewer_instance_id": str(context.get("reviewer_instance_id") or ""),
            "context_digest_hash": str(context.get("context_digest_hash") or ""),
            "independence_mode": str(context.get("independence_mode") or ""),
            "review_round": int(context.get("review_round") or 0),
            "validate_command": ensure_longform_prefix(str(manifest.get("validate_command") or "")),
            "apply_command": ensure_longform_prefix(str(manifest.get("apply_command") or "")),
            "failure_next_command": ensure_longform_prefix(str(manifest.get("failure_next_command") or "")),
            "hard_boundaries": as_string_list(manifest.get("hard_boundaries")) or list(HARD_BOUNDARIES),
            "completion_report_template": [
                "Role result written:",
                "Validation command run:",
                "Validation result:",
                "Aggregate or next command:",
            ],
        },
    }
    if work_order_file:
        payload["sources"] = dedupe([str(task.get("manifest_file") or ""), work_order_file])
    return payload


def first_editorial_work_order_file(manifest: dict[str, Any]) -> str:
    for item in as_string_list(manifest.get("input_files")):
        if "50_workbench/editorial_reviews/agent_tasks/" in item and item.endswith(".md"):
            return item
    return ""


def first_editorial_context_file(manifest: dict[str, Any]) -> str:
    for item in as_string_list(manifest.get("input_files")):
        if "50_workbench/editorial_reviews/agent_tasks/" in item and item.endswith(".context.json"):
            return item
    return ""


def first_string(value: Any) -> str:
    items = as_string_list(value)
    return items[0] if items else ""


def readable_need_human_reasons(reasons: list[str]) -> list[dict[str, str]]:
    return [
        {
            "code": reason,
            "message": NEED_HUMAN_REASON_LABELS.get(reason, reason.replace("_", " ").strip().capitalize()),
        }
        for reason in reasons
    ]


def role_from_editorial_task(task: dict[str, Any]) -> str:
    task_id = str(task.get("task_id") or "")
    match = re.match(r"editorial_review:([^:]+):ch\d{3}:v\d+", task_id)
    if match:
        return match.group(1)
    outputs = task.get("allowed_output_paths") or []
    if outputs:
        name = Path(str(outputs[0])).name
        match = re.match(r"ch\d{3}\.([^.]+)\.json", name)
        if match:
            return match.group(1)
    return ""


def agent_task_board_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, dict[str, int]] = {}
    active: list[str] = []
    for task in tasks:
        task_type = str(task.get("task_type") or "unknown")
        status = str(task.get("status") or "unknown")
        by_type.setdefault(task_type, {})
        by_type[task_type][status] = by_type[task_type].get(status, 0) + 1
        if status in {"awaiting_agent", "submitted", "validated", "invalid"}:
            active.append(str(task.get("task_id") or ""))
    return {"total": len(tasks), "active_count": len(active), "by_type": by_type, "active_task_ids": active}


def latest_json_summary(directory: Path, chapter_number: int) -> dict[str, Any]:
    candidates: list[Path] = []
    for path in directory.glob("*.json"):
        payload = read_json(path)
        if isinstance(payload, dict) and int(payload.get("chapter_number") or 0) == chapter_number:
            candidates.append(path)
            continue
        if f"ch{chapter_number:03d}" in path.name:
            candidates.append(path)
    if not candidates:
        return {}
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    payload = read_json(path)
    if not isinstance(payload, dict):
        payload = {}
    return {
        "file": relative_path(directory.parents[1], path),
        "report_type": str(payload.get("report_type") or ""),
        "status": str(payload.get("status") or ""),
        "command": str(payload.get("command") or payload.get("last_pipeline") or ""),
        "created_at": str(payload.get("created_at") or payload.get("updated_at") or ""),
    }


def board_totals(chapters: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "chapters": len(chapters),
        "finalized": sum(1 for item in chapters if item.get("final_status") == "finalized"),
        "gate_failed": sum(1 for item in chapters if (item.get("gate_status") or {}).get("status") == "failed"),
        "need_human": sum(1 for item in chapters if (item.get("editorial") or {}).get("need_human")),
        "active_agent_tasks": sum(int((item.get("agent_tasks") or {}).get("active_count") or 0) for item in chapters),
    }


def count_values(items: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return counts


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def project_readiness_action(config: ConfigDocument, root: Path) -> dict[str, Any] | None:
    readiness = assess_project_readiness(config)
    if readiness.ready:
        return None
    if readiness.stage == "open_book":
        return base_action(
            status="ready_for_open_book",
            chapter_number=0,
            blocked_by="project_opening",
            waiting_for="cli",
            next_command="longform-engine open-book project.yaml",
            human_summary="Confirm the opening contract before project-level Agent design begins.",
        )
    task_type = readiness.required_task_type
    active_statuses = {"awaiting_agent", "submitted", "validated", "invalid"}
    if any(
        task.get("task_type") == task_type and task.get("status") in active_statuses
        for task in list_manifests(root, chapter_number=0)
    ):
        return None
    return base_action(
        status="ready_for_intelligence_task",
        chapter_number=0,
        task_type=task_type,
        blocked_by=readiness.stage,
        waiting_for="cli",
        next_command=project_intelligence_task_command(task_type),
        human_summary=(
            f"Project is not chapter-ready. Create the {task_type} Agent task; "
            f"current validation issues: {'; '.join(readiness.errors[:3])}"
        ),
    )


def project_intelligence_task_command(task_type: str) -> str:
    if task_type == "fanfiction_canon":
        return "longform-engine fanfiction canon-task project.yaml --input 50_workbench/fanfiction_sources/<source-file>"
    if task_type == "fanfiction_design":
        return "longform-engine fanfiction design-task project.yaml"
    if task_type == "character_expression_design":
        return "longform-engine character design-task project.yaml"
    return f"longform-engine intelligence task project.yaml --task-type {task_type}"


def chapter_direction_action(config: ConfigDocument, root: Path) -> dict[str, Any] | None:
    chapter_number = highest_finalized_chapter(root) + 1
    status = assess_chapter_direction(config, chapter_number)
    if not status["required"]:
        return None
    active = {
        "awaiting_agent",
        "submitted",
        "validated",
        "invalid",
    }
    if any(
        task.get("task_type") == "chapter_direction"
        and int(task.get("chapter_number") or 0) == chapter_number
        and task.get("status") in active
        for task in list_manifests(root, chapter_number=chapter_number)
    ):
        return None
    reasons = [str(item) for item in status["reasons"]]
    return base_action(
        status="ready_for_intelligence_task",
        chapter_number=chapter_number,
        task_type="chapter_direction",
        blocked_by="chapter_direction",
        waiting_for="cli",
        next_command=(
            "longform-engine intelligence task project.yaml "
            f"--task-type chapter_direction --chapter {chapter_number}"
        ),
        human_summary=(
            f"ch{chapter_number:03d} needs a human-selected direction before its writing task "
            f"({', '.join(reasons)})."
        ),
        trigger_reasons=reasons,
    )


def first_need_human_action(root: Path) -> dict[str, Any] | None:
    candidates: list[tuple[int, str, Path, dict[str, Any]]] = []
    for path in (root / "50_workbench" / "editorial_reviews").glob("ch*.aggregate.json"):
        payload = read_json(path)
        if not isinstance(payload, dict) or payload.get("need_human") is not True:
            continue
        chapter_number = int(payload.get("chapter_number") or chapter_from_name(path.name) or 0)
        if chapter_number <= 0:
            continue
        missing_roles = as_string_list(payload.get("missing_roles"))
        active_roles = {
            role_from_editorial_task(task)
            for task in list_manifests(root, chapter_number=chapter_number)
            if task.get("task_type") == "editorial_review"
            and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated"}
        }
        if missing_roles and set(missing_roles) <= active_roles:
            continue
        next_command = str(
            payload.get("next_command")
            or f"longform-engine editorial need-human project.yaml --chapter {chapter_number} --reason editorial_aggregate"
        )
        candidates.append((chapter_number, path.name, path, payload | {"next_command": next_command}))
    if not candidates:
        return None
    chapter_number, _, path, payload = sorted(candidates, key=lambda item: (item[0], item[1]))[0]
    reasons = payload.get("need_human_reasons") or payload.get("reasons") or []
    if not isinstance(reasons, list):
        reasons = [str(reasons)]
    reason_codes = [str(item) for item in reasons]
    action = base_action(
        status="need_human",
        chapter_number=chapter_number,
        blocked_by="editorial_need_human",
        waiting_for="human_review",
        next_command=str(payload.get("next_command") or ""),
        human_summary=f"ch{chapter_number:03d} requires human editorial review.",
        sources=[relative_path(root, path)],
        need_human_reasons=reason_codes,
    )
    action.update(
        {
            "expected_roles": as_string_list(payload.get("expected_roles")),
            "accepted_roles": as_string_list(payload.get("accepted_roles")),
            "missing_roles": as_string_list(payload.get("missing_roles")),
            "duplicate_role_results": payload.get("duplicate_role_results") or [],
            "invalid_results": payload.get("invalid_results") or [],
            "severity_counts": payload.get("severity_counts") or {"P0": 0, "P1": 0, "P2": 0},
            "conditional_passes": int(payload.get("conditional_passes") or 0),
            "result_count": int(payload.get("result_count") or 0),
            "need_human_reasons_readable": readable_need_human_reasons(reason_codes),
        }
    )
    return action


def first_active_agent_task(root: Path) -> dict[str, Any] | None:
    tasks = [
        task
        for task in list_manifests(root)
        if str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
    ]
    if not tasks:
        return None
    task = sorted(tasks, key=task_sort_key)[0]
    return agent_task_action(root, task)


def agent_task_action(root: Path, task: dict[str, Any]) -> dict[str, Any]:
    """Render one indexed active task as a production action."""

    manifest = load_manifest(root, str(task.get("task_id") or task.get("manifest_file") or ""))
    validation = validate_manifest_strict(root, manifest, strict=True)
    status = str(task.get("status") or manifest.get("status") or "awaiting_agent")
    task_type = str(task.get("task_type") or manifest.get("task_type") or "agent_task")
    chapter_number = int(task.get("chapter_number") or manifest.get("chapter_number") or 0)
    if not validation.ok:
        action = base_action(
            status="agent_task_contract_invalid",
            chapter_number=chapter_number,
            blocked_by="task_contract_invalid",
            waiting_for="cli_task_regeneration",
            task_id=str(manifest.get("task_id") or task.get("task_id") or ""),
            task_type=task_type,
            input_files=as_string_list(manifest.get("input_files")),
            context_policy=dict(manifest.get("context_policy") or {}),
            allowed_output_paths=as_string_list(manifest.get("allowed_output_paths")),
            output_schema=str(manifest.get("output_schema") or ""),
            validate_command=str(manifest.get("validate_command") or ""),
            apply_command=str(manifest.get("apply_command") or ""),
            failure_next_command=str(manifest.get("failure_next_command") or ""),
            next_command=str(manifest.get("failure_next_command") or ""),
            human_summary=(
                f"ch{chapter_number:03d} {task_type} has an invalid Agent task contract and must be regenerated."
            ),
            sources=[str(task.get("manifest_file") or "")],
        )
        action["contract_errors"] = list(validation.errors)
        return action
    next_command = command_for_task_status(manifest, status)
    if task_type == "humanize_semantic_review" and status == "invalid":
        validation = read_json(
            root
            / "50_workbench"
            / "humanizer_tasks"
            / f"ch{chapter_number:03d}.semantic_review.validation.json"
        )
        if isinstance(validation, dict) and validation.get("next_command"):
            next_command = str(validation["next_command"])
    if task_type == "reader_payoff_review" and status == "invalid":
        validation = read_json(
            root
            / "50_workbench"
            / "quality_reviews"
            / f"ch{chapter_number:03d}.reader_payoff.validation.json"
        )
        if isinstance(validation, dict) and validation.get("next_command"):
            next_command = str(validation["next_command"])
    action = base_action(
        status=f"agent_task_{status}",
        chapter_number=chapter_number,
        blocked_by="agent_task_invalid" if status == "invalid" else "agent_task",
        waiting_for=waiting_for_task_status(task_type, status),
        task_id=str(manifest.get("task_id") or task.get("task_id") or ""),
        task_type=task_type,
        input_files=as_string_list(manifest.get("input_files")),
        context_policy=dict(manifest.get("context_policy") or {}),
        allowed_output_paths=as_string_list(manifest.get("allowed_output_paths")),
        output_schema=str(manifest.get("output_schema") or ""),
        validate_command=str(manifest.get("validate_command") or ""),
        apply_command=str(manifest.get("apply_command") or ""),
        failure_next_command=str(manifest.get("failure_next_command") or ""),
        hard_boundaries=as_string_list(manifest.get("hard_boundaries")) or list(HARD_BOUNDARIES),
        scope=manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {},
        canonical_targets=as_string_list(manifest.get("canonical_targets")),
        requires_human_apply=bool(manifest.get("requires_human_apply")),
        next_command=next_command,
        human_summary=agent_task_summary(chapter_number, task_type, status),
        sources=[str(task.get("manifest_file") or "")],
    )
    if task_type == "editorial_review":
        action.update(editorial_next_work_order(root, task, manifest, chapter_number, status))
    return action


def chapter_workflow_action(config: ConfigDocument, root: Path) -> dict[str, Any] | None:
    """Route an unfinished chapter by its evidence-backed stage before consulting generic task priority."""

    chapter_numbers = sorted(
        {
            chapter_from_name(path.name)
            for path in (root / MANUSCRIPT_DIR / "draft").glob("ch*.md")
        }
        | {
            chapter_from_name(path.parent.name)
            for path in (root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json")
        }
    )
    for chapter_number in chapter_numbers:
        if chapter_number <= 0 or final_chapter_exists(root, chapter_number):
            continue
        stage = derive_chapter_stage(config, root, chapter_number)
        stage_name = str(stage.get("stage") or "")
        if stage_name == "semantic_review_pending":
            tasks = active_chapter_tasks(root, chapter_number, {"semantic_review"})
            if tasks:
                return agent_task_action(root, sorted(tasks, key=task_sort_key)[0])
            command = f"longform-engine gate semantic-task project.yaml --chapter {chapter_number}"
            return base_action(
                status="ready_for_semantic_review_task",
                chapter_number=chapter_number,
                blocked_by="semantic_review_pending",
                waiting_for="cli",
                task_type="semantic_review",
                next_command=command,
                failure_next_command=command,
                human_summary=f"ch{chapter_number:03d} requires an evidence-backed semantic review task.",
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "payoff_pending":
            return reader_payoff_action(config, root)
        if stage_name == "editorial_pending":
            return editorial_review_action(config, root)
        if stage_name == "ready_to_finalize":
            return first_gate_action(root)
        if stage_name == "repair_pending":
            replacements = active_chapter_tasks(root, chapter_number, {"repair", "humanize", "content_expand"})
            if replacements:
                return agent_task_action(root, sorted(replacements, key=task_sort_key)[0])
            return first_gate_action(root)
    return None


def derive_chapter_stage(config: ConfigDocument, root: Path, chapter_number: int) -> dict[str, Any]:
    """Derive one chapter stage from canonical artifacts and current candidate evidence."""

    closure = root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json"
    final = root / MANUSCRIPT_DIR / FINAL_LANE / f"ch{chapter_number:03d}.md"
    ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    draft = root / MANUSCRIPT_DIR / "draft" / f"ch{chapter_number:03d}.md"
    if closure.exists():
        return {"stage": "closed", "sources": [relative_path(root, closure)]}
    if final.exists() and not ledger.exists():
        return {"stage": "finalized_needs_semantic_bundle", "sources": [relative_path(root, final)]}
    if final.exists():
        return {"stage": "finalized_needs_close", "sources": [relative_path(root, ledger)]}
    gate = read_json(gate_path)
    if gate_path.is_file() and isinstance(gate, dict) and gate:
        semantic = gate.get("agent_semantic_review")
        declared_stage = str(gate.get("workflow_stage") or "")
        failures = gate.get("failures") if isinstance(gate.get("failures"), list) else []
        only_semantic_failure = bool(failures) and all(
            isinstance(item, dict) and item.get("code") == "semantic_review_required"
            for item in failures
        )
        semantic_pending = declared_stage == "semantic_review_pending" or (
            not declared_stage
            and only_semantic_failure
            and isinstance(semantic, dict)
            and semantic.get("required") is True
            and str(semantic.get("status") or "") != "applied"
        )
        if semantic_pending:
            return {"stage": "semantic_review_pending", "sources": [relative_path(root, gate_path)]}
        if gate.get("passed") is True or gate_has_waiver(gate):
            payoff = reader_payoff_review_status(config, chapter_number=chapter_number)
            if payoff.get("required") and not payoff.get("passed"):
                return {"stage": "payoff_pending", "sources": [relative_path(root, gate_path)]}
            reasons = editorial_review_required_reasons(config, chapter_number=chapter_number)
            if reasons:
                aggregate = read_json(
                    root / "50_workbench" / "editorial_reviews" / f"ch{chapter_number:03d}.aggregate.json"
                )
                editorial_complete = (
                    isinstance(aggregate, dict)
                    and aggregate.get("need_human") is not True
                    and not aggregate.get("missing_roles")
                    and int(aggregate.get("result_count") or 0) > 0
                )
                if not editorial_complete:
                    return {"stage": "editorial_pending", "sources": [relative_path(root, gate_path)]}
            return {"stage": "ready_to_finalize", "sources": [relative_path(root, gate_path)]}
        return {"stage": "repair_pending", "sources": [relative_path(root, gate_path)]}
    if draft.exists():
        return {"stage": "writing_pending", "sources": [relative_path(root, draft)]}
    return {"stage": "writing_pending", "sources": []}


def active_chapter_tasks(root: Path, chapter_number: int, task_types: set[str]) -> list[dict[str, Any]]:
    return [
        task
        for task in list_manifests(root, chapter_number=chapter_number)
        if str(task.get("task_type") or "") in task_types
        and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
    ]


def chapter_semantic_lifecycle_action(root: Path) -> dict[str, Any] | None:
    """Require one semantic bundle and explicit close for every finalized chapter."""

    final_dir = root / "40_manuscript" / FINAL_LANE
    for final_file in sorted(final_dir.glob("ch*.md")):
        chapter_number = chapter_from_name(final_file.name)
        if chapter_number <= 0:
            continue
        ledger_file = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
        closure_file = root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json"
        if not ledger_file.exists():
            active = [
                task
                for task in list_manifests(root, chapter_number=chapter_number)
                if task.get("task_type") == "chapter_semantic"
                and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
            ]
            if active:
                return agent_task_action(root, sorted(active, key=task_sort_key)[0])
            command = f"longform-engine chapter semantic-task project.yaml --chapter {chapter_number}"
            return base_action(
                status="ready_for_chapter_semantic_task",
                chapter_number=chapter_number,
                blocked_by="semantic_ledger_missing",
                waiting_for="cli",
                task_type="chapter_semantic",
                output_schema="chapter_semantic_bundle_v1",
                next_command=command,
                failure_next_command=command,
                human_summary=(
                    f"ch{chapter_number:03d} is finalized but needs one unified evidence-bound semantic extraction."
                ),
                sources=[relative_path(root, final_file)],
            )
        if not closure_file.exists():
            command = f"longform-engine chapter close project.yaml --chapter {chapter_number} --approved-by human"
            return base_action(
                status="awaiting_chapter_close",
                chapter_number=chapter_number,
                blocked_by="chapter_not_closed",
                waiting_for="human_close",
                task_type="chapter_semantic",
                apply_command=command,
                next_command=command,
                human_summary=(
                    f"ch{chapter_number:03d} semantic state is materialized and waits for explicit chapter close."
                ),
                sources=[relative_path(root, ledger_file)],
            )
    return None


def reader_payoff_action(config: ConfigDocument, root: Path) -> dict[str, Any] | None:
    """Schedule a payoff review after gate pass and before finalize."""

    candidates: list[int] = []
    for path in (root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json"):
        chapter_number = chapter_from_name(path.parent.name)
        if chapter_number <= 0 or final_chapter_exists(root, chapter_number):
            continue
        payload = read_json(path)
        if isinstance(payload, dict) and (payload.get("passed") is True or gate_has_waiver(payload)):
            candidates.append(chapter_number)
    for chapter_number in sorted(set(candidates)):
        status = reader_payoff_review_status(config, chapter_number=chapter_number)
        if not status.get("required") or status.get("passed"):
            continue
        if any(
            task.get("task_type") == "reader_payoff_review"
            and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
            for task in list_manifests(root, chapter_number=chapter_number)
        ):
            continue
        output = str(status.get("output_file") or "")
        return base_action(
            status="ready_for_reader_payoff_task",
            chapter_number=chapter_number,
            blocked_by="reader_payoff_review_required",
            waiting_for="cli",
            task_type="reader_payoff_review",
            allowed_output_paths=[output] if output else [],
            output_schema="reader_payoff_review_v1",
            validate_command=(
                f"longform-engine quality payoff-validate project.yaml --chapter {chapter_number} "
                f"--file {output}"
                if output
                else ""
            ),
            apply_command=(
                f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human"
            ),
            failure_next_command=(
                f"longform-engine repair-chapter project.yaml --chapter {chapter_number} --plan-only"
            ),
            next_command=f"longform-engine quality payoff-task project.yaml --chapter {chapter_number}",
            human_summary=(
                f"ch{chapter_number:03d} passed deterministic gates and needs observed reader-payoff review."
            ),
            sources=[str(status.get("report_file") or "")],
        )
    return None


def editorial_review_action(config: ConfigDocument, root: Path) -> dict[str, Any] | None:
    """Schedule required risk-based editorial review after payoff and before finalize."""

    candidates: list[int] = []
    for path in (root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json"):
        chapter_number = chapter_from_name(path.parent.name)
        if chapter_number <= 0 or final_chapter_exists(root, chapter_number):
            continue
        payload = read_json(path)
        if isinstance(payload, dict) and (payload.get("passed") is True or gate_has_waiver(payload)):
            candidates.append(chapter_number)
    for chapter_number in sorted(set(candidates)):
        payoff_status = reader_payoff_review_status(config, chapter_number=chapter_number)
        if payoff_status.get("required") and not payoff_status.get("passed"):
            continue
        reasons = editorial_review_required_reasons(config, chapter_number=chapter_number)
        if not reasons:
            continue
        aggregate = read_json(
            root / "50_workbench" / "editorial_reviews" / f"ch{chapter_number:03d}.aggregate.json"
        )
        if (
            isinstance(aggregate, dict)
            and aggregate.get("need_human") is not True
            and not aggregate.get("missing_roles")
            and int(aggregate.get("result_count") or 0) > 0
        ):
            continue
        tasks = [
            task
            for task in list_manifests(root, chapter_number=chapter_number)
            if task.get("task_type") == "editorial_review"
            and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
        ]
        if tasks:
            continue
        return base_action(
            status="ready_for_editorial_review",
            chapter_number=chapter_number,
            task_type="editorial_review",
            blocked_by="editorial_review_required",
            waiting_for="cli",
            next_command=f"longform-engine editorial review project.yaml --chapter {chapter_number}",
            human_summary=f"ch{chapter_number:03d} requires risk-based editorial role selection before finalize.",
            trigger_reasons=reasons,
        )
    return None


def first_gate_action(root: Path) -> dict[str, Any] | None:
    candidates: list[tuple[int, Path, dict[str, Any]]] = []
    for path in (root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json"):
        chapter_number = chapter_from_name(path.parent.name)
        if chapter_number <= 0 or final_chapter_exists(root, chapter_number):
            continue
        payload = read_json(path)
        if isinstance(payload, dict):
            candidates.append((chapter_number, path, payload))
    for chapter_number, path, payload in sorted(candidates, key=lambda item: item[0]):
        if payload.get("passed") is False and not gate_has_waiver(payload):
            next_command = str(
                payload.get("next_command")
                or f"longform-engine repair-chapter project.yaml --chapter {chapter_number} --plan-only"
            )
            return base_action(
                status="gate_failed",
                chapter_number=chapter_number,
                blocked_by="gate_failed",
                waiting_for="repair_plan_or_candidate",
                next_command=ensure_longform_prefix(next_command),
                failure_next_command=ensure_longform_prefix(next_command),
                human_summary=f"ch{chapter_number:03d} failed gate and needs repair.",
                sources=[relative_path(root, path)],
            )
        if payload.get("passed") is True or gate_has_waiver(payload):
            validated_candidates = active_chapter_tasks(
                root,
                chapter_number,
                {"chapter_write", "repair", "humanize", "content_expand"},
            )
            validated_candidates = [
                task for task in validated_candidates if str(task.get("status") or "") == "validated"
            ]
            candidate_apply = ""
            if len(validated_candidates) == 1:
                candidate_manifest = load_manifest(root, str(validated_candidates[0].get("task_id") or ""))
                candidate_apply = str(candidate_manifest.get("apply_command") or "")
            next_command = candidate_apply or str(
                payload.get("next_command")
                or f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human"
            )
            return base_action(
                status="awaiting_finalize",
                chapter_number=chapter_number,
                blocked_by="approved_draft_not_finalized",
                waiting_for="human_finalize",
                next_command=ensure_longform_prefix(next_command),
                apply_command=ensure_longform_prefix(next_command),
                human_summary=f"ch{chapter_number:03d} passed gate and waits for chapter finalize.",
                sources=[relative_path(root, path)],
            )
    return None


def first_draft_without_gate_action(root: Path) -> dict[str, Any] | None:
    for path in sorted((root / MANUSCRIPT_DIR / "draft").glob("ch*.md")):
        chapter_number = chapter_from_name(path.name)
        if chapter_number <= 0 or final_chapter_exists(root, chapter_number):
            continue
        gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
        if gate_path.exists():
            continue
        return base_action(
            status="awaiting_gate",
            chapter_number=chapter_number,
            blocked_by="draft_without_gate",
            waiting_for="gate_check",
            next_command=f"longform-engine gate-check project.yaml --chapter {chapter_number}",
            validate_command=f"longform-engine gate-check project.yaml --chapter {chapter_number}",
            human_summary=f"ch{chapter_number:03d} has a draft and needs gate-check.",
            sources=[relative_path(root, path)],
        )
    return None


def first_writing_task_action(root: Path) -> dict[str, Any] | None:
    for path in sorted((root / "50_workbench" / "writing_tasks").glob("ch*.json")):
        if path.name.endswith(".agent_task.json"):
            continue
        chapter_number = chapter_from_name(path.name)
        if chapter_number <= 0:
            continue
        if final_chapter_exists(root, chapter_number) or draft_chapter_exists(root, chapter_number):
            continue
        payload = read_json(path)
        task_md = path.with_suffix(".md")
        draft_path = root / "50_workbench" / "agent_drafts" / f"ch{chapter_number:03d}.codex.md"
        next_command = (
            str(payload.get("next_command"))
            if isinstance(payload, dict) and payload.get("next_command")
            else f"longform-engine draft submit project.yaml --chapter {chapter_number} --file {relative_path(root, draft_path)} --agent codex"
        )
        inputs = [relative_path(root, path)]
        if task_md.exists():
            inputs.append(relative_path(root, task_md))
        return base_action(
            status="awaiting_agent_draft",
            chapter_number=chapter_number,
            blocked_by="writing_task",
            waiting_for="agent_draft",
            task_type="chapter_write",
            input_files=inputs,
            allowed_output_paths=[relative_path(root, draft_path)],
            output_schema="markdown_chapter_only",
            validate_command=ensure_longform_prefix(next_command),
            apply_command=f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
            failure_next_command=f"longform-engine repair-chapter project.yaml --chapter {chapter_number} --plan-only",
            next_command=ensure_longform_prefix(next_command),
            human_summary=f"ch{chapter_number:03d} writing task exists and waits for an Agent draft.",
            sources=[relative_path(root, path)],
        )
    return None


def base_action(
    *,
    status: str,
    chapter_number: int,
    blocked_by: str,
    waiting_for: str,
    next_command: str,
    human_summary: str,
    task_id: str = "",
    task_type: str = "",
    input_files: list[str] | None = None,
    context_policy: dict[str, Any] | None = None,
    allowed_output_paths: list[str] | None = None,
    output_schema: str = "",
    validate_command: str = "",
    apply_command: str = "",
    failure_next_command: str = "",
    hard_boundaries: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    canonical_targets: list[str] | None = None,
    requires_human_apply: bool = False,
    sources: list[str] | None = None,
    need_human_reasons: list[str] | None = None,
    trigger_reasons: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "chapter_number": chapter_number,
        "blocked_by": blocked_by,
        "waiting_for": waiting_for,
        "task_id": task_id,
        "task_type": task_type,
        "input_files": input_files or [],
        "context_policy": context_policy or {},
        "allowed_output_paths": allowed_output_paths or [],
        "output_schema": output_schema,
        "validate_command": validate_command,
        "apply_command": apply_command,
        "failure_next_command": failure_next_command,
        "hard_boundaries": hard_boundaries or list(HARD_BOUNDARIES),
        "scope": scope or ({"kind": "chapter", "chapter_number": chapter_number} if chapter_number else {}),
        "canonical_targets": canonical_targets or [],
        "requires_human_apply": requires_human_apply,
        "next_command": ensure_longform_prefix(next_command),
        "human_summary": human_summary,
        "sources": sources or [],
        "need_human_reasons": need_human_reasons or [],
        "trigger_reasons": trigger_reasons or [],
    }


def task_sort_key(task: dict[str, Any]) -> tuple[int, int, int, str, str]:
    status = str(task.get("status") or "")
    task_type = str(task.get("task_type") or "")
    task_priority = TASK_PRIORITY.get(task_type, 999)
    if status == "validated" and task_type == "chapter_write":
        task_priority = 999
    return (
        int(task.get("chapter_number") or 0),
        STATUS_PRIORITY.get(status, 99),
        task_priority,
        str(task.get("updated_at") or task.get("created_at") or ""),
        str(task.get("task_id") or ""),
    )


def command_for_task_status(manifest: dict[str, Any], status: str) -> str:
    if status == "invalid":
        return str(manifest.get("failure_next_command") or "")
    if status == "validated":
        return str(manifest.get("apply_command") or "")
    return str(manifest.get("validate_command") or "")


def waiting_for_task_status(task_type: str, status: str) -> str:
    if status == "invalid":
        return "regenerated_agent_output"
    if status == "submitted":
        return "validation"
    if status == "validated":
        return "apply_command"
    return TASK_WAITING_FOR.get(task_type, "agent_output")


def agent_task_summary(chapter_number: int, task_type: str, status: str) -> str:
    subject = f"ch{chapter_number:03d}" if chapter_number > 0 else "project/range scope"
    if status == "awaiting_agent":
        return f"{subject} waits for Agent output for {task_type}."
    if status == "submitted":
        return f"{subject} has submitted Agent output for {task_type}; run validation."
    if status == "validated":
        return f"{subject} has validated {task_type}; run the declared apply command."
    if status == "invalid":
        return f"{subject} has invalid {task_type}; regenerate or follow failure command."
    return f"{subject} is blocked by {task_type}."


def highest_finalized_chapter(root: Path) -> int:
    chapters = [chapter_from_name(path.name) for path in (root / MANUSCRIPT_DIR / FINAL_LANE).glob("ch*.md")]
    return max([item for item in chapters if item > 0], default=0)


def final_chapter_exists(root: Path, chapter_number: int) -> bool:
    return manuscript_chapter_exists(root, FINAL_LANE, chapter_number)


def draft_chapter_exists(root: Path, chapter_number: int) -> bool:
    return manuscript_chapter_exists(root, "draft", chapter_number)


def manuscript_chapter_exists(root: Path, lane: str, chapter_number: int) -> bool:
    return (root / MANUSCRIPT_DIR / lane / f"ch{chapter_number:03d}.md").exists()


def gate_has_waiver(gate: dict[str, Any]) -> bool:
    return bool(gate.get("waiver") or gate.get("waived") or gate.get("override"))


def chapter_from_name(value: str) -> int:
    match = re.search(r"ch(\d{1,4})", value)
    return int(match.group(1)) if match else 0


def ensure_longform_prefix(command: str) -> str:
    command = re.sub(r"\s+", " ", str(command or "").strip())
    if not command:
        return ""
    if command.startswith("longform-engine "):
        return command
    if command.startswith("python -m longform_engine.cli "):
        return "longform-engine " + command.removeprefix("python -m longform_engine.cli ")
    if command.startswith("python3 -m longform_engine.cli "):
        return "longform-engine " + command.removeprefix("python3 -m longform_engine.cli ")
    return f"longform-engine {command}"


def as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def relative_path(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()
