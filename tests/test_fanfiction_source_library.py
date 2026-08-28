import json
from hashlib import sha256
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_tasks import load_manifest
from longform_engine.config import load_project_config
from longform_engine.fanfiction_sources import (
    CANON_SCHEMA,
    COVERAGE_SCHEMA,
    FanfictionSourceError,
    apply_coverage_plan,
    apply_source_upgrade,
    apply_version_conflict_decisions,
    approve_external_work_request,
    approve_incremental_source_request,
    approve_source_extraction,
    bind_library_item,
    coverage_gaps,
    create_external_work_request,
    create_incremental_source_request,
    create_source_extraction_template,
    create_source_processing_job,
    create_source_upgrade_proposal,
    fanfiction_source_readiness,
    import_source_item,
    initialize_project_source_packs,
    library_item,
    project_source_contract,
    register_source_work,
    resolve_incremental_source_request,
    search_approved_external_work,
    search_source_gap,
    source_evidence_preview,
    source_library_status,
    source_upgrade_status,
    run_source_processing_job,
)
from longform_engine.intelligence import (
    apply_intelligence_candidate,
    create_intelligence_task,
    validate_intelligence_candidate,
)
from longform_engine.intelligence import assess_project_readiness
from longform_engine.fanfiction_contracts import validate_fanfiction_source_canon
from longform_engine.orchestration import open_book
from longform_engine.research import ResearchError, promote_research
from longform_engine.semantic_protocols import (
    EVIDENCE_REFERENCE_SCHEMA,
    build_semantic_document,
)
from longform_engine.storage import init_project


def project_config(tmp_path: Path, *, mode: str = "fanfiction", sources: list[dict] | None = None):
    source_rows = sources or [
        {
            "source_id": "classic",
            "title": "公共领域冒险",
            "creator": "示例作者",
            "canon_cutoff": "第一卷末",
            "allowed_elements": ["characters", "relationships", "world", "timeline"],
            "rights_status": "unverified",
            "commercial_intent": False,
            "platform_policy_url": "",
        }
    ]
    overrides: dict = {
        "creation": {"mode": mode},
        "semantic": {"profile": "local-hash", "allow_fallback": True},
    }
    if mode == "fanfiction":
        overrides["fanfiction"] = {
            "continuity_mode": "crossover" if len(source_rows) > 1 else "canon_divergent",
            "sources": source_rows,
        }
    template = load_project_config(template="qidian-longform", cli_overrides=overrides)
    project = init_project(template, output=tmp_path / f"project-{mode}")
    config = load_project_config(project.project_config)
    open_book(config)
    return config, project.root


def approved_library_item(
    tmp_path: Path,
    monkeypatch,
    *,
    name: str = "公共领域冒险",
    creator: str = "示例作者",
):
    library = tmp_path / "用户资料" / "原著资料库"
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str(library.resolve()))
    work = register_source_work(
        name=name,
        creator=creator,
        aliases=["示例冒险"],
        versions=["第一卷"],
        approved_by="human",
    )
    source_text = (
        "林舟来到青铜门前。守门人说明门后的火不能用水熄灭。星纹钥匙仍由林舟保管。"
        "旧钟连续响了三次以后，石阶下方的安全通道逐段闭合；守门人要求持钥者先确认自己的选择，"
        "并承担开门后无法立刻返回的代价。林舟没有接受无限期的誓约，只同意完成一项边界明确的验证。"
    )
    source_file = tmp_path / f"{name}.txt"
    source_file.write_text(source_text, encoding="utf-8")
    item = import_source_item(
        work_id=work["work_id"],
        name="第一卷合法原件",
        source_type="小说卷册",
        version="第一卷",
        unit_range="第一卷全卷",
        source_method="用户本地导入",
        rights_status="public_domain_claimed",
        retention_mode="full_text",
        approved_by="human",
        file_path=source_file,
    )
    job = create_source_processing_job(item_id=item["item_id"])
    run_source_processing_job(item_id=item["item_id"], job_id=job["job_id"])
    registered = library_item(item["item_id"])
    preview = source_evidence_preview(item["item_id"])
    evidence_excerpt = "林舟来到青铜门前。"
    rule_excerpt = "门后的火不能用水熄灭"

    def evidence_record(evidence_id: str, excerpt: str) -> dict:
        segment = next(
            value for value in preview["segments"] if excerpt in value["normalized_text"]
        )
        return {
            "schema": EVIDENCE_REFERENCE_SCHEMA,
            "evidence_id": f"{item['item_id']}:{evidence_id}",
            "item_id": item["item_id"],
            "asset_id": segment["origin_locator"]["asset_id"],
            "segment_id": segment["segment_id"],
            "locator": segment["origin_locator"],
            "excerpt": excerpt,
            "excerpt_sha256": sha256(excerpt.encode("utf-8")).hexdigest(),
        }

    extraction = build_semantic_document(
        document_id=f"sem_source_{item['item_id'][5:]}",
        document_type="原著事实候选",
        title="第一卷合法原件语义提取",
        scope={"kind": "source_item", "item_id": item["item_id"], "work_id": work["work_id"]},
        continuity="原著基线",
        body="提取人物选择与门后规则；只保存释义主张和短证据。",
        claims=[
            {
                "claim_id": f"{item['item_id']}:lin_zhou",
                "statement": "林舟谨慎保管星纹钥匙，并在行动前验证守门规则。",
                "applicability": "第一卷当前场景",
                "evidence_refs": [f"{item['item_id']}:e1"],
                "uncertainty": "后续阶段的价值排序仍需单独证据。",
                "extensions": {
                    "semantic_type": "人物",
                    "display_name": "林舟",
                    "目标": "确认青铜门规则",
                },
            },
            {
                "claim_id": f"{item['item_id']}:gate_fire",
                "statement": "门后的火不遵循普通水灭规则。",
                "applicability": "第一卷门后规则",
                "evidence_refs": [f"{item['item_id']}:e2"],
                "uncertainty": "只确认普通水无效，不推断其他灭火方式。",
                "extensions": {"semantic_type": "世界规则", "display_name": "门后之火"},
            },
        ],
        evidence_references=[
            evidence_record("e1", evidence_excerpt),
            evidence_record("e2", rule_excerpt),
        ],
        extensions={
            "task_type": "source_fact_extraction",
            "item_id": item["item_id"],
            "bundle_sha256": registered["bundle_sha256"],
            "normalization_sha256": registered["normalization_sha256"],
        },
        input_hashes=[registered["bundle_sha256"], registered["normalization_sha256"]],
    )
    extraction_file = tmp_path / "提取候选.json"
    extraction_file.write_text(json.dumps(extraction, ensure_ascii=False), encoding="utf-8")
    approved = approve_source_extraction(
        item_id=item["item_id"], file_path=extraction_file, approved_by="human"
    )
    return work, {**item, **approved}, source_text


def reopen_extraction_candidate(payload: dict) -> dict:
    candidate = json.loads(json.dumps(payload, ensure_ascii=False))
    candidate["artifact"]["state"] = "candidate"
    candidate["extensions"].pop("approved_candidate_sha256", None)
    candidate["extensions"].pop("human_decision", None)
    return candidate


def test_source_extraction_template_is_noncanonical_and_does_not_overwrite(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)

    first = create_source_extraction_template(item_id=item["item_id"])
    candidate = Path(first["candidate_file"])
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["body"] = "作者保留的提取说明"
    candidate.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    second = create_source_extraction_template(item_id=item["item_id"])

    assert first["created"] is True
    assert first["non_canonical"] is True
    assert second["created"] is False
    assert json.loads(candidate.read_text(encoding="utf-8"))["body"] == "作者保留的提取说明"


def complete_project_pack(config, tmp_path: Path, item: dict) -> None:
    initialize_project_source_packs(config)
    source = next(
        row
        for row in config.data["fanfiction"]["sources"]
        if row["source_id"] == "classic"
    )
    bind_library_item(
        config,
        source_id="classic",
        item_id=item["item_id"],
        approved_by="human",
    )
    plan = {
        "协议版本": COVERAGE_SCHEMA,
        "资料源ID": "classic",
        "作品ID": item["work_id"],
        "覆盖模式": "分层按需",
        "权威版本": ["第一卷"],
        "截止点": source["canon_cutoff"],
        "覆盖需求": [
            {
                "需求ID": "identity_classic_v1",
                "需求": "确认作品、第一卷权威版本与第一卷末截止点。",
                "层级": "identity",
                "适用范围": "项目原著身份",
                "状态": "covered",
                "资料项ID列表": [item["item_id"]],
                "证据ID列表": [f"{item['item_id']}:e1"],
                "不适用理由": "",
            },
            {
                "需求ID": "design_core_gate_rule",
                "需求": "确认林舟的行动倾向与门后火焰规则，足以设计分歧路线。",
                "层级": "design_core",
                "适用范围": "同人路线设计",
                "状态": "covered",
                "资料项ID列表": [item["item_id"]],
                "证据ID列表": [f"{item['item_id']}:e1", f"{item['item_id']}:e2"],
                "不适用理由": "",
            },
        ],
        "模式变更理由": "",
    }
    plan_file = tmp_path / "全作覆盖计划.yaml"
    import yaml

    plan_file.write_text(yaml.safe_dump(plan, allow_unicode=True, sort_keys=False), encoding="utf-8")
    applied = apply_coverage_plan(
        config,
        source_id="classic",
        file_path=plan_file,
        approved_by="human",
    )
    assert applied["complete"] is True


def apply_project_canon(config, root: Path, *, interpretation: str) -> dict:
    contract = project_source_contract(config, "classic")
    task = create_intelligence_task(config, task_type="fanfiction_canon")
    candidate = root / task.candidate_file
    approved_evidence = {
        record["evidence_id"]: {
            key: record[key]
            for key in (
                "schema",
                "evidence_id",
                "item_id",
                "asset_id",
                "segment_id",
                "locator",
                "excerpt",
                "excerpt_sha256",
            )
        }
        for record in contract["evidence"].values()
    }
    character_evidence = next(
        value for key, value in approved_evidence.items() if key.endswith(":e1")
    )
    rule_evidence = next(
        value for key, value in approved_evidence.items() if key.endswith(":e2")
    )
    document = build_semantic_document(
        document_id="sem_project_source_canon_classic",
        document_type="项目原著基线Canon候选",
        title="公共领域冒险项目原著基线",
        scope={"kind": "project", "project": root.name},
        continuity="原著基线",
        body="本项目采用第一卷截至卷末的原著基线，并保留证据不足处的不确定性。",
        claims=[
            {
                "claim_id": "classic:lin_zhou",
                "statement": interpretation,
                "applicability": "第一卷末之前",
                "evidence_refs": [character_evidence["evidence_id"]],
                "uncertainty": "其他时期需要独立人物理解。",
                "extensions": {
                    "source_id": "classic",
                    "semantic_type": "人物",
                    "display_name": "林舟",
                },
            },
            {
                "claim_id": "classic:gate_fire",
                "statement": "门后的火不遵循普通水灭规则。",
                "applicability": "第一卷门后规则",
                "evidence_refs": [rule_evidence["evidence_id"]],
                "uncertainty": "没有证据支持其他灭火方式。",
                "extensions": {
                    "source_id": "classic",
                    "semantic_type": "世界规则",
                    "display_name": "门后之火",
                },
            },
        ],
        evidence_references=[character_evidence, rule_evidence],
        extensions={"task_type": "fanfiction_canon"},
    )
    candidate.write_text(
        json.dumps(document, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, task.task_id),
        result_file=candidate,
    )
    assert control.ok, control.normalization.errors
    validation = validate_intelligence_candidate(
        config, task_type="fanfiction_canon", file_path=candidate
    )
    assert validation.ok, validation.errors
    result = apply_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=candidate,
        approved_by="human",
    )
    assert result.status == "applied"
    return json.loads(
        (root / "10_bible" / "fanfiction" / "source_canon.json").read_text(encoding="utf-8")
    )


def test_user_library_project_binding_and_project_canon_are_separate(tmp_path, monkeypatch):
    work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, root = project_config(tmp_path)
    result = initialize_project_source_packs(config)

    pack = root / "50_workbench" / "同人原著资料" / "公共领域冒险"
    assert result["created"] == []
    assert result["existing"] == ["50_workbench/同人原著资料/公共领域冒险"]
    assert {path.name for path in pack.iterdir()} == {
        "作品资料设定.yaml",
        "资料绑定.yaml",
        "全作覆盖计划.yaml",
        "版本冲突清单.md",
        "资料覆盖情况.md",
        "资料项",
    }
    assert list((pack / "资料项").iterdir()) == []

    complete_project_pack(config, tmp_path, item)
    project_item = next((pack / "资料项").iterdir())
    assert not (project_item / "内容").exists()
    assert {path.name for path in project_item.iterdir()} == {
        "来源绑定.yaml",
        "提取结果.json",
        "证据索引.json",
    }
    assert source_library_status()["work_count"] == 1
    assert source_library_status()["item_count"] == 1
    assert fanfiction_source_readiness(config)["ready"] is True
    assert project_source_contract(config, "classic")["binding"]["work_id"] == work["work_id"]
    assert not (root / "10_bible" / "fanfiction" / "source_canon.json").exists()


def test_same_global_item_can_form_isolated_project_canons(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config_a, root_a = project_config(tmp_path / "a")
    config_b, root_b = project_config(tmp_path / "b")
    complete_project_pack(config_a, tmp_path / "a", item)
    complete_project_pack(config_b, tmp_path / "b", item)

    canon_a = apply_project_canon(
        config_a,
        root_a,
        interpretation="林舟在本项目中优先维护知情选择。",
    )
    canon_b = apply_project_canon(
        config_b,
        root_b,
        interpretation="林舟在本项目中优先验证规则来源。",
    )

    assert canon_a["schema"] == CANON_SCHEMA
    assert canon_b["schema"] == CANON_SCHEMA
    assert (
        canon_a["extensions"]["source_contracts"][0]["item_bindings"]
        == canon_b["extensions"]["source_contracts"][0]["item_bindings"]
    )
    assert canon_a["claims"][0]["statement"] != canon_b["claims"][0]["statement"]


def test_full_coverage_gate_requires_every_approved_unit_and_every_work(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    sources = [
        {
            "source_id": "classic",
            "title": "公共领域冒险",
            "creator": "示例作者",
            "canon_cutoff": "第一卷末",
            "allowed_elements": ["characters", "world"],
            "rights_status": "unverified",
            "commercial_intent": False,
            "platform_policy_url": "",
        },
        {
            "source_id": "second",
            "title": "第二作品",
            "creator": "另一作者",
            "canon_cutoff": "第十集",
            "allowed_elements": ["characters", "timeline"],
            "rights_status": "unverified",
            "commercial_intent": False,
            "platform_policy_url": "",
        },
    ]
    config, _root = project_config(tmp_path, sources=sources)
    initialize_project_source_packs(config)
    complete_project_pack(config, tmp_path, item)

    status = coverage_gaps(config)
    assert status["complete"] is False
    assert [work["complete"] for work in status["works"]] == [True, False]
    assert any(gap.startswith("second:") for gap in status["gaps"])


def test_chinese_directory_renames_do_not_change_stable_bindings(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    before = project_source_contract(config, "classic")
    pack = root / "50_workbench" / "同人原著资料" / "公共领域冒险"
    renamed_pack = pack.with_name("作者自定义作品目录")
    pack.rename(renamed_pack)
    project_item = next((renamed_pack / "资料项").iterdir())
    project_item.rename(project_item.with_name("作者自定义资料项目录"))
    library_root = Path(source_library_status()["library_root"])
    global_item = library_item(item["item_id"])
    global_item_dir = library_root / global_item["path"]
    global_work_dir = global_item_dir.parent.parent
    global_item_dir.rename(global_item_dir.with_name("作者自定义全局资料项"))
    global_work_dir.rename(global_work_dir.with_name("作者自定义全局作品目录"))

    after = project_source_contract(config, "classic")
    assert after["binding"] == before["binding"]
    assert after["coverage"] == before["coverage"]
    assert fanfiction_source_readiness(config)["ready"] is True


def test_missing_or_hash_drifted_global_original_blocks_dependent_work(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, _root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    registered = library_item(item["item_id"])
    asset = registered["assets"][0]
    content_file = Path(source_library_status()["library_root"]) / asset["managed_path"]
    content_file.write_text("被静默改动的原件", encoding="utf-8")

    readiness = fanfiction_source_readiness(config)
    assert readiness["ready"] is False
    assert any("hash" in error.lower() and "drift" in error.lower() for error in readiness["errors"])


def test_discovered_version_conflicts_require_explicit_human_resolution(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, _root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    current = library_item(item["item_id"])
    library_extraction = (
        Path(source_library_status()["library_root"])
        / current["path"]
        / "提取结果.json"
    )
    revised = reopen_extraction_candidate(
        json.loads(library_extraction.read_text(encoding="utf-8"))
    )
    conflict_id = f"{item['item_id']}:conflict_gate_rule"
    revised["claims"].append(
        {
            "claim_id": conflict_id,
            "statement": "两个版本对普通水是否有效给出不同描述。",
            "applicability": "小说版与动画版冲突",
            "evidence_refs": [f"{item['item_id']}:e2"],
            "uncertainty": "需要项目人工选择、隔离或排除版本。",
            "extensions": {
                "semantic_type": "版本冲突",
                "display_name": "门后之火规则冲突",
                "versions": ["小说版", "动画版"],
            },
        }
    )
    revised_file = tmp_path / "冲突提取候选.json"
    revised_file.write_text(json.dumps(revised, ensure_ascii=False), encoding="utf-8")
    approve_source_extraction(
        item_id=item["item_id"], file_path=revised_file, approved_by="human"
    )
    bind_library_item(
        config,
        source_id="classic",
        item_id=item["item_id"],
        approved_by="human",
    )
    readiness = fanfiction_source_readiness(config)
    assert readiness["ready"] is False
    assert any("版本冲突未解决" in error for error in readiness["errors"])

    decision = {
        "协议版本": "fanfiction_version_conflict_decision_v1",
        "资料源ID": "classic",
        "决定": [
            {
                "冲突事实ID": conflict_id,
                "处理": "选择版本",
                "采用版本": "小说版",
                "说明": "本项目权威版本和截止点均采用小说版。",
            }
        ],
    }
    decision_file = tmp_path / "版本冲突决定.yaml"
    import yaml

    decision_file.write_text(
        yaml.safe_dump(decision, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    result = apply_version_conflict_decisions(
        config,
        source_id="classic",
        file_path=decision_file,
        approved_by="human",
    )
    assert result["complete"] is True
    assert fanfiction_source_readiness(config)["ready"] is True


def test_chapter_canon_gap_requires_human_approval_before_search_and_resolution(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, _root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    request = create_incremental_source_request(
        config,
        source_id="classic",
        chapter_number=3,
        need="确认第三章准备使用的守门人关系阶段",
        reason="现有章节合同依赖该关系边界，不能由模型记忆补齐",
    )
    assert request["network_performed"] is False
    assert fanfiction_source_readiness(config)["ready"] is True
    assert fanfiction_source_readiness(
        config, gate="chapter_dependency", chapter_number=3
    )["ready"] is False
    calls: list[str] = []

    def fetcher(query: str, limit: int, timeout: int):
        calls.append(query)
        return [
            {
                "type": "web",
                "title": "官方人物资料",
                "url": "https://example.test/character",
                "summary": "候选定位",
                "credibility": "official",
            }
        ]

    with pytest.raises(FanfictionSourceError, match="requires human approval"):
        search_source_gap(
            config,
            source_id="classic",
            gap=request["request_id"],
            query="守门人 关系阶段",
            fetcher=fetcher,
        )
    assert calls == []
    approve_incremental_source_request(
        config,
        request_id=request["request_id"],
        approved_by="human",
    )
    candidates = search_source_gap(
        config,
        source_id="classic",
        gap=request["request_id"],
        query="守门人 关系阶段",
        fetcher=fetcher,
    )
    assert calls == ["守门人 关系阶段"]
    assert candidates["status"] == "awaiting_human_source_selection"
    resolve_incremental_source_request(
        config,
        request_id=request["request_id"],
        item_id=item["item_id"],
        reason="人工确认当前固定资料项的直接证据已经覆盖该关系阶段。",
        approved_by="human",
    )
    assert fanfiction_source_readiness(
        config, gate="chapter_dependency", chapter_number=3
    )["ready"] is True


def test_original_work_mention_requires_approval_before_search(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "library").resolve()))
    config, root = project_config(tmp_path, mode="original")
    request = create_external_work_request(
        config,
        work_name="某部作品",
        purpose="technique_analysis",
        reason="研究章节节奏",
    )
    assert request["status"] == "pending_human_approval"
    assert request["network_performed"] is False
    assert not (root / "50_workbench" / "同人原著资料").exists()
    assert source_library_status()["work_count"] == 0

    calls: list[str] = []

    def fetcher(query: str, limit: int, timeout: int):
        calls.append(query)
        return [
            {
                "type": "web",
                "title": "公开结构分析",
                "url": "https://example.test/analysis",
                "summary": "只分析节奏方法。",
                "credibility": "unverified",
            }
        ]

    with pytest.raises(FanfictionSourceError, match="explicit human approval"):
        search_approved_external_work(
            config,
            request=request["request_id"],
            query="某部作品 节奏分析",
            fetcher=fetcher,
        )
    assert calls == []

    approve_external_work_request(
        config, request=request["request_id"], approved_by="human"
    )
    result = search_approved_external_work(
        config,
        request=request["request_id"],
        query="某部作品 节奏分析",
        fetcher=fetcher,
    )
    assert calls == ["某部作品 节奏分析"]
    assert Path(result.item_file).is_relative_to(root / "50_workbench" / "research_inbox")
    assert source_library_status()["work_count"] == 0
    with pytest.raises(ResearchError, match="external-work research is non-canonical"):
        promote_research(config, research_item=result.item_id, approved_by="human")


def test_original_elements_route_to_fanfiction_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "library").resolve()))
    config, _root = project_config(tmp_path, mode="inspired_original")
    request = create_external_work_request(
        config,
        work_name="某部作品",
        purpose="use_original_elements",
        reason="准备使用原著人物",
    )
    decision = approve_external_work_request(
        config, request=request["request_id"], approved_by="human"
    )
    assert decision["status"] == "mode_change_required"
    assert decision["network_performed"] is False
    with pytest.raises(FanfictionSourceError, match="requires explicit human approval|creation.mode=fanfiction"):
        search_approved_external_work(
            config,
            request=request["request_id"],
            query="某部作品 人物",
        )


def test_v2_project_canon_is_rejected_without_dual_read(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, _root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    errors: list[str] = []
    validate_fanfiction_source_canon(
        config,
        {"schema": "fanfiction_source_canon_v2", "continuity_mode": "canon_divergent", "sources": []},
        errors,
    )
    assert any(CANON_SCHEMA in error and "incompatible" in error for error in errors)


def test_full_text_retention_requires_declared_rights(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "library").resolve()))
    work = register_source_work(
        name="权利边界作品", creator="作者", approved_by="human"
    )
    source = tmp_path / "原件.txt"
    source.write_text("受保护内容", encoding="utf-8")
    with pytest.raises(FanfictionSourceError, match="full_text retention requires"):
        import_source_item(
            work_id=work["work_id"],
            name="网页全文",
            source_type="网页",
            version="公开网页",
            unit_range="未知",
            source_method="网络",
            rights_status="unverified",
            retention_mode="full_text",
            approved_by="human",
            file_path=source,
        )


def test_unknown_chinese_source_type_uses_generic_dynamic_item(tmp_path, monkeypatch):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "library").resolve()))
    work = register_source_work(name="舞台资料作品", creator="作者", approved_by="human")
    item = import_source_item(
        work_id=work["work_id"],
        name="巡演后台作者谈",
        source_type="舞台剧巡演后台访谈",
        version="广州场",
        unit_range="访谈全段",
        source_method="官方页面",
        rights_status="unverified",
        retention_mode="metadata_only",
        approved_by="human",
        source_locator="https://example.test/interview",
    )
    registered = library_item(item["item_id"])
    assert registered["source_type"] == "舞台剧巡演后台访谈"
    assert registered["assets"] == []


def test_global_extraction_update_only_creates_project_upgrade_proposal(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    before = project_source_contract(config, "classic")
    project_extraction = next(
        (root / "50_workbench" / "同人原著资料").rglob("提取结果.json")
    )
    project_bytes = project_extraction.read_bytes()

    current = library_item(item["item_id"])
    library_extraction = (
        Path(source_library_status()["library_root"])
        / current["path"]
        / "提取结果.json"
    )
    revised = reopen_extraction_candidate(
        json.loads(library_extraction.read_text(encoding="utf-8"))
    )
    revised["claims"][0]["statement"] = "经人工修正：人物会先验证规则，再决定是否使用钥匙。"
    revised_file = tmp_path / "修正提取候选.json"
    revised_file.write_text(json.dumps(revised, ensure_ascii=False), encoding="utf-8")
    updated = approve_source_extraction(
        item_id=item["item_id"], file_path=revised_file, approved_by="human"
    )
    assert updated["extraction_sha256"] != item["extraction_sha256"]

    after = project_source_contract(config, "classic")
    assert after["binding_sha256"] == before["binding_sha256"]
    assert after["binding"] == before["binding"]
    assert project_extraction.read_bytes() == project_bytes
    assert fanfiction_source_readiness(config)["ready"] is True

    status = source_upgrade_status(config, source_id="classic")
    assert status["upgrade_available"] is True
    proposal = create_source_upgrade_proposal(
        config,
        source_id="classic",
        target_item_id=item["item_id"],
        created_by="human",
    )
    assert proposal["binding_changed"] is False
    assert proposal["canon_changed"] is False
    assert proposal["must_stale_artifacts"] == ["10_bible/fanfiction/source_canon.json"]
    assert (root / proposal["proposal_file"]).is_file()


def test_approved_future_upgrade_stales_project_canon_without_rewriting_it(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    canon_before = apply_project_canon(
        config,
        root,
        interpretation="林舟在本项目中优先维护知情选择。",
    )
    canon_file = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon_bytes = canon_file.read_bytes()

    current = library_item(item["item_id"])
    library_extraction = (
        Path(source_library_status()["library_root"])
        / current["path"]
        / "提取结果.json"
    )
    revised = reopen_extraction_candidate(
        json.loads(library_extraction.read_text(encoding="utf-8"))
    )
    revised["claims"][0]["statement"] = "经人工修正：人物验证规则来源后再决定是否使用钥匙。"
    revised_file = tmp_path / "修正提取候选.json"
    revised_file.write_text(json.dumps(revised, ensure_ascii=False), encoding="utf-8")
    approve_source_extraction(
        item_id=item["item_id"], file_path=revised_file, approved_by="human"
    )
    proposal_result = create_source_upgrade_proposal(
        config,
        source_id="classic",
        target_item_id=item["item_id"],
        created_by="human",
    )
    proposal_file = root / proposal_result["proposal_file"]
    proposal = json.loads(proposal_file.read_text(encoding="utf-8"))

    with pytest.raises(FanfictionSourceError, match="cannot be rebound directly"):
        bind_library_item(
            config,
            source_id="classic",
            item_id=item["item_id"],
            approved_by="human",
        )

    proposal_sha = sha256(
        json.dumps(
            proposal,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    review = {
        "schema": "fanfiction_source_upgrade_semantic_review_v1",
        "proposal_sha256": proposal_sha,
        "independent_from_proposer": True,
        "verdict": "pass",
        "affected_fact_ids": proposal["affected_fact_ids"],
        "must_stale_artifacts": proposal["must_stale_artifacts"],
        "earliest_affected_chapter": None,
        "reason": "修正只影响未来规划；当前没有已定稿章节。",
        "reviewed_by": "independent-reviewer",
    }
    review_file = root / "50_workbench" / "同人原著资料" / "升级语义审查.json"
    review_file.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    review_sha = sha256(
        json.dumps(
            review,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    decision = {
        "schema": "human_fanfiction_source_upgrade_decision_v1",
        "proposal_sha256": proposal_sha,
        "semantic_review_sha256": review_sha,
        "decision": "approve",
        "reason": "批准未来章节使用修正后的提取版本。",
        "decided_by": "human",
    }
    decision_file = root / "50_workbench" / "同人原著资料" / "升级人工决定.json"
    decision_file.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")

    result = apply_source_upgrade(
        config,
        proposal_path=proposal_file,
        review_path=review_file,
        decision_path=decision_file,
    )
    assert result["status"] == "applied_future_source_upgrade"
    assert result["canon_changed"] is False
    assert canon_file.read_bytes() == canon_bytes
    readiness = assess_project_readiness(config)
    assert readiness.ready is False
    assert readiness.stage == "fanfiction_canon"
    assert canon_before["extensions"]["source_contracts"][0][
        "binding_sha256"
    ] != project_source_contract(config, "classic")["binding_sha256"]


def test_historical_source_upgrade_routes_to_revision_branch_without_mutation(tmp_path, monkeypatch):
    _work, item, _source_text = approved_library_item(tmp_path, monkeypatch)
    config, root = project_config(tmp_path)
    complete_project_pack(config, tmp_path, item)
    apply_project_canon(
        config,
        root,
        interpretation="林舟在本项目中优先维护知情选择。",
    )
    final = root / "40_manuscript" / "final" / "ch001.md"
    final.write_text("# 第一章\n\n林舟拒绝了没有边界的誓约。\n", encoding="utf-8")
    binding_before = project_source_contract(config, "classic")["binding_sha256"]
    canon_file = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon_before = canon_file.read_bytes()

    current = library_item(item["item_id"])
    library_extraction = (
        Path(source_library_status()["library_root"])
        / current["path"]
        / "提取结果.json"
    )
    revised = reopen_extraction_candidate(
        json.loads(library_extraction.read_text(encoding="utf-8"))
    )
    revised["claims"][0]["statement"] = "经人工修正：这一选择也改变第一章的人物解释。"
    revised_file = tmp_path / "历史修正提取候选.json"
    revised_file.write_text(json.dumps(revised, ensure_ascii=False), encoding="utf-8")
    approve_source_extraction(
        item_id=item["item_id"], file_path=revised_file, approved_by="human"
    )
    proposal_result = create_source_upgrade_proposal(
        config,
        source_id="classic",
        target_item_id=item["item_id"],
        created_by="human",
    )
    proposal_file = root / proposal_result["proposal_file"]
    proposal = json.loads(proposal_file.read_text(encoding="utf-8"))
    proposal_sha = sha256(
        json.dumps(
            proposal,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    review = {
        "schema": "fanfiction_source_upgrade_semantic_review_v1",
        "proposal_sha256": proposal_sha,
        "independent_from_proposer": True,
        "verdict": "pass",
        "affected_fact_ids": proposal["affected_fact_ids"],
        "must_stale_artifacts": proposal["must_stale_artifacts"],
        "earliest_affected_chapter": 1,
        "reason": "修正影响第一章已经采用的人物解释。",
        "reviewed_by": "independent-reviewer",
    }
    review_file = root / "50_workbench" / "同人原著资料" / "历史升级语义审查.json"
    review_file.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    review_sha = sha256(
        json.dumps(
            review,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    decision = {
        "schema": "human_fanfiction_source_upgrade_decision_v1",
        "proposal_sha256": proposal_sha,
        "semantic_review_sha256": review_sha,
        "decision": "approve",
        "reason": "批准在隔离分支回溯第一章。",
        "decided_by": "human",
    }
    decision_file = root / "50_workbench" / "同人原著资料" / "历史升级人工决定.json"
    decision_file.write_text(json.dumps(decision, ensure_ascii=False), encoding="utf-8")

    result = apply_source_upgrade(
        config,
        proposal_path=proposal_file,
        review_path=review_file,
        decision_path=decision_file,
    )
    assert result["status"] == "routed_to_revision_branch_v2"
    assert result["binding_changed"] is False
    assert project_source_contract(config, "classic")["binding_sha256"] == binding_before
    assert canon_file.read_bytes() == canon_before
    branch = (
        root
        / "50_workbench"
        / "revision_branches"
        / result["revision_branch_id"]
        / "branch.json"
    )
    assert json.loads(branch.read_text(encoding="utf-8"))["schema"] == "revision_branch_v2"
