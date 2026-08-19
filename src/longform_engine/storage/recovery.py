"""Read-only recovery diagnostics and explicitly approved storage repair actions."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import os
import socket
import sqlite3

from longform_engine.config import ConfigDocument

from .project import (
    StorageError,
    acquire_named_lock,
    atomic_write_text,
    cleanup_transaction_snapshot,
    partition_transaction_paths,
    process_start_identity,
    project_relative_path,
    resolve_project_transaction_path,
    resolve_project_root,
    restore_sqlite_database,
    restore_transaction_path,
)


RECOVERY_STATUS_SCHEMA = "recovery_status_v1"
LOCK_RECOVERY_REPORT_SCHEMA = "lock_recovery_report_v1"
TRANSACTION_SCHEMA = "canonical_write_transaction_report_v3"


def recovery_status(config: ConfigDocument) -> dict[str, Any]:
    root = resolve_project_root(config)
    lock = inspect_project_lock(root)
    transactions = inspect_transactions(root)
    blockers = transaction_blockers(transactions)
    if lock["state"] in {"active", "confirmed_dead", "unknown", "invalid"}:
        blockers.insert(0, f"project_lock:{lock['state']}")
    next_command = recovery_next_command(lock, transactions)
    return {
        "schema": RECOVERY_STATUS_SCHEMA,
        "root": str(root),
        "lock": lock,
        "transactions": transactions,
        "blocked": bool(blockers),
        "blockers": blockers,
        "next_command": next_command,
    }


def inspect_project_lock(root: Path) -> dict[str, Any]:
    path = root / "70_runtime" / "locks" / "project.lock"
    if not path.is_file():
        return {"state": "absent", "path": project_relative_path(root, path), "sha256": ""}
    digest = sha256(path.read_bytes()).hexdigest()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "state": "invalid",
            "path": project_relative_path(root, path),
            "sha256": digest,
            "reason": "lock_payload_invalid",
        }
    if not isinstance(payload, dict) or payload.get("schema") != "project_lock_v2":
        return {
            "state": "unknown",
            "path": project_relative_path(root, path),
            "sha256": digest,
            "reason": "lock_schema_not_current",
            "metadata": payload if isinstance(payload, dict) else {},
        }
    common = {
        "path": project_relative_path(root, path),
        "sha256": digest,
        "metadata": payload,
    }
    if str(payload.get("root") or "") != str(root):
        return {"state": "invalid", **common, "reason": "lock_root_mismatch"}
    if str(payload.get("hostname") or "") != socket.gethostname():
        return {"state": "unknown", **common, "reason": "lock_owner_is_remote_or_unknown"}
    try:
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError):
        return {"state": "invalid", **common, "reason": "lock_pid_invalid"}
    if pid <= 0:
        return {"state": "invalid", **common, "reason": "lock_pid_invalid"}
    alive = process_is_alive(pid)
    if alive is False:
        return {"state": "confirmed_dead", **common, "reason": "owner_process_not_alive"}
    actual_identity = process_start_identity(pid)
    expected_identity = str(payload.get("process_identity") or "")
    if alive and pid == os.getpid():
        return {"state": "active_current", **common, "reason": "owner_is_current_process"}
    if alive and actual_identity and expected_identity == actual_identity:
        state = "active_current" if pid == os.getpid() else "active"
        return {"state": state, **common, "reason": "owner_process_alive"}
    if alive and actual_identity and expected_identity and expected_identity != actual_identity:
        return {"state": "confirmed_dead", **common, "reason": "pid_reused"}
    return {"state": "unknown", **common, "reason": "process_identity_unavailable"}


def inspect_transactions(root: Path) -> list[dict[str, Any]]:
    report_dir = root / "70_runtime" / "transactions"
    result: list[dict[str, Any]] = []
    for path in sorted(report_dir.glob("*.json")) if report_dir.is_dir() else []:
        if path.name.endswith(".rollback.json"):
            continue
        result.append(inspect_transaction(root, path))
    return result


def inspect_transaction(root: Path, path: Path) -> dict[str, Any]:
    digest = sha256(path.read_bytes()).hexdigest()
    base = {"path": project_relative_path(root, path), "sha256": digest}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {**base, "state": "need_human", "reason": "transaction_report_invalid"}
    if not isinstance(payload, dict):
        return {**base, "state": "need_human", "reason": "transaction_report_invalid"}
    schema = str(payload.get("schema") or "")
    status = str(payload.get("status") or "")
    common = {**base, "schema": schema, "status": status, "command": str(payload.get("command") or "")}
    if schema != TRANSACTION_SCHEMA:
        if status in {"pending", "preparing", "prepared"}:
            return {**common, "state": "need_human", "reason": "untrusted_transaction_inventory"}
        return {**common, "state": "terminal", "reason": "retired_transaction_terminal"}
    if status == "preparing":
        snapshot_dir = transaction_snapshot_dir(root, payload)
        recoverable = snapshot_dir is not None and snapshot_dir.is_dir()
        return {
            **common,
            "state": "recoverable_discard" if recoverable else "need_human",
            "reason": (
                "transaction_never_reached_prepared_state"
                if recoverable
                else "transaction_preparation_snapshot_missing_or_unsafe"
            ),
        }
    if status == "applied" and payload.get("cleanup_complete") is not True:
        snapshot_dir = transaction_snapshot_dir(root, payload)
        return {
            **common,
            "state": "recoverable_cleanup" if snapshot_dir is not None else "need_human",
            "reason": (
                "committed_snapshot_cleanup_required"
                if snapshot_dir is not None
                else "transaction_snapshot_path_unsafe"
            ),
        }
    if status == "recovery_failed" or (
        status == "rolled_back" and payload.get("cleanup_complete") is not True
    ):
        return {
            **common,
            "state": "need_human",
            "reason": "transaction_recovery_failed",
        }
    if status in {"applied", "rolled_back", "aborted_before_apply"}:
        return {**common, "state": "terminal", "reason": "transaction_complete"}
    inventory_error = validate_transaction_inventory(root, payload)
    if status == "prepared":
        return {
            **common,
            "state": "need_human" if inventory_error else "recoverable_rollback",
            "reason": inventory_error or "prepared_transaction_requires_rollback",
        }
    return {**common, "state": "need_human", "reason": "transaction_status_unknown"}


def validate_transaction_inventory(root: Path, payload: dict[str, Any]) -> str:
    snapshot_dir = transaction_snapshot_dir(root, payload)
    if snapshot_dir is None or not snapshot_dir.is_dir():
        return "transaction_snapshot_missing_or_unsafe"
    touched = payload.get("touched_paths")
    targets = payload.get("inventory_targets")
    snapshots = payload.get("snapshots")
    databases = payload.get("sqlite_backups")
    if (
        not isinstance(touched, list)
        or not isinstance(targets, dict)
        or not isinstance(snapshots, list)
        or not isinstance(databases, list)
    ):
        return "transaction_inventory_invalid"
    filesystem_targets = targets.get("filesystem")
    sqlite_targets = targets.get("sqlite")
    if not isinstance(filesystem_targets, list) or not isinstance(sqlite_targets, list):
        return "transaction_inventory_targets_invalid"
    if any(not isinstance(item, str) or not item for item in [*filesystem_targets, *sqlite_targets]):
        return "transaction_inventory_targets_invalid"
    if len(filesystem_targets) != len(set(filesystem_targets)) or len(sqlite_targets) != len(set(sqlite_targets)):
        return "transaction_inventory_targets_duplicated"
    try:
        resolved_touched = [resolve_project_transaction_path(root, item) for item in touched]
        expected_filesystem, expected_sqlite = partition_transaction_paths(root, resolved_touched)
    except (OSError, StorageError, ValueError):
        return "transaction_touched_paths_invalid"
    expected_filesystem_paths = {
        project_relative_path(root, path) for path in expected_filesystem
    }
    expected_sqlite_paths = {project_relative_path(root, path) for path in expected_sqlite}
    if set(filesystem_targets) != expected_filesystem_paths or set(sqlite_targets) != expected_sqlite_paths:
        return "transaction_inventory_targets_do_not_cover_touched_paths"
    if touched and not snapshots and not databases:
        return "transaction_inventory_empty"
    if (
        len(snapshots) != len(filesystem_targets)
        or {str(item.get("path") or "") for item in snapshots if isinstance(item, dict)}
        != set(filesystem_targets)
    ):
        return "transaction_filesystem_inventory_incomplete"
    if (
        len(databases) != len(sqlite_targets)
        or {str(item.get("path") or "") for item in databases if isinstance(item, dict)}
        != set(sqlite_targets)
    ):
        return "transaction_sqlite_inventory_incomplete"
    for item in snapshots:
        error = validate_inventory_item(
            root,
            snapshot_dir,
            item,
            "snapshot_path",
            expected_kinds={"missing", "file", "dir"},
        )
        if error:
            return error
    for item in databases:
        error = validate_inventory_item(
            root,
            snapshot_dir,
            item,
            "backup_path",
            expected_kinds={"sqlite_backup"},
        )
        if error:
            return error
    return ""


def validate_inventory_item(
    root: Path,
    snapshot_dir: Path,
    item: Any,
    storage_key: str,
    *,
    expected_kinds: set[str],
) -> str:
    if not isinstance(item, dict) or not str(item.get("path") or ""):
        return "transaction_inventory_item_invalid"
    if not isinstance(item.get("existed"), bool) or str(item.get("kind") or "") not in expected_kinds:
        return "transaction_inventory_item_invalid"
    try:
        (root / str(item["path"])).resolve().relative_to(root.resolve())
    except (KeyError, OSError, ValueError):
        return "transaction_target_escaped_project"
    storage_value = str(item.get(storage_key) or "")
    if item.get("existed") and not storage_value:
        return "transaction_snapshot_reference_missing"
    if storage_value:
        storage_path = (root / storage_value).resolve()
        try:
            storage_path.relative_to(snapshot_dir.resolve())
        except ValueError:
            return "transaction_snapshot_escaped_directory"
        if item.get("existed") and not storage_path.exists():
            return "transaction_snapshot_object_missing"
    return ""


def reclaim_project_lock(
    config: ConfigDocument,
    *,
    expected_sha256: str,
    approved_by: str,
) -> dict[str, Any]:
    root = resolve_project_root(config)
    approval = require_approval(approved_by)
    expected = require_sha256(expected_sha256)
    recovery_lock = acquire_named_lock(
        root,
        "recovery.lock",
        owner="longform-engine-recovery",
        command="recovery reclaim-lock",
    )
    with recovery_lock:
        lock = inspect_project_lock(root)
        if lock.get("sha256") != expected:
            raise StorageError("Project lock changed after recovery status; inspect it again.")
        if lock.get("state") != "confirmed_dead":
            raise StorageError(f"Project lock is not safely reclaimable: {lock.get('state')}")
        path = root / str(lock["path"])
        path.resolve().relative_to((root / "70_runtime" / "locks").resolve())
        path.unlink()
        return write_recovery_audit(
            root,
            action="reclaim_lock",
            approved_by=approval,
            subject=lock,
            result={"status": "reclaimed", "removed": project_relative_path(root, path)},
            schema=LOCK_RECOVERY_REPORT_SCHEMA,
        )


def rollback_prepared_transaction(
    config: ConfigDocument,
    *,
    report: str | Path,
    expected_sha256: str,
    approved_by: str,
) -> dict[str, Any]:
    root = resolve_project_root(config)
    approval = require_approval(approved_by)
    report_path = resolve_transaction_report(root, report)
    expected = require_sha256(expected_sha256)
    if sha256(report_path.read_bytes()).hexdigest() != expected:
        raise StorageError("Transaction report changed after recovery status; inspect it again.")
    status = inspect_transaction(root, report_path)
    if status.get("state") != "recoverable_rollback":
        raise StorageError(f"Transaction is not safely rollback-recoverable: {status.get('reason')}")
    payload = read_object(report_path)
    snapshot_dir = transaction_snapshot_dir(root, payload)
    if snapshot_dir is None:
        raise StorageError("Transaction snapshot directory is unsafe.")
    restored: list[str] = []
    restored_databases: list[str] = []
    errors: list[str] = []
    for item in reversed(payload["snapshots"]):
        try:
            restore_transaction_path(root, snapshot_dir, item)
            restored.append(str(item.get("path") or ""))
        except (OSError, StorageError) as exc:
            errors.append(f"{item.get('path')}: {exc}")
    for item in reversed(payload["sqlite_backups"]):
        try:
            restore_sqlite_database(root, snapshot_dir, item)
            restored_databases.append(str(item.get("path") or ""))
        except (OSError, sqlite3.Error, StorageError) as exc:
            errors.append(f"{item.get('path')}: {exc}")
    cleanup_errors = cleanup_transaction_snapshot(snapshot_dir) if not errors else []
    recovery_complete = not errors and not cleanup_errors
    payload.update(
        {
            "status": "rolled_back" if recovery_complete else "recovery_failed",
            "report_type": "canonical_write_transaction_rollback_v3",
            "restored_paths": restored,
            "restored_databases": restored_databases,
            "restore_errors": errors,
            "cleanup_errors": cleanup_errors,
            "cleanup_complete": recovery_complete,
            "snapshots_retained": bool(errors or cleanup_errors),
            "recovered_by": approval,
            "recovered_at": utc_now(),
        }
    )
    atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    rollback_path = report_path.with_name(f"{report_path.stem}.rollback.json")
    atomic_write_text(rollback_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    audit = write_recovery_audit(
        root,
        action="rollback_transaction",
        approved_by=approval,
        subject=status,
        result={
            "status": "rolled_back" if recovery_complete else "need_human",
            "report": project_relative_path(root, report_path),
            "restore_errors": errors,
            "cleanup_errors": cleanup_errors,
        },
        schema="transaction_recovery_report_v1",
    )
    if not recovery_complete:
        raise StorageError("Transaction rollback recovery was incomplete; snapshots were retained.")
    return audit


def discard_preparing_transaction(
    config: ConfigDocument,
    *,
    report: str | Path,
    expected_sha256: str,
    approved_by: str,
) -> dict[str, Any]:
    """Discard snapshots from a transaction that never exposed its mutation boundary."""

    root = resolve_project_root(config)
    approval = require_approval(approved_by)
    report_path = resolve_transaction_report(root, report)
    expected = require_sha256(expected_sha256)
    if sha256(report_path.read_bytes()).hexdigest() != expected:
        raise StorageError("Transaction report changed after recovery status; inspect it again.")
    status = inspect_transaction(root, report_path)
    if status.get("state") != "recoverable_discard":
        raise StorageError(f"Transaction is not safely discardable: {status.get('reason')}")
    payload = read_object(report_path)
    snapshot_dir = transaction_snapshot_dir(root, payload)
    if snapshot_dir is None:
        raise StorageError("Transaction snapshot directory is unsafe.")
    cleanup_errors = cleanup_transaction_snapshot(snapshot_dir)
    payload.update(
        {
            "status": "aborted_before_apply" if not cleanup_errors else "preparing",
            "cleanup_complete": not cleanup_errors,
            "snapshots_retained": bool(cleanup_errors),
            "cleanup_errors": cleanup_errors,
            "recovered_by": approval,
            "recovered_at": utc_now(),
        }
    )
    atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    audit = write_recovery_audit(
        root,
        action="discard_preparing_transaction",
        approved_by=approval,
        subject=status,
        result={
            "status": "discarded" if not cleanup_errors else "need_human",
            "report": project_relative_path(root, report_path),
            "cleanup_errors": cleanup_errors,
        },
        schema="transaction_recovery_report_v1",
    )
    if cleanup_errors:
        raise StorageError("Preparing transaction snapshot cleanup was incomplete.")
    return audit


def cleanup_committed_transaction(
    config: ConfigDocument,
    *,
    report: str | Path,
    expected_sha256: str,
    approved_by: str,
) -> dict[str, Any]:
    root = resolve_project_root(config)
    approval = require_approval(approved_by)
    report_path = resolve_transaction_report(root, report)
    expected = require_sha256(expected_sha256)
    if sha256(report_path.read_bytes()).hexdigest() != expected:
        raise StorageError("Transaction report changed after recovery status; inspect it again.")
    status = inspect_transaction(root, report_path)
    if status.get("state") != "recoverable_cleanup":
        raise StorageError(f"Transaction is not safely cleanup-recoverable: {status.get('reason')}")
    payload = read_object(report_path)
    snapshot_dir = transaction_snapshot_dir(root, payload)
    if snapshot_dir is None:
        raise StorageError("Transaction snapshot directory is unsafe.")
    cleanup_errors = cleanup_transaction_snapshot(snapshot_dir)
    payload.update(
        {
            "cleanup_complete": not cleanup_errors,
            "snapshots_retained": bool(cleanup_errors),
            "cleanup_errors": cleanup_errors,
            "cleanup_finished_at": utc_now(),
            "cleanup_approved_by": approval,
        }
    )
    atomic_write_text(report_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    audit = write_recovery_audit(
        root,
        action="cleanup_committed",
        approved_by=approval,
        subject=status,
        result={
            "status": "cleaned" if not cleanup_errors else "need_human",
            "report": project_relative_path(root, report_path),
            "cleanup_errors": cleanup_errors,
        },
        schema="transaction_recovery_report_v1",
    )
    if cleanup_errors:
        raise StorageError("Committed transaction snapshot cleanup was incomplete.")
    return audit


def recovery_next_command(lock: dict[str, Any], transactions: list[dict[str, Any]]) -> str:
    if lock.get("state") == "confirmed_dead":
        return (
            "longform-engine recovery reclaim-lock project.yaml "
            f"--expected-sha256 {lock['sha256']} --approved-by human"
        )
    if lock.get("state") in {"active", "unknown", "invalid"}:
        return "longform-engine recovery status project.yaml --json"
    for item in transactions:
        if item.get("state") == "recoverable_discard":
            return (
                "longform-engine recovery discard-preparing project.yaml "
                f"--report {item['path']} --expected-sha256 {item['sha256']} --approved-by human"
            )
        if item.get("state") == "recoverable_rollback":
            return (
                "longform-engine recovery rollback-transaction project.yaml "
                f"--report {item['path']} --expected-sha256 {item['sha256']} --approved-by human"
            )
        if item.get("state") == "recoverable_cleanup":
            return (
                "longform-engine recovery cleanup-committed project.yaml "
                f"--report {item['path']} --expected-sha256 {item['sha256']} --approved-by human"
            )
    return ""


def transaction_blockers(transactions: list[dict[str, Any]]) -> list[str]:
    return [
        f"transaction:{item.get('state')}:{item.get('path')}"
        for item in transactions
        if item.get("state") in {
            "recoverable_discard",
            "recoverable_rollback",
            "recoverable_cleanup",
            "need_human",
        }
    ]


def transaction_snapshot_dir(root: Path, payload: dict[str, Any]) -> Path | None:
    value = str(payload.get("snapshot_dir") or "")
    if not value:
        return None
    path = (root / value).resolve()
    expected_root = (root / "70_runtime" / "tx").resolve()
    try:
        path.relative_to(expected_root)
    except ValueError:
        return None
    return path


def resolve_transaction_report(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    report_root = (root / "70_runtime" / "transactions").resolve()
    try:
        resolved.relative_to(report_root)
    except ValueError as exc:
        raise StorageError("Recovery report must live under 70_runtime/transactions.") from exc
    if not resolved.is_file() or resolved.name.endswith(".rollback.json"):
        raise StorageError(f"Recovery transaction report does not exist: {value}")
    return resolved


def process_is_alive(pid: int) -> bool | None:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
            kernel32.GetExitCodeProcess.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel32.CloseHandle.restype = wintypes.BOOL
            process = kernel32.OpenProcess(0x1000, False, pid)
            if not process:
                return False if ctypes.get_last_error() == 87 else None
            try:
                exit_code = wintypes.DWORD()
                if not kernel32.GetExitCodeProcess(process, ctypes.byref(exit_code)):
                    return None
                return exit_code.value == 259
            finally:
                kernel32.CloseHandle(process)
        except (AttributeError, OSError, TypeError, ValueError):
            return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return None
    except OSError:
        return None
    return True


def require_approval(value: str) -> str:
    approval = str(value or "").strip()
    if not approval:
        raise StorageError("Recovery requires --approved-by.")
    return approval


def require_sha256(value: str) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise StorageError("Recovery requires an exact SHA-256 from recovery status.")
    return digest


def read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"Invalid recovery JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise StorageError(f"Recovery JSON must be an object: {path}")
    return payload


def write_recovery_audit(
    root: Path,
    *,
    action: str,
    approved_by: str,
    subject: dict[str, Any],
    result: dict[str, Any],
    schema: str,
) -> dict[str, Any]:
    payload = {
        "schema": schema,
        "action": action,
        "approved_by": approved_by,
        "subject": subject,
        "result": result,
        "created_at": utc_now(),
    }
    directory = root / "70_runtime" / "recovery"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{timestamp}_{action}.json"
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return {**payload, "report_file": project_relative_path(root, path)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
