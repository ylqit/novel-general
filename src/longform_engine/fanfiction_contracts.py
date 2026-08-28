from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.semantic_protocols import validate_semantic_document


STORY_ENGINE_REQUIRED_SEMANTIC_TYPES = (
    "唯一初始变量",
    "独立长期目标",
    "可持续阻力",
    "原著人物自主性",
    "原作后续故事来源",
    "主角与原著关系",
    "读者识别承诺",
    "原创主线承诺",
)

STORY_ENGINE_ROUTE_FAMILIES = frozenset(
    {"oc_si_progression", "canon_character_centered", "hybrid"}
)

EVENT_DISPOSITIONS = frozenset(
    {"保留", "提前", "延迟", "结果改变", "换人承担", "取消", "转化", "待决定"}
)

EVENT_CAUSAL_REFERENCE_FIELDS = (
    "responsibility_owner_ids",
    "first_order_effect_claim_ids",
    "second_order_effect_claim_ids",
)


class FanfictionContractError(ValueError):
    """A canonical fanfiction document is not approved under the current contract."""


def _read_document(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def fanfiction_semantic_types(payload: dict[str, Any]) -> set[str]:
    return {
        str(claim.get("extensions", {}).get("semantic_type") or "")
        for claim in payload.get("claims") or []
        if isinstance(claim, dict) and isinstance(claim.get("extensions"), dict)
    }


def _validate_story_engine_semantics(
    payload: dict[str, Any],
    errors: list[str],
) -> None:
    extensions = payload.get("extensions") if isinstance(payload.get("extensions"), dict) else {}
    artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else {}
    scope = artifact.get("scope") if isinstance(artifact.get("scope"), dict) else {}
    if payload.get("document_type") != "同人故事发动机":
        errors.append("document_type must be 同人故事发动机")
    if scope.get("kind") != "project":
        errors.append("fanfiction story engine must use project scope")
    if extensions.get("task_type") != "fanfiction_story_engine":
        errors.append("extensions.task_type must be fanfiction_story_engine")
    route_family = extensions.get("route_family")
    if not isinstance(route_family, str) or route_family not in STORY_ENGINE_ROUTE_FAMILIES:
        errors.append(
            "extensions.route_family must be oc_si_progression, "
            "canon_character_centered, or hybrid"
        )
    if not str(payload.get("body") or "").strip():
        errors.append("fanfiction story engine body must describe the long-form reading promise")
    semantic_types = fanfiction_semantic_types(payload)
    for semantic_type in STORY_ENGINE_REQUIRED_SEMANTIC_TYPES:
        if semantic_type not in semantic_types:
            errors.append(f"fanfiction story engine requires a {semantic_type} semantic claim")


def validate_fanfiction_story_engine(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    errors: list[str],
    *,
    require_approved: bool = False,
) -> None:
    errors.extend(validate_semantic_document(payload, require_approved=require_approved))
    if errors:
        return
    _validate_story_engine_semantics(payload, errors)
    extensions = payload["extensions"]
    configured = config.data.get("fanfiction")
    configured = configured if isinstance(configured, dict) else {}
    if extensions.get("continuity_mode") != configured.get("continuity_mode"):
        errors.append("extensions.continuity_mode must match project.yaml")
    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon = _read_document(canon_path)
    if validate_semantic_document(canon, require_approved=True):
        errors.append("fanfiction story engine requires approved project source Canon")
        return
    if extensions.get("source_canon_sha256") != sha256(canon_path.read_bytes()).hexdigest():
        errors.append("extensions.source_canon_sha256 is stale")


def load_current_fanfiction_story_engine(
    config: ConfigDocument,
    root: Path,
) -> dict[str, Any]:
    payload = _read_document(root / "10_bible" / "fanfiction" / "story_engine.json")
    errors: list[str] = []
    validate_fanfiction_story_engine(
        config,
        root,
        payload,
        errors,
        require_approved=True,
    )
    if errors:
        raise FanfictionContractError(
            "current fanfiction story engine is invalid: " + "; ".join(errors)
        )
    return payload


def validate_event_disposition_claims(
    root: Path,
    payload: dict[str, Any],
    errors: list[str],
) -> None:
    claim_ids = {
        str(item.get("claim_id") or "")
        for item in payload.get("claims") or []
        if isinstance(item, dict)
    }
    canon = _read_document(root / "10_bible" / "fanfiction" / "source_canon.json")
    if not validate_semantic_document(canon, require_approved=True):
        claim_ids.update(
            str(item.get("claim_id") or "")
            for item in canon.get("claims") or []
            if isinstance(item, dict) and item.get("claim_id")
        )
    engine = _read_document(root / "10_bible" / "fanfiction" / "story_engine.json")
    engine_errors = validate_semantic_document(engine, require_approved=True)
    if not engine_errors:
        _validate_story_engine_semantics(engine, engine_errors)
    if not engine_errors:
        claim_ids.update(
            str(item.get("claim_id") or "")
            for item in engine.get("claims") or []
            if isinstance(item, dict) and item.get("claim_id")
        )
    for index, claim in enumerate(payload.get("claims") or []):
        if not isinstance(claim, dict) or not isinstance(claim.get("extensions"), dict):
            continue
        extensions = claim["extensions"]
        if extensions.get("semantic_type") != "原著事件命运":
            continue
        disposition = str(extensions.get("disposition") or "")
        if disposition not in EVENT_DISPOSITIONS:
            errors.append(
                f"claims[{index}].extensions.disposition must be an allowed 原著事件命运 state"
            )
        dependencies = extensions.get("depends_on_claims") or []
        if not isinstance(dependencies, list) or any(
            not isinstance(value, str) or value not in claim_ids for value in dependencies
        ):
            errors.append(
                f"claims[{index}].extensions.depends_on_claims must reference current stable claims"
            )
        for field in EVENT_CAUSAL_REFERENCE_FIELDS:
            references = extensions.get(field)
            if (
                not isinstance(references, list)
                or not references
                or any(not isinstance(value, str) or not value.strip() for value in references)
            ):
                errors.append(
                    f"claims[{index}].extensions.{field} must be a non-empty string list"
                )
            elif any(value not in claim_ids for value in references):
                errors.append(
                    f"claims[{index}].extensions.{field} must reference current stable claims"
                )
        if disposition == "待决定" and not str(claim.get("uncertainty") or "").strip():
            errors.append(f"claims[{index}] 待决定 requires a non-empty uncertainty")


def validate_fanfiction_route_contract(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    errors: list[str],
    *,
    require_approved: bool = False,
) -> None:
    errors.extend(validate_semantic_document(payload, require_approved=require_approved))
    if errors:
        return
    configured = config.data.get("fanfiction")
    configured = configured if isinstance(configured, dict) else {}
    artifact = payload.get("artifact") if isinstance(payload.get("artifact"), dict) else {}
    scope = artifact.get("scope") if isinstance(artifact.get("scope"), dict) else {}
    extensions = payload["extensions"]
    if payload.get("document_type") != "同人路线设计候选":
        errors.append("document_type must be 同人路线设计候选")
    if scope.get("kind") != "project":
        errors.append("fanfiction route design must use project scope")
    if extensions.get("task_type") != "fanfiction_design":
        errors.append("extensions.task_type must be fanfiction_design")
    if extensions.get("continuity_mode") != configured.get("continuity_mode"):
        errors.append("extensions.continuity_mode must match project.yaml")
    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon = _read_document(canon_path)
    if validate_semantic_document(canon, require_approved=True):
        errors.append("fanfiction route design requires approved project source Canon")
        return
    if extensions.get("source_canon_sha256") != sha256(canon_path.read_bytes()).hexdigest():
        errors.append("extensions.source_canon_sha256 is stale")
    try:
        load_current_fanfiction_story_engine(config, root)
    except FanfictionContractError as exc:
        errors.append(str(exc))
        return
    story_engine_path = root / "10_bible" / "fanfiction" / "story_engine.json"
    if extensions.get("story_engine_sha256") != sha256(story_engine_path.read_bytes()).hexdigest():
        errors.append("extensions.story_engine_sha256 is stale")
    canon_evidence = {
        str(item.get("evidence_id") or "")
        for item in canon.get("evidence_references") or []
        if isinstance(item, dict)
    }
    for index, reference in enumerate(payload.get("evidence_references") or []):
        if not isinstance(reference, dict) or reference.get("evidence_id") not in canon_evidence:
            errors.append(f"evidence_references[{index}] is outside approved project Canon")
    if not str(payload.get("body") or "").strip():
        errors.append("fanfiction route design body must describe its route and causal boundaries")
    semantic_types = fanfiction_semantic_types(payload)
    for required_type in ("初始分歧", "故事切入点", "人物知识边界", "原著人物职责"):
        if required_type not in semantic_types:
            errors.append(f"fanfiction route design requires a {required_type} semantic claim")
    if extensions.get("future_knowledge_used") is True and "未来知识可靠性" not in semantic_types:
        errors.append(
            "fanfiction route using future knowledge requires a 未来知识可靠性 semantic claim"
        )
    if "future_knowledge_used" in extensions and not isinstance(
        extensions.get("future_knowledge_used"), bool
    ):
        errors.append("extensions.future_knowledge_used must be boolean when declared")
    if "原著事件命运" not in semantic_types and not str(
        extensions.get("event_disposition_not_applicable_reason") or ""
    ).strip():
        errors.append(
            "fanfiction route design requires 原著事件命运 claims or an explicit "
            "event_disposition_not_applicable_reason"
        )
    validate_event_disposition_claims(root, payload, errors)


def load_current_fanfiction_route(
    config: ConfigDocument,
    root: Path,
) -> dict[str, Any]:
    payload = _read_document(root / "10_bible" / "fanfiction" / "fanfiction_bible.json")
    errors: list[str] = []
    validate_fanfiction_route_contract(
        config,
        root,
        payload,
        errors,
        require_approved=True,
    )
    if errors:
        raise FanfictionContractError(
            "current fanfiction route is invalid: " + "; ".join(errors)
        )
    return payload
