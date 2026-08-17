import json
from pathlib import Path

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import EVIDENCE_REVIEW_SCHEMA
from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.gates import gate_check, semantic_review_apply, semantic_review_validate
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.production import production_next
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


def seed_high_risk_chapter(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    config.data["length"]["chapter"]["hard_min"] = 20
    continue_write(config, chapter_number=1)
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["requires_semantic_review"] = True
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(
        "# Chapter 1\n\n"
        + ("Ari checks the archive seal and records one bounded clue. " * 20)
        + "But the final seal names a second archive. Who opens it at midnight?",
        encoding="utf-8",
    )
    submit_agent_draft(config, chapter_number=1, file_path=agent_draft, agent="codex")
    chapter = root / "40_manuscript" / "draft" / "ch001.md"
    return config, root, chapter


def test_high_risk_gate_creates_strict_semantic_review_task(tmp_path):
    config, root, _ = seed_high_risk_chapter(tmp_path)

    result = gate_check(config, chapter_number=1, semantic=True)
    manifest = load_manifest(root, "semantic_review:ch001:v4")
    strict = validate_manifest_strict(root, manifest)

    assert result.passed
    assert not any(item["code"] == "semantic_review_required" for item in result.failures)
    assert json.loads(Path(result.gate_result).read_text(encoding="utf-8"))["workflow_stage"] == "reviews_pending"
    assert strict.ok, strict.errors
    assert manifest["io"]["output"]["protocol"] == EVIDENCE_REVIEW_SCHEMA
    assert len(manifest["io"]["inputs"]) <= 7
    assert manifest["policy"]["context"]["budget_profile"] == "standard"
    assert manifest["policy"]["context"]["capacity_units"] == 48_000


def test_semantic_review_validates_spans_and_applies_only_gate_artifacts(tmp_path):
    config, root, chapter = seed_high_risk_chapter(tmp_path)
    gate_check(config, chapter_number=1, semantic=True)
    output = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_result.json"
    output.write_text(
        json.dumps(
            {
                "schema": EVIDENCE_REVIEW_SCHEMA,
                "verdict": "pass",
                "coverage": {"canonical_fact": "checked", "motivation": "checked", "space_time_ability": "checked"},
                "findings": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    protected = snapshot_protected(root)

    manifest = load_manifest(root, "semantic_review:ch001:v4")
    control = validate_production_agent_result(root, manifest, result_file=output)
    validation = semantic_review_validate(config, chapter_number=1, file_path=output)
    next_action = production_next(config)
    assert next_action["task_type"] == "semantic_review"
    assert next_action["status"] == "agent_task_validated"
    assert next_action["next_command"].startswith("longform-engine gate semantic-apply")
    applied = semantic_review_apply(config, chapter_number=1, file_path=output)
    gate = json.loads(Path(applied.gate_result).read_text(encoding="utf-8"))

    assert control.ok, control.normalization.errors
    assert validation.ok, validation.errors
    assert gate["agent_semantic_review"]["status"] == "applied"
    assert not any(item["code"] == "semantic_review_required" for item in gate["failures"])
    assert snapshot_protected(root) == protected


def test_semantic_review_rejects_fabricated_span_without_pollution(tmp_path):
    config, root, chapter = seed_high_risk_chapter(tmp_path)
    gate_check(config, chapter_number=1, semantic=True)
    output = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_result.json"
    output.write_text(
        json.dumps(
            {
                "schema": EVIDENCE_REVIEW_SCHEMA,
                "verdict": "repair",
                "coverage": {"canonical_fact": "checked", "motivation": "checked", "space_time_ability": "checked"},
                "findings": [
                    {
                        "code": "CANONICAL_CONFLICT",
                        "severity": "P1",
                        "certainty": "confirmed",
                        "diagnosis": "Unsupported claim.",
                        "evidence_ids": ["ch001.md@0:99999"],
                        "reader_impact": "The transition contradicts the established fact.",
                        "repair_target": "Repair the causal bridge.",
                        "preserve": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    protected = snapshot_protected(root, include_db=True)

    manifest = load_manifest(root, "semantic_review:ch001:v4")
    control = validate_production_agent_result(root, manifest, result_file=output)
    validation = semantic_review_validate(config, chapter_number=1, file_path=output)

    assert not control.ok
    assert any("outside current source bounds" in error for error in control.normalization.errors)
    assert not validation.ok
    assert any("control-plane status" in error for error in validation.errors)
    assert snapshot_protected(root, include_db=True) == protected


def snapshot_protected(root: Path, *, include_db: bool = False) -> dict[str, bytes]:
    paths = [
        root / "40_manuscript" / "final",
        root / "60_rag",
        root / "30_state" / "story_graph.json",
        root / "30_state" / "tcs",
    ]
    if include_db:
        paths.append(root / "70_runtime" / "db")
    snapshot: dict[str, bytes] = {}
    for path in paths:
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
        elif path.is_dir():
            for file in sorted(item for item in path.rglob("*") if item.is_file()):
                snapshot[file.relative_to(root).as_posix()] = file.read_bytes()
    return snapshot
