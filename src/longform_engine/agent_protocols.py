"""Minimal Agent-facing protocols and deterministic validation helpers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable
import re

import yaml


PROSE_MARKDOWN_SCHEMA = "prose_markdown_v1"
DESIGN_DOCUMENT_SCHEMA = "design_document_v1"
EVIDENCE_REVIEW_SCHEMA = "evidence_review_v2"
CANONICAL_DELTA_SCHEMA = "canonical_delta_v1"
VALIDATION_REPORT_SCHEMA = "validation_report_v1"
HARD_BOUNDARIES = (
    "no final",
    "no rag",
    "no graph direct",
    "no sqlite direct",
    "no bible direct",
    "no outline direct",
    "no research canon direct",
)
BOUNDARY_PROFILE_ID = "canonical_write_boundary"
BOUNDARY_PROFILE_VERSION = 1
BOUNDARY_PROFILE_HASH = sha256("\n".join(HARD_BOUNDARIES).encode("utf-8")).hexdigest()

AGENT_OUTPUT_PROTOCOLS = frozenset(
    {
        PROSE_MARKDOWN_SCHEMA,
        DESIGN_DOCUMENT_SCHEMA,
        EVIDENCE_REVIEW_SCHEMA,
        CANONICAL_DELTA_SCHEMA,
    }
)

PROSE_TASK_TYPES = frozenset({"chapter_write", "repair", "humanize", "content_expand"})
DESIGN_TASK_TYPES = frozenset(
    {
        "book_ideation",
        "book_design",
        "character_expression_design",
        "outline_design",
        "arc_simulation",
        "outline_extension",
        "chapter_direction",
        "outline_revision",
        "repair_plan_synthesis",
        "style_analysis",
        "adaptation_analysis",
        "fanfiction_design",
        "human_review_consult",
    }
)
EVIDENCE_REVIEW_TASK_TYPES = frozenset(
    {
        "humanize_semantic_review",
        "reader_payoff_review",
        "editorial_review",
        "pacing_review",
        "semantic_review",
        "character_expression_review",
    }
)
CANONICAL_DELTA_TASK_TYPES = frozenset(
    {"chapter_semantic", "research_synthesis", "fanfiction_canon", "design_semantic_compile"}
)

DESIGN_REQUIRED_HEADINGS: dict[str, tuple[str, ...]] = {
    "book_ideation": ("创作问题", "可选方案", "方案代价", "人工决定"),
    "book_design": (
        "读者承诺",
        "核心卖点",
        "主角目标阶梯",
        "长期冲突",
        "世界与能力边界",
        "人物与关系",
        "结局边界",
    ),
    "character_expression_design": (
        "人物身份与欲望",
        "感知与注意",
        "决策模式",
        "语言与潜台词",
        "身体与情绪",
        "关系压力",
        "漂移禁区",
    ),
    "outline_design": (
        "全书故事弧",
        "卷级目标与字数预算",
        "滚动规划窗口",
        "章节职责",
        "人物弧",
        "伏笔窗口",
        "结局闭环",
    ),
    "arc_simulation": (
        "模拟范围与依据",
        "角色私人目标与拒绝点",
        "知识边界与场外行动",
        "资源与关系变化",
        "碰撞点与因果义务",
        "人工批准",
    ),
    "outline_extension": (
        "承接状态",
        "本轮故事弧",
        "章节职责",
        "人物与关系变化",
        "伏笔窗口",
        "字数与规划窗口",
    ),
    "chapter_direction": (
        "本章目标",
        "方向选项",
        "场景链",
        "人物选择与代价",
        "主线与伏笔",
        "人工选择",
    ),
    "outline_revision": ("修改目标", "影响分析", "保留项", "替换内容", "伏笔与人物弧影响"),
    "repair_plan_synthesis": (
        "候选 hash 与修复轮次",
        "完整 blocking finding 清单",
        "共同根因分组",
        "修复依赖与执行顺序",
        "每组最小修改范围",
        "必须保留内容",
        "允许改变内容",
        "冲突与 need-human 判断",
        "回归检查清单",
        "完成判据",
    ),
    "style_analysis": ("样本边界", "叙述视角", "句段与节奏", "对白与人物声音", "可迁移技法", "禁用模式"),
    "adaptation_analysis": ("来源与证据边界", "结构技法", "适用条件", "不可复制内容", "原创转化方案"),
    "fanfiction_design": (
        "Canon截止点",
        "分歧点与蝴蝶效应",
        "人物声音与OOC边界",
        "原创主线与贡献",
        "关系发展",
        "世界规则变化",
        "结局边界",
    ),
    "human_review_consult": (
        "问题复述",
        "证据判断",
        "可选修法",
        "风险与保护项",
        "建议动作",
    ),
}

DELTA_TYPES = {
    "chapter_semantic": "chapter_semantic",
    "research_synthesis": "research_canon",
    "fanfiction_canon": "fanfiction_canon",
    "design_semantic_compile": "design_document",
}
REVIEW_COVERAGE_STATES = frozenset({"checked", "insufficient", "not_applicable"})
REVIEW_VERDICTS = frozenset({"pass", "repair", "need_human", "insufficient_evidence"})
REVIEW_CERTAINTIES = frozenset({"confirmed", "probable", "insufficient_evidence"})
REVIEW_SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
DELTA_COVERAGE_STATES = frozenset({"changed", "unchanged", "insufficient"})
COMPACT_EVIDENCE_PATTERN = re.compile(r"^(?P<source>.+)@(?P<start>\d+):(?P<end>\d+)$")
FINDING_FIELDS = frozenset(
    {
        "code",
        "severity",
        "certainty",
        "diagnosis",
        "evidence_ids",
        "reader_impact",
        "repair_target",
        "preserve",
    }
)


class AgentProtocolError(ValueError):
    """Raised when an Agent-facing document is ambiguous or unsafe."""


class UniqueKeyLoader(yaml.SafeLoader):
    """YAML loader used only for trusted Prompt resource front matter."""


def _construct_unique_mapping(
    loader: UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise AgentProtocolError(f"duplicate YAML key `{key}`")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class DesignDocument:
    document_type: str
    headings: tuple[str, ...]
    sections: dict[str, str]
    markdown: str


def output_protocol_for_task(task_type: str) -> str:
    normalized = str(task_type or "").strip().lower().replace("-", "_")
    if normalized in PROSE_TASK_TYPES:
        return PROSE_MARKDOWN_SCHEMA
    if normalized in DESIGN_TASK_TYPES:
        return DESIGN_DOCUMENT_SCHEMA
    if normalized in EVIDENCE_REVIEW_TASK_TYPES:
        return EVIDENCE_REVIEW_SCHEMA
    if normalized in CANONICAL_DELTA_TASK_TYPES:
        return CANONICAL_DELTA_SCHEMA
    raise AgentProtocolError(f"task_type `{task_type}` has no Agent output protocol")


def parse_design_document(text: str, *, expected_type: str) -> DesignDocument:
    """Parse authoritative Markdown without accepting a hidden structured sidecar."""

    normalized = str(text or "").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise AgentProtocolError("design document Markdown must not be empty")
    if normalized.startswith("---\n"):
        raise AgentProtocolError("design_document_v1 is pure Markdown; YAML front matter is forbidden")
    if re.search(r"```\s*(?:json|ya?ml)\b", normalized, flags=re.IGNORECASE):
        raise AgentProtocolError("design_document_v1 must not contain JSON/YAML sidecars")
    required = DESIGN_REQUIRED_HEADINGS.get(expected_type)
    if required is None:
        raise AgentProtocolError(f"task_type `{expected_type}` is not a design document task")
    parsed = _markdown_sections(normalized)
    missing = [heading for heading in required if heading not in parsed]
    if missing:
        raise AgentProtocolError("design document is missing required headings: " + ", ".join(missing))
    empty = [heading for heading in required if not parsed[heading].strip()]
    if empty:
        raise AgentProtocolError("design document has empty required sections: " + ", ".join(empty))
    document = DesignDocument(
        document_type=expected_type,
        headings=tuple(parsed),
        sections=parsed,
        markdown=normalized,
    )
    if expected_type == "chapter_direction":
        chapter_direction_option_ids(document)
    return document


def chapter_direction_option_ids(document: DesignDocument) -> tuple[str, ...]:
    """Return the two or three stable option IDs declared by a direction document."""

    if document.document_type != "chapter_direction":
        raise AgentProtocolError("stable direction options apply only to chapter_direction documents")
    section = document.sections.get("方向选项", "")
    matches = re.findall(
        r"(?m)^#{3,6}\s+option:([a-z][a-z0-9_-]{2,63})\s+(?:[-—:：]\s*)?\S.*$",
        section,
    )
    if not 2 <= len(matches) <= 3:
        raise AgentProtocolError(
            "chapter_direction 方向选项 must declare two or three `### option:<stable_id> — 标题` headings"
        )
    if len(set(matches)) != len(matches):
        raise AgentProtocolError("chapter_direction option IDs must be unique")
    return tuple(matches)


def _markdown_sections(markdown: str) -> dict[str, str]:
    lines = markdown.splitlines()
    headings: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(#{2,6})\s+(.+?)\s*", line)
        if not match:
            continue
        heading = match.group(2).strip()
        if heading in seen:
            raise AgentProtocolError(f"Markdown heading `{heading}` must be unique")
        seen.add(heading)
        headings.append((index, len(match.group(1)), heading))
    if not headings:
        raise AgentProtocolError("design document must contain Markdown section headings")
    sections: dict[str, str] = {}
    for position, (start, level, heading) in enumerate(headings):
        end = len(lines)
        for next_start, next_level, _next_heading in headings[position + 1 :]:
            if next_level <= level:
                end = next_start
                break
        sections[heading] = "\n".join(lines[start + 1 : end]).strip()
    return sections


def validate_evidence_review(
    payload: Any,
    *,
    required_dimensions: Iterable[str] = (),
    allowed_finding_codes: Iterable[str] = (),
    optional_dimensions: Iterable[str] = (),
    canonical_ref_dimensions: Iterable[str] = (),
) -> list[str]:
    errors: list[str] = []
    expected = {"schema", "verdict", "coverage", "findings"}
    if not isinstance(payload, dict) or set(payload) != expected:
        return ["evidence review must contain exactly schema, verdict, coverage, findings"]
    if payload.get("schema") != EVIDENCE_REVIEW_SCHEMA:
        errors.append(f"schema must be {EVIDENCE_REVIEW_SCHEMA}")
    verdict = payload.get("verdict")
    if verdict not in REVIEW_VERDICTS:
        errors.append("verdict must be pass, repair, need_human, or insufficient_evidence")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or not coverage:
        errors.append("coverage must map review dimensions to evidence-bound coverage records")
        coverage = {}
    else:
        optional = {str(item).strip() for item in optional_dimensions if str(item).strip()}
        canonical_required = {
            str(item).strip() for item in canonical_ref_dimensions if str(item).strip()
        }
        for dimension, record in coverage.items():
            prefix = f"coverage.{dimension}"
            if not isinstance(dimension, str) or not dimension.strip():
                errors.append("coverage dimensions must be non-empty text")
                continue
            if not isinstance(record, dict) or set(record) != {
                "status",
                "evidence_ids",
                "canonical_refs",
            }:
                errors.append(
                    f"{prefix} must contain exactly status, evidence_ids, canonical_refs"
                )
                continue
            status = record.get("status")
            evidence_ids = record.get("evidence_ids")
            canonical_refs = record.get("canonical_refs")
            if status not in REVIEW_COVERAGE_STATES:
                errors.append(f"{prefix}.status must be checked, insufficient, or not_applicable")
            if not isinstance(evidence_ids, list) or any(
                not isinstance(item, str) or not item.strip() for item in evidence_ids
            ):
                errors.append(f"{prefix}.evidence_ids must be a list of non-empty evidence IDs")
                evidence_ids = []
            if not isinstance(canonical_refs, list) or any(
                not isinstance(item, str) or not item.strip() for item in canonical_refs
            ):
                errors.append(f"{prefix}.canonical_refs must be a list of non-empty references")
                canonical_refs = []
            if status == "checked" and not 1 <= len(evidence_ids) <= 2:
                errors.append(f"{prefix} checked coverage requires one or two evidence IDs")
            if status != "checked" and evidence_ids:
                errors.append(f"{prefix} may cite evidence only when status=checked")
            if status == "not_applicable" and dimension not in optional:
                errors.append(f"{prefix} is not declared optional by the active role")
            if status == "checked" and dimension in canonical_required and not canonical_refs:
                errors.append(f"{prefix} requires at least one canonical ref")
    required = {str(item).strip() for item in required_dimensions if str(item).strip()}
    missing_dimensions = sorted(required - set(coverage))
    if missing_dimensions:
        errors.append("coverage is missing required dimensions: " + ", ".join(missing_dimensions))
    insufficient = sorted(
        dimension
        for dimension in required
        if isinstance(coverage.get(dimension), dict)
        and coverage[dimension].get("status") == "insufficient"
    )
    if insufficient and verdict == "pass":
        errors.append("verdict=pass is forbidden when required coverage is insufficient")
    allowed_codes = {str(item).strip() for item in allowed_finding_codes if str(item).strip()}
    findings = payload.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        findings = []
    blocking = 0
    for index, finding in enumerate(findings):
        prefix = f"findings[{index}]"
        if not isinstance(finding, dict) or set(finding) != FINDING_FIELDS:
            errors.append(f"{prefix} fields must be exactly {', '.join(sorted(FINDING_FIELDS))}")
            continue
        for field in ("code", "diagnosis", "reader_impact", "repair_target"):
            if not isinstance(finding.get(field), str) or not str(finding.get(field)).strip():
                errors.append(f"{prefix}.{field} must be non-empty text")
        if allowed_codes and finding.get("code") not in allowed_codes:
            errors.append(f"{prefix}.code is not declared by the active review role")
        if finding.get("severity") not in REVIEW_SEVERITIES:
            errors.append(f"{prefix}.severity must be P0, P1, P2, or P3")
        if finding.get("certainty") not in REVIEW_CERTAINTIES:
            errors.append(f"{prefix}.certainty must be confirmed, probable, or insufficient_evidence")
        evidence_ids = finding.get("evidence_ids")
        if not isinstance(evidence_ids, list) or any(
            not isinstance(item, str) or not item.strip() for item in evidence_ids
        ):
            errors.append(f"{prefix}.evidence_ids must be a list of non-empty evidence IDs")
            evidence_ids = []
        preserve = finding.get("preserve")
        if not isinstance(preserve, list) or any(not isinstance(item, str) for item in preserve):
            errors.append(f"{prefix}.preserve must be a list of strings")
        if finding.get("severity") in {"P0", "P1"}:
            blocking += 1
            if finding.get("certainty") != "confirmed" or not evidence_ids:
                errors.append(f"{prefix} P0/P1 requires confirmed certainty and evidence IDs")
    if verdict == "pass" and blocking:
        errors.append("verdict=pass cannot contain P0/P1 findings")
    if verdict == "repair" and not findings:
        errors.append("verdict=repair requires at least one finding")
    if verdict == "insufficient_evidence" and not insufficient and all(
        item.get("certainty") != "insufficient_evidence" for item in findings if isinstance(item, dict)
    ):
        errors.append("insufficient_evidence verdict requires insufficient coverage or finding certainty")
    return errors


def validate_review_evidence_for_source(
    payload: Any,
    *,
    source_path: str,
    source_text: str,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    return validate_review_evidence_for_sources(payload, sources={source_path: source_text})


def validate_review_evidence_for_sources(
    payload: Any,
    *,
    sources: dict[str, str],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    alias_to_path: dict[str, str] = {}
    for path in sources:
        normalized = path.replace("\\", "/")
        name = normalized.rsplit("/", 1)[-1]
        for alias in (normalized, name, name.rsplit(".", 1)[0]):
            if alias in alias_to_path and alias_to_path[alias] != normalized:
                alias_to_path[alias] = ""
            else:
                alias_to_path[alias] = normalized
    records: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    evidence_groups: list[tuple[str, Iterable[Any]]] = []
    coverage = payload.get("coverage") if isinstance(payload, dict) else {}
    for dimension, record in (coverage.items() if isinstance(coverage, dict) else ()):
        if isinstance(record, dict):
            evidence_groups.append((f"coverage.{dimension}", record.get("evidence_ids") or []))
    findings = payload.get("findings") if isinstance(payload, dict) else []
    for finding_index, finding in enumerate(findings if isinstance(findings, list) else []):
        if isinstance(finding, dict):
            evidence_groups.append((f"findings[{finding_index}]", finding.get("evidence_ids") or []))
    for label, evidence_ids in evidence_groups:
        for raw in evidence_ids:
            evidence_id = str(raw or "").strip().replace("\\", "/")
            if evidence_id in records:
                continue
            match = COMPACT_EVIDENCE_PATTERN.fullmatch(evidence_id)
            if not match:
                errors.append(
                    f"{label} evidence `{evidence_id}` must use source_ref@start:end."
                )
                continue
            source_path = alias_to_path.get(match.group("source"))
            if source_path is None:
                errors.append(f"{label} evidence `{evidence_id}` is undeclared.")
                continue
            if not source_path:
                errors.append(f"{label} evidence `{evidence_id}` is ambiguous.")
                continue
            source_text = sources[source_path]
            start = int(match.group("start"))
            end = int(match.group("end"))
            if start < 0 or end <= start or end > len(source_text):
                errors.append(f"{label} evidence `{evidence_id}` is out of bounds.")
                continue
            records[evidence_id] = {
                "source_path": source_path,
                "start": start,
                "end": end,
                "excerpt": source_text[start:end],
            }
    return records, errors


def canonical_delta_domain_payload(
    payload: Any,
    *,
    task_type: str,
    domain_schema: str,
    cli_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    errors = validate_canonical_delta(payload, task_type=task_type)
    if errors:
        raise AgentProtocolError("; ".join(errors))
    if payload["uncertainties"]:
        raise AgentProtocolError("canonical delta uncertainties must be resolved before apply")
    result = deepcopy(payload["changes"])
    if "schema" in result:
        raise AgentProtocolError("canonical delta changes must not repeat a domain schema")
    result["schema"] = domain_schema
    for key, value in (cli_fields or {}).items():
        if key in result:
            raise AgentProtocolError(f"canonical delta changes must not author CLI field `{key}`")
        result[key] = value
    return result


def validate_canonical_delta(
    payload: Any,
    *,
    task_type: str,
    expected_delta_type: str = "",
) -> list[str]:
    errors: list[str] = []
    expected = {"schema", "delta_type", "coverage", "changes", "evidence", "uncertainties"}
    if not isinstance(payload, dict) or set(payload) != expected:
        return [
            "canonical delta must contain exactly schema, delta_type, coverage, changes, evidence, uncertainties"
        ]
    if payload.get("schema") != CANONICAL_DELTA_SCHEMA:
        errors.append(f"schema must be {CANONICAL_DELTA_SCHEMA}")
    required_delta_type = expected_delta_type or DELTA_TYPES.get(task_type)
    if not required_delta_type or payload.get("delta_type") != required_delta_type:
        errors.append(f"delta_type does not match task_type `{task_type}`")
    coverage = payload.get("coverage")
    if not isinstance(coverage, dict) or not coverage or any(
        not isinstance(section, str)
        or not section.strip()
        or status not in DELTA_COVERAGE_STATES
        for section, status in (coverage.items() if isinstance(coverage, dict) else ())
    ):
        errors.append("coverage must map section names to changed, unchanged, or insufficient")
    changes = payload.get("changes")
    if not isinstance(changes, dict):
        errors.append("changes must be an object")
        changes = {}
    evidence = payload.get("evidence")
    if not isinstance(evidence, dict):
        errors.append("evidence must map JSON Pointers to evidence ID lists")
        evidence = {}
    else:
        for pointer, ids in evidence.items():
            if not isinstance(pointer, str) or not pointer.startswith("/changes/"):
                errors.append("evidence keys must be JSON Pointers below /changes")
                continue
            if not isinstance(ids, list) or not ids or any(
                not isinstance(item, str) or not item.strip() for item in ids
            ):
                errors.append(f"evidence `{pointer}` must contain non-empty evidence IDs")
                continue
            if not _json_pointer_exists(payload, pointer):
                errors.append(f"evidence pointer `{pointer}` does not resolve to a change")
    if any(
        _contains_key(changes, field)
        for field in ("evidence_id", "evidence_ids", "evidence_refs")
    ):
        errors.append("changes must not repeat evidence IDs or refs; use the top-level evidence map")
    uncertainties = payload.get("uncertainties")
    if not isinstance(uncertainties, list) or any(
        not isinstance(item, str) or not item.strip() for item in uncertainties or []
    ):
        errors.append("uncertainties must be a list of non-empty strings")
    if any(status == "insufficient" for status in coverage.values() if isinstance(coverage, dict)) and not uncertainties:
        errors.append("insufficient coverage requires at least one uncertainty")
    return errors


def build_validation_report(
    *,
    ok: bool,
    stage: str,
    subject: str,
    errors: Iterable[str] = (),
    warnings: Iterable[str] = (),
    blockers: Iterable[str] = (),
    provenance: dict[str, Any] | None = None,
    next_command: str,
) -> dict[str, Any]:
    return {
        "schema": VALIDATION_REPORT_SCHEMA,
        "ok": bool(ok),
        "stage": str(stage),
        "subject": str(subject),
        "errors": [str(item) for item in errors],
        "warnings": [str(item) for item in warnings],
        "blockers": [str(item) for item in blockers],
        "provenance": dict(provenance or {}),
        "next_command": str(next_command),
    }


def _json_pointer_exists(payload: dict[str, Any], pointer: str) -> bool:
    current: Any = payload
    for token in pointer.lstrip("/").split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return False
    return True


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(item, key) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False
