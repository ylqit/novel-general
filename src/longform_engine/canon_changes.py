"""Versioned v0.10 canonical-setting changes with explicit dependency closure."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.config import ConfigDocument
from longform_engine.revision import create_versioned_revision_branch
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import list_finalized_chapter_files


CANONICAL_FACT_SCHEMA = "canonical_fact_v2"
FACT_REGISTRY_SCHEMA = "canonical_fact_registry_v2"
PROPOSAL_SCHEMA = "canon_change_proposal_v1"
IMPACT_SCHEMA = "dependency_impact_v1"
SEMANTIC_REVIEW_SCHEMA = "canon_change_semantic_review_v1"
HUMAN_DECISION_SCHEMA = "human_canon_change_decision_v1"
STALE_REGISTRY_SCHEMA = "stale_artifact_registry_v1"
STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$")


@dataclass(frozen=True)
class CanonChangeApplyResult:
    status: str
    proposal_id: str
    canonical_fact_file: str
    stale_artifacts: int
    revision_branch_id: str
    transaction_report: str


def validate_canonical_fact(value: Any) -> list[str]:
    fields = {
        "schema", "fact_id", "statement", "status", "dependency_fact_ids", "source_refs"
    }
    if not isinstance(value, dict) or set(value) != fields:
        return ["canonical_fact_v2 fields are invalid"]
    errors: list[str] = []
    if value.get("schema") != CANONICAL_FACT_SCHEMA:
        errors.append(f"schema must be {CANONICAL_FACT_SCHEMA}")
    _stable_id(value.get("fact_id"), "fact_id", errors)
    if not isinstance(value.get("statement"), str) or not value["statement"].strip():
        errors.append("statement must be non-empty")
    if value.get("status") not in {"active", "retired"}:
        errors.append("status must be active or retired")
    for field in ("dependency_fact_ids", "source_refs"):
        _stable_id_list(value.get(field), field, errors)
    if value.get("fact_id") in set(value.get("dependency_fact_ids") or []):
        errors.append("a fact cannot depend on itself")
    return errors


def validate_canon_change_proposal(value: Any) -> list[str]:
    fields = {
        "schema", "proposal_id", "reason", "effective_from_chapter", "operations",
        "dependency_fact_ids", "created_by",
    }
    if not isinstance(value, dict) or set(value) != fields:
        return ["canon_change_proposal_v1 fields are invalid"]
    errors: list[str] = []
    if value.get("schema") != PROPOSAL_SCHEMA:
        errors.append(f"schema must be {PROPOSAL_SCHEMA}")
    _stable_id(value.get("proposal_id"), "proposal_id", errors)
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        errors.append("reason must be non-empty")
    chapter = value.get("effective_from_chapter")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
        errors.append("effective_from_chapter must be positive")
    if value.get("created_by") != "human":
        errors.append("proposal must be human-created")
    _stable_id_list(value.get("dependency_fact_ids"), "dependency_fact_ids", errors)
    operations = value.get("operations")
    if not isinstance(operations, list) or not operations:
        errors.append("operations must be a non-empty list")
        return errors
    seen: set[str] = set()
    for index, operation in enumerate(operations):
        prefix = f"operations[{index}]"
        if not isinstance(operation, dict) or set(operation) != {"operation", "fact"}:
            errors.append(f"{prefix} fields are invalid")
            continue
        if operation.get("operation") not in {"upsert", "retire"}:
            errors.append(f"{prefix}.operation is invalid")
        fact_errors = validate_canonical_fact(operation.get("fact"))
        errors.extend(f"{prefix}.{error}" for error in fact_errors)
        fact = operation.get("fact") if isinstance(operation.get("fact"), dict) else {}
        fact_id = str(fact.get("fact_id") or "")
        if fact_id in seen:
            errors.append(f"duplicate fact operation: {fact_id}")
        seen.add(fact_id)
        if operation.get("operation") == "retire" and fact.get("status") != "retired":
            errors.append(f"{prefix}.retire requires fact.status=retired")
    return errors


def build_dependency_impact(root: Path, proposal: dict[str, Any]) -> dict[str, Any]:
    errors = validate_canon_change_proposal(proposal)
    if errors:
        raise ValueError("canon change proposal is invalid: " + "; ".join(errors))
    changed_fact_ids = {
        str(operation["fact"]["fact_id"]) for operation in proposal["operations"]
    } | set(proposal["dependency_fact_ids"])
    impacts = _deterministic_dependency_closure(root, changed_fact_ids)
    return {
        "schema": IMPACT_SCHEMA,
        "proposal": {
            "proposal_id": proposal["proposal_id"],
            "sha256": _json_hash(proposal),
        },
        "changed_fact_ids": sorted(changed_fact_ids),
        "impacts": impacts,
        "closure_method": "stable_fact_ids_and_explicit_dependency_refs_only",
    }


def validate_dependency_impact(
    root: Path,
    proposal: dict[str, Any],
    impact: Any,
) -> list[str]:
    expected = build_dependency_impact(root, proposal)
    if not isinstance(impact, dict) or set(impact) != set(expected):
        return ["dependency_impact_v1 fields are invalid"]
    errors: list[str] = []
    if impact.get("schema") != IMPACT_SCHEMA:
        errors.append(f"schema must be {IMPACT_SCHEMA}")
    if impact.get("proposal") != expected["proposal"]:
        errors.append("dependency impact proposal binding is stale")
    if impact.get("changed_fact_ids") != expected["changed_fact_ids"]:
        errors.append("changed_fact_ids must match the deterministic proposal closure")
    expected_by_path = {item["artifact_path"]: item for item in expected["impacts"]}
    actual_items = impact.get("impacts")
    if not isinstance(actual_items, list):
        return [*errors, "impacts must be a list"]
    actual_by_path = {
        str(item.get("artifact_path")): item for item in actual_items if isinstance(item, dict)
    }
    missing = sorted(set(expected_by_path) - set(actual_by_path))
    if missing:
        errors.append("dependency impact omitted must_stale artifacts: " + ", ".join(missing))
    for path, deterministic in expected_by_path.items():
        actual = actual_by_path.get(path)
        if actual is None:
            continue
        if actual.get("classification") != "must_stale":
            errors.append(f"deterministic must_stale cannot be downgraded: {path}")
        if actual.get("dependency_fact_ids") != deterministic["dependency_fact_ids"]:
            errors.append(f"dependency_fact_ids are incomplete for {path}")
    if impact.get("closure_method") != expected["closure_method"]:
        errors.append("closure_method must reject keyword inference")
    return errors


def validate_canon_change_semantic_review(
    proposal: dict[str, Any], impact: dict[str, Any], review: Any
) -> list[str]:
    fields = {
        "schema", "proposal_sha256", "impact_sha256", "reviewer_task_id", "reviewer_role_id",
        "independent_from_proposer", "verdict", "reason", "reviewed_by",
    }
    if not isinstance(review, dict) or set(review) != fields:
        return ["canon_change_semantic_review_v1 fields are invalid"]
    errors: list[str] = []
    if review.get("schema") != SEMANTIC_REVIEW_SCHEMA:
        errors.append(f"schema must be {SEMANTIC_REVIEW_SCHEMA}")
    if review.get("proposal_sha256") != _json_hash(proposal):
        errors.append("proposal_sha256 is stale")
    if review.get("impact_sha256") != _json_hash(impact):
        errors.append("impact_sha256 is stale")
    if review.get("independent_from_proposer") is not True:
        errors.append("canon change semantic review must be independent")
    if review.get("verdict") not in {"pass", "repair", "reject"}:
        errors.append("verdict is invalid")
    for field in ("reviewer_task_id", "reviewer_role_id", "reason", "reviewed_by"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            errors.append(f"{field} must be non-empty")
    return errors


def validate_human_canon_change_decision(
    proposal: dict[str, Any], impact: dict[str, Any], review: dict[str, Any], decision: Any
) -> list[str]:
    fields = {
        "schema", "proposal_sha256", "impact_sha256", "semantic_review_sha256",
        "decision", "reason", "decided_by",
    }
    if not isinstance(decision, dict) or set(decision) != fields:
        return ["human_canon_change_decision_v1 fields are invalid"]
    errors: list[str] = []
    if decision.get("schema") != HUMAN_DECISION_SCHEMA:
        errors.append(f"schema must be {HUMAN_DECISION_SCHEMA}")
    bindings = {
        "proposal_sha256": _json_hash(proposal),
        "impact_sha256": _json_hash(impact),
        "semantic_review_sha256": _json_hash(review),
    }
    for field, expected in bindings.items():
        if decision.get(field) != expected:
            errors.append(f"{field} is stale")
    if decision.get("decision") not in {"approve", "repair", "reject", "defer"}:
        errors.append("decision is invalid")
    if decision.get("decided_by") != "human":
        errors.append("canon change decision must be human-owned")
    if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
        errors.append("reason must be non-empty")
    if decision.get("decision") == "approve" and review.get("verdict") != "pass":
        errors.append("human approval requires a passing independent semantic review")
    return errors


def apply_canon_change(
    config: ConfigDocument,
    *,
    proposal_path: str | Path,
    impact_path: str | Path,
    review_path: str | Path,
    decision_path: str | Path,
) -> CanonChangeApplyResult:
    root = resolve_project_root(config)
    paths = [
        _resolve_workbench_file(root, value)
        for value in (proposal_path, impact_path, review_path, decision_path)
    ]
    proposal, impact, review, decision = (_read_json(path) for path in paths)
    errors = [
        *validate_canon_change_proposal(proposal),
        *validate_dependency_impact(root, proposal, impact),
        *validate_canon_change_semantic_review(proposal, impact, review),
        *validate_human_canon_change_decision(proposal, impact, review, decision),
    ]
    if errors:
        raise ValueError("canon change application is invalid: " + "; ".join(errors))
    if decision["decision"] != "approve":
        raise ValueError("canon change is not human-approved")
    current_head = max((chapter for chapter, _path in list_finalized_chapter_files(root)), default=0)
    effective = int(proposal["effective_from_chapter"])
    if current_head and effective <= current_head:
        branch = create_versioned_revision_branch(
            config,
            from_chapter=effective,
            to_chapter=current_head,
            reason=f"canon change {proposal['proposal_id']}: {proposal['reason']}",
            created_by="human",
        )
        return CanonChangeApplyResult(
            status="routed_to_revision_branch_v2",
            proposal_id=str(proposal["proposal_id"]),
            canonical_fact_file="",
            stale_artifacts=0,
            revision_branch_id=branch.branch_id,
            transaction_report="",
        )

    fact_file = root / "10_bible" / "canonical_facts.json"
    stale_file = root / "30_state" / "stale_artifacts.json"
    registry = _read_json(fact_file) if fact_file.is_file() else {
        "schema": FACT_REGISTRY_SCHEMA,
        "items": [],
    }
    if registry.get("schema") != FACT_REGISTRY_SCHEMA:
        raise ValueError("canonical fact registry is not v2; migration and dual-read are not supported")
    facts = {
        str(item.get("fact_id")): item
        for item in registry.get("items", [])
        if isinstance(item, dict) and item.get("fact_id")
    }
    for operation in proposal["operations"]:
        facts[str(operation["fact"]["fact_id"])] = operation["fact"]
    next_registry = {
        "schema": FACT_REGISTRY_SCHEMA,
        "items": [facts[key] for key in sorted(facts)],
    }
    stale = _read_json(stale_file) if stale_file.is_file() else {
        "schema": STALE_REGISTRY_SCHEMA,
        "items": [],
    }
    if stale.get("schema") != STALE_REGISTRY_SCHEMA:
        raise ValueError("stale artifact registry is incompatible")
    stale_by_path = {
        str(item.get("artifact_path")): item
        for item in stale.get("items", [])
        if isinstance(item, dict) and item.get("artifact_path")
    }
    for item in impact["impacts"]:
        stale_by_path[item["artifact_path"]] = {
            **item,
            "proposal_id": proposal["proposal_id"],
            "state": "stale",
        }
    task_files = [
        root / item["artifact_path"]
        for item in impact["impacts"]
        if str(item["artifact_path"]).startswith("50_workbench/writing_tasks/")
        and str(item["artifact_path"]).endswith(".json")
    ]
    with apply_transaction(
        root,
        command="canon change apply",
        source_paths=paths,
        touched_paths=[fact_file, stale_file, *task_files],
        metadata={"proposal_id": proposal["proposal_id"], "must_stale": len(impact["impacts"])},
    ) as transaction:
        _write_json(fact_file, next_registry)
        _write_json(stale_file, {"schema": STALE_REGISTRY_SCHEMA, "items": list(stale_by_path.values())})
        for task_file in task_files:
            task = _read_json(task_file)
            if task.get("schema") == "chapter_writing_task_v8":
                task["status"] = "stale"
                _write_json(task_file, task)
    return CanonChangeApplyResult(
        status="applied_future_change",
        proposal_id=str(proposal["proposal_id"]),
        canonical_fact_file=fact_file.relative_to(root).as_posix(),
        stale_artifacts=len(impact["impacts"]),
        revision_branch_id="",
        transaction_report=transaction.report_file.relative_to(root).as_posix(),
    )


def _deterministic_dependency_closure(root: Path, fact_ids: set[str]) -> list[dict[str, Any]]:
    obligation_file = root / "30_state" / "semantic_obligations.json"
    obligation_payload = _read_json(obligation_file) if obligation_file.is_file() else {}
    obligation_ids = {
        str(item.get("obligation_id"))
        for item in obligation_payload.get("items", [])
        if isinstance(item, dict)
        and fact_ids.intersection(
            {
                str(ref)
                for field in ("subject_refs", "prior_state_refs", "dependency_refs")
                for ref in item.get(field) or []
            }
        )
    }
    impacts: dict[str, set[str]] = {}
    for directory in (
        root / "20_outline" / "chapter_forecasts",
        root / "20_outline" / "chapter_contracts",
        root / "20_outline" / "plot_nodes",
    ):
        for path in sorted(directory.glob("ch*.json")):
            payload = _read_json(path)
            serialized_refs = _explicit_refs(payload)
            if obligation_ids.intersection(serialized_refs) or fact_ids.intersection(serialized_refs):
                impacts[path.relative_to(root).as_posix()] = set(fact_ids)
                task_dir = root / "50_workbench" / "writing_tasks"
                token = path.stem
                for suffix in (".json", ".md", ".basis.json", ".agent_task.json"):
                    task_path = task_dir / f"{token}{suffix}"
                    if task_path.is_file():
                        impacts[task_path.relative_to(root).as_posix()] = set(fact_ids)
    return [
        {
            "artifact_path": path,
            "dependency_fact_ids": sorted(ids),
            "classification": "must_stale",
            "reason": "explicit stable fact/semantic-obligation dependency",
        }
        for path, ids in sorted(impacts.items())
    ]


def _explicit_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {
                "dependency_fact_ids", "dependency_refs", "subject_refs", "prior_state_refs",
                "semantic_obligation_refs", "obligation_refs",
            } and isinstance(child, list):
                refs.update(str(item) for item in child)
            else:
                refs.update(_explicit_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_explicit_refs(child))
    return refs


def _stable_id(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, str) or not STABLE_ID.fullmatch(value):
        errors.append(f"{label} must be a stable ID")


def _stable_id_list(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not STABLE_ID.fullmatch(item) for item in value
    ):
        errors.append(f"{label} must be a stable ID list")
    elif len(value) != len(set(value)):
        errors.append(f"{label} must not contain duplicates")


def _resolve_workbench_file(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to((root / "50_workbench").resolve())
    except ValueError as exc:
        raise ValueError("canon change evidence must remain under 50_workbench") from exc
    if not resolved.is_file():
        raise ValueError(f"canon change evidence does not exist: {resolved}")
    return resolved


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CANONICAL_FACT_SCHEMA", "FACT_REGISTRY_SCHEMA", "HUMAN_DECISION_SCHEMA", "IMPACT_SCHEMA",
    "PROPOSAL_SCHEMA", "SEMANTIC_REVIEW_SCHEMA", "CanonChangeApplyResult", "apply_canon_change",
    "build_dependency_impact", "validate_canon_change_proposal", "validate_canon_change_semantic_review",
    "validate_canonical_fact", "validate_dependency_impact", "validate_human_canon_change_decision",
]
