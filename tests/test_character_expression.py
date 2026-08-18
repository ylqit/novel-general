import hashlib
import json
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import EVIDENCE_REVIEW_SCHEMA
from longform_engine.agent_tasks import list_manifests, load_manifest
from longform_engine.character_expression import (
    approve_voice_samples,
    build_character_expression_packet,
    character_expression_diagnostics,
)
from longform_engine.config import load_project_config
from longform_engine.editorial import editorial_review, editorial_submit_review
from longform_engine.gates.pipeline import check_style_and_humanizer
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.orchestration import continue_write, open_book
from longform_engine.storage import init_project
from tests.project_fixtures import checked_review_coverage, mark_project_ready


def seed_ready_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    mark_project_ready(tmp_path / "novel", config)
    write_expression_profile(tmp_path / "novel")
    return load_project_config(project.project_config), tmp_path / "novel"


def write_expression_profile(root: Path) -> None:
    payload = {
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
            expression_contract("lead_ari", "records before faces", "narrows claims", "aligns paper edges", "ally_mira"),
            expression_contract("ally_mira", "exits before records", "forces a choice", "changes distance", "lead_ari"),
        ],
    }
    path = root / "10_bible" / "character_expression.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def expression_contract(character_id: str, perception: str, tactic: str, leak: str, contrast: str) -> dict:
    return {
        "character_id": character_id,
        "perception_bias": perception,
        "decision_bias": f"acts through {tactic}",
        "speech_register": f"uses {tactic} under pressure",
        "conversation_tactics": [tactic],
        "emotional_leaks": [leak],
        "physical_presence": f"physical rhythm shaped by {leak}",
        "social_masks": ["competent professional"],
        "private_wants": "to be trusted without surrendering leverage",
        "contradictions": "asks for proof while protecting one private inference",
        "voice_examples": [],
        "contrast_with": [contrast],
    }


def test_chapter_work_order_compiles_character_packet_inside_existing_budget(tmp_path):
    config, root = seed_ready_project(tmp_path)
    plan_path = root / "20_outline" / "chapter_plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan[0].update(
        {
            "pov_character_id": "lead_ari",
            "featured_character_ids": ["lead_ari", "ally_mira"],
                "characterization_focus": ["lead_ari", "ally_mira"],
            "scene_wants": {"lead_ari": "verify the seal", "ally_mira": "force access before closure"},
            "opposing_wants": ["verification versus immediate access"],
            "hidden_agenda": ["Ari recognizes his father's filing mark"],
            "relationship_move": "move from procedural tolerance to bounded trust",
            "irreversible_action": "sign a joint evidence receipt",
            "emotional_aftereffect": "both lose the option to deny cooperation",
        }
    )
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    continue_write(config, chapter_number=1, overwrite=True)

    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8"))
    manifest = load_manifest(root, "chapter_write:ch001:v4")
    markdown = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")
    inventory = task["fact_inventory_summary"]
    assert inventory["schema"] == "chapter_fact_inventory_summary_v1"
    assert inventory["categories"]["cast"] >= 1
    inputs = [item["path"] for item in manifest["io"]["inputs"]]
    assert inputs == ["50_workbench/writing_tasks/ch001.md"]
    assert not (root / "50_workbench" / "character_packets" / "ch001.json").exists()
    assert len(inputs) <= 7
    assert task["context_plan"]["budget_profile"] == "standard"
    assert task["context_plan"]["estimated_units"] > 0
    assert "登场人物与声音" in markdown
    assert "lead_ari" in markdown and "ally_mira" in markdown
    assert "lead_ari" in markdown and "ally_mira" in markdown


def test_character_packet_does_not_promote_historical_tcs_cast(tmp_path):
    _config, root = seed_ready_project(tmp_path)
    characters_path = root / "10_bible" / "characters.json"
    characters = json.loads(characters_path.read_text(encoding="utf-8"))
    for index in range(5):
        characters.append(
            {
                "id": f"background_{index}",
                "name": f"Background {index}",
                "goal": "Remain available in current state without entering this scene.",
                "flaw": "none",
            }
        )
    characters_path.write_text(json.dumps(characters, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    packet = build_character_expression_packet(
        root,
        chapter_number=1,
        card={
            "chapter_number": 1,
            "pov_character_id": "lead_ari",
            "featured_character_ids": ["lead_ari", "ally_mira"],
        },
        tcs={
            "current_characters": [
                "lead_ari",
                "ally_mira",
                *(f"background_{index}" for index in range(5)),
            ]
        },
    )

    assert packet["featured_character_ids"] == ["lead_ari", "ally_mira"]


def test_dialogue_diagnostics_find_same_voice_without_mandating_dialogue_volume():
    text = (
        '甲说：“我们必须根据证据确认这项规定，所以现在不能离开。”\n'
        '乙说：“我们必须根据证据确认这项规定，所以现在不能离开。”\n'
        '甲问：“所以你也认为必须根据记录处理？”\n'
        '乙问：“所以你也认为必须根据记录处理？”'
    )
    diagnostics = character_expression_diagnostics(text, character_names=["甲", "乙"])
    assert diagnostics["dialogue_char_ratio"] > diagnostics["dialogue_mark_density"]
    assert diagnostics["attribution_coverage"] == 1.0
    assert diagnostics["swapability_risk"] >= 0.72
    assert {item["code"] for item in diagnostics["risks"]} >= {
        "dialogue_swapability_risk",
        "dialogue_as_exposition_risk",
    }


def test_sparse_dialogue_profile_does_not_create_universal_low_dialogue_warning(tmp_path):
    config, root = seed_ready_project(tmp_path)
    profile_path = root / "10_bible" / "character_expression.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["narrative_expression_profile"]["dialogue_mode"] = "sparse"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")

    _failures, warnings = check_style_and_humanizer(
        config,
        "雨水沿石阶落下。阿里核对封泥，把错误的编号压在掌下，然后独自走进档案室。",
    )
    assert not any("dialogue ratio is very low" in warning for warning in warnings)


def test_character_editor_accepts_scoped_p1_with_exact_character_evidence(tmp_path):
    config, root = seed_ready_project(tmp_path)
    continue_write(config, chapter_number=1, overwrite=True)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    evidence = "Ari aligns the torn receipt before asking who changed the seal."
    ally_evidence = "Mira blocks the archive door and demands that Ari name the cost of waiting."
    draft.write_text(f"# Chapter 1\n\n{evidence}\n\n{ally_evidence}\n", encoding="utf-8")
    review = editorial_review(config, chapter_number=1)
    assert "character_editor" in review.selected_roles
    result_file = root / "50_workbench" / "editorial_reviews" / "results" / "ch001.character_editor.json"
    text = draft.read_text(encoding="utf-8")
    start = text.index(ally_evidence)
    result_file.write_text(
        json.dumps(
            {
                "schema": EVIDENCE_REVIEW_SCHEMA,
                "verdict": "repair",
                "coverage": checked_review_coverage(
                    root,
                    draft,
                    ("character_agency", "voice_distinction", "embodied_presence"),
                ),
                "findings": [
                    {
                        "code": "DIALOGUE_SWAP",
                        "severity": "P1",
                        "certainty": "confirmed",
                        "diagnosis": "The reply could be exchanged with Ari without changing tactic or pressure.",
                        "evidence_ids": [f"ch001.md@{start}:{start + len(ally_evidence)}"],
                        "reader_impact": "Mira loses a recognizable social strategy.",
                        "repair_target": "Restore Mira's action-first demand while preserving the blocked doorway.",
                        "preserve": ["blocked doorway", "cost of waiting"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    task = next(
        item
        for item in list_manifests(root, chapter_number=1)
        if item.get("task_type") == "editorial_review"
        and (item.get("role") or {}).get("id") == "character_editor"
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, task["task_id"]),
        result_file=result_file,
    )
    assert control.ok, control.normalization.errors
    accepted = editorial_submit_review(config, chapter_number=1, role="character_editor", file_path=result_file)
    assert accepted.accepted
    assert accepted.need_human


def test_range_character_audit_validates_hash_spans_and_archives_only_to_workbench(tmp_path):
    config, root = seed_ready_project(tmp_path)
    chapter_texts = {
        1: "# Chapter 1\n\nAri aligns the receipt and asks for the seal record.\n",
        2: "# Chapter 2\n\nMira blocks the door and names the cost of waiting.\n",
    }
    sources: dict[int, Path] = {}
    for chapter_number, text in chapter_texts.items():
        path = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
        path.write_text(text, encoding="utf-8")
        sources[chapter_number] = path
    task = create_intelligence_task(
        config,
        task_type="character_expression_review",
        from_chapter=1,
        to_chapter=2,
    )
    manifest = load_manifest(root, task.task_id)
    payload = {
        "schema": EVIDENCE_REVIEW_SCHEMA,
        "verdict": "pass",
        "coverage": checked_review_coverage(
            root,
            sources[1],
            ("cross_chapter_character", "voice_drift", "arc_causality"),
            canonical_dimensions=("cross_chapter_character", "voice_drift", "arc_causality"),
            canonical_ref="10_bible/character_expression.json",
        ),
        "findings": [],
    }
    candidate = root / task.candidate_file
    candidate.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    canonical_before = {
        path: (root / path).read_bytes() if (root / path).exists() else b""
        for path in (
            "10_bible/character_expression.json",
            "30_state/story_graph.json",
            "70_runtime/db/novel.db",
        )
    }

    control = validate_production_agent_result(root, manifest, result_file=candidate)
    assert control.ok, control.normalization.errors
    validation = validate_intelligence_candidate(
        config,
        task_type="character_expression_review",
        file_path=candidate,
    )
    assert validation.ok, validation.errors
    applied = apply_intelligence_candidate(
        config,
        task_type="character_expression_review",
        file_path=candidate,
    )
    assert manifest["policy"]["canonical_targets"] == []
    assert (root / "50_workbench" / "character_reviews" / "review_ch001-ch002.json").is_file()
    assert canonical_before == {
        path: (root / path).read_bytes() if (root / path).exists() else b""
        for path in canonical_before
    }
    assert applied.status == "applied"


def test_range_character_audit_rejects_missing_chapter_source(tmp_path):
    config, root = seed_ready_project(tmp_path)
    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# Chapter 1\n\nAri checks the seal.\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="chapter 2"):
        create_intelligence_task(
            config,
            task_type="character_expression_review",
            from_chapter=1,
            to_chapter=2,
        )


def test_voice_samples_require_human_and_exact_final_span(tmp_path):
    _config, root = seed_ready_project(tmp_path)
    final = root / "40_manuscript" / "final" / "ch001.md"
    text = "# Chapter 1\n\nAri said, \"Name the claim, not the fear.\"\n"
    final.write_text(text, encoding="utf-8")
    excerpt = "Name the claim, not the fear."
    start = text.index(excerpt)
    approval = root / "50_workbench" / "character_reviews" / "voice_samples.json"
    approval.parent.mkdir(parents=True, exist_ok=True)
    approval.write_text(
        json.dumps(
            {
                "schema": "character_voice_sample_approval_v1",
                "source_hashes": {
                    "40_manuscript/final/ch001.md": hashlib.sha256(final.read_bytes()).hexdigest()
                },
                "samples": [
                    {
                        "character_id": "lead_ari",
                        "source_path": "40_manuscript/final/ch001.md",
                        "start": start,
                        "end": start + len(excerpt),
                        "excerpt": excerpt,
                        "polarity": "positive",
                        "note": "Ari narrows an emotional claim into a procedural distinction.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="approved_by=human"):
        approve_voice_samples(root, file_path=approval, approved_by="agent")
    result = approve_voice_samples(root, file_path=approval, approved_by="human")

    profile = json.loads((root / result.profile_file).read_text(encoding="utf-8"))
    ari = next(
        item for item in profile["character_expression_contracts"]
        if item["character_id"] == "lead_ari"
    )
    assert ari["voice_examples"] == [
        {
            "polarity": "positive",
            "text": excerpt,
            "note": "Ari narrows an emotional claim into a procedural distinction.",
            "approved": True,
        }
    ]
    assert (root / result.transaction_report).is_file()
