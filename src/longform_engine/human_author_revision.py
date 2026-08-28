"""Evidence-bound human author revision between AI review and final review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import unicodedata
from typing import Any

from longform_engine.agent_protocols import (
    EVIDENCE_REVIEW_SCHEMA,
    build_validation_report,
    output_protocol_for_task,
    validate_evidence_review,
    validate_review_evidence_for_sources,
)
from longform_engine.agent_tasks import (
    build_manifest,
    list_manifests,
    mark_tasks_for_output,
    manifest_output,
    validate_current_task_result,
    write_manifest,
)
from longform_engine.chapter_contract import load_verified_chapter_contract
from longform_engine.chapter_coedit import coedit_provenance
from longform_engine.config import ConfigDocument
from longform_engine.human_chapter_intent import require_current_human_chapter_intent
from longform_engine.repair_coordination import ensure_candidate_snapshot, human_review_bundle_binding
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path
from longform_engine.story_brief import load_current_story_brief_binding


SCHEMA = "human_author_revision_v4"
TASK_SCHEMA = "human_author_revision_task_v4"
VALIDATION_SCHEMA = "human_author_revision_validation_v4"
FINAL_LOCK_SCHEMA = "human_final_lock_v1"
SEMANTIC_TASK_TYPE = "prose_revision_semantic_review"
IMPACT_DIMENSIONS = frozenset(
    {
        "scene_causality",
        "character_voice_or_emotion",
        "reader_payoff_or_exit",
        "relationship_logic",
        "pacing_or_information",
        "prose_naturalness",
    }
)
CORE_IMPACT_DIMENSIONS = frozenset({"scene_causality", "character_voice_or_emotion"})
INTENT_REFERENCE_FIELDS = frozenset(
    {
        "story_intent",
        "key_character_choice",
        "emotional_truth",
        "pov_voice_intent",
    }
)
PROTECTED_CONSTRAINTS = frozenset(
    {
        "chapter_contract",
        "knowledge_boundaries",
        "ability_costs",
        "relationship_stage",
        "protected_outcomes",
    }
)
SEMANTIC_DIMENSIONS = frozenset(
    {
        "chapter_contract_preservation",
        "knowledge_boundary_preservation",
        "ability_cost_preservation",
        "relationship_stage_preservation",
        "protected_outcome_preservation",
        "revision_goal_achievement",
    }
)
SEMANTIC_FINDING_CODES = frozenset(
    {
        "PROSE_REVISION_FACT_DRIFT",
        "PROSE_REVISION_KNOWLEDGE_DRIFT",
        "PROSE_REVISION_ABILITY_COST_DRIFT",
        "PROSE_REVISION_RELATIONSHIP_DRIFT",
        "PROSE_REVISION_PROTECTED_OUTCOME_DRIFT",
        "PROSE_REVISION_NOT_SUBSTANTIVE",
    }
)


class HumanAuthorRevisionError(ValueError):
    """Raised when a human revision is absent, stale, trivial, or unsafe."""


@dataclass(frozen=True)
class HumanAuthorRevisionTaskResult:
    chapter_number: int
    task_file: str
    task_record_file: str
    source_file: str
    source_sha256: str
    candidate_file: str
    record_file: str
    review_bundle_file: str
    review_bundle_sha256: str
    story_brief_basis_sha256: str
    human_chapter_intent_sha256: str
    coedit_provenance_sha256: str
    final_lock_file: str
    next_command: str


@dataclass(frozen=True)
class HumanAuthorRevisionValidateResult:
    chapter_number: int
    ok: bool
    stage: str
    validation_file: str
    errors: tuple[str, ...]
    semantic_task_file: str
    next_command: str


def create_human_author_revision_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> HumanAuthorRevisionTaskResult:
    """Freeze the reviewed AI candidate and prepare a human-only full revision workspace."""

    if chapter_number <= 0:
        raise HumanAuthorRevisionError("chapter_number must be positive")
    root = resolve_project_root(config)
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft.is_file():
        raise HumanAuthorRevisionError("current chapter draft is missing")
    from longform_engine.repair_coordination import review_barrier_status

    barrier = review_barrier_status(config, chapter_number=chapter_number)
    barrier_status = str(barrier.get("status") or "")
    repair_task: dict[str, Any] | None = None
    if barrier_status == "review_bundle_ready":
        repair_tasks = [
            task
            for task in list_manifests(root, chapter_number=chapter_number)
            if task.get("task_type") == "repair"
            and str(task.get("status") or "") in {"awaiting_agent", "invalid"}
            and str(manifest_output(task).get("path") or "").endswith(".human.md")
        ]
        if len(repair_tasks) != 1:
            raise HumanAuthorRevisionError(
                "repair-bound human revision requires exactly one active repair task created with agent=human"
            )
        repair_task = repair_tasks[0]
    elif barrier_status != "awaiting_human_story_review":
        blockers = "; ".join(str(item) for item in barrier.get("blockers") or [])
        raise HumanAuthorRevisionError(
            "human author revision requires the current AI candidate to complete every independent review"
            + (f": {blockers}" if blockers else f"; current state is {barrier_status or 'unknown'}")
        )
    source_snapshot = ensure_candidate_snapshot(root, chapter_number=chapter_number)
    source_hash = file_hash(source_snapshot)
    bundle = human_review_bundle_binding(config, chapter_number=chapter_number, freeze=True)
    bundle_hash = str(bundle["review_bundle_sha256"])
    story_brief = load_current_story_brief_binding(root, chapter_number)
    story_brief_basis_hash = str(story_brief["story_brief_basis_sha256"])
    human_intent = require_current_human_chapter_intent(root, chapter_number)
    human_intent_hash = str(human_intent["sha256"])
    coedit = coedit_provenance(root, chapter_number)
    coedit_hash = str(coedit["sha256"])
    directory = revision_root(root) / f"ch{chapter_number:03d}"
    token = revision_token(source_hash, story_brief_basis_hash)
    task_file = directory / f"{token}.task.md"
    task_record = directory / f"{token}.task.json"
    candidate_file = (
        root / str(manifest_output(repair_task).get("path") or "")
        if repair_task is not None
        else directory / f"{token}.human.md"
    )
    record_file = directory / f"{token}.record.json"
    semantic_output = directory / f"{token}.semantic_review.json"
    record_snapshot = directory / f"{token}.semantic_record.json"
    validation_file = directory / f"{token}.validation.json"
    final_lock_file = directory / f"{token}.final_lock.json"
    task_payload = {
        "schema": TASK_SCHEMA,
        "chapter_number": chapter_number,
        "source_file": relative(root, source_snapshot),
        "source_candidate_sha256": source_hash,
        "review_bundle_file": str(bundle["review_bundle"]),
        "review_bundle_sha256": bundle_hash,
        "story_brief_basis_sha256": story_brief_basis_hash,
        "human_chapter_intent_sha256": human_intent_hash,
        "coedit_provenance_sha256": coedit_hash,
        "candidate_file": relative(root, candidate_file),
        "record_file": relative(root, record_file),
        "semantic_output_file": relative(root, semantic_output),
        "semantic_record_file": relative(root, record_snapshot),
        "validation_file": relative(root, validation_file),
        "final_lock_file": relative(root, final_lock_file),
        "repair_task_id": str(repair_task.get("task_id") or "") if repair_task is not None else "",
    }
    write_json(task_record, task_payload)
    template = {
        "schema": SCHEMA,
        "chapter_number": chapter_number,
        "source_candidate_sha256": source_hash,
        "review_bundle_sha256": bundle_hash,
        "story_brief_basis_sha256": story_brief_basis_hash,
        "human_chapter_intent_sha256": human_intent_hash,
        "coedit_provenance_sha256": coedit_hash,
        "revision_candidate_sha256": "",
        "semantic_review_sha256": "",
        "impact_dimensions": [],
        "changes": [],
        "protected_confirmations": {
            key: {"preserved": False, "note": ""}
            for key in sorted(PROTECTED_CONSTRAINTS)
        },
        "human_confirmation": {
            "confirmed_by": "human",
            "statement": "",
        },
        "final_lock_confirmation": {
            "locked_by": "human",
            "statement": "",
        },
    }
    if not record_file.exists():
        write_json(record_file, template)
    atomic_write_text(
        task_file,
        "\n".join(
            [
                f"# ch{chapter_number:03d} 人类作者完整修订",
                "",
                f"- 冻结 AI 源稿：`{relative(root, source_snapshot)}`",
                f"- 修订前审稿包：`{bundle['review_bundle']}`",
                f"- 人工完整候选：`{relative(root, candidate_file)}`",
                f"- 修订记录：`{relative(root, record_file)}`",
                "",
                f"- Story Brief basis SHA-256：`{story_brief_basis_hash}`",
                f"- 人类创作意图：`{human_intent['path']}`",
                f"- 协作来源摘要 SHA-256：`{coedit_hash}`",
                "",
                "请编辑完整章节，不直接修改 draft/final。记录至少两个真实影响维度，其中至少一个是场景因果或人物声音/情绪。",
                "每项修改必须给出源稿与人工稿的精确 span、intent_ref、修改意图、预期读者影响和保护项；只改空白、格式或标点不会通过。",
                "提交前必须由人类填写 final_lock_confirmation；锁定后 Agent 只能只读咨询。",
                "若需要改变章节合同、知识边界、能力代价、关系阶段或保护结果，请停止并 redirect。",
                "",
                "完成候选与记录后运行：",
                f"`longform-engine chapter human-revision-validate project.yaml --chapter {chapter_number} "
                f"--file {relative(root, candidate_file)} --record {relative(root, record_file)}`",
                "",
            ]
        ),
    )
    return HumanAuthorRevisionTaskResult(
        chapter_number=chapter_number,
        task_file=relative(root, task_file),
        task_record_file=relative(root, task_record),
        source_file=relative(root, source_snapshot),
        source_sha256=source_hash,
        candidate_file=relative(root, candidate_file),
        record_file=relative(root, record_file),
        review_bundle_file=str(bundle["review_bundle"]),
        review_bundle_sha256=bundle_hash,
        story_brief_basis_sha256=story_brief_basis_hash,
        human_chapter_intent_sha256=human_intent_hash,
        coedit_provenance_sha256=coedit_hash,
        final_lock_file=relative(root, final_lock_file),
        next_command=(
            f"longform-engine chapter human-revision-validate project.yaml --chapter {chapter_number} "
            f"--file {relative(root, candidate_file)} --record {relative(root, record_file)}"
        ),
    )


def validate_human_author_revision(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
    record_path: str | Path,
) -> HumanAuthorRevisionValidateResult:
    """Validate human evidence, then require one isolated dual-prose semantic review."""

    root = resolve_project_root(config)
    task = current_task_record(root, chapter_number)
    candidate = resolve_inside(root, file_path, expected_parent=root / "50_workbench")
    record_file = resolve_inside(root, record_path, expected_parent=revision_root(root))
    expected_candidate = resolve_inside(root, str(task.get("candidate_file") or ""), expected_parent=root / "50_workbench")
    expected_record = resolve_inside(root, str(task.get("record_file") or ""), expected_parent=revision_root(root))
    if candidate != expected_candidate:
        raise HumanAuthorRevisionError(f"human revision candidate must be {relative(root, expected_candidate)}")
    if record_file != expected_record:
        raise HumanAuthorRevisionError(f"human revision record must be {relative(root, expected_record)}")
    record = load_json(record_file)
    errors = human_author_revision_errors(root, task, candidate, record)
    semantic_task_file = ""
    semantic_output = resolve_inside(
        root,
        str(task.get("semantic_output_file") or ""),
        expected_parent=revision_root(root),
    )
    stage = "record_invalid"
    semantic_hash = file_hash(semantic_output) if semantic_output.is_file() else ""
    if not errors:
        if not semantic_output.is_file():
            result = create_human_revision_semantic_task(
                config,
                chapter_number=chapter_number,
                task=task,
                candidate=candidate,
                record_file=record_file,
            )
            semantic_task_file = result["task_file"]
            errors.append("independent prose revision semantic review is missing")
            stage = "semantic_review_pending"
        else:
            semantic_errors = validate_human_revision_semantic_output(
                config,
                chapter_number=chapter_number,
                task=task,
                candidate=candidate,
                semantic_output=semantic_output,
            )
            errors.extend(semantic_errors)
            if semantic_errors:
                stage = "semantic_review_invalid"
            elif not isinstance(record, dict) or record.get("semantic_review_sha256") != semantic_hash:
                errors.append("semantic_review_sha256 must bind the current validated semantic review")
                stage = "semantic_review_hash_unbound"
            else:
                lock_errors = write_human_final_lock(
                    root,
                    task=task,
                    candidate=candidate,
                    record_file=record_file,
                    semantic_output=semantic_output,
                )
                errors.extend(lock_errors)
                stage = "validated" if not lock_errors else "final_lock_invalid"
    validation_file = resolve_inside(
        root,
        str(task.get("validation_file") or ""),
        expected_parent=revision_root(root),
    )
    ok = not errors
    next_command = (
        f"longform-engine draft submit project.yaml --chapter {chapter_number} "
        f"--file {relative(root, candidate)} --agent human --overwrite"
        if ok
        else semantic_next_command(root, chapter_number, semantic_output, candidate, record_file, stage)
    )
    report = {
        "schema": VALIDATION_SCHEMA,
        "chapter_number": chapter_number,
        "ok": ok,
        "stage": stage,
        "source_file": str(task["source_file"]),
        "source_candidate_sha256": str(task["source_candidate_sha256"]),
        "review_bundle_file": str(task["review_bundle_file"]),
        "review_bundle_sha256": str(task["review_bundle_sha256"]),
        "story_brief_basis_sha256": str(task["story_brief_basis_sha256"]),
        "human_chapter_intent_sha256": str(task["human_chapter_intent_sha256"]),
        "coedit_provenance_sha256": str(task["coedit_provenance_sha256"]),
        "candidate_file": relative(root, candidate),
        "revision_candidate_sha256": file_hash(candidate) if candidate.is_file() else "",
        "record_file": relative(root, record_file),
        "record_sha256": file_hash(record_file) if record_file.is_file() else "",
        "semantic_review_file": relative(root, semantic_output),
        "semantic_review_sha256": semantic_hash,
        "final_lock_file": str(task["final_lock_file"]),
        "final_lock_sha256": file_hash(root / str(task["final_lock_file"])),
        "errors": errors,
        "next_command": next_command,
        "validated_at": utc_now(),
    }
    write_json(validation_file, report)
    return HumanAuthorRevisionValidateResult(
        chapter_number=chapter_number,
        ok=ok,
        stage=stage,
        validation_file=relative(root, validation_file),
        errors=tuple(errors),
        semantic_task_file=semantic_task_file,
        next_command=next_command,
    )


def validate_human_author_revision_semantic_result(
    config: ConfigDocument,
    *,
    chapter_number: int,
    semantic_output: str | Path,
) -> HumanAuthorRevisionValidateResult:
    """Dispatch a validated Agent result back to its human revision pair."""

    root = resolve_project_root(config)
    output = resolve_inside(root, semantic_output, expected_parent=revision_root(root))
    task = next(
        (
            payload
            for payload in (load_json(path) for path in output.parent.glob("*.task.json"))
            if isinstance(payload, dict)
            and payload.get("schema") == TASK_SCHEMA
            and payload.get("chapter_number") == chapter_number
            and str(payload.get("semantic_output_file") or "") == relative(root, output)
        ),
        None,
    )
    if not isinstance(task, dict):
        raise HumanAuthorRevisionError("semantic output is not owned by a current human revision task")
    return validate_human_author_revision(
        config,
        chapter_number=chapter_number,
        file_path=str(task["candidate_file"]),
        record_path=str(task["record_file"]),
    )


def create_human_revision_semantic_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    task: dict[str, Any],
    candidate: Path,
    record_file: Path,
) -> dict[str, str]:
    """Create the isolated semantic review only after both human artifacts exist."""

    root = resolve_project_root(config)
    source = resolve_inside(root, str(task["source_file"]), expected_parent=root / "50_workbench" / "candidate_blobs")
    output = resolve_inside(root, str(task["semantic_output_file"]), expected_parent=revision_root(root))
    record_snapshot = resolve_inside(
        root,
        str(task["semantic_record_file"]),
        expected_parent=revision_root(root),
    )
    token = revision_token(
        str(task["source_candidate_sha256"]),
        str(task["story_brief_basis_sha256"]),
    )
    directory = output.parent
    work_order = directory / f"{token}.semantic_review.md"
    manifest_file = directory / f"{token}.semantic_review.agent_task.json"
    contract_file = directory / f"{token}.semantic_contract.json"
    contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    story_brief = load_current_story_brief_binding(root, chapter_number)
    record_payload = load_json(record_file)
    if not isinstance(record_payload, dict):
        raise HumanAuthorRevisionError("human revision record is missing or invalid")
    write_json(record_snapshot, {**record_payload, "semantic_review_sha256": ""})
    write_json(
        contract_file,
        {
            "schema": "prose_revision_contract_context_v1",
            "chapter_number": chapter_number,
            "chapter_contract": contract,
            "chapter_contract_sha256": contract_hash,
            "story_brief_basis_sha256": story_brief["story_brief_basis_sha256"],
            "human_chapter_intent_sha256": task["human_chapter_intent_sha256"],
            "coedit_provenance_sha256": task["coedit_provenance_sha256"],
            "protected_constraints": sorted(PROTECTED_CONSTRAINTS),
        },
    )
    validate_command = (
        f"longform-engine chapter human-revision-validate project.yaml --chapter {chapter_number} "
        f"--file {relative(root, candidate)} --record {relative(root, record_file)}"
    )
    apply_command = (
        f"longform-engine draft submit project.yaml --chapter {chapter_number} "
        f"--file {relative(root, candidate)} --agent human --overwrite"
    )
    atomic_write_text(
        work_order,
        "\n".join(
            [
                f"# Prose Revision Semantic Review ch{chapter_number:03d}",
                "",
                "你是独立的双稿语义保真审稿员，不是改稿者。比较冻结源稿与人类完整改稿。",
                f"- 源稿：`{relative(root, source)}`",
                f"- 人工稿：`{relative(root, candidate)}`",
                f"- 冻结人工记录：`{relative(root, record_snapshot)}`",
                f"- 合同：`{relative(root, contract_file)}`",
                "",
                "coverage 必须精确包含：" + "、".join(sorted(SEMANTIC_DIMENSIONS)) + "。",
                "每个 checked coverage 同时引用源稿和人工稿 span；revision_goal_achievement 必须逐项覆盖记录中的前后 span。不得用改写比例、检测器分数或流畅度代替判断。",
                "P0/P1 只报告合同、知识、能力代价、关系阶段、保护结果漂移，或修订实际上只有格式/标点变化。",
                "finding code 仅限：" + "、".join(sorted(SEMANTIC_FINDING_CODES)) + "。",
                f"只输出 `{EVIDENCE_REVIEW_SCHEMA}` JSON 到 `{relative(root, output)}`。",
                f"校验：`{validate_command}`",
                "不得修改任何正文、Bible、outline、final、RAG、图谱或 SQLite。",
                "",
            ]
        ),
    )
    task_id = f"{SEMANTIC_TASK_TYPE}:ch{chapter_number:03d}:human_author_revision:{token}:v5"
    manifest = build_manifest(
        root,
        task_type=SEMANTIC_TASK_TYPE,
        chapter_number=chapter_number,
        input_files=[work_order, source, candidate, record_snapshot, contract_file],
        allowed_output_paths=[output],
        output_schema=output_protocol_for_task(SEMANTIC_TASK_TYPE),
        validate_command=validate_command,
        apply_command=apply_command,
        failure_next_command=validate_command,
        task_id=task_id,
        context_policy={
            "required_files": [work_order, source, candidate, record_snapshot, contract_file],
            "optional_files": [],
            "forbidden_paths": [
                "40_manuscript/final/",
                "50_workbench/editorial_reviews/",
                "50_workbench/research_inbox/",
                "60_rag/",
                "70_runtime/db/",
            ],
            "compiled_brief": work_order,
            "selection_report": work_order,
        },
    )
    write_manifest(root, manifest, manifest_file)
    return {
        "task_file": relative(root, work_order),
        "manifest_file": relative(root, manifest_file),
        "output_file": relative(root, output),
        "next_command": f"longform-engine agent-task brief project.yaml --task-id {task_id}",
    }


def validate_human_revision_semantic_output(
    config: ConfigDocument,
    *,
    chapter_number: int,
    task: dict[str, Any],
    candidate: Path,
    semantic_output: Path,
) -> list[str]:
    root = resolve_project_root(config)
    source = resolve_inside(root, str(task["source_file"]), expected_parent=root / "50_workbench" / "candidate_blobs")
    record_path = resolve_inside(
        root,
        str(task["record_file"]),
        expected_parent=revision_root(root),
    )
    record_snapshot = resolve_inside(
        root,
        str(task["semantic_record_file"]),
        expected_parent=revision_root(root),
    )
    payload = load_json(semantic_output)
    errors: list[str] = []
    current_record = load_json(record_path)
    frozen_record = load_json(record_snapshot)
    if not isinstance(current_record, dict) or not isinstance(frozen_record, dict):
        errors.append("human revision semantic record snapshot is missing")
    elif {**current_record, "semantic_review_sha256": ""} != frozen_record:
        errors.append("human revision record changed after semantic review was frozen")
    _manifest, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type=SEMANTIC_TASK_TYPE,
        output_path=semantic_output,
        allowed_statuses=("submitted", "validated"),
    )
    errors.extend(control_errors)
    errors.extend(
        validate_evidence_review(
            payload,
            required_dimensions=SEMANTIC_DIMENSIONS,
            allowed_finding_codes=SEMANTIC_FINDING_CODES,
            evidence_id_limits={"revision_goal_achievement": 16},
        )
    )
    source_text = source.read_text(encoding="utf-8") if source.is_file() else ""
    candidate_text = candidate.read_text(encoding="utf-8") if candidate.is_file() else ""
    source_key = relative(root, source)
    candidate_key = relative(root, candidate)
    evidence_records, evidence_errors = validate_review_evidence_for_sources(
        payload,
        sources={source_key: source_text, candidate_key: candidate_text},
    )
    errors.extend(evidence_errors)
    coverage = payload.get("coverage") if isinstance(payload, dict) and isinstance(payload.get("coverage"), dict) else {}
    for dimension in SEMANTIC_DIMENSIONS:
        coverage_record = coverage.get(dimension) if isinstance(coverage, dict) else None
        ids: list[Any] = []
        if isinstance(coverage_record, dict):
            evidence_ids = coverage_record.get("evidence_ids")
            if isinstance(evidence_ids, list):
                ids = evidence_ids
        normalized = [str(item).replace("\\", "/") for item in ids or []]
        if isinstance(coverage_record, dict) and coverage_record.get("status") == "checked" and not (
            any(source_key in item or source.name in item for item in normalized)
            and any(candidate_key in item or candidate.name in item for item in normalized)
        ):
            errors.append(f"coverage.{dimension} must cite both source and human candidate spans")
    goal_coverage = coverage.get("revision_goal_achievement") if isinstance(coverage, dict) else None
    goal_ids = (
        [str(item) for item in goal_coverage.get("evidence_ids") or []]
        if isinstance(goal_coverage, dict)
        else []
    )
    goal_records = [evidence_records[item] for item in goal_ids if item in evidence_records]
    recorded_changes = frozen_record.get("changes") if isinstance(frozen_record, dict) else []
    for index, change in enumerate(recorded_changes if isinstance(recorded_changes, list) else []):
        if not isinstance(change, dict):
            continue
        for side, expected_path in (("before", source_key), ("after", candidate_key)):
            span = change.get(side)
            if not isinstance(span, dict):
                continue
            start = span.get("start")
            end = span.get("end")
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            if not any(
                record.get("source_path") == expected_path
                and int(record.get("start") or 0) < end
                and int(record.get("end") or 0) > start
                for record in goal_records
            ):
                errors.append(
                    f"coverage.revision_goal_achievement must cite changes[{index}].{side}"
                )
    findings_value = payload.get("findings") if isinstance(payload, dict) else None
    findings: list[Any] = findings_value if isinstance(findings_value, list) else []
    blockers = [
        item
        for item in findings
        if isinstance(item, dict) and item.get("severity") in {"P0", "P1"}
    ]
    if isinstance(payload, dict) and payload.get("verdict") != "pass":
        errors.append("human author revision requires semantic verdict=pass")
    if blockers:
        errors.append("human author revision semantic review contains P0/P1 blockers")
    if not errors:
        report_file = semantic_output.with_suffix(".validation.json")
        report = build_validation_report(
            ok=True,
            stage="prose_revision_semantic_validate",
            subject=relative(root, semantic_output),
            errors=[],
            warnings=[],
            blockers=[],
            provenance={
                "purpose": "human_author_revision",
                "chapter_number": chapter_number,
                "source_path": source_key,
                "source_sha256": file_hash(source),
                "candidate_path": candidate_key,
                "candidate_sha256": file_hash(candidate),
                "semantic_review_sha256": file_hash(semantic_output),
                "passed": True,
            },
            next_command=(
                f"longform-engine chapter human-revision-validate project.yaml --chapter {chapter_number} "
                f"--file {candidate_key} --record {task['record_file']}"
            ),
        )
        write_json(report_file, report)
        mark_tasks_for_output(
            root,
            chapter_number=chapter_number,
            output_path=semantic_output,
            to_status="validated",
            command="chapter human-revision-validate",
            result=report_file,
            from_statuses=("submitted", "validated"),
        )
    return errors


def write_human_final_lock(
    root: Path,
    *,
    task: dict[str, Any],
    candidate: Path,
    record_file: Path,
    semantic_output: Path,
) -> list[str]:
    """Create an immutable lock for the exact human-final bytes and their evidence."""

    lock_file = resolve_inside(
        root,
        str(task.get("final_lock_file") or ""),
        expected_parent=revision_root(root),
    )
    record = load_json(record_file)
    confirmation = record.get("final_lock_confirmation") if isinstance(record, dict) else None
    payload = {
        "schema": FINAL_LOCK_SCHEMA,
        "chapter_number": int(task.get("chapter_number") or 0),
        "candidate_file": relative(root, candidate),
        "revision_candidate_sha256": file_hash(candidate),
        "record_sha256": file_hash(record_file),
        "semantic_review_sha256": file_hash(semantic_output),
        "review_bundle_sha256": str(task.get("review_bundle_sha256") or ""),
        "story_brief_basis_sha256": str(task.get("story_brief_basis_sha256") or ""),
        "human_chapter_intent_sha256": str(
            task.get("human_chapter_intent_sha256") or ""
        ),
        "coedit_provenance_sha256": str(task.get("coedit_provenance_sha256") or ""),
        "locked_by": str(confirmation.get("locked_by") or "")
        if isinstance(confirmation, dict)
        else "",
        "statement": str(confirmation.get("statement") or "")
        if isinstance(confirmation, dict)
        else "",
    }
    if lock_file.is_file():
        existing = load_json(lock_file)
        if not isinstance(existing, dict) or {
            key: value for key, value in existing.items() if key != "locked_at"
        } != payload:
            return ["human final lock already exists for different candidate or evidence bytes"]
        return []
    write_json(lock_file, {**payload, "locked_at": utc_now()})
    return []


def human_author_revision_status(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    """Return current-draft revision coverage without trusting mutable pointers."""

    root = resolve_project_root(config)
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft.is_file():
        return {"required": True, "status": "pending", "reason": "current draft is missing"}
    draft_hash = file_hash(draft)
    submission = load_json(draft.with_suffix(".submission.json"))
    binding = submission.get("human_author_revision") if isinstance(submission, dict) else None
    if isinstance(binding, dict):
        errors = human_revision_binding_errors(root, chapter_number, draft_hash, binding)
        if not errors:
            return {
                "required": True,
                "status": "complete",
                "candidate_sha256": draft_hash,
                **binding,
            }
        return {
            "required": True,
            "status": "stale",
            "candidate_sha256": draft_hash,
            "reason": "; ".join(errors),
        }
    try:
        task_file = task_record_path_for_hash(root, chapter_number, draft_hash)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {
            "required": True,
            "status": "pending",
            "candidate_sha256": draft_hash,
            "reason": str(exc),
        }
    task = load_json(task_file)
    if not isinstance(task, dict) or task.get("schema") != TASK_SCHEMA:
        return {"required": True, "status": "pending", "candidate_sha256": draft_hash}
    candidate = root / str(task.get("candidate_file") or "")
    validation = root / str(task.get("validation_file") or "")
    report = load_json(validation)
    if isinstance(report, dict) and report.get("schema") == VALIDATION_SCHEMA and report.get("ok") is True:
        if not human_revision_validation_errors(root, chapter_number, candidate, report):
            return {
                "required": True,
                "status": "validated_for_submit",
                "candidate_sha256": draft_hash,
                "revision_candidate_file": relative(root, candidate),
                "validation_file": relative(root, validation),
                "validation_sha256": file_hash(validation),
                "next_command": (
                    f"longform-engine draft submit project.yaml --chapter {chapter_number} "
                    f"--file {relative(root, candidate)} --agent human --overwrite"
                ),
            }
    return {
        "required": True,
        "status": "awaiting_human_candidate" if not candidate.is_file() else "validation_pending",
        "candidate_sha256": draft_hash,
        "task_file": relative(root, task_file),
        "candidate_file": relative(root, candidate),
        "record_file": str(task.get("record_file") or ""),
    }


def require_validated_human_revision_submission(
    config: ConfigDocument,
    *,
    chapter_number: int,
    candidate_file: Path,
) -> dict[str, Any]:
    """Return the immutable validation binding consumed by draft submit --agent human."""

    root = resolve_project_root(config)
    current_draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not current_draft.is_file():
        raise HumanAuthorRevisionError("current source draft is missing")
    task = current_task_record(root, chapter_number)
    expected_candidate = root / str(task.get("candidate_file") or "")
    if candidate_file.resolve() != expected_candidate.resolve():
        raise HumanAuthorRevisionError("agent=human must submit the validated complete human revision candidate")
    validation_file = root / str(task.get("validation_file") or "")
    report = load_json(validation_file)
    errors = human_revision_validation_errors(root, chapter_number, candidate_file, report)
    if str(task.get("source_candidate_sha256") or "") != file_hash(current_draft):
        errors.append("human revision source candidate is stale")
    if errors:
        raise HumanAuthorRevisionError("human author revision is missing, invalid, or stale: " + "; ".join(errors))
    return {
        "schema": "human_author_revision_submission_binding_v4",
        "validation_file": relative(root, validation_file),
        "validation_sha256": file_hash(validation_file),
        "record_file": str(report["record_file"]),
        "record_sha256": str(report["record_sha256"]),
        "source_candidate_sha256": str(report["source_candidate_sha256"]),
        "review_bundle_sha256": str(report["review_bundle_sha256"]),
        "story_brief_basis_sha256": str(report["story_brief_basis_sha256"]),
        "human_chapter_intent_sha256": str(report["human_chapter_intent_sha256"]),
        "coedit_provenance_sha256": str(report["coedit_provenance_sha256"]),
        "revision_candidate_sha256": str(report["revision_candidate_sha256"]),
        "semantic_review_sha256": str(report["semantic_review_sha256"]),
        "final_lock_file": str(report["final_lock_file"]),
        "final_lock_sha256": str(report["final_lock_sha256"]),
    }


def require_current_human_author_revision(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    status = human_author_revision_status(config, chapter_number=chapter_number)
    if status.get("status") != "complete":
        raise HumanAuthorRevisionError(
            f"chapter ch{chapter_number:03d} requires a current validated human_author_revision_v4 submission"
        )
    return status


def human_author_revision_errors(
    root: Path,
    task: dict[str, Any],
    candidate: Path,
    record: Any,
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema",
        "chapter_number",
        "source_candidate_sha256",
        "review_bundle_sha256",
        "story_brief_basis_sha256",
        "human_chapter_intent_sha256",
        "coedit_provenance_sha256",
        "revision_candidate_sha256",
        "semantic_review_sha256",
        "impact_dimensions",
        "changes",
        "protected_confirmations",
        "human_confirmation",
        "final_lock_confirmation",
    }
    if not isinstance(record, dict) or set(record) != required:
        return ["record must contain exactly the human_author_revision_v4 fields"]
    if record.get("schema") != SCHEMA:
        if record.get("schema") in {"human_author_revision_v1", "human_author_revision_v2"}:
            errors.append(
                "v0.8 human revision records are rejected in v0.9; create a v0.9 project and "
                "import authoritative material manually"
            )
        else:
            errors.append(f"schema must be {SCHEMA}")
    if record.get("chapter_number") != task.get("chapter_number"):
        errors.append("chapter_number does not match the current human revision task")
    source = resolve_inside(root, str(task.get("source_file") or ""), expected_parent=root / "50_workbench" / "candidate_blobs")
    bundle = resolve_inside(root, str(task.get("review_bundle_file") or ""), expected_parent=root / "50_workbench")
    if record.get("source_candidate_sha256") != task.get("source_candidate_sha256") or file_hash(source) != str(task.get("source_candidate_sha256") or ""):
        errors.append("source_candidate_sha256 is stale")
    if record.get("review_bundle_sha256") != task.get("review_bundle_sha256") or file_hash(bundle) != str(task.get("review_bundle_sha256") or ""):
        errors.append("review_bundle_sha256 is stale")
    try:
        story_brief = load_current_story_brief_binding(root, int(task.get("chapter_number") or 0))
    except ValueError as exc:
        errors.append(str(exc))
        story_brief = {}
    if (
        record.get("story_brief_basis_sha256") != task.get("story_brief_basis_sha256")
        or record.get("story_brief_basis_sha256")
        != story_brief.get("story_brief_basis_sha256")
    ):
        errors.append("story_brief_basis_sha256 is stale")
    try:
        human_intent = require_current_human_chapter_intent(
            root, int(task.get("chapter_number") or 0)
        )
    except ValueError as exc:
        errors.append(str(exc))
        human_intent = {}
    if (
        record.get("human_chapter_intent_sha256")
        != task.get("human_chapter_intent_sha256")
        or record.get("human_chapter_intent_sha256") != human_intent.get("sha256")
    ):
        errors.append("human_chapter_intent_sha256 is stale")
    current_coedit = coedit_provenance(root, int(task.get("chapter_number") or 0))
    if (
        record.get("coedit_provenance_sha256") != task.get("coedit_provenance_sha256")
        or record.get("coedit_provenance_sha256") != current_coedit.get("sha256")
    ):
        errors.append("coedit_provenance_sha256 is stale")
    if not candidate.is_file():
        errors.append("human revision candidate is missing")
        candidate_text = ""
    else:
        candidate_text = candidate.read_text(encoding="utf-8")
    if not candidate_text.strip():
        errors.append("human revision candidate is empty")
    elif b"\r" in candidate.read_bytes():
        errors.append("human revision candidate must use LF line endings")
    elif candidate_text != candidate_text.strip() + "\n":
        errors.append("human revision candidate must use canonical manuscript whitespace with one trailing newline")
    if record.get("revision_candidate_sha256") != (file_hash(candidate) if candidate.is_file() else ""):
        errors.append("revision_candidate_sha256 is stale")
    dimensions = record.get("impact_dimensions")
    if not isinstance(dimensions, list) or any(item not in IMPACT_DIMENSIONS for item in dimensions):
        errors.append("impact_dimensions contains an unsupported value")
        dimensions = []
    if len(set(dimensions)) < 2:
        errors.append("human revision requires at least two distinct impact dimensions")
    if not CORE_IMPACT_DIMENSIONS.intersection(str(item) for item in dimensions):
        errors.append("human revision requires scene_causality or character_voice_or_emotion impact")
    source_text = source.read_text(encoding="utf-8") if source.is_file() else ""
    changes = record.get("changes")
    if not isinstance(changes, list) or not changes:
        errors.append("changes must contain precise before/after human edits")
        changes = []
    ids: set[str] = set()
    substantive = False
    observed_dimensions: set[str] = set()
    for index, item in enumerate(changes):
        prefix = f"changes[{index}]"
        fields = {
            "change_id",
            "dimension",
            "before",
            "after",
            "intent_ref",
            "intent",
            "reader_effect",
            "must_preserve",
        }
        if not isinstance(item, dict) or set(item) != fields:
            errors.append(f"{prefix} has invalid fields")
            continue
        change_id = str(item.get("change_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,79}", change_id) or change_id in ids:
            errors.append(f"{prefix}.change_id must be unique and stable")
        ids.add(change_id)
        dimension = str(item.get("dimension") or "")
        if dimension not in IMPACT_DIMENSIONS:
            errors.append(f"{prefix}.dimension is unsupported")
        else:
            observed_dimensions.add(dimension)
        before = validate_span(item.get("before"), source_text, f"{prefix}.before", errors)
        after = validate_span(item.get("after"), candidate_text, f"{prefix}.after", errors)
        if before is not None and after is not None and semantic_characters(before) != semantic_characters(after):
            substantive = True
        if str(item.get("intent_ref") or "") not in {
            *INTENT_REFERENCE_FIELDS,
            "protected_items",
        }:
            errors.append(f"{prefix}.intent_ref must reference one human intent field")
        if not isinstance(item.get("intent"), str) or not item["intent"].strip():
            errors.append(f"{prefix}.intent must be non-empty")
        if not isinstance(item.get("reader_effect"), str) or not item["reader_effect"].strip():
            errors.append(f"{prefix}.reader_effect must be non-empty")
        preserve = item.get("must_preserve")
        if not isinstance(preserve, list) or not preserve or any(not isinstance(value, str) or not value.strip() for value in preserve):
            errors.append(f"{prefix}.must_preserve must contain non-empty protection items")
    if set(dimensions) - observed_dimensions:
        errors.append("each impact dimension must be supported by at least one recorded change")
    if not substantive:
        errors.append("human revision cannot consist only of whitespace, formatting, or punctuation changes")
    confirmations = record.get("protected_confirmations")
    if not isinstance(confirmations, dict) or set(confirmations) != PROTECTED_CONSTRAINTS:
        errors.append("protected_confirmations must cover exactly the five protected constraint groups")
    else:
        for key in PROTECTED_CONSTRAINTS:
            value = confirmations.get(key)
            if (
                not isinstance(value, dict)
                or set(value) != {"preserved", "note"}
                or value.get("preserved") is not True
                or not isinstance(value.get("note"), str)
                or not value["note"].strip()
            ):
                errors.append(f"protected_confirmations.{key} requires preserved=true and a human note")
    confirmation = record.get("human_confirmation")
    if (
        not isinstance(confirmation, dict)
        or set(confirmation) != {"confirmed_by", "statement"}
        or confirmation.get("confirmed_by") != "human"
        or not isinstance(confirmation.get("statement"), str)
        or not confirmation["statement"].strip()
    ):
        errors.append("human_confirmation requires confirmed_by=human and a non-empty statement")
    final_lock = record.get("final_lock_confirmation")
    if (
        not isinstance(final_lock, dict)
        or set(final_lock) != {"locked_by", "statement"}
        or final_lock.get("locked_by") != "human"
        or not isinstance(final_lock.get("statement"), str)
        or not final_lock["statement"].strip()
    ):
        errors.append("final_lock_confirmation requires locked_by=human and a non-empty statement")
    semantic_hash = record.get("semantic_review_sha256")
    if not isinstance(semantic_hash, str):
        errors.append("semantic_review_sha256 must be a string")
    return errors


def human_revision_validation_errors(
    root: Path,
    chapter_number: int,
    candidate: Path,
    report: Any,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict) or report.get("schema") != VALIDATION_SCHEMA or report.get("ok") is not True:
        return ["human revision validation is missing or did not pass"]
    if report.get("chapter_number") != chapter_number:
        errors.append("human revision validation chapter mismatch")
    if not candidate.is_file() or report.get("revision_candidate_sha256") != file_hash(candidate):
        errors.append("human revision candidate changed after validation")
    try:
        story_brief = load_current_story_brief_binding(root, chapter_number)
    except ValueError as exc:
        errors.append(str(exc))
        story_brief = {}
    if report.get("story_brief_basis_sha256") != story_brief.get("story_brief_basis_sha256"):
        errors.append("story_brief_basis_sha256 changed after validation")
    try:
        intent = require_current_human_chapter_intent(root, chapter_number)
    except ValueError as exc:
        errors.append(str(exc))
        intent = {}
    if report.get("human_chapter_intent_sha256") != intent.get("sha256"):
        errors.append("human_chapter_intent_sha256 changed after validation")
    if report.get("coedit_provenance_sha256") != coedit_provenance(
        root, chapter_number
    ).get("sha256"):
        errors.append("coedit provenance changed after validation")
    for field in (
        "record_file",
        "semantic_review_file",
        "review_bundle_file",
        "source_file",
        "final_lock_file",
    ):
        path = root / str(report.get(field) or "")
        hash_field = {
            "record_file": "record_sha256",
            "semantic_review_file": "semantic_review_sha256",
            "review_bundle_file": "review_bundle_sha256",
            "source_file": "source_candidate_sha256",
            "final_lock_file": "final_lock_sha256",
        }[field]
        if not path.is_file() or file_hash(path) != str(report.get(hash_field) or ""):
            errors.append(f"{field} changed after validation")
    return errors


def human_revision_binding_errors(
    root: Path,
    chapter_number: int,
    draft_hash: str,
    binding: dict[str, Any],
) -> list[str]:
    expected = {
        "schema",
        "validation_file",
        "validation_sha256",
        "record_file",
        "record_sha256",
        "source_candidate_sha256",
        "review_bundle_sha256",
        "story_brief_basis_sha256",
        "human_chapter_intent_sha256",
        "coedit_provenance_sha256",
        "revision_candidate_sha256",
        "semantic_review_sha256",
        "final_lock_file",
        "final_lock_sha256",
    }
    if set(binding) != expected or binding.get("schema") != "human_author_revision_submission_binding_v4":
        return ["submission human revision binding is invalid"]
    validation_file = root / str(binding.get("validation_file") or "")
    if not validation_file.is_file() or file_hash(validation_file) != str(binding.get("validation_sha256") or ""):
        return ["submission human revision validation hash is stale"]
    report = load_json(validation_file)
    errors = human_revision_validation_errors(root, chapter_number, root / str(report.get("candidate_file") or ""), report)
    if draft_hash != str(binding.get("revision_candidate_sha256") or ""):
        errors.append("current draft does not match the human revision")
    for field in (
        "record_sha256",
        "source_candidate_sha256",
        "review_bundle_sha256",
        "story_brief_basis_sha256",
        "human_chapter_intent_sha256",
        "coedit_provenance_sha256",
        "revision_candidate_sha256",
        "semantic_review_sha256",
        "final_lock_sha256",
    ):
        if str(binding.get(field) or "") != str(report.get(field) or ""):
            errors.append(f"submission {field} does not match validation")
    return errors


def current_task_record(root: Path, chapter_number: int) -> dict[str, Any]:
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    if not draft.is_file():
        raise HumanAuthorRevisionError("current chapter draft is missing")
    try:
        task_file = task_record_path_for_hash(root, chapter_number, file_hash(draft))
    except ValueError as exc:
        raise HumanAuthorRevisionError(str(exc)) from exc
    task = load_json(task_file)
    if not isinstance(task, dict) or task.get("schema") != TASK_SCHEMA or task.get("chapter_number") != chapter_number:
        raise HumanAuthorRevisionError(
            f"current human revision task is missing; run chapter human-revision-task for ch{chapter_number:03d}"
        )
    return task


def task_record_path_for_hash(root: Path, chapter_number: int, digest: str) -> Path:
    story_brief = load_current_story_brief_binding(root, chapter_number)
    token = revision_token(digest, str(story_brief["story_brief_basis_sha256"]))
    return revision_root(root) / f"ch{chapter_number:03d}" / f"{token}.task.json"


def revision_token(source_sha256: str, story_brief_basis_sha256: str) -> str:
    """Identify a revision workspace by both source prose and its authoring basis."""

    return f"{source_sha256[:12]}.{story_brief_basis_sha256[:12]}"


def semantic_next_command(
    root: Path,
    chapter_number: int,
    semantic_output: Path,
    candidate: Path,
    record: Path,
    stage: str,
) -> str:
    if stage == "semantic_review_pending":
        task = next(
            (
                payload
                for payload in (
                    load_json(path)
                    for path in semantic_output.parent.glob("*.semantic_review.agent_task.json")
                )
                if isinstance(payload, dict)
                and payload.get("task_type") == SEMANTIC_TASK_TYPE
                and str(((payload.get("io") or {}).get("output") or {}).get("path") or "") == relative(root, semantic_output)
            ),
            None,
        )
        if isinstance(task, dict):
            return f"longform-engine agent-task brief project.yaml --task-id {task['task_id']}"
    return (
        f"longform-engine chapter human-revision-validate project.yaml --chapter {chapter_number} "
        f"--file {relative(root, candidate)} --record {relative(root, record)}"
    )


def validate_span(value: Any, text: str, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, dict) or set(value) != {"start", "end", "text"}:
        errors.append(f"{label} must contain exactly start, end, text")
        return None
    start, end = value.get("start"), value.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(text):
        errors.append(f"{label} has invalid bounds")
        return None
    excerpt = text[start:end]
    if value.get("text") != excerpt:
        errors.append(f"{label}.text does not match its file")
        return None
    return excerpt


def semantic_characters(value: str) -> str:
    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", value)
        if not character.isspace() and not unicodedata.category(character).startswith(("P", "S"))
    )


def revision_root(root: Path) -> Path:
    return root / "50_workbench" / "human_author_revisions"


def resolve_inside(root: Path, value: str | Path, *, expected_parent: Path) -> Path:
    raw = Path(value)
    path = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        path.relative_to(root.resolve())
        path.relative_to(expected_parent.resolve())
    except ValueError as exc:
        raise HumanAuthorRevisionError("human revision artifacts must stay in their controlled project lane") from exc
    return path


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
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
    "CORE_IMPACT_DIMENSIONS",
    "HumanAuthorRevisionError",
    "HumanAuthorRevisionTaskResult",
    "HumanAuthorRevisionValidateResult",
    "IMPACT_DIMENSIONS",
    "PROTECTED_CONSTRAINTS",
    "SCHEMA",
    "SEMANTIC_FINDING_CODES",
    "SEMANTIC_TASK_TYPE",
    "create_human_author_revision_task",
    "human_author_revision_status",
    "require_current_human_author_revision",
    "require_validated_human_revision_submission",
    "validate_human_author_revision",
    "validate_human_author_revision_semantic_result",
]
