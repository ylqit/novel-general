"""Local Codex subprocess bridge constrained by AgentTaskManifest v5."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence
import json
import secrets
import shutil
import subprocess
import threading

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_tasks import (
    load_manifest,
    manifest_input_paths,
    manifest_output,
    resolve_under_root,
    validate_manifest_strict,
)
from longform_engine.config import ConfigDocument
from longform_engine.production import agent_task_brief
from longform_engine.storage import acquire_project_lock, atomic_write_text, resolve_project_root


ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "cancelling"})
TERMINAL_JOB_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "failed_validation",
        "rejected_output_boundary",
        "cancelled",
        "interrupted",
    }
)


class CodexAgentJobError(ValueError):
    """Raised before an unsafe or conflicting Codex job can start."""


class CodexAgentJobManager:
    """Run one manifest-authorized Codex job per project and persist compact status."""

    def __init__(self, *, codex_command: Sequence[str] | None = None) -> None:
        resolved = tuple(str(part) for part in (codex_command or (shutil.which("codex") or "codex",)))
        if not resolved or any(not part for part in resolved):
            raise CodexAgentJobError("Codex 启动命令无效")
        self.codex_command = resolved
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cancel_requested: set[str] = set()

    def runtime_status(self) -> dict[str, Any]:
        executable = shutil.which(self.codex_command[0]) or (
            self.codex_command[0] if Path(self.codex_command[0]).is_file() else ""
        )
        if not executable:
            return {
                "available": False,
                "installed": False,
                "logged_in": False,
                "reason": "本机未找到 Codex CLI",
            }
        version = subprocess.run(
            [*self.codex_command, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
        login = subprocess.run(
            [*self.codex_command, "login", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
        logged_in = login.returncode == 0 and "logged in" in (
            login.stdout + login.stderr
        ).casefold()
        return {
            "available": version.returncode == 0 and logged_in,
            "installed": version.returncode == 0,
            "logged_in": logged_in,
            "version": version.stdout.strip(),
            "reason": "" if logged_in else "本机 Codex 尚未登录",
        }

    def start(self, config: ConfigDocument, task: str | Path) -> dict[str, Any]:
        root = resolve_project_root(config)
        manifest = load_manifest(root, task)
        validation = validate_manifest_strict(root, manifest, strict=True)
        if not validation.ok:
            raise CodexAgentJobError("Agent manifest 无效：" + "; ".join(validation.errors))
        task_id = str(manifest.get("task_id") or "")
        status = str(manifest.get("status") or "awaiting_agent")
        if status not in {"awaiting_agent", "invalid"}:
            raise CodexAgentJobError(f"当前任务状态 {status} 不允许再次启动 Agent")
        brief = agent_task_brief(config, task_id, host="codex")
        if not brief.get("executable"):
            raise CodexAgentJobError(
                "Agent 工作单不可执行：" + str(brief.get("blocked_by") or "unknown")
            )

        with self._lock:
            active = self._active_job(root)
            if active is not None:
                raise CodexAgentJobError(
                    "当前项目已有运行中的 Codex 任务：" + str(active.get("job_id") or "")
                )
            job_id = "job_" + secrets.token_hex(12)
            job_dir = root / "70_runtime" / "agent_jobs" / job_id
            staging = job_dir / "staging"
            staging.mkdir(parents=True)
            declared_inputs = self._stage_inputs(root, staging, manifest, brief)
            output_path = str(manifest_output(manifest).get("path") or "")
            staged_output = resolve_under_root(staging, output_path)
            staged_output.parent.mkdir(parents=True, exist_ok=True)
            project_output = resolve_under_root(root, output_path)
            session = dict(brief.get("session") or {})
            resume_session_id = self._resume_session_id(root, session)
            initial_inventory = self._inventory(staging)
            record = {
                "schema": "codex_agent_job_v1",
                "job_id": job_id,
                "task_id": task_id,
                "task_type": str(manifest.get("task_type") or ""),
                "status": "queued",
                "project_root": str(root),
                "manifest_file": str(brief.get("manifest_file") or ""),
                "declared_input_files": declared_inputs,
                "allowed_output_path": output_path,
                "original_output_sha256": self._file_hash(project_output),
                "staging_inventory": initial_inventory,
                "session": {
                    "policy": str(session.get("policy") or ""),
                    "action": str(session.get("action") or ""),
                    "scope": str(session.get("scope") or ""),
                    "resumed_session_id": resume_session_id,
                    "session_id": "",
                },
                "event_counts": {},
                "validation": {},
                "unexpected_files": [],
                "modified_input_files": [],
                "canonical_mutated": False,
                "auto_apply": False,
                "auto_finalize": False,
                "auto_close": False,
            }
            self._write_job(job_dir, record)
            thread = threading.Thread(
                target=self._run_job,
                args=(config, manifest, brief, record),
                name=f"codex-agent-{job_id}",
                daemon=True,
            )
            self._threads[job_id] = thread
            thread.start()
        return self.status(config, job_id)

    def status(self, config: ConfigDocument, job_id: str) -> dict[str, Any]:
        root = resolve_project_root(config)
        return self._read_job(self._job_dir(root, job_id))

    def wait(
        self,
        config: ConfigDocument,
        job_id: str,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise CodexAgentJobError("等待 Codex 任务超时")
        result = self.status(config, job_id)
        if result.get("status") not in TERMINAL_JOB_STATUSES:
            raise CodexAgentJobError("任务仍在其他 Studio 进程中运行")
        return result

    def cancel(self, config: ConfigDocument, job_id: str) -> dict[str, Any]:
        root = resolve_project_root(config)
        job_dir = self._job_dir(root, job_id)
        with self._lock:
            record = self._read_job(job_dir)
            if record.get("status") in TERMINAL_JOB_STATUSES:
                return record
            self._cancel_requested.add(job_id)
            record["status"] = "cancelled"
            record["cancelled_by"] = "human"
            self._write_job(job_dir, record)
            process = self._processes.get(job_id)
            if process is not None and process.poll() is None:
                process.terminate()
        return self._read_job(job_dir)

    def list_jobs(self, config: ConfigDocument) -> list[dict[str, Any]]:
        root = resolve_project_root(config)
        jobs_root = root / "70_runtime" / "agent_jobs"
        if not jobs_root.is_dir():
            return []
        values: list[dict[str, Any]] = []
        for path in sorted(jobs_root.glob("job_*/job.json"), reverse=True):
            try:
                values.append(self._read_job(path.parent))
            except CodexAgentJobError:
                continue
        return values

    def _run_job(
        self,
        config: ConfigDocument,
        manifest: dict[str, Any],
        brief: dict[str, Any],
        initial_record: dict[str, Any],
    ) -> None:
        root = resolve_project_root(config)
        job_id = str(initial_record["job_id"])
        job_dir = self._job_dir(root, job_id)
        staging = job_dir / "staging"
        try:
            with self._lock:
                record = self._read_job(job_dir)
                if job_id in self._cancel_requested or record.get("status") == "cancelled":
                    return
                record["status"] = "running"
                command = self._build_command(staging, record)
                process = subprocess.Popen(
                    command,
                    cwd=staging,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                )
                self._processes[job_id] = process
                record["pid"] = process.pid
                self._write_job(job_dir, record)
                if job_id in self._cancel_requested:
                    process.terminate()
            stdout, stderr = process.communicate(self._prompt(brief, record))
            with self._lock:
                current = self._read_job(job_dir)
                if current.get("status") == "cancelled" or job_id in self._cancel_requested:
                    return
            event_counts, session_id = self._event_summary(stdout)
            record = self._read_job(job_dir)
            record["event_counts"] = event_counts
            record["session"]["session_id"] = session_id
            record["exit_code"] = process.returncode
            if process.returncode != 0:
                record["status"] = "failed"
                record["error"] = self._safe_error(stderr, process.returncode)
                self._write_job(job_dir, record)
                return

            output_path = str(record["allowed_output_path"])
            after_inventory = self._inventory(staging)
            before_inventory = dict(record.get("staging_inventory") or {})
            allowed = {*before_inventory, output_path}
            unexpected = sorted(set(after_inventory) - allowed)
            modified_inputs = sorted(
                path
                for path, digest in before_inventory.items()
                if after_inventory.get(path) != digest
            )
            if unexpected or modified_inputs:
                record["status"] = "rejected_output_boundary"
                record["unexpected_files"] = unexpected
                record["modified_input_files"] = modified_inputs
                self._write_job(job_dir, record)
                return
            staged_output = resolve_under_root(staging, output_path)
            if not staged_output.is_file():
                record["status"] = "failed_validation"
                record["error"] = "Codex 未生成 manifest 声明的唯一输出文件"
                self._write_job(job_dir, record)
                return
            try:
                rendered = staged_output.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                record["status"] = "failed_validation"
                record["error"] = "Codex 输出不是有效 UTF-8"
                self._write_job(job_dir, record)
                return

            project_output = resolve_under_root(root, output_path)
            with acquire_project_lock(
                config, owner="workspace-studio", command="agent-job result-validate"
            ):
                if self._file_hash(project_output) != str(record["original_output_sha256"]):
                    raise CodexAgentJobError("Agent 输出目标在任务运行期间被其他写入者修改")
                atomic_write_text(project_output, rendered)
                validation = validate_production_agent_result(
                    root,
                    manifest,
                    result_file=output_path,
                )
            record["validation"] = asdict(validation)
            record["result_sha256"] = self._file_hash(project_output)
            record["status"] = "completed" if validation.ok else "failed_validation"
            if session_id:
                self._remember_session(root, record["session"], session_id)
            self._write_job(job_dir, record)
        except Exception as exc:  # noqa: BLE001 - background boundary must persist failure
            try:
                record = self._read_job(job_dir)
                if record.get("status") != "cancelled":
                    record["status"] = "failed"
                    record["error"] = str(exc)
                    self._write_job(job_dir, record)
            except Exception:
                pass
        finally:
            with self._lock:
                self._processes.pop(job_id, None)
                self._cancel_requested.discard(job_id)

    def _stage_inputs(
        self,
        root: Path,
        staging: Path,
        manifest: dict[str, Any],
        brief: dict[str, Any],
    ) -> list[str]:
        paths = [*manifest_input_paths(manifest), str(brief.get("manifest_file") or "")]
        result: list[str] = []
        for relative in paths:
            normalized = relative.replace("\\", "/")
            if not normalized or normalized in result:
                continue
            source = resolve_under_root(root, normalized)
            if not source.is_file() or source.is_symlink():
                raise CodexAgentJobError(f"manifest 输入不是普通文件：{normalized}")
            target = resolve_under_root(staging, normalized)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            result.append(normalized)
        return result

    def _build_command(self, staging: Path, record: dict[str, Any]) -> list[str]:
        command = [
            *self.codex_command,
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "-C",
            str(staging),
            "--skip-git-repo-check",
        ]
        resume = str((record.get("session") or {}).get("resumed_session_id") or "")
        if resume:
            command.extend(("resume", resume, "-"))
        else:
            command.append("-")
        return command

    @staticmethod
    def _prompt(brief: dict[str, Any], record: dict[str, Any]) -> str:
        inputs = "\n".join(f"- {path}" for path in record["declared_input_files"])
        return "\n".join(
            [
                f"ONLY_OUTPUT_PATH={record['allowed_output_path']}",
                "Use the installed longform-novel-codex skill for this bounded task.",
                "This directory is an isolated staging mirror, not the canonical project.",
                "Read only the files listed below and write only ONLY_OUTPUT_PATH.",
                "Do not create notes, logs, helper files, final chapters, Canon, RAG, graph, or SQLite.",
                "Do not run apply, finalize, semantic-apply, close, or arbitrary longform-engine commands.",
                "Declared inputs:",
                inputs,
                "",
                str(brief.get("work_order_markdown") or ""),
            ]
        )

    def _active_job(self, root: Path) -> dict[str, Any] | None:
        jobs_root = root / "70_runtime" / "agent_jobs"
        if not jobs_root.is_dir():
            return None
        for path in jobs_root.glob("job_*/job.json"):
            try:
                record = self._read_job(path.parent)
            except CodexAgentJobError:
                continue
            if record.get("status") in ACTIVE_JOB_STATUSES:
                return record
        return None

    def _resume_session_id(self, root: Path, session: dict[str, Any]) -> str:
        if str(session.get("action") or "") not in {
            "continue_project_session",
            "continue_chapter_session",
        }:
            return ""
        registry = self._read_json(root / "70_runtime" / "agent_jobs" / "sessions.json")
        raw_sessions = registry.get("sessions")
        sessions: dict[str, Any] = dict(raw_sessions) if isinstance(raw_sessions, dict) else {}
        return str(sessions.get(str(session.get("scope") or "")) or "")

    def _remember_session(self, root: Path, session: dict[str, Any], session_id: str) -> None:
        if str(session.get("policy") or "") not in {"project_coordinator", "chapter_author"}:
            return
        path = root / "70_runtime" / "agent_jobs" / "sessions.json"
        registry = self._read_json(path)
        raw_sessions = registry.get("sessions")
        sessions: dict[str, Any] = dict(raw_sessions) if isinstance(raw_sessions, dict) else {}
        sessions[str(session.get("scope") or "")] = session_id
        atomic_write_text(
            path,
            json.dumps(
                {"schema": "codex_agent_session_registry_v1", "sessions": sessions},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )

    @staticmethod
    def _event_summary(stdout: str) -> tuple[dict[str, int], str]:
        counts: dict[str, int] = {}
        session_id = ""
        for line in stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                counts["non_json"] = counts.get("non_json", 0) + 1
                continue
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "unknown")
            counts[event_type] = counts.get(event_type, 0) + 1
            candidate = event.get("thread_id") or event.get("session_id")
            if isinstance(candidate, str) and candidate:
                session_id = candidate
        return counts, session_id

    @staticmethod
    def _inventory(root: Path) -> dict[str, str]:
        return {
            path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }

    @staticmethod
    def _file_hash(path: Path) -> str:
        return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""

    @staticmethod
    def _safe_error(stderr: str, return_code: int) -> str:
        normalized = " ".join(stderr.strip().split())
        if len(normalized) > 1000:
            normalized = normalized[:1000] + "…"
        return normalized or f"Codex process exited with code {return_code}"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _job_dir(root: Path, job_id: str) -> Path:
        if not re_fullmatch_job_id(job_id):
            raise CodexAgentJobError("Codex 任务 ID 无效")
        return root / "70_runtime" / "agent_jobs" / job_id

    def _read_job(self, job_dir: Path) -> dict[str, Any]:
        payload = self._read_json(job_dir / "job.json")
        if payload.get("schema") != "codex_agent_job_v1" or payload.get("job_id") != job_dir.name:
            raise CodexAgentJobError("Codex 任务不存在或状态文件无效")
        return payload

    @staticmethod
    def _write_job(job_dir: Path, record: dict[str, Any]) -> None:
        atomic_write_text(
            job_dir / "job.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        )


def re_fullmatch_job_id(value: str) -> bool:
    return len(value) == 28 and value.startswith("job_") and all(
        char in "0123456789abcdef" for char in value[4:]
    )


__all__ = ["CodexAgentJobError", "CodexAgentJobManager"]
