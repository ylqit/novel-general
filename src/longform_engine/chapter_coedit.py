"""Non-canonical, human-directed conversational chapter editing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import AgentProtocolError, parse_design_document
from longform_engine.agent_tasks import (
    build_manifest,
    list_manifests,
    load_manifest,
    manifest_output,
    mark_tasks_for_output,
    update_task_status,
    validate_current_task_result,
    write_manifest,
)
from longform_engine.config import ConfigDocument
from longform_engine.human_chapter_intent import require_current_human_chapter_intent
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.project import native_filesystem_path
from longform_engine.storage.layout import manuscript_chapter_path
from longform_engine.story_brief import load_current_story_brief_binding


SESSION_SCHEMA = "chapter_coedit_session_v2"
TURN_SCHEMA = "chapter_coedit_turn_v1"
SELECTION_SCHEMA = "chapter_coedit_selection_v1"
VALIDATION_SCHEMA = "chapter_coedit_candidate_validation_v1"
TEXT_ANCHOR_SCHEMA = "text_anchor_v2"
TASK_TYPE = "chapter_coedit_rewrite"
OPTION_PATTERN = re.compile(r"^###\s+(OPTION-[A-Z0-9][A-Z0-9_-]{0,31})\s*$", re.MULTILINE)


class ChapterCoeditError(ValueError):
    """Raised when a conversational edit would escape evidence or repair boundaries."""


@dataclass(frozen=True)
class ChapterCoeditTurnResult:
    chapter_number: int
    session_id: str
    turn_number: int
    task_id: str
    task_file: str
    response_file: str
    candidate_sha256: str
    next_command: str


@dataclass(frozen=True)
class ChapterCoeditRecordResult:
    chapter_number: int
    session_id: str
    turn_number: int
    response_sha256: str
    option_ids: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class ChapterCoeditResponseValidateResult:
    chapter_number: int
    ok: bool
    task_id: str
    report_file: str
    errors: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class ChapterCoeditRewriteTaskResult:
    chapter_number: int
    session_id: str
    turn_number: int
    task_id: str
    task_file: str
    candidate_file: str
    selected_option_id: str
    next_command: str


@dataclass(frozen=True)
class ChapterCoeditCandidateValidateResult:
    chapter_number: int
    ok: bool
    session_id: str
    turn_number: int
    candidate_file: str
    candidate_sha256: str
    report_file: str
    errors: tuple[str, ...]
    next_command: str


def create_chapter_coedit_turn(
    config: ConfigDocument,
    *,
    chapter_number: int,
    start: int,
    end: int,
    question: str,
) -> ChapterCoeditTurnResult:
    """Create one advisor turn against the latest full workbench candidate."""

    root = resolve_project_root(config)
    assert_coedit_allowed(root, chapter_number)
    story_brief = load_current_story_brief_binding(root, chapter_number)
    intent = require_current_human_chapter_intent(root, chapter_number)
    candidate = current_coedit_candidate(root, chapter_number)
    text = candidate.read_text(encoding="utf-8")
    if start < 0 or end <= start or end > len(text):
        raise ChapterCoeditError("selected span must satisfy 0 <= start < end <= candidate length")
    normalized_question = str(question or "").strip()
    if not normalized_question:
        raise ChapterCoeditError("coedit question must not be empty")
    mark_stale_coedit_sessions(root, chapter_number=chapter_number)
    candidate_hash = file_hash(candidate)
    basis_hash = str(story_brief["story_brief_basis_sha256"])
    intent_hash = str(intent["sha256"])
    session_dir, session = active_session(
        root,
        chapter_number=chapter_number,
        candidate=candidate,
        basis_hash=basis_hash,
        intent_hash=intent_hash,
    )
    turns = session["turns"]
    turn_number = len(turns) + 1
    token = f"turn{turn_number:02d}"
    task_id = (
        f"human_review_consult:coedit:ch{chapter_number:03d}:"
        f"{session['session_id']}:{token}:v1"
    )
    request_file = session_dir / f"{token}.request.json"
    history_file = session_dir / f"{token}.history.json"
    task_file = session_dir / f"{token}.task.md"
    response_file = session_dir / f"{token}.response.md"
    manifest_file = session_dir / f"{token}.manifest.json"
    turn_file = session_dir / f"{token}.json"
    request = {
        "schema": "chapter_coedit_request_v1",
        "chapter_number": chapter_number,
        "session_id": session["session_id"],
        "turn_number": turn_number,
        "candidate_sha256": candidate_hash,
        "story_brief_basis_sha256": basis_hash,
        "human_chapter_intent_sha256": intent_hash,
        "selection": build_text_anchor(text, start=start, end=end, source_sha256=candidate_hash),
        "question": normalized_question,
    }
    turn = {
        "schema": TURN_SCHEMA,
        "session_id": session["session_id"],
        "chapter_number": chapter_number,
        "turn_number": turn_number,
        "candidate_file": relative(root, candidate),
        "candidate_sha256": candidate_hash,
        "request_file": relative(root, request_file),
        "response_file": relative(root, response_file),
        "response_sha256": "",
        "option_ids": [],
        "selection_file": "",
        "rewrite_task_id": "",
        "rewrite_candidate_file": "",
        "rewrite_candidate_sha256": "",
        "status": "awaiting_advisor",
        "created_at": utc_now(),
    }
    history = []
    for prior_relative in turns:
        prior_path = resolve_inside(root, prior_relative, session_dir)
        prior = load_json(prior_path)
        if not isinstance(prior, dict):
            raise ChapterCoeditError("consultation history is incomplete")
        if prior.get("status") not in {"advisor_recorded", "rewrite_validated"}:
            continue
        if prior.get("candidate_sha256") != candidate_hash:
            continue
        prior_response = resolve_inside(root, str(prior["response_file"]), session_dir)
        prior_request = resolve_inside(root, str(prior["request_file"]), session_dir)
        if not prior_response.is_file() or file_hash(prior_response) != prior.get("response_sha256"):
            raise ChapterCoeditError("consultation history response hash changed")
        question_record = load_json(prior_request)
        if not isinstance(question_record, dict):
            raise ChapterCoeditError("consultation history request is incomplete")
        history.append({"turn_number": prior["turn_number"], "question": question_record["question"],
                        "selection": question_record["selection"],
                        "response": prior_response.read_text(encoding="utf-8"),
                        "response_sha256": prior["response_sha256"], "selection_file": prior.get("selection_file", "")})
    write_json(history_file, {"schema": "chapter_coedit_history_v1", "session_id": session["session_id"],
                              "candidate_sha256": candidate_hash, "turns": history})
    write_json(request_file, request)
    write_json(turn_file, turn)
    atomic_write_text(
        task_file,
        render_advisor_task(
            chapter_number=chapter_number,
            candidate=relative(root, candidate),
            story_brief=str(story_brief["story_brief_markdown_file"]),
            intent_file=str(intent["path"]),
            request_file=relative(root, request_file),
            response_file=relative(root, response_file),
        ),
    )
    manifest = build_manifest(
        root,
        task_type="human_review_consult",
        chapter_number=chapter_number,
        input_files=(
            task_file,
            candidate,
            root / str(story_brief["story_brief_markdown_file"]),
            root / str(intent["path"]),
            request_file,
            history_file,
        ),
        allowed_output_paths=(response_file,),
        output_schema="design_document_v1",
        validate_command=(
            f"longform-engine review consult-validate project.yaml --phase coedit --chapter {chapter_number} "
            f"--file {relative(root, response_file)}"
        ),
        apply_command=(
            f"longform-engine review consult-record project.yaml --phase coedit --chapter {chapter_number} "
            f"--file {relative(root, response_file)}"
        ),
        failure_next_command=(
            f"longform-engine chapter coedit-start project.yaml --chapter {chapter_number} "
            f"--start {start} --end {end} --question retry"
        ),
        canonical_targets=(),
        requires_human_apply=False,
        context_policy={
            "required_files": (task_file, candidate, request_file, history_file),
            "compiled_brief": task_file,
            "selection_report": request_file,
            "quality_focus": ("human_intent", "reader_effect", "preservation"),
        },
        role_id="human_author_advisor",
        task_id=task_id,
    )
    write_manifest(root, manifest, manifest_file)
    turns.append(relative(root, turn_file))
    session["updated_at"] = utc_now()
    write_json(session_dir / "session.json", session)
    return ChapterCoeditTurnResult(
        chapter_number=chapter_number,
        session_id=str(session["session_id"]),
        turn_number=turn_number,
        task_id=task_id,
        task_file=relative(root, task_file),
        response_file=relative(root, response_file),
        candidate_sha256=candidate_hash,
        next_command=f"longform-engine agent-task brief project.yaml {task_id}",
    )


def record_chapter_coedit_response(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> ChapterCoeditRecordResult:
    root = resolve_project_root(config)
    response = resolve_inside(root, file_path, coedit_root(root))
    manifest, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type="human_review_consult",
        output_path=response,
        allowed_statuses=("validated",),
    )
    if control_errors:
        raise ChapterCoeditError(
            "coedit response must pass consult-validate: " + "; ".join(control_errors)
        )
    try:
        parse_design_document(response.read_text(encoding="utf-8"), expected_type="human_review_consult")
    except (OSError, UnicodeError, AgentProtocolError) as exc:
        raise ChapterCoeditError(f"coedit advisor response is invalid: {exc}") from exc
    option_ids = tuple(dict.fromkeys(OPTION_PATTERN.findall(response.read_text(encoding="utf-8"))))
    if not 2 <= len(option_ids) <= 3:
        raise ChapterCoeditError("coedit advisor response must provide 2-3 stable OPTION-* headings")
    session_file, session, turn_file, turn = session_turn_for_response(root, response)
    ensure_session_current(root, session)
    ensure_turn_candidate_current(root, session, turn)
    validation = load_json(response.with_suffix(".validation.json"))
    if (
        not isinstance(validation, dict)
        or validation.get("schema") != "chapter_coedit_response_validation_v1"
        or validation.get("ok") is not True
        or validation.get("response_sha256") != file_hash(response)
    ):
        raise ChapterCoeditError("coedit response validation report is missing or stale")
    turn["response_sha256"] = file_hash(response)
    turn["option_ids"] = list(option_ids)
    turn["status"] = "advisor_recorded"
    turn["recorded_at"] = utc_now()
    write_json(turn_file, turn)
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=response,
        to_status="validated",
        command="chapter coedit-record",
        result=turn_file,
        from_statuses=("submitted", "validated"),
    )
    session["updated_at"] = utc_now()
    write_json(session_file, session)
    return ChapterCoeditRecordResult(
        chapter_number=chapter_number,
        session_id=str(session["session_id"]),
        turn_number=int(turn["turn_number"]),
        response_sha256=str(turn["response_sha256"]),
        option_ids=option_ids,
        next_command=(
            f"longform-engine chapter coedit-rewrite-task project.yaml --chapter {chapter_number} "
            f"--session {session['session_id']} --turn {turn['turn_number']} --option-id {option_ids[0]}"
        ),
    )


def validate_chapter_coedit_response(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> ChapterCoeditResponseValidateResult:
    """Validate one advisor response without selecting or applying a suggestion."""

    root = resolve_project_root(config)
    response = resolve_inside(root, file_path, coedit_root(root))
    errors: list[str] = []
    try:
        manifest_payload = manifest_for_output(
            root,
            chapter_number=chapter_number,
            task_type="human_review_consult",
            output_path=response,
        )
    except ChapterCoeditError as exc:
        manifest_payload = {}
        errors.append(str(exc))
    if manifest_payload:
        control = validate_production_agent_result(root, manifest_payload, result_file=response)
        if not control.ok:
            errors.extend(control.normalization.errors)
    else:
        errors.append("active coedit advisor task manifest is missing")
    if response.is_file():
        try:
            parse_design_document(
                response.read_text(encoding="utf-8"), expected_type="human_review_consult"
            )
        except (OSError, UnicodeError, AgentProtocolError) as exc:
            errors.append(str(exc))
    else:
        errors.append("coedit advisor response is missing")
    option_ids = (
        tuple(dict.fromkeys(OPTION_PATTERN.findall(response.read_text(encoding="utf-8"))))
        if response.is_file()
        else ()
    )
    if not 2 <= len(option_ids) <= 3:
        errors.append("coedit advisor response must provide 2-3 stable OPTION-* headings")
    try:
        _session_file, session, _turn_file, turn = session_turn_for_response(root, response)
        ensure_session_current(root, session)
        ensure_turn_candidate_current(root, session, turn)
    except ChapterCoeditError as exc:
        errors.append(str(exc))
    errors = list(dict.fromkeys(errors))
    report_file = response.with_suffix(".validation.json")
    report = {
        "schema": "chapter_coedit_response_validation_v1",
        "chapter_number": chapter_number,
        "task_id": str(manifest_payload.get("task_id") or ""),
        "ok": not errors,
        "response_file": relative(root, response),
        "response_sha256": file_hash(response),
        "option_ids": list(option_ids),
        "errors": errors,
        "canonical_write_performed": False,
        "validated_at": utc_now(),
    }
    write_json(report_file, report)
    update_task_status(
        root,
        str(manifest_payload.get("task_id") or ""),
        to_status="validated" if not errors else "invalid",
        command="review consult-validate --phase coedit",
        artifact=response,
        result=report_file,
    )
    return ChapterCoeditResponseValidateResult(
        chapter_number=chapter_number,
        ok=not errors,
        task_id=str(manifest_payload.get("task_id") or ""),
        report_file=relative(root, report_file),
        errors=tuple(errors),
        next_command=(
            f"longform-engine review consult-record project.yaml --phase coedit "
            f"--chapter {chapter_number} --file {relative(root, response)}"
            if not errors
            else str((manifest_payload.get("commands") or {}).get("failure") or "")
        ),
    )


def create_chapter_coedit_rewrite_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    session_id: str,
    turn_number: int,
    option_id: str,
    adjustment: str = "",
) -> ChapterCoeditRewriteTaskResult:
    root = resolve_project_root(config)
    assert_coedit_allowed(root, chapter_number)
    session_file, session = load_session_by_id(root, chapter_number, session_id)
    ensure_session_current(root, session)
    turn_file, turn = load_turn(root, session, turn_number)
    ensure_turn_candidate_current(root, session, turn)
    if turn.get("status") != "advisor_recorded":
        raise ChapterCoeditError("coedit turn must have one validated advisor response")
    option_id = str(option_id or "").strip()
    if option_id not in turn.get("option_ids", []):
        raise ChapterCoeditError("option_id must reference one recorded advisor option")
    source = root / str(turn["candidate_file"])
    response = root / str(turn["response_file"])
    if file_hash(response) != turn.get("response_sha256"):
        raise ChapterCoeditError("coedit response changed after it was recorded")
    story_brief = load_current_story_brief_binding(root, chapter_number)
    intent = require_current_human_chapter_intent(root, chapter_number)
    selection_file = turn_file.with_name(f"turn{turn_number:02d}.selection.json")
    selection = {
        "schema": SELECTION_SCHEMA,
        "session_id": session_id,
        "turn_number": turn_number,
        "candidate_sha256": str(turn["candidate_sha256"]),
        "response_sha256": str(turn["response_sha256"]),
        "selected_option_id": option_id,
        "human_adjustment": str(adjustment or "").strip(),
        "selected_by": "human",
        "selected_at": utc_now(),
    }
    write_json(selection_file, selection)
    output = (
        root
        / "50_workbench"
        / "agent_drafts"
        / f"ch{chapter_number:03d}.coedit.{safe_token(session_id)}.t{turn_number:02d}.md"
    )
    task_file = turn_file.with_name(f"turn{turn_number:02d}.rewrite.task.md")
    manifest_file = turn_file.with_name(f"turn{turn_number:02d}.rewrite.manifest.json")
    task_id = f"{TASK_TYPE}:ch{chapter_number:03d}:{safe_token(session_id)}:t{turn_number:02d}:v1"
    atomic_write_text(
        task_file,
        "\n".join(
            [
                f"# ch{chapter_number:03d} 对话协作完整替代稿",
                "",
                "你是章节作者。依据人类选择的方案生成一份完整替代稿，不输出局部补丁。",
                f"- 当前完整候选：`{relative(root, source)}`",
                f"- 人类创作意图：`{intent['path']}`",
                f"- Story Brief：`{story_brief['story_brief_markdown_file']}`",
                f"- 顾问方案：`{relative(root, response)}`",
                f"- 人工选择：`{relative(root, selection_file)}`",
                "",
                "必须保留章节合同、知识边界、能力代价、关系阶段和人类保护项。",
                "只写完整小说正文；不得写解释、Prompt、diff、finding code 或平台配额。",
                f"输出：`{relative(root, output)}`",
                f"校验：`longform-engine chapter coedit-candidate-validate project.yaml --chapter {chapter_number} "
                f"--file {relative(root, output)}`",
                "",
            ]
        ),
    )
    manifest = build_manifest(
        root,
        task_type=TASK_TYPE,
        chapter_number=chapter_number,
        input_files=(
            task_file,
            source,
            root / str(intent["path"]),
            root / str(story_brief["story_brief_markdown_file"]),
            response,
            selection_file,
        ),
        allowed_output_paths=(output,),
        output_schema="prose_markdown_v1",
        validate_command=(
            f"longform-engine chapter coedit-candidate-validate project.yaml --chapter {chapter_number} "
            f"--file {relative(root, output)}"
        ),
        apply_command=(
            f"longform-engine draft submit project.yaml --chapter {chapter_number} "
            f"--file {relative(root, output)} --agent codex"
        ),
        failure_next_command=(
            f"longform-engine chapter coedit-rewrite-task project.yaml --chapter {chapter_number} "
            f"--session {session_id} --turn {turn_number} --option-id {option_id}"
        ),
        canonical_targets=(),
        requires_human_apply=False,
        context_policy={
            "required_files": (task_file, source, selection_file),
            "compiled_brief": task_file,
            "selection_report": selection_file,
            "quality_focus": ("human_intent", "full_candidate", "preservation"),
        },
        role_id="chapter_author",
        task_id=task_id,
    )
    write_manifest(root, manifest, manifest_file)
    turn.update(
        {
            "selection_file": relative(root, selection_file),
            "rewrite_task_id": task_id,
            "rewrite_candidate_file": relative(root, output),
            "status": "awaiting_rewrite",
        }
    )
    write_json(turn_file, turn)
    session["updated_at"] = utc_now()
    write_json(session_file, session)
    return ChapterCoeditRewriteTaskResult(
        chapter_number=chapter_number,
        session_id=session_id,
        turn_number=turn_number,
        task_id=task_id,
        task_file=relative(root, task_file),
        candidate_file=relative(root, output),
        selected_option_id=option_id,
        next_command=f"longform-engine agent-task brief project.yaml {task_id}",
    )


def validate_chapter_coedit_candidate(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> ChapterCoeditCandidateValidateResult:
    root = resolve_project_root(config)
    # A job may finish after the human has entered the final revision phase.
    # Its late output must not replace the consultation candidate or provenance.
    assert_coedit_allowed(root, chapter_number)
    candidate = resolve_inside(root, file_path, root / "50_workbench" / "agent_drafts")
    manifest = manifest_for_output(
        root,
        chapter_number=chapter_number,
        task_type=TASK_TYPE,
        output_path=candidate,
    )
    session_file, session, turn_file, turn = session_turn_for_rewrite(root, manifest)
    ensure_session_current(root, session)
    if file_hash(candidate) != session.get("current_candidate_sha256"):
        ensure_turn_candidate_current(root, session, turn)
    errors: list[str] = []
    control = validate_production_agent_result(root, manifest, result_file=candidate)
    if not control.ok:
        errors.extend(control.normalization.errors)
    source = root / str(turn["candidate_file"])
    errors.extend(coedit_candidate_errors(source, candidate))
    report_file = candidate.with_suffix(".coedit.validation.json")
    report = {
        "schema": VALIDATION_SCHEMA,
        "chapter_number": chapter_number,
        "session_id": session["session_id"],
        "turn_number": turn["turn_number"],
        "ok": not errors,
        "source_candidate_sha256": file_hash(source),
        "candidate_file": relative(root, candidate),
        "candidate_sha256": file_hash(candidate),
        "story_brief_basis_sha256": session["story_brief_basis_sha256"],
        "human_chapter_intent_sha256": session["human_chapter_intent_sha256"],
        "errors": errors,
        "validated_at": utc_now(),
    }
    write_json(report_file, report)
    if not errors:
        mark_tasks_for_output(
            root,
            chapter_number=chapter_number,
            output_path=candidate,
            to_status="validated",
            command="chapter coedit-candidate-validate",
            result=report_file,
            from_statuses=("submitted", "validated"),
        )
        turn["rewrite_candidate_sha256"] = file_hash(candidate)
        turn["status"] = "rewrite_validated"
        turn["validated_at"] = utc_now()
        write_json(turn_file, turn)
        session["current_candidate_file"] = relative(root, candidate)
        session["current_candidate_sha256"] = file_hash(candidate)
        session["updated_at"] = utc_now()
        write_json(session_file, session)
    next_command = (
        f"longform-engine chapter coedit-status project.yaml --chapter {chapter_number}"
        if not errors
        else str((manifest.get("commands") or {}).get("failure") or "")
    )
    return ChapterCoeditCandidateValidateResult(
        chapter_number=chapter_number,
        ok=not errors,
        session_id=str(session["session_id"]),
        turn_number=int(turn["turn_number"]),
        candidate_file=relative(root, candidate),
        candidate_sha256=file_hash(candidate),
        report_file=relative(root, report_file),
        errors=tuple(errors),
        next_command=next_command,
    )


def coedit_status(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    root = resolve_project_root(config)
    sessions = []
    for path in sorted((coedit_root(root) / f"ch{chapter_number:03d}").glob("*/session.json")):
        payload = load_json(path)
        if isinstance(payload, dict) and payload.get("schema") == SESSION_SCHEMA:
            view = dict(payload)
            if session_is_stale(root, payload):
                view["effective_status"] = "stale"
            else:
                view["effective_status"] = str(payload.get("status") or "unknown")
            sessions.append(view)
    return {
        "schema": "chapter_coedit_status_v1",
        "chapter_number": chapter_number,
        "sessions": sessions,
        "canonical_write_performed": False,
    }


def coedit_provenance(root: Path, chapter_number: int) -> dict[str, Any]:
    """Return a hash-only collaboration projection for the final human revision."""

    sessions = []
    for path in sorted((coedit_root(root) / f"ch{chapter_number:03d}").glob("*/session.json")):
        session = load_json(path)
        if not isinstance(session, dict) or session.get("schema") != SESSION_SCHEMA:
            continue
        turns = []
        for turn_path_value in session.get("turns") or []:
            turn = load_json(root / str(turn_path_value))
            if not isinstance(turn, dict) or turn.get("schema") != TURN_SCHEMA:
                continue
            turns.append(
                {
                    "turn_number": turn.get("turn_number"),
                    "candidate_sha256": turn.get("candidate_sha256"),
                    "response_sha256": turn.get("response_sha256"),
                    "selection_sha256": file_hash(root / str(turn.get("selection_file") or "")),
                    "rewrite_candidate_sha256": turn.get("rewrite_candidate_sha256"),
                }
            )
        sessions.append(
            {
                "session_id": session.get("session_id"),
                "status": session.get("status"),
                "human_chapter_intent_sha256": session.get("human_chapter_intent_sha256"),
                "story_brief_basis_sha256": session.get("story_brief_basis_sha256"),
                "turns": turns,
            }
        )
    projection = {"schema": "chapter_coedit_provenance_v1", "sessions": sessions}
    return {**projection, "sha256": json_hash(projection)}


def mark_stale_coedit_sessions(root: Path, *, chapter_number: int) -> list[str]:
    stale: list[str] = []
    try:
        basis = load_current_story_brief_binding(root, chapter_number)["story_brief_basis_sha256"]
        intent = require_current_human_chapter_intent(root, chapter_number)["sha256"]
    except ValueError:
        basis = ""
        intent = ""
    for path in sorted((coedit_root(root) / f"ch{chapter_number:03d}").glob("*/session.json")):
        session = load_json(path)
        if not isinstance(session, dict) or session.get("schema") != SESSION_SCHEMA:
            continue
        if session.get("status") == "stale":
            continue
        if (
            session.get("story_brief_basis_sha256") != basis
            or session.get("human_chapter_intent_sha256") != intent
        ):
            session["status"] = "stale"
            session["stale_at"] = utc_now()
            write_json(path, session)
            stale.append(str(session.get("session_id") or ""))
    return stale


def record_coedit_submission(root: Path, chapter_number: int, candidate: Path) -> None:
    candidate_hash = file_hash(candidate)
    for path in sorted((coedit_root(root) / f"ch{chapter_number:03d}").glob("*/session.json")):
        session = load_json(path)
        if not isinstance(session, dict) or session.get("schema") != SESSION_SCHEMA:
            continue
        if session.get("current_candidate_sha256") == candidate_hash:
            session["status"] = "submitted"
            session["submitted_candidate_sha256"] = candidate_hash
            session["submitted_at"] = utc_now()
            write_json(path, session)
        elif session.get("status") == "active":
            session["status"] = "stale"
            session["stale_at"] = utc_now()
            write_json(path, session)


def active_session(
    root: Path,
    *,
    chapter_number: int,
    candidate: Path,
    basis_hash: str,
    intent_hash: str,
) -> tuple[Path, dict[str, Any]]:
    chapter_dir = coedit_root(root) / f"ch{chapter_number:03d}"
    for path in sorted(chapter_dir.glob("*/session.json")):
        session = load_json(path)
        if (
            isinstance(session, dict)
            and session.get("schema") == SESSION_SCHEMA
            and session.get("status") == "active"
            and session.get("current_candidate_sha256") == file_hash(candidate)
            and session.get("story_brief_basis_sha256") == basis_hash
            and session.get("human_chapter_intent_sha256") == intent_hash
        ):
            return path.parent, session
    session_id = f"coedit-ch{chapter_number:03d}-{file_hash(candidate)[:12]}-{basis_hash[:10]}"
    session_dir = chapter_dir / session_id
    session = {
        "schema": SESSION_SCHEMA,
        "session_id": session_id,
        "chapter_number": chapter_number,
        "phase": "coedit",
        "status": "active",
        "initial_candidate_file": relative(root, candidate),
        "initial_candidate_sha256": file_hash(candidate),
        "current_candidate_file": relative(root, candidate),
        "current_candidate_sha256": file_hash(candidate),
        "story_brief_basis_sha256": basis_hash,
        "human_chapter_intent_sha256": intent_hash,
        "turns": [],
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    write_json(session_dir / "session.json", session)
    return session_dir, session


def current_coedit_candidate(root: Path, chapter_number: int) -> Path:
    active = []
    for path in sorted((coedit_root(root) / f"ch{chapter_number:03d}").glob("*/session.json")):
        session = load_json(path)
        if isinstance(session, dict) and session.get("schema") == SESSION_SCHEMA and session.get("status") == "active":
            candidate = root / str(session.get("current_candidate_file") or "")
            if candidate.is_file() and file_hash(candidate) == session.get("current_candidate_sha256"):
                active.append((path.stat().st_mtime_ns, candidate))
    if active:
        return sorted(active)[-1][1]
    candidates = []
    for manifest in list_manifests(root, chapter_number=chapter_number):
        if manifest.get("task_type") not in {"chapter_write", TASK_TYPE}:
            continue
        output = root / str(manifest_output(manifest).get("path") or "")
        if output.is_file():
            candidates.append((output.stat().st_mtime_ns, output))
    if candidates:
        return sorted(candidates)[-1][1]
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if draft.is_file():
        return draft
    raise ChapterCoeditError("AI initial candidate is missing; complete the chapter_write task first")


def manifest_for_output(
    root: Path,
    *,
    chapter_number: int,
    task_type: str,
    output_path: Path,
) -> dict[str, Any]:
    output_text = relative(root, output_path)
    matches = [
        manifest
        for manifest in list_manifests(root, chapter_number=chapter_number)
        if manifest.get("task_type") == task_type
        and str(manifest_output(manifest).get("path") or "").replace("\\", "/")
        == output_text
        and manifest.get("status") not in {"stale", "superseded", "consumed"}
    ]
    if len(matches) != 1:
        raise ChapterCoeditError(
            f"expected exactly one current {task_type} task for {output_text}; found {len(matches)}"
        )
    task_id = str(matches[0].get("task_id") or "")
    if not task_id:
        raise ChapterCoeditError(f"current {task_type} task has no task_id")
    return load_manifest(root, task_id)


def assert_coedit_allowed(root: Path, chapter_number: int) -> None:
    from longform_engine.human_author_revision import TASK_SCHEMA as HUMAN_REVISION_TASK_SCHEMA

    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    draft_hash = file_hash(draft)
    submission = load_json(draft.with_suffix(".submission.json"))
    if isinstance(submission, dict) and isinstance(submission.get("human_author_revision"), dict):
        raise ChapterCoeditError("human-final phase has started; submitted human prose permits read-only advice")
    for task_file in (root / "50_workbench" / "human_author_revisions" / f"ch{chapter_number:03d}").glob(
        "*.task.json"
    ):
        task = load_json(task_file)
        if (
            isinstance(task, dict)
            and task.get("schema") == HUMAN_REVISION_TASK_SCHEMA
            and task.get("source_candidate_sha256") == draft_hash
        ):
            raise ChapterCoeditError(
                "human-final phase has started; AI advice is read-only until a new AI candidate resets the stage"
            )
    gate = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    payload = load_json(gate)
    if isinstance(payload, dict):
        raw_counts = payload.get("severity_counts")
        counts: dict[str, Any] = raw_counts if isinstance(raw_counts, dict) else {}
        if int(counts.get("P0") or 0) + int(counts.get("P1") or 0) > 0:
            raise ChapterCoeditError(
                "current candidate has P0/P1; use the immutable repair plan and two-round budget"
            )


def ensure_session_current(root: Path, session: dict[str, Any]) -> None:
    chapter_number = int(session.get("chapter_number") or 0)
    if session.get("status") == "stale":
        raise ChapterCoeditError("coedit session is stale")
    basis = load_current_story_brief_binding(root, chapter_number)
    intent = require_current_human_chapter_intent(root, chapter_number)
    if (
        session.get("story_brief_basis_sha256") != basis["story_brief_basis_sha256"]
        or session.get("human_chapter_intent_sha256") != intent["sha256"]
    ):
        raise ChapterCoeditError("coedit session is stale for the current intent or Story Brief")


def session_is_stale(root: Path, session: dict[str, Any]) -> bool:
    if session.get("status") == "stale":
        return True
    chapter_number = int(session.get("chapter_number") or 0)
    try:
        basis = load_current_story_brief_binding(root, chapter_number)
        intent = require_current_human_chapter_intent(root, chapter_number)
    except ValueError:
        return True
    return (
        session.get("story_brief_basis_sha256") != basis.get("story_brief_basis_sha256")
        or session.get("human_chapter_intent_sha256") != intent.get("sha256")
    )


def ensure_turn_candidate_current(root: Path, session: dict[str, Any], turn: dict[str, Any]) -> None:
    """A recorded answer applies only to the exact candidate it discussed."""
    candidate = resolve_inside(root, str(turn.get("candidate_file") or ""), root)
    expected = str(turn.get("candidate_sha256") or "")
    if not expected or expected != session.get("current_candidate_sha256") or file_hash(candidate) != expected:
        raise ChapterCoeditError("coedit advice is stale for the current candidate; create a new consultation")


def coedit_candidate_errors(source: Path, candidate: Path) -> list[str]:
    errors: list[str] = []
    if not candidate.is_file() or not candidate.read_text(encoding="utf-8").strip():
        return ["coedit candidate is empty"]
    if b"\r" in candidate.read_bytes():
        errors.append("coedit candidate must use LF line endings")
    text = candidate.read_text(encoding="utf-8")
    if text == source.read_text(encoding="utf-8"):
        errors.append("coedit candidate must change the selected full candidate")
    residue = ("TODO", "写作说明", "作为AI", "作为 AI", "prompt", "任务要求")
    if any(token.casefold() in text.casefold() for token in residue):
        errors.append("coedit candidate contains prompt or task residue")
    return errors


def render_advisor_task(**values: Any) -> str:
    from longform_engine.agent_protocols import DESIGN_REQUIRED_HEADINGS

    return "\n".join(
        [
            f"# ch{values['chapter_number']:03d} 人类主导协作顾问",
            "",
            "你是 human_author_advisor，只提供选择，不改正文。",
            f"- 当前候选：`{values['candidate']}`",
            f"- 人类意图：`{values['intent_file']}`",
            f"- Story Brief：`{values['story_brief']}`",
            f"- 圈选与问题：`{values['request_file']}`",
            "",
            "使用 design_document_v1，并依次保留以下二级标题："
            + "、".join(DESIGN_REQUIRED_HEADINGS["human_review_consult"]) + "。",
            "在“可选修法”下给出 2-3 个三级标题，ID 必须为 `OPTION-A`、`OPTION-B` 或 `OPTION-C`。",
            "每个方案说明对人物选择、情绪归属、读者收益和保护项的影响。不得输出检测器、平台配额或直接改稿。",
            f"输出：`{values['response_file']}`",
            "",
        ]
    )


def session_turn_for_response(
    root: Path, response: Path
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    path = response.parent / "session.json"
    session = load_json(path)
    if isinstance(session, dict) and session.get("schema") == SESSION_SCHEMA:
        for turn_value in session.get("turns") or []:
            turn_path = root / str(turn_value)
            turn = load_json(turn_path)
            if isinstance(turn, dict) and root / str(turn.get("response_file") or "") == response:
                return path, session, turn_path, turn
    raise ChapterCoeditError("coedit response has no current session turn")


def session_turn_for_rewrite(
    root: Path, manifest: dict[str, Any]
) -> tuple[Path, dict[str, Any], Path, dict[str, Any]]:
    task_id = str(manifest.get("task_id") or "")
    for session_file in coedit_root(root).glob("ch*/*/session.json"):
        session = load_json(session_file)
        if not isinstance(session, dict):
            continue
        for turn_value in session.get("turns") or []:
            turn_path = root / str(turn_value)
            turn = load_json(turn_path)
            if isinstance(turn, dict) and turn.get("rewrite_task_id") == task_id:
                return session_file, session, turn_path, turn
    raise ChapterCoeditError("coedit rewrite task has no session lineage")


def load_session_by_id(root: Path, chapter_number: int, session_id: str) -> tuple[Path, dict[str, Any]]:
    path = coedit_root(root) / f"ch{chapter_number:03d}" / safe_token(session_id) / "session.json"
    payload = load_json(path)
    if not isinstance(payload, dict) or payload.get("schema") != SESSION_SCHEMA or payload.get("session_id") != session_id:
        raise ChapterCoeditError("coedit session does not exist")
    return path, payload


def load_turn(root: Path, session: dict[str, Any], turn_number: int) -> tuple[Path, dict[str, Any]]:
    for value in session.get("turns") or []:
        path = root / str(value)
        payload = load_json(path)
        if isinstance(payload, dict) and payload.get("turn_number") == turn_number:
            return path, payload
    raise ChapterCoeditError("coedit turn does not exist")


def coedit_root(root: Path) -> Path:
    return root / "50_workbench" / "human_story_reviews" / "consultations" / "coedit"


def resolve_inside(root: Path, value: str | Path, parent: Path) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(parent.resolve())
    except ValueError as exc:
        raise ChapterCoeditError("coedit path escapes its controlled workbench") from exc
    return resolved


def load_json(path: Path) -> Any:
    try:
        return json.loads(native_filesystem_path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeError):
        return None


def build_text_anchor(
    text: str,
    *,
    start: int,
    end: int,
    source_sha256: str,
) -> dict[str, Any]:
    """Bind a UI selection using Unicode code-point offsets and paragraph identity."""

    if start < 0 or end <= start or end > len(text):
        raise ChapterCoeditError("text anchor must satisfy 0 <= start < end <= source length")
    paragraph_start = text.rfind("\n\n", 0, start)
    paragraph_start = 0 if paragraph_start < 0 else paragraph_start + 2
    paragraph_end = text.find("\n\n", end)
    paragraph_end = len(text) if paragraph_end < 0 else paragraph_end
    paragraph = text[paragraph_start:paragraph_end]
    selected = text[start:end]
    paragraph_index = text[:paragraph_start].count("\n\n") + 1
    return {
        "schema": TEXT_ANCHOR_SCHEMA,
        "offset_unit": "unicode_codepoint",
        "source_sha256": source_sha256,
        "start": start,
        "end": end,
        "text": selected,
        "selected_sha256": sha256(selected.encode("utf-8")).hexdigest(),
        "paragraph_id": f"p{paragraph_index:04d}-{sha256(paragraph.encode('utf-8')).hexdigest()[:12]}",
        "paragraph_sha256": sha256(paragraph.encode("utf-8")).hexdigest(),
        "paragraph_start": paragraph_start,
        "paragraph_end": paragraph_end,
        "context_before": text[max(paragraph_start, start - 80):start],
        "context_after": text[end:min(paragraph_end, end + 80)],
    }


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def file_hash(path: Path) -> str:
    path = native_filesystem_path(path)
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def json_hash(payload: Any) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).strip("-")[:96]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "ChapterCoeditCandidateValidateResult",
    "ChapterCoeditError",
    "ChapterCoeditRecordResult",
    "ChapterCoeditResponseValidateResult",
    "ChapterCoeditRewriteTaskResult",
    "ChapterCoeditTurnResult",
    "SESSION_SCHEMA",
    "TASK_TYPE",
    "TEXT_ANCHOR_SCHEMA",
    "TURN_SCHEMA",
    "build_text_anchor",
    "coedit_provenance",
    "coedit_status",
    "create_chapter_coedit_rewrite_task",
    "create_chapter_coedit_turn",
    "mark_stale_coedit_sessions",
    "record_chapter_coedit_response",
    "record_coedit_submission",
    "validate_chapter_coedit_candidate",
    "validate_chapter_coedit_response",
]
