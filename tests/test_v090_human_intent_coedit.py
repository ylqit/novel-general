from hashlib import sha256
import json
from pathlib import Path

import pytest

from longform_engine.agent_protocols import DESIGN_REQUIRED_HEADINGS
from longform_engine.chapter_coedit import (
    ChapterCoeditError,
    build_text_anchor,
    create_chapter_coedit_rewrite_task,
    create_chapter_coedit_turn,
    record_chapter_coedit_response,
    validate_chapter_coedit_candidate,
    validate_chapter_coedit_response,
)
from longform_engine.review_server import review_page_html
from longform_engine.config import load_project_config
from longform_engine.human_chapter_intent import (
    apply_human_chapter_intent,
    create_human_chapter_intent_task,
    validate_human_chapter_intent,
)
from longform_engine.orchestration import continue_write, open_book
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready, update_chapter_contract_fixture


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    return config, root


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def chapter_text(extra: str = "") -> str:
    sentence = "林迟沿着山门石阶向上，钟声把退路一层层压窄，他把唯一的路图递给阿岚，先承认自己害怕独自决定。"
    return "# 第一章 山门\n\n" + sentence * 70 + f"\n\n{extra or '阿岚接过路图，却没有立刻答应。'}\n"


def write_advisor_response(path: Path) -> None:
    sections: list[str] = ["# 人类主导共编方案", ""]
    for index, heading in enumerate(DESIGN_REQUIRED_HEADINGS["human_review_consult"]):
        sections.extend((f"## {heading}", "", "围绕人物选择、读者影响和保护项给出可执行判断。", ""))
        if index == 0:
            sections.extend(
                (
                    "### OPTION-A",
                    "让主角先交出路图，再用沉默承担失控感；收益是选择更可见，风险是节奏放慢。",
                    "",
                    "### OPTION-B",
                    "让配角拒收路图，迫使主角说明真实恐惧；收益是关系压力更强，风险是冲突升级。",
                    "",
                )
            )
    path.write_text("\n".join(sections), encoding="utf-8")


def test_human_intent_form_is_blank_and_stale_contract_rebuilds_it(tmp_path: Path):
    config, root = seed_project(tmp_path)
    task = create_human_chapter_intent_task(config, chapter_number=1)
    candidate = root / task.candidate_file
    payload = read_json(candidate)
    for field in ("story_intent", "key_character_choice", "emotional_truth", "pov_voice_intent"):
        assert payload[field] == ""
    assert payload["protected_items"] == []

    payload.update(
        {
            "story_intent": "让信任第一次成为会失去控制权的具体行动。",
            "key_character_choice": "林迟把唯一的路图交给阿岚，而不是继续独占决定。",
            "emotional_truth": "信任在成为同盟之前，首先像一次失控。",
            "pov_voice_intent": "先辨认可见证据，再用缩短的句子承认恐惧。",
            "protected_items": ["守门人的真实身份本章不能揭晓。"],
            "completed_by": "human",
        }
    )
    write_json(candidate, payload)
    validated = validate_human_chapter_intent(
        config, chapter_number=1, file_path=candidate
    )
    assert validated.ok, validated.errors
    applied = apply_human_chapter_intent(
        config, chapter_number=1, file_path=candidate, approved_by="human"
    )
    assert (root / applied.intent_file).is_file()

    current = read_json(root / "20_outline" / "chapter_contracts" / "ch001.json")
    aftermath = dict(current["aftermath"])
    aftermath["description"] = "交出路图后，胜利感被失控的羞耻压住。"
    contract = update_chapter_contract_fixture(root, 1, aftermath=aftermath)

    rebuilt = create_human_chapter_intent_task(config, chapter_number=1)
    rebuilt_payload = read_json(root / rebuilt.candidate_file)
    assert rebuilt_payload["chapter_contract_sha256"] == contract["chapter_contract_hash"]
    assert rebuilt_payload["story_intent"] == ""
    assert rebuilt_payload["completed_by"] == ""


def test_coedit_records_human_option_and_only_creates_complete_workbench_candidate(
    tmp_path: Path,
):
    config, root = seed_project(tmp_path)
    writing = continue_write(config, chapter_number=1)
    source = root / writing.recommended_agent_draft
    source.write_text(chapter_text(), encoding="utf-8")
    final_before = root / "40_manuscript" / "final" / "ch001.md"

    turn = create_chapter_coedit_turn(
        config,
        chapter_number=1,
        start=0,
        end=24,
        question="如何让交出路图成为人物选择，而不是剧情搬运？",
    )
    response = root / turn.response_file
    task_text = (root / turn.task_file).read_text(encoding="utf-8")
    for heading in DESIGN_REQUIRED_HEADINGS["human_review_consult"]:
        assert heading in task_text
    write_advisor_response(response)
    response_validation = validate_chapter_coedit_response(
        config, chapter_number=1, file_path=response
    )
    assert response_validation.ok, response_validation.errors
    recorded = record_chapter_coedit_response(
        config, chapter_number=1, file_path=response
    )
    assert recorded.option_ids == ("OPTION-A", "OPTION-B")

    second_turn = create_chapter_coedit_turn(
        config, chapter_number=1, start=0, end=24, question="承接上一轮，把关系边界说明清楚。",
    )
    second_response = root / second_turn.response_file
    write_advisor_response(second_response)
    assert validate_chapter_coedit_response(config, chapter_number=1, file_path=second_response).ok
    record_chapter_coedit_response(config, chapter_number=1, file_path=second_response)

    rewrite = create_chapter_coedit_rewrite_task(
        config,
        chapter_number=1,
        session_id=turn.session_id,
        turn_number=turn.turn_number,
        option_id="OPTION-B",
        adjustment="保留守门人身份悬念。",
    )
    replacement = root / rewrite.candidate_file
    replacement.write_bytes(
        chapter_text("阿岚拒收路图，逼他先说出害怕失去什么。").encode("utf-8")
    )
    candidate_validation = validate_chapter_coedit_candidate(
        config, chapter_number=1, file_path=replacement
    )
    assert candidate_validation.ok, candidate_validation.errors
    assert replacement.is_relative_to(root / "50_workbench")
    assert not final_before.exists()
    assert not (root / "40_manuscript" / "draft" / "ch001.md").exists()

    # A different answer about the original draft must not rewrite over the new
    # current candidate, even though its response and session are still intact.
    with pytest.raises(ChapterCoeditError, match="stale for the current candidate"):
        create_chapter_coedit_rewrite_task(
            config, chapter_number=1, session_id=second_turn.session_id,
            turn_number=second_turn.turn_number, option_id="OPTION-A",
        )
    from longform_engine.chapter_coedit import coedit_status
    from longform_engine.review_server import ReviewDeskService
    turns = ReviewDeskService(config, chapter_number=1)._coedit_views(
        coedit_status(config, chapter_number=1)["sessions"],
    )[0]["turns"]
    assert turns[0]["response_current"] is True
    assert turns[0]["candidate_current"] is False
    assert turns[0]["rewrite_current"] is True
    assert turns[1]["candidate_current"] is False
    assert turns[1]["rewrite_current"] is False
    next_turn = create_chapter_coedit_turn(
        config, chapter_number=1, start=0, end=24, question="请核对这一版已经修改的关系边界。",
    )
    history_path = (root / next_turn.task_file).with_name(f"turn{next_turn.turn_number:02d}.history.json")
    assert read_json(history_path)["turns"] == []
    next_response = root / next_turn.response_file
    write_advisor_response(next_response)
    assert validate_chapter_coedit_response(config, chapter_number=1, file_path=next_response).ok
    record_chapter_coedit_response(config, chapter_number=1, file_path=next_response)
    next_rewrite = create_chapter_coedit_rewrite_task(
        config, chapter_number=1, session_id=next_turn.session_id,
        turn_number=next_turn.turn_number, option_id="OPTION-A",
    )
    next_candidate = root / next_rewrite.candidate_file
    next_candidate.write_bytes(chapter_text("林迟把路图放下，等阿岚自己选择。").encode("utf-8"))
    assert validate_chapter_coedit_candidate(config, chapter_number=1, file_path=next_candidate).ok
    # Revalidating the selected candidate is idempotent; an older candidate
    # cannot take its place merely because its earlier validation passed.
    assert validate_chapter_coedit_candidate(config, chapter_number=1, file_path=next_candidate).ok
    with pytest.raises(ChapterCoeditError, match="stale for the current candidate"):
        validate_chapter_coedit_candidate(config, chapter_number=1, file_path=replacement)
    assert coedit_status(config, chapter_number=1)["sessions"][0]["current_candidate_sha256"] == sha256(next_candidate.read_bytes()).hexdigest()
    assert not final_before.exists()


def test_coedit_cannot_bypass_current_p0_or_p1(tmp_path: Path):
    config, root = seed_project(tmp_path)
    writing = continue_write(config, chapter_number=1)
    source = root / writing.recommended_agent_draft
    source.write_text(chapter_text(), encoding="utf-8")
    gate = root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    write_json(gate, {"severity_counts": {"P0": 0, "P1": 1, "P2": 0}})

    with pytest.raises(ChapterCoeditError, match="immutable repair plan"):
        create_chapter_coedit_turn(
            config,
            chapter_number=1,
            start=0,
            end=20,
            question="请直接绕过修复改写。",
        )


def test_coedit_text_anchor_uses_unicode_codepoints_and_paragraph_identity():
    text = "第一段😀。\n\n第二段人物选择。"
    start = text.index("😀")
    anchor = build_text_anchor(
        text,
        start=start,
        end=start + 1,
        source_sha256="a" * 64,
    )

    assert anchor["schema"] == "text_anchor_v2"
    assert anchor["offset_unit"] == "unicode_codepoint"
    assert anchor["text"] == "😀"
    assert anchor["paragraph_id"].startswith("p0001-")
    assert len(anchor["selected_sha256"]) == 64
    assert "utf16ToCodePoint" in review_page_html("token")
