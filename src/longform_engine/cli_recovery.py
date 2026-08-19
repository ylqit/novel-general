"""Recovery command surface owned by the crash-recovery subsystem."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from longform_engine.config import load_project_config
from longform_engine.storage import (
    cleanup_committed_transaction,
    discard_preparing_transaction,
    reclaim_project_lock,
    recovery_status,
    rollback_prepared_transaction,
)


def register_recovery_commands(subparsers: argparse._SubParsersAction) -> None:
    recovery = subparsers.add_parser(
        "recovery",
        help="Inspect and explicitly recover interrupted project storage lifecycles.",
    )
    commands = recovery.add_subparsers(dest="recovery_command", required=True)

    status = commands.add_parser("status", help="Inspect project lock and transaction recovery state.")
    status.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    status.add_argument("--json", action="store_true", help="Print machine-readable recovery_status_v1 JSON.")
    status.set_defaults(func=cmd_recovery_status)

    reclaim = commands.add_parser("reclaim-lock", help="Reclaim one confirmed-dead project lock.")
    reclaim.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    reclaim.add_argument("--expected-sha256", required=True, help="Exact lock hash returned by recovery status.")
    reclaim.add_argument("--approved-by", required=True, help="Human recovery approver.")
    reclaim.add_argument("--json", action="store_true", help="Print the recovery audit report.")
    reclaim.set_defaults(func=cmd_recovery_reclaim_lock, recovery_bypasses_project_lock=True)

    rollback = commands.add_parser(
        "rollback-transaction",
        help="Rollback one prepared transaction from its durable inventory.",
    )
    rollback.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    rollback.add_argument("--report", required=True, help="Transaction report under 70_runtime/transactions.")
    rollback.add_argument("--expected-sha256", required=True, help="Exact report hash returned by recovery status.")
    rollback.add_argument("--approved-by", required=True, help="Human recovery approver.")
    rollback.add_argument("--json", action="store_true", help="Print the recovery audit report.")
    rollback.set_defaults(func=cmd_recovery_rollback_transaction, mutates_project=True)

    discard = commands.add_parser(
        "discard-preparing",
        help="Discard snapshots from a transaction that never reached the prepared mutation boundary.",
    )
    discard.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    discard.add_argument("--report", required=True, help="Transaction report under 70_runtime/transactions.")
    discard.add_argument("--expected-sha256", required=True, help="Exact report hash returned by recovery status.")
    discard.add_argument("--approved-by", required=True, help="Human recovery approver.")
    discard.add_argument("--json", action="store_true", help="Print the recovery audit report.")
    discard.set_defaults(func=cmd_recovery_discard_preparing, mutates_project=True)

    cleanup = commands.add_parser(
        "cleanup-committed",
        help="Clean retained snapshots for an already committed transaction.",
    )
    cleanup.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    cleanup.add_argument("--report", required=True, help="Transaction report under 70_runtime/transactions.")
    cleanup.add_argument("--expected-sha256", required=True, help="Exact report hash returned by recovery status.")
    cleanup.add_argument("--approved-by", required=True, help="Human recovery approver.")
    cleanup.add_argument("--json", action="store_true", help="Print the recovery audit report.")
    cleanup.set_defaults(func=cmd_recovery_cleanup_committed, mutates_project=True)


def cmd_recovery_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = recovery_status(config)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("BLOCKED: project recovery required" if payload["blocked"] else "OK: no project recovery required")
        print(f"Lock: {payload['lock']['state']}")
        for item in payload["transactions"]:
            if item["state"] != "terminal":
                print(f"Transaction: {item['state']} {item['path']} ({item['reason']})")
        print(f"Next command: {payload['next_command'] or 'none'}")
    return 2 if payload["blocked"] else 0


def cmd_recovery_reclaim_lock(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = reclaim_project_lock(
        config,
        expected_sha256=args.expected_sha256,
        approved_by=args.approved_by,
    )
    print(
        json.dumps(payload, ensure_ascii=False, indent=2)
        if args.json
        else f"OK: stale lock reclaimed\nReport: {payload['report_file']}"
    )
    return 0


def cmd_recovery_rollback_transaction(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = rollback_prepared_transaction(
        config,
        report=args.report,
        expected_sha256=args.expected_sha256,
        approved_by=args.approved_by,
    )
    print(
        json.dumps(payload, ensure_ascii=False, indent=2)
        if args.json
        else f"OK: transaction rolled back\nReport: {payload['report_file']}"
    )
    return 0


def cmd_recovery_discard_preparing(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = discard_preparing_transaction(
        config,
        report=args.report,
        expected_sha256=args.expected_sha256,
        approved_by=args.approved_by,
    )
    print(
        json.dumps(payload, ensure_ascii=False, indent=2)
        if args.json
        else f"OK: preparing transaction discarded\nReport: {payload['report_file']}"
    )
    return 0


def cmd_recovery_cleanup_committed(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = cleanup_committed_transaction(
        config,
        report=args.report,
        expected_sha256=args.expected_sha256,
        approved_by=args.approved_by,
    )
    print(
        json.dumps(payload, ensure_ascii=False, indent=2)
        if args.json
        else f"OK: committed snapshots cleaned\nReport: {payload['report_file']}"
    )
    return 0
