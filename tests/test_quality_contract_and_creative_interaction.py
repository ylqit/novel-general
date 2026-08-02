import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import ConfigError, load_project_config
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    assess_chapter_direction,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.orchestration import open_book
from longform_engine.production import agent_task_brief, production_loop, production_next
from longform_engine.quality import approve_style_baseline, compile_effective_quality_contract
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready
import longform_engine.intelligence.pipeline as intelligence_pipeline


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    return config, project.root


def valid_direction_candidate(root: Path, chapter_number: int, reasons: list[str]) -> dict:
    card = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    return {
        "schema": "chapter_direction_candidate_v1",
        "chapter_number": chapter_number,
        "chapter_card_sha256": sha256(card.read_bytes()).hexdigest(),
        "trigger_reasons": reasons,
        "directions": [
            {
                "id": "verify_witness",
                "title": "先核验目击者",
                "chapter_duty": "用一次有代价的核验排除最显眼的错误判断。",
                "conflict": "主角必须在保护证人与追赶线索之间选择。",
                "information_release": "确认一条证词被人为改写，但不揭示改写者。",
                "local_payoff": "读者获得可复核的新证据和一个被排除的假设。",
                "character_cost": "主角失去当夜追踪另一名嫌疑人的机会。",
                "longline_impact": "父亲旧案与当前改写手法建立弱关联。",
                "foreshadow_impact": "推进 witness_line 伏笔但不越过兑现窗口。",
                "relationship_impact": "盟友因主角放弃追击而重新评估他的判断。",
                "ending_mode": "partial_payoff",
                "main_risks": ["调查过程可能解释过密。"],
            },
            {
                "id": "follow_decoy",
                "title": "追踪诱饵",
                "chapter_duty": "让主角主动犯下有依据的错误判断。",
                "conflict": "更快的追踪会牺牲证据链完整性。",
                "information_release": "诱饵来自内部流程，而非外部袭击者。",
                "local_payoff": "读者看到能力边界在实际调查中生效。",
                "character_cost": "主角承担一次错误承诺并暴露行动路线。",
                "longline_impact": "扩大机构内部矛盾但延迟父亲旧案线索。",
                "foreshadow_impact": "种下内部通行凭证的来源问题。",
                "relationship_impact": "盟友暂时取得决策权。",
                "ending_mode": "changed_problem",
                "main_risks": ["若证据不足会显得为了反转而误判。"],
            },
        ],
        "selection": {
            "direction_id": "verify_witness",
            "user_adjustments": {},
        },
    }


def test_effective_quality_contract_merges_resource_layers_and_project_override(tmp_path):
    config = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "project": {"root_dir": str(tmp_path / "contract")},
            "quality": {
                "profile": {
                    "market": "fanqie_free",
                    "genre": "suspense",
                    "phase": "opening",
                    "strictness": "strict",
                    "overrides": {
                        "slow_chapter_policy": {
                            "allowed": True,
                            "project_reason": "A quiet witness chapter is intentional.",
                        },
                        "ending_distribution": ["quiet_shift"],
                    },
                }
            },
        },
    )

    contract = compile_effective_quality_contract(config, chapter_number=1)

    assert contract["schema"] == "effective_quality_contract_v1"
    assert (contract["market"], contract["genre"], contract["phase"]) == (
        "fanqie_free",
        "suspense",
        "opening",
    )
    assert contract["strictness"] == "strict"
    assert contract["contract"]["foreshadow_release"]["preserve_core_answer"] is True
    assert contract["contract"]["slow_chapter_policy"]["project_reason"].startswith("A quiet")
    assert [item["kind"] for item in contract["sources"]] == [
        "market",
        "genre",
        "phase",
        "market_phase",
    ]
    assert all(len(item["sha256"]) == 64 for item in contract["sources"])
    assert contract["contract"]["ending_distribution"] == ["quiet_shift"]
    assert "ending_distribution" in contract["overridden_fields"]
    assert [item["layer"] for item in contract["merge_trace"]] == contract["merge_order"]
    assert contract["approved_style_baseline"]["auto_expand"] is False


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ({"profile": {"genre": "wuxia_unknown"}}, "quality.profile.genre"),
        ({"profile": {"phase": "midgame"}}, "quality.profile.phase"),
        ({"profile": {"strictness": "maximum"}}, "quality.profile.strictness"),
        ({"creative_guidance": {"mode": "always_interrupt"}}, "creative_guidance.mode"),
    ),
)
def test_quality_profile_config_rejects_unknown_contract_dimensions(override, message):
    with pytest.raises(ConfigError, match=message):
        load_project_config(template="qidian-longform", cli_overrides={"quality": override})


def test_style_baseline_only_expands_through_explicit_human_approval(tmp_path):
    config, root = seed_project(tmp_path)
    baseline = root / "10_bible" / "style_profiles" / "approved_style_baseline.json"
    assert not baseline.exists()
    with pytest.raises(ValueError, match="finalized"):
        approve_style_baseline(config, chapter_number=1, approved_by="editor")
    assert not baseline.exists()

    finalization = root / "40_manuscript" / "final" / "ch001.finalization.json"
    finalization.parent.mkdir(parents=True, exist_ok=True)
    finalization.write_text(
        json.dumps({"chapter_number": 1, "final_sha256": "f" * 64}),
        encoding="utf-8",
    )
    history = root / "30_state" / "quality" / "structure_history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "opening_mode": "in_scene",
                "topology_id": "investigation",
                "ending_mode": "partial_payoff",
                "dialogue_ratio": 0.24,
                "source_excerpt": "must not enter baseline",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    approved = approve_style_baseline(config, chapter_number=1, approved_by="editor")
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    contract = compile_effective_quality_contract(config, chapter_number=2)

    assert approved.approved_by == "editor"
    assert payload["auto_expand"] is False
    assert payload["approved_chapters"][0]["observation"]["opening_mode"] == "in_scene"
    assert "source_excerpt" not in payload["approved_chapters"][0]["observation"]
    assert contract["approved_style_baseline"]["approved_chapters"] == [1]
    assert Path(root / approved.transaction_report).exists()


def test_book_ideation_invalid_selection_does_not_pollute_bible_or_state(tmp_path):
    config, root = seed_project(tmp_path)
    task = create_intelligence_task(config, task_type="book_ideation")
    manifest = load_manifest(root, task.task_id)
    brief = agent_task_brief(config, task.task_id)
    candidate = root / task.candidate_file
    state_before = (root / "30_state" / "novel_state.json").read_bytes()
    candidate.write_text(
        json.dumps(
            {
                "schema": "book_ideation_candidate_v1",
                "round": 1,
                "dimension": "target_reader_and_reading_context",
                "question": "谁会在什么场景下连续阅读？",
                "options": [
                    {"id": "option_a", "proposal": "通勤追更", "tradeoffs": ["进入快", "余波短"]},
                    {"id": "option_b", "proposal": "夜间沉浸", "tradeoffs": ["氛围深", "进入慢"]},
                ],
                "selection": {"mode": "selected_option", "option_id": "missing", "answer": ""},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validation = validate_intelligence_candidate(
        config,
        task_type="book_ideation",
        file_path=candidate,
    )

    assert not validation.ok
    assert "selection.option_id" in " ".join(validation.errors)
    assert not (root / "10_bible" / "creative_decisions.json").exists()
    assert (root / "30_state" / "novel_state.json").read_bytes() == state_before
    assert manifest["requires_human_apply"] is True
    assert manifest["canonical_targets"] == [
        "10_bible/creative_decisions.json",
        "30_state/novel_state.json",
    ]
    assert manifest["context_policy"]["max_files"] == 5
    assert manifest["context_policy"]["max_chars"] == 12_000
    assert brief["manifest_validation"]["ok"] is True


def test_chapter_direction_is_conditional_strict_and_human_applied(tmp_path):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config)
    config.data["quality"]["creative_guidance"]["mode"] = "guided"

    next_action = production_next(config)
    assert next_action["task_type"] == "chapter_direction"
    loop = production_loop(config, max_steps=1)
    assert loop["steps"][0]["action"] == "intelligence_task"
    assert loop["next_action"]["status"] == "agent_task_awaiting_agent"

    task_id = loop["next_action"]["task_id"]
    manifest = load_manifest(root, task_id)
    assert validate_manifest_strict(root, manifest).ok
    assert manifest["scope"] == {"kind": "chapter", "chapter_number": 1}
    assert manifest["context_policy"]["max_files"] == 6
    assert manifest["context_policy"]["max_chars"] == 16_000
    assert manifest["requires_human_apply"] is True
    candidate = root / manifest["allowed_output_paths"][0]
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    plan = root / "20_outline" / "chapter_plan.json"
    before = {"card": card.read_bytes(), "plan": plan.read_bytes()}

    invalid = valid_direction_candidate(root, 1, next_action["trigger_reasons"])
    invalid["chapter_card_sha256"] = "0" * 64
    candidate.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
    invalid_result = validate_intelligence_candidate(
        config,
        task_type="chapter_direction",
        file_path=candidate,
    )
    assert not invalid_result.ok
    assert card.read_bytes() == before["card"]
    assert plan.read_bytes() == before["plan"]

    valid = valid_direction_candidate(root, 1, next_action["trigger_reasons"])
    candidate.write_text(json.dumps(valid, ensure_ascii=False), encoding="utf-8")
    validated = validate_intelligence_candidate(
        config,
        task_type="chapter_direction",
        file_path=candidate,
    )
    assert validated.ok, validated.errors
    with pytest.raises(ValueError, match="approved-by human"):
        apply_intelligence_candidate(
            config,
            task_type="chapter_direction",
            file_path=candidate,
        )
    applied = apply_intelligence_candidate(
        config,
        task_type="chapter_direction",
        file_path=candidate,
        approved_by="human",
    )
    applied_card = json.loads(card.read_text(encoding="utf-8"))
    assert applied.status == "applied"
    assert applied_card["direction_selection"]["direction_id"] == "verify_witness"
    assert applied_card["reader_gain"].startswith("读者获得")
    assert assess_chapter_direction(config, 1)["required"] is False
    assert production_next(config)["status"] == "ready_for_continue_write"


def test_automatic_direction_does_not_interrupt_specific_stable_chapter(tmp_path):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config)
    plan_path = root / "20_outline" / "chapter_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan[1].update(
        {
            "title": "第二份口供",
            "duty": "核对两份口供中的时间差并迫使主角放弃一个先入判断。",
            "conflict": "证人安全与当夜追踪机会不能同时保全。",
            "information_release": "门禁记录证明嫌疑人离开时间被提前登记。",
            "hook": "错误记录使用了主角父亲旧案的编号规则。",
        }
    )
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config.data["quality"]["creative_guidance"]["mode"] = "automatic"

    status = assess_chapter_direction(config, 2)

    assert status == {"required": False, "reasons": [], "status": "not_required"}


def test_chapter_direction_apply_failure_rolls_back_card_and_plan(tmp_path, monkeypatch):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config)
    config.data["quality"]["creative_guidance"]["mode"] = "guided"
    task = create_intelligence_task(config, task_type="chapter_direction", chapter_number=1)
    candidate = root / task.candidate_file
    reasons = assess_chapter_direction(config, 1)["reasons"]
    candidate.write_text(
        json.dumps(valid_direction_candidate(root, 1, reasons), ensure_ascii=False),
        encoding="utf-8",
    )
    assert validate_intelligence_candidate(
        config,
        task_type="chapter_direction",
        file_path=candidate,
    ).ok
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    plan = root / "20_outline" / "chapter_plan.json"
    before = {"card": card.read_bytes(), "plan": plan.read_bytes()}

    def fail_after_partial_write(project_root, task_type, payload):
        card.write_text('{"partial": true}', encoding="utf-8")
        plan.write_text("[]", encoding="utf-8")
        raise RuntimeError("injected direction apply failure")

    monkeypatch.setattr(intelligence_pipeline, "write_targets", fail_after_partial_write)
    with pytest.raises(RuntimeError, match="injected direction apply failure"):
        apply_intelligence_candidate(
            config,
            task_type="chapter_direction",
            file_path=candidate,
            approved_by="human",
        )

    assert card.read_bytes() == before["card"]
    assert plan.read_bytes() == before["plan"]
    assert list((root / "70_runtime" / "transactions").glob("*.rollback.json"))
