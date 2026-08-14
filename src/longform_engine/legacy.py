"""Evidence-driven migration for projects finalized before chapter closure existed."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any
import json

from longform_engine.agent_tasks import list_manifests
from longform_engine.artifacts import (
    artifact_status,
    compact_artifacts,
    orphan_agent_task_artifacts,
    transaction_snapshot_diagnostics,
)
from longform_engine.config import ConfigDocument
from longform_engine.models import cache_kind, models_dir
from longform_engine.semantic import semantic_rebuild, semantic_task
from longform_engine.semantic.pipeline import verify_materialized_chapter
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root


STATUS_SCHEMA = "legacy_migration_status_v1"


def legacy_status(config: ConfigDocument, *, through: int | None = None) -> dict[str, Any]:
    root = resolve_project_root(config)
    finals = chapter_numbers(root / "40_manuscript" / "final", "*.md")
    gates = chapter_numbers(root / "50_workbench" / "gate_artifacts", "ch*/gate_result.json")
    ledgers = chapter_numbers(root / "30_state" / "semantic_ledger", "*.json")
    closures = chapter_numbers(root / "30_state" / "chapter_closures", "*.json")
    target = int(through if through is not None else max(finals, default=0))
    expected = set(range(1, target + 1))
    gate_valid = {chapter for chapter in gates if gate_is_valid(root, chapter)}
    ledger_valid = {chapter for chapter in ledgers if ledger_is_valid(root, chapter)}
    closure_valid = {chapter for chapter in closures if closure_is_valid(root, chapter)}
    active_tasks = [
        str(item.get("task_id") or "")
        for item in list_manifests(root)
        if int(item.get("chapter_number") or 0) <= target
        and str(item.get("status") or "") in {"awaiting_agent", "submitted", "validated", "invalid"}
    ]
    blockers: list[str] = []
    if target <= 0:
        blockers.append("no finalized chapters were found")
    missing_final = sorted(expected - finals)
    missing_gate = sorted(expected - gate_valid)
    missing_ledger = sorted(expected - ledger_valid)
    if missing_final:
        blockers.append("missing continuous final evidence: " + chapter_list(missing_final))
    if missing_gate:
        blockers.append("missing or blocking deterministic gate: " + chapter_list(missing_gate))
    if active_tasks:
        blockers.append("active Agent tasks remain: " + ", ".join(active_tasks[:5]))
    diagnostics = transaction_snapshot_diagnostics(root)
    artifact = artifact_status(config)
    backfillable = sorted((finals & expected) - ledger_valid)
    closable = sorted((finals & gate_valid & ledger_valid & expected) - closure_valid)
    compactable_through = max(0, continuous_through(closure_valid) - 2)
    if backfillable:
        next_command = f"longform-engine legacy backfill project.yaml --through {target}"
    elif blockers:
        next_command = "longform-engine legacy status project.yaml --json"
    elif closable:
        next_command = (
            f"longform-engine legacy compact project.yaml --through {target} "
            "--approved-by NAME --dry-run"
        )
    else:
        next_command = "longform-engine artifacts verify project.yaml"
    return {
        "schema": STATUS_SCHEMA,
        "project_root": str(root),
        "through": target,
        "ranges": {
            "final_continuous_through": continuous_through(finals),
            "gate_continuous_through": continuous_through(gate_valid),
            "semantic_ledger_continuous_through": continuous_through(ledger_valid),
            "closure_continuous_through": continuous_through(closure_valid),
        },
        "missing_evidence": {
            "final": missing_final,
            "gate": missing_gate,
            "semantic_ledger": missing_ledger,
            "closure": sorted(expected - closure_valid),
        },
        "backfillable_chapters": backfillable,
        "closable_chapters": closable,
        "compactable_through": compactable_through,
        "active_tasks": active_tasks,
        "orphan_task_files": [str(path.relative_to(root)).replace("\\", "/") for path in orphan_agent_task_artifacts(root)],
        "transactions": diagnostics,
        "legacy_models": {
            "kind": cache_kind(config),
            "path": str(models_dir(config)),
            "bytes": directory_size(models_dir(config)) if cache_kind(config) == "legacy_project" else 0,
        },
        "artifact_summary": {
            "archive_files": artifact.archive_files,
            "loose_files": artifact.loose_files,
            "loose_bytes": artifact.loose_bytes,
        },
        "blockers": blockers,
        "next_command": next_command,
    }


def legacy_backfill(config: ConfigDocument, *, through: int) -> dict[str, Any]:
    status = legacy_status(config, through=through)
    chapters = list(status["backfillable_chapters"])
    if not chapters:
        return {
            "schema": "legacy_backfill_v1",
            "created": False,
            "chapter_number": None,
            "task_id": "",
            "next_command": status["next_command"],
        }
    chapter_number = int(chapters[0])
    result = semantic_task(config, chapter_number=chapter_number, backfill=True)
    return {
        "schema": "legacy_backfill_v1",
        "created": True,
        "chapter_number": chapter_number,
        "task_file": result.task_file,
        "manifest_file": result.manifest_file,
        "output_file": result.output_file,
        "next_command": result.next_command,
    }


def legacy_compact(
    config: ConfigDocument,
    *,
    through: int,
    approved_by: str,
    dry_run: bool,
) -> dict[str, Any]:
    approved_by = str(approved_by or "").strip()
    if not approved_by:
        raise ValueError("approved_by is required.")
    status = legacy_status(config, through=through)
    root = resolve_project_root(config)
    blockers = list(status["blockers"])
    if int(status["ranges"]["semantic_ledger_continuous_through"]) < through:
        blockers.append("semantic ledger backfill is incomplete")
    final_numbers = chapter_numbers(root / "40_manuscript" / "final", "*.md")
    if max(final_numbers, default=0) != through:
        blockers.append("legacy compact must cover the complete finalized range")
    compact_through = max(0, through - 2)
    payload: dict[str, Any] = {
        "schema": "legacy_compaction_v1",
        "through": through,
        "approved_by": approved_by,
        "dry_run": dry_run,
        "eligible": not blockers,
        "blockers": blockers,
        "closures_created": [],
        "closures_repaired": [],
        "compact_through": compact_through,
        "archives": [],
        "next_command": (
            f"longform-engine legacy compact project.yaml --through {through} --approved-by {approved_by}"
            if not blockers and dry_run
            else status["next_command"]
        ),
    }
    if dry_run or blockers:
        return payload

    semantic_rebuild(config, through=through, approved_by=approved_by)
    for chapter_number in range(1, through + 1):
        verify_materialized_chapter(config, root, chapter_number)

    closure_dir = root / "30_state" / "chapter_closures"
    state_file = root / "30_state" / "novel_state.json"
    closure_files = [closure_dir / f"ch{chapter:03d}.json" for chapter in range(1, through + 1)]
    with apply_transaction(
        root,
        command="legacy compact closures",
        chapter_number=through,
        source_paths=[
            root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json"
            for chapter in range(1, through + 1)
        ],
        touched_paths=[*closure_files, state_file],
        metadata={"migration": True, "approved_by": approved_by, "through": through},
    ):
        created: list[int] = []
        repaired: list[int] = []
        for chapter_number, closure_file in enumerate(closure_files, start=1):
            final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
            ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
            if closure_file.exists():
                if closure_is_valid(root, chapter_number):
                    continue
                prior_closure_hash = file_hash(closure_file)
                prior_closure = read_json(closure_file, {})
                if (
                    not isinstance(prior_closure, dict)
                    or int(prior_closure.get("chapter_number") or 0) != chapter_number
                ):
                    raise ValueError(f"Existing closure ch{chapter_number:03d} is unreadable or belongs to another chapter.")
                closure = {
                    "schema": "chapter_closure_v1",
                    "chapter_number": chapter_number,
                    "approved_by": approved_by,
                    "final_sha256": file_hash(final),
                    "semantic_ledger_sha256": file_hash(ledger),
                    "closed_at": utc_now(),
                    "archive_through": max(0, chapter_number - 2),
                    "migration": {
                        "schema": "legacy_closure_migration_v1",
                        "through": through,
                        "evidence_validated": True,
                        "repaired_stale_closure": True,
                        "prior_closure_sha256": prior_closure_hash,
                    },
                }
                atomic_write_text(closure_file, json.dumps(closure, ensure_ascii=False, indent=2) + "\n")
                repaired.append(chapter_number)
                continue
            closure = {
                "schema": "chapter_closure_v1",
                "chapter_number": chapter_number,
                "approved_by": approved_by,
                "final_sha256": file_hash(final),
                "semantic_ledger_sha256": file_hash(ledger),
                "closed_at": utc_now(),
                "archive_through": max(0, chapter_number - 2),
                "migration": {
                    "schema": "legacy_closure_migration_v1",
                    "through": through,
                    "evidence_validated": True,
                },
            }
            atomic_write_text(closure_file, json.dumps(closure, ensure_ascii=False, indent=2) + "\n")
            created.append(chapter_number)
        state = read_json(state_file, {})
        if not isinstance(state, dict):
            state = {}
        state.update(
            {
                "status": "legacy_migration_closed",
                "last_closed_chapter": through,
                "last_closure": f"30_state/chapter_closures/ch{through:03d}.json",
                "legacy_migration": {"through": through, "approved_by": approved_by},
                "updated_at": utc_now(),
            }
        )
        atomic_write_text(state_file, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        payload["closures_created"] = created
        payload["closures_repaired"] = repaired

    compact = compact_artifacts(config, through=compact_through, dry_run=False)
    payload["archives"] = list(compact.archive_files)
    payload["dry_run"] = False
    payload["next_command"] = "longform-engine artifacts verify project.yaml"
    return payload


def gate_is_valid(root: Path, chapter_number: int) -> bool:
    final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    gate = read_json(root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json", {})
    counts = gate.get("severity_counts") if isinstance(gate, dict) and isinstance(gate.get("severity_counts"), dict) else {}
    source_hash = str(gate.get("source_sha256") or "") if isinstance(gate, dict) else ""
    return bool(
        final.is_file()
        and isinstance(gate, dict)
        and gate.get("passed") is True
        and int(counts.get("P0") or 0) == 0
        and int(counts.get("P1") or 0) == 0
        and source_hash == file_hash(final)
    )


def ledger_is_valid(root: Path, chapter_number: int) -> bool:
    final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    ledger = read_json(root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json", {})
    source = ledger.get("source") if isinstance(ledger, dict) and isinstance(ledger.get("source"), dict) else {}
    return bool(
        final.is_file()
        and isinstance(ledger, dict)
        and ledger.get("schema") == "chapter_semantic_bundle_v1"
        and ledger.get("canonical") is True
        and int(ledger.get("chapter_number") or 0) == chapter_number
        and str(source.get("sha256") or "") == file_hash(final)
    )


def closure_is_valid(root: Path, chapter_number: int) -> bool:
    final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    closure = read_json(root / "30_state" / "chapter_closures" / f"ch{chapter_number:03d}.json", {})
    return bool(
        final.is_file()
        and ledger.is_file()
        and isinstance(closure, dict)
        and int(closure.get("chapter_number") or 0) == chapter_number
        and str(closure.get("final_sha256") or "") == file_hash(final)
        and str(closure.get("semantic_ledger_sha256") or "") == file_hash(ledger)
    )


def chapter_numbers(directory: Path, pattern: str) -> set[int]:
    result: set[int] = set()
    if not directory.is_dir():
        return result
    for path in directory.glob(pattern):
        match = path.stem.removeprefix("ch")
        if match.isdigit():
            result.add(int(match))
        elif path.parent.name.startswith("ch") and path.parent.name[2:].isdigit():
            result.add(int(path.parent.name[2:]))
    return result


def continuous_through(chapters: set[int]) -> int:
    value = 0
    while value + 1 in chapters:
        value += 1
    return value


def chapter_list(chapters: list[int]) -> str:
    return ", ".join(f"ch{chapter:03d}" for chapter in chapters[:8])


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file()) if path.is_dir() else 0


def read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
