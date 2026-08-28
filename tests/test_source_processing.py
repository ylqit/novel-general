import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.fanfiction_sources import (
    create_source_processing_job,
    import_source_item,
    library_item,
    register_source_work,
    run_source_processing_job,
    source_evidence_preview,
    source_processing_capabilities,
)
from longform_engine.source_processing import (
    SourceProcessingError,
    approve_remote_decision,
    create_processing_job_payload,
    create_remote_decision_payload,
    detect_asset_format,
)
from longform_engine.source_protocols import (
    EVIDENCE_SEGMENT_SCHEMA,
    canonical_json_hash,
    validate_evidence_segment,
    validate_origin_locator,
)


def _work(tmp_path: Path, monkeypatch) -> dict:
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "原著资料库").resolve()))
    return register_source_work(
        name="格式测试作品",
        creator="测试作者",
        versions=["测试版"],
        approved_by="human",
    )


def _import_and_process(
    tmp_path: Path,
    monkeypatch,
    *,
    filename: str,
    content: bytes,
    parameters: dict | None = None,
) -> tuple[dict, dict]:
    work = _work(tmp_path, monkeypatch)
    source = tmp_path / filename
    source.write_bytes(content)
    item = import_source_item(
        work_id=work["work_id"],
        name=filename,
        source_type="格式测试",
        version="测试版",
        unit_range="测试单元",
        source_method="用户本地导入",
        rights_status="public_domain_claimed",
        retention_mode="full_text",
        approved_by="human",
        file_path=source,
    )
    job = create_source_processing_job(item_id=item["item_id"], parameters=parameters)
    result = run_source_processing_job(item_id=item["item_id"], job_id=job["job_id"])
    return library_item(item["item_id"]), result


@pytest.mark.parametrize(
    "locator",
    [
        {"kind": "text_span", "asset_id": "asset_test", "start": 0, "end": 3, "start_line": 1, "end_line": 1},
        {"kind": "structured_path", "asset_id": "asset_test", "path": "/人物/0"},
        {"kind": "document_block", "asset_id": "asset_test", "page": 1, "block_id": "p1:b1", "bbox": [0.1, 0.1, 0.9, 0.3]},
        {"kind": "image_region", "asset_id": "asset_test", "page": 2, "bbox": [0.0, 0.0, 1.0, 1.0], "crop_sha256": "a" * 64},
        {"kind": "subtitle_cue", "asset_id": "asset_test", "cue_index": 0, "start_ms": 100, "end_ms": 900},
        {"kind": "audio_time_range", "asset_id": "asset_test", "segment_index": 0, "start_ms": 100, "end_ms": 900},
        {"kind": "video_time_range", "asset_id": "asset_test", "stream_index": 0, "start_ms": 100, "end_ms": 900, "frame_sha256": ["b" * 64]},
    ],
)
def test_final_multimedia_origin_locators_are_valid(locator):
    assert validate_origin_locator(locator) == []
    text = "可回溯证据"
    segment = {
        "schema": EVIDENCE_SEGMENT_SCHEMA,
        "segment_id": "seg_test",
        "normalized_text": text,
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "origin_locator": locator,
        "derivation": {
            "processor": "test_processor",
            "processor_version": "1",
            "config_sha256": canonical_json_hash({}),
            "confidence": None,
        },
        "review_status": "pending_human",
    }
    assert validate_evidence_segment(segment) == []


def test_markdown_normalizes_newlines_and_preserves_line_coordinates(tmp_path, monkeypatch):
    item, result = _import_and_process(
        tmp_path,
        monkeypatch,
        filename="设定.md",
        content="# 规则\r\n\r\n第一条规则。\r\n第二行。".encode(),
    )
    assert result["status"] == "evidence_ready"
    assert item["processing_status"] == "evidence_ready"
    preview = source_evidence_preview(item["item_id"])
    assert [entry["origin_locator"]["kind"] for entry in preview["segments"]] == [
        "text_span",
        "text_span",
    ]
    assert preview["segments"][1]["origin_locator"]["start_line"] == 3
    assert preview["full_normalized_text_exposed"] is False


def test_json_duplicate_keys_are_a_blocking_source_corrupt_diagnostic(tmp_path, monkeypatch):
    item, result = _import_and_process(
        tmp_path,
        monkeypatch,
        filename="重复.json",
        content=b'{"name":"first","name":"second"}',
    )
    assert result["status"] == "source_corrupt"
    assert item["processing_status"] == "source_corrupt"
    assert any(
        diagnostic["code"] == "source_corrupt" and "duplicate JSON key" in diagnostic["message"]
        for diagnostic in result["diagnostics"]
    )


def test_gb18030_requires_per_job_human_confirmation(tmp_path, monkeypatch):
    content = "人物关系需要人工确认。".encode("gb18030")
    item, result = _import_and_process(
        tmp_path,
        monkeypatch,
        filename="旧编码.txt",
        content=content,
        parameters={"encoding": "gb18030"},
    )
    assert result["status"] == "encoding_confirmation_required"
    job = create_source_processing_job(
        item_id=item["item_id"],
        parameters={"encoding": "gb18030", "encoding_approved_by": "human"},
    )
    approved = run_source_processing_job(item_id=item["item_id"], job_id=job["job_id"])
    assert approved["status"] == "evidence_ready"
    assert source_evidence_preview(item["item_id"])["segments"][0]["normalized_text"] == "人物关系需要人工确认。"


def test_content_signature_mismatch_is_not_decoded_as_text(tmp_path, monkeypatch):
    item, result = _import_and_process(
        tmp_path,
        monkeypatch,
        filename="伪装.txt",
        content=b"%PDF-1.7\nnot-a-text-source",
    )
    assert detect_asset_format(tmp_path / "伪装.txt")["suffix_matches"] == "false"
    assert result["status"] == "source_corrupt"
    assert source_evidence_preview(item["item_id"])["segments"] == []
    assert any(value["code"] == "mime_extension_mismatch" for value in result["diagnostics"])


def test_capability_registry_declares_every_release_phase_without_downloads():
    payload = source_processing_capabilities()
    processors = {entry["processor_id"]: entry for entry in payload["processors"]}
    assert {
        "plain_text_v1",
        "structured_data_v1",
        "pdf_text_v1",
        "pdf_render_v1",
        "docx_blocks_v1",
        "image_ocr_tesseract_v1",
        "subtitle_cues_v1",
        "audio_asr_faster_whisper_v1",
        "video_timeline_ffmpeg_v1",
        "openai_source_preprocess_v1",
    } <= set(processors)
    assert all(entry["max_input_bytes"] > 0 for entry in processors.values())
    assert all(entry["accepted_input_states"] for entry in processors.values())
    assert payload["network_download_performed"] is False


def test_remote_scope_requires_explicit_versioned_model_and_current_hash():
    item = {
        "item_id": "item_test",
        "bundle_sha256": "a" * 64,
        "assets": [
            {
                "asset_id": "asset_image",
                "media_type": "image/png",
                "sha256": "b" * 64,
                "size_bytes": 1200,
            }
        ],
    }
    job = create_processing_job_payload(
        item_id="item_test",
        bundle_sha256="a" * 64,
        asset_ids=["asset_image"],
        execution="openai",
        parameters={"purpose": "bounded_visual_observation"},
    )
    scope = [{"asset_id": "asset_image", "bbox": [0.0, 0.0, 1.0, 1.0]}]
    with pytest.raises(SourceProcessingError, match="latest"):
        create_remote_decision_payload(
            job=job,
            item=item,
            provider="openai",
            model="vision-latest",
            scopes=scope,
        )
    pending = create_remote_decision_payload(
        job=job,
        item=item,
        provider="openai",
        model="explicit-model-2026-01-01",
        scopes=scope,
    )
    assert pending["network_performed"] is False
    assert pending["job_sha256"] == canonical_json_hash(job)
    assert approve_remote_decision(pending, approved_by="human")["status"] == "approved"
    stale = json.loads(json.dumps(pending))
    stale["scopes"][0]["bbox"] = [0.1, 0.1, 0.9, 0.9]
    with pytest.raises(SourceProcessingError, match="stale"):
        approve_remote_decision(stale, approved_by="human")

    local_job = create_processing_job_payload(
        item_id="item_test",
        bundle_sha256="a" * 64,
        asset_ids=["asset_image"],
        execution="local",
    )
    with pytest.raises(SourceProcessingError, match="OpenAI source-processing job"):
        create_remote_decision_payload(
            job=local_job,
            item=item,
            provider="openai",
            model="explicit-model-2026-01-01",
            scopes=scope,
        )
