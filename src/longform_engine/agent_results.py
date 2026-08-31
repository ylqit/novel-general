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
)
from longform_engine.semantic_protocols import (
    SEMANTIC_DOCUMENT_SCHEMA,
    build_semantic_document,
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
    elif contract.protocol in {
        EVIDENCE_REVIEW_SCHEMA,
        CANONICAL_DELTA_SCHEMA,
        SEMANTIC_DOCUMENT_SCHEMA,
    }:
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
    if contract.protocol == SEMANTIC_DOCUMENT_SCHEMA:
        scope = manifest.get("scope") if isinstance(manifest.get("scope"), dict) else {}
        document_types = {
            "fanfiction_canon": "项目原著基线Canon候选",
            "fanfiction_story_engine": "同人故事发动机",
            "fanfiction_design": "同人路线设计候选",
            "source_discovery_planning": "原著资料搜索规划",
            "source_candidate_triage": "原著来源候选筛选",
            "source_timeline_alignment": "原著媒体时间线对齐",
            "source_conflict_analysis": "原著版本冲突分析",
            "character_interpretation": "人物理解候选",
            "fanfiction_route_design": "同人路线设计候选",
            "story_architecture_design": "故事架构设计候选",
            "chapter_semantic_planning": "章节语义规划候选",
            "draft_semantic_review": "章节因果与人物选择审查",
            "prose_revision_review": "文风与表达修订审查",
            "reader_feedback_analysis": "读者反馈分析",
            "source_fact_extraction": "原著事实候选",
            "source_visual_observation": "视觉直接观察候选",
            "source_evidence_review": "原著证据复核",
            "source_version_conflict_review": "原著版本冲突分析",
            "source_coverage_gap_analysis": "原著资料覆盖分析",
        }
        extensions = {
            "task_type": contract.task_type,
            "item_id": str(scope.get("item_id") or ""),
            "normalization_sha256": str(scope.get("normalization_sha256") or ""),
            "review_type": {
                "source_evidence_review": "segment_evidence",
                "source_version_conflict_review": "version_conflict",
                "source_coverage_gap_analysis": "coverage_gap",
            }.get(contract.task_type, ""),
        }
        if contract.task_type == "fanfiction_story_engine":
            extensions = {"route_family": ""}
        if scope.get("bundle_sha256"):
            extensions["bundle_sha256"] = str(scope["bundle_sha256"])
        return build_semantic_document(
            document_id="sem_" + sha256(contract.task_id.encode("utf-8")).hexdigest()[:24],
            document_type=document_types[contract.task_type],
            title=document_types[contract.task_type],
            scope=dict(scope),
            continuity="原著基线",
            body="",
            extensions=extensions,
            input_hashes=[
                str(item.get("sha256") or "")
                for item in ((manifest.get("io") or {}).get("inputs") or [])
                if isinstance(item, dict) and str(item.get("sha256") or "")
            ],
        )
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
    elif contract.protocol == SEMANTIC_DOCUMENT_SCHEMA:
        shape = (
            "只写 semantic_document_v1 JSON；正文使用自然中文，只有可断言内容写入 claims，"
            "每条主张只引用本工单声明的 evidence_reference_v1。直接观察与解释必须分开，"
            "结论保持候选状态、不得复制连续原文或使用模型记忆补全。"
        )
    else:
        shape = (
            "只写 canonical_delta_v1 JSON；evidence 使用 /changes/... JSON Pointer 到证据 ID 的映射，"
            "changes 内不重复证据字段；不要编写 canonical 路径、hash 或数据库字段。"
        )
    return shape


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
    "build_agent_result_template",
    "compile_agent_output_contract",
    "parse_agent_output_files",
    "render_agent_output_instructions",
    "validate_design_document_output",
    "validate_markdown_prose_output",
]
