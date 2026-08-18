import json
from hashlib import sha256
from pathlib import Path

import pytest
from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_DOCUMENT_SCHEMA,
    DESIGN_REQUIRED_HEADINGS,
)
from longform_engine.agent_tasks import list_manifests, load_manifest, validate_manifest_strict
from longform_engine.config import ConfigError, load_project_config
from longform_engine.intelligence import (
    apply_compiled_design,
    approve_design_document,
    assess_chapter_direction,
    create_design_compile_task,
    create_intelligence_task,
    validate_design_compile_delta,
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


def write_design_candidate(path: Path, task_type: str, payload: dict) -> None:
    def scalar_lines(value) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [line for item in value for line in scalar_lines(item)]
        if isinstance(value, dict):
            return [line for item in value.values() for line in scalar_lines(item)]
        return []

    facts = scalar_lines({key: value for key, value in payload.items() if key != "schema"})
    sections: list[str] = []
    for index, heading in enumerate(DESIGN_REQUIRED_HEADINGS[task_type]):
        body = ["本节决定已经由用户审阅。"]
        if index == 0:
            body.extend(f"- {fact}" for fact in facts)
        sections.extend((f"## {heading}", "", *body, ""))
    path.write_text(f"# {task_type} 设计文档\n\n" + "\n".join(sections), encoding="utf-8")


def validate_intelligence_output(config, root: Path, manifest: dict, candidate: Path):
    control = validate_production_agent_result(root, manifest, result_file=candidate)
    assert control.ok, control.normalization.errors
    return validate_intelligence_candidate(
        config,
        task_type=str(manifest["task_type"]),
        file_path=candidate,
    )


def prepare_design_delta(config, root: Path, task_type: str, candidate: Path, payload: dict) -> Path:
    manifest = next(
        load_manifest(root, item["task_id"])
        for item in reversed(list_manifests(root))
        if item.get("task_type") == task_type
        and candidate.relative_to(root).as_posix() == (item.get("io") or {}).get("output", {}).get("path")
    )
    assert validate_intelligence_output(config, root, manifest, candidate).ok
    approve_design_document(
        config,
        task_type=task_type,
        document_path=candidate,
        approved_by="human",
    )
    compile_task = create_design_compile_task(
        config,
        task_type=task_type,
        document_path=candidate,
    )
    delta = root / compile_task.candidate_file
    changes = {key: value for key, value in payload.items() if key != "schema"}
    for cli_field in {
        "book_ideation": ("round", "dimension"),
        "chapter_direction": ("chapter_number", "chapter_card_sha256", "trigger_reasons"),
        "outline_revision": ("from_chapter", "to_chapter"),
    }.get(task_type, ()):
        changes.pop(cli_field, None)
    text = candidate.read_text(encoding="utf-8")
    source = candidate.relative_to(root).as_posix()
    delta.write_text(
        json.dumps(
            {
                "schema": CANONICAL_DELTA_SCHEMA,
                "delta_type": "design_document",
                "coverage": {key: "changed" for key in changes},
                "changes": changes,
                "evidence": {
                    f"/changes/{key.replace('~', '~0').replace('/', '~1')}": [
                        f"{source}@0:{len(text)}"
                    ]
                    for key in changes
                },
                "uncertainties": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, compile_task.task_id),
        result_file=delta,
    )
    assert control.ok, control.normalization.errors
    return delta


def valid_direction_candidate(root: Path, chapter_number: int, reasons: list[str]) -> dict:
    card = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    scene_chain = [
        {
            "scene_id": "verify_witness",
            "location": "archive gate",
            "participants": ["lead_ari", "ally_mira"],
            "desire_collision": "Ari wants verification while Mira wants immediate pursuit.",
            "choice": "Ari spends the last safe minute checking the damaged seal.",
            "cost": "The visible suspect gains distance.",
            "turn": "The seal proves the suspect used an internal route.",
        },
        {
            "scene_id": "share_evidence",
            "location": "witness stair",
            "participants": ["lead_ari", "ally_mira"],
            "desire_collision": "Mira demands the route while Ari wants sole control of the clue.",
            "choice": "Ari shares the route and accepts Mira's condition.",
            "cost": "He loses sole control of the evidence.",
            "turn": "Their alliance becomes an operational obligation.",
        },
    ]
    common = {
        "book_goal": "Expose who controls collective memory.",
        "volume_goal": "Prove the archive is being altered from inside.",
        "protagonist_goal": "Preserve evidence without treating Mira as a tool.",
        "featured_character_ids": ["lead_ari", "ally_mira"],
        "scene_chain": scene_chain,
        "cast_desires": {
            "lead_ari": "Verify the physical trace before pursuit.",
            "ally_mira": "Catch the witness before the gate closes.",
        },
        "dialogue_ownership": "Ari narrows claims; Mira forces decisions and names costs.",
        "embodiment_plan": "Use Ari's careful handling and Mira's changing distance under pressure.",
        "interiority_function": "Expose Ari's urge to control information immediately before he shares it.",
        "conflict": "Verification consumes the only safe pursuit window.",
        "information_release": "The damaged seal identifies an internal route without naming the editor.",
        "local_payoff": "A prior seal detail becomes actionable evidence.",
        "character_cost": "Ari surrenders sole control and loses pursuit time.",
        "mainline_move": "The investigation moves from outside sabotage to internal access.",
        "character_arc_move": "Ari makes one bounded trust decision.",
        "foreshadow_move": "The false treaty thread echoes through the matching seal cut.",
        "relationship_move": "The alliance gains a shared obligation.",
        "ending_mode": "changed_problem",
        "main_risks": ["Too much procedure could flatten the choice."],
        "canon_refs": [],
        "world_rule_refs": [],
        "foreshadow_refs": [],
        "forbidden_reveals": ["identity of the archive editor"],
    }
    selected = {
        "id": "verify_witness",
        "title": "先核验目击者",
        "chapter_duty": "用一次有代价的核验排除最显眼的错误判断。",
        **common,
    }
    selected["reader_gain"] = selected.pop("local_payoff")
    selected["cost"] = selected.pop("character_cost")
    return {
        "schema": "chapter_direction_candidate_v2",
        "chapter_number": chapter_number,
        "chapter_card_sha256": sha256(card.read_bytes()).hexdigest(),
        "trigger_reasons": reasons,
        "selected_direction": selected,
        "selection": {
            "direction_id": "verify_witness",
            "user_adjustments": {},
        },
        "canonical_refs": selected["canon_refs"],
        "introduced_elements": [],
    }


def test_effective_quality_contract_merges_resource_layers_and_project_override(tmp_path):
    config = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "project": {"root_dir": str(tmp_path / "contract")},
            "story_profile": {
                "market": {"primary": "fanqie_free", "compatibility": []},
                "setting": {"primary": "urban", "secondary": []},
                "plot_engines": {"primary": "mystery", "supporting": []},
            },
            "quality": {
                "profile": {
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
    assert (contract["market"], contract["phase"]) == ("fanqie_free", "opening")
    assert [item["id"] for item in contract["active_facets"][:2]] == ["urban", "mystery"]
    assert contract["strictness"] == "strict"
    assert contract["contract"]["foreshadow_release"]["preserve_core_answer"] is True
    assert contract["contract"]["slow_chapter_policy"]["project_reason"].startswith("A quiet")
    source_kinds = [item["kind"] for item in contract["sources"]]
    assert source_kinds[0] == "market"
    assert "setting" in source_kinds
    assert "plot_engines" in source_kinds
    assert source_kinds.index("phase") < source_kinds.index("setting")
    assert source_kinds.index("market_phase") < source_kinds.index("setting")
    assert all(len(item["sha256"]) == 64 for item in contract["sources"])
    assert contract["contract"]["ending_distribution"] == ["quiet_shift"]
    assert "ending_distribution" in contract["overridden_fields"]
    trace_layers = [item["layer"] for item in contract["merge_trace"]]
    assert trace_layers[0] == "market"
    assert trace_layers.index("story_phase") < trace_layers.index("market_phase")
    assert trace_layers.index("market_phase") < trace_layers.index("project_overrides")
    assert contract["merge_order"] == [
        "fact_and_safety_boundaries",
        "market",
        "story_facets",
        "current_story_arc",
        "phase",
        "market_phase",
        "user_approved_style_baseline",
        "project_overrides",
    ]
    assert contract["approved_style_baseline"]["auto_expand"] is False


def test_effective_contract_applies_current_arc_focus_after_story_facets(tmp_path):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config)

    contract = compile_effective_quality_contract(config, chapter_number=1)

    trace = [item["layer"] for item in contract["merge_trace"]]
    assert trace.index("story_phase") < trace.index("setting")
    assert trace.index("market_phase") < trace.index("setting")
    assert trace.index("tone") < trace.index("current_story_arc")
    assert contract["contract"]["current_story_arc"]["arc_id"] == "arc_01"
    assert len(contract["active_facets"]) <= 3


@pytest.mark.parametrize(
    ("override", "message"),
    (
        ({"profile": {"phase": "midgame"}}, "quality.profile.phase"),
        ({"profile": {"strictness": "maximum"}}, "quality.profile.strictness"),
        ({"creative_guidance": {"mode": "always_interrupt"}}, "creative_guidance.mode"),
    ),
)
def test_quality_profile_config_rejects_unknown_contract_dimensions(override, message):
    with pytest.raises(ConfigError, match=message):
        load_project_config(template="qidian-longform", cli_overrides={"quality": override})


def test_story_profile_rejects_unknown_facet():
    with pytest.raises(ConfigError, match="unknown story facet"):
        load_project_config(
            template="qidian-longform",
            cli_overrides={"story_profile": {"plot_engines": {"primary": "unknown_plot"}}},
        )


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
    write_design_candidate(
        candidate,
        "book_ideation",
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
    )

    delta = prepare_design_delta(config, root, "book_ideation", candidate, {
        "schema": "book_ideation_candidate_v1",
        "round": 1,
        "dimension": "target_reader_and_reading_context",
        "question": "谁会在什么场景下连续阅读？",
        "options": [
            {"id": "option_a", "proposal": "通勤追更", "tradeoffs": ["进入快", "余波短"]},
            {"id": "option_b", "proposal": "夜间沉浸", "tradeoffs": ["氛围深", "进入慢"]},
        ],
        "selection": {"mode": "selected_option", "option_id": "missing", "answer": ""},
    })
    validation = validate_design_compile_delta(
        config,
        task_type="book_ideation",
        document_path=candidate,
        delta_path=delta,
    )

    assert not validation.ok
    assert "selection.option_id" in " ".join(validation.errors)
    assert not (root / "10_bible" / "creative_decisions.json").exists()
    assert (root / "30_state" / "novel_state.json").read_bytes() == state_before
    assert manifest["policy"]["requires_human_apply"] is True
    assert set(manifest["policy"]["canonical_targets"]) == {
        "10_bible/creative_decisions.json",
        "30_state/novel_state.json",
        "10_bible/design_documents/book_ideation.project.md",
        "30_state/design_deltas/book_ideation.project.json",
    }
    assert manifest["policy"]["context"]["budget_profile"] == "standard"
    assert manifest["policy"]["context"]["capacity_units"] == 48_000
    assert brief["manifest_validation"]["ok"] is True


def test_production_next_keeps_active_project_intelligence_ahead_of_chapter_work(tmp_path):
    config, _ = seed_project(tmp_path)
    task = create_intelligence_task(config, task_type="book_ideation")

    action = production_next(config)

    assert action["status"] == "agent_task_awaiting_agent"
    assert action["task_id"] == task.task_id
    assert action["task_type"] == "book_ideation"
    assert action["next_command"] == f"longform-engine agent-task brief project.yaml {task.task_id}"
    assert action["protocol_validate_command"].startswith("longform-engine agent-task result-validate ")


def test_chapter_direction_is_required_strict_and_human_applied(tmp_path):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config, direction_applied=False)
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
    assert manifest["policy"]["context"]["budget_profile"] == "standard"
    assert manifest["policy"]["context"]["capacity_units"] == 48_000
    assert manifest["policy"]["requires_human_apply"] is True
    candidate = root / manifest["io"]["output"]["path"]
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    plan = root / "20_outline" / "chapter_plan.json"
    before = {"card": card.read_bytes(), "plan": plan.read_bytes()}

    valid = valid_direction_candidate(root, 1, next_action["trigger_reasons"])
    write_design_candidate(candidate, "chapter_direction", valid)
    delta = prepare_design_delta(config, root, "chapter_direction", candidate, valid)
    valid_delta_bytes = delta.read_bytes()
    invalid_payload = json.loads(delta.read_text(encoding="utf-8"))
    invalid_payload["changes"]["chapter_card_sha256"] = "0" * 64
    source = candidate.relative_to(root).as_posix()
    invalid_payload["evidence"]["/changes/chapter_card_sha256"] = [
        f"{source}@0:{len(candidate.read_text(encoding='utf-8'))}"
    ]
    delta.write_text(json.dumps(invalid_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    control = validate_production_agent_result(
        root,
        next(
            load_manifest(root, item["task_id"])
            for item in reversed(list_manifests(root))
            if item.get("task_type") == "design_semantic_compile"
        ),
        result_file=delta,
    )
    assert control.ok, control.normalization.errors
    invalid_result = validate_design_compile_delta(
        config,
        task_type="chapter_direction",
        document_path=candidate,
        delta_path=delta,
    )
    assert not invalid_result.ok
    assert card.read_bytes() == before["card"]
    assert plan.read_bytes() == before["plan"]

    delta.write_bytes(valid_delta_bytes)
    control = validate_production_agent_result(
        root,
        next(
            load_manifest(root, item["task_id"])
            for item in reversed(list_manifests(root))
            if item.get("task_type") == "design_semantic_compile"
        ),
        result_file=delta,
    )
    assert control.ok, control.normalization.errors
    validated = validate_design_compile_delta(
        config,
        task_type="chapter_direction",
        document_path=candidate,
        delta_path=delta,
    )
    assert validated.ok, validated.errors
    with pytest.raises(ValueError, match="approved-by human"):
        apply_compiled_design(
            config,
            task_type="chapter_direction",
            document_path=candidate,
            delta_path=delta,
            approved_by="agent",
        )
    applied = apply_compiled_design(
        config,
        task_type="chapter_direction",
        document_path=candidate,
        delta_path=delta,
        approved_by="human",
    )
    applied_card = json.loads(card.read_text(encoding="utf-8"))
    assert applied.status == "applied"
    assert applied_card["direction_selection"]["direction_id"] == "verify_witness"
    assert applied_card["reader_gain"] == valid["selected_direction"]["reader_gain"]
    assert assess_chapter_direction(config, 1)["required"] is False
    assert production_next(config)["status"] == "ready_for_continue_write"


def test_guided_direction_still_requires_human_choice_for_specific_chapter(tmp_path):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config, direction_applied=False)
    config.data["quality"]["creative_guidance"]["mode"] = "guided"
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
    status = assess_chapter_direction(config, 2)

    assert status["required"] is True
    assert "guided_mode" in status["reasons"]


def test_chapter_direction_apply_failure_rolls_back_card_and_plan(tmp_path, monkeypatch):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config, direction_applied=False)
    config.data["quality"]["creative_guidance"]["mode"] = "guided"
    task = create_intelligence_task(config, task_type="chapter_direction", chapter_number=1)
    candidate = root / task.candidate_file
    reasons = assess_chapter_direction(config, 1)["reasons"]
    direction = valid_direction_candidate(root, 1, reasons)
    write_design_candidate(candidate, "chapter_direction", direction)
    delta = prepare_design_delta(config, root, "chapter_direction", candidate, direction)
    assert validate_design_compile_delta(
        config,
        task_type="chapter_direction",
        document_path=candidate,
        delta_path=delta,
    ).ok
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    plan = root / "20_outline" / "chapter_plan.json"
    before = {"card": card.read_bytes(), "plan": plan.read_bytes()}

    def fail_after_partial_write(project_config, project_root, task_type, payload, *, scope=None):
        card.write_text('{"partial": true}', encoding="utf-8")
        plan.write_text("[]", encoding="utf-8")
        raise RuntimeError("injected direction apply failure")

    monkeypatch.setattr(intelligence_pipeline, "write_targets", fail_after_partial_write)
    with pytest.raises(RuntimeError, match="injected direction apply failure"):
        apply_compiled_design(
            config,
            task_type="chapter_direction",
            document_path=candidate,
            delta_path=delta,
            approved_by="human",
        )

    assert card.read_bytes() == before["card"]
    assert plan.read_bytes() == before["plan"]
    assert list((root / "70_runtime" / "transactions").glob("*.rollback.json"))
