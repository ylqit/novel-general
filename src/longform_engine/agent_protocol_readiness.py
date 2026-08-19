"""Read-only structural readiness gate for the Agent-first data pipeline."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import re

import yaml

from longform_engine import __version__
from longform_engine.agent_isolation import assert_current_protocol_coverage
from longform_engine.agent_protocols import AGENT_OUTPUT_PROTOCOLS
from longform_engine.agent_tasks import (
    AGENT_TASK_EVENT_SCHEMA,
    AGENT_TASK_INDEX_SCHEMA,
    AGENT_TASK_SCHEMA_VERSION,
    SUPPORTED_AGENT_TASK_SCHEMA_VERSIONS,
    TASK_CONTRACTS,
)
from longform_engine.distribution import tree_hash
from longform_engine.resources import resource_root
from longform_engine.prompting import estimate_text_units, load_context_profile_registry
from longform_engine.roles import (
    GENERIC_TRIGGER_SIGNALS,
    PLAYBOOK_PROFESSIONAL_SECTIONS,
    ROLE_PROFESSIONAL_SECTIONS,
    ROLE_REGISTRY_SCHEMA,
    SESSION_POLICIES,
    load_role_registry,
)
from longform_engine.story_profiles import load_facet_registries


SCHEMA = "agent_data_pipeline_readiness_v3"
RETIRED_RUNTIME_MARKERS = (
    "LEGACY" + "_COMPATIBILITY_TASK_TYPES",
    "legacy" + "_document_json",
    "agent_data_pipeline_authorization_v1",
    "validate_document_index_bundle",
    "AGENT_RESULT_ENVELOPE_SCHEMA",
    "humanizer_semantic_output_template",
    "semantic_review_output_template",
    "semantic_pacing_output_template",
    "graph_extract",
    "memory_extract",
    'task_type="character_memory"',
)
FORBIDDEN_PROCESS_MARKERS = (
    "multiprocessing",
    "ProcessPoolExecutor",
    "subprocess.Popen",
)
FORBIDDEN_FIXED_PROMPT_BUDGET_PATTERNS = (
    re.compile(r"(?m)^\s*MAX_[A-Z_]*PROMPT[A-Z_]*\s*=\s*\d"),
    re.compile(r"(?m)^\s*[A-Z_]*CONTEXT_MAX_CHARS\s*=\s*\d"),
    re.compile(r"(?:prompt|context).{0,32}exceeds fixed character budget", re.IGNORECASE),
)
PROTOCOL_SURFACE_FILES = (
    "src/longform_engine/agent_pipeline.py",
    "src/longform_engine/agent_isolation.py",
    "src/longform_engine/agent_normalization.py",
    "src/longform_engine/agent_protocol_readiness.py",
    "src/longform_engine/agent_results.py",
    "src/longform_engine/agent_tasks.py",
    "src/longform_engine/graph/pipeline.py",
    "src/longform_engine/memory/pipeline.py",
    "src/longform_engine/prompting.py",
    "src/longform_engine/production.py",
    "src/longform_engine/roles.py",
    "src/longform_engine/semantic/pipeline.py",
)


class AgentDataPipelineBlocked(RuntimeError):
    """Raised when the current installation cannot compile a safe Agent task."""


def check_agent_data_pipeline_readiness(
    repository: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the current protocol in-process without running child commands."""

    root = Path(repository).expanduser().resolve() if repository else resource_root().resolve()
    checks: list[dict[str, Any]] = []

    role_error = ""
    try:
        registry = load_role_registry(root)
        assert_current_protocol_coverage(registry)
    except (OSError, RuntimeError, ValueError) as exc:
        registry = None
        role_error = str(exc)
    add_check(
        checks,
        "role_and_task_coverage",
        not role_error,
        {
            "registry_schema": ROLE_REGISTRY_SCHEMA,
            "role_count": len(registry.roles) if registry else 0,
            "playbook_count": len(registry.playbooks) if registry else 0,
            "error": role_error,
        },
        "修复角色注册表、任务映射或专业模块后重新运行 readiness。",
    )

    protocol_ok = (
        AGENT_TASK_SCHEMA_VERSION == 4
        and SUPPORTED_AGENT_TASK_SCHEMA_VERSIONS == (4,)
        and AGENT_TASK_INDEX_SCHEMA == "agent_task_index_v4"
        and AGENT_TASK_EVENT_SCHEMA == "agent_task_event_v4"
    )
    add_check(
        checks,
        "manifest_v4_only",
        protocol_ok,
        {
            "manifest_version": AGENT_TASK_SCHEMA_VERSION,
            "supported_versions": list(SUPPORTED_AGENT_TASK_SCHEMA_VERSIONS),
            "index_schema": AGENT_TASK_INDEX_SCHEMA,
            "event_schema": AGENT_TASK_EVENT_SCHEMA,
        },
        "仅保留 AgentTaskManifest、task index 和 event 的 v4 协议。",
    )

    prompt_errors: list[str] = [role_error] if role_error else []
    if registry:
        decision_hashes: dict[str, str] = {}
        calibration_hashes: dict[str, str] = {}
        for role in registry.roles.values():
            if not role.always_sections or not role.task_sections:
                prompt_errors.append(f"{role.role_id}: missing always/task sections")
            for section, expected_mode in ROLE_PROFESSIONAL_SECTIONS.items():
                actual_mode = (
                    _role_section_mode(role, section)
                    if section in role.prompt_sections
                    else ""
                )
                if actual_mode != expected_mode:
                    prompt_errors.append(
                        f"{role.role_id}: {section} must use {expected_mode}"
                    )
            generic = sorted(GENERIC_TRIGGER_SIGNALS & set(role.trigger_sections))
            if generic:
                prompt_errors.append(f"{role.role_id}: generic triggers {generic}")
            if "诊断树" not in role.prompt_sections.get("diagnostics", ""):
                prompt_errors.append(f"{role.role_id}: missing role-specific diagnostic tree")
            if role.role_family == "review" and (
                not role.review_dimensions or not role.finding_codes
            ):
                prompt_errors.append(f"{role.role_id}: review scope is not explicit")
            if role.role_family == "review":
                missing_codes = [code for code in role.finding_codes if code not in role.prompt_text]
                if missing_codes:
                    prompt_errors.append(
                        f"{role.role_id}: finding rules missing {', '.join(missing_codes)}"
                    )
            elif "专业判定表" not in role.prompt_sections.get("diagnostics", ""):
                prompt_errors.append(f"{role.role_id}: missing professional decision table")
            if role.role_family == "review" and role.max_active_playbooks > 2:
                prompt_errors.append(f"{role.role_id}: review Playbook limit exceeds two")
            if not contains_cjk(role.prompt_text):
                prompt_errors.append(f"{role.role_id}: prompt is not Chinese-first")
            calibration = role.prompt_sections.get("calibration", "")
            if any(marker not in calibration for marker in ("正例", "反例", "边界")):
                prompt_errors.append(f"{role.role_id}: calibration is not role-specific")
            decision_digest = role.prompt_section_hashes.get("decision_model", "")
            calibration_digest = role.prompt_section_hashes.get("calibration", "")
            if decision_digest in decision_hashes:
                prompt_errors.append(
                    f"{role.role_id}: decision model duplicates {decision_hashes[decision_digest]}"
                )
            decision_hashes[decision_digest] = role.role_id
            if calibration_digest in calibration_hashes:
                prompt_errors.append(
                    f"{role.role_id}: calibration duplicates {calibration_hashes[calibration_digest]}"
                )
            calibration_hashes[calibration_digest] = role.role_id
        for playbook in registry.playbooks.values():
            source = playbook.source
            for section, expected_mode in PLAYBOOK_PROFESSIONAL_SECTIONS.items():
                if source.section_modes.get(section) != expected_mode:
                    prompt_errors.append(
                        f"{playbook.playbook_id}: {section} must use {expected_mode}"
                    )
            if len(re.findall(r"(?m)^\d+\.\s*正例：", source.sections.get("examples", ""))) < 3:
                prompt_errors.append(f"{playbook.playbook_id}: fewer than three calibration pairs")
        facet_sections = [
            playbook.source.sections.get("facets", "").strip()
            for playbook in registry.playbooks.values()
        ]
        if len(facet_sections) != len(set(facet_sections)):
            prompt_errors.append("Playbooks contain duplicated story-facet guidance")
        if registry.registry_version != 3 or len(registry.roles) != 28 or len(registry.playbooks) != 12:
            prompt_errors.append("registry must contain v3, 28 roles, and 12 playbooks")
    add_check(
        checks,
        "chinese_role_contracts",
        not prompt_errors,
        {"errors": prompt_errors},
        "补齐渐进区段、角色判断范围与专业方法模块。",
    )

    session_errors: list[str] = []
    if registry:
        for role in registry.roles.values():
            if role.session_policy not in SESSION_POLICIES:
                session_errors.append(f"{role.role_id}: invalid session policy")
            if role.role_family == "review" and role.session_policy != "isolated_review":
                session_errors.append(f"{role.role_id}: review must use an isolated session")
        expected_sessions = {
            "book_design": "project_coordinator",
            "chapter_write": "chapter_author",
            "repair": "chapter_author",
            "humanize": "isolated_revision",
            "semantic_review": "isolated_review",
            "chapter_semantic": "isolated_archival",
            "design_semantic_compile": "isolated_archival",
        }
        for task_type, expected in expected_sessions.items():
            actual = registry.resolve(task_type).session_policy
            if actual != expected:
                session_errors.append(f"{task_type}: expected {expected}, got {actual}")
    add_check(
        checks,
        "hybrid_session_boundaries",
        not session_errors,
        {"policies": sorted(SESSION_POLICIES), "errors": session_errors},
        "修复角色会话策略，隔离作者、修订、审稿与语义归档上下文。",
    )

    context_errors: list[str] = []
    try:
        context_registry = load_context_profile_registry()
    except (OSError, ValueError) as exc:
        context_registry = {}
        context_errors.append(str(exc))
    expected_capacities = {"compact": 24_000, "standard": 48_000, "large": 96_000}
    actual_capacities = {
        str(profile_id): int(value.get("capacity_units") or 0)
        for profile_id, value in (context_registry.get("profiles") or {}).items()
        if isinstance(value, dict)
    }
    if actual_capacities != expected_capacities:
        context_errors.append(
            f"context capacities must be resource-defined as {expected_capacities}, got {actual_capacities}"
        )
    if context_registry.get("default_profile") != "standard":
        context_errors.append("standard must remain the default host profile")
    allocation = context_registry.get("allocation") or {}
    if float(allocation.get("minimum_output_and_handoff_ratio") or 0) < 0.25:
        context_errors.append("less than 25% of capacity is reserved for output and handoff")
    add_check(
        checks,
        "adaptive_context_profiles",
        not context_errors,
        {"profiles": actual_capacities, "errors": context_errors},
        "修复资源化上下文档位、输出保留比例或默认宿主档位。",
    )

    facet_errors: list[str] = []
    try:
        facets = load_facet_registries()
    except (OSError, ValueError) as exc:
        facets = {}
        facet_errors.append(str(exc))
    adapters = [
        str(value.get("prompt_adapter") or "").strip()
        for values in facets.values()
        for value in values.values()
    ]
    if len(adapters) != 44:
        facet_errors.append(f"expected 44 story facets, got {len(adapters)}")
    if len(adapters) != len(set(adapters)):
        facet_errors.append("story facets contain duplicated Prompt adapters")
    if any(not contains_cjk(adapter) for adapter in adapters):
        facet_errors.append("every story facet must provide Chinese adaptation guidance")
    add_check(
        checks,
        "chinese_story_facet_adapters",
        not facet_errors,
        {"facet_count": len(adapters), "unique_adapters": len(set(adapters)), "errors": facet_errors},
        "补齐 44 个故事分面的中文方法适配器并消除重复模板。",
    )

    professional_errors, professional_inventory = professional_prompt_evidence(
        root,
        registry=registry,
        facets=facets,
    )
    add_check(
        checks,
        "professional_prompt_calibration",
        not professional_errors,
        {
            "item_count": professional_inventory.get("item_count", 0),
            "expected_item_count": 84,
            "inventory": professional_inventory,
            "errors": professional_errors,
        },
        "逐项补齐 28 个角色、12 个 Playbook 与 44 个故事分面的专业内容和校准证据。",
    )

    protocol_errors: list[str] = []
    if len(TASK_CONTRACTS) != 25:
        protocol_errors.append(f"expected 25 task contracts, got {len(TASK_CONTRACTS)}")
    mapped_protocols: set[str] = set()
    for task_type, contract in TASK_CONTRACTS.items():
        schemas = tuple(contract.get("schemas") or ())
        if len(schemas) != 1 or schemas[0] not in AGENT_OUTPUT_PROTOCOLS:
            protocol_errors.append(f"{task_type}: output protocol must be one of the four current protocols")
        else:
            mapped_protocols.add(schemas[0])
    if mapped_protocols != set(AGENT_OUTPUT_PROTOCOLS):
        protocol_errors.append("task contracts do not exercise exactly the four current protocols")
    add_check(
        checks,
        "four_output_protocols",
        not protocol_errors,
        {"protocols": sorted(mapped_protocols), "task_count": len(TASK_CONTRACTS), "errors": protocol_errors},
        "将所有当前任务收敛到四种单文件 Agent 输出协议。",
    )

    selection_errors: list[str] = []
    if registry:
        role_tasks = [
            (task_type, role_id)
            for task_type, role_id in registry.task_role_map.items()
        ] + [("editorial_review", role_id) for role_id in registry.editorial_role_map]
        for task_type, role_id in role_tasks:
            selection = registry.select_prompt(
                task_type,
                declared_role_id=role_id,
                quality_focus=("dialogue", "opening", "character"),
            )
            if len(selection.playbooks) > registry.roles[role_id].max_active_playbooks:
                selection_errors.append(f"{role_id}: active playbook limit exceeded")
            for selected in selection.playbooks:
                source = registry.playbooks[selected.playbook_id].source
                forbidden = [
                    section
                    for section in selected.sections
                    if source.section_modes[section] in {"reference_only", "calibration_only"}
                ]
                if forbidden:
                    selection_errors.append(f"{role_id}: runtime selected {forbidden}")
                modes = {source.section_modes[section] for section in selected.sections}
                if task_type in {"repair", "humanize"} and "task" in modes:
                    selection_errors.append(f"{role_id}: repair task loaded a creation/review lane")
                if registry.roles[role_id].role_family == "review" and "trigger" in modes:
                    selection_errors.append(f"{role_id}: review task loaded a repair lane")
        author = registry.resolve("chapter_write")
        humanizer = registry.resolve("humanize")
        if author.contract_hash == humanizer.contract_hash or author.required_playbook_ids == humanizer.required_playbook_ids:
            selection_errors.append("chapter author and Humanizer are not professionally differentiated")
    add_check(
        checks,
        "progressive_prompt_selection",
        not selection_errors,
        {"errors": selection_errors},
        "修复角色区段、方法模块选择或角色职责区分。",
    )

    source_files = [
        root / relative
        for relative in PROTOCOL_SURFACE_FILES
        if relative != "src/longform_engine/agent_protocol_readiness.py"
        and (root / relative).is_file()
    ]
    source_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in source_files)
    retired_hits = [marker for marker in RETIRED_RUNTIME_MARKERS if marker in source_text]
    process_hits = [marker for marker in FORBIDDEN_PROCESS_MARKERS if marker in source_text]
    fixed_budget_hits = [
        pattern.pattern
        for pattern in FORBIDDEN_FIXED_PROMPT_BUDGET_PATTERNS
        if pattern.search(source_text)
    ]
    add_check(
        checks,
        "retired_runtime_removed",
        not retired_hits,
        {"markers": retired_hits, "source_tree_checked": bool(source_files)},
        "删除仍可执行的已退役协议、结果适配器和分裂语义任务。",
    )
    add_check(
        checks,
        "single_process_orchestration",
        not process_hits,
        {"markers": process_hits, "source_tree_checked": bool(source_files)},
        "移除生产运行时中的进程池、后台 worker 或子进程调度。",
    )
    add_check(
        checks,
        "fixed_prompt_budget_removed",
        not fixed_budget_hits,
        {"markers": fixed_budget_hits, "source_tree_checked": bool(source_files)},
        "删除 Python 中固定字符失败阈值，统一使用资源化自适应预算。",
    )

    failures = [item for item in checks if item["status"] == "fail"]
    protocol_ready = not failures
    production_chain_ready = protocol_ready
    literary_evidence_ready = False
    professional_prompt_ready = not any(
        item["id"] == "professional_prompt_calibration" and item["status"] == "fail"
        for item in checks
    )
    skill_records = {
        "codex": skill_record(root / "longform-novel-codex"),
        "claude_code": skill_record(root / "longform-novel-claude"),
    }
    return {
        "schema": SCHEMA,
        "ready_for_data_pipeline": protocol_ready and production_chain_ready,
        "protocol_ready": protocol_ready,
        "production_chain_ready": production_chain_ready,
        "literary_evidence_ready": literary_evidence_ready,
        "professional_prompt_ready": professional_prompt_ready,
        "repository": str(root),
        "provenance": {
            "engine_version": __version__,
            "skills": skill_records,
            "role_resource_sha256": directory_hash(root / "config" / "agent_roles"),
            "protocol_surface_sha256": protocol_surface_hash(root),
            "execution_model": "single_process_sequential",
        },
        "checks": checks,
        "summary": {
            "passed": sum(item["status"] == "pass" for item in checks),
            "failures": len(failures),
        },
        "blocking_reasons": [item["id"] for item in failures],
        "literary_evidence_blockers": [
            "original_five_chapter_human_review_missing",
            "fanfiction_five_chapter_human_review_missing",
            "independent_blind_review_missing",
        ],
        "next_command": (
            failures[0]["next_command"]
            if failures
            else "longform-engine production next project.yaml"
        ),
    }


def require_agent_data_pipeline_readiness(
    repository: str | Path | None = None,
    *,
    requested: bool,
) -> dict[str, Any] | None:
    """Raise before task compilation when the installed structural contract is invalid."""

    if not requested:
        return None
    report = check_agent_data_pipeline_readiness(repository)
    if not report["ready_for_data_pipeline"]:
        reasons = ", ".join(report["blocking_reasons"]) or "unknown readiness failure"
        raise AgentDataPipelineBlocked(
            f"Agent-first data pipeline is blocked: {reasons}. Next: {report['next_command']}"
        )
    return report


def render_agent_data_pipeline_readiness(report: dict[str, Any]) -> str:
    state = "READY" if report.get("ready_for_data_pipeline") else "BLOCKED"
    lines = [
        f"Agent-first data pipeline readiness: {state}",
        f"Engine: {report.get('provenance', {}).get('engine_version') or 'unknown'}",
        f"Execution: {report.get('provenance', {}).get('execution_model') or 'unknown'}",
        f"Protocol ready: {bool(report.get('protocol_ready'))}",
        f"Production chain ready: {bool(report.get('production_chain_ready'))}",
        f"Literary evidence ready: {bool(report.get('literary_evidence_ready'))}",
    ]
    for item in report.get("checks") or []:
        lines.append(f"[{str(item.get('status')).upper()}] {item.get('id')}")
        if item.get("status") == "fail":
            lines.append(f"  Next: {item.get('next_command')}")
    lines.append(f"Next command: {report.get('next_command')}")
    return "\n".join(lines)


def protocol_surface_hash(root: Path) -> str:
    paths: set[Path] = set()
    for relative in PROTOCOL_SURFACE_FILES:
        path = root / relative
        if path.is_file():
            paths.add(path)
    role_dir = root / "config" / "agent_roles"
    if role_dir.is_dir():
        paths.update(path for path in role_dir.rglob("*") if path.is_file())
    return hash_paths(root, paths)


def skill_record(path: Path) -> dict[str, str]:
    return {
        "version": __version__,
        "sha256": directory_hash(path),
    }


def directory_hash(path: Path) -> str:
    return tree_hash(path) if path.is_dir() else ""


def hash_paths(root: Path, paths: Iterable[Path]) -> str:
    digest = sha256()
    for path in sorted(set(paths), key=lambda item: item.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def professional_prompt_evidence(
    root: Path,
    *,
    registry: Any,
    facets: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[str], dict[str, Any]]:
    """Build reproducible evidence for all professional Prompt source objects."""

    errors: list[str] = []
    inventory: dict[str, Any] = {"roles": [], "playbooks": [], "facets": [], "item_count": 0}
    fixture_path = root / "config" / "agent_protocol_acceptance_fixtures.yaml"
    try:
        payload = yaml.safe_load(fixture_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        return [f"professional calibration fixture cannot be read: {exc}"], inventory
    calibration = payload.get("professional_prompt_calibration")
    if not isinstance(calibration, dict):
        return ["professional_prompt_calibration must be a mapping"], inventory

    role_cases = calibration.get("roles")
    playbook_cases = calibration.get("playbooks")
    facet_cases = calibration.get("facets")
    if not isinstance(role_cases, dict):
        errors.append("professional role calibration must be a mapping")
        role_cases = {}
    if not isinstance(playbook_cases, dict):
        errors.append("professional Playbook calibration must be a mapping")
        playbook_cases = {}
    if not isinstance(facet_cases, dict):
        errors.append("professional facet calibration must be a mapping")
        facet_cases = {}

    expected_role_ids = set(registry.roles) if registry else set()
    expected_playbook_ids = set(registry.playbooks) if registry else set()
    if set(role_cases) != expected_role_ids:
        errors.append(_coverage_error("role", expected_role_ids, set(role_cases)))
    if set(playbook_cases) != expected_playbook_ids:
        errors.append(_coverage_error("Playbook", expected_playbook_ids, set(playbook_cases)))

    flattened_facet_cases: dict[tuple[str, str], Any] = {}
    for kind, values in facet_cases.items():
        if not isinstance(values, dict):
            errors.append(f"facet calibration {kind} must be a mapping")
            continue
        for facet_id, case in values.items():
            flattened_facet_cases[(str(kind), str(facet_id))] = case
    expected_facet_ids = {
        (str(kind), str(facet_id))
        for kind, values in facets.items()
        for facet_id in values
    }
    if set(flattened_facet_cases) != expected_facet_ids:
        errors.append(
            _coverage_error(
                "facet",
                {f"{kind}:{facet_id}" for kind, facet_id in expected_facet_ids},
                {f"{kind}:{facet_id}" for kind, facet_id in flattened_facet_cases},
            )
        )

    calibration_text_owners: dict[str, str] = {}
    for prefix, cases in (
        ("role", role_cases),
        ("playbook", playbook_cases),
        (
            "facet",
            {f"{kind}:{facet_id}": case for (kind, facet_id), case in flattened_facet_cases.items()},
        ),
    ):
        for object_id, case in cases.items():
            fixture_id = f"{prefix}:{object_id}"
            if not isinstance(case, dict) or set(case) != {"positive", "negative", "boundary"}:
                errors.append(f"{fixture_id}: calibration must contain exactly positive/negative/boundary")
                continue
            for case_kind in ("positive", "negative", "boundary"):
                text = str(case.get(case_kind) or "").strip()
                owner = f"{fixture_id}:{case_kind}"
                if not text or not contains_cjk(text):
                    errors.append(f"{owner}: calibration must be non-empty Chinese text")
                    continue
                normalized = re.sub(r"\s+", "", text)
                duplicate = calibration_text_owners.get(normalized)
                if duplicate:
                    errors.append(f"{owner}: duplicates calibration text from {duplicate}")
                calibration_text_owners[normalized] = owner

    if registry:
        diagnostic_owners: dict[str, str] = {}
        for role_id, role in sorted(registry.roles.items()):
            diagnostics = role.prompt_sections.get("diagnostics", "").strip()
            diagnostic_hash = sha256(diagnostics.encode("utf-8")).hexdigest()
            duplicate = diagnostic_owners.get(diagnostic_hash)
            if duplicate:
                errors.append(f"{role_id}: diagnostics duplicate {duplicate}")
            diagnostic_owners[diagnostic_hash] = role_id

            task_type, declared_role_id = _representative_role_task(registry, role_id)
            try:
                selection = registry.select_prompt(
                    task_type,
                    declared_role_id=declared_role_id,
                )
            except (KeyError, RuntimeError, ValueError) as exc:
                errors.append(f"{role_id}: representative Prompt selection failed: {exc}")
                continue
            selected_text = [role.prompt_sections[item] for item in selection.role_sections]
            selected_playbooks: list[dict[str, Any]] = []
            for selected in selection.playbooks:
                source = registry.playbooks[selected.playbook_id].source
                selected_text.extend(source.sections[item] for item in selected.sections)
                selected_playbooks.append(
                    {"id": selected.playbook_id, "sections": list(selected.sections)}
                )
            inventory["roles"].append(
                {
                    "id": role_id,
                    "fixture_id": f"role:{role_id}",
                    "contract_hash": role.contract_hash,
                    "diagnostics_hash": diagnostic_hash,
                    "representative_task": task_type,
                    "loaded_role_sections": list(selection.role_sections),
                    "loaded_playbooks": selected_playbooks,
                    "selection_hash": selection.selection_hash,
                    "estimated_units": estimate_text_units("\n\n".join(selected_text)),
                }
            )

        method_owners: dict[str, str] = {}
        for playbook_id, playbook in sorted(registry.playbooks.items()):
            source = playbook.source
            review = source.sections.get("review", "").strip()
            repair = source.sections.get("repair", "").strip()
            method_text = f"{review}\n{repair}"
            method_hash = sha256(method_text.encode("utf-8")).hexdigest()
            duplicate = method_owners.get(method_hash)
            if duplicate:
                errors.append(f"{playbook_id}: review/repair method duplicates {duplicate}")
            method_owners[method_hash] = playbook_id
            if "诊断分支" not in review or len(re.findall(r"(?m)^-\s+", review)) < 3:
                errors.append(f"{playbook_id}: review requires at least three specific diagnostic branches")
            if "保护项" not in repair:
                errors.append(f"{playbook_id}: repair requires a specific preserve rule")
            inventory["playbooks"].append(
                {
                    "id": playbook_id,
                    "fixture_id": f"playbook:{playbook_id}",
                    "source_hash": source.source_hash,
                    "section_hashes": dict(source.section_hashes),
                    "section_modes": dict(source.section_modes),
                    "method_hash": method_hash,
                    "estimated_units": estimate_text_units(source.source_text),
                }
            )

    adapter_owners: dict[str, str] = {}
    for kind, values in sorted(facets.items()):
        for facet_id, value in sorted(values.items()):
            adapter = str(value.get("prompt_adapter") or "").strip()
            adapter_hash = sha256(adapter.encode("utf-8")).hexdigest()
            duplicate = adapter_owners.get(adapter_hash)
            if duplicate:
                errors.append(f"{kind}:{facet_id}: adapter duplicates {duplicate}")
            adapter_owners[adapter_hash] = f"{kind}:{facet_id}"
            clauses = [item.strip() for item in re.split(r"[。；]", adapter) if item.strip()]
            if len(clauses) < 4:
                errors.append(f"{kind}:{facet_id}: adapter lacks conflict/evidence/progression/boundary depth")
            inventory["facets"].append(
                {
                    "id": f"{kind}:{facet_id}",
                    "fixture_id": f"facet:{kind}:{facet_id}",
                    "source_hash": str(value.get("sha256") or ""),
                    "adapter_hash": adapter_hash,
                    "estimated_units": estimate_text_units(adapter),
                }
            )

    inventory["item_count"] = sum(
        len(inventory[kind]) for kind in ("roles", "playbooks", "facets")
    )
    if inventory["item_count"] != 84:
        errors.append(f"professional Prompt inventory must contain 84 items, got {inventory['item_count']}")
    inventory["calibration_fixture_sha256"] = sha256(fixture_path.read_bytes()).hexdigest()
    return errors, inventory


def _representative_role_task(registry: Any, role_id: str) -> tuple[str, str]:
    task_types = sorted(
        task_type
        for task_type, mapped_role_id in registry.task_role_map.items()
        if mapped_role_id == role_id
    )
    if task_types:
        return task_types[0], ""
    if role_id in registry.editorial_role_map:
        return "editorial_review", role_id
    raise ValueError(f"role {role_id} has no registered task")


def _coverage_error(label: str, expected: set[str], actual: set[str]) -> str:
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    return f"{label} calibration coverage mismatch; missing={missing}, unexpected={unexpected}"


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def _role_section_mode(role: Any, section: str) -> str:
    if section in role.always_sections:
        return "always"
    if section in role.task_sections:
        return "task"
    if section in role.trigger_sections.values():
        return "trigger"
    if section == "calibration":
        return "calibration_only"
    return ""


def add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    ok: bool,
    detail: Any,
    next_command: str,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "pass" if ok else "fail",
            "detail": detail,
            "next_command": "" if ok else next_command,
        }
    )
