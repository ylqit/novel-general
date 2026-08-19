import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from longform_engine.benchmark import RAG_REQUIRED_CATEGORIES, init_benchmark, rag_threshold_errors
from longform_engine.config import load_project_config
from longform_engine.rag.production_benchmark import (
    run_rag_production_benchmark,
    write_rag_production_template,
)
from longform_engine.storage import init_project


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def test_production_rag_runner_writes_claim_grade_engine_evidence(tmp_path, monkeypatch):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    final_dir = root / "40_manuscript" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    source_hashes = {}
    for chapter in range(1, 501):
        path = final_dir / f"ch{chapter:03d}.md"
        path.write_text(f"# 第{chapter}章\n\n关键证据编号 {chapter} 保持有效。", encoding="utf-8")
        source_hashes[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()

    init_benchmark(
        config,
        run_id="production-rag-run",
        host_product="codex",
        chapters=10,
    )
    queries = []
    for index in range(50):
        chapter = index + 1
        category = RAG_REQUIRED_CATEGORIES[index % len(RAG_REQUIRED_CATEGORIES)]
        source_path = f"40_manuscript/final/ch{chapter:03d}.md"
        queries.append(
            {
                "query_id": f"q{index + 1:03d}",
                "category": category,
                "query": f"query-{chapter}",
                "chapter_number": min(chapter + 1, 501),
                "expected": [
                    {
                        "source_path": source_path,
                        "source_sha256": source_hashes[source_path],
                        "evidence_span": f"关键证据编号 {chapter}",
                    }
                ],
                "forbidden": [],
            }
        )
    dataset = tmp_path / "production-dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "schema": "rag_production_dataset_v1",
                "dataset_id": "production-fixture-v1",
                "queries": queries,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "longform_engine.rag.production_benchmark.verify_models",
        lambda _config: SimpleNamespace(
            profile="bge-m3",
            embedding_model="BAAI/bge-m3",
            reranker_model="BAAI/bge-reranker-v2-m3",
            embedding_loadable=True,
            reranker_loadable=True,
            provider_ready=True,
            fallback_active=False,
        ),
    )
    monkeypatch.setattr(
        "longform_engine.rag.production_benchmark.healthcheck",
        lambda _config: SimpleNamespace(ok=True, record_count=500, message="ready"),
    )
    monkeypatch.setattr(
        "longform_engine.rag.production_benchmark.build_chunks",
        lambda _config, **kwargs: (
            SimpleNamespace(chapters=499, chunks=499, embeddings=499)
            if kwargs.get("with_embeddings")
            else SimpleNamespace(chapters=1, chunks=1, embeddings=0)
        ),
    )
    monkeypatch.setattr(
        "longform_engine.rag.production_benchmark.sync_semantic_delta",
        lambda _config, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "longform_engine.rag.production_benchmark.apply_embedding_delta",
        lambda _config, **_kwargs: SimpleNamespace(generated=1),
    )

    def fake_query(_config, query_text, **_kwargs):
        chapter = int(query_text.split("-")[-1])
        return SimpleNamespace(
            hits=(
                SimpleNamespace(
                    source_path=f"40_manuscript/final/ch{chapter:03d}.md",
                    text=f"关键证据编号 {chapter} 保持有效。",
                ),
            )
        )

    monkeypatch.setattr("longform_engine.rag.production_benchmark.query", fake_query)

    result = run_rag_production_benchmark(
        config,
        run_id="production-rag-run",
        dataset_file=dataset,
    )
    evidence = json.loads((root / result.evidence_file).read_text(encoding="utf-8"))

    assert result.meets_thresholds
    assert result.scale_chapters == 500
    assert result.query_count == 50
    assert result.recall_at_k == 1.0
    assert evidence["measurement_source"] == "engine_runner"
    assert evidence["evidence_grade"] == "production_model"
    assert evidence["fallback_active"] is False
    assert evidence["incremental_index_mode"] == "isolated_real_next_finalized_chapter"
    assert evidence["initial_indexed_chapters"] == 499
    assert evidence["incremental_source"]["chapter_number"] == 500
    assert not rag_threshold_errors(evidence)


def test_production_rag_preflight_rejects_missing_models_and_scale_without_evidence(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    init_benchmark(
        config,
        run_id="blocked-production-rag",
        host_product="codex",
        chapters=10,
    )
    dataset = tmp_path / "unused.json"
    dataset.write_text('{"schema":"rag_production_dataset_v1","queries":[]}', encoding="utf-8")

    with pytest.raises(ValueError, match="preflight failed"):
        run_rag_production_benchmark(
            config,
            run_id="blocked-production-rag",
            dataset_file=dataset,
        )

    assert not (
        root
        / "70_runtime"
        / "benchmarks"
        / "blocked-production-rag"
        / "rag_scale_evidence.json"
    ).exists()


def test_production_rag_template_is_prose_free_and_lists_required_categories(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"

    result = write_rag_production_template(config)
    payload = json.loads((root / result.template_file).read_text(encoding="utf-8"))

    assert payload["schema"] == "rag_production_dataset_v1"
    assert payload["queries"] == []
    assert payload["required_categories"] == list(RAG_REQUIRED_CATEGORIES)
    assert payload["minimum_query_count"] == 50
