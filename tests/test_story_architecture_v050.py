import json
from pathlib import Path

import pytest

import longform_engine.human_story_review as human_story_review_module
from longform_engine.chapter_contract import (
    stamp_chapter_contract,
    validate_chapter_contract,
)
from longform_engine.config import load_project_config
from longform_engine.editorial.pipeline import (
    SCENE_SEMANTIC_FINDING_CODES,
    cross_chapter_findings,
    editorial_review,
    editorial_team,
)
from longform_engine.human_story_review import (
    apply_human_story_review,
    create_human_story_review_task,
    human_story_review_status,
    validate_human_story_review,
)
from longform_engine.intelligence.pipeline import (
    chapter_carrier_repetition_status,
)
from longform_engine.orchestration import continue_write, finalize_chapter, open_book, submit_agent_draft
from longform_engine.quality import refresh_editorial_pattern_registry
from longform_engine.quality.status import quality_status
from longform_engine.reader_promises_v2 import (
    apply_planning_deferrals,
    load_reader_promise_ledger,
    materialize_explicit_reader_promises,
    promise_deadline_status,
    write_reader_promise_ledger,
)
from longform_engine.repair_coordination import review_barrier_status
from longform_engine.roles import load_role_registry
from longform_engine.storage import init_project
from tests.project_fixtures import (
    complete_human_author_revision,
    complete_required_quality_reviews,
    explicit_reader_promise_candidates,
    mark_project_ready,
)
from tests.test_agent_task_protocol import submit_editorial_review, write_editorial_role_result






def seed_candidate(tmp_path: Path, *, complete_human: bool = True):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    task = continue_write(config, chapter_number=1)
    draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    sentence = "林迟扑向即将合拢的山门，守门人横刀拒绝，他肩头撞上石缝才抢到半步，却不得不把唯一的铜符抛给身后的同伴。"
    draft.write_text(
        "# 第一章 山门\n\n"
        + sentence * 80
        + "\n\n门内的人接住铜符，却先关上了退路；石阶下怎会又响起那个早已死去的守门人的脚步？\n",
        encoding="utf-8",
    )
    submitted = submit_agent_draft(config, chapter_number=1, file_path=draft, agent="codex")
    assert submitted.passed
    review = editorial_review(config, chapter_number=1)
    for role in review.selected_roles:
        result_file = write_editorial_role_result(
            root / "50_workbench" / "editorial_reviews" / "results",
            chapter_number=1,
            role=role,
            verdict="pass",
            items=[],
        )
        submit_editorial_review(config, chapter_number=1, role=role, file_path=result_file)
    if complete_human:
        complete_human_author_revision(root, config, chapter_number=1)
    else:
        complete_required_quality_reviews(root, config, chapter_number=1)
    return config, root, task


def write_review(root: Path, template_file: str, *, decision: str, span_actions=None, redirect_scope="direction") -> Path:
    path = root / template_file
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["decision"] = decision
    core = {
        "scene_causality_and_key_turn_dramatized": (
            "key_turn",
            "The counteraction forces an owned choice and changes the scene condition.",
        ),
        "protagonist_agency_voice_and_emotion": (
            "character_choice_or_emotion",
            "The character owns both the decision and its visible emotional consequence.",
        ),
        "reader_gain_and_promise_progress": (
            "reader_gain",
            "The reader receives a changed condition that advances the active promise.",
        ),
        "exit_state_and_emotional_aftereffect": (
            "reader_gain",
            "The exit carries a changed practical state and an emotional aftereffect.",
        ),
    }
    for field, (kind, reason) in core.items():
        payload["dimension_coverage"][field] = {
            "status": "confirmed",
            "coverage_source": "human_core",
            "reason": reason,
            "evidence_refs": [f"candidate:{kind}:fixture"],
        }
    if decision in {"accept", "repair"}:
        draft = (root / "40_manuscript" / "draft" / "ch001.md").read_text(encoding="utf-8")
        end = min(40, len(draft))
        payload["evidence_spans"] = [
            {"start": 0, "end": end, "text": draft[:end], "kind": kind, "note": "The turn and owned choice are visible here."}
            for kind in ("key_turn", "character_choice_or_emotion", "reader_gain")
        ]
        payload["reader_gain_note"] = "The reader sees a concrete change in route, trust, and immediate risk."
    payload["annotations"] = [
        {
            "annotation_id": f"annotation-{index}",
            "start": item["start"],
            "end": item["end"],
            "text": item["text"],
            "check_id": "scene_causality_and_key_turn_dramatized",
            "severity": "P1",
            "action": item["action"],
            "intent": item["note"],
            "must_preserve": ["current chapter contract", "existing valid scene outcome"],
            "note": item["note"],
        }
        for index, item in enumerate(span_actions or [], start=1)
    ]
    if decision == "repair":
        payload["dimension_coverage"]["scene_causality_and_key_turn_dramatized"]["status"] = "repair"
    if decision == "redirect":
        payload["dimension_coverage"]["scene_causality_and_key_turn_dramatized"]["status"] = "redirect"
    payload["redirect_scope"] = redirect_scope
    payload["reason"] = "The carrier must change before another draft." if decision == "redirect" else ""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_author_markdown_is_story_brief_and_fact_inventory_stays_internal(tmp_path):
    _config, root, task = seed_candidate(tmp_path)
    markdown = (root / task.writing_task_markdown).read_text(encoding="utf-8")
    payload = json.loads((root / task.writing_task_json).read_text(encoding="utf-8"))

    assert payload["schema"] == "chapter_writing_task_v8"
    assert payload["story_brief"]["schema"] == "chapter_story_brief_v5"
    manifest = json.loads((root / payload["agent_task_manifest"]).read_text(encoding="utf-8"))
    assert [item["path"] for item in manifest["io"]["inputs"]] == [
        "50_workbench/writing_tasks/ch001.md"
    ]
    inventory_path = root / payload["internal_fact_inventory"]["path"]
    assert inventory_path.is_file()
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    categories = {item["category"] for item in inventory["facts"]}
    fact_ids = {item["id"] for item in inventory["facts"]}
    assert inventory["schema"] == "chapter_fact_inventory_v1"
    assert {"chapter_contract", "historical_evidence", "provenance"} <= categories
    assert "history.tcs" in fact_ids
    assert {"history.rag", "history.graph"} & fact_ids
    inventory_text = json.dumps(inventory, ensure_ascii=False)
    assert "promise_id" not in inventory_text
    assert "arc_simulation_ref" not in inventory_text
    assert "逐场行动" in markdown
    assert "本章正在发生" in markdown
    for forbidden in (
        "source hash", "source_hash", "事实 ID", "feedback", "pattern", "severity",
        "finding code", "promise_id", "ledger", "RAG", "Graph", "SQLite", "上下文来源",
    ):
        assert forbidden not in markdown


def test_chapter_contract_v5_rejects_old_chapter_card_aliases(tmp_path):
    from tests.test_current_planning_context import approved_project
    _config, root = approved_project(tmp_path)
    card = json.loads(
        (root / "20_outline" / "chapter_contracts" / "ch001.json").read_text(encoding="utf-8")
    )
    card["information_release"] = "legacy"
    assert validate_chapter_contract(card) == [
        "chapter_contract_v5 fields are invalid; v0.9 chapter cards are incompatible"
    ]










def test_reader_promise_deadline_warning_defer_and_blocker(tmp_path):
    from tests.test_current_planning_context import approved_project
    _config, root = approved_project(tmp_path)
    ledger = materialize_explicit_reader_promises(
        explicit_reader_promise_candidates()[:1], approved_by="human"
    )
    promise = ledger["items"][0]
    promise["payoff_window"] = {"earliest": 1, "target": 1, "latest": 2}
    promise["staged_payoffs"][0]["window"] = [1, 2]
    write_reader_promise_ledger(root, ledger)

    assert promise_deadline_status(root, chapter_number=1)["warnings"] == [
        f"promise_target_due:{promise['promise_id']}"
    ]
    apply_planning_deferrals(
        ledger,
        values=[
            {
                "promise_id": promise["promise_id"],
                "extended_latest": 3,
                "reason": "Human-approved one-chapter extension for the causal turn.",
            }
        ],
        chapter_number=2,
        approved_by="human",
    )
    write_reader_promise_ledger(root, ledger)
    deferred = load_reader_promise_ledger(root)["items"][0]
    assert deferred["payoff_window"]["latest"] == 3
    assert deferred["deferrals"][0]["approved_by"] == "human"
    assert not promise_deadline_status(root, chapter_number=3)["blockers"]
    assert promise_deadline_status(root, chapter_number=4)["blockers"] == [
        f"promise_breached:{promise['promise_id']}"
    ]




def test_scene_editor_is_mandatory_and_other_roles_are_additive(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    team = editorial_team(
        config,
        root=root,
        chapter_number=1,
        deterministic_items=[],
        risk_signals=["major_payoff_or_reveal"],
    )
    roles = {item["id"] for item in team}
    assert "scene_prose_editor" in roles
    assert "reader_experience_editor" in roles
    assert "planning_chief_editor" in roles
    scene_contract = load_role_registry().resolve(
        "editorial_review", declared_role_id="scene_prose_editor"
    )
    assert tuple(scene_contract.review_dimensions) == (
        "attempt", "counteraction", "choice", "visible_cost", "state_delta", "reader_gain"
    )
    assert {
        "REPORT_SUBSTITUTES_EVENT", "DIALOGUE_CONVEYOR", "PASSIVE_PROTAGONIST",
        "CARRIER_LABEL_LAUNDERING", "SCENE_WITHOUT_CHANGED_CONDITION", "RESTART_LOOP",
        "AGENCY_EROSION", "PAYOFF_DEFERRAL",
    } <= set(scene_contract.finding_codes)
    assert SCENE_SEMANTIC_FINDING_CODES == frozenset(scene_contract.finding_codes)


def test_fanfiction_review_contract_accepts_agency_and_emotional_ownership_findings():
    contract = load_role_registry().resolve(
        "editorial_review",
        declared_role_id="canon_fidelity_reviewer",
    )
    assert {
        "CANON_EVENT_DISPLACED",
        "CANON_CHARACTER_INSTRUMENTALIZED",
        "EMOTIONAL_OWNERSHIP_LOST",
    } <= set(contract.finding_codes)


def test_five_chapter_carrier_diagnostics_warn_and_require_human_reason(tmp_path):
    root = tmp_path / "novel"
    history = root / "30_state" / "quality" / "structure_history.jsonl"
    history.parent.mkdir(parents=True)
    records = [
        {
            "schema": "structure_observation_v2",
            "chapter_number": number,
            "primary_story_engine": "theme" if number <= 2 else "pursuit_and_leverage",
            "primary_scene_carrier": "document verification",
            "state_change_kind": "knowledge",
            "dramatic_method": "meeting verification",
            "exposition_carrier": "document verification meeting",
        }
        for number in range(1, 6)
    ]
    history.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    diagnostic = chapter_carrier_repetition_status(
        root,
        {
            "scene_carriers": ["document verification"],
            "state_change_kind": "knowledge",
            "dramatic_method": "meeting verification",
        },
    )
    findings = cross_chapter_findings(root, 1, 5)
    codes = {item["code"] for item in findings}

    assert diagnostic["warning"]
    assert diagnostic["requires_reason"]
    assert "CARRIER_REPETITION_3_OF_5" in codes
    assert "CARRIER_REPETITION_REASON_REQUIRED" in codes
    assert "SERIAL_CARRIER_REPETITION" in codes
    assert "THEME_DISPLACES_EVENT" in codes
    assert all(
        item["severity"] == "P2"
        for item in findings
        if item["code"] in {
            "CARRIER_REPETITION_3_OF_5", "CARRIER_REPETITION_REASON_REQUIRED",
            "SERIAL_CARRIER_REPETITION", "THEME_DISPLACES_EVENT",
        }
    )




def test_human_accept_is_hash_bound_and_unlocks_review_barrier(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    assert review_barrier_status(config, chapter_number=1)["status"] == "awaiting_human_story_review"
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="accept")
    validated = validate_human_story_review(config, chapter_number=1, file_path=review)
    assert validated.ok, validated.errors
    applied = apply_human_story_review(
        config,
        chapter_number=1,
        file_path=review,
        approved_by="human",
    )
    assert applied.decision == "accept"
    assert human_story_review_status(config, chapter_number=1)["status"] == "accept"
    assert review_barrier_status(config, chapter_number=1)["status"] == "ready_to_finalize"

    contract_path = root / "20_outline" / "chapter_contracts" / "ch001.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract.pop("chapter_contract_hash")
    contract["reader_value"] = "The approved relationship outcome changed after review."
    contract_path.write_text(
        json.dumps(stamp_chapter_contract(contract), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert human_story_review_status(config, chapter_number=1)["status"] == "stale"


def test_human_review_v7_freezes_revision_and_bundle_without_prefilled_human_reasons(tmp_path):
    config, root, _task = seed_candidate(tmp_path)

    task = create_human_story_review_task(config, chapter_number=1)
    payload = json.loads((root / task.template_file).read_text(encoding="utf-8"))

    assert payload["schema"] == "human_story_review_v7"
    assert payload["review_bundle_sha256"] == task.review_bundle_sha256
    assert payload["human_author_revision_sha256"] == task.human_author_revision_sha256
    assert (root / task.review_bundle_file).is_file()
    assert len(payload["dimension_coverage"]) == 10
    assert set(payload["dimension_coverage"]) == {
        "story_contract_preserved",
        "desire_opposition_and_question_clear",
        "scene_causality_and_key_turn_dramatized",
        "protagonist_agency_voice_and_emotion",
        "supporting_cast_and_relationship_logic",
        "reader_gain_and_promise_progress",
        "continuity_world_rules_and_ability_bounds",
        "pacing_information_and_carrier_effective",
        "prose_natural_and_readable",
        "exit_state_and_emotional_aftereffect",
    }
    assert all(
        not item["reason"]
        for item in payload["dimension_coverage"].values()
    )
    assert payload["annotations"] == []
    assert "checks" not in payload


def test_human_review_v7_rejects_v5_and_requires_three_accept_evidence_kinds(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = root / task.template_file
    payload = json.loads(review.read_text(encoding="utf-8"))
    payload["schema"] = "human_story_review_v5"
    review.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    rejected = validate_human_story_review(config, chapter_number=1, file_path=review)
    assert not rejected.ok
    assert any("human_story_review_v5 is rejected in v0.10" in error for error in rejected.errors)

    payload["schema"] = "human_story_review_v7"
    review.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    review = write_review(root, task.template_file, decision="accept")
    payload = json.loads(review.read_text(encoding="utf-8"))
    payload["evidence_spans"] = [
        item for item in payload["evidence_spans"] if item["kind"] != "reader_gain"
    ]
    review.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    missing = validate_human_story_review(config, chapter_number=1, file_path=review)
    assert not missing.ok
    assert "accept/repair requires human key_turn, character_choice_or_emotion, and reader_gain spans" in missing.errors


def test_human_review_v7_rejects_review_bundle_hash_drift(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = root / task.template_file
    bundle = root / task.review_bundle_file
    bundle.write_text(bundle.read_text(encoding="utf-8") + " ", encoding="utf-8")

    result = validate_human_story_review(config, chapter_number=1, file_path=review)

    assert not result.ok
    assert "review_bundle_sha256 is stale" in result.errors


def test_quality_status_reports_current_author_acceptance(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    before = quality_status(config)
    assert before["protocol_ready"] is True
    assert before["author_acceptance_ready"] is False
    assert "literary_evidence_ready" not in before

    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="accept")
    apply_human_story_review(config, chapter_number=1, file_path=review, approved_by="human")
    finalize_chapter(config, chapter_number=1, approved_by="human")

    accepted = quality_status(config)
    assert accepted["author_acceptance_ready"] is True
    assert "literary_evidence_ready" not in accepted
    assert accepted["author_acceptance"]["chapters"][0]["accepted"] is True

    decision_path = root / accepted["author_acceptance"]["chapters"][0]["decision_file"]
    decision_path.write_text(decision_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    stale = quality_status(config)
    assert stale["author_acceptance_ready"] is False
    assert any("human_accept_decision_hash_mismatch" in item for item in stale["author_acceptance"]["blockers"])


def test_human_accept_requires_story_spans_and_reader_gain_note(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="accept")
    payload = json.loads(review.read_text(encoding="utf-8"))
    payload["evidence_spans"] = []
    payload["reader_gain_note"] = ""
    review.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = validate_human_story_review(config, chapter_number=1, file_path=review)
    assert not result.ok
    assert "accept/repair requires human key_turn, character_choice_or_emotion, and reader_gain spans" in result.errors
    assert "accept requires a non-empty reader_gain_note" in result.errors


def test_human_review_rejects_each_stale_candidate_contract_and_promise_hash(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="accept")
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    contract = root / "20_outline" / "chapter_contracts" / "ch001.json"
    ledger = root / "30_state" / "reader_promise_ledger.json"

    cases = (
        (draft, lambda payload: None, "candidate_sha256 is stale", True),
        (contract, lambda payload: payload.__setitem__("reader_value", "Changed after review."), "chapter_contract_sha256 is stale", False),
        (ledger, lambda payload: payload["items"][0]["actual_evidence"].append({"chapter_number": 1, "action": "setup"}), "reader_promise_ledger_sha256 is stale", False),
    )
    for path, mutate, expected, append_text in cases:
        original = path.read_bytes()
        if append_text:
            path.write_text(path.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            mutate(payload)
            if path == contract:
                payload.pop("chapter_contract_hash")
                payload = stamp_chapter_contract(payload)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = validate_human_story_review(config, chapter_number=1, file_path=review)
        assert not result.ok
        assert expected in result.errors
        path.write_bytes(original)

    assert validate_human_story_review(config, chapter_number=1, file_path=review).ok


def test_human_repair_enters_immutable_review_bundle_and_stale_hash_fails(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    text = draft.read_text(encoding="utf-8")
    start = text.index("守门人")
    end = start + len("守门人横刀拒绝")
    review = write_review(
        root,
        task.template_file,
        decision="repair",
        span_actions=[
            {
                "start": start,
                "end": end,
                "text": text[start:end],
                "action": "expand_scene",
                "note": "The refusal is summarized; show the failed attempt and bodily consequence.",
            }
        ],
    )
    assert validate_human_story_review(config, chapter_number=1, file_path=review).ok
    apply_human_story_review(config, chapter_number=1, file_path=review, approved_by="human")
    barrier = review_barrier_status(config, chapter_number=1)
    assert barrier["status"] == "review_bundle_ready"
    assert any(item["source"] == "human_story" for item in barrier["findings"])

    draft.write_text(text + "\n新的候选变化。\n", encoding="utf-8")
    stale = validate_human_story_review(config, chapter_number=1, file_path=review)
    assert not stale.ok
    assert "candidate_sha256 is stale" in stale.errors


def test_human_redirect_uses_transaction_and_returns_to_replanning(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="redirect")
    assert validate_human_story_review(config, chapter_number=1, file_path=review).ok
    applied = apply_human_story_review(config, chapter_number=1, file_path=review, approved_by="human")

    assert applied.decision == "redirect"
    assert applied.transaction_report.endswith(".json")
    assert (root / applied.transaction_report).is_file()
    stale = json.loads((root / "30_state" / "stale_artifacts.json").read_text(encoding="utf-8"))
    assert any(
        item["artifact_path"] == "20_outline/chapter_contracts/ch001.json"
        and item["state"] == "stale"
        for item in stale["items"]
    )
    assert applied.next_command == "longform-engine production next project.yaml"


def test_outline_redirect_records_scope_and_returns_to_replanning(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(
        root,
        task.template_file,
        decision="redirect",
        redirect_scope="outline_revision",
    )
    assert validate_human_story_review(config, chapter_number=1, file_path=review).ok
    applied = apply_human_story_review(config, chapter_number=1, file_path=review, approved_by="human")

    transaction = json.loads((root / applied.transaction_report).read_text(encoding="utf-8"))
    assert transaction["metadata"]["redirect_scope"] == "outline_revision"
    assert applied.next_command == "longform-engine production next project.yaml"




def test_human_redirect_failure_restores_stale_registry_decision_and_patterns(tmp_path, monkeypatch):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="redirect")
    assert validate_human_story_review(config, chapter_number=1, file_path=review).ok
    refresh_editorial_pattern_registry(
        root,
        chapter_number=1,
        observations=[
            {
                "role_id": "scene_prose_editor",
                "finding_code": "CARRIER_LABEL_LAUNDERING",
                "severity": "P1",
                "source_path": "50_workbench/editorial_reviews/ch001.aggregate.json",
                "source_sha256": "a" * 64,
                "candidate_sha256": "b" * 64,
                "evidence_hash": "c" * 64,
            }
        ],
    )
    watched = [
        root / "20_outline" / "chapter_contracts" / "ch001.json",
        root / "20_outline" / "rolling_window.json",
        root / "40_manuscript" / "draft" / "ch001.md",
        root / "70_runtime" / "agent_tasks" / "index.json",
        root / "50_workbench" / "agent_tasks" / "events.jsonl",
        root / "50_workbench" / "editorial_patterns" / "registry.jsonl",
        root / "30_state" / "stale_artifacts.json",
        *sorted((root / "20_outline" / "arc_simulations").glob("ch*-ch*.json")),
    ]
    before = {path: path.read_bytes() for path in watched if path.is_file()}
    original_truncate = human_story_review_module.truncate_editorial_pattern_registry

    def fail_after_patterns(current_root, *, to_chapter):
        original_truncate(current_root, to_chapter=to_chapter)
        raise RuntimeError("redirect fault after patterns")

    monkeypatch.setattr(
        human_story_review_module,
        "truncate_editorial_pattern_registry",
        fail_after_patterns,
    )
    with pytest.raises(RuntimeError, match="redirect fault after patterns"):
        apply_human_story_review(config, chapter_number=1, file_path=review, approved_by="human")

    assert all(path.read_bytes() == content for path, content in before.items())
    assert not list((root / "50_workbench" / "human_story_reviews").glob("ch001.*.decision.json"))
    assert not (root / "50_workbench" / "human_story_reviews" / "ch001.latest.json").exists()
