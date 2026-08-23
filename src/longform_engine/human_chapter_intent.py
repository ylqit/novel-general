"""Human-authored chapter intent recorded before an Agent receives prose instructions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.agent_tasks import mark_tasks_for_chapter_type
from longform_engine.chapter_contract import load_verified_chapter_contract
from longform_engine.config import ConfigDocument
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root


SCHEMA = "human_chapter_intent_v2"
VALIDATION_SCHEMA = "human_chapter_intent_validation_v1"
INTENT_FIELDS = (
    "story_intent",
    "key_character_choice",
    "emotional_truth",
    "pov_voice_intent",
)


class HumanChapterIntentError(ValueError):
    """Raised when human intent is missing, stale, or not genuinely completed."""


@dataclass(frozen=True)
class HumanChapterIntentTaskResult:
    chapter_number: int
    task_file: str
    candidate_file: str
    chapter_contract_sha256: str
    plot_node_approval_sha256: str
    next_command: str


@dataclass(frozen=True)
class HumanChapterIntentValidateResult:
    chapter_number: int
    ok: bool
    report_file: str
    errors: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class HumanChapterIntentApplyResult:
    chapter_number: int
    intent_file: str
    intent_sha256: str
    transaction_report: str
    next_command: str


def human_chapter_intent_paths(root: Path, chapter_number: int) -> dict[str, Path]:
    token = f"ch{chapter_number:03d}"
    workbench = root / "50_workbench" / "chapter_intents"
    return {
        "task": workbench / f"{token}.task.md",
        "candidate": workbench / f"{token}.candidate.json",
        "validation": workbench / f"{token}.candidate.validation.json",
        "canonical": root / "20_outline" / "chapter_intents" / f"{token}.json",
    }


def create_human_chapter_intent_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> HumanChapterIntentTaskResult:
    """Create a blank human form with only immutable bindings pre-populated."""

    if chapter_number <= 0:
        raise HumanChapterIntentError("chapter_number must be positive")
    root = resolve_project_root(config)
    _contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    node_approval = current_plot_node_approval_binding(root, chapter_number)
    paths = human_chapter_intent_paths(root, chapter_number)
    candidate = {
        "schema": SCHEMA,
        "chapter_number": chapter_number,
        "chapter_contract_sha256": contract_hash,
        "plot_node_approval_sha256": node_approval["sha256"],
        "story_intent": "",
        "key_character_choice": "",
        "emotional_truth": "",
        "pov_voice_intent": "",
        "protected_items": [],
        "completed_by": "",
        "status": "draft",
        "approved_by": "",
        "approved_at": "",
    }
    existing = load_json(paths["candidate"])
    reusable = (
        isinstance(existing, dict)
        and existing.get("schema") == SCHEMA
        and existing.get("chapter_number") == chapter_number
        and existing.get("chapter_contract_sha256") == contract_hash
        and existing.get("plot_node_approval_sha256") == node_approval["sha256"]
        and existing.get("status") == "draft"
    )
    if not reusable:
        write_json(paths["candidate"], candidate)
    atomic_write_text(
        paths["task"],
        "\n".join(
            [
                f"# ch{chapter_number:03d} 人类创作意图",
                "",
                "此表必须由人类从空白创作字段开始填写，CLI 只预填章节、方向与合同绑定。",
                "不得让前端、Agent 或模板代填故事意图、人物选择、情绪真相或 POV 声音意图。",
                "",
                f"- 表单：`{relative(root, paths['candidate'])}`",
                "- 必填：故事意图、人物关键选择、情绪真相、POV 声音意图、保护项。",
                "- completed_by 必须为 human；status 保持 draft，批准由 CLI 事务化写入。",
                "",
                "完成后运行：",
                f"`longform-engine chapter human-intent-validate project.yaml --chapter {chapter_number} "
                f"--file {relative(root, paths['candidate'])}`",
                "",
            ]
        ),
    )
    return HumanChapterIntentTaskResult(
        chapter_number=chapter_number,
        task_file=relative(root, paths["task"]),
        candidate_file=relative(root, paths["candidate"]),
        chapter_contract_sha256=contract_hash,
        plot_node_approval_sha256=node_approval["sha256"],
        next_command=(
            f"longform-engine chapter human-intent-validate project.yaml --chapter {chapter_number} "
            f"--file {relative(root, paths['candidate'])}"
        ),
    )


def validate_human_chapter_intent(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> HumanChapterIntentValidateResult:
    root = resolve_project_root(config)
    paths = human_chapter_intent_paths(root, chapter_number)
    candidate = resolve_inside(root, file_path, paths["candidate"].parent)
    if candidate != paths["candidate"].resolve():
        raise HumanChapterIntentError(
            f"human chapter intent candidate must be {relative(root, paths['candidate'])}"
        )
    payload = load_json(candidate)
    errors = human_chapter_intent_errors(root, chapter_number, payload, expect_status="draft")
    report = {
        "schema": VALIDATION_SCHEMA,
        "chapter_number": chapter_number,
        "ok": not errors,
        "candidate_file": relative(root, candidate),
        "candidate_sha256": file_hash(candidate),
        "errors": errors,
        "validated_at": utc_now(),
    }
    write_json(paths["validation"], report)
    next_command = (
        f"longform-engine chapter human-intent-apply project.yaml --chapter {chapter_number} "
        f"--file {relative(root, candidate)} --approved-by human"
        if not errors
        else f"longform-engine chapter human-intent-task project.yaml --chapter {chapter_number}"
    )
    return HumanChapterIntentValidateResult(
        chapter_number=chapter_number,
        ok=not errors,
        report_file=relative(root, paths["validation"]),
        errors=tuple(errors),
        next_command=next_command,
    )


def apply_human_chapter_intent(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
    approved_by: str,
) -> HumanChapterIntentApplyResult:
    if approved_by != "human":
        raise HumanChapterIntentError("human chapter intent apply requires approved_by=human")
    root = resolve_project_root(config)
    paths = human_chapter_intent_paths(root, chapter_number)
    candidate = resolve_inside(root, file_path, paths["candidate"].parent)
    if candidate != paths["candidate"].resolve():
        raise HumanChapterIntentError(
            f"human chapter intent candidate must be {relative(root, paths['candidate'])}"
        )
    validation = load_json(paths["validation"])
    errors = human_chapter_intent_errors(
        root,
        chapter_number,
        load_json(candidate),
        expect_status="draft",
    )
    if (
        not isinstance(validation, dict)
        or validation.get("schema") != VALIDATION_SCHEMA
        or validation.get("ok") is not True
        or validation.get("candidate_file") != relative(root, candidate)
        or validation.get("candidate_sha256") != file_hash(candidate)
    ):
        errors.append("current human chapter intent validation is missing or stale")
    if errors:
        raise HumanChapterIntentError("invalid human chapter intent: " + "; ".join(errors))
    approved = dict(load_json(candidate))
    approved.update(
        {
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": utc_now(),
        }
    )
    touched: list[str | Path] = [
        paths["canonical"],
        root / "50_workbench" / "agent_tasks",
    ]
    with apply_transaction(
        root,
        command="chapter human-intent-apply",
        chapter_number=chapter_number,
        source_paths=(candidate, paths["validation"]),
        touched_paths=touched,
        metadata={
            "approved_by": approved_by,
            "candidate_sha256": file_hash(candidate),
        },
    ) as transaction:
        write_json(paths["canonical"], approved)
        mark_tasks_for_chapter_type(
            root,
            chapter_number=chapter_number,
            task_types=("chapter_write", "chapter_coedit_rewrite", "human_review_consult"),
            to_status="superseded",
            command="chapter human-intent-apply",
            artifact=paths["canonical"],
            from_statuses=("awaiting_agent", "submitted", "validated", "invalid"),
        )
    return HumanChapterIntentApplyResult(
        chapter_number=chapter_number,
        intent_file=relative(root, paths["canonical"]),
        intent_sha256=file_hash(paths["canonical"]),
        transaction_report=relative(root, transaction.report_file),
        next_command=f"longform-engine continue-write project.yaml --chapter {chapter_number}",
    )


def require_current_human_chapter_intent(root: Path, chapter_number: int) -> dict[str, Any]:
    paths = human_chapter_intent_paths(root, chapter_number)
    payload = load_json(paths["canonical"])
    errors = human_chapter_intent_errors(root, chapter_number, payload, expect_status="approved")
    if errors:
        raise HumanChapterIntentError(
            "current human_chapter_intent_v2 is missing or stale: " + "; ".join(errors)
        )
    return {
        "payload": payload,
        "path": relative(root, paths["canonical"]),
        "sha256": file_hash(paths["canonical"]),
    }


def human_chapter_intent_status(root: Path, chapter_number: int) -> dict[str, Any]:
    try:
        binding = require_current_human_chapter_intent(root, chapter_number)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "required": True,
            "status": "stale" if human_chapter_intent_paths(root, chapter_number)["canonical"].exists() else "pending",
            "chapter_number": chapter_number,
            "reason": str(exc),
        }
    return {
        "required": True,
        "status": "current",
        "chapter_number": chapter_number,
        "intent_file": binding["path"],
        "human_chapter_intent_sha256": binding["sha256"],
    }


def current_plot_node_approval_binding(root: Path, chapter_number: int) -> dict[str, str]:
    load_verified_chapter_contract(root, chapter_number)
    path = root / "20_outline" / "plot_nodes" / f"ch{chapter_number:03d}.json"
    payload = load_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "plot_node_table_v1"
        or payload.get("chapter_number") != chapter_number
        or not str(payload.get("approval_sha256") or "")
        or any(
            not isinstance(node, dict)
            or (node.get("human_decision") or {}).get("decision") not in {"approve", "adjust"}
            for node in payload.get("nodes") or []
        )
    ):
        raise HumanChapterIntentError("plot node table is missing, stale, or not fully human-approved")
    return {"path": relative(root, path), "sha256": file_hash(path)}


def human_chapter_intent_errors(
    root: Path,
    chapter_number: int,
    payload: Any,
    *,
    expect_status: str,
) -> list[str]:
    fields = {
        "schema",
        "chapter_number",
        "chapter_contract_sha256",
        "plot_node_approval_sha256",
        *INTENT_FIELDS,
        "protected_items",
        "completed_by",
        "status",
        "approved_by",
        "approved_at",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        return ["intent must contain exactly the human_chapter_intent_v2 fields"]
    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}; v0.8 human workflow records are not accepted")
    if payload.get("chapter_number") != chapter_number:
        errors.append("chapter_number does not match")
    try:
        _contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
        node_approval = current_plot_node_approval_binding(root, chapter_number)
    except ValueError as exc:
        errors.append(str(exc))
        contract_hash = ""
        node_approval = {"sha256": ""}
    if payload.get("chapter_contract_sha256") != contract_hash:
        errors.append("chapter_contract_sha256 is stale")
    if payload.get("plot_node_approval_sha256") != node_approval["sha256"]:
        errors.append("plot_node_approval_sha256 is stale")
    for field in INTENT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or len(value.strip()) < 8:
            errors.append(f"{field} must contain a specific human-authored statement")
    protected = payload.get("protected_items")
    if (
        not isinstance(protected, list)
        or not protected
        or any(not isinstance(item, str) or len(item.strip()) < 3 for item in protected)
    ):
        errors.append("protected_items must contain at least one specific protection")
    if payload.get("completed_by") != "human":
        errors.append("completed_by must be human")
    if payload.get("status") != expect_status:
        errors.append(f"status must be {expect_status}")
    if expect_status == "draft" and (payload.get("approved_by") or payload.get("approved_at")):
        errors.append("draft intent cannot pre-fill approval fields")
    if expect_status == "approved" and (
        payload.get("approved_by") != "human" or not str(payload.get("approved_at") or "").strip()
    ):
        errors.append("approved intent requires human approval evidence")
    return errors


def resolve_inside(root: Path, value: str | Path, expected_parent: Path) -> Path:
    path = Path(value)
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(expected_parent.resolve())
    except ValueError as exc:
        raise HumanChapterIntentError("human chapter intent path escapes its controlled directory") from exc
    return resolved


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "HumanChapterIntentApplyResult",
    "HumanChapterIntentError",
    "HumanChapterIntentTaskResult",
    "HumanChapterIntentValidateResult",
    "SCHEMA",
    "apply_human_chapter_intent",
    "create_human_chapter_intent_task",
    "human_chapter_intent_paths",
    "human_chapter_intent_status",
    "require_current_human_chapter_intent",
    "validate_human_chapter_intent",
]
