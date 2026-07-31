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
        "schema": "structure_observation_v1",
        "chapter_number": chapter_number,
        "source_hash": sha256_text(text),
        "chapter_duty": str(card.get("chapter_duty") or card.get("duty") or ""),
        "opening_mode": str(craft.get("opening_mode") or infer_opening_mode(text)),
        "topology_id": str(craft.get("topology_id") or card.get("topology_id") or "unknown"),
        "ending_mode": str(craft.get("ending_mode") or infer_ending_mode(text)),
        "scene_count": int(craft.get("scene_count") or max(1, text.count("\n---\n") + 1)),
        "dominant_scene_type": str(craft.get("dominant_scene_type") or "unreviewed"),
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


def analyze_structure_pattern(
    root: Path,
    observation: dict[str, Any],
    *,
    window: int = 20,
    language_similarity_threshold: float = 0.72,
) -> dict[str, Any]:
    """Detect repeated structure+language+payoff combinations without prescribing a template."""

    history = read_jsonl(root / STRUCTURE_HISTORY)[-max(2, window - 1):]
    sequence = [*history, observation]
    findings: list[dict[str, Any]] = []
    repeated_dimensions: list[str] = []
    for field in ("opening_mode", "topology_id", "ending_mode", "reader_gain_position"):
        value = str(observation.get(field) or "")
        streak = trailing_streak(sequence, field, value)
        if value not in {"", "unknown", "unreviewed", "other"} and streak >= 3:
            repeated_dimensions.append(field)
            findings.append(
                {
                    "code": f"repeated_{field}",
                    "severity": "P2",
                    "message": f"{field} repeats for {streak} consecutive finalized/candidate chapters.",
                    "chapters": [int(item.get("chapter_number") or 0) for item in sequence[-streak:]],
                }
            )

    recent = sequence[-3:]
    language_scores = [
        language_similarity(recent[index - 1], recent[index])
        for index in range(1, len(recent))
    ]
    language_repeated = (
        len(recent) == 3
        and bool(language_scores)
        and min(language_scores) >= language_similarity_threshold
    )
    structure_repeated = len(repeated_dimensions) >= 2
    payoff_repeated = "reader_gain_position" in repeated_dimensions
    if structure_repeated and language_repeated and payoff_repeated:
        findings.append(
            {
                "code": "combined_formula_repetition",
                "severity": "P1",
                "message": (
                    "Structure, language shape, and payoff position repeat together across three chapters; "
                    "revise causal scene construction instead of swapping surface wording."
                ),
                "chapters": [int(item.get("chapter_number") or 0) for item in recent],
            }
        )
    return {
        "schema": "structure_repetition_analysis_v1",
        "chapter_number": int(observation.get("chapter_number") or 0),
        "window_size": window,
        "language_similarity_threshold": language_similarity_threshold,
        "history_count": len(history),
        "repeated_dimensions": repeated_dimensions,
        "language_similarity": [round(value, 4) for value in language_scores],
        "findings": findings,
        "blocking": any(item["severity"] == "P1" for item in findings),
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

    planned = review.get("planned", {}) if isinstance(review, dict) else {}
    observed = review.get("observed", {}) if isinstance(review, dict) else {}
    evidence = review.get("evidence_spans", []) if isinstance(review, dict) else []
    craft = review.get("craft_observation", {}) if isinstance(review, dict) else {}
    reward = {
        "schema": "reader_reward_entry_v2",
        "chapter_number": chapter_number,
        "chapter_duty": str(
            planned.get("chapter_duty")
            or card.get("chapter_duty")
            or card.get("duty")
            or ""
        ),
        "planned_gain": str(planned.get("reader_gain") or card.get("reader_gain") or card.get("reader_payoff") or ""),
        "observed_gain": str(observed.get("reader_gain") or ""),
        "duty_fulfilled": observed.get("duty_fulfilled") if isinstance(observed.get("duty_fulfilled"), bool) else None,
        "planned_cost": str(planned.get("cost") or card.get("cost") or ""),
        "observed_cost": str(observed.get("cost") or ""),
        "promise_progress": sanitize_promise_progress(observed.get("promise_progress")),
        "evidence_source_hash": sha256_text(final_text),
        "evidence_spans": sanitize_evidence_spans(evidence),
        "topology_id": str(craft.get("topology_id") or card.get("topology_id") or ""),
        "ending_mode": str(craft.get("ending_mode") or infer_ending_mode(final_text)),
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


def trailing_streak(records: list[dict[str, Any]], field: str, value: str) -> int:
    count = 0
    for item in reversed(records):
        if str(item.get(field) or "") != value:
            break
        count += 1
    return count


def language_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_metrics = left.get("language_metrics") if isinstance(left.get("language_metrics"), dict) else {}
    right_metrics = right.get("language_metrics") if isinstance(right.get("language_metrics"), dict) else {}
    left_ngrams = set(clean_strings(left_metrics.get("ngram_signature")))
    right_ngrams = set(clean_strings(right_metrics.get("ngram_signature")))
    if left_ngrams or right_ngrams:
        ngram_score = len(left_ngrams & right_ngrams) / max(1, len(left_ngrams | right_ngrams))
    else:
        ngram_score = 0.0
    left_shape = left_metrics.get("paragraph_shape")
    right_shape = right_metrics.get("paragraph_shape")
    shape_score = 1.0 if isinstance(left_shape, list) and left_shape == right_shape and left_shape else 0.0
    sentence_score = ratio_closeness(
        float(left_metrics.get("average_sentence_chars") or 0),
        float(right_metrics.get("average_sentence_chars") or 0),
    )
    dialogue_score = ratio_closeness(
        float(left_metrics.get("dialogue_density") or 0),
        float(right_metrics.get("dialogue_density") or 0),
        floor=0.05,
    )
    rhythm_score = 0.45 * shape_score + 0.35 * sentence_score + 0.20 * dialogue_score
    return max(ngram_score, rhythm_score)


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


def ratio_closeness(left: float, right: float, *, floor: float = 1.0) -> float:
    scale = max(abs(left), abs(right), floor)
    return max(0.0, 1.0 - abs(left - right) / scale)


def count_patterns(text: str, patterns: tuple[str, ...]) -> int:
    return sum(text.count(pattern) for pattern in patterns)


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


def sanitize_promise_progress(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "promise_ref": str(item.get("promise_ref") or ""),
                "status": str(item.get("status") or ""),
                "evidence_span_indices": [
                    int(index)
                    for index in item.get("evidence_span_indices", [])
                    if isinstance(index, int) and not isinstance(index, bool)
                ],
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
