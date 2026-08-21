"""Creative operator protocol, humanizer, and style playbook helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.agent_protocols import (
    EVIDENCE_REVIEW_SCHEMA,
    VALIDATION_REPORT_SCHEMA,
    build_validation_report,
    output_protocol_for_task,
    validate_evidence_review,
    validate_review_evidence_for_sources,
)
from longform_engine.chapter_contract import ChapterContractError, load_verified_chapter_contract
from longform_engine.agent_tasks import (
    build_manifest,
    list_manifests,
    manifest_input_paths,
    manifest_output,
    mark_tasks_for_chapter_type,
    mark_tasks_for_output,
    validate_current_task_result,
    write_manifest,
)
from longform_engine.character_expression import character_expression_diagnostics
from longform_engine.config import ConfigDocument
from longform_engine.lengths import compile_length_forecast
from longform_engine.quality import (
    compact_effective_quality_contract,
    compile_effective_quality_contract,
)
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path
from longform_engine.text_metrics import content_character_count


CREATIVE_BRIEF_FIELDS = (
    "target_audience",
    "writing_style",
    "reader_contract",
    "core_taboo",
    "automation_level",
    "target_scale",
    "story_profile",
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
        "severity": "P2",
        "patterns": ("意义深远", "深远意义", "不言而喻", "命运的齿轮", "历史性的时刻", "至关重要", "举足轻重"),
        "threshold": 1,
        "suggestion": "把抽象拔高改成角色能看见、付出或误判的具体后果。",
    },
    {
        "code": "humanizer_summary_voice",
        "category": "总结腔",
        "severity": "P2",
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
    {
        "code": "humanizer_information_dump",
        "category": "信息轰炸",
        "severity": "P2",
        "patterns": ("众所周知", "需要说明的是", "简单来说", "换句话说", "值得一提的是", "关于这一点"),
        "threshold": 2,
        "suggestion": "把背景信息拆进冲突、误判、代价和角色当前需要，不要连续讲解设定。",
    },
    {
        "code": "humanizer_upgrade_log",
        "category": "流水账升级",
        "severity": "P2",
        "patterns": ("然后他", "接着他", "随后他", "第一步", "第二步", "第三步", "经验值", "属性提升"),
        "threshold": 3,
        "suggestion": "保留真正改变选择或关系的升级节点，删掉过程清单和无代价数值播报。",
    },
    {
        "code": "humanizer_emotion_label",
        "category": "情绪标签",
        "severity": "P2",
        "patterns": ("他很愤怒", "她很愤怒", "他很悲伤", "她很悲伤", "感到十分", "内心充满", "心中涌起"),
        "threshold": 2,
        "suggestion": "先写动作、判断、身体代价和选择，再决定是否需要命名情绪。",
    },
    {
        "code": "humanizer_forced_hook",
        "category": "强制钩子",
        "severity": "P2",
        "patterns": ("欲知后事如何", "一场更大的风暴", "真正的挑战才刚刚开始", "这只是开始", "未完待续"),
        "threshold": 1,
        "suggestion": "用具体的新信息、决定、威胁或误解收尾，不用抽象预告替代章节变化。",
    },
)

CHINESE_TEMPLATE_TRIO_REGEXES = (
    r"不仅[^。！？!?]{0,40}还[^。！？!?]{0,40}更",
    r"不是[^。！？!?]{0,40}而是[^。！？!?]{0,40}更是",
    r"有[^。！？!?]{0,18}有[^。！？!?]{0,18}还有",
)

HUMANIZER_FACT_DIMENSIONS = (
    "actor_action_object",
    "event_outcome",
    "causality",
    "chronology",
    "relationship_state",
    "ability_cost",
    "forbidden_reveals",
)
FINAL_LANE = "fin" + "al"
RAG_LANE = "60_" + "rag"
RUNTIME_DB_LANE = "70_runtime/" + "db"
STORY_GRAPH_NAME = "story_" + "graph.json"


@dataclass(frozen=True)
class CreativeBriefResult:
    ok: bool
    brief_file: str
    task_file: str
    errors: tuple[str, ...]
    created: bool


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
    need_human: bool
    issue_summary: dict[str, Any]
    issues: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    semantic_review_required: bool
    semantic_review_reasons: tuple[str, ...]
    semantic_task_file: str
    next_command: str


@dataclass(frozen=True)
class HumanizeSemanticTaskResult:
    chapter_number: int
    source_file: str
    candidate_file: str
    task_file: str
    manifest_file: str
    output_file: str
    reasons: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class HumanizeSemanticValidateResult:
    chapter_number: int
    ok: bool
    passed: bool
    need_human: bool
    file: str
    report_file: str
    errors: tuple[str, ...]
    blocking_findings: tuple[str, ...]
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
    metric: str
    current_content_characters: int
    minimum_content_characters: int
    missing_content_characters: int
    next_command: str


@dataclass(frozen=True)
class ExpandCheckResult:
    chapter_number: int
    file: str
    report_file: str
    markdown_report: str
    passed: bool
    metric: str
    content_characters: int
    minimum_content_characters: int
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
    forecast = compile_length_forecast(length)
    target_scale = confirmations.get("target_scale") or (
        f"{forecast.target_total_characters} content characters / "
        f"about {forecast.estimated_chapters} chapters / {forecast.support_status}"
    )
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
            "core_promise": novel.get("core_promise", ""),
            "main_question": novel.get("main_question", ""),
            "ending_direction": novel.get("ending_direction", ""),
        },
        "core_taboo": as_list(core_taboo),
        "automation_level": confirmations.get("automation_level") or "agent_skill with human approval for finalization",
        "target_scale": target_scale,
        "story_profile": config.data.get("story_profile", {}),
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
    sentence_lengths = [content_character_count(sentence) for sentence in sentences]
    paragraph_lengths = [content_character_count(paragraph) for paragraph in paragraphs]
    total_chars = max(1, len(compact))
    dialogue_marks = sum(text.count(mark) for mark in ('"', "'", "\u201c", "\u201d", "\u300c", "\u300d"))
    dialogue_paragraphs = [paragraph for paragraph in paragraphs if has_dialogue_marker(paragraph)]
    punctuation = sum(text.count(mark) for mark in ",.!?;:\u3002\uff0c\uff01\uff1f\uff1b\uff1a\u3001")
    pov = detect_pov(text)
    action = action_preference(text, total_chars)
    expression = character_expression_diagnostics(text)
    fingerprint = {
        "paragraph_count": len(paragraphs),
        "sentence_count": len(sentences),
        "avg_sentence_chars": round(mean(sentence_lengths), 2),
        "avg_paragraph_chars": round(mean(paragraph_lengths), 2),
        "paragraph_variance": round(mean_absolute_deviation(paragraph_lengths), 2),
        "dialogue_ratio": expression["dialogue_char_ratio"],
        "dialogue_char_ratio": expression["dialogue_char_ratio"],
        "dialogue_mark_density": round(dialogue_marks / total_chars, 4),
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
        "reader_gain": card.get("reader_gain") or "deliver one local payoff without resolving the core longform promise",
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
            "enter through a concrete place, body state, action, or necessary aftermath when it changes perception or choice",
            "use detail only when it affects perception, judgment, action, cost, or relationship",
            "let action carry psychology when that is truer to the current character and scene",
        ],
        "ending_state": card.get("hook") or (beat_hooks[-1] if beat_hooks else "leave a changed situation or emotional aftereffect"),
        "forbidden_reveals": as_list(card.get("forbidden_reveals")),
        "natural_prose_priorities": [
            "put the declared desire, resistance, choice, cost, gain, and protected outcome into the scene",
            "preserve character-specific perception, strategy, emotion, and relationship pressure",
            "remove task or prompt residue before submission",
            "avoid repeating one narrative function when no new action, information, or consequence is added",
        ],
        "style_memory": style_context,
        "creative_brief_status": creative.get("status", "missing"),
    }


def humanizer_rules() -> dict[str, Any]:
    return {
        "schema_version": 4,
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
                "只补会改变感知、判断、行动、关系或身体代价的有效细节，不设置感官配额。",
                "句长跟随当前压力与人物意识，不把短句或整齐变化当作目标。",
                "增强对白差异：每个说话人带不同目的、遮掩、身份压力或关系变化。",
                "章末必须留下状态变化或情绪余波，但不强制悬崖、反转或强尾钩。",
                "保留人物包声明的感知偏向、决策偏向、话语层级、社交面具和情绪泄漏。",
                "强化场景中的相反欲望、隐藏议程、不可逆行动和情绪余波，但不得改动 canonical 事实。",
                "不得用通用口头禅、强加方言、固定外貌段落或统一对白配额来伪造人物差异。",
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
            "platform guidance is not a sentence-length, dialogue-ratio, payoff, or cliffhanger quota",
        ],
    }


def author_natural_prose_policy() -> dict[str, Any]:
    """Return writer-facing principles without detector labels, codes, quotas, or word lists."""

    return {
        "before_submit": [
            "remove all task instructions, placeholders, and out-of-world author notes",
            "make the chapter's desire, resistance, choice, cost, gain, and changed exit state observable",
            "preserve character-specific perception, decision strategy, emotional ownership, and relationship pressure",
            "remove repeated explanation when it adds no action, information, choice, or emotional consequence",
            "use detail only when it changes perception, judgment, action, cost, or relationship",
            "follow the current scene and author preference; do not impose sentence, dialogue, sensory, pace, or cliffhanger quotas",
        ],
        "protected": [
            "chapter contract",
            "knowledge boundaries",
            "ability costs",
            "relationship stage",
            "protected outcomes",
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
    current_content_characters = content_character_count(source_text)
    minimum_content_characters = expansion_minimum_content_characters(config)
    missing_content_characters = max(0, minimum_content_characters - current_content_characters)
    gate_artifact_dir = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}"
    gate_payload = load_json(gate_artifact_dir / "gate_result.json", default={})
    gate_failures = gate_payload.get("failures", []) if isinstance(gate_payload, dict) else []
    gate_failures = [
        {
            key: item.get(key)
            for key in ("code", "severity", "message", "repair_action")
            if isinstance(item, dict) and item.get(key) not in (None, "", [], {})
        }
        for item in gate_failures[:8]
        if isinstance(item, dict)
    ]
    chapter_contract = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
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
                "- Metric: `content_characters_v1`",
                f"- Current content characters: {current_content_characters}",
                f"- Minimum content characters: {minimum_content_characters}",
                f"- Missing content characters: {missing_content_characters}",
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
                "## Writing Contract",
                "",
                f"- Read the unique chapter contract: `{relative_path(root, chapter_contract)}`",
                "- Treat the source candidate as the complete prose and voice baseline for this repair.",
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
        input_files=[task_file, source_path, chapter_contract],
        allowed_output_paths=[candidate_file],
        output_schema=output_protocol_for_task("content_expand"),
        validate_command=next_command,
        apply_command=(
            f"longform-engine draft submit project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, candidate_file)} --agent codex --overwrite"
        ),
        failure_next_command=f"longform-engine creative expand-task project.yaml --chapter {chapter_number} --source {source}",
        context_policy={
            "required_files": [task_file, source_path, chapter_contract],
            "optional_files": [],
            "compiled_brief": task_file,
            "selection_report": task_file,
        },
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
        metric="content_characters_v1",
        current_content_characters=current_content_characters,
        minimum_content_characters=minimum_content_characters,
        missing_content_characters=missing_content_characters,
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
    content_characters = content_character_count(text)
    minimum_content_characters = expansion_minimum_content_characters(config)
    issues, warnings = detect_expansion_issues(
        root,
        chapter_number,
        target,
        text,
        selected_types,
        minimum_content_characters,
    )
    humanizer_issues, humanizer_warnings = detect_humanizer_issues(text)
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
        "metric": "content_characters_v1",
        "content_characters": content_characters,
        "minimum_content_characters": minimum_content_characters,
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
                "- Metric: `content_characters_v1`",
                f"- Content characters: {content_characters}",
                f"- Minimum content characters: {minimum_content_characters}",
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
        metric="content_characters_v1",
        content_characters=content_characters,
        minimum_content_characters=minimum_content_characters,
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
    quality_contract = compact_effective_quality_contract(
        compile_effective_quality_contract(config, chapter_number=chapter_number)
    )
    contract_body = (
        quality_contract.get("contract")
        if isinstance(quality_contract.get("contract"), dict)
        else {}
    )
    compatibility = quality_contract.get("compatibility_observations", [])
    atomic_write_text(
        task_file,
        "\n".join(
            [
                f"# Humanizer v4 Task ch{chapter_number:03d}",
                "",
                f"- Source: `{relative_path(root, source_path)}`",
                f"- Candidate output: `{relative_path(root, candidate_file)}`",
                f"- Next command: `{next_command}`",
                "",
                "Write a repair/humanized candidate only. Do not edit final manuscripts, RAG, graph, memory, or SQLite.",
                "",
                "## Platform Writing Boundary",
                "",
                f"- Primary market: {quality_contract.get('primary_market', '')}",
                f"- Story phase: {quality_contract.get('phase', '')}",
                f"- Platform promise: {contract_body.get('platform_promise', '')}",
                f"- Blocking policy: {json.dumps(quality_contract.get('blocking_policy', {}), ensure_ascii=False)}",
                "- Platform guidance may improve expression and scene entry, but cannot change canon, causality, or character intent.",
                "- Do not optimize sentence length, dialogue ratio, payoff count, or cliffhanger endings as fixed platform quotas.",
                *[
                    f"- Advisory only [{item.get('market', '')}/{item.get('code', '')}]: {item.get('message', '')}"
                    for item in compatibility[:3]
                    if isinstance(item, dict)
                ],
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
                "- Preserve names, numbers, chronology, abilities, relationship state, and scene outcome.",
                "- Do not maximize surface difference. A rewrite over the configured change ratio requires human review.",
                "- Then run humanize-check.",
                "- If accepted, submit it with `draft submit`; only `chapter finalize` can enter canonical final/RAG/graph/memory.",
                "",
            ]
        ),
    )
    optional_inputs = [
        path
        for path in (
            root / "10_bible" / "style_bible.md",
            root / "10_bible" / "creative_brief.json",
            root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "humanize_report.md",
        )
        if path.is_file()
    ]
    manifest = build_manifest(
        root,
        task_type="humanize",
        chapter_number=chapter_number,
        input_files=[task_file, source_path, *optional_inputs],
        allowed_output_paths=[candidate_file],
        output_schema=output_protocol_for_task("humanize"),
        validate_command=next_command,
        apply_command=(
            f"longform-engine draft submit project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, candidate_file)} --agent codex --overwrite"
        ),
        failure_next_command=f"longform-engine creative humanize-task project.yaml --chapter {chapter_number} --source {source}",
        context_policy={
            "required_files": [task_file, source_path],
            "optional_files": optional_inputs,
            "compiled_brief": task_file,
            "selection_report": task_file,
        },
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
    issues, warnings = detect_humanizer_issues(text)
    source = humanizer_source_for_candidate(root, chapter_number, target)
    source_text = safe_read_text(source) if source is not None and source.exists() else ""
    fact_issues, fact_warnings = humanizer_fact_drift(root, source_text, text)
    issues.extend(fact_issues)
    warnings.extend(fact_warnings)
    report_dir = root / "50_workbench" / "humanizer_tasks"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"ch{chapter_number:03d}.humanize_check.json"
    md_file = report_dir / f"ch{chapter_number:03d}.humanize_check.md"
    need_human = any(item.get("code") in {"humanizer_number_drift", "humanizer_character_drift"} for item in issues)
    passed = not any(item.get("severity") in {"P0", "P1"} for item in issues)
    semantic_reasons = (
        humanize_semantic_review_reasons(
            config,
            chapter_number=chapter_number,
        )
        if passed and source is not None
        else ()
    )
    semantic_required = bool(semantic_reasons)
    semantic_task_file = ""
    submit_command = (
        f"longform-engine draft submit project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, target)} --agent codex --overwrite"
    )
    if passed and semantic_required:
        semantic_status = humanize_semantic_submission_status(
            config,
            chapter_number=chapter_number,
            candidate_file=target,
        )
        if semantic_status["passed"]:
            next_command = submit_command
        else:
            semantic_task = humanize_semantic_task(
                config,
                chapter_number=chapter_number,
                candidate_file=target,
                reasons=semantic_reasons,
            )
            semantic_task_file = semantic_task.task_file
            next_command = (
                "longform-engine agent-task brief project.yaml "
                f"--task-id prose_revision_semantic_review:ch{chapter_number:03d}:humanizer:v4"
            )
    elif passed:
        next_command = submit_command
    else:
        mark_tasks_for_chapter_type(
            root,
            chapter_number=chapter_number,
            task_types=("prose_revision_semantic_review",),
            to_status="superseded",
            command="creative humanize-check",
            artifact=target,
            result=report_file,
            from_statuses=("awaiting_agent", "submitted", "validated", "invalid"),
        )
        next_command = (
            f"longform-engine editorial need-human project.yaml --chapter {chapter_number} --reason humanizer_fact_or_rewrite_risk"
            if need_human
            else f"longform-engine creative humanize-task project.yaml --chapter {chapter_number} --source draft"
        )
    payload = {
        "schema": "humanizer_check_v3",
        "schema_version": 3,
        "chapter_number": chapter_number,
        "file": relative_path(root, target),
        "source_file": relative_path(root, source) if source is not None else "",
        "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest() if source_text else "",
        "candidate_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "passed": passed,
        "need_human": need_human,
        "semantic_review_required": semantic_required,
        "semantic_review_reasons": list(semantic_reasons),
        "semantic_task_file": relative_path(root, Path(semantic_task_file)) if semantic_task_file else "",
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
                f"# Humanizer v4 Check ch{chapter_number:03d}",
                "",
                f"- File: `{relative_path(root, target)}`",
                f"- Passed: {passed}",
                f"- Need human: {need_human}",
                f"- Semantic review required: {semantic_required}",
                f"- Semantic review reasons: {', '.join(semantic_reasons) or 'none'}",
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
        need_human=need_human,
        issue_summary=humanizer_issue_summary(issues),
        issues=tuple(issues),
        warnings=tuple(warnings),
        semantic_review_required=semantic_required,
        semantic_review_reasons=semantic_reasons,
        semantic_task_file=semantic_task_file,
        next_command=next_command,
    )


def humanize_semantic_review_reasons(
    config: ConfigDocument,
    *,
    chapter_number: int,
) -> tuple[str, ...]:
    """Return deterministic reasons that require an independent Humanizer semantic review."""

    quality = config.data.get("quality", {}) if isinstance(config.data.get("quality"), dict) else {}
    humanizer = quality.get("humanizer", {}) if isinstance(quality.get("humanizer"), dict) else {}
    profile = quality.get("profile") if isinstance(quality.get("profile"), dict) else {}
    assurance_mode = str(profile.get("strictness") or "balanced")
    review_mode = str(humanizer.get("semantic_review_mode") or "risk_based")
    reasons: list[str] = ["dual_prose_semantic_review"]
    if assurance_mode == "strict" or review_mode == "always":
        reasons.append("strict_or_always_mode")
    if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction":
        reasons.append("fanfiction")
    milestones = {
        int(item)
        for item in quality.get("semantic_review_milestones", [])
        if isinstance(item, int) and not isinstance(item, bool) and item > 0
    }
    if chapter_number in milestones:
        reasons.append("semantic_review_milestone")
    if bool(quality.get("semantic_review_boundaries", True)) and humanizer_volume_boundary(config, chapter_number):
        reasons.append("volume_boundary")
    root = resolve_project_root(config)
    card = load_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json", default={})
    if isinstance(card, dict):
        if bool(card.get("requires_semantic_review")):
            reasons.append("chapter_card_requires_review")
        event_types = {
            normalize_key(str(item))
            for field in ("event_types", "recommended_event_types", "risk_types")
            for item in as_list(card.get(field))
        }
        if event_types.intersection(
            {
                "major_reveal",
                "reveal",
                "relationship_turn",
                "relationship_change",
                "ability_change",
                "power_change",
            }
        ):
            reasons.append("chapter_card_semantic_risk")
        if as_list(card.get("protected_reveals")) or as_list(card.get("forbidden_reveals")):
            reasons.append("protected_reveal_contract")
    return tuple(dict.fromkeys(reasons))


def humanizer_volume_boundary(config: ConfigDocument, chapter_number: int) -> bool:
    if chapter_number <= 0:
        return False
    root = resolve_project_root(config)
    plan = load_json(root / "20_outline" / "chapter_plan.json", default=[])
    if not isinstance(plan, list):
        return chapter_number == 1
    rows = {
        int(item.get("chapter_number") or 0): item
        for item in plan
        if isinstance(item, dict) and int(item.get("chapter_number") or 0) > 0
    }
    current = rows.get(chapter_number, {})
    previous = rows.get(chapter_number - 1, {})
    next_row = rows.get(chapter_number + 1, {})
    current_volume = str(current.get("volume_id") or "")
    return (
        chapter_number == 1
        or bool(current_volume and current_volume != str(previous.get("volume_id") or ""))
        or bool(current_volume and next_row and current_volume != str(next_row.get("volume_id") or ""))
        or str(current.get("phase") or "") in {"volume_climax", "aftermath"}
    )


def humanize_semantic_task(
    config: ConfigDocument,
    *,
    chapter_number: int,
    candidate_file: str | Path | None = None,
    reasons: tuple[str, ...] | list[str] | None = None,
) -> HumanizeSemanticTaskResult:
    """Create a source-versus-candidate semantic preservation review task."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    expected_candidate = root / "50_workbench" / "repair_candidates" / f"ch{chapter_number:03d}.humanized_candidate.md"
    candidate = resolve_input_file(root, candidate_file or expected_candidate)
    if candidate.resolve() != expected_candidate.resolve():
        raise ValueError(
            "Humanizer semantic review candidate must be "
            f"50_workbench/repair_candidates/ch{chapter_number:03d}.humanized_candidate.md."
        )
    if not candidate.exists() or not candidate.is_file():
        raise ValueError(f"Humanizer candidate not found: {candidate}")
    source = humanizer_source_for_candidate(root, chapter_number, candidate)
    if source is None or not source.exists():
        raise ValueError("Humanizer source could not be resolved from the active humanize task.")
    source_text = safe_read_text(source)
    candidate_text = safe_read_text(candidate)
    review_reasons = tuple(reasons or humanize_semantic_review_reasons(
        config,
        chapter_number=chapter_number,
    ))
    task_dir = root / "50_workbench" / "humanizer_tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / f"ch{chapter_number:03d}.semantic_review.md"
    manifest_file = task_dir / f"ch{chapter_number:03d}.semantic_review.agent_task.json"
    output_file = task_dir / f"ch{chapter_number:03d}.semantic_review.json"
    contract_context = task_dir / f"ch{chapter_number:03d}.semantic_review.contract.json"
    try:
        chapter_contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    except ChapterContractError as exc:
        raise ValueError(str(exc)) from exc
    write_json(
        contract_context,
        {
            "schema": "humanizer_contract_context_v1",
            "chapter_contract": chapter_contract,
            "chapter_contract_hash": contract_hash,
            "allowed_canonical_refs": [
                f"20_outline/chapter_cards/ch{chapter_number:03d}.json"
            ],
        },
    )
    context_candidates = [
        contract_context,
        root / "10_bible" / "style_profiles" / "current_style_profile.json",
        root / "10_bible" / "style_bible.md",
        root / "10_bible" / "characters.json",
        root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json",
    ]
    selected_context: list[Path] = []
    for path in context_candidates:
        if path.exists() and path not in selected_context:
            selected_context.append(path)
        if len(selected_context) >= 3:
            break
    source_lane = humanizer_source_lane(root, source)
    agent = str(
        config.data.get("writing", {}).get("agent", {}).get("default_agent")
        if isinstance(config.data.get("writing", {}).get("agent"), dict)
        else ""
    ) or "codex"
    validate_command = (
        f"longform-engine creative humanize-semantic-validate project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, output_file)}"
    )
    apply_command = (
        f"longform-engine draft submit project.yaml --chapter {chapter_number} "
        f"--file {relative_path(root, candidate)} --agent {agent} --overwrite"
    )
    failure_command = (
        f"longform-engine creative humanize-task project.yaml --chapter {chapter_number} --source {source_lane}"
    )
    atomic_write_text(
        task_file,
        "\n".join(
            [
                f"# Humanizer Semantic Preservation Review ch{chapter_number:03d}",
                "",
                "## Role And Objective",
                "",
                "You are the independent prose-revision semantic reviewer, not the rewriting Agent.",
                "Compare the source and candidate. Judge meaning preservation before prose polish.",
                f"- Trigger reasons: {', '.join(review_reasons) or 'manual request'}",
                "",
                "## Required Inputs",
                "",
                f"- Source: `{relative_path(root, source)}` (sha256 `{sha256_text(source_text)}`)",
                f"- Candidate: `{relative_path(root, candidate)}` (sha256 `{sha256_text(candidate_text)}`)",
                *[f"- Contract/context: `{relative_path(root, path)}`" for path in selected_context],
                "",
                "## Review Dimensions",
                "",
                "- `chapter_contract_preservation`",
                "- `knowledge_boundary_preservation`",
                "- `ability_cost_preservation`",
                "- `relationship_stage_preservation`",
                "- `protected_outcome_preservation`",
                "- `revision_goal_achievement`",
                "- Preserve chapter duty, reader gain, cost, forbidden reveals, and each declared character voice.",
                "- Report P0/P1 AI-taste or semantic findings even when the prose sounds smoother.",
                "",
                "## Output Contract",
                "",
                f"- Write one `{EVIDENCE_REVIEW_SCHEMA}` JSON: `{relative_path(root, output_file)}`",
                "- coverage 必须精确包含六项双稿保护维度；每项写 status、1-2 个正文 evidence_ids 和 canonical_refs。",
                "- finding codes 只使用 PROSE_REVISION_* 注册项。",
                "- evidence_ids may cite source or candidate as path/filename@start:end; CLI supplies hashes and scope.",
                f"- Validate: `{validate_command}`",
                f"- Apply after a pass: `{apply_command}`",
                f"- Failure: `{failure_command}`",
                "- Do not edit draft, final, RAG, graph, TCS, Bible, outline, or SQLite.",
                "",
            ]
        ),
    )
    inputs = [task_file, source, candidate, *selected_context]
    inputs = inputs[:6]
    required_inputs = inputs[: min(5, len(inputs))]
    optional_inputs = inputs[len(required_inputs):]
    manifest = build_manifest(
        root,
        task_type="prose_revision_semantic_review",
        chapter_number=chapter_number,
        input_files=inputs,
        allowed_output_paths=[output_file],
        output_schema=output_protocol_for_task("prose_revision_semantic_review"),
        validate_command=validate_command,
        apply_command=apply_command,
        failure_next_command=failure_command,
        task_id=f"prose_revision_semantic_review:ch{chapter_number:03d}:humanizer:v4",
        context_policy={
            "required_files": required_inputs,
            "optional_files": optional_inputs,
            "forbidden_paths": [
                "40_manuscript/" + FINAL_LANE + "/",
                "50_workbench/research_inbox/",
                "50_workbench/repair_candidates/ (except the declared candidate)",
                RAG_LANE + "/query_cache/",
                RUNTIME_DB_LANE + "/",
            ],
            "compiled_brief": task_file,
            "selection_report": task_file,
        },
    )
    write_manifest(root, manifest, manifest_file)
    return HumanizeSemanticTaskResult(
        chapter_number=chapter_number,
        source_file=str(source),
        candidate_file=str(candidate),
        task_file=str(task_file),
        manifest_file=str(manifest_file),
        output_file=str(output_file),
        reasons=review_reasons,
        next_command=validate_command,
    )


def humanize_semantic_validate(
    config: ConfigDocument,
    *,
    chapter_number: int,
    file_path: str | Path,
) -> HumanizeSemanticValidateResult:
    """Validate Humanizer semantic preservation evidence without modifying manuscript lanes."""

    root = resolve_project_root(config)
    task_dir = root / "50_workbench" / "humanizer_tasks"
    expected = (task_dir / f"ch{chapter_number:03d}.semantic_review.json").resolve()
    target = resolve_input_file(root, file_path)
    if target.resolve() != expected:
        raise ValueError(
            "Humanizer semantic result must be "
            f"50_workbench/humanizer_tasks/ch{chapter_number:03d}.semantic_review.json."
        )
    payload = load_json(target, default={})
    errors: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []
    need_human = False
    _task, control_errors = validate_current_task_result(
        root,
        chapter_number=chapter_number,
        task_type="prose_revision_semantic_review",
        output_path=target,
        allowed_statuses=("submitted", "validated"),
    )
    errors.extend(control_errors)
    if not isinstance(payload, dict):
        payload = {}
        errors.append("semantic review result must be a JSON object.")
    expected_dimensions = {
        "chapter_contract_preservation",
        "knowledge_boundary_preservation",
        "ability_cost_preservation",
        "relationship_stage_preservation",
        "protected_outcome_preservation",
        "revision_goal_achievement",
    }
    allowed_codes = {
        "PROSE_REVISION_FACT_DRIFT",
        "PROSE_REVISION_KNOWLEDGE_DRIFT",
        "PROSE_REVISION_ABILITY_COST_DRIFT",
        "PROSE_REVISION_RELATIONSHIP_DRIFT",
        "PROSE_REVISION_PROTECTED_OUTCOME_DRIFT",
        "PROSE_REVISION_NOT_SUBSTANTIVE",
    }
    errors.extend(
        validate_evidence_review(
            payload,
            required_dimensions=expected_dimensions,
            allowed_finding_codes=allowed_codes,
        )
    )
    candidate = root / "50_workbench" / "repair_candidates" / f"ch{chapter_number:03d}.humanized_candidate.md"
    source = humanizer_source_for_candidate(root, chapter_number, candidate)
    source_text = safe_read_text(source) if source is not None and source.exists() else ""
    candidate_text = safe_read_text(candidate) if candidate.exists() else ""
    if source is None or not source.exists():
        errors.append("Humanizer source could not be resolved from the active task.")
    if not candidate.exists():
        errors.append("Humanizer candidate is missing.")
    manifest = load_json(task_dir / f"ch{chapter_number:03d}.semantic_review.agent_task.json", default={})
    if not isinstance(manifest, dict) or manifest.get("task_type") != "prose_revision_semantic_review":
        errors.append("Humanizer semantic Agent task manifest is missing or invalid.")
        manifest = {}
    source_key = relative_path(root, source) if source is not None else ""
    candidate_key = relative_path(root, candidate)
    _evidence, evidence_errors = validate_review_evidence_for_sources(
        payload,
        sources={source_key: source_text, candidate_key: candidate_text},
    )
    errors.extend(evidence_errors)
    if set((payload.get("coverage") or {}).keys()) != expected_dimensions:
        errors.append("coverage must contain exactly the six prose revision semantic dimensions.")
    findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            continue
        if finding.get("code") not in allowed_codes:
            errors.append(f"findings[{index}].code is outside Humanizer semantic scope.")
        if finding.get("severity") in {"P0", "P1"}:
            blockers.append(str(finding.get("code") or f"finding_{index + 1}"))
        if finding.get("certainty") == "insufficient_evidence":
            need_human = True
    verdict = str(payload.get("verdict") or "").lower()
    if verdict not in {"pass", "repair", "need_human", "insufficient_evidence"}:
        errors.append("verdict is invalid.")
    if verdict == "pass" and blockers:
        errors.append("verdict=pass cannot override changed/uncertain facts, failed chapter contract, voice drift, or P0/P1 findings.")
    if verdict in {"need_human", "insufficient_evidence"}:
        need_human = True
    if verdict == "repair" and not blockers:
        warnings.append("repair verdict has no structured blocking finding.")
    ok = not errors
    passed = ok and verdict == "pass" and not blockers
    source_lane = humanizer_source_lane(root, source) if source is not None else "draft"
    if passed:
        next_command = (
            f"longform-engine draft submit project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, candidate)} --agent codex --overwrite"
        )
    elif need_human and ok:
        next_command = (
            f"longform-engine editorial need-human project.yaml --chapter {chapter_number} "
            "--reason humanizer_semantic_uncertainty"
        )
    elif ok:
        next_command = (
            f"longform-engine creative humanize-task project.yaml --chapter {chapter_number} --source {source_lane}"
        )
    else:
        next_command = (
            f"longform-engine creative humanize-semantic-task project.yaml --chapter {chapter_number} "
            f"--file {relative_path(root, candidate)}"
        )
    report_file = task_dir / f"ch{chapter_number:03d}.semantic_review.validation.json"
    report = build_validation_report(
        ok=ok,
        stage="humanizer_semantic_validate",
        subject=relative_path(root, target),
        errors=errors,
        warnings=warnings,
        blockers=blockers,
        provenance={
            "chapter_number": chapter_number,
            "source_path": relative_path(root, source) if source is not None else "",
            "source_sha256": sha256_text(source_text) if source_text else "",
            "candidate_path": relative_path(root, candidate),
            "candidate_sha256": sha256_text(candidate_text) if candidate_text else "",
            "passed": passed,
            "need_human": need_human,
        },
        next_command=next_command,
    )
    write_json(report_file, report)
    mark_tasks_for_output(
        root,
        chapter_number=chapter_number,
        output_path=target,
        to_status="validated" if passed else "invalid",
        command="creative humanize-semantic-validate",
        result=report_file,
        from_statuses=("awaiting_agent", "submitted", "validated", "invalid"),
    )
    return HumanizeSemanticValidateResult(
        chapter_number=chapter_number,
        ok=ok,
        passed=passed,
        need_human=need_human,
        file=str(target),
        report_file=str(report_file),
        errors=tuple(errors),
        blocking_findings=tuple(blockers),
        warnings=tuple(warnings),
        next_command=next_command,
    )


def humanize_semantic_submission_status(
    config: ConfigDocument,
    *,
    chapter_number: int,
    candidate_file: str | Path,
) -> dict[str, Any]:
    """Check whether the current source/candidate pair has a passing semantic review."""

    root = resolve_project_root(config)
    candidate = resolve_input_file(root, candidate_file)
    source = humanizer_source_for_candidate(root, chapter_number, candidate)
    if source is None or not source.exists() or not candidate.exists():
        return {"required": True, "passed": False, "reason": "source_or_candidate_missing"}
    source_text = safe_read_text(source)
    candidate_text = safe_read_text(candidate)
    reasons = humanize_semantic_review_reasons(
        config,
        chapter_number=chapter_number,
    )
    if not reasons:
        return {"required": False, "passed": True, "reason": "not_required", "reasons": []}
    report_file = (
        root
        / "50_workbench"
        / "humanizer_tasks"
        / f"ch{chapter_number:03d}.semantic_review.validation.json"
    )
    report = load_json(report_file, default={})
    provenance = report.get("provenance") if isinstance(report.get("provenance"), dict) else {}
    current = (
        isinstance(report, dict)
        and report.get("schema") == VALIDATION_REPORT_SCHEMA
        and report.get("ok") is True
        and provenance.get("passed") is True
        and str(provenance.get("source_path") or "") == relative_path(root, source)
        and str(provenance.get("source_sha256") or "") == sha256_text(source_text)
        and str(provenance.get("candidate_path") or "") == relative_path(root, candidate)
        and str(provenance.get("candidate_sha256") or "") == sha256_text(candidate_text)
    )
    return {
        "required": True,
        "passed": current,
        "reason": "validated" if current else "semantic_review_missing_or_stale",
        "reasons": list(reasons),
        "report_file": relative_path(root, report_file),
    }


def humanize_candidate_submission_guard(
    config: ConfigDocument,
    *,
    chapter_number: int,
    candidate_file: str | Path,
) -> dict[str, Any]:
    """Enforce current deterministic and semantic Humanizer checks before draft submit."""

    root = resolve_project_root(config)
    candidate = resolve_input_file(root, candidate_file)
    expected = root / "50_workbench" / "repair_candidates" / f"ch{chapter_number:03d}.humanized_candidate.md"
    if candidate.resolve() != expected.resolve():
        return {"required": False, "allowed": True, "reason": "not_humanizer_candidate"}
    candidate_text = safe_read_text(candidate) if candidate.exists() else ""
    source = humanizer_source_for_candidate(root, chapter_number, candidate)
    source_text = safe_read_text(source) if source is not None and source.exists() else ""
    check_file = root / "50_workbench" / "humanizer_tasks" / f"ch{chapter_number:03d}.humanize_check.json"
    check = load_json(check_file, default={})
    deterministic_passed = (
        isinstance(check, dict)
        and check.get("schema") == "humanizer_check_v3"
        and check.get("passed") is True
        and str(check.get("file") or "") == relative_path(root, candidate)
        and str(check.get("candidate_sha256") or "") == sha256_text(candidate_text)
        and source is not None
        and str(check.get("source_file") or "") == relative_path(root, source)
        and str(check.get("source_sha256") or "") == sha256_text(source_text)
    )
    if not deterministic_passed:
        return {"required": True, "allowed": False, "reason": "humanizer_check_missing_failed_or_stale"}
    semantic = humanize_semantic_submission_status(
        config,
        chapter_number=chapter_number,
        candidate_file=candidate,
    )
    return {
        "required": True,
        "allowed": not semantic["required"] or semantic["passed"],
        "reason": semantic["reason"],
        "semantic": semantic,
    }


def validate_humanizer_file_identity(
    value: Any,
    *,
    label: str,
    expected_path: Path | None,
    expected_text: str,
    root: Path,
    errors: list[str],
) -> None:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        errors.append(f"{label} must contain exactly path and sha256.")
        return
    expected_rel = relative_path(root, expected_path) if expected_path is not None else ""
    if str(value.get("path") or "") != expected_rel:
        errors.append(f"{label}.path does not match the active Humanizer task.")
    if str(value.get("sha256") or "") != sha256_text(expected_text):
        errors.append(f"{label}.sha256 does not match the current file.")


def validate_humanizer_fact_item(
    value: Any,
    *,
    index: int,
    source_text: str,
    candidate_text: str,
    allowed_refs: set[str],
    known_entities: set[str],
    errors: list[str],
) -> tuple[str, str]:
    expected = {
        "dimension",
        "status",
        "source_span",
        "candidate_span",
        "canonical_refs",
        "entity_ids",
        "message",
    }
    if not isinstance(value, dict):
        errors.append(f"fact_preservation[{index}] must be an object.")
        return "", ""
    if set(value) != expected:
        errors.append(f"fact_preservation[{index}] keys must be exactly {sorted(expected)}.")
    dimension = str(value.get("dimension") or "")
    if dimension not in HUMANIZER_FACT_DIMENSIONS:
        errors.append(f"fact_preservation[{index}].dimension is invalid.")
    status = str(value.get("status") or "").lower()
    if status not in {"preserved", "changed", "uncertain"}:
        errors.append(f"fact_preservation[{index}].status must be preserved, changed, or uncertain.")
    validate_humanizer_span(
        value.get("source_span"),
        text=source_text,
        label=f"fact_preservation[{index}].source_span",
        errors=errors,
    )
    validate_humanizer_span(
        value.get("candidate_span"),
        text=candidate_text,
        label=f"fact_preservation[{index}].candidate_span",
        errors=errors,
    )
    validate_humanizer_refs_and_entities(
        value,
        label=f"fact_preservation[{index}]",
        allowed_refs=allowed_refs,
        known_entities=known_entities,
        errors=errors,
    )
    if not str(value.get("message") or "").strip():
        errors.append(f"fact_preservation[{index}].message is required.")
    return dimension, status


def validate_humanizer_voice_check(
    value: Any,
    *,
    index: int,
    candidate_text: str,
    known_entities: set[str],
    errors: list[str],
) -> str:
    expected = {"character_id", "status", "candidate_spans", "message"}
    if not isinstance(value, dict):
        errors.append(f"voice_checks[{index}] must be an object.")
        return ""
    if set(value) != expected:
        errors.append(f"voice_checks[{index}] keys must be exactly {sorted(expected)}.")
    character_id = str(value.get("character_id") or "")
    if character_id not in known_entities:
        errors.append(f"voice_checks[{index}] references unknown character_id: {character_id}.")
    status = str(value.get("status") or "").lower()
    if status not in {"preserved", "changed", "uncertain"}:
        errors.append(f"voice_checks[{index}].status must be preserved, changed, or uncertain.")
    spans = value.get("candidate_spans")
    if not isinstance(spans, list):
        errors.append(f"voice_checks[{index}].candidate_spans must be a list.")
    else:
        for span_index, span in enumerate(spans):
            validate_humanizer_span(
                span,
                text=candidate_text,
                label=f"voice_checks[{index}].candidate_spans[{span_index}]",
                errors=errors,
            )
    if not str(value.get("message") or "").strip():
        errors.append(f"voice_checks[{index}].message is required.")
    return status


def validate_humanizer_ai_finding(
    value: Any,
    *,
    index: int,
    candidate_text: str,
    errors: list[str],
) -> str:
    expected = {"code", "severity", "message", "candidate_span", "recommendation"}
    if not isinstance(value, dict):
        errors.append(f"ai_taste_findings[{index}] must be an object.")
        return ""
    if set(value) != expected:
        errors.append(f"ai_taste_findings[{index}] keys must be exactly {sorted(expected)}.")
    if not str(value.get("code") or "").strip():
        errors.append(f"ai_taste_findings[{index}].code is required.")
    severity = str(value.get("severity") or "").upper()
    if severity not in {"P0", "P1", "P2"}:
        errors.append(f"ai_taste_findings[{index}].severity must be P0, P1, or P2.")
    validate_humanizer_span(
        value.get("candidate_span"),
        text=candidate_text,
        label=f"ai_taste_findings[{index}].candidate_span",
        errors=errors,
    )
    if not str(value.get("message") or "").strip() or not str(value.get("recommendation") or "").strip():
        errors.append(f"ai_taste_findings[{index}] requires message and recommendation.")
    return severity


def validate_humanizer_span(value: Any, *, text: str, label: str, errors: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"start", "end", "text"}:
        errors.append(f"{label} must contain exactly start, end, text.")
        return
    start = value.get("start")
    end = value.get("end")
    quoted = value.get("text")
    if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool):
        errors.append(f"{label} start/end must be integers.")
    elif not (0 <= start < end <= len(text)):
        errors.append(f"{label} is outside the declared file.")
    elif quoted != text[start:end]:
        errors.append(f"{label}.text does not match the declared file slice.")


def validate_humanizer_refs_and_entities(
    value: dict[str, Any],
    *,
    label: str,
    allowed_refs: set[str],
    known_entities: set[str],
    errors: list[str],
) -> None:
    refs = value.get("canonical_refs")
    if not isinstance(refs, list):
        errors.append(f"{label}.canonical_refs must be a list.")
    else:
        for ref in refs:
            normalized = str(ref).replace("\\", "/")
            if normalized not in allowed_refs:
                errors.append(f"{label} references undeclared canonical file: {normalized}.")
    entity_ids = value.get("entity_ids")
    if not isinstance(entity_ids, list):
        errors.append(f"{label}.entity_ids must be a list.")
    else:
        for entity_id in entity_ids:
            if str(entity_id) not in known_entities:
                errors.append(f"{label} references unknown entity_id: {entity_id}.")


def humanizer_semantic_known_entities(root: Path) -> set[str]:
    ids: set[str] = set()
    for path in (
        root / "10_bible" / "characters.json",
        root / "30_state" / STORY_GRAPH_NAME,
        root / "10_bible" / "fanfiction" / "source_canon.json",
    ):
        collect_humanizer_entity_ids(load_json(path, default={}), ids)
    return ids


def collect_humanizer_entity_ids(value: Any, ids: set[str]) -> None:
    if isinstance(value, dict):
        for key in ("id", "entity_id", "character_id"):
            item = str(value.get(key) or "").strip()
            if item:
                ids.add(item)
        for item in value.values():
            collect_humanizer_entity_ids(item, ids)
    elif isinstance(value, list):
        for item in value:
            collect_humanizer_entity_ids(item, ids)


def humanizer_source_lane(root: Path, source: Path) -> str:
    normalized = relative_path(root, source)
    return "draft" if normalized.startswith("40_manuscript/draft/") else "repair-candidate"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def detect_humanizer_issues(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Detect formulaic prose and prompt residue in a chapter candidate."""

    lower = text.lower()
    issues: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not re.sub(r"\s+", "", text):
        return [
            {
                "code": "humanizer_empty_candidate",
                "severity": "P0",
                "category": "空文本",
                "message": "humanizer candidate is empty",
                "evidence": [],
                "suggestion": "生成完整候选正文后重新运行 humanize-check。",
            }
        ], []

    for rule in CHINESE_HUMANIZER_CATALOG:
        hits = pattern_hits(text, rule["patterns"])
        total_hits = sum(int(hit.get("count") or 0) for hit in hits)
        if total_hits >= int(rule.get("threshold") or 1):
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
    if marker_hits:
        issues.append(
            {
                "code": "generic_ai_diction",
                "severity": "P2",
                "category": "英文抽象表达定位信号",
                "message": f"abstract diction signal: {', '.join(marker_hits[:5])}",
                "evidence": [{"pattern": marker, "count": lower.count(marker), "snippet": evidence_span(text, marker)} for marker in marker_hits[:5]],
                "suggestion": "replace generic AI diction with specific scene consequence or plain verbs",
            }
        )

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", text) if part.strip()]
    duplicate_blocks = exact_duplicate_paragraph_blocks(paragraphs)
    if duplicate_blocks:
        issues.append(
            {
                "code": "duplicate_paragraphs",
                "severity": "P1",
                "category": "重复段落",
                "message": "paragraph duplication remains high",
                "evidence": [
                    {"pattern": "exact_duplicate_paragraph", "count": count, "snippet": paragraph[:120]}
                    for paragraph, count in duplicate_blocks[:3]
                ],
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

    return issues, warnings


def exact_duplicate_paragraph_blocks(paragraphs: list[str]) -> list[tuple[str, int]]:
    """Return only large exact duplicate prose blocks that are directly provable."""

    counts: dict[str, int] = {}
    for paragraph in paragraphs:
        normalized = paragraph.strip()
        if len(re.sub(r"\s+", "", normalized)) < 80:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1
    return sorted(
        ((paragraph, count) for paragraph, count in counts.items() if count >= 2),
        key=lambda item: (-item[1], -len(item[0])),
    )


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
    hook_markers = (
        "?",
        "？",
        "but",
        "however",
        "suddenly",
        "忽然",
        "可是",
        "然而",
        "门外",
        "下一刻",
        "只剩",
        "来不及",
        "截止",
        "期限",
        "封库",
        "必须在",
        "赶在",
    )
    if tier == "fast" and len(re.findall(r"(fight|kill|explode|truth|reveal|决战|爆发|真相|揭露|杀)", lower)) >= 4:
        warnings.append("continuous high-intensity beats need a buffer or cost beat")
    if not any(marker in lower for marker in payoff_markers):
        warnings.append("reader payoff is weak or implicit")
    tail = text[-500:]
    if not any(marker in tail.lower() for marker in hook_markers):
        warnings.append("ending hook is weak; chapter ends without a concrete next pressure")
    if repeated_scene_fatigue(text):
        warnings.append("repeated scene shape may cause reader fatigue")
    if emotion_turn_without_evidence(text):
        warnings.append("emotion turn appears without visible evidence or action")

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
    elif code == "content_character_count":
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
        return manuscript_chapter_path(root, chapter_number, lane="draft")
    if source == "repair-candidate":
        candidates = sorted(
            (root / "50_workbench" / "repair_candidates").glob(
                f"ch{chapter_number:03d}*.repair_candidate.md"
            )
        )
        if candidates:
            return candidates[-1]
        agent_candidates = sorted((root / "50_workbench" / "agent_drafts").glob(f"ch{chapter_number:03d}*.repair_candidate.md"))
        if agent_candidates:
            return agent_candidates[-1]
        return root / "50_workbench" / "repair_candidates" / f"ch{chapter_number:03d}.repair_candidate.md"
    raise ValueError("source must be draft or repair-candidate.")


def humanizer_source_for_candidate(root: Path, chapter_number: int, candidate: Path) -> Path | None:
    candidate_rel = relative_path(root, candidate)
    for entry in reversed(list_manifests(root, chapter_number=chapter_number)):
        if entry.get("task_type") != "humanize" or candidate_rel != manifest_output(entry).get("path"):
            continue
        for item in manifest_input_paths(entry):
            path = root / str(item)
            normalized = str(item).replace("\\", "/")
            if (
                path.resolve() != candidate.resolve()
                and path.suffix.lower() in {".md", ".txt"}
                and (
                    normalized.startswith("40_manuscript/draft/")
                    or normalized.startswith("50_workbench/repair_candidates/")
                    or normalized.startswith("50_workbench/agent_drafts/")
                )
                and "task" not in path.stem
                and path.exists()
            ):
                return path
    draft = manuscript_chapter_path(root, chapter_number, lane="draft")
    return draft if draft.exists() else None


def humanizer_fact_drift(
    root: Path,
    source: str,
    candidate: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not source:
        return [], ["humanizer source could not be resolved; fact-preservation comparison was skipped"]
    issues: list[dict[str, Any]] = []
    warnings: list[str] = []
    source_numbers = set(re.findall(r"(?<!\d)\d+(?:\.\d+)?(?!\d)", source))
    candidate_numbers = set(re.findall(r"(?<!\d)\d+(?:\.\d+)?(?!\d)", candidate))
    if source_numbers != candidate_numbers:
        issues.append(
            {
                "code": "humanizer_number_drift",
                "severity": "P1",
                "category": "事实漂移",
                "message": (
                    f"numeric facts changed: removed={sorted(source_numbers - candidate_numbers)}, "
                    f"added={sorted(candidate_numbers - source_numbers)}"
                ),
                "evidence": [],
                "suggestion": "恢复来源稿中的数值事实；如剧情事实确需变化，应回到修章而非 Humanizer。",
            }
        )
    characters = load_json(root / "10_bible" / "characters.json", default=[])
    names = {
        str(item.get("name") or "").strip()
        for item in characters if isinstance(item, dict)
        if len(str(item.get("name") or "").strip()) >= 2
    }
    removed_names = sorted(name for name in names if name in source and name not in candidate)
    if removed_names:
        issues.append(
            {
                "code": "humanizer_character_drift",
                "severity": "P1",
                "category": "角色漂移",
                "message": f"source character references disappeared: {', '.join(removed_names[:8])}",
                "evidence": [],
                "suggestion": "保留场景中的角色参与和关系事实；不要用润色任务改变人物构成。",
            }
        )
    source_paragraphs = [part for part in re.split(r"\n\s*\n+", source) if part.strip()]
    candidate_paragraphs = [part for part in re.split(r"\n\s*\n+", candidate) if part.strip()]
    if len(candidate_paragraphs) < max(1, len(source_paragraphs) // 2):
        warnings.append("candidate removed more than half of the source paragraphs; verify scene outcome and evidence")
    return issues, warnings


def resolve_expansion_source(root: Path, chapter_number: int, source: str) -> Path:
    if source == "draft":
        return manuscript_chapter_path(root, chapter_number, lane="draft")
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


def expansion_minimum_content_characters(config: ConfigDocument) -> int:
    chapter = config.data.get("length", {}).get("chapter", {})
    if not isinstance(chapter, dict):
        return 0
    return int(chapter.get("hard_min") or chapter.get("soft_min") or 0)


def expansion_instructions(expansion_types: tuple[str, ...]) -> list[str]:
    catalog = {
        "scene": "Scene expansion: add concrete place, sensory anchor, object interaction, and changed spatial pressure.",
        "dialogue": "Dialogue reinforcement: add speaker intent, status friction, subtext, and non-interchangeable rhythm.",
        "psychology": "Psychology deepening: show fear, hesitation, desire, or realization through body/action before naming it.",
        "action": "Action detailing: break outcomes into visible moves, obstacles, costs, and reactions.",
        "transition": "Transition smoothing: connect scene turns with time, cause, decision, or consequence beats.",
    }
    return [catalog[item] for item in expansion_types]


def clip_text(text: str, max_chars: int) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 3)].rstrip() + "..."


def detect_expansion_issues(
    root: Path,
    chapter_number: int,
    target: Path,
    text: str,
    expansion_types: tuple[str, ...],
    minimum_content_characters: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    content_characters = content_character_count(text)
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
    if minimum_content_characters and content_characters < minimum_content_characters:
        issues.append(
            {
                "code": "expansion_content_character_count",
                "severity": "P1",
                "message": (
                    "expanded candidate is still below minimum content characters: "
                    f"{content_characters} < {minimum_content_characters}"
                ),
            }
        )
    source_path = manuscript_chapter_path(root, chapter_number, lane="draft")
    if source_path.exists() and content_characters <= content_character_count(safe_read_text(source_path)):
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
    raw = str(value or "").strip().lower()
    aliases = {
        "玄幻": "xuanhuan",
        "东方玄幻": "xuanhuan",
        "奇幻": "xuanhuan",
        "都市": "urban",
        "言情": "romance",
        "爱情": "romance",
        "悬疑": "suspense",
        "推理": "suspense",
    }
    key = aliases.get(raw) or re.sub(r"[^a-zA-Z0-9_\-]+", "_", raw).strip("_")
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


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
