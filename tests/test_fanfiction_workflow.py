import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.creative import humanize_check, humanize_task
from longform_engine.editorial import editorial_review
from longform_engine.gates.pipeline import check_fanfiction_source_reproduction
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    assess_project_readiness,
    create_intelligence_task,
    fanfiction_status,
    validate_intelligence_candidate,
)
from longform_engine.intelligence.pipeline import validate_crossover_rules
from longform_engine.orchestration import continue_write, open_book
from longform_engine.publication import export_publication_bundle, publication_risk_report
from longform_engine.storage import init_project


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
            "quality": {"creative_guidance": {"mode": "off"}},
            "length": {
                "total_chapters": 4,
                "target_total_words": 12_000,
                "volume_count": 2,
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
    return {
        "schema": "book_design_candidate_v1",
        "creative_brief": {
            "target_audience": "Chinese fanfiction serial readers.",
            "writing_style": "Concrete scene-led prose with distinct voices.",
            "automation_level": "agent_skill with explicit human apply.",
            "target_scale": "4 chapters.",
            "genre_style_profile": {"genre": "fantasy", "tone": "suspense"},
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


def valid_outline() -> dict:
    return {
        "schema": "outline_design_candidate_v1",
        "book_outline_markdown": "# Outline\n\nA four-chapter divergence arc.",
        "volumes": [
            {
                "id": "vol_01",
                "number": 1,
                "title": "Changed Answer",
                "from_chapter": 1,
                "to_chapter": 2,
                "goal": "Establish the divergence.",
                "escalation": "Make the voice-recording cost visible.",
                "ending_turn": "The key points elsewhere.",
            },
            {
                "id": "vol_02",
                "number": 2,
                "title": "Alternate Gate",
                "from_chapter": 3,
                "to_chapter": 4,
                "goal": "Resolve the new duty conflict.",
                "escalation": "Force both canon characters to choose.",
                "ending_turn": "Close only the alternate gate.",
            },
        ],
        "chapter_plan": [
            {
                "chapter_number": chapter,
                "title": f"Changed Gate {chapter}",
                "duty": "Advance the declared divergence through a character choice.",
                "conflict": "Canon duty collides with the alternate gate.",
                "information_release": "Reveal one bounded consequence.",
                "hook": "Leave a changed concrete problem.",
                "reader_payoff": "Deliver one canon-aware consequence.",
                "volume_id": "vol_01" if chapter <= 2 else "vol_02",
                "forbidden_reveals": ["original gate controller"],
                "canon_refs": ["classic:event_warning"],
                "voice_refs": ["classic:lin_zhou"],
                "divergence_effects": ["the key records his answer"],
                "original_contribution": "Advance the alternate gate mainline.",
                "protected_reveals": ["original gate controller"],
            }
            for chapter in range(1, 5)
        ],
        "foreshadowing_ledger": [
            {
                "id": "thread_recorded_voice",
                "description": "The key stores the changed answer.",
                "plant_chapter": 1,
                "payoff_window": [3, 4],
                "status": "planned",
            }
        ],
    }


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


def apply_fanfiction_foundation(config, root: Path, source_path: Path) -> None:
    canon_task = create_intelligence_task(
        config,
        task_type="fanfiction_canon",
        input_files=[source_path],
    )
    canon_candidate = root / canon_task.candidate_file
    canon_candidate.write_text(json.dumps(valid_canon(source_path), ensure_ascii=False), encoding="utf-8")
    assert validate_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=canon_candidate,
    ).ok
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
        ideation_candidate.write_text(
            json.dumps(
                {
                    "schema": "book_ideation_candidate_v1",
                    "round": round_number,
                    "dimension": dimension,
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
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert validate_intelligence_candidate(
            config,
            task_type="book_ideation",
            file_path=ideation_candidate,
        ).ok
        apply_intelligence_candidate(
            config,
            task_type="book_ideation",
            file_path=ideation_candidate,
            approved_by="human",
        )

    design_task = create_intelligence_task(config, task_type="fanfiction_design")
    design_candidate = root / design_task.candidate_file
    design_candidate.write_text(json.dumps(valid_design(), ensure_ascii=False), encoding="utf-8")
    assert validate_intelligence_candidate(
        config,
        task_type="fanfiction_design",
        file_path=design_candidate,
    ).ok
    apply_intelligence_candidate(
        config,
        task_type="fanfiction_design",
        file_path=design_candidate,
        approved_by="human",
    )

    outline_task = create_intelligence_task(config, task_type="outline_design")
    outline_candidate = root / outline_task.candidate_file
    outline_candidate.write_text(json.dumps(valid_outline(), ensure_ascii=False), encoding="utf-8")
    assert validate_intelligence_candidate(
        config,
        task_type="outline_design",
        file_path=outline_candidate,
    ).ok
    apply_intelligence_candidate(
        config,
        task_type="outline_design",
        file_path=outline_candidate,
        approved_by="human",
    )


def test_unverified_commercial_fanfiction_reaches_writing_and_export_without_rights_block(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    apply_fanfiction_foundation(config, root, source_path)

    assert assess_project_readiness(config).ready
    status = fanfiction_status(config)
    assert status["rights_advisory_only"] is True
    assert status["rights_warnings"][0]["blocking"] is False

    writing = continue_write(config, chapter_number=1)
    manifest = load_manifest(root, "chapter_write:ch001:v1")
    assert validate_manifest_strict(root, manifest).ok
    assert len(manifest["input_files"]) <= 7
    assert "10_bible/fanfiction/source_canon.json" in manifest["input_files"]
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
    assert {"reader_quality_reviewer", "canon_fidelity_reviewer"} <= editorial_roles
    assert any(
        "canon_fidelity_reviewer" in path
        for path in review_payload["agent_task_files"]
    )


def test_fanfiction_manifest_and_invalid_evidence_do_not_pollute_bible(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    task = create_intelligence_task(config, task_type="fanfiction_canon", input_files=[source_path])
    manifest = load_manifest(root, task.task_id)
    assert validate_manifest_strict(root, manifest).ok
    before = (root / "10_bible" / "creative_brief.json").read_bytes()
    candidate = root / task.candidate_file
    payload = valid_canon(source_path)
    payload["sources"][0]["evidence"][0]["source_hash"] = "bad"
    candidate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=candidate,
    )
    assert not validation.ok
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
    candidate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = canonical_snapshot(root)

    validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=candidate,
    )

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
    canon_candidate.write_text(json.dumps(valid_canon(source_path), ensure_ascii=False), encoding="utf-8")
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
    design_candidate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = canonical_snapshot(root)

    validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_design",
        file_path=design_candidate,
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
