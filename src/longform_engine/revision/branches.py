"""Isolated, versioned historical revision branches for the v0.10 workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json
import shutil

from longform_engine.config import ConfigDocument
from longform_engine.semantic import semantic_rebuild
from longform_engine.semantic.pipeline import (
    validate_delta_evidence,
    validate_evidence_items,
)
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import list_finalized_chapter_files, manuscript_chapter_path


REVISION_BRANCH_SCHEMA = "revision_branch_v2"
REVISION_BASE_HEAD_SCHEMA = "revision_base_head_v1"
REVISION_CHAPTER_RECEIPT_SCHEMA = "revision_chapter_receipt_v1"
PUBLICATION_REVISION_MANIFEST_SCHEMA = "publication_revision_manifest_v1"
ACTIVE_BRANCH_STATES = frozenset({"open", "rewriting", "ready_for_promotion"})
REQUIRED_WORKFLOW_EVIDENCE = frozenset(
    {
        "direction_approval",
        "plot_node_approval",
        "planning_semantic_review",
        "human_lock",
        "final_gate",
        "semantic_realization",
        "chapter_close",
    }
)


@dataclass(frozen=True)
class RevisionBranchV2Result:
    branch_id: str
    branch_file: str
    from_chapter: int
    to_chapter: int
    status: str
    base_head_sha256: str
    next_chapter: int | None


@dataclass(frozen=True)
class RevisionPromotionResult:
    branch_id: str
    chapters: tuple[int, ...]
    transaction_report: str
    semantic_rebuild_report: str
    publication_manifest: str


def create_versioned_revision_branch(
    config: ConfigDocument,
    *,
    from_chapter: int,
    to_chapter: int,
    reason: str,
    created_by: str,
) -> RevisionBranchV2Result:
    """Create an isolated branch without changing canonical files or derived DB state."""

    if created_by != "human":
        raise ValueError("historical revision branches must be opened by human")
    if from_chapter <= 0 or to_chapter < from_chapter:
        raise ValueError("revision range must be positive and continuous")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("revision branch requires a non-empty human reason")
    root = resolve_project_root(config)
    active = active_revision_branch(root)
    if active is not None:
        raise ValueError(f"revision branch {active['branch_id']} is already active")
    finalized = dict(list_finalized_chapter_files(root))
    if not finalized:
        raise ValueError("historical revision requires finalized chapters")
    head = max(finalized)
    if to_chapter != head:
        raise ValueError(
            f"historical revision must cover through the current head ch{head:03d}; "
            "future-only changes use a canon change proposal"
        )
    missing = [chapter for chapter in range(from_chapter, to_chapter + 1) if chapter not in finalized]
    if missing:
        raise ValueError("revision range has missing finalized chapters: " + ", ".join(map(str, missing)))

    inventory = canonical_inventory(root)
    base_head = {
        "schema": REVISION_BASE_HEAD_SCHEMA,
        "from_chapter": from_chapter,
        "to_chapter": to_chapter,
        "latest_finalized_chapter": head,
        "inventory": inventory,
        "head_sha256": inventory_hash(inventory),
    }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    branch_id = (
        f"rev-{timestamp.lower()}-ch{from_chapter:03d}-ch{to_chapter:03d}-"
        f"{base_head['head_sha256'][:8]}"
    )
    directory = root / "50_workbench" / "revision_branches" / branch_id
    if directory.exists():
        raise ValueError(f"revision branch already exists: {branch_id}")
    for relative in (
        "base/final",
        "workbench",
        "final",
        "semantic_ledger",
        "closures",
        "receipts",
    ):
        (directory / relative).mkdir(parents=True, exist_ok=False if relative == "base/final" else True)
    for chapter in range(from_chapter, to_chapter + 1):
        shutil.copy2(finalized[chapter], directory / "base" / "final" / f"ch{chapter:03d}.md")
    _write_json(directory / "base_head.json", base_head)
    branch = {
        "schema": REVISION_BRANCH_SCHEMA,
        "branch_id": branch_id,
        "status": "open",
        "from_chapter": from_chapter,
        "to_chapter": to_chapter,
        "reason": reason.strip(),
        "created_by": "human",
        "base_head_path": (directory / "base_head.json").relative_to(root).as_posix(),
        "base_head_sha256": _file_hash(directory / "base_head.json"),
        "recorded_chapters": [],
        "next_chapter": from_chapter,
        "promotion": None,
    }
    branch_file = directory / "branch.json"
    _write_json(branch_file, branch)
    return _branch_result(root, branch_file, branch)


def record_revision_chapter(
    config: ConfigDocument,
    *,
    branch_id: str,
    receipt_path: str | Path,
) -> RevisionBranchV2Result:
    """Record one sequential branch chapter after its complete isolated workflow."""

    root = resolve_project_root(config)
    branch_file, branch = load_revision_branch(root, branch_id)
    if branch.get("status") not in {"open", "rewriting"}:
        raise ValueError("revision branch does not accept chapter records in its current state")
    assert_base_head_current(root, branch)
    receipt_file = _resolve_project_file(root, receipt_path)
    directory = branch_file.parent
    _require_inside(receipt_file, directory)
    receipt = _read_json(receipt_file)
    errors = validate_revision_chapter_receipt(root, directory, branch, receipt)
    if errors:
        raise ValueError("revision chapter receipt is invalid: " + "; ".join(errors))

    chapter = int(receipt["chapter_number"])
    candidate = _resolve_project_file(root, receipt["candidate_path"])
    semantic = _resolve_project_file(root, receipt["semantic_bundle_path"])
    final_target = directory / "final" / f"ch{chapter:03d}.md"
    semantic_target = directory / "semantic_ledger" / f"ch{chapter:03d}.json"
    receipt_target = directory / "receipts" / f"ch{chapter:03d}.json"
    shutil.copy2(candidate, final_target)
    shutil.copy2(semantic, semantic_target)
    _write_json(
        receipt_target,
        {
            **receipt,
            "candidate_path": final_target.relative_to(root).as_posix(),
            "candidate_sha256": _file_hash(final_target),
            "semantic_bundle_path": semantic_target.relative_to(root).as_posix(),
            "semantic_bundle_sha256": _file_hash(semantic_target),
        },
    )
    close_evidence = next(
        item for item in receipt["workflow_evidence"] if item["kind"] == "chapter_close"
    )
    closure_source = _resolve_project_file(root, close_evidence["path"])
    shutil.copy2(closure_source, directory / "closures" / f"ch{chapter:03d}.json")

    recorded = [*branch["recorded_chapters"], chapter]
    next_chapter = chapter + 1 if chapter < int(branch["to_chapter"]) else None
    branch.update(
        {
            "status": "ready_for_promotion" if next_chapter is None else "rewriting",
            "recorded_chapters": recorded,
            "next_chapter": next_chapter,
        }
    )
    _write_json(branch_file, branch)
    return _branch_result(root, branch_file, branch)


def validate_revision_chapter_receipt(
    root: Path,
    directory: Path,
    branch: dict[str, Any],
    receipt: Any,
) -> list[str]:
    errors: list[str] = []
    fields = {
        "schema",
        "branch_id",
        "chapter_number",
        "candidate_path",
        "candidate_sha256",
        "semantic_bundle_path",
        "semantic_bundle_sha256",
        "workflow_evidence",
        "approved_by",
    }
    if not isinstance(receipt, dict) or set(receipt) != fields:
        return ["receipt fields are invalid"]
    if receipt.get("schema") != REVISION_CHAPTER_RECEIPT_SCHEMA:
        errors.append(f"receipt schema must be {REVISION_CHAPTER_RECEIPT_SCHEMA}")
    if receipt.get("branch_id") != branch.get("branch_id"):
        errors.append("receipt branch_id does not match")
    expected_chapter = int(branch["from_chapter"]) + len(branch["recorded_chapters"])
    if receipt.get("chapter_number") != expected_chapter:
        errors.append(f"revision chapters must be recorded sequentially; expected {expected_chapter}")
    if receipt.get("approved_by") != "human":
        errors.append("revision chapter receipt must be human-approved")
    candidate: Path | None = None
    semantic: Path | None = None
    try:
        candidate = _resolve_project_file(root, receipt.get("candidate_path"))
        _require_inside(candidate, directory)
        if receipt.get("candidate_sha256") != _file_hash(candidate):
            errors.append("candidate SHA-256 is stale")
        text = candidate.read_text(encoding="utf-8")
        if not text.strip():
            errors.append("revision candidate must not be empty")
    except (OSError, UnicodeError, ValueError) as exc:
        errors.append(f"revision candidate is invalid: {exc}")
        text = ""
    try:
        semantic = _resolve_project_file(root, receipt.get("semantic_bundle_path"))
        _require_inside(semantic, directory)
        if receipt.get("semantic_bundle_sha256") != _file_hash(semantic):
            errors.append("semantic bundle SHA-256 is stale")
        semantic_payload = _read_json(semantic)
        errors.extend(
            validate_branch_semantic_bundle(
                root,
                semantic_payload,
                chapter_number=expected_chapter,
                candidate=candidate,
                source_text=text,
            )
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        errors.append(f"semantic bundle is invalid: {exc}")

    evidence = receipt.get("workflow_evidence")
    if not isinstance(evidence, list):
        errors.append("workflow_evidence must be a list")
        return errors
    kinds: set[str] = set()
    for index, item in enumerate(evidence):
        prefix = f"workflow_evidence[{index}]"
        if not isinstance(item, dict) or set(item) != {"kind", "path", "sha256"}:
            errors.append(f"{prefix} fields are invalid")
            continue
        kind = str(item.get("kind") or "")
        if kind in kinds:
            errors.append(f"duplicate workflow evidence kind: {kind}")
        kinds.add(kind)
        try:
            path = _resolve_project_file(root, item.get("path"))
            _require_inside(path, directory)
            if item.get("sha256") != _file_hash(path):
                errors.append(f"{prefix}.sha256 is stale")
        except (OSError, ValueError) as exc:
            errors.append(f"{prefix} is invalid: {exc}")
    missing = sorted(REQUIRED_WORKFLOW_EVIDENCE - kinds)
    extra = sorted(kinds - REQUIRED_WORKFLOW_EVIDENCE)
    if missing:
        errors.append("workflow evidence is missing: " + ", ".join(missing))
    if extra:
        errors.append("workflow evidence has unknown kinds: " + ", ".join(extra))
    return errors


def validate_branch_semantic_bundle(
    root: Path,
    payload: Any,
    *,
    chapter_number: int,
    candidate: Path | None,
    source_text: str,
) -> list[str]:
    """Validate branch semantic evidence without consulting mutable mainline state."""

    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["semantic bundle must be an object"]
    if payload.get("schema") != "chapter_semantic_bundle_v1":
        errors.append("semantic bundle schema must be chapter_semantic_bundle_v1")
    if payload.get("chapter_number") != chapter_number:
        errors.append("semantic bundle chapter_number does not match")
    source = payload.get("source")
    if not isinstance(source, dict) or set(source) != {"path", "sha256"}:
        errors.append("semantic bundle source fields are invalid")
    elif candidate is not None:
        if source.get("path") != candidate.relative_to(root).as_posix():
            errors.append("semantic bundle source.path must bind the branch candidate")
        if source.get("sha256") != _file_hash(candidate):
            errors.append("semantic bundle source.sha256 is stale")
    digest = payload.get("chapter_digest")
    if not isinstance(digest, dict):
        errors.append("chapter_digest must be an object")
    else:
        for field in ("summary", "causal_change", "reader_payoff", "cost"):
            if not isinstance(digest.get(field), str) or not digest[field].strip():
                errors.append(f"chapter_digest.{field} is required")
    required_lists = (
        "scenes",
        "events",
        "relationship_deltas",
        "character_deltas",
        "foreshadow_deltas",
        "world_deltas",
        "timeline_deltas",
    )
    for field in required_lists:
        if not isinstance(payload.get(field), list):
            errors.append(f"{field} must be a list")
    validate_evidence_items(payload.get("scenes"), "scenes", source_text, errors)
    for field in required_lists[1:]:
        validate_delta_evidence(payload.get(field), field, source_text, errors)
    for index, event in enumerate(payload.get("events") or []):
        if not isinstance(event, dict) or not str(event.get("event_id") or "").strip():
            errors.append(f"events[{index}] requires a stable event_id")
    return errors


def abandon_revision_branch(
    config: ConfigDocument,
    *,
    branch_id: str,
    reason: str,
    abandoned_by: str,
) -> RevisionBranchV2Result:
    """Abandon the workbench branch with zero canonical mutation."""

    if abandoned_by != "human":
        raise ValueError("revision branch abandonment must be human-owned")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("branch abandonment requires a non-empty reason")
    root = resolve_project_root(config)
    branch_file, branch = load_revision_branch(root, branch_id)
    if branch.get("status") not in ACTIVE_BRANCH_STATES:
        raise ValueError("only an active revision branch can be abandoned")
    branch.update(
        {
            "status": "abandoned",
            "next_chapter": None,
            "promotion": {
                "decision": "abandoned",
                "reason": reason.strip(),
                "decided_by": "human",
            },
        }
    )
    _write_json(branch_file, branch)
    return _branch_result(root, branch_file, branch)


def promote_revision_branch(
    config: ConfigDocument,
    *,
    branch_id: str,
    approved_by: str,
) -> RevisionPromotionResult:
    """Replace E..H and rebuild semantic/RAG/DB views under an outer transaction."""

    if approved_by != "human":
        raise ValueError("revision branch promotion must be human-approved")
    root = resolve_project_root(config)
    branch_file, branch = load_revision_branch(root, branch_id)
    if branch.get("status") != "ready_for_promotion":
        raise ValueError("revision branch is not ready for promotion")
    assert_base_head_current(root, branch)
    start = int(branch["from_chapter"])
    end = int(branch["to_chapter"])
    chapters = tuple(range(start, end + 1))
    directory = branch_file.parent
    for chapter in chapters:
        for path in (
            directory / "final" / f"ch{chapter:03d}.md",
            directory / "semantic_ledger" / f"ch{chapter:03d}.json",
            directory / "closures" / f"ch{chapter:03d}.json",
            directory / "receipts" / f"ch{chapter:03d}.json",
        ):
            if not path.is_file():
                raise ValueError(f"revision branch is incomplete: {path.relative_to(root).as_posix()}")

    publication_manifest = (
        root / "80_exports" / "platform" / f"publication_revision_manifest.{branch_id}.json"
    )
    touched_paths: list[Path] = [
        *[manuscript_chapter_path(root, chapter, lane="final") for chapter in chapters],
        *[root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json" for chapter in chapters],
        *[root / "30_state" / "chapter_closures" / f"ch{chapter:03d}.json" for chapter in chapters],
        root / "30_state" / "story_graph.json",
        root / "30_state" / "foreshadowing_state.json",
        root / "30_state" / "timeline.json",
        root / "30_state" / "world_state.json",
        root / "30_state" / "novel_state.json",
        root / "30_state" / "tcs",
        root / "40_manuscript" / "summaries",
        root / "40_manuscript" / "chapter_meta.jsonl",
        root / "60_rag" / "chunks",
        root / "60_rag" / "context",
        root / "60_rag" / "metadata",
        root / "60_rag" / "memory",
        root / "70_runtime" / "db",
        publication_manifest,
        branch_file,
    ]
    semantic_report = ""
    with apply_transaction(
        root,
        command="revision branch-promote-v2",
        chapter_number=start,
        source_paths=[
            branch_file,
            *[directory / "receipts" / f"ch{chapter:03d}.json" for chapter in chapters],
        ],
        touched_paths=touched_paths,
        metadata={"branch_id": branch_id, "from_chapter": start, "to_chapter": end},
    ) as transaction:
        revised_hashes: list[dict[str, Any]] = []
        for chapter in chapters:
            branch_final = directory / "final" / f"ch{chapter:03d}.md"
            canonical_final = manuscript_chapter_path(root, chapter, lane="final")
            canonical_final.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(branch_final, canonical_final)

            branch_semantic = _read_json(
                directory / "semantic_ledger" / f"ch{chapter:03d}.json"
            )
            branch_semantic.update(
                {
                    "source": {
                        "path": canonical_final.relative_to(root).as_posix(),
                        "sha256": _file_hash(canonical_final),
                    },
                    "canonical": True,
                    "candidate_sha256": _file_hash(
                        directory / "semantic_ledger" / f"ch{chapter:03d}.json"
                    ),
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                    "validation_file": (
                        directory / "receipts" / f"ch{chapter:03d}.json"
                    ).relative_to(root).as_posix(),
                }
            )
            canonical_ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter:03d}.json"
            _write_json(canonical_ledger, branch_semantic)

            branch_closure = _read_json(directory / "closures" / f"ch{chapter:03d}.json")
            branch_closure.update(
                {
                    "chapter_number": chapter,
                    "final_sha256": _file_hash(canonical_final),
                    "semantic_ledger_sha256": _file_hash(canonical_ledger),
                    "revision_branch_id": branch_id,
                    "approved_by": "human",
                }
            )
            _write_json(
                root / "30_state" / "chapter_closures" / f"ch{chapter:03d}.json",
                branch_closure,
            )
            revised_hashes.append(
                {
                    "chapter_number": chapter,
                    "final_sha256": _file_hash(canonical_final),
                    "semantic_ledger_sha256": _file_hash(canonical_ledger),
                }
            )

        rebuild = semantic_rebuild(config, through=end, approved_by="human")
        semantic_report = rebuild.transaction_file
        publication_payload = {
            "schema": PUBLICATION_REVISION_MANIFEST_SCHEMA,
            "branch_id": branch_id,
            "from_chapter": start,
            "to_chapter": end,
            "chapters": revised_hashes,
            "external_platform_sync": "manual_upload_required",
            "targets": ["qidian", "fanqie"],
            "platform_acceptance": "unknown_until_platform_submission",
        }
        _write_json(publication_manifest, publication_payload)
        branch.update(
            {
                "status": "promoted",
                "next_chapter": None,
                "promotion": {
                    "decision": "promoted",
                    "approved_by": "human",
                    "transaction_report": transaction.report_file.relative_to(root).as_posix(),
                    "semantic_rebuild_report": semantic_report,
                    "publication_manifest": publication_manifest.relative_to(root).as_posix(),
                },
            }
        )
        _write_json(branch_file, branch)
        transaction.update_metadata(
            revised_chapters=list(chapters),
            semantic_rebuild_report=semantic_report,
            publication_manifest=publication_manifest.relative_to(root).as_posix(),
        )
    return RevisionPromotionResult(
        branch_id=branch_id,
        chapters=chapters,
        transaction_report=transaction.report_file.relative_to(root).as_posix(),
        semantic_rebuild_report=semantic_report,
        publication_manifest=publication_manifest.relative_to(root).as_posix(),
    )


def active_revision_branch(root: Path) -> dict[str, Any] | None:
    active: list[dict[str, Any]] = []
    directory = root / "50_workbench" / "revision_branches"
    for path in sorted(directory.glob("*/branch.json")):
        try:
            branch = _read_json(path)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if branch.get("schema") == REVISION_BRANCH_SCHEMA and branch.get("status") in ACTIVE_BRANCH_STATES:
            active.append(branch)
    if len(active) > 1:
        raise ValueError("multiple active revision branches require human recovery")
    return active[0] if active else None


def load_revision_branch(root: Path, branch_id: str) -> tuple[Path, dict[str, Any]]:
    if not isinstance(branch_id, str) or not branch_id or Path(branch_id).name != branch_id:
        raise ValueError("invalid revision branch id")
    path = root / "50_workbench" / "revision_branches" / branch_id / "branch.json"
    branch = _read_json(path)
    if branch.get("schema") != REVISION_BRANCH_SCHEMA or branch.get("branch_id") != branch_id:
        raise ValueError("revision branch metadata is invalid")
    return path, branch


def assert_base_head_current(root: Path, branch: dict[str, Any]) -> None:
    base_path = _resolve_project_file(root, branch.get("base_head_path"))
    if branch.get("base_head_sha256") != _file_hash(base_path):
        raise ValueError("revision branch base head metadata is stale")
    base = _read_json(base_path)
    current = canonical_inventory(root)
    if base.get("inventory") != current or base.get("head_sha256") != inventory_hash(current):
        raise ValueError("mainline canonical head drifted after the revision branch was created")


def canonical_inventory(root: Path) -> list[dict[str, Any]]:
    paths: list[Path] = []
    for relative in ("00_governance", "10_bible", "20_outline", "30_state"):
        directory = root / relative
        if directory.is_dir():
            paths.extend(path for path in directory.rglob("*") if path.is_file())
    final_dir = root / "40_manuscript" / "final"
    if final_dir.is_dir():
        paths.extend(path for path in final_dir.rglob("*") if path.is_file())
    return [
        {
            "path": path.relative_to(root).as_posix(),
            "sha256": _file_hash(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(set(paths), key=lambda item: item.relative_to(root).as_posix())
    ]


def inventory_hash(inventory: Iterable[dict[str, Any]]) -> str:
    return sha256(
        json.dumps(list(inventory), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _branch_result(root: Path, path: Path, branch: dict[str, Any]) -> RevisionBranchV2Result:
    return RevisionBranchV2Result(
        branch_id=str(branch["branch_id"]),
        branch_file=path.relative_to(root).as_posix(),
        from_chapter=int(branch["from_chapter"]),
        to_chapter=int(branch["to_chapter"]),
        status=str(branch["status"]),
        base_head_sha256=str(branch["base_head_sha256"]),
        next_chapter=(int(branch["next_chapter"]) if branch.get("next_chapter") else None),
    )


def _resolve_project_file(root: Path, raw: str | Path | None) -> Path:
    if not isinstance(raw, (str, Path)) or not str(raw):
        raise ValueError("path must be non-empty")
    candidate = Path(raw)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escaped project root: {raw}") from exc
    if not path.is_file():
        raise ValueError(f"file does not exist: {path}")
    return path


def _require_inside(path: Path, directory: Path) -> None:
    try:
        path.resolve().relative_to(directory.resolve())
    except ValueError as exc:
        raise ValueError(f"revision artifact escaped branch directory: {path}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return payload


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()
