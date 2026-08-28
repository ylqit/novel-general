import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_results import build_agent_result_template
from longform_engine.agent_tasks import load_manifest
from longform_engine.fanfiction_sources import project_source_contract
from longform_engine.fanfiction_context import (
    FanfictionContextError,
    compile_fanfiction_context,
    event_disposition_status,
)
from longform_engine.fanfiction_contracts import (
    crossover_required_topics,
    fanfiction_route_review_projection_sha256,
    validate_fanfiction_source_canon,
)
from longform_engine.gates.pipeline import check_fanfiction_source_reproduction
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.intelligence.pipeline import (
    BOOK_IDEATION_DIMENSIONS,
    fanfiction_semantic_dependency_paths,
    validate_fanfiction_design,
)
from longform_engine.orchestration.pipeline import WorkflowError, load_fanfiction_writing_contract
from longform_engine.production import production_next
from longform_engine.semantic_protocols import (
    approved_semantic_document,
    build_human_decision,
    build_semantic_document,
    seal_semantic_document,
    validate_semantic_document,
)
from tests.test_fanfiction_source_library import (
    apply_project_canon,
    approved_library_item,
    complete_project_pack,
    project_config,
)


def prepared_project(tmp_path: Path, monkeypatch):
    _work, item, source_text = approved_library_item(tmp_path, monkeypatch)
    config, root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    canon = apply_project_canon(
        config,
        root,
        interpretation="林舟在第一卷末以前会先核验规则，再决定是否承担不可逆代价。",
    )
    return config, root, item, source_text, canon


def candidate_copy(document: dict) -> dict:
    candidate = deepcopy(document)
    candidate["artifact"]["state"] = "candidate"
    candidate["extensions"].pop("human_decision", None)
    candidate["extensions"].pop("approved_candidate_sha256", None)
    return seal_semantic_document(candidate)


def approved_copy(document: dict) -> dict:
    candidate = candidate_copy(document)
    artifact = candidate["artifact"]
    decision = build_human_decision(
        decision_id=f"decision:{artifact['artifact_id']}",
        target_id=artifact["artifact_id"],
        target_sha256=artifact["content_sha256"],
        decision="approve",
        decided_by="human",
        reason="测试夹具模拟人工批准当前语义链。",
        scope=artifact["scope"],
    )
    return approved_semantic_document(candidate, decision=decision)


def event_fate_claim(document: dict) -> dict:
    return next(
        claim
        for claim in document["claims"]
        if claim["extensions"].get("semantic_type") == "原著事件命运"
    )


def legacy_approved_story_engine(document: dict, gap: str) -> dict:
    legacy = deepcopy(document)
    if gap == "route_family":
        legacy["extensions"].pop("route_family")
    else:
        legacy["claims"] = [
            claim
            for claim in legacy["claims"]
            if claim["extensions"].get("semantic_type") != gap
        ]
    legacy = seal_semantic_document(legacy)
    assert not validate_semantic_document(legacy, require_approved=True)
    return legacy


def legacy_approved_route(document: dict, missing_field: str) -> dict:
    legacy = deepcopy(document)
    event_fate_claim(legacy)["extensions"].pop(missing_field)
    legacy = seal_semantic_document(legacy)
    assert not validate_semantic_document(legacy, require_approved=True)
    return legacy


def assert_writing_boundaries_reject(config, root: Path, pattern: str) -> None:
    with pytest.raises(FanfictionContextError, match=pattern):
        compile_fanfiction_context(
            config,
            chapter_number=1,
            chapter_contract={"chapter_number": 1},
            chapter_card={"title": "旧语义合同下游绕过"},
            character_packet={},
        )
    with pytest.raises(WorkflowError, match=pattern):
        load_fanfiction_writing_contract(
            config,
            root,
            chapter_number=1,
            chapter_contract={"chapter_number": 1},
            card={"title": "旧语义合同下游绕过"},
        )


def write_story_engine_candidate(
    config,
    root: Path,
    *,
    route_family: object = "hybrid",
    omitted_semantic_type: str | None = None,
):
    task = create_intelligence_task(config, task_type="fanfiction_story_engine")
    candidate_file = root / task.candidate_file
    claims = [
        {
            "claim_id": "engine:initial_variable",
            "statement": "林舟要求公开验证青铜门规则，成为唯一主要初始变量。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "验证方法在分卷设计中具体化。",
            "extensions": {"semantic_type": "唯一初始变量"},
        },
        {
            "claim_id": "engine:long_goal",
            "statement": "林舟要建立任何人都不能绕过的遗迹规则验证机制。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "终局制度形态尚未决定。",
            "extensions": {"semantic_type": "独立长期目标"},
        },
        {
            "claim_id": "engine:resistance",
            "statement": "守门利益集团、倒计时和验证本身的资源成本持续阻碍目标。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "各卷主要承担者不同。",
            "extensions": {"semantic_type": "可持续阻力"},
        },
        {
            "claim_id": "engine:agency",
            "statement": "守门人可以拒绝林舟，并在林舟不在场时推进自己的保密目标。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "拒绝造成的关系代价按卷确定。",
            "extensions": {"semantic_type": "原著人物自主性"},
        },
        {
            "claim_id": "engine:after_canon",
            "statement": "青铜门原作事件结束后，验证制度与既得利益的冲突继续产生新案件。",
            "applicability": "原作事件结束后",
            "evidence_refs": [],
            "uncertainty": "终局案件只保留方向。",
            "extensions": {"semantic_type": "原作后续故事来源"},
        },
        {
            "claim_id": "engine:protagonist_canon_relation",
            "statement": "林舟既依赖守门人的原著职责，也必须接受对方拒绝其验证方案。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "关系债务随事件处置变化。",
            "extensions": {"semantic_type": "主角与原著关系"},
        },
        {
            "claim_id": "engine:recognition_promise",
            "statement": "读者持续看见青铜门规则、守门人选择方式和原著关系债务的可识别回响。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "每卷选取不同识别载体。",
            "extensions": {"semantic_type": "读者识别承诺"},
        },
        {
            "claim_id": "engine:original_mainline_promise",
            "statement": "公开验证机制会产生原作未解决的新案件、制度冲突和终局选择。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "具体案件由分卷路线决定。",
            "extensions": {"semantic_type": "原创主线承诺"},
        },
    ]
    claims = [
        claim
        for claim in claims
        if claim["extensions"]["semantic_type"] != omitted_semantic_type
    ]
    extensions = {} if route_family is None else {"route_family": route_family}
    document = build_semantic_document(
        document_id="sem_story_engine_gate",
        document_type="同人故事发动机",
        title="青铜门长篇故事发动机",
        scope={"kind": "project", "project": root.name},
        continuity="原作分歧",
        body="以规则验证取代无条件开门，长期追踪信任、责任和安全通道关闭造成的代价。",
        claims=claims,
        evidence_references=[],
        extensions=extensions,
    )
    candidate_file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    control = validate_production_agent_result(
        root,
        load_manifest(root, task.task_id),
        result_file=candidate_file,
    )
    assert control.ok, control.normalization.errors
    return task, candidate_file


def apply_story_engine(config, root: Path) -> dict:
    _task, candidate_file = write_story_engine_candidate(config, root)
    validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_story_engine",
        file_path=candidate_file,
    )
    assert validation.ok, validation.errors
    applied = apply_intelligence_candidate(
        config,
        task_type="fanfiction_story_engine",
        file_path=candidate_file,
        approved_by="human",
    )
    assert applied.status == "applied"
    return json.loads(
        (root / "10_bible" / "fanfiction" / "story_engine.json").read_text(encoding="utf-8")
    )


def apply_route_design(
    config,
    root: Path,
    canon: dict,
    *,
    stop_after_validation: bool = False,
) -> dict | Path:
    apply_story_engine(config, root)
    task = create_intelligence_task(config, task_type="fanfiction_design")
    candidate_file = root / task.candidate_file
    evidence = deepcopy(canon["evidence_references"][:1])
    evidence_id = evidence[0]["evidence_id"]
    document = build_semantic_document(
        document_id="sem_route_gate_divergence",
        document_type="同人路线设计候选",
        title="青铜门分歧路线",
        scope={"kind": "project", "project": root.name},
        continuity="原作分歧",
        body=(
            "林舟没有立即开门，而是迫使守门人公开验证条件；这项选择先改变互信，"
            "随后让安全通道的关闭成为双方共同承担的倒计时。"
        ),
        claims=[
            {
                "claim_id": "route:initial_divergence",
                "statement": "林舟第一次拒绝在规则未公开时开启青铜门。",
                "applicability": "第一卷末",
                "evidence_refs": [evidence_id],
                "uncertainty": "验证所需时间仍取决于现场条件。",
                "extensions": {"semantic_type": "初始分歧"},
            },
            {
                "claim_id": "route:entry_point",
                "statement": "故事在第一卷青铜门开启前一日切入。",
                "applicability": "开篇",
                "evidence_refs": [evidence_id],
                "uncertainty": "具体时刻以分卷设计为准。",
                "extensions": {"semantic_type": "故事切入点"},
            },
            {
                "claim_id": "route:knowledge_boundary",
                "statement": "林舟知道青铜门将关闭，却不知道守门人的真实政治目的。",
                "applicability": "第一卷",
                "evidence_refs": [evidence_id],
                "uncertainty": "重大分歧后原时间线只能作为线索。",
                "extensions": {"semantic_type": "人物知识边界"},
            },
            {
                "claim_id": "route:future_reliability",
                "statement": "首次分歧后，林舟的未来知识降为部分可靠，不能直接决定事件结果。",
                "applicability": "首次分歧之后",
                "evidence_refs": [evidence_id],
                "uncertainty": "每次重大事件后重新评估。",
                "extensions": {"semantic_type": "未来知识可靠性"},
            },
            {
                "claim_id": "route:canon_duty",
                "statement": "守门人仍负责判断是否公开规则，并可拒绝林舟提出的验证方法。",
                "applicability": "第一卷",
                "evidence_refs": [evidence_id],
                "uncertainty": "职责可因后续政治债务转化。",
                "extensions": {"semantic_type": "原著人物职责"},
            },
            {
                "claim_id": "route:gate_divergence",
                "statement": "林舟以延迟开门换取规则公开，并承担安全通道继续关闭的代价。",
                "applicability": "第一卷末后的首个分歧节点",
                "evidence_refs": [evidence_id],
                "uncertainty": "守门人的后续政治立场仍需在分卷设计中决定。",
                "extensions": {"semantic_type": "分歧后果"},
            },
            {
                "claim_id": "route:organizational_debt",
                "statement": "规则公开迫使守门组织重新分配保密责任并追究失败成本。",
                "applicability": "第二卷",
                "evidence_refs": [evidence_id],
                "uncertainty": "具体追责对象按第一卷结局确定。",
                "extensions": {"semantic_type": "分歧后果"},
            },
            {
                "claim_id": "route:lin_voice",
                "statement": "林舟的对白保持短促、先追问证据再承诺行动。",
                "applicability": "当前卷的高压谈判场景",
                "evidence_refs": [evidence_id],
                "uncertainty": "私下亲密场景需要单独人物理解。",
                "extensions": {"semantic_type": "人物声音"},
            },
            {
                "claim_id": "route:gate_event_fate",
                "statement": "青铜门仍会开启，但结果改为在公开验证后由双方共同承担。",
                "applicability": "第一卷末",
                "evidence_refs": [evidence_id],
                "uncertainty": "验证失败时保留取消开启的人工决定。",
                "extensions": {
                    "semantic_type": "原著事件命运",
                    "disposition": "结果改变",
                    "depends_on_claims": ["route:initial_divergence"],
                    "responsibility_owner_ids": ["route:canon_duty"],
                    "first_order_effect_claim_ids": ["route:gate_divergence"],
                    "second_order_effect_claim_ids": ["route:organizational_debt"],
                },
            },
        ],
        evidence_references=evidence,
        extensions={"future_knowledge_used": True},
    )
    candidate_file.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, task.task_id),
        result_file=candidate_file,
    )
    assert control.ok, control.normalization.errors
    validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_design",
        file_path=candidate_file,
    )
    assert validation.ok, validation.errors
    if stop_after_validation:
        return candidate_file
    review_task = create_intelligence_task(
        config,
        task_type="fanfiction_design_review",
        input_files=[candidate_file],
    )
    review_file = root / review_task.candidate_file
    review = build_semantic_document(
        document_id="sem_route_gate_review",
        document_type="同人路线独立复核",
        title="青铜门分歧路线独立复核",
        scope={"kind": "project", "project": root.name},
        continuity="原作分歧",
        body="原著基线、唯一分歧、知识退化、人物拒绝权、事件命运和长期故事来源均已形成可执行因果链。",
        claims=[],
        evidence_references=[],
        extensions={"verdict": "pass"},
    )
    review_file.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    review_control = validate_production_agent_result(
        root,
        load_manifest(root, review_task.task_id),
        result_file=review_file,
    )
    assert review_control.ok, review_control.normalization.errors
    review_validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_design_review",
        file_path=review_file,
    )
    assert review_validation.ok, review_validation.errors
    applied = apply_intelligence_candidate(
        config,
        task_type="fanfiction_design",
        file_path=candidate_file,
        approved_by="human",
        review_path=review_file,
    )
    assert applied.status == "applied"
    return json.loads(
        (root / "10_bible" / "fanfiction" / "fanfiction_bible.json").read_text(
            encoding="utf-8"
        )
    )


def test_story_engine_accepts_exactly_the_three_route_families(tmp_path, monkeypatch):
    config, root, _item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)

    for route_family in ("oc_si_progression", "canon_character_centered", "hybrid"):
        _task, candidate_file = write_story_engine_candidate(
            config,
            root,
            route_family=route_family,
        )

        validation = validate_intelligence_candidate(
            config,
            task_type="fanfiction_story_engine",
            file_path=candidate_file,
        )

        assert validation.ok, (route_family, validation.errors)


@pytest.mark.parametrize("route_family", [None, "canon_replay", "", ["hybrid"]])
def test_story_engine_rejects_missing_or_unknown_route_family(
    tmp_path,
    monkeypatch,
    route_family,
):
    config, root, _item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)
    _task, candidate_file = write_story_engine_candidate(
        config,
        root,
        route_family=route_family,
    )

    validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_story_engine",
        file_path=candidate_file,
    )

    assert not validation.ok
    assert any("extensions.route_family" in error for error in validation.errors)


@pytest.mark.parametrize(
    "semantic_type",
    ["主角与原著关系", "读者识别承诺", "原创主线承诺"],
)
def test_story_engine_requires_new_semantic_claims(
    tmp_path,
    monkeypatch,
    semantic_type,
):
    config, root, _item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)
    _task, candidate_file = write_story_engine_candidate(
        config,
        root,
        omitted_semantic_type=semantic_type,
    )

    validation = validate_intelligence_candidate(
        config,
        task_type="fanfiction_story_engine",
        file_path=candidate_file,
    )

    assert not validation.ok
    assert any(semantic_type in error for error in validation.errors)


def test_story_engine_compiled_work_order_covers_both_routes(
    tmp_path,
    monkeypatch,
):
    config, root, _item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)
    task = create_intelligence_task(config, task_type="fanfiction_story_engine")
    manifest = load_manifest(root, task.task_id)
    template = build_agent_result_template(manifest)
    instruction = (root / task.instruction_file).read_text(encoding="utf-8")
    assert template["document_type"] == "同人故事发动机"
    assert template["extensions"]["route_family"] == ""
    assert "oc_si_progression" in instruction
    assert "canon_character_centered" in instruction
    assert "主角与原著关系" in instruction
    assert "读者识别承诺" in instruction
    assert "原创主线承诺" in instruction


def test_route_compiled_work_order_requires_event_chain_and_crossover_topology(
    tmp_path,
    monkeypatch,
):
    config, root, _item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)
    apply_story_engine(config, root)
    task = create_intelligence_task(config, task_type="fanfiction_design")
    instruction = (root / task.instruction_file).read_text(encoding="utf-8")
    context = json.loads(
        (
            root
            / "50_workbench"
            / "intelligence_context"
            / "fanfiction_design.project.context.json"
        ).read_text(encoding="utf-8")
    )
    causal_chain = "原著基线→变量→处置→职责→一阶→二阶→新问题"
    assert context["approved_story_engine"]["route_family"] == "hybrid"
    crossover_contract = context["crossover_contract"]
    assert crossover_contract["required"] is False
    assert set(crossover_contract["topologies"]) == {
        "fixed_host",
        "fusion_world",
        "sequential_worlds",
    }
    assert "body_or_soul" in crossover_contract["payload_kinds"]
    assert set(crossover_contract["topics_by_payload_kind"]["ability"]) == {
        "能量关系",
        "能力作用对象",
        "激活与补充",
        "代价",
        "当地反制",
    }
    assert "实际 transfers" in crossover_contract["adapter_scope"]
    assert crossover_contract["adapter_fields"] == [
        "source_id",
        "payload_kinds",
        "host_source_id",
        "volume_ids",
    ]
    assert "non-host" in crossover_contract["topology_rules"]["fixed_host"]
    assert "at least two" in crossover_contract["topology_rules"]["fusion_world"]
    assert "extensions.crossover.volume_ids" in crossover_contract["topology_rules"][
        "sequential_worlds"
    ]
    assert causal_chain in instruction
    assert "fixed_host" in instruction
    assert "fusion_world" in instruction
    assert "sequential_worlds" in instruction
    assert "payload_kinds" in instruction
    assert "实际 transfers" in instruction
    assert "host_source_id" in instruction
    assert "extensions.crossover.volume_ids" in instruction


def test_route_review_compiled_prompt_checks_topology_and_actual_payloads(
    tmp_path,
    monkeypatch,
):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    candidate_file = apply_route_design(
        config,
        root,
        canon,
        stop_after_validation=True,
    )
    review_task = create_intelligence_task(
        config,
        task_type="fanfiction_design_review",
        input_files=[candidate_file],
    )

    instruction = (root / review_task.instruction_file).read_text(encoding="utf-8")

    assert "fixed_host" in instruction
    assert "fusion_world" in instruction
    assert "sequential_worlds" in instruction
    assert "payload_kinds" in instruction
    assert "卷宿主世界" in instruction
    assert "host_source_id" in instruction
    assert "extensions.crossover.volume_ids" in instruction


@pytest.mark.parametrize("gap", ["route_family", "原创主线承诺"])
def test_design_task_rejects_legacy_approved_story_engine(
    tmp_path,
    monkeypatch,
    gap,
):
    config, root, _item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)
    current = apply_story_engine(config, root)
    legacy = legacy_approved_story_engine(current, gap)
    (root / "10_bible" / "fanfiction" / "story_engine.json").write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="current fanfiction story engine"):
        create_intelligence_task(config, task_type="fanfiction_design")


def test_design_validator_rejects_legacy_approved_story_engine(
    tmp_path,
    monkeypatch,
):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    route = apply_route_design(config, root, canon)
    current_engine = json.loads(
        (root / "10_bible" / "fanfiction" / "story_engine.json").read_text(
            encoding="utf-8"
        )
    )

    for gap in ("route_family", "原创主线承诺"):
        legacy = legacy_approved_story_engine(current_engine, gap)
        engine_path = root / "10_bible" / "fanfiction" / "story_engine.json"
        engine_path.write_text(
            json.dumps(legacy, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        candidate = candidate_copy(route)
        candidate["extensions"]["story_engine_sha256"] = sha256(
            engine_path.read_bytes()
        ).hexdigest()
        errors: list[str] = []

        validate_fanfiction_design(config, root, seal_semantic_document(candidate), errors)

        assert any(
            "current fanfiction story engine" in error
            or "route_family" in error
            or gap in error
            for error in errors
        ), (gap, errors)


def test_writing_context_rejects_legacy_approved_story_engine(
    tmp_path,
    monkeypatch,
):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    route = apply_route_design(config, root, canon)
    engine_path = root / "10_bible" / "fanfiction" / "story_engine.json"
    engine = json.loads(engine_path.read_text(encoding="utf-8"))
    legacy = legacy_approved_story_engine(engine, "route_family")
    engine_path.write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    route["extensions"]["story_engine_sha256"] = sha256(engine_path.read_bytes()).hexdigest()
    route = seal_semantic_document(route)
    (root / "10_bible" / "fanfiction" / "fanfiction_bible.json").write_text(
        json.dumps(route, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert_writing_boundaries_reject(config, root, "current fanfiction story engine")


def test_writing_context_and_event_status_reject_legacy_approved_route(
    tmp_path,
    monkeypatch,
):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    current = apply_route_design(config, root, canon)
    legacy = legacy_approved_route(current, "second_order_effect_claim_ids")
    errors: list[str] = []
    validate_fanfiction_design(config, root, legacy, errors)
    assert any("second_order_effect_claim_ids" in error for error in errors), errors
    (root / "10_bible" / "fanfiction" / "fanfiction_bible.json").write_text(
        json.dumps(legacy, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert_writing_boundaries_reject(config, root, "second_order_effect_claim_ids")

    status = event_disposition_status(config)
    assert status["route_status"] == "invalid"
    assert status["events"] == []
    assert any("second_order_effect_claim_ids" in item for item in status["diagnostics"])


def test_validated_route_requires_independent_review_and_production_routes_it(
    tmp_path, monkeypatch
):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    candidate_file = apply_route_design(
        config,
        root,
        canon,
        stop_after_validation=True,
    )
    assert isinstance(candidate_file, Path)

    with pytest.raises(ValueError, match="independent current review"):
        apply_intelligence_candidate(
            config,
            task_type="fanfiction_design",
            file_path=candidate_file,
            approved_by="human",
        )

    decisions = root / "10_bible" / "creative_decisions.json"
    decisions.write_text(
        json.dumps(
            {
                "schema": "book_ideation_decisions_v1",
                "dimensions": list(BOOK_IDEATION_DIMENSIONS),
                "decisions": {
                    dimension: f"人工确认：{dimension}"
                    for dimension in BOOK_IDEATION_DIMENSIONS
                },
                "rounds": [],
                "complete": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    action = production_next(config)

    assert action["status"] == "ready_for_fanfiction_design_review", action
    assert action["task_type"] == "fanfiction_design_review"
    assert action["next_command"] == (
        "longform-engine fanfiction design-review-task project.yaml "
        f"--file {candidate_file.relative_to(root).as_posix()}"
    )


def test_actual_crossover_transfer_derives_only_its_payload_topics():
    topics = crossover_required_topics(
        {
            "transfers": [
                {"source_id": "guest", "payload_kinds": ["ability"]}
            ]
        }
    )

    assert "宿主世界" in topics
    assert "能量关系" in topics
    assert "当地反制" in topics
    assert "身体与灵魂" not in topics
    assert "信息传播" not in topics
    assert "组织迁移" not in topics


def test_semantic_canon_compiles_to_readable_bounded_writing_context(tmp_path, monkeypatch):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    apply_route_design(config, root, canon)

    contract = load_fanfiction_writing_contract(
        config,
        root,
        chapter_number=1,
        chapter_contract={
            "chapter_number": 1,
            "canon_claim_refs": ["route:gate_divergence", "route:lin_voice"],
        },
        card={"title": "门前谈判", "featured_character_ids": ["classic:lin_zhou"]},
    )

    assert contract["enabled"] is True
    assert contract["continuity_mode"] == "canon_divergent"
    assert any("延迟开门" in item for item in contract["approved_divergences"])
    assert "route:gate_divergence" in contract["required_claim_ids"]
    assert contract["context_bundle_sha256"]
    assert {
        "engine:protagonist_canon_relation",
        "engine:recognition_promise",
        "engine:original_mainline_promise",
    } <= set(contract["included_claim_ids"])
    serialized = json.dumps(contract, ensure_ascii=False)
    assert "normalization_sha256" not in serialized
    assert "source-library://" not in serialized
    assert len(serialized) < 8_000


def install_cross_namespace_event_dependencies(
    config,
    root: Path,
    canon: dict,
    *,
    out_of_scope: bool = False,
) -> set[str]:
    route = apply_route_design(config, root, canon)
    engine_path = root / "10_bible" / "fanfiction" / "story_engine.json"
    engine = json.loads(engine_path.read_text(encoding="utf-8"))
    engine["claims"].append(
        {
            "claim_id": "engine:causal_target",
            "statement": "发动机层因果目标必须随事件命运进入章节合同。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "无。",
            "extensions": {"semantic_type": "因果辅助"},
        }
    )
    engine = approved_copy(engine)
    engine_path.write_text(
        json.dumps(engine, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    route["claims"].append(
        {
            "claim_id": "route:causal_target",
            "statement": "路线层二阶目标必须随事件命运进入章节合同。",
            "applicability": "全书",
            "evidence_refs": [],
            "uncertainty": "无。",
            "extensions": {
                "semantic_type": "因果辅助",
                **({"chapter_numbers": [2]} if out_of_scope else {}),
            },
        }
    )
    event_fate_claim(route)["extensions"].update(
        {
            "responsibility_owner_ids": [canon["claims"][0]["claim_id"]],
            "first_order_effect_claim_ids": ["engine:causal_target"],
            "second_order_effect_claim_ids": ["route:causal_target"],
        }
    )
    engine_sha = sha256(engine_path.read_bytes()).hexdigest()
    route["extensions"]["story_engine_sha256"] = engine_sha
    review_path = root / route["extensions"]["independent_review"]["review_path"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    target_path = root / review["extensions"]["review_target_path"]
    route_target = candidate_copy(route)
    route_target["extensions"].pop("independent_review", None)
    route_target = seal_semantic_document(route_target)
    target_path.write_text(
        json.dumps(route_target, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    review["extensions"]["story_engine_sha256"] = engine_sha
    review["extensions"]["review_target_sha256"] = sha256(target_path.read_bytes()).hexdigest()
    review = approved_copy(review)
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    route["extensions"]["independent_review"]["review_sha256"] = sha256(
        review_path.read_bytes()
    ).hexdigest()
    route["extensions"]["independent_review"]["review_artifact_id"] = review["artifact"][
        "artifact_id"
    ]
    route["extensions"]["independent_review"]["reviewed_route_projection_sha256"] = (
        fanfiction_route_review_projection_sha256(route_target)
    )
    route = approved_copy(route)
    (root / "10_bible" / "fanfiction" / "fanfiction_bible.json").write_text(
        json.dumps(route, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        canon["claims"][0]["claim_id"],
        "engine:causal_target",
        "route:causal_target",
    }


def test_event_causal_targets_join_required_cross_namespace_dependency_closure(
    tmp_path, monkeypatch
):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    causal_targets = install_cross_namespace_event_dependencies(config, root, canon)

    contract = load_fanfiction_writing_contract(
        config,
        root,
        chapter_number=1,
        chapter_contract={"chapter_number": 1},
        card={"title": "因果闭包"},
    )

    assert causal_targets <= set(contract["required_claim_ids"])
    assert causal_targets <= set(contract["included_claim_ids"])


def test_event_causal_dependency_outside_chapter_scope_blocks_context(tmp_path, monkeypatch):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    install_cross_namespace_event_dependencies(config, root, canon, out_of_scope=True)

    with pytest.raises(WorkflowError, match="fanfiction_context_dependency_out_of_scope"):
        load_fanfiction_writing_contract(
            config,
            root,
            chapter_number=1,
            chapter_contract={"chapter_number": 1},
            card={"title": "越界因果依赖"},
        )


def test_names_do_not_select_optional_claims_and_unknown_explicit_refs_block(
    tmp_path, monkeypatch
):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    apply_route_design(config, root, canon)

    contract = load_fanfiction_writing_contract(
        config,
        root,
        chapter_number=2,
        chapter_contract={"chapter_number": 2},
        card={"title": "林舟与守门人再次谈判"},
    )

    assert "route:lin_voice" not in contract["included_claim_ids"]
    with pytest.raises(WorkflowError, match="fanfiction_context_missing_claims"):
        load_fanfiction_writing_contract(
            config,
            root,
            chapter_number=2,
            chapter_contract={
                "chapter_number": 2,
                "fanfiction_claim_refs": ["route:unknown_claim"],
            },
            card={"title": "未知依赖"},
        )


def test_event_fate_status_and_exact_dependency_closure(tmp_path, monkeypatch):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    apply_route_design(config, root, canon)
    dependent = root / "20_outline" / "chapter_contracts" / "ch001.json"
    unrelated = root / "20_outline" / "chapter_contracts" / "ch002.json"
    dependent.parent.mkdir(parents=True, exist_ok=True)
    dependent.write_text(
        json.dumps({"fanfiction_claim_refs": ["route:gate_divergence"]}),
        encoding="utf-8",
    )
    unrelated.write_text(
        json.dumps({"fanfiction_claim_refs": ["route:entry_point"]}),
        encoding="utf-8",
    )

    paths = fanfiction_semantic_dependency_paths(
        root,
        task_type="fanfiction_design",
        changed_claim_ids={"route:gate_divergence"},
    )
    status = event_disposition_status(config)

    assert "20_outline/chapter_contracts/ch001.json" in paths
    assert "20_outline/chapter_contracts/ch002.json" not in paths
    assert status["route_status"] == "approved"
    assert status["events"] == [
        {
            "claim_id": "route:gate_event_fate",
            "statement": "青铜门仍会开启，但结果改为在公开验证后由双方共同承担。",
            "disposition": "结果改变",
            "depends_on_claims": ["route:initial_divergence"],
            "responsibility_owner_ids": ["route:canon_duty"],
            "first_order_effect_claim_ids": ["route:gate_divergence"],
            "second_order_effect_claim_ids": ["route:organizational_debt"],
            "uncertainty": "验证失败时保留取消开启的人工决定。",
        }
    ]


def test_v011_rigid_canon_is_explicitly_incompatible(tmp_path, monkeypatch):
    config, _root = project_config(tmp_path)
    errors: list[str] = []

    validate_fanfiction_source_canon(
        config,
        {"schema": "fanfiction_source_canon_v3", "sources": []},
        errors,
    )

    assert any("incompatible" in error for error in errors)
    assert any("v0.11" in error for error in errors)


def test_unbound_evidence_cannot_enter_project_canon(tmp_path, monkeypatch):
    config, _root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    candidate = candidate_copy(canon)
    candidate["evidence_references"][0]["evidence_id"] = "item_unbound:evidence_1"
    candidate["claims"][0]["evidence_refs"] = ["item_unbound:evidence_1"]
    candidate = seal_semantic_document(candidate)
    errors: list[str] = []

    validate_fanfiction_source_canon(config, candidate, errors)

    assert any("not approved by a pinned project source" in error for error in errors)


def test_pinned_evidence_locator_cannot_be_forged(tmp_path, monkeypatch):
    config, _root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    candidate = candidate_copy(canon)
    candidate["evidence_references"][0]["locator"] = {
        "kind": "text_span",
        "asset_id": candidate["evidence_references"][0]["asset_id"],
        "start": 999_999,
        "end": 1_000_001,
    }
    candidate = seal_semantic_document(candidate)
    errors: list[str] = []

    validate_fanfiction_source_canon(config, candidate, errors)

    assert any("locator does not match pinned evidence" in error for error in errors)


def test_invalid_route_design_does_not_mutate_approved_bible(tmp_path, monkeypatch):
    config, root, _item, _source_text, canon = prepared_project(tmp_path, monkeypatch)
    original = apply_route_design(config, root, canon)
    bible_path = root / "10_bible" / "fanfiction" / "fanfiction_bible.json"
    original_bytes = bible_path.read_bytes()
    candidate = candidate_copy(original)
    candidate["evidence_references"][0]["evidence_id"] = "outside:unknown"
    for claim in candidate["claims"]:
        claim["evidence_refs"] = ["outside:unknown"]
    candidate = seal_semantic_document(candidate)
    errors: list[str] = []

    validate_fanfiction_design(config, root, candidate, errors)

    assert any("outside approved project Canon" in error for error in errors)
    assert bible_path.read_bytes() == original_bytes


def test_source_reproduction_detects_continuous_prose(tmp_path, monkeypatch):
    config, root, _item, source_text, _canon = prepared_project(tmp_path, monkeypatch)

    findings, warnings = check_fanfiction_source_reproduction(config, root, source_text)

    assert not warnings
    assert findings and findings[0]["code"] == "fanfiction_source_prose_reproduction"


def test_names_and_terms_alone_do_not_trigger_source_reproduction(tmp_path, monkeypatch):
    config, root, _item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)
    prose = (
        "林舟把星纹钥匙压在掌心，没有复述守门人的警告。"
        "他转身问同行者愿意失去什么，这个新问题让三个人给出了完全不同的答案。"
    )

    findings, warnings = check_fanfiction_source_reproduction(config, root, prose)

    assert findings == []
    assert warnings == []


def test_project_source_contract_contains_only_approved_dynamic_evidence(tmp_path, monkeypatch):
    config, _root, item, _source_text, _canon = prepared_project(tmp_path, monkeypatch)

    contract = project_source_contract(config, "classic")

    assert contract["binding"]["items"][0]["item_id"] == item["item_id"]
    assert set(contract["evidence"]) == {
        f"{item['item_id']}:e1",
        f"{item['item_id']}:e2",
    }
