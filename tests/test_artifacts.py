from __future__ import annotations

import json
from hashlib import sha256
import zipfile

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
    duplicate_text = "# Chapter 1\n\nThe archived candidate is not a canonical fact source.\n"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    submitted = root / "40_manuscript" / "submitted" / "ch001.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    submitted.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(duplicate_text, encoding="utf-8")
    submitted.write_text(duplicate_text, encoding="utf-8")

    snapshot = root / "70_runtime" / "transactions" / "s" / "committed"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "copy.bin").write_bytes(b"snapshot")
    report = root / "70_runtime" / "transactions" / "20260101_test.json"
    report.write_text(
        json.dumps(
            {
                "status": "applied",
                "chapter_number": 1,
                "snapshot_dir": "70_runtime/transactions/s/committed",
            }
        ),
        encoding="utf-8",
    )

    dry_run = compact_artifacts(config, through=1, dry_run=True)
    assert dry_run.candidate_files == 5
    assert dry_run.unique_content_files == 4
    assert dry_run.deduplicated_files == 1
    assert dry_run.unique_content_bytes < dry_run.candidate_bytes
    assert dry_run.committed_snapshots == 1
    assert chapter_one.exists()
    with pytest.raises(ValueError, match="without closure records"):
        compact_artifacts(config, through=1, dry_run=False)
    for chapter_number in (1, 2, 3):
        final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
        ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
        closure = root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json"
        final.parent.mkdir(parents=True, exist_ok=True)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        closure.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(duplicate_text if chapter_number == 1 else f"# Final {chapter_number}\n", encoding="utf-8")
        final_hash = sha256(final.read_bytes()).hexdigest()
        ledger.write_text(
            json.dumps(
                {
                    "chapter_number": chapter_number,
                    "canonical": True,
                    "source": {"sha256": final_hash},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        closure.write_text(
            json.dumps(
                {
                    "chapter_number": chapter_number,
                    "final_sha256": final_hash,
                    "semantic_ledger_sha256": sha256(ledger.read_bytes()).hexdigest(),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    evidence_dry_run = compact_artifacts(config, through=1, dry_run=True)
    assert evidence_dry_run.unique_content_files == 3
    assert evidence_dry_run.deduplicated_files == 2
    result = compact_artifacts(config, through=1, dry_run=False)
    assert result.removed_files == 5
    assert not chapter_one.exists()
    assert chapter_two.exists()
    assert not task_manifest.exists()
    assert not draft.exists()
    assert not submitted.exists()
    assert not report.exists()
    assert not snapshot.exists()
    assert verify_artifacts(config).ok is True

    manifest = json.loads((root / "70_runtime" / "artifacts" / "chapters" / "ch001.manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "chapter_artifact_archive_v3"
    assert {item["role"] for item in manifest["retained_evidence"]} == {"final", "semantic_ledger", "closure"}
    entry_by_path = {item["path"]: item for item in manifest["entries"]}
    assert entry_by_path["40_manuscript/draft/ch001.md"]["retained_role"] == "final"
    assert entry_by_path["40_manuscript/submitted/ch001.md"]["retained_role"] == "final"
    assert "member" not in entry_by_path["40_manuscript/draft/ch001.md"]
    assert manifest["deduplicated_entries"] == 2
    with zipfile.ZipFile(root / "70_runtime" / "artifacts" / "chapters" / "ch001.zip") as handle:
        assert "_audit/manifest.json" in handle.namelist()
        assert f"_audit/blobs/{entry_by_path['40_manuscript/draft/ch001.md']['sha256']}" not in handle.namelist()

    restored = restore_artifacts(config, chapter_number=1)
    assert set(restored.restored_files) == {
        "40_manuscript/draft/ch001.md",
        "40_manuscript/submitted/ch001.md",
        "50_workbench/gate_artifacts/ch001/ch001.agent_task.json",
        "50_workbench/gate_artifacts/ch001/gate_result.json",
        "70_runtime/transactions/20260101_test.json",
    }
    assert chapter_one.exists()
    assert task_manifest.exists()
    status = artifact_status(config)
    assert status.archive_files == 1
    assert status.committed_snapshot_dirs == 0
    assert status.archived_loose_duplicates == 5
    assert verify_artifacts(config).ok is False

    chapter_one.write_text('{"chapter": 1, "changed": true}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="older version"):
        compact_artifacts(config, through=1, dry_run=False)
    assert chapter_one.exists()


def test_artifact_verify_rejects_missing_archive_extra_members_and_active_buffer_archive(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = project.root
    for chapter_number in (1, 2, 3):
        write_retained_evidence(root, chapter_number)

    compact_artifacts(config, through=1, dry_run=False)
    archive = root / "70_runtime" / "artifacts" / "chapters" / "ch001.zip"
    with pytest.raises(ValueError, match="active buffer"):
        compact_artifacts(config, through=2, dry_run=False)

    with zipfile.ZipFile(archive, "a") as handle:
        handle.writestr("undeclared.txt", "unexpected")
    manifest_path = archive.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = sha256(archive.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result = verify_artifacts(config)
    assert result.ok is False
    assert any("undeclared archive members" in error for error in result.errors)


def test_artifact_restore_rejects_canonical_archive_member(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = project.root
    for chapter_number in (1, 2, 3):
        write_retained_evidence(root, chapter_number)
    compact_artifacts(config, through=1, dry_run=False)

    archive = root / "70_runtime" / "artifacts" / "chapters" / "ch001.zip"
    member = "40_manuscript/final/ch001.md"
    data = (root / member).read_bytes()
    with zipfile.ZipFile(archive, "a") as handle:
        handle.writestr(member, data)
    manifest_path = archive.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"].append({"path": member, "sha256": sha256(data).hexdigest(), "size": len(data)})
    manifest["entry_count"] = len(manifest["entries"])
    manifest["archive_sha256"] = sha256(archive.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    result = verify_artifacts(config)
    assert result.ok is False
    assert any("outside non-canonical chapter artifact lanes" in error for error in result.errors)
    with pytest.raises(ValueError, match="outside non-canonical chapter artifact lanes"):
        restore_artifacts(config, chapter_number=1)


def test_artifact_v2_archive_remains_verifiable_and_restorable(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = project.root
    for chapter_number in (1, 2, 3):
        write_retained_evidence(root, chapter_number)

    relative = "40_manuscript/draft/ch001.md"
    data = b"legacy v2 draft\n"
    archive_dir = root / "70_runtime" / "artifacts" / "chapters"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive = archive_dir / "ch001.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(relative, data)
    retained = []
    for role, retained_relative in (
        ("final", "40_manuscript/final/ch001.md"),
        ("semantic_ledger", "30_state/semantic_ledger/ch001.json"),
        ("closure", "30_state/chapter_closures/ch001.json"),
    ):
        retained_path = root / retained_relative
        retained.append(
            {
                "role": role,
                "path": retained_relative,
                "sha256": sha256(retained_path.read_bytes()).hexdigest(),
                "size": retained_path.stat().st_size,
            }
        )
    manifest = {
        "schema": "chapter_artifact_archive_v2",
        "chapter_number": 1,
        "archive": "70_runtime/artifacts/chapters/ch001.zip",
        "archive_sha256": sha256(archive.read_bytes()).hexdigest(),
        "entries": [{"path": relative, "sha256": sha256(data).hexdigest(), "size": len(data)}],
        "entry_count": 1,
        "uncompressed_bytes": len(data),
        "retained_evidence": retained,
        "retention_policy": {"active_buffer_chapters": 2},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    archive.with_suffix(".manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    assert verify_artifacts(config).ok is True
    restored = restore_artifacts(config, chapter_number=1)
    assert restored.restored_files == (relative,)
    assert (root / relative).read_bytes() == data


def write_retained_evidence(root, chapter_number: int) -> None:
    final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    closure = root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json"
    final.parent.mkdir(parents=True, exist_ok=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)
    closure.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(f"# Final {chapter_number}\n", encoding="utf-8")
    final_hash = sha256(final.read_bytes()).hexdigest()
    ledger.write_text(
        json.dumps(
            {
                "chapter_number": chapter_number,
                "canonical": True,
                "source": {"sha256": final_hash},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    closure.write_text(
        json.dumps(
            {
                "chapter_number": chapter_number,
                "final_sha256": final_hash,
                "semantic_ledger_sha256": sha256(ledger.read_bytes()).hexdigest(),
            }
        )
        + "\n",
        encoding="utf-8",
    )
