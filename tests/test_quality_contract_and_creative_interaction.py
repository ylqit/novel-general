import json
from pathlib import Path

import pytest
from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_REQUIRED_HEADINGS,
)
from longform_engine.agent_tasks import list_manifests, load_manifest
from longform_engine.config import ConfigError, load_project_config
from longform_engine.intelligence import (
    approve_design_document,
    create_design_compile_task,
    create_intelligence_task,
    validate_design_compile_delta,
    validate_intelligence_candidate,
)
from longform_engine.orchestration import open_book
from longform_engine.production import agent_task_brief, production_next
from longform_engine.quality import approve_style_baseline, compile_effective_quality_contract
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


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
        if task_type == "chapter_direction" and heading == "方向选项":
            direction_id = str(payload["selected_direction"]["id"])
            body = [
                f"### option:{direction_id} — {payload['selected_direction']['title']}",
                "沿当前证据链推进并承担明确代价。",
                "",
                "### option:alternate_route — 改由关系压力切入",
                "保留章节保护结果，但改变场景进入和冲突承担者。",
            ]
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


def prepare_design_delta(
    config,
    root: Path,
    task_type: str,
    candidate: Path,
    payload: dict,
    *,
    validate_domain: bool = True,
) -> Path:
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
        "chapter_direction": ("chapter_number", "chapter_card_sha256", "trigger_reasons", "selection"),
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
    if validate_domain:
        domain_validation = validate_design_compile_delta(
            config,
            task_type=task_type,
            document_path=candidate,
            delta_path=delta,
        )
        assert domain_validation.ok, domain_validation.errors
    return delta




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
        "conditional_overlay",
        "user_approved_style_baseline",
        "project_overrides",
    ]
    assert contract["approved_style_baseline"]["auto_expand"] is False
    assert contract["conditional_overlays"] == []


def test_qidian_fanfiction_uses_advisory_chinese_longform_overlay(tmp_path):
    config, _root = seed_project(tmp_path)
    config.data["creation"]["mode"] = "fanfiction"

    contract = compile_effective_quality_contract(config, chapter_number=1)

    assert [item["id"] for item in contract["conditional_overlays"]] == [
        "cn_longform_fanfiction",
        "qidian_male_fanfiction",
    ]
    overlay = contract["contract"]["cn_longform_fanfiction"]
    assert overlay["execution_level"] == "P2_advisory"
    assert overlay["fixed_sentence_template"] is False
    assert overlay["fixed_dialogue_ratio"] is False
    assert overlay["mandatory_combat_frequency"] is False
    assert overlay["mandatory_cliffhanger"] is False
    assert any("原著人物" in item for item in overlay["review_questions"])
    qidian = contract["contract"]["qidian_male_fanfiction"]
    assert "原著人物" in qidian["canon_character_value"]
    assert any("固定字数" in item for item in qidian["prohibited_inferences"])


def test_fanqie_fanfiction_uses_separate_mobile_reading_quality_layer(tmp_path):
    config, _root = seed_project(tmp_path)
    config.data["creation"]["mode"] = "fanfiction"
    config.data["story_profile"]["market"]["primary"] = "fanqie_free"
    config.data["story_profile"]["market"]["compatibility"] = []

    contract = compile_effective_quality_contract(config, chapter_number=1)

    assert [item["id"] for item in contract["conditional_overlays"]] == [
        "cn_longform_fanfiction",
        "fanqie_free_fanfiction",
    ]
    fanqie = contract["contract"]["fanqie_free_fanfiction"]
    assert fanqie["execution_level"] == "P2_advisory"
    assert "移动阅读" in fanqie["mobile_clarity"]
    assert "重复信息" in fanqie["anti_padding"]
    assert "模板" in fanqie["anti_mass_generation"]


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
        ({"creative_guidance": {"mode": "always_interrupt"}}, "Removed config field"),
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
    }, validate_domain=False)
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


def test_production_next_honors_active_book_ideation_before_formal_planning(tmp_path):
    config, _ = seed_project(tmp_path)
    task = create_intelligence_task(config, task_type="book_ideation")

    action = production_next(config)

    assert task.task_id
    assert action["status"] == "agent_task_awaiting_agent"
    assert action["task_type"] == "book_ideation"
    assert action["next_command"].startswith("longform-engine agent-task brief ")
