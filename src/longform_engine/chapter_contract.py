"""The only formal chapter contract accepted by the v0.10 production line."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from longform_engine.reader_promises_v2 import validate_promise_actions_v2


CONTRACT_SCHEMA = "chapter_contract_v5"
TOPOLOGIES = frozenset(
    {"escalation", "revelation", "aftermath", "relationship", "transition", "payoff"}
)
APPLICABILITY = frozenset({"required", "optional", "not_applicable"})
APPLICABILITY_FIELDS = ("failure", "choice", "cost", "aftermath")
CONTRACT_FIELDS = {
    "schema",
    "contract_id",
    "chapter_number",
    "forecast_ref",
    "topology",
    "chapter_duty",
    "observable_change",
    "reader_value",
    "failure",
    "choice",
    "cost",
    "aftermath",
    "plot_node_table_ref",
    "semantic_obligation_refs",
    "reader_promise_actions",
    "protected_invariants",
    "prohibited_drift",
}


class ChapterContractError(ValueError):
    """Raised when current v0.10 chapter evidence is missing, incompatible, or stale."""


def validate_chapter_contract(
    value: Any,
    *,
    promise_ledger: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or set(value) != CONTRACT_FIELDS:
        return ["chapter_contract_v5 fields are invalid; v0.9 chapter cards are incompatible"]
    if value.get("schema") != CONTRACT_SCHEMA:
        errors.append(f"schema must be {CONTRACT_SCHEMA}")
    contract_id = value.get("contract_id")
    if not isinstance(contract_id, str) or not contract_id.startswith("contract:ch"):
        errors.append("contract_id must be a stable contract:chNNN ID")
    chapter = value.get("chapter_number")
    if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
        errors.append("chapter_number must be positive")
    if value.get("topology") not in TOPOLOGIES:
        errors.append("topology is invalid")
    for field in ("forecast_ref", "chapter_duty", "observable_change", "reader_value"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            errors.append(f"{field} must be non-empty")
    for field in APPLICABILITY_FIELDS:
        _validate_applicability(value.get(field), field, errors)
    node_ref = value.get("plot_node_table_ref")
    if not isinstance(node_ref, dict) or set(node_ref) != {"table_id", "candidate_sha256"}:
        errors.append("plot_node_table_ref fields are invalid")
    else:
        if not isinstance(node_ref.get("table_id"), str) or not node_ref["table_id"].strip():
            errors.append("plot_node_table_ref.table_id must be non-empty")
        if not _is_sha256(node_ref.get("candidate_sha256")):
            errors.append("plot_node_table_ref.candidate_sha256 must be SHA-256")
    for field in ("semantic_obligation_refs", "protected_invariants", "prohibited_drift"):
        raw = value.get(field)
        if not isinstance(raw, list) or any(
            not isinstance(item, str) or not item.strip() for item in raw
        ):
            errors.append(f"{field} must be a string list")
        elif len(raw) != len(set(raw)):
            errors.append(f"{field} must not contain duplicates")
    if not value.get("semantic_obligation_refs"):
        errors.append("semantic_obligation_refs must not be empty in the firm tier")
    if not value.get("protected_invariants"):
        errors.append("protected_invariants must not be empty")
    actions = value.get("reader_promise_actions")
    if not isinstance(actions, list):
        errors.append("reader_promise_actions must be a list")
    elif promise_ledger is not None:
        errors.extend(
            validate_promise_actions_v2(
                actions,
                promise_ledger,
                chapter_number=chapter if isinstance(chapter, int) else None,
            )
        )
    return errors


def chapter_contract_hash(contract: dict[str, Any]) -> str:
    return sha256(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def stamp_chapter_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Return a hash-stamped v5 contract; old chapter-card projection is intentionally absent."""

    errors = validate_chapter_contract(contract)
    if errors:
        raise ChapterContractError("chapter_contract_v5_invalid:" + ";".join(errors))
    return {**contract, "chapter_contract_hash": chapter_contract_hash(contract)}


def load_verified_chapter_contract(root: Path, chapter_number: int) -> tuple[dict[str, Any], str]:
    path = root / "20_outline" / "chapter_contracts" / f"ch{chapter_number:03d}.json"
    _reject_stale_artifact(root, path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChapterContractError(
            "chapter_contract_v5_unreadable; v0.9 chapter cards are not accepted: " + str(exc)
        ) from exc
    if not isinstance(payload, dict) or set(payload) != CONTRACT_FIELDS | {"chapter_contract_hash"}:
        raise ChapterContractError("chapter_contract_v5_invalid:fields")
    contract = {field: payload[field] for field in CONTRACT_FIELDS}
    ledger_path = root / "30_state" / "reader_promise_ledger.json"
    try:
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChapterContractError(f"reader_promise_ledger_v2_unreadable:{exc}") from exc
    if not isinstance(ledger, dict) or ledger.get("schema") != "reader_promise_ledger_v2":
        raise ChapterContractError("chapter_contract_v5_requires_reader_promise_ledger_v2")
    errors = validate_chapter_contract(contract, promise_ledger=ledger)
    if errors:
        raise ChapterContractError("chapter_contract_v5_invalid:" + ";".join(errors))
    if contract["chapter_number"] != chapter_number:
        raise ChapterContractError("chapter_contract_v5_invalid:chapter_number")
    digest = chapter_contract_hash(contract)
    if payload.get("chapter_contract_hash") != digest:
        raise ChapterContractError("chapter_contract_v5_invalid:hash")
    _validate_plot_node_table(root, contract, chapter_number)
    _validate_semantic_obligations(root, contract)
    return contract, digest


def resolve_chapter_contract_refs(root: Path, contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve author facts through stable fact IDs and explicit obligation dependencies only."""

    obligation_path = root / "30_state" / "semantic_obligations.json"
    obligation_payload = _read_json_object(obligation_path, "semantic_obligation_ledger")
    obligations = {
        str(item.get("obligation_id")): item
        for item in obligation_payload.get("items", [])
        if isinstance(item, dict) and item.get("obligation_id")
    }
    selected = [obligations[ref] for ref in contract["semantic_obligation_refs"]]
    fact_ids: list[str] = []
    for obligation in selected:
        for field in ("subject_refs", "prior_state_refs", "dependency_refs"):
            for fact_id in obligation.get(field) or []:
                token = str(fact_id)
                if token not in fact_ids:
                    fact_ids.append(token)
    fact_path = root / "10_bible" / "canonical_facts.json"
    if not fact_ids:
        return []
    facts_payload = _read_json_object(fact_path, "canonical_fact_registry_v2")
    if facts_payload.get("schema") != "canonical_fact_registry_v2":
        raise ChapterContractError("canonical_fact_v2_registry_incompatible")
    facts = {
        str(item.get("fact_id")): item
        for item in facts_payload.get("items", [])
        if isinstance(item, dict) and item.get("schema") == "canonical_fact_v2"
    }
    missing = [fact_id for fact_id in fact_ids if fact_id not in facts]
    if missing:
        raise ChapterContractError(
            "context_evidence_incomplete:unresolved_stable_fact_ids:" + ",".join(missing)
        )
    digest = sha256(fact_path.read_bytes()).hexdigest()
    return [
        {
            "kind": "canonical_fact",
            "ref": fact_id,
            "source": fact_path.relative_to(root).as_posix(),
            "sha256": digest,
            "value": facts[fact_id].get("statement"),
        }
        for fact_id in fact_ids
    ]


def _validate_plot_node_table(root: Path, contract: dict[str, Any], chapter_number: int) -> None:
    node_ref = contract["plot_node_table_ref"]
    node_path = root / "20_outline" / "plot_nodes" / f"ch{chapter_number:03d}.json"
    node_table = _read_json_object(node_path, "plot_node_table")
    if (
        node_table.get("table_id") != node_ref["table_id"]
        or node_table.get("candidate_sha256") != node_ref["candidate_sha256"]
        or not node_table.get("nodes")
        or any(
            not isinstance(node, dict)
            or not isinstance(node.get("human_decision"), dict)
            or node["human_decision"].get("decision") not in {"approve", "adjust"}
            for node in node_table.get("nodes", [])
        )
    ):
        raise ChapterContractError("chapter_contract_v5_invalid:plot_node_table_ref_stale")


def _validate_semantic_obligations(root: Path, contract: dict[str, Any]) -> None:
    payload = _read_json_object(
        root / "30_state" / "semantic_obligations.json", "semantic_obligation_ledger"
    )
    known = {
        str(item.get("obligation_id"))
        for item in payload.get("items", [])
        if isinstance(item, dict) and item.get("obligation_id")
    }
    missing = sorted(set(contract["semantic_obligation_refs"]) - known)
    if missing:
        raise ChapterContractError(
            "chapter_contract_v5_invalid:semantic_obligations_unresolved:" + ",".join(missing)
        )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChapterContractError(f"{label}_unreadable:{exc}") from exc
    if not isinstance(payload, dict):
        raise ChapterContractError(f"{label}_invalid")
    return payload


def _reject_stale_artifact(root: Path, path: Path) -> None:
    registry = root / "30_state" / "stale_artifacts.json"
    if not registry.is_file():
        return
    payload = _read_json_object(registry, "stale_artifact_registry")
    relative = path.relative_to(root).as_posix()
    if any(
        isinstance(item, dict)
        and item.get("artifact_path") == relative
        and item.get("state") == "stale"
        for item in payload.get("items", [])
    ):
        raise ChapterContractError(
            f"chapter_contract_v5_stale_by_canon_change:{relative}; replan and bind a new hash"
        )


def _validate_applicability(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"applicability", "description", "reason"}:
        errors.append(f"{label} applicability fields are invalid")
        return
    applicability = value.get("applicability")
    if applicability not in APPLICABILITY:
        errors.append(f"{label}.applicability is invalid")
    description = value.get("description")
    reason = value.get("reason")
    if applicability == "required":
        if not isinstance(description, str) or not description.strip():
            errors.append(f"{label}.description is required")
    elif description not in {None, ""} and not isinstance(description, str):
        errors.append(f"{label}.description must be text or null")
    if applicability == "not_applicable":
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{label}.reason must explain not_applicable")
    elif reason not in {None, ""} and not isinstance(reason, str):
        errors.append(f"{label}.reason must be text or null")


def _is_sha256(value: Any) -> bool:
    token = str(value or "")
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


__all__ = [
    "APPLICABILITY",
    "CONTRACT_FIELDS",
    "CONTRACT_SCHEMA",
    "TOPOLOGIES",
    "ChapterContractError",
    "chapter_contract_hash",
    "load_verified_chapter_contract",
    "resolve_chapter_contract_refs",
    "stamp_chapter_contract",
    "validate_chapter_contract",
]
