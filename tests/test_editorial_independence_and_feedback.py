import hashlib
import json
from pathlib import Path

import pytest

from longform_engine.agent_tasks import list_manifests, load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.editorial import editorial_aggregate, editorial_review, editorial_submit_review
from longform_engine.orchestration.pipeline import build_feedback_carryover
from longform_engine.production import editorial_task_is_current
from longform_engine.quality import (
    carry_feedback,
    refresh_feedback_registry,
    transition_feedback,
    truncate_feedback_registry,
)
from longform_engine.quality.feedback import read_registry
from longform_engine.storage import init_project


def test_risk_selected_editorial_v2_isolates_context_and_preserves_minority_blocker(tmp_path):
    config, root = seed_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# Chapter 1\n\nAri follows the clue, but a logic break changes the relationship without evidence.\n",
        encoding="utf-8",
    )

    review = editorial_review(config, chapter_number=1)
    review_payload = json.loads(Path(review.review_file).read_text(encoding="utf-8"))
    selected = set(review.selected_roles)

    assert review_payload["role_selection"]["policy"] == "risk_based_editorial_selection_v1"
    assert {"writing_agent", "serial_verifier", "executive_editor"} <= selected
    assert "planning_chief_editor" not in selected
    assert "continuity_or_relationship_risk" in review.risk_signals
    assert "blocking_P0_P1_risk" in review.risk_signals

    manifests = [
        load_manifest(root, item["task_id"])
        for item in list_manifests(root, chapter_number=1)
        if item["task_type"] == "editorial_review"
    ]
    instances = set()
    for manifest in manifests:
        assert manifest["output_schema"] == "editorial_role_review_v2"
        assert len(manifest["input_files"]) <= 7
        assert not any("/results/" in path for path in manifest["input_files"])
        assert validate_manifest_strict(root, manifest).ok
        work_order = (root / manifest["context_policy"]["compiled_brief"]).read_text(encoding="utf-8")
        assert "Each items[] object must contain: code, severity" in work_order
        assert "stable IDs in each item's `character_ids`" in work_order
        context_path = next(path for path in manifest["input_files"] if path.endswith(".context.json"))
        context = json.loads((root / context_path).read_text(encoding="utf-8"))
        instances.add(context["reviewer_instance_id"])
        assert len(context["context_digest_hash"]) == 64
        assert context["independence_mode"] == "same_host_isolated_context"
        assert context["excluded_peer_results"]
    assert len(instances) == len(manifests)

    role_items = {
        "serial_verifier": [
            {
                "code": "relationship_stage_jump",
                "severity": "P1",
                "message": "The relationship stage changes without causal evidence.",
                "evidence": ["logic break changes the relationship"],
            }
        ],
        "executive_editor": [
            {
                "code": "relationship_stage_jump",
                "severity": "P2",
                "message": "The same relationship change may be repairable.",
                "evidence": ["changes the relationship without evidence"],
            }
        ],
    }
    for role_id in sorted(selected):
        result_file = root / "50_workbench" / "editorial_reviews" / "results" / f"ch001.{role_id}.json"
        context = json.loads(
            (
                root
                / "50_workbench"
                / "editorial_reviews"
                / "agent_tasks"
                / "ch001"
                / f"{role_id}.context.json"
            ).read_text(encoding="utf-8")
        )
        items = role_items.get(role_id, [])
        result_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "chapter_number": 1,
                    "role_id": role_id,
                    "verdict": "needs_revision" if role_id == "serial_verifier" else (
                        "conditional_pass" if items else "pass"
                    ),
                    "items": items,
                    "reviewer_instance_id": context["reviewer_instance_id"],
                    "agent_product": "codex-app",
                    "agent_version": "test",
                    "context_digest_hash": context["context_digest_hash"],
                    "independence_mode": "same_host_isolated_context",
                    "review_round": context["review_round"],
                    "confidence": 0.83,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        editorial_submit_review(
            config,
            chapter_number=1,
            role=role_id,
            file_path=result_file,
        )

    aggregate = editorial_aggregate(config, chapter_number=1)
    payload = json.loads(Path(aggregate.aggregate_file).read_text(encoding="utf-8"))
    row = next(item for item in aggregate.disagreement_matrix if item["issue_code"] == "relationship_stage_jump")

    assert not aggregate.missing_roles
    assert row["severity_conflict"] is True
    assert row["minority_P0_P1"] is True
    assert aggregate.minority_blockers
    assert "minority_P0_P1" in aggregate.need_human_reasons
    assert "editorial_evidence_conflict" in aggregate.need_human_reasons
    assert payload["human_decisions"]
    assert payload["feedback_registry"]["status"] == "updated"
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_risk_selected_editorial_v2_recognizes_chinese_payoff_and_access_gain(tmp_path):
    config, root = seed_project(tmp_path)
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "chapter_duty": "完成军粮失踪案第一层闭环",
                "reader_gain": "追回军粮并取得三日旧账册调查权限",
                "ending_mode": "question",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# 第一章\n\n沈阙追回军粮，也拿到了三日旧账册调查权限。\n",
        encoding="utf-8",
    )

    review = editorial_review(config, chapter_number=1)

    assert "major_payoff_or_reveal" in review.risk_signals
    assert {"planning_chief_editor", "reader_quality_reviewer"} <= set(review.selected_roles)


def test_editorial_v2_rejects_stale_context_without_canonical_pollution(tmp_path):
    config, root = seed_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nAri inspects one bounded clue.\n", encoding="utf-8")
    review = editorial_review(config, chapter_number=1)
    role_id = review.selected_roles[0]
    context = json.loads(
        (
            root
            / "50_workbench"
            / "editorial_reviews"
            / "agent_tasks"
            / "ch001"
            / f"{role_id}.context.json"
        ).read_text(encoding="utf-8")
    )
    result_file = root / "50_workbench" / "editorial_reviews" / "results" / f"ch001.{role_id}.json"
    result_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "chapter_number": 1,
                "role_id": role_id,
                "verdict": "pass",
                "items": [],
                "reviewer_instance_id": context["reviewer_instance_id"],
                "agent_product": "codex-app",
                "agent_version": "test",
                "context_digest_hash": "0" * 64,
                "independence_mode": "same_host_isolated_context",
                "review_round": context["review_round"],
                "confidence": 0.9,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="context_digest_hash"):
        editorial_submit_review(config, chapter_number=1, role=role_id, file_path=result_file)

    assert not result_file.with_name(f"ch001.{role_id}.normalized.json").exists()
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not any((root / "60_rag").rglob("ch001*"))


def test_partial_editorial_submissions_do_not_invalidate_peer_contexts(tmp_path):
    config, root = seed_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# Chapter 1\n\nAri chooses the marked retreat route and records its cost.\n",
        encoding="utf-8",
    )
    refresh_feedback_registry(
        root,
        chapter_number=0,
        observations=[feedback_observation("prior_style_risk", "P2", "prior")],
    )
    registry = root / "50_workbench" / "quality_feedback" / "registry.jsonl"
    registry_before = hashlib.sha256(registry.read_bytes()).hexdigest()
    review = editorial_review(config, chapter_number=1)

    for role_id in review.selected_roles:
        context_path = (
            root
            / "50_workbench"
            / "editorial_reviews"
            / "agent_tasks"
            / "ch001"
            / f"{role_id}.context.json"
        )
        context = json.loads(context_path.read_text(encoding="utf-8"))
        assert "50_workbench/quality_feedback/registry.jsonl" not in context[
            "provenance_source_files"
        ]
        result_file = (
            root
            / "50_workbench"
            / "editorial_reviews"
            / "results"
            / f"ch001.{role_id}.json"
        )
        result_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "chapter_number": 1,
                    "role_id": role_id,
                    "verdict": "pass",
                    "items": [],
                    "reviewer_instance_id": context["reviewer_instance_id"],
                    "agent_product": "codex-app",
                    "agent_version": "test",
                    "context_digest_hash": context["context_digest_hash"],
                    "independence_mode": context["independence_mode"],
                    "review_round": context["review_round"],
                    "confidence": 0.9,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        submitted = editorial_submit_review(
            config,
            chapter_number=1,
            role=role_id,
            file_path=result_file,
        )
        assert submitted.accepted is True
        if role_id != review.selected_roles[-1]:
            assert hashlib.sha256(registry.read_bytes()).hexdigest() == registry_before

    aggregate = editorial_aggregate(config, chapter_number=1)
    assert not aggregate.missing_roles
    assert not aggregate.need_human


def test_editorial_aggregate_rejects_results_for_replaced_chapter_candidate(tmp_path):
    config, root = seed_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nAri inspects the first bounded clue.\n", encoding="utf-8")
    review = editorial_review(config, chapter_number=1)
    for role_id in review.selected_roles:
        context = json.loads(
            (
                root
                / "50_workbench"
                / "editorial_reviews"
                / "agent_tasks"
                / "ch001"
                / f"{role_id}.context.json"
            ).read_text(encoding="utf-8")
        )
        result_file = (
            root
            / "50_workbench"
            / "editorial_reviews"
            / "results"
            / f"ch001.{role_id}.json"
        )
        result_file.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "chapter_number": 1,
                    "role_id": role_id,
                    "verdict": "pass",
                    "items": [],
                    "reviewer_instance_id": context["reviewer_instance_id"],
                    "agent_product": "codex-app",
                    "agent_version": "test",
                    "context_digest_hash": context["context_digest_hash"],
                    "independence_mode": context["independence_mode"],
                    "review_round": context["review_round"],
                    "confidence": 0.9,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        editorial_submit_review(config, chapter_number=1, role=role_id, file_path=result_file)

    first = editorial_aggregate(config, chapter_number=1)
    first_payload = json.loads(Path(first.aggregate_file).read_text(encoding="utf-8"))
    assert first_payload["source_sha256"] == hashlib.sha256(draft.read_bytes()).hexdigest()

    draft.write_text("# Chapter 1\n\nAri inspects a replacement clue with a changed outcome.\n", encoding="utf-8")
    stale = editorial_aggregate(config, chapter_number=1)
    stale_payload = json.loads(Path(stale.aggregate_file).read_text(encoding="utf-8"))

    assert stale.missing_roles == tuple(review.selected_roles)
    assert stale_payload["accepted_results"] == []
    assert len(stale_payload["stale_results"]) == len(review.selected_roles)
    assert "stale_editorial_results" in stale.need_human_reasons
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_editorial_task_currency_tracks_isolated_context_and_current_draft(tmp_path):
    config, root = seed_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nAri follows one bounded clue.\n", encoding="utf-8")
    editorial_review(config, chapter_number=1)
    tasks = [
        task
        for task in list_manifests(root, chapter_number=1)
        if task.get("task_type") == "editorial_review"
    ]

    assert tasks
    assert all(editorial_task_is_current(root, 1, task) for task in tasks)

    draft.write_text("# Chapter 1\n\nAri follows a replacement clue.\n", encoding="utf-8")

    assert all(not editorial_task_is_current(root, 1, task) for task in tasks)


def test_editorial_v2_requires_exact_chapter_evidence_for_blocking_finding(tmp_path):
    config, root = seed_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# Chapter 1\n\nAri follows the clue, but a logic break changes the relationship.\n",
        encoding="utf-8",
    )
    review = editorial_review(config, chapter_number=1)
    role_id = "serial_verifier"
    assert role_id in review.selected_roles
    context = json.loads(
        (
            root
            / "50_workbench"
            / "editorial_reviews"
            / "agent_tasks"
            / "ch001"
            / f"{role_id}.context.json"
        ).read_text(encoding="utf-8")
    )
    result_file = root / "50_workbench" / "editorial_reviews" / "results" / f"ch001.{role_id}.json"
    result_file.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "chapter_number": 1,
                "role_id": role_id,
                "verdict": "needs_revision",
                "items": [
                    {
                        "code": "relationship_stage_jump",
                        "severity": "P1",
                        "message": "The relationship changes without support.",
                        "evidence": ["this excerpt does not exist"],
                    }
                ],
                "reviewer_instance_id": context["reviewer_instance_id"],
                "agent_product": "codex-app",
                "agent_version": "test",
                "context_digest_hash": context["context_digest_hash"],
                "independence_mode": context["independence_mode"],
                "review_round": context["review_round"],
                "confidence": 0.8,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="evidence must match exact text"):
        editorial_submit_review(config, chapter_number=1, role=role_id, file_path=result_file)
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_feedback_registry_ttl_recurrence_resolution_limit_and_rollback(tmp_path):
    config, root = seed_project(tmp_path)
    canonical_before = canonical_hashes(root)
    observations = [
        feedback_observation(f"issue_{index}", "P2", f"hash-{index}")
        for index in range(7)
    ]
    observations.append(feedback_observation("dialogue_sameness", "P1", "first"))
    observations.append(feedback_observation("continuity_risk", "P1", "stable"))
    refresh_feedback_registry(root, chapter_number=1, observations=observations)

    carried = carry_feedback(root, target_chapter=2, task_type="chapter_write")
    assert len(carried) == 5
    assert all(item["status"] == "carried" for item in carried)

    refresh_feedback_registry(
        root,
        chapter_number=2,
        observations=[feedback_observation("dialogue_sameness", "P1", "second")],
    )
    dialogue = next(item for item in read_registry(root) if item["issue_code"] == "dialogue_sameness")
    assert dialogue["recurrence_count"] == 2
    assert dialogue["gate_gaming_risk"] is True

    transition_feedback(
        config,
        feedback_id=dialogue["feedback_id"],
        status="resolved",
        evidence="Chapter 2 gives each speaker a distinct goal and speech act.",
    )
    refresh_feedback_registry(
        root,
        chapter_number=2,
        observations=[feedback_observation("dialogue_sameness", "P1", "second")],
    )
    assert next(
        item for item in read_registry(root) if item["feedback_id"] == dialogue["feedback_id"]
    )["status"] == "resolved"
    assert dialogue["feedback_id"] not in {
        item["feedback_id"]
        for item in carry_feedback(root, target_chapter=3, task_type="chapter_write")
    }

    carry_feedback(root, target_chapter=5, task_type="chapter_write")
    records = read_registry(root)
    assert all(
        item["status"] == "expired"
        for item in records
        if item["issue_code"].startswith("issue_")
    )
    continuity = next(item for item in records if item["issue_code"] == "continuity_risk")
    assert continuity["status"] == "resolved"
    assert "auto:no_recurrence_for_two_completed_chapters" in continuity["resolution_evidence"]

    refresh_feedback_registry(
        root,
        chapter_number=6,
        observations=[feedback_observation("late_issue", "P2", "late")],
    )
    changed = truncate_feedback_registry(root, to_chapter=2)
    assert changed == ("50_workbench/quality_feedback/registry.jsonl",)
    assert not any(item["issue_code"] == "late_issue" for item in read_registry(root))
    assert canonical_hashes(root) == canonical_before


def test_corrupt_feedback_registry_uses_bounded_fallback_without_canonical_writes(tmp_path):
    _, root = seed_project(tmp_path)
    canonical_before = canonical_hashes(root)
    registry = root / "50_workbench" / "quality_feedback" / "registry.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("{broken\n", encoding="utf-8")
    humanize = root / "50_workbench" / "humanizer_tasks" / "ch001.humanize_check.json"
    humanize.parent.mkdir(parents=True, exist_ok=True)
    humanize.write_text(
        json.dumps(
            {
                "passed": False,
                "issues": [
                    {
                        "code": "dialogue_sameness",
                        "severity": "P1",
                        "message": "The speakers use the same speech acts.",
                    }
                ],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    carryover = build_feedback_carryover(root, 2)

    assert carryover["status"] == "available"
    assert carryover["items"][0]["schema"] == "quality_feedback_fallback_v1"
    assert len(carryover["items"]) <= 5
    assert any("warning" in note.lower() for note in carryover["notes"])
    assert canonical_hashes(root) == canonical_before


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config), project.root


def feedback_observation(issue_code: str, severity: str, evidence_hash: str) -> dict:
    return {
        "issue_code": issue_code,
        "severity": severity,
        "kind": "editorial_aggregate",
        "source_path": "50_workbench/editorial_reviews/ch001.aggregate.json",
        "owner_task": "chapter_write:ch002",
        "summary": f"Resolve {issue_code} through causal scene work.",
        "evidence_hash": hashlib.sha256(evidence_hash.encode("utf-8")).hexdigest(),
    }


def canonical_hashes(root: Path) -> dict[str, str]:
    prefixes = ("10_bible", "20_outline", "30_state", "40_manuscript/final", "60_rag", "70_runtime/db")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for prefix in prefixes
        for path in sorted((root / prefix).rglob("*"))
        if path.is_file()
    }
