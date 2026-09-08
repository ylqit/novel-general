"""Candidate/validate/explicit-apply workflows for project-level intelligence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
    output_protocol_for_task,
    parse_design_document,
    validate_canonical_delta,
    validate_evidence_review,
    validate_review_evidence_for_source,
)
from longform_engine.agent_tasks import (
    agent_task_lifecycle_mutation_paths,
    build_manifest,
    is_canonical_output,
    list_manifests,
    load_manifest,
    manifest_chapter_number,
    manifest_commands,
    manifest_input_records,
    manifest_input_paths,
    manifest_output,
    mark_tasks_for_output,
    mark_tasks_for_chapter_type,
    validate_current_task_result,
    validate_manifest_strict,
    write_manifest,
)
from longform_engine.arc_simulation import (
    SIMULATION_DIR,
    arc_simulation_path,
    current_basis_hashes,
    mark_overlapping_arc_simulations_stale,
    permitted_arc_simulation_ranges,
    validate_arc_causal_simulation,
    write_arc_causal_simulation,
)
from longform_engine.character_expression import (
    CHARACTER_CONTRACT_LIST_FIELDS,
    CHARACTER_CONTRACT_STRING_FIELDS,
    CHARACTER_EXPRESSION_SCHEMA,
    CHARACTER_REVIEW_SCHEMA,
    EXPRESSION_PROFILE_FIELDS,
    character_expression_readiness,
    validate_character_expression_profile,
    write_character_expression_profile,
)
from longform_engine.config import ConfigDocument
from longform_engine.db import sync_fanfiction_source_canon
from longform_engine import fanfiction_contracts
from longform_engine.fanfiction_creative_requirements import (
    CREATIVE_CONTRACT_VERSION, ROUTE_FAMILIES, compile_fanfiction_creative_requirements,
)
from longform_engine.fanfiction_sources import (
    CANON_SCHEMA as FANFICTION_CANON_SCHEMA,
    fanfiction_canon_input_files,
    fanfiction_source_readiness,
    source_upgrade_status,
)
from longform_engine.future_knowledge_provenance import (
    build_future_knowledge_pin,
    future_knowledge_provenance_archive_path,
    pin_applicability_from_claims,
    provenance_pin_registry_path,
    seal_future_knowledge_provenance_archive,
    upsert_future_knowledge_pin,
)
from longform_engine.future_knowledge_current import (
    current_applied_future_knowledge_target,
    future_knowledge_approved_target,
    future_knowledge_reassessment_task_artifacts,
    validate_future_knowledge_reassessment,
)
from longform_engine.lengths import compile_length_forecast
from longform_engine.prompting import estimate_text_units, resolve_context_budget_contract
from longform_engine.story_profiles import BUILTIN_MARKET_IDS, compile_story_profile
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import list_finalized_chapter_files, manuscript_chapter_path
from longform_engine.source_materialization import materialize_fanfiction_source_canon
from longform_engine.semantic_protocols import (
    SEMANTIC_DOCUMENT_SCHEMA,
    approved_semantic_document,
    build_human_decision,
    canonical_json_hash as semantic_json_hash,
    seal_semantic_document,
    validate_semantic_document,
)


# Outline candidates still reject these pre-v0.8 aliases, but the rule belongs
# to outline-candidate validation rather than the formal v0.10 chapter contract.
REMOVED_CHAPTER_PLAN_ALIAS_FIELDS = frozenset(
    {
        "duty",
        "information",
        "information_release",
        "reader_payoff",
        "hook",
        "hook_mode",
        "plot_obligation",
        "irreversible_action",
        "dramatic_freedom",
    }
)


INTELLIGENCE_TASK_TYPES = (
    "fanfiction_canon",
    "fanfiction_story_engine",
    "fanfiction_design",
    "fanfiction_design_review",
    "fanfiction_future_knowledge_reassessment",
    "book_ideation",
    "book_design",
    "character_expression_design",
    "character_expression_review",
    "arc_simulation",
    "research_synthesis",
    "style_analysis",
    "adaptation_analysis",
    "character_interpretation",
    "story_architecture_design",
    "chapter_semantic_planning",
    "draft_semantic_review",
    "prose_revision_review",
    "reader_feedback_analysis",
    "source_discovery_planning",
    "source_candidate_triage",
)

DESIGN_INTELLIGENCE_TASK_TYPES = tuple(
    task_type
    for task_type in INTELLIGENCE_TASK_TYPES
    if output_protocol_for_task(task_type) == DESIGN_DOCUMENT_SCHEMA
)

TASK_SPECS: dict[str, dict[str, Any]] = {
    "character_interpretation": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (),
        "defaults": (
            "project.yaml",
            "10_bible/characters.json",
            "10_bible/relationships.json",
            "10_bible/fanfiction/source_canon.json",
        ),
    },
    "story_architecture_design": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (),
        "defaults": (
            "project.yaml",
            "10_bible/creative_decisions.json",
            "10_bible/fanfiction/fanfiction_bible.json",
        ),
    },
    "chapter_semantic_planning": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "chapter",
        "human": True,
        "targets": (),
        "defaults": (),
    },
    "draft_semantic_review": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "chapter",
        "human": False,
        "targets": (),
        "defaults": (),
    },
    "prose_revision_review": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "chapter",
        "human": False,
        "targets": (),
        "defaults": (),
    },
    "reader_feedback_analysis": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (),
        "defaults": (),
    },
    "source_discovery_planning": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (),
        "defaults": (),
    },
    "source_candidate_triage": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": False,
        "targets": (),
        "defaults": (),
    },
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
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/fanfiction/source_canon.json",
            "30_state/novel_state.json",
            "30_state/story_graph.json",
            "60_rag/chunks/fanfiction_source_canon.json",
            "70_runtime/provenance/creation_events.jsonl",
        ),
        "defaults": (),
    },
    "fanfiction_story_engine": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/fanfiction/story_engine.json",
            "30_state/novel_state.json",
            "70_runtime/provenance/creation_events.jsonl",
        ),
        "defaults": (),
    },
    "fanfiction_design": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": True,
        "targets": (
            "10_bible/fanfiction/fanfiction_bible.json",
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
    "fanfiction_design_review": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "project",
        "human": False,
        "targets": (),
        "defaults": (),
    },
    "fanfiction_future_knowledge_reassessment": {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "scope": "chapter",
        "human": True,
        # The approved document owns one payload-derived file, not the directory.
        # apply_targets() adds that exact semantic_task_target after validation.
        "targets": (),
        "defaults": (),
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
            "20_outline/volume_skeletons.json",
            "20_outline/rolling_window.json",
            "30_state/reader_promise_ledger.json",
            "30_state/character_state.json",
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

FANFICTION_CURRENT_CHAIN_TASK_TYPES = frozenset(
    {
        "story_architecture_design",
        "chapter_semantic_planning",
        "draft_semantic_review",
        "prose_revision_review",
        "reader_feedback_analysis",
        "book_design",
        "character_expression_design",
        "character_expression_review",
        "arc_simulation",
        "fanfiction_future_knowledge_reassessment",
    }
)

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
    rebuild: bool = False,
) -> IntelligenceTaskResult:
    root = resolve_project_root(config)
    spec = require_spec(task_type)
    if rebuild and task_type != "book_ideation":
        raise ValueError("Explicit ideation rebuilding only applies to book_ideation.")
    scope = task_scope(
        spec,
        chapter_number=chapter_number,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
    )
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES
    ):
        try:
            current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                config, root
            )
        except fanfiction_contracts.FanfictionContractError as exc:
            raise ValueError(str(exc)) from exc
    provided_inputs = tuple(input_files)
    requested_inputs: Iterable[str | Path] = provided_inputs
    if task_type == "fanfiction_canon" and not provided_inputs:
        requested_inputs = fanfiction_canon_input_files(config)
    inputs = normalize_inputs(
        root,
        requested_inputs
        or intelligence_default_inputs(
            root,
            task_type,
            spec,
            scope,
            current_fanfiction=current_fanfiction,
        ),
    )
    if task_type == "fanfiction_canon":
        allowed_root = (root / "50_workbench" / "同人原著资料").resolve()
        for path in inputs:
            try:
                path.resolve().relative_to(allowed_root)
            except ValueError as exc:
                raise ValueError(
                    "fanfiction_canon inputs must be approved extraction artifacts under "
                    "50_workbench/同人原著资料/"
                ) from exc
    if task_type == "fanfiction_story_engine":
        inputs = [write_fanfiction_story_engine_context(config, root)]
    if task_type == "fanfiction_design":
        inputs = [write_fanfiction_design_context(config, root)]
    if task_type == "fanfiction_design_review":
        if len(inputs) != 1:
            raise ValueError("fanfiction_design_review requires exactly one route candidate input")
        route_candidate = inputs[0]
        route_validation = validate_intelligence_candidate(
            config,
            task_type="fanfiction_design",
            file_path=route_candidate,
        )
        if not route_validation.ok:
            raise ValueError(
                "fanfiction_design_review requires a validated route candidate: "
                + "; ".join(route_validation.errors)
            )
        try:
            current_engine = (
                fanfiction_contracts.load_current_fanfiction_story_engine_documents(
                    config, root
                )
            )
        except fanfiction_contracts.FanfictionContractError as exc:
            raise ValueError(str(exc)) from exc
        inputs = [
            route_candidate,
            current_engine.paths["story_engine"],
            current_engine.paths["source_canon"],
        ]
        requirements_path = root / "50_workbench" / "intelligence_context" / "fanfiction_creative_review.project.context.json"
        write_json(requirements_path, {
            "schema": "fanfiction_creative_review_context_v1",
            "creative_requirements": compile_fanfiction_creative_requirements(
                current_engine.story_engine["extensions"]["continuity_mode"],
                current_engine.story_engine["extensions"]["route_family"],
            ),
            "coverage_shape": {"status": "checked|insufficient|contradicted", "reason": "independent reasoning", "basis_claim_ids": []},
            "instruction": "extensions.creative_coverage must cover every independent_review_focus with exact current route/engine claims; insufficient or contradicted cannot pass.",
        })
        inputs.append(requirements_path)
    if task_type == "fanfiction_future_knowledge_reassessment":
        if len(inputs) != 3:
            raise ValueError(
                "fanfiction_future_knowledge_reassessment requires workflow, context bundle, "
                "and event ledger inputs"
            )
    if task_type == "chapter_semantic_planning":
        inputs = [write_chapter_semantic_planning_context(config, root, int(scope["chapter_number"]))]
    if task_type in {"draft_semantic_review", "prose_revision_review"} and not inputs:
        chapter_number = int(scope["chapter_number"])
        draft = manuscript_chapter_path(root, chapter_number, lane="draft")
        if not draft.is_file():
            raise ValueError(f"{task_type} requires the current ch{chapter_number:03d} draft")
        inputs = [draft]
    if task_type in {
        "fanfiction_canon",
        "research_synthesis",
        "style_analysis",
        "adaptation_analysis",
        "reader_feedback_analysis",
        "source_discovery_planning",
        "source_candidate_triage",
    } and not inputs:
        raise ValueError(f"{task_type} requires at least one --input file.")
    if task_type.startswith("fanfiction_") and str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        raise ValueError(f"{task_type} requires creation.mode=fanfiction.")
    if current_fanfiction is not None and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES:
        for path in current_fanfiction.paths.values():
            if path not in inputs:
                inputs.append(path)

    if str(config.data.get("creation", {}).get("mode") or "") == "fanfiction" and task_type in {"book_design", "book_ideation", "arc_simulation", "story_semantic_planning", "chapter_semantic_planning"}:
        requirements_path = root / "50_workbench/intelligence_context" / f"{task_type}.creative_requirements.context.json"
        if current_fanfiction is not None:
            selected_routes = [current_fanfiction.story_engine["extensions"]["route_family"]]
        else:
            selected_routes = sorted(ROUTE_FAMILIES)
        write_json(requirements_path, {"schema": "fanfiction_creative_requirements_context_v1",
                   "requirements_by_route": {route: compile_fanfiction_creative_requirements(config.data["fanfiction"]["continuity_mode"], route) for route in selected_routes},
                   "instruction": "按人类批准的叙事承担方式使用对应要求；开书、人物变化和大纲不得重新强制改命、升级或独立原创主线。未选择路线时只讨论选项。"})
        inputs.append(requirements_path)

    token = scope_token(scope)
    round_number = next_book_ideation_round(root) if task_type == "book_ideation" else 0
    base = (
        f"{task_type}.{token}.round{round_number:02d}"
        if round_number
        else f"{task_type}.{token}"
    )
    ideation_predecessors: list[dict[str, Any]] = []
    ideation_task_id = ""
    if round_number:
        prefix = f"book_ideation:project:round{round_number:02d}:"
        ideation_predecessors = [item for item in list_manifests(root) if item["task_id"].startswith(prefix)]
        for item in reversed(ideation_predecessors):
            prior = load_manifest(root, item["task_id"])
            output = manifest_output(prior)["path"]
            if (not rebuild and item["status"] in {"awaiting_agent", "submitted", "validated", "approved"}
                    and Path(output).name.startswith(base + ".") and validate_manifest_strict(root, prior).ok):
                instructions = [value for value in manifest_input_paths(prior) if value.startswith("50_workbench/intelligence_tasks/")]
                if len(instructions) == 1:
                    return IntelligenceTaskResult(task_type, item["task_id"], item["manifest_file"], instructions[0],
                        output, f"longform-engine agent-task brief project.yaml {item['task_id']}")
        attempt = len(ideation_predecessors) + 1
        ideation_task_id = prefix + (f"attempt{attempt:02d}:" if attempt > 1 else "") + "v5"
        if attempt > 1:
            base += f".attempt{attempt:02d}"
    if task_type == "fanfiction_design_review":
        base += "." + sha256(inputs[0].read_bytes()).hexdigest()[:12]
    future_task_artifacts: dict[str, Any] | None = None
    if task_type == "fanfiction_future_knowledge_reassessment":
        future_task_artifacts = future_knowledge_reassessment_task_artifacts(
            root,
            chapter_number=int(scope["chapter_number"]),
            workflow_sha256=sha256(inputs[0].read_bytes()).hexdigest(),
        )
        base = str(future_task_artifacts["base"])
    instruction = (
        future_task_artifacts["instruction"]
        if future_task_artifacts is not None
        else root / "50_workbench" / "intelligence_tasks" / f"{base}.md"
    )
    candidate_base = base
    output_protocol = output_protocol_for_task(task_type)
    document_requires_human = output_protocol == DESIGN_DOCUMENT_SCHEMA
    candidate_suffix = ".candidate.md" if output_protocol == DESIGN_DOCUMENT_SCHEMA else ".candidate.json"
    candidate = (
        future_task_artifacts["candidate"]
        if future_task_artifacts is not None
        else root
        / "50_workbench"
        / "intelligence_candidates"
        / f"{candidate_base}{candidate_suffix}"
    )
    manifest_file = (
        future_task_artifacts["manifest"]
        if future_task_artifacts is not None
        else root / "50_workbench" / "agent_tasks" / f"{base}.manifest.json"
    )
    input_rel = [relative(root, path) for path in inputs]
    instruction_context = dict(scope)
    if task_type == "book_ideation":
        instruction_context.update(
            {
                "round": round_number,
                "dimension": next_book_ideation_dimension(root),
            }
        )
    atomic_write_text(
        instruction,
        render_instruction(task_type, spec, instruction_context, input_rel, relative(root, candidate)),
    )
    input_rel.append(relative(root, instruction))

    range_args = scope_command_args(scope)
    input_args = "".join(f" --input {path}" for path in input_rel if path != relative(root, instruction))
    validate_command, apply_command, failure_command = intelligence_commands(
        task_type,
        candidate=relative(root, candidate),
        range_args=range_args,
        input_args=input_args,
        input_paths=[relative(root, path) for path in inputs],
        requires_human=bool(spec["human"]) or document_requires_human,
    )
    if round_number:
        failure_command += " --rebuild"
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
        requires_human_apply=(
            bool(spec["human"])
            or document_requires_human
            or task_type == "fanfiction_design_review"
        ),
        context_policy={
            "required_files": [instruction],
            "optional_files": inputs,
            "compiled_brief": instruction,
            "selection_report": instruction,
        },
        task_id=(
            ideation_task_id
            if task_type == "book_ideation"
            else (
                f"fanfiction_design_review:project:{sha256(inputs[0].read_bytes()).hexdigest()[:12]}:v5"
                if task_type == "fanfiction_design_review"
                else (
                    str(future_task_artifacts["task_id"])
                    if future_task_artifacts is not None
                    else None
                )
            )
        ),
    )
    written = write_manifest(root, manifest, manifest_file, supersedes_task_ids=[item["task_id"]
        for item in ideation_predecessors if item["status"] not in {"applied", "superseded", "rolled_back"}])
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
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None,
) -> IntelligenceValidationResult:
    root = resolve_project_root(config)
    spec = require_spec(task_type)
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES
    ):
        if current_fanfiction is None:
            try:
                current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                    config, root
                )
            except fanfiction_contracts.FanfictionContractError as exc:
                raise ValueError(str(exc)) from exc
    candidate = resolve_candidate(root, file_path)
    errors: list[str] = []
    manifest = manifest_for_output(root, task_type, candidate)
    if manifest is None:
        errors.append("candidate is not declared by an active AgentTaskManifest.")
    else:
        if current_fanfiction is not None and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES:
            _require_current_fanfiction_task_provenance(
                root,
                manifest,
                current_fanfiction,
                label="design source task",
            )
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
    if ok and task_type == "fanfiction_story_engine":
        next_command = (
            "longform-engine fanfiction story-engine-apply project.yaml "
            f"--file {relative(root, candidate)} --approved-by human"
        )
    elif ok and task_type == "fanfiction_design":
        next_command = (
            "longform-engine fanfiction design-review-task project.yaml "
            f"--file {relative(root, candidate)}"
        )
    elif ok and task_type == "fanfiction_design_review":
        route_path = fanfiction_review_route_path(root, manifest)
        next_command = (
            "longform-engine fanfiction design-apply project.yaml "
            f"--file {relative(root, route_path)} --review {relative(root, candidate)} "
            "--approved-by human"
        )
    elif ok and protocol == DESIGN_DOCUMENT_SCHEMA:
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
    review_path: str | Path | None = None,
) -> IntelligenceApplyResult:
    root = resolve_project_root(config)
    spec = require_spec(task_type)
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES
    ):
        try:
            current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                config, root
            )
        except fanfiction_contracts.FanfictionContractError as exc:
            raise ValueError(str(exc)) from exc
    if output_protocol_for_task(task_type) == DESIGN_DOCUMENT_SCHEMA:
        raise ValueError(
            "design_document_v1 cannot be applied directly; approve the Markdown, compile a "
            "canonical_delta_v1, then apply with --document and --delta."
        )
    candidate = resolve_candidate(root, file_path)
    if task_type == "fanfiction_future_knowledge_reassessment":
        applied_target = current_applied_future_knowledge_target(config, candidate)
        if applied_target is not None:
            if approved_by != "human":
                raise ValueError(
                    "fanfiction_future_knowledge_reassessment apply requires --approved-by human."
                )
            return IntelligenceApplyResult(
                task_type=task_type,
                status="applied",
                candidate_file=relative(root, candidate),
                touched_paths=(relative(root, applied_target),),
                transaction_report="",
                next_command="longform-engine production next project.yaml",
            )
    validation = validate_intelligence_candidate(
        config,
        task_type=task_type,
        file_path=candidate,
        current_fanfiction=current_fanfiction,
    )
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
    source_paths = [
        candidate,
        *(current_fanfiction.paths.values() if current_fanfiction is not None else ()),
    ]
    review_manifest: dict[str, Any] | None = None
    review_candidate: Path | None = None
    approved_review_payload: dict[str, Any] | None = None
    reviewed_route_payload: dict[str, Any] | None = None
    if task_type == "fanfiction_design":
        if approved_by != "human":
            raise ValueError("fanfiction_design apply requires --approved-by human.")
        if review_path is None:
            raise ValueError(
                "fanfiction_design cannot be applied without an independent current review; "
                "run fanfiction design-review-task and pass --review FILE."
            )
        review_candidate = resolve_candidate(root, review_path)
        review_validation = validate_intelligence_candidate(
            config,
            task_type="fanfiction_design_review",
            file_path=review_candidate,
        )
        if not review_validation.ok:
            raise ValueError(
                "Independent fanfiction design review is invalid: "
                + "; ".join(review_validation.errors)
            )
        review_manifest = manifest_for_output(root, "fanfiction_design_review", review_candidate)
        review_errors: list[str] = []
        review_payload = load_candidate(
            config,
            root,
            review_candidate,
            review_errors,
            task_type="fanfiction_design_review",
            spec=TASK_SPECS["fanfiction_design_review"],
            manifest=review_manifest,
        )
        if review_payload is None or review_errors:
            raise ValueError(
                "Independent review could not be reloaded: " + "; ".join(review_errors)
            )
        if str(review_payload.get("extensions", {}).get("verdict") or "") != "pass":
            raise ValueError("fanfiction_design requires an independent review verdict of pass.")
        reviewed_route_payload = seal_semantic_document(payload)
        reviewed_route_bytes = (
            json.dumps(reviewed_route_payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        review_payload = json.loads(json.dumps(review_payload, ensure_ascii=False))
        review_payload["extensions"]["review_target_path"] = relative(root, candidate)
        review_payload["extensions"]["review_target_sha256"] = sha256(
            reviewed_route_bytes
        ).hexdigest()
        review_document = seal_semantic_document(review_payload)
        review_envelope = review_document["artifact"]
        review_decision = build_human_decision(
            decision_id="decision_"
            + semantic_json_hash(
                {
                    "target": review_envelope["artifact_id"],
                    "hash": review_envelope["content_sha256"],
                }
            )[:24],
            target_id=str(review_envelope["artifact_id"]),
            target_sha256=str(review_envelope["content_sha256"]),
            decision="approve",
            decided_by="human",
            reason="人工批准独立同人路线复核与其固定输入。",
            scope=dict(review_envelope.get("scope") or {"kind": "project"}),
        )
        approved_review_payload = approved_semantic_document(
            review_document,
            decision=review_decision,
        )
        approved_review_bytes = (
            json.dumps(approved_review_payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        payload = json.loads(json.dumps(payload, ensure_ascii=False))
        payload["extensions"]["independent_review"] = {
            "review_path": relative(root, review_candidate),
            "review_sha256": sha256(approved_review_bytes).hexdigest(),
            "review_artifact_id": str(
                approved_review_payload.get("artifact", {}).get("artifact_id") or ""
            ),
            "reviewer_role": "fanfiction_route_reviewer",
            "verdict": "pass",
            "reviewed_route_projection_sha256": (
                fanfiction_contracts.fanfiction_route_review_projection_sha256(
                    reviewed_route_payload
                )
            ),
        }
        payload = seal_semantic_document(payload)
        source_paths.append(review_candidate)
    historical = route_semantic_change_to_revision_branch(
        config,
        root,
        task_type=task_type,
        payload=payload,
        candidate=candidate,
    )
    if historical is not None:
        return historical
    scope = manifest or {}
    manifest_scope = scope.get("scope") if isinstance(scope.get("scope"), dict) else {}
    touched = apply_targets(root, task_type, payload, scope=manifest_scope)
    changed_claim_ids = fanfiction_semantic_changed_claim_ids(root, task_type, payload)
    stale_dependents = fanfiction_semantic_dependency_paths(
        root,
        task_type=task_type,
        changed_claim_ids=changed_claim_ids,
    )
    stale_registry = root / "30_state" / "stale_artifacts.json"
    if stale_dependents:
        touched.append(stale_registry)
        touched = list(dict.fromkeys(touched))
    if review_candidate is not None and approved_review_payload is not None:
        touched.append(review_candidate)
        touched = list(dict.fromkeys(touched))
    if reviewed_route_payload is not None:
        touched.append(candidate)
        touched = list(dict.fromkeys(touched))
    task_chapter = manifest_chapter_number(scope)
    future_pin_inputs: dict[str, Path] | None = None
    future_manifest_path: Path | None = None
    future_workflow_path: Path | None = None
    if task_type == "fanfiction_future_knowledge_reassessment":
        workflow_matches: list[Path] = []
        for input_record in manifest_input_records(scope):
            input_path = root / str(input_record.get("path") or "")
            input_payload = read_json(input_path, {}) if input_path.is_file() else {}
            if (
                isinstance(input_payload, dict)
                and input_payload.get("workflow_kind")
                == "fanfiction_future_knowledge_impact"
            ):
                workflow_matches.append(input_path)
        if len(workflow_matches) != 1:
            raise ValueError(
                "future knowledge apply requires exactly one engine-owned workflow input"
            )
        future_workflow_path = workflow_matches[0]
        owned = future_knowledge_reassessment_task_artifacts(
            root,
            chapter_number=task_chapter,
            workflow_sha256=sha256(future_workflow_path.read_bytes()).hexdigest(),
        )
        future_manifest_path = Path(owned["manifest"])
        if (
            scope.get("task_id") != owned["task_id"]
            or Path(owned["candidate"]).resolve() != candidate.resolve()
            or read_json(future_manifest_path, {}) != scope
        ):
            raise ValueError("future knowledge task identity is not engine-owned")
        workflow = read_json(future_workflow_path, {})
        workflow_inputs = {
            str(item.get("kind") or ""): root / str(item.get("path") or "")
            for item in workflow.get("inputs") or []
            if isinstance(item, dict)
        }
        if set(workflow_inputs) != {
            "fanfiction_context_bundle",
            "narrative_event_ledger",
        }:
            raise ValueError("future knowledge workflow input set is invalid")
        future_pin_inputs = {
            "workflow": future_workflow_path,
            "fanfiction_context_bundle": workflow_inputs["fanfiction_context_bundle"],
            "narrative_event_ledger": workflow_inputs["narrative_event_ledger"],
            "final_chapter": root
            / "40_manuscript"
            / "final"
            / f"ch{task_chapter:03d}.md",
            "semantic_ledger": root
            / "30_state"
            / "semantic_ledger"
            / f"ch{task_chapter:03d}.json",
            "task_manifest": future_manifest_path,
            "task_instruction": Path(owned["instruction"]),
            "candidate": candidate,
        }
        source_paths.extend(future_pin_inputs.values())
        touched.extend(
            [
                provenance_pin_registry_path(root),
                future_knowledge_provenance_archive_path(
                    root,
                    str(
                        workflow.get("extensions", {})
                        .get("trigger", {})
                        .get("trigger_id", "")
                    ),
                ),
                *agent_task_lifecycle_mutation_paths(root),
            ]
        )
        touched = list(dict.fromkeys(touched))
    with apply_transaction(
        root,
        command=f"intelligence apply {task_type}",
        chapter_number=task_chapter or None,
        source_paths=tuple(source_paths),
        touched_paths=tuple(touched),
        metadata={
            "task_type": task_type,
            "task_id": scope.get("task_id", ""),
            "approved_by": approved_by or "explicit_cli",
            "requires_human_apply": bool(spec["human"]),
            **(
                {
                    "fanfiction_chain": (
                        fanfiction_contracts.current_fanfiction_chain_binding(
                            root, current_fanfiction
                        )
                    )
                }
                if current_fanfiction is not None
                else {}
            ),
        },
    ) as transaction:
        if reviewed_route_payload is not None:
            write_json(candidate, reviewed_route_payload)
        if review_candidate is not None and approved_review_payload is not None:
            write_json(review_candidate, approved_review_payload)
        write_targets(
            config,
            root,
            task_type,
            payload,
            scope=manifest_scope,
            current_fanfiction=current_fanfiction,
        )
        if (
            task_type == "fanfiction_future_knowledge_reassessment"
            and future_pin_inputs is not None
            and future_manifest_path is not None
            and future_workflow_path is not None
        ):
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
            workflow = read_json(future_workflow_path, {})
            trigger = workflow.get("extensions", {}).get("trigger")
            if not isinstance(trigger, dict):
                raise ValueError("future knowledge workflow trigger is invalid")
            from_chapter, to_chapter = pin_applicability_from_claims(
                item for item in payload.get("claims") or [] if isinstance(item, dict)
            )
            approved_target = semantic_task_target(root, task_type, payload)
            base_pin = build_future_knowledge_pin(
                root,
                trigger_id=str(trigger.get("trigger_id") or ""),
                task_id=str(scope.get("task_id") or ""),
                chapter_number=task_chapter,
                from_chapter=from_chapter,
                to_chapter=to_chapter,
                approved_path=approved_target,
                evidence_paths=future_pin_inputs,
            )
            applied_task_projections = [
                item
                for item in list_manifests(root)
                if item.get("task_id") == scope.get("task_id")
            ]
            if len(applied_task_projections) != 1:
                raise ValueError(
                    "future knowledge applied task projection is not unique"
                )
            pin = seal_future_knowledge_provenance_archive(
                root,
                base_pin,
                task_projection=applied_task_projections[0],
            )
            upsert_future_knowledge_pin(root, pin)
        if stale_dependents:
            mark_fanfiction_semantic_dependents_stale(
                root,
                task_type=task_type,
                changed_claim_ids=changed_claim_ids,
                artifact_paths=stale_dependents,
            )
    if task_type != "fanfiction_future_knowledge_reassessment":
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
    if review_candidate is not None and review_manifest is not None:
        mark_tasks_for_chapter_type(
            root,
            chapter_number=0,
            task_types=("fanfiction_design_review",),
            to_status="applied",
            command="fanfiction design-apply",
            artifact=review_candidate,
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


def route_semantic_change_to_revision_branch(
    config: ConfigDocument,
    root: Path,
    *,
    task_type: str,
    payload: dict[str, Any],
    candidate: Path,
) -> IntelligenceApplyResult | None:
    """Prevent an approved engine/route edit from silently changing finalized chapters."""

    canonical_relatives = {
        "fanfiction_story_engine": "10_bible/fanfiction/story_engine.json",
        "fanfiction_design": "10_bible/fanfiction/fanfiction_bible.json",
    }
    relative_target = canonical_relatives.get(task_type)
    if relative_target is None:
        return None
    target = root / relative_target
    old = read_json(target, {})
    if not isinstance(old, dict) or not target.is_file():
        return None
    changed_ids = fanfiction_semantic_changed_claim_ids(root, task_type, payload)
    if not changed_ids:
        return None
    finalized = dict(list_finalized_chapter_files(root))
    affected: list[int] = []
    for chapter_number in sorted(finalized):
        bundle = read_json(
            root / "50_workbench" / "fanfiction_context" / f"ch{chapter_number:03d}.json",
            {},
        )
        included = set(bundle.get("included_claim_ids") or []) if isinstance(bundle, dict) else set()
        if included & changed_ids:
            affected.append(chapter_number)
    if not affected:
        return None
    from longform_engine.revision import create_versioned_revision_branch

    branch = create_versioned_revision_branch(
        config,
        from_chapter=min(affected),
        to_chapter=max(finalized),
        reason=(
            f"{task_type} approved candidate changes finalized-chapter dependencies: "
            + ", ".join(sorted(changed_ids))
        ),
        created_by="human",
    )
    return IntelligenceApplyResult(
        task_type=task_type,
        status="routed_to_revision_branch_v2",
        candidate_file=relative(root, candidate),
        touched_paths=(branch.branch_file,),
        transaction_report="",
        next_command="longform-engine status project.yaml",
    )


def fanfiction_semantic_changed_claim_ids(
    root: Path,
    task_type: str,
    payload: dict[str, Any],
) -> set[str]:
    target_relatives = {
        "fanfiction_story_engine": "10_bible/fanfiction/story_engine.json",
        "fanfiction_design": "10_bible/fanfiction/fanfiction_bible.json",
    }
    target_relative = target_relatives.get(task_type)
    if target_relative is None:
        return set()
    target = root / target_relative
    old = read_json(target, {})
    if not target.is_file() or not isinstance(old, dict):
        return set()
    old_claims = {
        str(item.get("claim_id") or ""): semantic_json_hash(item)
        for item in old.get("claims") or []
        if isinstance(item, dict) and item.get("claim_id")
    }
    new_claims = {
        str(item.get("claim_id") or ""): semantic_json_hash(item)
        for item in payload.get("claims") or []
        if isinstance(item, dict) and item.get("claim_id")
    }
    return {
        claim_id
        for claim_id in set(old_claims) | set(new_claims)
        if old_claims.get(claim_id) != new_claims.get(claim_id)
    }


def fanfiction_semantic_dependency_paths(
    root: Path,
    *,
    task_type: str,
    changed_claim_ids: set[str],
) -> list[str]:
    """Find only artifacts that carry exact semantic IDs or a formal engine hash dependency."""

    if not changed_claim_ids:
        return []
    paths: set[str] = set()
    if task_type == "fanfiction_story_engine":
        route = root / "10_bible" / "fanfiction" / "fanfiction_bible.json"
        if route.is_file():
            paths.add(route.relative_to(root).as_posix())
    book_dependency = root / "10_bible" / "fanfiction" / "book_design_dependency.json"
    if book_dependency.is_file():
        paths.add(book_dependency.relative_to(root).as_posix())
    for relative_directory in (
        "10_bible/fanfiction",
        "20_outline",
        "30_state",
        "50_workbench/fanfiction_context",
        "50_workbench/writing_tasks",
    ):
        directory = root / relative_directory
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.json"):
            if path in {
                root / "10_bible" / "fanfiction" / "story_engine.json",
                root / "10_bible" / "fanfiction" / "fanfiction_bible.json",
                root / "30_state" / "stale_artifacts.json",
            }:
                continue
            value = read_json(path, None)
            if any(contains_exact_semantic_value(value, claim_id) for claim_id in changed_claim_ids):
                paths.add(path.relative_to(root).as_posix())
    return sorted(paths)


def contains_exact_semantic_value(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return any(contains_exact_semantic_value(item, expected) for item in value)
    if isinstance(value, dict):
        return any(contains_exact_semantic_value(item, expected) for item in value.values())
    return False


def mark_fanfiction_semantic_dependents_stale(
    root: Path,
    *,
    task_type: str,
    changed_claim_ids: set[str],
    artifact_paths: list[str],
) -> None:
    stale_path = root / "30_state" / "stale_artifacts.json"
    stale = read_json(stale_path, {"schema": "stale_artifact_registry_v1", "items": []})
    rows = stale.get("items") if isinstance(stale, dict) else []
    rows = rows if isinstance(rows, list) else []
    by_path = {
        str(item.get("artifact_path") or ""): item
        for item in rows
        if isinstance(item, dict) and item.get("artifact_path")
    }
    timestamp = datetime.now(timezone.utc).isoformat()
    for artifact_path in artifact_paths:
        by_path[artifact_path] = {
            "artifact_path": artifact_path,
            "classification": "must_stale",
            "dependency_fact_ids": sorted(changed_claim_ids),
            "source": task_type,
            "state": "stale",
            "stale_at": timestamp,
            "reason": "explicit fanfiction semantic claim or engine-hash dependency changed",
        }
    write_json(
        stale_path,
        {"schema": "stale_artifact_registry_v1", "items": list(by_path.values())},
    )




def revise_design_document(
    config: ConfigDocument, *, task_id: str, expected_sha256: str, text: str,
) -> dict[str, Any]:
    """Preserve a design candidate and its approvals while registering a human revision."""
    from longform_engine.agent_pipeline import validate_production_agent_result
    from longform_engine.agent_tasks import record_supersession_projection, update_task_status

    root = resolve_project_root(config)
    original = load_manifest(root, task_id)
    task_type = str(original["task_type"])
    if task_type not in DESIGN_INTELLIGENCE_TASK_TYPES:
        raise ValueError("Only Markdown design candidates can be revised here.")
    source = resolve_candidate(root, manifest_output(original)["path"])
    if sha256(source.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("设计候选已变化，请保留本地文字并重新比较。")
    if not isinstance(text, str) or not text.strip() or len(text.encode("utf-8")) > 2_000_000:
        raise ValueError("设计修改稿为空或过长。")
    digest = sha256(text.encode("utf-8")).hexdigest()
    revision_id = f"{task_id}:human:{digest[:16]}"
    tasks = list_manifests(root)
    existing = next((row for row in tasks if row["task_id"] == revision_id), None)
    if existing:
        return {"task_id": revision_id, "status": existing["status"], "canonical_mutated": False}
    if original.get("status") not in {"submitted", "validated", "approved", "invalid"}:
        raise ValueError("此设计版本已被应用或替代，请修改当前候选。")
    token = sha256(revision_id.encode("utf-8")).hexdigest()[:16]
    candidate = root / "50_workbench/intelligence_candidates" / f"human.{token}.candidate.md"
    instruction = root / "50_workbench/intelligence_tasks" / f"human.{token}.md"
    atomic_write_text(instruction, "# 人工设计修改\n\n以原工作单及当前修改稿为准。此次只保存候选并校验，仍需重新人工批准。\n"
                      f"唯一输出：{relative(root, candidate)}\n")
    commands = manifest_commands(original)
    old_path, new_path = relative(root, source), relative(root, candidate)
    inputs = list(dict.fromkeys([*manifest_input_paths(original), old_path, relative(root, instruction)]))
    manifest = build_manifest(root, task_type=task_type, chapter_number=manifest_chapter_number(original) or None,
        scope=original["scope"], task_id=revision_id, input_files=inputs,
        allowed_output_paths=[candidate], output_schema=DESIGN_DOCUMENT_SCHEMA,
        validate_command=commands["validate"].replace(old_path, new_path),
        apply_command=commands["apply"].replace(old_path, new_path),
        failure_next_command=commands["failure"].replace(old_path, new_path),
        canonical_targets=intelligence_canonical_targets(root, task_type, original["scope"]),
        requires_human_apply=True, context_policy={"required_files": inputs, "compiled_brief": instruction})
    atomic_write_text(candidate, text)
    write_manifest(root, manifest, root / "50_workbench/agent_tasks" / f"human.{token}.manifest.json")
    protocol = validate_production_agent_result(root, manifest, result_file=candidate)
    if not protocol.ok:
        return {"task_id": revision_id, "status": "invalid", "validation": asdict(protocol), "canonical_mutated": False}
    validation = validate_intelligence_candidate(config, task_type=task_type, file_path=candidate)
    superseded = []
    if validation.ok:
        for row in tasks:
            if row.get("status") in {"applied", "superseded", "rolled_back"}:
                continue
            dependent = row["task_id"] == task_id or (
                row["task_type"] == task_type and row["task_id"].startswith(task_id + ":human:")
                and old_path in manifest_input_paths(load_manifest(root, row["task_id"]))) or (
                row["task_type"] == "design_semantic_compile"
                and old_path in manifest_input_paths(load_manifest(root, row["task_id"])))
            if dependent:
                update_task_status(root, row["task_id"], to_status="superseded",
                                   command="intelligence human revision", result=candidate)
                superseded.append(row["task_id"])
        record_supersession_projection(root, task_id=revision_id, supersedes_task_ids=superseded,
                                      command="intelligence human revision", artifact=candidate)
    return {"task_id": revision_id, "status": "validated" if validation.ok else "invalid",
            "validation": asdict(validation), "canonical_mutated": False}


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
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES
    ):
        try:
            current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                config, root
            )
        except fanfiction_contracts.FanfictionContractError as exc:
            raise ValueError(str(exc)) from exc
    document = resolve_candidate(root, document_path)
    validation = validate_intelligence_candidate(
        config,
        task_type=task_type,
        file_path=document,
        current_fanfiction=current_fanfiction,
    )
    if not validation.ok:
        raise ValueError("Design document is invalid: " + "; ".join(validation.errors))
    manifest = manifest_for_output(root, task_type, document)
    if manifest is None:
        raise ValueError("Design document is not bound to an active Agent task.")
    document_hash = sha256(document.read_bytes()).hexdigest()
    approval = design_approval_path(root, document)
    approval_payload: dict[str, Any] = {
        "schema": "design_document_approval_v1",
        "task_id": str(manifest.get("task_id") or ""),
        "task_type": task_type,
        "document_path": relative(root, document),
        "document_sha256": document_hash,
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    if current_fanfiction is not None:
        approval_payload["fanfiction_chain"] = (
            fanfiction_contracts.current_fanfiction_chain_binding(
                root, current_fanfiction
            )
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
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES
    ):
        try:
            current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                config, root
            )
        except fanfiction_contracts.FanfictionContractError as exc:
            raise ValueError(str(exc)) from exc
    document = resolve_candidate(root, document_path)
    approval = load_design_approval(
        root,
        task_type,
        document,
        current_fanfiction=current_fanfiction,
    )
    source_manifest = manifest_for_output(root, task_type, document)
    if source_manifest is None:
        raise ValueError("Approved design document has no active source task.")
    if current_fanfiction is not None:
        _require_current_fanfiction_task_provenance(
            root,
            source_manifest,
            current_fanfiction,
            label="design source task",
        )
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
    version = str(approval["document_sha256"])[:16]
    predecessors = [item for item in list_manifests(root) if item["task_type"] == "design_semantic_compile"
                    and relative(root, document) in manifest_input_paths(item)]
    for item in reversed(predecessors):
        if item.get("status") not in {"awaiting_agent", "submitted", "validated"}:
            continue
        prior = load_manifest(root, item["task_id"])
        instruction_inputs = [path for path in manifest_input_paths(prior) if path.startswith("50_workbench/intelligence_tasks/")]
        if len(instruction_inputs) != 1:
            continue
        prior_output = manifest_output(prior)["path"]
        expected_instruction = render_design_compile_instruction(task_type=task_type, document=relative(root, document),
            document_hash=str(approval["document_sha256"]), domain_schema=str(TASK_SPECS[task_type]["schema"]), output=prior_output)
        if (validate_manifest_strict(root, prior, strict=True).ok
                and (root / instruction_inputs[0]).read_text(encoding="utf-8") == expected_instruction):
            return IntelligenceTaskResult("design_semantic_compile", item["task_id"], item["manifest_file"],
                instruction_inputs[0], prior_output, f"longform-engine agent-task brief project.yaml {item['task_id']}")
    attempt = len(predecessors) + 1
    owner = sha256(str(source_manifest["task_id"]).encode("utf-8")).hexdigest()[:12]
    base = f"compile.{owner}.{version}.{attempt:02d}"
    instruction = root / "50_workbench" / "intelligence_tasks" / f"{base}.md"
    delta = root / "50_workbench" / "intelligence_candidates" / f"{base}.delta.json"
    manifest_file = root / "50_workbench" / "agent_tasks" / f"{base}.manifest.json"
    approval_path = design_approval_path(root, document)
    instruction_text = render_design_compile_instruction(
        task_type=task_type,
        document=relative(root, document),
        document_hash=str(approval["document_sha256"]),
        domain_schema=str(TASK_SPECS[task_type]["schema"]),
        output=relative(root, delta),
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
    compile_inputs = [
        instruction,
        document,
        approval_path,
        *(current_fanfiction.paths.values() if current_fanfiction is not None else ()),
    ]
    compile_inputs = list(dict.fromkeys(compile_inputs))
    manifest = build_manifest(
        root,
        task_type="design_semantic_compile",
        chapter_number=int(scope.get("chapter_number") or 0) or None,
        scope=scope,
        input_files=compile_inputs,
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
            "required_files": compile_inputs,
            "optional_files": [],
            "compiled_brief": instruction,
            "selection_report": instruction,
            "trigger_codes": [task_type],
        },
        task_id=f"design_semantic_compile:{task_type}:{token}:{version}:{attempt:02d}:v5",
    )
    written = write_manifest(root, manifest, manifest_file, supersedes_task_ids=[item["task_id"] for item in predecessors
        if item.get("status") not in {"applied", "superseded", "rolled_back"}])
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
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None,
) -> IntelligenceValidationResult:
    if task_type not in DESIGN_INTELLIGENCE_TASK_TYPES:
        raise ValueError(f"{task_type} is not a design_document_v1 task.")
    root = resolve_project_root(config)
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES
    ):
        if current_fanfiction is None:
            try:
                current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                    config, root
                )
            except fanfiction_contracts.FanfictionContractError as exc:
                raise ValueError(str(exc)) from exc
    document = resolve_candidate(root, document_path)
    delta = resolve_candidate(root, delta_path)
    errors: list[str] = []
    approval = load_design_approval(
        root,
        task_type,
        document,
        errors=errors,
        current_fanfiction=current_fanfiction,
    )
    source_manifest = manifest_for_output(root, task_type, document)
    if source_manifest is None:
        errors.append("approved design document has no active source task.")
    elif current_fanfiction is not None:
        _require_current_fanfiction_task_provenance(
            root,
            source_manifest,
            current_fanfiction,
            label="design source task",
        )
    manifest = manifest_for_output(root, "design_semantic_compile", delta)
    if manifest is None:
        errors.append("delta is not declared by an active design_semantic_compile task.")
    else:
        if current_fanfiction is not None:
            _require_current_fanfiction_task_provenance(
                root,
                manifest,
                current_fanfiction,
                label="design compile task",
            )
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
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and task_type in FANFICTION_CURRENT_CHAIN_TASK_TYPES
    ):
        try:
            current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                config, root
            )
        except fanfiction_contracts.FanfictionContractError as exc:
            raise ValueError(str(exc)) from exc
    document = resolve_candidate(root, document_path)
    delta = resolve_candidate(root, delta_path)
    validation = validate_design_compile_delta(
        config,
        task_type=task_type,
        document_path=document,
        delta_path=delta,
        record_result=False,
        current_fanfiction=current_fanfiction,
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
            *(current_fanfiction.paths.values() if current_fanfiction is not None else ()),
        ),
        touched_paths=tuple(touched),
        metadata={
            "task_type": task_type,
            "compile_task_id": str(manifest.get("task_id") or ""),
            "approved_by": approved_by,
            "document_sha256": sha256(document.read_bytes()).hexdigest(),
            **(
                {
                    "fanfiction_chain": (
                        fanfiction_contracts.current_fanfiction_chain_binding(
                            root, current_fanfiction
                        )
                    )
                }
                if current_fanfiction is not None
                else {}
            ),
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
                "delta": raw_delta,
            },
        )
        write_targets(
            config,
            root,
            task_type,
            domain_payload,
            scope=scope,
            current_fanfiction=current_fanfiction,
        )
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






def load_design_approval(
    root: Path,
    task_type: str,
    document: Path,
    *,
    errors: list[str] | None = None,
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None,
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
        if current_fanfiction is not None:
            chain_errors = fanfiction_contracts.validate_current_fanfiction_chain_binding(
                root,
                current_fanfiction,
                payload.get("fanfiction_chain"),
            )
            if chain_errors:
                raise ValueError(
                    "design approval fanfiction chain is stale: "
                    + "; ".join(chain_errors)
                )
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
) -> str:
    return "\n".join((
        "# 设计文档语义编译任务", "",
        f"- 原设计任务：`{task_type}`",
        f"- 已批准文档：`{document}`",
        f"- 文档 SHA-256：`{document_hash}`",
        f"- CLI 内部领域 schema：`{domain_schema}`",
        f"- 唯一输出：`{output}`", "", "## 编译职责",
        "只把已批准 Markdown 中明确成立的事实编译为 canonical_delta_v1。",
        '顶层只能含 schema、delta_type="design_document"、coverage、changes、evidence、uncertainties 六个字段。',
        'coverage 是章节标题到 "changed"、"unchanged" 或 "insufficient" 的映射，不是数组。',
        "changes 使用下列目标领域字段，不写顶层领域 schema、路径、hash、章节范围、命令或时间；明确要求的嵌套协议常量除外。",
        f'evidence 必须使用 /changes/... JSON Pointer 映射到非空字符串数组，例如 ["{document}@start:end"]。',
        "示例中的 start/end 须替换为全文 Unicode 字符偏移，end 不包含；不要使用字面路径 document。",
        "设计编译实行原文逐项落地：每个事实字符串必须完整出现在该字段引用的 Markdown 范围中。不要概括、改写、拼接分散句子，或给表格单元格添加原文没有的行标题与冒号。",
        "例如原表格为 | 代价 | 过用会伤及经脉。 |，tradeoffs 可提取原句“过用会伤及经脉。”，不能写成“代价：过用会伤及经脉。”。proposal 同样提取一个完整原文段落，不合并标题、正文和人工决定。",
        "可以用内存脚本读取已声明文档，通过 Python str.index 和 len 计算并回读 text[start:end]；不得另写辅助文件，也不要凭目测估算偏移。",
        "文档作为整体获批后，明确提出且未被否决的设计补足可以编译；互斥的备选方案、被否决内容和校准示例不能当作既定剧情。",
        "后续待细化的事件不需要在此解决，也不能虚构答案。仅当本次必需字段无法从批准文档唯一确定时记录 uncertainties；不要把明确保留的未来设计空间误报为本次必需决策缺失。",
        "任何稳定 ID、窗口、关系或语义存在歧义时写入 uncertainties；CLI 将阻止 apply。", "",
        ("本任务 changes 只含 question（本轮创作问题）、options（2–3 项，每项只有 id、proposal、tradeoffs 非空字符串列表）、"
         "selection（只有 mode、option_id、answer）。采用选项时 mode=selected_option，option_id 绑定 options 的稳定 ID，answer 为空；"
         "作者提供新回答时 mode=provided_answer，option_id 为空，answer 保留作者的决定。round 和 dimension 由 CLI 提供，不回填。"
         if task_type == "book_ideation" else ""),
        design_field_contract(task_type),
    ))


def design_field_contract(task_type: str) -> str:
    """Declare compiler fields and their design obligations from domain constants.

    Designers and compilers receive the same requirements. This is a protocol
    description, never an example that supplies invented story facts.
    """
    sections = []
    if task_type == "book_design":
        sections.extend((
            "## 全书设计必须明确的字段与类型",
            "Markdown 仍是唯一创作输出。用自然语言或表格明确下面每项设计，供批准后的编译器逐项取证；不要在设计阶段生成 JSON。",
            "编译时 changes 必须含 creative_brief（对象）、world_markdown、power_system_markdown（各为一个连续原文区段）、characters、relationships、narrative_expression_profile、character_expression_contracts。可选 factions、locations 均为对象列表，每项有稳定 id 与 name。",
            "creative_brief 必需非空字符串 target_audience、writing_style、automation_level、target_scale；非空对象 story_profile（用自然的描述键保存本书类型与叙事选择）；reader_contract 对象；core_taboo 非空字符串列表。不要补写 status。",
            "creative_brief.design_decisions 恰好含 core_hook、world_rule、protagonist_desire、long_conflict、volume_escalation、ending_boundary 六项非空原文字符串。",
            "creative_brief.story_engine_contract 恰好含 schema=story_engine_contract_v1；reader_fantasy、repeatable_action_loop、progression_loop、relationship_loop、mystery_or_question_loop、theme_carrier_limits 六项非空原文字符串；expected_payoffs（恰好 opening_three、early_serial、volume_end 三项非空字符串）；carrier_palette（至少三项不同场景承载方式的原文字符串）。推进可来自关系、理解或处境变化，不强制反复升级。",
            "characters 至少一项，每项含稳定 id、name、goal、flaw（非空字符串）、arc_stages（至少三项非空字符串，分别指出人物弧中可观察的状态；不是强制三幕剧情）。",
            "relationships 至少一项，每项含稳定 id、source_id、target_id、type、stage；端点引用 characters 的 id，type 和 stage 用明确的关系及当前阶段原文。人物与关系 ID 需在设计中声明，同一对象始终复用。稳定 ID 只用字母、数字、冒号、下划线和连字符。",
        ))
    if task_type in {"book_design", "character_expression_design"}:
        sections.extend((
            "## 人物表达字段与类型",
            "character_expression_design 的 changes 只含 narrative_expression_profile 与 character_expression_contracts；book_design 把这两项与全书字段一并提供。",
            "narrative_expression_profile 恰好含下面六项。设计用中文说明选择并在括号内标明选定编码，编译沿用该编码，不从未声明偏好猜测：",
            json.dumps({key: sorted(values) for key, values in EXPRESSION_PROFILE_FIELDS.items()}, ensure_ascii=False),
            "character_expression_contracts 为非空对象列表，覆盖已设计的重要人物。每项恰好包含以下非空字符串字段：" + "、".join(CHARACTER_CONTRACT_STRING_FIELDS) + "；以下非空字符串列表：" + "、".join(CHARACTER_CONTRACT_LIST_FIELDS) + "；以及 voice_examples 列表。",
            "character_id 与 contrast_with 使用已声明人物 ID。分别写出注意点、选择偏向、说话策略、情绪泄露、身体表现、面具、私欲、矛盾和对照，不能拿同一条通用性格说明填满各项。",
            "voice_examples 没有已批准声例时为 []，不能把设计示例提升为已批准正文。非空时每项仅 polarity（positive/negative）、text、note、approved；示例不得改变剧情事实。",
        ))
    return "\n".join(sections)


def design_cli_fields(
    config: ConfigDocument,
    root: Path,
    task_type: str,
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    scope = (manifest or {}).get("scope") if isinstance((manifest or {}).get("scope"), dict) else {}
    if task_type == "arc_simulation":
        return {
            "from_chapter": int(scope.get("from_chapter") or 0),
            "to_chapter": int(scope.get("to_chapter") or 0),
            "basis_hashes": current_basis_hashes(root),
            "approved_by": "human",
            "status": "approved",
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
        for scalar in design_fact_scalars(value, pointer.removeprefix("/changes/")):
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


def design_fact_scalars(value: Any, field_path: str = "") -> list[str]:
    # These validated encodings describe the protocol, not a fact the author
    # must literally type into an otherwise natural-language design document.
    if field_path.endswith("/schema") or (
        field_path == "selection/mode" and isinstance(value, str) and value in {"selected_option", "provided_answer"}
    ):
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in design_fact_scalars(child, f"{field_path}/{index}")]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in design_fact_scalars(child, f"{field_path}/{key}")]
    return []


def normalize_grounding_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def design_document_target(root: Path, task_type: str, scope: dict[str, Any]) -> Path:
    token = scope_token(scope)
    if task_type == "arc_simulation":
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
        if task_type in {"book_design", "fanfiction_design"}:
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
    if task_type in {"outline_design", "outline_extension", "chapter_direction", "outline_revision"}:
        raise ValueError(
            f"{task_type} is retired: rebuild and approve the current planning bundle through "
            "longform-engine planning task; existing final and source Canon remain authoritative."
        )
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
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None
    if creation_mode == "fanfiction":
        existing_canon = read_json(root / "10_bible" / "fanfiction" / "source_canon.json", {})
        if isinstance(existing_canon, dict) and existing_canon.get("schema") in {
            "fanfiction_source_canon_v1",
            "fanfiction_source_canon_v2",
            "fanfiction_source_canon_v3",
        }:
            return ProjectReadinessResult(
                False,
                "fanfiction_canon_incompatible",
                "",
                (
                    f"{existing_canon.get('schema')} is incompatible; rebuild the project source pack and apply "
                    f"{FANFICTION_CANON_SCHEMA}. Use the explicit v0.11 audit/import path; no dual read is available.",
                ),
            )
        source_readiness = fanfiction_source_readiness(config)
        if not source_readiness["ready"]:
            return ProjectReadinessResult(
                False,
                "fanfiction_sources",
                "",
                tuple(source_readiness["errors"]),
            )
        canon_marker = markers.get("fanfiction_canon") if isinstance(markers.get("fanfiction_canon"), dict) else {}
        if canon_marker.get("status") != "applied":
            return ProjectReadinessResult(
                False,
                "fanfiction_canon",
                "fanfiction_canon",
                ("fanfiction_canon has not been explicitly applied.",),
            )
        try:
            fanfiction_contracts.load_current_fanfiction_source_canon(config, root)
        except fanfiction_contracts.FanfictionContractError as exc:
            return ProjectReadinessResult(
                False,
                "fanfiction_canon",
                "fanfiction_canon",
                (str(exc),),
            )
        story_engine_marker = (
            markers.get("fanfiction_story_engine")
            if isinstance(markers.get("fanfiction_story_engine"), dict)
            else {}
        )
        if story_engine_marker.get("status") != "applied":
            return ProjectReadinessResult(
                False,
                "fanfiction_story_engine",
                "fanfiction_story_engine",
                ("fanfiction_story_engine has not been explicitly applied.",),
            )
        try:
            fanfiction_contracts.load_current_fanfiction_story_engine(config, root)
        except fanfiction_contracts.FanfictionContractError as exc:
            return ProjectReadinessResult(
                False,
                "fanfiction_story_engine",
                "fanfiction_story_engine",
                (str(exc),),
            )
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
        try:
            current_fanfiction = fanfiction_contracts.load_current_fanfiction_documents(
                config, root
            )
        except fanfiction_contracts.FanfictionContractError as exc:
            return ProjectReadinessResult(
                False,
                "fanfiction_design",
                "fanfiction_design",
                (str(exc),),
            )
    book_marker = markers.get("book_design") if isinstance(markers.get("book_design"), dict) else {}
    if book_marker.get("status") != "applied":
        return ProjectReadinessResult(
            False,
            "book_design",
            "book_design",
            ("book_design has not been explicitly applied.",),
        )
    if creation_mode == "fanfiction":
        dependency = read_json(
            root / "10_bible" / "fanfiction" / "book_design_dependency.json", {}
        )
        if (
            current_fanfiction is None
            or
            not isinstance(dependency, dict)
            or dependency.get("schema") != "fanfiction_book_design_dependency_v2"
            or fanfiction_contracts.validate_current_fanfiction_chain_binding(
                root,
                current_fanfiction,
                dependency.get("fanfiction_chain"),
            )
        ):
            return ProjectReadinessResult(
                False,
                "book_design",
                "book_design",
                ("book_design is stale against the complete current fanfiction chain.",),
            )
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
    from longform_engine.planning.context import load_chapter_planning_context

    chapter = int(state.get("last_closed_chapter") or 0) + 1
    try:
        load_chapter_planning_context(root, chapter)
    except ValueError as exc:
        return ProjectReadinessResult(False, "planning_refresh_required", "", (str(exc),))
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
            raise ValueError("chapter-scoped intelligence task requires --chapter N.")
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
    *,
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None,
) -> list[Path]:
    current_paths = (
        {
            path.relative_to(root).as_posix(): path
            for path in (
                current_fanfiction.paths["source_canon"],
                current_fanfiction.paths["story_engine"],
                current_fanfiction.paths["route_design"],
            )
        }
        if current_fanfiction is not None
        else {}
    )
    candidates = [
        current_paths.get(str(item), root / str(item)) for item in spec["defaults"]
    ]
    if task_type in {"book_design", "fanfiction_design"}:
        candidates.append(root / "10_bible" / "creative_decisions.json")
    if task_type == "book_design":
        candidates.extend(
            [
                current_fanfiction.paths["story_engine"],
                current_fanfiction.paths["route_design"],
            ]
            if current_fanfiction is not None
            else []
        )
    if task_type in {"book_design", "arc_simulation"}:
        candidates.extend(sorted((root / "20_outline" / "semantic" / "全书架构").glob("*.json")))
    if task_type in {
        "book_design",
        "character_expression_design",
        "arc_simulation",
    }:
        candidates.extend(sorted((root / "10_bible" / "semantic" / "人物理解").glob("*.json")))
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
    if task_type == "arc_simulation":
        candidates.extend(
            sorted((root / "60_rag" / "memory" / "characters").glob("*.json"))
        )
    return [path for path in candidates if path.is_file()]


def write_fanfiction_story_engine_context(config: ConfigDocument, root: Path) -> Path:
    """Compile the approved baseline into one bounded story-engine work order input."""

    try:
        canon = fanfiction_contracts.load_current_fanfiction_source_canon(config, root)
    except fanfiction_contracts.FanfictionContractError as exc:
        raise ValueError(str(exc)) from exc
    budget = resolve_context_budget_contract(root)
    selected_claims, selection_report = semantic_claim_context(
        canon,
        budget_units=max(1_000, int(budget.capacity_units * 0.62)),
        estimator=budget.estimator,
    )
    if selection_report["omitted_claim_ids"]:
        raise ValueError(
            "prompt_budget_exceeded: fanfiction story-engine design-core Canon cannot be "
            "silently truncated; narrow the approved design_core baseline or use a larger context profile"
        )
    configured = (
        config.data.get("fanfiction")
        if isinstance(config.data.get("fanfiction"), dict)
        else {}
    )
    sources = [
        {
            "source_id": str(item.get("source_id") or ""),
            "title": str(item.get("title") or ""),
            "canon_cutoff": str(item.get("canon_cutoff") or ""),
            "allowed_elements": list(item.get("allowed_elements") or []),
        }
        for item in configured.get("sources") or []
        if isinstance(item, dict)
    ]
    payload = {
        "schema": "fanfiction_story_engine_context_v2",
        "creative_contract_version": CREATIVE_CONTRACT_VERSION,
        "creative_requirements_by_route": {
            route: compile_fanfiction_creative_requirements(str(configured.get("continuity_mode") or ""), route)
            for route in sorted(ROUTE_FAMILIES)
        },
        "project": {
            "title": str(config.data.get("project", {}).get("title") or ""),
            "target_platform": str(config.data.get("novel", {}).get("target_platform") or ""),
            "audience": str(config.data.get("novel", {}).get("audience") or ""),
            "core_promise": str(config.data.get("novel", {}).get("core_promise") or ""),
            "forbidden_experience": list(
                config.data.get("novel", {}).get("forbidden_experience") or []
            ),
            "continuity_mode": str(configured.get("continuity_mode") or ""),
            "sources": sources,
        },
        "four_ranges": {
            "source_evidence_range": [
                {
                    "source_id": str(item.get("source_id") or ""),
                    "canon_cutoff": str(item.get("canon_cutoff") or ""),
                    "binding_sha256": str(item.get("binding_sha256") or ""),
                    "coverage_plan_sha256": str(item.get("coverage_plan_sha256") or ""),
                }
                for item in canon.get("extensions", {}).get("source_contracts") or []
                if isinstance(item, dict)
            ],
            "project_canon_cutoff": [
                {"source_id": item["source_id"], "canon_cutoff": item["canon_cutoff"]}
                for item in sources
            ],
            "story_entry_point": "由本任务提出候选并由人工批准",
            "character_knowledge_range": "按人物阶段分别声明，作者资料范围不得自动成为人物知识",
        },
        "approved_source_canon": {
            "title": canon.get("title"),
            "body": str(canon.get("body") or "")[:4_000],
            "claims": selected_claims,
            "uncertainties": list(canon.get("uncertainties") or []),
        },
        "selection_report": {
            **selection_report,
            "retrieval_domain": "project_canon",
            "budget_profile": budget.profile,
            "capacity_units": budget.capacity_units,
        },
        "canonical_provenance": [
            {
                "path": relative(root, canon.path),
                "sha256": canon.sha256,
                "authority": "approved_project_source_canon",
            },
            {
                "path": "project.yaml",
                "sha256": sha256((root / "project.yaml").read_bytes()).hexdigest(),
                "authority": "project_configuration",
            },
        ],
    }
    target = (
        root
        / "50_workbench"
        / "intelligence_context"
        / "fanfiction_story_engine.project.context.json"
    )
    atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return target


def semantic_claim_context(
    document: dict[str, Any],
    *,
    budget_units: int,
    estimator: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project open semantic claims with explicit token omissions and no row caps."""

    evidence = {
        str(item.get("evidence_id") or ""): item
        for item in document.get("evidence_references") or []
        if isinstance(item, dict)
    }
    selected: list[dict[str, Any]] = []
    omitted: list[str] = []
    used_units = 0
    for claim in document.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        record = {
            "claim_id": claim.get("claim_id"),
            "statement": claim.get("statement"),
            "applicability": claim.get("applicability"),
            "uncertainty": claim.get("uncertainty"),
            "extensions": claim.get("extensions") or {},
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "excerpt": str(evidence.get(str(evidence_id), {}).get("excerpt") or "")[:400],
                    "locator": evidence.get(str(evidence_id), {}).get("locator") or {},
                }
                for evidence_id in claim.get("evidence_refs") or []
            ],
        }
        units = estimate_text_units(
            json.dumps(record, ensure_ascii=False, sort_keys=True), estimator
        )
        if used_units + units > budget_units:
            omitted.append(str(claim.get("claim_id") or ""))
            continue
        selected.append(record)
        used_units += units
    return selected, {
        "strategy": "approved semantic claims in canonical order within an explicit token budget",
        "used_units": used_units,
        "claim_budget_units": budget_units,
        "omitted_claim_ids": omitted,
        "omission_reason": "token_budget" if omitted else "",
    }


def write_fanfiction_design_context(config: ConfigDocument, root: Path) -> Path:
    """Compile approved semantic claims by token budget, never by ontology row caps."""

    decisions_path = root / "10_bible" / "creative_decisions.json"
    decisions = read_json(decisions_path, {})
    try:
        current = fanfiction_contracts.load_current_fanfiction_story_engine_documents(
            config, root
        )
    except fanfiction_contracts.FanfictionContractError as exc:
        raise ValueError(str(exc)) from exc
    canon = current.source_canon
    story_engine = current.story_engine
    if not isinstance(decisions, dict) or decisions.get("schema") != "book_ideation_decisions_v1":
        decisions = {"decisions": {}}

    project = config.data.get("project") if isinstance(config.data.get("project"), dict) else {}
    fanfiction = config.data.get("fanfiction") if isinstance(config.data.get("fanfiction"), dict) else {}
    novel = config.data.get("novel") if isinstance(config.data.get("novel"), dict) else {}
    length = config.data.get("length") if isinstance(config.data.get("length"), dict) else {}
    forecast = compile_length_forecast(length)
    budget = resolve_context_budget_contract(root)
    claim_budget = max(1_000, int(budget.capacity_units * 0.55))
    selected_claims, selection_report = semantic_claim_context(
        canon,
        budget_units=claim_budget,
        estimator=budget.estimator,
    )
    if selection_report["omitted_claim_ids"]:
        raise ValueError(
            "prompt_budget_exceeded: formal fanfiction route design cannot silently omit "
            "approved design-core claims; narrow the Canon baseline or use a larger context profile"
        )
    payload = {
        "schema": "fanfiction_semantic_context_v2",
        "creative_contract_version": CREATIVE_CONTRACT_VERSION,
        "creative_requirements": compile_fanfiction_creative_requirements(
            str(fanfiction.get("continuity_mode") or ""), str(story_engine["extensions"]["route_family"])
        ),
        "project_contract": {
            "title": project.get("title"),
            "continuity_mode": fanfiction.get("continuity_mode"),
            "configured_sources": fanfiction.get("sources") or [],
            "novel": novel,
            "length": forecast.to_dict(),
            "story_profile": config.data.get("story_profile", {}),
        },
        "crossover_contract": {
            "required": fanfiction_contracts.requires_crossover_contract(config),
            "topologies": sorted(fanfiction_contracts.CROSSOVER_TOPOLOGIES),
            "payload_kinds": sorted(fanfiction_contracts.CROSSOVER_PAYLOAD_KINDS),
            "always_required_topics": sorted(
                fanfiction_contracts.CROSSOVER_ALWAYS_REQUIRED_TOPICS
            ),
            "topics_by_payload_kind": {
                payload_kind: sorted(topics)
                for payload_kind, topics in sorted(
                    fanfiction_contracts.CROSSOVER_TOPICS_BY_PAYLOAD_KIND.items()
                )
            },
            "adapter_scope": "只覆盖实际 transfers 形成的 source-volume-host interactions",
            "transfer_fields": ["source_id", "payload_kinds", "volume_ids"],
            "adapter_fields": [
                "source_id",
                "payload_kinds",
                "host_source_id",
                "volume_ids",
            ],
            "topology_rules": {
                "fixed_host": (
                    "at least one non-host transfer into default_host_source_id; "
                    "host self-transfer is invalid"
                ),
                "fusion_world": "at least two distinct participating transfers.source_id",
                "sequential_worlds": (
                    "explicit extensions.crossover.volume_ids and exactly one 卷宿主世界 "
                    "host plus at least one actual source-volume-host interaction per "
                    "declared volume; no Cartesian coverage"
                ),
            },
        },
        "approved_decisions": decisions.get("decisions") or {},
        "approved_story_engine": {
            "document_type": story_engine.get("document_type"),
            "title": story_engine.get("title"),
            "route_family": str(
                (story_engine.get("extensions") or {}).get("route_family") or ""
            ),
            "body": str(story_engine.get("body") or "")[:6_000],
            "claims": [
                {
                    "claim_id": claim.get("claim_id"),
                    "statement": claim.get("statement"),
                    "applicability": claim.get("applicability"),
                    "uncertainty": claim.get("uncertainty"),
                    "extensions": claim.get("extensions") or {},
                }
                for claim in story_engine.get("claims") or []
                if isinstance(claim, dict)
            ],
        },
        "canon": {
            "document_type": canon.get("document_type"),
            "title": canon.get("title"),
            "continuity": canon.get("continuity"),
            "body": str(canon.get("body") or "")[:4000],
            "claims": selected_claims,
            "uncertainties": canon.get("uncertainties") or [],
        },
        "selection_report": {**selection_report, "retrieval_domain": "project_canon"},
        "canonical_provenance": [
            {
                "path": relative(root, path),
                "sha256": current.sha256[name],
                "authority": "canonical_recheck_required",
            }
            for name, path in current.paths.items()
        ]
        + [
            {
                "path": relative(root, path),
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "authority": "canonical_recheck_required",
            }
            for path in (decisions_path, root / "project.yaml")
            if path.is_file()
        ],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    payload["selection_report"]["estimated_units"] = estimate_text_units(rendered, budget.estimator)
    payload["selection_report"]["budget_profile"] = budget.profile
    payload["selection_report"]["capacity_units"] = budget.capacity_units
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    target = root / "50_workbench" / "intelligence_context" / "fanfiction_design.project.context.json"
    atomic_write_text(target, rendered)
    return target


def write_chapter_semantic_planning_context(
    config: ConfigDocument, root: Path, chapter_number: int
) -> Path:
    """Bind open semantic planning to approved scope without inventing chapter choices."""
    from longform_engine.planning.context import load_chapter_planning_context
    from longform_engine.human_chapter_intent import require_current_human_chapter_intent

    planning = load_chapter_planning_context(root, chapter_number)
    intent = require_current_human_chapter_intent(root, chapter_number)
    payload = {
        "schema": "chapter_semantic_planning_context_v1",
        "chapter_number": chapter_number,
        "chapter_contract": planning.contract,
        "approved_nodes": list(planning.nodes),
        "semantic_obligations": list(planning.obligations),
        "volume": planning.volume,
        "human_intent": intent["payload"],
        "source_files": [*planning.source_files, {"path": intent["path"], "sha256": intent["sha256"]}],
        "boundary": "Planning claims describe intended changes. Only final-bound semantic evidence proves actual events.",
    }
    budget = resolve_context_budget_contract(root)
    if estimate_text_units(json.dumps(payload, ensure_ascii=False), budget.estimator) > budget.capacity_units:
        raise ValueError("prompt_budget_exceeded: approved semantic planning context cannot be truncated")
    path = root / "50_workbench" / "intelligence_tasks" / f"chapter_semantic_planning.ch{chapter_number:03d}.context.json"
    write_json(path, payload)
    return path




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


def _require_current_fanfiction_task_provenance(
    root: Path,
    manifest: dict[str, Any],
    current: fanfiction_contracts.CurrentFanfictionDocuments,
    *,
    label: str,
) -> None:
    """Require a current task manifest that binds every document in one validated chain."""

    validation = validate_manifest_strict(root, manifest, strict=True)
    if not validation.ok:
        raise ValueError(
            f"{label} fanfiction chain provenance is stale: "
            + "; ".join(validation.errors)
        )
    records = {
        str(item.get("path") or "").replace("\\", "/"): str(item.get("sha256") or "")
        for item in (manifest.get("io") or {}).get("inputs") or []
        if isinstance(item, dict) and item.get("kind") != "media_asset"
    }
    binding = fanfiction_contracts.current_fanfiction_chain_binding(root, current)
    mismatches = [
        str(item["name"])
        for item in binding["documents"]
        if records.get(str(item["path"])) != item["sha256"]
    ]
    if mismatches:
        raise ValueError(
            f"{label} fanfiction chain provenance is incomplete or stale for: "
            + ", ".join(mismatches)
        )


def fanfiction_review_route_path(
    root: Path,
    manifest: dict[str, Any] | None,
) -> Path:
    """Return the exact route candidate pinned as the first independent-review input."""

    inputs = manifest_input_paths(manifest or {})
    if not inputs:
        raise ValueError("fanfiction_design_review manifest is missing its route candidate input.")
    route = (root / inputs[0]).resolve()
    try:
        route.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("fanfiction design review route input escapes the project root.") from exc
    if not route.is_file():
        raise ValueError("fanfiction design review route candidate is unavailable.")
    return route


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
            elif protocol == SEMANTIC_DOCUMENT_SCHEMA:
                if not isinstance(payload, dict):
                    raise AgentProtocolError("semantic document must be an object")
                payload = seal_semantic_document(payload)
                if task_type == "fanfiction_canon":
                    payload = hydrate_fanfiction_semantic_document(config, payload)
                elif task_type == "fanfiction_story_engine":
                    payload = hydrate_fanfiction_story_engine_document(config, root, payload, manifest)
                elif task_type == "fanfiction_design":
                    payload = hydrate_fanfiction_design_document(config, root, payload, manifest)
                elif task_type == "fanfiction_design_review":
                    payload = hydrate_fanfiction_design_review_document(
                        config, root, payload, manifest
                    )
                semantic_errors = validate_semantic_document(payload)
                if semantic_errors:
                    raise AgentProtocolError("; ".join(semantic_errors))
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

    if task_type == "research_synthesis":
        return hydrate_research_delta(root, delta, domain_payload, manifest)
    return domain_payload


def hydrate_fanfiction_semantic_document(
    config: ConfigDocument, payload: dict[str, Any]
) -> dict[str, Any]:
    """Attach CLI-owned source pins without asking the Host Agent to copy hashes."""

    configured = config.data.get("fanfiction")
    configured = configured if isinstance(configured, dict) else {}
    extensions = payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}
    repeated = sorted({"source_contracts", "continuity_mode"} & set(extensions))
    if repeated:
        raise AgentProtocolError(
            "fanfiction semantic Canon must not repeat CLI-owned fields: " + ", ".join(repeated)
        )
    source_contracts = fanfiction_contracts.current_fanfiction_source_contracts(config)
    hydrated = json.loads(json.dumps(payload, ensure_ascii=False))
    hydrated["extensions"].update(
        {
            "task_type": "fanfiction_canon",
            "continuity_mode": str(configured.get("continuity_mode") or ""),
            "source_contracts": source_contracts,
        }
    )
    return seal_semantic_document(hydrated)


def bound_fanfiction_creative_version(root: Path, manifest: dict[str, Any] | None) -> str:
    """Read the version fixed in the immutable task input; never upgrade old work."""
    matches: list[str] = []
    for item in ((manifest or {}).get("io") or {}).get("inputs") or []:
        relative_path = str(item.get("path") or "")
        if not relative_path.endswith(".context.json"):
            continue
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root.resolve()):
            raise AgentProtocolError("creative contract input escapes project")
        raw = path.read_bytes()
        context = json.loads(raw)
        if not isinstance(context, dict) or context.get("schema") not in {
            "fanfiction_story_engine_context_v2", "fanfiction_semantic_context_v2"
        }:
            continue
        if sha256(raw).hexdigest() != item.get("sha256"):
            raise AgentProtocolError("creative contract task input is stale; rebuild task")
        matches.append(str(context.get("creative_contract_version") or ""))
    if matches != [CREATIVE_CONTRACT_VERSION]:
        raise AgentProtocolError("creative contract version is missing or incompatible in task input; rebuild task")
    return matches[0]


def hydrate_fanfiction_design_document(
    config: ConfigDocument, root: Path, payload: dict[str, Any], manifest: dict[str, Any] | None
) -> dict[str, Any]:
    """Bind a route proposal to the exact approved project Canon used to design it."""

    extensions = payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}
    cli_fields = {"creative_contract_version", "continuity_mode", "source_canon_sha256", "story_engine_sha256"}
    repeated = sorted(cli_fields & set(extensions))
    if repeated:
        raise AgentProtocolError(
            "fanfiction design must not repeat CLI-owned fields: " + ", ".join(repeated)
        )
    try:
        current = fanfiction_contracts.load_current_fanfiction_story_engine_documents(
            config, root
        )
    except fanfiction_contracts.FanfictionContractError as exc:
        raise AgentProtocolError(str(exc)) from exc
    configured = config.data.get("fanfiction")
    configured = configured if isinstance(configured, dict) else {}
    hydrated = json.loads(json.dumps(payload, ensure_ascii=False))
    hydrated["extensions"].update(
        {
            "task_type": "fanfiction_design",
            "creative_contract_version": bound_fanfiction_creative_version(root, manifest),
            "continuity_mode": str(configured.get("continuity_mode") or ""),
            "source_canon_sha256": current.sha256["source_canon"],
            "story_engine_sha256": current.sha256["story_engine"],
        }
    )
    return seal_semantic_document(hydrated)


def hydrate_fanfiction_story_engine_document(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Bind a story-engine proposal to the exact approved source baseline."""

    extensions = payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}
    cli_fields = {"creative_contract_version", "task_type", "continuity_mode", "source_canon_sha256"}
    repeated = sorted(cli_fields & set(extensions))
    if repeated:
        raise AgentProtocolError(
            "fanfiction story engine must not repeat CLI-owned fields: " + ", ".join(repeated)
        )
    try:
        canon = fanfiction_contracts.load_current_fanfiction_source_canon(config, root)
    except fanfiction_contracts.FanfictionContractError as exc:
        raise AgentProtocolError(str(exc)) from exc
    configured = config.data.get("fanfiction")
    configured = configured if isinstance(configured, dict) else {}
    hydrated = json.loads(json.dumps(payload, ensure_ascii=False))
    hydrated["extensions"].update(
        {
            "task_type": "fanfiction_story_engine",
            "creative_contract_version": bound_fanfiction_creative_version(root, manifest),
            "continuity_mode": str(configured.get("continuity_mode") or ""),
            "source_canon_sha256": canon.sha256,
        }
    )
    return seal_semantic_document(hydrated)


def hydrate_fanfiction_design_review_document(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Pin an independent review to the immutable route, engine, and Canon inputs."""

    extensions = payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}
    cli_fields = {
        "task_type",
        "review_target_path",
        "review_target_sha256",
        "story_engine_sha256",
        "source_canon_sha256",
    }
    repeated = sorted(cli_fields & set(extensions))
    if repeated:
        raise AgentProtocolError(
            "fanfiction route review must not repeat CLI-owned fields: " + ", ".join(repeated)
        )
    route = fanfiction_review_route_path(root, manifest)
    try:
        current = fanfiction_contracts.load_current_fanfiction_story_engine_documents(
            config, root
        )
    except fanfiction_contracts.FanfictionContractError as exc:
        raise AgentProtocolError(str(exc)) from exc
    hydrated = json.loads(json.dumps(payload, ensure_ascii=False))
    hydrated["extensions"].update(
        {
            "task_type": "fanfiction_design_review",
            "review_target_path": relative(root, route),
            "review_target_sha256": sha256(route.read_bytes()).hexdigest(),
            "story_engine_sha256": current.sha256["story_engine"],
            "source_canon_sha256": current.sha256["source_canon"],
        }
    )
    return seal_semantic_document(hydrated)


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
        "fanfiction_canon": lambda value, target: fanfiction_contracts.validate_fanfiction_source_canon(
            config, value, target
        ),
        "fanfiction_story_engine": lambda value, target: fanfiction_contracts.validate_fanfiction_story_engine(
            config, root, value, target
        ),
        "fanfiction_design": lambda value, target: validate_fanfiction_design(config, root, value, target),
        "fanfiction_design_review": lambda value, target: validate_fanfiction_design_review(
            config, root, value, manifest, target
        ),
        "fanfiction_future_knowledge_reassessment": lambda value, target: (
            validate_future_knowledge_reassessment(root, value, manifest, target)
        ),
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
        "arc_simulation": lambda value, target: validate_arc_simulation_payload(
            root, value, manifest, target
        ),
        "research_synthesis": validate_research_synthesis,
        "style_analysis": validate_style_analysis,
        "adaptation_analysis": validate_adaptation_analysis,
        "character_interpretation": lambda value, target: validate_open_semantic_task(
            "character_interpretation", value, target
        ),
        "story_architecture_design": lambda value, target: validate_open_semantic_task(
            "story_architecture_design", value, target
        ),
        "chapter_semantic_planning": lambda value, target: validate_open_semantic_task(
            "chapter_semantic_planning", value, target
        ),
        "draft_semantic_review": lambda value, target: validate_open_semantic_task(
            "draft_semantic_review", value, target
        ),
        "prose_revision_review": lambda value, target: validate_open_semantic_task(
            "prose_revision_review", value, target
        ),
        "reader_feedback_analysis": lambda value, target: validate_open_semantic_task(
            "reader_feedback_analysis", value, target
        ),
        "source_discovery_planning": lambda value, target: validate_open_semantic_task(
            "source_discovery_planning", value, target
        ),
        "source_candidate_triage": lambda value, target: validate_open_semantic_task(
            "source_candidate_triage", value, target
        ),
    }
    validators[task_type](payload, errors)
    if task_type in {"research_synthesis", "style_analysis", "adaptation_analysis"}:
        validate_sources(root, payload, manifest, errors, require_hashes=True)


def validate_open_semantic_task(
    task_type: str, payload: dict[str, Any], errors: list[str]
) -> None:
    expected_types = {
        "character_interpretation": "人物理解候选",
        "story_architecture_design": "故事架构设计候选",
        "chapter_semantic_planning": "章节语义规划候选",
        "draft_semantic_review": "章节因果与人物选择审查",
        "prose_revision_review": "文风与表达修订审查",
        "reader_feedback_analysis": "读者反馈分析",
        "source_discovery_planning": "原著资料搜索规划",
        "source_candidate_triage": "原著来源候选筛选",
    }
    errors.extend(validate_semantic_document(payload))
    if errors:
        return
    if payload.get("document_type") != expected_types[task_type]:
        errors.append(f"document_type must be {expected_types[task_type]}")
    extensions = payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}
    if extensions.get("task_type") != task_type:
        errors.append(f"extensions.task_type must be {task_type}")
    if not str(payload.get("body") or "").strip():
        errors.append("semantic document body must not be empty")


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
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{0,79}", option_id) or option_id in option_ids:
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


def validate_fanfiction_design(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    errors: list[str],
) -> None:
    fanfiction_contracts.validate_fanfiction_route_contract(config, root, payload, errors)


def validate_fanfiction_design_review(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    role: dict[str, Any] = (
        manifest["role"]
        if isinstance(manifest, dict) and isinstance(manifest.get("role"), dict)
        else {}
    )
    if role.get("id") != "fanfiction_route_reviewer" or role.get("independence_mode") != "isolated_review":
        errors.append("fanfiction route review must use the isolated fanfiction_route_reviewer role")
    try:
        route = fanfiction_review_route_path(root, manifest)
    except ValueError as exc:
        errors.append(str(exc))
        return
    try:
        current = fanfiction_contracts.load_current_fanfiction_story_engine_documents(
            config, root
        )
    except fanfiction_contracts.FanfictionContractError as exc:
        errors.append(str(exc))
        return
    fanfiction_contracts.validate_fanfiction_review_contract(
        payload,
        errors,
        source_canon_sha256=current.sha256["source_canon"],
        story_engine_sha256=current.sha256["story_engine"],
        review_target_path=relative(root, route),
        review_target_sha256=sha256(route.read_bytes()).hexdigest(),
    )
    if not errors:
        fanfiction_contracts.validate_creative_review_coverage(
            payload, read_json(route, {}), current.story_engine, errors
        )


def validate_sources(
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
    *,
    require_hashes: bool,
) -> None:
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


def stable_id(value: Any) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9:_-]{1,79}", text):
        return ""
    return text














def valid_progress_window(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(not isinstance(item, bool) and isinstance(item, (int, float)) for item in value)
        and 0 <= float(value[0]) < float(value[1]) <= 1
    )




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
    if task_type == "book_design":
        targets.extend(sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")))
        for optional in ("factions", "locations"):
            if optional in payload:
                targets.append(root / "10_bible" / f"{optional}.json")
        if (root / "10_bible" / "fanfiction" / "story_engine.json").is_file():
            targets.append(root / "10_bible" / "fanfiction" / "book_design_dependency.json")
    if task_type == "character_expression_review":
        targets.append(
            root
            / "50_workbench"
            / "character_reviews"
            / character_review_report_name(scope or {})
        )
    if task_type == "fanfiction_design":
        targets.append(root / "10_bible" / "fanfiction" / "fanfiction_bible.json")
    if task_type in {
        "character_interpretation",
        "story_architecture_design",
        "chapter_semantic_planning",
        "draft_semantic_review",
        "prose_revision_review",
        "reader_feedback_analysis",
        "source_discovery_planning",
        "source_candidate_triage",
        "fanfiction_future_knowledge_reassessment",
    }:
        targets.append(semantic_task_target(root, task_type, payload))
    return list(dict.fromkeys(targets))


def semantic_task_target(root: Path, task_type: str, payload: dict[str, Any]) -> Path:
    artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else {}
    scope = artifact.get("scope") if isinstance(artifact.get("scope"), dict) else {}
    if task_type == "fanfiction_future_knowledge_reassessment":
        extensions = (
            payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}
        )
        trigger_id = str(extensions.get("trigger_id") or "")
        return future_knowledge_approved_target(
            root,
            trigger_id=trigger_id,
            candidate_scope=scope,
        )
    chapter = int(scope.get("chapter_number") or 0)
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(artifact.get("artifact_id") or "semantic"))
    directories = {
        "character_interpretation": root / "10_bible" / "semantic" / "人物理解",
        "story_architecture_design": root / "20_outline" / "semantic" / "全书架构",
        "chapter_semantic_planning": root
        / "20_outline"
        / "semantic"
        / "章节规划"
        / f"ch{chapter:03d}",
        "draft_semantic_review": root
        / "50_workbench"
        / "semantic_reviews"
        / f"ch{chapter:03d}"
        / "因果与人物选择",
        "prose_revision_review": root
        / "50_workbench"
        / "semantic_reviews"
        / f"ch{chapter:03d}"
        / "文风与表达",
        "reader_feedback_analysis": root / "50_workbench" / "读者反馈" / "分析",
        "source_discovery_planning": root / "50_workbench" / "同人原著资料" / "搜索规划",
        "source_candidate_triage": root / "50_workbench" / "同人原著资料" / "来源筛选",
    }
    return directories[task_type] / f"{token[:120]}.json"








def write_targets(
    config: ConfigDocument,
    root: Path,
    task_type: str,
    payload: dict[str, Any],
    *,
    scope: dict[str, Any] | None = None,
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None,
) -> None:
    if task_type in {
        "character_interpretation",
        "story_architecture_design",
        "chapter_semantic_planning",
        "draft_semantic_review",
        "prose_revision_review",
        "reader_feedback_analysis",
        "source_discovery_planning",
        "source_candidate_triage",
        "fanfiction_future_knowledge_reassessment",
    }:
        document = seal_semantic_document(payload)
        if TASK_SPECS[task_type]["human"]:
            envelope = document["artifact"]
            decision = build_human_decision(
                decision_id="decision_" + semantic_json_hash(
                    {"target": envelope["artifact_id"], "hash": envelope["content_sha256"]}
                )[:24],
                target_id=str(envelope["artifact_id"]),
                target_sha256=str(envelope["content_sha256"]),
                decision="approve",
                decided_by="human",
                reason="人工批准该语义文档进入其声明的规划或研究层；不自动改变正文。",
                scope=dict(envelope.get("scope") or {"kind": "project"}),
            )
            document = approved_semantic_document(document, decision=decision)
        else:
            document["artifact"]["state"] = "reviewed"
            document = seal_semantic_document(document)
        write_json(semantic_task_target(root, task_type, document), document)
        return
    if task_type == "book_ideation":
        write_book_ideation_decision(root, payload)
        return
    if task_type == "book_design":
        basis_before = current_basis_hashes(root)
        write_book_design_targets(
            root,
            payload,
            current_fanfiction=current_fanfiction,
        )
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
        canonical_candidate = json.loads(json.dumps(payload, ensure_ascii=False))
        canonical_candidate["extensions"]["rights_declarations"] = fanfiction_rights_declarations(root)
        canonical_candidate["extensions"]["rights_policy"] = {
            "advisory_only": True,
            "blocks_creation": False,
            "blocks_export": False,
            "statement": "Rights entries are user declarations and are not legal verification.",
        }
        canonical_candidate = seal_semantic_document(canonical_candidate)
        envelope = canonical_candidate["artifact"]
        decision = build_human_decision(
            decision_id="decision_" + semantic_json_hash(
                {"target": envelope["artifact_id"], "hash": envelope["content_sha256"]}
            )[:24],
            target_id=str(envelope["artifact_id"]),
            target_sha256=str(envelope["content_sha256"]),
            decision="approve",
            decided_by="human",
            reason="人工批准项目原著基线 Canon。",
            scope={"kind": "project"},
        )
        canonical = approved_semantic_document(canonical_candidate, decision=decision)
        write_json(root / "10_bible" / "fanfiction" / "source_canon.json", canonical)
        materialize_fanfiction_source_canon(root, canonical)
        mark_project_intelligence_applied(root, "fanfiction_canon", canonical)
        append_creation_event(root, "fanfiction_canon_applied", canonical)
        sync_fanfiction_source_canon(config)
        return
    if task_type == "fanfiction_story_engine":
        candidate = seal_semantic_document(payload)
        envelope = candidate["artifact"]
        decision = build_human_decision(
            decision_id="decision_" + semantic_json_hash(
                {"target": envelope["artifact_id"], "hash": envelope["content_sha256"]}
            )[:24],
            target_id=str(envelope["artifact_id"]),
            target_sha256=str(envelope["content_sha256"]),
            decision="approve",
            decided_by="human",
            reason="人工批准同人故事发动机、独立长期目标与原著人物自主性边界。",
            scope={"kind": "project"},
        )
        canonical = approved_semantic_document(candidate, decision=decision)
        write_json(root / "10_bible" / "fanfiction" / "story_engine.json", canonical)
        mark_project_intelligence_applied(root, "fanfiction_story_engine", canonical)
        append_creation_event(root, "fanfiction_story_engine_applied", canonical)
        return
    if task_type == "fanfiction_design":
        candidate = seal_semantic_document(payload)
        envelope = candidate["artifact"]
        decision = build_human_decision(
            decision_id="decision_" + semantic_json_hash(
                {"target": envelope["artifact_id"], "hash": envelope["content_sha256"]}
            )[:24],
            target_id=str(envelope["artifact_id"]),
            target_sha256=str(envelope["content_sha256"]),
            decision="approve",
            decided_by="human",
            reason="人工批准该同人路线、分歧边界与跨界兼容原则。",
            scope={"kind": "project"},
        )
        canonical = approved_semantic_document(candidate, decision=decision)
        write_json(root / "10_bible" / "fanfiction" / "fanfiction_bible.json", canonical)
        mark_project_intelligence_applied(root, "fanfiction_design", canonical)
        state_path = root / "30_state" / "novel_state.json"
        state = read_json(state_path, {})
        state = state if isinstance(state, dict) else {}
        markers = state.get("project_intelligence")
        markers = dict(markers) if isinstance(markers, dict) else {}
        if not isinstance(markers.get("book_design"), dict) or markers["book_design"].get(
            "status"
        ) != "applied":
            markers["book_design"] = {"status": "required"}
        state["project_intelligence"] = markers
        write_json(state_path, state)
        append_creation_event(root, "fanfiction_design_applied", canonical)
        return
    if task_type == "arc_simulation":
        mark_overlapping_arc_simulations_stale(
            root,
            from_chapter=int(payload["from_chapter"]),
            to_chapter=int(payload["to_chapter"]),
        )
        write_arc_causal_simulation(root, payload)
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


def write_book_design_targets(
    root: Path,
    payload: dict[str, Any],
    *,
    current_fanfiction: fanfiction_contracts.CurrentFanfictionDocuments | None = None,
) -> None:
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
    if current_fanfiction is not None:
        write_json(
            root / "10_bible" / "fanfiction" / "book_design_dependency.json",
            {
                "schema": "fanfiction_book_design_dependency_v2",
                "fanfiction_chain": (
                    fanfiction_contracts.current_fanfiction_chain_binding(
                        root, current_fanfiction
                    )
                ),
                "book_design_projection_sha256": semantic_json_hash(payload),
            },
        )


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
            "只读取已绑定、已提取且满足当前设计核心覆盖的中文资料包；以自然中文正文表达项目原著基线，"
            "只有需进入 Canon、图谱、检索或依赖传播的可断言内容才拆成 claim。每条 claim 使用其资料源"
            "命名空间并引用 evidence_reference_v1；不保存连续原文，不自行扩大资料范围。需要限定适用域时，"
            "只在 claim.extensions 使用 source_ids、character_ids、event_ids、volume_ids、arc_ids、"
            "chapter_numbers、from_chapter、to_chapter；多个已声明维度同时满足才适用。人物、能力、地点、"
            "组织或能量术语的可检索身份统一写入 extensions.identity，字段必须恰为 identity_id、kind、"
            "display_name、source_id，kind 只允许 character、ability、location、organization、energy。"
        ),
        "fanfiction_story_engine": (
            "把批准的原著基线转成可持续的中文长篇故事发动机。extensions.route_family 必须明确选择 "
            "oc_si_progression、canon_character_centered 或 hybrid；按任务输入 creative_requirements_by_route "
            "中该路线对应的连续性合同形成主张。新增阅读价值可以来自关系、视角、人物理解或未展开情节。"
            "不得把所有模式都写成改命、升级或原作结束后的原创主线。资料范围不是人物知识，"
            "不得把作者掌握的后期事实自动交给角色。claim 适用域和跨来源实体身份只能使用正式 "
            "extensions.source_ids/character_ids/event_ids/volume_ids/arc_ids/chapter_numbers/from_chapter/"
            "to_chapter 与 extensions.identity(identity_id,kind,display_name,source_id)；多个适用维度按 AND。"
            "CLI 会绑定 Canon、连续性和哈希。"
        ),
        "fanfiction_design": (
            "基于已批准故事发动机和输入 creative_requirements 建立故事切入点、人物知识边界与原著人物职责。"
            "按连续性模式回答必需创作问题。用 event_disposition_applicability 的 status、reason、basis_claim_ids "
            "说明事件处置是否适用，不适用必须引用当前路线或发动机主张供独立复核。对适用的原著事件"
            "建立命运主张；extensions 必须列出非空责任承担、一阶影响和二阶影响稳定 claim 引用，并说明依赖。"
            "未来知识必须有每次重大分歧后的独立退化机制，可靠性结果只允许仍可靠、部分可靠、已失效或反向误导。"
            "触发联动合同时 extensions.crossover.topology 只允许 "
            "fixed_host、fusion_world 或 sequential_worlds，并填写 default_host_source_id 与非空 transfers；每个 "
            "transfer 用 configured source_id 和实际 payload_kinds 声明 character、body_or_soul、ability、"
            "item_or_contract、knowledge、organization 或 world_rule。sequential_worlds 的每条 transfer 还必须用"
            "非空 volume_ids 声明实际适用卷；fixed_host/fusion_world 的 transfer volume_ids 只能缺省或为 null。"
            "主世界适配器只覆盖实际 transfers 引用的来源，"
            "并以 payload_kinds、host_source_id、volume_ids 绑定实际载荷和宿主范围；fixed_host 至少有一个非宿主"
            "来源转入且禁止宿主自转移，fusion_world 至少有两个实际参与来源并说明世界规则优先级。"
            "sequential_worlds 先在 extensions.crossover.volume_ids 声明适用卷域，再以卷宿主世界 claim 为每个"
            "声明卷恰好指定一个 host_source_id。按 transfer 卷域与卷宿主归并实际 source-volume-host interaction，"
            "每个实际 interaction 恰好一个载荷精确匹配的适配器，每卷至少一个实际 interaction；不要求无关来源与卷的笛卡尔积。"
            "章节实际引用的转移能力或人物必须同时批准引用适配器，适配器用 depends_on_claims 绑定规则。"
            "选中的当前宿主、章节和载荷规则要覆盖激活、作用对象、补充、代价、限制、当地反制与后果；未来章规则不能补足当前章。"
            "sequential_worlds 后续卷必须引用跨卷延续后果主张，用 volume_ids 和 depends_on_claims 表达范围与因果。"
            "该主张只是计划：实际状态由 final 证据验证的 world_deltas.fact_id 使用同一 claim_id 更新，value 用自然语言记录"
            "当前后果，包括解除或改变；正文未发生不得生成事实。"
            "跨界宪法 topics 按实际载荷派生，不做全量主题集、N×N 数值"
            "换算或导入未批准元素。路线 claim 的适用域只能使用 source_ids、character_ids、event_ids、"
            "volume_ids、arc_ids、chapter_numbers、from_chapter、to_chapter，所有声明维度按 AND。人物、能力、"
            "地点、组织和能量名的结构化身份只写 extensions.identity(identity_id,kind,display_name,source_id)，"
            "不得使用旧式扁平 identity_kind/display_name。"
        ),
        "fanfiction_design_review": (
            "作为与路线生成隔离的独立复核者，按输入 creative_requirements 的 independent_review_focus 填写 extensions.creative_coverage：基线、适用的分歧因果、"
            "适用的未来知识退化、原著人物目标与拒绝权、事件处置适用性及其主张依据、新增阅读价值、主角资源垄断、"
            "跨界规则、原著复演风险和中文长篇卷级可持续性。跨界时逐项核对 fixed_host、fusion_world 或 "
            "sequential_worlds 的宿主规则、transfers 中实际 payload_kinds 的派生主题、实际来源适配器，以及"
            "适配器 payload_kinds、host_source_id、volume_ids 与 transfer/宿主卷域一致；还要核对 fixed_host "
            "无宿主自转移、fusion_world 至少两个实际参与来源、sequential_worlds 的 "
            "extensions.crossover.volume_ids 中每个声明卷恰好一个卷宿主世界，并按 transfer.volume_ids 检查"
            "每个实际 source-volume-host interaction 恰好一个 payload 精确匹配的适配器、每卷至少一个实际"
            "interaction；不要求无关来源与卷的笛卡尔积。extensions.verdict 只允许 "
            "pass、need_human、"
            "reject；阻断意见用 severity=blocking 的语义主张表达。复核不能修改路线或代替人工批准。"
        ),
        "fanfiction_future_knowledge_reassessment": (
            "作为与章节作者和路线生成隔离的复核者，只评估 workflow 声明的重大分歧触发和知识范围。"
            "每个知识 claim 恰好产生一条未来知识可靠性主张，reliability 只能是仍可靠、部分可靠、"
            "已失效或反向误导；绑定 trigger_id、trigger_sha256、workflow 输入 hashes、knowledge_claim_id、"
            "从下一章开始的 knowledge_range，以及原知识和分歧 claim 依赖。只生成候选，不自动批准或修改 Canon。"
        ),
        "book_design": (
            "明确读者承诺、核心卖点、世界规则、主角欲望与缺陷、长期冲突、升级方式和结局边界。"
            "必须建立 story_engine_contract_v1：读者幻想、持续推进方式、人物或关系变化、长线问题、"
            "分阶段兑现、载体调色板和主题载体限制；progression_loop 可说明理解、关系或处境变化，不强求升级与循环。每个重要人物都要有稳定 ID、目标、缺陷、关系与可观察的人物弧。"
        ),
        "character_expression_design": (
            "把人物设定转成可观察合同：感知偏向、决策习惯、语言层级、对话策略、情绪泄漏、"
            "身体反应、社会面具、私欲、矛盾与对照；示例只用于校准，不能当口头禅模板。"
        ),
        "character_expression_review": (
            "逐章检查声音匹配、对白可互换、人物工具化、具身存在、叙述者代替人物解释和说明式对白。"
            "问题结论必须引用 hash 绑定的精确 span；证据不足时明确 insufficient。"
        ),
        "arc_simulation": (
            "对声明窗口进行角色因果模拟：逐个写明主角目标、对手议程、主要人物私欲与拒绝点、知识边界、"
            "场外行动、资源/关系变化、碰撞点和逐章因果义务。模拟是规划约束，不是正文顺序模板或世界事实。"
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
        SEMANTIC_DOCUMENT_SCHEMA: (
            "只写 semantic_document_v1 JSON。正文使用自然中文，document_type 必须符合任务；"
            "可断言内容写入 claims，解释和创作建议保留在正文或 extensions。每条 Canon claim 必须引用"
            "本工单声明的 evidence_reference_v1；artifact.content_sha256 可留空，由 CLI 封存。"
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
        design_field_contract(task_type),
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
    input_paths: list[str],
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
    if task_type == "fanfiction_story_engine":
        return (
            f"longform-engine fanfiction story-engine-validate project.yaml --file {candidate}",
            f"longform-engine fanfiction story-engine-apply project.yaml --file {candidate} --approved-by human",
            "longform-engine fanfiction story-engine-task project.yaml",
        )
    if task_type == "fanfiction_design":
        review_candidate = (
            "50_workbench/intelligence_candidates/"
            "fanfiction_design_review.project.candidate.json"
        )
        return (
            f"longform-engine fanfiction design-validate project.yaml --file {candidate}",
            "longform-engine fanfiction design-apply project.yaml "
            f"--file {candidate} --review {review_candidate} --approved-by human",
            "longform-engine fanfiction design-task project.yaml",
        )
    if task_type == "fanfiction_design_review":
        if not input_paths:
            raise ValueError("fanfiction_design_review commands require a route input")
        route = input_paths[0]
        return (
            f"longform-engine fanfiction design-review-validate project.yaml --file {candidate}",
            "longform-engine fanfiction design-apply project.yaml "
            f"--file {route} --review {candidate} --approved-by human",
            f"longform-engine fanfiction design-review-task project.yaml --file {route}",
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
    source_readiness = fanfiction_source_readiness(config)
    upgrade_status = source_upgrade_status(config)
    return {
        "schema": "fanfiction_status_v2",
        "creation_mode": str(config.data.get("creation", {}).get("mode") or "original"),
        "continuity_mode": str(config.data.get("fanfiction", {}).get("continuity_mode") or ""),
        "source_count": len(sources),
        "canon_status": str((markers.get("fanfiction_canon") or {}).get("status") or "not_applied"),
        "story_engine_status": str(
            (markers.get("fanfiction_story_engine") or {}).get("status") or "not_applied"
        ),
        "design_status": str((markers.get("fanfiction_design") or {}).get("status") or "not_applied"),
        "design_review_status": fanfiction_design_review_status(config, root),
        "rights_advisory_only": True,
        "rights_warnings": rights_warnings,
        "source_coverage_ready": source_readiness["ready"],
        "source_coverage_errors": source_readiness["errors"],
        "source_coverage_next_command": source_readiness["next_command"],
        "source_upgrade_available": upgrade_status["upgrade_available"],
        "source_upgrades": upgrade_status["works"],
        "ready": readiness.ready,
        "next_task_type": readiness.required_task_type,
    }


def fanfiction_design_review_status(config: ConfigDocument, root: Path) -> str:
    try:
        fanfiction_contracts.load_current_fanfiction_documents(config, root)
    except fanfiction_contracts.FanfictionContractError as exc:
        current_error = exc.code
    else:
        return "applied"
    active = [
        item
        for item in list_manifests(root, chapter_number=0)
        if item.get("task_type") == "fanfiction_design_review"
        and item.get("status") not in {"applied", "superseded", "rolled_back"}
    ]
    return str(active[-1].get("status") or "awaiting_agent") if active else current_error


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
