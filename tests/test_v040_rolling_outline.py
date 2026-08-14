import json
from hashlib import sha256
from pathlib import Path

from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    assess_chapter_direction,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.lengths import compile_length_forecast
from longform_engine.orchestration import open_book
from longform_engine.production import production_next
from longform_engine.storage import init_project
from tests.project_fixtures import (
    build_outline_candidate,
    build_outline_extension_candidate,
    mark_project_ready,
    write_json,
)


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    return config, project.root


def direction_candidate(root: Path, chapter_number: int, reasons: list[str]) -> dict:
    card = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    scene_chain = [
        {
            "scene_id": "scene_arrival",
            "location": "archive gate",
            "participants": ["lead_ari", "ally_mira"],
            "desire_collision": "Ari wants verification while Mira wants immediate pursuit.",
            "choice": "Ari spends the last safe minute checking the damaged seal.",
            "cost": "The visible suspect gains distance.",
            "turn": "The seal proves the suspect used an internal route.",
        },
        {
            "scene_id": "scene_commitment",
            "location": "witness stair",
            "participants": ["lead_ari", "ally_mira"],
            "desire_collision": "Mira demands action while Ari must share an inference he wanted to control.",
            "choice": "Ari gives Mira the route and accepts her condition.",
            "cost": "He loses sole control of the evidence.",
            "turn": "Their uneasy alliance becomes operational rather than verbal.",
        },
    ]
    common = {
        "book_goal": "Expose who controls collective memory.",
        "volume_goal": "Prove the border archive is being altered from inside.",
        "protagonist_goal": "Preserve evidence without treating Mira as a tool.",
        "scene_chain": scene_chain,
        "cast_desires": {
            "lead_ari": "Verify the physical trace before pursuit.",
            "ally_mira": "Catch the witness before the court closes the gate.",
        },
        "dialogue_ownership": "Ari narrows claims; Mira forces decisions and names costs.",
        "embodiment_plan": "Use Ari's careful handling and Mira's changing distance under pressure.",
        "interiority_function": "Expose Ari's temptation to control information immediately before his choice to share it.",
        "conflict": "Verification consumes the only safe pursuit window.",
        "information_release": "The damaged seal identifies an internal route without naming the editor.",
        "local_payoff": "A prior seal detail becomes actionable evidence.",
        "character_cost": "Ari surrenders sole control and loses pursuit time.",
        "mainline_move": "The investigation moves from outside sabotage to an internal access chain.",
        "character_arc_move": "Ari makes one bounded trust decision instead of manipulating Mira.",
        "foreshadow_move": "The false treaty thread echoes through the matching seal cut.",
        "relationship_move": "The uneasy alliance gains a shared operational obligation.",
        "ending_mode": "changed_problem",
        "main_risks": ["Too much procedural explanation could flatten the choice."],
    }
    return {
        "schema": "chapter_direction_candidate_v2",
        "chapter_number": chapter_number,
        "chapter_card_sha256": sha256(card.read_bytes()).hexdigest(),
        "trigger_reasons": reasons,
        "directions": [
            {"id": "verify_seal", "title": "Verify the seal", "chapter_duty": "Trade pursuit speed for reliable evidence.", **common},
            {
                "id": "follow_witness",
                "title": "Follow the witness",
                "chapter_duty": "Let Ari choose speed and incur an evidence debt.",
                **{**common, "character_cost": "Ari loses the seal evidence and must later admit the shortcut."},
            },
        ],
        "selection": {"direction_id": "verify_seal", "user_adjustments": {}},
    }


def test_outline_design_persists_macro_budget_and_only_one_detailed_window(tmp_path):
    config, root = seed_project(tmp_path)
    task = create_intelligence_task(config, task_type="outline_design")
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps(build_outline_candidate(config), ensure_ascii=False), encoding="utf-8")

    validation = validate_intelligence_candidate(config, task_type="outline_design", file_path=candidate)
    assert validation.ok, validation.errors
    apply_intelligence_candidate(config, task_type="outline_design", file_path=candidate, approved_by="human")

    plan = json.loads((root / "20_outline" / "chapter_plan.json").read_text(encoding="utf-8"))
    window = json.loads((root / "20_outline" / "planning_window.json").read_text(encoding="utf-8"))
    ledger = json.loads((root / "20_outline" / "foreshadowing_ledger.json").read_text(encoding="utf-8"))
    assert len(plan) == 20
    assert window["end_chapter"] == 20
    assert ledger[0]["plant"]["arc_id"] == "arc_01"
    assert ledger[0]["projection"]["authority"] == "derived_from_arc_progress"


def test_outline_extension_uses_bounded_context_and_appends_atomically(tmp_path):
    config, root = seed_project(tmp_path)
    outline_task = create_intelligence_task(config, task_type="outline_design")
    outline_file = root / outline_task.candidate_file
    outline_file.write_text(json.dumps(build_outline_candidate(config)), encoding="utf-8")
    apply_intelligence_candidate(config, task_type="outline_design", file_path=outline_file, approved_by="human")

    task = create_intelligence_task(config, task_type="outline_extension", from_chapter=21, to_chapter=40)
    manifest = load_manifest(root, task.task_id)
    strict = validate_manifest_strict(root, manifest)
    context = root / "50_workbench" / "intelligence_context" / "outline_extension.ch021-ch040.context.json"
    assert strict.ok
    assert len(manifest["input_files"]) == 2
    assert len(context.read_text(encoding="utf-8")) <= 18_000
    assert json.loads(context.read_text(encoding="utf-8"))["selection"]["full_history_exposed"] is False

    candidate = root / task.candidate_file
    candidate.write_text(json.dumps(build_outline_extension_candidate(config, 21, 40)), encoding="utf-8")
    validation = validate_intelligence_candidate(config, task_type="outline_extension", file_path=candidate)
    assert validation.ok, validation.errors
    apply_intelligence_candidate(config, task_type="outline_extension", file_path=candidate, approved_by="human")
    plan = json.loads((root / "20_outline" / "chapter_plan.json").read_text(encoding="utf-8"))
    assert [item["chapter_number"] for item in plan] == list(range(1, 41))


def test_production_next_refills_at_threshold_before_writing(tmp_path):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config, direction_applied=False)
    plan_path = root / "20_outline" / "chapter_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))[:8]
    write_json(plan_path, plan)
    window_path = root / "20_outline" / "planning_window.json"
    window = json.loads(window_path.read_text(encoding="utf-8"))
    window["end_chapter"] = 8
    write_json(window_path, window)

    action = production_next(config)
    assert action["task_type"] == "outline_extension"
    assert action["planning_window"] == {"from_chapter": 9, "to_chapter": 28, "remaining": 8}


def test_every_chapter_requires_a_human_selected_scene_direction(tmp_path):
    config, root = seed_project(tmp_path)
    mark_project_ready(root, config, direction_applied=False)
    config.data["quality"]["creative_guidance"]["mode"] = "guided"
    status = assess_chapter_direction(config, 1)
    assert status["required"] is True
    assert "guided_mode" in status["reasons"]

    task = create_intelligence_task(config, task_type="chapter_direction", chapter_number=1)
    manifest = load_manifest(root, task.task_id)
    assert manifest["output_schema"] == "chapter_direction_candidate_v2"
    assert len(manifest["input_files"]) == 2
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps(direction_candidate(root, 1, status["reasons"]), ensure_ascii=False), encoding="utf-8")
    validation = validate_intelligence_candidate(config, task_type="chapter_direction", file_path=candidate)
    assert validation.ok, validation.errors
    apply_intelligence_candidate(config, task_type="chapter_direction", file_path=candidate, approved_by="human")
    card = json.loads((root / "20_outline" / "chapter_cards" / "ch001.json").read_text(encoding="utf-8"))
    assert card["direction_selection"]["status"] == "applied"
    assert card["scene_chain"][0]["desire_collision"]
    assert card["dialogue_ownership"]


def test_two_million_character_project_keeps_outline_extension_context_bounded(tmp_path):
    config, root = seed_project(tmp_path)
    task = create_intelligence_task(config, task_type="outline_design")
    candidate = root / task.candidate_file
    initial = build_outline_candidate(config)
    candidate.write_text(json.dumps(initial, ensure_ascii=False), encoding="utf-8")
    apply_intelligence_candidate(config, task_type="outline_design", file_path=candidate, approved_by="human")

    base = initial["chapter_plan"][0]
    plan = [
        {
            **base,
            "chapter_number": number,
            "title": f"Bounded chapter {number}",
            "duty": f"Advance bounded causal step {number}.",
        }
        for number in range(1, 668)
    ]
    write_json(root / "20_outline" / "chapter_plan.json", plan)
    write_json(
        root / "20_outline" / "planning_window.json",
        {
            "schema": "rolling_outline_window_v1",
            "start_chapter": 648,
            "end_chapter": 667,
            "detailed_horizon": 20,
            "refill_threshold": 8,
        },
    )

    extension = create_intelligence_task(
        config,
        task_type="outline_extension",
        from_chapter=668,
        to_chapter=687,
    )
    manifest = load_manifest(root, extension.task_id)
    context_path = root / "50_workbench" / "intelligence_context" / "outline_extension.ch668-ch687.context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))

    assert compile_length_forecast(config.data["length"]).estimated_chapters == 667
    assert validate_manifest_strict(root, manifest).ok
    assert len(context["recent_chapter_plan"]) == 8
    assert context["recent_chapter_plan"][0]["chapter_number"] == 660
    assert context["selection"]["full_history_exposed"] is False
    assert len(context_path.read_text(encoding="utf-8")) <= 18_000
