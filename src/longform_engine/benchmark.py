"""Deterministic benchmark run scaffolding and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from statistics import mean
import subprocess
from typing import Any

from longform_engine import __version__
from longform_engine.config import ConfigDocument
from longform_engine.distribution import skill_source, tree_hash
from longform_engine.storage import atomic_write_text, resolve_project_root


BENCHMARK_SCHEMA = "quality_benchmark_run_v2"
BENCHMARK_VALIDATION_SCHEMA = "quality_benchmark_validation_v2"
BENCHMARK_REPORT_SCHEMA = "quality_benchmark_report_v2"
BENCHMARK_RECORD_SCHEMA = "quality_benchmark_record_result_v2"
BENCHMARK_COMPARISON_SCHEMA = "quality_benchmark_comparison_v2"
RAG_BENCHMARK_SCHEMA = "rag_scale_evidence_v1"
AGENT_PRODUCTS = ("codex", "claude-code", "novel-skill")
SCORE_METRICS = (
    "continuity",
    "character_consistency",
    "foreshadowing_control",
    "pacing",
    "reader_payoff",
    "ai_taste",
)
FANFICTION_SCORE_METRICS = (
    "canon_fidelity",
    "ooc_control",
    "original_contribution",
    "divergence_causality",
    "source_prose_originality",
    "crossover_consistency",
)
COUNT_METRICS = (
    "repair_count",
    "need_human_count",
    "context_file_count",
    "context_character_count",
    "p0_contradiction_count",
    "canonical_pollution_count",
)
QUALITY_WEIGHTS = {
    "continuity": 0.25,
    "character_consistency": 0.25,
    "foreshadowing_control": 0.10,
    "pacing": 0.15,
    "reader_payoff": 0.10,
    "ai_taste": 0.15,
}
RAG_CLAIM_THRESHOLDS = {
    "scale_chapters": 500,
    "recall_at_k_min": 0.85,
    "fact_error_rate_max": 0.02,
    "p95_query_ms_max": 1000.0,
}
RAG_REQUIRED_CATEGORIES = (
    "entity_alias",
    "temporal_conflict",
    "foreshadowing",
    "causal",
    "ability_boundary",
    "relationship_state",
    "fact_conflict",
)
RAG_MIN_QUERY_COUNT = 50
CHAPTER_ARTIFACT_PATHS = {
    "work_order": "50_workbench/writing_tasks/ch{chapter:03d}.md",
    "manifest": "50_workbench/writing_tasks/ch{chapter:03d}.agent_task.json",
    "reviewed_manuscript": "40_manuscript/draft/ch{chapter:03d}.md",
    "gate_result": "50_workbench/gate_artifacts/ch{chapter:03d}/gate_result.json",
}


@dataclass(frozen=True)
class BenchmarkInitResult:
    run_id: str
    run_dir: str
    run_file: str
    records_file: str
    next_command: str


@dataclass(frozen=True)
class BenchmarkValidationResult:
    run_id: str
    ok: bool
    complete: bool
    acceptance_passed: bool
    acceptance_failures: tuple[str, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class BenchmarkReportResult:
    run_id: str
    report_json: str
    report_markdown: str
    chapters_recorded: int
    complete: bool
    acceptance_passed: bool
    acceptance_failures: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class BenchmarkRecordResult:
    schema: str
    run_id: str
    chapter_number: int
    records_file: str
    complete: bool
    next_command: str


@dataclass(frozen=True)
class BenchmarkComparisonResult:
    schema: str
    comparison_id: str
    comparison_json: str
    comparison_markdown: str
    run_ids: tuple[str, ...]
    best_by_metric: dict[str, str]
    claim_eligible: bool
    claim_reasons: tuple[str, ...]
    next_command: str


@dataclass(frozen=True)
class RagBenchmarkRecordResult:
    schema: str
    run_id: str
    evidence_file: str
    meets_thresholds: bool
    errors: tuple[str, ...]


def init_benchmark(
    config: ConfigDocument,
    *,
    run_id: str,
    agent_product: str,
    chapters: int,
    baseline: str = "",
    scenario_id: str = "",
    scenario_file: str | Path | None = None,
    agent_model: str = "",
    host_product: str = "",
    host_version: str = "",
    workflow_version: str = "",
) -> BenchmarkInitResult:
    root = resolve_project_root(config)
    normalized_id = validate_run_id(run_id)
    if agent_product not in AGENT_PRODUCTS:
        raise ValueError(f"agent_product must be one of: {', '.join(AGENT_PRODUCTS)}")
    if chapters < 1 or chapters > 100:
        raise ValueError("chapters must be between 1 and 100.")
    normalized_host_product = clean_metadata(host_product, field="host_product") or (
        agent_product if agent_product in {"codex", "claude-code"} else ""
    )
    if normalized_host_product and normalized_host_product not in {"codex", "claude-code"}:
        raise ValueError("host_product must be codex or claude-code.")
    scenario_sha256 = ""
    scenario_source = ""
    if scenario_file:
        scenario_path = Path(scenario_file).expanduser().resolve()
        if not scenario_path.is_file():
            raise ValueError(f"Benchmark scenario file does not exist: {scenario_path}")
        scenario_sha256 = sha256(scenario_path.read_bytes()).hexdigest()
        scenario_source = scenario_path.name
    run_dir = benchmark_dir(root, normalized_id)
    if run_dir.exists() and any(run_dir.iterdir()):
        raise ValueError(f"Benchmark run already exists: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    run_file = run_dir / "run.json"
    records_file = run_dir / "chapter_records.json"
    normalized_scenario_id = clean_metadata(scenario_id, field="scenario_id") or clean_metadata(
        str(config.data["project"]["slug"]),
        field="project.slug",
    )
    run_payload = {
        "schema": BENCHMARK_SCHEMA,
        "run_id": normalized_id,
        "engine_version": __version__,
        "project_slug": str(config.data["project"]["slug"]),
        "agent_product": agent_product,
        "agent_model": clean_metadata(agent_model, field="agent_model"),
        "host_product": normalized_host_product,
        "host_version": clean_metadata(host_version, field="host_version"),
        "workflow_version": clean_metadata(workflow_version, field="workflow_version") or (
            __version__ if agent_product in {"codex", "claude-code"} else ""
        ),
        "scenario_id": normalized_scenario_id,
        "scenario_sha256": scenario_sha256,
        "scenario_source": scenario_source,
        "source_state": capture_source_state(config, agent_product=agent_product),
        "baseline": clean_metadata(baseline, field="baseline"),
        "chapter_count": chapters,
        "creation_mode": str(config.data.get("creation", {}).get("mode") or "original"),
        "review_protocol": "blind_engine_identity",
        "stores_manuscript_body": False,
        "score_scale": {"min": 1, "max": 10, "ai_taste": "1=low AI taste, 10=high AI taste"},
        "required_metrics": [
            *SCORE_METRICS,
            *(
                FANFICTION_SCORE_METRICS
                if str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
                else ()
            ),
            "gate_passed",
            *COUNT_METRICS,
        ],
        "created_at": utc_now(),
    }
    records = [empty_chapter_record(chapter) for chapter in range(1, chapters + 1)]
    write_json(run_file, run_payload)
    write_json(records_file, records)
    return BenchmarkInitResult(
        run_id=normalized_id,
        run_dir=relative(root, run_dir),
        run_file=relative(root, run_file),
        records_file=relative(root, records_file),
        next_command=f"longform-engine benchmark validate project.yaml --run-id {normalized_id}",
    )


def capture_source_state(config: ConfigDocument, *, agent_product: str) -> dict[str, Any]:
    """Hash reproducibility inputs without storing source diffs or project prose."""

    config_hash = sha256(config.path.read_bytes()).hexdigest() if config.path and config.path.is_file() else ""
    skill_hash = ""
    if agent_product in {"codex", "claude-code"}:
        skill_hash = tree_hash(skill_source(agent_product))

    git_root = discover_git_root(config)
    commit = ""
    dirty = False
    dirty_tree_hash = ""
    if git_root is not None:
        commit = git_output(git_root, "rev-parse", "HEAD").decode("ascii", errors="replace").strip()
        status = git_output(git_root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        diff = git_output(git_root, "diff", "--binary", "HEAD", "--")
        untracked = git_output(git_root, "ls-files", "--others", "--exclude-standard", "-z")
        digest = sha256()
        digest.update(status)
        digest.update(b"\0")
        digest.update(diff)
        for item in sorted(path for path in untracked.split(b"\0") if path):
            relative = item.decode("utf-8", errors="surrogateescape")
            path = git_root / relative
            digest.update(item)
            digest.update(b"\0")
            if path.is_file():
                digest.update(sha256(path.read_bytes()).digest())
            digest.update(b"\0")
        dirty = bool(status)
        dirty_tree_hash = digest.hexdigest()

    return {
        "schema": "benchmark_source_state_v1",
        "git_commit": commit,
        "git_dirty": dirty,
        "dirty_tree_sha256": dirty_tree_hash,
        "skill_sha256": skill_hash,
        "project_config_sha256": config_hash,
    }


def discover_git_root(config: ConfigDocument) -> Path | None:
    candidates = [Path.cwd()]
    if config.path is not None:
        candidates.append(config.path.parent)
    for candidate in candidates:
        try:
            output = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        root = Path(output.decode("utf-8", errors="replace").strip()).resolve()
        if root.is_dir():
            return root
    return None


def git_output(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    ).stdout


def record_benchmark_chapter(
    config: ConfigDocument,
    *,
    run_id: str,
    chapter_number: int,
    scores: dict[str, int | float] | None,
    gate_passed: bool,
    repair_count: int,
    need_human_count: int,
    context_file_count: int,
    context_character_count: int = 0,
    p0_contradiction_count: int = 0,
    canonical_pollution_count: int = 0,
    judge_ids: list[str] | None = None,
    character_drift: list[str] | None = None,
    foreshadowing_leaks: list[str] | None = None,
    ai_taste_issues: list[str] | None = None,
    notes: str = "",
    fanfiction_scores: dict[str, int | float] | None = None,
    review_status: str = "diagnostic",
    require_artifact_hashes: bool = False,
) -> BenchmarkRecordResult:
    root = resolve_project_root(config)
    normalized_id = validate_run_id(run_id)
    run_dir = benchmark_dir(root, normalized_id)
    run = read_json(run_dir / "run.json", None)
    records = read_json(run_dir / "chapter_records.json", None)
    if not isinstance(run, dict) or run.get("schema") != BENCHMARK_SCHEMA:
        raise ValueError(f"Benchmark run does not exist or is invalid: {normalized_id}")
    if not isinstance(records, list):
        raise ValueError("chapter_records.json must be a list.")
    if chapter_number < 1 or chapter_number > len(records):
        raise ValueError(f"chapter_number must be between 1 and {len(records)}.")
    if review_status not in {"diagnostic", "technical_pending"}:
        raise ValueError("review_status must be diagnostic or technical_pending when recording a chapter.")
    if review_status == "technical_pending" and scores is not None:
        raise ValueError("technical_pending records must not contain literary scores.")
    if review_status == "diagnostic" and scores is None:
        raise ValueError("diagnostic records require literary scores.")
    if (
        str(config.data.get("creation", {}).get("mode") or "original") == "fanfiction"
        and review_status != "technical_pending"
        and set(fanfiction_scores or {}) != set(FANFICTION_SCORE_METRICS)
    ):
        raise ValueError("Fanfiction benchmark records require all six fanfiction scores.")
    artifact_hashes = collect_chapter_artifact_hashes(root, chapter_number)
    if require_artifact_hashes:
        missing = sorted(set(CHAPTER_ARTIFACT_PATHS) - set(artifact_hashes))
        if missing:
            raise ValueError(
                "Benchmark technical record requires finalized chapter artifacts: "
                + ", ".join(missing)
            )
    record = {
        "chapter_number": chapter_number,
        "generated": True,
        "scores": dict(scores) if scores is not None else {metric: None for metric in SCORE_METRICS},
        "fanfiction_scores": dict(fanfiction_scores or {}),
        "gate_passed": gate_passed,
        "repair_count": repair_count,
        "need_human_count": need_human_count,
        "context_file_count": context_file_count,
        "context_character_count": context_character_count,
        "p0_contradiction_count": p0_contradiction_count,
        "canonical_pollution_count": canonical_pollution_count,
        "judge_ids": clean_judge_ids(judge_ids or []),
        "review_status": review_status,
        "character_drift": clean_annotations(character_drift or [], field="character_drift"),
        "foreshadowing_leaks": clean_annotations(foreshadowing_leaks or [], field="foreshadowing_leaks"),
        "ai_taste_issues": clean_annotations(ai_taste_issues or [], field="ai_taste_issues"),
        "artifact_hashes": artifact_hashes,
        "notes": clean_annotation(notes, field="notes", max_length=1000),
        "recorded_at": utc_now(),
    }
    errors = validate_record(record, expected_chapter=chapter_number)
    if errors:
        raise ValueError("Benchmark chapter record is invalid: " + "; ".join(errors))
    records[chapter_number - 1] = record
    records_file = run_dir / "chapter_records.json"
    if require_artifact_hashes:
        run["chapter_artifact_hashes_required"] = True
        write_json(run_dir / "run.json", run)
    write_json(records_file, records)
    validation = validate_benchmark(config, run_id=normalized_id)
    next_chapter = next(
        (int(item["chapter_number"]) for item in records if isinstance(item, dict) and item.get("generated") is not True),
        None,
    )
    if validation.complete:
        next_command = f"longform-engine benchmark report project.yaml --run-id {normalized_id}"
    elif next_chapter is not None:
        next_command = f"Record chapter {next_chapter} after its real Agent run."
    else:
        next_command = f"longform-engine benchmark validate project.yaml --run-id {normalized_id}"
    return BenchmarkRecordResult(
        schema=BENCHMARK_RECORD_SCHEMA,
        run_id=normalized_id,
        chapter_number=chapter_number,
        records_file=relative(root, records_file),
        complete=validation.complete,
        next_command=next_command,
    )


def validate_benchmark(config: ConfigDocument, *, run_id: str) -> BenchmarkValidationResult:
    root = resolve_project_root(config)
    normalized_id = validate_run_id(run_id)
    run_dir = benchmark_dir(root, normalized_id)
    run = read_json(run_dir / "run.json", {})
    records = read_json(run_dir / "chapter_records.json", None)
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(run, dict) or run.get("schema") != BENCHMARK_SCHEMA:
        errors.append(f"run.json must use {BENCHMARK_SCHEMA}.")
    if isinstance(run, dict) and run.get("run_id") != normalized_id:
        errors.append("run.json run_id does not match directory.")
    if isinstance(run, dict) and run.get("agent_product") not in AGENT_PRODUCTS:
        errors.append(f"run.json agent_product must be one of: {', '.join(AGENT_PRODUCTS)}.")
    if isinstance(run, dict) and run.get("stores_manuscript_body") is not False:
        errors.append("run.json stores_manuscript_body must be false.")
    scenario_id = run.get("scenario_id") if isinstance(run, dict) else None
    if scenario_id is None:
        warnings.append("legacy benchmark has no scenario_id; comparison falls back to project_slug.")
    elif not isinstance(scenario_id, str) or not scenario_id.strip() or len(scenario_id) > 200 or "\n" in scenario_id or "\r" in scenario_id:
        errors.append("run.json scenario_id must be a non-empty single line of at most 200 characters.")
    scenario_digest = run.get("scenario_sha256") if isinstance(run, dict) else None
    if scenario_digest and not re.fullmatch(r"[0-9a-f]{64}", str(scenario_digest)):
        errors.append("run.json scenario_sha256 must be a lowercase SHA-256 digest.")
    if isinstance(run, dict) and not scenario_digest:
        warnings.append("scenario_sha256 is missing; the run cannot support a formal quality claim.")
    host_product = run.get("host_product") if isinstance(run, dict) else None
    if host_product and host_product not in {"codex", "claude-code"}:
        errors.append("run.json host_product must be codex or claude-code.")
    if isinstance(run, dict) and not host_product:
        warnings.append("host_product is missing; the run cannot support a formal quality claim.")
    if isinstance(run, dict) and not run.get("workflow_version"):
        warnings.append("workflow_version is missing; the run cannot support a formal quality claim.")
    if isinstance(run, dict) and (not run.get("agent_model") or not run.get("host_version")):
        warnings.append("agent_model or host_version is missing; record both before publishing quality evidence.")
    expected_count = run.get("chapter_count") if isinstance(run, dict) else None
    if not isinstance(expected_count, int) or expected_count <= 0:
        errors.append("run.json chapter_count must be positive.")
        expected_count = 0
    if not isinstance(records, list):
        errors.append("chapter_records.json must be a list.")
        records = []
    if expected_count and len(records) != expected_count:
        errors.append("chapter_records.json length must equal chapter_count.")

    generated = 0
    artifact_hashes_required = isinstance(run, dict) and run.get("chapter_artifact_hashes_required") is True
    for index, record in enumerate(records, start=1):
        record_errors = validate_record(record, expected_chapter=index)
        if isinstance(record, dict) and record.get("generated") is True:
            artifact_hashes = record.get("artifact_hashes")
            if artifact_hashes_required and set(artifact_hashes or {}) != set(CHAPTER_ARTIFACT_PATHS):
                record_errors.append("artifact_hashes must contain all four required chapter artifacts.")
            if isinstance(artifact_hashes, dict):
                for artifact_name, item in artifact_hashes.items():
                    if artifact_name not in CHAPTER_ARTIFACT_PATHS or not isinstance(item, dict):
                        continue
                    expected_path = CHAPTER_ARTIFACT_PATHS[artifact_name].format(chapter=index)
                    if item.get("path") != expected_path:
                        record_errors.append(
                            f"artifact_hashes.{artifact_name} path must be {expected_path}."
                        )
                        continue
                    artifact_path = root / str(item.get("path") or "")
                    if not artifact_path.is_file():
                        record_errors.append(f"artifact_hashes.{artifact_name} path is missing.")
                        continue
                    actual_digest = sha256(artifact_path.read_bytes()).hexdigest()
                    if actual_digest != item.get("sha256"):
                        record_errors.append(f"artifact_hashes.{artifact_name} SHA-256 does not match.")
        if (
            isinstance(run, dict)
            and run.get("creation_mode") == "fanfiction"
            and isinstance(record, dict)
            and record.get("generated") is True
            and record.get("review_status") != "technical_pending"
            and set(record.get("fanfiction_scores") or {}) != set(FANFICTION_SCORE_METRICS)
        ):
            record_errors.append("fanfiction_scores must contain all six fanfiction metrics for a fanfiction run.")
        errors.extend(f"chapter_records[{index - 1}]: {item}" for item in record_errors)
        if isinstance(record, dict) and record.get("generated") is True:
            generated += 1
    complete = not errors and bool(expected_count) and generated == expected_count
    if not complete and not errors:
        warnings.append(f"benchmark is structurally valid but incomplete: {generated}/{expected_count} chapters recorded.")
    acceptance_failures: list[str] = []
    if not complete:
        acceptance_failures.append(f"benchmark is incomplete: {generated}/{expected_count} chapters recorded")
    else:
        failed_gates = [
            int(record.get("chapter_number") or 0)
            for record in records
            if isinstance(record, dict) and record.get("generated") is True and record.get("gate_passed") is not True
        ]
        p0_count = sum(
            int(record.get("p0_contradiction_count") or 0)
            for record in records
            if isinstance(record, dict) and record.get("generated") is True
        )
        pollution_count = sum(
            int(record.get("canonical_pollution_count") or 0)
            for record in records
            if isinstance(record, dict) and record.get("generated") is True
        )
        if failed_gates:
            acceptance_failures.append(
                "final gate did not pass for chapters: " + ", ".join(str(chapter) for chapter in failed_gates)
            )
        if p0_count:
            acceptance_failures.append(f"P0 contradiction count must be zero, got {p0_count}")
        if pollution_count:
            acceptance_failures.append(f"canonical pollution count must be zero, got {pollution_count}")
    acceptance_passed = complete and not acceptance_failures
    next_command = (
        f"longform-engine benchmark report project.yaml --run-id {normalized_id}"
        if generated
        else f"Edit {relative(root, run_dir / 'chapter_records.json')} after real Agent chapter runs."
    )
    return BenchmarkValidationResult(
        run_id=normalized_id,
        ok=not errors,
        complete=complete,
        acceptance_passed=acceptance_passed,
        acceptance_failures=tuple(acceptance_failures),
        errors=tuple(errors),
        warnings=tuple(warnings),
        next_command=next_command,
    )


def report_benchmark(config: ConfigDocument, *, run_id: str) -> BenchmarkReportResult:
    root = resolve_project_root(config)
    validation = validate_benchmark(config, run_id=run_id)
    if not validation.ok:
        raise ValueError("Benchmark is invalid: " + "; ".join(validation.errors))
    run_dir = benchmark_dir(root, validation.run_id)
    run = read_json(run_dir / "run.json", {})
    records = read_json(run_dir / "chapter_records.json", [])
    generated = [record for record in records if isinstance(record, dict) and record.get("generated") is True]
    scored = [
        record
        for record in generated
        if record.get("review_status") != "technical_pending"
    ]
    score_averages = {
        metric: round(mean(float(record["scores"][metric]) for record in scored), 3)
        for metric in SCORE_METRICS
    } if scored else {metric: None for metric in SCORE_METRICS}
    fanfiction_score_averages = {
        metric: round(mean(float(record["fanfiction_scores"][metric]) for record in scored), 3)
        for metric in FANFICTION_SCORE_METRICS
        if all(
            isinstance(record.get("fanfiction_scores"), dict)
            and isinstance(record["fanfiction_scores"].get(metric), (int, float))
            for record in scored
        )
    } if scored else {}
    gate_total = sum(1 for record in generated if record.get("gate_passed") is not None)
    gate_failures = sum(1 for record in generated if record.get("gate_passed") is False)
    payload = {
        "schema": BENCHMARK_REPORT_SCHEMA,
        "run_id": validation.run_id,
        "agent_product": run.get("agent_product"),
        "baseline": run.get("baseline"),
        "chapters_planned": run.get("chapter_count"),
        "chapters_recorded": len(generated),
        "complete": validation.complete,
        "acceptance_passed": validation.acceptance_passed,
        "acceptance_failures": list(validation.acceptance_failures),
        "scores": score_averages,
        "fanfiction_scores": fanfiction_score_averages,
        "gate_failure_rate": round(gate_failures / gate_total, 4) if gate_total else None,
        "repair_count": sum(int(record.get("repair_count") or 0) for record in generated),
        "need_human_count": sum(int(record.get("need_human_count") or 0) for record in generated),
        "average_context_file_count": round(mean(int(record.get("context_file_count") or 0) for record in generated), 3) if generated else None,
        "average_context_character_count": round(mean(int(record.get("context_character_count") or 0) for record in generated), 3) if generated else None,
        "p0_contradiction_count": sum(int(record.get("p0_contradiction_count") or 0) for record in generated),
        "canonical_pollution_count": sum(int(record.get("canonical_pollution_count") or 0) for record in generated),
        "judge_ids": sorted({judge for record in generated for judge in record.get("judge_ids", [])}),
        "average_composite_score": round(mean(composite_score(record) for record in scored), 3) if scored else None,
        "manuscript_bodies_included": False,
        "generated_at": utc_now(),
    }
    report_json = run_dir / "report.json"
    report_markdown = run_dir / "report.md"
    write_json(report_json, payload)
    atomic_write_text(report_markdown, render_report(payload))
    return BenchmarkReportResult(
        run_id=validation.run_id,
        report_json=relative(root, report_json),
        report_markdown=relative(root, report_markdown),
        chapters_recorded=len(generated),
        complete=validation.complete,
        acceptance_passed=validation.acceptance_passed,
        acceptance_failures=validation.acceptance_failures,
        next_command=(
            "Complete the remaining real chapter records."
            if not validation.complete
            else (
                "Resolve every acceptance failure before treating this as a passed smoke."
                if not validation.acceptance_passed
                else "Compare reports from the same setting."
            )
        ),
    )


def compare_benchmarks(
    config: ConfigDocument,
    *,
    comparison_id: str,
    run_ids: list[str] | tuple[str, ...],
    allow_incomplete: bool = False,
) -> BenchmarkComparisonResult:
    root = resolve_project_root(config)
    normalized_comparison_id = validate_run_id(comparison_id)
    normalized_run_ids = tuple(validate_run_id(run_id) for run_id in run_ids)
    if len(normalized_run_ids) < 2:
        raise ValueError("At least two benchmark run ids are required for comparison.")
    if len(set(normalized_run_ids)) != len(normalized_run_ids):
        raise ValueError("Benchmark comparison run ids must be unique.")

    runs: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    records_by_run: dict[str, list[dict[str, Any]]] = {}
    for run_id in normalized_run_ids:
        validation = validate_benchmark(config, run_id=run_id)
        if not validation.ok:
            raise ValueError(f"Benchmark {run_id} is invalid: {'; '.join(validation.errors)}")
        if not validation.complete and not allow_incomplete:
            raise ValueError(f"Benchmark {run_id} is incomplete; pass --allow-incomplete only for provisional analysis.")
        run_dir = benchmark_dir(root, run_id)
        run = read_json(run_dir / "run.json", {})
        if not allow_incomplete and (not run.get("agent_model") or not run.get("host_version")):
            raise ValueError(f"Benchmark {run_id} is missing agent_model or host_version metadata.")
        report_benchmark(config, run_id=run_id)
        report = read_json(run_dir / "report.json", {})
        runs.append(run)
        reports.append(report)
        records_by_run[run_id] = [
            item for item in read_json(run_dir / "chapter_records.json", [])
            if isinstance(item, dict) and item.get("generated") is True
        ]

    scenario_ids = {str(run.get("scenario_id") or run.get("project_slug") or "") for run in runs}
    chapter_counts = {int(run.get("chapter_count") or 0) for run in runs}
    if len(scenario_ids) != 1:
        raise ValueError("Benchmark runs must use the same scenario_id.")
    if len(chapter_counts) != 1:
        raise ValueError("Benchmark runs must use the same chapter_count.")

    best_by_metric: dict[str, str] = {}
    for metric in SCORE_METRICS:
        scored = [
            (float(report["scores"][metric]), str(report["run_id"]))
            for report in reports
            if isinstance(report.get("scores"), dict) and report["scores"].get(metric) is not None
        ]
        if scored:
            reverse = metric != "ai_taste"
            best_by_metric[metric] = sorted(scored, key=lambda item: item[0], reverse=reverse)[0][1]

    claim_eligible, claim_reasons, superiority = assess_superiority_claim(
        runs,
        reports,
        records_by_run,
        chapter_count=next(iter(chapter_counts)),
        root=root,
    )
    payload = {
        "schema": BENCHMARK_COMPARISON_SCHEMA,
        "comparison_id": normalized_comparison_id,
        "scenario_id": next(iter(scenario_ids)),
        "chapter_count": next(iter(chapter_counts)),
        "allow_incomplete": allow_incomplete,
        "run_ids": list(normalized_run_ids),
        "runs": reports,
        "best_by_metric": best_by_metric,
        "superiority_assessments": superiority,
        "claim_eligible": claim_eligible,
        "claim_reasons": claim_reasons,
        "manuscript_bodies_included": False,
        "generated_at": utc_now(),
    }
    comparison_dir = root / "70_runtime" / "benchmarks" / "comparisons"
    comparison_json = comparison_dir / f"{normalized_comparison_id}.json"
    comparison_markdown = comparison_dir / f"{normalized_comparison_id}.md"
    write_json(comparison_json, payload)
    atomic_write_text(comparison_markdown, render_comparison(payload))
    return BenchmarkComparisonResult(
        schema=BENCHMARK_COMPARISON_SCHEMA,
        comparison_id=normalized_comparison_id,
        comparison_json=relative(root, comparison_json),
        comparison_markdown=relative(root, comparison_markdown),
        run_ids=normalized_run_ids,
        best_by_metric=best_by_metric,
        claim_eligible=claim_eligible,
        claim_reasons=tuple(claim_reasons),
        next_command=(
            "Quality claim threshold passed; retain blind-review evidence with the release."
            if claim_eligible
            else "Do not claim superiority; resolve every comparison claim_reasons item first."
        ),
    )


def empty_chapter_record(chapter: int) -> dict[str, Any]:
    return {
        "chapter_number": chapter,
        "generated": False,
        "scores": {metric: None for metric in SCORE_METRICS},
        "fanfiction_scores": {},
        "gate_passed": None,
        "repair_count": 0,
        "need_human_count": 0,
        "context_file_count": None,
        "context_character_count": None,
        "p0_contradiction_count": 0,
        "canonical_pollution_count": 0,
        "judge_ids": [],
        "review_status": "pending",
        "character_drift": [],
        "foreshadowing_leaks": [],
        "ai_taste_issues": [],
        "artifact_hashes": {},
        "notes": "",
    }


def validate_record(record: Any, *, expected_chapter: int) -> list[str]:
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record must be an object."]
    if record.get("chapter_number") != expected_chapter:
        errors.append(f"chapter_number must be {expected_chapter}.")
    if not isinstance(record.get("generated"), bool):
        errors.append("generated must be boolean.")
    scores = record.get("scores")
    if not isinstance(scores, dict) or set(scores) != set(SCORE_METRICS):
        errors.append("scores must contain exactly the six fixed score metrics.")
    review_status = record.get("review_status", "diagnostic" if record.get("generated") else "pending")
    if review_status not in {"pending", "technical_pending", "diagnostic", "blind_aggregated"}:
        errors.append("review_status must be pending, technical_pending, diagnostic, or blind_aggregated.")
    if record.get("generated") is True and isinstance(scores, dict):
        if review_status == "technical_pending":
            if any(scores.get(metric) is not None for metric in SCORE_METRICS):
                errors.append("technical_pending records must not contain literary scores.")
        else:
            for metric in SCORE_METRICS:
                value = scores.get(metric)
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not 1 <= value <= 10:
                    errors.append(f"scores.{metric} must be between 1 and 10 for a generated chapter.")
        fanfiction_scores = record.get("fanfiction_scores")
        if not isinstance(fanfiction_scores, dict):
            errors.append("fanfiction_scores must be an object.")
        elif set(fanfiction_scores) - set(FANFICTION_SCORE_METRICS):
            errors.append("fanfiction_scores contains unknown metrics.")
        else:
            for metric, value in fanfiction_scores.items():
                if not isinstance(value, (int, float)) or isinstance(value, bool) or not 1 <= value <= 10:
                    errors.append(f"fanfiction_scores.{metric} must be between 1 and 10.")
        if not isinstance(record.get("gate_passed"), bool):
            errors.append("gate_passed must be boolean for a generated chapter.")
        for metric in COUNT_METRICS:
            value = record.get(metric)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"{metric} must be a non-negative integer for a generated chapter.")
    for field in ("character_drift", "foreshadowing_leaks", "ai_taste_issues"):
        if not isinstance(record.get(field), list):
            errors.append(f"{field} must be a list.")
    artifact_hashes = record.get("artifact_hashes", {})
    if not isinstance(artifact_hashes, dict):
        errors.append("artifact_hashes must be an object.")
    else:
        unknown_artifacts = set(artifact_hashes) - set(CHAPTER_ARTIFACT_PATHS)
        if unknown_artifacts:
            errors.append("artifact_hashes contains unknown artifact names.")
        for artifact_name, item in artifact_hashes.items():
            if not isinstance(item, dict):
                errors.append(f"artifact_hashes.{artifact_name} must be an object.")
                continue
            if set(item) != {"path", "sha256"}:
                errors.append(f"artifact_hashes.{artifact_name} must contain path and sha256.")
                continue
            path = item.get("path")
            digest = item.get("sha256")
            if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
                errors.append(f"artifact_hashes.{artifact_name}.path must be a safe project-relative path.")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                errors.append(f"artifact_hashes.{artifact_name}.sha256 must be a lowercase SHA-256 digest.")
    if "manuscript" in record or "chapter_body" in record or "prose" in record:
        errors.append("benchmark records must not contain manuscript bodies.")
    notes = record.get("notes")
    if not isinstance(notes, str) or len(notes) > 1000:
        errors.append("notes must be a string with at most 1000 characters.")
    for field in ("character_drift", "foreshadowing_leaks", "ai_taste_issues"):
        values = record.get(field)
        if isinstance(values, list) and (len(values) > 20 or any(not isinstance(value, str) or len(value) > 300 for value in values)):
            errors.append(f"{field} must contain at most 20 strings of at most 300 characters.")
    judge_ids = record.get("judge_ids")
    if not isinstance(judge_ids, list) or len(judge_ids) != len(set(judge_ids)):
        errors.append("judge_ids must be a unique list.")
    elif any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value) for value in judge_ids):
        errors.append("judge_ids must contain safe 1-64 character evaluator ids.")
    return errors


def collect_chapter_artifact_hashes(root: Path, chapter_number: int) -> dict[str, dict[str, str]]:
    artifacts: dict[str, dict[str, str]] = {}
    for name, path_template in CHAPTER_ARTIFACT_PATHS.items():
        path = root / path_template.format(chapter=chapter_number)
        if not path.is_file():
            continue
        artifacts[name] = {
            "path": relative(root, path),
            "sha256": sha256(path.read_bytes()).hexdigest(),
        }
    return artifacts


def composite_score(record: dict[str, Any]) -> float:
    scores = record.get("scores") if isinstance(record.get("scores"), dict) else {}
    return sum(
        QUALITY_WEIGHTS[metric] * (
            11.0 - float(scores.get(metric) or 0)
            if metric == "ai_taste"
            else float(scores.get(metric) or 0)
        )
        for metric in SCORE_METRICS
    )


def assess_superiority_claim(
    runs: list[dict[str, Any]],
    reports: list[dict[str, Any]],
    records_by_run: dict[str, list[dict[str, Any]]],
    *,
    chapter_count: int,
    root: Path,
) -> tuple[bool, list[str], list[dict[str, Any]]]:
    report_by_id = {str(item.get("run_id")): item for item in reports}
    run_by_id = {str(item.get("run_id")): item for item in runs}
    baselines = [run for run in runs if run.get("agent_product") == "novel-skill"]
    candidates = [run for run in runs if run.get("agent_product") in {"codex", "claude-code"}]
    global_reasons: list[str] = []
    assessments: list[dict[str, Any]] = []
    if chapter_count < 10:
        global_reasons.append("Formal superiority evidence requires at least 10 chapters per run.")
    if not baselines:
        global_reasons.append("No novel-skill baseline run is present.")
    if not candidates:
        global_reasons.append("No longform Codex or Claude Code candidate run is present.")
    for candidate in candidates:
        candidate_id = str(candidate.get("run_id"))
        candidate_report = report_by_id[candidate_id]
        matching_baselines = [
            baseline
            for baseline in baselines
            if baseline.get("host_product") == candidate.get("host_product")
        ]
        if not matching_baselines:
            global_reasons.append(
                f"No novel-skill baseline uses host_product={candidate.get('host_product') or 'missing'} "
                f"for candidate {candidate_id}."
            )
        for baseline in matching_baselines:
            baseline_id = str(baseline.get("run_id"))
            baseline_report = report_by_id[baseline_id]
            if any(
                not isinstance(report.get("scores"), dict)
                or any(report["scores"].get(metric) is None for metric in SCORE_METRICS)
                for report in (candidate_report, baseline_report)
            ):
                assessments.append(
                    {
                        "candidate_run_id": candidate_id,
                        "baseline_run_id": baseline_id,
                        "composite_delta": None,
                        "chapter_wins": 0,
                        "dimension_deltas": {},
                        "eligible": False,
                        "reasons": [
                            "Formal literary scores are pending blind-review aggregation."
                        ],
                    }
                )
                continue
            dimension_deltas = {
                metric: round(
                    (
                        float(baseline_report["scores"][metric]) - float(candidate_report["scores"][metric])
                        if metric == "ai_taste"
                        else float(candidate_report["scores"][metric]) - float(baseline_report["scores"][metric])
                    ),
                    3,
                )
                for metric in SCORE_METRICS
            }
            candidate_records = records_by_run[candidate_id]
            baseline_records = records_by_run[baseline_id]
            chapter_wins = sum(
                1
                for candidate_record, baseline_record in zip(candidate_records, baseline_records)
                if composite_score(candidate_record) > composite_score(baseline_record)
            )
            composite_delta = round(
                float(candidate_report.get("average_composite_score") or 0)
                - float(baseline_report.get("average_composite_score") or 0),
                3,
            )
            reasons: list[str] = []
            if not candidate.get("scenario_sha256") or candidate.get("scenario_sha256") != baseline.get("scenario_sha256"):
                reasons.append("Candidate and baseline scenario_sha256 values are missing or differ.")
            if not candidate.get("host_product") or candidate.get("host_product") != baseline.get("host_product"):
                reasons.append("Candidate and baseline host_product labels are missing or differ.")
            if candidate.get("agent_model") != baseline.get("agent_model"):
                reasons.append("Candidate and baseline agent_model labels differ.")
            if candidate.get("host_version") != baseline.get("host_version"):
                reasons.append("Candidate and baseline host_version labels differ.")
            if not candidate.get("workflow_version") or not baseline.get("workflow_version"):
                reasons.append("Candidate or baseline workflow_version is missing.")
            if candidate_report.get("acceptance_passed") is not True:
                reasons.append("Candidate engineering acceptance did not pass.")
            if baseline_report.get("acceptance_passed") is not True:
                reasons.append("Baseline engineering acceptance did not pass.")
            if composite_delta < 0.5:
                reasons.append("Composite blind score lead is below 0.5/10.")
            if chapter_wins < 7:
                reasons.append("Candidate wins fewer than 7 chapter-level comparisons.")
            if any(delta < -0.3 for delta in dimension_deltas.values()):
                reasons.append("At least one core literary dimension trails by more than 0.3/10.")
            if int(candidate_report.get("p0_contradiction_count") or 0) != 0:
                reasons.append("Candidate has P0 continuity, character, or fact contradictions.")
            if int(candidate_report.get("canonical_pollution_count") or 0) != 0:
                reasons.append("Candidate canonical pollution count is not zero.")
            if int(candidate_report.get("repair_count") or 0) > int(baseline_report.get("repair_count") or 0):
                reasons.append("Candidate repair count exceeds baseline.")
            if int(candidate_report.get("need_human_count") or 0) > int(baseline_report.get("need_human_count") or 0):
                reasons.append("Candidate need-human count exceeds baseline.")
            for report, label in ((candidate_report, "candidate"), (baseline_report, "baseline")):
                if len(report.get("judge_ids") or []) < 3:
                    reasons.append(f"{label} has fewer than three independent evaluator ids.")
            from longform_engine.blind_review import formal_blind_review_errors

            reasons.extend(
                formal_blind_review_errors(
                    root,
                    candidate_id=candidate_id,
                    baseline_id=baseline_id,
                    candidate_records=candidate_records,
                    baseline_records=baseline_records,
                )
            )
            rag_evidence = read_json(benchmark_dir(root, candidate_id) / "rag_scale_evidence.json", {})
            rag_errors = rag_threshold_errors(rag_evidence if isinstance(rag_evidence, dict) else {})
            reasons.extend(f"RAG evidence: {item}" for item in rag_errors)
            assessments.append(
                {
                    "candidate_run_id": candidate_id,
                    "baseline_run_id": baseline_id,
                    "composite_delta": composite_delta,
                    "chapter_wins": chapter_wins,
                    "dimension_deltas": dimension_deltas,
                    "eligible": chapter_count >= 10 and not reasons,
                    "reasons": reasons,
                }
            )
    eligible = chapter_count >= 10 and bool(assessments) and all(item["eligible"] for item in assessments)
    all_reasons = list(global_reasons)
    for item in assessments:
        all_reasons.extend(
            f"{item['candidate_run_id']} vs {item['baseline_run_id']}: {reason}"
            for reason in item["reasons"]
        )
    return eligible and not global_reasons, all_reasons, assessments


def rag_threshold_errors(payload: dict[str, Any], *, require_claim_grade: bool = True) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != RAG_BENCHMARK_SCHEMA:
        return ["missing valid rag_scale_evidence_v1."]
    if require_claim_grade:
        if payload.get("measurement_source") != "engine_runner":
            errors.append("metrics were manually recorded instead of produced by an engine runner.")
        if payload.get("evidence_grade") != "production_model":
            errors.append("evidence grade is not production_model.")
        if payload.get("fallback_active") is not False:
            errors.append("semantic fallback is active or was not recorded.")
        if not str(payload.get("embedding_model") or "").strip():
            errors.append("production embedding model identity is missing.")
        if not str(payload.get("reranker_model") or "").strip():
            errors.append("production reranker model identity is missing.")
        if not str(payload.get("vector_backend") or "").strip():
            errors.append("vector backend identity is missing.")
        if not is_sha256(payload.get("backend_config_hash")):
            errors.append("vector backend configuration hash is missing or invalid.")
        if not is_sha256(payload.get("dataset_sha256")):
            errors.append("production query dataset hash is missing or invalid.")
        if not is_sha256(payload.get("source_merkle_root")):
            errors.append("final-manuscript source merkle root is missing or invalid.")
        if int(payload.get("source_chapter_count") or 0) < RAG_CLAIM_THRESHOLDS["scale_chapters"]:
            errors.append("source chapter count is below 500.")
        if int(payload.get("query_count") or 0) < RAG_MIN_QUERY_COUNT:
            errors.append("production query count is below 50.")
        category_counts = payload.get("category_counts")
        if not isinstance(category_counts, dict) or any(
            int(category_counts.get(category) or 0) < 1
            for category in RAG_REQUIRED_CATEGORIES
        ):
            errors.append("production queries do not cover every required retrieval category.")
        if not str(payload.get("incremental_index_mode") or "").strip():
            errors.append("incremental indexing measurement mode is missing.")
        if float(payload.get("incremental_index_ms") or -1) < 0:
            errors.append("incremental indexing latency is missing or invalid.")
    if numeric_value(payload.get("scale_chapters"), default=0) < RAG_CLAIM_THRESHOLDS["scale_chapters"]:
        errors.append("scale is below 500 chapters.")
    if numeric_value(payload.get("recall_at_k"), default=0) < RAG_CLAIM_THRESHOLDS["recall_at_k_min"]:
        errors.append("recall_at_k is below 0.85.")
    if numeric_value(payload.get("fact_error_rate"), default=1) > RAG_CLAIM_THRESHOLDS["fact_error_rate_max"]:
        errors.append("fact_error_rate exceeds 0.02.")
    if numeric_value(payload.get("p95_query_ms"), default=float("inf")) > RAG_CLAIM_THRESHOLDS["p95_query_ms_max"]:
        errors.append("P95 query latency exceeds 1000 ms.")
    return errors


def numeric_value(value: Any, *, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def clean_judge_ids(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", normalized):
            raise ValueError("judge_ids must use 1-64 letters, digits, dot, underscore, or hyphen.")
        if normalized not in result:
            result.append(normalized)
    return result


def record_rag_benchmark(
    config: ConfigDocument,
    *,
    run_id: str,
    scale_chapters: int,
    recall_at_k: float,
    fact_error_rate: float,
    p95_query_ms: float,
    incremental_index_ms: float,
) -> RagBenchmarkRecordResult:
    root = resolve_project_root(config)
    normalized_id = validate_run_id(run_id)
    run_dir = benchmark_dir(root, normalized_id)
    run = read_json(run_dir / "run.json", {})
    if not isinstance(run, dict) or run.get("schema") != BENCHMARK_SCHEMA:
        raise ValueError(f"Benchmark run does not exist or is invalid: {normalized_id}")
    errors: list[str] = []
    if scale_chapters < 1:
        errors.append("scale_chapters must be positive.")
    if not 0 <= recall_at_k <= 1 or not 0 <= fact_error_rate <= 1:
        errors.append("recall_at_k and fact_error_rate must be between 0 and 1.")
    if p95_query_ms < 0 or incremental_index_ms < 0:
        errors.append("latency metrics must be non-negative.")
    if errors:
        raise ValueError("RAG benchmark evidence is invalid: " + "; ".join(errors))
    threshold_errors = rag_threshold_errors(
        {
            "schema": RAG_BENCHMARK_SCHEMA,
            "scale_chapters": scale_chapters,
            "recall_at_k": recall_at_k,
            "fact_error_rate": fact_error_rate,
            "p95_query_ms": p95_query_ms,
        },
        require_claim_grade=False,
    )
    payload = {
        "schema": RAG_BENCHMARK_SCHEMA,
        "run_id": normalized_id,
        "scale_chapters": scale_chapters,
        "recall_at_k": recall_at_k,
        "fact_error_rate": fact_error_rate,
        "p95_query_ms": p95_query_ms,
        "incremental_index_ms": incremental_index_ms,
        "measurement_source": "manual",
        "evidence_grade": "manual_record",
        "claim_eligible": False,
        "thresholds": RAG_CLAIM_THRESHOLDS,
        "meets_thresholds": not threshold_errors,
        "threshold_errors": threshold_errors,
        "recorded_at": utc_now(),
    }
    evidence_file = run_dir / "rag_scale_evidence.json"
    write_json(evidence_file, payload)
    return RagBenchmarkRecordResult(
        schema=RAG_BENCHMARK_SCHEMA,
        run_id=normalized_id,
        evidence_file=relative(root, evidence_file),
        meets_thresholds=not threshold_errors,
        errors=tuple(threshold_errors),
    )


def benchmark_dir(root: Path, run_id: str) -> Path:
    return root / "70_runtime" / "benchmarks" / run_id


def validate_run_id(run_id: str) -> str:
    value = str(run_id).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise ValueError("run_id must use 1-64 letters, digits, dot, underscore, or hyphen.")
    if value.lower() == "comparisons":
        raise ValueError("run_id 'comparisons' is reserved for comparison reports.")
    return value


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        f"# Quality Benchmark: {payload['run_id']}",
        "",
        f"- Agent product: `{payload.get('agent_product')}`",
        f"- Chapters: `{payload.get('chapters_recorded')}/{payload.get('chapters_planned')}`",
        f"- Complete: `{payload.get('complete')}`",
        f"- Acceptance passed: `{payload.get('acceptance_passed')}`",
        f"- Gate failure rate: `{payload.get('gate_failure_rate')}`",
        f"- Repair count: `{payload.get('repair_count')}`",
        f"- Need-human count: `{payload.get('need_human_count')}`",
        f"- Average context files: `{payload.get('average_context_file_count')}`",
        f"- Average context characters: `{payload.get('average_context_character_count')}`",
        f"- P0 contradictions: `{payload.get('p0_contradiction_count')}`",
        f"- Canonical pollution: `{payload.get('canonical_pollution_count')}`",
        f"- Independent evaluator IDs: `{len(payload.get('judge_ids') or [])}`",
        f"- Composite quality score: `{payload.get('average_composite_score')}`",
        "",
        "## Acceptance Failures",
        "",
        *([f"- {item}" for item in payload.get("acceptance_failures", [])] or ["- None"]),
        "",
        "## Scores",
        "",
    ]
    lines.extend(f"- {metric}: `{payload['scores'].get(metric)}`" for metric in SCORE_METRICS)
    lines.extend(("", "This report contains metrics and notes only; manuscript bodies are excluded.", ""))
    return "\n".join(lines)


def render_comparison(payload: dict[str, Any]) -> str:
    lines = [
        f"# Quality Benchmark Comparison: {payload['comparison_id']}",
        "",
        f"- Scenario: `{payload.get('scenario_id')}`",
        f"- Chapters per run: `{payload.get('chapter_count')}`",
        f"- Provisional: `{payload.get('allow_incomplete')}`",
        "",
        "| Run | Product | Recorded | Gate failure | Repairs | Need human | Context files | Continuity | Character | Foreshadowing | Pacing | Payoff | AI taste |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for report in payload.get("runs", []):
        scores = report.get("scores", {})
        lines.append(
            "| {run_id} | {agent_product} | {chapters_recorded}/{chapters_planned} | {gate_failure_rate} | "
            "{repair_count} | {need_human_count} | {average_context_file_count} | {continuity} | "
            "{character_consistency} | {foreshadowing_control} | {pacing} | {reader_payoff} | {ai_taste} |".format(
                **report,
                **scores,
            )
        )
    lines.extend(("", "## Best By Metric", ""))
    lines.extend(f"- {metric}: `{run_id}`" for metric, run_id in payload.get("best_by_metric", {}).items())
    lines.extend(("", "## Public Claim Gate", ""))
    lines.append(f"- Eligible: `{payload.get('claim_eligible')}`")
    lines.extend(f"- Blocker: {reason}" for reason in payload.get("claim_reasons", []))
    lines.extend(("", "This comparison contains metrics and notes only; manuscript bodies are excluded.", ""))
    return "\n".join(lines)


def clean_metadata(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > 200 or "\n" in normalized or "\r" in normalized:
        raise ValueError(f"{field} must be a single line with at most 200 characters.")
    return normalized


def clean_annotation(value: str, *, field: str, max_length: int) -> str:
    normalized = str(value or "").strip()
    if len(normalized) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters.")
    return normalized


def clean_annotations(values: list[str], *, field: str) -> list[str]:
    if len(values) > 20:
        raise ValueError(f"{field} accepts at most 20 entries.")
    return [clean_annotation(value, field=field, max_length=300) for value in values]


def write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
