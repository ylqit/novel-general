from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import CANONICAL_DELTA_SCHEMA
from longform_engine.agent_tasks import load_manifest
from longform_engine.config import load_project_config
from longform_engine.graph import validate_graph
from longform_engine.memory import build_tcs, validate_tcs
from longform_engine.rag import query
from longform_engine.semantic import chapter_close, semantic_apply, semantic_rebuild, semantic_task, semantic_validate
from longform_engine.storage import init_project


def test_unified_semantic_bundle_materializes_evidence_bound_views(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    config.data["semantic"]["profile"] = "local-hash"
    config.data["semantic"]["allow_fallback"] = True
    config.data["semantic"]["vector_store"]["backend"] = "local_sqlite"
    root = project.root
    write_json(
        root / "10_bible" / "characters.json",
        [
            {"id": "char_shen", "name": "沈阙"},
            {"id": "char_he", "name": "何简"},
            {"id": "char_future", "name": "尚未登场者"},
        ],
    )
    write_json(
        root / "10_bible" / "relationships.json",
        [
            {
                "id": "rel_shen_he_initial",
                "source_id": "char_shen",
                "target_id": "char_he",
                "type": "investigation_partner",
                "stage": "试探",
            },
            {
                "id": "rel_shen_future_planned",
                "source_id": "char_shen",
                "target_id": "char_future",
                "type": "future_rival",
                "stage": "尚未相识",
            },
        ],
    )
    write_json(
        root / "20_outline" / "foreshadowing_ledger.json",
        [
            {
                "id": "thread_old_badge",
                "name": "旧木牌来历",
                "plant_chapter": 1,
                "payoff_window": [2, 3],
            }
        ],
    )
    final = root / "40_manuscript" / "final" / "ch001.md"
    text = "# 第一章\n\n沈阙把旧木牌交给何简，何简点头。\n"
    final.write_text(text, encoding="utf-8")
    start = text.index("沈阙")
    end = text.index("。", start) + 1
    evidence = {"start": start, "end": end, "excerpt": text[start:end]}

    task = semantic_task(config, chapter_number=1)
    manifest = load_manifest(root, task.manifest_file)
    inputs = [item["path"] for item in manifest["io"]["inputs"]]
    assert manifest["task_type"] == "chapter_semantic"
    assert len(inputs) == 3
    assert manifest["policy"]["context"]["budget_profile"] == "standard"
    assert manifest["policy"]["context"]["capacity_units"] == 48_000
    assert inputs == [
        "50_workbench/semantic_tasks/ch001.semantic_task.md",
        "40_manuscript/final/ch001.md",
        "50_workbench/semantic_tasks/ch001.semantic_context.json",
    ]
    context = json.loads(Path(task.context_file).read_text(encoding="utf-8"))
    assert context["schema"] == "chapter_semantic_context_v2"
    assert {item["id"] for item in context["stable_ids"]["characters"]} == {"char_shen", "char_he"}
    assert "char_future" not in json.dumps(context, ensure_ascii=False)
    assert manifest["policy"]["context"]["overflow_policy"] == "split_context"
    assert "Source Excerpt" not in Path(task.task_file).read_text(encoding="utf-8")

    payload = {
        "chapter_digest": {
            "summary": "沈阙将旧木牌交给何简，两人的调查合作由试探转为有限确认。",
            "causal_change": "木牌交接让何简进入证据链。",
            "reader_payoff": "确认何简愿意有限合作。",
            "cost": "沈阙失去对旧木牌的单独控制。",
        },
        "scenes": [
            {
                "scene_id": "ch001:scene:1",
                **evidence,
                "participants": ["char_shen", "char_he"],
                "location_id": "",
                "goal": "交付证物",
                "outcome": "建立有限合作",
            }
        ],
        "events": [
            {
                "event_id": "event:badge_handover",
                "title": "旧木牌交接",
                "participants": ["char_shen", "char_he"],
                "locations": [],
                "consequences": "何简进入调查证据链。",
                "evidence": evidence,
            }
        ],
        "relationship_deltas": [
            {
                "source_id": "char_shen",
                "target_id": "char_he",
                "prior_state": "试探",
                "new_state": "有限合作",
                "relation_type": "investigation_partner",
                "cause": "沈阙主动交付旧木牌。",
                "evidence": evidence,
            }
        ],
        "character_deltas": [
            {
                "character_id": "char_he",
                "status": "active",
                "goal": "核验旧木牌",
                "emotion": "克制认可",
                "beliefs_added": ["沈阙愿意交出关键证物"],
                "beliefs_removed": [],
                "knowledge_gained": [
                    {"fact": "旧木牌由沈阙持有", "route": "observed", "evidence": evidence}
                ],
                "knowledge_removed": [],
                "commitments_added": ["核验旧木牌"],
                "commitments_removed": [],
                "abilities_added": [],
                "abilities_removed": [],
                "inventory_added": ["旧木牌"],
                "inventory_removed": [],
                "evidence": evidence,
            }
        ],
        "foreshadow_deltas": [
            {
                "thread_id": "thread_old_badge",
                "action": "plant",
                "description": "旧木牌的来历仍未解释。",
                "resulting_status": "planted",
                "evidence": evidence,
            }
        ],
        "world_deltas": [{"fact_id": "world:badge_transfer", "value": "旧木牌可交接", "evidence": evidence}],
        "timeline_deltas": [{"event_id": "event:badge_handover", "order": 1, "evidence": evidence}],
        "retrieval": {
            "tags": ["旧木牌", "合作"],
            "entity_ids": ["char_shen", "char_he"],
            "focus": ["关系变化", "伏笔埋设"],
        },
        "coverage": {
            "featured_character_ids": ["char_shen", "char_he"],
            "unchanged_character_ids": ["char_shen"],
            "active_thread_ids": ["thread_old_badge"],
            "unchanged_thread_ids": [],
        },
    }
    output = Path(task.output_file)
    control = write_semantic_delta(root, task, payload)
    assert control.ok, control.normalization.errors

    validation = semantic_validate(config, chapter_number=1, file_path=output)
    assert validation.ok, validation.errors
    applied = semantic_apply(config, chapter_number=1, file_path=output)
    ledger_hash_after_first_apply = sha256(Path(applied.ledger_file).read_bytes()).hexdigest()
    repeated = semantic_apply(config, chapter_number=1, file_path=output)
    assert repeated.ledger_file == applied.ledger_file
    assert repeated.transaction_file
    assert repeated.embeddings_reused > 0
    assert sha256(Path(applied.ledger_file).read_bytes()).hexdigest() == ledger_hash_after_first_apply

    ledger = json.loads(Path(applied.ledger_file).read_text(encoding="utf-8"))
    graph = json.loads(Path(applied.graph_file).read_text(encoding="utf-8"))
    foreshadow = json.loads(Path(applied.foreshadow_state_file).read_text(encoding="utf-8"))
    character = json.loads(Path(applied.character_files[0]).read_text(encoding="utf-8"))
    assert ledger["canonical"] is True
    assert validate_graph(config).errors == ()
    assert any(item.get("state") == "有限合作" for item in graph["relationships"])
    assert any(item.get("id") == "thread_old_badge" for item in graph["entities"])
    assert foreshadow["threads"]["thread_old_badge"]["status"] == "planted"
    assert character["knowledge_scope"] == ["旧木牌由沈阙持有"]
    assert "state_history" not in character
    assert "沈阙将旧木牌" in Path(applied.summary_file).read_text(encoding="utf-8")
    assert Path(applied.tcs_file).exists()
    tcs_path = Path(applied.tcs_file)
    tcs_payload = json.loads(tcs_path.read_text(encoding="utf-8"))
    assert tcs_payload["schema"] == "tcs_compact_v2"
    assert tcs_payload["current_characters"] == ["char_he", "char_shen"]
    assert "char_future" not in tcs_payload["current_characters"]
    assert all(item["target"] != "char_future" for item in tcs_payload["relationship_state"])
    assert tcs_payload["source_semantic_ledger_sha256"] == sha256(Path(applied.ledger_file).read_bytes()).hexdigest()
    tcs_hash = sha256(tcs_path.read_bytes()).hexdigest()
    reused = build_tcs(config, chapter_number=2)
    assert reused.current_characters == ("char_he", "char_shen")
    assert sha256(tcs_path.read_bytes()).hexdigest() == tcs_hash
    assert validate_tcs(config, chapter_number=2).ok is True
    planned_relationship = next(item for item in graph["relationships"] if item.get("id") == "rel_shen_future_planned")
    assert planned_relationship["status"] == "planned"
    assert planned_relationship["from_chapter"] is None
    assert not list((root / "70_runtime" / "transactions" / "s").glob("*"))
    retrieval = query(config, "旧木牌", top_k=3)
    assert retrieval.hits
    assert "旧木牌" in retrieval.hits[0].text
    assert "semantic ledger routed chapter" in retrieval.hits[0].reasons

    gate_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    write_json(gate_dir / "gate_result.json", {"passed": True, "severity_counts": {"P0": 0, "P1": 0}})
    chunk_file = root / "60_rag" / "chunks" / "ch001.json"
    chunk_payload = json.loads(chunk_file.read_text(encoding="utf-8"))
    tampered_chunk = deepcopy(chunk_payload)
    tampered_chunk["source_sha256"] = "0" * 64
    write_json(chunk_file, tampered_chunk)
    with pytest.raises(ValueError, match="RAG chunks are not derived"):
        chapter_close(config, chapter_number=1, approved_by="tester")
    write_json(chunk_file, chunk_payload)

    graph_file = Path(applied.graph_file)
    materialized_graph = json.loads(graph_file.read_text(encoding="utf-8"))
    materialized_graph["last_semantic_chapter"] = 0
    write_json(graph_file, materialized_graph)
    with pytest.raises(ValueError, match="story graph is not materialized"):
        chapter_close(config, chapter_number=1, approved_by="tester")
    semantic_rebuild(config, through=1, approved_by="repair-owner")
    repaired_graph = json.loads(graph_file.read_text(encoding="utf-8"))
    repaired_foreshadow = json.loads(Path(applied.foreshadow_state_file).read_text(encoding="utf-8"))
    repaired_character = json.loads(Path(applied.character_files[0]).read_text(encoding="utf-8"))
    assert repaired_graph["last_semantic_chapter"] == 1
    assert len(repaired_foreshadow["threads"]["thread_old_badge"]["recent_actions"]) == 1
    assert len(repaired_character["recent_evidence"]) == 1

    closed = chapter_close(config, chapter_number=1, approved_by="tester")
    assert Path(closed.closure_file).exists()
    assert closed.next_command.endswith("--chapter 2")
    repeated_close = chapter_close(config, chapter_number=1, approved_by="another-user")
    assert repeated_close.closure_file == closed.closure_file
    assert repeated_close.approved_by == "tester"
    closure_hash = sha256(Path(closed.closure_file).read_bytes()).hexdigest()
    closed_reapply = semantic_apply(config, chapter_number=1, file_path=output)
    assert closed_reapply.transaction_file
    assert sha256(Path(applied.ledger_file).read_bytes()).hexdigest() == ledger_hash_after_first_apply
    assert sha256(Path(closed.closure_file).read_bytes()).hexdigest() == closure_hash

    ledger_hash_before_rebuild = sha256(Path(applied.ledger_file).read_bytes()).hexdigest()
    drifted_graph = json.loads(graph_file.read_text(encoding="utf-8"))
    drifted_graph["events"].append({"id": "event:stale-derived-fact", "title": "stale"})
    write_json(graph_file, drifted_graph)
    stale_character = root / "60_rag" / "memory" / "characters" / "stale.json"
    write_json(stale_character, {"character_id": "stale"})
    stale_tcs = root / "30_state" / "tcs" / "ch999.json"
    write_json(stale_tcs, {"chapter_number": 999})
    rebuilt = semantic_rebuild(config, through=1, approved_by="migration-owner")
    rebuilt_graph = json.loads(graph_file.read_text(encoding="utf-8"))
    assert not any(item.get("id") == "event:stale-derived-fact" for item in rebuilt_graph["events"])
    assert rebuilt_graph["last_semantic_chapter"] == 1
    assert not stale_character.exists()
    assert not stale_tcs.exists()
    assert sha256(Path(applied.ledger_file).read_bytes()).hexdigest() == ledger_hash_before_rebuild
    assert Path(closed.closure_file).exists()
    assert rebuilt.next_command == "longform-engine production next project.yaml"
    assert Path(rebuilt.transaction_file).exists()
    assert not list((root / "70_runtime" / "transactions" / "s").glob("*"))

    payload["chapter_digest"]["summary"] = "不同的候选不能覆盖已落盘语义事实。"
    write_semantic_delta(root, task, payload, validate_control=False)
    with pytest.raises(ValueError, match="different candidate"):
        semantic_apply(config, chapter_number=1, file_path=output)


def test_semantic_validation_rejects_hash_and_evidence_mismatch(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    final = project.root / "40_manuscript" / "final" / "ch001.md"
    final.write_text("# 第一章\n\n正文证据。\n", encoding="utf-8")
    task = semantic_task(config, chapter_number=1)
    payload = {
        "chapter_digest": {"summary": "摘要", "causal_change": "变化", "reader_payoff": "收益", "cost": "代价"},
        "scenes": [{"start": 0, "end": 2, "excerpt": "错误"}],
        "events": [],
        "relationship_deltas": [],
        "character_deltas": [],
        "foreshadow_deltas": [],
        "world_deltas": [],
        "timeline_deltas": [],
        "retrieval": {"tags": [], "entity_ids": [], "focus": []},
        "coverage": {
            "featured_character_ids": [],
            "unchanged_character_ids": [],
            "active_thread_ids": [],
            "unchanged_thread_ids": [],
        },
    }
    output = Path(task.output_file)
    invalid_delta = semantic_delta_payload(payload, source_name=final.name)
    invalid_delta["evidence"] = {
        "/changes/chapter_digest": [f"{final.name}@0:999"],
        "/changes/scenes/0": [f"{final.name}@0:999"],
    }
    write_json(output, invalid_delta)
    control = validate_production_agent_result(
        project.root,
        load_manifest(project.root, task.manifest_file),
        result_file=output,
    )
    assert control.ok is False
    result = semantic_validate(config, chapter_number=1, file_path=task.output_file)
    assert result.ok is False
    assert any("out of bounds" in error for error in result.errors)
    assert any("control-plane" in error for error in result.errors)
    assert not (project.root / "30_state" / "semantic_ledger" / "ch001.json").exists()

    outside_result = semantic_validate(config, chapter_number=1, file_path=final)
    assert outside_result.ok is False
    assert any("not declared" in error for error in outside_result.errors)
    assert Path(outside_result.report_file).parent == project.root / "50_workbench" / "semantic_tasks"
    assert not final.with_suffix(".validation.json").exists()

    forged = project.root / "50_workbench" / "semantic_tasks" / "ch001.forged.backfill.semantic.json"
    write_json(forged, invalid_delta)
    forged_result = semantic_validate(config, chapter_number=1, file_path=forged)
    assert any("not declared" in error for error in forged_result.errors)

    chapter_two = project.root / "40_manuscript" / "final" / "ch002.md"
    chapter_two.write_text("# 第二章\n\n后续正文证据。\n", encoding="utf-8")
    task_two = semantic_task(config, chapter_number=2)
    payload_two = deepcopy(payload)
    start = chapter_two.read_text(encoding="utf-8").index("后续")
    end = start + len("后续正文证据")
    payload_two["scenes"] = [{"start": start, "end": end, "excerpt": "后续正文证据"}]
    output_two = Path(task_two.output_file)
    write_json(output_two, semantic_delta_payload(payload_two, source_name=chapter_two.name))
    control_two = validate_production_agent_result(
        project.root,
        load_manifest(project.root, task_two.manifest_file),
        result_file=output_two,
    )
    assert control_two.ok, control_two.normalization.errors
    sequence_result = semantic_validate(config, chapter_number=2, file_path=task_two.output_file)
    assert any("must be applied before" in error for error in sequence_result.errors)


def write_semantic_delta(root: Path, task, payload: dict, *, validate_control: bool = True):
    output = Path(task.output_file)
    final = root / "40_manuscript" / "final" / f"ch{int(task.chapter_number):03d}.md"
    write_json(output, semantic_delta_payload(payload, source_name=final.name))
    if not validate_control:
        return None
    return validate_production_agent_result(
        root,
        load_manifest(root, task.manifest_file),
        result_file=output,
    )


def semantic_delta_payload(payload: dict, *, source_name: str) -> dict:
    """Convert rich test facts into the compact Agent-authored semantic protocol."""

    changes = deepcopy(payload)
    evidence: dict[str, list[str]] = {}

    def compact(item: dict, pointer: str, *, inline: bool = False) -> None:
        evidence = item if inline else item.pop("evidence", None)
        if not isinstance(evidence, dict):
            return
        evidence_id = f"{source_name}@{int(evidence['start'])}:{int(evidence['end'])}"
        evidence_map[pointer] = [evidence_id]
        if inline:
            item.pop("start", None)
            item.pop("end", None)
            item.pop("excerpt", None)

    evidence_map = evidence
    for index, scene in enumerate(changes.get("scenes") or []):
        compact(scene, f"/changes/scenes/{index}", inline=True)
    for field in (
        "events",
        "relationship_deltas",
        "character_deltas",
        "foreshadow_deltas",
        "world_deltas",
        "timeline_deltas",
    ):
        for index, item in enumerate(changes.get(field) or []):
            pointer = f"/changes/{field}/{index}"
            compact(item, pointer)
            if field == "character_deltas":
                for fact_index, fact in enumerate(item.get("knowledge_gained") or []):
                    compact(fact, f"{pointer}/knowledge_gained/{fact_index}")

    changes["entity_coverage"] = changes.pop("coverage")
    sections = {
        "chapter_digest": "changed",
        "scenes": "changed" if changes.get("scenes") else "unchanged",
        "events": "changed" if changes.get("events") else "unchanged",
        "relationships": "changed" if changes.get("relationship_deltas") else "unchanged",
        "characters": "changed" if changes.get("character_deltas") else "unchanged",
        "foreshadowing": "changed" if changes.get("foreshadow_deltas") else "unchanged",
        "world": "changed" if changes.get("world_deltas") else "unchanged",
        "timeline": "changed" if changes.get("timeline_deltas") else "unchanged",
    }
    return {
        "schema": CANONICAL_DELTA_SCHEMA,
        "delta_type": "chapter_semantic",
        "coverage": sections,
        "evidence": {
            "/changes/chapter_digest": next(iter(evidence.values()), [f"{source_name}@0:1"]),
            **evidence,
        },
        "changes": changes,
        "uncertainties": [],
    }


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
