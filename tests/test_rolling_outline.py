"""Current planning replaces the retired card-producing outline commands."""
from pathlib import Path

import pytest

from longform_engine.intelligence import create_intelligence_task
from longform_engine.planning import apply_planning_bundle
from longform_engine.planning.context import load_chapter_planning_context
from tests.test_current_planning_context import approved_project


@pytest.mark.parametrize("task_type", ["outline_design", "outline_extension", "chapter_direction", "outline_revision"])
def test_retired_planning_tasks_require_explicit_current_rebuild(tmp_path, task_type):
    config, root = approved_project(tmp_path)
    before = {str(path.relative_to(root)): path.read_bytes() for path in (root / "20_outline").rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="planning task"):
        create_intelligence_task(config, task_type=task_type, chapter_number=1, from_chapter=1, to_chapter=3)
    assert before == {str(path.relative_to(root)): path.read_bytes() for path in (root / "20_outline").rglob("*") if path.is_file()}
    assert load_chapter_planning_context(root, 1).contract["schema"] == "chapter_contract_v5"


def test_current_planning_apply_failure_preserves_approved_canonical(tmp_path, monkeypatch):
    import longform_engine.planning.workflow as workflow
    config, root = approved_project(tmp_path)
    before = {str(path.relative_to(root)): path.read_bytes() for directory in ("20_outline", "30_state")
              for path in (root / directory).rglob("*") if path.is_file()}
    original = workflow._write_json
    writes = 0
    def fail_after_first_write(path: Path, value):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("injected planning write failure")
        return original(path, value)
    monkeypatch.setattr(workflow, "_write_json", fail_after_first_write)
    folder = root / "50_workbench/planning"
    with pytest.raises(OSError, match="injected planning"):
        apply_planning_bundle(config, bundle_path=folder / "bundle.json", application_path=folder / "application.json",
                              approval_path=folder / "approval.json", node_decisions_path=folder / "node-decisions.json",
                              approved_by="human")
    assert before == {str(path.relative_to(root)): path.read_bytes() for directory in ("20_outline", "30_state")
                      for path in (root / directory).rglob("*") if path.is_file()}
