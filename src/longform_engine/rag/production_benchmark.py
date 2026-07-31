"""Claim-grade RAG benchmark over finalized Chinese novel chapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import platform
from pathlib import Path
import re
from statistics import mean
from time import perf_counter
from typing import Any

from longform_engine.benchmark import (
    BENCHMARK_SCHEMA,
    RAG_BENCHMARK_SCHEMA,
    RAG_CLAIM_THRESHOLDS,
    RAG_MIN_QUERY_COUNT,
    RAG_REQUIRED_CATEGORIES,
    benchmark_dir,
    rag_threshold_errors,
    validate_run_id,
)
from longform_engine.config import ConfigDocument
from longform_engine.models import verify_models
from longform_engine.rag.pipeline import build_chunks, build_embeddings, query
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import FINAL_MANUSCRIPT_DIRECTORY
from longform_engine.vectorstore import healthcheck


PRODUCTION_DATASET_SCHEMA = "rag_production_dataset_v1"
REQUIRED_QUERY_CATEGORIES = RAG_REQUIRED_CATEGORIES
MIN_QUERY_COUNT = RAG_MIN_QUERY_COUNT


@dataclass(frozen=True)
class RagProductionTemplateResult:
    schema: str
    template_file: str
    minimum_query_count: int
    required_categories: tuple[str, ...]


@dataclass(frozen=True)
class RagProductionBenchmarkResult:
    schema: str
    run_id: str
    evidence_file: str
    claim_eligible: bool
    scale_chapters: int
    query_count: int
    recall_at_k: float
    fact_error_rate: float
    p95_query_ms: float
    incremental_index_ms: float
    errors: tuple[str, ...]


def write_rag_production_template(
    config: ConfigDocument,
    *,
    output: str | Path | None = None,
) -> RagProductionTemplateResult:
    """Write a prose-free dataset skeleton for human/Agent evidence annotation."""

    root = resolve_project_root(config)
    target = (
        Path(output).expanduser().resolve()
        if output is not None
        else root / "50_workbench" / "benchmark_tasks" / "rag_production_dataset.template.json"
    )
    example = {
        "query_id": "q001",
        "category": "entity_alias",
        "query": "用自然语言填写一个可由既有定稿事实回答的问题",
        "chapter_number": 501,
        "expected": [
            {
                "source_path": f"{FINAL_MANUSCRIPT_DIRECTORY}/ch001.md",
                "source_sha256": "<sha256>",
                "evidence_span": "<定稿中的短证据片段>",
            }
        ],
        "forbidden": [],
    }
    payload = {
        "schema": PRODUCTION_DATASET_SCHEMA,
        "dataset_id": "replace-with-stable-dataset-id",
        "description": "At least 50 independently annotated Chinese-webnovel retrieval queries.",
        "minimum_query_count": MIN_QUERY_COUNT,
        "required_categories": list(REQUIRED_QUERY_CATEGORIES),
        "annotation_rules": [
            "Every evidence reference must point to a finalized manuscript file.",
            "source_sha256 must match the complete current source file.",
            "evidence_span must occur verbatim in that source file.",
            "Do not include complete chapters or long manuscript passages in this dataset.",
        ],
        "example_query_not_counted": example,
        "queries": [],
    }
    write_json(target, payload)
    return RagProductionTemplateResult(
        schema=PRODUCTION_DATASET_SCHEMA,
        template_file=relative(root, target),
        minimum_query_count=MIN_QUERY_COUNT,
        required_categories=REQUIRED_QUERY_CATEGORIES,
    )


def run_rag_production_benchmark(
    config: ConfigDocument,
    *,
    run_id: str,
    dataset_file: str | Path,
    top_k: int = 10,
) -> RagProductionBenchmarkResult:
    """Run claim-grade retrieval measurements using real models and final prose."""

    if not 1 <= top_k <= 100:
        raise ValueError("top_k must be between 1 and 100.")
    root = resolve_project_root(config)
    normalized_run_id = validate_run_id(run_id)
    run_dir = benchmark_dir(root, normalized_run_id)
    run = read_object(run_dir / "run.json")
    if run.get("schema") != BENCHMARK_SCHEMA:
        raise ValueError(f"Benchmark run does not exist or is invalid: {normalized_run_id}")

    model_status = verify_models(config)
    readiness_errors = production_model_errors(model_status)
    final_sources = finalized_sources(root)
    if len(final_sources) < RAG_CLAIM_THRESHOLDS["scale_chapters"]:
        readiness_errors.append(
            f"Production RAG evidence requires at least 500 final chapters; found {len(final_sources)}."
        )
    if readiness_errors:
        raise ValueError("Production RAG benchmark preflight failed: " + "; ".join(readiness_errors))
    vector_status = healthcheck(config)
    if not vector_status.ok:
        raise ValueError(f"Production RAG benchmark preflight failed: Vector store health failed: {vector_status.message}")

    dataset_path = Path(dataset_file).expanduser().resolve()
    dataset = read_object(dataset_path)
    queries, category_counts = validate_dataset(
        dataset,
        root=root,
        source_hashes={item["source_path"]: item["sha256"] for item in final_sources},
    )

    initial_started = perf_counter()
    build_stats = build_chunks(config, with_embeddings=True)
    initial_index_ms = elapsed_ms(initial_started)
    if build_stats.chapters != len(final_sources) or build_stats.embeddings <= 0:
        raise ValueError(
            "Production RAG benchmark index build did not cover every final chapter: "
            f"final={len(final_sources)}, indexed={build_stats.chapters}, "
            f"embeddings={build_stats.embeddings}."
        )
    vector_status = healthcheck(config)
    if not vector_status.ok or vector_status.record_count <= 0:
        raise ValueError(
            "Production RAG benchmark index build failed: "
            f"{vector_status.message}; active={vector_status.record_count}."
        )

    latencies: list[float] = []
    recalled = 0
    fact_errors = 0
    category_results = {
        category: {"queries": 0, "recalled": 0, "fact_errors": 0}
        for category in REQUIRED_QUERY_CATEGORIES
    }
    for item in queries:
        started = perf_counter()
        result = query(
            config,
            str(item["query"]),
            top_k=top_k,
            semantic=True,
            chapter_number=int(item["chapter_number"]),
        )
        latencies.append(elapsed_ms(started))
        was_recalled = any(
            hit_matches_reference(hit, reference)
            for hit in result.hits
            for reference in item["expected"]
        )
        top_hit_forbidden = bool(result.hits) and any(
            hit_matches_reference(result.hits[0], reference)
            for reference in item["forbidden"]
        )
        if was_recalled:
            recalled += 1
        if top_hit_forbidden:
            fact_errors += 1
        category_result = category_results[item["category"]]
        category_result["queries"] += 1
        category_result["recalled"] += int(was_recalled)
        category_result["fact_errors"] += int(top_hit_forbidden)

    incremental_started = perf_counter()
    incremental_embedding_count = build_embeddings(config)
    incremental_index_ms = elapsed_ms(incremental_started)
    recall_at_k = recalled / len(queries)
    fact_error_rate = fact_errors / len(queries)
    p95_query_ms = percentile(latencies, 0.95)
    source_merkle_root = merkle_root(final_sources)
    backend = configured_vector_store(config)
    payload = {
        "schema": RAG_BENCHMARK_SCHEMA,
        "run_id": normalized_run_id,
        "measurement_source": "engine_runner",
        "evidence_grade": "production_model",
        "scale_chapters": len(final_sources),
        "source_chapter_count": len(final_sources),
        "source_merkle_root": source_merkle_root,
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": sha256(dataset_path.read_bytes()).hexdigest(),
        "query_count": len(queries),
        "category_counts": category_counts,
        "category_metrics": {
            category: {
                "query_count": values["queries"],
                "recall_at_k": round(values["recalled"] / values["queries"], 6),
                "fact_error_rate": round(values["fact_errors"] / values["queries"], 6),
            }
            for category, values in category_results.items()
        },
        "top_k": top_k,
        "recall_at_k": round(recall_at_k, 6),
        "fact_error_rate": round(fact_error_rate, 6),
        "p95_query_ms": round(p95_query_ms, 3),
        "mean_query_ms": round(mean(latencies), 3),
        "initial_index_ms": round(initial_index_ms, 3),
        "incremental_index_ms": round(incremental_index_ms, 3),
        "incremental_index_mode": "no-change synchronization after full production index",
        "incremental_embedding_count": incremental_embedding_count,
        "indexed_chapters": build_stats.chapters,
        "indexed_chunks": build_stats.chunks,
        "indexed_embeddings": build_stats.embeddings,
        "embedding_model": model_status.embedding_model,
        "reranker_model": model_status.reranker_model,
        "model_profile": model_status.profile,
        "fallback_active": model_status.fallback_active,
        "vector_backend": backend.get("backend"),
        "backend_config_hash": sha256(canonical_json(backend).encode("utf-8")).hexdigest(),
        "python_version": platform.python_version(),
        "thresholds": RAG_CLAIM_THRESHOLDS,
    }
    threshold_errors = rag_threshold_errors(payload)
    payload["meets_thresholds"] = not threshold_errors
    payload["claim_eligible"] = not threshold_errors
    payload["threshold_errors"] = threshold_errors
    evidence_file = run_dir / "rag_scale_evidence.json"
    write_json(evidence_file, payload)
    return RagProductionBenchmarkResult(
        schema=RAG_BENCHMARK_SCHEMA,
        run_id=normalized_run_id,
        evidence_file=relative(root, evidence_file),
        claim_eligible=not threshold_errors,
        scale_chapters=len(final_sources),
        query_count=len(queries),
        recall_at_k=payload["recall_at_k"],
        fact_error_rate=payload["fact_error_rate"],
        p95_query_ms=payload["p95_query_ms"],
        incremental_index_ms=payload["incremental_index_ms"],
        errors=tuple(threshold_errors),
    )


def production_model_errors(status: Any) -> list[str]:
    errors: list[str] = []
    if status.profile == "local-hash":
        errors.append("local-hash is not a production embedding profile.")
    if not status.embedding_loadable:
        errors.append("configured production embedding model is not loadable.")
    if not status.reranker_loadable:
        errors.append("configured production reranker model is not loadable.")
    if not status.provider_ready:
        errors.append("configured semantic model provider is not fully ready.")
    if status.fallback_active:
        errors.append("semantic fallback is active.")
    return errors


def validate_dataset(
    payload: dict[str, Any],
    *,
    root: Path,
    source_hashes: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    errors: list[str] = []
    if payload.get("schema") != PRODUCTION_DATASET_SCHEMA:
        errors.append(f"schema must be {PRODUCTION_DATASET_SCHEMA}.")
    if not isinstance(payload.get("dataset_id"), str) or not payload["dataset_id"].strip():
        errors.append("dataset_id is required.")
    rows = payload.get("queries")
    if not isinstance(rows, list) or len(rows) < MIN_QUERY_COUNT:
        errors.append(f"queries must contain at least {MIN_QUERY_COUNT} rows.")
        rows = rows if isinstance(rows, list) else []
    seen_ids: set[str] = set()
    category_counts = {category: 0 for category in REQUIRED_QUERY_CATEGORIES}
    normalized_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        prefix = f"queries[{index - 1}]"
        if not isinstance(row, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        query_id = str(row.get("query_id") or "").strip()
        if not query_id or query_id in seen_ids:
            errors.append(f"{prefix}.query_id must be non-empty and unique.")
        seen_ids.add(query_id)
        category = str(row.get("category") or "")
        if category not in category_counts:
            errors.append(f"{prefix}.category is unsupported.")
        else:
            category_counts[category] += 1
        query_text = row.get("query")
        if not isinstance(query_text, str) or not query_text.strip():
            errors.append(f"{prefix}.query is required.")
        chapter_number = row.get("chapter_number")
        if (
            not isinstance(chapter_number, int)
            or isinstance(chapter_number, bool)
            or chapter_number < 2
        ):
            errors.append(f"{prefix}.chapter_number must be an integer of at least 2.")
        expected = validate_references(
            row.get("expected"),
            prefix=f"{prefix}.expected",
            root=root,
            source_hashes=source_hashes,
            required=True,
            errors=errors,
        )
        forbidden = validate_references(
            row.get("forbidden"),
            prefix=f"{prefix}.forbidden",
            root=root,
            source_hashes=source_hashes,
            required=False,
            errors=errors,
        )
        if isinstance(chapter_number, int) and not isinstance(chapter_number, bool):
            for reference in [*expected, *forbidden]:
                source_chapter = parse_chapter_number(Path(reference["source_path"]).stem)
                if source_chapter is None or source_chapter >= chapter_number:
                    errors.append(
                        f"{prefix} references chapter {source_chapter or 'unknown'} "
                        f"outside its historical cutoff {chapter_number - 1}."
                    )
        normalized_rows.append(
            {
                "query_id": query_id,
                "category": category,
                "query": str(query_text or ""),
                "chapter_number": int(chapter_number or 0),
                "expected": expected,
                "forbidden": forbidden,
            }
        )
    missing_categories = [name for name, count in category_counts.items() if count == 0]
    if missing_categories:
        errors.append("queries do not cover required categories: " + ", ".join(missing_categories))
    if errors:
        raise ValueError("Production RAG dataset is invalid: " + "; ".join(errors))
    return normalized_rows, category_counts


def validate_references(
    value: Any,
    *,
    prefix: str,
    root: Path,
    source_hashes: dict[str, str],
    required: bool,
    errors: list[str],
) -> list[dict[str, str]]:
    if not isinstance(value, list) or (required and not value):
        errors.append(f"{prefix} must be {'a non-empty' if required else 'an'} array.")
        return []
    normalized: list[dict[str, str]] = []
    for index, reference in enumerate(value):
        item_prefix = f"{prefix}[{index}]"
        if not isinstance(reference, dict):
            errors.append(f"{item_prefix} must be an object.")
            continue
        source_path = str(reference.get("source_path") or "").replace("\\", "/")
        source_sha = str(reference.get("source_sha256") or "")
        evidence_span = str(reference.get("evidence_span") or "")
        if not source_path.startswith(f"{FINAL_MANUSCRIPT_DIRECTORY}/") or source_path not in source_hashes:
            errors.append(f"{item_prefix}.source_path must reference an indexed final chapter.")
            continue
        if source_sha != source_hashes[source_path]:
            errors.append(f"{item_prefix}.source_sha256 does not match the current final chapter.")
        source = (root / source_path).resolve()
        try:
            source.relative_to((root / FINAL_MANUSCRIPT_DIRECTORY).resolve())
        except ValueError:
            errors.append(f"{item_prefix}.source_path escapes the final manuscript directory.")
            continue
        if len(evidence_span.strip()) < 4 or evidence_span not in source.read_text(encoding="utf-8"):
            errors.append(f"{item_prefix}.evidence_span is missing from the referenced final chapter.")
        normalized.append(
            {
                "source_path": source_path,
                "source_sha256": source_sha,
                "evidence_span": evidence_span,
            }
        )
    return normalized


def hit_matches_reference(hit: Any, reference: dict[str, str]) -> bool:
    hit_path = str(hit.source_path or "").replace("\\", "/")
    return (
        hit_path == reference["source_path"]
        and reference["evidence_span"] in str(hit.text or "")
    )


def finalized_sources(root: Path) -> list[dict[str, Any]]:
    final_dir = root / FINAL_MANUSCRIPT_DIRECTORY
    paths = sorted([*final_dir.glob("*.md"), *final_dir.glob("*.txt")])
    sources: list[dict[str, Any]] = []
    seen_chapters: set[int] = set()
    for path in paths:
        chapter_number = parse_chapter_number(path.stem)
        if chapter_number is None:
            raise ValueError(f"Cannot determine chapter number from final manuscript file: {path}")
        if chapter_number in seen_chapters:
            raise ValueError(f"Duplicate final manuscript files for chapter {chapter_number}.")
        seen_chapters.add(chapter_number)
        body = path.read_bytes()
        sources.append(
            {
                "chapter_number": chapter_number,
                "source_path": path.resolve().relative_to(root.resolve()).as_posix(),
                "sha256": sha256(body).hexdigest(),
            }
        )
    return sorted(sources, key=lambda item: item["chapter_number"])


def parse_chapter_number(value: str) -> int | None:
    match = re.search(r"(?:^|\D)(\d{1,6})(?:\D|$)", value)
    return int(match.group(1)) if match else None


def merkle_root(sources: list[dict[str, Any]]) -> str:
    digest = sha256()
    for item in sorted(sources, key=lambda value: value["source_path"]):
        digest.update(f"{item['source_path']}:{item['sha256']}\n".encode("utf-8"))
    return digest.hexdigest()


def configured_vector_store(config: ConfigDocument) -> dict[str, Any]:
    semantic = config.data.get("semantic")
    if not isinstance(semantic, dict):
        return {}
    vector_store = semantic.get("vector_store")
    return dict(vector_store) if isinstance(vector_store, dict) else {}


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000.0


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
