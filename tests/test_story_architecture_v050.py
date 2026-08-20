import json
import sqlite3
from pathlib import Path

import pytest

import longform_engine.db as db_module
import longform_engine.intelligence.pipeline as intelligence_pipeline
from longform_engine.agent_tasks import list_manifests
from longform_engine.arc_simulation import ArcSimulationError, load_active_arc_simulation
from longform_engine.chapter_contract import (
    ChapterContractError,
    project_chapter_contract,
    stamp_chapter_contract,
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
from longform_engine.intelligence import (
    apply_compiled_design,
    assess_chapter_direction,
    create_intelligence_task,
)
from longform_engine.intelligence.pipeline import (
    chapter_carrier_repetition_status,
    recompute_revision_impact,
    validate_chapter_direction,
)
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.quality import refresh_editorial_pattern_registry
from longform_engine.reader_promises import (
    ReaderPromiseError,
    apply_reader_promise_actions,
    load_reader_promise_ledger,
    promise_deadline_status,
    write_reader_promise_ledger,
)
from longform_engine.repair_coordination import review_barrier_status
from longform_engine.roles import load_role_registry
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready, write_arc_simulation_fixture
from tests.test_agent_task_protocol import submit_editorial_review, write_editorial_role_result
from tests.test_quality_contract_and_creative_interaction import (
    prepare_design_delta,
    valid_direction_candidate,
    write_design_candidate,
)


def seed_direction_contract(tmp_path: Path, *, chapter_number: int = 1):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "direction")
    config = load_project_config(project.project_config)
    root = tmp_path / "direction"
    open_book(config)
    mark_project_ready(root, config, direction_applied=False)
    if chapter_number > 3:
        promise_id = "story_engine:opening_three"
        action = {
            "promise_id": promise_id,
            "intended_reader_gain": "The opening conflict produces a visible answer and changed condition.",
            "evidence_requirement": "The opening payoff is visible in the accepted chapter.",
            "defer_reason": "",
        }
        apply_reader_promise_actions(
            root,
            chapter_number=1,
            actions=[{**action, "action": "setup"}],
            final_path="40_manuscript/final/ch001.md",
            final_sha256="1" * 64,
        )
        apply_reader_promise_actions(
            root,
            chapter_number=3,
            actions=[{**action, "action": "payoff"}],
            final_path="40_manuscript/final/ch003.md",
            final_sha256="3" * 64,
        )
        window = json.loads((root / "20_outline" / "planning_window.json").read_text(encoding="utf-8"))
        write_arc_simulation_fixture(
            root,
            from_chapter=int(window["start_chapter"]),
            to_chapter=int(window["end_chapter"]),
        )
    create_intelligence_task(config, task_type="chapter_direction", chapter_number=chapter_number)
    reasons = assess_chapter_direction(config, chapter_number)["reasons"]
    return config, root, valid_direction_candidate(root, chapter_number, reasons)


def direction_errors(config, root: Path, payload: dict) -> list[str]:
    errors: list[str] = []
    validate_chapter_direction(
        config,
        root,
        payload,
        {"scope": {"chapter_number": payload["chapter_number"]}},
        errors,
    )
    return errors


def seed_candidate(tmp_path: Path):
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
    return config, root, task


def write_review(root: Path, template_file: str, *, decision: str, span_actions=None, redirect_scope="direction") -> Path:
    path = root / template_file
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["checks"] = {
        key: {"passed": decision == "accept", "reason": "Verified against the current candidate."}
        for key in payload["checks"]
    }
    payload["decision"] = decision
    if decision == "accept":
        draft = (root / "40_manuscript" / "draft" / "ch001.md").read_text(encoding="utf-8")
        end = min(40, len(draft))
        payload["evidence_spans"] = [
            {"start": 0, "end": end, "text": draft[:end], "kind": kind, "note": "The turn and owned choice are visible here."}
            for kind in ("key_turn", "character_choice_or_emotion")
        ]
        payload["reader_gain_note"] = "The reader sees a concrete change in route, trust, and immediate risk."
    payload["span_actions"] = span_actions or []
    payload["redirect_scope"] = redirect_scope
    payload["reason"] = "The carrier must change before another draft." if decision == "redirect" else ""
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_author_markdown_is_story_brief_and_fact_inventory_stays_internal(tmp_path):
    _config, root, task = seed_candidate(tmp_path)
    markdown = (root / task.writing_task_markdown).read_text(encoding="utf-8")
    payload = json.loads((root / task.writing_task_json).read_text(encoding="utf-8"))

    assert payload["schema"] == "chapter_writing_task_v4"
    assert payload["story_brief"]["schema"] == "chapter_story_brief_v2"
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
    assert {"chapter_contract", "hard_rules", "historical_evidence", "provenance"} <= categories
    assert "history.tcs" in fact_ids
    assert {"history.rag", "history.graph"} & fact_ids
    inventory_text = json.dumps(inventory, ensure_ascii=False)
    assert "promise_id" not in inventory_text
    assert "arc_simulation_ref" not in inventory_text
    assert "逐场行动" in markdown
    assert "主角现在要" in markdown
    for forbidden in (
        "source hash", "source_hash", "事实 ID", "feedback", "pattern", "severity",
        "finding code", "promise_id", "ledger", "RAG", "Graph", "SQLite", "上下文来源",
    ):
        assert forbidden not in markdown


def test_chapter_contract_v3_rejects_removed_information_release(tmp_path):
    _config, root, _payload = seed_direction_contract(tmp_path)
    card = json.loads(
        (root / "20_outline" / "chapter_cards" / "ch001.json").read_text(encoding="utf-8")
    )
    card["information_release"] = "legacy"
    with pytest.raises(ChapterContractError, match="removed_alias_present:information_release"):
        project_chapter_contract(card)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("immediate_desire", ""),
        ("opposition_force", ""),
        ("key_failure", ""),
        ("irreversible_choice", ""),
        ("cost", ""),
        ("state_change_kind", ""),
        ("scene_carriers", []),
    ],
)
def test_direction_rejects_missing_story_pressure(field, value, tmp_path):
    config, root, payload = seed_direction_contract(tmp_path)
    payload["selected_direction"][field] = value
    errors = direction_errors(config, root, payload)
    assert errors
    assert any(field in error for error in errors)


def test_direction_outcome_change_requires_outline_revision(tmp_path):
    config, root, payload = seed_direction_contract(tmp_path)
    payload["selected_direction"]["chapter_turn"] = "A different long-term outcome replaces the approved result."
    errors = direction_errors(config, root, payload)
    assert any("outside chapter-direction authority" in error for error in errors)
    assert any("outline_revision" in error for error in errors)


def test_direction_requires_current_causal_simulation_basis(tmp_path):
    config, root, _payload = seed_direction_contract(tmp_path)
    simulation, _path, _hash = load_active_arc_simulation(root, chapter_number=1)
    assert simulation["status"] == "approved"

    characters = root / "60_rag" / "memory" / "characters" / "lead_ari.json"
    characters.parent.mkdir(parents=True, exist_ok=True)
    characters.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "memory_type": "character_current_view",
                "character_id": "lead_ari",
                "current_goal": "The semantic state now carries a changed private goal.",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    status = assess_chapter_direction(config, 1)
    assert status["status"] == "arc_simulation_required"
    assert any("arc_causal_simulation_stale" in reason for reason in status["reasons"])
    with pytest.raises(ArcSimulationError, match="stale"):
        load_active_arc_simulation(root, chapter_number=1)


def test_direction_rejects_missing_or_noncovering_causal_simulation(tmp_path):
    config, root, _payload = seed_direction_contract(tmp_path)
    simulation_path = next((root / "20_outline" / "arc_simulations").glob("ch*-ch*.json"))
    original = simulation_path.read_bytes()
    simulation_path.unlink()
    missing = assess_chapter_direction(config, 1)
    assert missing["status"] == "arc_simulation_required"
    assert any("missing" in reason for reason in missing["reasons"])

    simulation_path.write_bytes(original)
    simulation = json.loads(simulation_path.read_text(encoding="utf-8"))
    simulation["from_chapter"] = 2
    simulation_path.write_text(json.dumps(simulation, ensure_ascii=False, indent=2), encoding="utf-8")
    noncovering = assess_chapter_direction(config, 1)
    assert noncovering["status"] == "arc_simulation_required"
    assert any("missing" in reason for reason in noncovering["reasons"])


def test_reader_promise_deadline_warning_defer_and_blocker(tmp_path):
    _config, root, _payload = seed_direction_contract(tmp_path)
    ledger = load_reader_promise_ledger(root)
    promise = ledger["items"][0]
    promise["payoff_window"] = {"earliest": 1, "target": 1, "latest": 2}
    write_reader_promise_ledger(root, ledger)

    assert promise_deadline_status(root, chapter_number=1)["warnings"] == [
        f"promise_target_due:{promise['promise_id']}"
    ]
    with pytest.raises(ReaderPromiseError, match="reader_promise_transition_invalid"):
        apply_reader_promise_actions(
            root,
            chapter_number=1,
            actions=[
                {
                    "promise_id": promise["promise_id"],
                    "action": "payoff",
                    "intended_reader_gain": "A planned promise cannot be paid before it is established.",
                    "evidence_requirement": "Show the setup before payoff.",
                    "defer_reason": "",
                }
            ],
            final_path="40_manuscript/final/ch001.md",
            final_sha256="e" * 64,
        )
    apply_reader_promise_actions(
        root,
        chapter_number=2,
        actions=[
            {
                "promise_id": promise["promise_id"],
                "action": "defer",
                "intended_reader_gain": "The delay becomes a visible new pressure.",
                "evidence_requirement": "Show the cost of delaying the payoff.",
                "defer_reason": "Human-approved one-chapter extension for the causal turn.",
            }
        ],
        final_path="40_manuscript/final/ch002.md",
        final_sha256="f" * 64,
    )
    deferred = load_reader_promise_ledger(root)["items"][0]
    assert deferred["payoff_window"]["latest"] == 3
    assert deferred["deferrals"][0]["approved_by"] == "human"
    assert not promise_deadline_status(root, chapter_number=3)["blockers"]
    assert promise_deadline_status(root, chapter_number=4)["blockers"] == [
        f"promise_breached:{promise['promise_id']}"
    ]


def test_fanfiction_new_long_term_fact_cannot_apply_as_direction(tmp_path):
    config, root, payload = seed_direction_contract(tmp_path)
    config.data["creation"]["mode"] = "fanfiction"
    payload["selected_direction"].update(
        {
            "protected_canon_outcomes": ["The canon character keeps ownership of the decisive choice."],
            "changed_scene_means": "The same result is reached through a rescue instead of a council scene.",
            "canon_character_agency": "The canon character refuses, chooses, and bears the emotional consequence.",
            "new_long_term_facts": ["A new permanent faction now controls the route."],
            "outline_revision_required": False,
        }
    )
    errors = direction_errors(config, root, payload)
    assert any("long-term facts must require outline revision" in error for error in errors)


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


def test_four_of_five_carrier_repetition_requires_and_accepts_human_reason(tmp_path):
    config, root, payload = seed_direction_contract(tmp_path, chapter_number=5)
    selected = payload["selected_direction"]
    history = root / "30_state" / "quality" / "structure_history.jsonl"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_text(
        "".join(
            json.dumps(
                {
                    "schema": "structure_observation_v2",
                    "chapter_number": number,
                    "primary_story_engine": selected["primary_story_engine"],
                    "primary_scene_carrier": selected["scene_carriers"][0],
                    "state_change_kind": selected["state_change_kind"],
                    "dramatic_method": selected["dramatic_method"],
                    "exposition_carrier": selected["exposition_carrier"],
                }
            )
            + "\n"
            for number in range(1, 5)
        ),
        encoding="utf-8",
    )

    errors = direction_errors(config, root, payload)
    assert any("repetition_reason" in error for error in errors)

    payload["selection"]["repetition_reason"] = (
        "This legal-procedure sequence keeps the carrier, but a different character owns the refusal "
        "and the state change moves from evidence access to relationship liability."
    )
    assert direction_errors(config, root, payload) == []


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

    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["relationship_move"] = "The approved relationship outcome changed after review."
    stamp_chapter_contract(card)
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert human_story_review_status(config, chapter_number=1)["status"] == "stale"


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
    assert "accept requires key_turn and character_choice_or_emotion evidence spans" in result.errors
    assert "accept requires a non-empty reader_gain_note" in result.errors


def test_human_review_rejects_each_stale_candidate_contract_promise_and_simulation_hash(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="accept")
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    ledger = root / "30_state" / "reader_promise_ledger.json"
    simulation = next((root / "20_outline" / "arc_simulations").glob("ch*-ch*.json"))

    cases = (
        (draft, lambda payload: None, "candidate_sha256 is stale", True),
        (card, lambda payload: payload.__setitem__("relationship_move", "Changed after review."), "chapter_contract_sha256 is stale", False),
        (ledger, lambda payload: payload.__setitem__("updated_at", "stale-review-hash"), "reader_promise_ledger_sha256 is stale", False),
        (simulation, lambda payload: payload["offstage_actions"].append("A new offstage move appears."), "arc_causal_simulation_sha256 is stale", False),
    )
    for path, mutate, expected, append_text in cases:
        original = path.read_bytes()
        if append_text:
            path.write_text(path.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
            mutate(payload)
            if path == card:
                stamp_chapter_contract(payload)
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


def test_human_redirect_uses_transaction_and_returns_to_direction(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, task.template_file, decision="redirect")
    assert validate_human_story_review(config, chapter_number=1, file_path=review).ok
    applied = apply_human_story_review(config, chapter_number=1, file_path=review, approved_by="human")

    assert applied.decision == "redirect"
    assert applied.transaction_report.endswith(".json")
    assert (root / applied.transaction_report).is_file()
    assert assess_chapter_direction(config, 1)["required"] is True
    assert "chapter_direction" in applied.next_command


def test_outline_redirect_blocks_direction_until_outline_revision(tmp_path):
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

    direction = assess_chapter_direction(config, 1)
    assert direction["status"] == "outline_revision_required"
    assert "outline_revision" in applied.next_command
    with pytest.raises(ValueError, match="outline_revision"):
        create_intelligence_task(config, task_type="chapter_direction", chapter_number=1)


def test_outline_revision_transaction_invalidates_patterns_tasks_simulation_and_sqlite(
    tmp_path,
    monkeypatch,
):
    config, root, _task = seed_candidate(tmp_path)
    promise_before = load_reader_promise_ledger(root)["items"][0]
    extended_latest = int(promise_before["payoff_window"]["latest"]) + 5
    refresh_editorial_pattern_registry(
        root,
        chapter_number=1,
        observations=[
            {
                "role_id": "scene_prose_editor",
                "finding_code": "RESTART_LOOP",
                "severity": "P1",
                "source_path": "50_workbench/editorial_reviews/ch001.aggregate.json",
                "source_sha256": "a" * 64,
                "candidate_sha256": "b" * 64,
                "evidence_hash": "c" * 64,
            }
        ],
    )
    chapters, artifacts = recompute_revision_impact(root, 1, 1)
    payload = {
        "schema": "outline_revision_candidate_v1",
        "from_chapter": 1,
        "to_chapter": 1,
        "change_summary": "Replace the current carrier while preserving the approved chapter outcome.",
        "impact": {"stale_chapters": chapters, "stale_artifacts": artifacts},
        "replacements": {
            "book_outline_markdown": (
                (root / "20_outline" / "book_outline.md").read_text(encoding="utf-8").rstrip()
                + "\n\nThe first carrier now turns through an active refusal.\n"
            ),
            "reader_promise_deferrals": [
                {
                    "promise_id": promise_before["promise_id"],
                    "extended_latest": extended_latest,
                    "reason": "Human-approved causal delay after the carrier revision.",
                }
            ],
        },
    }
    task = create_intelligence_task(
        config,
        task_type="outline_revision",
        from_chapter=1,
        to_chapter=1,
    )
    document = root / task.candidate_file
    write_design_candidate(document, "outline_revision", payload)
    delta = prepare_design_delta(config, root, "outline_revision", document, payload)
    watched = [
        root / "20_outline" / "book_outline.md",
        root / "20_outline" / "chapter_cards" / "ch001.json",
        root / "50_workbench" / "writing_tasks" / "ch001.json",
        root / "50_workbench" / "editorial_patterns" / "registry.jsonl",
        root / "50_workbench" / "agent_tasks" / "agent_task_index.json",
        *sorted((root / "20_outline" / "arc_simulations").glob("ch*-ch*.json")),
    ]
    before = {path: path.read_bytes() for path in watched if path.is_file()}
    databases = sorted((root / "70_runtime" / "db").glob("*.sqlite"))
    database_before = {}
    for database in databases:
        with sqlite3.connect(database) as connection:
            database_before[database] = tuple(connection.iterdump())
    real_sync = intelligence_pipeline.sync_database

    def fail_after_sqlite(current_config):
        real_sync(current_config)
        raise RuntimeError("outline revision fault after sqlite")

    monkeypatch.setattr(intelligence_pipeline, "sync_database", fail_after_sqlite)
    with pytest.raises(RuntimeError, match="outline revision fault after sqlite"):
        apply_compiled_design(
            config,
            task_type="outline_revision",
            document_path=document,
            delta_path=delta,
            approved_by="human",
        )
    mismatched = [
        path.relative_to(root).as_posix()
        for path, content in before.items()
        if path.read_bytes() != content
    ]
    assert mismatched == []
    for database, dump in database_before.items():
        with sqlite3.connect(database) as connection:
            assert tuple(connection.iterdump()) == dump
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)

    monkeypatch.setattr(intelligence_pipeline, "sync_database", real_sync)
    result = apply_compiled_design(
        config,
        task_type="outline_revision",
        document_path=document,
        delta_path=delta,
        approved_by="human",
    )

    transaction = json.loads((root / result.transaction_report).read_text(encoding="utf-8"))
    assert transaction["status"] == "applied"
    assert "70_runtime/db" in transaction["touched_paths"]
    assert not (root / "50_workbench" / "editorial_patterns" / "registry.jsonl").read_text(
        encoding="utf-8"
    ).strip()
    writing_task = json.loads(
        (root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8")
    )
    assert writing_task["status"] == "stale"
    assert all(
        json.loads(path.read_text(encoding="utf-8"))["status"] == "stale"
        for path in (root / "20_outline" / "arc_simulations").glob("ch*-ch*.json")
    )
    promise_after = load_reader_promise_ledger(root)["items"][0]
    assert promise_after["payoff_window"]["latest"] == extended_latest
    assert promise_after["deferrals"][-1]["approved_by"] == "human"
    affected_tasks = [item for item in list_manifests(root, chapter_number=1)]
    assert affected_tasks
    assert all(item["status"] == "superseded" for item in affected_tasks)


def test_human_redirect_failure_restores_card_decision_and_sqlite(tmp_path, monkeypatch):
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
        root / "20_outline" / "chapter_cards" / "ch001.json",
        root / "20_outline" / "chapter_plan.json",
        root / "40_manuscript" / "draft" / "ch001.md",
        root / "70_runtime" / "agent_tasks" / "index.json",
        root / "50_workbench" / "agent_tasks" / "events.jsonl",
        root / "50_workbench" / "editorial_patterns" / "registry.jsonl",
        *sorted((root / "20_outline" / "arc_simulations").glob("ch*-ch*.json")),
    ]
    before = {path: path.read_bytes() for path in watched if path.is_file()}
    databases = sorted((root / "70_runtime" / "db").glob("*.sqlite"))
    database_before = {}
    for database in databases:
        with sqlite3.connect(database) as connection:
            database_before[database] = tuple(connection.iterdump())
    original_sync = db_module.sync_database

    def fail_after_sqlite(current_config):
        original_sync(current_config)
        raise RuntimeError("redirect fault after sqlite")

    monkeypatch.setattr(db_module, "sync_database", fail_after_sqlite)
    with pytest.raises(RuntimeError, match="redirect fault after sqlite"):
        apply_human_story_review(config, chapter_number=1, file_path=review, approved_by="human")

    assert all(path.read_bytes() == content for path, content in before.items())
    for database, dump in database_before.items():
        with sqlite3.connect(database) as connection:
            assert tuple(connection.iterdump()) == dump
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    assert not list((root / "50_workbench" / "human_story_reviews").glob("ch001.*.decision.json"))
    assert not (root / "50_workbench" / "human_story_reviews" / "ch001.latest.json").exists()
