import sys
from pathlib import Path

import pytest

from longform_engine.agent_jobs import CodexAgentJobError, CodexAgentJobManager
from longform_engine.agent_tasks import list_manifests
from longform_engine.config import load_project_config
from longform_engine.intelligence import create_intelligence_task
from longform_engine.orchestration import open_book
from longform_engine.storage import init_project


def _project_with_task(tmp_path: Path):
    initialized = init_project(
        load_project_config(template="qidian-longform"), output=tmp_path / "novel"
    )
    config = load_project_config(initialized.project_config)
    open_book(config)
    task = create_intelligence_task(config, task_type="book_ideation")
    return config, initialized.root, task


def _fake_codex(tmp_path: Path, *, write_unexpected: bool = False, sleep_seconds: float = 0) -> tuple[str, ...]:
    script = tmp_path / ("fake_codex_unexpected.py" if write_unexpected else "fake_codex.py")
    lines = [
        "import json, pathlib, re, sys, time",
        f"time.sleep({sleep_seconds!r})",
        "prompt = sys.stdin.read()",
        "match = re.search(r'^ONLY_OUTPUT_PATH=(.+)$', prompt, flags=re.MULTILINE)",
        "if match is None: raise SystemExit(3)",
        "target = pathlib.Path.cwd() / match.group(1).strip()",
        "target.parent.mkdir(parents=True, exist_ok=True)",
        "target.write_text('# 开书创意\\n\\n## 创作问题\\n谁会持续阅读？\\n\\n## 可选方案\\n方案甲与方案乙。\\n\\n## 方案代价\\n两者都有明确代价。\\n\\n## 人工决定\\n等待作者选择。\\n', encoding='utf-8')",
    ]
    if write_unexpected:
        lines.append("(pathlib.Path.cwd() / 'unexpected.txt').write_text('越界', encoding='utf-8')")
    lines.extend(
        [
            "print(json.dumps({'type': 'thread.started', 'thread_id': '11111111-1111-4111-8111-111111111111'}))",
            "print(json.dumps({'type': 'turn.completed'}))",
        ]
    )
    script.write_text("\n".join(lines), encoding="utf-8")
    return (sys.executable, str(script))


def test_codex_job_copies_only_manifest_inputs_validates_output_and_never_applies_canon(tmp_path):
    config, root, task = _project_with_task(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for directory in ("10_bible", "20_outline", "30_state")
        for path in (root / directory).rglob("*")
        if path.is_file()
    }
    manager = CodexAgentJobManager(codex_command=_fake_codex(tmp_path))

    started = manager.start(config, task.task_id)
    completed = manager.wait(config, started["job_id"], timeout=10)

    assert completed["status"] == "completed"
    from datetime import datetime
    times = [datetime.fromisoformat(completed[key]) for key in ("created_at", "started_at", "finished_at", "updated_at")]
    assert times == sorted(times)
    assert completed["validation"]["ok"] is True
    assert completed["canonical_mutated"] is False
    assert completed["auto_apply"] is False
    assert completed["auto_finalize"] is False
    assert completed["session"]["action"] == "continue_project_session"
    assert completed["session"]["session_id"] == "11111111-1111-4111-8111-111111111111"
    assert (root / task.candidate_file).is_file()
    assert next(item for item in list_manifests(root) if item["task_id"] == task.task_id)[
        "status"
    ] == "submitted"
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for directory in ("10_bible", "20_outline", "30_state")
        for path in (root / directory).rglob("*")
        if path.is_file()
    }
    assert after == before


def test_codex_job_rejects_any_staging_write_beyond_the_one_declared_output(tmp_path):
    config, root, task = _project_with_task(tmp_path)
    manager = CodexAgentJobManager(
        codex_command=_fake_codex(tmp_path, write_unexpected=True)
    )

    started = manager.start(config, task.task_id)
    completed = manager.wait(config, started["job_id"], timeout=10)

    assert completed["status"] == "rejected_output_boundary"
    assert completed["unexpected_files"] == ["unexpected.txt"]
    assert not (root / task.candidate_file).exists()
    assert next(item for item in list_manifests(root) if item["task_id"] == task.task_id)[
        "status"
    ] == "awaiting_agent"


def test_terminal_job_is_visible_only_after_cleanup_and_cannot_restart_submitted_task(tmp_path, monkeypatch):
    import threading

    config, _root, task = _project_with_task(tmp_path)
    manager = CodexAgentJobManager(codex_command=_fake_codex(tmp_path))
    written, release = threading.Event(), threading.Event()
    persist = manager._write_job

    def pause_after_terminal(job_dir, record):
        persist(job_dir, record)
        if record.get("status") == "completed":
            written.set()
            release.wait(10)

    monkeypatch.setattr(manager, "_write_job", pause_after_terminal)
    job = manager.start(config, task.task_id)
    try:
        assert written.wait(10)
        assert manager.status(config, job["job_id"])["phase"] == "finishing"
        assert manager.list_jobs(config)[0]["status"] == "running"
        with pytest.raises(CodexAgentJobError, match="已有运行中的"):
            manager.start(config, task.task_id)
    finally:
        release.set()
    assert manager.wait(config, job["job_id"], timeout=10)["status"] == "completed"
    with pytest.raises(CodexAgentJobError, match="submitted.*不允许再次启动"):
        manager.start(config, task.task_id)


def test_codex_job_allows_only_one_active_state_changing_job_per_project(tmp_path):
    config, _root, task = _project_with_task(tmp_path)
    manager = CodexAgentJobManager(
        codex_command=_fake_codex(tmp_path, sleep_seconds=2)
    )

    started = manager.start(config, task.task_id)
    with pytest.raises(CodexAgentJobError, match="已有运行中的 Codex 任务"):
        manager.start(config, task.task_id)
    cancelled = manager.cancel(config, started["job_id"])

    assert cancelled["status"] in {"cancelling", "cancelled"}
    assert manager.wait(config, started["job_id"], timeout=10)["status"] == "cancelled"


def test_cancel_stops_owned_child_process_and_allows_retry(tmp_path):
    import time
    from longform_engine.storage.project import process_start_identity
    config, root, task = _project_with_task(tmp_path)
    pid_file = tmp_path / "child.pid"
    script = tmp_path / "owned_child.py"
    script.write_text(
        "import pathlib,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n", encoding="utf-8")
    manager = CodexAgentJobManager(codex_command=(sys.executable, str(script)))
    job = manager.start(config, task.task_id)
    deadline = time.monotonic() + 10
    while not pid_file.is_file() and time.monotonic() < deadline:
        time.sleep(.05)
    assert pid_file.is_file()
    child_pid = int(pid_file.read_text())
    manager.cancel(config, job["job_id"])
    assert manager.wait(config, job["job_id"], timeout=10)["status"] == "cancelled"
    assert not process_start_identity(child_pid)
    assert not (root / task.candidate_file).exists()
    manager.codex_command = _fake_codex(tmp_path)
    retried = manager.start(config, task.task_id)
    assert manager.wait(config, retried["job_id"], timeout=10)["status"] == "completed"
