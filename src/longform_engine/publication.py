"""Non-blocking publication risk reporting and manuscript export."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.storage import atomic_write_text, resolve_project_root


@dataclass(frozen=True)
class PublicationRiskReportResult:
    report_file: str
    markdown_file: str
    warning_count: int
    blocking: bool


@dataclass(frozen=True)
class PublicationExportResult:
    bundle_file: str
    report_file: str
    chapter_count: int
    blocking: bool


def publication_risk_report(config: ConfigDocument) -> PublicationRiskReportResult:
    root = resolve_project_root(config)
    creation_mode = str(config.data.get("creation", {}).get("mode") or "original")
    fanfiction = config.data.get("fanfiction", {}) if isinstance(config.data.get("fanfiction"), dict) else {}
    sources = [
        publication_source_record(source)
        for source in fanfiction.get("sources") or []
        if isinstance(source, dict)
    ]
    warnings: list[dict[str, Any]] = []
    if creation_mode == "fanfiction":
        for source in sources:
            if source["rights_status"] == "unverified":
                warnings.append(
                    risk_warning(
                        "unverified_rights",
                        source["source_id"],
                        "Rights status is a user declaration and has not been independently verified.",
                    )
                )
            if source["commercial_intent"]:
                warnings.append(
                    risk_warning(
                        "commercial_fanfiction",
                        source["source_id"],
                        "Commercial intent may require additional rights and platform-policy review.",
                    )
                )
            if not source["platform_policy_url"]:
                warnings.append(
                    risk_warning(
                        "platform_policy_not_recorded",
                        source["source_id"],
                        "No target-platform fanfiction policy URL is recorded.",
                    )
                )
        warnings.append(
            risk_warning(
                "source_confusion",
                "",
                "Do not describe the work as official, authorized, or creator-participated unless the user supplies that claim.",
            )
        )
    warnings.append(
        risk_warning(
            "ai_assistance_label",
            "",
            "Before public release, review the target platform's AI-generated-content labeling function and current rules.",
        )
    )
    payload = {
        "schema": "publication_risk_report_v1",
        "project_slug": str(config.data.get("project", {}).get("slug") or ""),
        "project_title": str(config.data.get("project", {}).get("title") or ""),
        "creation_mode": creation_mode,
        "continuity_mode": str(fanfiction.get("continuity_mode") or ""),
        "sources": sources,
        "rights_status_is_user_claimed": True,
        "engine_performed_legal_verification": False,
        "commercial_intent_blocks_export": False,
        "unverified_rights_blocks_export": False,
        "ai_assisted": True,
        "warnings": warnings,
        "blocking": False,
        "generated_at": utc_now(),
        "references": {
            "china_copyright_law": "https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html",
            "fanfiction_case_context": "https://www.sdcourt.gov.cn/dyzy/372897/372899/44482953/index.html",
            "ai_content_labeling": "https://www.cac.gov.cn/2025-03/14/c_1743654685896173.htm",
        },
    }
    report_dir = root / "80_exports" / "publication_reports"
    report_file = report_dir / "publication_risk_report.json"
    markdown_file = report_dir / "publication_risk_report.md"
    atomic_write_text(report_file, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(markdown_file, render_publication_report(payload))
    append_publication_event(root, "publication_risk_report_generated", report_file)
    return PublicationRiskReportResult(
        report_file=relative(root, report_file),
        markdown_file=relative(root, markdown_file),
        warning_count=len(warnings),
        blocking=False,
    )


def export_publication_bundle(
    config: ConfigDocument,
    *,
    output: str | Path | None = None,
) -> PublicationExportResult:
    root = resolve_project_root(config)
    chapters = sorted(
        (root / "40_manuscript" / "final").glob("ch*.md"),
        key=lambda path: chapter_number(path.name),
    )
    if not chapters:
        raise ValueError("No finalized chapters are available for publication export.")
    if output:
        bundle_file = Path(output)
        if not bundle_file.is_absolute():
            bundle_file = root / bundle_file
    else:
        slug = str(config.data.get("project", {}).get("slug") or "novel")
        bundle_file = root / "80_exports" / "bundles" / f"{slug}.md"
    bundle_file = bundle_file.expanduser().resolve()
    export_root = (root / "80_exports").resolve()
    try:
        bundle_file.relative_to(export_root)
    except ValueError as exc:
        raise ValueError("Publication bundle output must stay under 80_exports/.") from exc
    title = str(config.data.get("project", {}).get("title") or "Untitled")
    body = [f"# {title}", ""]
    for chapter in chapters:
        body.append(chapter.read_text(encoding="utf-8").lstrip("\ufeff").rstrip())
        body.extend(["", ""])
    atomic_write_text(bundle_file, "\n".join(body).rstrip() + "\n")
    report = publication_risk_report(config)
    append_publication_event(root, "publication_bundle_exported", bundle_file)
    return PublicationExportResult(
        bundle_file=relative(root, bundle_file),
        report_file=report.report_file,
        chapter_count=len(chapters),
        blocking=False,
    )


def publication_source_record(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": str(source.get("source_id") or ""),
        "title": str(source.get("title") or ""),
        "creator": str(source.get("creator") or ""),
        "canon_cutoff": str(source.get("canon_cutoff") or ""),
        "allowed_elements": [str(item) for item in source.get("allowed_elements") or []],
        "rights_status": str(source.get("rights_status") or "unverified"),
        "commercial_intent": bool(source.get("commercial_intent")),
        "platform_policy_url": str(source.get("platform_policy_url") or ""),
        "user_claimed": True,
    }


def risk_warning(code: str, source_id: str, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "source_id": source_id,
        "message": message,
        "blocking": False,
    }


def render_publication_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Publication Risk Report",
        "",
        f"- Creation mode: {payload['creation_mode']}",
        f"- Continuity mode: {payload['continuity_mode'] or 'not applicable'}",
        f"- Blocking: {payload['blocking']}",
        "- Rights declarations are user-claimed and were not legally verified by the engine.",
        "- This report is advisory and does not block manuscript export.",
        "",
        "## Sources",
        "",
    ]
    for source in payload["sources"]:
        lines.append(
            f"- {source['source_id']}: {source['title']} / {source['creator']} / "
            f"{source['rights_status']} / commercial={source['commercial_intent']}"
        )
    if not payload["sources"]:
        lines.append("- None")
    lines.extend(["", "## Warnings", ""])
    lines.extend(
        f"- [{item['code']}] {item['message']}"
        for item in payload["warnings"]
    )
    lines.extend(
        [
            "",
            "The export process does not insert copyright, authorization, or AI statements into manuscript prose.",
            "",
        ]
    )
    return "\n".join(lines)


def append_publication_event(root: Path, event: str, artifact: Path) -> None:
    path = root / "70_runtime" / "provenance" / "publication_events.jsonl"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    record = {
        "schema": "publication_provenance_event_v1",
        "event": event,
        "artifact": relative(root, artifact),
        "artifact_sha256": sha256(artifact.read_bytes()).hexdigest(),
        "stores_manuscript_body": False,
        "created_at": utc_now(),
    }
    prefix = existing if not existing or existing.endswith("\n") else existing + "\n"
    atomic_write_text(path, prefix + json.dumps(record, ensure_ascii=False) + "\n")


def chapter_number(name: str) -> int:
    match = re.search(r"ch(\d+)", name)
    return int(match.group(1)) if match else 0


def relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
