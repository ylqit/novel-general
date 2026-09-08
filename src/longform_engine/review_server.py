"""Loopback-only human deep-review desk with strict non-canonical web boundaries."""

from __future__ import annotations

from dataclasses import asdict
from difflib import unified_diff
from hashlib import sha256
from http import HTTPStatus
from pathlib import Path
from typing import Any, cast
import hmac
import html
import json
import re

from longform_engine.agent_tasks import list_manifests, load_manifest, manifest_chapter_number, manifest_output, relative_path
from longform_engine.chapter_contract import load_verified_chapter_contract
from longform_engine.chapter_coedit import (
    coedit_status,
    create_chapter_coedit_rewrite_task,
    create_chapter_coedit_turn,
    current_coedit_candidate,
    record_chapter_coedit_response,
    validate_chapter_coedit_candidate,
    validate_chapter_coedit_response,
)
from longform_engine.config import ConfigDocument
from longform_engine.execution_origin import execution_origin
from longform_engine.human_chapter_intent import human_chapter_intent_status
from longform_engine.human_review_consultation import (
    consultation_status,
    create_human_review_consult_task,
    record_human_review_consultation,
    validate_human_review_consultation,
)
from longform_engine.human_story_review import (
    CHECK_FIELDS,
    apply_human_story_review,
    create_human_story_review_task,
    human_story_review_status,
    validate_human_story_review,
)
from longform_engine.human_author_revision import (
    create_human_author_revision_task,
    human_author_revision_status,
    task_record_path_for_hash,
    validate_human_author_revision,
)
from longform_engine.local_web import LocalWebError, LoopbackHTTPServer, LoopbackRequestHandler
from longform_engine.orchestration.pipeline import submit_agent_draft
from longform_engine.reader_promises_v2 import load_reader_promise_ledger
from longform_engine.repair_coordination import (
    create_repair_candidate_task,
    repair_attempt_status,
    review_barrier_status,
)
from longform_engine.storage import acquire_project_lock, atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_path
from longform_engine.storage.project import native_filesystem_path
from longform_engine.story_brief import load_current_story_brief_binding, story_brief_status


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


class ReviewServerError(LocalWebError):
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
        brief_currentness = story_brief_status(self.root, chapter)
        try:
            story_brief_binding = load_current_story_brief_binding(self.root, chapter)
        except ValueError:
            story_brief_binding = {}
        story_brief_basis_hash = str(
            story_brief_binding.get("story_brief_basis_sha256") or ""
        )

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
        template_path = (
            self.root
            / "50_workbench"
            / "human_story_reviews"
            / f"ch{chapter:03d}.{draft_hash[:12]}.{story_brief_basis_hash[:12]}.candidate.json"
        )
        review_template = _load_json(template_path, default={})
        review_validation_path = template_path.with_suffix(".validation.json")
        review_validation = _load_json(review_validation_path, default={})
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
        coedit = coedit_status(self.config, chapter_number=chapter)
        coedit["sessions"] = self._coedit_views(coedit.get("sessions") or [])
        human_revision = self.human_revision_state()
        human_final = bool(human_revision.get("available") or human_revision.get("status") in {"complete", "stale"})
        try:
            coedit_candidate = current_coedit_candidate(self.root, chapter)
        except ValueError:
            coedit_candidate = draft
        active_candidate = (
            self.root / str(human_revision.get("candidate_file") or "")
            if human_revision.get("available")
            else draft if human_final else coedit_candidate
        )
        if not active_candidate.is_file():
            frozen_source = self.root / str(human_revision.get("source_file") or "")
            active_candidate = frozen_source if human_revision.get("available") and frozen_source.is_file() else draft
        consult_candidate = {
            "path": relative_path(self.root, active_candidate),
            "sha256": _file_hash(active_candidate),
            "text": active_candidate.read_text(encoding="utf-8"),
            "phase": "human_final" if human_final else "coedit",
        }
        return {
            "schema": "human_review_desk_state_v3",
            "execution_origin": execution_origin(self.root),
            "chapter_number": chapter,
            "draft": {
                "path": relative_path(self.root, draft),
                "sha256": draft_hash,
                "text": draft_text,
            },
            "story_brief": {
                "path": relative_path(self.root, story_brief),
                "text": story_brief.read_text(encoding="utf-8") if story_brief.is_file() else "",
                "basis_sha256": story_brief_basis_hash,
                "currentness": brief_currentness,
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
            "review_template_sha256": _file_hash(template_path) if template_path.is_file() else "",
            "review_template": review_template,
            "review_validation": review_validation,
            "review_checks": [
                {"id": check_id, "label": CHECK_LABELS[check_id]} for check_id in CHECK_ORDER
            ],
            "consultations": consult,
            "coedit": coedit,
            "human_chapter_intent": human_chapter_intent_status(self.root, chapter),
            "human_intent_content": _load_json(self.root / "20_outline/chapter_intents" / f"ch{chapter:03d}.json", default={}),
            "manual_repair": manual,
            "repair_diff": diff_text,
            "human_author_revision": human_revision,
            "consultation_candidate": consult_candidate,
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
        try:
            story_brief_binding = load_current_story_brief_binding(
                self.root,
                self.chapter_number,
            )
        except ValueError as exc:
            raise ReviewServerError(str(exc)) from exc
        basis_hash = str(story_brief_binding["story_brief_basis_sha256"])
        candidate = (
            self.root
            / "50_workbench"
            / "human_story_reviews"
            / f"ch{self.chapter_number:03d}.{expected_candidate_sha256[:12]}.{basis_hash[:12]}.candidate.json"
        )
        if not candidate.is_file():
            raise ReviewServerError("human review task must be prepared before validation")
        if review.get("schema") != "human_story_review_v7":
            raise ReviewServerError("review schema must be human_story_review_v7")
        if set(review.get("dimension_coverage") or {}) != CHECK_FIELDS:
            raise ReviewServerError("review must cover all ten risk-layered story dimensions")
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

    def apply_human_review(
        self,
        *,
        expected_candidate_sha256: str,
        expected_review_sha256: str,
        approved_by: str,
        acknowledge_human_decision: bool,
    ) -> dict[str, Any]:
        self._require_current_candidate(expected_candidate_sha256)
        if approved_by != "human" or acknowledge_human_decision is not True:
            raise ReviewServerError("human review apply requires an explicit human decision")
        state = self.state()
        relative = str(state.get("review_template_file") or "")
        review = (self.root / relative).resolve()
        base = (self.root / "50_workbench" / "human_story_reviews").resolve()
        try:
            review.relative_to(base)
        except ValueError as exc:
            raise ReviewServerError("human review candidate escaped its workbench") from exc
        if not review.is_file() or _file_hash(review) != expected_review_sha256:
            raise ReviewServerError("human review changed; refresh before apply")
        validation = state.get("review_validation") or {}
        if not isinstance(validation, dict) or validation.get("ok") is not True:
            raise ReviewServerError("human review must pass validation before apply")
        with acquire_project_lock(
            self.config, owner="review-desk", command="review human-review-apply"
        ):
            result = apply_human_story_review(
                self.config,
                chapter_number=self.chapter_number,
                file_path=review,
                approved_by="human",
            )
        return asdict(result)

    def create_consultation(
        self,
        *,
        expected_candidate_sha256: str,
        start: int,
        end: int,
        question: str,
        phase: str,
    ) -> dict[str, Any]:
        self._require_current_consult_candidate(expected_candidate_sha256)
        with acquire_project_lock(
            self.config, owner="review-desk", command="review consult-task"
        ):
            if phase == "coedit":
                return asdict(
                    create_chapter_coedit_turn(
                        self.config,
                        chapter_number=self.chapter_number,
                        start=start,
                        end=end,
                        question=question,
                    )
                )
            elif phase == "human_final":
                return asdict(
                    create_human_review_consult_task(
                        self.config,
                        chapter_number=self.chapter_number,
                        start=start,
                        end=end,
                        question=question,
                    )
                )
            else:
                raise ReviewServerError("consultation phase must be coedit or human_final")

    def prepare_human_revision(self, *, expected_candidate_sha256: str) -> dict[str, Any]:
        self._require_current_candidate(expected_candidate_sha256)
        with acquire_project_lock(
            self.config, owner="review-desk", command="review human-revision-prepare"
        ):
            result = create_human_author_revision_task(
                self.config,
                chapter_number=self.chapter_number,
            )
        return asdict(result)

    def save_human_revision(
        self,
        *,
        expected_draft_sha256: str,
        expected_candidate_sha256: str,
        expected_record_sha256: str,
        text: str,
        record: dict[str, Any],
    ) -> dict[str, Any]:
        self._require_current_candidate(expected_draft_sha256)
        state = self.human_revision_state()
        if not state.get("available"):
            raise ReviewServerError("human revision task must be prepared first")
        if state.get("editable") is False:
            raise ReviewServerError("已提交的人工终稿只读；请经正常修订流程建立新版本")
        candidate = (self.root / str(state["candidate_file"])).resolve()
        record_file = (self.root / str(state["record_file"])).resolve()
        if (_file_hash(candidate) if candidate.is_file() else "") != str(expected_candidate_sha256 or ""):
            raise ReviewServerError("human revision candidate changed concurrently; reload first")
        if (_file_hash(record_file) if record_file.is_file() else "") != str(expected_record_sha256 or ""):
            raise ReviewServerError("human revision record changed concurrently; reload first")
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip() or not isinstance(record, dict):
            raise ReviewServerError("human revision requires a complete chapter and JSON record")
        if not normalized.endswith("\n"):
            normalized += "\n"
        record = {**record, "revision_candidate_sha256": sha256(normalized.encode("utf-8")).hexdigest()}
        previous = state.get("record") or {}
        if ({k: v for k, v in previous.items() if k != "semantic_review_sha256"}
                != {k: v for k, v in record.items() if k != "semantic_review_sha256"}):
            record["semantic_review_sha256"] = ""
        with acquire_project_lock(
            self.config, owner="review-desk", command="review human-revision-save"
        ):
            self._require_current_candidate(expected_draft_sha256)
            if (_file_hash(candidate) if candidate.is_file() else "") != str(expected_candidate_sha256 or ""):
                raise ReviewServerError("human revision candidate changed before save")
            if (_file_hash(record_file) if record_file.is_file() else "") != str(expected_record_sha256 or ""):
                raise ReviewServerError("human revision record changed before save")
            atomic_write_text(candidate, normalized)
            atomic_write_text(record_file, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            from longform_engine.human_review_consultation import mark_stale_human_consultations

            mark_stale_human_consultations(self.root, chapter_number=self.chapter_number)
        return {
            "candidate_file": relative_path(self.root, candidate),
            "candidate_sha256": _file_hash(candidate),
            "record_file": relative_path(self.root, record_file),
            "record_sha256": _file_hash(record_file),
            "canonical_mutated": False,
        }

    def validate_human_revision(self, *, expected_draft_sha256: str) -> dict[str, Any]:
        self._require_current_candidate(expected_draft_sha256)
        state = self.human_revision_state()
        if not state.get("available"):
            raise ReviewServerError("human revision task must be prepared first")
        with acquire_project_lock(
            self.config, owner="review-desk", command="review human-revision-validate"
        ):
            result = validate_human_author_revision(
                self.config,
                chapter_number=self.chapter_number,
                file_path=str(state["candidate_file"]),
                record_path=str(state["record_file"]),
            )
        return asdict(result)

    def submit_human_revision(
        self,
        *,
        expected_draft_sha256: str,
        expected_candidate_sha256: str,
    ) -> dict[str, Any]:
        self._require_current_candidate(expected_draft_sha256)
        state = self.human_revision_state()
        candidate = (self.root / str(state.get("candidate_file") or "")).resolve()
        if not state.get("available") or not candidate.is_file() or _file_hash(candidate) != expected_candidate_sha256:
            raise ReviewServerError("validated human revision candidate is missing or changed")
        with acquire_project_lock(
            self.config, owner="review-desk", command="review human-revision-submit"
        ):
            result = submit_agent_draft(
                self.config,
                chapter_number=self.chapter_number,
                file_path=candidate,
                agent="human",
                overwrite=True,
            )
        return asdict(result)

    def human_revision_state(self) -> dict[str, Any]:
        chapter = self.chapter_number
        draft = manuscript_chapter_path(self.root, chapter, lane="draft")
        if not draft.is_file():
            return {"available": False, "status": "pending"}
        status = human_author_revision_status(self.config, chapter_number=chapter)
        digest = str(status["source_candidate_sha256"]) if status.get("status") == "complete" else _file_hash(draft)
        try:
            task_file = task_record_path_for_hash(self.root, chapter, digest)
        except ValueError:
            return {
                "available": False,
                **human_author_revision_status(self.config, chapter_number=chapter),
            }
        task = _load_json(task_file, default={})
        if not isinstance(task, dict) or not task:
            return {"available": False, **status}
        candidate = self.root / str(task.get("candidate_file") or "")
        record_file = self.root / str(task.get("record_file") or "")
        source = self.root / str(task.get("source_file") or "")
        candidate_text = candidate.read_text(encoding="utf-8") if candidate.is_file() else ""
        source_text = source.read_text(encoding="utf-8") if source.is_file() else ""
        diff = "".join(
            unified_diff(
                source_text.splitlines(keepends=True),
                candidate_text.splitlines(keepends=True),
                fromfile=relative_path(self.root, source),
                tofile=relative_path(self.root, candidate),
            )
        ) if candidate_text else ""
        return {
            "available": True,
            **status,
            "editable": status.get("status") != "complete",
            "task_file": relative_path(self.root, task_file),
            "source_file": relative_path(self.root, source),
            "candidate_file": relative_path(self.root, candidate),
            "candidate_sha256": _file_hash(candidate) if candidate.is_file() else "",
            "record_file": relative_path(self.root, record_file),
            "record_sha256": _file_hash(record_file) if record_file.is_file() else "",
            "record": _load_json(record_file, default={}),
            "text": candidate_text or source_text,
            "source_text": source_text,
            "diff": diff,
        }

    def validate_consultation(self, *, response_file: str, phase: str) -> dict[str, Any]:
        with acquire_project_lock(
            self.config, owner="review-desk", command="review consult-validate"
        ):
            if phase == "coedit":
                return asdict(
                    validate_chapter_coedit_response(
                        self.config,
                        chapter_number=self.chapter_number,
                        file_path=response_file,
                    )
                )
            elif phase == "human_final":
                return asdict(
                    validate_human_review_consultation(
                        self.config,
                        chapter_number=self.chapter_number,
                        file_path=response_file,
                    )
                )
            else:
                raise ReviewServerError("consultation phase must be coedit or human_final")

    def record_consultation(self, *, response_file: str, phase: str) -> dict[str, Any]:
        with acquire_project_lock(
            self.config, owner="review-desk", command="review consult-record"
        ):
            if phase == "coedit":
                return asdict(
                    record_chapter_coedit_response(
                        self.config,
                        chapter_number=self.chapter_number,
                        file_path=response_file,
                    )
                )
            elif phase == "human_final":
                return asdict(
                    record_human_review_consultation(
                        self.config,
                        chapter_number=self.chapter_number,
                        file_path=response_file,
                    )
                )
            else:
                raise ReviewServerError("consultation phase must be coedit or human_final")

    def create_coedit_rewrite(
        self,
        *,
        session_id: str,
        turn_number: int,
        option_id: str,
        adjustment: str,
    ) -> dict[str, Any]:
        with acquire_project_lock(
            self.config, owner="review-desk", command="review coedit-rewrite-task"
        ):
            result = create_chapter_coedit_rewrite_task(
                self.config,
                chapter_number=self.chapter_number,
                session_id=session_id,
                turn_number=turn_number,
                option_id=option_id,
                adjustment=adjustment,
            )
        return asdict(result)

    def validate_coedit_candidate(self, *, candidate_file: str) -> dict[str, Any]:
        with acquire_project_lock(
            self.config, owner="review-desk", command="review coedit-candidate-validate"
        ):
            result = validate_chapter_coedit_candidate(
                self.config,
                chapter_number=self.chapter_number,
                file_path=candidate_file,
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

    def submit_coedit_candidate(self, *, task_id: str, expected_draft_sha256: str,
                                expected_candidate_sha256: str, acknowledge: bool) -> dict[str, Any]:
        if acknowledge is not True:
            raise ReviewServerError("请阅读完整候选差异后明确确认提交")
        with acquire_project_lock(self.config, owner="review-desk", command="review coedit submit"):
            self._require_current_candidate(expected_draft_sha256)
            if self.state()["consultation_candidate"]["phase"] != "coedit":
                raise ReviewServerError("人工终稿锁定后不能采用 AI 改写")
            task = load_manifest(self.root, task_id)
            if task.get("task_type") != "chapter_coedit_rewrite" or manifest_chapter_number(task) != self.chapter_number:
                raise ReviewServerError("候选任务不属于本章协作")
            candidate = (self.root / manifest_output(task)["path"]).resolve()
            if not candidate.is_relative_to(self.root) or not candidate.is_file() or _file_hash(candidate) != expected_candidate_sha256:
                raise ReviewServerError("完整候选已变化，请重新阅读")
            validation = validate_chapter_coedit_candidate(self.config, chapter_number=self.chapter_number, file_path=candidate)
            if not validation.ok:
                raise ReviewServerError("完整候选尚未通过校验：" + "; ".join(validation.errors))
            return asdict(submit_agent_draft(self.config, chapter_number=self.chapter_number, file_path=candidate, agent="codex", overwrite=True))

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
        raise ReviewServerError(
            "manual repair cannot submit directly; prepare and validate human_author_revision_v4 first"
        )

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

    def _require_current_consult_candidate(self, expected_hash: str) -> None:
        state = self.human_revision_state()
        current = str(state.get("candidate_sha256") or "") if state.get("available") else ""
        if not current:
            try:
                candidate = current_coedit_candidate(self.root, self.chapter_number)
            except ValueError:
                candidate = manuscript_chapter_path(
                    self.root, self.chapter_number, lane="draft"
                )
            current = _file_hash(candidate) if candidate.is_file() else ""
        if not current or not hmac.compare_digest(current, str(expected_hash or "")):
            raise ReviewServerError("consultation candidate hash changed; reload the review desk")

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
                turn["response_current"] = bool(response_text and turn.get("response_sha256")
                                                and _file_hash(resolved) == turn["response_sha256"])
                request_path = (self.root / str(turn.get("request_file") or "")).resolve()
                turn["request"] = _load_json(request_path, default={}) if request_path.is_relative_to(base) and request_path.is_file() else {}
                turns.append(turn)
            item["turns"] = turns
            views.append(item)
        return views

    def _coedit_views(self, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        base = (
            self.root
            / "50_workbench"
            / "human_story_reviews"
            / "consultations"
            / "coedit"
            / f"ch{self.chapter_number:03d}"
        ).resolve()
        views: list[dict[str, Any]] = []
        for session in sessions:
            item = dict(session)
            turns: list[dict[str, Any]] = []
            for turn_path in session.get("turns") or []:
                resolved = (self.root / str(turn_path or "")).resolve()
                try:
                    resolved.relative_to(base)
                except ValueError:
                    continue
                turn = _load_json(resolved, default={})
                if not isinstance(turn, dict):
                    continue
                response = (self.root / str(turn.get("response_file") or "")).resolve()
                try:
                    response.relative_to(base)
                except ValueError:
                    response = Path()
                view = dict(turn)
                view["response"] = response.read_text(encoding="utf-8") if response.is_file() else ""
                view["response_current"] = bool(view["response"] and turn.get("response_sha256")
                                                and _file_hash(response) == turn["response_sha256"])
                view["candidate_current"] = bool(turn.get("candidate_sha256")
                                                  and turn["candidate_sha256"] == session.get("current_candidate_sha256"))
                view["rewrite_current"] = bool(turn.get("rewrite_candidate_sha256")
                                                and turn["rewrite_candidate_sha256"] == session.get("current_candidate_sha256"))
                request_path = (self.root / str(turn.get("request_file") or "")).resolve()
                view["request"] = _load_json(request_path, default={}) if request_path.is_relative_to(base) and request_path.is_file() else {}
                turns.append(view)
            item["turns"] = turns
            views.append(item)
        return views


REVIEW_ACTION_FIELDS: dict[str, frozenset[str]] = {
    "/human-review/prepare": frozenset({"expected_candidate_sha256"}),
    "/human-review/validate": frozenset({"expected_candidate_sha256", "review"}),
    "/human-review/apply": frozenset(
        {
            "expected_candidate_sha256",
            "expected_review_sha256",
            "approved_by",
            "acknowledge_human_decision",
        }
    ),
    "/consult/task": frozenset(
        {"expected_candidate_sha256", "start", "end", "question", "phase"}
    ),
    "/consult/validate": frozenset({"response_file", "phase"}),
    "/consult/record": frozenset({"response_file", "phase"}),
    "/coedit/rewrite-task": frozenset(
        {"session_id", "turn_number", "option_id", "adjustment"}
    ),
    "/coedit/candidate-validate": frozenset({"candidate_file"}),
    "/coedit/submit": frozenset({"task_id", "expected_draft_sha256", "expected_candidate_sha256", "acknowledge"}),
    "/human-revision/prepare": frozenset({"expected_candidate_sha256"}),
    "/human-revision/save": frozenset(
        {
            "expected_draft_sha256",
            "expected_candidate_sha256",
            "expected_record_sha256",
            "text",
            "record",
        }
    ),
    "/human-revision/validate": frozenset({"expected_draft_sha256"}),
    "/human-revision/submit": frozenset(
        {"expected_draft_sha256", "expected_candidate_sha256"}
    ),
    "/manual-repair/prepare": frozenset({"expected_candidate_sha256"}),
    "/manual-repair/save": frozenset(
        {"expected_draft_sha256", "expected_candidate_sha256", "text"}
    ),
    "/manual-repair/submit": frozenset(
        {"expected_draft_sha256", "expected_candidate_sha256"}
    ),
}


def dispatch_review_action(
    service: ReviewDeskService,
    action: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch one exact Review Desk workbench action without Canon authority."""

    expected_fields = REVIEW_ACTION_FIELDS.get(action)
    if expected_fields is None:
        raise ReviewServerError("review action is not allowed")
    if set(body) != expected_fields:
        raise ReviewServerError("review action fields are incomplete or contain unknown values")
    routes = {
        "/human-review/prepare": lambda: service.prepare_human_review(
            expected_candidate_sha256=str(body["expected_candidate_sha256"])
        ),
        "/human-review/validate": lambda: service.validate_human_review(
            expected_candidate_sha256=str(body["expected_candidate_sha256"]),
            review=cast(dict[str, Any], body["review"]),
        ),
        "/human-review/apply": lambda: service.apply_human_review(
            expected_candidate_sha256=str(body["expected_candidate_sha256"]),
            expected_review_sha256=str(body["expected_review_sha256"]),
            approved_by=str(body["approved_by"]),
            acknowledge_human_decision=cast(bool, body["acknowledge_human_decision"]),
        ),
        "/consult/task": lambda: service.create_consultation(
            expected_candidate_sha256=str(body["expected_candidate_sha256"]),
            start=int(body["start"]),
            end=int(body["end"]),
            question=str(body["question"]),
            phase=str(body["phase"]),
        ),
        "/consult/validate": lambda: service.validate_consultation(
            response_file=str(body["response_file"]), phase=str(body["phase"])
        ),
        "/consult/record": lambda: service.record_consultation(
            response_file=str(body["response_file"]), phase=str(body["phase"])
        ),
        "/coedit/rewrite-task": lambda: service.create_coedit_rewrite(
            session_id=str(body["session_id"]),
            turn_number=int(body["turn_number"]),
            option_id=str(body["option_id"]),
            adjustment=str(body["adjustment"]),
        ),
        "/coedit/candidate-validate": lambda: service.validate_coedit_candidate(
            candidate_file=str(body["candidate_file"])
        ),
        "/human-revision/prepare": lambda: service.prepare_human_revision(
            expected_candidate_sha256=str(body["expected_candidate_sha256"])
        ),
        "/human-revision/save": lambda: service.save_human_revision(
            expected_draft_sha256=str(body["expected_draft_sha256"]),
            expected_candidate_sha256=str(body["expected_candidate_sha256"]),
            expected_record_sha256=str(body["expected_record_sha256"]),
            text=str(body["text"]),
            record=cast(dict[str, Any], body["record"]),
        ),
        "/human-revision/validate": lambda: service.validate_human_revision(
            expected_draft_sha256=str(body["expected_draft_sha256"])
        ),
        "/human-revision/submit": lambda: service.submit_human_revision(
            expected_draft_sha256=str(body["expected_draft_sha256"]),
            expected_candidate_sha256=str(body["expected_candidate_sha256"]),
        ),
        "/manual-repair/prepare": lambda: service.prepare_manual_repair(
            expected_candidate_sha256=str(body["expected_candidate_sha256"])
        ),
        "/manual-repair/save": lambda: service.save_manual_repair(
            expected_draft_sha256=str(body["expected_draft_sha256"]),
            expected_candidate_sha256=str(body["expected_candidate_sha256"]),
            text=str(body["text"]),
        ),
        "/manual-repair/submit": lambda: service.submit_manual_repair(
            expected_draft_sha256=str(body["expected_draft_sha256"]),
            expected_candidate_sha256=str(body["expected_candidate_sha256"]),
        ),
    }
    if action == "/coedit/submit":
        result = service.submit_coedit_candidate(**body)
    else:
        result = routes[action]()
    return {**result, "canonical_mutated": action == "/human-review/apply"}


class ReviewHTTPServer(LoopbackHTTPServer):
    """Threaded loopback HTTP server; domain mutations still serialize on project.lock."""

    def __init__(self, service: ReviewDeskService, *, port: int) -> None:
        super().__init__(
            service=service,
            port=port,
            handler=ReviewRequestHandler,
            session_cookie="review_session",
            csrf_header="X-Review-CSRF",
            app_label="local review desk",
        )


class ReviewRequestHandler(LoopbackRequestHandler):
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
        except (ReviewServerError, LocalWebError) as exc:
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
            action = parsed.path.removeprefix("/api")
            if action not in REVIEW_ACTION_FIELDS:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "route_not_found"})
                return
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "result": dispatch_review_action(self.server.service, action, body)},
            )
        except (ReviewServerError, LocalWebError) as exc:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
        except (KeyError, TypeError, ValueError) as exc:
            self._send_json(HTTPStatus.CONFLICT, {"error": str(exc)})

def review_page_html(
    csrf_token: str,
    *,
    csp_nonce: str = "reviewdesk",
    api_prefix: str = "/api",
) -> str:
    """Render a static shell; all project text enters the DOM through textContent/value only."""

    if re.fullmatch(
        r"/api(?:/projects/project_[0-9a-f]{20}/chapters/[1-9][0-9]*/review)?",
        api_prefix,
    ) is None:
        raise ReviewServerError("review API prefix is invalid")
    csrf = html.escape(csrf_token, quote=True)
    nonce = html.escape(csp_nonce, quote=True)
    from longform_engine.resources import resource_path
    styles = resource_path("templates", "studio", "studio.css").read_text(encoding="utf-8")
    return (
        _REVIEW_PAGE.replace("__STUDIO_STYLES__", styles)
        .replace("__REVIEW_STYLES__", resource_path("templates", "studio", "review.css").read_text(encoding="utf-8"))
        .replace("__REVIEW_LAYOUT__", resource_path("templates", "studio", "review_layout.js").read_text(encoding="utf-8"))
        .replace("__REVIEW_FORMS__", resource_path("templates", "studio", "review_forms.js").read_text(encoding="utf-8"))
        .replace("__REVIEW_JOBS__", resource_path("templates", "studio", "review_jobs.js").read_text(encoding="utf-8"))
        .replace("__CSRF_TOKEN__", csrf)
        .replace("reviewdesk", nonce)
        .replace('"/api/', f'"{api_prefix}/')
        .replace("X-Review-CSRF", "X-Studio-CSRF" if api_prefix != "/api" else "X-Review-CSRF")
    )


def _file_hash(path: Path) -> str:
    return sha256(native_filesystem_path(path).read_bytes()).hexdigest()


def _load_json(path: Path, *, default: Any) -> Any:
    path = native_filesystem_path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


_REVIEW_PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="data:,"><title>Longform 人工可视化深审</title>
<style nonce="reviewdesk">
__STUDIO_STYLES__
__REVIEW_STYLES__
</style></head><body>
<header><a href="/" id="workspaceBack">作品书架</a><a id="readerBack" hidden>返回阅读</a><strong id="title">人工可视化深审</strong><details><summary>正文版本</summary><span id="candidate" class="muted"></span></details><button id="reload">刷新</button><button id="toggleReviewContext" class="secondary" aria-controls="reviewContext">创作依据</button><button id="toggleReviewDecisions" class="secondary" aria-controls="reviewDecisions">审稿与讨论</button><span id="globalStatus"></span><span id="reviewOrigin" class="muted" hidden></span></header>
<div id="layout">
<main class="col"><section><h2>正文与证据圈选</h2><textarea id="manuscript" readonly></textarea><div class="toolbar"><button data-evidence="key_turn">设为关键转折</button><button data-evidence="character_choice_or_emotion">设为人物选择/情绪</button><button data-evidence="reader_gain">设为读者收益</button></div><label>这处原文说明了什么<input id="evidenceNote" placeholder="说明选择、情绪或阅读收益"></label><details><summary>已登记证据详情</summary><pre id="evidenceView" class="muted"></pre></details></section>
<section><h2>人工修改与终稿确认</h2><div id="revisionMeta" class="muted"></div><details><summary>查看修改前冻结原稿</summary><textarea id="revisionSource" readonly aria-label="修改前冻结原稿"></textarea></details><textarea id="revisionText"></textarea><details><summary>修改记录协议详情</summary><textarea id="revisionRecord" aria-label="修改记录协议"></textarea></details><pre id="diff"></pre><div class="toolbar"><button id="revisionPrepare">建立人工终稿工作区</button><button id="revisionSave">保存完整修改稿</button><button id="revisionValidate">语义复核并锁定</button><button id="revisionSubmit" class="primary">提交人工终稿并重新审稿</button></div><div id="revisionStatus" class="status"></div></section>
<section><h2>整章修复候选</h2><div id="repairMeta" class="muted"></div><textarea id="repairText"></textarea><div class="toolbar"><button id="repairPrepare">准备人工整章修复</button><button id="repairSave">保存完整候选</button><button id="repairSubmit" class="primary">转入人工修订验证</button></div><div id="repairStatus" class="status"></div></section></main>
<aside class="col" id="reviewContext"><section><h2>人类章节意图</h2><pre id="chapterIntent"></pre></section><section><details><summary>本章写作说明</summary><pre id="brief"></pre></details></section><section><details><summary>章节规划与绑定详情</summary><pre id="contract"></pre></details></section><section><details><summary>读者承诺详情</summary><pre id="promises"></pre></details></section></aside>
<aside class="col" id="reviewDecisions"><section><h2>独立审稿意见</h2><div id="findings"></div></section>
<section><h2>人工审稿决定</h2><div id="checks"></div><details><summary>十维覆盖协议详情</summary><textarea id="coverageJson"></textarea></details><details><summary>问题处置协议详情</summary><textarea id="findingJson"></textarea></details><label>本章决定 <select id="decision"><option value="repair">修改后重新审稿</option><option value="accept">接受当前终稿</option><option value="redirect">调整创作方向</option></select></label><label>调整范围 <select id="redirect"><option value="direction">章节方向</option><option value="outline_revision">大纲规划</option></select></label><label>读者收益说明<input id="gainNote" placeholder="本章带来了哪些新的理解、感受或进展"></label><label>决定理由<input id="reviewReason" placeholder="结合正文与审稿意见说明"></label><div class="toolbar"><button id="reviewPrepare">准备本章审稿表</button><button id="reviewValidate" class="primary">保存并校验审稿决定</button></div><label><input id="reviewApplyAck" type="checkbox"> 我确认采用当前正文版本的人工审稿决定</label><button id="reviewApply" class="primary">确认采用人工审稿决定</button><div id="reviewStatus" class="status"></div></section>
<section><h2>结构化批注</h2><label>问题程度<select id="severity"><option value="P2">一般建议</option><option value="P1">严重问题</option><option value="P0">重大阻断</option></select></label><label>修改方式<select id="action"><option value="rewrite">重写这段</option><option value="expand_scene">展开场景</option><option value="compress">压缩重复</option><option value="clarify">澄清信息</option><option value="reorder">调整顺序</option><option value="replace_carrier">调整呈现方式</option><option value="preserve">保留原文</option></select></label><label>批注对应的阅读问题<select id="checkId"></select></label><input id="intent" placeholder="修改意图"><input id="preserve" placeholder="必须保护项，逗号分隔"><button id="addAnnotation">给选中片段添加批注</button><details><summary>批注协议详情</summary><pre id="annotationView"></pre></details></section>
<section><h2>对话式协作 / 终稿只读咨询</h2><div id="consultPhase" class="muted"></div><textarea id="question" placeholder="围绕选中的正文片段提问"></textarea><div class="toolbar"><button id="consultTask">创建咨询工单</button><button id="consultValidate">校验最新回答</button><button id="consultRecord">记录最新回答</button></div><label>已选修改方案<input id="optionId" readonly placeholder="先在回答中选择方案"></label><input id="optionAdjustment" placeholder="人工调整（可空）"><div class="toolbar"><button id="coeditRewrite">从已记录方案创建完整改写任务</button><button id="coeditCandidateValidate">校验完整协作候选</button></div><div id="consultHistory"></div><div id="consultStatus" class="status"></div></section></aside>
</div>
<script nonce="reviewdesk">
const projectRoute=location.pathname.match(/^\/projects\/(project_[a-f0-9]{20})\/chapters\/(\d+)/);if(projectRoute){document.getElementById("readerBack").href=location.pathname;document.getElementById("readerBack").hidden=false}else document.getElementById("workspaceBack").hidden=true;
const csrf="__CSRF_TOKEN__";let state=null;let selected={start:0,end:0,text:""};let evidence={};let annotations=[];
const dirtyFields=new Set();const draftKey="studio-review:"+location.pathname;let composing=false;let acknowledgedCandidateHash="";
function preserveLocal(){if(composing)return;try{localStorage.setItem(draftKey,JSON.stringify({source:state?.draft?.sha256,evidence,annotations,fields:Object.fromEntries([...dirtyFields].map(id=>[id,$(id)?.value]))}))}catch{show("globalStatus","本机存储不可用，请保存后再离开。","error")}}
function restoreLocal(){if(dirtyFields.size)return;try{const saved=JSON.parse(localStorage.getItem(draftKey)||"null");if(saved){evidence=saved.evidence||evidence;annotations=saved.annotations||annotations;renderEvidence();renderAnnotations();for(const [id,value] of Object.entries(saved.fields)){if($(id)){$(id).value=value;dirtyFields.add(id)}}if(saved.source!==state?.draft?.sha256)show("globalStatus","已恢复旧来源的修改，请核对正文版本。","error")}}catch{}}
document.addEventListener("compositionstart",()=>{composing=true});document.addEventListener("compositionend",()=>{composing=false;preserveLocal()});document.addEventListener("input",e=>{if(!e.target.closest("#revisionNaturalForm,#coverageNaturalForm,#findingNaturalForm")&&e.target.id&&e.target.matches("textarea:not([readonly]),input:not([type=checkbox]),select")){dirtyFields.add(e.target.id);preserveLocal()}});window.addEventListener("beforeunload",e=>{preserveLocal();if(dirtyFields.size){e.preventDefault();e.returnValue=""}});window.addEventListener("storage",e=>{if(e.key===draftKey)show("globalStatus","另一标签页保存了修改；当前文字保持原样。","error")});
const $=id=>document.getElementById(id);const show=(id,value,cls="")=>{const el=$(id);el.textContent=typeof value==="string"?value:JSON.stringify(value,null,2);el.className="status "+cls};
async function api(path,body){const r=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-Review-CSRF":csrf},body:JSON.stringify(body)});const data=await r.json();if(!r.ok)throw new Error(data.error||"request failed");if(path.endsWith("/human-revision/prepare"))acknowledgedCandidateHash=body.expected_candidate_sha256||"";if(path.endsWith("/human-revision/save")){dirtyFields.delete("revisionText");dirtyFields.delete("revisionRecord");for(const id of [...dirtyFields])if($(id)?.closest("#revisionNaturalForm"))dirtyFields.delete(id);acknowledgedCandidateHash=data.result?.candidate_sha256||"";preserveLocal()}if(path.endsWith("/manual-repair/save")){dirtyFields.delete("repairText");preserveLocal()}return data.result}
function utf16ToCodePoint(text,index){return Array.from(text.slice(0,index)).length}
function capture(){const el=$("manuscript");const utf16Start=el.selectionStart,utf16End=el.selectionEnd;selected={start:utf16ToCodePoint(el.value,utf16Start),end:utf16ToCodePoint(el.value,utf16End),text:el.value.slice(utf16Start,utf16End)};if(selected.end<=selected.start)throw new Error("请先圈选正文片段");return selected}
async function load(){const previous=state;const previousEvidence=evidence,previousAnnotations=annotations;const preserved=Object.fromEntries([...dirtyFields].map(id=>[id,$(id)?.value]));const updated=await fetch("/api/state",{credentials:"same-origin"}).then(r=>{if(!r.ok)throw Error("无法读取审稿状态");return r.json()});if(previous&&dirtyFields.size&&updated.consultation_candidate.sha256!==previous.consultation_candidate.sha256&&updated.consultation_candidate.sha256!==acknowledgedCandidateHash){show("globalStatus","正文版本已变化。已保留未保存内容，请比较后处理。","error");return}state=updated;acknowledgedCandidateHash="";$("reviewOrigin").hidden=!state.execution_origin?.simulated_human;$("reviewOrigin").textContent=state.execution_origin?.simulated_human?"自动演练 · 人工步骤为模拟记录，未获文学验收":"";$("title").textContent=`第 ${state.chapter_number} 章 · 修改与审稿`;$("candidate").textContent=state.consultation_candidate.sha256;$("chapterIntent").textContent=Object.entries({story_intent:"故事意图",key_character_choice:"人物关键选择",emotional_truth:"情绪真相",pov_voice_intent:"视角与声音"}).map(([key,label])=>`${label}：\n${state.human_intent_content?.[key]||"尚未填写"}`).join("\n\n");$("checkId").replaceChildren(...state.review_checks.map(check=>{const option=document.createElement("option");option.value=check.id;option.textContent=check.label;return option}));$("brief").textContent=state.story_brief.text;$("contract").textContent=JSON.stringify(state.chapter_contract,null,2);$("promises").textContent=JSON.stringify(state.reader_promises,null,2);$("manuscript").value=state.consultation_candidate.text;$("consultPhase").textContent=state.consultation_candidate.phase==="coedit"?"创作协作阶段：可以选择方案并生成完整候选":"人工终稿阶段：仅提供只读咨询";
$("findings").replaceChildren(...(state.review_barrier.findings||[]).map(f=>{const d=document.createElement("div");d.className="finding";d.textContent=`[${f.severity}] ${f.code||f.finding_id}: ${f.diagnosis||""}`;return d}));
if(!$("findings").children.length)$("findings").textContent="当前审稿没有待处理问题；是否可以定稿仍由完整审稿和人工确认决定。";
$("checks").replaceChildren(...state.review_checks.map(c=>{const l=document.createElement("div");l.className="check";const current=(state.review_template.dimension_coverage||{})[c.id]||{};l.textContent=`${c.label} — ${({human_core:"本人阅读",independent_review:"独立审稿",human_resolution:"本人处理"}[current.coverage_source]||"待覆盖")} / ${({confirmed:"已核对",covered:"已有覆盖",accepted_p2:"接受一般建议",repair:"待修改",redirect:"调整方向"}[current.status]||"待判断")}`;return l}));
const t=state.review_template||{};$("coverageJson").value=JSON.stringify(t.dimension_coverage||{},null,2);$("findingJson").value=JSON.stringify(t.finding_resolutions||[],null,2);evidence=Object.fromEntries((t.evidence_spans||[]).map(x=>[x.kind,x]));annotations=t.annotations||[];renderEvidence();renderAnnotations();renderRevision();renderRepair();renderConsult();$("reviewApply").disabled=state.review_validation?.ok!==true||!state.review_template_sha256;for(const [id,value] of Object.entries(preserved)){if($(id))$(id).value=value}if(dirtyFields.has("__evidence"))evidence=previousEvidence;if(dirtyFields.has("__annotations"))annotations=previousAnnotations;restoreLocal();renderEvidence();renderAnnotations();renderHumanForms();show("globalStatus",`审稿状态：${reviewStatusLabel(state.review_barrier.status)}`)}
function renderEvidence(){$("evidenceView").textContent=JSON.stringify(evidence,null,2)}function renderAnnotations(){$("annotationView").textContent=JSON.stringify(annotations,null,2)}
function renderRevision(){const r=state.human_author_revision||{};$("revisionMeta").textContent=r.available?reviewStatusLabel(r.status):"尚未建立人工修订工作区";$("revisionSource").value=r.source_text||state.draft.text;$("revisionText").value=r.text||state.draft.text;$("revisionRecord").value=JSON.stringify(r.record||{},null,2);$("diff").textContent=r.diff||"暂无修改对照";$("revisionPrepare").disabled=!!r.available;$("revisionText").readOnly=r.editable===false;$("revisionRecord").readOnly=r.editable===false;$("revisionSave").disabled=!r.available||r.editable===false;$("revisionValidate").disabled=!r.available||r.editable===false;$("revisionSubmit").disabled=!r.available||r.status!=="validated_for_submit"}
function renderRepair(){const r=state.manual_repair||{};$("repairMeta").textContent=r.available?`${r.task_id} / ${r.task_status} / 剩余 ${r.attempts.remaining}`:r.reason||"无 repair 工单";$("repairText").value=r.text||state.draft.text;$("repairSave").disabled=!r.available||!r.editable;$("repairSubmit").disabled=!r.available||!r.editable;$("repairPrepare").disabled=!!r.available}
function activeSessions(){return state.consultation_candidate.phase==="coedit"?(state.coedit.sessions||[]):(state.consultations.sessions||[])}
function latestTurn(){const sessions=activeSessions();for(let j=sessions.length-1;j>=0;j--)for(let i=(sessions[j].turns||[]).length-1;i>=0;i--)return {...sessions[j].turns[i],session_id:sessions[j].session_id};return null}
function consultSessions(){return [...(state.coedit.sessions||[]).map(s=>({...s,phase:"coedit"})),...(state.consultations.sessions||[]).map(s=>({...s,phase:"human_final"}))]}
function renderConsult(){const box=$("consultHistory");box.replaceChildren();for(const s of consultSessions())for(const t of s.turns||[]){const section=document.createElement("section"),title=document.createElement("p"),q=document.createElement("p"),quote=document.createElement("blockquote"),answer=document.createElement("div");title.className="muted";const stale=s.phase!==state.consultation_candidate.phase||(s.effective_status||s.status)==="stale"||Boolean(t.response_sha256&&!t.response_current)||t.candidate_current===false;title.textContent=`${s.phase==="coedit"?"创作协作":"终稿咨询"} · 第 ${t.turn_number} 轮 · ${stale?"历史建议，已不可采用":s.status==="active"?"进行中":"已记录"}`;q.textContent=t.request?.question||"原问题未保存";quote.className="selection-quote";quote.textContent=t.request?.selection?.text||t.request?.selection?.excerpt||"";answer.className="consult-answer";answer.textContent=t.response?(t.response_current?"":"待校验的生成结果\n")+t.response:"等待顾问回答";section.append(title,q,quote,answer);for(const option of t.option_ids||[]){const button=document.createElement("button");button.textContent=`选择 ${option}`;button.className="secondary";button.disabled=stale;button.onclick=()=>{$("optionId").value=option;show("consultStatus",`已选择 ${option}，可生成完整候选。`)};section.append(button)}box.append(section)}const coedit=state.consultation_candidate.phase==="coedit";$("coeditRewrite").disabled=!coedit;$("coeditCandidateValidate").disabled=!coedit;renderConsultJobs()}

document.querySelectorAll("[data-evidence]").forEach(b=>b.onclick=()=>{try{const s=capture();evidence[b.dataset.evidence]={kind:b.dataset.evidence,...s,note:$("evidenceNote").value};const coverage=JSON.parse($("coverageJson").value||"{}");for(const [key,kind] of Object.entries({scene_causality_and_key_turn_dramatized:"key_turn",protagonist_agency_voice_and_emotion:"character_choice_or_emotion",reader_gain_and_promise_progress:"reader_gain",exit_state_and_emotional_aftereffect:"reader_gain"})){if(kind===b.dataset.evidence&&coverage[key])coverage[key].evidence_refs=[`candidate:${kind}:${s.start}-${s.end}`]}$("coverageJson").value=JSON.stringify(coverage,null,2);dirtyFields.add("coverageJson");renderCoverageForm();renderEvidence();dirtyFields.add("__evidence");preserveLocal()}catch(e){show("globalStatus",e.message,"error")}});
$("addAnnotation").onclick=()=>{try{const s=capture();annotations.push({annotation_id:`HR-${Date.now()}`,start:s.start,end:s.end,text:s.text,check_id:$("checkId").value,severity:$("severity").value,action:$("action").value,intent:$("intent").value,must_preserve:$("preserve").value.split(",").map(x=>x.trim()).filter(Boolean),note:"由人工在审稿台明确转换"});renderAnnotations();dirtyFields.add("__annotations");preserveLocal()}catch(e){show("reviewStatus",e.message,"error")}};
$("reviewPrepare").onclick=async()=>{try{show("reviewStatus",await api("/api/human-review/prepare",{expected_candidate_sha256:state.draft.sha256}),"ok");await load()}catch(e){show("reviewStatus",e.message,"error")}};
$("reviewValidate").onclick=async()=>{try{const base=state.review_template;if(!base.schema)throw new Error("请先准备深审表");const dimension_coverage=JSON.parse($("coverageJson").value);const finding_resolutions=JSON.parse($("findingJson").value);const review={...base,dimension_coverage,finding_resolutions,decision:$("decision").value,evidence_spans:Object.values(evidence),reader_gain_note:$("gainNote").value,annotations,redirect_scope:$("redirect").value,reason:$("reviewReason").value};show("reviewStatus",await api("/api/human-review/validate",{expected_candidate_sha256:state.draft.sha256,review}),"ok");await load()}catch(e){show("reviewStatus",e.message,"error")}};
$("reviewApply").onclick=async()=>{try{if(!$("reviewApplyAck").checked)throw new Error("请先明确确认采用当前人工深审决定");show("reviewStatus",await api("/api/human-review/apply",{expected_candidate_sha256:state.draft.sha256,expected_review_sha256:state.review_template_sha256,approved_by:"human",acknowledge_human_decision:true}),"ok");await load()}catch(e){show("reviewStatus",e.message,"error")}};
$("consultTask").onclick=async()=>{try{const s=capture();show("consultStatus",await api("/api/consult/task",{phase:state.consultation_candidate.phase,expected_candidate_sha256:state.consultation_candidate.sha256,start:s.start,end:s.end,question:$("question").value}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("consultValidate").onclick=async()=>{try{const t=latestTurn();if(!t)throw new Error("暂无咨询工单");show("consultStatus",await api("/api/consult/validate",{phase:state.consultation_candidate.phase,response_file:t.response_file}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("consultRecord").onclick=async()=>{try{const t=latestTurn();if(!t)throw new Error("暂无咨询工单");show("consultStatus",await api("/api/consult/record",{phase:state.consultation_candidate.phase,response_file:t.response_file}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("coeditRewrite").onclick=async()=>{try{const t=latestTurn();if(!t)throw new Error("暂无已记录协作方案");show("consultStatus",await api("/api/coedit/rewrite-task",{session_id:t.session_id,turn_number:t.turn_number,option_id:$("optionId").value,adjustment:$("optionAdjustment").value}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("coeditCandidateValidate").onclick=async()=>{try{const t=latestTurn();if(!t||!t.rewrite_candidate_file)throw new Error("暂无完整协作候选");show("consultStatus",await api("/api/coedit/candidate-validate",{candidate_file:t.rewrite_candidate_file}),"ok");await load()}catch(e){show("consultStatus",e.message,"error")}};
$("revisionPrepare").onclick=async()=>{try{show("revisionStatus",await api("/api/human-revision/prepare",{expected_candidate_sha256:state.draft.sha256}),"ok");await load()}catch(e){show("revisionStatus",e.message,"error")}};
$("revisionSave").onclick=async()=>{try{const r=state.human_author_revision;show("revisionStatus",await api("/api/human-revision/save",{expected_draft_sha256:state.draft.sha256,expected_candidate_sha256:r.candidate_sha256||"",expected_record_sha256:r.record_sha256||"",text:$("revisionText").value,record:JSON.parse($("revisionRecord").value)}),"ok");await load()}catch(e){show("revisionStatus",e.message,"error")}};
$("revisionValidate").onclick=async()=>{try{show("revisionStatus",await api("/api/human-revision/validate",{expected_draft_sha256:state.draft.sha256}),"ok");await load()}catch(e){show("revisionStatus",e.message,"error")}};
$("revisionSubmit").onclick=async()=>{try{const r=state.human_author_revision;show("revisionStatus",await api("/api/human-revision/submit",{expected_draft_sha256:state.draft.sha256,expected_candidate_sha256:r.candidate_sha256}),"ok");await load()}catch(e){show("revisionStatus",e.message,"error")}};
$("repairPrepare").onclick=async()=>{try{show("repairStatus",await api("/api/manual-repair/prepare",{expected_candidate_sha256:state.draft.sha256}),"ok");await load()}catch(e){show("repairStatus",e.message,"error")}};
$("repairSave").onclick=async()=>{try{show("repairStatus",await api("/api/manual-repair/save",{expected_draft_sha256:state.draft.sha256,expected_candidate_sha256:state.manual_repair.candidate_sha256||"",text:$("repairText").value}),"ok");await load()}catch(e){show("repairStatus",e.message,"error")}};
$("repairSubmit").onclick=async()=>{try{show("repairStatus",await api("/api/human-revision/prepare",{expected_candidate_sha256:state.draft.sha256}),"ok");await load()}catch(e){show("repairStatus",e.message,"error")}};
__REVIEW_LAYOUT__
__REVIEW_FORMS__
__REVIEW_JOBS__
$("reload").onclick=()=>load().catch(e=>show("globalStatus",e.message,"error"));load().catch(e=>show("globalStatus",e.message,"error"));
</script></body></html>'''


__all__ = [
    "REVIEW_ACTION_FIELDS",
    "ReviewDeskService",
    "ReviewHTTPServer",
    "ReviewServerError",
    "dispatch_review_action",
    "review_page_html",
]
