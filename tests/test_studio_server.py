import http.client
import io
import json
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from longform_engine.config import load_project_config
from longform_engine.fanfiction_sources import (
    FanfictionSourceError,
    register_source_work,
    source_library_catalog,
    source_library_status,
)
from longform_engine.storage import init_project
from longform_engine.studio_server import StudioHTTPServer, StudioServerError, StudioService


def _project(tmp_path: Path):
    template = load_project_config(
        template="qidian-longform",
        cli_overrides={"creation": {"mode": "original"}},
    )
    project = init_project(template, output=tmp_path / "中文小说项目")
    return load_project_config(project.project_config), project.root


def _upload_payload(work_id: str) -> dict:
    return {
        "work_id": work_id,
        "source_type": "用户动态资料",
        "version": "人工指定版本",
        "unit_range": "第一单元",
        "source_method": "浏览器人工导入",
        "rights_status": "public_domain_claimed",
        "retention_mode": "full_text",
        "storage_mode": "managed_copy",
    }


def _request(server, method, path, *, headers=None, payload=None, body=None, content_type=""):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    if payload is not None and body is not None:
        raise ValueError("request helper accepts payload or body, not both")
    request_body = body if body is not None else (
        None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    )
    actual_headers = {"Host": f"127.0.0.1:{server.port}", **(headers or {})}
    if request_body is not None:
        actual_headers["Content-Type"] = content_type or "application/json"
        actual_headers["Content-Length"] = str(len(request_body))
    connection.request(method, path, body=request_body, headers=actual_headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_browser_upload_is_streamed_to_staging_and_cancel_never_changes_formal_index(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "用户原著资料库").resolve()))
    config, _root = _project(tmp_path)
    work = register_source_work(
        name="控制台测试作品", creator="测试作者", approved_by="human"
    )
    service = StudioService(config)
    session = service.start_upload(_upload_payload(work["work_id"]))
    content = ("一段只进入暂存区的资料。" * 1000).encode()
    received = service.receive_upload(
        session_id=session["session_id"],
        relative_path="第一集/资料.md",
        length=len(content),
        stream=io.BytesIO(content),
    )
    assert received["size_bytes"] == len(content)
    assert source_library_status()["item_count"] == 0

    cancelled = service.cancel_upload(session["session_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["formal_index_changed"] is False
    assert source_library_status()["item_count"] == 0
    assert source_library_catalog()["items"] == []


@pytest.mark.parametrize(
    "path",
    [
        "../逃逸.txt",
        "/绝对路径.txt",
        "C:/服务器路径.txt",
        "\\\\server\\share\\资料.txt",
        "正常目录/../逃逸.txt",
        "CON.txt",
        "目录/NUL.md",
        "尾点./资料.txt",
        "目录/尾空格 .txt ",
    ],
)
def test_browser_upload_rejects_absolute_traversal_unc_and_windows_reserved_paths(
    tmp_path, monkeypatch, path
):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "用户原著资料库").resolve()))
    config, _root = _project(tmp_path)
    service = StudioService(config)
    session = service.start_upload(_upload_payload("work_pending"))
    with pytest.raises(StudioServerError, match="相对路径无效"):
        service.receive_upload(
            session_id=session["session_id"],
            relative_path=path,
            length=4,
            stream=io.BytesIO(b"test"),
        )
    assert source_library_status()["item_count"] == 0


def test_browser_upload_only_creates_ingest_preview_until_separate_human_apply(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "用户原著资料库").resolve()))
    config, _root = _project(tmp_path)
    work = register_source_work(
        name="分组预览作品", creator="测试作者", approved_by="human"
    )
    service = StudioService(config)
    session = service.start_upload(_upload_payload(work["work_id"]))
    content = "第一集资料，只生成导入预览。".encode()
    service.receive_upload(
        session_id=session["session_id"],
        relative_path="第一集/笔记.md",
        length=len(content),
        stream=io.BytesIO(content),
    )
    plan = service.finalize_upload(session["session_id"])
    assert plan["schema"] == "source_ingest_batch_v1"
    assert plan["status"] == "awaiting_import_approval"
    assert source_library_status()["item_count"] == 0
    assert len(source_library_catalog()["batches"]) == 1

    groups = json.loads(json.dumps(plan["groups"], ensure_ascii=False))
    groups[0]["approved"] = True
    confirmed = service.confirm_ingest_groups(
        {"batch_id": plan["batch_id"], "groups": groups, "approved_by": "human"}
    )
    assert confirmed["approved_group_count"] == 1
    assert source_library_status()["item_count"] == 0

    applied = service.apply_ingest(
        {"batch_id": plan["batch_id"], "approved_by": "human"}
    )
    assert applied["status"] == "applied"
    assert source_library_status()["item_count"] == 1


def test_source_library_lock_blocks_a_second_writer(tmp_path, monkeypatch):
    library = (tmp_path / "用户原著资料库").resolve()
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str(library))
    lock = library / "暂存区" / "资料库.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text(
        json.dumps(
            {
                "schema": "source_library_lock_v1",
                "owner_token": "another-writer",
                "command": "ingest-apply",
                "pid": 42,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(FanfictionSourceError, match="资料库正被另一项写入任务占用"):
        register_source_work(name="锁测试", creator="测试作者", approved_by="human")


def test_studio_http_boundary_enforces_bootstrap_host_origin_csrf_cookie_and_csp(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "用户原著资料库").resolve()))
    config, _root = _project(tmp_path)
    server = StudioHTTPServer(StudioService(config), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, _body = _request(server, "GET", "/")
        assert status == 403
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

        status, headers, _body = _request(
            server, "GET", f"/?token={server.bootstrap_token}"
        )
        assert status == 303
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert _request(server, "GET", f"/?token={server.bootstrap_token}")[0] == 403

        status, headers, page = _request(server, "GET", "/", headers={"Cookie": cookie})
        assert status == 200
        assert "form-action 'self'" in headers["Content-Security-Policy"]
        assert f'nonce="{server.csp_nonce}"'.encode() in page
        assert "Canon审批".encode() in page
        assert b"application/octet-stream" not in page
        assert b"new FormData()" in page

        valid = {
            "Cookie": cookie,
            "Origin": f"http://127.0.0.1:{server.port}",
            "X-Studio-CSRF": server.csrf_token,
        }
        payload = _upload_payload("work_pending")
        assert _request(
            server,
            "POST",
            "/api/upload/start",
            headers={**valid, "Origin": "https://evil.invalid"},
            payload=payload,
        )[0] == 403
        assert _request(
            server,
            "POST",
            "/api/upload/start",
            headers={**valid, "X-Studio-CSRF": "wrong"},
            payload=payload,
        )[0] == 403
        assert _request(
            server,
            "POST",
            "/api/upload/start",
            headers=valid,
            payload=payload,
        )[0] == 200
        assert _request(
            server,
            "GET",
            "/api/state",
            headers={"Cookie": cookie, "Host": "evil.invalid"},
        )[0] == 403
        assert _request(server, "GET", "/..%2fproject.yaml", headers={"Cookie": cookie})[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_studio_http_multipart_upload_streams_one_file_and_rejects_octet_stream(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "用户原著资料库").resolve()))
    config, _root = _project(tmp_path)
    work = register_source_work(name="multipart测试作品", creator="测试作者", approved_by="human")
    server = StudioHTTPServer(StudioService(config), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, _body = _request(server, "GET", f"/?token={server.bootstrap_token}")
        assert status == 303
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        valid = {
            "Cookie": cookie,
            "Origin": f"http://127.0.0.1:{server.port}",
            "X-Studio-CSRF": server.csrf_token,
        }
        status, _headers, response = _request(
            server,
            "POST",
            "/api/upload/start",
            headers=valid,
            payload=_upload_payload(work["work_id"]),
        )
        assert status == 200
        session_id = json.loads(response)["result"]["session_id"]
        content = "分段上传的中文资料。".encode("utf-8")
        boundary = "LongformBoundary7MA4YWxkTrZu0gW"
        multipart = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="source.md"\r\n'
            "Content-Type: text/markdown\r\n\r\n"
        ).encode("ascii") + content + f"\r\n--{boundary}--\r\n".encode("ascii")
        upload_path = (
            f"/api/upload/file?session={quote(session_id)}&path={quote('第一集/资料.md')}"
        )
        assert _request(
            server,
            "POST",
            upload_path,
            headers=valid,
            body=content,
            content_type="application/octet-stream",
        )[0] == 403
        status, _headers, response = _request(
            server,
            "POST",
            upload_path,
            headers=valid,
            body=multipart,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        assert status == 200
        assert json.loads(response)["result"]["size_bytes"] == len(content)
        staged = source_library_status()["library_root"]
        assert (
            Path(staged) / "暂存区" / session_id / "文件" / "第一集" / "资料.md"
        ).read_bytes() == content
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_creation_goal_and_market_claim_remain_noncanonical_workbench_records(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LONGFORM_SOURCE_LIBRARY", str((tmp_path / "用户原著资料库").resolve()))
    config, root = _project(tmp_path)
    service = StudioService(config)
    goal = service.save_creation_goal(
        {
            "purpose": "长篇连载",
            "work_type": "同人",
            "format": "长篇",
            "target_platform": "起点",
            "update_capacity": "每周三章",
            "validation_period": "四周",
            "commercial_intent": False,
            "fanfiction_rights_risk_confirmed": True,
        }
    )
    claim = service.save_market_claim(
        {
            "claim": "课程提出某个短篇价格",
            "source": "用户课程讲义",
            "observed_on": "2026-08-24",
            "category": "课程观点",
        }
    )
    assert goal["file"].startswith("50_workbench/")
    assert claim["advisory_only"] is True
    assert claim["canon"] is False
    assert claim["quality_gate"] is False
    assert not (root / "10_bible" / "creation_goal.yaml").exists()
