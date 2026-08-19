"""Production experience orchestration helpers."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from hashlib import sha256
import json
import re
from pathlib import Path
from typing import Any

from longform_engine.agent_pipeline import (
    compile_production_agent_package,
    production_package_payload,
    require_agent_first_production_pipeline,
    validate_production_agent_result,
)
from longform_engine.agent_protocols import HARD_BOUNDARIES, output_protocol_for_task
from longform_engine.agent_tasks import (
    list_manifests,
    load_manifest,
    manifest_chapter_number,
    manifest_commands,
    manifest_context,
    manifest_input_paths,
    manifest_output,
    manifest_policy,
    manifest_role,
    status_summary,
    task_reconciliation_status,
    validate_manifest_strict,
)
from longform_engine.config import ConfigDocument
from longform_engine.completion import fast_completion_marker
from longform_engine.creative import expand_check, humanize_check, humanize_semantic_validate
from longform_engine.editorial import (
    editorial_aggregate,
    editorial_review,
    editorial_review_required_reasons,
    editorial_submit_review,
)
from longform_engine.editorial.pipeline import context_digest_hash, role_definition
from longform_engine.gates import (
    gate_check,
    semantic_pacing_task_is_current,
    semantic_pacing_validate,
    semantic_review_validate,
)
from longform_engine.intelligence import (
    assess_chapter_direction,
    assess_project_readiness,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.lengths import compile_length_forecast
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.quality import (
    reader_payoff_review_status,
    reader_payoff_task,
    reader_payoff_task_is_current,
    reader_payoff_validate,
)
from longform_engine.repair_coordination import (
    editorial_human_resolution_reasons,
    next_repair_round,
    repair_attempt_status,
    repair_plan_status,
    review_barrier_status,
)
from longform_engine.roles import load_role_registry, session_directive
from longform_engine.semantic import semantic_task as chapter_semantic_task
from longform_engine.semantic import semantic_validate as chapter_semantic_validate
from longform_engine.storage import recovery_status, resolve_project_root
from longform_engine.storage.layout import (
    existing_manuscript_chapter_path,
    list_canonical_chapter_files,
    list_finalized_chapter_files,
    manuscript_chapter_path,
)


TASK_WAITING_FOR = {
    "book_ideation": "human_selected_creative_decision_markdown",
    "fanfiction_canon": "fanfiction_source_canon_json",
    "fanfiction_design": "fanfiction_design_markdown",
    "book_design": "book_design_markdown",
    "outline_design": "outline_design_markdown",
    "outline_extension": "outline_extension_markdown",
    "chapter_direction": "human_selected_chapter_direction_markdown",
    "outline_revision": "outline_revision_markdown",
    "research_synthesis": "research_synthesis_json",
    "style_analysis": "style_analysis_markdown",
    "adaptation_analysis": "adaptation_analysis_markdown",
    "design_semantic_compile": "canonical_delta_json",
    "chapter_write": "agent_draft",
    "repair_plan_synthesis": "repair_plan_markdown",
    "repair": "repair_candidate",
    "humanize": "humanized_candidate",
    "humanize_semantic_review": "humanizer_semantic_review_json",
    "reader_payoff_review": "reader_payoff_review_json",
    "content_expand": "expanded_candidate",
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
    "outline_extension": 8,
    "chapter_direction": 9,
    "outline_revision": 5,
    "research_synthesis": 4,
    "style_analysis": 5,
    "adaptation_analysis": 6,
    "design_semantic_compile": 7,
    "chapter_write": 10,
    "repair_plan_synthesis": 19,
    "repair": 20,
    "humanize": 21,
    "humanize_semantic_review": 22,
    "content_expand": 23,
    "semantic_review": 29,
    "pacing_review": 30,
    "reader_payoff_review": 31,
    "editorial_review": 40,
}

STATUS_PRIORITY = {
    "invalid": 1,
    "awaiting_agent": 2,
    "submitted": 3,
    "validated": 4,
    "approved": 5,
}

MANUSCRIPT_DIR = "40_manuscript"

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

    require_agent_first_production_pipeline()
    root = resolve_project_root(config)
    recovery = recovery_status(config)
    if recovery["blocked"]:
        lock_state = str(recovery["lock"].get("state") or "absent")
        return base_action(
            status="project_busy" if lock_state == "active" else "project_recovery_required",
            chapter_number=highest_finalized_chapter(root),
            blocked_by="active_project_lock" if lock_state == "active" else "storage_recovery_required",
            waiting_for="external_process" if lock_state == "active" else "human_approved_recovery",
            next_command=str(recovery.get("next_command") or ""),
            human_summary="; ".join(str(item) for item in recovery.get("blockers") or []),
            sources=[
                str(recovery["lock"].get("path") or ""),
                *[
                    str(item.get("path") or "")
                    for item in recovery["transactions"]
                    if item.get("state") != "terminal"
                ],
            ],
        )
    completion_state, completion = fast_completion_marker(config)
    if completion_state == "approved":
        return base_action(
            status="book_completed",
            chapter_number=int(completion["latest_final_chapter"]),
            blocked_by="none",
            waiting_for="none",
            next_command="",
            human_summary=(
                f"Book completion is human-approved at {completion['total_content_characters']} content characters."
            ),
        )
    if completion_state == "invalid":
        return base_action(
            status="need_human",
            chapter_number=int(completion.get("latest_final_chapter") or 0),
            blocked_by="stale_book_completion_approval",
            waiting_for="human",
            next_command="longform-engine book completion-status project.yaml",
            human_summary="The book completion approval no longer matches its immutable final evidence.",
        )
    action = (
        first_need_human_action(root)
        or task_lifecycle_reconciliation_action(root)
        or chapter_semantic_lifecycle_action(root)
        or chapter_workflow_action(config, root)
        or project_readiness_action(config, root)
        or rolling_outline_action(config, root)
        or chapter_direction_action(config, root)
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


def task_lifecycle_reconciliation_action(root: Path) -> dict[str, Any] | None:
    """Expose a read-only next command for explicit parent-child projection drift."""

    chapters = sorted(
        {
            manifest_chapter_number(task)
            for task in list_manifests(root)
            if str((task.get("scope") or {}).get("kind") or "") == "chapter"
        }
    )
    for chapter_number in chapters:
        if chapter_number <= 0:
            continue
        status = task_reconciliation_status(root, chapter_number=chapter_number)
        if status.get("status") == "need_human":
            return base_action(
                status="need_human",
                chapter_number=chapter_number,
                blocked_by="agent_task_lineage_ambiguous",
                waiting_for="human_decision",
                next_command="",
                human_summary="; ".join(str(item) for item in status.get("errors") or []),
                sources=[
                    str(task.get("manifest_file") or "")
                    for task in list_manifests(root, chapter_number=chapter_number)
                    if task.get("consumes_task_id") or task.get("consumed_by_task_id")
                ],
            )
        if status.get("recoverable"):
            command = str(status.get("next_command") or "")
            relation = status["recoverable"][0]
            return base_action(
                status="agent_task_lifecycle_reconciliation_required",
                chapter_number=chapter_number,
                blocked_by="agent_task_parent_projection_stale",
                waiting_for="cli",
                task_id=str(relation.get("parent_task_id") or ""),
                task_type="agent_task_reconcile",
                next_command=command,
                failure_next_command=command,
                human_summary=(
                    f"ch{chapter_number:03d} has an explicit hash-proven child task, but its parent "
                    "projection still needs deterministic reconciliation."
                ),
                sources=[
                    str(relation.get("parent_task_id") or ""),
                    str(relation.get("child_task_id") or ""),
                ],
            )
    return None


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


def agent_task_brief(
    config: ConfigDocument,
    task: str | Path,
    *,
    host: str = "codex",
) -> dict[str, Any]:
    """Render one readiness-authorized, role-isolated Agent work order."""

    root = resolve_project_root(config)
    manifest = load_manifest(root, task)
    entry = manifest_entry(root, task, manifest)
    validation = validate_manifest_strict(root, manifest, strict=True)
    if not validation.ok:
        raise ValueError("Agent task contract is invalid: " + "; ".join(validation.errors))
    package = compile_production_agent_package(root, manifest, host=host)
    integrated = production_package_payload(package)
    policy = manifest_policy(manifest)
    output = manifest_output(manifest)
    payload = {
        "schema_version": 1,
        "renderer": "agent_task_brief_v4",
        "read_only": True,
        "manifest_file": str(entry.get("manifest_file") or manifest_file_from_task(root, task)),
        "task_id": package.task_id,
        "task_type": package.task_type,
        "scope": manifest.get("scope") or {},
        "status": str(entry.get("status") or "awaiting_agent"),
        "io": {
            "inputs": [asdict(item) for item in package.context.sources],
            "output": dict(output),
            "context_hash": package.context.context_hash,
        },
        "policy": {
            "boundary_profile": policy.get("boundary_profile") or {},
            "canonical_targets": list(policy.get("canonical_targets") or []),
            "requires_human_apply": bool(policy.get("requires_human_apply")),
            "context": dict(manifest_context(package.context.effective_manifest)),
        },
        "budget": dict(package.prompt.payload.get("budget") or {}),
        "executable": bool(package.prompt.payload.get("executable", True)),
        "blocked_by": (
            "prompt_budget_exceeded"
            if not bool(package.prompt.payload.get("executable", True))
            else ""
        ),
        "session": dict(package.session),
        "commands": {
            "result_validate": (
                f"longform-engine agent-task result-validate project.yaml {package.task_id} "
                f"--file {package.output_contract.output_path}"
            ),
            "validate": ensure_longform_prefix(package.output_contract.validate_command),
            "apply": ensure_longform_prefix(package.output_contract.apply_command),
            "failure": ensure_longform_prefix(package.output_contract.failure_command),
        },
        "role": {
            "id": package.role_id,
            "version": package.role_version,
            "contract_hash": package.role_contract_hash,
            "independence_mode": package.independence_mode,
            "session_policy": str(package.session.get("policy") or ""),
            "overlay_hash": package.project_overlay_hash,
            "compiled_prompt_hash": package.prompt_hash,
        },
        "host": package.host_work_order.host,
        "manifest_validation": {
            "strict": True,
            "ok": True,
            "errors": [],
            "warnings": list(validation.warnings),
        },
        "result_template": package.result_template,
        "pipeline": integrated,
        "work_order_markdown": package.host_work_order.markdown,
    }
    payload["next_command"] = (
        payload["commands"]["failure"]
        if not payload["executable"]
        else payload["commands"]["result_validate"]
    )
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
    if status == "book_completed":
        return {"kind": "stop", "reason": "book_completed"}
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
        return {
            "kind": "execute",
            "action": "agent_result_validate",
            "command": action.get("protocol_validate_command") or action.get("next_command"),
            "output_path": output_path,
        }
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
    "chapter_semantic": "chapter_semantic_validate",
    "editorial_review": "editorial_submit_review",
    "pacing_review": "pacing_semantic_validate",
    "semantic_review": "gate_semantic_validate",
    "book_ideation": "intelligence_validate",
    "book_design": "intelligence_validate",
    "outline_design": "intelligence_validate",
    "outline_extension": "intelligence_validate",
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
    if command == "agent_result_validate":
        manifest = load_manifest(root, str(action.get("task_id") or ""))
        return serialize_loop_result(
            root,
            validate_production_agent_result(
                root,
                manifest,
                result_file=require_loop_output_path(output_path),
            ),
        )
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
    final_status = (
        "finalized"
        if existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None
        else "missing"
    )
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
        "chapter_semantic_status": task_lane_status(
            root,
            chapter_number,
            tasks,
            ("chapter_semantic",),
            (
                "50_workbench/semantic_tasks/ch{chapter}.semantic_bundle.json",
                "30_state/semantic_ledger/ch{chapter}.json",
            ),
        ),
        "semantic_pacing_status": task_lane_status(root, chapter_number, tasks, ("pacing_review",), ("50_workbench/gate_artifacts/ch{chapter}/semantic_pacing_result.json", "50_workbench/gate_artifacts/ch{chapter}/semantic_pacing_validation.json")),
        "editorial": editorial_board_status(root, chapter_number, tasks),
        "agent_tasks": agent_task_board_summary(tasks),
        "latest_transaction": latest_json_summary(root / "70_runtime" / "transactions", chapter_number),
        "latest_report": latest_json_summary(root / "70_runtime" / "run_reports", chapter_number),
    }


def manifest_entry(root: Path, task: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    task_text = str(task)
    task_id = str(manifest.get("task_id") or "")
    for entry in list_manifests(root, chapter_number=manifest_chapter_number(manifest) or None):
        if entry.get("task_id") == task_text or entry.get("task_id") == task_id:
            return entry
    return {}


def manifest_file_from_task(root: Path, task: str | Path) -> str:
    task_path = Path(task)
    if task_path.suffix.lower() != ".json":
        return ""
    return relative_path(root, task_path)




def max_known_chapter(root: Path) -> int:
    chapters: set[int] = {1}
    for lane in ("draft", "final"):
        chapters.update(number for number, _path in list_canonical_chapter_files(root / MANUSCRIPT_DIR / lane))
    for pattern in (
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
        chapter = manifest_chapter_number(task)
        if chapter > 0:
            chapters.add(chapter)
    for path in (root / "70_runtime" / "transactions").glob("*.json"):
        payload = read_json(path)
        chapter = int(payload.get("chapter_number") or 0) if isinstance(payload, dict) else 0
        if chapter > 0:
            chapters.add(chapter)
    return max(chapters)


def chapter_draft_status(root: Path, chapter_number: int, gate: dict[str, Any], tasks: list[dict[str, Any]]) -> str:
    if existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None:
        return "finalized"
    gate_state = str(gate.get("status") or "none")
    if gate_state in {"passed", "failed", "waived"}:
        return "gate_passed" if gate_state in {"passed", "waived"} else "gate_failed"
    if existing_manuscript_chapter_path(root, chapter_number, lane="draft") is not None:
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
                "result_file": str(manifest_output(manifest).get("path") or ""),
                "validate_command": ensure_longform_prefix(str(manifest_commands(manifest).get("validate") or "")),
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
    output = manifest_output(manifest)
    commands = manifest_commands(manifest)
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
            "result_file": str(output.get("path") or ""),
            "output_protocol": str(output.get("protocol") or ""),
            "reviewer_instance_id": str(context.get("reviewer_instance_id") or ""),
            "context_digest_hash": str(context.get("context_digest_hash") or ""),
            "independence_mode": str(context.get("independence_mode") or ""),
            "review_round": int(context.get("review_round") or 0),
            "validate_command": ensure_longform_prefix(str(commands.get("validate") or "")),
            "apply_command": ensure_longform_prefix(str(commands.get("apply") or "")),
            "failure_next_command": ensure_longform_prefix(str(commands.get("failure") or "")),
            "hard_boundaries": list(HARD_BOUNDARIES),
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
    for item in manifest_input_paths(manifest):
        if "50_workbench/editorial_reviews/agent_tasks/" in item and item.endswith(".md"):
            return item
    return ""


def first_editorial_context_file(manifest: dict[str, Any]) -> str:
    for item in manifest_input_paths(manifest):
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
    role_id = str(manifest_role(task).get("id") or "")
    if str(task.get("task_type") or "") == "editorial_review" and role_id:
        return role_id
    task_id = str(task.get("task_id") or "")
    match = re.match(r"editorial_review:([^:]+):ch\d{3}:v\d+", task_id)
    if match:
        return match.group(1)
    output = str(manifest_output(task).get("path") or "")
    if output:
        name = Path(output).name
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
    if readiness.stage == "story_profile_conflict":
        return base_action(
            status="need_human",
            chapter_number=0,
            blocked_by="story_profile_conflict",
            waiting_for="human",
            next_command="longform-engine quality story-profile project.yaml --json",
            human_summary=(
                "Resolve the selected story-facet conflicts explicitly in project.yaml: "
                + "; ".join(readiness.errors[:3])
            ),
        )
    task_type = readiness.required_task_type
    active_statuses = {"awaiting_agent", "submitted", "validated", "invalid"}
    active_tasks = [
        task
        for task in list_manifests(root, chapter_number=0)
        if task.get("task_type") == task_type and task.get("status") in active_statuses
    ]
    if active_tasks:
        return agent_task_action(root, sorted(active_tasks, key=task_sort_key)[0])
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


def rolling_outline_action(config: ConfigDocument, root: Path) -> dict[str, Any] | None:
    """Request the next detailed planning window before the active plan runs dry."""

    next_chapter = highest_finalized_chapter(root) + 1
    plan = read_json(root / "20_outline" / "chapter_plan.json")
    rows = [item for item in plan if isinstance(item, dict)] if isinstance(plan, list) else []
    planned_numbers = sorted(
        {int(item.get("chapter_number") or 0) for item in rows if int(item.get("chapter_number") or 0) > 0}
    )
    if not planned_numbers:
        return None
    last_planned = planned_numbers[-1]
    planning = config.data["length"]["planning"]
    remaining = max(0, last_planned - next_chapter + 1)
    if next_chapter <= last_planned and remaining > int(planning["refill_threshold"]):
        return None
    active_statuses = {"awaiting_agent", "submitted", "validated", "invalid"}
    if any(
        task.get("task_type") == "outline_extension" and task.get("status") in active_statuses
        for task in list_manifests(root, chapter_number=0)
    ):
        return None
    start = last_planned + 1
    end = start + int(planning["detailed_horizon"]) - 1
    forecast = compile_length_forecast(config.data["length"])
    return base_action(
        status="ready_for_intelligence_task",
        chapter_number=next_chapter,
        task_type="outline_extension",
        blocked_by="rolling_outline_refill",
        waiting_for="cli",
        next_command=(
            "longform-engine intelligence task project.yaml --task-type outline_extension "
            f"--from-chapter {start} --to-chapter {end}"
        ),
        human_summary=(
            f"Only {remaining} detailed chapter plans remain. Prepare ch{start:03d}-ch{end:03d} "
            f"against the {forecast.target_total_characters}-character book budget for explicit human apply."
        ),
        planning_window={"from_chapter": start, "to_chapter": end, "remaining": remaining},
    )


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
        and manifest_chapter_number(task) == chapter_number
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
        if not editorial_aggregate_is_current(root, chapter_number, payload):
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
        if not editorial_human_resolution_reasons(payload):
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
        and str((task.get("scope") or {}).get("kind") or "") != "chapter"
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
    chapter_number = manifest_chapter_number(manifest)
    role = manifest_role(manifest)
    output = manifest_output(manifest)
    policy = manifest_policy(manifest)
    commands = manifest_commands(manifest)
    inputs = manifest_input_paths(manifest)
    context = manifest_context(manifest)
    session = session_for_manifest(manifest)
    if not validation.ok:
        action = base_action(
            status="agent_task_contract_invalid",
            chapter_number=chapter_number,
            blocked_by="task_contract_invalid",
            waiting_for="cli_task_regeneration",
            task_id=str(manifest.get("task_id") or task.get("task_id") or ""),
            task_type=task_type,
            role_id=str(role.get("id") or ""),
            role_version=str(role.get("version") or ""),
            role_contract_hash=str(role.get("contract_hash") or ""),
            independence_mode=str(role.get("independence_mode") or ""),
            project_overlay_hash=str(role.get("overlay_hash") or ""),
            session=session,
            input_files=inputs,
            context_policy=context,
            allowed_output_paths=[str(output.get("path") or "")],
            output_schema=str(output.get("protocol") or ""),
            validate_command=str(commands.get("validate") or ""),
            apply_command=str(commands.get("apply") or ""),
            failure_next_command=str(commands.get("failure") or ""),
            next_command=str(commands.get("failure") or ""),
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
        role_id=str(role.get("id") or ""),
        role_version=str(role.get("version") or ""),
        role_contract_hash=str(role.get("contract_hash") or ""),
        independence_mode=str(role.get("independence_mode") or ""),
        project_overlay_hash=str(role.get("overlay_hash") or ""),
        session=session,
        input_files=inputs,
        context_policy=context,
        allowed_output_paths=[str(output.get("path") or "")],
        output_schema=str(output.get("protocol") or ""),
        validate_command=str(commands.get("validate") or ""),
        protocol_validate_command=(
            f"longform-engine agent-task result-validate project.yaml "
            f"{manifest.get('task_id')} --file {output.get('path')}"
        ),
        apply_command=str(commands.get("apply") or ""),
        failure_next_command=str(commands.get("failure") or ""),
        hard_boundaries=list(HARD_BOUNDARIES),
        scope=manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {},
        canonical_targets=as_string_list(policy.get("canonical_targets")),
        requires_human_apply=bool(policy.get("requires_human_apply")),
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
            number
            for number, _path in list_canonical_chapter_files(root / MANUSCRIPT_DIR / "draft")
        }
        | {
            chapter_from_name(path.parent.name)
            for path in (root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json")
        }
        | {
            manifest_chapter_number(task)
            for task in list_manifests(root)
            if str((task.get("scope") or {}).get("kind") or "") == "chapter"
        }
    )
    for chapter_number in chapter_numbers:
        if chapter_number <= 0 or existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None:
            continue
        stage = derive_chapter_stage(config, root, chapter_number)
        stage_name = str(stage.get("stage") or "")
        allowed_types = chapter_stage_task_types(stage_name)
        stage_tasks = active_chapter_tasks(root, chapter_number, allowed_types)
        if stage_name == "payoff_pending" and not reader_payoff_task_is_current(
            config,
            chapter_number=chapter_number,
        ):
            return reader_payoff_action(config, root)
        if stage_name == "pacing_pending":
            stage_tasks = [
                task
                for task in stage_tasks
                if semantic_pacing_task_is_current(root, chapter_number, task)
            ]
        if stage_name == "editorial_pending":
            stage_tasks = [
                task
                for task in stage_tasks
                if editorial_task_is_current(root, chapter_number, task)
            ]
        if stage_tasks:
            return agent_task_action(root, sorted(stage_tasks, key=task_sort_key)[0])
        if stage_name == "gate_pending":
            command = f"longform-engine gate-check project.yaml --chapter {chapter_number}"
            return base_action(
                status="awaiting_gate",
                chapter_number=chapter_number,
                blocked_by="current_candidate_without_gate",
                waiting_for="gate_check",
                next_command=command,
                validate_command=command,
                human_summary=f"ch{chapter_number:03d} current candidate needs deterministic gate-check.",
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "semantic_review_pending":
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
        if stage_name == "pacing_pending":
            return base_action(
                status="ready_for_pacing_review",
                chapter_number=chapter_number,
                blocked_by="semantic_pacing_review_required",
                waiting_for="cli",
                task_type="pacing_review",
                output_schema=output_protocol_for_task("pacing_review"),
                next_command=f"longform-engine pacing semantic-task project.yaml --chapter {chapter_number}",
                failure_next_command=f"longform-engine pacing semantic-task project.yaml --chapter {chapter_number}",
                human_summary=f"ch{chapter_number:03d} requires a current-draft semantic pacing review.",
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "editorial_pending":
            return editorial_review_action(config, root)
        if stage_name == "repair_synthesis_pending":
            command = f"longform-engine repair synthesis-task project.yaml --chapter {chapter_number}"
            return base_action(
                status="review_bundle_ready",
                chapter_number=chapter_number,
                blocked_by="repair_plan_required",
                waiting_for="cli",
                task_type="repair_plan_synthesis",
                next_command=command,
                failure_next_command=command,
                human_summary=(
                    f"ch{chapter_number:03d} completed all required reviews and needs one evidence-complete repair plan."
                ),
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "repair_plan_validated":
            command = (
                f"longform-engine repair candidate-task project.yaml --chapter {chapter_number} --agent codex"
            )
            return base_action(
                status="repair_plan_validated",
                chapter_number=chapter_number,
                blocked_by="repair_candidate_required",
                waiting_for="cli",
                task_type="repair",
                next_command=command,
                failure_next_command=command,
                human_summary=f"ch{chapter_number:03d} repair plan validated; create the immutable repair candidate task.",
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "review_need_human":
            command = (
                f"longform-engine editorial need-human project.yaml --chapter {chapter_number} "
                "--reason review_barrier_conflict"
            )
            return base_action(
                status="need_human",
                chapter_number=chapter_number,
                blocked_by="review_barrier_conflict",
                waiting_for="human_decision",
                next_command=command,
                human_summary=str(stage.get("reason") or "Review evidence requires a human decision."),
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "repair_budget_exhausted":
            return base_action(
                status="need_human",
                chapter_number=chapter_number,
                blocked_by="repair_budget_exhausted",
                waiting_for="human_decision",
                next_command="",
                failure_next_command="",
                human_summary=(
                    f"ch{chapter_number:03d} still has P0/P1 findings after two submitted repair rounds; "
                    "the engine will not create a third repair command."
                ),
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "reviews_pending":
            return base_action(
                status="reviews_pending",
                chapter_number=chapter_number,
                blocked_by="review_barrier_incomplete",
                waiting_for="review_protocol_repair",
                next_command="longform-engine production status project.yaml",
                human_summary=f"ch{chapter_number:03d} review barrier is incomplete or internally stale.",
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "ready_to_finalize":
            command = (
                f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human"
            )
            return base_action(
                status="awaiting_finalize",
                chapter_number=chapter_number,
                blocked_by="review_barrier_passed",
                waiting_for="human_finalize",
                next_command=command,
                apply_command=command,
                human_summary=f"ch{chapter_number:03d} passed the complete review barrier and waits for explicit finalize.",
                sources=as_string_list(stage.get("sources")),
            )
        if stage_name == "repair_pending":
            return first_gate_action(root)
        if stage_name == "writing_pending":
            draft = manuscript_chapter_path(root, chapter_number, lane="draft")
            if draft.exists():
                command = f"longform-engine gate-check project.yaml --chapter {chapter_number}"
                return base_action(
                    status="awaiting_gate",
                    chapter_number=chapter_number,
                    blocked_by="draft_without_gate",
                    waiting_for="gate_check",
                    next_command=command,
                    validate_command=command,
                    human_summary=f"ch{chapter_number:03d} has a draft and needs gate-check.",
                    sources=[relative_path(root, draft)],
                )
    return None


def derive_chapter_stage(config: ConfigDocument, root: Path, chapter_number: int) -> dict[str, Any]:
    """Derive one chapter stage from canonical artifacts and current candidate evidence."""

    closure = root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json"
    final = manuscript_chapter_path(root, chapter_number, lane="final")
    ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if closure.exists():
        return {"stage": "closed", "sources": [relative_path(root, closure)]}
    if final.exists() and not ledger.exists():
        return {"stage": "finalized_needs_semantic_bundle", "sources": [relative_path(root, final)]}
    if final.exists():
        return {"stage": "finalized_needs_close", "sources": [relative_path(root, ledger)]}
    pre_gate_reviews = active_chapter_tasks(
        root,
        chapter_number,
        {"humanize_semantic_review"},
    )
    if pre_gate_reviews:
        return {
            "stage": "pre_gate_candidate_review",
            "sources": [str(item.get("manifest_file") or "") for item in pre_gate_reviews],
        }
    gate = read_json(gate_path)
    if gate_path.is_file() and isinstance(gate, dict) and gate:
        source_hash = str(gate.get("source_sha256") or "")
        current_hash = sha256(draft.read_bytes()).hexdigest() if draft.is_file() else ""
        if source_hash and source_hash != current_hash:
            return {
                "stage": "gate_pending",
                "sources": [relative_path(root, draft), relative_path(root, gate_path)],
                "reason": "gate_source_stale",
            }
        barrier = review_barrier_status(config, chapter_number=chapter_number)
        stages = barrier.get("stages") if isinstance(barrier.get("stages"), dict) else {}
        for review_name, stage_name in (
            ("semantic", "semantic_review_pending"),
            ("payoff", "payoff_pending"),
            ("pacing", "pacing_pending"),
            ("editorial", "editorial_pending"),
        ):
            review = stages.get(review_name) if isinstance(stages.get(review_name), dict) else {}
            if review.get("required") and not review.get("complete"):
                return {"stage": stage_name, "sources": [relative_path(root, gate_path)]}
        barrier_status = str(barrier.get("status") or "reviews_pending")
        if barrier_status == "need_human":
            return {
                "stage": "review_need_human",
                "sources": [relative_path(root, gate_path)],
                "reason": "; ".join(str(item) for item in barrier.get("blockers") or []),
            }
        if barrier_status == "review_bundle_ready":
            attempts = repair_attempt_status(config, chapter_number=chapter_number)
            if attempts.get("exhausted"):
                return {
                    "stage": "repair_budget_exhausted",
                    "sources": [relative_path(root, gate_path)],
                }
            round_number = next_repair_round(config, chapter_number=chapter_number)
            round_token = f"r{int(round_number or 0):02d}"
            synthesis_id = f"repair_plan_synthesis:ch{chapter_number:03d}:{round_token}:v4"
            synthesis = next(
                (
                    task
                    for task in list_manifests(root, chapter_number=chapter_number)
                    if str(task.get("task_id") or "") == synthesis_id
                ),
                None,
            )
            plan_status = repair_plan_status(config, chapter_number=chapter_number)
            if plan_status.get("need_human"):
                return {
                    "stage": "review_need_human",
                    "sources": [str(plan_status.get("report_file") or relative_path(root, gate_path))],
                    "reason": "repair target conflicts with the preservation ledger",
                }
            repair_id = f"repair:ch{chapter_number:03d}:{round_token}:v4"
            repair_task = next(
                (
                    task
                    for task in list_manifests(root, chapter_number=chapter_number)
                    if str(task.get("task_id") or "") == repair_id
                ),
                None,
            )
            if repair_task is not None:
                if synthesis is not None and str(synthesis.get("status") or "") == "validated":
                    return {
                        "stage": "repair_lifecycle_reconciliation_required",
                        "sources": [
                            str(synthesis.get("manifest_file") or ""),
                            str(repair_task.get("manifest_file") or ""),
                        ],
                    }
                return {
                    "stage": "repair_candidate_pending",
                    "sources": [str(repair_task.get("manifest_file") or "")],
                }
            if synthesis is None or str(synthesis.get("status") or "") != "validated":
                return {
                    "stage": "repair_synthesis_pending",
                    "sources": [relative_path(root, gate_path)],
                }
            return {
                "stage": "repair_plan_validated",
                "sources": [str(synthesis.get("manifest_file") or "")],
            }
        if barrier_status == "ready_to_finalize":
            return {"stage": "ready_to_finalize", "sources": [relative_path(root, gate_path)]}
        return {"stage": "reviews_pending", "sources": [relative_path(root, gate_path)]}
    if draft.exists():
        return {"stage": "gate_pending", "sources": [relative_path(root, draft)]}
    return {"stage": "writing_pending", "sources": []}


def active_chapter_tasks(root: Path, chapter_number: int, task_types: set[str]) -> list[dict[str, Any]]:
    return [
        task
        for task in list_manifests(root, chapter_number=chapter_number)
        if str(task.get("task_type") or "") in task_types
        and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
    ]


def chapter_stage_task_types(stage: str) -> set[str]:
    """Return the only Agent roles allowed to compete within one evidence-derived chapter stage."""

    return {
        "writing_pending": {"chapter_direction", "chapter_write"},
        "pre_gate_candidate_review": {"humanize_semantic_review"},
        "gate_pending": set(),
        "reviews_pending": set(),
        "review_need_human": set(),
        "repair_synthesis_pending": {"repair_plan_synthesis"},
        "repair_plan_validated": set(),
        "repair_lifecycle_reconciliation_required": set(),
        "repair_candidate_pending": {"repair"},
        "repair_budget_exhausted": set(),
        "repair_pending": {"repair", "humanize", "content_expand", "humanize_semantic_review"},
        "semantic_review_pending": {"semantic_review"},
        "payoff_pending": {"reader_payoff_review"},
        "pacing_pending": {"pacing_review"},
        "editorial_pending": {"editorial_review", "character_expression_review"},
        "ready_to_finalize": set(),
        "finalized_needs_semantic_bundle": {"chapter_semantic"},
        "finalized_needs_close": set(),
        "closed": set(),
    }.get(stage, set())


def chapter_semantic_lifecycle_action(root: Path) -> dict[str, Any] | None:
    """Require one semantic bundle and explicit close for every finalized chapter."""

    for chapter_number, final_file in list_finalized_chapter_files(root):
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
                output_schema=output_protocol_for_task("chapter_semantic"),
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
        if chapter_number <= 0 or existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None:
            continue
        payload = read_json(path)
        if isinstance(payload, dict):
            candidates.append(chapter_number)
    for chapter_number in sorted(set(candidates)):
        status = reader_payoff_review_status(config, chapter_number=chapter_number)
        if not status.get("required") or status.get("complete"):
            continue
        if reader_payoff_task_is_current(config, chapter_number=chapter_number) and any(
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
            output_schema=output_protocol_for_task("reader_payoff_review"),
            validate_command=(
                f"longform-engine quality payoff-validate project.yaml --chapter {chapter_number} "
                f"--file {output}"
                if output
                else ""
            ),
            apply_command=(
                f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human"
            ),
            failure_next_command="longform-engine production next project.yaml",
            next_command=f"longform-engine quality payoff-task project.yaml --chapter {chapter_number}",
            human_summary=(
                f"ch{chapter_number:03d} needs an independent reader-payoff review before repair or finalization."
            ),
            sources=[str(status.get("report_file") or "")],
        )
    return None


def editorial_review_action(config: ConfigDocument, root: Path) -> dict[str, Any] | None:
    """Schedule required risk-based editorial review after payoff and before finalize."""

    candidates: list[int] = []
    for path in (root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json"):
        chapter_number = chapter_from_name(path.parent.name)
        if chapter_number <= 0 or existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None:
            continue
        payload = read_json(path)
        if isinstance(payload, dict):
            candidates.append(chapter_number)
    for chapter_number in sorted(set(candidates)):
        payoff_status = reader_payoff_review_status(config, chapter_number=chapter_number)
        if payoff_status.get("required") and not payoff_status.get("complete"):
            continue
        reasons = editorial_review_required_reasons(config, chapter_number=chapter_number)
        if not reasons:
            continue
        aggregate = read_json(
            root / "50_workbench" / "editorial_reviews" / f"ch{chapter_number:03d}.aggregate.json"
        )
        if (
            isinstance(aggregate, dict)
            and editorial_aggregate_is_current(root, chapter_number, aggregate)
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
            and editorial_task_is_current(root, chapter_number, task)
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
        if chapter_number <= 0 or existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None:
            continue
        payload = read_json(path)
        if isinstance(payload, dict):
            candidates.append((chapter_number, path, payload))
    for chapter_number, path, payload in sorted(candidates, key=lambda item: item[0]):
        if payload.get("passed") is False and not gate_has_waiver(payload):
            next_command = str(
                payload.get("next_command")
                or "longform-engine production next project.yaml"
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
            candidate_already_submitted = False
            if len(validated_candidates) == 1:
                candidate_manifest = load_manifest(root, str(validated_candidates[0].get("task_id") or ""))
                candidate_already_submitted = submitted_candidate_matches_passed_gate(
                    root,
                    chapter_number=chapter_number,
                    task_id=str(candidate_manifest.get("task_id") or ""),
                    gate=payload,
                )
                if not candidate_already_submitted:
                    candidate_apply = str(manifest_commands(candidate_manifest).get("apply") or "")
            finalize_command = (
                f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human"
            )
            next_command = (
                finalize_command
                if candidate_already_submitted
                else candidate_apply or str(payload.get("next_command") or finalize_command)
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


def submitted_candidate_matches_passed_gate(
    root: Path,
    *,
    chapter_number: int,
    task_id: str,
    gate: dict[str, Any],
) -> bool:
    """Return whether a validated task is already the submitted, gate-passed candidate."""

    submission = read_json(
        root / MANUSCRIPT_DIR / "draft" / f"ch{chapter_number:03d}.submission.json"
    )
    if not isinstance(submission, dict):
        return False
    gate_hash = str(gate.get("source_sha256") or "")
    submission_hash = str(submission.get("draft_sha256") or submission.get("source_sha256") or "")
    return bool(
        task_id
        and str(submission.get("candidate_task_id") or "") == task_id
        and str(submission.get("candidate_status") or "") in {"submitted", "validated"}
        and gate_hash
        and gate_hash == submission_hash
    )


def editorial_aggregate_is_current(
    root: Path,
    chapter_number: int,
    aggregate: dict[str, Any],
) -> bool:
    """Bind v2+ editorial aggregates to the exact current chapter candidate."""

    schema_version = int(aggregate.get("schema_version") or 1)
    if schema_version < 2:
        return True
    source_hash = str(aggregate.get("source_sha256") or "")
    if not source_hash:
        return False
    chapter = manuscript_chapter_path(root, chapter_number, lane="final")
    if not chapter.is_file():
        chapter = manuscript_chapter_path(root, chapter_number, lane="draft")
    return chapter.is_file() and sha256(chapter.read_bytes()).hexdigest() == source_hash


def editorial_task_is_current(root: Path, chapter_number: int, task: dict[str, Any]) -> bool:
    """Bind an editorial role task to its isolated context and current chapter bytes."""

    manifest = load_manifest(root, str(task.get("task_id") or task.get("manifest_file") or ""))
    context_paths = [
        root / str(item)
        for item in manifest_input_paths(manifest)
        if str(item).replace("\\", "/").startswith(
            f"50_workbench/editorial_reviews/agent_tasks/ch{chapter_number:03d}/"
        )
        and str(item).endswith(".context.json")
    ]
    if len(context_paths) != 1 or not context_paths[0].is_file():
        return False
    context = read_json(context_paths[0])
    if not isinstance(context, dict) or context.get("schema") != "editorial_context_isolation_v1":
        return False
    provenance = [
        root / str(item)
        for item in context.get("provenance_source_files") or context.get("declared_source_files") or []
        if str(item).strip()
    ]
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if draft not in provenance or any(not path.is_file() for path in provenance):
        return False
    return context_digest_hash(root, provenance) == str(context.get("context_digest_hash") or "")


def first_draft_without_gate_action(root: Path) -> dict[str, Any] | None:
    for chapter_number, path in list_canonical_chapter_files(root / MANUSCRIPT_DIR / "draft"):
        if existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None:
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
        if (
            existing_manuscript_chapter_path(root, chapter_number, lane="final") is not None
            or existing_manuscript_chapter_path(root, chapter_number, lane="draft") is not None
        ):
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
            output_schema=output_protocol_for_task("chapter_write"),
            validate_command=ensure_longform_prefix(next_command),
            apply_command=f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
            failure_next_command="longform-engine production next project.yaml",
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
    role_id: str = "",
    role_version: str = "",
    role_contract_hash: str = "",
    independence_mode: str = "",
    project_overlay_hash: str = "",
    session: dict[str, Any] | None = None,
    input_files: list[str] | None = None,
    context_policy: dict[str, Any] | None = None,
    allowed_output_paths: list[str] | None = None,
    output_schema: str = "",
    validate_command: str = "",
    protocol_validate_command: str = "",
    apply_command: str = "",
    failure_next_command: str = "",
    hard_boundaries: list[str] | None = None,
    scope: dict[str, Any] | None = None,
    canonical_targets: list[str] | None = None,
    requires_human_apply: bool = False,
    sources: list[str] | None = None,
    need_human_reasons: list[str] | None = None,
    trigger_reasons: list[str] | None = None,
    planning_window: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "chapter_number": chapter_number,
        "blocked_by": blocked_by,
        "waiting_for": waiting_for,
        "task_id": task_id,
        "task_type": task_type,
        "role_id": role_id,
        "role_version": role_version,
        "role_contract_hash": role_contract_hash,
        "independence_mode": independence_mode,
        "project_overlay_hash": project_overlay_hash,
        "session": session or {},
        "input_files": input_files or [],
        "context_policy": context_policy or {},
        "allowed_output_paths": allowed_output_paths or [],
        "output_schema": output_schema,
        "validate_command": validate_command,
        "protocol_validate_command": protocol_validate_command,
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
        "planning_window": planning_window or {},
    }


def session_for_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    role_projection = manifest_role(manifest)
    role = load_role_registry().resolve(
        str(manifest.get("task_type") or ""),
        declared_role_id=str(role_projection.get("id") or ""),
    )
    return session_directive(
        role,
        task_type=str(manifest.get("task_type") or ""),
        scope=manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {},
        task_id=str(manifest.get("task_id") or ""),
    )


def task_sort_key(task: dict[str, Any]) -> tuple[int, int, int, str, str]:
    status = str(task.get("status") or "")
    task_type = str(task.get("task_type") or "")
    task_priority = TASK_PRIORITY.get(task_type, 999)
    if status == "validated" and task_type == "chapter_write":
        task_priority = 999
    return (
        manifest_chapter_number(task),
        STATUS_PRIORITY.get(status, 99),
        task_priority,
        str(task.get("updated_at") or task.get("created_at") or ""),
        str(task.get("task_id") or ""),
    )


def command_for_task_status(manifest: dict[str, Any], status: str) -> str:
    commands = manifest_commands(manifest)
    if status == "awaiting_agent":
        return f"longform-engine agent-task brief project.yaml {manifest.get('task_id')}"
    if status == "invalid":
        return str(commands.get("failure") or "")
    if status == "validated":
        return str(commands.get("apply") or "")
    if status == "approved" and str(manifest.get("task_type") or "") in {
        "book_ideation",
        "book_design",
        "character_expression_design",
        "outline_design",
        "outline_extension",
        "chapter_direction",
        "outline_revision",
        "style_analysis",
        "adaptation_analysis",
        "fanfiction_design",
    }:
        document = str(manifest_output(manifest).get("path") or "")
        return (
            "longform-engine intelligence compile-task project.yaml "
            f"--task-type {manifest.get('task_type')} --document {document}"
        )
    return str(commands.get("validate") or "")


def waiting_for_task_status(task_type: str, status: str) -> str:
    if status == "invalid":
        return "regenerated_agent_output"
    if status == "submitted":
        return "validation"
    if status == "validated":
        return "apply_command"
    if status == "approved":
        return "semantic_compilation"
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
    if status == "approved":
        return f"{subject} has approved {task_type} Markdown; create its semantic compilation task."
    return f"{subject} is blocked by {task_type}."


def highest_finalized_chapter(root: Path) -> int:
    return max((chapter_number for chapter_number, _path in list_finalized_chapter_files(root)), default=0)


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
