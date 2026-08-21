"""Evidence-bound, non-canonical consultation for the human story-review desk."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from longform_engine.agent_pipeline import (
    AgentProductionPipelineError,
    validate_production_agent_result,
)
from longform_engine.agent_protocols import AgentProtocolError, parse_design_document
from longform_engine.agent_tasks import (
    build_manifest,
    list_manifests,
    load_manifest,
    manifest_output,
    relative_path,
    update_task_status,
    validate_current_task_result,
    write_manifest,
)
from longform_engine.config import ConfigDocument
from longform_engine.repair_coordination import human_review_bundle_binding
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


SESSION_SCHEMA = "human_review_consult_session_v1"
REQUEST_SCHEMA = "human_review_consult_request_v1"
HISTORY_SCHEMA = "human_review_consult_history_v1"
VALIDATION_SCHEMA = "human_review_consult_validation_v1"
RECORD_SCHEMA = "human_review_consult_record_v1"


class HumanReviewConsultError(ValueError):
    """Raised when consultation evidence, lifecycle, or candidate bindings drift."""


@dataclass(frozen=True)
class HumanReviewConsultTaskResult:
    chapter_number: int
    task_id: str
    session_id: str
    turn_number: int
    manifest_file: str
    task_file: str
    request_file: str
    history_file: str
    response_file: str
    candidate_sha256: str
    review_bundle_sha256: str
    next_command: str


@dataclass(frozen=True)
class HumanReviewConsultValidateResult:
    chapter_number: int
    task_id: str
    ok: bool
    report_file: str
    errors: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class HumanReviewConsultRecordResult:
    chapter_number: int
    task_id: str
    session_id: str
    turn_number: int
    record_file: str
    response_sha256: str
    next_command: str


def create_human_review_consult_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    start: int,
    end: int,
    question: str,
) -> HumanReviewConsultTaskResult:
    """Create one immutable advisory turn bound to the current frozen review bundle."""

    root = resolve_project_root(config)
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft.is_file():
        raise HumanReviewConsultError("current chapter draft is missing")
    candidate = _current_consult_candidate(root, chapter_number)
    candidate_text = candidate.read_text(encoding="utf-8")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start:
        raise HumanReviewConsultError("selected span must satisfy 0 <= start < end")
    if end > len(candidate_text):
        raise HumanReviewConsultError("selected span exceeds the current consultation candidate")
    normalized_question = str(question or "").strip()
    if not normalized_question:
        raise HumanReviewConsultError("consultation question must not be empty")

    candidate_hash = _file_hash(candidate)
    mark_stale_human_consultations(root, chapter_number=chapter_number)
    binding = human_review_bundle_binding(config, chapter_number=chapter_number, freeze=False)
    if not binding.get("frozen"):
        raise HumanReviewConsultError(
            "human consultation requires the immutable review bundle created by human-review-task"
        )
    source_hash = _file_hash(draft)
    if str(binding.get("candidate_sha256") or "") != source_hash:
        raise HumanReviewConsultError("frozen review bundle is stale for the current revision source")

    story_brief = root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.md"
    if not story_brief.is_file():
        raise HumanReviewConsultError("current Story Brief is missing")
    bundle = root / str(binding["review_bundle"])
    if _file_hash(bundle) != str(binding["review_bundle_sha256"]):
        raise HumanReviewConsultError("frozen review bundle bytes do not match its SHA-256")

    session_dir = _candidate_session_dir(root, chapter_number, candidate_hash)
    session_file = session_dir / "session.json"
    session = _load_json(session_file, default={})
    if session:
        _require_session_candidate(session, candidate_hash)
        session_id = str(session.get("session_id") or "")
        turns = list(session.get("turns") or [])
    else:
        session_id = f"consult-ch{chapter_number:03d}-{candidate_hash[:16]}"
        turns = []
        session = {
            "schema": SESSION_SCHEMA,
            "session_id": session_id,
            "chapter_number": chapter_number,
            "candidate_sha256": candidate_hash,
            "review_bundle": str(binding["review_bundle"]),
            "review_bundle_sha256": str(binding["review_bundle_sha256"]),
            "status": "active",
            "created_at": _utc_now(),
            "turns": turns,
        }
    turn_number = len(turns) + 1
    token = f"turn{turn_number:02d}"
    task_id = f"human_review_consult:ch{chapter_number:03d}:{candidate_hash[:12]}:t{turn_number:02d}:v4"
    task_file = session_dir / f"{token}.task.md"
    request_file = session_dir / f"{token}.request.json"
    history_file = session_dir / f"{token}.history.json"
    response_file = session_dir / f"{token}.response.md"
    manifest_file = session_dir / f"{token}.manifest.json"
    for immutable in (task_file, request_file, history_file, manifest_file):
        if immutable.exists():
            raise HumanReviewConsultError(f"immutable consultation artifact already exists: {immutable.name}")

    request = {
        "schema": REQUEST_SCHEMA,
        "session_id": session_id,
        "turn_number": turn_number,
        "chapter_number": chapter_number,
        "candidate_sha256": candidate_hash,
        "review_bundle_sha256": str(binding["review_bundle_sha256"]),
        "selection": {
            "start": start,
            "end": end,
            "text": candidate_text[start:end],
        },
        "question": normalized_question,
        "created_at": _utc_now(),
    }
    history = {
        "schema": HISTORY_SCHEMA,
        "session_id": session_id,
        "candidate_sha256": candidate_hash,
        "turns": _recorded_history(root, turns)[-8:],
    }
    _write_json(request_file, request)
    _write_json(history_file, history)
    atomic_write_text(
        task_file,
        _render_task(
            chapter_number=chapter_number,
            turn_number=turn_number,
            candidate_hash=candidate_hash,
            draft=relative_path(root, candidate),
            story_brief=relative_path(root, story_brief),
            bundle=relative_path(root, bundle),
            request=relative_path(root, request_file),
            history=relative_path(root, history_file),
            response=relative_path(root, response_file),
        ),
    )
    manifest = build_manifest(
        root,
        task_type="human_review_consult",
        chapter_number=chapter_number,
        input_files=(task_file, candidate, story_brief, bundle, request_file, history_file),
        allowed_output_paths=(response_file,),
        output_schema="design_document_v1",
        validate_command=(
            "longform-engine review consult-validate project.yaml "
            f"--chapter {chapter_number} --file {relative_path(root, response_file)}"
        ),
        apply_command=(
            "longform-engine review consult-record project.yaml "
            f"--chapter {chapter_number} --file {relative_path(root, response_file)}"
        ),
        failure_next_command=(
            "longform-engine review consult-task project.yaml "
            f"--chapter {chapter_number} --start {start} --end {end} --question retry"
        ),
        canonical_targets=(),
        requires_human_apply=False,
        context_policy={
            "required_files": (task_file, candidate, story_brief, bundle, request_file, history_file),
            "compiled_brief": task_file,
            "selection_report": request_file,
            "quality_focus": ("scene_causality", "character_agency"),
        },
        role_id="human_review_advisor",
        task_id=task_id,
    )
    written_manifest = write_manifest(root, manifest, manifest_file)
    turns.append(
        {
            "turn_number": turn_number,
            "task_id": task_id,
            "request_file": relative_path(root, request_file),
            "history_file": relative_path(root, history_file),
            "response_file": relative_path(root, response_file),
            "manifest_file": relative_path(root, written_manifest),
            "record_file": "",
            "status": "awaiting_agent",
        }
    )
    session["turns"] = turns
    session["updated_at"] = _utc_now()
    _write_json(session_file, session)
    return HumanReviewConsultTaskResult(
        chapter_number=chapter_number,
        task_id=task_id,
        session_id=session_id,
        turn_number=turn_number,
        manifest_file=relative_path(root, written_manifest),
        task_file=relative_path(root, task_file),
        request_file=relative_path(root, request_file),
        history_file=relative_path(root, history_file),
        response_file=relative_path(root, response_file),
        candidate_sha256=candidate_hash,
        review_bundle_sha256=str(binding["review_bundle_sha256"]),
        next_command=f"longform-engine agent-task brief project.yaml {task_id}",
    )


def validate_human_review_consultation(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> HumanReviewConsultValidateResult:
    """Validate one advisory response without admitting it as a manuscript change."""

    root = resolve_project_root(config)
    response = _resolve_inside(root, file_path)
    task = _task_for_response(root, chapter_number, response)
    errors = _current_turn_errors(root, chapter_number, task)
    control = None
    if not errors:
        try:
            control = validate_production_agent_result(
                root,
                load_manifest(root, str(task["task_id"])),
                result_file=response,
            )
        except (AgentProductionPipelineError, OSError, UnicodeError, ValueError) as exc:
            errors.append(str(exc))
        else:
            if not control.ok:
                errors.extend(control.normalization.errors)
    if response.is_file():
        try:
            parse_design_document(
                response.read_text(encoding="utf-8"), expected_type="human_review_consult"
            )
        except (OSError, UnicodeError, AgentProtocolError) as exc:
            errors.append(str(exc))
    else:
        errors.append("consultation response file is missing")
    errors = list(dict.fromkeys(errors))
    ok = not errors
    report = response.with_suffix(".validation.json")
    report_payload = {
        "schema": VALIDATION_SCHEMA,
        "chapter_number": chapter_number,
        "task_id": str(task["task_id"]),
        "ok": ok,
        "response_file": relative_path(root, response),
        "response_sha256": _file_hash(response) if response.is_file() else "",
        "candidate_sha256": _current_candidate_hash(root, chapter_number),
        "review_bundle_sha256": _session_for_task(root, task)[1].get(
            "review_bundle_sha256", ""
        ),
        "errors": errors,
        "canonical_mutated": False,
        "validated_at": _utc_now(),
    }
    _write_json(report, report_payload)
    if control is not None:
        update_task_status(
            root,
            str(task["task_id"]),
            to_status="validated" if ok else "invalid",
            command="review consult-validate",
            artifact=response,
            result=report,
            current_result={
                "ok": ok,
                "path": relative_path(root, response),
                "sha256": report_payload["response_sha256"],
                "diagnostic_file": "",
                "source_schema": "design_document_v1",
                "validated_at": report_payload["validated_at"],
            },
        )
    _update_turn_status(root, task, "validated" if ok else "invalid")
    next_command = (
        "longform-engine review consult-record project.yaml "
        f"--chapter {chapter_number} --file {relative_path(root, response)}"
        if ok
        else str((task.get("commands") or {}).get("failure") or "")
    )
    return HumanReviewConsultValidateResult(
        chapter_number=chapter_number,
        task_id=str(task["task_id"]),
        ok=ok,
        report_file=relative_path(root, report),
        errors=tuple(errors),
        next_command=next_command,
    )


def record_human_review_consultation(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> HumanReviewConsultRecordResult:
    """Record a validated suggestion as non-canonical consultation history."""

    root = resolve_project_root(config)
    response = _resolve_inside(root, file_path)
    task = _task_for_response(root, chapter_number, response)
    current_errors = _current_turn_errors(root, chapter_number, task)
    if current_errors:
        raise HumanReviewConsultError("stale consultation: " + "; ".join(current_errors))
    _bound_task, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type="human_review_consult",
        output_path=response,
        allowed_statuses=("validated",),
    )
    if control_errors:
        raise HumanReviewConsultError("consultation must pass consult-validate: " + "; ".join(control_errors))
    session_file, session, turn = _session_turn(root, task)
    response_hash = _file_hash(response)
    validation = _load_json(response.with_suffix(".validation.json"), default={})
    if (
        validation.get("schema") != VALIDATION_SCHEMA
        or validation.get("ok") is not True
        or str(validation.get("response_sha256") or "") != response_hash
    ):
        raise HumanReviewConsultError("consultation validation report is missing or stale")
    parse_design_document(response.read_text(encoding="utf-8"), expected_type="human_review_consult")
    turn_number = int(turn["turn_number"])
    record = response.with_name(f"turn{turn_number:02d}.record.json")
    if record.exists():
        prior = _load_json(record, default={})
        if str(prior.get("response_sha256") or "") != response_hash:
            raise HumanReviewConsultError("immutable consultation record already binds different bytes")
    else:
        _write_json(
            record,
            {
                "schema": RECORD_SCHEMA,
                "session_id": str(session["session_id"]),
                "turn_number": turn_number,
                "task_id": str(task["task_id"]),
                "chapter_number": chapter_number,
                "candidate_sha256": str(session["candidate_sha256"]),
                "review_bundle_sha256": str(session["review_bundle_sha256"]),
                "request_file": str(turn["request_file"]),
                "response_file": relative_path(root, response),
                "response_sha256": response_hash,
                "canonical_write_performed": False,
                "suggestion_conversion_required": True,
                "status": "recorded",
                "recorded_at": _utc_now(),
            },
        )
    turn["record_file"] = relative_path(root, record)
    turn["status"] = "recorded"
    session["updated_at"] = _utc_now()
    _write_json(session_file, session)
    update_task_status(
        root,
        str(task["task_id"]),
        to_status="applied",
        command="review consult-record",
        artifact=response,
        result=record,
    )
    return HumanReviewConsultRecordResult(
        chapter_number=chapter_number,
        task_id=str(task["task_id"]),
        session_id=str(session["session_id"]),
        turn_number=turn_number,
        record_file=relative_path(root, record),
        response_sha256=response_hash,
        next_command="convert selected advice to a human_story_review_v4 annotation in the review desk",
    )


def mark_stale_human_consultations(root: Path, *, chapter_number: int) -> list[str]:
    """Mark every session for an older candidate stale; never mutate manuscript state."""

    root = root.resolve()
    current_hash = _current_candidate_hash(root, chapter_number)
    base = root / "50_workbench" / "human_story_reviews" / "consultations" / f"ch{chapter_number:03d}"
    stale_sessions: list[str] = []
    if not base.is_dir():
        return stale_sessions
    for session_file in sorted(base.glob("*/session.json")):
        session = _load_json(session_file, default={})
        if not isinstance(session, dict) or session.get("schema") != SESSION_SCHEMA:
            continue
        if str(session.get("candidate_sha256") or "") == current_hash:
            continue
        if session.get("status") != "stale":
            session["status"] = "stale"
            session["stale_reason"] = "candidate_sha256_changed"
            session["stale_at"] = _utc_now()
            for turn in session.get("turns") or []:
                if not isinstance(turn, dict):
                    continue
                turn["status"] = "stale"
                record_file = str(turn.get("record_file") or "")
                if record_file:
                    record = _load_json(root / record_file, default={})
                    if isinstance(record, dict):
                        record["status"] = "stale"
                        record["stale_reason"] = "candidate_sha256_changed"
                        _write_json(root / record_file, record)
                task_id = str(turn.get("task_id") or "")
                indexed = next(
                    (item for item in list_manifests(root, chapter_number=chapter_number)
                     if str(item.get("task_id") or "") == task_id),
                    None,
                )
                if indexed is not None and str(indexed.get("status") or "") not in {
                    "applied", "superseded", "rolled_back"
                }:
                    update_task_status(
                        root,
                        task_id,
                        to_status="superseded",
                        command="review consultation candidate changed",
                    )
            _write_json(session_file, session)
        stale_sessions.append(str(session.get("session_id") or ""))
    return stale_sessions


def consultation_status(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    """Return all consultation sessions for the chapter with dynamic candidate freshness."""

    root = resolve_project_root(config)
    current_hash = _current_candidate_hash(root, chapter_number)
    base = root / "50_workbench" / "human_story_reviews" / "consultations" / f"ch{chapter_number:03d}"
    sessions: list[dict[str, Any]] = []
    if base.is_dir():
        for session_file in sorted(base.glob("*/session.json")):
            session = _load_json(session_file, default={})
            if not isinstance(session, dict) or session.get("schema") != SESSION_SCHEMA:
                continue
            item = dict(session)
            item["session_file"] = relative_path(root, session_file)
            if str(item.get("candidate_sha256") or "") != current_hash:
                item["status"] = "stale"
            sessions.append(item)
    sessions.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {
        "schema": "human_review_consultation_status_v1",
        "chapter_number": chapter_number,
        "candidate_sha256": current_hash,
        "sessions": sessions,
        "canonical_mutated": False,
    }


def _render_task(**values: Any) -> str:
    return "\n".join(
        [
            f"# ch{values['chapter_number']:03d} 人工深审咨询 t{values['turn_number']:02d}",
            "",
            f"- 当前候选 SHA-256：`{values['candidate_hash']}`",
            f"- 当前正文：`{values['draft']}`",
            f"- Story Brief：`{values['story_brief']}`",
            f"- 冻结 review bundle：`{values['bundle']}`",
            f"- 本轮选中 span 与问题：`{values['request']}`",
            f"- 同候选历史咨询：`{values['history']}`",
            "",
            "只回答本轮问题；区分正文证据、审稿证据和可能性。不得修改正文、批准章节或写 canonical。",
            "只输出 design_document_v1，并依次使用：问题复述、证据判断、可选修法、风险与保护项、建议动作。",
            "建议只能由人工转换为批注，不能自动执行。",
            "",
            f"输出：`{values['response']}`",
            "",
        ]
    )


def _recorded_history(root: Path, turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, dict) or not str(turn.get("record_file") or ""):
            continue
        request = _load_json(root / str(turn.get("request_file") or ""), default={})
        response = root / str(turn.get("response_file") or "")
        history.append(
            {
                "turn_number": int(turn.get("turn_number") or 0),
                "question": str(request.get("question") or ""),
                "selection": request.get("selection") or {},
                "response": response.read_text(encoding="utf-8") if response.is_file() else "",
                "response_sha256": _file_hash(response) if response.is_file() else "",
            }
        )
    return history


def _task_for_response(root: Path, chapter_number: int, response: Path) -> dict[str, Any]:
    response_rel = relative_path(root, response)
    matches = [
        item
        for item in list_manifests(root, chapter_number=chapter_number)
        if item.get("task_type") == "human_review_consult"
        and str(manifest_output(item).get("path") or "") == response_rel
    ]
    if len(matches) != 1:
        raise HumanReviewConsultError(
            f"expected exactly one consultation task for {response_rel}; found {len(matches)}"
        )
    return matches[0]


def _current_turn_errors(root: Path, chapter_number: int, task: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    _session_file, session, _turn = _session_turn(root, task)
    if session.get("status") == "stale":
        errors.append("consultation session is stale")
    if str(session.get("candidate_sha256") or "") != _current_candidate_hash(root, chapter_number):
        errors.append("candidate_sha256 changed")
    bundle = root / str(session.get("review_bundle") or "")
    if not bundle.is_file() or _file_hash(bundle) != str(session.get("review_bundle_sha256") or ""):
        errors.append("review_bundle_sha256 changed")
    return errors


def _session_for_task(root: Path, task: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    output = root / str(manifest_output(task).get("path") or "")
    session_file = output.parent / "session.json"
    session = _load_json(session_file, default={})
    if not isinstance(session, dict) or session.get("schema") != SESSION_SCHEMA:
        raise HumanReviewConsultError("consultation session record is missing or invalid")
    return session_file, session


def _session_turn(
    root: Path, task: dict[str, Any]
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    session_file, session = _session_for_task(root, task)
    turn = next(
        (
            item
            for item in session.get("turns") or []
            if isinstance(item, dict) and item.get("task_id") == task.get("task_id")
        ),
        None,
    )
    if turn is None:
        raise HumanReviewConsultError("consultation turn is not registered in its session")
    return session_file, session, turn


def _update_turn_status(root: Path, task: dict[str, Any], status: str) -> None:
    session_file, session, turn = _session_turn(root, task)
    if session.get("status") != "stale":
        turn["status"] = status
        session["updated_at"] = _utc_now()
        _write_json(session_file, session)


def _candidate_session_dir(root: Path, chapter_number: int, candidate_hash: str) -> Path:
    return (
        root
        / "50_workbench"
        / "human_story_reviews"
        / "consultations"
        / f"ch{chapter_number:03d}"
        / candidate_hash[:12]
    )


def _require_session_candidate(session: dict[str, Any], candidate_hash: str) -> None:
    if session.get("schema") != SESSION_SCHEMA:
        raise HumanReviewConsultError("consultation session schema is invalid")
    if str(session.get("candidate_sha256") or "") != candidate_hash:
        raise HumanReviewConsultError("candidate hash prefix collision in consultation storage")
    if session.get("status") == "stale":
        raise HumanReviewConsultError("stale consultation session cannot receive a new turn")


def _current_candidate_hash(root: Path, chapter_number: int) -> str:
    candidate = _current_consult_candidate(root, chapter_number)
    return _file_hash(candidate) if candidate.is_file() else ""


def _current_consult_candidate(root: Path, chapter_number: int) -> Path:
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft.is_file():
        return draft
    digest = _file_hash(draft)
    task_file = (
        root
        / "50_workbench"
        / "human_author_revisions"
        / f"ch{chapter_number:03d}"
        / f"{digest[:12]}.task.json"
    )
    task = _load_json(task_file, default={})
    candidate = root / str(task.get("candidate_file") or "") if isinstance(task, dict) else Path()
    try:
        candidate.resolve().relative_to((root / "50_workbench" / "human_author_revisions").resolve())
    except ValueError:
        return draft
    return candidate if candidate.is_file() and candidate.read_text(encoding="utf-8").strip() else draft


def _resolve_inside(root: Path, file_path: str | Path) -> Path:
    candidate = Path(file_path)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise HumanReviewConsultError("consultation file must stay inside the project root") from exc
    return resolved


def _load_json(path: Path, *, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


__all__ = [
    "HumanReviewConsultError",
    "HumanReviewConsultRecordResult",
    "HumanReviewConsultTaskResult",
    "HumanReviewConsultValidateResult",
    "consultation_status",
    "create_human_review_consult_task",
    "mark_stale_human_consultations",
    "record_human_review_consultation",
    "validate_human_review_consultation",
]
