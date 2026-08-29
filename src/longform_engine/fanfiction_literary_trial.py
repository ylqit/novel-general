"""Hash-bound two-route, twenty-chapter fanfiction literary acceptance protocol."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
from typing import Any

from longform_engine.blind_review import (
    chapter_merkle_root,
    clean_identifier,
    find_chapter_file,
    payload_sha256,
)
from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


TRIAL_SCHEMA = "fanfiction_literary_trial_v1"
PRIVATE_MAPPING_SCHEMA = "fanfiction_literary_trial_private_mapping_v1"
GATE_REPORT_SCHEMA = "fanfiction_literary_trial_gate_report_v1"
REVIEW_SUBMISSION_SCHEMA = "fanfiction_literary_trial_review_v1"
AGGREGATE_SCHEMA = "fanfiction_literary_trial_aggregate_v1"
RESOLUTION_SCHEMA = "fanfiction_literary_trial_disagreement_resolution_v1"
EVIDENCE_SCHEMA = "fanfiction_literary_evidence_manifest_v1"
CHAPTER_COUNT = 20
ROUTE_FAMILIES = ("oc_si_progression", "canon_character_centered")
SCORE_METRICS = (
    "canon_fidelity",
    "character_agency",
    "canon_recognition_payoff",
    "original_mainline_ownership",
    "divergence_causality",
    "in_world_cost_and_countermeasure",
    "chapter_reading_value",
    "continued_reading_desire",
)
CORE_METRICS = (
    "canon_fidelity",
    "character_agency",
    "original_mainline_ownership",
    "continued_reading_desire",
)
FAILURE_CODES = (
    "encyclopedic_exposition",
    "mechanical_canon_recap",
    "canon_character_duty_theft",
    "template_event_loop",
)
DISQUALIFYING_CODES = (
    "untraceable_canon_assertion",
    "continuous_source_text_reproduction",
    "fabricated_authorization_claim",
)


@dataclass(frozen=True)
class FanfictionLiteraryTrialResult:
    trial_id: str
    public_manifest: str
    private_mapping: str
    pack_hash: str
    blind_ids: tuple[str, ...]


@dataclass(frozen=True)
class FanfictionLiteraryAggregateResult:
    trial_id: str
    aggregate_file: str
    aggregate_sha256: str
    threshold_conclusion: str
    material_disagreement_count: int
    literary_evidence_ready: bool


def create_fanfiction_literary_trial(
    config: ConfigDocument,
    *,
    trial_id: str,
    oc_si_source_dir: str | Path,
    oc_si_gate_report: str | Path,
    canon_character_source_dir: str | Path,
    canon_character_gate_report: str | Path,
    seed: str,
) -> FanfictionLiteraryTrialResult:
    """Create one anonymous dual-route pack after verifying both hash-only gate reports."""

    if str(config.data.get("creation", {}).get("mode") or "original") != "fanfiction":
        raise ValueError("fanfiction literary trials require creation.mode=fanfiction")
    normalized_id = clean_identifier(trial_id, field="trial_id")
    if not str(seed or "").strip():
        raise ValueError("seed must be non-empty")
    root = resolve_project_root(config)
    trial_root = fanfiction_literary_trial_root(root, normalized_id)
    if trial_root.exists() and any(trial_root.iterdir()):
        raise ValueError(f"fanfiction literary trial already exists: {trial_root}")

    route_inputs = {
        "oc_si_progression": (Path(oc_si_source_dir).expanduser().resolve(), Path(oc_si_gate_report).expanduser().resolve()),
        "canon_character_centered": (
            Path(canon_character_source_dir).expanduser().resolve(),
            Path(canon_character_gate_report).expanduser().resolve(),
        ),
    }
    source_records: dict[str, dict[str, Any]] = {}
    for route_family, (source_dir, gate_file) in route_inputs.items():
        chapters = source_chapter_records(source_dir)
        gate_report = read_object(gate_file)
        gate_errors = validate_gate_report(gate_report, route_family=route_family, chapters=chapters)
        if gate_errors:
            raise ValueError(f"{route_family} gate report is invalid: " + "; ".join(gate_errors))
        source_records[route_family] = {
            "route_family": route_family,
            "source_dir": str(source_dir),
            "source_merkle_root": chapter_merkle_root(chapters),
            "chapters": chapters,
            "gate_report_file": str(gate_file),
            "gate_report_sha256": file_hash(gate_file),
        }

    ordered_routes = sorted(
        ROUTE_FAMILIES,
        key=lambda route: sha256(f"{seed}:{normalized_id}:{route}".encode("utf-8")).hexdigest(),
    )
    blind_map = {
        f"entry-{chr(ord('a') + index)}": route
        for index, route in enumerate(ordered_routes)
    }
    public_root = trial_root / "public"
    public_entries: list[dict[str, Any]] = []
    for blind_id, route_family in blind_map.items():
        target_dir = public_root / blind_id
        public_chapters: list[dict[str, Any]] = []
        for chapter in source_records[route_family]["chapters"]:
            source = Path(chapter["source_path"])
            body = source.read_text(encoding="utf-8")
            target = target_dir / f"ch{int(chapter['chapter_number']):03d}.md"
            atomic_write_text(target, body)
            public_chapters.append(
                {
                    "chapter_number": int(chapter["chapter_number"]),
                    "path": f"{blind_id}/{target.name}",
                    "sha256": chapter["sha256"],
                    "character_count": chapter["character_count"],
                }
            )
        public_entries.append({"blind_id": blind_id, "chapters": public_chapters})

    public_basis = {
        "schema": TRIAL_SCHEMA,
        "trial_id": normalized_id,
        "chapter_count_per_route": CHAPTER_COUNT,
        "blind_ids": sorted(blind_map),
        "score_scale": {"min": 1, "max": 5, "higher_is_better": True},
        "score_metrics": list(SCORE_METRICS),
        "failure_codes": list(FAILURE_CODES),
        "disqualifying_codes": list(DISQUALIFYING_CODES),
        "entries": sorted(public_entries, key=lambda item: item["blind_id"]),
        "reviewer_count_required": 3,
        "instructions": [
            "评审不得参与任一路线生成，也不得查看私有路线映射。",
            "先独立阅读并评分，再提交失败模式与可取消资格的问题。",
            "系统使用三人中位数，不选择对作品更有利的个别意见。",
        ],
        "stores_prompt": False,
        "stores_source_canon": False,
    }
    public_payload = {**public_basis, "pack_hash": payload_sha256(public_basis)}
    public_manifest = public_root / "manifest.json"
    write_json(public_manifest, public_payload)
    atomic_write_text(public_root / "REVIEW_INSTRUCTIONS.md", render_instructions(public_payload))

    private_basis = {
        "schema": PRIVATE_MAPPING_SCHEMA,
        "trial_id": normalized_id,
        "pack_hash": public_payload["pack_hash"],
        "blind_route_mapping": blind_map,
        "routes": source_records,
        "stores_manuscript_body": False,
        "created_at": utc_now(),
    }
    private_payload = {**private_basis, "mapping_sha256": payload_sha256(private_basis)}
    private_mapping = trial_root / "private_mapping.json"
    write_json(private_mapping, private_payload)
    return FanfictionLiteraryTrialResult(
        trial_id=normalized_id,
        public_manifest=relative(root, public_manifest),
        private_mapping=relative(root, private_mapping),
        pack_hash=str(public_payload["pack_hash"]),
        blind_ids=tuple(sorted(blind_map)),
    )


def create_fanfiction_literary_review_template(
    config: ConfigDocument,
    *,
    trial_id: str,
    reviewer_id: str,
) -> str:
    root = resolve_project_root(config)
    normalized_id = clean_identifier(trial_id, field="trial_id")
    normalized_reviewer = clean_identifier(reviewer_id, field="reviewer_id")
    manifest = load_public_manifest(root, normalized_id)
    payload = {
        "schema": REVIEW_SUBMISSION_SCHEMA,
        "trial_id": normalized_id,
        "pack_hash": manifest["pack_hash"],
        "reviewer": {
            "reviewer_id": normalized_reviewer,
            "instance_id": "FILL_UNIQUE_HUMAN_INSTANCE_ID",
            "participated_in_generation": False,
            "saw_private_mapping": False,
            "conflict_of_interest": False,
        },
        "attestation_note": "FILL: explain reviewer independence and blind-review conditions",
        "entries": [
            {
                "blind_id": blind_id,
                "scores": {metric: None for metric in SCORE_METRICS},
                "failure_findings": [],
                "disqualifying_findings": [],
                "notes": "",
            }
            for blind_id in manifest["blind_ids"]
        ],
        "submitted_at": "FILL_ISO_8601",
    }
    path = fanfiction_literary_trial_root(root, normalized_id) / "review_templates" / f"{normalized_reviewer}.json"
    write_json(path, payload)
    return relative(root, path)


def submit_fanfiction_literary_review(
    config: ConfigDocument,
    *,
    trial_id: str,
    reviewer_id: str,
    file_path: str | Path,
) -> str:
    root = resolve_project_root(config)
    normalized_id = clean_identifier(trial_id, field="trial_id")
    normalized_reviewer = clean_identifier(reviewer_id, field="reviewer_id")
    manifest = load_public_manifest(root, normalized_id)
    source = Path(file_path).expanduser().resolve()
    payload = read_object(source)
    errors = validate_review_submission(
        payload,
        manifest=manifest,
        trial_id=normalized_id,
        reviewer_id=normalized_reviewer,
    )
    if errors:
        raise ValueError("fanfiction literary review is invalid: " + "; ".join(errors))
    basis = dict(payload)
    basis.pop("submission_sha256", None)
    stored = {**basis, "submission_sha256": payload_sha256(basis)}
    target = fanfiction_literary_trial_root(root, normalized_id) / "reviews" / f"{normalized_reviewer}.json"
    if target.exists():
        raise ValueError(f"reviewer already submitted: {normalized_reviewer}")
    write_json(target, stored)
    return relative(root, target)


def literary_assessment_from_submissions(
    mapping: dict[str, Any],
    submissions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recompute the fixed median assessment used by both aggregation and live evidence audit."""

    by_route: dict[str, dict[str, list[float]]] = {
        route: {metric: [] for metric in SCORE_METRICS} for route in ROUTE_FAMILIES
    }
    failure_findings: list[dict[str, Any]] = []
    disqualifying_findings: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    blind_map = mapping["blind_route_mapping"]
    for submission in submissions:
        reviewer_id = str(submission["reviewer"]["reviewer_id"])
        for entry in submission["entries"]:
            route = str(blind_map[entry["blind_id"]])
            for metric in SCORE_METRICS:
                by_route[route][metric].append(float(entry["scores"][metric]))
            failure_findings.extend(
                {**item, "route_family": route, "reviewer_id": reviewer_id}
                for item in entry["failure_findings"]
            )
            disqualifying_findings.extend(
                {**item, "route_family": route, "reviewer_id": reviewer_id}
                for item in entry["disqualifying_findings"]
            )
    medians = {
        route: {metric: round(float(median(scores)), 3) for metric, scores in metrics.items()}
        for route, metrics in by_route.items()
    }
    for route, metrics in by_route.items():
        for metric, scores in metrics.items():
            threshold = 4.0 if metric in CORE_METRICS else 3.5
            score_range = max(scores) - min(scores)
            if len(set(scores)) > 1:
                disagreements.append(
                    {
                        "disagreement_id": f"{route}:{metric}",
                        "route_family": route,
                        "metric": metric,
                        "scores": scores,
                        "median": medians[route][metric],
                        "threshold": threshold,
                        "material": score_range >= 2.0 or (min(scores) < threshold <= max(scores)),
                        "resolution_status": "human_resolution_required",
                    }
                )
    threshold_passed = all(
        score >= (4.0 if metric in CORE_METRICS else 3.5)
        for metrics in medians.values()
        for metric, score in metrics.items()
    )
    material_ids = [
        str(item["disagreement_id"]) for item in disagreements if item["material"]
    ]
    return {
        "route_metric_medians": medians,
        "failure_findings": failure_findings,
        "disqualifying_findings": disqualifying_findings,
        "disagreements": disagreements,
        "material_disagreement_ids": material_ids,
        "threshold_conclusion": (
            "pass" if threshold_passed and not disqualifying_findings else "fail"
        ),
    }


def aggregate_fanfiction_literary_trial(
    config: ConfigDocument,
    *,
    trial_id: str,
) -> FanfictionLiteraryAggregateResult:
    root = resolve_project_root(config)
    normalized_id = clean_identifier(trial_id, field="trial_id")
    trial_root = fanfiction_literary_trial_root(root, normalized_id)
    manifest = load_public_manifest(root, normalized_id)
    mapping = load_private_mapping(root, normalized_id, manifest)
    submissions = load_review_submissions(trial_root, manifest, normalized_id)
    if len(submissions) != 3:
        raise ValueError("fanfiction literary trial requires exactly three independent human reviewers")
    reviewer_instances = [str(item["reviewer"]["instance_id"]) for item in submissions]
    if len(set(reviewer_instances)) != 3:
        raise ValueError("fanfiction literary reviewers must use three unique human instance IDs")

    assessment = literary_assessment_from_submissions(mapping, submissions)
    material = assessment["material_disagreement_ids"]
    threshold_conclusion = assessment["threshold_conclusion"]
    aggregate_basis = {
        "schema": AGGREGATE_SCHEMA,
        "trial_id": normalized_id,
        "pack_hash": manifest["pack_hash"],
        "mapping_sha256": mapping["mapping_sha256"],
        "reviewer_ids": sorted(str(item["reviewer"]["reviewer_id"]) for item in submissions),
        "reviewer_instances": sorted(reviewer_instances),
        "submission_sha256": sorted(str(item["submission_sha256"]) for item in submissions),
        "aggregation": "three-independent-reviewer median; no favorable-opinion selection",
        "route_metric_medians": assessment["route_metric_medians"],
        "failure_findings": assessment["failure_findings"],
        "disqualifying_findings": assessment["disqualifying_findings"],
        "disagreements": assessment["disagreements"],
        "material_disagreement_ids": material,
        "threshold_conclusion": threshold_conclusion,
        "conclusion": "pending_human_resolution" if material else threshold_conclusion,
        "stores_manuscript_body": False,
    }
    aggregate = {**aggregate_basis, "aggregate_sha256": payload_sha256(aggregate_basis)}
    aggregate_file = trial_root / "aggregate.json"
    write_json(aggregate_file, aggregate)
    evidence_ready = False
    if threshold_conclusion == "pass" and not material:
        write_fanfiction_literary_evidence_manifest(root, normalized_id, manifest, mapping, aggregate, None)
        evidence_ready = True
    return FanfictionLiteraryAggregateResult(
        trial_id=normalized_id,
        aggregate_file=relative(root, aggregate_file),
        aggregate_sha256=aggregate["aggregate_sha256"],
        threshold_conclusion=threshold_conclusion,
        material_disagreement_count=len(material),
        literary_evidence_ready=evidence_ready,
    )


def resolve_fanfiction_literary_disagreements(
    config: ConfigDocument,
    *,
    trial_id: str,
    decided_by: str,
    resolutions: list[dict[str, str]],
) -> dict[str, Any]:
    """Acknowledge every material panel disagreement without replacing any reviewer score."""

    root = resolve_project_root(config)
    normalized_id = clean_identifier(trial_id, field="trial_id")
    human = str(decided_by or "").strip()
    if not human:
        raise ValueError("decided_by must identify the human resolving panel disagreement")
    trial_root = fanfiction_literary_trial_root(root, normalized_id)
    manifest = load_public_manifest(root, normalized_id)
    mapping = load_private_mapping(root, normalized_id, manifest)
    aggregate = read_object(trial_root / "aggregate.json")
    validate_aggregate_hash(aggregate, trial_id=normalized_id)
    required_ids = set(str(item) for item in aggregate.get("material_disagreement_ids") or [])
    by_id = {
        str(item.get("disagreement_id") or ""): item
        for item in resolutions
        if isinstance(item, dict)
    }
    if set(by_id) != required_ids or len(by_id) != len(resolutions):
        raise ValueError("resolutions must cover every material disagreement exactly once")
    for disagreement_id, item in by_id.items():
        if (
            item.get("decision") != "acknowledge_panel_median_without_score_override"
            or not str(item.get("note") or "").strip()
        ):
            raise ValueError(f"resolution is invalid for {disagreement_id}")
    basis = {
        "schema": RESOLUTION_SCHEMA,
        "trial_id": normalized_id,
        "aggregate_sha256": aggregate["aggregate_sha256"],
        "decided_by": human,
        "decided_at": utc_now(),
        "resolutions": [by_id[item] for item in sorted(by_id)],
        "score_override_permitted": False,
    }
    resolution = {**basis, "resolution_sha256": payload_sha256(basis)}
    resolution_file = trial_root / "disagreement_resolution.json"
    write_json(resolution_file, resolution)
    if aggregate.get("threshold_conclusion") == "pass":
        write_fanfiction_literary_evidence_manifest(
            root, normalized_id, manifest, mapping, aggregate, resolution
        )
    return resolution


def fanfiction_literary_trial_status(config: ConfigDocument, *, trial_id: str) -> dict[str, Any]:
    root = resolve_project_root(config)
    normalized_id = clean_identifier(trial_id, field="trial_id")
    trial_root = fanfiction_literary_trial_root(root, normalized_id)
    aggregate = read_object(trial_root / "aggregate.json")
    evidence = read_object(root / "70_runtime" / "literary_evidence" / "manifest.json")
    evidence_errors = (
        validate_fanfiction_literary_evidence(root, evidence)
        if evidence.get("schema") == EVIDENCE_SCHEMA and evidence.get("trial_id") == normalized_id
        else ["fanfiction_literary_evidence_manifest_missing"]
    )
    return {
        "schema": "fanfiction_literary_trial_status_v1",
        "trial_id": normalized_id,
        "pack_exists": (trial_root / "public" / "manifest.json").is_file(),
        "review_count": len(list((trial_root / "reviews").glob("*.json"))) if (trial_root / "reviews").is_dir() else 0,
        "aggregate_conclusion": str(aggregate.get("conclusion") or "missing"),
        "threshold_conclusion": str(aggregate.get("threshold_conclusion") or "missing"),
        "material_disagreement_ids": list(aggregate.get("material_disagreement_ids") or []),
        "literary_evidence_ready": not evidence_errors,
        "literary_evidence_blockers": evidence_errors,
    }


def validate_fanfiction_literary_evidence(root: Path, manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = {
        "schema", "protocol_version", "trial_id", "pack_hash", "mapping_sha256",
        "aggregate_file", "aggregate_sha256", "resolution_file", "resolution_sha256",
        "routes", "reviewer_count", "overall_conclusion", "stores_manuscript_body",
        "manifest_sha256",
    }
    if set(manifest) != expected or manifest.get("schema") != EVIDENCE_SCHEMA:
        return ["fanfiction_literary_evidence_manifest_fields_invalid"]
    stored_hash = str(manifest.get("manifest_sha256") or "")
    basis = dict(manifest)
    basis.pop("manifest_sha256", None)
    if not stored_hash or payload_sha256(basis) != stored_hash:
        errors.append("fanfiction_literary_evidence_manifest_hash_invalid")
    if (
        manifest.get("protocol_version") != TRIAL_SCHEMA
        or manifest.get("overall_conclusion") != "pass"
        or manifest.get("stores_manuscript_body") is not False
        or int(manifest.get("reviewer_count") or 0) != 3
        or set(manifest.get("routes") or []) != set(ROUTE_FAMILIES)
    ):
        errors.append("fanfiction_literary_evidence_protocol_invalid")
    trial_id = str(manifest.get("trial_id") or "")
    try:
        public = load_public_manifest(root, trial_id)
        mapping = load_private_mapping(root, trial_id, public)
        verify_trial_source_and_public_files(root, trial_id, public, mapping)
    except (OSError, ValueError) as exc:
        errors.append(f"fanfiction_literary_evidence_live_pack_invalid:{exc}")
        return errors
    if public.get("pack_hash") != manifest.get("pack_hash") or mapping.get("mapping_sha256") != manifest.get("mapping_sha256"):
        errors.append("fanfiction_literary_evidence_pack_binding_invalid")
    aggregate_path = project_artifact(root, str(manifest.get("aggregate_file") or ""))
    aggregate = read_object(aggregate_path) if aggregate_path is not None else {}
    try:
        validate_aggregate_hash(aggregate, trial_id=trial_id)
    except ValueError as exc:
        errors.append(f"fanfiction_literary_evidence_aggregate_invalid:{exc}")
    if (
        aggregate.get("aggregate_sha256") != manifest.get("aggregate_sha256")
        or aggregate.get("pack_hash") != manifest.get("pack_hash")
        or aggregate.get("mapping_sha256") != manifest.get("mapping_sha256")
        or aggregate.get("threshold_conclusion") != "pass"
        or aggregate.get("disqualifying_findings")
        or len(aggregate.get("reviewer_ids") or []) != 3
    ):
        errors.append("fanfiction_literary_evidence_threshold_invalid")
    try:
        submissions = load_review_submissions(
            fanfiction_literary_trial_root(root, trial_id), public, trial_id
        )
    except ValueError as exc:
        errors.append(f"fanfiction_literary_evidence_reviews_invalid:{exc}")
        submissions = []
    if (
        len(submissions) != 3
        or sorted(str(item["submission_sha256"]) for item in submissions)
        != aggregate.get("submission_sha256")
        or sorted(str(item["reviewer"]["reviewer_id"]) for item in submissions)
        != aggregate.get("reviewer_ids")
    ):
        errors.append("fanfiction_literary_evidence_review_binding_invalid")
    elif {
        key: aggregate.get(key)
        for key in (
            "route_metric_medians",
            "failure_findings",
            "disqualifying_findings",
            "disagreements",
            "material_disagreement_ids",
            "threshold_conclusion",
        )
    } != literary_assessment_from_submissions(mapping, submissions):
        errors.append("fanfiction_literary_evidence_assessment_recomputation_failed")
    material_ids = set(str(item) for item in aggregate.get("material_disagreement_ids") or [])
    resolution_file = str(manifest.get("resolution_file") or "")
    if material_ids:
        resolution_path = project_artifact(root, resolution_file)
        resolution = read_object(resolution_path) if resolution_path is not None else {}
        if not valid_resolution(resolution, aggregate=aggregate, required_ids=material_ids):
            errors.append("fanfiction_literary_evidence_disagreement_resolution_invalid")
        elif resolution.get("resolution_sha256") != manifest.get("resolution_sha256"):
            errors.append("fanfiction_literary_evidence_resolution_binding_invalid")
    elif resolution_file or manifest.get("resolution_sha256"):
        errors.append("fanfiction_literary_evidence_unnecessary_resolution_binding")
    return errors


def write_fanfiction_literary_evidence_manifest(
    root: Path,
    trial_id: str,
    public: dict[str, Any],
    mapping: dict[str, Any],
    aggregate: dict[str, Any],
    resolution: dict[str, Any] | None,
) -> None:
    basis = {
        "schema": EVIDENCE_SCHEMA,
        "protocol_version": TRIAL_SCHEMA,
        "trial_id": trial_id,
        "pack_hash": public["pack_hash"],
        "mapping_sha256": mapping["mapping_sha256"],
        "aggregate_file": f"70_runtime/literary_evidence/fanfiction_trials/{trial_id}/aggregate.json",
        "aggregate_sha256": aggregate["aggregate_sha256"],
        "resolution_file": (
            f"70_runtime/literary_evidence/fanfiction_trials/{trial_id}/disagreement_resolution.json"
            if resolution is not None else ""
        ),
        "resolution_sha256": str((resolution or {}).get("resolution_sha256") or ""),
        "routes": list(ROUTE_FAMILIES),
        "reviewer_count": 3,
        "overall_conclusion": "pass",
        "stores_manuscript_body": False,
    }
    write_json(
        root / "70_runtime" / "literary_evidence" / "manifest.json",
        {**basis, "manifest_sha256": payload_sha256(basis)},
    )


def source_chapter_records(source_dir: Path) -> list[dict[str, Any]]:
    if not source_dir.is_dir():
        raise ValueError(f"literary trial source directory does not exist: {source_dir}")
    records: list[dict[str, Any]] = []
    for chapter_number in range(1, CHAPTER_COUNT + 1):
        path = find_chapter_file(source_dir, chapter_number)
        body = path.read_text(encoding="utf-8")
        if not body.strip():
            raise ValueError(f"literary trial chapter is empty: {path}")
        records.append(
            {
                "chapter_number": chapter_number,
                "source_path": str(path),
                "sha256": sha256(body.encode("utf-8")).hexdigest(),
                "character_count": len(body),
            }
        )
    return records


def validate_gate_report(
    payload: dict[str, Any],
    *,
    route_family: str,
    chapters: list[dict[str, Any]],
) -> list[str]:
    expected = {
        "schema", "route_family", "chapter_count", "chapter_hashes", "p1_blockers",
        "untraceable_canon_assertions", "continuous_source_reproduction_findings",
        "fabricated_authorization_findings", "generated_at",
    }
    errors: list[str] = []
    if set(payload) != expected or payload.get("schema") != GATE_REPORT_SCHEMA:
        return ["gate_report_fields_invalid"]
    if payload.get("route_family") != route_family or payload.get("chapter_count") != CHAPTER_COUNT:
        errors.append("gate_report_route_or_chapter_count_invalid")
    expected_hashes = [
        {"chapter_number": item["chapter_number"], "sha256": item["sha256"]}
        for item in chapters
    ]
    if payload.get("chapter_hashes") != expected_hashes:
        errors.append("gate_report_chapter_hashes_stale")
    for field in (
        "p1_blockers",
        "untraceable_canon_assertions",
        "continuous_source_reproduction_findings",
        "fabricated_authorization_findings",
    ):
        if payload.get(field) != []:
            errors.append(f"gate_report_{field}_must_be_empty")
    return errors


def validate_review_submission(
    payload: dict[str, Any],
    *,
    manifest: dict[str, Any],
    trial_id: str,
    reviewer_id: str,
) -> list[str]:
    errors: list[str] = []
    expected = {
        "schema", "trial_id", "pack_hash", "reviewer", "attestation_note", "entries", "submitted_at",
    }
    if set(payload) != expected or payload.get("schema") != REVIEW_SUBMISSION_SCHEMA:
        return ["review_submission_fields_invalid"]
    if payload.get("trial_id") != trial_id or payload.get("pack_hash") != manifest.get("pack_hash"):
        errors.append("review_submission_pack_binding_invalid")
    reviewer = payload.get("reviewer")
    if not isinstance(reviewer, dict) or set(reviewer) != {
        "reviewer_id", "instance_id", "participated_in_generation", "saw_private_mapping", "conflict_of_interest",
    }:
        errors.append("reviewer_identity_invalid")
    elif (
        reviewer.get("reviewer_id") != reviewer_id
        or not str(reviewer.get("instance_id") or "").strip()
        or reviewer.get("participated_in_generation") is not False
        or reviewer.get("saw_private_mapping") is not False
        or reviewer.get("conflict_of_interest") is not False
    ):
        errors.append("reviewer_independence_invalid")
    if not str(payload.get("attestation_note") or "").strip() or not str(payload.get("submitted_at") or "").strip():
        errors.append("reviewer_attestation_invalid")
    entries = payload.get("entries")
    by_id = {
        str(item.get("blind_id") or ""): item
        for item in entries or []
        if isinstance(item, dict)
    }
    if set(by_id) != set(manifest["blind_ids"]) or len(by_id) != len(entries or []):
        errors.append("review_entries_incomplete")
        return errors
    for blind_id, entry in by_id.items():
        if set(entry) != {"blind_id", "scores", "failure_findings", "disqualifying_findings", "notes"}:
            errors.append(f"{blind_id}_review_entry_fields_invalid")
            continue
        scores = entry.get("scores")
        if not isinstance(scores, dict) or set(scores) != set(SCORE_METRICS):
            errors.append(f"{blind_id}_scores_invalid")
        else:
            for metric, score in scores.items():
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not 1 <= score <= 5:
                    errors.append(f"{blind_id}_{metric}_score_invalid")
        errors.extend(validate_findings(entry.get("failure_findings"), set(FAILURE_CODES), f"{blind_id}_failure"))
        errors.extend(
            validate_findings(entry.get("disqualifying_findings"), set(DISQUALIFYING_CODES), f"{blind_id}_disqualifying")
        )
        if not isinstance(entry.get("notes"), str) or len(entry["notes"]) > 4000:
            errors.append(f"{blind_id}_notes_invalid")
    return errors


def validate_findings(value: Any, codes: set[str], label: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{label}_findings_must_be_list"]
    errors: list[str] = []
    for index, item in enumerate(value):
        if (
            not isinstance(item, dict)
            or set(item) != {"code", "note"}
            or item.get("code") not in codes
            or not str(item.get("note") or "").strip()
        ):
            errors.append(f"{label}_finding_{index}_invalid")
    return errors


def load_review_submissions(
    trial_root: Path,
    manifest: dict[str, Any],
    trial_id: str,
) -> list[dict[str, Any]]:
    submissions: list[dict[str, Any]] = []
    for path in sorted((trial_root / "reviews").glob("*.json")):
        payload = read_object(path)
        reviewer_id = str((payload.get("reviewer") or {}).get("reviewer_id") or "")
        errors = validate_review_submission(
            {key: value for key, value in payload.items() if key != "submission_sha256"},
            manifest=manifest,
            trial_id=trial_id,
            reviewer_id=reviewer_id,
        )
        stored_hash = str(payload.get("submission_sha256") or "")
        basis = dict(payload)
        basis.pop("submission_sha256", None)
        if errors or not stored_hash or payload_sha256(basis) != stored_hash:
            raise ValueError(f"stored fanfiction literary review is invalid: {path}")
        submissions.append(payload)
    reviewer_ids = [str(item["reviewer"]["reviewer_id"]) for item in submissions]
    if len(reviewer_ids) != len(set(reviewer_ids)):
        raise ValueError("fanfiction literary reviewer IDs must be unique")
    return submissions


def load_public_manifest(root: Path, trial_id: str) -> dict[str, Any]:
    path = fanfiction_literary_trial_root(root, trial_id) / "public" / "manifest.json"
    payload = read_object(path)
    if payload.get("schema") != TRIAL_SCHEMA or payload.get("trial_id") != trial_id:
        raise ValueError(f"fanfiction literary trial does not exist or is invalid: {trial_id}")
    stored_hash = str(payload.get("pack_hash") or "")
    basis = dict(payload)
    basis.pop("pack_hash", None)
    if not stored_hash or payload_sha256(basis) != stored_hash:
        raise ValueError("fanfiction literary trial pack hash is invalid")
    if payload.get("chapter_count_per_route") != CHAPTER_COUNT or set(payload.get("blind_ids") or []) != {"entry-a", "entry-b"}:
        raise ValueError("fanfiction literary trial shape is invalid")
    return payload


def load_private_mapping(root: Path, trial_id: str, manifest: dict[str, Any]) -> dict[str, Any]:
    path = fanfiction_literary_trial_root(root, trial_id) / "private_mapping.json"
    payload = read_object(path)
    if (
        payload.get("schema") != PRIVATE_MAPPING_SCHEMA
        or payload.get("trial_id") != trial_id
        or payload.get("pack_hash") != manifest.get("pack_hash")
    ):
        raise ValueError("fanfiction literary private mapping is invalid")
    stored_hash = str(payload.get("mapping_sha256") or "")
    basis = dict(payload)
    basis.pop("mapping_sha256", None)
    if not stored_hash or payload_sha256(basis) != stored_hash:
        raise ValueError("fanfiction literary private mapping hash is invalid")
    if set(payload.get("blind_route_mapping") or {}) != set(manifest["blind_ids"]):
        raise ValueError("fanfiction literary blind mapping is incomplete")
    if set((payload.get("blind_route_mapping") or {}).values()) != set(ROUTE_FAMILIES):
        raise ValueError("fanfiction literary route mapping is invalid")
    return payload


def verify_trial_source_and_public_files(
    root: Path,
    trial_id: str,
    manifest: dict[str, Any],
    mapping: dict[str, Any],
) -> None:
    public_root = fanfiction_literary_trial_root(root, trial_id) / "public"
    public_by_id = {str(item["blind_id"]): item for item in manifest["entries"]}
    for blind_id, route in mapping["blind_route_mapping"].items():
        route_record = mapping["routes"][route]
        source_records = source_chapter_records(Path(route_record["source_dir"]))
        if chapter_merkle_root(source_records) != route_record["source_merkle_root"]:
            raise ValueError(f"source chapters changed for {route}")
        gate_file = Path(route_record["gate_report_file"])
        if file_hash(gate_file) != route_record["gate_report_sha256"]:
            raise ValueError(f"gate report changed for {route}")
        if validate_gate_report(read_object(gate_file), route_family=route, chapters=source_records):
            raise ValueError(f"gate report is no longer current for {route}")
        for expected, public in zip(source_records, public_by_id[blind_id]["chapters"], strict=True):
            path = (public_root / public["path"]).resolve()
            path.relative_to(public_root.resolve())
            if not path.is_file() or sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest() != expected["sha256"]:
                raise ValueError(f"public blind chapter changed for {blind_id}")


def validate_aggregate_hash(payload: dict[str, Any], *, trial_id: str) -> None:
    if payload.get("schema") != AGGREGATE_SCHEMA or payload.get("trial_id") != trial_id:
        raise ValueError("fanfiction literary aggregate is missing or invalid")
    stored_hash = str(payload.get("aggregate_sha256") or "")
    basis = dict(payload)
    basis.pop("aggregate_sha256", None)
    if not stored_hash or payload_sha256(basis) != stored_hash:
        raise ValueError("fanfiction literary aggregate hash is invalid")


def valid_resolution(
    payload: dict[str, Any],
    *,
    aggregate: dict[str, Any],
    required_ids: set[str],
) -> bool:
    if payload.get("schema") != RESOLUTION_SCHEMA or payload.get("aggregate_sha256") != aggregate.get("aggregate_sha256"):
        return False
    stored_hash = str(payload.get("resolution_sha256") or "")
    basis = dict(payload)
    basis.pop("resolution_sha256", None)
    resolutions = payload.get("resolutions")
    by_id = {
        str(item.get("disagreement_id") or ""): item
        for item in resolutions or []
        if isinstance(item, dict)
    }
    return bool(
        stored_hash
        and payload_sha256(basis) == stored_hash
        and payload.get("score_override_permitted") is False
        and set(by_id) == required_ids
        and all(
            item.get("decision") == "acknowledge_panel_median_without_score_override"
            and str(item.get("note") or "").strip()
            for item in by_id.values()
        )
    )


def render_instructions(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# 同人双路线 20 章文学盲审",
            "",
            f"- Trial: `{payload['trial_id']}`",
            f"- Pack hash: `{payload['pack_hash']}`",
            "- 每条匿名路线：20 章",
            "- 评审人数：3 名互相独立且未参与生成的人类评审",
            "",
            "按 1–5 分评审：原著保真、角色声音/目标/自主性、原著辨识回报、原创主线所有权、",
            "分歧一二阶因果、世界内代价与反制、章节阅读价值、持续阅读欲望。",
            "同时记录百科说明、机械复述、原著职责掠夺、模板事件循环，以及不可追溯 Canon 断言、",
            "连续原文复现或伪造授权声明。不要查看 private_mapping.json。",
            "",
        ]
    )


def fanfiction_literary_trial_root(root: Path, trial_id: str) -> Path:
    return root / "70_runtime" / "literary_evidence" / "fanfiction_trials" / trial_id


def project_artifact(root: Path, relative_path: str) -> Path | None:
    if not relative_path:
        return None
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "CHAPTER_COUNT",
    "DISQUALIFYING_CODES",
    "EVIDENCE_SCHEMA",
    "FAILURE_CODES",
    "FanfictionLiteraryAggregateResult",
    "FanfictionLiteraryTrialResult",
    "ROUTE_FAMILIES",
    "SCORE_METRICS",
    "aggregate_fanfiction_literary_trial",
    "create_fanfiction_literary_review_template",
    "create_fanfiction_literary_trial",
    "fanfiction_literary_trial_status",
    "resolve_fanfiction_literary_disagreements",
    "submit_fanfiction_literary_review",
    "validate_fanfiction_literary_evidence",
]
