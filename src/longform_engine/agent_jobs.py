"""Local Codex subprocess bridge constrained by AgentTaskManifest v5."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence
import json
import os
import re
import secrets
import signal
import shutil
import subprocess
import threading
import tempfile

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
        executable = os.environ.get("LONGFORM_CODEX_EXECUTABLE") or shutil.which("codex") or "codex"
        resolved = tuple(str(part) for part in (codex_command or (executable,)))
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
        # Serialize admission with worker cleanup and re-read the manifest only
        # after admission. A pre-lock snapshot can still say awaiting_agent after
        # the preceding worker has submitted its result.
        with self._lock:
            if any(worker.is_alive() for worker in self._threads.values()):
                raise CodexAgentJobError("工作台已有运行中的 Codex 任务，请等待结束后再启动")
            return self._start_admitted(config, root, task)

    def _start_admitted(
        self, config: ConfigDocument, root: Path, task: str | Path
    ) -> dict[str, Any]:
        """Create the isolated job while its manager admission lock is held."""
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
            # Keep the isolated CLI working directory short on Windows. Nesting
            # mirrored manifest paths below an already deep project exceeds the
            # path limit of several supported CLI runtimes.
            staging = Path(tempfile.mkdtemp(prefix="longform-job-")).resolve()
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
                "created_at": datetime.now(timezone.utc).isoformat(),
                "staging_directory": str(staging),
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
        with self._lock:
            record = self._read_job(self._job_dir(root, job_id))
            worker = self._threads.get(job_id)
            if worker is not None and worker.is_alive() and record.get("status") in TERMINAL_JOB_STATUSES:
                # Terminal output may be durable before thread-owned resources
                # are released. Keep polling until a subsequent job can start.
                record["status"] = "cancelling" if job_id in self._cancel_requested else "running"
                record["phase"] = "finishing"
            return record

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
            record["status"] = "cancelling"
            record["cancelled_by"] = "human"
            self._write_job(job_dir, record)
            process = self._processes.get(job_id)
            if process is not None and process.poll() is None:
                try:
                    self._terminate_process(process)
                except (OSError, subprocess.SubprocessError, CodexAgentJobError) as exc:
                    self._cancel_requested.discard(job_id)
                    record["status"] = "running"
                    record["error"] = f"取消未完成：{exc}"
                    self._write_job(job_dir, record)
                    raise CodexAgentJobError(record["error"]) from exc
        return self._read_job(job_dir)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        """Stop the owned job's process tree so inherited pipes cannot hang cancellation."""
        if process.poll() is not None:
            return
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode and process.poll() is None:
                raise CodexAgentJobError("无法结束本工单的进程树，请稍后重试取消")
        elif hasattr(os, "killpg"):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        else:
            raise CodexAgentJobError("当前系统不支持取消本工单进程组")

    def list_jobs(self, config: ConfigDocument) -> list[dict[str, Any]]:
        root = resolve_project_root(config)
        jobs_root = root / "70_runtime" / "agent_jobs"
        if not jobs_root.is_dir():
            return []
        values: list[dict[str, Any]] = []
        for path in sorted(jobs_root.glob("job_*/job.json"), reverse=True):
            try:
                values.append(self.status(config, path.parent.name))
            except CodexAgentJobError:
                continue
        return sorted(values, key=lambda record: str(record.get("created_at") or ""), reverse=True)

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
        staging = Path(str(initial_record["staging_directory"]))
        error_stream = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
        try:
            with self._lock:
                record = self._read_job(job_dir)
                if job_id in self._cancel_requested or record.get("status") == "cancelled":
                    record["status"] = "cancelled"
                    self._write_job(job_dir, record)
                    return
                record["status"] = "running"
                command = self._build_command(staging, record)
                process = subprocess.Popen(
                    command,
                    cwd=staging,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=error_stream,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                self._processes[job_id] = process
                record["pid"] = process.pid
                self._write_job(job_dir, record)
                if job_id in self._cancel_requested:
                    self._terminate_process(process)
            if process.stdin is None or process.stdout is None:
                raise CodexAgentJobError("Codex pipes are unavailable")
            process.stdin.write(self._prompt(brief, record))
            process.stdin.close()
            event_counts: dict[str, int] = {}
            session_id = ""
            public_events: list[dict[str, Any]] = []
            sequence = 0
            terminal_error = ""
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                kind = str(event.get("type") or "unknown")
                event_counts[kind] = event_counts.get(kind, 0) + 1
                if kind in {"error", "turn.failed"}:
                    error = event.get("error")
                    message = error.get("message") if isinstance(error, dict) else event.get("message") or error
                    if isinstance(message, str) and message:
                        terminal_error = message
                if kind == "thread.started":
                    session_id = str(event.get("thread_id") or "")
                # Expose progress and assistant-facing messages only. Never persist
                # shell commands, tool output, reasoning, credentials or raw prompts.
                raw_item = event.get("item")
                item: dict[str, Any] = raw_item if isinstance(raw_item, dict) else {}
                if kind in {"turn.started", "turn.completed", "turn.failed"} or (
                    kind in {"item.completed", "item.updated"} and item.get("type") == "agent_message"
                ):
                    sequence += 1
                    public_events.append({"cursor": sequence, "type": kind,
                                          "text": str(item.get("text") or "")[:12_000],
                                          "validated": False})
                    public_events = public_events[-100:]
                    with self._lock:
                        progress = self._read_job(job_dir)
                        progress.update({"events": public_events, "event_cursor": sequence, "event_counts": event_counts})
                        self._write_job(job_dir, progress)
            process.wait()
            error_stream.seek(0)
            stderr = error_stream.read(12_000)
            with self._lock:
                current = self._read_job(job_dir)
                if current.get("status") == "cancelled" or job_id in self._cancel_requested:
                    return
            record = self._read_job(job_dir)
            record["event_counts"] = event_counts
            record["session"]["session_id"] = session_id
            record["exit_code"] = process.returncode
            if process.returncode != 0:
                record["status"] = "failed"
                record["error"] = self._safe_error(terminal_error or stderr, process.returncode)
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
            if not validation.ok:
                reasons = [*validation.normalization.errors, *validation.normalization.need_human_reasons]
                if "canonical_delta_contains_uncertainties" in reasons:
                    reasons = ["设计仍有未解决的编译问题" if item == "canonical_delta_contains_uncertainties" else item for item in reasons]
                    reasons.extend(str(item)[:600] for item in validation.normalization.normalized_result.get("notes", [])[:3])
                record["error"] = "；".join(reasons)[:3000] or "输出证据不完整，请查看当前校验诊断。"
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
            error_stream.close()
            with self._lock:
                if job_id in self._cancel_requested:
                    cancelled = self._read_job(job_dir)
                    cancelled["status"] = "cancelled"
                    self._write_job(job_dir, cancelled)
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
                "Use the longform-novel-codex workflow. The current work order and output template below are authoritative over older installed skill examples.",
                "This directory is an isolated staging mirror, not the canonical project.",
                "The control plane has already prepared the current work order. In this staging transport, do not run any longform-engine command, including production next, agent-task brief, or validation commands printed in the handoff.",
                "The server runs output validation after this job exits. project.yaml is intentionally absent unless it is a declared input; its absence here is not missing story evidence.",
                "Read only the files listed below and write only ONLY_OUTPUT_PATH.",
                "Work as a single agent. Do not spawn subagents, delegate tasks, start other model jobs, or parallelize agent execution.",
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
        normalized = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", stderr)
        normalized = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[redacted]", normalized)
        normalized = " ".join(normalized.strip().split())
        if len(normalized) > 1000:
            normalized = "…" + normalized[-1000:]
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
        now = datetime.now(timezone.utc).isoformat()
        record["updated_at"] = now
        if record.get("status") == "running":
            record.setdefault("started_at", now)
        elif record.get("status") in TERMINAL_JOB_STATUSES:
            record.setdefault("finished_at", now)
        atomic_write_text(
            job_dir / "job.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n"
        )


def re_fullmatch_job_id(value: str) -> bool:
    return len(value) == 28 and value.startswith("job_") and all(
        char in "0123456789abcdef" for char in value[4:]
    )


__all__ = ["CodexAgentJobError", "CodexAgentJobManager"]
