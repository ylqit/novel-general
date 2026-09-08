"""Platform policy snapshots, fanfiction rights decisions, and publication export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine import fanfiction_contracts
from longform_engine.config import ConfigDocument
from longform_engine.resources import resource_path
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import list_canonical_chapter_files, list_finalized_chapter_files


POLICY_REGISTRY_SCHEMA = "platform_publication_policy_registry_v2"
PREFLIGHT_SCHEMA = "platform_publication_preflight_v2"
RIGHTS_DECISION_SCHEMA = "fanfiction_publication_rights_decision_v1"
SUPPORTED_TARGETS = {"qidian_male", "fanqie_free"}
PROHIBITED_REPORT_FIELDS = {
    "ai_probability",
    "ai_detection_passed",
    "detection_passed",
    "bypass_detection",
    "human_percentage",
    "human_ratio",
    "ai_assisted",
}


@dataclass(frozen=True)
class PublicationRiskReportResult:
    report_file: str
    markdown_file: str
    warning_count: int
    blocking: bool


@dataclass(frozen=True)
class PublicationExportResult:
    target: str
    bundle_file: str
    report_file: str
    chapter_count: int
    blocking: bool


@dataclass(frozen=True)
class PublicationPreflightResult:
    target: str
    status: str
    report_file: str
    warning_count: int
    blocking: bool
    policy_snapshot_sha256: str


@dataclass(frozen=True)
class PublicationRightsDecisionResult:
    target: str
    decision: str
    decision_file: str
    decision_sha256: str
    risk_acknowledgement_required: bool


class PublicationExportBlockedError(ValueError):
    """Raised only when a concrete platform export lacks a current fanfiction decision."""


@dataclass(frozen=True)
class CreationProvenanceResult:
    target: str
    manifest_file: str
    chapter_count: int
    blocking: bool
    manifest_sha256: str


def record_publication_rights_decision(
    config: ConfigDocument,
    *,
    target: str,
    decision: str,
    approved_by: str,
    note: str,
) -> tuple[PublicationRightsDecisionResult, dict[str, Any]]:
    """Persist one human risk decision without storing Canon, prompts, or manuscript prose."""

    target = normalize_target(target)
    normalized_decision = str(decision or "").strip()
    if normalized_decision not in {"proceed", "hold"}:
        raise ValueError("decision must be proceed or hold")
    approver = str(approved_by or "").strip()
    if not approver:
        raise ValueError("approved_by must identify the human decision maker")
    normalized_note = str(note or "").strip()
    if len(normalized_note) > 2_000:
        raise ValueError("note must be at most 2000 characters and must not contain source text or manuscript prose")
    if str(config.data.get("creation", {}).get("mode") or "original") != "fanfiction":
        raise ValueError("publication rights decisions apply only to fanfiction projects")

    registry, registry_file, registry_hash = load_policy_registry()
    records = applicable_policy_records(registry, target)
    policy_snapshot = target_policy_snapshot(registry, target, records)
    binding, binding_errors = current_rights_binding(
        config,
        target=target,
        registry_file=registry_file,
        registry_hash=registry_hash,
        policy_snapshot=policy_snapshot,
    )
    if binding_errors:
        raise ValueError(
            "cannot bind a publication rights decision to the current project: "
            + "; ".join(binding_errors)
        )
    declarations = binding["source_rights_declarations"]
    risk_acknowledgement_required = any(
        item["rights_status"] == "unverified" or item["commercial_intent"] is True
        for item in declarations
    )
    if normalized_decision == "proceed" and risk_acknowledgement_required and not normalized_note:
        raise ValueError(
            "proceed requires a non-empty risk note when rights are unverified or commercial intent is declared"
        )

    payload = {
        "schema": RIGHTS_DECISION_SCHEMA,
        "target": target,
        "decision": normalized_decision,
        "approved_by": approver,
        "decided_at": utc_now(),
        "note": normalized_note,
        "risk_acknowledgement_required": risk_acknowledgement_required,
        "bindings": binding,
        "stores_source_text": False,
        "stores_prompt": False,
        "stores_manuscript_body": False,
        "claim_boundary": (
            "This is a human risk-awareness and workflow responsibility decision; it is not legal advice, "
            "a licence, rights-holder authorization, or a platform acceptance guarantee."
        ),
    }
    assert_no_prohibited_fields(payload)
    root = resolve_project_root(config)
    decision_file = publication_rights_decision_path(root, target)
    atomic_write_text(decision_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    append_publication_event(root, "fanfiction_publication_rights_decision_recorded", decision_file)
    result = PublicationRightsDecisionResult(
        target=target,
        decision=normalized_decision,
        decision_file=relative(root, decision_file),
        decision_sha256=file_hash(decision_file),
        risk_acknowledgement_required=risk_acknowledgement_required,
    )
    return result, payload


def publication_rights_decision_status(
    config: ConfigDocument,
    *,
    target: str,
) -> dict[str, Any]:
    """Describe whether the target's stored human decision still matches every bound input."""

    target = normalize_target(target)
    registry, registry_file, registry_hash = load_policy_registry()
    records = applicable_policy_records(registry, target)
    policy_snapshot = target_policy_snapshot(registry, target, records)
    return _publication_rights_decision_status(
        config,
        target=target,
        registry_file=registry_file,
        registry_hash=registry_hash,
        policy_snapshot=policy_snapshot,
        records=records,
    )


def _publication_rights_decision_status(
    config: ConfigDocument,
    *,
    target: str,
    registry_file: Path,
    registry_hash: str,
    policy_snapshot: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    root = resolve_project_root(config)
    decision_file = publication_rights_decision_path(root, target)
    stored = read_json(decision_file)
    creation_mode = str(config.data.get("creation", {}).get("mode") or "original")
    stale_policy_records = [str(item["record_id"]) for item in records if policy_record_is_stale(item)]
    base = {
        "target": target,
        "applicable": creation_mode == "fanfiction",
        "decision_file": relative(root, decision_file) if decision_file.is_file() else "",
        "decision_sha256": file_hash(decision_file),
        "policy_snapshot_sha256": policy_snapshot["snapshot_sha256"],
        "policy_record_ids": policy_snapshot["record_ids"],
        "stale_policy_record_ids": stale_policy_records,
    }
    if creation_mode != "fanfiction":
        return {
            **base,
            "rights_decision_status": "not_applicable",
            "decision_stale": False,
            "stale_reasons": [],
            "binding_errors": [],
            "export_blocking": False,
        }

    binding, binding_errors = current_rights_binding(
        config,
        target=target,
        registry_file=registry_file,
        registry_hash=registry_hash,
        policy_snapshot=policy_snapshot,
    )
    if not isinstance(stored, dict):
        return {
            **base,
            "rights_decision_status": "missing",
            "decision_stale": False,
            "stale_reasons": [],
            "binding_errors": binding_errors,
            "export_blocking": True,
        }
    validation_errors = validate_rights_decision_payload(stored, target=target)
    if validation_errors:
        return {
            **base,
            "rights_decision_status": "invalid",
            "decision_stale": True,
            "stale_reasons": validation_errors,
            "binding_errors": binding_errors,
            "export_blocking": True,
        }

    stored_bindings = stored["bindings"]
    stale_reasons = list(binding_errors)
    for field, reason in (
        ("effective_project_config_sha256", "project_config_changed"),
        ("source_canon_sha256", "source_canon_changed"),
        ("source_rights_declarations_sha256", "source_rights_declarations_changed"),
        ("platform_policy_snapshot_sha256", "platform_policy_snapshot_changed"),
    ):
        if stored_bindings.get(field) != binding.get(field):
            stale_reasons.append(reason)
    if stored_bindings.get("source_rights_declarations") != binding.get("source_rights_declarations"):
        stale_reasons.append("source_rights_declarations_changed")
    expected_risk_acknowledgement = any(
        item["rights_status"] == "unverified" or item["commercial_intent"] is True
        for item in binding["source_rights_declarations"]
    )
    if stored.get("risk_acknowledgement_required") is not expected_risk_acknowledgement:
        stale_reasons.append("risk_acknowledgement_requirement_changed")
    if stale_policy_records:
        stale_reasons.append("platform_policy_verification_expired")
    stale_reasons = sorted(set(stale_reasons))
    decision = str(stored["decision"])
    return {
        **base,
        "rights_decision_status": decision,
        "decision_stale": bool(stale_reasons),
        "stale_reasons": stale_reasons,
        "binding_errors": binding_errors,
        "approved_by": str(stored["approved_by"]),
        "decided_at": str(stored["decided_at"]),
        "note_present": bool(str(stored.get("note") or "").strip()),
        "export_blocking": decision != "proceed" or bool(stale_reasons),
    }


def publication_preflight(
    config: ConfigDocument,
    *,
    target: str,
    write: bool = True,
) -> tuple[PublicationPreflightResult, dict[str, Any]]:
    """Map public policy claims to current evidence without predicting platform acceptance."""

    target = normalize_target(target)
    root = resolve_project_root(config)
    registry, registry_file, registry_hash = load_policy_registry()
    records = applicable_policy_records(registry, target)
    policy_snapshot = target_policy_snapshot(registry, target, records)
    stale_records = [str(item["record_id"]) for item in records if policy_record_is_stale(item)]
    corpus = current_creation_fingerprint(root)
    revision = human_revision_coverage(root, corpus)
    observations = platform_observations(target, corpus, revision)
    rights = _publication_rights_decision_status(
        config,
        target=target,
        registry_file=registry_file,
        registry_hash=registry_hash,
        policy_snapshot=policy_snapshot,
        records=records,
    )
    status = (
        "policy_verification_required"
        if stale_records
        else "attention"
        if any(item["status"] == "attention" for item in observations)
        else "clear"
    )
    payload = {
        "schema": PREFLIGHT_SCHEMA,
        "target": target,
        "status": status,
        "blocking": rights["export_blocking"],
        "rights_decision_status": rights["rights_decision_status"],
        "decision_stale": rights["decision_stale"],
        "export_blocking": rights["export_blocking"],
        "rights_decision": rights,
        "corpus_sha256": corpus["corpus_sha256"],
        "chapter_hashes": corpus["chapters"],
        "human_revision_coverage": revision,
        "observations": observations,
        "policy_snapshot": {
            "registry_file": registry_file.as_posix(),
            "registry_sha256": registry_hash,
            "snapshot_sha256": policy_snapshot["snapshot_sha256"],
            "snapshot_verified_at": registry["snapshot_verified_at"],
            "record_ids": [str(item["record_id"]) for item in records],
            "stale_record_ids": stale_records,
        },
        "unknowns": sorted(
            {
                str(unknown)
                for item in records
                for unknown in item.get("unknown_items") or []
                if str(unknown).strip()
            }
        ),
        "policy_sources": [
            {
                "record_id": item["record_id"],
                "publisher": item["publisher"],
                "claim": item["claim"],
                "policy_dimension": item["policy_dimension"],
                "state": item["state"],
                "scope": item["scope"],
                "source_url": item["source_url"],
                "verified_at": item["verified_at"],
                "next_review_at": item["next_review_at"],
            }
            for item in records
        ],
        "disclosure_reminder": (
            "投稿时人工核验目标平台与现行法律要求的生成合成内容标识；"
            "引擎不会向正文自动插入声明，也不会删除或规避已有标识。"
        ),
        "claim_boundary": (
            "Content observations do not predict acceptance, expose an internal detector, or certify literary "
            "quality. Only the current human fanfiction rights decision and its policy/currentness bindings can "
            "block this target's export."
        ),
        "generated_at": utc_now(),
    }
    assert_no_prohibited_fields(payload)
    report_file = root / "80_exports" / "platform" / f"{target}.preflight.json"
    if write:
        atomic_write_text(report_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        append_publication_event(root, "platform_publication_preflight_generated", report_file)
    result = PublicationPreflightResult(
        target=target,
        status=status,
        report_file=relative(root, report_file),
        warning_count=sum(item["status"] == "attention" for item in observations) + len(stale_records),
        blocking=bool(rights["export_blocking"]),
        policy_snapshot_sha256=policy_snapshot["snapshot_sha256"],
    )
    return result, payload


def publication_preflight_status(config: ConfigDocument, *, target: str) -> dict[str, Any]:
    """Return current policy, rights-decision, and stored-report state for one target."""

    root = resolve_project_root(config)
    result, payload = publication_preflight(config, target=target, write=False)
    report = root / result.report_file
    stored = read_json(report)
    stale = bool(
        isinstance(stored, dict)
        and (
            stored.get("corpus_sha256") != payload["corpus_sha256"]
            or ((stored.get("policy_snapshot") or {}).get("snapshot_sha256") != result.policy_snapshot_sha256)
            or stored.get("rights_decision_status") != payload["rights_decision_status"]
            or stored.get("decision_stale") != payload["decision_stale"]
            or ((stored.get("rights_decision") or {}).get("decision_sha256")
                != payload["rights_decision"]["decision_sha256"])
        )
    )
    return {
        "target": result.target,
        "status": result.status,
        "blocking": result.blocking,
        "rights_decision_status": payload["rights_decision_status"],
        "decision_stale": payload["decision_stale"],
        "export_blocking": payload["export_blocking"],
        "rights_decision": payload["rights_decision"],
        "policy_snapshot": payload["policy_snapshot"],
        "report_file": result.report_file if report.is_file() else "",
        "report_stale": stale,
        "human_revision_coverage": payload["human_revision_coverage"],
        "policy_verification_required": result.status == "policy_verification_required",
    }


def creation_provenance_manifest(
    config: ConfigDocument,
    *,
    target: str,
) -> tuple[CreationProvenanceResult, dict[str, Any]]:
    """Write hash-only provenance; never store prose, prompts, or a human authorship ratio."""

    target = normalize_target(target)
    root = resolve_project_root(config)
    registry, registry_file, registry_hash = load_policy_registry()
    records = applicable_policy_records(registry, target)
    policy_snapshot = target_policy_snapshot(registry, target, records)
    chapters: list[dict[str, Any]] = []
    for chapter_number, final_file in list_finalized_chapter_files(root):
        finalization_file = final_file.with_suffix(".finalization.json")
        finalization = read_json(finalization_file)
        revision: dict[str, Any] = {}
        review: dict[str, Any] = {}
        if isinstance(finalization, dict):
            revision_value = finalization.get("human_author_revision")
            review_value = finalization.get("human_story_review")
            if isinstance(revision_value, dict):
                revision = revision_value
            if isinstance(review_value, dict):
                review = review_value
        selection = root / "50_workbench" / "intelligence_selections" / f"ch{chapter_number:03d}.selection.json"
        chapters.append(
            {
                "chapter_number": chapter_number,
                "final_file": relative(root, final_file),
                "final_sha256": file_hash(final_file),
                "direction_selection_file": relative(root, selection) if selection.is_file() else "",
                "direction_selection_sha256": file_hash(selection),
                "human_revision_validation_file": str(revision.get("validation_file") or ""),
                "human_revision_validation_sha256": str(revision.get("validation_sha256") or ""),
                "human_story_review_file": str(review.get("decision_file") or ""),
                "human_story_review_sha256": str(review.get("decision_sha256") or ""),
                "review_bundle_sha256": str(review.get("review_bundle_sha256") or ""),
                "voice_pair_ids": voice_pair_ids(root, chapter_number, file_hash(final_file)),
            }
        )
    from longform_engine.execution_origin import execution_origin
    payload = {
        "schema": "creation_provenance_manifest_v1",
        "execution_origin": execution_origin(root),
        "target": target,
        "production_method": "agent_candidate_then_evidence_bound_complete_human_revision_and_review",
        "chapters": chapters,
        "policy_snapshot": {
            "registry_file": registry_file.as_posix(),
            "registry_sha256": registry_hash,
            "snapshot_sha256": policy_snapshot["snapshot_sha256"],
            "record_ids": policy_snapshot["record_ids"],
        },
        "stores_full_prompt": False,
        "stores_manuscript_body": False,
        "claim_boundary": (
            "Hashes record workflow provenance; they do not prove literary quality, legal authorship, or platform acceptance."
        ),
        "generated_at": utc_now(),
    }
    assert_no_prohibited_fields(payload)
    manifest_file = root / "80_exports" / "platform" / f"{target}.creation_provenance.json"
    atomic_write_text(manifest_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    digest = file_hash(manifest_file)
    append_publication_event(root, "creation_provenance_manifest_generated", manifest_file)
    return (
        CreationProvenanceResult(target, relative(root, manifest_file), len(chapters), False, digest),
        payload,
    )


def publication_risk_report(config: ConfigDocument) -> PublicationRiskReportResult:
    root = resolve_project_root(config)
    creation_mode = str(config.data.get("creation", {}).get("mode") or "original")
    fanfiction = config.data.get("fanfiction", {}) if isinstance(config.data.get("fanfiction"), dict) else {}
    sources = [publication_source_record(item) for item in fanfiction.get("sources") or [] if isinstance(item, dict)]
    warnings: list[dict[str, Any]] = []
    if creation_mode == "fanfiction":
        for source in sources:
            if source["rights_status"] == "unverified":
                warnings.append(risk_warning("unverified_rights", source["source_id"], "Rights status is user-declared and unverified."))
            if source["commercial_intent"]:
                warnings.append(risk_warning("commercial_fanfiction", source["source_id"], "Commercial use may require additional rights and platform-policy review."))
        warnings.append(risk_warning("source_confusion", "", "Do not claim official authorization without supplied evidence."))
    preflights: dict[str, Any] = {}
    for target in sorted(SUPPORTED_TARGETS):
        result, payload = publication_preflight(config, target=target, write=True)
        preflights[target] = {
            "status": result.status,
            "blocking": result.blocking,
            "rights_decision_status": payload["rights_decision_status"],
            "decision_stale": payload["decision_stale"],
            "export_blocking": payload["export_blocking"],
            "report_file": result.report_file,
            "corpus_sha256": payload["corpus_sha256"],
        }
        if result.status != "clear":
            warnings.append(risk_warning("platform_preflight_attention", target, f"{target} preflight status is {result.status}."))
    payload = {
        "schema": "publication_risk_report_v2",
        "project_slug": str(config.data.get("project", {}).get("slug") or ""),
        "project_title": str(config.data.get("project", {}).get("title") or ""),
        "creation_mode": creation_mode,
        "production_method": "agent candidate followed by mandatory evidence-bound complete human revision",
        "continuity_mode": str(fanfiction.get("continuity_mode") or ""),
        "sources": sources,
        "rights_status_is_user_claimed": True,
        "engine_performed_legal_verification": False,
        "preflights": preflights,
        "warnings": warnings,
        "disclosure_reminder": "Verify current generated-content labeling duties at submission time; no statement is inserted into prose.",
        "blocking": False,
        "generated_at": utc_now(),
    }
    assert_no_prohibited_fields(payload)
    report_dir = root / "80_exports" / "publication_reports"
    report_file = report_dir / "publication_risk_report.json"
    markdown_file = report_dir / "publication_risk_report.md"
    atomic_write_text(report_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(markdown_file, render_publication_report(payload))
    append_publication_event(root, "publication_risk_report_generated", report_file)
    return PublicationRiskReportResult(relative(root, report_file), relative(root, markdown_file), len(warnings), False)


def export_publication_bundle(
    config: ConfigDocument,
    *,
    target: str,
    output: str | Path | None = None,
) -> PublicationExportResult:
    target = normalize_target(target)
    root = resolve_project_root(config)
    preflight, preflight_payload = publication_preflight(config, target=target, write=True)
    if preflight_payload["export_blocking"]:
        rights = preflight_payload["rights_decision"]
        reasons = list(rights.get("stale_reasons") or []) + list(rights.get("binding_errors") or [])
        if rights.get("rights_decision_status") == "missing":
            reasons.insert(0, "rights_decision_missing")
        elif rights.get("rights_decision_status") == "hold":
            reasons.insert(0, "rights_decision_hold")
        elif rights.get("rights_decision_status") == "invalid":
            reasons.insert(0, "rights_decision_invalid")
        detail = ", ".join(dict.fromkeys(str(item) for item in reasons if str(item)))
        raise PublicationExportBlockedError(
            f"publication export for {target} is blocked by the fanfiction rights decision gate"
            + (f": {detail}" if detail else "")
        )
    chapters = [path for _number, path in list_finalized_chapter_files(root)]
    if not chapters:
        raise ValueError("No finalized chapters are available for publication export.")
    if output:
        bundle_file = Path(output)
        if not bundle_file.is_absolute():
            bundle_file = root / bundle_file
    else:
        slug = str(config.data.get("project", {}).get("slug") or "novel")
        bundle_file = root / "80_exports" / "bundles" / f"{slug}.{target}.md"
    bundle_file = bundle_file.expanduser().resolve()
    try:
        bundle_file.relative_to((root / "80_exports").resolve())
    except ValueError as exc:
        raise ValueError("Publication bundle output must stay under 80_exports/.") from exc
    body = [f"# {str(config.data.get('project', {}).get('title') or 'Untitled')}", ""]
    from longform_engine.execution_origin import execution_origin
    if execution_origin(root)["simulated_human"]:
        body.extend(["> 自动演练材料：人工步骤由测试流程模拟，未通过真实人工文学或平台发布验收。", ""])
    for chapter in chapters:
        body.extend([chapter.read_text(encoding="utf-8").lstrip("\ufeff").rstrip(), "", ""])
    atomic_write_text(bundle_file, "\n".join(body).rstrip() + "\n")
    report = publication_risk_report(config)
    append_publication_event(root, "publication_bundle_exported", bundle_file)
    return PublicationExportResult(target, relative(root, bundle_file), report.report_file, len(chapters), False)


def platform_observations(target: str, corpus: dict[str, Any], revision: dict[str, Any]) -> list[dict[str, Any]]:
    observations = [
        observation("manuscript_available", "clear" if corpus["chapters"] else "attention", "At least one current draft or final chapter is available."),
        observation("human_revision_evidence", "clear" if revision["complete"] else "attention", "Every current chapter has a current human revision binding."),
        observation("format_integrity", "clear" if corpus["format_integrity"] else "attention", "Canonical chapter files are non-empty and readable."),
        observation("structure_and_continuity", "clear" if corpus["reviewed_chapters"] == len(corpus["chapters"]) and corpus["chapters"] else "attention", "Current chapters carry gate/review or finalization evidence."),
    ]
    if target == "fanqie_free":
        observations.extend(
            [
                observation("fanqie_rough_mass_production", "clear" if revision["complete"] else "attention", "Uses current human-revision evidence as a quality-process signal; no detector inference."),
                observation("fanqie_empty_padding", "clear" if corpus["reviewed_chapters"] == len(corpus["chapters"]) and corpus["chapters"] else "attention", "Uses scene, payoff, and anti-template reviews; no dialogue or pacing quota."),
            ]
        )
    else:
        observations.extend(
            [
                observation("qidian_original_source_provenance", "clear" if revision["complete"] else "attention", "Checks declared source and human revision provenance only."),
                observation("qidian_internal_ai_judgment", "attention", "No verifiable public blanket AI ban or internal detection algorithm was found in the bundled snapshot."),
            ]
        )
    return observations


def current_creation_fingerprint(root: Path) -> dict[str, Any]:
    finals = {number: path for number, path in list_finalized_chapter_files(root)}
    drafts = {number: path for number, path in list_canonical_chapter_files(root / "40_manuscript" / "draft")}
    selected = dict(drafts)
    selected.update(finals)
    chapters: list[dict[str, Any]] = []
    reviewed = 0
    format_integrity = True
    for number, path in sorted(selected.items()):
        lane = "final" if number in finals else "draft"
        text = path.read_text(encoding="utf-8")
        format_integrity = format_integrity and bool(text.strip())
        evidence_file = (
            path.with_suffix(".finalization.json")
            if lane == "final"
            else root / "50_workbench" / "gate_artifacts" / f"ch{number:03d}" / "gate_result.json"
        )
        evidence = read_json(evidence_file)
        digest = file_hash(path)
        if isinstance(evidence, dict) and (
            (lane == "final" and evidence.get("final_sha256") == digest)
            or (lane == "draft" and evidence.get("source_sha256") == digest)
        ):
            reviewed += 1
        chapters.append({"chapter_number": number, "lane": lane, "path": relative(root, path), "sha256": digest})
    rendered = json.dumps(chapters, ensure_ascii=False, sort_keys=True)
    return {
        "chapters": chapters,
        "corpus_sha256": sha256(rendered.encode("utf-8")).hexdigest(),
        "format_integrity": format_integrity,
        "reviewed_chapters": reviewed,
    }


def human_revision_coverage(root: Path, corpus: dict[str, Any]) -> dict[str, Any]:
    covered: list[int] = []
    missing: list[int] = []
    for chapter in corpus["chapters"]:
        number = int(chapter["chapter_number"])
        if chapter["lane"] == "final":
            payload = read_json(root / "40_manuscript" / "final" / f"ch{number:03d}.finalization.json")
        else:
            payload = read_json(root / "40_manuscript" / "draft" / f"ch{number:03d}.submission.json")
        binding = payload.get("human_author_revision") if isinstance(payload, dict) else None
        expected_schema = (
            "human_author_revision_finalization_binding_v4"
            if chapter["lane"] == "final"
            else "human_author_revision_submission_binding_v4"
        )
        validation = project_artifact(
            root, str((binding or {}).get("validation_file") or "")
        )
        final_lock = project_artifact(
            root, str((binding or {}).get("final_lock_file") or "")
        )
        current = bool(
            isinstance(binding, dict)
            and binding.get("schema") == expected_schema
            and binding.get("revision_candidate_sha256") == chapter["sha256"]
            and validation is not None
            and validation.is_file()
            and binding.get("validation_sha256") == file_hash(validation)
            and final_lock is not None
            and final_lock.is_file()
            and binding.get("final_lock_sha256") == file_hash(final_lock)
        )
        if current:
            covered.append(number)
        else:
            missing.append(number)
    return {"complete": bool(corpus["chapters"]) and not missing, "covered_chapters": covered, "missing_chapters": missing}


def project_artifact(root: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def voice_pair_ids(root: Path, chapter_number: int, final_hash: str) -> list[str]:
    bank = read_json(root / "10_bible" / "style_profiles" / "author_voice_edit_pairs.json")
    pairs: list[Any] = []
    if isinstance(bank, dict) and isinstance(bank.get("pairs"), list):
        pairs = bank["pairs"]
    return [
        str(item.get("pair_id") or "")
        for item in pairs
        if isinstance(item, dict)
        and item.get("active") is True
        and item.get("chapter_number") == chapter_number
        and item.get("final_sha256") == final_hash
    ]


def publication_rights_decision_path(root: Path, target: str) -> Path:
    return root / "50_workbench" / "publication" / "rights_decisions" / f"{target}.decision.json"


def canonical_json_hash(value: Any) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(rendered.encode("utf-8")).hexdigest()


def source_rights_declarations(config: ConfigDocument) -> list[dict[str, Any]]:
    fanfiction = config.data.get("fanfiction")
    fanfiction = fanfiction if isinstance(fanfiction, dict) else {}
    declarations: list[dict[str, Any]] = []
    for raw in fanfiction.get("sources") or []:
        if not isinstance(raw, dict):
            continue
        basis = {
            "source_id": str(raw.get("source_id") or ""),
            "rights_status": str(raw.get("rights_status") or "unverified"),
            "commercial_intent": bool(raw.get("commercial_intent")),
            "platform_policy_url": str(raw.get("platform_policy_url") or ""),
        }
        declarations.append({**basis, "declaration_sha256": canonical_json_hash(basis)})
    return sorted(declarations, key=lambda item: item["source_id"])


def target_policy_snapshot(
    registry: dict[str, Any],
    target: str,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    basis = {
        "schema": registry["schema"],
        "target": target,
        "snapshot_verified_at": registry["snapshot_verified_at"],
        "records": records,
    }
    return {
        "snapshot_sha256": canonical_json_hash(basis),
        "record_ids": [str(item["record_id"]) for item in records],
    }


def current_rights_binding(
    config: ConfigDocument,
    *,
    target: str,
    registry_file: Path,
    registry_hash: str,
    policy_snapshot: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    root = resolve_project_root(config)
    errors: list[str] = []
    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon_hash = ""
    try:
        canon = fanfiction_contracts.load_current_fanfiction_source_canon(config, root)
        canon_hash = canon.sha256
    except (fanfiction_contracts.FanfictionContractError, OSError, KeyError, ValueError) as exc:
        errors.append(f"source_canon_not_current: {exc}")
    declarations = source_rights_declarations(config)
    binding = {
        "effective_project_config_sha256": canonical_json_hash(config.data),
        "source_canon_file": relative(root, canon_path),
        "source_canon_sha256": canon_hash,
        "source_rights_declarations": declarations,
        "source_rights_declarations_sha256": canonical_json_hash(declarations),
        "platform_policy_registry_file": registry_file.as_posix(),
        "platform_policy_registry_sha256": registry_hash,
        "platform_policy_snapshot_sha256": policy_snapshot["snapshot_sha256"],
        "platform_policy_record_ids": policy_snapshot["record_ids"],
        "target": target,
    }
    return binding, errors


def validate_rights_decision_payload(payload: dict[str, Any], *, target: str) -> list[str]:
    errors: list[str] = []
    expected = {
        "schema",
        "target",
        "decision",
        "approved_by",
        "decided_at",
        "note",
        "risk_acknowledgement_required",
        "bindings",
        "stores_source_text",
        "stores_prompt",
        "stores_manuscript_body",
        "claim_boundary",
    }
    if set(payload) != expected or payload.get("schema") != RIGHTS_DECISION_SCHEMA:
        return ["rights_decision_schema_invalid"]
    if payload.get("target") != target:
        errors.append("rights_decision_target_mismatch")
    if payload.get("decision") not in {"proceed", "hold"}:
        errors.append("rights_decision_value_invalid")
    if not str(payload.get("approved_by") or "").strip() or not str(payload.get("decided_at") or "").strip():
        errors.append("rights_decision_human_identity_invalid")
    bindings = payload.get("bindings")
    required_bindings = {
        "effective_project_config_sha256",
        "source_canon_file",
        "source_canon_sha256",
        "source_rights_declarations",
        "source_rights_declarations_sha256",
        "platform_policy_registry_file",
        "platform_policy_registry_sha256",
        "platform_policy_snapshot_sha256",
        "platform_policy_record_ids",
        "target",
    }
    if not isinstance(bindings, dict) or set(bindings) != required_bindings:
        errors.append("rights_decision_bindings_invalid")
    if not isinstance(payload.get("risk_acknowledgement_required"), bool):
        errors.append("rights_decision_risk_acknowledgement_invalid")
    if (
        payload.get("decision") == "proceed"
        and payload.get("risk_acknowledgement_required") is True
        and not str(payload.get("note") or "").strip()
    ):
        errors.append("rights_decision_risk_note_missing")
    if not isinstance(payload.get("note"), str) or len(str(payload.get("note") or "")) > 2_000:
        errors.append("rights_decision_note_invalid")
    for flag in ("stores_source_text", "stores_prompt", "stores_manuscript_body"):
        if payload.get(flag) is not False:
            errors.append(f"rights_decision_{flag}_must_be_false")
    return errors


def load_policy_registry() -> tuple[dict[str, Any], Path, str]:
    path = resource_path("config", "platform_publication_policy_registry.json")
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema", "snapshot_verified_at", "records"}
        or payload.get("schema") != POLICY_REGISTRY_SCHEMA
        or not isinstance(payload.get("records"), list)
    ):
        raise ValueError("bundled platform publication policy registry is missing or invalid")
    required = {
        "record_id", "platform", "claim", "unknown_items", "source_type", "publisher",
        "source_url", "effective_at", "verified_at", "next_review_at", "scope",
        "policy_dimension", "state",
    }
    for index, item in enumerate(payload["records"]):
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"platform policy registry record {index} has invalid fields")
        if item.get("platform") not in {*SUPPORTED_TARGETS, "all"}:
            raise ValueError(f"platform policy registry record {index} has invalid platform")
        if item.get("policy_dimension") not in {
            "category_availability",
            "submission_eligibility",
            "signing_eligibility",
            "incentive_eligibility",
            "content_governance",
            "rights_risk",
            "disclosure_requirement",
            "quality_guidance",
            "public_tooling",
        }:
            raise ValueError(f"platform policy registry record {index} has invalid policy_dimension")
        if item.get("state") not in {"confirmed", "excluded", "unknown", "advisory"}:
            raise ValueError(f"platform policy registry record {index} has invalid state")
    return payload, Path("config/platform_publication_policy_registry.json"), file_hash(path)


def applicable_policy_records(registry: dict[str, Any], target: str) -> list[dict[str, Any]]:
    return [item for item in registry["records"] if item.get("platform") in {target, "all"}]


def policy_record_is_stale(record: dict[str, Any]) -> bool:
    try:
        return date.fromisoformat(str(record.get("next_review_at") or "")) < datetime.now(timezone.utc).date()
    except ValueError:
        return True


def normalize_target(target: str) -> str:
    value = str(target or "").strip()
    if value not in SUPPORTED_TARGETS:
        raise ValueError("target must be qidian_male or fanqie_free")
    return value


def publication_source_record(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(source.get("source_id") or ""),
        "title": str(source.get("title") or ""),
        "creator": str(source.get("creator") or ""),
        "canon_cutoff": str(source.get("canon_cutoff") or ""),
        "allowed_elements": [str(item) for item in source.get("allowed_elements") or []],
        "rights_status": str(source.get("rights_status") or "unverified"),
        "commercial_intent": bool(source.get("commercial_intent")),
        "platform_policy_url": str(source.get("platform_policy_url") or ""),
        "user_claimed": True,
    }


def observation(code: str, status: str, message: str) -> dict[str, Any]:
    return {"code": code, "status": status, "message": message, "blocking": False}


def risk_warning(code: str, source_id: str, message: str) -> dict[str, Any]:
    return {"code": code, "source_id": source_id, "message": message, "blocking": False}


def render_publication_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Publication Risk Report v2", "",
        f"- Creation mode: {payload['creation_mode']}",
        f"- Production method: {payload['production_method']}",
        f"- Blocking: {payload['blocking']}",
        "- Advisory only: this report does not certify detector results, platform acceptance, literary quality, or legal authorship.",
        "", "## Platform preflights", "",
    ]
    for target, item in payload["preflights"].items():
        lines.append(
            f"- {target}: {item['status']} "
            f"(rights={item['rights_decision_status']}, export_blocking={item['export_blocking']})"
        )
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- [{item['code']}] {item['message']}" for item in payload["warnings"])
    if not payload["warnings"]:
        lines.append("- None")
    lines.extend(["", payload["disclosure_reminder"], ""])
    return "\n".join(lines)


def assert_no_prohibited_fields(value: Any) -> None:
    if isinstance(value, dict):
        prohibited = PROHIBITED_REPORT_FIELDS.intersection(value)
        if prohibited:
            raise ValueError("publication payload contains prohibited detector/ratio fields: " + ", ".join(sorted(prohibited)))
        for item in value.values():
            assert_no_prohibited_fields(item)
    elif isinstance(value, list):
        for item in value:
            assert_no_prohibited_fields(item)


def append_publication_event(root: Path, event: str, artifact: Path) -> None:
    path = root / "70_runtime" / "provenance" / "publication_events.jsonl"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    record = {
        "schema": "publication_provenance_event_v1",
        "event": event,
        "artifact": relative(root, artifact),
        "artifact_sha256": file_hash(artifact),
        "stores_manuscript_body": False,
        "created_at": utc_now(),
    }
    prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
    atomic_write_text(path, prefix + json.dumps(record, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CreationProvenanceResult",
    "PublicationExportResult",
    "PublicationExportBlockedError",
    "PublicationPreflightResult",
    "PublicationRiskReportResult",
    "PublicationRightsDecisionResult",
    "creation_provenance_manifest",
    "export_publication_bundle",
    "publication_preflight",
    "publication_preflight_status",
    "publication_rights_decision_status",
    "publication_risk_report",
    "record_publication_rights_decision",
]
