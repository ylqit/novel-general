"""Human-approved, evidence-bound book completion."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.lengths import compile_length_forecast
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.text_metrics import content_character_count


@dataclass(frozen=True)
class BookCompletionStatus:
    schema: str
    ready_for_human_approval: bool
    approved: bool
    total_content_characters: int
    completion_range: tuple[int, int]
    length_status: str
    recommended_action: str
    latest_final_chapter: int
    unresolved_required_promises: tuple[str, ...]
    blockers: tuple[str, ...]
    next_command: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def completion_status(config: ConfigDocument) -> BookCompletionStatus:
    root = resolve_project_root(config)
    forecast = compile_length_forecast(config.data["length"])
    finals = sorted((root / "40_manuscript" / "final").glob("ch*.md"))
    latest = max((chapter_number(path) for path in finals), default=0)
    total = sum(content_character_count(path.read_text(encoding="utf-8")) for path in finals)
    blockers: list[str] = []
    if not finals:
        blockers.append("no_final_chapters")
    if total < forecast.completion_min_characters:
        length_status = "below_tolerance"
        recommended_action = (
            "Ask the human to approve another story arc or a changed ending scope; do not add filler merely to reach size."
        )
        blockers.append("content_character_target_below_tolerance")
    elif total > forecast.completion_max_characters:
        length_status = "above_tolerance"
        recommended_action = (
            "Ask the human to re-estimate length.target_total_characters if the longer causal ending should be retained; "
            "do not compress established events mechanically."
        )
        blockers.append("content_character_target_above_tolerance")
    else:
        length_status = "within_tolerance"
        recommended_action = "Review ending closure and explicitly approve completion."
    if latest and not (root / "30_state" / "chapter_closures" / f"ch{latest:03d}.json").is_file():
        blockers.append("latest_chapter_not_closed")
    if has_blocking_gate(root):
        blockers.append("unresolved_P0_or_P1")
    unresolved = unresolved_completion_promises(root)
    if unresolved:
        blockers.append("required_promises_not_closed")
    approval = read_json(root / "30_state" / "book_completion.json", {})
    approved = (
        isinstance(approval, dict)
        and approval.get("schema") == "book_completion_approval_v2"
        and approval.get("approved") is True
        and int(approval.get("total_content_characters") or -1) == total
        and int(approval.get("latest_final_chapter") or -1) == latest
        and latest > 0
        and approval.get("latest_final_sha256")
        == sha256((root / "40_manuscript" / "final" / f"ch{latest:03d}.md").read_bytes()).hexdigest()
        and approval.get("final_corpus_sha256") == final_corpus_hash(root)
    )
    return BookCompletionStatus(
        schema="book_completion_status_v2",
        ready_for_human_approval=not blockers,
        approved=approved,
        total_content_characters=total,
        completion_range=(forecast.completion_min_characters, forecast.completion_max_characters),
        length_status=length_status,
        recommended_action=recommended_action,
        latest_final_chapter=latest,
        unresolved_required_promises=tuple(unresolved),
        blockers=tuple(blockers),
        next_command=(
            "longform-engine book completion-approve project.yaml --approved-by human --ending-summary \"<summary>\""
            if not blockers and not approved
            else ""
            if approved
            else "longform-engine production next project.yaml"
            if length_status == "below_tolerance"
            else "longform-engine validate-config project.yaml --explain"
            if length_status == "above_tolerance"
            else "longform-engine book completion-status project.yaml"
        ),
    )


def approve_completion(
    config: ConfigDocument,
    *,
    approved_by: str,
    ending_summary: str,
) -> BookCompletionStatus:
    if approved_by != "human":
        raise ValueError("Book completion requires --approved-by human.")
    if not str(ending_summary).strip():
        raise ValueError("Book completion requires a non-empty --ending-summary.")
    status = completion_status(config)
    if not status.ready_for_human_approval:
        raise ValueError("Book completion is blocked: " + ", ".join(status.blockers))
    root = resolve_project_root(config)
    target = root / "30_state" / "book_completion.json"
    payload = {
        "schema": "book_completion_approval_v2",
        "approved": True,
        "approved_by": approved_by,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "ending_summary": str(ending_summary).strip(),
        "latest_final_chapter": status.latest_final_chapter,
        "latest_final_sha256": sha256(
            (root / "40_manuscript" / "final" / f"ch{status.latest_final_chapter:03d}.md").read_bytes()
        ).hexdigest(),
        "total_content_characters": status.total_content_characters,
        "final_corpus_sha256": final_corpus_hash(root),
        "required_promises_closed": True,
        "unresolved_P0_P1": 0,
    }
    with apply_transaction(
        root,
        command="book completion-approve",
        touched_paths=(target,),
        metadata={"approved_by": approved_by, "latest_final_chapter": status.latest_final_chapter},
    ):
        atomic_write_text(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return completion_status(config)


def fast_completion_marker(config: ConfigDocument) -> tuple[str, dict[str, Any]]:
    """Return a constant-size completion projection for normal production routing.

    The explicit completion commands perform the expensive corpus-wide verification. Normal
    chapter routing only verifies the approval record against the immutable final chapter and
    its closure, so routing does not reread every manuscript file as a book grows.
    """

    root = resolve_project_root(config)
    target = root / "30_state" / "book_completion.json"
    if not target.is_file():
        return "absent", {}
    approval = read_json(target, {})
    if not isinstance(approval, dict) or approval.get("schema") != "book_completion_approval_v2":
        return "invalid", approval if isinstance(approval, dict) else {}
    latest = int(approval.get("latest_final_chapter") or 0)
    final_file = root / "40_manuscript" / "final" / f"ch{latest:03d}.md"
    closure_file = root / "30_state" / "chapter_closures" / f"ch{latest:03d}.json"
    if (
        approval.get("approved") is not True
        or latest <= 0
        or not final_file.is_file()
        or not closure_file.is_file()
        or approval.get("latest_final_sha256") != sha256(final_file.read_bytes()).hexdigest()
    ):
        return "invalid", approval
    return "approved", approval


def unresolved_completion_promises(root: Path) -> list[str]:
    ledger = read_json(root / "20_outline" / "foreshadowing_ledger.json", [])
    state = read_json(root / "30_state" / "foreshadowing_state.json", {})
    actual = state.get("threads") if isinstance(state, dict) and isinstance(state.get("threads"), dict) else {}
    unresolved: list[str] = []
    for item in ledger if isinstance(ledger, list) else []:
        if not isinstance(item, dict) or item.get("completion_required") is not True:
            continue
        thread_id = str(item.get("id") or item.get("thread_id") or "")
        status = str((actual.get(thread_id) or {}).get("status") or item.get("status") or "planned")
        if status not in {"paid_off", "resolved", "fulfilled"}:
            unresolved.append(thread_id)
    return sorted(item for item in unresolved if item)


def has_blocking_gate(root: Path) -> bool:
    for path in (root / "50_workbench" / "gate_artifacts").glob("ch*/gate_result.json"):
        payload = read_json(path, {})
        counts = payload.get("severity_counts") if isinstance(payload, dict) else {}
        if isinstance(counts, dict) and (int(counts.get("P0") or 0) or int(counts.get("P1") or 0)):
            return True
    return False


def final_corpus_hash(root: Path) -> str:
    digest = sha256()
    for path in sorted((root / "40_manuscript" / "final").glob("ch*.md")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def chapter_number(path: Path) -> int:
    digits = "".join(character for character in path.stem if character.isdigit())
    return int(digits or 0)


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
