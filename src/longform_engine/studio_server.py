"""Loopback-only Chinese novel creation and source-processing console."""

from __future__ import annotations

from hashlib import sha256
from http import HTTPStatus
from pathlib import Path
from typing import Any, BinaryIO, cast
import html
import json
import os
import re
import secrets
import shutil
import tempfile

import yaml

from longform_engine import fanfiction_contracts
from longform_engine.config import ConfigDocument
from longform_engine.fanfiction_sources import (
    FanfictionSourceError,
    apply_source_ingest_batch,
    confirm_source_ingest_groups,
    coverage_gaps,
    create_source_ingest_plan,
    create_source_processing_job,
    initialize_source_library,
    register_source_work,
    run_source_processing_job,
    source_evidence_preview,
    source_item_status,
    source_library_catalog,
    source_library_root,
    source_library_status,
    source_processing_capabilities,
)
from longform_engine.local_web import LocalWebError, LoopbackHTTPServer, LoopbackRequestHandler
from longform_engine.fanfiction_context import event_disposition_status, fanfiction_context_status
from longform_engine.intelligence import fanfiction_status
from longform_engine.publication import publication_preflight_status
from longform_engine.semantic_protocols import build_semantic_document
from longform_engine.storage import atomic_write_text, resolve_project_root


MAX_BROWSER_UPLOAD_FILE_BYTES = 4 * 1024 * 1024 * 1024
MAX_BROWSER_UPLOAD_SESSION_BYTES = 16 * 1024 * 1024 * 1024
MAX_BROWSER_UPLOAD_FILES = 500
MAX_MULTIPART_HEADER_BYTES = 64 * 1024
MAX_MULTIPART_OVERHEAD_BYTES = 256 * 1024


class StudioServerError(LocalWebError):
    """Raised when a console action violates its project or upload boundary."""


class StudioService:
    """Project/source-library boundary for the Chinese creation console."""

    def __init__(self, config: ConfigDocument) -> None:
        self.config = config
        self.root = resolve_project_root(config)

    def state(self) -> dict[str, Any]:
        try:
            library = source_library_status()
        except FanfictionSourceError as exc:
            library = {"exists": True, "compatible": False, "error": str(exc)}
        try:
            catalog = source_library_catalog() if library["exists"] else {
                "works": [], "items": [], "batches": []
            }
        except FanfictionSourceError as exc:
            catalog = {"works": [], "items": [], "batches": [], "error": str(exc)}
        creation_mode = str(self.config.data.get("creation", {}).get("mode") or "original")
        coverage: dict[str, Any] = {"complete": creation_mode != "fanfiction", "works": [], "gaps": []}
        if creation_mode == "fanfiction":
            try:
                coverage = coverage_gaps(self.config)
            except (FanfictionSourceError, OSError, KeyError) as exc:
                coverage = {"complete": False, "works": [], "gaps": [str(exc)]}
        goal_path = self.root / "50_workbench" / "创作目标.yaml"
        fanfiction_workflow: dict[str, Any] = {}
        if creation_mode == "fanfiction":
            novel_state = _read_json(self.root / "30_state" / "novel_state.json")
            current_chapter = int((novel_state or {}).get("last_closed_chapter") or 0) + 1
            fanfiction_workflow = {
                "status": fanfiction_status(self.config),
                "event_dispositions": event_disposition_status(self.config),
                "current_chapter_context": fanfiction_context_status(
                    self.config, chapter_number=current_chapter
                ),
            }
        publication = {
            "targets": {
                target: publication_preflight_status(self.config, target=target)
                for target in ("qidian_male", "fanqie_free")
            },
            "boundary": (
                "权利决定只确认人工风险知情和流程责任，不是法律意见、授权或平台接收保证；"
                "普通创作不受门禁，只有具体平台发布包导出受当前决定约束。"
            ),
            "automatic_upload": False,
        }
        return {
            "schema": "novel_creation_studio_state_v1",
            "project": {
                "title": self.config.data.get("project", {}).get("title"),
                "slug": self.config.data.get("project", {}).get("slug"),
                "creation_mode": creation_mode,
                "target_platform": self.config.data.get("novel", {}).get("target_platform"),
            },
            "creation_goal": goal_path.read_text(encoding="utf-8") if goal_path.is_file() else "",
            "source_library": library,
            "catalog": catalog,
            "capabilities": source_processing_capabilities(),
            "coverage": coverage,
            "semantic_documents": self._semantic_document_index(),
            "fanfiction_workflow": fanfiction_workflow,
            "publication": publication,
            "crossover_contract": {
                "topologies": [
                    topology
                    for topology in (
                        "fixed_host",
                        "fusion_world",
                        "sequential_worlds",
                    )
                    if topology in fanfiction_contracts.CROSSOVER_TOPOLOGIES
                ],
                "default_host_source_id": {
                    "fixed_host": "configured source_id",
                    "fusion_world": None,
                    "sequential_worlds": None,
                },
                "transfer_fields": ["source_id", "payload_kinds", "volume_ids"],
                "adapter_fields": [
                    "source_id",
                    "payload_kinds",
                    "host_source_id",
                    "volume_ids",
                ],
                "payload_kinds": sorted(fanfiction_contracts.CROSSOVER_PAYLOAD_KINDS),
                "adapter_coverage": "actual source-volume-host interactions only",
                "topology_rules": {
                    "fixed_host": (
                        "non-host transfers into default_host_source_id; no host self-transfer"
                    ),
                    "fusion_world": (
                        "at least two distinct transfers.source_id participants"
                    ),
                    "sequential_worlds": (
                        "extensions.crossover.volume_ids; exactly one 卷宿主世界 host and at "
                        "least one actual source-volume-host interaction per declared volume; "
                        "no Cartesian coverage"
                    ),
                },
                "always_required_topics": sorted(
                    fanfiction_contracts.CROSSOVER_ALWAYS_REQUIRED_TOPICS
                ),
                "topology_claims": {
                    "fusion_world": "世界规则优先级",
                    "sequential_worlds": "卷宿主世界(volume_ids, host_source_id)",
                },
            },
            "panels": [
                "创建小说", "创作目标", "创作沙盒", "语义文档", "原著资料库", "批量导入",
                "资料处理", "证据审查", "项目绑定", "动态覆盖", "Canon审批", "同人故事发动机",
                "原著时间与知识范围", "原著事件命运", "原著人物职责", "同人路线复核",
                "跨界宪法", "主世界适配器", "当前卷同人设计", "当前章同人上下文诊断",
                "全书与分卷", "情节节点", "滚动章节", "章节审阅", "原著一致性与同人创造性双轴审查",
                "读者反馈", "影响与回溯", "平台发布前确认",
            ],
            "boundaries": {
                "browser_absolute_path_reading": False,
                "chapter_finalize": False,
                "canon_apply": False,
                "cloud_automatic_fallback": False,
                "full_source_in_project": False,
            },
            "safe_commands": {
                "chapter_review": "longform-engine review serve project.yaml --chapter N",
                "canon_task": "longform-engine fanfiction canon-task project.yaml",
                "story_engine": "longform-engine fanfiction story-engine-task project.yaml",
                "route_design": "longform-engine fanfiction design-task project.yaml",
                "route_review": "longform-engine fanfiction design-review-task project.yaml --file ROUTE",
                "event_dispositions": "longform-engine fanfiction event-disposition-status project.yaml --json",
                "chapter_context": "longform-engine fanfiction context-status project.yaml --chapter N --json",
                "coverage": "longform-engine fanfiction coverage-gaps project.yaml --json",
                "sandbox": "longform-engine sandbox create project.yaml --type 场景试写 --title 标题 --body-file 内容.md",
                "migration": "longform-engine migrate audit-v011 --source OLD_PATH --json",
                "publication_rights_decision": (
                    "longform-engine publication rights-decision project.yaml --target "
                    "qidian_male|fanqie_free --decision proceed|hold --approved-by HUMAN --note NOTE"
                ),
            },
        }

    def save_semantic_candidate(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"document_type", "title", "continuity", "body", "scope_kind"}
        if set(payload) != required:
            raise StudioServerError("语义候选字段不完整或包含未知字段")
        if any(not str(payload[field]).strip() for field in required):
            raise StudioServerError("语义候选的类型、标题、作用域、连续性和正文不能为空")
        digest = sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        document = build_semantic_document(
            document_id=f"sem_studio_{digest[:20]}",
            document_type=str(payload["document_type"]).strip(),
            title=str(payload["title"]).strip(),
            scope={"kind": str(payload["scope_kind"]).strip(), "project": self.root.name},
            continuity=str(payload["continuity"]).strip(),
            body=str(payload["body"]),
            extensions={
                "studio_candidate": True,
                "canonical": False,
                "requires_independent_review": True,
            },
            created_by="human",
        )
        path = self.root / "50_workbench" / "语义候选" / f"{document['artifact']['artifact_id']}.json"
        atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        return {
            "schema": "semantic_document_v1",
            "file": path.relative_to(self.root).as_posix(),
            "state": "candidate",
            "canonical_mutated": False,
        }

    def _semantic_document_index(self) -> list[dict[str, Any]]:
        roots = (
            self.root / "10_bible" / "semantic",
            self.root / "20_outline" / "semantic",
            self.root / "50_workbench" / "语义候选",
            self.root / "50_workbench" / "创作沙盒",
        )
        values: list[dict[str, Any]] = []
        for directory in roots:
            for path in sorted(directory.glob("**/*.json")) if directory.is_dir() else ():
                document = _read_json(path)
                if not isinstance(document, dict) or document.get("schema") != "semantic_document_v1":
                    continue
                raw_artifact = document.get("artifact")
                artifact: dict[str, Any] = raw_artifact if isinstance(raw_artifact, dict) else {}
                values.append(
                    {
                        "id": artifact.get("artifact_id"),
                        "type": document.get("document_type"),
                        "title": document.get("title"),
                        "state": artifact.get("state"),
                        "file": path.relative_to(self.root).as_posix(),
                    }
                )
        return values

    def save_creation_goal(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {
            "purpose", "work_type", "format", "target_platform", "update_capacity",
            "validation_period", "commercial_intent", "fanfiction_rights_risk_confirmed",
        }
        if set(payload) != required:
            raise StudioServerError("创作目标字段不完整或包含未知字段")
        if payload["work_type"] not in {
            "原创", "灵感原创", "改编研究", "同人", "跨作品同人"
        }:
            raise StudioServerError("创作类型无效")
        for field in ("commercial_intent", "fanfiction_rights_risk_confirmed"):
            if not isinstance(payload[field], bool):
                raise StudioServerError(f"{field} 必须为布尔值")
        document = {
            "协议版本": "creation_goal_profile_v1",
            "创作目的": str(payload["purpose"]).strip(),
            "创作类型": payload["work_type"],
            "篇幅形式": str(payload["format"]).strip(),
            "目标平台": str(payload["target_platform"]).strip(),
            "更新能力": str(payload["update_capacity"]).strip(),
            "计划验证周期": str(payload["validation_period"]).strip(),
            "是否商业化": payload["commercial_intent"],
            "同人权利风险已确认": payload["fanfiction_rights_risk_confirmed"],
            "说明": "创作目标用于规划，不是 Canon、收入承诺或平台通过保证。",
        }
        path = self.root / "50_workbench" / "创作目标.yaml"
        atomic_write_text(path, yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
        return {"schema": "creation_goal_profile_v1", "file": path.relative_to(self.root).as_posix()}

    def save_market_claim(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = {"claim", "source", "observed_on", "category"}
        if set(payload) != required or any(not str(payload[field]).strip() for field in required):
            raise StudioServerError("市场研究记录必须包含主张、来源、观察日期和分类")
        claim = {
            "schema": "market_research_claim_v1",
            "claim": str(payload["claim"]).strip(),
            "source": str(payload["source"]).strip(),
            "observed_on": str(payload["observed_on"]).strip(),
            "category": str(payload["category"]).strip(),
            "advisory_only": True,
            "canon": False,
            "quality_gate": False,
        }
        digest = sha256(
            json.dumps(claim, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        path = self.root / "50_workbench" / "市场研究" / f"{digest}.json"
        atomic_write_text(path, json.dumps(claim, ensure_ascii=False, indent=2) + "\n")
        return {**claim, "file": path.relative_to(self.root).as_posix()}

    def register_work(self, payload: dict[str, Any]) -> dict[str, Any]:
        return register_source_work(
            name=str(payload.get("name") or ""),
            creator=str(payload.get("creator") or ""),
            aliases=cast(list[str], payload.get("aliases") or []),
            versions=cast(list[str], payload.get("versions") or []),
            approved_by=str(payload.get("approved_by") or ""),
        )

    def start_upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        initialize_source_library()
        required = {
            "work_id", "source_type", "version", "unit_range", "source_method",
            "rights_status", "retention_mode", "storage_mode",
        }
        if set(payload) != required:
            raise StudioServerError("浏览器导入批次字段无效")
        if payload["storage_mode"] != "managed_copy":
            raise StudioServerError("浏览器上传只能使用 managed_copy；大型外部引用请使用 CLI")
        session_id = "browser_" + secrets.token_hex(10)
        session_dir = source_library_root() / "暂存区" / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        record = {
            "schema": "source_browser_upload_session_v1",
            "session_id": session_id,
            "status": "uploading",
            "metadata": payload,
            "files": [],
            "total_bytes": 0,
        }
        self._write_upload_record(session_dir, record)
        return {"session_id": session_id, "status": "uploading"}

    def receive_upload(
        self,
        *,
        session_id: str,
        relative_path: str,
        length: int,
        stream: BinaryIO,
    ) -> dict[str, Any]:
        if length <= 0 or length > MAX_BROWSER_UPLOAD_FILE_BYTES:
            raise StudioServerError("上传文件大小超出允许范围")
        session_dir, record = self._upload_session(session_id)
        if record.get("status") != "uploading":
            raise StudioServerError("上传会话当前不可接收文件")
        safe_relative = _safe_browser_relative_path(relative_path)
        raw_files = record.get("files")
        files: list[dict[str, Any]] = (
            [item for item in raw_files if isinstance(item, dict)]
            if isinstance(raw_files, list)
            else []
        )
        if len(files) >= MAX_BROWSER_UPLOAD_FILES:
            raise StudioServerError("浏览器上传会话文件数超限")
        if int(record.get("total_bytes") or 0) + length > MAX_BROWSER_UPLOAD_SESSION_BYTES:
            raise StudioServerError("浏览器上传会话总大小超限")
        target = session_dir / "文件" / safe_relative
        resolved = target.resolve()
        try:
            resolved.relative_to((session_dir / "文件").resolve())
        except ValueError as exc:
            raise StudioServerError("上传路径逃逸暂存区") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise StudioServerError("同一上传会话不能重复提交相同相对路径")
        free_bytes = shutil.disk_usage(session_dir).free
        if free_bytes < length + 64 * 1024 * 1024:
            raise StudioServerError("资料库磁盘剩余空间不足，上传尚未写入正式索引")
        partial = target.with_name(target.name + ".partial")
        remaining = length
        with partial.open("wb") as handle:
            while remaining:
                chunk = stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise StudioServerError("浏览器连接在文件上传完成前中断")
                handle.write(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        partial.replace(target)
        files.append({"relative_path": safe_relative, "size_bytes": length})
        record["files"] = files
        record["total_bytes"] = int(record.get("total_bytes") or 0) + length
        self._write_upload_record(session_dir, record)
        return {"relative_path": safe_relative, "size_bytes": length, "file_count": len(files)}

    def finalize_upload(self, session_id: str) -> dict[str, Any]:
        session_dir, record = self._upload_session(session_id)
        if record.get("status") != "uploading" or not record.get("files"):
            raise StudioServerError("上传会话没有可生成预览的文件")
        metadata = record["metadata"]
        plan = create_source_ingest_plan(
            work_id=str(metadata["work_id"]),
            directory=session_dir / "文件",
            source_type=str(metadata["source_type"]),
            version=str(metadata["version"]),
            unit_range=str(metadata["unit_range"]),
            source_method=str(metadata["source_method"]),
            rights_status=str(metadata["rights_status"]),
            retention_mode=str(metadata["retention_mode"]),
            storage_mode="managed_copy",
        )
        record["status"] = "planned"
        record["ingest_batch_id"] = plan["batch_id"]
        self._write_upload_record(session_dir, record)
        return plan

    def confirm_ingest_groups(self, payload: dict[str, Any]) -> dict[str, Any]:
        groups = payload.get("groups")
        if not isinstance(groups, list) or any(not isinstance(item, dict) for item in groups):
            raise StudioServerError("资料分组必须是对象列表")
        return confirm_source_ingest_groups(
            batch_id=str(payload.get("batch_id") or ""),
            groups=cast(list[dict[str, Any]], groups),
            approved_by=str(payload.get("approved_by") or ""),
        )

    def apply_ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        return apply_source_ingest_batch(
            batch_id=str(payload.get("batch_id") or ""),
            approved_by=str(payload.get("approved_by") or ""),
        )

    def cancel_upload(self, session_id: str) -> dict[str, Any]:
        session_dir, record = self._upload_session(session_id)
        if record.get("status") in {"planned", "cancelled"}:
            raise StudioServerError("已生成导入计划或已取消的会话不能再次取消")
        files_dir = (session_dir / "文件").resolve()
        files_dir.relative_to((source_library_root() / "暂存区").resolve())
        if files_dir.is_dir():
            shutil.rmtree(files_dir)
        record["status"] = "cancelled"
        record["cancelled_files"] = len(record.get("files") or [])
        record["files"] = []
        record["total_bytes"] = 0
        self._write_upload_record(session_dir, record)
        return {
            "session_id": session_id,
            "status": "cancelled",
            "temporary_bytes_removed": True,
            "formal_index_changed": False,
        }

    def item_status(self, item_id: str) -> dict[str, Any]:
        return source_item_status(item_id)

    def evidence_preview(self, payload: dict[str, Any]) -> dict[str, Any]:
        return source_evidence_preview(
            str(payload.get("item_id") or ""),
            offset=int(payload.get("offset") or 0),
            limit=int(payload.get("limit") or 40),
        )

    def plan_processing(self, payload: dict[str, Any]) -> dict[str, Any]:
        return create_source_processing_job(
            item_id=str(payload.get("item_id") or ""),
            asset_ids=cast(list[str], payload.get("asset_ids") or []),
            execution=str(payload.get("execution") or "local"),
            processor_id=str(payload.get("processor_id") or "auto"),
            parameters=cast(dict[str, Any], payload.get("parameters") or {}),
        )

    def run_processing(self, payload: dict[str, Any]) -> dict[str, Any]:
        return run_source_processing_job(
            item_id=str(payload.get("item_id") or ""), job_id=str(payload.get("job_id") or "")
        )

    def _upload_session(self, session_id: str) -> tuple[Path, dict[str, Any]]:
        if not session_id.startswith("browser_") or not session_id[8:].isalnum():
            raise StudioServerError("上传会话 ID 无效")
        directory = source_library_root() / "暂存区" / session_id
        record = _read_json(directory / "上传会话.json")
        if not isinstance(record, dict) or record.get("session_id") != session_id:
            raise StudioServerError("上传会话不存在")
        return directory, record

    @staticmethod
    def _write_upload_record(directory: Path, record: dict[str, Any]) -> None:
        atomic_write_text(
            directory / "上传会话.json",
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
        )


class StudioHTTPServer(LoopbackHTTPServer):
    def __init__(self, service: StudioService, *, port: int) -> None:
        super().__init__(
            service=service,
            port=port,
            handler=StudioRequestHandler,
            session_cookie="studio_session",
            csrf_header="X-Studio-CSRF",
            app_label="local creation studio",
            form_action="'self'",
        )


class StudioRequestHandler(LoopbackRequestHandler):
    server: StudioHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._require_host()
            parsed = self._safe_url()
            if parsed.path == "/" and parsed.query:
                self._bootstrap(parsed.query)
                return
            self._require_session()
            if parsed.path == "/":
                self._send_html(studio_page_html(self.server.csrf_token, self.server.csp_nonce))
            elif parsed.path == "/api/state":
                self._send_json(HTTPStatus.OK, self.server.service.state())
            elif parsed.path == "/api/evidence/preview":
                from urllib.parse import parse_qs

                values = parse_qs(parsed.query, keep_blank_values=True)
                if not values.get("item_id"):
                    raise StudioServerError("证据预览需要 item_id")
                self._send_json(
                    HTTPStatus.OK,
                    self.server.service.evidence_preview(
                        {
                            "item_id": values["item_id"][0],
                            "offset": (values.get("offset") or ["0"])[0],
                            "limit": (values.get("limit") or ["40"])[0],
                        }
                    ),
                )
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
        except LocalWebError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except (FanfictionSourceError, OSError, KeyError, ValueError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_host()
            self._require_origin()
            self._require_session()
            self._require_csrf()
            parsed = self._safe_url()
            if parsed.path == "/api/upload/file":
                self._upload_file(parsed.query)
                return
            body = self._read_json()
            routes = {
                "/api/creation-goal/save": lambda: self.server.service.save_creation_goal(body),
                "/api/market-claim/save": lambda: self.server.service.save_market_claim(body),
                "/api/semantic/save": lambda: self.server.service.save_semantic_candidate(body),
                "/api/work/register": lambda: self.server.service.register_work(body),
                "/api/upload/start": lambda: self.server.service.start_upload(body),
                "/api/upload/finalize": lambda: self.server.service.finalize_upload(
                    str(body.get("session_id") or "")
                ),
                "/api/upload/cancel": lambda: self.server.service.cancel_upload(
                    str(body.get("session_id") or "")
                ),
                "/api/ingest/groups-confirm": lambda: self.server.service.confirm_ingest_groups(body),
                "/api/ingest/apply": lambda: self.server.service.apply_ingest(body),
                "/api/item/status": lambda: self.server.service.item_status(
                    str(body.get("item_id") or "")
                ),
                "/api/process/plan": lambda: self.server.service.plan_processing(body),
                "/api/process/run": lambda: self.server.service.run_processing(body),
                "/api/evidence/preview": lambda: self.server.service.evidence_preview(body),
            }
            action = routes.get(parsed.path)
            if action is None:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "result": action()})
        except LocalWebError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except (FanfictionSourceError, OSError, KeyError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

    def _upload_file(self, query: str) -> None:
        from urllib.parse import parse_qs

        values = parse_qs(query, keep_blank_values=True)
        if set(values) != {"session", "path"}:
            raise StudioServerError("上传文件参数无效")
        session_id = values["session"][0]
        relative_path = values["path"][0]
        self.server.service._upload_session(session_id)
        _safe_browser_relative_path(relative_path)
        boundary = _multipart_boundary(self.headers.get("Content-Type", ""))
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise StudioServerError("上传文件长度无效") from exc
        if length <= 0 or length > MAX_BROWSER_UPLOAD_FILE_BYTES + MAX_MULTIPART_OVERHEAD_BYTES:
            raise StudioServerError("multipart 上传请求大小超出允许范围")
        if shutil.disk_usage(source_library_root()).free < length + 64 * 1024 * 1024:
            raise StudioServerError("资料库磁盘剩余空间不足，multipart 请求尚未写入暂存区")
        with tempfile.TemporaryFile(mode="w+b") as spool:
            remaining = length
            while remaining:
                chunk = self.rfile.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise StudioServerError("浏览器连接在 multipart 上传完成前中断")
                spool.write(chunk)
                remaining -= len(chunk)
            multipart_stream = cast(BinaryIO, spool)
            file_start, file_length = _multipart_file_bounds(
                multipart_stream, length=length, boundary=boundary
            )
            spool.seek(file_start)
            result = self.server.service.receive_upload(
                session_id=session_id,
                relative_path=relative_path,
                length=file_length,
                stream=multipart_stream,
            )
        self._send_json(HTTPStatus.OK, {"ok": True, "result": result})


def studio_page_html(csrf_token: str, csp_nonce: str) -> str:
    page = (
        _STUDIO_PAGE.replace("__CSRF_TOKEN__", html.escape(csrf_token, quote=True))
        .replace("studiononce", html.escape(csp_nonce, quote=True))
    )
    legacy_transport = (
        'const r=await fetch(`/api/upload/file?session=${encodeURIComponent(session.session_id)}'
        '&path=${encodeURIComponent(path)}`,{method:"POST",credentials:"same-origin",headers:'
        '{"Content-Type":"application/octet-stream","X-Studio-CSRF":csrf},body:f});'
    )
    multipart_transport = (
        'const uploadBody=new FormData();uploadBody.append("file",f,f.name);'
        'const r=await fetch(`/api/upload/file?session=${encodeURIComponent(session.session_id)}'
        '&path=${encodeURIComponent(path)}`,{method:"POST",credentials:"same-origin",headers:'
        '{"X-Studio-CSRF":csrf},body:uploadBody});'
    )
    if page.count(legacy_transport) != 1:
        raise StudioServerError("创作控制台上传模板与 multipart 安全边界不一致")
    page = page.replace(legacy_transport, multipart_transport, 1)
    publication_projection = 'show("crossoverContract",state.crossover_contract);'
    publication_projection += (
        'show("publicationStatus",state.publication);'
        'show("rightsDecisionCommand",state.safe_commands.publication_rights_decision);'
    )
    projection_anchor = 'show("crossoverContract",state.crossover_contract);'
    if page.count(projection_anchor) != 1:
        raise StudioServerError("创作控制台发布状态投影边界不一致")
    return page.replace(projection_anchor, publication_projection, 1)


def _safe_browser_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    parts = normalized.split("/") if normalized else []
    reserved = {"con", "prn", "aux", "nul", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))}
    if (
        not parts
        or normalized.startswith("/")
        or Path(normalized).is_absolute()
        or any(part in {"", ".", ".."} for part in parts)
        or any(any(char in part for char in '<>:"|?*') for part in parts)
        or any(part.endswith((" ", ".")) for part in parts)
        or any(re.search(r"[\x00-\x1f]", part) for part in parts)
        or any(part.split(".", 1)[0].casefold() in reserved for part in parts)
    ):
        raise StudioServerError("浏览器相对路径无效")
    return "/".join(parts)


def _multipart_boundary(content_type: str) -> bytes:
    match = re.fullmatch(
        r'\s*multipart/form-data\s*;\s*boundary=(?:"([0-9A-Za-z\'()+_,./:=?-]{1,70})"|([0-9A-Za-z\'()+_,./:=?-]{1,70}))\s*',
        content_type,
        flags=re.IGNORECASE,
    )
    if match is None:
        raise StudioServerError("上传文件必须使用带安全 boundary 的 multipart/form-data")
    return str(match.group(1) or match.group(2)).encode("ascii")


def _multipart_file_bounds(stream: BinaryIO, *, length: int, boundary: bytes) -> tuple[int, int]:
    """Validate a one-file multipart envelope and locate its byte window on disk."""

    opening = b"--" + boundary + b"\r\n"
    stream.seek(0)
    header_window = stream.read(min(length, MAX_MULTIPART_HEADER_BYTES))
    if not header_window.startswith(opening):
        raise StudioServerError("multipart 起始 boundary 无效")
    header_end = header_window.find(b"\r\n\r\n", len(opening))
    if header_end < 0:
        raise StudioServerError("multipart 文件头缺失或超过大小限制")
    try:
        header_lines = header_window[len(opening) : header_end].decode("latin-1").split("\r\n")
    except UnicodeError as exc:  # pragma: no cover - latin-1 accepts all bytes
        raise StudioServerError("multipart 文件头编码无效") from exc
    headers: dict[str, str] = {}
    for line in header_lines:
        if not line or line[:1].isspace() or ":" not in line:
            raise StudioServerError("multipart 文件头格式无效")
        name, value = line.split(":", 1)
        normalized_name = name.strip().casefold()
        if normalized_name in headers or normalized_name not in {
            "content-disposition",
            "content-type",
        }:
            raise StudioServerError("multipart 文件头包含重复或不允许字段")
        headers[normalized_name] = value.strip()
    disposition = headers.get("content-disposition", "")
    if not re.search(r'(?:^|;\s*)name="file"(?:;|$)', disposition, flags=re.IGNORECASE):
        raise StudioServerError('multipart 必须且只能提交 name="file" 的文件字段')

    closing_options = (b"\r\n--" + boundary + b"--\r\n", b"\r\n--" + boundary + b"--")
    closing = b""
    for candidate in closing_options:
        if length < len(candidate):
            continue
        stream.seek(length - len(candidate))
        if stream.read(len(candidate)) == candidate:
            closing = candidate
            break
    if not closing:
        raise StudioServerError("multipart 结束 boundary 无效")
    file_start = header_end + 4
    file_length = length - len(closing) - file_start
    if file_length <= 0 or file_length > MAX_BROWSER_UPLOAD_FILE_BYTES:
        raise StudioServerError("multipart 文件大小超出允许范围")

    intermediate = b"\r\n--" + boundary + b"\r\n"
    stream.seek(file_start)
    remaining = file_length
    overlap = b""
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not chunk:
            raise StudioServerError("multipart 文件内容不完整")
        if intermediate in overlap + chunk:
            raise StudioServerError("每个上传请求只能包含一个文件字段")
        overlap = (overlap + chunk)[-(len(intermediate) - 1) :]
        remaining -= len(chunk)
    return file_start, file_length


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


_STUDIO_PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>小说创作控制台</title>
<style nonce="studiononce">:root{font-family:"Microsoft YaHei",sans-serif;color:#202124;background:#f6f4ef}body{margin:0}header{padding:22px 28px;background:#263238;color:white}main{display:grid;grid-template-columns:230px 1fr;min-height:calc(100vh - 82px)}nav{padding:18px;background:#ece7dd}button{cursor:pointer}nav button{display:block;width:100%;text-align:left;padding:9px;margin:3px 0;border:0;background:transparent}.active{background:#fff!important;border-left:4px solid #8a5a2b!important}section{display:none;padding:24px;max-width:1100px}.show{display:block}.card{background:white;border:1px solid #ddd4c7;border-radius:10px;padding:16px;margin:12px 0}label{display:block;margin:8px 0}input,select,textarea{box-sizing:border-box;width:100%;padding:8px}button.action{padding:9px 14px;background:#6c4425;color:white;border:0;border-radius:6px}.muted{color:#69645d}pre{white-space:pre-wrap;background:#202124;color:#e8eaed;padding:12px;border-radius:8px;max-height:360px;overflow:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}</style></head>
<body><header><h1>小说创作控制台</h1><div id="project"></div></header><main><nav id="nav"></nav><div id="content">
<section data-panel="创建小说"><h2>创建小说</h2><div class="card">选择原创、灵感原创、改编研究、同人或跨作品同人。修改模式需要编辑并重新校验 project.yaml；本页不会绕过配置门禁。<pre id="createCommand">longform-engine project init ...</pre></div></section>
<section data-panel="创作目标"><h2>创作目标</h2><div class="card grid"><label>创作目的<input id="purpose" value="兴趣创作"></label><label>创作类型<select id="workType"><option>原创</option><option>灵感原创</option><option>改编研究</option><option>同人</option><option>跨作品同人</option></select></label><label>篇幅形式<input id="format" value="长篇连载"></label><label>目标平台<input id="platform"></label><label>更新能力<input id="capacity" value="按实际填写"></label><label>验证周期<input id="period" value="按卷复盘"></label></div><label><input id="commercial" type="checkbox" style="width:auto"> 计划商业化</label><label><input id="rights" type="checkbox" style="width:auto"> 已理解同人权利风险声明不是法律鉴定</label><button class="action" id="saveGoal">保存创作目标</button><pre id="goalResult"></pre></section>
<section data-panel="创作沙盒"><h2>创作沙盒</h2><div class="card"><p>沙盒用于试写、比较分歧和测试人物组合，不更新 Canon、图谱、RAG 或正式大纲。</p><pre id="sandboxCommand"></pre></div></section>
<section data-panel="语义文档"><h2>语义文档</h2><div class="card grid"><label>文档类型<input id="semanticType" value="人物理解"></label><label>标题<input id="semanticTitle"></label><label>连续性<input id="semanticContinuity" value="项目候选"></label><label>作用域<input id="semanticScope" value="project_semantic_candidate"></label></div><label>中文 Markdown 语义正文<textarea id="semanticBody" rows="14"></textarea></label><button class="action" id="saveSemantic">保存待独立复核候选</button><pre id="semanticResult"></pre><h3>现有语义文档</h3><pre id="semanticDocuments"></pre></section>
<section data-panel="原著资料库"><h2>原著资料库</h2><button class="action" id="refresh">刷新</button><pre id="catalog"></pre></section>
<section data-panel="批量导入"><h2>批量导入</h2><div class="card"><p>浏览器只上传相对路径和字节；不会把浏览器路径当成服务器的 D:\ 路径。大型 MP4/MKV 外部引用请使用 CLI。</p><label>作品ID<input id="uploadWork"></label><label>资料类型<input id="sourceType" value="动态资料"></label><label>版本<input id="sourceVersion" value="用户指定版本"></label><label>覆盖范围<input id="unitRange" value="待确认"></label><label>选择文件或目录<input id="files" type="file" multiple webkitdirectory></label><button class="action" id="upload">上传并生成分组预览</button><pre id="uploadResult"></pre><label>资料分组（JSON；逐一覆盖全部 file_id）<textarea id="groupPlan" rows="12"></textarea></label><button class="action" id="confirmGroups">确认资料分组</button> <button class="action" id="applyIngest">导入已批准分组</button><pre id="groupResult"></pre></div></section>
<section data-panel="资料处理"><h2>资料处理</h2><pre id="capabilities"></pre><div class="card"><label>资料项ID<input id="processItem"></label><button class="action" id="processPlan">生成本地处理任务</button><button class="action" id="processRun">运行刚生成的任务</button><pre id="processResult"></pre></div></section>
<section data-panel="证据审查"><h2>证据审查</h2><div class="card"><p>这里仅分页显示短规范化片段、原始页码／区域／cue／时间码和处理来源，不显示完整规范化全文。</p><form action="/api/evidence/preview" method="get" target="_blank"><label>资料项ID<input name="item_id" required></label><label>起始位置<input name="offset" type="number" value="0" min="0"></label><input name="limit" type="hidden" value="40"><button class="action" type="submit">载入证据对照</button></form><pre>longform-engine source-library evidence-review-template --item-id ITEM
longform-engine source-library evidence-review-apply --item-id ITEM --file REVIEW --approved-by human</pre></div></section>
<section data-panel="项目绑定"><h2>项目绑定</h2><pre>longform-engine fanfiction item-bind project.yaml --source-id SOURCE --item-id ITEM --approved-by human</pre></section>
<section data-panel="动态覆盖"><h2>动态覆盖</h2><p>默认只门禁身份、设计核心和当前章节依赖；“全作到截止点”仅在人工显式选择后成为门禁。</p><pre id="coverage"></pre></section>
<section data-panel="Canon审批"><h2>Canon审批</h2><div class="card">控制台不会直接批准 Canon。先由 Host Agent 生成任务，再执行独立校验和人工 apply。<pre id="canonCommand"></pre></div></section>
<section data-panel="同人故事发动机"><h2>同人故事发动机</h2><p>原著基线批准后，先明确唯一初始变量、独立长期目标、可持续阻力、原著人物自主性和原作结束后的故事来源。</p><pre>longform-engine fanfiction story-engine-task project.yaml</pre></section>
<section data-panel="原著时间与知识范围"><h2>原著时间与知识范围</h2><p>资料证据范围、项目采用截止点、故事切入点和人物知识范围彼此独立。重大分歧后的未来知识必须重新评估。</p><pre>longform-engine fanfiction status project.yaml --json</pre></section>
<section data-panel="原著事件命运"><h2>原著事件命运</h2><p>重大原著事件使用保留、提前、延迟、结果改变、换人承担、取消、转化或待决定；修改只传播到显式依赖。</p><pre>longform-engine fanfiction event-disposition-status project.yaml --json</pre></section>
<section data-panel="原著人物职责"><h2>原著人物职责</h2><p>路线必须保留原著人物的独立目标、拒绝权、场外行动和不能被原创主角无因果接管的职责。</p><pre>longform-engine fanfiction design-task project.yaml</pre></section>
<section data-panel="同人路线复核"><h2>同人路线复核</h2><p>路线生成会话不能自审；独立复核通过并绑定当前路线、故事发动机和 Canon 哈希后才允许人工 apply。</p><pre>longform-engine fanfiction design-review-task project.yaml --file ROUTE</pre></section>
<section data-panel="跨界宪法"><h2>跨界宪法</h2><p>路线必须选择 fixed_host | fusion_world | sequential_worlds，并显式写 default_host_source_id：fixed_host 使用已配置来源且禁止宿主自转移；fusion_world 必须为 null 且至少两个来源实际参与；sequential_worlds 必须为 null，并以 extensions.crossover.volume_ids 声明适用卷域、为每个声明卷恰好一个宿主。sequential transfer 以 transfers[].volume_ids 声明实际适用卷；fixed_host/fusion_world 中该字段只能缺省或为 null。兼容主题只由 transfers[].payload_kinds 的实际载荷派生，始终包含宿主世界与不可逆后果，不建立全作品两两矩阵。</p><pre id="crossoverContract"></pre><pre>transfers[].source_id + transfers[].payload_kinds + transfers[].volume_ids；fusion_world → 世界规则优先级；sequential_worlds → 卷宿主世界(volume_ids, host_source_id)</pre></section>
<section data-panel="主世界适配器"><h2>主世界适配器</h2><p>主世界适配器只覆盖 transfers 实际引用的 source_id；未转移的已配置来源不需要空适配器。顺序世界按 transfer 卷域与卷宿主归并实际 source-volume-host interaction，每个实际 interaction 恰好一个载荷精确匹配的适配器，每个声明卷至少一个 interaction；不要求无关来源与卷的笛卡尔积。每条适配器以 adapter.payload_kinds 精确绑定实际载荷，以 adapter.host_source_id 和单个 adapter.volume_ids 绑定顺序卷域，再进入跨界宪法。</p><pre>实际 transfer source-volume-host interaction → 主世界适配器(payload_kinds, host_source_id, volume_ids) → 跨界宪法 → 当前卷例外规则</pre></section>
<section data-panel="当前卷同人设计"><h2>当前卷同人设计</h2><p>当前卷同时投影原著范围、人物阶段、原创问题、事件命运、能力规则、原著价值与原创价值。</p><pre>longform-engine intelligence task project.yaml --task-type story_architecture_design</pre></section>
<section data-panel="当前章同人上下文诊断"><h2>当前章同人上下文诊断</h2><p>这里显示 v2 显式必需项、依赖闭包、来源/人物/事件/卷/篇章分区、命名冲突、遗漏、stale 和预算诊断；作者工作单只显示自然中文，多来源或冲突时保留来源标签。</p><pre>longform-engine fanfiction context-status project.yaml --chapter N --json</pre></section>
<section data-panel="全书与分卷"><h2>全书与分卷</h2><pre>longform-engine intelligence task project.yaml --task-type story_architecture_design
longform-engine intelligence task project.yaml --task-type book_design
longform-engine intelligence task project.yaml --task-type outline_design</pre></section>
<section data-panel="情节节点"><h2>情节节点</h2><p>卷级、长期目标、关系阶段、分歧、死亡、背叛和能力突破需逐节点人工决定；微观动作与普通对话不逐项审批。</p><pre>longform-engine planning task project.yaml</pre></section>
<section data-panel="滚动章节"><h2>滚动章节</h2><p>全书保留方向、当前卷保留因果、最近三章 firm、当前章形成场景级 Story Brief。</p><pre>longform-engine production next project.yaml</pre></section>
<section data-panel="章节审阅"><h2>章节审阅</h2><pre id="reviewCommand"></pre></section>
<section data-panel="原著一致性与同人创造性双轴审查"><h2>原著一致性与同人创造性双轴审查</h2><p>原著一致性核对知识、价值排序、关系阶段与规则；同人创造性核对新选择、分歧后果、原创主线与原著人物主体性。一般“新意不够”只作 P2 建议。</p><pre>longform-engine editorial review project.yaml --chapter N</pre></section>
<section data-panel="读者反馈"><h2>读者反馈</h2><p>反馈先形成假设与人工决定，只能转成规划或 Canon 变更提案，不能直接改正文。</p><pre>longform-engine intelligence task project.yaml --task-type reader_feedback_analysis --input FEEDBACK.md</pre></section>
<section data-panel="影响与回溯"><h2>影响与回溯</h2><div class="card">资料升级只生成影响提案；触及定稿章节时进入 revision_branch_v2，不自动替换全文。</div></section>
<section data-panel="平台发布前确认"><h2>平台发布前确认</h2><p>分别显示起点男频与番茄免费档的政策快照、人工权利决定、陈旧原因和导出门禁。这里不自动登录、投稿或回传平台状态。</p><pre id="publicationStatus"></pre><pre id="rightsDecisionCommand"></pre></section>
</div></main><script nonce="studiononce">const csrf="__CSRF_TOKEN__";let state=null,lastJob=null,lastBatch=null;const $=id=>document.getElementById(id);async function api(path,body){const r=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-Studio-CSRF":csrf},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.error||"请求失败");return d.result}function show(id,v){$(id).textContent=typeof v==="string"?v:JSON.stringify(v,null,2)}function panels(){const names=state.panels;$("nav").replaceChildren(...names.map((n,i)=>{const b=document.createElement("button");b.textContent=n;b.className=i===0?"active":"";b.onclick=()=>{document.querySelectorAll("section").forEach(s=>s.classList.toggle("show",s.dataset.panel===n));document.querySelectorAll("nav button").forEach(x=>x.classList.toggle("active",x===b))};return b}));document.querySelector("section").classList.add("show")}async function load(){const r=await fetch("/api/state",{credentials:"same-origin"});state=await r.json();if(!r.ok)throw new Error(state.error);$("project").textContent=`${state.project.title} · ${state.project.creation_mode} · ${state.project.target_platform}`;$("platform").value=state.project.target_platform||"";show("catalog",state.catalog);show("capabilities",state.capabilities);show("coverage",state.coverage);show("semanticDocuments",state.semantic_documents);show("crossoverContract",state.crossover_contract);show("sandboxCommand",state.safe_commands.sandbox);show("canonCommand",state.safe_commands.canon_task);show("reviewCommand",state.safe_commands.chapter_review);if(!$("nav").children.length)panels()}$("refresh").onclick=load;$("saveGoal").onclick=async()=>{try{show("goalResult",await api("/api/creation-goal/save",{purpose:$("purpose").value,work_type:$("workType").value,format:$("format").value,target_platform:$("platform").value,update_capacity:$("capacity").value,validation_period:$("period").value,commercial_intent:$("commercial").checked,fanfiction_rights_risk_confirmed:$("rights").checked}))}catch(e){show("goalResult",e.message)}};$("saveSemantic").onclick=async()=>{try{show("semanticResult",await api("/api/semantic/save",{document_type:$("semanticType").value,title:$("semanticTitle").value,continuity:$("semanticContinuity").value,body:$("semanticBody").value,scope_kind:$("semanticScope").value}));await load()}catch(e){show("semanticResult",e.message)}};$("upload").onclick=async()=>{try{const chosen=[...$("files").files];if(!chosen.length)throw new Error("请选择文件");const session=await api("/api/upload/start",{work_id:$("uploadWork").value,source_type:$("sourceType").value,version:$("sourceVersion").value,unit_range:$("unitRange").value,source_method:"浏览器人工导入",rights_status:"user_claimed_authorized",retention_mode:"full_text",storage_mode:"managed_copy"});for(const f of chosen){const path=f.webkitRelativePath||f.name;const r=await fetch(`/api/upload/file?session=${encodeURIComponent(session.session_id)}&path=${encodeURIComponent(path)}`,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/octet-stream","X-Studio-CSRF":csrf},body:f});const d=await r.json();if(!r.ok)throw new Error(d.error||"上传失败")}const plan=await api("/api/upload/finalize",{session_id:session.session_id});lastBatch=plan.batch_id;$("groupPlan").value=JSON.stringify(plan.groups,null,2);show("uploadResult",plan)}catch(e){show("uploadResult",e.message)}};$("confirmGroups").onclick=async()=>{try{if(!lastBatch)throw new Error("请先生成分组预览");show("groupResult",await api("/api/ingest/groups-confirm",{batch_id:lastBatch,groups:JSON.parse($("groupPlan").value),approved_by:"human"}))}catch(e){show("groupResult",e.message)}};$("applyIngest").onclick=async()=>{try{if(!lastBatch)throw new Error("请先确认资料分组");show("groupResult",await api("/api/ingest/apply",{batch_id:lastBatch,approved_by:"human"}));await load()}catch(e){show("groupResult",e.message)}};$("processPlan").onclick=async()=>{try{lastJob=await api("/api/process/plan",{item_id:$("processItem").value,asset_ids:[],execution:"local",processor_id:"auto",parameters:{}});show("processResult",lastJob)}catch(e){show("processResult",e.message)}};$("processRun").onclick=async()=>{try{if(!lastJob)throw new Error("请先生成处理任务");show("processResult",await api("/api/process/run",{item_id:$("processItem").value,job_id:lastJob.job_id}))}catch(e){show("processResult",e.message)}};load().catch(e=>show("catalog",e.message));</script></body></html>'''


__all__ = ["StudioHTTPServer", "StudioServerError", "StudioService", "studio_page_html"]
