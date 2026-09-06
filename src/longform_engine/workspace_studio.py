"""Workspace-level local Web entry for novel project creation and navigation."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from http import HTTPStatus
from pathlib import Path
from typing import Any
import html
import hmac
import json
import re
import secrets
import subprocess
import time

from longform_engine import __version__
from longform_engine.agent_jobs import CodexAgentJobManager
from longform_engine.agent_tasks import load_manifest, manifest_input_paths, manifest_output
from longform_engine.author_voice import approve_author_voice_edit_pair
from longform_engine.chapter_contract import load_verified_chapter_contract
from longform_engine.config import ConfigError, load_project_config
from longform_engine.human_chapter_intent import (
    INTENT_FIELDS, create_human_chapter_intent_task, human_chapter_intent_paths,
    human_chapter_intent_status, validate_human_chapter_intent, apply_human_chapter_intent,
)
from longform_engine.planning.context import load_chapter_planning_context
from longform_engine.intelligence import (
    DESIGN_INTELLIGENCE_TASK_TYPES,
    apply_compiled_design,
    approve_design_document,
)
from longform_engine.local_web import LocalWebError, LoopbackHTTPServer, LoopbackRequestHandler
from longform_engine.narrative_events import (
    apply_event_realization,
    build_event_realization_application,
    validate_event_realization_application,
)
from longform_engine.orchestration import finalize_chapter, open_book
from longform_engine.production import production_loop, production_next
from longform_engine.review_server import (
    REVIEW_ACTION_FIELDS,
    ReviewDeskService,
    ReviewServerError,
    dispatch_review_action,
    review_page_html,
)
from longform_engine.reader_promises_v2 import (
    apply_promise_evidence,
    build_promise_evidence_application,
    validate_promise_evidence_application,
)
from longform_engine.storage import acquire_project_lock, atomic_write_text, init_project
from longform_engine.storage.layout import manuscript_chapter_path
from longform_engine.semantic import chapter_close, semantic_apply


PROJECT_SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{1,79}")
PROJECT_CREATE_FIELDS = {
    "title",
    "slug",
    "creation_mode",
    "continuity_mode",
    "target_platform",
    "target_audience",
    "writing_style",
    "core_promise",
    "main_question",
    "ending_direction",
    "forbidden_experience",
    "automation_level",
    "target_total_characters",
    "chapter_target_characters",
    "volume_target_characters",
    "planning_horizon",
    "refill_threshold",
    "rights_risk_acknowledged",
    "sources",
}


class WorkspaceStudioError(LocalWebError):
    """Raised when a workspace action crosses its declared filesystem boundary."""


class WorkspaceStudioService:
    """Own project discovery, creation, and deep-link state inside one workspace root."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        create: bool = False,
        agent_jobs: CodexAgentJobManager | None = None,
    ) -> None:
        raw = Path(workspace).expanduser()
        if not raw.is_absolute():
            raise WorkspaceStudioError("工作区必须是明确的绝对目录")
        resolved = raw.resolve()
        if resolved.parent == resolved:
            raise WorkspaceStudioError("磁盘根目录不能作为小说工作区")
        if resolved == Path.home().resolve():
            raise WorkspaceStudioError("用户目录不能直接作为小说工作区")
        if resolved.exists() and not resolved.is_dir():
            raise WorkspaceStudioError("工作区路径必须是目录")
        if not resolved.exists():
            if not create:
                raise WorkspaceStudioError("工作区不存在；请明确允许创建")
            resolved.mkdir(parents=True)
        self.root = resolved
        self.agent_jobs = agent_jobs or CodexAgentJobManager()
        self._codex_status_cache: tuple[float, dict[str, Any]] | None = None

    def state(self) -> dict[str, Any]:
        from longform_engine.fanfiction_creative_requirements import (
            CONTINUITY_REQUIREMENTS, ROUTE_FAMILIES, compile_fanfiction_creative_requirements,
        )
        projects = [self._project_card(config_path) for config_path in self._project_configs()]
        return {
            "schema": "novel_workspace_studio_state_v1",
            "workspace": str(self.root),
            "engine_version": __version__,
            "codex": self.codex_status(),
            "projects": projects,
            "project_count": len(projects),
            "creation_modes": ["original", "fanfiction"],
            "fanfiction_creative_requirements": {
                mode: {family: compile_fanfiction_creative_requirements(mode, family) for family in sorted(ROUTE_FAMILIES)}
                for mode in CONTINUITY_REQUIREMENTS
            },
            "routes": [
                "/",
                "/projects/new",
                "/projects/{id}",
                "/projects/{id}/design",
                "/projects/{id}/sources",
                "/projects/{id}/fanfiction",
                "/projects/{id}/planning",
                "/projects/{id}/chapters/{n}",
                "/projects/{id}/knowledge",
                "/projects/{id}/publication",
                "/projects/{id}/literary",
                "/projects/{id}/recovery",
            ],
            "boundaries": {
                "workspace_escape_allowed": False,
                "arbitrary_command_allowed": False,
                "arbitrary_agent_prompt_allowed": False,
                "open_page_mutates_canonical": False,
            },
        }

    def codex_status(self) -> dict[str, Any]:
        now = time.monotonic()
        cached = self._codex_status_cache
        if cached is not None and now - cached[0] < 30:
            return dict(cached[1])
        try:
            status = self.agent_jobs.runtime_status()
        except (OSError, subprocess.SubprocessError) as exc:
            status = {
                "available": False,
                "installed": False,
                "logged_in": False,
                "reason": str(exc),
            }
        self._codex_status_cache = (now, status)
        return dict(status)

    def create_project(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != PROJECT_CREATE_FIELDS:
            raise WorkspaceStudioError("创建项目字段不完整或包含未知字段")
        title = self._required_text(payload, "title")
        slug = self._required_text(payload, "slug").lower()
        if PROJECT_SLUG_PATTERN.fullmatch(slug) is None:
            raise WorkspaceStudioError("项目 slug 必须是 2-80 位小写字母、数字、连字符或下划线")
        destination = (self.root / slug).resolve()
        self._require_inside_workspace(destination)
        if destination.exists():
            raise WorkspaceStudioError(f"项目目录已经存在：{slug}")

        creation_mode = self._required_text(payload, "creation_mode")
        if creation_mode not in {"original", "fanfiction"}:
            raise WorkspaceStudioError("creation_mode 只能是 original 或 fanfiction")
        sources = payload["sources"]
        if not isinstance(sources, list):
            raise WorkspaceStudioError("sources 必须是列表")
        rights_acknowledged = payload["rights_risk_acknowledged"]
        if not isinstance(rights_acknowledged, bool):
            raise WorkspaceStudioError("rights_risk_acknowledged 必须为布尔值")
        if creation_mode == "fanfiction" and not rights_acknowledged:
            raise WorkspaceStudioError("创建同人项目必须明确确认权利风险边界")
        if creation_mode == "original" and sources:
            raise WorkspaceStudioError("原创项目不能配置原著来源")

        chapter_target = self._positive_int(payload, "chapter_target_characters")
        total_target = self._positive_int(payload, "target_total_characters")
        volume_target = self._positive_int(payload, "volume_target_characters")
        planning_horizon = self._positive_int(payload, "planning_horizon")
        refill_threshold = self._positive_int(payload, "refill_threshold")
        forbidden = payload["forbidden_experience"]
        if not isinstance(forbidden, list) or any(
            not isinstance(item, str) or not item.strip() for item in forbidden
        ):
            raise WorkspaceStudioError("forbidden_experience 必须是非空字符串列表")

        overrides = {
            "creation": {"mode": creation_mode},
            "fanfiction": {
                "continuity_mode": self._required_text(payload, "continuity_mode"),
                "sources": sources,
            },
            "project": {"slug": slug, "title": title, "root_dir": str(destination)},
            "novel": {
                "target_platform": self._required_text(payload, "target_platform"),
                "audience": self._required_text(payload, "target_audience"),
                "style": self._required_text(payload, "writing_style"),
                "core_promise": self._required_text(payload, "core_promise"),
                "main_question": self._required_text(payload, "main_question"),
                "ending_direction": self._required_text(payload, "ending_direction"),
                "forbidden_experience": [str(item).strip() for item in forbidden],
            },
            "length": {
                "metric": "content_characters_v1",
                "target_total_characters": total_target,
                "completion_tolerance": [0.90, 1.10],
                "chapter": {
                    "target_characters": chapter_target,
                    "soft_min": max(1, int(chapter_target * 0.8)),
                    "soft_max": max(chapter_target, int(chapter_target * 1.2)),
                    "hard_min": max(1, int(chapter_target * 0.64)),
                    "hard_max": max(chapter_target, int(chapter_target * 1.4)),
                },
                "volume": {"target_characters": volume_target},
                "planning": {
                    "mode": "rolling",
                    "detailed_horizon": planning_horizon,
                    "refill_threshold": refill_threshold,
                },
            },
        }
        try:
            template = load_project_config(template="qidian-longform", cli_overrides=overrides)
        except ConfigError as exc:
            raise WorkspaceStudioError(str(exc)) from exc
        initialized = init_project(template, output=destination)
        config = load_project_config(initialized.project_config)
        confirmations = {
            "target_audience": self._required_text(payload, "target_audience"),
            "writing_style": self._required_text(payload, "writing_style"),
            "core_forbidden_zone": [str(item).strip() for item in forbidden],
            "automation_level": self._required_text(payload, "automation_level"),
            "target_scale": f"{total_target} content characters",
        }
        with acquire_project_lock(config, owner="workspace-studio", command="create-project open-book"):
            open_book(config, confirmations)
            atomic_write_text(
                destination / "50_workbench" / "项目创建决定.json",
                json.dumps(
                    {
                        "schema": "workspace_project_creation_decision_v1",
                        "creation_mode": creation_mode,
                        "rights_risk_acknowledged": rights_acknowledged,
                        "source_rights_statuses": [
                            {
                                "source_id": source.get("source_id"),
                                "rights_status": source.get("rights_status"),
                                "retention_mode": source.get("retention_mode", "metadata_only"),
                                "commercial_intent": source.get("commercial_intent"),
                            }
                            for source in sources
                            if isinstance(source, dict)
                        ],
                        "canonical_mutated": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        return self.project_state(self._project_id(initialized.project_config))

    def import_project(self, config_path: str | Path) -> dict[str, Any]:
        candidate = Path(config_path).expanduser()
        if not candidate.is_absolute():
            raise WorkspaceStudioError("导入项目必须提供绝对 project.yaml 路径")
        resolved = candidate.resolve()
        self._require_inside_workspace(resolved)
        if resolved.name != "project.yaml" or not resolved.is_file():
            raise WorkspaceStudioError("导入目标必须是现有 project.yaml")
        if resolved.parent == self.root:
            raise WorkspaceStudioError("工作区根目录本身不能同时作为小说项目")
        try:
            load_project_config(resolved)
        except ConfigError as exc:
            raise WorkspaceStudioError(str(exc)) from exc
        registry_path = self.root / ".longform-studio.json"
        registry = self._read_workspace_registry()
        relative = resolved.relative_to(self.root).as_posix()
        projects = [str(item) for item in registry.get("projects") or [] if isinstance(item, str)]
        if relative not in projects:
            projects.append(relative)
            atomic_write_text(
                registry_path,
                json.dumps(
                    {"schema": "novel_workspace_registry_v1", "projects": sorted(projects)},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
        return self.project_state(self._project_id(resolved))

    def project_state(self, project_id: str) -> dict[str, Any]:
        config_path = self._config_for_project_id(project_id)
        card = self._project_card(config_path)
        if card.get("status") == "invalid":
            raise WorkspaceStudioError(str(card.get("error") or "项目配置无效"))
        next_action = dict(card["next_action"])
        chapter_number = int(next_action.get("chapter_number") or 0)
        return {
            "schema": "novel_workspace_project_state_v1",
            "project": card,
            "next_action": next_action,
            "candidate": self._candidate_projection(config_path, next_action),
            "canonical_candidate": self._canonical_candidate_projection(
                config_path, next_action
            ),
            "semantic_candidate": self._semantic_candidate_projection(
                config_path, next_action
            ),
            "chapter_context": (
                self._chapter_context(config_path, chapter_number)
                if chapter_number > 0
                else None
            ),
            "chapter_confirmation": (
                self._chapter_confirmation_projection(
                    config_path, chapter_number, next_action
                )
                if chapter_number > 0
                else None
            ),
            "agent_jobs": self.list_agent_jobs(project_id),
            "navigation": {
                "dashboard": f"/projects/{project_id}",
                "design": f"/projects/{project_id}/design",
                "sources": f"/projects/{project_id}/sources",
                "fanfiction": f"/projects/{project_id}/fanfiction",
                "planning": f"/projects/{project_id}/planning",
                "knowledge": f"/projects/{project_id}/knowledge",
                "publication": f"/projects/{project_id}/publication",
                "literary": f"/projects/{project_id}/literary",
                "recovery": f"/projects/{project_id}/recovery",
            },
            "canonical_write_allowed": False,
        }

    def chapter_state(self, project_id: str, chapter_number: int) -> dict[str, Any]:
        if chapter_number <= 0:
            raise WorkspaceStudioError("章节号必须为正整数")
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        root = config_path.parent
        draft = manuscript_chapter_path(root, chapter_number, lane="draft")
        final = manuscript_chapter_path(root, chapter_number, lane="final")
        production = production_next(config)
        review: dict[str, Any]
        if draft.is_file() and not final.is_file():
            try:
                review = {
                    "available": True,
                    "state": ReviewDeskService(config, chapter_number=chapter_number).state(),
                }
            except ReviewServerError as exc:
                review = {"available": False, "error": str(exc)}
        else:
            review = {"available": False, "reason": "current chapter draft is missing"}
        return {
            "schema": "novel_workspace_chapter_state_v1",
            "project_id": project_id,
            "chapter_number": chapter_number,
            "production": production,
            "review": review,
            "canonical_write_allowed": False,
            "actions": {
                "finalize": (
                    production.get("status") == "awaiting_finalize"
                    and int(production.get("chapter_number") or 0) == chapter_number
                ),
                "semantic_apply": (
                    production.get("status") == "agent_task_validated"
                    and production.get("task_type") == "chapter_semantic"
                    and int(production.get("chapter_number") or 0) == chapter_number
                ),
                "close": (
                    production.get("status") == "awaiting_chapter_close"
                    and int(production.get("chapter_number") or 0) == chapter_number
                ),
                "human_revision_workbench": review.get("available") is True,
            },
            "chapter_context": self._chapter_context(config_path, chapter_number),
            "semantic_candidate": self._semantic_candidate_projection(
                config_path, production
            ),
        }

    def literary_state(self, project_id: str) -> dict[str, Any]:
        from longform_engine.quality.status import quality_status
        config = load_project_config(self._config_for_project_id(project_id))
        quality = quality_status(config)
        return {**quality["literary_trials"], "quality_axes": {
            "protocol_ready": quality["protocol_ready"],
            "author_acceptance_ready": quality["author_acceptance_ready"],
            "platform_preflights": quality["platform_preflights"],
        }}

    def literary_review_state(self, project_id: str, trial_id: str, reviewer_id: str,
                             token: str) -> dict[str, Any]:
        from longform_engine.fanfiction_literary_trial import literary_reviewer_state
        if not token:
            raise WorkspaceStudioError("缺少此评审人的独立入口凭证")
        root = self._config_for_project_id(project_id).parent
        try:
            return literary_reviewer_state(root, trial_id, reviewer_id, token=token)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WorkspaceStudioError("匿名包已失效或评审入口凭证无效，请联系评测组织者") from exc

    def literary_action(self, project_id: str, action: str, body: dict[str, Any],
                        *, token: str = "") -> dict[str, Any]:
        from longform_engine.fanfiction_literary_trial import (
            create_literary_trial, register_literary_reviewer,
            resolve_literary_issues, save_literary_review, record_literary_effort,
        )
        fields = {
            "create": {"trial_id", "stage", "samples"},
            "reviewer-add": {"trial_id", "reviewer_id"},
            "save": {"trial_id", "reviewer_id", "draft", "expected_sha256"},
            "submit": {"trial_id", "reviewer_id", "draft", "expected_sha256"},
            "resolve": {"trial_id", "submission_sha256", "decisions", "decided_by"},
            "effort-record": {"chapter_number", "human_review_minutes", "human_edit_minutes"},
        }
        if action not in fields or set(body) != fields[action]:
            raise WorkspaceStudioError("文学评测动作字段无效")
        config = load_project_config(self._config_for_project_id(project_id))
        root = config.path.parent
        with acquire_project_lock(config, command=f"studio literary {action}"):
            if action == "effort-record":
                return record_literary_effort(config, **body)
            if action == "create":
                samples = []
                for item in body["samples"]:
                    if set(item) != {"project_id", "chapter_start", "chapter_end"}:
                        raise WorkspaceStudioError("网页样本必须使用工作区项目 ID 与章节范围")
                    from longform_engine.storage import resolve_project_root
                    source_config = self._config_for_project_id(item["project_id"])
                    source_root = resolve_project_root(load_project_config(source_config))
                    if source_root != source_config.parent or not source_root.is_relative_to(self.root):
                        raise WorkspaceStudioError("样本根目录不属于当前工作区项目")
                    samples.append({"config_path": str(source_config),
                                    "chapter_start": item["chapter_start"], "chapter_end": item["chapter_end"]})
                return create_literary_trial(config, trial_id=body["trial_id"], stage=body["stage"], samples=samples)
            if action in {"save", "submit"}:
                if not token:
                    raise WorkspaceStudioError("缺少此评审人的独立入口凭证")
                try:
                    return save_literary_review(root, submit=action == "submit", token=token, **body)
                except (OSError, KeyError, TypeError) as exc:
                    raise WorkspaceStudioError("匿名评审材料或评分字段无效，请重新打开本人入口") from exc
            if action == "reviewer-add":
                return register_literary_reviewer(root, **body)
            return resolve_literary_issues(root, **body)

    def chapter_intent_state(self, project_id: str, chapter: int) -> dict[str, Any]:
        config_path = self._config_for_project_id(project_id)
        root = config_path.parent
        planning = load_chapter_planning_context(root, chapter)
        path = human_chapter_intent_paths(root, chapter)["candidate"]
        characters = json.loads((root / "10_bible" / "characters.json").read_text(encoding="utf-8"))
        if not isinstance(characters, list):
            raise WorkspaceStudioError("人物资料必须为列表")
        return {
            "chapter_number": chapter,
            "current": human_chapter_intent_status(root, chapter),
            "candidate": self._read_json_object(path) if path.is_file() else None,
            "candidate_sha256": sha256(path.read_bytes()).hexdigest() if path.is_file() else "",
            "characters": [{"id": item["id"], "name": item.get("name") or item["id"],
                            "participates": item["id"] in planning.character_ids}
                           for item in characters if isinstance(item, dict) and item.get("id")],
        }

    def chapter_intent_action(
        self, project_id: str, chapter: int, action: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        root = config_path.parent
        fields = {*INTENT_FIELDS, "expression_focus", "protected_items"}
        required = {
            "create": set(),
            "save": {"expected_sha256", "fields"},
            "apply": {"expected_sha256", "acknowledge_human_decision"},
        }
        if action not in required or set(payload) != required[action]:
            raise WorkspaceStudioError("人工意图动作字段无效")
        with acquire_project_lock(config, owner="workspace-studio", command=f"chapter human-intent-{action}"):
            load_chapter_planning_context(root, chapter)
            path = human_chapter_intent_paths(root, chapter)["candidate"]
            if action == "create":
                create_human_chapter_intent_task(config, chapter_number=chapter)
            else:
                if not path.is_file() or sha256(path.read_bytes()).hexdigest() != payload["expected_sha256"]:
                    raise WorkspaceStudioError("人工意图草稿已变化，请刷新后重试")
                if action == "save":
                    supplied = payload["fields"]
                    if not isinstance(supplied, dict) or set(supplied) != fields:
                        raise WorkspaceStudioError("只接受人工填写的创作字段")
                    candidate = self._read_json_object(path)
                    candidate.update(supplied)
                    candidate["completed_by"] = "human"
                    atomic_write_text(path, json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")
                    validation = validate_human_chapter_intent(config, chapter_number=chapter, file_path=path)
                    return {**self.chapter_intent_state(project_id, chapter), "validation": asdict(validation)}
                if payload["acknowledge_human_decision"] is not True:
                    raise WorkspaceStudioError("应用人工意图需要人类明确确认")
                validation = validate_human_chapter_intent(config, chapter_number=chapter, file_path=path)
                if not validation.ok:
                    raise WorkspaceStudioError("；".join(validation.errors))
                apply_human_chapter_intent(config, chapter_number=chapter, file_path=path, approved_by="human")
        return self.chapter_intent_state(project_id, chapter)

    def start_agent_job(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"task_id"}:
            raise WorkspaceStudioError("浏览器只能提交当前 manifest 的 task_id，不能提交任意 Prompt")
        task_id = self._required_text(payload, "task_id")
        config = load_project_config(self._config_for_project_id(project_id))
        return self.agent_jobs.start(config, task_id)

    def agent_job_status(self, project_id: str, job_id: str) -> dict[str, Any]:
        config = load_project_config(self._config_for_project_id(project_id))
        return self.agent_jobs.status(config, job_id)

    def list_agent_jobs(self, project_id: str) -> list[dict[str, Any]]:
        config = load_project_config(self._config_for_project_id(project_id))
        return self.agent_jobs.list_jobs(config)

    def cancel_agent_job(self, project_id: str, job_id: str) -> dict[str, Any]:
        config = load_project_config(self._config_for_project_id(project_id))
        return self.agent_jobs.cancel(config, job_id)

    def advance_production(self, project_id: str) -> dict[str, Any]:
        config = load_project_config(self._config_for_project_id(project_id))
        with acquire_project_lock(
            config, owner="workspace-studio", command="production advance-safe"
        ):
            result = production_loop(config, max_steps=10, no_apply=True)
        return {**result, "canonical_mutated": False}

    def approve_design_candidate(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "task_id",
            "expected_sha256",
            "approved_by",
            "acknowledge_human_decision",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("设计批准字段不完整或包含未知字段")
        if payload["approved_by"] != "human" or payload["acknowledge_human_decision"] is not True:
            raise WorkspaceStudioError("设计批准必须由人类明确确认")
        config = load_project_config(self._config_for_project_id(project_id))
        root = self._config_for_project_id(project_id).parent
        manifest = load_manifest(root, self._required_text(payload, "task_id"))
        task_type = str(manifest.get("task_type") or "")
        if task_type not in DESIGN_INTELLIGENCE_TASK_TYPES:
            raise WorkspaceStudioError("当前任务不是可人工批准的设计文档")
        if str(manifest.get("status") or "") != "validated":
            raise WorkspaceStudioError("设计文档必须先完成协议与领域校验")
        output = str(manifest_output(manifest).get("path") or "")
        candidate = (root / output).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceStudioError("设计候选路径逃逸项目") from exc
        digest = sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else ""
        if digest != str(payload["expected_sha256"]):
            raise WorkspaceStudioError("设计候选已变化；请刷新后重新确认")
        with acquire_project_lock(
            config, owner="workspace-studio", command="intelligence approve-design"
        ):
            result = approve_design_document(
                config,
                task_type=task_type,
                document_path=output,
                approved_by="human",
            )
        return {**asdict(result), "canonical_mutated": False}

    def apply_compiled_design_candidate(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "task_id",
            "expected_sha256",
            "approved_by",
            "acknowledge_canonical_write",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("Canon apply 字段不完整或包含未知字段")
        if payload["approved_by"] != "human" or payload["acknowledge_canonical_write"] is not True:
            raise WorkspaceStudioError("写入 Canon 必须由人类明确确认")
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        root = config_path.parent.resolve()
        task_id = self._required_text(payload, "task_id")
        current = production_next(config)
        if (
            current.get("status") != "agent_task_validated"
            or current.get("task_type") != "design_semantic_compile"
            or current.get("task_id") != task_id
        ):
            raise WorkspaceStudioError("只能 apply 当前已校验的语义编译工单")
        manifest = load_manifest(root, task_id)
        source_task_type, document = self._compiled_design_source(manifest)
        output = str(manifest_output(manifest).get("path") or "")
        delta = (root / output).resolve()
        try:
            delta.relative_to(root)
        except ValueError as exc:
            raise WorkspaceStudioError("语义编译候选路径逃逸项目") from exc
        digest = sha256(delta.read_bytes()).hexdigest() if delta.is_file() else ""
        if digest != str(payload["expected_sha256"]):
            raise WorkspaceStudioError("语义编译候选已变化；请刷新后重新确认")
        with acquire_project_lock(
            config, owner="workspace-studio", command="intelligence apply-compiled-design"
        ):
            result = apply_compiled_design(
                config,
                task_type=source_task_type,
                document_path=document,
                delta_path=output,
                approved_by="human",
            )
        return {**asdict(result), "canonical_mutated": True}

    def finalize_current_chapter(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "chapter_number",
            "expected_draft_sha256",
            "approved_by",
            "acknowledge_finalize",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("章节定稿字段不完整或包含未知字段")
        if payload["approved_by"] != "human" or payload["acknowledge_finalize"] is not True:
            raise WorkspaceStudioError("章节定稿必须由人类明确确认")
        chapter_number = self._payload_chapter_number(payload)
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        current = production_next(config)
        if (
            current.get("status") != "awaiting_finalize"
            or int(current.get("chapter_number") or 0) != chapter_number
        ):
            raise WorkspaceStudioError("当前生产状态不允许定稿这个章节")
        draft = manuscript_chapter_path(config_path.parent, chapter_number, lane="draft")
        digest = sha256(draft.read_bytes()).hexdigest() if draft.is_file() else ""
        if digest != str(payload["expected_draft_sha256"]):
            raise WorkspaceStudioError("章节草稿已变化；请刷新后重新确认")
        with acquire_project_lock(
            config, owner="workspace-studio", command="chapter finalize"
        ):
            result = finalize_chapter(
                config,
                chapter_number=chapter_number,
                approved_by="human",
                overwrite=False,
            )
        return {**asdict(result), "canonical_mutated": True}

    def apply_chapter_semantic_candidate(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "task_id",
            "chapter_number",
            "expected_sha256",
            "approved_by",
            "acknowledge_semantic_apply",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("章节语义 apply 字段不完整或包含未知字段")
        if (
            payload["approved_by"] != "human"
            or payload["acknowledge_semantic_apply"] is not True
        ):
            raise WorkspaceStudioError("章节语义 apply 必须由人类明确确认")
        chapter_number = self._payload_chapter_number(payload)
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        root = config_path.parent.resolve()
        task_id = self._required_text(payload, "task_id")
        current = production_next(config)
        if (
            current.get("status") != "agent_task_validated"
            or current.get("task_type") != "chapter_semantic"
            or current.get("task_id") != task_id
            or int(current.get("chapter_number") or 0) != chapter_number
        ):
            raise WorkspaceStudioError("只能 apply 当前已校验的章节语义工单")
        manifest = load_manifest(root, task_id)
        relative = str(manifest_output(manifest).get("path") or "")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceStudioError("章节语义候选路径逃逸项目") from exc
        digest = sha256(candidate.read_bytes()).hexdigest() if candidate.is_file() else ""
        if digest != str(payload["expected_sha256"]):
            raise WorkspaceStudioError("章节语义候选已变化；请刷新后重新确认")
        with acquire_project_lock(
            config, owner="workspace-studio", command="chapter semantic-apply"
        ):
            result = semantic_apply(
                config, chapter_number=chapter_number, file_path=candidate
            )
        return {**asdict(result), "canonical_mutated": True, "approved_by": "human"}

    def close_current_chapter(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "chapter_number",
            "expected_final_sha256",
            "approved_by",
            "acknowledge_chapter_close",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("章节关闭字段不完整或包含未知字段")
        if (
            payload["approved_by"] != "human"
            or payload["acknowledge_chapter_close"] is not True
        ):
            raise WorkspaceStudioError("章节关闭必须由人类明确确认")
        chapter_number = self._payload_chapter_number(payload)
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        current = production_next(config)
        if (
            current.get("status") != "awaiting_chapter_close"
            or int(current.get("chapter_number") or 0) != chapter_number
        ):
            raise WorkspaceStudioError("当前章节尚未满足关闭条件")
        final = manuscript_chapter_path(config_path.parent, chapter_number, lane="final")
        digest = sha256(final.read_bytes()).hexdigest() if final.is_file() else ""
        if digest != str(payload["expected_final_sha256"]):
            raise WorkspaceStudioError("正式章节已变化；请刷新后重新确认")
        with acquire_project_lock(
            config, owner="workspace-studio", command="chapter close"
        ):
            result = chapter_close(
                config, chapter_number=chapter_number, approved_by="human"
            )
        return {**asdict(result), "canonical_mutated": True}

    def confirm_chapter_events(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "chapter_number",
            "expected_final_sha256",
            "observations",
            "discovered_causal_nodes",
            "realized_major_divergences",
            "confirmed_by",
            "acknowledge_event_apply",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("事件确认字段不完整或包含未知字段")
        if payload["confirmed_by"] != "human" or payload["acknowledge_event_apply"] is not True:
            raise WorkspaceStudioError("事件确认必须由人类明确确认")
        for field in ("observations", "discovered_causal_nodes", "realized_major_divergences"):
            if not isinstance(payload[field], list) or any(
                not isinstance(item, dict) for item in payload[field]
            ):
                raise WorkspaceStudioError(f"{field} 必须是对象列表")
        chapter_number = self._payload_chapter_number(payload)
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        root = config_path.parent.resolve()
        current = production_next(config)
        if (
            current.get("status") != "awaiting_event_realization"
            or int(current.get("chapter_number") or 0) != chapter_number
        ):
            raise WorkspaceStudioError("当前章节不等待事件确认")
        self._require_final_hash(root, chapter_number, str(payload["expected_final_sha256"]))
        application = build_event_realization_application(
            config,
            chapter_number=chapter_number,
            observations=payload["observations"],
            discovered_causal_nodes=payload["discovered_causal_nodes"],
            realized_major_divergences=payload["realized_major_divergences"],
            confirmed_by="human",
        )
        validation = validate_event_realization_application(config, application)
        if not validation.ok:
            raise WorkspaceStudioError(
                "事件确认未通过：" + "; ".join([*validation.errors, *validation.warnings])
            )
        application_file = (
            root / "50_workbench" / "event_realizations" / f"ch{chapter_number:03d}.json"
        )
        with acquire_project_lock(
            config, owner="workspace-studio", command="chapter event-realization-apply"
        ):
            atomic_write_text(
                application_file,
                json.dumps(application, ensure_ascii=False, indent=2) + "\n",
            )
            result = apply_event_realization(
                config, application_path=application_file
            )
        return {**asdict(result), "canonical_mutated": True}

    def approve_author_voice_pair(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "chapter_number",
            "expected_final_sha256",
            "record",
            "approved_by",
            "acknowledge_voice_apply",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("作者声音样本字段不完整或包含未知字段")
        if payload["approved_by"] != "human" or payload["acknowledge_voice_apply"] is not True:
            raise WorkspaceStudioError("作者声音样本必须由人类明确确认")
        if not isinstance(payload["record"], dict):
            raise WorkspaceStudioError("record 必须是对象")
        chapter_number = self._payload_chapter_number(payload)
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        root = config_path.parent.resolve()
        current = production_next(config)
        if (
            current.get("status") != "awaiting_author_voice_approval"
            or int(current.get("chapter_number") or 0) != chapter_number
        ):
            raise WorkspaceStudioError("当前章节不等待作者声音样本")
        self._require_final_hash(root, chapter_number, str(payload["expected_final_sha256"]))
        record_file = (
            root
            / "50_workbench"
            / "human_author_revisions"
            / f"ch{chapter_number:03d}"
            / f"ch{chapter_number:03d}.voice_pair.web.json"
        )
        with acquire_project_lock(
            config, owner="workspace-studio", command="creative author-voice-approve"
        ):
            atomic_write_text(
                record_file,
                json.dumps(payload["record"], ensure_ascii=False, indent=2) + "\n",
            )
            result = approve_author_voice_edit_pair(
                config,
                chapter_number=chapter_number,
                record_path=record_file,
                approved_by="human",
            )
        return {**asdict(result), "canonical_mutated": True}

    def confirm_reader_promises(
        self, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "chapter_number",
            "expected_final_sha256",
            "evidence",
            "confirmed_by",
            "acknowledge_promise_apply",
        }
        if set(payload) != required:
            raise WorkspaceStudioError("读者承诺确认字段不完整或包含未知字段")
        if (
            payload["confirmed_by"] != "human"
            or payload["acknowledge_promise_apply"] is not True
        ):
            raise WorkspaceStudioError("读者承诺确认必须由人类明确确认")
        if not isinstance(payload["evidence"], list) or any(
            not isinstance(item, dict) for item in payload["evidence"]
        ):
            raise WorkspaceStudioError("evidence 必须是对象列表")
        chapter_number = self._payload_chapter_number(payload)
        config_path = self._config_for_project_id(project_id)
        config = load_project_config(config_path)
        root = config_path.parent.resolve()
        current = production_next(config)
        if (
            current.get("status") != "awaiting_reader_promise_evidence"
            or int(current.get("chapter_number") or 0) != chapter_number
        ):
            raise WorkspaceStudioError("当前章节不等待读者承诺证据确认")
        self._require_final_hash(root, chapter_number, str(payload["expected_final_sha256"]))
        contract, _contract_hash = load_verified_chapter_contract(root, chapter_number)
        actions = contract.get("reader_promise_actions") or []
        if not isinstance(actions, list) or any(not isinstance(item, dict) for item in actions):
            raise WorkspaceStudioError("章节合同的读者承诺动作无效")
        application = build_promise_evidence_application(
            root,
            chapter_number=chapter_number,
            actions=actions,
            evidence=payload["evidence"],
            confirmed_by="human",
        )
        errors = validate_promise_evidence_application(root, application)
        if errors:
            raise WorkspaceStudioError("读者承诺证据未通过：" + "; ".join(errors))
        application_file = (
            root / "50_workbench" / "promise_evidence" / f"ch{chapter_number:03d}.json"
        )
        with acquire_project_lock(
            config, owner="workspace-studio", command="chapter promise-evidence-apply"
        ):
            atomic_write_text(
                application_file,
                json.dumps(application, ensure_ascii=False, indent=2) + "\n",
            )
            result = apply_promise_evidence(
                config, application_path=application_file
            )
        return {**asdict(result), "canonical_mutated": True}

    def review_state(self, project_id: str, chapter_number: int) -> dict[str, Any]:
        config = load_project_config(self._config_for_project_id(project_id))
        return ReviewDeskService(config, chapter_number=chapter_number).state()

    def review_action(
        self,
        project_id: str,
        chapter_number: int,
        action: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        config = load_project_config(self._config_for_project_id(project_id))
        service = ReviewDeskService(config, chapter_number=chapter_number)
        return dispatch_review_action(service, action, payload)

    def _project_configs(self) -> list[Path]:
        candidates = list(self.root.glob("*/project.yaml"))
        for relative in self._read_workspace_registry().get("projects") or []:
            if isinstance(relative, str):
                candidates.append(self.root / relative)
        configs: list[Path] = []
        for candidate in candidates:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
            try:
                resolved.relative_to(self.root)
            except ValueError:
                continue
            configs.append(resolved)
        return sorted(set(configs), key=lambda path: path.relative_to(self.root).as_posix().casefold())

    def _read_workspace_registry(self) -> dict[str, Any]:
        path = self.root / ".longform-studio.json"
        if not path.is_file():
            return {"schema": "novel_workspace_registry_v1", "projects": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {"schema": "novel_workspace_registry_v1", "projects": []}
        if not isinstance(payload, dict) or payload.get("schema") != "novel_workspace_registry_v1":
            return {"schema": "novel_workspace_registry_v1", "projects": []}
        return payload

    def _config_for_project_id(self, project_id: str) -> Path:
        if not re.fullmatch(r"project_[0-9a-f]{20}", project_id):
            raise WorkspaceStudioError("项目 ID 无效")
        for config_path in self._project_configs():
            if self._project_id(config_path) == project_id:
                return config_path
        raise WorkspaceStudioError("项目不存在或不属于当前工作区")

    def _project_card(self, config_path: Path) -> dict[str, Any]:
        project_id = self._project_id(config_path)
        relative = config_path.relative_to(self.root).as_posix()
        try:
            config = load_project_config(config_path)
        except ConfigError as exc:
            return {
                "id": project_id,
                "relative_config": relative,
                "status": "invalid",
                "error": str(exc),
            }
        return {
            "id": project_id,
            "relative_config": relative,
            "status": "ready",
            "title": config.data["project"]["title"],
            "slug": config.data["project"]["slug"],
            "creation_mode": config.data["creation"]["mode"],
            "continuity_mode": config.data["fanfiction"]["continuity_mode"],
            "target_platform": config.data["novel"]["target_platform"],
            "next_action": production_next(config),
        }

    def _candidate_projection(
        self, config_path: Path, next_action: dict[str, Any]
    ) -> dict[str, Any] | None:
        if (
            next_action.get("status") != "agent_task_validated"
            or next_action.get("task_type") not in DESIGN_INTELLIGENCE_TASK_TYPES
        ):
            return None
        root = config_path.parent.resolve()
        manifest = load_manifest(root, str(next_action.get("task_id") or ""))
        relative = str(manifest_output(manifest).get("path") or "")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceStudioError("设计候选路径逃逸项目") from exc
        if not candidate.is_file():
            return None
        raw = candidate.read_bytes()
        content = raw.decode("utf-8")
        limit = 500_000
        return {
            "task_id": str(manifest.get("task_id") or ""),
            "task_type": str(manifest.get("task_type") or ""),
            "path": relative,
            "sha256": sha256(raw).hexdigest(),
            "content": content[:limit],
            "truncated": len(content) > limit,
            "human_approval_required": True,
            "canonical_mutated": False,
        }

    def _canonical_candidate_projection(
        self, config_path: Path, next_action: dict[str, Any]
    ) -> dict[str, Any] | None:
        if (
            next_action.get("status") != "agent_task_validated"
            or next_action.get("task_type") != "design_semantic_compile"
        ):
            return None
        root = config_path.parent.resolve()
        manifest = load_manifest(root, str(next_action.get("task_id") or ""))
        source_task_type, document = self._compiled_design_source(manifest)
        relative = str(manifest_output(manifest).get("path") or "")
        delta = (root / relative).resolve()
        try:
            delta.relative_to(root)
        except ValueError as exc:
            raise WorkspaceStudioError("语义编译候选路径逃逸项目") from exc
        if not delta.is_file():
            return None
        raw = delta.read_bytes()
        content = raw.decode("utf-8")
        limit = 500_000
        return {
            "task_id": str(manifest.get("task_id") or ""),
            "source_task_type": source_task_type,
            "document_path": document,
            "path": relative,
            "sha256": sha256(raw).hexdigest(),
            "content": content[:limit],
            "truncated": len(content) > limit,
            "requires_human_apply": True,
            "canonical_mutated": False,
        }

    def _semantic_candidate_projection(
        self, config_path: Path, next_action: dict[str, Any]
    ) -> dict[str, Any] | None:
        if (
            next_action.get("status") != "agent_task_validated"
            or next_action.get("task_type") != "chapter_semantic"
        ):
            return None
        root = config_path.parent.resolve()
        manifest = load_manifest(root, str(next_action.get("task_id") or ""))
        relative = str(manifest_output(manifest).get("path") or "")
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise WorkspaceStudioError("章节语义候选路径逃逸项目") from exc
        if not candidate.is_file():
            return None
        raw = candidate.read_bytes()
        content = raw.decode("utf-8")
        limit = 500_000
        return {
            "task_id": str(manifest.get("task_id") or ""),
            "chapter_number": int(next_action.get("chapter_number") or 0),
            "path": relative,
            "sha256": sha256(raw).hexdigest(),
            "content": content[:limit],
            "truncated": len(content) > limit,
            "requires_human_apply": True,
            "canonical_mutated": False,
        }

    @staticmethod
    def _chapter_context(config_path: Path, chapter_number: int) -> dict[str, Any]:
        root = config_path.parent
        draft = manuscript_chapter_path(root, chapter_number, lane="draft")
        final = manuscript_chapter_path(root, chapter_number, lane="final")
        return {
            "chapter_number": chapter_number,
            "draft": {
                "path": draft.relative_to(root).as_posix(),
                "exists": draft.is_file(),
                "sha256": sha256(draft.read_bytes()).hexdigest() if draft.is_file() else "",
            },
            "final": {
                "path": final.relative_to(root).as_posix(),
                "exists": final.is_file(),
                "sha256": sha256(final.read_bytes()).hexdigest() if final.is_file() else "",
            },
        }

    @staticmethod
    def _chapter_confirmation_projection(
        config_path: Path,
        chapter_number: int,
        next_action: dict[str, Any],
    ) -> dict[str, Any] | None:
        status = str(next_action.get("status") or "")
        if status not in {
            "awaiting_author_voice_approval",
            "awaiting_event_realization",
            "awaiting_reader_promise_evidence",
        }:
            return None
        root = config_path.parent
        final = manuscript_chapter_path(root, chapter_number, lane="final")
        if not final.is_file():
            return None
        kind = {
            "awaiting_author_voice_approval": "author_voice",
            "awaiting_event_realization": "events",
            "awaiting_reader_promise_evidence": "reader_promises",
        }[status]
        projection: dict[str, Any] = {
            "kind": kind,
            "chapter_number": chapter_number,
            "final_path": final.relative_to(root).as_posix(),
            "final_sha256": sha256(final.read_bytes()).hexdigest(),
            "final_text": final.read_text(encoding="utf-8"),
            "canonical_mutated": False,
        }
        if status == "awaiting_author_voice_approval":
            finalization = WorkspaceStudioService._read_json_object(
                final.with_suffix(".finalization.json")
            )
            binding = finalization.get("human_author_revision") or {}
            validation_file = root / str(
                binding.get("validation_file") if isinstance(binding, dict) else ""
            )
            validation = WorkspaceStudioService._read_json_object(validation_file)
            revision_record = WorkspaceStudioService._read_json_object(
                root / str(validation.get("record_file") or "")
            )
            changes = revision_record.get("changes") or []
            change = changes[0] if isinstance(changes, list) and changes else {}
            projection["record"] = {
                "schema": "author_voice_edit_pair_v1",
                "chapter_number": chapter_number,
                "pair_id": f"ch{chapter_number:03d}-voice-{projection['final_sha256'][:10]}",
                "purpose": "",
                "abstract_principle": "",
                "pov_character_id": "",
                "scene_kind": "",
                "before": change.get("before") if isinstance(change, dict) else {},
                "after": change.get("after") if isinstance(change, dict) else {},
                "final_sha256": projection["final_sha256"],
                "human_author_revision_sha256": (
                    sha256(validation_file.read_bytes()).hexdigest()
                    if validation_file.is_file()
                    else ""
                ),
                "replace_pair_id": "",
            }
        elif status == "awaiting_event_realization":
            ledger = WorkspaceStudioService._read_json_object(
                root / "30_state" / "narrative_events" / f"ch{chapter_number:03d}.json"
            )
            projection["events"] = ledger.get("events") or []
            application = WorkspaceStudioService._read_json_object(
                root / "50_workbench" / "event_realizations" / f"ch{chapter_number:03d}.json"
            )
            projection["draft_application"] = application
        else:
            contract, _hash = load_verified_chapter_contract(root, chapter_number)
            projection["actions"] = contract.get("reader_promise_actions") or []
            application = WorkspaceStudioService._read_json_object(
                root / "50_workbench" / "promise_evidence" / f"ch{chapter_number:03d}.json"
            )
            projection["draft_application"] = application
        return projection

    @staticmethod
    def _compiled_design_source(manifest: dict[str, Any]) -> tuple[str, str]:
        task_id_parts = str(manifest.get("task_id") or "").split(":")
        source_task_type = task_id_parts[1] if len(task_id_parts) >= 4 else ""
        documents = [
            path
            for path in manifest_input_paths(manifest)
            if path.startswith("50_workbench/intelligence_candidates/")
            and path.endswith(".candidate.md")
        ]
        if source_task_type not in DESIGN_INTELLIGENCE_TASK_TYPES or len(documents) != 1:
            raise WorkspaceStudioError("语义编译工单未绑定唯一的已批准设计文档")
        return source_task_type, documents[0]

    def _project_id(self, config_path: Path) -> str:
        relative = config_path.resolve().relative_to(self.root).as_posix().casefold()
        return "project_" + sha256(relative.encode("utf-8")).hexdigest()[:20]

    def _require_inside_workspace(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceStudioError("项目路径位于当前工作区之外") from exc

    @staticmethod
    def _require_final_hash(root: Path, chapter_number: int, expected_sha256: str) -> None:
        final = manuscript_chapter_path(root, chapter_number, lane="final")
        digest = sha256(final.read_bytes()).hexdigest() if final.is_file() else ""
        if digest != expected_sha256:
            raise WorkspaceStudioError("正式章节已变化；请刷新后重新确认")

    @staticmethod
    def _read_json_object(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _required_text(payload: dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise WorkspaceStudioError(f"{field} 必须是非空字符串")
        return value.strip()

    @staticmethod
    def _positive_int(payload: dict[str, Any], field: str) -> int:
        value = payload.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise WorkspaceStudioError(f"{field} 必须是正整数")
        return value

    @staticmethod
    def _payload_chapter_number(payload: dict[str, Any]) -> int:
        value = payload.get("chapter_number")
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise WorkspaceStudioError("chapter_number 必须是正整数")
        return value


class WorkspaceStudioHTTPServer(LoopbackHTTPServer):
    def __init__(
        self,
        service: WorkspaceStudioService,
        *,
        port: int,
        initial_project_id: str = "",
        initial_chapter: int | None = None,
        launcher_secret: str = "",
    ) -> None:
        if initial_chapter is not None and not initial_project_id:
            raise WorkspaceStudioError("章节深链接必须同时指定项目")
        if initial_project_id:
            service.project_state(initial_project_id)
        if initial_chapter is not None and initial_chapter <= 0:
            raise WorkspaceStudioError("章节号必须为正整数")
        bootstrap_path = "/"
        if initial_project_id and initial_chapter is not None:
            bootstrap_path = f"/projects/{initial_project_id}/chapters/{initial_chapter}"
        elif initial_project_id:
            bootstrap_path = f"/projects/{initial_project_id}"
        self.launcher_secret = launcher_secret or secrets.token_urlsafe(32)
        super().__init__(
            service=service,
            port=port,
            handler=WorkspaceStudioRequestHandler,
            session_cookie="studio_session",
            csrf_header="X-Studio-CSRF",
            app_label="local novel workspace studio",
            form_action="'self'",
            bootstrap_path=bootstrap_path,
        )


class WorkspaceStudioRequestHandler(LoopbackRequestHandler):
    server: WorkspaceStudioHTTPServer

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._require_host()
            parsed = self._safe_url()
            if parsed.path == "/" and parsed.query:
                self._bootstrap(parsed.query)
                return
            literary_review = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/literary/([-.A-Za-z0-9_]+)/review/([-.A-Za-z0-9_]+)", parsed.path)
            if literary_review:
                self._send_json(HTTPStatus.OK, self.server.service.literary_review_state(
                    *literary_review.groups(), self.headers.get("X-Literary-Reviewer", "")))
                return
            reviewer_page = re.fullmatch(
                r"/projects/(project_[0-9a-f]{20})/literary/review/([-.A-Za-z0-9_]+)/([-.A-Za-z0-9_]+)", parsed.path)
            if reviewer_page:
                from longform_engine.literary_studio import literary_page_html
                self._send_html(literary_page_html(self.server.csrf_token, self.server.csp_nonce))
                return
            if self.headers.get("X-Literary-Reviewer"):
                raise WorkspaceStudioError("评审凭证只能读取匿名材料与本人的评分")
            self._require_session()
            if parsed.path == "/api/workspace":
                self._send_json(HTTPStatus.OK, self.server.service.state())
                return
            if parsed.path == "/api/codex/status":
                self._send_json(HTTPStatus.OK, self.server.service.codex_status())
                return
            literary = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/literary", parsed.path)
            if literary:
                self._send_json(HTTPStatus.OK, self.server.service.literary_state(literary.group(1)))
                return
            literary_export = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/literary/([-.A-Za-z0-9_]+)/export-pack", parsed.path)
            if literary_export:
                from longform_engine.fanfiction_literary_trial import export_literary_pack
                root = self.server.service._config_for_project_id(literary_export.group(1)).parent
                data = export_literary_pack(root, literary_export.group(2))
                self._send_bytes(HTTPStatus.OK, data, "application/zip", {
                    "Content-Disposition": 'attachment; filename="anonymous-reading-pack.zip"',
                })
                return
            literary_page = re.fullmatch(
                r"/projects/(project_[0-9a-f]{20})/literary", parsed.path)
            if literary_page:
                from longform_engine.literary_studio import literary_page_html
                self.server.service._config_for_project_id(literary_page.group(1))
                self._send_html(literary_page_html(self.server.csrf_token, self.server.csp_nonce))
                return
            intent_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/intent", parsed.path
            )
            if intent_match:
                self._send_json(HTTPStatus.OK, self.server.service.chapter_intent_state(
                    intent_match.group(1), int(intent_match.group(2))))
                return
            review_state_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/review/state",
                parsed.path,
            )
            if review_state_match:
                self._send_json(
                    HTTPStatus.OK,
                    self.server.service.review_state(
                        review_state_match.group(1), int(review_state_match.group(2))
                    ),
                )
                return
            project_match = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})", parsed.path)
            if project_match:
                self._send_json(
                    HTTPStatus.OK, self.server.service.project_state(project_match.group(1))
                )
                return
            chapter_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)",
                parsed.path,
            )
            if chapter_match:
                self._send_json(
                    HTTPStatus.OK,
                    self.server.service.chapter_state(
                        chapter_match.group(1), int(chapter_match.group(2))
                    ),
                )
                return
            jobs_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/agent-jobs", parsed.path
            )
            if jobs_match:
                self._send_json(
                    HTTPStatus.OK,
                    {"jobs": self.server.service.list_agent_jobs(jobs_match.group(1))},
                )
                return
            job_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/agent-jobs/(job_[0-9a-f]{24})",
                parsed.path,
            )
            if job_match:
                self._send_json(
                    HTTPStatus.OK,
                    self.server.service.agent_job_status(job_match.group(1), job_match.group(2)),
                )
                return
            chapter_page_match = re.fullmatch(
                r"/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)",
                parsed.path,
            )
            if chapter_page_match:
                project_id = chapter_page_match.group(1)
                chapter_number = int(chapter_page_match.group(2))
                chapter = self.server.service.chapter_state(project_id, chapter_number)
                if (chapter.get("review") or {}).get("available") and parsed.query != "intent=1":
                    prefix = (
                        f"/api/projects/{project_id}/chapters/{chapter_number}/review"
                    )
                    self._send_html(
                        review_page_html(
                            self.server.csrf_token,
                            csp_nonce=self.server.csp_nonce,
                            api_prefix=prefix,
                        )
                    )
                    return
            if _is_workspace_page_route(parsed.path):
                self._send_html(
                    workspace_studio_page_html(self.server.csrf_token, self.server.csp_nonce)
                )
                return
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
        except LocalWebError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except (ConfigError, OSError, ValueError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._require_host()
            parsed = self._safe_url()
            if parsed.path == "/api/instance/bootstrap":
                if not hmac.compare_digest(
                    self.headers.get("X-Studio-Launcher", ""),
                    self.server.launcher_secret,
                ):
                    raise WorkspaceStudioError("launcher credential is invalid")
                body = self._read_json()
                if set(body) != {"path"} or not isinstance(body["path"], str):
                    raise WorkspaceStudioError("launcher bootstrap fields are invalid")
                path = str(body["path"])
                if not _is_workspace_page_route(path):
                    raise WorkspaceStudioError("launcher deep link is not a Studio page")
                self._send_json(
                    HTTPStatus.OK,
                    {"bootstrap_url": self.server.issue_bootstrap(path)},
                )
                return
            self._require_origin()
            personal_review = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/literary/(save|submit)", parsed.path)
            if not personal_review:
                if self.headers.get("X-Literary-Reviewer"):
                    raise WorkspaceStudioError("评审凭证不能执行项目管理操作")
                self._require_session()
            self._require_csrf()
            body = self._read_json()
            literary_action = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/literary/(create|reviewer-add|save|submit|resolve|effort-record)", parsed.path)
            if literary_action:
                self._send_json(HTTPStatus.OK, self.server.service.literary_action(
                    literary_action.group(1), literary_action.group(2), body,
                    token=self.headers.get("X-Literary-Reviewer", "")))
                return
            start_job_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/agent-jobs", parsed.path
            )
            cancel_job_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/agent-jobs/(job_[0-9a-f]{24})/cancel",
                parsed.path,
            )
            review_action_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/review(/[-a-z/]+)",
                parsed.path,
            )
            advance_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/advance",
                parsed.path,
            )
            approve_design_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/approve-design",
                parsed.path,
            )
            apply_design_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/apply-compiled-design",
                parsed.path,
            )
            finalize_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/finalize",
                parsed.path,
            )
            semantic_apply_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/semantic-apply",
                parsed.path,
            )
            close_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/close",
                parsed.path,
            )
            event_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/confirm-events",
                parsed.path,
            )
            promise_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/confirm-promises",
                parsed.path,
            )
            voice_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/approve-author-voice",
                parsed.path,
            )
            intent_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/intent/(create|save|apply)", parsed.path
            )
            if intent_match:
                result = self.server.service.chapter_intent_action(
                    intent_match.group(1), int(intent_match.group(2)), intent_match.group(3), body)
            elif advance_match:
                if body:
                    raise WorkspaceStudioError("安全推进不接受浏览器命令或 Prompt")
                result = self.server.service.advance_production(advance_match.group(1))
            elif approve_design_match:
                result = self.server.service.approve_design_candidate(
                    approve_design_match.group(1), body
                )
            elif apply_design_match:
                result = self.server.service.apply_compiled_design_candidate(
                    apply_design_match.group(1), body
                )
            elif finalize_match:
                if body.get("chapter_number") != int(finalize_match.group(2)):
                    raise WorkspaceStudioError("URL 章节号与请求字段不一致")
                result = self.server.service.finalize_current_chapter(
                    finalize_match.group(1), body
                )
            elif semantic_apply_match:
                if body.get("chapter_number") != int(semantic_apply_match.group(2)):
                    raise WorkspaceStudioError("URL 章节号与请求字段不一致")
                result = self.server.service.apply_chapter_semantic_candidate(
                    semantic_apply_match.group(1), body
                )
            elif close_match:
                if body.get("chapter_number") != int(close_match.group(2)):
                    raise WorkspaceStudioError("URL 章节号与请求字段不一致")
                result = self.server.service.close_current_chapter(
                    close_match.group(1), body
                )
            elif event_match:
                if body.get("chapter_number") != int(event_match.group(2)):
                    raise WorkspaceStudioError("URL 章节号与请求字段不一致")
                result = self.server.service.confirm_chapter_events(
                    event_match.group(1), body
                )
            elif promise_match:
                if body.get("chapter_number") != int(promise_match.group(2)):
                    raise WorkspaceStudioError("URL 章节号与请求字段不一致")
                result = self.server.service.confirm_reader_promises(
                    promise_match.group(1), body
                )
            elif voice_match:
                if body.get("chapter_number") != int(voice_match.group(2)):
                    raise WorkspaceStudioError("URL 章节号与请求字段不一致")
                result = self.server.service.approve_author_voice_pair(
                    voice_match.group(1), body
                )
            elif review_action_match and review_action_match.group(3) in REVIEW_ACTION_FIELDS:
                result = self.server.service.review_action(
                    review_action_match.group(1),
                    int(review_action_match.group(2)),
                    review_action_match.group(3),
                    body,
                )
            elif start_job_match:
                result = self.server.service.start_agent_job(start_job_match.group(1), body)
            elif cancel_job_match:
                if body:
                    raise WorkspaceStudioError("取消 Agent 任务不接受额外参数")
                result = self.server.service.cancel_agent_job(
                    cancel_job_match.group(1), cancel_job_match.group(2)
                )
            elif parsed.path == "/api/projects":
                result = self.server.service.create_project(body)
            elif parsed.path == "/api/projects/import":
                if set(body) != {"config_path"}:
                    raise WorkspaceStudioError("导入请求字段无效")
                result = self.server.service.import_project(str(body["config_path"]))
            else:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
                return
            self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
        except LocalWebError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except (ConfigError, OSError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})


def _is_workspace_page_route(path: str) -> bool:
    return bool(
        path in {"/", "/projects/new"}
        or re.fullmatch(
            r"/projects/project_[0-9a-f]{20}(?:/(?:design|sources|fanfiction|planning|knowledge|publication|literary|recovery))?",
            path,
        )
        or re.fullmatch(r"/projects/project_[0-9a-f]{20}/chapters/[1-9][0-9]*", path)
    )


def workspace_studio_page_html(csrf_token: str, csp_nonce: str) -> str:
    return _WORKSPACE_PAGE.replace(
        "__CSRF_TOKEN__", html.escape(csrf_token, quote=True)
    ).replace("workspacenonce", html.escape(csp_nonce, quote=True))


_WORKSPACE_PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><title>小说创作工作台</title>
<style nonce="workspacenonce">
:root{font-family:"Microsoft YaHei","PingFang SC",system-ui,sans-serif;color:#28231f;background:#f4f0e7;line-height:1.6;--ink:#28231f;--paper:#fffdf9;--line:#ded4c5;--accent:#8b4a22;--accent-dark:#653216;--soft:#eee5d7;--ok:#226b4d;--warn:#9b5b12;--danger:#a6382f;--shadow:0 12px 34px rgba(55,42,29,.09)}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% 0,#eadcc5 0,transparent 28rem),#f4f0e7}a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}.shell-header{background:#2f2822;color:#fff;padding:22px 28px;border-bottom:4px solid #b3733f}.header-inner{max-width:1220px;margin:auto;display:flex;align-items:center;justify-content:space-between;gap:24px}.brand h1{font-family:Georgia,"STKaiti",serif;font-size:26px;margin:0}.brand p{margin:3px 0 0;color:#d9cec2;font-size:13px}.top-nav{display:flex;gap:10px;flex-wrap:wrap}.top-nav a{color:#fff;border:1px solid #6b5c50;border-radius:999px;padding:7px 13px}.top-nav a:hover{background:#443a32;text-decoration:none}main{max-width:1220px;margin:auto;padding:28px}.hero{display:grid;grid-template-columns:1.7fr 1fr;gap:22px;margin-bottom:24px}.panel,.card{background:var(--paper);border:1px solid var(--line);border-radius:16px;box-shadow:var(--shadow)}.panel{padding:24px}.card{padding:20px}.card h2,.card h3,.panel h2{margin-top:0}.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);font-weight:700}.lead{font-size:17px;color:#4b423b}.muted{color:#756b62}.small{font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}.project-card{display:flex;flex-direction:column;min-height:260px}.project-card .project-actions{margin-top:auto}.badge{display:inline-flex;align-items:center;border-radius:999px;padding:3px 9px;font-size:12px;font-weight:700;background:var(--soft);color:#5d4a39}.badge.ok{background:#dcece4;color:var(--ok)}.badge.warn{background:#f4e6ca;color:var(--warn)}.badge.danger{background:#f5dcda;color:var(--danger)}.action-box{border-left:5px solid var(--accent);padding:18px 20px;background:#fffaf1;border-radius:10px;margin:18px 0}.action-box h3{margin:0 0 6px}.button-row{display:flex;gap:10px;flex-wrap:wrap;margin-top:16px}button,.button{appearance:none;border:0;border-radius:9px;padding:10px 16px;font:inherit;font-weight:700;cursor:pointer;background:var(--accent);color:#fff;display:inline-block}button:hover,.button:hover{background:var(--accent-dark);text-decoration:none}button.secondary,.button.secondary{background:#e9dfd1;color:#553d2b}button.ghost{background:transparent;color:var(--accent);border:1px solid #b99679}button.danger{background:var(--danger)}button:disabled{cursor:not-allowed;opacity:.5}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.field{display:flex;flex-direction:column;gap:6px}.field.full{grid-column:1/-1}label{font-weight:700}input,select,textarea{width:100%;border:1px solid #cfc1b0;border-radius:9px;background:#fff;padding:10px 12px;font:inherit;color:var(--ink)}textarea{min-height:94px;resize:vertical}input:focus,select:focus,textarea:focus{outline:3px solid rgba(139,74,34,.16);border-color:var(--accent)}.mode-picker{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px}.mode-card{border:1px solid var(--line);background:#fff;padding:14px;color:var(--ink);text-align:left}.mode-card.active{outline:3px solid rgba(139,74,34,.18);border-color:var(--accent);background:#fff8ee}.mode-card strong{display:block}.source-row{border:1px dashed #bba88e;border-radius:12px;padding:16px;margin:12px 0;background:#fcf8f1}.notice{border-radius:10px;padding:13px 15px;background:#edf4ef;color:#285741}.notice.warn{background:#fff0d5;color:#704710}.notice.danger{background:#f9e1df;color:#772d28}.status-line{min-height:24px;margin:12px 0}.project-layout{display:grid;grid-template-columns:220px minmax(0,1fr);gap:22px}.side-nav{align-self:start;position:sticky;top:20px}.side-nav a{display:block;padding:9px 11px;border-radius:8px}.side-nav a.active{background:#eadfce;font-weight:700}.candidate{background:#25211e;color:#f6eee4;border-radius:12px;padding:18px;max-height:520px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-family:"Microsoft YaHei",sans-serif}.job{border:1px solid var(--line);border-radius:10px;padding:13px;margin-top:10px}.job.running{border-color:#d69d50;background:#fff8ea}.kv{display:grid;grid-template-columns:150px 1fr;gap:6px 14px}.kv dt{color:#74685e}.kv dd{margin:0;overflow-wrap:anywhere}.divider{border:0;border-top:1px solid var(--line);margin:24px 0}.empty{text-align:center;padding:44px}.loading{padding:46px;text-align:center;color:#756b62}@media(max-width:800px){.hero,.project-layout{grid-template-columns:1fr}.form-grid{grid-template-columns:1fr}.field.full{grid-column:auto}.mode-picker{grid-template-columns:1fr}.side-nav{position:static}.header-inner{align-items:flex-start;flex-direction:column}main{padding:18px}}
.spaced-top{margin-top:22px}.spaced-bottom{margin-bottom:22px}.single-column{grid-template-columns:1fr}.compact-top{margin-top:14px}</style></head>
<body><header class="shell-header"><div class="header-inner"><div class="brand"><h1>小说创作工作台</h1><p id="workspace">本机安全工作区</p></div><nav class="top-nav"><a href="/">项目首页</a><a href="/projects/new">创建小说</a></nav></div></header><main><div id="app" class="loading">正在载入工作区……</div></main>
<script nonce="workspacenonce">
const csrf="__CSRF_TOKEN__";const app=document.getElementById("app");const currentPath=location.pathname;
const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const modeName=v=>({original:"原创小说",fanfiction:"同人小说"}[v]||v||"未知类型");
const statusName=v=>({ready_for_intelligence_task:"准备创建智能设计工单",agent_task_awaiting_agent:"等待 Codex",agent_task_submitted:"等待引擎校验",agent_task_validated:"候选已校验，等待人类决定",agent_task_approved:"设计已批准，等待语义编译",agent_task_invalid:"Agent 输出未通过",need_human:"等待人类处理",ready_for_continue_write:"可以准备下一章",awaiting_agent_draft:"等待 Codex 写章",awaiting_gate:"等待门禁检查",gate_failed:"章节门禁未通过",awaiting_finalize:"等待人工定稿",awaiting_event_realization:"等待人工确认事件实现",awaiting_reader_promise_evidence:"等待人工确认读者承诺",awaiting_author_voice_approval:"等待人工作者声音样本",awaiting_chapter_close:"等待人工关闭章节",planning_refresh_required:"需要刷新滚动规划",project_busy:"项目正在处理",project_recovery_required:"项目需要恢复",book_completed:"全书已完成"}[v]||v||"尚未建立状态");
const badgeClass=v=>/invalid|failed|recovery/.test(v||"")?"danger":/validated|completed|ready_for_continue|book_completed/.test(v||"")?"ok":"warn";
async function request(path,options={}){const r=await fetch(path,{credentials:"same-origin",...options});let d={};try{d=await r.json()}catch{d={error:"服务器返回了非 JSON 响应"}}if(!r.ok)throw new Error(d.error||`请求失败（${r.status}）`);return d}
const get=path=>request(path);const post=(path,payload)=>request(path,{method:"POST",headers:{"Content-Type":"application/json","X-Studio-CSRF":csrf},body:JSON.stringify(payload)});
function setWorkspace(value){document.getElementById("workspace").textContent=value||"本机安全工作区"}
function showError(error){app.className="";app.innerHTML=`<div class="notice danger"><strong>无法完成操作</strong><br>${esc(error.message||error)}</div>`}
function actionSummary(action){return action?.human_summary||"引擎尚未给出下一项生产动作。"}
async function renderHome(){const state=await get("/api/workspace");setWorkspace(state.workspace);const codex=state.codex||{};const cards=state.projects.map(p=>{const a=p.next_action||{};return `<article class="card project-card"><div><span class="badge">${esc(modeName(p.creation_mode))}</span> <span class="badge ${badgeClass(a.status)}">${esc(statusName(a.status))}</span></div><h2>${esc(p.title||p.relative_config)}</h2><p class="muted">${esc(p.target_platform||"")} · ${esc(p.continuity_mode||"")}</p><p>${esc(actionSummary(a))}</p><div class="project-actions"><a class="button" href="/projects/${esc(p.id)}">继续创作</a></div></article>`}).join("");app.className="";app.innerHTML=`<section class="hero"><div class="panel"><div class="eyebrow">Workspace Studio</div><h2>从一本书的当前安全动作继续</h2><p class="lead">原创小说与同人小说各自拥有独立 Canon、工单、章节状态和审计记录。页面只提交结构化动作，不接受任意命令。</p><div class="button-row"><a class="button" href="/projects/new">＋ 创建小说</a></div></div><div class="panel"><h3>本机能力</h3><dl class="kv"><dt>Longform Engine</dt><dd>${esc(state.engine_version)}</dd><dt>Codex CLI</dt><dd><span class="badge ${codex.available?"ok":"danger"}">${codex.available?"已登录可用":"暂不可用"}</span></dd><dt>项目数量</dt><dd>${state.project_count}</dd></dl><p class="small muted">即使 Codex 不可用，仍可查看、编辑和审阅项目。</p></div></section><div class="grid">${cards||'<section class="card empty"><h2>还没有小说项目</h2><p class="muted">创建原创小说，或从《咒术回战》等作品开始同人项目。</p><a class="button" href="/projects/new">开始创建</a></section>'}</div><section class="panel spaced-top"><h3>导入工作区内的已有项目</h3><form id="import-form" class="form-grid"><div class="field full"><label for="import-path">project.yaml 绝对路径</label><input id="import-path" name="config_path" required placeholder="D:\\NovelProjects\\my-book\\project.yaml"><span class="small muted">只允许导入当前工作区目录内部的项目。</span></div><div><button>导入项目</button></div></form><div id="import-status" class="status-line"></div></section>`;document.getElementById("import-form").addEventListener("submit",importProject)}
async function importProject(event){event.preventDefault();const box=document.getElementById("import-status");box.textContent="正在校验并导入……";try{const d=await post("/api/projects/import",{config_path:new FormData(event.target).get("config_path")});location.href=`/projects/${d.result.project.id}`}catch(e){box.innerHTML=`<span class="notice danger">${esc(e.message)}</span>`}}
let creationMode="original";let sourceCounter=0;
function sourceRow(){sourceCounter+=1;return `<div class="source-row" data-source-row><div class="form-grid"><div class="field"><label>原作稳定 ID</label><input data-source="source_id" value="${sourceCounter===1?"jujutsu_kaisen":`source_${sourceCounter}`}" required></div><div class="field"><label>原作名称</label><input data-source="title" value="${sourceCounter===1?"咒术回战":""}" required></div><div class="field"><label>作者 / 创作者</label><input data-source="creator" value="${sourceCounter===1?"芥见下下":""}" required></div><div class="field"><label>Canon 截止点</label><input data-source="canon_cutoff" placeholder="例如：动画第二季 / 单行本第 16 卷" required></div><div class="field"><label>原著权利状态</label><select data-source="rights_status"><option value="unverified">未核验（推荐）</option><option value="user_claimed_authorized">用户声明已授权</option><option value="platform_permitted_claimed">用户声明平台允许</option><option value="public_domain_claimed">用户声明公版</option></select></div><div class="field"><label>资料留存策略</label><select data-source="retention_mode"><option value="metadata_only">只留元数据（推荐）</option><option value="short_evidence">短证据片段</option><option value="full_text">完整文本（仅明确授权）</option></select></div><div class="field full"><label>允许借用的元素</label><input data-source="allowed_elements" value="人物、世界观、能力规则"></div><div class="field"><label>发布平台政策链接</label><input data-source="platform_policy_url" type="url" placeholder="https://"></div><div class="field"><label><input data-source="commercial_intent" type="checkbox"> 计划商业化使用</label></div></div></div>`}
function setCreationMode(mode){creationMode=mode;document.querySelectorAll("[data-mode]").forEach(b=>b.classList.toggle("active",b.dataset.mode===mode));const fan=document.getElementById("fanfiction-fields");fan.hidden=mode==="original";document.getElementById("continuity").disabled=mode==="original";document.getElementById("rights-ack").required=mode!=="original"}
function renderCreativeRequirements(){const contracts=window.creativeRequirements[document.getElementById("continuity").value],names={oc_si_progression:"原创或穿越主角",canon_character_centered:"原著人物中心",hybrid:"混合路线"};document.getElementById("creative-requirements-body").innerHTML=Object.entries(contracts).map(([route,c])=>`<h4>${names[route]}</h4><p>${c.story_engine_claim_types.map(esc).join("；")}</p>`).join("")+`<p class="muted">${contracts.hybrid.creative_guidance.map(esc).join(" ")}</p>`}
async function renderNewProject(){const state=await get("/api/workspace");window.creativeRequirements=state.fanfiction_creative_requirements;setWorkspace(state.workspace);app.className="";app.innerHTML=`<section class="panel"><div class="eyebrow">New book</div><h2>创建一本新的长篇小说</h2><p class="muted">创建完成后自动建立工程目录并完成 open-book；不会自动运行 Codex，也不会写入后续 Canon。</p><div class="mode-picker"><button type="button" class="mode-card active" data-mode="original"><strong>创建原创小说</strong><span>从创意方案、故事设计和总纲开始</span></button><button type="button" class="mode-card" data-mode="fanfiction"><strong>创建同人小说</strong><span>先建立原作来源、截止点与连续性</span></button><button type="button" class="mode-card" data-mode="crossover"><strong>创建跨作品同人</strong><span>使用多个独立原作资料源</span></button></div><form id="create-form"><div class="form-grid"><div class="field"><label>书名</label><input name="title" required placeholder="我的长篇小说"></div><div class="field"><label>项目 slug</label><input name="slug" required pattern="[a-z0-9][a-z0-9_\\-]{1,79}" placeholder="my-longform"></div><div class="field"><label>目标平台</label><select name="target_platform"><option value="qidian">起点中文网</option><option value="fanqie">番茄小说</option><option value="general_cn">通用中文长篇</option></select></div><div class="field"><label>目标读者</label><input name="target_audience" value="起点中文网长篇读者" required></div><div class="field"><label>写作视角与风格</label><input name="writing_style" value="第三人称有限视角" required></div><div class="field"><label>自动化等级</label><select name="automation_level"><option value="human_approved_agent_workflow">人类批准的 Agent 工作流</option></select></div><div class="field full"><label>核心阅读承诺</label><textarea name="core_promise" required>人物选择带来可感知的变化，并持续展开本作的阅读价值。</textarea></div><div class="field full"><label>全书核心问题</label><textarea name="main_question" required>人物将如何面对核心问题，彼此关系与处境会怎样变化？</textarea></div><div class="field full"><label>结局方向</label><textarea name="ending_direction" required>终局完成主线收束，兑现核心承诺并支付明确代价。</textarea></div><div class="field full"><label>禁止的阅读体验（每行一项）</label><textarea name="forbidden_experience" required>连续多章无推进\n人物无理由降智\n事实、战力或时间线无依据漂移</textarea></div><div class="field"><label>目标总字数</label><input name="target_total_characters" type="number" min="10000" value="1000000" required></div><div class="field"><label>单章目标字数</label><input name="chapter_target_characters" type="number" min="500" value="3000" required></div><div class="field"><label>单卷目标字数</label><input name="volume_target_characters" type="number" min="10000" value="200000" required></div><div class="field"><label>详细规划跨度（章）</label><input name="planning_horizon" type="number" min="3" value="20" required></div><div class="field"><label>规划补充阈值（章）</label><input name="refill_threshold" type="number" min="1" value="8" required></div></div><section id="fanfiction-fields" hidden><hr class="divider"><h3>同人连续性与原著资料声明</h3><div class="notice warn">同人创作不等于获得商业授权。权利状态和资料留存策略必须显式选择；默认使用“未核验 + 只留元数据”。</div><div class="field compact-top"><label for="continuity">连续性模式</label><select id="continuity" name="continuity_mode" disabled><option value="canon_divergent">Canon 分歧</option><option value="canon_compliant">Canon 兼容</option><option value="alternate_universe">平行世界 / AU</option><option value="continuation">原作续篇</option><option value="prequel">原作前传</option><option value="crossover">跨作品</option></select></div><details id="creative-requirements"><summary>当前写法需要明确的创作问题</summary><div id="creative-requirements-body"></div></details><div id="source-list">${sourceRow()}</div><button id="add-source" type="button" class="secondary">＋ 添加另一个原作</button><p><label><input id="rights-ack" type="checkbox"> 我理解同人创作的权利、平台政策和资料留存风险，并确认这些声明由我提供。</label></p></section><hr class="divider"><div class="button-row"><button id="create-submit">确认创建并开书</button><a class="button secondary" href="/">取消</a></div><div id="create-status" class="status-line"></div></form></section>`;document.querySelectorAll("[data-mode]").forEach(b=>b.addEventListener("click",()=>setCreationMode(b.dataset.mode==="crossover"?"fanfiction":b.dataset.mode)));document.querySelector('[data-mode="crossover"]').addEventListener("click",()=>{document.getElementById("continuity").value="crossover";renderCreativeRequirements();if(document.querySelectorAll("[data-source-row]").length<2)document.getElementById("source-list").insertAdjacentHTML("beforeend",sourceRow())});document.getElementById("add-source").addEventListener("click",()=>document.getElementById("source-list").insertAdjacentHTML("beforeend",sourceRow()));document.getElementById("continuity").addEventListener("change",renderCreativeRequirements);renderCreativeRequirements();document.getElementById("create-form").addEventListener("submit",createProject)}
function sourceValues(){return [...document.querySelectorAll("[data-source-row]")].map(row=>{const read=name=>row.querySelector(`[data-source="${name}"]`);return {source_id:read("source_id").value.trim(),title:read("title").value.trim(),creator:read("creator").value.trim(),canon_cutoff:read("canon_cutoff").value.trim(),rights_status:read("rights_status").value,retention_mode:read("retention_mode").value,commercial_intent:read("commercial_intent").checked,allowed_elements:read("allowed_elements").value.split(/[、,，\n]/).map(v=>v.trim()).filter(Boolean),platform_policy_url:read("platform_policy_url").value.trim()}})}
async function createProject(event){event.preventDefault();const f=new FormData(event.target),status=document.getElementById("create-status"),button=document.getElementById("create-submit");button.disabled=true;status.textContent="正在创建目录、校验配置并完成开书……";const fan=creationMode!=="original";const payload={title:f.get("title").trim(),slug:f.get("slug").trim(),creation_mode:fan?"fanfiction":"original",continuity_mode:fan?f.get("continuity_mode"):"canon_compliant",target_platform:f.get("target_platform"),target_audience:f.get("target_audience").trim(),writing_style:f.get("writing_style").trim(),core_promise:f.get("core_promise").trim(),main_question:f.get("main_question").trim(),ending_direction:f.get("ending_direction").trim(),forbidden_experience:f.get("forbidden_experience").split(/\n/).map(v=>v.trim()).filter(Boolean),automation_level:f.get("automation_level"),target_total_characters:Number(f.get("target_total_characters")),chapter_target_characters:Number(f.get("chapter_target_characters")),volume_target_characters:Number(f.get("volume_target_characters")),planning_horizon:Number(f.get("planning_horizon")),refill_threshold:Number(f.get("refill_threshold")),rights_risk_acknowledged:fan&&document.getElementById("rights-ack").checked,sources:fan?sourceValues():[]};try{const d=await post("/api/projects",payload);location.href=`/projects/${d.result.project.id}`}catch(e){status.innerHTML=`<span class="notice danger">${esc(e.message)}</span>`;button.disabled=false}}
const sectionText={design:["开书与设计","管理 Book Ideation、故事发动机、人物设计与总纲候选。"],sources:["原著资料中心","核对原作身份、Canon 截止点、证据覆盖和资料缺口。"],fanfiction:["同人 Canon 与路线","管理连续性模式、原作知识边界和同人路线审查。"],planning:["总纲与滚动规划","查看卷纲、三章 firm 合同和后续滚动窗口。"],knowledge:["故事知识库","查看人物状态、事件、图谱、伏笔和读者承诺。"],publication:["发布中心","准备起点发布材料；发布动作不会因打开页面自动发生。"],recovery:["恢复与审计","检查锁、事务、Agent 工单和失败恢复记录。"]};
function projectNav(state,active){return `<aside class="card side-nav"><a href="${state.navigation.dashboard}" class="${active==="dashboard"?"active":""}">项目驾驶舱</a><a href="${state.navigation.design}" class="${active==="design"?"active":""}">开书与设计</a><a href="${state.navigation.sources}" class="${active==="sources"?"active":""}">原著资料</a><a href="${state.navigation.fanfiction}" class="${active==="fanfiction"?"active":""}">同人 Canon</a><a href="${state.navigation.planning}" class="${active==="planning"?"active":""}">总纲与规划</a><a href="${state.navigation.knowledge}" class="${active==="knowledge"?"active":""}">知识与伏笔</a><a href="${state.navigation.publication}" class="${active==="publication"?"active":""}">发布中心</a><a href="${state.navigation.literary}">质量评测</a><a href="${state.navigation.recovery}" class="${active==="recovery"?"active":""}">恢复与审计</a></aside>`}
function jobsHtml(jobs){if(!jobs?.length)return '<p class="muted">尚无 Codex 工单。</p>';return jobs.slice().reverse().slice(0,5).map(j=>`<div class="job ${["starting","running","cancelling"].includes(j.status)?"running":""}"><strong>${esc(j.task_type||"Codex 任务")}</strong> <span class="badge ${badgeClass(j.status)}">${esc(j.status)}</span><div class="small muted">${esc(j.job_id)} · ${esc(j.started_at||j.created_at||"")}</div>${["starting","running"].includes(j.status)?`<button class="danger compact-top" data-cancel-job="${esc(j.job_id)}">取消任务</button>`:""}${j.error?`<p class="notice danger">${esc(j.error)}</p>`:""}</div>`).join("")}
function actionControls(state){const a=state.next_action||{},codex=window.workspaceState?.codex||{},ctx=state.chapter_context||{};if(a.status==="agent_task_awaiting_agent")return `<button data-start-job="${esc(a.task_id)}" ${codex.available?"":"disabled"}>交给 Codex</button>${codex.available?"":'<span class="notice danger small">Codex CLI 未安装或未登录，暂不能运行 Agent。</span>'}`;if(a.status==="agent_task_approved")return '<button data-advance>创建语义编译工单</button>';if(a.status==="awaiting_finalize")return `<label><input id="finalize-check" type="checkbox"> 我确认把当前精确草稿定稿为正式章节</label><button class="danger" data-finalize="${a.chapter_number}" data-sha="${esc(ctx.draft?.sha256||"")}">明确 finalize 第 ${a.chapter_number} 章</button>`;if(a.status==="awaiting_chapter_close")return `<label><input id="close-check" type="checkbox"> 我确认事件、承诺和语义状态均已核对</label><button class="danger" data-close="${a.chapter_number}" data-sha="${esc(ctx.final?.sha256||"")}">明确 close 第 ${a.chapter_number} 章</button>`;if(["ready_for_intelligence_task","ready_for_continue_write","ready_for_chapter_semantic_task","ready_for_reader_payoff_task","ready_for_editorial_review","awaiting_gate","agent_task_submitted"].includes(a.status))return '<button data-advance>执行安全准备步骤</button>';return ''}
function candidateHtml(state){const c=state.candidate;if(!c)return "";return `<section class="card"><div class="eyebrow">Human decision</div><h3>经校验的设计候选</h3><p class="small muted">${esc(c.path)} · SHA-256 ${esc(c.sha256)}</p><pre class="candidate">${esc(c.content)}</pre>${c.truncated?'<p class="notice warn">页面预览已截断，请在项目文件中查看完整内容。</p>':""}<p><label><input id="approve-check" type="checkbox"> 我已阅读当前候选，并确认批准的是这个精确哈希版本。</label></p><button data-approve-task="${esc(c.task_id)}" data-sha="${esc(c.sha256)}">批准当前候选</button><p class="small muted">批准只记录人类设计决定，不会直接写入 Canon；后续语义编译与 Canon apply 仍是独立步骤。</p></section>`}
function canonicalCandidateHtml(state){const c=state.canonical_candidate;if(!c)return "";return `<section class="card"><div class="eyebrow">Canonical mutation</div><h3>语义编译候选已通过校验</h3><p>下面是将要写入正式故事状态的结构化 delta。此动作会改变 Canon，必须单独确认。</p><p class="small muted">来源设计：${esc(c.document_path)}<br>Delta：${esc(c.path)} · SHA-256 ${esc(c.sha256)}</p><pre class="candidate">${esc(c.content)}</pre><p><label><input id="canonical-check" type="checkbox"> 我已核对这个精确 delta，并明确授权本次写入 Canon。</label></p><button class="danger" data-apply-canonical="${esc(c.task_id)}" data-sha="${esc(c.sha256)}">确认写入 Canon</button></section>`}
function semanticCandidateHtml(state){const c=state.semantic_candidate;if(!c)return "";return `<section class="card"><div class="eyebrow">Chapter semantic apply</div><h3>第 ${c.chapter_number} 章语义候选已校验</h3><p class="small muted">${esc(c.path)} · SHA-256 ${esc(c.sha256)}</p><pre class="candidate">${esc(c.content)}</pre><p><label><input id="semantic-check" type="checkbox"> 我已核对语义抽取，并明确授权更新事件、人物、图谱、伏笔及检索状态。</label></p><button class="danger" data-semantic-apply="${esc(c.task_id)}" data-chapter="${c.chapter_number}" data-sha="${esc(c.sha256)}">明确 semantic apply</button></section>`}
function chapterConfirmationHtml(state){const c=state.chapter_confirmation;if(!c)return "";if(c.kind==="author_voice")return `<section class="card"><div class="eyebrow">Human author voice</div><h3>从真实人工改稿中批准一个作者声音样本</h3><p>before / after 必须与本章已验证的人工修订重叠。请补充该修改的用途和可迁移的抽象原则；样本只来自真实人类改稿。</p><textarea id="voice-record">${esc(JSON.stringify(c.record,null,2))}</textarea><p><label><input id="voice-check" type="checkbox"> 我确认这是自己的真实改稿，并批准它进入作者声音样本库。</label></p><button class="danger" data-approve-voice="${c.chapter_number}" data-sha="${esc(c.final_sha256)}">批准作者声音样本</button></section>`;if(c.kind==="events"){const observations=c.draft_application?.observations||c.events.map(e=>({event_id:e.event_id,state:"",evidence:null,semantic_reason:""}));const discovered=c.draft_application?.discovered_causal_nodes||[];const divergences=c.draft_application?.realized_major_divergences||[];return `<section class="card"><div class="eyebrow">Human event confirmation</div><h3>逐项确认叙事事件</h3><p>每个规划事件都必须选择 realized、partially_realized、deferred、cancelled 或 contradicted。需要正文证据的状态必须提供精确 Unicode code-point start/end、excerpt 和语义理由。</p><pre class="candidate">${esc(c.final_text)}</pre><label>事件 observations</label><textarea id="event-observations">${esc(JSON.stringify(observations,null,2))}</textarea><label>新增因果节点（非空会转入重定向审批）</label><textarea id="event-discovered">${esc(JSON.stringify(discovered,null,2))}</textarea><label>同人重大分歧声明</label><textarea id="event-divergences">${esc(JSON.stringify(divergences,null,2))}</textarea><p><label><input id="event-check" type="checkbox"> 我逐项核对了事件状态及其精确正文证据。</label></p><button class="danger" data-confirm-events="${c.chapter_number}" data-sha="${esc(c.final_sha256)}">确认并 apply 事件状态</button></section>`}const evidence=c.draft_application?.evidence||[];return `<section class="card"><div class="eyebrow">Reader promise evidence</div><h3>确认读者承诺动作</h3><p>章节合同中的动作不可由浏览器改写。对 setup、advance、partial_payoff、payoff 提供精确正文 span；defer 动作不提供 evidence，其理由来自已批准章节合同。</p><label>已批准的承诺动作（只读）</label><pre class="candidate">${esc(JSON.stringify(c.actions,null,2))}</pre><label>正文 evidence</label><textarea id="promise-evidence">${esc(JSON.stringify(evidence,null,2))}</textarea><pre class="candidate">${esc(c.final_text)}</pre><p><label><input id="promise-check" type="checkbox"> 我核对了承诺动作与精确正文证据。</label></p><button class="danger" data-confirm-promises="${c.chapter_number}" data-sha="${esc(c.final_sha256)}">确认并 apply 承诺证据</button></section>`}
function intentFormHtml(state){const c=state.candidate;if(!c)return `<section class="card"><h3>本章人工创作意图</h3><button id="intent-create">建立空白表单</button></section>`;const labels={story_intent:"故事意图",key_character_choice:"人物关键选择",emotional_truth:"情绪真相",pov_voice_intent:"视角与声音意图"};return `<section class="card"><h3>本章人工创作意图</h3><p>人物和场景选择用于匹配表达资料与声例。人物留空表示不按 POV 筛选；场景留空表示不按场景筛选。</p><form id="intent-form">${Object.entries(labels).map(([key,label])=>`<label>${label}<textarea name="${key}">${esc(c[key])}</textarea></label>`).join("")}<label>视角人物（可多选）<select id="intent-pov" multiple>${state.characters.map(ch=>`<option value="${esc(ch.id)}" ${(c.expression_focus.pov_character_ids||[]).includes(ch.id)?"selected":""}>${esc(ch.name)}${ch.participates?"（本章参与）":""}</option>`).join("")}</select></label><button type="button" id="intent-clear-pov" class="secondary">清空视角筛选</button><label>主要场景标签（可选）<input id="intent-scene" value="${esc(c.expression_focus.scene_kind)}"></label><label>保护项（每行一项）<textarea id="intent-protected">${esc(c.protected_items.join("\n"))}</textarea></label><div class="button-row"><button type="submit">保存并校验草稿</button><button type="button" id="intent-apply" class="danger" disabled>确认并应用已保存意图</button></div><p><label><input type="checkbox" id="intent-confirm"> 以上内容由我填写，我确认将已保存的意图用于本章创作。</label></p><pre id="intent-result" aria-live="polite"></pre></form></section>`}
function bindIntentForm(projectId,state){let dirty=false;const form=document.getElementById("intent-form"),applyButton=document.getElementById("intent-apply"),confirm=document.getElementById("intent-confirm");confirm?.addEventListener("change",()=>{applyButton.disabled=dirty||!state.validation?.ok||!confirm.checked});const markDirty=()=>{dirty=true;document.getElementById("intent-apply").disabled=true;document.getElementById("intent-confirm").checked=false};form?.addEventListener("input",e=>{if(e.target.id!=="intent-confirm")markDirty()});form?.addEventListener("change",e=>{if(e.target.id!=="intent-confirm")markDirty()});const base=`/api/projects/${projectId}/chapters/${state.chapter_number}/intent`;document.getElementById("intent-create")?.addEventListener("click",async()=>{try{await post(base+"/create",{});await renderProject(projectId,"dashboard",state.chapter_number)}catch(e){showError(e)}});document.getElementById("intent-clear-pov")?.addEventListener("click",()=>{for(const o of document.getElementById("intent-pov").options)o.selected=false;markDirty()});document.getElementById("intent-form")?.addEventListener("submit",async e=>{e.preventDefault();const f=Object.fromEntries(new FormData(e.target));f.expression_focus={pov_character_ids:Array.from(document.getElementById("intent-pov").selectedOptions,o=>o.value),scene_kind:document.getElementById("intent-scene").value};f.protected_items=document.getElementById("intent-protected").value.split("\n").map(x=>x.trim()).filter(Boolean);try{const res=await post(base+"/save",{expected_sha256:state.candidate_sha256,fields:f});Object.assign(state,res.result);dirty=false;document.getElementById("intent-apply").disabled=true;document.getElementById("intent-result").textContent=state.validation.ok?"草稿已保存，校验通过。":state.validation.errors.join("\n");document.getElementById("intent-confirm").checked=false}catch(e){showError(e)}});document.getElementById("intent-apply")?.addEventListener("click",async()=>{try{if(dirty)throw Error("请先保存并校验当前意图。");await post(base+"/apply",{expected_sha256:state.candidate_sha256,acknowledge_human_decision:document.getElementById("intent-confirm").checked});await renderProject(projectId,"dashboard",state.chapter_number)}catch(e){showError(e)}})}
function dashboardHtml(state){const a=state.next_action||{};return `<section class="card"><div class="eyebrow">Current safe action</div><div class="action-box"><span class="badge ${badgeClass(a.status)}">${esc(statusName(a.status))}</span><h3>${esc(a.task_type||"当前生产状态")}</h3><p>${esc(actionSummary(a))}</p>${a.chapter_number?`<p><a href="/projects/${esc(state.project.id)}/chapters/${a.chapter_number}">打开第 ${a.chapter_number} 章工作台</a> · <a href="/projects/${esc(state.project.id)}/chapters/${a.chapter_number}?intent=1">编辑本章人工意图</a></p>`:""}${a.blocked_by&&a.blocked_by!=="none"?`<p class="small muted">阻断：${esc(a.blocked_by)}</p>`:""}<div class="button-row">${actionControls(state)}</div></div><div id="action-status" class="status-line"></div></section>${candidateHtml(state)}${canonicalCandidateHtml(state)}${semanticCandidateHtml(state)}${chapterConfirmationHtml(state)}<section class="card"><h3>Codex 工单</h3><p class="small muted">浏览器不会提交任意 Prompt；后端只接收当前 Manifest 的 task_id，并把声明输入复制到隔离 staging。</p><div id="job-list">${jobsHtml(state.agent_jobs)}</div></section>`}
async function renderProject(projectId,section="dashboard",chapter=null){const [state,workspace]=await Promise.all([get(`/api/projects/${projectId}`),get("/api/workspace")]);window.projectState=state;window.workspaceState=workspace;setWorkspace(`${workspace.workspace} · ${state.project.title}`);let content,intentState=null;if(chapter){const ch=await get(`/api/projects/${projectId}/chapters/${chapter}`);try{intentState=await get(`/api/projects/${projectId}/chapters/${chapter}/intent`)}catch(e){intentState=null}content=`${intentState?intentFormHtml(intentState):""}<section class="card"><div class="eyebrow">Chapter ${chapter}</div><h2>第 ${chapter} 章创作中心</h2><p>${esc(actionSummary(ch.production))}</p>${ch.chapter_context.final.exists?'<p class="notice">正式章节已经存在；当前页面继续处理语义状态与关闭流程。</p>':'<p class="notice warn">当前尚无可进入审稿台的章节草稿。先完成当前安全动作。</p>'}<a class="button secondary" href="/projects/${projectId}">返回项目驾驶舱</a></section>${dashboardHtml(state)}`}else if(section==="dashboard"){content=dashboardHtml(state)}else{const info=sectionText[section]||["项目工作区","该工作区由当前生产状态驱动。"],mode=state.project.creation_mode;content=`<section class="card"><div class="eyebrow">${esc(section)}</div><h2>${esc(info[0])}</h2><p class="lead">${esc(info[1])}</p><div class="action-box"><span class="badge ${badgeClass(state.next_action.status)}">${esc(statusName(state.next_action.status))}</span><p>${esc(actionSummary(state.next_action))}</p><div class="button-row">${actionControls(state)}</div></div>${section==="sources"&&mode==="original"?'<p class="notice">原创项目不需要原著资料流程。</p>':""}${section==="fanfiction"&&mode==="original"?'<p class="notice">这是原创项目，不启用同人 Canon 路线。</p>':""}<p class="small muted">本页遵守同一个项目状态机；打开页面本身不会触发 apply、finalize、semantic apply 或 chapter close。</p></section>`}app.className="";app.innerHTML=`<section class="panel spaced-bottom"><span class="badge">${esc(modeName(state.project.creation_mode))}</span><h2>${esc(state.project.title)}</h2><p class="muted">${esc(state.project.relative_config)}</p></section><div class="project-layout">${projectNav(state,section)}<div class="grid single-column">${content}</div></div>`;bindProjectActions(projectId);if(intentState)bindIntentForm(projectId,intentState);if(state.agent_jobs.some(j=>["starting","running","cancelling"].includes(j.status)))setTimeout(()=>renderProject(projectId,section,chapter).catch(showError),1800)}
function bindProjectActions(projectId){document.querySelectorAll("[data-advance]").forEach(b=>b.addEventListener("click",()=>runAction(b,()=>post(`/api/projects/${projectId}/production/advance`,{}),projectId)));document.querySelectorAll("[data-start-job]").forEach(b=>b.addEventListener("click",()=>runAction(b,()=>post(`/api/projects/${projectId}/agent-jobs`,{task_id:b.dataset.startJob}),projectId)));document.querySelectorAll("[data-cancel-job]").forEach(b=>b.addEventListener("click",()=>runAction(b,()=>post(`/api/projects/${projectId}/agent-jobs/${b.dataset.cancelJob}/cancel`,{}),projectId)));document.querySelectorAll("[data-approve-task]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("approve-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先确认已阅读当前精确候选。</span>';return}runAction(b,()=>post(`/api/projects/${projectId}/production/approve-design`,{task_id:b.dataset.approveTask,expected_sha256:b.dataset.sha,approved_by:"human",acknowledge_human_decision:true}),projectId)}));document.querySelectorAll("[data-apply-canonical]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("canonical-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先明确确认本次 Canon 写入。</span>';return}runAction(b,()=>post(`/api/projects/${projectId}/production/apply-compiled-design`,{task_id:b.dataset.applyCanonical,expected_sha256:b.dataset.sha,approved_by:"human",acknowledge_canonical_write:true}),projectId)}));document.querySelectorAll("[data-finalize]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("finalize-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先明确确认章节定稿。</span>';return}const chapter=Number(b.dataset.finalize);runAction(b,()=>post(`/api/projects/${projectId}/chapters/${chapter}/finalize`,{chapter_number:chapter,expected_draft_sha256:b.dataset.sha,approved_by:"human",acknowledge_finalize:true}),projectId)}));document.querySelectorAll("[data-semantic-apply]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("semantic-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先明确确认章节语义 apply。</span>';return}const chapter=Number(b.dataset.chapter);runAction(b,()=>post(`/api/projects/${projectId}/chapters/${chapter}/semantic-apply`,{task_id:b.dataset.semanticApply,chapter_number:chapter,expected_sha256:b.dataset.sha,approved_by:"human",acknowledge_semantic_apply:true}),projectId)}));document.querySelectorAll("[data-approve-voice]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("voice-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先确认这是自己的真实改稿。</span>';return}try{const chapter=Number(b.dataset.approveVoice),payload={chapter_number:chapter,expected_final_sha256:b.dataset.sha,record:JSON.parse(document.getElementById("voice-record").value),approved_by:"human",acknowledge_voice_apply:true};runAction(b,()=>post(`/api/projects/${projectId}/chapters/${chapter}/approve-author-voice`,payload),projectId)}catch(e){document.getElementById("action-status").innerHTML=`<span class="notice danger">JSON 无效：${esc(e.message)}</span>`}}));document.querySelectorAll("[data-confirm-events]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("event-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先确认已逐项核对事件。</span>';return}try{const chapter=Number(b.dataset.confirmEvents),payload={chapter_number:chapter,expected_final_sha256:b.dataset.sha,observations:JSON.parse(document.getElementById("event-observations").value),discovered_causal_nodes:JSON.parse(document.getElementById("event-discovered").value),realized_major_divergences:JSON.parse(document.getElementById("event-divergences").value),confirmed_by:"human",acknowledge_event_apply:true};runAction(b,()=>post(`/api/projects/${projectId}/chapters/${chapter}/confirm-events`,payload),projectId)}catch(e){document.getElementById("action-status").innerHTML=`<span class="notice danger">JSON 无效：${esc(e.message)}</span>`}}));document.querySelectorAll("[data-confirm-promises]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("promise-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先确认已核对读者承诺证据。</span>';return}try{const chapter=Number(b.dataset.confirmPromises),payload={chapter_number:chapter,expected_final_sha256:b.dataset.sha,evidence:JSON.parse(document.getElementById("promise-evidence").value),confirmed_by:"human",acknowledge_promise_apply:true};runAction(b,()=>post(`/api/projects/${projectId}/chapters/${chapter}/confirm-promises`,payload),projectId)}catch(e){document.getElementById("action-status").innerHTML=`<span class="notice danger">JSON 无效：${esc(e.message)}</span>`}}));document.querySelectorAll("[data-close]").forEach(b=>b.addEventListener("click",()=>{if(!document.getElementById("close-check").checked){document.getElementById("action-status").innerHTML='<span class="notice warn">请先明确确认章节关闭。</span>';return}const chapter=Number(b.dataset.close);runAction(b,()=>post(`/api/projects/${projectId}/chapters/${chapter}/close`,{chapter_number:chapter,expected_final_sha256:b.dataset.sha,approved_by:"human",acknowledge_chapter_close:true}),projectId)}))}
async function runAction(button,fn,projectId){button.disabled=true;const box=document.getElementById("action-status");if(box)box.textContent="正在处理结构化动作……";try{await fn();location.href=`/projects/${projectId}`}catch(e){button.disabled=false;if(box)box.innerHTML=`<span class="notice danger">${esc(e.message)}</span>`;else showError(e)}}
async function boot(){if(currentPath==="/projects/new")return renderNewProject();const match=currentPath.match(/^\/projects\/(project_[0-9a-f]{20})(?:\/(design|sources|fanfiction|planning|knowledge|publication|literary|recovery))?$/);if(match)return renderProject(match[1],match[2]||"dashboard");const chapter=currentPath.match(/^\/projects\/(project_[0-9a-f]{20})\/chapters\/([1-9][0-9]*)$/);if(chapter)return renderProject(chapter[1],"dashboard",Number(chapter[2]));return renderHome()}
boot().catch(showError);
</script></body></html>'''


__all__ = [
    "WorkspaceStudioError",
    "WorkspaceStudioHTTPServer",
    "WorkspaceStudioService",
    "workspace_studio_page_html",
]
