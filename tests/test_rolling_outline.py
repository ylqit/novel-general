import json
from hashlib import sha256
from pathlib import Path

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_DOCUMENT_SCHEMA,
    DESIGN_REQUIRED_HEADINGS,
)
from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.intelligence import (
    apply_compiled_design,
    approve_design_document,
    assess_chapter_direction,
    create_design_compile_task,
    create_intelligence_task,
    validate_design_compile_delta,
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


def write_design_candidate(root: Path, task, task_type: str, payload: dict) -> Path:
    candidate = root / task.candidate_file
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
    candidate.write_text(f"# {task_type} 设计文档\n\n" + "\n".join(sections), encoding="utf-8")
    control = validate_production_agent_result(
        root,
        load_manifest(root, task.manifest_file),
        result_file=candidate,
    )
    assert control.ok, control.normalization.errors
    return candidate


def apply_design_candidate(config, root: Path, task_type: str, candidate: Path, payload: dict):
    approval = validate_intelligence_candidate(config, task_type=task_type, file_path=candidate)
    assert approval.ok, approval.errors
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
    validation = validate_design_compile_delta(
        config,
        task_type=task_type,
        document_path=candidate,
        delta_path=delta,
    )
    assert validation.ok, validation.errors
    return apply_compiled_design(
        config,
        task_type=task_type,
        document_path=candidate,
        delta_path=delta,
        approved_by="human",
    )


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
        "featured_character_ids": ["lead_ari", "ally_mira"],
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
        "canon_refs": [],
        "world_rule_refs": [],
        "foreshadow_refs": [],
        "forbidden_reveals": ["identity of the archive editor"],
    }
    selected = {
        "id": "verify_seal",
        "title": "Verify the seal",
        "chapter_duty": "Trade pursuit speed for reliable evidence.",
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
        "selection": {"direction_id": "verify_seal", "user_adjustments": {}},
        "canonical_refs": selected["canon_refs"],
        "introduced_elements": [],
    }


def test_outline_design_persists_macro_budget_and_only_one_detailed_window(tmp_path):
    config, root = seed_project(tmp_path)
    task = create_intelligence_task(config, task_type="outline_design")
    payload = build_outline_candidate(config)
    candidate = write_design_candidate(root, task, "outline_design", payload)

    validation = validate_intelligence_candidate(config, task_type="outline_design", file_path=candidate)
    assert validation.ok, validation.errors
    apply_design_candidate(config, root, "outline_design", candidate, payload)

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
    outline_payload = build_outline_candidate(config)
    outline_file = write_design_candidate(
        root,
        outline_task,
        "outline_design",
        outline_payload,
    )
    apply_design_candidate(config, root, "outline_design", outline_file, outline_payload)

    task = create_intelligence_task(config, task_type="outline_extension", from_chapter=21, to_chapter=40)
    manifest = load_manifest(root, task.task_id)
    strict = validate_manifest_strict(root, manifest)
    context = root / "50_workbench" / "intelligence_context" / "outline_extension.ch021-ch040.context.json"
    assert strict.ok
    assert len(manifest["io"]["inputs"]) == 2
    context_payload = json.loads(context.read_text(encoding="utf-8"))
    assert context_payload["selection"]["full_history_exposed"] is False
    assert context_payload["selection"]["budget_profile"] == "standard"
    assert context_payload["selection"]["estimated_units"] > 0

    extension_payload = build_outline_extension_candidate(config, 21, 40)
    candidate = write_design_candidate(
        root,
        task,
        "outline_extension",
        extension_payload,
    )
    validation = validate_intelligence_candidate(config, task_type="outline_extension", file_path=candidate)
    assert validation.ok, validation.errors
    apply_design_candidate(config, root, "outline_extension", candidate, extension_payload)
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
    status = assess_chapter_direction(config, 1)
    assert status["required"] is True
    assert "guided_mode" in status["reasons"]

    task = create_intelligence_task(config, task_type="chapter_direction", chapter_number=1)
    manifest = load_manifest(root, task.task_id)
    assert manifest["io"]["output"]["protocol"] == DESIGN_DOCUMENT_SCHEMA
    assert len(manifest["io"]["inputs"]) == 2
    direction_payload = direction_candidate(root, 1, status["reasons"])
    candidate = write_design_candidate(
        root,
        task,
        "chapter_direction",
        direction_payload,
    )
    validation = validate_intelligence_candidate(config, task_type="chapter_direction", file_path=candidate)
    assert validation.ok, validation.errors
    apply_design_candidate(config, root, "chapter_direction", candidate, direction_payload)
    card = json.loads((root / "20_outline" / "chapter_cards" / "ch001.json").read_text(encoding="utf-8"))
    assert card["direction_selection"]["status"] == "applied"
    assert card["scene_chain"][0]["desire_collision"]
    assert card["dialogue_ownership"]


def test_two_million_character_project_keeps_outline_extension_context_bounded(tmp_path):
    config, root = seed_project(tmp_path)
    task = create_intelligence_task(config, task_type="outline_design")
    initial = build_outline_candidate(config)
    candidate = write_design_candidate(root, task, "outline_design", initial)
    apply_design_candidate(config, root, "outline_design", candidate, initial)

    base = initial["chapter_plan"][0]
    plan = [
        {
            **base,
            "chapter_number": number,
            "title": f"Bounded chapter {number}",
            "chapter_duty": f"Advance bounded causal step {number}.",
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
    assert context["selection"]["budget_profile"] == "standard"
    assert context["selection"]["estimated_units"] > 0
