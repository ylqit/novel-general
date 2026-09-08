"""Source-bound, read-only creative discussions on the existing advisor contract."""

from hashlib import sha256
import json
from pathlib import Path
import secrets
from typing import Any

from longform_engine.agent_tasks import build_manifest, write_manifest, load_manifest, update_task_status
from longform_engine.config import load_project_config
from longform_engine.creative_sandbox import create_sandbox_artifact
from longform_engine.storage import acquire_project_lock, atomic_write_text
from longform_engine.studio_content import StudioContent, StudioContentError


class StudioDiscussion:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "50_workbench/human_story_reviews/consultations/studio"

    def _load(self, turn_id: str) -> tuple[Path, dict[str, Any]]:
        import re
        if not re.fullmatch(r"discussion_[0-9a-f]{24}", turn_id):
            raise StudioContentError("讨论 ID 无效")
        path = self.directory / turn_id / "turn.json"
        if not path.resolve().is_relative_to(self.directory.resolve()) or not path.is_file():
            raise StudioContentError("本作品中没有这条讨论")
        return path, json.loads(path.read_text(encoding="utf-8"))

    def history(self) -> dict[str, Any]:
        turns = []
        for path in sorted(self.directory.glob("discussion_*/turn.json"), key=lambda p:p.stat().st_mtime_ns):
            _, turn = self._load(path.parent.name)
            stale = []
            if turn["scope_key"]["scope"] == "volume" and not {
                "20_outline/volume_skeletons.json", "30_state/planning_basis.json",
            }.issubset({binding["path"] for binding in turn["bindings"]}):
                stale.append("旧讨论未绑定批准卷范围，请重新提问")
            for binding in turn["bindings"]:
                source = (self.root / binding["path"]).resolve()
                if not source.is_relative_to(self.root) or not source.is_file() or sha256(source.read_bytes()).hexdigest() != binding["sha256"]:
                    stale.append(binding["label"])
            response = path.parent / "response.md"
            request = path.parent / "request.json"
            if not request.is_file() or sha256(request.read_bytes()).hexdigest() != turn.get("request_sha256"):
                stale.append("本轮问题或历史输入发生变化")
            recorded_hash = turn.get("response_sha256")
            if recorded_hash and (not response.is_file() or sha256(response.read_bytes()).hexdigest() != recorded_hash):
                stale.append("已记录回答发生变化")
            task = load_manifest(self.root, turn["task_id"])
            turns.append({**turn, "task_status": task.get("status"), "stale": bool(stale), "stale_sources": stale,
                          "response": response.read_text(encoding="utf-8") if recorded_hash and response.is_file() else ""})
        return {"turns": turns}

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Scope selection, evidence hashes and task registration form one snapshot.
        config = load_project_config(self.root / "project.yaml")
        with acquire_project_lock(config, command="studio discussion create"):
            return self._create_locked(payload)

    def _create_locked(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"scope", "chapter", "question", "selection", "document_ids"}:
            raise StudioContentError("讨论字段无效")
        scope, chapter, question = payload["scope"], payload["chapter"], payload["question"]
        if scope not in {"project", "volume", "chapter", "selection"} or not isinstance(question, str) or not 1 <= len(question.strip()) <= 6000:
            raise StudioContentError("请选择讨论范围，并填写 1–6000 字的问题")
        content = StudioContent(self.root)
        sources: dict[str, str] = {}
        manifest_scope: dict[str, Any] = {"kind": "project", "chapter_number": 0}
        selected_volume = None
        if scope != "project":
            if type(chapter) is not int or chapter < 1:
                raise StudioContentError("请先选择章节")
            manuscript = content.chapter(chapter)
            if scope == "volume":
                selected = [v for v in content.catalogue()["volumes"] if v["chapter_range"][0] <= chapter <= v["chapter_range"][1]]
                if len(selected) != 1:
                    raise StudioContentError("本章没有唯一的批准卷范围")
                selected_volume = selected[0]
                sources["20_outline/volume_skeletons.json"] = "批准的卷范围"
                sources["30_state/planning_basis.json"] = "卷规划批准依据"
                manifest_scope = {"kind": "range", "from_chapter": selected_volume["chapter_range"][0], "to_chapter": selected_volume["chapter_range"][1]}
            else:
                manifest_scope = {"kind": "chapter", "chapter_number": chapter}
            if manuscript["available"]:
                sources[f"40_manuscript/{manuscript['version']}/ch{chapter:03d}.md"] = f"第 {chapter} 章 {manuscript['version']}"
        ids = payload["document_ids"]
        if not isinstance(ids, list) or len(ids) > 30 or any(not isinstance(v, str) for v in ids):
            raise StudioContentError("参考资料选择无效")
        docs = content.documents()
        # Default scope includes governing design, plus the exact selected chapter
        # or volume plan. Large evidence is blocked by the normal context budget.
        for row in docs:
            relative = row["relative"]
            if (relative.startswith("00_governance/") and relative.endswith(".md")) or relative == "20_outline/book_spine.json":
                sources[relative] = row["title"]
            elif scope in {"chapter", "selection"} and relative in {
                f"20_outline/chapter_contracts/ch{chapter:03d}.json", f"20_outline/chapter_intents/ch{chapter:03d}.json",
                f"20_outline/plot_nodes/ch{chapter:03d}.json"}:
                sources[relative] = row["title"]
            elif selected_volume is not None and relative.startswith("20_outline/volumes/"):
                d = json.loads(content.document(row["id"])["text"])
                if d.get("volume_id") == selected_volume["volume_id"]:
                    sources[relative] = row["title"]
        for document_id in ids:
            document = content.document(document_id)
            sources[document["relative"]] = document["title"]
        selection = payload["selection"]
        if scope == "selection" and not selection:
            raise StudioContentError("选段讨论需要原文引用")
        if selection:
            if not isinstance(selection, dict) or set(selection) != {"chapter_number", "start", "end", "quote", "sha256", "version"}:
                raise StudioContentError("原文引用字段无效")
            selected_text = content.chapter(selection["chapter_number"], version=selection["version"])
            start, end = selection["start"], selection["end"]
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(selected_text["text"])
                    or selected_text["sha256"] != selection["sha256"] or selected_text["text"][start:end] != selection["quote"]):
                raise StudioContentError("引用版本或原文位置已失效，请重新圈选")
            sources[f"40_manuscript/{selected_text['version']}/ch{selection['chapter_number']:03d}.md"] = f"引用第 {selection['chapter_number']} 章"
        if not sources:
            raise StudioContentError("必要上下文不完整，请先保存作品设计或选择参考资料")
        bindings = []
        for relative, label in sorted(sources.items()):
            data = content._read(self.root / relative)
            bindings.append({"path": relative, "sha256": sha256(data).hexdigest(), "label": label})
        scope_key = {"scope": scope, "chapter": chapter if scope in {"chapter", "selection"} else None,
                     "volume": selected_volume["volume_id"] if selected_volume else None}
        history = [{"question": t["question"], "selection": t["selection"], "response": t["response"],
                    "response_sha256": t["response_sha256"]} for t in self.history()["turns"]
                   if not t["stale"] and t["scope_key"] == scope_key and t.get("response_sha256")]
        turn_id = "discussion_" + secrets.token_hex(12)
        folder = self.directory / turn_id
        relative_folder = folder.relative_to(self.root).as_posix()
        task_id = f"human_review_consult:studio:ch{chapter or 0:03d}:{turn_id}:v1"
        task_file = folder / "task.md"
        request_file = folder / "request.json"
        output = folder / "response.md"
        atomic_write_text(request_file, json.dumps({"scope": scope_key, "question": question.strip(),
            "selection": selection, "bindings": bindings, "history": history}, ensure_ascii=False, indent=2) + "\n")
        atomic_write_text(task_file, "# 创作讨论\n\n依据声明材料与 request.json 中的完整有效历史，回答作者本轮问题。"
            "区分批准规划与正文实际发生的事实；指出材料不足，不编造缺失设定。"
            "仅给出分析与可选建议，不改写正式正文，不批准任何设计，不输出命令。"
            "这是只读创作讨论，建议采用由作者另行选择。\n\n"
            "输出 design_document_v1 Markdown，依次包含二级标题：问题复述、证据判断、可选修法、风险与保护项、建议动作。\n")
        inputs = [task_file, request_file, *(self.root / b["path"] for b in bindings)]
        manifest = build_manifest(self.root, task_type="human_review_consult", chapter_number=None,
            scope=manifest_scope, task_id=task_id, input_files=inputs, allowed_output_paths=[output],
            output_schema="design_document_v1", validate_command=f"longform-engine agent-task result-validate project.yaml {task_id} --file {relative_folder}/response.md",
            apply_command=f"longform-engine review discussion-record project.yaml --id {turn_id}",
            failure_next_command=f"longform-engine agent-task brief project.yaml {task_id}",
            context_policy={"required_files": inputs, "compiled_brief": task_file}, role_id="human_author_advisor")
        write_manifest(self.root, manifest, folder / "manifest.json")
        turn = {"schema": "studio_discussion_turn_v1", "id": turn_id, "task_id": task_id,
                "scope_key": scope_key, "question": question.strip(), "selection": selection,
                "request_sha256": sha256(request_file.read_bytes()).hexdigest(),
                "bindings": bindings, "response_sha256": None, "adoptions": []}
        atomic_write_text(folder / "turn.json", json.dumps(turn, ensure_ascii=False, indent=2) + "\n")
        return turn

    def record(self, turn_id: str) -> dict[str, Any]:
        config = load_project_config(self.root / "project.yaml")
        with acquire_project_lock(config, command="studio discussion record"):
            path, turn = self._load(turn_id)
            if next(t for t in self.history()["turns"] if t["id"] == turn_id)["stale"]:
                raise StudioContentError("讨论依据已失效，请针对当前版本重新提问")
            task = load_manifest(self.root, turn["task_id"])
            response = path.parent / "response.md"
            current = task.get("current_result") or {}
            digest = sha256(response.read_bytes()).hexdigest() if response.is_file() else ""
            if not current.get("ok") or current.get("sha256") != digest or task.get("status") not in {"submitted", "validated", "applied"}:
                raise StudioContentError("顾问回答尚未通过当前任务校验")
            if task.get("status") == "applied" and turn.get("response_sha256") == digest:
                return turn
            turn["response_sha256"] = digest
            update_task_status(self.root, turn["task_id"], to_status="validated", command="studio discussion record",
                               artifact=response.relative_to(self.root).as_posix())
            atomic_write_text(path, json.dumps(turn, ensure_ascii=False, indent=2) + "\n")
            update_task_status(self.root, turn["task_id"], to_status="applied", command="studio discussion record",
                               artifact=response.relative_to(self.root).as_posix())
        return turn

    def adopt(self, turn_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"kind", "text"} or payload["kind"] not in {"memo", "intent_draft", "planning_proposal"} or not isinstance(payload["text"], str) or not payload["text"].strip():
            raise StudioContentError("请选择建议去向并填写要保留的内容")
        config = load_project_config(self.root / "project.yaml")
        with acquire_project_lock(config, command="studio discussion adopt"):
            path, turn = self._load(turn_id)
            view = next(t for t in self.history()["turns"] if t["id"] == turn_id)
            if view["stale"] or not turn.get("response_sha256"):
                raise StudioContentError("未记录或已失效的建议不能采用")
            result = create_sandbox_artifact(config, document_type=payload["kind"], title=f"创作讨论：{turn['question'][:50]}",
                                            body=payload["text"], created_by="human")
            turn["adoptions"].append({"kind": payload["kind"], "file": result["file"], "response_sha256": turn["response_sha256"]})
            atomic_write_text(path, json.dumps(turn, ensure_ascii=False, indent=2) + "\n")
            return result
