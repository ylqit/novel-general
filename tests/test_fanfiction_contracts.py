import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_tasks import load_manifest
from longform_engine.config import ConfigDocument
from longform_engine.editorial import (
    editorial_aggregate,
    editorial_review,
    editorial_submit_review,
)
from longform_engine.gates import GateError, semantic_review_task
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    create_intelligence_task,
    validate_design_compile_delta,
    validate_intelligence_candidate,
)
from longform_engine.orchestration import (
    auto_write_plan,
    auto_write_report,
    auto_write_run,
    batch_write,
    finalize_chapter,
    generate_beat_sheet,
    submit_agent_draft,
)
from longform_engine.planning import (
    apply_planning_bundle,
    build_human_node_decisions,
    build_human_planning_approval,
    build_planning_semantic_application,
)
from longform_engine import fanfiction_contracts as contracts
from longform_engine.fanfiction_context import (
    FanfictionContextError,
    compile_fanfiction_context,
    event_disposition_status,
    fanfiction_context_status,
    write_fanfiction_context_bundle,
)
from longform_engine.semantic_protocols import (
    EVIDENCE_REFERENCE_SCHEMA,
    approved_semantic_document,
    build_human_decision,
    build_semantic_document,
    canonical_json_hash,
    seal_semantic_document,
)


def approve(document: dict) -> dict:
    candidate = seal_semantic_document(document)
    decision = build_human_decision(
        decision_id=f"decision:{candidate['artifact']['artifact_id']}",
        target_id=candidate["artifact"]["artifact_id"],
        target_sha256=candidate["artifact"]["content_sha256"],
        decision="approve",
        decided_by="human",
        reason="测试当前 canonical 合同。",
        scope={"kind": "project"},
    )
    return approved_semantic_document(candidate, decision=decision)


def reapprove(document: dict) -> dict:
    candidate = deepcopy(document)
    artifact = candidate.get("artifact")
    if isinstance(artifact, dict):
        artifact["state"] = "candidate"
    extensions = candidate.get("extensions")
    if isinstance(extensions, dict):
        extensions.pop("approved_candidate_sha256", None)
        extensions.pop("human_decision", None)
    return approve(candidate)


def write_document(path: Path, document: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw)
    return sha256(raw).hexdigest()


def project_file_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def reviewed_route_projection_sha256(document: dict) -> str:
    extensions = deepcopy(document["extensions"])
    for field in (
        "independent_review",
        "approved_candidate_sha256",
        "human_decision",
    ):
        extensions.pop(field, None)
    return canonical_json_hash(
        {
            "document_type": document["document_type"],
            "title": document["title"],
            "continuity": document["continuity"],
            "body": document["body"],
            "claims": document["claims"],
            "evidence_references": document["evidence_references"],
            "uncertainties": document["uncertainties"],
            "extensions": extensions,
        }
    )


def semantic_claim(
    claim_id: str,
    semantic_type: str,
    *,
    evidence_refs: list[str] | None = None,
    extensions: dict | None = None,
) -> dict:
    return {
        "claim_id": claim_id,
        "statement": f"{claim_id} 的开放中文语义正文。",
        "applicability": "全书",
        "evidence_refs": list(evidence_refs or []),
        "uncertainty": "具体实现由后续设计决定。",
        "extensions": {"semantic_type": semantic_type, **(extensions or {})},
    }


def crossover_config(
    tmp_path: Path,
    *,
    continuity_mode: str = "crossover",
    third_source: bool = False,
    cross_source_elements: bool = True,
) -> ConfigDocument:
    sources = [
        {
            "source_id": "host",
            "title": "宿主作品",
            "allowed_elements": ["world"] if cross_source_elements else ["timeline"],
        },
        {
            "source_id": "guest",
            "title": "来访作品",
            "allowed_elements": ["characters"] if cross_source_elements else ["relationships"],
        },
    ]
    if third_source:
        sources.append(
            {
                "source_id": "unused",
                "title": "未参与作品",
                "allowed_elements": ["timeline"],
            }
        )
    return ConfigDocument(
        data={
            "creation": {"mode": "fanfiction"},
            "project": {"root_dir": str(tmp_path / "project")},
            "fanfiction": {
                "continuity_mode": continuity_mode,
                "sources": sources,
            },
        },
        path=tmp_path / "project" / "project.yaml",
        sources=(),
    )


def crossover_route(
    *,
    topology: str,
    default_host_source_id: str | None,
    payload_kinds: list[str] | None = None,
) -> dict:
    actual_payload_kinds = list(payload_kinds or ["ability"])
    transfers = [
        {
            "source_id": "guest",
            "payload_kinds": actual_payload_kinds,
        }
    ]
    if topology == "fusion_world":
        transfers.append(
            {
                "source_id": "host",
                "payload_kinds": actual_payload_kinds,
            }
        )
    crossover = {
        "topology": topology,
        "default_host_source_id": default_host_source_id,
        "transfers": transfers,
    }
    if topology == "sequential_worlds":
        crossover["volume_ids"] = ["vol001"]
    adapter_scopes = {
        "fixed_host": {
            "host_source_id": default_host_source_id,
            "volume_ids": None,
        },
        "fusion_world": {"host_source_id": None, "volume_ids": None},
        "sequential_worlds": {
            "host_source_id": "host",
            "volume_ids": ["vol001"],
        },
    }
    claims = [
        *[
            semantic_claim(
                f"route:{transfer['source_id']}_adapter",
                "主世界适配器",
                extensions={
                    "source_id": transfer["source_id"],
                    "payload_kinds": list(transfer["payload_kinds"]),
                    **adapter_scopes[topology],
                },
            )
            for transfer in transfers
        ],
        semantic_claim(
            "route:constitution",
            "跨界宪法",
            extensions={"topics": sorted(contracts.crossover_required_topics(crossover))},
        ),
    ]
    if topology == "fusion_world":
        claims.append(semantic_claim("route:world_priority", "世界规则优先级"))
    elif topology == "sequential_worlds":
        claims.append(
            semantic_claim(
                "route:volume_host",
                "卷宿主世界",
                extensions={"volume_ids": ["vol001"], "host_source_id": "host"},
            )
        )
    return {"extensions": {"crossover": crossover}, "claims": claims}


def validate_crossover(config: ConfigDocument, route: dict) -> list[str]:
    errors: list[str] = []
    contracts.validate_crossover_route_contract(config, route, errors)
    return errors


@pytest.mark.parametrize(
    ("payload_kind", "expected_topics"),
    [
        (
            "character",
            {"身体与灵魂", "感知", "身份组织法律", "死亡与复活", "返回"},
        ),
        (
            "body_or_soul",
            {"身体与灵魂", "感知", "身份组织法律", "死亡与复活", "返回"},
        ),
        (
            "ability",
            {"能量关系", "能力作用对象", "激活与补充", "代价", "当地反制"},
        ),
        (
            "item_or_contract",
            {"装备召唤物契约", "激活与补充", "代价", "当地反制"},
        ),
        ("knowledge", {"来源时间点", "信息传播"}),
        ("organization", {"身份组织法律", "信息传播"}),
        ("world_rule", {"身份组织法律", "信息传播"}),
    ],
)
def test_crossover_topics_are_derived_only_from_actual_payload_kinds(
    payload_kind, expected_topics
):
    crossover = {
        "transfers": [{"source_id": "guest", "payload_kinds": [payload_kind]}]
    }

    assert contracts.crossover_required_topics(crossover) == {
        "宿主世界",
        "不可逆后果",
        *expected_topics,
    }


@pytest.mark.parametrize(
    ("topology", "default_host_source_id"),
    [
        ("fixed_host", "host"),
        ("fusion_world", None),
        ("sequential_worlds", None),
    ],
)
def test_crossover_contract_accepts_each_topology(
    tmp_path, topology, default_host_source_id
):
    route = crossover_route(
        topology=topology,
        default_host_source_id=default_host_source_id,
    )

    assert validate_crossover(crossover_config(tmp_path), route) == []


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda route: route["extensions"].pop("crossover"), "extensions.crossover"),
        (
            lambda route: route["extensions"]["crossover"].pop("topology"),
            "topology",
        ),
        (
            lambda route: route["extensions"]["crossover"].__setitem__(
                "topology", "portal_network"
            ),
            "topology",
        ),
        (
            lambda route: route["extensions"]["crossover"].pop(
                "default_host_source_id"
            ),
            "default_host_source_id",
        ),
        (
            lambda route: route["extensions"]["crossover"].__setitem__(
                "default_host_source_id", None
            ),
            "fixed_host",
        ),
        (
            lambda route: route["extensions"]["crossover"].__setitem__(
                "default_host_source_id", "unknown"
            ),
            "default_host_source_id",
        ),
    ],
)
def test_crossover_contract_rejects_missing_or_invalid_topology_and_host(
    tmp_path, mutation, expected
):
    route = crossover_route(topology="fixed_host", default_host_source_id="host")
    mutation(route)

    assert any(expected in error for error in validate_crossover(crossover_config(tmp_path), route))


@pytest.mark.parametrize("topology", ["fusion_world", "sequential_worlds"])
def test_non_fixed_topology_rejects_a_default_host(tmp_path, topology):
    route = crossover_route(topology=topology, default_host_source_id=None)
    route["extensions"]["crossover"]["default_host_source_id"] = "host"

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any(topology in error and "null" in error for error in errors)


@pytest.mark.parametrize("topology", ["fusion_world", "sequential_worlds"])
def test_non_fixed_topology_still_requires_explicit_null_host(tmp_path, topology):
    route = crossover_route(topology=topology, default_host_source_id=None)
    route["extensions"]["crossover"].pop("default_host_source_id")

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("default_host_source_id" in error for error in errors)


@pytest.mark.parametrize(
    ("transfers", "expected"),
    [
        (None, "transfers"),
        ([], "transfers"),
        (["guest"], "transfers[0]"),
        ([{"source_id": "unknown", "payload_kinds": ["ability"]}], "source_id"),
        ([{"source_id": "guest"}], "payload_kinds"),
        ([{"source_id": "guest", "payload_kinds": []}], "payload_kinds"),
        ([{"source_id": "guest", "payload_kinds": [""]}], "payload_kinds"),
        ([{"source_id": "guest", "payload_kinds": [7]}], "payload_kinds"),
        ([{"source_id": "guest", "payload_kinds": ["spell"]}], "payload_kinds"),
    ],
)
def test_crossover_contract_rejects_invalid_transfers(tmp_path, transfers, expected):
    route = crossover_route(topology="fixed_host", default_host_source_id="host")
    if transfers is None:
        route["extensions"]["crossover"].pop("transfers")
    else:
        route["extensions"]["crossover"]["transfers"] = transfers

    assert any(expected in error for error in validate_crossover(crossover_config(tmp_path), route))


def test_only_sources_used_by_transfers_require_host_adapters(tmp_path):
    config = crossover_config(tmp_path, third_source=True)
    route = crossover_route(topology="fixed_host", default_host_source_id="host")

    assert validate_crossover(config, route) == []

    route["claims"].append(
        semantic_claim(
            "route:invalid_adapter",
            "主世界适配器",
            extensions={
                "source_id": "not-configured",
                "payload_kinds": ["ability"],
                "host_source_id": "host",
                "volume_ids": None,
            },
        )
    )
    assert any("configured" in error for error in validate_crossover(config, route))


def test_fixed_host_rejects_host_self_transfer(tmp_path):
    route = crossover_route(topology="fixed_host", default_host_source_id="host")
    route["extensions"]["crossover"]["transfers"] = [
        {"source_id": "host", "payload_kinds": ["ability"]}
    ]
    route["claims"][0]["extensions"]["source_id"] = "host"

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("non-host" in error or "self-transfer" in error for error in errors)


def test_fusion_world_requires_two_actual_participating_sources(tmp_path):
    route = crossover_route(topology="fusion_world", default_host_source_id=None)
    route["extensions"]["crossover"]["transfers"] = route["extensions"]["crossover"][
        "transfers"
    ][:1]
    route["claims"] = [
        claim
        for claim in route["claims"]
        if claim["extensions"].get("source_id") != "host"
    ]

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("at least two participating" in error for error in errors)


def test_adapter_rejects_configured_source_outside_actual_transfers(tmp_path):
    config = crossover_config(tmp_path, third_source=True)
    route = crossover_route(topology="fixed_host", default_host_source_id="host")
    route["claims"].append(
        semantic_claim(
            "route:unused_adapter",
            "主世界适配器",
            extensions={
                "source_id": "unused",
                "payload_kinds": ["ability"],
                "host_source_id": "host",
                "volume_ids": None,
            },
        )
    )

    errors = validate_crossover(config, route)

    assert any("outside actual transfers" in error for error in errors)


def test_adapter_payload_kinds_must_match_actual_transfer(tmp_path):
    route = crossover_route(topology="fixed_host", default_host_source_id="host")
    route["claims"][0]["extensions"]["payload_kinds"] = ["knowledge"]

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("payload_kinds" in error and "actual transfer" in error for error in errors)


def test_fixed_host_adapter_must_bind_declared_host(tmp_path):
    route = crossover_route(topology="fixed_host", default_host_source_id="host")
    route["claims"][0]["extensions"]["host_source_id"] = "guest"

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("host_source_id" in error and "fixed_host" in error for error in errors)


def test_crossover_constitution_topics_must_cover_derived_payload_topics(tmp_path):
    route = crossover_route(
        topology="fixed_host",
        default_host_source_id="host",
        payload_kinds=["knowledge"],
    )
    route["claims"][1]["extensions"]["topics"].remove("信息传播")

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("信息传播" in error and "topics" in error for error in errors)


def test_fusion_world_requires_world_rule_priority_claim(tmp_path):
    route = crossover_route(topology="fusion_world", default_host_source_id=None)
    route["claims"] = [
        claim
        for claim in route["claims"]
        if claim["extensions"]["semantic_type"] != "世界规则优先级"
    ]

    assert any(
        "世界规则优先级" in error
        for error in validate_crossover(crossover_config(tmp_path), route)
    )


@pytest.mark.parametrize(
    ("extensions", "expected"),
    [
        (None, "卷宿主世界"),
        ({"volume_ids": [], "host_source_id": "host"}, "volume_ids"),
        ({"volume_ids": [""], "host_source_id": "host"}, "volume_ids"),
        ({"volume_ids": ["vol001", 2], "host_source_id": "host"}, "volume_ids"),
        ({"volume_ids": ["vol001"], "host_source_id": "unknown"}, "host_source_id"),
    ],
)
def test_sequential_worlds_validates_every_volume_host_claim(tmp_path, extensions, expected):
    route = crossover_route(topology="sequential_worlds", default_host_source_id=None)
    route["claims"] = [
        claim
        for claim in route["claims"]
        if claim["extensions"]["semantic_type"] != "卷宿主世界"
    ]
    if extensions is not None:
        route["claims"].append(
            semantic_claim("route:bad_volume_host", "卷宿主世界", extensions=extensions)
        )

    assert any(expected in error for error in validate_crossover(crossover_config(tmp_path), route))


def test_sequential_worlds_requires_explicit_route_volume_scope(tmp_path):
    route = crossover_route(topology="sequential_worlds", default_host_source_id=None)
    route["extensions"]["crossover"].pop("volume_ids")

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("extensions.crossover.volume_ids" in error for error in errors)


def test_sequential_worlds_rejects_missing_declared_volume_host(tmp_path):
    route = crossover_route(topology="sequential_worlds", default_host_source_id=None)
    route["extensions"]["crossover"]["volume_ids"] = ["vol001", "vol002"]

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("vol002" in error and "exactly one" in error for error in errors)


def test_sequential_worlds_rejects_duplicate_volume_host_assignment(tmp_path):
    route = crossover_route(topology="sequential_worlds", default_host_source_id=None)
    route["claims"].append(
        semantic_claim(
            "route:duplicate_volume_host",
            "卷宿主世界",
            extensions={"volume_ids": ["vol001"], "host_source_id": "guest"},
        )
    )

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("vol001" in error and "exactly one" in error for error in errors)


def test_sequential_adapter_volume_and_host_must_match_assignments(tmp_path):
    route = crossover_route(topology="sequential_worlds", default_host_source_id=None)
    route["claims"][0]["extensions"].update(
        {"volume_ids": ["vol001", "vol002"], "host_source_id": "guest"}
    )

    errors = validate_crossover(crossover_config(tmp_path), route)

    assert any("volume_ids" in error and "declared" in error for error in errors)
    assert any("host_source_id" in error and "卷宿主世界" in error for error in errors)


def test_non_crossover_routes_do_not_require_topology(tmp_path):
    single_source = crossover_config(tmp_path, continuity_mode="canon_divergent")
    single_source.data["fanfiction"]["sources"] = single_source.data["fanfiction"]["sources"][:1]
    multi_source_without_cross_elements = crossover_config(
        tmp_path,
        continuity_mode="canon_divergent",
        cross_source_elements=False,
    )

    assert validate_crossover(single_source, {"extensions": {}, "claims": []}) == []
    assert validate_crossover(
        multi_source_without_cross_elements, {"extensions": {}, "claims": []}
    ) == []


def test_multiple_sources_with_actual_cross_elements_trigger_topology(tmp_path):
    config = crossover_config(tmp_path, continuity_mode="canon_divergent")

    errors = validate_crossover(config, {"extensions": {}, "claims": []})

    assert any("extensions.crossover" in error for error in errors)


def test_full_route_contract_rejects_legacy_crossover_without_topology(
    current_contract_project,
):
    project = current_contract_project
    route, _route_path = install_route(project)
    engine_path = project["root"] / "10_bible" / "fanfiction" / "story_engine.json"
    engine = json.loads(engine_path.read_text(encoding="utf-8"))
    engine_sha = sha256(engine_path.read_bytes()).hexdigest()
    project["config"].data["fanfiction"]["continuity_mode"] = "crossover"
    project["config"].data["fanfiction"]["sources"].append(
        {
            "source_id": "guest",
            "title": "来访作品",
            "allowed_elements": ["abilities"],
        }
    )
    legacy = deepcopy(route)
    legacy["extensions"]["continuity_mode"] = "crossover"
    legacy["extensions"].pop("crossover", None)
    legacy = reapprove(legacy)
    source_canon = contracts.CurrentFanfictionDocument(
        project["canon"],
        path=project["canon_path"],
        digest=project["canon_sha"],
    )
    story_engine = contracts.CurrentFanfictionDocument(
        engine,
        path=engine_path,
        digest=engine_sha,
    )
    errors: list[str] = []

    contracts.validate_fanfiction_route_contract(
        project["config"],
        project["root"],
        legacy,
        errors,
        require_approved=True,
        _source_canon=source_canon,
        _story_engine=story_engine,
    )

    assert any("extensions.crossover" in error for error in errors), errors


@pytest.fixture
def current_contract_project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    source = {
        "source_id": "classic",
        "title": "原作",
        "creator": "作者",
        "canon_cutoff": "第一卷末",
        "allowed_elements": ["characters", "world", "timeline"],
    }
    config = ConfigDocument(
        data={
            "creation": {"mode": "fanfiction"},
            "project": {"root_dir": str(root)},
            "length": {
                "metric": "content_characters_v1",
                "target_total_characters": 100_000,
                "completion_tolerance": [0.9, 1.1],
                "chapter": {
                    "target_characters": 3_000,
                    "soft_min": 2_500,
                    "soft_max": 3_500,
                    "hard_min": 2_000,
                    "hard_max": 4_000,
                },
                "volume": {"target_characters": 50_000},
                "planning": {
                    "mode": "rolling",
                    "detailed_horizon": 10,
                    "refill_threshold": 3,
                },
            },
            "fanfiction": {
                "continuity_mode": "canon_divergent",
                "sources": [source],
            },
        },
        path=root / "project.yaml",
        sources=(),
    )
    excerpt = "林舟先验证青铜门规则。"
    evidence = {
        "schema": EVIDENCE_REFERENCE_SCHEMA,
        "evidence_id": "evidence:classic",
        "item_id": "item:classic",
        "asset_id": "asset:classic",
        "segment_id": "segment:classic",
        "locator": {"kind": "line", "start": 1, "end": 1},
        "excerpt": excerpt,
        "excerpt_sha256": sha256(excerpt.encode("utf-8")).hexdigest(),
    }
    project_contract = {
        "setting": {"作品ID": "work:classic"},
        "binding_sha256": "a" * 64,
        "coverage_sha256": "b" * 64,
        "binding": {
            "items": [
                {
                    "item_id": "item:classic",
                    "bundle_sha256": "c" * 64,
                    "normalization_sha256": "d" * 64,
                    "extraction_sha256": "e" * 64,
                }
            ]
        },
        "evidence": {evidence["evidence_id"]: evidence},
    }
    project_contracts = {"classic": project_contract}
    monkeypatch.setattr(
        contracts,
        "project_source_contract",
        lambda _config, source_id: project_contracts[source_id],
        raising=False,
    )
    source_contract_record = {
        "source_id": "classic",
        "work_id": "work:classic",
        "title": "原作",
        "creator": "作者",
        "canon_cutoff": "第一卷末",
        "binding_sha256": "a" * 64,
        "coverage_plan_sha256": "b" * 64,
        "item_bindings": [
            {
                "item_id": "item:classic",
                "bundle_sha256": "c" * 64,
                "normalization_sha256": "d" * 64,
                "extraction_sha256": "e" * 64,
            }
        ],
    }
    canon = approve(
        build_semantic_document(
            document_id="sem:source_canon",
            document_type="项目原著基线Canon候选",
            title="当前项目原著基线",
            scope={"kind": "project", "project": root.name},
            continuity="原著基线",
            body="经证据固定的项目原著基线。",
            claims=[
                semantic_claim(
                    "classic:canon",
                    "人物",
                    evidence_refs=[evidence["evidence_id"]],
                    extensions={"source_id": "classic"},
                )
            ],
            evidence_references=[evidence],
            extensions={
                "task_type": "fanfiction_canon",
                "continuity_mode": "canon_divergent",
                "source_contracts": [source_contract_record],
            },
        )
    )
    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon_sha = write_document(canon_path, canon)
    return {
        "config": config,
        "root": root,
        "canon": canon,
        "canon_path": canon_path,
        "canon_sha": canon_sha,
        "project_contract": project_contract,
        "project_contracts": project_contracts,
    }


def configure_crossover_project(project: dict) -> None:
    """Promote the reusable current-contract fixture to a valid two-source crossover."""

    guest_source = {
        "source_id": "guest",
        "title": "来访作品",
        "creator": "来访作者",
        "canon_cutoff": "第二卷末",
        "allowed_elements": ["characters", "abilities"],
    }
    guest_contract = {
        "setting": {"作品ID": "work:guest"},
        "binding_sha256": "1" * 64,
        "coverage_sha256": "2" * 64,
        "binding": {"items": []},
        "evidence": {},
    }
    configured = project["config"].data["fanfiction"]
    configured["continuity_mode"] = "crossover"
    configured["sources"].append(guest_source)
    project["project_contracts"]["guest"] = guest_contract

    canon = deepcopy(project["canon"])
    canon["continuity"] = "跨界连续性"
    canon["extensions"]["continuity_mode"] = "crossover"
    canon["extensions"]["source_contracts"] = contracts.current_fanfiction_source_contracts(
        project["config"]
    )
    canon = reapprove(canon)
    project["canon"] = canon
    project["canon_sha"] = write_document(project["canon_path"], canon)


def install_story_engine(project: dict) -> tuple[dict, str]:
    semantic_types = (
        "唯一初始变量",
        "独立长期目标",
        "可持续阻力",
        "原著人物自主性",
        "原作后续故事来源",
        "主角与原著关系",
        "读者识别承诺",
        "原创主线承诺",
    )
    continuity_mode = project["config"].data["fanfiction"]["continuity_mode"]
    engine = approve(
        build_semantic_document(
            document_id="sem:story_engine",
            document_type="同人故事发动机",
            title="当前故事发动机",
            scope={"kind": "project", "project": project["root"].name},
            continuity="跨界连续性" if continuity_mode == "crossover" else "原作分歧",
            body="保持原作识别度并持续生成原创主线。",
            claims=[
                semantic_claim(f"engine:claim_{index}", semantic_type)
                for index, semantic_type in enumerate(semantic_types)
            ],
            extensions={
                "task_type": "fanfiction_story_engine",
                "continuity_mode": continuity_mode,
                "route_family": "hybrid",
                "source_canon_sha256": project["canon_sha"],
            },
        )
    )
    path = project["root"] / "10_bible" / "fanfiction" / "story_engine.json"
    return engine, write_document(path, engine)


def install_route(project: dict) -> tuple[dict, Path]:
    _engine, engine_sha = install_story_engine(project)
    continuity_mode = project["config"].data["fanfiction"]["continuity_mode"]
    claims = [
        semantic_claim("route:divergence", "初始分歧"),
        semantic_claim("route:entry", "故事切入点"),
        semantic_claim("route:knowledge", "人物知识边界"),
        semantic_claim("route:owner", "原著人物职责"),
        semantic_claim("route:first", "分歧后果"),
        semantic_claim("route:second", "分歧后果"),
        semantic_claim(
            "route:event",
            "原著事件命运",
            extensions={
                "disposition": "结果改变",
                "depends_on_claims": ["route:divergence"],
                "responsibility_owner_ids": ["route:owner"],
                "first_order_effect_claim_ids": ["route:first"],
                "second_order_effect_claim_ids": ["route:second"],
            },
        ),
    ]
    crossover_extensions: dict = {}
    if continuity_mode == "crossover":
        crossover = crossover_route(
            topology="fixed_host",
            default_host_source_id="classic",
            payload_kinds=["ability"],
        )
        crossover_extensions = crossover["extensions"]
        claims.extend(crossover["claims"])
    route_candidate = build_semantic_document(
        document_id="sem:route",
        document_type="同人路线设计候选",
        title="当前同人路线",
        scope={"kind": "project", "project": project["root"].name},
        continuity="跨界连续性" if continuity_mode == "crossover" else "原作分歧",
        body="从原著基线产生可追责的分歧后果。",
        claims=claims,
        extensions={
            "task_type": "fanfiction_design",
            "continuity_mode": continuity_mode,
            "source_canon_sha256": project["canon_sha"],
            "story_engine_sha256": engine_sha,
            "future_knowledge_used": False,
            **crossover_extensions,
        },
    )
    target_path = project["root"] / "50_workbench" / "route_candidate.json"
    target_sha = write_document(target_path, route_candidate)
    review = approve(
        build_semantic_document(
            document_id="sem:route_review",
            document_type="同人路线独立复核",
            title="当前路线复核",
            scope={"kind": "project", "project": project["root"].name},
            continuity="跨界连续性" if continuity_mode == "crossover" else "原作分歧",
            body="独立复核通过。",
            extensions={
                "task_type": "fanfiction_design_review",
                "verdict": "pass",
                "review_target_path": target_path.relative_to(project["root"]).as_posix(),
                "review_target_sha256": target_sha,
                "source_canon_sha256": project["canon_sha"],
                "story_engine_sha256": engine_sha,
            },
        )
    )
    review_path = project["root"] / "50_workbench" / "route_review.json"
    review_sha = write_document(review_path, review)
    route_candidate["extensions"]["independent_review"] = {
        "review_path": review_path.relative_to(project["root"]).as_posix(),
        "review_sha256": review_sha,
        "review_artifact_id": review["artifact"]["artifact_id"],
        "reviewer_role": "fanfiction_route_reviewer",
        "verdict": "pass",
        "reviewed_route_projection_sha256": reviewed_route_projection_sha256(route_candidate),
    }
    route = approve(route_candidate)
    route_path = project["root"] / "10_bible" / "fanfiction" / "fanfiction_bible.json"
    write_document(route_path, route)
    return route, route_path


def bind_review_to_route(project: dict, route: dict, review: dict, review_sha: str) -> dict:
    changed = deepcopy(route)
    binding = changed["extensions"]["independent_review"]
    binding["review_sha256"] = review_sha
    artifact = review.get("artifact")
    if isinstance(artifact, dict) and isinstance(artifact.get("artifact_id"), str):
        binding["review_artifact_id"] = artifact["artifact_id"]
    canonical = reapprove(changed)
    route_path = project["root"] / "10_bible" / "fanfiction" / "fanfiction_bible.json"
    write_document(route_path, canonical)
    return canonical


def corrupt_canonical_crossover_route(
    project: dict,
    *,
    corruption: str = "legacy",
) -> None:
    route, route_path = install_route(project)
    corrupted = deepcopy(route)
    if corruption == "legacy":
        corrupted["extensions"].pop("crossover")
    elif corruption == "transfers":
        corrupted["extensions"]["crossover"]["transfers"][0]["payload_kinds"] = []
    else:
        raise AssertionError(f"unknown crossover corruption: {corruption}")
    write_document(route_path, reapprove(corrupted))


def test_complete_current_chain_rejects_legacy_crossover_route_and_review_target(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    route, _route_path = install_route(project)
    current = contracts.load_current_fanfiction_documents(project["config"], project["root"])
    assert current.route["extensions"]["crossover"]["topology"] == "fixed_host"

    review_path = current.independent_review.path
    review = json.loads(review_path.read_text(encoding="utf-8"))
    target_path = current.review_target.path
    legacy_target = json.loads(target_path.read_text(encoding="utf-8"))
    legacy_target["extensions"].pop("crossover")
    target_sha = write_document(target_path, seal_semantic_document(legacy_target))
    review["extensions"]["review_target_sha256"] = target_sha
    review = reapprove(review)
    review_sha = write_document(review_path, review)
    bind_review_to_route(project, route, review, review_sha)

    with pytest.raises(contracts.FanfictionContractError, match="extensions.crossover"):
        contracts.load_current_fanfiction_documents(project["config"], project["root"])


def test_fanfiction_design_apply_rejects_legacy_crossover_candidate(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    _route, _route_path = install_route(project)
    task = create_intelligence_task(project["config"], task_type="fanfiction_design")
    candidate_path = project["root"] / task.candidate_file
    candidate = json.loads(
        (project["root"] / "50_workbench" / "route_candidate.json").read_text(
            encoding="utf-8"
        )
    )
    candidate["extensions"].pop("crossover")
    for field in ("continuity_mode", "source_canon_sha256", "story_engine_sha256"):
        candidate["extensions"].pop(field)
    write_document(candidate_path, seal_semantic_document(candidate))
    control = validate_production_agent_result(
        project["root"],
        load_manifest(project["root"], task.task_id),
        result_file=candidate_path,
    )
    assert control.ok, control.normalization.errors

    with pytest.raises(ValueError, match="extensions.crossover"):
        apply_intelligence_candidate(
            project["config"],
            task_type="fanfiction_design",
            file_path=candidate_path,
            approved_by="human",
        )


def test_downstream_intelligence_gate_and_editorial_reject_legacy_crossover(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    corrupt_canonical_crossover_route(project)
    draft = project["root"] / "40_manuscript" / "draft" / "ch001.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("林舟验证来访能力的代价。", encoding="utf-8")

    with pytest.raises(ValueError, match="extensions.crossover"):
        create_intelligence_task(project["config"], task_type="book_design")
    with pytest.raises(GateError, match="extensions.crossover"):
        semantic_review_task(project["config"], chapter_number=1)
    with pytest.raises(ValueError, match="extensions.crossover"):
        editorial_review(project["config"], chapter_number=1)

    assert not (project["root"] / "50_workbench" / "intelligence_tasks").exists()
    assert not (project["root"] / "50_workbench" / "gate_artifacts").exists()
    assert not (project["root"] / "50_workbench" / "editorial_reviews").exists()


def test_planning_apply_rejects_legacy_crossover_without_canonical_or_transaction_pollution(
    current_contract_project,
):
    from tests.test_v010_planning import evidence_review, planning_bundle, write_json

    project = current_contract_project
    configure_crossover_project(project)
    route, _route_path = install_route(project)
    root = project["root"]
    bundle = planning_bundle()
    bundle["active_volume_plan"]["fanfiction_projection"] = {
        "body": "当前卷只投影已经批准的跨界路线。",
        "claim_refs": [route["claims"][0]["claim_id"]],
    }
    bundle_path = write_json(root / "50_workbench" / "planning" / "bundle.json", bundle)
    subject_relative = bundle_path.relative_to(root).as_posix()
    review_path = write_json(
        root / "50_workbench" / "planning" / "review.json",
        evidence_review(subject_relative),
    )
    application_path = write_json(
        root / "50_workbench" / "planning" / "application.json",
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
        root / "50_workbench" / "planning" / "approval.json",
        build_human_planning_approval(
            root,
            application_path=application_path,
            decision="approve",
            reason="跨界规划语义与节点均已人工确认。",
            approved_by="human",
        ),
    )
    decisions_path = write_json(
        root / "50_workbench" / "planning" / "node-decisions.json",
        build_human_node_decisions(
            root,
            bundle_path=bundle_path,
            decisions=[
                {
                    "node_id": node["node_id"],
                    "decision": "approve",
                    "adjustment": "",
                    "reason": "保留当前跨界因果节点。",
                }
                for table in bundle["plot_node_tables"]
                for node in table["nodes"]
                if node["node_kind"] == "state_change"
            ],
            decided_by="human",
        ),
    )
    corrupt_canonical_crossover_route(project)
    protected_roots = [root / "20_outline", root / "30_state", root / "70_runtime" / "transactions"]
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for protected_root in protected_roots
        if protected_root.exists()
        for path in protected_root.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError, match="extensions.crossover"):
        apply_planning_bundle(
            project["config"],
            bundle_path=bundle_path,
            application_path=application_path,
            approval_path=approval_path,
            node_decisions_path=decisions_path,
            approved_by="human",
        )

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for protected_root in protected_roots
        if protected_root.exists()
        for path in protected_root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_submit_agent_draft_rejects_route_drift_before_any_project_write(
    current_contract_project,
    monkeypatch,
):
    project = current_contract_project
    configure_crossover_project(project)
    corrupt_canonical_crossover_route(project)
    root = project["root"]
    source = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("林舟验证来访能力的代价。", encoding="utf-8")
    candidate_task = {
        "task_id": "chapter_write:ch001:v5",
        "task_type": "chapter_write",
        "status": "awaiting_agent",
    }
    monkeypatch.setattr(
        "longform_engine.orchestration.pipeline.prose_naturalness_candidate_submission_guard",
        lambda *_args, **_kwargs: {"allowed": True, "required": False},
    )
    monkeypatch.setattr(
        "longform_engine.orchestration.pipeline.resolve_candidate_task",
        lambda *_args, **_kwargs: candidate_task,
    )
    monkeypatch.setattr(
        "longform_engine.orchestration.pipeline.list_manifests",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "longform_engine.orchestration.pipeline.ensure_candidate_snapshot",
        lambda *_args, **_kwargs: source,
    )
    monkeypatch.setattr(
        "longform_engine.orchestration.pipeline.update_task_status",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "longform_engine.orchestration.pipeline.supersede_other_candidate_tasks",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "longform_engine.chapter_coedit.record_coedit_submission",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "longform_engine.human_review_consultation.mark_stale_human_consultations",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "longform_engine.orchestration.pipeline.gate_check",
        lambda *_args, **_kwargs: contracts.load_current_fanfiction_documents(
            project["config"], root
        ),
    )
    before = project_file_snapshot(root)

    with pytest.raises(ValueError, match="extensions.crossover"):
        submit_agent_draft(
            project["config"],
            chapter_number=1,
            file_path=source,
            agent="codex",
        )

    assert project_file_snapshot(root) == before


def test_finalize_chapter_rejects_invalid_transfers_without_project_write(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    corrupt_canonical_crossover_route(project, corruption="transfers")
    root = project["root"]
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("林舟验证来访能力的代价。", encoding="utf-8")
    before = project_file_snapshot(root)

    with pytest.raises(ValueError, match="payload_kinds"):
        finalize_chapter(project["config"], chapter_number=1, approved_by="human")

    assert project_file_snapshot(root) == before


@pytest.mark.parametrize(
    ("corruption", "expected"),
    [("legacy", "extensions.crossover"), ("transfers", "payload_kinds")],
)
def test_intelligence_validation_rechecks_current_route_before_report_or_status_write(
    current_contract_project,
    corruption,
    expected,
):
    project = current_contract_project
    configure_crossover_project(project)
    install_route(project)
    task = create_intelligence_task(project["config"], task_type="book_design")
    candidate = project["root"] / task.candidate_file
    candidate.write_text("不完整设计候选。", encoding="utf-8")
    corrupt_canonical_crossover_route(project, corruption=corruption)
    before = project_file_snapshot(project["root"])

    with pytest.raises(ValueError, match=expected):
        validate_intelligence_candidate(
            project["config"],
            task_type="book_design",
            file_path=candidate,
        )

    assert project_file_snapshot(project["root"]) == before


def test_compile_delta_validation_rechecks_current_route_before_report_or_status_write(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    corrupt_canonical_crossover_route(project, corruption="transfers")
    root = project["root"]
    document = root / "50_workbench" / "intelligence_candidates" / "book_design.md"
    delta = root / "50_workbench" / "intelligence_candidates" / "book_design.delta.json"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("# 设计文档\n", encoding="utf-8")
    delta.write_text("{}\n", encoding="utf-8")
    before = project_file_snapshot(root)

    with pytest.raises(ValueError, match="payload_kinds"):
        validate_design_compile_delta(
            project["config"],
            task_type="book_design",
            document_path=document,
            delta_path=delta,
        )

    assert project_file_snapshot(root) == before


def test_editorial_submit_rechecks_current_route_before_acceptance_or_status_write(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    corrupt_canonical_crossover_route(project)
    root = project["root"]
    result = (
        root
        / "50_workbench"
        / "editorial_reviews"
        / "results"
        / "ch001.planning_chief_editor.json"
    )
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text("{}\n", encoding="utf-8")
    before = project_file_snapshot(root)

    with pytest.raises(ValueError, match="extensions.crossover"):
        editorial_submit_review(
            project["config"],
            chapter_number=1,
            role="planning_chief_editor",
            file_path=result,
        )

    assert project_file_snapshot(root) == before


def test_editorial_aggregate_rechecks_current_route_before_aggregate_or_applied_write(
    current_contract_project,
):
    project = current_contract_project
    configure_crossover_project(project)
    corrupt_canonical_crossover_route(project, corruption="transfers")
    before = project_file_snapshot(project["root"])

    with pytest.raises(ValueError, match="payload_kinds"):
        editorial_aggregate(project["config"], chapter_number=1)

    assert project_file_snapshot(project["root"]) == before


@pytest.mark.parametrize(
    "entrypoint",
    ["generate_beat_sheet", "batch_write", "auto_write_plan", "auto_write_run", "auto_write_report"],
)
def test_related_production_lifecycle_entrypoints_reject_route_drift_before_write(
    current_contract_project,
    entrypoint,
):
    project = current_contract_project
    configure_crossover_project(project)
    corrupt_canonical_crossover_route(project)
    root = project["root"]
    card = root / "20_outline" / "chapter_cards" / "ch001.json"
    card.parent.mkdir(parents=True, exist_ok=True)
    card.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "title": "第一章",
                "chapter_duty": "验证跨界代价。",
                "event_recommendation": {"recommended": []},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = {
        "generate_beat_sheet": lambda: generate_beat_sheet(
            project["config"], chapter_number=1
        ),
        "batch_write": lambda: batch_write(project["config"], chapters=1),
        "auto_write_plan": lambda: auto_write_plan(project["config"]),
        "auto_write_run": lambda: auto_write_run(project["config"]),
        "auto_write_report": lambda: auto_write_report(project["config"]),
    }
    before = project_file_snapshot(root)

    with pytest.raises(ValueError, match="extensions.crossover"):
        calls[entrypoint]()

    assert project_file_snapshot(root) == before


def event_fate_claim(route: dict) -> dict:
    return next(
        claim
        for claim in route["claims"]
        if claim["extensions"].get("semantic_type") == "原著事件命运"
    )


@pytest.mark.parametrize(
    "field",
    contracts.EVENT_CAUSAL_REFERENCE_FIELDS,
)
def test_event_causal_reference_fields_require_non_empty_string_lists(
    current_contract_project, field
):
    project = current_contract_project
    route, _route_path = install_route(project)
    missing = object()
    invalid_values = (missing, [], [""], ["route:owner", 7], "route:owner")

    for value in invalid_values:
        candidate = deepcopy(route)
        event = event_fate_claim(candidate)
        if value is missing:
            event["extensions"].pop(field)
        else:
            event["extensions"][field] = value
        errors: list[str] = []

        contracts.validate_event_disposition_claims(
            project["config"], project["root"], candidate, errors
        )

        assert any(field in error and "non-empty string list" in error for error in errors), (
            value,
            errors,
        )


def test_event_causal_references_accept_route_source_and_story_claims(
    current_contract_project,
):
    project = current_contract_project
    route, _route_path = install_route(project)
    event_fate_claim(route)["extensions"].update(
        {
            "responsibility_owner_ids": ["route:owner"],
            "first_order_effect_claim_ids": ["classic:canon"],
            "second_order_effect_claim_ids": ["engine:claim_3"],
        }
    )
    errors: list[str] = []

    contracts.validate_event_disposition_claims(
        project["config"], project["root"], route, errors
    )

    assert errors == []


@pytest.mark.parametrize("field", contracts.EVENT_CAUSAL_REFERENCE_FIELDS)
def test_event_causal_references_reject_unknown_claims(current_contract_project, field):
    project = current_contract_project
    route, _route_path = install_route(project)
    event_fate_claim(route)["extensions"][field] = ["outside:unstable"]
    errors: list[str] = []

    contracts.validate_event_disposition_claims(
        project["config"], project["root"], route, errors
    )

    assert any(field in error and "current stable claims" in error for error in errors)


@pytest.mark.parametrize("document_name", ["source_canon", "story_engine"])
def test_event_causal_references_reject_unapproved_canonical_targets(
    current_contract_project, document_name
):
    project = current_contract_project
    route, _route_path = install_route(project)
    path = (
        project["canon_path"]
        if document_name == "source_canon"
        else project["root"] / "10_bible" / "fanfiction" / "story_engine.json"
    )
    approved = json.loads(path.read_text(encoding="utf-8"))
    candidate = deepcopy(approved)
    candidate["artifact"]["state"] = "candidate"
    candidate["extensions"].pop("human_decision", None)
    candidate["extensions"].pop("approved_candidate_sha256", None)
    write_document(path, seal_semantic_document(candidate))
    event_fate_claim(route)["extensions"]["responsibility_owner_ids"] = [
        approved["claims"][0]["claim_id"]
    ]
    errors: list[str] = []

    contracts.validate_event_disposition_claims(
        project["config"], project["root"], route, errors
    )

    assert any("semantic document must be approved" in error for error in errors)


def test_current_source_canon_loads_full_domain_contract_once(
    current_contract_project, monkeypatch
):
    project = current_contract_project
    original = Path.read_bytes
    reads = 0

    def counted(path: Path) -> bytes:
        nonlocal reads
        if path == project["canon_path"]:
            reads += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)

    loaded = contracts.load_current_fanfiction_source_canon(
        project["config"], project["root"]
    )

    assert loaded["artifact"]["state"] == "approved"
    assert reads == 1


def test_context_compiler_reads_each_current_chain_artifact_once(
    current_contract_project, monkeypatch
):
    project = current_contract_project
    route, route_path = install_route(project)
    targets = {
        project["canon_path"],
        project["root"] / "10_bible" / "fanfiction" / "story_engine.json",
        route_path,
        project["root"] / route["extensions"]["independent_review"]["review_path"],
    }
    original = Path.read_bytes
    reads = {path: 0 for path in targets}

    def counted(path: Path) -> bytes:
        if path in reads:
            reads[path] += 1
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", counted)

    compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract={"chapter_number": 1},
        chapter_card={"title": "单次读取边界"},
        character_packet={},
    )

    assert reads == {path: 1 for path in targets}


@pytest.mark.parametrize("drift", ["configured_source", "source_contract", "evidence_locator"])
def test_current_source_canon_rejects_project_contract_drift(
    current_contract_project, monkeypatch, drift
):
    project = current_contract_project
    if drift == "configured_source":
        project["config"].data["fanfiction"]["sources"][0]["source_id"] = "renamed"
    else:
        changed = deepcopy(project["project_contract"])
        if drift == "source_contract":
            changed["binding_sha256"] = "f" * 64
        else:
            changed["evidence"]["evidence:classic"]["locator"] = {
                "kind": "line",
                "start": 99,
                "end": 99,
            }
        monkeypatch.setattr(
            contracts,
            "project_source_contract",
            lambda _config, _source_id: changed,
            raising=False,
        )

    with pytest.raises(contracts.FanfictionContractError, match="source_canon") as exc_info:
        contracts.load_current_fanfiction_source_canon(project["config"], project["root"])

    assert exc_info.value.code == "stale"


@pytest.mark.parametrize(
    ("condition", "code"),
    [
        ("missing", "missing"),
        ("invalid_json", "invalid_json"),
        ("invalid_utf8", "invalid_utf8"),
        ("unreadable", "unreadable"),
    ],
)
def test_canonical_read_errors_report_path_and_code(tmp_path, monkeypatch, condition, code):
    root = tmp_path / "project"
    path = root / "10_bible" / "fanfiction" / "source_canon.json"
    path.parent.mkdir(parents=True)
    if condition == "invalid_json":
        path.write_bytes(b"{")
    elif condition == "invalid_utf8":
        path.write_bytes(b"\xff")
    elif condition == "unreadable":
        path.write_bytes(b"{}")
        original = Path.read_bytes

        def unreadable(target: Path) -> bytes:
            if target == path:
                raise PermissionError("denied")
            return original(target)

        monkeypatch.setattr(Path, "read_bytes", unreadable)
    config = ConfigDocument(
        data={"fanfiction": {"continuity_mode": "canon_divergent", "sources": []}},
        path=root / "project.yaml",
        sources=(),
    )

    with pytest.raises(contracts.FanfictionContractError) as exc_info:
        contracts.load_current_fanfiction_source_canon(config, root)

    assert exc_info.value.code == code
    assert exc_info.value.path == path
    assert str(path) in str(exc_info.value)


@pytest.mark.parametrize("drift", ["missing", "hash", "verdict"])
def test_current_route_requires_current_independent_pass_review(
    current_contract_project, drift
):
    project = current_contract_project
    route, route_path = install_route(project)
    review = route["extensions"]["independent_review"]
    review_path = project["root"] / review["review_path"]
    if drift == "missing":
        review_path.unlink()
    elif drift == "hash":
        review_path.write_bytes(review_path.read_bytes() + b"\n")
    else:
        changed = deepcopy(route)
        changed["extensions"]["independent_review"]["verdict"] = "reject"
        write_document(route_path, seal_semantic_document(changed))

    with pytest.raises(contracts.FanfictionContractError) as exc_info:
        contracts.load_current_fanfiction_route(project["config"], project["root"])

    assert exc_info.value.code in {"missing", "stale", "invalid"}
    assert "independent review" in str(exc_info.value)


@pytest.mark.parametrize(
    ("malformation", "expected_status"),
    [
        ("artifact_null", "invalid"),
        ("extensions_list", "invalid"),
        ("missing_task_type", "invalid"),
        ("wrong_task_type", "invalid"),
        ("wrong_document_type", "invalid"),
        ("wrong_source_hash", "stale"),
        ("wrong_story_hash", "stale"),
        ("wrong_target_path", "missing"),
        ("wrong_target_hash", "stale"),
    ],
)
def test_current_review_contract_rejects_malformed_or_stale_documents(
    current_contract_project, malformation, expected_status
):
    project = current_contract_project
    route, _route_path = install_route(project)
    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract={"chapter_number": 1},
        chapter_card={"title": "复核合同"},
        character_packet={},
    )
    write_fanfiction_context_bundle(project["root"], bundle)
    review_path = project["root"] / route["extensions"]["independent_review"]["review_path"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    if malformation == "artifact_null":
        review["artifact"] = None
    elif malformation == "extensions_list":
        review["extensions"] = []
    else:
        extensions = review["extensions"]
        if malformation == "missing_task_type":
            extensions.pop("task_type")
        elif malformation == "wrong_task_type":
            extensions["task_type"] = "fanfiction_design"
        elif malformation == "wrong_document_type":
            review["document_type"] = "同人路线设计候选"
        elif malformation == "wrong_source_hash":
            extensions["source_canon_sha256"] = "f" * 64
        elif malformation == "wrong_story_hash":
            extensions["story_engine_sha256"] = "f" * 64
        elif malformation == "wrong_target_path":
            extensions["review_target_path"] = "50_workbench/missing_route.json"
        else:
            extensions["review_target_sha256"] = "f" * 64
        review = reapprove(review)
    review_sha = write_document(review_path, review)
    bind_review_to_route(project, route, review, review_sha)

    with pytest.raises(contracts.FanfictionContractError) as exc_info:
        contracts.load_current_fanfiction_route(project["config"], project["root"])
    with pytest.raises(FanfictionContextError, match="fanfiction_contract"):
        compile_fanfiction_context(
            project["config"],
            chapter_number=1,
            chapter_contract={"chapter_number": 1},
            chapter_card={"title": "畸形复核"},
            character_packet={},
        )

    assert exc_info.value.code == expected_status
    context_status = fanfiction_context_status(project["config"], chapter_number=1)
    event_status = event_disposition_status(project["config"])
    assert context_status["status"] == expected_status
    assert event_status["route_status"] == expected_status
    assert context_status["diagnostics"]
    assert event_status["diagnostics"]


def test_context_bundle_records_independent_review_provenance(current_contract_project):
    project = current_contract_project
    route, _route_path = install_route(project)

    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract={"chapter_number": 1},
        chapter_card={"title": "复核来源"},
        character_packet={},
    )

    review_path = route["extensions"]["independent_review"]["review_path"]
    review = json.loads((project["root"] / review_path).read_text(encoding="utf-8"))
    target_path = review["extensions"]["review_target_path"]
    provenance = {item["path"]: item["sha256"] for item in bundle["source_files"]}
    assert provenance[review_path] == route["extensions"]["independent_review"]["review_sha256"]
    assert provenance[target_path] == review["extensions"]["review_target_sha256"]
    current = contracts.load_current_fanfiction_documents(project["config"], project["root"])
    assert current.review_target.path == project["root"] / target_path
    assert current.review_target.sha256 == review["extensions"]["review_target_sha256"]


def test_current_review_target_must_match_current_canonical_route_semantics(
    current_contract_project,
):
    project = current_contract_project
    route, route_path = install_route(project)
    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract={"chapter_number": 1},
        chapter_card={"title": "路线 A"},
        character_packet={},
    )
    write_fanfiction_context_bundle(project["root"], bundle)
    different_route = deepcopy(route)
    different_route["title"] = "语义不同的路线 B"
    different_route["body"] = "路线 B 改写了已复核路线的核心因果语义。"
    different_route["claims"][0]["statement"] = "路线 B 使用另一项初始分歧。"
    write_document(route_path, reapprove(different_route))

    with pytest.raises(contracts.FanfictionContractError) as exc_info:
        contracts.load_current_fanfiction_route(project["config"], project["root"])
    with pytest.raises(FanfictionContextError, match="fanfiction_contract"):
        compile_fanfiction_context(
            project["config"],
            chapter_number=1,
            chapter_contract={"chapter_number": 1},
            chapter_card={"title": "路线 B"},
            character_packet={},
        )

    assert exc_info.value.code == "stale"
    status = fanfiction_context_status(project["config"], chapter_number=1)
    assert status["status"] == "stale"
    assert status["diagnostics"]["contract_errors"]


def test_current_review_target_must_be_a_valid_fanfiction_route(current_contract_project):
    project = current_contract_project
    route, _route_path = install_route(project)
    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract={"chapter_number": 1},
        chapter_card={"title": "合法路线"},
        character_packet={},
    )
    write_fanfiction_context_bundle(project["root"], bundle)
    review_path = project["root"] / route["extensions"]["independent_review"]["review_path"]
    review = json.loads(review_path.read_text(encoding="utf-8"))
    target_path = project["root"] / review["extensions"]["review_target_path"]
    unrelated = approve(
        build_semantic_document(
            document_id="sem:unrelated_approved_document",
            document_type="普通批准语义文档",
            title="与同人路线无关",
            scope={"kind": "project", "project": project["root"].name},
            continuity="无关连续性",
            body="这是通用 approved semantic document，但不是同人路线。",
        )
    )
    target_sha = write_document(target_path, unrelated)
    review["extensions"]["review_target_sha256"] = target_sha
    review = reapprove(review)
    review_sha = write_document(review_path, review)
    bind_review_to_route(project, route, review, review_sha)

    with pytest.raises(contracts.FanfictionContractError) as exc_info:
        contracts.load_current_fanfiction_route(project["config"], project["root"])
    with pytest.raises(FanfictionContextError, match="fanfiction_contract"):
        compile_fanfiction_context(
            project["config"],
            chapter_number=1,
            chapter_contract={"chapter_number": 1},
            chapter_card={"title": "非法 target"},
            character_packet={},
        )

    assert exc_info.value.code == "invalid"
    status = fanfiction_context_status(project["config"], chapter_number=1)
    assert status["status"] == "invalid"
    assert status["diagnostics"]["contract_errors"]


@pytest.mark.parametrize(
    ("drift", "expected_status"),
    [
        ("configured_source", "stale"),
        ("review_missing", "missing"),
        ("review_replaced", "stale"),
        ("review_verdict", "invalid"),
        ("review_target_hash", "stale"),
    ],
)
def test_context_status_revalidates_complete_current_chain(
    current_contract_project, drift, expected_status
):
    project = current_contract_project
    route, _route_path = install_route(project)
    bundle = compile_fanfiction_context(
        project["config"],
        chapter_number=1,
        chapter_contract={"chapter_number": 1},
        chapter_card={"title": "状态重验"},
        character_packet={},
    )
    write_fanfiction_context_bundle(project["root"], bundle)
    review_path = project["root"] / route["extensions"]["independent_review"]["review_path"]
    if drift == "configured_source":
        project["config"].data["fanfiction"]["sources"][0]["source_id"] = "renamed"
    elif drift == "review_missing":
        review_path.unlink()
    elif drift == "review_replaced":
        review_path.write_bytes(review_path.read_bytes() + b"\n")
    else:
        review = json.loads(review_path.read_text(encoding="utf-8"))
        if drift == "review_verdict":
            review["extensions"]["verdict"] = "reject"
        else:
            review["extensions"]["review_target_sha256"] = "f" * 64
        review = reapprove(review)
        review_sha = write_document(review_path, review)
        bind_review_to_route(project, route, review, review_sha)

    context_status = fanfiction_context_status(project["config"], chapter_number=1)
    event_status = event_disposition_status(project["config"])

    assert context_status["status"] == expected_status
    assert event_status["route_status"] == expected_status
    assert context_status["diagnostics"]
    assert event_status["diagnostics"]


@pytest.mark.parametrize(
    ("drift", "status"),
    [
        ("configured_source", "stale"),
        ("source_contract", "stale"),
        ("evidence_locator", "stale"),
        ("review_missing", "missing"),
        ("review_hash", "stale"),
        ("review_verdict", "invalid"),
    ],
)
def test_context_and_event_status_block_each_current_chain_drift(
    current_contract_project, monkeypatch, drift, status
):
    project = current_contract_project
    route, route_path = install_route(project)
    if drift == "configured_source":
        project["config"].data["fanfiction"]["sources"][0]["source_id"] = "renamed"
    elif drift in {"source_contract", "evidence_locator"}:
        changed = deepcopy(project["project_contract"])
        if drift == "source_contract":
            changed["binding_sha256"] = "f" * 64
        else:
            changed["evidence"]["evidence:classic"]["locator"] = {
                "kind": "line",
                "start": 99,
                "end": 99,
            }
        monkeypatch.setattr(
            contracts,
            "project_source_contract",
            lambda _config, _source_id: changed,
        )
    else:
        review = route["extensions"]["independent_review"]
        review_path = project["root"] / review["review_path"]
        if drift == "review_missing":
            review_path.unlink()
        elif drift == "review_hash":
            review_path.write_bytes(review_path.read_bytes() + b"\n")
        else:
            changed = deepcopy(route)
            changed["extensions"]["independent_review"]["verdict"] = "reject"
            write_document(route_path, seal_semantic_document(changed))

    with pytest.raises(FanfictionContextError, match="fanfiction_contract"):
        compile_fanfiction_context(
            project["config"],
            chapter_number=1,
            chapter_contract={"chapter_number": 1},
            chapter_card={"title": "当前合同漂移"},
            character_packet={},
        )

    event_status = event_disposition_status(project["config"])
    assert event_status["route_status"] == status
    assert event_status["events"] == []
    assert event_status["diagnostics"]
