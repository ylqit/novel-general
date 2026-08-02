from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.config import load_project_config
from longform_engine.graph import validate_graph
from longform_engine.rag import query
from longform_engine.semantic import chapter_close, semantic_apply, semantic_rebuild, semantic_task, semantic_validate
from longform_engine.storage import init_project


def test_unified_semantic_bundle_materializes_evidence_bound_views(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = project.root
    write_json(
        root / "10_bible" / "characters.json",
        [
            {"id": "char_shen", "name": "沈阙"},
            {"id": "char_he", "name": "何简"},
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
            }
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
    manifest = json.loads(Path(task.manifest_file).read_text(encoding="utf-8"))
    assert manifest["task_type"] == "chapter_semantic"
    assert len(manifest["input_files"]) <= 7
    assert "Source Excerpt" not in Path(task.task_file).read_text(encoding="utf-8")

    payload = {
        "schema": "chapter_semantic_bundle_v1",
        "chapter_number": 1,
        "source": {
            "path": "40_manuscript/final/ch001.md",
            "sha256": sha256(final.read_bytes()).hexdigest(),
        },
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
    write_json(output, payload)

    validation = semantic_validate(config, chapter_number=1, file_path=output)
    assert validation.ok, validation.errors
    applied = semantic_apply(config, chapter_number=1, file_path=output)
    repeated = semantic_apply(config, chapter_number=1, file_path=output)
    assert repeated.ledger_file == applied.ledger_file

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
    assert not list((root / "70_runtime" / "transactions" / "s").glob("*"))
    retrieval = query(config, "旧木牌", top_k=3)
    assert retrieval.hits
    assert "旧木牌" in retrieval.hits[0].text
    assert "semantic ledger routed chapter" in retrieval.hits[0].reasons

    gate_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    write_json(gate_dir / "gate_result.json", {"passed": True, "severity_counts": {"P0": 0, "P1": 0}})
    graph_file = Path(applied.graph_file)
    materialized_graph = json.loads(graph_file.read_text(encoding="utf-8"))
    materialized_graph["last_semantic_chapter"] = 0
    write_json(graph_file, materialized_graph)
    with pytest.raises(ValueError, match="story graph is not materialized"):
        chapter_close(config, chapter_number=1, approved_by="tester")
    semantic_apply(config, chapter_number=1, file_path=output)
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
    write_json(output, payload)
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
        "schema": "chapter_semantic_bundle_v1",
        "chapter_number": 1,
        "source": {"path": "40_manuscript/final/ch001.md", "sha256": "bad"},
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
    write_json(Path(task.output_file), payload)
    result = semantic_validate(config, chapter_number=1, file_path=task.output_file)
    assert result.ok is False
    assert any("sha256" in error for error in result.errors)
    assert any("excerpt" in error for error in result.errors)
    assert not (project.root / "30_state" / "semantic_ledger" / "ch001.json").exists()

    outside_result = semantic_validate(config, chapter_number=1, file_path=final)
    assert outside_result.ok is False
    assert any("not declared" in error for error in outside_result.errors)
    assert Path(outside_result.report_file).parent == project.root / "50_workbench" / "semantic_tasks"
    assert not final.with_suffix(".validation.json").exists()

    forged = project.root / "50_workbench" / "semantic_tasks" / "ch001.forged.backfill.semantic.json"
    write_json(forged, payload)
    forged_result = semantic_validate(config, chapter_number=1, file_path=forged)
    assert any("not declared" in error for error in forged_result.errors)

    chapter_two = project.root / "40_manuscript" / "final" / "ch002.md"
    chapter_two.write_text("# 第二章\n\n后续正文证据。\n", encoding="utf-8")
    task_two = semantic_task(config, chapter_number=2)
    payload_two = json.loads(json.dumps(payload, ensure_ascii=False))
    payload_two["chapter_number"] = 2
    payload_two["source"] = {
        "path": "40_manuscript/final/ch002.md",
        "sha256": sha256(chapter_two.read_bytes()).hexdigest(),
    }
    write_json(Path(task_two.output_file), payload_two)
    sequence_result = semantic_validate(config, chapter_number=2, file_path=task_two.output_file)
    assert any("must be applied before" in error for error in sequence_result.errors)


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
