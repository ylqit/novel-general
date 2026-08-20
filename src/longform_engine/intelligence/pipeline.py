"""Candidate/validate/explicit-apply workflows for project-level intelligence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_REQUIRED_HEADINGS,
    DESIGN_DOCUMENT_SCHEMA,
    EVIDENCE_REVIEW_SCHEMA,
    AgentProtocolError,
    build_validation_report,
    canonical_delta_domain_payload,
    chapter_direction_option_ids,
    output_protocol_for_task,
    parse_design_document,
    validate_canonical_delta,
    validate_evidence_review,
    validate_review_evidence_for_source,
)
from longform_engine.agent_tasks import (
    build_manifest,
    is_canonical_output,
    list_manifests,
    manifest_chapter_number,
    manifest_commands,
    manifest_input_paths,
    manifest_output,
    mark_tasks_for_output,
    mark_tasks_for_chapter_type,
    update_task_status,
    validate_current_task_result,
    write_manifest,
)
from longform_engine.arc_simulation import (
    SIMULATION_DIR,
    ArcSimulationError,
    arc_simulation_path,
    current_basis_hashes,
    load_active_arc_simulation,
    load_covering_arc_simulation,
    mark_overlapping_arc_simulations_stale,
    permitted_arc_simulation_ranges,
    validate_arc_causal_simulation,
    write_arc_causal_simulation,
)
from longform_engine.character_expression import (
    CHARACTER_EXPRESSION_SCHEMA,
    CHARACTER_REVIEW_SCHEMA,
    character_expression_readiness,
    validate_character_expression_profile,
    write_character_expression_profile,
)
from longform_engine.chapter_contract import REMOVED_ALIAS_FIELDS
from longform_engine.config import ConfigDocument
from longform_engine.db import sync_database
from longform_engine.lengths import compile_length_forecast
from longform_engine.prompting import estimate_text_units, resolve_context_budget_contract
from longform_engine.quality import truncate_editorial_pattern_registry
from longform_engine.reader_promises import (
    LEDGER_PATH,
    apply_planning_deferrals,
    load_reader_promise_ledger,
    merge_planned_reader_promises,
    promise_deadline_status,
    validate_promise_actions,
    validate_planning_deferrals,
    write_reader_promise_ledger,
)
from longform_engine.story_profiles import BUILTIN_MARKET_IDS, active_story_facets, compile_story_profile
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


INTELLIGENCE_TASK_TYPES = (
    "fanfiction_canon",
    "fanfiction_design",
    "book_ideation",
    "book_design",
    "character_expression_design",
    "character_expression_review",
    "outline_design",
    "arc_simulation",
    "outline_extension",
    "chapter_direction",
    "outline_revision",
    "research_synthesis",
    "style_analysis",
    "adaptation_analysis",
)

DESIGN_INTELLIGENCE_TASK_TYPES = tuple(
    task_type
    for task_type in INTELLIGENCE_TASK_TYPES
    if output_protocol_for_task(task_type) == DESIGN_DOCUMENT_SCHEMA
)

TASK_SPECS: dict[str, dict[str, Any]] = {
    "book_ideation": {
        "schema": "book_ideation_candidate_v1",
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/creative_decisions.json",
            "30_state/novel_state.json",
        ),
        "defaults": (
            "project.yaml",
            "00_governance/idea_seed.md",
            "00_governance/reader_contract.md",
        ),
    },
    "fanfiction_canon": {
        "schema": "fanfiction_source_canon_v1",
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/fanfiction/source_canon.json",
            "30_state/novel_state.json",
            "70_runtime/provenance/creation_events.jsonl",
        ),
        "defaults": (),
    },
    "fanfiction_design": {
        "schema": "fanfiction_design_candidate_v1",
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/fanfiction/fanfiction_bible.json",
            "10_bible/creative_brief.json",
            "10_bible/world.md",
            "10_bible/power_system.md",
            "10_bible/characters.json",
            "10_bible/relationships.json",
            "10_bible/character_expression.json",
            "30_state/novel_state.json",
            "70_runtime/provenance/creation_events.jsonl",
        ),
        "defaults": (
            "project.yaml",
            "10_bible/fanfiction/source_canon.json",
            "00_governance/idea_seed.md",
            "00_governance/reader_contract.md",
        ),
    },
    "book_design": {
        "schema": "book_design_candidate_v2",
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/creative_brief.json",
            "10_bible/world.md",
            "10_bible/power_system.md",
            "10_bible/characters.json",
            "10_bible/relationships.json",
            "10_bible/character_expression.json",
            "30_state/novel_state.json",
        ),
        "defaults": (
            "project.yaml",
            "00_governance/idea_seed.md",
            "00_governance/reader_contract.md",
        ),
    },
    "character_expression_design": {
        "schema": CHARACTER_EXPRESSION_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/character_expression.json",
            "30_state/novel_state.json",
        ),
        "defaults": (
            "project.yaml",
            "10_bible/creative_brief.json",
            "10_bible/characters.json",
            "10_bible/relationships.json",
        ),
    },
    "character_expression_review": {
        "schema": CHARACTER_REVIEW_SCHEMA,
        "scope": "range",
        "human": False,
        "targets": (),
        "defaults": (
            "10_bible/characters.json",
            "10_bible/relationships.json",
            "10_bible/character_expression.json",
        ),
    },
    "outline_design": {
        "schema": "outline_design_candidate_v2",
        "scope": "project",
        "human": True,
        "targets": (
            "20_outline/book_outline.md",
            "20_outline/story_arcs.json",
            "20_outline/volumes.json",
            "20_outline/chapter_plan.json",
            "20_outline/planning_window.json",
            "20_outline/foreshadowing_ledger.json",
            "30_state/reader_promise_ledger.json",
            "30_state/novel_state.json",
        ),
        "defaults": (
            "project.yaml",
            "10_bible/creative_brief.json",
            "10_bible/world.md",
            "10_bible/characters.json",
            "10_bible/relationships.json",
        ),
    },
    "arc_simulation": {
        "schema": "arc_causal_simulation_v1",
        "scope": "range",
        "human": True,
        "targets": (),
        "defaults": (
            "project.yaml",
            "10_bible/creative_brief.json",
            "10_bible/characters.json",
            "10_bible/relationships.json",
            "20_outline/book_outline.md",
            "20_outline/story_arcs.json",
            "20_outline/chapter_plan.json",
            "30_state/reader_promise_ledger.json",
            "30_state/character_state.json",
        ),
    },
    "outline_extension": {
        "schema": "outline_extension_candidate_v1",
        "scope": "range",
        "human": True,
        "targets": (
            "20_outline/chapter_plan.json",
            "20_outline/planning_window.json",
            "20_outline/foreshadowing_ledger.json",
            "30_state/reader_promise_ledger.json",
            "30_state/novel_state.json",
        ),
        "defaults": (
            "project.yaml",
            "10_bible/creative_brief.json",
            "20_outline/story_arcs.json",
            "20_outline/volumes.json",
            "20_outline/chapter_plan.json",
            "20_outline/planning_window.json",
            "20_outline/foreshadowing_ledger.json",
        ),
    },
    "chapter_direction": {
        "schema": "chapter_direction_candidate_v4",
        "scope": "chapter",
        "human": True,
        "targets": (),
        "defaults": (),
    },
    "outline_revision": {
        "schema": "outline_revision_candidate_v1",
        "scope": "range",
        "human": True,
        "targets": (
            "20_outline/book_outline.md",
            "20_outline/story_arcs.json",
            "20_outline/volumes.json",
            "20_outline/chapter_plan.json",
            "20_outline/planning_window.json",
            "20_outline/foreshadowing_ledger.json",
            "30_state/reader_promise_ledger.json",
            "30_state/novel_state.json",
        ),
        "defaults": (
            "20_outline/book_outline.md",
            "20_outline/volumes.json",
            "20_outline/chapter_plan.json",
            "20_outline/foreshadowing_ledger.json",
            "30_state/novel_state.json",
        ),
    },
    "research_synthesis": {
        "schema": "research_synthesis_v1",
        "scope": "project",
        "human": False,
        "targets": ("10_bible/research_canon.jsonl",),
        "defaults": (),
    },
    "style_analysis": {
        "schema": "semantic_style_profile_v1",
        "scope": "project",
        "human": False,
        "targets": ("10_bible/style_profiles/current_style_profile.json",),
        "defaults": (),
    },
    "adaptation_analysis": {
        "schema": "adaptation_analysis_v1",
        "scope": "project",
        "human": False,
        "targets": ("10_bible/style_profiles/adaptation_profile.json",),
        "defaults": (),
    },
}

BOOK_IDEATION_DIMENSIONS = (
    "target_reader_and_reading_context",
    "core_hook",
    "world_core_rule",
    "protagonist_desire_and_flaw",
    "long_conflict",
    "volume_escalation",
    "ending_boundary",
    "taboos_and_unwanted_tropes",
)


@dataclass(frozen=True)
class IntelligenceTaskResult:
    task_type: str
    task_id: str
    manifest_file: str
    instruction_file: str
    candidate_file: str
    next_command: str


@dataclass(frozen=True)
class IntelligenceValidationResult:
    task_type: str
    ok: bool
    candidate_file: str
    report_file: str
    errors: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class IntelligenceApplyResult:
    task_type: str
    status: str
    candidate_file: str
    touched_paths: tuple[str, ...]
    transaction_report: str
    next_command: str


@dataclass(frozen=True)
class DesignApprovalResult:
    task_type: str
    document_file: str
    approval_file: str
    document_sha256: str
    selection_file: str
    selection_sha256: str
    next_command: str


@dataclass(frozen=True)
class ChapterDirectionSelectionResult:
    chapter_number: int
    document_file: str
    selection_file: str
    document_sha256: str
    selection_sha256: str
    selected_option_id: str
    next_command: str


@dataclass(frozen=True)
class ProjectReadinessResult:
    ready: bool
    stage: str
    required_task_type: str
    errors: tuple[str, ...]


def create_intelligence_task(
    config: ConfigDocument,
    *,
    task_type: str,
    input_files: Iterable[str | Path] = (),
    chapter_number: int | None = None,
    from_chapter: int | None = None,
    to_chapter: int | None = None,
) -> IntelligenceTaskResult:
    root = resolve_project_root(config)
    spec = require_spec(task_type)
    scope = task_scope(
        spec,
        chapter_number=chapter_number,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
    )
    if task_type == "chapter_direction":
        direction_status = assess_chapter_direction(config, int(scope["chapter_number"]))
        if direction_status.get("status") == "outline_revision_required":
            raise ValueError(
                "chapter direction is blocked by an unresolved outline redirect; create an "
                "outline_revision task before selecting a new direction."
            )
        if direction_status.get("status") == "arc_simulation_required":
            raise ValueError(
                "chapter direction requires a current human-approved arc_causal_simulation_v1 "
                "covering this chapter."
            )
        from longform_engine.orchestration.pipeline import plan_chapter

        plan_chapter(config, chapter_number=int(scope["chapter_number"]))
    inputs = normalize_inputs(
        root,
        input_files or intelligence_default_inputs(root, task_type, spec, scope),
    )
    if task_type == "fanfiction_design":
        inputs = [write_fanfiction_design_context(config, root)]
    if task_type == "outline_extension":
        inputs = [write_outline_extension_context(config, root, scope)]
    if task_type == "chapter_direction":
        inputs = [write_chapter_direction_context(config, root, int(scope["chapter_number"]))]
    if task_type in {"fanfiction_canon", "research_synthesis", "style_analysis", "adaptation_analysis"} and not inputs:
        raise ValueError(f"{task_type} requires at least one --input file.")
    if task_type.startswith("fanfiction_") and str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        raise ValueError(f"{task_type} requires creation.mode=fanfiction.")

    token = scope_token(scope)
    round_number = next_book_ideation_round(root) if task_type == "book_ideation" else 0
    base = (
        f"{task_type}.{token}.round{round_number:02d}"
        if round_number
        else f"{task_type}.{token}"
    )
    instruction = root / "50_workbench" / "intelligence_tasks" / f"{base}.md"
    candidate_base = f"{task_type}.{token}" if task_type == "book_ideation" else base
    output_protocol = output_protocol_for_task(task_type)
    document_requires_human = output_protocol == DESIGN_DOCUMENT_SCHEMA
    candidate_suffix = ".candidate.md" if output_protocol == DESIGN_DOCUMENT_SCHEMA else ".candidate.json"
    candidate = root / "50_workbench" / "intelligence_candidates" / f"{candidate_base}{candidate_suffix}"
    manifest_file = root / "50_workbench" / "agent_tasks" / f"{base}.manifest.json"
    input_rel = [relative(root, path) for path in inputs]
    instruction_context = dict(scope)
    if task_type == "book_ideation":
        instruction_context.update(
            {
                "round": round_number,
                "dimension": next_book_ideation_dimension(root),
            }
        )
    if task_type == "chapter_direction":
        status = assess_chapter_direction(config, int(scope["chapter_number"]))
        instruction_context["trigger_reasons"] = list(status["reasons"])
    atomic_write_text(
        instruction,
        render_instruction(task_type, spec, instruction_context, input_rel, relative(root, candidate)),
    )
    input_rel.append(relative(root, instruction))

    range_args = scope_command_args(scope)
    input_args = (
        ""
        if task_type == "chapter_direction"
        else "".join(f" --input {path}" for path in input_rel if path != relative(root, instruction))
    )
    validate_command, apply_command, failure_command = intelligence_commands(
        task_type,
        candidate=relative(root, candidate),
        range_args=range_args,
        input_args=input_args,
        requires_human=bool(spec["human"]) or document_requires_human,
    )
    manifest = build_manifest(
        root,
        task_type=task_type,
        chapter_number=int(scope.get("chapter_number") or 0) or None,
        scope=scope,
        input_files=input_rel,
        allowed_output_paths=(candidate,),
        output_schema=output_protocol_for_task(task_type),
        validate_command=validate_command,
        apply_command=apply_command,
        failure_next_command=failure_command,
        canonical_targets=intelligence_canonical_targets(root, task_type, scope),
        requires_human_apply=bool(spec["human"]) or document_requires_human,
        context_policy={
            "required_files": [instruction],
            "optional_files": inputs,
            "compiled_brief": instruction,
            "selection_report": instruction,
        },
        task_id=(
            f"book_ideation:project:round{round_number:02d}:v4"
            if task_type == "book_ideation"
            else None
        ),
    )
    written = write_manifest(root, manifest, manifest_file)
    return IntelligenceTaskResult(
        task_type=task_type,
        task_id=str(manifest["task_id"]),
        manifest_file=relative(root, written),
        instruction_file=relative(root, instruction),
        candidate_file=relative(root, candidate),
        next_command=f"longform-engine agent-task brief project.yaml {manifest['task_id']}",
    )


def validate_intelligence_candidate(
    config: ConfigDocument,
    *,
    task_type: str,
    file_path: str | Path,
) -> IntelligenceValidationResult:
    root = resolve_project_root(config)
    spec = require_spec(task_type)
    candidate = resolve_candidate(root, file_path)
    errors: list[str] = []
    manifest = manifest_for_output(root, task_type, candidate)
    if manifest is None:
        errors.append("candidate is not declared by an active AgentTaskManifest.")
    else:
        _task, control_errors = validate_current_task_result(
            root,
            chapter_number=manifest_chapter_number(manifest),
            task_type=task_type,
            output_path=candidate,
            allowed_statuses=("submitted", "validated"),
        )
        errors.extend(control_errors)
    protocol = output_protocol_for_task(task_type)
    if protocol == DESIGN_DOCUMENT_SCHEMA:
        try:
            parse_design_document(candidate.read_text(encoding="utf-8"), expected_type=task_type)
        except (OSError, UnicodeError, AgentProtocolError) as exc:
            errors.append(f"candidate does not satisfy {DESIGN_DOCUMENT_SCHEMA}: {exc}")
    else:
        payload = load_candidate(
            config,
            root,
            candidate,
            errors,
            task_type=task_type,
            spec=spec,
            manifest=manifest,
        )
        if payload is not None:
            validate_payload(config, root, task_type, spec, payload, manifest, errors)

    report = root / "50_workbench" / "intelligence_validations" / f"{candidate.stem}.validation.json"
    ok = not errors
    if ok and protocol == DESIGN_DOCUMENT_SCHEMA:
        next_command = (
            "longform-engine intelligence approve project.yaml "
            f"--task-type {task_type} --document {relative(root, candidate)} --approved-by human"
        )
    elif ok:
        next_command = (
            f"longform-engine intelligence apply project.yaml --task-type {task_type} "
            f"--delta {relative(root, candidate)}"
            + (" --approved-by human" if spec["human"] else "")
        )
    else:
        next_command = str(
            manifest_commands(manifest or {}).get("failure")
            or f"longform-engine intelligence task project.yaml --task-type {task_type}"
        )
    report_payload = build_validation_report(
        ok=ok,
        stage="intelligence_validate",
        subject=relative(root, candidate),
        errors=errors,
        blockers=errors,
        provenance={
            "task_type": task_type,
            "result_sha256": sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else "",
            "canonical_mutated": False,
        },
        next_command=next_command,
    )
    atomic_write_text(report, json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n")
    manifest_chapter = manifest_chapter_number(manifest or {})
    mark_tasks_for_output(
        root,
        chapter_number=manifest_chapter,
        output_path=candidate,
        to_status="validated" if ok else "invalid",
        command="intelligence validate",
        result=report,
        from_statuses=("awaiting_agent", "submitted", "invalid", "validated"),
    )
    return IntelligenceValidationResult(
        task_type=task_type,
        ok=ok,
        candidate_file=relative(root, candidate),
        report_file=relative(root, report),
        errors=tuple(errors),
        next_command=str(report_payload["next_command"]),
    )


def apply_intelligence_candidate(
    config: ConfigDocument,
    *,
    task_type: str,
    file_path: str | Path,
    approved_by: str | None = None,
) -> IntelligenceApplyResult:
    root = resolve_project_root(config)
    spec = require_spec(task_type)
    if output_protocol_for_task(task_type) == DESIGN_DOCUMENT_SCHEMA:
        raise ValueError(
            "design_document_v1 cannot be applied directly; approve the Markdown, compile a "
            "canonical_delta_v1, then apply with --document and --delta."
        )
    candidate = resolve_candidate(root, file_path)
    validation = validate_intelligence_candidate(config, task_type=task_type, file_path=candidate)
    if not validation.ok:
        raise ValueError("Intelligence candidate is invalid: " + "; ".join(validation.errors))
    if spec["human"] and approved_by != "human":
        raise ValueError(f"{task_type} apply requires --approved-by human.")
    load_errors: list[str] = []
    manifest = manifest_for_output(root, task_type, candidate)
    payload = load_candidate(
        config,
        root,
        candidate,
        load_errors,
        task_type=task_type,
        spec=spec,
        manifest=manifest,
    )
    if payload is None or load_errors:
        raise ValueError("Validated intelligence candidate could not be reloaded: " + "; ".join(load_errors))
    scope = manifest or {}
    manifest_scope = scope.get("scope") if isinstance(scope.get("scope"), dict) else {}
    touched = apply_targets(root, task_type, payload, scope=manifest_scope)
    task_chapter = manifest_chapter_number(scope)
    with apply_transaction(
        root,
        command=f"intelligence apply {task_type}",
        chapter_number=task_chapter or None,
        source_paths=(candidate,),
        touched_paths=tuple(touched),
        metadata={
            "task_type": task_type,
            "task_id": scope.get("task_id", ""),
            "approved_by": approved_by or "explicit_cli",
            "requires_human_apply": bool(spec["human"]),
        },
    ) as transaction:
        write_targets(config, root, task_type, payload, scope=manifest_scope)
    mark_tasks_for_chapter_type(
        root,
        chapter_number=task_chapter,
        task_types=(task_type,),
        to_status="applied",
        command="intelligence apply",
        artifact=candidate,
        result=transaction.report_file,
        from_statuses=("validated",),
    )
    return IntelligenceApplyResult(
        task_type=task_type,
        status="applied",
        candidate_file=relative(root, candidate),
        touched_paths=tuple(relative(root, path) for path in touched),
        transaction_report=relative(root, transaction.report_file),
        next_command="longform-engine production next project.yaml",
    )


def record_chapter_direction_selection(
    config: ConfigDocument,
    *,
    document_path: str | Path,
    selected_option_id: str,
    user_adjustments: dict[str, Any] | None = None,
    repetition_reason: str = "",
    selected_by: str,
) -> ChapterDirectionSelectionResult:
    """Record the human choice separately from Agent-authored direction Markdown."""

    if selected_by != "human":
        raise ValueError("Chapter direction selection requires selected_by=human.")
    root = resolve_project_root(config)
    document = resolve_candidate(root, document_path)
    try:
        parsed = parse_design_document(
            document.read_text(encoding="utf-8"),
            expected_type="chapter_direction",
        )
    except (OSError, UnicodeError, AgentProtocolError) as exc:
        raise ValueError(f"Chapter direction document is invalid: {exc}") from exc
    option_ids = chapter_direction_option_ids(parsed)
    selected_option_id = str(selected_option_id or "").strip()
    if selected_option_id not in option_ids:
        raise ValueError("selected_option_id must reference one stable option ID in the document.")
    adjustments = {} if user_adjustments is None else user_adjustments
    if not isinstance(adjustments, dict) or any(
        not isinstance(key, str)
        or not key.strip()
        or value in (None, "", [], {})
        for key, value in adjustments.items()
    ):
        raise ValueError("user_adjustments must be an object with non-empty field names and values.")
    if not isinstance(repetition_reason, str):
        raise ValueError("repetition_reason must be text.")
    manifest = manifest_for_output(root, "chapter_direction", document)
    if manifest is None:
        raise ValueError("Chapter direction document is not bound to an active Agent task.")
    chapter_number = manifest_chapter_number(manifest)
    if chapter_number <= 0:
        raise ValueError("Chapter direction task has no valid chapter scope.")
    document_hash = sha256(document.read_bytes()).hexdigest()
    selection_path = chapter_direction_selection_path(root, document)
    selection = {
        "schema": "chapter_direction_selection_v1",
        "task_id": str(manifest.get("task_id") or ""),
        "chapter_number": chapter_number,
        "document_path": relative(root, document),
        "document_sha256": document_hash,
        "option_ids": list(option_ids),
        "selected_option_id": selected_option_id,
        "user_adjustments": adjustments,
        "repetition_reason": repetition_reason.strip(),
        "selected_by": selected_by,
        "selected_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_text(selection_path, json.dumps(selection, ensure_ascii=False, indent=2) + "\n")
    selection_hash = sha256(selection_path.read_bytes()).hexdigest()
    return ChapterDirectionSelectionResult(
        chapter_number=chapter_number,
        document_file=relative(root, document),
        selection_file=relative(root, selection_path),
        document_sha256=document_hash,
        selection_sha256=selection_hash,
        selected_option_id=selected_option_id,
        next_command=(
            "longform-engine intelligence approve project.yaml --task-type chapter_direction "
            f"--document {relative(root, document)} --approved-by human"
        ),
    )


def approve_design_document(
    config: ConfigDocument,
    *,
    task_type: str,
    document_path: str | Path,
    approved_by: str,
) -> DesignApprovalResult:
    if task_type not in DESIGN_INTELLIGENCE_TASK_TYPES:
        raise ValueError(f"{task_type} is not a design_document_v1 task.")
    if approved_by != "human":
        raise ValueError("Design document approval requires --approved-by human.")
    root = resolve_project_root(config)
    document = resolve_candidate(root, document_path)
    validation = validate_intelligence_candidate(
        config,
        task_type=task_type,
        file_path=document,
    )
    if not validation.ok:
        raise ValueError("Design document is invalid: " + "; ".join(validation.errors))
    manifest = manifest_for_output(root, task_type, document)
    if manifest is None:
        raise ValueError("Design document is not bound to an active Agent task.")
    document_hash = sha256(document.read_bytes()).hexdigest()
    selection_file = ""
    selection_hash = ""
    if task_type == "chapter_direction":
        selection = load_chapter_direction_selection(root, document)
        selection_file = str(selection["selection_file"])
        selection_hash = str(selection["selection_sha256"])
    approval = design_approval_path(root, document)
    approval_payload = {
        "schema": "design_document_approval_v1",
        "task_id": str(manifest.get("task_id") or ""),
        "task_type": task_type,
        "document_path": relative(root, document),
        "document_sha256": document_hash,
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    if task_type == "chapter_direction":
        approval_payload.update(
            {
                "selection_file": selection_file,
                "selection_sha256": selection_hash,
            }
        )
    atomic_write_text(approval, json.dumps(approval_payload, ensure_ascii=False, indent=2) + "\n")
    mark_tasks_for_output(
        root,
        chapter_number=manifest_chapter_number(manifest),
        output_path=document,
        to_status="approved",
        command="intelligence approve",
        result=approval,
        from_statuses=("validated", "approved"),
    )
    return DesignApprovalResult(
        task_type=task_type,
        document_file=relative(root, document),
        approval_file=relative(root, approval),
        document_sha256=document_hash,
        selection_file=selection_file,
        selection_sha256=selection_hash,
        next_command=(
            "longform-engine intelligence compile-task project.yaml "
            f"--task-type {task_type} --document {relative(root, document)}"
        ),
    )


def create_design_compile_task(
    config: ConfigDocument,
    *,
    task_type: str,
    document_path: str | Path,
) -> IntelligenceTaskResult:
    if task_type not in DESIGN_INTELLIGENCE_TASK_TYPES:
        raise ValueError(f"{task_type} is not a design_document_v1 task.")
    root = resolve_project_root(config)
    document = resolve_candidate(root, document_path)
    approval = load_design_approval(root, task_type, document)
    source_manifest = manifest_for_output(root, task_type, document)
    if source_manifest is None:
        raise ValueError("Approved design document has no active source task.")
    source_task = next(
        (
            item
            for item in list_manifests(root)
            if item.get("task_id") == source_manifest.get("task_id")
        ),
        {},
    )
    if source_task.get("status") != "approved":
        raise ValueError("Design document must be approved before semantic compilation.")
    scope = dict(source_manifest.get("scope") or {})
    token = scope_token(scope)
    base = f"design_semantic_compile.{task_type}.{token}"
    instruction = root / "50_workbench" / "intelligence_tasks" / f"{base}.md"
    delta = root / "50_workbench" / "intelligence_candidates" / f"{base}.delta.json"
    manifest_file = root / "50_workbench" / "agent_tasks" / f"{base}.manifest.json"
    selection_path = (
        root / str(approval["selection_file"])
        if task_type == "chapter_direction"
        else None
    )
    instruction_text = render_design_compile_instruction(
        task_type=task_type,
        document=relative(root, document),
        document_hash=str(approval["document_sha256"]),
        domain_schema=str(TASK_SPECS[task_type]["schema"]),
        output=relative(root, delta),
        selection=(
            load_chapter_direction_selection(root, document)
            if task_type == "chapter_direction"
            else None
        ),
    )
    atomic_write_text(instruction, instruction_text)
    document_rel = relative(root, document)
    delta_rel = relative(root, delta)
    scope_args = scope_command_args(scope)
    validate_command = (
        "longform-engine intelligence compile-validate project.yaml "
        f"--task-type {task_type} --document {document_rel} --delta {delta_rel}"
    )
    apply_command = (
        "longform-engine intelligence apply project.yaml "
        f"--task-type {task_type} --document {document_rel} --delta {delta_rel} --approved-by human"
    )
    manifest = build_manifest(
        root,
        task_type="design_semantic_compile",
        chapter_number=int(scope.get("chapter_number") or 0) or None,
        scope=scope,
        input_files=(instruction, document, *([selection_path] if selection_path is not None else [])),
        allowed_output_paths=(delta,),
        output_schema=CANONICAL_DELTA_SCHEMA,
        validate_command=validate_command,
        apply_command=apply_command,
        failure_next_command=(
            "longform-engine intelligence compile-task project.yaml "
            f"--task-type {task_type} --document {document_rel}{scope_args}"
        ),
        canonical_targets=intelligence_canonical_targets(root, task_type, scope),
        requires_human_apply=True,
        context_policy={
            "required_files": [instruction, document, *([selection_path] if selection_path is not None else [])],
            "optional_files": [],
            "compiled_brief": instruction,
            "selection_report": instruction,
            "trigger_codes": [task_type],
        },
        task_id=f"design_semantic_compile:{task_type}:{token}:v4",
    )
    written = write_manifest(root, manifest, manifest_file)
    return IntelligenceTaskResult(
        task_type="design_semantic_compile",
        task_id=str(manifest["task_id"]),
        manifest_file=relative(root, written),
        instruction_file=relative(root, instruction),
        candidate_file=delta_rel,
        next_command=f"longform-engine agent-task brief project.yaml {manifest['task_id']}",
    )


def validate_design_compile_delta(
    config: ConfigDocument,
    *,
    task_type: str,
    document_path: str | Path,
    delta_path: str | Path,
    record_result: bool = True,
) -> IntelligenceValidationResult:
    if task_type not in DESIGN_INTELLIGENCE_TASK_TYPES:
        raise ValueError(f"{task_type} is not a design_document_v1 task.")
    root = resolve_project_root(config)
    document = resolve_candidate(root, document_path)
    delta = resolve_candidate(root, delta_path)
    errors: list[str] = []
    approval = load_design_approval(root, task_type, document, errors=errors)
    manifest = manifest_for_output(root, "design_semantic_compile", delta)
    if manifest is None:
        errors.append("delta is not declared by an active design_semantic_compile task.")
    else:
        _task, control_errors = validate_current_task_result(
            root,
            chapter_number=manifest_chapter_number(manifest),
            task_type="design_semantic_compile",
            output_path=delta,
            allowed_statuses=("submitted", "validated") if record_result else ("validated",),
        )
        errors.extend(control_errors)
    payload: dict[str, Any] = {}
    try:
        loaded = json.loads(delta.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("delta must be a JSON object")
        payload = loaded
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"delta is not valid UTF-8 JSON: {exc}")
    if payload:
        errors.extend(validate_canonical_delta(payload, task_type="design_semantic_compile"))
        if payload.get("uncertainties"):
            errors.append("canonical delta uncertainties must be resolved before apply.")
        evidence_records, evidence_errors = validate_review_evidence_for_source(
            {"findings": [{"evidence_ids": item} for item in (payload.get("evidence") or {}).values()]},
            source_path=relative(root, document),
            source_text=document.read_text(encoding="utf-8"),
        )
        errors.extend(evidence_errors)
        errors.extend(validate_delta_evidence_completeness(payload))
        errors.extend(validate_delta_document_grounding(payload, evidence_records))
        try:
            domain_payload = canonical_delta_domain_payload(
                payload,
                task_type="design_semantic_compile",
                domain_schema=str(TASK_SPECS[task_type]["schema"]),
                cli_fields=design_cli_fields(config, root, task_type, manifest),
            )
        except AgentProtocolError as exc:
            errors.append(str(exc))
        else:
            validate_payload(
                config,
                root,
                task_type,
                TASK_SPECS[task_type],
                domain_payload,
                manifest,
                errors,
            )
    report = root / "50_workbench" / "intelligence_validations" / f"{delta.stem}.validation.json"
    ok = not errors
    next_command = (
        "longform-engine intelligence apply project.yaml "
        f"--task-type {task_type} --document {relative(root, document)} "
        f"--delta {relative(root, delta)} --approved-by human"
        if ok
        else str(
            manifest_commands(manifest or {}).get("failure")
            or "longform-engine intelligence compile-task project.yaml "
            f"--task-type {task_type} --document {relative(root, document)}"
        )
    )
    report_payload = build_validation_report(
        ok=ok,
        stage="intelligence_compile_validate",
        subject=relative(root, delta),
        errors=errors,
        blockers=errors,
        provenance={
            "task_type": task_type,
            "document_path": relative(root, document),
            "document_sha256": approval.get("document_sha256", "") if isinstance(approval, dict) else "",
            "delta_sha256": sha256(delta.read_bytes()).hexdigest() if delta.is_file() else "",
            "canonical_mutated": False,
        },
        next_command=next_command,
    )
    if record_result:
        atomic_write_text(report, json.dumps(report_payload, ensure_ascii=False, indent=2) + "\n")
        mark_tasks_for_output(
            root,
            chapter_number=manifest_chapter_number(manifest or {}),
            output_path=delta,
            to_status="validated" if ok else "invalid",
            command="intelligence compile-validate",
            result=report,
            from_statuses=("submitted", "validated", "invalid"),
        )
    return IntelligenceValidationResult(
        task_type=task_type,
        ok=ok,
        candidate_file=relative(root, delta),
        report_file=relative(root, report),
        errors=tuple(errors),
        next_command=next_command,
    )


def apply_compiled_design(
    config: ConfigDocument,
    *,
    task_type: str,
    document_path: str | Path,
    delta_path: str | Path,
    approved_by: str,
) -> IntelligenceApplyResult:
    if approved_by != "human":
        raise ValueError("Compiled design apply requires --approved-by human.")
    root = resolve_project_root(config)
    document = resolve_candidate(root, document_path)
    delta = resolve_candidate(root, delta_path)
    validation = validate_design_compile_delta(
        config,
        task_type=task_type,
        document_path=document,
        delta_path=delta,
        record_result=False,
    )
    if not validation.ok:
        raise ValueError("Compiled design delta is invalid: " + "; ".join(validation.errors))
    manifest = manifest_for_output(root, "design_semantic_compile", delta)
    if manifest is None:
        raise ValueError("Compiled design delta has no current task.")
    raw_delta = json.loads(delta.read_text(encoding="utf-8"))
    domain_payload = canonical_delta_domain_payload(
        raw_delta,
        task_type="design_semantic_compile",
        domain_schema=str(TASK_SPECS[task_type]["schema"]),
        cli_fields=design_cli_fields(config, root, task_type, manifest),
    )
    scope = dict(manifest.get("scope") or {})
    canonical_document = design_document_target(root, task_type, scope)
    canonical_delta = design_delta_target(root, task_type, scope)
    selection = (
        load_chapter_direction_selection(root, document)
        if task_type == "chapter_direction"
        else {}
    )
    touched = design_apply_targets(root, task_type, scope, payload=domain_payload)
    touched.append(root / "50_workbench" / "agent_tasks")
    touched = list(dict.fromkeys(touched))
    with apply_transaction(
        root,
        command=f"intelligence apply compiled {task_type}",
        chapter_number=int(scope.get("chapter_number") or 0) or None,
        source_paths=(
            document,
            delta,
            *(
                [root / str(selection["selection_file"])]
                if selection
                else []
            ),
        ),
        touched_paths=tuple(touched),
        metadata={
            "task_type": task_type,
            "compile_task_id": str(manifest.get("task_id") or ""),
            "approved_by": approved_by,
            "document_sha256": sha256(document.read_bytes()).hexdigest(),
            "selection_sha256": str(selection.get("selection_sha256") or ""),
        },
    ) as transaction:
        atomic_write_text(canonical_document, document.read_text(encoding="utf-8").rstrip() + "\n")
        write_json(
            canonical_delta,
            {
                "schema": "approved_design_delta_v1",
                "task_type": task_type,
                "scope": scope,
                "document_path": relative(root, canonical_document),
                "document_sha256": sha256(document.read_bytes()).hexdigest(),
                "selection": (
                    {
                        "path": str(selection["selection_file"]),
                        "sha256": str(selection["selection_sha256"]),
                        "selected_option_id": str(selection["selected_option_id"]),
                    }
                    if selection
                    else {}
                ),
                "delta": raw_delta,
            },
        )
        write_targets(config, root, task_type, domain_payload, scope=scope)
        if task_type == "chapter_direction" and selection:
            from longform_engine.chapter_contract import stamp_chapter_contract
            from longform_engine.orchestration.pipeline import upsert_chapter_plan, write_chapter_card_artifacts

            chapter_number = int(scope.get("chapter_number") or 0)
            card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
            card = read_json(card_path, {})
            direction_selection = card.get("direction_selection") if isinstance(card, dict) else None
            if not isinstance(card, dict) or not isinstance(direction_selection, dict):
                raise ValueError("chapter direction apply did not produce a direction selection record.")
            direction_selection["selection_file"] = str(selection["selection_file"])
            direction_selection["selection_sha256"] = str(selection["selection_sha256"])
            direction_selection["document_sha256"] = sha256(document.read_bytes()).hexdigest()
            stamp_chapter_contract(card)
            write_chapter_card_artifacts(root, card)
            upsert_chapter_plan(root, card)
        mark_tasks_for_chapter_type(
            root,
            chapter_number=int(scope.get("chapter_number") or 0),
            task_types=("design_semantic_compile", task_type),
            to_status="applied",
            command="intelligence apply compiled",
            artifact=delta,
            result=transaction.report_file,
            from_statuses=("validated", "approved"),
        )
    return IntelligenceApplyResult(
        task_type=task_type,
        status="applied",
        candidate_file=relative(root, delta),
        touched_paths=tuple(relative(root, path) for path in touched),
        transaction_report=relative(root, transaction.report_file),
        next_command="longform-engine production next project.yaml",
    )


def design_approval_path(root: Path, document: Path) -> Path:
    return root / "50_workbench" / "intelligence_approvals" / f"{document.stem}.approval.json"


def chapter_direction_selection_path(root: Path, document: Path) -> Path:
    return root / "50_workbench" / "intelligence_selections" / f"{document.stem}.selection.json"


def load_chapter_direction_selection(
    root: Path,
    document: Path,
    *,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    target = errors if errors is not None else []
    path = chapter_direction_selection_path(root, document)
    payload = read_json(path, {})
    required = {
        "schema", "task_id", "chapter_number", "document_path", "document_sha256",
        "option_ids", "selected_option_id", "user_adjustments", "repetition_reason",
        "selected_by", "selected_at",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        target.append("chapter direction has no valid chapter_direction_selection_v1 record.")
    else:
        if payload.get("schema") != "chapter_direction_selection_v1":
            target.append("chapter direction selection schema is invalid.")
        if payload.get("document_path") != relative(root, document):
            target.append("chapter direction selection points to a different document.")
        document_hash = sha256(document.read_bytes()).hexdigest() if document.is_file() else ""
        if payload.get("document_sha256") != document_hash:
            target.append("chapter direction document changed after selection; select an option again.")
        try:
            parsed = parse_design_document(
                document.read_text(encoding="utf-8"),
                expected_type="chapter_direction",
            )
            option_ids = chapter_direction_option_ids(parsed)
        except (OSError, UnicodeError, AgentProtocolError) as exc:
            target.append(f"chapter direction selection cannot parse current options: {exc}")
            option_ids = ()
        if payload.get("option_ids") != list(option_ids):
            target.append("chapter direction option IDs changed after selection; select an option again.")
        if payload.get("selected_option_id") not in option_ids:
            target.append("chapter direction selected option is not present in the current document.")
        if payload.get("selected_by") != "human":
            target.append("chapter direction selection must be recorded by human.")
        if not isinstance(payload.get("user_adjustments"), dict):
            target.append("chapter direction user_adjustments must be an object.")
        if not isinstance(payload.get("repetition_reason"), str):
            target.append("chapter direction repetition_reason must be text.")
    if target and errors is None:
        raise ValueError(" ".join(target))
    if not isinstance(payload, dict):
        return {}
    return {
        **payload,
        "selection_file": relative(root, path),
        "selection_sha256": sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
    }


def load_design_approval(
    root: Path,
    task_type: str,
    document: Path,
    *,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    target = errors if errors is not None else []
    path = design_approval_path(root, document)
    payload = read_json(path, {})
    if not isinstance(payload, dict) or payload.get("schema") != "design_document_approval_v1":
        target.append("design document has no valid human approval record.")
    else:
        if payload.get("task_type") != task_type:
            target.append("design approval task_type does not match the requested compilation.")
        if payload.get("document_path") != relative(root, document):
            target.append("design approval points to a different document.")
        current_hash = sha256(document.read_bytes()).hexdigest() if document.is_file() else ""
        if payload.get("document_sha256") != current_hash:
            target.append("design document changed after approval; revalidate and approve it again.")
        if payload.get("approved_by") != "human":
            target.append("design approval must be recorded by human.")
        if task_type == "chapter_direction":
            selection_errors: list[str] = []
            selection = load_chapter_direction_selection(root, document, errors=selection_errors)
            target.extend(selection_errors)
            if payload.get("selection_file") != selection.get("selection_file"):
                target.append("design approval points to a different chapter direction selection.")
            if payload.get("selection_sha256") != selection.get("selection_sha256"):
                target.append("chapter direction selection changed after approval; approve it again.")
    if target and errors is None:
        raise ValueError(" ".join(target))
    return payload if isinstance(payload, dict) else {}


def render_design_compile_instruction(
    *,
    task_type: str,
    document: str,
    document_hash: str,
    domain_schema: str,
    output: str,
    selection: dict[str, Any] | None = None,
) -> str:
    task_specific = (
        "chapter_direction 只编译 selected_direction、selection、canonical_refs、introduced_elements；"
        "不得把未选方向复制进 delta。"
        if task_type == "chapter_direction"
        else "只编译文档中经人工批准的最终设计事实。"
    )
    return "\n".join(
        (
            "# 设计文档语义编译任务",
            "",
            f"- 原设计任务：`{task_type}`",
            f"- 已批准文档：`{document}`",
            f"- 文档 SHA-256：`{document_hash}`",
            *(
                (
                    f"- 人工选择：`{selection['selected_option_id']}`",
                    f"- 选择 sidecar：`{selection['selection_file']}`",
                    f"- 选择 SHA-256：`{selection['selection_sha256']}`",
                )
                if selection is not None
                else ()
            ),
            f"- CLI 内部领域 schema：`{domain_schema}`",
            f"- 唯一输出：`{output}`",
            "",
            "## 编译职责",
            "只把已批准 Markdown 中明确成立的事实编译为 canonical_delta_v1。",
            "changes 使用目标领域字段，但不要写 schema、路径、hash、章节范围、命令或时间。",
            "evidence 必须使用 /changes/... JSON Pointer 映射到 document@start:end。",
            "备选方案、被否决内容、示例和分析理由不能作为已批准事实。",
            task_specific,
            *(
                (
                    "chapter_direction 的 selection 由 CLI 从 sidecar 注入；delta 不得自行编写 selection。",
                )
                if selection is not None
                else ()
            ),
            "任何稳定 ID、窗口、关系或语义存在歧义时写入 uncertainties；CLI 将阻止 apply。",
            "",
        )
    )


def design_cli_fields(
    config: ConfigDocument,
    root: Path,
    task_type: str,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    scope = (manifest or {}).get("scope") if isinstance((manifest or {}).get("scope"), dict) else {}
    if task_type == "chapter_direction":
        chapter_number = int(scope.get("chapter_number") or 0)
        card = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
        document = (
            root / str(manifest_input_paths(manifest or {})[1])
            if len(manifest_input_paths(manifest or {})) >= 2
            else None
        )
        if document is None or not document.is_file():
            raise ValueError("chapter direction compile task is missing its approved Markdown input.")
        selection = load_chapter_direction_selection(root, document)
        return {
            "chapter_number": chapter_number,
            "chapter_card_sha256": sha256(card.read_bytes()).hexdigest() if card.is_file() else "",
            "trigger_reasons": assess_chapter_direction(config, chapter_number)["reasons"],
            "selection": {
                "direction_id": str(selection["selected_option_id"]),
                "user_adjustments": dict(selection["user_adjustments"]),
                "repetition_reason": str(selection["repetition_reason"]),
            },
        }
    if task_type == "arc_simulation":
        return {
            "from_chapter": int(scope.get("from_chapter") or 0),
            "to_chapter": int(scope.get("to_chapter") or 0),
            "basis_hashes": current_basis_hashes(root),
            "approved_by": "human",
            "status": "approved",
        }
    if task_type == "outline_revision":
        return {
            "from_chapter": int(scope.get("from_chapter") or 0),
            "to_chapter": int(scope.get("to_chapter") or 0),
        }
    if task_type == "book_ideation":
        return {
            "round": next_book_ideation_round(root),
            "dimension": next_book_ideation_dimension(root),
        }
    return {}


def validate_delta_evidence_completeness(payload: dict[str, Any]) -> list[str]:
    changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    errors: list[str] = []
    for key in changes:
        escaped = str(key).replace("~", "~0").replace("/", "~1")
        prefix = f"/changes/{escaped}"
        if not any(pointer == prefix or pointer.startswith(prefix + "/") for pointer in evidence):
            errors.append(f"change field `{key}` has no evidence pointer.")
    return errors


def validate_delta_document_grounding(
    payload: dict[str, Any],
    evidence_records: dict[str, dict[str, Any]],
) -> list[str]:
    """Require machine facts to be textually present in their bound Markdown evidence."""

    evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    errors: list[str] = []
    for pointer, evidence_ids in evidence.items():
        value = json_pointer_value(payload, str(pointer))
        if value is None:
            continue
        excerpts = "\n".join(
            str(evidence_records.get(str(evidence_id), {}).get("excerpt") or "")
            for evidence_id in evidence_ids if str(evidence_id)
        )
        compact_excerpt = normalize_grounding_text(excerpts)
        for scalar in design_fact_scalars(value):
            if normalize_grounding_text(scalar) not in compact_excerpt:
                errors.append(
                    f"delta fact `{scalar[:80]}` at `{pointer}` is absent from its Markdown evidence."
                )
                if len(errors) >= 20:
                    return errors
    return errors


def json_pointer_value(payload: dict[str, Any], pointer: str) -> Any:
    current: Any = payload
    if not pointer.startswith("/"):
        return None
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return None
    return current


def design_fact_scalars(value: Any) -> list[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if len(text) >= 2 else []
    if isinstance(value, list):
        return [item for child in value for item in design_fact_scalars(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in design_fact_scalars(child)]
    return []


def normalize_grounding_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def design_document_target(root: Path, task_type: str, scope: dict[str, Any]) -> Path:
    token = scope_token(scope)
    if task_type == "chapter_direction":
        return root / "20_outline" / "chapter_directions" / f"{token}.md"
    if task_type in {"outline_design", "arc_simulation", "outline_extension", "outline_revision"}:
        return root / "20_outline" / "design_documents" / f"{task_type}.{token}.md"
    return root / "10_bible" / "design_documents" / f"{task_type}.{token}.md"


def design_delta_target(root: Path, task_type: str, scope: dict[str, Any]) -> Path:
    return root / "30_state" / "design_deltas" / f"{task_type}.{scope_token(scope)}.json"


def design_apply_targets(
    root: Path,
    task_type: str,
    scope: dict[str, Any],
    *,
    payload: dict[str, Any] | None = None,
) -> list[Path]:
    if payload is None:
        targets = [root / item for item in TASK_SPECS[task_type]["targets"]]
        if task_type in {"book_design", "fanfiction_design", "outline_design"}:
            targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
        if task_type == "arc_simulation":
            targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
            targets.append(
                arc_simulation_path(
                    root,
                    int(scope.get("from_chapter") or 0),
                    int(scope.get("to_chapter") or 0),
                )
            )
        if task_type == "outline_extension":
            targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
        if task_type == "chapter_direction":
            chapter = int(scope.get("chapter_number") or 0)
            targets.extend(
                (
                    root / "20_outline" / "chapter_cards" / f"ch{chapter:03d}.json",
                    root / "20_outline" / "chapter_cards" / f"ch{chapter:03d}.md",
                    root / "20_outline" / "chapter_plan.json",
                )
            )
        if task_type == "outline_revision":
            targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
            targets.extend(
                outline_revision_side_effect_targets(
                    root,
                    range(
                        int(scope.get("from_chapter") or 0),
                        int(scope.get("to_chapter") or 0) + 1,
                    ),
                )
            )
            targets.append(
                root
                / "20_outline"
                / "revise_reports"
                / f"agent_revision_ch{int(scope.get('from_chapter') or 0):03d}-ch{int(scope.get('to_chapter') or 0):03d}.json"
            )
    else:
        targets = apply_targets(root, task_type, payload, scope=scope)
    targets.extend(
        (
            design_document_target(root, task_type, scope),
            design_delta_target(root, task_type, scope),
        )
    )
    return list(dict.fromkeys(targets))


def require_spec(task_type: str) -> dict[str, Any]:
    if task_type not in TASK_SPECS:
        raise ValueError(f"task_type must be one of: {', '.join(INTELLIGENCE_TASK_TYPES)}")
    return TASK_SPECS[task_type]


def assess_project_readiness(config: ConfigDocument) -> ProjectReadinessResult:
    """Verify that opening decisions and full-book outline were explicitly applied."""

    root = resolve_project_root(config)
    state = read_json(root / "30_state" / "novel_state.json", {})
    status = str(state.get("status") or "initialized") if isinstance(state, dict) else "initialized"
    if status == "initialized":
        return ProjectReadinessResult(False, "open_book", "", ("open-book confirmations have not been recorded.",))
    compiled_story = compile_story_profile(config.data["story_profile"], market_ids=set(BUILTIN_MARKET_IDS))
    if not compiled_story["ready"]:
        issues = [
            "unresolved story-profile conflict: " + str(item["conflict_id"])
            for item in compiled_story["unresolved_conflicts"]
        ] + [
            "story-profile resolution does not match a selected conflict: " + item
            for item in compiled_story["unused_resolution_ids"]
        ]
        return ProjectReadinessResult(False, "story_profile_conflict", "", tuple(issues))
    markers = state.get("project_intelligence") if isinstance(state, dict) and isinstance(state.get("project_intelligence"), dict) else {}
    creation_mode = str(config.data.get("creation", {}).get("mode") or "original")
    if creation_mode == "fanfiction":
        canon_marker = markers.get("fanfiction_canon") if isinstance(markers.get("fanfiction_canon"), dict) else {}
        if canon_marker.get("status") != "applied":
            return ProjectReadinessResult(
                False,
                "fanfiction_canon",
                "fanfiction_canon",
                ("fanfiction_canon has not been explicitly applied.",),
            )
        canon_errors: list[str] = []
        canon_payload = read_json(root / "10_bible" / "fanfiction" / "source_canon.json", {})
        if not isinstance(canon_payload, dict):
            canon_errors.append("10_bible/fanfiction/source_canon.json must be an object.")
        else:
            validate_fanfiction_canon(
                config,
                {
                    key: canon_payload.get(key)
                    for key in ("schema", "continuity_mode", "sources")
                },
                canon_errors,
            )
        if canon_errors:
            return ProjectReadinessResult(False, "fanfiction_canon", "fanfiction_canon", tuple(canon_errors))
    ideation_errors = book_ideation_readiness_errors(root)
    if ideation_errors:
        return ProjectReadinessResult(False, "book_ideation", "book_ideation", tuple(ideation_errors))
    if creation_mode == "fanfiction":
        design_marker = markers.get("fanfiction_design") if isinstance(markers.get("fanfiction_design"), dict) else {}
        if design_marker.get("status") != "applied":
            return ProjectReadinessResult(
                False,
                "fanfiction_design",
                "fanfiction_design",
                ("fanfiction_design has not been explicitly applied.",),
            )
        design_errors: list[str] = []
        design_payload = read_json(root / "10_bible" / "fanfiction" / "fanfiction_bible.json", {})
        if not isinstance(design_payload, dict):
            design_errors.append("10_bible/fanfiction/fanfiction_bible.json must be an object.")
        else:
            validate_fanfiction_design(config, root, design_payload, design_errors)
        if design_errors:
            return ProjectReadinessResult(False, "fanfiction_design", "fanfiction_design", tuple(design_errors))
    else:
        book_marker = markers.get("book_design") if isinstance(markers.get("book_design"), dict) else {}
        if book_marker.get("status") != "applied":
            return ProjectReadinessResult(False, "book_design", "book_design", ("book_design has not been explicitly applied.",))
    book_errors: list[str] = []
    expression = read_json(root / "10_bible" / "character_expression.json", {})
    validate_book_design(
        {
            "schema": "book_design_candidate_v2",
            "creative_brief": read_json(root / "10_bible" / "creative_brief.json", {}),
            "world_markdown": read_text(root / "10_bible" / "world.md"),
            "power_system_markdown": read_text(root / "10_bible" / "power_system.md"),
            "characters": read_json(root / "10_bible" / "characters.json", []),
            "relationships": read_json(root / "10_bible" / "relationships.json", []),
            "narrative_expression_profile": (
                expression.get("narrative_expression_profile") if isinstance(expression, dict) else None
            ),
            "character_expression_contracts": (
                expression.get("character_expression_contracts") if isinstance(expression, dict) else None
            ),
        },
        book_errors,
    )
    if book_errors:
        return ProjectReadinessResult(False, "book_design", "book_design", tuple(book_errors))
    outline_marker = markers.get("outline_design") if isinstance(markers.get("outline_design"), dict) else {}
    if outline_marker.get("status") != "applied":
        return ProjectReadinessResult(False, "outline_design", "outline_design", ("outline_design has not been explicitly applied.",))
    outline_errors: list[str] = []
    outline_payload = {
        "story_arcs": read_json(root / "20_outline" / "story_arcs.json", []),
        "volumes": read_json(root / "20_outline" / "volumes.json", []),
        "chapter_plan": read_json(root / "20_outline" / "chapter_plan.json", []),
        "foreshadowing_ledger": [
            {
                key: item.get(key)
                for key in ("id", "description", "plant", "payoff", "completion_required", "status")
            }
            for item in read_json(root / "20_outline" / "foreshadowing_ledger.json", [])
            if isinstance(item, dict)
        ],
        "planning_window": read_json(root / "20_outline" / "planning_window.json", {}),
    }
    if not read_text(root / "20_outline" / "book_outline.md").strip():
        outline_errors.append("20_outline/book_outline.md must contain the approved macro outline.")
    validate_canonical_rolling_outline(config, outline_payload, outline_errors)
    if outline_errors:
        return ProjectReadinessResult(False, "outline_design", "outline_design", tuple(outline_errors))
    expression_marker = markers.get("character_expression_design")
    expression_path = root / "10_bible" / "character_expression.json"
    expression_ready, expression_errors = character_expression_readiness(root)
    if (isinstance(expression_marker, dict) or expression_path.is_file()) and not expression_ready:
        return ProjectReadinessResult(
            False,
            "character_expression_design",
            "character_expression_design",
            tuple(expression_errors),
        )
    return ProjectReadinessResult(True, "ready", "", ())


def task_scope(
    spec: dict[str, Any],
    *,
    chapter_number: int | None,
    from_chapter: int | None,
    to_chapter: int | None,
) -> dict[str, Any]:
    if spec["scope"] == "chapter":
        if chapter_number is None or chapter_number <= 0:
            raise ValueError("chapter_direction requires --chapter N.")
        if from_chapter is not None or to_chapter is not None:
            raise ValueError("chapter scope cannot use --from-chapter/--to-chapter.")
        return {"kind": "chapter", "chapter_number": chapter_number}
    if spec["scope"] == "range":
        if from_chapter is None or to_chapter is None or from_chapter <= 0 or to_chapter < from_chapter:
            raise ValueError(f"{spec['scope']} task requires --from-chapter N --to-chapter M with N <= M.")
        return {"kind": "range", "from_chapter": from_chapter, "to_chapter": to_chapter}
    if chapter_number is not None:
        raise ValueError(f"{spec['scope']} scope does not accept --chapter.")
    if from_chapter is not None or to_chapter is not None:
        if from_chapter is None or to_chapter is None or from_chapter <= 0 or to_chapter < from_chapter:
            raise ValueError("range scope requires both --from-chapter and --to-chapter.")
        return {"kind": "range", "from_chapter": from_chapter, "to_chapter": to_chapter}
    return {"kind": "project"}


def scope_token(scope: dict[str, Any]) -> str:
    if scope["kind"] == "chapter":
        return f"ch{scope['chapter_number']:03d}"
    if scope["kind"] == "range":
        return f"ch{scope['from_chapter']:03d}-ch{scope['to_chapter']:03d}"
    return "project"


def scope_command_args(scope: dict[str, Any]) -> str:
    if scope["kind"] == "chapter":
        return f" --chapter {scope['chapter_number']}"
    if scope["kind"] != "range":
        return ""
    return f" --from-chapter {scope['from_chapter']} --to-chapter {scope['to_chapter']}"


def intelligence_default_inputs(
    root: Path,
    task_type: str,
    spec: dict[str, Any],
    scope: dict[str, Any],
) -> list[Path]:
    candidates = [root / str(item) for item in spec["defaults"]]
    if task_type in {"book_design", "fanfiction_design"}:
        candidates.append(root / "10_bible" / "creative_decisions.json")
    if task_type == "character_expression_design":
        style_profile = root / "10_bible" / "style_profiles" / "current_style_profile.json"
        if style_profile.is_file():
            candidates.append(style_profile)
    if task_type == "character_expression_review":
        for chapter_number in range(int(scope["from_chapter"]), int(scope["to_chapter"]) + 1):
            final = manuscript_chapter_path(root, chapter_number, lane="final")
            draft = manuscript_chapter_path(root, chapter_number, lane="draft")
            source = final if final.is_file() else draft
            if not source.is_file():
                raise ValueError(
                    "character_expression_review requires a final or draft source for "
                    f"chapter {chapter_number}."
                )
            candidates.append(source)
    if task_type == "book_ideation":
        candidates.append(root / "10_bible" / "creative_decisions.json")
    if task_type == "chapter_direction":
        chapter_number = int(scope["chapter_number"])
        candidates.extend(
            [
                root / "project.yaml",
                root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
                root / "20_outline" / "chapter_plan.json",
                root / "20_outline" / "foreshadowing_ledger.json",
                root / "10_bible" / "creative_brief.json",
            ]
        )
    if task_type == "arc_simulation":
        candidates.extend(
            sorted((root / "60_rag" / "memory" / "characters").glob("*.json"))
        )
    return [path for path in candidates if path.is_file()]


def write_fanfiction_design_context(config: ConfigDocument, root: Path) -> Path:
    """Compile bounded canon and approved decisions for one fanfiction design task."""

    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    decisions_path = root / "10_bible" / "creative_decisions.json"
    canon = read_json(canon_path, {})
    decisions = read_json(decisions_path, {})
    if not isinstance(canon, dict) or canon.get("schema") != "fanfiction_source_canon_v1":
        raise ValueError("fanfiction_design requires an applied fanfiction source canon.")
    if not isinstance(decisions, dict) or decisions.get("schema") != "book_ideation_decisions_v1":
        decisions = {"decisions": {}}

    def text(value: Any, limit: int) -> str:
        normalized = " ".join(str(value or "").split())
        return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"

    def rows(value: Any, fields: tuple[tuple[str, int], ...], limit: int) -> tuple[list[dict[str, Any]], int]:
        records = value if isinstance(value, list) else []
        selected: list[dict[str, Any]] = []
        for item in records[:limit]:
            if not isinstance(item, dict):
                continue
            compact: dict[str, Any] = {}
            for field, char_limit in fields:
                field_value = item.get(field)
                if isinstance(field_value, list):
                    compact[field] = [text(entry, char_limit) for entry in field_value[:5]]
                elif isinstance(field_value, (int, float, bool)):
                    compact[field] = field_value
                else:
                    compact[field] = text(field_value, char_limit)
            selected.append(compact)
        return selected, max(0, len(records) - len(selected))

    compact_sources: list[dict[str, Any]] = []
    omissions: dict[str, int] = {}
    remaining_characters = 24
    for source in canon.get("sources") or []:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("source_id") or "")
        character_limit = max(1, min(remaining_characters, 12))
        characters, omitted_characters = rows(
            source.get("characters"),
            (("id", 96), ("name", 80), ("summary", 180), ("motivation", 140), ("voice_traits", 80)),
            character_limit,
        )
        remaining_characters = max(0, remaining_characters - len(characters))
        relationships, omitted_relationships = rows(
            source.get("relationships"),
            (("id", 96), ("source_character_id", 96), ("target_character_id", 96), ("stage", 80), ("summary", 160)),
            16,
        )
        world_rules, omitted_rules = rows(source.get("world_rules"), (("id", 96), ("summary", 180)), 16)
        abilities, omitted_abilities = rows(
            source.get("abilities"),
            (("id", 96), ("name", 80), ("summary", 160), ("limits", 80)),
            12,
        )
        timeline, omitted_timeline = rows(
            source.get("timeline"), (("id", 96), ("order", 0), ("summary", 160)), 16
        )
        events, omitted_events = rows(
            source.get("canon_events"), (("id", 96), ("order", 0), ("summary", 160)), 16
        )
        unresolved, omitted_unresolved = rows(
            source.get("unresolved_questions"), (("id", 96), ("summary", 160)), 12
        )
        for label, count in (
            ("characters", omitted_characters),
            ("relationships", omitted_relationships),
            ("world_rules", omitted_rules),
            ("abilities", omitted_abilities),
            ("timeline", omitted_timeline),
            ("canon_events", omitted_events),
            ("unresolved_questions", omitted_unresolved),
        ):
            if count:
                omissions[f"{source_id}:{label}"] = count
        compact_sources.append(
            {
                "source_id": source_id,
                "title": text(source.get("title"), 120),
                "canon_cutoff": text(source.get("canon_cutoff"), 160),
                "characters": characters,
                "relationships": relationships,
                "world_rules": world_rules,
                "abilities": abilities,
                "timeline": timeline,
                "canon_events": events,
                "unresolved_questions": unresolved,
            }
        )

    project = config.data.get("project") if isinstance(config.data.get("project"), dict) else {}
    fanfiction = config.data.get("fanfiction") if isinstance(config.data.get("fanfiction"), dict) else {}
    novel = config.data.get("novel") if isinstance(config.data.get("novel"), dict) else {}
    length = config.data.get("length") if isinstance(config.data.get("length"), dict) else {}
    forecast = compile_length_forecast(length)
    payload = {
        "schema": "fanfiction_design_context_v1",
        "project_contract": {
            "title": project.get("title"),
            "continuity_mode": fanfiction.get("continuity_mode"),
            "configured_sources": fanfiction.get("sources") or [],
            "novel": novel,
            "length": forecast.to_dict(),
            "story_profile": config.data.get("story_profile", {}),
        },
        "approved_decisions": decisions.get("decisions") or {},
        "canon": compact_sources,
        "selection_report": {
            "strategy": "all configured sources; bounded characters, relationships, rules, abilities, timeline, events, and unresolved questions",
            "omitted_counts": omissions,
        },
        "canonical_provenance": [
            {
                "path": relative(root, path),
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "authority": "canonical_recheck_required",
            }
            for path in (canon_path, decisions_path, root / "project.yaml")
            if path.is_file()
        ],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    budget = resolve_context_budget_contract(root)
    payload["selection_report"]["estimated_units"] = estimate_text_units(rendered, budget.estimator)
    payload["selection_report"]["budget_profile"] = budget.profile
    payload["selection_report"]["capacity_units"] = budget.capacity_units
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    target = root / "50_workbench" / "intelligence_context" / "fanfiction_design.project.context.json"
    atomic_write_text(target, rendered)
    return target


def write_chapter_direction_context(
    config: ConfigDocument,
    root: Path,
    chapter_number: int,
) -> Path:
    """Compile one chapter's decision evidence without exposing the full outline."""

    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    plan_path = root / "20_outline" / "chapter_plan.json"
    ledger_path = root / "20_outline" / "foreshadowing_ledger.json"
    brief_path = root / "10_bible" / "creative_brief.json"
    arcs_path = root / "20_outline" / "story_arcs.json"
    volumes_path = root / "20_outline" / "volumes.json"
    characters_path = root / "10_bible" / "characters.json"
    expression_path = root / "10_bible" / "character_expression.json"
    structure_path = root / "30_state" / "quality" / "structure_history.jsonl"
    promise_path = root / LEDGER_PATH
    card = read_json(card_path, {})
    plan = read_json(plan_path, [])
    ledger = read_json(ledger_path, [])
    brief = read_json(brief_path, {})
    arcs = read_json(arcs_path, [])
    volumes = read_json(volumes_path, [])
    characters = read_json(characters_path, [])
    expression = read_json(expression_path, {})
    promises = load_reader_promise_ledger(root)
    simulation, simulation_path, simulation_hash = load_active_arc_simulation(
        root, chapter_number=chapter_number
    )
    card = card if isinstance(card, dict) else {}
    plan_rows = plan if isinstance(plan, list) else []
    ledger_rows = ledger if isinstance(ledger, list) else []
    plan_row = next(
        (
            item
            for item in plan_rows
            if isinstance(item, dict) and int(item.get("chapter_number") or 0) == chapter_number
        ),
        {},
    )
    active_threads = []
    for item in ledger_rows:
        if not isinstance(item, dict):
            continue
        plant = int(item.get("plant_chapter") or 0)
        window = item.get("payoff_window") if isinstance(item.get("payoff_window"), list) else []
        payoff_end = int(window[-1]) if window and isinstance(window[-1], int) else chapter_number
        if plant <= chapter_number <= payoff_end:
            active_threads.append(
                {
                    key: item.get(key)
                    for key in ("id", "thread_id", "description", "plant_chapter", "payoff_window", "status")
                    if item.get(key) not in (None, "", [], {})
                }
            )
        if len(active_threads) >= 8:
            break
    arc_id = str(plan_row.get("arc_id") or "") if isinstance(plan_row, dict) else ""
    volume_id = str(plan_row.get("volume_id") or "") if isinstance(plan_row, dict) else ""
    current_arc = next(
        (item for item in arcs if isinstance(item, dict) and str(item.get("id") or "") == arc_id),
        {},
    ) if isinstance(arcs, list) else {}
    current_volume = next(
        (item for item in volumes if isinstance(item, dict) and str(item.get("id") or "") == volume_id),
        {},
    ) if isinstance(volumes, list) else {}
    featured_ids = [
        str(item) for item in (card.get("featured_character_ids") or plan_row.get("featured_character_ids") or [])
    ][:5]
    character_by_id = {
        str(item.get("id")): item for item in characters if isinstance(item, dict) and item.get("id")
    } if isinstance(characters, list) else {}
    expression_rows = expression.get("character_expression_contracts") if isinstance(expression, dict) else []
    expression_by_id = {
        str(item.get("character_id")): item
        for item in expression_rows or []
        if isinstance(item, dict) and item.get("character_id")
    }
    compiled_story = compile_story_profile(config.data["story_profile"], market_ids=set(BUILTIN_MARKET_IDS))
    requested_facets = list(plan_row.get("active_facets") or []) if isinstance(plan_row, dict) else []
    source_paths = [
        path
        for path in (
            card_path, plan_path, ledger_path, brief_path, arcs_path, volumes_path,
            characters_path, expression_path, root / "project.yaml",
            structure_path, promise_path, simulation_path,
        )
        if path.is_file()
    ]
    payload = {
        "schema": "chapter_direction_context_v1",
        "chapter_number": chapter_number,
        "chapter_card": {
            key: card.get(key)
            for key in (
                "chapter_number",
                "title",
                "chapter_duty",
                "conflict",
                "chapter_turn",
                "primary_story_engine",
                "scene_carriers",
                "state_change_kind",
                "dramatic_method",
                "reader_gain",
                "cost",
                "relationship_move",
                "forbidden_reveals",
                "protected_reveals",
                "protected_story_outcomes",
                "protected_canon_outcomes",
                "prohibited_drift",
                "immediate_desire",
                "opposition_force",
                "key_failure",
                "irreversible_choice",
                "pov_character_id",
                "featured_character_ids",
            )
            if card.get(key) not in (None, "", [], {})
        },
        "chapter_plan": plan_row,
        "goal_ladder": {
            "book_goal": str((brief.get("design_decisions") or {}).get("long_conflict") or ""),
            "volume_goal": str(current_volume.get("goal") or ""),
            "arc_goal": str(current_arc.get("goal") or ""),
            "protagonist_goal": str((brief.get("design_decisions") or {}).get("protagonist_desire") or ""),
        },
        "active_story_facets": active_story_facets(compiled_story, requested_facets, limit=3),
        "featured_cast": [
            {
                "id": character_id,
                "name": str(character_by_id.get(character_id, {}).get("name") or character_id),
                "desire": str(
                    (plan_row.get("scene_wants") or {}).get(character_id)
                    or character_by_id.get(character_id, {}).get("goal")
                    or ""
                ),
                "voice": {
                    key: expression_by_id.get(character_id, {}).get(key)
                    for key in (
                        "perception_bias", "decision_pattern", "speech_register",
                        "conversation_tactics", "emotional_leakage", "physical_presence",
                    )
                    if expression_by_id.get(character_id, {}).get(key) not in (None, "", [], {})
                },
            }
            for character_id in featured_ids
        ],
        "active_foreshadowing": active_threads,
        "reader_promises": [
            item
            for item in promises["items"]
            if item["status"] not in {"paid", "retired"}
            and int(item["payoff_window"]["earliest"]) <= chapter_number
            <= int(item["payoff_window"]["latest"]) + 1
        ],
        "arc_causal_simulation": {
            **simulation,
            "path": relative(root, simulation_path),
            "sha256": simulation_hash,
        },
        "book_contract": {
            key: brief.get(key)
            for key in (
                "design_decisions", "reader_contract", "core_taboo", "story_profile",
                "story_engine_contract",
            )
            if isinstance(brief, dict) and brief.get(key) not in (None, "", [], {})
        },
        "recent_structure": [
            {
                key: item.get(key)
                for key in (
                    "chapter_number", "primary_story_engine", "primary_scene_carrier",
                    "state_change_kind", "dramatic_method", "exposition_carrier",
                )
            }
            for item in read_jsonl_records(structure_path)[-5:]
            if isinstance(item, dict)
        ],
        "project_contract": {
            "length": config.data.get("length", {}),
            "primary_market": str(config.data["story_profile"]["market"]["primary"]),
        },
        "provenance": [
            {"path": relative(root, path), "sha256": sha256(path.read_bytes()).hexdigest()}
            for path in source_paths
        ],
        "selection": {
            "mode": "single_chapter_projection",
            "full_chapter_plan_exposed": False,
            "active_foreshadow_limit": 8,
        },
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    budget = resolve_context_budget_contract(root)
    payload["selection"]["estimated_units"] = estimate_text_units(rendered, budget.estimator)
    payload["selection"]["budget_profile"] = budget.profile
    payload["selection"]["capacity_units"] = budget.capacity_units
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    path = root / "50_workbench" / "intelligence_tasks" / f"chapter_direction.ch{chapter_number:03d}.context.json"
    atomic_write_text(path, rendered)
    return path


def write_outline_extension_context(
    config: ConfigDocument,
    root: Path,
    scope: dict[str, Any],
) -> Path:
    """Compile bounded continuation evidence instead of resending the growing outline."""

    start = int(scope["from_chapter"])
    end = int(scope["to_chapter"])
    simulation, simulation_path, _simulation_hash = load_covering_arc_simulation(
        root,
        from_chapter=start,
        to_chapter=end,
    )
    plan_path = root / "20_outline" / "chapter_plan.json"
    arcs_path = root / "20_outline" / "story_arcs.json"
    volumes_path = root / "20_outline" / "volumes.json"
    ledger_path = root / "20_outline" / "foreshadowing_ledger.json"
    brief_path = root / "10_bible" / "creative_brief.json"
    plan = read_json(plan_path, [])
    arcs = read_json(arcs_path, [])
    volumes = read_json(volumes_path, [])
    ledger = read_json(ledger_path, [])
    brief = read_json(brief_path, {})
    recent_plan = [item for item in plan if isinstance(item, dict)][-8:] if isinstance(plan, list) else []
    active_threads = [
        {
            key: item.get(key)
            for key in ("id", "description", "plant", "payoff", "completion_required", "status")
            if item.get(key) not in (None, "", [], {})
        }
        for item in ledger
        if isinstance(item, dict) and str(item.get("status") or "") not in {"resolved", "expired"}
    ][:12] if isinstance(ledger, list) else []
    source_paths = [
        path
        for path in (
            plan_path,
            arcs_path,
            volumes_path,
            ledger_path,
            brief_path,
            simulation_path,
            root / "project.yaml",
        )
        if path.is_file()
    ]
    compiled_story = compile_story_profile(
        config.data["story_profile"], market_ids=set(BUILTIN_MARKET_IDS)
    )
    forecast = compile_length_forecast(config.data["length"])
    start_progress = min(1.0, max(0.0, (start - 1) / max(1, forecast.estimated_chapters)))
    end_progress = min(1.0, max(start_progress, end / max(1, forecast.estimated_chapters)))
    relevant_arcs = [
        item
        for item in arcs if isinstance(item, dict) and valid_progress_window(item.get("progress_window"))
        and float(item["progress_window"][0]) <= end_progress
        and float(item["progress_window"][1]) >= start_progress
    ] if isinstance(arcs, list) else []
    requested_facets = [
        str(facet_id)
        for arc in relevant_arcs
        for facet_id in arc.get("active_facets") or []
    ]
    selected_facets = active_story_facets(compiled_story, requested_facets, limit=3)
    payload = {
        "schema": "outline_extension_context_v1",
        "requested_range": {"from_chapter": start, "to_chapter": end},
        "length_forecast": forecast.to_dict(),
        "story_profile": {
            "market": compiled_story["market"],
            "selected_facets": [
                {
                    "kind": item.get("kind"),
                    "id": item.get("id"),
                    "level": item.get("level"),
                    "requirements": list(item.get("requirements") or [])[:2],
                    "risks": list(item.get("risks") or [])[:2],
                }
                for item in selected_facets
            ],
            "resolutions": compiled_story["resolutions"],
        },
        "book_contract": {
            key: brief.get(key)
            for key in ("design_decisions", "reader_contract", "core_taboo", "story_engine_contract")
            if isinstance(brief, dict) and brief.get(key) not in (None, "", [], {})
        },
        "story_arc_map": [
            {
                key: item.get(key)
                for key in ("id", "number", "progress_window", "target_characters", "goal", "active_facets")
            }
            for item in arcs if isinstance(item, dict)
        ] if isinstance(arcs, list) else [],
        "active_story_arcs": relevant_arcs,
        "volume_map": [
            {
                key: item.get(key)
                for key in ("id", "number", "target_characters", "arc_ids", "goal", "ending_turn")
            }
            for item in volumes if isinstance(item, dict)
        ] if isinstance(volumes, list) else [],
        "recent_chapter_plan": recent_plan,
        "active_foreshadowing": active_threads,
        "arc_causal_simulation": {
            key: value
            for key, value in simulation.items()
            if key not in {"basis_hashes", "approved_by", "status"}
        },
        "provenance": [
            {"path": relative(root, path), "sha256": sha256(path.read_bytes()).hexdigest()}
            for path in source_paths
        ],
        "selection": {
            "full_history_exposed": False,
            "recent_chapter_limit": 8,
            "active_foreshadow_limit": 12,
            "story_facet_limit": 3,
            "active_arc_ids": [str(item.get("id") or "") for item in relevant_arcs],
        },
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    budget = resolve_context_budget_contract(root)
    payload["selection"]["estimated_units"] = estimate_text_units(rendered, budget.estimator)
    payload["selection"]["budget_profile"] = budget.profile
    payload["selection"]["capacity_units"] = budget.capacity_units
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    target = (
        root / "50_workbench" / "intelligence_context" /
        f"outline_extension.ch{start:03d}-ch{end:03d}.context.json"
    )
    atomic_write_text(target, rendered)
    return target


def intelligence_canonical_targets(
    root: Path,
    task_type: str,
    scope: dict[str, Any],
) -> tuple[str, ...]:
    if task_type in DESIGN_INTELLIGENCE_TASK_TYPES:
        targets = (
            relative(root, path)
            for path in design_apply_targets(root, task_type, scope)
        )
        return tuple(path for path in targets if is_canonical_output(path))
    if task_type == "chapter_direction":
        chapter_number = int(scope["chapter_number"])
        return (
            f"20_outline/chapter_cards/ch{chapter_number:03d}.json",
            f"20_outline/chapter_cards/ch{chapter_number:03d}.md",
            "20_outline/chapter_plan.json",
        )
    return tuple(str(item) for item in TASK_SPECS[task_type]["targets"])


def book_ideation_readiness_errors(root: Path) -> list[str]:
    payload = read_json(root / "10_bible" / "creative_decisions.json", {})
    if not isinstance(payload, dict) or payload.get("schema") != "book_ideation_decisions_v1":
        return ["book_ideation has not recorded any human-approved creative decisions."]
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict):
        return ["book_ideation decisions must be an object."]
    missing = [dimension for dimension in BOOK_IDEATION_DIMENSIONS if not str(decisions.get(dimension) or "").strip()]
    if missing:
        return [f"book_ideation is incomplete; next dimension: {missing[0]}."]
    if payload.get("complete") is not True:
        return ["book_ideation decisions are present but not marked complete."]
    return []


def next_book_ideation_round(root: Path) -> int:
    payload = read_json(root / "10_bible" / "creative_decisions.json", {})
    rounds = payload.get("rounds") if isinstance(payload, dict) else []
    return len(rounds) + 1 if isinstance(rounds, list) else 1


def next_book_ideation_dimension(root: Path) -> str:
    payload = read_json(root / "10_bible" / "creative_decisions.json", {})
    decisions = payload.get("decisions") if isinstance(payload, dict) else {}
    decisions = decisions if isinstance(decisions, dict) else {}
    for dimension in BOOK_IDEATION_DIMENSIONS:
        if not str(decisions.get(dimension) or "").strip():
            return dimension
    return "complete"


def assess_chapter_direction(config: ConfigDocument, chapter_number: int) -> dict[str, Any]:
    """Return deterministic reasons for requiring a human chapter-direction choice."""

    root = resolve_project_root(config)
    deadlines = promise_deadline_status(root, chapter_number=chapter_number)
    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    card = read_json(card_path, {})
    selected = card.get("direction_selection") if isinstance(card, dict) else None
    if isinstance(selected, dict) and selected.get("status") == "outline_revision_required":
        return {
            "required": True,
            "reasons": ["outline_revision_required"],
            "warnings": deadlines["warnings"],
            "status": "outline_revision_required",
        }
    try:
        load_active_arc_simulation(root, chapter_number=chapter_number)
    except ArcSimulationError as exc:
        return {
            "required": True,
            "reasons": [str(exc)],
            "warnings": deadlines["warnings"],
            "status": "arc_simulation_required",
        }
    if isinstance(card, dict):
        if isinstance(selected, dict) and selected.get("status") == "applied":
            return {
                "required": False,
                "reasons": [],
                "warnings": [*deadlines["warnings"], *deadlines["blockers"]],
                "status": "applied",
            }
    if deadlines["blockers"]:
        return {
            "required": True,
            "reasons": deadlines["blockers"],
            "warnings": deadlines["warnings"],
            "status": "outline_revision_required",
        }
    plan = read_json(root / "20_outline" / "chapter_plan.json", [])
    planned = next(
        (
            item
            for item in plan
            if isinstance(item, dict) and int(item.get("chapter_number") or 0) == chapter_number
        ),
        {},
    ) if isinstance(plan, list) else {}
    reasons: list[str] = ["mandatory_chapter_direction", *deadlines["warnings"]]
    text = " ".join(
        str(planned.get(key) or "")
        for key in ("title", "chapter_duty", "conflict", "chapter_turn", "hook")
    ).lower()
    abstract_markers = (
        "待定",
        "推进主线",
        "推进剧情",
        "advance the active investigation",
        "advance one bounded",
        "open the next contradiction",
    )
    if any(marker in text for marker in abstract_markers):
        reasons.append("abstract_outline_target")
    if any(bool(planned.get(key)) for key in ("major_turn", "major_reveal", "relationship_turn")):
        reasons.append("major_turn")
    if isinstance(planned.get("plotline_options"), list) and len(planned["plotline_options"]) >= 2:
        reasons.append("multiple_valid_plotlines")
    if planned.get("multiple_valid_plotlines") is True:
        reasons.append("multiple_valid_plotlines")
    repair_count = sum(
        1
        for task in list_manifests(root, chapter_number=chapter_number)
        if task.get("task_type") == "repair" and task.get("status") in {"invalid", "applied"}
    )
    if repair_count >= 2:
        reasons.append("repeated_repairs")
    deduped = list(dict.fromkeys(reasons))
    return {
        "required": bool(deduped),
        "reasons": deduped,
        "warnings": deadlines["warnings"],
        "status": "required" if deduped else "not_required",
    }


def normalize_inputs(root: Path, inputs: Iterable[str | Path]) -> list[Path]:
    result: list[Path] = []
    for item in inputs:
        candidate = Path(item)
        if not candidate.is_absolute():
            candidate = root / candidate
        candidate = candidate.expanduser().resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"Input must live under project root: {item}") from exc
        if not candidate.is_file():
            raise ValueError(f"Input file does not exist: {item}")
        if candidate not in result:
            result.append(candidate)
    return result


def resolve_candidate(root: Path, file_path: str | Path) -> Path:
    candidate = Path(file_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.expanduser().resolve()
    try:
        relative_path = candidate.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"Candidate must live under project root: {file_path}") from exc
    if not relative_path.startswith("50_workbench/intelligence_candidates/"):
        raise ValueError("Candidate must live under 50_workbench/intelligence_candidates/.")
    return candidate


def manifest_for_output(root: Path, task_type: str, candidate: Path) -> dict[str, Any] | None:
    output = relative(root, candidate)
    active = {"awaiting_agent", "submitted", "validated", "approved", "invalid"}
    for entry in reversed(list_manifests(root)):
        if (
            entry.get("task_type") == task_type
            and entry.get("status") in active
            and output == manifest_output(entry).get("path")
        ):
            manifest_path = root / str(entry.get("manifest_file") or "")
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
            if not isinstance(payload, dict):
                return None
            from longform_engine.agent_tasks import normalize_manifest

            return normalize_manifest(payload)
    return None


def load_candidate(
    config: ConfigDocument,
    root: Path,
    path: Path,
    errors: list[str],
    *,
    task_type: str,
    spec: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except FileNotFoundError:
        errors.append(f"candidate file does not exist: {path}")
        return None
    protocol = output_protocol_for_task(task_type)
    try:
        if protocol == DESIGN_DOCUMENT_SCHEMA:
            raise AgentProtocolError(
                "design_document_v1 is authoritative Markdown and must use approve -> compile-task"
            )
        else:
            payload = json.loads(text)
            if protocol == CANONICAL_DELTA_SCHEMA:
                raw_delta = payload
                payload = canonical_delta_domain_payload(
                    raw_delta,
                    task_type=task_type,
                    domain_schema=str(spec["schema"]),
                )
                payload = hydrate_canonical_delta_domain_payload(
                    config,
                    root,
                    task_type=task_type,
                    delta=raw_delta,
                    domain_payload=payload,
                    manifest=manifest,
                )
            elif protocol == EVIDENCE_REVIEW_SCHEMA:
                review_errors = validate_evidence_review(payload)
                if review_errors:
                    raise AgentProtocolError("; ".join(review_errors))
    except (json.JSONDecodeError, AgentProtocolError) as exc:
        errors.append(f"candidate does not satisfy {protocol}: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append("candidate must normalize to an object.")
        return None
    return payload


def hydrate_canonical_delta_domain_payload(
    config: ConfigDocument,
    root: Path,
    *,
    task_type: str,
    delta: dict[str, Any],
    domain_payload: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Derive CLI-owned evidence fields without expanding the Agent protocol."""

    if task_type == "fanfiction_canon":
        return hydrate_fanfiction_canon_delta(config, root, delta, domain_payload, manifest)
    if task_type == "research_synthesis":
        return hydrate_research_delta(root, delta, domain_payload, manifest)
    return domain_payload


def hydrate_fanfiction_canon_delta(
    config: ConfigDocument,
    root: Path,
    delta: dict[str, Any],
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    if any(field in payload for field in ("continuity_mode",)):
        raise AgentProtocolError("fanfiction canon changes must not repeat CLI-known continuity_mode")
    configured = config.data.get("fanfiction")
    configured = configured if isinstance(configured, dict) else {}
    configured_sources = {
        str(item.get("source_id")): item
        for item in configured.get("sources") or []
        if isinstance(item, dict) and item.get("source_id")
    }
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        raise AgentProtocolError("fanfiction canon changes.sources must be a non-empty list")
    evidence_map = delta.get("evidence") if isinstance(delta.get("evidence"), dict) else {}
    hydrated_sources: list[dict[str, Any]] = []
    fact_collections = (
        "characters",
        "relationships",
        "world_rules",
        "abilities",
        "timeline",
        "terminology",
        "canon_events",
        "unresolved_questions",
    )
    for source_index, raw_source in enumerate(sources):
        if not isinstance(raw_source, dict):
            raise AgentProtocolError(f"fanfiction canon sources[{source_index}] must be an object")
        forbidden = {"title", "creator", "canon_cutoff", "source_files", "source_hashes", "evidence"}
        repeated = sorted(forbidden & set(raw_source))
        if repeated:
            raise AgentProtocolError(
                "fanfiction canon changes must not repeat CLI-owned fields: " + ", ".join(repeated)
            )
        source_id = str(raw_source.get("source_id") or "")
        source_config = configured_sources.get(source_id)
        if not isinstance(source_config, dict):
            raise AgentProtocolError(f"fanfiction canon source_id is not configured: {source_id}")
        source_pointer = f"/changes/sources/{source_index}"
        raw_refs = sorted(
            {
                ref
                for pointer, refs in evidence_map.items()
                if pointer == source_pointer or pointer.startswith(source_pointer + "/")
                for ref in refs
            }
        )
        if not raw_refs:
            raise AgentProtocolError(f"fanfiction canon sources[{source_index}] has no evidence")
        resolved = [resolve_delta_evidence(root, ref, manifest) for ref in raw_refs]
        evidence_ids = {
            item["ref"]: f"{source_id}:e{index:03d}"
            for index, item in enumerate(resolved, start=1)
        }
        hydrated = dict(raw_source)
        hydrated.update(
            {
                "title": str(source_config.get("title") or ""),
                "creator": str(source_config.get("creator") or ""),
                "canon_cutoff": str(source_config.get("canon_cutoff") or ""),
                "source_files": sorted({item["path"] for item in resolved}),
                "source_hashes": {
                    path: next(item["sha256"] for item in resolved if item["path"] == path)
                    for path in sorted({item["path"] for item in resolved})
                },
                "evidence": [
                    {
                        "evidence_id": evidence_ids[item["ref"]],
                        "source_path": item["path"],
                        "source_hash": item["sha256"],
                        "evidence_span": {"start": item["start"], "end": item["end"]},
                    }
                    for item in resolved
                ],
            }
        )
        for collection in fact_collections:
            records = hydrated.get(collection)
            if not isinstance(records, list):
                continue
            for record_index, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                if "evidence_refs" in record:
                    raise AgentProtocolError(
                        f"fanfiction canon {collection}[{record_index}] repeats evidence_refs"
                    )
                pointer = f"{source_pointer}/{collection}/{record_index}"
                refs = delta_refs_for_pointer(evidence_map, pointer)
                if not refs:
                    raise AgentProtocolError(f"fanfiction canon `{pointer}` has no evidence mapping")
                record["evidence_refs"] = [evidence_ids[ref] for ref in refs]
        hydrated_sources.append(hydrated)
    return {
        **payload,
        "continuity_mode": str(configured.get("continuity_mode") or ""),
        "sources": hydrated_sources,
    }


def hydrate_research_delta(
    root: Path,
    delta: dict[str, Any],
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    repeated = sorted({"source_files", "source_hashes"} & set(payload))
    if repeated:
        raise AgentProtocolError(
            "research changes must not repeat CLI-owned fields: " + ", ".join(repeated)
        )
    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise AgentProtocolError("research changes.claims must be a list")
    evidence_map = delta.get("evidence") if isinstance(delta.get("evidence"), dict) else {}
    hydrated_claims: list[dict[str, Any]] = []
    resolved_all: list[dict[str, Any]] = []
    for index, raw_claim in enumerate(claims):
        if not isinstance(raw_claim, dict):
            raise AgentProtocolError(f"research changes.claims[{index}] must be an object")
        forbidden = {"source_path", "source_hash", "evidence_span", "evidence"}
        repeated = sorted(forbidden & set(raw_claim))
        if repeated:
            raise AgentProtocolError(
                f"research changes.claims[{index}] repeats CLI-owned evidence fields: "
                + ", ".join(repeated)
            )
        pointer = f"/changes/claims/{index}"
        refs = delta_refs_for_pointer(evidence_map, pointer)
        if len(refs) != 1:
            raise AgentProtocolError(f"research `{pointer}` must map to exactly one evidence span")
        resolved = resolve_delta_evidence(root, refs[0], manifest)
        resolved_all.append(resolved)
        hydrated_claims.append(
            {
                **raw_claim,
                "source_path": resolved["path"],
                "source_hash": resolved["sha256"],
                "evidence_span": {"start": resolved["start"], "end": resolved["end"]},
                "evidence": resolved["text"],
            }
        )
    paths = sorted({item["path"] for item in resolved_all})
    return {
        **payload,
        "source_files": paths,
        "source_hashes": {
            path: next(item["sha256"] for item in resolved_all if item["path"] == path)
            for path in paths
        },
        "claims": hydrated_claims,
    }


def delta_refs_for_pointer(evidence_map: dict[str, Any], pointer: str) -> list[str]:
    return sorted(
        {
            str(ref)
            for evidence_pointer, refs in evidence_map.items()
            if evidence_pointer == pointer or evidence_pointer.startswith(pointer + "/")
            for ref in refs
        }
    )


def resolve_delta_evidence(
    root: Path,
    reference: str,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    match = re.fullmatch(r"(.+)@(\d+):(\d+)", str(reference))
    if not match:
        raise AgentProtocolError(f"invalid evidence ID `{reference}`; expected project/path@start:end")
    relative_source = match.group(1).replace("\\", "/")
    declared = set(manifest_input_paths(manifest or {}))
    if relative_source not in declared:
        raise AgentProtocolError(f"evidence source is not declared by the manifest: {relative_source}")
    source = (root / relative_source).resolve()
    try:
        source.relative_to(root.resolve())
    except ValueError as exc:
        raise AgentProtocolError(f"evidence source escapes project root: {relative_source}") from exc
    if not source.is_file():
        raise AgentProtocolError(f"evidence source does not exist: {relative_source}")
    text = source.read_text(encoding="utf-8").lstrip("\ufeff")
    start, end = int(match.group(2)), int(match.group(3))
    if start < 0 or end <= start or end > len(text):
        raise AgentProtocolError(f"evidence span is outside source content: {reference}")
    return {
        "ref": str(reference),
        "path": relative_source,
        "sha256": sha256(source.read_bytes()).hexdigest(),
        "start": start,
        "end": end,
        "text": text[start:end],
    }


def validate_payload(
    config: ConfigDocument,
    root: Path,
    task_type: str,
    spec: dict[str, Any],
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    protocol = output_protocol_for_task(task_type)
    if protocol != EVIDENCE_REVIEW_SCHEMA and payload.get("schema") != spec["schema"]:
        errors.append(f"schema must normalize to {spec['schema']}.")
    validators = {
        "book_ideation": lambda value, target: validate_book_ideation(root, value, target),
        "fanfiction_canon": lambda value, target: validate_fanfiction_canon(config, value, target),
        "fanfiction_design": lambda value, target: validate_fanfiction_design(config, root, value, target),
        "book_design": lambda value, target: validate_book_design(value, target),
        "character_expression_design": lambda value, target: target.extend(
            validate_character_expression_profile(
                value,
                character_ids=character_ids_from_root(root),
            )
        ),
        "character_expression_review": lambda value, target: target.extend(
            validate_evidence_review(value)
        ),
        "outline_design": lambda value, target: validate_outline_design(config, value, target),
        "arc_simulation": lambda value, target: validate_arc_simulation_payload(
            root, value, manifest, target
        ),
        "outline_extension": lambda value, target: validate_outline_extension(
            config, root, value, manifest, target
        ),
        "chapter_direction": lambda value, target: validate_chapter_direction(config, root, value, manifest, target),
        "outline_revision": lambda value, target: validate_outline_revision(config, root, value, target),
        "research_synthesis": validate_research_synthesis,
        "style_analysis": validate_style_analysis,
        "adaptation_analysis": validate_adaptation_analysis,
    }
    validators[task_type](payload, errors)
    if task_type in {"fanfiction_canon", "research_synthesis", "style_analysis", "adaptation_analysis"}:
        validate_sources(root, payload, manifest, errors, require_hashes=True)


def require_keys(payload: dict[str, Any], required: set[str], allowed: set[str], errors: list[str]) -> None:
    missing = sorted(required - set(payload))
    extra = sorted(set(payload) - allowed)
    if missing:
        errors.append("missing fields: " + ", ".join(missing))
    if extra:
        errors.append("unknown fields: " + ", ".join(extra))


def validate_arc_simulation_payload(
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    scope = (manifest or {}).get("scope") if isinstance((manifest or {}).get("scope"), dict) else {}
    task_range = (
        int(scope.get("from_chapter") or 0),
        int(scope.get("to_chapter") or 0),
    )
    permitted_ranges = permitted_arc_simulation_ranges(root)
    if not permitted_ranges:
        errors.append("arc simulation requires a materialized rolling planning_window.")
    elif task_range not in permitted_ranges:
        errors.append(
            "arc simulation task range must match the current rolling window or its immediately adjacent next window."
        )
    errors.extend(
        validate_arc_causal_simulation(
            payload,
            expected_range=task_range,
            expected_basis=current_basis_hashes(root),
        )
    )


def require_nonempty_string(payload: dict[str, Any], key: str, errors: list[str]) -> None:
    if not isinstance(payload.get(key), str) or not str(payload.get(key)).strip():
        errors.append(f"{key} must be a non-empty string.")


def require_list(payload: dict[str, Any], key: str, errors: list[str]) -> None:
    if not isinstance(payload.get(key), list):
        errors.append(f"{key} must be a list.")


def validate_book_ideation(root: Path, payload: dict[str, Any], errors: list[str]) -> None:
    required = {"schema", "round", "dimension", "question", "options", "selection"}
    require_keys(payload, required, required, errors)
    expected_round = next_book_ideation_round(root)
    expected_dimension = next_book_ideation_dimension(root)
    if payload.get("round") != expected_round:
        errors.append(f"round must be the current unapplied round: {expected_round}.")
    if payload.get("dimension") != expected_dimension:
        errors.append(f"dimension must be the next undecided dimension: {expected_dimension}.")
    require_nonempty_string(payload, "question", errors)
    options = payload.get("options")
    option_ids: set[str] = set()
    if not isinstance(options, list) or not 2 <= len(options) <= 3:
        errors.append("options must contain two or three choices.")
    else:
        for index, option in enumerate(options):
            if not isinstance(option, dict) or set(option) != {"id", "proposal", "tradeoffs"}:
                errors.append(f"options[{index}] must contain id, proposal, and tradeoffs only.")
                continue
            option_id = str(option.get("id") or "")
            if not stable_id(option_id) or option_id in option_ids:
                errors.append(f"options[{index}].id must be stable and unique.")
            option_ids.add(option_id)
            if not isinstance(option.get("proposal"), str) or not option["proposal"].strip():
                errors.append(f"options[{index}].proposal must be a non-empty string.")
            tradeoffs = option.get("tradeoffs")
            if (
                not isinstance(tradeoffs, list)
                or not tradeoffs
                or any(not isinstance(item, str) or not item.strip() for item in tradeoffs)
            ):
                errors.append(f"options[{index}].tradeoffs must be a non-empty string list.")
    selection = payload.get("selection")
    if not isinstance(selection, dict) or set(selection) != {"mode", "option_id", "answer"}:
        errors.append("selection must contain mode, option_id, and answer only.")
        return
    mode = selection.get("mode")
    if mode not in {"selected_option", "provided_answer"}:
        errors.append("selection.mode must be selected_option or provided_answer.")
    if mode == "selected_option":
        if selection.get("option_id") not in option_ids:
            errors.append("selection.option_id must reference one declared option.")
        if str(selection.get("answer") or "").strip():
            errors.append("selection.answer must be empty when mode=selected_option.")
    if mode == "provided_answer":
        if str(selection.get("option_id") or "").strip():
            errors.append("selection.option_id must be empty when mode=provided_answer.")
        if not isinstance(selection.get("answer"), str) or not selection["answer"].strip():
            errors.append("selection.answer must be non-empty when mode=provided_answer.")


def validate_chapter_direction(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    required = {
        "schema",
        "chapter_number",
        "chapter_card_sha256",
        "trigger_reasons",
        "selected_direction",
        "selection",
        "canonical_refs",
        "introduced_elements",
    }
    require_keys(payload, required, required, errors)
    chapter_number = payload.get("chapter_number")
    manifest_chapter = manifest_chapter_number(manifest or {})
    if not isinstance(chapter_number, int) or chapter_number <= 0:
        errors.append("chapter_number must be a positive integer.")
        return
    if chapter_number != manifest_chapter:
        errors.append("chapter_number must match the active Agent task.")
    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    if not card_path.is_file():
        errors.append("declared chapter card does not exist.")
        return
    expected_hash = sha256(card_path.read_bytes()).hexdigest()
    if payload.get("chapter_card_sha256") != expected_hash:
        errors.append("chapter_card_sha256 does not match the current chapter card.")
    status = assess_chapter_direction(config, chapter_number)
    reasons = payload.get("trigger_reasons")
    if not isinstance(reasons, list) or sorted(str(item) for item in reasons) != sorted(status["reasons"]):
        errors.append("trigger_reasons must match CLI-computed chapter direction reasons.")

    direction = payload.get("selected_direction")
    required_direction = {
        "id",
        "title",
        "book_goal",
        "volume_goal",
        "protagonist_goal",
        "chapter_duty",
        "immediate_desire",
        "opposition_force",
        "dramatic_question",
        "key_failure",
        "irreversible_choice",
        "chapter_turn",
        "reveal_boundary",
        "must_dramatize",
        "may_summarize",
        "primary_story_engine",
        "scene_carriers",
        "reader_promise_actions",
        "arc_simulation_ref",
        "protected_story_outcomes",
        "prohibited_drift",
        "state_change_kind",
        "dramatic_method",
        "exposition_carrier",
        "scene_chain",
        "featured_character_ids",
        "cast_desires",
        "dialogue_ownership",
        "embodiment_plan",
        "interiority_function",
        "conflict",
        "reader_gain",
        "cost",
        "mainline_move",
        "character_arc_move",
        "foreshadow_move",
        "relationship_move",
        "canon_refs",
        "world_rule_refs",
        "foreshadow_refs",
        "forbidden_reveals",
        "ending_mode",
        "main_risks",
    }
    fanfiction_mode = str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
    fanfiction_fields = {
        "protected_canon_outcomes",
        "changed_scene_means",
        "canon_character_agency",
        "new_long_term_facts",
        "outline_revision_required",
    }
    if fanfiction_mode:
        required_direction |= fanfiction_fields
    if not isinstance(direction, dict) or set(direction) != required_direction:
        errors.append(
            "selected_direction must contain exactly: "
            + ", ".join(sorted(required_direction))
            + "."
        )
        direction = {}
    direction_id = stable_id(direction.get("id"))
    if not direction_id:
        errors.append("selected_direction.id must be stable.")
    for field in (
        "title", "book_goal", "volume_goal", "protagonist_goal", "chapter_duty",
        "dialogue_ownership", "embodiment_plan", "interiority_function", "conflict",
        "immediate_desire", "opposition_force", "dramatic_question", "key_failure",
        "irreversible_choice", "chapter_turn", "reveal_boundary", "reader_gain", "cost",
        "primary_story_engine", "state_change_kind", "dramatic_method", "exposition_carrier",
        "mainline_move",
        "character_arc_move", "foreshadow_move", "relationship_move", "ending_mode",
    ):
        if not isinstance(direction.get(field), str) or not direction[field].strip():
            errors.append(f"selected_direction.{field} must be non-empty text.")
    for field in (
        "featured_character_ids", "canon_refs", "world_rule_refs", "foreshadow_refs",
        "forbidden_reveals", "main_risks", "must_dramatize", "may_summarize",
        "scene_carriers", "protected_story_outcomes", "prohibited_drift",
    ):
        values = direction.get(field)
        if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
            errors.append(f"selected_direction.{field} must be a string list.")
    for field in (
        "featured_character_ids", "must_dramatize", "scene_carriers",
        "protected_story_outcomes", "prohibited_drift",
    ):
        if not direction.get(field):
            errors.append(f"selected_direction.{field} must be non-empty.")
    ledger = load_reader_promise_ledger(root)
    errors.extend(
        f"selected_direction.{item}"
        for item in validate_promise_actions(direction.get("reader_promise_actions"), ledger)
    )
    try:
        simulation, simulation_path, simulation_hash = load_active_arc_simulation(
            root, chapter_number=chapter_number
        )
    except ArcSimulationError as exc:
        errors.append(str(exc))
    else:
        expected_simulation_ref = {
            "path": relative(root, simulation_path),
            "sha256": simulation_hash,
            "from_chapter": simulation["from_chapter"],
            "to_chapter": simulation["to_chapter"],
        }
        if direction.get("arc_simulation_ref") != expected_simulation_ref:
            errors.append(
                "selected_direction.arc_simulation_ref must reference the current approved causal simulation."
            )
    current_card = read_json(card_path, {})
    protected_scalar_fields = (
        "chapter_duty", "chapter_turn", "reader_gain", "relationship_move", "state_change_kind",
    )
    protected_list_fields = ("featured_character_ids", "protected_story_outcomes")
    authority_changes = [
        field
        for field in protected_scalar_fields
        if isinstance(current_card, dict)
        and current_card.get(field) not in (None, "")
        and direction.get(field) != current_card.get(field)
    ]
    authority_changes.extend(
        field
        for field in protected_list_fields
        if isinstance(current_card, dict)
        and isinstance(current_card.get(field), list)
        and current_card.get(field)
        and direction.get(field) != current_card.get(field)
    )
    if authority_changes:
        errors.append(
            "selected direction changes protected chapter outcomes outside chapter-direction authority "
            f"({', '.join(authority_changes)}); create an outline_revision task."
        )
    scenes = direction.get("scene_chain")
    scene_fields = {
        "scene_id", "location", "participants", "carrier", "desire_collision", "action",
        "reaction", "choice", "cost", "turn", "exit_state",
    }
    if not isinstance(scenes, list) or not 2 <= len(scenes) <= 5:
        errors.append("selected_direction.scene_chain must contain two to five scenes.")
    else:
        for scene_index, scene in enumerate(scenes):
            if not isinstance(scene, dict) or set(scene) != scene_fields:
                errors.append(
                    f"selected_direction.scene_chain[{scene_index}] must contain exactly: "
                    f"{', '.join(sorted(scene_fields))}."
                )
                continue
            for key in scene_fields - {"participants"}:
                if not isinstance(scene.get(key), str) or not scene[key].strip():
                    errors.append(f"selected_direction.scene_chain[{scene_index}].{key} must be non-empty.")
            if not isinstance(scene.get("participants"), list) or not scene["participants"]:
                errors.append(f"selected_direction.scene_chain[{scene_index}].participants must be non-empty.")
    cast_desires = direction.get("cast_desires")
    if not isinstance(cast_desires, dict) or not cast_desires:
        errors.append("selected_direction.cast_desires must be a non-empty character-id object.")
    elif any(
        not stable_id(key) or not isinstance(value, str) or not value.strip()
        for key, value in cast_desires.items()
    ):
        errors.append("selected_direction.cast_desires must map stable character IDs to visible desires.")
    if fanfiction_mode:
        for field in ("changed_scene_means", "canon_character_agency"):
            if not isinstance(direction.get(field), str) or not direction[field].strip():
                errors.append(f"selected_direction.{field} must be non-empty text.")
        for field in ("protected_canon_outcomes", "new_long_term_facts"):
            values = direction.get(field)
            if not isinstance(values, list) or any(not isinstance(item, str) or not item.strip() for item in values):
                errors.append(f"selected_direction.{field} must be a string list.")
        if not isinstance(direction.get("outline_revision_required"), bool):
            errors.append("selected_direction.outline_revision_required must be boolean.")
        protected_canon_outcomes = direction.get("protected_canon_outcomes")
        card_protected_canon = (
            current_card.get("protected_canon_outcomes")
            if isinstance(current_card, dict)
            else None
        )
        if not isinstance(card_protected_canon, list) or not card_protected_canon:
            errors.append(
                "chapter card lacks protected_canon_outcomes; create an outline_revision task before selecting a fanfiction direction."
            )
        elif protected_canon_outcomes != card_protected_canon:
            errors.append(
                "selected fanfiction direction changes protected canon outcomes; create an outline_revision task."
            )
        changed_outcomes = (
            bool(direction.get("new_long_term_facts"))
            or not bool(protected_canon_outcomes)
            or (
                isinstance(card_protected_canon, list)
                and bool(card_protected_canon)
                and protected_canon_outcomes != card_protected_canon
            )
        )
        if changed_outcomes and direction.get("outline_revision_required") is not True:
            errors.append(
                "fanfiction direction changing protected outcomes or long-term facts must require outline revision."
            )
        if direction.get("outline_revision_required") is True:
            errors.append(
                "selected fanfiction direction is outside chapter-direction authority; create an outline_revision task."
            )
    selection = payload.get("selection")
    selection_fields = {"direction_id", "user_adjustments", "repetition_reason"}
    if not isinstance(selection, dict) or set(selection) != selection_fields:
        errors.append("selection must contain direction_id, user_adjustments, and repetition_reason only.")
        return
    if selection.get("direction_id") != direction_id:
        errors.append("selection.direction_id must reference selected_direction.id.")
    adjustments = selection.get("user_adjustments")
    allowed_adjustments = required_direction - {"id", "title", "main_risks"}
    if not isinstance(adjustments, dict) or set(adjustments) - allowed_adjustments:
        errors.append("selection.user_adjustments contains unsupported fields.")
    elif any(value in (None, "", [], {}) for value in adjustments.values()):
        errors.append("selection.user_adjustments values must be non-empty.")
    repetition = chapter_carrier_repetition_status(root, direction, chapter_number=chapter_number)
    if not isinstance(selection.get("repetition_reason"), str):
        errors.append("selection.repetition_reason must be text.")
    repetition_reason = str(selection.get("repetition_reason") or "").strip()
    if repetition["requires_reason"] and not repetition_reason:
        errors.append(
            "selection.repetition_reason is required because the primary carrier and dramatic method repeat in four of five chapters."
        )
    if not isinstance(payload.get("canonical_refs"), list):
        errors.append("canonical_refs must be a list.")
    elif sorted(payload["canonical_refs"]) != sorted(direction.get("canon_refs") or []):
        errors.append("canonical_refs must match selected_direction.canon_refs.")
    if not isinstance(payload.get("introduced_elements"), list):
        errors.append("introduced_elements must be a list.")


def chapter_carrier_repetition_status(
    root: Path,
    direction: dict[str, Any],
    *,
    chapter_number: int | None = None,
) -> dict[str, Any]:
    """Describe five-chapter carrier repetition without turning genre repetition into a quota."""

    path = root / "30_state" / "quality" / "structure_history.jsonl"
    history = [
        item
        for item in read_jsonl_records(path)
        if chapter_number is None or int(item.get("chapter_number") or 0) < chapter_number
    ]
    recent = sorted(history, key=lambda item: int(item.get("chapter_number") or 0))[-4:]
    carriers = [
        str(item.get("primary_scene_carrier") or item.get("dominant_scene_type") or "")
        for item in recent
    ]
    methods = [str(item.get("dramatic_method") or "") for item in recent]
    states = [str(item.get("state_change_kind") or "") for item in recent]
    selected_carriers = direction.get("scene_carriers")
    primary = str(selected_carriers[0] if isinstance(selected_carriers, list) and selected_carriers else "")
    method = str(direction.get("dramatic_method") or "")
    state = str(direction.get("state_change_kind") or "")
    five_carriers = [*carriers, primary]
    carrier_count = sum(item == primary for item in five_carriers if primary)
    method_count = sum(item == method for item in [*methods, method] if method)
    state_count = sum(item == state for item in [*states, state] if state)
    return {
        "schema": "carrier_repetition_diagnostic_v1",
        "window": len(five_carriers),
        "primary_scene_carrier": primary,
        "carrier_count": carrier_count,
        "warning": carrier_count >= 3,
        "requires_reason": carrier_count >= 4 and (method_count >= 4 or state_count >= 4),
        "recent_carriers": carriers,
    }


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    if not path.is_file():
        return records
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


def validate_book_design(payload: dict[str, Any], errors: list[str]) -> None:
    required = {"schema", "creative_brief", "world_markdown", "power_system_markdown", "characters", "relationships"}
    expression_fields = {"narrative_expression_profile", "character_expression_contracts"}
    required |= expression_fields
    allowed = required | expression_fields | {"factions", "locations"}
    require_keys(payload, required, allowed, errors)
    brief = payload.get("creative_brief")
    if not isinstance(brief, dict):
        errors.append("creative_brief must be an object.")
    else:
        for field in ("target_audience", "writing_style", "automation_level", "target_scale"):
            if not isinstance(brief.get(field), str) or not brief[field].strip():
                errors.append(f"creative_brief.{field} must be a non-empty string.")
        if not isinstance(brief.get("story_profile"), dict) or not brief.get("story_profile"):
            errors.append("creative_brief.story_profile must be a non-empty object.")
        story_engine = brief.get("story_engine_contract")
        story_engine_fields = {
            "schema",
            "reader_fantasy",
            "repeatable_action_loop",
            "progression_loop",
            "relationship_loop",
            "mystery_or_question_loop",
            "expected_payoffs",
            "carrier_palette",
            "theme_carrier_limits",
        }
        if not isinstance(story_engine, dict) or set(story_engine) != story_engine_fields:
            errors.append(
                "creative_brief.story_engine_contract must contain the story_engine_contract_v1 fields only."
            )
        else:
            if story_engine.get("schema") != "story_engine_contract_v1":
                errors.append("creative_brief.story_engine_contract.schema must be story_engine_contract_v1.")
            for field in (
                "reader_fantasy", "repeatable_action_loop", "progression_loop",
                "relationship_loop", "mystery_or_question_loop", "theme_carrier_limits",
            ):
                if not isinstance(story_engine.get(field), str) or not story_engine[field].strip():
                    errors.append(f"creative_brief.story_engine_contract.{field} must be non-empty text.")
            payoffs = story_engine.get("expected_payoffs")
            payoff_fields = {"opening_three", "early_serial", "volume_end"}
            if not isinstance(payoffs, dict) or set(payoffs) != payoff_fields or any(
                not isinstance(payoffs.get(field), str) or not payoffs[field].strip()
                for field in payoff_fields
            ):
                errors.append(
                    "creative_brief.story_engine_contract.expected_payoffs must define opening_three, early_serial, and volume_end."
                )
            palette = story_engine.get("carrier_palette")
            if not isinstance(palette, list) or len(palette) < 3 or any(
                not isinstance(item, str) or not item.strip() for item in palette
            ):
                errors.append("creative_brief.story_engine_contract.carrier_palette must contain at least three carriers.")
        decisions = brief.get("design_decisions")
        decision_fields = {
            "core_hook",
            "world_rule",
            "protagonist_desire",
            "long_conflict",
            "volume_escalation",
            "ending_boundary",
        }
        if not isinstance(decisions, dict) or set(decisions) != decision_fields:
            errors.append("creative_brief.design_decisions must contain the six opening decisions only.")
        else:
            for field in sorted(decision_fields):
                if not isinstance(decisions.get(field), str) or not decisions[field].strip():
                    errors.append(f"creative_brief.design_decisions.{field} must be a non-empty string.")
        if not isinstance(brief.get("reader_contract"), dict):
            errors.append("creative_brief.reader_contract must be an object.")
        if not isinstance(brief.get("core_taboo"), list) or not brief.get("core_taboo"):
            errors.append("creative_brief.core_taboo must be a non-empty list.")
    for key in ("world_markdown", "power_system_markdown"):
        require_nonempty_string(payload, key, errors)
    for key in ("characters", "relationships"):
        require_list(payload, key, errors)
    for key in ("factions", "locations"):
        if key in payload:
            require_list(payload, key, errors)

    characters = payload.get("characters")
    character_ids: set[str] = set()
    if not isinstance(characters, list) or not characters:
        errors.append("characters must contain at least one designed character.")
    else:
        for index, character in enumerate(characters):
            if not isinstance(character, dict):
                errors.append(f"characters[{index}] must be an object.")
                continue
            required_character = {"id", "name", "goal", "flaw", "arc_stages"}
            missing = required_character - set(character)
            if missing:
                errors.append(f"characters[{index}] missing fields: {', '.join(sorted(missing))}.")
                continue
            character_id = stable_id(character.get("id"))
            if not character_id:
                errors.append(f"characters[{index}].id must be a stable id.")
            elif character_id in character_ids:
                errors.append(f"characters[{index}].id is duplicated: {character_id}.")
            else:
                character_ids.add(character_id)
            for field in ("name", "goal", "flaw"):
                if not isinstance(character.get(field), str) or not character[field].strip():
                    errors.append(f"characters[{index}].{field} must be a non-empty string.")
            stages = character.get("arc_stages")
            if not isinstance(stages, list) or len(stages) < 3 or any(not isinstance(item, str) or not item.strip() for item in stages):
                errors.append(f"characters[{index}].arc_stages must contain at least three non-empty stages.")

    relationships = payload.get("relationships")
    relationship_ids: set[str] = set()
    if not isinstance(relationships, list) or not relationships:
        errors.append("relationships must contain at least one relationship arc.")
    else:
        for index, relation in enumerate(relationships):
            if not isinstance(relation, dict):
                errors.append(f"relationships[{index}] must be an object.")
                continue
            required_relation = {"id", "source_id", "target_id", "type", "stage"}
            missing = required_relation - set(relation)
            if missing:
                errors.append(f"relationships[{index}] missing fields: {', '.join(sorted(missing))}.")
                continue
            relation_id = stable_id(relation.get("id"))
            if not relation_id or relation_id in relationship_ids:
                errors.append(f"relationships[{index}].id must be stable and unique.")
            else:
                relationship_ids.add(relation_id)
            for endpoint in ("source_id", "target_id"):
                if str(relation.get(endpoint) or "") not in character_ids:
                    errors.append(f"relationships[{index}].{endpoint} must reference a declared character id.")
            for field in ("type", "stage"):
                if not isinstance(relation.get(field), str) or not relation[field].strip():
                    errors.append(f"relationships[{index}].{field} must be a non-empty string.")

    for key in ("factions", "locations"):
        records = payload.get(key)
        if not isinstance(records, list):
            continue
        seen: set[str] = set()
        for index, record in enumerate(records):
            if not isinstance(record, dict) or not stable_id(record.get("id")) or not str(record.get("name") or "").strip():
                errors.append(f"{key}[{index}] must contain stable id and non-empty name.")
                continue
            record_id = str(record["id"])
            if record_id in seen:
                errors.append(f"{key}[{index}].id is duplicated: {record_id}.")
            seen.add(record_id)
    expression_payload = {
        "schema": CHARACTER_EXPRESSION_SCHEMA,
        "narrative_expression_profile": payload.get("narrative_expression_profile"),
        "character_expression_contracts": payload.get("character_expression_contracts"),
    }
    errors.extend(
        validate_character_expression_profile(
            expression_payload,
            character_ids=character_ids,
        )
    )


def validate_outline_design(config: ConfigDocument, payload: dict[str, Any], errors: list[str]) -> None:
    required = {
        "schema",
        "book_outline_markdown",
        "story_arcs",
        "volumes",
        "planning_window",
        "chapter_plan",
        "foreshadowing_ledger",
    }
    require_keys(payload, required, required, errors)
    require_nonempty_string(payload, "book_outline_markdown", errors)
    for key in ("story_arcs", "volumes", "chapter_plan", "foreshadowing_ledger"):
        require_list(payload, key, errors)
    if not isinstance(payload.get("planning_window"), dict):
        errors.append("planning_window must be an object.")
    validate_outline_structures(config, payload, errors, initial=True)


def validate_canonical_rolling_outline(
    config: ConfigDocument,
    payload: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate the accumulated canonical outline after zero or more rolling extensions."""

    forecast = compile_length_forecast(config.data["length"])
    story = compile_story_profile(
        config.data["story_profile"],
        market_ids=set(BUILTIN_MARKET_IDS),
    )
    selected_facets = {
        f"{item['kind']}:{item['id']}" for item in story["selected_facets"]
    }
    arc_ids = validate_story_arcs(payload.get("story_arcs"), forecast, selected_facets, errors)
    volume_ids = validate_rolling_volumes(payload.get("volumes"), forecast, arc_ids, errors)
    active_window = validate_planning_window(
        config,
        payload.get("planning_window"),
        errors,
        expected_range=None,
        initial=False,
    )
    plan = payload.get("chapter_plan")
    rows = [item for item in plan if isinstance(item, dict)] if isinstance(plan, list) else []
    last_planned = max(
        (int(item.get("chapter_number") or 0) for item in rows),
        default=0,
    )
    full_range = (1, last_planned) if last_planned > 0 else None
    validate_rolling_chapter_plan(
        plan,
        full_range,
        arc_ids,
        volume_ids,
        selected_facets,
        errors,
        fanfiction_mode=str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction",
    )
    if active_window is not None and last_planned > 0:
        horizon = int(config.data["length"]["planning"]["detailed_horizon"])
        expected_start = max(1, last_planned - horizon + 1)
        if active_window != (expected_start, last_planned):
            errors.append(
                "planning_window must identify the latest bounded section of the accumulated chapter plan."
            )
    validate_arc_foreshadowing(
        payload.get("foreshadowing_ledger"),
        arc_ids,
        errors,
        allow_empty=False,
    )


def validate_outline_extension(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    required = {"schema", "planning_window", "chapter_plan", "foreshadowing_updates"}
    require_keys(payload, required, required, errors)
    for key in ("chapter_plan", "foreshadowing_updates"):
        require_list(payload, key, errors)
    if not isinstance(payload.get("planning_window"), dict):
        errors.append("planning_window must be an object.")
    scope = (manifest or {}).get("scope")
    scope = scope if isinstance(scope, dict) else {}
    start = int(scope.get("from_chapter") or 0)
    end = int(scope.get("to_chapter") or 0)
    try:
        load_covering_arc_simulation(
            root,
            from_chapter=start,
            to_chapter=end,
        )
    except ArcSimulationError as exc:
        errors.append(f"outline_extension requires a current covering causal simulation: {exc}")
    current_plan = read_json(root / "20_outline" / "chapter_plan.json", [])
    if not isinstance(current_plan, list) or not current_plan:
        errors.append("outline_extension requires an existing rolling chapter plan.")
        return
    current_end = max(
        (int(item.get("chapter_number") or 0) for item in current_plan if isinstance(item, dict)),
        default=0,
    )
    if start != current_end + 1:
        errors.append(f"outline_extension must start at the next unplanned chapter: {current_end + 1}.")
    rows = payload.get("chapter_plan")
    if isinstance(rows, list) and rows:
        numbers = [int(item.get("chapter_number") or 0) for item in rows if isinstance(item, dict)]
        if numbers != list(range(start, end + 1)):
            errors.append("outline_extension chapter_plan must exactly cover the declared range.")
    combined = {
        "story_arcs": read_json(root / "20_outline" / "story_arcs.json", []),
        "volumes": read_json(root / "20_outline" / "volumes.json", []),
        "planning_window": payload.get("planning_window"),
        "chapter_plan": rows,
        "foreshadowing_ledger": payload.get("foreshadowing_updates"),
    }
    validate_outline_structures(
        config,
        combined,
        errors,
        initial=False,
        expected_range=(start, end),
        allow_empty_ledger=True,
    )


def validate_outline_revision(config: ConfigDocument, root: Path, payload: dict[str, Any], errors: list[str]) -> None:
    required = {"schema", "from_chapter", "to_chapter", "change_summary", "impact", "replacements"}
    require_keys(payload, required, required, errors)
    start, end = payload.get("from_chapter"), payload.get("to_chapter")
    if not isinstance(start, int) or start <= 0 or not isinstance(end, int) or end < start:
        errors.append("from_chapter/to_chapter must be a valid positive range.")
    elif any(
        manuscript_chapter_path(root, chapter_number, lane="final").is_file()
        for chapter_number in range(start, end + 1)
    ):
        errors.append(
            "outline_revision cannot rewrite finalized chapters; run revision rollback to a safe boundary first."
        )
    require_nonempty_string(payload, "change_summary", errors)
    impact = payload.get("impact")
    if not isinstance(impact, dict) or not isinstance(impact.get("stale_chapters"), list) or not isinstance(impact.get("stale_artifacts"), list):
        errors.append("impact must contain stale_chapters and stale_artifacts lists.")
    replacements = payload.get("replacements")
    allowed = {
        "book_outline_markdown",
        "story_arcs",
        "volumes",
        "planning_window",
        "chapter_plan",
        "foreshadowing_ledger",
        "reader_promise_deferrals",
    }
    if not isinstance(replacements, dict) or not replacements:
        errors.append("replacements must be a non-empty object.")
    elif set(replacements) - allowed:
        errors.append("replacements contains unknown targets.")
    elif isinstance(replacements, dict):
        if "reader_promise_deferrals" in replacements:
            errors.extend(
                validate_planning_deferrals(
                    replacements["reader_promise_deferrals"],
                    load_reader_promise_ledger(root),
                )
            )
        replacement_window = replacements.get(
            "planning_window", read_json(root / "20_outline" / "planning_window.json", {})
        )
        replacement_plan = replacements.get(
            "chapter_plan", read_json(root / "20_outline" / "chapter_plan.json", [])
        )
        if isinstance(replacement_window, dict) and isinstance(replacement_plan, list):
            window_start = int(replacement_window.get("start_chapter") or 0)
            window_end = int(replacement_window.get("end_chapter") or 0)
            replacement_plan = [
                item
                for item in replacement_plan
                if isinstance(item, dict)
                and window_start <= int(item.get("chapter_number") or 0) <= window_end
            ]
        replacement_payload = {
            "story_arcs": replacements.get("story_arcs", read_json(root / "20_outline" / "story_arcs.json", [])),
            "volumes": replacements.get("volumes", read_json(root / "20_outline" / "volumes.json", [])),
            "planning_window": replacement_window,
            "chapter_plan": replacement_plan,
            "foreshadowing_ledger": replacements.get("foreshadowing_ledger") or [
                {
                    key: item.get(key)
                    for key in ("id", "description", "plant", "payoff", "completion_required", "status")
                }
                for item in read_json(root / "20_outline" / "foreshadowing_ledger.json", [])
                if isinstance(item, dict)
            ],
        }
        validate_outline_structures(config, replacement_payload, errors, initial=False)
    if isinstance(start, int) and isinstance(end, int) and start > 0 and end >= start and isinstance(impact, dict):
        expected_chapters, expected_artifacts = recompute_revision_impact(root, start, end)
        supplied_chapters = sorted({item for item in impact.get("stale_chapters", []) if isinstance(item, int)})
        supplied_artifacts = sorted({str(item) for item in impact.get("stale_artifacts", [])})
        if supplied_chapters != expected_chapters:
            errors.append("impact.stale_chapters does not match CLI-recomputed project dependencies.")
        if supplied_artifacts != expected_artifacts:
            errors.append("impact.stale_artifacts does not match CLI-recomputed project dependencies.")


def validate_research_synthesis(payload: dict[str, Any], errors: list[str]) -> None:
    required = {"schema", "synthesis_id", "source_files", "source_hashes", "summary", "claims"}
    require_keys(payload, required, required, errors)
    require_nonempty_string(payload, "synthesis_id", errors)
    require_nonempty_string(payload, "summary", errors)
    require_list(payload, "source_files", errors)
    if not isinstance(payload.get("source_hashes"), dict):
        errors.append("source_hashes must be an object.")
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("claims must be a non-empty list.")
        return
    for index, claim in enumerate(claims):
        claim_fields = {"claim_id", "statement", "evidence", "source_path", "source_hash", "evidence_span"}
        if not isinstance(claim, dict) or set(claim) != claim_fields:
            errors.append(
                f"claims[{index}] must contain claim_id, statement, evidence, source_path, source_hash, evidence_span only."
            )
            continue
        for key in ("claim_id", "statement", "evidence", "source_path", "source_hash"):
            if not isinstance(claim.get(key), str) or not claim[key].strip():
                errors.append(f"claims[{index}].{key} must be a non-empty string.")
        span = claim.get("evidence_span")
        if not isinstance(span, dict) or set(span) != {"start", "end"}:
            errors.append(f"claims[{index}].evidence_span must contain start and end only.")
        elif not isinstance(span.get("start"), int) or not isinstance(span.get("end"), int) or span["start"] < 0 or span["end"] <= span["start"]:
            errors.append(f"claims[{index}].evidence_span must be a valid character range.")


def validate_style_analysis(payload: dict[str, Any], errors: list[str]) -> None:
    required = {"schema", "source_files", "source_hashes", "semantic_profile"}
    require_keys(payload, required, required, errors)
    require_list(payload, "source_files", errors)
    if not isinstance(payload.get("source_hashes"), dict):
        errors.append("source_hashes must be an object.")
    profile = payload.get("semantic_profile")
    fields = {"pov", "tone", "pacing", "dialogue", "craft_rules", "forbidden_patterns"}
    if not isinstance(profile, dict) or set(profile) != fields:
        errors.append("semantic_profile must contain pov, tone, pacing, dialogue, craft_rules, forbidden_patterns only.")
    elif not isinstance(profile.get("craft_rules"), list) or not isinstance(profile.get("forbidden_patterns"), list):
        errors.append("semantic_profile craft_rules and forbidden_patterns must be lists.")


def validate_adaptation_analysis(payload: dict[str, Any], errors: list[str]) -> None:
    required = {
        "schema", "source_files", "source_hashes", "structural_patterns", "pacing_patterns",
        "character_methods", "prose_constraints", "forbidden_copying",
    }
    require_keys(payload, required, required, errors)
    require_list(payload, "source_files", errors)
    if not isinstance(payload.get("source_hashes"), dict):
        errors.append("source_hashes must be an object.")
    for key in ("structural_patterns", "pacing_patterns", "character_methods", "prose_constraints", "forbidden_copying"):
        require_list(payload, key, errors)
    forbidden_fields = {"quoted_passages", "sample_text", "excerpts", "prose_examples", "source_body"}
    if forbidden_fields & set(payload):
        errors.append("adaptation output must not copy source prose or excerpts.")
    for value in walk_strings(payload):
        if len(value) > 800:
            errors.append("adaptation output contains an overlong passage; store techniques, not source prose.")
            break


def validate_fanfiction_canon(config: ConfigDocument, payload: dict[str, Any], errors: list[str]) -> None:
    required = {"schema", "continuity_mode", "sources"}
    require_keys(payload, required, required, errors)
    configured = config.data.get("fanfiction", {}) if isinstance(config.data.get("fanfiction"), dict) else {}
    continuity_mode = str(configured.get("continuity_mode") or "")
    if payload.get("continuity_mode") != continuity_mode:
        errors.append("continuity_mode must match project.yaml fanfiction.continuity_mode.")
    sources = payload.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("sources must be a non-empty list.")
        return
    configured_sources = {
        str(item.get("source_id")): item
        for item in configured.get("sources") or []
        if isinstance(item, dict) and item.get("source_id")
    }
    payload_ids = {
        str(item.get("source_id"))
        for item in sources
        if isinstance(item, dict) and item.get("source_id")
    }
    if payload_ids != set(configured_sources):
        errors.append("sources must contain exactly the source_id values declared in project.yaml.")
    all_entity_ids: set[str] = set()
    for index, source in enumerate(sources):
        validate_fanfiction_canon_source(
            source,
            index=index,
            configured=configured_sources.get(str(source.get("source_id"))) if isinstance(source, dict) else None,
            all_entity_ids=all_entity_ids,
            errors=errors,
        )


def validate_fanfiction_canon_source(
    source: Any,
    *,
    index: int,
    configured: dict[str, Any] | None,
    all_entity_ids: set[str],
    errors: list[str],
) -> None:
    prefix = f"sources[{index}]"
    required = {
        "source_id",
        "title",
        "creator",
        "canon_cutoff",
        "source_files",
        "source_hashes",
        "characters",
        "relationships",
        "world_rules",
        "abilities",
        "timeline",
        "terminology",
        "canon_events",
        "unresolved_questions",
        "evidence",
    }
    if not isinstance(source, dict):
        errors.append(f"{prefix} must be an object.")
        return
    require_keys(source, required, required, errors)
    source_id = stable_id(source.get("source_id"))
    if not source_id:
        errors.append(f"{prefix}.source_id must be a stable id.")
        return
    if configured is None:
        errors.append(f"{prefix}.source_id is not configured: {source_id}.")
    else:
        for field in ("title", "creator", "canon_cutoff"):
            if source.get(field) != configured.get(field):
                errors.append(f"{prefix}.{field} must match project.yaml.")
    if not isinstance(source.get("source_files"), list) or not source.get("source_files"):
        errors.append(f"{prefix}.source_files must be a non-empty list.")
    if not isinstance(source.get("source_hashes"), dict):
        errors.append(f"{prefix}.source_hashes must be an object.")
    evidence_ids: set[str] = set()
    evidence = source.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append(f"{prefix}.evidence must be a non-empty list.")
        evidence = []
    for evidence_index, item in enumerate(evidence):
        evidence_prefix = f"{prefix}.evidence[{evidence_index}]"
        fields = {"evidence_id", "source_path", "source_hash", "evidence_span"}
        if not isinstance(item, dict) or set(item) != fields:
            errors.append(f"{evidence_prefix} must contain evidence_id, source_path, source_hash, evidence_span only.")
            continue
        evidence_id = stable_id(item.get("evidence_id"))
        if not evidence_id or evidence_id in evidence_ids:
            errors.append(f"{evidence_prefix}.evidence_id must be stable and unique.")
        else:
            evidence_ids.add(evidence_id)
        span = item.get("evidence_span")
        if (
            not isinstance(span, dict)
            or set(span) != {"start", "end"}
            or not isinstance(span.get("start"), int)
            or not isinstance(span.get("end"), int)
            or span["start"] < 0
            or span["end"] <= span["start"]
        ):
            errors.append(f"{evidence_prefix}.evidence_span must be a valid start/end character range.")
    characters = source.get("characters")
    character_ids: set[str] = set()
    if not isinstance(characters, list) or not characters:
        errors.append(f"{prefix}.characters must be a non-empty list.")
        characters = []
    for item_index, item in enumerate(characters):
        item_prefix = f"{prefix}.characters[{item_index}]"
        fields = {"id", "name", "summary", "motivation", "voice_traits", "evidence_refs"}
        validate_fanfiction_fact(item, item_prefix, fields, source_id, evidence_ids, all_entity_ids, errors)
        if isinstance(item, dict) and stable_id(item.get("id")):
            character_ids.add(str(item["id"]))
        if isinstance(item, dict) and not isinstance(item.get("voice_traits"), list):
            errors.append(f"{item_prefix}.voice_traits must be a list.")
    relationships = source.get("relationships")
    if not isinstance(relationships, list):
        errors.append(f"{prefix}.relationships must be a list.")
        relationships = []
    for item_index, item in enumerate(relationships):
        item_prefix = f"{prefix}.relationships[{item_index}]"
        fields = {"id", "source_character_id", "target_character_id", "stage", "summary", "evidence_refs"}
        validate_fanfiction_fact(item, item_prefix, fields, source_id, evidence_ids, all_entity_ids, errors)
        if isinstance(item, dict):
            for field in ("source_character_id", "target_character_id"):
                if str(item.get(field) or "") not in character_ids:
                    errors.append(f"{item_prefix}.{field} must reference a character in the same source namespace.")
    fact_specs = {
        "world_rules": {"id", "summary", "evidence_refs"},
        "abilities": {"id", "name", "summary", "limits", "evidence_refs"},
        "timeline": {"id", "order", "summary", "evidence_refs"},
        "terminology": {"id", "name", "summary", "evidence_refs"},
        "canon_events": {"id", "order", "summary", "evidence_refs"},
        "unresolved_questions": {"id", "summary", "evidence_refs"},
    }
    for field, fields in fact_specs.items():
        records = source.get(field)
        if not isinstance(records, list):
            errors.append(f"{prefix}.{field} must be a list.")
            continue
        for item_index, item in enumerate(records):
            validate_fanfiction_fact(
                item,
                f"{prefix}.{field}[{item_index}]",
                fields,
                source_id,
                evidence_ids,
                all_entity_ids,
                errors,
            )


def validate_fanfiction_fact(
    item: Any,
    prefix: str,
    fields: set[str],
    source_id: str,
    evidence_ids: set[str],
    all_entity_ids: set[str],
    errors: list[str],
) -> None:
    if not isinstance(item, dict) or set(item) != fields:
        errors.append(f"{prefix} must contain exactly: {', '.join(sorted(fields))}.")
        return
    item_id = stable_id(item.get("id"))
    if not item_id or not item_id.startswith(f"{source_id}:") or item_id in all_entity_ids:
        errors.append(f"{prefix}.id must be unique and start with `{source_id}:`.")
    else:
        all_entity_ids.add(item_id)
    if not isinstance(item.get("summary"), str) or not item["summary"].strip():
        errors.append(f"{prefix}.summary must be a non-empty paraphrased fact.")
    refs = item.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        errors.append(f"{prefix}.evidence_refs must be a non-empty list.")
    elif any(str(ref) not in evidence_ids for ref in refs):
        errors.append(f"{prefix}.evidence_refs must reference evidence from the same source.")


def validate_fanfiction_design(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    errors: list[str],
) -> None:
    required = {
        "schema",
        "continuity_mode",
        "canon_cutoff",
        "divergence_point",
        "ooc_tolerance",
        "character_voice_contracts",
        "original_mainline",
        "original_characters",
        "world_rule_changes",
        "butterfly_effects",
        "ending_boundary",
        "original_contribution",
        "protected_reveals",
        "cross_source_rules",
        "book_design",
    }
    require_keys(payload, required, required, errors)
    configured = config.data.get("fanfiction", {}) if isinstance(config.data.get("fanfiction"), dict) else {}
    if payload.get("continuity_mode") != configured.get("continuity_mode"):
        errors.append("continuity_mode must match project.yaml fanfiction.continuity_mode.")
    for field in ("canon_cutoff", "divergence_point", "ending_boundary"):
        require_nonempty_string(payload, field, errors)
    if payload.get("ooc_tolerance") not in {"strict", "bounded", "transformative"}:
        errors.append("ooc_tolerance must be strict, bounded, or transformative.")
    for field in (
        "character_voice_contracts",
        "original_characters",
        "world_rule_changes",
        "butterfly_effects",
        "original_contribution",
        "protected_reveals",
        "cross_source_rules",
    ):
        require_list(payload, field, errors)
    mainline = payload.get("original_mainline")
    if not isinstance(mainline, dict) or set(mainline) != {"premise", "central_conflict", "reader_promise"}:
        errors.append("original_mainline must contain premise, central_conflict, reader_promise only.")
    elif any(not isinstance(value, str) or not value.strip() for value in mainline.values()):
        errors.append("original_mainline fields must be non-empty strings.")
    canon = read_json(root / "10_bible" / "fanfiction" / "source_canon.json", {})
    known_characters = {
        str(character.get("id"))
        for source in canon.get("sources", []) if isinstance(canon, dict) and isinstance(source, dict)
        for character in source.get("characters", []) if isinstance(character, dict) and character.get("id")
    }
    contracts = payload.get("character_voice_contracts")
    if not isinstance(contracts, list) or not contracts:
        errors.append("character_voice_contracts must be a non-empty list.")
    else:
        seen: set[str] = set()
        for index, contract in enumerate(contracts):
            fields = {"character_id", "baseline_voice", "invariants", "allowed_changes", "forbidden_shortcuts"}
            if not isinstance(contract, dict) or set(contract) != fields:
                errors.append(f"character_voice_contracts[{index}] must contain exactly {sorted(fields)}.")
                continue
            character_id = str(contract.get("character_id") or "")
            if character_id not in known_characters or character_id in seen:
                errors.append(f"character_voice_contracts[{index}].character_id must be unique and declared in source canon.")
            seen.add(character_id)
            require_nonempty_string(contract, "baseline_voice", errors)
            for field in ("invariants", "allowed_changes", "forbidden_shortcuts"):
                if not isinstance(contract.get(field), list):
                    errors.append(f"character_voice_contracts[{index}].{field} must be a list.")
    if configured.get("continuity_mode") == "crossover" and not payload.get("cross_source_rules"):
        errors.append("crossover fanfiction requires cross_source_rules.")
    if configured.get("continuity_mode") == "crossover":
        validate_crossover_rules(configured, payload.get("cross_source_rules"), errors)
    book_design = payload.get("book_design")
    if not isinstance(book_design, dict):
        errors.append("book_design must be a book_design_candidate_v2 object.")
    else:
        validate_book_design(book_design, errors)


def validate_crossover_rules(
    configured: dict[str, Any],
    rules: Any,
    errors: list[str],
) -> None:
    if not isinstance(rules, list):
        return
    configured_ids = {
        str(source.get("source_id"))
        for source in configured.get("sources") or []
        if isinstance(source, dict) and source.get("source_id")
    }
    covered_ids: set[str] = set()
    expected_fields = {
        "source_ids",
        "conflict_rule",
        "power_conversion",
        "terminology_collision_policy",
    }
    for index, rule in enumerate(rules):
        prefix = f"cross_source_rules[{index}]"
        if not isinstance(rule, dict) or set(rule) != expected_fields:
            errors.append(f"{prefix} must contain exactly {sorted(expected_fields)}.")
            continue
        source_ids = rule.get("source_ids")
        if (
            not isinstance(source_ids, list)
            or len(source_ids) < 2
            or len(source_ids) != len(set(str(item) for item in source_ids))
            or any(str(item) not in configured_ids for item in source_ids)
        ):
            errors.append(f"{prefix}.source_ids must contain at least two unique configured source ids.")
        else:
            covered_ids.update(str(item) for item in source_ids)
        for field in ("conflict_rule", "power_conversion", "terminology_collision_policy"):
            if not isinstance(rule.get(field), str) or not rule[field].strip():
                errors.append(f"{prefix}.{field} must be a non-empty string.")
    missing = sorted(configured_ids - covered_ids)
    if missing:
        errors.append("cross_source_rules must cover every configured source: " + ", ".join(missing) + ".")


def validate_sources(
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
    *,
    require_hashes: bool,
) -> None:
    if payload.get("schema") == "fanfiction_source_canon_v1":
        validate_fanfiction_source_evidence(root, payload, manifest, errors)
        return
    sources = payload.get("source_files")
    if not isinstance(sources, list) or not sources:
        errors.append("source_files must be a non-empty list.")
        return
    declared = set(manifest_input_paths(manifest or {}))
    hashes = payload.get("source_hashes") if isinstance(payload.get("source_hashes"), dict) else {}
    for index, item in enumerate(sources):
        source = str(item)
        if source not in declared:
            errors.append(f"source_files[{index}] is not declared in manifest input_files: {source}")
            continue
        path = root / source
        if not path.is_file():
            errors.append(f"source_files[{index}] does not exist: {source}")
            continue
        if require_hashes and hashes.get(source) != sha256(path.read_bytes()).hexdigest():
            errors.append(f"source_hashes[{source}] does not match current input content.")
    if payload.get("schema") == "research_synthesis_v1":
        for index, claim in enumerate(payload.get("claims") or []):
            if not isinstance(claim, dict):
                continue
            source = str(claim.get("source_path") or "")
            if source not in sources:
                errors.append(f"claims[{index}].source_path must reference source_files.")
                continue
            path = root / source
            if not path.is_file():
                continue
            expected_hash = sha256(path.read_bytes()).hexdigest()
            if claim.get("source_hash") != expected_hash:
                errors.append(f"claims[{index}].source_hash does not match current source content.")
            span = claim.get("evidence_span")
            evidence = str(claim.get("evidence") or "")
            if not isinstance(span, dict) or not isinstance(span.get("start"), int) or not isinstance(span.get("end"), int):
                continue
            source_text = path.read_text(encoding="utf-8").lstrip("\ufeff")
            start, end = span["start"], span["end"]
            if start < 0 or end > len(source_text) or end <= start:
                errors.append(f"claims[{index}].evidence_span is outside source content.")
            elif source_text[start:end] != evidence:
                errors.append(f"claims[{index}].evidence must exactly match the declared source span.")
    if payload.get("schema") == "adaptation_analysis_v1":
        validate_adaptation_similarity(root, payload, errors)


def validate_fanfiction_source_evidence(
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    declared = set(manifest_input_paths(manifest or {}))
    for source_index, source in enumerate(payload.get("sources") or []):
        if not isinstance(source, dict):
            continue
        source_files = source.get("source_files") if isinstance(source.get("source_files"), list) else []
        source_hashes = source.get("source_hashes") if isinstance(source.get("source_hashes"), dict) else {}
        source_texts: list[str] = []
        for file_index, source_path in enumerate(source_files):
            source_path = str(source_path)
            if source_path not in declared:
                errors.append(
                    f"sources[{source_index}].source_files[{file_index}] is not declared in manifest input_files."
                )
                continue
            path = root / source_path
            if not path.is_file():
                errors.append(f"sources[{source_index}].source_files[{file_index}] does not exist.")
                continue
            expected_hash = sha256(path.read_bytes()).hexdigest()
            if source_hashes.get(source_path) != expected_hash:
                errors.append(f"sources[{source_index}].source_hashes[{source_path}] does not match current content.")
            source_texts.append(path.read_text(encoding="utf-8").lstrip("\ufeff"))
        for evidence_index, evidence in enumerate(source.get("evidence") or []):
            if not isinstance(evidence, dict):
                continue
            source_path = str(evidence.get("source_path") or "")
            if source_path not in source_files:
                errors.append(
                    f"sources[{source_index}].evidence[{evidence_index}].source_path must reference source_files."
                )
                continue
            path = root / source_path
            if not path.is_file():
                continue
            expected_hash = sha256(path.read_bytes()).hexdigest()
            if evidence.get("source_hash") != expected_hash:
                errors.append(
                    f"sources[{source_index}].evidence[{evidence_index}].source_hash does not match current content."
                )
            span = evidence.get("evidence_span")
            if not isinstance(span, dict):
                continue
            text = path.read_text(encoding="utf-8").lstrip("\ufeff")
            start, end = span.get("start"), span.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end > len(text) or end <= start:
                errors.append(
                    f"sources[{source_index}].evidence[{evidence_index}].evidence_span is outside source content."
                )
        validate_fanfiction_canon_prose_originality(
            source,
            source_texts=source_texts,
            source_index=source_index,
            errors=errors,
        )


def validate_fanfiction_canon_prose_originality(
    source: dict[str, Any],
    *,
    source_texts: list[str],
    source_index: int,
    errors: list[str],
) -> None:
    protected_terms = {
        str(source.get("title") or ""),
        str(source.get("creator") or ""),
    }
    for field in ("characters", "abilities", "terminology"):
        for item in source.get(field) or []:
            if isinstance(item, dict) and item.get("name"):
                protected_terms.add(str(item["name"]))
    parts = [
        normalize_fanfiction_canon_text(value, protected_terms)
        for value in fanfiction_canon_prose_fields(source)
    ]
    parts = [part for part in parts if len(part) >= 8]
    if not parts:
        return
    candidate_grams = {
        part[index:index + 8]
        for part in parts
        for index in range(len(part) - 7)
    }
    for source_text in source_texts:
        normalized_source = normalize_fanfiction_canon_text(source_text, protected_terms)
        if any(
            len(part) >= 36
            and (
                part in normalized_source
                or ngram_overlap_ratio(part, normalized_source, size=8) >= 0.80
            )
            for part in parts
        ) or has_reconstructed_source_run(normalized_source, candidate_grams, size=8):
            errors.append(
                f"sources[{source_index}] reconstructs source prose in canon fields; "
                "store paraphrased facts and evidence locations only."
            )
            return


def fanfiction_canon_prose_fields(source: dict[str, Any]) -> Iterable[str]:
    scalar_fields = ("summary", "motivation", "stage")
    list_fields = ("voice_traits", "limits")
    for collection in (
        "characters",
        "relationships",
        "world_rules",
        "abilities",
        "timeline",
        "terminology",
        "canon_events",
        "unresolved_questions",
    ):
        for item in source.get(collection) or []:
            if not isinstance(item, dict):
                continue
            for field in scalar_fields:
                if isinstance(item.get(field), str):
                    yield item[field]
            for field in list_fields:
                for value in item.get(field) or []:
                    if isinstance(value, str):
                        yield value


def normalize_fanfiction_canon_text(value: str, protected_terms: set[str]) -> str:
    normalized = str(value).lower()
    for term in sorted((item for item in protected_terms if item), key=len, reverse=True):
        normalized = normalized.replace(term.lower(), "")
    return normalize_similarity_text(normalized)


def has_reconstructed_source_run(source: str, candidate_grams: set[str], *, size: int) -> bool:
    if len(source) < 36 or not candidate_grams:
        return False
    run_start: int | None = None
    last_hit: int | None = None
    hit_count = 0
    for index in range(len(source) - size + 1):
        if source[index:index + size] not in candidate_grams:
            continue
        if last_hit is None or index - last_hit > size:
            run_start = index
            hit_count = 0
        last_hit = index
        hit_count += 1
        assert run_start is not None
        run_grams = index - run_start + 1
        run_chars = run_grams + size - 1
        if run_chars >= 36 and hit_count / run_grams >= 0.65:
            return True
    return False


def stable_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{1,79}", text):
        return ""
    return text


def validate_outline_structures(
    config: ConfigDocument,
    payload: dict[str, Any],
    errors: list[str],
    *,
    initial: bool,
    expected_range: tuple[int, int] | None = None,
    allow_empty_ledger: bool = False,
) -> None:
    forecast = compile_length_forecast(config.data["length"])
    story = compile_story_profile(
        config.data["story_profile"],
        market_ids=set(BUILTIN_MARKET_IDS),
    )
    selected_facets = {
        f"{item['kind']}:{item['id']}" for item in story["selected_facets"]
    }
    arc_ids = validate_story_arcs(payload.get("story_arcs"), forecast, selected_facets, errors)
    volume_ids = validate_rolling_volumes(payload.get("volumes"), forecast, arc_ids, errors)
    window = validate_planning_window(
        config,
        payload.get("planning_window"),
        errors,
        expected_range=expected_range,
        initial=initial,
    )
    validate_rolling_chapter_plan(
        payload.get("chapter_plan"),
        window,
        arc_ids,
        volume_ids,
        selected_facets,
        errors,
        fanfiction_mode=str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction",
    )
    validate_arc_foreshadowing(
        payload.get("foreshadowing_ledger"),
        arc_ids,
        errors,
        allow_empty=allow_empty_ledger,
    )


def validate_story_arcs(
    value: Any,
    forecast: Any,
    selected_facets: set[str],
    errors: list[str],
) -> set[str]:
    required = {
        "id", "number", "title", "phase", "progress_window", "target_characters", "goal",
        "conflict_escalation", "character_arc_moves", "promise_ids", "active_facets",
        "quality_focus",
    }
    if not isinstance(value, list) or not value:
        errors.append("story_arcs must contain the full-book macro arcs.")
        return set()
    arc_ids: set[str] = set()
    previous_end = 0.0
    total_characters = 0
    for index, arc in enumerate(value):
        if not isinstance(arc, dict) or set(arc) != required:
            errors.append(f"story_arcs[{index}] must contain exactly: {', '.join(sorted(required))}.")
            continue
        arc_id = stable_id(arc.get("id"))
        if not arc_id or arc_id in arc_ids:
            errors.append(f"story_arcs[{index}].id must be stable and unique.")
        else:
            arc_ids.add(arc_id)
        if arc.get("number") != index + 1:
            errors.append(f"story_arcs[{index}].number must be {index + 1}.")
        window = arc.get("progress_window")
        if not valid_progress_window(window) or abs(float(window[0]) - previous_end) > 0.000001:
            errors.append(f"story_arcs[{index}].progress_window must continue from {previous_end:.6f}.")
        else:
            previous_end = float(window[1])
        target = arc.get("target_characters")
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            errors.append(f"story_arcs[{index}].target_characters must be a positive integer.")
        else:
            total_characters += target
        for field in ("title", "phase", "goal", "conflict_escalation"):
            if not isinstance(arc.get(field), str) or not arc[field].strip():
                errors.append(f"story_arcs[{index}].{field} must be a non-empty string.")
        for field in ("character_arc_moves", "promise_ids"):
            if not isinstance(arc.get(field), list):
                errors.append(f"story_arcs[{index}].{field} must be a list.")
        active = arc.get("active_facets")
        if not isinstance(active, list) or not 1 <= len(active) <= 3:
            errors.append(f"story_arcs[{index}].active_facets must contain one to three selected facets.")
        elif any(str(item) not in selected_facets for item in active):
            errors.append(f"story_arcs[{index}].active_facets references an unselected facet.")
        quality_focus = arc.get("quality_focus")
        focus_fields = {"requirements", "preferences", "risks", "review_questions"}
        if not isinstance(quality_focus, dict) or set(quality_focus) != focus_fields:
            errors.append(
                f"story_arcs[{index}].quality_focus must contain exactly: "
                + ", ".join(sorted(focus_fields))
                + "."
            )
        else:
            for field in sorted(focus_fields):
                entries = quality_focus.get(field)
                if not isinstance(entries, list) or any(
                    not isinstance(item, str) or not item.strip() for item in entries
                ):
                    errors.append(f"story_arcs[{index}].quality_focus.{field} must be a string list.")
    if abs(previous_end - 1.0) > 0.000001:
        errors.append("story_arcs progress windows must end at 1.0.")
    if not forecast.completion_min_characters <= total_characters <= forecast.completion_max_characters:
        errors.append("story_arcs target_characters must fit the book completion tolerance.")
    return arc_ids


def validate_rolling_volumes(
    value: Any,
    forecast: Any,
    arc_ids: set[str],
    errors: list[str],
) -> set[str]:
    required = {
        "id", "number", "title", "target_characters", "arc_ids", "goal", "escalation", "ending_turn",
    }
    if not isinstance(value, list) or not value:
        errors.append("volumes must contain the full-book volume budget.")
        return set()
    volume_ids: set[str] = set()
    total_characters = 0
    for index, volume in enumerate(value):
        if not isinstance(volume, dict) or set(volume) != required:
            errors.append(f"volumes[{index}] must contain exactly: {', '.join(sorted(required))}.")
            continue
        volume_id = stable_id(volume.get("id"))
        if not volume_id or volume_id in volume_ids:
            errors.append(f"volumes[{index}].id must be stable and unique.")
        else:
            volume_ids.add(volume_id)
        if volume.get("number") != index + 1:
            errors.append(f"volumes[{index}].number must be {index + 1}.")
        target = volume.get("target_characters")
        if not isinstance(target, int) or isinstance(target, bool) or target <= 0:
            errors.append(f"volumes[{index}].target_characters must be a positive integer.")
        else:
            total_characters += target
        declared_arcs = volume.get("arc_ids")
        if not isinstance(declared_arcs, list) or not declared_arcs:
            errors.append(f"volumes[{index}].arc_ids must be a non-empty list.")
        elif any(str(item) not in arc_ids for item in declared_arcs):
            errors.append(f"volumes[{index}].arc_ids references an undeclared story arc.")
        for field in ("title", "goal", "escalation", "ending_turn"):
            if not isinstance(volume.get(field), str) or not volume[field].strip():
                errors.append(f"volumes[{index}].{field} must be a non-empty string.")
    if not forecast.completion_min_characters <= total_characters <= forecast.completion_max_characters:
        errors.append("volumes target_characters must fit the book completion tolerance.")
    return volume_ids


def validate_planning_window(
    config: ConfigDocument,
    value: Any,
    errors: list[str],
    *,
    expected_range: tuple[int, int] | None,
    initial: bool,
) -> tuple[int, int] | None:
    required = {"schema", "start_chapter", "end_chapter", "detailed_horizon", "refill_threshold"}
    if not isinstance(value, dict) or set(value) != required:
        errors.append("planning_window must contain schema, start_chapter, end_chapter, detailed_horizon, refill_threshold only.")
        return None
    if value.get("schema") != "rolling_outline_window_v1":
        errors.append("planning_window.schema must be rolling_outline_window_v1.")
    planning = config.data["length"]["planning"]
    start = value.get("start_chapter")
    end = value.get("end_chapter")
    if not isinstance(start, int) or not isinstance(end, int) or start <= 0 or end < start:
        errors.append("planning_window start_chapter/end_chapter must be a positive continuous range.")
        return None
    if initial and start != 1:
        errors.append("initial planning_window must start at chapter 1.")
    if expected_range and (start, end) != expected_range:
        errors.append("planning_window must match the Agent task range.")
    if value.get("detailed_horizon") != int(planning["detailed_horizon"]):
        errors.append("planning_window.detailed_horizon must match the length contract.")
    if value.get("refill_threshold") != int(planning["refill_threshold"]):
        errors.append("planning_window.refill_threshold must match the length contract.")
    if end - start + 1 > int(planning["detailed_horizon"]):
        errors.append("planning_window exceeds the configured detailed horizon.")
    return start, end


def validate_rolling_chapter_plan(
    value: Any,
    window: tuple[int, int] | None,
    arc_ids: set[str],
    volume_ids: set[str],
    selected_facets: set[str],
    errors: list[str],
    *,
    fanfiction_mode: bool,
) -> None:
    required = {
        "chapter_number", "title", "chapter_duty", "conflict", "chapter_turn", "hook",
        "reader_gain", "volume_id", "arc_id", "featured_character_ids", "characterization_focus",
        "scene_wants", "relationship_move", "active_facets", "forbidden_reveals",
        "primary_story_engine", "primary_scene_carrier", "state_change_kind", "dramatic_method",
    }
    if fanfiction_mode:
        required.add("protected_canon_outcomes")
    if not isinstance(value, list) or not value:
        errors.append("chapter_plan must contain the current detailed rolling window.")
        return
    expected_numbers = list(range(window[0], window[1] + 1)) if window else []
    numbers: list[int] = []
    for index, chapter in enumerate(value):
        if not isinstance(chapter, dict):
            errors.append(f"chapter_plan[{index}] must be an object.")
            continue
        removed_aliases = sorted(REMOVED_ALIAS_FIELDS & set(chapter))
        if removed_aliases:
            errors.append(
                f"chapter_plan[{index}] contains removed aliases: {', '.join(removed_aliases)}."
            )
        missing = required - set(chapter)
        if missing:
            errors.append(f"chapter_plan[{index}] missing fields: {', '.join(sorted(missing))}.")
            continue
        number = chapter.get("chapter_number")
        numbers.append(number if isinstance(number, int) else 0)
        if str(chapter.get("arc_id") or "") not in arc_ids:
            errors.append(f"chapter_plan[{index}].arc_id must reference a declared story arc.")
        if str(chapter.get("volume_id") or "") not in volume_ids:
            errors.append(f"chapter_plan[{index}].volume_id must reference a declared volume.")
        for field in (
            "title", "chapter_duty", "conflict", "chapter_turn", "hook", "reader_gain",
            "relationship_move", "primary_story_engine", "primary_scene_carrier",
            "state_change_kind", "dramatic_method",
        ):
            if not isinstance(chapter.get(field), str) or not chapter[field].strip():
                errors.append(f"chapter_plan[{index}].{field} must be a non-empty string.")
        for field in ("featured_character_ids", "characterization_focus"):
            items = chapter.get(field)
            if not isinstance(items, list) or not items or any(not stable_id(item) for item in items):
                errors.append(f"chapter_plan[{index}].{field} must be a non-empty stable-id list.")
        if not isinstance(chapter.get("scene_wants"), dict) or not chapter["scene_wants"]:
            errors.append(f"chapter_plan[{index}].scene_wants must be a non-empty object.")
        active = chapter.get("active_facets")
        if not isinstance(active, list) or not 1 <= len(active) <= 3:
            errors.append(f"chapter_plan[{index}].active_facets must contain one to three facets.")
        elif any(str(item) not in selected_facets for item in active):
            errors.append(f"chapter_plan[{index}].active_facets references an unselected facet.")
        if not isinstance(chapter.get("forbidden_reveals"), list):
            errors.append(f"chapter_plan[{index}].forbidden_reveals must be a list.")
        if fanfiction_mode:
            protected = chapter.get("protected_canon_outcomes")
            if not isinstance(protected, list) or not protected or any(
                not isinstance(item, str) or not item.strip() for item in protected
            ):
                errors.append(
                    f"chapter_plan[{index}].protected_canon_outcomes must be a non-empty string list."
                )
    if window and numbers != expected_numbers:
        errors.append("chapter_plan chapter numbers must exactly match planning_window.")


def validate_arc_foreshadowing(
    value: Any,
    arc_ids: set[str],
    errors: list[str],
    *,
    allow_empty: bool,
) -> None:
    required = {"id", "description", "plant", "payoff", "completion_required", "status"}
    if not isinstance(value, list) or (not value and not allow_empty):
        errors.append("foreshadowing_ledger must contain at least one arc-relative planned thread.")
        return
    seen: set[str] = set()
    for index, thread in enumerate(value):
        if not isinstance(thread, dict) or set(thread) != required:
            errors.append(f"foreshadowing_ledger[{index}] must contain exactly: {', '.join(sorted(required))}.")
            continue
        thread_id = stable_id(thread.get("id"))
        if not thread_id or thread_id in seen:
            errors.append(f"foreshadowing_ledger[{index}].id must be stable and unique.")
        else:
            seen.add(thread_id)
        for field in ("description", "status"):
            if not isinstance(thread.get(field), str) or not thread[field].strip():
                errors.append(f"foreshadowing_ledger[{index}].{field} must be a non-empty string.")
        if not isinstance(thread.get("completion_required"), bool):
            errors.append(f"foreshadowing_ledger[{index}].completion_required must be boolean.")
        for field in ("plant", "payoff"):
            marker = thread.get(field)
            if not isinstance(marker, dict) or set(marker) != {"arc_id", "progress_window"}:
                errors.append(f"foreshadowing_ledger[{index}].{field} must contain arc_id and progress_window only.")
            elif str(marker.get("arc_id") or "") not in arc_ids or not valid_progress_window(marker.get("progress_window")):
                errors.append(f"foreshadowing_ledger[{index}].{field} must reference an arc and valid progress window.")


def valid_progress_window(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(not isinstance(item, bool) and isinstance(item, (int, float)) for item in value)
        and 0 <= float(value[0]) < float(value[1]) <= 1
    )


def recompute_revision_impact(root: Path, start: int, end: int) -> tuple[list[int], list[str]]:
    chapters: set[int] = set()
    artifacts: set[str] = set()
    patterns = (
        ("20_outline/chapter_cards", "ch*.json"),
        ("50_workbench/beats", "ch*.json"),
        ("50_workbench/writing_tasks", "ch*.json"),
        ("30_state/tcs", "ch*.json"),
    )
    for directory, pattern in patterns:
        for path in (root / directory).glob(pattern):
            match = re.search(r"ch(\d+)", path.name)
            if not match:
                continue
            number = int(match.group(1))
            if start <= number <= end:
                chapters.add(number)
                artifacts.add(path.resolve().relative_to(root.resolve()).as_posix())
    return sorted(chapters), sorted(artifacts)


def validate_adaptation_similarity(root: Path, payload: dict[str, Any], errors: list[str]) -> None:
    sources: list[str] = [str(item) for item in payload.get("source_files") or []]
    source_texts = [read_text(root / item) for item in sources if (root / item).is_file()]
    technique_fields = ("structural_patterns", "pacing_patterns", "character_methods", "prose_constraints")
    candidate_parts = [
        str(item)
        for field in technique_fields
        for item in payload.get(field) or []
        if isinstance(item, str)
    ]
    for candidate in candidate_parts:
        normalized_candidate = normalize_similarity_text(candidate)
        if len(normalized_candidate) < 30:
            continue
        for source_text in source_texts:
            normalized_source = normalize_similarity_text(source_text)
            if normalized_candidate in normalized_source or ngram_overlap_ratio(normalized_candidate, normalized_source) >= 0.35:
                errors.append("adaptation output is too similar to declared source prose; keep abstract techniques only.")
                return
    combined = normalize_similarity_text(" ".join(candidate_parts))
    if len(combined) >= 60:
        for source_text in source_texts:
            if ngram_overlap_ratio(combined, normalize_similarity_text(source_text)) >= 0.35:
                errors.append("adaptation output reconstructs source prose across fields.")
                return


def normalize_similarity_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value).lower(), flags=re.UNICODE)


def ngram_overlap_ratio(candidate: str, source: str, *, size: int = 8) -> float:
    if len(candidate) < size or len(source) < size:
        return 0.0
    candidate_grams = {candidate[index:index + size] for index in range(len(candidate) - size + 1)}
    source_grams = {source[index:index + size] for index in range(len(source) - size + 1)}
    return len(candidate_grams & source_grams) / max(1, len(candidate_grams))


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except OSError:
        return ""


def walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_strings(child)


def apply_targets(
    root: Path,
    task_type: str,
    payload: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
) -> list[Path]:
    spec = TASK_SPECS[task_type]
    targets = [root / item for item in spec["targets"]]
    if task_type == "arc_simulation":
        start = int(payload["from_chapter"])
        end = int(payload["to_chapter"])
        targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
        targets.append(arc_simulation_path(root, start, end))
    if task_type == "outline_extension":
        targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
    if task_type == "chapter_direction":
        chapter_number = int(payload["chapter_number"])
        targets.extend(
            [
                root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
                root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.md",
                root / "20_outline" / "chapter_plan.json",
            ]
        )
    if task_type == "book_design":
        targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
        for optional in ("factions", "locations"):
            if optional in payload:
                targets.append(root / "10_bible" / f"{optional}.json")
    if task_type == "character_expression_review":
        targets.append(
            root
            / "50_workbench"
            / "character_reviews"
            / character_review_report_name(scope or {})
        )
    if task_type == "fanfiction_design":
        targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
        book_design = payload.get("book_design") if isinstance(payload.get("book_design"), dict) else {}
        for optional in ("factions", "locations"):
            if optional in book_design:
                targets.append(root / "10_bible" / f"{optional}.json")
    if task_type == "outline_revision":
        targets.append(root / "20_outline" / "revise_reports" / revision_report_name(payload))
        for chapter_number in range(int(payload["from_chapter"]), int(payload["to_chapter"]) + 1):
            targets.extend(
                [
                    root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
                    root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.md",
                ]
            )
        targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
        targets.extend(
            outline_revision_side_effect_targets(
                root,
                payload["impact"]["stale_chapters"],
            )
        )
    if task_type == "outline_design":
        targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
    return list(dict.fromkeys(targets))


def outline_revision_side_effect_targets(
    root: Path,
    chapter_numbers: Iterable[int],
) -> list[Path]:
    """Declare every derived/editorial owner mutated by an outline revision."""

    chapters = sorted(
        {
            int(chapter_number)
            for chapter_number in chapter_numbers
            if isinstance(chapter_number, int)
            and not isinstance(chapter_number, bool)
            and chapter_number > 0
        }
    )
    return [
        root / "50_workbench" / "editorial_patterns" / "registry.jsonl",
        root / "50_workbench" / "agent_tasks",
        root / "70_runtime" / "db",
        *(
            root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json"
            for chapter_number in chapters
        ),
    ]


def invalidate_outline_revision_tasks(
    root: Path,
    *,
    chapter_numbers: Iterable[int],
    artifact: Path,
) -> None:
    """Supersede task projections whose chapter contract was replaced by a revision."""

    affected = {
        int(chapter_number)
        for chapter_number in chapter_numbers
        if isinstance(chapter_number, int)
        and not isinstance(chapter_number, bool)
        and chapter_number > 0
    }
    for task in list_manifests(root):
        if manifest_chapter_number(task) not in affected:
            continue
        task_type = str(task.get("task_type") or "")
        status = str(task.get("status") or "awaiting_agent")
        if task_type in {"outline_revision", "design_semantic_compile"} or status in {
            "rolled_back",
            "superseded",
        }:
            continue
        update_task_status(
            root,
            str(task.get("task_id") or ""),
            to_status="superseded",
            command="intelligence apply compiled outline_revision",
            artifact=artifact,
            result="chapter planning contract changed",
        )


def mark_outline_revision_writing_tasks_stale(
    root: Path,
    *,
    chapter_numbers: Iterable[int],
) -> None:
    """Invalidate compiled author briefs after their planning contract changes."""

    chapters = sorted(
        {
            int(chapter_number)
            for chapter_number in chapter_numbers
            if isinstance(chapter_number, int)
            and not isinstance(chapter_number, bool)
            and chapter_number > 0
        }
    )
    for chapter_number in chapters:
        path = root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json"
        payload = read_json(path, {})
        if not isinstance(payload, dict):
            continue
        payload["status"] = "stale"
        payload["stale_reason"] = "outline_revision"
        payload["stale_at"] = datetime.now(timezone.utc).isoformat()
        write_json(path, payload)


def write_targets(
    config: ConfigDocument,
    root: Path,
    task_type: str,
    payload: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
) -> None:
    if task_type == "book_ideation":
        write_book_ideation_decision(root, payload)
        return
    if task_type == "book_design":
        basis_before = current_basis_hashes(root)
        write_book_design_targets(root, payload)
        mark_project_intelligence_applied(root, "book_design", payload)
        mark_project_intelligence_applied(root, "character_expression_design", payload)
        stale_causal_simulations_if_basis_changed(root, basis_before)
        return
    if task_type == "character_expression_design":
        write_character_expression_profile(root, payload)
        mark_project_intelligence_applied(root, "character_expression_design", payload)
        return
    if task_type == "character_expression_review":
        write_json(
            root / "50_workbench" / "character_reviews" / character_review_report_name(scope or {}),
            payload,
        )
        return
    if task_type == "fanfiction_canon":
        canonical = dict(payload)
        canonical["rights_declarations"] = fanfiction_rights_declarations(root)
        canonical["rights_policy"] = {
            "advisory_only": True,
            "blocks_creation": False,
            "blocks_export": False,
            "statement": "Rights entries are user declarations and are not legal verification.",
        }
        write_json(root / "10_bible" / "fanfiction" / "source_canon.json", canonical)
        mark_project_intelligence_applied(root, "fanfiction_canon", canonical)
        append_creation_event(root, "fanfiction_canon_applied", canonical)
        return
    if task_type == "fanfiction_design":
        basis_before = current_basis_hashes(root)
        write_json(root / "10_bible" / "fanfiction" / "fanfiction_bible.json", payload)
        write_book_design_targets(root, payload["book_design"])
        mark_project_intelligence_applied(root, "fanfiction_design", payload)
        mark_project_intelligence_applied(root, "book_design", payload["book_design"])
        mark_project_intelligence_applied(root, "character_expression_design", payload["book_design"])
        append_creation_event(root, "fanfiction_design_applied", payload)
        stale_causal_simulations_if_basis_changed(root, basis_before)
        return
    if task_type == "outline_design":
        basis_before = current_basis_hashes(root)
        atomic_write_text(root / "20_outline" / "book_outline.md", payload["book_outline_markdown"].rstrip() + "\n")
        write_json(root / "20_outline" / "story_arcs.json", payload["story_arcs"])
        write_json(root / "20_outline" / "volumes.json", payload["volumes"])
        write_json(root / "20_outline" / "chapter_plan.json", payload["chapter_plan"])
        write_json(root / "20_outline" / "planning_window.json", payload["planning_window"])
        materialized_threads = materialize_foreshadowing_ledger(
            config, payload["foreshadowing_ledger"], payload["story_arcs"]
        )
        write_json(root / "20_outline" / "foreshadowing_ledger.json", materialized_threads)
        creative_brief = read_json(root / "10_bible" / "creative_brief.json", {})
        story_engine = creative_brief.get("story_engine_contract") if isinstance(creative_brief, dict) else {}
        write_reader_promise_ledger(
            root,
            merge_planned_reader_promises(
                root,
                story_engine_contract=story_engine if isinstance(story_engine, dict) else {},
                foreshadowing_ledger=materialized_threads,
                estimated_chapters=compile_length_forecast(config.data["length"]).estimated_chapters,
            ),
        )
        mark_project_intelligence_applied(root, "outline_design", payload)
        stale_causal_simulations_if_basis_changed(root, basis_before)
        return
    if task_type == "arc_simulation":
        mark_overlapping_arc_simulations_stale(
            root,
            from_chapter=int(payload["from_chapter"]),
            to_chapter=int(payload["to_chapter"]),
        )
        write_arc_causal_simulation(root, payload)
        return
    if task_type == "outline_extension":
        basis_before = current_basis_hashes(root)
        existing_plan = read_json(root / "20_outline" / "chapter_plan.json", [])
        existing_ledger = read_json(root / "20_outline" / "foreshadowing_ledger.json", [])
        arcs = read_json(root / "20_outline" / "story_arcs.json", [])
        write_json(root / "20_outline" / "chapter_plan.json", list(existing_plan) + list(payload["chapter_plan"]))
        write_json(root / "20_outline" / "planning_window.json", payload["planning_window"])
        merged_threads = {
            str(item.get("id")): item for item in existing_ledger if isinstance(item, dict) and item.get("id")
        }
        for item in materialize_foreshadowing_ledger(config, payload["foreshadowing_updates"], arcs):
            merged_threads[str(item["id"])] = item
        merged_thread_rows = list(merged_threads.values())
        write_json(root / "20_outline" / "foreshadowing_ledger.json", merged_thread_rows)
        creative_brief = read_json(root / "10_bible" / "creative_brief.json", {})
        story_engine = creative_brief.get("story_engine_contract") if isinstance(creative_brief, dict) else {}
        write_reader_promise_ledger(
            root,
            merge_planned_reader_promises(
                root,
                story_engine_contract=story_engine if isinstance(story_engine, dict) else {},
                foreshadowing_ledger=merged_thread_rows,
                estimated_chapters=compile_length_forecast(config.data["length"]).estimated_chapters,
            ),
        )
        stale_causal_simulations_if_basis_changed(root, basis_before)
        return
    if task_type == "chapter_direction":
        write_chapter_direction(root, payload)
        return
    if task_type == "outline_revision":
        from longform_engine.orchestration.pipeline import plan_chapter

        basis_before = current_basis_hashes(root)
        replacements = payload["replacements"]
        if "book_outline_markdown" in replacements:
            atomic_write_text(root / "20_outline" / "book_outline.md", str(replacements["book_outline_markdown"]).rstrip() + "\n")
        for key, filename in (
            ("story_arcs", "story_arcs.json"),
            ("volumes", "volumes.json"),
            ("chapter_plan", "chapter_plan.json"),
            ("planning_window", "planning_window.json"),
        ):
            if key in replacements:
                write_json(root / "20_outline" / filename, replacements[key])
        if "foreshadowing_ledger" in replacements:
            arcs = replacements.get("story_arcs", read_json(root / "20_outline" / "story_arcs.json", []))
            write_json(
                root / "20_outline" / "foreshadowing_ledger.json",
                materialize_foreshadowing_ledger(config, replacements["foreshadowing_ledger"], arcs),
            )
        materialized_threads = read_json(root / "20_outline" / "foreshadowing_ledger.json", [])
        creative_brief = read_json(root / "10_bible" / "creative_brief.json", {})
        story_engine = creative_brief.get("story_engine_contract") if isinstance(creative_brief, dict) else {}
        merged_promises = merge_planned_reader_promises(
            root,
            story_engine_contract=story_engine if isinstance(story_engine, dict) else {},
            foreshadowing_ledger=materialized_threads if isinstance(materialized_threads, list) else [],
            estimated_chapters=compile_length_forecast(config.data["length"]).estimated_chapters,
        )
        if "reader_promise_deferrals" in replacements:
            apply_planning_deferrals(
                merged_promises,
                values=replacements["reader_promise_deferrals"],
                chapter_number=int(payload["from_chapter"]),
                approved_by="human",
            )
        write_reader_promise_ledger(root, merged_promises)
        basis_changed = current_basis_hashes(root) != basis_before
        mark_overlapping_arc_simulations_stale(
            root,
            from_chapter=1 if basis_changed else int(payload["from_chapter"]),
            to_chapter=10**9 if basis_changed else int(payload["to_chapter"]),
        )
        truncate_editorial_pattern_registry(
            root,
            to_chapter=max(0, int(payload["from_chapter"]) - 1),
        )
        invalidate_outline_revision_tasks(
            root,
            chapter_numbers=payload["impact"]["stale_chapters"],
            artifact=root / "20_outline" / "revise_reports" / revision_report_name(payload),
        )
        mark_outline_revision_writing_tasks_stale(
            root,
            chapter_numbers=payload["impact"]["stale_chapters"],
        )
        state_path = root / "30_state" / "novel_state.json"
        state = read_json(state_path, {})
        stale = list(state.get("stale") or []) if isinstance(state, dict) else []
        for item in payload["impact"]["stale_artifacts"]:
            if item not in stale:
                stale.append(item)
        state["stale"] = stale
        state["stale_chapters"] = payload["impact"]["stale_chapters"]
        write_json(state_path, state)
        write_json(root / "20_outline" / "revise_reports" / revision_report_name(payload), payload)
        for chapter_number in range(int(payload["from_chapter"]), int(payload["to_chapter"]) + 1):
            card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
            if card_path.is_file():
                plan_chapter(config, chapter_number=chapter_number, overwrite=True)
        sync_database(config)
        return
    if task_type == "research_synthesis":
        path = root / "10_bible" / "research_canon.jsonl"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        lines = []
        for claim in payload["claims"]:
            lines.append(json.dumps({
                "schema": "research_canon_claim_v1",
                "synthesis_id": payload["synthesis_id"],
                **claim,
            }, ensure_ascii=False))
        prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
        atomic_write_text(path, prefix + "\n".join(lines) + "\n")
        return
    if task_type == "style_analysis":
        path = root / "10_bible" / "style_profiles" / "current_style_profile.json"
        current = read_json(path, {})
        current["schema"] = "combined_style_profile_v1"
        current["semantic_profile"] = payload["semantic_profile"]
        current["semantic_sources"] = payload["source_files"]
        current["semantic_source_hashes"] = payload["source_hashes"]
        write_json(path, current)
        return
    if task_type == "adaptation_analysis":
        write_json(root / "10_bible" / "style_profiles" / "adaptation_profile.json", payload)


def stale_causal_simulations_if_basis_changed(
    root: Path,
    basis_before: dict[str, str],
) -> tuple[str, ...]:
    """Materialize planning-basis invalidation instead of leaving approved-looking stale files."""

    if current_basis_hashes(root) == basis_before:
        return ()
    return mark_overlapping_arc_simulations_stale(
        root,
        from_chapter=1,
        to_chapter=10**9,
    )


def materialize_foreshadowing_ledger(
    config: ConfigDocument,
    threads: list[dict[str, Any]],
    story_arcs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add deterministic chapter projections while preserving arc-relative authority."""

    arc_by_id = {
        str(item.get("id")): item
        for item in story_arcs
        if isinstance(item, dict) and item.get("id")
    }
    estimated_chapters = compile_length_forecast(config.data["length"]).estimated_chapters
    materialized: list[dict[str, Any]] = []
    for thread in threads:
        item = dict(thread)
        plant = item.get("plant") if isinstance(item.get("plant"), dict) else {}
        payoff = item.get("payoff") if isinstance(item.get("payoff"), dict) else {}
        item["plant_chapter"] = project_arc_progress_to_chapter(
            arc_by_id.get(str(plant.get("arc_id")), {}),
            plant.get("progress_window"),
            estimated_chapters,
            endpoint="start",
        )
        item["payoff_window"] = [
            project_arc_progress_to_chapter(
                arc_by_id.get(str(payoff.get("arc_id")), {}),
                payoff.get("progress_window"),
                estimated_chapters,
                endpoint=endpoint,
            )
            for endpoint in ("start", "end")
        ]
        item["projection"] = {
            "schema": "foreshadow_chapter_projection_v1",
            "estimated_total_chapters": estimated_chapters,
            "authority": "derived_from_arc_progress",
        }
        materialized.append(item)
    return materialized


def project_arc_progress_to_chapter(
    arc: dict[str, Any],
    local_window: Any,
    estimated_chapters: int,
    *,
    endpoint: str,
) -> int:
    arc_window = arc.get("progress_window") if isinstance(arc, dict) else None
    if not valid_progress_window(arc_window) or not valid_progress_window(local_window):
        return 1
    local = float(local_window[0 if endpoint == "start" else 1])
    global_progress = float(arc_window[0]) + local * (float(arc_window[1]) - float(arc_window[0]))
    return max(1, min(estimated_chapters, round(global_progress * (estimated_chapters - 1)) + 1))


def write_book_design_targets(root: Path, payload: dict[str, Any]) -> None:
    creative_brief = dict(payload["creative_brief"])
    creative_brief["status"] = "confirmed"
    write_json(root / "10_bible" / "creative_brief.json", creative_brief)
    atomic_write_text(root / "10_bible" / "world.md", payload["world_markdown"].rstrip() + "\n")
    atomic_write_text(root / "10_bible" / "power_system.md", payload["power_system_markdown"].rstrip() + "\n")
    write_json(root / "10_bible" / "characters.json", payload["characters"])
    write_json(root / "10_bible" / "relationships.json", payload["relationships"])
    write_character_expression_profile(
        root,
        {
            "schema": CHARACTER_EXPRESSION_SCHEMA,
            "narrative_expression_profile": payload["narrative_expression_profile"],
            "character_expression_contracts": payload["character_expression_contracts"],
        },
    )
    for optional in ("factions", "locations"):
        if optional in payload:
            write_json(root / "10_bible" / f"{optional}.json", payload[optional])


def fanfiction_rights_declarations(root: Path) -> list[dict[str, Any]]:
    project = read_json(root / "project.json", {})
    if isinstance(project, dict):
        return list(project.get("fanfiction", {}).get("sources") or [])
    try:
        import yaml

        project_yaml = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        project_yaml = {}
    sources = project_yaml.get("fanfiction", {}).get("sources") if isinstance(project_yaml, dict) else []
    return [dict(item) for item in sources or [] if isinstance(item, dict)]


def append_creation_event(root: Path, event: str, payload: dict[str, Any]) -> None:
    path = root / "70_runtime" / "provenance" / "creation_events.jsonl"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    record = {
        "schema": "creation_provenance_event_v1",
        "event": event,
        "payload_hash": sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "stores_source_body": False,
    }
    prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
    atomic_write_text(path, prefix + json.dumps(record, ensure_ascii=False) + "\n")


def mark_project_intelligence_applied(root: Path, task_type: str, payload: dict[str, Any]) -> None:
    state_path = root / "30_state" / "novel_state.json"
    state = read_json(state_path, {})
    if not isinstance(state, dict):
        state = {}
    project_intelligence = state.get("project_intelligence")
    if not isinstance(project_intelligence, dict):
        project_intelligence = {}
    project_intelligence[task_type] = {
        "status": "applied",
        "candidate_hash": sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
    }
    state["project_intelligence"] = project_intelligence
    outline_marker = project_intelligence.get("outline_design")
    expression_ready, _expression_errors = character_expression_readiness(root)
    if (
        isinstance(outline_marker, dict)
        and outline_marker.get("status") == "applied"
        and expression_ready
    ):
        state["status"] = "project_ready"
    elif task_type == "outline_design":
        state["status"] = "project_designed"
    write_json(state_path, state)


def write_book_ideation_decision(root: Path, payload: dict[str, Any]) -> None:
    path = root / "10_bible" / "creative_decisions.json"
    current = read_json(path, {})
    if not isinstance(current, dict) or current.get("schema") != "book_ideation_decisions_v1":
        current = {
            "schema": "book_ideation_decisions_v1",
            "decisions": {},
            "rounds": [],
            "complete": False,
        }
    selection = payload["selection"]
    if selection["mode"] == "selected_option":
        selected = next(item for item in payload["options"] if item["id"] == selection["option_id"])
        answer = str(selected["proposal"]).strip()
    else:
        answer = str(selection["answer"]).strip()
    decisions = current.get("decisions")
    decisions = dict(decisions) if isinstance(decisions, dict) else {}
    decisions[str(payload["dimension"])] = answer
    rounds = current.get("rounds")
    rounds = list(rounds) if isinstance(rounds, list) else []
    rounds.append(
        {
            "round": int(payload["round"]),
            "dimension": str(payload["dimension"]),
            "question": str(payload["question"]),
            "selection_mode": str(selection["mode"]),
            "selected_option_id": str(selection["option_id"]),
            "answer": answer,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    complete = all(str(decisions.get(item) or "").strip() for item in BOOK_IDEATION_DIMENSIONS)
    canonical = {
        "schema": "book_ideation_decisions_v1",
        "dimensions": list(BOOK_IDEATION_DIMENSIONS),
        "decisions": decisions,
        "rounds": rounds,
        "complete": complete,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(path, canonical)
    state_path = root / "30_state" / "novel_state.json"
    state = read_json(state_path, {})
    state = state if isinstance(state, dict) else {}
    markers = state.get("project_intelligence")
    markers = dict(markers) if isinstance(markers, dict) else {}
    markers["book_ideation"] = {
        "status": "applied" if complete else "in_progress",
        "rounds": len(rounds),
        "next_dimension": "" if complete else next(
            item for item in BOOK_IDEATION_DIMENSIONS if not str(decisions.get(item) or "").strip()
        ),
        "candidate_hash": sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    if complete:
        next_type = "fanfiction_design" if state.get("creation_mode") == "fanfiction" else "book_design"
        next_marker = markers.get(next_type)
        if not isinstance(next_marker, dict) or next_marker.get("status") != "applied":
            markers[next_type] = {"status": "required"}
    state["project_intelligence"] = markers
    write_json(state_path, state)


def write_chapter_direction(root: Path, payload: dict[str, Any]) -> None:
    from longform_engine.chapter_contract import stamp_chapter_contract
    from longform_engine.orchestration.pipeline import upsert_chapter_plan, write_chapter_card_artifacts

    chapter_number = int(payload["chapter_number"])
    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    card = read_json(card_path, {})
    if not isinstance(card, dict):
        raise ValueError("Chapter card must be a JSON object.")
    selection = payload["selection"]
    selected = payload["selected_direction"]
    resolved = dict(selected)
    resolved.update(selection["user_adjustments"])
    card.update(
        {
            "chapter_duty": resolved["chapter_duty"],
            "immediate_desire": resolved["immediate_desire"],
            "opposition_force": resolved["opposition_force"],
            "dramatic_question": resolved["dramatic_question"],
            "conflict": resolved["conflict"],
            "key_failure": resolved["key_failure"],
            "irreversible_choice": resolved["irreversible_choice"],
            "irreversible_action": resolved["irreversible_choice"],
            "chapter_turn": resolved["chapter_turn"],
            "reveal_boundary": resolved["reveal_boundary"],
            "reader_gain": resolved["reader_gain"],
            "cost": resolved["cost"],
            "must_dramatize": resolved["must_dramatize"],
            "may_summarize": resolved["may_summarize"],
            "primary_story_engine": resolved["primary_story_engine"],
            "scene_carriers": resolved["scene_carriers"],
            "protected_story_outcomes": resolved["protected_story_outcomes"],
            "prohibited_drift": resolved["prohibited_drift"],
            "state_change_kind": resolved["state_change_kind"],
            "dramatic_method": resolved["dramatic_method"],
            "exposition_carrier": resolved["exposition_carrier"],
            "book_goal": resolved["book_goal"],
            "volume_goal": resolved["volume_goal"],
            "protagonist_goal": resolved["protagonist_goal"],
            "platform_promise": resolved["chapter_duty"],
            "scene_chain": resolved["scene_chain"],
            "featured_character_ids": resolved["featured_character_ids"],
            "scene_wants": resolved["cast_desires"],
            "dialogue_ownership": resolved["dialogue_ownership"],
            "embodiment_strategy": resolved["embodiment_plan"],
            "interiority_function": resolved["interiority_function"],
            "ending_mode": resolved["ending_mode"],
            "longline_impact": resolved["mainline_move"],
            "character_arc_move": resolved["character_arc_move"],
            "foreshadow_impact": resolved["foreshadow_move"],
            "relationship_impact": resolved["relationship_move"],
            "relationship_move": resolved["relationship_move"],
            "reader_promise_actions": resolved["reader_promise_actions"],
            "arc_simulation_ref": resolved["arc_simulation_ref"],
            "canon_refs": resolved["canon_refs"],
            "world_rule_refs": resolved["world_rule_refs"],
            "foreshadow_refs": resolved["foreshadow_refs"],
            "forbidden_reveals": resolved["forbidden_reveals"],
            "direction_risks": list(selected["main_risks"]),
            "direction_selection": {
                "status": "applied",
                "direction_id": selected["id"],
                "title": selected["title"],
                "trigger_reasons": list(payload["trigger_reasons"]),
                "candidate_hash": sha256(
                    json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "repetition_reason": str(selection.get("repetition_reason") or ""),
                "applied_at": datetime.now(timezone.utc).isoformat(),
            },
        }
    )
    for field in (
        "protected_canon_outcomes", "changed_scene_means", "canon_character_agency",
        "new_long_term_facts", "outline_revision_required",
    ):
        if field in resolved:
            card[field] = resolved[field]
    card.pop("chapter_contract_status", None)
    stamp_chapter_contract(card)
    write_chapter_card_artifacts(root, card)
    upsert_chapter_plan(root, card)


def revision_report_name(payload: dict[str, Any]) -> str:
    return f"agent_revision_ch{int(payload['from_chapter']):03d}-ch{int(payload['to_chapter']):03d}.json"


def character_review_report_name(scope: dict[str, Any]) -> str:
    return f"review_ch{int(scope.get('from_chapter') or 0):03d}-ch{int(scope.get('to_chapter') or 0):03d}.json"


def render_instruction(task_type: str, spec: dict[str, Any], scope: dict[str, Any], inputs: list[str], output: str) -> str:
    requirements = {
        "book_ideation": (
            "只推动当前维度的一个真实创作决定；给出二至三个有实质差异的选项与代价，"
            "记录用户明确决定，不替用户默选，也不顺带决定其他维度。"
        ),
        "fanfiction_canon": (
            "用来源命名空间 ID 和可回读 span 转述 canon 事实，覆盖人物、关系、规则、能力、术语、"
            "时间线与未解决问题；不保存连续原文，不自行判断授权状态。"
        ),
        "fanfiction_design": (
            "建立同人形态、分歧因果、人物声音、原创主线、原创贡献和保护揭露。已声明且有因果支持的"
            "分歧不是 OOC；联动作品必须说明命名空间、力量换算与规则冲突。"
        ),
        "book_design": (
            "明确读者承诺、核心卖点、世界规则、主角欲望与缺陷、长期冲突、升级方式和结局边界。"
            "必须建立 story_engine_contract_v1：读者幻想、可重复行动循环、成长循环、关系循环、长线问题、"
            "分阶段兑现、载体调色板和主题载体限制。每个重要人物都要有稳定 ID、目标、缺陷、关系与可观察的人物弧。"
        ),
        "character_expression_design": (
            "把人物设定转成可观察合同：感知偏向、决策习惯、语言层级、对话策略、情绪泄漏、"
            "身体反应、社会面具、私欲、矛盾与对照；示例只用于校准，不能当口头禅模板。"
        ),
        "character_expression_review": (
            "逐章检查声音匹配、对白可互换、人物工具化、具身存在、叙述者代替人物解释和说明式对白。"
            "问题结论必须引用 hash 绑定的精确 span；证据不足时明确 insufficient。"
        ),
        "outline_design": (
            "按正文字符预算全书故事弧和卷，只细化滚动窗口。每章说明故事弧、卷、章节职责、登场人物、"
            "主故事引擎、主要场景载体、状态变化、戏剧方法、人物欲望、关系变化和最多三个活跃分面；"
            "相同主题必须改变事件压力、承担者或载体。伏笔使用 arc_id 与进度窗口，不虚构固定终章数。"
        ),
        "arc_simulation": (
            "对声明窗口进行角色因果模拟：逐个写明主角目标、对手议程、主要人物私欲与拒绝点、知识边界、"
            "场外行动、资源/关系变化、碰撞点和逐章因果义务。模拟是规划约束，不是正文顺序模板或世界事实。"
        ),
        "outline_extension": (
            "只扩展声明的滚动章节范围，承接既有故事弧、人物、关系和承诺因果，不重复早期章节。"
            "伏笔继续使用故事弧进度窗口，应用前必须有人明确批准。"
        ),
        "outline_revision": (
            "明确修改目标、完整替换内容、依赖影响与保留项。stale 影响只能涉及声明范围内的现有文件，"
            "CLI 会根据真实依赖重新计算。"
        ),
        "chapter_direction": (
            "在方向选项下用 `### option:<stable_id> — 标题` 给出二至三个因果路径不同的方向，"
            "稳定 ID 在人工选择后不得改名；明确当下欲望、真实阻力、最早失败、不可逆选择、可见代价、"
            "chapter_turn，以及逐场行动、反应和离场状态；声明必须演出、可压缩过程、故事引擎、载体与状态变化。"
            "最近五章载体达到重复门槛时记录人工理由。同人必须保护原作结果、人物能动性与情绪归属；"
            "改变长期事实或保护结果必须要求 outline_revision。"
        ),
        "research_synthesis": (
            "每条 claim 都必须绑定声明来源的 hash 与 UTF-8 字符 span，证据必须与原文切片完全一致。"
        ),
        "adaptation_analysis": (
            "只保留抽象结构和技法，不引用、重构、跨字段拆分或轻改来源正文；CLI 会执行精确与 n-gram 检查。"
        ),
    }.get(task_type, "只使用声明来源，并严格遵守唯一输出协议。")
    protocol = output_protocol_for_task(task_type)
    output_rule = {
        DESIGN_DOCUMENT_SCHEMA: (
            "只写纯 Markdown，不写 YAML front matter、JSON sidecar 或 CLI 已知字段。必需标题："
            + "、".join(DESIGN_REQUIRED_HEADINGS.get(task_type, ()))
        ),
        CANONICAL_DELTA_SCHEMA: (
            "只写 canonical_delta_v1 JSON。changes 只保存有证据的领域增量，evidence 使用 JSON Pointer 映射；"
            "路径、hash、scope、角色和时间由 CLI 提供。"
        ),
        EVIDENCE_REVIEW_SCHEMA: (
            "只写 evidence_review_v2 JSON。coverage 每个维度包含 status、正文 evidence_ids 和所需 canonical_refs；"
            "checked 必须给出一至两个可回读正文证据，证据 ID 使用 source_ref@start:end；"
            "不要回填任务、路径、hash、角色、scope 或时间。"
        ),
    }[protocol]
    return "\n".join((
        f"# {task_type} Agent 工作单",
        "",
        f"Agent 输出协议：`{protocol}`",
        f"任务范围：`{json.dumps(scope, ensure_ascii=False)}`",
        f"唯一允许输出：`{output}`",
        "",
        "只读输入：",
        *(f"- `{item}`" for item in inputs),
        "",
        "校验要求：",
        f"- {requirements}",
        f"- {output_rule}",
        "",
        "不得直接写 Bible、outline、research canon、final、RAG、graph、TCS 或 SQLite。",
        "CLI 只在内存中规范化唯一输出，并在显式 apply 前完成验证。",
        "",
    ))


def intelligence_commands(
    task_type: str,
    *,
    candidate: str,
    range_args: str,
    input_args: str,
    requires_human: bool,
) -> tuple[str, str, str]:
    if output_protocol_for_task(task_type) == DESIGN_DOCUMENT_SCHEMA:
        return (
            f"longform-engine intelligence validate project.yaml --task-type {task_type} --file {candidate}",
            "longform-engine intelligence approve project.yaml "
            f"--task-type {task_type} --document {candidate} --approved-by human",
            f"longform-engine intelligence task project.yaml --task-type {task_type}{range_args}{input_args}",
        )
    if task_type == "fanfiction_canon":
        return (
            f"longform-engine fanfiction canon-validate project.yaml --file {candidate}",
            f"longform-engine fanfiction canon-apply project.yaml --file {candidate} --approved-by human",
            f"longform-engine fanfiction canon-task project.yaml{input_args}",
        )
    if task_type == "character_expression_review":
        return (
            f"longform-engine character audit-validate project.yaml --file {candidate}",
            f"longform-engine character audit-apply project.yaml --file {candidate}",
            f"longform-engine character audit-task project.yaml{range_args}",
        )
    approval = " --approved-by human" if requires_human else ""
    return (
        f"longform-engine intelligence validate project.yaml --task-type {task_type} --file {candidate}",
        f"longform-engine intelligence apply project.yaml --task-type {task_type} --file {candidate}{approval}",
        f"longform-engine intelligence task project.yaml --task-type {task_type}{range_args}{input_args}",
    )


def fanfiction_status(config: ConfigDocument) -> dict[str, Any]:
    root = resolve_project_root(config)
    state = read_json(root / "30_state" / "novel_state.json", {})
    markers = state.get("project_intelligence") if isinstance(state, dict) else {}
    if not isinstance(markers, dict):
        markers = {}
    sources = config.data.get("fanfiction", {}).get("sources") or []
    rights_warnings = [
        {
            "source_id": str(source.get("source_id") or ""),
            "rights_status": str(source.get("rights_status") or "unverified"),
            "commercial_intent": bool(source.get("commercial_intent")),
            "blocking": False,
        }
        for source in sources
        if isinstance(source, dict)
    ]
    readiness = assess_project_readiness(config)
    return {
        "schema": "fanfiction_status_v1",
        "creation_mode": str(config.data.get("creation", {}).get("mode") or "original"),
        "continuity_mode": str(config.data.get("fanfiction", {}).get("continuity_mode") or ""),
        "source_count": len(sources),
        "canon_status": str((markers.get("fanfiction_canon") or {}).get("status") or "not_applied"),
        "design_status": str((markers.get("fanfiction_design") or {}).get("status") or "not_applied"),
        "rights_advisory_only": True,
        "rights_warnings": rights_warnings,
        "ready": readiness.ready,
        "next_task_type": readiness.required_task_type,
    }


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def character_ids_from_root(root: Path) -> list[str]:
    characters = read_json(root / "10_bible" / "characters.json", [])
    if not isinstance(characters, list):
        return []
    return [
        str(item.get("id"))
        for item in characters
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def relative(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve().relative_to(root.resolve()).as_posix()
