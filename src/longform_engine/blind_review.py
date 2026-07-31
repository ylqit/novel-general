"""Blind-review packaging and independent evaluator evidence for formal benchmarks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable

from longform_engine.benchmark import (
    BENCHMARK_SCHEMA,
    FANFICTION_SCORE_METRICS,
    SCORE_METRICS,
    benchmark_dir,
    validate_run_id,
)
from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


SOURCE_MANIFEST_SCHEMA = "benchmark_source_manifest_v1"
BLIND_PACK_SCHEMA = "blind_review_pack_v1"
BLIND_MAPPING_SCHEMA = "blind_review_private_mapping_v1"
BLIND_SUBMISSION_SCHEMA = "blind_review_submission_v1"
BLIND_AGGREGATE_SCHEMA = "blind_review_aggregate_v1"


@dataclass(frozen=True)
class BenchmarkSourceResult:
    schema: str
    run_id: str
    chapter_count: int
    source_merkle_root: str
    manifest_file: str


@dataclass(frozen=True)
class BlindPackResult:
    schema: str
    comparison_id: str
    pack_hash: str
    public_dir: str
    private_mapping_file: str
    blind_ids: tuple[str, ...]


@dataclass(frozen=True)
class BlindTemplateResult:
    schema: str
    comparison_id: str
    judge_id: str
    template_file: str
    pack_hash: str


@dataclass(frozen=True)
class BlindSubmissionResult:
    schema: str
    comparison_id: str
    judge_id: str
    submission_file: str
    submission_sha256: str


@dataclass(frozen=True)
class BlindAggregateResult:
    schema: str
    comparison_id: str
    aggregate_file: str
    aggregate_sha256: str
    judge_count: int
    run_ids: tuple[str, ...]
    next_command: str


def attach_benchmark_source(
    config: ConfigDocument,
    *,
    run_id: str,
    source_dir: str | Path,
) -> BenchmarkSourceResult:
    """Hash a reviewed manuscript source without copying prose into run records."""

    root = resolve_project_root(config)
    normalized_run_id = validate_run_id(run_id)
    run_dir = benchmark_dir(root, normalized_run_id)
    run = read_object(run_dir / "run.json")
    if run.get("schema") != BENCHMARK_SCHEMA:
        raise ValueError(f"Benchmark run does not exist or is invalid: {normalized_run_id}")
    chapter_count = int(run.get("chapter_count") or 0)
    directory = Path(source_dir).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError(f"Benchmark manuscript source directory does not exist: {directory}")

    chapters = []
    for chapter_number in range(1, chapter_count + 1):
        path = find_chapter_file(directory, chapter_number)
        body = path.read_text(encoding="utf-8")
        if not body.strip():
            raise ValueError(f"Benchmark source chapter is empty: {path}")
        chapters.append(
            {
                "chapter_number": chapter_number,
                "source_path": str(path),
                "sha256": sha256(body.encode("utf-8")).hexdigest(),
                "character_count": len(body),
            }
        )
    merkle_root = chapter_merkle_root(chapters)
    payload = {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "run_id": normalized_run_id,
        "chapter_count": chapter_count,
        "source_dir": str(directory),
        "source_merkle_root": merkle_root,
        "chapters": chapters,
        "stores_manuscript_body": False,
    }
    payload["manifest_sha256"] = payload_sha256(payload)
    manifest_file = run_dir / "source_manifest.json"
    write_json(manifest_file, payload)
    return BenchmarkSourceResult(
        schema=SOURCE_MANIFEST_SCHEMA,
        run_id=normalized_run_id,
        chapter_count=chapter_count,
        source_merkle_root=merkle_root,
        manifest_file=relative(root, manifest_file),
    )


def create_blind_review_pack(
    config: ConfigDocument,
    *,
    comparison_id: str,
    run_ids: Iterable[str],
    seed: str,
) -> BlindPackResult:
    """Create randomized public prose copies and a private run-id mapping."""

    root = resolve_project_root(config)
    normalized_comparison_id = validate_run_id(comparison_id)
    normalized_run_ids = tuple(validate_run_id(run_id) for run_id in run_ids)
    if len(normalized_run_ids) != 2 or len(set(normalized_run_ids)) != 2:
        raise ValueError("A formal blind pack requires exactly two unique run ids.")
    if not str(seed).strip():
        raise ValueError("Blind pack seed cannot be empty.")

    runs = [read_valid_run(root, run_id) for run_id in normalized_run_ids]
    if len({run.get("scenario_sha256") for run in runs}) != 1 or not runs[0].get("scenario_sha256"):
        raise ValueError("Blind pack runs must share a non-empty scenario_sha256.")
    if len({run.get("chapter_count") for run in runs}) != 1:
        raise ValueError("Blind pack runs must use the same chapter_count.")
    if int(runs[0].get("chapter_count") or 0) < 10:
        raise ValueError("Formal blind packs require at least 10 chapters.")
    products = {str(run.get("agent_product") or "") for run in runs}
    if "novel-skill" not in products or not products.intersection({"codex", "claude-code"}):
        raise ValueError("A formal blind pack requires one longform run and one novel-skill baseline.")
    for field in ("host_product", "agent_model", "host_version", "creation_mode"):
        values = {str(run.get(field) or "") for run in runs}
        if len(values) != 1 or "" in values:
            raise ValueError(f"Blind pack runs must share a non-empty {field}.")

    manifests = {
        run_id: read_and_verify_source_manifest(root, run_id)
        for run_id in normalized_run_ids
    }
    ordered = sorted(
        normalized_run_ids,
        key=lambda run_id: sha256(f"{seed}:{run_id}".encode("utf-8")).hexdigest(),
    )
    blind_map = {
        f"entry-{chr(ord('a') + index)}": run_id
        for index, run_id in enumerate(ordered)
    }
    pack_root = blind_review_dir(root, normalized_comparison_id)
    public_dir = pack_root / "public"
    if public_dir.exists() and any(public_dir.iterdir()):
        raise ValueError(f"Blind review pack already exists: {public_dir}")

    entries = []
    for blind_id, run_id in blind_map.items():
        target_dir = public_dir / blind_id
        target_dir.mkdir(parents=True, exist_ok=True)
        public_chapters = []
        for chapter in manifests[run_id]["chapters"]:
            source = Path(str(chapter["source_path"]))
            body = source.read_text(encoding="utf-8")
            current_hash = sha256(body.encode("utf-8")).hexdigest()
            if current_hash != chapter["sha256"]:
                raise ValueError(f"Benchmark source changed after attachment: {source}")
            target = target_dir / f"ch{int(chapter['chapter_number']):03d}.md"
            atomic_write_text(target, body)
            public_chapters.append(
                {
                    "chapter_number": int(chapter["chapter_number"]),
                    "path": f"{blind_id}/{target.name}",
                    "sha256": current_hash,
                    "character_count": len(body),
                }
            )
        entries.append({"blind_id": blind_id, "chapters": public_chapters})

    public_payload = {
        "schema": BLIND_PACK_SCHEMA,
        "comparison_id": normalized_comparison_id,
        "scenario_sha256": runs[0]["scenario_sha256"],
        "chapter_count": int(runs[0]["chapter_count"]),
        "blind_ids": sorted(blind_map),
        "score_metrics": list(SCORE_METRICS),
        "fanfiction_score_metrics": (
            list(FANFICTION_SCORE_METRICS)
            if runs[0].get("creation_mode") == "fanfiction"
            else []
        ),
        "entries": sorted(entries, key=lambda item: item["blind_id"]),
        "instructions": [
            "Review entries without trying to infer the engine or workflow.",
            "Score every chapter independently before comparing paired chapters.",
            "Do not read private_mapping.json or other project files.",
        ],
    }
    pack_hash = payload_sha256(public_payload)
    public_payload["pack_hash"] = pack_hash
    write_json(public_dir / "manifest.json", public_payload)
    atomic_write_text(public_dir / "REVIEW_INSTRUCTIONS.md", render_review_instructions(public_payload))

    mapping_payload = {
        "schema": BLIND_MAPPING_SCHEMA,
        "comparison_id": normalized_comparison_id,
        "pack_hash": pack_hash,
        "mapping": blind_map,
        "source_merkle_roots": {
            run_id: manifests[run_id]["source_merkle_root"]
            for run_id in normalized_run_ids
        },
        "run_ids": list(normalized_run_ids),
        "private": True,
    }
    private_mapping = pack_root / "private_mapping.json"
    write_json(private_mapping, mapping_payload)
    return BlindPackResult(
        schema=BLIND_PACK_SCHEMA,
        comparison_id=normalized_comparison_id,
        pack_hash=pack_hash,
        public_dir=relative(root, public_dir),
        private_mapping_file=relative(root, private_mapping),
        blind_ids=tuple(sorted(blind_map)),
    )


def create_blind_review_template(
    config: ConfigDocument,
    *,
    comparison_id: str,
    judge_id: str,
) -> BlindTemplateResult:
    root = resolve_project_root(config)
    normalized_comparison_id = validate_run_id(comparison_id)
    normalized_judge_id = clean_identifier(judge_id, field="judge_id")
    pack_root = blind_review_dir(root, normalized_comparison_id)
    manifest = read_object(pack_root / "public" / "manifest.json")
    validate_public_manifest(manifest, comparison_id=normalized_comparison_id)
    verify_public_pack_files(pack_root, manifest)
    fanfiction_metrics = list(manifest.get("fanfiction_score_metrics") or [])
    entries = []
    for blind_id in manifest["blind_ids"]:
        entries.append(
            {
                "blind_id": blind_id,
                "chapters": [
                    {
                        "chapter_number": chapter,
                        "scores": {metric: None for metric in SCORE_METRICS},
                        "fanfiction_scores": {metric: None for metric in fanfiction_metrics},
                        "confidence": None,
                        "notes": "",
                    }
                    for chapter in range(1, int(manifest["chapter_count"]) + 1)
                ],
            }
        )
    payload = {
        "schema": BLIND_SUBMISSION_SCHEMA,
        "comparison_id": normalized_comparison_id,
        "pack_hash": manifest["pack_hash"],
        "judge_id": normalized_judge_id,
        "reviewer": {
            "kind": "human",
            "product": "human",
            "version": "",
            "instance_id": "",
            "session_id": "",
        },
        "attestation": {
            "independent_review": True,
            "saw_private_mapping": False,
            "authored_any_entry": False,
            "conflict_of_interest": False,
        },
        "entries": entries,
        "overall_notes": "",
    }
    template = pack_root / "review_templates" / f"{normalized_judge_id}.json"
    write_json(template, payload)
    return BlindTemplateResult(
        schema=BLIND_SUBMISSION_SCHEMA,
        comparison_id=normalized_comparison_id,
        judge_id=normalized_judge_id,
        template_file=relative(root, template),
        pack_hash=str(manifest["pack_hash"]),
    )


def submit_blind_review(
    config: ConfigDocument,
    *,
    comparison_id: str,
    judge_id: str,
    file_path: str | Path,
) -> BlindSubmissionResult:
    root = resolve_project_root(config)
    normalized_comparison_id = validate_run_id(comparison_id)
    normalized_judge_id = clean_identifier(judge_id, field="judge_id")
    pack_root = blind_review_dir(root, normalized_comparison_id)
    manifest = read_object(pack_root / "public" / "manifest.json")
    validate_public_manifest(manifest, comparison_id=normalized_comparison_id)
    verify_public_pack_files(pack_root, manifest)
    source = Path(file_path).expanduser().resolve()
    payload = read_object(source)
    errors = validate_blind_submission(
        payload,
        manifest=manifest,
        comparison_id=normalized_comparison_id,
        judge_id=normalized_judge_id,
    )
    if errors:
        raise ValueError("Blind review submission is invalid: " + "; ".join(errors))
    normalized = dict(payload)
    normalized["submission_sha256"] = payload_sha256(payload)
    target = pack_root / "submissions" / f"{normalized_judge_id}.json"
    if target.exists():
        raise ValueError(f"Blind review submission already exists for judge: {normalized_judge_id}")
    write_json(target, normalized)
    return BlindSubmissionResult(
        schema=BLIND_SUBMISSION_SCHEMA,
        comparison_id=normalized_comparison_id,
        judge_id=normalized_judge_id,
        submission_file=relative(root, target),
        submission_sha256=normalized["submission_sha256"],
    )


def aggregate_blind_reviews(
    config: ConfigDocument,
    *,
    comparison_id: str,
) -> BlindAggregateResult:
    """Aggregate at least three complete independent submissions into run records."""

    root = resolve_project_root(config)
    normalized_comparison_id = validate_run_id(comparison_id)
    pack_root = blind_review_dir(root, normalized_comparison_id)
    manifest = read_object(pack_root / "public" / "manifest.json")
    mapping = read_object(pack_root / "private_mapping.json")
    validate_public_manifest(manifest, comparison_id=normalized_comparison_id)
    verify_public_pack_files(pack_root, manifest)
    if mapping.get("schema") != BLIND_MAPPING_SCHEMA or mapping.get("pack_hash") != manifest.get("pack_hash"):
        raise ValueError("Blind review private mapping is missing or does not match the public pack.")
    blind_map = mapping.get("mapping")
    if not isinstance(blind_map, dict) or set(blind_map) != set(manifest["blind_ids"]):
        raise ValueError("Blind review private mapping does not cover every blind entry.")

    submissions = []
    for path in sorted((pack_root / "submissions").glob("*.json")):
        payload = read_object(path)
        stored_submission_hash = str(payload.get("submission_sha256") or "")
        submission_basis = dict(payload)
        submission_basis.pop("submission_sha256", None)
        if not stored_submission_hash or payload_sha256(submission_basis) != stored_submission_hash:
            raise ValueError(f"Stored blind review hash failed ({path.name}).")
        judge_id = str(payload.get("judge_id") or "")
        errors = validate_blind_submission(
            payload,
            manifest=manifest,
            comparison_id=normalized_comparison_id,
            judge_id=judge_id,
        )
        if errors:
            raise ValueError(f"Stored blind review is invalid ({path.name}): {'; '.join(errors)}")
        submissions.append(payload)
    if len(submissions) < 3:
        raise ValueError("Blind review aggregation requires at least three valid judge submissions.")
    if any(item["reviewer"]["kind"] != "human" for item in submissions):
        raise ValueError(
            "Formal blind review aggregation accepts human reviewers only; "
            "external Agent scores remain diagnostic."
        )
    instance_ids = [str(item["reviewer"]["instance_id"]) for item in submissions]
    if len(instance_ids) != len(set(instance_ids)):
        raise ValueError("Blind review submissions must use distinct reviewer instance_id values.")
    session_ids = [str(item["reviewer"]["session_id"]) for item in submissions]
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("Blind review submissions must use distinct reviewer session_id values.")

    run_ids = tuple(str(item) for item in mapping.get("run_ids") or [])
    if len(run_ids) != 2:
        raise ValueError("Blind review mapping must contain exactly two run ids.")
    source_manifests = {
        run_id: read_and_verify_source_manifest(root, run_id)
        for run_id in run_ids
    }
    for run_id, source_manifest in source_manifests.items():
        verify_current_source_files(source_manifest, run_id=run_id)
    for run_id, expected_root in (mapping.get("source_merkle_roots") or {}).items():
        if source_manifests.get(run_id, {}).get("source_merkle_root") != expected_root:
            raise ValueError(f"Source merkle root changed for run: {run_id}")
    records_by_run: dict[str, list[dict[str, Any]]] = {}
    for run_id in run_ids:
        records = read_json(benchmark_dir(root, run_id) / "chapter_records.json")
        if not isinstance(records, list) or len(records) != int(manifest["chapter_count"]):
            raise ValueError(f"Benchmark records are missing or incomplete for run: {run_id}")
        for chapter_number, record in enumerate(records, start=1):
            if not isinstance(record, dict) or record.get("generated") is not True:
                raise ValueError(
                    f"Record technical metrics before blind aggregation: {run_id} chapter {chapter_number}"
                )
        records_by_run[run_id] = records

    aggregate_base = {
        "schema": BLIND_AGGREGATE_SCHEMA,
        "comparison_id": normalized_comparison_id,
        "pack_hash": manifest["pack_hash"],
        "run_ids": list(run_ids),
        "judge_ids": sorted(str(item["judge_id"]) for item in submissions),
        "reviewer_instances": [
            {
                "judge_id": item["judge_id"],
                "kind": item["reviewer"]["kind"],
                "product": item["reviewer"]["product"],
                "version": item["reviewer"]["version"],
                "instance_id": item["reviewer"]["instance_id"],
                "session_id": item["reviewer"]["session_id"],
                "attestation": item["attestation"],
                "submission_sha256": item["submission_sha256"],
            }
            for item in submissions
        ],
        "source_merkle_roots": {
            run_id: source_manifests[run_id]["source_merkle_root"]
            for run_id in run_ids
        },
        "aggregation": "per-chapter median",
        "stores_manuscript_body": False,
    }
    aggregate_path = pack_root / "aggregate.json"
    aggregate_sha = payload_sha256(aggregate_base)
    aggregate_payload = dict(aggregate_base)
    aggregate_payload["aggregate_sha256"] = aggregate_sha
    write_json(aggregate_path, aggregate_payload)

    by_judge = {
        str(submission["judge_id"]): submission_entries(submission)
        for submission in submissions
    }
    judge_ids = sorted(by_judge)
    for blind_id, run_id in blind_map.items():
        run_dir = benchmark_dir(root, str(run_id))
        records = records_by_run[str(run_id)]
        for chapter_number, record in enumerate(records, start=1):
            scores = {
                metric: round(
                    float(median(
                        by_judge[judge_id][blind_id][chapter_number]["scores"][metric]
                        for judge_id in judge_ids
                    )),
                    3,
                )
                for metric in SCORE_METRICS
            }
            fanfiction_metrics = manifest.get("fanfiction_score_metrics") or []
            fanfiction_scores = {
                metric: round(
                    float(median(
                        by_judge[judge_id][blind_id][chapter_number]["fanfiction_scores"][metric]
                        for judge_id in judge_ids
                    )),
                    3,
                )
                for metric in fanfiction_metrics
            }
            record["scores"] = scores
            record["fanfiction_scores"] = fanfiction_scores
            record["judge_ids"] = judge_ids
            record["review_status"] = "blind_aggregated"
            record["blind_review"] = {
                "comparison_id": normalized_comparison_id,
                "pack_hash": manifest["pack_hash"],
                "aggregate_file": relative(root, aggregate_path),
                "aggregate_sha256": aggregate_sha,
                "source_merkle_root": source_manifests[str(run_id)]["source_merkle_root"],
                "submission_sha256": sorted(
                    str(item["submission_sha256"]) for item in submissions
                ),
            }
        write_json(run_dir / "chapter_records.json", records)

    return BlindAggregateResult(
        schema=BLIND_AGGREGATE_SCHEMA,
        comparison_id=normalized_comparison_id,
        aggregate_file=relative(root, aggregate_path),
        aggregate_sha256=aggregate_sha,
        judge_count=len(submissions),
        run_ids=run_ids,
        next_command=(
            "longform-engine benchmark compare project.yaml "
            f"--comparison-id {normalized_comparison_id} "
            + " ".join(f"--run-id {run_id}" for run_id in run_ids)
        ),
    )


def formal_blind_review_errors(
    root: Path,
    *,
    candidate_id: str,
    baseline_id: str,
    candidate_records: list[dict[str, Any]],
    baseline_records: list[dict[str, Any]],
) -> list[str]:
    """Validate that formal scores came from one shared, independent blind panel."""

    errors: list[str] = []
    all_records = [*candidate_records, *baseline_records]
    if not all_records:
        return ["Formal blind review records are missing."]
    if any(record.get("review_status") != "blind_aggregated" for record in all_records):
        errors.append("Formal scores were not produced by blind-review aggregation.")
        return errors
    blind_payloads = [record.get("blind_review") for record in all_records]
    if any(not isinstance(payload, dict) for payload in blind_payloads):
        return ["Blind-review provenance is missing from chapter records."]
    pack_hashes = {str(payload.get("pack_hash") or "") for payload in blind_payloads}
    aggregate_files = {str(payload.get("aggregate_file") or "") for payload in blind_payloads}
    aggregate_hashes = {str(payload.get("aggregate_sha256") or "") for payload in blind_payloads}
    if len(pack_hashes) != 1 or "" in pack_hashes:
        errors.append("Candidate and baseline do not share one blind pack hash.")
    if len(aggregate_files) != 1 or "" in aggregate_files:
        errors.append("Candidate and baseline do not share one blind aggregate.")
        return errors
    if len(aggregate_hashes) != 1 or "" in aggregate_hashes:
        errors.append("Blind aggregate hashes are missing or inconsistent.")
    candidate_panels = {tuple(record.get("judge_ids") or []) for record in candidate_records}
    baseline_panels = {tuple(record.get("judge_ids") or []) for record in baseline_records}
    if (
        len(candidate_panels) != 1
        or candidate_panels != baseline_panels
        or len(next(iter(candidate_panels), ())) < 3
    ):
        errors.append("Candidate and baseline do not share the same panel of at least three judges.")

    aggregate_path = root / next(iter(aggregate_files))
    aggregate = read_object(aggregate_path)
    if aggregate.get("schema") != BLIND_AGGREGATE_SCHEMA:
        errors.append("Blind aggregate schema is missing or invalid.")
        return errors
    pack_root = aggregate_path.parent
    public_manifest = read_object(pack_root / "public" / "manifest.json")
    try:
        validate_public_manifest(
            public_manifest,
            comparison_id=str(aggregate.get("comparison_id") or ""),
        )
        verify_public_pack_files(pack_root, public_manifest)
    except ValueError as exc:
        errors.append(str(exc))
    expected_aggregate_hash = str(next(iter(aggregate_hashes), ""))
    stored_hash = str(aggregate.pop("aggregate_sha256", ""))
    if stored_hash != expected_aggregate_hash or payload_sha256(aggregate) != stored_hash:
        errors.append("Blind aggregate hash validation failed.")
    if set(aggregate.get("run_ids") or []) != {candidate_id, baseline_id}:
        errors.append("Blind aggregate does not map exactly the candidate and baseline runs.")
    reviewers = aggregate.get("reviewer_instances")
    if not isinstance(reviewers, list) or len(reviewers) < 3:
        errors.append("Blind aggregate has fewer than three reviewer instances.")
    else:
        if any(
            not isinstance(item, dict) or item.get("kind") != "human"
            for item in reviewers
        ):
            errors.append("Formal blind aggregate contains a non-human reviewer.")
        instance_ids = [str(item.get("instance_id") or "") for item in reviewers if isinstance(item, dict)]
        if "" in instance_ids or len(instance_ids) != len(set(instance_ids)):
            errors.append("Blind reviewer instance IDs are missing or duplicated.")
        for reviewer in reviewers:
            attestation = reviewer.get("attestation") if isinstance(reviewer, dict) else {}
            if not valid_attestation(attestation):
                errors.append("At least one blind reviewer lacks a valid independence attestation.")
                break
    for run_id in (candidate_id, baseline_id):
        try:
            source = read_and_verify_source_manifest(root, run_id)
            verify_current_source_files(source, run_id=run_id)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        expected_root = (aggregate.get("source_merkle_roots") or {}).get(run_id)
        if source.get("source_merkle_root") != expected_root:
            errors.append(f"Source manifest is missing or mismatched for run {run_id}.")
    roots = set((aggregate.get("source_merkle_roots") or {}).values())
    if len(roots) != 2:
        errors.append("Candidate and baseline manuscript sources are identical or missing.")
    return errors


def validate_blind_submission(
    payload: Any,
    *,
    manifest: dict[str, Any],
    comparison_id: str,
    judge_id: str,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["submission must be an object."]
    if payload.get("schema") != BLIND_SUBMISSION_SCHEMA:
        errors.append(f"schema must be {BLIND_SUBMISSION_SCHEMA}.")
    if payload.get("comparison_id") != comparison_id:
        errors.append("comparison_id does not match the blind pack.")
    if payload.get("pack_hash") != manifest.get("pack_hash"):
        errors.append("pack_hash does not match the blind pack.")
    if payload.get("judge_id") != judge_id:
        errors.append("judge_id does not match the submitted identity.")
    reviewer = payload.get("reviewer")
    if not isinstance(reviewer, dict):
        errors.append("reviewer must be an object.")
    else:
        if reviewer.get("kind") not in {"human", "external_agent"}:
            errors.append("reviewer.kind must be human or external_agent.")
        for field in ("product", "instance_id", "session_id"):
            if not isinstance(reviewer.get(field), str) or not str(reviewer[field]).strip():
                errors.append(f"reviewer.{field} is required.")
        if not isinstance(reviewer.get("version"), str):
            errors.append("reviewer.version must be a string.")
    if not valid_attestation(payload.get("attestation")):
        errors.append("independence attestation must be complete and non-conflicted.")
    entries = payload.get("entries")
    expected_ids = set(manifest.get("blind_ids") or [])
    if not isinstance(entries, list):
        errors.append("entries must be a list.")
        entries = []
    entry_map = {
        str(entry.get("blind_id")): entry
        for entry in entries
        if isinstance(entry, dict)
    }
    if set(entry_map) != expected_ids or len(entry_map) != len(entries):
        errors.append("entries must contain every blind_id exactly once.")
    chapter_count = int(manifest.get("chapter_count") or 0)
    fanfiction_metrics = set(manifest.get("fanfiction_score_metrics") or [])
    for blind_id, entry in entry_map.items():
        chapters = entry.get("chapters")
        if not isinstance(chapters, list) or len(chapters) != chapter_count:
            errors.append(f"{blind_id}.chapters must contain exactly {chapter_count} rows.")
            continue
        seen: set[int] = set()
        for chapter in chapters:
            if not isinstance(chapter, dict):
                errors.append(f"{blind_id}.chapters contains a non-object row.")
                continue
            number = chapter.get("chapter_number")
            if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= chapter_count:
                errors.append(f"{blind_id}.chapter_number is invalid.")
                continue
            if number in seen:
                errors.append(f"{blind_id}.chapter_number {number} is duplicated.")
            seen.add(number)
            errors.extend(validate_score_map(chapter.get("scores"), set(SCORE_METRICS), f"{blind_id}.ch{number:03d}.scores"))
            errors.extend(
                validate_score_map(
                    chapter.get("fanfiction_scores"),
                    fanfiction_metrics,
                    f"{blind_id}.ch{number:03d}.fanfiction_scores",
                )
            )
            confidence = chapter.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                errors.append(f"{blind_id}.ch{number:03d}.confidence must be between 0 and 1.")
            notes = chapter.get("notes")
            if not isinstance(notes, str) or len(notes) > 500:
                errors.append(f"{blind_id}.ch{number:03d}.notes must be at most 500 characters.")
    if any(field in payload for field in ("engine", "run_id", "private_mapping", "manuscript_body")):
        errors.append("submission must not contain engine identity, run ids, private mapping, or manuscript bodies.")
    overall_notes = payload.get("overall_notes")
    if not isinstance(overall_notes, str) or len(overall_notes) > 2000:
        errors.append("overall_notes must be at most 2000 characters.")
    return errors


def validate_score_map(value: Any, expected: set[str], field: str) -> list[str]:
    if not isinstance(value, dict) or set(value) != expected:
        return [f"{field} must contain exactly: {', '.join(sorted(expected)) or 'no metrics'}."]
    errors = []
    for metric, score in value.items():
        if not isinstance(score, (int, float)) or isinstance(score, bool) or not 1 <= score <= 10:
            errors.append(f"{field}.{metric} must be between 1 and 10.")
    return errors


def valid_attestation(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("independent_review") is True
        and value.get("saw_private_mapping") is False
        and value.get("authored_any_entry") is False
        and value.get("conflict_of_interest") is False
    )


def validate_public_manifest(payload: Any, *, comparison_id: str) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != BLIND_PACK_SCHEMA:
        raise ValueError("Blind review public manifest is missing or invalid.")
    if payload.get("comparison_id") != comparison_id:
        raise ValueError("Blind review public manifest comparison_id does not match.")
    stored_hash = str(payload.get("pack_hash") or "")
    basis = dict(payload)
    basis.pop("pack_hash", None)
    if not stored_hash or payload_sha256(basis) != stored_hash:
        raise ValueError("Blind review public manifest hash validation failed.")


def read_valid_run(root: Path, run_id: str) -> dict[str, Any]:
    run = read_object(benchmark_dir(root, run_id) / "run.json")
    if run.get("schema") != BENCHMARK_SCHEMA or run.get("run_id") != run_id:
        raise ValueError(f"Benchmark run does not exist or is invalid: {run_id}")
    return run


def verify_public_pack_files(pack_root: Path, manifest: dict[str, Any]) -> None:
    public_root = (pack_root / "public").resolve()
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Blind review public manifest entries are missing.")
    for entry in entries:
        chapters = entry.get("chapters") if isinstance(entry, dict) else None
        if not isinstance(chapters, list):
            raise ValueError("Blind review public manifest chapter rows are missing.")
        for chapter in chapters:
            if not isinstance(chapter, dict):
                raise ValueError("Blind review public manifest contains an invalid chapter row.")
            path = (public_root / str(chapter.get("path") or "")).resolve()
            try:
                path.relative_to(public_root)
            except ValueError as exc:
                raise ValueError("Blind review public chapter path escapes the public pack.") from exc
            if not path.is_file():
                raise ValueError(f"Blind review public chapter is missing: {path}")
            current_hash = sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if current_hash != chapter.get("sha256"):
                raise ValueError(f"Blind review public chapter hash failed: {path}")


def read_and_verify_source_manifest(root: Path, run_id: str) -> dict[str, Any]:
    path = benchmark_dir(root, run_id) / "source_manifest.json"
    payload = read_object(path)
    if payload.get("schema") != SOURCE_MANIFEST_SCHEMA or payload.get("run_id") != run_id:
        raise ValueError(f"Benchmark source manifest is missing or invalid: {run_id}")
    stored_hash = str(payload.get("manifest_sha256") or "")
    basis = dict(payload)
    basis.pop("manifest_sha256", None)
    if not stored_hash or payload_sha256(basis) != stored_hash:
        raise ValueError(f"Benchmark source manifest hash failed: {run_id}")
    chapters = payload.get("chapters")
    if not isinstance(chapters, list) or chapter_merkle_root(chapters) != payload.get("source_merkle_root"):
        raise ValueError(f"Benchmark source merkle root failed: {run_id}")
    return payload


def verify_current_source_files(payload: dict[str, Any], *, run_id: str) -> None:
    for chapter in payload["chapters"]:
        source = Path(str(chapter["source_path"]))
        if not source.is_file():
            raise ValueError(f"Benchmark source file is missing for run {run_id}: {source}")
        current_hash = sha256(source.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if current_hash != chapter["sha256"]:
            raise ValueError(f"Benchmark source changed after attachment for run {run_id}: {source}")


def find_chapter_file(directory: Path, chapter_number: int) -> Path:
    direct = [
        directory / f"ch{chapter_number:03d}.md",
        directory / f"ch{chapter_number:03d}.txt",
        directory / f"ch{chapter_number}.md",
        directory / f"ch{chapter_number}.txt",
    ]
    matches = [path for path in direct if path.is_file()]
    if not matches:
        pattern = re.compile(rf"(?:^|\D)(?:ch)?0*{chapter_number}(?:\D|$)", re.IGNORECASE)
        matches = [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in {".md", ".txt"} and pattern.search(path.stem)
        ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one source file for chapter {chapter_number}, found {len(matches)} in {directory}."
        )
    return matches[0].resolve()


def chapter_merkle_root(chapters: list[dict[str, Any]]) -> str:
    digest = sha256()
    for chapter in sorted(chapters, key=lambda item: int(item["chapter_number"])):
        digest.update(f"{int(chapter['chapter_number']):06d}:{chapter['sha256']}\n".encode("ascii"))
    return digest.hexdigest()


def submission_entries(payload: dict[str, Any]) -> dict[str, dict[int, dict[str, Any]]]:
    return {
        str(entry["blind_id"]): {
            int(chapter["chapter_number"]): chapter
            for chapter in entry["chapters"]
        }
        for entry in payload["entries"]
    }


def render_review_instructions(payload: dict[str, Any]) -> str:
    lines = [
        "# Blind Review Package",
        "",
        f"- Comparison ID: `{payload['comparison_id']}`",
        f"- Pack hash: `{payload['pack_hash']}`",
        f"- Chapters per entry: `{payload['chapter_count']}`",
        "",
        "Read only this public directory and your submission template.",
        "Do not inspect sibling project files, private mappings, benchmark run IDs, or engine metadata.",
        "Score every chapter from 1 to 10 for continuity, character consistency, foreshadowing control, pacing, reader payoff, and AI taste.",
        "For AI taste, 1 means low AI taste and 10 means high AI taste.",
        "",
    ]
    return "\n".join(lines)


def blind_review_dir(root: Path, comparison_id: str) -> Path:
    return root / "70_runtime" / "benchmarks" / "blind_reviews" / comparison_id


def clean_identifier(value: str, *, field: str) -> str:
    normalized = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", normalized):
        raise ValueError(f"{field} must use 1-64 letters, digits, dot, underscore, or hyphen.")
    return normalized


def payload_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(raw.encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any] | list[Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, (dict, list)) else {}


def read_object(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
