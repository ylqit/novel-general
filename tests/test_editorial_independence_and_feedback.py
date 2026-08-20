import hashlib
import json
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import EVIDENCE_REVIEW_SCHEMA
from longform_engine.agent_tasks import list_manifests, load_manifest, validate_manifest_strict
from longform_engine.chapter_contract import stamp_chapter_contract
from longform_engine.config import load_project_config
from longform_engine.editorial import editorial_aggregate, editorial_review, editorial_submit_review
from longform_engine.human_story_review import (
    apply_human_story_review,
    create_human_story_review_task,
    validate_human_story_review,
)
from longform_engine.production import editorial_task_is_current, production_next
from longform_engine.quality import (
    editorial_patterns_for_task,
    pattern_registry_status,
    rebuild_editorial_pattern_registry,
    refresh_editorial_pattern_registry,
    transition_editorial_pattern,
    truncate_editorial_pattern_registry,
)
from longform_engine.quality.editorial_patterns import read_pattern_registry
from longform_engine.distribution import doctor_payload
from longform_engine.repair_coordination import create_repair_synthesis_task, review_barrier_status
from longform_engine.roles import load_role_registry
from longform_engine.storage import init_project
from tests.project_fixtures import checked_review_coverage, mark_project_ready


def test_risk_selected_editorial_v2_isolates_context_and_preserves_minority_blocker(tmp_path):
    config, root = seed_project(tmp_path)
    config.data["quality"]["profile"]["strictness"] = "light"
    config.data["quality"]["semantic_review_milestones"] = []
    config.data["quality"]["semantic_review_boundaries"] = False
    config.data["quality"]["reader_payoff"]["review_mode"] = "off"
    config.data["quality"].setdefault("semantic_pacing", {})["review_mode"] = "off"
    config.data.setdefault("editorial", {})["review_roles"] = [
        "planning_chief_editor",
        "scene_prose_editor",
        "character_editor",
    ]
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# Chapter 1\n\nAri follows the clue, but a logic break changes the relationship without evidence.\n",
        encoding="utf-8",
    )

    review = editorial_review(config, chapter_number=1)
    review_payload = json.loads(Path(review.review_file).read_text(encoding="utf-8"))
    selected = set(review.selected_roles)

    assert review_payload["role_selection"]["policy"] == "risk_based_editorial_selection_v1"
    assert {"scene_prose_editor", "planning_chief_editor"} <= selected
    assert "continuity_or_relationship_risk" in review.risk_signals
    assert "blocking_P0_P1_risk" not in review.risk_signals

    manifests = [
        load_manifest(root, item["task_id"])
        for item in list_manifests(root, chapter_number=1)
        if item["task_type"] == "editorial_review"
    ]
    instances = set()
    for manifest in manifests:
        inputs = [item["path"] for item in manifest["io"]["inputs"]]
        assert manifest["io"]["output"]["protocol"] == EVIDENCE_REVIEW_SCHEMA
        assert len(inputs) <= 7
        assert not any("/results/" in path for path in inputs)
        assert validate_manifest_strict(root, manifest).ok
        work_order_path = next(
            item["path"] for item in manifest["io"]["inputs"] if item["reason"] == "compiled_task_brief"
        )
        work_order = (root / work_order_path).read_text(encoding="utf-8")
        assert "Each coverage dimension is an object" in work_order
        assert "Each finding uses code, severity, certainty" in work_order
        assert "CLI binds them" in work_order
        context_path = next(path for path in inputs if path.endswith(".context.json"))
        context = json.loads((root / context_path).read_text(encoding="utf-8"))
        instances.add(context["reviewer_instance_id"])
        assert len(context["context_digest_hash"]) == 64
        assert context["independence_mode"] == "same_host_isolated_context"
        assert context["excluded_peer_results"]
    assert len(instances) == len(manifests)

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
        if role_id == "character_editor":
            findings = [editorial_finding("SPEAKER_AMBIGUOUS", "voice_distinction", "P1", draft)]
        elif role_id == "scene_prose_editor":
            findings = [editorial_finding("SPEAKER_AMBIGUOUS", "dialogue_attribution", "P2", draft)]
        else:
            findings = []
        result_file.write_text(
            json.dumps(
                editorial_payload(root, draft, role_id, findings=findings),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        submit_editorial_result(config, root, role_id, result_file)

    aggregate = editorial_aggregate(config, chapter_number=1)
    payload = json.loads(Path(aggregate.aggregate_file).read_text(encoding="utf-8"))
    row = next(item for item in aggregate.disagreement_matrix if item["issue_code"] == "SPEAKER_AMBIGUOUS")

    assert not aggregate.missing_roles
    assert row["severity_conflict"] is True
    assert row["minority_P0_P1"] is True
    assert aggregate.minority_blockers
    assert "minority_P0_P1" in aggregate.need_human_reasons
    assert "editorial_evidence_conflict" in aggregate.need_human_reasons
    assert payload["human_decisions"]
    assert payload["editorial_pattern_registry"]["status"] == "updated"
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()

    gate_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "gate_result.json").write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "passed": True,
                "severity": "PASS",
                "source_path": "40_manuscript/draft/ch001.md",
                "source_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
                "failures": [],
                "warnings": [],
                "agent_semantic_review": {"required": False, "status": "not_required"},
                "workflow_stage": "review_barrier",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    human_task = create_human_story_review_task(config, chapter_number=1)
    human_file = root / human_task.template_file
    human_payload = json.loads(human_file.read_text(encoding="utf-8"))
    human_payload["checks"] = {
        field: {
            "passed": field not in {
                "scene_causality_and_key_turn_dramatized",
                "protagonist_agency_voice_and_emotion",
            },
            "reason": "The current draft and independent finding require one bounded repair.",
        }
        for field in human_payload["checks"]
    }
    human_payload["decision"] = "repair"
    human_payload["annotations"] = [
        {
            "annotation_id": "agency-speaker-repair",
            "start": 0,
            "end": len(draft.read_text(encoding="utf-8")),
            "text": draft.read_text(encoding="utf-8"),
            "check_id": "protagonist_agency_voice_and_emotion",
            "severity": "P1",
            "action": "expand_scene",
            "intent": "Repair the admitted agency and speaker-ownership problem in this candidate.",
            "must_preserve": [],
            "note": "Repair the admitted agency and speaker-ownership problem in this candidate.",
        }
    ]
    human_file.write_text(
        json.dumps(human_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    assert validate_human_story_review(
        config,
        chapter_number=1,
        file_path=human_file,
    ).ok
    apply_human_story_review(
        config,
        chapter_number=1,
        file_path=human_file,
        approved_by="human",
    )
    barrier = review_barrier_status(config, chapter_number=1)
    next_action = production_next(config)
    synthesis = create_repair_synthesis_task(config, chapter_number=1)
    bundle = json.loads((root / synthesis["review_bundle"]).read_text(encoding="utf-8"))
    minority = next(item for item in bundle["findings"] if item["code"] == "SPEAKER_AMBIGUOUS" and item["severity"] == "P1")

    assert barrier["status"] == "review_bundle_ready"
    assert next_action["status"] == "review_bundle_ready"
    assert next_action["next_command"] == "longform-engine repair synthesis-task project.yaml --chapter 1"
    assert minority["selected"] is True
    assert minority["finding_id"] in bundle["blocking_finding_ids"]


def test_risk_selected_editorial_v2_recognizes_chinese_payoff_and_access_gain(tmp_path):
    config, root = seed_project(tmp_path)
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    card_payload = json.loads(card.read_text(encoding="utf-8"))
    card_payload.update(
        {
            "chapter_duty": "完成军粮失踪案第一层闭环",
            "reader_gain": "追回军粮并取得三日旧账册调查权限",
            "ending_mode": "question",
        }
    )
    stamp_chapter_contract(card_payload)
    card.write_text(json.dumps(card_payload, ensure_ascii=False), encoding="utf-8")
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# 第一章\n\n沈阙追回军粮，也拿到了三日旧账册调查权限。\n",
        encoding="utf-8",
    )

    review = editorial_review(config, chapter_number=1)

    assert "major_payoff_or_reveal" in review.risk_signals
    assert {"planning_chief_editor", "reader_experience_editor"} <= set(review.selected_roles)


def test_editorial_v2_rejects_stale_context_without_canonical_pollution(tmp_path):
    config, root = seed_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nAri inspects one bounded clue.\n", encoding="utf-8")
    review = editorial_review(config, chapter_number=1)
    role_id = review.selected_roles[0]
    result_file = root / "50_workbench" / "editorial_reviews" / "results" / f"ch001.{role_id}.json"
    result_file.write_text(
        json.dumps(editorial_payload(root, draft, role_id), ensure_ascii=False),
        encoding="utf-8",
    )
    draft.write_text("# Chapter 1\n\nAri inspects a replacement clue.\n", encoding="utf-8")
    manifest = editorial_manifest(root, role_id)
    control = validate_production_agent_result(root, manifest, result_file=result_file)

    assert not control.ok
    assert any("SHA-256 drifted" in error for error in control.normalization.errors)

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
    refresh_editorial_pattern_registry(
        root,
        chapter_number=1,
        observations=[pattern_observation("prior_style_risk", "P2", "prior")],
    )
    registry = root / "50_workbench" / "editorial_patterns" / "registry.jsonl"
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
        assert "50_workbench/editorial_patterns/registry.jsonl" not in context[
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
            json.dumps(editorial_payload(root, draft, role_id), ensure_ascii=False),
            encoding="utf-8",
        )
        submitted = submit_editorial_result(config, root, role_id, result_file)
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
        result_file = (
            root
            / "50_workbench"
            / "editorial_reviews"
            / "results"
            / f"ch001.{role_id}.json"
        )
        result_file.write_text(
            json.dumps(editorial_payload(root, draft, role_id), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        submit_editorial_result(config, root, role_id, result_file)

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
    role_id = "planning_chief_editor"
    assert role_id in review.selected_roles
    result_file = root / "50_workbench" / "editorial_reviews" / "results" / f"ch001.{role_id}.json"
    result_file.write_text(
        json.dumps(
            editorial_payload(
                root,
                draft,
                role_id,
                findings=[
                    {
                        **editorial_finding("MAINLINE_MISSING", "mainline_visibility", "P1", draft),
                        "evidence_ids": ["ch001.md@0:99999"],
                    }
                ],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest = editorial_manifest(root, role_id)
    control = validate_production_agent_result(root, manifest, result_file=result_file)

    assert not control.ok
    with pytest.raises(ValueError, match="control-plane|out of bounds"):
        editorial_submit_review(config, chapter_number=1, role=role_id, file_path=result_file)
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_editorial_pattern_registry_ttl_recurrence_resolution_and_rollback(tmp_path):
    config, root = seed_project(tmp_path)
    canonical_before = canonical_hashes(root)
    observations = [
        pattern_observation(f"ISSUE_{index}", "P2", f"hash-{index}")
        for index in range(7)
    ]
    observations.append(pattern_observation("DIALOGUE_SAMENESS", "P1", "first"))
    observations.append(pattern_observation("CONTINUITY_RISK", "P1", "stable"))
    refresh_editorial_pattern_registry(root, chapter_number=1, observations=observations)

    assert editorial_patterns_for_task(root, task_type="chapter_write") == ()
    carried = editorial_patterns_for_task(root, task_type="editorial_review")
    assert len(carried) == 5
    assert all(item["status"] == "monitoring" for item in carried)

    refresh_editorial_pattern_registry(
        root,
        chapter_number=2,
        observations=[pattern_observation("DIALOGUE_SAMENESS", "P1", "second")],
    )
    dialogue = next(item for item in read_pattern_registry(root) if item["finding_code"] == "DIALOGUE_SAMENESS")
    assert dialogue["recurrence_count"] == 2

    evidence = root / "50_workbench" / "editorial_reviews" / "pattern_resolution.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"verified": true}\n', encoding="utf-8")
    transition_editorial_pattern(
        config,
        pattern_id=dialogue["pattern_id"],
        status="resolved",
        evidence=evidence.relative_to(root).as_posix(),
    )
    assert next(
        item for item in read_pattern_registry(root) if item["pattern_id"] == dialogue["pattern_id"]
    )["status"] == "resolved"

    pattern_registry_status(config, target_chapter=5)
    assert all(
        item["status"] == "monitoring"
        for item in read_pattern_registry(root)
        if item["finding_code"].startswith("ISSUE_")
    )
    assert canonical_hashes(root) == canonical_before
    closures = root / "30_state" / "chapter_closures"
    closures.mkdir(parents=True, exist_ok=True)
    for chapter_number in range(2, 5):
        (closures / f"ch{chapter_number:03d}.json").write_text(
            json.dumps({"chapter_number": chapter_number}),
            encoding="utf-8",
        )
    canonical_before = canonical_hashes(root)
    pattern_registry_status(config, target_chapter=5)
    records = read_pattern_registry(root)
    assert all(
        item["status"] == "expired"
        for item in records
        if item["finding_code"].startswith("ISSUE_")
    )
    continuity = next(item for item in records if item["finding_code"] == "CONTINUITY_RISK")
    assert continuity["status"] == "monitoring"

    refresh_editorial_pattern_registry(
        root,
        chapter_number=6,
        observations=[pattern_observation("LATE_ISSUE", "P2", "late")],
    )
    changed = truncate_editorial_pattern_registry(root, to_chapter=2)
    assert changed == ("50_workbench/editorial_patterns/registry.jsonl",)
    assert not any(item["finding_code"] == "LATE_ISSUE" for item in read_pattern_registry(root))
    assert canonical_hashes(root) == canonical_before


def test_corrupt_editorial_pattern_registry_requires_explicit_rebuild(tmp_path):
    config, root = seed_project(tmp_path)
    registry = root / "50_workbench" / "editorial_patterns" / "registry.jsonl"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text("{broken\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid editorial pattern JSONL"):
        pattern_registry_status(config)
    doctor = doctor_payload("all", project=str(config.path))
    check = next(item for item in doctor["checks"] if item["name"] == "editorial_pattern_registry")
    assert check["ok"] is False
    canonical_before = canonical_hashes(root)
    rebuilt = rebuild_editorial_pattern_registry(config)
    assert rebuilt.total == 0
    assert canonical_hashes(root) == canonical_before


def editorial_payload(root: Path, source: Path, role_id: str, *, findings: list[dict] | None = None) -> dict:
    contract = load_role_registry().resolve("editorial_review", declared_role_id=role_id)
    items = list(findings or [])
    return {
        "schema": EVIDENCE_REVIEW_SCHEMA,
        "verdict": "repair" if items else "pass",
        "coverage": checked_review_coverage(
            root,
            source,
            contract.review_dimensions,
            canonical_dimensions=contract.canonical_ref_dimensions,
        ),
        "findings": items,
    }


def editorial_finding(code: str, dimension: str, severity: str, source: Path) -> dict:
    text = source.read_text(encoding="utf-8")
    return {
        "code": code,
        "severity": severity,
        "certainty": "confirmed",
        "diagnosis": "The current scene does not make the speaker or causal move reliably legible.",
        "evidence_ids": [f"{source.name}@0:{len(text)}"],
        "reader_impact": "The reader cannot reliably attribute the decisive move.",
        "repair_target": "Clarify the responsible speaker or causal action without changing the outcome.",
        "preserve": ["accepted scene outcome"],
    }


def editorial_manifest(root: Path, role_id: str) -> dict:
    task = next(
        item
        for item in list_manifests(root, chapter_number=1)
        if item.get("task_type") == "editorial_review"
        and (item.get("role") or {}).get("id") == role_id
    )
    return load_manifest(root, str(task["task_id"]))


def submit_editorial_result(config, root: Path, role_id: str, result_file: Path):
    control = validate_production_agent_result(
        root,
        editorial_manifest(root, role_id),
        result_file=result_file,
    )
    assert control.ok, control.normalization.errors
    return editorial_submit_review(
        config,
        chapter_number=1,
        role=role_id,
        file_path=result_file,
    )


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    mark_project_ready(project.root, config)
    return config, project.root


def pattern_observation(finding_code: str, severity: str, evidence_hash: str) -> dict:
    digest = hashlib.sha256(evidence_hash.encode("utf-8")).hexdigest()
    return {
        "role_id": "scene_prose_editor",
        "finding_code": finding_code,
        "severity": severity,
        "source_path": "50_workbench/editorial_reviews/ch001.aggregate.json",
        "source_sha256": digest,
        "candidate_sha256": hashlib.sha256(b"candidate").hexdigest(),
        "evidence_hash": digest,
    }


def canonical_hashes(root: Path) -> dict[str, str]:
    prefixes = ("10_bible", "20_outline", "30_state", "40_manuscript/final", "60_rag", "70_runtime/db")
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for prefix in prefixes
        for path in sorted((root / prefix).rglob("*"))
        if path.is_file()
    }
