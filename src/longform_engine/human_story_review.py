"""Hash-bound human story review between editorial completion and finalization."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from longform_engine.chapter_contract import load_verified_chapter_contract
from longform_engine.arc_simulation import SIMULATION_DIR, load_active_arc_simulation, mark_overlapping_arc_simulations_stale
from longform_engine.config import ConfigDocument
from longform_engine.quality import truncate_editorial_pattern_registry
from longform_engine.reader_promises import reader_promise_ledger_hash
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


SCHEMA = "human_story_review_v4"
DECISIONS = {"accept", "repair", "redirect"}
ANNOTATION_ACTIONS = {
    "preserve",
    "expand_scene",
    "compress",
    "clarify",
    "reorder",
    "rewrite",
    "replace_carrier",
}
ANNOTATION_SEVERITIES = {"P0", "P1", "P2"}
CHECK_FIELDS = {
    "story_contract_preserved",
    "desire_opposition_and_question_clear",
    "scene_causality_and_key_turn_dramatized",
    "protagonist_agency_voice_and_emotion",
    "supporting_cast_and_relationship_logic",
    "reader_gain_and_promise_progress",
    "continuity_world_rules_and_ability_bounds",
    "pacing_information_and_carrier_effective",
    "prose_natural_and_readable",
    "exit_state_and_emotional_aftereffect",
}
EVIDENCE_KINDS = {"key_turn", "character_choice_or_emotion", "reader_gain"}
COVERAGE_STATUSES = {"confirmed", "covered", "accepted_p2", "repair", "redirect"}
COVERAGE_SOURCES = {"human_core", "independent_review", "human_resolution"}
FINDING_DISPOSITIONS = {"accept_p2", "repair", "redirect"}
CORE_CHECK_EVIDENCE = {
    "scene_causality_and_key_turn_dramatized": "key_turn",
    "protagonist_agency_voice_and_emotion": "character_choice_or_emotion",
    "reader_gain_and_promise_progress": "reader_gain",
    "exit_state_and_emotional_aftereffect": "reader_gain",
}


class HumanStoryReviewError(ValueError):
    """Raised when human story evidence is missing, stale, or malformed."""


@dataclass(frozen=True)
class HumanStoryReviewTaskResult:
    chapter_number: int
    task_file: str
    template_file: str
    candidate_sha256: str
    chapter_contract_sha256: str
    reader_promise_ledger_sha256: str
    arc_causal_simulation_sha256: str
    review_bundle_file: str
    review_bundle_sha256: str
    human_author_revision_sha256: str
    next_command: str


@dataclass(frozen=True)
class HumanStoryReviewValidateResult:
    chapter_number: int
    ok: bool
    decision: str
    report_file: str
    errors: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class HumanStoryReviewApplyResult:
    chapter_number: int
    decision: str
    decision_file: str
    next_command: str
    transaction_report: str


def human_story_review_status(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    root = resolve_project_root(config)
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft.is_file():
        return {"required": True, "status": "pending", "reason": "current draft is missing"}
    candidate_hash = sha256(draft.read_bytes()).hexdigest()
    from longform_engine.human_author_revision import human_author_revision_status

    revision = human_author_revision_status(config, chapter_number=chapter_number)
    if revision.get("status") != "complete":
        return {
            "required": True,
            "status": "pending",
            "reason": "current candidate has no validated human_author_revision_v1 binding",
            "candidate_sha256": candidate_hash,
            "human_author_revision_status": revision.get("status") or "pending",
        }
    revision_hash = str(revision.get("validation_sha256") or "")
    try:
        _contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    except ValueError as exc:
        return {"required": True, "status": "pending", "reason": str(exc)}
    try:
        promise_hash = reader_promise_ledger_hash(root)
        _simulation, _simulation_path, simulation_hash = load_active_arc_simulation(
            root, chapter_number=chapter_number
        )
    except ValueError as exc:
        return {"required": True, "status": "pending", "reason": str(exc)}
    from longform_engine.repair_coordination import human_review_bundle_binding

    try:
        bundle_binding = human_review_bundle_binding(
            config,
            chapter_number=chapter_number,
            freeze=False,
        )
    except ValueError as exc:
        return {"required": True, "status": "pending", "reason": str(exc)}
    review_bundle_hash = str(bundle_binding["review_bundle_sha256"])
    latest_path = review_root(root) / f"ch{chapter_number:03d}.latest.json"
    latest = load_json(latest_path)
    if not isinstance(latest, dict):
        return {
            "required": True,
            "status": "pending",
            "candidate_sha256": candidate_hash,
            "chapter_contract_sha256": contract_hash,
            "reader_promise_ledger_sha256": promise_hash,
            "arc_causal_simulation_sha256": simulation_hash,
            "review_bundle_sha256": review_bundle_hash,
            "human_author_revision_sha256": revision_hash,
        }
    decision_path = resolve_decision_pointer(root, chapter_number, latest)
    decision_record = load_json(decision_path) if decision_path is not None else None
    if (
        decision_path is None
        or not isinstance(decision_record, dict)
        or sha256(decision_path.read_bytes()).hexdigest() != str(latest.get("decision_sha256") or "")
        or decision_record.get("schema") != SCHEMA
        or decision_record.get("chapter_number") != chapter_number
        or decision_record.get("approved_by") != "human"
    ):
        return {
            "required": True,
            "status": "stale",
            "reason": "latest human story review does not reference an immutable decision",
            "candidate_sha256": candidate_hash,
            "chapter_contract_sha256": contract_hash,
        }
    if (
        str(latest.get("candidate_sha256") or "") != candidate_hash
        or str(latest.get("chapter_contract_sha256") or "") != contract_hash
        or str(decision_record.get("candidate_sha256") or "") != candidate_hash
        or str(decision_record.get("chapter_contract_sha256") or "") != contract_hash
        or str(latest.get("reader_promise_ledger_sha256") or "") != promise_hash
        or str(latest.get("arc_causal_simulation_sha256") or "") != simulation_hash
        or str(decision_record.get("reader_promise_ledger_sha256") or "") != promise_hash
        or str(decision_record.get("arc_causal_simulation_sha256") or "") != simulation_hash
        or str(latest.get("review_bundle_sha256") or "") != review_bundle_hash
        or str(decision_record.get("review_bundle_sha256") or "") != review_bundle_hash
        or str(latest.get("human_author_revision_sha256") or "") != revision_hash
        or str(decision_record.get("human_author_revision_sha256") or "") != revision_hash
        or not bundle_binding.get("frozen")
    ):
        return {
            "required": True,
            "status": "stale",
            "candidate_sha256": candidate_hash,
            "chapter_contract_sha256": contract_hash,
            "reader_promise_ledger_sha256": promise_hash,
            "arc_causal_simulation_sha256": simulation_hash,
            "review_bundle_sha256": review_bundle_hash,
            "human_author_revision_sha256": revision_hash,
            "decision_file": relative_path(root, decision_path),
        }
    decision = str(decision_record.get("decision") or "")
    return {
        "required": True,
        "status": decision if decision in DECISIONS else "pending",
        "decision": decision,
        "candidate_sha256": candidate_hash,
        "chapter_contract_sha256": contract_hash,
        "reader_promise_ledger_sha256": promise_hash,
        "arc_causal_simulation_sha256": simulation_hash,
        "review_bundle_sha256": review_bundle_hash,
        "human_author_revision_sha256": revision_hash,
        "decision_file": relative_path(root, decision_path),
        "decision_sha256": sha256(decision_path.read_bytes()).hexdigest(),
        "approved_by": "human",
        "dimension_coverage": (
            decision_record.get("dimension_coverage")
            if isinstance(decision_record.get("dimension_coverage"), dict)
            else {}
        ),
        "evidence_spans": (
            decision_record.get("evidence_spans")
            if isinstance(decision_record.get("evidence_spans"), list)
            else []
        ),
        "redirect_scope": str(decision_record.get("redirect_scope") or ""),
        "reader_gain_note": str(decision_record.get("reader_gain_note") or ""),
        "reason": str(decision_record.get("reason") or ""),
        "annotations": (
            decision_record.get("annotations")
            if isinstance(decision_record.get("annotations"), list)
            else []
        ),
        "finding_resolutions": (
            decision_record.get("finding_resolutions")
            if isinstance(decision_record.get("finding_resolutions"), list)
            else []
        ),
    }


def create_human_story_review_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> HumanStoryReviewTaskResult:
    root = resolve_project_root(config)
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft.is_file():
        raise HumanStoryReviewError("current chapter draft is missing")
    from longform_engine.human_author_revision import (
        HumanAuthorRevisionError,
        require_current_human_author_revision,
    )

    try:
        revision = require_current_human_author_revision(config, chapter_number=chapter_number)
    except HumanAuthorRevisionError as exc:
        raise HumanStoryReviewError(str(exc)) from exc
    revision_hash = str(revision["validation_sha256"])
    from longform_engine.repair_coordination import review_barrier_status

    barrier = review_barrier_status(config, chapter_number=chapter_number)
    if barrier.get("status") != "awaiting_human_story_review":
        reasons = "; ".join(str(item) for item in barrier.get("blockers") or [])
        raise HumanStoryReviewError(
            "human story review requires every independent review to be current"
            + (f": {reasons}" if reasons else f"; current review state is {barrier.get('status')}")
        )
    candidate_hash = sha256(draft.read_bytes()).hexdigest()
    _contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    promise_hash = reader_promise_ledger_hash(root)
    _simulation, _simulation_path, simulation_hash = load_active_arc_simulation(
        root, chapter_number=chapter_number
    )
    from longform_engine.repair_coordination import human_review_bundle_binding

    bundle_binding = human_review_bundle_binding(
        config,
        chapter_number=chapter_number,
        freeze=True,
    )
    review_bundle_hash = str(bundle_binding["review_bundle_sha256"])
    task_path = review_root(root) / f"ch{chapter_number:03d}.{candidate_hash[:12]}.task.md"
    template_path = review_root(root) / f"ch{chapter_number:03d}.{candidate_hash[:12]}.candidate.json"
    story_brief = root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.md"
    lines = [
        f"# ch{chapter_number:03d} 人工故事深审",
        "",
        f"- 正文：`{relative_path(root, draft)}`",
        f"- 故事工作单：`{relative_path(root, story_brief)}`",
        "",
        f"- 冻结审稿包：`{bundle_binding['review_bundle']}`",
        f"- 审稿包 SHA-256：`{review_bundle_hash}`",
        "",
        f"- 人工作者修订 SHA-256：`{revision_hash}`",
        "",
        "三组核心证据必须由人填写：故事因果/关键转折、人物选择/情绪、读者收益/离场状态。",
        "其余维度可引用冻结审稿包中的独立审稿覆盖；有 finding 时必须明确接受 P2、repair 或 redirect。",
        "不得自动生成或复用‘人工逐项确认通过’理由；accept 不允许遗留 P0/P1。",
        "repair 至少提供一个非 preserve 结构化批注；redirect 必须选择 direction 或 outline_revision。",
        "",
        f"填写：`{relative_path(root, template_path)}`",
        f"校验：`longform-engine chapter human-review-validate project.yaml --chapter {chapter_number} --file {relative_path(root, template_path)}`",
        "",
    ]
    template = {
        "schema": SCHEMA,
        "chapter_number": chapter_number,
        "candidate_sha256": candidate_hash,
        "chapter_contract_sha256": contract_hash,
        "reader_promise_ledger_sha256": promise_hash,
        "arc_causal_simulation_sha256": simulation_hash,
        "review_bundle_sha256": review_bundle_hash,
        "human_author_revision_sha256": revision_hash,
        "dimension_coverage": default_dimension_coverage(bundle_binding["payload"]),
        "decision": "repair",
        "evidence_spans": [],
        "reader_gain_note": "",
        "finding_resolutions": default_finding_resolutions(bundle_binding["payload"]),
        "annotations": [],
        "redirect_scope": "direction",
        "reason": "",
    }
    atomic_write_text(task_path, "\n".join(lines))
    write_json(template_path, template)
    return HumanStoryReviewTaskResult(
        chapter_number=chapter_number,
        task_file=relative_path(root, task_path),
        template_file=relative_path(root, template_path),
        candidate_sha256=candidate_hash,
        chapter_contract_sha256=contract_hash,
        reader_promise_ledger_sha256=promise_hash,
        arc_causal_simulation_sha256=simulation_hash,
        review_bundle_file=str(bundle_binding["review_bundle"]),
        review_bundle_sha256=review_bundle_hash,
        human_author_revision_sha256=revision_hash,
        next_command=(
            f"longform-engine chapter human-review-validate project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, template_path)}"
        ),
    )


def validate_human_story_review(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> HumanStoryReviewValidateResult:
    root = resolve_project_root(config)
    path = resolve_inside(root, file_path)
    payload = load_json(path)
    errors = human_story_review_errors(config, chapter_number, payload)
    from longform_engine.repair_coordination import review_barrier_status

    barrier = review_barrier_status(config, chapter_number=chapter_number)
    if barrier.get("status") != "awaiting_human_story_review":
        errors.append(
            "human story review is only valid after every independent review is current; "
            f"current review state is {barrier.get('status') or 'unknown'}"
        )
    decision = str(payload.get("decision") or "") if isinstance(payload, dict) else ""
    report_path = path.with_suffix(".validation.json")
    report = {
        "schema": "human_story_review_validation_v4",
        "chapter_number": chapter_number,
        "candidate_file": relative_path(root, path),
        "ok": not errors,
        "decision": decision,
        "errors": errors,
    }
    write_json(report_path, report)
    next_command = (
        f"longform-engine chapter human-review-apply project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, path)} --approved-by human"
        if not errors
        else f"longform-engine chapter human-review-task project.yaml --chapter {chapter_number}"
    )
    return HumanStoryReviewValidateResult(
        chapter_number=chapter_number,
        ok=not errors,
        decision=decision,
        report_file=relative_path(root, report_path),
        errors=tuple(errors),
        next_command=next_command,
    )


def apply_human_story_review(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
    approved_by: str,
) -> HumanStoryReviewApplyResult:
    root = resolve_project_root(config)
    path = resolve_inside(root, file_path)
    payload = load_json(path)
    errors = human_story_review_errors(config, chapter_number, payload)
    from longform_engine.repair_coordination import review_barrier_status

    barrier = review_barrier_status(config, chapter_number=chapter_number)
    if barrier.get("status") != "awaiting_human_story_review":
        errors.append(
            "human story review is only valid after every independent review is current; "
            f"current review state is {barrier.get('status') or 'unknown'}"
        )
    if errors:
        raise HumanStoryReviewError("invalid human story review: " + "; ".join(errors))
    approved_by = str(approved_by or "").strip()
    if approved_by != "human":
        raise HumanStoryReviewError("human story review apply requires approved_by=human")
    candidate_hash = str(payload["candidate_sha256"])
    decision_path = review_root(root) / f"ch{chapter_number:03d}.{candidate_hash[:12]}.decision.json"
    if decision_path.exists():
        raise HumanStoryReviewError("this candidate already has an immutable human story decision")
    latest_path = review_root(root) / f"ch{chapter_number:03d}.latest.json"
    record = {**payload, "approved_by": approved_by, "source_file": relative_path(root, path)}
    record_text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    latest = {
        "schema": "human_story_review_latest_v4",
        "chapter_number": chapter_number,
        "candidate_sha256": candidate_hash,
        "chapter_contract_sha256": payload["chapter_contract_sha256"],
        "reader_promise_ledger_sha256": payload["reader_promise_ledger_sha256"],
        "arc_causal_simulation_sha256": payload["arc_causal_simulation_sha256"],
        "review_bundle_sha256": payload["review_bundle_sha256"],
        "human_author_revision_sha256": payload["human_author_revision_sha256"],
        "decision_file": relative_path(root, decision_path),
        "decision_sha256": sha256(record_text.encode("utf-8")).hexdigest(),
    }
    decision = str(payload["decision"])
    transaction_report = ""
    if decision == "redirect":
        from longform_engine.agent_tasks import mark_tasks_for_chapter_type
        from longform_engine.db import sync_database
        from longform_engine.orchestration.pipeline import upsert_chapter_plan, write_chapter_card_artifacts

        card_path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
        card_md = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.md"
        plan_path = root / "20_outline" / "chapter_plan.json"
        card = load_json(card_path)
        if not isinstance(card, dict):
            raise HumanStoryReviewError("chapter card is missing")
        card["direction_selection"] = {
            "status": "outline_revision_required" if payload["redirect_scope"] == "outline_revision" else "required",
            "redirect_reason": str(payload["reason"]),
        }
        with apply_transaction(
            root,
            command="chapter human-review redirect",
            chapter_number=chapter_number,
            source_paths=[path],
            touched_paths=[
                card_path,
                card_md,
                plan_path,
                decision_path,
                latest_path,
                root / "70_runtime" / "agent_tasks",
                root / "70_runtime" / "db",
                root / "50_workbench" / "agent_tasks",
                root / "50_workbench" / "editorial_patterns" / "registry.jsonl",
                *sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")),
            ],
            metadata={
                "candidate_sha256": candidate_hash,
                "chapter_contract_sha256": payload["chapter_contract_sha256"],
                "approved_by": approved_by,
                "redirect_scope": payload["redirect_scope"],
            },
        ) as transaction:
            write_json(decision_path, record)
            write_json(latest_path, latest)
            write_chapter_card_artifacts(root, card)
            upsert_chapter_plan(root, card)
            truncate_editorial_pattern_registry(root, to_chapter=chapter_number - 1)
            mark_overlapping_arc_simulations_stale(
                root, from_chapter=chapter_number, to_chapter=10**9
            )
            mark_tasks_for_chapter_type(
                root,
                chapter_number=chapter_number,
                task_types=(
                    "chapter_write", "semantic_review", "pacing_review", "reader_payoff_review",
                    "editorial_review", "repair", "repair_plan_synthesis",
                ),
                to_status="superseded",
                command="chapter human-review redirect",
                artifact=decision_path,
            )
            sync_database(config)
        transaction_report = relative_path(root, transaction.report_file)
    else:
        write_json(decision_path, record)
        write_json(latest_path, latest)
    if decision == "accept":
        next_command = f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human"
    elif decision == "repair":
        next_command = f"longform-engine repair synthesis-task project.yaml --chapter {chapter_number}"
    elif payload["redirect_scope"] == "outline_revision":
        next_command = f"longform-engine intelligence task project.yaml --task-type outline_revision --from-chapter {chapter_number} --to-chapter {chapter_number}"
    else:
        next_command = (
            "longform-engine intelligence task project.yaml "
            f"--task-type chapter_direction --chapter {chapter_number}"
        )
    return HumanStoryReviewApplyResult(
        chapter_number=chapter_number,
        decision=decision,
        decision_file=relative_path(root, decision_path),
        next_command=next_command,
        transaction_report=transaction_report,
    )


def require_human_story_accept(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    status = human_story_review_status(config, chapter_number=chapter_number)
    if status.get("status") != "accept":
        raise HumanStoryReviewError(
            f"chapter ch{chapter_number:03d} requires a current hash-bound human story accept decision"
        )
    return status


def human_story_review_errors(
    config: ConfigDocument,
    chapter_number: int,
    payload: Any,
) -> list[str]:
    root = resolve_project_root(config)
    errors: list[str] = []
    required = {
        "schema", "chapter_number", "candidate_sha256", "chapter_contract_sha256",
        "reader_promise_ledger_sha256", "arc_causal_simulation_sha256",
        "review_bundle_sha256", "human_author_revision_sha256", "dimension_coverage",
        "decision", "evidence_spans", "reader_gain_note", "finding_resolutions",
        "annotations", "redirect_scope", "reason",
    }
    if isinstance(payload, dict) and payload.get("schema") == "human_story_review_v3":
        return [
            "human_story_review_v3 is rejected in v0.7; create a new v0.7 project and import authoritative material manually"
        ]
    if not isinstance(payload, dict) or set(payload) != required:
        return ["review must contain exactly the human_story_review_v4 fields"]
    if payload.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if payload.get("chapter_number") != chapter_number:
        errors.append("chapter_number does not match the command")
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    draft_text = draft.read_text(encoding="utf-8") if draft.is_file() else ""
    candidate_hash = sha256(draft.read_bytes()).hexdigest() if draft.is_file() else ""
    if payload.get("candidate_sha256") != candidate_hash:
        errors.append("candidate_sha256 is stale")
    try:
        _contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    except ValueError as exc:
        errors.append(str(exc))
        contract_hash = ""
    if payload.get("chapter_contract_sha256") != contract_hash:
        errors.append("chapter_contract_sha256 is stale")
    if payload.get("reader_promise_ledger_sha256") != reader_promise_ledger_hash(root):
        errors.append("reader_promise_ledger_sha256 is stale")
    try:
        _simulation, _simulation_path, simulation_hash = load_active_arc_simulation(
            root, chapter_number=chapter_number
        )
    except ValueError as exc:
        errors.append(str(exc))
        simulation_hash = ""
    if payload.get("arc_causal_simulation_sha256") != simulation_hash:
        errors.append("arc_causal_simulation_sha256 is stale")
    from longform_engine.human_author_revision import human_author_revision_status

    revision = human_author_revision_status(config, chapter_number=chapter_number)
    if revision.get("status") != "complete":
        errors.append("current candidate has no validated human_author_revision_v1 binding")
    elif payload.get("human_author_revision_sha256") != revision.get("validation_sha256"):
        errors.append("human_author_revision_sha256 is stale")
    bundle_payload: dict[str, Any] = {}
    try:
        from longform_engine.repair_coordination import human_review_bundle_binding

        bundle_binding = human_review_bundle_binding(
            config,
            chapter_number=chapter_number,
            freeze=False,
        )
        if (
            payload.get("review_bundle_sha256") != bundle_binding["review_bundle_sha256"]
            or bundle_binding.get("actual_sha256") != bundle_binding["review_bundle_sha256"]
        ):
            errors.append("review_bundle_sha256 is stale")
        payload_value = bundle_binding.get("payload")
        if isinstance(payload_value, dict):
            bundle_payload = payload_value
    except ValueError as exc:
        errors.append(str(exc))
    coverage = payload.get("dimension_coverage")
    if not isinstance(coverage, dict) or set(coverage) != CHECK_FIELDS:
        errors.append("dimension_coverage must contain exactly the ten story dimensions")
        coverage = {}
    for field in CHECK_FIELDS:
        item = coverage.get(field) if isinstance(coverage, dict) else None
        if not isinstance(item, dict) or set(item) != {"status", "coverage_source", "reason", "evidence_refs"}:
            errors.append(f"dimension_coverage.{field} has invalid fields")
            continue
        if item.get("status") not in COVERAGE_STATUSES:
            errors.append(f"dimension_coverage.{field}.status is unsupported")
        if item.get("coverage_source") not in COVERAGE_SOURCES:
            errors.append(f"dimension_coverage.{field}.coverage_source is unsupported")
        refs = item.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
            errors.append(f"dimension_coverage.{field}.evidence_refs must contain explicit evidence")
        if item.get("coverage_source") in {"human_core", "human_resolution"} and (
            not isinstance(item.get("reason"), str) or not item["reason"].strip()
        ):
            errors.append(f"dimension_coverage.{field} requires a human-written reason")
        if field in CORE_CHECK_EVIDENCE and item.get("coverage_source") != "human_core":
            errors.append(f"dimension_coverage.{field} must be confirmed as human_core evidence")
    decision = str(payload.get("decision") or "")
    if decision not in DECISIONS:
        errors.append("decision must be accept, repair, or redirect")
    evidence_spans = payload.get("evidence_spans")
    if not isinstance(evidence_spans, list):
        errors.append("evidence_spans must be a list")
        evidence_spans = []
    evidence_kinds: set[str] = set()
    for index, item in enumerate(evidence_spans):
        if not isinstance(item, dict) or set(item) != {"start", "end", "text", "kind", "note"}:
            errors.append(f"evidence_spans[{index}] has invalid fields")
            continue
        start, end = item.get("start"), item.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(draft_text):
            errors.append(f"evidence_spans[{index}] has invalid bounds")
        elif item.get("text") != draft_text[start:end]:
            errors.append(f"evidence_spans[{index}].text does not match the current candidate")
        if item.get("kind") not in EVIDENCE_KINDS:
            errors.append(f"evidence_spans[{index}].kind is unsupported")
        else:
            evidence_kinds.add(str(item["kind"]))
        if not isinstance(item.get("note"), str) or not item["note"].strip():
            errors.append(f"evidence_spans[{index}].note must be non-empty")
    finding_resolutions = payload.get("finding_resolutions")
    if not isinstance(finding_resolutions, list):
        errors.append("finding_resolutions must be a list")
        finding_resolutions = []
    findings = [
        item
        for item in bundle_payload.get("findings") or []
        if isinstance(item, dict) and str(item.get("finding_id") or "")
    ]
    findings_by_id = {str(item["finding_id"]): item for item in findings}
    resolution_ids: set[str] = set()
    for index, item in enumerate(finding_resolutions):
        fields = {"finding_id", "severity", "disposition", "reason", "must_preserve"}
        if not isinstance(item, dict) or set(item) != fields:
            errors.append(f"finding_resolutions[{index}] has invalid fields")
            continue
        finding_id = str(item.get("finding_id") or "")
        if finding_id not in findings_by_id or finding_id in resolution_ids:
            errors.append(f"finding_resolutions[{index}].finding_id is unknown or duplicated")
            continue
        resolution_ids.add(finding_id)
        finding = findings_by_id[finding_id]
        severity = str(item.get("severity") or "")
        if severity != str(finding.get("severity") or ""):
            errors.append(f"finding_resolutions[{index}].severity does not match the frozen bundle")
        disposition = str(item.get("disposition") or "")
        if disposition not in FINDING_DISPOSITIONS:
            errors.append(f"finding_resolutions[{index}].disposition is unsupported")
        if disposition == "accept_p2" and severity != "P2":
            errors.append(f"finding_resolutions[{index}] may accept only P2")
        if not isinstance(item.get("reason"), str) or not item["reason"].strip():
            errors.append(f"finding_resolutions[{index}].reason must be human-written")
        preserve = item.get("must_preserve")
        if not isinstance(preserve, list) or any(
            not isinstance(value, str) or not value.strip() for value in preserve
        ):
            errors.append(f"finding_resolutions[{index}].must_preserve must be a string list")
    if set(findings_by_id) != resolution_ids:
        errors.append("every finding in the frozen review bundle requires an explicit human disposition")
    annotations = payload.get("annotations")
    if not isinstance(annotations, list):
        errors.append("annotations must be a list")
        annotations = []
    annotation_ids: set[str] = set()
    for index, item in enumerate(annotations):
        annotation_fields = {
            "annotation_id", "start", "end", "text", "check_id", "severity",
            "action", "intent", "must_preserve", "note",
        }
        if not isinstance(item, dict) or set(item) != annotation_fields:
            errors.append(f"annotations[{index}] has invalid fields")
            continue
        annotation_id = str(item.get("annotation_id") or "")
        if (
            not annotation_id
            or not all(character.isalnum() or character in {"_", "-"} for character in annotation_id)
            or annotation_id in annotation_ids
        ):
            errors.append(f"annotations[{index}].annotation_id must be unique and stable")
        annotation_ids.add(annotation_id)
        start, end = item.get("start"), item.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(draft_text):
            errors.append(f"annotations[{index}] has invalid bounds")
        elif item.get("text") != draft_text[start:end]:
            errors.append(f"annotations[{index}].text does not match the current candidate")
        if item.get("check_id") not in CHECK_FIELDS:
            errors.append(f"annotations[{index}].check_id is unsupported")
        if item.get("severity") not in ANNOTATION_SEVERITIES:
            errors.append(f"annotations[{index}].severity must be P0, P1, or P2")
        if item.get("action") not in ANNOTATION_ACTIONS:
            errors.append(f"annotations[{index}].action is unsupported")
        if not isinstance(item.get("intent"), str) or not item["intent"].strip():
            errors.append(f"annotations[{index}].intent must be non-empty")
        must_preserve = item.get("must_preserve")
        if not isinstance(must_preserve, list) or any(
            not isinstance(value, str) or not value.strip() for value in must_preserve
        ):
            errors.append(f"annotations[{index}].must_preserve must be a list of non-empty strings")
        if not isinstance(item.get("note"), str) or not item["note"].strip():
            errors.append(f"annotations[{index}].note must be non-empty")
    core_evidence_required = decision in {"accept", "repair"}
    if core_evidence_required and EVIDENCE_KINDS - evidence_kinds:
        errors.append("accept/repair requires human key_turn, character_choice_or_emotion, and reader_gain spans")
    if core_evidence_required:
        for field, kind in CORE_CHECK_EVIDENCE.items():
            item = coverage.get(field) if isinstance(coverage, dict) else None
            refs = item.get("evidence_refs") if isinstance(item, dict) else []
            if not any(str(ref).startswith(f"candidate:{kind}:") for ref in refs or []):
                errors.append(f"dimension_coverage.{field} must reference candidate:{kind}: evidence")
    acceptable = {"confirmed", "covered", "accepted_p2"}
    if decision == "accept" and any(
        not isinstance(coverage.get(field), dict)
        or coverage[field].get("status") not in acceptable
        for field in CHECK_FIELDS
    ):
        errors.append("accept requires all ten dimensions to be confirmed, covered, or explicitly accepted as P2")
    if decision == "accept" and any(
        isinstance(item, dict) and item.get("disposition") != "accept_p2"
        for item in finding_resolutions
    ):
        errors.append("accept requires every frozen finding to be an explicit P2 acceptance")
    if decision == "accept" and not str(payload.get("reader_gain_note") or "").strip():
        errors.append("accept requires a non-empty reader_gain_note")
    if decision == "accept" and any(
        isinstance(item, dict)
        and (item.get("severity") in {"P0", "P1"} or item.get("action") != "preserve")
        for item in annotations
    ):
        errors.append("accept cannot retain P0/P1 or non-preserve annotations")
    if decision == "repair" and not annotations:
        errors.append("repair requires at least one structured annotation")
    elif decision == "repair" and not any(
        isinstance(item, dict) and item.get("action") != "preserve" for item in annotations
    ):
        errors.append("repair requires at least one non-preserve annotation")
    if decision == "repair" and not any(
        isinstance(item, dict) and item.get("disposition") == "repair"
        for item in finding_resolutions
    ) and not any(
        isinstance(item, dict) and item.get("status") == "repair"
        for item in coverage.values()
    ):
        errors.append("repair must identify a repair disposition in finding or dimension coverage")
    if payload.get("redirect_scope") not in {"direction", "outline_revision"}:
        errors.append("redirect_scope must be direction or outline_revision")
    if decision == "redirect" and not str(payload.get("reason") or "").strip():
        errors.append("redirect requires a non-empty reason")
    if decision == "redirect" and not any(
        isinstance(item, dict) and item.get("disposition") == "redirect"
        for item in finding_resolutions
    ) and not any(
        isinstance(item, dict) and item.get("status") == "redirect"
        for item in coverage.values()
    ):
        errors.append("redirect must identify the redirected finding or story dimension")
    return errors


def default_dimension_coverage(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Pre-populate only machine-verifiable review coverage, never human pass reasons."""

    stage_refs = {
        "story_contract_preserved": "review_bundle#review_stages/semantic",
        "desire_opposition_and_question_clear": "review_bundle#review_stages/editorial",
        "supporting_cast_and_relationship_logic": "review_bundle#review_stages/editorial",
        "continuity_world_rules_and_ability_bounds": "review_bundle#review_stages/semantic",
        "pacing_information_and_carrier_effective": "review_bundle#review_stages/pacing",
        "prose_natural_and_readable": "review_bundle#review_stages/editorial",
    }
    coverage: dict[str, dict[str, Any]] = {}
    for field in sorted(CHECK_FIELDS):
        if field in CORE_CHECK_EVIDENCE:
            kind = CORE_CHECK_EVIDENCE[field]
            coverage[field] = {
                "status": "repair",
                "coverage_source": "human_core",
                "reason": "",
                "evidence_refs": [f"candidate:{kind}:ADD_SPAN_ID"],
            }
            continue
        ref = stage_refs.get(field, "review_bundle#review_stages/editorial")
        coverage[field] = {
            "status": "covered",
            "coverage_source": "independent_review",
            "reason": "",
            "evidence_refs": [ref],
        }
    return coverage


def default_finding_resolutions(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "finding_id": str(item.get("finding_id") or ""),
            "severity": str(item.get("severity") or ""),
            "disposition": "accept_p2" if item.get("severity") == "P2" else "repair",
            "reason": "",
            "must_preserve": [],
        }
        for item in bundle.get("findings") or []
        if isinstance(item, dict) and str(item.get("finding_id") or "")
    ]


def review_root(root: Path) -> Path:
    return root / "50_workbench" / "human_story_reviews"


def resolve_decision_pointer(root: Path, chapter_number: int, latest: dict[str, Any]) -> Path | None:
    expected = {
        "schema", "chapter_number", "candidate_sha256", "chapter_contract_sha256",
        "reader_promise_ledger_sha256", "arc_causal_simulation_sha256",
        "review_bundle_sha256", "human_author_revision_sha256",
        "decision_file", "decision_sha256",
    }
    if (
        set(latest) != expected
        or latest.get("schema") != "human_story_review_latest_v4"
        or latest.get("chapter_number") != chapter_number
    ):
        return None
    try:
        path = resolve_inside(root, str(latest.get("decision_file") or ""))
        path.relative_to(review_root(root).resolve())
    except (HumanStoryReviewError, ValueError):
        return None
    if path.name != f"ch{chapter_number:03d}.{str(latest.get('candidate_sha256') or '')[:12]}.decision.json":
        return None
    return path


def resolve_inside(root: Path, file_path: str | Path) -> Path:
    path = Path(file_path)
    path = path if path.is_absolute() else root / path
    path = path.resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise HumanStoryReviewError("review file must stay inside the project") from exc
    return path


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
