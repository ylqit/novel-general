"""Compile the shared creative contract for a continuity mode and narrative route."""

from __future__ import annotations

from typing import Any


CREATIVE_CONTRACT_VERSION = "fanfiction_creative_contract_v2"
ROUTE_FAMILIES = frozenset({"oc_si_progression", "canon_character_centered", "hybrid"})
COMMON_STORY_TYPES = (
    "原著人物自主性", "主角与原著关系", "读者识别承诺", "本作新增阅读价值",
    "可持续叙事动力", "叙事责任分配",
)
CONTINUITY_REQUIREMENTS = {
    "canon_compliant": ("原著事实保持边界", "新增视角或未展开空间"),
    "canon_divergent": ("初始分歧", "分歧因果后果"),
    "alternate_universe": ("架空初始条件", "人物辨识锚点", "世界规则改写边界"),
    "continuation": ("承接终止状态", "既有结局保护", "后续未决问题"),
    "prequel": ("补写前史", "通向既定状态", "知识提前出现边界"),
    "crossover": ("作品相遇前提", "逐来源保留与改变", "跨界拓扑"),
}
ROUTE_REQUIREMENTS = {
    "oc_si_progression": ("主角推进方式与限制", "原著人物拒绝权"),
    "canon_character_centered": ("原著人物选择与后果归属",),
    "hybrid": ("双方叙事责任分配",),
}


def compile_fanfiction_creative_requirements(
    continuity_mode: str, route_family: str
) -> dict[str, Any]:
    """Return the same applicability decisions to validators, prompts and evaluation."""
    if continuity_mode not in CONTINUITY_REQUIREMENTS:
        raise ValueError(f"unsupported fanfiction continuity mode: {continuity_mode}")
    if route_family not in ROUTE_FAMILIES:
        raise ValueError(f"unsupported fanfiction route family: {route_family}")
    continuity = CONTINUITY_REQUIREMENTS[continuity_mode]
    responsibility = ROUTE_REQUIREMENTS[route_family]
    return {
        "version": CREATIVE_CONTRACT_VERSION,
        "continuity_mode": continuity_mode,
        "route_family": route_family,
        "story_engine_claim_types": [*COMMON_STORY_TYPES, *continuity, *responsibility],
        "route_claim_types": ["故事切入点", "人物知识边界", "原著人物职责", *continuity, *responsibility],
        "context_required_claim_types": [*COMMON_STORY_TYPES, *continuity, *responsibility],
        "independent_review_focus": [
            "人物自主选择及拒绝权", "原著辨识度", "本作新增阅读价值", "持续叙事动力",
            "叙事责任与后果归属", "原著事件处置适用性及其依据", *continuity, *responsibility,
        ],
        "conditional_metrics": {
            "divergence_causality": continuity_mode == "canon_divergent",
            "cross_system_cost_and_counterplay": continuity_mode == "crossover",
        },
        "creative_guidance": [
            "新增阅读价值可以来自关系、视角、人物理解和未展开情节。",
            "持续动力可以来自人物、关系、调查、生活或未决问题，不强制升级与改命循环。",
            "分歧路线可以有多个具备明确因果关系的初始条件，不要求唯一初始变量。",
            "保留原著事件可以引用原有因果；不适用的事件处置须提供可独立审查的路线主张依据。",
        ],
    }
