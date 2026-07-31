import hashlib
import json
from pathlib import Path

import pytest

from longform_engine.agent_tasks import list_manifests, load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.creative import (
    humanize_check,
    humanize_semantic_task,
    humanize_semantic_validate,
    humanize_task,
)
from longform_engine.orchestration import WorkflowError, submit_agent_draft
from longform_engine.production import production_loop, production_next
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


SOURCE_TEXT = "# 第一章\n\nAri在城门前核对旧档，Mira守住门口。钟声停下时，他把缺页夹回册中。\n"
CANDIDATE_TEXT = "# 第一章\n\nAri在城门前翻检旧档，Mira守住门口。钟声停下时，他把缺页收回册中。\n"


def test_humanizer_semantic_task_is_strict_and_surfaces_in_production_next(tmp_path):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[1])

    check = humanize_check(config, chapter_number=1, file_path=candidate)

    assert check.passed
    assert check.semantic_review_required
    assert "semantic_review_milestone" in check.semantic_review_reasons
    assert "humanize_semantic_review:ch001:v1" in check.next_command
    manifest = load_manifest(root, "humanize_semantic_review:ch001:v1")
    validation = validate_manifest_strict(root, manifest)
    assert validation.ok, validation.errors
    assert manifest["output_schema"] == "humanizer_semantic_review_v1"
    assert manifest["allowed_output_paths"] == ["50_workbench/humanizer_tasks/ch001.semantic_review.json"]
    assert len(manifest["input_files"]) <= 6
    assert manifest["context_policy"]["max_chars"] == 28_000

    action = production_next(config)
    assert action["status"] == "agent_task_awaiting_agent"
    assert action["task_type"] == "humanize_semantic_review"
    assert action["task_id"] == "humanize_semantic_review:ch001:v1"


def test_humanizer_semantic_pass_allows_submit_and_applies_both_tasks(tmp_path):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[1])
    humanize_check(config, chapter_number=1, file_path=candidate)
    original_draft = (root / "40_manuscript" / "draft" / "ch001.md").read_text(encoding="utf-8")

    with pytest.raises(WorkflowError, match="semantic_review_missing_or_stale"):
        submit_agent_draft(
            config,
            chapter_number=1,
            file_path=candidate,
            agent="codex",
            overwrite=True,
        )
    assert (root / "40_manuscript" / "draft" / "ch001.md").read_text(encoding="utf-8") == original_draft

    output = write_semantic_result(root)
    review = humanize_semantic_validate(config, chapter_number=1, file_path=output)
    assert review.ok
    assert review.passed
    result = submit_agent_draft(
        config,
        chapter_number=1,
        file_path=candidate,
        agent="codex",
        overwrite=True,
    )
    assert Path(result.draft_file).read_text(encoding="utf-8").strip() == CANDIDATE_TEXT.strip()
    statuses = {
        item["task_type"]: item["status"]
        for item in list_manifests(root, chapter_number=1)
        if item["task_type"] in {"humanize", "humanize_semantic_review"}
    }
    assert statuses == {"humanize": "applied", "humanize_semantic_review": "applied"}


def test_production_loop_validates_existing_humanizer_semantic_output_without_apply(tmp_path):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[1])
    humanize_check(config, chapter_number=1, file_path=candidate)
    write_semantic_result(root)

    result = production_loop(config, max_steps=1, no_apply=True)

    assert result["steps"][0]["action"] == "humanize_semantic_validate"
    task = next(
        item
        for item in list_manifests(root, chapter_number=1)
        if item["task_type"] == "humanize_semantic_review"
    )
    assert task["status"] == "validated"
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_humanizer_semantic_rejects_output_path_outside_declared_slot(tmp_path):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[1])
    humanize_check(config, chapter_number=1, file_path=candidate)
    outside = root / "50_workbench" / "humanizer_tasks" / "wrong.json"
    write_json(outside, {"schema": "humanizer_semantic_review_v1"})
    before = canonical_snapshot(root)

    with pytest.raises(ValueError, match="Humanizer semantic result must be"):
        humanize_semantic_validate(config, chapter_number=1, file_path=outside)
    assert canonical_snapshot(root) == before


@pytest.mark.parametrize(
    "mutate, expected_error",
    [
        (
            lambda payload: payload["source"].update({"sha256": "0" * 64}),
            "source.sha256 does not match",
        ),
        (
            lambda payload: payload["candidate"].update({"path": "50_workbench/repair_candidates/wrong.md"}),
            "candidate.path does not match",
        ),
        (
            lambda payload: payload["fact_preservation"][0]["source_span"].update({"text": "错误切片"}),
            "source_span.text does not match",
        ),
        (
            lambda payload: payload["fact_preservation"][0]["canonical_refs"].append("10_bible/undeclared.json"),
            "undeclared canonical file",
        ),
        (
            lambda payload: payload["fact_preservation"][0]["entity_ids"].append("char:unknown"),
            "unknown entity_id",
        ),
    ],
)
def test_humanizer_semantic_invalid_evidence_does_not_pollute_canonical_state(
    tmp_path,
    mutate,
    expected_error,
):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[1])
    humanize_check(config, chapter_number=1, file_path=candidate)
    output = write_semantic_result(root)
    payload = read_json(output)
    mutate(payload)
    write_json(output, payload)
    before = canonical_snapshot(root)

    result = humanize_semantic_validate(config, chapter_number=1, file_path=output)

    assert not result.ok
    assert not result.passed
    assert any(expected_error in error for error in result.errors)
    assert canonical_snapshot(root) == before
    task = next(
        item
        for item in list_manifests(root, chapter_number=1)
        if item["task_type"] == "humanize_semantic_review"
    )
    assert task["status"] == "invalid"


def test_humanizer_semantic_pass_verdict_cannot_override_fact_change(tmp_path):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[1])
    humanize_check(config, chapter_number=1, file_path=candidate)
    output = write_semantic_result(root)
    assert humanize_semantic_validate(config, chapter_number=1, file_path=output).passed
    payload = read_json(output)
    payload["fact_preservation"][0]["status"] = "changed"
    write_json(output, payload)
    before = canonical_snapshot(root)

    contradictory = humanize_semantic_validate(config, chapter_number=1, file_path=output)
    assert not contradictory.ok
    assert not contradictory.passed
    assert any("verdict=pass cannot override" in error for error in contradictory.errors)
    assert canonical_snapshot(root) == before
    task = next(
        item
        for item in list_manifests(root, chapter_number=1)
        if item["task_type"] == "humanize_semantic_review"
    )
    assert task["status"] == "invalid"

    payload["verdict"] = "repair"
    write_json(output, payload)
    repair = humanize_semantic_validate(config, chapter_number=1, file_path=output)
    assert repair.ok
    assert not repair.passed
    assert "fact_changed:actor_action_object" in repair.blocking_findings
    assert "creative humanize-task" in repair.next_command
    assert canonical_snapshot(root) == before


def test_humanizer_candidate_change_after_review_requires_a_new_review(tmp_path):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[1])
    humanize_check(config, chapter_number=1, file_path=candidate)
    output = write_semantic_result(root)
    assert humanize_semantic_validate(config, chapter_number=1, file_path=output).passed
    original_draft = (root / "40_manuscript" / "draft" / "ch001.md").read_text(encoding="utf-8")

    candidate.write_text(CANDIDATE_TEXT.replace("收回册中", "压回册中"), encoding="utf-8")
    refreshed = humanize_check(config, chapter_number=1, file_path=candidate)
    assert refreshed.semantic_review_required
    with pytest.raises(WorkflowError, match="semantic_review_missing_or_stale"):
        submit_agent_draft(
            config,
            chapter_number=1,
            file_path=candidate,
            agent="codex",
            overwrite=True,
        )
    assert (root / "40_manuscript" / "draft" / "ch001.md").read_text(encoding="utf-8") == original_draft


def test_low_change_non_risk_humanizer_candidate_does_not_require_semantic_review(tmp_path):
    config, root, candidate = seed_humanizer_project(tmp_path, milestones=[])
    config.data["quality"]["semantic_review_boundaries"] = False

    check = humanize_check(config, chapter_number=1, file_path=candidate)

    assert check.passed
    assert not check.semantic_review_required
    assert "draft submit" in check.next_command
    assert not (root / "50_workbench" / "humanizer_tasks" / "ch001.semantic_review.agent_task.json").exists()


def test_strict_mode_requires_review_even_without_milestone_or_change_risk(tmp_path):
    config, _root, candidate = seed_humanizer_project(tmp_path, milestones=[])
    config.data["quality"]["assurance_mode"] = "strict"
    config.data["quality"]["semantic_review_boundaries"] = False

    check = humanize_check(config, chapter_number=1, file_path=candidate)

    assert check.semantic_review_required
    assert "strict_or_always_mode" in check.semantic_review_reasons


def seed_humanizer_project(tmp_path, *, milestones):
    base = load_project_config(template="qidian-longform")
    project = init_project(base, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = tmp_path / "novel"
    mark_project_ready(root, config)
    config.data["quality"]["semantic_review_milestones"] = list(milestones)
    config.data["quality"]["semantic_review_boundaries"] = False
    source = root / "40_manuscript" / "draft" / "ch001.md"
    source.write_text(SOURCE_TEXT, encoding="utf-8")
    humanize_task(config, chapter_number=1, source="draft")
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    candidate.write_text(CANDIDATE_TEXT, encoding="utf-8")
    return config, root, candidate


def write_semantic_result(root):
    source = root / "40_manuscript" / "draft" / "ch001.md"
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    source_text = source.read_text(encoding="utf-8")
    candidate_text = candidate.read_text(encoding="utf-8")
    source_span = {"start": 0, "end": 8, "text": source_text[:8]}
    candidate_span = {"start": 0, "end": 8, "text": candidate_text[:8]}
    payload = {
        "schema": "humanizer_semantic_review_v1",
        "chapter_number": 1,
        "source": {"path": "40_manuscript/draft/ch001.md", "sha256": sha256(source_text)},
        "candidate": {
            "path": "50_workbench/repair_candidates/ch001.humanized_candidate.md",
            "sha256": sha256(candidate_text),
        },
        "verdict": "pass",
        "fact_preservation": [
            {
                "dimension": dimension,
                "status": "preserved",
                "source_span": dict(source_span),
                "candidate_span": dict(candidate_span),
                "canonical_refs": ["10_bible/characters.json"],
                "entity_ids": ["lead_ari"],
                "message": f"{dimension} remains supported by the same scene action.",
            }
            for dimension in (
                "actor_action_object",
                "event_outcome",
                "causality",
                "chronology",
                "relationship_state",
                "ability_cost",
                "forbidden_reveals",
            )
        ],
        "chapter_contract": {
            "duty_preserved": True,
            "reader_gain_preserved": True,
            "cost_preserved": True,
            "forbidden_reveals_preserved": True,
        },
        "voice_checks": [
            {
                "character_id": "lead_ari",
                "status": "preserved",
                "candidate_spans": [dict(candidate_span)],
                "message": "Ari remains evidence-led and restrained.",
            }
        ],
        "ai_taste_findings": [],
        "confidence": 0.95,
        "notes": "",
    }
    output = root / "50_workbench" / "humanizer_tasks" / "ch001.semantic_review.json"
    write_json(output, payload)
    return output


def canonical_snapshot(root):
    paths = [
        root / "40_manuscript" / "draft" / "ch001.md",
        root / "40_manuscript" / "final" / "ch001.md",
        root / "30_state" / "story_graph.json",
        root / "70_runtime" / "db" / "longform_engine.sqlite",
    ]
    return {
        str(path.relative_to(root)): path.read_bytes() if path.exists() else None
        for path in paths
    }


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
