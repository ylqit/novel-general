"""Advisory platform policy snapshots, provenance, and publication export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.resources import resource_path
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import list_canonical_chapter_files, list_finalized_chapter_files


POLICY_REGISTRY_SCHEMA = "platform_publication_policy_registry_v1"
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
class CreationProvenanceResult:
    target: str
    manifest_file: str
    chapter_count: int
    blocking: bool
    manifest_sha256: str


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
    stale_records = [str(item["record_id"]) for item in records if policy_record_is_stale(item)]
    corpus = current_creation_fingerprint(root)
    revision = human_revision_coverage(root, corpus)
    observations = platform_observations(target, corpus, revision)
    status = (
        "policy_verification_required"
        if stale_records
        else "attention"
        if any(item["status"] == "attention" for item in observations)
        else "clear"
    )
    payload = {
        "schema": "platform_publication_preflight_v1",
        "target": target,
        "status": status,
        "blocking": False,
        "corpus_sha256": corpus["corpus_sha256"],
        "chapter_hashes": corpus["chapters"],
        "human_revision_coverage": revision,
        "observations": observations,
        "policy_snapshot": {
            "registry_file": registry_file.as_posix(),
            "registry_sha256": registry_hash,
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
            "This advisory does not predict acceptance, expose an internal detector, or certify literary quality."
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
        blocking=False,
        policy_snapshot_sha256=registry_hash,
    )
    return result, payload


def publication_preflight_status(config: ConfigDocument, *, target: str) -> dict[str, Any]:
    """Return current advisory state and whether a previously written report has gone stale."""

    root = resolve_project_root(config)
    result, payload = publication_preflight(config, target=target, write=False)
    report = root / result.report_file
    stored = read_json(report)
    stale = bool(
        isinstance(stored, dict)
        and (
            stored.get("corpus_sha256") != payload["corpus_sha256"]
            or ((stored.get("policy_snapshot") or {}).get("registry_sha256") != result.policy_snapshot_sha256)
        )
    )
    return {
        "target": result.target,
        "status": result.status,
        "blocking": False,
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
    _registry, registry_file, registry_hash = load_policy_registry()
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
    payload = {
        "schema": "creation_provenance_manifest_v1",
        "target": target,
        "production_method": "agent_candidate_then_evidence_bound_complete_human_revision_and_review",
        "chapters": chapters,
        "policy_snapshot": {"registry_file": registry_file.as_posix(), "registry_sha256": registry_hash},
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
            "blocking": False,
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


def export_publication_bundle(config: ConfigDocument, *, output: str | Path | None = None) -> PublicationExportResult:
    root = resolve_project_root(config)
    chapters = [path for _number, path in list_finalized_chapter_files(root)]
    if not chapters:
        raise ValueError("No finalized chapters are available for publication export.")
    if output:
        bundle_file = Path(output)
        if not bundle_file.is_absolute():
            bundle_file = root / bundle_file
    else:
        slug = str(config.data.get("project", {}).get("slug") or "novel")
        bundle_file = root / "80_exports" / "bundles" / f"{slug}.md"
    bundle_file = bundle_file.expanduser().resolve()
    try:
        bundle_file.relative_to((root / "80_exports").resolve())
    except ValueError as exc:
        raise ValueError("Publication bundle output must stay under 80_exports/.") from exc
    body = [f"# {str(config.data.get('project', {}).get('title') or 'Untitled')}", ""]
    for chapter in chapters:
        body.extend([chapter.read_text(encoding="utf-8").lstrip("\ufeff").rstrip(), "", ""])
    atomic_write_text(bundle_file, "\n".join(body).rstrip() + "\n")
    report = publication_risk_report(config)
    append_publication_event(root, "publication_bundle_exported", bundle_file)
    return PublicationExportResult(relative(root, bundle_file), report.report_file, len(chapters), False)


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
        if isinstance(binding, dict) and str(binding.get("validation_sha256") or ""):
            covered.append(number)
        else:
            missing.append(number)
    return {"complete": bool(corpus["chapters"]) and not missing, "covered_chapters": covered, "missing_chapters": missing}


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
    }
    for index, item in enumerate(payload["records"]):
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError(f"platform policy registry record {index} has invalid fields")
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
        lines.append(f"- {target}: {item['status']} (blocking=false)")
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
    "PublicationPreflightResult",
    "PublicationRiskReportResult",
    "creation_provenance_manifest",
    "export_publication_bundle",
    "publication_preflight",
    "publication_preflight_status",
    "publication_risk_report",
]
