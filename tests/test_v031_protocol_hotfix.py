import hashlib
import json
from pathlib import Path

import pytest

from longform_engine.agent_tasks import (
    AgentTaskContractError,
    build_manifest,
    list_manifests,
    load_manifest,
    validate_manifest_strict,
    write_manifest,
)
from longform_engine.artifacts import artifact_status
from longform_engine.cli import write_repair_candidate_task
from longform_engine.config import load_project_config
from longform_engine.gates import semantic_review_apply, semantic_review_task, semantic_review_validate
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.production import production_next
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


def test_invalid_manifest_is_rejected_before_registration_and_reported_as_orphan(tmp_path):
    config, root = seed_project(tmp_path)
    task_dir = root / "50_workbench" / "writing_tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / "ch001.md"
    task_file.write_text("# Invalid over-budget task\n", encoding="utf-8")
    extra_inputs = []
    for index in range(7):
        path = task_dir / f"context-{index}.txt"
        path.write_text(f"context {index}\n", encoding="utf-8")
        extra_inputs.append(path)
    manifest_file = task_dir / "ch001.agent_task.json"
    manifest = build_manifest(
        root,
        task_type="chapter_write",
        chapter_number=1,
        input_files=[task_file, *extra_inputs],
        allowed_output_paths=[root / "50_workbench" / "agent_drafts" / "ch001.codex.md"],
        output_schema="markdown_chapter_only",
        validate_command=(
            "longform-engine draft submit project.yaml --chapter 1 "
            "--file 50_workbench/agent_drafts/ch001.codex.md --agent codex"
        ),
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
        context_policy={
            "required_files": [task_file, *extra_inputs],
            "max_files": 7,
            "max_chars": 20_000,
            "compiled_brief": task_file,
            "selection_report": task_file,
        },
    )
    index_path = root / "50_workbench" / "agent_tasks" / "agent_task_index.json"
    events_path = root / "50_workbench" / "agent_tasks" / "events.jsonl"
    before_index = index_path.read_bytes() if index_path.exists() else None
    before_events = events_path.read_bytes() if events_path.exists() else None

    with pytest.raises(AgentTaskContractError, match="actual input file count 8 exceeds max_files 7"):
        write_manifest(root, manifest, manifest_file)

    assert not manifest_file.exists()
    assert (index_path.read_bytes() if index_path.exists() else None) == before_index
    assert (events_path.read_bytes() if events_path.exists() else None) == before_events
    status = artifact_status(config)
    assert "50_workbench/writing_tasks/ch001.md" in status.orphan_task_files


def test_fanfiction_semantic_context_is_compiled_to_three_strict_inputs(tmp_path):
    config, root = seed_project(tmp_path)
    continue_write(config, chapter_number=1)
    config.data["creation"] = {"mode": "fanfiction"}
    write_fanfiction_context(root)
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card = read_json(card_path)
    card.update(
        {
            "requires_semantic_review": True,
            "pov_character_id": "lead_ari",
            "featured_character_ids": ["lead_ari"],
            "canon_refs": ["ability_trace"],
            "voice_refs": ["lead_ari"],
        }
    )
    write_json(card_path, card)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(passing_text("fanfiction context"), encoding="utf-8")

    semantic_review_task(config, chapter_number=1)
    manifest = load_manifest(root, "semantic_review:ch001:v1")
    validation = validate_manifest_strict(root, manifest, strict=True)
    context_path = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_context.json"
    context = read_json(context_path)
    actual_chars = sum(len((root / item).read_text(encoding="utf-8")) for item in manifest["input_files"])

    assert validation.ok, validation.errors
    assert len(manifest["input_files"]) == 3
    assert actual_chars <= 18_000
    assert context["schema"] == "semantic_review_context_v1"
    assert context["selection"]["full_canonical_files_exposed"] is False
    assert "10_bible/fanfiction/source_canon.json" in context["allowed_canonical_refs"]
    assert "10_bible/fanfiction/fanfiction_bible.json" in context["allowed_canonical_refs"]
    assert context["sections"]["fanfiction"]["declared_continuity"]["continuity_mode"] == "canon_divergent"


def test_repair_candidate_supersedes_writing_and_legacy_submission_reaches_finalize(tmp_path):
    config, root, repair_path = seed_repair_waiting_for_semantic_review(tmp_path)
    tasks = task_statuses(root)
    submission_path = root / "40_manuscript" / "draft" / "ch001.submission.json"
    submission = read_json(submission_path)

    assert tasks["chapter_write:ch001:v1"] == "superseded"
    assert tasks["repair:ch001:v1"] == "submitted"
    assert submission["candidate_task_id"] == "repair:ch001:v1"
    assert submission["candidate_status"] == "submitted"
    assert production_next(config)["task_type"] == "semantic_review"

    result_path = write_semantic_result(root, repair_path, verdict="pass")
    validation = semantic_review_validate(config, chapter_number=1, file_path=result_path)
    assert validation.ok, validation.errors
    next_action = production_next(config)
    assert next_action["task_type"] == "semantic_review"
    assert next_action["status"] == "agent_task_validated"

    before_next = project_hashes(root)
    production_next(config)
    assert project_hashes(root) == before_next

    legacy_submission = read_json(submission_path)
    for key in (
        "candidate_task_id",
        "candidate_task_type",
        "candidate_revision",
        "candidate_source_path",
        "candidate_source_hash",
        "candidate_status",
        "replaces_task_ids",
    ):
        legacy_submission.pop(key, None)
    legacy_submission["schema_version"] = 1
    write_json(submission_path, legacy_submission)
    protected = protected_hashes(root)

    applied = semantic_review_apply(config, chapter_number=1, file_path=result_path)

    assert applied.blocking_findings == 0
    assert protected_hashes(root) == protected
    tasks = task_statuses(root)
    assert tasks["chapter_write:ch001:v1"] == "superseded"
    assert tasks["repair:ch001:v1"] == "validated"
    assert tasks["semantic_review:ch001:v1"] == "applied"
    normalized_submission = read_json(submission_path)
    assert normalized_submission["candidate_task_id"] == "repair:ch001:v1"
    assert normalized_submission["candidate_status"] == "validated"
    final_action = production_next(config)
    assert final_action["task_type"] != "chapter_write"
    assert final_action["status"] in {
        "awaiting_finalize",
        "ready_for_reader_payoff_task",
        "ready_for_editorial_review",
    }


def test_blocking_semantic_review_invalidates_current_candidate_and_requests_new_repair(tmp_path):
    config, root, repair_path = seed_repair_waiting_for_semantic_review(tmp_path)
    result_path = write_semantic_result(root, repair_path, verdict="fail")
    validation = semantic_review_validate(config, chapter_number=1, file_path=result_path)
    assert validation.ok, validation.errors
    protected = protected_hashes(root)

    applied = semantic_review_apply(config, chapter_number=1, file_path=result_path)

    assert applied.blocking_findings == 1
    assert protected_hashes(root) == protected
    tasks = task_statuses(root)
    assert tasks["chapter_write:ch001:v1"] == "superseded"
    assert tasks["repair:ch001:v1"] == "invalid"
    assert tasks["semantic_review:ch001:v1"] == "applied"
    assert read_json(root / "40_manuscript" / "draft" / "ch001.submission.json")["candidate_status"] == "invalid"
    action = production_next(config)
    assert action["status"] == "agent_task_invalid"
    assert action["task_type"] == "repair"
    assert "--candidate-only" in action["next_command"]
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def seed_project(tmp_path: Path):
    project = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    config = load_project_config(project.project_config, cli_overrides={"editorial": {"review_mode": "off"}})
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    config.data["length"]["chapter_word_count"]["hard_min"] = 20
    return config, root


def seed_repair_waiting_for_semantic_review(tmp_path: Path):
    config, root = seed_project(tmp_path)
    continue_write(config, chapter_number=1)
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card = read_json(card_path)
    card.update(
        {
            "requires_semantic_review": True,
            "pov_character_id": "lead_ari",
            "featured_character_ids": ["lead_ari"],
        }
    )
    write_json(card_path, card)

    initial = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    initial.write_text("# Chapter 1\n\nshort\n", encoding="utf-8")
    first = submit_agent_draft(config, chapter_number=1, file_path=initial, agent="codex")
    assert not first.passed

    repair = write_repair_candidate_task(config, chapter_number=1, agent="codex")
    assert production_next(config)["task_type"] == "repair"
    repair_path = Path(str(repair["candidate_draft"]))
    repair_path.write_text(passing_text("repair"), encoding="utf-8")
    second = submit_agent_draft(
        config,
        chapter_number=1,
        file_path=repair_path,
        agent="codex",
        overwrite=True,
    )
    gate = read_json(Path(second.gate_result))
    assert not second.passed
    assert gate["workflow_stage"] == "semantic_review_pending"
    assert [item["code"] for item in gate["failures"]] == ["semantic_review_required"]
    return config, root, repair_path


def passing_text(marker: str) -> str:
    sentence = (
        f"{marker} Ari climbs toward North Gate while the caravan waits below; "
        "she chooses the harder road, protects the witness, keeps the promise, "
        "and turns the local conflict into a sharper chapter hook. "
    )
    return "# Chapter 1: North Gate\n\n" + sentence * 22 + "\n\nBut a second witness waits outside?\n"


def write_semantic_result(root: Path, source_path: Path, *, verdict: str) -> Path:
    chapter_path = root / "40_manuscript" / "draft" / "ch001.md"
    text = chapter_path.read_text(encoding="utf-8")
    findings = []
    if verdict == "fail":
        start = text.index("Ari")
        end = start + len("Ari")
        findings.append(
            {
                "code": "unsupported_motivation",
                "category": "motivation",
                "severity": "P1",
                "message": "The current decision lacks canonical motivation evidence.",
                "evidence_span": {"start": start, "end": end, "text": text[start:end]},
                "canonical_refs": ["10_bible/characters.json"],
                "entity_ids": ["lead_ari"],
                "recommendation": "Regenerate a repair candidate with an explicit motivation beat.",
            }
        )
    result = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_result.json"
    write_json(
        result,
        {
            "schema": "semantic_review_result_v1",
            "chapter_number": 1,
            "source_path": "40_manuscript/draft/ch001.md",
            "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "verdict": verdict,
            "findings": findings,
            "notes": f"Reviewed current candidate {source_path.name}.",
        },
    )
    return result


def write_fanfiction_context(root: Path) -> None:
    directory = root / "10_bible" / "fanfiction"
    write_json(
        directory / "source_canon.json",
        {
            "schema": "fanfiction_source_canon_v1",
            "sources": [
                {
                    "source_id": "sao",
                    "characters": [{"id": "lead_ari", "name": "Ari", "voice": "conditional and concise"}],
                    "abilities": [{"id": "ability_trace", "name": "Trace", "limit": "requires a witnessed cost"}],
                    "world_rules": [{"id": "rule_logout", "summary": "logout is unavailable"}],
                }
            ],
        },
    )
    write_json(
        directory / "fanfiction_bible.json",
        {
            "schema": "fanfiction_design_candidate_v1",
            "continuity_mode": "canon_divergent",
            "divergence_points": ["The first raid leader survives at a visible cost."],
            "voice_contracts": [{"character_id": "lead_ari", "voice": "conditional and concise"}],
        },
    )


def task_statuses(root: Path) -> dict[str, str]:
    return {str(item["task_id"]): str(item["status"]) for item in list_manifests(root, chapter_number=1)}


def project_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def protected_hashes(root: Path) -> dict[str, str]:
    protected = [
        root / "30_state" / "story_graph.json",
        *(root / "30_state" / "tcs").rglob("*"),
        *(root / "40_manuscript" / "final").rglob("*"),
        *(root / "60_rag").rglob("*"),
        *(root / "70_runtime" / "db").rglob("*"),
    ]
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in protected
        if path.is_file()
    }


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
