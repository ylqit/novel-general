import hashlib
import json
from pathlib import Path

import pytest

from longform_engine.agent_tasks import load_manifest
from longform_engine.character_expression import approve_voice_samples, character_expression_diagnostics
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
from tests.project_fixtures import mark_project_ready


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
            "characterization_focus": ["Ari hides guilt by narrowing the claim", "Mira tests trust through action"],
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
    manifest = load_manifest(root, "chapter_write:ch001:v1")
    markdown = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")
    packet = task["character_expression_packet"]
    assert packet["schema"] == "character_expression_packet_v1"
    assert packet["featured_character_ids"] == ["lead_ari", "ally_mira"]
    assert len(packet["approved_voice_samples"]) <= 2
    assert "50_workbench/character_packets/ch001.json" in manifest["input_files"]
    assert len(manifest["input_files"]) <= 7
    assert len(markdown) <= 20_000
    assert "Character Performance Packet" in markdown
    assert "verification versus immediate access" in markdown


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


def test_character_editor_rejects_empty_pass_and_requires_featured_character_evidence(tmp_path):
    config, root = seed_ready_project(tmp_path)
    continue_write(config, chapter_number=1, overwrite=True)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    evidence = "Ari aligns the torn receipt before asking who changed the seal."
    draft.write_text(f"# Chapter 1\n\n{evidence}\n", encoding="utf-8")
    review = editorial_review(config, chapter_number=1)
    assert "character_editor" in review.selected_roles
    context_path = (
        root
        / "50_workbench"
        / "editorial_reviews"
        / "agent_tasks"
        / "ch001"
        / "character_editor.context.json"
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))
    result_file = root / "50_workbench" / "editorial_reviews" / "results" / "ch001.character_editor.json"
    base = {
        "schema_version": 2,
        "chapter_number": 1,
        "role_id": "character_editor",
        "verdict": "pass",
        "reviewer_instance_id": context["reviewer_instance_id"],
        "agent_product": "codex-app",
        "agent_version": "test",
        "context_digest_hash": context["context_digest_hash"],
        "independence_mode": "same_host_isolated_context",
        "review_round": context["review_round"],
        "confidence": 0.9,
    }
    result_file.write_text(json.dumps({**base, "items": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="must not submit an empty pass"):
        editorial_submit_review(config, chapter_number=1, role="character_editor", file_path=result_file)

    result_file.write_text(
        json.dumps(
            {
                **base,
                "items": [
                    {
                        "code": "character_evidence_lead_ari",
                        "severity": "PASS",
                        "status": "resolved",
                        "message": "Ari's procedural mask and embodied anxiety remain distinct.",
                        "evidence": [evidence],
                        "character_ids": ["lead_ari"],
                        "recommendation": "preserve this pressure-specific behavior",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    accepted = editorial_submit_review(config, chapter_number=1, role="character_editor", file_path=result_file)
    assert accepted.accepted


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
    relative_sources = {
        chapter: path.relative_to(root).as_posix() for chapter, path in sources.items()
    }

    def evidence(chapter: int, excerpt: str) -> dict:
        text = chapter_texts[chapter]
        start = text.index(excerpt)
        return {
            "source_path": relative_sources[chapter],
            "start": start,
            "end": start + len(excerpt),
            "excerpt": excerpt,
        }

    payload = {
        "schema": "character_expression_review_v1",
        "scope": {"from_chapter": 1, "to_chapter": 2},
        "source_hashes": {
            relative_sources[chapter]: hashlib.sha256(path.read_bytes()).hexdigest()
            for chapter, path in sources.items()
        },
        "character_reviews": [
            {
                "character_id": "lead_ari",
                "verdict": "pass",
                "dimensions": {
                    "voice_fit": "pass",
                    "swapability": "not_observed",
                    "character_as_function": "pass",
                    "embodied_presence": "pass",
                    "narrator_over_explains": "pass",
                    "dialogue_as_exposition": "pass",
                },
                "summary": "Ari acts through procedural pressure.",
                "evidence": [evidence(1, "Ari aligns the receipt")],
            }
        ],
        "chapter_reviews": [
            {
                "chapter_number": 1,
                "verdict": "pass",
                "risks": [],
                "summary": "The scene carries character through action.",
                "evidence": [evidence(1, "asks for the seal record")],
            },
            {
                "chapter_number": 2,
                "verdict": "pass",
                "risks": [],
                "summary": "Mira applies distinct social pressure.",
                "evidence": [evidence(2, "names the cost of waiting")],
            },
        ],
        "cross_chapter_findings": [],
        "verdict": "pass",
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
    assert manifest["canonical_targets"] == []
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
