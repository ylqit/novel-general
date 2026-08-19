import json
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_REQUIRED_HEADINGS,
    DESIGN_TASK_TYPES,
    output_protocol_for_task,
)
from longform_engine.agent_tasks import TASK_CONTRACTS, load_manifest
from longform_engine.config import load_project_config
from longform_engine.intelligence import (
    apply_compiled_design,
    apply_intelligence_candidate,
    approve_design_document,
    create_design_compile_task,
    create_intelligence_task,
    validate_design_compile_delta,
    validate_intelligence_candidate,
)
from longform_engine.storage import init_project


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def project_snapshot(root: Path) -> dict[str, bytes]:
    paths = (
        "10_bible/creative_brief.json",
        "10_bible/characters.json",
        "10_bible/relationships.json",
        "10_bible/world.md",
        "20_outline/chapter_plan.json",
        "30_state/novel_state.json",
        "30_state/story_graph.json",
    )
    return {item: (root / item).read_bytes() if (root / item).exists() else b"" for item in paths}


def book_design_payload() -> dict:
    return {
        "schema": "book_design_candidate_v2",
        "creative_brief": {
            "target_audience": "Chinese longform serial readers",
            "writing_style": "Concrete scene-driven prose",
            "automation_level": "agent_skill with human approval",
            "target_scale": "2000000 content characters",
            "story_profile": load_project_config(template="qidian-longform").data["story_profile"],
            "design_decisions": {
                "core_hook": "The archive changes overnight",
                "world_rule": "Corrections erase a witnessed memory",
                "protagonist_desire": "Preserve the border archive",
                "long_conflict": "The court depends on controlled forgetting",
                "volume_escalation": "Each volume widens the cost",
                "ending_boundary": "Resolve control of collective memory",
            },
            "reader_contract": {"core_promise": "Evidence-led mystery"},
            "core_taboo": ["No premature final reveal"],
            "status": "candidate",
        },
        "world_markdown": "Rules have visible costs",
        "power_system_markdown": "Every advance spends memory",
        "characters": [
            {"id": "lead_ari", "name": "Ari", "goal": "Preserve the archive", "flaw": "Distrusts allies", "arc_stages": ["isolated", "tested", "trusting"]},
            {"id": "ally_mira", "name": "Mira", "goal": "Expose the treaty", "flaw": "Acts too quickly", "arc_stages": ["outsider", "ally", "partner"]},
        ],
        "relationships": [
            {"id": "rel_ari_mira", "source_id": "lead_ari", "target_id": "ally_mira", "type": "alliance", "stage": "uneasy"}
        ],
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
                "perception_bias": "Notices altered records before faces",
                "decision_bias": "Verifies one trace before trusting testimony",
                "speech_register": "Short procedural questions conceal fear",
                "conversation_tactics": ["narrows claims"],
                "emotional_leaks": ["aligns document edges when afraid"],
                "physical_presence": "Still shoulders and ink-stained fingertips",
                "social_masks": ["neutral archive clerk"],
                "private_wants": "Preserve people as well as records",
                "contradictions": "Demands evidence but acts on guilt",
                "voice_examples": [],
                "contrast_with": ["ally_mira"],
            },
            {
                "character_id": "ally_mira",
                "perception_bias": "Notices exits and status pressure",
                "decision_bias": "Tests a weak point before consensus closes",
                "speech_register": "Concrete challenges and compressed humor",
                "conversation_tactics": ["forces a choice"],
                "emotional_leaks": ["paces toward exits when boxed in"],
                "physical_presence": "Restless stance and quick distance changes",
                "social_masks": ["reckless outsider"],
                "private_wants": "Be trusted without surrendering initiative",
                "contradictions": "Mocks procedure but keeps exact witness times",
                "voice_examples": [],
                "contrast_with": ["lead_ari"],
            },
        ],
    }


def scalar_lines(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [item for child in value for item in scalar_lines(child)]
    if isinstance(value, dict):
        return [item for child in value.values() for item in scalar_lines(child)]
    return []


def write_design_document(path: Path, task_type: str, payload: dict) -> str:
    facts = scalar_lines({key: value for key, value in payload.items() if key != "schema"})
    sections: list[str] = []
    for index, heading in enumerate(DESIGN_REQUIRED_HEADINGS[task_type]):
        body = ["本节决定已经由用户审阅。"]
        if index == 0:
            body.extend(f"- {fact}" for fact in facts)
        sections.extend((f"## {heading}", "", *body, ""))
    text = f"# {task_type} 设计文档\n\n" + "\n".join(sections)
    path.write_text(text, encoding="utf-8")
    return text


def write_delta(path: Path, *, changes: dict, source_name: str, source_text: str, delta_type: str) -> None:
    evidence_id = f"{source_name}@0:{len(source_text)}"
    path.write_text(
        json.dumps(
            {
                "schema": CANONICAL_DELTA_SCHEMA,
                "delta_type": delta_type,
                "coverage": {key: "changed" for key in changes},
                "changes": changes,
                "evidence": {f"/changes/{key.replace('~', '~0').replace('/', '~1')}": [evidence_id] for key in changes},
                "uncertainties": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def submit_result(root: Path, task_id: str, output: Path):
    manifest = load_manifest(root, task_id)
    result = validate_production_agent_result(root, manifest, result_file=output)
    assert result.ok, result.normalization.errors
    return result


def prepare_book_design(config, root: Path, payload: dict):
    task = create_intelligence_task(config, task_type="book_design")
    document = root / task.candidate_file
    text = write_design_document(document, "book_design", payload)
    submit_result(root, task.task_id, document)
    validation = validate_intelligence_candidate(config, task_type="book_design", file_path=document)
    assert validation.ok, validation.errors
    approve_design_document(config, task_type="book_design", document_path=document, approved_by="human")
    compile_task = create_design_compile_task(config, task_type="book_design", document_path=document)
    delta = root / compile_task.candidate_file
    write_delta(
        delta,
        changes={key: value for key, value in payload.items() if key != "schema"},
        source_name=document.name,
        source_text=text,
        delta_type="design_document",
    )
    submit_result(root, compile_task.task_id, delta)
    return document, delta


def test_manifest_v4_and_four_protocol_surface_rejects_history(tmp_path):
    seed_project(tmp_path)
    root = tmp_path / "novel"
    assert len(TASK_CONTRACTS) == 25
    assert DESIGN_TASK_TYPES
    assert {output_protocol_for_task(task_type) for task_type in TASK_CONTRACTS} == {
        "prose_markdown_v1", "design_document_v1", "evidence_review_v2", "canonical_delta_v1"
    }
    retired = root / "50_workbench" / "agent_tasks" / "retired.json"
    retired.write_text(json.dumps({"schema_version": 3}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version must be 4"):
        load_manifest(root, retired)


def test_design_markdown_rejects_front_matter_without_pollution(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    task = create_intelligence_task(config, task_type="book_design")
    document = root / task.candidate_file
    before = project_snapshot(root)
    document.write_text("---\nschema: old\n---\n\n# Old candidate", encoding="utf-8")
    invalid = validate_intelligence_candidate(config, task_type="book_design", file_path=document)
    assert not invalid.ok
    assert any("pure Markdown" in error for error in invalid.errors)
    assert project_snapshot(root) == before


def test_book_design_document_compile_and_atomic_apply(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    document, delta = prepare_book_design(config, root, book_design_payload())
    validation = validate_design_compile_delta(config, task_type="book_design", document_path=document, delta_path=delta)
    assert validation.ok, validation.errors
    applied = apply_compiled_design(
        config, task_type="book_design", document_path=document, delta_path=delta, approved_by="human"
    )
    assert applied.status == "applied"
    assert "Rules have visible costs" in (root / "10_bible" / "world.md").read_text(encoding="utf-8")
    assert (root / "10_bible" / "design_documents" / "book_design.project.md").is_file()
    assert (root / "30_state" / "design_deltas" / "book_design.project.json").is_file()


def test_design_delta_fact_absent_from_markdown_is_rejected_without_pollution(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    document, delta = prepare_book_design(config, root, book_design_payload())
    payload = json.loads(delta.read_text(encoding="utf-8"))
    payload["changes"]["world_markdown"] = "A fact never approved by the human"
    delta.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    submit_result(root, "design_semantic_compile:book_design:project:v4", delta)
    before = project_snapshot(root)
    invalid = validate_design_compile_delta(config, task_type="book_design", document_path=document, delta_path=delta)
    assert not invalid.ok
    assert any("absent from its Markdown evidence" in error for error in invalid.errors)
    assert project_snapshot(root) == before


def test_research_delta_uses_one_output_and_explicit_apply(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    source = root / "50_workbench" / "research_inbox" / "source.md"
    source.write_text("A reviewed source fact.", encoding="utf-8")
    task = create_intelligence_task(config, task_type="research_synthesis", input_files=[source])
    delta = root / task.candidate_file
    changes = {
        "synthesis_id": "s1",
        "summary": "A bounded synthesis",
        "claims": [{"claim_id": "c1", "statement": "Fact"}],
    }
    delta.write_text(
        json.dumps(
            {
                "schema": CANONICAL_DELTA_SCHEMA,
                "delta_type": "research_canon",
                "coverage": {"claims": "changed"},
                "changes": changes,
                "evidence": {
                    "/changes/claims/0": [
                        "50_workbench/research_inbox/source.md@0:23"
                    ]
                },
                "uncertainties": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    submit_result(root, task.task_id, delta)
    validation = validate_intelligence_candidate(config, task_type="research_synthesis", file_path=delta)
    assert validation.ok, validation.errors
    applied = apply_intelligence_candidate(config, task_type="research_synthesis", file_path=delta)
    assert applied.status == "applied"
    assert "research_canon_claim_v1" in (root / "10_bible" / "research_canon.jsonl").read_text(encoding="utf-8")
