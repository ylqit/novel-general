from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.fanfiction_sources import FanfictionSourceError, project_source_contract
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
    root: Path,
    route: dict[str, Any],
    *,
    source_canon: CurrentFanfictionDocument,
    story_engine: CurrentFanfictionDocument,
) -> CurrentFanfictionDocument:
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
    if review_extensions.get("review_target_sha256") != target.sha256:
        raise FanfictionContractError(
            path=target_path,
            code="stale",
            detail="current route independent review target hash is stale",
        )
    return review_payload


def _load_current_route(
    config: ConfigDocument,
    root: Path,
    *,
    source_canon: CurrentFanfictionDocument | None = None,
    story_engine: CurrentFanfictionDocument | None = None,
) -> tuple[CurrentFanfictionDocument, CurrentFanfictionDocument]:
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
    review = _validate_independent_review(
        root,
        payload,
        source_canon=current_source,
        story_engine=current_engine,
    )
    return payload, review


def load_current_fanfiction_route(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionDocument:
    route, _review = _load_current_route(config, root)
    return route


def load_current_fanfiction_documents(
    config: ConfigDocument,
    root: Path,
) -> CurrentFanfictionDocuments:
    """Load one coherent source→engine→route chain, reading each canonical file once."""

    engine_documents = load_current_fanfiction_story_engine_documents(config, root)
    source_canon = engine_documents.source_canon
    story_engine = engine_documents.story_engine
    route, review = _load_current_route(
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
    }
    return CurrentFanfictionDocuments(
        source_canon=source_canon,
        story_engine=story_engine,
        route=route,
        independent_review=review,
        paths=paths,
        sha256={
            "source_canon": source_canon.sha256,
            "story_engine": story_engine.sha256,
            "route_design": route.sha256,
            "independent_review": review.sha256,
        },
    )


__all__ = [
    "EVENT_CAUSAL_REFERENCE_FIELDS",
    "EVENT_DISPOSITIONS",
    "CurrentFanfictionDocument",
    "CurrentFanfictionDocuments",
    "CurrentFanfictionStoryEngineDocuments",
    "FanfictionContractError",
    "STORY_ENGINE_REQUIRED_SEMANTIC_TYPES",
    "STORY_ENGINE_ROUTE_FAMILIES",
    "current_fanfiction_source_contracts",
    "fanfiction_semantic_types",
    "load_current_fanfiction_route",
    "load_current_fanfiction_documents",
    "load_current_fanfiction_source_canon",
    "load_current_fanfiction_story_engine",
    "load_current_fanfiction_story_engine_documents",
    "validate_event_disposition_claims",
    "validate_fanfiction_review_contract",
    "validate_fanfiction_route_contract",
    "validate_fanfiction_source_canon",
    "validate_fanfiction_story_engine",
]
