"""Installed Skill lifecycle and environment diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from importlib import metadata as importlib_metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid
from typing import Any

from longform_engine import __version__
from longform_engine.config import ConfigError, load_project_config
from longform_engine.lengths import compile_length_forecast
from longform_engine.models import verify_models
from longform_engine.resources import load_resource_manifest, resource_integrity_bytes, resource_path, resource_root


INSTALL_SCHEMA = "longform_skill_install_v1"
STATUS_SCHEMA = "skill_install_status_v1"
DOCTOR_SCHEMA = "doctor_v1"
METADATA_NAME = ".longform-install.json"
TOOL_SPECS = {
    "codex": ("longform-novel-codex", ".codex", "LONGFORM_CODEX_SKILL_ROOT"),
    "claude-code": ("longform-novel-claude", ".claude", "LONGFORM_CLAUDE_SKILL_ROOT"),
}


def distribution_reinstall_command() -> str:
    return (
        'python -m pipx install --force '
        f'"longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v{__version__}"'
    )


def _pyproject_version() -> str | None:
    path = resource_root() / "pyproject.toml"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    return match.group(1) if match else None


def _installed_distribution_version() -> str | None:
    try:
        return importlib_metadata.version("longform-novel-engine")
    except importlib_metadata.PackageNotFoundError:
        return None


def distribution_version_payload(tool: str) -> dict[str, Any]:
    skill_versions = {
        name: inspect_skill(name).installed_version
        for name in selected_tools(tool)
    }
    versions = {
        "pyproject": _pyproject_version(),
        "module": __version__,
        "distribution_metadata": _installed_distribution_version(),
        "cli": __version__,
        "skills": skill_versions,
    }
    comparable = [versions["pyproject"], versions["module"], versions["distribution_metadata"], versions["cli"]]
    comparable.extend(skill_versions.values())
    mismatches = {
        name: value
        for name, value in {
            "pyproject": versions["pyproject"],
            "module": versions["module"],
            "distribution_metadata": versions["distribution_metadata"],
            "cli": versions["cli"],
            **{f"skill_{name}": value for name, value in skill_versions.items()},
        }.items()
        if value != __version__
    }
    return {
        "schema": "distribution_version_v1",
        "expected_version": __version__,
        "versions": versions,
        "ok": bool(comparable) and not mismatches,
        "mismatches": mismatches,
        "next_command": "" if not mismatches else distribution_reinstall_command(),
    }


@dataclass(frozen=True)
class SkillStatus:
    tool: str
    skill_name: str
    path: str
    state: str
    owned: bool
    installed_version: str | None
    expected_version: str
    installed_hash: str | None
    expected_hash: str
    references_ok: bool
    next_command: str


def selected_tools(tool: str) -> tuple[str, ...]:
    if tool == "all":
        return tuple(TOOL_SPECS)
    if tool not in TOOL_SPECS:
        raise ValueError("tool must be one of: codex, claude-code, all")
    return (tool,)


def skill_root(tool: str) -> Path:
    _, default_dir, env_name = TOOL_SPECS[tool]
    override = os.environ.get(env_name)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / default_dir / "skills").resolve()


def skill_source(tool: str) -> Path:
    skill_name, _, _ = TOOL_SPECS[tool]
    return resource_path(skill_name)


def tree_hash(root: Path) -> str:
    digest = sha256()
    paths = (candidate for candidate in root.rglob("*") if candidate.is_file())
    for path in sorted(paths, key=lambda item: item.relative_to(root).as_posix().casefold()):
        if path.name == METADATA_NAME:
            continue
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(resource_integrity_bytes(path))
        digest.update(b"\0")
    return digest.hexdigest()


def _metadata(target: Path) -> dict[str, Any] | None:
    path = target / METADATA_NAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _owned_metadata(target: Path, tool: str) -> dict[str, Any] | None:
    skill_name, _, _ = TOOL_SPECS[tool]
    payload = _metadata(target)
    if (
        payload
        and payload.get("schema") == INSTALL_SCHEMA
        and payload.get("tool") == tool
        and payload.get("skill_name") == skill_name
    ):
        return payload
    return None


def _references_ok(target: Path) -> bool:
    skill_file = target / "SKILL.md"
    references = target / "references"
    if not skill_file.is_file() or not references.is_dir():
        return False
    text = skill_file.read_text(encoding="utf-8")
    if "../shared" in text:
        return False
    return all((references / name).is_file() for name in (
        "artifact_reporting.md",
        "command_protocol.md",
        "creative_operator_protocol.md",
        "iron_laws.md",
        "workflow_mapping.md",
    ))


def _safe_target(root: Path, target: Path, expected_name: str) -> None:
    root = root.resolve()
    target = target.resolve()
    home = Path.home().resolve()
    disk_root = Path(root.anchor).resolve()
    resource = resource_root().resolve()
    forbidden_roots = {disk_root, home, resource}
    forbidden_targets = {Path(target.anchor).resolve(), home, root, resource}
    if root in forbidden_roots or target in forbidden_targets or target.name != expected_name or target.parent != root:
        raise ValueError(f"Refusing unsafe Skill target: {target}")


def inspect_skill(tool: str) -> SkillStatus:
    skill_name, _, _ = TOOL_SPECS[tool]
    root = skill_root(tool)
    target = root / skill_name
    source = skill_source(tool)
    expected_hash = tree_hash(source)
    metadata = _owned_metadata(target, tool) if target.exists() else None
    installed_hash = tree_hash(target) if target.is_dir() else None
    references_ok = _references_ok(target) if target.is_dir() else False

    if not target.exists():
        state = "missing"
        next_command = f"longform-engine skills install --tool {tool}"
    elif metadata is None:
        state = "legacy"
        next_command = f"longform-engine skills install --tool {tool} --force"
    elif installed_hash == expected_hash and metadata.get("engine_version") == __version__ and references_ok:
        state = "current"
        next_command = "longform-engine doctor --tool " + tool
    else:
        state = "outdated"
        next_command = f"longform-engine skills update --tool {tool}"

    return SkillStatus(
        tool=tool,
        skill_name=skill_name,
        path=str(target),
        state=state,
        owned=metadata is not None,
        installed_version=str(metadata.get("engine_version")) if metadata else None,
        expected_version=__version__,
        installed_hash=installed_hash,
        expected_hash=expected_hash,
        references_ok=references_ok,
        next_command=next_command,
    )


def skill_status_payload(tool: str) -> dict[str, Any]:
    return {
        "schema": STATUS_SCHEMA,
        "engine_version": __version__,
        "requested_tool": tool,
        "results": [asdict(inspect_skill(name)) for name in selected_tools(tool)],
    }


def _replace_skill(tool: str, *, allow_legacy: bool, require_owned: bool) -> SkillStatus:
    skill_name, _, _ = TOOL_SPECS[tool]
    root = skill_root(tool)
    root.mkdir(parents=True, exist_ok=True)
    target = root / skill_name
    _safe_target(root, target, skill_name)

    existing_metadata = _owned_metadata(target, tool) if target.exists() else None
    if target.exists() and require_owned and existing_metadata is None:
        raise ValueError(f"Refusing to update unowned or legacy Skill: {target}")
    if target.exists() and existing_metadata is None and not allow_legacy:
        raise ValueError(f"Legacy Skill exists; rerun with --force after reviewing it: {target}")

    source = skill_source(tool)
    staging = root / f".{skill_name}.staging-{uuid.uuid4().hex}"
    backup = root / f".{skill_name}.backup-{uuid.uuid4().hex}"
    _safe_target(root, staging.with_name(skill_name), skill_name)
    try:
        shutil.copytree(source, staging)
        if not _references_ok(staging):
            raise ValueError(f"Bundled Skill references are incomplete: {source}")
        installed_hash = tree_hash(staging)
        metadata = {
            "schema": INSTALL_SCHEMA,
            "engine_version": __version__,
            "tool": tool,
            "skill_name": skill_name,
            "tree_hash": installed_hash,
            "source": "bundled-wheel-resource",
        }
        (staging / METADATA_NAME).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        if target.exists():
            target.replace(backup)
        staging.replace(target)
        if backup.exists():
            shutil.rmtree(backup)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    return inspect_skill(tool)


def install_skills(tool: str, *, force: bool = False) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for name in selected_tools(tool):
        current = inspect_skill(name)
        if current.state == "current":
            results.append(asdict(current))
            continue
        results.append(asdict(_replace_skill(name, allow_legacy=force, require_owned=False)))
    return {"schema": STATUS_SCHEMA, "engine_version": __version__, "requested_tool": tool, "results": results}


def update_skills(tool: str) -> dict[str, Any]:
    results = [asdict(_replace_skill(name, allow_legacy=False, require_owned=True)) for name in selected_tools(tool)]
    return {"schema": STATUS_SCHEMA, "engine_version": __version__, "requested_tool": tool, "results": results}


def uninstall_skills(tool: str, *, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("Uninstall requires --yes.")
    results: list[dict[str, Any]] = []
    for name in selected_tools(tool):
        skill_name, _, _ = TOOL_SPECS[name]
        root = skill_root(name)
        target = root / skill_name
        _safe_target(root, target, skill_name)
        if target.exists():
            if _owned_metadata(target, name) is None:
                raise ValueError(f"Refusing to uninstall unowned or legacy Skill: {target}")
            shutil.rmtree(target)
        results.append(asdict(inspect_skill(name)))
    return {"schema": STATUS_SCHEMA, "engine_version": __version__, "requested_tool": tool, "results": results}


def _check(name: str, ok: bool, detail: str, next_command: str = "") -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail, "next_command": next_command}


def _verify_bundled_resources() -> tuple[bool, str]:
    manifest = load_resource_manifest()
    failures: list[str] = []
    root = resource_root()
    for entry in manifest.get("assets", []):
        if not isinstance(entry, dict):
            failures.append("invalid manifest entry")
            continue
        path = root / str(entry.get("path", ""))
        if not path.is_file() or sha256(resource_integrity_bytes(path)).hexdigest() != entry.get("sha256"):
            failures.append(str(entry.get("path", "")))
    return not failures, "all hashes match" if not failures else "mismatch: " + ", ".join(failures[:5])


def doctor_payload(tool: str, *, project: str | None = None) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_check("python", sys.version_info >= (3, 10), sys.version.split()[0], "Install Python 3.10 or newer."))
    distribution_versions = distribution_version_payload(tool)
    version_detail = json.dumps(distribution_versions["versions"], ensure_ascii=False, sort_keys=True)
    checks.append(
        _check(
            "distribution_version",
            bool(distribution_versions["ok"]),
            version_detail,
            str(distribution_versions["next_command"]),
        )
    )
    try:
        resources_ok, detail = _verify_bundled_resources()
    except Exception as exc:
        resources_ok, detail = False, str(exc)
    checks.append(_check("bundled_resources", resources_ok, detail, "Reinstall longform-novel-engine."))

    semantic_modules = ("huggingface_hub", "hnswlib", "sentence_transformers")
    missing_modules = [name for name in semantic_modules if importlib.util.find_spec(name) is None]
    checks.append(
        _check(
            "semantic_dependencies",
            not missing_modules,
            "installed" if not missing_modules else "missing: " + ", ".join(missing_modules),
            distribution_reinstall_command(),
        )
    )

    for name in selected_tools(tool):
        status = inspect_skill(name)
        checks.append(_check(f"skill_{name}", status.state == "current", status.state, status.next_command))

    if project:
        try:
            config = load_project_config(Path(project).expanduser().resolve())
            checks.append(_check("project_config", True, str(config.path or project)))
            length_forecast = compile_length_forecast(config.data["length"])
            checks.append(
                _check(
                    "length_support",
                    True,
                    (
                        f"status={length_forecast.support_status}; "
                        f"target_content_characters={length_forecast.target_total_characters}; "
                        f"forecast_chapters={length_forecast.estimated_chapters}"
                    ),
                    "",
                )
            )
            model_result = verify_models(config)
            model_ok = bool(model_result.provider_ready)
            checks.append(
                _check(
                    "semantic_models",
                    model_ok,
                    f"status={model_result.status}; download_required={model_result.download_required}",
                    f"longform-engine models install {project} --profile {model_result.profile} --download",
                )
            )
            from longform_engine.artifacts import artifact_status

            artifact_result = artifact_status(config)
            transaction_ok = artifact_result.pending_transactions == 0
            transaction_detail = (
                f"pending={artifact_result.pending_transactions}; "
                f"reclaimable_snapshots={artifact_result.committed_snapshot_dirs}; "
                f"reclaimable_bytes={artifact_result.reclaimable_snapshot_bytes}; "
                f"retained_failure_snapshots={artifact_result.retained_failure_snapshots}"
            )
            checks.append(
                _check(
                    "transaction_lifecycle",
                    transaction_ok,
                    transaction_detail,
                    f"longform-engine artifacts status {project} --json",
                )
            )
            from longform_engine.vectorstore import healthcheck as vector_healthcheck

            vector_result = vector_healthcheck(config)
            vector_detail = (
                f"backend={vector_result.backend}; active={vector_result.record_count}; "
                f"stale={vector_result.stale_count}; {vector_result.message}"
            )
            if vector_result.recommendation:
                vector_detail += f"; recommendation={vector_result.recommendation}"
            checks.append(
                _check(
                    "vector_store",
                    vector_result.ok,
                    vector_detail,
                    vector_result.recommendation
                    or f"longform-engine vector-store rebuild {project}",
                )
            )
        except (ConfigError, ValueError, OSError) as exc:
            checks.append(_check("project_config", False, str(exc), f"longform-engine validate-config {project}"))
    else:
        checks.append(_check("project_config", True, "not requested"))

    return {
        "schema": DOCTOR_SCHEMA,
        "engine_version": __version__,
        "requested_tool": tool,
        "project": project,
        "distribution_version": distribution_versions,
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
    }


def render_status(payload: dict[str, Any]) -> str:
    lines = [f"longform-engine {payload['engine_version']}"]
    for result in payload["results"]:
        lines.append(f"{result['tool']}: {result['state']} ({result['path']})")
        lines.append(f"  next: {result['next_command']}")
    return "\n".join(lines)


def render_doctor(payload: dict[str, Any]) -> str:
    lines = [f"Doctor: {'PASS' if payload['ok'] else 'NEEDS ATTENTION'}"]
    for check in payload["checks"]:
        lines.append(f"[{'ok' if check['ok'] else '!!'}] {check['name']}: {check['detail']}")
        if not check["ok"] and check["next_command"]:
            lines.append(f"  next: {check['next_command']}")
    return "\n".join(lines)
