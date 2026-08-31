"""Canonical reader-reward ledger and cross-chapter craft observations."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.storage import atomic_write_text


STRUCTURE_HISTORY = Path("30_state/quality/structure_history.jsonl")
REWARD_LEDGER = Path("30_state/reward_ledger.jsonl")


def build_structure_observation(
    *,
    chapter_number: int,
    text: str,
    card: dict[str, Any],
    review: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a prose-free structure fingerprint for one chapter."""

    craft = review.get("craft_observation", {}) if isinstance(review, dict) else {}
    observed = review.get("observed", {}) if isinstance(review, dict) else {}
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()]
    sentence_lengths = [
        len(item.strip())
        for item in re.split(r"[。！？!?]+", text)
        if item.strip()
    ]
    dialogue_chars = sum(len(item) for item in re.findall(r"[“「『](.*?)[”」』]", text, flags=re.S))
    prose_chars = max(1, len(re.sub(r"\s+", "", text)))
    paragraph_lengths = [len(re.sub(r"\s+", "", item)) for item in paragraphs]
    return {
        "schema": "structure_observation_v3",
        "chapter_number": chapter_number,
        "source_hash": sha256_text(text),
        "chapter_duty": str(card.get("chapter_duty") or ""),
        "opening_mode": str(craft.get("opening_mode") or infer_opening_mode(text)),
        "opening_carrier": str(
            craft.get("opening_carrier")
            or card.get("opening_carrier")
            or infer_opening_mode(text)
        ),
        "scene_function_chain": infer_scene_function_chain(craft, card),
        "character_reaction_mode": str(
            craft.get("character_reaction_mode")
            or card.get("character_reaction_mode")
            or infer_character_reaction_mode(text)
        ),
        "topology_id": str(craft.get("topology_id") or card.get("topology_id") or "unknown"),
        "ending_mode": str(craft.get("ending_mode") or infer_ending_mode(text)),
        "ending_function": str(
            craft.get("ending_function")
            or card.get("ending_function")
            or ending_function(infer_ending_mode(text))
        ),
        "scene_count": int(craft.get("scene_count") or max(1, text.count("\n---\n") + 1)),
        "dominant_scene_type": str(craft.get("dominant_scene_type") or "unreviewed"),
        "primary_story_engine": str(
            craft.get("primary_story_engine") or card.get("primary_story_engine") or "unreviewed"
        ),
        "primary_scene_carrier": str(
            craft.get("primary_scene_carrier")
            or ((card.get("scene_carriers") or [""])[0] if isinstance(card.get("scene_carriers"), list) else "")
            or craft.get("dominant_scene_type")
            or "unreviewed"
        ),
        "state_change_kind": str(
            craft.get("state_change_kind") or card.get("state_change_kind") or "unreviewed"
        ),
        "dramatic_method": str(
            craft.get("dramatic_method") or card.get("dramatic_method") or "unreviewed"
        ),
        "exposition_carrier": str(
            craft.get("exposition_carrier") or card.get("exposition_carrier") or "unreviewed"
        ),
        "reader_gain_position": str(craft.get("reader_gain_position") or "unreviewed"),
        "dialogue_acts": clean_strings(craft.get("dialogue_acts")),
        "emotional_curve": clean_strings(craft.get("emotional_curve")),
        "language_metrics": {
            "sentence_count": len(sentence_lengths),
            "average_sentence_chars": round(sum(sentence_lengths) / max(1, len(sentence_lengths)), 2),
            "paragraph_count": len(paragraphs),
            "average_paragraph_chars": round(sum(paragraph_lengths) / max(1, len(paragraph_lengths)), 2),
            "dialogue_density": round(dialogue_chars / prose_chars, 4),
            "body_reaction_count": count_patterns(
                text,
                ("呼吸", "心跳", "指尖", "喉结", "后背", "手心", "胸口", "眉心"),
            ),
            "pseudo_detail_count": count_patterns(
                text,
                ("某种", "难以言喻", "说不清", "莫名", "复杂的情绪", "意味深长"),
            ),
            "paragraph_shape": paragraph_shape(paragraph_lengths),
            "ngram_signature": ngram_signature(text),
        },
        "observed_gain_present": bool(str(observed.get("reader_gain") or "").strip()),
        "recorded_at": utc_now(),
    }


def record_quality_history(
    root: Path,
    *,
    chapter_number: int,
    final_text: str,
    card: dict[str, Any],
    review: dict[str, Any] | None,
) -> dict[str, str]:
    """Upsert reward v2 and structure history after an explicit chapter finalize."""

    observed = review.get("_cli_observed", {}) if isinstance(review, dict) else {}
    evidence = observed.get("evidence_spans", []) if isinstance(observed, dict) else []
    reward = {
        "schema": "reader_reward_entry_v2",
        "chapter_number": chapter_number,
        "chapter_duty": str(card.get("chapter_duty") or ""),
        "planned_gain": str(card.get("reader_gain") or ""),
        "observed_gain": str(observed.get("reader_gain") or ""),
        "duty_fulfilled": bool(observed.get("reader_gain")) if review else None,
        "planned_cost": str(card.get("cost") or ""),
        "observed_cost": str(observed.get("cost") or ""),
        "promise_progress": [],
        "evidence_source_hash": sha256_text(final_text),
        "evidence_spans": sanitize_evidence_spans(evidence),
        "topology_id": str(card.get("topology_id") or ""),
        "ending_mode": infer_ending_mode(final_text),
        "observation_status": "semantic_reviewed" if review else "not_required",
        "finalized": True,
        "recorded_at": utc_now(),
    }
    observation = build_structure_observation(
        chapter_number=chapter_number,
        text=final_text,
        card=card,
        review=review,
    )
    upsert_jsonl(root / REWARD_LEDGER, reward, chapter_number=chapter_number)
    upsert_jsonl(root / STRUCTURE_HISTORY, observation, chapter_number=chapter_number)
    return {
        "reward_ledger": REWARD_LEDGER.as_posix(),
        "structure_history": STRUCTURE_HISTORY.as_posix(),
    }


def truncate_quality_history(root: Path, *, to_chapter: int) -> tuple[str, ...]:
    """Drop derived quality records for chapters detached by a rollback."""

    changed: list[str] = []
    for relative in (REWARD_LEDGER, STRUCTURE_HISTORY):
        path = root / relative
        records = read_jsonl(path)
        kept = [item for item in records if int(item.get("chapter_number") or 0) <= to_chapter]
        if len(kept) != len(records):
            atomic_write_text(path, serialize_jsonl(kept))
            changed.append(relative.as_posix())
    return tuple(changed)


def upsert_jsonl(path: Path, record: dict[str, Any], *, chapter_number: int) -> None:
    records = [
        item
        for item in read_jsonl(path)
        if int(item.get("chapter_number") or 0) != chapter_number
    ]
    records.append(record)
    records.sort(key=lambda item: int(item.get("chapter_number") or 0))
    atomic_write_text(path, serialize_jsonl(records))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid JSONL object at {path}:{line_number}.")
        records.append(payload)
    return records


def serialize_jsonl(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records)


def ngram_signature(text: str, *, size: int = 4, limit: int = 128) -> list[str]:
    normalized = re.sub(r"\s+", "", text)
    if len(normalized) < size:
        return []
    values = {
        hashlib.sha256(normalized[index:index + size].encode("utf-8")).hexdigest()[:12]
        for index in range(len(normalized) - size + 1)
    }
    return sorted(values)[:limit]


def paragraph_shape(lengths: list[int]) -> list[int]:
    if not lengths:
        return []
    return [min(9, value // 40) for value in lengths[:24]]


def count_patterns(text: str, patterns: tuple[str, ...]) -> int:
    return sum(text.count(pattern) for pattern in patterns)


def infer_scene_function_chain(
    craft: dict[str, Any], card: dict[str, Any]
) -> list[str]:
    for value in (
        craft.get("scene_function_chain"),
        card.get("scene_function_chain"),
        card.get("scene_functions"),
    ):
        items = clean_strings(value)
        if items:
            return items[:6]
    values = [
        str(card.get("chapter_duty") or "").strip(),
        str(card.get("state_change_kind") or "").strip(),
    ]
    return [value for value in values if value][:6] or ["unreviewed"]


def infer_character_reaction_mode(text: str) -> str:
    body = re.sub(r"^#.*?\n", "", text.strip(), count=1)
    scores = {
        "acts_under_pressure": len(re.findall(r"(抓|推|走|冲|挡|拿|放|转身|拒绝|答应)", body)),
        "speaks_under_pressure": len(re.findall(r"[“「『].*?[”」』]", body, flags=re.S)),
        "withholds_and_observes": len(re.findall(r"(沉默|没说|没有回答|看着|盯着|避开)", body)),
        "internalizes": len(re.findall(r"(想到|意识到|明白|记起|心里)", body)),
    }
    return max(scores, key=scores.get) if any(scores.values()) else "unreviewed"


def ending_function(mode: str) -> str:
    return {
        "decision": "commitment",
        "reveal": "information_shift",
        "threat": "pressure_escalation",
        "question": "dramatic_question",
        "closure": "aftereffect_or_settlement",
    }.get(mode, "unreviewed")


def infer_opening_mode(text: str) -> str:
    body = re.sub(r"^#.*?\n", "", text.strip(), count=1).lstrip()
    if body.startswith(("“", "「", "『")):
        return "dialogue"
    if re.search(r"(走|冲|抓|推|抬|砸|拔|奔|跑|撞)", body[:80]):
        return "action"
    if re.search(r"(发现|看见|听见|闻到|察觉)", body[:100]):
        return "discovery"
    return "description"


def infer_ending_mode(text: str) -> str:
    tail = re.sub(r"\s+", "", text)[-160:]
    if tail.endswith(("？", "?")):
        return "question"
    if re.search(r"(决定|选择|答应|拒绝|转身|出发)", tail):
        return "decision"
    if re.search(r"(原来|竟是|名字|真相|发现)", tail):
        return "reveal"
    if re.search(r"(刀|杀|追来|危险|期限|来不及)", tail):
        return "threat"
    return "closure"


def sanitize_evidence_spans(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "start": int(item.get("start") or 0),
                "end": int(item.get("end") or 0),
                "supports": clean_strings(item.get("supports")),
            }
        )
    return result


def clean_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
