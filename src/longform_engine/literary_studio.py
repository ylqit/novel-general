"""Local literary assessment resources with escaped request-scoped credentials."""
from __future__ import annotations
import html

from longform_engine.resources import resource_path


def literary_page_html(csrf_token: str, csp_nonce: str) -> str:
    page = resource_path("templates", "studio", "literary.html").read_text(encoding="utf-8")
    for marker, name in (("__STUDIO_STYLES__", "studio.css"), ("__LITERARY_STYLES__", "literary.css"),
                         ("__LITERARY_SCRIPT__", "literary.js")):
        page = page.replace(marker, resource_path("templates", "studio", name).read_text(encoding="utf-8"))
    return page.replace("__CSRF__", html.escape(csrf_token, quote=True)).replace("__NONCE__", html.escape(csp_nonce, quote=True))
