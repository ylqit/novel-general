"""Human/Agent ownership boundaries for the v0.10 planning workflow."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from longform_engine.agent_protocols import (
    EVIDENCE_REVIEW_SCHEMA,
    validate_evidence_review,
    validate_review_evidence_for_source,
)
from longform_engine.chapter_contract import stamp_chapter_contract
from longform_engine.config import ConfigDocument
from longform_engine import fanfiction_contracts
from longform_engine.reader_promises_v2 import materialize_explicit_reader_promises
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root

from .contracts import (
    PLANNING_BUNDLE_SCHEMA,
    PLOT_NODE_TABLE_SCHEMA,
    StructuralValidation,
    canonical_json_hash,
    validate_planning_bundle,
)


PLANNING_SEMANTIC_APPLICATION_SCHEMA = "planning_semantic_review_application_v1"
HUMAN_PLANNING_APPROVAL_SCHEMA = "human_planning_approval_v1"
HUMAN_NODE_DECISIONS_SCHEMA = "human_plot_node_decisions_v1"
NARRATIVE_EVENT_LEDGER_SCHEMA = "narrative_event_ledger_v1"

REVIEW_PROFILES: dict[str, dict[str, tuple[str, ...]]] = {
    "architecture": {
        "dimensions": (
            "causal_coherence",
            "character_motivation",
            "volume_progression",
            "reader_value",
            "protected_invariants",
        ),
        "finding_codes": (
            "causal_gap",
            "motivation_gap",
            "volume_drift",
            "weak_reader_value",
            "invariant_risk",
        ),
    },
    "chapter_plan": {
        "dimensions": (
            "node_causality",
            "character_agency",
            "reader_value",
            "obligation_coverage",
            "protected_invariants",
        ),
        "finding_codes": (
            "node_causal_gap",
            "agency_gap",
            "weak_reader_value",
            "obligation_gap",
            "invariant_risk",
        ),
    },
    "change_impact": {
        "dimensions": (
            "dependency_closure",
            "fact_consistency",
            "character_continuity",
            "promise_continuity",
            "prose_impact",
        ),
        "finding_codes": (
            "dependency_gap",
            "fact_contradiction",
            "character_discontinuity",
            "promise_break",
            "prose_rewrite_required",
        ),
    },
}
REVIEW_STATES = frozenset(
    {
        "draft",
        "protocol_valid",
        "compiled_candidate",
        "structurally_valid",
        "semantic_review_pending",
        "semantic_passed",
        "repair_required",
        "need_human",
        "insufficient",
        "human_approved",
        "apply_ready",
        "applied",
        "stale",
    }
)
NODE_DECISIONS = frozenset({"approve", "adjust", "reject", "defer"})


@dataclass(frozen=True)
class PlanningReviewValidation:
    ok: bool
    state: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    evidence: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class PlanningApplyResult:
    transaction_report: str
    canonical_paths: tuple[str, ...]
    planned_events: int
    micro_nodes: int


def build_planning_semantic_application(
    root: Path,
    *,
    subject_path: str | Path,
    profile: str,
    author_task_id: str,
    author_role_id: str,
    reviewer_task_id: str,
    reviewer_role_id: str,
    reviewer_version: str,
    review_result_path: str | Path,
    compiler_task_id: str = "",
    compiler_role_id: str = "",
) -> dict[str, Any]:
    """Bind an isolated review to exact subject/result bytes without applying it."""

    subject = _resolve_file(root, subject_path)
    result = _resolve_file(root, review_result_path)
    subject_payload = _read_json(subject)
    review_payload = _read_json(result)
    structural = validate_planning_bundle(subject_payload)
    verdict = str(review_payload.get("verdict") or "")
    state = {
        "pass": "semantic_passed",
        "repair": "repair_required",
        "need_human": "need_human",
        "insufficient_evidence": "insufficient",
    }.get(verdict, "semantic_review_pending")
    return {
        "schema": PLANNING_SEMANTIC_APPLICATION_SCHEMA,
        "application_id": "planning-review:" + sha256(
            f"{subject.relative_to(root).as_posix()}:{_file_hash(subject)}:{reviewer_task_id}".encode(
                "utf-8"
            )
        ).hexdigest()[:24],
        "profile": profile,
        "subject": {
            "path": subject.relative_to(root).as_posix(),
            "sha256": _file_hash(subject),
            "schema": str(subject_payload.get("schema") or ""),
            "structural_validation": structural.as_dict(),
        },
        "author_task": {"task_id": author_task_id, "role_id": author_role_id},
        "compiler_task": (
            {"task_id": compiler_task_id, "role_id": compiler_role_id}
            if compiler_task_id or compiler_role_id
            else None
        ),
        "reviewer_task": {
            "task_id": reviewer_task_id,
            "role_id": reviewer_role_id,
            "version": reviewer_version,
        },
        "independence": {
            "isolated": True,
            "reviewer_did_not_read_author_reasoning": True,
        },
        "review_result": {
            "path": result.relative_to(root).as_posix(),
            "sha256": _file_hash(result),
            "schema": str(review_payload.get("schema") or ""),
            "verdict": verdict,
        },
        "blockers": [
            str(item.get("code"))
            for item in review_payload.get("findings", [])
            if isinstance(item, dict) and item.get("severity") in {"P0", "P1"}
        ],
        "human_resolution": None,
        "state": state,
    }


def validate_planning_semantic_application(
    root: Path,
    application: Any,
) -> PlanningReviewValidation:
    errors: list[str] = []
    warnings: list[str] = []
    evidence: dict[str, dict[str, Any]] = {}
    fields = {
        "schema",
        "application_id",
        "profile",
        "subject",
        "author_task",
        "compiler_task",
        "reviewer_task",
        "independence",
        "review_result",
        "blockers",
        "human_resolution",
        "state",
    }
    if not isinstance(application, dict) or set(application) != fields:
        return PlanningReviewValidation(
            False,
            "draft",
            ("planning semantic application has unexpected or missing fields",),
            (),
            {},
        )
    if application.get("schema") != PLANNING_SEMANTIC_APPLICATION_SCHEMA:
        errors.append(f"schema must be {PLANNING_SEMANTIC_APPLICATION_SCHEMA}")
    profile = application.get("profile")
    if profile not in REVIEW_PROFILES:
        errors.append("profile must be architecture, chapter_plan, or change_impact")
    state = str(application.get("state") or "")
    if state not in REVIEW_STATES:
        errors.append("state is invalid")

    author = application.get("author_task")
    reviewer = application.get("reviewer_task")
    compiler = application.get("compiler_task")
    for label, value, expected in (
        ("author_task", author, {"task_id", "role_id"}),
        ("reviewer_task", reviewer, {"task_id", "role_id", "version"}),
    ):
        if not isinstance(value, dict) or set(value) != expected or any(
            not isinstance(value.get(field), str) or not value[field].strip()
            for field in expected
        ):
            errors.append(f"{label} is invalid")
    if compiler is not None and (
        not isinstance(compiler, dict)
        or set(compiler) != {"task_id", "role_id"}
        or any(not isinstance(compiler.get(field), str) or not compiler[field].strip() for field in compiler)
    ):
        errors.append("compiler_task must be null or contain task_id and role_id")
    if isinstance(author, dict) and isinstance(reviewer, dict):
        if author.get("task_id") == reviewer.get("task_id"):
            errors.append("reviewer task must be independent from the author task")
        if author.get("role_id") == reviewer.get("role_id"):
            errors.append("reviewer role must differ from the author role")
    if isinstance(compiler, dict) and isinstance(reviewer, dict):
        if compiler.get("task_id") == reviewer.get("task_id"):
            errors.append("reviewer task must be independent from the compiler task")
        if compiler.get("role_id") == reviewer.get("role_id"):
            errors.append("reviewer role must differ from the compiler role")
    independence = application.get("independence")
    if independence != {
        "isolated": True,
        "reviewer_did_not_read_author_reasoning": True,
    }:
        errors.append("independence must explicitly confirm isolated review without author reasoning")

    subject = application.get("subject")
    subject_path: Path | None = None
    subject_text = ""
    structural: StructuralValidation | None = None
    if not isinstance(subject, dict) or set(subject) != {
        "path",
        "sha256",
        "schema",
        "structural_validation",
    }:
        errors.append("subject fields are invalid")
    else:
        try:
            subject_path = _resolve_file(root, subject.get("path"))
            subject_text = subject_path.read_text(encoding="utf-8")
            subject_payload = json.loads(subject_text)
            structural = validate_planning_bundle(subject_payload)
            if subject.get("sha256") != _file_hash(subject_path):
                errors.append("subject SHA-256 is stale")
            if subject.get("schema") != subject_payload.get("schema"):
                errors.append("subject schema does not match current content")
            if subject.get("structural_validation") != structural.as_dict():
                errors.append("structural validation projection is stale")
            if not structural.ok:
                errors.append("subject is not structurally valid")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"subject cannot be read: {exc}")

    result_ref = application.get("review_result")
    review_payload: dict[str, Any] = {}
    if not isinstance(result_ref, dict) or set(result_ref) != {
        "path",
        "sha256",
        "schema",
        "verdict",
    }:
        errors.append("review_result fields are invalid")
    else:
        try:
            result_path = _resolve_file(root, result_ref.get("path"))
            review_payload = _read_json(result_path)
            if result_ref.get("sha256") != _file_hash(result_path):
                errors.append("review result SHA-256 is stale")
            if result_ref.get("schema") != EVIDENCE_REVIEW_SCHEMA:
                errors.append(f"review result schema must be {EVIDENCE_REVIEW_SCHEMA}")
            if result_ref.get("verdict") != review_payload.get("verdict"):
                errors.append("review result verdict projection is stale")
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"review result cannot be read: {exc}")

    if profile in REVIEW_PROFILES and review_payload:
        profile_contract = REVIEW_PROFILES[profile]
        errors.extend(
            validate_evidence_review(
                review_payload,
                required_dimensions=profile_contract["dimensions"],
                allowed_finding_codes=profile_contract["finding_codes"],
                canonical_ref_dimensions=("protected_invariants",),
            )
        )
        if subject_path is not None:
            evidence, evidence_errors = validate_review_evidence_for_source(
                review_payload,
                source_path=subject_path.relative_to(root).as_posix(),
                source_text=subject_text,
            )
            errors.extend(evidence_errors)

    verdict = str(review_payload.get("verdict") or "")
    expected_state = {
        "pass": "semantic_passed",
        "repair": "repair_required",
        "need_human": "need_human",
        "insufficient_evidence": "insufficient",
    }.get(verdict, "semantic_review_pending")
    if state not in {expected_state, "stale"}:
        errors.append(f"state must be {expected_state} for verdict={verdict or 'missing'}")
    blockers = application.get("blockers")
    expected_blockers = [
        str(item.get("code"))
        for item in review_payload.get("findings", [])
        if isinstance(item, dict) and item.get("severity") in {"P0", "P1"}
    ]
    if blockers != expected_blockers:
        errors.append("blockers must exactly project current P0/P1 finding codes")
    if application.get("human_resolution") is not None:
        errors.append("human_resolution is a separate CLI-owned approval record")
    if verdict == "pass" and not evidence:
        warnings.append("semantic pass has no resolved evidence spans")
    return PlanningReviewValidation(
        ok=not errors,
        state="stale" if any("stale" in item for item in errors) else expected_state,
        errors=tuple(errors),
        warnings=tuple(warnings),
        evidence=evidence,
    )


def build_human_planning_approval(
    root: Path,
    *,
    application_path: str | Path,
    decision: str,
    reason: str,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise ValueError("planning approval must be recorded by human")
    if decision not in {"approve", "repair", "defer"}:
        raise ValueError("planning approval decision must be approve, repair, or defer")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("planning approval requires a non-empty human reason")
    path = _resolve_file(root, application_path)
    application = _read_json(path)
    validation = validate_planning_semantic_application(root, application)
    if not validation.ok:
        raise ValueError("planning semantic application is invalid: " + "; ".join(validation.errors))
    if decision == "approve" and validation.state != "semantic_passed":
        raise ValueError("human approval requires a current semantic_passed application")
    return {
        "schema": HUMAN_PLANNING_APPROVAL_SCHEMA,
        "application_path": path.relative_to(root).as_posix(),
        "application_sha256": _file_hash(path),
        "decision": decision,
        "reason": reason.strip(),
        "approved_by": "human",
        "state": "human_approved" if decision == "approve" else decision,
    }


def validate_human_planning_approval(
    root: Path,
    approval: Any,
    *,
    expected_application_path: str | Path,
) -> list[str]:
    errors: list[str] = []
    fields = {
        "schema",
        "application_path",
        "application_sha256",
        "decision",
        "reason",
        "approved_by",
        "state",
    }
    if not isinstance(approval, dict) or set(approval) != fields:
        return ["human planning approval fields are invalid"]
    if approval.get("schema") != HUMAN_PLANNING_APPROVAL_SCHEMA:
        errors.append(f"approval schema must be {HUMAN_PLANNING_APPROVAL_SCHEMA}")
    expected = _resolve_file(root, expected_application_path)
    if approval.get("application_path") != expected.relative_to(root).as_posix():
        errors.append("approval application_path does not match")
    if approval.get("application_sha256") != _file_hash(expected):
        errors.append("approval application_sha256 is stale")
    if approval.get("decision") != "approve" or approval.get("state") != "human_approved":
        errors.append("planning apply requires an explicit human approve decision")
    if approval.get("approved_by") != "human":
        errors.append("planning approval must be human-owned")
    if not isinstance(approval.get("reason"), str) or not approval["reason"].strip():
        errors.append("planning approval reason must be non-empty")
    return errors


def build_human_node_decisions(
    root: Path,
    *,
    bundle_path: str | Path,
    decisions: list[dict[str, Any]],
    decided_by: str,
) -> dict[str, Any]:
    if decided_by != "human":
        raise ValueError("plot node decisions must be recorded by human")
    path = _resolve_file(root, bundle_path)
    bundle = _read_json(path)
    validation = validate_planning_bundle(bundle)
    if not validation.ok:
        raise ValueError("planning bundle is structurally invalid: " + "; ".join(validation.errors))
    payload = {
        "schema": HUMAN_NODE_DECISIONS_SCHEMA,
        "subject_path": path.relative_to(root).as_posix(),
        "subject_sha256": _file_hash(path),
        "decided_by": "human",
        "decisions": decisions,
    }
    decision_errors = validate_human_node_decisions(root, payload, bundle=bundle)
    if decision_errors:
        raise ValueError("plot node decisions are invalid: " + "; ".join(decision_errors))
    return payload


def validate_human_node_decisions(
    root: Path,
    payload: Any,
    *,
    bundle: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    fields = {"schema", "subject_path", "subject_sha256", "decided_by", "decisions"}
    if not isinstance(payload, dict) or set(payload) != fields:
        return ["human plot node decision fields are invalid"]
    if payload.get("schema") != HUMAN_NODE_DECISIONS_SCHEMA:
        errors.append(f"node decision schema must be {HUMAN_NODE_DECISIONS_SCHEMA}")
    if payload.get("decided_by") != "human":
        errors.append("node decisions must be human-owned")
    try:
        subject_path = _resolve_file(root, payload.get("subject_path"))
        if payload.get("subject_sha256") != _file_hash(subject_path):
            errors.append("node decision subject SHA-256 is stale")
        current_bundle = bundle or _read_json(subject_path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"node decision subject cannot be read: {exc}")
        return errors
    expected_nodes = {
        str(node.get("node_id"))
        for table in current_bundle.get("plot_node_tables", [])
        if isinstance(table, dict) and table.get("schema") == PLOT_NODE_TABLE_SCHEMA
        for node in table.get("nodes", [])
        if isinstance(node, dict) and node.get("node_kind") == "state_change"
    }
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return [*errors, "decisions must be a list"]
    actual_nodes: set[str] = set()
    for index, decision in enumerate(decisions):
        prefix = f"decisions[{index}]"
        if not isinstance(decision, dict) or set(decision) != {
            "node_id",
            "decision",
            "adjustment",
            "reason",
        }:
            errors.append(f"{prefix} fields are invalid")
            continue
        node_id = decision.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            errors.append(f"{prefix}.node_id is invalid")
        elif node_id in actual_nodes:
            errors.append(f"duplicate node decision: {node_id}")
        else:
            actual_nodes.add(node_id)
        choice = decision.get("decision")
        if choice not in NODE_DECISIONS:
            errors.append(f"{prefix}.decision is invalid")
        adjustment = decision.get("adjustment")
        if choice == "adjust":
            if not isinstance(adjustment, str) or not adjustment.strip():
                errors.append(f"{prefix}.adjustment is required for adjust")
        elif adjustment not in {None, ""}:
            errors.append(f"{prefix}.adjustment is only allowed for adjust")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            errors.append(f"{prefix}.reason must be non-empty")
    if actual_nodes != expected_nodes:
        missing = sorted(expected_nodes - actual_nodes)
        extra = sorted(actual_nodes - expected_nodes)
        if missing:
            errors.append(
                "every major state-change plot node requires an explicit decision; missing: "
                + ", ".join(missing)
            )
        if extra:
            errors.append("node decisions contain unknown nodes: " + ", ".join(extra))
    return errors


def apply_planning_bundle(
    config: ConfigDocument,
    *,
    bundle_path: str | Path,
    application_path: str | Path,
    approval_path: str | Path,
    node_decisions_path: str | Path,
    approved_by: str,
) -> PlanningApplyResult:
    """Atomically materialize planning only after semantic and per-node approval."""

    if approved_by != "human":
        raise ValueError("planning apply requires approved_by=human")
    root = resolve_project_root(config)
    bundle_file = _resolve_file(root, bundle_path)
    application_file = _resolve_file(root, application_path)
    approval_file = _resolve_file(root, approval_path)
    decisions_file = _resolve_file(root, node_decisions_path)
    bundle = _read_json(bundle_file)
    structural = validate_planning_bundle(bundle)
    if not structural.ok:
        raise ValueError("planning bundle is structurally invalid: " + "; ".join(structural.errors))
    fanfiction_sources: list[Path] = []
    fanfiction_source_sha256: dict[Path, str] = {}
    if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction":
        projection = bundle["active_volume_plan"].get("fanfiction_projection")
        if not isinstance(projection, dict):
            raise ValueError(
                "fanfiction planning requires active_volume_plan.fanfiction_projection"
            )
        try:
            current = fanfiction_contracts.load_current_fanfiction_documents(config, root)
        except fanfiction_contracts.FanfictionContractError as exc:
            raise ValueError(str(exc)) from exc
        fanfiction_sources = [
            current.paths["source_canon"],
            current.paths["story_engine"],
            current.paths["route_design"],
        ]
        fanfiction_source_sha256 = {
            current.paths["source_canon"]: current.sha256["source_canon"],
            current.paths["story_engine"]: current.sha256["story_engine"],
            current.paths["route_design"]: current.sha256["route_design"],
        }
        documents = [current.source_canon, current.story_engine, current.route]
        claim_ids = {
            str(claim.get("claim_id") or "")
            for document in documents
            if isinstance(document, dict)
            for claim in document.get("claims") or []
            if isinstance(claim, dict) and claim.get("claim_id")
        }
        unresolved = sorted(set(projection.get("claim_refs") or []) - claim_ids)
        if unresolved:
            raise ValueError(
                "fanfiction volume projection has unresolved stable claims: "
                + ", ".join(unresolved)
            )
    application = _read_json(application_file)
    review = validate_planning_semantic_application(root, application)
    if not review.ok or review.state != "semantic_passed":
        raise ValueError("planning semantic review is not a current pass: " + "; ".join(review.errors))
    if application["subject"]["path"] != bundle_file.relative_to(root).as_posix():
        raise ValueError("semantic review does not bind the planning bundle")
    approval_errors = validate_human_planning_approval(
        root,
        _read_json(approval_file),
        expected_application_path=application_file,
    )
    if approval_errors:
        raise ValueError("planning approval is invalid: " + "; ".join(approval_errors))
    decisions = _read_json(decisions_file)
    decision_errors = validate_human_node_decisions(root, decisions, bundle=bundle)
    if decision_errors:
        raise ValueError("plot node decisions are invalid: " + "; ".join(decision_errors))
    blocking = [
        item["node_id"]
        for item in decisions["decisions"]
        if item["decision"] in {"reject", "defer"}
    ]
    if blocking:
        raise ValueError(
            "planning apply is blocked by rejected/deferred plot nodes: " + ", ".join(blocking)
        )

    decisions_by_id = {item["node_id"]: item for item in decisions["decisions"]}
    active_volume_id = bundle["active_volume_plan"]["volume_id"]
    active_volume_order = next(
        int(item["order"])
        for item in bundle["volume_skeletons"]["items"]
        if item["volume_id"] == active_volume_id
    )
    canonical_payloads: dict[Path, Any] = {
        root / "20_outline" / "book_spine.json": bundle["book_spine"],
        root / "20_outline" / "volume_skeletons.json": bundle["volume_skeletons"],
        root
        / "20_outline"
        / "volumes"
        / f"vol{active_volume_order:03d}.json": {
            **bundle["active_volume_plan"],
            "lifecycle": "active",
            "approved_by": "human",
        },
        root / "20_outline" / "rolling_window.json": bundle["rolling_window"],
        root / "30_state" / "semantic_obligations.json": {
            "schema": "semantic_obligation_ledger_v1",
            "items": bundle["semantic_obligations"],
            "source_bundle_sha256": _file_hash(bundle_file),
        },
        root / "30_state" / "reader_promise_ledger.json": materialize_explicit_reader_promises(
            bundle["active_volume_plan"]["promise_threads"],
            approved_by="human",
        ),
    }
    fact_registry = root / "10_bible" / "canonical_facts.json"
    canonical_payloads[root / "30_state" / "planning_basis.json"] = {
        "schema": "planning_basis_v1",
        "rolling_window_basis_sha256": bundle["rolling_window"]["basis_sha256"],
        "source_files": [
            {
                "path": "20_outline/book_spine.json",
                "sha256": _json_file_hash(bundle["book_spine"]),
            },
            {
                "path": "20_outline/volume_skeletons.json",
                "sha256": _json_file_hash(bundle["volume_skeletons"]),
            },
            *(
                [
                    {
                        "path": fact_registry.relative_to(root).as_posix(),
                        "sha256": _file_hash(fact_registry),
                    }
                ]
                if fact_registry.is_file()
                else []
            ),
            *(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": fanfiction_source_sha256[path],
                }
                for path in fanfiction_sources
            ),
        ],
        "source_bundle_sha256": _file_hash(bundle_file),
    }
    planned_events = 0
    micro_nodes = 0
    for forecast in bundle["chapter_forecasts"]:
        chapter = int(forecast["chapter_number"])
        canonical_payloads[
            root / "20_outline" / "chapter_forecasts" / f"ch{chapter:03d}.json"
        ] = forecast
    for contract in bundle["chapter_contracts"]:
        chapter = int(contract["chapter_number"])
        canonical_payloads[
            root / "20_outline" / "chapter_contracts" / f"ch{chapter:03d}.json"
        ] = stamp_chapter_contract(contract)
    for table in bundle["plot_node_tables"]:
        chapter = int(table["chapter_number"])
        approved_nodes = []
        events = []
        for node in table["nodes"]:
            decision = decisions_by_id.get(node["node_id"])
            approved_node = {**node, "human_decision": decision}
            approved_nodes.append(approved_node)
            if node["node_kind"] == "state_change":
                if decision is None:
                    raise ValueError(
                        f"major plot node {node['node_id']} has no explicit human decision"
                    )
                planned_events += 1
                events.append(
                    {
                        "schema": "narrative_event_v1",
                        "event_id": f"event:{node['node_id']}",
                        "source_node_id": node["node_id"],
                        "chapter_number": chapter,
                        "preconditions": node["preconditions"],
                        "dependency_refs": node["dependency_refs"],
                        "fanfiction_claim_refs": node["fanfiction_claim_refs"],
                        "expected_changes": node["expected_changes"],
                        "reader_effect": node["reader_effect"],
                        "state": "planned_approved",
                        "realization_evidence": None,
                    }
                )
            else:
                micro_nodes += 1
        canonical_payloads[
            root / "20_outline" / "plot_nodes" / f"ch{chapter:03d}.json"
        ] = {**table, "nodes": approved_nodes, "approval_sha256": _file_hash(decisions_file)}
        canonical_payloads[
            root / "30_state" / "narrative_events" / f"ch{chapter:03d}.json"
        ] = {
            "schema": NARRATIVE_EVENT_LEDGER_SCHEMA,
            "chapter_number": chapter,
            "events": events,
            "realized_major_divergences": [],
            "source_plot_node_table_sha256": canonical_json_hash(
                {**table, "nodes": approved_nodes}
            ),
        }

    source_paths = (bundle_file, application_file, approval_file, decisions_file)
    stale_registry = root / "30_state" / "stale_artifacts.json"
    stale_payload = _read_json(stale_registry) if stale_registry.is_file() else None
    if isinstance(stale_payload, dict) and stale_payload.get("schema") == "stale_artifact_registry_v1":
        refreshed = {path.relative_to(root).as_posix() for path in canonical_payloads}
        stale_payload = {
            **stale_payload,
            "items": [
                item
                for item in stale_payload.get("items", [])
                if not isinstance(item, dict) or item.get("artifact_path") not in refreshed
            ],
        }
        canonical_payloads[stale_registry] = stale_payload
    touched_paths = tuple(canonical_payloads)
    with apply_transaction(
        root,
        command="planning apply-v010",
        source_paths=source_paths,
        touched_paths=touched_paths,
        metadata={
            "planning_bundle_schema": PLANNING_BUNDLE_SCHEMA,
            "semantic_review_state": review.state,
            "node_decision_count": len(decisions_by_id),
        },
    ) as transaction:
        for path, payload in canonical_payloads.items():
            _write_json(path, payload)
        transaction.update_metadata(
            canonical_paths=[path.relative_to(root).as_posix() for path in canonical_payloads]
        )
    return PlanningApplyResult(
        transaction_report=transaction.report_file.relative_to(root).as_posix(),
        canonical_paths=tuple(path.relative_to(root).as_posix() for path in canonical_payloads),
        planned_events=planned_events,
        micro_nodes=micro_nodes,
    )


def write_workbench_record(root: Path, relative: str, payload: dict[str, Any]) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("workbench record escaped the project root") from exc
    if not path.relative_to(root.resolve()).as_posix().startswith("50_workbench/"):
        raise ValueError("planning review records must remain under 50_workbench")
    _write_json(path, payload)
    return path


def _resolve_file(root: Path, raw: str | Path | None) -> Path:
    if not isinstance(raw, (str, Path)) or not str(raw):
        raise ValueError("path must be non-empty")
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escaped project root: {raw}") from exc
    if not path.is_file():
        raise ValueError(f"file does not exist: {path}")
    return path


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _json_file_hash(payload: Any) -> str:
    return sha256((json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")).hexdigest()
