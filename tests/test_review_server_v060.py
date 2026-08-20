import http.client
import json
import threading

import pytest

from longform_engine.human_review_consultation import consultation_status
from longform_engine.human_story_review import (
    apply_human_story_review,
    create_human_story_review_task,
    human_story_review_status,
)
from longform_engine.repair_coordination import (
    create_repair_synthesis_task,
    repair_attempt_status,
    review_barrier_status,
    validate_repair_plan,
)
from longform_engine.review_server import ReviewDeskService, ReviewHTTPServer
from longform_engine.storage import acquire_project_lock
from tests.test_agent_task_protocol import repair_plan_markdown
from tests.test_story_architecture_v050 import seed_candidate, write_review


def request(server, method, path, *, headers=None, payload=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    actual_headers = {"Host": f"127.0.0.1:{server.port}", **(headers or {})}
    if body is not None:
        actual_headers["Content-Type"] = "application/json"
        actual_headers["Content-Length"] = str(len(body))
    connection.request(method, path, body=body, headers=actual_headers)
    response = connection.getresponse()
    data = response.read()
    result = response.status, dict(response.getheaders()), data
    connection.close()
    return result


def test_loopback_review_server_burns_token_and_rejects_host_origin_csrf_path_and_xss(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    injected = "<script>window.projectTextExecuted=true</script>"
    draft.write_text(draft.read_text(encoding="utf-8") + injected, encoding="utf-8")
    server = ReviewHTTPServer(ReviewDeskService(config, chapter_number=1), port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, headers, _body = request(server, "GET", "/")
        assert status == 403
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

        status, headers, _body = request(
            server, "GET", f"/?token={server.bootstrap_token}"
        )
        assert status == 303
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        assert "HttpOnly" in headers["Set-Cookie"]
        assert "SameSite=Strict" in headers["Set-Cookie"]
        assert request(server, "GET", f"/?token={server.bootstrap_token}")[0] == 403

        status, headers, body = request(
            server, "GET", "/", headers={"Cookie": cookie}
        )
        page = body.decode("utf-8")
        assert status == 200
        assert injected not in page
        assert f"nonce-{server.csp_nonce}" in headers["Content-Security-Policy"]
        assert f'nonce="{server.csp_nonce}"' in page

        status, headers, body = request(
            server, "GET", "/api/state", headers={"Cookie": cookie}
        )
        assert status == 200
        assert headers["Content-Type"].startswith("application/json")
        assert b"<script>" not in body
        assert injected in json.loads(body)["draft"]["text"]
        assert request(server, "GET", "/..%2fproject.yaml", headers={"Cookie": cookie})[0] == 403

        valid_headers = {
            "Cookie": cookie,
            "Origin": f"http://127.0.0.1:{server.port}",
            "X-Review-CSRF": server.csrf_token,
        }
        assert request(
            server,
            "POST",
            "/api/not-found",
            headers={**valid_headers, "Origin": "https://evil.invalid"},
            payload={},
        )[0] == 403
        assert request(
            server,
            "POST",
            "/api/not-found",
            headers={**valid_headers, "X-Review-CSRF": "wrong"},
            payload={},
        )[0] == 403
        assert request(
            server, "POST", "/api/not-found", headers=valid_headers, payload={}
        )[0] == 404
        assert request(
            server,
            "GET",
            "/api/state",
            headers={"Cookie": cookie, "Host": "evil.invalid"},
        )[0] == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_review_desk_mutations_obey_project_lock(tmp_path):
    config, _root, _task = seed_candidate(tmp_path)
    service = ReviewDeskService(config, chapter_number=1)
    digest = service.state()["draft"]["sha256"]

    with acquire_project_lock(config, owner="test", command="hold"):
        with pytest.raises(ValueError, match="Project lock already exists"):
            service.prepare_human_review(expected_candidate_sha256=digest)


def test_manual_full_repair_submit_consumes_budget_and_stales_old_review_and_consultation(tmp_path):
    config, root, _task = seed_candidate(tmp_path)
    review_task = create_human_story_review_task(config, chapter_number=1)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    original = draft.read_text(encoding="utf-8")

    service = ReviewDeskService(config, chapter_number=1)
    before_hash = service.state()["draft"]["sha256"]
    consult = service.create_consultation(
        expected_candidate_sha256=before_hash,
        start=0,
        end=min(40, len(original)),
        question="这个选择是否真正改变了下一步条件？",
    )
    assert consult["turn_number"] == 1

    start = original.index("守门人")
    end = start + len("守门人横刀拒绝")
    review = write_review(
        root,
        review_task.template_file,
        decision="repair",
        span_actions=[
            {
                "start": start,
                "end": end,
                "text": original[start:end],
                "action": "expand_scene",
                "note": "把拒绝的行动、失败与即时关系后果完整成场。",
            }
        ],
    )
    apply_human_story_review(
        config, chapter_number=1, file_path=review, approved_by="human"
    )
    synthesis = create_repair_synthesis_task(config, chapter_number=1)
    bundle = json.loads((root / synthesis["review_bundle"]).read_text(encoding="utf-8"))
    plan = root / synthesis["plan_file"]
    plan.write_text(
        repair_plan_markdown(bundle, bundle["blocking_finding_ids"]), encoding="utf-8"
    )
    validation = validate_repair_plan(config, chapter_number=1, file_path=plan)
    assert validation["ok"], validation["errors"]

    prepared = service.prepare_manual_repair(expected_candidate_sha256=before_hash)
    assert prepared["candidate_draft"].endswith(".human.md")
    manual = service.manual_repair_state()
    replacement = original + "\n林迟没有让拒绝停在一句话里，他用失去铜符的代价迫使守门人后退半步。\n"
    saved = service.save_manual_repair(
        expected_draft_sha256=before_hash,
        expected_candidate_sha256=manual["candidate_sha256"],
        text=replacement,
    )
    result = service.submit_manual_repair(
        expected_draft_sha256=before_hash,
        expected_candidate_sha256=saved["candidate_sha256"],
    )

    assert result["chapter_number"] == 1
    assert repair_attempt_status(config, chapter_number=1)["used"] == 1
    assert human_story_review_status(config, chapter_number=1)["status"] in {"pending", "stale"}
    assert review_barrier_status(config, chapter_number=1)["status"] == "reviews_pending"
    assert consultation_status(config, chapter_number=1)["sessions"][0]["status"] == "stale"
    submission = json.loads(
        (root / "40_manuscript" / "draft" / "ch001.submission.json").read_text(encoding="utf-8")
    )
    assert submission["agent"] == "human"
    assert submission["candidate_task_type"] == "repair"
