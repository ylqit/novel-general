"""Agent task manifest protocol for host-agent creative work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import gzip
from hashlib import sha256
import json
import re

from longform_engine.agent_protocols import (
    BOUNDARY_PROFILE_HASH,
    BOUNDARY_PROFILE_ID,
    BOUNDARY_PROFILE_VERSION,
    HARD_BOUNDARIES,
    output_protocol_for_task,
)
from longform_engine.roles import (
    RoleRegistryError,
    load_role_registry,
    validate_manifest_role_metadata,
    validate_role_task_coverage,
)
from longform_engine.prompting import (
    PromptCompilationError,
    load_project_prompt_overlay,
    resolve_context_budget_contract,
)
from longform_engine.story_profiles import project_active_facet_adapters
from longform_engine.storage import atomic_write_text


AGENT_TASK_SCHEMA_VERSION = 4
AGENT_TASK_INDEX_SCHEMA = "agent_task_index_v4"
AGENT_TASK_EVENT_SCHEMA = "agent_task_event_v4"
EVENT_SEGMENT_SCHEMA = "agent_task_event_segments_v1"
EVENT_ROTATE_BYTES = 5 * 1024 * 1024
EVENT_ROTATE_LINES = 10_000
SUPPORTED_AGENT_TASK_SCHEMA_VERSIONS = (4,)
AGENT_TASK_STATUSES = (
    "awaiting_agent",
    "submitted",
    "validated",
    "approved",
    "invalid",
    "applied",
    "superseded",
    "rolled_back",
)
TERMINAL_TASK_STATUSES = frozenset({"invalid", "applied", "superseded", "rolled_back"})
CHAPTER_CANDIDATE_TASK_TYPES = frozenset({"chapter_write", "repair", "humanize", "content_expand"})
DEFAULT_FORBIDDEN_CONTEXT = (
    "40_manuscript/final/",
    "50_workbench/agent_drafts/ (except the declared output)",
    "50_workbench/research_inbox/ (unless explicitly declared)",
    "60_rag/query_cache/",
    "70_runtime/db/",
)
TASK_RELATION_FIELDS = (
    "consumes_task_id",
    "consumed_by_task_id",
    "satisfied_by_result_sha256",
    "supersedes_task_ids",
)
class AgentTaskContractError(ValueError):
    """Raised before an invalid Agent task can enter the project task index."""


@dataclass(frozen=True)
class AgentTaskManifest:
    """Stable task contract consumed by Codex, Claude, GUI, and API surfaces."""

    schema_version: int
    task_id: str
    task_type: str
    scope: dict[str, Any]
    role: dict[str, Any]
    io: dict[str, Any]
    policy: dict[str, Any]
    commands: dict[str, str]
    created_at: str


def manifest_chapter_number(manifest: dict[str, Any]) -> int:
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    return int(scope.get("chapter_number") or 0)


def manifest_role(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("role")
    return value if isinstance(value, dict) else {}


def manifest_input_records(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    io = manifest.get("io") if isinstance(manifest.get("io"), dict) else {}
    values = io.get("inputs") if isinstance(io.get("inputs"), list) else []
    return [item for item in values if isinstance(item, dict)]


def manifest_input_paths(manifest: dict[str, Any]) -> list[str]:
    return [str(item.get("path") or "") for item in manifest_input_records(manifest)]


def manifest_output(manifest: dict[str, Any]) -> dict[str, Any]:
    io = manifest.get("io") if isinstance(manifest.get("io"), dict) else {}
    value = io.get("output")
    return value if isinstance(value, dict) else {}


def manifest_policy(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest.get("policy")
    return value if isinstance(value, dict) else {}


def manifest_context(manifest: dict[str, Any]) -> dict[str, Any]:
    value = manifest_policy(manifest).get("context")
    return value if isinstance(value, dict) else {}


def manifest_commands(manifest: dict[str, Any]) -> dict[str, str]:
    value = manifest.get("commands")
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ManifestValidationResult:
    """Validation result for an AgentTaskManifest contract check."""

    ok: bool
    task_id: str
    task_type: str
    strict: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AgentTaskLifecycleResult:
    """Result returned after updating Agent task lifecycle state."""

    task_id: str
    from_status: str
    to_status: str
    event_file: str


TASK_CONTRACTS: dict[str, dict[str, tuple[str, ...]]] = {
    "book_ideation": {
        "scope_kinds": ("project",),
        "schemas": (output_protocol_for_task("book_ideation"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "chapter_write": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("chapter_write"),),
        "output_prefixes": ("50_workbench/agent_drafts/",),
        "validate_prefixes": ("longform-engine draft submit ",),
        "apply_prefixes": ("longform-engine chapter finalize ",),
        "failure_prefixes": ("longform-engine production next ",),
    },
    "repair": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("repair"),),
        "output_prefixes": ("50_workbench/repair_candidates/",),
        "validate_prefixes": ("longform-engine draft submit ",),
        "apply_prefixes": ("longform-engine chapter finalize ",),
        "failure_prefixes": (
            "longform-engine agent-task brief ",
            "longform-engine repair candidate-task ",
            "longform-engine editorial need-human ",
        ),
    },
    "repair_plan_synthesis": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("repair_plan_synthesis"),),
        "output_prefixes": ("50_workbench/repair_plans/",),
        "validate_prefixes": ("longform-engine repair synthesis-validate ",),
        "apply_prefixes": ("longform-engine repair candidate-task ",),
        "failure_prefixes": (
            "longform-engine repair synthesis-task ",
            "longform-engine editorial need-human ",
        ),
    },
    "humanize": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("humanize"),),
        "output_prefixes": ("50_workbench/repair_candidates/",),
        "validate_prefixes": ("longform-engine creative humanize-check ",),
        "apply_prefixes": ("longform-engine draft submit ",),
        "failure_prefixes": ("longform-engine creative humanize-task ",),
    },
    "humanize_semantic_review": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("humanize_semantic_review"),),
        "output_prefixes": ("50_workbench/humanizer_tasks/",),
        "validate_prefixes": ("longform-engine creative humanize-semantic-validate ",),
        "apply_prefixes": ("longform-engine draft submit ",),
        "failure_prefixes": (
            "longform-engine creative humanize-task ",
            "longform-engine editorial need-human ",
        ),
    },
    "reader_payoff_review": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("reader_payoff_review"),),
        "output_prefixes": ("50_workbench/quality_reviews/",),
        "validate_prefixes": ("longform-engine quality payoff-validate ",),
        "apply_prefixes": ("longform-engine chapter finalize ",),
        "failure_prefixes": (
            "longform-engine production next ",
            "longform-engine editorial need-human ",
        ),
    },
    "content_expand": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("content_expand"),),
        "output_prefixes": ("50_workbench/repair_candidates/",),
        "validate_prefixes": ("longform-engine creative expand-check ",),
        "apply_prefixes": ("longform-engine draft submit ",),
        "failure_prefixes": ("longform-engine creative expand-task ",),
    },
    "chapter_semantic": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("chapter_semantic"),),
        "output_prefixes": ("50_workbench/semantic_tasks/",),
        "validate_prefixes": ("longform-engine chapter semantic-validate ",),
        "apply_prefixes": ("longform-engine chapter semantic-apply ",),
        "failure_prefixes": ("longform-engine chapter semantic-task ",),
    },
    "editorial_review": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("editorial_review"),),
        "output_prefixes": ("50_workbench/editorial_reviews/results/",),
        "validate_prefixes": ("longform-engine editorial submit-review ",),
        "apply_prefixes": ("longform-engine editorial aggregate ",),
        "failure_prefixes": ("longform-engine editorial need-human ",),
    },
    "pacing_review": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("pacing_review"),),
        "output_prefixes": ("50_workbench/gate_artifacts/",),
        "validate_prefixes": ("longform-engine pacing semantic-validate ",),
        "apply_prefixes": ("longform-engine pacing semantic-apply ",),
        "failure_prefixes": ("longform-engine pacing semantic-task ",),
    },
    "semantic_review": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("semantic_review"),),
        "output_prefixes": ("50_workbench/gate_artifacts/",),
        "validate_prefixes": ("longform-engine gate semantic-validate ",),
        "apply_prefixes": ("longform-engine gate semantic-apply ",),
        "failure_prefixes": ("longform-engine gate semantic-task ",),
    },
    "book_design": {
        "scope_kinds": ("project",),
        "schemas": (output_protocol_for_task("book_design"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "character_expression_design": {
        "scope_kinds": ("project",),
        "schemas": (output_protocol_for_task("character_expression_design"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": (
            "longform-engine character design-validate ",
            "longform-engine intelligence validate ",
        ),
        "apply_prefixes": (
            "longform-engine character design-apply ",
            "longform-engine intelligence apply ",
        ),
        "failure_prefixes": (
            "longform-engine character design-task ",
            "longform-engine intelligence task ",
        ),
    },
    "character_expression_review": {
        "scope_kinds": ("range",),
        "schemas": (output_protocol_for_task("character_expression_review"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": (
            "longform-engine character audit-validate ",
            "longform-engine intelligence validate ",
        ),
        "apply_prefixes": (
            "longform-engine character audit-apply ",
            "longform-engine intelligence apply ",
        ),
        "failure_prefixes": (
            "longform-engine character audit-task ",
            "longform-engine intelligence task ",
        ),
    },
    "outline_design": {
        "scope_kinds": ("project",),
        "schemas": (output_protocol_for_task("outline_design"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "outline_extension": {
        "scope_kinds": ("range",),
        "schemas": (output_protocol_for_task("outline_extension"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "chapter_direction": {
        "scope_kinds": ("chapter",),
        "schemas": (output_protocol_for_task("chapter_direction"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "outline_revision": {
        "scope_kinds": ("range",),
        "schemas": (output_protocol_for_task("outline_revision"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "research_synthesis": {
        "scope_kinds": ("project", "range"),
        "schemas": (output_protocol_for_task("research_synthesis"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "style_analysis": {
        "scope_kinds": ("project", "range"),
        "schemas": (output_protocol_for_task("style_analysis"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "adaptation_analysis": {
        "scope_kinds": ("project", "range"),
        "schemas": (output_protocol_for_task("adaptation_analysis"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence task ",),
    },
    "fanfiction_canon": {
        "scope_kinds": ("project",),
        "schemas": (output_protocol_for_task("fanfiction_canon"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine fanfiction canon-validate ", "longform-engine intelligence validate "),
        "apply_prefixes": ("longform-engine fanfiction canon-apply ", "longform-engine intelligence apply "),
        "failure_prefixes": ("longform-engine fanfiction canon-task ", "longform-engine intelligence task "),
    },
    "fanfiction_design": {
        "scope_kinds": ("project",),
        "schemas": (output_protocol_for_task("fanfiction_design"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine fanfiction design-validate ", "longform-engine intelligence validate "),
        "apply_prefixes": ("longform-engine fanfiction design-apply ", "longform-engine intelligence apply "),
        "failure_prefixes": ("longform-engine fanfiction design-task ", "longform-engine intelligence task "),
    },
    "design_semantic_compile": {
        "scope_kinds": ("project", "chapter", "range"),
        "schemas": (output_protocol_for_task("design_semantic_compile"),),
        "output_prefixes": ("50_workbench/intelligence_candidates/",),
        "validate_prefixes": ("longform-engine intelligence compile-validate ",),
        "apply_prefixes": ("longform-engine intelligence apply ",),
        "failure_prefixes": ("longform-engine intelligence compile-task ",),
    },
}

CANONICAL_OUTPUT_PREFIXES = (
    "10_bible/",
    "20_outline/",
    "40_manuscript/final/",
    "60_rag/",
    "30_state/",
    "70_runtime/db/",
    "70_runtime/provenance/",
)
CANONICAL_OUTPUT_FILES = (
    "30_state/story_graph.json",
)


def build_manifest(
    root: Path,
    *,
    task_type: str,
    chapter_number: int | None,
    input_files: Iterable[str | Path],
    allowed_output_paths: Iterable[str | Path],
    output_schema: str,
    validate_command: str,
    apply_command: str = "",
    failure_next_command: str = "",
    status: str = "awaiting_agent",
    task_id: str | None = None,
    hard_boundaries: Iterable[str] = HARD_BOUNDARIES,
    scope: dict[str, Any] | None = None,
    canonical_targets: Iterable[str | Path] = (),
    requires_human_apply: bool = False,
    context_policy: dict[str, Any] | None = None,
    role_id: str = "",
) -> dict[str, Any]:
    """Create one immutable AgentTaskManifest v4 without duplicated projections."""

    normalized_type = normalize_token(task_type)
    normalized_status = normalize_status(status)
    normalized_scope = normalize_scope(scope, chapter_number=chapter_number)
    scope_kind = str(normalized_scope["kind"])
    normalized_chapter = int(normalized_scope.get("chapter_number") or 0)
    if scope_kind == "chapter":
        scope_token = f"ch{normalized_chapter:03d}"
    elif scope_kind == "range":
        scope_token = f"ch{int(normalized_scope['from_chapter']):03d}-ch{int(normalized_scope['to_chapter']):03d}"
    else:
        scope_token = "project"
    id_revision = "v4"
    manifest_id = task_id or f"{normalized_type}:{scope_token}:{id_revision}"
    normalized_inputs = normalize_paths(root, input_files)
    try:
        registry = validate_role_task_coverage(set(TASK_CONTRACTS))
        role = registry.resolve(normalized_type, declared_role_id=role_id)
    except RoleRegistryError as exc:
        raise AgentTaskContractError(f"Cannot build Agent task role contract: {exc}") from exc
    normalized_policy = normalize_context_policy(
        root,
        context_policy,
        input_files=normalized_inputs,
        task_type=normalized_type,
        scope=normalized_scope,
    )
    selection = registry.select_prompt(
        normalized_type,
        declared_role_id=role.role_id,
        quality_focus=normalized_policy.get("quality_focus") or [],
        trigger_codes=normalized_policy.get("trigger_codes") or [],
    )
    if normalized_status != "awaiting_agent":
        raise AgentTaskContractError("New immutable manifests must start in task-index state awaiting_agent.")
    if tuple(str(item).strip().lower() for item in hard_boundaries) != HARD_BOUNDARIES:
        raise AgentTaskContractError("Agent tasks must use the registered canonical boundary profile.")
    normalized_outputs = normalize_paths(root, allowed_output_paths)
    if len(normalized_outputs) != 1:
        raise AgentTaskContractError("AgentTaskManifest v4 requires exactly one output path.")
    try:
        overlay = load_project_prompt_overlay(root, role)
    except PromptCompilationError as exc:
        raise AgentTaskContractError(
            f"Cannot build Agent task project overlay: {exc}; "
            f"repair with `{exc.report['repair_command']}`."
        ) from exc
    required = set(normalized_policy.get("required_files") or [])
    input_records: list[dict[str, Any]] = []
    for path_text in normalized_inputs:
        path = resolve_under_root(root, path_text)
        try:
            content = path.read_text(encoding="utf-8").lstrip("\ufeff")
        except UnicodeDecodeError as exc:
            raise AgentTaskContractError(f"Agent input must be UTF-8 text: {path_text}") from exc
        requirement = "required" if path_text in required else "optional"
        reason = "compiled_task_brief" if path_text == normalized_policy.get("compiled_brief") else requirement
        input_records.append(
            {
                "path": path_text,
                "requirement": requirement,
                "sha256": sha256(path.read_bytes()).hexdigest(),
                "characters": len(content),
                "reason": reason,
            }
        )
    role_sections = [
        {"id": section, "sha256": digest}
        for section, digest in zip(selection.role_sections, selection.role_section_hashes, strict=True)
    ]
    playbooks = [
        {
            "id": item.playbook_id,
            "sections": [
                {"id": section, "sha256": digest}
                for section, digest in zip(item.sections, item.section_hashes, strict=True)
            ],
        }
        for item in selection.playbooks
    ]
    return {
            "schema_version": AGENT_TASK_SCHEMA_VERSION,
            "task_id": manifest_id,
            "task_type": normalized_type,
            "scope": normalized_scope,
            "role": {
                "id": role.role_id,
                "version": role.role_version,
                "contract_hash": role.contract_hash,
                "selection_hash": selection.selection_hash,
                "independence_mode": role.independence_mode,
                "overlay_hash": overlay.overlay_hash,
                "sections": role_sections,
                "playbooks": playbooks,
            },
            "io": {
                "inputs": input_records,
                "output": {"path": normalized_outputs[0], "protocol": output_schema},
            },
            "policy": {
                "boundary_profile": {
                    "id": BOUNDARY_PROFILE_ID,
                    "version": BOUNDARY_PROFILE_VERSION,
                    "sha256": BOUNDARY_PROFILE_HASH,
                },
                "canonical_targets": normalize_paths(root, canonical_targets),
                "requires_human_apply": bool(requires_human_apply),
                "context": {
                    "forbidden_paths": list(normalized_policy.get("forbidden_paths") or []),
                    "budget_profile": str(normalized_policy["budget_profile"]),
                    "capacity_units": int(normalized_policy["capacity_units"]),
                    "overflow_policy": str(normalized_policy["overflow_policy"]),
                    "quality_focus": list(normalized_policy.get("quality_focus") or []),
                    "trigger_codes": list(normalized_policy.get("trigger_codes") or []),
                    "active_facets": list(normalized_policy.get("active_facets") or []),
                },
            },
            "commands": {
                "validate": validate_command,
                "apply": apply_command,
                "failure": failure_next_command,
            },
            "created_at": utc_now(),
        }


def write_manifest(
    root: Path,
    manifest: dict[str, Any],
    manifest_file: str | Path,
    *,
    consumes_task_id: str = "",
    supersedes_task_ids: Iterable[str] = (),
) -> str:
    """Persist a manifest and update the project-level read-only index."""

    path = resolve_under_root(root, manifest_file)
    normalized = normalize_manifest(manifest)
    validate_manifest_shape(normalized)
    validation = validate_manifest_strict(root, normalized)
    if not validation.ok:
        details = "; ".join(validation.errors)
        raise AgentTaskContractError(
            f"Agent task contract is invalid for {validation.task_id or '<unknown>'}: {details}"
        )
    output = (normalized["io"].get("output") or {}).get("path")
    if output:
        resolve_under_root(root, output).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(dict(normalized), ensure_ascii=False, indent=2) + "\n")
    register_manifest(
        root,
        normalized,
        path,
        consumes_task_id=consumes_task_id,
        supersedes_task_ids=supersedes_task_ids,
    )
    return str(path)


def register_manifest(
    root: Path,
    manifest: dict[str, Any],
    manifest_file: Path,
    *,
    consumes_task_id: str = "",
    supersedes_task_ids: Iterable[str] = (),
) -> None:
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    if not isinstance(payload, dict):
        payload = new_task_index()
    elif payload.get("schema_version") not in SUPPORTED_AGENT_TASK_SCHEMA_VERSIONS:
        payload = new_task_index()
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    rel_file = relative_path(root, manifest_file)
    existing = next(
        (
            item
            for item in tasks
            if isinstance(item, dict) and item.get("task_id") == manifest["task_id"]
        ),
        None,
    )
    new_status = "awaiting_agent"
    consumed_parent = next(
        (
            item
            for item in tasks
            if isinstance(item, dict) and item.get("task_id") == consumes_task_id
        ),
        None,
    ) if consumes_task_id else None
    if consumes_task_id and consumed_parent is None:
        raise AgentTaskContractError(f"consumed parent task is not indexed: {consumes_task_id}")
    if isinstance(consumed_parent, dict):
        parent_status = normalize_status(str(consumed_parent.get("status") or "awaiting_agent"))
        if parent_status not in {"validated", "approved", "applied"}:
            raise AgentTaskContractError(
                f"consumed parent task must be validated or approved; got {parent_status}"
            )
        parent_result = consumed_parent.get("current_result")
        if not isinstance(parent_result, dict) or parent_result.get("ok") is not True:
            raise AgentTaskContractError("consumed parent task has no valid bound result")
        parent_output = str((load_manifest(root, consumes_task_id).get("io") or {}).get("output", {}).get("path") or "")
        if parent_output not in manifest_input_paths(manifest):
            raise AgentTaskContractError("child task must declare the consumed parent output as an input")
    superseded_ids = sorted({str(item).strip() for item in supersedes_task_ids if str(item).strip()})
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    entry = {
        "task_id": manifest["task_id"],
        "task_type": manifest["task_type"],
        "scope": dict(manifest.get("scope") or {}),
        "chapter_number": int(scope.get("chapter_number") or 0),
        "status": new_status,
        "manifest_file": rel_file,
        "created_at": manifest.get("created_at", ""),
        "updated_at": utc_now(),
        "consumes_task_id": consumes_task_id,
        "consumed_by_task_id": "",
        "satisfied_by_result_sha256": "",
        "supersedes_task_ids": superseded_ids,
    }
    tasks = [item for item in tasks if not (isinstance(item, dict) and item.get("task_id") == manifest["task_id"])]
    tasks.append(entry)
    parent_transition: tuple[str, str] | None = None
    if isinstance(consumed_parent, dict):
        parent_from = normalize_status(str(consumed_parent.get("status") or "awaiting_agent"))
        consumed_parent["status"] = "applied"
        consumed_parent["consumed_by_task_id"] = str(manifest["task_id"])
        consumed_parent["satisfied_by_result_sha256"] = str(
            (consumed_parent.get("current_result") or {}).get("sha256") or ""
        )
        consumed_parent["updated_at"] = utc_now()
        entry["satisfied_by_result_sha256"] = consumed_parent["satisfied_by_result_sha256"]
        parent_transition = (parent_from, "applied")
    superseded_transitions: list[tuple[str, str]] = []
    for item in tasks:
        if not isinstance(item, dict) or item.get("task_id") not in superseded_ids:
            continue
        prior = normalize_status(str(item.get("status") or "awaiting_agent"))
        if prior in {"applied", "rolled_back", "superseded"}:
            continue
        item["status"] = "superseded"
        item["consumed_by_task_id"] = str(manifest["task_id"])
        item["updated_at"] = utc_now()
        superseded_transitions.append((str(item.get("task_id") or ""), prior))
    payload["tasks"] = sorted(
        tasks,
        key=lambda item: (
            int(item.get("chapter_number") or 0) if isinstance(item, dict) else 0,
            str(item.get("task_type") or "") if isinstance(item, dict) else "",
            str(item.get("task_id") or "") if isinstance(item, dict) else "",
        ),
    )
    payload["updated_at"] = utc_now()
    payload["schema_version"] = AGENT_TASK_SCHEMA_VERSION
    payload["schema"] = AGENT_TASK_INDEX_SCHEMA
    payload.setdefault("terminal_counts", {"total": 0, "by_status": {}, "by_type": {}})
    payload.setdefault("archived_chapters", {})
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if existing is None:
        record_task_event(
            root,
            task_id=str(manifest["task_id"]),
            from_status="",
            to_status=new_status,
            command="agent-task create",
            artifact=rel_file,
            result=rel_file,
            consumes_task_id=consumes_task_id,
            satisfied_by_result_sha256=str(entry.get("satisfied_by_result_sha256") or ""),
            supersedes_task_ids=superseded_ids,
        )
    if parent_transition is not None and parent_transition[0] != "applied":
        record_task_event(
            root,
            task_id=consumes_task_id,
            from_status=parent_transition[0],
            to_status="applied",
            command="agent-task child registered",
            artifact=rel_file,
            result=rel_file,
            consumed_by_task_id=str(manifest["task_id"]),
            satisfied_by_result_sha256=str(entry.get("satisfied_by_result_sha256") or ""),
        )
    for task_id, prior in superseded_transitions:
        record_task_event(
            root,
            task_id=task_id,
            from_status=prior,
            to_status="superseded",
            command="agent-task replacement registered",
            artifact=rel_file,
            result=rel_file,
            consumed_by_task_id=str(manifest["task_id"]),
        )


def list_manifests(root: Path, *, chapter_number: int | None = None) -> list[dict[str, Any]]:
    """Return indexed manifest entries without mutating project state."""

    index = read_json(agent_task_index_file(root), default={})
    tasks = index.get("tasks") if isinstance(index, dict) else []
    if not isinstance(tasks, list):
        return []
    result: list[dict[str, Any]] = []
    for item in tasks:
        if not isinstance(item, dict):
            continue
        manifest_path = root / str(item.get("manifest_file") or "")
        payload = read_json(manifest_path, default={})
        if isinstance(payload, dict) and payload.get("schema_version") == AGENT_TASK_SCHEMA_VERSION:
            view = normalize_manifest(payload)
            view["status"] = str(item.get("status") or "awaiting_agent")
            view["manifest_file"] = str(item.get("manifest_file") or "")
            view["updated_at"] = str(item.get("updated_at") or "")
            if isinstance(item.get("current_result"), dict):
                view["current_result"] = dict(item["current_result"])
            for field in TASK_RELATION_FIELDS:
                if item.get(field) not in (None, "", []):
                    view[field] = item[field]
            result.append(view)
        else:
            result.append(dict(item))
    if chapter_number is not None:
        result = [item for item in result if manifest_chapter_number(item) == chapter_number]
    return result


def load_manifest(root: Path, task: str | Path) -> dict[str, Any]:
    """Load a manifest by task_id or project-relative/absolute manifest path."""

    task_text = str(task)
    for entry in list_manifests(root):
        if entry.get("task_id") == task_text:
            return entry
    path = resolve_under_root(root, task)
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        raise ValueError(f"Agent task manifest is not JSON object: {task}")
    normalized = normalize_manifest(payload)
    relative_manifest = relative_path(root, path)
    index = read_json(agent_task_index_file(root), default={})
    entries = index.get("tasks") if isinstance(index, dict) else []
    projection = next(
        (
            item
            for item in entries if isinstance(item, dict)
            and (
                item.get("task_id") == normalized.get("task_id")
                or str(item.get("manifest_file") or "") == relative_manifest
            )
        ),
        None,
    )
    if isinstance(projection, dict):
        normalized["status"] = str(projection.get("status") or "awaiting_agent")
        normalized["manifest_file"] = str(projection.get("manifest_file") or relative_manifest)
        normalized["updated_at"] = str(projection.get("updated_at") or "")
        if isinstance(projection.get("current_result"), dict):
            normalized["current_result"] = dict(projection["current_result"])
        for field in TASK_RELATION_FIELDS:
            if projection.get(field) not in (None, "", []):
                normalized[field] = projection[field]
    return normalized


def update_task_status(
    root: Path,
    task_id: str,
    *,
    to_status: str,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
    current_result: dict[str, Any] | None = None,
) -> AgentTaskLifecycleResult | None:
    """Move an indexed Agent task to a lifecycle status and append an event."""

    normalized_status = normalize_status(to_status)
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    if not isinstance(payload, dict):
        return None
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    selected: dict[str, Any] | None = None
    for item in tasks:
        if isinstance(item, dict) and item.get("task_id") == task_id:
            selected = item
            break
    if selected is None:
        return None
    from_status = normalize_status(str(selected.get("status") or "awaiting_agent"))
    normalized_result = normalize_current_result_binding(current_result) if current_result is not None else None
    binding_changed = normalized_result is not None and selected.get("current_result") != normalized_result
    if from_status == normalized_status and not binding_changed:
        return AgentTaskLifecycleResult(
            task_id=task_id,
            from_status=from_status,
            to_status=normalized_status,
            event_file=str(agent_task_events_file(root)),
        )
    selected["status"] = normalized_status
    if normalized_result is not None:
        selected["current_result"] = normalized_result
    selected["updated_at"] = utc_now()
    payload["updated_at"] = utc_now()
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    event_file = record_task_event(
        root,
        task_id=task_id,
        from_status=from_status,
        to_status=normalized_status,
        command=command,
        artifact=artifact,
        result=result,
    )
    return AgentTaskLifecycleResult(
        task_id=task_id,
        from_status=from_status,
        to_status=normalized_status,
        event_file=str(event_file),
    )


def validate_current_task_result(
    root: Path,
    *,
    chapter_number: int,
    task_type: str,
    output_path: str | Path,
    allowed_statuses: Iterable[str],
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Verify that one current task owns and hash-binds an Agent result."""

    output_text = relative_path(root, output_path)
    normalized_type = normalize_token(task_type)
    allowed = {normalize_status(item) for item in allowed_statuses}
    owners = [
        task
        for task in list_manifests(root, chapter_number=chapter_number)
        if normalize_token(str(task.get("task_type") or "")) == normalized_type
        and output_text == str(manifest_output(task).get("path") or "").replace("\\", "/")
    ]
    matches = [
        task
        for task in owners
        if normalize_status(str(task.get("status") or "awaiting_agent")) in allowed
    ]
    if not matches:
        pending = [
            task
            for task in owners
            if normalize_status(str(task.get("status") or "awaiting_agent"))
            in {"awaiting_agent", "invalid"}
        ]
        if len(pending) == 1:
            matches = pending
    if len(matches) != 1:
        return None, (
            f"Expected exactly one current {normalized_type} task for `{output_text}`; found {len(matches)}.",
        )
    projection = matches[0]
    manifest_file = str(projection.get("manifest_file") or "")
    task = load_manifest(root, manifest_file) if manifest_file else projection
    status = normalize_status(str(task.get("status") or "awaiting_agent"))
    errors: list[str] = []
    if status not in allowed:
        errors.append(
            f"Agent result control-plane status must be one of {', '.join(sorted(allowed))}; got `{status}`. "
            "Run `longform-engine agent-task result-validate ...` first."
        )
    binding = task.get("current_result")
    if not isinstance(binding, dict):
        errors.append("Agent result has no current control-plane hash binding.")
        return task, tuple(errors)
    if binding.get("ok") is not True:
        errors.append("The current Agent result failed control-plane validation.")
    if str(binding.get("path") or "") != output_text:
        errors.append("The control-plane result path does not match the requested domain result.")
    path = resolve_under_root(root, output_text)
    current_hash = sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
    if not current_hash or str(binding.get("sha256") or "") != current_hash:
        errors.append("The Agent result changed after control-plane validation.")
    diagnostic_text = str(binding.get("diagnostic_file") or "")
    if diagnostic_text:
        diagnostic = read_json(resolve_under_root(root, diagnostic_text), default={})
        if (
            not isinstance(diagnostic, dict)
            or diagnostic.get("ok") is not True
            or str(diagnostic.get("task_id") or "") != str(task.get("task_id") or "")
            or str(diagnostic.get("result_file") or "") != output_text
            or str(diagnostic.get("result_sha256") or "") != current_hash
        ):
            errors.append("The Agent result diagnostic is stale or does not match the current task and file.")
    return task, tuple(errors)


def mark_tasks_for_output(
    root: Path,
    *,
    chapter_number: int,
    output_path: str | Path,
    to_status: str,
    command: str,
    result: str | Path = "",
    from_statuses: Iterable[str] | None = None,
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Update every indexed task whose allowed output path matches output_path."""

    output_text = relative_path(root, output_path)
    allowed_from = {normalize_status(item) for item in from_statuses} if from_statuses else None
    results: list[AgentTaskLifecycleResult] = []
    for task in list_manifests(root, chapter_number=chapter_number):
        if output_text != str(manifest_output(task).get("path") or "").replace("\\", "/"):
            continue
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if allowed_from is not None and current not in allowed_from:
            continue
        result_item = update_task_status(
            root,
            str(task.get("task_id") or ""),
            to_status=to_status,
            command=command,
            artifact=output_text,
            result=result,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def tasks_for_output(
    root: Path,
    *,
    chapter_number: int,
    output_path: str | Path,
    task_types: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return indexed tasks that own one candidate output path."""

    output_text = relative_path(root, output_path)
    allowed_types = {normalize_token(item) for item in task_types} if task_types is not None else None
    return [
        task
        for task in list_manifests(root, chapter_number=chapter_number)
        if (allowed_types is None or normalize_token(str(task.get("task_type") or "")) in allowed_types)
        and normalize_status(str(task.get("status") or "awaiting_agent"))
        in {"awaiting_agent", "submitted", "validated", "invalid"}
        and output_text == str(manifest_output(task).get("path") or "").replace("\\", "/")
    ]


def resolve_candidate_task(
    root: Path,
    *,
    chapter_number: int,
    output_path: str | Path,
) -> dict[str, Any]:
    """Resolve exactly one chapter-candidate task or stop before ownership becomes ambiguous."""

    matches = tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=output_path,
        task_types=CHAPTER_CANDIDATE_TASK_TYPES,
    )
    if len(matches) != 1:
        output_text = relative_path(root, output_path)
        raise AgentTaskContractError(
            f"Candidate output must belong to exactly one active chapter task; "
            f"found {len(matches)} for {output_text}."
        )
    return matches[0]


def supersede_other_candidate_tasks(
    root: Path,
    *,
    chapter_number: int,
    current_task_id: str,
    command: str,
    artifact: str | Path,
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Retire earlier prose candidates after a replacement becomes the submitted chapter."""

    results: list[AgentTaskLifecycleResult] = []
    for task in list_manifests(root, chapter_number=chapter_number):
        task_id = str(task.get("task_id") or "")
        if task_id == current_task_id:
            continue
        if normalize_token(str(task.get("task_type") or "")) not in CHAPTER_CANDIDATE_TASK_TYPES:
            continue
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if current in {"applied", "rolled_back", "superseded"}:
            continue
        result_item = update_task_status(
            root,
            task_id,
            to_status="superseded",
            command=command,
            artifact=artifact,
            result=current_task_id,
        )
        if result_item is not None:
            results.append(result_item)
    if results:
        record_supersession_projection(
            root,
            task_id=current_task_id,
            supersedes_task_ids=[item.task_id for item in results],
            command=command,
            artifact=artifact,
        )
    return tuple(results)


def record_supersession_projection(
    root: Path,
    *,
    task_id: str,
    supersedes_task_ids: Iterable[str],
    command: str,
    artifact: str | Path,
) -> None:
    ids = sorted({str(item).strip() for item in supersedes_task_ids if str(item).strip()})
    if not ids:
        return
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(tasks, list):
        return
    selected = next(
        (item for item in tasks if isinstance(item, dict) and item.get("task_id") == task_id),
        None,
    )
    if not isinstance(selected, dict):
        return
    selected["supersedes_task_ids"] = sorted(
        set(str(item) for item in selected.get("supersedes_task_ids") or []) | set(ids)
    )
    selected["updated_at"] = utc_now()
    payload["updated_at"] = utc_now()
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    status = normalize_status(str(selected.get("status") or "awaiting_agent"))
    record_task_event(
        root,
        task_id=task_id,
        from_status=status,
        to_status=status,
        command=command,
        artifact=artifact,
        supersedes_task_ids=ids,
    )


def mark_tasks_for_chapter_type(
    root: Path,
    *,
    chapter_number: int,
    task_types: Iterable[str],
    to_status: str,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
    from_statuses: Iterable[str] | None = None,
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Update indexed tasks for a chapter/type set, useful after canonical apply."""

    allowed_types = {normalize_token(item) for item in task_types}
    allowed_from = {normalize_status(item) for item in from_statuses} if from_statuses else None
    results: list[AgentTaskLifecycleResult] = []
    for task in list_manifests(root, chapter_number=chapter_number):
        if normalize_token(str(task.get("task_type") or "")) not in allowed_types:
            continue
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if allowed_from is not None and current not in allowed_from:
            continue
        result_item = update_task_status(
            root,
            str(task.get("task_id") or ""),
            to_status=to_status,
            command=command,
            artifact=artifact,
            result=result,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def mark_existing_tasks_superseded(
    root: Path,
    *,
    chapter_number: int,
    task_type: str,
    replacement_task_id: str,
    command: str,
    artifact: str | Path,
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Mark older same-lane tasks as superseded when a replacement task is created."""

    results: list[AgentTaskLifecycleResult] = []
    normalized_type = normalize_token(task_type)
    for task in list_manifests(root, chapter_number=chapter_number):
        task_id = str(task.get("task_id") or "")
        if task_id == replacement_task_id:
            continue
        if normalize_token(str(task.get("task_type") or "")) != normalized_type:
            continue
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if current in {"applied", "rolled_back", "superseded"}:
            continue
        result_item = update_task_status(
            root,
            task_id,
            to_status="superseded",
            command=command,
            artifact=artifact,
            result=replacement_task_id,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def mark_tasks_rolled_back(
    root: Path,
    *,
    chapter_number: int,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
    from_statuses: Iterable[str] = ("applied", "validated", "submitted", "invalid"),
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Mark tasks for a chapter as rolled_back after a project rollback command."""

    allowed_from = {normalize_status(item) for item in from_statuses}
    results: list[AgentTaskLifecycleResult] = []
    for task in list_manifests(root, chapter_number=chapter_number):
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if current not in allowed_from:
            continue
        result_item = update_task_status(
            root,
            str(task.get("task_id") or ""),
            to_status="rolled_back",
            command=command,
            artifact=artifact,
            result=result,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def status_summary(root: Path, *, chapter_number: int | None = None) -> dict[str, Any]:
    tasks = list_manifests(root, chapter_number=chapter_number)
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status") or "unknown")
        task_type = str(task.get("task_type") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_type[task_type] = by_type.get(task_type, 0) + 1
    index = read_json(agent_task_index_file(root), default={})
    archived_terminal = (
        dict(index.get("terminal_counts") or {})
        if chapter_number is None and isinstance(index, dict)
        else {"total": 0, "by_status": {}, "by_type": {}}
    )
    return {
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "chapter_number": chapter_number,
        "tasks": len(tasks),
        "by_status": by_status,
        "by_type": by_type,
        "event_file": relative_path(root, agent_task_events_file(root)),
        "items": tasks,
        "archived_terminal": archived_terminal,
    }


def task_reconciliation_status(root: Path, *, chapter_number: int) -> dict[str, Any]:
    """Inspect explicit parent-child projections without mutating project state."""

    index = read_json(agent_task_index_file(root), default={})
    tasks = index.get("tasks") if isinstance(index, dict) else []
    if not isinstance(tasks, list):
        return {
            "status": "invalid",
            "chapter_number": chapter_number,
            "recoverable": [],
            "errors": ["agent task index is unreadable"],
        }
    scoped = [
        item
        for item in tasks
        if isinstance(item, dict) and int(item.get("chapter_number") or 0) == chapter_number
    ]
    by_id = {str(item.get("task_id") or ""): item for item in scoped}
    consumers: dict[str, list[dict[str, Any]]] = {}
    for item in scoped:
        parent_id = str(item.get("consumes_task_id") or "")
        if parent_id:
            consumers.setdefault(parent_id, []).append(item)
    errors: list[str] = []
    recoverable: list[dict[str, Any]] = []
    for parent_id, children in sorted(consumers.items()):
        if len(children) != 1:
            errors.append(f"parent task {parent_id} has {len(children)} consuming children")
            continue
        parent = by_id.get(parent_id)
        child = children[0]
        child_id = str(child.get("task_id") or "")
        if not isinstance(parent, dict):
            errors.append(f"consumed parent task is missing from the active projection: {parent_id}")
            continue
        try:
            parent_manifest = read_json(root / str(parent.get("manifest_file") or ""), default={})
            child_manifest = read_json(root / str(child.get("manifest_file") or ""), default={})
        except ValueError as exc:
            errors.append(str(exc))
            continue
        parent_validation = validate_manifest_strict(root, parent_manifest)
        child_validation = validate_manifest_strict(root, child_manifest)
        if not parent_validation.ok or not child_validation.ok:
            errors.extend(parent_validation.errors)
            errors.extend(child_validation.errors)
            continue
        binding = parent.get("current_result")
        if not isinstance(binding, dict) or binding.get("ok") is not True:
            errors.append(f"consumed parent task has no valid result binding: {parent_id}")
            continue
        parent_output = str(manifest_output(parent_manifest).get("path") or "")
        if parent_output not in manifest_input_paths(child_manifest):
            errors.append(f"child task does not declare parent output: {child_id}")
            continue
        output_path = resolve_under_root(root, parent_output)
        current_hash = sha256(output_path.read_bytes()).hexdigest() if output_path.is_file() else ""
        expected_hash = str(binding.get("sha256") or "")
        if not current_hash or current_hash != expected_hash:
            errors.append(f"consumed parent result hash drifted: {parent_id}")
            continue
        consistent = (
            str(parent.get("status") or "") == "applied"
            and str(parent.get("consumed_by_task_id") or "") == child_id
            and str(parent.get("satisfied_by_result_sha256") or "") == expected_hash
            and str(child.get("satisfied_by_result_sha256") or "") == expected_hash
        )
        if not consistent:
            recoverable.append(
                {
                    "parent_task_id": parent_id,
                    "child_task_id": child_id,
                    "satisfied_by_result_sha256": expected_hash,
                }
            )
    return {
        "schema": "agent_task_reconciliation_status_v1",
        "status": "need_human" if errors else ("reconciliation_required" if recoverable else "ok"),
        "chapter_number": chapter_number,
        "recoverable": recoverable,
        "errors": sorted(set(errors)),
        "next_command": (
            f"longform-engine agent-task reconcile project.yaml --chapter {chapter_number}"
            if recoverable and not errors
            else ""
        ),
    }


def reconcile_task_lineage(root: Path, *, chapter_number: int) -> dict[str, Any]:
    """Repair only explicit, hash-proven parent-child task projections."""

    status = task_reconciliation_status(root, chapter_number=chapter_number)
    if status["errors"]:
        raise AgentTaskContractError("; ".join(status["errors"]))
    recoverable = status.get("recoverable") or []
    if not recoverable:
        return {**status, "status": "ok", "reconciled": []}
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    tasks = payload.get("tasks") if isinstance(payload, dict) else []
    by_id = {
        str(item.get("task_id") or ""): item for item in tasks if isinstance(item, dict)
    }
    transitions: list[tuple[str, str, str, str]] = []
    for relation in recoverable:
        parent_id = str(relation["parent_task_id"])
        child_id = str(relation["child_task_id"])
        digest = str(relation["satisfied_by_result_sha256"])
        parent = by_id[parent_id]
        child = by_id[child_id]
        prior = normalize_status(str(parent.get("status") or "awaiting_agent"))
        parent["status"] = "applied"
        parent["consumed_by_task_id"] = child_id
        parent["satisfied_by_result_sha256"] = digest
        parent["updated_at"] = utc_now()
        child["consumes_task_id"] = parent_id
        child["satisfied_by_result_sha256"] = digest
        child["updated_at"] = utc_now()
        transitions.append((parent_id, child_id, prior, digest))
    payload["updated_at"] = utc_now()
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    for parent_id, child_id, prior, digest in transitions:
        record_task_event(
            root,
            task_id=parent_id,
            from_status=prior,
            to_status="applied",
            command="agent-task reconcile",
            result=child_id,
            consumed_by_task_id=child_id,
            satisfied_by_result_sha256=digest,
        )
    return {
        **task_reconciliation_status(root, chapter_number=chapter_number),
        "status": "reconciled",
        "reconciled": [
            {"parent_task_id": parent, "child_task_id": child}
            for parent, child, _prior, _digest in transitions
        ],
    }


def live_chapter_tasks(root: Path, *, chapter_number: int) -> list[dict[str, Any]]:
    """Return active tasks in the explicit current lineage without type-based exceptions."""

    tasks = list_manifests(root, chapter_number=chapter_number)
    ids = {str(item.get("task_id") or "") for item in tasks}
    superseded = {
        str(task_id)
        for item in tasks
        for task_id in item.get("supersedes_task_ids") or []
        if str(task_id)
    }
    consumed = {
        str(item.get("task_id") or "")
        for item in tasks
        if str(item.get("consumed_by_task_id") or "") in ids
    }
    terminal = {"applied", "superseded", "rolled_back"}
    return [
        item
        for item in tasks
        if str(item.get("task_id") or "") not in superseded | consumed
        and normalize_status(str(item.get("status") or "awaiting_agent")) not in terminal
    ]


def normalize_scope(scope: dict[str, Any] | None, *, chapter_number: int | None) -> dict[str, Any]:
    """Validate and normalize project/chapter/range scope."""

    value = dict(scope or {})
    kind = normalize_token(str(value.get("kind") or ("chapter" if chapter_number else "project")))
    if kind == "chapter":
        number = value.get("chapter_number", chapter_number)
        if not isinstance(number, int) or number <= 0:
            raise ValueError("chapter scope requires a positive chapter_number.")
        return {"kind": "chapter", "chapter_number": number}
    if kind == "range":
        start = value.get("from_chapter")
        end = value.get("to_chapter")
        if not isinstance(start, int) or start <= 0 or not isinstance(end, int) or end < start:
            raise ValueError("range scope requires positive from_chapter <= to_chapter.")
        return {"kind": "range", "from_chapter": start, "to_chapter": end}
    if kind == "project":
        return {"kind": "project"}
    raise ValueError("scope.kind must be one of: project, chapter, range.")


def normalize_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize the current v4 manifest without adding compatibility fields."""

    if not isinstance(manifest, dict):
        raise ValueError("Agent task manifest must be a JSON object.")
    source_version = manifest.get("schema_version")
    if source_version != AGENT_TASK_SCHEMA_VERSION:
        raise ValueError("Agent task manifest schema_version must be 4; historical manifests are unsupported.")
    normalized = dict(manifest)
    normalized["scope"] = normalize_scope(
        normalized.get("scope") if isinstance(normalized.get("scope"), dict) else None,
        chapter_number=None,
    )
    return normalized


def validate_manifest_shape(manifest: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "task_id",
        "task_type",
        "scope",
        "role",
        "io",
        "policy",
        "commands",
        "created_at",
    }
    actual = {key for key in manifest.keys() if key not in {"status", "manifest_file", "updated_at", "current_result"}}
    if actual != required:
        raise ValueError(
            "AgentTaskManifest v4 fields must be exactly: " + ", ".join(sorted(required))
        )
    if manifest.get("schema_version") != AGENT_TASK_SCHEMA_VERSION:
        raise ValueError("Agent task manifest schema_version must be 4.")
    if not str(manifest.get("task_id") or "").strip():
        raise ValueError("Agent task manifest task_id is required.")
    if not isinstance(manifest.get("scope"), dict):
        raise ValueError("Agent task manifest scope must be an object.")
    role = manifest.get("role")
    if not isinstance(role, dict) or set(role) != {
        "id", "version", "contract_hash", "selection_hash", "independence_mode",
        "overlay_hash", "sections", "playbooks",
    }:
        raise ValueError("Agent task manifest role must use the v4 role projection.")
    io = manifest.get("io")
    if not isinstance(io, dict) or set(io) != {"inputs", "output"}:
        raise ValueError("Agent task manifest io must contain exactly inputs and output.")
    policy = manifest.get("policy")
    if not isinstance(policy, dict) or set(policy) != {
        "boundary_profile", "canonical_targets", "requires_human_apply", "context",
    }:
        raise ValueError("Agent task manifest policy must use the v4 policy projection.")
    commands = manifest.get("commands")
    if not isinstance(commands, dict) or set(commands) != {"validate", "apply", "failure"}:
        raise ValueError("Agent task manifest commands must contain validate, apply, and failure.")


def validate_manifest_strict(root: Path, manifest: dict[str, Any], *, strict: bool = True) -> ManifestValidationResult:
    """Validate the current AgentTaskManifest v4 workflow contract."""

    errors: list[str] = []
    warnings: list[str] = []
    try:
        manifest = normalize_manifest(manifest)
        validate_manifest_shape(manifest)
    except ValueError as exc:
        errors.append(str(exc))
        return ManifestValidationResult(
            ok=False,
            task_id=str(manifest.get("task_id") or ""),
            task_type=str(manifest.get("task_type") or ""),
            strict=strict,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
    if not strict:
        return ManifestValidationResult(
            ok=True,
            task_id=str(manifest.get("task_id") or ""),
            task_type=str(manifest.get("task_type") or ""),
            strict=False,
            errors=(),
            warnings=(),
        )

    task_id = str(manifest.get("task_id") or "").strip()
    task_type = normalize_token(str(manifest.get("task_type") or ""))
    role_value = manifest.get("role") if isinstance(manifest.get("role"), dict) else {}
    policy_value = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}
    commands_value = manifest.get("commands") if isinstance(manifest.get("commands"), dict) else {}
    scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
    try:
        registry = validate_role_task_coverage(set(TASK_CONTRACTS))
        errors.extend(validate_manifest_role_metadata(manifest, registry=registry))
        role = registry.resolve(
            task_type,
            declared_role_id=str(role_value.get("id") or ""),
        )
        try:
            expected_overlay_hash = load_project_prompt_overlay(root, role).overlay_hash
        except PromptCompilationError as exc:
            errors.append(
                f"Project Prompt overlay is invalid: {exc}; "
                f"repair with `{exc.report['repair_command']}`."
            )
        else:
            actual_overlay_hash = str(role_value.get("overlay_hash") or "")
            if actual_overlay_hash != expected_overlay_hash:
                errors.append(
                    "Agent task manifest project_overlay_hash drifted from the current approved overlay; "
                    f"expected `{expected_overlay_hash}`, got `{actual_overlay_hash}`."
                )
    except RoleRegistryError as exc:
        errors.append(str(exc))
    scope_kind = str(scope.get("kind") or "")
    chapter_number = int(scope.get("chapter_number") or 0)
    if scope_kind == "chapter":
        if not isinstance(chapter_number, int) or chapter_number <= 0:
            errors.append("chapter scope requires a positive chapter_number.")
        elif f"ch{chapter_number:03d}" not in task_id:
            errors.append(f"task_id must contain ch{chapter_number:03d}.")
    elif scope_kind == "project":
        if chapter_number != 0:
            errors.append("project scope must use chapter_number=0.")
    elif scope_kind == "range":
        try:
            normalize_scope(scope, chapter_number=None)
        except ValueError as exc:
            errors.append(str(exc))
    else:
        errors.append("scope.kind must be one of: project, chapter, range.")

    contract = TASK_CONTRACTS.get(task_type)
    if contract is None:
        errors.append(f"task_type must be one of: {', '.join(sorted(TASK_CONTRACTS))}.")
    else:
        if scope_kind not in contract["scope_kinds"]:
            errors.append(
                f"{task_type} scope.kind must be one of {', '.join(contract['scope_kinds'])}; got `{scope_kind}`."
            )
        validate_schema(manifest, contract, errors)
        validate_outputs(root, manifest, contract, errors)
        validate_command_field(commands_value, "validate", contract["validate_prefixes"], errors)
        apply_prefixes = contract["apply_prefixes"]
        if output_protocol_for_task(task_type) == "design_document_v1":
            apply_prefixes = (*apply_prefixes, "longform-engine intelligence approve ")
        validate_command_field(commands_value, "apply", apply_prefixes, errors)
        validate_command_field(commands_value, "failure", contract["failure_prefixes"], errors)

    io_value = manifest.get("io") if isinstance(manifest.get("io"), dict) else {}
    input_records = io_value.get("inputs") if isinstance(io_value.get("inputs"), list) else []
    if not input_records:
        errors.append("io.inputs must contain at least one input record.")
    for index, item in enumerate(input_records):
        path_text = normalize_manifest_path(root, item.get("path") if isinstance(item, dict) else "")
        if not path_text:
            errors.append(f"io.inputs[{index}].path must be a non-empty path.")
        elif is_parent_escape(path_text):
            errors.append(f"io.inputs[{index}].path must not escape the project root: {path_text}")
    validate_context_policy(root, manifest, errors)

    boundary_profile = policy_value.get("boundary_profile")
    expected_boundary = {
        "id": BOUNDARY_PROFILE_ID,
        "version": BOUNDARY_PROFILE_VERSION,
        "sha256": BOUNDARY_PROFILE_HASH,
    }
    if boundary_profile != expected_boundary:
        errors.append("policy.boundary_profile does not match the registered canonical write boundary.")
    canonical_targets = policy_value.get("canonical_targets") or []
    for index, item in enumerate(canonical_targets if isinstance(canonical_targets, list) else []):
        path_text = normalize_manifest_path(root, item)
        if not path_text or Path(str(item)).is_absolute() or is_parent_escape(path_text):
            errors.append(f"canonical_targets[{index}] must be a project-relative path inside the project.")
        elif not is_canonical_output(path_text):
            errors.append(f"canonical_targets[{index}] is not a recognized canonical lane: {path_text}")
    if bool(policy_value.get("requires_human_apply")) and "--approved-by human" not in str(commands_value.get("apply") or ""):
        errors.append("policy.requires_human_apply tasks must include `--approved-by human` in commands.apply.")

    return ManifestValidationResult(
        ok=not errors,
        task_id=task_id,
        task_type=task_type,
        strict=True,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def validate_schema(manifest: dict[str, Any], contract: dict[str, tuple[str, ...]], errors: list[str]) -> None:
    output = ((manifest.get("io") or {}).get("output") or {}) if isinstance(manifest.get("io"), dict) else {}
    schema = str(output.get("protocol") or "").strip()
    allowed = contract["schemas"]
    if schema not in allowed:
        errors.append(f"io.output.protocol must be one of {', '.join(allowed)}; got `{schema}`.")


def validate_outputs(root: Path, manifest: dict[str, Any], contract: dict[str, tuple[str, ...]], errors: list[str]) -> None:
    output = ((manifest.get("io") or {}).get("output") or {}) if isinstance(manifest.get("io"), dict) else {}
    outputs = [output.get("path")] if output.get("path") else []
    if not outputs:
        errors.append("io.output.path must be a non-empty path.")
        return
    for index, item in enumerate(outputs):
        path_text = normalize_manifest_path(root, item)
        if not path_text:
            errors.append("io.output.path must be a non-empty path.")
            continue
        if Path(str(item)).is_absolute():
            errors.append(f"io.output.path must be project-relative: {item}")
        if is_parent_escape(path_text):
            errors.append(f"io.output.path must not escape the project root: {item}")
        if is_canonical_output(path_text):
            errors.append(f"io.output.path must not point to canonical state: {path_text}")
        if not path_matches_prefix(path_text, contract["output_prefixes"]):
            errors.append(
                f"io.output.path must live under one of "
                f"{', '.join(contract['output_prefixes'])}; got `{path_text}`."
            )


def validate_command_field(
    commands: dict[str, Any],
    field: str,
    allowed_prefixes: tuple[str, ...],
    errors: list[str],
) -> None:
    command = normalize_command(str(commands.get(field) or ""))
    if not command:
        errors.append(f"commands.{field} is required.")
        return
    if not any(command.startswith(prefix) for prefix in allowed_prefixes):
        errors.append(f"commands.{field} must start with one of {', '.join(allowed_prefixes)}; got `{command}`.")


def normalize_manifest_path(root: Path, value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return text
    return path.as_posix()


def normalize_command(command: str) -> str:
    compact = re.sub(r"\s+", " ", command.strip())
    if compact.startswith("python -m longform_engine.cli "):
        return "longform-engine " + compact.removeprefix("python -m longform_engine.cli ")
    if compact.startswith("python3 -m longform_engine.cli "):
        return "longform-engine " + compact.removeprefix("python3 -m longform_engine.cli ")
    return compact


def is_parent_escape(path_text: str) -> bool:
    return ".." in Path(path_text).parts


def is_canonical_output(path_text: str) -> bool:
    normalized = path_text.strip().replace("\\", "/")
    return normalized in CANONICAL_OUTPUT_FILES or any(normalized.startswith(prefix) for prefix in CANONICAL_OUTPUT_PREFIXES)


def path_matches_prefix(path_text: str, prefixes: tuple[str, ...]) -> bool:
    normalized = path_text.strip().replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in prefixes)


def agent_task_index_file(root: Path) -> Path:
    return root / "50_workbench" / "agent_tasks" / "agent_task_index.json"


def agent_task_events_file(root: Path) -> Path:
    return root / "50_workbench" / "agent_tasks" / "events.jsonl"


def normalize_current_result_binding(value: dict[str, Any]) -> dict[str, Any]:
    required = ("ok", "path", "sha256", "diagnostic_file", "source_schema", "validated_at")
    missing = [field for field in required if field not in value]
    if missing:
        raise ValueError("current_result binding missing fields: " + ", ".join(missing))
    path = str(value.get("path") or "").replace("\\", "/")
    diagnostic = str(value.get("diagnostic_file") or "").replace("\\", "/")
    digest = str(value.get("sha256") or "")
    if not path or (digest and not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("current_result binding requires an output path and a lowercase SHA-256 digest.")
    if value.get("ok") is True and not digest:
        raise ValueError("A valid current_result binding requires a SHA-256 digest.")
    if value.get("ok") is not True and not diagnostic:
        raise ValueError("An invalid current_result binding requires one diagnostic file.")
    return {
        "ok": value.get("ok") is True,
        "path": path,
        "sha256": digest,
        "diagnostic_file": diagnostic,
        "source_schema": str(value.get("source_schema") or ""),
        "validated_at": str(value.get("validated_at") or ""),
    }


def record_task_event(
    root: Path,
    *,
    task_id: str,
    from_status: str,
    to_status: str,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
    consumes_task_id: str = "",
    consumed_by_task_id: str = "",
    satisfied_by_result_sha256: str = "",
    supersedes_task_ids: Iterable[str] = (),
) -> Path:
    normalized_to = normalize_status(to_status)
    normalized_from = normalize_status(from_status) if str(from_status).strip() else ""
    path = agent_task_events_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    task = next((item for item in list_manifests(root) if item.get("task_id") == task_id), {})
    payload = {
        "schema": AGENT_TASK_EVENT_SCHEMA,
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "task_id": task_id,
        "task_type": str(task.get("task_type") or ""),
        "chapter_number": manifest_chapter_number(task),
        "from_status": normalized_from,
        "to_status": normalized_to,
        "command": command,
        "artifact": relative_path(root, artifact) if str(artifact).strip() else "",
        "result": relative_path(root, result) if str(result).strip() else "",
        "consumes_task_id": str(consumes_task_id or ""),
        "consumed_by_task_id": str(consumed_by_task_id or ""),
        "satisfied_by_result_sha256": str(satisfied_by_result_sha256 or ""),
        "supersedes_task_ids": sorted(
            {str(item).strip() for item in supersedes_task_ids if str(item).strip()}
        ),
        "created_at": utc_now(),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    rotate_project_events(root)
    return path


def new_task_index() -> dict[str, Any]:
    return {
        "schema": AGENT_TASK_INDEX_SCHEMA,
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "tasks": [],
        "terminal_counts": {"total": 0, "by_status": {}, "by_type": {}},
        "archived_chapters": {},
        "updated_at": utc_now(),
    }


def read_task_events(root: Path) -> list[dict[str, Any]]:
    path = agent_task_events_file(root)
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            normalized = dict(payload)
            normalized.setdefault("schema", AGENT_TASK_EVENT_SCHEMA)
            normalized.setdefault("schema_version", AGENT_TASK_SCHEMA_VERSION)
            if "chapter_number" not in normalized or "task_type" not in normalized:
                task = next(
                    (item for item in list_manifests(root) if item.get("task_id") == normalized.get("task_id")),
                    {},
                )
                normalized.setdefault("chapter_number", int(task.get("chapter_number") or 0))
                normalized.setdefault("task_type", str(task.get("task_type") or ""))
            events.append(normalized)
    return events


def task_archive_projection(root: Path, chapter_number: int) -> dict[str, Any]:
    tasks = list_manifests(root, chapter_number=chapter_number)
    task_ids = {str(item.get("task_id") or "") for item in tasks}
    events = [
        item
        for item in read_task_events(root)
        if int(item.get("chapter_number") or 0) == chapter_number
        or str(item.get("task_id") or "") in task_ids
    ]
    return {
        "schema": "chapter_agent_task_projection_v1",
        "chapter_number": chapter_number,
        "tasks": tasks,
        "events": events,
    }


def project_task_archive_projection(root: Path) -> dict[str, Any]:
    tasks = [item for item in list_manifests(root) if manifest_chapter_number(item) == 0]
    task_ids = {str(item.get("task_id") or "") for item in tasks}
    events = [
        item
        for item in read_task_events(root)
        if int(item.get("chapter_number") or 0) == 0
        or str(item.get("task_id") or "") in task_ids
    ]
    return {
        "schema": "project_agent_task_projection_v1",
        "tasks": tasks,
        "events": events,
    }


def compact_project_task_projection(root: Path, *, archive_ref: str) -> dict[str, Any]:
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    if not isinstance(payload, dict):
        payload = new_task_index()
    tasks = [dict(item) for item in payload.get("tasks", []) if isinstance(item, dict)]
    project_tasks = [item for item in tasks if int(item.get("chapter_number") or 0) == 0]
    nonterminal = [item for item in project_tasks if str(item.get("status") or "") not in TERMINAL_TASK_STATUSES]
    if nonterminal:
        names = ", ".join(str(item.get("task_id") or "") for item in nonterminal[:5])
        raise ValueError(f"Cannot compact project setup with active tasks: {names}")
    retained = [item for item in tasks if item not in project_tasks]
    counts = payload.get("terminal_counts") if isinstance(payload.get("terminal_counts"), dict) else {}
    by_status = dict(counts.get("by_status") or {})
    by_type = dict(counts.get("by_type") or {})
    for item in project_tasks:
        status = str(item.get("status") or "unknown")
        task_type = str(item.get("task_type") or "unknown")
        by_status[status] = int(by_status.get(status) or 0) + 1
        by_type[task_type] = int(by_type.get(task_type) or 0) + 1
    payload.update(
        {
            "schema": AGENT_TASK_INDEX_SCHEMA,
            "schema_version": AGENT_TASK_SCHEMA_VERSION,
            "tasks": retained,
            "terminal_counts": {
                "total": int(counts.get("total") or 0) + len(project_tasks),
                "by_status": by_status,
                "by_type": by_type,
            },
            "project_setup_archive": {
                "archive": archive_ref,
                "task_count": len(project_tasks),
            },
            "updated_at": utc_now(),
        }
    )
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    events = read_task_events(root)
    retained_events = [item for item in events if int(item.get("chapter_number") or 0) != 0]
    atomic_write_text(
        agent_task_events_file(root),
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in retained_events),
    )
    return {
        "archived_tasks": len(project_tasks),
        "retained_tasks": len(retained),
        "archived_events": len(events) - len(retained_events),
        "retained_events": len(retained_events),
    }


def compact_task_projection(root: Path, *, through: int, archive_refs: dict[int, str]) -> dict[str, Any]:
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    if not isinstance(payload, dict):
        payload = new_task_index()
    tasks = [dict(item) for item in payload.get("tasks", []) if isinstance(item, dict)]
    archived = [item for item in tasks if 0 < int(item.get("chapter_number") or 0) <= through]
    retained = [item for item in tasks if item not in archived]
    counts = payload.get("terminal_counts") if isinstance(payload.get("terminal_counts"), dict) else {}
    by_status = dict(counts.get("by_status") or {})
    by_type = dict(counts.get("by_type") or {})
    for item in archived:
        status = str(item.get("status") or "unknown")
        task_type = str(item.get("task_type") or "unknown")
        by_status[status] = int(by_status.get(status) or 0) + 1
        by_type[task_type] = int(by_type.get(task_type) or 0) + 1
    archived_chapters = dict(payload.get("archived_chapters") or {})
    for chapter_number, archive in archive_refs.items():
        chapter_tasks = [item for item in archived if int(item.get("chapter_number") or 0) == chapter_number]
        if not chapter_tasks and str(chapter_number) in archived_chapters:
            continue
        archived_chapters[str(chapter_number)] = {
            "archive": archive,
            "task_count": len(chapter_tasks),
        }
    payload.update(
        {
            "schema": AGENT_TASK_INDEX_SCHEMA,
            "schema_version": AGENT_TASK_SCHEMA_VERSION,
            "tasks": retained,
            "terminal_counts": {
                "total": int(counts.get("total") or 0) + len(archived),
                "by_status": by_status,
                "by_type": by_type,
            },
            "archived_chapters": archived_chapters,
            "updated_at": utc_now(),
        }
    )
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    events = read_task_events(root)
    retained_events = [item for item in events if int(item.get("chapter_number") or 0) == 0 or int(item.get("chapter_number") or 0) > through]
    event_path = agent_task_events_file(root)
    atomic_write_text(
        event_path,
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in retained_events),
    )
    return {
        "archived_tasks": len(archived),
        "retained_tasks": len(retained),
        "archived_events": len(events) - len(retained_events),
        "retained_events": len(retained_events),
    }


def rotate_project_events(root: Path) -> None:
    path = agent_task_events_file(root)
    if not path.is_file():
        return
    lines = path.read_text(encoding="utf-8").splitlines()
    if path.stat().st_size < EVENT_ROTATE_BYTES and len(lines) < EVENT_ROTATE_LINES:
        return
    parsed: list[tuple[str, dict[str, Any]]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            parsed.append((line, payload))
    project_lines = [line for line, payload in parsed if int(payload.get("chapter_number") or 0) == 0]
    active_lines = [line for line, payload in parsed if int(payload.get("chapter_number") or 0) != 0]
    if not project_lines:
        return
    segment_dir = root / "70_runtime" / "artifacts" / "events"
    segment_dir.mkdir(parents=True, exist_ok=True)
    content = ("\n".join(project_lines) + "\n").encode("utf-8")
    digest = sha256(content).hexdigest()
    segment = segment_dir / f"project-events-{digest[:16]}.jsonl.gz"
    if not segment.exists():
        with gzip.open(segment, "wb", compresslevel=9) as handle:
            handle.write(content)
    manifest_path = segment_dir / "segments.json"
    manifest = read_json(manifest_path, default={})
    if not isinstance(manifest, dict):
        manifest = {}
    segments = [item for item in manifest.get("segments", []) if isinstance(item, dict)]
    record = {
        "path": relative_path(root, segment),
        "sha256": sha256(segment.read_bytes()).hexdigest(),
        "content_sha256": digest,
        "lines": len(project_lines),
    }
    if not any(item.get("path") == record["path"] for item in segments):
        segments.append(record)
    atomic_write_text(
        manifest_path,
        json.dumps({"schema": EVENT_SEGMENT_SCHEMA, "segments": segments}, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(path, "".join(line + "\n" for line in active_lines))


def normalize_status(value: str) -> str:
    status = normalize_token(value)
    if status not in AGENT_TASK_STATUSES:
        raise ValueError(f"Agent task status must be one of: {', '.join(AGENT_TASK_STATUSES)}.")
    return status


def normalize_paths(root: Path, paths: Iterable[str | Path]) -> list[str]:
    result: list[str] = []
    for raw in paths:
        if raw is None:
            continue
        path_text = str(raw).strip()
        if not path_text:
            continue
        result.append(relative_path(root, Path(path_text)))
    return dedupe(result)


def normalize_context_policy(
    root: Path,
    policy: dict[str, Any] | None,
    *,
    input_files: list[str],
    task_type: str,
    scope: dict[str, Any],
) -> dict[str, Any]:
    """Normalize required/optional context tiers without widening declared inputs."""

    raw = dict(policy or {})
    required = normalize_paths(root, raw.get("required_files") or input_files)
    optional = normalize_paths(root, raw.get("optional_files") or [])
    declared = dedupe([str(item).replace("\\", "/") for item in input_files])
    required = [item for item in required if item in declared]
    optional = [item for item in optional if item in declared and item not in required]
    classified = set(required) | set(optional)
    optional.extend(item for item in declared if item not in classified)
    compiled_brief = normalize_manifest_path(root, raw.get("compiled_brief") or (required[0] if required else ""))
    selection_report = normalize_manifest_path(root, raw.get("selection_report") or "")
    quality_focus = normalize_signal_list(raw.get("quality_focus") or [])
    trigger_codes = normalize_signal_list(raw.get("trigger_codes") or [])
    budget = resolve_context_budget_contract(root, raw)
    chapter_number = int(scope.get("chapter_number") or 0) if scope.get("kind") == "chapter" else 0
    facet_records = project_active_facet_adapters(
        root,
        chapter_number=chapter_number,
        requested=[str(item) for item in raw.get("active_facets") or []],
        limit=3,
    )
    return {
        "schema": "agent_context_policy_v1",
        "required_files": required,
        "optional_files": optional,
        "forbidden_paths": dedupe([str(item) for item in raw.get("forbidden_paths") or DEFAULT_FORBIDDEN_CONTEXT]),
        "budget_profile": budget.profile,
        "capacity_units": budget.capacity_units,
        "overflow_policy": budget.overflow_policy,
        "compiled_brief": compiled_brief,
        "selection_report": selection_report,
        "quality_focus": quality_focus,
        "trigger_codes": trigger_codes,
        "active_facets": [
            {
                "kind": item["kind"],
                "id": item["id"],
                "level": item["level"],
                "source": item["source"],
                "sha256": item["sha256"],
            }
            for item in facet_records
        ],
    }


def validate_context_policy(root: Path, manifest: dict[str, Any], errors: list[str]) -> None:
    policy_root = manifest.get("policy") if isinstance(manifest.get("policy"), dict) else {}
    policy = policy_root.get("context")
    if not isinstance(policy, dict):
        errors.append("policy.context must be an object.")
        return
    required_fields = {
        "forbidden_paths",
        "budget_profile",
        "capacity_units",
        "overflow_policy",
        "quality_focus",
        "trigger_codes",
        "active_facets",
    }
    if set(policy) != required_fields:
        errors.append("policy.context must contain exactly the v4 context fields.")
        return
    input_records = ((manifest.get("io") or {}).get("inputs") or []) if isinstance(manifest.get("io"), dict) else []
    inputs = [
        str(item.get("path") or "").replace("\\", "/")
        for item in input_records
        if isinstance(item, dict)
    ]
    required = [
        str(item.get("path") or "").replace("\\", "/")
        for item in input_records
        if isinstance(item, dict) and item.get("requirement") == "required"
    ]
    optional = [
        str(item.get("path") or "").replace("\\", "/")
        for item in input_records
        if isinstance(item, dict) and item.get("requirement") == "optional"
    ]
    forbidden = policy.get("forbidden_paths")
    if not isinstance(forbidden, list):
        errors.append("policy.context.forbidden_paths must be a list.")
        return
    classified = [str(item).replace("\\", "/") for item in [*required, *optional]]
    if len(classified) != len(set(classified)):
        errors.append("io.inputs paths must not be duplicated across required and optional tiers.")
    if set(classified) != set(inputs):
        errors.append("io.inputs must classify every path as required or optional exactly once.")
    try:
        resolve_context_budget_contract(root, policy)
    except ValueError as exc:
        errors.append(str(exc))
    if isinstance(input_records, list):
        records = {
            str(item.get("path") or ""): item
            for item in input_records
            if isinstance(item, dict)
        }
        for index, item in enumerate(inputs):
            path_text = normalize_manifest_path(root, item)
            if not path_text or is_parent_escape(path_text):
                continue
            path = (root / path_text).resolve()
            if not path.exists() or not path.is_file():
                errors.append(f"io.inputs[{index}].path does not exist or is not a file: {path_text}")
                continue
            try:
                content = path.read_text(encoding="utf-8").lstrip("\ufeff")
            except UnicodeDecodeError:
                errors.append(f"io.inputs[{index}].path must be valid UTF-8 text: {path_text}")
                continue
            record = records.get(path_text)
            if record is None:
                errors.append(f"io.inputs is missing `{path_text}`.")
                continue
            if set(record) != {"path", "requirement", "sha256", "characters", "reason"}:
                errors.append(f"io.inputs record for `{path_text}` has an invalid v4 shape.")
                continue
            if record.get("requirement") not in {"required", "optional"}:
                errors.append(f"io.inputs requirement for `{path_text}` is invalid.")
            if int(record.get("characters") or -1) != len(content):
                errors.append(f"io.inputs character count drifted for `{path_text}`.")
            if str(record.get("sha256") or "") != sha256(path.read_bytes()).hexdigest():
                errors.append(f"io.inputs SHA-256 drifted for `{path_text}`.")
    compiled = next(
        (
            str(item.get("path") or "")
            for item in input_records
            if isinstance(item, dict) and item.get("reason") == "compiled_task_brief"
        ),
        "",
    )
    if not compiled or compiled not in required:
        errors.append("io.inputs must identify one required compiled_task_brief input.")
    for field in ("quality_focus", "trigger_codes"):
        values = policy.get(field)
        if not isinstance(values, list) or any(
            not isinstance(item, str) or not item for item in values
        ):
            errors.append(f"policy.context.{field} must be a list of non-empty normalized tokens.")
    facets = policy.get("active_facets")
    if not isinstance(facets, list) or len(facets) > 3:
        errors.append("policy.context.active_facets must be a list with at most three entries.")
    else:
        scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
        chapter_number = int(scope.get("chapter_number") or 0) if scope.get("kind") == "chapter" else 0
        current = project_active_facet_adapters(
            root,
            chapter_number=chapter_number,
            requested=[f"{item.get('kind')}:{item.get('id')}" for item in facets if isinstance(item, dict)],
            limit=3,
        )
        expected = [
            {key: item[key] for key in ("kind", "id", "level", "source", "sha256")}
            for item in current
        ]
        if facets != expected:
            errors.append("policy.context.active_facets drifted from the current story facet registry.")


def normalize_signal_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        values: list[Any] = []
        for key, items in value.items():
            values.append(key)
            values.extend(items if isinstance(items, list) else [items])
    elif isinstance(value, list):
        values = value
    elif value:
        values = [value]
    else:
        values = []
    return dedupe([normalize_token(str(item)) for item in values if str(item or "").strip()])


def normalize_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower().replace("-", "_"))
    return token.strip("_") or "agent_task"


def resolve_under_root(root: Path, file_path: str | Path) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path must live under project root: {file_path}") from exc
    return resolved


def relative_path(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def read_json(path: Path, *, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
