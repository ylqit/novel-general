"""Outline anchors, event cooling, and revise-outline workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import re
import shutil

from longform_engine.config import ConfigDocument
from longform_engine.graph import cascade_graph
from longform_engine.lengths import compile_length_forecast
from longform_engine.memory import mark_memory_stale
from longform_engine.storage import atomic_write_text, resolve_project_root


EVENT_TYPE_POOL = (
    "conflict_thrill",
    "bond_deepening",
    "faction_building",
    "world_painting",
    "tension_escalation",
)
SOFT_EVENT_TYPES = ("bond_deepening", "faction_building", "world_painting")
FAST_EVENT_TYPES = ("conflict_thrill", "tension_escalation")
EVENT_TYPE_ALIASES = {
    "fight": "conflict_thrill",
    "battle": "conflict_thrill",
    "conflict": "conflict_thrill",
    "reveal": "tension_escalation",
    "truth": "tension_escalation",
    "tail_hook": "tension_escalation",
    "relationship_turn": "bond_deepening",
    "bond": "bond_deepening",
    "training": "world_painting",
    "travel": "world_painting",
    "setup": "world_painting",
    "choice": "tension_escalation",
}


@dataclass(frozen=True)
class OutlineAnchorResult:
    """Result for recalculating outline anchors."""

    anchor_file: str
    backup_file: str
    report_file: str
    anchors: int


@dataclass(frozen=True)
class EventRecommendationResult:
    """Event types recommended before writing a chapter."""

    chapter_number: int
    recommended: tuple[str, ...]
    blocked: tuple[str, ...]
    constraints: tuple[str, ...]
    soft_event_required: bool
    recent_summary: tuple[dict[str, Any], ...]
    fast_quota: dict[str, Any]
    event_types: tuple[str, ...]
    source_file: str


@dataclass(frozen=True)
class EventUsageResult:
    """Event cooling usage recorded after finalization or gate review."""

    chapter_number: int
    event_types: tuple[str, ...]
    matrix_file: str
    pacing_file: str


@dataclass(frozen=True)
class EventMatrixEvaluationResult:
    """Event matrix checks for a draft chapter."""

    chapter_number: int
    event_types: tuple[str, ...]
    tier: str
    failures: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    constraints: tuple[str, ...]
    soft_event_required: bool


@dataclass(frozen=True)
class ReviseOutlineResult:
    """Artifacts produced by revise-outline."""

    from_chapter: int
    anchor_file: str
    anchor_backup: str
    cascade_report: str
    rag_stale_file: str
    stale_index_file: str
    report_file: str
    next_command: str


def recalculate_outline_anchors(
    config: ConfigDocument,
    *,
    from_chapter: int = 1,
    change_description: str = "",
) -> OutlineAnchorResult:
    """Recalculate outline anchors and keep a timestamped backup."""

    if from_chapter <= 0:
        raise ValueError("from_chapter must be positive.")
    root = resolve_project_root(config)
    anchor_path = root / "20_outline" / "outline_anchors.json"
    report_dir = root / "20_outline" / "revise_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = utc_stamp()
    backup_path = report_dir / f"outline_anchors_backup_{timestamp}.json"
    if anchor_path.exists():
        shutil.copyfile(anchor_path, backup_path)
    else:
        atomic_write_text(backup_path, "[]\n")

    anchors = build_outline_anchors(config, root, from_chapter=from_chapter, change_description=change_description)
    atomic_write_text(anchor_path, json.dumps(anchors, ensure_ascii=False, indent=2) + "\n")
    report_path = report_dir / f"anchor_recalc_ch{from_chapter:03d}_{timestamp}.md"
    atomic_write_text(
        report_path,
        "\n".join(
            [
                f"# Outline Anchor Recalculation ch{from_chapter:03d}",
                "",
                f"- Backup: `{relative_path(root, backup_path)}`",
                f"- Anchor file: `{relative_path(root, anchor_path)}`",
                f"- Change: {change_description or 'not provided'}",
                f"- Anchors: {len(anchors)}",
                "",
            ]
        ),
    )
    return OutlineAnchorResult(
        anchor_file=str(anchor_path),
        backup_file=str(backup_path),
        report_file=str(report_path),
        anchors=len(anchors),
    )


def recommend_event_types(config: ConfigDocument, *, chapter_number: int) -> EventRecommendationResult:
    """Recommend formal event types with cooldown, soft-event, and fast-quota rules."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    matrix_path = root / "30_state" / "event_matrix.json"
    matrix = load_event_matrix(config, root)
    history = load_pacing_history(root)
    cooldown = event_cooldown_config(config)
    last_used = matrix.get("last_used") if isinstance(matrix.get("last_used"), dict) else {}
    if not last_used:
        last_used = event_last_used(history)
    all_types = event_type_pool(config)
    blocked: list[str] = []
    recommended: list[str] = []
    for event_type in all_types:
        last = as_int(last_used.get(event_type))
        if last and chapter_number - last <= cooldown.get(event_type, 0):
            blocked.append(event_type)
        else:
            recommended.append(event_type)
    soft_required = soft_event_required_for_history(config, history, chapter_number)
    fast_quota = fast_quota_status(config, history, chapter_number)
    constraints: list[str] = []
    if soft_required:
        constraints.append(
            f"soft event required: include one of {', '.join(SOFT_EVENT_TYPES)} because the previous window lacks soft events"
        )
        soft_available = [item for item in SOFT_EVENT_TYPES if item in recommended]
        recommended = soft_available + [item for item in recommended if item not in soft_available]
    if fast_quota.get("blocked"):
        constraints.append("fast event quota is exhausted for the current volume; avoid conflict_thrill/tension_escalation")
        recommended = [item for item in recommended if item not in FAST_EVENT_TYPES]
        for event_type in FAST_EVENT_TYPES:
            if event_type not in blocked:
                blocked.append(event_type)
    if not recommended:
        recommended = [item for item in SOFT_EVENT_TYPES if item not in blocked] or [all_types[0]]

    recent_summary = recent_event_summary(history, chapter_number, limit=5)
    matrix["schema_version"] = 2
    matrix["event_types"] = list(all_types)
    matrix["soft_event_types"] = list(SOFT_EVENT_TYPES)
    matrix["fast_event_types"] = list(FAST_EVENT_TYPES)
    matrix["cooldown"] = cooldown
    matrix["last_used"] = last_used
    matrix["recent_5"] = list(recent_summary)
    matrix["soft_event_window"] = soft_event_window(config)
    matrix["latest_recommendation"] = {
        "chapter_number": chapter_number,
        "recommended": recommended[:4],
        "blocked": blocked,
        "constraints": constraints,
        "soft_event_required": soft_required,
        "fast_quota": fast_quota,
        "updated_at": utc_now(),
    }
    write_json(matrix_path, matrix)
    return EventRecommendationResult(
        chapter_number=chapter_number,
        recommended=tuple(recommended[:4]),
        blocked=tuple(blocked),
        constraints=tuple(constraints),
        soft_event_required=soft_required,
        recent_summary=recent_summary,
        fast_quota=fast_quota,
        event_types=all_types,
        source_file=str(matrix_path),
    )


def record_event_usage(
    config: ConfigDocument,
    *,
    chapter_number: int,
    event_types: list[str] | tuple[str, ...],
    tier: str = "medium",
) -> EventUsageResult:
    """Record used event types for later cooling and pacing review."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    matrix_path = root / "30_state" / "event_matrix.json"
    pacing_path = root / "30_state" / "pacing_history.json"
    event_types = normalize_event_types(event_types)
    matrix = load_event_matrix(config, root)
    last_used = matrix.setdefault("last_used", {})
    for event_type in event_types:
        last_used[event_type] = chapter_number
    history = load_json(pacing_path, default=[])
    if not isinstance(history, list):
        history = []
    history = [item for item in history if not (isinstance(item, dict) and as_int(item.get("chapter_number") or item.get("chapter")) == chapter_number)]
    tier = event_tier_for_types(event_types, tier)
    history.append(
        {
            "id": f"pacing:{chapter_number}",
            "chapter_number": chapter_number,
            "tier": tier,
            "event_types": list(event_types),
            "quota_used": {
                "fast": tier == "fast",
                "fast_event_types": [item for item in event_types if item in FAST_EVENT_TYPES],
            },
            "updated_at": utc_now(),
        }
    )
    history.sort(key=lambda item: as_int(item.get("chapter_number") if isinstance(item, dict) else 0))
    matrix["schema_version"] = 2
    matrix["event_types"] = list(event_type_pool(config))
    matrix["soft_event_types"] = list(SOFT_EVENT_TYPES)
    matrix["fast_event_types"] = list(FAST_EVENT_TYPES)
    matrix["cooldown"] = event_cooldown_config(config)
    matrix["last_used"] = last_used
    matrix["usage"] = [
        {
            "chapter_number": item.get("chapter_number"),
            "tier": item.get("tier"),
            "event_types": item.get("event_types", []),
        }
        for item in history
        if isinstance(item, dict)
    ]
    matrix["recent_5"] = list(recent_event_summary(history, chapter_number + 1, limit=5))
    matrix["volume_fast_usage"] = volume_fast_usage(config, history)
    matrix["updated_at"] = utc_now()
    atomic_write_text(matrix_path, json.dumps(matrix, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(pacing_path, json.dumps(history, ensure_ascii=False, indent=2) + "\n")
    return EventUsageResult(
        chapter_number=chapter_number,
        event_types=event_types,
        matrix_file=str(matrix_path),
        pacing_file=str(pacing_path),
    )


def revise_outline(config: ConfigDocument, *, from_chapter: int, change_description: str) -> ReviseOutlineResult:
    """Recalculate anchors, mark dependent graph/RAG artifacts stale, and block unsafe continuation."""

    if not change_description.strip():
        raise ValueError("change_description is required.")
    root = resolve_project_root(config)
    anchor = recalculate_outline_anchors(config, from_chapter=from_chapter, change_description=change_description)
    cascade = cascade_graph(config, from_chapter=from_chapter, change_description=change_description)
    memory_stale_path = mark_memory_stale(
        config,
        from_chapter=from_chapter,
        reason="outline_revised",
        change_description=change_description,
    )

    rag_stale = {
        "from_chapter": from_chapter,
        "reason": "outline_revised",
        "change_description": change_description,
        "stale": True,
        "requires": ["rag build", "rag context", "db rebuild"],
        "updated_at": utc_now(),
    }
    rag_stale_path = root / "60_rag" / "stale.json"
    stale_index_path = root / "30_state" / "stale_indexes.json"
    atomic_write_text(rag_stale_path, json.dumps(rag_stale, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(
        stale_index_path,
        json.dumps(
            {
                **rag_stale,
                "stale": [
                    "rag_chunks",
                    "scene_memory",
                    "chapter_memory",
                    "arc_memory",
                    "temporal_context_state",
                    "semantic_embeddings",
                ],
                "unsafe_continuation_blocker": True,
                "next_command": "db rebuild project.yaml",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )

    report_dir = root / "20_outline" / "revise_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"revise_outline_ch{from_chapter:03d}_{utc_stamp()}.md"
    atomic_write_text(
        report_path,
        "\n".join(
            [
                f"# Revise Outline ch{from_chapter:03d}",
                "",
                f"- Change: {change_description}",
                f"- Anchor file: `{relative_path(root, Path(anchor.anchor_file))}`",
                f"- Anchor backup: `{relative_path(root, Path(anchor.backup_file))}`",
                f"- Cascade report: `{relative_path(root, Path(cascade.report_file))}`",
                f"- RAG stale: `{relative_path(root, rag_stale_path)}`",
                f"- Memory stale: `{relative_path(root, memory_stale_path)}`",
                f"- Stale indexes: `{relative_path(root, stale_index_path)}`",
                "",
                "## Blocker",
                "",
                "- Unsafe continuation is blocked until stale artifacts are rebuilt or explicitly handled.",
                "",
            ]
        ),
    )
    return ReviseOutlineResult(
        from_chapter=from_chapter,
        anchor_file=anchor.anchor_file,
        anchor_backup=anchor.backup_file,
        cascade_report=cascade.report_file,
        rag_stale_file=str(rag_stale_path),
        stale_index_file=str(stale_index_path),
        report_file=str(report_path),
        next_command="db rebuild project.yaml",
    )


def build_outline_anchors(
    config: ConfigDocument,
    root: Path,
    *,
    from_chapter: int,
    change_description: str,
) -> list[dict[str, Any]]:
    chapter_plan = load_json(root / "20_outline" / "chapter_plan.json", default=[])
    records = normalize_records(chapter_plan)
    if not records:
        forecast = compile_length_forecast(config.data["length"])
        total = max(forecast.estimated_chapters, from_chapter)
        step = max(1, total // forecast.estimated_volumes)
        records = [
            {
                "chapter_number": chapter,
                "title": f"Anchor ch{chapter:03d}",
                "duty": "major arc checkpoint",
            }
            for chapter in range(1, total + 1, step)
        ]
    anchors: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        chapter = as_int(item.get("chapter_number") or item.get("chapter"))
        if not chapter:
            continue
        anchors.append(
            {
                "chapter_number": chapter,
                "title": item.get("title") or f"Anchor ch{chapter:03d}",
                "duty": item.get("duty") or item.get("goal") or "maintain longform promise",
                "status": "stale_pending_review" if chapter >= from_chapter else "locked",
                "forbidden_reveals": list(event_values(item.get("forbidden_reveals"))),
                "resolution_markers": list(event_values(item.get("resolution_markers"))) or ["core longform mystery", "main volume conflict"],
                "requires_tail_suspense": bool(item.get("requires_tail_suspense")),
                "allowed_reveal_level": str(item.get("allowed_reveal_level") or "hint"),
                "must_preserve_suspense": list(event_values(item.get("must_preserve_suspense"))) or ["core longform mystery", "main volume conflict"],
                "change_description": change_description if chapter >= from_chapter else "",
                "updated_at": utc_now(),
            }
        )
    anchors.sort(key=lambda item: item["chapter_number"])
    return anchors


def evaluate_event_matrix(
    config: ConfigDocument,
    *,
    chapter_number: int,
    event_types: list[str] | tuple[str, ...],
    tier: str,
) -> EventMatrixEvaluationResult:
    """Evaluate a draft chapter against the event matrix."""

    root = resolve_project_root(config)
    history = [item for item in load_pacing_history(root) if as_int(item.get("chapter_number") or item.get("chapter")) < chapter_number]
    matrix = load_event_matrix(config, root)
    cooldown = event_cooldown_config(config)
    normalized = normalize_event_types(event_types)
    tier = event_tier_for_types(normalized, tier)
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    constraints: list[str] = []
    last_used = matrix.get("last_used") if isinstance(matrix.get("last_used"), dict) else {}
    if not last_used:
        last_used = event_last_used(history)

    for event_type in normalized:
        last = as_int(last_used.get(event_type))
        if last and chapter_number - last <= cooldown.get(event_type, 0):
            message = f"event cooldown violated for {event_type}: last used ch{last:03d}"
            if event_type in FAST_EVENT_TYPES:
                failures.append({"code": "event_cooldown", "severity": "P1", "message": message})
            else:
                warnings.append(message)

    soft_required = soft_event_required_for_history(config, history, chapter_number)
    if soft_required:
        constraints.append(f"soft event required: include one of {', '.join(SOFT_EVENT_TYPES)}")
        if not any(event_type in SOFT_EVENT_TYPES for event_type in normalized):
            warnings.append("soft event gap persists; next plan should include bond_deepening/faction_building/world_painting")

    consecutive_limit = max_consecutive_fast(config)
    previous_fast = consecutive_fast_before(history, chapter_number)
    if tier == "fast" and previous_fast + 1 > consecutive_limit:
        failures.append(
            {
                "code": "fast_streak",
                "severity": "P1",
                "message": f"consecutive fast chapters exceed limit: {previous_fast + 1} > {consecutive_limit}",
            }
        )

    quota = fast_quota_status(config, history, chapter_number)
    if tier == "fast" and quota.get("blocked"):
        failures.append(
            {
                "code": "fast_quota",
                "severity": "P1",
                "message": f"volume fast quota exceeded: {quota.get('used')} >= {quota.get('limit')}",
            }
        )

    return EventMatrixEvaluationResult(
        chapter_number=chapter_number,
        event_types=normalized,
        tier=tier,
        failures=tuple(failures),
        warnings=tuple(warnings),
        constraints=tuple(constraints),
        soft_event_required=soft_required,
    )


EVENT_TYPE_MARKERS = {
        "conflict_thrill": (
            "fight",
            "battle",
            "kill",
            "strike",
            "ambush",
            "duel",
            "战斗",
            "交手",
            "搏杀",
            "厮杀",
            "决战",
            "伏击",
            "追杀",
            "拔刀",
            "刀锋",
            "爆发冲突",
            "正面冲突",
        ),
        "tension_escalation": (
            "truth",
            "secret",
            "reveal",
            "threat",
            "trap",
            "危机",
            "查明真相",
            "逼近真相",
            "真相浮出",
            "发现秘密",
            "隐藏秘密",
            "秘密揭露",
            "陷阱",
            "威胁",
            "逼近",
        ),
        "bond_deepening": (
            "trust",
            "save",
            "promise",
            "forgive",
            "ally",
            "建立信任",
            "获得信任",
            "互相信任",
            "舍命相救",
            "救下",
            "救出",
            "兑现承诺",
            "成为同伴",
            "并肩作战",
        ),
        "faction_building": (
            "faction",
            "clan",
            "guild",
            "sect",
            "council",
            "势力",
            "宗门",
            "家族",
            "结盟",
            "同盟",
            "城主",
            "议会",
        ),
        "world_painting": (
            "market",
            "road",
            "city",
            "weather",
            "rule",
            "custom",
            "市井",
            "街巷",
            "街市",
            "城池",
            "城镇",
            "风俗",
            "地貌",
            "法则",
            "天气",
            "暴雨",
            "大雪",
            "赶路",
        ),
}


def event_type_marker_count(text: str, event_type: str) -> int:
    """Count lexical evidence without treating one incidental mention as an event."""

    lower = text.lower()
    count = 0
    for word in EVENT_TYPE_MARKERS.get(event_type, ()):
        if word.isascii():
            count += len(re.findall(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", lower))
        else:
            count += lower.count(word)
    return count


def infer_event_types_from_text(text: str) -> tuple[str, ...]:
    lower = text.lower()
    detected = [
        event_type
        for event_type, words in EVENT_TYPE_MARKERS.items()
        if any(
            re.search(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", lower)
            if word.isascii()
            else word in lower
            for word in words
        )
    ]
    return tuple(detected)


def load_event_matrix(config: ConfigDocument, root: Path) -> dict[str, Any]:
    path = root / "30_state" / "event_matrix.json"
    payload = load_json(path, default={})
    matrix = payload if isinstance(payload, dict) else {}
    matrix.setdefault("schema_version", 2)
    matrix.setdefault("event_types", list(event_type_pool(config)))
    matrix.setdefault("soft_event_types", list(SOFT_EVENT_TYPES))
    matrix.setdefault("fast_event_types", list(FAST_EVENT_TYPES))
    matrix.setdefault("cooldown", event_cooldown_config(config))
    matrix.setdefault("last_used", {})
    return matrix


def load_pacing_history(root: Path) -> list[dict[str, Any]]:
    payload = load_json(root / "30_state" / "pacing_history.json", default=[])
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def event_type_pool(config: ConfigDocument) -> tuple[str, ...]:
    configured = config.data.get("pacing", {}).get("event_types")
    if isinstance(configured, list):
        normalized = normalize_event_types(tuple(str(item) for item in configured))
        return normalized or EVENT_TYPE_POOL
    return EVENT_TYPE_POOL


def normalize_event_types(event_types: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in event_types:
        event_type = EVENT_TYPE_ALIASES.get(str(item).strip(), str(item).strip())
        if event_type in EVENT_TYPE_POOL and event_type not in normalized:
            normalized.append(event_type)
    return tuple(normalized)


def event_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),) if str(value).strip() else ()


def event_tier_for_types(event_types: tuple[str, ...], fallback: str = "medium") -> str:
    if any(event_type in FAST_EVENT_TYPES for event_type in event_types):
        return "fast"
    if any(event_type in SOFT_EVENT_TYPES for event_type in event_types):
        return "medium"
    return fallback or "medium"


def soft_event_window(config: ConfigDocument) -> int:
    return max(1, as_int(config.data.get("pacing", {}).get("soft_event_window_chapters")) or 5)


def soft_event_required_for_history(config: ConfigDocument, history: list[dict[str, Any]], chapter_number: int) -> bool:
    window = soft_event_window(config)
    recent = [
        item for item in history
        if chapter_number - window <= as_int(item.get("chapter_number") or item.get("chapter")) < chapter_number
    ]
    if len(recent) < window:
        return False
    return not any(any(event_type in SOFT_EVENT_TYPES for event_type in normalize_event_types(event_values(item.get("event_types")))) for item in recent)


def recent_event_summary(history: list[dict[str, Any]], chapter_number: int, *, limit: int) -> tuple[dict[str, Any], ...]:
    recent = [
        item for item in history
        if as_int(item.get("chapter_number") or item.get("chapter")) < chapter_number
    ]
    recent = sorted(recent, key=lambda item: as_int(item.get("chapter_number") or item.get("chapter")))[-limit:]
    return tuple(
        {
            "chapter_number": as_int(item.get("chapter_number") or item.get("chapter")),
            "tier": item.get("tier", ""),
            "event_types": list(normalize_event_types(event_values(item.get("event_types")))),
        }
        for item in recent
    )


def event_last_used(history: list[dict[str, Any]]) -> dict[str, int]:
    last: dict[str, int] = {}
    for item in history:
        chapter = as_int(item.get("chapter_number") or item.get("chapter"))
        for event_type in normalize_event_types(event_values(item.get("event_types"))):
            last[event_type] = max(last.get(event_type, 0), chapter)
    return last


def max_consecutive_fast(config: ConfigDocument) -> int:
    return max(1, as_int(config.data.get("pacing", {}).get("max_consecutive_fast_chapters")) or 2)


def consecutive_fast_before(history: list[dict[str, Any]], chapter_number: int) -> int:
    count = 0
    for item in sorted(history, key=lambda value: as_int(value.get("chapter_number") or value.get("chapter")), reverse=True):
        chapter = as_int(item.get("chapter_number") or item.get("chapter"))
        if chapter >= chapter_number:
            continue
        if str(item.get("tier") or "") == "fast" or any(event_type in FAST_EVENT_TYPES for event_type in normalize_event_types(event_values(item.get("event_types")))):
            count += 1
        else:
            break
    return count


def fast_quota_status(config: ConfigDocument, history: list[dict[str, Any]], chapter_number: int) -> dict[str, Any]:
    limit = as_int(config.data.get("pacing", {}).get("fast_chapter_quota_per_volume")) or 9999
    volume = infer_volume(config, chapter_number)
    used = 0
    for item in history:
        chapter = as_int(item.get("chapter_number") or item.get("chapter"))
        if chapter and infer_volume(config, chapter) == volume:
            if str(item.get("tier") or "") == "fast" or any(event_type in FAST_EVENT_TYPES for event_type in normalize_event_types(event_values(item.get("event_types")))):
                used += 1
    return {"volume": volume, "used": used, "limit": limit, "blocked": used >= limit}


def volume_fast_usage(config: ConfigDocument, history: list[dict[str, Any]]) -> dict[str, int]:
    usage: dict[str, int] = {}
    for item in history:
        chapter = as_int(item.get("chapter_number") or item.get("chapter"))
        if chapter and (str(item.get("tier") or "") == "fast" or any(event_type in FAST_EVENT_TYPES for event_type in normalize_event_types(event_values(item.get("event_types"))))):
            key = str(infer_volume(config, chapter))
            usage[key] = usage.get(key, 0) + 1
    return usage


def infer_volume(config: ConfigDocument, chapter_number: int) -> int:
    distribution = config.data.get("pacing", {}).get("volume_distribution")
    if isinstance(distribution, list) and distribution:
        cursor = 0
        for index, count in enumerate(distribution, start=1):
            cursor += as_int(count)
            if chapter_number <= cursor:
                return index
        return len(distribution)
    root = resolve_project_root(config)
    plan = load_json(root / "20_outline" / "chapter_plan.json", default=[])
    row = next(
        (
            item for item in plan
            if isinstance(plan, list) and isinstance(item, dict)
            and as_int(item.get("chapter_number")) == chapter_number
        ),
        {},
    )
    volume_id = str(row.get("volume_id") or "") if isinstance(row, dict) else ""
    volumes = load_json(root / "20_outline" / "volumes.json", default=[])
    for index, volume in enumerate(volumes if isinstance(volumes, list) else [], start=1):
        if isinstance(volume, dict) and str(volume.get("id") or "") == volume_id:
            return as_int(volume.get("number")) or index
    length = config.data["length"]
    size = max(
        1,
        round(int(length["volume"]["target_characters"]) / int(length["chapter"]["target_characters"])),
    )
    return ((chapter_number - 1) // size) + 1


def event_cooldown_config(config: ConfigDocument) -> dict[str, int]:
    configured = config.data.get("pacing", {}).get("event_cooldown")
    if isinstance(configured, dict):
        values = {EVENT_TYPE_ALIASES.get(str(key), str(key)): max(0, as_int(value) or 0) for key, value in configured.items()}
    else:
        values = {}
    defaults = {
        "conflict_thrill": 1,
        "bond_deepening": 1,
        "faction_building": 1,
        "world_painting": 1,
        "tension_escalation": 1,
    }
    return {event_type: values.get(event_type, defaults.get(event_type, 1)) for event_type in event_type_pool(config)}


def normalize_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("anchors", "chapters", "items", "records", "data"):
            if isinstance(value.get(key), list):
                return value[key]
        return list(value.values())
    return []


def load_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def utc_stamp() -> str:
    return re.sub(r"[^0-9]", "", utc_now())[:14]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
