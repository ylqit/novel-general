"""Creative operator protocol, humanizer, and style playbook helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.agent_tasks import (
    build_manifest,
    list_manifests,
    mark_tasks_for_chapter_type,
    mark_tasks_for_output,
    write_manifest,
)
from longform_engine.character_expression import character_expression_diagnostics
from longform_engine.config import ConfigDocument
from longform_engine.quality import (
    compact_effective_quality_contract,
    compile_effective_quality_contract,
)
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
    selected["market_contract"] = compact_effective_quality_contract(
        compile_effective_quality_contract(config, chapter_number=1)
    )
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
                "补感官细节：每个主要场景至少有声音、触感、气味、光线或身体代价之一。",
                "调整句长节奏：紧张处短句切开，解释处合并，避免等长句排队。",
                "增强对白差异：每个说话人带不同目的、遮掩、身份压力或关系变化。",
                "把章末落点改成具体发现、决定、威胁或误解，而不是抽象展望。",
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
                f"# Humanizer v3 Task ch{chapter_number:03d}",
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
        context_policy={
            "required_files": [task_file, source_path],
            "optional_files": [
                root / "10_bible" / "style_bible.md",
                root / "10_bible" / "creative_brief.json",
                root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "humanize_report.md",
            ],
            "compiled_brief": task_file,
            "selection_report": task_file,
            "max_files": 5,
            "max_chars": 14_000,
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
    issues, warnings = detect_humanizer_v2_issues(text)
    source = humanizer_source_for_candidate(root, chapter_number, target)
    source_text = safe_read_text(source) if source is not None and source.exists() else ""
    change_ratio = humanizer_change_ratio(source_text, text) if source_text else None
    humanizer_config = config.data.get("quality", {}).get("humanizer", {})
    warning_ratio = float(humanizer_config.get("changed_character_warning_ratio") or 0.35)
    human_ratio = float(humanizer_config.get("changed_character_human_ratio") or 0.60)
    if change_ratio is not None and change_ratio >= human_ratio:
        issues.append(
            {
                "code": "humanizer_excessive_rewrite",
                "severity": "P1",
                "category": "过度改写",
                "message": f"candidate changed-character ratio requires human review: {change_ratio:.3f} >= {human_ratio:.3f}",
                "evidence": [],
                "suggestion": "缩小改写范围，保留事实、场景结果和人物声音；或交由人工确认。",
            }
        )
    elif change_ratio is not None and change_ratio >= warning_ratio:
        warnings.append(
            f"changed-character ratio is high: {change_ratio:.3f} >= warning threshold {warning_ratio:.3f}"
        )
    fact_issues, fact_warnings = humanizer_fact_drift(root, source_text, text)
    issues.extend(fact_issues)
    warnings.extend(fact_warnings)
    report_dir = root / "50_workbench" / "humanizer_tasks"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_file = report_dir / f"ch{chapter_number:03d}.humanize_check.json"
    md_file = report_dir / f"ch{chapter_number:03d}.humanize_check.md"
    need_human = any(item.get("code") in {"humanizer_excessive_rewrite", "humanizer_number_drift", "humanizer_character_drift"} for item in issues)
    passed = not any(item.get("severity") in {"P0", "P1"} for item in issues)
    semantic_reasons = (
        humanize_semantic_review_reasons(
            config,
            chapter_number=chapter_number,
            change_ratio=change_ratio,
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
                f"--task-id humanize_semantic_review:ch{chapter_number:03d}:v1"
            )
    elif passed:
        next_command = submit_command
    else:
        mark_tasks_for_chapter_type(
            root,
            chapter_number=chapter_number,
            task_types=("humanize_semantic_review",),
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
        "changed_character_ratio": round(change_ratio, 4) if change_ratio is not None else None,
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
                f"# Humanizer v3 Check ch{chapter_number:03d}",
                "",
                f"- File: `{relative_path(root, target)}`",
                f"- Passed: {passed}",
                f"- Need human: {need_human}",
                f"- Changed-character ratio: {change_ratio if change_ratio is not None else 'not available'}",
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
    change_ratio: float | None,
) -> tuple[str, ...]:
    """Return deterministic reasons that require an independent Humanizer semantic review."""

    quality = config.data.get("quality", {}) if isinstance(config.data.get("quality"), dict) else {}
    humanizer = quality.get("humanizer", {}) if isinstance(quality.get("humanizer"), dict) else {}
    assurance_mode = str(quality.get("assurance_mode") or "balanced")
    review_mode = str(humanizer.get("semantic_review_mode") or "risk_based")
    reasons: list[str] = []
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
    threshold = float(humanizer.get("semantic_review_change_ratio") or 0.15)
    if assurance_mode == "light":
        threshold = max(threshold, 0.20)
    if change_ratio is not None and change_ratio >= threshold:
        reasons.append(f"change_ratio:{change_ratio:.4f}>={threshold:.4f}")
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
    length = config.data.get("length", {}) if isinstance(config.data.get("length"), dict) else {}
    total_chapters = int(length.get("total_chapters") or 0)
    volume_count = int(length.get("volume_count") or 0)
    if chapter_number <= 0 or total_chapters <= 0 or volume_count <= 0:
        return False
    boundaries = {1, total_chapters}
    for volume in range(1, volume_count):
        boundary = round(total_chapters * volume / volume_count)
        boundaries.update({boundary, boundary + 1})
    return chapter_number in boundaries


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
        change_ratio=humanizer_change_ratio(source_text, candidate_text),
    ))
    task_dir = root / "50_workbench" / "humanizer_tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_file = task_dir / f"ch{chapter_number:03d}.semantic_review.md"
    manifest_file = task_dir / f"ch{chapter_number:03d}.semantic_review.agent_task.json"
    output_file = task_dir / f"ch{chapter_number:03d}.semantic_review.json"
    context_candidates = [
        root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json",
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
    canonical_refs = [
        relative_path(root, path)
        for path in selected_context
        if relative_path(root, path).startswith(("10_bible/", "20_outline/", "30_state/", RAG_LANE + "/memory/"))
    ]
    schema = humanizer_semantic_output_template(
        chapter_number=chapter_number,
        source=source,
        candidate=candidate,
        root=root,
        canonical_refs=canonical_refs,
    )
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
                "You are the independent Humanizer semantic-preservation reviewer, not the rewriting Agent.",
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
                *[f"- `{dimension}`" for dimension in HUMANIZER_FACT_DIMENSIONS],
                "- Preserve chapter duty, reader gain, cost, forbidden reveals, and each declared character voice.",
                "- Report P0/P1 AI-taste or semantic findings even when the prose sounds smoother.",
                "",
                "## Output Contract",
                "",
                f"- Write JSON only: `{relative_path(root, output_file)}`",
                f"- Validate: `{validate_command}`",
                f"- Apply after a pass: `{apply_command}`",
                f"- Failure: `{failure_command}`",
                "- Do not edit draft, final, RAG, graph, TCS, Bible, outline, or SQLite.",
                "",
                "```json",
                json.dumps(schema, ensure_ascii=False, indent=2),
                "```",
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
        task_type="humanize_semantic_review",
        chapter_number=chapter_number,
        input_files=inputs,
        allowed_output_paths=[output_file],
        output_schema="humanizer_semantic_review_v1",
        validate_command=validate_command,
        apply_command=apply_command,
        failure_next_command=failure_command,
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
            "max_files": 6,
            "max_chars": 28_000,
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


def humanizer_semantic_output_template(
    *,
    chapter_number: int,
    source: Path,
    candidate: Path,
    root: Path,
    canonical_refs: list[str],
) -> dict[str, Any]:
    source_text = safe_read_text(source)
    candidate_text = safe_read_text(candidate)
    source_end = min(len(source_text), 12)
    candidate_end = min(len(candidate_text), 12)
    return {
        "schema": "humanizer_semantic_review_v1",
        "chapter_number": chapter_number,
        "source": {
            "path": relative_path(root, source),
            "sha256": sha256_text(source_text),
        },
        "candidate": {
            "path": relative_path(root, candidate),
            "sha256": sha256_text(candidate_text),
        },
        "verdict": "pass",
        "fact_preservation": [
            {
                "dimension": dimension,
                "status": "preserved",
                "source_span": {"start": 0, "end": source_end, "text": source_text[:source_end]},
                "candidate_span": {"start": 0, "end": candidate_end, "text": candidate_text[:candidate_end]},
                "canonical_refs": canonical_refs[:1],
                "entity_ids": [],
                "message": f"Explain how {dimension} is preserved.",
            }
            for dimension in HUMANIZER_FACT_DIMENSIONS
        ],
        "chapter_contract": {
            "duty_preserved": True,
            "reader_gain_preserved": True,
            "cost_preserved": True,
            "forbidden_reveals_preserved": True,
        },
        "voice_checks": [],
        "ai_taste_findings": [],
        "confidence": 0.9,
        "notes": "",
    }


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
    if not isinstance(payload, dict):
        payload = {}
        errors.append("semantic review result must be a JSON object.")
    expected_keys = {
        "schema",
        "chapter_number",
        "source",
        "candidate",
        "verdict",
        "fact_preservation",
        "chapter_contract",
        "voice_checks",
        "ai_taste_findings",
        "confidence",
        "notes",
    }
    if set(payload) != expected_keys:
        errors.append(f"top-level keys must be exactly {sorted(expected_keys)}.")
    if payload.get("schema") != "humanizer_semantic_review_v1":
        errors.append("schema must be humanizer_semantic_review_v1.")
    if int(payload.get("chapter_number") or 0) != chapter_number:
        errors.append("payload chapter_number does not match command chapter.")
    candidate = root / "50_workbench" / "repair_candidates" / f"ch{chapter_number:03d}.humanized_candidate.md"
    source = humanizer_source_for_candidate(root, chapter_number, candidate)
    source_text = safe_read_text(source) if source is not None and source.exists() else ""
    candidate_text = safe_read_text(candidate) if candidate.exists() else ""
    if source is None or not source.exists():
        errors.append("Humanizer source could not be resolved from the active task.")
    if not candidate.exists():
        errors.append("Humanizer candidate is missing.")
    validate_humanizer_file_identity(
        payload.get("source"),
        label="source",
        expected_path=source,
        expected_text=source_text,
        root=root,
        errors=errors,
    )
    validate_humanizer_file_identity(
        payload.get("candidate"),
        label="candidate",
        expected_path=candidate,
        expected_text=candidate_text,
        root=root,
        errors=errors,
    )
    manifest = load_json(task_dir / f"ch{chapter_number:03d}.semantic_review.agent_task.json", default={})
    if not isinstance(manifest, dict) or manifest.get("task_type") != "humanize_semantic_review":
        errors.append("Humanizer semantic Agent task manifest is missing or invalid.")
        manifest = {}
    allowed_refs = {
        str(item).replace("\\", "/")
        for item in manifest.get("input_files", [])
        if str(item).replace("\\", "/").startswith(("10_bible/", "20_outline/", "30_state/", RAG_LANE + "/memory/"))
    }
    known_entities = humanizer_semantic_known_entities(root)
    fact_items = payload.get("fact_preservation")
    if not isinstance(fact_items, list):
        errors.append("fact_preservation must be a list.")
        fact_items = []
    dimensions: list[str] = []
    for index, item in enumerate(fact_items):
        dimension, status = validate_humanizer_fact_item(
            item,
            index=index,
            source_text=source_text,
            candidate_text=candidate_text,
            allowed_refs=allowed_refs,
            known_entities=known_entities,
            errors=errors,
        )
        if dimension:
            dimensions.append(dimension)
        if status == "changed":
            blockers.append(f"fact_changed:{dimension or index}")
        elif status == "uncertain":
            blockers.append(f"fact_uncertain:{dimension or index}")
            need_human = True
    if sorted(dimensions) != sorted(HUMANIZER_FACT_DIMENSIONS):
        errors.append(
            "fact_preservation must contain each required dimension exactly once: "
            + ", ".join(HUMANIZER_FACT_DIMENSIONS)
            + "."
        )
    contract = payload.get("chapter_contract")
    expected_contract = {
        "duty_preserved",
        "reader_gain_preserved",
        "cost_preserved",
        "forbidden_reveals_preserved",
    }
    if not isinstance(contract, dict) or set(contract) != expected_contract:
        errors.append(f"chapter_contract keys must be exactly {sorted(expected_contract)}.")
    else:
        for key in sorted(expected_contract):
            if not isinstance(contract.get(key), bool):
                errors.append(f"chapter_contract.{key} must be boolean.")
            elif contract[key] is False:
                blockers.append(f"chapter_contract_changed:{key}")
    voice_checks = payload.get("voice_checks")
    if not isinstance(voice_checks, list):
        errors.append("voice_checks must be a list.")
        voice_checks = []
    for index, item in enumerate(voice_checks):
        status = validate_humanizer_voice_check(
            item,
            index=index,
            candidate_text=candidate_text,
            known_entities=known_entities,
            errors=errors,
        )
        if status == "changed":
            blockers.append(f"voice_changed:{index}")
        elif status == "uncertain":
            blockers.append(f"voice_uncertain:{index}")
            need_human = True
    findings = payload.get("ai_taste_findings")
    if not isinstance(findings, list):
        errors.append("ai_taste_findings must be a list.")
        findings = []
    for index, finding in enumerate(findings):
        severity = validate_humanizer_ai_finding(
            finding,
            index=index,
            candidate_text=candidate_text,
            errors=errors,
        )
        if severity in {"P0", "P1"}:
            blockers.append(f"ai_taste:{severity}:{index}")
        if severity == "P0":
            need_human = True
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= float(confidence) <= 1:
        errors.append("confidence must be a number between 0 and 1.")
    verdict = str(payload.get("verdict") or "").lower()
    if verdict not in {"pass", "repair", "need_human"}:
        errors.append("verdict must be pass, repair, or need_human.")
    if verdict == "pass" and blockers:
        errors.append("verdict=pass cannot override changed/uncertain facts, failed chapter contract, voice drift, or P0/P1 findings.")
    if verdict == "need_human":
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
    report = {
        "schema": "humanizer_semantic_validation_v1",
        "chapter_number": chapter_number,
        "file": relative_path(root, target),
        "source_path": relative_path(root, source) if source is not None else "",
        "source_sha256": sha256_text(source_text) if source_text else "",
        "candidate_path": relative_path(root, candidate),
        "candidate_sha256": sha256_text(candidate_text) if candidate_text else "",
        "ok": ok,
        "passed": passed,
        "need_human": need_human,
        "errors": errors,
        "blocking_findings": blockers,
        "warnings": warnings,
        "next_command": next_command,
        "validated_at": utc_now(),
    }
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
        change_ratio=humanizer_change_ratio(source_text, candidate_text),
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
    current = (
        isinstance(report, dict)
        and report.get("schema") == "humanizer_semantic_validation_v1"
        and report.get("ok") is True
        and report.get("passed") is True
        and str(report.get("source_path") or "") == relative_path(root, source)
        and str(report.get("source_sha256") or "") == sha256_text(source_text)
        and str(report.get("candidate_path") or "") == relative_path(root, candidate)
        and str(report.get("candidate_sha256") or "") == sha256_text(candidate_text)
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
    """Backward-compatible entry point for the Chinese web-novel Humanizer v3 detector."""

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
        if entry.get("task_type") != "humanize" or candidate_rel not in entry.get("allowed_output_paths", []):
            continue
        manifest_file = root / str(entry.get("manifest_file") or "")
        manifest = load_json(manifest_file, default={})
        for item in manifest.get("input_files") or [] if isinstance(manifest, dict) else []:
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
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    return draft if draft.exists() else None


def humanizer_change_ratio(source: str, candidate: str) -> float:
    source_compact = re.sub(r"\s+", "", source)
    candidate_compact = re.sub(r"\s+", "", candidate)
    if not source_compact and not candidate_compact:
        return 0.0
    return 1.0 - SequenceMatcher(None, source_compact, candidate_compact, autojunk=False).ratio()


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


def estimate_words(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
