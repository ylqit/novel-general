"""Loopback-only human deep-review desk with strict non-canonical web boundaries."""

from __future__ import annotations

from dataclasses import asdict
from difflib import unified_diff
from hashlib import sha256
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
import hmac
import html
import json
import secrets

from longform_engine.agent_tasks import list_manifests, manifest_output, relative_path
from longform_engine.chapter_contract import load_verified_chapter_contract
from longform_engine.config import ConfigDocument
from longform_engine.human_review_consultation import (
    consultation_status,
    create_human_review_consult_task,
    record_human_review_consultation,
    validate_human_review_consultation,
)
from longform_engine.human_story_review import (
    CHECK_FIELDS,
    create_human_story_review_task,
    human_story_review_status,
    validate_human_story_review,
)
from longform_engine.orchestration.pipeline import submit_agent_draft
from longform_engine.quality import compile_effective_quality_contract
from longform_engine.reader_promises import load_reader_promise_ledger
from longform_engine.repair_coordination import (
    create_repair_candidate_task,
    repair_attempt_status,
    review_barrier_status,
)
from longform_engine.storage import acquire_project_lock, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path


MAX_JSON_BYTES = 2 * 1024 * 1024
CHECK_ORDER = (
    "story_contract_preserved",
    "desire_opposition_and_question_clear",
    "scene_causality_and_key_turn_dramatized",
    "protagonist_agency_voice_and_emotion",
    "supporting_cast_and_relationship_logic",
    "reader_gain_and_promise_progress",
    "continuity_world_rules_and_ability_bounds",
    "pacing_information_and_carrier_effective",
    "prose_natural_and_readable",
    "exit_state_and_emotional_aftereffect",
)
CHECK_LABELS = {
    "story_contract_preserved": "故事合同和保护结果",
    "desire_opposition_and_question_clear": "欲望、阻力与戏剧问题",
    "scene_causality_and_key_turn_dramatized": "场景因果和关键转折",
    "protagonist_agency_voice_and_emotion": "人物主体性、声音和情绪归属",
    "supporting_cast_and_relationship_logic": "配角与关系逻辑",
    "reader_gain_and_promise_progress": "读者收益和承诺推进",
    "continuity_world_rules_and_ability_bounds": "连贯性、世界规则和能力边界",
    "pacing_information_and_carrier_effective": "节奏、信息释放和载体重复",
    "prose_natural_and_readable": "文字自然度与可读性",
    "exit_state_and_emotional_aftereffect": "离场状态与情绪余波",
}


class ReviewServerError(ValueError):
    """Raised when a browser action crosses a review-desk safety boundary."""


class ReviewDeskService:
    """Domain boundary used by the HTTP adapter and direct security tests."""

    def __init__(self, config: ConfigDocument, *, chapter_number: int) -> None:
        if chapter_number <= 0:
            raise ReviewServerError("chapter_number must be positive")
        self.config = config
        self.chapter_number = chapter_number
        self.root = resolve_project_root(config)

    def state(self) -> dict[str, Any]:
        chapter = self.chapter_number
        draft = manuscript_chapter_path(self.root, chapter, lane="draft")
        if not draft.is_file():
            raise ReviewServerError("current chapter draft is missing")
        draft_text = draft.read_text(encoding="utf-8")
        draft_hash = _file_hash(draft)
        story_brief = self.root / "50_workbench" / "writing_tasks" / f"ch{chapter:03d}.md"

        contract: dict[str, Any] = {}
        contract_hash = ""
        contract_error = ""
        try:
            contract, contract_hash = load_verified_chapter_contract(self.root, chapter)
        except ValueError as exc:
            contract_error = str(exc)
        try:
            promises = load_reader_promise_ledger(self.root)
        except ValueError as exc:
            promises = {"error": str(exc), "items": []}
        try:
            barrier = review_barrier_status(self.config, chapter_number=chapter)
        except ValueError as exc:
            barrier = {"status": "blocked", "findings": [], "blockers": [str(exc)]}
        try:
            human_status = human_story_review_status(self.config, chapter_number=chapter)
        except ValueError as exc:
            human_status = {"status": "pending", "reason": str(exc)}
        try:
            market = compile_effective_quality_contract(
                self.config,
                chapter_number=chapter,
                compare_markets=("fanqie_free",),
            )
            market_view = {
                "primary_market": market.get("primary_market"),
                "blocking_policy": market.get("blocking_policy"),
                "compatibility_observations": market.get("compatibility_observations") or [],
            }
        except ValueError as exc:
            market_view = {"error": str(exc), "compatibility_observations": []}

        template_path = (
            self.root
            / "50_workbench"
            / "human_story_reviews"
            / f"ch{chapter:03d}.{draft_hash[:12]}.candidate.json"
        )
        review_template = _load_json(template_path, default={})
        manual = self.manual_repair_state()
        candidate_text = str(manual.get("text") or "") if manual.get("candidate_exists") else ""
        diff_text = ""
        if candidate_text:
            diff_text = "".join(
                unified_diff(
                    draft_text.splitlines(keepends=True),
                    candidate_text.splitlines(keepends=True),
                    fromfile=f"draft/ch{chapter:03d}.md",
                    tofile=str(manual.get("candidate_file") or "repair-candidate"),
                )
            )
        consult = consultation_status(self.config, chapter_number=chapter)
        consult["sessions"] = self._consultation_views(consult.get("sessions") or [])
        return {
            "schema": "human_review_desk_state_v1",
            "chapter_number": chapter,
            "draft": {
                "path": relative_path(self.root, draft),
                "sha256": draft_hash,
                "text": draft_text,
            },
            "story_brief": {
                "path": relative_path(self.root, story_brief),
                "text": story_brief.read_text(encoding="utf-8") if story_brief.is_file() else "",
            },
            "chapter_contract": contract,
            "chapter_contract_sha256": contract_hash,
            "chapter_contract_error": contract_error,
            "reader_promises": promises,
            "review_barrier": barrier,
            "human_review_status": human_status,
            "review_template_file": (
                relative_path(self.root, template_path) if template_path.is_file() else ""
            ),
            "review_template": review_template,
            "review_checks": [
                {"id": check_id, "label": CHECK_LABELS[check_id]} for check_id in CHECK_ORDER
            ],
            "market_observations": market_view,
            "consultations": consult,
            "manual_repair": manual,
            "repair_diff": diff_text,
            "canonical_write_allowed": False,
        }

    def prepare_human_review(self, *, expected_candidate_sha256: str) -> dict[str, Any]:
        self._require_current_candidate(expected_candidate_sha256)
        with acquire_project_lock(
            self.config, owner="review-desk", command="review prepare-human-review"
        ):
            result = create_human_story_review_task(
                self.config, chapter_number=self.chapter_number
            )
        return asdict(result)

    def validate_human_review(
        self, *, expected_candidate_sha256: str, review: dict[str, Any]
    ) -> dict[str, Any]:
        self._require_current_candidate(expected_candidate_sha256)
        if not isinstance(review, dict):
            raise ReviewServerError("review must be a JSON object")
        candidate = (
            self.root
            / "50_workbench"
            / "human_story_reviews"
            / f"ch{self.chapter_number:03d}.{expected_candidate_sha256[:12]}.candidate.json"
        )
        if not candidate.is_file():
            raise ReviewServerError("human review task must be prepared before validation")
        if review.get("schema") != "human_story_review_v3":
            raise ReviewServerError("review schema must be human_story_review_v3")
        if set(review.get("checks") or {}) != CHECK_FIELDS:
            raise ReviewServerError("review must contain all ten deep-review checks")
        with acquire_project_lock(
            self.config, owner="review-desk", command="review human-review-validate"
        ):
            atomic_write_text(candidate, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
            result = validate_human_story_review(
                self.config,
                chapter_number=self.chapter_number,
                file_path=candidate,
            )
        return asdict(result)

    def create_consultation(
        self,
        *,
        expected_candidate_sha256: str,
        start: int,
        end: int,
        question: str,
    ) -> dict[str, Any]:
        self._require_current_candidate(expected_candidate_sha256)
        with acquire_project_lock(
            self.config, owner="review-desk", command="review consult-task"
        ):
            result = create_human_review_consult_task(
                self.config,
                chapter_number=self.chapter_number,
                start=start,
                end=end,
                question=question,
            )
        return asdict(result)

    def validate_consultation(self, *, response_file: str) -> dict[str, Any]:
        with acquire_project_lock(
            self.config, owner="review-desk", command="review consult-validate"
        ):
            result = validate_human_review_consultation(
                self.config,
                chapter_number=self.chapter_number,
                file_path=response_file,
            )
        return asdict(result)

    def record_consultation(self, *, response_file: str) -> dict[str, Any]:
        with acquire_project_lock(
            self.config, owner="review-desk", command="review consult-record"
        ):
            result = record_human_review_consultation(
                self.config,
                chapter_number=self.chapter_number,
                file_path=response_file,
            )
        return asdict(result)

    def prepare_manual_repair(self, *, expected_candidate_sha256: str) -> dict[str, Any]:
        self._require_current_candidate(expected_candidate_sha256)
        with acquire_project_lock(
            self.config, owner="review-desk", command="review manual-repair-prepare"
        ):
            result = create_repair_candidate_task(
                self.config, chapter_number=self.chapter_number, agent="human"
            )
        return result

    def save_manual_repair(
        self,
        *,
        expected_draft_sha256: str,
        expected_candidate_sha256: str,
        text: str,
    ) -> dict[str, Any]:
        self._require_current_candidate(expected_draft_sha256)
        target = self._editable_repair_target()
        current_hash = _file_hash(target) if target.is_file() else ""
        if not hmac.compare_digest(current_hash, str(expected_candidate_sha256 or "")):
            raise ReviewServerError("repair candidate changed concurrently; reload before saving")
        normalized = str(text or "").strip()
        if not normalized:
            raise ReviewServerError("manual repair must remain a complete non-empty chapter")
        with acquire_project_lock(
            self.config, owner="review-desk", command="review manual-repair-save"
        ):
            current_hash = _file_hash(target) if target.is_file() else ""
            if not hmac.compare_digest(current_hash, str(expected_candidate_sha256 or "")):
                raise ReviewServerError("repair candidate changed concurrently; reload before saving")
            atomic_write_text(target, normalized + "\n")
        return {
            "candidate_file": relative_path(self.root, target),
            "candidate_sha256": _file_hash(target),
            "canonical_mutated": False,
        }

    def submit_manual_repair(
        self,
        *,
        expected_draft_sha256: str,
        expected_candidate_sha256: str,
    ) -> dict[str, Any]:
        self._require_current_candidate(expected_draft_sha256)
        target = self._editable_repair_target()
        if not target.is_file() or not hmac.compare_digest(
            _file_hash(target), str(expected_candidate_sha256 or "")
        ):
            raise ReviewServerError("repair candidate is missing or changed; save and reload first")
        with acquire_project_lock(
            self.config, owner="review-desk", command="review manual-repair-submit"
        ):
            if not hmac.compare_digest(_file_hash(target), str(expected_candidate_sha256 or "")):
                raise ReviewServerError("repair candidate changed concurrently before submission")
            result = submit_agent_draft(
                self.config,
                chapter_number=self.chapter_number,
                file_path=target,
                agent="human",
                overwrite=True,
            )
        return asdict(result)

    def manual_repair_state(self) -> dict[str, Any]:
        tasks = [
            task
            for task in list_manifests(self.root, chapter_number=self.chapter_number)
            if str(task.get("task_type") or "") == "repair"
            and str(task.get("status") or "")
            in {"awaiting_agent", "submitted", "validated", "invalid"}
        ]
        attempts = repair_attempt_status(self.config, chapter_number=self.chapter_number)
        if len(tasks) != 1:
            return {
                "available": False,
                "reason": (
                    "validated repair candidate task is missing"
                    if not tasks
                    else "multiple active repair candidate tasks are ambiguous"
                ),
                "attempts": attempts,
            }
        task = tasks[0]
        candidate = self.root / str(manifest_output(task).get("path") or "")
        draft = manuscript_chapter_path(self.root, self.chapter_number, lane="draft")
        exists = candidate.is_file()
        return {
            "available": True,
            "editable": str(task.get("status") or "") in {"awaiting_agent", "invalid"},
            "task_id": str(task.get("task_id") or ""),
            "task_status": str(task.get("status") or ""),
            "candidate_file": relative_path(self.root, candidate),
            "candidate_exists": exists,
            "candidate_sha256": _file_hash(candidate) if exists else "",
            "text": (
                candidate.read_text(encoding="utf-8")
                if exists
                else draft.read_text(encoding="utf-8") if draft.is_file() else ""
            ),
            "attempts": attempts,
        }

    def _editable_repair_target(self) -> Path:
        state = self.manual_repair_state()
        if not state.get("available") or not state.get("editable"):
            raise ReviewServerError(
                str(state.get("reason") or "repair candidate is no longer editable")
            )
        target = (self.root / str(state["candidate_file"])).resolve()
        allowed = (self.root / "50_workbench" / "repair_candidates").resolve()
        try:
            target.relative_to(allowed)
        except ValueError as exc:
            raise ReviewServerError("repair task output escaped the controlled candidate lane") from exc
        return target

    def _require_current_candidate(self, expected_hash: str) -> None:
        draft = manuscript_chapter_path(self.root, self.chapter_number, lane="draft")
        current = _file_hash(draft) if draft.is_file() else ""
        if not current or not hmac.compare_digest(current, str(expected_hash or "")):
            raise ReviewServerError("current draft hash changed; reload the review desk")

    def _consultation_views(self, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        base = (
            self.root
            / "50_workbench"
            / "human_story_reviews"
            / "consultations"
            / f"ch{self.chapter_number:03d}"
        ).resolve()
        views: list[dict[str, Any]] = []
        for session in sessions:
            item = dict(session)
            turns: list[dict[str, Any]] = []
            for raw_turn in session.get("turns") or []:
                if not isinstance(raw_turn, dict):
                    continue
                turn = dict(raw_turn)
                response_text = ""
                response_path = str(turn.get("response_file") or "")
                if response_path:
                    resolved = (self.root / response_path).resolve()
                    try:
                        resolved.relative_to(base)
                    except ValueError:
                        resolved = Path()
                    if resolved.is_file():
                        response_text = resolved.read_text(encoding="utf-8")
                turn["response"] = response_text
                turns.append(turn)
            item["turns"] = turns
            views.append(item)
        return views


class ReviewHTTPServer(ThreadingHTTPServer):
    """Threaded loopback HTTP server; domain mutations still serialize on project.lock."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, service: ReviewDeskService, *, port: int) -> None:
        if port < 0 or port > 65535:
            raise ReviewServerError("port must be between 0 and 65535")
        self.service = service
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.session_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.csp_nonce = secrets.token_urlsafe(24)
        self.bootstrap_used = False
        super().__init__(("127.0.0.1", port), ReviewRequestHandler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def bootstrap_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?{urlencode({'token': self.bootstrap_token})}"


class ReviewRequestHandler(BaseHTTPRequestHandler):
    """Exact-route HTTP adapter with Host, Origin, cookie, CSRF, and size checks."""

    server: ReviewHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._require_host()
            parsed = self._safe_url()
            if parsed.path == "/" and parsed.query:
                self._bootstrap(parsed.query)
                return
            self._require_session()
            if parsed.path == "/":
                self._send_html(
                    review_page_html(self.server.csrf_token, csp_nonce=self.server.csp_nonce)
                )
            elif parsed.path == "/api/state":
                self._send_json(HTTPStatus.OK, self.server.service.state())
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
        except ReviewServerError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except ValueError as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_host()
            self._require_origin()
            self._require_session()
            self._require_csrf()
            parsed = self._safe_url()
            body = self._read_json()
            routes = {
                "/api/human-review/prepare": lambda: self.server.service.prepare_human_review(
                    expected_candidate_sha256=str(body.get("expected_candidate_sha256") or "")
                ),
                "/api/human-review/validate": lambda: self.server.service.validate_human_review(
                    expected_candidate_sha256=str(body.get("expected_candidate_sha256") or ""),
                    review=body.get("review"),
                ),
                "/api/consult/task": lambda: self.server.service.create_consultation(
                    expected_candidate_sha256=str(body.get("expected_candidate_sha256") or ""),
                    start=int(body.get("start")),
                    end=int(body.get("end")),
                    question=str(body.get("question") or ""),
                ),
                "/api/consult/validate": lambda: self.server.service.validate_consultation(
                    response_file=str(body.get("response_file") or "")
                ),
                "/api/consult/record": lambda: self.server.service.record_consultation(
                    response_file=str(body.get("response_file") or "")
                ),
                "/api/manual-repair/prepare": lambda: self.server.service.prepare_manual_repair(
                    expected_candidate_sha256=str(body.get("expected_candidate_sha256") or "")
                ),
                "/api/manual-repair/save": lambda: self.server.service.save_manual_repair(
                    expected_draft_sha256=str(body.get("expected_draft_sha256") or ""),
                    expected_candidate_sha256=str(body.get("expected_candidate_sha256") or ""),
                    text=str(body.get("text") or ""),
                ),
                "/api/manual-repair/submit": lambda: self.server.service.submit_manual_repair(
                    expected_draft_sha256=str(body.get("expected_draft_sha256") or ""),
                    expected_candidate_sha256=str(body.get("expected_candidate_sha256") or ""),
                ),
            }
            action = routes.get(parsed.path)
            if action is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "result": action()})
        except ReviewServerError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _bootstrap(self, query: str) -> None:
        values = parse_qs(query, keep_blank_values=True)
        token = values.get("token") if set(values) == {"token"} else None
        supplied = token[0] if isinstance(token, list) and len(token) == 1 else ""
        if self.server.bootstrap_used or not hmac.compare_digest(
            supplied, self.server.bootstrap_token
        ):
            raise ReviewServerError("bootstrap token is invalid or already used")
        self.server.bootstrap_used = True
        headers = {
            "Location": "/",
            "Set-Cookie": (
                f"review_session={self.server.session_token}; Path=/; HttpOnly; "
                "SameSite=Strict; Max-Age=43200"
            ),
        }
        self._send_bytes(HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8", headers)

    def _safe_url(self) -> Any:
        parsed = urlsplit(self.path)
        decoded = unquote(parsed.path)
        if (
            decoded != parsed.path
            or ".." in decoded
            or "\\" in decoded
            or not decoded.startswith("/")
        ):
            raise ReviewServerError("unsafe request path")
        return parsed

    def _require_host(self) -> None:
        allowed = {f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"}
        if self.headers.get("Host", "") not in allowed:
            raise ReviewServerError("Host is not the local review desk")

    def _require_origin(self) -> None:
        allowed = {
            f"http://127.0.0.1:{self.server.port}",
            f"http://localhost:{self.server.port}",
        }
        if self.headers.get("Origin", "") not in allowed:
            raise ReviewServerError("Origin is not the local review desk")

    def _require_session(self) -> None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception as exc:  # pragma: no cover - stdlib parser defensive boundary
            raise ReviewServerError("invalid session cookie") from exc
        morsel = cookie.get("review_session")
        supplied = morsel.value if morsel is not None else ""
        if not hmac.compare_digest(supplied, self.server.session_token):
            raise ReviewServerError("review session is missing or invalid")

    def _require_csrf(self) -> None:
        if not hmac.compare_digest(
            self.headers.get("X-Review-CSRF", ""), self.server.csrf_token
        ):
            raise ReviewServerError("CSRF token is missing or invalid")

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ReviewServerError("POST bodies must use application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ReviewServerError("Content-Length is invalid") from exc
        if length <= 0 or length > MAX_JSON_BYTES:
            raise ReviewServerError("JSON request size is outside the allowed range")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ReviewServerError("request body is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ReviewServerError("request body must be a JSON object")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        rendered = json.dumps(payload, ensure_ascii=False)
        rendered = rendered.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        body = rendered.encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_html(self, body: str) -> None:
        self._send_bytes(
            HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8"
        )

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'self'; script-src 'nonce-{self.server.csp_nonce}'; "
            f"style-src 'nonce-{self.server.csp_nonce}'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'none'",
        )
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)


def review_page_html(csrf_token: str, *, csp_nonce: str = "reviewdesk") -> str:
    """Render a static shell; all project text enters the DOM through textContent/value only."""

    csrf = html.escape(csrf_token, quote=True)
    nonce = html.escape(csp_nonce, quote=True)
    return _REVIEW_PAGE.replace("__CSRF_TOKEN__", csrf).replace("reviewdesk", nonce)


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path, *, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


_REVIEW_PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Longform 人工可视化深审</title>
<style nonce="reviewdesk">
:root{color-scheme:light;--ink:#20231f;--muted:#667064;--paper:#fbfaf5;--line:#d9d7cb;--accent:#8f3b2d;--panel:#fffefa}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 system-ui,"Microsoft YaHei",sans-serif;color:var(--ink);background:var(--paper)}
header{height:52px;padding:12px 18px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;background:#f4f0e7}
#layout{display:grid;grid-template-columns:minmax(240px,26%) minmax(420px,48%) minmax(280px,26%);height:calc(100vh - 52px)}
.col{overflow:auto;padding:14px;border-right:1px solid var(--line)}.col:last-child{border-right:0}section{margin:0 0 16px;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px}
h2,h3{margin:0 0 8px}h2{font-size:16px}h3{font-size:14px}pre{white-space:pre-wrap;word-break:break-word;margin:0;color:#353a34}
textarea{width:100%;min-height:120px;border:1px solid var(--line);border-radius:5px;padding:8px;font:13px/1.6 ui-monospace,"Microsoft YaHei",monospace;background:white}
#manuscript{min-height:48vh}.toolbar{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}button,select,input{border:1px solid #b9b7ad;border-radius:5px;padding:6px 8px;background:white}button{cursor:pointer}button.primary{background:var(--accent);color:white;border-color:var(--accent)}button:disabled{opacity:.45;cursor:not-allowed}
.finding{padding:7px;border-left:3px solid #a75a43;margin:6px 0;background:#faf3ef}.muted{color:var(--muted)}.ok{color:#28653c}.error{color:#9a3027}.check{display:flex;gap:8px;margin:6px 0}.status{white-space:pre-wrap;border-top:1px dashed var(--line);margin-top:8px;padding-top:8px}
@media(max-width:1050px){#layout{grid-template-columns:1fr;height:auto}.col{border-right:0;border-bottom:1px solid var(--line)}#manuscript{min-height:50vh}}
</style></head><body>
<header><strong id="title">人工可视化深审</strong><span id="candidate" class="muted"></span><button id="reload">刷新</button><span id="globalStatus"></span></header>
<div id="layout">
<aside class="col"><section><h2>Story Brief</h2><pre id="brief"></pre></section><section><h2>章节合同</h2><pre id="contract"></pre></section><section><h2>承诺账本</h2><pre id="promises"></pre></section><section><h2>起点主合同 / 番茄 P2 观察</h2><pre id="market"></pre></section></aside>
<main class="col"><section><h2>正文与精确 span</h2><textarea id="manuscript" readonly></textarea><div class="toolbar"><button data-evidence="key_turn">设为关键转折</button><button data-evidence="character_choice_or_emotion">设为人物选择/情绪</button><button data-evidence="reader_gain">设为读者收益</button></div><pre id="evidenceView" class="muted"></pre></section>
<section><h2>修复前后 diff</h2><pre id="diff"></pre></section>
<section><h2>人工完整 repair 候选</h2><div id="repairMeta" class="muted"></div><textarea id="repairText"></textarea><div class="toolbar"><button id="repairPrepare">建立 human repair 工单</button><button id="repairSave">保存完整候选</button><button id="repairSubmit" class="primary">以 human 提交并全量复审</button></div><div id="repairStatus" class="status"></div></section></main>
<aside class="col"><section><h2>独立审稿 finding</h2><div id="findings"></div></section>
<section><h2>十项人工深审</h2><div id="checks"></div><label>决定 <select id="decision"><option>repair</option><option>accept</option><option>redirect</option></select></label><label>redirect 范围 <select id="redirect"><option>direction</option><option>outline_revision</option></select></label><input id="gainNote" placeholder="读者收益说明"><input id="reviewReason" placeholder="决定理由"><div class="toolbar"><button id="reviewPrepare">准备冻结深审表</button><button id="reviewValidate" class="primary">保存并校验（不 apply）</button></div><div id="reviewStatus" class="status"></div></section>
<section><h2>结构化批注</h2><select id="severity"><option>P1</option><option>P0</option><option>P2</option></select><select id="action"><option>rewrite</option><option>expand_scene</option><option>compress</option><option>clarify</option><option>reorder</option><option>replace_carrier</option><option>preserve</option></select><input id="checkId" placeholder="check_id"><input id="intent" placeholder="修改意图"><input id="preserve" placeholder="必须保护项，逗号分隔"><button id="addAnnotation">将当前 span 转为批注</button><pre id="annotationView"></pre></section>
<section><h2>Codex 咨询</h2><textarea id="question" placeholder="围绕当前选中 span 提问"></textarea><div class="toolbar"><button id="consultTask">创建咨询工单</button><button id="consultValidate">校验最新回答</button><button id="consultRecord">记录最新回答</button></div><div id="consultHistory"></div><div id="consultStatus" class="status"></div></section></aside>
</div>
<script nonce="reviewdesk">
const csrf="__CSRF_TOKEN__";let state=null;let selected={start:0,end:0,text:""};let evidence={};let annotations=[];
const $=id=>document.getElementById(id);const show=(id,value,cls="")=>{const el=$(id);el.textContent=typeof value==="string"?value:JSON.stringify(value,null,2);el.className="status "+cls};
async function api(path,body){const r=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-Review-CSRF":csrf},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||"request failed");return data.result}
function capture(){const el=$("manuscript");selected={start:el.selectionStart,end:el.selectionEnd,text:el.value.slice(el.selectionStart,el.selectionEnd)};if(selected.end<=selected.start)throw new Error("请先圈选正文 span");return selected}
async function load(){state=await fetch("/api/state",{credentials:"same-origin"}).then(r=>r.json());$("title").textContent=`ch${String(state.chapter_number).padStart(3,"0")} 人工可视化深审`;$("candidate").textContent=state.draft.sha256;$("brief").textContent=state.story_brief.text;$("contract").textContent=JSON.stringify(state.chapter_contract,null,2);$("promises").textContent=JSON.stringify(state.reader_promises,null,2);$("market").textContent=JSON.stringify(state.market_observations,null,2);$("manuscript").value=state.draft.text;$("diff").textContent=state.repair_diff||"暂无 repair diff";
$("findings").replaceChildren(...(state.review_barrier.findings||[]).map(f=>{const d=document.createElement("div");d.className="finding";d.textContent=`[${f.severity}] ${f.code||f.finding_id}: ${f.diagnosis||""}`;return d}));
$("checks").replaceChildren(...state.review_checks.map(c=>{const l=document.createElement("label");l.className="check";const i=document.createElement("input");i.type="checkbox";i.dataset.check=c.id;l.append(i,document.createTextNode(c.label));return l}));
const t=state.review_template||{};evidence=Object.fromEntries((t.evidence_spans||[]).map(x=>[x.kind,x]));annotations=t.annotations||[];renderEvidence();renderAnnotations();renderRepair();renderConsult();show("globalStatus",`屏障：${state.review_barrier.status}`)}
function renderEvidence(){$("evidenceView").textContent=JSON.stringify(evidence,null,2)}function renderAnnotations(){$("annotationView").textContent=JSON.stringify(annotations,null,2)}
function renderRepair(){const r=state.manual_repair||{};$("repairMeta").textContent=r.available?`${r.task_id} / ${r.task_status} / 剩余 ${r.attempts.remaining}`:r.reason||"无 repair 工单";$("repairText").value=r.text||state.draft.text;$("repairSave").disabled=!r.available||!r.editable;$("repairSubmit").disabled=!r.available||!r.editable;$("repairPrepare").disabled=!!r.available}
function latestTurn(){for(const s of state.consultations.sessions||[])for(let i=(s.turns||[]).length-1;i>=0;i--)return s.turns[i];return null}
function renderConsult(){const rows=[];for(const s of state.consultations.sessions||[])for(const t of s.turns||[])rows.push(`${s.status} t${t.turn_number}: ${t.response||t.response_file}`);$("consultHistory").textContent=rows.join("\n\n")||"暂无咨询"}
document.querySelectorAll("[data-evidence]").forEach(b=>b.onclick=()=>{try{const s=capture();evidence[b.dataset.evidence]={kind:b.dataset.evidence,...s};renderEvidence()}catch(e){show("globalStatus",e.message,"error")}});
$("addAnnotation").onclick=()=>{try{const s=capture();annotations.push({annotation_id:`HR-${Date.now()}`,start:s.start,end:s.end,text:s.text,check_id:$("checkId").value,severity:$("severity").value,action:$("action").value,intent:$("intent").value,must_preserve:$("preserve").value.split(",").map(x=>x.trim()).filter(Boolean),note:"由人工在审稿台明确转换"});renderAnnotations()}catch(e){show("reviewStatus",e.message,"error")}};
$("reviewPrepare").onclick=async()=>{try{show("reviewStatus",await api("/api/human-review/prepare",{expected_candidate_sha256:state.draft.sha256}),"ok");await load()}catch(e){show("reviewStatus",e.message,"error")}};
$("reviewValidate").onclick=async()=>{try{const base=state.review_template;if(!base.schema)throw new Error("请先准备深审表");const checks={};document.querySelectorAll("#checks input").forEach(i=>checks[i.dataset.check]={passed:i.checked,reason:i.checked?"人工逐项确认通过":"人工未确认通过"});const review={...base,checks,decision:$("decision").value,evidence_spans:Object.values(evidence),reader_gain_note:$("gainNote").value,annotations,redirect_scope:$("redirect").value,reason:$("reviewReason").value};show("reviewStatus",await api("/api/human-review/validate",{expected_candidate_sha256:state.draft.sha256,review}),"ok");await load()}catch(e){show("reviewStatus",e.message,"error")}};
$("consultTask").onclick=async()=>{try{const s=capture();show("consultStatus",await api("/api/consult/task",{expected_candidate_sha256:state.draft.sha256,start:s.start,end:s.end,question:$("question").value}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("consultValidate").onclick=async()=>{try{const t=latestTurn();if(!t)throw new Error("暂无咨询工单");show("consultStatus",await api("/api/consult/validate",{response_file:t.response_file}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("consultRecord").onclick=async()=>{try{const t=latestTurn();if(!t)throw new Error("暂无咨询工单");show("consultStatus",await api("/api/consult/record",{response_file:t.response_file}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("repairPrepare").onclick=async()=>{try{show("repairStatus",await api("/api/manual-repair/prepare",{expected_candidate_sha256:state.draft.sha256}),"ok");await load()}catch(e){show("repairStatus",e.message,"error")}};
$("repairSave").onclick=async()=>{try{show("repairStatus",await api("/api/manual-repair/save",{expected_draft_sha256:state.draft.sha256,expected_candidate_sha256:state.manual_repair.candidate_sha256||"",text:$("repairText").value}),"ok");await load()}catch(e){show("repairStatus",e.message,"error")}};
$("repairSubmit").onclick=async()=>{try{show("repairStatus",await api("/api/manual-repair/submit",{expected_draft_sha256:state.draft.sha256,expected_candidate_sha256:state.manual_repair.candidate_sha256}),"ok");await load()}catch(e){show("repairStatus",e.message,"error")}};
$("reload").onclick=()=>load().catch(e=>show("globalStatus",e.message,"error"));load().catch(e=>show("globalStatus",e.message,"error"));
</script></body></html>'''


__all__ = [
    "ReviewDeskService",
    "ReviewHTTPServer",
    "ReviewServerError",
    "review_page_html",
]
