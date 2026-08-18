"""Four small Agent output protocols; canonical ownership remains with the CLI."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.agent_protocols import (
    AGENT_OUTPUT_PROTOCOLS,
    CANONICAL_DELTA_SCHEMA,
    DESIGN_DOCUMENT_SCHEMA,
    EVIDENCE_REVIEW_SCHEMA,
    PROSE_MARKDOWN_SCHEMA,
    AgentProtocolError,
    output_protocol_for_task,
    parse_design_document,
    validate_canonical_delta,
    validate_evidence_review,
)
from longform_engine.agent_tasks import (
    is_canonical_output,
    manifest_commands,
    manifest_output,
    manifest_role,
    relative_path,
    resolve_under_root,
)
from longform_engine.roles import RoleRegistry, load_role_registry, reject_duplicate_json_keys


AGENT_OUTPUT_CONTRACT_SCHEMA = "agent_output_contract_v2"
ANALYSIS_HEADING_PATTERN = re.compile(
    r"(?im)^#{1,6}\s*(analysis|reasoning|self[- ]?check|json|分析|说明|修订说明|作者说明)\s*$"
)


class AgentResultProtocolError(ValueError):
    """Raised when an Agent output is ambiguous or crosses a CLI boundary."""


@dataclass(frozen=True)
class AgentOutputContract:
    schema: str
    task_id: str
    task_type: str
    role_id: str
    protocol: str
    output_path: str
    validate_command: str
    apply_command: str
    failure_command: str
    cli_prefilled_fields: tuple[str, ...] = (
        "task_id",
        "task_type",
        "role_id",
        "scope",
        "chapter_number",
        "source_path",
        "source_hash",
        "validated_at",
    )


@dataclass(frozen=True)
class AgentResultValidation:
    ok: bool
    output_mode: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ParsedAgentOutput:
    output_mode: str
    result_path: str
    result_sha256: str
    text: str
    payload: dict[str, Any] | None


def read_utf8_output(path: Path, *, label: str) -> tuple[bytes, str]:
    if not path.is_file():
        raise AgentResultProtocolError(f"{label} does not exist or is not a file: {path}")
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError as exc:
        raise AgentResultProtocolError(f"{label} must be valid UTF-8: {path}") from exc
    return raw, text


def parse_agent_output_files(
    root: Path,
    manifest: dict[str, Any],
    *,
    result_file: str | Path,
    registry: RoleRegistry | None = None,
) -> ParsedAgentOutput:
    contract = compile_agent_output_contract(manifest, registry=registry)
    project_root = root.resolve()
    path = resolve_under_root(project_root, result_file)
    relative = relative_path(project_root, path)
    if relative != contract.output_path:
        raise AgentResultProtocolError(
            f"result file must exactly match `{contract.output_path}`; got `{relative}`"
        )
    raw, text = read_utf8_output(path, label="Agent result")
    payload: dict[str, Any] | None = None
    if contract.protocol == DESIGN_DOCUMENT_SCHEMA:
        try:
            parse_design_document(text, expected_type=contract.task_type)
        except AgentProtocolError as exc:
            raise AgentResultProtocolError(str(exc)) from exc
    elif contract.protocol in {EVIDENCE_REVIEW_SCHEMA, CANONICAL_DELTA_SCHEMA}:
        try:
            loaded = json.loads(text, object_pairs_hook=reject_duplicate_json_keys)
        except (json.JSONDecodeError, ValueError) as exc:
            raise AgentResultProtocolError(f"Agent result must be duplicate-key-safe JSON: {exc}") from exc
        if not isinstance(loaded, dict):
            raise AgentResultProtocolError("structured Agent result must be a JSON object")
        payload = loaded
    return ParsedAgentOutput(
        output_mode=contract.protocol,
        result_path=relative,
        result_sha256=sha256(raw).hexdigest(),
        text=text,
        payload=payload,
    )


def compile_agent_output_contract(
    manifest: dict[str, Any],
    *,
    registry: RoleRegistry | None = None,
) -> AgentOutputContract:
    task_id = required_text(manifest.get("task_id"), "manifest.task_id")
    task_type = required_text(manifest.get("task_type"), "manifest.task_type")
    role_id = required_text(manifest_role(manifest).get("id"), "manifest.role.id")
    roles = registry or load_role_registry()
    try:
        role = roles.resolve(task_type, declared_role_id=role_id)
        expected_protocol = output_protocol_for_task(task_type)
    except (ValueError, AgentProtocolError) as exc:
        raise AgentResultProtocolError(str(exc)) from exc
    output = manifest_output(manifest)
    declared_protocol = required_text(output.get("protocol"), "manifest.io.output.protocol")
    if declared_protocol not in AGENT_OUTPUT_PROTOCOLS or declared_protocol != expected_protocol:
        raise AgentResultProtocolError(
            f"task_type `{task_type}` requires `{expected_protocol}`, got `{declared_protocol}`"
        )
    if role.output_mode != declared_protocol:
        raise AgentResultProtocolError(
            f"role `{role_id}` output_mode must be `{declared_protocol}`, got `{role.output_mode}`"
        )
    primary = required_text(output.get("path"), "manifest.io.output.path").replace("\\", "/")
    if Path(primary).is_absolute() or ".." in Path(primary).parts or is_canonical_output(primary):
        raise AgentResultProtocolError("manifest.io.output.path must be project-relative and non-canonical")
    if declared_protocol in {PROSE_MARKDOWN_SCHEMA, DESIGN_DOCUMENT_SCHEMA}:
        if not primary.lower().endswith(".md"):
            raise AgentResultProtocolError(f"{declared_protocol} output must use a .md path")
    elif not primary.lower().endswith(".json"):
        raise AgentResultProtocolError(f"{declared_protocol} output must use a .json path")
    commands = manifest_commands(manifest)
    return AgentOutputContract(
        schema=AGENT_OUTPUT_CONTRACT_SCHEMA,
        task_id=task_id,
        task_type=task_type,
        role_id=role_id,
        protocol=declared_protocol,
        output_path=primary,
        validate_command=required_text(commands.get("validate"), "manifest.commands.validate"),
        apply_command=required_text(commands.get("apply"), "manifest.commands.apply"),
        failure_command=required_text(
            commands.get("failure"), "manifest.commands.failure"
        ),
    )


def build_agent_result_template(
    manifest: dict[str, Any],
    *,
    registry: RoleRegistry | None = None,
) -> dict[str, Any]:
    contract = compile_agent_output_contract(manifest, registry=registry)
    if contract.protocol == EVIDENCE_REVIEW_SCHEMA:
        return {
            "schema": EVIDENCE_REVIEW_SCHEMA,
            "verdict": "",
            "coverage": {},
            "findings": [],
        }
    if contract.protocol == CANONICAL_DELTA_SCHEMA:
        from longform_engine.agent_protocols import DELTA_TYPES

        return {
            "schema": CANONICAL_DELTA_SCHEMA,
            "delta_type": DELTA_TYPES[contract.task_type],
            "coverage": {},
            "changes": {},
            "evidence": {},
            "uncertainties": [],
        }
    raise AgentResultProtocolError(
        f"{contract.protocol} is a document protocol; follow the rendered work order"
    )


def render_agent_output_instructions(contract: AgentOutputContract) -> str:
    if contract.protocol == PROSE_MARKDOWN_SCHEMA:
        shape = "只写完整 Markdown 正文，不附加 JSON、分析、修订说明或作者说明。"
    elif contract.protocol == DESIGN_DOCUMENT_SCHEMA:
        shape = (
            "只写纯 Markdown 设计文档，使用工作单列出的中文必需标题；"
            "禁止 YAML front matter、JSON sidecar 和 CLI 已知字段。"
        )
    elif contract.protocol == EVIDENCE_REVIEW_SCHEMA:
        shape = (
            "只写 evidence_review_v2 JSON；coverage 每个维度都写 status、1-2 个正文 evidence_ids "
            "和角色要求的 canonical_refs；finding 不写 dimension 或 notes；"
            "不要回填任务、章节、路径、hash、角色、命令或时间。"
        )
    else:
        shape = (
            "只写 canonical_delta_v1 JSON；evidence 使用 /changes/... JSON Pointer 到证据 ID 的映射，"
            "changes 内不重复证据字段；不要编写 canonical 路径、hash 或数据库字段。"
        )
    return shape


def validate_agent_result_envelope(
    manifest: dict[str, Any],
    payload: Any,
    *,
    registry: RoleRegistry | None = None,
) -> AgentResultValidation:
    try:
        contract = compile_agent_output_contract(manifest, registry=registry)
    except AgentResultProtocolError as exc:
        return AgentResultValidation(False, "unknown", (str(exc),))
    if contract.protocol == EVIDENCE_REVIEW_SCHEMA:
        role = (registry or load_role_registry()).resolve(
            contract.task_type,
            declared_role_id=contract.role_id,
        )
        errors = validate_evidence_review(
            payload,
            required_dimensions=role.review_dimensions,
            allowed_finding_codes=role.finding_codes,
            optional_dimensions=role.optional_review_dimensions,
            canonical_ref_dimensions=role.canonical_ref_dimensions,
        )
    elif contract.protocol == CANONICAL_DELTA_SCHEMA:
        errors = validate_canonical_delta(payload, task_type=contract.task_type)
    else:
        errors = [f"{contract.protocol} is not a JSON result protocol"]
    return AgentResultValidation(not errors, contract.protocol, tuple(errors))


def validate_markdown_prose_output(
    manifest: dict[str, Any],
    text: Any,
    *,
    output_path: str,
    registry: RoleRegistry | None = None,
) -> AgentResultValidation:
    try:
        contract = compile_agent_output_contract(manifest, registry=registry)
    except AgentResultProtocolError as exc:
        return AgentResultValidation(False, "unknown", (str(exc),))
    errors: list[str] = []
    if contract.protocol != PROSE_MARKDOWN_SCHEMA:
        errors.append(f"{contract.protocol} cannot be submitted as prose")
    if normalize_path(output_path) != contract.output_path:
        errors.append("output_path must exactly match the allowed prose path")
    if not isinstance(text, str):
        errors.append("prose output must be UTF-8 text")
    else:
        stripped = text.strip()
        if len(stripped) < 100 or len([line for line in stripped.splitlines() if line.strip()]) < 2:
            errors.append("prose output must be a complete multi-paragraph candidate")
        if re.search(r"```\s*json\b", stripped, flags=re.IGNORECASE):
            errors.append("prose output must not contain a JSON code block")
        if ANALYSIS_HEADING_PATTERN.search(stripped) or "<analysis>" in stripped.lower():
            errors.append("prose output must not contain analysis or author-note sections")
    return AgentResultValidation(not errors, contract.protocol, tuple(errors))


def validate_design_document_output(
    manifest: dict[str, Any],
    *,
    document_text: Any,
    document_path: str,
    registry: RoleRegistry | None = None,
) -> AgentResultValidation:
    """Validate the single-file design document protocol."""
    try:
        contract = compile_agent_output_contract(manifest, registry=registry)
    except AgentResultProtocolError as exc:
        return AgentResultValidation(False, "unknown", (str(exc),))
    errors: list[str] = []
    if contract.protocol != DESIGN_DOCUMENT_SCHEMA:
        errors.append(f"{contract.protocol} cannot be submitted as a design document")
    if normalize_path(document_path) != contract.output_path:
        errors.append("document_path must exactly match the allowed design document path")
    try:
        parse_design_document(str(document_text or ""), expected_type=contract.task_type)
    except AgentProtocolError as exc:
        errors.append(str(exc))
    return AgentResultValidation(not errors, contract.protocol, tuple(errors))


def authoritative_delta_records(payload: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(payload, dict) or payload.get("schema") != CANONICAL_DELTA_SCHEMA:
        return ()
    changes = payload.get("changes")
    return (changes,) if isinstance(changes, dict) else ()


def normalize_path(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/")


def required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AgentResultProtocolError(f"{field} must be non-empty")
    return text


__all__ = [
    "AGENT_OUTPUT_CONTRACT_SCHEMA",
    "AgentOutputContract",
    "AgentResultProtocolError",
    "AgentResultValidation",
    "ParsedAgentOutput",
    "authoritative_delta_records",
    "build_agent_result_template",
    "compile_agent_output_contract",
    "parse_agent_output_files",
    "render_agent_output_instructions",
    "validate_agent_result_envelope",
    "validate_design_document_output",
    "validate_markdown_prose_output",
]
