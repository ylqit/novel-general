"""Formal production work order for generating ``planning_bundle_v1``."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
import json

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


PLANNING_GENERATION_TASK_SCHEMA = "planning_generation_task_v1"

SEMANTIC_OBLIGATION_FIELDS = [
    "schema",
    "obligation_id",
    "domain",
    "subject_refs",
    "prior_state_refs",
    "preconditions",
    "intended_change",
    "reader_value",
    "evidence_requirement",
    "protected_invariants",
    "dependency_refs",
    "fanfiction_claim_refs",
]

PLOT_NODE_FIELDS = [
    "schema",
    "node_id",
    "chapter_number",
    "scene_id",
    "sequence",
    "actors",
    "location_ref",
    "dramatic_function",
    "preconditions",
    "dependency_refs",
    "obligation_refs",
    "action_or_exchange",
    "expected_changes",
    "reader_effect",
    "requirement",
    "condition",
    "node_kind",
    "protected_invariants",
    "allowed_deviation",
    "human_decision",
    "fanfiction_claim_refs",
]

FANFICTION_CHANNEL_FIELDS = [
    "schema",
    "active_volume_claim_refs",
    "semantic_obligation_claim_refs",
    "plot_node_claim_refs",
    "chapter_claim_refs",
    "all_claim_refs",
]


@dataclass(frozen=True)
class PlanningGenerationTaskResult:
    contract_file: str
    instruction_file: str
    output_file: str


def write_planning_generation_task(config: ConfigDocument) -> PlanningGenerationTaskResult:
    """Write the sole current planning-generation contract and human-readable work order."""

    root = resolve_project_root(config)
    mode = str(config.data.get("creation", {}).get("mode") or "original")
    directory = root / "50_workbench" / "planning"
    contract_path = directory / "planning_generation_task_v1.json"
    instruction_path = directory / "planning_generation_task_v1.md"
    output_path = directory / "planning_bundle_v1.json"
    source_files: list[dict[str, str]] = []
    for path in (
        config.path,
        root / "10_bible" / "fanfiction" / "source_canon.json",
        root / "10_bible" / "fanfiction" / "story_engine.json",
        root / "10_bible" / "fanfiction" / "fanfiction_bible.json",
    ):
        if path is None or not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        source_files.append(
            {
                "path": relative,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
        )
    mode_rules = (
        {
            "fanfiction_projection": "required",
            "fanfiction_claim_refs": "required_resolved",
            "claim_applicability": "all_declared_dimensions_must_intersect",
        }
        if mode == "fanfiction"
        else {
            "fanfiction_projection": "forbidden",
            "fanfiction_claim_refs": "required_empty",
            "claim_applicability": "not_applicable",
        }
    )
    contract: dict[str, Any] = {
        "schema": PLANNING_GENERATION_TASK_SCHEMA,
        "creation_mode": mode,
        "output_path": output_path.relative_to(root).as_posix(),
        "output_schema": "planning_bundle_v1",
        "semantic_obligation_fields": SEMANTIC_OBLIGATION_FIELDS,
        "plot_node_fields": PLOT_NODE_FIELDS,
        "fanfiction_chapter_claim_channel_fields": FANFICTION_CHANNEL_FIELDS,
        "mode_rules": mode_rules,
        "source_files": source_files,
    }
    atomic_write_text(
        contract_path,
        json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(instruction_path, _render_planning_generation_task(contract))
    return PlanningGenerationTaskResult(
        contract_file=contract_path.relative_to(root).as_posix(),
        instruction_file=instruction_path.relative_to(root).as_posix(),
        output_file=output_path.relative_to(root).as_posix(),
    )


def _render_planning_generation_task(contract: dict[str, Any]) -> str:
    lines = [
        "# planning_bundle_v1 生成工作单",
        "",
        "输出只能写入合同声明的 output_path，并严格使用 planning_bundle_v1。",
        "semantic_obligation_v1 与 plot_node_v1 必须包含 fanfiction_claim_refs 字段。",
        "chapter_contract_v5 必须包含 fanfiction_chapter_claim_channel_v1；",
        "all_claim_refs 必须等于四个来源字段的有序去重并集。",
    ]
    if contract["creation_mode"] == "fanfiction":
        lines.extend(
            [
                "活动卷必须提供 fanfiction_projection(body, claim_refs)。",
                "所有 claim_refs 必须解析到当前人工批准同人语义文档，并满足来源、人物、事件、卷、篇章与章节适用域的 AND 规则。",
                "义务、Plot Node 与章节专属引用分别进入对应来源字段，不得伪造 provenance。",
            ]
        )
    else:
        lines.extend(
            [
                "原作模式禁止 fanfiction_projection。",
                "仍须输出正式 fanfiction_claim_refs/channel 字段，但每个列表必须为空。",
            ]
        )
    lines.extend(
        [
            "",
            "生成后依次执行 structural-validate、独立 semantic review、人工 approval 与 node decisions；工作单本身无权写入 Canon。",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "PLANNING_GENERATION_TASK_SCHEMA",
    "PlanningGenerationTaskResult",
    "write_planning_generation_task",
]
