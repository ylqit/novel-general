import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from longform_engine import fanfiction_context as context_module
from longform_engine.agent_tasks import agent_task_events_file, agent_task_index_file
from longform_engine.config import ConfigDocument
from longform_engine.editorial.pipeline import (
    build_editorial_context_payload,
    editorial_review,
    editorial_role_source_inputs,
)
from longform_engine.fanfiction_context import (
    FANFICTION_CONTEXT_BUNDLE_SCHEMA,
    FanfictionContextError,
    compile_fanfiction_context,
    fanfiction_context_status,
    write_fanfiction_context_bundle,
)
from longform_engine.gates.pipeline import (
    GateError,
    build_semantic_review_context,
    semantic_review_task,
)
from longform_engine.narrative_events import (
    apply_event_realization,
    build_event_realization_application,
)
from longform_engine.semantic_protocols import seal_semantic_document
from longform_engine.semantic import chapter_close
from longform_engine.semantic import pipeline as semantic_pipeline
from longform_engine.storage.layout import manuscript_chapter_path
from tests.test_fanfiction_contracts import (
    configure_crossover_project,
    install_route,
    reapprove,
    reviewed_route_projection_sha256,
    semantic_claim,
    write_document,
)

pytest_plugins = ("tests.test_fanfiction_contracts",)


def _candidate_route(route: dict) -> dict:
    candidate = deepcopy(route)
    candidate["artifact"]["state"] = "candidate"
    candidate["extensions"].pop("human_decision", None)
    candidate["extensions"].pop("approved_candidate_sha256", None)
    candidate["extensions"].pop("independent_review", None)
    return seal_semantic_document(candidate)


def _rebind_route(project: dict, route: dict) -> dict:
    root = project["root"]
    review_binding = route["extensions"]["independent_review"]
    review_path = root / review_binding["review_path"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    target_path = root / review["extensions"]["review_target_path"]
    candidate = _candidate_route(route)
    target_sha = write_document(target_path, candidate)
    review["extensions"].update(
        {
            "review_target_sha256": target_sha,
            "source_canon_sha256": route["extensions"]["source_canon_sha256"],
            "story_engine_sha256": route["extensions"]["story_engine_sha256"],
        }
    )
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
    route = reapprove(route)
    write_document(root / "10_bible/fanfiction/fanfiction_bible.json", route)
    return route


def _add_route_claims(project: dict, claims: list[dict]) -> dict:
    route, _path = install_route(project)
    route = deepcopy(route)
    route["claims"].extend(claims)
    return _rebind_route(project, route)


def _chapter_contract(*, claim_refs: list[str]) -> dict:
    channel = {
        "schema": "fanfiction_chapter_claim_channel_v1",
        "active_volume_claim_refs": [],
        "semantic_obligation_claim_refs": [],
        "plot_node_claim_refs": [],
        "chapter_claim_refs": list(claim_refs),
        "all_claim_refs": list(claim_refs),
    }
    return {
        "schema": "chapter_contract_v5",
        "contract_id": "contract:ch001",
        "chapter_number": 1,
        "forecast_ref": "forecast:ch001",
        "topology": "relationship",
        "chapter_duty": "让已批准规则迫使人物作出选择。",
        "observable_change": "选择改变下一章可采取的行动。",
        "reader_value": "规则、选择与代价形成因果。",
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
            "reason": "收益缩窄后续选择。",
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
        "fanfiction_claim_refs": channel,
    }


def _persist_inputs(
    project: dict, *, explicit: list[str] | None = None, card: dict | None = None
) -> tuple[dict, dict]:
    contract = _chapter_contract(claim_refs=list(explicit or []))
    chapter_card = card or {
        "title": "边界测试",
        "volume_id": "vol001",
        "arc_id": "arc001",
    }
    root = project["root"]
    from tests.project_fixtures import persist_current_planning_fixture

    persist_current_planning_fixture(root, contract, scope=chapter_card)
    return contract, chapter_card


def _compile(project: dict, *, explicit: list[str] | None = None, card: dict | None = None):
    contract, chapter_card = _persist_inputs(project, explicit=explicit, card=card)
    return compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract=contract,
        character_packet={},
    )


def _realized_trigger(
    trigger_id: str,
    source_claim_id: str,
    *,
    confirmed_by: str = "human",
) -> dict:
    return {
        "trigger_id": trigger_id,
        "source_event_id": f"event:{trigger_id}",
        "source_claim_id": source_claim_id,
        "realized_chapter": 1,
        "impact_level": "major",
        "knowledge_scope_refs": ["route:knowledge"],
        "human_confirmation": {
            "confirmed_by": confirmed_by,
            "reason": "已核对最终正文与语义证据。",
        },
        "evidence": {"start": 0, "end": 1, "excerpt": "证"},
        "final_path": "40_manuscript/final/ch001.md",
        "final_sha256": "1" * 64,
        "semantic_ledger_path": "30_state/semantic_ledger/ch001.json",
        "semantic_ledger_sha256": "2" * 64,
        "realization_application_sha256": "3" * 64,
    }


def _materialize_divergences(
    project: dict,
    source_claim_ids: list[str],
) -> tuple[Path, dict]:
    root = project["root"]
    final = manuscript_chapter_path(root, 1, lane="final")
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_text("证据显示人物承担选择的长期代价。\n", encoding="utf-8")
    semantic = root / "30_state/semantic_ledger/ch001.json"
    write_document(
        semantic,
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
    plot_path = root / "20_outline/plot_nodes/ch001.json"
    events = [
        {
            "schema": "narrative_event_v1",
            "event_id": f"event:ch001:{index}",
            "source_node_id": f"node:ch001:{index}",
            "chapter_number": 1,
            "preconditions": [],
            "dependency_refs": [],
            "fanfiction_claim_refs": [claim_id],
            "expected_changes": [{"domain": "relationship"}],
            "reader_effect": "选择改变后续行动边界。",
            "state": "planned_approved",
            "realization_evidence": None,
        }
        for index, claim_id in enumerate(source_claim_ids, start=1)
    ]
    event_path = root / "30_state/narrative_events/ch001.json"
    write_document(
        event_path,
        {
            "schema": "narrative_event_ledger_v1",
            "chapter_number": 1,
            "events": events,
            "realized_major_divergences": [],
            "source_plot_node_table_sha256": sha256(plot_path.read_bytes()).hexdigest(),
        },
    )
    evidence = {"start": 0, "end": 2, "excerpt": "证据"}
    application = build_event_realization_application(
        project["config"],
        chapter_number=1,
        observations=[
            {
                "event_id": event["event_id"],
                "state": "realized",
                "evidence": evidence,
                "semantic_reason": "终稿精确证据显示事件实现。",
            }
            for event in events
        ],
        discovered_causal_nodes=[],
        confirmed_by="human",
        realized_major_divergences=[
            {
                "declaration_id": f"declaration:ch001:{index}",
                "source_event_id": event["event_id"],
                "source_claim_id": claim_id,
                "realized_chapter": 1,
                "impact_level": "major",
                "knowledge_scope_refs": ["route:knowledge"],
                "human_confirmation": {
                    "confirmed_by": "human",
                    "reason": "人工核对终稿与语义证据。",
                },
                "evidence": evidence,
            }
            for index, (event, claim_id) in enumerate(
                zip(events, source_claim_ids, strict=True), start=1
            )
        ],
    )
    application_path = root / "50_workbench/event_realizations/ch001.legal.json"
    write_document(application_path, application)
    apply_event_realization(project["config"], application_path=application_path)
    return event_path, json.loads(event_path.read_text(encoding="utf-8"))


def test_context_v2_exposes_precedence_closure_partitions_and_exact_review_evidence(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)

    bundle = _compile(project, explicit=["route:event", "classic:canon"])

    assert FANFICTION_CONTEXT_BUNDLE_SCHEMA == "fanfiction_context_bundle_v3"
    assert bundle["schema"] == "fanfiction_context_bundle_v3"
    assert bundle["diagnostics"]["selection_precedence"] == [
        "global_non_negotiable",
        "chapter_explicit_refs",
        "recursive_dependency_closure",
        "current_structured_scope",
        "optional_rag",
    ]
    assert {"route:event", "classic:canon"} <= set(bundle["required_claim_ids"])
    edges = {
        (item["from_claim_id"], item["to_claim_id"], item["field"])
        for item in bundle["dependency_closure"]
    }
    assert {
        ("route:event", "route:divergence", "depends_on_claims"),
        ("route:event", "route:owner", "responsibility_owner_ids"),
        ("route:event", "route:first", "first_order_effect_claim_ids"),
        ("route:event", "route:second", "second_order_effect_claim_ids"),
    } <= edges
    assert set(bundle["source_partitions"]) >= {"source", "character", "event", "volume", "arc"}
    assert set(bundle["budget_usage"]) >= {
        "estimator",
        "units",
        "budget_units",
        "required",
        "dependency",
        "optional",
        "partitions",
    }
    reviewed = {item["claim_id"]: item for item in bundle["review_projection"]["claims"]}
    canon = reviewed["classic:canon"]
    assert canon["namespace"] == "source_canon"
    assert canon["source_id"] == "classic"
    assert canon["evidence_refs"] == ["evidence:classic"]
    assert canon["evidence_records"][0]["evidence_id"] == "evidence:classic"
    assert set(reviewed) == set(bundle["included_claim_ids"])


def test_legacy_v1_bundle_is_rejected_and_reported_missing(current_contract_project):
    project = current_contract_project
    install_route(project)
    path = project["root"] / "50_workbench/fanfiction_context/ch001.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"schema": "fanfiction_context_bundle_v1", "chapter_number": 1}),
        encoding="utf-8",
    )

    status = fanfiction_context_status(project["config"], chapter_number=1)

    assert status["status"] == "missing"
    assert status["found_schema"] == "fanfiction_context_bundle_v1"
    assert status["required_schema"] == "fanfiction_context_bundle_v3"


def test_malformed_v2_bundle_is_invalid_and_writer_refuses_it(current_contract_project):
    project = current_contract_project
    install_route(project)
    malformed = _compile(project)
    malformed.pop("review_projection")

    with pytest.raises(FanfictionContextError, match="review_projection"):
        write_fanfiction_context_bundle(project["root"], malformed)

    path = project["root"] / "50_workbench/fanfiction_context/ch001.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(malformed, ensure_ascii=False), encoding="utf-8")
    status = fanfiction_context_status(project["config"], chapter_number=1)
    assert status["status"] == "invalid"
    assert any("review_projection" in item for item in status["diagnostics"]["bundle_errors"])


def test_v1_bundle_blocks_review_task_creation_without_partial_artifacts(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)
    bundle_path = project["root"] / "50_workbench/fanfiction_context/ch001.json"
    bundle_path.parent.mkdir(parents=True)
    bundle_path.write_text(
        json.dumps({"schema": "fanfiction_context_bundle_v1", "chapter_number": 1}),
        encoding="utf-8",
    )
    draft = project["root"] / "40_manuscript/draft/ch001.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# 第一章\n\n角色作出选择。\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fanfiction_context_missing"):
        editorial_review(project["config"], chapter_number=1)
    with pytest.raises(GateError, match="fanfiction_context_missing"):
        semantic_review_task(project["config"], chapter_number=1)

    assert not (project["root"] / "50_workbench/editorial_reviews").exists()
    assert not (project["root"] / "50_workbench/gate_artifacts").exists()


def test_source_change_stales_bundle_and_blocks_review_tasks_before_writes(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)
    write_fanfiction_context_bundle(project["root"], _compile(project))
    draft = project["root"] / "40_manuscript/draft/ch001.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# 第一章\n\n角色作出选择。\n", encoding="utf-8")
    project["canon_path"].write_bytes(project["canon_path"].read_bytes() + b"\n")

    with pytest.raises(ValueError, match="stale"):
        editorial_review(project["config"], chapter_number=1)
    with pytest.raises(GateError, match="stale"):
        semantic_review_task(project["config"], chapter_number=1)

    assert not (project["root"] / "50_workbench/editorial_reviews").exists()
    assert not (project["root"] / "50_workbench/gate_artifacts").exists()


def test_explicit_ref_wins_without_name_or_keyword_match(current_contract_project):
    project = current_contract_project
    install_route(project)

    bundle = _compile(
        project,
        explicit=["route:knowledge"],
        card={"title": "完全不含任何人物或规则词", "volume_id": "vol001"},
    )

    assert "route:knowledge" in bundle["required_claim_ids"]
    assert "route:knowledge" in bundle["included_claim_ids"]
    assert bundle["selection_reasons"]["route:knowledge"] == ["chapter_explicit_ref"]


def test_required_overflow_reports_top_claims_and_writes_nothing(
    current_contract_project, monkeypatch
):
    project = current_contract_project
    install_route(project)
    _persist_inputs(project, explicit=["route:knowledge"])
    before = {
        path.relative_to(project["root"]).as_posix(): path.read_bytes()
        for path in project["root"].rglob("*")
        if path.is_file()
    }
    original = context_module._claim_units

    def huge_required(claim: dict, estimator: object) -> int:
        if claim["claim_id"] == "route:knowledge":
            return 100_000
        return original(claim, estimator)

    monkeypatch.setattr(context_module, "_claim_units", huge_required)

    with pytest.raises(FanfictionContextError) as exc_info:
        _compile(project, explicit=["route:knowledge"])

    message = str(exc_info.value)
    assert "prompt_budget_exceeded" in message
    assert "route:knowledge" in message
    assert "top_contributors" in message
    assert '"partitions":' in message
    assert "scope-reduction" in message
    after = {
        path.relative_to(project["root"]).as_posix(): path.read_bytes()
        for path in project["root"].rglob("*")
        if path.is_file()
    }
    assert after == before


def test_optional_claim_can_be_omitted_without_truncating_required(
    current_contract_project, monkeypatch
):
    project = current_contract_project
    install_route(project)
    original = context_module._claim_units
    monkeypatch.setattr(
        context_module,
        "_optional_project_canon_claims",
        lambda *args, **kwargs: (["classic:canon"], {"status": "completed"}),
    )

    def expensive_optional(claim: dict, estimator: object) -> int:
        if claim["claim_id"] == "classic:canon":
            return 100_000
        return original(claim, estimator)

    monkeypatch.setattr(context_module, "_claim_units", expensive_optional)

    bundle = _compile(project)

    assert "classic:canon" not in bundle["included_claim_ids"]
    assert bundle["optional_claim_ids"] == []
    assert bundle["omitted_claims"] == [
        {
            "claim_id": "classic:canon",
            "reason": "optional_budget_omitted",
            "required": False,
            "units": 100_000,
        }
    ]


def test_multi_source_collisions_force_source_labels_in_author_projection(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    identity_claims = []
    explicit = []
    for kind, display_name in (
        ("character", "镜"),
        ("ability", "回响"),
        ("location", "环城"),
        ("organization", "守望会"),
        ("energy", "星 能"),
    ):
        for source_id in ("classic", "guest"):
            claim_id = f"route:{kind}:{source_id}"
            explicit.append(claim_id)
            identity_claims.append(
                semantic_claim(
                    claim_id,
                    "跨来源身份",
                    extensions={
                        "identity": {
                            "identity_id": f"identity:{kind}:{source_id}",
                            "kind": kind,
                            "display_name": (
                                "星能"
                                if kind == "energy" and source_id == "guest"
                                else display_name
                            ),
                            "source_id": source_id,
                        },
                    },
                )
            )
    _add_route_claims(project, identity_claims)

    from longform_engine.fanfiction_contracts import crossover_required_topics
    route_path = project["root"] / "10_bible/fanfiction/fanfiction_bible.json"
    route = json.loads(route_path.read_text(encoding="utf-8"))
    payloads = ["ability", "character", "organization"]
    route["extensions"]["crossover"]["transfers"][0]["payload_kinds"] = payloads
    for claim in route["claims"]:
        if claim["extensions"].get("semantic_type") == "主世界适配器":
            claim["extensions"]["payload_kinds"] = payloads
            claim["extensions"]["depends_on_claims"] = ["route:constitution"]
            explicit.append(claim["claim_id"])
        elif claim["claim_id"] == "route:constitution":
            claim["extensions"]["topics"] = sorted(crossover_required_topics(route["extensions"]["crossover"]))
    _rebind_route(project, route)
    bundle = _compile(project, explicit=explicit)

    kinds = {item["kind"] for item in bundle["namespace_collisions"]}
    assert {"character", "ability", "location", "organization", "energy"} <= kinds
    rendered = json.dumps(bundle["author_projection"], ensure_ascii=False)
    assert "【classic】" in rendered
    assert "【guest】" in rendered


def test_canon_reviewer_uses_only_v2_review_projection_even_when_canon_is_large(
    current_contract_project, monkeypatch
):
    # This unit test isolates bounded Canon projection; current-basis validation is
    # exercised by the production loop and test_review_tasks_expire_when_author_design_changes.
    for module in ("longform_engine.editorial.pipeline", "longform_engine.gates.pipeline"):
        monkeypatch.setattr(module + ".load_current_story_brief_binding", lambda root, chapter: {
            "schema": "chapter_story_brief_binding_v3", "story_brief_basis_sha256": "e" * 64,
        })
        monkeypatch.setattr(module + ".require_current_human_chapter_intent", lambda root, chapter: {
            "payload": {"schema": "human_chapter_intent_v3", "story_intent": "核对原著边界。"},
        })
    project = current_contract_project
    route, _path = install_route(project)
    canon_path = project["canon_path"]
    raw = json.loads(canon_path.read_text(encoding="utf-8"))
    raw["body"] = "不进入章节审阅投影的体量填充。" * 2_000
    raw = reapprove(raw)
    canon_sha = write_document(canon_path, raw)
    engine_path = project["root"] / "10_bible/fanfiction/story_engine.json"
    engine = json.loads(engine_path.read_text(encoding="utf-8"))
    engine["extensions"]["source_canon_sha256"] = canon_sha
    engine = reapprove(engine)
    engine_sha = write_document(engine_path, engine)
    route["extensions"].update(
        {"source_canon_sha256": canon_sha, "story_engine_sha256": engine_sha}
    )
    _rebind_route(project, route)
    bundle = _compile(project, explicit=["classic:canon"])
    bundle_path = write_fanfiction_context_bundle(project["root"], bundle)
    chapter = project["root"] / "40_manuscript/draft/ch001.md"
    chapter.parent.mkdir(parents=True)
    chapter.write_text("# 第一章\n\n角色依据已批准边界作出选择。\n", encoding="utf-8")
    inputs = editorial_role_source_inputs(
        project["root"],
        {"chapter_number": 1, "source_path": "40_manuscript/draft/ch001.md"},
        "canon_fidelity_reviewer",
    )
    context = build_editorial_context_payload(
        project["root"],
        payload={"chapter_number": 1, "source_path": "40_manuscript/draft/ch001.md", "review_round": 1},
        role_id="canon_fidelity_reviewer",
        source_inputs=inputs,
    )

    assert inputs == [chapter, bundle_path]
    projection = context["source_projections"]["50_workbench/fanfiction_context/ch001.json"]
    assert projection == bundle["review_projection"]
    serialized = json.dumps(context, ensure_ascii=False)
    assert "不进入章节审阅投影" not in serialized
    assert "source_canon.json" not in context["provenance_source_files"]

    gate_context = build_semantic_review_context(
        project["root"],
        chapter_number=1,
        source_path=chapter,
        source_text=chapter.read_text(encoding="utf-8"),
        canonical_inputs=[bundle_path],
        fanfiction=True,
    )
    assert gate_context["sections"]["fanfiction"] == bundle["review_projection"]


def test_three_divergence_triggers_create_three_idempotent_hash_bound_workflows(
    current_contract_project,
):
    project = current_contract_project
    extra_claims = [
        semantic_claim("route:divergence_two", "初始分歧"),
        semantic_claim(
            "route:event_two",
            "原著事件命运",
            extensions={
                "disposition": "取消",
                "depends_on_claims": ["route:divergence_two"],
                "responsibility_owner_ids": ["route:owner"],
                "first_order_effect_claim_ids": ["route:first"],
                "second_order_effect_claim_ids": ["route:second"],
            },
        ),
    ]
    _add_route_claims(project, extra_claims)
    explicit = [
        "route:divergence",
        "route:event",
        "route:event_two",
        "route:knowledge",
    ]
    bundle = _compile(project, explicit=explicit)
    bundle_path = write_fanfiction_context_bundle(project["root"], bundle)
    event_path, events = _materialize_divergences(project, explicit[:3])

    workflows = context_module.future_knowledge_impact_workflows(
        project["config"],
        chapter_number=1,
        event_ledger=events,
        event_ledger_path=event_path,
    )

    assert len(workflows) == 3
    assert len({path.name for path, _payload in workflows}) == 3
    for path, payload in workflows:
        assert payload["extensions"]["allowed_reliability_states"] == [
            "仍可靠",
            "部分可靠",
            "已失效",
            "反向误导",
        ]
        assert payload["authorization"]["requires_human_decision"] is True
        assert payload["extensions"]["trigger"]["impact_level"] == "major"
        inputs = {item["kind"]: item for item in payload["inputs"]}
        assert inputs["fanfiction_context_bundle"]["sha256"] == sha256(bundle_path.read_bytes()).hexdigest()
        assert inputs["narrative_event_ledger"]["sha256"] == sha256(event_path.read_bytes()).hexdigest()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert context_module.future_knowledge_impact_workflows(
        project["config"],
        chapter_number=1,
        event_ledger=events,
        event_ledger_path=event_path,
    ) == ()


def test_future_knowledge_workflow_rejects_stale_source_bound_bundle(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)
    bundle = _compile(project, explicit=["route:divergence", "route:knowledge"])
    write_fanfiction_context_bundle(project["root"], bundle)
    event_path, events = _materialize_divergences(project, ["route:divergence"])
    project["canon_path"].write_bytes(project["canon_path"].read_bytes() + b"\n")

    with pytest.raises(FanfictionContextError, match="stale"):
        context_module.future_knowledge_impact_workflows(
            project["config"],
            chapter_number=1,
            event_ledger=events,
            event_ledger_path=event_path,
        )


def test_existing_trigger_workflow_detects_changed_event_binding(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)
    bundle = _compile(project, explicit=["route:divergence", "route:knowledge"])
    write_fanfiction_context_bundle(project["root"], bundle)
    event_path, events = _materialize_divergences(project, ["route:divergence"])
    workflow_path, payload = context_module.future_knowledge_impact_workflows(
        project["config"],
        chapter_number=1,
        event_ledger=events,
        event_ledger_path=event_path,
    )[0]
    workflow_path.parent.mkdir(parents=True)
    workflow_path.write_text(json.dumps(payload), encoding="utf-8")
    events["human_note"] = "事件账本已经由人修改。"
    event_path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FanfictionContextError, match="workflow_stale"):
        context_module.future_knowledge_impact_workflows(
            project["config"],
            chapter_number=1,
            event_ledger=events,
            event_ledger_path=event_path,
        )


def test_future_knowledge_trigger_requires_human_approved_event_ledger(
    current_contract_project,
):
    project = current_contract_project
    install_route(project)
    bundle = _compile(project, explicit=["route:divergence", "route:knowledge"])
    write_fanfiction_context_bundle(project["root"], bundle)
    event_path, events = _materialize_divergences(project, ["route:divergence"])
    events["realized_major_divergences"][0]["human_confirmation"]["confirmed_by"] = "model"
    event_path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(FanfictionContextError, match="human_confirmation"):
        context_module.future_knowledge_impact_workflows(
            project["config"],
            chapter_number=1,
            event_ledger=events,
            event_ledger_path=event_path,
        )


def test_future_knowledge_workflows_leave_original_mode_unchanged(tmp_path):
    root = tmp_path / "original"
    config = ConfigDocument(
        data={"creation": {"mode": "original"}, "project": {"root_dir": str(root)}},
        path=root / "project.yaml",
        sources=(),
    )

    assert context_module.future_knowledge_impact_workflows(
        config,
        chapter_number=1,
        event_ledger={"events": []},
        event_ledger_path=root / "missing.json",
    ) == ()


def _legacy_chapter_close_fixture_superseded_by_real_owner_chain_e2e(
    current_contract_project,
    monkeypatch,
):
    project = current_contract_project
    _add_route_claims(
        project,
        [
            semantic_claim("route:divergence_two", "初始分歧"),
            semantic_claim("route:divergence_three", "初始分歧"),
        ],
    )
    root = project["root"]
    claim_ids = [
        "route:divergence",
        "route:divergence_two",
        "route:divergence_three",
        "route:knowledge",
    ]
    write_fanfiction_context_bundle(root, _compile(project, explicit=claim_ids))
    final_file = manuscript_chapter_path(root, 1, lane="final")
    ledger_file = root / "30_state/semantic_ledger/ch001.json"
    event_file = root / "30_state/narrative_events/ch001.json"
    promise_file = root / "30_state/reader_promise_ledger.json"
    events = {
        "schema": "narrative_event_ledger_v1",
        "chapter_number": 1,
        "events": [],
        "realized_major_divergences": [
            _realized_trigger(f"divergence:ch001:{index}", claim_id)
            for index, claim_id in enumerate(claim_ids[:3], start=1)
        ],
        "realization_application_sha256": "3" * 64,
        "source_plot_node_table_sha256": "4" * 64,
    }
    for path, text in (
        (final_file, "# 第一章\n\n完成一个有代价的选择。\n"),
        (ledger_file, "{}\n"),
        (event_file, json.dumps(events, ensure_ascii=False, indent=2) + "\n"),
        (promise_file, "{}\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    gate = root / "50_workbench/gate_artifacts/ch001/gate_result.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(
        json.dumps({"passed": True, "severity_counts": {"P0": 0, "P1": 0}}),
        encoding="utf-8",
    )
    (root / "30_state/novel_state.json").parent.mkdir(parents=True, exist_ok=True)
    (root / "30_state/novel_state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(semantic_pipeline, "verify_materialized_chapter", lambda *args: None)
    monkeypatch.setattr(
        semantic_pipeline,
        "require_v010_close_evidence",
        lambda *_args: {
            "event_ledger_path": event_file.relative_to(root).as_posix(),
            "event_ledger_sha256": sha256(event_file.read_bytes()).hexdigest(),
            "reader_promise_ledger_path": promise_file.relative_to(root).as_posix(),
            "reader_promise_ledger_sha256": sha256(promise_file.read_bytes()).hexdigest(),
        },
    )
    monkeypatch.setattr(
        "longform_engine.author_voice.require_author_voice_pair_for_close",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        semantic_pipeline,
        "task_reconciliation_status",
        lambda *_args, **_kwargs: {"status": "ok"},
    )
    monkeypatch.setattr(
        semantic_pipeline,
        "live_chapter_tasks",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(semantic_pipeline, "compact_closed_artifacts", lambda *_args: ())

    first = chapter_close(project["config"], chapter_number=1, approved_by="human")
    closure = json.loads(Path(first.closure_file).read_text(encoding="utf-8"))

    assert len(closure["future_knowledge_workflows"]) == 3
    assert len({item["task_id"] for item in closure["future_knowledge_workflows"]}) == 3
    assert all((root / item["manifest_path"]).is_file() for item in closure["future_knowledge_workflows"])
    assert len(
        list(
            (root / "50_workbench/agent_tasks").glob(
                "fanfiction_future_knowledge_reassessment.ch001.*.manifest.json"
            )
        )
    ) == 3

    second = chapter_close(project["config"], chapter_number=1, approved_by="human")
    assert second.closure_file == first.closure_file
    assert len(
        list(
            (root / "50_workbench/agent_tasks").glob(
                "fanfiction_future_knowledge_reassessment.ch001.*.manifest.json"
            )
        )
    ) == 3


def _legacy_chapter_close_rollback_fixture_superseded_by_real_owner_chain_e2e(
    tmp_path, monkeypatch
):
    root = tmp_path / "project"
    config = ConfigDocument(
        data={"creation": {"mode": "fanfiction"}, "project": {"root_dir": str(root)}},
        path=root / "project.yaml",
        sources=(),
    )
    final_file = manuscript_chapter_path(root, 1, lane="final")
    ledger_file = root / "30_state/semantic_ledger/ch001.json"
    event_file = root / "30_state/narrative_events/ch001.json"
    promise_file = root / "30_state/reader_promise_ledger.json"
    context_file = root / "50_workbench/fanfiction_context/ch001.json"
    for path, text in (
        (final_file, "# 第一章\n\n完成选择。\n"),
        (ledger_file, "{}"),
        (event_file, json.dumps({"events": []})),
        (promise_file, "{}"),
        (context_file, json.dumps({"schema": "fanfiction_context_bundle_v3"})),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    gate = root / "50_workbench/gate_artifacts/ch001/gate_result.json"
    gate.parent.mkdir(parents=True)
    gate.write_text(
        json.dumps({"passed": True, "severity_counts": {"P0": 0, "P1": 0}}),
        encoding="utf-8",
    )
    state_file = root / "30_state/novel_state.json"
    cursor_file = root / "30_state/planning_cursor.json"
    state_file.write_text('{"status":"before"}', encoding="utf-8")
    cursor_file.write_text('{"cursor":"before"}', encoding="utf-8")
    state_before = state_file.read_bytes()
    cursor_before = cursor_file.read_bytes()
    workflow_paths = (
        root / "50_workbench/fanfiction_knowledge_impacts/ch001.a.workflow.json",
        root / "50_workbench/fanfiction_knowledge_impacts/ch001.b.workflow.json",
    )
    task_artifacts_by_digest: dict[str, dict] = {}
    monkeypatch.setattr(semantic_pipeline, "verify_materialized_chapter", lambda *args: None)
    monkeypatch.setattr(
        semantic_pipeline,
        "require_v010_close_evidence",
        lambda *_args: {
            "event_ledger_path": event_file.relative_to(root).as_posix(),
            "event_ledger_sha256": sha256(event_file.read_bytes()).hexdigest(),
            "reader_promise_ledger_path": promise_file.relative_to(root).as_posix(),
            "reader_promise_ledger_sha256": sha256(promise_file.read_bytes()).hexdigest(),
        },
    )
    monkeypatch.setattr(
        context_module,
        "future_knowledge_impact_workflows",
        lambda *args, **kwargs: tuple(
            (path, {"workflow_id": f"workflow:{index}"})
            for index, path in enumerate(workflow_paths, start=1)
        ),
    )
    def fake_task_artifacts(
        task_root: Path,
        *,
        chapter_number: int,
        workflow_sha256: str,
    ) -> dict:
        base = f"future.ch{chapter_number:03d}.{workflow_sha256[:12]}"
        artifacts = {
            "base": base,
            "task_id": f"future:{workflow_sha256[:12]}",
            "instruction": task_root / "50_workbench/intelligence_tasks" / f"{base}.md",
            "candidate": task_root / "50_workbench/intelligence_candidates" / f"{base}.json",
            "manifest": task_root / "50_workbench/agent_tasks" / f"{base}.manifest.json",
        }
        task_artifacts_by_digest[workflow_sha256] = artifacts
        return artifacts

    def fake_create_task(
        _config,
        *,
        task_type: str,
        input_files,
        chapter_number: int,
    ):
        workflow_digest = sha256(Path(input_files[0]).read_bytes()).hexdigest()
        artifacts = task_artifacts_by_digest[workflow_digest]
        for path in (artifacts["instruction"], artifacts["manifest"]):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("task", encoding="utf-8")
        for path in (
            agent_task_index_file(root),
            agent_task_events_file(root),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("task", encoding="utf-8")
        return SimpleNamespace(
            task_id=artifacts["task_id"],
            manifest_file=artifacts["manifest"].relative_to(root).as_posix(),
        )

    monkeypatch.setattr(
        "longform_engine.intelligence.future_knowledge_reassessment_task_artifacts",
        fake_task_artifacts,
    )
    monkeypatch.setattr(
        "longform_engine.intelligence.create_intelligence_task",
        fake_create_task,
    )
    monkeypatch.setattr(
        "longform_engine.author_voice.require_author_voice_pair_for_close",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        semantic_pipeline,
        "task_reconciliation_status",
        lambda *_args, **_kwargs: {"status": "ok"},
    )
    monkeypatch.setattr(
        semantic_pipeline,
        "live_chapter_tasks",
        lambda *_args, **_kwargs: [],
    )
    original_write = semantic_pipeline.atomic_write_text

    def fail_on_cursor(path: Path, text: str) -> None:
        if path == cursor_file:
            raise RuntimeError("injected chapter-close failure")
        original_write(path, text)

    monkeypatch.setattr(semantic_pipeline, "atomic_write_text", fail_on_cursor)

    with pytest.raises(RuntimeError, match="injected chapter-close failure"):
        chapter_close(config, chapter_number=1, approved_by="human")

    assert not (root / "30_state/chapter_closures/ch001.json").exists()
    assert all(not path.exists() for path in workflow_paths)
    assert all(
        not artifacts[key].exists()
        for artifacts in task_artifacts_by_digest.values()
        for key in ("instruction", "manifest")
    )
    assert not agent_task_index_file(root).exists()
    assert not agent_task_events_file(root).exists()
    assert state_file.read_bytes() == state_before
    assert cursor_file.read_bytes() == cursor_before


@pytest.mark.parametrize("topology", ["fixed_host", "fusion_world", "sequential_worlds"])
@pytest.mark.parametrize("fault", [None, "future_rule", "wrong_host", "missing_dependency", "missing_limit", "future_adapter"])
def test_chapter_transfer_requires_current_dependent_rules(topology, fault):
    from longform_engine.fanfiction_context import _claim_record, _dependency_closure, _validate_current_crossover_rules
    from tests.test_fanfiction_contracts import crossover_route

    route = crossover_route(topology=topology, default_host_source_id="host" if topology == "fixed_host" else None)
    adapter = route["claims"][0]
    rule = next(item for item in route["claims"] if item["claim_id"] == "route:constitution")
    adapter["extensions"]["depends_on_claims"] = ["route:constitution"]
    if topology == "fusion_world":
        adapter["extensions"]["depends_on_claims"].append("route:world_priority")
    if fault == "future_rule":
        rule["extensions"]["from_chapter"] = 2
    elif fault == "wrong_host":
        adapter["extensions"]["host_source_id"] = "wrong"
    elif fault == "missing_dependency":
        adapter["extensions"]["depends_on_claims"] = []
    elif fault == "missing_limit":
        rule["extensions"]["topics"].remove("限制")
    elif fault == "future_adapter":
        adapter["extensions"]["from_chapter"] = 2
    claims = {item["claim_id"]: _claim_record("route_design", item, route) for item in route["claims"]}
    explicit = {adapter["claim_id"]}
    closure, _ = _dependency_closure(explicit, claims)
    def validate():
        _validate_current_crossover_rules(route, claims, explicit, closure, chapter_number=1,
                                          planning_scope={"volume_id": "vol001"}, current_scope={"volume": {"vol001"}})
    if fault:
        with pytest.raises(FanfictionContextError, match="context_evidence_incomplete"):
            validate()
    else:
        validate()


@pytest.mark.parametrize("states,expected", [([], None), (["伤势尚未恢复"], "伤势尚未恢复"),
                                          (["伤势尚未恢复", "伤势已恢复，行动限制解除"], "伤势已恢复，行动限制解除")])
def test_cross_volume_facts_follow_final_evidence_and_latest_state(tmp_path, states, expected):
    from longform_engine.fanfiction_context import _claim_record, _cross_volume_continuity
    from tests.test_fanfiction_contracts import asymmetric_sequential_route

    route = asymmetric_sequential_route()
    claims = {item["claim_id"]: _claim_record("route_design", item, route) for item in route["claims"]}
    for chapter in (1, 2):
        value = states[chapter - 1] if len(states) >= chapter else "本章没有伤势变化。"
        final = manuscript_chapter_path(tmp_path, chapter, lane="final")
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_text(value, encoding="utf-8")
        write_document(tmp_path / f"30_state/semantic_ledger/ch{chapter:03d}.json", {
            "schema": "chapter_semantic_bundle_v1", "chapter_number": chapter, "canonical": True,
            "source": {"path": final.relative_to(tmp_path).as_posix(), "sha256": sha256(final.read_bytes()).hexdigest()},
            "world_deltas": ([{"fact_id": "route:carryover", "value": value,
                              "evidence": {"start": 0, "end": len(value), "excerpt": value}}] if len(states) >= chapter else []),
        })
    def compile_state():
        return _cross_volume_continuity(tmp_path, route, claims, {"route:carryover"}, chapter_number=3,
                                        planning_scope={"volume_id": "vol002"}, current_scope={"volume": {"vol002"}})
    result = compile_state()
    actual = result["items"][0]["actual"]
    assert (actual["value"] if actual else None) == expected
    assert len(result["source_files"]) == 4
    manuscript_chapter_path(tmp_path, 1, lane="final").write_text("正文已修改", encoding="utf-8")
    with pytest.raises(FanfictionContextError, match="not bound to current final"):
        compile_state()
