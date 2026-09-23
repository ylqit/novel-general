"""Human-only proposals must stay non-canonical and bind exact source versions."""

from hashlib import sha256
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from longform_engine.config import load_project_config
from longform_engine.storage import init_project
from longform_engine.studio_learning import StudioLearning
from longform_engine.studio_content import StudioContent
from longform_engine.reader_feedback import validate_reader_feedback_decision
from longform_engine.semantic_protocols import canonical_json_hash


def learning_project(tmp_path):
    project = init_project(load_project_config(template="qidian-longform"), output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    source = project.root / "10_bible/style_profiles/adaptation_profile.json"
    source.parent.mkdir(exist_ok=True)
    evidence = project.root / "50_workbench/source_materials/craft_notes.md"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("测试来源：双方目标不同，先呈现分歧。", encoding="utf-8")
    relative = evidence.relative_to(project.root).as_posix()
    source.write_text(json.dumps({"schema": "adaptation_analysis_v1", "source_files": [relative],
        "source_hashes": {relative: sha256(evidence.read_bytes()).hexdigest()},
        "structural_patterns": ["对话先呈现分歧"], "pacing_patterns": [], "character_methods": [],
        "prose_constraints": [], "forbidden_copying": ["不照搬原句"]}, ensure_ascii=False), encoding="utf-8")
    return config, project.root, source, StudioLearning(config)


def craft_request(service):
    source = service.state()["sources"][0]
    return {"source_id": source["id"], "source_sha256": source["sha256"], "title": "先呈现分歧",
            "target": "第一卷合作场景", "conditions": "双方目标不一致时", "transformation": "让不同人物通过行动说明目的",
            "protect": "人物知识边界与本作声音", "expected_effect": "读者能判断选择的代价", "acknowledge": True}


def test_craft_selection_is_immutable_scoped_and_zero_canonical_pollution(tmp_path):
    _, root, source, service = learning_project(tmp_path)
    before = {p.relative_to(root): p.read_bytes() for folder in ("10_bible", "20_outline", "30_state", "40_manuscript")
              for p in (root / folder).rglob("*") if p.is_file()}
    body = craft_request(service)
    with pytest.raises(ValueError, match="确认"):
        service.act("adopt", {**body, "acknowledge": False})
    created = service.act("adopt", body)
    raw = (root / created["path"]).read_bytes()
    assert service.act("adopt", body) == created
    assert (root / created["path"]).read_bytes() == raw
    proposal = next(row for row in service.proposals() if row["kind"] == "craft")
    selected = [{"id": proposal["id"], "sha256": proposal["sha256"]}]
    assert service.proposal_inputs([]) == []
    assert source in service.proposal_inputs(selected)
    _, other_root, _, other_service = learning_project(tmp_path / "other")
    copied = other_root / created["path"]
    copied.parent.mkdir(parents=True, exist_ok=True)
    copied.write_bytes(raw)
    assert "提案属于其他项目" in other_service.proposals()[0]["issues"]
    for invalid in ([{**selected[0], "sha256": "0" * 64}], selected * 2, [{"id": "other-project", "sha256": proposal["sha256"]}]):
        with pytest.raises(ValueError):
            service.proposal_inputs(invalid)
    assert before == {p.relative_to(root): p.read_bytes() for folder in ("10_bible", "20_outline", "30_state", "40_manuscript")
                      for p in (root / folder).rglob("*") if p.is_file()}
    evidence = root / "50_workbench/source_materials/craft_notes.md"
    evidence_bytes = evidence.read_bytes()
    evidence.write_bytes(evidence_bytes + b"\n")
    with pytest.raises(ValueError, match="失效"):
        service.proposal_inputs(selected)
    evidence.write_bytes(evidence_bytes)
    source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert service.proposals()[0]["issues"]
    assert service.proposals()[0]["title"] == body["title"]
    with pytest.raises(ValueError, match="失效"):
        service.proposal_inputs(selected)
    with pytest.raises(ValueError, match="失效"):
        service.act("adopt", body)
    broken_note = root / "50_workbench/创作沙盒/broken.json"
    broken_note.write_text("{broken", encoding="utf-8")
    assert any(row["kind"] == "invalid" for row in service.state()["proposals"])
    assert next(row for row in StudioContent(root).documents() if row["relative"].endswith("broken.json"))["title"] == "broken（资料待核对）"


def test_feedback_requires_decision_and_conversion_then_rejects_tampered_hypothesis(tmp_path):
    _, root, _, service = learning_project(tmp_path)
    batch = {"schema": "reader_feedback_batch_v1", "batch_id": "sample.1", "scope": {"from_chapter": 1, "to_chapter": 3},
             "observations": ["测试读者未理解选择代价"], "hypotheses": [{"hypothesis_id": "hypothesis.1", "statement": "代价应在后续场景更清楚",
             "evidence_observation_indexes": [0], "possible_targets": ["planning"]}], "recorded_by": "human"}
    record = service.act("feedback-record", {"batch": batch, "acknowledge": True})
    assert service.act("feedback-record", {"batch": batch, "acknowledge": True}) == record
    base = {"batch_id": batch["batch_id"], "batch_sha256": canonical_json_hash(batch), "acknowledge": True}
    with pytest.raises(ValueError):
        service.act("feedback-convert", base)
    decisions = [{"hypothesis_id": "hypothesis.1", "decision": "accept", "target": "planning", "reason": "实际读者意见值得试验"}]
    service.act("feedback-decide", {**base, "decisions": decisions, "reason": "仅调整未来规划"})
    proposal_file = service.act("feedback-convert", base)["artifact_file"]
    selected = [{key: service.proposals()[0][key] for key in ("id", "sha256")}]
    assert len(service.proposal_inputs(selected)) == 3
    decision = service.state()["batches"][0]["decision"]
    assert validate_reader_feedback_decision(batch, {**decision, "decisions": decisions * 2})
    path = root / proposal_file
    proposal = json.loads(path.read_text(encoding="utf-8"))
    proposal["hypotheses"][0]["statement"] = "篡改决定之外的要求"
    path.write_text(json.dumps(proposal, ensure_ascii=False), encoding="utf-8")
    assert service.proposals()[0]["issues"]
    with pytest.raises(ValueError):
        service.proposal_inputs([{**selected[0], "sha256": sha256(path.read_bytes()).hexdigest()}])
    decision["decisions"][0].update(decision="reject", target=None)
    (root / "50_workbench/reader_feedback/sample.1.decision.json").write_text(json.dumps(decision), encoding="utf-8")
    assert "反馈提案没有人工接受的规划假设" in service.state()["proposals"][0]["issues"]


def test_planning_only_binds_explicit_current_proposals_and_expires_when_source_changes(tmp_path, monkeypatch):
    from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
    from longform_engine.planning.workbench import PlanningWorkbench
    config, root, source, service = learning_project(tmp_path)
    service.act("adopt", craft_request(service))
    selected = [{key: service.proposals()[0][key] for key in ("id", "sha256")}]
    monkeypatch.setattr("longform_engine.production.production_next", lambda _: {"status": "planning_refresh_required", "chapter_number": 1})
    workbench = PlanningWorkbench(config)
    first = workbench.create()
    assert source.relative_to(root).as_posix() not in [row["path"] for row in first["author"]["io"]["inputs"]]
    with pytest.raises(ValueError, match="不可变"):
        workbench.create(proposals=selected)
    second = workbench.create(rebuild=True, proposals=selected)
    author = load_manifest(root, second["author_task_id"])
    inputs = [row["path"] for row in author["io"]["inputs"]]
    assert service.proposals()[0]["path"] in inputs
    assert source.relative_to(root).as_posix() in inputs
    assert second["selected_proposals"] == selected
    assert load_manifest(root, first["author_task_id"])["status"] == "superseded"
    assert validate_manifest_strict(root, author, strict=True).ok
    from longform_engine.agent_pipeline import validate_production_agent_result
    from longform_engine.agent_results import build_agent_result_template
    from tests.test_v010_planning import planning_bundle
    def remove_control(value):
        if isinstance(value, dict):
            return {key: remove_control(item) for key, item in value.items()
                    if key not in {"candidate_sha256", "basis_sha256", "approved_by", "human_decision", "lifecycle"}}
        if isinstance(value, list):
            return [remove_control(item) for item in value]
        return value
    output = root / author["io"]["output"]["path"]
    document = {**build_agent_result_template(author), "body": "协议夹具：提案仅供评估，采用仍需审查与批准。",
                "extensions": {"planning_bundle": remove_control(planning_bundle())}}
    output.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    assert validate_production_agent_result(root, author, result_file=output).ok
    pointer = workbench.pointer.read_bytes()
    altered = json.loads(pointer)
    altered["selected_proposals"] = []
    workbench.pointer.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="不可变作者工作单"):
        workbench.prepare_review(author["task_id"])
    workbench.pointer.write_bytes(pointer)
    reviewed = workbench.prepare_review(author["task_id"])
    reviewer = load_manifest(root, reviewed["reviewer_task_id"])
    reviewer_paths = {item["path"] for item in reviewer["io"]["inputs"]}
    assert {path.relative_to(root).as_posix() for path in service.proposal_inputs(selected)} <= reviewer_paths
    assert output.relative_to(root).as_posix() not in reviewer_paths
    source.write_text("{}", encoding="utf-8")
    assert not validate_manifest_strict(root, author, strict=True).ok
    assert workbench.state()["stale_reasons"]


def test_effect_and_quality_bind_final_unicode_evidence(tmp_path):
    from longform_engine.quality.history import build_structure_observation
    _, root, source, service = learning_project(tmp_path)
    service.act("adopt", craft_request(service))
    proposal = service.proposals()[0]
    final = root / "40_manuscript/final/ch001.md"
    text = "# 测试\n\n😀他推开门，为留下证人承担了代价。\n"
    final.write_text(text, encoding="utf-8")
    current = StudioContent(root).chapter(1, version="final")
    body = {"proposal_id": proposal["id"], "proposal_sha256": proposal["sha256"], "chapter_number": 1,
            "final_sha256": current["sha256"], "quote": "承担了代价", "assessment": "uncertain", "note": "仅验证协议，文学效果未评测", "acknowledge": True}
    created = service.act("effect", body)
    assert service.act("effect", body) == created
    assert service.state()["effects"][0]["binding"]["start"] == text.index(body["quote"])
    source_bytes = source.read_bytes()
    source.write_bytes(source_bytes + b"\n")
    assert service.state()["effects"][0]["current"] is False
    source.write_bytes(source_bytes)
    assert service.state()["effects"][0]["current"] is True
    observation = build_structure_observation(chapter_number=1, text=text, chapter_contract={}, review=None)
    history = root / "30_state/quality/structure_history.jsonl"
    history.write_text(json.dumps(observation) + "\n", encoding="utf-8")
    assert StudioContent(root).quality_history()["chapters"][0]["structure"] == observation
    final.write_text(text + "正文变化。", encoding="utf-8")
    assert service.state()["effects"][0]["current"] is False
    assert "structure" not in StudioContent(root).quality_history()["chapters"][0]
    with pytest.raises(ValueError, match="版本已变化"):
        service.act("effect", body)


@pytest.mark.parametrize("firm_count", [1, 2, 3])
def test_approved_planning_view_fails_closed_after_source_change(tmp_path, monkeypatch, firm_count):
    from tests.test_current_planning_context import approved_project
    from tests.test_v010_planning import planning_bundle
    bundle = planning_bundle()
    bundle["rolling_window"].update(end_chapter=firm_count, tiers={"firm": [1, firm_count], "directional": None, "horizon": None})
    for key in ("chapter_forecasts", "plot_node_tables", "chapter_contracts"):
        bundle[key] = bundle[key][:firm_count]
    monkeypatch.setattr("tests.test_current_planning_context.planning_bundle", lambda: bundle)
    _, root = approved_project(tmp_path)
    content = StudioContent(root)
    view = content.planning_view()
    assert view["status"] == "current", view
    assert len(view["chapter_contracts"]) == firm_count
    path = root / "20_outline/book_spine.json"
    path.write_text("{}", encoding="utf-8")
    assert content.planning_view()["status"] == "stale"
    assert "book_spine" not in content.planning_view()


def test_revision_quote_locator_disambiguates_unicode_and_rejects_changed_fulltext():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the browser's Unicode offset contract")
    source = (Path(__file__).parents[1] / "templates/studio/review_forms.js").read_text(encoding="utf-8")
    function = source[source.index("function locateExact("):source.index("function syncRevisionRecord(")]
    script = function + """
const assert=require('node:assert/strict');
const text='😀重复。转身。重复。';
const binding={source:text,start:7,end:9,text:'重复'};
assert.deepEqual(locateExact(text,'重复',true,binding),{start:7,end:9,text:'重复'});
assert.throws(()=>locateExact(text,'重复',true));
assert.throws(()=>locateExact('前'+text,'重复',true,binding));
assert.deepEqual(locateExact(text,'转身',true),{start:4,end:6,text:'转身'});
assert.throws(()=>locateExact(text,'不存在',true));
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr


def test_learning_http_requires_session_csrf_and_scoped_source(tmp_path):
    import threading
    from tests.test_workspace_studio import _request
    from longform_engine.workspace_studio import WorkspaceStudioService, WorkspaceStudioHTTPServer

    _, _, _, learning = learning_project(tmp_path)
    service = WorkspaceStudioService(tmp_path)
    project_id = service.state()["projects"][0]["id"]
    server = WorkspaceStudioHTTPServer(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"/api/projects/{project_id}/learning"
        assert _request(server, "GET", endpoint)[0] == 403
        _, headers, _ = _request(server, "GET", f"/?token={server.bootstrap_token}")
        headers = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
        assert _request(server, "GET", endpoint, headers=headers)[0] == 200
        assert _request(server, "GET", f"/projects/{project_id}/quality", headers=headers)[0] == 200
        body = craft_request(learning)
        assert _request(server, "POST", endpoint + "/adopt", headers=headers, payload=body)[0] == 403
        headers["X-Studio-CSRF"] = server.csrf_token
        headers["Origin"] = f"http://127.0.0.1:{server.port}"
        assert _request(server, "POST", endpoint + "/adopt", headers=headers, payload={**body, "source_id": "other-project"})[0] == 409
        assert _request(server, "POST", endpoint + "/adopt", headers=headers, payload=body)[0] == 200
        assert len(learning.proposals()) == 1
        status, _, raw = _request(server, "GET", f"/api/projects/{project_id}/planning/state", headers=headers)
        assert status == 200
        assert json.loads(raw)["approved"]["status"] == "missing"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_planning_diff_includes_protections_dependencies_and_removed_chapters():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the planning presentation contract")
    source = (Path(__file__).parents[1] / "templates/studio/planning_view.js").read_text(encoding="utf-8")
    script = source + """
import assert from 'node:assert/strict';
const approved={status:'current',chapter_contracts:[
  {chapter_number:1,protected_invariants:['不得知情']},
  {chapter_number:2,protected_invariants:['保持代价']}],
  plot_node_tables:[{chapter_number:1,nodes:[{dependencies:['node.old']}]}]};
const bundle={chapter_contracts:[{chapter_number:1,protected_invariants:['允许知情']}],
  plot_node_tables:[{chapter_number:1,nodes:[{dependencies:['node.new']}]}]};
const html=planningChanges({status:'validated',approved,bundle});
for(const text of ['不得知情','允许知情','第 2 章','本轮移除','node.old','node.new']) assert.ok(html.includes(text),text);
assert.equal(planningChanges({status:'applied',approved,bundle}),'');
const unchanged=planningChanges({approved:{status:'current',book_spine:{premise:'<不可执行>',lifecycle:'approved'}},
  bundle:{book_spine:{premise:'<不可执行>',lifecycle:'proposed'}}});
assert.ok(unchanged.includes('没有变化'));
assert.ok(!unchanged.includes('<不可执行>'));
"""
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, encoding="utf-8", check=False)
    assert result.returncode == 0, result.stderr
