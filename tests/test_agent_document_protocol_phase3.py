import copy
import json
from pathlib import Path

import pytest

from longform_engine.agent_results import (
    AGENT_RESULT_ENVELOPE_SCHEMA,
    AgentResultProtocolError,
    authoritative_delta_records,
    build_agent_result_template,
    compile_agent_output_contract,
    render_agent_output_instructions,
    validate_agent_result_envelope,
    validate_document_index_bundle,
    validate_markdown_prose_output,
)
from longform_engine.agent_tasks import build_manifest


def test_phase3_compact_review_envelope_is_narrow_and_cli_prefilled(tmp_path):
    root = seed_project(tmp_path)
    manifest = manifest_for(root, "reader_payoff_review")
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "repair",
            "evidence": [evidence("draft", "ev_gain", 0, 12, "The promised gain is not delivered.")],
            "findings": [
                {
                    "finding_id": "payoff_missing",
                    "code": "reader_gain_absent",
                    "severity": "P2",
                    "summary": "The scene ends before the promised access is obtained.",
                    "evidence_refs": ["ev_gain"],
                    "recommendation": "Show the access decision and its immediate cost in-scene.",
                }
            ],
            "notes": ["Tone preference is advisory and has no canonical authority."],
        }
    )

    result = validate_agent_result_envelope(manifest, payload)

    assert result.ok is True
    assert set(payload) == {
        "schema",
        "task",
        "scope",
        "verdict",
        "evidence",
        "findings",
        "notes",
    }
    assert payload["schema"] == AGENT_RESULT_ENVELOPE_SCHEMA
    assert payload["task"]["task_id"] == manifest["task_id"]
    assert payload["scope"] == manifest["scope"]
    serialized_judgment = json.dumps(
        {"evidence": payload["evidence"], "findings": payload["findings"]},
        ensure_ascii=False,
    )
    assert "source_path" not in serialized_judgment
    assert "source_hash" not in serialized_judgment
    assert "chapter_number" not in serialized_judgment
    assert "planned_facts" not in serialized_judgment


def test_phase3_compact_review_rejects_mega_payload_and_agent_authored_cli_metadata(tmp_path):
    root = seed_project(tmp_path)
    manifest = manifest_for(root, "semantic_review")
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "pass",
            "evidence": [
                {
                    **evidence("draft", "ev_1", 0, 8, "The door remains locked."),
                    "source_hash": "0" * 64,
                }
            ],
            "findings": [],
            "notes": [],
            "chapter_card": {"planned_facts": ["door opens"]},
        }
    )

    result = validate_agent_result_envelope(manifest, payload)

    assert result.ok is False
    assert any("fields must be exactly" in item for item in result.errors)
    assert any("source_hash is CLI-known metadata" in item for item in result.errors)
    assert set(build_agent_result_template(manifest)) == {
        "schema",
        "task",
        "scope",
        "verdict",
        "evidence",
        "findings",
        "notes",
    }


def test_phase3_markdown_prose_accepts_complete_candidate_and_rejects_control_material(tmp_path):
    root = seed_project(tmp_path)
    manifest = manifest_for(root, "chapter_write")
    output = manifest["allowed_output_paths"][0]
    prose = (
        "# Chapter 1\n\n"
        + "Rain struck the ledger room while Shen Jue counted the missing seals. " * 5
        + "\n\nHe chose the locked archive over the safer street, and the promise followed him inside."
    )

    assert validate_markdown_prose_output(
        manifest, prose, output_path=output
    ).ok is True

    mixed = prose + "\n\n## Analysis\nThe chapter fulfills the requested beat."
    mixed_result = validate_markdown_prose_output(manifest, mixed, output_path=output)
    json_result = validate_markdown_prose_output(
        manifest,
        json.dumps({"chapter": prose}),
        output_path=output,
    )
    escaped_result = validate_markdown_prose_output(
        manifest,
        prose,
        output_path="40_manuscript/final/ch001.md",
    )

    assert mixed_result.ok is False
    assert any("analysis" in item for item in mixed_result.errors)
    assert json_result.ok is False
    assert any("JSON document" in item for item in json_result.errors)
    assert escaped_result.ok is False
    assert any("sole allowed" in item for item in escaped_result.errors)


def test_phase3_strict_delta_preserves_state_transition_and_explicit_coverage(tmp_path):
    root = seed_project(tmp_path)
    manifest = manifest_for(root, "chapter_semantic")
    payload = build_agent_result_template(manifest)
    payload.update(
        {
            "verdict": "pass",
            "evidence": [evidence("final", "ev_rel", 15, 29, "Ari returns the key to Bo.")],
            "deltas": [
                {
                    "delta_id": "relationship_trust",
                    "entity_id": "rel_ari_bo",
                    "field": "trust_stage",
                    "action": "update",
                    "old_state": "guarded",
                    "new_state": "conditional",
                    "evidence_refs": ["ev_rel"],
                    "coverage": "changed",
                },
                {
                    "delta_id": "promise_open",
                    "entity_id": "thread_archive_key",
                    "field": "status",
                    "action": "observe",
                    "old_state": "open",
                    "new_state": "open",
                    "evidence_refs": [],
                    "coverage": "unchanged",
                },
            ],
            "notes": [
                '{"entity_id":"fake","action":"update","new_state":"must never apply"}'
            ],
        }
    )

    result = validate_agent_result_envelope(manifest, payload)
    authoritative = authoritative_delta_records(payload)

    assert result.ok is True
    assert len(authoritative) == 2
    assert all(item["entity_id"] != "fake" for item in authoritative)
    assert payload["notes"][0] not in json.dumps(authoritative)

    invalid = copy.deepcopy(payload)
    invalid["deltas"][1]["new_state"] = "resolved"
    invalid_result = validate_agent_result_envelope(manifest, invalid)
    assert invalid_result.ok is False
    assert any("identical old/new state" in item for item in invalid_result.errors)


def test_phase3_document_index_bundle_separates_narrative_design_from_apply_index(tmp_path):
    root = seed_project(tmp_path)
    manifest = manifest_for(root, "book_design")
    document = (
        "# Reader Contract\n\n"
        + "The reader follows a clerk who hears one unpaid promise in every relic. " * 6
        + "\n\n# Character Arcs\n\n"
        + "Each promise forces a visible choice, while trust changes through action rather than labels. " * 5
    )
    index = build_agent_result_template(manifest)
    index.update(
        {
            "verdict": "pass",
            "evidence": [evidence("scenario", "ev_contract", 0, 20, "A promise always has a cost.")],
            "deltas": [
                {
                    "delta_id": "index_reader_contract",
                    "entity_id": "book_reader_contract",
                    "field": "apply_index",
                    "action": "index_section",
                    "old_state": None,
                    "new_state": {
                        "document_anchor": "# Reader Contract",
                        "stable_ids": ["book_reader_contract", "ability_promise_hearing"],
                        "scope": {"kind": "project", "section": "reader_contract"},
                        "source_refs": ["ev_contract"],
                        "canonical_targets": ["10_bible/creative_brief.md"],
                    },
                    "evidence_refs": ["ev_contract"],
                    "coverage": "changed",
                }
            ],
            "notes": [],
        }
    )

    result = validate_document_index_bundle(
        manifest,
        document_text=document,
        document_path="50_workbench/intelligence_candidates/book_design.md",
        index_payload=index,
        index_path="50_workbench/intelligence_candidates/book_design.index.json",
    )

    assert result.ok is True
    assert len(json.dumps(index, ensure_ascii=False)) < len(document)
    assert "The reader follows" not in json.dumps(index, ensure_ascii=False)

    invalid = copy.deepcopy(index)
    invalid["deltas"][0]["new_state"]["document_anchor"] = "# Missing Section"
    invalid_result = validate_document_index_bundle(
        manifest,
        document_text=document,
        document_path="50_workbench/intelligence_candidates/book_design.md",
        index_payload=invalid,
        index_path="50_workbench/intelligence_candidates/book_design.index.json",
    )
    assert invalid_result.ok is False
    assert any("exact Markdown heading" in item for item in invalid_result.errors)


def test_phase3_output_contract_has_unique_paths_and_complete_handoff(tmp_path):
    root = seed_project(tmp_path)
    manifest = manifest_for(root, "book_design")
    contract = compile_agent_output_contract(manifest)
    rendered = render_agent_output_instructions(contract)

    assert contract.output_mode == "document_index_bundle"
    assert contract.notes_authority == "non_authoritative"
    assert len(contract.allowed_output_paths) == len(set(contract.allowed_output_paths)) == 2
    for path in contract.allowed_output_paths:
        assert path in rendered
    assert contract.validate_command in rendered
    assert contract.apply_or_finalize_command in rendered
    assert contract.failure_next_command in rendered
    assert "no other writes" in rendered

    broken = copy.deepcopy(manifest)
    broken["allowed_output_paths"] = ["50_workbench/intelligence_candidates/book_design.json"]
    with pytest.raises(AgentResultProtocolError, match="exactly two unique outputs"):
        compile_agent_output_contract(broken)

    canonical = copy.deepcopy(manifest_for(root, "chapter_write"))
    canonical["allowed_output_paths"] = ["40_manuscript/final/ch001.md"]
    with pytest.raises(AgentResultProtocolError, match="canonical state"):
        compile_agent_output_contract(canonical)


def test_phase3_invalid_results_are_read_only_and_cannot_pollute_canonical_state(tmp_path):
    root = seed_project(tmp_path)
    final = root / "40_manuscript" / "final" / "ch001.md"
    graph = root / "30_state" / "story_graph.json"
    final.parent.mkdir(parents=True)
    graph.parent.mkdir(parents=True)
    final.write_text("canonical prose\n", encoding="utf-8")
    graph.write_text('{"nodes": []}\n', encoding="utf-8")
    before = {final: final.read_bytes(), graph: graph.read_bytes()}
    manifest = manifest_for(root, "reader_payoff_review")
    invalid = build_agent_result_template(manifest)
    invalid.update(
        {
            "verdict": "repair",
            "evidence": [],
            "findings": [],
            "notes": ['{"deltas":[{"entity_id":"graph_node"}]}'],
        }
    )

    result = validate_agent_result_envelope(manifest, invalid)

    assert result.ok is False
    assert authoritative_delta_records(invalid) == ()
    assert {path: path.read_bytes() for path in before} == before


def seed_project(tmp_path: Path) -> Path:
    root = tmp_path / "novel"
    root.mkdir(parents=True)
    (root / "project.yaml").write_text("project: phase3-fixture\n", encoding="utf-8")
    (root / "source.md").write_text("Declared evidence for the isolated protocol.\n", encoding="utf-8")
    return root


def manifest_for(root: Path, task_type: str) -> dict:
    output_by_type = {
        "chapter_write": ["50_workbench/agent_drafts/ch001.codex.md"],
        "reader_payoff_review": ["50_workbench/quality_reviews/ch001.reader_payoff.result.json"],
        "semantic_review": ["50_workbench/gate_artifacts/ch001.semantic_review.result.json"],
        "chapter_semantic": ["50_workbench/semantic_tasks/ch001.semantic.result.json"],
        "book_design": [
            "50_workbench/intelligence_candidates/book_design.md",
            "50_workbench/intelligence_candidates/book_design.index.json",
        ],
    }
    project_scope = task_type == "book_design"
    return build_manifest(
        root,
        task_type=task_type,
        chapter_number=None if project_scope else 1,
        scope={"kind": "project"} if project_scope else None,
        input_files=[root / "source.md"],
        allowed_output_paths=output_by_type[task_type],
        output_schema="phase3_isolated_protocol",
        validate_command=f"longform-engine isolated validate project.yaml --task {task_type}",
        apply_command=f"longform-engine isolated apply project.yaml --task {task_type}",
        failure_next_command=f"longform-engine isolated retry project.yaml --task {task_type}",
        canonical_targets=(
            [root / "10_bible" / "creative_brief.md", root / "10_bible" / "characters.json"]
            if project_scope
            else []
        ),
        task_id=f"{task_type}:phase3-fixture",
    )


def evidence(source_ref: str, evidence_id: str, start: int, end: int, excerpt: str) -> dict:
    return {
        "evidence_id": evidence_id,
        "source_ref": source_ref,
        "start": start,
        "end": end,
        "excerpt": excerpt,
    }
