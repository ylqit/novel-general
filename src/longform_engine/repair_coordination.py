"""Evidence-complete review aggregation and immutable repair-round orchestration."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from longform_engine.agent_protocols import (
    AgentProtocolError,
    output_protocol_for_task,
    parse_design_document,
)
from longform_engine.agent_tasks import (
    build_manifest,
    list_manifests,
    load_manifest,
    update_task_status,
    write_manifest,
)
from longform_engine.config import ConfigDocument
from longform_engine.editorial import editorial_review_required_reasons
from longform_engine.gates import semantic_pacing_review_status
from longform_engine.quality import payoff_review_required_reasons, reader_payoff_review_status
from longform_engine.storage import atomic_write_text, resolve_project_root


REVIEW_BUNDLE_SCHEMA = "repair_review_bundle_v1"
REPAIR_ATTEMPTS_SCHEMA = "repair_attempts_v1"
REPAIR_PLAN_VALIDATION_SCHEMA = "validation_report_v1"
BLOCKING_SEVERITIES = frozenset({"P0", "P1"})
REVIEW_ORDER = ("semantic", "payoff", "pacing", "editorial")
FINDING_ID_PATTERN = re.compile(r"RF-[0-9a-f]{12}")


class RepairCoordinationError(ValueError):
    """Raised when the review barrier or immutable repair contract is violated."""


def review_barrier_status(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    """Describe review completeness separately from content verdicts for one candidate."""

    if chapter_number <= 0:
        raise RepairCoordinationError("chapter_number must be positive")
    root = resolve_project_root(config)
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    gate_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    if not draft.is_file() or not gate_path.is_file():
        return _barrier_result(
            chapter_number,
            candidate_path=relative_path(root, draft),
            candidate_hash="",
            stages={},
            findings=[],
            status="reviews_pending",
            blockers=["current draft and gate_result.json are required"],
        )
    candidate_hash = _file_hash(draft)
    gate = load_json(gate_path, default={})
    if not isinstance(gate, dict) or str(gate.get("source_sha256") or "") != candidate_hash:
        return _barrier_result(
            chapter_number,
            candidate_path=relative_path(root, draft),
            candidate_hash=candidate_hash,
            stages={},
            findings=[],
            status="reviews_pending",
            blockers=["gate result is missing, invalid, or stale for the current candidate"],
        )

    stages = {
        "semantic": _semantic_stage(root, chapter_number, candidate_hash, gate),
        "payoff": _payoff_stage(config, root, chapter_number, candidate_hash),
        "pacing": _pacing_stage(config, root, chapter_number, candidate_hash),
        "editorial": _editorial_stage(config, root, chapter_number, candidate_hash),
    }
    protocol_blockers = [
        f"{name}: {stage['reason']}"
        for name, stage in stages.items()
        if stage.get("required") and not stage.get("complete")
    ]
    findings = _collect_findings(config, root, chapter_number, candidate_hash, gate, stages)
    human_reasons = [
        f"{name}: {stage['reason']}"
        for name, stage in stages.items()
        if stage.get("complete") and stage.get("need_human")
    ]
    blocking = [item for item in findings if item.get("selected") and item.get("severity") in BLOCKING_SEVERITIES]
    if protocol_blockers:
        status = "reviews_pending"
    elif human_reasons:
        status = "need_human"
    elif blocking:
        status = "review_bundle_ready"
    else:
        status = "ready_to_finalize"
    return _barrier_result(
        chapter_number,
        candidate_path=relative_path(root, draft),
        candidate_hash=candidate_hash,
        stages=stages,
        findings=findings,
        status=status,
        blockers=protocol_blockers + human_reasons,
    )


def create_repair_synthesis_task(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    """Freeze complete reviews and register one immutable repair-plan synthesis task."""

    root = resolve_project_root(config)
    barrier = review_barrier_status(config, chapter_number=chapter_number)
    if barrier["status"] == "reviews_pending":
        raise RepairCoordinationError(
            "review barrier is incomplete: " + "; ".join(barrier.get("blockers") or [])
        )
    if barrier["status"] == "need_human":
        raise RepairCoordinationError(
            "review barrier requires human resolution: " + "; ".join(barrier.get("blockers") or [])
        )
    if barrier["status"] != "review_bundle_ready":
        raise RepairCoordinationError("current candidate has no admitted blocking finding to repair")

    round_number = next_repair_round(config, chapter_number=chapter_number)
    if round_number is None:
        raise RepairCoordinationError("repair_budget_exhausted: two submitted content repair rounds already exist")
    round_token = f"r{round_number:02d}"
    plan_dir = root / "50_workbench" / "repair_plans" / f"ch{chapter_number:03d}"
    plan_dir.mkdir(parents=True, exist_ok=True)
    bundle_file = plan_dir / f"{round_token}.review_bundle.json"
    task_file = plan_dir / f"{round_token}.task.md"
    context_file = plan_dir / f"{round_token}.context.json"
    plan_file = plan_dir / f"{round_token}.plan.md"
    manifest_file = plan_dir / f"{round_token}.plan.agent_task.json"
    task_id = f"repair_plan_synthesis:ch{chapter_number:03d}:{round_token}:v4"
    existing = _task_by_id(root, task_id)
    if existing is not None:
        existing_bundle = load_json(bundle_file, default={})
        if (
            isinstance(existing_bundle, dict)
            and str(existing_bundle.get("candidate_sha256") or "") == barrier["candidate_sha256"]
            and bundle_file.is_file()
            and manifest_file.is_file()
        ):
            return {
                "schema": "repair_synthesis_task_result_v1",
                "chapter_number": chapter_number,
                "repair_round": round_number,
                "task_id": task_id,
                "review_bundle": relative_path(root, bundle_file),
                "candidate_snapshot": str(existing_bundle.get("candidate_snapshot") or ""),
                "plan_file": relative_path(root, plan_file),
                "manifest_file": relative_path(root, manifest_file),
                "next_command": f"longform-engine agent-task brief project.yaml {task_id}",
            }
        raise RepairCoordinationError("existing immutable repair synthesis task is stale or incomplete")
    snapshot = ensure_candidate_snapshot(root, chapter_number=chapter_number)

    bundle = {
        "schema": REVIEW_BUNDLE_SCHEMA,
        "chapter_number": chapter_number,
        "repair_round": round_number,
        "candidate_path": barrier["candidate_path"],
        "candidate_sha256": barrier["candidate_sha256"],
        "candidate_snapshot": relative_path(root, snapshot),
        "required_reviews": [name for name in REVIEW_ORDER if barrier["stages"][name]["required"]],
        "completed_reviews": [name for name in REVIEW_ORDER if barrier["stages"][name]["complete"]],
        "findings": barrier["findings"],
        "blocking_finding_ids": barrier["blocking_finding_ids"],
        "selected_finding_ids": [
            str(finding["finding_id"])
            for finding in barrier["findings"]
            if finding.get("selected")
        ],
        "preservation_ledger": _dedupe(
            value
            for finding in barrier["findings"]
            if finding.get("selected")
            for value in finding.get("preserve") or []
        ),
        "generated_at": utc_now(),
    }
    _write_immutable_json(bundle_file, bundle)
    context = _repair_context(root, chapter_number, barrier["candidate_sha256"])
    _write_immutable_json(context_file, context)
    _write_immutable_text(
        task_file,
        "\n".join(
            [
                f"# 修复主编工作单 ch{chapter_number:03d} {round_token}",
                "",
                "只编排修复方案，不写正文，不重新裁决审稿结论。",
                f"候选 SHA-256：`{barrier['candidate_sha256']}`",
                f"修复轮次：`{round_token}`",
                f"阻断 finding：{', '.join(barrier['blocking_finding_ids'])}",
                "",
                "先按事实规则、因果、人物、场景、表达的顺序聚类根因；再确定从最早错误主张到最后依赖结果的最小修改范围。",
                "所有 P0/P1 必须在“完整 blocking finding 清单”中各出现一次，并保留原 finding ID 和严重级别。",
                "若 repair target 与 preservation ledger 冲突，必须在“冲突与 need-human 判断”中明确 need-human。",
                "“冲突与 need-human 判断”必须写 `need-human: yes` 或 `need-human: no`；任何 yes 都会停止修章任务。",
                "",
                f"输出：`{relative_path(root, plan_file)}`",
            ]
        )
        + "\n",
    )
    manifest = build_manifest(
        root,
        task_type="repair_plan_synthesis",
        chapter_number=chapter_number,
        task_id=task_id,
        input_files=[task_file, snapshot, bundle_file, context_file],
        allowed_output_paths=[plan_file],
        output_schema=output_protocol_for_task("repair_plan_synthesis"),
        validate_command=(
            f"longform-engine repair synthesis-validate project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, plan_file)}"
        ),
        apply_command=(
            f"longform-engine repair candidate-task project.yaml --chapter {chapter_number} --agent codex"
        ),
        failure_next_command=(
            f"longform-engine repair synthesis-task project.yaml --chapter {chapter_number}"
        ),
        context_policy={
            "required_files": [task_file, snapshot, bundle_file, context_file],
            "optional_files": [],
            "compiled_brief": task_file,
            "selection_report": task_file,
            "trigger_codes": ["repair_plan_synthesis"],
        },
    )
    write_manifest(root, manifest, manifest_file)
    return {
        "schema": "repair_synthesis_task_result_v1",
        "chapter_number": chapter_number,
        "repair_round": round_number,
        "task_id": task_id,
        "review_bundle": relative_path(root, bundle_file),
        "candidate_snapshot": relative_path(root, snapshot),
        "plan_file": relative_path(root, plan_file),
        "manifest_file": relative_path(root, manifest_file),
        "next_command": f"longform-engine agent-task brief project.yaml {task_id}",
    }


def validate_repair_plan(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> dict[str, Any]:
    """Validate finding completeness, immutable bindings, and preservation conflicts."""

    root = resolve_project_root(config)
    task = _current_task(root, chapter_number, "repair_plan_synthesis")
    if task is None:
        raise RepairCoordinationError("no current repair_plan_synthesis task exists")
    output = Path(file_path)
    if not output.is_absolute():
        output = root / output
    output = output.resolve()
    expected = (root / str((task.get("io") or {}).get("output", {}).get("path") or "")).resolve()
    if output != expected:
        raise RepairCoordinationError(f"repair plan must be written to {relative_path(root, expected)}")
    errors: list[str] = []
    try:
        document = parse_design_document(output.read_text(encoding="utf-8"), expected_type="repair_plan_synthesis")
    except (OSError, UnicodeDecodeError, AgentProtocolError) as exc:
        document = None
        errors.append(str(exc))
    round_number = _round_from_task(str(task.get("task_id") or ""))
    plan_dir = root / "50_workbench" / "repair_plans" / f"ch{chapter_number:03d}"
    bundle_file = plan_dir / f"r{round_number:02d}.review_bundle.json"
    report_file = plan_dir / f"r{round_number:02d}.validation.json"
    if str(task.get("status") or "") == "validated" and report_file.is_file():
        prior = load_json(report_file, default={})
        prior_provenance = prior.get("provenance") if isinstance(prior, dict) else {}
        if (
            isinstance(prior_provenance, dict)
            and prior.get("ok") is True
            and output.is_file()
            and str(prior_provenance.get("plan_sha256") or "") == _file_hash(output)
        ):
            return {**prior, "report_file": relative_path(root, report_file)}
        raise RepairCoordinationError("a validated immutable repair plan cannot be replaced or revalidated")
    bundle = load_json(bundle_file, default={})
    if not isinstance(bundle, dict) or bundle.get("schema") != REVIEW_BUNDLE_SCHEMA:
        errors.append("review bundle is missing or invalid")
        bundle = {}
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    candidate_hash = _file_hash(draft) if draft.is_file() else ""
    if str(bundle.get("candidate_sha256") or "") != candidate_hash:
        errors.append("review bundle is stale for the current candidate")
    blocking_ids = [str(item) for item in bundle.get("blocking_finding_ids") or []]
    selected_ids = [str(item) for item in bundle.get("selected_finding_ids") or blocking_ids]
    admitted_ids = set(selected_ids)
    preserve_conflicts: list[str] = []
    if document is not None:
        complete_list = document.sections["完整 blocking finding 清单"]
        for finding_id in selected_ids:
            if len(re.findall(rf"(?<![0-9a-f]){re.escape(finding_id)}(?![0-9a-f])", complete_list)) != 1:
                errors.append(f"admitted finding {finding_id} must appear exactly once in the complete list")
            root_groups = document.sections["共同根因分组"]
            if len(re.findall(rf"(?<![0-9a-f]){re.escape(finding_id)}(?![0-9a-f])", root_groups)) != 1:
                errors.append(f"admitted finding {finding_id} must map to exactly one root-cause group")
        unknown = sorted(set(FINDING_ID_PATTERN.findall(document.markdown)) - admitted_ids)
        if unknown:
            errors.append("repair plan contains unknown or policy-excluded finding IDs: " + ", ".join(unknown))
        severity_by_id = {
            str(item.get("finding_id") or ""): str(item.get("severity") or "")
            for item in bundle.get("findings") or []
            if isinstance(item, dict)
        }
        for line in complete_list.splitlines():
            ids = FINDING_ID_PATTERN.findall(line)
            for finding_id in ids:
                expected_severity = severity_by_id.get(finding_id)
                declared = re.search(r"\bP[0-3]\b", line)
                if expected_severity and (declared is None or declared.group(0) != expected_severity):
                    errors.append(f"{finding_id} must retain severity {expected_severity} in the complete list")
        preserve = _markdown_items(document.sections["必须保留内容"])
        mutable = _markdown_items(document.sections["允许改变内容"])
        frozen_preserve = [str(item).strip() for item in bundle.get("preservation_ledger") or [] if str(item).strip()]
        declared_preserve_text = re.sub(r"\s+", "", document.sections["必须保留内容"]).lower()
        for protected in frozen_preserve:
            if re.sub(r"\s+", "", protected).lower() not in declared_preserve_text:
                errors.append(f"frozen preservation item is missing from the plan: {protected}")
        preserve_conflicts = _text_conflicts(_dedupe([*frozen_preserve, *preserve]), mutable)
        if preserve_conflicts:
            errors.append("repair target conflicts with preserve ledger: " + "; ".join(preserve_conflicts))
        human_section = document.sections["冲突与 need-human 判断"]
        explicit_human = _need_human_decision(human_section)
        if explicit_human is None:
            errors.append("conflict section must declare exactly one `need-human: yes|no` decision")
        elif explicit_human:
            errors.append("repair coordinator declared need-human")
        binding = document.sections["候选 hash 与修复轮次"]
        if candidate_hash not in binding or f"r{round_number:02d}" not in binding.lower():
            errors.append("candidate hash and immutable repair round must be declared in the binding section")

    ok = not errors
    need_human = bool(
        preserve_conflicts
        or (document is not None and _need_human_decision(document.sections["冲突与 need-human 判断"]) is True)
    )
    next_command = (
        f"longform-engine repair candidate-task project.yaml --chapter {chapter_number} --agent codex"
        if ok
        else (
            f"longform-engine editorial need-human project.yaml --chapter {chapter_number} "
            "--reason repair_target_preserve_conflict"
            if need_human
            else f"longform-engine repair synthesis-task project.yaml --chapter {chapter_number}"
        )
    )
    report = {
        "schema": REPAIR_PLAN_VALIDATION_SCHEMA,
        "ok": ok,
        "stage": "repair_plan_synthesis_validate",
        "subject": relative_path(root, output),
        "errors": errors,
        "warnings": [],
        "blockers": errors,
        "provenance": {
            "chapter_number": chapter_number,
            "repair_round": round_number,
            "candidate_sha256": candidate_hash,
            "review_bundle": relative_path(root, bundle_file),
            "review_bundle_sha256": _file_hash(bundle_file) if bundle_file.is_file() else "",
            "plan_sha256": _file_hash(output) if output.is_file() else "",
            "blocking_finding_ids": blocking_ids,
            "selected_finding_ids": selected_ids,
            "need_human": need_human,
            "preserve_conflicts": preserve_conflicts,
        },
        "next_command": next_command,
    }
    write_json(report_file, report)
    update_task_status(
        root,
        str(task.get("task_id") or ""),
        to_status="validated" if ok else "invalid",
        command="repair synthesis-validate",
        artifact=output,
        result=report_file,
    )
    return {**report, "report_file": relative_path(root, report_file)}


def create_repair_candidate_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    agent: str = "codex",
) -> dict[str, Any]:
    """Register a repair author task bound to one validated immutable plan."""

    root = resolve_project_root(config)
    synthesis = _current_task(root, chapter_number, "repair_plan_synthesis")
    if synthesis is None or str(synthesis.get("status") or "") != "validated":
        raise RepairCoordinationError("repair plan must validate before creating a repair candidate task")
    round_number = _round_from_task(str(synthesis.get("task_id") or ""))
    if next_repair_round(config, chapter_number=chapter_number) != round_number:
        raise RepairCoordinationError("repair plan round is stale or repair budget is exhausted")
    round_token = f"r{round_number:02d}"
    plan_dir = root / "50_workbench" / "repair_plans" / f"ch{chapter_number:03d}"
    plan_file = plan_dir / f"{round_token}.plan.md"
    report_file = plan_dir / f"{round_token}.validation.json"
    report = load_json(report_file, default={})
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    snapshot = root / str(load_json(plan_dir / f"{round_token}.review_bundle.json", default={}).get("candidate_snapshot") or "")
    if not (
        isinstance(report, dict)
        and report.get("ok") is True
        and str((report.get("provenance") or {}).get("candidate_sha256") or "") == _file_hash(draft)
        and str((report.get("provenance") or {}).get("plan_sha256") or "") == _file_hash(plan_file)
        and snapshot.is_file()
        and _file_hash(snapshot) == _file_hash(draft)
    ):
        raise RepairCoordinationError("validated repair plan is stale for the current candidate")
    safe_agent = re.sub(r"[^a-z0-9_-]+", "_", str(agent or "codex").strip().lower()) or "codex"
    candidate_dir = root / "50_workbench" / "repair_candidates"
    candidate_dir.mkdir(parents=True, exist_ok=True)
    task_file = plan_dir / f"{round_token}.repair_task.md"
    candidate_file = candidate_dir / f"ch{chapter_number:03d}.{round_token}.{safe_agent}.md"
    manifest_file = plan_dir / f"{round_token}.repair.agent_task.json"
    task_id = f"repair:ch{chapter_number:03d}:{round_token}:v4"
    _write_immutable_text(
        task_file,
        "\n".join(
            [
                f"# 修章作者工作单 ch{chapter_number:03d} {round_token}",
                "",
                "依据已批准的修复计划写一份完整替代稿。",
                "只改计划允许的最小范围，保留 preservation ledger 中已经通过的内容。",
                "不得新增计划外剧情、改写 canonical 事实或用解释性段落掩盖机制冲突。",
                f"输出：`{relative_path(root, candidate_file)}`",
            ]
        )
        + "\n",
    )
    existing = _task_by_id(root, task_id)
    if existing is None:
        submit_command = (
            f"longform-engine draft submit project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, candidate_file)} --agent {safe_agent} --overwrite"
        )
        manifest = build_manifest(
            root,
            task_type="repair",
            chapter_number=chapter_number,
            task_id=task_id,
            input_files=[task_file, snapshot, plan_file],
            allowed_output_paths=[candidate_file],
            output_schema=output_protocol_for_task("repair"),
            validate_command=submit_command,
            apply_command=f"longform-engine chapter finalize project.yaml --chapter {chapter_number} --approved-by human",
            failure_next_command=(
                f"longform-engine agent-task brief project.yaml {task_id}"
            ),
            context_policy={
                "required_files": [task_file, snapshot, plan_file],
                "optional_files": [],
                "compiled_brief": task_file,
                "selection_report": task_file,
                "trigger_codes": ["repair"],
            },
        )
        write_manifest(root, manifest, manifest_file)
    return {
        "schema": "repair_candidate_task_result_v1",
        "chapter_number": chapter_number,
        "repair_round": round_number,
        "task_id": task_id,
        "candidate_task": relative_path(root, task_file),
        "candidate_draft": relative_path(root, candidate_file),
        "manifest_file": relative_path(root, manifest_file),
        "next_command": f"longform-engine agent-task brief project.yaml {task_id}",
    }


def record_repair_submission(
    config: ConfigDocument,
    *,
    chapter_number: int,
    task_id: str,
    source_path: Path,
) -> dict[str, Any]:
    """Consume one content attempt only after an immutable repair candidate is submitted."""

    if not str(task_id).startswith(f"repair:ch{chapter_number:03d}:r"):
        return repair_attempt_status(config, chapter_number=chapter_number)
    root = resolve_project_root(config)
    round_number = _round_from_task(task_id)
    attempts_file = _attempts_file(root, chapter_number)
    payload = _load_attempts(attempts_file, chapter_number)
    existing = next((item for item in payload["submitted_rounds"] if item.get("task_id") == task_id), None)
    if existing is None:
        expected = len(payload["submitted_rounds"]) + 1
        if round_number != expected or round_number > max_repair_rounds(config):
            raise RepairCoordinationError("repair submission round is out of sequence or exceeds the content budget")
        payload["submitted_rounds"].append(
            {
                "round": round_number,
                "task_id": task_id,
                "source_path": relative_path(root, source_path),
                "source_sha256": _file_hash(source_path),
                "submitted_at": utc_now(),
            }
        )
        payload["updated_at"] = utc_now()
        write_json(attempts_file, payload)
    return repair_attempt_status(config, chapter_number=chapter_number)


def preflight_repair_submission(
    config: ConfigDocument,
    *,
    chapter_number: int,
    task_id: str,
    source_path: Path,
) -> None:
    """Reject stale or over-budget repair output before the active draft is replaced."""

    if not str(task_id).startswith(f"repair:ch{chapter_number:03d}:r"):
        return
    root = resolve_project_root(config)
    payload = _load_attempts(_attempts_file(root, chapter_number), chapter_number)
    existing = next((item for item in payload["submitted_rounds"] if item.get("task_id") == task_id), None)
    if existing is not None:
        if str(existing.get("source_sha256") or "") != _file_hash(source_path):
            raise RepairCoordinationError("a submitted immutable repair round cannot be replaced with different bytes")
        return
    expected = len(payload["submitted_rounds"]) + 1
    round_number = _round_from_task(task_id)
    if round_number != expected or round_number > max_repair_rounds(config):
        raise RepairCoordinationError("repair submission round is stale, out of sequence, or over budget")


def repair_attempt_status(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    root = resolve_project_root(config)
    payload = _load_attempts(_attempts_file(root, chapter_number), chapter_number)
    used = len(payload["submitted_rounds"])
    maximum = max_repair_rounds(config)
    return {
        "schema": REPAIR_ATTEMPTS_SCHEMA,
        "chapter_number": chapter_number,
        "used": used,
        "maximum": maximum,
        "remaining": max(0, maximum - used),
        "exhausted": used >= maximum,
        "submitted_rounds": payload["submitted_rounds"],
    }


def repair_plan_status(config: ConfigDocument, *, chapter_number: int) -> dict[str, Any]:
    """Return the current immutable plan state, including a validated human-conflict marker."""

    root = resolve_project_root(config)
    task = _current_task(root, chapter_number, "repair_plan_synthesis")
    if task is None:
        return {
            "task_id": "",
            "status": "missing",
            "repair_round": 0,
            "need_human": False,
            "report_file": "",
        }
    round_number = _round_from_task(str(task.get("task_id") or ""))
    report_file = (
        root
        / "50_workbench"
        / "repair_plans"
        / f"ch{chapter_number:03d}"
        / f"r{round_number:02d}.validation.json"
    )
    report = load_json(report_file, default={})
    provenance = report.get("provenance") if isinstance(report, dict) and isinstance(report.get("provenance"), dict) else {}
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    current_hash = _file_hash(draft) if draft.is_file() else ""
    report_current = bool(
        isinstance(provenance, dict)
        and current_hash
        and str(provenance.get("candidate_sha256") or "") == current_hash
    )
    return {
        "task_id": str(task.get("task_id") or ""),
        "status": str(task.get("status") or ""),
        "repair_round": round_number,
        "need_human": bool(report_current and provenance.get("need_human")),
        "report_current": report_current,
        "report_file": relative_path(root, report_file) if report_file.is_file() else "",
        "next_command": str(report.get("next_command") or "") if isinstance(report, dict) else "",
    }


def next_repair_round(config: ConfigDocument, *, chapter_number: int) -> int | None:
    status = repair_attempt_status(config, chapter_number=chapter_number)
    return None if status["exhausted"] else int(status["used"]) + 1


def max_repair_rounds(config: ConfigDocument) -> int:
    quality = config.data.get("quality") if isinstance(config.data.get("quality"), dict) else {}
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    value = int(repair.get("max_content_rounds") or 2)
    return min(max(value, 1), 2)


def ensure_candidate_snapshot(root: Path, *, chapter_number: int) -> Path:
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    if not draft.is_file():
        raise RepairCoordinationError("current chapter draft is missing")
    digest = _file_hash(draft)
    snapshot = root / "50_workbench" / "candidate_snapshots" / f"ch{chapter_number:03d}" / f"{digest}.md"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.exists():
        if _file_hash(snapshot) != digest:
            raise RepairCoordinationError("immutable candidate snapshot hash mismatch")
        return snapshot
    _write_immutable_bytes(snapshot, draft.read_bytes())
    return snapshot


def _semantic_stage(root: Path, chapter: int, candidate_hash: str, gate: dict[str, Any]) -> dict[str, Any]:
    state = gate.get("agent_semantic_review") if isinstance(gate.get("agent_semantic_review"), dict) else {}
    required = bool(state.get("required"))
    if not required:
        return {"required": False, "complete": True, "need_human": False, "reason": "not_required"}
    application = load_json(
        root / "50_workbench" / "gate_artifacts" / f"ch{chapter:03d}" / "semantic_review_application.json",
        default={},
    )
    payload = application.get("payload") if isinstance(application, dict) else None
    complete = bool(
        isinstance(payload, dict)
        and application.get("schema") == "semantic_review_application_v1"
        and str(application.get("source_hash") or "") == candidate_hash
    )
    verdict = str(payload.get("verdict") or "") if isinstance(payload, dict) else ""
    return {
        "required": True,
        "complete": complete,
        "need_human": complete and verdict in {"need_human", "insufficient_evidence"},
        "reason": "applied" if complete else "semantic review missing or stale",
        "verdict": verdict,
    }


def _payoff_stage(config: ConfigDocument, root: Path, chapter: int, candidate_hash: str) -> dict[str, Any]:
    required = bool(payoff_review_required_reasons(config, chapter_number=chapter))
    if not required:
        return {"required": False, "complete": True, "need_human": False, "reason": "not_required"}
    status = reader_payoff_review_status(config, chapter_number=chapter)
    report = load_json(root / str(status.get("report_file") or ""), default={})
    provenance = report.get("provenance") if isinstance(report, dict) and isinstance(report.get("provenance"), dict) else {}
    complete = bool(
        report.get("schema") == "validation_report_v1"
        and report.get("ok") is True
        and str(provenance.get("source_hash") or "") == candidate_hash
    )
    review = load_json(root / str(status.get("output_file") or ""), default={})
    verdict = str(review.get("verdict") or "") if isinstance(review, dict) else ""
    return {
        "required": True,
        "complete": complete,
        "need_human": complete and (bool(provenance.get("need_human")) or verdict in {"need_human", "insufficient_evidence"}),
        "reason": "validated" if complete else "payoff review missing, invalid, or stale",
        "verdict": verdict,
    }


def _pacing_stage(config: ConfigDocument, root: Path, chapter: int, candidate_hash: str) -> dict[str, Any]:
    status = semantic_pacing_review_status(config, chapter_number=chapter)
    if not status.get("required"):
        return {"required": False, "complete": True, "need_human": False, "reason": "not_required"}
    gate = load_json(root / str(status.get("gate_result") or ""), default={})
    applied = gate.get("semantic_pacing") if isinstance(gate, dict) and isinstance(gate.get("semantic_pacing"), dict) else {}
    result = root / str(status.get("result_file") or "")
    complete = bool(
        result.is_file()
        and str(applied.get("source_sha256") or "") == candidate_hash
        and str(applied.get("result_sha256") or "") == _file_hash(result)
    )
    verdict = str(applied.get("verdict") or "")
    return {
        "required": True,
        "complete": complete,
        "need_human": complete and verdict in {"need_human", "insufficient_evidence"},
        "reason": "applied" if complete else "pacing review missing, invalid, or stale",
        "verdict": verdict,
    }


def _editorial_stage(config: ConfigDocument, root: Path, chapter: int, candidate_hash: str) -> dict[str, Any]:
    required = bool(editorial_review_required_reasons(config, chapter_number=chapter))
    if not required:
        return {"required": False, "complete": True, "need_human": False, "reason": "not_required"}
    aggregate = load_json(root / "50_workbench" / "editorial_reviews" / f"ch{chapter:03d}.aggregate.json", default={})
    structural = any(
        aggregate.get(key)
        for key in ("missing_roles", "duplicate_role_results", "invalid_results", "stale_results")
    ) if isinstance(aggregate, dict) else True
    complete = bool(
        isinstance(aggregate, dict)
        and str(aggregate.get("source_sha256") or "") == candidate_hash
        and int(aggregate.get("result_count") or 0) > 0
        and not structural
    )
    human_reasons = set(str(item) for item in aggregate.get("need_human_reasons") or []) if isinstance(aggregate, dict) else set()
    minority_codes = {
        str(item.get("issue_code") or "")
        for item in aggregate.get("minority_blockers") or []
        if isinstance(item, dict)
    } if isinstance(aggregate, dict) else set()
    conflict_codes = {
        str(item.get("issue_code") or "")
        for item in aggregate.get("human_decisions") or []
        if isinstance(item, dict)
    } if isinstance(aggregate, dict) else set()
    minority_only_conflict = bool(conflict_codes and conflict_codes.issubset(minority_codes))
    content_reasons = {"unresolved_P0", "unresolved_P1", "editorial_blocking_verdict", "minority_P0_P1"}
    if minority_only_conflict:
        content_reasons.add("editorial_evidence_conflict")
    protocol_or_conflict = human_reasons - content_reasons
    return {
        "required": True,
        "complete": complete,
        "need_human": complete and bool(protocol_or_conflict),
        "reason": "aggregated" if complete else "editorial reviews missing, invalid, duplicate, or stale",
        "need_human_reasons": sorted(protocol_or_conflict),
    }


def _collect_findings(
    config: ConfigDocument,
    root: Path,
    chapter: int,
    candidate_hash: str,
    gate: dict[str, Any],
    stages: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_p2 = _selected_p2_codes(config)
    rows: list[dict[str, Any]] = []
    for index, finding in enumerate(gate.get("failures") or []):
        if not isinstance(finding, dict):
            continue
        code = str(finding.get("code") or "gate_failure")
        if code == "semantic_review_required" or code.startswith(("agent_semantic:", "semantic_pacing:")):
            continue
        rows.append(_admit_finding("deterministic", finding, candidate_hash, fallback_evidence=f"gate#/failures/{index}", selected_p2=selected_p2))
    if stages["semantic"].get("complete"):
        application = load_json(
            root / "50_workbench" / "gate_artifacts" / f"ch{chapter:03d}" / "semantic_review_application.json",
            default={},
        )
        for index, finding in enumerate(((application.get("payload") or {}).get("findings") or [])):
            if isinstance(finding, dict):
                rows.append(_admit_finding("semantic", finding, candidate_hash, fallback_evidence=f"semantic#/findings/{index}", selected_p2=selected_p2))
    if stages["payoff"].get("complete"):
        payoff = load_json(root / "50_workbench" / "quality_reviews" / f"ch{chapter:03d}.reader_payoff.json", default={})
        for index, finding in enumerate(payoff.get("findings") or [] if isinstance(payoff, dict) else []):
            if isinstance(finding, dict):
                rows.append(_admit_finding("payoff", finding, candidate_hash, fallback_evidence=f"payoff#/findings/{index}", selected_p2=selected_p2))
    if stages["pacing"].get("complete"):
        pacing = load_json(root / "50_workbench" / "gate_artifacts" / f"ch{chapter:03d}" / "semantic_pacing_result.json", default={})
        for index, finding in enumerate(pacing.get("findings") or [] if isinstance(pacing, dict) else []):
            if isinstance(finding, dict):
                rows.append(_admit_finding("pacing", finding, candidate_hash, fallback_evidence=f"pacing#/findings/{index}", selected_p2=selected_p2))
    if stages["editorial"].get("complete"):
        aggregate = load_json(root / "50_workbench" / "editorial_reviews" / f"ch{chapter:03d}.aggregate.json", default={})
        for index, finding in enumerate(aggregate.get("unresolved_items") or [] if isinstance(aggregate, dict) else []):
            if isinstance(finding, dict):
                rows.append(_admit_finding("editorial", finding, candidate_hash, fallback_evidence=f"editorial#/unresolved_items/{index}", selected_p2=selected_p2))
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(str(row["finding_id"]), row)
    return sorted(unique.values(), key=lambda item: (str(item["severity"]), str(item["finding_id"])))


def _admit_finding(
    source: str,
    finding: dict[str, Any],
    candidate_hash: str,
    *,
    fallback_evidence: str,
    selected_p2: set[str],
) -> dict[str, Any]:
    code = str(finding.get("code") or finding.get("id") or "UNCLASSIFIED").strip()
    severity = str(finding.get("severity") or "P2").upper()
    if severity not in {"P0", "P1", "P2", "P3"}:
        severity = "P2"
    diagnosis = str(finding.get("diagnosis") or finding.get("message") or finding.get("description") or code).strip()
    evidence_ids = [str(item) for item in finding.get("evidence_ids") or [] if str(item).strip()]
    if not evidence_ids:
        evidence_ids = [fallback_evidence]
    repair_target = str(
        finding.get("repair_target")
        or finding.get("repair_action")
        or finding.get("recommendation")
        or "repair the admitted issue at its first causal source"
    ).strip()
    preserve = [str(item).strip() for item in finding.get("preserve") or [] if str(item).strip()]
    reviewer_role = str(finding.get("role_id") or finding.get("reviewer_role") or "").strip()
    certainty = str(
        finding.get("certainty")
        or ("confirmed" if severity in BLOCKING_SEVERITIES else "probable")
    )
    stable_material = json.dumps(
        [source, reviewer_role, code, severity, diagnosis, sorted(evidence_ids), candidate_hash],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    finding_id = "RF-" + sha256(stable_material.encode("utf-8")).hexdigest()[:12]
    return {
        "finding_id": finding_id,
        "source": source,
        "reviewer_role": reviewer_role,
        "code": code,
        "severity": severity,
        "certainty": certainty,
        "diagnosis": diagnosis,
        "evidence_ids": evidence_ids,
        "reader_impact": str(finding.get("reader_impact") or "the chapter contract is not reliably satisfied").strip(),
        "repair_target": repair_target,
        "preserve": preserve,
        "selected": severity in BLOCKING_SEVERITIES or (severity == "P2" and code in selected_p2),
    }


def _repair_context(root: Path, chapter: int, candidate_hash: str) -> dict[str, Any]:
    files = [
        root / "20_outline" / "chapter_cards" / f"ch{chapter:03d}.json",
        root / "30_state" / "tcs" / f"ch{chapter:03d}.json",
    ]
    constraints = []
    for path in files:
        if path.is_file():
            constraints.append(
                {
                    "path": relative_path(root, path),
                    "sha256": _file_hash(path),
                    "content": load_json(path, default={}),
                }
            )
    return {
        "schema": "repair_synthesis_context_v1",
        "chapter_number": chapter,
        "candidate_sha256": candidate_hash,
        "constraints": constraints,
        "hard_boundary": "reference only; cannot write canonical state",
    }


def _barrier_result(
    chapter: int,
    *,
    candidate_path: str,
    candidate_hash: str,
    stages: dict[str, Any],
    findings: list[dict[str, Any]],
    status: str,
    blockers: list[str],
) -> dict[str, Any]:
    blocking_ids = [
        str(item["finding_id"])
        for item in findings
        if item.get("selected") and item.get("severity") in BLOCKING_SEVERITIES
    ]
    return {
        "schema": "review_barrier_status_v1",
        "chapter_number": chapter,
        "candidate_path": candidate_path,
        "candidate_sha256": candidate_hash,
        "status": status,
        "stages": stages,
        "findings": findings,
        "blocking_finding_ids": blocking_ids,
        "blockers": blockers,
    }


def _selected_p2_codes(config: ConfigDocument) -> set[str]:
    quality = config.data.get("quality") if isinstance(config.data.get("quality"), dict) else {}
    repair = quality.get("repair") if isinstance(quality.get("repair"), dict) else {}
    return {str(item).strip() for item in repair.get("selected_p2_codes") or [] if str(item).strip()}


def _current_task(root: Path, chapter: int, task_type: str) -> dict[str, Any] | None:
    tasks = [
        task
        for task in list_manifests(root, chapter_number=chapter)
        if str(task.get("task_type") or "") == task_type
        and str(task.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
    ]
    if not tasks:
        return None
    return sorted(tasks, key=lambda item: str(item.get("task_id") or ""))[-1]


def _task_by_id(root: Path, task_id: str) -> dict[str, Any] | None:
    try:
        task = load_manifest(root, task_id)
    except (OSError, ValueError):
        return None
    return task if str(task.get("task_id") or "") == task_id else None


def _round_from_task(task_id: str) -> int:
    match = re.search(r":r(\d{2}):v4$", task_id)
    if not match:
        raise RepairCoordinationError(f"task id does not declare an immutable repair round: {task_id}")
    return int(match.group(1))


def _attempts_file(root: Path, chapter: int) -> Path:
    return root / "50_workbench" / "repair_plans" / f"ch{chapter:03d}" / "attempts.json"


def _load_attempts(path: Path, chapter: int) -> dict[str, Any]:
    payload = load_json(path, default={})
    if not isinstance(payload, dict) or payload.get("schema") != REPAIR_ATTEMPTS_SCHEMA:
        return {
            "schema": REPAIR_ATTEMPTS_SCHEMA,
            "chapter_number": chapter,
            "submitted_rounds": [],
            "updated_at": "",
        }
    rounds = payload.get("submitted_rounds")
    if not isinstance(rounds, list):
        raise RepairCoordinationError("repair attempts file is invalid")
    return payload


def _markdown_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        normalized = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", line).strip()
        if normalized:
            items.append(normalized)
    return items


def _text_conflicts(preserve: list[str], mutable: list[str]) -> list[str]:
    conflicts: list[str] = []
    for protected in preserve:
        protected_key = re.sub(r"\s+", "", protected).lower()
        if len(protected_key) < 4:
            continue
        for target in mutable:
            target_key = re.sub(r"\s+", "", target).lower()
            if len(target_key) < 4:
                continue
            if protected_key == target_key or protected_key in target_key or target_key in protected_key:
                conflicts.append(f"{protected} <-> {target}")
    return _dedupe(conflicts)


def _need_human_decision(text: str) -> bool | None:
    matches = re.findall(
        r"(?im)^\s*(?:[-*+]\s*)?need-human\s*[:：]\s*(yes|no|true|false|是|否|需要|不需要)\s*$",
        text,
    )
    if len(matches) != 1:
        return None
    return matches[0].lower() in {"yes", "true", "是", "需要"}


def _dedupe(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _write_immutable_text(path, text)


def _write_immutable_text(path: Path, text: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise RepairCoordinationError(f"immutable repair artifact already exists with different content: {path}")
        return
    atomic_write_text(path, text)


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RepairCoordinationError(f"immutable repair artifact already exists with different bytes: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def relative_path(root: Path, path: str | Path) -> str:
    target = Path(path)
    if not target.is_absolute():
        target = root / target
    try:
        return target.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(target.resolve())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
