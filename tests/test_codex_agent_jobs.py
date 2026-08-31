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


def test_codex_job_allows_only_one_active_state_changing_job_per_project(tmp_path):
    config, _root, task = _project_with_task(tmp_path)
    manager = CodexAgentJobManager(
        codex_command=_fake_codex(tmp_path, sleep_seconds=2)
    )

    started = manager.start(config, task.task_id)
    with pytest.raises(CodexAgentJobError, match="已有运行中的 Codex 任务"):
        manager.start(config, task.task_id)
    cancelled = manager.cancel(config, started["job_id"])

    assert cancelled["status"] == "cancelled"
