"""Pluggable vector store contracts for semantic RAG."""

from .pipeline import (
    SUPPORTED_BACKENDS,
    VectorHealth,
    VectorHit,
    VectorQuery,
    VectorRebuildResult,
    VectorRecord,
    VectorWriteResult,
    delete_by_filter,
    healthcheck,
    query,
    rebuild_from_files,
    upsert,
)

__all__ = [
    "SUPPORTED_BACKENDS",
    "VectorHealth",
    "VectorHit",
    "VectorQuery",
    "VectorRebuildResult",
    "VectorRecord",
    "VectorWriteResult",
    "delete_by_filter",
    "healthcheck",
    "query",
    "rebuild_from_files",
    "upsert",
]
