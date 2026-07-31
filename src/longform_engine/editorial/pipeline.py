"""Deterministic editorial review task generation and status tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.agent_tasks import build_manifest, mark_tasks_for_chapter_type, mark_tasks_for_output, write_manifest
from longform_engine.config import ConfigDocument
from longform_engine.quality import refresh_feedback_registry
from longform_engine.storage import atomic_write_text, resolve_project_root


DEFAULT_EDITORIAL_TEAM: tuple[dict[str, str], ...] = (
    {
        "id": "planning_chief_editor",
        "display_name": "策划主编",
        "focus": "outline duty, longform promise, payoff timing, A/B/C quota discipline",
    },
    {
        "id": "writing_agent",
        "display_name": "写作特工",
        "focus": "scene execution, dialogue force, emotional evidence, action texture",
    },
    {
        "id": "anti_ai_editor",
        "display_name": "反 AI 编辑",
        "focus": "AI diction, template paragraphs, summary-heavy prose, meta residue",
    },
    {
        "id": "serial_verifier",
        "display_name": "连载核实官",
        "focus": "TCS, Character Memory, graph facts, relationship stage, location continuity",
    },
    {
        "id": "reader_quality_reviewer",
        "display_name": "读者体验编辑",
        "focus": "chapter duty, reader gain, scene fatigue, payoff cost, ending fit, platform-profile fit",
    },
    {
        "id": "canon_fidelity_reviewer",
        "display_name": "同人还原编辑",
        "focus": "canon motive, voice, relationship phase, world rules, divergence causality, agency, original contribution",
    },
    {
        "id": "executive_editor",
        "display_name": "总编辑",
        "focus": "P0/P1/P2 priority, unresolved risk, conditional pass streak, need-human decision",
    },
)

ROLE_ALIASES = {
    "planning_editor": "planning_chief_editor",
    "consistency_reviewer": "serial_verifier",
    "reader_experience_reviewer": "reader_quality_reviewer",
    "chief_editor": "executive_editor",
}


@dataclass(frozen=True)
class EditorialReviewResult:
    chapter_number: int
    review_file: str
    task_file: str
    status: str
    unresolved_items: int
    need_human: bool
    review_round: int
    severity_counts: dict[str, int]
    conditional_pass_streak: int
    need_human_reasons: tuple[str, ...]
    selected_roles: tuple[str, ...]
    risk_signals: tuple[str, ...]


@dataclass(frozen=True)
class EditorialBatchReviewResult:
    chapter_start: int
    chapter_end: int
    batch_file: str
    reviews: int
    need_human: bool
    health_report_files: dict[str, str]
    need_human_reasons: tuple[str, ...]


@dataclass(frozen=True)
class EditorialStatusResult:
    status_file: str
    unresolved_items: int
    conditional_passes: int
    need_human: bool
    severity_counts: dict[str, int]
    review_rounds: dict[str, int]
    conditional_pass_streak: int
    need_human_reasons: tuple[str, ...]
    human_request_file: str | None = None


@dataclass(frozen=True)
class EditorialSubmitResult:
    chapter_number: int
    role: str
    accepted: bool
    result_file: str
    validation_file: str
    aggregate_file: str
    severity_counts: dict[str, int]
    need_human: bool
    need_human_reasons: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class EditorialAggregateResult:
    chapter_number: int
    aggregate_file: str
    markdown_file: str
    result_files: tuple[str, ...]
    severity_counts: dict[str, int]
    unresolved_items: int
    missing_roles: tuple[str, ...]
    duplicate_role_results: tuple[dict[str, Any], ...]
    invalid_results: tuple[dict[str, Any], ...]
    conditional_passes: int
    need_human: bool
    need_human_reasons: tuple[str, ...]
    next_command: str
    disagreement_matrix: tuple[dict[str, Any], ...]
    minority_blockers: tuple[dict[str, Any], ...]


def editorial_review(config: ConfigDocument, *, chapter_number: int) -> EditorialReviewResult:
    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    chapter_path = find_chapter(root, chapter_number)
    if chapter_path is None:
        raise ValueError(f"Chapter ch{chapter_number:03d} not found in final or draft lanes.")

    text = safe_read_text(chapter_path)
    review_dir = review_root(root)
    review_dir.mkdir(parents=True, exist_ok=True)
    review_file = review_dir / f"ch{chapter_number:03d}.review.json"
    task_file = review_dir / f"ch{chapter_number:03d}.task.md"
    previous_payload = load_json(review_file, default={})
    previous_round = int(previous_payload.get("review_round") or 0) if isinstance(previous_payload, dict) else 0

    status, items = deterministic_editorial_items(text)
    counts = severity_counts(items)
    unresolved = unresolved_items(items)
    risk_signals = editorial_risk_signals(
        config,
        root=root,
        chapter_number=chapter_number,
        deterministic_items=items,
    )
    team = editorial_team(
        config,
        root=root,
        chapter_number=chapter_number,
        deterministic_items=items,
        risk_signals=risk_signals,
    )
    payload: dict[str, Any] = {
        "schema_version": 3,
        "chapter_number": chapter_number,
        "source_path": relative_path(root, chapter_path),
        "mode": "task_file_multi_role",
        "status": status,
        "review_round": previous_round + 1,
        "severity_counts": counts,
        "unresolved_items": unresolved,
        "items": items,
        "editorial_team": team,
        "role_selection": {
            "policy": "risk_based_editorial_selection_v1",
            "risk_signals": risk_signals,
            "selected_roles": [role["id"] for role in team],
            "configured_override": configured_editorial_roles(config),
        },
        "created_at": utc_now(),
    }
    current_reviews = reviews_with_current(root, payload)
    streak = conditional_pass_streak(current_reviews)
    reasons = need_human_reasons_for_review(config, status=status, items=items, conditional_streak=streak)
    payload["conditional_pass_streak"] = streak
    payload["need_human"] = bool(reasons)
    payload["need_human_reasons"] = reasons
    payload["agent_task_files"] = write_multi_agent_task_files(root, payload)

    atomic_write_text(review_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(task_file, format_review_task(payload))
    return EditorialReviewResult(
        chapter_number=chapter_number,
        review_file=str(review_file),
        task_file=str(task_file),
        status=status,
        unresolved_items=len(unresolved),
        need_human=bool(reasons),
        review_round=payload["review_round"],
        severity_counts=counts,
        conditional_pass_streak=streak,
        need_human_reasons=tuple(reasons),
        selected_roles=tuple(role["id"] for role in team),
        risk_signals=tuple(risk_signals),
    )


def editorial_batch_review(
    config: ConfigDocument,
    *,
    chapter_start: int,
    chapter_end: int,
) -> EditorialBatchReviewResult:
    if chapter_start <= 0 or chapter_end < chapter_start:
        raise ValueError("chapter_start/chapter_end are invalid.")
    root = resolve_project_root(config)
    results = [editorial_review(config, chapter_number=chapter) for chapter in range(chapter_start, chapter_end + 1)]
    reviews = reviews_in_range(root, chapter_start, chapter_end)
    findings = cross_chapter_findings(root, chapter_start, chapter_end)
    health_report_files = write_batch_health_reports(root, chapter_start, chapter_end, reviews, findings)
    counts = aggregate_severity_counts(reviews)
    streak = conditional_pass_streak(reviews)
    reasons = batch_need_human_reasons(config, results=results, findings=findings, conditional_streak=streak)
    batch_file = review_root(root) / f"batch_ch{chapter_start:03d}_ch{chapter_end:03d}.json"
    atomic_write_text(
        batch_file,
        json.dumps(
            {
                "schema_version": 2,
                "chapter_start": chapter_start,
                "chapter_end": chapter_end,
                "reviews": [result.__dict__ for result in results],
                "severity_counts": counts,
                "conditional_pass_streak": streak,
                "cross_chapter_findings": findings,
                "role_summary": default_role_summary(),
                "health_report_files": health_report_files,
                "need_human": bool(reasons),
                "need_human_reasons": reasons,
                "created_at": utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return EditorialBatchReviewResult(
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        batch_file=str(batch_file),
        reviews=len(results),
        need_human=bool(reasons),
        health_report_files=health_report_files,
        need_human_reasons=tuple(reasons),
    )


def editorial_status(config: ConfigDocument) -> EditorialStatusResult:
    root = resolve_project_root(config)
    reviews = load_reviews(root)
    unresolved: list[dict[str, Any]] = []
    conditional = 0
    rounds: dict[str, int] = {}
    for review in reviews:
        chapter = int(review.get("chapter_number") or 0)
        if chapter:
            rounds[f"ch{chapter:03d}"] = int(review.get("review_round") or 1)
        items = review.get("items") if isinstance(review.get("items"), list) else []
        unresolved.extend(item for item in items if isinstance(item, dict) and item.get("status") != "resolved")
        if review.get("status") == "conditional_pass":
            conditional += 1
    counts = aggregate_severity_counts(reviews)
    streak = conditional_pass_streak(reviews)
    reasons = need_human_reasons_for_status(config, reviews=reviews, conditional_streak=streak)
    status_file = review_root(root) / "status.json"
    status_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        status_file,
        json.dumps(
            {
                "schema_version": 2,
                "reviews": len(reviews),
                "unresolved_items": len(unresolved),
                "unresolved_by_severity": counts,
                "conditional_passes": conditional,
                "conditional_pass_streak": streak,
                "review_rounds": rounds,
                "need_human": bool(reasons),
                "need_human_reasons": reasons,
                "updated_at": utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return EditorialStatusResult(
        status_file=str(status_file),
        unresolved_items=len(unresolved),
        conditional_passes=conditional,
        need_human=bool(reasons),
        severity_counts=counts,
        review_rounds=rounds,
        conditional_pass_streak=streak,
        need_human_reasons=tuple(reasons),
    )


def editorial_need_human(
    config: ConfigDocument,
    *,
    chapter_number: int | None = None,
    reason: str | None = None,
) -> EditorialStatusResult:
    status = editorial_status(config)
    root = resolve_project_root(config)
    review_dir = review_root(root)
    review_dir.mkdir(parents=True, exist_ok=True)
    request_file = review_dir / (f"need_human_ch{chapter_number:03d}.json" if chapter_number else "need_human.json")
    request_reason = reason or "manual editorial escalation requested"
    payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "reason": request_reason,
        "status_file": relative_path(root, Path(status.status_file)),
        "status_snapshot": {
            "unresolved_items": status.unresolved_items,
            "severity_counts": status.severity_counts,
            "conditional_passes": status.conditional_passes,
            "conditional_pass_streak": status.conditional_pass_streak,
            "need_human_reasons": list(status.need_human_reasons),
        },
        "created_at": utc_now(),
    }
    atomic_write_text(request_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    status_path = Path(status.status_file)
    status_payload = load_json(status_path, default={})
    if not isinstance(status_payload, dict):
        status_payload = {}
    reasons = list(status_payload.get("need_human_reasons") or [])
    if "manual_escalation" not in reasons:
        reasons.append("manual_escalation")
    status_payload["need_human"] = True
    status_payload["need_human_reasons"] = reasons
    status_payload["manual_reason"] = request_reason
    status_payload["human_request_file"] = relative_path(root, request_file)
    status_payload["updated_at"] = utc_now()
    atomic_write_text(status_path, json.dumps(status_payload, ensure_ascii=False, indent=2) + "\n")
    return EditorialStatusResult(
        status_file=str(status_path),
        unresolved_items=status.unresolved_items,
        conditional_passes=status.conditional_passes,
        need_human=True,
        severity_counts=status.severity_counts,
        review_rounds=status.review_rounds,
        conditional_pass_streak=status.conditional_pass_streak,
        need_human_reasons=tuple(reasons),
        human_request_file=str(request_file),
    )


def editorial_submit_review(
    config: ConfigDocument,
    *,
    chapter_number: int,
    role: str,
    file_path: str | Path,
) -> EditorialSubmitResult:
    """Validate one host-agent editorial role result and aggregate accepted results."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    role_id = role_definition(role)["id"]
    path = resolve_editorial_result_path(root, file_path)
    payload = load_json(path, default={})
    errors, warnings, normalized = validate_editorial_result_payload(
        payload,
        chapter_number=chapter_number,
        role_id=role_id,
        root=root,
        result_file=path,
    )
    validation_file = editorial_validation_file(root, chapter_number, role_id)
    accepted = not errors
    validation_payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "role_id": role_id,
        "result_file": relative_path(root, path),
        "accepted": accepted,
        "errors": errors,
        "warnings": warnings,
        "next_command": (
            f"longform-engine editorial aggregate project.yaml --chapter {chapter_number}"
            if accepted
            else f"longform-engine editorial review project.yaml --chapter {chapter_number}"
        ),
        "validated_at": utc_now(),
    }
    atomic_write_text(validation_file, json.dumps(validation_payload, ensure_ascii=False, indent=2) + "\n")
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=path,
        to_status="validated" if accepted else "invalid",
        command="editorial submit-review",
        result=validation_file,
        from_statuses=("awaiting_agent", "submitted"),
    )
    if not accepted:
        raise ValueError(f"editorial review result did not validate: {'; '.join(errors)}")

    normalized_file = editorial_normalized_file(root, chapter_number, role_id)
    atomic_write_text(normalized_file, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    aggregate = editorial_aggregate(config, chapter_number=chapter_number)
    return EditorialSubmitResult(
        chapter_number=chapter_number,
        role=role_id,
        accepted=True,
        result_file=str(path),
        validation_file=str(validation_file),
        aggregate_file=aggregate.aggregate_file,
        severity_counts=aggregate.severity_counts,
        need_human=aggregate.need_human,
        need_human_reasons=aggregate.need_human_reasons,
        next_command=aggregate.next_command,
    )


def editorial_aggregate(config: ConfigDocument, *, chapter_number: int) -> EditorialAggregateResult:
    """Aggregate accepted role results into one auditable editorial decision."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    result_dir = review_root(root) / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    expected_roles = expected_editorial_roles(config, root=root, chapter_number=chapter_number)
    accepted: list[dict[str, Any]] = []
    result_files: list[str] = []
    for path in sorted(result_dir.glob(f"ch{chapter_number:03d}.*.normalized.json")):
        payload = load_json(path, default={})
        if isinstance(payload, dict):
            accepted.append(payload)
            result_files.append(relative_path(root, path))
    accepted_roles = {role_definition(str(result.get("role_id") or ""))["id"] for result in accepted}
    missing_roles = tuple(role for role in expected_roles if role not in accepted_roles)
    duplicate_role_results = tuple(duplicate_editorial_role_results(root, chapter_number))
    invalid_results = tuple(invalid_editorial_results(root, chapter_number))

    items: list[dict[str, Any]] = []
    verdicts: list[str] = []
    for result in accepted:
        verdicts.append(str(result.get("verdict") or "pass"))
        for item in result.get("items") or []:
            if isinstance(item, dict):
                items.append(item)
    counts = severity_counts([item for item in items if item.get("status") != "resolved"])
    unresolved = unresolved_items(items)
    disagreement = build_editorial_disagreement_matrix(accepted)
    disagreement_matrix = disagreement["matrix"]
    minority_blockers = disagreement["minority_blockers"]
    reasons: list[str] = []
    if counts["P0"]:
        reasons.append("unresolved_P0")
    if counts["P1"]:
        reasons.append("unresolved_P1")
    if any(verdict in {"needs_revision", "rewrite", "blocked"} for verdict in verdicts):
        reasons.append("editorial_blocking_verdict")
    if repeated_conditional_pass(root, chapter_number, verdicts):
        reasons.append("repeated_conditional_pass")
    if missing_roles:
        reasons.append("missing_editorial_roles")
    if duplicate_role_results:
        reasons.append("duplicate_role_results")
    if invalid_results:
        reasons.append("invalid_role_results")
    if minority_blockers:
        reasons.append("minority_P0_P1")
    if disagreement["human_decisions"]:
        reasons.append("editorial_evidence_conflict")
    reasons = dedupe(reasons)
    need_human = bool(reasons)
    next_command = (
        f"longform-engine editorial need-human project.yaml --chapter {chapter_number} --reason editorial_aggregate"
        if need_human
        else f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human"
    )
    aggregate_file = review_root(root) / f"ch{chapter_number:03d}.aggregate.json"
    markdown_file = review_root(root) / f"ch{chapter_number:03d}.aggregate.md"
    feedback_registry: dict[str, Any] = {
        "status": "not_updated",
        "hard_boundary": "workbench guidance only; never a canonical fact source",
    }
    try:
        records = refresh_feedback_registry(
            root,
            chapter_number=chapter_number,
            observations=editorial_feedback_observations(
                unresolved,
                source_path=relative_path(root, aggregate_file),
                chapter_number=chapter_number,
            ),
        )
        feedback_registry = {
            "status": "updated",
            "path": "50_workbench/quality_feedback/registry.jsonl",
            "records": len(records),
            "hard_boundary": "workbench guidance only; never a canonical fact source",
        }
    except (OSError, ValueError) as exc:
        feedback_registry = {
            "status": "warning",
            "warning": str(exc),
            "hard_boundary": "registry failure does not block aggregate or chapter finalization",
        }
    payload = {
        "schema_version": 2,
        "chapter_number": chapter_number,
        "accepted_results": result_files,
        "result_count": len(accepted),
        "expected_roles": expected_roles,
        "accepted_roles": sorted(accepted_roles),
        "missing_roles": list(missing_roles),
        "duplicate_role_results": list(duplicate_role_results),
        "invalid_results": list(invalid_results),
        "conditional_passes": verdicts.count("conditional_pass"),
        "severity_counts": counts,
        "unresolved_items": unresolved,
        "consensus_findings": disagreement["consensus_findings"],
        "conflicting_findings": disagreement["conflicting_findings"],
        "minority_blockers": minority_blockers,
        "disagreement_matrix": disagreement_matrix,
        "human_decisions": disagreement["human_decisions"],
        "need_human": need_human,
        "need_human_reasons": reasons,
        "feedback_registry": feedback_registry,
        "next_command": next_command,
        "updated_at": utc_now(),
    }
    atomic_write_text(aggregate_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(markdown_file, format_editorial_aggregate_markdown(payload))
    mark_tasks_for_chapter_type(
        root,
        chapter_number=chapter_number,
        task_types=("editorial_review",),
        to_status="applied",
        command="editorial aggregate",
        artifact=aggregate_file,
        result=next_command,
        from_statuses=("validated",),
    )
    return EditorialAggregateResult(
        chapter_number=chapter_number,
        aggregate_file=str(aggregate_file),
        markdown_file=str(markdown_file),
        result_files=tuple(result_files),
        severity_counts=counts,
        unresolved_items=len(unresolved),
        missing_roles=missing_roles,
        duplicate_role_results=duplicate_role_results,
        invalid_results=invalid_results,
        conditional_passes=verdicts.count("conditional_pass"),
        need_human=need_human,
        need_human_reasons=tuple(reasons),
        next_command=next_command,
        disagreement_matrix=tuple(disagreement_matrix),
        minority_blockers=tuple(minority_blockers),
    )


def deterministic_editorial_items(text: str) -> tuple[str, list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    word_count = len(re.sub(r"\s+", "", text))
    lower_text = text.lower()
    if word_count < 800:
        items.append(
            review_item(
                "short_chapter",
                "P2",
                "chapter is short for longform web pacing",
                role_id="writing_agent",
            )
        )
    if any(marker in text for marker in ("TODO", "写作说明", "作者按", "[说明]")):
        items.append(
            review_item(
                "meta_residue",
                "P0",
                "meta or prompt residue remains in prose",
                role_id="anti_ai_editor",
            )
        )
    if any(marker in lower_text for marker in ("plot_hole", "logic break", "contradiction")) or any(
        marker in text for marker in ("逻辑断裂", "前后矛盾")
    ):
        items.append(
            review_item(
                "logic_continuity_risk",
                "P1",
                "logic or continuity marker remains unresolved",
                role_id="serial_verifier",
            )
        )
    if duplicate_sentence_ratio(text) > 0.25:
        items.append(
            review_item(
                "repetition",
                "P2",
                "sentence repetition is high",
                role_id="anti_ai_editor",
            )
        )
    ai_markers = [marker for marker in ("不禁", "仿佛", "意义深远", "嘴角微扬") if marker in text]
    if len(ai_markers) >= 2:
        items.append(
            review_item(
                "ai_diction_cluster",
                "P2",
                f"AI-flavored diction cluster: {', '.join(ai_markers[:4])}",
                role_id="anti_ai_editor",
            )
        )
    if not items:
        return "pass", []
    if any(item["severity"] in {"P0", "P1"} for item in items):
        return "needs_revision", items
    return "conditional_pass", items


def build_editorial_disagreement_matrix(results: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Preserve evidence-backed disagreement instead of reducing reviews to votes."""

    accepted_roles = {
        str(result.get("role_id") or "")
        for result in results
        if str(result.get("role_id") or "")
    }
    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        role_id = str(result.get("role_id") or "")
        confidence = result.get("confidence")
        for item in result.get("items") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "unnamed_finding")
            grouped.setdefault(code, []).append(
                {
                    **item,
                    "role_id": role_id,
                    "confidence": confidence,
                    "evidence_grade": result.get("evidence_grade", "unknown"),
                }
            )

    matrix: list[dict[str, Any]] = []
    consensus: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    minority_blockers: list[dict[str, Any]] = []
    human_decisions: list[dict[str, Any]] = []
    for code, findings in sorted(grouped.items()):
        roles = sorted({str(item.get("role_id") or "") for item in findings})
        severities = sorted({str(item.get("severity") or "") for item in findings})
        statuses = sorted({str(item.get("status") or "") for item in findings})
        severity_by_role = {
            role: sorted(
                {
                    str(item.get("severity") or "")
                    for item in findings
                    if str(item.get("role_id") or "") == role
                }
            )
            for role in roles
        }
        row = {
            "issue_code": code,
            "roles": roles,
            "role_count": len(roles),
            "severity_by_role": severity_by_role,
            "status_by_role": {
                role: sorted(
                    {
                        str(item.get("status") or "")
                        for item in findings
                        if str(item.get("role_id") or "") == role
                    }
                )
                for role in roles
            },
            "confidence_by_role": {
                role: next(
                    (
                        item.get("confidence")
                        for item in findings
                        if str(item.get("role_id") or "") == role
                    ),
                    None,
                )
                for role in roles
            },
            "validated_evidence_roles": sorted(
                {
                    str(item.get("role_id") or "")
                    for item in findings
                    if item.get("evidence_validated") is True
                }
            ),
            "evidence_span_overlap": evidence_overlap(findings),
            "severity_conflict": len(severities) > 1,
            "status_conflict": len(statuses) > 1,
            "consensus": len(roles) >= 2 and len(severities) == 1 and len(statuses) == 1,
        }
        validated_blocking_roles = {
            str(item.get("role_id") or "")
            for item in findings
            if item.get("evidence_validated") is True
            and str(item.get("severity") or "") in {"P0", "P1"}
        }
        row["minority_P0_P1"] = bool(
            validated_blocking_roles
            and len(accepted_roles) > 1
            and len(validated_blocking_roles) < len(accepted_roles)
        )
        matrix.append(row)
        if row["consensus"]:
            consensus.append(row)
        if row["severity_conflict"] or row["status_conflict"]:
            conflicts.append(row)
        if row["minority_P0_P1"]:
            minority_blockers.append(row)
        if row["minority_P0_P1"] or row["severity_conflict"] or row["status_conflict"]:
            human_decisions.append(
                {
                    "issue_code": code,
                    "reason": (
                        "evidence-backed minority P0/P1 must not be outvoted"
                        if row["minority_P0_P1"]
                        else "reviewers disagree on severity or status"
                    ),
                    "roles": roles,
                }
            )
    return {
        "matrix": matrix,
        "consensus_findings": consensus,
        "conflicting_findings": conflicts,
        "minority_blockers": minority_blockers,
        "human_decisions": human_decisions,
    }


def evidence_overlap(findings: list[dict[str, Any]]) -> float | None:
    evidence_sets = [
        evidence_ngrams(item.get("evidence"))
        for item in findings
        if normalize_string_list(item.get("evidence"))
    ]
    if len(evidence_sets) < 2:
        return None
    scores: list[float] = []
    for left_index, left in enumerate(evidence_sets):
        for right in evidence_sets[left_index + 1:]:
            union = left | right
            scores.append(len(left & right) / len(union) if union else 1.0)
    return round(sum(scores) / len(scores), 4) if scores else None


def evidence_ngrams(value: Any) -> set[str]:
    text = " ".join(normalize_string_list(value))
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[index:index + 2] for index in range(len(compact) - 1)}


def editorial_feedback_observations(
    unresolved: list[dict[str, Any]],
    *,
    source_path: str,
    chapter_number: int,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for item in unresolved:
        if not isinstance(item, dict):
            continue
        evidence = normalize_string_list(item.get("evidence"))
        evidence_hash = hashlib.sha256(
            json.dumps(evidence or [str(item.get("message") or "")], ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        observations.append(
            {
                "issue_code": str(item.get("code") or "editorial_finding"),
                "severity": str(item.get("severity") or "P2"),
                "kind": "editorial_aggregate",
                "source_path": source_path,
                "owner_task": f"editorial_review:ch{chapter_number:03d}",
                "summary": str(item.get("message") or item.get("code") or ""),
                "evidence_hash": evidence_hash,
            }
        )
    return observations


def review_item(code: str, severity: str, message: str, *, role_id: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "status": "open",
        "role_id": role_id,
        "message": message,
    }


def format_review_task(payload: dict[str, Any]) -> str:
    counts = payload.get("severity_counts") or {}
    lines = [
        f"# Editorial Review Task ch{payload['chapter_number']:03d}",
        "",
        f"- Mode: {payload['mode']}",
        f"- Source: `{payload['source_path']}`",
        f"- Status: {payload['status']}",
        f"- Review round: {payload['review_round']}",
        f"- Severity counts: P0={counts.get('P0', 0)}, P1={counts.get('P1', 0)}, P2={counts.get('P2', 0)}",
        f"- Unresolved items: {len(payload.get('unresolved_items') or [])}",
        f"- Conditional pass streak: {payload.get('conditional_pass_streak', 0)}",
        f"- Need human: {payload['need_human']}",
        "",
        "## Need-Human Reasons",
        "",
    ]
    for reason in payload.get("need_human_reasons", []):
        lines.append(f"- {reason}")
    if not payload.get("need_human_reasons"):
        lines.append("- None")
    lines.extend(["", "## Editorial Team", ""])
    for role in payload.get("editorial_team", []):
        lines.append(f"- {role['display_name']} (`{role['id']}`): {role['focus']}")
    lines.extend(["", "## Agent Task Files", ""])
    for task_file in payload.get("agent_task_files", []):
        lines.append(f"- `{task_file}`")
    if not payload.get("agent_task_files"):
        lines.append("- None")
    lines.extend(["", "## Open Items", ""])
    for item in payload.get("items", []):
        lines.append(f"- [{item.get('severity')}] {item.get('code')} ({item.get('role_id')}): {item.get('message')}")
    if not payload.get("items"):
        lines.append("- None")
    lines.extend(
        [
            "",
            "Multi-agent hooks are task files only, not hard runtime dependencies.",
            "Review output must not mutate final/RAG/graph/memory/TCS/SQLite directly.",
            "",
        ]
    )
    return "\n".join(lines)


def write_multi_agent_task_files(root: Path, payload: dict[str, Any]) -> list[str]:
    task_dir = review_root(root) / "agent_tasks" / f"ch{payload['chapter_number']:03d}"
    result_dir = review_root(root) / "results"
    task_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    files: list[str] = []
    for role in payload.get("editorial_team", []):
        role_id = sanitize_role_id(str(role.get("id") or "reviewer"))
        path = task_dir / f"{role_id}.md"
        context_file = task_dir / f"{role_id}.context.json"
        output_file = result_dir / f"ch{payload['chapter_number']:03d}.{role_id}.json"
        manifest_file = task_dir / f"{role_id}.agent_task.json"
        source_inputs = editorial_role_source_inputs(root, payload, role_id)
        context_payload = build_editorial_context_payload(
            root,
            payload=payload,
            role_id=role_id,
            source_inputs=source_inputs,
        )
        atomic_write_text(context_file, json.dumps(context_payload, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(
            path,
            format_role_task(
                root,
                payload,
                role,
                output_file=output_file,
                context_payload=context_payload,
            ),
        )
        role_inputs = [path, context_file, *source_inputs]
        manifest = build_manifest(
            root,
            task_type="editorial_review",
            chapter_number=int(payload["chapter_number"]),
            input_files=role_inputs,
            allowed_output_paths=[output_file],
            output_schema="editorial_role_review_v2",
            validate_command=(
                f"longform-engine editorial submit-review project.yaml --chapter {int(payload['chapter_number'])} "
                f"--role {role_id} --file {relative_path(root, output_file)}"
            ),
            apply_command=f"longform-engine editorial aggregate project.yaml --chapter {int(payload['chapter_number'])}",
            failure_next_command=(
                f"longform-engine editorial need-human project.yaml --chapter {int(payload['chapter_number'])} "
                "--reason editorial_result_invalid"
            ),
            task_id=f"editorial_review:{role_id}:ch{int(payload['chapter_number']):03d}:v2",
            context_policy={
                "required_files": role_inputs[: min(3, len(role_inputs))],
                "optional_files": role_inputs[min(3, len(role_inputs)):],
                "compiled_brief": path,
                "selection_report": path,
                "max_files": 7,
                "max_chars": 18_000,
            },
        )
        write_manifest(root, manifest, manifest_file)
        files.append(relative_path(root, path))
    return files


def editorial_role_source_inputs(
    root: Path,
    payload: dict[str, Any],
    role_id: str,
) -> list[Path]:
    chapter_number = int(payload["chapter_number"])
    chapter = root / str(payload.get("source_path") or "")
    card = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    candidates_by_role = {
        "planning_chief_editor": [
            chapter,
            card,
            root / "20_outline" / "book_outline.md",
            root / "00_governance" / "reader_contract.md",
            root / "30_state" / "reward_ledger.jsonl",
        ],
        "writing_agent": [
            chapter,
            card,
            root / "10_bible" / "creative_brief.json",
            root / "00_governance" / "reader_contract.md",
        ],
        "anti_ai_editor": [
            chapter,
            root / "50_workbench" / "humanizer_tasks" / f"ch{chapter_number:03d}.humanize_check.json",
            root / "50_workbench" / "quality_feedback" / "registry.jsonl",
            card,
        ],
        "serial_verifier": [
            chapter,
            root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json",
            root / "30_state" / "character_memory.json",
            card,
        ],
        "reader_quality_reviewer": [
            chapter,
            card,
            root / "00_governance" / "reader_contract.md",
            root / "50_workbench" / "quality_reviews" / f"ch{chapter_number:03d}.reader_payoff.normalized.json",
            root / "30_state" / "reward_ledger.jsonl",
        ],
        "canon_fidelity_reviewer": [
            chapter,
            root / "10_bible" / "fanfiction" / "source_canon.json",
            root / "10_bible" / "fanfiction" / "fanfiction_bible.json",
            card,
            root / "10_bible" / "creative_brief.json",
        ],
        "executive_editor": [
            chapter,
            card,
            root / "50_workbench" / "quality_feedback" / "registry.jsonl",
        ],
    }
    candidates = candidates_by_role.get(
        role_id,
        [chapter, card, root / "00_governance" / "reader_contract.md"],
    )
    return dedupe_paths(path for path in candidates if path.exists())[:5]


def build_editorial_context_payload(
    root: Path,
    *,
    payload: dict[str, Any],
    role_id: str,
    source_inputs: list[Path],
) -> dict[str, Any]:
    chapter_number = int(payload["chapter_number"])
    review_round = int(payload.get("review_round") or 1)
    context_hash = context_digest_hash(root, source_inputs)
    return {
        "schema": "editorial_context_isolation_v1",
        "chapter_number": chapter_number,
        "role_id": role_id,
        "review_round": review_round,
        "reviewer_instance_id": (
            f"editorial:{role_id}:ch{chapter_number:03d}:r{review_round}:{context_hash[:12]}"
        ),
        "context_digest_hash": context_hash,
        "independence_mode": "same_host_isolated_context",
        "declared_source_files": [relative_path(root, path) for path in source_inputs],
        "excluded_peer_results": [
            f"50_workbench/editorial_reviews/results/ch{chapter_number:03d}.*.json",
            f"50_workbench/editorial_reviews/ch{chapter_number:03d}.aggregate.json",
        ],
        "identity_claim_boundary": (
            "Agent product/version and independence mode are self-reported evidence; "
            "the engine validates consistency, not reviewer identity."
        ),
        "created_at": utc_now(),
    }


def context_digest_hash(root: Path, paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: relative_path(root, item)):
        relative = relative_path(root, path)
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def format_role_task(
    root: Path,
    payload: dict[str, Any],
    role: dict[str, str],
    *,
    output_file: Path | None = None,
    context_payload: dict[str, Any] | None = None,
) -> str:
    counts = payload.get("severity_counts") or {}
    role_id = role["id"]
    relevant = [item for item in payload.get("items", []) if item.get("role_id") in {role_id, None}]
    output_path = relative_path(root, output_file) if output_file else ""
    context_payload = context_payload or {}
    lines = [
        f"# Editorial Agent Task: {role['display_name']} ({role_id})",
        "",
        f"- Chapter: ch{payload['chapter_number']:03d}",
        f"- Source: `{payload['source_path']}`",
        f"- Output JSON: `{output_path}`" if output_path else "- Output JSON: declared in manifest",
        "- Mode: task-file hook",
        f"- Review round: {payload['review_round']}",
        f"- Reviewer instance: `{context_payload.get('reviewer_instance_id', '')}`",
        f"- Context digest: `{context_payload.get('context_digest_hash', '')}`",
        f"- Independence mode: `{context_payload.get('independence_mode', 'same_host_isolated_context')}`",
        f"- Severity model: P0={counts.get('P0', 0)}, P1={counts.get('P1', 0)}, P2={counts.get('P2', 0)}",
        f"- Conditional pass streak: {payload.get('conditional_pass_streak', 0)}",
        "",
        "## Role Mission",
        "",
        role["focus"],
        "",
        "## Required Checks",
        "",
        role_instruction(role_id),
        "",
        "## Open Items For This Role",
        "",
    ]
    for item in relevant:
        lines.append(f"- [{item.get('severity')}] {item.get('code')}: {item.get('message')}")
    if not relevant:
        lines.append("- No role-specific deterministic item; still perform the role mission.")
    lines.extend(
        [
            "",
            "Write one JSON result to the output path only.",
            (
                "Required JSON fields: schema_version=2, chapter_number, role_id, verdict, items, "
                "reviewer_instance_id, agent_product, agent_version, context_digest_hash, "
                "independence_mode, review_round, confidence."
            ),
            "Valid verdicts: pass, conditional_pass, needs_revision, rewrite, blocked.",
            "Every P0/P1 item must cite one or more exact excerpts from the current chapter in `evidence`.",
            "Do not read any other editorial role result before submitting this result.",
            "Use only the files declared by this role's AgentTaskManifest.",
            "Do not mutate final/RAG/graph/memory/TCS/SQLite directly.",
            "",
        ]
    )
    return "\n".join(lines)


def role_instruction(role_id: str) -> str:
    instructions = {
        "planning_chief_editor": (
            "Check chapter duty, outline anchor, payoff timing, event quota pressure, reverse-brake retention, "
            "and whether the chapter advances the longform promise."
        ),
        "writing_agent": (
            "Check scene execution, dialogue difference, action-carried psychology, transition smoothness, "
            "tail hook, and whether prose can be repaired without changing canon."
        ),
        "anti_ai_editor": (
            "Check AI diction, high-frequency filler, template paragraphs, summary-heavy prose, same-shape sentences, "
            "interchangeable dialogue, and meta residue."
        ),
        "serial_verifier": (
            "Check TCS, Character Memory, graph facts, relationship stage, location continuity, ability limits, "
            "foreshadowing state, and contradiction markers."
        ),
        "reader_quality_reviewer": (
            "Check whether the declared chapter duty is fulfilled, the reader receives a concrete gain, the gain has "
            "an earned cost, scenes avoid upgrade-log repetition, and the ending mode fits this chapter instead of "
            "forcing a universal cliffhanger."
        ),
        "canon_fidelity_reviewer": (
            "Read only declared source canon and fanfiction design. Check motive, speech habits, relationship phase, "
            "ability limits, location/era knowledge, source-world rules, and canon character agency. Treat declared "
            "AU/divergence as valid when butterfly effects support it. Flag skin-only characterization, collective "
            "irrationality, canon characters reduced to props, and setting names retained while rules silently fail."
        ),
        "executive_editor": (
            "Prioritize P0/P1/P2 issues, review unresolved items and conditional pass streak, then decide proceed, repair, "
            "batch-review, or need-human."
        ),
    }
    return instructions.get(role_id, "Review this chapter from the named role.")


def default_role_summary() -> dict[str, str]:
    return {role["id"]: role["focus"] for role in DEFAULT_EDITORIAL_TEAM}


def cross_chapter_findings(root: Path, chapter_start: int, chapter_end: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    reviews = reviews_in_range(root, chapter_start, chapter_end)
    conditional = [review for review in reviews if review.get("status") == "conditional_pass"]
    if len(conditional) >= 2:
        findings.append(
            {
                "code": "repeated_conditional_pass",
                "severity": "P2",
                "message": "multiple conditional editorial passes in the batch; check style fatigue and pacing drift",
            }
        )
    needs = [review for review in reviews if review.get("status") == "needs_revision"]
    if needs:
        findings.append(
            {
                "code": "batch_blocking_revisions",
                "severity": "P1",
                "message": "one or more chapters in the batch need revision before continuation",
            }
        )
    short_count = count_item_code(reviews, "short_chapter")
    if short_count >= 3:
        findings.append(
            {
                "code": "batch_pacing_thin_chapters",
                "severity": "P2",
                "message": f"{short_count} chapters are short; run pacing and expansion checks before serial continuation",
            }
        )
    ai_count = count_item_code(reviews, "ai_diction_cluster") + count_item_code(reviews, "repetition")
    if ai_count >= 2:
        findings.append(
            {
                "code": "batch_ai_taste_cluster",
                "severity": "P2",
                "message": f"{ai_count} AI-taste or repetition findings across the batch",
            }
        )
    logic_count = count_item_code(reviews, "logic_continuity_risk")
    if logic_count:
        findings.append(
            {
                "code": "batch_logic_risk",
                "severity": "P1",
                "message": f"{logic_count} logic or continuity risks require serial verifier review",
            }
        )
    if not findings:
        findings.append({"code": "batch_review_clean", "severity": "PASS", "message": "no deterministic cross-chapter issues"})
    return findings


def write_batch_health_reports(
    root: Path,
    chapter_start: int,
    chapter_end: int,
    reviews: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, str]:
    report_dir = review_root(root) / "batch_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"batch_ch{chapter_start:03d}_ch{chapter_end:03d}"
    specs = {
        "pacing": ("Pacing Health Report", ("short_chapter", "repeated_conditional_pass", "batch_pacing_thin_chapters")),
        "logic": ("Logic Health Report", ("logic_continuity_risk", "batch_blocking_revisions", "batch_logic_risk")),
        "ai_taste": ("AI Taste Report", ("ai_diction_cluster", "repetition", "batch_ai_taste_cluster")),
    }
    files: dict[str, str] = {}
    for key, (title, codes) in specs.items():
        path = report_dir / f"{prefix}.{key}.md"
        atomic_write_text(path, format_batch_health_report(title, chapter_start, chapter_end, reviews, findings, codes))
        files[key] = relative_path(root, path)
    return files


def format_batch_health_report(
    title: str,
    chapter_start: int,
    chapter_end: int,
    reviews: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    codes: tuple[str, ...],
) -> str:
    relevant_findings = [item for item in findings if item.get("code") in codes]
    relevant_items: list[dict[str, Any]] = []
    for review in reviews:
        chapter = int(review.get("chapter_number") or 0)
        for item in review.get("items") or []:
            if isinstance(item, dict) and item.get("code") in codes:
                relevant_items.append({"chapter_number": chapter, **item})
    lines = [
        f"# {title}",
        "",
        f"- Range: ch{chapter_start:03d}-ch{chapter_end:03d}",
        f"- Chapters reviewed: {len(reviews)}",
        f"- Conditional pass streak: {conditional_pass_streak(reviews)}",
        "",
        "## Cross-Chapter Findings",
        "",
    ]
    for item in relevant_findings:
        lines.append(f"- [{item.get('severity')}] {item.get('code')}: {item.get('message')}")
    if not relevant_findings:
        lines.append("- None")
    lines.extend(["", "## Chapter Items", ""])
    for item in relevant_items:
        lines.append(
            f"- ch{int(item.get('chapter_number') or 0):03d} [{item.get('severity')}] "
            f"{item.get('code')}: {item.get('message')}"
        )
    if not relevant_items:
        lines.append("- None")
    lines.extend(["", "These reports are workbench review artifacts only.", ""])
    return "\n".join(lines)


def validate_editorial_result_payload(
    payload: Any,
    *,
    chapter_number: int,
    role_id: str,
    root: Path,
    result_file: Path,
) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return ["editorial review result must be a JSON object."], [], {}
    try:
        schema_version = int(payload.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 0
    if schema_version not in {1, 2}:
        errors.append("schema_version must be 1 or 2.")
    if int(payload.get("chapter_number") or 0) != chapter_number:
        errors.append("payload chapter_number does not match command chapter.")
    payload_role = role_definition(str(payload.get("role_id") or "")).get("id")
    if payload_role != role_id:
        errors.append(f"payload role_id must be {role_id}.")
    review_payload = load_json(
        review_root(root) / f"ch{chapter_number:03d}.review.json",
        default={},
    )
    selected_roles = {
        role_definition(str(item.get("id") or ""))["id"]
        for item in (
            review_payload.get("editorial_team")
            if isinstance(review_payload, dict) and isinstance(review_payload.get("editorial_team"), list)
            else []
        )
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    }
    if selected_roles and role_id not in selected_roles:
        errors.append(f"role_id {role_id} was not selected for this editorial review round.")
    verdict = normalize_verdict(payload.get("verdict"))
    if not verdict:
        errors.append("verdict must be pass, conditional_pass, needs_revision, rewrite, or blocked.")
        verdict = "blocked"
    items_raw = payload.get("items")
    if not isinstance(items_raw, list):
        errors.append("items must be a list.")
        items_raw = []
    items: list[dict[str, Any]] = []
    chapter_path = find_chapter(root, chapter_number)
    chapter_text = safe_read_text(chapter_path) if chapter_path is not None else ""
    for index, item in enumerate(items_raw):
        if not isinstance(item, dict):
            errors.append(f"items[{index}] must be an object.")
            continue
        code = str(item.get("code") or "").strip()
        severity = str(item.get("severity") or "").strip().upper()
        message = str(item.get("message") or "").strip()
        if not code:
            errors.append(f"items[{index}] missing code.")
        if severity not in {"P0", "P1", "P2", "PASS"}:
            errors.append(f"items[{index}] severity must be P0, P1, P2, or PASS.")
        if not message:
            errors.append(f"items[{index}] missing message.")
        evidence = normalize_string_list(item.get("evidence"))
        evidence_validated = bool(
            schema_version == 2
            and evidence
            and chapter_text
            and all(normalize_evidence_text(fragment) in normalize_evidence_text(chapter_text) for fragment in evidence)
        )
        normalized_item = {
            "code": code or f"item_{index + 1}",
            "severity": severity or "P2",
            "status": str(item.get("status") or ("resolved" if severity == "PASS" else "open")),
            "role_id": role_id,
            "message": message,
            "evidence": evidence,
            "evidence_validated": evidence_validated,
            "recommendation": str(item.get("recommendation") or ""),
        }
        if normalized_item["severity"] in {"P0", "P1"} and not normalized_item["evidence"]:
            if schema_version == 2:
                errors.append(f"items[{index}] {normalized_item['severity']} requires exact chapter evidence.")
            else:
                warnings.append(f"items[{index}] {normalized_item['severity']} has no evidence span.")
        elif schema_version == 2 and normalized_item["evidence"] and not evidence_validated:
            errors.append(f"items[{index}] evidence must match exact text in the current chapter.")
        items.append(normalized_item)
    if verdict == "pass" and any(item.get("severity") in {"P0", "P1"} and item.get("status") != "resolved" for item in items):
        errors.append("pass verdict cannot include unresolved P0/P1 items.")
    context = load_editorial_context(root, chapter_number=chapter_number, role_id=role_id)
    reviewer_instance_id = str(payload.get("reviewer_instance_id") or "").strip()
    agent_product = str(payload.get("agent_product") or "").strip()
    agent_version = str(payload.get("agent_version") or "").strip()
    submitted_context_hash = str(payload.get("context_digest_hash") or "").strip()
    independence_mode = str(payload.get("independence_mode") or "").strip()
    review_round = int(payload.get("review_round") or 0)
    confidence = normalize_confidence(payload.get("confidence"))
    if schema_version == 2:
        if not context:
            errors.append("editorial v2 context metadata is missing; regenerate the editorial review task.")
        else:
            if reviewer_instance_id != str(context.get("reviewer_instance_id") or ""):
                errors.append("reviewer_instance_id does not match the isolated role context.")
            if submitted_context_hash != str(context.get("context_digest_hash") or ""):
                errors.append("context_digest_hash does not match the isolated role context.")
            declared_paths = [
                root / str(path)
                for path in context.get("declared_source_files") or []
                if str(path).strip()
            ]
            if any(not path.exists() for path in declared_paths):
                errors.append("one or more declared editorial context files no longer exist.")
            elif context_digest_hash(root, declared_paths) != str(context.get("context_digest_hash") or ""):
                errors.append("editorial context changed after task creation; regenerate the role task.")
            if review_round != int(context.get("review_round") or 0):
                errors.append("review_round does not match the isolated role context.")
        if not agent_product:
            errors.append("agent_product is required for editorial_role_review_v2.")
        if not agent_version:
            errors.append("agent_version is required for editorial_role_review_v2.")
        if independence_mode not in {"same_host_isolated_context", "cross_host", "human", "unknown"}:
            errors.append(
                "independence_mode must be same_host_isolated_context, cross_host, human, or unknown."
            )
        if confidence is None:
            errors.append("confidence must be a number from 0 to 1.")
    else:
        warnings.append(
            "editorial_role_review_v1 accepted for compatibility; independence evidence is unknown."
        )
        reviewer_instance_id = reviewer_instance_id or str(context.get("reviewer_instance_id") or "legacy-v1")
        submitted_context_hash = submitted_context_hash or str(context.get("context_digest_hash") or "")
        independence_mode = independence_mode or "unknown"
        review_round = review_round or int(context.get("review_round") or 1)
        confidence = confidence if confidence is not None else 0.0
    normalized = {
        "schema_version": 2,
        "chapter_number": chapter_number,
        "role_id": role_id,
        "verdict": verdict,
        "items": items,
        "summary": str(payload.get("summary") or ""),
        "reviewer_instance_id": reviewer_instance_id,
        "agent_product": agent_product or "legacy-v1",
        "agent_version": agent_version or "unknown",
        "context_digest_hash": submitted_context_hash,
        "independence_mode": independence_mode,
        "review_round": review_round,
        "confidence": confidence,
        "evidence_grade": "declared_independent_context" if schema_version == 2 else "legacy_unknown",
        "source_result_file": relative_path(root, result_file),
        "validated_at": utc_now(),
    }
    return errors, warnings, normalized


def resolve_editorial_result_path(root: Path, file_path: str | Path) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
    resolved = path.expanduser().resolve()
    result_dir = (review_root(root) / "results").resolve()
    try:
        resolved.relative_to(result_dir)
    except ValueError as exc:
        raise ValueError("editorial result file must live under 50_workbench/editorial_reviews/results/.") from exc
    return resolved


def load_editorial_context(root: Path, *, chapter_number: int, role_id: str) -> dict[str, Any]:
    path = (
        review_root(root)
        / "agent_tasks"
        / f"ch{chapter_number:03d}"
        / f"{role_id}.context.json"
    )
    payload = load_json(path, default={})
    if not isinstance(payload, dict) or payload.get("schema") != "editorial_context_isolation_v1":
        return {}
    return payload


def normalize_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return round(confidence, 4) if 0.0 <= confidence <= 1.0 else None


def editorial_validation_file(root: Path, chapter_number: int, role_id: str) -> Path:
    return review_root(root) / "results" / f"ch{chapter_number:03d}.{role_id}.validation.json"


def editorial_normalized_file(root: Path, chapter_number: int, role_id: str) -> Path:
    return review_root(root) / "results" / f"ch{chapter_number:03d}.{role_id}.normalized.json"


def normalize_verdict(value: Any) -> str:
    verdict = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return verdict if verdict in {"pass", "conditional_pass", "needs_revision", "rewrite", "blocked"} else ""


def normalize_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def normalize_evidence_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def repeated_conditional_pass(root: Path, chapter_number: int, verdicts: list[str]) -> bool:
    if verdicts.count("conditional_pass") >= 2:
        return True
    previous = load_json(review_root(root) / f"ch{chapter_number - 1:03d}.aggregate.json", default={})
    if isinstance(previous, dict) and previous.get("chapter_number") and "conditional_pass" in verdicts:
        previous_items = previous.get("unresolved_items") if isinstance(previous.get("unresolved_items"), list) else []
        if previous_items:
            return True
    return False


def duplicate_editorial_role_results(root: Path, chapter_number: int) -> list[dict[str, Any]]:
    """Find multiple raw Agent result files that claim the same editorial role."""

    result_dir = review_root(root) / "results"
    by_role: dict[str, list[str]] = {}
    for path in sorted(result_dir.glob(f"ch{chapter_number:03d}.*.json")):
        if path.name.endswith((".normalized.json", ".validation.json")):
            continue
        role_id = editorial_role_from_result_file(root, path)
        by_role.setdefault(role_id, []).append(relative_path(root, path))
    return [
        {
            "role_id": role_id,
            "files": files,
            "count": len(files),
        }
        for role_id, files in sorted(by_role.items())
        if len(files) > 1
    ]


def invalid_editorial_results(root: Path, chapter_number: int) -> list[dict[str, Any]]:
    """Return rejected role result validation reports for aggregate visibility."""

    invalid: list[dict[str, Any]] = []
    result_dir = review_root(root) / "results"
    for path in sorted(result_dir.glob(f"ch{chapter_number:03d}.*.validation.json")):
        payload = load_json(path, default={})
        if not isinstance(payload, dict) or payload.get("accepted") is not False:
            continue
        invalid.append(
            {
                "role_id": str(payload.get("role_id") or editorial_role_from_result_file(root, path)),
                "validation_file": relative_path(root, path),
                "result_file": str(payload.get("result_file") or ""),
                "errors": [str(error) for error in payload.get("errors") or []],
            }
        )
    return invalid


def editorial_role_from_result_file(root: Path, path: Path) -> str:
    payload = load_json(path, default={})
    if isinstance(payload, dict) and str(payload.get("role_id") or "").strip():
        return role_definition(str(payload.get("role_id")))["id"]
    name = path.name
    match = re.match(r"^ch\d{3}\.(.+)$", name)
    if match:
        name = match.group(1)
    for suffix in (".validation.json", ".normalized.json", ".json"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return role_definition(name.split(".", 1)[0])["id"]


def editorial_finalization_blockers(config: ConfigDocument, *, chapter_number: int) -> list[str]:
    """Return reasons that an existing editorial aggregate blocks finalization."""

    root = resolve_project_root(config)
    aggregate_file = review_root(root) / f"ch{chapter_number:03d}.aggregate.json"
    if not aggregate_file.exists():
        return (
            ["editorial_review_missing"]
            if editorial_review_required_reasons(config, chapter_number=chapter_number)
            else []
        )
    payload = load_json(aggregate_file, default={})
    if not isinstance(payload, dict):
        return ["invalid_editorial_aggregate"]
    counts = payload.get("severity_counts") if isinstance(payload.get("severity_counts"), dict) else {}
    reasons = [str(reason) for reason in payload.get("need_human_reasons") or [] if str(reason).strip()]
    if int(counts.get("P0") or 0) > 0 and "unresolved_P0" not in reasons:
        reasons.append("unresolved_P0")
    if int(counts.get("P1") or 0) > 0 and "unresolved_P1" not in reasons:
        reasons.append("unresolved_P1")
    if payload.get("need_human") is True and not reasons:
        reasons.append("editorial_need_human")
    return dedupe(reasons)


def format_editorial_aggregate_markdown(payload: dict[str, Any]) -> str:
    counts = payload.get("severity_counts") or {}
    lines = [
        f"# Editorial Aggregate ch{int(payload.get('chapter_number') or 0):03d}",
        "",
        f"- Result count: {payload.get('result_count', 0)}",
        f"- Severity counts: P0={counts.get('P0', 0)}, P1={counts.get('P1', 0)}, P2={counts.get('P2', 0)}",
        f"- Conditional passes: {payload.get('conditional_passes', 0)}",
        f"- Need human: {payload.get('need_human')}",
        f"- Next command: `{payload.get('next_command')}`",
        "",
        "## Need-Human Reasons",
        "",
    ]
    reasons = payload.get("need_human_reasons") if isinstance(payload.get("need_human_reasons"), list) else []
    lines.extend([f"- {reason}" for reason in reasons] or ["- None"])
    lines.extend(["", "## Unresolved Items", ""])
    items = payload.get("unresolved_items") if isinstance(payload.get("unresolved_items"), list) else []
    for item in items:
        if isinstance(item, dict):
            lines.append(f"- [{item.get('severity')}] {item.get('role_id')} / {item.get('code')}: {item.get('message')}")
    if not items:
        lines.append("- None")
    lines.extend(["", "## Disagreement Matrix", ""])
    matrix = payload.get("disagreement_matrix") if isinstance(payload.get("disagreement_matrix"), list) else []
    for row in matrix:
        if not isinstance(row, dict):
            continue
        lines.append(
            f"- `{row.get('issue_code')}` roles={','.join(row.get('roles') or []) or 'none'} "
            f"overlap={row.get('evidence_span_overlap')} "
            f"severity_conflict={row.get('severity_conflict')} "
            f"minority_P0_P1={row.get('minority_P0_P1')}"
        )
    if not matrix:
        lines.append("- None")
    lines.extend(["", "## Human Decisions", ""])
    decisions = payload.get("human_decisions") if isinstance(payload.get("human_decisions"), list) else []
    for decision in decisions:
        if isinstance(decision, dict):
            lines.append(
                f"- `{decision.get('issue_code')}`: {decision.get('reason')} "
                f"({', '.join(decision.get('roles') or [])})"
            )
    if not decisions:
        lines.append("- None")
    lines.extend(["", "## Team Completeness", ""])
    missing_roles = payload.get("missing_roles") if isinstance(payload.get("missing_roles"), list) else []
    lines.extend([f"- Missing role: `{role}`" for role in missing_roles] or ["- Missing roles: None"])
    duplicate_results = payload.get("duplicate_role_results") if isinstance(payload.get("duplicate_role_results"), list) else []
    lines.extend(["", "## Duplicate Role Results", ""])
    for duplicate in duplicate_results:
        if isinstance(duplicate, dict):
            files = ", ".join(f"`{file}`" for file in duplicate.get("files") or [])
            lines.append(f"- `{duplicate.get('role_id')}`: {files}")
    if not duplicate_results:
        lines.append("- None")
    invalid_results = payload.get("invalid_results") if isinstance(payload.get("invalid_results"), list) else []
    lines.extend(["", "## Invalid Role Results", ""])
    for invalid in invalid_results:
        if isinstance(invalid, dict):
            errors = "; ".join(str(error) for error in invalid.get("errors") or [])
            lines.append(f"- `{invalid.get('role_id')}`: `{invalid.get('validation_file')}` {errors}")
    if not invalid_results:
        lines.append("- None")
    lines.extend(["", "Aggregate is advisory. Only chapter finalize may mutate canonical final/RAG/graph/SQLite.", ""])
    return "\n".join(lines)


def load_reviews(root: Path) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for path in sorted(review_root(root).glob("ch*.review.json")):
        payload = load_json(path, default={})
        if isinstance(payload, dict):
            reviews.append(payload)
    return sorted(reviews, key=lambda review: (int(review.get("chapter_number") or 0), int(review.get("review_round") or 1)))


def reviews_in_range(root: Path, chapter_start: int, chapter_end: int) -> list[dict[str, Any]]:
    return [
        review
        for review in load_reviews(root)
        if chapter_start <= int(review.get("chapter_number") or 0) <= chapter_end
    ]


def reviews_with_current(root: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    chapter_number = int(payload.get("chapter_number") or 0)
    reviews = [review for review in load_reviews(root) if int(review.get("chapter_number") or 0) != chapter_number]
    reviews.append(payload)
    return sorted(reviews, key=lambda review: (int(review.get("chapter_number") or 0), int(review.get("review_round") or 1)))


def editorial_team(
    config: ConfigDocument,
    *,
    root: Path,
    chapter_number: int,
    deterministic_items: list[dict[str, Any]],
    risk_signals: list[str],
) -> list[dict[str, str]]:
    """Select only roles justified by current chapter risk."""

    configured = configured_editorial_roles(config)
    if configured:
        return [role_definition(role) for role in configured]

    selected: set[str] = set()
    payoff_file = (
        root
        / "50_workbench"
        / "quality_reviews"
        / f"ch{chapter_number:03d}.reader_payoff.normalized.json"
    )
    fanfiction_mode = str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
    selected.add(
        "reader_quality_reviewer"
        if payoff_file.exists() or fanfiction_mode
        else "writing_agent"
    )
    selected.update(
        str(item.get("role_id") or "")
        for item in deterministic_items
        if str(item.get("role_id") or "")
    )
    signals = set(risk_signals)
    if "ai_flavor_recurrence" in signals:
        selected.add("anti_ai_editor")
    if "continuity_or_relationship_risk" in signals:
        selected.add("serial_verifier")
    if signals & {"volume_boundary", "major_payoff_or_reveal"}:
        selected.update({"planning_chief_editor", "reader_quality_reviewer"})
    if "fanfiction_canon_risk" in signals:
        selected.add("canon_fidelity_reviewer")
    if "blocking_P0_P1_risk" in signals:
        selected.add("executive_editor")
    ordered = [dict(role) for role in DEFAULT_EDITORIAL_TEAM if role["id"] in selected]
    custom = sorted(selected - {role["id"] for role in DEFAULT_EDITORIAL_TEAM})
    ordered.extend(role_definition(role) for role in custom)
    return ordered


def editorial_review_required_reasons(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> list[str]:
    """Return deterministic reasons that require a pre-finalize editorial review."""

    if chapter_number <= 0:
        return []
    root = resolve_project_root(config)
    chapter_path = find_chapter(root, chapter_number)
    if chapter_path is None:
        return []
    editorial_config = config.data.get("editorial")
    if not isinstance(editorial_config, dict):
        editorial_config = {}
    review_mode = str(editorial_config.get("review_mode") or "risk_based")
    if review_mode == "off":
        return []

    reasons: list[str] = []
    quality = config.data.get("quality")
    if not isinstance(quality, dict):
        quality = {}
    if review_mode == "always" or str(quality.get("assurance_mode") or "") == "strict":
        reasons.append("strict_assurance")
    milestones = {
        int(item)
        for item in quality.get("semantic_review_milestones", [])
        if isinstance(item, int) and not isinstance(item, bool)
    }
    if chapter_number in milestones:
        reasons.append("quality_milestone")

    _status, deterministic_items = deterministic_editorial_items(safe_read_text(chapter_path))
    reasons.extend(
        signal
        for signal in editorial_risk_signals(
            config,
            root=root,
            chapter_number=chapter_number,
            deterministic_items=deterministic_items,
        )
        if signal != "normal_chapter"
    )
    return dedupe(reasons)


def configured_editorial_roles(config: ConfigDocument) -> list[str]:
    roles = config.data.get("editorial", {}).get("review_roles")
    if not isinstance(roles, list):
        return []
    return [str(role).strip() for role in roles if str(role).strip()]


def expected_editorial_roles(
    config: ConfigDocument,
    *,
    root: Path,
    chapter_number: int,
) -> list[str]:
    review = load_json(review_root(root) / f"ch{chapter_number:03d}.review.json", default={})
    team = review.get("editorial_team") if isinstance(review, dict) else None
    if isinstance(team, list) and team:
        return [
            role_definition(str(role.get("id") or ""))["id"]
            for role in team
            if isinstance(role, dict) and str(role.get("id") or "").strip()
        ]
    configured = configured_editorial_roles(config)
    return [role_definition(role)["id"] for role in configured]


def editorial_risk_signals(
    config: ConfigDocument,
    *,
    root: Path,
    chapter_number: int,
    deterministic_items: list[dict[str, Any]],
) -> list[str]:
    signals: list[str] = []
    issue_codes = {str(item.get("code") or "") for item in deterministic_items}
    roles = {str(item.get("role_id") or "") for item in deterministic_items}
    severities = {str(item.get("severity") or "") for item in deterministic_items}
    if "anti_ai_editor" in roles or any("ai_" in code or "repetition" in code for code in issue_codes):
        signals.append("ai_flavor_recurrence")
    if "serial_verifier" in roles or any(
        token in code
        for code in issue_codes
        for token in ("continuity", "relationship", "timeline", "logic")
    ):
        signals.append("continuity_or_relationship_risk")
    if severities & {"P0", "P1"}:
        signals.append("blocking_P0_P1_risk")

    card = load_json(
        root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
        default={},
    )
    if isinstance(card, dict):
        duty_text = " ".join(
            str(card.get(key) or "")
            for key in ("chapter_duty", "duty", "reader_gain", "reader_payoff", "ending_mode")
        ).lower()
        if any(
            token in duty_text
            for token in (
                "揭露",
                "兑现",
                "真相",
                "闭环",
                "阶段性结案",
                "调查权限",
                "账册入口",
                "payoff",
                "reveal",
                "关系转折",
            )
        ):
            signals.append("major_payoff_or_reveal")
        if bool(card.get("volume_boundary")) or str(card.get("event_tier") or "").upper() == "A":
            signals.append("volume_boundary")

    if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction":
        signals.append("fanfiction_canon_risk")

    registry = root / "50_workbench" / "quality_feedback" / "registry.jsonl"
    if registry.exists():
        try:
            feedback_items = [
                json.loads(line)
                for line in registry.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except (OSError, json.JSONDecodeError):
            feedback_items = []
        active = [
            item
            for item in feedback_items
            if isinstance(item, dict) and item.get("status") in {"open", "carried"}
        ]
        if any(
            token in str(item.get("issue_code") or "")
            for item in active
            for token in ("ai_", "dialogue", "repetition", "formula")
        ):
            signals.append("ai_flavor_recurrence")
        if any(
            token in str(item.get("issue_code") or "")
            for item in active
            for token in ("continuity", "relationship", "timeline", "logic")
        ):
            signals.append("continuity_or_relationship_risk")
    return dedupe(signals) or ["normal_chapter"]


def role_definition(role_name: str) -> dict[str, str]:
    role_id = sanitize_role_id(role_name)
    role_id = ROLE_ALIASES.get(role_id, role_id)
    for role in DEFAULT_EDITORIAL_TEAM:
        if role["id"] == role_id:
            return dict(role)
    return {
        "id": role_id,
        "display_name": role_name.strip(),
        "focus": "custom editorial role configured by project.yaml",
    }


def sanitize_role_id(role_name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", role_name.strip().lower().replace("-", "_").replace(" ", "_")).strip("_") or "reviewer"


def severity_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for item in items:
        severity = str(item.get("severity") or "")
        if severity in counts:
            counts[severity] += 1
    return counts


def aggregate_severity_counts(reviews: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for review in reviews:
        for item in review.get("items") or []:
            if not isinstance(item, dict) or item.get("status") == "resolved":
                continue
            severity = str(item.get("severity") or "")
            if severity in counts:
                counts[severity] += 1
    return counts


def unresolved_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item.get("status") != "resolved"]


def conditional_limit(config: ConfigDocument) -> int:
    raw = config.data.get("editorial", {}).get("conditional_pass_limit")
    try:
        return max(1, int(raw or 3))
    except (TypeError, ValueError):
        return 3


def conditional_pass_streak(reviews: list[dict[str, Any]]) -> int:
    streak = 0
    for review in reversed(sorted(reviews, key=lambda item: (int(item.get("chapter_number") or 0), int(item.get("review_round") or 1)))):
        if review.get("status") != "conditional_pass":
            break
        streak += 1
    return streak


def need_human_reasons_for_review(
    config: ConfigDocument,
    *,
    status: str,
    items: list[dict[str, Any]],
    conditional_streak: int,
) -> list[str]:
    reasons: list[str] = []
    counts = severity_counts(unresolved_items(items))
    if status == "needs_revision":
        reasons.append("status_needs_revision")
    if counts["P0"]:
        reasons.append("unresolved_P0")
    if counts["P1"]:
        reasons.append("unresolved_P1")
    limit = conditional_limit(config)
    if conditional_streak >= limit:
        reasons.append(f"conditional_pass_streak:{conditional_streak}>={limit}")
    return reasons


def need_human_reasons_for_status(
    config: ConfigDocument,
    *,
    reviews: list[dict[str, Any]],
    conditional_streak: int,
) -> list[str]:
    counts = aggregate_severity_counts(reviews)
    reasons: list[str] = []
    if counts["P0"]:
        reasons.append("unresolved_P0")
    if counts["P1"]:
        reasons.append("unresolved_P1")
    if any(review.get("status") == "needs_revision" for review in reviews):
        reasons.append("status_needs_revision")
    limit = conditional_limit(config)
    if conditional_streak >= limit:
        reasons.append(f"conditional_pass_streak:{conditional_streak}>={limit}")
    return dedupe(reasons)


def batch_need_human_reasons(
    config: ConfigDocument,
    *,
    results: list[EditorialReviewResult],
    findings: list[dict[str, Any]],
    conditional_streak: int,
) -> list[str]:
    reasons: list[str] = []
    for result in results:
        reasons.extend(result.need_human_reasons)
    if any(item.get("severity") in {"P0", "P1"} for item in findings):
        reasons.append("batch_blocking_findings")
    limit = conditional_limit(config)
    if conditional_streak >= limit:
        reasons.append(f"conditional_pass_streak:{conditional_streak}>={limit}")
    return dedupe(reasons)


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def dedupe_paths(items: Any) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in items:
        key = str(Path(path).resolve())
        if key in seen:
            continue
        seen.add(key)
        result.append(Path(path))
    return result


def count_item_code(reviews: list[dict[str, Any]], code: str) -> int:
    total = 0
    for review in reviews:
        for item in review.get("items") or []:
            if isinstance(item, dict) and item.get("code") == code and item.get("status") != "resolved":
                total += 1
    return total


def duplicate_sentence_ratio(text: str) -> float:
    sentences = [part.strip() for part in re.split(r"[。！？!?]+", text) if part.strip()]
    if not sentences:
        return 0.0
    return 1 - (len(set(sentences)) / len(sentences))


def find_chapter(root: Path, chapter_number: int) -> Path | None:
    for lane in ("final", "draft"):
        directory = root / "40_manuscript" / lane
        for name in (f"ch{chapter_number:03d}.md", f"chapter_{chapter_number:03d}.md", f"{chapter_number}.md"):
            path = directory / name
            if path.exists():
                return path
    return None


def review_root(root: Path) -> Path:
    return root / "50_workbench" / "editorial_reviews"


def load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


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
