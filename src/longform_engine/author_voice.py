"""Human-approved author voice edit pairs derived only from real chapter revisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


SCHEMA = "author_voice_edit_pair_v1"
BANK_SCHEMA = "author_voice_edit_pair_bank_v1"
BANK_PATH = "10_bible/style_profiles/author_voice_edit_pairs.json"
MAX_ACTIVE_PAIRS = 12


class AuthorVoiceError(ValueError):
    """Raised when a proposed voice pair is not a real human edit."""


@dataclass(frozen=True)
class AuthorVoiceApproveResult:
    chapter_number: int
    pair_id: str
    bank_file: str
    active_pairs: int
    replaced_pair_id: str
    transaction_report: str
    next_command: str


def approve_author_voice_edit_pair(
    config: ConfigDocument,
    *,
    chapter_number: int,
    record_path: str | Path,
    approved_by: str,
) -> AuthorVoiceApproveResult:
    """Promote one exact before/after pair after finalization and explicit human approval."""

    if approved_by != "human":
        raise AuthorVoiceError("author voice approval requires approved_by=human")
    root = resolve_project_root(config)
    record_file = resolve_record(root, record_path)
    record = load_json(record_file)
    errors, normalized = validate_pair_record(root, chapter_number, record)
    if errors:
        raise AuthorVoiceError("invalid author voice edit pair: " + "; ".join(errors))
    bank_file = root / BANK_PATH
    bank = load_bank(bank_file)
    active = [item for item in bank["pairs"] if item.get("active") is True]
    pair_id = str(normalized["pair_id"])
    if any(item.get("pair_id") == pair_id for item in bank["pairs"]):
        raise AuthorVoiceError(f"pair_id already exists: {pair_id}")
    replace_pair_id = str(normalized.get("replace_pair_id") or "")
    replacement = next((item for item in active if item.get("pair_id") == replace_pair_id), None)
    if len(active) >= MAX_ACTIVE_PAIRS and replacement is None:
        raise AuthorVoiceError(
            "the 12-pair active limit is reached; replace_pair_id must name an active pair selected by the human"
        )
    if replace_pair_id and replacement is None:
        raise AuthorVoiceError("replace_pair_id does not name an active pair")
    approved_at = utc_now()
    stored = {
        **normalized,
        "approved_by": "human",
        "approved_at": approved_at,
        "active": True,
        "source_record_file": relative(root, record_file),
        "source_record_sha256": file_hash(record_file),
    }
    with apply_transaction(
        root,
        command="creative author-voice-approve",
        chapter_number=chapter_number,
        source_paths=[record_file, root / str(normalized["final_file"]), root / str(normalized["revision_validation_file"])],
        touched_paths=[bank_file],
        metadata={
            "approved_by": approved_by,
            "pair_id": pair_id,
            "replace_pair_id": replace_pair_id,
            "max_active_pairs": MAX_ACTIVE_PAIRS,
        },
    ) as transaction:
        if replacement is not None:
            replacement["active"] = False
            replacement["replaced_by"] = pair_id
            replacement["deactivated_at"] = approved_at
        bank["pairs"].append(stored)
        bank["updated_at"] = approved_at
        active_count = sum(item.get("active") is True for item in bank["pairs"])
        if active_count > MAX_ACTIVE_PAIRS:
            raise AuthorVoiceError("author voice bank would exceed the active-pair limit")
        write_json(bank_file, bank)
    return AuthorVoiceApproveResult(
        chapter_number=chapter_number,
        pair_id=pair_id,
        bank_file=BANK_PATH,
        active_pairs=sum(item.get("active") is True for item in bank["pairs"]),
        replaced_pair_id=replace_pair_id,
        transaction_report=relative(root, transaction.report_file),
        next_command=f"longform-engine chapter close project.yaml --chapter {chapter_number} --approved-by human",
    )


def author_voice_chapter_status(root: Path, chapter_number: int) -> dict[str, Any]:
    bank = load_bank(root / BANK_PATH)
    final = manuscript_chapter_path(root, chapter_number, lane="final")
    final_hash = file_hash(final)
    pairs = [
        item
        for item in bank["pairs"]
        if item.get("active") is True
        and item.get("chapter_number") == chapter_number
        and item.get("final_sha256") == final_hash
    ]
    required = 1 <= chapter_number <= 3
    return {
        "required": required,
        "status": "complete" if pairs or not required else "pending",
        "chapter_number": chapter_number,
        "active_pair_count": len(pairs),
        "pair_ids": [str(item.get("pair_id") or "") for item in pairs],
        "next_command": (
            f"longform-engine creative author-voice-approve project.yaml --chapter {chapter_number} "
            "--record 50_workbench/human_author_revisions/chNNN/voice_pair.json --approved-by human"
            if required and not pairs
            else ""
        ),
    }


def require_author_voice_pair_for_close(root: Path, chapter_number: int) -> dict[str, Any]:
    status = author_voice_chapter_status(root, chapter_number)
    if status["required"] and status["status"] != "complete":
        raise AuthorVoiceError(
            f"Cannot close ch{chapter_number:03d}: chapters 1-3 require one approved author_voice_edit_pair_v1 "
            "from the current human revision and final prose."
        )
    return status


def relevant_author_voice_examples(
    root: Path,
    *,
    pov_character_id: str = "",
    scene_kind: str = "",
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Return at most two bounded positive examples relevant to the current POV or scene."""

    bank = load_bank(root / BANK_PATH)
    active = [item for item in bank["pairs"] if item.get("active") is True]
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for item in active:
        score = 0
        item_pov = str(item.get("pov_character_id") or "")
        item_scene = str(item.get("scene_kind") or "")
        if pov_character_id and item_pov == pov_character_id:
            score += 2
        if scene_kind and item_scene == scene_kind:
            score += 1
        if (pov_character_id or scene_kind) and score == 0:
            continue
        scored.append((score, str(item.get("approved_at") or ""), item))
    scored.sort(key=lambda value: (value[0], value[1]), reverse=True)
    return [
        {
            "pair_id": str(item.get("pair_id") or ""),
            "pov_character_id": str(item.get("pov_character_id") or ""),
            "scene_kind": str(item.get("scene_kind") or ""),
            "positive_excerpt": str(((item.get("after") or {}).get("text") or ""))[:320],
            "abstract_principle": str(item.get("abstract_principle") or "")[:240],
            "purpose": str(item.get("purpose") or "")[:160],
        }
        for _score, _approved_at, item in scored[: max(0, min(2, limit))]
    ]


def validate_pair_record(
    root: Path,
    chapter_number: int,
    payload: Any,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    expected = {
        "schema",
        "chapter_number",
        "pair_id",
        "purpose",
        "abstract_principle",
        "pov_character_id",
        "scene_kind",
        "before",
        "after",
        "final_sha256",
        "human_author_revision_sha256",
        "replace_pair_id",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        return ["voice pair record must contain exactly the author_voice_edit_pair_v1 fields"], {}
    if payload.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if payload.get("chapter_number") != chapter_number:
        errors.append("chapter_number does not match the command")
    pair_id = str(payload.get("pair_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,79}", pair_id):
        errors.append("pair_id must be a stable 2-80 character id")
    for field in ("purpose", "abstract_principle"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"{field} must be non-empty")
    for field in ("pov_character_id", "scene_kind", "replace_pair_id"):
        if not isinstance(payload.get(field), str):
            errors.append(f"{field} must be a string")
    final_file = manuscript_chapter_path(root, chapter_number, lane="final")
    finalization = load_json(final_file.with_suffix(".finalization.json"))
    human_binding: dict[str, Any] = {}
    if isinstance(finalization, dict):
        binding_value = finalization.get("human_author_revision")
        if isinstance(binding_value, dict):
            human_binding = binding_value
    revision_file = root / str(human_binding.get("validation_file") or "")
    revision = load_json(revision_file)
    if not final_file.is_file() or payload.get("final_sha256") != file_hash(final_file):
        errors.append("final_sha256 is missing or stale")
    if (
        not revision_file.is_file()
        or payload.get("human_author_revision_sha256") != file_hash(revision_file)
        or human_binding.get("validation_sha256") != file_hash(revision_file)
    ):
        errors.append("human_author_revision_sha256 is missing or stale")
    source_file = root / str(revision.get("source_file") or "") if isinstance(revision, dict) else Path()
    record_file = root / str(revision.get("record_file") or "") if isinstance(revision, dict) else Path()
    revision_record = load_json(record_file)
    source_text = source_file.read_text(encoding="utf-8") if source_file.is_file() else ""
    final_text = final_file.read_text(encoding="utf-8") if final_file.is_file() else ""
    before = validate_span(payload.get("before"), source_text, "before", errors)
    after = validate_span(payload.get("after"), final_text, "after", errors)
    if before is not None and after is not None and before == after:
        errors.append("voice pair before and after excerpts must differ")
    changes = revision_record.get("changes") if isinstance(revision_record, dict) else []
    if not span_overlaps_recorded_change(payload.get("before"), changes, side="before"):
        errors.append("before span must overlap a recorded human modification")
    if not span_overlaps_recorded_change(payload.get("after"), changes, side="after"):
        errors.append("after span must overlap a recorded human modification")
    normalized = {
        **payload,
        "final_file": relative(root, final_file),
        "revision_validation_file": relative(root, revision_file) if revision_file.is_file() else "",
    }
    return errors, normalized


def span_overlaps_recorded_change(span: Any, changes: Any, *, side: str) -> bool:
    if not isinstance(span, dict) or not isinstance(changes, list):
        return False
    start, end = span.get("start"), span.get("end")
    if not isinstance(start, int) or not isinstance(end, int):
        return False
    for item in changes:
        value = item.get(side) if isinstance(item, dict) else None
        if not isinstance(value, dict):
            continue
        other_start, other_end = value.get("start"), value.get("end")
        if isinstance(other_start, int) and isinstance(other_end, int) and start < other_end and other_start < end:
            return True
    return False


def validate_span(value: Any, text: str, label: str, errors: list[str]) -> str | None:
    if not isinstance(value, dict) or set(value) != {"start", "end", "text"}:
        errors.append(f"{label} must contain exactly start, end, text")
        return None
    start, end = value.get("start"), value.get("end")
    if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(text):
        errors.append(f"{label} has invalid bounds")
        return None
    excerpt = text[start:end]
    if value.get("text") != excerpt:
        errors.append(f"{label}.text does not match its source")
        return None
    return excerpt


def load_bank(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    if payload is None and not path.exists():
        return {"schema": BANK_SCHEMA, "max_active_pairs": MAX_ACTIVE_PAIRS, "pairs": [], "updated_at": "initialized"}
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "max_active_pairs", "pairs", "updated_at"}
        or payload.get("schema") != BANK_SCHEMA
        or payload.get("max_active_pairs") != MAX_ACTIVE_PAIRS
        or not isinstance(payload.get("pairs"), list)
    ):
        raise AuthorVoiceError("author voice edit pair bank is malformed; repair it explicitly instead of resetting evidence")
    return payload


def resolve_record(root: Path, value: str | Path) -> Path:
    raw = Path(value)
    path = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        path.relative_to((root / "50_workbench").resolve())
    except ValueError as exc:
        raise AuthorVoiceError("author voice record must stay under 50_workbench") from exc
    return path


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "AuthorVoiceApproveResult",
    "AuthorVoiceError",
    "MAX_ACTIVE_PAIRS",
    "SCHEMA",
    "approve_author_voice_edit_pair",
    "author_voice_chapter_status",
    "relevant_author_voice_examples",
    "require_author_voice_pair_for_close",
]
