"""Compile platform, genre, story-phase, and approved-style quality contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import copy
import json
from pathlib import Path
from typing import Any

import yaml

from longform_engine.config import ConfigDocument
from longform_engine.resources import resource_path
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root


MARKET_PROFILE_IDS = ("general_cn", "qidian_male", "fanqie_free", "jinjiang_female")
GENRE_PROFILE_IDS = ("xuanhuan", "urban", "suspense", "romance", "history")
STORY_PHASE_IDS = ("opening", "early_serial", "stable_serial", "volume_climax", "aftermath")
QUALITY_STRICTNESS = ("light", "balanced", "strict")
APPROVED_BASELINE_PATH = "10_bible/style_profiles/approved_style_baseline.json"


@dataclass(frozen=True)
class StyleBaselineApprovalResult:
    chapter_number: int
    baseline_file: str
    approved_by: str
    transaction_report: str
    next_command: str


def compile_effective_quality_contract(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> dict[str, Any]:
    """Return the deterministic quality contract for one chapter."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    quality = config.data.get("quality")
    quality = quality if isinstance(quality, dict) else {}
    profile = quality.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    market = str(profile.get("market") or quality.get("market_profile") or "general_cn")
    genre = str(profile.get("genre") or quality.get("genre_profile") or "xuanhuan")
    configured_phase = str(profile.get("phase") or "auto")
    strictness = str(profile.get("strictness") or quality.get("assurance_mode") or "balanced")
    if market not in MARKET_PROFILE_IDS:
        raise ValueError(f"Unknown quality market profile: {market}")
    if genre not in GENRE_PROFILE_IDS:
        raise ValueError(f"Unknown quality genre profile: {genre}")
    if strictness not in QUALITY_STRICTNESS:
        raise ValueError(f"Unknown quality strictness: {strictness}")
    phase = (
        infer_story_phase(config, chapter_number)
        if configured_phase == "auto"
        else configured_phase
    )
    if phase not in STORY_PHASE_IDS:
        raise ValueError(f"Unknown quality story phase: {phase}")

    sources = [
        load_quality_profile("markets", market),
        load_quality_profile("genres", genre),
        load_quality_profile("phases", phase),
    ]
    contract: dict[str, Any] = {}
    source_records: list[dict[str, str]] = []
    for payload, path, digest in sources:
        deep_merge(contract, payload["contract"])
        source_records.append(
            {
                "kind": str(payload["kind"]),
                "id": str(payload["id"]),
                "path": path,
                "sha256": digest,
            }
        )

    root = resolve_project_root(config)
    baseline = load_approved_style_baseline(root)
    baseline_contract = baseline.get("contract_overrides")
    if isinstance(baseline_contract, dict):
        deep_merge(contract, baseline_contract)
    project_overrides = profile.get("overrides")
    if isinstance(project_overrides, dict):
        deep_merge(contract, project_overrides)

    approved_records = baseline.get("approved_chapters")
    approved_records = approved_records if isinstance(approved_records, list) else []
    return {
        "schema": "effective_quality_contract_v1",
        "chapter_number": chapter_number,
        "market": market,
        "genre": genre,
        "phase": phase,
        "strictness": strictness,
        "contract": contract,
        "approved_style_baseline": {
            "source": APPROVED_BASELINE_PATH if approved_records else "",
            "approved_chapter_count": len(approved_records),
            "approved_chapters": [
                int(item["chapter_number"])
                for item in approved_records
                if isinstance(item, dict) and isinstance(item.get("chapter_number"), int)
            ],
            "observations": compact_baseline_observations(approved_records),
            "auto_expand": False,
        },
        "sources": source_records,
        "merge_order": [
            "market",
            "genre",
            "phase",
            "user_approved_style_baseline",
            "project_overrides",
        ],
    }


def approve_style_baseline(
    config: ConfigDocument,
    *,
    chapter_number: int,
    approved_by: str,
) -> StyleBaselineApprovalResult:
    """Explicitly add one finalized chapter's prose-free craft fingerprint to the baseline."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    approved_by = str(approved_by or "").strip()
    if not approved_by:
        raise ValueError("approved_by is required.")
    root = resolve_project_root(config)
    finalization_path = root / "40_manuscript" / ("fin" + "al") / f"ch{chapter_number:03d}.finalization.json"
    finalization = read_json(finalization_path, {})
    if not isinstance(finalization, dict) or not finalization.get("final_sha256"):
        raise ValueError(f"Chapter ch{chapter_number:03d} must be explicitly finalized before baseline approval.")
    observation = finalized_structure_observation(root, chapter_number)
    if observation is None:
        raise ValueError(
            f"Chapter ch{chapter_number:03d} has no finalized structure observation; finalize it with quality history enabled."
        )

    baseline_path = root / APPROVED_BASELINE_PATH
    baseline = load_approved_style_baseline(root)
    records = baseline.get("approved_chapters")
    records = list(records) if isinstance(records, list) else []
    record = {
        "chapter_number": chapter_number,
        "final_sha256": str(finalization["final_sha256"]),
        "approved_by": approved_by,
        "approved_at": utc_now(),
        "observation": prose_free_observation(observation),
    }
    records = [
        item
        for item in records
        if not isinstance(item, dict) or int(item.get("chapter_number") or 0) != chapter_number
    ]
    records.append(record)
    records.sort(key=lambda item: int(item.get("chapter_number") or 0))
    payload = {
        "schema": "approved_style_baseline_v1",
        "auto_expand": False,
        "update_requires_human": True,
        "approved_chapters": records,
        "contract_overrides": baseline.get("contract_overrides") if isinstance(baseline.get("contract_overrides"), dict) else {},
        "updated_at": utc_now(),
    }
    with apply_transaction(
        root,
        command="quality baseline-approve",
        chapter_number=chapter_number,
        source_paths=(finalization_path, root / "30_state" / "quality" / "structure_history.jsonl"),
        touched_paths=(baseline_path,),
        metadata={"approved_by": approved_by, "auto_expand": False},
    ) as transaction:
        write_json(baseline_path, payload)
    return StyleBaselineApprovalResult(
        chapter_number=chapter_number,
        baseline_file=APPROVED_BASELINE_PATH,
        approved_by=approved_by,
        transaction_report=relative(root, transaction.report_file),
        next_command="longform-engine production next project.yaml",
    )


def infer_story_phase(config: ConfigDocument, chapter_number: int) -> str:
    root = resolve_project_root(config)
    volumes = read_json(root / "20_outline" / "volumes.json", [])
    if isinstance(volumes, list):
        boundaries = {
            int(item.get("to_chapter") or 0)
            for item in volumes
            if isinstance(item, dict) and int(item.get("to_chapter") or 0) > 0
        }
        if chapter_number in boundaries:
            return "volume_climax"
        if chapter_number > 1 and chapter_number - 1 in boundaries:
            return "aftermath"
    length = config.data.get("length")
    length = length if isinstance(length, dict) else {}
    total = max(1, int(length.get("total_chapters") or 1))
    opening_end = min(total, max(3, int(length.get("new_book_phase_chapters") or round(total * 0.06))))
    if chapter_number <= opening_end:
        return "opening"
    if chapter_number <= max(opening_end + 1, round(total * 0.25)):
        return "early_serial"
    return "stable_serial"


def load_quality_profile(kind: str, profile_id: str) -> tuple[dict[str, Any], str, str]:
    path = resource_path("config", "quality_profiles", kind, f"{profile_id}.yaml")
    raw = path.read_bytes()
    payload = yaml.safe_load(raw.decode("utf-8")) or {}
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "quality_profile_v1"
        or payload.get("kind") != kind.rstrip("s")
        or payload.get("id") != profile_id
        or not isinstance(payload.get("contract"), dict)
    ):
        raise ValueError(f"Invalid quality profile resource: {path}")
    return payload, f"config/quality_profiles/{kind}/{profile_id}.yaml", sha256(raw).hexdigest()


def load_approved_style_baseline(root: Path) -> dict[str, Any]:
    payload = read_json(root / APPROVED_BASELINE_PATH, {})
    if not isinstance(payload, dict) or payload.get("schema") != "approved_style_baseline_v1":
        return {
            "schema": "approved_style_baseline_v1",
            "auto_expand": False,
            "update_requires_human": True,
            "approved_chapters": [],
            "contract_overrides": {},
        }
    return payload


def finalized_structure_observation(root: Path, chapter_number: int) -> dict[str, Any] | None:
    path = root / "30_state" / "quality" / "structure_history.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and int(payload.get("chapter_number") or 0) == chapter_number:
            return payload
    return None


def prose_free_observation(observation: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "opening_mode",
        "topology_id",
        "ending_mode",
        "scene_count",
        "scene_types",
        "payoff_position",
        "emotional_curve",
        "dialogue_acts",
        "sentence_length",
        "paragraph_length",
        "dialogue_ratio",
        "paragraph_shape",
    }
    return {
        key: copy.deepcopy(value)
        for key, value in observation.items()
        if key in allowed
    }


def compact_baseline_observations(records: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in records[-8:]:
        if not isinstance(item, dict) or not isinstance(item.get("observation"), dict):
            continue
        result.append(
            {
                "chapter_number": int(item.get("chapter_number") or 0),
                "observation": prose_free_observation(item["observation"]),
            }
        )
    return result


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
