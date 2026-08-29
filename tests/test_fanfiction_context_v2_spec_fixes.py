from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import zipfile

import pytest

from longform_engine import fanfiction_context as context_module
from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_tasks import load_manifest, manifest_output
from longform_engine.artifacts import compact_artifacts
from longform_engine.chapter_contract import validate_chapter_contract
from longform_engine.config import load_project_config
from longform_engine.fanfiction_context import (
    FanfictionContextError,
    compile_fanfiction_context,
    write_fanfiction_context_bundle,
)
from longform_engine.narrative_events import (
    apply_event_realization,
    build_event_realization_application,
    validate_event_realization_application,
)
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.intelligence.pipeline import semantic_task_target
from longform_engine.human_chapter_intent import (
    apply_human_chapter_intent,
    create_human_chapter_intent_task,
    validate_human_chapter_intent,
)
from longform_engine.orchestration import (
    continue_write,
    finalize_chapter,
    open_book,
    submit_agent_draft,
)
from longform_engine.planning import (
    apply_planning_bundle,
    build_human_node_decisions,
    build_human_planning_approval,
    build_planning_semantic_application,
)
from longform_engine.planning import workflow as planning_workflow
from longform_engine.semantic import chapter_close, semantic_apply
from longform_engine.semantic_protocols import build_semantic_document, seal_semantic_document
from longform_engine.storage import init_project
from tests.project_fixtures import (
    approve_story_candidate,
    complete_unified_semantic_lifecycle,
    mark_project_ready,
    prepare_unified_semantic_bundle,
)
from tests.test_fanfiction_contracts import (
    configure_crossover_project,
    install_route,
    reapprove,
    reviewed_route_projection_sha256,
    semantic_claim,
    write_document,
)
from tests.test_v010_event_realization import write_json
from tests.test_v010_planning import evidence_review, planning_bundle


pytest_plugins = ("tests.test_fanfiction_contracts",)


def _claim_channel(*, chapter: list[str] | None = None) -> dict:
    chapter_refs = list(chapter or [])
    return {
        "schema": "fanfiction_chapter_claim_channel_v1",
        "active_volume_claim_refs": [],
        "semantic_obligation_claim_refs": [],
        "plot_node_claim_refs": [],
        "chapter_claim_refs": chapter_refs,
        "all_claim_refs": chapter_refs,
    }


def _contract(*, claim_refs: list[str] | None = None) -> dict:
    return {
        "schema": "chapter_contract_v5",
        "contract_id": "contract:ch001",
        "chapter_number": 1,
        "forecast_ref": "forecast:ch001",
        "topology": "relationship",
        "chapter_duty": "让已批准的规则迫使人物作出选择。",
        "observable_change": "选择改变了下一章可采取的行动。",
        "reader_value": "读者看到规则、选择与代价形成因果。",
        "failure": {
            "applicability": "optional",
            "description": "直接方案可能失败。",
            "reason": "人物选择承担本章转折。",
        },
        "choice": {
            "applicability": "required",
            "description": "人物采用有代价的替代方案。",
            "reason": "选择造成可观察变化。",
        },
        "cost": {
            "applicability": "required",
            "description": "人物失去一种安全选项。",
            "reason": "收益必须缩窄后续选择。",
        },
        "aftermath": {
            "applicability": "optional",
            "description": "余波可延续到下一章。",
            "reason": "本章在行动后果处结束。",
        },
        "plot_node_table_ref": {
            "table_id": "node-table:ch001",
            "candidate_sha256": "a" * 64,
        },
        "semantic_obligation_refs": ["obligation:choice"],
        "reader_promise_actions": [],
        "protected_invariants": ["人物保留拒绝权。"],
        "prohibited_drift": ["不得让代价自动消失。"],
        "fanfiction_claim_refs": _claim_channel(chapter=claim_refs),
    }


def _persist_compile_inputs(root: Path, contract: dict, card: dict) -> None:
    write_json(root / "20_outline/chapter_contracts" / f"ch{contract['chapter_number']:03d}.json", contract)
    write_json(root / "20_outline/chapter_cards" / f"ch{contract['chapter_number']:03d}.json", card)


def _write_current_semantic_and_plot(root: Path, final: Path) -> str:
    write_json(
        root / "30_state/semantic_ledger/ch001.json",
        {
            "schema": "chapter_semantic_bundle_v1",
            "chapter_number": 1,
            "canonical": True,
            "source": {
                "path": "40_manuscript/final/ch001.md",
                "sha256": sha256(final.read_bytes()).hexdigest(),
            },
        },
    )
    plot = {
        "schema": "plot_node_table_v1",
        "chapter_number": 1,
        "nodes": [],
        "approval_sha256": "a" * 64,
    }
    plot_path = write_json(
        root / "20_outline/plot_nodes/ch001.json",
        plot,
    )
    return sha256(plot_path.read_bytes()).hexdigest()


def _bundle_hash(payload: dict) -> str:
    body = {key: value for key, value in payload.items() if key != "bundle_sha256"}
    return sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _rebind_route(project: dict, route: dict) -> None:
    root = project["root"]
    binding = route["extensions"]["independent_review"]
    review_path = root / binding["review_path"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    target_path = root / review["extensions"]["review_target_path"]
    candidate = deepcopy(route)
    candidate["artifact"]["state"] = "candidate"
    candidate["extensions"].pop("human_decision", None)
    candidate["extensions"].pop("approved_candidate_sha256", None)
    candidate["extensions"].pop("independent_review", None)
    candidate = seal_semantic_document(candidate)
    target_sha = write_document(target_path, candidate)
    review["extensions"]["review_target_sha256"] = target_sha
    review = reapprove(review)
    review_sha = write_document(review_path, review)
    route["extensions"]["independent_review"].update(
        {
            "review_sha256": review_sha,
            "review_artifact_id": review["artifact"]["artifact_id"],
            "review_target_sha256": target_sha,
            "reviewed_route_projection_sha256": reviewed_route_projection_sha256(candidate),
        }
    )
    write_document(root / "10_bible/fanfiction/fanfiction_bible.json", reapprove(route))


def _project_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def _write_planning_preflight_inputs(root: Path, bundle: dict) -> tuple[Path, Path, Path, Path]:
    planning = root / "50_workbench/planning"
    return (
        write_json(planning / "bundle.json", bundle),
        write_json(planning / "application.json", {}),
        write_json(planning / "approval.json", {}),
        write_json(planning / "decisions.json", {}),
    )


def test_formal_chapter_contract_validates_claim_channel_and_rejects_union_drift():
    contract = _contract(claim_refs=["route:entry", "route:knowledge"])
    assert validate_chapter_contract(contract) == []

    contract["fanfiction_claim_refs"]["all_claim_refs"] = ["route:entry"]
    errors = validate_chapter_contract(contract)
    assert any("all_claim_refs" in error for error in errors)


def test_applicability_dimensions_are_and_and_wrong_source_optional_hit_is_omitted(
    current_contract_project,
    monkeypatch,
):
    project = current_contract_project
    configure_crossover_project(project)
    route, _ = install_route(project)
    scoped = semantic_claim(
        "route:scoped_ability",
        "能力条件",
        extensions={
            "source_ids": ["classic"],
            "character_ids": ["character:lead"],
            "event_ids": ["event:gate"],
            "volume_ids": ["volume:001"],
            "arc_ids": ["arc:main"],
            "chapter_numbers": [1],
            "from_chapter": 1,
            "to_chapter": 2,
            "identity": {
                "identity_id": "ability:scoped",
                "kind": "ability",
                "display_name": "回声",
                "source_id": "classic",
            },
        },
    )
    wrong_source = semantic_claim(
        "route:wrong_source_optional",
        "能力条件",
        extensions={"source_ids": ["guest"], "chapter_numbers": [1]},
    )
    route = deepcopy(route)
    route["claims"].extend([scoped, wrong_source])
    _rebind_route(project, route)

    class Hit:
        def __init__(self, claim_id: str):
            self.id = f"source-canon:{claim_id}"

    class Result:
        hits = [Hit("route:wrong_source_optional")]
        omitted_hit_ids: tuple[str, ...] = ()
        used_units = 1

    monkeypatch.setattr(context_module, "rag_query", lambda *_args, **_kwargs: Result())
    contract = _contract(claim_refs=["route:scoped_ability"])
    card = {
        "chapter_number": 1,
        "volume_id": "volume:001",
        "arc_id": "arc:main",
        "source_ids": ["classic"],
        "featured_character_ids": ["character:lead"],
        "event_ids": ["event:gate"],
        "reader_value": "规则发生作用。",
        "observable_change": "选择改变局势。",
    }
    _persist_compile_inputs(project["root"], contract, card)

    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract=contract,
        chapter_card=card,
        character_packet={},
    )

    assert "route:scoped_ability" in bundle["required_claim_ids"]
    assert "route:wrong_source_optional" not in bundle["included_claim_ids"]


def test_required_claim_ids_equal_only_the_formal_contract_channel(current_contract_project):
    project = current_contract_project
    install_route(project)
    contract = _contract(claim_refs=["route:entry"])
    card = {"chapter_number": 1, "fanfiction_claim_refs": ["route:event"]}
    _persist_compile_inputs(project["root"], contract, card)

    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract=contract,
        chapter_card=card,
        character_packet={"fanfiction_claim_refs": ["route:knowledge"]},
    )

    assert bundle["required_claim_ids"] == ["route:entry"]
    assert "route:event" not in bundle["selection_reasons"]
    assert "route:knowledge" not in bundle["selection_reasons"]


def test_explicit_global_claim_remains_observable_as_required(current_contract_project):
    project = current_contract_project
    install_route(project)
    global_claim_id = "engine:claim_0"
    contract = _contract(claim_refs=[global_claim_id])
    card = {"chapter_number": 1}
    _persist_compile_inputs(project["root"], contract, card)

    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract=contract,
        chapter_card=card,
        character_packet={},
    )

    assert bundle["required_claim_ids"] == [global_claim_id]
    assert global_claim_id in bundle["global_claim_ids"]
    assert bundle["selection_reasons"][global_claim_id] == [
        "global_story_promise",
        "chapter_explicit_ref",
    ]


@pytest.mark.parametrize("origin", ["active_volume", "semantic_obligation", "plot_node", "chapter"])
def test_planning_apply_rejects_unknown_claim_from_every_formal_origin_without_pollution(
    current_contract_project,
    origin,
):
    project = current_contract_project
    install_route(project)
    bundle = planning_bundle()
    unknown = f"route:missing:{origin}"
    active_refs = [unknown] if origin == "active_volume" else []
    obligation_refs = [unknown] if origin == "semantic_obligation" else []
    bundle["active_volume_plan"]["fanfiction_projection"] = {
        "body": "当前卷只使用已批准的同人语义主张。",
        "claim_refs": active_refs,
    }
    bundle["semantic_obligations"][0]["fanfiction_claim_refs"] = obligation_refs
    for table in bundle["plot_node_tables"]:
        for node in table["nodes"]:
            node["fanfiction_claim_refs"] = (
                [unknown]
                if origin == "plot_node"
                and table["chapter_number"] == 1
                and node["node_kind"] == "state_change"
                else []
            )
    for contract in bundle["chapter_contracts"]:
        channel = contract["fanfiction_claim_refs"]
        plot_refs = [unknown] if origin == "plot_node" and contract["chapter_number"] == 1 else []
        chapter_refs = [unknown] if origin == "chapter" and contract["chapter_number"] == 1 else []
        channel.update(
            {
                "active_volume_claim_refs": active_refs,
                "semantic_obligation_claim_refs": obligation_refs,
                "plot_node_claim_refs": plot_refs,
                "chapter_claim_refs": chapter_refs,
                "all_claim_refs": [*active_refs, *obligation_refs, *plot_refs, *chapter_refs],
            }
        )
    paths = _write_planning_preflight_inputs(project["root"], bundle)
    before = _project_bytes(project["root"])

    with pytest.raises(ValueError, match="unresolved stable claim"):
        apply_planning_bundle(
            project["config"],
            bundle_path=paths[0],
            application_path=paths[1],
            approval_path=paths[2],
            node_decisions_path=paths[3],
            approved_by="human",
        )

    assert _project_bytes(project["root"]) == before


def test_planning_apply_rejects_out_of_scope_and_duplicate_claim_provenance_before_write(
    current_contract_project,
):
    project = current_contract_project
    route, _ = install_route(project)
    scoped = semantic_claim(
        "route:future_only",
        "能力条件",
        extensions={"chapter_numbers": [99]},
    )
    route = deepcopy(route)
    route["claims"].append(scoped)
    _rebind_route(project, route)
    bundle = planning_bundle()
    bundle["active_volume_plan"]["fanfiction_projection"] = {
        "body": "第一章引用必须落在正式适用范围内。",
        "claim_refs": [],
    }
    for contract in bundle["chapter_contracts"]:
        refs = ["route:future_only"] if contract["chapter_number"] == 1 else []
        contract["fanfiction_claim_refs"]["chapter_claim_refs"] = refs
        contract["fanfiction_claim_refs"]["all_claim_refs"] = refs
    paths = _write_planning_preflight_inputs(project["root"], bundle)
    before = _project_bytes(project["root"])
    with pytest.raises(ValueError, match="out of scope"):
        apply_planning_bundle(
            project["config"],
            bundle_path=paths[0],
            application_path=paths[1],
            approval_path=paths[2],
            node_decisions_path=paths[3],
            approved_by="human",
        )
    assert _project_bytes(project["root"]) == before

    duplicate_route = json.loads(
        (project["root"] / "10_bible/fanfiction/fanfiction_bible.json").read_text(
            encoding="utf-8"
        )
    )
    duplicate_route["claims"].append(
        semantic_claim("engine:claim_0", "能力条件")
    )
    _rebind_route(project, duplicate_route)
    clean_bundle = planning_bundle()
    clean_bundle["active_volume_plan"]["fanfiction_projection"] = {
        "body": "正式声明必须拥有唯一来源。",
        "claim_refs": [],
    }
    duplicate_paths = _write_planning_preflight_inputs(project["root"], clean_bundle)
    before_duplicate = _project_bytes(project["root"])
    with pytest.raises(ValueError, match="duplicate claim provenance"):
        apply_planning_bundle(
            project["config"],
            bundle_path=duplicate_paths[0],
            application_path=duplicate_paths[1],
            approval_path=duplicate_paths[2],
            node_decisions_path=duplicate_paths[3],
            approved_by="human",
        )
    assert _project_bytes(project["root"]) == before_duplicate


def test_planning_applicability_intersects_all_chapter_dimensions_on_one_candidate():
    claim = semantic_claim(
        "route:impossible_chapter_intersection",
        "能力条件",
        extensions={
            "chapter_numbers": [1, 3],
            "from_chapter": 2,
            "to_chapter": 2,
        },
    )

    assert not planning_workflow._planning_claim_applies(
        claim,
        chapter_numbers={1, 2, 3},
        volume_id="volume:001",
        source_ids={"classic"},
        character_ids={"character:lead"},
        event_ids={"event:gate"},
        arc_ids={"arc:main"},
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "wrong_chapter",
        "review_statement",
        "fake_dependency_reason",
        "fake_partition",
        "fake_budget",
        "fake_evidence",
    ],
)
def test_deep_v2_validator_rejects_rehashed_cross_field_tampering(
    current_contract_project,
    tamper,
):
    project = current_contract_project
    install_route(project)
    contract = _contract(claim_refs=["route:event", "classic:canon"])
    card = {"chapter_number": 1, "volume_id": "volume:001"}
    _persist_compile_inputs(project["root"], contract, card)
    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract=contract,
        chapter_card=card,
        character_packet={},
    )
    changed = deepcopy(bundle)
    if tamper == "wrong_chapter":
        changed["chapter_number"] = 2
    elif tamper == "review_statement":
        changed["review_projection"]["claims"][0]["statement"] += "篡改"
    elif tamper == "fake_dependency_reason":
        changed["dependency_closure"][0]["reason"] = "伪造原因"
    elif tamper == "fake_partition":
        changed["source_partitions"]["source"]["fake"] = [changed["included_claim_ids"][0]]
    elif tamper == "fake_budget":
        changed["budget_usage"]["used_units"] += 1
    else:
        changed["review_projection"]["evidence_closure"] = []
    changed["bundle_sha256"] = _bundle_hash(changed)

    with pytest.raises(FanfictionContextError):
        write_fanfiction_context_bundle(project["root"], changed)


def test_realized_major_divergence_is_formal_and_not_inferred_from_event_dependencies(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)
    config = project["config"]
    root = project["root"]
    contract = _contract(claim_refs=["route:divergence", "route:knowledge"])
    card = {"chapter_number": 1}
    _persist_compile_inputs(root, contract, card)
    write_fanfiction_context_bundle(
        root,
        compile_fanfiction_context(
            config,
            chapter_number=1,
            chapter_contract=contract,
            chapter_card=card,
            character_packet={},
        ),
    )
    text = "# 第一章\n\n人物完成了一次有代价的选择。\n"
    final = root / "40_manuscript/final/ch001.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(text, encoding="utf-8")
    plot_sha = _write_current_semantic_and_plot(root, final)
    ledger_path = root / "30_state/narrative_events/ch001.json"
    write_json(
        ledger_path,
        {
            "schema": "narrative_event_ledger_v1",
            "chapter_number": 1,
            "events": [
                {
                    "schema": "narrative_event_v1",
                    "event_id": "event:ch001:choice",
                    "source_node_id": "node:ch001:choice",
                    "chapter_number": 1,
                    "preconditions": [],
                    "dependency_refs": ["node:earlier"],
                    "fanfiction_claim_refs": ["route:divergence"],
                    "expected_changes": [{"domain": "relationship"}],
                    "reader_effect": "选择改变可行动者。",
                    "state": "planned_approved",
                    "realization_evidence": None,
                }
            ],
            "source_plot_node_table_sha256": plot_sha,
        },
    )
    start = text.index("人物")
    end = text.index("。", start) + 1
    exact = {"start": start, "end": end, "excerpt": text[start:end]}
    application = build_event_realization_application(
        config,
        chapter_number=1,
        observations=[
            {
                "event_id": "event:ch001:choice",
                "state": "realized",
                "evidence": exact,
                "semantic_reason": "最终文本展示了选择。",
            }
        ],
        discovered_causal_nodes=[],
        confirmed_by="human",
        realized_major_divergences=[
            {
                "declaration_id": "declaration:choice",
                "source_event_id": "event:ch001:choice",
                "source_claim_id": "route:divergence",
                "realized_chapter": 1,
                "impact_level": "major",
                "knowledge_scope_refs": ["route:knowledge"],
                "human_confirmation": {
                    "confirmed_by": "human",
                    "reason": "终稿已实现批准分歧。",
                },
                "evidence": exact,
            }
        ],
    )
    assert validate_event_realization_application(config, application).ok
    application_path = write_json(
        root / "50_workbench/event_realizations/ch001.json", application
    )

    apply_event_realization(config, application_path=application_path)

    realized = json.loads(ledger_path.read_text(encoding="utf-8"))[
        "realized_major_divergences"
    ]
    assert [item["trigger_id"] for item in realized] == [
        application["realized_major_divergences"][0]["trigger_id"]
    ]
    assert realized[0]["declaration_id"] == "declaration:choice"
    assert realized[0]["final_sha256"] == application["final"]["sha256"]
    assert realized[0]["semantic_ledger_sha256"] == application["semantic_ledger"]["sha256"]
    assert len(realized[0]["realization_application_sha256"]) == 64


def test_event_realization_rejects_semantically_invalid_and_duplicate_divergences_before_write(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)
    root = project["root"]
    contract = _contract(
        claim_refs=["route:divergence", "route:knowledge", "route:event"]
    )
    card = {"chapter_number": 1}
    _persist_compile_inputs(root, contract, card)
    write_fanfiction_context_bundle(
        root,
        compile_fanfiction_context(
            project["config"],
            chapter_number=1,
            chapter_contract=contract,
            chapter_card=card,
            character_packet={},
        ),
    )
    final = root / "40_manuscript/final/ch001.md"
    ledger = root / "30_state/narrative_events/ch001.json"
    text = "# 第一章\n\n人物完成了一次有代价的选择。\n"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text(text, encoding="utf-8")
    plot_sha = _write_current_semantic_and_plot(root, final)
    write_json(
        ledger,
        {
            "schema": "narrative_event_ledger_v1",
            "chapter_number": 1,
            "events": [
                {
                    "schema": "narrative_event_v1",
                    "event_id": "event:ch001:choice",
                    "source_node_id": "node:ch001:choice",
                    "chapter_number": 1,
                    "preconditions": [],
                    "dependency_refs": [],
                    "fanfiction_claim_refs": ["route:knowledge", "route:event"],
                    "expected_changes": [{"domain": "relationship"}],
                    "reader_effect": "选择改变可行动者。",
                    "state": "planned_approved",
                    "realization_evidence": None,
                }
            ],
            "source_plot_node_table_sha256": plot_sha,
        },
    )
    start = text.index("人物")
    end = text.index("。", start) + 1
    evidence = {"start": start, "end": end, "excerpt": text[start:end]}
    base = {
        "declaration_id": "declaration:duplicate",
        "source_event_id": "event:ch001:choice",
        "source_claim_id": "route:knowledge",
        "realized_chapter": 1,
        "impact_level": "major",
        "knowledge_scope_refs": ["route:event"],
        "human_confirmation": {"confirmed_by": "human", "reason": "人工确认分歧。"},
        "evidence": evidence,
    }
    application = build_event_realization_application(
        project["config"],
        chapter_number=1,
        observations=[
            {
                "event_id": "event:ch001:choice",
                "state": "realized",
                "evidence": evidence,
                "semantic_reason": "最终文本展示了选择。",
            }
        ],
        discovered_causal_nodes=[],
        confirmed_by="human",
        realized_major_divergences=[
            dict(base),
            dict(base),
        ],
    )
    application_file = write_json(
        root / "50_workbench/event_realizations/ch001.invalid.json", application
    )
    before = _project_bytes(root)

    validation = validate_event_realization_application(project["config"], application)
    assert not validation.ok
    assert any("source claim" in error for error in validation.errors)
    assert any("knowledge scope" in error for error in validation.errors)
    assert any("duplicate logical" in error for error in validation.errors)
    with pytest.raises(ValueError):
        apply_event_realization(project["config"], application_path=application_file)
    assert _project_bytes(root) == before


def test_future_knowledge_reassessment_is_independent_typed_task_and_human_apply_enters_closure(
    current_contract_project,
    monkeypatch,
):
    project = current_contract_project
    install_route(project)
    root = project["root"]
    chapter_one = _contract(claim_refs=["route:divergence", "route:knowledge"])
    chapter_one_card = {"chapter_number": 1, "volume_id": "volume:001"}
    _persist_compile_inputs(root, chapter_one, chapter_one_card)
    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract=chapter_one,
        chapter_card=chapter_one_card,
        character_packet={},
    )
    bundle_path = write_fanfiction_context_bundle(root, bundle)
    final = root / "40_manuscript/final/ch001.md"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text("证据显示人物作出有代价的选择。\n", encoding="utf-8")
    plot_sha = _write_current_semantic_and_plot(root, final)
    planned_event = {
        "schema": "narrative_event_v1",
        "event_id": "event:ch001:gate",
        "source_node_id": "node:ch001:gate",
        "chapter_number": 1,
        "preconditions": [],
        "dependency_refs": [],
        "fanfiction_claim_refs": ["route:divergence"],
        "expected_changes": [{"domain": "relationship"}],
        "reader_effect": "选择改变后续行动边界。",
        "state": "planned_approved",
        "realization_evidence": None,
    }
    event_payload = {
        "schema": "narrative_event_ledger_v1",
        "chapter_number": 1,
        "events": [planned_event],
        "realized_major_divergences": [],
        "source_plot_node_table_sha256": plot_sha,
    }
    event_path = write_json(root / "30_state/narrative_events/ch001.json", event_payload)
    evidence = {"start": 0, "end": 2, "excerpt": "证据"}
    realization = build_event_realization_application(
        project["config"],
        chapter_number=1,
        observations=[
            {
                "event_id": "event:ch001:gate",
                "state": "realized",
                "evidence": evidence,
                "semantic_reason": "终稿精确证据显示已批准事件实现。",
            }
        ],
        discovered_causal_nodes=[],
        confirmed_by="human",
        realized_major_divergences=[
            {
                "declaration_id": "declaration:ch001:gate",
                "source_event_id": "event:ch001:gate",
                "source_claim_id": "route:divergence",
                "realized_chapter": 1,
                "impact_level": "major",
                "knowledge_scope_refs": ["route:knowledge"],
                "human_confirmation": {
                    "confirmed_by": "human",
                    "reason": "已核对终稿证据。",
                },
                "evidence": evidence,
            }
        ],
    )
    realization_path = write_json(
        root / "50_workbench/event_realizations/ch001.valid.json", realization
    )
    apply_event_realization(project["config"], application_path=realization_path)
    event_payload = json.loads(event_path.read_text(encoding="utf-8"))
    trigger_id = event_payload["realized_major_divergences"][0]["trigger_id"]
    workflow_path, workflow = context_module.future_knowledge_impact_workflows(
        project["config"],
        chapter_number=1,
        event_ledger=event_payload,
        event_ledger_path=event_path,
    )[0]
    write_json(workflow_path, workflow)

    task = create_intelligence_task(
        project["config"],
        task_type="fanfiction_future_knowledge_reassessment",
        input_files=[workflow_path, bundle_path, event_path],
        chapter_number=1,
    )
    candidate_path = root / task.candidate_file
    input_hashes = {
        item["kind"]: item["sha256"] for item in workflow["inputs"]
    }

    def candidate(reliability: str) -> dict:
        return seal_semantic_document(
            build_semantic_document(
                document_id="sem:future_knowledge:ch001:gate",
                document_type="同人未来知识重估",
                title="重大分歧后的知识可靠性候选",
                scope={"kind": "chapter", "chapter_number": 1},
                continuity="批准分歧后的知识边界",
                body="只评估本次触发绑定的知识范围，不自动修改原著基线。",
                claims=[
                    semantic_claim(
                        "future:ch001:gate:knowledge",
                        "未来知识可靠性",
                        extensions={
                            "trigger_id": trigger_id,
                            "trigger_sha256": sha256(
                                json.dumps(
                                    workflow["extensions"]["trigger"],
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest(),
                            "input_hashes": input_hashes,
                            "knowledge_claim_id": "route:knowledge",
                            "knowledge_range": {
                                "from_chapter": 2,
                                "to_chapter": None,
                                "scope_refs": ["route:knowledge"],
                            },
                            "reliability": reliability,
                            "depends_on_claims": [
                                "route:knowledge",
                                "route:divergence",
                            ],
                        },
                    )
                ],
                extensions={
                    "task_type": "fanfiction_future_knowledge_reassessment",
                    "trigger_id": trigger_id,
                },
            )
        )

    write_document(candidate_path, candidate("未知状态"))
    invalid_submission = validate_production_agent_result(
        root,
        load_manifest(root, task.task_id),
        result_file=candidate_path,
    )
    assert invalid_submission.ok
    invalid = validate_intelligence_candidate(
        project["config"],
        task_type="fanfiction_future_knowledge_reassessment",
        file_path=candidate_path,
    )
    assert not invalid.ok
    assert any("仍可靠|部分可靠|已失效|反向误导" in error for error in invalid.errors)

    write_document(candidate_path, candidate("部分可靠"))
    valid_submission = validate_production_agent_result(
        root,
        load_manifest(root, task.task_id),
        result_file=candidate_path,
    )
    assert valid_submission.ok
    valid = validate_intelligence_candidate(
        project["config"],
        task_type="fanfiction_future_knowledge_reassessment",
        file_path=candidate_path,
    )
    assert valid.ok, valid.errors
    with pytest.raises(ValueError, match="approved-by human"):
        apply_intelligence_candidate(
            project["config"],
            task_type="fanfiction_future_knowledge_reassessment",
            file_path=candidate_path,
        )
    from longform_engine import future_knowledge_provenance as provenance_module

    expected_target = semantic_task_target(
        root,
        "fanfiction_future_knowledge_reassessment",
        json.loads(candidate_path.read_text(encoding="utf-8")),
    )
    real_pin_write = provenance_module.atomic_write_text

    def fail_pin_write(path: Path, text: str) -> None:
        if path.name == "future_knowledge_provenance_pins.json":
            raise RuntimeError("injected pin registry failure")
        real_pin_write(path, text)

    with monkeypatch.context() as fault:
        fault.setattr(provenance_module, "atomic_write_text", fail_pin_write)
        with pytest.raises(RuntimeError, match="injected pin registry failure"):
            apply_intelligence_candidate(
                project["config"],
                task_type="fanfiction_future_knowledge_reassessment",
                file_path=candidate_path,
                approved_by="human",
            )
    assert not expected_target.exists()
    assert not (root / "30_state/future_knowledge_provenance_pins.json").exists()
    provenance_archive_root = root / "70_runtime/artifacts/future_knowledge"
    assert not list(provenance_archive_root.glob("*.zip"))
    assert load_manifest(root, task.task_id)["status"] == "validated"

    applied = apply_intelligence_candidate(
        project["config"],
        task_type="fanfiction_future_knowledge_reassessment",
        file_path=candidate_path,
        approved_by="human",
    )
    assert applied.status == "applied"
    repeated = apply_intelligence_candidate(
        project["config"],
        task_type="fanfiction_future_knowledge_reassessment",
        file_path=candidate_path,
        approved_by="human",
    )
    assert repeated.status == "applied"
    assert repeated.transaction_report == ""

    chapter_two = deepcopy(chapter_one)
    chapter_two["contract_id"] = "contract:ch002"
    chapter_two["chapter_number"] = 2
    chapter_two_card = {"chapter_number": 2, "volume_id": "volume:001"}
    _persist_compile_inputs(root, chapter_two, chapter_two_card)
    next_bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=2,
        chapter_contract=chapter_two,
        chapter_card=chapter_two_card,
        character_packet={},
    )
    assert "future:ch001:gate:knowledge" in next_bundle["dependency_claim_ids"]
    assert any(
        edge["to_claim_id"] == "future:ch001:gate:knowledge"
        and edge["field"] == "approved_future_knowledge_update"
        for edge in next_bundle["dependency_closure"]
    )
    pin_path = root / "30_state/future_knowledge_provenance_pins.json"
    pin_registry = json.loads(pin_path.read_text(encoding="utf-8"))
    assert pin_registry["schema"] == "future_knowledge_provenance_pins_v1"
    assert len(pin_registry["pins"]) == 1
    pinned_kinds = {item["kind"] for item in pin_registry["pins"][0]["evidence"]}
    assert pinned_kinds == {
        "workflow",
        "fanfiction_context_bundle",
        "narrative_event_ledger",
        "final_chapter",
        "semantic_ledger",
        "task_manifest",
        "task_instruction",
        "candidate",
    }

    manifest_path = root / load_manifest(root, task.task_id)["manifest_file"]
    approved_path = semantic_task_target(
        root,
        "fanfiction_future_knowledge_reassessment",
        json.loads(candidate_path.read_text(encoding="utf-8")),
    )

    def assert_stale_after_json_tamper(path: Path, mutate) -> None:
        original = path.read_bytes()
        changed = json.loads(original.decode("utf-8"))
        replacement = mutate(changed)
        write_json(path, replacement if isinstance(replacement, dict) else changed)
        with pytest.raises(FanfictionContextError, match="future_knowledge_document_stale"):
            compile_fanfiction_context(
                project["config"],
                chapter_number=2,
                chapter_contract=chapter_two,
                chapter_card=chapter_two_card,
                character_packet={},
            )
        path.write_bytes(original)

    assert_stale_after_json_tamper(
        event_path,
        lambda value: value["realized_major_divergences"][0]["evidence"].update(
            {"excerpt": "据显"}
        ),
    )
    assert_stale_after_json_tamper(
        event_path,
        lambda value: value["realized_major_divergences"][0][
            "human_confirmation"
        ].update({"reason": "同一稳定 ID 下被替换的确认理由。"}),
    )
    assert_stale_after_json_tamper(
        event_path,
        lambda value: value["realized_major_divergences"][0].update(
            {"knowledge_scope_refs": []}
        ),
    )
    assert_stale_after_json_tamper(
        workflow_path,
        lambda value: value["inputs"][0].update({"sha256": "f" * 64}),
    )
    assert_stale_after_json_tamper(
        manifest_path,
        lambda value: value["scope"].update({"chapter_number": 2}),
    )

    def reseal_changed_candidate(value: dict) -> dict:
        value["body"] = "候选正文被替换，即使 trigger_id 不变。"
        return seal_semantic_document(value)

    assert_stale_after_json_tamper(
        candidate_path,
        reseal_changed_candidate,
    )

    def reapprove_changed_document(value: dict) -> dict:
        value["body"] = "批准投影不再等于被验证候选。"
        return reapprove(value)

    assert_stale_after_json_tamper(
        approved_path,
        reapprove_changed_document,
    )
    assert_stale_after_json_tamper(
        pin_path,
        lambda value: value["pins"][0]["evidence"][0].update(
            {"sha256": "0" * 64}
        ),
    )


def test_future_knowledge_target_identity_is_owned_by_stable_trigger(tmp_path):
    shared_artifact = {
        "artifact_id": "agent-chosen-shared-name",
        "scope": {"kind": "chapter", "chapter_number": 1},
    }
    first = {
        "artifact": shared_artifact,
        "extensions": {"trigger_id": "divergence:stable:first"},
    }
    second = {
        "artifact": shared_artifact,
        "extensions": {"trigger_id": "divergence:stable:second"},
    }
    workflow_dir = tmp_path / "50_workbench/fanfiction_knowledge_impacts"
    for index, trigger_id in enumerate(
        ("divergence:stable:first", "divergence:stable:second"), start=1
    ):
        write_json(
            workflow_dir / f"ch001.{index}.workflow.json",
            {
                "workflow_kind": "fanfiction_future_knowledge_impact",
                "extensions": {
                    "trigger": {"trigger_id": trigger_id, "realized_chapter": 1}
                },
            },
        )

    first_target = semantic_task_target(
        tmp_path, "fanfiction_future_knowledge_reassessment", first
    )
    second_target = semantic_task_target(
        tmp_path, "fanfiction_future_knowledge_reassessment", second
    )

    assert first_target != second_target
    assert "agent-chosen-shared-name" not in first_target.name
    assert first_target.name.startswith("ch001.")


def test_legal_planning_apply_and_continue_write_preserve_all_formal_claim_channels(
    current_contract_project,
    tmp_path,
    monkeypatch,
):
    source_project = current_contract_project
    configure_crossover_project(source_project)
    install_route(source_project)

    template = load_project_config(template="qidian-longform")
    template.data["creation"]["mode"] = "fanfiction"
    template.data["fanfiction"] = deepcopy(source_project["config"].data["fanfiction"])
    for source in template.data["fanfiction"]["sources"]:
        source.update(
            {
                "rights_status": "unverified",
                "commercial_intent": False,
                "platform_policy_url": "",
            }
        )
    initialized = init_project(template, output=tmp_path / "production")
    config = load_project_config(initialized.project_config)
    root = initialized.root
    open_book(config)
    mark_project_ready(root, config)

    canon = deepcopy(source_project["canon"])
    canon_path = root / "10_bible/fanfiction/source_canon.json"
    canon_sha = write_document(canon_path, canon)
    project = {
        **source_project,
        "config": config,
        "root": root,
        "canon": canon,
        "canon_path": canon_path,
        "canon_sha": canon_sha,
    }
    install_route(project)

    bundle = planning_bundle()
    active_refs = ["route:entry"]
    obligation_refs = ["route:knowledge", "route:event"]
    plot_refs = ["route:guest_adapter", "route:constitution", "route:divergence"]
    bundle["active_volume_plan"]["fanfiction_projection"] = {
        "body": "本卷从正式切入点推进已批准的跨界规则。",
        "claim_refs": active_refs,
    }
    bundle["semantic_obligations"][0]["subject_refs"] = []
    bundle["semantic_obligations"][0]["prior_state_refs"] = []
    bundle["semantic_obligations"][0]["preconditions"] = []
    bundle["semantic_obligations"][0]["fanfiction_claim_refs"] = obligation_refs
    for table in bundle["plot_node_tables"]:
        for node in table["nodes"]:
            node["fanfiction_claim_refs"] = (
                plot_refs
                if table["chapter_number"] == 1 and node["node_kind"] == "state_change"
                else []
            )
    for contract in bundle["chapter_contracts"]:
        chapter_plot_refs = plot_refs if contract["chapter_number"] == 1 else []
        channel = contract["fanfiction_claim_refs"]
        channel["active_volume_claim_refs"] = active_refs
        channel["semantic_obligation_claim_refs"] = obligation_refs
        channel["plot_node_claim_refs"] = chapter_plot_refs
        channel["all_claim_refs"] = [
            *active_refs,
            *obligation_refs,
            *chapter_plot_refs,
        ]

    planning_dir = root / "50_workbench/planning"
    bundle_path = write_json(planning_dir / "bundle.json", bundle)
    review_path = write_json(
        planning_dir / "review.json",
        evidence_review(bundle_path.relative_to(root).as_posix()),
    )
    application_path = write_json(
        planning_dir / "application.json",
        build_planning_semantic_application(
            root,
            subject_path=bundle_path,
            profile="architecture",
            author_task_id="task:author",
            author_role_id="story_architect",
            reviewer_task_id="task:reviewer",
            reviewer_role_id="continuity_reviewer",
            reviewer_version="v1",
            review_result_path=review_path,
        ),
    )
    approval_path = write_json(
        planning_dir / "approval.json",
        build_human_planning_approval(
            root,
            application_path=application_path,
            decision="approve",
            reason="正式同人 claim 通道与章节因果均已人工核对。",
            approved_by="human",
        ),
    )
    decisions_path = write_json(
        planning_dir / "node-decisions.json",
        build_human_node_decisions(
            root,
            bundle_path=bundle_path,
            decisions=[
                {
                    "node_id": node["node_id"],
                    "decision": "approve",
                    "adjustment": "",
                    "reason": "保留该状态变化及其正式 claim 引用。",
                }
                for table in bundle["plot_node_tables"]
                for node in table["nodes"]
                if node["node_kind"] == "state_change"
            ],
            decided_by="human",
        ),
    )
    apply_planning_bundle(
        config,
        bundle_path=bundle_path,
        application_path=application_path,
        approval_path=approval_path,
        node_decisions_path=decisions_path,
        approved_by="human",
    )
    intent_task = create_human_chapter_intent_task(config, chapter_number=1)
    intent_candidate_path = root / intent_task.candidate_file
    intent = json.loads(intent_candidate_path.read_text(encoding="utf-8"))
    intent.update(
        {
            "story_intent": "让访客在规则压力下作出不可回退的本章选择。",
            "key_character_choice": "人物主动接受眼前代价并保留下一步拒绝权。",
            "emotional_truth": "信任来自承担后果，而不是来自无条件服从。",
            "pov_voice_intent": "视角保持克制，以动作和判断呈现内在压力。",
            "protected_items": ["不得消除本章选择造成的长期代价"],
            "completed_by": "human",
        }
    )
    write_json(intent_candidate_path, intent)
    intent_validation = validate_human_chapter_intent(
        config,
        chapter_number=1,
        file_path=intent_candidate_path,
    )
    assert intent_validation.ok, intent_validation.errors
    apply_human_chapter_intent(
        config,
        chapter_number=1,
        file_path=intent_candidate_path,
        approved_by="human",
    )

    continue_write(config, chapter_number=1)

    context = json.loads(
        (root / "50_workbench/fanfiction_context/ch001.json").read_text(encoding="utf-8")
    )
    expected = {
        "route:entry",
        "route:knowledge",
        "route:event",
        "route:guest_adapter",
        "route:constitution",
        "route:divergence",
    }
    assert expected <= set(context["required_claim_ids"])
    assert context["chapter_provenance"]["chapter_contract_path"] == (
        "20_outline/chapter_contracts/ch001.json"
    )
    assert context["chapter_provenance"]["chapter_card_path"] == (
        "20_outline/chapter_cards/ch001.json"
    )
    task = json.loads(
        (root / "50_workbench/writing_tasks/ch001.json").read_text(encoding="utf-8")
    )
    basis = json.loads(
        (root / task["story_brief_basis"]["path"]).read_text(encoding="utf-8")
    )
    bundle_binding = next(
        item
        for item in basis["source_files"]
        if item["path"] == "50_workbench/fanfiction_context/ch001.json"
    )
    assert bundle_binding["sha256"] == sha256(
        (root / bundle_binding["path"]).read_bytes()
    ).hexdigest()
    # Author-facing prose intentionally hides stable IDs; the writing task proves that it
    # consumed the exact selected bundle through the immutable story-brief basis instead.
    assert task["story_brief"]["fanfiction_context"] == context["author_projection"]

    draft_path = root / "50_workbench/agent_drafts/ch001.codex.md"
    sentence = (
        "CHAIN_E2E the visitor verifies the rule, accepts a visible cost, and leaves "
        "one unresolved consequence before the next choice? "
    )
    draft_path.write_text("# Chapter\n\n" + sentence * 36 + "\n", encoding="utf-8")
    submitted = submit_agent_draft(
        config,
        chapter_number=1,
        file_path=draft_path,
        agent="codex",
    )
    assert submitted.passed is True
    approve_story_candidate(root, config, chapter_number=1)
    finalized = finalize_chapter(config, chapter_number=1, approved_by="human")
    final_path = Path(finalized.final_file)
    semantic_candidate = prepare_unified_semantic_bundle(root, config, 1)
    semantic_apply(config, chapter_number=1, file_path=semantic_candidate)

    event_path = root / "30_state/narrative_events/ch001.json"
    event_ledger = json.loads(event_path.read_text(encoding="utf-8"))
    planned_events = [
        item
        for item in event_ledger["events"]
        if item.get("state") not in {"realized", "deferred", "cancelled"}
    ]
    source_event = next(
        item
        for item in planned_events
        if "route:divergence" in item.get("fanfiction_claim_refs", [])
    )
    final_text = final_path.read_text(encoding="utf-8")
    start = next(index for index, character in enumerate(final_text) if not character.isspace())
    end = min(len(final_text), start + 24)
    evidence = {"start": start, "end": end, "excerpt": final_text[start:end]}
    realization = build_event_realization_application(
        config,
        chapter_number=1,
        observations=[
            {
                "event_id": item["event_id"],
                "state": "realized",
                "evidence": evidence,
                "semantic_reason": "终稿已展示正式规划的状态变化。",
            }
            for item in planned_events
        ],
        discovered_causal_nodes=[],
        confirmed_by="human",
        realized_major_divergences=[
            {
                "declaration_id": f"declaration:planned:{index}",
                "source_event_id": source_event["event_id"],
                "source_claim_id": "route:divergence",
                "realized_chapter": 1,
                "impact_level": "major",
                "knowledge_scope_refs": ["route:knowledge"],
                "human_confirmation": {
                    "confirmed_by": "human",
                    "reason": f"人工确认第 {index} 个正式分歧声明已在终稿实现。",
                },
                "evidence": evidence,
            }
            for index in range(1, 4)
        ],
    )
    assert validate_event_realization_application(config, realization).ok
    realization_path = write_json(
        root / "50_workbench/event_realizations/ch001.real.json", realization
    )
    apply_event_realization(config, application_path=realization_path)

    complete_unified_semantic_lifecycle(
        root,
        config,
        1,
        approved_by="human",
        close=False,
    )
    closure_path = root / "30_state/chapter_closures/ch001.json"
    workflow_dir = root / "50_workbench/fanfiction_knowledge_impacts"
    state_path = root / "30_state/novel_state.json"
    task_index_path = root / "50_workbench/agent_tasks/agent_task_index.json"
    task_events_path = root / "50_workbench/agent_tasks/events.jsonl"
    before_transaction = {
        path: path.read_bytes()
        for path in (state_path, task_index_path, task_events_path)
        if path.is_file()
    }
    from longform_engine.semantic import pipeline as semantic_pipeline

    real_atomic_write = semantic_pipeline.atomic_write_text
    workflow_writes = 0

    def fail_second_workflow(path: Path, text: str) -> None:
        nonlocal workflow_writes
        if path.suffixes[-2:] == [".workflow", ".json"]:
            workflow_writes += 1
            if workflow_writes == 2:
                raise RuntimeError("injected second workflow failure")
        real_atomic_write(path, text)

    with monkeypatch.context() as fault:
        fault.setattr(semantic_pipeline, "atomic_write_text", fail_second_workflow)
        with pytest.raises(RuntimeError, match="injected second workflow failure"):
            chapter_close(config, chapter_number=1, approved_by="human")
    assert not closure_path.exists()
    assert not list(workflow_dir.glob("*.workflow.json"))
    assert all(path.read_bytes() == content for path, content in before_transaction.items())

    chapter_close(config, chapter_number=1, approved_by="human")
    closure = json.loads(closure_path.read_text(encoding="utf-8"))
    workflows = closure["future_knowledge_workflows"]
    assert len(workflows) == 3
    assert len({item["path"] for item in workflows}) == 3
    assert len({item["task_id"] for item in workflows}) == 3
    repeated_close = chapter_close(config, chapter_number=1, approved_by="human")
    assert repeated_close.closure_file == str(closure_path)

    target_paths: list[Path] = []
    reliability_states = ["仍可靠", "部分可靠", "反向误导"]
    knowledge_ranges = [
        {"from_chapter": 5, "to_chapter": None, "scope_refs": ["route:knowledge"]},
        {"from_chapter": 2, "to_chapter": 3, "scope_refs": ["route:knowledge"]},
        {"from_chapter": 2, "to_chapter": None, "scope_refs": ["route:knowledge"]},
    ]
    for index, (workflow_binding, reliability) in enumerate(
        zip(workflows, reliability_states, strict=True),
        start=1,
    ):
        workflow_path = root / workflow_binding["path"]
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        trigger = workflow["extensions"]["trigger"]
        manifest = load_manifest(root, workflow_binding["task_id"])
        candidate_path = root / str(manifest_output(manifest)["path"])
        input_hashes = {item["kind"]: item["sha256"] for item in workflow["inputs"]}
        candidate = seal_semantic_document(
            build_semantic_document(
                document_id="sem:future_knowledge:shared_agent_name",
                document_type="同人未来知识重估",
                title="重大分歧后的知识可靠性候选",
                scope={"kind": "chapter", "chapter_number": 1},
                continuity="批准分歧后的知识边界",
                body="逐项评估当前触发绑定的知识范围。",
                claims=[
                    semantic_claim(
                        f"future:real_chain:{index}:knowledge",
                        "未来知识可靠性",
                        extensions={
                            "trigger_id": trigger["trigger_id"],
                            "trigger_sha256": sha256(
                                json.dumps(
                                    trigger,
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest(),
                            "input_hashes": input_hashes,
                            "knowledge_claim_id": "route:knowledge",
                            "knowledge_range": knowledge_ranges[index - 1],
                            "reliability": reliability,
                            "depends_on_claims": [
                                "route:knowledge",
                                "route:divergence",
                            ],
                        },
                    )
                ],
                extensions={
                    "task_type": "fanfiction_future_knowledge_reassessment",
                    "trigger_id": trigger["trigger_id"],
                },
            )
        )
        write_document(candidate_path, candidate)
        submission = validate_production_agent_result(
            root,
            manifest,
            result_file=candidate_path,
        )
        assert submission.ok, submission.normalization.errors
        validation = validate_intelligence_candidate(
            config,
            task_type="fanfiction_future_knowledge_reassessment",
            file_path=candidate_path,
        )
        assert validation.ok, validation.errors
        applied = apply_intelligence_candidate(
            config,
            task_type="fanfiction_future_knowledge_reassessment",
            file_path=candidate_path,
            approved_by="human",
        )
        assert applied.status == "applied"
        repeated = apply_intelligence_candidate(
            config,
            task_type="fanfiction_future_knowledge_reassessment",
            file_path=candidate_path,
            approved_by="human",
        )
        assert repeated.transaction_report == ""
        target_paths.append(
            semantic_task_target(
                root,
                "fanfiction_future_knowledge_reassessment",
                candidate,
            )
        )

    assert len(set(target_paths)) == 3
    assert all(path.is_file() for path in target_paths)
    chapter_two = json.loads(
        (root / "20_outline/chapter_contracts/ch002.json").read_text(encoding="utf-8")
    )
    chapter_two.pop("chapter_contract_hash", None)
    next_context = compile_fanfiction_context(
        config,
        chapter_number=2,
        chapter_contract=chapter_two,
        chapter_card=json.loads(
            (root / "20_outline/chapter_cards/ch002.json").read_text(encoding="utf-8")
        ),
        character_packet={},
    )
    delayed_update = "future:real_chain:1:knowledge"
    finite_update = "future:real_chain:2:knowledge"
    indefinite_update = "future:real_chain:3:knowledge"
    assert delayed_update not in next_context["dependency_claim_ids"]
    assert {finite_update, indefinite_update} <= set(
        next_context["dependency_claim_ids"]
    )

    pin_path = root / "30_state/future_knowledge_provenance_pins.json"
    original_pin_registry = pin_path.read_bytes()
    pin_path.unlink()
    missing_pin_snapshot = _project_bytes(root)
    with pytest.raises(ValueError, match="provenance pin registry is missing"):
        compact_artifacts(config, through=1, dry_run=False)
    assert _project_bytes(root) == missing_pin_snapshot
    pin_path.write_bytes(original_pin_registry)
    invalid_pin_registry = json.loads(original_pin_registry.decode("utf-8"))
    invalid_pin_registry["schema"] = "future_knowledge_provenance_pins_invalid"
    write_json(pin_path, invalid_pin_registry)
    invalid_pin_snapshot = _project_bytes(root)
    with pytest.raises(ValueError, match="pin registry schema is invalid"):
        compact_artifacts(config, through=1, dry_run=False)
    assert _project_bytes(root) == invalid_pin_snapshot
    pin_path.write_bytes(original_pin_registry)

    initial_pin_registry = json.loads(original_pin_registry.decode("utf-8"))
    initial_archives = {
        pin["trigger_id"]: (
            root / pin["provenance_archive"]["path"]
        ).read_bytes()
        for pin in initial_pin_registry["pins"]
    }
    for pin in initial_pin_registry["pins"]:
        archive_path = root / pin["provenance_archive"]["path"]
        with zipfile.ZipFile(archive_path, "r") as handle:
            audit = json.loads(handle.read("_audit/manifest.json"))
            assert audit["pin"] == {
                key: pin[key]
                for key in (
                    "trigger_id",
                    "task_id",
                    "chapter_number",
                    "from_chapter",
                    "to_chapter",
                    "approved_document",
                    "evidence",
                )
            }
            assert audit["task_projection"]["task_id"] == pin["task_id"]
            assert audit["task_projection"]["status"] == "applied"
            assert {
                f"_audit/blobs/{pin['approved_document']['sha256']}",
                *(f"_audit/blobs/{item['sha256']}" for item in pin["evidence"]),
            } <= set(handle.namelist())

    unrelated = root / "50_workbench/unrelated/ch001.unrelated.json"
    write_json(unrelated, {"schema": "unrelated_test_artifact_v1", "chapter_number": 1})

    def close_followup_chapter(chapter_number: int) -> None:
        intent_task = create_human_chapter_intent_task(
            config,
            chapter_number=chapter_number,
        )
        intent_path = root / intent_task.candidate_file
        intent_payload = json.loads(intent_path.read_text(encoding="utf-8"))
        intent_payload.update(
            {
                "story_intent": "让既有后果推动新的不可回退选择。",
                "key_character_choice": "人物主动承担信息失效后的行动代价。",
                "emotional_truth": "选择的价值来自持续承担，而非即时奖励。",
                "pov_voice_intent": "视角以判断和动作呈现压力。",
                "protected_items": ["不得重置前章代价"],
                "completed_by": "human",
            }
        )
        write_json(intent_path, intent_payload)
        intent_result = validate_human_chapter_intent(
            config,
            chapter_number=chapter_number,
            file_path=intent_path,
        )
        assert intent_result.ok, intent_result.errors
        apply_human_chapter_intent(
            config,
            chapter_number=chapter_number,
            file_path=intent_path,
            approved_by="human",
        )
        continue_write(config, chapter_number=chapter_number)
        generated_context = json.loads(
            (
                root
                / "50_workbench"
                / "fanfiction_context"
                / f"ch{chapter_number:03d}.json"
            ).read_text(encoding="utf-8")
        )
        assert delayed_update not in generated_context["dependency_claim_ids"]
        assert {finite_update, indefinite_update} <= set(
            generated_context["dependency_claim_ids"]
        )
        followup_draft = (
            root
            / "50_workbench"
            / "agent_drafts"
            / f"ch{chapter_number:03d}.codex.md"
        )
        followup_sentence = (
            f"FOLLOWUP_{chapter_number} the character tests a changed premise, accepts "
            "a visible consequence, and preserves one unresolved choice? "
        )
        followup_draft.write_text(
            "# Chapter\n\n" + followup_sentence * 36 + "\n",
            encoding="utf-8",
        )
        assert submit_agent_draft(
            config,
            chapter_number=chapter_number,
            file_path=followup_draft,
            agent="codex",
        ).passed
        approve_story_candidate(root, config, chapter_number=chapter_number)
        finalized = finalize_chapter(
            config,
            chapter_number=chapter_number,
            approved_by="human",
        )
        followup_final = Path(finalized.final_file)
        semantic = prepare_unified_semantic_bundle(root, config, chapter_number)
        semantic_apply(config, chapter_number=chapter_number, file_path=semantic)
        followup_event_path = (
            root / "30_state/narrative_events" / f"ch{chapter_number:03d}.json"
        )
        followup_events = json.loads(
            followup_event_path.read_text(encoding="utf-8")
        )
        pending = [
            item
            for item in followup_events["events"]
            if item.get("state") not in {"realized", "deferred", "cancelled"}
        ]
        final_text = followup_final.read_text(encoding="utf-8")
        start = next(
            index for index, character in enumerate(final_text) if not character.isspace()
        )
        end = min(len(final_text), start + 24)
        followup_evidence = {
            "start": start,
            "end": end,
            "excerpt": final_text[start:end],
        }
        followup_realization = build_event_realization_application(
            config,
            chapter_number=chapter_number,
            observations=[
                {
                    "event_id": item["event_id"],
                    "state": "realized",
                    "evidence": followup_evidence,
                    "semantic_reason": "终稿已实现正式规划节点。",
                }
                for item in pending
            ],
            discovered_causal_nodes=[],
            confirmed_by="human",
            realized_major_divergences=[],
        )
        assert validate_event_realization_application(
            config, followup_realization
        ).ok
        followup_realization_path = write_json(
            root
            / "50_workbench/event_realizations"
            / f"ch{chapter_number:03d}.real.json",
            followup_realization,
        )
        apply_event_realization(
            config,
            application_path=followup_realization_path,
        )
        complete_unified_semantic_lifecycle(
            root,
            config,
            chapter_number,
            approved_by="human",
            close=False,
        )
        chapter_close(config, chapter_number=chapter_number, approved_by="human")

    close_followup_chapter(2)
    close_followup_chapter(3)

    assert not unrelated.exists()
    assert (root / "70_runtime/artifacts/chapters/ch001.zip").is_file()
    pin_registry = json.loads(pin_path.read_text(encoding="utf-8"))
    assert len(pin_registry["pins"]) == 3
    retained_pins = [pin for pin in pin_registry["pins"] if pin["to_chapter"] is None]
    expired_pin = next(pin for pin in pin_registry["pins"] if pin["to_chapter"] == 3)
    for pin in retained_pins:
        assert all((root / item["path"]).is_file() for item in pin["evidence"])
        assert (root / pin["approved_document"]["path"]).is_file()
    for item in expired_pin["evidence"]:
        if item["kind"] in {
            "workflow",
            "task_manifest",
            "task_instruction",
            "candidate",
        }:
            assert not (root / item["path"]).exists()
    for pin in pin_registry["pins"]:
        archive_path = root / pin["provenance_archive"]["path"]
        assert archive_path.read_bytes() == initial_archives[pin["trigger_id"]]
    active_task_ids = {
        item["task_id"]
        for item in json.loads(
            (root / "50_workbench/agent_tasks/agent_task_index.json").read_text(
                encoding="utf-8"
            )
        )["tasks"]
    }
    assert {pin["task_id"] for pin in retained_pins} <= active_task_ids
    assert expired_pin["task_id"] not in active_task_ids

    chapter_four = deepcopy(chapter_two)
    chapter_four["contract_id"] = "contract:ch004"
    chapter_four["chapter_number"] = 4
    chapter_four_card = {"chapter_number": 4, "volume_id": "volume:001"}
    _persist_compile_inputs(root, chapter_four, chapter_four_card)
    chapter_four_context = compile_fanfiction_context(
        config,
        chapter_number=4,
        chapter_contract=chapter_four,
        chapter_card=chapter_four_card,
        character_packet={},
    )
    assert delayed_update not in chapter_four_context["dependency_claim_ids"]
    assert finite_update not in chapter_four_context["dependency_claim_ids"]
    assert indefinite_update in chapter_four_context["dependency_claim_ids"]

    chapter_five = deepcopy(chapter_two)
    chapter_five["contract_id"] = "contract:ch005"
    chapter_five["chapter_number"] = 5
    chapter_five_card = {"chapter_number": 5, "volume_id": "volume:001"}
    _persist_compile_inputs(root, chapter_five, chapter_five_card)
    chapter_five_context = compile_fanfiction_context(
        config,
        chapter_number=5,
        chapter_contract=chapter_five,
        chapter_card=chapter_five_card,
        character_packet={},
    )
    assert {delayed_update, indefinite_update} <= set(
        chapter_five_context["dependency_claim_ids"]
    )
    assert finite_update not in chapter_five_context["dependency_claim_ids"]

    expired_archive = root / expired_pin["provenance_archive"]["path"]
    expired_archive_bytes = expired_archive.read_bytes()
    expired_archive.write_bytes(expired_archive_bytes + b"tamper")
    with pytest.raises(FanfictionContextError, match="provenance_pin"):
        compile_fanfiction_context(
            config,
            chapter_number=4,
            chapter_contract=chapter_four,
            chapter_card=chapter_four_card,
            character_packet={},
        )
    expired_archive.write_bytes(expired_archive_bytes)

    original_pins = pin_path.read_bytes()
    tampered_pins = json.loads(original_pins.decode("utf-8"))
    tampered_pins["pins"][0]["evidence"][0]["sha256"] = "f" * 64
    write_json(pin_path, tampered_pins)
    with pytest.raises(FanfictionContextError, match="provenance_pin"):
        compile_fanfiction_context(
            config,
            chapter_number=4,
            chapter_contract=chapter_four,
            chapter_card=chapter_four_card,
            character_packet={},
        )
    pin_path.write_bytes(original_pins)
