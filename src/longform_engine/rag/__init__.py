"""Retrieval augmented generation package."""

from .pipeline import (
    EmbeddingBuildStats,
    RagBuildStats,
    RagContextResult,
    RagHit,
    RagQueryResult,
    apply_embedding_delta,
    build_chunks,
    build_context,
    query,
    query_cache_path,
    rebuild_embedding_index,
)
from .scale_benchmark import (
    RAG_SCALE_BENCHMARK_SCHEMA,
    RAG_SCALE_DATASET_ID,
    RagScaleBenchmarkResult,
    dataset_scenario,
    run_rag_scale_benchmark,
)
from .production_benchmark import (
    PRODUCTION_DATASET_SCHEMA,
    REQUIRED_QUERY_CATEGORIES,
    RagProductionBenchmarkResult,
    RagProductionTemplateResult,
    run_rag_production_benchmark,
    write_rag_production_template,
)

__all__ = [
    "EmbeddingBuildStats",
    "RagBuildStats",
    "RagContextResult",
    "RagHit",
    "RagQueryResult",
    "apply_embedding_delta",
    "build_chunks",
    "build_context",
    "query",
    "query_cache_path",
    "rebuild_embedding_index",
    "RAG_SCALE_BENCHMARK_SCHEMA",
    "RAG_SCALE_DATASET_ID",
    "RagScaleBenchmarkResult",
    "dataset_scenario",
    "run_rag_scale_benchmark",
    "PRODUCTION_DATASET_SCHEMA",
    "REQUIRED_QUERY_CATEGORIES",
    "RagProductionBenchmarkResult",
    "RagProductionTemplateResult",
    "run_rag_production_benchmark",
    "write_rag_production_template",
]
