"""Canonical text metrics for Chinese longform manuscript prose."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import unicodedata


TEXT_METRIC_ID = "content_characters_v1"


@dataclass(frozen=True)
class ManuscriptTextMetrics:
    """Transparent counts for manuscript body text."""

    metric: str
    content_characters: int
    display_characters: int

    def to_dict(self) -> dict[str, int | str]:
        return asdict(self)


def manuscript_body(text: str) -> str:
    """Remove document-only Markdown syntax and an optional leading title."""

    normalized = unicodedata.normalize("NFC", str(text or "")).lstrip("\ufeff")
    lines = normalized.splitlines()
    first_content = next((index for index, line in enumerate(lines) if line.strip()), None)
    if first_content is not None and re.match(r"^\s*#{1,6}\s+", lines[first_content]):
        del lines[first_content]
    body = "\n".join(lines)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
    body = re.sub(r"(?m)^\s*(```+|~~~+).*$", "", body)
    body = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*]\s|\d+[.)]\s)", "", body)
    return body


def measure_manuscript_text(text: str) -> ManuscriptTextMetrics:
    body = manuscript_body(text)
    display = sum(1 for char in body if not char.isspace())
    content = sum(1 for char in body if unicodedata.category(char)[:1] in {"L", "N"})
    return ManuscriptTextMetrics(
        metric=TEXT_METRIC_ID,
        content_characters=content,
        display_characters=display,
    )


def content_character_count(text: str) -> int:
    return measure_manuscript_text(text).content_characters


def display_character_count(text: str) -> int:
    return measure_manuscript_text(text).display_characters
