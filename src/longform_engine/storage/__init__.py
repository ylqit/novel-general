"""Project storage layout and initialization."""

from .project import (
    ProjectInitResult,
    ProjectLock,
    SnapshotResult,
    StorageError,
    ApplyTransaction,
    TransactionReportResult,
    acquire_project_lock,
    apply_transaction,
    atomic_write_text,
    init_project,
    record_transaction_report,
    resolve_project_root,
    snapshot_project,
)

__all__ = [
    "ProjectInitResult",
    "ProjectLock",
    "SnapshotResult",
    "StorageError",
    "ApplyTransaction",
    "TransactionReportResult",
    "acquire_project_lock",
    "apply_transaction",
    "atomic_write_text",
    "init_project",
    "record_transaction_report",
    "resolve_project_root",
    "snapshot_project",
]
