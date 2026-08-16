from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import zipfile

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_normalization import SourceRegistry
from longform_engine.agent_tasks import load_manifest
from longform_engine.artifacts import artifact_status, restore_artifacts, verify_artifacts
from longform_engine.config import load_project_config
from longform_engine.intelligence import apply_intelligence_candidate, create_intelligence_task
from longform_engine.orchestration import continue_write, finalize_chapter, open_book, submit_agent_draft
from longform_engine.production import agent_task_brief, production_next
from longform_engine.quality import reader_payoff_task, reader_payoff_validate
from longform_engine.quality.review import payoff_output_template
from longform_engine.semantic import chapter_close, semantic_apply
from longform_engine.storage import init_project
from tests.project_fixtures import (
    build_outline_extension_candidate,
    mark_project_ready,
    prepare_unified_semantic_bundle,
)


AUTHORIZATION = {
    "schema": "agent_data_pipeline_authorization_v1",
    "authorized": True,
    "engine_version": "0.4.0",
    "protocol_surface_sha256": "f" * 64,
    "phase6_evidence_sha256": "e" * 64,
}


def test_phase8_engine_resource_provenance_is_hash_bound_and_cannot_escape(tmp_path):
    registry = SourceRegistry(tmp_path)
    assert registry.add_resource(
        "config/quality_profiles/markets/qidian_male.yaml",
        declared_by="phase8-test",
        source_ref="qidian_contract",
    ) is None
    record, error = registry.resolve("qidian_contract")
    assert error == ""
    assert record is not None
    assert record.authority == "engine_resource"
    assert len(record.sha256) == 64
    assert registry.add_resource(
        "../pyproject.toml",
        declared_by="phase8-test",
    ) == "declared Engine resource does not exist or escapes the resource root: ../pyproject.toml"


def test_phase8_current_protocol_chapter_one_full_closure(tmp_path, monkeypatch):
    config, root = seed_project(tmp_path, "chapter-one")
    authorize(monkeypatch)
    config.data["quality"]["reader_payoff"]["review_mode"] = "always"

    writing = run_chapter_write(config, root, 1)
    assert writing["gate_passed"] is True
    assert production_next(config)["status"] == "ready_for_reader_payoff_task"

    payoff = reader_payoff_task(config, chapter_number=1)
    payoff_manifest = load_manifest(root, "reader_payoff_review:ch001:v1")
    payoff_brief = agent_task_brief(config, payoff_manifest["task_id"], host="codex")
    assert payoff_brief["role_id"] == "reader_payoff_reviewer"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    card = read_json(root / "20_outline" / "chapter_cards" / "ch001.json")
    output = Path(payoff.output_file)
    write_json(output, payoff_output_template(root, 1, draft, draft.read_text(encoding="utf-8"), card))
    protocol_payoff = validate_production_agent_result(root, payoff_manifest, result_file=output)
    assert protocol_payoff.ok is True
    assert reader_payoff_validate(config, chapter_number=1, file_path=output).passed is True
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (root / "30_state" / "semantic_ledger" / "ch001.json").exists()

    finalized = finalize_chapter(config, chapter_number=1, approved_by="phase8-owner")
    assert Path(finalized.final_file).is_file()
    assert production_next(config)["status"] == "ready_for_chapter_semantic_task"
    semantic = run_chapter_semantic(config, root, 1)
    closed = chapter_close(config, chapter_number=1, approved_by="phase8-owner")

    assert semantic["ledger"].is_file()
    assert Path(closed.closure_file).is_file()
    assert production_next(config)["chapter_number"] == 2
    assert read_json(Path(closed.closure_file))["final_sha256"] == sha256(
        Path(finalized.final_file).read_bytes()
    ).hexdigest()


def test_phase8_five_chapter_gate_then_twenty_chapter_artifact_acceptance(tmp_path, monkeypatch):
    config, root = seed_project(tmp_path, "twenty-chapter-protocol-replay")
    authorize(monkeypatch)
    milestones: dict[int, dict[str, object]] = {}

    for chapter_number in range(1, 21):
        before = production_next(config)
        if before["task_type"] == "outline_extension":
            window = before["planning_window"]
            extension = create_intelligence_task(
                config,
                task_type="outline_extension",
                from_chapter=window["from_chapter"],
                to_chapter=window["to_chapter"],
            )
            candidate = root / extension.candidate_file
            candidate.write_text(
                json.dumps(
                    build_outline_extension_candidate(
                        config,
                        window["from_chapter"],
                        window["to_chapter"],
                    ),
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            apply_intelligence_candidate(
                config,
                task_type="outline_extension",
                file_path=candidate,
                approved_by="human",
            )
            before = production_next(config)
        assert before["status"] == "ready_for_continue_write"
        assert before["chapter_number"] == chapter_number
        writing = run_chapter_write(config, root, chapter_number)
        assert writing["gate_passed"] is True
        assert writing["p0"] == 0
        assert writing["p1"] == 0
        finalize_chapter(config, chapter_number=chapter_number, approved_by="phase8-owner")
        run_chapter_semantic(config, root, chapter_number)
        chapter_close(config, chapter_number=chapter_number, approved_by="phase8-owner")

        if chapter_number in {1, 5, 20}:
            verification = verify_artifacts(config)
            status = artifact_status(config)
            archived_manifests = [
                read_json(path)
                for path in sorted((root / "70_runtime" / "artifacts" / "chapters").glob("ch*.manifest.json"))
            ]
            for manifest in archived_manifests:
                assert manifest["schema"] == "chapter_artifact_archive_v3"
                assert manifest["deduplicated_entries"] >= 1
                archive = root / manifest["archive"]
                with zipfile.ZipFile(archive) as handle:
                    assert "_audit/manifest.json" in handle.namelist()
            milestones[chapter_number] = {
                "verify_ok": verification.ok,
                "archive_files": status.archive_files,
                "archived_loose_duplicates": status.archived_loose_duplicates,
                "active_buffer_chapters": status.active_buffer_chapters,
                "next_chapter": production_next(config)["chapter_number"],
            }

    assert milestones[1]["next_chapter"] == 2
    assert milestones[5] == {
        "verify_ok": True,
        "archive_files": 3,
        "archived_loose_duplicates": 0,
        "active_buffer_chapters": (4, 5),
        "next_chapter": 6,
    }
    assert milestones[20] == {
        "verify_ok": True,
        "archive_files": 18,
        "archived_loose_duplicates": 0,
        "active_buffer_chapters": (19, 20),
        "next_chapter": 21,
    }
    assert not list((root / "70_runtime" / "transactions" / "s").glob("*"))
    for chapter_number in range(1, 19):
        assert not (root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md").exists()
        assert not (root / "40_manuscript" / "submitted" / f"ch{chapter_number:03d}.md").exists()

    restored = restore_artifacts(config, chapter_number=1)
    assert restored.restored_files
    assert artifact_status(config).archived_loose_duplicates > 0
    assert verify_artifacts(config).ok is False


def seed_project(tmp_path: Path, name: str):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / name)
    config = load_project_config(
        project.project_config,
        cli_overrides={"editorial": {"review_mode": "off"}},
    )
    open_book(config)
    mark_project_ready(project.root, config)
    return config, project.root


def authorize(monkeypatch) -> None:
    monkeypatch.setattr(
        "longform_engine.agent_pipeline.require_agent_first_production_pipeline",
        lambda: AUTHORIZATION,
    )
    monkeypatch.setattr(
        "longform_engine.production.require_agent_first_production_pipeline",
        lambda: AUTHORIZATION,
    )


def run_chapter_write(config, root: Path, chapter_number: int) -> dict[str, object]:
    task = continue_write(config, chapter_number=chapter_number)
    manifest = load_manifest(root, f"chapter_write:ch{chapter_number:03d}:v1")
    brief = agent_task_brief(config, manifest["task_id"], host="codex")
    assert brief["role_id"] == "chapter_author"
    assert len(manifest["input_files"]) <= 7
    output = Path(task.recommended_agent_draft)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(passing_chapter_text(chapter_number), encoding="utf-8")
    protocol_result = validate_production_agent_result(root, manifest, result_file=output)
    assert protocol_result.ok is True
    assert not (root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md").exists()
    assert not (root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json").exists()
    submitted = submit_agent_draft(
        config,
        chapter_number=chapter_number,
        file_path=output,
        agent="codex",
    )
    gate = read_json(Path(submitted.gate_result))
    assert not (root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md").exists()
    assert not (root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json").exists()
    return {
        "gate_passed": submitted.passed,
        "p0": int((gate.get("severity_counts") or {}).get("P0") or 0),
        "p1": int((gate.get("severity_counts") or {}).get("P1") or 0),
    }


def run_chapter_semantic(config, root: Path, chapter_number: int) -> dict[str, Path]:
    output = prepare_unified_semantic_bundle(root, config, chapter_number)
    manifest = load_manifest(root, f"chapter_semantic:ch{chapter_number:03d}:v1")
    brief = agent_task_brief(config, manifest["task_id"], host="codex")
    assert brief["role_id"] == "chapter_semantic_archivist"
    protocol_result = validate_production_agent_result(root, manifest, result_file=output)
    assert protocol_result.ok is True
    applied = semantic_apply(config, chapter_number=chapter_number, file_path=output)
    return {"ledger": Path(applied.ledger_file), "transaction": Path(applied.transaction_file)}


def passing_chapter_text(chapter_number: int) -> str:
    marker = f"PHASE8_CHAPTER_{chapter_number:02d}"
    sentence = (
        f"{marker} Ari keeps the verified route, pays a visible cost, and leaves one unresolved clue at the gate? "
    )
    return f"# Chapter {chapter_number}\n\n" + sentence * 35 + "\n"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
