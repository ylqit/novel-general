"""Single-source chapter contract projection and integrity checks."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json


CONTRACT_SCHEMA = "chapter_contract_v1"
CONTRACT_FIELDS = (
    "chapter_number",
    "title",
    "book_goal",
    "volume_goal",
    "protagonist_goal",
    "chapter_duty",
    "platform_promise",
    "conflict",
    "information_release",
    "scene_chain",
    "featured_character_ids",
    "reader_gain",
    "cost",
    "relationship_move",
    "canon_refs",
    "world_rule_refs",
    "foreshadow_refs",
    "forbidden_reveals",
)
LIST_FIELDS = frozenset(
    {
        "scene_chain",
        "featured_character_ids",
        "canon_refs",
        "world_rule_refs",
        "foreshadow_refs",
        "forbidden_reveals",
    }
)


class ChapterContractError(ValueError):
    """Raised when chapter production sees a split or incomplete contract."""


def project_chapter_contract(card: dict[str, Any]) -> dict[str, Any]:
    contract: dict[str, Any] = {"schema": CONTRACT_SCHEMA}
    for field in CONTRACT_FIELDS:
        value = card.get(field)
        if field in LIST_FIELDS:
            if not isinstance(value, list):
                raise ChapterContractError(f"chapter_contract_inconsistent:{field}_must_be_list")
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
    return contract, digest


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
