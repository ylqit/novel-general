"""Integrity binding for the compiled, author-facing chapter Story Brief."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable

from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.chapter_contract import load_verified_chapter_contract


BASIS_SCHEMA = "chapter_story_brief_basis_v3"
STORY_BRIEF_SCHEMA = "chapter_story_brief_v5"
WRITING_TASK_SCHEMA = "chapter_writing_task_v7"
RENDERER_VERSION = "chapter_story_brief_renderer_v5"


class StoryBriefBindingError(ValueError):
    """Raised when a Story Brief no longer matches its compilation basis."""


def json_sha256(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def build_story_brief_basis(
    *,
    chapter_number: int,
    chapter_contract_sha256: str,
    human_chapter_intent_sha256: str,
    rolling_window_sha256: str,
    plot_node_table_sha256: str,
    semantic_obligation_ledger_sha256: str,
    canonical_projection: Any,
    character_voice_projection: Any,
    author_voice_projection: Any,
    structure_history_projection: Any,
    quality_contract_projection: Any,
    source_files: Iterable[dict[str, str]],
) -> dict[str, Any]:
    """Build the deterministic digest of every projection that can alter author Markdown."""

    payload: dict[str, Any] = {
        "schema": BASIS_SCHEMA,
        "chapter_number": chapter_number,
        "renderer_version": RENDERER_VERSION,
        "components": {
            "chapter_contract_sha256": chapter_contract_sha256,
            "human_chapter_intent_sha256": human_chapter_intent_sha256,
            "rolling_window_sha256": rolling_window_sha256,
            "plot_node_table_sha256": plot_node_table_sha256,
            "semantic_obligation_ledger_sha256": semantic_obligation_ledger_sha256,
            "canonical_projection_sha256": json_sha256(canonical_projection),
            "character_voice_projection_sha256": json_sha256(character_voice_projection),
            "author_voice_projection_sha256": json_sha256(author_voice_projection),
            "structure_history_projection_sha256": json_sha256(structure_history_projection),
            "quality_contract_projection_sha256": json_sha256(quality_contract_projection),
        },
        "source_files": sorted(
            (
                {"path": str(item["path"]), "sha256": str(item["sha256"])}
                for item in source_files
                if str(item.get("path") or "") and re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256") or ""))
            ),
            key=lambda item: item["path"],
        ),
    }
    payload["basis_sha256"] = json_sha256(payload)
    return payload


def story_brief_paths(root: Path, chapter_number: int) -> dict[str, Path]:
    directory = root / "50_workbench" / "writing_tasks"
    token = f"ch{chapter_number:03d}"
    return {
        "task": directory / f"{token}.json",
        "markdown": directory / f"{token}.md",
        "basis": directory / f"{token}.basis.json",
        "manifest": directory / f"{token}.agent_task.json",
    }


def load_current_story_brief_binding(root: Path, chapter_number: int) -> dict[str, Any]:
    """Validate the current task, basis, Markdown, sources, contract, and manifest bytes."""

    paths = story_brief_paths(root, chapter_number)
    task = _read_json(paths["task"])
    basis = _read_json(paths["basis"])
    manifest = _read_json(paths["manifest"])
    if not isinstance(task, dict) or task.get("schema") != WRITING_TASK_SCHEMA:
        raise StoryBriefBindingError(
            "story_brief_incompatible: v0.9 writing tasks are rejected; create a v0.10 project "
            "and manually import authoritative Bible and outline material"
        )
    if task.get("status") != "task_ready":
        raise StoryBriefBindingError("story_brief_stale: writing task status is not task_ready")
    if not isinstance(task.get("story_brief"), dict) or task["story_brief"].get("schema") != STORY_BRIEF_SCHEMA:
        raise StoryBriefBindingError("story_brief_incompatible: author brief schema is not current")
    if not isinstance(basis, dict) or basis.get("schema") != BASIS_SCHEMA:
        raise StoryBriefBindingError("story_brief_basis_missing_or_incompatible")
    if basis.get("chapter_number") != chapter_number:
        raise StoryBriefBindingError("story_brief_basis_chapter_mismatch")
    expected_basis = dict(basis)
    recorded_basis_hash = str(expected_basis.pop("basis_sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", recorded_basis_hash) or json_sha256(expected_basis) != recorded_basis_hash:
        raise StoryBriefBindingError("story_brief_basis_sha256_drift")
    if basis.get("renderer_version") != RENDERER_VERSION:
        raise StoryBriefBindingError("story_brief_renderer_version_stale")
    binding = task.get("story_brief_basis")
    if not isinstance(binding, dict) or set(binding) != {"path", "sha256", "file_sha256"}:
        raise StoryBriefBindingError("story_brief_basis_binding_invalid")
    if str(binding.get("path") or "") != paths["basis"].relative_to(root).as_posix():
        raise StoryBriefBindingError("story_brief_basis_path_mismatch")
    if str(binding.get("sha256") or "") != recorded_basis_hash:
        raise StoryBriefBindingError("story_brief_basis_sha256_stale")
    if str(binding.get("file_sha256") or "") != _file_sha256(paths["basis"]):
        raise StoryBriefBindingError("story_brief_basis_file_sha256_stale")
    markdown_hash = _file_sha256(paths["markdown"])
    if not markdown_hash or task.get("story_brief_markdown_sha256") != markdown_hash:
        raise StoryBriefBindingError("story_brief_markdown_sha256_stale")
    if not isinstance(manifest, dict):
        raise StoryBriefBindingError("story_brief_manifest_missing")
    try:
        active_manifest = load_manifest(root, paths["manifest"])
    except (OSError, ValueError) as exc:
        raise StoryBriefBindingError("story_brief_manifest_unregistered") from exc
    manifest_status = str(active_manifest.get("status") or "")
    if manifest_status not in {
        "awaiting_agent",
        "submitted",
        "validated",
        "approved",
        "applied",
    }:
        submission = _read_json(
            root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.submission.json"
        )
        human_revision: dict[str, Any] = {}
        submission_story_brief: dict[str, Any] = {}
        if isinstance(submission, dict):
            revision_value = submission.get("human_author_revision")
            if isinstance(revision_value, dict):
                human_revision = revision_value
            story_brief_value = submission.get("story_brief")
            if isinstance(story_brief_value, dict):
                submission_story_brief = story_brief_value
        submission_binding_current = (
            submission_story_brief.get("schema")
            == "chapter_story_brief_submission_binding_v1"
            and submission_story_brief.get("chapter_contract_sha256")
            == task.get("chapter_contract_hash")
            and submission_story_brief.get("story_brief_basis_sha256")
            == recorded_basis_hash
            and submission_story_brief.get("story_brief_markdown_sha256")
            == markdown_hash
            and submission_story_brief.get("manifest_sha256")
            == _file_sha256(paths["manifest"])
        )
        human_binding_current = (
            human_revision.get("schema")
            == "human_author_revision_submission_binding_v4"
            and human_revision.get("story_brief_basis_sha256") == recorded_basis_hash
        )
        if not (
            manifest_status == "superseded"
            and (submission_binding_current or human_binding_current)
        ):
            raise StoryBriefBindingError("story_brief_manifest_superseded_or_stale")
    if not validate_manifest_strict(root, active_manifest, strict=True).ok:
        raise StoryBriefBindingError("story_brief_manifest_integrity_stale")
    inputs = ((manifest.get("io") or {}).get("inputs") or []) if isinstance(manifest.get("io"), dict) else []
    markdown_relative = paths["markdown"].relative_to(root).as_posix()
    markdown_input = next(
        (
            item
            for item in inputs
            if isinstance(item, dict) and str(item.get("path") or "") == markdown_relative
        ),
        None,
    )
    if not isinstance(markdown_input, dict) or markdown_input.get("sha256") != markdown_hash:
        raise StoryBriefBindingError("story_brief_manifest_input_stale")
    contract, contract_hash = load_verified_chapter_contract(root, chapter_number)
    components_value = basis.get("components")
    components: dict[str, Any] = components_value if isinstance(components_value, dict) else {}
    if task.get("chapter_contract_hash") != contract_hash or components.get("chapter_contract_sha256") != contract_hash:
        raise StoryBriefBindingError("story_brief_contract_sha256_stale")
    from longform_engine.human_chapter_intent import require_current_human_chapter_intent

    intent = require_current_human_chapter_intent(root, chapter_number)
    if (
        task.get("human_chapter_intent_sha256") != intent["sha256"]
        or components.get("human_chapter_intent_sha256") != intent["sha256"]
    ):
        raise StoryBriefBindingError("story_brief_human_chapter_intent_sha256_stale")
    current_sources = {
        "rolling_window_sha256": root / "20_outline" / "rolling_window.json",
        "plot_node_table_sha256": root / "20_outline" / "plot_nodes" / f"ch{chapter_number:03d}.json",
        "semantic_obligation_ledger_sha256": root / "30_state" / "semantic_obligations.json",
    }
    for component, source in current_sources.items():
        if not source.is_file() or components.get(component) != _file_sha256(source):
            raise StoryBriefBindingError(f"story_brief_{component}_stale")
    for item in basis.get("source_files") or []:
        if not isinstance(item, dict):
            raise StoryBriefBindingError("story_brief_basis_source_binding_invalid")
        source = _resolve_project_path(root, str(item.get("path") or ""))
        if not source.is_file() or _file_sha256(source) != str(item.get("sha256") or ""):
            raise StoryBriefBindingError(
                "story_brief_basis_source_stale:" + str(item.get("path") or "")
            )
    return {
        "schema": "chapter_story_brief_binding_v3",
        "chapter_number": chapter_number,
        "chapter_contract_sha256": contract_hash,
        "human_chapter_intent_sha256": intent["sha256"],
        "story_brief_basis_sha256": recorded_basis_hash,
        "story_brief_basis_file": paths["basis"].relative_to(root).as_posix(),
        "story_brief_basis_file_sha256": _file_sha256(paths["basis"]),
        "story_brief_markdown_file": markdown_relative,
        "story_brief_markdown_sha256": markdown_hash,
        "manifest_file": paths["manifest"].relative_to(root).as_posix(),
        "manifest_sha256": _file_sha256(paths["manifest"]),
    }


def story_brief_status(root: Path, chapter_number: int) -> dict[str, Any]:
    try:
        binding = load_current_story_brief_binding(root, chapter_number)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        reason = str(exc)
        try:
            load_verified_chapter_contract(root, chapter_number)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            contract_current = False
        else:
            contract_current = True
        try:
            from longform_engine.human_chapter_intent import (
                require_current_human_chapter_intent,
            )

            require_current_human_chapter_intent(root, chapter_number)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            intent_current = False
        else:
            intent_current = True
        basis_current = contract_current and intent_current and not any(
            marker in reason
            for marker in (
                "basis",
                "renderer",
                "source_stale",
                "writing task",
                "author brief schema",
                "human_chapter_intent",
            )
        )
        return {
            "chapter_number": chapter_number,
            "contract_current": contract_current,
            "human_chapter_intent_current": intent_current,
            "basis_current": basis_current,
            "manifest_current": False,
            "status": "stale",
            "reason": reason,
        }
    return {
        "chapter_number": chapter_number,
        "contract_current": True,
        "human_chapter_intent_current": True,
        "basis_current": True,
        "manifest_current": True,
        "status": "current",
        **binding,
    }


def source_file_bindings(root: Path, paths: Iterable[Path]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise StoryBriefBindingError(f"story brief source escapes project root: {path}") from exc
        if relative in seen or not resolved.is_file():
            continue
        seen.add(relative)
        records.append({"path": relative, "sha256": _file_sha256(resolved)})
    return records


def _resolve_project_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise StoryBriefBindingError("story_brief_basis_source_escapes_project") from exc
    return path


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


__all__ = [
    "BASIS_SCHEMA",
    "RENDERER_VERSION",
    "STORY_BRIEF_SCHEMA",
    "WRITING_TASK_SCHEMA",
    "StoryBriefBindingError",
    "build_story_brief_basis",
    "json_sha256",
    "load_current_story_brief_binding",
    "source_file_bindings",
    "story_brief_paths",
    "story_brief_status",
]
