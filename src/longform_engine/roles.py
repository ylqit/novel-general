"""Versioned, host-neutral Prompt role contracts for Agent tasks."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

import yaml

from longform_engine.agent_protocols import (
    AGENT_OUTPUT_PROTOCOLS,
    AgentProtocolError,
    UniqueKeyLoader,
)
from longform_engine.resources import resource_root


ROLE_REGISTRY_SCHEMA = "agent_role_registry_v3"
ROLE_REGISTRY_PATH = Path("config/agent_roles/registry.json")
EMPTY_PROJECT_OVERLAY_HASH = sha256(b"").hexdigest()
ROLE_PROMPT_HEADINGS = ("core",)
ROLE_PROFESSIONAL_SECTIONS = {
    "core": "always",
    "decision_model": "task",
    "workflow": "task",
    "diagnostics": "trigger",
    "failure_modes": "trigger",
    "calibration": "calibration_only",
}
PLAYBOOK_PROFESSIONAL_SECTIONS = {
    "core": "always",
    "creation": "task",
    "review": "task",
    "repair": "trigger",
    "facets": "reference_only",
    "examples": "calibration_only",
    "false_positives": "task",
    "calibration": "calibration_only",
}
GENERIC_TRIGGER_SIGNALS = frozenset(
    {"quality_risk", "need_human", "insufficient_evidence"}
)
OUTPUT_MODES = AGENT_OUTPUT_PROTOCOLS
INDEPENDENCE_MODES = frozenset({"author_context", "isolated_review", "cross_host_review"})
SESSION_POLICIES = frozenset(
    {
        "project_coordinator",
        "chapter_author",
        "isolated_revision",
        "isolated_review",
        "isolated_archival",
    }
)
ROLE_FAMILIES = frozenset(
    {"facilitation", "planning", "generation", "revision", "review", "analysis", "archival"}
)
SECTION_MODES = frozenset(
    {"always", "task", "trigger", "reference_only", "calibration_only"}
)
ROLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class RoleRegistryError(ValueError):
    """Raised when role resources cannot produce an unambiguous task contract."""


@dataclass(frozen=True)
class SectionedPrompt:
    path: str
    source_text: str
    sections: dict[str, str]
    section_modes: dict[str, str]
    source_hash: str
    section_hashes: dict[str, str]


@dataclass(frozen=True)
class PlaybookContract:
    playbook_id: str
    source: SectionedPrompt


@dataclass(frozen=True)
class SelectedPlaybook:
    playbook_id: str
    sections: tuple[str, ...]
    section_hashes: tuple[str, ...]

    def as_manifest_value(self) -> dict[str, Any]:
        return {
            "playbook_id": self.playbook_id,
            "sections": list(self.sections),
            "section_hashes": list(self.section_hashes),
        }


@dataclass(frozen=True)
class PromptSelection:
    role_sections: tuple[str, ...]
    role_section_hashes: tuple[str, ...]
    playbooks: tuple[SelectedPlaybook, ...]
    selection_hash: str


@dataclass(frozen=True)
class RoleContract:
    role_id: str
    role_version: str
    prompt_path: str
    prompt_text: str
    prompt_sections: dict[str, str]
    prompt_section_hashes: dict[str, str]
    contract_hash: str
    role_family: str
    output_mode: str
    independence_mode: str
    session_policy: str
    allowed_overlay_fields: tuple[str, ...]
    always_sections: tuple[str, ...]
    task_sections: tuple[str, ...]
    trigger_sections: dict[str, str]
    required_playbook_ids: tuple[str, ...]
    optional_playbook_ids: tuple[str, ...]
    max_active_playbooks: int
    review_dimensions: tuple[str, ...]
    optional_review_dimensions: tuple[str, ...]
    canonical_ref_dimensions: tuple[str, ...]
    finding_codes: tuple[str, ...]

    @property
    def identity(self) -> str:
        for line in self.prompt_sections.get("core", "").strip().splitlines():
            candidate = line.strip()
            if candidate and not candidate.startswith(("#", "**")):
                return candidate
        return self.role_id

    @property
    def prompt_hash(self) -> str:
        """Return the contract hash for callers that only display Prompt provenance."""

        return self.contract_hash

@dataclass(frozen=True)
class RoleRegistry:
    registry_version: int
    roles: dict[str, RoleContract]
    task_role_map: dict[str, str]
    editorial_role_map: dict[str, str]
    playbooks: dict[str, PlaybookContract]

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

    def select_prompt(
        self,
        task_type: str,
        *,
        declared_role_id: str = "",
        quality_focus: Any = (),
        trigger_codes: Any = (),
    ) -> PromptSelection:
        role = self.resolve(task_type, declared_role_id=declared_role_id)
        signals = selection_signals(quality_focus, trigger_codes)
        signals.add(normalize_id(task_type))
        role_sections = list(role.always_sections) + list(role.task_sections)
        for signal, section in role.trigger_sections.items():
            if signal in signals and section not in role_sections:
                role_sections.append(section)

        selected_ids = list(role.required_playbook_ids)
        for playbook_id in role.optional_playbook_ids:
            aliases = PLAYBOOK_SIGNAL_ALIASES.get(playbook_id, frozenset({playbook_id}))
            matched = sorted(signals & aliases)
            if matched and len(selected_ids) < role.max_active_playbooks:
                selected_ids.append(playbook_id)

        selected_playbooks: list[SelectedPlaybook] = []
        for playbook_id in selected_ids[: role.max_active_playbooks]:
            playbook = self.playbooks[playbook_id]
            section_ids = select_playbook_sections(
                playbook,
                role_family=role.role_family,
                task_type=normalize_id(task_type),
                signals=signals,
            )
            selected_playbooks.append(
                SelectedPlaybook(
                    playbook_id=playbook_id,
                    sections=section_ids,
                    section_hashes=tuple(
                        playbook.source.section_hashes[item] for item in section_ids
                    ),
                )
            )

        role_section_tuple = tuple(role_sections)
        role_hashes = tuple(role.prompt_section_hashes[item] for item in role_section_tuple)
        selection_payload = {
            "role_id": role.role_id,
            "role_version": role.role_version,
            "session_policy": role.session_policy,
            "role_sections": list(zip(role_section_tuple, role_hashes)),
            "playbooks": [item.as_manifest_value() for item in selected_playbooks],
            "signals": sorted(signals),
        }
        selection_hash = sha256(
            json.dumps(
                selection_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return PromptSelection(
            role_sections=role_section_tuple,
            role_section_hashes=role_hashes,
            playbooks=tuple(selected_playbooks),
            selection_hash=selection_hash,
        )


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

    playbooks = load_playbooks(base, payload.get("playbooks"))
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
    raw_session_policies = payload.get("session_policy_map")
    if not isinstance(raw_session_policies, dict) or not raw_session_policies:
        raise RoleRegistryError("session_policy_map must declare every active role.")
    session_policy_map = {
        normalize_id(role_id): normalize_id(policy)
        for role_id, policy in raw_session_policies.items()
    }
    invalid_session_policies = sorted(
        f"{role_id}:{policy}"
        for role_id, policy in session_policy_map.items()
        if policy not in SESSION_POLICIES
    )
    if invalid_session_policies:
        raise RoleRegistryError(
            "session_policy_map contains invalid policies: " + ", ".join(invalid_session_policies)
        )

    entries = payload.get("roles")
    if not isinstance(entries, list) or not entries:
        raise RoleRegistryError("Prompt role registry roles must be a non-empty list.")
    roles: dict[str, RoleContract] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RoleRegistryError(f"roles[{index}] must be an object.")
        required_fields = {
            "role_id",
            "role_version",
            "prompt_path",
            "role_family",
            "output_mode",
            "independence_mode",
            "allowed_overlay_fields",
            "always_sections",
            "task_sections",
            "trigger_sections",
            "required_playbook_ids",
            "optional_playbook_ids",
            "max_active_playbooks",
            "review_dimensions",
            "finding_codes",
        }
        optional_fields = {"optional_review_dimensions", "canonical_ref_dimensions"}
        if not required_fields <= set(entry) or not set(entry) <= required_fields | optional_fields:
            raise RoleRegistryError(
                "roles[{}] fields must contain {} and may add {}.".format(
                    index,
                    ", ".join(sorted(required_fields)),
                    ", ".join(sorted(optional_fields)),
                )
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
        prompt_source = read_sectioned_prompt(
            base / prompt_path,
            expected_schema="role_prompt_source_v1",
            expected_id=role_id,
            id_field="role_id",
        )
        role_family = normalize_id(entry.get("role_family"))
        if role_family not in ROLE_FAMILIES:
            raise RoleRegistryError(f"Role `{role_id}` has unknown role_family `{role_family}`.")
        output_mode = normalize_id(entry.get("output_mode"))
        if output_mode not in OUTPUT_MODES:
            raise RoleRegistryError(f"Role `{role_id}` has unknown output_mode `{output_mode}`.")
        independence_mode = normalize_id(entry.get("independence_mode"))
        if independence_mode not in INDEPENDENCE_MODES:
            raise RoleRegistryError(
                f"Role `{role_id}` has unknown independence_mode `{independence_mode}`."
            )
        session_policy = session_policy_map.get(role_id, "")
        if session_policy not in SESSION_POLICIES:
            raise RoleRegistryError(f"Role `{role_id}` has no valid session policy.")
        allowed_overlay_fields = validate_optional_string_list(
            entry.get("allowed_overlay_fields"), field=f"roles[{index}].allowed_overlay_fields"
        )
        always_sections = validate_string_list(
            entry.get("always_sections"), field=f"roles[{index}].always_sections"
        )
        task_sections = validate_string_list(
            entry.get("task_sections"), field=f"roles[{index}].task_sections"
        )
        trigger_sections = validate_trigger_sections(
            entry.get("trigger_sections"), field=f"roles[{index}].trigger_sections"
        )
        declared_sections = set(always_sections) | set(task_sections) | set(trigger_sections.values())
        unknown_sections = sorted(declared_sections - set(prompt_source.sections))
        if unknown_sections:
            raise RoleRegistryError(
                f"Role `{role_id}` references unknown Prompt sections: {', '.join(unknown_sections)}."
            )
        for section in always_sections:
            if prompt_source.section_modes.get(section) != "always":
                raise RoleRegistryError(f"Role `{role_id}` section `{section}` must use mode always.")
        for section in task_sections:
            if prompt_source.section_modes.get(section) != "task":
                raise RoleRegistryError(f"Role `{role_id}` section `{section}` must use mode task.")
        for section in trigger_sections.values():
            if prompt_source.section_modes.get(section) != "trigger":
                raise RoleRegistryError(f"Role `{role_id}` section `{section}` must use mode trigger.")
        required_playbooks = validate_string_list(
            entry.get("required_playbook_ids"),
            field=f"roles[{index}].required_playbook_ids",
        )
        optional_playbooks = validate_optional_string_list(
            entry.get("optional_playbook_ids"),
            field=f"roles[{index}].optional_playbook_ids",
        )
        playbook_ids = required_playbooks + optional_playbooks
        unknown_playbooks = sorted(set(playbook_ids) - set(playbooks))
        if unknown_playbooks:
            raise RoleRegistryError(
                f"Role `{role_id}` references unknown playbooks: {', '.join(unknown_playbooks)}."
            )
        max_active_playbooks = entry.get("max_active_playbooks")
        if not isinstance(max_active_playbooks, int) or isinstance(max_active_playbooks, bool):
            raise RoleRegistryError(f"roles[{index}].max_active_playbooks must be an integer.")
        if max_active_playbooks < len(required_playbooks) or max_active_playbooks > 3:
            raise RoleRegistryError(
                f"Role `{role_id}` max_active_playbooks must include all required modules and be at most 3."
            )
        if role_family == "review" and max_active_playbooks > 2:
            raise RoleRegistryError(
                f"Review role `{role_id}` may activate at most two Playbooks."
            )
        review_dimensions = validate_optional_string_list(
            entry.get("review_dimensions"), field=f"roles[{index}].review_dimensions"
        )
        optional_review_dimensions = validate_optional_string_list(
            entry.get("optional_review_dimensions", []),
            field=f"roles[{index}].optional_review_dimensions",
        )
        canonical_ref_dimensions = validate_optional_string_list(
            entry.get("canonical_ref_dimensions", []),
            field=f"roles[{index}].canonical_ref_dimensions",
        )
        if not set(optional_review_dimensions) <= set(review_dimensions):
            raise RoleRegistryError(
                f"Role `{role_id}` optional_review_dimensions must be review dimensions."
            )
        if not set(canonical_ref_dimensions) <= set(review_dimensions):
            raise RoleRegistryError(
                f"Role `{role_id}` canonical_ref_dimensions must be review dimensions."
            )
        finding_codes = validate_optional_string_list(
            entry.get("finding_codes"), field=f"roles[{index}].finding_codes"
        )
        if role_family == "review" and (not review_dimensions or not finding_codes):
            raise RoleRegistryError(
                f"Review role `{role_id}` requires review_dimensions and finding_codes."
            )
        validate_role_professional_structure(
            role_id,
            prompt_source,
            trigger_sections=trigger_sections,
        )
        contract_hash = sha256(
            json.dumps(
                {
                    "prompt_sha256": prompt_source.source_hash,
                    "role_version": role_version,
                    "role_family": role_family,
                    "independence_mode": independence_mode,
                    "session_policy": session_policy,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        roles[role_id] = RoleContract(
            role_id=role_id,
            role_version=role_version,
            prompt_path=prompt_path.as_posix(),
            prompt_text=prompt_source.source_text,
            prompt_sections=prompt_source.sections,
            prompt_section_hashes=prompt_source.section_hashes,
            contract_hash=contract_hash,
            role_family=role_family,
            output_mode=output_mode,
            independence_mode=independence_mode,
            session_policy=session_policy,
            allowed_overlay_fields=allowed_overlay_fields,
            always_sections=always_sections,
            task_sections=task_sections,
            trigger_sections=trigger_sections,
            required_playbook_ids=required_playbooks,
            optional_playbook_ids=optional_playbooks,
            max_active_playbooks=max_active_playbooks,
            review_dimensions=review_dimensions,
            optional_review_dimensions=optional_review_dimensions,
            canonical_ref_dimensions=canonical_ref_dimensions,
            finding_codes=finding_codes,
        )

    referenced_roles = set(task_role_map.values()) | set(editorial_role_map.values())
    missing_roles = sorted(referenced_roles - set(roles))
    unused_roles = sorted(set(roles) - referenced_roles)
    if missing_roles:
        raise RoleRegistryError(f"Role mappings reference missing contracts: {', '.join(missing_roles)}.")
    if unused_roles:
        raise RoleRegistryError(f"Role contracts are not mapped to any task: {', '.join(unused_roles)}.")
    missing_session_roles = sorted(set(roles) - set(session_policy_map))
    unknown_session_roles = sorted(set(session_policy_map) - set(roles))
    if missing_session_roles or unknown_session_roles:
        raise RoleRegistryError(
            "session_policy_map coverage mismatch: "
            f"missing={missing_session_roles}, unknown={unknown_session_roles}"
        )
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
        playbooks=playbooks,
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
    role_value = manifest.get("role") if isinstance(manifest.get("role"), dict) else {}
    declared_role_id = str(role_value.get("id") or "")
    try:
        contract = (registry or load_role_registry(root)).resolve(
            task_type, declared_role_id=declared_role_id
        )
    except RoleRegistryError as exc:
        return [str(exc)]
    active_registry = registry or load_role_registry(root)
    try:
        selection = active_registry.select_prompt(
            task_type,
            declared_role_id=declared_role_id,
            quality_focus=((manifest.get("policy") or {}).get("context") or {}).get("quality_focus") or [],
            trigger_codes=((manifest.get("policy") or {}).get("context") or {}).get("trigger_codes") or [],
        )
    except RoleRegistryError as exc:
        return [str(exc)]
    expected_scalars = {
        "id": contract.role_id,
        "version": contract.role_version,
        "contract_hash": contract.contract_hash,
        "selection_hash": selection.selection_hash,
        "independence_mode": contract.independence_mode,
    }
    for field, expected in expected_scalars.items():
        if role_value.get(field) != expected:
            errors.append(
                f"Agent task manifest role.{field} drifted for role `{contract.role_id}`; "
                f"expected `{expected}`, got `{role_value.get(field)}`."
            )
    for field in ("contract_hash", "selection_hash", "overlay_hash"):
        digest = str(role_value.get(field) or "")
        if not SHA256_PATTERN.fullmatch(digest):
            errors.append(f"Agent task manifest role.{field} must be a lowercase SHA-256 value.")
    expected_sections = [
        {"id": section, "sha256": digest}
        for section, digest in zip(selection.role_sections, selection.role_section_hashes, strict=True)
    ]
    expected_playbooks = [
        {
            "id": item.playbook_id,
            "sections": [
                {"id": section, "sha256": digest}
                for section, digest in zip(item.sections, item.section_hashes, strict=True)
            ],
        }
        for item in selection.playbooks
    ]
    if role_value.get("sections") != expected_sections:
        errors.append(f"Agent task manifest role.sections drifted for role `{contract.role_id}`.")
    if role_value.get("playbooks") != expected_playbooks:
        errors.append(f"Agent task manifest role.playbooks drifted for role `{contract.role_id}`.")
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


def validate_optional_string_list(value: Any, *, field: str) -> tuple[str, ...]:
    if value == []:
        return ()
    return validate_string_list(value, field=field)


def validate_trigger_sections(value: Any, *, field: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise RoleRegistryError(f"{field} must be an object.")
    result: dict[str, str] = {}
    for raw_signal, raw_section in value.items():
        signal = normalize_id(raw_signal)
        section = normalize_id(raw_section)
        if not ROLE_ID_PATTERN.fullmatch(signal) or not ROLE_ID_PATTERN.fullmatch(section):
            raise RoleRegistryError(f"{field} contains an invalid trigger mapping.")
        if signal in result:
            raise RoleRegistryError(f"{field} contains duplicate trigger `{signal}`.")
        result[signal] = section
    return result


def load_playbooks(base: Path, value: Any) -> dict[str, PlaybookContract]:
    if not isinstance(value, list) or not value:
        raise RoleRegistryError("playbooks must be a non-empty list.")
    result: dict[str, PlaybookContract] = {}
    for index, entry in enumerate(value):
        if not isinstance(entry, dict) or set(entry) != {"playbook_id", "path"}:
            raise RoleRegistryError(
                f"playbooks[{index}] must contain exactly playbook_id and path."
            )
        playbook_id = normalize_id(entry.get("playbook_id"))
        if not ROLE_ID_PATTERN.fullmatch(playbook_id) or playbook_id in result:
            raise RoleRegistryError(f"playbooks[{index}].playbook_id is invalid or duplicate.")
        path = Path(str(entry.get("path") or "").replace("\\", "/"))
        expected_parent = Path("config/agent_roles/playbooks")
        if path.is_absolute() or ".." in path.parts or path.parent != expected_parent or path.suffix != ".md":
            raise RoleRegistryError(
                f"Playbook `{playbook_id}` path must be a Markdown file under {expected_parent.as_posix()}."
            )
        resolved = (base / path).resolve()
        try:
            resolved.relative_to(base)
        except ValueError as exc:
            raise RoleRegistryError(f"Playbook `{playbook_id}` escapes the resource root.") from exc
        source = read_sectioned_prompt(
            resolved,
            expected_schema="craft_playbook_source_v1",
            expected_id=playbook_id,
            id_field="playbook_id",
        )
        validate_playbook_professional_structure(playbook_id, source)
        result[playbook_id] = PlaybookContract(playbook_id=playbook_id, source=source)
    return result


def validate_role_professional_structure(
    role_id: str,
    source: SectionedPrompt,
    *,
    trigger_sections: dict[str, str],
) -> None:
    """Reject shallow or generic role contracts before they can compile a task."""

    for section, expected_mode in ROLE_PROFESSIONAL_SECTIONS.items():
        actual_mode = source.section_modes.get(section)
        if actual_mode != expected_mode:
            raise RoleRegistryError(
                f"Role `{role_id}` section `{section}` must use mode `{expected_mode}`."
            )
    generic = sorted(GENERIC_TRIGGER_SIGNALS & set(trigger_sections))
    if generic:
        raise RoleRegistryError(
            f"Role `{role_id}` uses generic trigger signals: {', '.join(generic)}."
        )
    if "诊断树" not in source.sections["diagnostics"]:
        raise RoleRegistryError(
            f"Role `{role_id}` diagnostics must contain a role-specific diagnostic tree."
        )
    calibration = source.sections["calibration"]
    missing_markers = [
        marker for marker in ("正例", "反例", "边界") if marker not in calibration
    ]
    if missing_markers:
        raise RoleRegistryError(
            f"Role `{role_id}` calibration must contain role-specific positive, negative, "
            f"and boundary cases; missing: {', '.join(missing_markers)}."
        )


def validate_playbook_professional_structure(
    playbook_id: str,
    source: SectionedPrompt,
) -> None:
    """Require complete craft lanes while keeping examples out of runtime Prompts."""

    for section, expected_mode in PLAYBOOK_PROFESSIONAL_SECTIONS.items():
        actual_mode = source.section_modes.get(section)
        if actual_mode != expected_mode:
            raise RoleRegistryError(
                f"Playbook `{playbook_id}` section `{section}` must use mode `{expected_mode}`."
            )
    examples = source.sections["examples"]
    positive_count = len(re.findall(r"(?m)^\d+\.\s*正例：", examples))
    negative_count = len(re.findall(r"反例：", examples))
    if positive_count < 3 or negative_count < 3:
        raise RoleRegistryError(
            f"Playbook `{playbook_id}` requires at least three positive/negative micro-example pairs."
        )
    if "边界" not in source.sections["calibration"]:
        raise RoleRegistryError(
            f"Playbook `{playbook_id}` calibration must declare boundary cases."
        )


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


def read_sectioned_prompt(
    path: Path,
    *,
    expected_schema: str,
    expected_id: str,
    id_field: str,
) -> SectionedPrompt:
    try:
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    except (OSError, UnicodeError) as exc:
        raise RoleRegistryError(f"Prompt source `{expected_id}` is not readable UTF-8: {exc}") from exc
    if not text.startswith("---\n"):
        raise RoleRegistryError(f"Prompt source `{expected_id}` must start with YAML front matter.")
    boundary = text.find("\n---\n", 4)
    if boundary < 0:
        raise RoleRegistryError(f"Prompt source `{expected_id}` front matter is not closed.")
    try:
        front = yaml.load(text[4:boundary], Loader=UniqueKeyLoader)
    except (yaml.YAMLError, AgentProtocolError) as exc:
        raise RoleRegistryError(f"Prompt source `{expected_id}` front matter is invalid: {exc}") from exc
    expected_fields = {"schema", id_field, "sections"}
    if not isinstance(front, dict) or set(front) != expected_fields:
        raise RoleRegistryError(
            f"Prompt source `{expected_id}` front matter fields must be exactly: "
            + ", ".join(sorted(expected_fields))
            + "."
        )
    if front.get("schema") != expected_schema or normalize_id(front.get(id_field)) != expected_id:
        raise RoleRegistryError(f"Prompt source identity mismatch for `{expected_id}`.")
    raw_modes = front.get("sections")
    if not isinstance(raw_modes, dict) or not raw_modes:
        raise RoleRegistryError(f"Prompt source `{expected_id}` must declare sections.")
    modes: dict[str, str] = {}
    for raw_section, raw_mode in raw_modes.items():
        section = normalize_id(raw_section)
        mode = normalize_id(raw_mode)
        if not ROLE_ID_PATTERN.fullmatch(section) or mode not in SECTION_MODES:
            raise RoleRegistryError(
                f"Prompt source `{expected_id}` has invalid section `{raw_section}` mode `{raw_mode}`."
            )
        modes[section] = mode
    body = text[boundary + 5 :].strip()
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(body.splitlines()):
        match = re.fullmatch(r"##\s+([a-z][a-z0-9_]*)\s*", line.strip())
        if match:
            headings.append((index, match.group(1)))
    heading_ids = [item[1] for item in headings]
    if len(heading_ids) != len(set(heading_ids)) or set(heading_ids) != set(modes):
        raise RoleRegistryError(
            f"Prompt source `{expected_id}` Markdown headings must exactly match declared sections."
        )
    lines = body.splitlines()
    sections: dict[str, str] = {}
    for position, (start, section) in enumerate(headings):
        end = headings[position + 1][0] if position + 1 < len(headings) else len(lines)
        content = "\n".join(lines[start + 1 : end]).strip()
        if not content or not re.search(r"[\u4e00-\u9fff]", content):
            raise RoleRegistryError(
                f"Prompt source `{expected_id}` section `{section}` must contain Chinese guidance."
            )
        sections[section] = content
    source_hash = sha256(text.encode("utf-8")).hexdigest()
    return SectionedPrompt(
        path=path.as_posix(),
        source_text=text,
        sections=sections,
        section_modes=modes,
        source_hash=source_hash,
        section_hashes={
            section: sha256(content.encode("utf-8")).hexdigest()
            for section, content in sections.items()
        },
    )


PLAYBOOK_SIGNAL_ALIASES: dict[str, frozenset[str]] = {
    "opening_and_mainline": frozenset({"opening", "mainline", "goal", "motivation", "opening_and_mainline"}),
    "scene_causality": frozenset({"scene", "causality", "summary_heavy", "scene_causality"}),
    "character_agency": frozenset({"character", "agency", "flat_character", "character_agency"}),
    "dialogue_and_subtext": frozenset({"dialogue", "speaker", "subtext", "dialogue_and_subtext"}),
    "interiority_and_emotion": frozenset({"interiority", "emotion", "trauma", "memory", "interiority_and_emotion"}),
    "world_rules_and_exposition": frozenset({"world", "rules", "exposition", "world_rules_and_exposition"}),
    "relationship_dynamics": frozenset({"relationship", "romance", "team", "relationship_dynamics"}),
    "foreshadow_and_mystery": frozenset({"foreshadow", "mystery", "reveal", "foreshadow_and_mystery"}),
    "serial_pacing": frozenset({"pacing", "payoff", "serial", "serial_pacing"}),
    "anti_ai_expression": frozenset({"anti_ai", "template", "voice", "anti_ai_expression"}),
    "ensemble_and_viewpoint": frozenset({"ensemble", "viewpoint", "pov", "ensemble_and_viewpoint"}),
    "fanfiction_canon": frozenset({"fanfiction", "canon", "ooc", "fanfiction_canon"}),
}


def selection_signals(quality_focus: Any, trigger_codes: Any) -> set[str]:
    values: list[Any] = []
    if isinstance(quality_focus, dict):
        for key, items in quality_focus.items():
            values.append(key)
            values.extend(items if isinstance(items, list) else [items])
    else:
        values.extend(quality_focus if isinstance(quality_focus, list) else [quality_focus])
    values.extend(trigger_codes if isinstance(trigger_codes, list) else [trigger_codes])
    return {normalize_id(item) for item in values if str(item or "").strip()}


def select_playbook_sections(
    playbook: PlaybookContract,
    *,
    role_family: str,
    task_type: str,
    signals: set[str],
) -> tuple[str, ...]:
    source = playbook.source
    selected = [section for section, mode in source.section_modes.items() if mode == "always"]
    preferred = {
        "facilitation": ("creation", "decision"),
        "planning": ("creation", "planning"),
        "generation": ("creation", "production"),
        "review": ("review", "false_positives"),
        "analysis": ("analysis", "review"),
        "archival": ("review", "false_positives"),
    }.get(role_family, ())
    repair_task = role_family == "revision" and task_type in {"repair", "humanize"}
    if role_family == "revision":
        preferred = () if repair_task else ("creation",)
    task_sections = [section for section, mode in source.section_modes.items() if mode == "task"]
    matched_task = [section for section in preferred if section in task_sections]
    if matched_task:
        selected.extend(matched_task)
    elif task_sections and not repair_task:
        selected.append(task_sections[0])
    repair_requested = task_type in {"repair", "humanize"} or any(
        signal.startswith("repair_") or signal.endswith("_repair") for signal in signals
    )
    if repair_requested:
        selected.extend(
            section
            for section, mode in source.section_modes.items()
            if mode == "trigger" and section == "repair" and section not in selected
        )
    return tuple(dict.fromkeys(selected))


def duplicate_values(mapping: dict[str, str]) -> list[str]:
    counts: dict[str, int] = {}
    for value in mapping.values():
        counts[value] = counts.get(value, 0) + 1
    return sorted(value for value, count in counts.items() if count > 1)


def normalize_id(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def session_directive(
    role: RoleContract,
    *,
    task_type: str,
    scope: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Render the host-facing session boundary owned by the role contract."""

    policy = role.session_policy
    chapter = int(scope.get("chapter_number") or 0) if isinstance(scope, dict) else 0
    if policy == "project_coordinator":
        action = "continue_project_session"
        scope_key = "project:coordination"
        forbidden: list[str] = []
    elif policy == "chapter_author":
        action = "continue_chapter_session" if normalize_id(task_type) == "repair" else "new_session_required"
        scope_key = f"ch{chapter:03d}:author"
        forbidden = ["isolated_review", "isolated_archival"]
    elif policy == "isolated_revision":
        action = "new_session_required"
        scope_key = f"ch{chapter:03d}:revision" if chapter else "project:revision"
        forbidden = ["chapter_author", "isolated_review"]
    elif policy == "isolated_review":
        action = "new_session_required"
        scope_key = f"ch{chapter:03d}:review:{role.role_id}" if chapter else f"project:review:{role.role_id}"
        forbidden = ["chapter_author", "isolated_revision", "peer_review", "editorial_aggregate"]
    else:
        action = "new_session_required"
        scope_key = f"ch{chapter:03d}:archival" if chapter else "project:archival"
        forbidden = ["chapter_author", "isolated_revision", "isolated_review"]
    return {
        "policy": policy,
        "action": action,
        "scope": scope_key,
        "forbidden_previous_context": forbidden,
        "first_command": f"longform-engine agent-task brief project.yaml {task_id}",
        "host_enforced": False,
    }
