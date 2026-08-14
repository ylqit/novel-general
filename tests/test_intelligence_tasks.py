import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.intelligence import (
    assess_project_readiness,
    apply_intelligence_candidate,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.orchestration import open_book
from longform_engine.production import agent_task_brief, production_next
from longform_engine.storage import init_project
import longform_engine.intelligence.pipeline as intelligence_pipeline


IDEATION_DIMENSIONS = (
    "target_reader_and_reading_context",
    "core_hook",
    "world_core_rule",
    "protagonist_desire_and_flaw",
    "long_conflict",
    "volume_escalation",
    "ending_boundary",
    "taboos_and_unwanted_tropes",
)


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def project_snapshot(root: Path) -> dict[str, bytes]:
    paths = (
        "10_bible/creative_decisions.json",
        "10_bible/creative_brief.json",
        "10_bible/character_expression.json",
        "10_bible/world.md",
        "10_bible/research_canon.jsonl",
        "20_outline/book_outline.md",
        "20_outline/chapter_plan.json",
        "30_state/novel_state.json",
        "30_state/story_graph.json",
    )
    return {item: (root / item).read_bytes() if (root / item).exists() else b"" for item in paths}


def valid_book_candidate() -> dict:
    return {
        "schema": "book_design_candidate_v1",
        "creative_brief": {
            "target_audience": "Chinese longform serial readers.",
            "writing_style": "Concrete and continuous prose.",
            "automation_level": "agent_skill with human approval.",
            "target_scale": "500 chapters.",
            "genre_style_profile": {"genre": "mystery fantasy", "tone": "restrained"},
            "design_decisions": {
                "core_hook": "The archive changes overnight.",
                "world_rule": "Corrections erase a witnessed memory.",
                "protagonist_desire": "Preserve the border archive.",
                "long_conflict": "The court depends on controlled forgetting.",
                "volume_escalation": "Each volume widens the cost.",
                "ending_boundary": "Resolve control of collective memory.",
            },
            "reader_contract": {"core_promise": "Evidence-led mystery."},
            "core_taboo": ["No premature final reveal."],
            "status": "candidate",
        },
        "world_markdown": "# World\n\nRules have visible costs.",
        "power_system_markdown": "# Power\n\nEvery advance spends memory.",
        "characters": [
            {
                "id": "lead_ari",
                "name": "Ari",
                "goal": "Preserve the archive.",
                "flaw": "Distrusts allies.",
                "arc_stages": ["isolated", "tested", "trusting"],
            },
            {
                "id": "ally_mira",
                "name": "Mira",
                "goal": "Expose the treaty.",
                "flaw": "Acts too quickly.",
                "arc_stages": ["outsider", "ally", "partner"],
            },
        ],
        "relationships": [
            {
                "id": "rel_ari_mira",
                "source_id": "lead_ari",
                "target_id": "ally_mira",
                "type": "alliance",
                "stage": "uneasy",
            }
        ],
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
                "character_id": "lead_ari",
                "perception_bias": "Notices altered records before faces.",
                "decision_bias": "Verifies one physical trace before trusting testimony.",
                "speech_register": "Short procedural questions that conceal personal fear.",
                "conversation_tactics": ["narrows claims", "withholds one inference"],
                "emotional_leaks": ["aligns document edges when afraid"],
                "physical_presence": "Still shoulders and ink-stained fingertips.",
                "social_masks": ["neutral archive clerk"],
                "private_wants": "Wants proof that preserving records can also preserve people.",
                "contradictions": "Demands evidence but acts on private guilt.",
                "voice_examples": [],
                "contrast_with": ["ally_mira"],
            },
            {
                "character_id": "ally_mira",
                "perception_bias": "Notices exits, witnesses, and status pressure.",
                "decision_bias": "Tests a weak point before consensus closes it.",
                "speech_register": "Concrete challenges, compressed humor, direct stakes.",
                "conversation_tactics": ["forces a choice", "names the hidden cost"],
                "emotional_leaks": ["paces toward the nearest exit when boxed in"],
                "physical_presence": "Restless stance and quick changes of distance.",
                "social_masks": ["reckless outsider"],
                "private_wants": "Wants to be trusted without surrendering initiative.",
                "contradictions": "Mocks procedure but keeps exact witness times.",
                "voice_examples": [],
                "contrast_with": ["lead_ari"],
            },
        ],
    }


def valid_outline_candidate(config) -> dict:
    total = int(config.data["length"]["total_chapters"])
    count = int(config.data["length"]["volume_count"])
    volumes = []
    chapters = []
    start = 1
    for number in range(1, count + 1):
        remaining = total - start + 1
        size = remaining // (count - number + 1)
        end = start + size - 1
        volume_id = f"vol_{number:02d}"
        volumes.append(
            {
                "id": volume_id,
                "number": number,
                "title": f"Volume {number}",
                "from_chapter": start,
                "to_chapter": end,
                "goal": f"Resolve layer {number}.",
                "escalation": f"Increase the cost at layer {number}.",
                "ending_turn": f"Change the evidence model at turn {number}.",
            }
        )
        for chapter in range(start, end + 1):
            chapters.append(
                {
                    "chapter_number": chapter,
                    "title": f"Evidence {chapter}",
                    "duty": "Advance one bounded investigation step.",
                    "conflict": "Choose speed or verified evidence.",
                    "information_release": "Release one clue.",
                    "hook": "Open the next contradiction.",
                    "reader_payoff": "Reframe one prior detail.",
                    "volume_id": volume_id,
                    "forbidden_reveals": ["final editor identity"],
                }
            )
        start = end + 1
    return {
        "schema": "outline_design_candidate_v1",
        "book_outline_markdown": "# Book Outline\n\nEscalating evidence arcs.",
        "volumes": volumes,
        "chapter_plan": chapters,
        "foreshadowing_ledger": [
            {
                "id": "thread_false_treaty",
                "description": "The treaty witness line was altered.",
                "plant_chapter": 1,
                "payoff_window": [total - 2, total],
                "status": "planned",
            }
        ],
    }


def apply_all_ideation_rounds(config, root: Path) -> None:
    for round_number, dimension in enumerate(IDEATION_DIMENSIONS, start=1):
        task = create_intelligence_task(config, task_type="book_ideation")
        candidate = root / task.candidate_file
        candidate.write_text(
            json.dumps(
                {
                    "schema": "book_ideation_candidate_v1",
                    "round": round_number,
                    "dimension": dimension,
                    "question": f"Choose the project decision for {dimension}.",
                    "options": [
                        {
                            "id": "option_primary",
                            "proposal": f"Approved project decision for {dimension}.",
                            "tradeoffs": ["Focused reader promise.", "Constrains later improvisation."],
                        },
                        {
                            "id": "option_secondary",
                            "proposal": f"Alternative project decision for {dimension}.",
                            "tradeoffs": ["Broader possibility.", "Higher continuity cost."],
                        },
                    ],
                    "selection": {
                        "mode": "selected_option",
                        "option_id": "option_primary",
                        "answer": "",
                    },
                }
            ),
            encoding="utf-8",
        )
        validation = validate_intelligence_candidate(
            config,
            task_type="book_ideation",
            file_path=candidate,
        )
        assert validation.ok, validation.errors
        apply_intelligence_candidate(
            config,
            task_type="book_ideation",
            file_path=candidate,
            approved_by="human",
        )


def test_v1_manifest_is_read_and_normalized_to_v2(tmp_path):
    seed_project(tmp_path)
    root = tmp_path / "novel"
    manifest_file = root / "50_workbench" / "agent_tasks" / "legacy.json"
    manifest_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "task_id": "chapter_write:ch001:legacy",
                "task_type": "chapter_write",
                "chapter_number": 1,
                "input_files": ["project.yaml"],
                "allowed_output_paths": ["50_workbench/agent_drafts/ch001.codex.md"],
                "output_schema": "markdown_chapter_only",
                "validate_command": "longform-engine draft submit project.yaml --chapter 1 --file 50_workbench/agent_drafts/ch001.codex.md --agent codex",
                "apply_command": "longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
                "failure_next_command": "longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
                "hard_boundaries": ["no final", "no rag", "no graph direct", "no sqlite direct"],
                "status": "awaiting_agent",
                "created_at": "2026-01-01T00:00:00+00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    normalized = load_manifest(root, "50_workbench/agent_tasks/legacy.json")
    validation = validate_manifest_strict(root, normalized)

    assert normalized["schema_version"] == 2
    assert normalized["source_schema_version"] == 1
    assert normalized["scope"] == {"kind": "chapter", "chapter_number": 1}
    assert normalized["canonical_targets"] == []
    assert validation.ok, validation.errors


@pytest.mark.parametrize(
    ("task_type", "kwargs"),
    (
        ("book_design", {}),
        ("outline_design", {}),
        ("outline_revision", {"from_chapter": 10, "to_chapter": 20}),
        ("research_synthesis", {"input_files": ["50_workbench/research_inbox/source.md"]}),
        ("style_analysis", {"input_files": ["50_workbench/research_inbox/source.md"]}),
        ("adaptation_analysis", {"input_files": ["50_workbench/research_inbox/source.md"]}),
    ),
)
def test_all_project_intelligence_tasks_are_v2_workbench_only(tmp_path, task_type, kwargs):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    source = root / "50_workbench" / "research_inbox" / "source.md"
    source.write_text("Declared research or authorized style sample.", encoding="utf-8")
    before = project_snapshot(root)

    result = create_intelligence_task(config, task_type=task_type, **kwargs)
    manifest = load_manifest(root, result.task_id)
    validation = validate_manifest_strict(root, manifest)
    brief = agent_task_brief(config, result.task_id)

    assert manifest["schema_version"] == 2
    assert manifest["scope"]["kind"] in {"project", "range"}
    assert all(path.startswith("50_workbench/intelligence_candidates/") for path in manifest["allowed_output_paths"])
    assert {"no bible direct", "no outline direct", "no research canon direct"}.issubset(manifest["hard_boundaries"])
    assert validation.ok, validation.errors
    assert brief["scope"] == manifest["scope"]
    assert brief["canonical_targets"] == manifest["canonical_targets"]
    assert project_snapshot(root) == before


def test_invalid_book_design_candidate_does_not_pollute_canonical_state(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    task = create_intelligence_task(config, task_type="book_design")
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps({"schema": "wrong", "world_markdown": "unsafe"}), encoding="utf-8")
    before = project_snapshot(root)

    result = validate_intelligence_candidate(config, task_type="book_design", file_path=candidate)

    assert not result.ok
    assert result.errors
    assert (root / result.report_file).exists()
    assert project_snapshot(root) == before


def test_opening_flow_requires_applied_book_and_full_outline_before_chapter_work(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(config)
    assert production_next(config)["task_type"] == "book_ideation"
    apply_all_ideation_rounds(config, root)

    book_task = create_intelligence_task(config, task_type="book_design")
    book_candidate = root / book_task.candidate_file
    book_candidate.write_text(json.dumps(valid_book_candidate()), encoding="utf-8")
    apply_intelligence_candidate(config, task_type="book_design", file_path=book_candidate, approved_by="human")
    after_book = production_next(config)
    assert after_book["status"] == "ready_for_intelligence_task"
    assert after_book["task_type"] == "outline_design"

    outline_task = create_intelligence_task(config, task_type="outline_design")
    outline_candidate = root / outline_task.candidate_file
    outline_candidate.write_text(json.dumps(valid_outline_candidate(config)), encoding="utf-8")
    outline_validation = validate_intelligence_candidate(config, task_type="outline_design", file_path=outline_candidate)
    assert outline_validation.ok, outline_validation.errors
    apply_intelligence_candidate(config, task_type="outline_design", file_path=outline_candidate, approved_by="human")

    readiness = assess_project_readiness(config)
    assert not readiness.ready
    assert readiness.required_task_type == "character_expression_design"
    expression_action = production_next(config)
    assert expression_action["task_type"] == "character_expression_design"

    expression_task = create_intelligence_task(config, task_type="character_expression_design")
    expression_candidate = root / expression_task.candidate_file
    expression_candidate.write_text(json.dumps(valid_character_expression()), encoding="utf-8")
    expression_validation = validate_intelligence_candidate(
        config,
        task_type="character_expression_design",
        file_path=expression_candidate,
    )
    assert expression_validation.ok, expression_validation.errors
    apply_intelligence_candidate(
        config,
        task_type="character_expression_design",
        file_path=expression_candidate,
        approved_by="human",
    )

    readiness = assess_project_readiness(config)
    assert readiness.ready
    next_action = production_next(config)
    assert next_action["status"] == "ready_for_intelligence_task"
    assert next_action["task_type"] == "chapter_direction"
    assert next_action["trigger_reasons"] == ["abstract_outline_target", "volume_boundary"]


def test_book_design_v2_applies_character_expression_in_same_human_transaction(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    task = create_intelligence_task(config, task_type="book_design")
    candidate = root / task.candidate_file
    payload = valid_book_candidate()
    expression = valid_character_expression()
    payload.update(
        {
            "schema": "book_design_candidate_v2",
            "narrative_expression_profile": expression["narrative_expression_profile"],
            "character_expression_contracts": expression["character_expression_contracts"],
        }
    )
    candidate.write_text(json.dumps(payload), encoding="utf-8")

    validation = validate_intelligence_candidate(config, task_type="book_design", file_path=candidate)
    assert validation.ok, validation.errors
    apply_intelligence_candidate(config, task_type="book_design", file_path=candidate, approved_by="human")

    canonical = json.loads((root / "10_bible" / "character_expression.json").read_text(encoding="utf-8"))
    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    assert canonical["schema"] == "character_expression_profile_v1"
    assert state["project_intelligence"]["character_expression_design"]["status"] == "applied"


def test_invalid_character_expression_candidate_does_not_pollute_bible(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    task = create_intelligence_task(config, task_type="character_expression_design")
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps({"schema": "character_expression_profile_v1"}), encoding="utf-8")
    before = project_snapshot(root)

    validation = validate_intelligence_candidate(
        config,
        task_type="character_expression_design",
        file_path=candidate,
    )

    assert not validation.ok
    assert project_snapshot(root) == before


@pytest.mark.parametrize(
    ("task_type", "kwargs"),
    (
        ("book_design", {}),
        ("outline_design", {}),
        ("outline_revision", {"from_chapter": 2, "to_chapter": 4}),
        ("research_synthesis", {"input_files": ["50_workbench/research_inbox/source.md"]}),
        ("style_analysis", {"input_files": ["50_workbench/research_inbox/source.md"]}),
        ("adaptation_analysis", {"input_files": ["50_workbench/research_inbox/source.md"]}),
    ),
)
def test_every_intelligence_schema_rejects_invalid_output_without_pollution(tmp_path, task_type, kwargs):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    source = root / "50_workbench" / "research_inbox" / "source.md"
    source.write_text("Declared source.", encoding="utf-8")
    task = create_intelligence_task(config, task_type=task_type, **kwargs)
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps({"schema": load_manifest(root, task.task_id)["output_schema"]}), encoding="utf-8")
    before = project_snapshot(root)

    validation = validate_intelligence_candidate(config, task_type=task_type, file_path=candidate)

    assert not validation.ok
    assert validation.errors
    assert project_snapshot(root) == before


def test_intelligence_candidate_outside_allowed_lane_is_rejected(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    create_intelligence_task(config, task_type="book_design")
    outside = root / "10_bible" / "candidate.json"
    before = project_snapshot(root)

    with pytest.raises(ValueError, match="intelligence_candidates"):
        validate_intelligence_candidate(config, task_type="book_design", file_path=outside)

    assert project_snapshot(root) == before


def test_book_design_requires_human_and_applies_in_transaction(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    task = create_intelligence_task(config, task_type="book_design")
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps(valid_book_candidate(), ensure_ascii=False), encoding="utf-8")
    before = project_snapshot(root)

    valid = validate_intelligence_candidate(config, task_type="book_design", file_path=candidate)
    assert valid.ok, valid.errors
    with pytest.raises(ValueError, match="approved-by human"):
        apply_intelligence_candidate(config, task_type="book_design", file_path=candidate)
    assert project_snapshot(root) == before

    applied = apply_intelligence_candidate(config, task_type="book_design", file_path=candidate, approved_by="human")
    assert applied.status == "applied"
    assert (root / applied.transaction_report).exists()
    assert "Rules have visible costs" in (root / "10_bible" / "world.md").read_text(encoding="utf-8")


def test_intelligence_apply_failure_rolls_back_every_touched_canonical_path(tmp_path, monkeypatch):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    task = create_intelligence_task(config, task_type="book_design")
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps(valid_book_candidate()), encoding="utf-8")
    before = project_snapshot(root)

    def failing_write_targets(project_root, task_type, payload):
        (project_root / "10_bible" / "world.md").write_text("partially changed", encoding="utf-8")
        raise RuntimeError("injected apply failure")

    monkeypatch.setattr(intelligence_pipeline, "write_targets", failing_write_targets)
    with pytest.raises(RuntimeError, match="injected apply failure"):
        apply_intelligence_candidate(config, task_type="book_design", file_path=candidate, approved_by="human")

    assert project_snapshot(root) == before
    rollback_reports = list((root / "70_runtime" / "transactions").glob("*.rollback.json"))
    assert rollback_reports


def test_research_citations_and_adaptation_copy_guard(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    source = root / "50_workbench" / "research_inbox" / "source.md"
    source.write_text("A reviewed source fact.", encoding="utf-8")

    research = create_intelligence_task(
        config,
        task_type="research_synthesis",
        input_files=["50_workbench/research_inbox/source.md"],
    )
    research_candidate = root / research.candidate_file
    research_candidate.write_text(
        json.dumps(
            {
                "schema": "research_synthesis_v1",
                "synthesis_id": "s1",
                "source_files": ["50_workbench/research_inbox/source.md"],
                "source_hashes": {
                    "50_workbench/research_inbox/source.md": sha256(source.read_bytes()).hexdigest()
                },
                "summary": "A bounded synthesis.",
                "claims": [
                    {
                        "claim_id": "c1",
                        "statement": "Fact",
                        "evidence": "A reviewed source fact.",
                        "source_path": "50_workbench/research_inbox/source.md",
                        "source_hash": sha256(source.read_bytes()).hexdigest(),
                        "evidence_span": {"start": 0, "end": len("A reviewed source fact.")},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    assert validate_intelligence_candidate(config, task_type="research_synthesis", file_path=research_candidate).ok
    applied = apply_intelligence_candidate(config, task_type="research_synthesis", file_path=research_candidate)
    assert applied.status == "applied"
    assert "research_canon_claim_v1" in (root / "10_bible" / "research_canon.jsonl").read_text(encoding="utf-8")

    adaptation = create_intelligence_task(
        config,
        task_type="adaptation_analysis",
        input_files=["50_workbench/research_inbox/source.md"],
    )
    adaptation_candidate = root / adaptation.candidate_file
    source_rel = "50_workbench/research_inbox/source.md"
    adaptation_candidate.write_text(
        json.dumps(
            {
                "schema": "adaptation_analysis_v1",
                "source_files": [source_rel],
                "source_hashes": {source_rel: sha256(source.read_bytes()).hexdigest()},
                "structural_patterns": [],
                "pacing_patterns": [],
                "character_methods": [],
                "prose_constraints": [],
                "forbidden_copying": ["no source prose"],
                "quoted_passages": ["copied text"],
            }
        ),
        encoding="utf-8",
    )
    before = project_snapshot(root)
    invalid = validate_intelligence_candidate(config, task_type="adaptation_analysis", file_path=adaptation_candidate)
    assert not invalid.ok
    assert project_snapshot(root) == before
