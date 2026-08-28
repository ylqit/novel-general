"""Shared validation for human-approved, realized fanfiction divergences."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping
import json


KNOWLEDGE_SEMANTIC_TYPES = frozenset(
    {"人物知识边界", "人物阶段与知识边界", "未来知识可靠性"}
)


def derive_major_divergence_trigger_id(
    source_event_id: str,
    source_claim_id: str,
    realized_chapter: int,
    declaration_id: str,
) -> str:
    identity = {
        "source_event_id": source_event_id,
        "source_claim_id": source_claim_id,
        "realized_chapter": realized_chapter,
        "declaration_id": declaration_id,
    }
    digest = sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()[:24]
    return f"major_divergence:{digest}"


def realized_major_divergence_errors(
    *,
    root: Path,
    chapter_number: int,
    divergences: Any,
    event_payload: Mapping[str, Any],
    review_claims: Mapping[str, Mapping[str, Any]],
    final_path: Path,
    semantic_path: Path,
    context_path: Path,
    projected_states: Mapping[str, str] | None = None,
    require_stored_bindings: bool,
    existing_logical_identities: set[tuple[str, str, int, str]] | None = None,
) -> list[str]:
    """Deep-validate declaration identity, semantic scope, exact evidence and bindings."""

    if not isinstance(divergences, list):
        return ["realized_major_divergences must be a list"]
    errors: list[str] = []
    final_text = final_path.read_text(encoding="utf-8") if final_path.is_file() else ""
    events = {
        str(item.get("event_id") or ""): item
        for item in event_payload.get("events") or []
        if isinstance(item, dict) and item.get("event_id")
    }
    application_sha = str(event_payload.get("realization_application_sha256") or "")
    base_fields = {
        "trigger_id",
        "declaration_id",
        "source_event_id",
        "source_claim_id",
        "realized_chapter",
        "impact_level",
        "knowledge_scope_refs",
        "human_confirmation",
        "evidence",
    }
    binding_fields = {
        "final_path",
        "final_sha256",
        "semantic_ledger_path",
        "semantic_ledger_sha256",
        "fanfiction_context_path",
        "fanfiction_context_sha256",
        "realization_application_sha256",
    }
    seen_triggers: set[str] = set()
    seen_logical: set[tuple[str, str, int, str]] = set()
    prior = existing_logical_identities or set()
    for index, divergence in enumerate(divergences):
        prefix = f"realized_major_divergences[{index}]"
        expected_fields = base_fields | (binding_fields if require_stored_bindings else set())
        if not isinstance(divergence, dict) or set(divergence) != expected_fields:
            errors.append(f"{prefix} fields are invalid")
            continue
        declaration_id = str(divergence.get("declaration_id") or "")
        source_event_id = str(divergence.get("source_event_id") or "")
        source_claim_id = str(divergence.get("source_claim_id") or "")
        if not declaration_id or not source_event_id or not source_claim_id:
            errors.append(f"{prefix} identity fields must be stable and non-empty")
        logical = (source_event_id, source_claim_id, chapter_number, declaration_id)
        expected_trigger = derive_major_divergence_trigger_id(*logical)
        trigger_id = str(divergence.get("trigger_id") or "")
        if trigger_id != expected_trigger:
            errors.append(f"{prefix}.trigger_id must be engine-derived from logical identity")
        if trigger_id in seen_triggers:
            errors.append(f"duplicate realized divergence trigger_id: {trigger_id}")
        if logical in seen_logical or logical in prior:
            errors.append(f"{prefix} has duplicate logical divergence identity")
        seen_triggers.add(trigger_id)
        seen_logical.add(logical)
        event = events.get(source_event_id)
        if event is None:
            errors.append(f"{prefix}.source_event_id is not an approved planned event")
        else:
            state = (
                projected_states.get(source_event_id)
                if projected_states is not None
                else event.get("state")
            )
            if state != "realized":
                errors.append(f"{prefix}.source_event_id must be realized")
            if source_claim_id not in (event.get("fanfiction_claim_refs") or []):
                errors.append(f"{prefix}.source_claim_id is not approved by the source event")
        source_claim = review_claims.get(source_claim_id)
        semantic_type = str((source_claim or {}).get("semantic_type") or "")
        extensions_value = source_claim.get("extensions") if isinstance(source_claim, dict) else None
        extensions: dict[str, Any] = (
            dict(extensions_value) if isinstance(extensions_value, dict) else {}
        )
        disposition = str(extensions.get("disposition") or "")
        if source_claim is None:
            errors.append(f"{prefix}.source claim is not selected in review_projection")
        elif semantic_type != "初始分歧" and not (
            semantic_type == "原著事件命运" and disposition not in {"", "保留"}
        ):
            errors.append(f"{prefix}.source claim is not a realized major divergence")
        if divergence.get("realized_chapter") != chapter_number:
            errors.append(f"{prefix}.realized_chapter must match chapter_number")
        if divergence.get("impact_level") != "major":
            errors.append(f"{prefix}.impact_level must be major")
        knowledge_refs = divergence.get("knowledge_scope_refs")
        if (
            not isinstance(knowledge_refs, list)
            or not knowledge_refs
            or any(not isinstance(item, str) or not item for item in knowledge_refs)
            or len(knowledge_refs) != len(set(knowledge_refs))
        ):
            errors.append(f"{prefix}.knowledge_scope_refs must be a non-empty unique string list")
        else:
            for claim_id in knowledge_refs:
                claim = review_claims.get(claim_id)
                if claim is None or claim.get("semantic_type") not in KNOWLEDGE_SEMANTIC_TYPES:
                    errors.append(f"{prefix}.knowledge scope claim is invalid: {claim_id}")
        confirmation = divergence.get("human_confirmation")
        if (
            not isinstance(confirmation, dict)
            or set(confirmation) != {"confirmed_by", "reason"}
            or confirmation.get("confirmed_by") != "human"
            or not str(confirmation.get("reason") or "").strip()
        ):
            errors.append(f"{prefix}.human_confirmation must contain human and a reason")
        errors.extend(_exact_span_errors(divergence.get("evidence"), final_text, prefix))
        if require_stored_bindings:
            expected_bindings = {
                "final_path": final_path.relative_to(root).as_posix(),
                "final_sha256": _file_hash(final_path),
                "semantic_ledger_path": semantic_path.relative_to(root).as_posix(),
                "semantic_ledger_sha256": _file_hash(semantic_path),
                "fanfiction_context_path": context_path.relative_to(root).as_posix(),
                "fanfiction_context_sha256": _file_hash(context_path),
                "realization_application_sha256": application_sha,
            }
            for field, expected in expected_bindings.items():
                if divergence.get(field) != expected:
                    errors.append(f"{prefix}.{field} is stale")
    return errors


def _exact_span_errors(value: Any, source: str, prefix: str) -> list[str]:
    if not isinstance(value, dict) or set(value) != {"start", "end", "excerpt"}:
        return [f"{prefix}.evidence must contain exactly start, end, excerpt"]
    start, end, excerpt = value.get("start"), value.get("end"), value.get("excerpt")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(source)
        or source[start:end] != excerpt
    ):
        return [f"{prefix}.evidence does not match the current final text"]
    return []


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


__all__ = ["derive_major_divergence_trigger_id", "realized_major_divergence_errors"]
