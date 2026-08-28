"""Shared loopback web security boundary for local author interfaces."""

from __future__ import annotations

from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
import hmac
import json
import secrets


class LocalWebError(ValueError):
    """Raised when a browser request crosses the loopback security boundary."""


class LoopbackHTTPServer(ThreadingHTTPServer):
    """One-use bootstrap session, CSRF, CSP, and strict loopback binding."""

    daemon_threads = True
    allow_reuse_address = False

    def __init__(
        self,
        *,
        service: Any,
        port: int,
        handler: type[BaseHTTPRequestHandler],
        session_cookie: str,
        csrf_header: str,
        app_label: str,
        form_action: str = "'none'",
    ) -> None:
        if port < 0 or port > 65535:
            raise LocalWebError("port must be between 0 and 65535")
        self.service = service
        self.session_cookie = session_cookie
        self.csrf_header = csrf_header
        self.app_label = app_label
        if form_action not in {"'none'", "'self'"}:
            raise LocalWebError("form_action must be 'none' or 'self'")
        self.form_action = form_action
        self.bootstrap_token = secrets.token_urlsafe(32)
        self.session_token = secrets.token_urlsafe(32)
        self.csrf_token = secrets.token_urlsafe(32)
        self.csp_nonce = secrets.token_urlsafe(24)
        self.bootstrap_used = False
        super().__init__(("127.0.0.1", port), handler)

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    @property
    def bootstrap_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?{urlencode({'token': self.bootstrap_token})}"


class LoopbackRequestHandler(BaseHTTPRequestHandler):
    """Reusable transport boundary; concrete interfaces own exact routes."""

    server: LoopbackHTTPServer
    protocol_version = "HTTP/1.1"
    max_json_bytes = 2 * 1024 * 1024

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _bootstrap(self, query: str) -> None:
        values = parse_qs(query, keep_blank_values=True)
        token = values.get("token") if set(values) == {"token"} else None
        supplied = token[0] if isinstance(token, list) and len(token) == 1 else ""
        if self.server.bootstrap_used or not hmac.compare_digest(
            supplied, self.server.bootstrap_token
        ):
            raise LocalWebError("bootstrap token is invalid or already used")
        self.server.bootstrap_used = True
        headers = {
            "Location": "/",
            "Set-Cookie": (
                f"{self.server.session_cookie}={self.server.session_token}; Path=/; HttpOnly; "
                "SameSite=Strict; Max-Age=43200"
            ),
        }
        self._send_bytes(HTTPStatus.SEE_OTHER, b"", "text/plain; charset=utf-8", headers)

    def _safe_url(self) -> Any:
        parsed = urlsplit(self.path)
        decoded = unquote(parsed.path)
        if decoded != parsed.path or ".." in decoded or "\\" in decoded or not decoded.startswith("/"):
            raise LocalWebError("unsafe request path")
        return parsed

    def _require_host(self) -> None:
        allowed = {f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"}
        if self.headers.get("Host", "") not in allowed:
            raise LocalWebError(f"Host is not the {self.server.app_label}")

    def _require_origin(self) -> None:
        allowed = {
            f"http://127.0.0.1:{self.server.port}",
            f"http://localhost:{self.server.port}",
        }
        if self.headers.get("Origin", "") not in allowed:
            raise LocalWebError(f"Origin is not the {self.server.app_label}")

    def _require_session(self) -> None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception as exc:  # pragma: no cover - stdlib parser defensive boundary
            raise LocalWebError("invalid session cookie") from exc
        morsel = cookie.get(self.server.session_cookie)
        supplied = morsel.value if morsel is not None else ""
        if not hmac.compare_digest(supplied, self.server.session_token):
            raise LocalWebError("local web session is missing or invalid")

    def _require_csrf(self) -> None:
        if not hmac.compare_digest(
            self.headers.get(self.server.csrf_header, ""), self.server.csrf_token
        ):
            raise LocalWebError("CSRF token is missing or invalid")

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise LocalWebError("POST bodies must use application/json")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise LocalWebError("Content-Length is invalid") from exc
        if length <= 0 or length > self.max_json_bytes:
            raise LocalWebError("JSON request size is outside the allowed range")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise LocalWebError("request body is not valid UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise LocalWebError("request body must be a JSON object")
        return payload

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        rendered = json.dumps(payload, ensure_ascii=False)
        rendered = rendered.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
        self._send_bytes(status, rendered.encode("utf-8"), "application/json; charset=utf-8")

    def _send_html(self, body: str) -> None:
        self._send_bytes(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8")

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
            "connect-src 'self'; img-src 'self' data:; media-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            f"form-action {self.server.form_action}",
        )
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)


__all__ = ["LocalWebError", "LoopbackHTTPServer", "LoopbackRequestHandler"]
