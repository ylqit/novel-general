import http.client
import json
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from longform_engine.cli import build_parser
from longform_engine.agent_jobs import CodexAgentJobManager
from longform_engine.config import load_project_config
from longform_engine.intelligence import create_intelligence_task
from longform_engine.storage import init_project
from longform_engine.studio_instance import StudioInstanceError, WorkspaceStudioInstance
from longform_engine.workspace_studio import (
    WorkspaceStudioError,
    WorkspaceStudioHTTPServer,
    WorkspaceStudioService,
    workspace_studio_page_html,
)
from tests.project_fixtures import (
    approve_story_candidate,
    complete_unified_semantic_lifecycle,
    prepare_unified_semantic_bundle,
)
from tests.test_story_architecture_v050 import seed_candidate
from tests.test_intelligence_tasks import book_design_payload, prepare_book_design, seed_project


def _create_payload(*, mode: str = "original") -> dict:
    sources = []
    if mode == "fanfiction":
        sources = [
            {
                "source_id": "jujutsu_kaisen",
                "title": "咒术回战",
                "creator": "芥见下下",
                "canon_cutoff": "用户确认的单行本截止点",
                "rights_status": "unverified",
                "commercial_intent": False,
                "allowed_elements": ["人物", "世界观", "术式规则"],
                "platform_policy_url": "",
            }
        ]
    return {
        "title": "咒术回战同人" if mode == "fanfiction" else "原创长篇",
        "slug": "jujutsu-fanfic" if mode == "fanfiction" else "original-longform",
        "creation_mode": mode,
        "continuity_mode": "canon_divergent" if mode == "fanfiction" else "canon_compliant",
        "target_platform": "qidian",
        "target_audience": "起点中文网长篇读者",
        "writing_style": "第三人称有限视角",
        "core_promise": "持续成长、阶段兑现并保留人物选择的代价。",
        "main_question": "主角如何改变既定命运？",
        "ending_direction": "终局完成主线收束并支付明确代价。",
        "forbidden_experience": ["连续多章无推进", "人物无理由降智"],
        "automation_level": "human_approved_agent_workflow",
        "target_total_characters": 1_000_000,
        "chapter_target_characters": 3_000,
        "volume_target_characters": 200_000,
        "planning_horizon": 20,
        "refill_threshold": 8,
        "rights_risk_acknowledged": mode == "fanfiction",
        "sources": sources,
    }


def _request(server, method: str, path: str, *, headers=None, payload=None, timeout=10):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=timeout)
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    actual_headers = {"Host": f"127.0.0.1:{server.port}", **(headers or {})}
    if body is not None:
        actual_headers["Content-Type"] = "application/json"
        actual_headers["Content-Length"] = str(len(body))
    connection.request(method, path, body=body, headers=actual_headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_workspace_requires_an_explicit_absolute_non_root_directory(tmp_path, monkeypatch):
    relative = Path("relative-workspace")
    with pytest.raises(WorkspaceStudioError, match="绝对目录"):
        WorkspaceStudioService(relative)

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(WorkspaceStudioError, match="用户目录"):
        WorkspaceStudioService(tmp_path)


def test_workspace_creates_original_and_fanfiction_projects_with_open_book_state(tmp_path):
    workspace = tmp_path / "小说工作区"
    service = WorkspaceStudioService(workspace, create=True)

    original = service.create_project(_create_payload())
    fanfiction = service.create_project(_create_payload(mode="fanfiction"))

    assert original["project"]["creation_mode"] == "original"
    assert fanfiction["project"]["creation_mode"] == "fanfiction"
    assert original["next_action"]["status"] != "ready_for_open_book"
    assert fanfiction["next_action"]["status"] != "ready_for_open_book"
    assert len(service.state()["projects"]) == 2
    assert service.state()["projects"][0]["next_action"]["status"]

    original_config = load_project_config(workspace / "original-longform" / "project.yaml")
    fanfiction_config = load_project_config(workspace / "jujutsu-fanfic" / "project.yaml")
    assert original_config.data["fanfiction"]["sources"] == []
    assert fanfiction_config.data["fanfiction"]["sources"][0]["rights_status"] == "unverified"
    assert (workspace / "original-longform" / "00_governance" / "reader_contract.md").is_file()
    assert (workspace / "jujutsu-fanfic" / "50_workbench" / "同人原著资料").is_dir()


def test_workspace_rejects_duplicate_slug_outside_import_and_unacknowledged_fanfiction(
    tmp_path,
):
    workspace = tmp_path / "小说工作区"
    service = WorkspaceStudioService(workspace, create=True)
    service.create_project(_create_payload())

    with pytest.raises(WorkspaceStudioError, match="已经存在"):
        service.create_project(_create_payload())

    payload = _create_payload(mode="fanfiction")
    payload["rights_risk_acknowledged"] = False
    with pytest.raises(WorkspaceStudioError, match="权利风险"):
        service.create_project(payload)

    outside_config = init_project(
        load_project_config(template="qidian-longform"), output=tmp_path / "外部项目"
    ).project_config
    with pytest.raises(WorkspaceStudioError, match="工作区之外"):
        service.import_project(outside_config)


def test_workspace_discovery_ignores_staging_copies_and_persists_explicit_nested_import(tmp_path):
    workspace = tmp_path / "小说工作区"
    service = WorkspaceStudioService(workspace, create=True)
    service.create_project(_create_payload())
    staging_copy = workspace / "original-longform" / "70_runtime" / "agent_jobs" / "job_x" / "staging"
    staging_copy.mkdir(parents=True)
    staging_copy.joinpath("project.yaml").write_text(
        (workspace / "original-longform" / "project.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    nested = init_project(
        load_project_config(template="qidian-longform"),
        output=workspace / "导入区" / "nested-project",
    ).project_config

    assert len(service.state()["projects"]) == 1
    imported = service.import_project(nested)
    assert imported["project"]["relative_config"] == "导入区/nested-project/project.yaml"
    assert len(WorkspaceStudioService(workspace).state()["projects"]) == 2


def test_workspace_deep_links_are_same_server_and_never_mutate_canonical_state(tmp_path):
    workspace = tmp_path / "小说工作区"
    service = WorkspaceStudioService(workspace, create=True)
    created = service.create_project(_create_payload())
    project_id = created["project"]["id"]
    root = workspace / "original-longform"
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    server = WorkspaceStudioHTTPServer(service, port=0, initial_project_id=project_id, initial_chapter=12)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, _body = _request(server, "GET", f"/?token={server.bootstrap_token}")
        assert status == 303
        assert headers["Location"] == f"/projects/{project_id}/chapters/12"
        cookie = headers["Set-Cookie"].split(";", 1)[0]

        assert _request(server, "GET", "/", headers={"Cookie": cookie})[0] == 200
        assert _request(
            server, "GET", f"/projects/{project_id}", headers={"Cookie": cookie}
        )[0] == 200
        status, _headers, body = _request(
            server,
            "GET",
            f"/api/projects/{project_id}/chapters/12",
            headers={"Cookie": cookie},
        )
        assert status == 200
        chapter = json.loads(body)
        assert chapter["chapter_number"] == 12
        assert chapter["canonical_write_allowed"] is False
        assert chapter["actions"]["finalize"] is False
        assert chapter["actions"]["semantic_apply"] is False
        assert chapter["actions"]["close"] is False
        status, _headers, body = _request(
            server,
            "POST",
            "/api/instance/bootstrap",
            headers={"X-Studio-Launcher": server.launcher_secret},
            payload={"path": f"/projects/{project_id}"},
        )
        assert status == 200
        fresh_url = json.loads(body)["bootstrap_url"]
        fresh_path = urlsplit(fresh_url).path + "?" + urlsplit(fresh_url).query
        status, fresh_headers, _body = _request(server, "GET", fresh_path)
        assert status == 303
        assert fresh_headers["Location"] == f"/projects/{project_id}"
        assert _request(server, "GET", fresh_path)[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_studio_cli_accepts_workspace_project_and_chapter_deep_link_modes(tmp_path):
    parser = build_parser()
    workspace_args = parser.parse_args(
        ["studio", "serve", "--workspace", str(tmp_path), "--create-workspace", "--no-open"]
    )
    project_args = parser.parse_args(
        ["studio", "serve", str(tmp_path / "novel" / "project.yaml"), "--chapter", "7"]
    )
    shortcut_args = parser.parse_args(
        [
            "studio",
            "shortcut-install",
            "--workspace",
            str(tmp_path),
            "--desktop",
            str(tmp_path / "Desktop"),
        ]
    )

    assert workspace_args.workspace == str(tmp_path)
    assert workspace_args.config is None
    assert workspace_args.create_workspace is True
    assert project_args.workspace is None
    assert project_args.chapter == 7
    assert shortcut_args.workspace == str(tmp_path)
    assert shortcut_args.desktop == str(tmp_path / "Desktop")


def test_workspace_instance_reuses_live_server_and_releases_exact_lifecycle_files(tmp_path):
    workspace = tmp_path / "小说工作区"
    service = WorkspaceStudioService(workspace, create=True)
    server = WorkspaceStudioHTTPServer(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    owner = WorkspaceStudioInstance(workspace)
    owner.acquire()
    owner.publish(port=server.port, launcher_secret=server.launcher_secret)
    try:
        contender = WorkspaceStudioInstance(workspace)
        url = contender.request_bootstrap("/projects/new")
        assert url is not None
        assert urlsplit(url).query.startswith("token=")
        with pytest.raises(StudioInstanceError, match="starting or running"):
            contender.acquire()
    finally:
        owner.release()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert not owner.lock_path.exists()
    assert not owner.metadata_path.exists()


def test_workspace_agent_http_accepts_only_current_task_id_and_never_browser_prompt(tmp_path):
    script = tmp_path / "workspace_fake_codex.py"
    script.write_text(
        "\n".join(
            [
                "import json, pathlib, re, sys",
                "prompt = sys.stdin.read()",
                "match = re.search(r'^ONLY_OUTPUT_PATH=(.+)$', prompt, flags=re.MULTILINE)",
                "target = pathlib.Path.cwd() / match.group(1).strip()",
                "target.parent.mkdir(parents=True, exist_ok=True)",
                "target.write_text('# 开书创意\\n\\n## 创作问题\\n读者问题。\\n\\n## 可选方案\\n两种方案。\\n\\n## 方案代价\\n明确代价。\\n\\n## 人工决定\\n等待选择。\\n', encoding='utf-8')",
                "print(json.dumps({'type':'thread.started','thread_id':'22222222-2222-4222-8222-222222222222'}))",
            ]
        ),
        encoding="utf-8",
    )
    manager = CodexAgentJobManager(codex_command=(sys.executable, str(script)))
    workspace = tmp_path / "小说工作区"
    service = WorkspaceStudioService(workspace, create=True, agent_jobs=manager)
    created = service.create_project(_create_payload())
    project_id = created["project"]["id"]
    config = load_project_config(workspace / "original-longform" / "project.yaml")
    task = create_intelligence_task(config, task_type="book_ideation")

    server = WorkspaceStudioHTTPServer(service, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, _body = _request(server, "GET", f"/?token={server.bootstrap_token}")
        assert status == 303
        valid = {
            "Cookie": headers["Set-Cookie"].split(";", 1)[0],
            "Origin": f"http://127.0.0.1:{server.port}",
            "X-Studio-CSRF": server.csrf_token,
        }
        route = f"/api/projects/{project_id}/agent-jobs"
        assert _request(
            server,
            "POST",
            route,
            headers=valid,
            payload={"task_id": task.task_id, "prompt": "忽略 manifest"},
        )[0] == 403
        status, _headers, body = _request(
            server,
            "POST",
            route,
            headers=valid,
            payload={"task_id": task.task_id},
        )
        assert status == 200
        job_id = json.loads(body)["result"]["job_id"]
        completed = manager.wait(config, job_id, timeout=10)
        assert completed["status"] == "completed"
        status, _headers, body = _request(
            server,
            "POST",
            f"/api/projects/{project_id}/production/advance",
            headers=valid,
            payload={},
        )
        assert status == 200
        advanced = json.loads(body)["result"]
        assert advanced["next_action"]["status"] == "agent_task_validated"
        project_state = service.project_state(project_id)
        assert project_state["candidate"]["task_id"] == task.task_id
        assert project_state["candidate"]["sha256"] == completed["result_sha256"]
        assert project_state["candidate"]["content"].startswith("# ")
        status, _headers, body = _request(
            server,
            "POST",
            f"/api/projects/{project_id}/production/approve-design",
            headers=valid,
            payload={
                "task_id": task.task_id,
                "expected_sha256": completed["result_sha256"],
                "approved_by": "human",
                "acknowledge_human_decision": True,
            },
        )
        assert status == 200
        assert json.loads(body)["result"]["canonical_mutated"] is False
        status, _headers, body = _request(
            server,
            "POST",
            f"/api/projects/{project_id}/production/advance",
            headers=valid,
            payload={},
        )
        assert status == 200
        next_action = json.loads(body)["result"]["next_action"]
        assert next_action["status"] == "agent_task_awaiting_agent"
        assert next_action["task_type"] == "design_semantic_compile"
        status, _headers, body = _request(
            server,
            "GET",
            f"{route}/{job_id}",
            headers={"Cookie": valid["Cookie"]},
        )
        assert status == 200
        assert json.loads(body)["canonical_mutated"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_workspace_page_contains_creation_dashboard_and_explicit_human_boundaries():
    page = workspace_studio_page_html("csrf-token", "nonce-token")

    assert "创建原创小说" in page
    assert "创建同人小说" in page
    assert "原著权利状态" in page
    assert "资料留存策略" in page
    assert "交给 Codex" in page
    assert "批准当前候选" in page
    assert "写入 Canon" in page
    assert "明确 finalize" in page
    assert "明确 semantic apply" in page
    assert "确认并 apply 事件状态" in page
    assert "确认并 apply 承诺证据" in page
    assert "明确 close" in page
    assert "浏览器不会提交任意 Prompt" in page
    assert "__CSRF_TOKEN__" not in page


def test_workspace_requires_exact_human_confirmation_for_compiled_design_apply(tmp_path):
    config = seed_project(tmp_path)
    root = tmp_path / "novel"
    _document, delta = prepare_book_design(config, root, book_design_payload())
    service = WorkspaceStudioService(tmp_path)
    project_id = service.import_project(config.path)["project"]["id"]

    advanced = service.advance_production(project_id)
    assert advanced["next_action"]["status"] == "agent_task_validated"
    state = service.project_state(project_id)
    compiled = state["canonical_candidate"]
    before = (root / "10_bible" / "world.md").read_bytes()
    with pytest.raises(WorkspaceStudioError, match="明确确认"):
        service.apply_compiled_design_candidate(
            project_id,
            {
                "task_id": compiled["task_id"],
                "expected_sha256": compiled["sha256"],
                "approved_by": "human",
                "acknowledge_canonical_write": False,
            },
        )
    assert (root / "10_bible" / "world.md").read_bytes() == before

    result = service.apply_compiled_design_candidate(
        project_id,
        {
            "task_id": compiled["task_id"],
            "expected_sha256": compiled["sha256"],
            "approved_by": "human",
            "acknowledge_canonical_write": True,
        },
    )
    assert result["canonical_mutated"] is True
    assert "Rules have visible costs" in (root / "10_bible" / "world.md").read_text(
        encoding="utf-8"
    )


def test_workspace_chapter_finalize_and_semantic_apply_are_separate_human_gates(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    approve_story_candidate(root, config)
    service = WorkspaceStudioService(root.parent)
    project_id = service.import_project(config.path)["project"]["id"]
    state = service.project_state(project_id)
    assert state["next_action"]["status"] == "awaiting_finalize"
    draft_hash = state["chapter_context"]["draft"]["sha256"]

    with pytest.raises(WorkspaceStudioError, match="明确确认"):
        service.finalize_current_chapter(
            project_id,
            {
                "chapter_number": 1,
                "expected_draft_sha256": draft_hash,
                "approved_by": "human",
                "acknowledge_finalize": False,
            },
        )
    finalized = service.finalize_current_chapter(
        project_id,
        {
            "chapter_number": 1,
            "expected_draft_sha256": draft_hash,
            "approved_by": "human",
            "acknowledge_finalize": True,
        },
    )
    assert finalized["canonical_mutated"] is True
    semantic_output = prepare_unified_semantic_bundle(root, config, 1)
    advanced = service.advance_production(project_id)
    assert advanced["next_action"]["status"] == "agent_task_validated"
    state = service.project_state(project_id)
    semantic = state["semantic_candidate"]
    assert semantic["path"] == semantic_output.relative_to(root).as_posix()

    with pytest.raises(WorkspaceStudioError, match="明确确认"):
        service.apply_chapter_semantic_candidate(
            project_id,
            {
                "task_id": semantic["task_id"],
                "chapter_number": 1,
                "expected_sha256": semantic["sha256"],
                "approved_by": "human",
                "acknowledge_semantic_apply": False,
            },
        )
    applied = service.apply_chapter_semantic_candidate(
        project_id,
        {
            "task_id": semantic["task_id"],
            "chapter_number": 1,
            "expected_sha256": semantic["sha256"],
            "approved_by": "human",
            "acknowledge_semantic_apply": True,
        },
    )
    assert applied["canonical_mutated"] is True
    assert (root / "30_state" / "semantic_ledger" / "ch001.json").is_file()
    state = service.project_state(project_id)
    assert state["next_action"]["status"] == "awaiting_author_voice_approval"
    voice = state["chapter_confirmation"]
    voice["record"]["purpose"] = "保留真实人工改稿中的人物选择与代价。"
    voice["record"]["abstract_principle"] = "让人物的犹疑改变下一步行动条件。"
    approved_voice = service.approve_author_voice_pair(
        project_id,
        {
            "chapter_number": 1,
            "expected_final_sha256": voice["final_sha256"],
            "record": voice["record"],
            "approved_by": "human",
            "acknowledge_voice_apply": True,
        },
    )
    assert approved_voice["canonical_mutated"] is True

    complete_unified_semantic_lifecycle(
        root, config, 1, approve_voice=False, close=False
    )
    state = service.project_state(project_id)
    assert state["next_action"]["status"] == "awaiting_chapter_close"
    final_hash = state["chapter_context"]["final"]["sha256"]
    with pytest.raises(WorkspaceStudioError, match="明确确认"):
        service.close_current_chapter(
            project_id,
            {
                "chapter_number": 1,
                "expected_final_sha256": final_hash,
                "approved_by": "human",
                "acknowledge_chapter_close": False,
            },
        )
    closed = service.close_current_chapter(
        project_id,
        {
            "chapter_number": 1,
            "expected_final_sha256": final_hash,
            "approved_by": "human",
            "acknowledge_chapter_close": True,
        },
    )
    assert closed["canonical_mutated"] is True


def test_workspace_chapter_route_mounts_the_existing_review_desk_on_the_same_server(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    service = WorkspaceStudioService(root.parent)
    project_id = service.import_project(config.path)["project"]["id"]
    server = WorkspaceStudioHTTPServer(
        service, port=0, initial_project_id=project_id, initial_chapter=1
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, _body = _request(server, "GET", f"/?token={server.bootstrap_token}")
        assert status == 303
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        chapter_path = f"/projects/{project_id}/chapters/1"
        # Full evidence verification is deliberately uncached. This is a routing
        # contract test, so allow coverage tracing overhead without a 10-second SLA.
        status, _headers, body = _request(
            server, "GET", chapter_path, headers={"Cookie": cookie}, timeout=60
        )
        assert status == 200
        page = body.decode("utf-8")
        assert "人工可视化深审" in page
        assert f"/api/projects/{project_id}/chapters/1/review/state" in page

        review_api = f"/api/projects/{project_id}/chapters/1/review"
        status, _headers, body = _request(
            server, "GET", f"{review_api}/state", headers={"Cookie": cookie}, timeout=60
        )
        assert status == 200
        review_state = json.loads(body)
        assert review_state["canonical_write_allowed"] is False
        valid = {
            "Cookie": cookie,
            "Origin": f"http://127.0.0.1:{server.port}",
            "X-Studio-CSRF": server.csrf_token,
        }
        status, _headers, body = _request(
            server,
            "POST",
            f"{review_api}/human-review/prepare",
            headers=valid,
            timeout=60,
            payload={
                "expected_candidate_sha256": review_state["draft"]["sha256"]
            },
        )
        assert status == 200
        assert json.loads(body)["result"]["canonical_mutated"] is False
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_chapter_intent_web_uses_named_characters_and_rejects_stale_edits(tmp_path):
    from tests.test_current_planning_context import approved_project
    from longform_engine.human_chapter_intent import require_current_human_chapter_intent

    _config, root = approved_project(tmp_path)
    (root / "10_bible/characters.json").write_text(json.dumps([
        {"id": "character:ari", "name": "阿黎"}, {"id": "character:mira", "name": "米拉"}
    ], ensure_ascii=False), encoding="utf-8")
    service = WorkspaceStudioService(tmp_path)
    project_id = service._project_id(root / "project.yaml")
    before = service.chapter_intent_state(project_id, 1)
    assert before["candidate"] is None
    assert before["characters"][0]["name"] == "阿黎"
    state = service.chapter_intent_action(project_id, 1, "create", {})
    assert state["candidate"]["expression_focus"] == {"pov_character_ids": [], "scene_kind": ""}
    fields = {
        "story_intent": "让对方拿到路线选择权后主动拒绝近路。",
        "key_character_choice": "米拉选择多走一段路来核实来人的身份。",
        "emotional_truth": "信任增加之后仍然保留不愿说出口的担心。",
        "pov_voice_intent": "两名视角人物关注不同线索，避免互相解释已知事实。",
        "expression_focus": {"pov_character_ids": ["character:ari", "character:mira"], "scene_kind": "对峙"},
        "protected_items": ["不得提前知道近路的出口"],
    }
    saved = service.chapter_intent_action(project_id, 1, "save", {
        "expected_sha256": state["candidate_sha256"], "fields": fields,
    })
    assert saved["validation"]["ok"] is True
    with pytest.raises(WorkspaceStudioError, match="已变化"):
        service.chapter_intent_action(project_id, 1, "save", {
            "expected_sha256": state["candidate_sha256"], "fields": fields,
        })
    with pytest.raises(WorkspaceStudioError, match="明确确认"):
        service.chapter_intent_action(project_id, 1, "apply", {
            "expected_sha256": saved["candidate_sha256"], "acknowledge_human_decision": False,
        })
    service.chapter_intent_action(project_id, 1, "apply", {
        "expected_sha256": saved["candidate_sha256"], "acknowledge_human_decision": True,
    })
    assert require_current_human_chapter_intent(root, 1)["payload"]["expression_focus"] == fields["expression_focus"]
    with pytest.raises(WorkspaceStudioError, match="项目不存在"):
        service.chapter_intent_state("project_" + "0" * 20, 1)
