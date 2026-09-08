import json
from pathlib import Path

import pytest

from longform_engine.chapter_contract import ChapterContractError
from longform_engine.chapter_contract import resolve_chapter_contract_refs
from longform_engine.planning.context import load_chapter_planning_context
from longform_engine.planning import (
    apply_planning_bundle, build_human_node_decisions, build_human_planning_approval,
    build_planning_semantic_application, validate_planning_semantic_application,
)
from tests.test_v010_planning import seed_project, planning_bundle, write_json, evidence_review


def test_contract_resolves_mixed_planning_references_without_promoting_future_changes(tmp_path):
    root = tmp_path
    write_json(root / "10_bible/canonical_facts.json", {"schema": "canonical_fact_registry_v2", "items": []})
    write_json(root / "10_bible/characters.json", [{"id": "C_LEAD", "name": "陆照", "goal": "保住岗位"}])
    rule = root / "10_bible/power_system.md"
    rule.write_text("# 能力\n\n## 代价\n接触后才有感知，过用会伤手。\n\n## 其他\n不应混入所引段落。", encoding="utf-8")
    obligations = {"items": [
        {"obligation_id": "ob.before", "subject_refs": ["C_LEAD"], "prior_state_refs": [],
         "dependency_refs": [], "intended_change": "计划发生的新伤，尚未发生"},
        {"obligation_id": "ob.current", "subject_refs": ["C_LEAD", "10_bible/characters.json#/0"],
         "prior_state_refs": ["10_bible/power_system.md#代价"], "dependency_refs": ["ob.before"]},
    ]}
    ledger = root / "30_state/semantic_obligations.json"
    write_json(ledger, obligations)
    contract = {"semantic_obligation_refs": ["ob.current"]}
    values = resolve_chapter_contract_refs(root, contract)
    assert len(values) == 2  # Stable ID and exact pointer describe the same entity.
    assert values[0]["value"]["name"] == "陆照"
    assert "过用会伤手" in values[1]["value"]
    assert "不应混入" not in values[1]["value"]
    assert "尚未发生" not in json.dumps(values, ensure_ascii=False)
    assert all(len(item["sha256"]) == 64 for item in values)

    unapproved = root / "50_workbench/unapproved.md"
    unapproved.parent.mkdir(parents=True)
    unapproved.write_text("未批准的工作材料不能伪装成 Canon。", encoding="utf-8")
    for bad in ["C_MISSING", "10_bible/characters.json#/99", "../private.json", "10_bible/power_system.md#不存在",
                "10_bible/../50_workbench/unapproved.md"]:
        obligations["items"][1]["prior_state_refs"] = [bad]
        write_json(ledger, obligations)
        with pytest.raises(ChapterContractError, match="context_evidence_incomplete"):
            resolve_chapter_contract_refs(root, contract)
    obligations["items"][1]["prior_state_refs"] = []
    obligations["items"][0]["dependency_refs"] = ["ob.current"]
    write_json(ledger, obligations)
    with pytest.raises(ChapterContractError, match="obligation_dependency_cycle"):
        resolve_chapter_contract_refs(root, contract)

def approved_project(tmp_path: Path):
    config, root = seed_project(tmp_path)
    bundle_path = write_json(
        root / "50_workbench" / "planning" / "bundle.json", planning_bundle()
    )
    subject_relative = bundle_path.relative_to(root).as_posix()
    review_path = write_json(
        root / "50_workbench" / "planning" / "review.json",
        evidence_review(subject_relative),
    )
    application = build_planning_semantic_application(
        root,
        subject_path=bundle_path,
        profile="architecture",
        author_task_id="task:author",
        author_role_id="story_architect",
        reviewer_task_id="task:reviewer",
        reviewer_role_id="continuity_reviewer",
        reviewer_version="v1",
        review_result_path=review_path,
    )
    application_path = write_json(
        root / "50_workbench" / "planning" / "application.json", application
    )
    semantic_validation = validate_planning_semantic_application(root, application)
    assert semantic_validation.ok, semantic_validation.errors
    approval_path = write_json(
        root / "50_workbench" / "planning" / "approval.json",
        build_human_planning_approval(
            root,
            application_path=application_path,
            decision="approve",
            reason="The evidence-bound architecture is acceptable.",
            approved_by="human",
        ),
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    node_decisions = build_human_node_decisions(
        root,
        bundle_path=bundle_path,
        decisions=[
            {
                "node_id": node["node_id"],
                "decision": "approve",
                "adjustment": "",
                "reason": "Keep this causal step.",
            }
            for table in bundle["plot_node_tables"]
            for node in table["nodes"]
            if node["node_kind"] == "state_change"
        ],
        decided_by="human",
    )
    decisions_path = write_json(
        root / "50_workbench" / "planning" / "node-decisions.json", node_decisions
    )

    apply_planning_bundle(
        config,
        bundle_path=bundle_path,
        application_path=application_path,
        approval_path=approval_path,
        node_decisions_path=decisions_path,
        approved_by="human",
    )

    return config, root


def test_current_planning_context_has_no_legacy_dependencies(tmp_path):
    _config, root = approved_project(tmp_path)
    assert not (root / "20_outline/chapter_cards").exists()
    assert not (root / "20_outline/chapter_plan.json").exists()
    context = load_chapter_planning_context(root, 1)
    assert context.volume_id == "volume:001"
    assert context.is_volume_start and not context.is_volume_end
    assert context.character_ids == ("character:ari", "character:mira")
    assert context.scene_ids == ("scene:arrival",)
    assert context.obligations[0]["obligation_id"] == "obligation:trust-choice"
    assert all("chapter_cards" not in row["path"] for row in context.source_files)


def test_current_planning_context_rejects_missing_or_changed_skeleton(tmp_path):
    _config, root = approved_project(tmp_path)
    path = root / "20_outline/volume_skeletons.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"][0]["title"] = "Changed without approval"
    write_json(path, data)
    with pytest.raises(ChapterContractError, match="basis_stale"):
        load_chapter_planning_context(root, 1)
    path.unlink()
    with pytest.raises(ChapterContractError, match="unreadable"):
        load_chapter_planning_context(root, 1)


def test_current_planning_context_rejects_duplicate_active_volume(tmp_path):
    _config, root = approved_project(tmp_path)
    path = root / "20_outline/volumes/vol001.json"
    write_json(path.with_name("vol002.json"), json.loads(path.read_text(encoding="utf-8")))
    with pytest.raises(ChapterContractError, match="multiple_active_volumes"):
        load_chapter_planning_context(root, 1)


def test_voice_examples_require_every_selected_filter_and_survive_workbench_absence(tmp_path):
    from longform_engine.author_voice import BANK_PATH, BANK_SCHEMA, relevant_author_voice_examples

    bank = tmp_path / BANK_PATH
    write_json(bank, {
        "schema": BANK_SCHEMA, "max_active_pairs": 12, "updated_at": "fixture", "pairs": [
            {"pair_id": name, "active": True, "pov_character_id": pov, "scene_kind": scene,
             "approved_at": name, "after": {"text": "批准的人工修改正例"}}
            for name, pov, scene in [("A", "ari", "talk"), ("B", "mira", "fight"), ("C", "ari", "fight")]
        ],
    })
    assert [item["pair_id"] for item in relevant_author_voice_examples(
        tmp_path, pov_character_ids=["ari"], scene_kind="talk"
    )] == ["A"]
    assert relevant_author_voice_examples(tmp_path, pov_character_ids=["unknown"], scene_kind="fight") == []
    assert len(relevant_author_voice_examples(tmp_path, pov_character_ids=[])) == 2


def test_non_ten_volume_end_drives_voice_refresh_and_naturalness(tmp_path):
    from longform_engine.author_voice import author_voice_requirement_reason
    from longform_engine.creative.pipeline import prose_naturalness_volume_boundary
    from tests.project_fixtures import persist_current_planning_fixture, refresh_planning_fixture_basis
    config, root = seed_project(tmp_path)
    contract = planning_bundle()["chapter_contracts"][0]
    contract.update(chapter_number=7, contract_id="contract:ch007", reader_promise_actions=[])
    persist_current_planning_fixture(root, contract)
    for relative in ("20_outline/volume_skeletons.json", "20_outline/volumes/vol001.json"):
        path = root / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        volume = payload["items"][0] if "items" in payload else payload
        volume["chapter_range"] = [1, 7]
        write_json(path, payload)
    refresh_planning_fixture_basis(root)
    assert load_chapter_planning_context(root, 7).is_volume_end
    assert author_voice_requirement_reason(root, 7) == "volume_boundary"
    assert prose_naturalness_volume_boundary(config, 7)


def test_required_expression_cast_is_not_silently_truncated_or_guessed(tmp_path):
    from longform_engine.character_expression import build_character_expression_packet
    ids = [f"character:{n}" for n in range(7)]
    write_json(tmp_path / "10_bible/characters.json", [{"id": key, "name": key} for key in ids])
    focus = {"pov_character_ids": [], "scene_kind": ""}
    packet = build_character_expression_packet(tmp_path, chapter_number=1, character_ids=ids[1:3],
                                               expression_focus=focus, tcs={})
    assert packet["pov_character_ids"] == []
    assert packet["featured_character_ids"] == ids[1:3]
    full_packet = build_character_expression_packet(tmp_path, chapter_number=1, character_ids=ids,
                                                   expression_focus=focus, tcs={})
    assert full_packet["featured_character_ids"] == ids
    from longform_engine.orchestration.pipeline import author_character_guidance
    assert len(author_character_guidance(full_packet)) == len(ids)
    with pytest.raises(ValueError, match="unknown_chapter_characters"):
        build_character_expression_packet(tmp_path, chapter_number=1, character_ids=["missing"],
                                          expression_focus=focus, tcs={})


def test_production_missing_planning_has_a_concrete_rebuild_command(tmp_path):
    from longform_engine.production import project_readiness_action
    from longform_engine.config import load_project_config
    from longform_engine.storage import init_project
    from longform_engine.orchestration import open_book
    from tests.project_fixtures import mark_project_ready
    project = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    mark_project_ready(project.root, config)
    (project.root / "20_outline/volume_skeletons.json").unlink()
    action = project_readiness_action(config, project.root)
    assert action["status"] == "planning_refresh_required"
    assert action["next_command"] == "longform-engine planning task project.yaml"


@pytest.mark.parametrize("changed_source", [
    "20_outline/chapter_intents/ch001.json",
    "10_bible/character_expression.json",
    "10_bible/style_profiles/author_voice_edit_pairs.json",
    "20_outline/volume_skeletons.json",
])
def test_review_tasks_expire_when_author_design_changes(tmp_path, changed_source):
    from longform_engine.agent_tasks import list_manifests
    from longform_engine.creative.pipeline import prose_naturalness_task, prose_naturalness_source_for_candidate
    from longform_engine.editorial import editorial_review
    from longform_engine.gates import semantic_pacing_task, semantic_review_task
    from longform_engine.gates.pipeline import gate_review_context_is_current
    from longform_engine.production import editorial_task_is_current
    from longform_engine.quality import reader_payoff_task
    from longform_engine.quality.review import reader_payoff_task_is_current
    from tests.test_reader_payoff_review import seed_payoff_project

    config, root, _ = seed_payoff_project(tmp_path)
    reader_payoff_task(config, chapter_number=1)
    semantic_pacing_task(config, chapter_number=1)
    semantic_review_task(config, chapter_number=1)
    editorial_review(config, chapter_number=1)
    naturalness = prose_naturalness_task(config, chapter_number=1)
    editorial_tasks = [row for row in list_manifests(root, chapter_number=1) if row["task_type"] == "editorial_review"]
    assert editorial_tasks
    assert all(editorial_task_is_current(root, 1, row) for row in editorial_tasks)
    assert reader_payoff_task_is_current(config, chapter_number=1)
    assert gate_review_context_is_current(root, 1, task_type="semantic_review")
    assert gate_review_context_is_current(root, 1, task_type="pacing_review")
    context = json.loads((root / "50_workbench/editorial_reviews/agent_tasks/ch001/character_editor.context.json").read_text(encoding="utf-8"))
    packet = context["source_projections"]["character_expression_packet"]
    assert packet["schema"] == "character_expression_packet_v2"
    assert packet["featured_character_ids"]
    assert "[context-evidence-incomplete]" not in json.dumps(context)
    planning = load_chapter_planning_context(root, 1)
    intent = json.loads((root / "20_outline/chapter_intents/ch001.json").read_text(encoding="utf-8"))
    review_paths = (
        "50_workbench/quality_reviews/ch001.reader_payoff.context.json",
        "50_workbench/gate_artifacts/ch001/semantic_review_context.json",
        "50_workbench/gate_artifacts/ch001/semantic_pacing_task.json",
    )
    for review_context in [context, *[
        json.loads((root / path).read_text(encoding="utf-8")) for path in review_paths
    ]]:
        assert review_context["approved_planning"]["approved_nodes"] == list(planning.nodes)
        assert review_context["approved_planning"]["semantic_obligations"] == list(planning.obligations)
        assert review_context["approved_planning"]["volume"]["volume_id"] == planning.volume_id
        assert review_context["human_chapter_intent"] == intent
    changed = root / changed_source
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text((changed.read_text(encoding="utf-8") if changed.exists() else "{}") + "\n", encoding="utf-8")
    assert not reader_payoff_task_is_current(config, chapter_number=1)
    assert not gate_review_context_is_current(root, 1, task_type="semantic_review")
    assert not gate_review_context_is_current(root, 1, task_type="pacing_review")
    assert all(not editorial_task_is_current(root, 1, row) for row in editorial_tasks)
    assert prose_naturalness_source_for_candidate(root, 1, Path(naturalness.candidate_file)) is None


def test_required_character_guidance_uses_real_prompt_budget(tmp_path):
    from tests.test_reader_payoff_review import seed_payoff_project
    from tests.project_fixtures import compile_chapter_brief_fixture
    config, root, _ = seed_payoff_project(tmp_path)
    expression_path = root / "10_bible/character_expression.json"
    expression = json.loads(expression_path.read_text(encoding="utf-8"))
    expression["character_expression_contracts"][0]["speech_register"] = "这段必要的人物表达依据需要完整保留。" * 10000
    write_json(expression_path, expression)
    with pytest.raises(ValueError, match="prompt_budget_exceeded|context.*budget|budget.*exceed"):
        compile_chapter_brief_fixture(root, config)
    assert not (root / "40_manuscript/final/ch001.md").exists()
