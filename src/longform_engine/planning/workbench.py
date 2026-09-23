"""Manifest-backed planning generation, isolated review and explicit approval."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import secrets
from typing import Any

from longform_engine.agent_tasks import (
    build_manifest, load_manifest, manifest_input_paths, manifest_output,
    validate_manifest_strict, write_manifest, update_task_status,
)
from longform_engine.config import ConfigDocument
from longform_engine.chapter_contract import TOPOLOGIES
from longform_engine.reader_promises_v2 import PROMISE_ACTIONS, PROMISE_SCHEMA
from longform_engine.resources import resource_path
from longform_engine.prompting import estimate_text_units, resolve_context_budget_contract
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import manuscript_chapter_relative_path
from .contracts import (
    NODE_KINDS, NODE_REQUIREMENTS, PRECONDITION_TYPES, SEMANTIC_DOMAINS,
    canonical_json_hash, validate_planning_bundle,
)
from .task import write_planning_generation_task
from .workflow import (
    REVIEW_PROFILES, apply_planning_bundle, build_human_node_decisions,
    build_human_planning_approval, build_planning_semantic_application,
    validate_planning_semantic_application, write_workbench_record,
)


class PlanningWorkbench:
    """Own the current planning attempt; all mutations run under the caller's lock."""

    def __init__(self, config: ConfigDocument):
        self.config = config
        self.root = resolve_project_root(config)
        self.pointer = self.root / "50_workbench/planning/active_attempt.json"

    def state(self) -> dict[str, Any]:
        if not self.pointer.is_file():
            return {"status": "not_started"}
        record = json.loads(self.pointer.read_text(encoding="utf-8"))
        author = load_manifest(self.root, record["author_task_id"])
        view = {**record, "status": author["status"], "author": author, "bundle": None, "review": None, "output_errors": []}
        directory = (self.root / manifest_output(author)["path"]).parent
        bundle = directory / "bundle.json"
        if bundle.is_file():
            try:
                view.update(bundle=json.loads(bundle.read_text(encoding="utf-8")), bundle_sha256=sha256(bundle.read_bytes()).hexdigest())
            except (ValueError, UnicodeError):
                view["output_errors"].append("规划候选格式无效，请重建当前任务")
        if record.get("reviewer_task_id"):
            reviewer = load_manifest(self.root, record["reviewer_task_id"])
            view["reviewer"] = reviewer
            if reviewer["status"] == "applied":
                view["status"] = "applied"
            review_file = self.root / manifest_output(reviewer)["path"]
            if review_file.is_file():
                try:
                    view["review"] = json.loads(review_file.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError):
                    view["output_errors"].append("审查输出尚非有效 JSON，等待任务结束后查看校验结果")
            application = directory / "application.json"
            if application.is_file():
                validation = validate_planning_semantic_application(self.root, json.loads(application.read_text(encoding="utf-8")))
                view["review_validation"] = asdict(validation)
        errors = validate_manifest_strict(self.root, author, strict=True).errors
        view["stale_reasons"] = list(errors) if view["status"] != "applied" else []
        return view

    def create(self, *, rebuild: bool = False, proposals: list[dict[str, str]] | None = None) -> dict[str, Any]:
        from longform_engine.production import production_next
        from longform_engine.studio_learning import StudioLearning

        current = self.state()
        if current["status"] not in {"not_started", "applied"} and not rebuild:
            if proposals:
                raise ValueError("当前规划工作单不可变；更换提案请明确重建规划")
            return current
        proposal_sources = StudioLearning(self.config).proposal_inputs([] if proposals is None else proposals)
        action = production_next(self.config)
        if action["status"] != "planning_refresh_required":
            raise ValueError("当前生产步骤尚不允许重建规划：" + action["status"])
        chapter = int(action["chapter_number"])
        run_id = "plan_" + secrets.token_hex(8)
        directory = self.root / "50_workbench/planning" / run_id
        generation = write_planning_generation_task(self.config)
        task_file = directory / "generation.md"
        shape = directory / "bundle_shape.json"
        atomic_write_text(shape, resource_path("templates", "planning", "bundle_shape.json").read_text(encoding="utf-8"))
        budget = resolve_context_budget_contract(self.root)
        field_rules = {
            "chapter_contract.topology": sorted(TOPOLOGIES),
            "semantic_obligation.domain": sorted(SEMANTIC_DOMAINS),
            "node.node_kind": sorted(NODE_KINDS), "node.requirement": sorted(NODE_REQUIREMENTS),
            "precondition.type": sorted(PRECONDITION_TYPES), "promise_action.action": sorted(PROMISE_ACTIONS),
            "promise_thread_shape": {"schema": PROMISE_SCHEMA, "promise_id": "", "reader_expectation": "", "owner_ref": "",
                "payoff_window": {"earliest": 0, "target": 0, "latest": 0},
                "staged_payoffs": [{"stage_id": "", "description": "", "window": [0, 0]}], "selected_by": None},
            "promise_action_shape": {"promise_id": "", "action": "", "stage_id": None,
                "intended_reader_gain": "", "evidence_requirement": "", "defer_reason": None},
        }
        atomic_write_text(task_file, resource_path("templates", "planning", "generation.md").read_text(encoding="utf-8")
                          + f"\n本轮待写章节：第 {chapter} 章。\n"
                          + f"当前上下文档位为 {budget.profile}，后续单份审查材料上限为 {budget.input_hard_units} 估算单位。"
                          + "候选内部完整 planning_bundle 需要在此范围内；保留所有必需字段与依赖，通过避免重复长句控制体量。"
                          + "单位估算参数：" + json.dumps(budget.estimator, ensure_ascii=False) + "。\n"
                          + "\n## 当前字段规则\n枚举来自现行校验器；shape 的零和空串为占位，填写有依据的内容。承诺列表可以为空。\n```json\n"
                          + json.dumps(field_rules, ensure_ascii=False, indent=2) + "\n```\n")
        sources = [self.config.path, self.root / generation.contract_file, task_file, shape]
        if rebuild and current.get("author"):
            previous_output = self.root / manifest_output(current["author"])["path"]
            if not previous_output.is_file():
                references = [self.root / relative for relative in manifest_input_paths(current["author"])
                              if relative.startswith("50_workbench/planning/") and relative.endswith("/author.json")]
                if len(references) == 1:
                    previous_output = references[0]
            if previous_output.is_file():
                previous_text = previous_output.read_text(encoding="utf-8")
                reference = previous_output
                if estimate_text_units(previous_text) > budget.input_hard_units:
                    try:
                        previous = json.loads(previous_text)
                    except ValueError:
                        previous = {}
                    body = previous.get("body") if isinstance(previous, dict) else ""
                    body = body if isinstance(body, str) else ""
                    limit = min(6000, budget.input_hard_units // 3)
                    reference = directory / "previous_candidate_excerpt.json"
                    atomic_write_text(reference, json.dumps({
                        "schema": "unapproved_planning_excerpt_v1", "approved": False,
                        "source_path": previous_output.relative_to(self.root).as_posix(),
                        "source_sha256": sha256(previous_output.read_bytes()).hexdigest(),
                        "notice": "上一轮候选过大，仅摘录其自然语言构思。不是批准依据；完整规划须以当前设计重新编写。",
                        "body_excerpt": body[:limit], "truncated": len(body) > limit,
                    }, ensure_ascii=False, indent=2) + "\n")
                sources.append(reference)
                previous_validation = previous_output.parent / "structural_validation.json"
                if previous_validation.is_file():
                    diagnostic = json.loads(previous_validation.read_text(encoding="utf-8"))
                    if isinstance(diagnostic, dict) and diagnostic.get("author_sha256") == sha256(previous_output.read_bytes()).hexdigest():
                        sources.append(previous_validation)
                atomic_write_text(task_file, task_file.read_text(encoding="utf-8")
                    + "\n## 本次重建\n上一轮未批准候选作为参考输入："
                    + reference.relative_to(self.root).as_posix()
                    + "。它不是事实依据或批准规划。可以保留仍有当前设计依据的创作内容，修正协议和领域问题后写入本轮唯一新输出；"
                    "旧候选不得改写。以当前完整输出模板为准，保留外层全部必需字段及预填 artifact。\n")
        for folder in ("00_governance", "10_bible"):
            sources.extend(p for p in sorted((self.root / folder).rglob("*")) if p.is_file() and p.suffix in {".md", ".json"}
                           and p.relative_to(self.root).as_posix() not in {
                               "10_bible/style_profiles/adaptation_profile.json", "10_bible/style_profiles/current_style_profile.json"})
        sources.extend(proposal_sources)
        if proposal_sources:
            atomic_write_text(task_file, task_file.read_text(encoding="utf-8") + "\n## 人工选择的本轮建议\n"
                "以下非 Canon 提案仅供本轮考虑，不得覆盖批准设计或正文事实。依适用目标、条件、保护项评估；"
                "在候选 body 逐项说明采用、调整或不采用及理由。采用内容必须落实在正式规划和章节合同中，仍需独立审查和逐节点人工批准。\n"
                + "\n".join(path.relative_to(self.root).as_posix() for path in proposal_sources) + "\n")
        for relative in ("20_outline/book_spine.json", "20_outline/volume_skeletons.json", "20_outline/rolling_window.json",
                         "30_state/reader_promise_ledger.json", "30_state/story_graph.json"):
            if (self.root / relative).is_file():
                sources.append(self.root / relative)
        sources.extend(sorted((self.root / "20_outline/volumes").glob("vol*.json")))
        for n in range(max(1, chapter - 2), chapter):
            for relative in (manuscript_chapter_relative_path(n, lane="final"), f"30_state/semantic_ledger/ch{n:03d}.json"):
                path = self.root / relative
                if not path.is_file():
                    raise ValueError("前章事实证据不完整：" + relative)
                sources.append(path)
        sources = list(dict.fromkeys(sources))
        task_id = f"planning_generation:{run_id}"
        output = directory / "author.json"
        manifest = build_manifest(self.root, task_type="planning_generation", chapter_number=None, scope={"kind": "project"},
            task_id=task_id, input_files=sources, allowed_output_paths=[output], output_schema="semantic_document_v1",
            validate_command=f"longform-engine agent-task result-validate project.yaml {task_id} --file {output.relative_to(self.root).as_posix()}",
            apply_command=f"longform-engine planning prepare-review project.yaml --task-id {task_id}",
            failure_next_command="longform-engine planning task project.yaml --rebuild", context_policy={"required_files": sources, "compiled_brief": task_file})
        previous = [current[k] for k in ("author_task_id", "reviewer_task_id") if current.get(k)] if rebuild else []
        write_manifest(self.root, manifest, directory / "author.manifest.json", supersedes_task_ids=previous)
        atomic_write_text(self.pointer, json.dumps({"run_id": run_id, "chapter_number": chapter, "author_task_id": task_id,
            "selected_proposals": proposals or []}, ensure_ascii=False, indent=2) + "\n")
        return self.state()

    def prepare_review(self, task_id: str) -> dict[str, Any]:
        state = self.state()
        if task_id != state.get("author_task_id"):
            raise ValueError("规划任务不属于当前尝试")
        if state.get("reviewer_task_id"):
            return state
        author = state["author"]
        output = self._validated_output(author)
        candidate = json.loads(output.read_text(encoding="utf-8"))
        bundle = candidate.get("extensions", {}).get("planning_bundle")
        if not isinstance(bundle, dict):
            raise ValueError("规划候选必须提供 extensions.planning_bundle")
        bundle = json.loads(json.dumps(bundle))
        for field, kind in (("active_volume_plan", dict), ("rolling_window", dict), ("plot_node_tables", list), ("chapter_contracts", list)):
            if not isinstance(bundle.get(field), kind):
                raise ValueError(f"规划结构校验失败：{field} 缺失或类型错误")
        # These bindings describe the actual candidate bytes, not invented Agent hashes.
        forbidden = {"candidate_sha256", "basis_sha256", "approved_by", "human_decision", "lifecycle"}
        def reject_control_fields(value: Any) -> None:
            if isinstance(value, dict):
                if forbidden.intersection(value):
                    raise ValueError("规划候选不得填写控制面字段：" + ", ".join(sorted(forbidden.intersection(value))))
                for child in value.values():
                    reject_control_fields(child)
            elif isinstance(value, list):
                for child in value:
                    reject_control_fields(child)
        reject_control_fields(bundle)
        bundle["active_volume_plan"].update(lifecycle="proposed", approved_by=None)
        bundle["rolling_window"]["basis_sha256"] = canonical_json_hash(author["io"]["inputs"])
        if bundle["rolling_window"].get("start_chapter") != state["chapter_number"]:
            raise ValueError("规划窗口没有从当前待写章节开始")
        tables = {}
        for table in bundle["plot_node_tables"]:
            if not isinstance(table, dict) or not isinstance(table.get("table_id"), str) or not isinstance(table.get("nodes"), list):
                raise ValueError("规划结构校验失败：节点表必须包含有效 table_id 和 nodes")
            for node in table["nodes"]:
                if not isinstance(node, dict):
                    raise ValueError("规划结构校验失败：节点必须是对象")
                node["human_decision"] = None
            table["candidate_sha256"] = canonical_json_hash(table)
            tables[table["table_id"]] = table
        for contract in bundle["chapter_contracts"]:
            ref = contract.get("plot_node_table_ref") if isinstance(contract, dict) else None
            if not isinstance(ref, dict) or not isinstance(ref.get("table_id"), str):
                raise ValueError("规划结构校验失败：章节合同缺少有效节点表引用")
            if ref["table_id"] not in tables:
                raise ValueError("章节合同引用了不存在的节点表")
            ref["candidate_sha256"] = tables[ref["table_id"]]["candidate_sha256"]
        structural = validate_planning_bundle(bundle)
        write_workbench_record(self.root, (output.parent / "structural_validation.json").relative_to(self.root).as_posix(), {
            "schema": "planning_author_structural_validation_v1", "author_sha256": sha256(output.read_bytes()).hexdigest(),
            "validation": structural.as_dict(),
        })
        if not structural.ok:
            raise ValueError("规划结构校验失败：" + "; ".join(structural.errors))
        directory = output.parent
        bundle_path = write_workbench_record(self.root, (directory / "bundle.json").relative_to(self.root).as_posix(), bundle)
        binding_path = write_workbench_record(self.root, (directory / "compilation.json").relative_to(self.root).as_posix(), {
            "schema": "planning_compilation_binding_v1", "author_task_id": task_id,
            "author_sha256": sha256(output.read_bytes()).hexdigest(), "bundle_sha256": sha256(bundle_path.read_bytes()).hexdigest(),
        })
        task_file = directory / "review.md"
        atomic_write_text(task_file, "# 独立规划审查\n\n审查 bundle.json 的计划是否有因果和人物依据，不读取作者推理。\n"
            "检查各章节点是否完成自己的职责及语义义务，三章之间能否成立。用实际 JSON 原文位置作为 evidence_id（路径@起点:终点）；保护项还需批准设计文件 canonical_refs。\n"
            + json.dumps(REVIEW_PROFILES["architecture"], ensure_ascii=False, indent=2) + "\n")
        sources = [self.root / path for path in manifest_input_paths(author) if not path.startswith("50_workbench/")]
        # The review receives every source of the selected suggestions, including
        # workbench evidence. The mutable attempt pointer cannot add or drop inputs.
        from longform_engine.studio_learning import StudioLearning
        selected_sources = StudioLearning(self.config).proposal_inputs(state.get("selected_proposals", []))
        selected_paths = {path.relative_to(self.root).as_posix() for path in selected_sources}
        author_paths = set(manifest_input_paths(author))
        bound_proposals = {path for path in author_paths if path.startswith(("50_workbench/创作沙盒/", "50_workbench/reader_feedback/"))}
        if not selected_paths.issubset(author_paths) or not bound_proposals.issubset(selected_paths):
            raise ValueError("本轮提案选择与不可变作者工作单不一致，请重建规划")
        sources.extend(selected_sources)
        if selected_sources:
            atomic_write_text(task_file, task_file.read_text(encoding="utf-8") + "\n## 人工选择的非 Canon 建议\n"
                "依据随附的技法采用或读者反馈提案及原始来源，检查候选中相关变化是否适合本作、目标卷章和适用条件，"
                "是否保留人物声音、知识边界与提案保护项。建议没有高于批准设计或正文事实的权威；不适合的建议无需强制采用，"
                "不能仅因未采用建议判定阻断问题。审查候选的实际内容，不猜测作者的采用理由。\n")
        sources.extend((bundle_path, binding_path, task_file))
        reviewer_id = f"planning_semantic_review:{state['run_id']}"
        result_path = directory / "review.json"
        reviewer = build_manifest(self.root, task_type="planning_semantic_review", chapter_number=None, scope={"kind": "project"}, task_id=reviewer_id,
            input_files=sources, allowed_output_paths=[result_path], output_schema="evidence_review_v2",
            validate_command=f"longform-engine agent-task result-validate project.yaml {reviewer_id} --file {result_path.relative_to(self.root).as_posix()}",
            apply_command=f"longform-engine planning review-validate project.yaml --task-id {reviewer_id}",
            failure_next_command="longform-engine planning task project.yaml --rebuild", context_policy={"required_files": sources, "compiled_brief": task_file})
        update_task_status(self.root, task_id, to_status="validated", command="planning prepare-review", result=bundle_path)
        # The reviewer sees the compiled subject plus a hash-only binding. A
        # consumes edge requires copying the entire parent output, including
        # author reasoning, into reviewer inputs. Keep these independent tasks
        # pending together until the single approved planning transaction.
        write_manifest(self.root, reviewer, directory / "review.manifest.json")
        record = {key: state[key] for key in ("run_id", "chapter_number", "author_task_id")}
        record["selected_proposals"] = state.get("selected_proposals", [])
        record["reviewer_task_id"] = reviewer_id
        atomic_write_text(self.pointer, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
        return self.state()

    def validate_review(self, task_id: str) -> dict[str, Any]:
        state = self.state()
        if task_id != state.get("reviewer_task_id"):
            raise ValueError("审查任务不属于当前规划")
        reviewer = state["reviewer"]
        output = self._validated_output(reviewer)
        binding = json.loads((output.parent / "compilation.json").read_text(encoding="utf-8"))
        author_output = self._validated_output(state["author"])
        if binding["author_sha256"] != sha256(author_output.read_bytes()).hexdigest() or binding["bundle_sha256"] != sha256((output.parent / "bundle.json").read_bytes()).hexdigest():
            raise ValueError("规划编译来源绑定已失效，请重建规划")
        application = build_planning_semantic_application(self.root, subject_path=output.parent / "bundle.json", profile="architecture",
            author_task_id=state["author_task_id"], author_role_id=state["author"]["role"]["id"], reviewer_task_id=task_id,
            reviewer_role_id=reviewer["role"]["id"], reviewer_version=reviewer["role"]["version"], review_result_path=output)
        validation = validate_planning_semantic_application(self.root, application)
        if not validation.ok:
            raise ValueError("规划审查证据无效：" + "; ".join(validation.errors))
        write_workbench_record(self.root, (output.parent / "application.json").relative_to(self.root).as_posix(), application)
        update_task_status(self.root, task_id, to_status="validated", command="planning review-validate", result=output.parent / "application.json")
        return self.state()

    def approve(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"expected_sha256", "reason", "decisions", "acknowledge"} or payload["acknowledge"] is not True:
            raise ValueError("请逐节点决定并明确确认规划")
        state = self.state()
        if state.get("bundle_sha256") != payload["expected_sha256"]:
            raise ValueError("规划候选已变化，请重新阅读")
        if state["status"] == "applied":
            return state
        self.validate_review(state["reviewer_task_id"])
        self._validated_output(state["author"])
        review_output = self._validated_output(state["reviewer"])
        directory = review_output.parent
        approval = build_human_planning_approval(self.root, application_path=directory / "application.json", decision="approve", reason=payload["reason"], approved_by="human")
        decisions = build_human_node_decisions(self.root, bundle_path=directory / "bundle.json", decisions=payload["decisions"], decided_by="human")
        write_workbench_record(self.root, (directory / "approval.json").relative_to(self.root).as_posix(), approval)
        write_workbench_record(self.root, (directory / "decisions.json").relative_to(self.root).as_posix(), decisions)
        result = apply_planning_bundle(self.config, bundle_path=directory / "bundle.json", application_path=directory / "application.json",
            approval_path=directory / "approval.json", node_decisions_path=directory / "decisions.json", approved_by="human",
            agent_task_ids=(state["author_task_id"], state["reviewer_task_id"]))
        return {**self.state(), "application": asdict(result)}

    def _validated_output(self, manifest: dict[str, Any]) -> Path:
        validation = validate_manifest_strict(self.root, manifest, strict=True)
        if not validation.ok:
            raise ValueError("规划工作单已失效：" + "; ".join(validation.errors))
        path = (self.root / manifest_output(manifest)["path"]).resolve()
        current = manifest.get("current_result") or {}
        if not path.is_relative_to(self.root) or not path.is_file() or not current.get("ok") or current.get("sha256") != sha256(path.read_bytes()).hexdigest():
            raise ValueError("规划输出未通过当前任务校验")
        return path
