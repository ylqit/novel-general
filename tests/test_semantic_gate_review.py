import hashlib
import json
from pathlib import Path

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
    config.data["length"]["chapter_word_count"]["hard_min"] = 20
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
    manifest = load_manifest(root, "semantic_review:ch001:v1")
    strict = validate_manifest_strict(root, manifest)

    assert not result.passed
    assert any(item["code"] == "semantic_review_required" for item in result.failures)
    assert strict.ok, strict.errors
    assert manifest["output_schema"] == "semantic_review_result_v1"
    assert len(manifest["input_files"]) <= 7
    assert manifest["context_policy"]["max_chars"] == 18000


def test_semantic_review_validates_spans_and_applies_only_gate_artifacts(tmp_path):
    config, root, chapter = seed_high_risk_chapter(tmp_path)
    gate_check(config, chapter_number=1, semantic=True)
    output = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_result.json"
    text = chapter.read_text(encoding="utf-8")
    output.write_text(
        json.dumps(
            {
                "schema": "semantic_review_result_v1",
                "chapter_number": 1,
                "source_path": "40_manuscript/draft/ch001.md",
                "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "verdict": "pass",
                "findings": [],
                "notes": "Three-way semantic review found no unsupported transition.",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    protected = snapshot_protected(root)

    validation = semantic_review_validate(config, chapter_number=1, file_path=output)
    next_action = production_next(config)
    assert next_action["task_type"] == "semantic_review"
    assert next_action["status"] == "agent_task_validated"
    assert next_action["next_command"].startswith("longform-engine gate semantic-apply")
    applied = semantic_review_apply(config, chapter_number=1, file_path=output)
    gate = json.loads(Path(applied.gate_result).read_text(encoding="utf-8"))

    assert validation.ok, validation.errors
    assert gate["agent_semantic_review"]["status"] == "applied"
    assert not any(item["code"] == "semantic_review_required" for item in gate["failures"])
    assert snapshot_protected(root) == protected


def test_semantic_review_rejects_fabricated_span_without_pollution(tmp_path):
    config, root, chapter = seed_high_risk_chapter(tmp_path)
    gate_check(config, chapter_number=1, semantic=True)
    output = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_result.json"
    text = chapter.read_text(encoding="utf-8")
    output.write_text(
        json.dumps(
            {
                "schema": "semantic_review_result_v1",
                "chapter_number": 1,
                "source_path": "40_manuscript/draft/ch001.md",
                "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "verdict": "fail",
                "findings": [
                    {
                        "code": "fabricated",
                        "category": "causality",
                        "severity": "P1",
                        "message": "Unsupported claim.",
                        "evidence_span": {"start": 0, "end": 5, "text": "wrong"},
                        "canonical_refs": ["10_bible/characters.json"],
                        "entity_ids": ["lead_ari"],
                        "recommendation": "Repair the causal bridge.",
                    }
                ],
                "notes": "",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    protected = snapshot_protected(root, include_db=True)

    validation = semantic_review_validate(config, chapter_number=1, file_path=output)

    assert not validation.ok
    assert any("does not match the chapter slice" in error for error in validation.errors)
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
