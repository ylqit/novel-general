"""Single-source chapter contract projection and integrity checks."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json
import re

from longform_engine.arc_simulation import ArcSimulationError, load_active_arc_simulation
from longform_engine.reader_promises import (
    ACTION_FIELDS,
    PROMISE_ACTIONS,
    ReaderPromiseError,
    load_reader_promise_ledger,
    validate_promise_actions,
)


CONTRACT_SCHEMA = "chapter_contract_v3"
REMOVED_ALIAS_FIELDS = frozenset(
    {"duty", "information", "information_release", "reader_payoff"}
)
CONTRACT_FIELDS = (
    "chapter_number",
    "title",
    "book_goal",
    "volume_goal",
    "protagonist_goal",
    "chapter_duty",
    "platform_promise",
    "immediate_desire",
    "opposition_force",
    "dramatic_question",
    "conflict",
    "key_failure",
    "irreversible_choice",
    "chapter_turn",
    "reveal_boundary",
    "scene_chain",
    "must_dramatize",
    "may_summarize",
    "primary_story_engine",
    "scene_carriers",
    "protected_story_outcomes",
    "prohibited_drift",
    "featured_character_ids",
    "reader_gain",
    "cost",
    "state_change_kind",
    "dramatic_method",
    "exposition_carrier",
    "relationship_move",
    "canon_refs",
    "world_rule_refs",
    "foreshadow_refs",
    "forbidden_reveals",
    "reader_promise_actions",
    "arc_simulation_ref",
)
LIST_FIELDS = frozenset(
    {
        "scene_chain",
        "must_dramatize",
        "may_summarize",
        "scene_carriers",
        "protected_story_outcomes",
        "prohibited_drift",
        "featured_character_ids",
        "canon_refs",
        "world_rule_refs",
        "foreshadow_refs",
        "forbidden_reveals",
        "reader_promise_actions",
    }
)
NON_EMPTY_LIST_FIELDS = frozenset(
    {
        "scene_chain",
        "must_dramatize",
        "scene_carriers",
        "protected_story_outcomes",
        "prohibited_drift",
        "featured_character_ids",
        "reader_promise_actions",
    }
)
SCENE_FIELDS = frozenset(
    {
        "scene_id", "location", "participants", "carrier", "desire_collision",
        "action", "reaction", "choice", "cost", "turn", "exit_state",
    }
)


class ChapterContractError(ValueError):
    """Raised when chapter production sees a split or incomplete contract."""


def project_chapter_contract(card: dict[str, Any]) -> dict[str, Any]:
    removed_aliases = sorted(REMOVED_ALIAS_FIELDS & set(card))
    if removed_aliases:
        raise ChapterContractError(
            "chapter_contract_inconsistent:removed_alias_present:" + ",".join(removed_aliases)
        )
    contract: dict[str, Any] = {"schema": CONTRACT_SCHEMA}
    for field in CONTRACT_FIELDS:
        value = card.get(field)
        if field in LIST_FIELDS:
            if not isinstance(value, list):
                raise ChapterContractError(f"chapter_contract_inconsistent:{field}_must_be_list")
            if field in NON_EMPTY_LIST_FIELDS and not value:
                raise ChapterContractError(f"chapter_contract_inconsistent:{field}_missing")
            if field == "scene_chain":
                validate_scene_chain(value)
            elif field == "reader_promise_actions":
                validate_promise_action_shape(value)
            elif any(not isinstance(item, str) or not item.strip() for item in value):
                raise ChapterContractError(f"chapter_contract_inconsistent:{field}_must_be_string_list")
            contract[field] = value
        elif field == "arc_simulation_ref":
            validate_arc_simulation_ref(value, int(card.get("chapter_number") or 0))
            contract[field] = value
        else:
            if field == "chapter_number":
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    raise ChapterContractError("chapter_contract_inconsistent:chapter_number")
                contract[field] = value
            elif not isinstance(value, str) or not value.strip():
                raise ChapterContractError(f"chapter_contract_inconsistent:{field}_missing")
            else:
                contract[field] = value.strip()
    return contract


def validate_scene_chain(scenes: list[Any]) -> None:
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict) or set(scene) != SCENE_FIELDS:
            raise ChapterContractError(
                f"chapter_contract_inconsistent:scene_chain_{index}_fields"
            )
        participants = scene.get("participants")
        if not isinstance(participants, list) or not participants or any(
            not isinstance(item, str) or not item.strip() for item in participants
        ):
            raise ChapterContractError(
                f"chapter_contract_inconsistent:scene_chain_{index}_participants"
            )
        for field in SCENE_FIELDS - {"participants"}:
            if not isinstance(scene.get(field), str) or not scene[field].strip():
                raise ChapterContractError(
                    f"chapter_contract_inconsistent:scene_chain_{index}_{field}"
                )


def validate_promise_action_shape(actions: list[Any]) -> None:
    for index, action in enumerate(actions):
        if not isinstance(action, dict) or set(action) != ACTION_FIELDS:
            raise ChapterContractError(
                f"chapter_contract_inconsistent:reader_promise_actions_{index}_fields"
            )
        if action.get("action") not in PROMISE_ACTIONS:
            raise ChapterContractError(
                f"chapter_contract_inconsistent:reader_promise_actions_{index}_action"
            )
        for field in ("promise_id", "intended_reader_gain", "evidence_requirement"):
            if not isinstance(action.get(field), str) or not action[field].strip():
                raise ChapterContractError(
                    f"chapter_contract_inconsistent:reader_promise_actions_{index}_{field}"
                )
        if action.get("action") == "defer":
            if not isinstance(action.get("defer_reason"), str) or not action["defer_reason"].strip():
                raise ChapterContractError(
                    f"chapter_contract_inconsistent:reader_promise_actions_{index}_defer_reason"
                )
        elif action.get("defer_reason") not in {"", None}:
            raise ChapterContractError(
                f"chapter_contract_inconsistent:reader_promise_actions_{index}_unexpected_defer_reason"
            )


def validate_arc_simulation_ref(value: Any, chapter_number: int) -> None:
    fields = {"path", "sha256", "from_chapter", "to_chapter"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ChapterContractError("chapter_contract_inconsistent:arc_simulation_ref_fields")
    if not isinstance(value.get("path"), str) or not value["path"].strip():
        raise ChapterContractError("chapter_contract_inconsistent:arc_simulation_ref_path")
    if not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise ChapterContractError("chapter_contract_inconsistent:arc_simulation_ref_sha256")
    start, end = value.get("from_chapter"), value.get("to_chapter")
    if not isinstance(start, int) or not isinstance(end, int) or not start <= chapter_number <= end:
        raise ChapterContractError("chapter_contract_inconsistent:arc_simulation_ref_range")


def chapter_contract_hash(contract: dict[str, Any]) -> str:
    return sha256(
        json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def stamp_chapter_contract(card: dict[str, Any]) -> dict[str, Any]:
    contract = project_chapter_contract(card)
    card["chapter_contract_hash"] = chapter_contract_hash(contract)
    return contract


def load_verified_chapter_contract(root: Path, chapter_number: int) -> tuple[dict[str, Any], str]:
    path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ChapterContractError(f"chapter_contract_inconsistent:{exc}") from exc
    if not isinstance(card, dict) or card.get("chapter_number") != chapter_number:
        raise ChapterContractError("chapter_contract_inconsistent:chapter_number")
    contract = project_chapter_contract(card)
    digest = chapter_contract_hash(contract)
    if card.get("chapter_contract_hash") != digest:
        raise ChapterContractError("chapter_contract_inconsistent:hash")
    validate_contract_planning_dependencies(root, contract, chapter_number)
    return contract, digest


def validate_contract_planning_dependencies(
    root: Path,
    contract: dict[str, Any],
    chapter_number: int,
) -> None:
    try:
        ledger = load_reader_promise_ledger(root)
    except ReaderPromiseError as exc:
        raise ChapterContractError(f"chapter_contract_inconsistent:{exc}") from exc
    promise_errors = validate_promise_actions(contract.get("reader_promise_actions"), ledger)
    if promise_errors:
        raise ChapterContractError(
            "chapter_contract_inconsistent:" + ";".join(promise_errors)
        )
    try:
        simulation, path, digest = load_active_arc_simulation(root, chapter_number=chapter_number)
    except ArcSimulationError as exc:
        raise ChapterContractError(f"chapter_contract_inconsistent:{exc}") from exc
    reference = contract.get("arc_simulation_ref") or {}
    expected_path = path.relative_to(root).as_posix()
    if (
        reference.get("path") != expected_path
        or reference.get("sha256") != digest
        or reference.get("from_chapter") != simulation.get("from_chapter")
        or reference.get("to_chapter") != simulation.get("to_chapter")
    ):
        raise ChapterContractError("chapter_contract_inconsistent:arc_simulation_ref_stale")


def resolve_chapter_contract_refs(root: Path, contract: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve every declared canon, world-rule, and foreshadow ref without truncation."""

    groups = (
        ("canon", contract.get("canon_refs") or [], canon_reference_sources(root)),
        ("world_rule", contract.get("world_rule_refs") or [], world_rule_sources(root)),
        ("foreshadow", contract.get("foreshadow_refs") or [], foreshadow_sources(root)),
    )
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for kind, refs, sources in groups:
        for raw_ref in refs:
            ref = str(raw_ref or "").strip().replace("\\", "/")
            if not ref:
                continue
            path = (root / ref).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError:
                missing.append(f"{kind}:{ref}")
                continue
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                reject_depth_limited(kind, ref, text)
                resolved.append(
                    {
                        "kind": kind,
                        "ref": ref,
                        "source": ref,
                        "sha256": sha256(path.read_bytes()).hexdigest(),
                        "value": text,
                    }
                )
                continue
            matches = find_records_by_id(sources, ref)
            if not matches:
                missing.append(f"{kind}:{ref}")
                continue
            if kind != "foreshadow" and len(matches) > 1:
                raise ChapterContractError(f"context_evidence_incomplete:ambiguous_ref:{kind}:{ref}")
            source_paths = [item[0] for item in matches]
            if kind == "foreshadow":
                value: Any = {
                    "thread_id": ref,
                    "plan": next(
                        (record for source, record in matches if source.name == "foreshadowing_ledger.json"),
                        None,
                    ),
                    "current_state": next(
                        (record for source, record in matches if source.name == "foreshadowing_state.json"),
                        None,
                    ),
                }
            else:
                value = matches[0][1]
            reject_depth_limited(kind, ref, value)
            source_names = [path.relative_to(root).as_posix() for path in source_paths]
            resolved.append(
                {
                    "kind": kind,
                    "ref": ref,
                    "source": source_names[0] if len(source_names) == 1 else source_names,
                    "sha256": sha256(
                        "\n".join(sha256(path.read_bytes()).hexdigest() for path in source_paths).encode("ascii")
                    ).hexdigest(),
                    "value": value,
                }
            )
    if missing:
        raise ChapterContractError(
            "context_evidence_incomplete:unresolved_refs:" + ",".join(sorted(missing))
        )
    return resolved


def canon_reference_sources(root: Path) -> list[Path]:
    return existing_files(
        root,
        (
            "10_bible/fanfiction/source_canon.json",
            "10_bible/fanfiction/fanfiction_bible.json",
            "10_bible/research_canon.json",
        ),
    )


def world_rule_sources(root: Path) -> list[Path]:
    return existing_files(
        root,
        (
            "10_bible/fanfiction/source_canon.json",
            "10_bible/fanfiction/fanfiction_bible.json",
            "10_bible/abilities.json",
            "10_bible/world_rules.json",
        ),
    )


def foreshadow_sources(root: Path) -> list[Path]:
    return existing_files(
        root,
        (
            "20_outline/foreshadowing_ledger.json",
            "30_state/foreshadowing_state.json",
        ),
    )


def existing_files(root: Path, relatives: Iterable[str]) -> list[Path]:
    return [root / relative for relative in relatives if (root / relative).is_file()]


def reject_depth_limited(kind: str, ref: str, value: Any) -> None:
    serialized = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    if "[depth-limited]" in serialized:
        raise ChapterContractError(f"context_evidence_incomplete:depth_limited:{kind}:{ref}")


def find_records_by_id(sources: Iterable[Path], expected_id: str) -> list[tuple[Path, dict[str, Any]]]:
    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sources:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        for record in iter_id_records(payload):
            if str(record.get("id") or record.get("thread_id") or "") == expected_id:
                matches.append((path, record))
    source_counts: dict[Path, int] = {}
    for path, _record in matches:
        source_counts[path] = source_counts.get(path, 0) + 1
    duplicate_sources = {path for path, count in source_counts.items() if count > 1}
    if duplicate_sources:
        names = ",".join(sorted(path.as_posix() for path in duplicate_sources))
        raise ChapterContractError(f"context_evidence_incomplete:duplicate_ref:{expected_id}:{names}")
    return matches


def iter_id_records(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get("id") or value.get("thread_id"):
            yield value
        for child in value.values():
            yield from iter_id_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_id_records(child)
