from __future__ import annotations

import copy
import json
from pathlib import Path

from longform_engine.config import ConfigDocument, load_project_config
from longform_engine.rag import run_rag_scale_benchmark
from longform_engine.storage import init_project
from longform_engine.vectorstore import (
    VectorQuery,
    VectorRecord,
    delete_by_filter,
    healthcheck,
    query,
    replace_records,
    sync_records,
)


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def with_backend(config: ConfigDocument, backend: str) -> ConfigDocument:
    data = copy.deepcopy(config.data)
    root = Path(config.data["project"]["root_dir"])
    vector_store = data["semantic"]["vector_store"]
    vector_store.update(
        {
            "backend": backend,
            "url": str(root / "70_runtime" / "db" / f"{backend}.sqlite"),
            "index_url": str(root / "70_runtime" / "db" / f"{backend}.hnsw"),
            "metric": "cosine",
            "dim": 3,
        }
    )
    return ConfigDocument(data=data, path=config.path, sources=config.sources)


def test_local_hnsw_incremental_upsert_stale_restore_and_health(tmp_path):
    config = with_backend(seed_project(tmp_path), "local_hnsw")
    initial = [
        vector_record("fact:a", (1.0, 0.0, 0.0), chapter=1),
        vector_record("fact:b", (0.0, 1.0, 0.0), chapter=2),
    ]
    replace_records(config, initial)

    first = query(config, VectorQuery(vector=(1.0, 0.0, 0.0), top_k=2))
    added = vector_record("fact:c", (0.0, 0.0, 1.0), chapter=3)
    synced = sync_records(config, [*initial, added])
    removed = delete_by_filter(config, from_chapter=3)
    stale = query(config, VectorQuery(vector=(0.0, 0.0, 1.0), top_k=3))
    restored = sync_records(config, [*initial, added])
    after = query(config, VectorQuery(vector=(0.0, 0.0, 1.0), top_k=3))
    health = healthcheck(config)

    assert first[0].id == "fact:a"
    assert synced.upserted == 1
    assert synced.unchanged == 2
    assert removed == 1
    assert all(hit.id != "fact:c" for hit in stale)
    assert restored.upserted == 1
    assert after[0].id == "fact:c"
    assert health.ok
    assert health.record_count == 3
    assert Path(health.index_path).is_file()


def test_local_sqlite_health_recommends_hnsw_at_configured_threshold(tmp_path):
    config = with_backend(seed_project(tmp_path), "local_sqlite")
    config.data["semantic"]["vector_store"]["hnsw_threshold"] = 2
    replace_records(
        config,
        [
            vector_record("fact:a", (1.0, 0.0, 0.0), chapter=1),
            vector_record("fact:b", (0.0, 1.0, 0.0), chapter=2),
        ],
    )

    health = healthcheck(config)

    assert health.ok
    assert health.record_count == 2
    assert "local_hnsw" in health.recommendation


def test_fixed_50_chapter_scale_runner_records_measured_nonclaim_evidence(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"

    result = run_rag_scale_benchmark(
        config,
        scale_chapters=50,
        backend="local_sqlite",
        query_count=12,
        top_k=5,
    )
    payload = json.loads((root / result.result_file).read_text(encoding="utf-8"))

    assert result.vector_count == 1000
    assert result.recall_at_k == 1.0
    assert result.fact_error_rate == 0.0
    assert result.stale_sync_ok
    assert result.rollback_restore_ok
    assert result.meets_thresholds
    assert result.evidence_grade == "synthetic_engineering"
    assert payload["measurement_source"] == "engine_runner"
    assert not list((root / "40_manuscript" / "final").glob("*.md"))
    assert not list((root / "60_rag" / "chunks").glob("*.json"))


def test_two_million_character_forecast_rag_scale_is_incremental_and_bounded(tmp_path):
    config = seed_project(tmp_path)

    result = run_rag_scale_benchmark(
        config,
        scale_chapters=667,
        backend="local_hnsw",
        query_count=12,
        top_k=5,
    )

    assert result.scale_chapters == 667
    assert result.vector_count == 667 * 20
    assert result.recall_at_k >= 0.85
    assert result.fact_error_rate <= 0.02
    assert result.p95_query_ms <= 1000
    assert result.stale_sync_ok
    assert result.rollback_restore_ok
    assert result.meets_thresholds


def vector_record(record_id: str, vector: tuple[float, ...], *, chapter: int) -> VectorRecord:
    return VectorRecord(
        id=record_id,
        owner_type="benchmark_fact",
        owner_id=record_id,
        vector=vector,
        source_path=f"benchmark://{record_id}",
        chapter_number=chapter,
        metadata={
            "content_hash": record_id,
            "model": "test-vector",
            "fact_key": record_id,
        },
    )
