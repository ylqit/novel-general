"""Creative operator protocol, humanizer, and style playbook helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.agent_tasks import build_manifest, mark_tasks_for_output, write_manifest
from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


CREATIVE_BRIEF_FIELDS = (
    "target_audience",
    "writing_style",
    "reader_contract",
    "core_taboo",
    "automation_level",
    "target_scale",
    "genre_style_profile",
)

EXPANSION_TYPES = ("scene", "dialogue", "psychology", "action", "transition")

CHINESE_HUMANIZER_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "code": "humanizer_meta_residue",
        "category": "TODO/占位符",
        "severity": "P0",
        "patterns": ("TODO", "写作说明", "作者按", "角色定位", "占位", "待补", "as an ai", "language model", "prompt:"),
        "threshold": 1,
        "suggestion": "删除所有写作指令、占位符和 AI 自述，只保留世界内正文。",
    },
    {
        "code": "humanizer_inflated_significance",
        "category": "意义膨胀",
        "severity": "P1",
        "patterns": ("意义深远", "深远意义", "不言而喻", "命运的齿轮", "历史性的时刻", "至关重要", "举足轻重"),
        "threshold": 1,
        "suggestion": "把抽象拔高改成角色能看见、付出或误判的具体后果。",
    },
    {
        "code": "humanizer_summary_voice",
        "category": "总结腔",
        "severity": "P1",
        "patterns": ("总之", "由此可见", "可以看出", "这意味着", "接下来", "本章", "这一刻标志着"),
        "threshold": 2,
        "suggestion": "删掉作者总结，把信息压回动作、对白、选择或场景变化里。",
    },
    {
        "code": "humanizer_cliche_action",
        "category": "套话动作",
        "severity": "P2",
        "patterns": ("嘴角微扬", "眼神复杂", "身体一僵", "瞳孔微缩", "倒吸一口凉气", "攥紧拳头", "眼底闪过"),
        "threshold": 1,
        "suggestion": "替换成和人物目标、场景道具、身体代价绑定的独有动作。",
    },
    {
        "code": "humanizer_high_frequency_words",
        "category": "高频词",
        "severity": "P2",
        "patterns": ("仿佛", "不禁", "瞬间", "顿时", "猛地", "显然", "整个人"),
        "threshold": 2,
        "suggestion": "保留少量必要语气词，其余改成可见动作或明确因果。",
    },
    {
        "code": "humanizer_weak_adverbs",
        "category": "弱化副词",
        "severity": "P2",
        "patterns": ("似乎", "好像", "微微", "有些", "莫名", "隐隐", "略微", "下意识"),
        "threshold": 3,
        "suggestion": "减少模糊副词，改写为明确感知、判断证据或动作反应。",
    },
)

CHINESE_TEMPLATE_TRIO_REGEXES = (
    r"不仅[^。！？!?]{0,40}还[^。！？!?]{0,40}更",
    r"不是[^。！？!?]{0,40}而是[^。！？!?]{0,40}更是",
    r"有[^。！？!?]{0,18}有[^。！？!?]{0,18}还有",
)


@dataclass(frozen=True)
class CreativeBriefResult:
    ok: bool
    brief_file: str
    task_file: str
    errors: tuple[str, ...]
    created: bool


@dataclass(frozen=True)
class StyleProfileResult:
    profile_file: str
    current_profile_file: str
    genre: str
    target_audience: str


@dataclass(frozen=True)
class StyleExtractResult:
    profile_file: str
    current_profile_file: str
    library_file: str
    name: str
    sample_files: tuple[str, ...]
    source_project: str
    activated: bool
    fingerprint: dict[str, Any]


@dataclass(frozen=True)
class HumanizeTaskResult:
    chapter_number: int
    source: str
    source_file: str
    task_file: str
    manifest_file: str
    candidate_file: str
    next_command: str


@dataclass(frozen=True)
class HumanizeCheckResult:
    chapter_number: int
    file: str
    report_file: str
    markdown_report: str
    passed: bool
    issue_summary: dict[str, Any]
    issues: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class ExpandTaskResult:
    chapter_number: int
    source: str
    source_file: str
    task_file: str
    manifest_file: str
    candidate_file: str
    expansion_types: tuple[str, ...]
    current_word_count: int
    minimum_word_count: int
    missing_words: int
    next_command: str


@dataclass(frozen=True)
class ExpandCheckResult:
    chapter_number: int
    file: str
    report_file: str
    markdown_report: str
    passed: bool
    word_count: int
    minimum_word_count: int
    expansion_types: tuple[str, ...]
    issues: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    next_command: str


def init_creative_brief(
    config: ConfigDocument,
    *,
    confirmations: dict[str, Any] | None = None,
    overwrite: bool = False,
) -> CreativeBriefResult:
    """Create or refresh the canonical creative brief."""

    root = resolve_project_root(config)
    brief_path = root / "10_bible" / "creative_brief.json"
    if brief_path.exists() and not overwrite:
        return validate_creative_brief(config)
    payload = default_creative_brief(config, confirmations or {})
    write_json(brief_path, payload)
    result = validate_creative_brief(config)
    return CreativeBriefResult(
        ok=result.ok,
        brief_file=str(brief_path),
        task_file=result.task_file,
        errors=result.errors,
        created=True,
    )


def validate_creative_brief(config: ConfigDocument) -> CreativeBriefResult:
    """Validate creative_brief.json and write a confirmation task on failure."""

    root = resolve_project_root(config)
    brief_path = root / "10_bible" / "creative_brief.json"
    errors: list[str] = []
    payload: Any = {}
    if not brief_path.exists():
        errors.append("missing 10_bible/creative_brief.json")
    else:
        payload = load_json(brief_path, default={})
        if not isinstance(payload, dict):
            errors.append("creative_brief.json must be an object")
        else:
            for field in CREATIVE_BRIEF_FIELDS:
                if not payload.get(field):
                    errors.append(f"missing creative brief field: {field}")
            if str(payload.get("status") or "").lower() not in {"confirmed", "draft", "pending_confirmation"}:
                errors.append("creative brief status must be confirmed, draft, or pending_confirmation")

    task_file = ""
    if errors:
        task_file = str(write_creative_brief_task(root, errors, payload if isinstance(payload, dict) else {}))
    return CreativeBriefResult(
        ok=not errors,
        brief_file=str(brief_path),
        task_file=task_file,
        errors=tuple(errors),
        created=False,
    )


def default_creative_brief(config: ConfigDocument, confirmations: dict[str, Any]) -> dict[str, Any]:
    novel = config.data.get("novel", {}) if isinstance(config.data.get("novel"), dict) else {}
    length = config.data.get("length", {}) if isinstance(config.data.get("length"), dict) else {}
    target_words = length.get("target_total_words", "")
    chapters = length.get("total_chapters", "")
    target_scale = confirmations.get("target_scale") or f"{chapters} chapters / {target_words} words"
    target_audience = confirmations.get("target_audience") or novel.get("target_audience") or "longform novel readers"
    writing_style = confirmations.get("writing_style") or novel.get("style") or novel.get("writing_style") or "immersive serialized prose"
    core_taboo = confirmations.get("core_taboo") or confirmations.get("core_forbidden_zone") or novel.get("forbidden_experience") or [
        "do not prematurely resolve the core conflict",
        "do not leave meta/prompt/AI residue in manuscript prose",
    ]
    return {
        "schema_version": 1,
        "target_audience": target_audience,
        "writing_style": writing_style,
        "reader_contract": {
            "platform": novel.get("target_platform", "unknown"),
            "genre": novel.get("genre", "unknown"),
            "core_promise": novel.get("core_promise", ""),
            "main_question": novel.get("main_question", ""),
            "ending_direction": novel.get("ending_direction", ""),
        },
        "core_taboo": as_list(core_taboo),
        "automation_level": confirmations.get("automation_level") or "agent_skill with human approval for finalization",
        "target_scale": target_scale,
        "genre_style_profile": {
            "genre": novel.get("genre", "unknown"),
            "tone": writing_style,
            "payoff_density": "one local payoff per chapter; preserve longform core mystery",
            "dialogue_bias": "use dialogue for pressure, status, and subtext rather than exposition",
        },
        "status": "confirmed",
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }


def write_creative_brief_task(root: Path, errors: list[str], payload: dict[str, Any]) -> Path:
    path = root / "50_workbench" / "creative_brief_task.md"
    atomic_write_text(
        path,
        "\n".join(
            [
                "# Creative Brief Confirmation Task",
                "",
                "The writing pipeline needs a confirmed creative brief before Codex writes a chapter.",
                "",
                "## Missing Or Invalid Fields",
                "",
                *[f"- {error}" for error in errors],
                "",
                "## Required Fields",
                "",
                *[f"- `{field}`" for field in CREATIVE_BRIEF_FIELDS],
                "",
                "## Current Payload",
                "",
                "```json",
                json.dumps(payload, ensure_ascii=False, indent=2),
                "```",
                "",
                "Run `longform-engine creative brief project.yaml --init` after confirming the opening contract.",
                "",
            ]
        ),
    )
    return path


def load_creative_brief(root: Path) -> dict[str, Any]:
    payload = load_json(root / "10_bible" / "creative_brief.json", default={})
    return payload if isinstance(payload, dict) else {}


def style_profile(config: ConfigDocument, *, genre: str, target_audience: str) -> StyleProfileResult:
    """Write a deterministic genre-style matrix and selected style profile."""

    root = resolve_project_root(config)
    style_dir = root / "10_bible" / "style_profiles"
    style_dir.mkdir(parents=True, exist_ok=True)
    normalized_genre = normalize_key(genre or "general")
    normalized_audience = normalize_key(target_audience or "general")
    selected = profile_for_genre(genre, target_audience)
    matrix = {
        "schema_version": 1,
        "profiles": {
            "xuanhuan": profile_for_genre("xuanhuan", target_audience),
            "urban": profile_for_genre("urban", target_audience),
            "romance": profile_for_genre("romance", target_audience),
            "suspense": profile_for_genre("suspense", target_audience),
            normalized_genre: selected,
        },
        "selected": normalized_genre,
        "target_audience": target_audience,
        "updated_at": utc_now(),
    }
    matrix_path = style_dir / "genre_style_matrix.json"
    current_path = style_dir / "current_style_profile.json"
    write_json(matrix_path, matrix)
    write_json(
        current_path,
        {
            "schema_version": 1,
            "genre": genre,
            "target_audience": target_audience,
            "profile_key": normalized_genre,
            "profile": selected,
            "updated_at": utc_now(),
        },
    )
    return StyleProfileResult(
        profile_file=str(matrix_path),
        current_profile_file=str(current_path),
        genre=genre,
        target_audience=target_audience,
    )


def style_extract(
    config: ConfigDocument,
    *,
    sample_files: list[str | Path] | tuple[str | Path, ...] | None = None,
    name: str = "sample",
    source_project: str = "",
    library_profile: str | Path | None = None,
    activate: bool = True,
) -> StyleExtractResult:
    """Extract a reusable style profile from one or more sample chapters."""

    root = resolve_project_root(config)
    style_dir = root / "10_bible" / "style_profiles"
    style_dir.mkdir(parents=True, exist_ok=True)
    resolved_samples: list[Path] = []
    sample_records: list[dict[str, Any]] = []
    sample_texts: list[str] = []
    for raw_file in sample_files or []:
        path = resolve_input_file(root, raw_file)
        if not path.exists():
            raise ValueError(f"Style sample not found: {path}")
        text = safe_read_text(path)
        resolved_samples.append(path)
        sample_texts.append(text)
        sample_records.append(
            {
                "path": relative_path(root, path),
                "source_project": source_project,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "chars": len(re.sub(r"\s+", "", text)),
            }
        )

    library_payload: dict[str, Any] = {}
    library_source = ""
    if library_profile:
        library_path = resolve_input_file(root, library_profile)
        if not library_path.exists():
            raise ValueError(f"Style library profile not found: {library_path}")
        loaded = load_json(library_path, default={})
        if not isinstance(loaded, dict):
            raise ValueError(f"Style library profile must be a JSON object: {library_path}")
        library_payload = loaded
        library_source = relative_path(root, library_path)

    if not sample_texts and not library_payload:
        raise ValueError("style_extract requires at least one sample file or a library profile.")

    profile_name = normalize_key(name or (resolved_samples[0].stem if resolved_samples else "library_profile"))
    if sample_texts:
        combined = "\n\n".join(sample_texts)
        profile = build_sample_style_profile(combined)
        profile_type = "sample_extract"
    else:
        profile = normalize_imported_style_profile(library_payload)
        profile_type = "library_import"

    payload = {
        "schema_version": 2,
        "profile_type": profile_type,
        "name": profile_name,
        "display_name": name or profile_name,
        "source_project": source_project,
        "sample_sources": sample_records,
        "library_source": library_source,
        "profile": profile,
        "updated_at": utc_now(),
    }
    profile_file = style_dir / f"{profile_name}.sample_profile.json"
    current_file = style_dir / "current_style_profile.json"
    library_file = style_dir / "style_library.json"
    write_json(profile_file, payload)
    update_style_library(library_file, profile_name, profile_file, payload)
    if activate:
        current_payload = {
            **payload,
            "active_profile_file": relative_path(root, profile_file),
            "activated_at": utc_now(),
        }
        write_json(current_file, current_payload)

    return StyleExtractResult(
        profile_file=str(profile_file),
        current_profile_file=str(current_file) if activate else "",
        library_file=str(library_file),
        name=profile_name,
        sample_files=tuple(relative_path(root, path) for path in resolved_samples),
        source_project=source_project,
        activated=activate,
        fingerprint=profile.get("fingerprint") if isinstance(profile.get("fingerprint"), dict) else {},
    )


def profile_for_genre(genre: str, target_audience: str) -> dict[str, Any]:
    key = normalize_key(genre)
    defaults = {
        "sentence_length": "medium with short impact sentences at turns",
        "dialogue_ratio": "25-35%",
        "narrative_focus": ["scene action", "choice pressure", "emotional consequence"],
        "payoff_density": "one local payoff plus one new hook",
        "ai_voice_avoidance": ["summary lecture", "generic significance", "template trios"],
    }
    variants = {
        "xuanhuan": {
            "sentence_length": "medium-long for atmosphere, short at combat turns",
            "dialogue_ratio": "18-28%",
            "narrative_focus": ["power cost", "hierarchy pressure", "mystery escalation"],
            "payoff_density": "cultivation gain or clue every chapter; major realm payoff after evidence",
        },
        "urban": {
            "sentence_length": "short-medium with fast sensory anchoring",
            "dialogue_ratio": "30-45%",
            "narrative_focus": ["status reversal", "social pressure", "practical stakes"],
            "payoff_density": "visible reversal or advantage every chapter",
        },
        "romance": {
            "sentence_length": "medium with more interior cadence",
            "dialogue_ratio": "35-50%",
            "narrative_focus": ["subtext", "misread motive", "touch/absence detail"],
            "payoff_density": "relationship micro-shift every chapter",
        },
        "suspense": {
            "sentence_length": "variable; clipped at clues and threats",
            "dialogue_ratio": "20-35%",
            "narrative_focus": ["clue chain", "false certainty", "threat proximity"],
            "payoff_density": "one clue clarified, one question sharpened",
        },
    }
    profile = {**defaults, **variants.get(key, {})}
    profile["target_audience"] = target_audience
    profile["dialogue_strategy"] = "each speaker should reveal goal, concealment, or status; avoid interchangeable explanation"
    profile["sensory_strategy"] = "give each major scene one concrete anchor: sound, texture, smell, light, or body cost"
    profile["ending_hook_strategy"] = "end on an image, decision, discovery, or threat that changes the next chapter's problem"
    return profile


def build_sample_style_profile(text: str) -> dict[str, Any]:
    fingerprint = extracted_style_fingerprint(text)
    return {
        "summary": style_profile_summary(fingerprint),
        "fingerprint": fingerprint,
        "sentence_length": fingerprint["sentence_length"],
        "paragraph_length": fingerprint["paragraph_length"],
        "dialogue_ratio": fingerprint["dialogue_ratio"],
        "punctuation_density": fingerprint["punctuation_density"],
        "pov": fingerprint["pov"],
        "common_phrases": fingerprint["common_phrases"],
        "action_preference": fingerprint["action_preference"],
        "pacing_tags": fingerprint["pacing_tags"],
        "narrative_density": fingerprint["narrative_density"],
        "usage_guidance": [
            "Use this profile as a style boundary, not as plot canon.",
            "Preserve sentence/paragraph rhythm before copying surface phrases.",
            "Gate may warn or block if draft metrics drift sharply from this sample profile.",
        ],
    }


def normalize_imported_style_profile(payload: dict[str, Any]) -> dict[str, Any]:
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else payload
    fingerprint = profile.get("fingerprint") if isinstance(profile.get("fingerprint"), dict) else {}
    if not fingerprint:
        fingerprint = {
            key: profile.get(key)
            for key in (
                "avg_sentence_chars",
                "avg_paragraph_chars",
                "dialogue_ratio",
                "punctuation_density",
                "pov",
                "common_phrases",
                "pacing_tags",
                "narrative_density",
            )
            if key in profile
        }
    normalized = dict(profile)
    normalized["fingerprint"] = fingerprint
    normalized.setdefault("summary", style_profile_summary(fingerprint))
    normalized.setdefault("usage_guidance", ["Imported style profile; verify source authorization before reuse."])
    return normalized


def update_style_library(library_file: Path, name: str, profile_file: Path, payload: dict[str, Any]) -> None:
    library = load_json(library_file, default={})
    if not isinstance(library, dict):
        library = {}
    profiles = library.get("profiles") if isinstance(library.get("profiles"), list) else []
    profiles = [item for item in profiles if not (isinstance(item, dict) and item.get("name") == name)]
    profiles.append(
        {
            "name": name,
            "profile_type": payload.get("profile_type"),
            "profile_file": profile_file.name,
            "source_project": payload.get("source_project", ""),
            "sample_sources": payload.get("sample_sources", []),
            "library_source": payload.get("library_source", ""),
            "updated_at": payload.get("updated_at"),
        }
    )
    write_json(
        library_file,
        {
            "schema_version": 1,
            "profiles": profiles,
            "updated_at": utc_now(),
        },
    )


def extracted_style_fingerprint(text: str) -> dict[str, Any]:
    compact = re.sub(r"\s+", "", text)
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    sentences = [part.strip() for part in re.split(r"[.!?\u3002\uff01\uff1f]+", text) if part.strip()]
    sentence_lengths = [estimate_words(sentence) for sentence in sentences]
    paragraph_lengths = [estimate_words(paragraph) for paragraph in paragraphs]
    total_chars = max(1, len(compact))
    dialogue_marks = sum(text.count(mark) for mark in ('"', "'", "\u201c", "\u201d", "\u300c", "\u300d"))
    dialogue_paragraphs = [paragraph for paragraph in paragraphs if has_dialogue_marker(paragraph)]
    punctuation = sum(text.count(mark) for mark in ",.!?;:\u3002\uff0c\uff01\uff1f\uff1b\uff1a\u3001")
    pov = detect_pov(text)
    action = action_preference(text, total_chars)
    fingerprint = {
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentences),
        "avg_sentence_chars": round(mean(sentence_lengths), 2),
        "avg_paragraph_chars": round(mean(paragraph_lengths), 2),
        "paragraph_variance": round(mean_absolute_deviation(paragraph_lengths), 2),
        "dialogue_ratio": round(dialogue_marks / total_chars, 4),
        "dialogue_paragraph_ratio": round(len(dialogue_paragraphs) / max(1, len(paragraphs)), 4),
        "punctuation_density": round(punctuation / total_chars, 4),
        "sentence_length": length_stats(sentence_lengths),
        "paragraph_length": length_stats(paragraph_lengths),
        "pov": pov,
        "common_phrases": common_phrases(text),
        "action_preference": action,
        "pacing_tags": pacing_tags(sentence_lengths, paragraph_lengths, action, len(dialogue_paragraphs), len(paragraphs)),
        "narrative_density": {
            "sentences_per_paragraph": round(len(sentences) / max(1, len(paragraphs)), 2),
            "chars_per_paragraph": round(mean(paragraph_lengths), 2),
            "action_markers_per_1000_chars": action["markers_per_1000_chars"],
            "punctuation_density": round(punctuation / total_chars, 4),
        },
    }
    return fingerprint


def style_profile_summary(fingerprint: dict[str, Any]) -> str:
    tags = fingerprint.get("pacing_tags") if isinstance(fingerprint.get("pacing_tags"), list) else []
    pov = fingerprint.get("pov") if isinstance(fingerprint.get("pov"), dict) else {}
    return (
        f"avg_sentence={fingerprint.get('avg_sentence_chars', 0)}, "
        f"avg_paragraph={fingerprint.get('avg_paragraph_chars', 0)}, "
        f"dialogue_ratio={fingerprint.get('dialogue_ratio', 0)}, "
        f"pov={pov.get('dominant', 'unknown')}, "
        f"tags={', '.join(str(tag) for tag in tags) or 'none'}"
    )


def has_dialogue_marker(text: str) -> bool:
    return any(mark in text for mark in ('"', "'", "\u201c", "\u201d", "\u300c", "\u300d")) or bool(re.search(r"^\s*[-A-Za-z\u4e00-\u9fff]{1,18}:", text))


def detect_pov(text: str) -> dict[str, Any]:
    lower = text.lower()
    counts = {
        "first_person": len(re.findall(r"\b(i|me|my|mine|we|our|us)\b|\u6211|\u6211\u4eec", lower)),
        "second_person": len(re.findall(r"\b(you|your|yours)\b|\u4f60|\u4f60\u4eec", lower)),
        "third_person": len(re.findall(r"\b(he|she|they|him|her|them|his|their)\b|\u4ed6|\u5979|\u4ed6\u4eec|\u5979\u4eec", lower)),
    }
    dominant = max(counts, key=lambda key: counts[key]) if any(counts.values()) else "unknown"
    return {"dominant": dominant, "counts": counts}


def common_phrases(text: str, *, limit: int = 8) -> list[dict[str, Any]]:
    lower = text.lower()
    words = re.findall(r"[a-z][a-z']+", lower)
    phrases: dict[str, int] = {}
    for size in (2, 3):
        for index in range(0, max(0, len(words) - size + 1)):
            phrase = " ".join(words[index : index + size])
            if not any(word in {"the", "and", "but", "then", "with"} for word in phrase.split()):
                phrases[phrase] = phrases.get(phrase, 0) + 1
    compact = re.sub(r"[A-Za-z0-9\s,.!?;:'\"-]+", "", text)
    for index in range(0, max(0, len(compact) - 3)):
        phrase = compact[index : index + 4]
        if len(phrase) == 4:
            phrases[phrase] = phrases.get(phrase, 0) + 1
    ranked = sorted(((phrase, count) for phrase, count in phrases.items() if count >= 2), key=lambda item: (-item[1], item[0]))
    return [{"phrase": phrase, "count": count} for phrase, count in ranked[:limit]]


def action_preference(text: str, total_chars: int) -> dict[str, Any]:
    lower = text.lower()
    markers = (
        "stepped",
        "grabbed",
        "turned",
        "pushed",
        "opened",
        "raised",
        "ran",
        "moved",
        "struck",
        "looked",
        "\u63a8",
        "\u8d70",
        "\u6293",
        "\u62ac",
        "\u8f6c",
        "\u770b",
    )
    hits = {marker: len(re.findall(re.escape(marker), lower)) for marker in markers}
    hits = {marker: count for marker, count in hits.items() if count}
    total_hits = sum(hits.values())
    return {
        "top_markers": [{"marker": marker, "count": count} for marker, count in sorted(hits.items(), key=lambda item: (-item[1], item[0]))[:8]],
        "markers_per_1000_chars": round(total_hits / max(1, total_chars) * 1000, 2),
    }


def pacing_tags(
    sentence_lengths: list[int],
    paragraph_lengths: list[int],
    action: dict[str, Any],
    dialogue_paragraph_count: int,
    paragraph_count: int,
) -> list[str]:
    tags: list[str] = []
    avg_sentence = mean(sentence_lengths)
    avg_paragraph = mean(paragraph_lengths)
    dialogue_paragraph_ratio = dialogue_paragraph_count / max(1, paragraph_count)
    if avg_sentence <= 28:
        tags.append("short-sentence")
    elif avg_sentence >= 70:
        tags.append("long-breath")
    if avg_paragraph <= 120:
        tags.append("quick-paragraph")
    elif avg_paragraph >= 420:
        tags.append("dense-paragraph")
    if dialogue_paragraph_ratio >= 0.35:
        tags.append("dialogue-led")
    if float(action.get("markers_per_1000_chars") or 0) >= 8:
        tags.append("action-forward")
    if not tags:
        tags.append("balanced")
    return tags


def length_stats(values: list[int]) -> dict[str, Any]:
    sorted_values = sorted(values)
    return {
        "min": min(sorted_values) if sorted_values else 0,
        "max": max(sorted_values) if sorted_values else 0,
        "avg": round(mean(sorted_values), 2),
        "median": median(sorted_values),
        "buckets": {
            "short": sum(1 for value in sorted_values if value <= 30),
            "medium": sum(1 for value in sorted_values if 30 < value <= 80),
            "long": sum(1 for value in sorted_values if value > 80),
        },
    }


def mean(values: list[int]) -> float:
    return sum(values) / max(1, len(values))


def median(values: list[int]) -> float:
    if not values:
        return 0.0
    index = len(values) // 2
    if len(values) % 2:
        return float(values[index])
    return round((values[index - 1] + values[index]) / 2, 2)


def mean_absolute_deviation(values: list[int]) -> float:
    if not values:
        return 0.0
    avg = mean(values)
    return sum(abs(value - avg) for value in values) / len(values)


def writer_craft_brief(
    config: ConfigDocument,
    *,
    chapter_number: int,
    card: dict[str, Any],
    beat: dict[str, Any],
    tcs: dict[str, Any],
    style_context: dict[str, Any],
) -> dict[str, Any]:
    creative = load_creative_brief(resolve_project_root(config))
    beats = beat.get("beats") if isinstance(beat.get("beats"), list) else []
    beat_hooks = [str(item.get("hook") or "") for item in beats if isinstance(item, dict) and item.get("hook")]
    return {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "reader_payoff": card.get("reader_payoff") or "deliver one local payoff without resolving the core longform promise",
        "emotion_progression": {
            "start": tcs.get("emotion_state", "current pressure"),
            "turn": "pressure becomes a visible choice or cost",
            "end": "reader should feel the situation has advanced, not merely been explained",
        },
        "dialogue_strategy": [
            "make dialogue carry pressure, status, concealment, or relationship change",
            "avoid characters explaining the chapter card to each other",
            "give important speakers distinct rhythm and intent",
        ],
        "scene_texture": [
            "open in a concrete place, body state, or action",
            "anchor each major scene with one sensory detail",
            "use action to carry psychology before direct explanation",
        ],
        "ending_hook": card.get("hook") or (beat_hooks[-1] if beat_hooks else "leave a concrete unanswered pressure"),
        "forbidden_reveals": as_list(card.get("forbidden_reveals")),
        "ai_voice_forbidden_zone": [
            "generic importance language",
            "summary-heavy plot recap",
            "visible prompt labels",
            "rule-of-three filler",
            "same-length paragraphs and same speaker rhythm",
        ],
        "style_memory": style_context,
        "creative_brief_status": creative.get("status", "missing"),
    }


def humanizer_rules() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "two_pass_workflow": {
            "pass_1_remove_ai_templates": [
                "删除 TODO、写作说明、作者按、角色定位、prompt 残留和 AI 自述。",
                "压缩“总之/由此可见/可以看出/这意味着/本章”等总结腔。",
                "删改“意义深远/至关重要/命运的齿轮”等意义膨胀句。",
                "替换“嘴角微扬/眼神复杂/身体一僵/倒吸一口凉气”等模板动作。",
                "降低“仿佛/不禁/似乎/微微/有些/莫名/下意识”等高频词和弱化副词密度。",
                "拆掉“不仅...还...更...”和“不是...而是...更是...”等模板三连。",
            ],
            "pass_2_strengthen_voice": [
                "补具体动作：让角色用选择、手上动作、移动路线承担心理变化。",
                "补感官细节：每个主要场景至少有声音、触感、气味、光线或身体代价之一。",
                "调整句长节奏：紧张处短句切开，解释处合并，避免等长句排队。",
                "增强对白差异：每个说话人带不同目的、遮掩、身份压力或关系变化。",
                "把章末落点改成具体发现、决定、威胁或误解，而不是抽象展望。",
            ],
        },
        "chinese_issue_catalog": [
            {
                "code": rule["code"],
                "category": rule["category"],
                "severity": rule["severity"],
                "patterns": list(rule["patterns"]),
                "suggestion": rule["suggestion"],
            }
            for rule in CHINESE_HUMANIZER_CATALOG
        ],
        "hard_boundaries": [
            "humanizer output is a candidate only",
            "candidate must be submitted with draft submit",
            "candidate cannot write final/RAG/graph/memory/db directly",
        ],
    }


def expand_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    source: str = "draft",
    expansion_types: list[str] | tuple[str, ...] | None = None,
) -> ExpandTaskResult:
    """Write an expansion task without touching canonical manuscript lanes."""

    root = resolve_project_root(config)
    source_path = resolve_expansion_source(root, chapter_number, source)
    if not source_path.exists():
        raise ValueError(f"Expansion source not found: {source_path}")

    selected_types = normalize_expansion_types(expansion_types)
    task_dir = root / "50_workbench" / "repair_candidates"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / f"ch{chapter_number:03d}.expand_task.md"
    manifest_file = task_dir / f"ch{chapter_number:03d}.expand_task.agent_task.json"
    candidate_file = task_dir / f"ch{chapter_number:03d}.expanded_candidate.md"
    source_text = safe_read_text(source_path)
    current_word_count = estimate_words(source_text)
    minimum_word_count = expansion_minimum_words(config)
    missing_words = max(0, minimum_word_count - current_word_count)
    gate_artifact_dir = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}"
    gate_payload = load_json(gate_artifact_dir / "gate_result.json", default={})
    gate_failures = gate_payload.get("failures", []) if isinstance(gate_payload, dict) else []
    repair_path = gate_artifact_dir / "repair_plan.md"
    repair_text = safe_read_text(repair_path) if repair_path.exists() else "No repair_plan.md found."
    writing_task = load_json(root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json", default={})
    next_command = (
        f"longform-engine creative expand-check project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, candidate_file)}"
    )
    atomic_write_text(
        task_file,
        "\n".join(
            [
                f"# Content Expansion Task ch{chapter_number:03d}",
                "",
                f"- Source: `{relative_path(root, source_path)}`",
                f"- Candidate output: `{relative_path(root, candidate_file)}`",
                f"- Current word count: {current_word_count}",
                f"- Minimum word count: {minimum_word_count}",
                f"- Missing words: {missing_words}",
                f"- Expansion types: {', '.join(selected_types)}",
                f"- Next command: `{next_command}`",
                "",
                "Write an expansion candidate only. Do not edit final manuscripts, RAG, graph, memory, or SQLite.",
                "",
                "## Expansion Instructions",
                "",
                *[f"- {item}" for item in expansion_instructions(selected_types)],
                "",
                "## Gate Failures",
                "",
                "```json",
                json.dumps(gate_failures, ensure_ascii=False, indent=2),
                "```",
                "",
                "## Repair Plan",
                "",
                repair_text.strip() or "No repair plan content.",
                "",
                "## Writing Brief Snapshot",
                "",
                writing_brief_snapshot(writing_task),
                "",
                "## Candidate Contract",
                "",
                "- Save the rewritten candidate at the candidate output path.",
                "- Preserve canonical facts and forbidden-reveal boundaries from the writing task.",
                "- Add scene material instead of filler or summary padding.",
                "- Then run expand-check.",
                "- If accepted, submit it with `draft submit --overwrite`; only `chapter finalize` can update final/RAG/graph/memory.",
                "",
            ]
        ),
    )
    manifest = build_manifest(
        root,
        task_type="content_expand",
        chapter_number=chapter_number,
        input_files=[
            task_file,
            source_path,
            gate_artifact_dir / "gate_result.json",
            repair_path,
            root / "50_workbench" / "writing_tasks" / f"ch{chapter_number:03d}.json",
            root / "10_bible" / "style_bible.md",
            root / "10_bible" / "creative_brief.json",
        ],
        allowed_output_paths=[candidate_file],
        output_schema="markdown_expanded_candidate",
        validate_command=next_command,
        apply_command=(
            f"longform-engine draft submit project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, candidate_file)} --agent codex --overwrite"
        ),
        failure_next_command=f"longform-engine creative expand-task project.yaml --chapter {chapter_number} --source {source}",
    )
    write_manifest(root, manifest, manifest_file)
    return ExpandTaskResult(
        chapter_number=chapter_number,
        source=source,
        source_file=str(source_path),
        task_file=str(task_file),
        manifest_file=str(manifest_file),
        candidate_file=str(candidate_file),
        expansion_types=selected_types,
        current_word_count=current_word_count,
        minimum_word_count=minimum_word_count,
        missing_words=missing_words,
        next_command=next_command,
    )


def expand_check(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
    expansion_types: list[str] | tuple[str, ...] | None = None,
) -> ExpandCheckResult:
    """Check an expansion candidate and report the next safe workflow command."""

    root = resolve_project_root(config)
    target = resolve_input_file(root, file_path)
    if not target.exists():
        raise ValueError(f"Expansion candidate not found: {target}")

    selected_types = normalize_expansion_types(expansion_types)
    text = safe_read_text(target)
    word_count = estimate_words(text)
    minimum_word_count = expansion_minimum_words(config)
    issues, warnings = detect_expansion_issues(root, chapter_number, target, text, selected_types, minimum_word_count)
    humanizer_issues, humanizer_warnings = detect_humanizer_v2_issues(text)
    for item in humanizer_issues:
        if item.get("severity") in {"P0", "P1"}:
            issues.append(
                {
                    "code": f"expansion_{item.get('code')}",
                    "severity": item.get("severity"),
                    "message": item.get("message"),
                }
            )
    warnings.extend(humanizer_warnings)

    report_dir = root / "50_workbench" / "repair_candidates"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"ch{chapter_number:03d}.expand_check.json"
    md_file = report_dir / f"ch{chapter_number:03d}.expand_check.md"
    passed = not any(item.get("severity") in {"P0", "P1"} for item in issues)
    next_command = (
        f"longform-engine draft submit project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, target)} --agent codex --overwrite"
        if passed
        else f"longform-engine creative expand-task project.yaml --chapter {chapter_number} --source draft"
    )
    payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "file": relative_path(root, target),
        "passed": passed,
        "word_count": word_count,
        "minimum_word_count": minimum_word_count,
        "expansion_types": list(selected_types),
        "issues": issues,
        "warnings": warnings,
        "next_command": next_command,
        "checked_at": utc_now(),
    }
    write_json(report_file, payload)
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=target,
        to_status="validated" if passed else "invalid",
        command="creative expand-check",
        result=report_file,
        from_statuses=("awaiting_agent", "submitted"),
    )
    atomic_write_text(
        md_file,
        "\n".join(
            [
                f"# Content Expansion Check ch{chapter_number:03d}",
                "",
                f"- File: `{relative_path(root, target)}`",
                f"- Passed: {passed}",
                f"- Word count: {word_count}",
                f"- Minimum word count: {minimum_word_count}",
                f"- Expansion types: {', '.join(selected_types)}",
                f"- Next command: `{next_command}`",
                "",
                "## Issues",
                "",
                *([f"- [{item.get('severity')}] {item.get('code')}: {item.get('message')}" for item in issues] or ["- None"]),
                "",
                "## Warnings",
                "",
                *([f"- {warning}" for warning in warnings] or ["- None"]),
                "",
            ]
        ),
    )
    return ExpandCheckResult(
        chapter_number=chapter_number,
        file=str(target),
        report_file=str(report_file),
        markdown_report=str(md_file),
        passed=passed,
        word_count=word_count,
        minimum_word_count=minimum_word_count,
        expansion_types=selected_types,
        issues=tuple(issues),
        warnings=tuple(warnings),
        next_command=next_command,
    )


def humanize_task(config: ConfigDocument, *, chapter_number: int, source: str = "draft") -> HumanizeTaskResult:
    root = resolve_project_root(config)
    source_path = resolve_humanizer_source(root, chapter_number, source)
    if not source_path.exists():
        raise ValueError(f"Humanizer source not found: {source_path}")
    task_dir = root / "50_workbench" / "humanizer_tasks"
    candidate_dir = root / "50_workbench" / "repair_candidates"
    task_dir.mkdir(parents=True, exist_ok=True)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / f"ch{chapter_number:03d}.{source.replace('-', '_')}.humanize_task.md"
    manifest_file = task_dir / f"ch{chapter_number:03d}.{source.replace('-', '_')}.humanize_task.agent_task.json"
    candidate_file = candidate_dir / f"ch{chapter_number:03d}.humanized_candidate.md"
    next_command = (
        f"longform-engine creative humanize-check project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, candidate_file)}"
    )
    atomic_write_text(
        task_file,
        "\n".join(
            [
                f"# Humanizer v2 Task ch{chapter_number:03d}",
                "",
                f"- Source: `{relative_path(root, source_path)}`",
                f"- Candidate output: `{relative_path(root, candidate_file)}`",
                f"- Next command: `{next_command}`",
                "",
                "Write a repair/humanized candidate only. Do not edit final manuscripts, RAG, graph, memory, or SQLite.",
                "",
                "## Pass 1: 中文 AI 痕迹清理",
                "",
                *[f"- {item}" for item in humanizer_rules()["two_pass_workflow"]["pass_1_remove_ai_templates"]],
                "",
                "## Pass 2: 中文网文质感增强",
                "",
                *[f"- {item}" for item in humanizer_rules()["two_pass_workflow"]["pass_2_strengthen_voice"]],
                "",
                "## Chinese Issue Catalog",
                "",
                *[
                    f"- [{item['severity']}] {item['category']} / {item['code']}: {', '.join(item['patterns'][:6])}"
                    for item in humanizer_rules()["chinese_issue_catalog"]
                ],
                "",
                "## Submission Contract",
                "",
                "- Save the candidate at the candidate output path.",
                "- Then run humanize-check.",
                "- If accepted, submit it with `draft submit`; only `chapter finalize` can enter canonical final/RAG/graph/memory.",
                "",
            ]
        ),
    )
    manifest = build_manifest(
        root,
        task_type="humanize",
        chapter_number=chapter_number,
        input_files=[
            task_file,
            source_path,
            root / "10_bible" / "style_bible.md",
            root / "10_bible" / "creative_brief.json",
            root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "humanize_report.md",
        ],
        allowed_output_paths=[candidate_file],
        output_schema="markdown_humanized_candidate",
        validate_command=next_command,
        apply_command=(
            f"longform-engine draft submit project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, candidate_file)} --agent codex --overwrite"
        ),
        failure_next_command=f"longform-engine creative humanize-task project.yaml --chapter {chapter_number} --source {source}",
    )
    write_manifest(root, manifest, manifest_file)
    return HumanizeTaskResult(
        chapter_number=chapter_number,
        source=source,
        source_file=str(source_path),
        task_file=str(task_file),
        manifest_file=str(manifest_file),
        candidate_file=str(candidate_file),
        next_command=next_command,
    )


def humanize_check(config: ConfigDocument, *, chapter_number: int, file_path: str | Path) -> HumanizeCheckResult:
    root = resolve_project_root(config)
    target = resolve_input_file(root, file_path)
    if not target.exists():
        raise ValueError(f"Humanizer candidate not found: {target}")
    text = safe_read_text(target)
    issues, warnings = detect_humanizer_v2_issues(text)
    report_dir = root / "50_workbench" / "humanizer_tasks"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"ch{chapter_number:03d}.humanize_check.json"
    md_file = report_dir / f"ch{chapter_number:03d}.humanize_check.md"
    passed = not any(item.get("severity") in {"P0", "P1"} for item in issues)
    next_command = (
        f"longform-engine draft submit project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, target)} --agent codex --overwrite"
        if passed
        else f"longform-engine creative humanize-task project.yaml --chapter {chapter_number} --source draft"
    )
    payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "file": relative_path(root, target),
        "passed": passed,
        "issue_summary": humanizer_issue_summary(issues),
        "issues": issues,
        "warnings": warnings,
        "next_command": next_command,
        "checked_at": utc_now(),
    }
    write_json(report_file, payload)
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=target,
        to_status="validated" if passed else "invalid",
        command="creative humanize-check",
        result=report_file,
        from_statuses=("awaiting_agent", "submitted"),
    )
    atomic_write_text(
        md_file,
        "\n".join(
            [
                f"# Humanizer v2 Check ch{chapter_number:03d}",
                "",
                f"- File: `{relative_path(root, target)}`",
                f"- Passed: {passed}",
                f"- Next command: `{next_command}`",
                "",
                "## Issues",
                "",
                *humanizer_issue_lines(issues),
                "",
                "## Warnings",
                "",
                *([f"- {warning}" for warning in warnings] or ["- None"]),
                "",
            ]
        ),
    )
    return HumanizeCheckResult(
        chapter_number=chapter_number,
        file=str(target),
        report_file=str(report_file),
        markdown_report=str(md_file),
        passed=passed,
        issue_summary=humanizer_issue_summary(issues),
        issues=tuple(issues),
        warnings=tuple(warnings),
        next_command=next_command,
    )


def humanizer_issue_summary(issues: list[dict[str, Any]]) -> dict[str, Any]:
    by_severity: dict[str, int] = {}
    by_category: dict[str, int] = {}
    for issue in issues:
        severity = str(issue.get("severity") or "warning")
        category = str(issue.get("category") or "uncategorized")
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_category[category] = by_category.get(category, 0) + 1
    return {"by_severity": by_severity, "by_category": by_category, "total": len(issues)}


def humanizer_issue_lines(issues: list[dict[str, Any]]) -> list[str]:
    if not issues:
        return ["- None"]
    lines: list[str] = []
    for issue in issues:
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), list) else []
        evidence_text = "; ".join(
            f"{item.get('pattern')} x{item.get('count')}: {item.get('snippet')}"
            for item in evidence[:3]
            if isinstance(item, dict)
        )
        lines.append(
            f"- [{issue.get('severity')}] {issue.get('category', 'uncategorized')} / {issue.get('code')}: {issue.get('message')}"
        )
        if evidence_text:
            lines.append(f"  Evidence: {evidence_text}")
        if issue.get("suggestion"):
            lines.append(f"  Fix: {issue.get('suggestion')}")
    return lines


def legacy_detect_humanizer_v2_issues(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    lower = text.lower()
    issues: list[dict[str, Any]] = []
    warnings: list[str] = []
    meta_patterns = ("todo", "as an ai", "language model", "writing instruction", "outline:", "prompt:")
    for pattern in meta_patterns:
        if pattern in lower:
            issues.append({"code": "humanizer_meta_residue", "severity": "P0", "message": f"meta/prompt residue remains: {pattern}"})
    ai_markers = ("pivotal", "crucial", "significant", "tapestry", "showcase", "stands as", "serves as", "not only")
    marker_hits = [marker for marker in ai_markers if marker in lower]
    if len(marker_hits) >= 3:
        issues.append({"code": "generic_ai_diction", "severity": "P1", "message": f"generic AI diction remains: {', '.join(marker_hits[:5])}"})
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if duplicate_ratio(paragraphs) >= 0.25 and len(paragraphs) >= 4:
        issues.append({"code": "duplicate_paragraphs", "severity": "P1", "message": "paragraph duplication remains high"})
    sentences = [part.strip() for part in re.split(r"[.!?。！？]+", text) if part.strip()]
    if duplicate_ratio(sentences) >= 0.2 and len(sentences) >= 8:
        warnings.append("sentence repetition remains high")
    lengths = [len(re.sub(r"\s+", "", sentence)) for sentence in sentences]
    if lengths and max(lengths) - min(lengths) < 8 and len(lengths) >= 8:
        warnings.append("sentence lengths are too uniform; vary pressure and release")
    dialogue_marks = text.count('"') + text.count("'") + text.count("“") + text.count("”") + text.count("「") + text.count("」")
    if dialogue_marks == 0 and len(re.sub(r"\s+", "", text)) > 800:
        warnings.append("no visible dialogue; verify scene dramatization")
    if not strong_tail_hook(text):
        warnings.append("tail hook is weak or abstract")
    return issues, warnings


def detect_humanizer_v2_issues(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Chinese web-novel Humanizer v2 detector with categorized evidence."""

    lower = text.lower()
    issues: list[dict[str, Any]] = []
    warnings: list[str] = []

    for rule in CHINESE_HUMANIZER_CATALOG:
        hits = pattern_hits(text, rule["patterns"])
        if len(hits) >= int(rule.get("threshold") or 1):
            issues.append(
                {
                    "code": rule["code"],
                    "severity": rule["severity"],
                    "category": rule["category"],
                    "message": f"{rule['category']} remains: {', '.join(sorted({hit['pattern'] for hit in hits})[:5])}",
                    "evidence": hits[:5],
                    "suggestion": rule["suggestion"],
                }
            )
        elif hits:
            warnings.append(
                f"{rule['category']} warning: {', '.join(sorted({hit['pattern'] for hit in hits})[:3])}; {rule['suggestion']}"
            )

    trio_hits = template_trio_hits(text)
    if trio_hits:
        issues.append(
            {
                "code": "humanizer_template_triad",
                "severity": "P2",
                "category": "模板三连",
                "message": "template triple structure remains",
                "evidence": trio_hits[:5],
                "suggestion": "拆掉三连排比，只保留真正改变局势的两项或改成具体动作链。",
            }
        )

    ai_markers = ("pivotal", "crucial", "significant", "tapestry", "showcase", "stands as", "serves as", "not only")
    marker_hits = [marker for marker in ai_markers if marker in lower]
    if len(marker_hits) >= 3:
        issues.append(
            {
                "code": "generic_ai_diction",
                "severity": "P1",
                "category": "英文通用 AI 词",
                "message": f"generic AI diction remains: {', '.join(marker_hits[:5])}",
                "evidence": [{"pattern": marker, "count": lower.count(marker), "snippet": evidence_span(text, marker)} for marker in marker_hits[:5]],
                "suggestion": "replace generic AI diction with specific scene consequence or plain verbs",
            }
        )

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    if duplicate_ratio(paragraphs) >= 0.25 and len(paragraphs) >= 4:
        issues.append(
            {
                "code": "duplicate_paragraphs",
                "severity": "P1",
                "category": "重复段落",
                "message": "paragraph duplication remains high",
                "evidence": [{"pattern": "duplicate_paragraph_ratio", "count": len(paragraphs), "snippet": paragraphs[0][:80] if paragraphs else ""}],
                "suggestion": "rewrite repeated paragraphs into distinct scene beats with changed pressure",
            }
        )

    sentences = split_sentences(text)
    if duplicate_ratio(sentences) >= 0.2 and len(sentences) >= 8:
        warnings.append("sentence repetition remains high")
    lengths = [len(re.sub(r"\s+", "", sentence)) for sentence in sentences]
    if lengths and max(lengths) - min(lengths) < 8 and len(lengths) >= 8:
        issues.append(
            {
                "code": "humanizer_uniform_sentence_length",
                "severity": "P2",
                "category": "等长句",
                "message": "sentence lengths are too uniform; vary pressure and release",
                "evidence": [{"pattern": "uniform_sentence_lengths", "count": len(lengths), "snippet": ", ".join(str(length) for length in lengths[:8])}],
                "suggestion": "紧张动作拆成短句，解释句合并或删减，让句长跟压力变化。",
            }
        )

    dialogue_marks = text.count('"') + text.count("'") + text.count("\u201c") + text.count("\u201d") + text.count("\u300c") + text.count("\u300d")
    if dialogue_marks == 0 and len(re.sub(r"\s+", "", text)) > 800:
        warnings.append("no visible dialogue; verify scene dramatization")
    if not strong_tail_hook(text):
        warnings.append("tail hook is weak or abstract")
    return issues, warnings


def pattern_hits(text: str, patterns: tuple[str, ...]) -> list[dict[str, Any]]:
    lower = text.lower()
    hits: list[dict[str, Any]] = []
    for pattern in patterns:
        haystack = lower if pattern.isascii() else text
        needle = pattern.lower() if pattern.isascii() else pattern
        count = haystack.count(needle)
        if count:
            hits.append({"pattern": pattern, "count": count, "snippet": evidence_span(text, pattern)})
    return hits


def template_trio_hits(text: str) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    for regex in CHINESE_TEMPLATE_TRIO_REGEXES:
        for match in re.finditer(regex, text):
            hits.append({"pattern": regex, "count": 1, "snippet": match.group(0)[:120]})
    return hits


def split_sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[.!?\u3002\uff01\uff1f]+", text) if part.strip()]


def evidence_span(text: str, pattern: str, *, window: int = 32) -> str:
    index = text.lower().find(pattern.lower()) if pattern.isascii() else text.find(pattern)
    if index < 0:
        return ""
    start = max(0, index - window)
    end = min(len(text), index + len(pattern) + window)
    return text[start:end].replace("\n", " ")


def reader_experience_review(
    config: ConfigDocument,
    *,
    chapter_number: int,
    text: str,
    artifact_dir: Path,
    tier: str,
) -> dict[str, Any]:
    issues: list[str] = []
    warnings: list[str] = []
    lower = text.lower()
    payoff_markers = ("payoff", "won", "choice", "cost", "clue", "truth", "发现", "选择", "代价", "线索", "收获")
    hook_markers = ("?", "？", "but", "however", "suddenly", "忽然", "可是", "然而", "门外", "下一刻")
    if tier == "fast" and len(re.findall(r"(fight|kill|explode|truth|reveal|决战|爆发|真相|揭露|杀)", lower)) >= 4:
        warnings.append("continuous high-intensity beats need a buffer or cost beat")
    if not any(marker in lower for marker in payoff_markers):
        warnings.append("reader payoff is weak or implicit")
    tail = text[-500:]
    if not any(marker in tail.lower() for marker in hook_markers):
        issues.append("ending hook is weak; chapter ends without a concrete next pressure")
    if repeated_scene_fatigue(text):
        warnings.append("repeated scene shape may cause reader fatigue")
    if emotion_turn_without_evidence(text):
        issues.append("emotion turn appears without visible evidence or action")

    report_file = artifact_dir / "reader_experience_review.md"
    atomic_write_text(
        report_file,
        "\n".join(
            [
                f"# Reader Experience Review ch{chapter_number:03d}",
                "",
                f"- Pacing tier: {tier}",
                "",
                "## Issues",
                "",
                *([f"- {issue}" for issue in issues] or ["- None"]),
                "",
                "## Warnings",
                "",
                *([f"- {warning}" for warning in warnings] or ["- None"]),
                "",
            ]
        ),
    )
    return {"report_file": str(report_file), "issues": issues, "warnings": warnings}


def creative_repair_guidance(failure: dict[str, Any], chapter_number: int) -> dict[str, Any]:
    code = str(failure.get("code") or "")
    base = {
        "failure_code": code,
        "preserve": ["chapter duty", "canonical facts", "approved character state"],
        "delete_or_reduce": [],
        "add_evidence": [],
        "character_state_adjustment": [],
        "humanizer_target": ["remove summary lecture", "vary sentence and paragraph rhythm"],
        "rewrite_goal": "repair the failed gate and keep the chapter in the same canonical lane",
    }
    if code in {"meta_pollution", "humanizer_meta_pollution", "humanizer_meta_residue"}:
        base["delete_or_reduce"] = ["TODO/prompt labels", "AI self-reference", "author instructions"]
        base["rewrite_goal"] = "turn all instruction residue into clean in-world prose or remove it"
    elif code == "word_count":
        base["add_evidence"] = ["one extra conflict beat", "one consequence beat", "one sensory anchor"]
        base["rewrite_goal"] = "reach configured length through scene material, not padding"
    elif code == "pacing":
        base["delete_or_reduce"] = ["stacked major reveals", "consecutive high-intensity events without cost"]
        base["add_evidence"] = ["cooldown beat", "reader payoff", "tail hook with unresolved pressure"]
    elif code in {"duplicate_paragraphs", "generic_ai_diction"}:
        base["delete_or_reduce"] = ["repeated templates", "generic significance words", "same-shape paragraphs"]
        base["humanizer_target"] = ["differentiate scene purpose", "replace abstraction with action"]
    elif code in {"humanizer_inflated_significance", "humanizer_summary_voice"}:
        base["delete_or_reduce"] = ["abstract significance claims", "author summary voice", "chapter-level explanation"]
        base["add_evidence"] = ["visible consequence", "scene decision", "dialogue pressure"]
        base["humanizer_target"] = ["convert commentary into action", "replace summary with scene evidence"]
        base["rewrite_goal"] = "turn inflated or summary prose into concrete web-novel scene pressure"
    elif code in {"humanizer_cliche_action", "humanizer_high_frequency_words", "humanizer_weak_adverbs", "humanizer_template_triad", "humanizer_uniform_sentence_length"}:
        base["delete_or_reduce"] = ["template gestures", "weak adverb stacks", "rule-of-three phrasing", "same-length sentences"]
        base["add_evidence"] = ["character-specific action", "sensory anchor", "varied sentence rhythm"]
        base["humanizer_target"] = ["make body language specific", "break uniform cadence", "reduce high-frequency filler"]
        base["rewrite_goal"] = "restore human-feeling Chinese web-novel texture without changing canon facts"
    elif code == "style_drift":
        base["delete_or_reduce"] = ["sentence rhythm that ignores the active sample", "paragraph scale drift", "POV switches"]
        base["add_evidence"] = ["sample-matched sentence cadence", "dialogue density aligned to current_style_profile", "paragraph rhythm variation from style fingerprint"]
        base["rewrite_goal"] = "restore the active sample style without copying phrases or changing canon facts"
    elif code.startswith("semantic_") or "character" in code:
        base["add_evidence"] = ["motivation evidence span", "relationship transition beat", "known-fact boundary"]
        base["character_state_adjustment"] = ["align knowledge, speech, and action with Character Memory and TCS"]
    return base


def resolve_humanizer_source(root: Path, chapter_number: int, source: str) -> Path:
    if source == "draft":
        return root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    if source == "repair-candidate":
        candidates = sorted((root / "50_workbench" / "repair_candidates").glob(f"ch{chapter_number:03d}*.md"))
        if candidates:
            return candidates[-1]
        agent_candidates = sorted((root / "50_workbench" / "agent_drafts").glob(f"ch{chapter_number:03d}*.repair_candidate.md"))
        if agent_candidates:
            return agent_candidates[-1]
        return root / "50_workbench" / "repair_candidates" / f"ch{chapter_number:03d}.repair_candidate.md"
    raise ValueError("source must be draft or repair-candidate.")


def resolve_expansion_source(root: Path, chapter_number: int, source: str) -> Path:
    if source == "draft":
        return root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    if source == "repair-candidate":
        candidates = sorted((root / "50_workbench" / "repair_candidates").glob(f"ch{chapter_number:03d}*.md"))
        if candidates:
            return candidates[-1]
        return root / "50_workbench" / "repair_candidates" / f"ch{chapter_number:03d}.repair_candidate.md"
    if source == "agent-draft":
        candidates = sorted((root / "50_workbench" / "agent_drafts").glob(f"ch{chapter_number:03d}*.md"))
        if candidates:
            return candidates[-1]
        return root / "50_workbench" / "agent_drafts" / f"ch{chapter_number:03d}.codex.md"
    raise ValueError("source must be draft, repair-candidate, or agent-draft.")


def normalize_expansion_types(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return EXPANSION_TYPES
    normalized: list[str] = []
    for value in values:
        item = normalize_key(value)
        if item not in EXPANSION_TYPES:
            raise ValueError(f"unknown expansion type: {value}")
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def expansion_minimum_words(config: ConfigDocument) -> int:
    wc_config = config.data.get("length", {}).get("chapter_word_count", {})
    if not isinstance(wc_config, dict):
        return 0
    return int(wc_config.get("hard_min") or wc_config.get("min") or 0)


def expansion_instructions(expansion_types: tuple[str, ...]) -> list[str]:
    catalog = {
        "scene": "Scene expansion: add concrete place, sensory anchor, object interaction, and changed spatial pressure.",
        "dialogue": "Dialogue reinforcement: add speaker intent, status friction, subtext, and non-interchangeable rhythm.",
        "psychology": "Psychology deepening: show fear, hesitation, desire, or realization through body/action before naming it.",
        "action": "Action detailing: break outcomes into visible moves, obstacles, costs, and reactions.",
        "transition": "Transition smoothing: connect scene turns with time, cause, decision, or consequence beats.",
    }
    return [catalog[item] for item in expansion_types]


def writing_brief_snapshot(payload: Any) -> str:
    if not isinstance(payload, dict):
        return "No writing task JSON found."
    snapshot = {
        "chapter_number": payload.get("chapter_number"),
        "writing_brief": payload.get("writing_brief", {}),
        "beat_expansion_requirements": payload.get("beat_expansion_requirements", []),
        "constraint_packet": payload.get("constraint_packet", {}),
    }
    return "\n".join(["```json", json.dumps(snapshot, ensure_ascii=False, indent=2), "```"])


def detect_expansion_issues(
    root: Path,
    chapter_number: int,
    target: Path,
    text: str,
    expansion_types: tuple[str, ...],
    minimum_word_count: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    word_count = estimate_words(text)
    issues: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not is_workbench_candidate(root, target):
        issues.append(
            {
                "code": "expansion_candidate_path",
                "severity": "P0",
                "message": "expansion candidates must stay in 50_workbench/repair_candidates or 50_workbench/agent_drafts",
            }
        )
    if minimum_word_count and word_count < minimum_word_count:
        issues.append(
            {
                "code": "expansion_word_count",
                "severity": "P1",
                "message": f"expanded candidate is still below minimum: {word_count} < {minimum_word_count}",
            }
        )
    source_path = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    if source_path.exists() and word_count <= estimate_words(safe_read_text(source_path)):
        issues.append(
            {
                "code": "expansion_not_longer",
                "severity": "P1",
                "message": "expanded candidate did not add measurable manuscript material",
            }
        )
    for expansion_type in expansion_types:
        if not expansion_evidence(text, expansion_type):
            issues.append(
                {
                    "code": f"expansion_missing_{expansion_type}",
                    "severity": "P1",
                    "message": f"candidate lacks visible {expansion_type} expansion evidence",
                }
            )
    if not strong_tail_hook(text):
        warnings.append("tail hook is weak or abstract after expansion")
    return issues, warnings


def is_workbench_candidate(root: Path, target: Path) -> bool:
    for directory in (root / "50_workbench" / "repair_candidates", root / "50_workbench" / "agent_drafts"):
        try:
            target.resolve().relative_to(directory.resolve())
            return True
        except ValueError:
            continue
    return False


def expansion_evidence(text: str, expansion_type: str) -> bool:
    lower = text.lower()
    if expansion_type == "scene":
        return any(
            marker in lower
            for marker in (
                "gate",
                "room",
                "road",
                "stone",
                "wind",
                "light",
                "sound",
                "smell",
                "door",
                "hall",
            )
        )
    if expansion_type == "dialogue":
        return any(mark in text for mark in ('"', "'", ":", "-", "\u201c", "\u201d", "\u300c", "\u300d"))
    if expansion_type == "psychology":
        return any(
            marker in lower
            for marker in (
                "thought",
                "feared",
                "realized",
                "hesitated",
                "wanted",
                "heart",
                "breath",
                "mind",
                "doubt",
            )
        )
    if expansion_type == "action":
        return any(
            marker in lower
            for marker in (
                "stepped",
                "grabbed",
                "turned",
                "pushed",
                "ran",
                "opened",
                "raised",
                "moved",
                "struck",
            )
        )
    if expansion_type == "transition":
        return any(
            marker in lower
            for marker in (
                "then",
                "after",
                "when",
                "by the time",
                "finally",
                "next",
                "before",
                "meanwhile",
                "therefore",
            )
        )
    return False


def resolve_input_file(root: Path, file_path: str | Path) -> Path:
    raw = Path(file_path).expanduser()
    if raw.is_absolute():
        return raw.resolve()
    cwd_candidate = (Path.cwd() / raw).resolve()
    if cwd_candidate.exists():
        return cwd_candidate
    return (root / raw).resolve()


def repeated_scene_fatigue(text: str) -> bool:
    paragraphs = [part.strip().lower()[:40] for part in re.split(r"\n\s*\n+", text) if part.strip()]
    return duplicate_ratio(paragraphs) >= 0.25 and len(paragraphs) >= 6


def emotion_turn_without_evidence(text: str) -> bool:
    turn_markers = ("forgave", "trusted", "suddenly understood", "原谅", "信任", "忽然明白", "突然释然")
    evidence_markers = ("because", "cost", "saved", "choice", "evidence", "因为", "代价", "救", "证据", "选择")
    lower = text.lower()
    return any(marker in lower for marker in turn_markers) and not any(marker in lower for marker in evidence_markers)


def strong_tail_hook(text: str) -> bool:
    tail = text[-500:].lower()
    return any(marker in tail for marker in ("?", "？", "suddenly", "but", "however", "secret", "clue", "忽然", "可是", "然而", "线索", "秘密"))


def duplicate_ratio(items: list[str]) -> float:
    normalized = [item.strip() for item in items if item.strip()]
    if not normalized:
        return 0.0
    return 1 - (len(set(normalized)) / len(normalized))


def normalize_key(value: str) -> str:
    key = re.sub(r"[^a-zA-Z0-9_\-]+", "_", str(value or "").strip().lower()).strip("_")
    return key or "general"


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def estimate_words(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
