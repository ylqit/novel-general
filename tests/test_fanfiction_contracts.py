import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.config import ConfigDocument
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
    monkeypatch.setattr(
        contracts,
        "project_source_contract",
        lambda _config, source_id: project_contract
        if source_id == "classic"
        else pytest.fail(f"unexpected source {source_id}"),
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
    }


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
    engine = approve(
        build_semantic_document(
            document_id="sem:story_engine",
            document_type="同人故事发动机",
            title="当前故事发动机",
            scope={"kind": "project", "project": project["root"].name},
            continuity="原作分歧",
            body="保持原作识别度并持续生成原创主线。",
            claims=[
                semantic_claim(f"engine:claim_{index}", semantic_type)
                for index, semantic_type in enumerate(semantic_types)
            ],
            extensions={
                "task_type": "fanfiction_story_engine",
                "continuity_mode": "canon_divergent",
                "route_family": "hybrid",
                "source_canon_sha256": project["canon_sha"],
            },
        )
    )
    path = project["root"] / "10_bible" / "fanfiction" / "story_engine.json"
    return engine, write_document(path, engine)


def install_route(project: dict) -> tuple[dict, Path]:
    _engine, engine_sha = install_story_engine(project)
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
    route_candidate = build_semantic_document(
        document_id="sem:route",
        document_type="同人路线设计候选",
        title="当前同人路线",
        scope={"kind": "project", "project": project["root"].name},
        continuity="原作分歧",
        body="从原著基线产生可追责的分歧后果。",
        claims=claims,
        extensions={
            "task_type": "fanfiction_design",
            "continuity_mode": "canon_divergent",
            "source_canon_sha256": project["canon_sha"],
            "story_engine_sha256": engine_sha,
            "future_knowledge_used": False,
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
            continuity="原作分歧",
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
    provenance = {item["path"]: item["sha256"] for item in bundle["source_files"]}
    assert provenance[review_path] == route["extensions"]["independent_review"]["review_sha256"]


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
