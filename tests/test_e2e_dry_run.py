import os
import subprocess
import sys
from pathlib import Path

from longform_engine.config import load_project_config
from tests.project_fixtures import mark_project_ready


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "longform_engine.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_e2e_dry_run_init_open_continue_gate_and_rebuild(tmp_path):
    project_dir = tmp_path / "novel"

    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(project_dir))
    project_yaml = project_dir / "project.yaml"
    project_text = project_yaml.read_text(encoding="utf-8")
    project_text = project_text.replace('mode: "agent_skill"', 'mode: "template_dry_run"')
    project_text = project_text.replace("mode: agent_skill", "mode: template_dry_run")
    project_yaml.write_text(project_text, encoding="utf-8")
    open_book = run_cli("open-book", str(project_yaml))
    mark_project_ready(project_dir, load_project_config(project_yaml))
    snapshot = run_cli("revision", "snapshot", str(project_yaml), "--label", "e2e")
    cont = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    gate = run_cli("gate-check", str(project_yaml), "--chapter", "1")
    rebuild = run_cli("db", "rebuild", str(project_yaml))

    assert init.returncode == 0
    assert open_book.returncode == 0
    assert snapshot.returncode == 0
    assert cont.returncode == 0
    assert gate.returncode in (0, 1)
    assert rebuild.returncode == 0
    assert "OK: gate-check completed" in gate.stdout
    assert "OK: database rebuilt" in rebuild.stdout
    assert "OK: snapshot created" in snapshot.stdout
    assert (project_dir / "40_manuscript" / "draft" / "ch001.md").exists()
    assert (project_dir / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").exists()
    assert (project_dir / "70_runtime" / "db" / "longform_engine.sqlite").exists()
