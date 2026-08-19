"""Read-only normalization and evidence validation for Agent outputs.

This module is deliberately outside canonical apply paths. It validates the
four Agent-facing protocols and builds one evidence-bound control-plane view;
context packets, Agent-supplied hashes, and prose notes never become facts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json
import re

from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_DOCUMENT_SCHEMA,
    EVIDENCE_REVIEW_SCHEMA,
    PROSE_MARKDOWN_SCHEMA,
    AgentProtocolError,
    parse_design_document,
    validate_canonical_delta,
    validate_evidence_review,
)
from longform_engine.agent_results import (
    validate_design_document_output,
    validate_markdown_prose_output,
)
from longform_engine.agent_tasks import (
    manifest_chapter_number,
    manifest_commands,
    manifest_input_paths,
    manifest_output,
    manifest_policy,
    manifest_role,
    normalize_manifest,
    relative_path,
    resolve_under_root,
    validate_manifest_strict,
)
from longform_engine.roles import load_role_registry, reject_duplicate_json_keys
from longform_engine.resources import resource_path, resource_root
from longform_engine.storage import atomic_write_text
from longform_engine.storage.layout import manuscript_chapter_path


NORMALIZED_RESULT_SCHEMA = "normalized_agent_result_v1"
AGENT_RESULT_DIAGNOSTIC_SCHEMA = "agent_result_diagnostic_v1"
CANONICAL_READ_PREFIXES = (
    "00_governance/",
    "10_bible/",
    "20_outline/",
    "30_state/",
    "40_manuscript/final/",
)
CONTROL_PLANE_FIELDS = frozenset(
    {
        "boundary_profile",
        "commands",
        "allowed_output_paths",
        "apply_command",
        "failure_next_command",
        "hard_boundaries",
        "requires_human_apply",
        "validate_command",
    }
)
KNOWLEDGE_ROUTES = frozenset({"observed", "heard", "told", "inferred", "document", "experienced"})
FORESHADOW_ACTIONS = frozenset({"plant", "reinforce", "mislead", "payoff", "expire"})


@dataclass(frozen=True)
class SourceRecord:
    source_ref: str
    path: str
    sha256: str
    characters: int
    declared_by: tuple[str, ...]
    authority: str


@dataclass(frozen=True)
class AgentResultNormalization:
    schema: str
    ok: bool
    status: str
    task_id: str
    task_type: str
    source_schema: str
    adapter: str
    result_file: str
    result_sha256: str
    normalized_result: dict[str, Any]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    need_human_reasons: tuple[str, ...]
    next_command: str
    diagnostic_file: str = ""


class SourceRegistry:
    """Current-file source registry with ambiguity-preserving aliases."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._records: dict[str, SourceRecord] = {}
        self._aliases: dict[str, set[str]] = {}

    def add(
        self,
        path_value: str | Path,
        *,
        declared_by: str,
        source_ref: str = "",
        declared_hash: str = "",
    ) -> str | None:
        try:
            path = resolve_under_root(self.root, path_value)
        except ValueError:
            return f"source path escapes project root: {path_value}"
        relative = relative_path(self.root, path)
        return self._register(
            path,
            relative=relative,
            authority="project",
            declared_by=declared_by,
            source_ref=source_ref,
            declared_hash=declared_hash,
        )

    def add_resource(
        self,
        path_value: str | Path,
        *,
        declared_by: str,
        source_ref: str = "",
        declared_hash: str = "",
    ) -> str | None:
        relative = str(path_value).replace("\\", "/").strip("/")
        try:
            path = resource_path(*Path(relative).parts).resolve()
            path.relative_to(resource_root().resolve())
        except (FileNotFoundError, ValueError):
            return f"declared Engine resource does not exist or escapes the resource root: {relative}"
        return self._register(
            path,
            relative=relative,
            authority="engine_resource",
            declared_by=declared_by,
            source_ref=source_ref,
            declared_hash=declared_hash,
        )

    def _register(
        self,
        path: Path,
        *,
        relative: str,
        authority: str,
        declared_by: str,
        source_ref: str,
        declared_hash: str,
    ) -> str | None:
        if not path.is_file():
            return f"declared source does not exist or is not a file: {relative}"
        try:
            text = path.read_text(encoding="utf-8").lstrip("\ufeff")
        except UnicodeDecodeError:
            return f"declared source is not valid UTF-8: {relative}"
        # Context producers historically used both raw-file and normalized-text hashes.
        # Accept either verifiable representation, then expose the portable text hash.
        current_hash = sha256(text.encode("utf-8")).hexdigest()
        raw_hash = sha256(path.read_bytes()).hexdigest()
        if declared_hash and declared_hash not in {current_hash, raw_hash}:
            return f"declared source hash drifted for {relative}: expected {declared_hash}, got {current_hash}"
        prior = self._records.get(relative)
        if prior is not None and prior.authority != authority:
            return (
                f"declared source authority conflicts for {relative}: "
                f"{prior.authority} versus {authority}"
            )
        declarations = set(prior.declared_by if prior else ())
        declarations.add(declared_by)
        self._records[relative] = SourceRecord(
            source_ref=source_ref or (prior.source_ref if prior else relative),
            path=relative,
            sha256=current_hash,
            characters=len(text),
            declared_by=tuple(sorted(declarations)),
            authority=authority,
        )
        aliases = {
            relative,
            Path(relative).name,
            Path(relative).stem,
            source_ref.strip(),
        }
        aliases.update(conventional_aliases(relative))
        for alias in aliases:
            if alias:
                self._aliases.setdefault(alias, set()).add(relative)
        return None

    def resolve(self, source_ref: str) -> tuple[SourceRecord | None, str]:
        ref = str(source_ref or "").strip().replace("\\", "/")
        matches = self._aliases.get(ref, set())
        if not matches and ref in self._records:
            matches = {ref}
        if not matches:
            return None, f"source_ref `{ref}` is not declared by the manifest or a verified context packet"
        if len(matches) > 1:
            return None, f"source_ref `{ref}` is ambiguous across: {', '.join(sorted(matches))}"
        return self._records[next(iter(matches))], ""

    def record(self, path_value: str | Path) -> SourceRecord | None:
        return self._records.get(str(path_value).replace("\\", "/"))

    def records(self) -> list[SourceRecord]:
        return [self._records[key] for key in sorted(self._records)]


def normalize_and_validate_agent_result(
    root: Path,
    manifest: dict[str, Any],
    *,
    result_file: str | Path,
) -> AgentResultNormalization:
    """Normalize one result and verify it against current disk and canonical state."""

    root = root.resolve()
    errors: list[str] = []
    warnings: list[str] = []
    need_human: list[str] = []
    try:
        normalized_manifest = normalize_manifest(manifest)
    except ValueError as exc:
        return failed_report(
            manifest,
            result_file,
            errors=[str(exc)],
            adapter="manifest_rejected",
        )
    manifest_validation = validate_manifest_strict(root, normalized_manifest)
    if not manifest_validation.ok:
        return failed_report(
            normalized_manifest,
            result_file,
            errors=list(manifest_validation.errors),
            warnings=list(manifest_validation.warnings),
            adapter="manifest_rejected",
        )
    warnings.extend(manifest_validation.warnings)

    task_id = str(normalized_manifest["task_id"])
    task_type = str(normalized_manifest["task_type"])
    role = load_role_registry().resolve(
        task_type,
        declared_role_id=str(manifest_role(normalized_manifest).get("id") or ""),
    )
    try:
        result_path = resolve_under_root(root, result_file)
    except ValueError as exc:
        return failed_report(normalized_manifest, result_file, errors=[str(exc)], adapter="path_rejected")
    result_relative = relative_path(root, result_path)
    allowed_output = str(manifest_output(normalized_manifest).get("path") or "").replace("\\", "/")
    if result_relative != allowed_output:
        errors.append("result file must exactly match manifest io.output.path.")
    if not result_path.is_file():
        errors.append(f"result file does not exist: {result_relative}")
        result_bytes = b""
        result_text = ""
    else:
        result_bytes = result_path.read_bytes()
        try:
            result_text = result_bytes.decode("utf-8").lstrip("\ufeff")
        except UnicodeDecodeError:
            result_text = ""
            errors.append("result file must be valid UTF-8.")
    result_hash = sha256(result_bytes).hexdigest() if result_bytes else ""

    registry = SourceRegistry(root)
    for path_text in manifest_input_paths(normalized_manifest):
        source_error = registry.add(path_text, declared_by="manifest.io.inputs")
        if source_error:
            errors.append(source_error)
    context_errors, context_warnings = add_context_packet_sources(root, registry, normalized_manifest)
    errors.extend(context_errors)
    warnings.extend(context_warnings)

    chapter_number = manifest_chapter_number(normalized_manifest)
    planned_facts = load_planned_facts(root, chapter_number)
    allowed_refs = allowed_canonical_refs(registry)
    source_schema = role.output_mode
    adapter = "four_protocols_v1"
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    verdict = "pass"
    notes: list[str] = []
    raw_payload: dict[str, Any] = {}
    design_payload: dict[str, Any] = {}

    if role.output_mode == PROSE_MARKDOWN_SCHEMA:
        structural = validate_markdown_prose_output(
            normalized_manifest,
            result_text,
            output_path=result_relative,
        )
        errors.extend(structural.errors)
        warnings.extend(structural.warnings)
    elif role.output_mode == DESIGN_DOCUMENT_SCHEMA and result_text:
        structural = validate_design_document_output(
            normalized_manifest,
            document_text=result_text,
            document_path=result_relative,
        )
        errors.extend(structural.errors)
        warnings.extend(structural.warnings)
        if not structural.errors:
            try:
                document = parse_design_document(result_text, expected_type=task_type)
            except AgentProtocolError as exc:
                errors.append(str(exc))
            else:
                design_payload = {
                    "document_type": document.document_type,
                    "headings": list(document.headings),
                    "markdown_sha256": sha256(document.markdown.encode("utf-8")).hexdigest(),
                }
    elif result_text:
        try:
            loaded = json.loads(result_text, object_pairs_hook=reject_duplicate_json_keys)
        except (json.JSONDecodeError, ValueError) as exc:
            if isinstance(exc, json.JSONDecodeError):
                detail = f"{exc.msg} at line {exc.lineno} column {exc.colno}"
            else:
                detail = str(exc)
            errors.append(f"result file is not valid duplicate-key-safe JSON: {detail}.")
            loaded = {}
        if not isinstance(loaded, dict):
            errors.append("structured Agent result must be a JSON object.")
            loaded = {}
        raw_payload = loaded
        if role.output_mode == EVIDENCE_REVIEW_SCHEMA:
            errors.extend(
                validate_evidence_review(
                    loaded,
                    required_dimensions=role.review_dimensions,
                    allowed_finding_codes=role.finding_codes,
                    optional_dimensions=role.optional_review_dimensions,
                    canonical_ref_dimensions=role.canonical_ref_dimensions,
                )
            )
            verdict = str(loaded.get("verdict") or "")
            findings = list_of_dicts(loaded.get("findings"))
            validate_review_scope(role, loaded, errors, need_human)
            coverage_records = [
                item
                for item in (loaded.get("coverage") or {}).values()
                if isinstance(item, dict)
            ]
            evidence = compact_evidence_records(
                (
                    evidence_id
                    for item in [*coverage_records, *findings]
                    for evidence_id in item.get("evidence_ids") or []
                ),
                registry,
                errors,
                need_human,
            )
            declared_refs = {
                str(ref)
                for item in coverage_records
                for ref in item.get("canonical_refs") or []
            }
            allowed_ref_set = {
                str(item.get("path") or "")
                for item in allowed_refs
                if isinstance(item, dict) and item.get("path")
            }
            unknown_refs = sorted(
                ref for ref in declared_refs if ref.split("#", 1)[0] not in allowed_ref_set
            )
            if unknown_refs:
                errors.append(
                    "coverage references undeclared canonical sources: "
                    + ", ".join(unknown_refs)
                )
        elif role.output_mode == CANONICAL_DELTA_SCHEMA:
            errors.extend(validate_canonical_delta(loaded, task_type=task_type))
            evidence_map = loaded.get("evidence") if isinstance(loaded.get("evidence"), dict) else {}
            evidence = compact_evidence_records(
                (
                    evidence_id
                    for ids in evidence_map.values()
                    if isinstance(ids, list)
                    for evidence_id in ids
                ),
                registry,
                errors,
                need_human,
            )
            changes = loaded.get("changes") if isinstance(loaded.get("changes"), dict) else {}
            deltas = list_of_dicts(changes.get("deltas"))
            notes = [str(item) for item in loaded.get("uncertainties") or [] if isinstance(item, str)]
            if notes:
                need_human.append("canonical_delta_contains_uncertainties")
        else:
            errors.append(f"Unsupported Agent output protocol `{role.output_mode}`.")
        errors.extend(agent_control_plane_errors(loaded))

    enriched_evidence = validate_current_evidence(root, registry, evidence, errors, need_human)
    if verdict in {"need_human", "insufficient_evidence"}:
        need_human.append("agent_requested_need_human")
    validate_canonical_preconditions(
        root,
        normalized_manifest,
        deltas,
        enriched_evidence,
        errors,
        need_human,
    )
    if role.output_mode == CANONICAL_DELTA_SCHEMA and task_type == "chapter_semantic" and raw_payload:
        from longform_engine.semantic.pipeline import semantic_candidate_domain_payload

        semantic_errors: list[str] = []
        semantic_payload = semantic_candidate_domain_payload(
            root,
            result_path,
            chapter_number=chapter_number,
            expected_source=manuscript_chapter_path(root, chapter_number, lane="final"),
            errors=semantic_errors,
        )
        errors.extend(semantic_errors)
        if not semantic_errors:
            validate_chapter_semantic_preconditions(
                root,
                semantic_payload,
                chapter_number,
                errors,
                need_human,
            )

    normalized_result = {
        "schema": NORMALIZED_RESULT_SCHEMA,
        "source_schema": source_schema,
        "adapter": adapter,
        "task": {
            "task_id": task_id,
            "task_type": task_type,
            "role_id": str(manifest_role(normalized_manifest).get("id") or ""),
        },
        "scope": normalized_manifest.get("scope") or {},
        "chapter_number": chapter_number,
        "verdict": verdict,
        "evidence": enriched_evidence,
        "findings": findings,
        "deltas": deltas,
        "design_document": design_payload,
        "notes": notes,
        "cli_context": {
            "manifest_schema_version": int(normalized_manifest["schema_version"]),
            "manifest_sha256": stable_json_hash(normalized_manifest),
            "result_path": result_relative,
            "result_sha256": result_hash,
            "planned_facts": planned_facts,
            "allowed_canonical_refs": allowed_refs,
            "canonical_targets": list(manifest_policy(normalized_manifest).get("canonical_targets") or []),
            "source_registry": [asdict(item) for item in registry.records()],
        },
    }
    errors = unique(errors)
    warnings = unique(warnings)
    need_human = unique(need_human)
    status = "invalid" if errors else "need_human" if need_human else "valid"
    return AgentResultNormalization(
        schema=NORMALIZED_RESULT_SCHEMA,
        ok=status == "valid",
        status=status,
        task_id=task_id,
        task_type=task_type,
        source_schema=source_schema,
        adapter=adapter,
        result_file=result_relative,
        result_sha256=result_hash,
        normalized_result=normalized_result,
        errors=tuple(errors),
        warnings=tuple(warnings),
        need_human_reasons=tuple(need_human),
        next_command=str(
            manifest_commands(normalized_manifest).get("validate" if status == "valid" else "failure") or ""
        ),
    )


def write_agent_result_diagnostic(root: Path, result: AgentResultNormalization) -> AgentResultNormalization:
    """Write only a controlled workbench diagnostic; never update task lifecycle."""

    safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", result.task_id).strip("_") or "unknown_task"
    digest = result.result_sha256[:12] or "no_result"
    path = root / "50_workbench" / "agent_tasks" / "diagnostics" / f"{safe_task}.{digest}.json"
    payload = asdict(result)
    payload["schema"] = AGENT_RESULT_DIAGNOSTIC_SCHEMA
    payload["diagnostic_file"] = relative_path(root, path)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return AgentResultNormalization(**payload)


def add_context_packet_sources(
    root: Path,
    registry: SourceRegistry,
    manifest: dict[str, Any],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    for input_path in manifest_input_paths(manifest):
        path = resolve_under_root(root, input_path)
        if path.suffix.lower() != ".json" or not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        packet_path = relative_path(root, path)
        for key in ("source_catalog", "provenance", "canonical_source_provenance"):
            for item in list_of_dicts(payload.get(key)):
                source_path = str(item.get("path") or item.get("source_path") or "").strip()
                if not source_path:
                    continue
                authority = str(item.get("authority") or item.get("scope") or "project")
                add_source = registry.add_resource if authority == "engine_resource" else registry.add
                if authority not in {"project", "engine_resource"}:
                    errors.append(f"unknown source authority `{authority}` in {packet_path}:{key}")
                    continue
                source_error = add_source(
                    source_path,
                    declared_by=f"{packet_path}:{key}",
                    source_ref=str(item.get("source_id") or item.get("source_ref") or ""),
                    declared_hash=str(item.get("sha256") or item.get("source_hash") or ""),
                )
                if source_error:
                    errors.append(source_error)
        for item in payload.get("allowed_canonical_refs") or []:
            if isinstance(item, str):
                source_path, source_hash, source_ref = item, "", item
            elif isinstance(item, dict):
                source_path = str(item.get("path") or item.get("source_path") or "")
                source_hash = str(item.get("sha256") or item.get("source_hash") or "")
                source_ref = str(item.get("source_id") or item.get("source_ref") or source_path)
            else:
                warnings.append(f"ignored malformed allowed_canonical_refs entry in {packet_path}")
                continue
            normalized = source_path.replace("\\", "/")
            if not normalized.startswith(CANONICAL_READ_PREFIXES):
                errors.append(f"context packet canonical ref is outside readable canonical lanes: {normalized}")
                continue
            source_error = registry.add(
                normalized,
                declared_by=f"{packet_path}:allowed_canonical_refs",
                source_ref=source_ref,
                declared_hash=source_hash,
            )
            if source_error:
                errors.append(source_error)
    return errors, warnings


COMPACT_EVIDENCE_PATTERN = re.compile(r"^(?P<source>.+)@(?P<start>\d+):(?P<end>\d+)$")


def compact_evidence_records(
    evidence_ids: Iterable[Any],
    registry: SourceRegistry,
    errors: list[str],
    need_human: list[str],
) -> list[dict[str, Any]]:
    """Turn compact source@start:end IDs into exact current-file evidence objects."""

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(evidence_ids):
        evidence_id = str(raw or "").strip().replace("\\", "/")
        if not evidence_id or evidence_id in seen:
            continue
        seen.add(evidence_id)
        match = COMPACT_EVIDENCE_PATTERN.fullmatch(evidence_id)
        if not match:
            errors.append(
                f"evidence ID `{evidence_id}` must use source_ref@start:end character offsets."
            )
            continue
        source_ref = match.group("source")
        source, resolution_error = registry.resolve(source_ref)
        if resolution_error:
            target = need_human if "ambiguous" in resolution_error else errors
            target.append(f"evidence[{index}]: {resolution_error}")
            continue
        assert source is not None
        source_path = registry.root / source.path
        text = source_path.read_text(encoding="utf-8").lstrip("\ufeff")
        start = int(match.group("start"))
        end = int(match.group("end"))
        if start < 0 or end <= start or end > len(text):
            errors.append(f"evidence ID `{evidence_id}` is outside current source bounds.")
            continue
        records.append(
            {
                "evidence_id": evidence_id,
                "source_ref": source_ref,
                "start": start,
                "end": end,
                "excerpt": text[start:end],
            }
        )
    return records


def validate_review_scope(
    role: Any,
    payload: dict[str, Any],
    errors: list[str],
    need_human: list[str],
) -> None:
    allowed_dimensions = set(role.review_dimensions)
    allowed_codes = set(role.finding_codes)
    coverage_payload = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    coverage = set(coverage_payload)
    unknown_coverage = sorted(coverage - allowed_dimensions)
    if unknown_coverage:
        errors.append(
            f"review coverage exceeds role `{role.role_id}` scope: {', '.join(unknown_coverage)}."
        )
    missing_coverage = sorted(allowed_dimensions - coverage)
    if missing_coverage:
        need_human.append("review_coverage_incomplete:" + ",".join(missing_coverage))
    optional_dimensions = set(role.optional_review_dimensions)
    for dimension, record in coverage_payload.items():
        status = record.get("status") if isinstance(record, dict) else ""
        if dimension in allowed_dimensions and status == "insufficient":
            need_human.append(f"review_dimension_insufficient:{dimension}")
        if status == "not_applicable" and dimension not in optional_dimensions:
            errors.append(f"coverage.{dimension} is not optional for role `{role.role_id}`.")
    for index, finding in enumerate(list_of_dicts(payload.get("findings"))):
        code = str(finding.get("code") or "")
        if code not in allowed_codes:
            errors.append(f"findings[{index}].code `{code}` is outside the role finding range.")
        if finding.get("certainty") == "insufficient_evidence":
            need_human.append(f"finding_insufficient_evidence:{code or index}")


def validate_current_evidence(
    root: Path,
    registry: SourceRegistry,
    evidence: Iterable[dict[str, Any]],
    errors: list[str],
    need_human: list[str],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for index, item in enumerate(evidence):
        source, resolution_error = registry.resolve(str(item.get("source_ref") or ""))
        if resolution_error:
            target = need_human if "ambiguous" in resolution_error else errors
            target.append(f"evidence[{index}]: {resolution_error}")
            continue
        assert source is not None
        text = (root / source.path).read_text(encoding="utf-8").lstrip("\ufeff")
        start, end = item.get("start"), item.get("end")
        excerpt = item.get("excerpt")
        if not isinstance(start, int) or isinstance(start, bool) or start < 0:
            errors.append(f"evidence[{index}].start must be a non-negative integer.")
            continue
        if not isinstance(end, int) or isinstance(end, bool) or end <= start or end > len(text):
            errors.append(f"evidence[{index}].end must be within the current source and greater than start.")
            continue
        current_excerpt = text[start:end]
        if excerpt != current_excerpt:
            errors.append(
                f"evidence[{index}] exact span does not match current source `{source.path}`; "
                "context packets and Agent excerpts are not authoritative."
            )
            continue
        enriched.append(
            {
                "evidence_id": str(item.get("evidence_id") or f"evidence_{index + 1}"),
                "source_ref": str(item.get("source_ref") or ""),
                "source_path": source.path,
                "source_hash": source.sha256,
                "start": start,
                "end": end,
                "excerpt": current_excerpt,
            }
        )
    return enriched


def validate_canonical_preconditions(
    root: Path,
    manifest: dict[str, Any],
    deltas: Iterable[dict[str, Any]],
    evidence: Iterable[dict[str, Any]],
    errors: list[str],
    need_human: list[str],
) -> None:
    entities, characters = canonical_ids(root)
    relationships = relationship_records(root)
    planned = planned_threads(root)
    actual = actual_threads(root)
    evidence_ids = {str(item.get("evidence_id") or "") for item in evidence}
    task_type = str(manifest.get("task_type") or "")
    chapter = manifest_chapter_number(manifest)
    enforce_existing = task_type == "chapter_semantic"
    for index, delta in enumerate(deltas):
        entity_id = str(delta.get("entity_id") or "")
        field = str(delta.get("field") or "")
        action = str(delta.get("action") or "")
        refs = {str(item) for item in delta.get("evidence_refs") or []}
        if not refs <= evidence_ids:
            errors.append(f"deltas[{index}] references evidence that failed current-file validation.")
        known = entity_id in entities or entity_id in relationships or entity_id in planned
        existing_fact_lane = any(
            token in field.lower()
            for token in ("relationship", "character", "knowledge", "foreshadow")
        )
        if enforce_existing and existing_fact_lane and action not in {"add", "declare"} and not known:
            errors.append(f"deltas[{index}] references unknown canonical entity_id `{entity_id}`.")
        if entity_id in relationships or "relationship" in field:
            current = relationship_state(relationships.get(entity_id))
            if current is not None and delta.get("old_state") != current:
                errors.append(
                    f"deltas[{index}].old_state is {delta.get('old_state')!r}, expected relationship state {current!r}."
                )
        if "knowledge" in field.lower():
            if entity_id not in characters:
                errors.append(f"deltas[{index}] character knowledge references unknown character `{entity_id}`.")
            if action not in {"observe"} and not refs:
                errors.append(f"deltas[{index}] character knowledge change requires an exact evidence source.")
        if entity_id in planned or "foreshadow" in field.lower():
            if entity_id not in planned:
                errors.append(f"deltas[{index}] foreshadow change must use a planned thread_id.")
                continue
            domain_action = delta_domain_action(delta)
            window = payoff_window(planned[entity_id])
            if domain_action == "payoff" and window and not window[0] <= chapter <= window[1]:
                need_human.append(f"foreshadow_payoff_outside_window:{entity_id}")
            plant_chapter = int(planned[entity_id].get("plant_chapter") or 0)
            if domain_action == "plant" and plant_chapter and chapter < plant_chapter:
                need_human.append(f"foreshadow_planted_early:{entity_id}")
            if domain_action in {"reinforce", "mislead", "payoff", "expire"} and entity_id not in actual:
                errors.append(f"deltas[{index}] acts on foreshadow thread `{entity_id}` before it is planted.")


def validate_chapter_semantic_preconditions(
    root: Path,
    payload: dict[str, Any],
    chapter_number: int,
    errors: list[str],
    need_human: list[str],
) -> None:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_path = str(source.get("path") or "")
    try:
        source_file = resolve_under_root(root, source_path)
    except ValueError:
        source_text = ""
    else:
        source_text = source_file.read_text(encoding="utf-8").lstrip("\ufeff") if source_file.is_file() else ""
    entities, characters = canonical_ids(root)
    pair_states = relationship_pair_states(root)
    for index, delta in enumerate(list_of_dicts(payload.get("relationship_deltas"))):
        source_id = str(delta.get("source_id") or "")
        target_id = str(delta.get("target_id") or "")
        if source_id not in entities or target_id not in entities:
            errors.append(f"relationship_deltas[{index}] references an unknown stable entity ID.")
        current = pair_states.get((source_id, target_id), "none")
        if str(delta.get("prior_state") or "") != current:
            errors.append(
                f"relationship_deltas[{index}].prior_state is {delta.get('prior_state')!r}, expected {current!r}."
            )
    for index, delta in enumerate(list_of_dicts(payload.get("character_deltas"))):
        character_id = str(delta.get("character_id") or "")
        if character_id not in characters:
            errors.append(f"character_deltas[{index}] references unknown character_id `{character_id}`.")
        for fact_index, fact in enumerate(list_of_dicts(delta.get("knowledge_gained"))):
            if str(fact.get("route") or "") not in KNOWLEDGE_ROUTES:
                errors.append(f"character_deltas[{index}].knowledge_gained[{fact_index}].route is invalid.")
            if not str(fact.get("fact") or "").strip():
                errors.append(f"character_deltas[{index}].knowledge_gained[{fact_index}].fact is required.")
            validate_exact_evidence_object(
                fact.get("evidence"),
                source_text,
                f"character_deltas[{index}].knowledge_gained[{fact_index}].evidence",
                errors,
            )
    planned = planned_threads(root)
    actual = actual_threads(root)
    for index, delta in enumerate(list_of_dicts(payload.get("foreshadow_deltas"))):
        thread_id = str(delta.get("thread_id") or "")
        action = str(delta.get("action") or "")
        if thread_id not in planned:
            errors.append(f"foreshadow_deltas[{index}] must use a planned thread_id.")
            continue
        if action not in FORESHADOW_ACTIONS:
            errors.append(f"foreshadow_deltas[{index}].action is invalid.")
        window = payoff_window(planned[thread_id])
        if action == "payoff" and window and not window[0] <= chapter_number <= window[1]:
            need_human.append(f"foreshadow_payoff_outside_window:{thread_id}")
        plant_chapter = int(planned[thread_id].get("plant_chapter") or 0)
        if action == "plant" and plant_chapter and chapter_number < plant_chapter:
            need_human.append(f"foreshadow_planted_early:{thread_id}")
        if action in {"reinforce", "mislead", "payoff", "expire"} and thread_id not in actual:
            errors.append(f"foreshadow_deltas[{index}] acts on a thread that has not been planted.")
def validate_exact_evidence_object(
    value: Any,
    source_text: str,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an evidence object.")
        return
    start, end, excerpt = value.get("start"), value.get("end"), value.get("excerpt")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(source_text)
    ):
        errors.append(f"{label} has invalid offsets in the current source.")
        return
    if not isinstance(excerpt, str) or source_text[start:end] != excerpt:
        errors.append(f"{label}.excerpt does not match the current source span.")


def agent_control_plane_errors(
    payload: Any,
    *,
    allow_document_targets: bool = False,
    manifest_targets: set[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    manifest_targets = manifest_targets or set()

    def walk(value: Any, location: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                current = f"{location}.{key}" if location else key
                if key in CONTROL_PLANE_FIELDS:
                    errors.append(f"{current} is CLI-owned control-plane data and cannot be Agent-authored.")
                elif key == "canonical_targets":
                    if allow_document_targets and current.endswith("new_state.canonical_targets"):
                        targets = {str(target) for target in item or []}
                        if not targets <= manifest_targets:
                            errors.append(f"{current} exceeds manifest canonical_targets.")
                    else:
                        errors.append(f"{current} cannot expand manifest canonical write authority.")
                walk(item, current)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{location}[{index}]")

    walk(payload, "")
    return unique(errors)


def load_planned_facts(root: Path, chapter_number: int) -> dict[str, Any]:
    if chapter_number <= 0:
        return {"source_path": "", "source_hash": "", "values": {}}
    path = root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json"
    if not path.is_file():
        return {"source_path": "", "source_hash": "", "values": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"source_path": relative_path(root, path), "source_hash": file_hash(path), "values": {}}
    if not isinstance(payload, dict):
        payload = {}
    aliases = {
        "chapter_duty": ("chapter_duty",),
        "reader_gain": ("reader_gain",),
        "cost": ("cost",),
        "promise_refs": ("promise_refs",),
        "platform_promise": ("platform_promise",),
        "relationship_move": ("relationship_move", "relationship_impact"),
        "canon_refs": ("canon_refs",),
    }
    values: dict[str, Any] = {}
    for target, candidates in aliases.items():
        for candidate in candidates:
            if candidate in payload:
                values[target] = payload[candidate]
                break
    return {
        "source_path": relative_path(root, path),
        "source_hash": file_hash(path),
        "values": values,
    }


def allowed_canonical_refs(registry: SourceRegistry) -> list[dict[str, str]]:
    return [
        {"path": item.path, "sha256": item.sha256}
        for item in registry.records()
        if item.authority == "project" and item.path.startswith(CANONICAL_READ_PREFIXES)
    ]


def canonical_ids(root: Path) -> tuple[set[str], set[str]]:
    entities: set[str] = set()
    characters: set[str] = set()
    graph = read_json(root / "30_state" / "story_graph.json", {})
    for item in list_of_dicts(graph.get("entities") if isinstance(graph, dict) else []):
        if str(item.get("id") or ""):
            entities.add(str(item["id"]))
    for filename, is_character in (("characters.json", True), ("locations.json", False), ("factions.json", False)):
        for item in list_of_dicts(read_json(root / "10_bible" / filename, [])):
            identifier = str(item.get("id") or "")
            if identifier:
                entities.add(identifier)
                if is_character:
                    characters.add(identifier)
    return entities, characters


def relationship_records(root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path, key in (
        (root / "10_bible" / "relationships.json", None),
        (root / "30_state" / "story_graph.json", "relationships"),
    ):
        payload = read_json(path, [] if key is None else {})
        values = payload.get(key) if key and isinstance(payload, dict) else payload
        for item in list_of_dicts(values):
            identifier = str(item.get("id") or "")
            if identifier:
                records[identifier] = item
    return records


def relationship_pair_states(root: Path) -> dict[tuple[str, str], str]:
    states: dict[tuple[str, str], str] = {}
    for item in relationship_records(root).values():
        source = str(item.get("source_id") or item.get("source") or item.get("from") or "")
        target = str(item.get("target_id") or item.get("target") or item.get("to") or "")
        if source and target:
            states[(source, target)] = relationship_state(item) or "related"
    return states


def relationship_state(item: dict[str, Any] | None) -> Any:
    if not item:
        return None
    return item.get("state", item.get("stage", item.get("type", item.get("relation", "related"))))


def planned_threads(root: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(root / "20_outline" / "foreshadowing_ledger.json", [])
    return {
        str(item.get("thread_id") or item.get("id")): item
        for item in list_of_dicts(payload)
        if str(item.get("thread_id") or item.get("id") or "")
    }


def actual_threads(root: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(root / "30_state" / "foreshadowing_state.json", {})
    threads = payload.get("threads") if isinstance(payload, dict) else {}
    return {str(key): value for key, value in threads.items() if isinstance(value, dict)} if isinstance(threads, dict) else {}


def payoff_window(item: dict[str, Any]) -> tuple[int, int] | None:
    value = item.get("payoff_window")
    if isinstance(value, list) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    if item.get("payoff_start") is not None and item.get("payoff_end") is not None:
        try:
            return int(item["payoff_start"]), int(item["payoff_end"])
        except (TypeError, ValueError):
            return None
    return None


def delta_domain_action(delta: dict[str, Any]) -> str:
    new_state = delta.get("new_state")
    if isinstance(new_state, dict):
        return str(new_state.get("action") or new_state.get("status") or "")
    field = str(delta.get("field") or "").lower()
    return str(new_state or "") if field == "status" or field.endswith("_status") else ""


def conventional_aliases(relative: str) -> set[str]:
    aliases: set[str] = set()
    normalized = relative.replace("\\", "/")
    if normalized.startswith("40_manuscript/draft/") or normalized.startswith("50_workbench/repair_candidates/"):
        aliases.update({"draft", "current_draft", "current_chapter"})
    if normalized.startswith("40_manuscript/final/"):
        aliases.update({"final", "current_final", "current_chapter"})
    if "/chapter_cards/" in normalized:
        aliases.add("chapter_card")
    if "gate" in normalized.lower():
        aliases.add("gate_result")
    if "context" in normalized.lower():
        aliases.add("context")
    return aliases


def failed_report(
    manifest: dict[str, Any],
    result_file: str | Path,
    *,
    errors: list[str],
    warnings: list[str] | None = None,
    adapter: str,
) -> AgentResultNormalization:
    return AgentResultNormalization(
        schema=NORMALIZED_RESULT_SCHEMA,
        ok=False,
        status="invalid",
        task_id=str(manifest.get("task_id") or ""),
        task_type=str(manifest.get("task_type") or ""),
        source_schema="",
        adapter=adapter,
        result_file=str(result_file).replace("\\", "/"),
        result_sha256="",
        normalized_result={},
        errors=tuple(unique(errors)),
        warnings=tuple(unique(warnings or [])),
        need_human_reasons=(),
        next_command=str(manifest_commands(manifest).get("failure") or ""),
    )


def stable_json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
        return default


def list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(str(item) for item in values if str(item).strip()))
