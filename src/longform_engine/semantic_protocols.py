"""Minimal hard-shell protocols for semantic source and story work.

The engine intentionally validates identity, provenance, authority and state
transitions here.  It does *not* encode a closed ontology for characters,
events, relationships, abilities or crossover rules.  Those meanings live in
``semantic_document_v1`` bodies and evidence-backed claims produced by a Host
Agent and promoted only through an immutable human decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Iterable


ARTIFACT_ENVELOPE_SCHEMA = "artifact_envelope_v1"
EVIDENCE_REFERENCE_SCHEMA = "evidence_reference_v1"
WORKFLOW_RECORD_SCHEMA = "workflow_record_v1"
SEMANTIC_DOCUMENT_SCHEMA = "semantic_document_v1"
HUMAN_DECISION_SCHEMA = "human_decision_v1"

WORKFLOW_STATES = frozenset(
    {
        "draft",
        "awaiting_human",
        "approved",
        "running",
        "completed",
        "rejected",
        "stale",
        "failed",
        "cancelled",
    }
)
SEMANTIC_STATES = frozenset({"candidate", "reviewed", "approved", "rejected", "stale"})
DECISIONS = frozenset({"approve", "reject", "revise"})
SHA256_RE = re.compile(r"[0-9a-f]{64}")
STABLE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}")


class SemanticProtocolError(ValueError):
    """Raised when a hard-shell semantic contract is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def stable_id(value: Any) -> bool:
    return bool(STABLE_ID_RE.fullmatch(str(value or "")))


def build_artifact_envelope(
    *,
    artifact_id: str,
    artifact_kind: str,
    scope: dict[str, Any],
    state: str,
    created_by: str,
    input_hashes: Iterable[str] = (),
    dependencies: Iterable[str] = (),
    content_sha256: str = "",
    created_at: str = "",
) -> dict[str, Any]:
    """Build the shared identity/provenance envelope used by semantic artifacts."""

    envelope = {
        "schema": ARTIFACT_ENVELOPE_SCHEMA,
        "artifact_id": artifact_id,
        "artifact_kind": str(artifact_kind).strip(),
        "scope": dict(scope),
        "state": str(state).strip(),
        "input_hashes": sorted({str(item) for item in input_hashes if str(item)}),
        "content_sha256": str(content_sha256),
        "created_by": str(created_by).strip(),
        "dependencies": sorted({str(item) for item in dependencies if str(item)}),
        "created_at": created_at or utc_now(),
        "updated_at": created_at or utc_now(),
    }
    errors = validate_artifact_envelope(envelope)
    if errors:
        raise SemanticProtocolError("; ".join(errors))
    return envelope


def validate_artifact_envelope(value: Any) -> list[str]:
    required = {
        "schema",
        "artifact_id",
        "artifact_kind",
        "scope",
        "state",
        "input_hashes",
        "content_sha256",
        "created_by",
        "dependencies",
        "created_at",
        "updated_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        return [f"artifact envelope must contain exactly: {', '.join(sorted(required))}"]
    errors: list[str] = []
    if value.get("schema") != ARTIFACT_ENVELOPE_SCHEMA:
        errors.append(f"artifact envelope schema must be {ARTIFACT_ENVELOPE_SCHEMA}")
    if not stable_id(value.get("artifact_id")):
        errors.append("artifact_id must be a stable id")
    if not str(value.get("artifact_kind") or "").strip():
        errors.append("artifact_kind is required")
    if not isinstance(value.get("scope"), dict) or not value["scope"]:
        errors.append("scope must be a non-empty object")
    if not str(value.get("state") or "").strip():
        errors.append("state is required")
    for field in ("input_hashes", "dependencies"):
        items = value.get(field)
        if not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items):
            errors.append(f"{field} must be a list of non-empty strings")
    raw_input_hashes = value.get("input_hashes")
    input_hashes = raw_input_hashes if isinstance(raw_input_hashes, list) else []
    if any(not SHA256_RE.fullmatch(item) for item in input_hashes):
        errors.append("input_hashes must contain SHA-256 digests")
    digest = str(value.get("content_sha256") or "")
    if digest and not SHA256_RE.fullmatch(digest):
        errors.append("content_sha256 must be empty or a SHA-256 digest")
    if not str(value.get("created_by") or "").strip():
        errors.append("created_by is required")
    for field in ("created_at", "updated_at"):
        if not _valid_datetime(value.get(field)):
            errors.append(f"{field} must be an ISO-8601 timestamp")
    return errors


def validate_evidence_reference(value: Any) -> list[str]:
    """Validate an open media locator without imposing a closed locator ontology."""

    required = {
        "schema",
        "evidence_id",
        "item_id",
        "asset_id",
        "segment_id",
        "locator",
        "excerpt",
        "excerpt_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        return [f"evidence reference must contain exactly: {', '.join(sorted(required))}"]
    errors: list[str] = []
    if value.get("schema") != EVIDENCE_REFERENCE_SCHEMA:
        errors.append(f"schema must be {EVIDENCE_REFERENCE_SCHEMA}")
    for field in ("evidence_id", "item_id", "asset_id"):
        if not stable_id(value.get(field)):
            errors.append(f"{field} must be a stable id")
    segment_id = str(value.get("segment_id") or "")
    if segment_id and not stable_id(segment_id):
        errors.append("segment_id must be empty or a stable id")
    locator = value.get("locator")
    if not isinstance(locator, dict) or not str(locator.get("kind") or "").strip():
        errors.append("locator must be an object with a non-empty kind")
    excerpt = value.get("excerpt")
    if not isinstance(excerpt, str):
        errors.append("excerpt must be a string")
        excerpt = ""
    if sha256(excerpt.encode("utf-8")).hexdigest() != value.get("excerpt_sha256"):
        errors.append("excerpt_sha256 does not match excerpt")
    return errors


def build_semantic_document(
    *,
    document_id: str,
    document_type: str,
    title: str,
    scope: dict[str, Any],
    continuity: str,
    body: str,
    claims: Iterable[dict[str, Any]] = (),
    evidence_references: Iterable[dict[str, Any]] = (),
    uncertainties: Iterable[str] = (),
    extensions: dict[str, Any] | None = None,
    state: str = "candidate",
    created_by: str = "host_agent",
    input_hashes: Iterable[str] = (),
) -> dict[str, Any]:
    semantic_content = {
        "document_type": str(document_type).strip(),
        "title": str(title).strip(),
        "continuity": str(continuity).strip(),
        "body": str(body),
        "claims": [dict(item) for item in claims],
        "evidence_references": [dict(item) for item in evidence_references],
        "uncertainties": [str(item) for item in uncertainties],
        "extensions": dict(extensions or {}),
    }
    envelope = build_artifact_envelope(
        artifact_id=document_id,
        artifact_kind="semantic_document",
        scope=scope,
        state=state,
        created_by=created_by,
        input_hashes=input_hashes,
        content_sha256=canonical_json_hash(semantic_content),
    )
    payload = {"schema": SEMANTIC_DOCUMENT_SCHEMA, "artifact": envelope, **semantic_content}
    errors = validate_semantic_document(payload)
    if errors:
        raise SemanticProtocolError("; ".join(errors))
    return payload


def validate_semantic_document(value: Any, *, require_approved: bool = False) -> list[str]:
    required = {
        "schema",
        "artifact",
        "document_type",
        "title",
        "continuity",
        "body",
        "claims",
        "evidence_references",
        "uncertainties",
        "extensions",
    }
    if not isinstance(value, dict) or set(value) != required:
        return [f"semantic document must contain exactly: {', '.join(sorted(required))}"]
    errors: list[str] = []
    if value.get("schema") != SEMANTIC_DOCUMENT_SCHEMA:
        errors.append(f"schema must be {SEMANTIC_DOCUMENT_SCHEMA}")
    envelope = value.get("artifact")
    errors.extend(f"artifact.{item}" for item in validate_artifact_envelope(envelope))
    if isinstance(envelope, dict):
        if envelope.get("artifact_kind") != "semantic_document":
            errors.append("artifact.artifact_kind must be semantic_document")
        if envelope.get("state") not in SEMANTIC_STATES:
            errors.append("artifact.state is not a semantic document state")
        if require_approved and envelope.get("state") != "approved":
            errors.append("semantic document must be approved")
    for field in ("document_type", "title", "continuity"):
        if not str(value.get(field) or "").strip():
            errors.append(f"{field} is required")
    if not isinstance(value.get("body"), str):
        errors.append("body must be a string")
    evidence = value.get("evidence_references")
    if not isinstance(evidence, list):
        errors.append("evidence_references must be a list")
        evidence = []
    evidence_ids: set[str] = set()
    for index, item in enumerate(evidence):
        item_errors = validate_evidence_reference(item)
        errors.extend(f"evidence_references[{index}].{error}" for error in item_errors)
        if isinstance(item, dict):
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id in evidence_ids:
                errors.append(f"evidence_references[{index}].evidence_id must be unique")
            evidence_ids.add(evidence_id)
    claims = value.get("claims")
    if not isinstance(claims, list):
        errors.append("claims must be a list")
        claims = []
    claim_ids: set[str] = set()
    for index, claim in enumerate(claims):
        errors.extend(
            f"claims[{index}].{error}"
            for error in _validate_semantic_claim(claim, evidence_ids=evidence_ids)
        )
        if isinstance(claim, dict):
            claim_id = str(claim.get("claim_id") or "")
            if claim_id in claim_ids:
                errors.append(f"claims[{index}].claim_id must be unique")
            claim_ids.add(claim_id)
    uncertainties = value.get("uncertainties")
    if not isinstance(uncertainties, list) or any(
        not isinstance(item, str) or not item.strip() for item in uncertainties
    ):
        errors.append("uncertainties must be a list of non-empty strings")
    if not isinstance(value.get("extensions"), dict):
        errors.append("extensions must be an object")
    elif isinstance(envelope, dict) and envelope.get("state") == "approved":
        decision = value["extensions"].get("human_decision")
        decision_errors = validate_human_decision(decision)
        errors.extend(f"extensions.human_decision.{item}" for item in decision_errors)
        if isinstance(decision, dict):
            if decision.get("decision") != "approve":
                errors.append("approved semantic document requires an approve decision")
            if decision.get("target_id") != envelope.get("artifact_id"):
                errors.append("human decision target_id does not match approved artifact")
            if decision.get("target_sha256") != value["extensions"].get(
                "approved_candidate_sha256"
            ):
                errors.append("approved candidate hash does not match the human decision")
    content = {
        key: value[key]
        for key in (
            "document_type",
            "title",
            "continuity",
            "body",
            "claims",
            "evidence_references",
            "uncertainties",
            "extensions",
        )
    }
    if isinstance(envelope, dict):
        digest = str(envelope.get("content_sha256") or "")
        if digest and digest != canonical_json_hash(content):
            errors.append("artifact.content_sha256 does not match semantic content")
        elif not digest and envelope.get("state") != "candidate":
            errors.append("non-candidate semantic documents require artifact.content_sha256")
    return errors


def build_workflow_record(
    *,
    workflow_id: str,
    workflow_kind: str,
    scope: dict[str, Any],
    state: str,
    inputs: Iterable[dict[str, Any]] = (),
    outputs: Iterable[dict[str, Any]] = (),
    authorization: dict[str, Any] | None = None,
    diagnostics: Iterable[dict[str, Any]] = (),
    extensions: dict[str, Any] | None = None,
    created_at: str = "",
) -> dict[str, Any]:
    payload = {
        "schema": WORKFLOW_RECORD_SCHEMA,
        "workflow_id": workflow_id,
        "workflow_kind": str(workflow_kind).strip(),
        "scope": dict(scope),
        "state": state,
        "inputs": [dict(item) for item in inputs],
        "outputs": [dict(item) for item in outputs],
        "authorization": dict(authorization or {}),
        "diagnostics": [dict(item) for item in diagnostics],
        "extensions": dict(extensions or {}),
        "created_at": created_at or utc_now(),
        "updated_at": created_at or utc_now(),
    }
    errors = validate_workflow_record(payload)
    if errors:
        raise SemanticProtocolError("; ".join(errors))
    return payload


def validate_workflow_record(value: Any) -> list[str]:
    required = {
        "schema",
        "workflow_id",
        "workflow_kind",
        "scope",
        "state",
        "inputs",
        "outputs",
        "authorization",
        "diagnostics",
        "extensions",
        "created_at",
        "updated_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        return [f"workflow record must contain exactly: {', '.join(sorted(required))}"]
    errors: list[str] = []
    if value.get("schema") != WORKFLOW_RECORD_SCHEMA:
        errors.append(f"schema must be {WORKFLOW_RECORD_SCHEMA}")
    if not stable_id(value.get("workflow_id")):
        errors.append("workflow_id must be a stable id")
    if not str(value.get("workflow_kind") or "").strip():
        errors.append("workflow_kind is required")
    if value.get("state") not in WORKFLOW_STATES:
        errors.append("state is not a supported workflow state")
    if not isinstance(value.get("scope"), dict) or not value["scope"]:
        errors.append("scope must be a non-empty object")
    for field in ("inputs", "outputs", "diagnostics"):
        if not isinstance(value.get(field), list) or any(
            not isinstance(item, dict) for item in value.get(field) or []
        ):
            errors.append(f"{field} must be a list of objects")
    for field in ("authorization", "extensions"):
        if not isinstance(value.get(field), dict):
            errors.append(f"{field} must be an object")
    for field in ("created_at", "updated_at"):
        if not _valid_datetime(value.get(field)):
            errors.append(f"{field} must be an ISO-8601 timestamp")
    return errors


def build_human_decision(
    *,
    decision_id: str,
    target_id: str,
    target_sha256: str,
    decision: str,
    decided_by: str,
    reason: str,
    scope: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": HUMAN_DECISION_SCHEMA,
        "decision_id": decision_id,
        "target_id": target_id,
        "target_sha256": target_sha256,
        "decision": decision,
        "decided_by": decided_by,
        "reason": reason,
        "scope": dict(scope),
        "decided_at": utc_now(),
    }
    errors = validate_human_decision(payload)
    if errors:
        raise SemanticProtocolError("; ".join(errors))
    return payload


def validate_human_decision(value: Any) -> list[str]:
    required = {
        "schema",
        "decision_id",
        "target_id",
        "target_sha256",
        "decision",
        "decided_by",
        "reason",
        "scope",
        "decided_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        return [f"human decision must contain exactly: {', '.join(sorted(required))}"]
    errors: list[str] = []
    if value.get("schema") != HUMAN_DECISION_SCHEMA:
        errors.append(f"schema must be {HUMAN_DECISION_SCHEMA}")
    for field in ("decision_id", "target_id"):
        if not stable_id(value.get(field)):
            errors.append(f"{field} must be a stable id")
    if not SHA256_RE.fullmatch(str(value.get("target_sha256") or "")):
        errors.append("target_sha256 must be a SHA-256 digest")
    if value.get("decision") not in DECISIONS:
        errors.append("decision must be approve, reject, or revise")
    if value.get("decided_by") != "human":
        errors.append("decided_by must be human")
    if not str(value.get("reason") or "").strip():
        errors.append("reason is required")
    if not isinstance(value.get("scope"), dict) or not value["scope"]:
        errors.append("scope must be a non-empty object")
    if not _valid_datetime(value.get("decided_at")):
        errors.append("decided_at must be an ISO-8601 timestamp")
    return errors


def approved_semantic_document(
    document: dict[str, Any], *, decision: dict[str, Any]
) -> dict[str, Any]:
    """Return a newly hashed approved projection after verifying a human decision."""

    document = seal_semantic_document(document)
    errors = validate_semantic_document(document)
    if errors:
        raise SemanticProtocolError("; ".join(errors))
    decision_errors = validate_human_decision(decision)
    if decision_errors:
        raise SemanticProtocolError("; ".join(decision_errors))
    envelope = document["artifact"]
    if decision["target_id"] != envelope["artifact_id"]:
        raise SemanticProtocolError("human decision target_id does not match semantic document")
    if decision["target_sha256"] != envelope["content_sha256"]:
        raise SemanticProtocolError("human decision target hash is stale")
    if decision["decision"] != "approve":
        raise SemanticProtocolError("only an approve decision can promote a semantic document")
    approved = json.loads(json.dumps(document, ensure_ascii=False))
    approved["artifact"]["state"] = "approved"
    approved["artifact"]["updated_at"] = decision["decided_at"]
    approved["extensions"]["approved_candidate_sha256"] = decision["target_sha256"]
    approved["extensions"]["human_decision"] = decision
    approved["artifact"]["content_sha256"] = canonical_json_hash(
        {
            key: approved[key]
            for key in (
                "document_type",
                "title",
                "continuity",
                "body",
                "claims",
                "evidence_references",
                "uncertainties",
                "extensions",
            )
        }
    )
    return approved


def seal_semantic_document(document: dict[str, Any]) -> dict[str, Any]:
    """Bind candidate semantic content to a deterministic hash owned by the CLI."""

    sealed = json.loads(json.dumps(document, ensure_ascii=False))
    content = {
        key: sealed[key]
        for key in (
            "document_type",
            "title",
            "continuity",
            "body",
            "claims",
            "evidence_references",
            "uncertainties",
            "extensions",
        )
    }
    sealed["artifact"]["content_sha256"] = canonical_json_hash(content)
    sealed["artifact"]["updated_at"] = utc_now()
    errors = validate_semantic_document(sealed)
    if errors:
        raise SemanticProtocolError("; ".join(errors))
    return sealed


def _validate_semantic_claim(value: Any, *, evidence_ids: set[str]) -> list[str]:
    required = {
        "claim_id",
        "statement",
        "applicability",
        "evidence_refs",
        "uncertainty",
        "extensions",
    }
    if not isinstance(value, dict) or set(value) != required:
        return [f"claim must contain exactly: {', '.join(sorted(required))}"]
    errors: list[str] = []
    if not stable_id(value.get("claim_id")):
        errors.append("claim_id must be a stable id")
    if not str(value.get("statement") or "").strip():
        errors.append("statement is required")
    if not isinstance(value.get("applicability"), (str, dict)):
        errors.append("applicability must be natural-language text or an object")
    refs = value.get("evidence_refs")
    if not isinstance(refs, list) or any(not stable_id(item) for item in refs):
        errors.append("evidence_refs must contain stable evidence ids")
    elif any(str(item) not in evidence_ids for item in refs):
        errors.append("evidence_refs contains an undeclared evidence id")
    if not isinstance(value.get("uncertainty"), str):
        errors.append("uncertainty must be a string")
    if not isinstance(value.get("extensions"), dict):
        errors.append("extensions must be an object")
    return errors


def _valid_datetime(value: Any) -> bool:
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return True


__all__ = [
    "ARTIFACT_ENVELOPE_SCHEMA",
    "DECISIONS",
    "EVIDENCE_REFERENCE_SCHEMA",
    "HUMAN_DECISION_SCHEMA",
    "SEMANTIC_DOCUMENT_SCHEMA",
    "SEMANTIC_STATES",
    "WORKFLOW_RECORD_SCHEMA",
    "WORKFLOW_STATES",
    "SemanticProtocolError",
    "approved_semantic_document",
    "build_artifact_envelope",
    "build_human_decision",
    "build_semantic_document",
    "build_workflow_record",
    "canonical_json_hash",
    "stable_id",
    "seal_semantic_document",
    "utc_now",
    "validate_artifact_envelope",
    "validate_evidence_reference",
    "validate_human_decision",
    "validate_semantic_document",
    "validate_workflow_record",
]
