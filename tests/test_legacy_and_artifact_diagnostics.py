import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from longform_engine.artifacts import compact_artifacts, verify_artifacts
from longform_engine.config import load_project_config
from longform_engine.legacy import closure_is_valid, legacy_backfill, legacy_compact, legacy_status
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


def seed_project(tmp_path: Path, name: str = "legacy"):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / name)
    config = load_project_config(project.project_config)
    mark_project_ready(project.root, config)
    return config, project.root


def write_final_and_gate(root: Path, chapter: int) -> Path:
    final = root / "40_manuscript" / "final" / f"ch{chapter:03d}.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(f"# 第{chapter}章\n\n这是一段已定稿且等待语义回填的正文。\n", encoding="utf-8")
    gate = root / "50_workbench" / "gate_artifacts" / f"ch{chapter:03d}" / "gate_result.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(
        json.dumps(
            {
                "chapter_number": chapter,
                "passed": True,
                "source_sha256": sha256(final.read_bytes()).hexdigest(),
                "severity_counts": {"P0": 0, "P1": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return final


def write_ledger(root: Path, chapter: int, final: Path) -> Path:
    ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        json.dumps(
            {
                "schema": "chapter_semantic_bundle_v1",
                "canonical": True,
                "chapter_number": chapter,
                "source": {
                    "path": f"40_manuscript/final/ch{chapter:03d}.md",
                    "sha256": sha256(final.read_bytes()).hexdigest(),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return ledger


def tree_hash(root: Path) -> str:
    digest = sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_artifact_dry_run_reports_blocker_and_verify_reports_migration_required(tmp_path):
    config, root = seed_project(tmp_path)
    write_final_and_gate(root, 1)
    before = tree_hash(root)

    dry_run = compact_artifacts(config, through=1, dry_run=True)
    verification = verify_artifacts(config)

    assert not dry_run.eligible
    assert any("without closure records" in blocker for blocker in dry_run.blockers)
    assert verification.status == "migration_required"
    assert verification.migration_required_chapters == (1,)
    assert tree_hash(root) == before


def test_artifact_verify_reports_only_latest_materialized_final_as_pending_close(tmp_path):
    config, root = seed_project(tmp_path)
    final = write_final_and_gate(root, 1)
    write_ledger(root, 1, final)

    result = verify_artifacts(config)

    assert result.ok
    assert result.status == "pending_close"
    assert result.pending_close_chapters == (1,)


def test_legacy_backfill_creates_only_earliest_missing_semantic_task(tmp_path):
    config, root = seed_project(tmp_path)
    write_final_and_gate(root, 1)
    write_final_and_gate(root, 2)

    status = legacy_status(config, through=2)
    result = legacy_backfill(config, through=2)

    assert status["backfillable_chapters"] == [1, 2]
    assert result["created"]
    assert result["chapter_number"] == 1
    assert Path(result["manifest_file"]).is_file()
    assert not (root / "30_state" / "semantic_ledger" / "ch001.json").exists()
    assert not (root / "30_state" / "semantic_ledger" / "ch002.json").exists()


def test_legacy_compact_creates_migration_closures_without_manual_files(monkeypatch, tmp_path):
    config, root = seed_project(tmp_path)
    for chapter in range(1, 4):
        final = write_final_and_gate(root, chapter)
        write_ledger(root, chapter, final)
    monkeypatch.setattr("longform_engine.legacy.semantic_rebuild", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("longform_engine.legacy.verify_materialized_chapter", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "longform_engine.legacy.compact_artifacts",
        lambda *_args, **_kwargs: SimpleNamespace(archive_files=("ch001.zip",)),
    )

    dry_run = legacy_compact(config, through=3, approved_by="migration-owner", dry_run=True)
    assert dry_run["eligible"]
    assert not list((root / "30_state" / "chapter_closures").glob("ch*.json"))

    result = legacy_compact(config, through=3, approved_by="migration-owner", dry_run=False)
    assert result["closures_created"] == [1, 2, 3]
    closure = json.loads((root / "30_state" / "chapter_closures" / "ch001.json").read_text(encoding="utf-8"))
    assert closure["migration"]["schema"] == "legacy_closure_migration_v1"
    assert closure["approved_by"] == "migration-owner"


def test_legacy_compact_rebuild_failure_creates_no_closure(monkeypatch, tmp_path):
    config, root = seed_project(tmp_path)
    final = write_final_and_gate(root, 1)
    write_ledger(root, 1, final)
    monkeypatch.setattr(
        "longform_engine.legacy.semantic_rebuild",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("rebuild failed")),
    )

    with pytest.raises(RuntimeError, match="rebuild failed"):
        legacy_compact(config, through=1, approved_by="migration-owner", dry_run=False)
    assert not list((root / "30_state" / "chapter_closures").glob("ch*.json"))


def test_legacy_compact_repairs_stale_closure_only_after_evidence_rebuild(monkeypatch, tmp_path):
    config, root = seed_project(tmp_path)
    for chapter in range(1, 4):
        final = write_final_and_gate(root, chapter)
        write_ledger(root, chapter, final)
        closure = root / "30_state" / "chapter_closures" / f"ch{chapter:03d}.json"
        closure.parent.mkdir(parents=True, exist_ok=True)
        closure.write_text(
            json.dumps(
                {
                    "schema": "chapter_closure_v1",
                    "chapter_number": chapter,
                    "approved_by": "original-owner",
                    "final_sha256": sha256(final.read_bytes()).hexdigest(),
                    "semantic_ledger_sha256": "stale" if chapter == 1 else sha256(
                        (root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json").read_bytes()
                    ).hexdigest(),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr("longform_engine.legacy.semantic_rebuild", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("longform_engine.legacy.verify_materialized_chapter", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "longform_engine.legacy.compact_artifacts",
        lambda *_args, **_kwargs: SimpleNamespace(archive_files=("ch001.zip",)),
    )

    result = legacy_compact(config, through=3, approved_by="migration-owner", dry_run=False)

    assert result["closures_created"] == []
    assert result["closures_repaired"] == [1]
    repaired = json.loads((root / "30_state" / "chapter_closures" / "ch001.json").read_text(encoding="utf-8"))
    assert repaired["approved_by"] == "migration-owner"
    assert repaired["migration"]["repaired_stale_closure"] is True
    assert len(repaired["migration"]["prior_closure_sha256"]) == 64
    assert closure_is_valid(root, 1)
