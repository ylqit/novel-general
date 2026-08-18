import json
from hashlib import sha256
from pathlib import Path

import yaml

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import CANONICAL_DELTA_SCHEMA
from longform_engine.agent_tasks import load_manifest
from longform_engine.chapter_contract import stamp_chapter_contract
from longform_engine.semantic import chapter_close, semantic_apply, semantic_task
from longform_engine.semantic.pipeline import active_planned_thread_ids, foreshadow_state_threads, planned_threads
from longform_engine.lengths import compile_length_forecast
from longform_engine.quality import compact_effective_quality_contract, compile_effective_quality_contract


def mark_project_ready(
    root: Path,
    config,
    *,
    preserve_existing_characters: bool = False,
    direction_applied: bool = True,
) -> None:
    """Seed canonical book/outline state for tests that start after human apply."""

    # Legacy chapter-flow fixtures predate mandatory milestone Agent reviews.
    # Dedicated semantic/fanfiction tests opt into those reviews explicitly.
    config.data.setdefault("quality", {})["semantic_review_milestones"] = []
    config.data["quality"]["semantic_review_boundaries"] = False
    config.data["quality"]["assurance_mode"] = "light"
    config.data.setdefault("editorial", {})["review_mode"] = "off"
    config.data.setdefault("semantic", {})["allow_fallback"] = True
    config.data["semantic"].setdefault("vector_store", {})["backend"] = "local_sqlite"
    config.data.setdefault("rag", {}).setdefault("embedding", {})["profile"] = "local-hash"
    project_yaml = (root / "project.yaml").resolve()
    if config.path is not None and config.path.resolve() == project_yaml and project_yaml.is_file():
        payload = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
        payload.setdefault("quality", {})["semantic_review_milestones"] = []
        payload["quality"]["semantic_review_boundaries"] = False
        payload["quality"]["assurance_mode"] = "light"
        payload.setdefault("editorial", {})["review_mode"] = "off"
        payload.setdefault("semantic", {})["allow_fallback"] = True
        payload["semantic"].setdefault("vector_store", {})["backend"] = "local_sqlite"
        payload.setdefault("rag", {}).setdefault("embedding", {})["profile"] = "local-hash"
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
                "chapter_duty": row["duty"],
                "information": row["information_release"],
                "information_release": row["information_release"],
                "reader_gain": row["reader_payoff"],
                "cost": "The chosen gain narrows the protagonist's next safe option.",
                "platform_promise": str(quality_body.get("platform_promise") or ""),
                "effective_quality_contract": compact_effective_quality_contract(
                    effective_quality_contract
                ),
                "scene_chain": [
                    {
                        "scene_id": f"ch{chapter_number:03d}:fixture",
                        "location": "archive gate",
                        "participants": row["featured_character_ids"],
                        "desire_collision": "Verification competes with immediate pursuit.",
                        "choice": "Ari shares the clue and accepts the delay.",
                        "cost": "The suspect gains distance.",
                        "turn": "The evidence points to internal access.",
                    }
                ],
                "canon_refs": [],
                "world_rule_refs": ["10_bible/world.md", "10_bible/power_system.md"],
                "foreshadow_refs": ["thread_false_treaty"],
                "forbidden_reveals": list(row.get("forbidden_reveals") or []),
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


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_outline_candidate(config, *, characters: list[dict] | None = None) -> dict:
    """Return a valid v0.4 rolling-outline candidate for integration tests."""

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
                "duty": "Advance the active investigation.",
                "conflict": "Ari must choose between speed and verified evidence.",
                "information_release": "Release one bounded clue.",
                "hook": "The clue points to a larger contradiction.",
                "reader_payoff": "A prior detail gains a concrete new meaning.",
                "volume_id": f"vol_{volume_number:02d}", "arc_id": f"arc_{arc_number:02d}",
                "featured_character_ids": [characters[0]["id"], characters[1]["id"]],
                "characterization_focus": [characters[0]["id"]],
                "scene_wants": {
                    characters[0]["id"]: "Verify the clue before acting.",
                    characters[1]["id"]: "Force a decision before the witness leaves.",
                },
                "relationship_move": "Pressure the uneasy alliance through a costly choice.",
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
                "duty": f"Advance causal step {number} without rewriting prior plans.",
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
