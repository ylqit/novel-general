"""Isolated fixtures verify reading, Unicode references and edit isolation, not literary quality."""

from hashlib import sha256
import json

import pytest

from longform_engine.studio_content import StudioContent, StudioContentError
from tests.test_current_planning_context import approved_project
from longform_engine.config import load_project_config
from longform_engine.storage import init_project


@pytest.mark.parametrize("action", ["rollback", "discard", "cleanup"])
def test_recovery_web_actions_require_exclusive_lock_confirmation_and_exact_version(tmp_path, monkeypatch, action):
    from longform_engine.local_web import LocalWebError
    from longform_engine.storage import acquire_project_lock, apply_transaction, StorageError
    from longform_engine.storage import project as storage_module
    from longform_engine.studio_operations import execute_studio_operation, studio_operation_state

    project = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    target = project.root / "50_workbench/recovery_fixture.txt"
    target.write_text("before", encoding="utf-8")
    transaction = apply_transaction(project.root, command="Web recovery fixture", touched_paths=[target])
    with monkeypatch.context() as fault:
        if action == "discard":
            def interrupt_snapshot(*args, **kwargs):
                raise RuntimeError("fixture snapshot interruption")
            fault.setattr(storage_module, "snapshot_transaction_path", interrupt_snapshot)
            with pytest.raises(RuntimeError, match="fixture snapshot interruption"):
                transaction.begin()
        elif action == "cleanup":
            fault.setattr(storage_module, "cleanup_transaction_snapshot", lambda path: ["fixture cleanup interruption"])
            with transaction:
                target.write_text("after", encoding="utf-8")
        else:
            transaction.begin()
            target.write_text("interrupted", encoding="utf-8")
    row = next(row for row in studio_operation_state(config, "recovery")["transactions"]
               if row["state"] == "recoverable_" + action)
    payload = {"action": action, "id": row["id"], "expected_sha256": row["sha256"], "acknowledge": True}
    before = target.read_bytes(), transaction.report_file.read_bytes()
    with pytest.raises(LocalWebError, match="必须确认"):
        execute_studio_operation(config, "recovery", {**payload, "acknowledge": False})
    with acquire_project_lock(config, command="another live writer"):
        with pytest.raises(StorageError, match="lock|Lock"):
            execute_studio_operation(config, "recovery", payload)
    with pytest.raises(LocalWebError, match="已变化"):
        execute_studio_operation(config, "recovery", {**payload, "expected_sha256": "0" * 64})
    assert (target.read_bytes(), transaction.report_file.read_bytes()) == before
    result = execute_studio_operation(config, "recovery", payload)
    assert result["result"]["status"] == {"rollback": "rolled_back", "discard": "discarded", "cleanup": "cleaned"}[action]
    assert target.read_text(encoding="utf-8") == ("after" if action == "cleanup" else "before")
    assert not studio_operation_state(config, "recovery")["blocked"]
    with pytest.raises(LocalWebError, match="已变化"):
        execute_studio_operation(config, "recovery", payload)


def test_reader_survives_missing_planning_and_separates_final_from_draft(tmp_path):
    _, root = approved_project(tmp_path)
    final = root / "40_manuscript/final/ch001.md"
    final.write_text("# 第一章\n\n陆照摸到炉沿。\n", encoding="utf-8")
    (root / "40_manuscript/draft/ch001.md").write_text("未批准的新版本", encoding="utf-8")
    content = StudioContent(root)
    assert content.chapter(1)["text"] == final.read_text(encoding="utf-8")
    assert content.chapter(1, version="draft")["text"] == "未批准的新版本"
    (root / "30_state/planning_basis.json").unlink()
    assert content.catalogue()["issues"]
    assert content.catalogue()["chapters"][0]["status"] == "final"
    assert content.chapter(1)["available"]
    assert content.chapter(2)["available"] is False


def test_catalogue_uses_approved_actual_ranges_and_reports_conflicts(tmp_path):
    _, root = approved_project(tmp_path)
    view = StudioContent(root)
    catalog = view.catalogue(limit=1)
    assert catalog["volumes"][0]["chapter_range"] == [1, 30]
    assert catalog["chapters"][0]["volume_id"] == "volume:001"
    assert catalog["next_offset"] == 1
    path = root / "20_outline/volume_skeletons.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["items"].append({**data["items"][0], "volume_id": "volume:collision", "title": "重叠卷"})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert "失效" in view.catalogue()["issues"][0]
    basis_path = root / "30_state/planning_basis.json"
    basis = json.loads(basis_path.read_text(encoding="utf-8"))
    for row in basis["source_files"]:
        if row["path"] == "20_outline/volume_skeletons.json":
            row["sha256"] = sha256(path.read_bytes()).hexdigest()
    basis_path.write_text(json.dumps(basis), encoding="utf-8")
    collision = view.catalogue()
    assert any("冲突" in issue for issue in collision["issues"])
    assert collision["chapters"][0]["volume_id"] is None


def test_search_locations_are_exact_unicode_and_documents_are_inventory_scoped(tmp_path):
    from longform_engine.semantic_protocols import build_semantic_document
    _, root = approved_project(tmp_path)
    text = "# 炉房\n\n𠮷和陆照检验余火。陆照没有作答。"
    (root / "40_manuscript/final/ch001.md").write_text(text, encoding="utf-8")
    content = StudioContent(root)
    hits = [r for r in content.search("陆照")["results"] if r["kind"] == "chapter"]
    assert len(hits) == 2
    for hit in hits:
        assert text[hit["start"]:hit["end"]] == hit["quote"] == "陆照"
        assert hit["sha256"] == sha256((root / "40_manuscript/final/ch001.md").read_bytes()).hexdigest()
    documents = content.documents()
    assert documents
    assert content.document(documents[0]["id"])["text"]
    with pytest.raises(StudioContentError):
        content.document("../../project.yaml")
    with pytest.raises(StudioContentError):
        content.document("doc_" + "0" * 24)
    with pytest.raises(StudioContentError):
        content.search("", limit=0)
    candidate = build_semantic_document(
        document_id="reading:source", document_type="原著基线候选", title="待确认的原著资料",
        scope={"kind": "project", "project": root.name}, continuity="原著补完",
        body="这份解释尚未获得批准。", claims=[], evidence_references=[],
    )
    source = root / "10_bible/fanfiction/source_canon.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    source_id = next(row["id"] for row in content.documents() if row["relative"] == "10_bible/fanfiction/source_canon.json")
    assert content.document(source_id)["notice"] == "尚未批准的资料，不作为创作事实"
    candidate["artifact"]["content_sha256"] = "0" * 64
    source.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    assert content.document(source_id)["notice"] == "资料结构或内容绑定无效，仅供查看原文"


@pytest.mark.parametrize("states,expected", [([], None), (["手伤未愈"], "手伤未愈"),
                                          (["手伤未愈", "手伤已愈，搬运限制解除"], "手伤已愈，搬运限制解除")])
def test_consequence_document_keeps_plans_separate_from_latest_final_evidence(tmp_path, states, expected):
    from longform_engine.semantic_protocols import build_semantic_document
    from tests.test_fanfiction_contracts import approve, semantic_claim

    _, root = approved_project(tmp_path)
    claim = semantic_claim("route:injury", "跨卷延续后果", extensions={"volume_ids": ["volume:002"]})
    claim["statement"] = "进入下一卷时必须承接右手伤势，除非正文已有解除依据。"
    route = approve(build_semantic_document(document_id="route:reading", document_type="同人路线", title="后果阅读夹具",
        scope={"kind": "project"}, continuity="顺序诸天", body="合成协议夹具", claims=[claim]))
    route_path = root / "10_bible/fanfiction/fanfiction_bible.json"
    route_path.parent.mkdir(parents=True, exist_ok=True)
    route_path.write_text(json.dumps(route, ensure_ascii=False), encoding="utf-8")
    for chapter in (1, 2):
        text = states[chapter - 1] if len(states) >= chapter else "本章没有伤势变化。"
        final = root / f"40_manuscript/final/ch{chapter:03d}.md"
        final.write_text(text, encoding="utf-8")
        ledger = {"schema": "chapter_semantic_bundle_v1", "chapter_number": chapter, "canonical": True,
                  "source": {"path": final.relative_to(root).as_posix(), "sha256": sha256(final.read_bytes()).hexdigest()},
                  "world_deltas": ([{"fact_id": "route:injury", "value": text,
                                     "evidence": {"start": 0, "end": len(text), "excerpt": text}}] if len(states) >= chapter else [])}
        ledger_path = root / f"30_state/semantic_ledger/ch{chapter:03d}.json"
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    content = StudioContent(root)
    identifier = next(row["id"] for row in content.documents() if row["relative"] == route_path.relative_to(root).as_posix())
    history = content.document(identifier)["consequence_history"]
    assert not history["issues"]
    assert history["items"][0]["plan"] == claim["statement"]
    actual = history["items"][0]["actual"]
    assert (actual["value"] if actual else None) == expected
    final.write_text("正文版本已改变", encoding="utf-8")
    stale = content.document(identifier)
    assert stale["consequence_history"]["issues"]
    assert not stale["consequence_history"]["items"]
    assert stale["text"] == route_path.read_text(encoding="utf-8")


def test_edit_draft_optimistic_lock_and_source_change_never_write_canonical(tmp_path):
    _, root = approved_project(tmp_path)
    final = root / "40_manuscript/final/ch001.md"
    final.write_text("他收起了手。", encoding="utf-8")
    content = StudioContent(root)
    source = content.chapter(1)["sha256"]
    payload = {"revision": 0, "source_sha256": source, "text": "他把烫伤的手藏进袖里。"}
    result = content.save_draft(1, payload)
    assert result["revision"] == 1
    assert final.read_text(encoding="utf-8") == "他收起了手。"
    with pytest.raises(StudioContentError, match="revision_conflict"):
        content.save_draft(1, payload)
    final.write_text("正文被另一合法流程更新。", encoding="utf-8")
    with pytest.raises(StudioContentError, match="source_stale"):
        content.save_draft(1, {**payload, "revision": 1})
    assert content.draft(1) == result


def test_discussion_explicit_history_and_source_stale(tmp_path):
    from longform_engine.studio_discussion import StudioDiscussion
    from longform_engine.agent_tasks import load_manifest, manifest_input_paths
    from longform_engine.agent_pipeline import validate_production_agent_result
    initialized = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    root = initialized.root
    discussion = StudioDiscussion(root)
    request = {"scope": "project", "chapter": None, "question": "怎样减少解释腔？", "selection": None, "document_ids": []}
    turn = discussion.create(request)
    from longform_engine.production import first_active_agent_task
    assert first_active_agent_task(root) is None
    manifest = load_manifest(root, turn["task_id"])
    assert manifest["scope"]["kind"] == "project"
    response = root / manifest["io"]["output"]["path"]
    response.write_text("# 创作讨论\n\n## 问题复述\n减少重复解释。\n\n## 证据判断\n现有材料仅有开书约定。\n\n## 可选修法\n优先让人物行动承担信息。\n\n## 风险与保护项\n保留必要概述。\n\n## 建议动作\n选定具体段落后复查。\n", encoding="utf-8", newline="\n")
    validation = validate_production_agent_result(root, manifest, result_file=response)
    assert validation.ok
    discussion.record(turn["id"])
    assert load_manifest(root, turn["task_id"])["status"] == "applied"
    assert discussion.record(turn["id"])["response_sha256"]
    next_turn = discussion.create({**request, "question": "过渡段也需要这样吗？"})
    next_manifest = load_manifest(root, next_turn["task_id"])
    request_file = next(p for p in manifest_input_paths(next_manifest) if p.endswith("request.json"))
    history = json.loads((root / request_file).read_text(encoding="utf-8"))["history"]
    assert history[0]["question"] == request["question"]
    assert "必要概述" in history[0]["response"]
    from longform_engine.artifacts import compact_project_setup
    # Completed and still-running conversations both outlive setup work orders.
    config = load_project_config(root / "project.yaml")
    assert compact_project_setup(config, dry_run=True).eligible
    compact_project_setup(config, dry_run=False)
    assert len(discussion.history()["turns"]) == 2
    assert discussion.history()["turns"][0]["response_sha256"]
    assert load_manifest(root, turn["task_id"])["status"] == "applied"
    binding = turn["bindings"][0]
    (root / binding["path"]).write_text("资料已修改", encoding="utf-8")
    assert all(t["stale"] for t in discussion.history()["turns"])
    with pytest.raises(StudioContentError, match="失效"):
        discussion.adopt(turn["id"], {"kind": "memo", "text": "保留概述"})


@pytest.mark.parametrize("scope", ["project", "volume", "chapter", "selection"])
def test_discussion_scopes_compile_and_volume_range_changes_are_stale(tmp_path, scope):
    from longform_engine.studio_discussion import StudioDiscussion
    from longform_engine.production import agent_task_brief
    config, root = approved_project(tmp_path)
    final = root / "40_manuscript/final/ch001.md"
    final.write_text("# 第一章 合成材料\n\n陆照放下铜符，让同伴先作选择。\n", encoding="utf-8")
    content = StudioContent(root).chapter(1)
    selection = {"chapter_number": 1, "start": 0, "end": 5, "quote": content["text"][:5],
                 "sha256": content["sha256"], "version": content["version"]} if scope == "selection" else None
    discussions = StudioDiscussion(root)
    turn = discussions.create({"scope": scope, "chapter": 1, "question": "请分析这些合成材料，不生成正文。",
                               "selection": selection, "document_ids": []})
    brief = agent_task_brief(config, turn["task_id"], host="codex")
    assert brief["executable"], brief.get("blocked_by")
    if scope == "volume":
        paths = {row["path"] for row in turn["bindings"]}
        assert {"20_outline/volume_skeletons.json", "30_state/planning_basis.json"} <= paths
        source = root / "20_outline/volume_skeletons.json"
        source.write_bytes(source.read_bytes() + b"\n")
        assert discussions.history()["turns"][0]["stale"] is True
        with pytest.raises(StudioContentError, match="失效"):
            discussions.adopt(turn["id"], {"kind": "memo", "text": "旧卷范围的建议"})


def test_rehearsal_provenance_cannot_qualify_for_literary_acceptance(tmp_path):
    from longform_engine.execution_origin import execution_origin
    from longform_engine.fanfiction_literary_trial import collect_literary_sample
    initialized = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    root = initialized.root
    marker = root / "00_governance/execution_origin.json"
    marker.write_text(json.dumps({"schema": "execution_origin_v1", "kind": "automated_rehearsal", "simulated_human": True, "run_id": "fixture"}), encoding="utf-8")
    assert execution_origin(root)["simulated_human"]
    with pytest.raises(ValueError, match="automated_rehearsal_ineligible"):
        collect_literary_sample(load_project_config(initialized.project_config), 1, 3)
    marker.write_text("broken", encoding="utf-8")
    assert execution_origin(root)["simulated_human"]


def test_versions_read_verified_archives_without_restore_and_reject_tampering(tmp_path):
    import zipfile
    initialized = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    root = initialized.root
    final = root / "40_manuscript/final/ch001.md"
    final.write_text("# 第一章\n\n陆照收回烫伤的手。", encoding="utf-8")
    content = StudioContent(root)
    original = final.read_bytes()
    candidate = "50_workbench/candidate_blobs/fixture.md"
    text = "# 第一章\n\n𠮷字之前的旧候选。".encode("utf-8")
    archive = root / "70_runtime/artifacts/chapters/ch001.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "chapter_artifact_archive_v3", "chapter_number": 1,
                "entries": [{"path": candidate, "member": "_audit/blobs/fixture", "sha256": sha256(text).hexdigest()}]}
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("_audit/manifest.json", json.dumps(manifest))
        handle.writestr("_audit/blobs/fixture", text)
    versions = content.versions(1)
    archived = next(row for row in versions["versions"] if row["archived"])
    assert content.version(1, archived["id"])["text"].encode("utf-8") == text
    assert not (root / candidate).exists()
    assert final.read_bytes() == original
    with pytest.raises(StudioContentError):
        content.version(2, archived["id"])
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("_audit/manifest.json", json.dumps(manifest))
        handle.writestr("_audit/blobs/fixture", b"tampered")
    assert any("hash mismatch" in issue for issue in content.versions(1)["issues"])
    with pytest.raises(StudioContentError):
        content.version(1, archived["id"])
    from longform_engine.human_author_revision import TASK_SCHEMA
    source = root / "50_workbench/candidate_blobs/active-source.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(original)
    directory = root / "50_workbench/human_author_revisions/ch001"
    directory.mkdir(parents=True)
    human = directory / "revision.human.md"
    human.write_text("# 第一章\n\n陆照把伤手藏进袖里。", encoding="utf-8")
    record = {"schema": TASK_SCHEMA, "chapter_number": 1, "source_file": source.relative_to(root).as_posix(),
              "source_candidate_sha256": sha256(original).hexdigest(), "candidate_file": human.relative_to(root).as_posix()}
    task = directory / "revision.task.json"
    task.write_text(json.dumps(record), encoding="utf-8")
    version = next(row for row in content.versions(1)["versions"] if row["relative"] == record["candidate_file"])
    assert content.version(1, version["id"])["text"] == human.read_text(encoding="utf-8")
    with pytest.raises(StudioContentError):
        content.version(2, version["id"])
    task.write_text(json.dumps({**record, "candidate_file": "project.yaml"}), encoding="utf-8")
    assert all(row["relative"] != "project.yaml" for row in content.versions(1)["versions"])
    source.write_bytes(b"tampered")
    assert any("绑定失效" in error for error in content.versions(1)["issues"])
    assert final.read_bytes() == original


def test_saved_memos_are_discoverable_searchable_and_stay_noncanonical(tmp_path):
    initialized = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    content = StudioContent(initialized.root)
    result = content.save_note({"title": "对白分寸", "text": "保留人物回避问题的方式。"})
    assert not result["canonical"]
    note = next(row for row in content.documents() if row["group"] == "notes")
    assert note["title"] == "对白分寸"
    assert "回避问题" in content.document(note["id"])["text"]
    assert content.search("回避问题")["results"][0]["id"] == note["id"]


@pytest.mark.parametrize("fail_during_apply", [False, True])
def test_planning_manifest_compilation_review_and_atomic_apply(tmp_path, monkeypatch, fail_during_apply):
    from longform_engine.agent_pipeline import validate_production_agent_result
    from longform_engine.agent_tasks import load_manifest
    from longform_engine.semantic_protocols import build_semantic_document
    from longform_engine.planning.workbench import PlanningWorkbench
    from tests.test_v010_planning import planning_bundle, evidence_review
    initialized = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    config = load_project_config(initialized.project_config)
    root = initialized.root
    # Only readiness is arranged here. Model outputs below are labelled protocol
    # fixtures; the production and browser rehearsal tests own full readiness.
    monkeypatch.setattr("longform_engine.production.production_next", lambda _config: {"status": "planning_refresh_required", "chapter_number": 1})
    workbench = PlanningWorkbench(config)
    created = workbench.create()
    author = load_manifest(root, created["author_task_id"])
    bundle = planning_bundle()
    def remove_control(value):
        if isinstance(value, dict):
            return {k: remove_control(v) for k, v in value.items() if k not in {"candidate_sha256", "basis_sha256", "approved_by", "human_decision", "lifecycle"}}
        if isinstance(value, list):
            return [remove_control(v) for v in value]
        return value
    author_file = root / author["io"]["output"]["path"]
    document = build_semantic_document(document_id="planning:fixture", document_type="长篇滚动规划候选", title="协议测试规划",
        scope={"kind": "project"}, continuity="原创测试", body="这是编译协议夹具，不是模型创作或文学评审证据。",
        extensions={"planning_bundle": remove_control(bundle)})
    author_file.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    if fail_during_apply:
        oversized = {**document, "body": "过长的未批准构思。" * 4000}
        author_file.write_text(json.dumps(oversized, ensure_ascii=False), encoding="utf-8")
    old_author_file = author_file
    old_bytes = author_file.read_bytes()
    created = workbench.create(rebuild=True)
    author = load_manifest(root, created["author_task_id"])
    input_paths = {row["path"] for row in author["io"]["inputs"]}
    if fail_during_apply:
        assert old_author_file.relative_to(root).as_posix() not in input_paths
        excerpt_path = next(path for path in input_paths if path.endswith("/previous_candidate_excerpt.json"))
        excerpt = json.loads((root / excerpt_path).read_text(encoding="utf-8"))
        assert excerpt["source_sha256"] == sha256(old_bytes).hexdigest()
        assert excerpt["approved"] is False and excerpt["truncated"] is True
        assert len(excerpt["body_excerpt"]) <= 6000
    else:
        assert old_author_file.relative_to(root).as_posix() in input_paths
    from longform_engine.agent_isolation import compile_isolated_agent_package
    package = compile_isolated_agent_package(root, author, host="codex")
    assert package.result_template is not None
    assert set(package.result_template) == {"schema", "artifact", "document_type", "title", "continuity", "body", "claims", "evidence_references", "uncertainties", "extensions"}
    assert package.prompt.markdown.count("## 当前输出模板") == 1
    assert '"document_type": "长篇滚动规划候选"' in package.host_work_order.markdown
    assert package.result_template["artifact"]["content_sha256"] == ""
    from longform_engine.agent_results import build_agent_result_template
    assert package.result_template == build_agent_result_template(author)
    assert package.result_template["artifact"]["created_at"] == author["created_at"]
    from longform_engine.semantic_protocols import validate_semantic_document
    document = {**package.result_template, "body": document["body"], "extensions": document["extensions"]}
    assert validate_semantic_document(document) == []
    damaged = {**document, "artifact": {**document["artifact"], "content_sha256": "0" * 64}}
    assert "artifact.content_sha256 does not match semantic content" in validate_semantic_document(damaged)
    author_file = root / author["io"]["output"]["path"]
    author_file.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert old_author_file.read_bytes() == old_bytes
    result = validate_production_agent_result(root, author, result_file=author_file)
    assert result.ok, result.normalization.errors
    reviewed = workbench.prepare_review(author["task_id"])
    assert reviewed["bundle"]["plot_node_tables"][0]["candidate_sha256"]
    reviewer = load_manifest(root, reviewed["reviewer_task_id"])
    from longform_engine.planning.workflow import REVIEW_PROFILES
    reviewer_template = build_agent_result_template(reviewer)
    assert set(reviewer_template["coverage"]) == set(REVIEW_PROFILES["architecture"]["dimensions"])
    assert all(row == {"status": "", "evidence_ids": [], "canonical_refs": []} for row in reviewer_template["coverage"].values())
    reviewer_package = compile_isolated_agent_package(root, reviewer, host="codex")
    reference_guidance = reviewer_package.prompt.markdown.split("## canonical_refs 资料范围", 1)[1].split("## 当前输出模板", 1)[0]
    assert "project.yaml 和普通 workbench 候选不能放入 canonical_refs" in reference_guidance
    assert "- `project.yaml`" not in reference_guidance
    assert "- `10_bible/" in reference_guidance
    assert author_file.relative_to(root).as_posix() not in [row["path"] for row in reviewer["io"]["inputs"]]
    review_file = root / reviewer["io"]["output"]["path"]
    review = evidence_review((author_file.parent / "bundle.json").relative_to(root).as_posix())
    canonical = next(row["path"] for row in reviewer["io"]["inputs"] if row["path"].startswith("10_bible/"))
    review["coverage"]["protected_invariants"]["canonical_refs"] = [canonical]
    review_file.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
    result = validate_production_agent_result(root, reviewer, result_file=review_file)
    assert result.ok, result.normalization.errors
    validated = workbench.validate_review(reviewer["task_id"])
    assert validated["review_validation"]["state"] == "semantic_passed"
    decisions = [{"node_id": n["node_id"], "decision": "approve", "reason": "明确的测试批准", "adjustment": ""}
                 for table in bundle["plot_node_tables"] for n in table["nodes"] if n["node_kind"] == "state_change"]
    payload = {"expected_sha256": validated["bundle_sha256"], "reason": "模拟批准仅验证协议", "decisions": decisions, "acknowledge": True}
    with pytest.raises(ValueError, match="候选已变化"):
        workbench.approve({**payload, "expected_sha256": "0" * 64})
    if fail_during_apply:
        from longform_engine.planning import workflow
        snapshot = {p.relative_to(root): p.read_bytes() for name in ("10_bible", "20_outline", "30_state")
                    for p in (root / name).rglob("*") if p.is_file()}
        original_write = workflow._write_json
        writes = 0
        def fail_third_write(path, value):
            nonlocal writes
            writes += 1
            if writes == 3:
                raise OSError("injected planning transaction failure")
            return original_write(path, value)
        with monkeypatch.context() as injection:
            injection.setattr(workflow, "_write_json", fail_third_write)
            with pytest.raises(OSError, match="injected planning"):
                workbench.approve(payload)
        assert {p.relative_to(root): p.read_bytes() for name in ("10_bible", "20_outline", "30_state")
                for p in (root / name).rglob("*") if p.is_file()} == snapshot
        assert load_manifest(root, author["task_id"])["status"] == "validated"
        assert load_manifest(root, reviewer["task_id"])["status"] == "validated"
    result = workbench.approve(payload)
    assert result["status"] == "applied"
    assert (root / "20_outline/chapter_contracts/ch001.json").is_file()
    assert not (root / "20_outline/chapter_cards").exists()
    assert workbench.approve(payload)["status"] == "applied"
