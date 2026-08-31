"""Internal multi-format source-library storage records and validators.

The source library is user-owned and non-canonical.  These contracts make raw
assets and derived evidence independently hashable so that a parser, OCR model
or ASR model change cannot silently alter a novel project.  Except for
``source_asset_v1``, these records are implementation projections rather than
public semantic object types; Host Agent meaning is exchanged through
``semantic_document_v1`` and ``workflow_record_v1``.
"""

from __future__ import annotations

from hashlib import sha256
import json
import re
from typing import Any

from longform_engine.semantic_protocols import (
    SEMANTIC_DOCUMENT_SCHEMA,
    WORKFLOW_RECORD_SCHEMA,
)


LIBRARY_INDEX_SCHEMA = "source_library_index_v2"
LIBRARY_WORK_SCHEMA = "source_library_work_v1"
ASSET_SCHEMA = "source_asset_v1"
LIBRARY_ITEM_SCHEMA = "source_library_item_v2"
INGEST_BATCH_SCHEMA = "source_ingest_batch_v1"
PROCESSING_JOB_SCHEMA = "source_processing_job_v1"
NORMALIZATION_SCHEMA = "source_normalization_manifest_v1"
EVIDENCE_SEGMENT_SCHEMA = "source_evidence_segment_v1"
EXTRACTION_SCHEMA = "source_extraction_candidate_v2"
REMOTE_DECISION_SCHEMA = "source_remote_processing_decision_v1"
PROVIDER_RECEIPT_SCHEMA = "source_provider_receipt_v1"

PROJECT_BINDING_SCHEMA = "fanfiction_work_binding_v2"
# Coverage and project Canon are semantic/public contracts in v0.12.  The
# source-library records above remain deterministic storage projections, not a
# closed ontology for what a work contains.
COVERAGE_SCHEMA = WORKFLOW_RECORD_SCHEMA
CANON_SCHEMA = SEMANTIC_DOCUMENT_SCHEMA

ACTIVE_LOCATOR_KINDS = frozenset(
    {
        "text_span",
        "structured_path",
        "document_block",
        "image_region",
        "subtitle_cue",
        "audio_time_range",
        "video_time_range",
    }
)

STORAGE_MODES = frozenset({"managed_copy", "external_reference"})
PROCESSING_EXECUTIONS = frozenset({"local", "openai"})
PROCESSING_STATES = frozenset(
    {
        "staged",
        "awaiting_import_approval",
        "source_fixed",
        "processing_pending",
        "processing",
        "evidence_review_pending",
        "evidence_ready",
        "extraction_pending",
        "extraction_approved",
        "project_bound",
        "project_canon_approved",
        "capability_missing",
        "encoding_confirmation_required",
        "encrypted_source",
        "source_corrupt",
        "partial_parse",
        "ocr_confidence_low",
        "asr_confidence_low",
        "reading_order_required",
        "speaker_mapping_required",
        "remote_approval_required",
        "source_hash_drift",
        "processor_stale",
        "unsupported_processor",
        "failed",
        "cancelled",
    }
)


def canonical_json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def stable_id(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}", str(value or "")))


def validate_origin_locator(locator: Any) -> list[str]:
    """Validate the tagged locator shared by every evidence-bearing format."""

    if not isinstance(locator, dict):
        return ["origin_locator must be an object"]
    kind = str(locator.get("kind") or "")
    if kind not in ACTIVE_LOCATOR_KINDS:
        return [f"origin_locator.kind must be one of: {', '.join(sorted(ACTIVE_LOCATOR_KINDS))}"]
    asset_id = str(locator.get("asset_id") or "")
    if not stable_id(asset_id):
        return ["origin_locator.asset_id must be a stable id"]

    errors: list[str] = []
    if kind == "text_span":
        _validate_int_range(locator, "start", "end", errors)
        _validate_optional_positive_int(locator, "start_line", errors)
        _validate_optional_positive_int(locator, "end_line", errors)
    elif kind == "structured_path":
        if not str(locator.get("path") or "").strip():
            errors.append("structured_path.path is required")
        _validate_optional_int_range(locator, "start", "end", errors)
    elif kind == "document_block":
        _validate_positive_int(locator, "page", errors)
        if not str(locator.get("block_id") or "").strip():
            errors.append("document_block.block_id is required")
        _validate_optional_bbox(locator.get("bbox"), errors)
    elif kind == "image_region":
        _validate_optional_positive_int(locator, "page", errors)
        _validate_optional_bbox(locator.get("bbox"), errors, required=True)
        crop_sha = str(locator.get("crop_sha256") or "")
        if crop_sha and not re.fullmatch(r"[0-9a-f]{64}", crop_sha):
            errors.append("image_region.crop_sha256 must be a SHA-256 hex digest")
    elif kind == "subtitle_cue":
        _validate_positive_int(locator, "cue_index", errors, allow_zero=True)
        _validate_millisecond_range(locator, errors)
    elif kind == "audio_time_range":
        _validate_millisecond_range(locator, errors)
        segment_index = locator.get("segment_index")
        if not isinstance(segment_index, int) or isinstance(segment_index, bool) or segment_index < 0:
            errors.append("audio_time_range.segment_index must be a non-negative integer")
    elif kind == "video_time_range":
        _validate_millisecond_range(locator, errors)
        stream_index = locator.get("stream_index")
        if not isinstance(stream_index, int) or isinstance(stream_index, bool) or stream_index < 0:
            errors.append("video_time_range.stream_index must be a non-negative integer")
        frame_hashes = locator.get("frame_sha256")
        if not isinstance(frame_hashes, list) or any(
            not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item)
            for item in frame_hashes
        ):
            errors.append("video_time_range.frame_sha256 must be a list of SHA-256 hex digests")
    return errors


def validate_evidence_segment(segment: Any) -> list[str]:
    required = {
        "schema",
        "segment_id",
        "normalized_text",
        "text_sha256",
        "origin_locator",
        "derivation",
        "review_status",
    }
    if not isinstance(segment, dict) or set(segment) != required:
        return [f"evidence segment must contain exactly: {', '.join(sorted(required))}"]
    errors: list[str] = []
    if segment.get("schema") != EVIDENCE_SEGMENT_SCHEMA:
        errors.append(f"segment schema must be {EVIDENCE_SEGMENT_SCHEMA}")
    if not stable_id(segment.get("segment_id")):
        errors.append("segment_id must be stable")
    text = segment.get("normalized_text")
    if not isinstance(text, str):
        errors.append("normalized_text must be a string")
        text = ""
    if sha256(text.encode("utf-8")).hexdigest() != segment.get("text_sha256"):
        errors.append("text_sha256 does not match normalized_text")
    errors.extend(validate_origin_locator(segment.get("origin_locator")))
    derivation = segment.get("derivation")
    if not isinstance(derivation, dict) or not str(derivation.get("processor") or "").strip():
        errors.append("derivation must identify a processor")
    elif not str(derivation.get("processor_version") or "").strip():
        errors.append("derivation.processor_version is required")
    config_sha = str(derivation.get("config_sha256") or "") if isinstance(derivation, dict) else ""
    if not re.fullmatch(r"[0-9a-f]{64}", config_sha):
        errors.append("derivation.config_sha256 must be a SHA-256 hex digest")
    confidence = derivation.get("confidence") if isinstance(derivation, dict) else None
    if confidence is not None and (
        not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not 0 <= float(confidence) <= 1
    ):
        errors.append("derivation.confidence must be null or a number between 0 and 1")
    if segment.get("review_status") not in {"source_exact", "pending_human", "approved", "rejected"}:
        errors.append("review_status must be source_exact, pending_human, approved, or rejected")
    return errors


def _validate_int_range(value: dict[str, Any], start_key: str, end_key: str, errors: list[str]) -> None:
    start, end = value.get(start_key), value.get(end_key)
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
    ):
        errors.append(f"{start_key}/{end_key} must be a valid non-empty integer range")


def _validate_optional_int_range(
    value: dict[str, Any], start_key: str, end_key: str, errors: list[str]
) -> None:
    if start_key not in value and end_key not in value:
        return
    _validate_int_range(value, start_key, end_key, errors)


def _validate_positive_int(
    value: dict[str, Any], key: str, errors: list[str], *, allow_zero: bool = False
) -> None:
    item = value.get(key)
    minimum = 0 if allow_zero else 1
    if not isinstance(item, int) or isinstance(item, bool) or item < minimum:
        errors.append(f"{key} must be an integer >= {minimum}")


def _validate_optional_positive_int(value: dict[str, Any], key: str, errors: list[str]) -> None:
    if key in value:
        _validate_positive_int(value, key, errors)


def _validate_optional_bbox(value: Any, errors: list[str], *, required: bool = False) -> None:
    if value is None and not required:
        return
    if (
        not isinstance(value, list)
        or len(value) != 4
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value)
        or any(not 0 <= float(item) <= 1 for item in value)
        or float(value[2]) <= float(value[0])
        or float(value[3]) <= float(value[1])
    ):
        errors.append("bbox must be normalized [left, top, right, bottom]")


def _validate_millisecond_range(value: dict[str, Any], errors: list[str]) -> None:
    start, end = value.get("start_ms"), value.get("end_ms")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
    ):
        errors.append("start_ms/end_ms must be a valid non-empty millisecond range")


__all__ = [
    "ACTIVE_LOCATOR_KINDS",
    "ASSET_SCHEMA",
    "CANON_SCHEMA",
    "COVERAGE_SCHEMA",
    "EVIDENCE_SEGMENT_SCHEMA",
    "EXTRACTION_SCHEMA",
    "INGEST_BATCH_SCHEMA",
    "LIBRARY_INDEX_SCHEMA",
    "LIBRARY_ITEM_SCHEMA",
    "LIBRARY_WORK_SCHEMA",
    "NORMALIZATION_SCHEMA",
    "PROCESSING_EXECUTIONS",
    "PROCESSING_JOB_SCHEMA",
    "PROCESSING_STATES",
    "PROJECT_BINDING_SCHEMA",
    "PROVIDER_RECEIPT_SCHEMA",
    "REMOTE_DECISION_SCHEMA",
    "STORAGE_MODES",
    "canonical_json_hash",
    "stable_id",
    "validate_evidence_segment",
    "validate_origin_locator",
]
