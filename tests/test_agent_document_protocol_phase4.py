import copy
import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_normalization import normalize_and_validate_agent_result
from longform_engine.agent_results import build_agent_result_template
from longform_engine.agent_tasks import (
    AgentTaskContractError,
    build_manifest,
    list_manifests,
    write_manifest,
)
from longform_engine.artifacts import orphan_agent_task_artifacts
from longform_engine.cli import main
from longform_engine.config import load_project_config
from longform_engine.storage import init_project


def test_phase4_cli_fills_current_source_hash_chapter_planned_facts_and_refs(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "draft" / "ch001.md"
    manifest = review_manifest(root, "reader_payoff_review", [source, chapter_card(root)])
    excerpt = "Ari chooses the north gate."
    start = source.read_text(encoding="utf-8").index(excerpt)
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "pass",
            "evidence": [evidence("draft", start, excerpt)],
            "findings": [],
            "notes": [],
        }
    )
    result_file = write_result(root, manifest, payload)

    result = normalize_and_validate_agent_result(root, manifest, result_file=result_file)

    assert result.ok is True
    normalized = result.normalized_result
    assert normalized["chapter_number"] == 1
    assert normalized["evidence"][0]["source_path"] == "40_manuscript/draft/ch001.md"
    assert normalized["evidence"][0]["source_hash"] == file_hash(source)
    assert normalized["cli_context"]["planned_facts"]["values"] == {
        "chapter_duty": "Force a costly route decision.",
        "reader_gain": "Reveal which route remains open.",
        "cost": "Ari loses the safer option.",
        "promise_refs": ["thread_gate"],
        "relationship_move": "guarded to conditional trust",
    }
    assert normalized["cli_context"]["allowed_canonical_refs"] == [
        {"path": "20_outline/chapter_cards/ch001.json", "sha256": file_hash(chapter_card(root))}
    ]


def test_phase4_exact_span_and_context_hash_are_rechecked_from_current_files(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "draft" / "ch001.md"
    context = root / "50_workbench" / "quality_reviews" / "ch001.context.json"
    write_json(
        context,
        {
            "schema": "reader_payoff_context_v2",
            "source_catalog": [
                {
                    "source_id": "draft",
                    "path": "40_manuscript/draft/ch001.md",
                    "sha256": file_hash(source),
                    "selected_for": ["evidence"],
                    "truncation_reason": "",
                }
            ],
        },
    )
    manifest = review_manifest(root, "reader_payoff_review", [source, context])
    excerpt = "Ari chooses the north gate."
    start = source.read_text(encoding="utf-8").index(excerpt)
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "pass",
            "evidence": [evidence("draft", start, "Ari chooses the south gate.")],
            "findings": [],
            "notes": [],
        }
    )
    result_file = write_result(root, manifest, payload)

    wrong_span = normalize_and_validate_agent_result(root, manifest, result_file=result_file)
    assert wrong_span.status == "invalid"
    assert any("exact span does not match current source" in item for item in wrong_span.errors)

    source.write_text(source.read_text(encoding="utf-8") + "The lock clicks.\n", encoding="utf-8")
    stale_context = normalize_and_validate_agent_result(root, manifest, result_file=result_file)
    assert stale_context.status == "invalid"
    assert any("declared source hash drifted" in item for item in stale_context.errors)


def test_phase4_strict_state_checks_relation_entity_knowledge_and_foreshadow_window(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "final" / "ch001.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(chapter_text(), encoding="utf-8")
    manifest = semantic_manifest(root, source)
    excerpt = "Ari chooses the north gate."
    start = source.read_text(encoding="utf-8").index(excerpt)
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "pass",
            "evidence": [evidence("final", start, excerpt)],
            "deltas": [
                delta("rel_change", "rel_ari_bo", "relationship_state", "hostile", "conditional"),
                delta("knowledge", "unknown_character", "knowledge_scope", [], ["north gate"]),
                delta("payoff", "thread_gate", "foreshadow_status", "open", "payoff"),
            ],
            "notes": [],
        }
    )
    result_file = write_result(root, manifest, payload)

    result = normalize_and_validate_agent_result(root, manifest, result_file=result_file)

    assert result.status == "invalid"
    assert any("expected relationship state 'guarded'" in item for item in result.errors)
    assert any("unknown character `unknown_character`" in item for item in result.errors)
    assert "foreshadow_payoff_outside_window:thread_gate" in result.need_human_reasons


def test_phase4_v1_and_v2_legacy_results_normalize_without_trusting_agent_planned_fields(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "draft" / "ch001.md"
    manifest_v2 = review_manifest(root, "reader_payoff_review", [source, chapter_card(root)])
    manifest_v1 = to_v1_manifest(manifest_v2)
    excerpt = "Ari chooses the north gate."
    text = source.read_text(encoding="utf-8")
    start = text.index(excerpt)
    legacy = {
        "schema": "reader_payoff_review_v1",
        "chapter_number": 1,
        "source_path": "40_manuscript/draft/ch001.md",
        "source_hash": file_hash(source),
        "planned": {"chapter_duty": "Agent tries to replace the plan."},
        "observed": {"duty_fulfilled": True},
        "evidence_spans": [{"start": start, "end": start + len(excerpt), "text": excerpt, "supports": ["duty"]}],
        "fake_payoff_flags": [],
        "craft_observation": {},
        "verdict": "pass",
        "recommendations": [],
    }
    result_file = write_result(root, manifest_v2, legacy)

    normalized_v1 = normalize_and_validate_agent_result(root, manifest_v1, result_file=result_file)
    normalized_v2 = normalize_and_validate_agent_result(root, manifest_v2, result_file=result_file)

    assert normalized_v1.ok is normalized_v2.ok is True
    assert normalized_v1.adapter == normalized_v2.adapter == "reader_payoff_review_v1"
    assert normalized_v1.normalized_result["evidence"] == normalized_v2.normalized_result["evidence"]
    assert normalized_v1.normalized_result["cli_context"]["planned_facts"]["values"]["chapter_duty"] == (
        "Force a costly route decision."
    )
    assert "Agent tries to replace" not in json.dumps(normalized_v1.normalized_result, ensure_ascii=False)


def test_phase4_legacy_semantic_keeps_knowledge_and_state_preconditions_strict(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "final" / "ch001.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(chapter_text(), encoding="utf-8")
    manifest = semantic_manifest(root, source)
    text = source.read_text(encoding="utf-8")
    excerpt = "Ari chooses the north gate."
    start = text.index(excerpt)
    exact = {"start": start, "end": start + len(excerpt), "excerpt": excerpt}
    payload = {
        "schema": "chapter_semantic_bundle_v1",
        "chapter_number": 1,
        "source": {"path": "40_manuscript/final/ch001.md", "sha256": file_hash(source)},
        "chapter_digest": {"summary": "route chosen"},
        "scenes": [{"scene_id": "scene_1", **exact}],
        "events": [],
        "relationship_deltas": [
            {
                "source_id": "char_ari",
                "target_id": "char_bo",
                "prior_state": "hostile",
                "new_state": "conditional",
                "evidence": exact,
            }
        ],
        "character_deltas": [
            {
                "character_id": "char_ari",
                "knowledge_gained": [
                    {
                        "fact": "the north gate is open",
                        "route": "telepathy",
                        "evidence": {**exact, "excerpt": "Agent-invented evidence"},
                    }
                ],
                "evidence": exact,
            }
        ],
        "foreshadow_deltas": [
            {"thread_id": "thread_gate", "action": "payoff", "evidence": exact}
        ],
        "world_deltas": [],
        "timeline_deltas": [],
        "retrieval": {},
        "coverage": {},
    }
    result_file = write_result(root, manifest, payload)

    result = normalize_and_validate_agent_result(root, manifest, result_file=result_file)

    assert result.status == "invalid"
    assert any("expected 'guarded'" in item for item in result.errors)
    assert any("route is invalid" in item for item in result.errors)
    assert any("does not match the current source span" in item for item in result.errors)
    assert "foreshadow_payoff_outside_window:thread_gate" in result.need_human_reasons


def test_phase4_legacy_semantic_allows_evidence_bound_new_event_and_world_ids(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "final" / "ch001.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(chapter_text(), encoding="utf-8")
    manifest = semantic_manifest(root, source)
    excerpt = "Ari chooses the north gate."
    start = source.read_text(encoding="utf-8").index(excerpt)
    exact = {"start": start, "end": start + len(excerpt), "excerpt": excerpt}
    payload = {
        "schema": "chapter_semantic_bundle_v1",
        "chapter_number": 1,
        "source": {"path": "40_manuscript/final/ch001.md", "sha256": file_hash(source)},
        "chapter_digest": {"summary": "Ari chooses a route."},
        "scenes": [{"scene_id": "scene_1", **exact}],
        "events": [
            {
                "event_id": "event:ch001:north_gate_choice",
                "title": "North gate chosen",
                "evidence": exact,
            }
        ],
        "relationship_deltas": [],
        "character_deltas": [
            {
                "character_id": "char_ari",
                "knowledge_gained": [
                    {"fact": "the north gate is chosen", "route": "document", "evidence": exact}
                ],
                "evidence": exact,
            }
        ],
        "foreshadow_deltas": [],
        "world_deltas": [
            {"fact_id": "world:north_gate:selected", "value": True, "evidence": exact}
        ],
        "timeline_deltas": [
            {"event_id": "event:ch001:north_gate_choice", "order": 1, "evidence": exact}
        ],
        "retrieval": {},
        "coverage": {},
    }
    result_file = write_result(root, manifest, payload)

    result = normalize_and_validate_agent_result(root, manifest, result_file=result_file)

    assert result.ok, result.errors
    assert not any("unknown canonical entity_id" in item for item in result.errors)


def test_phase4_semantic_relationship_endpoints_resolve_to_stable_relationship_id(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "final" / "ch001.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(chapter_text(), encoding="utf-8")
    manifest = semantic_manifest(root, source)
    excerpt = "Ari chooses the north gate."
    start = source.read_text(encoding="utf-8").index(excerpt)
    exact = {"start": start, "end": start + len(excerpt), "excerpt": excerpt}
    payload = {
        "schema": "chapter_semantic_bundle_v1",
        "chapter_number": 1,
        "source": {"path": "40_manuscript/final/ch001.md", "sha256": file_hash(source)},
        "chapter_digest": {"summary": "Ari changes the relationship through a route choice."},
        "scenes": [{"scene_id": "scene_1", **exact}],
        "events": [],
        "relationship_deltas": [
            {
                "source_id": "char_ari",
                "target_id": "char_bo",
                "prior_state": "guarded",
                "new_state": "conditional",
                "evidence": exact,
            }
        ],
        "character_deltas": [],
        "foreshadow_deltas": [],
        "world_deltas": [],
        "timeline_deltas": [],
        "retrieval": {},
        "coverage": {},
    }
    result_file = write_result(root, manifest, payload)

    result = normalize_and_validate_agent_result(root, manifest, result_file=result_file)

    assert result.ok, result.errors
    relationship_delta = next(
        delta for delta in result.normalized_result["deltas"] if delta["field"] == "relationship_deltas"
    )
    assert relationship_delta["entity_id"] == "rel_ari_bo"


def test_phase4_ambiguous_legacy_editorial_evidence_enters_need_human(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "draft" / "ch001.md"
    source.write_text("# Chapter 1\n\nThe gate stays shut. The gate stays shut.\n", encoding="utf-8")
    manifest = editorial_manifest(root, source)
    legacy = {
        "schema_version": 1,
        "chapter_number": 1,
        "role_id": "writing_agent",
        "verdict": "needs_revision",
        "items": [
            {
                "code": "duplicate_line",
                "severity": "P2",
                "message": "The repeated line flattens the scene.",
                "evidence": ["The gate stays shut."],
                "recommendation": "Keep only the causally useful occurrence.",
            }
        ],
    }
    result_file = write_result(root, manifest, legacy)

    result = normalize_and_validate_agent_result(root, manifest, result_file=result_file)

    assert result.status == "need_human"
    assert any("editorial_evidence_not_unique" in item for item in result.need_human_reasons)
    assert result.next_command == manifest["failure_next_command"]


def test_phase4_prompt_role_cannot_expand_manifest_canonical_authority(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "final" / "ch001.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(chapter_text(), encoding="utf-8")
    manifest = semantic_manifest(root, source)
    excerpt = "Ari chooses the north gate."
    start = source.read_text(encoding="utf-8").index(excerpt)
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "pass",
            "evidence": [evidence("final", start, excerpt)],
            "deltas": [
                {
                    **delta("rel_change", "rel_ari_bo", "relationship_state", "guarded", "conditional"),
                    "new_state": {
                        "state": "conditional",
                        "canonical_targets": ["30_state/story_graph.json", "70_runtime/db/novel.sqlite"],
                    },
                }
            ],
            "notes": [],
        }
    )
    result_file = write_result(root, manifest, payload)

    result = normalize_and_validate_agent_result(root, manifest, result_file=result_file)

    assert result.status == "invalid"
    assert any("cannot expand manifest canonical write authority" in item for item in result.errors)
    assert result.normalized_result["cli_context"]["canonical_targets"] == manifest["canonical_targets"]


def test_phase4_invalid_manifest_fails_before_registration_and_residue_is_recognized(tmp_path):
    root = seed_protocol_project(tmp_path)
    source = root / "40_manuscript" / "draft" / "ch001.md"
    task_file = root / "50_workbench" / "quality_reviews" / "ch001.reader_payoff_task.md"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text("# Failed work order\n", encoding="utf-8")
    manifest = review_manifest(root, "reader_payoff_review", [source, chapter_card(root)])
    manifest["context_policy"]["max_files"] = 1
    manifest_file = task_file.with_name("ch001.reader_payoff_task.agent_task.json")

    with pytest.raises(AgentTaskContractError, match="actual input file count"):
        write_manifest(root, manifest, manifest_file)

    assert not manifest_file.exists()
    assert not (root / "50_workbench" / "agent_tasks" / "agent_task_index.json").exists()
    assert not (root / "50_workbench" / "agent_tasks" / "events.jsonl").exists()
    assert task_file in orphan_agent_task_artifacts(root)


def test_phase4_cli_writes_only_controlled_diagnostic_and_keeps_canonical_and_lifecycle_unchanged(tmp_path, capsys):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "cli-novel")
    root = Path(project.project_config).parent
    source = root / "40_manuscript" / "draft" / "ch001.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(chapter_text(), encoding="utf-8")
    write_json(chapter_card(root), card_payload())
    manifest = review_manifest(root, "reader_payoff_review", [source, chapter_card(root)])
    manifest_path = root / "50_workbench" / "quality_reviews" / "ch001.reader_payoff.agent_task.json"
    write_manifest(root, manifest, manifest_path)
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "pass",
            "evidence": [evidence("draft", source.read_text(encoding="utf-8").index("Ari"), "wrong excerpt")],
            "findings": [],
            "notes": [],
        }
    )
    result_file = write_result(root, manifest, payload)
    protected = canonical_snapshot(root)
    exit_code = main(
        [
            "agent-task",
            "result-validate",
            str(project.project_config),
            manifest["task_id"],
            "--file",
            str(result_file),
            "--json",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert output["status"] == "invalid"
    diagnostic = root / output["diagnostic_file"]
    assert diagnostic.is_file()
    assert diagnostic.parent == root / "50_workbench" / "agent_tasks" / "diagnostics"
    assert canonical_snapshot(root) == protected
    indexed = next(item for item in list_manifests(root) if item["task_id"] == manifest["task_id"])
    assert indexed["status"] == "invalid"
    events = (root / "50_workbench" / "agent_tasks" / "events.jsonl").read_text(encoding="utf-8")
    assert "agent-task result-validate" in events


def seed_protocol_project(tmp_path: Path) -> Path:
    root = tmp_path / "novel"
    root.mkdir(parents=True)
    source = root / "40_manuscript" / "draft" / "ch001.md"
    source.parent.mkdir(parents=True)
    source.write_text(chapter_text(), encoding="utf-8")
    write_json(chapter_card(root), card_payload())
    write_json(
        root / "10_bible" / "characters.json",
        [{"id": "char_ari", "name": "Ari"}, {"id": "char_bo", "name": "Bo"}],
    )
    write_json(
        root / "10_bible" / "relationships.json",
        [{"id": "rel_ari_bo", "source_id": "char_ari", "target_id": "char_bo", "stage": "guarded"}],
    )
    write_json(root / "10_bible" / "locations.json", [])
    write_json(root / "10_bible" / "factions.json", [])
    write_json(root / "30_state" / "story_graph.json", {"entities": [], "relationships": [], "events": []})
    write_json(
        root / "20_outline" / "foreshadowing_ledger.json",
        [{"thread_id": "thread_gate", "plant_chapter": 1, "payoff_window": [5, 10]}],
    )
    write_json(
        root / "30_state" / "foreshadowing_state.json",
        {"schema": "foreshadowing_state_v1", "threads": {"thread_gate": {"status": "open"}}},
    )
    return root


def chapter_text() -> str:
    return "# Chapter 1\n\nAri chooses the north gate. Bo keeps the key.\n"


def card_payload() -> dict:
    return {
        "chapter_number": 1,
        "chapter_duty": "Force a costly route decision.",
        "reader_gain": "Reveal which route remains open.",
        "cost": "Ari loses the safer option.",
        "promise_refs": ["thread_gate"],
        "relationship_move": "guarded to conditional trust",
    }


def chapter_card(root: Path) -> Path:
    return root / "20_outline" / "chapter_cards" / "ch001.json"


def review_manifest(root: Path, task_type: str, inputs: list[Path]) -> dict:
    return build_manifest(
        root,
        task_type=task_type,
        chapter_number=1,
        input_files=inputs,
        allowed_output_paths=[root / "50_workbench" / "quality_reviews" / "ch001.reader_payoff.result.json"],
        output_schema="reader_payoff_review_v1",
        validate_command="longform-engine quality payoff-validate project.yaml --chapter 1",
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
        task_id="reader_payoff_review:ch001:phase4",
    )


def semantic_manifest(root: Path, source: Path) -> dict:
    return build_manifest(
        root,
        task_type="chapter_semantic",
        chapter_number=1,
        input_files=[source, chapter_card(root)],
        allowed_output_paths=[root / "50_workbench" / "semantic_tasks" / "ch001.semantic.result.json"],
        output_schema="chapter_semantic_bundle_v1",
        validate_command="longform-engine chapter semantic-validate project.yaml --chapter 1 --file 50_workbench/semantic_tasks/ch001.semantic.result.json",
        apply_command="longform-engine chapter semantic-apply project.yaml --chapter 1 --file 50_workbench/semantic_tasks/ch001.semantic.result.json",
        failure_next_command="longform-engine chapter semantic-task project.yaml --chapter 1",
        canonical_targets=[
            root / "30_state" / "semantic_ledger" / "ch001.json",
            root / "30_state" / "story_graph.json",
        ],
        task_id="chapter_semantic:ch001:phase4",
    )


def editorial_manifest(root: Path, source: Path) -> dict:
    return build_manifest(
        root,
        task_type="editorial_review",
        role_id="writing_agent",
        chapter_number=1,
        input_files=[source],
        allowed_output_paths=[root / "50_workbench" / "editorial_reviews" / "results" / "ch001.writing_agent.json"],
        output_schema="editorial_role_review_v1",
        validate_command="longform-engine editorial submit-review project.yaml --chapter 1 --role writing_agent --file 50_workbench/editorial_reviews/results/ch001.writing_agent.json",
        apply_command="longform-engine editorial aggregate project.yaml --chapter 1",
        failure_next_command="longform-engine editorial need-human project.yaml --chapter 1 --reason invalid-review",
        task_id="editorial_review:writing_agent:ch001:phase4",
    )


def to_v1_manifest(manifest: dict) -> dict:
    result = copy.deepcopy(manifest)
    result["schema_version"] = 1
    for field in (
        "scope",
        "canonical_targets",
        "requires_human_apply",
        "context_policy",
        "role_id",
        "role_version",
        "role_prompt_hash",
        "independence_mode",
        "project_overlay_hash",
    ):
        result.pop(field, None)
    return result


def evidence(source_ref: str, start: int, excerpt: str) -> dict:
    return {
        "evidence_id": "ev_1",
        "source_ref": source_ref,
        "start": start,
        "end": start + len(excerpt),
        "excerpt": excerpt,
    }


def delta(delta_id: str, entity_id: str, field: str, old_state, new_state) -> dict:
    return {
        "delta_id": delta_id,
        "entity_id": entity_id,
        "field": field,
        "action": "update",
        "old_state": old_state,
        "new_state": new_state,
        "evidence_refs": ["ev_1"],
        "coverage": "changed",
    }


def write_result(root: Path, manifest: dict, payload: dict) -> Path:
    path = root / manifest["allowed_output_paths"][0]
    write_json(path, payload)
    return path


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def canonical_snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for prefix in ("00_governance", "10_bible", "20_outline", "30_state", "40_manuscript/final", "60_rag", "70_runtime/db"):
        base = root / prefix
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                result[path.relative_to(root).as_posix()] = file_hash(path)
    return result
