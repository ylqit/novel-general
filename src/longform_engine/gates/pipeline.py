"""Deterministic quality gates for draft chapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.agent_protocols import (
    EVIDENCE_REVIEW_SCHEMA,
    build_validation_report,
    output_protocol_for_task,
    validate_evidence_review,
    validate_review_evidence_for_source,
)
from longform_engine.chapter_contract import ChapterContractError, load_verified_chapter_contract
from longform_engine.agent_tasks import (
    AgentTaskContractError,
    build_manifest,
    list_manifests,
    manifest_input_paths,
    mark_tasks_for_output,
    resolve_under_root,
    resolve_candidate_task,
    supersede_other_candidate_tasks,
    update_task_status,
    validate_current_task_result,
    write_manifest,
)
from longform_engine.config import ConfigDocument
from longform_engine.character_expression import character_expression_diagnostics
from longform_engine.creative import detect_humanizer_issues, reader_experience_review
from longform_engine.db import database_path, sync_database
from longform_engine.graph import check_graph
from longform_engine.memory import deterministic_evidence_gate_findings
from longform_engine.planning import evaluate_event_matrix, event_type_marker_count, infer_event_types_from_text
from longform_engine.prompting import estimate_text_units, resolve_context_budget_contract
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import (
    chapter_filename,
    existing_manuscript_chapter_path,
    manuscript_chapter_path,
    parse_canonical_chapter_number,
)
from longform_engine.text_metrics import content_character_count


class GateError(ValueError):
    """Raised when a gate command cannot run."""


@dataclass(frozen=True)
class GateCheckResult:
    """Public result for gate-check."""

    chapter_number: int
    passed: bool
    severity: str
    gate_result: str
    failures: tuple[dict[str, Any], ...]
    allowed_actions: tuple[str, ...]


@dataclass(frozen=True)
class PacingReviewResult:
    """Public result for pacing-review."""

    chapter_number: int
    report_file: str
    tier: str
    issues: tuple[str, ...]
    warnings: tuple[str, ...]
    reader_experience_report: str = ""


@dataclass(frozen=True)
class SemanticPacingTaskResult:
    chapter_number: int
    task_json: str
    task_markdown: str
    manifest_file: str
    output_file: str
    source_file: str
    next_command: str


@dataclass(frozen=True)
class SemanticPacingValidateResult:
    chapter_number: int
    ok: bool
    file: str
    report_file: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class SemanticPacingApplyResult:
    chapter_number: int
    applied: bool
    result_file: str
    validation_file: str
    gate_result: str
    pacing_review: str
    escalated_failures: int
    next_command: str


@dataclass(frozen=True)
class SemanticReviewTaskResult:
    chapter_number: int
    task_markdown: str
    manifest_file: str
    output_file: str
    source_file: str
    next_command: str


@dataclass(frozen=True)
class SemanticReviewValidateResult:
    chapter_number: int
    ok: bool
    file: str
    report_file: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class SemanticReviewApplyResult:
    chapter_number: int
    applied: bool
    application_file: str
    gate_result: str
    blocking_findings: int
    next_command: str


def build_semantic_review_context(
    root: Path,
    *,
    chapter_number: int,
    source_path: Path,
    source_text: str,
    canonical_inputs: list[Path],
    fanfiction: bool,
) -> dict[str, Any]:
    """Compile bounded canonical evidence without exposing full project state to the Agent."""

    payloads = {relative_path(root, path): load_json(path, default={}) for path in canonical_inputs}
    chapter_ref = f"20_outline/chapter_cards/ch{chapter_number:03d}.json"
    chapter_card = payloads.get(chapter_ref, {})
    if not isinstance(chapter_card, dict):
        chapter_card = {}
    character_payload = payloads.get("10_bible/characters.json", {})
    graph_payload = payloads.get("30_state/story_graph.json", {})
    tcs_payload = payloads.get(f"30_state/tcs/ch{chapter_number:03d}.json", {})
    try:
        verified_contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    except ChapterContractError as exc:
        raise GateError(str(exc)) from exc
    source_canon = payloads.get("10_bible/fanfiction/source_canon.json", {})
    fanfiction_bible = payloads.get("10_bible/fanfiction/fanfiction_bible.json", {})
    participant_ids = semantic_review_participant_ids(
        chapter_card,
        source_text=source_text,
        identity_sources=(character_payload, source_canon, fanfiction_bible),
    )
    canon_refs = dedupe_strings(normalize_strings(chapter_card.get("canon_refs")))
    match_terms = dedupe_strings([*participant_ids, *canon_refs])

    raw_sections = {
        "chapter_contract": verified_contract,
        "current_state": tcs_payload,
        "characters": semantic_review_matching_records(character_payload, participant_ids),
        "story_graph": semantic_review_matching_records(graph_payload, participant_ids),
    }
    if fanfiction:
        raw_sections["fanfiction"] = {
            "source_canon_matches": semantic_review_matching_records(source_canon, match_terms),
            "design_matches": semantic_review_matching_records(fanfiction_bible, participant_ids),
            "voice_contracts": semantic_review_voice_contracts(fanfiction_bible, participant_ids),
            "declared_continuity": semantic_review_declared_fanfiction_policy(fanfiction_bible),
        }

    budget_contract = resolve_context_budget_contract(root)
    visible_character_units = max(
        1.0,
        float(budget_contract.estimator["cjk_unit_weight"])
        * float(budget_contract.estimator["safety_multiplier"]),
    )
    packet_character_budget = max(
        3_000,
        int((budget_contract.input_soft_units * 0.6) / visible_character_units),
    )
    section_weights = {
        "chapter_contract": 0.19,
        "current_state": 0.11,
        "characters": 0.19,
        "story_graph": 0.10,
        "fanfiction": 0.33,
    }
    active_weight = sum(section_weights[key] for key in raw_sections)
    section_budgets = {
        key: max(350, int(packet_character_budget * section_weights[key] / active_weight))
        for key in raw_sections
    }
    sections: dict[str, Any] = {}
    section_selection: dict[str, dict[str, Any]] = {}
    for key, raw_value in raw_sections.items():
        terms = participant_ids if key in {"characters", "story_graph"} else match_terms
        critical = key in {"chapter_contract", "characters", "fanfiction"} and bool(raw_value)
        compiled_value = (
            raw_value
            if critical
            else fit_semantic_context_value(raw_value, section_budgets[key], terms)
        )
        source_chars = len(json.dumps(raw_value, ensure_ascii=False, separators=(",", ":")))
        compiled_chars = len(json.dumps(compiled_value, ensure_ascii=False, separators=(",", ":")))
        omitted = isinstance(compiled_value, dict) and compiled_value.get("omitted") is True
        if critical and (omitted or compiled_chars < source_chars or contains_depth_limited(compiled_value)):
            raise GateError(f"context_evidence_incomplete:{key}")
        sections[key] = compiled_value
        section_selection[key] = {
            "source_chars": source_chars,
            "compiled_chars": compiled_chars,
            "truncated": compiled_chars < source_chars,
            "omitted": omitted,
        }

    provenance = [
        {
            "path": path,
            "sha256": sha256_text(safe_read_text(root / path)),
            "selection_reason": semantic_review_selection_reason(path, fanfiction=fanfiction),
        }
        for path in payloads
    ]
    packet = {
        "schema": "semantic_review_context_v1",
        "chapter_number": chapter_number,
        "chapter_contract_hash": contract_hash,
        "source_path": relative_path(root, source_path),
        "source_hash": sha256_text(source_text),
        "participant_ids": participant_ids,
        "canon_refs": canon_refs,
        "allowed_canonical_refs": list(payloads),
        "sections": sections,
        "provenance": provenance,
        "selection": {
            "mode": "deterministic_relevant_projection",
            "full_canonical_files_exposed": False,
            "selected_source_count": len(payloads),
            "sections": section_selection,
            "notes": [
                "The packet is a bounded review aid; canonical files remain the facts verified by the CLI.",
                "Only the verified chapter contract, participants, declared canon references, and current state are projected.",
            ],
        },
    }
    serialized = json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
    packet["selection"]["compiled_chars"] = len(serialized)
    packet["selection"]["estimated_units"] = estimate_text_units(serialized, budget_contract.estimator)
    packet["selection"]["budget_profile"] = budget_contract.profile
    packet["selection"]["capacity_units"] = budget_contract.capacity_units
    if packet["selection"]["estimated_units"] > budget_contract.input_hard_units:
        raise GateError("context_evidence_incomplete:prompt_budget_exceeded")
    return packet


def semantic_review_participant_ids(
    chapter_card: dict[str, Any],
    *,
    source_text: str = "",
    identity_sources: tuple[Any, ...] = (),
) -> list[str]:
    values = [str(chapter_card.get("pov_character_id") or "")]
    for key in ("featured_character_ids", "voice_refs", "characterization_focus"):
        values.extend(normalize_strings(chapter_card.get(key)))
    lowered_source = source_text.casefold()
    for source in identity_sources:
        for record in semantic_review_all_records(source):
            character_id = str(
                record.get("character_id")
                or record.get("entity_id")
                or record.get("id")
                or ""
            ).strip()
            if not character_id:
                continue
            names: list[str] = []
            for key in ("name", "display_name", "game_name", "real_name"):
                names.extend(normalize_strings(record.get(key)))
            names.extend(normalize_strings(record.get("aliases")))
            aliases = [
                part.strip()
                for name in names
                for part in re.split(r"[/／|]", name)
                if len(part.strip()) >= 2
            ]
            if any(alias.casefold() in lowered_source for alias in aliases):
                values.append(character_id)
    return dedupe_strings([value.strip() for value in values if value.strip()])


def semantic_review_voice_contracts(value: Any, participant_ids: list[str]) -> list[dict[str, Any]]:
    allowed = {item.casefold() for item in participant_ids}
    contracts: list[dict[str, Any]] = []
    for record in semantic_review_all_records(value):
        character_id = str(record.get("character_id") or "").strip()
        if character_id.casefold() not in allowed:
            continue
        if not any(key in record for key in ("baseline_voice", "voice", "invariants", "forbidden_shortcuts")):
            continue
        contracts.append(record)
    return contracts[:8]


def semantic_review_chapter_record(value: Any, chapter_number: int) -> Any:
    for record in semantic_review_all_records(value):
        if int(record.get("chapter_number") or 0) == chapter_number:
            return record
    return {}


def semantic_review_matching_records(value: Any, terms: list[str]) -> list[dict[str, Any]]:
    if not terms:
        return []
    lowered = [term.casefold() for term in terms if term]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in semantic_review_all_records(value):
        identity = " ".join(
            str(record.get(key) or "")
            for key in ("id", "character_id", "entity_id", "thread_id", "name", "source_id")
        ).casefold()
        rendered = json.dumps(record, ensure_ascii=False, separators=(",", ":")).casefold()
        if not any(term in identity or term in rendered for term in lowered):
            continue
        signature = sha256_text(rendered)
        if signature in seen:
            continue
        seen.add(signature)
        result.append(record)
        if len(result) >= 16:
            break
    return result


def semantic_review_all_records(value: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def visit(item: Any, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(item, dict):
            if any(key in item for key in ("id", "character_id", "entity_id", "thread_id", "source_id")):
                records.append(item)
            for child in item.values():
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value, 0)
    return records


def semantic_review_declared_fanfiction_policy(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keys = (
        "continuity_mode",
        "canon_cutoff",
        "divergence_point",
        "divergence_points",
        "ooc_tolerance",
        "world_rule_changes",
        "relationship_boundaries",
    )
    return {key: value.get(key) for key in keys if value.get(key) not in (None, "", [], {})}


def fit_semantic_context_value(value: Any, max_chars: int, terms: list[str]) -> Any:
    for max_items, max_string in ((8, 320), (6, 240), (4, 180), (2, 120), (1, 80)):
        candidate = bound_semantic_context_value(
            value,
            terms=terms,
            max_items=max_items,
            max_string=max_string,
            depth=0,
        )
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) <= max_chars:
            return candidate
    return {
        "omitted": True,
        "reason": "section_exceeded_budget",
        "sha256": sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True)),
    }


def contains_depth_limited(value: Any) -> bool:
    if value == "[depth-limited]":
        return True
    if isinstance(value, dict):
        return any(contains_depth_limited(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_depth_limited(item) for item in value)
    return False


def bound_semantic_context_value(
    value: Any,
    *,
    terms: list[str],
    max_items: int,
    max_string: int,
    depth: int,
) -> Any:
    if depth >= 5:
        return "[depth-limited]"
    if isinstance(value, str):
        return value if len(value) <= max_string else value[: max_string - 1] + "..."
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, dict):
        items = list(value.items())[: max_items * 2]
        return {
            str(key): bound_semantic_context_value(
                item,
                terms=terms,
                max_items=max_items,
                max_string=max_string,
                depth=depth + 1,
            )
            for key, item in items
        }
    if isinstance(value, list):
        lowered = [term.casefold() for term in terms if term]
        ranked = sorted(
            enumerate(value),
            key=lambda pair: (
                0
                if any(
                    term in json.dumps(pair[1], ensure_ascii=False, separators=(",", ":")).casefold()
                    for term in lowered
                )
                else 1,
                pair[0],
            ),
        )
        return [
            bound_semantic_context_value(
                item,
                terms=terms,
                max_items=max_items,
                max_string=max_string,
                depth=depth + 1,
            )
            for _, item in ranked[:max_items]
        ]
    return str(value)[:max_string]


def semantic_review_selection_reason(path: str, *, fanfiction: bool) -> str:
    if "/chapter_cards/" in path:
        return "chapter contract"
    if path.startswith("30_state/tcs/"):
        return "current chapter state"
    if path.endswith("story_graph.json"):
        return "participant relationship state"
    if path.endswith("characters.json"):
        return "participant identity and motivation"
    if path.endswith("outline_anchors.json"):
        return "current reveal boundary"
    if fanfiction and path.endswith("source_canon.json"):
        return "declared canon references"
    if fanfiction and path.endswith("fanfiction_bible.json"):
        return "continuity, divergence, and voice contract"
    return "declared semantic review source"


@dataclass(frozen=True)
class GateWaiverResult:
    """Result for a recorded gate waiver."""

    chapter_number: int
    waiver_file: str
    gate_result: str
    allowed: bool
    severity: str
    next_command: str


def gate_check(
    config: ConfigDocument,
    *,
    chapter_number: int,
    source: str = "draft",
    semantic: bool = False,
    sync_db: bool = True,
) -> GateCheckResult:
    """Run deterministic gates and write the artifact contract."""

    root = resolve_project_root(config)
    draft_path = chapter_text_path(root, chapter_number, source=source)
    if draft_path is None:
        raise GateError(f"Chapter text not found for ch{chapter_number:03d} ({source}).")
    text = safe_read_text(draft_path)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    failures.extend(check_meta_pollution(config, text))
    failures.extend(check_content_character_count(config, text))
    failures.extend(check_chapter_card(root, chapter_number, text))
    consistency_issues, consistency_warnings = run_consistency_check(config)
    failures.extend(consistency_issues)
    warnings.extend(consistency_warnings)
    pacing = pacing_review(config, chapter_number=chapter_number, source=source, semantic_reader=semantic)
    failures.extend({"code": "pacing", "severity": "P1", "message": issue} for issue in pacing.issues)
    warnings.extend(pacing.warnings)
    reverse_failures, reverse_warnings, reverse_brake = check_reverse_brake(config, root, chapter_number, text)
    failures.extend(reverse_failures)
    warnings.extend(reverse_warnings)
    style_failures, style_warnings = check_style_and_humanizer(config, text)
    failures.extend(style_failures)
    warnings.extend(style_warnings)
    fanfiction_failures, fanfiction_warnings = check_fanfiction_source_reproduction(config, root, text)
    failures.extend(fanfiction_failures)
    warnings.extend(fanfiction_warnings)
    deterministic_evidence_report = None
    if semantic:
        semantic_failures, semantic_warnings, deterministic_evidence_report = deterministic_evidence_gate_findings(
            config,
            chapter_number=chapter_number,
            text=text,
        )
        failures.extend(semantic_failures)
        warnings.extend(semantic_warnings)
        review_failures, review_warnings, review_state = semantic_review_gate_items(
            config,
            chapter_number=chapter_number,
            source_path=draft_path,
            deterministic_failures=semantic_failures,
        )
        failures.extend(review_failures)
        warnings.extend(review_warnings)
    else:
        review_state = {"required": False, "status": "not_requested"}

    severity = max_severity(failures)
    passed = severity not in {"P0", "P1"}
    reviews_pending = bool(
        isinstance(review_state, dict)
        and review_state.get("required") is True
        and str(review_state.get("status") or "") != "applied"
    )
    if reviews_pending:
        allowed_actions = ("complete_reviews",)
        next_command = "longform-engine production next project.yaml"
    elif passed:
        allowed_actions = ("complete_reviews", "finalize_chapter")
        next_command = "longform-engine production next project.yaml"
    else:
        allowed_actions = ("complete_reviews",)
        next_command = "longform-engine production next project.yaml"

    write_artifact_reports(
        artifact_dir,
        chapter_number=chapter_number,
        draft_path=draft_path,
        failures=failures,
        warnings=warnings,
        pacing=pacing,
        reverse_brake=reverse_brake,
    )
    gate_payload = {
        "chapter_number": chapter_number,
        "passed": passed,
        "severity": "PASS" if passed else severity,
        "failures": failures,
        "warnings": warnings,
        "allowed_actions": list(allowed_actions),
        "next_command": next_command,
        "artifact_dir": str(artifact_dir),
        "source_path": relative_path(root, draft_path),
        "source_sha256": hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        "deterministic_evidence_enabled": semantic,
        "deterministic_evidence_report": str(deterministic_evidence_report) if deterministic_evidence_report else "",
        "semantic_enabled": semantic,
        "semantic_report": str(deterministic_evidence_report) if deterministic_evidence_report else "",
        "agent_semantic_review": review_state,
        "workflow_stage": "reviews_pending" if reviews_pending else "review_barrier",
        "reverse_brake_report": str(artifact_dir / "reverse_brake_report.md"),
        "reverse_brake": reverse_brake,
        "updated_at": utc_now(),
    }
    gate_path = artifact_dir / "gate_result.json"
    write_json(gate_path, gate_payload)
    if sync_db:
        sync_database(config)
    return GateCheckResult(
        chapter_number=chapter_number,
        passed=passed,
        severity=gate_payload["severity"],
        gate_result=str(gate_path),
        failures=tuple(failures),
        allowed_actions=allowed_actions,
    )


def semantic_review_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    source: str = "draft",
) -> SemanticReviewTaskResult:
    """Create an evidence-backed semantic review task for a high-risk chapter."""

    if chapter_number <= 0:
        raise GateError("chapter_number must be positive.")
    root = resolve_project_root(config)
    chapter_path = chapter_text_path(root, chapter_number, source=source)
    if chapter_path is None:
        raise GateError(f"Chapter text not found for ch{chapter_number:03d} ({source}).")
    artifact_dir = gate_artifact_dir(root, chapter_number)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    task_md = artifact_dir / "semantic_review_task.md"
    context_file = artifact_dir / "semantic_review_context.json"
    output_file = artifact_dir / "semantic_review_result.json"
    manifest_file = artifact_dir / "semantic_review_task.agent_task.json"
    canonical_inputs = [
        root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json",
        root / "30_state" / "story_graph.json",
        root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
        root / "10_bible" / "characters.json",
    ]
    if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction":
        canonical_inputs.extend(
            [
                root / "10_bible" / "fanfiction" / "source_canon.json",
                root / "10_bible" / "fanfiction" / "fanfiction_bible.json",
            ]
        )
    canonical_inputs = [path for path in canonical_inputs if path.exists()]
    source_text = safe_read_text(chapter_path)
    source_rel = relative_path(root, chapter_path)
    context_payload = build_semantic_review_context(
        root,
        chapter_number=chapter_number,
        source_path=chapter_path,
        source_text=source_text,
        canonical_inputs=canonical_inputs,
        fanfiction=str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction",
    )
    write_json(context_file, context_payload)
    atomic_write_text(
        task_md,
        "\n".join(
            [
                f"# Agent Semantic Review ch{chapter_number:03d}",
                "",
                "## Objective",
                "",
                "Judge motivation, location, ability boundaries, relationship changes, foreshadowing leakage, and causal continuity.",
                "For fanfiction, also judge canon voice, relationship phase, source-world rules, divergence causality, "
                "canon character agency, and original contribution. Declared AU/divergence is not itself an OOC error.",
                "Flag skin-only characterization, collective irrationality, canon characters used only as props, "
                "or retained setting names whose declared rules no longer operate without causal support.",
                "Every finding must cite an exact chapter character span and declared canonical references.",
                "",
                "## Required Input",
                "",
                f"- `{source_rel}` (sha256 `{sha256_text(source_text)}`)",
                "",
                "## Compiled Canonical Context",
                "",
                f"- Read only `{relative_path(root, context_file)}` for bounded canonical facts and allowed references.",
                "- Do not open the canonical source files listed inside that packet; the CLI will verify cited references.",
                "",
                "## Output Contract",
                "",
                f"- Write one `{EVIDENCE_REVIEW_SCHEMA}` JSON: `{relative_path(root, output_file)}`",
                "- coverage: canonical_fact, motivation, space_time_ability；每项写 status、1-2 个正文 evidence_ids 和 canonical_refs。",
                "- finding codes: CANONICAL_CONFLICT, MOTIVATION_JUMP, SPACE_TIME_ABILITY_BREAK.",
                "- evidence_ids use current source path or filename plus @start:end; CLI supplies chapter/path/hash/time.",
                f"- Validate: `longform-engine gate semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}`",
                f"- Apply: `longform-engine gate semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}`",
                "- Never edit final/RAG/graph/TCS/SQLite or canonical source files.",
                "",
            ]
        ),
    )
    manifest_inputs = [task_md, chapter_path, context_file]
    manifest = build_manifest(
        root,
        task_type="semantic_review",
        chapter_number=chapter_number,
        input_files=manifest_inputs,
        allowed_output_paths=[output_file],
        output_schema=output_protocol_for_task("semantic_review"),
        validate_command=f"longform-engine gate semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
        apply_command=f"longform-engine gate semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
        failure_next_command=f"longform-engine gate semantic-task project.yaml --chapter {chapter_number}",
        context_policy={
            "required_files": [
                relative_path(root, task_md),
                source_rel,
                relative_path(root, context_file),
            ],
            "optional_files": [],
            "forbidden_paths": [
                "40_manuscript/final/",
                "50_workbench/agent_drafts/",
                "60_rag/query_cache/",
                "70_runtime/db/",
            ],
            "compiled_brief": relative_path(root, task_md),
            "selection_report": relative_path(root, context_file),
        },
    )
    write_manifest(root, manifest, manifest_file)
    return SemanticReviewTaskResult(
        chapter_number=chapter_number,
        task_markdown=str(task_md),
        manifest_file=str(manifest_file),
        output_file=str(output_file),
        source_file=str(chapter_path),
        next_command=f"longform-engine gate semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
    )


def semantic_review_validate(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> SemanticReviewValidateResult:
    """Validate chapter spans and canonical references in an Agent semantic review."""

    root = resolve_project_root(config)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    path = resolve_semantic_review_result_path(root, artifact_dir, file_path)
    payload = load_json(path, default={})
    errors: list[str] = []
    warnings: list[str] = []
    task, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type="semantic_review",
        output_path=path,
        allowed_statuses=("submitted", "validated"),
    )
    errors.extend(control_errors)
    if not isinstance(payload, dict):
        payload = {}
        errors.append("semantic review result must be a JSON object.")
    expected_dimensions = {"canonical_fact", "motivation", "space_time_ability"}
    allowed_codes = {"CANONICAL_CONFLICT", "MOTIVATION_JUMP", "SPACE_TIME_ABILITY_BREAK"}
    errors.extend(
        validate_evidence_review(
            payload,
            required_dimensions=expected_dimensions,
            allowed_finding_codes=allowed_codes,
            canonical_ref_dimensions=expected_dimensions,
        )
    )
    source = semantic_review_source_for_task(root, task, chapter_number)
    if source is None:
        errors.append("chapter source is missing.")
        source_text = ""
    else:
        source_text = safe_read_text(source)
    verdict = str(payload.get("verdict") or "").lower()
    if set((payload.get("coverage") or {}).keys()) != expected_dimensions:
        errors.append("coverage must contain exactly canonical_fact, motivation, space_time_ability.")
    if source is not None:
        _evidence, evidence_errors = validate_review_evidence_for_source(
            payload,
            source_path=relative_path(root, source),
            source_text=source_text,
        )
        errors.extend(evidence_errors)
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        if finding.get("code") not in allowed_codes:
            errors.append(f"findings[{index}].code is outside semantic-continuity scope.")
    if verdict == "repair" and not any(
        isinstance(item, dict) and str(item.get("severity") or "").upper() in {"P0", "P1"}
        for item in findings
    ):
        warnings.append("repair verdict contains no P0/P1 finding.")
    report_file = artifact_dir / "semantic_review_validation.json"
    ok = not errors
    next_command = (
        f"longform-engine gate semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, path)}"
        if ok
        else f"longform-engine gate semantic-task project.yaml --chapter {chapter_number}"
    )
    write_json(
        report_file,
        build_validation_report(
            ok=ok,
            stage="semantic_review_validate",
            subject=relative_path(root, path),
            errors=errors,
            warnings=warnings,
            blockers=errors,
            provenance={"chapter_number": chapter_number},
            next_command=next_command,
        ),
    )
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=path,
        to_status="validated" if ok else "invalid",
        command="gate semantic-validate",
        result=report_file,
        from_statuses=("awaiting_agent", "submitted", "invalid"),
    )
    return SemanticReviewValidateResult(
        chapter_number=chapter_number,
        ok=ok,
        file=str(path),
        report_file=str(report_file),
        errors=tuple(errors),
        warnings=tuple(warnings),
        next_command=next_command,
    )


def semantic_review_apply(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> SemanticReviewApplyResult:
    """Apply a validated review to gate artifacts and rerun all semantic gates."""

    validation = semantic_review_validate(config, chapter_number=chapter_number, file_path=file_path)
    if not validation.ok:
        raise GateError("semantic review result did not validate; no gate artifact was applied.")
    root = resolve_project_root(config)
    path = Path(validation.file)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    review_task, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type="semantic_review",
        output_path=path,
        allowed_statuses=("validated",),
    )
    source = semantic_review_source_for_task(root, review_task, chapter_number)
    if control_errors or source is None:
        detail = "; ".join(control_errors) or "the current semantic review source is unavailable"
        raise GateError(f"semantic review control-plane binding is invalid: {detail}")
    application_file = artifact_dir / "semantic_review_application.json"
    payload = load_json(path, default={})
    candidate_task = semantic_review_candidate_task(root, chapter_number)
    candidate_task_id = str(candidate_task.get("task_id") or "")
    submission_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.submission.json"
    candidate_manifest_paths = [
        root / str(task.get("manifest_file") or "")
        for task in list_manifests(root, chapter_number=chapter_number)
        if str(task.get("manifest_file") or "")
    ]
    blocking = sum(
        1
        for item in payload.get("findings", [])
        if isinstance(item, dict) and str(item.get("severity") or "").upper() in {"P0", "P1"}
    )
    with apply_transaction(
        root,
        command="gate semantic-apply",
        chapter_number=chapter_number,
        source_paths=[path, validation.report_file],
        touched_paths=[
            artifact_dir,
            root / "50_workbench" / "agent_tasks",
            root / "30_state" / "novel_state.json",
            root / "40_manuscript" / "chapter_meta.jsonl",
            submission_path,
            *candidate_manifest_paths,
        ],
        metadata={"gate_artifact_only": True, "blocking_findings": blocking},
    ):
        write_json(
            application_file,
            {
                "schema": "semantic_review_application_v1",
                "chapter_number": chapter_number,
                "result_file": relative_path(root, path),
                "source_hash": sha256_text(safe_read_text(source)) if source is not None else "",
                "payload": payload,
                "applied_at": utc_now(),
            },
        )
        gate_result = gate_check(config, chapter_number=chapter_number, semantic=True, sync_db=False)
        mark_tasks_for_output(
            root,
            chapter_number=chapter_number,
            output_path=path,
            to_status="applied",
            command="gate semantic-apply",
            result=application_file,
            from_statuses=("validated",),
        )
        update_task_status(
            root,
            candidate_task_id,
            to_status="submitted",
            command="gate semantic-apply",
            artifact=path,
            result=gate_result.gate_result,
        )
        supersede_other_candidate_tasks(
            root,
            chapter_number=chapter_number,
            current_task_id=candidate_task_id,
            command="gate semantic-apply",
            artifact=path,
        )
        update_semantic_review_stage_projection(
            root,
            chapter_number=chapter_number,
            candidate_task=candidate_task,
            gate_result_path=Path(gate_result.gate_result),
            passed=gate_result.passed and not blocking,
        )
    return SemanticReviewApplyResult(
        chapter_number=chapter_number,
        applied=True,
        application_file=str(application_file),
        gate_result=gate_result.gate_result,
        blocking_findings=blocking,
        next_command="longform-engine production next project.yaml",
    )


def semantic_review_candidate_task(root: Path, chapter_number: int) -> dict[str, Any]:
    submission_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.submission.json"
    submission = load_json(submission_path, default={})
    if not isinstance(submission, dict):
        raise GateError("Chapter submission metadata is missing; semantic review cannot identify the current candidate.")
    source_path = str(submission.get("candidate_source_path") or submission.get("source_file") or "")
    if not source_path:
        raise GateError("Chapter submission does not identify its candidate source path.")
    try:
        task = resolve_candidate_task(
            root,
            chapter_number=chapter_number,
            output_path=source_path,
        )
    except AgentTaskContractError as exc:
        raise GateError(str(exc)) from exc
    declared_id = str(submission.get("candidate_task_id") or "")
    if declared_id and declared_id != str(task.get("task_id") or ""):
        raise GateError("Chapter submission candidate_task_id does not own candidate_source_path.")
    return task


def update_semantic_review_stage_projection(
    root: Path,
    *,
    chapter_number: int,
    candidate_task: dict[str, Any],
    gate_result_path: Path,
    passed: bool,
) -> None:
    gate_payload = load_json(gate_result_path, default={})
    if not isinstance(gate_payload, dict):
        raise GateError("Semantic apply did not produce a readable gate result.")
    candidate = dict(gate_payload.get("candidate") or {})
    candidate.setdefault("task_id", str(candidate_task.get("task_id") or ""))
    candidate.setdefault("task_type", str(candidate_task.get("task_type") or ""))
    gate_payload["candidate"] = candidate
    gate_payload["workflow_stage"] = "reviews_pending"
    gate_payload["next_command"] = "longform-engine production next project.yaml"
    gate_payload["updated_at"] = utc_now()
    write_json(gate_result_path, gate_payload)

    state_path = root / "30_state" / "novel_state.json"
    state = load_json(state_path, default={})
    if not isinstance(state, dict):
        state = {}
    state["status"] = "reviews_pending"
    state["current_chapter"] = chapter_number
    state["pending_gate_chapter"] = chapter_number
    state["last_gate_result"] = relative_path(root, gate_result_path)
    state["updated_at"] = utc_now()
    state.pop("pending_semantic_review_chapter", None)
    state.pop("pending_final_chapter", None)
    write_json(state_path, state)

    meta_path = root / "40_manuscript" / "chapter_meta.jsonl"
    records: list[dict[str, Any]] = []
    if meta_path.exists():
        for line in safe_read_text(meta_path).splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    found = False
    for item in records:
        number = int(item.get("chapter_number") or item.get("chapter") or item.get("number") or 0)
        if number != chapter_number:
            continue
        item["status"] = "reviews_pending"
        item["gate_result"] = relative_path(root, gate_result_path)
        found = True
    if not found:
        records.append(
            {
                "chapter_number": chapter_number,
                "status": "reviews_pending",
                "gate_result": relative_path(root, gate_result_path),
            }
        )
    atomic_write_text(
        meta_path,
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records),
    )

    submission_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.submission.json"
    submission = load_json(submission_path, default={})
    if not isinstance(submission, dict):
        raise GateError("Chapter submission metadata became unreadable during semantic apply.")
    submission["candidate_task_id"] = str(candidate_task.get("task_id") or "")
    submission["candidate_task_type"] = str(candidate_task.get("task_type") or "")
    submission["candidate_status"] = "submitted"
    submission["updated_at"] = utc_now()
    write_json(submission_path, submission)


def pacing_review(
    config: ConfigDocument,
    *,
    chapter_number: int,
    source: str = "draft",
    semantic_reader: bool = False,
) -> PacingReviewResult:
    """Write a deterministic pacing review artifact."""

    root = resolve_project_root(config)
    chapter_path = chapter_text_path(root, chapter_number, source=source)
    if chapter_path is None:
        raise GateError(f"Chapter text not found for ch{chapter_number:03d} ({source}).")
    text = safe_read_text(chapter_path)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    tier = infer_pacing_tier(text)
    issues: list[str] = []
    warnings: list[str] = []
    pacing_config = config.data.get("pacing", {})
    history = load_json(root / "30_state" / "pacing_history.json", default=[])
    if not isinstance(history, list):
        history = []
    fast_cooldown = int(pacing_config.get("fast_chapter_cooldown") or 1)
    recent_fast = [
        item for item in history
        if isinstance(item, dict)
        and item.get("tier") == "fast"
        and chapter_number - int(item.get("chapter_number") or item.get("chapter") or 0) <= fast_cooldown
    ]
    if tier == "fast" and recent_fast:
        issues.append("fast chapter cooldown violated")

    quota = detect_quota_usage(text)
    if sum(1 for value in quota.values() if value) > int(pacing_config.get("max_major_quota_triggers_per_chapter") or 1):
        issues.append("A/B/C major quota overflow")
    if len(text) < 120:
        warnings.append("chapter is very short; pacing signal may be unreliable")
    if complete_core_reveal_detected(text):
        warnings.append("possible complete core secret reveal")

    card = load_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json", default={})
    event_recommendation = card.get("event_recommendation") if isinstance(card, dict) and isinstance(card.get("event_recommendation"), dict) else {}
    detected_event_types = infer_event_types_from_text(text)
    recommended_event_types = normalize_strings(event_recommendation.get("recommended")) if event_recommendation else []
    blocked_event_types = normalize_strings(event_recommendation.get("blocked")) if event_recommendation else []
    strong_detected_event_types = tuple(
        event_type
        for event_type in detected_event_types
        if event_type_marker_count(text, event_type) >= 2
    )
    weak_detected_event_types = tuple(
        event_type for event_type in detected_event_types if event_type not in strong_detected_event_types
    )
    active_event_types = strong_detected_event_types or tuple(recommended_event_types[:1])
    if weak_detected_event_types:
        warnings.append(
            "weak lexical event hints did not override the chapter plan: "
            + ", ".join(weak_detected_event_types)
        )
    matrix = evaluate_event_matrix(
        config,
        chapter_number=chapter_number,
        event_types=active_event_types,
        tier=tier,
    )
    issues.extend(f"{item.get('code')}: {item.get('message')}" for item in matrix.failures)
    warnings.extend(matrix.warnings)
    warnings.extend(matrix.constraints)
    blocked_used = [event_type for event_type in active_event_types if event_type in blocked_event_types]
    if blocked_used:
        issues.append(f"event matrix blocked event type used: {', '.join(blocked_used)}")

    reader_report = ""
    if semantic_reader:
        reader = reader_experience_review(
            config,
            chapter_number=chapter_number,
            text=text,
            artifact_dir=artifact_dir,
            tier=tier,
        )
        reader_report = str(reader.get("report_file") or "")
        issues.extend(str(issue) for issue in reader.get("issues", []) if str(issue).strip())
        warnings.extend(str(warning) for warning in reader.get("warnings", []) if str(warning).strip())

    report_path = artifact_dir / "pacing_review.md"
    atomic_write_text(
        report_path,
        "\n".join(
            [
                f"# Pacing Review ch{chapter_number:03d}",
                "",
                f"- Tier: {tier}",
                f"- Semantic reader review: {'enabled' if semantic_reader else 'disabled'}",
                f"- Reader experience report: {reader_report or 'none'}",
                f"- Quota used: {json.dumps(quota, ensure_ascii=False)}",
                f"- Detected event types: {', '.join(active_event_types) or 'none'}",
                f"- Matrix constraints: {', '.join(matrix.constraints) or 'none'}",
                "",
                "## Issues",
                "",
                *([f"- {issue}" for issue in issues] or ["- None"]),
                "",
                "## Warnings",
                "",
                *([f"- {warning}" for warning in warnings] or ["- None"]),
                "",
            ]
        ),
    )
    return PacingReviewResult(
        chapter_number=chapter_number,
        report_file=str(report_path),
        tier=tier,
        issues=tuple(issues),
        warnings=tuple(warnings),
        reader_experience_report=reader_report,
    )


def semantic_pacing_task(config: ConfigDocument, *, chapter_number: int, source: str = "draft") -> SemanticPacingTaskResult:
    """Generate a host-agent semantic pacing task without mutating gate decisions."""

    if chapter_number <= 0:
        raise GateError("chapter_number must be positive.")
    root = resolve_project_root(config)
    chapter_path = chapter_text_path(root, chapter_number, source=source)
    if chapter_path is None:
        raise GateError(f"Chapter text not found for ch{chapter_number:03d} ({source}).")
    artifact_dir = gate_artifact_dir(root, chapter_number)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output_file = artifact_dir / "semantic_pacing_result.json"
    task_json = artifact_dir / "semantic_pacing_task.json"
    task_md = artifact_dir / "semantic_pacing_task.md"
    manifest_file = artifact_dir / "semantic_pacing_task.agent_task.json"
    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    event_matrix_path = root / "30_state" / "event_matrix.json"
    pacing_history_path = root / "30_state" / "pacing_history.json"
    chapter_card = load_json(card_path, default={})
    try:
        verified_contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    except ChapterContractError as exc:
        raise GateError(str(exc)) from exc
    event_matrix = load_json(event_matrix_path, default={})
    pacing_history = load_json(pacing_history_path, default=[])
    if not isinstance(chapter_card, dict):
        chapter_card = {}
    if not isinstance(event_matrix, dict):
        event_matrix = {}
    if not isinstance(pacing_history, list):
        pacing_history = []
    task_payload = {
        "schema_version": 2,
        "chapter_number": chapter_number,
        "source_path": relative_path(root, chapter_path),
        "source_sha256": sha256_text(safe_read_text(chapter_path)),
        "planning_context": {
            "chapter_contract": verified_contract,
            "chapter_contract_hash": contract_hash,
            "event_recommendation": chapter_card.get("event_recommendation")
            or event_matrix.get("latest_recommendation")
            or {},
            "recent_pacing": pacing_history[-5:],
            "source_catalog": [
                {
                    "path": relative_path(root, path),
                    "sha256": sha256_text(safe_read_text(path)) if path.is_file() else "",
                    "selected_for": selected_for,
                }
                for path, selected_for in (
                    (card_path, "current chapter pacing contract"),
                    (event_matrix_path, "current event recommendation"),
                    (pacing_history_path, "last five applied pacing records"),
                )
            ],
        },
        "allowed_output_path": relative_path(root, output_file),
        "output_schema": {
            "schema": EVIDENCE_REVIEW_SCHEMA,
            "verdict": "pass|repair|need_human|insufficient_evidence",
            "coverage": {
                "pressure_release": "checked|insufficient|not_applicable",
                "beat_change": "checked|insufficient|not_applicable",
                "aftermath": "checked|insufficient|not_applicable",
            },
            "findings": [],
        },
        "instructions": [
            "Judge semantic pacing, escalation, reader pressure, tail hook, and reverse-brake risks.",
            "Do not edit final/RAG/graph/TCS/SQLite or gate_result.json directly.",
            "Return one evidence_review_v2 JSON at the allowed output path.",
        ],
        "created_at": utc_now(),
    }
    write_json(task_json, task_payload)
    atomic_write_text(
        task_md,
        "\n".join(
            [
                f"# Semantic Pacing Task ch{chapter_number:03d}",
                "",
                f"- Source: `{relative_path(root, chapter_path)}`",
                f"- Output JSON: `{relative_path(root, output_file)}`",
                f"- Validate: `longform-engine pacing semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}`",
                f"- Apply: `longform-engine pacing semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}`",
                "",
                "Judge semantic pacing only. Do not mutate final/RAG/graph/TCS/SQLite or gate_result.json directly.",
                "The task JSON already contains the bounded planning context. Do not open its source_catalog paths.",
                "",
                "## Output Protocol",
                "",
                "```json",
                json.dumps(task_payload["output_schema"], ensure_ascii=False, indent=2),
                "```",
                "",
                "Read the declared source chapter in full; do not rely on a duplicated excerpt.",
                "",
            ]
        ),
    )
    manifest = build_manifest(
        root,
        task_type="pacing_review",
        chapter_number=chapter_number,
        input_files=[task_md, chapter_path, task_json],
        allowed_output_paths=[output_file],
        output_schema=output_protocol_for_task("pacing_review"),
        validate_command=f"longform-engine pacing semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
        apply_command=f"longform-engine pacing semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
        failure_next_command=f"longform-engine pacing semantic-task project.yaml --chapter {chapter_number}",
        context_policy={
            "required_files": [task_md, chapter_path, task_json],
            "optional_files": [],
            "compiled_brief": task_md,
            "selection_report": task_json,
        },
    )
    write_manifest(root, manifest, manifest_file)
    return SemanticPacingTaskResult(
        chapter_number=chapter_number,
        task_json=str(task_json),
        task_markdown=str(task_md),
        manifest_file=str(manifest_file),
        output_file=str(output_file),
        source_file=str(chapter_path),
        next_command=f"longform-engine pacing semantic-validate project.yaml --chapter {chapter_number} --file {relative_path(root, output_file)}",
    )


def semantic_pacing_validate(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> SemanticPacingValidateResult:
    """Validate a semantic pacing result before gate application."""

    if chapter_number <= 0:
        raise GateError("chapter_number must be positive.")
    root = resolve_project_root(config)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    path = resolve_semantic_pacing_result_path(root, artifact_dir, file_path)
    errors: list[str] = []
    warnings: list[str] = []
    _task, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type="pacing_review",
        output_path=path,
        allowed_statuses=("submitted", "validated"),
    )
    errors.extend(control_errors)
    payload = load_json(path, default={})
    if not isinstance(payload, dict):
        payload = {}
        errors.append("semantic pacing result must be a JSON object.")
    expected_dimensions = {"pressure_release", "beat_change", "aftermath"}
    allowed_codes = {"BEAT_REPETITION", "TURN_TOO_ABRUPT", "AFTERMATH_MISSING"}
    errors.extend(
        validate_evidence_review(
            payload,
            required_dimensions=expected_dimensions,
            allowed_finding_codes=allowed_codes,
        )
    )
    chapter_path = chapter_text_path(root, chapter_number, source="draft")
    if chapter_path is None:
        errors.append(f"Current draft not found for ch{chapter_number:03d}.")
    else:
        expected_path = relative_path(root, chapter_path)
        expected_hash = sha256_text(safe_read_text(chapter_path))
        _evidence, evidence_errors = validate_review_evidence_for_source(
            payload,
            source_path=expected_path,
            source_text=safe_read_text(chapter_path),
        )
        errors.extend(evidence_errors)
    verdict = str(payload.get("verdict") or "").strip().lower()
    if set((payload.get("coverage") or {}).keys()) != expected_dimensions:
        errors.append("coverage must contain exactly pressure_release, beat_change, aftermath.")
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        if finding.get("code") not in allowed_codes:
            errors.append(f"findings[{index}].code is outside semantic-pacing scope.")
    if verdict == "repair" and not any(
        isinstance(item, dict) and str(item.get("severity") or "").upper() in {"P0", "P1"}
        for item in findings
    ):
        warnings.append("repair verdict has no P0/P1 finding.")
    report_file = artifact_dir / "semantic_pacing_validation.json"
    ok = not errors
    next_command = (
        f"longform-engine pacing semantic-apply project.yaml --chapter {chapter_number} --file {relative_path(root, path)}"
        if ok
        else f"longform-engine pacing semantic-task project.yaml --chapter {chapter_number}"
    )
    write_json(
        report_file,
        build_validation_report(
            ok=ok,
            stage="semantic_pacing_validate",
            subject=relative_path(root, path),
            errors=errors,
            warnings=warnings,
            blockers=errors,
            provenance={
                "chapter_number": chapter_number,
                "source_path": expected_path if chapter_path is not None else "",
                "source_sha256": expected_hash if chapter_path is not None else "",
            },
            next_command=next_command,
        ),
    )
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=path,
        to_status="validated" if ok else "invalid",
        command="pacing semantic-validate",
        result=report_file,
        from_statuses=("submitted", "validated"),
    )
    return SemanticPacingValidateResult(
        chapter_number=chapter_number,
        ok=ok,
        file=str(path),
        report_file=str(report_file),
        errors=tuple(errors),
        warnings=tuple(warnings),
        next_command=next_command,
    )


def semantic_pacing_apply(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> SemanticPacingApplyResult:
    """Apply validated semantic pacing findings into gate artifacts only."""

    root = resolve_project_root(config)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    path = resolve_semantic_pacing_result_path(root, artifact_dir, file_path)
    task, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type="pacing_review",
        output_path=path,
        allowed_statuses=("validated", "applied"),
    )
    if control_errors:
        raise GateError("semantic pacing result has not passed the required control-plane lifecycle: " + "; ".join(control_errors))
    assert task is not None
    if str(task.get("status") or "") == "applied":
        gate_path = artifact_dir / "gate_result.json"
        pacing_path = artifact_dir / "pacing_review.md"
        gate_payload = load_json(gate_path, default={})
        pacing = gate_payload.get("semantic_pacing") if isinstance(gate_payload, dict) else {}
        current_hash = sha256_text(safe_read_text(path))
        if (
            not isinstance(pacing, dict)
            or str(pacing.get("result_sha256") or "") != current_hash
            or not pacing_path.is_file()
        ):
            raise GateError("applied semantic pacing evidence is stale or incomplete; regenerate the pacing task.")
        passed = bool(gate_payload.get("passed"))
        next_command = "longform-engine production next project.yaml"
        return SemanticPacingApplyResult(
            chapter_number=chapter_number,
            applied=True,
            result_file=str(path),
            validation_file=str(artifact_dir / "semantic_pacing_validation.json"),
            gate_result=str(gate_path),
            pacing_review=str(pacing_path),
            escalated_failures=len(semantic_pacing_gate_items(load_json(path, default={}))[0]),
            next_command=next_command,
        )
    validation = semantic_pacing_validate(config, chapter_number=chapter_number, file_path=path)
    if not validation.ok:
        raise GateError("semantic pacing result did not validate; gate artifacts were not updated.")
    path = Path(validation.file)
    payload = load_json(path, default={})
    if not isinstance(payload, dict):
        raise GateError("semantic pacing result is not a JSON object.")
    gate_path = artifact_dir / "gate_result.json"
    pacing_path = artifact_dir / "pacing_review.md"
    with apply_transaction(
        root,
        command="pacing semantic-apply",
        chapter_number=chapter_number,
        source_paths=[path, validation.report_file],
        touched_paths=[artifact_dir, gate_path, pacing_path, database_path(config)],
        metadata={
            "gate_artifact_only": True,
            "rebuild_boundaries": ["SQLite sync"],
        },
    ) as transaction:
        if not gate_path.exists():
            gate_check(config, chapter_number=chapter_number)
        gate_payload = load_json(gate_path, default={})
        if not isinstance(gate_payload, dict):
            gate_payload = {}
        failures = [
            item
            for item in gate_payload.get("failures", [])
            if not (isinstance(item, dict) and str(item.get("code") or "").startswith("semantic_pacing"))
        ]
        warnings = [
            str(item)
            for item in gate_payload.get("warnings", [])
            if not str(item).startswith("semantic_pacing:")
        ]
        semantic_failures, semantic_warnings = semantic_pacing_gate_items(payload)
        current_source = chapter_text_path(root, chapter_number, source="draft")
        current_source_path = relative_path(root, current_source) if current_source is not None else ""
        current_source_hash = sha256_text(safe_read_text(current_source)) if current_source is not None else ""
        failures.extend(semantic_failures)
        warnings.extend(semantic_warnings)
        severity = max_severity(failures)
        passed = severity not in {"P0", "P1"}
        allowed_actions = ("complete_reviews", "finalize_chapter") if passed else ("complete_reviews",)
        next_command = "longform-engine production next project.yaml"
        gate_payload.update(
            {
                "passed": passed,
                "severity": "PASS" if passed else severity,
                "failures": failures,
                "warnings": warnings,
                "allowed_actions": list(allowed_actions),
                "next_command": next_command,
                "semantic_pacing_result": relative_path(root, path),
                "semantic_pacing": {
                    "verdict": payload.get("verdict"),
                    "coverage": payload.get("coverage", []),
                    "source_path": current_source_path,
                    "source_sha256": current_source_hash,
                    "result_sha256": sha256_text(safe_read_text(path)),
                    "findings": payload.get("findings", []),
                },
                "workflow_stage": "reviews_pending",
                "updated_at": utc_now(),
            }
        )
        write_json(gate_path, gate_payload)
        append_semantic_pacing_report(pacing_path, payload, relative_path(root, path))
        sync_database(config)
        transaction.update_metadata(escalated_failures=len(semantic_failures), passed=passed, db_synced=True)
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=path,
        to_status="applied",
        command="pacing semantic-apply",
        result=gate_path,
        from_statuses=("validated",),
    )
    return SemanticPacingApplyResult(
        chapter_number=chapter_number,
        applied=True,
        result_file=str(path),
        validation_file=validation.report_file,
        gate_result=str(gate_path),
        pacing_review=str(pacing_path),
        escalated_failures=len(semantic_failures),
        next_command=next_command,
    )


def record_waiver(
    config: ConfigDocument,
    *,
    chapter_number: int,
    reason: str,
    approved_by: str = "human",
) -> GateWaiverResult:
    """Record a human waiver for PASS/P2 gate outcomes."""

    if not reason.strip():
        raise GateError("waiver reason is required.")
    root = resolve_project_root(config)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    gate_path = artifact_dir / "gate_result.json"
    if not gate_path.exists():
        raise GateError(f"gate_result.json not found for ch{chapter_number:03d}.")
    gate = load_json(gate_path, default={})
    if not isinstance(gate, dict):
        raise GateError(f"Invalid gate_result.json for ch{chapter_number:03d}.")
    severity = str(gate.get("severity") or "UNKNOWN")
    if severity in {"P0", "P1"}:
        raise GateError(f"Cannot waive blocking severity {severity}; repair or rollback is required.")

    waiver = {
        "chapter_number": chapter_number,
        "allowed": True,
        "severity": severity,
        "reason": reason,
        "approved_by": approved_by,
        "created_at": utc_now(),
        "scope": "gate_result",
    }
    waiver_path = artifact_dir / "waiver.json"
    write_json(waiver_path, waiver)
    gate["waiver"] = waiver
    gate["waived"] = True
    actions = list(gate.get("allowed_actions") or [])
    if "continue_write_with_waiver" not in actions:
        actions.append("continue_write_with_waiver")
    gate["allowed_actions"] = actions
    gate["next_command"] = "continue-write"
    gate["updated_at"] = utc_now()
    write_json(gate_path, gate)
    sync_database(config)
    return GateWaiverResult(
        chapter_number=chapter_number,
        waiver_file=str(waiver_path),
        gate_result=str(gate_path),
        allowed=True,
        severity=severity,
        next_command="continue-write",
    )


def check_meta_pollution(config: ConfigDocument, text: str) -> list[dict[str, Any]]:
    patterns = config.data.get("gates", {}).get("p0_meta_pollution_patterns") or [
        "TODO",
        "写作说明",
        "作者按",
        "角色定位",
        "[说明]",
    ]
    failures = []
    for pattern in patterns:
        if pattern and pattern in text:
            failures.append(
                {
                    "code": "meta_pollution",
                    "severity": "P0",
                    "message": f"正文包含 meta/prompt 污染标记：{pattern}",
                }
            )
    if re.search(r"(?i)\b(as an ai|language model)\b", text):
        failures.append({"code": "meta_pollution", "severity": "P0", "message": "正文包含 AI 自述。"})
    return failures


def check_content_character_count(config: ConfigDocument, text: str) -> list[dict[str, Any]]:
    chapter_contract = config.data.get("length", {}).get("chapter", {})
    hard_min = int(chapter_contract.get("hard_min") or 0)
    hard_max = int(chapter_contract.get("hard_max") or 10**9)
    count = content_character_count(text)
    failures = []
    if count < hard_min:
        failures.append(
            {
                "code": "content_character_count",
                "severity": "P1",
                "message": f"正文字符数低于 hard_min：{count} < {hard_min}",
            }
        )
    if count > hard_max:
        failures.append(
            {
                "code": "content_character_count",
                "severity": "P1",
                "message": f"正文字符数高于 hard_max：{count} > {hard_max}",
            }
        )
    return failures


def check_chapter_card(root: Path, chapter_number: int, text: str) -> list[dict[str, Any]]:
    card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    if not card_path.exists():
        return [{"code": "chapter_card", "severity": "P1", "message": "章节卡缺失。"}]
    try:
        load_verified_chapter_contract(root, chapter_number)
    except ChapterContractError as exc:
        return [
            {
                "code": "chapter_contract_inconsistent",
                "severity": "P1",
                "message": str(exc),
            }
        ]
    failures = []
    if len(text.strip()) < 80:
        failures.append({"code": "chapter_goal", "severity": "P1", "message": "正文过短，无法判断是否履行章节目标。"})
    return failures


def run_consistency_check(config: ConfigDocument) -> tuple[list[dict[str, Any]], list[str]]:
    result = check_graph(config)
    failures = [{"code": "graph_consistency", "severity": "P1", "message": issue} for issue in result.issues]
    return failures, list(result.warnings)


def check_reverse_brake(
    config: ConfigDocument,
    root: Path,
    chapter_number: int,
    text: str,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    """Evaluate anti-resolution guardrails as an explicit gate report."""

    anchor = normalize_reverse_brake_anchor(config, chapter_number, current_outline_anchor(root, chapter_number))
    closure_allowed = bool(anchor.get("closure_allowed"))
    allowed_reveal_level = str(anchor.get("allowed_reveal_level") or "hint").lower()
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    forbidden_reveals = dedupe_strings(
        normalize_strings(anchor.get("forbidden_reveals"))
        + normalize_strings(config.data.get("gates", {}).get("forbidden_reveals"))
    )
    forbidden_hits = [item for item in forbidden_reveals if item and item.lower() in text.lower()]
    for item in forbidden_hits:
        if not closure_allowed:
            failures.append(
                {
                    "code": "anchor_forbidden_reveal",
                    "severity": "P1",
                    "message": f"outline anchor forbids revealing `{item}` before the planned closure.",
                    "repair_action": "remove the reveal or revise-outline to explicitly allow it",
                }
            )
    checks.append(
        {
            "name": "forbidden_reveals",
            "status": "fail" if forbidden_hits and not closure_allowed else "pass",
            "hits": forbidden_hits,
            "policy": forbidden_reveals,
        }
    )

    resolution_markers = normalize_strings(anchor.get("resolution_markers")) or default_resolution_markers()
    resolution_hits = [marker for marker in resolution_markers if marker and marker.lower() in text.lower()]
    if not closure_allowed and resolution_hits:
        failures.append(
            {
                "code": "premature_resolution",
                "severity": "P1",
                "message": "chapter appears to resolve a core conflict before the active outline anchor allows closure.",
                "repair_action": "rewrite the chapter so the marker remains unresolved or only partially reframed",
            }
        )
    checks.append(
        {
            "name": "core_resolution_markers",
            "status": "fail" if resolution_hits and not closure_allowed else "pass",
            "hits": resolution_hits,
            "policy": resolution_markers,
        }
    )

    complete_reveal = complete_core_reveal_detected(text)
    if complete_reveal and not closure_allowed and allowed_reveal_level != "full":
        failures.append(
            {
                "code": "core_secret_complete_reveal",
                "severity": "P1",
                "message": "non-finale chapter appears to fully reveal a core secret or final answer.",
                "repair_action": "downgrade the reveal to hint/partial evidence and preserve the final answer",
            }
        )
    checks.append(
        {
            "name": "complete_core_secret_reveal",
            "status": "fail" if complete_reveal and not closure_allowed and allowed_reveal_level != "full" else "pass",
            "detected": complete_reveal,
            "allowed_reveal_level": allowed_reveal_level,
        }
    )

    quota = detect_quota_usage(text)
    active_quota = [key for key, value in quota.items() if value]
    max_quota = int(config.data.get("pacing", {}).get("max_major_quota_triggers_per_chapter") or 1)
    if len(active_quota) > max_quota:
        failures.append(
            {
                "code": "plot_quota_overflow",
                "severity": "P1",
                "message": f"A/B/C plot acceleration quota overflow: {len(active_quota)} > {max_quota}.",
                "repair_action": "keep only one major acceleration lane and defer the others",
            }
        )
    checks.append(
        {
            "name": "abc_plot_quota",
            "status": "fail" if len(active_quota) > max_quota else "pass",
            "quota": quota,
            "active": active_quota,
            "limit": max_quota,
        }
    )

    info_release = mainline_info_release(text)
    release_warning_threshold = int(config.data.get("gates", {}).get("mainline_info_release_warning_hits") or 8)
    if not closure_allowed and info_release["hits"] >= release_warning_threshold:
        warnings.append(
            f"mainline information release is high: {info_release['hits']} reveal markers; preserve enough uncertainty for later chapters."
        )
    checks.append(
        {
            "name": "mainline_information_release",
            "status": "warn" if not closure_allowed and info_release["hits"] >= release_warning_threshold else "pass",
            **info_release,
            "warning_threshold": release_warning_threshold,
        }
    )

    tail_ok = has_tail_suspense(text)
    requires_tail = bool(anchor.get("requires_tail_suspense"))
    if requires_tail and not closure_allowed and not tail_ok:
        failures.append(
            {
                "code": "missing_tail_suspense",
                "severity": "P1",
                "message": "active outline anchor requires tail suspense for this chapter.",
                "repair_action": "add a concrete unresolved pressure, clue, threat, or changed problem in the final scene",
            }
        )
    elif not closure_allowed and not tail_ok:
        warnings.append("tail suspense signal is weak for the active outline anchor.")
    checks.append(
        {
            "name": "tail_suspense",
            "status": "fail" if requires_tail and not closure_allowed and not tail_ok else ("warn" if not closure_allowed and not tail_ok else "pass"),
            "requires_tail_suspense": requires_tail,
            "detected": tail_ok,
            "must_preserve": normalize_strings(anchor.get("must_preserve_suspense")),
        }
    )

    payload = {
        "chapter_number": chapter_number,
        "source": "20_outline/outline_anchors.json",
        "anchor": anchor,
        "closure_allowed": closure_allowed,
        "allowed_reveal_level": allowed_reveal_level,
        "checks": checks,
        "failures": failures,
        "warnings": warnings,
        "summary": {
            "forbidden_hits": len(forbidden_hits),
            "resolution_hits": len(resolution_hits),
            "complete_reveal": complete_reveal,
            "active_quota": active_quota,
            "mainline_info_hits": info_release["hits"],
            "tail_suspense_detected": tail_ok,
        },
    }
    return failures, warnings, payload


def check_anchor_resolution(
    config: ConfigDocument,
    root: Path,
    chapter_number: int,
    text: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    failures, warnings, _ = check_reverse_brake(config, root, chapter_number, text)
    return failures, warnings

    anchor = current_outline_anchor(root, chapter_number)
    if not anchor:
        return [], []
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    closure_allowed = bool(anchor.get("closure_allowed") or str(anchor.get("status") or "").lower() in {"closure", "closing", "finale"})

    forbidden_reveals = normalize_strings(anchor.get("forbidden_reveals"))
    forbidden_reveals.extend(normalize_strings(config.data.get("gates", {}).get("forbidden_reveals")))
    for item in dedupe_strings(forbidden_reveals):
        if item and item in text:
            failures.append(
                {
                    "code": "anchor_forbidden_reveal",
                    "severity": "P1",
                    "message": f"outline anchor forbids revealing `{item}` before the planned closure.",
                }
            )

    resolution_markers = normalize_strings(anchor.get("resolution_markers")) or [
        "core conflict resolved",
        "final truth",
        "ultimate secret",
        "everything is solved",
        "核心矛盾解决",
        "最终真相",
        "终极秘密",
        "一切都解决",
    ]
    if not closure_allowed and any(marker and marker.lower() in text.lower() for marker in resolution_markers):
        failures.append(
            {
                "code": "premature_resolution",
                "severity": "P1",
                "message": "chapter appears to resolve a core conflict before the active outline anchor allows closure.",
            }
        )

    if anchor.get("requires_tail_suspense") is True and not closure_allowed and not has_tail_suspense(text):
        failures.append(
            {
                "code": "missing_tail_suspense",
                "severity": "P1",
                "message": "active outline anchor requires tail suspense for this chapter.",
            }
        )
    elif not closure_allowed and not has_tail_suspense(text):
        warnings.append("tail suspense signal is weak for the active outline anchor.")

    return failures, warnings


def current_outline_anchor(root: Path, chapter_number: int) -> dict[str, Any]:
    payload = load_json(root / "20_outline" / "outline_anchors.json", default=[])
    anchors = normalize_records(payload)
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
    return selected


def normalize_reverse_brake_anchor(config: ConfigDocument, chapter_number: int, anchor: dict[str, Any]) -> dict[str, Any]:
    anchor = anchor if isinstance(anchor, dict) else {}
    status = str(anchor.get("status") or "synthetic").lower()
    closure_allowed = bool(anchor.get("closure_allowed") or status in {"closure", "closing", "finale", "final", "resolved"})
    allowed_reveal_level = str(anchor.get("allowed_reveal_level") or ("full" if closure_allowed else "hint")).lower()
    if allowed_reveal_level not in {"none", "hint", "partial", "full"}:
        allowed_reveal_level = "hint"
    forbidden_reveals = dedupe_strings(
        normalize_strings(anchor.get("forbidden_reveals"))
        + normalize_strings(config.data.get("gates", {}).get("forbidden_reveals"))
    )
    resolution_markers = normalize_strings(anchor.get("resolution_markers")) or default_resolution_markers()
    preserve = normalize_strings(anchor.get("must_preserve_suspense")) or [
        "core longform mystery",
        "main volume conflict",
    ]
    return {
        **anchor,
        "chapter_number": int(anchor.get("chapter_number") or anchor.get("chapter") or chapter_number),
        "status": anchor.get("status") or "synthetic",
        "duty": anchor.get("duty") or "maintain longform promise and avoid premature resolution",
        "forbidden_reveals": forbidden_reveals,
        "resolution_markers": resolution_markers,
        "requires_tail_suspense": bool(anchor.get("requires_tail_suspense")),
        "allowed_reveal_level": allowed_reveal_level,
        "must_preserve_suspense": preserve,
        "closure_allowed": closure_allowed,
    }


def default_resolution_markers() -> list[str]:
    return [
        "core conflict resolved",
        "final truth",
        "ultimate secret",
        "everything is solved",
        "core secret",
        "complete truth",
        "最终真相",
        "终极秘密",
        "核心秘密",
        "一切都解决",
    ]


def complete_core_reveal_detected(text: str) -> bool:
    lower = text.lower()
    direct_markers = (
        "everything is solved",
        "core secret is revealed",
        "reveals the core secret",
        "revealed the core secret",
        "真相大白",
        "全部揭开",
        "全部揭露",
        "一切都解决",
    )
    final_markers = ("final", "ultimate", "core", "complete", "全部", "最终", "终极", "核心")
    reveal_markers = ("truth", "secret", "answer", "solved", "resolved", "revealed", "真相", "秘密", "答案", "解决", "揭开", "揭露")
    negation_markers = ("not", "unknown", "unresolved", "remain", "未", "没有", "并非", "不是", "尚未", "仍未", "只", "一角")
    clauses = [item.strip() for item in re.split(r"[。！？!?；;\n]+", lower) if item.strip()]
    for clause in clauses:
        if any(marker in clause for marker in negation_markers):
            continue
        if any(marker in clause for marker in direct_markers):
            return True
        final_positions = [clause.find(marker) for marker in final_markers if marker in clause]
        reveal_positions = [clause.find(marker) for marker in reveal_markers if marker in clause]
        if final_positions and reveal_positions and min(
            abs(final_pos - reveal_pos)
            for final_pos in final_positions
            for reveal_pos in reveal_positions
        ) <= 80:
            return True
    return False


def mainline_info_release(text: str) -> dict[str, Any]:
    lower = text.lower()
    markers = (
        "truth",
        "secret",
        "reveal",
        "revealed",
        "answer",
        "core",
        "ultimate",
        "final",
        "solved",
        "resolved",
        "真相",
        "秘密",
        "揭露",
        "揭开",
        "答案",
        "核心",
        "最终",
        "终极",
        "解决",
    )
    hits_by_marker = {marker: lower.count(marker) for marker in markers if lower.count(marker)}
    return {
        "hits": sum(hits_by_marker.values()),
        "markers": hits_by_marker,
    }


def has_tail_suspense(text: str) -> bool:
    tail = text[-500:].lower()
    markers = (
        "?",
        "？",
        "but then",
        "before he could",
        "unknown",
        "secret",
        "clue",
        "然而",
        "忽然",
        "未知",
        "秘密",
        "线索",
        "没有结束",
        "只剩",
        "来不及",
        "截止",
        "期限",
        "封库",
        "必须在",
        "赶在",
    )
    return any(marker in tail for marker in markers)


def check_style_and_humanizer(config: ConfigDocument, text: str) -> tuple[list[dict[str, Any]], list[str]]:
    metrics = style_fingerprint(text)
    humanize = humanizer_metrics(text)
    root = resolve_project_root(config)
    characters = load_json(root / "10_bible" / "characters.json", default=[])
    character_names = [
        str(item.get("name"))
        for item in characters
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ] if isinstance(characters, list) else []
    expression_diagnostics = character_expression_diagnostics(text, character_names=character_names)
    humanizer_issues, humanizer_warnings = detect_humanizer_issues(text)
    failures: list[dict[str, Any]] = []
    warnings: list[str] = list(humanizer_warnings)

    failures.extend(humanizer_issues)
    if humanize["meta_pollution_hits"]:
        failures.append(
            {
                "code": "humanizer_meta_pollution",
                "severity": "P0",
                "message": "humanizer detected prompt/meta residue in manuscript prose",
            }
        )
    if humanize["duplicate_paragraph_ratio"] >= 0.45 and metrics["paragraph_count"] >= 3:
        failures.append(
            {
                "code": "duplicate_paragraphs",
                "severity": "P1",
                "message": f"duplicate paragraph ratio too high: {humanize['duplicate_paragraph_ratio']:.2f}",
            }
        )
    if humanize["summary_heavy_ratio"] >= 0.35:
        warnings.append(f"summary-heavy prose ratio is high: {humanize['summary_heavy_ratio']:.2f}")
    if humanize["template_repetition_score"] >= 0.35:
        warnings.append(f"repeated sentence/template score is high: {humanize['template_repetition_score']:.2f}")
    expression_profile = load_json(root / "10_bible" / "character_expression.json", default={})
    narrative_profile = (
        expression_profile.get("narrative_expression_profile")
        if isinstance(expression_profile, dict)
        and isinstance(expression_profile.get("narrative_expression_profile"), dict)
        else {}
    )
    if metrics["dialogue_char_ratio"] < 0.01 and narrative_profile.get("dialogue_mode") != "sparse":
        warnings.append("dialogue ratio is very low; verify scene dramatization.")
    for risk in expression_diagnostics["risks"]:
        warnings.append(f"{risk['code']}: {risk['message']}")
    if metrics["punctuation_density"] > 0.18:
        warnings.append("punctuation density is high; verify rhythm and readability.")
    if metrics["paragraph_variance"] < 8 and metrics["paragraph_count"] >= 5:
        warnings.append("paragraph lengths are unusually uniform; possible template prose.")
    if perspective_drift_detected(text):
        warnings.append("possible perspective drift detected.")
    style_drift_failures, style_drift_warnings = check_active_style_drift(config, metrics, text)
    failures.extend(style_drift_failures)
    warnings.extend(style_drift_warnings)

    return failures, warnings


def check_fanfiction_source_reproduction(
    config: ConfigDocument,
    root: Path,
    text: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if str(config.data.get("creation", {}).get("mode") or "original") != "fanfiction":
        return [], []
    canon = load_json(root / "10_bible" / "fanfiction" / "source_canon.json", default={})
    if not isinstance(canon, dict):
        return [], ["fanfiction source canon is missing; source-prose reproduction check could not run"]
    protected_terms = fanfiction_protected_terms(canon)
    candidate_parts = [
        part.strip()
        for part in re.split(r"\n\s*\n+|(?<=[。！？!?])", text)
        if len(normalize_fanfiction_similarity(part, protected_terms)) >= 36
    ]
    if len(normalize_fanfiction_similarity(text, protected_terms)) >= 36:
        candidate_parts.insert(0, text)
    for source in canon.get("sources") or []:
        if not isinstance(source, dict):
            continue
        for source_file in source.get("source_files") or []:
            path = root / str(source_file)
            if not path.is_file():
                continue
            source_text = normalize_fanfiction_similarity(safe_read_text(path), protected_terms)
            for part in candidate_parts:
                candidate = normalize_fanfiction_similarity(part, protected_terms)
                overlap = ngram_overlap_ratio(candidate, source_text, size=10)
                if candidate in source_text or overlap >= 0.62:
                    return [
                        {
                            "code": "fanfiction_source_prose_reproduction",
                            "severity": "P1",
                            "message": (
                                "continuous prose is too similar to a declared source after excluding names, "
                                "abilities, and world terminology"
                            ),
                            "source_path": str(source_file),
                            "overlap_ratio": round(overlap, 4),
                        }
                    ], []
    return [], []


def fanfiction_protected_terms(canon: dict[str, Any]) -> tuple[str, ...]:
    terms: set[str] = set()
    for source in canon.get("sources") or []:
        if not isinstance(source, dict):
            continue
        for field in ("characters", "abilities", "terminology"):
            for item in source.get(field) or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip()
                if name:
                    terms.add(name)
    return tuple(sorted(terms, key=len, reverse=True))


def normalize_fanfiction_similarity(text: str, protected_terms: tuple[str, ...]) -> str:
    normalized = str(text).lower()
    for term in protected_terms:
        normalized = normalized.replace(term.lower(), "")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", normalized, flags=re.UNICODE)


def ngram_overlap_ratio(candidate: str, source: str, *, size: int) -> float:
    if len(candidate) < size or len(source) < size:
        return 0.0
    candidate_grams = {candidate[index:index + size] for index in range(len(candidate) - size + 1)}
    source_grams = {source[index:index + size] for index in range(len(source) - size + 1)}
    return len(candidate_grams & source_grams) / max(1, len(candidate_grams))


def style_fingerprint(text: str) -> dict[str, Any]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    sentences = [part.strip() for part in re.split(r"[。！？.!?]+", text) if part.strip()]
    sentence_lengths = [content_character_count(sentence) for sentence in sentences]
    paragraph_lengths = [content_character_count(paragraph) for paragraph in paragraphs]
    avg_sentence = sum(sentence_lengths) / max(1, len(sentence_lengths))
    avg_paragraph = sum(paragraph_lengths) / max(1, len(paragraph_lengths))
    variance = sum(abs(length - avg_paragraph) for length in paragraph_lengths) / max(1, len(paragraph_lengths))
    dialogue_marks = text.count('"') + text.count("'") + text.count("“") + text.count("”") + text.count("「") + text.count("」")
    punctuation = sum(text.count(mark) for mark in "，。！？；：,.!?;:")
    total_chars = max(1, len(re.sub(r"\s+", "", text)))
    repeated_phrases = repeated_ngram_count(text)
    expression = character_expression_diagnostics(text)
    return {
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentences),
        "avg_sentence_chars": round(avg_sentence, 2),
        "avg_paragraph_chars": round(avg_paragraph, 2),
        "paragraph_variance": round(variance, 2),
        "dialogue_ratio": expression["dialogue_char_ratio"],
        "dialogue_char_ratio": expression["dialogue_char_ratio"],
        "dialogue_mark_density": round(dialogue_marks / total_chars, 4),
        "punctuation_density": round(punctuation / total_chars, 4),
        "repeated_phrase_count": repeated_phrases,
    }


def check_active_style_drift(config: ConfigDocument, metrics: dict[str, Any], text: str) -> tuple[list[dict[str, Any]], list[str]]:
    root = resolve_project_root(config)
    active = load_active_style_profile(root)
    baseline = active.get("fingerprint") if isinstance(active.get("fingerprint"), dict) else {}
    if not baseline:
        return [], []

    comparisons: list[str] = []
    strong: list[str] = []
    moderate: list[str] = []
    compare_numeric_metric(
        "avg_sentence_chars",
        baseline,
        metrics,
        comparisons,
        strong,
        moderate,
        strong_ratio=1.25,
        moderate_ratio=0.7,
        strong_abs=35,
        moderate_abs=18,
    )
    compare_numeric_metric(
        "avg_paragraph_chars",
        baseline,
        metrics,
        comparisons,
        strong,
        moderate,
        strong_ratio=1.1,
        moderate_ratio=0.65,
        strong_abs=180,
        moderate_abs=90,
    )
    compare_numeric_metric(
        "dialogue_ratio",
        baseline,
        metrics,
        comparisons,
        strong,
        moderate,
        strong_ratio=2.5,
        moderate_ratio=1.5,
        strong_abs=0.018,
        moderate_abs=0.01,
    )
    compare_numeric_metric(
        "punctuation_density",
        baseline,
        metrics,
        comparisons,
        strong,
        moderate,
        strong_ratio=1.25,
        moderate_ratio=0.75,
        strong_abs=0.08,
        moderate_abs=0.045,
    )
    baseline_pov = baseline_pov_label(baseline)
    current_pov = current_pov_label(text)
    if baseline_pov != "unknown" and current_pov != "unknown" and baseline_pov != current_pov:
        comparisons.append(f"pov {baseline_pov} -> {current_pov}")
        strong.append("pov")

    if not strong and not moderate:
        return [], []

    severity = "P1" if len(strong) >= 2 or "avg_sentence_chars" in strong else "P2"
    message = "style drift from active sample profile: " + "; ".join(comparisons[:6])
    failure = {
        "code": "style_drift",
        "severity": severity,
        "message": message,
        "repair_action": "align sentence/paragraph rhythm, dialogue density, and POV with current_style_profile.json",
    }
    warnings = [f"active style profile source: {active.get('source', '')}"]
    if severity == "P2":
        warnings.append(message)
    return [failure], warnings


def load_active_style_profile(root: Path) -> dict[str, Any]:
    path = root / "10_bible" / "style_profiles" / "current_style_profile.json"
    payload = load_json(path, default={})
    if not isinstance(payload, dict):
        return {}
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    fingerprint = profile.get("fingerprint") if isinstance(profile.get("fingerprint"), dict) else {}
    if not fingerprint:
        return {}
    return {
        "source": relative_path(root, path),
        "profile_type": payload.get("profile_type", ""),
        "fingerprint": fingerprint,
        "sample_sources": payload.get("sample_sources", []),
    }


def compare_numeric_metric(
    key: str,
    baseline: dict[str, Any],
    current: dict[str, Any],
    comparisons: list[str],
    strong: list[str],
    moderate: list[str],
    *,
    strong_ratio: float,
    moderate_ratio: float,
    strong_abs: float,
    moderate_abs: float,
) -> None:
    baseline_value = float_or_none(baseline.get(key))
    current_value = float_or_none(current.get(key))
    if baseline_value is None or current_value is None:
        return
    absolute_delta = abs(current_value - baseline_value)
    ratio_delta = absolute_delta / max(abs(baseline_value), 0.001)
    if absolute_delta >= strong_abs and ratio_delta >= strong_ratio:
        strong.append(key)
        comparisons.append(f"{key} {baseline_value:.3g} -> {current_value:.3g}")
    elif absolute_delta >= moderate_abs and ratio_delta >= moderate_ratio:
        moderate.append(key)
        comparisons.append(f"{key} {baseline_value:.3g} -> {current_value:.3g}")


def float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def baseline_pov_label(baseline: dict[str, Any]) -> str:
    pov = baseline.get("pov") if isinstance(baseline.get("pov"), dict) else {}
    return str(pov.get("dominant") or "unknown")


def current_pov_label(text: str) -> str:
    lower = text.lower()
    counts = {
        "first_person": len(re.findall(r"\b(i|me|my|mine|we|our|us)\b|\u6211|\u6211\u4eec", lower)),
        "second_person": len(re.findall(r"\b(you|your|yours)\b|\u4f60|\u4f60\u4eec", lower)),
        "third_person": len(re.findall(r"\b(he|she|they|him|her|them|his|their)\b|\u4ed6|\u5979|\u4ed6\u4eec|\u5979\u4eec", lower)),
    }
    if not any(counts.values()):
        return "unknown"
    return max(counts, key=lambda key: counts[key])


def humanizer_metrics(text: str) -> dict[str, Any]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    unique = set(paragraphs)
    duplicate_ratio = 0.0 if not paragraphs else 1 - (len(unique) / len(paragraphs))
    summary_markers = ("总之", "这一章", "本章", "接下来", "可以看出", "summary", "outline")
    summary_hits = sum(1 for paragraph in paragraphs if any(marker in paragraph.lower() for marker in summary_markers))
    sentences = [part.strip() for part in re.split(r"[。！？.!?]+", text) if part.strip()]
    repeated_sentences = len(sentences) - len(set(sentences))
    meta_patterns = ("TODO", "写作说明", "作者按", "角色定位", "as an ai", "language model")
    return {
        "duplicate_paragraph_ratio": round(duplicate_ratio, 4),
        "summary_heavy_ratio": round(summary_hits / max(1, len(paragraphs)), 4),
        "template_repetition_score": round(repeated_sentences / max(1, len(sentences)), 4),
        "meta_pollution_hits": [pattern for pattern in meta_patterns if pattern.lower() in text.lower()],
    }


def perspective_drift_detected(text: str) -> bool:
    first_person = len(re.findall(r"\b(I|me|my|we|our)\b|我|我们", text, re.IGNORECASE))
    third_person = len(re.findall(r"\b(he|she|they|him|her|them)\b|他|她|他们|她们", text, re.IGNORECASE))
    return first_person >= 5 and third_person >= 5


def repeated_ngram_count(text: str) -> int:
    compact = re.sub(r"\s+", "", text)
    counts: dict[str, int] = {}
    for index in range(0, max(0, len(compact) - 8), 4):
        gram = compact[index : index + 8]
        if len(gram) == 8:
            counts[gram] = counts.get(gram, 0) + 1
    return sum(1 for value in counts.values() if value >= 3)


def write_artifact_reports(
    artifact_dir: Path,
    *,
    chapter_number: int,
    draft_path: Path,
    failures: list[dict[str, Any]],
    warnings: list[str],
    pacing: PacingReviewResult,
    reverse_brake: dict[str, Any],
) -> None:
    consistency_issues = [failure for failure in failures if failure["code"] == "graph_consistency"]
    text = safe_read_text(draft_path)
    style = style_fingerprint(text)
    active_style = load_active_style_profile(artifact_dir.parents[2]) if len(artifact_dir.parents) >= 3 else {}
    style_issues = [failure for failure in failures if failure.get("code") == "style_drift"]
    humanize = humanizer_metrics(text)
    characters = load_json(artifact_dir.parents[2] / "10_bible" / "characters.json", default={}) if len(artifact_dir.parents) >= 3 else []
    character_names = [
        str(item.get("name"))
        for item in characters
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ] if isinstance(characters, list) else []
    expression_diagnostics = character_expression_diagnostics(text, character_names=character_names)
    humanize["character_expression"] = expression_diagnostics
    humanizer_issues, humanizer_warnings = detect_humanizer_issues(text)
    humanize["issues"] = humanizer_issues
    humanize["warnings"] = humanizer_warnings
    write_reverse_brake_report(artifact_dir / "reverse_brake_report.md", reverse_brake)
    atomic_write_text(
        artifact_dir / "consistency_report.md",
        markdown_report(
            f"Consistency Report ch{chapter_number:03d}",
            [failure["message"] for failure in consistency_issues],
            warnings,
        ),
    )
    atomic_write_text(
        artifact_dir / "style_review.md",
        markdown_report(
            f"Style Review ch{chapter_number:03d}",
            [],
            ["Deterministic style review placeholder; LLM editorial review will be added later."],
        ),
    )
    atomic_write_text(
        artifact_dir / "quality_report.md",
        markdown_report(
            f"Quality Report ch{chapter_number:03d}",
            [failure["message"] for failure in failures],
            [f"Source: {draft_path}", *warnings],
        ),
    )
    atomic_write_text(
        artifact_dir / "publish_ready.md",
        "可发布：否\n\n当前门禁未确认发布条件，正式定稿发布需要通过 finalize 流程。\n",
    )


    atomic_write_text(
        artifact_dir / "style_review.md",
        "\n".join(
            [
                f"# Style Review ch{chapter_number:03d}",
                "",
                "## Fingerprint",
                "",
                f"```json\n{json.dumps(style, ensure_ascii=False, indent=2)}\n```",
                "",
                "## Active Style Baseline",
                "",
                f"```json\n{json.dumps(active_style, ensure_ascii=False, indent=2)}\n```",
                "",
                "## Drift Issues",
                "",
                *([f"- [{item.get('severity')}] {item.get('message')}" for item in style_issues] or ["- None"]),
                "",
                "## Warnings",
                "",
                *style_warning_subset(warnings),
                "",
            ]
        ),
    )
    atomic_write_text(
        artifact_dir / "humanize_report.md",
        "\n".join(
            [
                f"# Humanize Report ch{chapter_number:03d}",
                "",
                f"```json\n{json.dumps(humanize, ensure_ascii=False, indent=2)}\n```",
                "",
                "## Deterministic Checks",
                "",
                "- meta pollution",
                "- repeated templates",
                "- summary-heavy prose",
                "- duplicate paragraph ratio",
                f"- dialogue sameness risk: {expression_diagnostics['swapability_risk']}",
                f"- dialogue attribution coverage: {expression_diagnostics['attribution_coverage']}",
                f"- dialogue exposition ratio: {expression_diagnostics['dialogue_exposition_ratio']}",
                f"- embodied presence density: {expression_diagnostics['embodiment_term_density']}",
                f"- interiority density: {expression_diagnostics['interiority_term_density']}",
                "",
            ]
        ),
    )
    atomic_write_text(
        artifact_dir / "copyedit_report.md",
        markdown_report(
            f"Copyedit Report ch{chapter_number:03d}",
            [],
            copyedit_warnings(text),
        ),
    )
    atomic_write_text(
        artifact_dir / "memory_update.md",
        "\n".join(
            [
                f"# Memory Update ch{chapter_number:03d}",
                "",
                f"- Source: `{draft_path}`",
                f"- Pacing tier: {pacing.tier}",
                f"- Word count: {content_character_count(text)}",
                "",
                "This is a gate artifact only. Canonical memory updates occur after chapter finalize.",
                "",
            ]
        ),
    )
    atomic_write_text(
        artifact_dir / "publish_ready.md",
        "\n".join(
            [
                f"# Publish Readiness ch{chapter_number:03d}",
                "",
                f"- Ready: {'yes' if not failures else 'no'}",
                f"- Blocking failures: {len(failures)}",
                f"- Warnings: {len(warnings)}",
                "",
            ]
        ),
    )


def write_reverse_brake_report(path: Path, payload: dict[str, Any]) -> None:
    checks = payload.get("checks") if isinstance(payload.get("checks"), list) else []
    failures = payload.get("failures") if isinstance(payload.get("failures"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    anchor = payload.get("anchor") if isinstance(payload.get("anchor"), dict) else {}
    lines = [
        f"# Reverse Brake Report ch{int(payload.get('chapter_number') or 0):03d}",
        "",
        f"- Closure allowed: {payload.get('closure_allowed', False)}",
        f"- Allowed reveal level: {payload.get('allowed_reveal_level', 'hint')}",
        f"- Requires tail suspense: {anchor.get('requires_tail_suspense', False)}",
        f"- Forbidden reveals: {', '.join(normalize_strings(anchor.get('forbidden_reveals'))) or 'none'}",
        f"- Do not resolve: {', '.join(normalize_strings(anchor.get('resolution_markers'))) or 'none'}",
        f"- Must preserve suspense: {', '.join(normalize_strings(anchor.get('must_preserve_suspense'))) or 'none'}",
        "",
        "## Checks",
        "",
    ]
    for check in checks:
        if not isinstance(check, dict):
            continue
        lines.append(f"- {check.get('name')}: {check.get('status')}")
        detail = {key: value for key, value in check.items() if key not in {"name", "status"}}
        if detail:
            lines.append(f"  Detail: {json.dumps(detail, ensure_ascii=False)}")
    if not checks:
        lines.append("- None")
    lines.extend(["", "## Failures", ""])
    lines.extend([f"- [{item.get('severity')}] {item.get('code')}: {item.get('message')}" for item in failures if isinstance(item, dict)] or ["- None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in warnings] or ["- None"])
    lines.extend(
        [
            "",
            "## Raw Payload",
            "",
            f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```",
            "",
        ]
    )
    atomic_write_text(path, "\n".join(lines))


def style_warning_subset(warnings: list[str]) -> list[str]:
    keywords = ("dialogue", "punctuation", "paragraph", "perspective", "template")
    selected = [f"- {warning}" for warning in warnings if any(keyword in warning.lower() for keyword in keywords)]
    return selected or ["- None"]


def copyedit_warnings(text: str) -> list[str]:
    warnings: list[str] = []
    if re.search(r"\s{3,}", text):
        warnings.append("multiple consecutive spaces detected")
    if text.count("...") + text.count("……") > 8:
        warnings.append("ellipsis usage is high")
    if re.search(r"([!?！？。])\1{2,}", text):
        warnings.append("repeated terminal punctuation detected")
    if not text.strip().startswith("#"):
        warnings.append("chapter heading is missing or not markdown-style")
    return warnings or ["No deterministic copyedit warnings."]


def markdown_report(title: str, issues: list[str], warnings: list[str]) -> str:
    lines = [f"# {title}", "", "## Issues", ""]
    lines.extend([f"- {issue}" for issue in issues] or ["- None"])
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {warning}" for warning in warnings] or ["- None"])
    lines.append("")
    return "\n".join(lines)


def semantic_pacing_review_status(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> dict[str, Any]:
    """Return whether a required pacing review was applied to the current draft bytes."""

    root = resolve_project_root(config)
    quality = config.data.get("quality", {}) if isinstance(config.data.get("quality"), dict) else {}
    pacing_config = quality.get("semantic_pacing") if isinstance(quality.get("semantic_pacing"), dict) else {}
    mode = str(pacing_config.get("review_mode") or "off").strip().lower()
    card = load_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json", default={})
    required = mode == "required" or (
        mode == "risk_based"
        and (
            str((quality.get("profile") or {}).get("strictness") or "balanced") == "strict"
            or bool(card.get("requires_semantic_pacing_review"))
        )
    )
    if not required:
        return {"required": False, "complete": True, "passed": True, "reason": "not_required", "review_mode": mode}
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    result = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "semantic_pacing_result.json"
    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    draft_hash = sha256_text(safe_read_text(draft)) if draft.is_file() else ""
    payload = load_json(result, default={})
    gate = load_json(gate_path, default={})
    applied = gate.get("semantic_pacing") if isinstance(gate, dict) else None
    complete = bool(
        draft_hash
        and isinstance(payload, dict)
        and payload.get("schema") == EVIDENCE_REVIEW_SCHEMA
        and isinstance(applied, dict)
        and str(applied.get("source_path") or "") == relative_path(root, draft)
        and str(applied.get("source_sha256") or "") == draft_hash
        and str(applied.get("result_sha256") or "") == sha256_text(safe_read_text(result))
    )
    passed = complete and str(applied.get("verdict") or "") == "pass"
    return {
        "required": True,
        "complete": complete,
        "passed": passed,
        "reason": "applied" if complete else "semantic_pacing_missing_invalid_or_stale",
        "review_mode": mode,
        "result_file": relative_path(root, result),
        "gate_result": relative_path(root, gate_path),
    }


def semantic_pacing_task_is_current(root: Path, chapter_number: int, task: dict[str, Any]) -> bool:
    """Return whether a pacing task packet was compiled for the current draft bytes."""

    if str(task.get("task_type") or "") != "pacing_review":
        return False
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    task_json = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "semantic_pacing_task.json"
    if not draft.is_file() or not task_json.is_file():
        return False
    payload = load_json(task_json, default={})
    return bool(
        isinstance(payload, dict)
        and int(payload.get("schema_version") or 0) == 2
        and str(payload.get("source_path") or "") == relative_path(root, draft)
        and str(payload.get("source_sha256") or "") == sha256_text(safe_read_text(draft))
    )


def semantic_review_source_for_task(
    root: Path,
    task: dict[str, Any] | None,
    chapter_number: int,
) -> Path | None:
    """Resolve the exact draft/final bytes declared by the current review task."""

    if not isinstance(task, dict):
        return None
    candidates: list[Path] = []
    expected_name = chapter_filename(chapter_number)
    for value in manifest_input_paths(task):
        relative = str(value or "").replace("\\", "/")
        if not relative.startswith(("40_manuscript/draft/", "40_manuscript/final/")):
            continue
        path = resolve_under_root(root, relative)
        if path.name == expected_name and parse_canonical_chapter_number(path) == chapter_number and path.is_file():
            candidates.append(path)
    return candidates[0] if len(candidates) == 1 else None


def semantic_review_gate_items(
    config: ConfigDocument,
    *,
    chapter_number: int,
    source_path: Path,
    deterministic_failures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    root = resolve_project_root(config)
    artifact_dir = gate_artifact_dir(root, chapter_number)
    card = load_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json", default={})
    quality = config.data.get("quality") if isinstance(config.data.get("quality"), dict) else {}
    milestones = {
        int(item)
        for item in quality.get("semantic_review_milestones", [])
        if isinstance(item, int) and not isinstance(item, bool) and item > 0
    }
    explicit = (
        (isinstance(card, dict) and bool(card.get("requires_semantic_review")))
        or chapter_number in milestones
    )
    deterministic_risk = any(
        str(item.get("severity") or "").upper() in {"P0", "P1"}
        for item in deterministic_failures
        if isinstance(item, dict)
    )
    required = explicit or deterministic_risk
    application_file = artifact_dir / "semantic_review_application.json"
    application = load_json(application_file, default={})
    source_hash = sha256_text(safe_read_text(source_path))
    current = (
        isinstance(application, dict)
        and application.get("schema") == "semantic_review_application_v1"
        and str(application.get("source_hash") or "") == source_hash
        and isinstance(application.get("payload"), dict)
    )
    if not required and not current:
        return [], [], {"required": False, "status": "not_required"}
    if not current:
        task = semantic_review_task(config, chapter_number=chapter_number, source="final" if "final" in source_path.parts else "draft")
        return [], [], {
            "required": True,
            "status": "awaiting_agent",
            "task": relative_path(root, Path(task.manifest_file)),
            "next_command": f"longform-engine agent-task brief project.yaml --task-id semantic_review:ch{chapter_number:03d}:v4",
        }
    payload = application["payload"]
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    for item in payload.get("findings", []):
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity") or "").upper()
        code = str(item.get("code") or "finding")
        message = str(item.get("diagnosis") or code)
        if severity in {"P0", "P1"}:
            failures.append(
                {
                    "code": f"agent_semantic:{code}",
                    "severity": severity,
                    "message": message,
                    "evidence_ids": list(item.get("evidence_ids") or []),
                    "repair_action": item.get("repair_target"),
                }
            )
        elif severity == "P2":
            warnings.append(f"agent_semantic:{code}: {message}")
    return failures, warnings, {
        "required": required,
        "status": "applied",
        "result": str(application.get("result_file") or ""),
        "source_hash": source_hash,
        "verdict": payload.get("verdict"),
    }


def resolve_semantic_review_result_path(root: Path, artifact_dir: Path, file_path: str | Path) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
    resolved = path.expanduser().resolve()
    expected = (artifact_dir / "semantic_review_result.json").resolve()
    if resolved != expected:
        raise GateError("semantic review result must be 50_workbench/gate_artifacts/chNNN/semantic_review_result.json.")
    return resolved


def resolve_review_source(root: Path, chapter_number: int, source_path: str) -> Path | None:
    if not source_path:
        return None
    candidate = (root / source_path).resolve()
    allowed = {
        path.resolve()
        for lane in ("draft", "final")
        for path in [chapter_text_path(root, chapter_number, source=lane)]
        if path is not None
    }
    return candidate if candidate in allowed and candidate.exists() else None


def is_canonical_reference(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith(("10_bible/", "20_outline/", "30_state/", "60_rag/memory/"))


def semantic_review_known_entities(root: Path) -> set[str]:
    ids: set[str] = set()
    characters = load_json(root / "10_bible" / "characters.json", default={})
    for item in normalize_records(characters):
        if isinstance(item, dict):
            for key in ("id", "character_id", "name"):
                value = str(item.get(key) or "").strip()
                if value:
                    ids.add(value)
    graph = load_json(root / "30_state" / "story_graph.json", default={})
    if isinstance(graph, dict):
        for item in normalize_records(graph.get("nodes")):
            if isinstance(item, dict):
                for key in ("id", "entity_id", "name"):
                    value = str(item.get(key) or "").strip()
                    if value:
                        ids.add(value)
    canon = load_json(root / "10_bible" / "fanfiction" / "source_canon.json", default={})
    if isinstance(canon, dict):
        for source in canon.get("sources") or []:
            if not isinstance(source, dict):
                continue
            for field in ("characters", "abilities", "terminology", "world_rules"):
                for item in source.get(field) or []:
                    if not isinstance(item, dict):
                        continue
                    for key in ("id", "name"):
                        value = str(item.get(key) or "").strip()
                        if value:
                            ids.add(value)
    return ids


def validate_semantic_review_finding(
    finding: Any,
    *,
    index: int,
    source_text: str,
    allowed_refs: set[str],
    known_entities: set[str],
    errors: list[str],
) -> None:
    if not isinstance(finding, dict):
        errors.append(f"findings[{index}] must be an object.")
        return
    expected = {
        "code",
        "category",
        "severity",
        "message",
        "evidence_span",
        "canonical_refs",
        "entity_ids",
        "recommendation",
    }
    if set(finding) != expected:
        errors.append(f"findings[{index}] keys must be exactly {sorted(expected)}.")
    if not str(finding.get("code") or "").strip():
        errors.append(f"findings[{index}].code is required.")
    if str(finding.get("category") or "") not in {
        "motivation",
        "location",
        "ability",
        "relationship",
        "foreshadowing",
        "causality",
        "canon_fidelity",
        "voice",
        "divergence",
        "original_contribution",
    }:
        errors.append(f"findings[{index}].category is invalid.")
    if str(finding.get("severity") or "").upper() not in {"P0", "P1", "P2"}:
        errors.append(f"findings[{index}].severity must be P0, P1, or P2.")
    if not str(finding.get("message") or "").strip() or not str(finding.get("recommendation") or "").strip():
        errors.append(f"findings[{index}] requires message and recommendation.")
    span = finding.get("evidence_span")
    if not isinstance(span, dict) or set(span) != {"start", "end", "text"}:
        errors.append(f"findings[{index}].evidence_span must contain exactly start, end, text.")
    else:
        start = span.get("start")
        end = span.get("end")
        quoted = span.get("text")
        if not isinstance(start, int) or not isinstance(end, int) or not (0 <= start < end <= len(source_text)):
            errors.append(f"findings[{index}].evidence_span is outside the chapter.")
        elif quoted != source_text[start:end]:
            errors.append(f"findings[{index}].evidence_span.text does not match the chapter slice.")
    refs = finding.get("canonical_refs")
    if not isinstance(refs, list) or not refs:
        errors.append(f"findings[{index}].canonical_refs must be a non-empty list.")
    else:
        for ref in refs:
            normalized = str(ref).replace("\\", "/")
            if normalized not in allowed_refs:
                errors.append(f"findings[{index}] references undeclared canonical file: {normalized}.")
    entity_ids = finding.get("entity_ids")
    if not isinstance(entity_ids, list):
        errors.append(f"findings[{index}].entity_ids must be a list.")
    else:
        for entity_id in entity_ids:
            if str(entity_id) not in known_entities:
                errors.append(f"findings[{index}] references unknown entity_id: {entity_id}.")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def resolve_semantic_pacing_result_path(root: Path, artifact_dir: Path, file_path: str | Path) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
    resolved = path.expanduser().resolve()
    expected = (artifact_dir / "semantic_pacing_result.json").resolve()
    if resolved != expected:
        raise GateError("semantic pacing result must be 50_workbench/gate_artifacts/chNNN/semantic_pacing_result.json.")
    return resolved


def semantic_pacing_gate_items(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        severity = str(finding.get("severity") or "").upper()
        message = str(finding.get("diagnosis") or "").strip()
        code = str(finding.get("code") or "semantic_pacing").strip()
        if severity in {"P0", "P1"}:
            failures.append(
                {
                    "code": f"semantic_pacing:{code}",
                    "severity": severity,
                    "message": message or code,
                    "evidence": list(finding.get("evidence_ids") or []),
                    "repair_action": finding.get("repair_target", "repair pacing and rerun semantic pacing review"),
                }
            )
        elif severity == "P2":
            warnings.append(f"semantic_pacing:{code}: {message or code}")
    if str(payload.get("verdict") or "").lower() == "repair" and not failures:
        failures.append(
            {
                "code": "semantic_pacing:fail_verdict",
                "severity": "P1",
                "message": "semantic pacing agent returned fail verdict without a blocking issue.",
                "repair_action": "rerun semantic pacing task or repair pacing before finalization",
            }
        )
    return failures, warnings


def append_semantic_pacing_report(path: Path, payload: dict[str, Any], result_path: str) -> None:
    existing = safe_read_text(path) if path.exists() else ""
    marker = "\n## Semantic Pacing Review\n"
    base = existing.split(marker, 1)[0].rstrip()
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    lines = [
        base,
        marker.strip(),
        "",
        f"- Result: `{result_path}`",
        f"- Verdict: {payload.get('verdict', '')}",
        "- Coverage: " + ", ".join(
            f"{key}={value.get('status', '') if isinstance(value, dict) else 'invalid'}"
            for key, value in sorted(coverage.items())
        ),
        "",
        "### Issues",
        "",
    ]
    for finding in findings:
        if isinstance(finding, dict):
            lines.append(f"- [{finding.get('severity')}] {finding.get('code')}: {finding.get('diagnosis')}")
    if not findings:
        lines.append("- None")
    lines.extend(["", "### Coverage Gaps", ""])
    gaps = [
        f"{key}: insufficient"
        for key, value in sorted(coverage.items())
        if isinstance(value, dict) and value.get("status") == "insufficient"
    ]
    lines.extend([f"- {item}" for item in gaps] or ["- None"])
    lines.extend(["", "Semantic pacing is advisory until applied by CLI.", ""])
    atomic_write_text(path, "\n".join(lines))


def trim_text(text: str, limit: int) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit].rstrip() + "\n\n...[truncated]"


def chapter_text_path(root: Path, chapter_number: int, *, source: str) -> Path | None:
    return existing_manuscript_chapter_path(
        root,
        chapter_number,
        lane="final" if source == "final" else "draft",
    )


def gate_artifact_dir(root: Path, chapter_number: int) -> Path:
    return root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}"


def infer_pacing_tier(text: str) -> str:
    markers = ("决战", "爆发", "杀", "秘密", "真相", "突破", "反杀", "危机")
    count = sum(text.count(marker) for marker in markers)
    if count >= 4:
        return "fast"
    if count <= 1:
        return "slow"
    return "medium"


def detect_quota_usage(text: str) -> dict[str, bool]:
    lower = text.lower()
    return {
        "A": any(marker in lower for marker in ("mainline", "core conflict", "old order", "countermove", "主线", "核心矛盾", "旧秩序", "反制", "涓荤嚎", "鏍稿績鐭涚浘")),
        "B": any(marker in lower for marker in ("relationship", "bond", "alliance", "betrayal", "breakup", "关系", "背叛", "结盟", "决裂", "鍏崇郴", "鑳屽彌", "缁撶洘", "鍐宠")),
        "C": any(marker in lower for marker in ("secret", "truth", "reveal", "revealed", "complete", "秘密", "真相", "揭露", "全部", "绉樺瘑", "鐪熺浉", "鎻湶", "鍏ㄩ儴")),
    }

    return {
        "A": any(marker in text for marker in ("主线", "核心矛盾", "旧秩序", "反制")),
        "B": any(marker in text for marker in ("关系", "背叛", "结盟", "决裂")),
        "C": any(marker in text for marker in ("秘密", "真相", "揭露", "全部")),
    }


def max_severity(failures: list[dict[str, Any]]) -> str:
    order = {"P0": 3, "P1": 2, "P2": 1}
    if not failures:
        return "PASS"
    return max((failure.get("severity", "P2") for failure in failures), key=lambda item: order.get(item, 0))


def load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def normalize_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("anchors", "items", "records", "data"):
            if isinstance(value.get(key), list):
                return value[key]
        return list(value.values())
    return []


def normalize_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(item) for item in value.values() if str(item).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
