# RAG Scale Phase 5 Engineering Record

## Scope

This record covers the deterministic `chinese-webnovel-rag-scale-phase5-v1`
fixture. It verifies vector index correctness, growth, incremental upsert,
stale deletion, and rollback restoration. It does not measure literary
quality, real BGE embeddings, or production Chinese fact-retrieval accuracy.

- Date: 2026-07-31
- Host: Windows 11 AMD64, Intel64 Family 6 Model 170 Stepping 4
- Python: 3.13.5
- Vector model: `fixed-hash-vector-v1`
- Query count: 60 per scale
- Top K: 10
- Evidence grade: `synthetic_engineering`
- Public superiority claim eligible: no

## Results

| Backend | Chapters | Vectors | Recall@10 | Fact error | P95 query | Initial index | Incremental chapter |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `local_sqlite` | 50 | 1,000 | 1.000 | 0.000 | 121.795 ms | 182.266 ms | 23.252 ms |
| `local_sqlite` | 200 | 4,000 | 1.000 | 0.000 | 414.695 ms | 570.907 ms | 75.794 ms |
| `local_sqlite` | 500 | 10,000 | 1.000 | 0.000 | 962.387 ms | 899.960 ms | 80.258 ms |
| `local_hnsw` | 50 | 1,000 | 1.000 | 0.000 | 16.454 ms | 241.235 ms | 69.555 ms |
| `local_hnsw` | 200 | 4,000 | 1.000 | 0.000 | 24.176 ms | 568.302 ms | 100.374 ms |
| `local_hnsw` | 500 | 10,000 | 1.000 | 0.000 | 105.004 ms | 993.549 ms | 186.163 ms |

All six runs passed stale deletion and rollback restoration. At 10,000
vectors, SQLite linear search remained just below the 1,000 ms engineering
limit but had little headroom. HNSW provided substantially lower query
latency while retaining the same fixture recall.

## Reproduction

```text
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 50 --backend local_sqlite
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 200 --backend local_sqlite
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 500 --backend local_sqlite
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 50 --backend local_hnsw
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 200 --backend local_hnsw
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 500 --backend local_hnsw
```

Machine-readable results are written below
`70_runtime/benchmarks/rag-scale-phase5-v1/` and remain untracked runtime
artifacts.

## Remaining Evidence

The next evidence grade must use finalized Chinese chapters, the configured
embedding/reranker models, adversarial conflicting facts, temporal filters,
entity aliases, and foreshadowing queries. Only that production-model run may
be considered for a public quality comparison.
