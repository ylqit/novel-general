"""Agent task manifest protocol for host-agent creative work."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import json
import re

from longform_engine.storage import atomic_write_text


AGENT_TASK_SCHEMA_VERSION = 1
AGENT_TASK_STATUSES = (
    "awaiting_agent",
    "submitted",
    "validated",
    "invalid",
    "applied",
    "superseded",
    "rolled_back",
)
HARD_BOUNDARIES = (
    "no final",
    "no rag",
    "no graph direct",
    "no sqlite direct",
)


@dataclass(frozen=True)
class AgentTaskManifest:
    """Stable task contract consumed by Codex, Claude, GUI, and API surfaces."""

    schema_version: int
    task_id: str
    task_type: str
    chapter_number: int
    input_files: tuple[str, ...]
    allowed_output_paths: tuple[str, ...]
    output_schema: str
    validate_command: str
    apply_command: str
    failure_next_command: str
    hard_boundaries: tuple[str, ...]
    status: str
    created_at: str


@dataclass(frozen=True)
class ManifestValidationResult:
    """Validation result for an AgentTaskManifest contract check."""

    ok: bool
    task_id: str
    task_type: str
    strict: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AgentTaskLifecycleResult:
    """Result returned after updating Agent task lifecycle state."""

    task_id: str
    from_status: str
    to_status: str
    event_file: str


TASK_CONTRACTS: dict[str, dict[str, tuple[str, ...]]] = {
    "chapter_write": {
        "schemas": ("markdown_chapter_only",),
        "output_prefixes": ("50_workbench/agent_drafts/",),
        "validate_prefixes": ("longform-engine draft submit ",),
        "apply_prefixes": ("longform-engine chapter finalize ",),
        "failure_prefixes": ("longform-engine repair-chapter ",),
    },
    "repair": {
        "schemas": ("markdown_repair_candidate",),
        "output_prefixes": ("50_workbench/repair_candidates/",),
        "validate_prefixes": ("longform-engine draft submit ",),
        "apply_prefixes": ("longform-engine chapter finalize ",),
        "failure_prefixes": ("longform-engine repair-chapter ", "longform-engine editorial need-human "),
    },
    "humanize": {
        "schemas": ("markdown_humanized_candidate",),
        "output_prefixes": ("50_workbench/repair_candidates/",),
        "validate_prefixes": ("longform-engine creative humanize-check ",),
        "apply_prefixes": ("longform-engine draft submit ",),
        "failure_prefixes": ("longform-engine creative humanize-task ",),
    },
    "content_expand": {
        "schemas": ("markdown_expanded_candidate",),
        "output_prefixes": ("50_workbench/repair_candidates/",),
        "validate_prefixes": ("longform-engine creative expand-check ",),
        "apply_prefixes": ("longform-engine draft submit ",),
        "failure_prefixes": ("longform-engine creative expand-task ",),
    },
    "graph_extract": {
        "schemas": ("semantic_graph_update_v1",),
        "output_prefixes": ("50_workbench/graph_updates/",),
        "validate_prefixes": ("longform-engine graph semantic-validate ",),
        "apply_prefixes": ("longform-engine graph semantic-apply ",),
        "failure_prefixes": ("longform-engine graph semantic-task ",),
    },
    "memory_extract": {
        "schemas": ("semantic_memory_v1",),
        "output_prefixes": ("50_workbench/memory_tasks/",),
        "validate_prefixes": ("longform-engine memory semantic-validate ",),
        "apply_prefixes": ("longform-engine memory semantic-apply ",),
        "failure_prefixes": ("longform-engine memory semantic-task ",),
    },
    "character_memory": {
        "schemas": ("character_memory_cards_v1",),
        "output_prefixes": ("50_workbench/memory_tasks/",),
        "validate_prefixes": ("longform-engine memory character-validate ",),
        "apply_prefixes": ("longform-engine memory character-apply ",),
        "failure_prefixes": ("longform-engine memory character-task ",),
    },
    "editorial_review": {
        "schemas": ("editorial_role_review_v1",),
        "output_prefixes": ("50_workbench/editorial_reviews/results/",),
        "validate_prefixes": ("longform-engine editorial submit-review ",),
        "apply_prefixes": ("longform-engine editorial aggregate ",),
        "failure_prefixes": ("longform-engine editorial need-human ",),
    },
    "pacing_review": {
        "schemas": ("semantic_pacing_result_v1",),
        "output_prefixes": ("50_workbench/gate_artifacts/",),
        "validate_prefixes": ("longform-engine pacing semantic-validate ",),
        "apply_prefixes": ("longform-engine pacing semantic-apply ",),
        "failure_prefixes": ("longform-engine pacing semantic-task ",),
    },
}

CANONICAL_OUTPUT_PREFIXES = (
    "40_manuscript/final/",
    "60_rag/",
    "30_state/tcs/",
    "70_runtime/db/",
)
CANONICAL_OUTPUT_FILES = (
    "30_state/story_graph.json",
)


def build_manifest(
    root: Path,
    *,
    task_type: str,
    chapter_number: int,
    input_files: Iterable[str | Path],
    allowed_output_paths: Iterable[str | Path],
    output_schema: str,
    validate_command: str,
    apply_command: str = "",
    failure_next_command: str = "",
    status: str = "awaiting_agent",
    task_id: str | None = None,
    hard_boundaries: Iterable[str] = HARD_BOUNDARIES,
) -> dict[str, Any]:
    """Create an AgentTaskManifest v1 payload with project-relative paths."""

    if chapter_number <= 0:
        raise ValueError("chapter_number must be positive.")
    normalized_type = normalize_token(task_type)
    normalized_status = normalize_status(status)
    manifest_id = task_id or f"{normalized_type}:ch{chapter_number:03d}:v1"
    return {
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "task_id": manifest_id,
        "task_type": normalized_type,
        "chapter_number": chapter_number,
        "input_files": normalize_paths(root, input_files),
        "allowed_output_paths": normalize_paths(root, allowed_output_paths),
        "output_schema": output_schema,
        "validate_command": validate_command,
        "apply_command": apply_command,
        "failure_next_command": failure_next_command,
        "hard_boundaries": list(hard_boundaries),
        "status": normalized_status,
        "created_at": utc_now(),
    }


def write_manifest(root: Path, manifest: dict[str, Any], manifest_file: str | Path) -> str:
    """Persist a manifest and update the project-level read-only index."""

    path = resolve_under_root(root, manifest_file)
    validate_manifest_shape(manifest)
    atomic_write_text(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    register_manifest(root, manifest, path)
    return str(path)


def register_manifest(root: Path, manifest: dict[str, Any], manifest_file: Path) -> None:
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    if not isinstance(payload, dict) or payload.get("schema_version") != AGENT_TASK_SCHEMA_VERSION:
        payload = {"schema_version": AGENT_TASK_SCHEMA_VERSION, "tasks": []}
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
    rel_file = relative_path(root, manifest_file)
    existing = next(
        (
            item
            for item in tasks
            if isinstance(item, dict) and item.get("task_id") == manifest["task_id"]
        ),
        None,
    )
    new_status = normalize_status(str(manifest.get("status") or "awaiting_agent"))
    entry = {
        "task_id": manifest["task_id"],
        "task_type": manifest["task_type"],
        "chapter_number": manifest["chapter_number"],
        "status": new_status,
        "manifest_file": rel_file,
        "allowed_output_paths": list(manifest.get("allowed_output_paths") or []),
        "validate_command": manifest.get("validate_command", ""),
        "apply_command": manifest.get("apply_command", ""),
        "failure_next_command": manifest.get("failure_next_command", ""),
        "created_at": manifest.get("created_at", ""),
        "updated_at": utc_now(),
    }
    tasks = [item for item in tasks if not (isinstance(item, dict) and item.get("task_id") == manifest["task_id"])]
    tasks.append(entry)
    payload["tasks"] = sorted(
        tasks,
        key=lambda item: (
            int(item.get("chapter_number") or 0) if isinstance(item, dict) else 0,
            str(item.get("task_type") or "") if isinstance(item, dict) else "",
            str(item.get("task_id") or "") if isinstance(item, dict) else "",
        ),
    )
    payload["updated_at"] = utc_now()
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if existing is None:
        record_task_event(
            root,
            task_id=str(manifest["task_id"]),
            from_status="",
            to_status=new_status,
            command="agent-task create",
            artifact=rel_file,
            result=rel_file,
        )


def list_manifests(root: Path, *, chapter_number: int | None = None) -> list[dict[str, Any]]:
    """Return indexed manifest entries without mutating project state."""

    index = read_json(agent_task_index_file(root), default={})
    tasks = index.get("tasks") if isinstance(index, dict) else []
    if not isinstance(tasks, list):
        return []
    result = [dict(item) for item in tasks if isinstance(item, dict)]
    if chapter_number is not None:
        result = [item for item in result if int(item.get("chapter_number") or 0) == chapter_number]
    return result


def load_manifest(root: Path, task: str | Path) -> dict[str, Any]:
    """Load a manifest by task_id or project-relative/absolute manifest path."""

    task_text = str(task)
    for entry in list_manifests(root):
        if entry.get("task_id") == task_text:
            path = root / str(entry.get("manifest_file") or "")
            payload = read_json(path, default={})
            if isinstance(payload, dict):
                return payload
    path = resolve_under_root(root, task)
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        raise ValueError(f"Agent task manifest is not JSON object: {task}")
    return payload


def update_task_status(
    root: Path,
    task_id: str,
    *,
    to_status: str,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
) -> AgentTaskLifecycleResult | None:
    """Move an indexed Agent task to a lifecycle status and append an event."""

    normalized_status = normalize_status(to_status)
    index_path = agent_task_index_file(root)
    payload = read_json(index_path, default={})
    if not isinstance(payload, dict):
        return None
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    selected: dict[str, Any] | None = None
    for item in tasks:
        if isinstance(item, dict) and item.get("task_id") == task_id:
            selected = item
            break
    if selected is None:
        return None
    from_status = normalize_status(str(selected.get("status") or "awaiting_agent"))
    if from_status == normalized_status:
        return AgentTaskLifecycleResult(
            task_id=task_id,
            from_status=from_status,
            to_status=normalized_status,
            event_file=str(agent_task_events_file(root)),
        )
    selected["status"] = normalized_status
    selected["updated_at"] = utc_now()
    payload["updated_at"] = utc_now()
    atomic_write_text(index_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    manifest_file = selected.get("manifest_file")
    if manifest_file:
        update_manifest_status_file(root, str(manifest_file), normalized_status)
    event_file = record_task_event(
        root,
        task_id=task_id,
        from_status=from_status,
        to_status=normalized_status,
        command=command,
        artifact=artifact,
        result=result,
    )
    return AgentTaskLifecycleResult(
        task_id=task_id,
        from_status=from_status,
        to_status=normalized_status,
        event_file=str(event_file),
    )


def mark_tasks_for_output(
    root: Path,
    *,
    chapter_number: int,
    output_path: str | Path,
    to_status: str,
    command: str,
    result: str | Path = "",
    from_statuses: Iterable[str] | None = None,
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Update every indexed task whose allowed output path matches output_path."""

    output_text = relative_path(root, output_path)
    allowed_from = {normalize_status(item) for item in from_statuses} if from_statuses else None
    results: list[AgentTaskLifecycleResult] = []
    for task in list_manifests(root, chapter_number=chapter_number):
        if output_text not in [str(path).replace("\\", "/") for path in task.get("allowed_output_paths") or []]:
            continue
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if allowed_from is not None and current not in allowed_from:
            continue
        result_item = update_task_status(
            root,
            str(task.get("task_id") or ""),
            to_status=to_status,
            command=command,
            artifact=output_text,
            result=result,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def mark_tasks_for_chapter_type(
    root: Path,
    *,
    chapter_number: int,
    task_types: Iterable[str],
    to_status: str,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
    from_statuses: Iterable[str] | None = None,
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Update indexed tasks for a chapter/type set, useful after canonical apply."""

    allowed_types = {normalize_token(item) for item in task_types}
    allowed_from = {normalize_status(item) for item in from_statuses} if from_statuses else None
    results: list[AgentTaskLifecycleResult] = []
    for task in list_manifests(root, chapter_number=chapter_number):
        if normalize_token(str(task.get("task_type") or "")) not in allowed_types:
            continue
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if allowed_from is not None and current not in allowed_from:
            continue
        result_item = update_task_status(
            root,
            str(task.get("task_id") or ""),
            to_status=to_status,
            command=command,
            artifact=artifact,
            result=result,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def mark_existing_tasks_superseded(
    root: Path,
    *,
    chapter_number: int,
    task_type: str,
    replacement_task_id: str,
    command: str,
    artifact: str | Path,
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Mark older same-lane tasks as superseded when a replacement task is created."""

    results: list[AgentTaskLifecycleResult] = []
    normalized_type = normalize_token(task_type)
    for task in list_manifests(root, chapter_number=chapter_number):
        task_id = str(task.get("task_id") or "")
        if task_id == replacement_task_id:
            continue
        if normalize_token(str(task.get("task_type") or "")) != normalized_type:
            continue
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if current in {"applied", "rolled_back", "superseded"}:
            continue
        result_item = update_task_status(
            root,
            task_id,
            to_status="superseded",
            command=command,
            artifact=artifact,
            result=replacement_task_id,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def mark_tasks_rolled_back(
    root: Path,
    *,
    chapter_number: int,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
    from_statuses: Iterable[str] = ("applied", "validated", "submitted", "invalid"),
) -> tuple[AgentTaskLifecycleResult, ...]:
    """Mark tasks for a chapter as rolled_back after a project rollback command."""

    allowed_from = {normalize_status(item) for item in from_statuses}
    results: list[AgentTaskLifecycleResult] = []
    for task in list_manifests(root, chapter_number=chapter_number):
        current = normalize_status(str(task.get("status") or "awaiting_agent"))
        if current not in allowed_from:
            continue
        result_item = update_task_status(
            root,
            str(task.get("task_id") or ""),
            to_status="rolled_back",
            command=command,
            artifact=artifact,
            result=result,
        )
        if result_item is not None:
            results.append(result_item)
    return tuple(results)


def status_summary(root: Path, *, chapter_number: int | None = None) -> dict[str, Any]:
    tasks = list_manifests(root, chapter_number=chapter_number)
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status") or "unknown")
        task_type = str(task.get("task_type") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        by_type[task_type] = by_type.get(task_type, 0) + 1
    return {
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "chapter_number": chapter_number,
        "tasks": len(tasks),
        "by_status": by_status,
        "by_type": by_type,
        "event_file": relative_path(root, agent_task_events_file(root)),
        "items": tasks,
    }


def validate_manifest_shape(manifest: dict[str, Any]) -> None:
    required = (
        "schema_version",
        "task_id",
        "task_type",
        "chapter_number",
        "input_files",
        "allowed_output_paths",
        "output_schema",
        "validate_command",
        "apply_command",
        "failure_next_command",
        "hard_boundaries",
        "status",
        "created_at",
    )
    missing = [field for field in required if field not in manifest]
    if missing:
        raise ValueError(f"Agent task manifest missing fields: {', '.join(missing)}")
    if manifest.get("schema_version") != AGENT_TASK_SCHEMA_VERSION:
        raise ValueError("Agent task manifest schema_version must be 1.")
    if not str(manifest.get("task_id") or "").strip():
        raise ValueError("Agent task manifest task_id is required.")
    if not isinstance(manifest.get("input_files"), list):
        raise ValueError("Agent task manifest input_files must be a list.")
    if not isinstance(manifest.get("allowed_output_paths"), list):
        raise ValueError("Agent task manifest allowed_output_paths must be a list.")
    if not isinstance(manifest.get("hard_boundaries"), list):
        raise ValueError("Agent task manifest hard_boundaries must be a list.")
    normalize_status(str(manifest.get("status") or ""))


def validate_manifest_strict(root: Path, manifest: dict[str, Any], *, strict: bool = True) -> ManifestValidationResult:
    """Validate AgentTaskManifest v1 shape and semantic workflow contract."""

    errors: list[str] = []
    warnings: list[str] = []
    try:
        validate_manifest_shape(manifest)
    except ValueError as exc:
        errors.append(str(exc))
        return ManifestValidationResult(
            ok=False,
            task_id=str(manifest.get("task_id") or ""),
            task_type=str(manifest.get("task_type") or ""),
            strict=strict,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )
    if not strict:
        return ManifestValidationResult(
            ok=True,
            task_id=str(manifest.get("task_id") or ""),
            task_type=str(manifest.get("task_type") or ""),
            strict=False,
            errors=(),
            warnings=(),
        )

    task_id = str(manifest.get("task_id") or "").strip()
    task_type = normalize_token(str(manifest.get("task_type") or ""))
    chapter_number = manifest.get("chapter_number")
    if not isinstance(chapter_number, int) or chapter_number <= 0:
        errors.append("chapter_number must be a positive integer.")
    elif f"ch{chapter_number:03d}" not in task_id:
        errors.append(f"task_id must contain ch{chapter_number:03d}.")

    contract = TASK_CONTRACTS.get(task_type)
    if contract is None:
        errors.append(f"task_type must be one of: {', '.join(sorted(TASK_CONTRACTS))}.")
    else:
        validate_schema(manifest, contract, errors)
        validate_outputs(root, manifest, contract, errors)
        validate_command_field(manifest, "validate_command", contract["validate_prefixes"], errors)
        validate_command_field(manifest, "apply_command", contract["apply_prefixes"], errors)
        validate_command_field(manifest, "failure_next_command", contract["failure_prefixes"], errors)

    input_files = manifest.get("input_files") or []
    if not input_files:
        errors.append("input_files must contain at least one path.")
    for index, item in enumerate(input_files if isinstance(input_files, list) else []):
        path_text = normalize_manifest_path(root, item)
        if not path_text:
            errors.append(f"input_files[{index}] must be a non-empty path.")
        elif is_parent_escape(path_text):
            errors.append(f"input_files[{index}] must not escape the project root: {item}")

    boundaries = {str(item).strip().lower() for item in manifest.get("hard_boundaries") or []}
    for boundary in HARD_BOUNDARIES:
        if boundary not in boundaries:
            errors.append(f"hard_boundaries must include `{boundary}`.")
    if normalize_status(str(manifest.get("status") or "")) != "awaiting_agent":
        warnings.append("initial Agent task status is normally `awaiting_agent`.")

    return ManifestValidationResult(
        ok=not errors,
        task_id=task_id,
        task_type=task_type,
        strict=True,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )


def validate_schema(manifest: dict[str, Any], contract: dict[str, tuple[str, ...]], errors: list[str]) -> None:
    schema = str(manifest.get("output_schema") or "").strip()
    allowed = contract["schemas"]
    if schema not in allowed:
        errors.append(f"output_schema must be one of {', '.join(allowed)}; got `{schema}`.")


def validate_outputs(root: Path, manifest: dict[str, Any], contract: dict[str, tuple[str, ...]], errors: list[str]) -> None:
    outputs = manifest.get("allowed_output_paths") or []
    if not outputs:
        errors.append("allowed_output_paths must contain at least one path.")
        return
    for index, item in enumerate(outputs):
        path_text = normalize_manifest_path(root, item)
        if not path_text:
            errors.append(f"allowed_output_paths[{index}] must be a non-empty path.")
            continue
        if Path(str(item)).is_absolute():
            errors.append(f"allowed_output_paths[{index}] must be project-relative: {item}")
        if is_parent_escape(path_text):
            errors.append(f"allowed_output_paths[{index}] must not escape the project root: {item}")
        if is_canonical_output(path_text):
            errors.append(f"allowed_output_paths[{index}] must not point to canonical state: {path_text}")
        if not path_matches_prefix(path_text, contract["output_prefixes"]):
            errors.append(
                f"allowed_output_paths[{index}] must live under one of "
                f"{', '.join(contract['output_prefixes'])}; got `{path_text}`."
            )


def validate_command_field(
    manifest: dict[str, Any],
    field: str,
    allowed_prefixes: tuple[str, ...],
    errors: list[str],
) -> None:
    command = normalize_command(str(manifest.get(field) or ""))
    if not command:
        errors.append(f"{field} is required.")
        return
    if not any(command.startswith(prefix) for prefix in allowed_prefixes):
        errors.append(f"{field} must start with one of {', '.join(allowed_prefixes)}; got `{command}`.")


def normalize_manifest_path(root: Path, value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return text
    return path.as_posix()


def normalize_command(command: str) -> str:
    compact = re.sub(r"\s+", " ", command.strip())
    if compact.startswith("python -m longform_engine.cli "):
        return "longform-engine " + compact.removeprefix("python -m longform_engine.cli ")
    if compact.startswith("python3 -m longform_engine.cli "):
        return "longform-engine " + compact.removeprefix("python3 -m longform_engine.cli ")
    return compact


def is_parent_escape(path_text: str) -> bool:
    return ".." in Path(path_text).parts


def is_canonical_output(path_text: str) -> bool:
    normalized = path_text.strip().replace("\\", "/")
    return normalized in CANONICAL_OUTPUT_FILES or any(normalized.startswith(prefix) for prefix in CANONICAL_OUTPUT_PREFIXES)


def path_matches_prefix(path_text: str, prefixes: tuple[str, ...]) -> bool:
    normalized = path_text.strip().replace("\\", "/")
    return any(normalized.startswith(prefix) for prefix in prefixes)


def agent_task_index_file(root: Path) -> Path:
    return root / "50_workbench" / "agent_tasks" / "agent_task_index.json"


def agent_task_events_file(root: Path) -> Path:
    return root / "50_workbench" / "agent_tasks" / "events.jsonl"


def update_manifest_status_file(root: Path, manifest_file: str | Path, status: str) -> None:
    path = resolve_under_root(root, manifest_file)
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        return
    payload["status"] = normalize_status(status)
    payload["updated_at"] = utc_now()
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def record_task_event(
    root: Path,
    *,
    task_id: str,
    from_status: str,
    to_status: str,
    command: str,
    artifact: str | Path = "",
    result: str | Path = "",
) -> Path:
    normalized_to = normalize_status(to_status)
    normalized_from = normalize_status(from_status) if str(from_status).strip() else ""
    path = agent_task_events_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "task_id": task_id,
        "from_status": normalized_from,
        "to_status": normalized_to,
        "command": command,
        "artifact": relative_path(root, artifact) if str(artifact).strip() else "",
        "result": relative_path(root, result) if str(result).strip() else "",
        "created_at": utc_now(),
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def normalize_status(value: str) -> str:
    status = normalize_token(value)
    if status not in AGENT_TASK_STATUSES:
        raise ValueError(f"Agent task status must be one of: {', '.join(AGENT_TASK_STATUSES)}.")
    return status


def normalize_paths(root: Path, paths: Iterable[str | Path]) -> list[str]:
    result: list[str] = []
    for raw in paths:
        if raw is None:
            continue
        path_text = str(raw).strip()
        if not path_text:
            continue
        result.append(relative_path(root, Path(path_text)))
    return dedupe(result)


def normalize_token(value: str) -> str:
    token = re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower().replace("-", "_"))
    return token.strip("_") or "agent_task"


def resolve_under_root(root: Path, file_path: str | Path) -> Path:
    path = Path(file_path)
    if not path.is_absolute():
        path = root / path
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path must live under project root: {file_path}") from exc
    return resolved


def relative_path(root: Path, path: str | Path) -> str:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return candidate.as_posix()


def read_json(path: Path, *, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
