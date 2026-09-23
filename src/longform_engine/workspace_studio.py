"""Workspace-level local Web entry for novel project creation and navigation."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
import html
import hmac
import json
import re
import secrets
import subprocess
import time

from longform_engine import __version__
from longform_engine.agent_jobs import CodexAgentJobManager
from longform_engine.agent_tasks import list_manifests, load_manifest, manifest_input_paths, manifest_output, resolve_under_root
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
from longform_engine.storage.layout import manuscript_chapter_path, parse_canonical_chapter_number
from longform_engine.semantic import chapter_close, semantic_apply
from longform_engine.resources import resource_path
from longform_engine.studio_content import StudioContent, StudioDraftConflict
from longform_engine.execution_origin import execution_origin


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
        rehearsal_run_id: str | None = None,
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
        if rehearsal_run_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", rehearsal_run_id):
            raise WorkspaceStudioError("自动演练 run_id 无效")
        self.rehearsal_run_id = rehearsal_run_id
        self._codex_status_cache: tuple[float, dict[str, Any]] | None = None

    def state(self) -> dict[str, Any]:
        from longform_engine.fanfiction_creative_requirements import (
            CONTINUITY_REQUIREMENTS, ROUTE_FAMILIES, compile_fanfiction_creative_requirements,
        )
        projects = [self._project_card(config_path, include_production=False) for config_path in self._project_configs()]
        return {
            "schema": "novel_workspace_studio_state_v1",
            "workspace": str(self.root),
            "engine_version": __version__,
            "automated_rehearsal": self.rehearsal_run_id is not None,
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
            if self.rehearsal_run_id:
                atomic_write_text(destination / "00_governance/execution_origin.json", json.dumps({
                    "schema": "execution_origin_v1", "kind": "automated_rehearsal", "simulated_human": True,
                    "run_id": self.rehearsal_run_id, "formal_literary_eligible": False,
                }, ensure_ascii=False, indent=2) + "\n")
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
        review_candidate = None
        if next_action.get("status") == "agent_task_validated" and next_action.get("task_type") in {"semantic_review", "pacing_review"}:
            manifest = load_manifest(config_path.parent, next_action["task_id"])
            candidate = resolve_under_root(config_path.parent, manifest_output(manifest)["path"])
            raw = candidate.read_bytes()
            review_candidate = {"task_id": manifest["task_id"], "sha256": sha256(raw).hexdigest(),
                                "content": raw.decode("utf-8"), "chapter_number": chapter_number}
        return {
            "schema": "novel_workspace_project_state_v1",
            "project": card,
            "next_action": next_action,
            "review_candidate": review_candidate,
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
                "quality": f"/projects/{project_id}/quality",
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
            "execution_origin": quality["execution_origin"],
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

    def record_current_review(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Record one exact validated gate review without changing canonical story facts."""
        from longform_engine.gates import semantic_review_apply, semantic_pacing_apply

        if set(payload) != {"task_id", "expected_sha256"}:
            raise WorkspaceStudioError("记录审稿只接受当前任务及结果版本")
        config = load_project_config(self._config_for_project_id(project_id))
        root = config.path.parent
        with acquire_project_lock(config, owner="workspace-studio", command="record current gate review"):
            current = production_next(config)
            if (current.get("status") != "agent_task_validated" or current.get("task_id") != payload["task_id"]
                    or current.get("task_type") not in {"semantic_review", "pacing_review"}):
                raise WorkspaceStudioError("只能记录当前已校验的连贯性或节奏审稿")
            manifest = load_manifest(root, payload["task_id"])
            candidate = resolve_under_root(root, manifest_output(manifest)["path"])
            if not candidate.is_file() or sha256(candidate.read_bytes()).hexdigest() != payload["expected_sha256"]:
                raise WorkspaceStudioError("审稿结果已变化，请刷新查看当前版本")
            if current["task_type"] == "semantic_review":
                result = semantic_review_apply(config, chapter_number=current["chapter_number"], file_path=candidate)
            else:
                result = semantic_pacing_apply(config, chapter_number=current["chapter_number"], file_path=candidate)
        return {**asdict(result), "canonical_mutated": False}

    def rebuild_current_review(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Rebuild only the failed current review using its domain-owned inputs."""
        from longform_engine.gates import semantic_review_task, semantic_pacing_task
        from longform_engine.quality import reader_payoff_task

        if set(payload) != {"task_id"}:
            raise WorkspaceStudioError("重建审稿只接受当前任务 ID")
        config = load_project_config(self._config_for_project_id(project_id))
        with acquire_project_lock(config, owner="workspace-studio", command="rebuild current review"):
            current = production_next(config)
            if (current.get("status") not in {"agent_task_invalid", "agent_task_contract_invalid"}
                    or current.get("task_id") != payload["task_id"]
                    or current.get("task_type") not in {"semantic_review", "pacing_review", "reader_payoff_review"}):
                raise WorkspaceStudioError("只能重建当前失败或失效的独立审稿")
            if any(job["status"] in {"queued", "running", "cancelling"} for job in self.agent_jobs.list_jobs(config)):
                raise WorkspaceStudioError("请等待或取消当前运行的任务")
            if current["task_type"] == "semantic_review":
                result = asdict(semantic_review_task(config, chapter_number=current["chapter_number"]))
            elif current["task_type"] == "pacing_review":
                result = asdict(semantic_pacing_task(config, chapter_number=current["chapter_number"]))
            else:
                result = asdict(reader_payoff_task(config, chapter_number=current["chapter_number"]))
        return {**result, "canonical_mutated": False}

    def revise_design_candidate(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from longform_engine.intelligence.pipeline import revise_design_document
        if set(payload) != {"task_id", "expected_sha256", "text"}:
            raise WorkspaceStudioError("设计修改字段无效")
        config = load_project_config(self._config_for_project_id(project_id))
        with acquire_project_lock(config, owner="workspace-studio", command="intelligence human revision"):
            if any(job["status"] in {"queued", "running", "cancelling"} for job in self.agent_jobs.list_jobs(config)):
                raise WorkspaceStudioError("请等待或取消本作品运行中的任务，再修改设计。")
            return revise_design_document(config, **payload)

    def rebuild_design_compile(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from longform_engine.intelligence import create_design_compile_task
        if set(payload) != {"task_id"}:
            raise WorkspaceStudioError("重建只接受当前工单 ID")
        config = load_project_config(self._config_for_project_id(project_id))
        with acquire_project_lock(config, owner="workspace-studio", command="intelligence compile-task"):
            current = production_next(config)
            if current.get("task_id") != payload["task_id"] or current.get("task_type") != "design_semantic_compile":
                raise WorkspaceStudioError("此工单已不是当前设计编译任务，请刷新状态。")
            if any(job["status"] in {"queued", "running", "cancelling"} for job in self.agent_jobs.list_jobs(config)):
                raise WorkspaceStudioError("请先等待或取消当前运行任务。")
            manifest = load_manifest(config.path.parent, payload["task_id"])
            task_type, document = self._compiled_design_source(manifest)
            return asdict(create_design_compile_task(config, task_type=task_type, document_path=document))

    def rebuild_ideation(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from longform_engine.intelligence import create_intelligence_task
        if set(payload) != {"task_id"}:
            raise WorkspaceStudioError("重建只接受当前开书工单 ID")
        config = load_project_config(self._config_for_project_id(project_id))
        with acquire_project_lock(config, owner="workspace-studio", command="intelligence task --rebuild"):
            current = production_next(config)
            if current.get("task_id") != payload["task_id"] or current.get("task_type") != "book_ideation":
                raise WorkspaceStudioError("此工单已不是当前开书轮次，请刷新状态。")
            if any(job["status"] in {"queued", "running", "cancelling"} for job in self.agent_jobs.list_jobs(config)):
                raise WorkspaceStudioError("请先等待或取消当前运行任务。")
            manifest = load_manifest(config.path.parent, payload["task_id"])
            inputs = [value for value in manifest_input_paths(manifest)
                      if not value.startswith("50_workbench/intelligence_tasks/")]
            return asdict(create_intelligence_task(config, task_type="book_ideation", input_files=inputs, rebuild=True))

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

    def _project_card(self, config_path: Path, *, include_production: bool = True) -> dict[str, Any]:
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
        manuscript_files = [path for lane in ("draft", "final")
                            for path in (config_path.parent / "40_manuscript" / lane).glob("ch*.md")
                            if path.is_file() and path.resolve().is_relative_to(config_path.parent)
                            and parse_canonical_chapter_number(path) is not None]
        final_chapters = [path for path in manuscript_files if path.parent.name == "final"]
        action: dict[str, Any] = {"status": "reading_available" if final_chapters else "draft_available" if manuscript_files else "project_created"}
        if include_production:
            try:
                action = production_next(config)
            except (ValueError, OSError, KeyError) as exc:
                action = {"status": "need_human", "blocked_by": "production_state_unreadable",
                          "human_summary": f"生产状态暂时无法读取：{exc}。已有正文仍可从目录打开。"}
        return {
            "id": project_id,
            "relative_config": relative,
            "status": "ready",
            "title": config.data["project"]["title"],
            "slug": config.data["project"]["slug"],
            "creation_mode": config.data["creation"]["mode"],
            "continuity_mode": config.data["fanfiction"]["continuity_mode"],
            "target_platform": config.data["novel"]["target_platform"],
            "execution_origin": execution_origin(config_path.parent),
            "final_chapter_count": len(final_chapters),
            "latest_manuscript_chapter": max((parse_canonical_chapter_number(path) or 0 for path in manuscript_files), default=None),
            "manuscript_updated_at": max((path.stat().st_mtime for path in manuscript_files), default=None),
            "next_action": action,
        }

    def _candidate_projection(
        self, config_path: Path, next_action: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not next_action.get("task_id") or next_action.get("task_type") not in {*DESIGN_INTELLIGENCE_TASK_TYPES, "design_semantic_compile"}:
            return None
        root = config_path.parent.resolve()
        manifest = load_manifest(root, str(next_action.get("task_id") or ""))
        if manifest["task_type"] == "design_semantic_compile":
            source_type, source_path = self._compiled_design_source(manifest)
            sources = [row for row in list_manifests(root) if row["task_type"] == source_type
                       and row.get("status") in {"submitted", "validated", "approved", "invalid"}
                       and manifest_output(load_manifest(root, row["task_id"]))["path"] == source_path]
            if len(sources) != 1:
                return None
            manifest = load_manifest(root, sources[0]["task_id"])
        if manifest.get("status") not in {"submitted", "validated", "approved", "invalid"}:
            return None
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
            "human_approval_required": manifest.get("status") == "validated",
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
            projection["changes"] = changes
            characters_file = root / "10_bible/characters.json"
            characters = json.loads(characters_file.read_text(encoding="utf-8")) if characters_file.is_file() else []
            projection["characters"] = [{"id": item["id"], "name": item.get("name") or item["id"]}
                                        for item in characters if isinstance(item, dict) and item.get("id")]
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
            if path.startswith("50_workbench/intelligence_candidates/") and path.endswith(".candidate.md")
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
            if parsed.path == "/favicon.ico":
                self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                return
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
            source_page = re.fullmatch(r"/projects/(project_[0-9a-f]{20})/sources/manage", parsed.path)
            if source_page:
                from longform_engine.studio_server import studio_page_html
                project_id = source_page.group(1)
                self.server.service._config_for_project_id(project_id)
                self._send_html(studio_page_html(self.server.csrf_token, self.server.csp_nonce,
                    api_prefix=f"/api/projects/{project_id}/sources"))
                return
            source_state = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/sources/state", parsed.path)
            if source_state:
                from longform_engine.studio_server import StudioService
                config = load_project_config(self.server.service._config_for_project_id(source_state.group(1)))
                self._send_json(HTTPStatus.OK, StudioService(config).state())
                return
            source_preview = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/sources/evidence/preview", parsed.path)
            if source_preview:
                from longform_engine.studio_server import StudioService
                values = parse_qs(parsed.query, keep_blank_values=True)
                if set(values) - {"item_id", "offset", "limit"} or any(len(v) != 1 for v in values.values()):
                    raise WorkspaceStudioError("证据预览参数无效")
                config = load_project_config(self.server.service._config_for_project_id(source_preview.group(1)))
                self._send_json(HTTPStatus.OK, StudioService(config).evidence_preview({
                    "item_id": (values.get("item_id") or [""])[0],
                    "offset": (values.get("offset") or ["0"])[0],
                    "limit": (values.get("limit") or ["40"])[0],
                }))
                return
            if parsed.path.startswith("/assets/studio/"):
                name = parsed.path.removeprefix("/assets/studio/")
                if name not in {"workspace.js", "onboarding.js", "planning_view.js", "learning.js", "reader.js", "discussion.js", "versions.js", "planning.js", "chapter_confirmations.js", "document_view.js", "graph_view.js", "studio.css"}:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "asset_not_found"})
                    return
                content_type = "text/css; charset=utf-8" if name.endswith(".css") else "text/javascript; charset=utf-8"
                self._send_bytes(HTTPStatus.OK, resource_path("templates", "studio", name).read_bytes(), content_type)
                return
            discussion_match = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/discussions", parsed.path)
            if discussion_match:
                from longform_engine.studio_discussion import StudioDiscussion
                root = self.server.service._config_for_project_id(discussion_match.group(1)).parent
                self._send_json(HTTPStatus.OK, StudioDiscussion(root).history())
                return
            planning_state = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/planning/state", parsed.path)
            if planning_state:
                from longform_engine.planning.workbench import PlanningWorkbench
                config = load_project_config(self.server.service._config_for_project_id(planning_state.group(1)))
                self._send_json(HTTPStatus.OK, {**PlanningWorkbench(config).state(),
                    "approved": StudioContent(self.server.service._config_for_project_id(planning_state.group(1)).parent).planning_view()})
                return
            learning_state = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/learning", parsed.path)
            if learning_state:
                from longform_engine.studio_learning import StudioLearning
                config = load_project_config(self.server.service._config_for_project_id(learning_state.group(1)))
                self._send_json(HTTPStatus.OK, StudioLearning(config).state())
                return
            operation_state = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/(recovery|publication)/state", parsed.path)
            if operation_state:
                from longform_engine.studio_operations import studio_operation_state
                config = load_project_config(self.server.service._config_for_project_id(operation_state.group(1)))
                self._send_json(HTTPStatus.OK, studio_operation_state(config, operation_state.group(2)))
                return
            content_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/(identity|catalogue|documents|search|documents/doc_[a-f0-9]{24}|chapters/[1-9][0-9]*/(?:text|edit-draft|versions|versions/version_[a-f0-9]{24}))", parsed.path)
            if content_match:
                project_id, action = content_match.groups()
                config_path = self.server.service._config_for_project_id(project_id)
                content = StudioContent(config_path.parent)
                query = parse_qs(parsed.query, keep_blank_values=True)
                allowed = {"offset", "limit"} if action == "catalogue" else {"q", "offset", "limit"} if action == "search" else {"version"} if action.endswith("/text") else set()
                if set(query) - allowed or any(len(values) != 1 for values in query.values()):
                    raise WorkspaceStudioError("内容查询字段无效")
                if action == "identity":
                    card = self.server.service._project_card(config_path, include_production=False)
                    result = {key: card.get(key) for key in ("id", "title", "creation_mode", "execution_origin")}
                elif action == "catalogue":
                    result = content.catalogue(offset=int(query.get("offset", ["0"])[0]), limit=int(query.get("limit", ["100"])[0]))
                elif action == "documents":
                    result = {"documents": content.documents()}
                elif action == "search":
                    result = content.search(query.get("q", [""])[0], offset=int(query.get("offset", ["0"])[0]), limit=int(query.get("limit", ["40"])[0]))
                elif action.startswith("documents/"):
                    result = content.document(action.split("/")[1])
                elif action.endswith("/versions"):
                    result = content.versions(int(action.split("/")[1]))
                elif "/versions/" in action:
                    result = content.version(int(action.split("/")[1]), action.split("/")[-1])
                elif action.endswith("/edit-draft"):
                    result = content.draft(int(action.split("/")[1]))
                else:
                    result = content.chapter(int(action.split("/")[1]), version=query.get("version", ["preferred"])[0])
                self._send_json(HTTPStatus.OK, result)
                return
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
                self.server.service._config_for_project_id(project_id)
                if parsed.query == "mode=review":
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
            source_action = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/sources/([-a-z/]+)", parsed.path)
            if source_action:
                from longform_engine.studio_server import StudioService, dispatch_studio_action, receive_browser_upload
                source_service = StudioService(load_project_config(self.server.service._config_for_project_id(source_action.group(1))))
                action_path = "/api/" + source_action.group(2)
                if action_path == "/api/upload/file":
                    receive_browser_upload(self, source_service, parsed.query)
                else:
                    self._send_json(HTTPStatus.OK, {"ok": True, "result": dispatch_studio_action(source_service, action_path, self._read_json())})
                return
            body = self._read_json()
            learning_action = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/learning/(adopt|effect|feedback-record|feedback-decide|feedback-convert)", parsed.path)
            if learning_action:
                from longform_engine.studio_learning import StudioLearning
                config = load_project_config(self.server.service._config_for_project_id(learning_action.group(1)))
                with acquire_project_lock(config, command=f"studio learning {learning_action.group(2)}"):
                    result = StudioLearning(config).act(learning_action.group(2), body)
                self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
                return
            planning_action = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/planning/(create|rebuild|prepare-review|review-validate|approve)", parsed.path)
            if planning_action:
                from longform_engine.planning.workbench import PlanningWorkbench
                config = load_project_config(self.server.service._config_for_project_id(planning_action.group(1)))
                action = planning_action.group(2)
                with acquire_project_lock(config, command=f"studio planning {action}"):
                    workbench = PlanningWorkbench(config)
                    if action in {"create", "rebuild"}:
                        if set(body) - {"proposals"}:
                            raise WorkspaceStudioError("创建规划任务只接受显式提案选择")
                        result = workbench.create(rebuild=action == "rebuild", proposals=body.get("proposals"))
                    elif action in {"prepare-review", "review-validate"}:
                        if set(body) != {"task_id"}:
                            raise WorkspaceStudioError("规划动作需要当前任务 ID")
                        result = workbench.prepare_review(body["task_id"]) if action == "prepare-review" else workbench.validate_review(body["task_id"])
                    else:
                        result = workbench.approve(body)
                self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
                return
            note_action = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/notes", parsed.path)
            if note_action:
                content = StudioContent(self.server.service._config_for_project_id(note_action.group(1)).parent)
                self._send_json(HTTPStatus.OK, {"ok": True, "result": content.save_note(body)})
                return
            operation_action = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/(recovery|publication)/action", parsed.path)
            if operation_action:
                from longform_engine.studio_operations import execute_studio_operation
                config = load_project_config(self.server.service._config_for_project_id(operation_action.group(1)))
                self._send_json(HTTPStatus.OK, {"ok": True, "result": execute_studio_operation(config, operation_action.group(2), body)})
                return
            discussion_action = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/discussions(?:/(discussion_[0-9a-f]{24})/(record|adopt))?", parsed.path)
            if discussion_action:
                from longform_engine.studio_discussion import StudioDiscussion
                project_id, turn_id, action = discussion_action.groups()
                discussion = StudioDiscussion(self.server.service._config_for_project_id(project_id).parent)
                if action == "record":
                    if body:
                        raise WorkspaceStudioError("记录回答不接受附加内容")
                    result = discussion.record(turn_id)
                elif action == "adopt":
                    result = discussion.adopt(turn_id, body)
                else:
                    result = discussion.create(body)
                self._send_json(HTTPStatus.OK, {"ok": True, "result": result})
                return
            edit_draft = re.fullmatch(r"/api/projects/(project_[0-9a-f]{20})/chapters/([1-9][0-9]*)/edit-draft", parsed.path)
            if edit_draft:
                content = StudioContent(self.server.service._config_for_project_id(edit_draft.group(1)).parent)
                self._send_json(HTTPStatus.OK, {"ok": True, "result": content.save_draft(int(edit_draft.group(2)), body)})
                return
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
            revise_design_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/revise-design", parsed.path)
            rebuild_compile_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/rebuild-design-compile", parsed.path)
            rebuild_ideation_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/rebuild-ideation", parsed.path)
            record_review_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/record-review", parsed.path)
            rebuild_review_match = re.fullmatch(
                r"/api/projects/(project_[0-9a-f]{20})/production/rebuild-review", parsed.path)
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
            elif rebuild_compile_match:
                result = self.server.service.rebuild_design_compile(rebuild_compile_match.group(1), body)
            elif record_review_match:
                result = self.server.service.record_current_review(record_review_match.group(1), body)
            elif rebuild_review_match:
                result = self.server.service.rebuild_current_review(rebuild_review_match.group(1), body)
            elif rebuild_ideation_match:
                result = self.server.service.rebuild_ideation(rebuild_ideation_match.group(1), body)
            elif revise_design_match:
                result = self.server.service.revise_design_candidate(revise_design_match.group(1), body)
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
        except StudioDraftConflict as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})
        except LocalWebError as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except (ConfigError, OSError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})


def _is_workspace_page_route(path: str) -> bool:
    return bool(
        path in {"/", "/projects/new"}
        or re.fullmatch(
            r"/projects/project_[0-9a-f]{20}(?:/(?:design|sources(?:/manage)?|fanfiction|planning|knowledge|publication|quality|literary|recovery))?",
            path,
        )
        or re.fullmatch(r"/projects/project_[0-9a-f]{20}/chapters/[1-9][0-9]*", path)
    )


def workspace_studio_page_html(csrf_token: str, csp_nonce: str) -> str:
    return resource_path("templates", "studio", "workspace.html").read_text(encoding="utf-8").replace(
        "__CSRF_TOKEN__", html.escape(csrf_token, quote=True)
    ).replace("workspacenonce", html.escape(csp_nonce, quote=True))





__all__ = [
    "WorkspaceStudioError",
    "WorkspaceStudioHTTPServer",
    "WorkspaceStudioService",
    "workspace_studio_page_html",
]
