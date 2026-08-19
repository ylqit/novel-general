from pathlib import Path
import json
import socket

import pytest

from longform_engine.config import load_project_config
from longform_engine.production import production_next
from longform_engine.storage import (
    StorageError,
    acquire_project_lock,
    apply_transaction,
    atomic_write_text,
    cleanup_committed_transaction,
    discard_preparing_transaction,
    init_project,
    recovery_status,
    reclaim_project_lock,
    rollback_prepared_transaction,
    snapshot_project,
)
from longform_engine.storage.layout import (
    chapter_filename,
    list_finalized_chapter_files,
    parse_canonical_chapter_number,
)


def test_init_project_creates_canonical_layout(tmp_path):
    config = load_project_config(template="qidian-longform")
    result = init_project(config, output=tmp_path / "novel")

    assert result.project_config.exists()
    for relative in [
        "00_governance/idea_seed.md",
        "10_bible/world.md",
        "20_outline/chapter_cards",
        "30_state/story_graph.json",
        "40_manuscript/final",
        "50_workbench/research_inbox",
        "50_workbench/writing_tasks",
        "50_workbench/agent_drafts",
        "60_rag/context/next_plot_context.md",
        "70_runtime/db",
        "80_exports/platform",
    ]:
        assert (result.root / Path(relative)).exists()


def test_canonical_chapter_filename_contract_accepts_four_digits_and_ignores_json(tmp_path):
    final_dir = tmp_path / "40_manuscript" / "final"
    final_dir.mkdir(parents=True)
    (final_dir / "ch1000.md").write_text("# 第1000章\n", encoding="utf-8")
    (final_dir / "ch1000.submission.json").write_text("{}\n", encoding="utf-8")

    assert chapter_filename(1) == "ch001.md"
    assert chapter_filename(1000) == "ch1000.md"
    assert parse_canonical_chapter_number("ch1000.md") == 1000
    assert parse_canonical_chapter_number("chapter_1000.md") is None
    assert list_finalized_chapter_files(tmp_path) == ((1000, final_dir / "ch1000.md"),)


@pytest.mark.parametrize(
    "name",
    ["ch001.txt", "ch001.rtf", "chapter_001.md", "1.md", "ch1.md", "ch01.md", "ch000.md", "ch0001.md", "第1章.md"],
)
def test_canonical_chapter_filename_contract_rejects_aliases(tmp_path, name):
    final_dir = tmp_path / "40_manuscript" / "final"
    final_dir.mkdir(parents=True)
    (final_dir / name).write_text("retired alias", encoding="utf-8")

    with pytest.raises(ValueError, match="Non-canonical manuscript filename"):
        list_finalized_chapter_files(tmp_path)


def test_init_project_is_idempotent_without_force(tmp_path):
    config = load_project_config(template="qidian-longform")
    first = init_project(config, output=tmp_path / "novel")
    second = init_project(config, output=tmp_path / "novel")

    assert first.created_files
    assert second.created_files == ()
    assert second.project_config.exists()


def test_atomic_write_overwrites_without_partial_file(tmp_path):
    path = tmp_path / "nested" / "file.txt"

    atomic_write_text(path, "first")
    atomic_write_text(path, "second")

    assert path.read_text(encoding="utf-8") == "second"
    assert not list(path.parent.glob("tmp*"))


def test_project_lock_blocks_concurrent_mutation(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    lock_path = project.root / "70_runtime" / "locks" / "project.lock"

    with acquire_project_lock(project_config, owner="test", command="lock-test"):
        assert lock_path.exists()
        try:
            acquire_project_lock(project_config, owner="other", command="lock-test").acquire()
        except StorageError as exc:
            assert "Project lock already exists" in str(exc)
        else:
            raise AssertionError("Expected StorageError")

    assert not lock_path.exists()


def test_snapshot_project_copies_state_and_manifest(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    (project.root / "40_manuscript" / "draft" / "ch001.md").write_text("# 第一章\n\n草稿。\n", encoding="utf-8")

    result = snapshot_project(project_config, label="before_mutation")

    assert result.snapshot_dir.exists()
    assert (result.snapshot_dir / "snapshot_manifest.json").exists()
    assert (result.snapshot_dir / "30_state" / "novel_state.json").exists()
    assert (result.snapshot_dir / "40_manuscript" / "draft" / "ch001.md").exists()


def test_apply_transaction_writes_report_and_rolls_back_touched_paths(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root
    existing = root / "30_state" / "story_graph.json"
    created = root / "40_manuscript" / "final" / "ch001.md"
    before = existing.read_text(encoding="utf-8")

    with apply_transaction(
        root,
        command="graph semantic-apply",
        chapter_number=1,
        source_paths=[root / "50_workbench" / "graph_updates" / "ch001.semantic.json"],
        touched_paths=[existing],
    ) as transaction:
        existing.write_text('{"changed": true}\n', encoding="utf-8")
        transaction.update_metadata(applied=1)

    reports = list((root / "70_runtime" / "transactions").glob("*graph_semantic_apply_ch001.json"))
    assert reports
    applied = json.loads(reports[-1].read_text(encoding="utf-8"))
    assert applied["status"] == "applied"
    assert applied["schema"] == "canonical_write_transaction_report_v3"
    assert applied["schema_version"] == 3
    assert applied["cleanup_complete"] is True
    assert applied["metadata"]["applied"] == 1
    assert applied["boundary"]["rollback_restores_touched_paths"] is True
    assert applied["inventory_targets"]["filesystem"] == ["30_state/story_graph.json"]
    assert "/objects/" in applied["snapshots"][0]["snapshot_path"].replace("\\", "/")
    assert "30_state/story_graph.json" not in applied["snapshots"][0]["snapshot_path"].replace("\\", "/")
    assert applied["snapshots_retained"] is False
    assert applied["before_state"][0]["path"] == "30_state/story_graph.json"
    assert applied["after_state"][0]["sha256"]
    assert not (root / applied["snapshot_dir"]).exists()
    before_rollback = existing.read_text(encoding="utf-8")

    try:
        with apply_transaction(
            root,
            command="chapter finalize",
            chapter_number=1,
            source_paths=[root / "40_manuscript" / "draft" / "ch001.md"],
            touched_paths=[existing, created],
        ):
            existing.write_text('{"broken": true}\n', encoding="utf-8")
            created.write_text("# Broken\n", encoding="utf-8")
            raise RuntimeError("simulate apply failure")
    except RuntimeError:
        pass
    else:
        raise AssertionError("Expected RuntimeError")

    rollback_reports = list((root / "70_runtime" / "transactions").glob("*chapter_finalize_ch001.rollback.json"))
    assert rollback_reports
    rollback = json.loads(rollback_reports[-1].read_text(encoding="utf-8"))
    assert rollback["status"] == "rolled_back"
    assert rollback["cleanup_complete"] is True
    assert rollback["error"]["message"] == "simulate apply failure"
    assert before_rollback != before
    assert existing.read_text(encoding="utf-8") == before_rollback
    assert not created.exists()


def test_apply_transaction_uses_sqlite_backup_and_never_copies_runtime_db_directory(tmp_path):
    import sqlite3

    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root
    database = root / "70_runtime" / "db" / "longform_engine.sqlite"
    vector = root / "70_runtime" / "db" / "vector_store.sqlite"
    for path, value in ((database, "canonical"), (vector, "vector")):
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE state (value TEXT NOT NULL)")
            connection.execute("INSERT INTO state VALUES (?)", (value,))

    with pytest.raises(RuntimeError, match="database failure"):
        with apply_transaction(
            root,
            command="chapter semantic-apply",
            chapter_number=1,
            touched_paths=[root / "70_runtime" / "db"],
        ):
            for path in (database, vector):
                with sqlite3.connect(path) as connection:
                    connection.execute("UPDATE state SET value = 'broken'")
                Path(str(path) + "-wal").write_bytes(b"interrupted sidecar")
            raise RuntimeError("database failure")

    values = []
    for path in (database, vector):
        with sqlite3.connect(path) as connection:
            values.append(connection.execute("SELECT value FROM state").fetchone()[0])
    assert values == ["canonical", "vector"]
    assert not Path(str(database) + "-wal").exists()
    assert not Path(str(vector) + "-wal").exists()
    report_path = sorted((root / "70_runtime" / "transactions").glob("*semantic_apply_ch001.rollback.json"))[-1]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert len(report["sqlite_backups"]) == 2
    assert report["snapshots"] == []
    assert report["cleanup_complete"] is True
    assert not (root / report["snapshot_dir"]).exists()


def test_recovery_rolls_back_prepared_transaction_with_exact_report_hash(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    target = project.root / "30_state" / "story_graph.json"
    before = target.read_text(encoding="utf-8")
    transaction = apply_transaction(
        project.root,
        command="recovery prepared fixture",
        touched_paths=[target],
    )
    transaction.begin()
    target.write_text('{"interrupted": true}\n', encoding="utf-8")

    status = recovery_status(project_config)
    pending = next(item for item in status["transactions"] if item["state"] == "recoverable_rollback")
    result = rollback_prepared_transaction(
        project_config,
        report=pending["path"],
        expected_sha256=pending["sha256"],
        approved_by="storage-test",
    )

    assert target.read_text(encoding="utf-8") == before
    assert result["result"]["status"] == "rolled_back"
    assert recovery_status(project_config)["blocked"] is False


def test_recovery_rejects_prepared_transaction_with_incomplete_inventory(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    target = project.root / "30_state" / "story_graph.json"
    transaction = apply_transaction(
        project.root,
        command="recovery incomplete inventory fixture",
        touched_paths=[target],
    )
    transaction.begin()
    payload = json.loads(transaction.report_file.read_text(encoding="utf-8"))
    payload["snapshots"] = []
    atomic_write_text(transaction.report_file, json.dumps(payload, indent=2) + "\n")

    status = recovery_status(project_config)
    pending = next(item for item in status["transactions"] if item["path"].endswith(transaction.report_file.name))

    assert pending["state"] == "need_human"
    assert pending["reason"] == "transaction_inventory_empty"


def test_recovery_rejects_inventory_that_does_not_cover_every_touched_path(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    target = project.root / "30_state" / "story_graph.json"
    transaction = apply_transaction(
        project.root,
        command="recovery incomplete target coverage fixture",
        touched_paths=[target],
    )
    transaction.begin()
    payload = json.loads(transaction.report_file.read_text(encoding="utf-8"))
    payload["inventory_targets"]["filesystem"] = []
    payload["snapshots"] = []
    atomic_write_text(transaction.report_file, json.dumps(payload, indent=2) + "\n")

    status = recovery_status(project_config)
    pending = next(item for item in status["transactions"] if item["path"].endswith(transaction.report_file.name))

    assert pending["state"] == "need_human"
    assert pending["reason"] == "transaction_inventory_targets_do_not_cover_touched_paths"


def test_recovery_discards_preparing_snapshot_without_touching_canonical_state(tmp_path, monkeypatch):
    import longform_engine.storage.project as project_module

    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    target = project.root / "30_state" / "story_graph.json"
    before = target.read_text(encoding="utf-8")
    transaction = apply_transaction(
        project.root,
        command="recovery preparing fixture",
        touched_paths=[target],
    )
    monkeypatch.setattr(
        project_module,
        "snapshot_transaction_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("snapshot interrupted")),
    )
    with pytest.raises(RuntimeError, match="snapshot interrupted"):
        transaction.begin()

    status = recovery_status(project_config)
    pending = next(item for item in status["transactions"] if item["state"] == "recoverable_discard")
    result = discard_preparing_transaction(
        project_config,
        report=pending["path"],
        expected_sha256=pending["sha256"],
        approved_by="storage-test",
    )

    assert target.read_text(encoding="utf-8") == before
    assert result["result"]["status"] == "discarded"
    assert recovery_status(project_config)["blocked"] is False


def test_recovery_only_cleans_snapshots_after_durable_applied_marker(tmp_path, monkeypatch):
    import longform_engine.storage.project as project_module

    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    target = project.root / "30_state" / "story_graph.json"
    real_cleanup = project_module.cleanup_transaction_snapshot
    monkeypatch.setattr(project_module, "cleanup_transaction_snapshot", lambda _path: ["simulated cleanup interruption"])
    with apply_transaction(
        project.root,
        command="recovery applied fixture",
        touched_paths=[target],
    ):
        target.write_text('{"committed": true}\n', encoding="utf-8")
    monkeypatch.setattr(project_module, "cleanup_transaction_snapshot", real_cleanup)

    status = recovery_status(project_config)
    pending = next(item for item in status["transactions"] if item["state"] == "recoverable_cleanup")
    report_path = project.root / pending["path"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "applied"
    snapshot_object = project.root / report["snapshots"][0]["snapshot_path"]
    snapshot_object.unlink()
    pending = next(
        item
        for item in recovery_status(project_config)["transactions"]
        if item["path"] == pending["path"]
    )
    assert pending["state"] == "recoverable_cleanup"
    result = cleanup_committed_transaction(
        project_config,
        report=pending["path"],
        expected_sha256=pending["sha256"],
        approved_by="storage-test",
    )

    assert json.loads(target.read_text(encoding="utf-8"))["committed"] is True
    assert result["result"]["status"] == "cleaned"
    assert recovery_status(project_config)["blocked"] is False


def test_recovery_reclaims_only_confirmed_dead_lock_with_exact_hash(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    lock_path = project.root / "70_runtime" / "locks" / "project.lock"
    lock_path.write_text(
        json.dumps(
            {
                "schema": "project_lock_v2",
                "schema_version": 2,
                "owner": "interrupted-test",
                "owner_token": "dead-owner-token",
                "command": "fixture",
                "created_at": "2026-08-19T00:00:00+00:00",
                "root": str(project.root.resolve()),
                "pid": 999_999_999,
                "hostname": socket.gethostname(),
                "process_identity": "dead-process",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    status = recovery_status(project_config)
    assert status["lock"]["state"] == "confirmed_dead"
    next_action = production_next(project_config)
    assert next_action["status"] == "project_recovery_required"
    assert "recovery reclaim-lock" in next_action["next_command"]
    result = reclaim_project_lock(
        project_config,
        expected_sha256=status["lock"]["sha256"],
        approved_by="storage-test",
    )

    assert not lock_path.exists()
    assert result["result"]["status"] == "reclaimed"
