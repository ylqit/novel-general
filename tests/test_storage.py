from pathlib import Path
import json

from longform_engine.config import load_project_config
from longform_engine.storage import StorageError, acquire_project_lock, apply_transaction, atomic_write_text, init_project, snapshot_project


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
    assert applied["metadata"]["applied"] == 1
    assert applied["boundary"]["rollback_restores_touched_paths"] is True
    assert "/objects/" in applied["snapshots"][0]["snapshot_path"].replace("\\", "/")
    assert "30_state/story_graph.json" not in applied["snapshots"][0]["snapshot_path"].replace("\\", "/")
    assert applied["snapshots_retained"] is False
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
    assert rollback["error"]["message"] == "simulate apply failure"
    assert before_rollback != before
    assert existing.read_text(encoding="utf-8") == before_rollback
    assert not created.exists()
