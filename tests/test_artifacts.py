from __future__ import annotations

import json
from pathlib import Path

import pytest

from longform_engine.artifacts import artifact_status, compact_artifacts, restore_artifacts, verify_artifacts
from longform_engine.config import load_project_config
from longform_engine.storage import init_project


def test_artifact_compaction_is_hash_verified_and_reversible(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = project.root
    chapter_one = root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json"
    chapter_two = root / "50_workbench" / "gate_artifacts" / "ch002" / "gate_result.json"
    chapter_one.parent.mkdir(parents=True, exist_ok=True)
    chapter_two.parent.mkdir(parents=True, exist_ok=True)
    chapter_one.write_text('{"chapter": 1}\n', encoding="utf-8")
    chapter_two.write_text('{"chapter": 2}\n', encoding="utf-8")
    task_manifest = root / "50_workbench" / "gate_artifacts" / "ch001" / "ch001.agent_task.json"
    task_manifest.write_text("{}\n", encoding="utf-8")

    snapshot = root / "70_runtime" / "transactions" / "s" / "committed"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "copy.bin").write_bytes(b"snapshot")
    report = root / "70_runtime" / "transactions" / "20260101_test.json"
    report.write_text(
        json.dumps({"status": "applied", "snapshot_dir": "70_runtime/transactions/s/committed"}),
        encoding="utf-8",
    )

    dry_run = compact_artifacts(config, through=1, dry_run=True)
    assert dry_run.candidate_files == 2
    assert dry_run.committed_snapshots == 1
    assert chapter_one.exists()
    with pytest.raises(ValueError, match="without closure records"):
        compact_artifacts(config, through=1, dry_run=False)
    for chapter_number in (1, 2, 3):
        closure = root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json"
        closure.parent.mkdir(parents=True, exist_ok=True)
        closure.write_text(json.dumps({"chapter_number": chapter_number}) + "\n", encoding="utf-8")

    result = compact_artifacts(config, through=1, dry_run=False)
    assert result.removed_files == 2
    assert not chapter_one.exists()
    assert chapter_two.exists()
    assert not task_manifest.exists()
    assert not snapshot.exists()
    assert verify_artifacts(config).ok is True

    restored = restore_artifacts(config, chapter_number=1)
    assert set(restored.restored_files) == {
        "50_workbench/gate_artifacts/ch001/ch001.agent_task.json",
        "50_workbench/gate_artifacts/ch001/gate_result.json",
    }
    assert chapter_one.exists()
    assert task_manifest.exists()
    status = artifact_status(config)
    assert status.archive_files == 1
    assert status.committed_snapshot_dirs == 0

    chapter_one.write_text('{"chapter": 1, "changed": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="older version"):
        compact_artifacts(config, through=1, dry_run=False)
    assert chapter_one.exists()
