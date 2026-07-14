import copy
import json
from pathlib import Path

import pytest

from longform_engine.agent_tasks import load_manifest, status_summary, validate_manifest_strict
from longform_engine.config import ConfigDocument
from longform_engine.config import load_project_config
from longform_engine.db import query_table, rebuild_database
from longform_engine.gates import gate_check
from longform_engine.graph import retrieve_graph, semantic_graph_apply, semantic_graph_task, semantic_graph_validate
from longform_engine.memory import (
    apply_semantic_memory,
    build_style_memory,
    build_tcs,
    build_tcs_transition,
    character_apply,
    character_check,
    character_task,
    character_validate,
    compress_memory,
    semantic_task,
    semantic_validate,
    validate_tcs,
    validate_memory,
)
from longform_engine.models import ModelError, ModelVerifyResult, embed_text_with_provider, rerank_pair, verify_models
from longform_engine.models import pipeline as model_pipeline
from longform_engine.orchestration import continue_write
from longform_engine.planning import revise_outline
from longform_engine.rag import build_chunks, build_context, query
from longform_engine.storage import init_project
from longform_engine.vectorstore import (
    VectorQuery,
    VectorRecord,
    healthcheck as vector_healthcheck,
    query as vector_query,
    rebuild_from_files as vector_rebuild,
    upsert as vector_upsert,
)


def test_models_verify_reports_download_required_when_real_model_missing(tmp_path):
    config = seed_semantic_project(tmp_path, fallback=False, allow_network_download=False)

    result = verify_models(config)

    assert result.profile == "bge-m3"
    assert result.status == "download_required"
    assert result.download_required is True
    assert result.can_auto_download is False
    assert result.fallback_allowed is False
    assert result.fallback_active is False
    assert result.ok is False


def test_models_verify_can_auto_download_default_bge_profile(tmp_path):
    config = seed_semantic_project(tmp_path, fallback=False, allow_network_download=True)

    result = verify_models(config)

    assert result.status == "download_required"
    assert result.download_required is True
    assert result.can_auto_download is True
    assert result.embedding_model == "BAAI/bge-m3"
    assert result.reranker_model == "BAAI/bge-reranker-v2-m3"


def test_models_verify_allows_explicit_local_fallback(tmp_path):
    config = seed_semantic_project(tmp_path)

    result = verify_models(config)

    assert result.profile == "bge-m3"
    assert result.status == "fallback_only"
    assert result.fallback == "local-hash"
    assert result.fallback_allowed is True
    assert result.fallback_active is True
    assert result.ok is True


def test_models_ensure_auto_installs_when_download_allowed(tmp_path, monkeypatch):
    config = seed_semantic_project(tmp_path, fallback=False, allow_network_download=True)
    calls = []

    def fake_install(config_arg, *, profile="bge-m3", download=False):
        calls.append((profile, download))
        root = model_pipeline.models_dir(config_arg) / profile
        embedding = root / "embedding"
        reranker = root / "reranker"
        embedding.mkdir(parents=True, exist_ok=True)
        reranker.mkdir(parents=True, exist_ok=True)
        (embedding / "model.txt").write_text("embedding", encoding="utf-8")
        (reranker / "model.txt").write_text("reranker", encoding="utf-8")
        return model_pipeline.ModelInstallResult(
            profile=profile,
            models_dir=str(model_pipeline.models_dir(config_arg)),
            manifest_file=str(model_pipeline.manifest_path(config_arg)),
            downloaded=True,
            embedding_path=str(embedding),
            reranker_path=str(reranker),
            warnings=(),
        )

    monkeypatch.setattr(model_pipeline, "install_model_profile", fake_install)
    monkeypatch.setattr(model_pipeline, "can_load_sentence_transformer", lambda path: model_pipeline.directory_has_files(path))

    result = model_pipeline.ensure_models_ready(config, allow_download=True, require_reranker=True)

    assert calls == [("bge-m3", True)]
    assert result.status == "ready"
    assert result.provider_ready is True


def test_models_provider_interface_can_be_mocked(tmp_path, monkeypatch):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"

    monkeypatch.setattr(
        model_pipeline,
        "verify_models",
        lambda _config: ModelVerifyResult(
            ok=True,
            status="ready",
            profile="bge-m3",
            models_dir=str(root / "70_runtime" / "models"),
            embedding_model="BAAI/bge-m3",
            reranker_model="BAAI/bge-reranker-v2-m3",
            embedding_cached=True,
            reranker_cached=True,
            embedding_loadable=True,
            reranker_loadable=True,
            provider_ready=True,
            download_required=False,
            can_auto_download=False,
            fallback_allowed=False,
            fallback_active=False,
            fallback="",
            warnings=(),
        ),
    )
    monkeypatch.setattr(model_pipeline, "sentence_transformer_embed", lambda _path, _text: [0.2, 0.8])
    monkeypatch.setattr(model_pipeline, "sentence_transformer_rerank", lambda _path, _query, _candidate: 0.91)

    assert embed_text_with_provider(config, "semantic provider branch") == [0.2, 0.8]
    assert rerank_pair(config, "query", "candidate") == 0.91


def test_semantic_query_requires_real_embedding_without_fallback(tmp_path):
    config = seed_semantic_project(tmp_path, fallback=False, allow_network_download=False)
    build_chunks(config, max_chars=220)

    with pytest.raises(ModelError):
        query(config, "why did the character forgive the other", top_k=5, semantic=True)


def test_remote_vector_store_contract_does_not_query_or_persist_facts(tmp_path, monkeypatch):
    config = seed_semantic_project(tmp_path)
    remote = config_with_vector_backend(config, backend="milvus", url="http://milvus.local:19530")
    monkeypatch.setenv("LONGFORM_VECTOR_API_KEY", "test-token")

    health = vector_healthcheck(remote)
    written = vector_upsert(
        remote,
        [
            VectorRecord(
                id="memory:scene:ch001:001",
                owner_type="scene_memory",
                owner_id="ch001_scene001",
                vector=(0.1, 0.9),
                source_path="60_rag/memory/scenes/ch001_scene001.json",
                chapter_number=1,
            )
        ],
    )
    hits = vector_query(remote, VectorQuery(vector=(0.1, 0.9), top_k=3))

    assert health.ok is True
    assert health.backend == "milvus"
    assert written.records == 1
    assert written.store_path == "http://milvus.local:19530"
    assert hits == []


def test_semantic_memory_task_validate_apply_rag_and_db_rebuild(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"

    task = semantic_task(config, chapter_number=1)
    payload_file = root / "50_workbench" / "memory_tasks" / "ch001.semantic.codex.json"
    payload_file.write_text(json.dumps(semantic_payload(), ensure_ascii=False), encoding="utf-8")

    manifest = load_manifest(root, task.manifest_file)
    strict = validate_manifest_strict(root, manifest)
    validation = semantic_validate(config, chapter_number=1, file_path=payload_file)
    status_after_validate = status_summary(root, chapter_number=1)
    applied = apply_semantic_memory(config, chapter_number=1, file_path=payload_file)
    status_after_apply = status_summary(root, chapter_number=1)
    memory_status = validate_memory(config)
    stats = build_chunks(config, max_chars=220, with_embeddings=True)
    hits = query(config, "角色为什么原谅对方", top_k=5, semantic=True)
    context = build_context(config, chapter_number=2, query_text="角色为什么原谅对方", top_k=5, semantic=True)
    embeddings = query_table(config, "embeddings", limit=50)
    rebuild = rebuild_database(config)

    assert task.output_file.endswith("ch001.semantic.codex.json")
    assert task.manifest_file.endswith("ch001.semantic_memory.agent_task.json")
    assert manifest["task_type"] == "memory_extract"
    assert strict.ok, strict.errors
    assert validation.ok is True
    assert status_after_validate["by_status"]["validated"] >= 1
    assert len(applied.scene_files) == 1
    assert status_after_apply["by_status"]["applied"] >= 1
    assert load_manifest(root, task.manifest_file)["status"] == "applied"
    assert memory_status.ok is True
    assert stats.embeddings >= 2
    assert embeddings
    assert rebuild.embeddings == len(embeddings)
    assert any(hit.id.startswith("memory:scene") for hit in hits.hits)
    assert any("救援" in hit.text or "让步" in hit.text for hit in hits.hits)
    assert context.hit_count >= 1
    memory_reports = transaction_payloads(root, "memory semantic-apply")
    assert memory_reports
    assert "60_rag/memory/chapters/ch001.json" in memory_reports[-1]["touched_paths"]
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")
    assert "Temporal Context State" in context_text
    assert "Semantic score" in context_text


def test_semantic_memory_rejects_noncanonical_source(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    bad = semantic_payload()
    bad["source_path"] = "50_workbench/agent_drafts/ch001.codex.md"
    payload_file = root / "50_workbench" / "memory_tasks" / "bad.json"
    payload_file.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")

    validation = semantic_validate(config, chapter_number=1, file_path=payload_file)

    assert validation.ok is False
    assert any("source_path" in error for error in validation.errors)


def test_continue_write_includes_tcs_and_semantic_gate_writes_report(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    tcs = build_tcs(config, chapter_number=2)
    cont = continue_write(config, chapter_number=2)

    draft = root / "40_manuscript" / "draft" / "ch002.md"
    draft.write_text("# Chapter 2\n\n" + ("她突然原谅了他，却没有任何救援、让步或道歉作为证据。" * 120), encoding="utf-8")
    gate = gate_check(config, chapter_number=2, source="draft", semantic=True)

    task_payload = json.loads((root / "50_workbench" / "writing_tasks" / "ch002.json").read_text(encoding="utf-8"))
    assert tcs.tcs_file.endswith("ch002.json")
    assert cont.status == "task_ready"
    assert "temporal_context_state" in task_payload
    assert gate.passed is False
    assert any(failure["code"] == "semantic_motivation_break" for failure in gate.failures)
    assert (root / "50_workbench" / "gate_artifacts" / "ch002" / "semantic_report.md").exists()


def test_revise_outline_marks_memory_stale_and_semantic_query_skips_memory(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    payload_file = root / "50_workbench" / "memory_tasks" / "ch001.semantic.codex.json"
    payload_file.write_text(json.dumps(semantic_payload(), ensure_ascii=False), encoding="utf-8")
    apply_semantic_memory(config, chapter_number=1, file_path=payload_file)
    before = query(config, "角色为什么原谅对方", top_k=5, semantic=True)

    result = revise_outline(config, from_chapter=1, change_description="move forgiveness arc later")
    after = query(config, "角色为什么原谅对方", top_k=5, semantic=True)

    stale = json.loads((root / "60_rag" / "memory" / "stale.json").read_text(encoding="utf-8"))
    assert any(hit.id.startswith("memory:") for hit in before.hits)
    assert not any(hit.id.startswith("memory:") for hit in after.hits)
    assert stale["stale"] is True
    assert result.report_file.endswith(".md")


def test_style_memory_compression_and_sqlite_mirrors(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    payload_file = root / "50_workbench" / "memory_tasks" / "ch001.semantic.codex.json"
    payload_file.write_text(json.dumps(semantic_payload(), ensure_ascii=False), encoding="utf-8")
    apply_semantic_memory(config, chapter_number=1, file_path=payload_file)
    style = build_style_memory(config)
    chapter = compress_memory(config, scope="chapter", from_chapter=1, to_chapter=1)
    arc = compress_memory(config, scope="arc", from_chapter=1, to_chapter=1)
    tcs = build_tcs(config, chapter_number=2)
    rebuild = rebuild_database(config)

    assert style.source_chapters == 1
    assert chapter.output_file.endswith("ch001.json")
    assert arc.output_file.endswith("arc_ch001_to_ch001.json")
    assert tcs.tcs_file.endswith("ch002.json")
    assert rebuild.memory_units >= 3
    assert query_table(config, "scene_memories", limit=10)
    assert query_table(config, "chapter_memories", limit=10)
    assert query_table(config, "arc_memories", limit=10)
    assert query_table(config, "style_memories", limit=10)
    assert query_table(config, "tcs_snapshots", limit=10)


def test_vector_store_local_rebuild_and_no_pollution(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    payload_file = root / "50_workbench" / "memory_tasks" / "ch001.semantic.codex.json"
    payload = semantic_payload()
    payload["scenes"][0]["summary"] = "Prior rescue and concession explain later forgiveness."
    payload["scenes"][0]["events"] = ["rescue", "concession"]
    payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    apply_semantic_memory(config, chapter_number=1, file_path=payload_file)

    stats = build_chunks(config, max_chars=220, with_embeddings=True)
    health = vector_healthcheck(config)
    rebuilt = vector_rebuild(config)
    hits = vector_query(config, VectorQuery(vector=tuple(embed_text_with_provider(config, "why forgive after rescue")), top_k=5))

    assert health.ok is True
    assert stats.embeddings >= 2
    assert rebuilt.records == stats.embeddings
    assert hits
    assert not any("agent_drafts" in hit.source_path or "research_inbox" in hit.source_path for hit in hits)


def test_consistency_rerank_prefers_causal_scene_memory(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    payload_file = root / "50_workbench" / "memory_tasks" / "ch001.semantic.codex.json"
    payload = semantic_payload()
    payload["scenes"][0]["summary"] = "A rescue and concession created the causal basis for later forgiveness."
    payload["scenes"][0]["events"] = ["rescue", "concession"]
    payload["chapter_memory"]["summary"] = "The rescue and concession explain forgiveness."
    payload_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    apply_semantic_memory(config, chapter_number=1, file_path=payload_file)

    hits = query(config, "why did the character forgive the other", top_k=5, semantic=True)

    assert hits.hits
    assert hits.hits[0].id.startswith("memory:")
    assert "causal support" in hits.hits[0].consistency_reason


def test_semantic_gate_uses_style_memory(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    build_style_memory(config)
    draft = root / "40_manuscript" / "draft" / "ch002.md"
    draft.write_text("# Chapter 2\n\n" + ("word " * 1200) + "\n", encoding="utf-8")

    gate = gate_check(config, chapter_number=2, source="draft", semantic=True)

    assert any(item["code"] == "semantic_style_voice_drift" for item in gate.failures) or any("semantic_style_voice_drift" in item for item in gate.warnings)


def test_graph_semantic_task_validate_apply_temporal_relationship(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    source_text = (root / "40_manuscript" / "final" / "ch001.md").read_text(encoding="utf-8")
    evidence = source_text.splitlines()[-1][:30]
    task = semantic_graph_task(config, chapter_number=1)
    payload_file = root / "50_workbench" / "graph_updates" / "ch001.semantic.json"
    payload_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "source": "final",
                "source_path": "40_manuscript/final/ch001.md",
                "updates": [
                    {
                        "type": "relationship_change",
                        "source": "character:shen_lan",
                        "target": "character:lu_heng",
                        "relation": "alliance",
                        "from_chapter": 1,
                        "confidence": 0.9,
                        "evidence_span": evidence,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(root, task.manifest_file)
    strict = validate_manifest_strict(root, manifest)
    validation = semantic_graph_validate(config, chapter_number=1, file_path=payload_file)
    status_after_validate = status_summary(root, chapter_number=1)
    applied = semantic_graph_apply(config, chapter_number=1, file_path=payload_file)
    status_after_apply = status_summary(root, chapter_number=1)
    graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))

    assert task.output_file.endswith("ch001.semantic.json")
    assert task.manifest_file.endswith("ch001.semantic_graph.agent_task.json")
    assert manifest["task_type"] == "graph_extract"
    assert strict.ok, strict.errors
    assert validation.ok is True
    assert status_after_validate["by_status"]["validated"] >= 1
    assert applied.applied == 1
    assert status_after_apply["by_status"]["applied"] >= 1
    assert load_manifest(root, task.manifest_file)["status"] == "applied"
    assert any(item.get("type") == "alliance" for item in graph["relationships"])
    assert any(item.get("type") == "conflicts_with" and item.get("to_chapter") == 1 for item in graph["relationships"])
    graph_reports = transaction_payloads(root, "graph semantic-apply")
    assert graph_reports
    assert "30_state/story_graph.json" in graph_reports[-1]["touched_paths"]


def test_graph_semantic_validate_requires_evidence(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    payload_file = root / "50_workbench" / "graph_updates" / "bad.semantic.json"
    payload_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "source": "final",
                "source_path": "40_manuscript/final/ch001.md",
                "updates": [{"type": "event", "title": "bad", "from_chapter": 1, "confidence": 0.9}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    validation = semantic_graph_validate(config, chapter_number=1, file_path=payload_file)

    assert validation.ok is False
    assert any("evidence_span" in error for error in validation.errors)


def test_character_memory_task_validate_apply_check_and_db_rebuild(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    task = character_task(config, chapter_number=1)

    bad_file = root / "50_workbench" / "memory_tasks" / "bad.character.json"
    bad_payload = character_payload()
    bad_payload["characters"][0]["evidence"] = []
    bad_file.write_text(json.dumps(bad_payload, ensure_ascii=False), encoding="utf-8")

    payload_file = root / "50_workbench" / "memory_tasks" / "ch001.character.codex.json"
    payload_file.write_text(json.dumps(character_payload(), ensure_ascii=False), encoding="utf-8")
    invalid = character_validate(config, chapter_number=1, file_path=bad_file)
    manifest = load_manifest(root, task.manifest_file)
    strict = validate_manifest_strict(root, manifest)
    validation = character_validate(config, chapter_number=1, file_path=payload_file)
    status_after_validate = status_summary(root, chapter_number=1)
    applied = character_apply(config, chapter_number=1, file_path=payload_file)
    status_after_apply = status_summary(root, chapter_number=1)

    draft = root / "40_manuscript" / "draft" / "ch002.md"
    draft.write_text(
        "# Chapter 2\n\nShen Lan fully trusts Lu Heng before evidence and calls him dearest.\n",
        encoding="utf-8",
    )
    check = character_check(config, chapter_number=2, file_path=draft)
    rebuild = rebuild_database(config)

    assert task.output_file.endswith("ch001.character.codex.json")
    assert task.manifest_file.endswith("ch001.character_memory.agent_task.json")
    assert manifest["task_type"] == "character_memory"
    assert strict.ok, strict.errors
    assert invalid.ok is False
    assert validation.ok is True
    assert status_after_validate["by_status"]["validated"] >= 1
    assert applied.character_files
    assert status_after_apply["by_status"]["applied"] >= 1
    assert load_manifest(root, task.manifest_file)["status"] == "applied"
    assert (root / "60_rag" / "memory" / "characters" / "character_shen_lan.json").exists()
    assert check.passed is False
    assert any(item["code"] == "character_forbidden_action" for item in check.findings)
    assert rebuild.character_memories == 1
    assert query_table(config, "character_memories", limit=10)
    character_reports = transaction_payloads(root, "memory character-apply")
    assert character_reports
    assert "60_rag/memory/characters/character_shen_lan.json" in character_reports[-1]["touched_paths"]


def test_graph_traversal_and_rag_fusion_return_temporal_graph_facts(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    graph_path = root / "30_state" / "story_graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["entities"].extend(
        [
            {
                "id": "foreshadowing:old_letter",
                "name": "old letter clue",
                "type": "foreshadowing",
                "status": "planted",
                "from_chapter": 1,
                "evidence_span": "old letter was hidden",
                "source_path": "40_manuscript/final/ch001.md",
            },
            {
                "id": "ability:future_blade",
                "name": "future blade",
                "type": "ability",
                "from_chapter": 5,
                "cost": "future-only cost",
            },
        ]
    )
    graph["relationships"].append(
        {
            "id": "rel:shen_lan:lu_heng:trust_seed",
            "source": "character:shen_lan",
            "target": "character:lu_heng",
            "type": "trust_seed",
            "from_chapter": 1,
            "status": "active",
            "confidence": 0.91,
            "evidence_span": "rescue and concession",
            "source_path": "40_manuscript/final/ch001.md",
        }
    )
    graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")

    trust = retrieve_graph(config, query_text="why does Shen trust Lu Heng after rescue", chapter_number=2)
    clue = retrieve_graph(config, query_text="when was the foreshadow clue planted", chapter_number=2)
    ability = retrieve_graph(config, query_text="why cannot he use shield ability cost cooldown", chapter_number=2)
    rag_hits = query(config, "why trust after rescue", top_k=5, semantic=True, chapter_number=2)

    assert any(hit.kind == "relationship" and "trust_seed" in hit.label for hit in trust.hits)
    assert any(hit.kind == "event" for hit in trust.hits)
    assert any(hit.kind == "foreshadowing" and hit.id == "foreshadowing:old_letter" for hit in clue.hits)
    assert any(hit.kind == "ability" and hit.id == "ability:shield" for hit in ability.hits)
    assert not any(hit.id == "ability:future_blade" for hit in ability.hits)
    assert any(hit.id.startswith("graph:") and hit.graph_score > 0 for hit in rag_hits.hits)


def test_tcs_transition_current_validate_and_future_leak_detection(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"

    transition = build_tcs_transition(config, chapter_number=1)
    valid = validate_tcs(config, chapter_number=2)
    ch002 = root / "30_state" / "tcs" / "ch002.json"
    payload = json.loads(ch002.read_text(encoding="utf-8"))
    payload.setdefault("known_facts", []).append({"chapter": 9, "fact": "future reveal"})
    ch002.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    invalid = validate_tcs(config, chapter_number=2)

    assert transition.next_chapter == 2
    assert (root / "30_state" / "tcs" / "current.json").exists()
    assert (root / "30_state" / "tcs" / "transitions" / "ch001.json").exists()
    assert valid.ok is True
    assert invalid.ok is False
    assert any("future chapter 9" in error for error in invalid.errors)
    assert query_table(config, "tcs_transitions", limit=10)


def test_semantic_gate_blocks_character_consistency_violations(tmp_path):
    config = seed_semantic_project(tmp_path)
    root = tmp_path / "novel"
    payload_file = root / "50_workbench" / "memory_tasks" / "ch001.character.codex.json"
    payload_file.write_text(json.dumps(character_payload(), ensure_ascii=False), encoding="utf-8")
    character_apply(config, chapter_number=1, file_path=payload_file)
    draft = root / "40_manuscript" / "draft" / "ch002.md"
    draft.write_text(
        "# Chapter 2\n\n"
        "Shen Lan reveals secret pact knowledge before evidence. "
        "She fully trusts Lu Heng before evidence and says dearest.\n",
        encoding="utf-8",
    )

    gate = gate_check(config, chapter_number=2, source="draft", semantic=True)

    codes = {item["code"] for item in gate.failures}
    assert gate.passed is False
    assert "character_forbidden_action" in codes
    assert "character_knowledge_leak" in codes
    assert "character_forbidden_action" in Path(gate.repair_plan).read_text(encoding="utf-8")


def seed_semantic_project(tmp_path, *, fallback=True, allow_network_download=False):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root
    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# Chapter 1\n\n"
        "雨夜里，沈岚把受伤的陆衡从追兵中救下。她没有说原谅，只是退让一步，"
        "把旧日误会暂时压下。陆衡付出代价保护她，两人的关系第一次出现松动。\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "summaries" / "ch001.md").write_text(
        "沈岚在雨夜救援陆衡，陆衡让步并付出代价，两人的敌意开始松动。\n",
        encoding="utf-8",
    )
    (root / "10_bible" / "characters.json").write_text(
        json.dumps(
            [
                {"id": "character:shen_lan", "name": "沈岚", "type": "character"},
                {"id": "character:lu_heng", "name": "陆衡", "type": "character"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "30_state" / "story_graph.json").write_text(
        json.dumps(
            {
                "entities": [
                    {"id": "character:shen_lan", "name": "沈岚", "type": "character", "mentions": [{"chapter_number": 1}]},
                    {"id": "character:lu_heng", "name": "陆衡", "type": "character", "mentions": [{"chapter_number": 1}]},
                    {"id": "ability:shield", "name": "护身印", "type": "ability", "cost": "burns stamina", "cooldown": "one scene"},
                ],
                "relationships": [
                    {
                        "id": "rel:shen_lan:lu_heng:softening",
                        "source": "character:shen_lan",
                        "target": "character:lu_heng",
                        "type": "conflicts_with",
                        "from_chapter": 1,
                        "status": "active",
                        "confidence": 0.8,
                        "evidence_span": "敌意开始松动",
                    }
                ],
                "events": [
                    {
                        "id": "event:ch001:rescue",
                        "chapter_number": 1,
                        "title": "雨夜救援",
                        "participants": ["character:shen_lan", "character:lu_heng"],
                        "source_path": "40_manuscript/final/ch001.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    state["last_finalized_chapter"] = 1
    state["current_chapter"] = 1
    state["status"] = "chapter_finalized"
    (root / "30_state" / "novel_state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    overrides = semantic_fallback_overrides() if fallback else semantic_strict_overrides(allow_network_download=allow_network_download)
    return load_project_config(project.project_config, cli_overrides=overrides)


def semantic_fallback_overrides():
    return {
        "semantic": {
            "enabled": True,
            "profile": "bge-m3",
            "allow_network_download": False,
            "require_real_model": False,
            "allow_fallback": True,
        },
        "rag": {
            "embedding": {"enabled": False, "profile": "bge-m3"},
            "reranker": {"enabled": False, "profile": "bge-m3"},
        },
    }


def semantic_strict_overrides(*, allow_network_download=False):
    return {
        "semantic": {
            "enabled": True,
            "profile": "bge-m3",
            "allow_network_download": allow_network_download,
            "require_real_model": True,
            "allow_fallback": False,
        },
        "rag": {
            "embedding": {"enabled": True, "profile": "bge-m3", "model": "BAAI/bge-m3"},
            "reranker": {"enabled": True, "profile": "bge-m3", "model": "BAAI/bge-reranker-v2-m3"},
        },
    }


def config_with_vector_backend(config, *, backend, url):
    data = copy.deepcopy(config.data)
    data.setdefault("semantic", {})["vector_store"] = {
        "backend": backend,
        "url": url,
        "collection": "longform_vectors",
        "api_key_env": "LONGFORM_VECTOR_API_KEY",
        "metric": "cosine",
        "dim": 1024,
    }
    return ConfigDocument(data=data, path=config.path, sources=config.sources)


def semantic_payload():
    return {
        "schema_version": 1,
        "chapter_number": 1,
        "source_path": "40_manuscript/final/ch001.md",
        "scenes": [
            {
                "chapter": 1,
                "scene": 1,
                "summary": "沈岚在雨夜救援陆衡，陆衡让步并付出代价，原谅的前置因果开始成立。",
                "characters": ["沈岚", "陆衡"],
                "location": "雨夜巷口",
                "events": ["雨夜救援", "关系让步"],
                "emotion_state": "softening",
                "conflict_state": "softening",
                "evidence": ["把受伤的陆衡从追兵中救下", "退让一步"],
            }
        ],
        "chapter_memory": {
            "summary": "救援和让步成为后续原谅的因果基础。",
            "characters": ["沈岚", "陆衡"],
            "locations": ["雨夜巷口"],
            "events": ["雨夜救援", "关系让步"],
            "emotion_state": "softening",
            "conflict_state": "softening",
            "evidence": ["关系第一次出现松动"],
        },
        "graph_updates": {
            "character_status_changes": [],
            "events": [],
            "relationship_changes": [],
            "foreshadow_planted": [],
            "foreshadow_paid_off": [],
            "conflict_escalation": [],
            "location_transitions": [],
            "ability_boundary_changes": [],
        },
    }


def character_payload():
    return {
        "schema_version": 1,
        "chapter_number": 1,
        "source_path": "40_manuscript/final/ch001.md",
        "characters": [
            {
                "character_id": "character:shen_lan",
                "name": "Shen Lan",
                "aliases": ["Shen Lan", "Shen"],
                "personality_baseline": ["cautious", "does not trust without evidence"],
                "current_beliefs": ["Lu Heng paid a cost to protect her"],
                "knowledge_scope": ["rescue in chapter 1"],
                "relationship_map": [
                    {
                        "target": "character:lu_heng",
                        "state": "softening distrust",
                        "from_chapter": 1,
                        "evidence": "rescue and concession",
                    }
                ],
                "speech_style": {"forbidden_address": ["dearest"]},
                "forbidden_actions": ["fully trusts Lu Heng before evidence"],
                "state_history": [
                    {
                        "chapter": 1,
                        "known_facts": ["rescue in chapter 1"],
                        "belief": "Lu Heng paid a cost to protect her",
                    }
                ],
                "evidence": ["rescue and concession"],
                "source_chapters": [1],
                "status": "canonical",
            }
        ],
    }


def transaction_payloads(root: Path, command: str) -> list[dict]:
    payloads = []
    for path in sorted((root / "70_runtime" / "transactions").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("command") == command:
            payloads.append(payload)
    return payloads
