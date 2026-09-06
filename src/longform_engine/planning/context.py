"""Validated, read-only chapter planning facts shared by production consumers.

This is an internal projection of approved planning, never a second chapter
contract. Creative choices that planning does not contain are not inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.chapter_contract import ChapterContractError, load_verified_chapter_contract
from longform_engine.planning.contracts import validate_volume_plan, validate_volume_skeletons


@dataclass(frozen=True)
class ChapterPlanningContext:
    chapter_number: int
    contract: dict[str, Any]
    contract_sha256: str
    volume: dict[str, Any]
    skeleton: dict[str, Any]
    nodes: tuple[dict[str, Any], ...]
    obligations: tuple[dict[str, Any], ...]
    character_ids: tuple[str, ...]
    scene_ids: tuple[str, ...]
    event_ids: tuple[str, ...]
    arc_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_files: tuple[dict[str, str], ...]

    @property
    def volume_id(self) -> str:
        return str(self.volume["volume_id"])

    @property
    def is_volume_start(self) -> bool:
        return self.chapter_number == self.skeleton["chapter_range"][0]

    @property
    def is_volume_end(self) -> bool:
        return self.chapter_number == self.skeleton["chapter_range"][1]

    def review_projection(self) -> dict[str, Any]:
        """Expose approved chapter scope without importing unrelated future volume rows."""
        return {
            "volume": {
                "volume_id": self.volume_id,
                "title": self.skeleton["title"],
                "chapter_range": self.skeleton["chapter_range"],
                "volume_goal": self.skeleton["volume_goal"],
                "entry_state": self.volume["entry_state"],
                "exit_state": self.volume["exit_state"],
                "is_volume_start": self.is_volume_start,
                "is_volume_end": self.is_volume_end,
            },
            "approved_nodes": list(self.nodes),
            "semantic_obligations": list(self.obligations),
            "character_arcs": [
                row for row in self.volume["character_arcs"] if row.get("id") in self.arc_ids
            ],
            "volume_events": [
                row for row in self.volume["event_graph"] if row.get("id") in self.event_ids
            ],
        }


def load_chapter_planning_context(root: Path, chapter_number: int) -> ChapterPlanningContext:
    """Resolve one approved chapter, rejecting ambiguous, missing or stale facts."""

    if isinstance(chapter_number, bool) or not isinstance(chapter_number, int) or chapter_number <= 0:
        raise ChapterContractError("planning_context_invalid_chapter")
    sources: dict[str, str] = {}

    def read(path: Path) -> dict[str, Any]:
        try:
            content = path.read_bytes()
            payload = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ChapterContractError(f"planning_context_unreadable:{path.relative_to(root).as_posix()}:{exc}") from exc
        if not isinstance(payload, dict):
            raise ChapterContractError(f"planning_context_invalid:{path.relative_to(root).as_posix()}")
        sources[path.relative_to(root).as_posix()] = sha256(content).hexdigest()
        return payload

    contract, digest = load_verified_chapter_contract(root, chapter_number)
    outline = root / "20_outline"
    read(outline / "chapter_contracts" / f"ch{chapter_number:03d}.json")
    skeletons = read(outline / "volume_skeletons.json")
    errors: list[str] = []
    skeleton_ids = validate_volume_skeletons(skeletons, errors)
    if errors:
        raise ChapterContractError("planning_context_skeleton_invalid:" + ";".join(errors))
    matching = [row for row in skeletons["items"] if row["chapter_range"][0] <= chapter_number <= row["chapter_range"][1]]
    if len(matching) != 1:
        raise ChapterContractError("planning_context_requires_unique_volume")
    skeleton = matching[0]
    volumes = [(path, read(path)) for path in sorted((outline / "volumes").glob("vol*.json"))]
    if sum(value.get("lifecycle") == "active" for _, value in volumes) > 1:
        raise ChapterContractError("planning_context_multiple_active_volumes")
    selected = [(path, value) for path, value in volumes if value.get("volume_id") == skeleton["volume_id"]]
    if len(selected) != 1:
        raise ChapterContractError("planning_context_requires_unique_approved_volume")
    volume_path, volume = selected[0]
    validate_volume_plan(volume, errors, skeleton_ids=skeleton_ids)
    if errors or volume.get("lifecycle") == "proposed" or volume.get("approved_by") != "human":
        raise ChapterContractError("planning_context_volume_unapproved_or_invalid:" + ";".join(errors))
    if volume.get("chapter_range") != skeleton["chapter_range"]:
        raise ChapterContractError("planning_context_volume_range_mismatch")
    # Other volumes affect uniqueness, but not this chapter's prose basis.
    for path, _ in volumes:
        if path != volume_path:
            sources.pop(path.relative_to(root).as_posix(), None)
    basis = read(root / "30_state" / "planning_basis.json")
    if basis.get("schema") != "planning_basis_v1":
        raise ChapterContractError("planning_context_basis_incompatible")
    bindings = basis.get("source_files")
    if not isinstance(bindings, list) or not bindings or any(
        not isinstance(item, dict) or set(item) != {"path", "sha256"}
        or not isinstance(item["path"], str) or not item["path"]
        or not isinstance(item["sha256"], str)
        for item in bindings
    ):
        raise ChapterContractError("planning_context_basis_bindings_invalid")
    if len({item["path"] for item in bindings}) != len(bindings):
        raise ChapterContractError("planning_context_basis_duplicate_bindings")
    required = {"20_outline/volume_skeletons.json", volume_path.relative_to(root).as_posix(),
                "20_outline/rolling_window.json", "30_state/semantic_obligations.json",
                f"20_outline/plot_nodes/ch{chapter_number:03d}.json",
                f"20_outline/chapter_contracts/ch{chapter_number:03d}.json"}
    if not required <= {item["path"] for item in bindings}:
        raise ChapterContractError("planning_context_approved_volume_binding_missing; rebuild and approve planning")
    for record in bindings:
        relative = str(record.get("path") or "")
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ChapterContractError("planning_context_basis_path_escape")
        try:
            actual = sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ChapterContractError(f"planning_context_basis_missing:{relative}") from exc
        if actual != record.get("sha256"):
            raise ChapterContractError(f"planning_context_basis_stale:{relative}")
        sources[relative] = actual
    if "20_outline/volume_skeletons.json" not in {item.get("path") for item in basis.get("source_files") or []}:
        raise ChapterContractError("planning_context_skeleton_binding_missing")
    node_table = read(outline / "plot_nodes" / f"ch{chapter_number:03d}.json")
    if node_table.get("schema") != "plot_node_table_v1" or node_table.get("chapter_number") != chapter_number:
        raise ChapterContractError("planning_context_node_table_invalid")
    nodes = tuple(node_table["nodes"])
    ledger = read(root / "30_state" / "semantic_obligations.json")
    if ledger.get("schema") != "semantic_obligation_ledger_v1":
        raise ChapterContractError("planning_context_obligations_incompatible")
    by_id = {item["obligation_id"]: item for item in ledger.get("items") or []}
    obligations = tuple(by_id[ref] for ref in contract["semantic_obligation_refs"])
    characters = tuple(dict.fromkeys(str(actor) for node in nodes for actor in node.get("actors") or []))
    references = {str(item) for node in nodes for field in ("actors", "dependency_refs", "obligation_refs") for item in node.get(field) or []}
    references.update(str(item) for obligation in obligations for field in ("subject_refs", "prior_state_refs", "dependency_refs") for item in obligation.get(field) or [])
    references.update(str(node.get("node_id") or "") for node in nodes)

    def strings(value: Any) -> set[str]:
        if isinstance(value, str):
            return {value}
        if isinstance(value, dict):
            return set().union(*(strings(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(strings(item) for item in value))
        return set()

    arcs = tuple(str(row["id"]) for row in volume.get("character_arcs") or [] if row.get("id") and strings(row) & references)
    events = tuple(dict.fromkeys([*(str(node["node_id"]) for node in nodes), *(str(row["id"]) for row in volume.get("event_graph") or [] if row.get("id") and strings(row) & references)]))
    source_references = references | set(contract["fanfiction_claim_refs"]["all_claim_refs"])
    source_ids: tuple[str, ...] = ()
    if contract["fanfiction_claim_refs"]["all_claim_refs"]:
        from longform_engine.semantic_protocols import validate_semantic_document
        claims: list[dict[str, Any]] = []
        configured_sources: set[str] = set()
        for filename in ("source_canon.json", "story_engine.json", "fanfiction_bible.json"):
            document = read(root / "10_bible/fanfiction" / filename)
            errors = validate_semantic_document(document, require_approved=True)
            if errors:
                raise ChapterContractError("planning_context_fanfiction_source_invalid:" + ";".join(errors))
            claims.extend(document["claims"])
            if filename == "source_canon.json":
                source_contracts = document.get("extensions", {}).get("source_contracts")
                if not isinstance(source_contracts, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("source_id"), str)
                    or not item["source_id"].strip() for item in source_contracts
                ):
                    raise ChapterContractError("planning_context_source_contracts_invalid")
                source_keys = [item["source_id"] for item in source_contracts]
                if len(source_keys) != len(set(source_keys)):
                    raise ChapterContractError("planning_context_source_contracts_duplicate")
                configured_sources.update(source_keys)
        source_ids = tuple(sorted(resolve_planning_source_references(source_references, claims, configured_sources)))
    stale_path = root / "30_state" / "stale_artifacts.json"
    if stale_path.is_file():
        stale = read(stale_path)
        affected = [item.get("artifact_path") for item in stale.get("items") or [] if item.get("artifact_path") in sources]
        sources.pop(stale_path.relative_to(root).as_posix(), None)
        if affected:
            raise ChapterContractError("planning_context_stale:" + ",".join(affected))
    return ChapterPlanningContext(
        chapter_number, contract, digest, volume, skeleton, nodes, obligations,
        characters, tuple(dict.fromkeys(str(node["scene_id"]) for node in nodes if node.get("scene_id"))),
        events, arcs, source_ids,
        tuple({"path": path, "sha256": value} for path, value in sorted(sources.items())),
    )


def resolve_planning_source_references(
    references: set[str], claims: list[dict[str, Any]], configured_sources: set[str],
) -> set[str]:
    """Resolve explicit source, identity and claim IDs through approved structured metadata."""
    identity_sources: dict[str, str] = {}
    for claim in claims:
        extensions = claim.get("extensions") or {}
        identity = extensions.get("identity") or {}
        source = str(identity.get("source_id") or extensions.get("source_id") or "")
        if not source:
            continue
        for reference in (identity.get("identity_id"), claim.get("claim_id")):
            if not reference:
                continue
            if reference in identity_sources and identity_sources[reference] != source:
                raise ChapterContractError(f"planning_context_conflicting_source_identity:{reference}")
            identity_sources[str(reference)] = source
    return {identity_sources[ref] for ref in references if ref in identity_sources} | (references & configured_sources)
