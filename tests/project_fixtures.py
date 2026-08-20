import json
from hashlib import sha256
from pathlib import Path

import yaml

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import CANONICAL_DELTA_SCHEMA
from longform_engine.agent_tasks import load_manifest
from longform_engine.chapter_contract import stamp_chapter_contract
from longform_engine.arc_simulation import (
    current_basis_hashes,
    mark_overlapping_arc_simulations_stale,
    write_arc_causal_simulation,
)
from longform_engine.semantic import chapter_close, semantic_apply, semantic_task
from longform_engine.semantic.pipeline import active_planned_thread_ids, foreshadow_state_threads, planned_threads
from longform_engine.lengths import compile_length_forecast
from longform_engine.reader_promises import (
    merge_planned_reader_promises,
    write_reader_promise_ledger,
)
from longform_engine.quality import compact_effective_quality_contract, compile_effective_quality_contract


def story_engine_contract() -> dict:
    return {
        "schema": "story_engine_contract_v1",
        "reader_fantasy": "Outthink a stronger order and turn each costly discovery into leverage.",
        "repeatable_action_loop": "Pursue a live lead, meet resistance, choose a cost, and act on the changed situation.",
        "progression_loop": "Evidence becomes access, allies, status, and stronger investigative choices.",
        "relationship_loop": "Trust changes only when characters choose, refuse, rescue, betray, or pay for one another.",
        "mystery_or_question_loop": "Each local answer changes the long question instead of merely restating it.",
        "expected_payoffs": {
            "opening_three": "A first usable clue, a costly alliance, and proof that the archive is actively changing.",
            "early_serial": "The protagonist converts evidence into access and defeats one institutional counterplay.",
            "volume_end": "One controller is exposed while the cost and scale of memory correction widen.",
        },
        "carrier_palette": ["pursuit", "rescue", "negotiation", "infiltration", "training", "relationship conflict"],
        "theme_carrier_limits": "Theme may explain consequences but records, notices, and meetings may not monopolize events.",
    }


def build_arc_simulation_candidate(
    root: Path,
    *,
    from_chapter: int,
    to_chapter: int,
    characters: list[dict] | None = None,
) -> dict:
    """Build one complete human-approved causal planning window for integration tests."""

    if characters is None:
        loaded = json.loads((root / "10_bible" / "characters.json").read_text(encoding="utf-8"))
        characters = [item for item in loaded if isinstance(item, dict)]
    characters = [
        {
            **item,
            "id": str(item.get("id") or f"fixture_character_{index}"),
            "goal": str(item.get("goal") or "Protect the current causal objective."),
            "flaw": str(item.get("flaw") or "Withholds trust under pressure."),
        }
        for index, item in enumerate(characters, start=1)
    ]
    while len(characters) < 2:
        number = len(characters) + 1
        characters.append(
            {
                "id": "lead_ari" if number == 1 else "ally_mira",
                "goal": "Protect the route." if number == 1 else "Reach the witness first.",
                "flaw": "Distrusts allies." if number == 1 else "Moves before consensus.",
            }
        )
    participants = [str(item["id"]) for item in characters[:2]]
    return {
        "schema": "arc_causal_simulation_v1",
        "from_chapter": from_chapter,
        "to_chapter": to_chapter,
        "basis_hashes": current_basis_hashes(root),
        "protagonist_goal": str(characters[0]["goal"]),
        "opposition_agenda": "Seal the altered archive route before the contradiction becomes public.",
        "character_drives": [
            {
                "character_id": str(item["id"]),
                "private_goal": str(item["goal"]),
                "refusal_point": f"Refuses a choice that repeats {str(item['flaw']).lower()} without a visible cost.",
                "offscreen_intent": "Pursue one private lead while the other character acts.",
            }
            for item in characters[:2]
        ],
        "knowledge_boundaries": ["The protagonist knows the route changed, but not who controls the final editor."],
        "offstage_actions": ["The saboteur closes one route after every visible pursuit."],
        "resource_shifts": ["Each verified clue costs time, access, or trust."],
        "relationship_shifts": ["Trust can change only through costly mutual choices."],
        "collision_points": [
            {
                "chapter_number": number,
                "participants": participants,
                "collision": "Verification and speed demand incompatible actions.",
                "required_change": "The choice changes access or trust before chapter exit.",
            }
            for number in range(from_chapter, to_chapter + 1)
        ],
        "causal_obligations": [
            {
                "chapter_number": number,
                "cause": "The prior clue exposes a route the saboteur can close.",
                "pressure": "The protagonist must act before verification is complete.",
                "choice": "The protagonist shares control of the clue.",
                "consequence": "The suspect gains distance while the alliance becomes binding.",
            }
            for number in range(from_chapter, to_chapter + 1)
        ],
        "approved_by": "human",
        "status": "approved",
    }


def write_arc_simulation_fixture(
    root: Path,
    *,
    from_chapter: int,
    to_chapter: int,
    characters: list[dict] | None = None,
) -> Path:
    """Write one current causal planning window without running the task workflow."""

    mark_overlapping_arc_simulations_stale(
        root,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
    )
    return write_arc_causal_simulation(
        root,
        build_arc_simulation_candidate(
            root,
            from_chapter=from_chapter,
            to_chapter=to_chapter,
            characters=characters,
        ),
    )


def refresh_arc_simulation_fixture(root: Path) -> Path:
    """Approve a current planning window and rebind already approved fixture cards."""

    window = json.loads(
        (root / "20_outline" / "planning_window.json").read_text(encoding="utf-8")
    )
    start = int(window["start_chapter"])
    end = int(window["end_chapter"])
    simulation_path = write_arc_simulation_fixture(
        root,
        from_chapter=start,
        to_chapter=end,
    )
    simulation_ref = {
        "path": simulation_path.relative_to(root).as_posix(),
        "sha256": sha256(simulation_path.read_bytes()).hexdigest(),
        "from_chapter": start,
        "to_chapter": end,
    }
    for card_path in sorted((root / "20_outline" / "chapter_cards").glob("ch*.json")):
        card = json.loads(card_path.read_text(encoding="utf-8"))
        chapter_number = int(card.get("chapter_number") or 0)
        selection = card.get("direction_selection")
        if not start <= chapter_number <= end or not isinstance(selection, dict):
            continue
        if selection.get("status") != "applied":
            continue
        card["arc_simulation_ref"] = simulation_ref
        stamp_chapter_contract(card)
        write_json(card_path, card)
    return simulation_path


def mark_project_ready(
    root: Path,
    config,
    *,
    preserve_existing_characters: bool = False,
    direction_applied: bool = True,
) -> None:
    """Seed canonical book/outline state for tests that start after human apply."""

    # Minimal chapter-flow fixtures omit optional milestone Agent reviews.
    # Dedicated semantic/fanfiction tests opt into those reviews explicitly.
    config.data.setdefault("quality", {})["semantic_review_milestones"] = []
    config.data["quality"]["semantic_review_boundaries"] = False
    config.data["quality"]["profile"]["strictness"] = "light"
    config.data.setdefault("editorial", {})["review_mode"] = "off"
    config.data.setdefault("semantic", {})["allow_fallback"] = True
    config.data["semantic"].setdefault("vector_store", {})["backend"] = "local_sqlite"
    config.data["semantic"]["profile"] = "local-hash"
    project_yaml = (root / "project.yaml").resolve()
    if config.path is not None and config.path.resolve() == project_yaml and project_yaml.is_file():
        payload = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
        payload.setdefault("quality", {})["semantic_review_milestones"] = []
        payload["quality"]["semantic_review_boundaries"] = False
        payload["quality"]["profile"]["strictness"] = "light"
        payload.setdefault("editorial", {})["review_mode"] = "off"
        payload.setdefault("semantic", {})["allow_fallback"] = True
        payload["semantic"].setdefault("vector_store", {})["backend"] = "local_sqlite"
        payload["semantic"]["profile"] = "local-hash"
        project_yaml.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    forecast = compile_length_forecast(config.data["length"])
    characters = [
        {
            "id": "lead_ari",
            "name": "Ari",
            "goal": "Protect the border archive.",
            "flaw": "Distrusts allies.",
            "arc_stages": ["isolated", "tested alliance", "earned trust"],
        },
        {
            "id": "ally_mira",
            "name": "Mira",
            "goal": "Expose the false treaty.",
            "flaw": "Takes reckless risks.",
            "arc_stages": ["outsider", "uneasy ally", "trusted partner"],
        },
    ]
    if preserve_existing_characters:
        existing_path = root / "10_bible" / "characters.json"
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            existing = []
        if isinstance(existing, list) and existing:
            characters = []
            for item in existing:
                if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                    continue
                enriched = dict(item)
                enriched.setdefault("goal", "Protect the current evidence chain.")
                enriched.setdefault("flaw", "Misjudges an ally under pressure.")
                enriched.setdefault("arc_stages", ["guarded", "tested", "changed"])
                characters.append(enriched)
            if len(characters) == 1:
                characters.append(
                    {
                        "id": "support_mira",
                        "name": "Mira",
                        "goal": "Verify the second witness.",
                        "flaw": "Moves before consensus.",
                        "arc_stages": ["outsider", "tested ally", "trusted ally"],
                    }
                )
    relationships = [
        {
            "id": "rel_ari_mira",
            "source_id": "lead_ari",
            "target_id": "ally_mira",
            "type": "alliance",
            "stage": "uneasy",
        }
    ]
    if len(characters) >= 2:
        relationships = [
            {
                "id": "rel_primary_pair",
                "source_id": characters[0]["id"],
                "target_id": characters[1]["id"],
                "type": "alliance",
                "stage": "uneasy",
            }
        ]
    brief = {
        "target_audience": "Chinese longform serial readers.",
        "writing_style": "Concrete, continuous, evidence-led prose.",
        "automation_level": "agent_skill with human approval for canonical apply.",
        "target_scale": f"{forecast.target_total_characters} content characters.",
        "story_profile": config.data["story_profile"],
        "story_engine_contract": story_engine_contract(),
        "design_decisions": {
            "core_hook": "A border clerk discovers history is being edited overnight.",
            "world_rule": "Every supernatural correction erases a witnessed memory.",
            "protagonist_desire": "Preserve the archive and the people recorded in it.",
            "long_conflict": "The court needs controlled forgetting to preserve its rule.",
            "volume_escalation": "Each volume widens the cost from one town to the realm.",
            "ending_boundary": "The ending must resolve who controls collective memory.",
        },
        "reader_contract": {"core_promise": "Evidence-led mystery and costly growth."},
        "core_taboo": ["Do not reveal the final editor before the last volume."],
        "status": "confirmed",
    }
    outline = build_outline_candidate(config, characters=characters)
    story_arcs = outline["story_arcs"]
    volumes = outline["volumes"]
    chapter_plan = outline["chapter_plan"]
    planning_window = outline["planning_window"]
    ledger = [
        {
            **item,
            "plant_chapter": 1,
            "payoff_window": [max(2, forecast.estimated_chapters - 2), forecast.estimated_chapters],
        }
        for item in outline["foreshadowing_ledger"]
    ]
    write_json(root / "10_bible" / "creative_brief.json", brief)
    decisions = {
        "schema": "book_ideation_decisions_v1",
        "dimensions": [
            "target_reader_and_reading_context",
            "core_hook",
            "world_core_rule",
            "protagonist_desire_and_flaw",
            "long_conflict",
            "volume_escalation",
            "ending_boundary",
            "taboos_and_unwanted_tropes",
        ],
        "decisions": {
            "target_reader_and_reading_context": "Chinese longform serial readers seeking evidence-led mystery.",
            "core_hook": "A border clerk discovers history is being edited overnight.",
            "world_core_rule": "Every supernatural correction erases a witnessed memory.",
            "protagonist_desire_and_flaw": "Ari protects the archive but distrusts allies.",
            "long_conflict": "The court depends on controlled forgetting.",
            "volume_escalation": "Each volume widens the cost from one town to the realm.",
            "ending_boundary": "Resolve who controls collective memory.",
            "taboos_and_unwanted_tropes": "No premature final reveal or cost-free correction.",
        },
        "rounds": [],
        "complete": True,
    }
    write_json(root / "10_bible" / "creative_decisions.json", decisions)
    (root / "10_bible" / "world.md").write_text("# World\n\nMemory edits always leave physical evidence.\n", encoding="utf-8")
    (root / "10_bible" / "power_system.md").write_text("# Power\n\nEvery correction consumes a witnessed memory.\n", encoding="utf-8")
    write_json(root / "10_bible" / "characters.json", characters)
    write_json(root / "10_bible" / "relationships.json", relationships)
    write_json(
        root / "10_bible" / "character_expression.json",
        {
            "schema": "character_expression_profile_v1",
            "narrative_expression_profile": {
                "narrative_distance": "close",
                "expression_mode": "balanced",
                "description_density": "selective",
                "dialogue_mode": "balanced",
                "voice_separation": "clear",
                "ensemble_mode": "dual",
            },
            "character_expression_contracts": [
                {
                    "character_id": item["id"],
                    "perception_bias": f"Notices evidence through {item['goal'].lower()}",
                    "decision_bias": f"Acts against the pressure created by {item['flaw'].lower()}",
                    "speech_register": f"Uses concrete claims shaped by {item['goal'].lower()}",
                    "conversation_tactics": ["asks for one observable fact", "names one immediate cost"],
                    "emotional_leaks": ["changes physical distance before admitting fear"],
                    "physical_presence": "Carries tension through posture, hands, and use of space.",
                    "social_masks": ["competent ally"],
                    "private_wants": item["goal"],
                    "contradictions": f"Wants {item['goal'].lower()} but is limited by {item['flaw'].lower()}",
                    "voice_examples": [],
                    "contrast_with": [other["id"] for other in characters if other["id"] != item["id"]],
                }
                for item in characters
            ],
        },
    )
    (root / "20_outline" / "book_outline.md").write_text("# Book Outline\n\nTen escalating evidence arcs.\n", encoding="utf-8")
    write_json(root / "20_outline" / "story_arcs.json", story_arcs)
    write_json(root / "20_outline" / "volumes.json", volumes)
    write_json(root / "20_outline" / "chapter_plan.json", chapter_plan)
    write_json(root / "20_outline" / "planning_window.json", planning_window)
    write_json(root / "20_outline" / "foreshadowing_ledger.json", ledger)
    write_reader_promise_ledger(
        root,
        merge_planned_reader_promises(
            root,
            story_engine_contract=brief["story_engine_contract"],
            foreshadowing_ledger=ledger,
            estimated_chapters=forecast.estimated_chapters,
        ),
    )
    horizon_start = int(planning_window["start_chapter"])
    horizon_end = int(planning_window["end_chapter"])
    simulation_path = write_arc_simulation_fixture(
        root,
        from_chapter=horizon_start,
        to_chapter=horizon_end,
        characters=characters,
    )
    simulation_ref = {
        "path": simulation_path.relative_to(root).as_posix(),
        "sha256": sha256(simulation_path.read_bytes()).hexdigest(),
        "from_chapter": horizon_start,
        "to_chapter": horizon_end,
    }
    if direction_applied:
        for row in chapter_plan:
            chapter_number = int(row["chapter_number"])
            effective_quality_contract = compile_effective_quality_contract(
                config,
                chapter_number=chapter_number,
            )
            quality_body = effective_quality_contract.get("contract") or {}
            card = {
                **row,
                "status": "planned",
                "book_goal": "Resolve who controls collective memory.",
                "volume_goal": "Resolve the current evidence escalation layer.",
                "protagonist_goal": characters[0]["goal"],
                "pov_character_id": row["featured_character_ids"][0],
                "chapter_duty": row["chapter_duty"],
                "immediate_desire": "Reach the witness before the archive gate closes.",
                "opposition_force": row["conflict"],
                "dramatic_question": "Can Ari secure the witness without surrendering the physical clue?",
                "key_failure": "The direct pursuit is blocked when the damaged seal triggers the gate alarm.",
                "irreversible_choice": "Ari gives Mira the only copy of the route and follows her plan.",
                "chapter_turn": row["chapter_turn"],
                "reveal_boundary": "Reveal the internal route but not the editor's identity.",
                "reader_gain": row["reader_gain"],
                "cost": "The chosen gain narrows the protagonist's next safe option.",
                "must_dramatize": ["the failed pursuit", "Ari's trust choice", "the suspect gaining distance"],
                "may_summarize": ["routine movement between archive levels"],
                "primary_story_engine": row["primary_story_engine"],
                "scene_carriers": [row["primary_scene_carrier"]],
                "protected_story_outcomes": [row["chapter_turn"]],
                "prohibited_drift": ["Do not replace the pursuit with a document-verification discussion."],
                "state_change_kind": row["state_change_kind"],
                "dramatic_method": row["dramatic_method"],
                "exposition_carrier": "embedded_in_action",
                "platform_promise": str(quality_body.get("platform_promise") or ""),
                "effective_quality_contract": compact_effective_quality_contract(
                    effective_quality_contract
                ),
                "scene_chain": [
                    {
                        "scene_id": f"ch{chapter_number:03d}:fixture",
                        "location": "archive gate",
                        "participants": row["featured_character_ids"],
                        "carrier": row["primary_scene_carrier"],
                        "desire_collision": "Verification competes with immediate pursuit.",
                        "action": "Ari blocks the closing mechanism while Mira reaches for the witness route.",
                        "reaction": "The alarm seals the lower stair and forces them to split the clue.",
                        "choice": "Ari shares the clue and accepts the delay.",
                        "cost": "The suspect gains distance.",
                        "turn": "The evidence points to internal access.",
                        "exit_state": row["chapter_turn"],
                    }
                ],
                "canon_refs": [],
                "world_rule_refs": ["10_bible/world.md", "10_bible/power_system.md"],
                "foreshadow_refs": ["thread_false_treaty"],
                "forbidden_reveals": list(row.get("forbidden_reveals") or []),
                "reader_promise_actions": [
                    {
                        "promise_id": "story_engine:opening_three" if chapter_number <= 3 else "story_engine:early_serial",
                        "action": (
                            "setup" if chapter_number in {1, 4}
                            else "payoff" if chapter_number == 3
                            else "escalate"
                        ),
                        "intended_reader_gain": row["reader_gain"],
                        "evidence_requirement": "The final chapter must show a concrete changed condition.",
                        "defer_reason": "",
                    }
                ],
                "arc_simulation_ref": simulation_ref,
                "direction_selection": {
                    "status": "applied",
                    "direction_id": "fixture_human_choice",
                    "approved_by": "human",
                },
            }
            stamp_chapter_contract(card)
            write_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json", card)
            (root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.md").write_text(
                f"# Chapter Card ch{chapter_number:03d}\n\nHuman-approved fixture direction.\n",
                encoding="utf-8",
            )
    state_path = root / "30_state" / "novel_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "project_ready"
    state["project_intelligence"] = {
        "book_ideation": {"status": "applied", "candidate_hash": "test-ideation"},
        "book_design": {"status": "applied", "candidate_hash": "test-book"},
        "outline_design": {"status": "applied", "candidate_hash": "test-outline"},
        "character_expression_design": {"status": "applied", "candidate_hash": "test-expression"},
    }
    write_json(state_path, state)


def checked_review_coverage(
    root: Path,
    source: Path,
    dimensions,
    *,
    canonical_dimensions=(),
    canonical_ref: str = "20_outline/chapter_cards/ch001.json",
) -> dict:
    text = source.read_text(encoding="utf-8")
    end = min(max(len(text), 1), 48)
    evidence_id = f"{source.relative_to(root).as_posix()}@0:{end}"
    canonical = set(canonical_dimensions)
    return {
        str(dimension): {
            "status": "checked",
            "evidence_ids": [evidence_id],
            "canonical_refs": [canonical_ref] if dimension in canonical else [],
        }
        for dimension in dimensions
    }


def complete_editorial_reviews(root: Path, config, *, chapter_number: int = 1) -> None:
    """Submit passing results for every independently selected fixture editor."""

    from longform_engine.agent_pipeline import validate_production_agent_result
    from longform_engine.agent_protocols import EVIDENCE_REVIEW_SCHEMA
    from longform_engine.agent_tasks import load_manifest, manifest_output
    from longform_engine.editorial import (
        editorial_finalization_blockers,
        editorial_review,
        editorial_submit_review,
    )
    from longform_engine.roles import load_role_registry

    blockers = editorial_finalization_blockers(config, chapter_number=chapter_number)
    if blockers:
        if not set(blockers) <= {"editorial_review_missing", "stale_editorial_aggregate"}:
            raise AssertionError(f"candidate has unresolved editorial blockers: {blockers}")
        review = editorial_review(config, chapter_number=chapter_number)
        source = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
        for role_id in review.selected_roles:
            manifest = load_manifest(
                root,
                f"editorial_review:{role_id}:ch{chapter_number:03d}:v4",
            )
            result_path = root / str(manifest_output(manifest)["path"])
            contract = load_role_registry().resolve(
                "editorial_review",
                declared_role_id=role_id,
            )
            write_json(
                result_path,
                {
                    "schema": EVIDENCE_REVIEW_SCHEMA,
                    "verdict": "pass",
                    "coverage": checked_review_coverage(
                        root,
                        source,
                        contract.review_dimensions,
                        canonical_dimensions=contract.canonical_ref_dimensions,
                        canonical_ref=f"20_outline/chapter_cards/ch{chapter_number:03d}.json",
                    ),
                    "findings": [],
                },
            )
            control = validate_production_agent_result(root, manifest, result_file=result_path)
            if not control.ok:
                raise AssertionError(control.normalization.errors)
            editorial_submit_review(
                config,
                chapter_number=chapter_number,
                role=role_id,
                file_path=result_path,
            )


def approve_story_candidate(root: Path, config, *, chapter_number: int = 1) -> None:
    """Complete mandatory independent editorial review and hash-bound human acceptance."""

    from longform_engine.human_story_review import (
        apply_human_story_review,
        create_human_story_review_task,
    )

    complete_editorial_reviews(root, config, chapter_number=chapter_number)
    task = create_human_story_review_task(config, chapter_number=chapter_number)
    decision_path = root / task.template_file
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    decision["checks"] = {
        key: {"passed": True, "reason": "Verified against the current candidate."}
        for key in decision["checks"]
    }
    decision["decision"] = "accept"
    draft_text = (root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md").read_text(encoding="utf-8")
    evidence_end = min(40, len(draft_text))
    decision["evidence_spans"] = [
        {
            "start": 0,
            "end": evidence_end,
            "text": draft_text[:evidence_end],
            "kind": kind,
            "note": "The final candidate makes the turn and character ownership visible.",
        }
        for kind in ("key_turn", "character_choice_or_emotion", "reader_gain")
    ]
    decision["reader_gain_note"] = "The chapter delivers a concrete changed condition and emotional ownership."
    decision["annotations"] = []
    decision["reason"] = ""
    write_json(decision_path, decision)
    apply_human_story_review(
        config,
        chapter_number=chapter_number,
        file_path=decision_path,
        approved_by="human",
    )


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_outline_candidate(config, *, characters: list[dict] | None = None) -> dict:
    """Return a valid v0.5 rolling-outline candidate for integration tests."""

    characters = characters or [{"id": "lead_ari"}, {"id": "ally_mira"}]
    forecast = compile_length_forecast(config.data["length"])
    horizon = int(config.data["length"]["planning"]["detailed_horizon"])
    arc_phases = ("opening", "early_serial", "stable_serial", "volume_climax")
    arc_count = len(arc_phases)
    story_arcs = []
    remaining = forecast.target_total_characters
    for number, phase in enumerate(arc_phases, start=1):
        target = remaining if number == arc_count else forecast.target_total_characters // arc_count
        remaining -= target
        story_arcs.append(
            {
                "id": f"arc_{number:02d}", "number": number, "title": f"Story Arc {number}",
                "phase": phase, "progress_window": [(number - 1) / arc_count, number / arc_count],
                "target_characters": target, "goal": f"Resolve causal layer {number}.",
                "conflict_escalation": f"Increase the evidence cost at layer {number}.",
                "character_arc_moves": ["Ari changes one trust decision."],
                "promise_ids": ["thread_false_treaty"],
                "active_facets": ["setting:xuanhuan", "plot_engines:progression"],
                "quality_focus": {
                    "requirements": [f"Arc {number} must change a decision, relationship, or known fact."],
                    "preferences": [f"Let escalation layer {number} shape scene selection."],
                    "risks": [f"Arc {number} may repeat investigation beats without a new cost."],
                    "review_questions": [f"Did arc {number} produce its declared causal change?"],
                },
            }
        )
    volumes = []
    remaining = forecast.target_total_characters
    volume_target = int(config.data["length"]["volume"]["target_characters"])
    for number in range(1, forecast.estimated_volumes + 1):
        target = remaining if number == forecast.estimated_volumes else volume_target
        remaining -= target
        arc_number = min(arc_count, max(1, (number - 1) * arc_count // forecast.estimated_volumes + 1))
        volumes.append(
            {
                "id": f"vol_{number:02d}", "number": number, "title": f"Volume {number}",
                "target_characters": target, "arc_ids": [f"arc_{arc_number:02d}"],
                "goal": f"Resolve escalation layer {number}.",
                "escalation": f"Raise the institutional cost at layer {number}.",
                "ending_turn": f"Change the evidence model at turn {number}.",
            }
        )
    chapter_plan = []
    chapter_target = int(config.data["length"]["chapter"]["target_characters"])
    for chapter_number in range(1, horizon + 1):
        arc_number = min(arc_count, max(1, (chapter_number - 1) * arc_count // forecast.estimated_chapters + 1))
        volume_number = min(
            forecast.estimated_volumes,
            max(1, (chapter_number - 1) * chapter_target // volume_target + 1),
        )
        chapter_plan.append(
            {
                "chapter_number": chapter_number, "title": f"Evidence {chapter_number}",
                "chapter_duty": "Advance the active investigation.",
                "conflict": "Ari must choose between speed and verified evidence.",
                "chapter_turn": "Ari's pursuit proves the saboteur has internal access and binds Mira to the next move.",
                "hook": "The clue points to a larger contradiction.",
                "reader_gain": "A prior detail gains a concrete new meaning.",
                "volume_id": f"vol_{volume_number:02d}", "arc_id": f"arc_{arc_number:02d}",
                "featured_character_ids": [characters[0]["id"], characters[1]["id"]],
                "characterization_focus": [characters[0]["id"]],
                "scene_wants": {
                    characters[0]["id"]: "Verify the clue before acting.",
                    characters[1]["id"]: "Force a decision before the witness leaves.",
                },
                "relationship_move": "Pressure the uneasy alliance through a costly choice.",
                "primary_story_engine": "pursuit_and_leverage",
                "primary_scene_carrier": "pursuit",
                "state_change_kind": "situation_and_relationship",
                "dramatic_method": "failed_pursuit_then_shared_choice",
                "active_facets": ["setting:xuanhuan", "plot_engines:progression"],
                "forbidden_reveals": ["final editor identity"],
            }
        )
    return {
        "schema": "outline_design_candidate_v2",
        "book_outline_markdown": "# Book Outline\n\nEscalating evidence arcs.",
        "story_arcs": story_arcs,
        "volumes": volumes,
        "planning_window": {
            "schema": "rolling_outline_window_v1", "start_chapter": 1, "end_chapter": horizon,
            "detailed_horizon": horizon,
            "refill_threshold": int(config.data["length"]["planning"]["refill_threshold"]),
        },
        "chapter_plan": chapter_plan,
        "foreshadowing_ledger": [
            {
                "id": "thread_false_treaty",
                "description": "The treaty contains a deliberately altered witness line.",
                "plant": {"arc_id": "arc_01", "progress_window": [0.0, 0.2]},
                "payoff": {"arc_id": "arc_04", "progress_window": [0.8, 1.0]},
                "completion_required": True,
                "status": "planned",
            }
        ],
    }


def build_outline_extension_candidate(config, start: int, end: int) -> dict:
    """Return one bounded rolling-outline window for integration tests."""

    base = build_outline_candidate(config)["chapter_plan"][0]
    return {
        "schema": "outline_extension_candidate_v1",
        "planning_window": {
            "schema": "rolling_outline_window_v1",
            "start_chapter": start,
            "end_chapter": end,
            "detailed_horizon": int(config.data["length"]["planning"]["detailed_horizon"]),
            "refill_threshold": int(config.data["length"]["planning"]["refill_threshold"]),
        },
        "chapter_plan": [
            {
                **base,
                "chapter_number": number,
                "title": f"Rolling Evidence {number}",
                "chapter_duty": f"Advance causal step {number} without rewriting prior plans.",
            }
            for number in range(start, end + 1)
        ],
        "foreshadowing_updates": [],
    }


def prepare_unified_semantic_bundle(root: Path, config, chapter_number: int) -> Path:
    """Write a minimal valid Agent result for tests that exercise post-finalize plumbing."""

    task = semantic_task(config, chapter_number=chapter_number)
    final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    text = final.read_text(encoding="utf-8")
    start = next((index for index, character in enumerate(text) if not character.isspace()), 0)
    end = min(len(text), max(start + 1, start + 24))
    evidence_id = f"40_manuscript/final/ch{chapter_number:03d}.md@{start}:{end}"
    active_threads = sorted(
        active_planned_thread_ids(
            planned_threads(root),
            foreshadow_state_threads(root),
            chapter_number,
        )
    )
    output = Path(task.output_file)
    write_json(
        output,
        {
            "schema": CANONICAL_DELTA_SCHEMA,
            "delta_type": "chapter_semantic",
            "coverage": {
                "chapter_digest": "changed",
                "scenes": "changed",
                "events": "unchanged",
                "relationships": "unchanged",
                "characters": "unchanged",
                "foreshadowing": "unchanged",
                "world": "unchanged",
                "timeline": "unchanged",
            },
            "evidence": {
                "/changes/chapter_digest": [evidence_id],
                "/changes/scenes/0": [evidence_id],
            },
            "changes": {
                "chapter_digest": {
                    "summary": "The chapter advances the immediate conflict through a concrete choice.",
                    "causal_change": "The protagonist's action changes the next available decision.",
                    "reader_payoff": "The immediate chapter question receives a concrete answer.",
                    "cost": "The action narrows the protagonist's safe options.",
                },
                "scenes": [
                    {
                        "scene_id": f"ch{chapter_number:03d}:scene:1",
                        "participants": [],
                        "location_id": "",
                        "goal": "Advance the immediate chapter conflict.",
                        "outcome": "The next decision becomes unavoidable.",
                    }
                ],
                "events": [],
                "relationship_deltas": [],
                "character_deltas": [],
                "foreshadow_deltas": [],
                "world_deltas": [],
                "timeline_deltas": [],
                "retrieval": {
                    "tags": ["chapter progression"],
                    "entity_ids": [],
                    "focus": ["causal change"],
                },
                "entity_coverage": {
                    "featured_character_ids": [],
                    "unchanged_character_ids": [],
                    "active_thread_ids": active_threads,
                    "unchanged_thread_ids": active_threads,
                },
            },
            "uncertainties": [],
        },
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, task.manifest_file),
        result_file=output,
    )
    assert control.ok, control.normalization.errors
    return output


def complete_unified_semantic_lifecycle(root: Path, config, chapter_number: int, *, approved_by: str = "human") -> None:
    ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    if not ledger.exists():
        output = prepare_unified_semantic_bundle(root, config, chapter_number)
        semantic_apply(config, chapter_number=chapter_number, file_path=output)
    gate = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    if not gate.exists():
        write_json(gate, {"chapter_number": chapter_number, "passed": True, "severity_counts": {"P0": 0, "P1": 0}})
    chapter_close(config, chapter_number=chapter_number, approved_by=approved_by)
