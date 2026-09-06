"""Exact currentness contract for approved future-knowledge results."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from longform_engine.agent_tasks import (
    list_manifests,
    manifest_chapter_number,
    manifest_input_paths,
    manifest_input_records,
    manifest_output,
    validate_manifest_strict,
)
from longform_engine.config import ConfigDocument
from longform_engine import fanfiction_contracts
from longform_engine.fanfiction_divergence import realized_major_divergence_errors
from longform_engine.future_knowledge_provenance import (
    FutureKnowledgeProvenanceError,
    FutureKnowledgeProvenanceSnapshot,
    build_future_knowledge_pin,
    future_knowledge_pin_applies,
    future_knowledge_pin_retains,
    pin_applicability_from_claims,
    load_future_knowledge_provenance_snapshot,
    require_exact_future_knowledge_pin,
)
from longform_engine.semantic_protocols import (
    approved_semantic_document,
    canonical_json_hash,
    seal_semantic_document,
    validate_human_decision,
    validate_semantic_document,
    validate_workflow_record,
)
from longform_engine.storage import resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


FUTURE_KNOWLEDGE_TASK_TYPE = "fanfiction_future_knowledge_reassessment"
FANFICTION_CONTEXT_BUNDLE_SCHEMA = "fanfiction_context_bundle_v3"


class FutureKnowledgeCurrentError(ValueError):
    """Raised when an apparently applied result is no longer exactly current."""


@dataclass(frozen=True)
class FutureKnowledgeCurrentSnapshot:
    """Immutable indexes reused while checking all approved results once."""

    provenance: FutureKnowledgeProvenanceSnapshot
    workflows_by_trigger: Mapping[str, tuple[tuple[Path, Mapping[str, Any]], ...]]
    manifests_by_task_id: Mapping[str, tuple[Mapping[str, Any], ...]]


def load_future_knowledge_current_snapshot(root: Path) -> FutureKnowledgeCurrentSnapshot:
    """Index workflows, task projections, pins, and archives once per operation."""

    provenance = load_future_knowledge_provenance_snapshot(root, require_exists=True)
    workflows: dict[str, list[tuple[Path, Mapping[str, Any]]]] = {}
    directory = root / "50_workbench" / "fanfiction_knowledge_impacts"
    for path in sorted(directory.glob("*.workflow.json")) if directory.is_dir() else []:
        payload = _read_json(path)
        trigger = (
            payload.get("extensions", {}).get("trigger")
            if isinstance(payload, dict)
            else None
        )
        trigger_id = str(trigger.get("trigger_id") or "") if isinstance(trigger, dict) else ""
        if trigger_id:
            workflows.setdefault(trigger_id, []).append((path, payload))
    manifests: dict[str, list[Mapping[str, Any]]] = {}
    for manifest in list_manifests(root):
        task_id = str(manifest.get("task_id") or "")
        if task_id:
            manifests.setdefault(task_id, []).append(manifest)
    return FutureKnowledgeCurrentSnapshot(
        provenance=provenance,
        workflows_by_trigger={key: tuple(value) for key, value in workflows.items()},
        manifests_by_task_id={key: tuple(value) for key, value in manifests.items()},
    )


def future_knowledge_reassessment_task_artifacts(
    root: Path,
    *,
    chapter_number: int,
    workflow_sha256: str,
) -> dict[str, Any]:
    """Own the stable one-trigger task identity used by close and task creation."""

    if chapter_number <= 0 or not re.fullmatch(r"[0-9a-f]{64}", workflow_sha256):
        raise ValueError(
            "future-knowledge task identity requires chapter and workflow SHA-256"
        )
    base = (
        "fanfiction_future_knowledge_reassessment."
        f"ch{chapter_number:03d}.{workflow_sha256[:12]}"
    )
    return {
        "base": base,
        "task_id": (
            "fanfiction_future_knowledge_reassessment:"
            f"ch{chapter_number:03d}:{workflow_sha256[:12]}:v5"
        ),
        "instruction": root / "50_workbench" / "intelligence_tasks" / f"{base}.md",
        "candidate": (
            root
            / "50_workbench"
            / "intelligence_candidates"
            / f"{base}.candidate.json"
        ),
        "manifest": root / "50_workbench" / "agent_tasks" / f"{base}.manifest.json",
    }


def future_knowledge_workflow_for_trigger(
    root: Path,
    trigger_id: str,
    *,
    snapshot: FutureKnowledgeCurrentSnapshot | None = None,
) -> tuple[Path, dict[str, Any]]:
    matches: list[tuple[Path, Mapping[str, Any]]]
    if snapshot is not None:
        matches = list(snapshot.workflows_by_trigger.get(trigger_id, ()))
    else:
        matches = []
        directory = root / "50_workbench" / "fanfiction_knowledge_impacts"
        for path in sorted(directory.glob("*.workflow.json")) if directory.is_dir() else []:
            payload = _read_json(path)
            trigger = (
                payload.get("extensions", {}).get("trigger")
                if isinstance(payload, dict)
                else None
            )
            if isinstance(trigger, dict) and trigger.get("trigger_id") == trigger_id:
                matches.append((path, payload))
    if len(matches) != 1:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:workflow_identity:"
            f"expected one workflow for {trigger_id}, found {len(matches)}"
        )
    path, payload = matches[0]
    return path, dict(payload)


def future_knowledge_approved_target(
    root: Path,
    *,
    trigger_id: str,
    candidate_scope: Mapping[str, Any] | None = None,
) -> Path:
    """Derive the only canonical target from the engine-owned workflow trigger."""

    if not trigger_id:
        raise ValueError("future knowledge target requires a stable trigger_id")
    _workflow_path, workflow = future_knowledge_workflow_for_trigger(root, trigger_id)
    trigger = workflow.get("extensions", {}).get("trigger")
    if not isinstance(trigger, dict):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:workflow_trigger"
        )
    chapter = int(trigger.get("realized_chapter") or 0)
    if chapter <= 0:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:workflow_trigger_chapter"
        )
    if candidate_scope is not None and dict(candidate_scope) != {
        "kind": "chapter",
        "chapter_number": chapter,
    }:
        raise ValueError("future knowledge candidate scope differs from engine-owned trigger")
    digest = sha256(trigger_id.encode("utf-8")).hexdigest()[:24]
    return (
        root
        / "10_bible"
        / "fanfiction"
        / "future_knowledge"
        / f"ch{chapter:03d}.{digest}.json"
    )


def validate_future_knowledge_reassessment(
    root: Path,
    payload: dict[str, Any],
    manifest: dict[str, Any] | None,
    errors: list[str],
) -> None:
    """Validate the typed Agent result against its exact workflow and task owner."""

    role: dict[str, Any] = (
        manifest["role"]
        if isinstance(manifest, dict) and isinstance(manifest.get("role"), dict)
        else {}
    )
    if role.get("independence_mode") != "isolated_review":
        errors.append("future knowledge reassessment must use an isolated review role")
    if payload.get("document_type") != "同人未来知识重估":
        errors.append("document_type must be 同人未来知识重估")
    artifact: dict[str, Any] = (
        payload["artifact"] if isinstance(payload.get("artifact"), dict) else {}
    )
    scope: dict[str, Any] = (
        artifact["scope"] if isinstance(artifact.get("scope"), dict) else {}
    )
    if scope.get("kind") != "chapter" or int(scope.get("chapter_number") or 0) <= 0:
        errors.append("future knowledge reassessment must use a chapter scope")
    extensions: dict[str, Any] = (
        payload["extensions"] if isinstance(payload.get("extensions"), dict) else {}
    )
    if extensions.get("task_type") != FUTURE_KNOWLEDGE_TASK_TYPE:
        errors.append(f"extensions.task_type must be {FUTURE_KNOWLEDGE_TASK_TYPE}")
    input_paths = [root / item for item in manifest_input_paths(manifest or {})]
    workflow: dict[str, Any] | None = None
    for path in input_paths:
        if not path.is_file():
            continue
        candidate = _read_json(path)
        if (
            isinstance(candidate, dict)
            and candidate.get("workflow_kind")
            == "fanfiction_future_knowledge_impact"
        ):
            if workflow is not None:
                errors.append(
                    "future knowledge reassessment manifest must bind exactly one workflow input"
                )
                return
            workflow = candidate
    if not isinstance(workflow, dict):
        errors.append("future knowledge reassessment manifest is missing its workflow input")
        return
    trigger = workflow.get("extensions", {}).get("trigger")
    if not isinstance(trigger, dict):
        errors.append("future knowledge workflow trigger is invalid")
        return
    trigger_id = str(trigger.get("trigger_id") or "")
    if extensions.get("trigger_id") != trigger_id:
        errors.append("extensions.trigger_id must match the workflow trigger")
    realized_chapter = int(trigger.get("realized_chapter") or 0)
    expected_scope = {"kind": "chapter", "chapter_number": realized_chapter}
    if scope != expected_scope:
        errors.append("artifact.scope must equal the engine-owned workflow trigger scope")
    if manifest_chapter_number(manifest or {}) != realized_chapter:
        errors.append("task manifest chapter must equal the workflow trigger chapter")
    try:
        owned_target = future_knowledge_approved_target(
            root,
            trigger_id=trigger_id,
            candidate_scope=scope,
        )
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if owned_target.is_file():
            approved = _read_json(owned_target)
            approved_extensions: dict[str, Any] = {}
            if isinstance(approved, dict):
                raw_extensions = approved.get("extensions")
                if isinstance(raw_extensions, dict):
                    approved_extensions = dict(raw_extensions)
            if approved_extensions.get("trigger_id") != trigger_id:
                errors.append(
                    "future knowledge target is already owned by a different trigger"
                )
    expected_trigger_sha = canonical_json_hash(trigger)
    expected_inputs = {
        str(item.get("kind") or ""): str(item.get("sha256") or "")
        for item in workflow.get("inputs") or []
        if isinstance(item, dict)
    }
    knowledge_scope = list(trigger.get("knowledge_scope_refs") or [])
    claims = payload.get("claims")
    if not isinstance(claims, list) or len(claims) != len(knowledge_scope):
        errors.append("claims must cover every workflow knowledge scope exactly once")
        return
    covered: list[str] = []
    allowed = {"仍可靠", "部分可靠", "已失效", "反向误导"}
    for index, claim in enumerate(claims):
        claim_extensions: dict[str, Any] = {}
        if isinstance(claim, dict):
            raw_extensions = claim.get("extensions")
            if isinstance(raw_extensions, dict):
                claim_extensions = dict(raw_extensions)
        prefix = f"claims[{index}].extensions"
        if claim_extensions.get("semantic_type") != "未来知识可靠性":
            errors.append(f"{prefix}.semantic_type must be 未来知识可靠性")
        if claim_extensions.get("trigger_id") != trigger_id:
            errors.append(f"{prefix}.trigger_id must match the workflow trigger")
        if claim_extensions.get("trigger_sha256") != expected_trigger_sha:
            errors.append(f"{prefix}.trigger_sha256 is stale")
        if claim_extensions.get("input_hashes") != expected_inputs:
            errors.append(f"{prefix}.input_hashes must match workflow inputs")
        knowledge_claim_id = claim_extensions.get("knowledge_claim_id")
        if knowledge_claim_id not in knowledge_scope or knowledge_claim_id in covered:
            errors.append(
                f"{prefix}.knowledge_claim_id is outside or duplicates workflow scope"
            )
        covered.append(str(knowledge_claim_id))
        if claim_extensions.get("reliability") not in allowed:
            errors.append(
                f"{prefix}.reliability must be 仍可靠|部分可靠|已失效|反向误导"
            )
        knowledge_range = claim_extensions.get("knowledge_range")
        if not isinstance(knowledge_range, dict) or set(knowledge_range) != {
            "from_chapter",
            "to_chapter",
            "scope_refs",
        }:
            errors.append(f"{prefix}.knowledge_range fields are invalid")
        else:
            start = knowledge_range.get("from_chapter")
            end = knowledge_range.get("to_chapter")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or start <= realized_chapter
            ):
                errors.append(
                    f"{prefix}.knowledge_range.from_chapter must follow realization"
                )
            if end is not None and (
                not isinstance(end, int)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or end < start
            ):
                errors.append(f"{prefix}.knowledge_range.to_chapter is invalid")
            if knowledge_range.get("scope_refs") != [knowledge_claim_id]:
                errors.append(
                    f"{prefix}.knowledge_range.scope_refs must bind its knowledge claim"
                )
        if claim_extensions.get("depends_on_claims") != [
            knowledge_claim_id,
            trigger.get("source_claim_id"),
        ]:
            errors.append(
                f"{prefix}.depends_on_claims must bind knowledge and divergence"
            )
    if covered != knowledge_scope:
        errors.append("claims must preserve workflow knowledge scope order")


def current_applied_future_knowledge_target(
    config: ConfigDocument,
    candidate: Path,
) -> Path | None:
    """Return a healthy duplicate target; stale applied state raises before mutation."""

    root = resolve_project_root(config)
    output = candidate.relative_to(root).as_posix()
    applied = [
        entry
        for entry in list_manifests(root)
        if entry.get("task_type") == FUTURE_KNOWLEDGE_TASK_TYPE
        and entry.get("status") == "applied"
        and manifest_output(entry).get("path") == output
    ]
    if not applied:
        return None
    if len(applied) != 1:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:applied_task_identity"
        )
    if not candidate.is_file():
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:candidate_missing"
        )
    payload = _read_json(candidate)
    if not isinstance(payload, dict):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:candidate_unreadable"
        )
    document = seal_semantic_document(payload)
    artifact = document.get("artifact")
    extensions = document.get("extensions")
    if not isinstance(artifact, dict) or not isinstance(extensions, dict):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:candidate_contract"
        )
    scope = artifact.get("scope") if isinstance(artifact.get("scope"), dict) else {}
    target = future_knowledge_approved_target(
        root,
        trigger_id=str(extensions.get("trigger_id") or ""),
        candidate_scope=scope,
    )
    require_current_approved_future_knowledge(config, target)
    return target


def require_current_approved_future_knowledge(
    config: ConfigDocument,
    approved_path: Path,
    *,
    snapshot: FutureKnowledgeCurrentSnapshot | None = None,
) -> None:
    """Deeply revalidate the complete applied chain without writing any state."""

    root = resolve_project_root(config)
    approved = _read_json(approved_path)
    if not isinstance(approved, dict):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:approved_document"
        )
    semantic_errors = validate_semantic_document(approved, require_approved=True)
    if semantic_errors:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:approved_contract:"
            + ";".join(semantic_errors)
        )
    approved_extensions = approved.get("extensions")
    extensions = (
        approved_extensions if isinstance(approved_extensions, dict) else {}
    )
    trigger_id = str(extensions.get("trigger_id") or "")
    workflow_path, workflow = future_knowledge_workflow_for_trigger(
        root, trigger_id, snapshot=snapshot
    )
    workflow_errors = validate_workflow_record(workflow)
    workflow_extensions = (
        workflow["extensions"] if isinstance(workflow.get("extensions"), dict) else {}
    )
    trigger_value = workflow_extensions.get("trigger")
    trigger = dict(trigger_value) if isinstance(trigger_value, dict) else {}
    if not trigger:
        workflow_errors.append("workflow extensions.trigger is invalid")
    chapter = int(trigger.get("realized_chapter") or 0)
    expected_authorization = {
        "canonical_mutation": False,
        "requires_human_decision": True,
    }
    if workflow.get("workflow_kind") != "fanfiction_future_knowledge_impact":
        workflow_errors.append("workflow_kind is invalid")
    trigger_identity = sha256(trigger_id.encode("utf-8")).hexdigest()
    expected_workflow_id = (
        f"future_knowledge_impact_ch{chapter:03d}_{trigger_identity[:16]}"
    )
    expected_workflow_path = (
        root
        / "50_workbench"
        / "fanfiction_knowledge_impacts"
        / f"ch{chapter:03d}.{trigger_identity[:16]}.workflow.json"
    )
    if (
        workflow.get("workflow_id") != expected_workflow_id
        or workflow_path.resolve() != expected_workflow_path.resolve()
    ):
        workflow_errors.append("workflow identity/path is not engine-owned")
    if workflow.get("scope") != {"kind": "chapter", "chapter_number": chapter}:
        workflow_errors.append("workflow scope differs from trigger chapter")
    if workflow.get("state") != "awaiting_human" or workflow.get("outputs") != []:
        workflow_errors.append("workflow lifecycle fields are invalid")
    if workflow.get("authorization") != expected_authorization:
        workflow_errors.append("workflow authorization is invalid")
    if set(workflow_extensions) != {
        "trigger",
        "knowledge_scope_refs",
        "allowed_reliability_states",
        "instruction",
        "next_command",
    }:
        workflow_errors.append("workflow extension fields are invalid")
    if workflow_extensions.get("knowledge_scope_refs") != trigger.get(
        "knowledge_scope_refs"
    ):
        workflow_errors.append("workflow knowledge scope differs from trigger")
    if workflow_extensions.get("allowed_reliability_states") != [
        "仍可靠",
        "部分可靠",
        "已失效",
        "反向误导",
    ]:
        workflow_errors.append("workflow reliability states are invalid")
    if workflow_extensions.get("instruction") != (
        "由独立语义任务评估每条未来知识在本次分歧后的可靠性；只生成候选。"
        "结果必须经过人工批准并通过既有同人路线/知识语义 apply，才可进入后续章节依赖。"
    ) or workflow_extensions.get("next_command") != (
        "longform-engine fanfiction design-task project.yaml"
    ):
        workflow_errors.append("workflow instruction contract is invalid")
    if workflow.get("diagnostics") != [
        {
            "code": "major_divergence_realized",
            "trigger_claim_ids": [trigger.get("source_claim_id")],
            "source_event_id": trigger.get("source_event_id"),
            "knowledge_claim_ids": trigger.get("knowledge_scope_refs"),
        }
    ]:
        workflow_errors.append("workflow diagnostics differ from trigger")
    if workflow_errors:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:workflow_record:"
            + ";".join(workflow_errors)
        )
    expected_target = future_knowledge_approved_target(
        root,
        trigger_id=trigger_id,
        candidate_scope={"kind": "chapter", "chapter_number": chapter},
    )
    if approved_path.resolve() != expected_target.resolve():
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:approved_target"
        )

    input_records = workflow.get("inputs") or []
    inputs = {
        str(item.get("kind") or ""): item
        for item in input_records
        if isinstance(item, dict)
    }
    if (
        len(input_records) != 2
        or set(inputs) != {"fanfiction_context_bundle", "narrative_event_ledger"}
        or any(set(item) != {"path", "sha256", "kind"} for item in input_records)
    ):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:workflow_inputs"
        )
    context_path = root / str(inputs["fanfiction_context_bundle"].get("path") or "")
    event_path = root / str(inputs["narrative_event_ledger"].get("path") or "")
    expected_context = (
        root / "50_workbench" / "fanfiction_context" / f"ch{chapter:03d}.json"
    )
    expected_event = (
        root / "30_state" / "narrative_events" / f"ch{chapter:03d}.json"
    )
    if (
        context_path.resolve() != expected_context.resolve()
        or event_path.resolve() != expected_event.resolve()
    ):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:workflow_paths"
        )
    for label, record, path in (
        ("context", inputs["fanfiction_context_bundle"], context_path),
        ("event", inputs["narrative_event_ledger"], event_path),
    ):
        if not path.is_file() or record.get("sha256") != _file_hash(path):
            raise FutureKnowledgeCurrentError(
                f"future_knowledge_document_stale:{label}_hash"
            )
    context = _read_json(context_path)
    if not isinstance(context, dict) or _current_context_errors(
        config, root, context, chapter=chapter
    ):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:context_binding"
        )
    event = _read_json(event_path)
    review_claims = {
        str(item.get("claim_id") or ""): item
        for item in context.get("review_projection", {}).get("claims") or []
        if isinstance(item, dict) and item.get("claim_id")
    }
    final_path = manuscript_chapter_path(root, chapter, lane="final")
    semantic_path = (
        root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json"
    )
    divergence_errors = realized_major_divergence_errors(
        root=root,
        chapter_number=chapter,
        divergences=event.get("realized_major_divergences") if isinstance(event, dict) else None,
        event_payload=event if isinstance(event, dict) else {},
        review_claims=review_claims,
        final_path=final_path,
        semantic_path=semantic_path,
        context_path=context_path,
        require_stored_bindings=True,
    )
    event_triggers = [
        item
        for item in event.get("realized_major_divergences") or []
        if isinstance(item, dict) and item.get("trigger_id") == trigger_id
    ] if isinstance(event, dict) else []
    if divergence_errors or event_triggers != [trigger]:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:trigger_evidence"
        )

    workflow_relative = workflow_path.relative_to(root).as_posix()
    workflow_sha256 = _file_hash(workflow_path)
    owned = future_knowledge_reassessment_task_artifacts(
        root,
        chapter_number=chapter,
        workflow_sha256=workflow_sha256,
    )
    manifest_path = Path(owned["manifest"])
    candidate_path = Path(owned["candidate"])
    indexed: list[dict[str, Any]] = (
        [dict(item) for item in snapshot.manifests_by_task_id.get(str(owned["task_id"]), ())]
        if snapshot is not None
        else [
            dict(item)
            for item in list_manifests(root)
            if item.get("task_id") == owned["task_id"]
        ]
    )
    if len(indexed) != 1:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:task_manifest"
    )
    projection = indexed[0]
    current_result_value = projection.get("current_result")
    current_result: dict[str, Any] = (
        current_result_value if isinstance(current_result_value, dict) else {}
    )
    manifest_relative = manifest_path.relative_to(root).as_posix()
    if (
        projection.get("manifest_file") != manifest_relative
        or projection.get("status") != "applied"
        or manifest_chapter_number(projection) != chapter
        or current_result.get("path") != candidate_path.relative_to(root).as_posix()
        or not candidate_path.is_file()
        or current_result.get("sha256") != _file_hash(candidate_path)
        or current_result.get("ok") is not True
    ):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:task_state"
        )
    manifest = _read_json(manifest_path)
    try:
        current = fanfiction_contracts.load_current_fanfiction_documents(config, root)
    except fanfiction_contracts.FanfictionContractError as exc:
        raise FutureKnowledgeCurrentError(
            f"future_knowledge_document_stale:fanfiction_chain:{exc}"
        ) from exc
    base_sources = {
        path.relative_to(root).as_posix(): current.sha256[name]
        for name, path in current.paths.items()
    }
    expected_manifest_inputs = {
        workflow_relative,
        context_path.relative_to(root).as_posix(),
        event_path.relative_to(root).as_posix(),
        Path(owned["instruction"]).relative_to(root).as_posix(),
        *base_sources,
    }
    if (
        not isinstance(manifest, dict)
        or manifest.get("task_id") != owned["task_id"]
        or manifest.get("task_type") != FUTURE_KNOWLEDGE_TASK_TYPE
        or manifest.get("scope") != {"kind": "chapter", "chapter_number": chapter}
        or {str(item.get("path") or "") for item in manifest_input_records(manifest)}
        != expected_manifest_inputs
        or str(manifest_output(manifest).get("path") or "")
        != candidate_path.relative_to(root).as_posix()
    ):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:task_manifest"
        )
    manifest_validation = validate_manifest_strict(root, manifest, strict=True)
    if not manifest_validation.ok:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:task_manifest:"
            + ";".join(manifest_validation.errors)
        )
    for item in manifest_input_records(manifest):
        path = root / str(item.get("path") or "")
        if not path.is_file() or item.get("sha256") != _file_hash(path):
            raise FutureKnowledgeCurrentError(
                "future_knowledge_document_stale:task_inputs"
            )
    candidate = _read_json(candidate_path)
    candidate_artifact = (
        candidate.get("artifact") if isinstance(candidate, dict) else None
    )
    if (
        not isinstance(candidate, dict)
        or not isinstance(candidate_artifact, dict)
        or extensions.get("approved_candidate_sha256")
        != candidate_artifact.get("content_sha256")
    ):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:candidate"
        )
    candidate_errors = validate_semantic_document(candidate)
    validate_future_knowledge_reassessment(root, candidate, manifest, candidate_errors)
    if candidate_errors:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:candidate_contract:"
            + ";".join(candidate_errors)
        )
    human_decision: dict[str, Any] = {}
    raw_human_decision = extensions.get("human_decision")
    if isinstance(raw_human_decision, dict):
        human_decision = dict(raw_human_decision)
    human_errors = validate_human_decision(human_decision)
    if human_errors:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:human_decision:"
            + ";".join(human_errors)
        )
    candidate_document = seal_semantic_document(candidate)
    if (
        human_decision.get("target_id")
        != candidate_document.get("artifact", {}).get("artifact_id")
        or human_decision.get("target_sha256")
        != candidate_document.get("artifact", {}).get("content_sha256")
        or human_decision.get("scope")
        != {"kind": "chapter", "chapter_number": chapter}
        or approved_semantic_document(candidate_document, decision=human_decision)
        != approved
    ):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:approved_projection"
        )
    try:
        from_chapter, to_chapter = pin_applicability_from_claims(
            item for item in candidate.get("claims") or [] if isinstance(item, dict)
        )
        expected_pin = build_future_knowledge_pin(
            root,
            trigger_id=trigger_id,
            task_id=str(owned["task_id"]),
            chapter_number=chapter,
            from_chapter=from_chapter,
            to_chapter=to_chapter,
            approved_path=approved_path,
            evidence_paths={
                "workflow": workflow_path,
                "fanfiction_context_bundle": context_path,
                "narrative_event_ledger": event_path,
                "final_chapter": final_path,
                "semantic_ledger": semantic_path,
                "task_manifest": manifest_path,
                "task_instruction": Path(owned["instruction"]),
                "candidate": candidate_path,
            },
        )
        pin = require_exact_future_knowledge_pin(
            root,
            expected_pin,
            snapshot=snapshot.provenance if snapshot is not None else None,
        )
    except FutureKnowledgeProvenanceError as exc:
        raise FutureKnowledgeCurrentError(
            f"future_knowledge_document_stale:provenance_pin:{exc}"
        ) from exc
    if pin.get("from_chapter") != from_chapter or pin.get("to_chapter") != to_chapter:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:applicability"
        )
    if not future_knowledge_pin_applies(pin, from_chapter):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:applicability"
        )
    next_chapter = _next_production_chapter(root, fallback=from_chapter)
    if not future_knowledge_pin_retains(pin, next_chapter):
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:retention_expired"
        )
    stale_registry = _read_json(root / "30_state" / "stale_artifacts.json")
    stale_paths = {
        str(item.get("artifact_path") or "")
        for item in stale_registry.get("items") or []
        if isinstance(item, dict) and item.get("state") == "stale"
    } if isinstance(stale_registry, dict) else set()
    protected_paths = {
        approved_path.relative_to(root).as_posix(),
        *(
            str(item.get("path") or "")
            for item in pin.get("evidence") or []
            if isinstance(item, dict)
        ),
        *(str(path) for path in base_sources),
    }
    archive_binding = pin.get("provenance_archive")
    if isinstance(archive_binding, dict):
        protected_paths.add(str(archive_binding.get("path") or ""))
    if stale_paths & protected_paths:
        raise FutureKnowledgeCurrentError(
            "future_knowledge_document_stale:registry"
        )


def _next_production_chapter(root: Path, *, fallback: int) -> int:
    closure_dir = root / "30_state" / "chapter_closures"
    closed: list[int] = []
    for path in sorted(closure_dir.glob("ch*.json")) if closure_dir.is_dir() else []:
        match = re.fullmatch(r"ch0*(\d+)\.json", path.name)
        if match:
            closed.append(int(match.group(1)))
    return max(closed) + 1 if closed else fallback


def _current_context_errors(
    config: ConfigDocument,
    root: Path,
    context: Mapping[str, Any],
    *,
    chapter: int,
) -> list[str]:
    errors: list[str] = []
    if (
        context.get("schema") != FANFICTION_CONTEXT_BUNDLE_SCHEMA
        or context.get("chapter_number") != chapter
    ):
        errors.append("context schema/chapter differs")
    without_hash = {key: value for key, value in context.items() if key != "bundle_sha256"}
    if context.get("bundle_sha256") != sha256(
        json.dumps(
            without_hash,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest():
        errors.append("context bundle hash differs")
    try:
        current = fanfiction_contracts.load_current_fanfiction_documents(config, root)
    except fanfiction_contracts.FanfictionContractError as exc:
        return [f"fanfiction chain: {exc}"]
    declared = {
        str(item.get("path") or ""): str(item.get("sha256") or "")
        for item in context.get("source_files") or []
        if isinstance(item, dict)
    }
    for name, path in current.paths.items():
        if declared.get(path.relative_to(root).as_posix()) != current.sha256[name]:
            errors.append(f"current source differs: {name}")
    provenance = context.get("chapter_provenance")
    if not isinstance(provenance, dict):
        return [*errors, "chapter provenance is missing"]
    for kind, path in {
        "chapter_contract": (
            root / "20_outline" / "chapter_contracts" / f"ch{chapter:03d}.json"
        ),
    }.items():
        relative = path.relative_to(root).as_posix()
        payload = _read_json(path)
        if not path.is_file() or provenance.get(f"{kind}_path") != relative:
            errors.append(f"{kind} path differs")
            continue
        if kind == "chapter_contract" and isinstance(payload, dict):
            payload = {
                key: value
                for key, value in payload.items()
                if key != "chapter_contract_hash"
            }
        if provenance.get(f"{kind}_sha256") != sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest():
            errors.append(f"{kind} hash differs")
    return errors


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


__all__ = [
    "FutureKnowledgeCurrentSnapshot",
    "FutureKnowledgeCurrentError",
    "current_applied_future_knowledge_target",
    "future_knowledge_approved_target",
    "future_knowledge_reassessment_task_artifacts",
    "future_knowledge_workflow_for_trigger",
    "load_future_knowledge_current_snapshot",
    "require_current_approved_future_knowledge",
    "validate_future_knowledge_reassessment",
]
