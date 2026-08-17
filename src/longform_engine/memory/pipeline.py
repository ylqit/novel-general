"""Deterministic TCS, current memory views, and semantic evidence checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import re

from longform_engine.character_expression import character_expression_diagnostics
from longform_engine.config import ConfigDocument
from longform_engine.db import sync_database
from longform_engine.storage import apply_transaction, atomic_write_text, resolve_project_root


@dataclass(frozen=True)
class MemoryValidateResult:
    """Validation result for canonical narrative memory files."""

    ok: bool
    scene_memories: int
    chapter_memories: int
    arc_memories: int
    character_memories: int
    tcs_snapshots: int
    errors: tuple[str, ...]
    warnings: tuple[str, ...]



@dataclass(frozen=True)
class TcsResult:
    """Result for a Temporal Context State snapshot."""

    chapter_number: int
    tcs_file: str
    current_characters: tuple[str, ...]
    locations: tuple[str, ...]
    recent_events: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    open_foreshadows: tuple[str, ...]
    active_constraints: tuple[str, ...]


@dataclass(frozen=True)
class TcsTransitionResult:
    """Result for advancing the Temporal Context State machine."""

    chapter_number: int
    transition_file: str
    current_file: str
    next_chapter: int
    known_facts: int
    relationship_states: int


@dataclass(frozen=True)
class TcsValidateResult:
    """Validation result for TCS state machine snapshots and transitions."""

    chapter_number: int
    ok: bool
    file: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class StyleMemoryResult:
    """Result for canonical Style/Voice Memory refresh."""

    style_file: str
    source_chapters: int
    updated: bool


@dataclass(frozen=True)
class MemoryCompressResult:
    """Result for deterministic memory compression."""

    scope: str
    from_chapter: int
    to_chapter: int
    output_file: str
    source_count: int
    db_synced: bool



@dataclass(frozen=True)
class CharacterCheckResult:
    """Deterministic character consistency report for a draft/candidate file."""

    chapter_number: int
    file: str
    report_file: str
    passed: bool
    findings: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


REQUIRED_MEMORY_FIELDS = (
    "chapter",
    "scene",
    "characters",
    "location",
    "events",
    "emotion_state",
    "conflict_state",
    "evidence",
)

REQUIRED_CHARACTER_FIELDS = (
    "character_id",
    "name",
    "aliases",
    "personality_baseline",
    "current_beliefs",
    "knowledge_scope",
    "relationship_map",
    "speech_style",
    "forbidden_actions",
    "state_history",
    "evidence",
    "source_chapters",
    "status",
)


def validate_memory(config: ConfigDocument) -> MemoryValidateResult:
    """Validate canonical scene/chapter/arc memory and TCS snapshots."""

    root = resolve_project_root(config)
    errors: list[str] = []
    warnings: list[str] = []
    scene_count = validate_memory_dir(root, root / "60_rag" / "memory" / "scenes", "scene", errors, warnings)
    chapter_count = validate_memory_dir(root, root / "60_rag" / "memory" / "chapters", "chapter", errors, warnings)
    arc_count = validate_memory_dir(root, root / "60_rag" / "memory" / "arcs", "arc", errors, warnings, allow_partial=True)
    character_count = validate_character_memory_dir(root, errors, warnings)
    validate_style_memory(root, errors, warnings)
    tcs_count = validate_tcs_dir(root, errors, warnings)
    return MemoryValidateResult(
        ok=not errors,
        scene_memories=scene_count,
        chapter_memories=chapter_count,
        arc_memories=arc_count,
        character_memories=character_count,
        tcs_snapshots=tcs_count,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def build_style_memory(config: ConfigDocument) -> StyleMemoryResult:
    """Build canonical Style/Voice Memory from finalized chapters and style bible."""

    root = resolve_project_root(config)
    final_dir = root / "40_manuscript" / "final"
    style_dir = root / "60_rag" / "memory" / "style"
    style_dir.mkdir(parents=True, exist_ok=True)
    final_texts: list[tuple[int, Path, str]] = []
    for path in sorted([*final_dir.glob("*.md"), *final_dir.glob("*.txt")]):
        chapter = parse_chapter_number(path)
        if chapter is None:
            continue
        final_texts.append((chapter, path, safe_read_text(path)))
    style_bible = root / "10_bible" / "style_bible.md"
    bible_text = safe_read_text(style_bible) if style_bible.exists() else ""
    combined = "\n\n".join([bible_text, *[text for _chapter, _path, text in final_texts]]).strip()
    fingerprint = style_fingerprint(combined)
    payload = {
        "schema_version": 1,
        "memory_type": "style",
        "status": "canonical",
        "source_path": "40_manuscript/final",
        "style_bible": relative_path(root, style_bible) if style_bible.exists() else "",
        "source_chapters": [chapter for chapter, _path, _text in final_texts],
        "source_hash": sha256_text(combined),
        "fingerprint": fingerprint,
        "notes": trim_text(bible_text, 800),
        "updated_at": utc_now(),
    }
    path = style_dir / "style_fingerprint.json"
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sync_database(config)
    return StyleMemoryResult(style_file=str(path), source_chapters=len(final_texts), updated=True)


def compress_memory(
    config: ConfigDocument,
    *,
    scope: str,
    from_chapter: int,
    to_chapter: int,
) -> MemoryCompressResult:
    """Compress canonical scene/chapter/arc memory into a higher-level memory unit."""

    if scope not in {"chapter", "arc", "volume"}:
        raise ValueError("scope must be chapter, arc, or volume.")
    if from_chapter <= 0 or to_chapter <= 0 or to_chapter < from_chapter:
        raise ValueError("chapter range must be positive and ordered.")
    root = resolve_project_root(config)
    if is_memory_globally_stale(root):
        raise ValueError("memory is marked stale; handle stale artifacts before compression.")
    if scope == "chapter":
        records = load_memory_records(root / "60_rag" / "memory" / "scenes", from_chapter=from_chapter, to_chapter=to_chapter)
        output_dir = root / "60_rag" / "memory" / "chapters"
        output_name = f"ch{from_chapter:03d}.json" if from_chapter == to_chapter else f"ch{from_chapter:03d}_to_ch{to_chapter:03d}.json"
        memory_type = "chapter"
    else:
        source_dir = root / "60_rag" / "memory" / "chapters" if scope == "arc" else root / "60_rag" / "memory" / "arcs"
        records = load_memory_records(source_dir, from_chapter=from_chapter, to_chapter=to_chapter)
        output_dir = root / "60_rag" / "memory" / "arcs"
        output_name = f"{scope}_ch{from_chapter:03d}_to_ch{to_chapter:03d}.json"
        memory_type = scope
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = compressed_memory_payload(
        memory_type=memory_type,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        records=records,
    )
    path = output_dir / output_name
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    sync_database(config)
    return MemoryCompressResult(
        scope=scope,
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        output_file=str(path),
        source_count=len(records),
        db_synced=True,
    )



def character_check(config: ConfigDocument, *, chapter_number: int, file_path: str | Path) -> CharacterCheckResult:
    """Check a draft/candidate file against Character Memory Cards and TCS."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    path = resolve_under_root(root, file_path)
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        raise ValueError("character-check file must live under the project root.")
    text = safe_read_text(path)
    tcs = build_tcs(config, chapter_number=chapter_number)
    tcs_payload = read_json(Path(tcs.tcs_file), default={})
    findings, warnings = character_consistency_findings(root, chapter_number=chapter_number, text=text, tcs=tcs_payload)
    report_file = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "character_consistency_report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        report_file,
        json.dumps(
            {
                "chapter_number": chapter_number,
                "file": relative_path(root, path),
                "passed": not any(item.get("severity") in {"P0", "P1"} for item in findings),
                "findings": findings,
                "warnings": warnings,
                "updated_at": utc_now(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return CharacterCheckResult(
        chapter_number=chapter_number,
        file=str(path),
        report_file=str(report_file),
        passed=not any(item.get("severity") in {"P0", "P1"} for item in findings),
        findings=tuple(findings),
        warnings=tuple(warnings),
    )


def build_tcs(config: ConfigDocument, *, chapter_number: int) -> TcsResult:
    """Build and persist a Temporal Context State snapshot for a target chapter."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    tcs_dir = root / "30_state" / "tcs"
    path = tcs_dir / f"ch{chapter_number:03d}.json"
    existing = read_json(path, default={})
    if reusable_semantic_tcs(root, existing, chapter_number):
        return tcs_result_from_payload(path, existing, chapter_number)
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    characters = current_characters(graph, chapter_number)
    locations = current_locations(graph, chapter_number)
    events = recent_events(graph, chapter_number)
    conflicts = unresolved_conflicts(root, graph, chapter_number)
    foreshadows = open_foreshadows(graph, chapter_number)
    constraints = active_constraints(root, graph, chapter_number)
    payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "current_characters": characters,
        "locations": locations,
        "emotion_state": infer_recent_emotion(root, chapter_number),
        "recent_events": events,
        "unresolved_conflicts": conflicts,
        "open_foreshadows": foreshadows,
        "active_constraints": constraints,
        "sources": {
            "story_graph": "30_state/story_graph.json",
            "unresolved_threads": "30_state/unresolved_threads.json",
            "scene_memory": "60_rag/memory/scenes",
            "chapter_memory": "60_rag/memory/chapters",
        },
        "updated_at": utc_now(),
    }
    payload.update(tcs_state_payload(root, graph, chapter_number, characters, locations, events, conflicts, foreshadows, constraints))
    tcs_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return tcs_result_from_payload(path, payload, chapter_number)


def reusable_semantic_tcs(root: Path, payload: Any, chapter_number: int) -> bool:
    """Return true only for a compact TCS bound to an unchanged canonical ledger."""

    if not isinstance(payload, dict) or payload.get("schema") != "tcs_compact_v2":
        return False
    if int(payload.get("chapter_number") or 0) != chapter_number:
        return False
    source_path = str(payload.get("source_semantic_ledger") or "")
    source_hash = str(payload.get("source_semantic_ledger_sha256") or "")
    if not source_path or not source_hash:
        return False
    source = (root / source_path).resolve()
    try:
        source.relative_to((root / "30_state" / "semantic_ledger").resolve())
    except ValueError:
        return False
    if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        return False
    ledger = read_json(source, default={})
    return (
        isinstance(ledger, dict)
        and ledger.get("canonical") is True
        and int(ledger.get("chapter_number") or 0) + 1 == chapter_number
    )


def tcs_result_from_payload(path: Path, payload: dict[str, Any], chapter_number: int) -> TcsResult:
    open_threads = []
    raw_open_threads = payload.get("open_foreshadows")
    values = raw_open_threads if isinstance(raw_open_threads, list) else normalize_list(raw_open_threads)
    for item in values:
        if isinstance(item, dict):
            value = str(item.get("thread_id") or item.get("thread") or "")
        else:
            value = str(item)
        if value:
            open_threads.append(value)
    return TcsResult(
        chapter_number=chapter_number,
        tcs_file=str(path),
        current_characters=tuple(normalize_list(payload.get("current_characters"))),
        locations=tuple(normalize_list(payload.get("locations"))),
        recent_events=tuple(normalize_list(payload.get("recent_events"))),
        unresolved_conflicts=tuple(normalize_list(payload.get("unresolved_conflicts"))),
        open_foreshadows=tuple(open_threads),
        active_constraints=tuple(normalize_list(payload.get("active_constraints"))),
    )


def build_tcs_transition(config: ConfigDocument, *, chapter_number: int) -> TcsTransitionResult:
    """Advance TCS from a finalized chapter into current state for the next chapter."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    source = find_final_chapter(root, chapter_number)
    if source is None:
        raise ValueError("TCS transitions require a finalized chapter source.")
    graph = read_json(root / "30_state" / "story_graph.json", default={})
    next_chapter = chapter_number + 1
    snapshot = build_tcs(config, chapter_number=next_chapter)
    current_payload = read_json(Path(snapshot.tcs_file), default={})
    final_text = safe_read_text(source)
    transition_payload = {
        "schema_version": 1,
        "chapter_number": chapter_number,
        "source_path": relative_path(root, source),
        "from_chapter": chapter_number,
        "to_chapter": next_chapter,
        "known_facts_added": known_facts_from_chapter(root, graph, chapter_number, final_text),
        "relationship_state_added": relationship_state_for_chapter(graph, next_chapter),
        "character_knowledge_added": character_knowledge(root, next_chapter),
        "active_plot_threads": active_plot_threads_from_state(current_payload),
        "state_transitions": state_transitions_from_chapter(root, graph, chapter_number),
        "status": "canonical",
        "updated_at": utc_now(),
    }
    tcs_dir = root / "30_state" / "tcs"
    transition_dir = tcs_dir / "transitions"
    transition_dir.mkdir(parents=True, exist_ok=True)
    transition_file = transition_dir / f"ch{chapter_number:03d}.json"
    current_file = tcs_dir / "current.json"
    current_payload["current_transition"] = relative_path(root, transition_file)
    current_payload["state_transitions"] = transition_payload["state_transitions"]
    current_payload["updated_at"] = utc_now()
    atomic_write_text(transition_file, json.dumps(transition_payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(current_file, json.dumps(current_payload, ensure_ascii=False, indent=2) + "\n")
    sync_database(config)
    return TcsTransitionResult(
        chapter_number=chapter_number,
        transition_file=str(transition_file),
        current_file=str(current_file),
        next_chapter=next_chapter,
        known_facts=len(transition_payload["known_facts_added"]),
        relationship_states=len(transition_payload["relationship_state_added"]),
    )


def validate_tcs(config: ConfigDocument, *, chapter_number: int) -> TcsValidateResult:
    """Validate a TCS snapshot/current state for future-fact leakage."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    root = resolve_project_root(config)
    path = root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json"
    if not path.exists():
        path = root / "30_state" / "tcs" / "current.json"
    payload = read_json(path, default={})
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        errors.append("TCS payload must be a JSON object.")
        payload = {}
    for field in (
        "reader_progress",
        "known_facts",
        "character_knowledge",
        "relationship_state",
        "active_plot_threads",
        "spoiler_guard",
        "state_transitions",
    ):
        if field not in payload:
            errors.append(f"TCS missing {field}.")
    for collection_name in ("known_facts", "relationship_state", "state_transitions"):
        for index, item in enumerate(normalize_records(payload.get(collection_name))):
            if not isinstance(item, dict):
                continue
            fact_chapter = as_int(item.get("chapter") or item.get("chapter_number") or item.get("from_chapter"))
            if fact_chapter and fact_chapter > chapter_number:
                errors.append(f"{collection_name}[{index}] leaks future chapter {fact_chapter}.")
    guard = payload.get("spoiler_guard") if isinstance(payload.get("spoiler_guard"), dict) else {}
    if guard.get("forbid_future_spoiler") is not True:
        warnings.append("spoiler_guard.forbid_future_spoiler should be true.")
    return TcsValidateResult(
        chapter_number=chapter_number,
        ok=not errors,
        file=str(path),
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def deterministic_evidence_gate_findings(
    config: ConfigDocument,
    *,
    chapter_number: int,
    text: str,
) -> tuple[list[dict[str, Any]], list[str], Path]:
    """Run deterministic phrase/statistical evidence checks; this is not semantic reasoning."""

    root = resolve_project_root(config)
    tcs_path = root / "30_state" / "tcs" / f"ch{chapter_number:03d}.json"
    if not tcs_path.exists():
        tcs_path = Path(build_tcs(config, chapter_number=chapter_number).tcs_file)
    payload = read_json(tcs_path, default={})
    failures: list[dict[str, Any]] = []
    warnings: list[str] = []
    checks = [
        (
            "semantic_motivation_break",
            "P1",
            ("突然原谅", "无缘无故原谅", "instantly forgave", "suddenly forgave"),
            "possible forgiveness/motivation jump without causal memory support",
        ),
        (
            "semantic_location_jump",
            "P1",
            ("瞬间抵达", "突然出现在", "teleported without cost"),
            "possible location jump without transition evidence",
        ),
        (
            "semantic_ability_boundary",
            "P1",
            ("无视代价", "没有冷却", "ignored the cost", "without cooldown"),
            "possible ability boundary violation",
        ),
        (
            "semantic_foreshadow_early_payoff",
            "P1",
            ("提前揭开", "直接揭开核心秘密", "revealed the core secret too early"),
            "possible premature foreshadow payoff",
        ),
        (
            "semantic_relationship_jump",
            "P2",
            ("突然信任", "突然结盟", "suddenly trusted"),
            "possible relationship jump; verify against TCS",
        ),
    ]
    for code, severity, needles, message in checks:
        span = first_evidence(text, needles)
        if not span:
            continue
        finding = {
            "code": code,
            "severity": severity,
            "message": message,
            "evidence_span": span,
            "tcs_file": relative_path(root, tcs_path),
        }
        if severity in {"P0", "P1"}:
            failures.append(finding)
        else:
            warnings.append(f"{code}: {message} ({span})")

    style_payload = read_json(root / "60_rag" / "memory" / "style" / "style_fingerprint.json", default={})
    if isinstance(style_payload, dict) and isinstance(style_payload.get("fingerprint"), dict):
        current_style = style_fingerprint(text)
        baseline = style_payload["fingerprint"]
        drift = style_drift_finding(baseline, current_style)
        if drift:
            if drift["severity"] in {"P0", "P1"}:
                failures.append(drift)
            else:
                warnings.append(f"{drift['code']}: {drift['message']} ({drift['evidence_span']})")

    character_failures, character_warnings = character_consistency_findings(root, chapter_number=chapter_number, text=text, tcs=payload)
    for finding in character_failures:
        if finding.get("severity") in {"P0", "P1"}:
            failures.append(finding)
        else:
            warnings.append(f"{finding.get('code')}: {finding.get('message')} ({finding.get('evidence_span')})")
    warnings.extend(character_warnings)

    if failures and not payload.get("recent_events"):
        warnings.append("semantic gate ran without recent TCS events; consider memory semantic-task/apply.")
    report_path = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "deterministic_evidence_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        report_path,
        "\n".join(
            [
                f"# Deterministic Evidence Gate Report ch{chapter_number:03d}",
                "",
                f"- TCS: `{relative_path(root, tcs_path)}`",
                f"- Failures: {len(failures)}",
                f"- Warnings: {len(warnings)}",
                "",
                "## Findings",
                "",
                *[f"- [{item['severity']}] {item['code']}: {item['message']} | evidence: {item['evidence_span']}" for item in failures],
                *[f"- WARN: {warning}" for warning in warnings],
                "" if failures or warnings else "- None",
                "",
            ]
        ),
    )
    return failures, warnings, report_path


def semantic_gate_findings(config: ConfigDocument, *, chapter_number: int, text: str) -> tuple[list[dict[str, Any]], list[str], Path]:
    """Backward-compatible alias for deterministic_evidence_gate_findings."""

    return deterministic_evidence_gate_findings(config, chapter_number=chapter_number, text=text)


def style_drift_finding(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any] | None:
    base_sentence = float(baseline.get("avg_sentence_chars") or 0)
    current_sentence = float(current.get("avg_sentence_chars") or 0)
    base_dialogue = float(baseline.get("dialogue_ratio") or 0)
    current_dialogue = float(current.get("dialogue_ratio") or 0)
    if base_sentence <= 0:
        return None
    ratio = current_sentence / max(1.0, base_sentence)
    dialogue_delta = abs(current_dialogue - base_dialogue)
    if ratio >= 3.0 or ratio <= 0.33:
        return {
            "code": "semantic_style_voice_drift",
            "severity": "P1",
            "message": "style/voice drift from canonical Style Memory",
            "evidence_span": f"avg_sentence_chars baseline={base_sentence:.2f} current={current_sentence:.2f}",
        }
    if ratio >= 2.0 or ratio <= 0.5 or dialogue_delta > 0.08:
        return {
            "code": "semantic_style_voice_drift",
            "severity": "P2",
            "message": "possible style/voice drift from canonical Style Memory",
            "evidence_span": f"sentence_ratio={ratio:.2f}, dialogue_delta={dialogue_delta:.4f}",
        }
    return None


def mark_memory_stale(config: ConfigDocument, *, from_chapter: int, reason: str, change_description: str = "") -> Path:
    """Mark Memory v2 artifacts stale after outline revision or rollback."""

    root = resolve_project_root(config)
    payload = {
        "from_chapter": from_chapter,
        "reason": reason,
        "change_description": change_description,
        "stale": True,
        "stale_artifacts": [
            "scene_memory",
            "chapter_memory",
            "arc_memory",
            "style_memory",
            "temporal_context_state",
            "semantic_embeddings",
        ],
        "requires": ["memory validate", "rag build --with-embeddings", "db rebuild"],
        "unsafe_continuation_blocker": True,
        "updated_at": utc_now(),
    }
    path = root / "60_rag" / "memory" / "stale.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    state_path = root / "30_state" / "stale_indexes.json"
    state = read_json(state_path, default={})
    if not isinstance(state, dict):
        state = {}
    stale = normalize_list(state.get("stale"))
    for item in ("scene_memory", "chapter_memory", "arc_memory", "style_memory", "temporal_context_state", "semantic_embeddings"):
        if item not in stale:
            stale.append(item)
    state.update(payload)
    state["stale"] = stale
    state["next_command"] = "longform-engine memory validate project.yaml"
    atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    try:
        from longform_engine.vectorstore import delete_by_filter

        delete_by_filter(config, from_chapter=from_chapter)
    except Exception:
        pass
    return path


def is_memory_globally_stale(root: Path) -> bool:
    payload = read_json(root / "60_rag" / "memory" / "stale.json", default={})
    return isinstance(payload, dict) and bool(payload.get("stale"))


def validate_memory_dir(
    root: Path,
    directory: Path,
    memory_type: str,
    errors: list[str],
    warnings: list[str],
    *,
    allow_partial: bool = False,
) -> int:
    count = 0
    if not directory.exists():
        warnings.append(f"{relative_path(root, directory)} is missing.")
        return 0
    for path in sorted(directory.glob("*.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            errors.append(f"{relative_path(root, path)} is not a JSON object.")
            continue
        if payload.get("status") == "stale":
            warnings.append(f"{relative_path(root, path)} is stale.")
        source = str(payload.get("source_path") or "")
        if source and not source.startswith("40_manuscript/final/"):
            errors.append(f"{relative_path(root, path)} source_path is not a final manuscript.")
        if any(part in source.replace("\\", "/") for part in ("agent_drafts", "research_inbox", "40_manuscript/draft")):
            errors.append(f"{relative_path(root, path)} source_path points to non-canonical material.")
        required = () if allow_partial else REQUIRED_MEMORY_FIELDS
        for field in required:
            if field not in payload:
                errors.append(f"{relative_path(root, path)} missing {field}.")
        count += 1
    return count


def validate_style_memory(root: Path, errors: list[str], warnings: list[str]) -> int:
    directory = root / "60_rag" / "memory" / "style"
    if not directory.exists():
        warnings.append("60_rag/memory/style is missing.")
        return 0
    count = 0
    for path in sorted(directory.glob("*.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            errors.append(f"{relative_path(root, path)} is not a JSON object.")
            continue
        if payload.get("memory_type") != "style":
            errors.append(f"{relative_path(root, path)} memory_type must be style.")
        fingerprint = payload.get("fingerprint")
        if not isinstance(fingerprint, dict):
            errors.append(f"{relative_path(root, path)} missing fingerprint object.")
        else:
            for field in (
                "sentence_length_distribution",
                "paragraph_length_distribution",
                "dialogue_ratio",
                "punctuation_density",
                "repeated_phrases",
                "perspective_stability",
                "narrative_action_interior_ratio",
            ):
                if field not in fingerprint:
                    errors.append(f"{relative_path(root, path)} fingerprint missing {field}.")
        count += 1
    return count


def validate_tcs_dir(root: Path, errors: list[str], warnings: list[str]) -> int:
    directory = root / "30_state" / "tcs"
    if not directory.exists():
        warnings.append("30_state/tcs is missing.")
        return 0
    count = 0
    for path in sorted(directory.glob("ch*.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            errors.append(f"{relative_path(root, path)} is not a JSON object.")
            continue
        for field in ("chapter_number", "current_characters", "locations", "recent_events", "unresolved_conflicts", "open_foreshadows", "active_constraints"):
            if field not in payload:
                errors.append(f"{relative_path(root, path)} missing {field}.")
        count += 1
    return count


def validate_character_memory_dir(root: Path, errors: list[str], warnings: list[str]) -> int:
    directory = root / "60_rag" / "memory" / "characters"
    if not directory.exists():
        warnings.append("60_rag/memory/characters is missing.")
        return 0
    count = 0
    for path in sorted(directory.glob("*.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            errors.append(f"{relative_path(root, path)} is not a JSON object.")
            continue
        for field in REQUIRED_CHARACTER_FIELDS:
            if field not in payload:
                errors.append(f"{relative_path(root, path)} missing {field}.")
        if not normalize_list(payload.get("evidence")):
            errors.append(f"{relative_path(root, path)} missing evidence.")
        count += 1
    return count


def load_memory_records(directory: Path, *, from_chapter: int, to_chapter: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            continue
        if str(payload.get("status") or "canonical").lower() == "stale":
            continue
        chapter = as_int(payload.get("chapter") or payload.get("from_chapter") or parse_chapter_number(path))
        end = as_int(payload.get("to_chapter")) or chapter
        if chapter and end and not (end < from_chapter or chapter > to_chapter):
            payload = dict(payload)
            payload["_memory_file"] = str(path)
            records.append(payload)
    return records


def compressed_memory_payload(
    *,
    memory_type: str,
    from_chapter: int,
    to_chapter: int,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    summaries = [str(item.get("summary") or item.get("text") or "") for item in records]
    events = merge_memory_lists(records, "events")
    characters = merge_memory_lists(records, "characters")
    locations = merge_memory_lists(records, "locations") + merge_memory_lists(records, "location")
    emotions = [str(item.get("emotion_state") or "") for item in records if item.get("emotion_state")]
    conflicts = [str(item.get("conflict_state") or "") for item in records if item.get("conflict_state")]
    evidence = merge_memory_lists(records, "evidence")[:12]
    summary = trim_text(" ".join(item for item in summaries if item), 1600)
    return {
        "schema_version": 1,
        "memory_type": memory_type,
        "chapter": from_chapter,
        "from_chapter": from_chapter,
        "to_chapter": to_chapter,
        "scene": 0,
        "source_path": "40_manuscript/final",
        "summary": summary or f"{memory_type} memory ch{from_chapter:03d}-ch{to_chapter:03d}",
        "characters": dedupe(characters),
        "location": ", ".join(dedupe(locations)),
        "locations": dedupe(locations),
        "events": dedupe(events),
        "main_event_chain": dedupe(events),
        "relationship_changes": merge_memory_lists(records, "relationship_changes"),
        "emotion_curve": emotions[-20:],
        "emotion_state": emotions[-1] if emotions else "unknown",
        "conflict_progress": conflicts[-20:],
        "conflict_state": conflicts[-1] if conflicts else "open",
        "foreshadow_state": merge_memory_lists(records, "foreshadow_refs") + merge_memory_lists(records, "open_foreshadows"),
        "ability_boundary_changes": merge_memory_lists(records, "ability_boundary_changes"),
        "evidence": evidence,
        "source_count": len(records),
        "status": "canonical",
        "updated_at": utc_now(),
    }


def merge_memory_lists(records: list[dict[str, Any]], key: str) -> list[str]:
    values: list[str] = []
    for record in records:
        values.extend(normalize_list(record.get(key)))
    return dedupe(values)



def load_character_cards(root: Path) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for path in sorted((root / "60_rag" / "memory" / "characters").glob("*.json")):
        payload = read_json(path, default={})
        if isinstance(payload, dict) and str(payload.get("status") or "canonical").lower() != "stale":
            payload = dict(payload)
            payload["_memory_file"] = relative_path(root, path)
            cards.append(payload)
    return cards


def character_consistency_findings(root: Path, *, chapter_number: int, text: str, tcs: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    findings: list[dict[str, Any]] = []
    warnings: list[str] = []
    lowered = text.lower()
    for card in load_character_cards(root):
        character_id = str(card.get("character_id") or "")
        names = [str(card.get("name") or ""), *normalize_list(card.get("aliases"))]
        if names and not any(name and name in text for name in names):
            continue
        for action in normalize_list(card.get("forbidden_actions")):
            if action and action.lower() in lowered:
                findings.append(
                    consistency_finding(
                        "character_forbidden_action",
                        "P1",
                        character_id,
                        evidence_span=action,
                        expected_state="forbidden action must not occur at this chapter",
                        observed_text=action,
                        repair_action="Rewrite the action or add validated transition/evidence before using it.",
                    )
                )
        for secret in normalize_list(card.get("knowledge_scope")):
            if secret and secret.lower() in lowered and not known_to_character(card, secret, chapter_number):
                findings.append(
                    consistency_finding(
                        "character_knowledge_leak",
                        "P1",
                        character_id,
                        evidence_span=secret,
                        expected_state="character must not know this fact yet",
                        observed_text=secret,
                        repair_action="Remove the leaked knowledge or apply a canonical memory/graph transition first.",
                    )
                )
        known_text = character_known_text(card)
        for fact in tcs_known_fact_texts(tcs):
            if fact and fact.lower() in lowered and fact.lower() not in known_text:
                findings.append(
                    consistency_finding(
                        "character_knowledge_leak",
                        "P1",
                        character_id,
                        evidence_span=fact,
                        expected_state="character knowledge scope must include this fact before use",
                        observed_text=fact,
                        repair_action="Remove the leaked fact or apply a character/TCS transition that grants the knowledge.",
                    )
                )
        generic_leak = first_evidence(
            text,
            (
                "before evidence",
                "before learning",
                "reveals secret",
                "secret pact knowledge",
                "future reveal",
            ),
        )
        if generic_leak and any(term in lowered for term in ("secret", "future", "before evidence", "before learning")):
            findings.append(
                consistency_finding(
                    "character_knowledge_leak",
                    "P1",
                    character_id,
                    evidence_span=generic_leak,
                    expected_state="knowledge must be earned by prior canonical evidence",
                    observed_text=generic_leak,
                    repair_action="Rewrite the line or add validated evidence that the character learned this fact.",
                )
            )
        baseline_hit = personality_conflict(card, text)
        if baseline_hit:
            findings.append(
                consistency_finding(
                    "character_personality_conflict",
                    "P2",
                    character_id,
                    evidence_span=baseline_hit,
                    expected_state=", ".join(normalize_list(card.get("personality_baseline"))[:3]) or "personality baseline",
                    observed_text=baseline_hit,
                    repair_action="Adjust the scene to match the baseline or add evidence for the personality shift.",
                )
            )
        speech_hit = speech_style_conflict(card, text)
        if speech_hit:
            findings.append(
                consistency_finding(
                    "character_speech_stage_mismatch",
                    "P2",
                    character_id,
                    evidence_span=speech_hit,
                    expected_state=json.dumps(card.get("speech_style") or {}, ensure_ascii=False),
                    observed_text=speech_hit,
                    repair_action="Adjust address/speech style to the current relationship stage.",
                )
            )
    relationship_jump = first_evidence(text, ("突然信任", "突然结盟", "suddenly trusted", "instantly trusted"))
    if relationship_jump:
        findings.append(
            consistency_finding(
                "character_relationship_jump",
                "P1",
                "unknown",
                evidence_span=relationship_jump,
                expected_state=json.dumps(tcs.get("relationship_state") or [], ensure_ascii=False),
                observed_text=relationship_jump,
                repair_action="Add a validated relationship transition or rewrite the sudden trust turn.",
            )
        )
    return findings, warnings


def consistency_finding(
    code: str,
    severity: str,
    character_id: str,
    *,
    evidence_span: str,
    expected_state: str,
    observed_text: str,
    repair_action: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "character_id": character_id,
        "message": code.replace("_", " "),
        "evidence_span": evidence_span,
        "expected_state": expected_state,
        "observed_text": observed_text,
        "repair_action": repair_action,
    }


def known_to_character(card: dict[str, Any], fact: str, chapter_number: int) -> bool:
    for item in normalize_records(card.get("state_history")):
        if not isinstance(item, dict):
            continue
        item_chapter = as_int(item.get("chapter") or item.get("chapter_number"))
        if item_chapter and item_chapter <= chapter_number and fact in " ".join(normalize_list(item.get("known_facts") or item.get("knowledge") or item.get("evidence"))):
            return True
    return fact in " ".join(normalize_list(card.get("current_beliefs")))


def character_known_text(card: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("current_beliefs", "knowledge_scope", "evidence"):
        values.extend(normalize_list(card.get(key)))
    for item in normalize_records(card.get("state_history")):
        if isinstance(item, dict):
            values.extend(normalize_list(item.get("known_facts") or item.get("knowledge") or item.get("evidence")))
    return " ".join(values).lower()


def tcs_known_fact_texts(tcs: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    for item in normalize_records(tcs.get("known_facts")):
        if isinstance(item, dict):
            facts.extend(normalize_list(item.get("fact") or item.get("summary") or item.get("text") or item.get("title")))
        else:
            facts.extend(normalize_list(item))
    return dedupe(facts)


def personality_conflict(card: dict[str, Any], text: str) -> str:
    lowered = text.lower()
    baselines = " ".join(normalize_list(card.get("personality_baseline"))).lower()
    if any(term in baselines for term in ("克制", "冷静", "cautious", "restrained")) and any(term in lowered for term in ("毫不犹豫坦白", "完全失控", "without hesitation confessed")):
        return first_evidence(text, ("毫不犹豫坦白", "完全失控", "without hesitation confessed")) or "personality conflict"
    return ""


def speech_style_conflict(card: dict[str, Any], text: str) -> str:
    style = card.get("speech_style") if isinstance(card.get("speech_style"), dict) else {}
    forbidden = normalize_list(style.get("forbidden_address") or style.get("forbidden_terms"))
    for term in forbidden:
        if term and term in text:
            return term
    return ""


def tcs_state_payload(
    root: Path,
    graph: Any,
    chapter_number: int,
    characters: list[str],
    locations: list[str],
    events: list[str],
    conflicts: list[str],
    foreshadows: list[str],
    constraints: list[str],
) -> dict[str, Any]:
    return {
        "reader_progress": {
            "current_chapter": chapter_number,
            "allowed_chapter_range": [1, chapter_number],
            "forbid_future_spoiler": True,
        },
        "known_facts": known_facts_until(root, graph, chapter_number),
        "character_knowledge": character_knowledge(root, chapter_number),
        "relationship_state": relationship_state_for_chapter(graph, chapter_number),
        "active_plot_threads": active_plot_threads_from_state(
            {
                "unresolved_conflicts": conflicts,
                "open_foreshadows": foreshadows,
            }
        ),
        "spoiler_guard": {
            "current_chapter": chapter_number,
            "forbid_future_spoiler": True,
            "blocked_after_chapter": chapter_number,
        },
        "state_transitions": state_transitions_from_graph(graph, chapter_number),
    }


def known_facts_until(root: Path, graph: Any, chapter_number: int) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    if isinstance(graph, dict):
        for event in normalize_records(graph.get("events")):
            if not isinstance(event, dict):
                continue
            number = as_int(event.get("chapter_number") or event.get("chapter"))
            if number and number <= chapter_number:
                facts.append(
                    {
                        "chapter": number,
                        "fact": str(event.get("title") or event.get("name") or event.get("id") or ""),
                        "source_path": event.get("source_path"),
                    }
                )
    for path in sorted((root / "60_rag" / "memory" / "chapters").glob("ch*.json")):
        number = parse_chapter_number(path)
        if number and number <= chapter_number:
            payload = read_json(path, default={})
            if isinstance(payload, dict):
                facts.append({"chapter": number, "fact": str(payload.get("summary") or ""), "source_path": relative_path(root, path)})
    return [item for item in facts if item.get("fact")][-20:]


def known_facts_from_chapter(root: Path, graph: Any, chapter_number: int, final_text: str) -> list[dict[str, Any]]:
    facts = [item for item in known_facts_until(root, graph, chapter_number) if as_int(item.get("chapter")) == chapter_number]
    if not facts:
        facts.append({"chapter": chapter_number, "fact": trim_text(strip_heading(final_text), 240), "source_path": f"40_manuscript/final/ch{chapter_number:03d}.md"})
    return facts


def strip_heading(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].lstrip().startswith("#"):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def character_knowledge(root: Path, chapter_number: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for card in load_character_cards(root):
        source_chapters = [as_int(item) for item in normalize_list(card.get("source_chapters")) if as_int(item)]
        if source_chapters and min(source_chapters) > chapter_number:
            continue
        records.append(
            {
                "character_id": card.get("character_id"),
                "name": card.get("name"),
                "current_beliefs": normalize_list(card.get("current_beliefs")),
                "knowledge_scope": normalize_list(card.get("knowledge_scope")),
                "source_chapters": source_chapters,
            }
        )
    return records


def relationship_state_for_chapter(graph: Any, chapter_number: int) -> list[dict[str, Any]]:
    states: list[dict[str, Any]] = []
    if not isinstance(graph, dict):
        return states
    for relation in normalize_records(graph.get("relationships")):
        if not isinstance(relation, dict) or not edge_active_for_chapter(relation, chapter_number):
            continue
        states.append(
            {
                "source": relation.get("source") or relation.get("from"),
                "target": relation.get("target") or relation.get("to"),
                "state": relation.get("type") or relation.get("relation"),
                "status": relation.get("status") or "active",
                "from_chapter": as_int(relation.get("from_chapter")) or 1,
                "to_chapter": as_int(relation.get("to_chapter")),
                "evidence_span": relation.get("evidence_span"),
            }
        )
    return states[-20:]


def active_plot_threads_from_state(payload: dict[str, Any]) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    for item in normalize_list(payload.get("unresolved_conflicts")):
        threads.append({"thread": item, "status": "unresolved"})
    for item in normalize_list(payload.get("open_foreshadows")):
        threads.append({"thread": item, "status": "open_foreshadow"})
    return threads


def state_transitions_from_graph(graph: Any, chapter_number: int) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    if not isinstance(graph, dict):
        return transitions
    for relation in normalize_records(graph.get("relationships")):
        if isinstance(relation, dict) and as_int(relation.get("from_chapter")) == chapter_number:
            transitions.append({"type": "relationship", **{key: relation.get(key) for key in ("source", "target", "status", "evidence_span")}})
    for entity in normalize_records(graph.get("entities")):
        if isinstance(entity, dict) and as_int(entity.get("from_chapter")) == chapter_number:
            transitions.append({"type": str(entity.get("type") or "entity"), "id": entity.get("id"), "status": entity.get("status"), "evidence_span": entity.get("evidence_span")})
    return transitions


def state_transitions_from_chapter(root: Path, graph: Any, chapter_number: int) -> list[dict[str, Any]]:
    transitions = state_transitions_from_graph(graph, chapter_number)
    for path in sorted((root / "60_rag" / "memory" / "characters").glob("*.json")):
        payload = read_json(path, default={})
        if not isinstance(payload, dict):
            continue
        if chapter_number in [as_int(item) for item in normalize_list(payload.get("source_chapters")) if as_int(item)]:
            transitions.append({"type": "character_memory", "character_id": payload.get("character_id"), "source_path": relative_path(root, path)})
    return transitions


def current_characters(graph: Any, chapter_number: int) -> list[str]:
    if not isinstance(graph, dict):
        return []
    names: list[str] = []
    for entity in graph.get("entities", []):
        if not isinstance(entity, dict) or str(entity.get("type") or "").lower() != "character":
            continue
        if entity_active_for_chapter(entity, chapter_number):
            names.append(str(entity.get("name") or entity.get("id") or ""))
    return dedupe([item for item in names if item])


def current_locations(graph: Any, chapter_number: int) -> list[str]:
    if not isinstance(graph, dict):
        return []
    names: list[str] = []
    for entity in graph.get("entities", []):
        if not isinstance(entity, dict) or str(entity.get("type") or "").lower() != "location":
            continue
        if entity_active_for_chapter(entity, chapter_number):
            names.append(str(entity.get("name") or entity.get("id") or ""))
    for event in normalize_records(graph.get("events"))[-5:]:
        if not isinstance(event, dict):
            continue
        number = as_int(event.get("chapter_number") or event.get("chapter"))
        if not number or number >= chapter_number:
            continue
        for location in normalize_list(event.get("locations") or event.get("location")):
            names.append(location)
    return dedupe([item for item in names if item])


def recent_events(graph: Any, chapter_number: int) -> list[str]:
    if not isinstance(graph, dict):
        return []
    events: list[tuple[int, str]] = []
    for event in normalize_records(graph.get("events")):
        if not isinstance(event, dict):
            continue
        number = as_int(event.get("chapter_number") or event.get("chapter"))
        if number and number < chapter_number:
            title = str(event.get("title") or event.get("name") or event.get("id") or "")
            if title:
                events.append((number, title))
    return [title for _number, title in sorted(events)[-8:]]


def unresolved_conflicts(root: Path, graph: Any, chapter_number: int) -> list[str]:
    lines: list[str] = []
    threads = read_json(root / "30_state" / "unresolved_threads.json", default=[])
    for item in normalize_records(threads):
        if not isinstance(item, dict):
            continue
        if str(item.get("status") or "open").lower() in {"closed", "resolved", "done"}:
            continue
        lines.append(str(item.get("title") or item.get("name") or item.get("id") or "open thread"))
    if isinstance(graph, dict):
        for relation in normalize_records(graph.get("relationships")):
            if not isinstance(relation, dict):
                continue
            if not edge_active_for_chapter(relation, chapter_number):
                continue
            if "conflict" in str(relation.get("type") or relation.get("relation") or "").lower():
                lines.append(f"{relation.get('source') or relation.get('from')} -> {relation.get('target') or relation.get('to')}")
    return dedupe(lines)[:10]


def open_foreshadows(graph: Any, chapter_number: int) -> list[str]:
    if not isinstance(graph, dict):
        return []
    values: list[str] = []
    for entity in normalize_records(graph.get("entities")):
        if not isinstance(entity, dict):
            continue
        if str(entity.get("type") or "").lower() != "foreshadowing":
            continue
        status = str(entity.get("status") or "active").lower()
        if status in {"planted", "active", "open"}:
            values.append(str(entity.get("name") or entity.get("id") or "foreshadow"))
    return dedupe(values)


def active_constraints(root: Path, graph: Any, chapter_number: int) -> list[str]:
    constraints: list[str] = []
    power = root / "10_bible" / "power_system.md"
    if power.exists():
        constraints.append("power_system")
    if isinstance(graph, dict):
        for entity in normalize_records(graph.get("entities")):
            if not isinstance(entity, dict) or str(entity.get("type") or "").lower() != "ability":
                continue
            if not entity_active_for_chapter(entity, chapter_number):
                continue
            cost = entity.get("cost") or entity.get("limit") or entity.get("cooldown")
            if cost:
                constraints.append(f"ability:{entity.get('name') or entity.get('id')} cost/limit={cost}")
        for relation in normalize_records(graph.get("relationships")):
            if not isinstance(relation, dict):
                continue
            if edge_active_for_chapter(relation, chapter_number):
                relation_type = relation.get("type") or relation.get("relation") or "related"
                constraints.append(f"relationship:{relation.get('source') or relation.get('from')}->{relation.get('target') or relation.get('to')}:{relation_type}")
    return dedupe(constraints)[:14]


def entity_active_for_chapter(entity: dict[str, Any], chapter_number: int) -> bool:
    mentions = normalize_records(entity.get("mentions"))
    if not mentions:
        start = as_int(entity.get("from_chapter"))
        if not start or start >= chapter_number:
            return False
        status = str(entity.get("status") or "active").lower()
        return status not in {"planned", "inactive", "expired", "paid_off", "resolved", "closed"}
    numbers = [as_int(item.get("chapter_number") or item.get("chapter")) for item in mentions if isinstance(item, dict)]
    return any(number and number < chapter_number for number in numbers)


def edge_active_for_chapter(edge: dict[str, Any], chapter_number: int) -> bool:
    start = as_int(edge.get("from_chapter")) or 1
    end = as_int(edge.get("to_chapter"))
    status = str(edge.get("status") or "active").lower()
    if status in {"planned", "inactive", "expired", "paid_off", "resolved", "closed"}:
        return False
    return start <= chapter_number and (not end or chapter_number <= end)


def summarize_scenes(scenes: Any) -> str:
    if not isinstance(scenes, list):
        return ""
    return " ".join(str(item.get("summary") or item.get("text") or "") for item in scenes if isinstance(item, dict)).strip()


def infer_recent_emotion(root: Path, chapter_number: int) -> str:
    memories = []
    for path in sorted((root / "60_rag" / "memory" / "chapters").glob("ch*.json")):
        number = parse_chapter_number(path)
        if number and number < chapter_number:
            payload = read_json(path, default={})
            if isinstance(payload, dict) and payload.get("emotion_state"):
                memories.append(str(payload.get("emotion_state")))
    return memories[-1] if memories else "unknown"


def infer_emotion_state(text: str) -> str:
    lowered = text.lower()
    if any(item in lowered for item in ("愤怒", "恨", "anger", "hate")):
        return "hostile"
    if any(item in lowered for item in ("信任", "和解", "relief", "trust")):
        return "softening"
    if any(item in lowered for item in ("恐惧", "fear")):
        return "afraid"
    return "unknown"


def infer_conflict_state(text: str) -> str:
    lowered = text.lower()
    if any(item in lowered for item in ("升级", "冲突", "决裂", "escalate", "conflict")):
        return "escalating"
    if any(item in lowered for item in ("解决", "和解", "resolved", "reconcile")):
        return "softening"
    return "open"


def style_fingerprint(text: str) -> dict[str, Any]:
    """Return a deterministic canonical style/voice fingerprint."""

    compact = text.strip()
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", compact) if part.strip()]
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？；;])", compact) if part.strip()]
    sentence_lengths = [len(re.sub(r"\s+", "", item)) for item in sentences]
    paragraph_lengths = [len(re.sub(r"\s+", "", item)) for item in paragraphs]
    dialogue_marks = compact.count('"') + compact.count("'") + compact.count("“") + compact.count("”") + compact.count("「") + compact.count("」")
    expression = character_expression_diagnostics(compact)
    punctuation = sum(compact.count(mark) for mark in "，。！？；：,.!?;:")
    action_terms = ("走", "冲", "看", "打", "退", "握", "attack", "move", "turn", "run")
    interior_terms = ("想", "觉得", "意识到", "心", "fear", "realize", "remember", "wonder")
    narrative_terms = ("说", "道", "看见", "听见", "night", "city", "room")
    return {
        "sentence_count": len(sentences),
        "paragraph_count": len(paragraphs),
        "sentence_length_distribution": distribution(sentence_lengths),
        "paragraph_length_distribution": distribution(paragraph_lengths),
        "avg_sentence_chars": average(sentence_lengths),
        "avg_paragraph_chars": average(paragraph_lengths),
        "dialogue_ratio": expression["dialogue_char_ratio"],
        "dialogue_char_ratio": expression["dialogue_char_ratio"],
        "dialogue_mark_density": round(dialogue_marks / max(1, len(compact)), 6),
        "punctuation_density": round(punctuation / max(1, len(compact)), 6),
        "repeated_phrases": repeated_phrases(compact),
        "perspective_stability": perspective_stability(compact),
        "narrative_action_interior_ratio": {
            "narrative": term_density(compact, narrative_terms),
            "action": term_density(compact, action_terms),
            "interior": term_density(compact, interior_terms),
        },
    }


def distribution(values: list[int]) -> dict[str, float]:
    if not values:
        return {"min": 0, "p50": 0, "p90": 0, "max": 0}
    ordered = sorted(values)
    return {
        "min": float(ordered[0]),
        "p50": float(percentile(ordered, 0.5)),
        "p90": float(percentile(ordered, 0.9)),
        "max": float(ordered[-1]),
    }


def percentile(values: list[int], ratio: float) -> int:
    if not values:
        return 0
    index = min(len(values) - 1, max(0, int(round((len(values) - 1) * ratio))))
    return values[index]


def average(values: list[int]) -> float:
    return round(sum(values) / max(1, len(values)), 3)


def repeated_phrases(text: str, *, size: int = 4, limit: int = 10) -> list[dict[str, Any]]:
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+", text.lower())
    counts: dict[str, int] = {}
    for index in range(0, max(0, len(tokens) - size + 1)):
        phrase = "".join(tokens[index : index + size])
        if len(phrase.strip()) < size:
            continue
        counts[phrase] = counts.get(phrase, 0) + 1
    repeated = [{"phrase": phrase, "count": count} for phrase, count in counts.items() if count > 1]
    repeated.sort(key=lambda item: (-int(item["count"]), str(item["phrase"])))
    return repeated[:limit]


def perspective_stability(text: str) -> dict[str, Any]:
    first = sum(text.count(item) for item in ("我", "我们", "my ", " i "))
    second = sum(text.count(item) for item in ("你", "你们", "you "))
    third = sum(text.count(item) for item in ("他", "她", "他们", "她们", "he ", "she ", "they "))
    total = max(1, first + second + third)
    dominant = max((first, "first"), (second, "second"), (third, "third"))[1]
    return {
        "dominant": dominant,
        "first_ratio": round(first / total, 4),
        "second_ratio": round(second / total, 4),
        "third_ratio": round(third / total, 4),
    }


def term_density(text: str, terms: tuple[str, ...]) -> float:
    lowered = text.lower()
    hits = sum(lowered.count(term.lower()) for term in terms)
    return round(hits / max(1, len(text)), 6)


def first_evidence(text: str, needles: tuple[str, ...]) -> str:
    lowered = text.lower()
    for needle in needles:
        index = lowered.find(needle.lower())
        if index >= 0:
            start = max(0, index - 40)
            end = min(len(text), index + len(needle) + 60)
            return text[start:end].replace("\n", " ").strip()
    return ""


def find_final_chapter(root: Path, chapter_number: int) -> Path | None:
    final_dir = root / "40_manuscript" / "final"
    for name in (f"ch{chapter_number:03d}.md", f"chapter_{chapter_number:03d}.md", f"{chapter_number}.md", f"ch{chapter_number:03d}.txt"):
        path = final_dir / name
        if path.exists():
            return path
    return None


def resolve_under_root(root: Path, file_path: str | Path) -> Path:
    path = Path(file_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def parse_chapter_number(path: Path) -> int | None:
    match = re.search(r"(?:ch|chapter[_-]?)0*(\d{1,5})", path.stem, re.IGNORECASE)
    return int(match.group(1)) if match else None


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(item) for item in value.values() if str(item).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


def normalize_records(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "records", "threads", "anchors", "data"):
            if isinstance(value.get(key), list):
                return value[key]
        return list(value.values())
    return []


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def trim_text(text: str, max_chars: int) -> str:
    compact = text.strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")


def read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except json.JSONDecodeError:
        return default


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
