import json
from hashlib import sha256

import pytest

from longform_engine.author_voice import AuthorVoiceError, approve_author_voice_edit_pair
from longform_engine.creative import humanize_check, humanize_task
from longform_engine.human_author_revision import (
    create_human_author_revision_task,
    human_author_revision_status,
    validate_human_author_revision,
)
from longform_engine.human_story_review import (
    HumanStoryReviewError,
    apply_human_story_review,
    create_human_story_review_task,
)
from longform_engine.orchestration import WorkflowError, finalize_chapter, submit_agent_draft
from longform_engine.publication import (
    creation_provenance_manifest,
    publication_preflight,
    publication_preflight_status,
    publication_risk_report,
)
from longform_engine.quality.status import quality_status
from longform_engine.review_server import review_page_html
from longform_engine.semantic import chapter_close, semantic_apply
from tests.project_fixtures import approve_author_voice_fixture, prepare_unified_semantic_bundle
from tests.test_humanizer_semantic_review import validate_humanizer_output, write_semantic_result
from tests.test_story_architecture_v050 import seed_candidate, write_review


def test_human_revision_rejects_punctuation_only_changes_and_cannot_submit(tmp_path):
    config, root, _task = seed_candidate(tmp_path, complete_human=False)
    task = create_human_author_revision_task(config, chapter_number=1)
    source = (root / task.source_file).read_text(encoding="utf-8")
    candidate_text = source.replace("林迟", "林迟，", 1).replace("守门人", "守门人——", 1).strip() + "\n"
    candidate = root / task.candidate_file
    candidate.write_text(candidate_text, encoding="utf-8", newline="\n")
    record_path = root / task.record_file
    record = json.loads(record_path.read_text(encoding="utf-8"))
    before_one = source.index("林迟")
    before_two = source.index("守门人")
    after_one = candidate_text.index("林迟，")
    after_two = candidate_text.index("守门人——")
    record.update(
        {
            "revision_candidate_sha256": sha256(candidate.read_bytes()).hexdigest(),
            "impact_dimensions": ["scene_causality", "character_voice_or_emotion"],
            "changes": [
                change(
                    "punctuation-one",
                    "scene_causality",
                    source,
                    before_one,
                    len("林迟"),
                    candidate_text,
                    after_one,
                    len("林迟，"),
                ),
                change(
                    "punctuation-two",
                    "character_voice_or_emotion",
                    source,
                    before_two,
                    len("守门人"),
                    candidate_text,
                    after_two,
                    len("守门人——"),
                ),
            ],
            "protected_confirmations": {
                key: {"preserved": True, "note": f"Checked {key}."}
                for key in record["protected_confirmations"]
            },
            "human_confirmation": {
                "confirmed_by": "human",
                "statement": "I reviewed this candidate.",
            },
        }
    )
    write_json(record_path, record)

    result = validate_human_author_revision(
        config,
        chapter_number=1,
        file_path=candidate,
        record_path=record_path,
    )

    assert not result.ok
    assert "human revision cannot consist only of whitespace, formatting, or punctuation changes" in result.errors
    with pytest.raises(WorkflowError, match="human author revision is missing, invalid, or stale"):
        submit_agent_draft(
            config,
            chapter_number=1,
            file_path=candidate,
            agent="human",
            overwrite=True,
        )
    with pytest.raises(HumanStoryReviewError, match="human_author_revision_v1"):
        create_human_story_review_task(config, chapter_number=1)


def test_human_revision_rejects_crlf_to_keep_candidate_hash_identity_stable(tmp_path):
    config, root, _task = seed_candidate(tmp_path, complete_human=False)
    task = create_human_author_revision_task(config, chapter_number=1)
    source = (root / task.source_file).read_text(encoding="utf-8")
    candidate = root / task.candidate_file
    candidate.write_bytes((source.strip() + "\r\n\r\n人工补入一个承担代价的选择。\r\n").encode("utf-8"))
    record = json.loads((root / task.record_file).read_text(encoding="utf-8"))
    record["revision_candidate_sha256"] = sha256(candidate.read_bytes()).hexdigest()
    write_json(root / task.record_file, record)

    result = validate_human_author_revision(
        config,
        chapter_number=1,
        file_path=candidate,
        record_path=root / task.record_file,
    )

    assert not result.ok
    assert "human revision candidate must use LF line endings" in result.errors


def test_platform_preflight_is_officially_sourced_advisory_without_detector_claims(tmp_path):
    config, root, _task = seed_candidate(tmp_path, complete_human=False)

    qidian_result, qidian = publication_preflight(config, target="qidian_male")
    fanqie_result, fanqie = publication_preflight(config, target="fanqie_free")
    risk = publication_risk_report(config)
    _manifest_result, manifest = creation_provenance_manifest(config, target="qidian_male")
    risk_payload = json.loads((root / risk.report_file).read_text(encoding="utf-8"))
    rendered = json.dumps(
        {"qidian": qidian, "fanqie": fanqie, "risk": risk_payload, "manifest": manifest},
        ensure_ascii=False,
    ).lower()

    assert qidian_result.blocking is False
    assert fanqie_result.blocking is False
    assert qidian_result.status == "attention"
    assert any("全面 AI 禁令" in item for item in qidian["unknowns"])
    assert any("粗制滥造" in item["claim"] for item in fanqie["policy_sources"])
    assert all(item["source_url"].startswith("https://") for item in fanqie["policy_sources"])
    assert risk_payload["schema"] == "publication_risk_report_v2"
    assert manifest["schema"] == "creation_provenance_manifest_v1"
    for forbidden in (
        "ai_probability",
        "detection_passed",
        "bypass_detection",
        "human_percentage",
        "human_ratio",
    ):
        assert forbidden not in rendered
    assert publication_preflight_status(config, target="qidian_male")["report_stale"] is False

    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(draft.read_text(encoding="utf-8") + "候选变化。\n", encoding="utf-8")
    assert publication_preflight_status(config, target="qidian_male")["report_stale"] is True


def test_expired_policy_snapshot_requires_manual_verification_but_never_blocks(tmp_path, monkeypatch):
    import longform_engine.publication as publication

    config, _root, _task = seed_candidate(tmp_path, complete_human=False)
    monkeypatch.setattr(publication, "policy_record_is_stale", lambda _record: True)

    result, payload = publication_preflight(config, target="fanqie_free")

    assert result.status == "policy_verification_required"
    assert result.blocking is False
    assert payload["blocking"] is False
    assert payload["policy_snapshot"]["stale_record_ids"]


def test_quality_status_reports_revision_coverage_and_nonblocking_platform_states(tmp_path):
    config, _root, _task = seed_candidate(tmp_path)

    payload = quality_status(config)

    assert payload["schema"] == "quality_status_v2"
    assert payload["human_author_revision_coverage"]["complete"] is False
    assert set(payload["platform_preflights"]) == {"qidian_male", "fanqie_free"}
    assert all(item["blocking"] is False for item in payload["platform_preflights"].values())
    assert all(
        item["human_revision_coverage"]["complete"] is True
        for item in payload["platform_preflights"].values()
    )
    assert payload["literary_evidence_ready"] is False


def test_review_desk_has_no_prefilled_human_pass_reason_or_direct_repair_submit_button():
    page = review_page_html("csrf-test", csp_nonce="nonce-test")

    assert "人工逐项确认通过" not in page
    assert "转入人工修订验证" in page
    assert 'api("/api/manual-repair/submit"' not in page


def test_agent_change_after_human_revision_requires_a_new_human_phase(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    humanize_task(config, chapter_number=1, source="draft")
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    candidate.write_text(
        (root / "40_manuscript" / "draft" / "ch001.md").read_text(encoding="utf-8").strip()
        + "\n\nAgent 又调整了场内动作，因此旧人工修订不能继续证明最终候选。\n",
        encoding="utf-8",
        newline="\n",
    )
    check = humanize_check(config, chapter_number=1, file_path=candidate)
    assert check.semantic_review_required
    semantic_output = write_semantic_result(root)
    assert validate_humanizer_output(config, root, semantic_output).passed

    submitted = submit_agent_draft(
        config,
        chapter_number=1,
        file_path=candidate,
        agent="codex",
        overwrite=True,
    )

    assert submitted.passed
    assert human_author_revision_status(config, chapter_number=1)["status"] == "pending"
    with pytest.raises(HumanStoryReviewError, match="human_author_revision_v1"):
        create_human_story_review_task(config, chapter_number=1)


def test_early_chapter_voice_pair_requires_real_edit_and_explicit_limit_replacement(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    review_task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, review_task.template_file, decision="accept")
    apply_human_story_review(
        config,
        chapter_number=1,
        file_path=review,
        approved_by="human",
    )
    finalized = finalize_chapter(config, chapter_number=1, approved_by="human")
    semantic_output = prepare_unified_semantic_bundle(root, config, 1)
    semantic_apply(config, chapter_number=1, file_path=semantic_output)
    with pytest.raises(ValueError, match="chapters 1-3 require one approved"):
        chapter_close(config, chapter_number=1, approved_by="human")

    approve_author_voice_fixture(root, config, chapter_number=1)
    final = root / finalized.final_file
    finalization = json.loads(final.with_suffix(".finalization.json").read_text(encoding="utf-8"))
    validation_file = root / finalization["human_author_revision"]["validation_file"]
    pair_file = validation_file.with_name("ch001.voice_pair.json")
    base_pair = json.loads(pair_file.read_text(encoding="utf-8"))
    invalid_pair = {
        **base_pair,
        "pair_id": "invalid-nonhuman-region",
        "after": {"start": 0, "end": 5, "text": final.read_text(encoding="utf-8")[:5]},
    }
    invalid_file = validation_file.with_name("ch001.invalid_voice_pair.json")
    write_json(invalid_file, invalid_pair)
    with pytest.raises(AuthorVoiceError, match="after span must overlap"):
        approve_author_voice_edit_pair(
            config,
            chapter_number=1,
            record_path=invalid_file,
            approved_by="human",
        )

    bank_file = root / "10_bible" / "style_profiles" / "author_voice_edit_pairs.json"
    bank = json.loads(bank_file.read_text(encoding="utf-8"))
    bank["pairs"].extend(
        {
            "pair_id": f"dummy-{index}",
            "chapter_number": 99,
            "final_sha256": f"{index:064x}",
            "approved_at": "2026-08-21T00:00:00+00:00",
            "active": True,
        }
        for index in range(11)
    )
    write_json(bank_file, bank)
    replacement_pair = {**base_pair, "pair_id": "replacement-voice-pair", "replace_pair_id": ""}
    replacement_file = validation_file.with_name("ch001.replacement_voice_pair.json")
    write_json(replacement_file, replacement_pair)
    with pytest.raises(AuthorVoiceError, match="12-pair active limit"):
        approve_author_voice_edit_pair(
            config,
            chapter_number=1,
            record_path=replacement_file,
            approved_by="human",
        )
    replacement_pair["replace_pair_id"] = "dummy-0"
    write_json(replacement_file, replacement_pair)
    approved = approve_author_voice_edit_pair(
        config,
        chapter_number=1,
        record_path=replacement_file,
        approved_by="human",
    )
    assert approved.active_pairs == 12
    assert approved.replaced_pair_id == "dummy-0"
    assert chapter_close(config, chapter_number=1, approved_by="human").chapter_number == 1


def change(
    change_id,
    dimension,
    source,
    before_start,
    before_length,
    candidate,
    after_start,
    after_length,
):
    return {
        "change_id": change_id,
        "dimension": dimension,
        "before": {
            "start": before_start,
            "end": before_start + before_length,
            "text": source[before_start : before_start + before_length],
        },
        "after": {
            "start": after_start,
            "end": after_start + after_length,
            "text": candidate[after_start : after_start + after_length],
        },
        "intent": "Record an alleged revision effect for validation.",
        "must_preserve": ["chapter contract"],
    }


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
