"""Source-bound, non-canonical craft and feedback handoff to the planning owner."""

from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.agent_tasks import resolve_under_root
from longform_engine.config import ConfigDocument
from longform_engine.reader_feedback import (
    DECISION_SCHEMA, PLANNING_PROPOSAL_SCHEMA, STABLE_ID,
    record_reader_feedback_batch, record_reader_feedback_decision,
    convert_reader_feedback_to_proposal, validate_reader_feedback_batch, validate_reader_feedback_decision,
)
from longform_engine.semantic_protocols import build_semantic_document, canonical_json_hash, validate_semantic_document
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.studio_content import StudioContent, StudioContentError, MAX_DOCUMENT_BYTES


class StudioLearning:
    """Own immutable proposals, their provenance, and explicit per-attempt selection."""

    def __init__(self, config: ConfigDocument):
        self.config = config
        self.root = resolve_project_root(config)

    def read(self, relative: str) -> tuple[Path, bytes, dict[str, Any]]:
        path = resolve_under_root(self.root, relative)
        if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise ValueError("来源不存在或超过读取上限：" + relative)
        raw = path.read_bytes()
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("来源不是 JSON 对象：" + relative)
        return path, raw, payload

    def proposal_inputs(self, selected: list[dict[str, str]]) -> list[Path]:
        if not isinstance(selected, list) or len(selected) > 8:
            raise ValueError("一次规划最多选择八项提案")
        inventory = {row["id"]: row for row in self.proposals()}
        paths: list[Path] = []
        seen: set[str] = set()
        for choice in selected:
            if not isinstance(choice, dict) or set(choice) != {"id", "sha256"}:
                raise ValueError("规划选择必须包含提案及当前版本")
            row = inventory.get(choice["id"])
            if row is None or row["id"] in seen or row["sha256"] != choice["sha256"] or row["issues"]:
                raise ValueError("提案缺失、重复或来源已失效，请重新核对选择")
            seen.add(row["id"])
            paths.extend(resolve_under_root(self.root, relative) for relative in row["inputs"])
        return list(dict.fromkeys(paths))

    def profile_inputs(self, relative: str, digest: str) -> list[Path]:
        from longform_engine.intelligence.pipeline import validate_adaptation_analysis, validate_style_analysis
        if not relative.startswith("10_bible/style_profiles/"):
            raise ValueError("技法来源必须属于本作已保存的风格分析")
        source, raw, profile = self.read(relative)
        if sha256(raw).hexdigest() != digest:
            raise ValueError("技法来源版本已变化")
        validators = {"adaptation_analysis_v1": validate_adaptation_analysis, "semantic_style_profile_v1": validate_style_analysis}
        validator = validators.get(str(profile.get("schema") or ""))
        if validator is None:
            raise ValueError("不支持的技法分析协议")
        errors: list[str] = []
        validator(profile, errors)
        if errors:
            raise ValueError("技法分析无效：" + "；".join(errors))
        if set(profile["source_files"]) != set(profile["source_hashes"]):
            raise ValueError("技法分析缺少完整来源版本")
        inputs = [source]
        for path, expected in profile["source_hashes"].items():
            evidence = resolve_under_root(self.root, path)
            if not evidence.is_file() or evidence.stat().st_size > MAX_DOCUMENT_BYTES or sha256(evidence.read_bytes()).hexdigest() != expected:
                raise ValueError("技法分析的原始依据缺失或已变化：" + path)
            inputs.append(evidence)
        return inputs

    def proposals(self) -> list[dict[str, Any]]:
        rows = []
        files = [*(self.root / "50_workbench/创作沙盒").glob("*.json"),
                 *(self.root / "50_workbench/reader_feedback").glob("*.proposal.json")]
        for file in sorted(files):
            relative = file.relative_to(self.root).as_posix()
            title = file.stem
            try:
                _, raw, payload = self.read(relative)
                if isinstance(payload.get("title"), str):
                    title = payload["title"]
                extensions = payload.get("extensions")
                craft = extensions.get("craft_adoption") if isinstance(extensions, dict) else None
                if craft is not None and not isinstance(craft, dict):
                    raise ValueError("技法采用的来源绑定格式无效")
                if not craft and payload.get("schema") != PLANNING_PROPOSAL_SCHEMA:
                    continue
                issues = []
                inputs = [relative]
                if craft:
                    issues.extend(validate_semantic_document(payload))
                    if craft.get("project") != sha256(str(self.root).encode()).hexdigest():
                        issues.append("提案属于其他项目")
                    inputs.extend(path.relative_to(self.root).as_posix() for path in self.profile_inputs(craft["source"]["path"], craft["source"]["sha256"]))
                    title = payload["title"]
                else:
                    proposal_id = payload["proposal_id"]
                    if not proposal_id.startswith("feedback.") or not proposal_id.endswith(".planning"):
                        raise ValueError("反馈提案 ID 无效")
                    batch_id = proposal_id[len("feedback."):-len(".planning")]
                    if not STABLE_ID.fullmatch(batch_id):
                        raise ValueError("反馈批次 ID 无效")
                    batch_path = f"50_workbench/reader_feedback/{batch_id}.json"
                    decision_path = f"50_workbench/reader_feedback/{batch_id}.decision.json"
                    _, _, batch = self.read(batch_path)
                    _, _, decision = self.read(decision_path)
                    issues.extend(validate_reader_feedback_batch(batch))
                    issues.extend(validate_reader_feedback_decision(batch, decision))
                    accepted = {item["hypothesis_id"] for item in decision["decisions"] if item["decision"] == "accept" and item["target"] == "planning"}
                    hypotheses = [item for item in batch["hypotheses"] if item["hypothesis_id"] in accepted]
                    if not hypotheses:
                        raise ValueError("反馈提案没有人工接受的规划假设")
                    if (payload.get("source_batch_sha256") != canonical_json_hash(batch)
                            or payload.get("source_decision_sha256") != canonical_json_hash(decision)
                            or payload.get("hypotheses") != hypotheses):
                        issues.append("反馈提案与人工决定不一致")
                    inputs.extend((batch_path, decision_path))
                    title = f"第 {batch['scope']['from_chapter']}–{batch['scope']['to_chapter']} 章反馈 · {hypotheses[0]['statement'][:60]}"
                rows.append({"id": "proposal_" + sha256(relative.encode()).hexdigest()[:24],
                             "title": title, "path": relative, "sha256": sha256(raw).hexdigest(),
                             "kind": "craft" if craft else "feedback", "issues": issues, "inputs": inputs, "content": payload})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                rows.append({"id": "proposal_" + sha256(relative.encode()).hexdigest()[:24], "title": title,
                             "path": relative, "sha256": "", "kind": "invalid", "issues": [str(exc)], "inputs": []})
        return rows

    def state(self) -> dict[str, Any]:
        sources, batches, issues = [], [], []
        for doc in StudioContent(self.root).documents(directories={"10_bible"}):
            if doc["relative"] not in {"10_bible/style_profiles/adaptation_profile.json", "10_bible/style_profiles/current_style_profile.json"}:
                continue
            try:
                _, raw, profile = self.read(doc["relative"])
                digest = sha256(raw).hexdigest()
                self.profile_inputs(doc["relative"], digest)
                sources.append({**doc, "sha256": digest, "profile": profile})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                issues.append(str(exc))
        for path in sorted((self.root / "50_workbench/reader_feedback").glob("*.json")):
            if path.name.endswith((".decision.json", ".proposal.json")):
                continue
            try:
                _, _, batch = self.read(path.relative_to(self.root).as_posix())
                errors = validate_reader_feedback_batch(batch)
                if errors:
                    raise ValueError("；".join(errors))
                decision = None
                if path.with_name(path.stem + ".decision.json").is_file():
                    _, _, decision = self.read(path.with_name(path.stem + ".decision.json").relative_to(self.root).as_posix())
                    errors = validate_reader_feedback_decision(batch, decision)
                batches.append({"batch": batch, "sha256": canonical_json_hash(batch), "decision": decision, "issues": errors})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                issues.append(str(exc))
        proposals = self.proposals()
        current_proposals = {row["path"] for row in proposals if not row["issues"]}
        effects = []
        for path in sorted((self.root / "50_workbench/创作沙盒").glob("effect_*.json")):
            try:
                _, _, effect = self.read(path.relative_to(self.root).as_posix())
                binding = effect["extensions"]["craft_effect"]
                final = StudioContent(self.root).chapter(binding["chapter_number"], version="final")
                _, proposal_raw, _ = self.read(binding["proposal_path"])
                current = (binding["proposal_path"] in current_proposals and final["sha256"] == binding["final_sha256"]
                           and sha256(proposal_raw).hexdigest() == binding["proposal_sha256"])
                effects.append({"title": effect["title"], "body": effect["body"], "binding": binding, "current": current})
            except (ValueError, OSError, KeyError, TypeError) as exc:
                issues.append(str(exc))
        return {"sources": sources, "proposals": proposals, "batches": batches, "effects": effects, "issues": issues,
                "quality": StudioContent(self.root).quality_history()}

    def act(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        # Caller holds the project lock; approval checks precede every write.
        if body.get("acknowledge") is not True:
            raise ValueError("请明确确认本次人工记录")
        resolve_under_root(self.root, "50_workbench/创作沙盒")
        resolve_under_root(self.root, "50_workbench/reader_feedback")
        if action == "effect":
            if set(body) != {"proposal_id", "proposal_sha256", "chapter_number", "final_sha256", "quote", "assessment", "note", "acknowledge"}:
                raise ValueError("效果回查字段无效")
            proposal = next((row for row in self.proposals() if row["id"] == body["proposal_id"] and row["kind"] == "craft"), None)
            if not proposal or proposal["sha256"] != body["proposal_sha256"] or proposal["issues"]:
                raise ValueError("采用提案已变化，请重新核对")
            if type(body["chapter_number"]) is not int or body["chapter_number"] < 1:
                raise ValueError("章节号无效")
            final = StudioContent(self.root).chapter(body["chapter_number"], version="final")
            quote = body["quote"]
            if not final["available"] or final["sha256"] != body["final_sha256"]:
                raise ValueError("正式正文版本已变化")
            if not isinstance(quote, str) or not quote.strip() or final["text"].count(quote) != 1:
                raise ValueError("回查引用必须能在正式正文中唯一定位")
            if body["assessment"] not in {"effective", "ineffective", "uncertain"} or not isinstance(body["note"], str) or not body["note"].strip():
                raise ValueError("请填写人工效果判断及依据")
            binding = {"proposal_path": proposal["path"], "proposal_sha256": proposal["sha256"],
                       "chapter_number": body["chapter_number"], "final_sha256": final["sha256"],
                       "start": final["text"].index(quote), "end": final["text"].index(quote) + len(quote), "assessment": body["assessment"]}
            digest = canonical_json_hash({**binding, "note": body["note"]})
            path = resolve_under_root(self.root, f"50_workbench/创作沙盒/effect_{digest[:24]}.json")
            if not path.exists():
                document = build_semantic_document(document_id=f"effect_{digest[:24]}", document_type="craft_effect",
                    title="技法效果回查 · " + proposal["title"], scope={"kind": "creative_sandbox", "project": self.root.name},
                    continuity="人工回查（非 Canon）", body=body["note"], created_by="human",
                    extensions={"sandbox": True, "canonical": False, "materialize_graph": False, "materialize_rag": False, "craft_effect": binding})
                atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            return {"path": path.relative_to(self.root).as_posix(), "canonical": False}
        if action == "adopt":
            fields = {"source_id", "source_sha256", "title", "target", "conditions", "transformation", "protect", "expected_effect", "acknowledge"}
            if set(body) != fields or any(not isinstance(body.get(k), str) or not body[k].strip() or len(body[k]) > 6000 for k in fields - {"acknowledge"}):
                raise ValueError("请填写来源、适用目标、条件、转化、保护项与预期效果")
            source = next((item for item in self.state()["sources"] if item["id"] == body["source_id"]), None)
            if source is None or source["sha256"] != body["source_sha256"]:
                raise ValueError("技法来源已失效，请重新阅读")
            adoption = {k: body[k] for k in ("target", "conditions", "transformation", "protect", "expected_effect")}
            adoption.update(project=sha256(str(self.root).encode()).hexdigest(), source={"path": source["relative"], "sha256": source["sha256"]})
            digest = canonical_json_hash({"title": body["title"], **adoption})
            path = resolve_under_root(self.root, f"50_workbench/创作沙盒/craft_{digest[:24]}.json")
            if not path.exists():
                document = build_semantic_document(document_id=f"craft_{digest[:24]}", document_type="craft_adoption",
                    title=body["title"], scope={"kind": "creative_sandbox", "project": self.root.name}, continuity="创作沙盒（非 Canon）",
                    body="\n\n".join(f"{key}：{body[key]}" for key in ("target", "conditions", "transformation", "protect", "expected_effect")),
                    extensions={"sandbox": True, "canonical": False, "materialize_graph": False, "materialize_rag": False, "craft_adoption": adoption}, created_by="human")
                atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
            return {"path": path.relative_to(self.root).as_posix(), "canonical": False}
        if action == "feedback-record":
            if set(body) != {"acknowledge", "batch"}:
                raise ValueError("反馈记录字段无效")
            return asdict(record_reader_feedback_batch(self.config, payload=body["batch"]))
        if action in {"feedback-decide", "feedback-convert"}:
            batch_id = body.get("batch_id")
            if not isinstance(batch_id, str) or not STABLE_ID.fullmatch(batch_id):
                raise ValueError("反馈批次 ID 无效")
            relative = f"50_workbench/reader_feedback/{batch_id}.json"
            _, _, batch = self.read(relative)
            if body.get("batch_sha256") != canonical_json_hash(batch):
                raise ValueError("反馈批次版本已变化")
            if action == "feedback-decide":
                if set(body) != {"batch_id", "batch_sha256", "decisions", "reason", "acknowledge"}:
                    raise ValueError("反馈决定字段无效")
                decision = {"schema": DECISION_SCHEMA, "batch_sha256": body["batch_sha256"], "decided_by": "human", "decisions": body["decisions"], "reason": body["reason"]}
                return asdict(record_reader_feedback_decision(self.config, batch_path=relative, decision=decision))
            if set(body) != {"batch_id", "batch_sha256", "acknowledge"}:
                raise ValueError("反馈转换字段无效")
            return asdict(convert_reader_feedback_to_proposal(self.config, batch_path=relative,
                decision_path=f"50_workbench/reader_feedback/{batch_id}.decision.json", target="planning"))
        raise StudioContentError("未知学习记录动作")
