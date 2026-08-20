"""Human-approved rolling character-causality simulations for chapter planning."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.reader_promises import LEDGER_PATH, reader_promise_planning_hash
from longform_engine.storage import atomic_write_text


SIMULATION_SCHEMA = "arc_causal_simulation_v1"
SIMULATION_DIR = Path("20_outline/arc_simulations")
BASIS_PATHS = (
    "10_bible/creative_brief.json",
    "10_bible/characters.json",
    "10_bible/relationships.json",
    "20_outline/book_outline.md",
    "20_outline/story_arcs.json",
    "20_outline/volumes.json",
    LEDGER_PATH.as_posix(),
    "30_state/character_state.json",
    "60_rag/memory/characters",
)
CHARACTER_DRIVE_FIELDS = {"character_id", "private_goal", "refusal_point", "offscreen_intent"}
COLLISION_FIELDS = {"chapter_number", "participants", "collision", "required_change"}
OBLIGATION_FIELDS = {"chapter_number", "cause", "pressure", "choice", "consequence"}


class ArcSimulationError(ValueError):
    """Raised when the active causal simulation is missing, stale, or malformed."""


def arc_simulation_path(root: Path, start: int, end: int) -> Path:
    return root / SIMULATION_DIR / f"ch{start:03d}-ch{end:03d}.json"


def current_basis_hashes(root: Path) -> dict[str, str]:
    hashes = {
        relative: basis_path_hash(root / relative)
        for relative in BASIS_PATHS
    }
    hashes[LEDGER_PATH.as_posix()] = reader_promise_planning_hash(root)
    return hashes


def basis_path_hash(path: Path) -> str:
    """Hash one causal source file or a deterministically ordered state directory."""

    if path.is_file():
        return sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        return "missing"
    digest = sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_arc_causal_simulation(
    payload: Any,
    *,
    expected_range: tuple[int, int] | None = None,
    expected_basis: dict[str, str] | None = None,
) -> list[str]:
    fields = {
        "schema", "from_chapter", "to_chapter", "basis_hashes", "protagonist_goal",
        "opposition_agenda", "character_drives", "knowledge_boundaries", "offstage_actions",
        "resource_shifts", "relationship_shifts", "collision_points", "causal_obligations",
        "approved_by", "status",
    }
    if not isinstance(payload, dict) or set(payload) != fields:
        return ["arc simulation must contain exactly the arc_causal_simulation_v1 fields"]
    errors: list[str] = []
    if payload.get("schema") != SIMULATION_SCHEMA:
        errors.append(f"schema must be {SIMULATION_SCHEMA}")
    start, end = payload.get("from_chapter"), payload.get("to_chapter")
    if not isinstance(start, int) or isinstance(start, bool) or start <= 0 or not isinstance(end, int) or end < start:
        errors.append("from_chapter/to_chapter must be a valid positive range")
    elif expected_range and (start, end) != expected_range:
        errors.append("arc simulation range does not match the requested planning window")
    basis = payload.get("basis_hashes")
    if not isinstance(basis, dict) or set(basis) != set(BASIS_PATHS) or any(
        not isinstance(value, str) or not value for value in basis.values()
    ):
        errors.append("basis_hashes must contain every causal basis path")
    elif expected_basis is not None and basis != expected_basis:
        errors.append("arc simulation basis_hashes are stale")
    for field in ("protagonist_goal", "opposition_agenda"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            errors.append(f"{field} must be non-empty")
    errors.extend(validate_object_list(payload.get("character_drives"), CHARACTER_DRIVE_FIELDS, "character_drives"))
    errors.extend(validate_string_list(payload.get("knowledge_boundaries"), "knowledge_boundaries"))
    errors.extend(validate_string_list(payload.get("offstage_actions"), "offstage_actions"))
    errors.extend(validate_string_list(payload.get("resource_shifts"), "resource_shifts"))
    errors.extend(validate_string_list(payload.get("relationship_shifts"), "relationship_shifts"))
    errors.extend(validate_ranged_object_list(payload.get("collision_points"), COLLISION_FIELDS, "collision_points", start, end))
    errors.extend(validate_ranged_object_list(payload.get("causal_obligations"), OBLIGATION_FIELDS, "causal_obligations", start, end))
    if isinstance(start, int) and isinstance(end, int) and isinstance(payload.get("causal_obligations"), list):
        obligation_chapters = {
            item.get("chapter_number")
            for item in payload["causal_obligations"]
            if isinstance(item, dict) and isinstance(item.get("chapter_number"), int)
        }
        if obligation_chapters != set(range(start, end + 1)):
            errors.append("causal_obligations must cover every chapter in the simulation window exactly by chapter")
    if payload.get("approved_by") != "human":
        errors.append("approved_by must be human")
    if payload.get("status") not in {"approved", "stale"}:
        errors.append("status must be approved or stale")
    return errors


def write_arc_causal_simulation(root: Path, payload: dict[str, Any]) -> Path:
    errors = validate_arc_causal_simulation(
        payload,
        expected_basis=current_basis_hashes(root),
    )
    payload_range = (
        payload.get("from_chapter"),
        payload.get("to_chapter"),
    )
    if payload_range not in permitted_arc_simulation_ranges(root):
        errors.append(
            "arc simulation range must match the current rolling window or its immediately adjacent next window"
        )
    if errors:
        raise ArcSimulationError("arc_causal_simulation_invalid:" + ";".join(errors))
    path = arc_simulation_path(root, int(payload["from_chapter"]), int(payload["to_chapter"]))
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def load_active_arc_simulation(root: Path, *, chapter_number: int) -> tuple[dict[str, Any], Path, str]:
    planning_range = current_planning_window_range(root)
    matches: list[tuple[dict[str, Any], Path]] = []
    for path in sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("status") != "approved":
            continue
        simulation_range = (
            int(payload.get("from_chapter") or 0),
            int(payload.get("to_chapter") or 0),
        )
        if (
            simulation_range[0] <= chapter_number <= simulation_range[1]
            and (planning_range is None or simulation_range == planning_range)
        ):
            matches.append((payload, path))
    if len(matches) != 1:
        reason = "missing" if not matches else "ambiguous"
        raise ArcSimulationError(f"arc_causal_simulation_{reason}:ch{chapter_number:03d}")
    payload, path = matches[0]
    errors = validate_arc_causal_simulation(
        payload,
        expected_range=planning_range,
        expected_basis=current_basis_hashes(root),
    )
    if errors or payload.get("status") != "approved":
        raise ArcSimulationError("arc_causal_simulation_stale:" + ";".join(errors or ["status is not approved"]))
    return payload, path, sha256(path.read_bytes()).hexdigest()


def current_planning_window_range(root: Path) -> tuple[int, int] | None:
    """Return the authoritative rolling-outline range when it is materialized."""

    path = root / "20_outline" / "planning_window.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    start = payload.get("start_chapter")
    end = payload.get("end_chapter")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or start <= 0
        or not isinstance(end, int)
        or isinstance(end, bool)
        or end < start
    ):
        return None
    return start, end


def permitted_arc_simulation_ranges(root: Path) -> frozenset[tuple[int, int]]:
    """Return the current window and the one legal pre-extension simulation window."""

    path = root / "20_outline" / "planning_window.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return frozenset()
    current = current_planning_window_range(root)
    horizon = payload.get("detailed_horizon") if isinstance(payload, dict) else None
    if current is None or not isinstance(horizon, int) or isinstance(horizon, bool) or horizon <= 0:
        return frozenset()
    return frozenset(
        {
            current,
            (current[1] + 1, current[1] + horizon),
        }
    )


def load_covering_arc_simulation(
    root: Path,
    *,
    from_chapter: int,
    to_chapter: int,
) -> tuple[dict[str, Any], Path, str]:
    """Load the one current simulation that covers an entire planning window."""

    if from_chapter <= 0 or to_chapter < from_chapter:
        raise ArcSimulationError("arc_causal_simulation_invalid_window")
    requested = (from_chapter, to_chapter)
    matches: list[tuple[dict[str, Any], Path]] = []
    for path in sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(payload, dict)
            and payload.get("status") == "approved"
            and (payload.get("from_chapter"), payload.get("to_chapter")) == requested
        ):
            matches.append((payload, path))
    if len(matches) != 1:
        reason = "missing" if not matches else "ambiguous"
        raise ArcSimulationError(
            f"arc_causal_simulation_{reason}:ch{from_chapter:03d}-ch{to_chapter:03d}"
        )
    payload, path = matches[0]
    errors = validate_arc_causal_simulation(
        payload,
        expected_range=requested,
        expected_basis=current_basis_hashes(root),
    )
    if errors:
        raise ArcSimulationError("arc_causal_simulation_stale:" + ";".join(errors))
    return payload, path, sha256(path.read_bytes()).hexdigest()


def mark_overlapping_arc_simulations_stale(root: Path, *, from_chapter: int, to_chapter: int) -> tuple[str, ...]:
    changed: list[str] = []
    for path in sorted((root / SIMULATION_DIR).glob("ch*-ch*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        start, end = int(payload.get("from_chapter") or 0), int(payload.get("to_chapter") or 0)
        if start <= to_chapter and from_chapter <= end and payload.get("status") != "stale":
            payload["status"] = "stale"
            atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            changed.append(path.relative_to(root).as_posix())
    return tuple(changed)


def validate_object_list(value: Any, fields: set[str], label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        return [f"{label} must be a non-empty list"]
    errors: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != fields:
            errors.append(f"{label}[{index}] has invalid fields")
            continue
        for field in fields:
            field_value = item.get(field)
            if field == "participants":
                if not isinstance(field_value, list) or not field_value or any(not isinstance(v, str) or not v.strip() for v in field_value):
                    errors.append(f"{label}[{index}].participants must be a non-empty string list")
            elif field == "chapter_number":
                continue
            elif not isinstance(field_value, str) or not field_value.strip():
                errors.append(f"{label}[{index}].{field} must be non-empty")
    return errors


def validate_ranged_object_list(
    value: Any,
    fields: set[str],
    label: str,
    start: Any,
    end: Any,
) -> list[str]:
    errors = validate_object_list(value, fields, label)
    if not isinstance(value, list) or not isinstance(start, int) or not isinstance(end, int):
        return errors
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        chapter = item.get("chapter_number")
        if not isinstance(chapter, int) or isinstance(chapter, bool) or not start <= chapter <= end:
            errors.append(f"{label}[{index}].chapter_number must stay inside the simulation window")
    return errors


def validate_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item.strip() for item in value):
        return [f"{label} must be a non-empty string list"]
    return []
