"""Reproducible engineering benchmark for local vector-store scale behavior."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import copy
from hashlib import sha256
import json
import math
import platform
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.vectorstore import (
    VectorQuery,
    VectorRecord,
    delete_by_filter,
    healthcheck,
    query,
    replace_records,
    sync_records,
)


RAG_SCALE_BENCHMARK_SCHEMA = "rag_scale_engine_measurement_v2"
RAG_SCALE_DATASET_ID = "chinese-webnovel-rag-scale-v1"
SUPPORTED_SCALES = (50, 200, 500, 667)
SUPPORTED_LOCAL_BACKENDS = ("local_sqlite", "local_hnsw")
VECTOR_DIMENSION = 64
VECTORS_PER_CHAPTER = 20
DEFAULT_QUERY_COUNT = 60
DEFAULT_TOP_K = 10
RAG_500_THRESHOLDS = {
    "recall_at_k_min": 0.85,
    "fact_error_rate_max": 0.02,
    "p95_query_ms_max": 1000.0,
}


@dataclass(frozen=True)
class RagScaleBenchmarkResult:
    schema: str
    dataset_id: str
    dataset_hash: str
    evidence_grade: str
    measurement_source: str
    scale_chapters: int
    backend: str
    vector_count: int
    chunk_count: int
    query_count: int
    top_k: int
    recall_at_k: float
    fact_error_rate: float
    p95_query_ms: float
    mean_query_ms: float
    initial_index_ms: float
    incremental_index_ms: float
    stale_sync_ok: bool
    rollback_restore_ok: bool
    meets_thresholds: bool
    threshold_errors: tuple[str, ...]
    model: str
    python_version: str
    hardware: str
    backend_config_hash: str
    result_file: str


def run_rag_scale_benchmark(
    config: ConfigDocument,
    *,
    scale_chapters: int,
    backend: str | None = None,
    query_count: int = DEFAULT_QUERY_COUNT,
    top_k: int = DEFAULT_TOP_K,
) -> RagScaleBenchmarkResult:
    """Measure a deterministic isolated index without touching canonical project state."""

    if scale_chapters not in SUPPORTED_SCALES:
        raise ValueError(f"scale_chapters must be one of: {', '.join(str(item) for item in SUPPORTED_SCALES)}")
    selected_backend = backend or configured_backend(config)
    if selected_backend not in SUPPORTED_LOCAL_BACKENDS:
        raise ValueError("RAG scale benchmark supports local_sqlite or local_hnsw.")
    if query_count < 1 or query_count > 500:
        raise ValueError("query_count must be between 1 and 500.")
    if top_k < 1 or top_k > 100:
        raise ValueError("top_k must be between 1 and 100.")

    root = resolve_project_root(config)
    run_dir = (
        root
        / "70_runtime"
        / "benchmarks"
        / "rag-scale-v1"
        / selected_backend
        / f"ch{scale_chapters:03d}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    isolated = benchmark_config(config, run_dir=run_dir, backend=selected_backend)
    records = build_fixed_records(scale_chapters)
    initial_records = [
        record for record in records if (record.chapter_number or 0) < scale_chapters
    ]
    incremental_records = [
        record for record in records if (record.chapter_number or 0) == scale_chapters
    ]

    started = perf_counter()
    replace_records(isolated, initial_records)
    initial_index_ms = elapsed_ms(started)

    started = perf_counter()
    sync_result = sync_records(isolated, records)
    incremental_index_ms = elapsed_ms(started)
    if sync_result.upserted != len(incremental_records):
        raise RuntimeError(
            "Incremental vector sync changed an unexpected number of records: "
            f"expected {len(incremental_records)}, got {sync_result.upserted}."
        )

    targets = select_query_targets(records, query_count)
    latencies: list[float] = []
    recalled = 0
    fact_errors = 0
    for target in targets:
        started = perf_counter()
        hits = query(
            isolated,
            VectorQuery(
                vector=target.vector,
                top_k=top_k,
                owner_types=("benchmark_fact",),
                max_chapter=scale_chapters,
            ),
        )
        latencies.append(elapsed_ms(started))
        if any(hit.id == target.id for hit in hits):
            recalled += 1
        expected_fact = str((target.metadata or {}).get("fact_key") or "")
        actual_fact = str(hits[0].metadata.get("fact_key") or "") if hits else ""
        if actual_fact != expected_fact:
            fact_errors += 1

    rollback_target = incremental_records[0]
    stale_count = delete_by_filter(isolated, from_chapter=scale_chapters)
    stale_hits = query(
        isolated,
        VectorQuery(
            vector=rollback_target.vector,
            top_k=top_k,
            owner_types=("benchmark_fact",),
            max_chapter=scale_chapters,
        ),
    )
    stale_sync_ok = stale_count == len(incremental_records) and all(
        hit.id != rollback_target.id for hit in stale_hits
    )
    restored = sync_records(isolated, records)
    restored_hits = query(
        isolated,
        VectorQuery(
            vector=rollback_target.vector,
            top_k=top_k,
            owner_types=("benchmark_fact",),
            max_chapter=scale_chapters,
        ),
    )
    rollback_restore_ok = (
        restored.upserted == len(incremental_records)
        and any(hit.id == rollback_target.id for hit in restored_hits)
    )

    recall_at_k = recalled / len(targets)
    fact_error_rate = fact_errors / len(targets)
    p95_query_ms = percentile(latencies, 0.95)
    threshold_errors = scale_threshold_errors(
        scale_chapters=scale_chapters,
        recall_at_k=recall_at_k,
        fact_error_rate=fact_error_rate,
        p95_query_ms=p95_query_ms,
        stale_sync_ok=stale_sync_ok,
        rollback_restore_ok=rollback_restore_ok,
    )
    health = healthcheck(isolated)
    if not health.ok:
        threshold_errors.append(f"vector health failed: {health.message}")

    scenario = dataset_scenario()
    backend_config = isolated.data["semantic"]["vector_store"]
    result_path = run_dir / "result.json"
    result = RagScaleBenchmarkResult(
        schema=RAG_SCALE_BENCHMARK_SCHEMA,
        dataset_id=RAG_SCALE_DATASET_ID,
        dataset_hash=dataset_hash(scenario, records),
        evidence_grade="synthetic_engineering",
        measurement_source="engine_runner",
        scale_chapters=scale_chapters,
        backend=selected_backend,
        vector_count=len(records),
        chunk_count=len(records),
        query_count=len(targets),
        top_k=top_k,
        recall_at_k=round(recall_at_k, 6),
        fact_error_rate=round(fact_error_rate, 6),
        p95_query_ms=round(p95_query_ms, 3),
        mean_query_ms=round(mean(latencies), 3),
        initial_index_ms=round(initial_index_ms, 3),
        incremental_index_ms=round(incremental_index_ms, 3),
        stale_sync_ok=stale_sync_ok,
        rollback_restore_ok=rollback_restore_ok,
        meets_thresholds=not threshold_errors,
        threshold_errors=tuple(threshold_errors),
        model="fixed-hash-vector-v1",
        python_version=platform.python_version(),
        hardware=hardware_label(),
        backend_config_hash=sha256(canonical_json(backend_config).encode("utf-8")).hexdigest(),
        result_file=relative_path(root, result_path),
    )
    atomic_write_text(
        result_path,
        json.dumps(asdict(result), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return result


def build_fixed_records(scale_chapters: int) -> list[VectorRecord]:
    records: list[VectorRecord] = []
    for chapter in range(1, scale_chapters + 1):
        for slot in range(VECTORS_PER_CHAPTER):
            fact_key = f"ch{chapter:03d}:fact:{slot:02d}"
            vector = fixed_vector(f"{RAG_SCALE_DATASET_ID}:{fact_key}", VECTOR_DIMENSION)
            records.append(
                VectorRecord(
                    id=f"benchmark:{fact_key}",
                    owner_type="benchmark_fact",
                    owner_id=fact_key,
                    vector=vector,
                    source_path=f"benchmark://chapter/{chapter:03d}/fact/{slot:02d}",
                    chapter_number=chapter,
                    status="canonical",
                    metadata={
                        "dataset_id": RAG_SCALE_DATASET_ID,
                        "fact_key": fact_key,
                        "content_hash": sha256(fact_key.encode("utf-8")).hexdigest(),
                        "model": "fixed-hash-vector-v1",
                    },
                )
            )
    return records


def fixed_vector(key: str, dimension: int) -> tuple[float, ...]:
    values: list[float] = []
    counter = 0
    while len(values) < dimension:
        digest = sha256(f"{key}:{counter}".encode("utf-8")).digest()
        values.extend((byte / 127.5) - 1.0 for byte in digest)
        counter += 1
    values = values[:dimension]
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / norm for value in values)


def select_query_targets(records: list[VectorRecord], query_count: int) -> list[VectorRecord]:
    count = min(query_count, len(records))
    if count == len(records):
        return records
    indexes = {
        round(index * (len(records) - 1) / max(count - 1, 1))
        for index in range(count)
    }
    targets = [records[index] for index in sorted(indexes)]
    if len(targets) < count:
        seen = {record.id for record in targets}
        targets.extend(record for record in records if record.id not in seen and len(targets) < count)
    return targets


def benchmark_config(config: ConfigDocument, *, run_dir: Path, backend: str) -> ConfigDocument:
    data = copy.deepcopy(config.data)
    vector_store = data.setdefault("semantic", {}).setdefault("vector_store", {})
    vector_store.update(
        {
            "backend": backend,
            "url": str((run_dir / "vector_store.sqlite").resolve()),
            "index_url": str((run_dir / "vector_store.hnsw").resolve()),
            "metric": "cosine",
            "dim": VECTOR_DIMENSION,
        }
    )
    return ConfigDocument(data=data, path=config.path, sources=(*config.sources, "isolated RAG scale benchmark"))


def configured_backend(config: ConfigDocument) -> str:
    semantic = config.data.get("semantic") if isinstance(config.data.get("semantic"), dict) else {}
    vector_store = semantic.get("vector_store") if isinstance(semantic.get("vector_store"), dict) else {}
    return str(vector_store.get("backend") or "local_sqlite")


def scale_threshold_errors(
    *,
    scale_chapters: int,
    recall_at_k: float,
    fact_error_rate: float,
    p95_query_ms: float,
    stale_sync_ok: bool,
    rollback_restore_ok: bool,
) -> list[str]:
    errors: list[str] = []
    if not stale_sync_ok:
        errors.append("stale deletion did not remove the rolled-back chapter vectors.")
    if not rollback_restore_ok:
        errors.append("incremental sync did not restore the rolled-back chapter vectors.")
    if scale_chapters >= 500:
        if recall_at_k < RAG_500_THRESHOLDS["recall_at_k_min"]:
            errors.append("recall_at_k is below 0.85.")
        if fact_error_rate > RAG_500_THRESHOLDS["fact_error_rate_max"]:
            errors.append("fact_error_rate exceeds 0.02.")
        if p95_query_ms > RAG_500_THRESHOLDS["p95_query_ms_max"]:
            errors.append("P95 query latency exceeds 1000 ms.")
    return errors


def dataset_scenario() -> dict[str, Any]:
    return {
        "schema": "rag_scale_dataset_spec_v1",
        "dataset_id": RAG_SCALE_DATASET_ID,
        "scales": list(SUPPORTED_SCALES),
        "vector_dimension": VECTOR_DIMENSION,
        "vectors_per_chapter": VECTORS_PER_CHAPTER,
        "query_count": DEFAULT_QUERY_COUNT,
        "top_k": DEFAULT_TOP_K,
        "generator": "sha256-normalized-vector-v1",
        "evidence_grade": "synthetic_engineering",
    }


def dataset_hash(scenario: dict[str, Any], records: list[VectorRecord]) -> str:
    digest = sha256(canonical_json(scenario).encode("utf-8"))
    for record in records:
        digest.update(record.id.encode("utf-8"))
        digest.update(json.dumps(record.vector, separators=(",", ":")).encode("ascii"))
    return digest.hexdigest()


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


def elapsed_ms(started: float) -> float:
    return (perf_counter() - started) * 1000.0


def hardware_label() -> str:
    processor = platform.processor().strip() or "unknown-cpu"
    return f"{platform.system()} {platform.release()} {platform.machine()} {processor}".strip()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())
