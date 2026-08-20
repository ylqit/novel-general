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


SCHEMA = "human_story_review_v2"
DECISIONS = {"accept", "repair", "redirect"}
SPAN_ACTIONS = {"preserve", "expand_scene", "compress", "replace_carrier"}
CHECK_FIELDS = {
    "protected_outcome_preserved",
    "desire_and_opposition_clear",
    "key_turn_dramatized",
    "character_owns_choice_and_emotion",
    "no_expository_or_repeated_carrier",
}
EVIDENCE_KINDS = {"key_turn", "character_choice_or_emotion"}


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
        }
    decision_path = resolve_decision_pointer(root, chapter_number, latest)
    decision_record = load_json(decision_path) if decision_path is not None else None
    if (
        not isinstance(decision_record, dict)
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
    ):
        return {
            "required": True,
            "status": "stale",
            "candidate_sha256": candidate_hash,
            "chapter_contract_sha256": contract_hash,
            "reader_promise_ledger_sha256": promise_hash,
            "arc_causal_simulation_sha256": simulation_hash,
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
        "decision_file": relative_path(root, decision_path),
        "redirect_scope": str(decision_record.get("redirect_scope") or ""),
        "span_actions": (
            decision_record.get("span_actions")
            if isinstance(decision_record.get("span_actions"), list)
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
    task_path = review_root(root) / f"ch{chapter_number:03d}.{candidate_hash[:12]}.task.md"
    template_path = review_root(root) / f"ch{chapter_number:03d}.{candidate_hash[:12]}.candidate.json"
    story_brief = root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.md"
    lines = [
        f"# ch{chapter_number:03d} 人工故事简审",
        "",
        f"- 正文：`{relative_path(root, draft)}`",
        f"- 故事工作单：`{relative_path(root, story_brief)}`",
        "",
        "逐项给出 passed 与 reason：批准结果是否保持；欲望与阻力是否清楚；关键转折是否场景化；",
        "人物是否拥有自己的选择与情绪；是否说明文化、会议化或重复载体；最后选择 accept、repair 或 redirect。",
        "accept 还必须标注 key_turn 与 character_choice_or_emotion 两类正文证据，并填写 reader_gain_note。",
        "repair 可用 span 动作为 preserve、expand_scene、compress、replace_carrier。",
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
        "checks": {
            field: {"passed": False, "reason": ""}
            for field in sorted(CHECK_FIELDS)
        },
        "decision": "repair",
        "evidence_spans": [],
        "reader_gain_note": "",
        "span_actions": [],
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
    errors = human_story_review_errors(root, chapter_number, payload)
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
        "schema": "human_story_review_validation_v2",
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
    errors = human_story_review_errors(root, chapter_number, payload)
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
        "schema": "human_story_review_latest_v2",
        "chapter_number": chapter_number,
        "candidate_sha256": candidate_hash,
        "chapter_contract_sha256": payload["chapter_contract_sha256"],
        "reader_promise_ledger_sha256": payload["reader_promise_ledger_sha256"],
        "arc_causal_simulation_sha256": payload["arc_causal_simulation_sha256"],
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


def human_story_review_errors(root: Path, chapter_number: int, payload: Any) -> list[str]:
    errors: list[str] = []
    required = {
        "schema", "chapter_number", "candidate_sha256", "chapter_contract_sha256",
        "reader_promise_ledger_sha256", "arc_causal_simulation_sha256",
        "checks", "decision", "evidence_spans", "reader_gain_note", "span_actions",
        "redirect_scope", "reason",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        return ["review must contain exactly the human_story_review_v2 fields"]
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
    checks = payload.get("checks")
    if not isinstance(checks, dict) or set(checks) != CHECK_FIELDS:
        errors.append("checks must contain exactly five reasoned story judgments")
        checks = {}
    for field in CHECK_FIELDS:
        check = checks.get(field) if isinstance(checks, dict) else None
        if (
            not isinstance(check, dict)
            or set(check) != {"passed", "reason"}
            or not isinstance(check.get("passed"), bool)
            or not isinstance(check.get("reason"), str)
            or not check["reason"].strip()
        ):
            errors.append(f"checks.{field} must contain passed and a non-empty reason")
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
    spans = payload.get("span_actions")
    if not isinstance(spans, list):
        errors.append("span_actions must be a list")
        spans = []
    for index, item in enumerate(spans):
        if not isinstance(item, dict) or set(item) != {"start", "end", "text", "action", "note"}:
            errors.append(f"span_actions[{index}] has invalid fields")
            continue
        start, end = item.get("start"), item.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(draft_text):
            errors.append(f"span_actions[{index}] has invalid bounds")
        elif item.get("text") != draft_text[start:end]:
            errors.append(f"span_actions[{index}].text does not match the current candidate")
        if item.get("action") not in SPAN_ACTIONS:
            errors.append(f"span_actions[{index}].action is unsupported")
        if not isinstance(item.get("note"), str) or not item["note"].strip():
            errors.append(f"span_actions[{index}].note must be non-empty")
    if decision == "accept" and (
        not isinstance(checks, dict)
        or any(not isinstance(checks.get(field), dict) or checks[field].get("passed") is not True for field in CHECK_FIELDS)
    ):
        errors.append("accept requires all five story checks to pass")
    if decision == "accept" and EVIDENCE_KINDS - evidence_kinds:
        errors.append("accept requires key_turn and character_choice_or_emotion evidence spans")
    if decision == "accept" and not str(payload.get("reader_gain_note") or "").strip():
        errors.append("accept requires a non-empty reader_gain_note")
    if decision == "repair" and not spans:
        errors.append("repair requires at least one marked span")
    elif decision == "repair" and not any(
        isinstance(item, dict) and item.get("action") != "preserve" for item in spans
    ):
        errors.append("repair requires at least one non-preserve span action")
    if payload.get("redirect_scope") not in {"direction", "outline_revision"}:
        errors.append("redirect_scope must be direction or outline_revision")
    if decision == "redirect" and not str(payload.get("reason") or "").strip():
        errors.append("redirect requires a non-empty reason")
    return errors


def review_root(root: Path) -> Path:
    return root / "50_workbench" / "human_story_reviews"


def resolve_decision_pointer(root: Path, chapter_number: int, latest: dict[str, Any]) -> Path | None:
    expected = {
        "schema", "chapter_number", "candidate_sha256", "chapter_contract_sha256",
        "reader_promise_ledger_sha256", "arc_causal_simulation_sha256",
        "decision_file", "decision_sha256",
    }
    if (
        set(latest) != expected
        or latest.get("schema") != "human_story_review_latest_v2"
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
