"""Agent-first result contracts that do not mutate project state.

This module is intentionally isolated from production routing and canonical apply.
It defines what an Agent may return; later phases own normalization and persistence.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.agent_tasks import is_canonical_output, relative_path, resolve_under_root
from longform_engine.roles import RoleRegistry, load_role_registry, reject_duplicate_json_keys


AGENT_RESULT_ENVELOPE_SCHEMA = "agent_result_envelope_v1"
AGENT_OUTPUT_CONTRACT_SCHEMA = "agent_output_contract_v1"
RESULT_VERDICTS = frozenset({"pass", "repair", "need_human"})
REVIEW_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
DELTA_ACTIONS = frozenset({"add", "update", "remove", "observe", "declare", "index_section"})
DELTA_COVERAGE = frozenset({"changed", "unchanged"})
LEGACY_DOCUMENT_JSON_SCHEMAS = frozenset(
    {
        "adaptation_analysis_v1",
        "book_design_candidate_v2",
        "fanfiction_design_candidate_v1",
        "outline_design_candidate_v2",
        "outline_extension_candidate_v1",
        "outline_revision_candidate_v1",
    }
)

ENVELOPE_COMMON_FIELDS = frozenset(
    {"schema", "task", "scope", "verdict", "evidence", "notes"}
)
TASK_FIELDS = frozenset({"task_id", "task_type", "role_id"})
EVIDENCE_FIELDS = frozenset({"evidence_id", "source_ref", "start", "end", "excerpt"})
FINDING_FIELDS = frozenset(
    {"finding_id", "code", "severity", "summary", "evidence_refs", "recommendation"}
)
DELTA_FIELDS = frozenset(
    {
        "delta_id",
        "entity_id",
        "field",
        "action",
        "old_state",
        "new_state",
        "evidence_refs",
        "coverage",
    }
)
DOCUMENT_INDEX_STATE_FIELDS = frozenset(
    {"document_anchor", "stable_ids", "scope", "source_refs", "canonical_targets"}
)
AGENT_OWNED_CLI_FIELDS = frozenset(
    {"source_path", "source_hash", "chapter_number", "planned_facts", "canonical_source_hash"}
)
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
ANALYSIS_HEADING_PATTERN = re.compile(
    r"(?im)^#{1,6}\s*(analysis|reasoning|self[- ]?check|json|分析|说明|修订说明|作者说明)\s*$"
)


class AgentResultProtocolError(ValueError):
    """Raised when an Agent output contract is ambiguous or unsafe."""


@dataclass(frozen=True)
class AgentOutputContract:
    schema: str
    task_id: str
    task_type: str
    role_id: str
    output_mode: str
    output_schema: str
    allowed_output_paths: tuple[str, ...]
    primary_output_path: str
    companion_output_path: str
    record_lane: str
    validate_command: str
    apply_or_finalize_command: str
    failure_next_command: str
    cli_prefilled_fields: tuple[str, ...] = ("task", "scope")
    notes_authority: str = "non_authoritative"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "role_id": self.role_id,
            "output_mode": self.output_mode,
            "output_schema": self.output_schema,
            "allowed_output_paths": list(self.allowed_output_paths),
            "primary_output_path": self.primary_output_path,
            "companion_output_path": self.companion_output_path,
            "record_lane": self.record_lane,
            "validate_command": self.validate_command,
            "apply_or_finalize_command": self.apply_or_finalize_command,
            "failure_next_command": self.failure_next_command,
            "cli_prefilled_fields": list(self.cli_prefilled_fields),
            "notes_authority": self.notes_authority,
        }


@dataclass(frozen=True)
class AgentResultValidation:
    ok: bool
    output_mode: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedAgentOutput:
    """UTF-8, duplicate-key-safe files parsed from one declared Agent output."""

    output_mode: str
    result_path: str
    result_sha256: str
    text: str
    payload: dict[str, Any] | None
    document_path: str = ""
    document_sha256: str = ""
    document_text: str = ""


def read_utf8_output(path: Path, *, label: str) -> tuple[bytes, str]:
    """Read one declared output without accepting missing or lossy text."""

    if not path.is_file():
        raise AgentResultProtocolError(f"{label} does not exist or is not a file: {path}")
    try:
        payload = path.read_bytes()
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentResultProtocolError(f"{label} must be valid UTF-8: {path}") from exc
    return payload, text.lstrip("\ufeff")


def parse_agent_output_files(
    root: Path,
    manifest: dict[str, Any],
    *,
    result_file: str | Path,
    document_file: str | Path | None = None,
    registry: RoleRegistry | None = None,
) -> ParsedAgentOutput:
    """Parse only files declared by the role output contract; never mutate project state."""

    contract = compile_agent_output_contract(manifest, registry=registry)
    project_root = root.resolve()
    result_path = resolve_under_root(project_root, result_file)
    result_relative = relative_path(project_root, result_path)
    expected_result = (
        contract.primary_output_path
        if contract.output_mode == "markdown_prose"
        else contract.companion_output_path
        if contract.output_mode == "document_index_bundle"
        else contract.primary_output_path
    )
    if result_relative != expected_result:
        raise AgentResultProtocolError(
            f"result file must exactly match declared output `{expected_result}`; got `{result_relative}`."
        )
    result_bytes, result_text = read_utf8_output(result_path, label="Agent result")
    if contract.output_mode == "markdown_prose":
        return ParsedAgentOutput(
            output_mode=contract.output_mode,
            result_path=result_relative,
            result_sha256=sha256(result_bytes).hexdigest(),
            text=result_text,
            payload=None,
        )
    try:
        payload = json.loads(result_text, object_pairs_hook=reject_duplicate_json_keys)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AgentResultProtocolError(f"Agent result must be duplicate-key-safe JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AgentResultProtocolError("structured Agent result must be a JSON object.")

    document_path = ""
    document_hash = ""
    document_text = ""
    if contract.output_mode == "document_index_bundle":
        expected_document = resolve_under_root(project_root, contract.primary_output_path)
        supplied_document = (
            resolve_under_root(project_root, document_file)
            if document_file is not None
            else expected_document
        )
        if supplied_document != expected_document:
            raise AgentResultProtocolError(
                "document file must exactly match the declared Markdown companion output."
            )
        document_bytes, document_text = read_utf8_output(
            expected_document,
            label="Agent design document",
        )
        document_path = relative_path(project_root, expected_document)
        document_hash = sha256(document_bytes).hexdigest()
    return ParsedAgentOutput(
        output_mode=contract.output_mode,
        result_path=result_relative,
        result_sha256=sha256(result_bytes).hexdigest(),
        text=result_text,
        payload=payload,
        document_path=document_path,
        document_sha256=document_hash,
        document_text=document_text,
    )


def compile_agent_output_contract(
    manifest: dict[str, Any],
    *,
    registry: RoleRegistry | None = None,
) -> AgentOutputContract:
    """Compile one unambiguous, non-mutating output and handoff contract."""

    task_id = required_text(manifest.get("task_id"), "manifest.task_id")
    task_type = required_text(manifest.get("task_type"), "manifest.task_type")
    role_id = required_text(manifest.get("role_id"), "manifest.role_id")
    roles = registry or load_role_registry()
    try:
        role = roles.resolve(task_type, declared_role_id=role_id)
    except ValueError as exc:
        raise AgentResultProtocolError(str(exc)) from exc
    if role.role_id != role_id:
        raise AgentResultProtocolError(
            f"manifest role_id `{role_id}` does not match registered role `{role.role_id}`."
        )

    paths = normalize_output_paths(manifest.get("allowed_output_paths"))
    mode = role.output_mode
    if mode == "document_index_bundle":
        declared_schema = required_text(manifest.get("output_schema"), "manifest.output_schema")
        if (
            len(paths) == 1
            and paths[0].lower().endswith(".json")
            and declared_schema in LEGACY_DOCUMENT_JSON_SCHEMAS
        ):
            mode = "legacy_document_json"
            primary = paths[0]
            companion = ""
            record_lane = "deltas"
            output_schema = declared_schema
        elif len(paths) != 2:
            raise AgentResultProtocolError(
                "document_index_bundle requires exactly two unique outputs: one Markdown document and one JSON index."
            )
        else:
            markdown_paths = [item for item in paths if item.lower().endswith(".md")]
            json_paths = [item for item in paths if item.lower().endswith(".json")]
            if len(markdown_paths) != 1 or len(json_paths) != 1:
                raise AgentResultProtocolError(
                    "document_index_bundle outputs must contain exactly one .md document and one .json index."
                )
            primary = markdown_paths[0]
            companion = json_paths[0]
            record_lane = "deltas"
            output_schema = AGENT_RESULT_ENVELOPE_SCHEMA
    else:
        if len(paths) != 1:
            raise AgentResultProtocolError(f"{mode} requires exactly one unique allowed output path.")
        primary = paths[0]
        companion = ""
        if mode == "markdown_prose":
            if not primary.lower().endswith(".md"):
                raise AgentResultProtocolError("markdown_prose output must use a .md path.")
            record_lane = ""
            output_schema = "markdown_prose_only"
        elif mode == "compact_review_json":
            require_json_path(primary, mode)
            record_lane = "findings"
            output_schema = AGENT_RESULT_ENVELOPE_SCHEMA
        elif mode == "strict_delta_json":
            require_json_path(primary, mode)
            record_lane = "deltas"
            output_schema = AGENT_RESULT_ENVELOPE_SCHEMA
        else:
            raise AgentResultProtocolError(f"Unsupported role output mode `{mode}`.")

    validate_command = required_text(manifest.get("validate_command"), "manifest.validate_command")
    apply_command = required_text(manifest.get("apply_command"), "manifest.apply_command")
    failure_command = required_text(
        manifest.get("failure_next_command"), "manifest.failure_next_command"
    )
    return AgentOutputContract(
        schema=AGENT_OUTPUT_CONTRACT_SCHEMA,
        task_id=task_id,
        task_type=task_type,
        role_id=role_id,
        output_mode=mode,
        output_schema=output_schema,
        allowed_output_paths=tuple(paths),
        primary_output_path=primary,
        companion_output_path=companion,
        record_lane=record_lane,
        validate_command=validate_command,
        apply_or_finalize_command=apply_command,
        failure_next_command=failure_command,
    )


def build_agent_result_template(
    manifest: dict[str, Any],
    *,
    registry: RoleRegistry | None = None,
) -> dict[str, Any]:
    """Return a CLI-prefilled structured result template for the assigned role."""

    contract = compile_agent_output_contract(manifest, registry=registry)
    if contract.output_mode in {"markdown_prose", "legacy_document_json"}:
        raise AgentResultProtocolError(
            f"{contract.output_mode} has no Agent envelope template; follow its declared output schema."
        )
    payload: dict[str, Any] = {
        "schema": AGENT_RESULT_ENVELOPE_SCHEMA,
        "task": {
            "task_id": contract.task_id,
            "task_type": contract.task_type,
            "role_id": contract.role_id,
        },
        "scope": deepcopy(manifest.get("scope") or {}),
        "verdict": "",
        "evidence": [],
        contract.record_lane: [],
        "notes": [],
    }
    return payload


def render_agent_output_instructions(contract: AgentOutputContract) -> str:
    """Render the output-only section used by a later work-order integration phase."""

    paths = "\n".join(f"- `{item}`" for item in contract.allowed_output_paths)
    if contract.output_mode == "markdown_prose":
        shape = "Write only the complete replacement prose. Do not add JSON, analysis, or author notes."
    elif contract.output_mode == "compact_review_json":
        shape = (
            "Return agent_result_envelope_v1 with only CLI-prefilled task/scope, verdict, "
            "evidence, findings, and optional non-authoritative notes."
        )
    elif contract.output_mode == "strict_delta_json":
        shape = (
            "Return agent_result_envelope_v1 with only CLI-prefilled task/scope, verdict, "
            "evidence, explicit changed/unchanged deltas, and optional non-authoritative notes."
        )
    elif contract.output_mode == "legacy_document_json":
        shape = (
            f"Compatibility manifest: return the complete `{contract.output_schema}` JSON candidate. "
            "This mode remains explicit and does not claim the document/index migration is complete."
        )
    else:
        shape = (
            "Write the long design as Markdown and the compact apply index as an "
            "agent_result_envelope_v1 delta document."
        )
    return (
        "## Output And Handoff\n\n"
        f"Mode: `{contract.output_mode}`\n\n"
        "Allowed output paths (no other writes):\n"
        f"{paths}\n\n"
        f"{shape}\n\n"
        f"Validate: `{contract.validate_command}`\n\n"
        f"Apply/finalize: `{contract.apply_or_finalize_command}`\n\n"
        f"On failure: `{contract.failure_next_command}`\n"
    )


def validate_agent_result_envelope(
    manifest: dict[str, Any],
    payload: Any,
    *,
    registry: RoleRegistry | None = None,
) -> AgentResultValidation:
    """Validate the structural Agent judgment without normalizing canonical facts."""

    try:
        contract = compile_agent_output_contract(manifest, registry=registry)
    except AgentResultProtocolError as exc:
        return AgentResultValidation(False, "unknown", (str(exc),))
    if contract.output_mode == "markdown_prose":
        return AgentResultValidation(
            False,
            contract.output_mode,
            ("markdown_prose must be validated as raw prose, not a JSON envelope.",),
        )
    errors: list[str] = []
    if not isinstance(payload, dict):
        return AgentResultValidation(False, contract.output_mode, ("result must be a JSON object.",))

    expected_fields = set(ENVELOPE_COMMON_FIELDS) | {contract.record_lane}
    if set(payload) != expected_fields:
        errors.append(
            "result fields must be exactly: " + ", ".join(sorted(expected_fields)) + "."
        )
    if payload.get("schema") != AGENT_RESULT_ENVELOPE_SCHEMA:
        errors.append(f"schema must be {AGENT_RESULT_ENVELOPE_SCHEMA}.")
    expected_task = {
        "task_id": contract.task_id,
        "task_type": contract.task_type,
        "role_id": contract.role_id,
    }
    if payload.get("task") != expected_task:
        errors.append("task is CLI-prefilled and must exactly match the manifest task contract.")
    if payload.get("scope") != manifest.get("scope"):
        errors.append("scope is CLI-prefilled and must exactly match the manifest scope.")
    verdict = payload.get("verdict")
    if verdict not in RESULT_VERDICTS:
        errors.append("verdict must be one of: pass, repair, need_human.")

    evidence_ids = validate_evidence(payload.get("evidence"), errors)
    validate_notes(payload.get("notes"), errors)
    if contract.output_mode == "compact_review_json":
        validate_findings(payload.get("findings"), evidence_ids, verdict, errors)
        forbidden = find_forbidden_cli_fields(payload.get("evidence"), "evidence")
        forbidden.extend(find_forbidden_cli_fields(payload.get("findings"), "findings"))
        errors.extend(forbidden)
    else:
        validate_deltas(payload.get("deltas"), evidence_ids, verdict, errors)
    return AgentResultValidation(not errors, contract.output_mode, tuple(errors))


def validate_markdown_prose_output(
    manifest: dict[str, Any],
    text: Any,
    *,
    output_path: str,
    registry: RoleRegistry | None = None,
) -> AgentResultValidation:
    """Ensure a prose task returns one complete candidate and no control-plane material."""

    try:
        contract = compile_agent_output_contract(manifest, registry=registry)
    except AgentResultProtocolError as exc:
        return AgentResultValidation(False, "unknown", (str(exc),))
    errors: list[str] = []
    if contract.output_mode != "markdown_prose":
        errors.append(f"{contract.output_mode} cannot be submitted as markdown_prose.")
    if normalize_path(output_path) != contract.primary_output_path:
        errors.append("output_path must exactly match the sole allowed prose output path.")
    if not isinstance(text, str):
        errors.append("markdown_prose output must be UTF-8 text.")
        return AgentResultValidation(False, contract.output_mode, tuple(errors))
    stripped = text.strip()
    if len(stripped) < 100 or len([item for item in stripped.splitlines() if item.strip()]) < 2:
        errors.append("markdown_prose must contain a complete multi-paragraph candidate, not a fragment.")
    if stripped.startswith(("{", "[")):
        try:
            json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            errors.append("markdown_prose must not be a JSON document.")
    if re.search(r"```\s*json\b", stripped, flags=re.IGNORECASE):
        errors.append("markdown_prose must not contain a JSON code block.")
    if ANALYSIS_HEADING_PATTERN.search(stripped) or "<analysis>" in stripped.lower():
        errors.append("markdown_prose must not contain analysis, reasoning, self-check, or author-note sections.")
    return AgentResultValidation(not errors, contract.output_mode, tuple(errors))


def validate_document_index_bundle(
    manifest: dict[str, Any],
    *,
    document_text: Any,
    document_path: str,
    index_payload: Any,
    index_path: str,
    registry: RoleRegistry | None = None,
) -> AgentResultValidation:
    """Validate a long Markdown design and its compact, machine-readable apply index."""

    try:
        contract = compile_agent_output_contract(manifest, registry=registry)
    except AgentResultProtocolError as exc:
        return AgentResultValidation(False, "unknown", (str(exc),))
    errors: list[str] = []
    if contract.output_mode != "document_index_bundle":
        errors.append(f"{contract.output_mode} cannot be submitted as document_index_bundle.")
    if normalize_path(document_path) != contract.primary_output_path:
        errors.append("document_path must exactly match the declared Markdown output path.")
    if normalize_path(index_path) != contract.companion_output_path:
        errors.append("index_path must exactly match the declared JSON index output path.")
    if not isinstance(document_text, str) or len(document_text.strip()) < 200:
        errors.append("design document must be substantive Markdown text.")
        headings: set[str] = set()
    else:
        headings = {
            line.strip()
            for line in document_text.splitlines()
            if re.match(r"^#{1,6}\s+\S", line.strip())
        }
        if not headings:
            errors.append("design document must contain at least one Markdown heading.")

    envelope = validate_agent_result_envelope(manifest, index_payload, registry=registry)
    errors.extend(envelope.errors)
    if isinstance(index_payload, dict) and isinstance(index_payload.get("deltas"), list):
        evidence_ids = {
            item.get("evidence_id")
            for item in index_payload.get("evidence") or []
            if isinstance(item, dict)
        }
        seen_anchors: set[str] = set()
        allowed_targets = set(manifest.get("canonical_targets") or [])
        for index, delta in enumerate(index_payload["deltas"]):
            if not isinstance(delta, dict):
                continue
            prefix = f"deltas[{index}]"
            if delta.get("action") != "index_section" or delta.get("field") != "apply_index":
                errors.append(f"{prefix} must use action=index_section and field=apply_index.")
            if delta.get("old_state") is not None or delta.get("coverage") != "changed":
                errors.append(f"{prefix} must describe a new changed index entry with old_state=null.")
            state = delta.get("new_state")
            if not isinstance(state, dict) or set(state) != DOCUMENT_INDEX_STATE_FIELDS:
                errors.append(
                    f"{prefix}.new_state fields must be exactly: "
                    + ", ".join(sorted(DOCUMENT_INDEX_STATE_FIELDS))
                    + "."
                )
                continue
            anchor = state.get("document_anchor")
            if not isinstance(anchor, str) or anchor not in headings:
                errors.append(f"{prefix}.new_state.document_anchor must match an exact Markdown heading.")
            elif anchor in seen_anchors:
                errors.append(f"{prefix}.new_state.document_anchor must be unique.")
            else:
                seen_anchors.add(anchor)
            validate_stable_id_list(state.get("stable_ids"), f"{prefix}.new_state.stable_ids", errors)
            if not isinstance(state.get("scope"), dict) or not state["scope"]:
                errors.append(f"{prefix}.new_state.scope must be a non-empty compact scope object.")
            source_refs = validate_string_list(
                state.get("source_refs"), f"{prefix}.new_state.source_refs", errors
            )
            if not set(source_refs) <= evidence_ids:
                errors.append(f"{prefix}.new_state.source_refs must reference declared evidence IDs.")
            targets = validate_string_list(
                state.get("canonical_targets"), f"{prefix}.new_state.canonical_targets", errors
            )
            if not targets or not set(targets) <= allowed_targets:
                errors.append(f"{prefix}.new_state.canonical_targets must stay within manifest targets.")
    return AgentResultValidation(not errors, contract.output_mode, tuple(errors))


def authoritative_delta_records(payload: Any) -> tuple[dict[str, Any], ...]:
    """Return only explicit delta objects; never infer state from prose or notes."""

    if not isinstance(payload, dict) or payload.get("schema") != AGENT_RESULT_ENVELOPE_SCHEMA:
        return ()
    records = payload.get("deltas")
    if not isinstance(records, list):
        return ()
    return tuple(deepcopy(item) for item in records if isinstance(item, dict))


def validate_evidence(value: Any, errors: list[str]) -> set[str]:
    if not isinstance(value, list):
        errors.append("evidence must be a list.")
        return set()
    if len(value) > 100:
        errors.append("evidence must contain at most 100 compact records.")
    identifiers: set[str] = set()
    for index, item in enumerate(value):
        prefix = f"evidence[{index}]"
        if not isinstance(item, dict) or set(item) != EVIDENCE_FIELDS:
            errors.append(f"{prefix} fields must be exactly: {', '.join(sorted(EVIDENCE_FIELDS))}.")
            continue
        evidence_id = validate_id(item.get("evidence_id"), f"{prefix}.evidence_id", errors)
        if evidence_id in identifiers:
            errors.append(f"{prefix}.evidence_id must be unique.")
        identifiers.add(evidence_id)
        validate_id(item.get("source_ref"), f"{prefix}.source_ref", errors)
        start, end = item.get("start"), item.get("end")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            errors.append(f"{prefix}.start must be a non-negative integer.")
        if not isinstance(end, int) or isinstance(end, bool) or not isinstance(start, int) or end <= start:
            errors.append(f"{prefix}.end must be greater than start.")
        excerpt = item.get("excerpt")
        if not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 500:
            errors.append(f"{prefix}.excerpt must be non-empty text no longer than 500 characters.")
    return identifiers


def validate_findings(value: Any, evidence_ids: set[str], verdict: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("findings must be a list.")
        return
    identifiers: set[str] = set()
    blocking = 0
    for index, item in enumerate(value):
        prefix = f"findings[{index}]"
        if not isinstance(item, dict) or set(item) != FINDING_FIELDS:
            errors.append(f"{prefix} fields must be exactly: {', '.join(sorted(FINDING_FIELDS))}.")
            continue
        finding_id = validate_id(item.get("finding_id"), f"{prefix}.finding_id", errors)
        if finding_id in identifiers:
            errors.append(f"{prefix}.finding_id must be unique.")
        identifiers.add(finding_id)
        validate_id(item.get("code"), f"{prefix}.code", errors)
        severity = item.get("severity")
        if severity not in REVIEW_SEVERITIES:
            errors.append(f"{prefix}.severity must be one of P0, P1, P2, P3.")
        elif severity in {"P0", "P1"}:
            blocking += 1
        for field in ("summary", "recommendation"):
            text = item.get(field)
            if not isinstance(text, str) or not text.strip() or len(text) > 800:
                errors.append(f"{prefix}.{field} must be compact non-empty text.")
        refs = validate_string_list(item.get("evidence_refs"), f"{prefix}.evidence_refs", errors)
        if not set(refs) <= evidence_ids:
            errors.append(f"{prefix}.evidence_refs must reference declared evidence IDs.")
        if severity in {"P0", "P1"} and not refs:
            errors.append(f"{prefix} P0/P1 findings require exact evidence references.")
    if verdict == "pass" and blocking:
        errors.append("verdict=pass cannot contain P0/P1 findings.")
    if verdict == "repair" and not value:
        errors.append("verdict=repair requires at least one finding.")


def validate_deltas(value: Any, evidence_ids: set[str], verdict: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("deltas must be a list.")
        return
    identifiers: set[str] = set()
    for index, item in enumerate(value):
        prefix = f"deltas[{index}]"
        if not isinstance(item, dict) or set(item) != DELTA_FIELDS:
            errors.append(f"{prefix} fields must be exactly: {', '.join(sorted(DELTA_FIELDS))}.")
            continue
        delta_id = validate_id(item.get("delta_id"), f"{prefix}.delta_id", errors)
        if delta_id in identifiers:
            errors.append(f"{prefix}.delta_id must be unique.")
        identifiers.add(delta_id)
        validate_id(item.get("entity_id"), f"{prefix}.entity_id", errors)
        validate_id(item.get("field"), f"{prefix}.field", errors)
        action = item.get("action")
        coverage = item.get("coverage")
        if action not in DELTA_ACTIONS:
            errors.append(f"{prefix}.action is not supported.")
        if coverage not in DELTA_COVERAGE:
            errors.append(f"{prefix}.coverage must be changed or unchanged.")
        if coverage == "unchanged":
            if action != "observe" or item.get("old_state") != item.get("new_state"):
                errors.append(
                    f"{prefix} unchanged coverage requires action=observe and identical old/new state."
                )
        elif coverage == "changed" and action == "observe":
            errors.append(f"{prefix} changed coverage cannot use action=observe.")
        refs = validate_string_list(item.get("evidence_refs"), f"{prefix}.evidence_refs", errors)
        if not set(refs) <= evidence_ids:
            errors.append(f"{prefix}.evidence_refs must reference declared evidence IDs.")
        if coverage == "changed" and action != "index_section" and not refs:
            errors.append(f"{prefix} changed canonical deltas require evidence references.")
    if verdict == "repair":
        errors.append("strict delta and document index tasks use pass or need_human, not repair.")
    if verdict == "pass" and not value:
        errors.append("verdict=pass requires explicit changed or unchanged coverage deltas.")


def validate_notes(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append("notes must be a list of non-authoritative strings.")
        return
    if len(value) > 8:
        errors.append("notes must contain at most 8 non-authoritative items.")
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip() or len(item) > 500:
            errors.append(f"notes[{index}] must be compact non-empty text.")


def find_forbidden_cli_fields(value: Any, location: str) -> list[str]:
    errors: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in AGENT_OWNED_CLI_FIELDS:
                errors.append(
                    f"{location}.{key} is CLI-known metadata and must not be Agent-authored."
                )
            errors.extend(find_forbidden_cli_fields(item, f"{location}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(find_forbidden_cli_fields(item, f"{location}[{index}]"))
    return errors


def normalize_output_paths(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise AgentResultProtocolError("manifest.allowed_output_paths must be a non-empty list.")
    result: list[str] = []
    for index, item in enumerate(value):
        path = normalize_path(item)
        if not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise AgentResultProtocolError(
                f"allowed_output_paths[{index}] must be a project-relative non-escaping path."
            )
        if is_canonical_output(path):
            raise AgentResultProtocolError(
                f"allowed_output_paths[{index}] targets canonical state and is forbidden: {path}"
            )
        if path in result:
            raise AgentResultProtocolError(f"allowed output path is duplicated: {path}")
        result.append(path)
    return result


def normalize_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def require_json_path(path: str, mode: str) -> None:
    if not path.lower().endswith(".json"):
        raise AgentResultProtocolError(f"{mode} output must use a .json path.")


def required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AgentResultProtocolError(f"{field} must be non-empty.")
    return text


def validate_id(value: Any, field: str, errors: list[str]) -> str:
    text = str(value or "").strip()
    if not ID_PATTERN.fullmatch(text):
        errors.append(f"{field} must be a stable identifier.")
    return text


def validate_string_list(value: Any, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list):
        errors.append(f"{field} must be a list of strings.")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{field}[{index}] must be a non-empty string.")
            continue
        result.append(item.strip())
    if len(result) != len(set(result)):
        errors.append(f"{field} must not contain duplicates.")
    return result


def validate_stable_id_list(value: Any, field: str, errors: list[str]) -> None:
    identifiers = validate_string_list(value, field, errors)
    if not identifiers:
        errors.append(f"{field} must contain at least one stable ID.")
    for index, item in enumerate(identifiers):
        if not ID_PATTERN.fullmatch(item):
            errors.append(f"{field}[{index}] must be a stable identifier.")
