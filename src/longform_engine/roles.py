"""Versioned, host-neutral Prompt role contracts for Agent tasks."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from longform_engine.resources import resource_root


ROLE_REGISTRY_SCHEMA = "agent_role_registry_v1"
ROLE_REGISTRY_PATH = Path("config/agent_roles/registry.json")
EMPTY_PROJECT_OVERLAY_HASH = sha256(b"").hexdigest()
ROLE_METADATA_FIELDS = (
    "role_id",
    "role_version",
    "role_prompt_hash",
    "independence_mode",
    "project_overlay_hash",
)
ROLE_PROMPT_HEADINGS = (
    "Identity",
    "Serves",
    "Single Mission",
    "Cognitive Lens",
    "Source Authority",
    "Creative Freedom",
    "Forbidden Actions",
    "Evidence Duty",
    "Output Contract",
    "Stop And Escalate",
    "Handoff",
    "Observable Self Check",
)
OUTPUT_MODES = frozenset(
    {"markdown_prose", "compact_review_json", "document_index_bundle", "strict_delta_json"}
)
INDEPENDENCE_MODES = frozenset({"author_context", "isolated_review", "cross_host_review"})
ROLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RoleRegistryError(ValueError):
    """Raised when role resources cannot produce an unambiguous task contract."""


@dataclass(frozen=True)
class RoleContract:
    role_id: str
    role_version: str
    prompt_path: str
    prompt_text: str
    prompt_hash: str
    output_mode: str
    independence_mode: str
    allowed_overlay_fields: tuple[str, ...]

    @property
    def identity(self) -> str:
        marker = "## Identity\n"
        if marker not in self.prompt_text:
            return self.role_id
        body = self.prompt_text.split(marker, 1)[1].split("\n## ", 1)[0].strip()
        title = self.prompt_text.splitlines()[0].removeprefix("# ").strip().capitalize()
        return f"{title}. {body}" if title else body

    def manifest_metadata(
        self,
        *,
        project_overlay_hash: str = EMPTY_PROJECT_OVERLAY_HASH,
    ) -> dict[str, str]:
        return {
            "role_id": self.role_id,
            "role_version": self.role_version,
            "role_prompt_hash": self.prompt_hash,
            "independence_mode": self.independence_mode,
            "project_overlay_hash": project_overlay_hash,
        }


@dataclass(frozen=True)
class RoleRegistry:
    registry_version: int
    roles: dict[str, RoleContract]
    task_role_map: dict[str, str]
    editorial_role_map: dict[str, str]
    allowed_overlay_fields: tuple[str, ...]

    def resolve(self, task_type: str, *, declared_role_id: str = "") -> RoleContract:
        normalized_task = normalize_id(task_type)
        normalized_declared = normalize_id(declared_role_id) if declared_role_id else ""
        if normalized_task == "editorial_review":
            if not normalized_declared:
                raise RoleRegistryError(
                    "editorial_review requires a declared specialized role_id; generic fallback is forbidden."
                )
            role_id = self.editorial_role_map.get(normalized_declared)
            if not role_id:
                allowed = ", ".join(sorted(self.editorial_role_map))
                raise RoleRegistryError(
                    f"Unknown editorial role_id `{declared_role_id}`; expected one of: {allowed}."
                )
        else:
            role_id = self.task_role_map.get(normalized_task)
            if not role_id:
                raise RoleRegistryError(f"No Prompt role is registered for task_type `{task_type}`.")
            if normalized_declared and normalized_declared != role_id:
                raise RoleRegistryError(
                    f"task_type `{task_type}` requires role_id `{role_id}`, got `{declared_role_id}`."
                )
        try:
            return self.roles[role_id]
        except KeyError as exc:
            raise RoleRegistryError(f"Registered role `{role_id}` has no role contract.") from exc


def load_role_registry(root: Path | None = None) -> RoleRegistry:
    """Load and fully validate the bundled role registry and Markdown contracts."""

    base = (root or resource_root()).resolve()
    return _load_role_registry_cached(str(base), role_resource_fingerprint(base))


@lru_cache(maxsize=16)
def _load_role_registry_cached(
    base_text: str,
    resource_fingerprint: tuple[tuple[str, int, int], ...],
) -> RoleRegistry:
    # resource_fingerprint is part of the cache key; the body reads the same immutable snapshot.
    del resource_fingerprint
    base = Path(base_text)
    registry_path = (base / ROLE_REGISTRY_PATH).resolve()
    if not registry_path.is_file():
        raise RoleRegistryError(f"Prompt role registry is missing: {registry_path}")
    try:
        payload = json.loads(
            registry_path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_json_keys,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RoleRegistryError) as exc:
        raise RoleRegistryError(f"Invalid Prompt role registry {registry_path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != ROLE_REGISTRY_SCHEMA:
        raise RoleRegistryError(f"Prompt role registry schema must be `{ROLE_REGISTRY_SCHEMA}`.")
    registry_version = payload.get("registry_version")
    if not isinstance(registry_version, int) or registry_version <= 0:
        raise RoleRegistryError("Prompt role registry_version must be a positive integer.")

    allowed_overlay_fields = validate_string_list(
        payload.get("allowed_overlay_fields"), field="allowed_overlay_fields"
    )
    task_role_map = validate_role_map(payload.get("task_role_map"), field="task_role_map")
    if "editorial_review" in task_role_map:
        raise RoleRegistryError(
            "editorial_review must use editorial_role_map and cannot have a generic task_role_map fallback."
        )
    editorial_role_map = validate_role_map(
        payload.get("editorial_role_map"), field="editorial_role_map"
    )
    if not editorial_role_map:
        raise RoleRegistryError("editorial_role_map must declare at least one specialized role.")

    entries = payload.get("roles")
    if not isinstance(entries, list) or not entries:
        raise RoleRegistryError("Prompt role registry roles must be a non-empty list.")
    roles: dict[str, RoleContract] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RoleRegistryError(f"roles[{index}] must be an object.")
        expected_fields = {
            "role_id",
            "role_version",
            "prompt_path",
            "output_mode",
            "independence_mode",
        }
        if set(entry) != expected_fields:
            raise RoleRegistryError(
                f"roles[{index}] fields must be exactly: {', '.join(sorted(expected_fields))}."
            )
        role_id = normalize_id(entry.get("role_id"))
        if not ROLE_ID_PATTERN.fullmatch(role_id):
            raise RoleRegistryError(f"roles[{index}].role_id is invalid: {entry.get('role_id')!r}.")
        if role_id in roles:
            raise RoleRegistryError(f"Duplicate Prompt role_id `{role_id}`.")
        role_version = str(entry.get("role_version") or "").strip()
        if not re.fullmatch(r"[1-9]\d*\.\d+\.\d+", role_version):
            raise RoleRegistryError(f"Role `{role_id}` has invalid role_version `{role_version}`.")
        prompt_path = validate_prompt_path(base, role_id, entry.get("prompt_path"))
        prompt_text = read_prompt_contract(base / prompt_path, role_id=role_id)
        output_mode = normalize_id(entry.get("output_mode"))
        if output_mode not in OUTPUT_MODES:
            raise RoleRegistryError(f"Role `{role_id}` has unknown output_mode `{output_mode}`.")
        independence_mode = normalize_id(entry.get("independence_mode"))
        if independence_mode not in INDEPENDENCE_MODES:
            raise RoleRegistryError(
                f"Role `{role_id}` has unknown independence_mode `{independence_mode}`."
            )
        roles[role_id] = RoleContract(
            role_id=role_id,
            role_version=role_version,
            prompt_path=prompt_path.as_posix(),
            prompt_text=prompt_text,
            prompt_hash=sha256(prompt_text.encode("utf-8")).hexdigest(),
            output_mode=output_mode,
            independence_mode=independence_mode,
            allowed_overlay_fields=allowed_overlay_fields,
        )

    referenced_roles = set(task_role_map.values()) | set(editorial_role_map.values())
    missing_roles = sorted(referenced_roles - set(roles))
    unused_roles = sorted(set(roles) - referenced_roles)
    if missing_roles:
        raise RoleRegistryError(f"Role mappings reference missing contracts: {', '.join(missing_roles)}.")
    if unused_roles:
        raise RoleRegistryError(f"Role contracts are not mapped to any task: {', '.join(unused_roles)}.")
    duplicate_editorial_targets = duplicate_values(editorial_role_map)
    if duplicate_editorial_targets:
        raise RoleRegistryError(
            "editorial_role_map aliases multiple declarations to the same contract: "
            + ", ".join(duplicate_editorial_targets)
        )
    return RoleRegistry(
        registry_version=registry_version,
        roles=roles,
        task_role_map=task_role_map,
        editorial_role_map=editorial_role_map,
        allowed_overlay_fields=allowed_overlay_fields,
    )


def role_resource_fingerprint(base: Path) -> tuple[tuple[str, int, int], ...]:
    """Return a cheap invalidation key without caching across edited Prompt resources."""

    role_root = base / "config" / "agent_roles"
    if not role_root.is_dir():
        return (("config/agent_roles/<missing>", 0, 0),)
    records: list[tuple[str, int, int]] = []
    for path in sorted((item for item in role_root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        stat = path.stat()
        records.append((path.relative_to(base).as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(records)


def validate_role_task_coverage(task_types: set[str], *, root: Path | None = None) -> RoleRegistry:
    registry = load_role_registry(root)
    expected = {normalize_id(item) for item in task_types} - {"editorial_review"}
    actual = set(registry.task_role_map)
    if expected != actual:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        raise RoleRegistryError("Prompt role task coverage mismatch: " + "; ".join(details))
    return registry


def validate_manifest_role_metadata(
    manifest: dict[str, Any],
    *,
    root: Path | None = None,
    registry: RoleRegistry | None = None,
) -> list[str]:
    """Return reproducible role metadata errors for one normalized manifest."""

    errors: list[str] = []
    task_type = str(manifest.get("task_type") or "")
    declared_role_id = str(manifest.get("role_id") or "")
    try:
        contract = (registry or load_role_registry(root)).resolve(
            task_type, declared_role_id=declared_role_id
        )
    except RoleRegistryError as exc:
        return [str(exc)]
    expected = contract.manifest_metadata()
    for field in ROLE_METADATA_FIELDS:
        value = str(manifest.get(field) or "")
        if not value:
            errors.append(f"Agent task manifest {field} is required.")
        elif field != "project_overlay_hash" and value != expected[field]:
            errors.append(
                f"Agent task manifest {field} drifted for role `{contract.role_id}`; "
                f"expected `{expected[field]}`, got `{value}`."
            )
    prompt_hash = str(manifest.get("role_prompt_hash") or "")
    overlay_hash = str(manifest.get("project_overlay_hash") or "")
    if prompt_hash and not SHA256_PATTERN.fullmatch(prompt_hash):
        errors.append("Agent task manifest role_prompt_hash must be a lowercase SHA-256 value.")
    if overlay_hash and not SHA256_PATTERN.fullmatch(overlay_hash):
        errors.append("Agent task manifest project_overlay_hash must be a lowercase SHA-256 value.")
    return errors


def reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise RoleRegistryError(f"Duplicate JSON key `{key}`.")
        payload[key] = value
    return payload


def validate_string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise RoleRegistryError(f"{field} must be a non-empty list.")
    normalized = tuple(str(item).strip() for item in value)
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise RoleRegistryError(f"{field} must contain unique non-empty strings.")
    return normalized


def validate_role_map(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RoleRegistryError(f"{field} must be an object.")
    result: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = normalize_id(raw_key)
        role_id = normalize_id(raw_value)
        if not ROLE_ID_PATTERN.fullmatch(key) or not ROLE_ID_PATTERN.fullmatch(role_id):
            raise RoleRegistryError(f"{field} contains invalid mapping {raw_key!r}: {raw_value!r}.")
        result[key] = role_id
    return result


def validate_prompt_path(base: Path, role_id: str, value: Any) -> Path:
    prompt_path = Path(str(value or "").replace("\\", "/"))
    if prompt_path.is_absolute() or ".." in prompt_path.parts:
        raise RoleRegistryError(f"Role `{role_id}` prompt_path must stay inside the resource root.")
    expected_parent = Path("config/agent_roles/prompts")
    if prompt_path.parent != expected_parent or prompt_path.suffix.lower() != ".md":
        raise RoleRegistryError(
            f"Role `{role_id}` prompt_path must be a Markdown file under {expected_parent.as_posix()}."
        )
    resolved = (base / prompt_path).resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise RoleRegistryError(f"Role `{role_id}` prompt_path escapes the resource root.") from exc
    if not resolved.is_file():
        raise RoleRegistryError(f"Role `{role_id}` Prompt is missing: {prompt_path.as_posix()}.")
    return prompt_path


def read_prompt_contract(path: Path, *, role_id: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise RoleRegistryError(f"Role `{role_id}` Prompt is not readable UTF-8: {exc}") from exc
    for heading in ROLE_PROMPT_HEADINGS:
        marker = f"## {heading}"
        count = sum(1 for line in text.splitlines() if line.strip() == marker)
        if count != 1:
            raise RoleRegistryError(
                f"Role `{role_id}` Prompt must contain exactly one `{marker}` heading; found {count}."
            )
        body = text.split(marker, 1)[1].split("\n## ", 1)[0].strip()
        if not body:
            raise RoleRegistryError(f"Role `{role_id}` Prompt section `{heading}` is empty.")
    return text


def duplicate_values(mapping: dict[str, str]) -> list[str]:
    counts: dict[str, int] = {}
    for value in mapping.values():
        counts[value] = counts.get(value, 0) + 1
    return sorted(value for value, count in counts.items() if count > 1)


def normalize_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
