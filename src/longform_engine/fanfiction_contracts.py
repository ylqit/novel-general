from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.fanfiction_sources import FanfictionSourceError, project_source_contract
from longform_engine.semantic_protocols import canonical_json_hash, validate_semantic_document


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

CROSSOVER_TOPOLOGIES = frozenset(
    {"fixed_host", "fusion_world", "sequential_worlds"}
)

CROSSOVER_PAYLOAD_KINDS = frozenset(
    {
        "character",
        "body_or_soul",
        "ability",
        "item_or_contract",
        "knowledge",
        "organization",
        "world_rule",
    }
)

CROSSOVER_ALWAYS_REQUIRED_TOPICS = frozenset({"宿主世界", "不可逆后果"})

CROSSOVER_TOPICS_BY_PAYLOAD_KIND = {
    "character": frozenset(
        {"身体与灵魂", "感知", "身份组织法律", "死亡与复活", "返回"}
    ),
    "body_or_soul": frozenset(
        {"身体与灵魂", "感知", "身份组织法律", "死亡与复活", "返回"}
    ),
    "ability": frozenset(
        {"能量关系", "能力作用对象", "激活与补充", "代价", "当地反制"}
    ),
    "item_or_contract": frozenset(
        {"装备召唤物契约", "激活与补充", "代价", "当地反制"}
    ),
    "knowledge": frozenset({"来源时间点", "信息传播"}),
    "organization": frozenset({"身份组织法律", "信息传播"}),
    "world_rule": frozenset({"身份组织法律", "信息传播"}),
}

_CROSSOVER_TRIGGER_ELEMENTS = frozenset(
    {
        "characters",
        "人物",
        "character",
        "abilities",
        "能力",
        "power",
        "powers",
        "organizations",
        "组织",
        "world",
        "世界",
    }
)


class CurrentFanfictionDocument(dict[str, Any]):
    """A validated canonical payload plus the digest of the exact bytes parsed."""

    def __init__(self, payload: dict[str, Any], *, path: Path, digest: str) -> None:
        super().__init__(payload)
        self.path = path
        self.sha256 = digest


@dataclass(frozen=True)
class CurrentFanfictionStoryEngineDocuments:
    source_canon: CurrentFanfictionDocument
    story_engine: CurrentFanfictionDocument
    paths: dict[str, Path]
    sha256: dict[str, str]


@dataclass(frozen=True)
class CurrentFanfictionDocuments:
    source_canon: CurrentFanfictionDocument
    story_engine: CurrentFanfictionDocument
    route: CurrentFanfictionDocument
    independent_review: CurrentFanfictionDocument
    review_target: CurrentFanfictionDocument
    paths: dict[str, Path]
    sha256: dict[str, str]


class FanfictionContractError(ValueError):
    """A canonical fanfiction artifact failed a typed read or current-contract check."""

    def __init__(self, *, path: Path, code: str, detail: str) -> None:
        self.path = path
        self.code = code
        self.detail = detail
        super().__init__(f"fanfiction_contract[{code}] {path}: {detail}")


def _read_document(path: Path) -> CurrentFanfictionDocument:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise FanfictionContractError(path=path, code="missing", detail="file is missing") from exc
    except OSError as exc:
        raise FanfictionContractError(
            path=path,
            code="unreadable",
            detail=f"file cannot be read: {exc}",
        ) from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FanfictionContractError(
            path=path,
            code="invalid_utf8",
            detail="file is not valid UTF-8",
        ) from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FanfictionContractError(
            path=path,
            code="invalid_json",
            detail=f"file is not valid JSON at line {exc.lineno} column {exc.colno}",
        ) from exc
    if not isinstance(payload, dict):
        raise FanfictionContractError(
            path=path,
            code="invalid",
            detail="canonical JSON root must be an object",
        )
    return CurrentFanfictionDocument(payload, path=path, digest=sha256(raw).hexdigest())


def _contract_error(path: Path, label: str, errors: list[str]) -> FanfictionContractError:
    stale_markers = (
        "stale",
        "current project source contract",
        "pinned evidence",
        "configured source",
    )
    code = "stale" if any(marker in error for error in errors for marker in stale_markers) else "invalid"
    return FanfictionContractError(
        path=path,
        code=code,
        detail=f"current {label} is invalid: {'; '.join(errors)}",
    )


def fanfiction_semantic_types(payload: dict[str, Any]) -> set[str]:
    return {
        str(claim.get("extensions", {}).get("semantic_type") or "")
        for claim in payload.get("claims") or []
        if isinstance(claim, dict) and isinstance(claim.get("extensions"), dict)
    }


def requires_crossover_contract(config: ConfigDocument) -> bool:
    """Return whether the current source configuration activates the route topology contract."""

    configured_value = config.data.get("fanfiction")
    configured: dict[str, Any] = configured_value if isinstance(configured_value, dict) else {}
    sources = [
        source
        for source in configured.get("sources") or []
        if isinstance(source, dict) and source.get("source_id")
    ]
    if configured.get("continuity_mode") == "crossover":
        return True
    if len(sources) <= 1:
        return False
    allowed_elements = {
        str(element).strip().lower()
        for source in sources
        for element in source.get("allowed_elements") or []
    }
    return bool(allowed_elements.intersection(_CROSSOVER_TRIGGER_ELEMENTS))


def crossover_required_topics(crossover: dict[str, Any]) -> set[str]:
    """Derive constitution topics from only the payload kinds actually transferred."""

    topics = set(CROSSOVER_ALWAYS_REQUIRED_TOPICS)
    for transfer in crossover.get("transfers") or []:
        if not isinstance(transfer, dict):
            continue
        for payload_kind in transfer.get("payload_kinds") or []:
            if isinstance(payload_kind, str):
                topics.update(CROSSOVER_TOPICS_BY_PAYLOAD_KIND.get(payload_kind, ()))
    return topics


def validate_crossover_route_contract(
    config: ConfigDocument,
    payload: dict[str, Any],
    errors: list[str],
) -> None:
    """Validate the topology, transfers, adapters, and constitution of one crossover route."""

    if not requires_crossover_contract(config):
        return

    configured_value = config.data.get("fanfiction")
    configured: dict[str, Any] = configured_value if isinstance(configured_value, dict) else {}
    source_ids = {
        str(source.get("source_id"))
        for source in configured.get("sources") or []
        if isinstance(source, dict) and source.get("source_id")
    }
    extensions_value = payload.get("extensions")
    if not isinstance(extensions_value, dict):
        errors.append("extensions.crossover must be a mapping for a crossover route")
        return
    crossover_value = extensions_value.get("crossover")
    if not isinstance(crossover_value, dict):
        errors.append("extensions.crossover must be a mapping for a crossover route")
        return
    crossover = crossover_value

    topology = crossover.get("topology")
    if topology not in CROSSOVER_TOPOLOGIES:
        errors.append(
            "extensions.crossover.topology must be fixed_host, fusion_world, or "
            "sequential_worlds"
        )

    default_host_source_id = crossover.get("default_host_source_id")
    if "default_host_source_id" not in crossover:
        errors.append(
            "extensions.crossover.default_host_source_id must be explicitly set to a "
            "configured source or null"
        )
    elif default_host_source_id is not None and (
        not isinstance(default_host_source_id, str)
        or default_host_source_id not in source_ids
    ):
        errors.append(
            "extensions.crossover.default_host_source_id must be a configured source or null"
        )
    if topology == "fixed_host" and (
        not isinstance(default_host_source_id, str)
        or default_host_source_id not in source_ids
    ):
        errors.append("fixed_host requires a configured default_host_source_id")
    elif topology in {"fusion_world", "sequential_worlds"} and default_host_source_id is not None:
        errors.append(f"{topology} requires default_host_source_id to be null")

    transfers_value = crossover.get("transfers")
    transfers: list[Any] = transfers_value if isinstance(transfers_value, list) else []
    if not transfers:
        errors.append("extensions.crossover.transfers must be a non-empty list")
    transferred_sources: set[str] = set()
    transfer_payloads: dict[str, set[str]] = {}
    for index, transfer in enumerate(transfers):
        if not isinstance(transfer, dict):
            errors.append(f"extensions.crossover.transfers[{index}] must be a mapping")
            continue
        source_id = transfer.get("source_id")
        source_valid = isinstance(source_id, str) and source_id in source_ids
        if not source_valid:
            errors.append(
                f"extensions.crossover.transfers[{index}].source_id must name a configured source"
            )
        else:
            transferred_sources.add(source_id)
        payload_kinds = transfer.get("payload_kinds")
        payloads_valid = (
            isinstance(payload_kinds, list)
            and bool(payload_kinds)
            and not any(
                not isinstance(item, str)
                or not item.strip()
                or item not in CROSSOVER_PAYLOAD_KINDS
                for item in payload_kinds
            )
        )
        if not payloads_valid:
            errors.append(
                f"extensions.crossover.transfers[{index}].payload_kinds must be a non-empty "
                "list containing only character, body_or_soul, ability, item_or_contract, "
                "knowledge, organization, or world_rule"
            )
        elif source_valid:
            transfer_payloads.setdefault(source_id, set()).update(payload_kinds)

    if topology == "fixed_host" and isinstance(default_host_source_id, str):
        if default_host_source_id in transferred_sources:
            errors.append("fixed_host rejects a host self-transfer in extensions.crossover.transfers")
        if not (transferred_sources - {default_host_source_id}):
            errors.append("fixed_host requires at least one actual non-host transfer into the host")
    if topology == "fusion_world" and len(transferred_sources) < 2:
        errors.append("fusion_world requires at least two participating configured transfer sources")

    declared_volume_ids: list[str] = []
    declared_volume_set: set[str] = set()
    if topology == "sequential_worlds":
        route_volume_ids = crossover.get("volume_ids")
        if (
            not isinstance(route_volume_ids, list)
            or not route_volume_ids
            or any(
                not isinstance(item, str) or not item.strip()
                for item in route_volume_ids
            )
            or len(set(route_volume_ids)) != len(route_volume_ids)
        ):
            errors.append(
                "extensions.crossover.volume_ids must be an explicit non-empty unique string "
                "list for sequential_worlds"
            )
        else:
            declared_volume_ids = list(route_volume_ids)
            declared_volume_set = set(route_volume_ids)

    claims_value = payload.get("claims")
    claims: list[Any] = claims_value if isinstance(claims_value, list) else []
    covered_topics: set[str] = set()
    adapter_records: list[dict[str, Any]] = []
    has_world_rule_priority = False
    volume_host_claim_count = 0
    volume_host_assignments: dict[str, list[str]] = {}
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        claim_extensions_value = claim.get("extensions")
        if not isinstance(claim_extensions_value, dict):
            continue
        claim_extensions = claim_extensions_value
        semantic_type = str(claim_extensions.get("semantic_type") or "")
        if semantic_type in {"跨界宪法", "跨界兼容规则"}:
            topics = claim_extensions.get("topics")
            if (
                not isinstance(topics, list)
                or not topics
                or any(not isinstance(item, str) or not item.strip() for item in topics)
            ):
                errors.append(
                    f"claims[{index}].extensions.topics must be a non-empty string list"
                )
            else:
                covered_topics.update(item.strip() for item in topics)
            continue
        if semantic_type == "世界规则优先级":
            has_world_rule_priority = True
            continue
        if semantic_type == "卷宿主世界":
            volume_host_claim_count += 1
            volume_ids = claim_extensions.get("volume_ids")
            volume_ids_valid = (
                isinstance(volume_ids, list)
                and bool(volume_ids)
                and not any(
                    not isinstance(item, str) or not item.strip() for item in volume_ids
                )
                and len(set(volume_ids)) == len(volume_ids)
            )
            if not volume_ids_valid:
                errors.append(
                    f"claims[{index}].extensions.volume_ids must be a non-empty unique string list"
                )
            host_source_id = claim_extensions.get("host_source_id")
            host_valid = isinstance(host_source_id, str) and host_source_id in source_ids
            if not host_valid:
                errors.append(
                    f"claims[{index}].extensions.host_source_id must name a configured source"
                )
            if topology == "sequential_worlds" and volume_ids_valid and host_valid:
                for volume_id in volume_ids:
                    if volume_id not in declared_volume_set:
                        errors.append(
                            f"claims[{index}].extensions.volume_ids contains undeclared "
                            f"sequential volume {volume_id}"
                        )
                    else:
                        volume_host_assignments.setdefault(volume_id, []).append(host_source_id)
            continue
        if semantic_type != "主世界适配器":
            continue

        source_id = claim_extensions.get("source_id")
        source_valid = isinstance(source_id, str) and source_id in source_ids
        if not source_valid:
            errors.append(
                f"claims[{index}].extensions.source_id must name a configured source"
            )
        elif source_id not in transferred_sources:
            errors.append(f"claims[{index}].extensions.source_id is outside actual transfers")

        adapter_payloads = claim_extensions.get("payload_kinds")
        payloads_valid = (
            isinstance(adapter_payloads, list)
            and bool(adapter_payloads)
            and not any(
                not isinstance(item, str)
                or not item.strip()
                or item not in CROSSOVER_PAYLOAD_KINDS
                for item in adapter_payloads
            )
            and len(set(adapter_payloads)) == len(adapter_payloads)
        )
        if not payloads_valid:
            errors.append(
                f"claims[{index}].extensions.payload_kinds must be a non-empty unique "
                "crossover payload-kind list"
            )
        elif source_valid and source_id in transfer_payloads and (
            set(adapter_payloads) != transfer_payloads[source_id]
        ):
            errors.append(
                f"claims[{index}].extensions.payload_kinds must exactly match the actual "
                f"transfer payload_kinds for {source_id}"
            )

        adapter_host = claim_extensions.get("host_source_id")
        adapter_volume_ids = claim_extensions.get("volume_ids")
        adapter_volume_set: set[str] = set()
        scope_valid = True
        if topology == "fixed_host":
            if adapter_host != default_host_source_id:
                errors.append(
                    f"claims[{index}].extensions.host_source_id must match the fixed_host "
                    "default_host_source_id"
                )
                scope_valid = False
            if "volume_ids" not in claim_extensions or adapter_volume_ids is not None:
                errors.append(
                    f"claims[{index}].extensions.volume_ids must be explicit null for fixed_host"
                )
                scope_valid = False
        elif topology == "fusion_world":
            if "host_source_id" not in claim_extensions or adapter_host is not None:
                errors.append(
                    f"claims[{index}].extensions.host_source_id must be explicit null for fusion_world"
                )
                scope_valid = False
            if "volume_ids" not in claim_extensions or adapter_volume_ids is not None:
                errors.append(
                    f"claims[{index}].extensions.volume_ids must be explicit null for fusion_world"
                )
                scope_valid = False
        elif topology == "sequential_worlds":
            if not isinstance(adapter_host, str) or adapter_host not in source_ids:
                errors.append(
                    f"claims[{index}].extensions.host_source_id must name a configured source "
                    "for sequential_worlds"
                )
                scope_valid = False
            if (
                not isinstance(adapter_volume_ids, list)
                or not adapter_volume_ids
                or any(
                    not isinstance(item, str) or not item.strip()
                    for item in adapter_volume_ids
                )
                or len(set(adapter_volume_ids)) != len(adapter_volume_ids)
            ):
                errors.append(
                    f"claims[{index}].extensions.volume_ids must be a non-empty unique string "
                    "list for sequential_worlds"
                )
                scope_valid = False
            else:
                adapter_volume_set = set(adapter_volume_ids)
                outside_scope = sorted(adapter_volume_set - declared_volume_set)
                if outside_scope:
                    errors.append(
                        f"claims[{index}].extensions.volume_ids are outside declared "
                        "extensions.crossover.volume_ids: " + ", ".join(outside_scope)
                    )
                    scope_valid = False
        adapter_records.append(
            {
                "index": index,
                "source_id": source_id if source_valid else "",
                "payloads_valid": payloads_valid,
                "scope_valid": scope_valid,
                "host_source_id": adapter_host,
                "volume_ids": adapter_volume_set,
            }
        )

    if topology == "sequential_worlds":
        for volume_id in declared_volume_ids:
            assignments = volume_host_assignments.get(volume_id, [])
            if len(assignments) != 1:
                errors.append(
                    f"sequential_worlds volume {volume_id} must have exactly one 卷宿主世界 "
                    "host assignment"
                )
        for adapter in adapter_records:
            for volume_id in adapter["volume_ids"]:
                assignments = volume_host_assignments.get(volume_id, [])
                if len(assignments) == 1 and adapter["host_source_id"] != assignments[0]:
                    errors.append(
                        f"claims[{adapter['index']}].extensions.host_source_id must match the "
                        f"卷宿主世界 host for {volume_id}"
                    )

    missing_topics = sorted(crossover_required_topics(crossover) - covered_topics)
    if missing_topics:
        errors.append(
            "crossover constitution topics are missing derived topics: "
            + ", ".join(missing_topics)
        )
    valid_adapter_sources = {
        str(adapter["source_id"])
        for adapter in adapter_records
        if adapter["source_id"]
        and adapter["payloads_valid"]
        and adapter["scope_valid"]
    }
    missing_adapters = sorted(transferred_sources - valid_adapter_sources)
    if missing_adapters:
        errors.append(
            "crossover route is missing valid host-world adapters for transfer sources: "
            + ", ".join(missing_adapters)
        )
    if topology in {"fixed_host", "fusion_world"}:
        for source_id in sorted(transferred_sources):
            matching = [
                adapter
                for adapter in adapter_records
                if adapter["source_id"] == source_id
                and adapter["payloads_valid"]
                and adapter["scope_valid"]
            ]
            if len(matching) != 1:
                errors.append(
                    "crossover route requires exactly one scoped host-world adapter for "
                    f"transfer source {source_id}"
                )
    elif topology == "sequential_worlds":
        for source_id in sorted(transferred_sources):
            for volume_id in declared_volume_ids:
                matching = [
                    adapter
                    for adapter in adapter_records
                    if adapter["source_id"] == source_id
                    and adapter["payloads_valid"]
                    and adapter["scope_valid"]
                    and volume_id in adapter["volume_ids"]
                ]
                if len(matching) != 1:
                    errors.append(
                        "sequential_worlds requires exactly one scoped host-world adapter for "
                        f"transfer source {source_id} in volume {volume_id}"
                    )
    if topology == "fusion_world" and not has_world_rule_priority:
        errors.append("fusion_world requires a 世界规则优先级 semantic claim")
    if topology == "sequential_worlds" and volume_host_claim_count == 0:
        errors.append("sequential_worlds requires at least one 卷宿主世界 semantic claim")


def fanfiction_route_review_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Project only the route semantics that an independent review actually approves."""

    extensions_value = payload.get("extensions")
    extensions: dict[str, Any] = (
        deepcopy(extensions_value) if isinstance(extensions_value, dict) else {}
    )
    for field in (
        "independent_review",
        "approved_candidate_sha256",
        "human_decision",
    ):
        extensions.pop(field, None)
    return {
        "document_type": deepcopy(payload.get("document_type")),
        "title": deepcopy(payload.get("title")),
        "continuity": deepcopy(payload.get("continuity")),
        "body": deepcopy(payload.get("body")),
        "claims": deepcopy(payload.get("claims")),
        "evidence_references": deepcopy(payload.get("evidence_references")),
        "uncertainties": deepcopy(payload.get("uncertainties")),
        "extensions": extensions,
    }


def fanfiction_route_review_projection_sha256(payload: dict[str, Any]) -> str:
    return canonical_json_hash(fanfiction_route_review_projection(payload))


def _source_contract_record(source: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(source.get("source_id") or ""),
        "work_id": str(contract["setting"].get("作品ID") or ""),
        "title": str(source.get("title") or ""),
        "creator": str(source.get("creator") or ""),
        "canon_cutoff": str(source.get("canon_cutoff") or ""),
        "binding_sha256": str(contract.get("binding_sha256") or ""),
        "coverage_plan_sha256": str(contract.get("coverage_sha256") or ""),
        "item_bindings": [
            {
                "item_id": str(item.get("item_id") or ""),
                "bundle_sha256": str(item.get("bundle_sha256") or ""),
                "normalization_sha256": str(item.get("normalization_sha256") or ""),
                "extraction_sha256": str(item.get("extraction_sha256") or ""),
            }
            for item in contract["binding"].get("items") or []
            if isinstance(item, dict)
        ],
    }


def current_fanfiction_source_contracts(config: ConfigDocument) -> list[dict[str, Any]]:
    """Return CLI-owned project source pins in canonical source order."""

    configured = config.data.get("fanfiction")
    configured = configured if isinstance(configured, dict) else {}
    records: list[dict[str, Any]] = []
    for source in configured.get("sources") or []:
        if not isinstance(source, dict) or not source.get("source_id"):
            continue
        source_id = str(source["source_id"])
        contract = project_source_contract(config, source_id)
        records.append(_source_contract_record(source, contract))
    return records


def validate_fanfiction_source_canon(
    config: ConfigDocument,
    payload: dict[str, Any],
    errors: list[str],
    *,
    require_approved: bool = False,
) -> None:
    if payload.get("schema") in {
        "fanfiction_source_canon_v1",
        "fanfiction_source_canon_v2",
        "fanfiction_source_canon_v3",
    }:
        errors.append(
            f"{payload.get('schema')} is incompatible with semantic_document_v1; "
            "use the explicit v0.11 audit/import path and rebuild semantic Canon"
        )
        return
    errors.extend(validate_semantic_document(payload, require_approved=require_approved))
    if errors:
        return
    configured_value = config.data.get("fanfiction")
    configured: dict[str, Any] = configured_value if isinstance(configured_value, dict) else {}
    artifact_value = payload.get("artifact")
    artifact: dict[str, Any] = artifact_value if isinstance(artifact_value, dict) else {}
    scope_value = artifact.get("scope")
    scope: dict[str, Any] = scope_value if isinstance(scope_value, dict) else {}
    extensions_value = payload.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    if payload.get("document_type") != "项目原著基线Canon候选":
        errors.append("fanfiction source Canon document_type must be 项目原著基线Canon候选")
    if scope.get("kind") != "project":
        errors.append("fanfiction source Canon must use project scope")
    if extensions.get("task_type") != "fanfiction_canon":
        errors.append("extensions.task_type must be fanfiction_canon")
    if extensions.get("continuity_mode") != configured.get("continuity_mode"):
        errors.append("extensions.continuity_mode must match project.yaml")
    configured_sources = {
        str(item.get("source_id")): item
        for item in configured.get("sources") or []
        if isinstance(item, dict) and item.get("source_id")
    }
    raw_contracts = extensions.get("source_contracts")
    contracts = raw_contracts if isinstance(raw_contracts, list) else []
    stored_by_source = {
        str(item.get("source_id") or ""): item for item in contracts if isinstance(item, dict)
    }
    if len(stored_by_source) != len(contracts) or set(stored_by_source) != set(configured_sources):
        errors.append(
            "extensions.source_contracts must cover every configured source exactly once; "
            "the current configured source set is stale"
        )
        return
    approved_evidence: dict[str, tuple[str, dict[str, Any]]] = {}
    for source_id, source in configured_sources.items():
        try:
            contract = project_source_contract(config, source_id)
        except (FanfictionSourceError, OSError, KeyError) as exc:
            errors.append(f"project source contract is unavailable for {source_id}: {exc}")
            continue
        if stored_by_source[source_id] != _source_contract_record(source, contract):
            errors.append(
                f"extensions.source_contracts[{source_id}] does not match the current project source contract"
            )
        for record in contract["evidence"].values():
            if not isinstance(record, dict):
                continue
            evidence_id = str(record.get("evidence_id") or "")
            if evidence_id:
                approved_evidence[evidence_id] = (source_id, record)
    reference_sources: dict[str, str] = {}
    for index, reference in enumerate(payload.get("evidence_references") or []):
        evidence_id = str(reference.get("evidence_id") or "") if isinstance(reference, dict) else ""
        approved = approved_evidence.get(evidence_id)
        if approved is None:
            errors.append(f"evidence_references[{index}] is not approved by a pinned project source")
            continue
        source_id, expected = approved
        for actual_field, expected_field in (
            ("item_id", "item_id"),
            ("asset_id", "asset_id"),
            ("segment_id", "segment_id"),
            ("locator", "locator"),
            ("excerpt", "excerpt"),
        ):
            if reference.get(actual_field) != expected.get(expected_field):
                errors.append(
                    f"evidence_references[{index}].{actual_field} does not match pinned evidence"
                )
        reference_sources[evidence_id] = source_id
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        errors.append("fanfiction source Canon requires at least one semantic claim")
        return
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            continue
        claim_extensions_value = claim.get("extensions")
        claim_extensions: dict[str, Any] = (
            claim_extensions_value if isinstance(claim_extensions_value, dict) else {}
        )
        source_id = str(claim_extensions.get("source_id") or "")
        if source_id not in configured_sources:
            errors.append(f"claims[{index}].extensions.source_id must name a configured source")
        if not str(claim.get("claim_id") or "").startswith(f"{source_id}:"):
            errors.append(f"claims[{index}].claim_id must use its source namespace")
        refs = [str(value) for value in claim.get("evidence_refs") or []]
        if not refs:
            errors.append(f"claims[{index}] requires evidence_refs")
        elif any(reference_sources.get(value) != source_id for value in refs):
            errors.append(f"claims[{index}] must reference evidence from the same source")
        if len(str(claim.get("statement") or "")) > 800:
            errors.append(f"claims[{index}].statement exceeds the bounded paraphrase limit")


def _load_current_source_canon(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionDocument:
    path = root / "10_bible" / "fanfiction" / "source_canon.json"
    payload = _read_document(path)
    errors: list[str] = []
    validate_fanfiction_source_canon(
        config,
        payload,
        errors,
        require_approved=True,
    )
    if errors:
        raise _contract_error(path, "fanfiction source_canon", errors)
    return payload


def load_current_fanfiction_source_canon(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionDocument:
    """Load one approved source Canon that matches all current project source pins."""

    return _load_current_source_canon(config, root)


def _validate_story_engine_semantics(
    payload: dict[str, Any],
    errors: list[str],
) -> None:
    extensions_value = payload.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    artifact_value = payload.get("artifact")
    artifact: dict[str, Any] = artifact_value if isinstance(artifact_value, dict) else {}
    scope_value = artifact.get("scope")
    scope: dict[str, Any] = scope_value if isinstance(scope_value, dict) else {}
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
    _source_canon: CurrentFanfictionDocument | None = None,
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
    try:
        canon = _source_canon or _load_current_source_canon(config, root)
    except FanfictionContractError as exc:
        errors.append(str(exc))
        return
    if extensions.get("source_canon_sha256") != canon.sha256:
        errors.append("extensions.source_canon_sha256 is stale")


def _load_current_story_engine(
    config: ConfigDocument,
    root: Path,
    *,
    source_canon: CurrentFanfictionDocument | None = None,
) -> CurrentFanfictionDocument:
    current_source = source_canon or _load_current_source_canon(config, root)
    path = root / "10_bible" / "fanfiction" / "story_engine.json"
    payload = _read_document(path)
    errors: list[str] = []
    validate_fanfiction_story_engine(
        config,
        root,
        payload,
        errors,
        require_approved=True,
        _source_canon=current_source,
    )
    if errors:
        raise _contract_error(path, "fanfiction story engine", errors)
    return payload


def load_current_fanfiction_story_engine(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionDocument:
    return _load_current_story_engine(config, root)


def load_current_fanfiction_story_engine_documents(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionStoryEngineDocuments:
    """Load one coherent source→engine chain, reading each canonical file once."""

    source_canon = _load_current_source_canon(config, root)
    story_engine = _load_current_story_engine(config, root, source_canon=source_canon)
    paths = {
        "source_canon": source_canon.path,
        "story_engine": story_engine.path,
    }
    return CurrentFanfictionStoryEngineDocuments(
        source_canon=source_canon,
        story_engine=story_engine,
        paths=paths,
        sha256={
            "source_canon": source_canon.sha256,
            "story_engine": story_engine.sha256,
        },
    )


def validate_event_disposition_claims(
    config: ConfigDocument,
    root: Path,
    payload: dict[str, Any],
    errors: list[str],
    *,
    _source_canon: CurrentFanfictionDocument | None = None,
    _story_engine: CurrentFanfictionDocument | None = None,
) -> None:
    claim_ids = {
        str(item.get("claim_id") or "")
        for item in payload.get("claims") or []
        if isinstance(item, dict)
    }
    try:
        source_canon = _source_canon or _load_current_source_canon(config, root)
        story_engine = _story_engine or _load_current_story_engine(
            config,
            root,
            source_canon=source_canon,
        )
    except FanfictionContractError as exc:
        errors.append(str(exc))
        return
    for document in (source_canon, story_engine):
        claim_ids.update(
            str(item.get("claim_id") or "")
            for item in document.get("claims") or []
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
    _source_canon: CurrentFanfictionDocument | None = None,
    _story_engine: CurrentFanfictionDocument | None = None,
) -> None:
    errors.extend(validate_semantic_document(payload, require_approved=require_approved))
    if errors:
        return
    configured_value = config.data.get("fanfiction")
    configured: dict[str, Any] = configured_value if isinstance(configured_value, dict) else {}
    artifact_value = payload.get("artifact")
    artifact: dict[str, Any] = artifact_value if isinstance(artifact_value, dict) else {}
    scope_value = artifact.get("scope")
    scope: dict[str, Any] = scope_value if isinstance(scope_value, dict) else {}
    extensions_value = payload.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    if payload.get("document_type") != "同人路线设计候选":
        errors.append("document_type must be 同人路线设计候选")
    if scope.get("kind") != "project":
        errors.append("fanfiction route design must use project scope")
    if extensions.get("task_type") != "fanfiction_design":
        errors.append("extensions.task_type must be fanfiction_design")
    if extensions.get("continuity_mode") != configured.get("continuity_mode"):
        errors.append("extensions.continuity_mode must match project.yaml")
    validate_crossover_route_contract(config, payload, errors)
    try:
        source_canon = _source_canon or _load_current_source_canon(config, root)
        story_engine = _story_engine or _load_current_story_engine(
            config,
            root,
            source_canon=source_canon,
        )
    except FanfictionContractError as exc:
        errors.append(str(exc))
        return
    canon = source_canon
    if extensions.get("source_canon_sha256") != canon.sha256:
        errors.append("extensions.source_canon_sha256 is stale")
    if extensions.get("story_engine_sha256") != story_engine.sha256:
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
    validate_event_disposition_claims(
        config,
        root,
        payload,
        errors,
        _source_canon=source_canon,
        _story_engine=story_engine,
    )


def validate_fanfiction_review_contract(
    payload: dict[str, Any],
    errors: list[str],
    *,
    require_approved: bool = False,
    require_pass: bool = False,
    source_canon_sha256: str,
    story_engine_sha256: str,
    review_target_path: str | None = None,
    review_target_sha256: str | None = None,
) -> None:
    """Validate the semantic and pinned-input contract of an isolated route review."""

    errors.extend(validate_semantic_document(payload, require_approved=require_approved))
    if errors:
        return
    artifact_value = payload.get("artifact")
    artifact: dict[str, Any] = artifact_value if isinstance(artifact_value, dict) else {}
    scope_value = artifact.get("scope")
    scope: dict[str, Any] = scope_value if isinstance(scope_value, dict) else {}
    extensions_value = payload.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    if payload.get("document_type") != "同人路线独立复核":
        errors.append("document_type must be 同人路线独立复核")
    if scope.get("kind") != "project":
        errors.append("fanfiction route review must use project scope")
    if extensions.get("task_type") != "fanfiction_design_review":
        errors.append("extensions.task_type must be fanfiction_design_review")
    verdict = extensions.get("verdict")
    if verdict not in {"pass", "need_human", "reject"}:
        errors.append("extensions.verdict must be pass, need_human, or reject")
    elif require_pass and verdict != "pass":
        errors.append("extensions.verdict must be pass for the current route")
    for field, digest in (
        ("source_canon_sha256", source_canon_sha256),
        ("story_engine_sha256", story_engine_sha256),
    ):
        if extensions.get(field) != digest:
            errors.append(f"extensions.{field} is stale")
    declared_target_path = extensions.get("review_target_path")
    if not isinstance(declared_target_path, str) or not declared_target_path.strip():
        errors.append("extensions.review_target_path must be a non-empty project-relative path")
    elif review_target_path is not None and declared_target_path != review_target_path:
        errors.append("extensions.review_target_path is stale")
    declared_target_sha256 = extensions.get("review_target_sha256")
    if not isinstance(declared_target_sha256, str) or len(declared_target_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in declared_target_sha256
    ):
        errors.append("extensions.review_target_sha256 must be a SHA-256 digest")
    elif review_target_sha256 is not None and declared_target_sha256 != review_target_sha256:
        errors.append("extensions.review_target_sha256 is stale")
    claims_value = payload.get("claims")
    claims: list[Any] = claims_value if isinstance(claims_value, list) else []
    blocking = []
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_extensions_value = claim.get("extensions")
        claim_extensions = (
            claim_extensions_value if isinstance(claim_extensions_value, dict) else {}
        )
        if claim_extensions.get("severity") == "blocking":
            blocking.append(claim)
    if verdict == "pass" and blocking:
        errors.append("a pass review cannot contain blocking claims")
    if verdict in {"need_human", "reject"} and not blocking:
        errors.append(f"a {verdict} review requires at least one blocking claim")


def _validate_independent_review(
    config: ConfigDocument,
    root: Path,
    route: dict[str, Any],
    *,
    source_canon: CurrentFanfictionDocument,
    story_engine: CurrentFanfictionDocument,
) -> tuple[CurrentFanfictionDocument, CurrentFanfictionDocument]:
    extensions_value = route.get("extensions")
    extensions: dict[str, Any] = extensions_value if isinstance(extensions_value, dict) else {}
    review_value = extensions.get("independent_review")
    review: dict[str, Any] = review_value if isinstance(review_value, dict) else {}
    route_path = root / "10_bible" / "fanfiction" / "fanfiction_bible.json"
    review_path_value = review.get("review_path")
    if not isinstance(review_path_value, str) or not review_path_value.strip():
        raise FanfictionContractError(
            path=route_path,
            code="invalid",
            detail="current route independent review path is missing",
        )
    review_path = (root / review_path_value).resolve()
    try:
        review_path.relative_to(root.resolve())
    except ValueError as exc:
        raise FanfictionContractError(
            path=review_path,
            code="invalid",
            detail="current route independent review path escapes the project",
        ) from exc
    if review.get("verdict") != "pass":
        raise FanfictionContractError(
            path=review_path,
            code="invalid",
            detail="current route independent review verdict must be pass",
        )
    if review.get("reviewer_role") != "fanfiction_route_reviewer":
        raise FanfictionContractError(
            path=review_path,
            code="invalid",
            detail="current route independent review must use the isolated fanfiction_route_reviewer role",
        )
    projection_digest = review.get("reviewed_route_projection_sha256")
    if not isinstance(projection_digest, str) or len(projection_digest) != 64 or any(
        character not in "0123456789abcdef" for character in projection_digest
    ):
        raise FanfictionContractError(
            path=route_path,
            code="invalid",
            detail="current route independent review projection binding must be a SHA-256 digest",
        )
    try:
        review_payload = _read_document(review_path)
    except FanfictionContractError as exc:
        raise FanfictionContractError(
            path=review_path,
            code=exc.code,
            detail=f"current route independent review artifact is unavailable: {exc.detail}",
        ) from exc
    if review.get("review_sha256") != review_payload.sha256:
        raise FanfictionContractError(
            path=review_path,
            code="stale",
            detail="current route independent review binding is stale",
        )
    review_errors: list[str] = []
    validate_fanfiction_review_contract(
        review_payload,
        review_errors,
        require_approved=True,
        require_pass=True,
        source_canon_sha256=source_canon.sha256,
        story_engine_sha256=story_engine.sha256,
    )
    if review_errors:
        raise _contract_error(review_path, "fanfiction route independent review", review_errors)
    artifact_value = review_payload.get("artifact")
    artifact: dict[str, Any] = artifact_value if isinstance(artifact_value, dict) else {}
    review_extensions_value = review_payload.get("extensions")
    review_extensions: dict[str, Any] = (
        review_extensions_value if isinstance(review_extensions_value, dict) else {}
    )
    if review.get("review_artifact_id") != artifact.get("artifact_id"):
        raise FanfictionContractError(
            path=review_path,
            code="stale",
            detail="current route independent review artifact binding is stale",
        )
    target_path_value = review_extensions.get("review_target_path")
    if not isinstance(target_path_value, str):
        raise FanfictionContractError(path=review_path, code="invalid", detail="review target is invalid")
    target_path = (root / target_path_value).resolve()
    try:
        target_path.relative_to(root.resolve())
    except ValueError as exc:
        raise FanfictionContractError(
            path=target_path,
            code="invalid",
            detail="current route independent review target path escapes the project",
        ) from exc
    try:
        target = _read_document(target_path)
    except FanfictionContractError as exc:
        raise FanfictionContractError(
            path=target_path,
            code=exc.code,
            detail=f"current route independent review target is unavailable: {exc.detail}",
        ) from exc
    target_errors: list[str] = []
    validate_fanfiction_route_contract(
        config,
        root,
        target,
        target_errors,
        require_approved=False,
        _source_canon=source_canon,
        _story_engine=story_engine,
    )
    if target_errors:
        raise FanfictionContractError(
            path=target_path,
            code="invalid",
            detail=(
                "current route independent review target is not a valid fanfiction route: "
                + "; ".join(target_errors)
            ),
        )
    if review_extensions.get("review_target_sha256") != target.sha256:
        raise FanfictionContractError(
            path=target_path,
            code="stale",
            detail="current route independent review target hash is stale",
        )
    target_projection_digest = fanfiction_route_review_projection_sha256(target)
    route_projection_digest = fanfiction_route_review_projection_sha256(route)
    if (
        projection_digest != target_projection_digest
        or projection_digest != route_projection_digest
    ):
        raise FanfictionContractError(
            path=route_path,
            code="stale",
            detail="current route semantics do not match the independently reviewed route projection",
        )
    return review_payload, target


def _load_current_route(
    config: ConfigDocument,
    root: Path,
    *,
    source_canon: CurrentFanfictionDocument | None = None,
    story_engine: CurrentFanfictionDocument | None = None,
) -> tuple[
    CurrentFanfictionDocument,
    CurrentFanfictionDocument,
    CurrentFanfictionDocument,
]:
    current_source = source_canon or _load_current_source_canon(config, root)
    current_engine = story_engine or _load_current_story_engine(
        config,
        root,
        source_canon=current_source,
    )
    path = root / "10_bible" / "fanfiction" / "fanfiction_bible.json"
    payload = _read_document(path)
    errors: list[str] = []
    validate_fanfiction_route_contract(
        config,
        root,
        payload,
        errors,
        require_approved=True,
        _source_canon=current_source,
        _story_engine=current_engine,
    )
    if errors:
        raise _contract_error(path, "fanfiction route", errors)
    review, review_target = _validate_independent_review(
        config,
        root,
        payload,
        source_canon=current_source,
        story_engine=current_engine,
    )
    return payload, review, review_target


def load_current_fanfiction_route(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionDocument:
    route, _review, _review_target = _load_current_route(config, root)
    return route


def load_current_fanfiction_documents(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionDocuments:
    """Load one coherent source→engine→route chain, reading each canonical file once."""

    engine_documents = load_current_fanfiction_story_engine_documents(config, root)
    source_canon = engine_documents.source_canon
    story_engine = engine_documents.story_engine
    route, review, review_target = _load_current_route(
        config,
        root,
        source_canon=source_canon,
        story_engine=story_engine,
    )
    paths = {
        "source_canon": root / "10_bible" / "fanfiction" / "source_canon.json",
        "story_engine": root / "10_bible" / "fanfiction" / "story_engine.json",
        "route_design": root / "10_bible" / "fanfiction" / "fanfiction_bible.json",
        "independent_review": review.path,
        "review_target": review_target.path,
    }
    return CurrentFanfictionDocuments(
        source_canon=source_canon,
        story_engine=story_engine,
        route=route,
        independent_review=review,
        review_target=review_target,
        paths=paths,
        sha256={
            "source_canon": source_canon.sha256,
            "story_engine": story_engine.sha256,
            "route_design": route.sha256,
            "independent_review": review.sha256,
            "review_target": review_target.sha256,
        },
    )


__all__ = [
    "CROSSOVER_ALWAYS_REQUIRED_TOPICS",
    "CROSSOVER_PAYLOAD_KINDS",
    "CROSSOVER_TOPOLOGIES",
    "CROSSOVER_TOPICS_BY_PAYLOAD_KIND",
    "EVENT_CAUSAL_REFERENCE_FIELDS",
    "EVENT_DISPOSITIONS",
    "CurrentFanfictionDocument",
    "CurrentFanfictionDocuments",
    "CurrentFanfictionStoryEngineDocuments",
    "FanfictionContractError",
    "STORY_ENGINE_REQUIRED_SEMANTIC_TYPES",
    "STORY_ENGINE_ROUTE_FAMILIES",
    "current_fanfiction_source_contracts",
    "crossover_required_topics",
    "fanfiction_route_review_projection",
    "fanfiction_route_review_projection_sha256",
    "fanfiction_semantic_types",
    "load_current_fanfiction_route",
    "load_current_fanfiction_documents",
    "load_current_fanfiction_source_canon",
    "load_current_fanfiction_story_engine",
    "load_current_fanfiction_story_engine_documents",
    "requires_crossover_contract",
    "validate_crossover_route_contract",
    "validate_event_disposition_claims",
    "validate_fanfiction_review_contract",
    "validate_fanfiction_route_contract",
    "validate_fanfiction_source_canon",
    "validate_fanfiction_story_engine",
]
