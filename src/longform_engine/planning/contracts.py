"""v0.10 planning contracts and deterministic structural validation.

This module deliberately validates protocol facts only.  It never claims that a
story choice is good, meaningful, or semantically correct; those judgements are
owned by an isolated evidence review and an explicit human decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable
import json
import re

from longform_engine.reader_promises_v2 import validate_reader_promise_candidates
from longform_engine.chapter_contract import validate_chapter_contract
from longform_engine.reader_promises_v2 import materialize_explicit_reader_promises


STRUCTURAL_VALIDATION_SCHEMA = "structural_validation_v2"
PLANNING_BUNDLE_SCHEMA = "planning_bundle_v1"
BOOK_SPINE_SCHEMA = "book_spine_v1"
VOLUME_SKELETONS_SCHEMA = "volume_skeletons_v1"
VOLUME_PLAN_SCHEMA = "volume_plan_v1"
ROLLING_WINDOW_SCHEMA = "rolling_window_plan_v2"
CHAPTER_FORECAST_SCHEMA = "chapter_forecast_v1"
SEMANTIC_OBLIGATION_SCHEMA = "semantic_obligation_v1"
PLOT_NODE_SCHEMA = "plot_node_v1"
PLOT_NODE_TABLE_SCHEMA = "plot_node_table_v1"

SEMANTIC_DOMAINS = frozenset(
    {
        "world",
        "knowledge",
        "relationship",
        "character",
        "resource",
        "location",
        "promise",
        "reader_cognition",
    }
)
NODE_REQUIREMENTS = frozenset({"required", "conditional", "optional"})
NODE_KINDS = frozenset({"micro", "state_change"})
PRECONDITION_TYPES = frozenset({"deterministic", "semantic", "human"})
WINDOW_TIERS = ("firm", "directional", "horizon")
STABLE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._:-][a-z0-9]+)*$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class StructuralValidation:
    """Machine-checkable protocol result; `ok` is never a semantic verdict."""

    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    subject_schema: str
    subject_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": STRUCTURAL_VALIDATION_SCHEMA,
            "ok": self.ok,
            "semantic_verdict": "not_evaluated",
            "subject_schema": self.subject_schema,
            "subject_sha256": self.subject_sha256,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def canonical_json_hash(payload: Any) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def validate_planning_bundle(payload: Any) -> StructuralValidation:
    errors: list[str] = []
    warnings: list[str] = []
    if not _exact_object(
        payload,
        {
            "schema",
            "book_spine",
            "volume_skeletons",
            "active_volume_plan",
            "rolling_window",
            "chapter_forecasts",
            "chapter_contracts",
            "semantic_obligations",
            "plot_node_tables",
        },
        "planning_bundle",
        errors,
    ):
        return _result(payload, errors, warnings)
    if payload.get("schema") != PLANNING_BUNDLE_SCHEMA:
        errors.append(f"schema must be {PLANNING_BUNDLE_SCHEMA}")

    validate_book_spine(payload.get("book_spine"), errors)
    skeleton_ids = validate_volume_skeletons(payload.get("volume_skeletons"), errors)
    active_range = validate_volume_plan(
        payload.get("active_volume_plan"), errors, skeleton_ids=skeleton_ids
    )
    window_range = validate_rolling_window(
        payload.get("rolling_window"), errors, active_volume_range=active_range
    )

    obligations = _list(payload.get("semantic_obligations"), "semantic_obligations", errors)
    obligation_ids: set[str] = set()
    for index, obligation in enumerate(obligations):
        obligation_id = validate_semantic_obligation(
            obligation, errors, f"semantic_obligations[{index}]"
        )
        if obligation_id:
            if obligation_id in obligation_ids:
                errors.append(f"duplicate semantic obligation id: {obligation_id}")
            obligation_ids.add(obligation_id)

    forecasts = _list(payload.get("chapter_forecasts"), "chapter_forecasts", errors)
    forecast_chapters: set[int] = set()
    for index, forecast in enumerate(forecasts):
        chapter = validate_chapter_forecast(
            forecast,
            errors,
            f"chapter_forecasts[{index}]",
            obligation_ids=obligation_ids,
        )
        if chapter:
            if chapter in forecast_chapters:
                errors.append(f"duplicate chapter forecast: {chapter}")
            forecast_chapters.add(chapter)

    node_tables = _list(payload.get("plot_node_tables"), "plot_node_tables", errors)
    node_chapters: set[int] = set()
    node_tables_by_chapter: dict[int, dict[str, Any]] = {}
    for index, table in enumerate(node_tables):
        chapter = validate_plot_node_table(
            table,
            errors,
            f"plot_node_tables[{index}]",
            obligation_ids=obligation_ids,
        )
        if chapter:
            if chapter in node_chapters:
                errors.append(f"duplicate plot node table: {chapter}")
            node_chapters.add(chapter)
            if isinstance(table, dict):
                node_tables_by_chapter[chapter] = table

    contracts = _list(payload.get("chapter_contracts"), "chapter_contracts", errors)
    contract_chapters: set[int] = set()
    try:
        promise_ledger = materialize_explicit_reader_promises(
            payload["active_volume_plan"]["promise_threads"],
            approved_by="human",
        )
    except (KeyError, TypeError, ValueError):
        promise_ledger = None
    for index, contract in enumerate(contracts):
        prefix = f"chapter_contracts[{index}]"
        contract_errors = validate_chapter_contract(
            contract,
            promise_ledger=promise_ledger,
        )
        errors.extend(f"{prefix}.{error}" for error in contract_errors)
        if not isinstance(contract, dict):
            continue
        chapter = contract.get("chapter_number")
        if not isinstance(chapter, int) or isinstance(chapter, bool) or chapter <= 0:
            continue
        if chapter in contract_chapters:
            errors.append(f"duplicate chapter contract: {chapter}")
        contract_chapters.add(chapter)
        table = node_tables_by_chapter.get(chapter)
        reference = contract.get("plot_node_table_ref")
        if not isinstance(table, dict) or not isinstance(reference, dict) or (
            reference.get("table_id") != table.get("table_id")
            or reference.get("candidate_sha256") != table.get("candidate_sha256")
        ):
            errors.append(f"{prefix}.plot_node_table_ref does not match the chapter node table")
        forecast = next(
            (
                item
                for item in forecasts
                if isinstance(item, dict) and item.get("chapter_number") == chapter
            ),
            None,
        )
        if not isinstance(forecast, dict) or contract.get("forecast_ref") != forecast.get(
            "forecast_id"
        ):
            errors.append(f"{prefix}.forecast_ref does not match the chapter forecast")
        missing_obligations = sorted(
            set(contract.get("semantic_obligation_refs") or []) - obligation_ids
        )
        if missing_obligations:
            errors.append(
                f"{prefix}.semantic_obligation_refs are unresolved: "
                + ", ".join(missing_obligations)
            )

    if window_range:
        start, end = window_range
        expected = set(range(start, end + 1))
        if forecast_chapters != expected:
            errors.append(
                "chapter_forecasts must exactly cover the rolling window: "
                + _range_difference(expected, forecast_chapters)
            )
        firm_end = min(start + 2, end)
        required_node_chapters = set(range(start, firm_end + 1))
        if node_chapters != required_node_chapters:
            errors.append(
                "plot_node_tables must exactly cover the firm 1-3 chapter tier: "
                + _range_difference(required_node_chapters, node_chapters)
            )
        if contract_chapters != required_node_chapters:
            errors.append(
                "chapter_contracts must exactly cover the firm 1-3 chapter tier: "
                + _range_difference(required_node_chapters, contract_chapters)
            )

    if not obligation_ids:
        warnings.append("semantic_obligations is empty; semantic review must justify this explicitly")
    return _result(payload, errors, warnings)


def validate_book_spine(value: Any, errors: list[str]) -> None:
    fields = {
        "schema",
        "spine_id",
        "premise",
        "central_conflict",
        "ending_boundary",
        "protagonist_arc",
        "reader_value",
        "protected_invariants",
    }
    if not _exact_object(value, fields, "book_spine", errors):
        return
    if value.get("schema") != BOOK_SPINE_SCHEMA:
        errors.append(f"book_spine.schema must be {BOOK_SPINE_SCHEMA}")
    _stable_id(value.get("spine_id"), "book_spine.spine_id", errors)
    for field in (
        "premise",
        "central_conflict",
        "ending_boundary",
        "protagonist_arc",
        "reader_value",
    ):
        _text(value.get(field), f"book_spine.{field}", errors)
    _string_list(
        value.get("protected_invariants"),
        "book_spine.protected_invariants",
        errors,
        non_empty=True,
    )


def validate_volume_skeletons(value: Any, errors: list[str]) -> set[str]:
    if not _exact_object(value, {"schema", "items"}, "volume_skeletons", errors):
        return set()
    if value.get("schema") != VOLUME_SKELETONS_SCHEMA:
        errors.append(f"volume_skeletons.schema must be {VOLUME_SKELETONS_SCHEMA}")
    items = _list(value.get("items"), "volume_skeletons.items", errors, non_empty=True)
    ids: set[str] = set()
    previous_end = 0
    fields = {
        "volume_id",
        "order",
        "title",
        "chapter_range",
        "volume_goal",
        "entry_state",
        "exit_state",
        "target_characters",
        "status",
    }
    for index, item in enumerate(items):
        prefix = f"volume_skeletons.items[{index}]"
        if not _exact_object(item, fields, prefix, errors):
            continue
        volume_id = _stable_id(item.get("volume_id"), f"{prefix}.volume_id", errors)
        if volume_id:
            if volume_id in ids:
                errors.append(f"duplicate volume_id: {volume_id}")
            ids.add(volume_id)
        order = _positive_int(item.get("order"), f"{prefix}.order", errors)
        if order and order != index + 1:
            errors.append(f"{prefix}.order must be contiguous and equal {index + 1}")
        chapter_range = _chapter_range(item.get("chapter_range"), f"{prefix}.chapter_range", errors)
        if chapter_range:
            start, end = chapter_range
            if previous_end and start != previous_end + 1:
                errors.append(f"{prefix}.chapter_range must continue after chapter {previous_end}")
            previous_end = end
        for field in ("title", "volume_goal", "entry_state", "exit_state"):
            _text(item.get(field), f"{prefix}.{field}", errors)
        _positive_int(item.get("target_characters"), f"{prefix}.target_characters", errors)
        if item.get("status") not in {
            "proposed",
            "approved",
            "active",
            "closing",
            "completed",
        }:
            errors.append(f"{prefix}.status is invalid")
    return ids


def validate_volume_plan(
    value: Any,
    errors: list[str],
    *,
    skeleton_ids: set[str],
) -> tuple[int, int] | None:
    fields = {
        "schema",
        "volume_id",
        "chapter_range",
        "entry_state",
        "exit_state",
        "event_graph",
        "character_arcs",
        "promise_threads",
        "foreshadow_threads",
        "flex_zones",
        "lifecycle",
        "approved_by",
    }
    if not _exact_object(value, fields, "active_volume_plan", errors):
        return None
    if value.get("schema") != VOLUME_PLAN_SCHEMA:
        errors.append(f"active_volume_plan.schema must be {VOLUME_PLAN_SCHEMA}")
    volume_id = _stable_id(value.get("volume_id"), "active_volume_plan.volume_id", errors)
    if volume_id and volume_id not in skeleton_ids:
        errors.append("active_volume_plan.volume_id must reference volume_skeletons")
    chapter_range = _chapter_range(
        value.get("chapter_range"), "active_volume_plan.chapter_range", errors
    )
    for field in ("entry_state", "exit_state"):
        _text(value.get(field), f"active_volume_plan.{field}", errors)
    for field in (
        "event_graph",
        "character_arcs",
        "foreshadow_threads",
        "flex_zones",
    ):
        _object_list(value.get(field), f"active_volume_plan.{field}", errors)
    errors.extend(
        validate_reader_promise_candidates(
            value.get("promise_threads"),
            label="active_volume_plan.promise_threads",
        )
    )
    if value.get("lifecycle") not in {
        "proposed",
        "approved",
        "active",
        "closing",
        "completed",
    }:
        errors.append("active_volume_plan.lifecycle is invalid")
    approved_by = value.get("approved_by")
    if value.get("lifecycle") == "proposed":
        if approved_by not in {None, ""}:
            errors.append("proposed active_volume_plan must not prefill approved_by")
    elif approved_by != "human":
        errors.append("non-proposed active_volume_plan requires approved_by=human")
    return chapter_range


def validate_rolling_window(
    value: Any,
    errors: list[str],
    *,
    active_volume_range: tuple[int, int] | None,
) -> tuple[int, int] | None:
    fields = {
        "schema",
        "window_id",
        "start_chapter",
        "end_chapter",
        "tiers",
        "basis_refs",
        "basis_sha256",
    }
    if not _exact_object(value, fields, "rolling_window", errors):
        return None
    if value.get("schema") != ROLLING_WINDOW_SCHEMA:
        errors.append(f"rolling_window.schema must be {ROLLING_WINDOW_SCHEMA}")
    _stable_id(value.get("window_id"), "rolling_window.window_id", errors)
    start = _positive_int(value.get("start_chapter"), "rolling_window.start_chapter", errors)
    end = _positive_int(value.get("end_chapter"), "rolling_window.end_chapter", errors)
    if start and end:
        if end < start or end - start + 1 > 20:
            errors.append("rolling_window must be a positive window of at most 20 chapters")
        if active_volume_range and not (
            active_volume_range[0] <= start <= end <= active_volume_range[1]
        ):
            errors.append("rolling_window must stay inside active_volume_plan.chapter_range")
    tiers = value.get("tiers")
    if not isinstance(tiers, dict) or set(tiers) != set(WINDOW_TIERS):
        errors.append("rolling_window.tiers must contain exactly firm, directional, horizon")
    elif start and end:
        expected = {
            "firm": (start, min(start + 2, end)),
            "directional": (start + 3, min(start + 9, end)),
            "horizon": (start + 10, min(start + 19, end)),
        }
        for tier, (expected_start, expected_end) in expected.items():
            raw = tiers[tier]
            if expected_start > end:
                if raw not in {None, ""}:
                    errors.append(f"rolling_window.tiers.{tier} must be null outside the window")
                continue
            actual = _chapter_range(raw, f"rolling_window.tiers.{tier}", errors)
            if actual and actual != (expected_start, expected_end):
                errors.append(
                    f"rolling_window.tiers.{tier} must be [{expected_start}, {expected_end}]"
                )
    _string_list(value.get("basis_refs"), "rolling_window.basis_refs", errors, non_empty=True)
    _hash(value.get("basis_sha256"), "rolling_window.basis_sha256", errors)
    return (start, end) if start and end and end >= start else None


def validate_semantic_obligation(
    value: Any,
    errors: list[str],
    prefix: str,
) -> str:
    fields = {
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
    }
    if not _exact_object(value, fields, prefix, errors):
        return ""
    if value.get("schema") != SEMANTIC_OBLIGATION_SCHEMA:
        errors.append(f"{prefix}.schema must be {SEMANTIC_OBLIGATION_SCHEMA}")
    obligation_id = _stable_id(value.get("obligation_id"), f"{prefix}.obligation_id", errors)
    if value.get("domain") not in SEMANTIC_DOMAINS:
        errors.append(f"{prefix}.domain is invalid")
    for field in (
        "subject_refs",
        "prior_state_refs",
        "protected_invariants",
        "dependency_refs",
    ):
        _string_list(value.get(field), f"{prefix}.{field}", errors)
    _object_list(value.get("preconditions"), f"{prefix}.preconditions", errors)
    for field in ("intended_change", "reader_value", "evidence_requirement"):
        _text(value.get(field), f"{prefix}.{field}", errors)
    return obligation_id


def validate_chapter_forecast(
    value: Any,
    errors: list[str],
    prefix: str,
    *,
    obligation_ids: set[str],
) -> int:
    fields = {
        "schema",
        "forecast_id",
        "chapter_number",
        "tier",
        "chapter_duty",
        "likely_change",
        "reader_value",
        "obligation_refs",
        "flexibility",
    }
    if not _exact_object(value, fields, prefix, errors):
        return 0
    if value.get("schema") != CHAPTER_FORECAST_SCHEMA:
        errors.append(f"{prefix}.schema must be {CHAPTER_FORECAST_SCHEMA}")
    _stable_id(value.get("forecast_id"), f"{prefix}.forecast_id", errors)
    chapter = _positive_int(value.get("chapter_number"), f"{prefix}.chapter_number", errors)
    if value.get("tier") not in WINDOW_TIERS:
        errors.append(f"{prefix}.tier is invalid")
    for field in ("chapter_duty", "likely_change", "reader_value", "flexibility"):
        _text(value.get(field), f"{prefix}.{field}", errors)
    refs = _string_list(value.get("obligation_refs"), f"{prefix}.obligation_refs", errors)
    missing = sorted(set(refs) - obligation_ids)
    if missing:
        errors.append(f"{prefix}.obligation_refs are unresolved: {', '.join(missing)}")
    return chapter


def validate_plot_node_table(
    value: Any,
    errors: list[str],
    prefix: str,
    *,
    obligation_ids: set[str],
) -> int:
    fields = {
        "schema",
        "table_id",
        "chapter_number",
        "forecast_ref",
        "nodes",
        "candidate_sha256",
    }
    if not _exact_object(value, fields, prefix, errors):
        return 0
    if value.get("schema") != PLOT_NODE_TABLE_SCHEMA:
        errors.append(f"{prefix}.schema must be {PLOT_NODE_TABLE_SCHEMA}")
    _stable_id(value.get("table_id"), f"{prefix}.table_id", errors)
    chapter = _positive_int(value.get("chapter_number"), f"{prefix}.chapter_number", errors)
    _stable_id(value.get("forecast_ref"), f"{prefix}.forecast_ref", errors)
    _hash(value.get("candidate_sha256"), f"{prefix}.candidate_sha256", errors)
    nodes = _list(value.get("nodes"), f"{prefix}.nodes", errors, non_empty=True)
    node_ids: set[str] = set()
    scene_order: dict[str, int] = {}
    for index, node in enumerate(nodes):
        node_id, scene_id, sequence, dependencies = validate_plot_node(
            node,
            errors,
            f"{prefix}.nodes[{index}]",
            chapter_number=chapter,
            obligation_ids=obligation_ids,
        )
        if node_id:
            if node_id in node_ids:
                errors.append(f"{prefix} has duplicate node_id: {node_id}")
            unresolved = [item for item in dependencies if item not in node_ids]
            if unresolved:
                errors.append(
                    f"{prefix}.nodes[{index}].dependency_refs must reference earlier nodes: "
                    + ", ".join(unresolved)
                )
            node_ids.add(node_id)
        if scene_id and sequence:
            previous = scene_order.get(scene_id, 0)
            if sequence != previous + 1:
                errors.append(
                    f"{prefix}.nodes[{index}].sequence must be contiguous inside scene {scene_id}"
                )
            scene_order[scene_id] = sequence
    return chapter


def validate_plot_node(
    value: Any,
    errors: list[str],
    prefix: str,
    *,
    chapter_number: int,
    obligation_ids: set[str],
) -> tuple[str, str, int, list[str]]:
    fields = {
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
    }
    if not _exact_object(value, fields, prefix, errors):
        return "", "", 0, []
    if value.get("schema") != PLOT_NODE_SCHEMA:
        errors.append(f"{prefix}.schema must be {PLOT_NODE_SCHEMA}")
    node_id = _stable_id(value.get("node_id"), f"{prefix}.node_id", errors)
    if value.get("chapter_number") != chapter_number:
        errors.append(f"{prefix}.chapter_number must match its table")
    scene_id = _stable_id(value.get("scene_id"), f"{prefix}.scene_id", errors)
    sequence = _positive_int(value.get("sequence"), f"{prefix}.sequence", errors)
    _string_list(value.get("actors"), f"{prefix}.actors", errors, non_empty=True)
    for field in (
        "location_ref",
        "dramatic_function",
        "action_or_exchange",
        "reader_effect",
        "allowed_deviation",
    ):
        _text(value.get(field), f"{prefix}.{field}", errors)
    preconditions = _list(value.get("preconditions"), f"{prefix}.preconditions", errors)
    for index, precondition in enumerate(preconditions):
        item_prefix = f"{prefix}.preconditions[{index}]"
        if not _exact_object(precondition, {"type", "ref", "requirement"}, item_prefix, errors):
            continue
        if precondition.get("type") not in PRECONDITION_TYPES:
            errors.append(f"{item_prefix}.type is invalid")
        _text(precondition.get("ref"), f"{item_prefix}.ref", errors)
        _text(precondition.get("requirement"), f"{item_prefix}.requirement", errors)
    dependencies = _string_list(
        value.get("dependency_refs"), f"{prefix}.dependency_refs", errors
    )
    obligation_refs = _string_list(
        value.get("obligation_refs"), f"{prefix}.obligation_refs", errors
    )
    missing = sorted(set(obligation_refs) - obligation_ids)
    if missing:
        errors.append(f"{prefix}.obligation_refs are unresolved: {', '.join(missing)}")
    _object_list(value.get("expected_changes"), f"{prefix}.expected_changes", errors)
    if value.get("requirement") not in NODE_REQUIREMENTS:
        errors.append(f"{prefix}.requirement is invalid")
    condition = value.get("condition")
    if value.get("requirement") == "conditional":
        _text(condition, f"{prefix}.condition", errors)
    elif condition not in {None, ""}:
        errors.append(f"{prefix}.condition is only allowed for conditional nodes")
    if value.get("node_kind") not in NODE_KINDS:
        errors.append(f"{prefix}.node_kind is invalid")
    _string_list(
        value.get("protected_invariants"),
        f"{prefix}.protected_invariants",
        errors,
    )
    if value.get("human_decision") is not None:
        errors.append(f"{prefix}.human_decision is CLI-owned and must be null in a candidate")
    return node_id, scene_id, sequence, dependencies


def _result(payload: Any, errors: list[str], warnings: list[str]) -> StructuralValidation:
    subject_schema = str(payload.get("schema") or "") if isinstance(payload, dict) else ""
    return StructuralValidation(
        ok=not errors,
        errors=tuple(errors),
        warnings=tuple(warnings),
        subject_schema=subject_schema,
        subject_sha256=canonical_json_hash(payload),
    )


def _exact_object(value: Any, fields: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    actual = set(value)
    if actual != fields:
        missing = sorted(fields - actual)
        extra = sorted(actual - fields)
        if missing:
            errors.append(f"{label} is missing fields: {', '.join(missing)}")
        if extra:
            errors.append(f"{label} has unknown fields: {', '.join(extra)}")
        return False
    return True


def _stable_id(value: Any, label: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not STABLE_ID.fullmatch(value):
        errors.append(f"{label} must be a stable lowercase ID")
        return ""
    return value


def _hash(value: Any, label: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        errors.append(f"{label} must be a lowercase SHA-256")
        return ""
    return value


def _text(value: Any, label: str, errors: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must be non-empty text")
        return ""
    return value.strip()


def _positive_int(value: Any, label: str, errors: list[str]) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        errors.append(f"{label} must be a positive integer")
        return 0
    return value


def _list(
    value: Any,
    label: str,
    errors: list[str],
    *,
    non_empty: bool = False,
) -> list[Any]:
    if not isinstance(value, list):
        errors.append(f"{label} must be a list")
        return []
    if non_empty and not value:
        errors.append(f"{label} must not be empty")
    return value


def _string_list(
    value: Any,
    label: str,
    errors: list[str],
    *,
    non_empty: bool = False,
) -> list[str]:
    values = _list(value, label, errors, non_empty=non_empty)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        errors.append(f"{label} must contain non-empty strings")
        return []
    if len(values) != len(set(values)):
        errors.append(f"{label} must not contain duplicates")
    return values


def _object_list(value: Any, label: str, errors: list[str]) -> list[dict[str, Any]]:
    values = _list(value, label, errors)
    if any(not isinstance(item, dict) for item in values):
        errors.append(f"{label} must contain objects")
        return []
    return values


def _chapter_range(
    value: Any,
    label: str,
    errors: list[str],
) -> tuple[int, int] | None:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in value)
        or value[1] < value[0]
    ):
        errors.append(f"{label} must be [positive_start, positive_end]")
        return None
    return value[0], value[1]


def _range_difference(expected: set[int], actual: set[int]) -> str:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    parts = []
    if missing:
        parts.append("missing=" + ",".join(map(str, missing)))
    if extra:
        parts.append("extra=" + ",".join(map(str, extra)))
    return "; ".join(parts) or "no difference"


def referenced_ids(values: Iterable[dict[str, Any]], field: str) -> set[str]:
    """Return explicit references without guessing from prose or lexical markers."""

    return {
        str(item)
        for value in values
        if isinstance(value, dict)
        for item in value.get(field, [])
        if isinstance(item, str) and item
    }
