"""Read-only normalization and evidence validation for Agent outputs.

This module is deliberately outside the production router and canonical apply
paths.  It turns current and legacy Agent results into one internal view, but
never treats a context packet, Agent-supplied hash, or prose note as fact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json
import re

from longform_engine.agent_results import (
    AGENT_RESULT_ENVELOPE_SCHEMA,
    validate_agent_result_envelope,
    validate_document_index_bundle,
    validate_markdown_prose_output,
)
from longform_engine.agent_tasks import (
    normalize_manifest,
    relative_path,
    resolve_under_root,
    validate_manifest_strict,
)
from longform_engine.roles import load_role_registry
from longform_engine.resources import resource_path, resource_root
from longform_engine.storage import atomic_write_text


NORMALIZED_RESULT_SCHEMA = "normalized_agent_result_v1"
VALIDATION_REPORT_SCHEMA = "agent_result_validation_v1"
CANONICAL_READ_PREFIXES = (
    "00_governance/",
    "10_bible/",
    "20_outline/",
    "30_state/",
    "40_manuscript/final/",
)
CONTROL_PLANE_FIELDS = frozenset(
    {
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
        current_hash = sha256(path.read_bytes()).hexdigest()
        if declared_hash and declared_hash != current_hash:
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
    document_file: str | Path | None = None,
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
        declared_role_id=str(normalized_manifest.get("role_id") or ""),
    )
    try:
        result_path = resolve_under_root(root, result_file)
    except ValueError as exc:
        return failed_report(normalized_manifest, result_file, errors=[str(exc)], adapter="path_rejected")
    result_relative = relative_path(root, result_path)
    allowed_outputs = {str(item).replace("\\", "/") for item in normalized_manifest["allowed_output_paths"]}
    if result_relative not in allowed_outputs:
        errors.append("result file must exactly match one manifest allowed_output_paths entry.")
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
    for path_text in normalized_manifest.get("input_files") or []:
        source_error = registry.add(path_text, declared_by="manifest.input_files")
        if source_error:
            errors.append(source_error)
    context_errors, context_warnings = add_context_packet_sources(root, registry, normalized_manifest)
    errors.extend(context_errors)
    warnings.extend(context_warnings)

    chapter_number = int(normalized_manifest.get("chapter_number") or 0)
    planned_facts = load_planned_facts(root, chapter_number)
    allowed_refs = allowed_canonical_refs(registry)
    source_schema = "markdown_prose_only" if role.output_mode == "markdown_prose" else ""
    adapter = "agent_first_v1"
    evidence: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    verdict = "pass"
    notes: list[str] = []
    raw_payload: dict[str, Any] = {}

    if role.output_mode == "markdown_prose":
        structural = validate_markdown_prose_output(
            normalized_manifest,
            result_text,
            output_path=result_relative,
        )
        errors.extend(structural.errors)
        warnings.extend(structural.warnings)
    elif result_text:
        try:
            loaded = json.loads(result_text)
        except json.JSONDecodeError as exc:
            errors.append(f"result file is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}.")
            loaded = {}
        if not isinstance(loaded, dict):
            errors.append("structured Agent result must be a JSON object.")
            loaded = {}
        raw_payload = loaded
        source_schema = detect_source_schema(loaded)
        if source_schema == AGENT_RESULT_ENVELOPE_SCHEMA:
            structural = validate_agent_result_envelope(normalized_manifest, loaded)
            errors.extend(structural.errors)
            warnings.extend(structural.warnings)
            if role.output_mode == "document_index_bundle":
                doc_path, doc_text = load_declared_document(root, normalized_manifest, document_file, errors)
                if doc_path is not None:
                    bundle = validate_document_index_bundle(
                        normalized_manifest,
                        document_text=doc_text,
                        document_path=relative_path(root, doc_path),
                        index_payload=loaded,
                        index_path=result_relative,
                    )
                    errors.extend(item for item in bundle.errors if item not in errors)
            verdict = str(loaded.get("verdict") or "")
            evidence = list_of_dicts(loaded.get("evidence"))
            findings = list_of_dicts(loaded.get("findings"))
            deltas = list_of_dicts(loaded.get("deltas"))
            notes = [str(item) for item in loaded.get("notes") or [] if isinstance(item, str)]
            permission_errors = agent_control_plane_errors(
                loaded,
                allow_document_targets=role.output_mode == "document_index_bundle",
                manifest_targets=set(normalized_manifest.get("canonical_targets") or []),
            )
            errors.extend(permission_errors)
        else:
            adapter_result = adapt_legacy_result(
                root,
                normalized_manifest,
                loaded,
                registry=registry,
                result_path=result_relative,
            )
            adapter = adapter_result["adapter"]
            verdict = adapter_result["verdict"]
            evidence = adapter_result["evidence"]
            findings = adapter_result["findings"]
            deltas = adapter_result["deltas"]
            notes = adapter_result["notes"]
            errors.extend(adapter_result["errors"])
            warnings.extend(adapter_result["warnings"])
            need_human.extend(adapter_result["need_human_reasons"])
            errors.extend(agent_control_plane_errors(loaded, legacy_schema=source_schema))

    enriched_evidence = validate_current_evidence(root, registry, evidence, errors, need_human)
    if verdict == "need_human":
        need_human.append("agent_requested_need_human")
    validate_canonical_preconditions(
        root,
        normalized_manifest,
        deltas,
        enriched_evidence,
        errors,
        need_human,
    )
    if source_schema == "chapter_semantic_bundle_v1":
        validate_legacy_semantic_preconditions(
            root,
            raw_payload,
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
            "role_id": str(normalized_manifest.get("role_id") or ""),
        },
        "scope": normalized_manifest.get("scope") or {},
        "chapter_number": chapter_number,
        "verdict": verdict,
        "evidence": enriched_evidence,
        "findings": findings,
        "deltas": deltas,
        "notes": notes,
        "cli_context": {
            "manifest_schema_version": int(normalized_manifest.get("source_schema_version") or normalized_manifest["schema_version"]),
            "manifest_sha256": stable_json_hash(normalized_manifest),
            "result_path": result_relative,
            "result_sha256": result_hash,
            "planned_facts": planned_facts,
            "allowed_canonical_refs": allowed_refs,
            "canonical_targets": list(normalized_manifest.get("canonical_targets") or []),
            "source_registry": [asdict(item) for item in registry.records()],
        },
    }
    errors = unique(errors)
    warnings = unique(warnings)
    need_human = unique(need_human)
    status = "invalid" if errors else "need_human" if need_human else "valid"
    return AgentResultNormalization(
        schema=VALIDATION_REPORT_SCHEMA,
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
        next_command=(
            str(normalized_manifest.get("validate_command") or "")
            if status == "valid"
            else str(normalized_manifest.get("failure_next_command") or "")
        ),
    )


def write_agent_result_diagnostic(root: Path, result: AgentResultNormalization) -> AgentResultNormalization:
    """Write only a controlled workbench diagnostic; never update task lifecycle."""

    safe_task = re.sub(r"[^A-Za-z0-9_.-]+", "_", result.task_id).strip("_") or "unknown_task"
    digest = result.result_sha256[:12] or "no_result"
    path = root / "50_workbench" / "agent_tasks" / "diagnostics" / f"{safe_task}.{digest}.json"
    payload = asdict(result)
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
    for input_path in manifest.get("input_files") or []:
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


def adapt_legacy_result(
    root: Path,
    manifest: dict[str, Any],
    payload: dict[str, Any],
    *,
    registry: SourceRegistry,
    result_path: str,
) -> dict[str, Any]:
    schema = detect_source_schema(payload)
    base: dict[str, Any] = {
        "adapter": "unsupported_legacy",
        "verdict": "need_human",
        "evidence": [],
        "findings": [],
        "deltas": [],
        "notes": [],
        "errors": [],
        "warnings": [],
        "need_human_reasons": [],
    }
    expected_task = str(manifest.get("task_type") or "")
    if not schema and int(payload.get("schema_version") or 0) == 1:
        schema = {
            "graph_extract": "semantic_graph_update_v1",
            "memory_extract": "semantic_memory_v1",
            "character_memory": "character_memory_cards_v1",
        }.get(expected_task, "")
    if schema == "semantic_pacing_result_v2" and expected_task == "pacing_review":
        base["adapter"] = "semantic_pacing_result_v2"
        base["verdict"] = normalize_legacy_verdict(payload.get("verdict"))
        expected_chapter = int(manifest.get("chapter_number") or 0)
        if int(payload.get("chapter_number") or 0) != expected_chapter:
            base["errors"].append("semantic pacing chapter_number does not match the task manifest.")
        verdict = str(payload.get("verdict") or "").strip().lower()
        if verdict not in {"pass", "warning", "fail"}:
            base["errors"].append("semantic pacing verdict must be pass, warning, or fail.")
        tier = str(payload.get("tier") or "").strip().lower()
        if tier not in {"slow", "medium", "fast"}:
            base["errors"].append("semantic pacing tier must be slow, medium, or fast.")
        event_types = payload.get("event_types")
        if not isinstance(event_types, list) or not all(isinstance(item, str) and item.strip() for item in event_types):
            base["errors"].append("semantic pacing event_types must be a list of non-empty strings.")
        if not str(payload.get("tail_hook_quality") or "").strip():
            base["errors"].append("semantic pacing tail_hook_quality is required.")
        source_ref = str(payload.get("source_path") or "")
        source_record, source_error = registry.resolve(source_ref)
        source_text = ""
        if source_error:
            if source_error not in base["errors"]:
                base["errors"].append(source_error)
        else:
            assert source_record is not None
            source_ref = source_record.path
            source_text = (root / source_record.path).read_text(encoding="utf-8").lstrip("\ufeff")
            current_text_hash = sha256(source_text.encode("utf-8")).hexdigest()
            if str(payload.get("source_sha256") or "") != current_text_hash:
                base["errors"].append(f"source text hash does not match current file `{source_record.path}`.")
        issues = payload.get("issues")
        if not isinstance(issues, list):
            base["errors"].append("semantic pacing issues must be a list.")
            issues = []
        for index, item in enumerate(issues):
            if not isinstance(item, dict):
                base["errors"].append(f"semantic pacing issues[{index}] must be an object.")
                continue
            code = str(item.get("code") or "").strip()
            severity = str(item.get("severity") or "").strip().upper()
            message = str(item.get("message") or "").strip()
            recommendation = str(item.get("recommendation") or "").strip()
            fragment = str(item.get("evidence") or "").strip()
            if not code:
                base["errors"].append(f"semantic pacing issues[{index}] missing code.")
            if severity not in {"P0", "P1", "P2"}:
                base["errors"].append(f"semantic pacing issues[{index}] severity must be P0, P1, or P2.")
            if not message:
                base["errors"].append(f"semantic pacing issues[{index}] missing message.")
            if not recommendation:
                base["errors"].append(f"semantic pacing issues[{index}] missing recommendation.")
            refs: list[str] = []
            offsets = exact_fragment_offsets(source_text, fragment) if source_text and fragment else []
            if len(offsets) != 1:
                base["errors"].append(
                    f"semantic pacing issues[{index}].evidence must be a non-empty unique quote from the current chapter."
                )
            else:
                evidence_id = f"semantic_pacing_{index + 1}"
                start = offsets[0]
                base["evidence"].append(
                    {
                        "evidence_id": evidence_id,
                        "source_ref": source_ref,
                        "start": start,
                        "end": start + len(fragment),
                        "excerpt": fragment,
                    }
                )
                refs.append(evidence_id)
            base["findings"].append(
                legacy_finding(
                    code or f"semantic_pacing_{index + 1}",
                    message or "semantic pacing finding",
                    refs,
                    severity=severity,
                    recommendation=recommendation or "repair only the cited pacing issue",
                )
            )
        raw_warnings = payload.get("warnings")
        if not isinstance(raw_warnings, list) or not all(isinstance(item, str) for item in raw_warnings):
            base["errors"].append("semantic pacing warnings must be a list of strings.")
            raw_warnings = []
        notes = payload.get("notes")
        if notes is not None and not isinstance(notes, str):
            base["errors"].append("semantic pacing notes must be a string.")
        base["warnings"] = [str(item) for item in raw_warnings if str(item).strip()]
        base["notes"] = [str(notes)] if isinstance(notes, str) and notes.strip() else []
    elif schema == "reader_payoff_review_v1" and expected_task == "reader_payoff_review":
        base["adapter"] = "reader_payoff_review_v1"
        base["verdict"] = normalize_legacy_verdict(payload.get("verdict"))
        verify_legacy_source(payload, registry, base["errors"])
        source_ref = str(payload.get("source_path") or "")
        for index, item in enumerate(list_of_dicts(payload.get("evidence_spans"))):
            evidence_id = f"legacy_payoff_{index + 1}"
            base["evidence"].append(
                evidence_record(evidence_id, source_ref, item, excerpt_key="text")
            )
        flags = payload.get("fake_payoff_flags") or []
        for index, item in enumerate(flags):
            if isinstance(item, dict):
                code = str(item.get("code") or f"fake_payoff_{index + 1}")
                summary = str(item.get("message") or item.get("summary") or code)
            else:
                code = f"fake_payoff_{index + 1}"
                summary = str(item)
            refs = [base["evidence"][0]["evidence_id"]] if base["evidence"] else []
            base["findings"].append(legacy_finding(code, summary, refs, severity="P2"))
        base["notes"] = [str(item) for item in payload.get("recommendations") or [] if str(item).strip()]
    elif schema == "semantic_review_result_v1" and expected_task == "semantic_review":
        base["adapter"] = "semantic_review_result_v1"
        base["verdict"] = normalize_legacy_verdict(payload.get("verdict"))
        verify_legacy_source(payload, registry, base["errors"])
        source_ref = str(payload.get("source_path") or "")
        allowed_refs = {item["path"] for item in allowed_canonical_refs(registry)}
        for index, item in enumerate(list_of_dicts(payload.get("findings"))):
            evidence_id = f"legacy_semantic_{index + 1}"
            span = item.get("evidence_span") if isinstance(item.get("evidence_span"), dict) else {}
            refs = []
            if span:
                base["evidence"].append(evidence_record(evidence_id, source_ref, span, excerpt_key="text"))
                refs.append(evidence_id)
            canonical_refs = {str(value) for value in item.get("canonical_refs") or []}
            unknown_refs = sorted(canonical_refs - allowed_refs)
            if unknown_refs:
                base["errors"].append(
                    f"legacy semantic finding references undeclared canonical paths: {', '.join(unknown_refs)}"
                )
            base["findings"].append(
                legacy_finding(
                    str(item.get("code") or f"semantic_{index + 1}"),
                    str(item.get("message") or "semantic continuity finding"),
                    refs,
                    severity=str(item.get("severity") or "P2").upper(),
                    recommendation=str(item.get("recommendation") or "review the cited continuity evidence"),
                )
            )
        notes = payload.get("notes")
        base["notes"] = [notes] if isinstance(notes, str) and notes.strip() else []
    elif schema == "humanizer_semantic_review_v1" and expected_task == "humanize_semantic_review":
        base["adapter"] = "humanizer_semantic_review_v1"
        base["verdict"] = normalize_legacy_verdict(payload.get("verdict"))
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        candidate = payload.get("candidate") if isinstance(payload.get("candidate"), dict) else {}
        source_ref = str(source.get("path") or "")
        candidate_ref = str(candidate.get("path") or "")
        for label, path_value, hash_value in (
            ("source", source_ref, source.get("sha256")),
            ("candidate", candidate_ref, candidate.get("sha256")),
        ):
            record, resolution_error = registry.resolve(path_value)
            if resolution_error:
                base["errors"].append(resolution_error)
                continue
            assert record is not None
            current_text = (root / record.path).read_text(encoding="utf-8").lstrip("\ufeff")
            current_text_hash = sha256(current_text.encode("utf-8")).hexdigest()
            if str(hash_value or "") != current_text_hash:
                base["errors"].append(
                    f"{label} text hash does not match current file `{record.path}`."
                )
        allowed_refs = {item["path"] for item in allowed_canonical_refs(registry)}
        for index, item in enumerate(list_of_dicts(payload.get("fact_preservation"))):
            evidence_refs: list[str] = []
            for lane, ref in (("source", source_ref), ("candidate", candidate_ref)):
                span = item.get(f"{lane}_span")
                if not isinstance(span, dict):
                    continue
                evidence_id = f"legacy_humanizer_fact_{index + 1}_{lane}"
                base["evidence"].append(
                    evidence_record(evidence_id, ref, span, excerpt_key="text")
                )
                evidence_refs.append(evidence_id)
            unknown_refs = sorted(
                {str(value).replace("\\", "/") for value in item.get("canonical_refs") or []}
                - allowed_refs
            )
            if unknown_refs:
                base["errors"].append(
                    "humanizer semantic fact references undeclared canonical paths: "
                    + ", ".join(unknown_refs)
                )
            status = str(item.get("status") or "").lower()
            if status in {"changed", "uncertain"}:
                dimension = str(item.get("dimension") or f"fact_{index + 1}")
                base["findings"].append(
                    legacy_finding(
                        f"humanizer_{dimension}_{status}",
                        str(item.get("message") or f"Humanizer {dimension} is {status}."),
                        evidence_refs,
                        severity="P1" if status == "changed" else "P2",
                        recommendation="repair only the cited semantic drift",
                    )
                )
        for index, item in enumerate(list_of_dicts(payload.get("voice_checks"))):
            evidence_refs = []
            for span_index, span in enumerate(list_of_dicts(item.get("candidate_spans"))):
                evidence_id = f"legacy_humanizer_voice_{index + 1}_{span_index + 1}"
                base["evidence"].append(
                    evidence_record(evidence_id, candidate_ref, span, excerpt_key="text")
                )
                evidence_refs.append(evidence_id)
            status = str(item.get("status") or "").lower()
            if status in {"changed", "uncertain"}:
                character_id = str(item.get("character_id") or f"character_{index + 1}")
                base["findings"].append(
                    legacy_finding(
                        f"humanizer_voice_{character_id}_{status}",
                        str(item.get("message") or f"Humanizer voice is {status}."),
                        evidence_refs,
                        severity="P1" if status == "changed" else "P2",
                        recommendation="restore the cited character voice without changing events",
                    )
                )
        for index, item in enumerate(list_of_dicts(payload.get("ai_taste_findings"))):
            evidence_refs = []
            span = item.get("candidate_span")
            if isinstance(span, dict):
                evidence_id = f"legacy_humanizer_ai_taste_{index + 1}"
                base["evidence"].append(
                    evidence_record(evidence_id, candidate_ref, span, excerpt_key="text")
                )
                evidence_refs.append(evidence_id)
            base["findings"].append(
                legacy_finding(
                    str(item.get("code") or f"humanizer_ai_taste_{index + 1}"),
                    str(item.get("message") or "Humanizer AI-taste finding."),
                    evidence_refs,
                    severity=str(item.get("severity") or "P2").upper(),
                    recommendation=str(item.get("recommendation") or "repair the cited expression only"),
                )
            )
        notes = payload.get("notes")
        base["notes"] = [notes] if isinstance(notes, str) and notes.strip() else []
    elif schema == "chapter_semantic_bundle_v1" and expected_task == "chapter_semantic":
        base["adapter"] = "chapter_semantic_bundle_v1"
        base["verdict"] = "pass"
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        verify_source_fields(source.get("path"), source.get("sha256"), registry, base["errors"])
        source_ref = str(source.get("path") or "")
        for index, scene in enumerate(list_of_dicts(payload.get("scenes"))):
            base["evidence"].append(
                evidence_record(f"legacy_scene_{index + 1}", source_ref, scene, excerpt_key="excerpt")
            )
        for lane in (
            "events",
            "relationship_deltas",
            "character_deltas",
            "foreshadow_deltas",
            "world_deltas",
            "timeline_deltas",
        ):
            for index, item in enumerate(list_of_dicts(payload.get(lane))):
                refs: list[str] = []
                evidence_value = item.get("evidence")
                if isinstance(evidence_value, dict):
                    evidence_id = f"legacy_{lane}_{index + 1}"
                    base["evidence"].append(
                        evidence_record(evidence_id, source_ref, evidence_value, excerpt_key="excerpt")
                    )
                    refs.append(evidence_id)
                entity_id = legacy_entity_id(root, lane, item, index)
                base["deltas"].append(
                    {
                        "delta_id": f"legacy_{lane}_{index + 1}",
                        "entity_id": entity_id,
                        "field": lane,
                        "action": "update",
                        "old_state": legacy_old_state(lane, item),
                        "new_state": item,
                        "evidence_refs": refs,
                        "coverage": "changed",
                    }
                )
        if not base["deltas"]:
            base["deltas"].append(
                {
                    "delta_id": "legacy_chapter_coverage",
                    "entity_id": f"chapter:{int(payload.get('chapter_number') or 0):03d}",
                    "field": "chapter_digest",
                    "action": "declare",
                    "old_state": None,
                    "new_state": payload.get("chapter_digest") or {},
                    "evidence_refs": [item["evidence_id"] for item in base["evidence"]],
                    "coverage": "changed",
                }
            )
    elif schema.startswith("editorial_role_review_v") and expected_task == "editorial_review":
        base["adapter"] = schema
        base["verdict"] = normalize_legacy_verdict(payload.get("verdict"))
        source = preferred_chapter_source(registry)
        if source is None:
            base["need_human_reasons"].append("editorial_source_is_ambiguous")
        for index, item in enumerate(list_of_dicts(payload.get("items"))):
            refs: list[str] = []
            for evidence_index, fragment in enumerate(item.get("evidence") or []):
                if not isinstance(fragment, str) or not fragment:
                    continue
                if source is None:
                    continue
                text = (root / source.path).read_text(encoding="utf-8").lstrip("\ufeff")
                offsets = exact_fragment_offsets(text, fragment)
                if len(offsets) != 1:
                    base["need_human_reasons"].append(
                        f"editorial_evidence_not_unique:items[{index}].evidence[{evidence_index}]"
                    )
                    continue
                evidence_id = f"legacy_editorial_{index + 1}_{evidence_index + 1}"
                start = offsets[0]
                base["evidence"].append(
                    {
                        "evidence_id": evidence_id,
                        "source_ref": source.path,
                        "start": start,
                        "end": start + len(fragment),
                        "excerpt": fragment,
                    }
                )
                refs.append(evidence_id)
            severity = str(item.get("severity") or "P2").upper()
            if severity == "PASS":
                severity = "P3"
            base["findings"].append(
                legacy_finding(
                    str(item.get("code") or f"editorial_{index + 1}"),
                    str(item.get("message") or "editorial observation"),
                    refs,
                    severity=severity,
                    recommendation=str(item.get("recommendation") or "preserve or repair the cited behavior"),
                )
            )
        if int(payload.get("schema_version") or 1) == 1:
            base["warnings"].append("editorial_role_review_v1 normalized with unknown independence evidence.")
    elif schema == "semantic_graph_update_v1" and expected_task == "graph_extract":
        base["adapter"] = schema
        base["verdict"] = "pass"
        source_ref, source_text = verify_compatibility_source(payload, registry, base)
        for index, item in enumerate(list_of_dicts(payload.get("updates"))):
            refs = append_legacy_fragment_evidence(
                base,
                source_ref=source_ref,
                source_text=source_text,
                fragment=item.get("evidence_span"),
                evidence_id=f"legacy_graph_{index + 1}",
                label=f"updates[{index}].evidence_span",
            )
            source_id = str(item.get("source") or "")
            target_id = str(item.get("target") or "")
            entity_id = str(item.get("id") or "") or (
                f"relationship:{source_id}:{target_id}"
                if source_id and target_id
                else f"legacy:graph:{index + 1}"
            )
            base["deltas"].append(
                {
                    "delta_id": f"legacy_graph_{index + 1}",
                    "entity_id": entity_id,
                    "field": "legacy_graph_update",
                    "action": "declare",
                    "old_state": None,
                    "new_state": item,
                    "evidence_refs": refs,
                    "coverage": "changed",
                }
            )
        if not base["deltas"]:
            base["need_human_reasons"].append("legacy_graph_update_has_no_records")
    elif schema == "semantic_memory_v1" and expected_task == "memory_extract":
        base["adapter"] = schema
        base["verdict"] = "pass"
        source_ref, source_text = verify_compatibility_source(payload, registry, base)
        for index, scene in enumerate(list_of_dicts(payload.get("scenes"))):
            refs: list[str] = []
            for evidence_index, fragment in enumerate(scene.get("evidence") or []):
                refs.extend(
                    append_legacy_fragment_evidence(
                        base,
                        source_ref=source_ref,
                        source_text=source_text,
                        fragment=fragment,
                        evidence_id=f"legacy_memory_{index + 1}_{evidence_index + 1}",
                        label=f"scenes[{index}].evidence[{evidence_index}]",
                    )
                )
            base["deltas"].append(
                {
                    "delta_id": f"legacy_memory_scene_{index + 1}",
                    "entity_id": f"chapter:{int(payload.get('chapter_number') or 0):03d}:scene:{int(scene.get('scene') or index + 1)}",
                    "field": "legacy_scene_memory",
                    "action": "declare",
                    "old_state": None,
                    "new_state": scene,
                    "evidence_refs": refs,
                    "coverage": "changed",
                }
            )
        chapter_memory = payload.get("chapter_memory")
        if isinstance(chapter_memory, dict):
            refs: list[str] = []
            for evidence_index, fragment in enumerate(chapter_memory.get("evidence") or []):
                refs.extend(
                    append_legacy_fragment_evidence(
                        base,
                        source_ref=source_ref,
                        source_text=source_text,
                        fragment=fragment,
                        evidence_id=f"legacy_chapter_memory_{evidence_index + 1}",
                        label=f"chapter_memory.evidence[{evidence_index}]",
                    )
                )
            base["deltas"].append(
                {
                    "delta_id": "legacy_chapter_memory",
                    "entity_id": f"chapter:{int(payload.get('chapter_number') or 0):03d}",
                    "field": "legacy_chapter_memory",
                    "action": "declare",
                    "old_state": None,
                    "new_state": chapter_memory,
                    "evidence_refs": refs,
                    "coverage": "changed",
                }
            )
        if not base["deltas"]:
            base["need_human_reasons"].append("legacy_memory_has_no_records")
    elif schema == "character_memory_cards_v1" and expected_task == "character_memory":
        base["adapter"] = schema
        base["verdict"] = "pass"
        source_ref, source_text = verify_compatibility_source(payload, registry, base)
        for index, card in enumerate(list_of_dicts(payload.get("characters"))):
            refs: list[str] = []
            for evidence_index, fragment in enumerate(card.get("evidence") or []):
                refs.extend(
                    append_legacy_fragment_evidence(
                        base,
                        source_ref=source_ref,
                        source_text=source_text,
                        fragment=fragment,
                        evidence_id=f"legacy_character_{index + 1}_{evidence_index + 1}",
                        label=f"characters[{index}].evidence[{evidence_index}]",
                    )
                )
            character_id = str(card.get("character_id") or card.get("id") or "")
            if not character_id:
                base["errors"].append(f"characters[{index}] has no stable character_id.")
                character_id = f"legacy:character:{index + 1}"
            base["deltas"].append(
                {
                    "delta_id": f"legacy_character_{index + 1}",
                    "entity_id": character_id,
                    "field": "legacy_character_card",
                    "action": "declare",
                    "old_state": None,
                    "new_state": card,
                    "evidence_refs": refs,
                    "coverage": "changed",
                }
            )
        if not base["deltas"]:
            base["need_human_reasons"].append("legacy_character_memory_has_no_cards")
    else:
        base["need_human_reasons"].append(
            f"no deterministic legacy adapter for `{schema or 'unknown'}` and task `{expected_task}`"
        )
    if int(payload.get("chapter_number") or manifest.get("chapter_number") or 0) != int(manifest.get("chapter_number") or 0):
        base["errors"].append("legacy result chapter_number does not match manifest scope.")
    return base


def verify_compatibility_source(
    payload: dict[str, Any],
    registry: SourceRegistry,
    result: dict[str, Any],
) -> tuple[str, str]:
    """Resolve a v0.3.1 source while tolerating its historically absent hash."""

    source_ref = str(payload.get("source_path") or "")
    record, error = registry.resolve(source_ref)
    if error:
        result["errors"].append(error)
        return source_ref, ""
    assert record is not None
    declared_hash = str(payload.get("source_hash") or "")
    if declared_hash and declared_hash != record.sha256:
        result["errors"].append(f"source hash does not match current file `{record.path}`.")
    elif not declared_hash:
        result["warnings"].append(
            f"{result['adapter']} has no source_hash; compatibility validation used current `{record.path}`."
        )
    return record.path, (registry.root / record.path).read_text(encoding="utf-8").lstrip("\ufeff")


def append_legacy_fragment_evidence(
    result: dict[str, Any],
    *,
    source_ref: str,
    source_text: str,
    fragment: Any,
    evidence_id: str,
    label: str,
) -> list[str]:
    text = str(fragment or "").strip()
    if not text:
        result["errors"].append(f"{label} is empty.")
        return []
    offsets = exact_fragment_offsets(source_text, text)
    if len(offsets) != 1:
        result["need_human_reasons"].append(f"legacy_evidence_not_unique:{label}")
        return []
    start = offsets[0]
    result["evidence"].append(
        {
            "evidence_id": evidence_id,
            "source_ref": source_ref,
            "start": start,
            "end": start + len(text),
            "excerpt": text,
        }
    )
    return [evidence_id]


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
    chapter = int(manifest.get("chapter_number") or 0)
    enforce_existing = task_type in {
        "chapter_semantic",
        "graph_extract",
        "memory_extract",
        "character_memory",
    }
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


def validate_legacy_semantic_preconditions(
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
    legacy_schema: str = "",
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
    if legacy_schema:
        return unique(errors)
    return unique(errors)


def verify_legacy_source(payload: dict[str, Any], registry: SourceRegistry, errors: list[str]) -> None:
    verify_source_fields(payload.get("source_path"), payload.get("source_hash"), registry, errors)


def verify_source_fields(
    source_path: Any,
    source_hash: Any,
    registry: SourceRegistry,
    errors: list[str],
) -> None:
    record, resolution_error = registry.resolve(str(source_path or ""))
    if resolution_error:
        errors.append(resolution_error)
        return
    assert record is not None
    if str(source_hash or "") != record.sha256:
        errors.append(f"source hash does not match current file `{record.path}`.")


def load_declared_document(
    root: Path,
    manifest: dict[str, Any],
    document_file: str | Path | None,
    errors: list[str],
) -> tuple[Path | None, str]:
    markdown_outputs = [
        str(item) for item in manifest.get("allowed_output_paths") or [] if str(item).lower().endswith(".md")
    ]
    if len(markdown_outputs) != 1:
        errors.append("document/index result requires exactly one declared Markdown companion output.")
        return None, ""
    expected = resolve_under_root(root, markdown_outputs[0])
    if document_file is not None and resolve_under_root(root, document_file) != expected:
        errors.append("document file must exactly match the manifest Markdown output path.")
    if not expected.is_file():
        errors.append(f"declared Markdown document does not exist: {relative_path(root, expected)}")
        return None, ""
    try:
        return expected, expected.read_text(encoding="utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        errors.append("declared Markdown document must be valid UTF-8.")
        return None, ""


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
        "chapter_duty": ("chapter_duty", "duty"),
        "reader_gain": ("reader_gain", "reader_payoff"),
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


def detect_source_schema(payload: dict[str, Any]) -> str:
    schema = str(payload.get("schema") or "").strip()
    if schema:
        return schema
    version = payload.get("schema_version")
    role_id = str(payload.get("role_id") or "")
    if version in {1, 2} and role_id:
        return f"editorial_role_review_v{int(version)}"
    if version == 2 and all(
        key in payload
        for key in (
            "chapter_number",
            "source_path",
            "source_sha256",
            "verdict",
            "tier",
            "event_types",
            "tail_hook_quality",
            "issues",
            "warnings",
        )
    ):
        return "semantic_pacing_result_v2"
    if version == 1 and isinstance(payload.get("updates"), list) and payload.get("source") == "final":
        return "semantic_graph_update_v1"
    if version == 1 and all(key in payload for key in ("scenes", "chapter_memory", "graph_updates")):
        return "semantic_memory_v1"
    if version == 1 and isinstance(payload.get("characters"), list) and payload.get("source_path"):
        return "character_memory_cards_v1"
    return ""


def preferred_chapter_source(registry: SourceRegistry) -> SourceRecord | None:
    candidates = [
        item
        for item in registry.records()
        if item.path.startswith(("40_manuscript/draft/", "40_manuscript/final/", "50_workbench/repair_candidates/"))
        and item.path.lower().endswith(".md")
    ]
    return candidates[0] if len(candidates) == 1 else None


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


def legacy_entity_id(root: Path, lane: str, item: dict[str, Any], index: int) -> str:
    candidates = {
        "events": ("event_id", "id"),
        "relationship_deltas": ("relationship_id", "id"),
        "character_deltas": ("character_id", "id"),
        "foreshadow_deltas": ("thread_id", "id"),
        "world_deltas": ("entity_id", "fact_id", "id"),
        "timeline_deltas": ("event_id", "id"),
    }.get(lane, ("id",))
    for key in candidates:
        value = str(item.get(key) or "")
        if value:
            return value
    if lane == "relationship_deltas":
        source = str(item.get("source_id") or "")
        target = str(item.get("target_id") or "")
        if source and target:
            matches = [
                relationship_id
                for relationship_id, relationship in relationship_records(root).items()
                if str(
                    relationship.get("source_id")
                    or relationship.get("source")
                    or relationship.get("from")
                    or ""
                )
                == source
                and str(
                    relationship.get("target_id")
                    or relationship.get("target")
                    or relationship.get("to")
                    or ""
                )
                == target
            ]
            if len(matches) == 1:
                return matches[0]
            return f"relationship:{source}:{target}"
    return f"legacy:{lane}:{index + 1}"


def legacy_old_state(lane: str, item: dict[str, Any]) -> Any:
    if lane == "relationship_deltas":
        return item.get("prior_state")
    return item.get("old_state")


def evidence_record(
    evidence_id: str,
    source_ref: str,
    item: dict[str, Any],
    *,
    excerpt_key: str,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_ref": source_ref,
        "start": item.get("start"),
        "end": item.get("end"),
        "excerpt": item.get(excerpt_key),
    }


def legacy_finding(
    code: str,
    summary: str,
    evidence_refs: list[str],
    *,
    severity: str,
    recommendation: str = "review the cited evidence and repair only the supported issue",
) -> dict[str, Any]:
    return {
        "finding_id": re.sub(r"[^A-Za-z0-9_.:-]+", "_", code).strip("_") or "legacy_finding",
        "code": code,
        "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
        "summary": summary,
        "evidence_refs": evidence_refs,
        "recommendation": recommendation,
    }


def normalize_legacy_verdict(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"pass", "conditional_pass", "warning"}:
        return "pass"
    if normalized in {"needs_revision", "rewrite", "fail", "repair"}:
        return "repair"
    return "need_human"


def exact_fragment_offsets(text: str, fragment: str) -> list[int]:
    result: list[int] = []
    start = 0
    while True:
        offset = text.find(fragment, start)
        if offset < 0:
            return result
        result.append(offset)
        start = offset + 1


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
        schema=VALIDATION_REPORT_SCHEMA,
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
        next_command=str(manifest.get("failure_next_command") or ""),
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
