"""Word-budget-first length contracts and deterministic forecasts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil
from typing import Any

from longform_engine.text_metrics import TEXT_METRIC_ID


LENGTH_CONTRACT_SCHEMA = "length_contract_v2"
FORMAL_MAX_CHARACTERS = 2_000_000


class LengthContractError(ValueError):
    """Raised when the word-budget-first length contract is invalid."""


@dataclass(frozen=True)
class LengthForecast:
    schema: str
    metric: str
    target_total_characters: int
    completion_min_characters: int
    completion_max_characters: int
    estimated_chapters: int
    minimum_reasonable_chapters: int
    maximum_reasonable_chapters: int
    estimated_volumes: int
    formal_support: bool
    support_status: str

    def to_dict(self) -> dict[str, int | str | bool]:
        return asdict(self)


def validate_length_contract(length: Any) -> dict[str, Any]:
    if not isinstance(length, dict):
        raise LengthContractError("length must be a mapping")
    legacy = {"total_chapters", "target_total_words", "volume_count", "chapter_word_count"} & set(length)
    if legacy:
        raise LengthContractError(
            "v0.4.0 does not accept fixed-count length fields: " + ", ".join(sorted(legacy))
        )
    if str(length.get("metric") or "") != TEXT_METRIC_ID:
        raise LengthContractError(f"length.metric must be {TEXT_METRIC_ID}")
    target = positive_int(length, "target_total_characters", "length")
    tolerance = length.get("completion_tolerance")
    if (
        not isinstance(tolerance, list)
        or len(tolerance) != 2
        or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in tolerance)
        or not 0 < float(tolerance[0]) <= 1 <= float(tolerance[1])
    ):
        raise LengthContractError("length.completion_tolerance must be [minimum_ratio, maximum_ratio] around 1.0")
    chapter = mapping(length, "chapter", "length")
    target_chapter = positive_int(chapter, "target_characters", "length.chapter")
    soft_min = positive_int(chapter, "soft_min", "length.chapter")
    soft_max = positive_int(chapter, "soft_max", "length.chapter")
    hard_min = positive_int(chapter, "hard_min", "length.chapter")
    hard_max = positive_int(chapter, "hard_max", "length.chapter")
    if not hard_min <= soft_min <= target_chapter <= soft_max <= hard_max:
        raise LengthContractError(
            "length.chapter must satisfy hard_min <= soft_min <= target_characters <= soft_max <= hard_max"
        )
    volume = mapping(length, "volume", "length")
    positive_int(volume, "target_characters", "length.volume")
    planning = mapping(length, "planning", "length")
    if str(planning.get("mode") or "") != "rolling":
        raise LengthContractError("length.planning.mode must be rolling")
    horizon = positive_int(planning, "detailed_horizon", "length.planning")
    threshold = positive_int(planning, "refill_threshold", "length.planning")
    if threshold >= horizon:
        raise LengthContractError("length.planning.refill_threshold must be lower than detailed_horizon")
    if target < hard_min:
        raise LengthContractError("length.target_total_characters must be at least one hard-minimum chapter")
    return length


def compile_length_forecast(length: dict[str, Any]) -> LengthForecast:
    validate_length_contract(length)
    target = int(length["target_total_characters"])
    low_ratio, high_ratio = (float(item) for item in length["completion_tolerance"])
    chapter = length["chapter"]
    volume = length["volume"]
    estimated_chapters = max(1, round(target / int(chapter["target_characters"])))
    minimum_chapters = max(1, ceil(target / int(chapter["soft_max"])))
    maximum_chapters = max(minimum_chapters, ceil(target / int(chapter["soft_min"])))
    estimated_volumes = max(1, round(target / int(volume["target_characters"])))
    formal = target <= FORMAL_MAX_CHARACTERS
    return LengthForecast(
        schema=LENGTH_CONTRACT_SCHEMA,
        metric=TEXT_METRIC_ID,
        target_total_characters=target,
        completion_min_characters=round(target * low_ratio),
        completion_max_characters=round(target * high_ratio),
        estimated_chapters=estimated_chapters,
        minimum_reasonable_chapters=minimum_chapters,
        maximum_reasonable_chapters=maximum_chapters,
        estimated_volumes=estimated_volumes,
        formal_support=formal,
        support_status="formal" if formal else "experimental",
    )


def positive_int(mapping_value: dict[str, Any], key: str, owner: str) -> int:
    value = mapping_value.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LengthContractError(f"{owner}.{key} must be a positive integer")
    return value


def mapping(value: dict[str, Any], key: str, owner: str) -> dict[str, Any]:
    child = value.get(key)
    if not isinstance(child, dict):
        raise LengthContractError(f"{owner}.{key} must be a mapping")
    return child
