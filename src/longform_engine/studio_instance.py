"""Single-instance lifecycle for one local Workspace Studio root."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import os
from pathlib import Path
from typing import Any

from longform_engine.storage import atomic_write_text


INSTANCE_SCHEMA = "novel_workspace_studio_instance_v1"


class StudioInstanceError(ValueError):
    """Raised when a workspace Studio process already owns the lifecycle lock."""


@dataclass
class WorkspaceStudioInstance:
    """Own one workspace lock and publish its loopback launcher endpoint."""

    workspace: Path

    def __post_init__(self) -> None:
        self.workspace = self.workspace.resolve()
        self.lock_path = self.workspace / ".longform-studio-instance.lock"
        self.metadata_path = self.workspace / ".longform-studio-instance.json"
        self._owns_lock = False
        self._launcher_secret = ""

    def request_bootstrap(self, path: str) -> str | None:
        """Ask a live instance for a new one-use browser bootstrap URL."""

        metadata = self._read_json(self.metadata_path)
        if (
            metadata.get("schema") != INSTANCE_SCHEMA
            or metadata.get("workspace") != str(self.workspace)
            or not isinstance(metadata.get("port"), int)
            or isinstance(metadata.get("port"), bool)
            or not 0 < int(metadata["port"]) <= 65535
            or not isinstance(metadata.get("launcher_secret"), str)
            or not metadata["launcher_secret"]
        ):
            return None
        body = json.dumps({"path": path}, ensure_ascii=False).encode("utf-8")
        connection = http.client.HTTPConnection(
            "127.0.0.1", int(metadata["port"]), timeout=2
        )
        try:
            connection.request(
                "POST",
                "/api/instance/bootstrap",
                body=body,
                headers={
                    "Host": f"127.0.0.1:{metadata['port']}",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "X-Studio-Launcher": str(metadata["launcher_secret"]),
                },
            )
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException):
            return None
        finally:
            connection.close()
        if response.status != 200:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return None
        url = payload.get("bootstrap_url") if isinstance(payload, dict) else None
        return str(url) if isinstance(url, str) and url.startswith("http://127.0.0.1:") else None

    def acquire(self) -> None:
        """Acquire an exclusive workspace lifecycle lock, cleaning only confirmed stale state."""

        if self._owns_lock:
            raise StudioInstanceError("workspace Studio lifecycle lock is already owned")
        if self.lock_path.exists():
            lock = self._read_json(self.lock_path)
            pid = lock.get("pid")
            if isinstance(pid, int) and not isinstance(pid, bool) and self._process_exists(pid):
                raise StudioInstanceError("another Workspace Studio process is starting or running")
            self.lock_path.unlink(missing_ok=True)
            self.metadata_path.unlink(missing_ok=True)
        try:
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise StudioInstanceError("another Workspace Studio process won the startup race") from exc
        try:
            os.write(
                descriptor,
                json.dumps(
                    {"schema": INSTANCE_SCHEMA, "pid": os.getpid()},
                    ensure_ascii=False,
                ).encode("utf-8"),
            )
        finally:
            os.close(descriptor)
        self._owns_lock = True

    def publish(self, *, port: int, launcher_secret: str) -> None:
        if not self._owns_lock:
            raise StudioInstanceError("cannot publish a Studio instance without its lock")
        if not 0 < port <= 65535 or not launcher_secret:
            raise StudioInstanceError("Studio instance metadata is invalid")
        self._launcher_secret = launcher_secret
        atomic_write_text(
            self.metadata_path,
            json.dumps(
                {
                    "schema": INSTANCE_SCHEMA,
                    "workspace": str(self.workspace),
                    "pid": os.getpid(),
                    "port": port,
                    "launcher_secret": launcher_secret,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )

    def release(self) -> None:
        if not self._owns_lock:
            return
        metadata = self._read_json(self.metadata_path)
        if (
            metadata.get("pid") == os.getpid()
            and metadata.get("launcher_secret") == self._launcher_secret
        ):
            self.metadata_path.unlink(missing_ok=True)
        lock = self._read_json(self.lock_path)
        if lock.get("pid") == os.getpid():
            self.lock_path.unlink(missing_ok=True)
        self._owns_lock = False
        self._launcher_secret = ""

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _process_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            import ctypes

            process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False
            ctypes.windll.kernel32.CloseHandle(process)
            return True
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True


__all__ = ["StudioInstanceError", "WorkspaceStudioInstance"]
