import json
from hashlib import sha256
from pathlib import Path

import pytest
from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_REQUIRED_HEADINGS,
)
from longform_engine.agent_tasks import list_manifests, load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.creative import humanize_check, humanize_task
from longform_engine.editorial import editorial_review
from longform_engine.gates.pipeline import check_fanfiction_source_reproduction
from longform_engine.intelligence import (
    apply_compiled_design,
    apply_intelligence_candidate,
    approve_design_document,
    assess_chapter_direction,
    assess_project_readiness,
    create_design_compile_task,
    create_intelligence_task,
    fanfiction_status,
    validate_design_compile_delta,
    validate_intelligence_candidate,
)
from longform_engine.intelligence.pipeline import validate_crossover_rules
from longform_engine.orchestration import continue_write, open_book
from longform_engine.orchestration.pipeline import load_fanfiction_writing_contract
from longform_engine.publication import export_publication_bundle, publication_risk_report
from longform_engine.storage import init_project
from tests.project_fixtures import build_outline_candidate


def seed_fanfiction_project(tmp_path: Path):
    source = {
        "source_id": "classic",
        "title": "Public Domain Adventure",
        "creator": "Example Author",
        "canon_cutoff": "volume-1-end",
        "allowed_elements": ["characters", "relationships", "world", "abilities", "timeline"],
        "rights_status": "unverified",
        "commercial_intent": True,
        "platform_policy_url": "",
    }
    template = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "creation": {"mode": "fanfiction"},
            "fanfiction": {"continuity_mode": "canon_divergent", "sources": [source]},
            "semantic": {"profile": "local-hash", "allow_fallback": True},
            "length": {
                "target_total_characters": 100_000,
                "volume": {"target_characters": 50_000},
                "planning": {"detailed_horizon": 4, "refill_threshold": 2},
            },
        },
    )
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    source_path = project.root / "50_workbench" / "fanfiction_sources" / "classic.txt"
    source_path.write_text(
        "林舟站在青铜门前，听见旧钟连续响了三次。守门人告诉他，门后的火不能被水熄灭。"
        "林舟没有回答，只把刻着星纹的钥匙收回袖中。",
        encoding="utf-8",
    )
    open_book(config)
    return config, project.root, source_path


def valid_canon(source_path: Path) -> dict:
    source_rel = "50_workbench/fanfiction_sources/classic.txt"
    digest = sha256(source_path.read_bytes()).hexdigest()
    return {
        "schema": "fanfiction_source_canon_v1",
        "continuity_mode": "canon_divergent",
        "sources": [
            {
                "source_id": "classic",
                "title": "Public Domain Adventure",
                "creator": "Example Author",
                "canon_cutoff": "volume-1-end",
                "source_files": [source_rel],
                "source_hashes": {source_rel: digest},
                "characters": [
                    {
                        "id": "classic:lin_zhou",
                        "name": "林舟",
                        "summary": "A guarded key bearer who tests claims before committing.",
                        "motivation": "Learn who controls the bronze gate.",
                        "voice_traits": ["brief", "evidence-led"],
                        "evidence_refs": ["classic:e1"],
                    },
                    {
                        "id": "classic:gatekeeper",
                        "name": "守门人",
                        "summary": "A keeper who communicates rules through warnings.",
                        "motivation": "Prevent an unprepared crossing.",
                        "voice_traits": ["indirect", "ritualized"],
                        "evidence_refs": ["classic:e1"],
                    },
                ],
                "relationships": [
                    {
                        "id": "classic:rel_gate",
                        "source_character_id": "classic:lin_zhou",
                        "target_character_id": "classic:gatekeeper",
                        "stage": "mutual testing",
                        "summary": "The keeper controls access while the bearer withholds trust.",
                        "evidence_refs": ["classic:e1"],
                    }
                ],
                "world_rules": [
                    {
                        "id": "classic:rule_fire",
                        "summary": "The gate fire does not obey ordinary water.",
                        "evidence_refs": ["classic:e1"],
                    }
                ],
                "abilities": [
                    {
                        "id": "classic:star_key",
                        "name": "星纹钥匙",
                        "summary": "A key associated with the bronze gate.",
                        "limits": ["Its opening conditions remain unresolved."],
                        "evidence_refs": ["classic:e1"],
                    }
                ],
                "timeline": [
                    {
                        "id": "classic:time_bell",
                        "order": 1,
                        "summary": "The old bell sounds three times before the warning.",
                        "evidence_refs": ["classic:e1"],
                    }
                ],
                "terminology": [
                    {
                        "id": "classic:term_bronze_gate",
                        "name": "青铜门",
                        "summary": "A guarded threshold tied to unusual fire.",
                        "evidence_refs": ["classic:e1"],
                    }
                ],
                "canon_events": [
                    {
                        "id": "classic:event_warning",
                        "order": 1,
                        "summary": "The keeper warns the key bearer about the gate fire.",
                        "evidence_refs": ["classic:e1"],
                    }
                ],
                "unresolved_questions": [
                    {
                        "id": "classic:q_controller",
                        "summary": "Who determines when the gate may open remains unresolved.",
                        "evidence_refs": ["classic:e1"],
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "classic:e1",
                        "source_path": source_rel,
                        "source_hash": digest,
                        "evidence_span": {"start": 0, "end": 48},
                    }
                ],
            }
        ],
    }


def book_design() -> dict:
    expression = valid_character_expression()
    return {
        "schema": "book_design_candidate_v2",
        "creative_brief": {
            "target_audience": "Chinese fanfiction serial readers.",
            "writing_style": "Concrete scene-led prose with distinct voices.",
            "automation_level": "agent_skill with explicit human apply.",
            "target_scale": "4 chapters.",
            "story_profile": load_project_config(template="qidian-longform").data["story_profile"],
            "design_decisions": {
                "core_hook": "The key opens a different gate after one changed choice.",
                "world_rule": "Every divergence must create a visible consequence.",
                "protagonist_desire": "Learn who controls the gate without losing agency.",
                "long_conflict": "Canon duty conflicts with the new mainline.",
                "volume_escalation": "The changed gate alters relationships before power.",
                "ending_boundary": "Resolve the new gate while preserving the canon question.",
            },
            "reader_contract": {"core_promise": "Canon voice plus a causally earned new plot."},
            "core_taboo": ["Do not turn canon characters into props."],
            "status": "candidate",
        },
        "world_markdown": "# World\n\nCanon rules remain active unless a declared divergence changes them.",
        "power_system_markdown": "# Power\n\nThe star key always has an opening cost.",
        "characters": [
            {
                "id": "classic:lin_zhou",
                "name": "林舟",
                "goal": "Learn who controls the gate.",
                "flaw": "Withholds trust after evidence is sufficient.",
                "arc_stages": ["guarded", "tested", "chooses"],
            },
            {
                "id": "classic:gatekeeper",
                "name": "守门人",
                "goal": "Preserve the threshold rule.",
                "flaw": "Explains danger only through ritual.",
                "arc_stages": ["keeper", "challenged", "revealed"],
            },
        ],
        "relationships": [
            {
                "id": "classic:rel_gate",
                "source_id": "classic:lin_zhou",
                "target_id": "classic:gatekeeper",
                "type": "mutual testing",
                "stage": "guarded",
            }
        ],
        "narrative_expression_profile": expression["narrative_expression_profile"],
        "character_expression_contracts": expression["character_expression_contracts"],
    }


def valid_design() -> dict:
    return {
        "schema": "fanfiction_design_candidate_v1",
        "continuity_mode": "canon_divergent",
        "canon_cutoff": "volume-1-end",
        "divergence_point": "林舟 answers the gatekeeper instead of hiding the key.",
        "ooc_tolerance": "bounded",
        "character_voice_contracts": [
            {
                "character_id": "classic:lin_zhou",
                "baseline_voice": "Brief, skeptical, and evidence-led.",
                "invariants": ["tests claims", "protects agency"],
                "allowed_changes": ["speaks more directly after earned trust"],
                "forbidden_shortcuts": ["instant trust", "serves only a new protagonist"],
            },
            {
                "character_id": "classic:gatekeeper",
                "baseline_voice": "Indirect and ritualized.",
                "invariants": ["protects threshold rules"],
                "allowed_changes": ["reveals one motive under cost"],
                "forbidden_shortcuts": ["forgets the gate rules"],
            },
        ],
        "original_mainline": {
            "premise": "One answer redirects the key to an undocumented gate.",
            "central_conflict": "The changed path threatens both characters' existing duties.",
            "reader_promise": "A new causal plot that keeps canon voices and rules active.",
        },
        "original_characters": [],
        "world_rule_changes": ["Only the declared alternate gate changes destination logic."],
        "butterfly_effects": [
            {"cause": "林舟 answers", "effect": "the key records his voice", "chapter_window": [1, 2]}
        ],
        "ending_boundary": "Close the alternate gate conflict without claiming an official continuation.",
        "original_contribution": ["alternate gate mechanism", "new duty conflict"],
        "protected_reveals": ["identity of the original gate controller"],
        "cross_source_rules": [],
        "book_design": book_design(),
    }


def valid_character_expression() -> dict:
    return {
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
                "character_id": "classic:lin_zhou",
                "perception_bias": "Notices changed rules before accepting stated motives.",
                "decision_bias": "Tests a boundary while preserving one route of retreat.",
                "speech_register": "Brief skeptical questions grounded in visible evidence.",
                "conversation_tactics": ["narrows the claim", "withholds one inference"],
                "emotional_leaks": ["turns the star key inside his sleeve when cornered"],
                "physical_presence": "Economical movement with attention fixed on thresholds.",
                "social_masks": ["unimpressed traveler"],
                "private_wants": "Wants agency without abandoning the people behind the gate.",
                "contradictions": "Distrusts ritual authority but protects its vulnerable keepers.",
                "voice_examples": [],
                "contrast_with": ["classic:gatekeeper"],
            },
            {
                "character_id": "classic:gatekeeper",
                "perception_bias": "Reads every choice as a change to threshold risk.",
                "decision_bias": "Reveals only the rule needed to prevent the next breach.",
                "speech_register": "Indirect ritual clauses that conceal personal stakes.",
                "conversation_tactics": ["answers with a condition", "tests declared intent"],
                "emotional_leaks": ["touches the door seam before admitting uncertainty"],
                "physical_presence": "Measured posture that keeps one hand near the gate.",
                "social_masks": ["impersonal keeper of rules"],
                "private_wants": "Wants the gate protected without becoming its last sacrifice.",
                "contradictions": "Claims rules are impersonal while bending them to protect Lin Zhou.",
                "voice_examples": [],
                "contrast_with": ["classic:lin_zhou"],
            },
        ],
    }


def test_fanfiction_design_compiles_realistic_canon_into_bounded_context(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    canon = valid_canon(source_path)
    template = canon["sources"][0]["characters"][0]
    canon["sources"][0]["characters"] = [
        {
            **template,
            "id": f"classic:character_{index:03d}",
            "name": f"Character {index}",
            "summary": "A source-backed character description with distinct motive and pressure. " * 8,
            "motivation": "Protect a bounded choice while preserving canon causality. " * 5,
        }
        for index in range(40)
    ]
    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon_path.parent.mkdir(parents=True, exist_ok=True)
    canon_path.write_text(json.dumps(canon, ensure_ascii=False, indent=2), encoding="utf-8")
    decisions_path = root / "10_bible" / "creative_decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "schema": "book_ideation_decisions_v1",
                "decisions": {
                    "target_reader_and_reading_context": "Serial readers who expect causally earned divergence.",
                    "core_hook": "A changed choice creates a new duty rather than free power.",
                    "world_core_rule": "Canon constraints remain active.",
                    "protagonist_desire_and_flaw": "Protect agency while learning not to control allies.",
                    "long_conflict": "Competing groups disagree over who may choose the route home.",
                    "volume_escalation": "Escalate relationships and institutions before raw power.",
                    "ending_boundary": "Preserve the canon protagonist's protected final responsibility.",
                    "taboos_and_unwanted_tropes": "No system shortcut, canon demotion, or prose copying.",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert len(canon_path.read_text(encoding="utf-8")) > 20_000

    task = create_intelligence_task(config, task_type="fanfiction_design")
    manifest = load_manifest(root, task.manifest_file)
    validation = validate_manifest_strict(root, manifest)
    context_path = root / "50_workbench" / "intelligence_context" / "fanfiction_design.project.context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))

    assert validation.ok
    assert [item["path"] for item in manifest["io"]["inputs"]] == [
        "50_workbench/intelligence_context/fanfiction_design.project.context.json",
        "50_workbench/intelligence_tasks/fanfiction_design.project.md",
    ]
    assert context["selection_report"]["estimated_units"] > 0
    assert context["selection_report"]["estimated_units"] < context["selection_report"]["capacity_units"]
    assert context["schema"] == "fanfiction_design_context_v1"
    assert context["selection_report"]["omitted_counts"]["classic:characters"] == 28
    assert {item["path"] for item in context["canonical_provenance"]} == {
        "10_bible/fanfiction/source_canon.json",
        "10_bible/creative_decisions.json",
        "project.yaml",
    }


def valid_outline(config) -> dict:
    outline = build_outline_candidate(config)
    for chapter in outline["chapter_plan"]:
        chapter.update(
            {
                "canon_refs": ["classic:event_warning"],
                "divergence_effects": ["The changed answer redirects the star key."],
                "voice_refs": ["classic:lin_zhou", "classic:gatekeeper"],
                "original_contribution": "Advance the alternate-gate duty conflict.",
                "protected_reveals": ["identity of the original gate controller"],
            }
        )
    return outline


def canonical_snapshot(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for relative_dir in (
        "10_bible",
        "20_outline",
        "30_state",
        "40_manuscript/final",
        "60_rag",
        "70_runtime/db",
    ):
        directory = root / relative_dir
        for path in directory.rglob("*"):
            if path.is_file():
                snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot


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
        body = ["本节内容已经由用户审阅。"]
        if index == 0:
            body.extend(f"- {fact}" for fact in facts)
        sections.extend((f"## {heading}", "", *body, ""))
    path.write_text(f"# {task_type} 设计文档\n\n" + "\n".join(sections), encoding="utf-8")


def compile_design_output(config, root: Path, task_type: str, candidate: Path, payload: dict):
    assert validate_intelligence_output(config, root, task_type, candidate).ok
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
    source = candidate.relative_to(root).as_posix()
    text = candidate.read_text(encoding="utf-8")
    changes = {key: value for key, value in payload.items() if key != "schema"}
    for cli_field in {
        "book_ideation": ("round", "dimension"),
        "chapter_direction": ("chapter_number", "chapter_card_sha256", "trigger_reasons"),
        "outline_revision": ("from_chapter", "to_chapter"),
    }.get(task_type, ()):
        changes.pop(cli_field, None)
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
    return delta, validation


def apply_design_output(config, root: Path, task_type: str, candidate: Path, payload: dict) -> None:
    delta, validation = compile_design_output(config, root, task_type, candidate, payload)
    assert validation.ok, validation.errors
    applied = apply_compiled_design(
        config,
        task_type=task_type,
        document_path=candidate,
        delta_path=delta,
        approved_by="human",
    )
    assert applied.status == "applied"


def write_canon_delta(path: Path, payload: dict, source_path: Path) -> None:
    source_text = source_path.read_text(encoding="utf-8")
    source_rel = "50_workbench/fanfiction_sources/classic.txt"
    evidence_ref = f"{source_rel}@0:{min(48, len(source_text))}"
    collections = (
        "characters",
        "relationships",
        "world_rules",
        "abilities",
        "timeline",
        "terminology",
        "canon_events",
        "unresolved_questions",
    )
    source = payload["sources"][0]
    compact_source = {"source_id": source["source_id"]}
    evidence: dict[str, list[str]] = {}
    for collection in collections:
        compact_source[collection] = []
        for index, record in enumerate(source[collection]):
            compact_source[collection].append(
                {key: value for key, value in record.items() if key != "evidence_refs"}
            )
            evidence[f"/changes/sources/0/{collection}/{index}"] = [evidence_ref]
    path.write_text(
        json.dumps(
            {
                "schema": CANONICAL_DELTA_SCHEMA,
                "delta_type": "fanfiction_canon",
                "coverage": {"source_canon": "changed"},
                "changes": {"sources": [compact_source]},
                "evidence": evidence,
                "uncertainties": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def validate_intelligence_output(config, root: Path, task_type: str, candidate: Path):
    task = next(
        item
        for item in reversed(list_manifests(root))
        if item.get("task_type") == task_type
        and candidate.relative_to(root).as_posix() == (item.get("io") or {}).get("output", {}).get("path")
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, task["task_id"]),
        result_file=candidate,
    )
    assert control.ok, control.normalization.errors
    return validate_intelligence_candidate(config, task_type=task_type, file_path=candidate)


def apply_fanfiction_foundation(config, root: Path, source_path: Path) -> None:
    canon_task = create_intelligence_task(
        config,
        task_type="fanfiction_canon",
        input_files=[source_path],
    )
    canon_candidate = root / canon_task.candidate_file
    write_canon_delta(canon_candidate, valid_canon(source_path), source_path)
    assert validate_intelligence_output(config, root, "fanfiction_canon", canon_candidate).ok
    apply_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=canon_candidate,
        approved_by="human",
    )

    dimensions = (
        "target_reader_and_reading_context",
        "core_hook",
        "world_core_rule",
        "protagonist_desire_and_flaw",
        "long_conflict",
        "volume_escalation",
        "ending_boundary",
        "taboos_and_unwanted_tropes",
    )
    for round_number, dimension in enumerate(dimensions, start=1):
        ideation_task = create_intelligence_task(config, task_type="book_ideation")
        ideation_candidate = root / ideation_task.candidate_file
        ideation_payload = {
            "schema": "book_ideation_candidate_v1",
            "round": round_number,
            "question": f"Choose {dimension}.",
            "options": [
                {
                    "id": "canon_focused",
                    "proposal": f"Canon-aware decision for {dimension}.",
                    "tradeoffs": ["Higher fidelity.", "Narrower divergence."],
                },
                {
                    "id": "original_focused",
                    "proposal": f"Original-mainline decision for {dimension}.",
                    "tradeoffs": ["More novelty.", "Higher continuity burden."],
                },
            ],
            "selection": {
                "mode": "selected_option",
                "option_id": "canon_focused",
                "answer": "",
            },
        }
        write_design_candidate(
            ideation_candidate,
            "book_ideation",
            ideation_payload,
        )
        apply_design_output(
            config, root, "book_ideation", ideation_candidate, ideation_payload
        )

    design_task = create_intelligence_task(config, task_type="fanfiction_design")
    design_candidate = root / design_task.candidate_file
    design_payload = valid_design()
    write_design_candidate(design_candidate, "fanfiction_design", design_payload)
    apply_design_output(config, root, "fanfiction_design", design_candidate, design_payload)

    outline_task = create_intelligence_task(config, task_type="outline_design")
    outline_candidate = root / outline_task.candidate_file
    outline_payload = valid_outline(config)
    write_design_candidate(outline_candidate, "outline_design", outline_payload)
    apply_design_output(config, root, "outline_design", outline_candidate, outline_payload)

    direction_task = create_intelligence_task(
        config,
        task_type="chapter_direction",
        chapter_number=1,
    )
    direction_candidate = root / direction_task.candidate_file
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    reasons = assess_chapter_direction(config, 1)["reasons"]
    direction = {
        "book_goal": "Resolve who controls the alternate gate.",
        "volume_goal": "Make the first divergence create a visible obligation.",
        "protagonist_goal": "Test the gate without surrendering agency.",
        "featured_character_ids": ["classic:lin_zhou", "classic:gatekeeper"],
        "scene_chain": [
            {
                "scene_id": "test_threshold",
                "location": "alternate gate",
                "participants": ["classic:lin_zhou", "classic:gatekeeper"],
                "desire_collision": "Lin Zhou wants proof while the keeper wants compliance.",
                "choice": "Lin Zhou tests one boundary before answering.",
                "cost": "The key records his voice and closes the safe route.",
                "turn": "The keeper must reveal one rule to prevent a breach.",
            },
            {
                "scene_id": "accept_condition",
                "location": "inside the threshold",
                "participants": ["classic:lin_zhou", "classic:gatekeeper"],
                "desire_collision": "Lin Zhou wants an exit while the keeper needs a binding witness.",
                "choice": "Lin Zhou accepts one named duty but refuses an open-ended oath.",
                "cost": "The safe route stays closed until the duty is discharged.",
                "turn": "Their guarded relationship becomes a temporary operational bargain.",
            },
        ],
        "cast_desires": {
            "classic:lin_zhou": "Preserve a route of retreat while testing the claim.",
            "classic:gatekeeper": "Protect the threshold without revealing its controller.",
        },
        "dialogue_ownership": "Lin Zhou narrows claims; the keeper answers with conditions.",
        "embodiment_plan": "Use the key turning in a sleeve and a hand resting on the gate seam.",
        "interiority_function": "Expose the urge to withhold trust before the costly answer.",
        "conflict": "Testing the gate consumes the only safe retreat window.",
        "information_release": "The alternate gate records voice as part of its access rule.",
        "local_payoff": "The changed answer produces an immediate mechanical consequence.",
        "character_cost": "Lin Zhou loses the unrecorded route back.",
        "mainline_move": "The divergence becomes an active duty conflict.",
        "character_arc_move": "Lin Zhou chooses a bounded test instead of passive distrust.",
        "foreshadow_move": "The hidden controller remains protected while its method appears.",
        "relationship_move": "Mutual testing becomes a temporary operational bargain.",
        "ending_mode": "changed_problem",
        "main_risks": ["Canon terminology could replace visible consequence."],
        "canon_refs": ["classic:event_warning"],
        "world_rule_refs": ["classic:rule_fire"],
        "foreshadow_refs": [],
        "forbidden_reveals": ["identity of the original gate controller"],
    }
    selected_direction = {
        "id": "test_gate",
        "title": "Test the gate",
        "chapter_duty": "Turn the first divergence into a costly choice.",
        **direction,
    }
    selected_direction["reader_gain"] = selected_direction.pop("local_payoff")
    selected_direction["cost"] = selected_direction.pop("character_cost")
    direction_payload = {
        "schema": "chapter_direction_candidate_v2",
        "chapter_number": 1,
        "chapter_card_sha256": sha256(card_path.read_bytes()).hexdigest(),
        "trigger_reasons": reasons,
        "selected_direction": selected_direction,
        "selection": {"direction_id": "test_gate", "user_adjustments": {}},
        "canonical_refs": selected_direction["canon_refs"],
        "introduced_elements": [],
    }
    write_design_candidate(
        direction_candidate,
        "chapter_direction",
        direction_payload,
    )
    apply_design_output(config, root, "chapter_direction", direction_candidate, direction_payload)

    readiness = assess_project_readiness(config)
    assert readiness.ready


def test_unverified_commercial_fanfiction_reaches_writing_and_export_without_rights_block(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    apply_fanfiction_foundation(config, root, source_path)

    assert assess_project_readiness(config).ready
    status = fanfiction_status(config)
    assert status["rights_advisory_only"] is True
    assert status["rights_warnings"][0]["blocking"] is False

    writing = continue_write(config, chapter_number=1)
    manifest = load_manifest(root, "chapter_write:ch001:v4")
    assert validate_manifest_strict(root, manifest).ok
    inputs = [item["path"] for item in manifest["io"]["inputs"]]
    assert len(inputs) <= 7
    assert inputs == ["50_workbench/writing_tasks/ch001.md"]
    writing_payload = json.loads(Path(writing.writing_task_json).read_text(encoding="utf-8"))
    assert "10_bible/fanfiction/source_canon.json" in writing_payload["context_plan"]["excluded_duplicates"]
    card = json.loads(Path(writing.chapter_card).read_text(encoding="utf-8"))
    assert card["requires_semantic_review"] is True
    assert card["canon_refs"] == ["classic:event_warning"]
    assert card["original_contribution"]

    final = root / "40_manuscript" / "final" / "ch001.md"
    final.write_text("# 第一章\n\n林舟握住星纹钥匙，决定回答守门人的问题。\n", encoding="utf-8")
    report = publication_risk_report(config)
    exported = export_publication_bundle(config)
    risk = json.loads((root / report.report_file).read_text(encoding="utf-8"))
    review = editorial_review(config, chapter_number=1)
    review_payload = json.loads(Path(review.review_file).read_text(encoding="utf-8"))
    editorial_roles = {item["id"] for item in review_payload["editorial_team"]}
    assert report.blocking is False
    assert exported.blocking is False
    assert risk["unverified_rights_blocks_export"] is False
    assert risk["commercial_intent_blocks_export"] is False
    assert "Rights" not in (root / exported.bundle_file).read_text(encoding="utf-8")
    assert {"reader_experience_editor", "canon_fidelity_reviewer"} <= editorial_roles
    assert any(
        "canon_fidelity_reviewer" in path
        for path in review_payload["agent_task_files"]
    )


def test_fanfiction_writing_contract_ignores_names_in_global_forbidden_rules(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    apply_fanfiction_foundation(config, root, source_path)

    contract = load_fanfiction_writing_contract(
        config,
        root,
        card={
            "title": "林舟的选择",
            "featured_character_ids": ["classic:lin_zhou"],
            "forbidden": ["不得削弱守门人的主体性"],
        },
        character_packet={"featured_character_ids": ["classic:lin_zhou"]},
    )

    assert [item["character_id"] for item in contract["voice_contracts"]] == [
        "classic:lin_zhou"
    ]


def test_fanfiction_manifest_and_invalid_evidence_do_not_pollute_bible(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    task = create_intelligence_task(config, task_type="fanfiction_canon", input_files=[source_path])
    manifest = load_manifest(root, task.task_id)
    assert validate_manifest_strict(root, manifest).ok
    before = (root / "10_bible" / "creative_brief.json").read_bytes()
    candidate = root / task.candidate_file
    write_canon_delta(candidate, valid_canon(source_path), source_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    first_pointer = next(iter(payload["evidence"]))
    payload["evidence"][first_pointer] = [
        "50_workbench/fanfiction_sources/classic.txt@0:9999"
    ]
    candidate.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    control = validate_production_agent_result(
        root,
        manifest,
        result_file=candidate,
    )
    assert not control.ok
    assert not (root / "10_bible" / "fanfiction" / "source_canon.json").exists()
    assert (root / "10_bible" / "creative_brief.json").read_bytes() == before


def test_fanfiction_canon_rejects_source_prose_reconstructed_across_fields(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    task = create_intelligence_task(config, task_type="fanfiction_canon", input_files=[source_path])
    candidate = root / task.candidate_file
    payload = valid_canon(source_path)
    source_text = source_path.read_text(encoding="utf-8")
    midpoint = len(source_text) // 2
    payload["sources"][0]["world_rules"][0]["summary"] = source_text[:midpoint]
    payload["sources"][0]["unresolved_questions"][0]["summary"] = source_text[midpoint:]
    write_canon_delta(candidate, payload, source_path)
    before = canonical_snapshot(root)

    validation = validate_intelligence_output(config, root, "fanfiction_canon", candidate)

    assert not validation.ok
    assert any("reconstructs source prose" in error for error in validation.errors)
    assert not (root / "10_bible" / "fanfiction" / "source_canon.json").exists()
    assert canonical_snapshot(root) == before


def test_invalid_fanfiction_design_does_not_pollute_canonical_state(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    canon_task = create_intelligence_task(
        config,
        task_type="fanfiction_canon",
        input_files=[source_path],
    )
    canon_candidate = root / canon_task.candidate_file
    write_canon_delta(canon_candidate, valid_canon(source_path), source_path)
    assert validate_intelligence_output(config, root, "fanfiction_canon", canon_candidate).ok
    apply_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=canon_candidate,
        approved_by="human",
    )
    design_task = create_intelligence_task(config, task_type="fanfiction_design")
    design_candidate = root / design_task.candidate_file
    payload = valid_design()
    payload["character_voice_contracts"] = []
    write_design_candidate(design_candidate, "fanfiction_design", payload)
    before = canonical_snapshot(root)

    _delta, validation = compile_design_output(
        config,
        root,
        "fanfiction_design",
        design_candidate,
        payload,
    )

    assert not validation.ok
    assert not (root / "10_bible" / "fanfiction" / "fanfiction_bible.json").exists()
    assert canonical_snapshot(root) == before


def test_fanfiction_similarity_excludes_names_but_detects_continuous_source_prose(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    apply_fanfiction_foundation(config, root, source_path)
    terms_only = "林舟握住星纹钥匙，绕过青铜门，守门人仍旧没有回答。"
    failures, _ = check_fanfiction_source_reproduction(config, root, terms_only)
    assert failures == []

    copied = source_path.read_text(encoding="utf-8")
    failures, _ = check_fanfiction_source_reproduction(config, root, copied)
    assert any(item["code"] == "fanfiction_source_prose_reproduction" for item in failures)


def test_humanizer_v3_escalates_fact_and_excessive_rewrite_drift(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# 第一章\n\n林舟在第12层青铜门前握住星纹钥匙。\n", encoding="utf-8")
    task = humanize_task(config, chapter_number=1)
    candidate = Path(task.candidate_file)
    candidate.write_text("# 第一章\n\n守门人在第13层转身离开，另一场战争已经开始。\n", encoding="utf-8")

    result = humanize_check(config, chapter_number=1, file_path=candidate)
    report = json.loads(Path(result.report_file).read_text(encoding="utf-8"))
    assert result.passed is False
    assert result.need_human is True
    assert report["schema"] == "humanizer_check_v3"
    assert {item["code"] for item in result.issues} >= {
        "humanizer_excessive_rewrite",
        "humanizer_number_drift",
    }


def test_publication_export_rejects_output_outside_exports(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    final = root / "40_manuscript" / "final" / "ch001.md"
    final.write_text("# 第一章\n\n林舟握住星纹钥匙。\n", encoding="utf-8")

    with pytest.raises(ValueError, match="80_exports"):
        export_publication_bundle(config, output="../outside.md")


def test_crossover_rules_require_conflict_power_and_terminology_policies():
    configured = {
        "sources": [
            {"source_id": "work_a"},
            {"source_id": "work_b"},
        ]
    }
    errors: list[str] = []
    validate_crossover_rules(
        configured,
        [
            {
                "source_ids": ["work_a", "work_b"],
                "conflict_rule": "When rules conflict, the host world's physical limit wins.",
                "power_conversion": "",
                "terminology_collision_policy": "Keep source-scoped display names.",
            }
        ],
        errors,
    )
    assert any("power_conversion" in error for error in errors)

    errors = []
    validate_crossover_rules(
        configured,
        [
            {
                "source_ids": ["work_a", "work_b"],
                "conflict_rule": "When rules conflict, the host world's physical limit wins.",
                "power_conversion": "Compare demonstrated cost and range, never title rank alone.",
                "terminology_collision_policy": "Keep source-scoped display names.",
            }
        ],
        errors,
    )
    assert errors == []
