import json
from pathlib import Path

import pytest

from longform_engine.agent_protocols import AGENT_OUTPUT_PROTOCOLS, DESIGN_REQUIRED_HEADINGS
from longform_engine.agent_tasks import load_manifest
from longform_engine.human_review_consultation import (
    HumanReviewConsultError,
    consultation_status,
    create_human_review_consult_task,
    mark_stale_human_consultations,
    record_human_review_consultation,
    validate_human_review_consultation,
)
from longform_engine.human_story_review import create_human_story_review_task
from tests.test_story_architecture_v050 import seed_candidate


def write_consult_response(path: Path) -> None:
    sections = []
    for heading in DESIGN_REQUIRED_HEADINGS["human_review_consult"]:
        sections.extend((f"## {heading}", "", "基于选中正文和冻结审稿证据给出可选建议。", ""))
    path.write_text("# 人工深审咨询\n\n" + "\n".join(sections), encoding="utf-8")


def test_consultation_uses_existing_design_protocol_and_cannot_write_canonical(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    review_task = create_human_story_review_task(config, chapter_number=1)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    text = draft.read_text(encoding="utf-8")
    before = {
        "draft": draft.read_bytes(),
        "card": (root / "20_outline" / "chapter_cards" / "ch001.json").read_bytes(),
    }

    task = create_human_review_consult_task(
        config,
        chapter_number=1,
        start=0,
        end=min(60, len(text)),
        question="这个转折是否真正由人物选择推动？",
    )
    manifest = load_manifest(root, task.task_id)
    assert len(AGENT_OUTPUT_PROTOCOLS) == 4
    assert manifest["io"]["output"]["protocol"] == "design_document_v1"
    assert manifest["role"]["id"] == "human_review_advisor"
    assert manifest["policy"]["canonical_targets"] == []
    assert manifest["policy"]["requires_human_apply"] is False
    assert review_task.review_bundle_file in [item["path"] for item in manifest["io"]["inputs"]]

    response = root / task.response_file
    write_consult_response(response)
    validated = validate_human_review_consultation(
        config,
        chapter_number=1,
        file_path=response,
    )
    assert validated.ok, validated.errors
    recorded = record_human_review_consultation(
        config,
        chapter_number=1,
        file_path=response,
    )
    record = json.loads((root / recorded.record_file).read_text(encoding="utf-8"))
    assert record["canonical_write_performed"] is False
    assert record["suggestion_conversion_required"] is True
    assert draft.read_bytes() == before["draft"]
    assert (root / "20_outline" / "chapter_cards" / "ch001.json").read_bytes() == before["card"]
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_same_candidate_reuses_session_and_candidate_change_marks_all_turns_stale(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    create_human_story_review_task(config, chapter_number=1)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    text = draft.read_text(encoding="utf-8")
    first = create_human_review_consult_task(
        config,
        chapter_number=1,
        start=0,
        end=min(40, len(text)),
        question="第一问",
    )
    first_response = root / first.response_file
    write_consult_response(first_response)
    assert validate_human_review_consultation(
        config, chapter_number=1, file_path=first_response
    ).ok
    record_human_review_consultation(config, chapter_number=1, file_path=first_response)

    second = create_human_review_consult_task(
        config,
        chapter_number=1,
        start=10,
        end=min(55, len(text)),
        question="第二问",
    )
    assert second.session_id == first.session_id
    assert second.turn_number == 2
    history = json.loads((root / second.history_file).read_text(encoding="utf-8"))
    assert history["turns"][0]["question"] == "第一问"

    draft.write_text(text + "\n候选已经变化。\n", encoding="utf-8")
    mark_stale_human_consultations(root, chapter_number=1)
    status = consultation_status(config, chapter_number=1)
    assert status["sessions"][0]["status"] == "stale"
    second_response = root / second.response_file
    write_consult_response(second_response)
    with pytest.raises(HumanReviewConsultError, match="stale"):
        record_human_review_consultation(
            config,
            chapter_number=1,
            file_path=second_response,
        )
