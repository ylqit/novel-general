"""Read-only author views and optimistic workbench drafts, independent of production.

Public identifiers resolve against an enumerated project inventory. The browser
never supplies a filesystem path. Reading cannot apply or restore an artifact.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import zipfile
from typing import Any

from longform_engine.local_web import LocalWebError
from longform_engine.config import load_project_config
from longform_engine.execution_origin import execution_origin
from longform_engine.storage import acquire_project_lock, atomic_write_text
from longform_engine.storage.layout import FINAL_MANUSCRIPT_DIRECTORY, manuscript_chapter_path, parse_canonical_chapter_number


class StudioContentError(LocalWebError):
    """An unavailable source or a concurrent edit requires an explicit decision."""


class StudioDraftConflict(StudioContentError):
    """The proposed save is valid but refers to an older source or revision."""


DOCUMENT_GROUPS = {
    "00_governance": "design", "10_bible": "knowledge", "20_outline": "planning",
    "30_state": "knowledge", "80_exports": "publication",
    "50_workbench/创作沙盒": "notes",
}
STATE_DOCUMENTS = {
    "semantic_obligations.json", "reader_promise_ledger.json", "story_graph.json",
    "character_state.json", "foreshadow_state.json", "foreshadowing_state.json", "tcs.json",
}
MAX_DOCUMENT_BYTES = 4_000_000


class StudioContent:
    """Own safe inventories, source-bound locations and non-canonical edit conflicts."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def _read(self, path: Path) -> bytes:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise StudioContentError("内容不存在或已移出本作品")
        if resolved.stat().st_size > MAX_DOCUMENT_BYTES:
            raise StudioContentError("资料超过单次阅读上限，请缩小材料范围")
        return resolved.read_bytes()

    def _json(self, path: Path) -> dict[str, Any]:
        data = json.loads(self._read(path))
        if not isinstance(data, dict):
            raise StudioContentError("资料格式不是对象")
        return data

    def catalogue(self, *, offset: int = 0, limit: int = 100) -> dict[str, Any]:
        if offset < 0 or not 1 <= limit <= 200:
            raise StudioContentError("目录分页范围无效")
        issues: list[str] = []
        volumes: list[dict[str, Any]] = []
        skeleton_path = self.root / "20_outline/volume_skeletons.json"
        if skeleton_path.is_file():
            try:
                skeletons = self._json(skeleton_path)
                basis = self._json(self.root / "30_state/planning_basis.json")
                bound = {row["path"]: row["sha256"] for row in basis.get("source_files", [])}
                if bound.get("20_outline/volume_skeletons.json") != sha256(self._read(skeleton_path)).hexdigest():
                    raise StudioContentError("卷目录批准绑定已失效，请重新批准规划")
                for row in skeletons.get("items", []):
                    span = row.get("chapter_range")
                    if (not isinstance(span, list) or len(span) != 2 or
                            any(type(n) is not int for n in span) or not 0 < span[0] <= span[1] <= 100_000):
                        raise StudioContentError("卷范围无效")
                    volumes.append({"volume_id": row["volume_id"], "title": row["title"],
                                    "chapter_range": span, "goal": row.get("volume_goal", "")})
            except (ValueError, OSError, KeyError, TypeError, StudioContentError) as exc:
                issues.append(str(exc))
                volumes = []
        else:
            issues.append("尚无批准的卷目录；已有正文仍可阅读")
        for index, left in enumerate(volumes):
            for right in volumes[index + 1:]:
                if max(left["chapter_range"][0], right["chapter_range"][0]) <= min(left["chapter_range"][1], right["chapter_range"][1]):
                    issues.append(f"卷范围冲突：{left['title']} / {right['title']}")
        lanes: dict[int, set[str]] = {}
        for lane in ("draft", "final"):
            for path in (self.root / "40_manuscript" / lane).glob("*.md"):
                n = parse_canonical_chapter_number(path)
                if n is not None and path.resolve().is_relative_to(self.root):
                    lanes.setdefault(n, set()).add(lane)
        for path in (self.root / "20_outline/chapter_contracts").glob("ch*.json"):
            if re.fullmatch(r"ch[0-9]+", path.stem):
                lanes.setdefault(int(path.stem[2:]), set())
        numbers = sorted(n for n in lanes if n > 0)
        chapters = []
        for n in numbers[offset:offset + limit]:
            versions = lanes[n]
            selected = [v for v in volumes if v["chapter_range"][0] <= n <= v["chapter_range"][1]]
            title = f"第 {n} 章"
            if versions:
                lane = "final" if "final" in versions else "draft"
                try:
                    first = self._read(manuscript_chapter_path(self.root, n, lane=lane)).decode("utf-8").strip().splitlines()[0]
                    if first.startswith("#"):
                        title = first.lstrip("# ")[:160]
                except (OSError, UnicodeError, IndexError, StudioContentError) as exc:
                    issues.append(f"第 {n} 章：{exc}")
            chapters.append({"number": n, "title": title, "versions": sorted(versions),
                             "status": "final" if "final" in versions else "draft" if versions else "planned",
                             "volume_id": selected[0]["volume_id"] if len(selected) == 1 else None,
                             "volume_conflict": len(selected) > 1})
        return {"origin": execution_origin(self.root), "volumes": volumes, "chapters": chapters, "issues": issues, "total": len(numbers),
                "offset": offset, "next_offset": offset + limit if offset + limit < len(numbers) else None}

    def chapter(self, number: int, *, version: str = "preferred") -> dict[str, Any]:
        if version not in {"preferred", "draft", "final"}:
            raise StudioContentError("正文版本无效")
        final = manuscript_chapter_path(self.root, number, lane="final")
        lane = ("final" if final.is_file() else "draft") if version == "preferred" else version
        path = manuscript_chapter_path(self.root, number, lane=lane)
        if not path.is_file():
            return {"chapter_number": number, "version": lane, "available": False, "text": "", "sha256": None}
        data = self._read(path)
        text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        numbers = sorted({n for directory in (self.root / FINAL_MANUSCRIPT_DIRECTORY, self.root / "40_manuscript/draft")
                          for p in directory.glob("*.md") if (n := parse_canonical_chapter_number(p)) is not None})
        closed = False
        closure_issue = None
        closure_file = self.root / "30_state/chapter_closures" / f"ch{number:03d}.json"
        if closure_file.is_file():
            try:
                closure = self._json(closure_file)
                semantic = self._read(self.root / "30_state/semantic_ledger" / f"ch{number:03d}.json")
                closed = (closure.get("schema") == "chapter_closure_v2" and closure.get("chapter_number") == number
                          and closure.get("final_sha256") == sha256(self._read(final)).hexdigest()
                          and closure.get("semantic_ledger_sha256") == sha256(semantic).hexdigest())
                if not closed:
                    closure_issue = "关闭记录与本章正式正文或语义档案不匹配"
            except (ValueError, OSError, StudioContentError) as exc:
                closure_issue = "关闭证据不完整：" + str(exc)
        return {"chapter_number": number, "version": lane, "available": True, "text": text,
                "sha256": sha256(data).hexdigest(), "characters": len(re.findall(r"[\u3400-\u9fff]", text)),
                "previous": max((n for n in numbers if n < number), default=None),
                "next": min((n for n in numbers if n > number), default=None),
                "closed": closed, "closure_issue": closure_issue}

    def documents(self, *, directories: set[str] | None = None) -> list[dict[str, Any]]:
        result = []
        for directory, group in DOCUMENT_GROUPS.items():
            if directories is not None and directory not in directories:
                continue
            for path in sorted((self.root / directory).rglob("*")):
                if not path.is_file() or path.suffix not in {".md", ".json"} or not path.resolve().is_relative_to(self.root):
                    continue
                relative = path.relative_to(self.root).as_posix()
                if directory == "30_state" and path.name not in STATE_DOCUMENTS and path.parent.name not in {"semantic_ledger", "narrative_events"}:
                    continue
                actual_group = group
                if directory == "10_bible" and ("fanfiction" in relative or path.stem in {"source_canon", "source_registry"}):
                    actual_group = "fanfiction"
                title = {"book_spine": "全书主干", "book_outline": "全书总纲", "volume_skeletons": "全书卷结构", "rolling_window": "滚动章节规划",
                         "canonical_facts": "批准的设计事实", "semantic_obligations": "叙事义务", "reader_promise_ledger": "读者承诺",
                         "story_graph": "人物与事件关系", "character_state": "人物当前状态", "foreshadow_state": "伏笔状态",
                         "foreshadowing_state": "伏笔当前状态", "foreshadowing_ledger": "伏笔规划", "outline_anchors": "全书情节锚点",
                         "characters": "人物档案", "character_expression": "人物声音与表达", "creative_brief": "创作方向",
                         "creative_decisions": "开书决策", "factions": "势力资料", "locations": "地点资料", "relationships": "人物关系",
                         "author_voice_edit_pairs": "批准的作者声音样例", "adaptation_profile": "拆书技法与改编分析", "current_style_profile": "当前写作风格分析", "story_arcs": "全书故事弧", "volumes": "卷规划",
                         "planning_window": "当前规划窗口", "execution_origin": "演练来源声明",
                         "source_canon": "原著基线", "story_engine": "同人故事动力", "fanfiction_bible": "同人路线与规则"}.get(path.stem, path.stem.replace("_", " "))
                chapter_name = re.fullmatch(r"ch([0-9]+)", path.stem)
                chapter_labels = {"chapter_contracts": "批准规划", "chapter_intents": "人工意图", "plot_nodes": "情节节点",
                                  "narrative_events": "事件与实现记录", "semantic_ledger": "正文语义档案"}
                if chapter_name and path.parent.name in chapter_labels:
                    title = f"第 {int(chapter_name[1])} 章 · {chapter_labels[path.parent.name]}"
                elif path.parent.name == "volumes" and re.fullmatch(r"vol[0-9]+", path.stem):
                    title = f"第 {int(path.stem[3:])} 卷 · 卷纲"
                if path.suffix == ".md":
                    with path.open(encoding="utf-8") as handle:
                        heading = handle.readline(300).strip()
                    if heading.startswith("#"):
                        title = heading.lstrip("# ")[:160]
                        title = {"Reader Contract": "读者合同", "Idea Seed": "开书构思", "Automation Policy": "创作流程与批准规则",
                                 "Book Outline": "全书总纲", "Style Bible": "写作风格", "World": "世界设定", "Power": "能力体系"}.get(title, title)
                elif directory == "50_workbench/创作沙盒":
                    try:
                        note = self._json(path)
                        title = str(note.get("title") or title)[:160]
                    except (ValueError, OSError, StudioContentError):
                        # Keep the item discoverable; opening it shows the exact
                        # read/parse error without hiding other project documents.
                        title = f"{path.stem[:140]}（资料待核对）"
                result.append({"id": "doc_" + sha256(relative.encode()).hexdigest()[:24], "title": title,
                               "group": actual_group, "relative": relative, "format": path.suffix[1:]})
        return result

    def planning_view(self) -> dict[str, Any]:
        """Display only planning whose persisted approval bindings still match."""
        from longform_engine.planning.context import load_chapter_planning_context

        basis_path = self.root / "30_state/planning_basis.json"
        if not basis_path.is_file():
            return {"status": "missing", "issues": ["尚无已批准规划，请先完成设计与规划审查。"]}
        try:
            basis = self._json(basis_path)
            bound = {row["path"]: row["sha256"] for row in basis["source_files"]}
            for relative, digest in bound.items():
                if sha256(self._read(self.root / relative)).hexdigest() != digest:
                    raise StudioContentError("批准依据已变化：" + relative)
            paths = {"book_spine": "20_outline/book_spine.json", "volume_skeletons": "20_outline/volume_skeletons.json",
                     "rolling_window": "20_outline/rolling_window.json"}
            if any(path not in bound for path in paths.values()):
                raise StudioContentError("规划批准依据不完整")
            documents = {name: self._json(self.root / path) for name, path in paths.items()}
            start = documents["rolling_window"]["start_chapter"]
            context = load_chapter_planning_context(self.root, start)
            firm_end = min(start + 2, documents["rolling_window"]["end_chapter"])
            contracts = [load_chapter_planning_context(self.root, n).contract for n in range(start, firm_end + 1)]
            forecasts = [self._json(self.root / relative) for relative in bound
                         if relative.startswith("20_outline/chapter_forecasts/")]
            tables = [self._json(self.root / relative) for relative in bound if relative.startswith("20_outline/plot_nodes/")]
            return {"status": "current", "issues": [], **documents, "active_volume_plan": context.volume,
                    "chapter_contracts": contracts, "chapter_forecasts": forecasts, "plot_node_tables": tables,
                    "source_files": basis["source_files"]}
        except (ValueError, OSError, KeyError, TypeError, StudioContentError) as exc:
            return {"status": "stale", "issues": [str(exc)]}

    def quality_history(self) -> dict[str, Any]:
        """Show existing observations only when their final text hash still matches."""
        records: dict[int, dict[str, Any]] = {}
        final_hashes: dict[int, tuple[str, str]] = {}
        issues = []
        for relative, kind, hash_field in (("30_state/quality/structure_history.jsonl", "structure", "source_hash"),
                                           ("30_state/reward_ledger.jsonl", "reward", "evidence_source_hash")):
            path = self.root / relative
            if not path.is_file():
                continue
            try:
                for line in self._read(path).decode("utf-8").splitlines():
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    number = row["chapter_number"]
                    if type(number) is not int or number < 1:
                        raise ValueError("质量观察的章节号无效")
                    # This projection needs only evidence hashes. Calling chapter()
                    # for every observation would rescan the whole book directory.
                    if number not in final_hashes:
                        final_path = manuscript_chapter_path(self.root, number, lane="final")
                        if final_path.is_file():
                            raw = self._read(final_path)
                            normalized = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                            final_hashes[number] = (sha256(raw).hexdigest(), sha256(normalized.encode()).hexdigest())
                        else:
                            final_hashes[number] = ("", "")
                    raw_hash, text_hash = final_hashes[number]
                    item = records.setdefault(number, {"chapter_number": number, "issues": []})
                    if not text_hash or text_hash != row.get(hash_field):
                        item["issues"].append("观察依据与当前正式正文不一致：" + kind)
                    else:
                        item[kind] = row
                        item["final_sha256"] = raw_hash
            except (ValueError, OSError, KeyError, TypeError, StudioContentError) as exc:
                issues.append(str(exc))
        return {"chapters": [records[n] for n in sorted(records)], "issues": issues,
                "origin": execution_origin(self.root), "literary_verdict": "not_inferred_from_metrics"}

    def save_note(self, payload: dict[str, Any]) -> dict[str, Any]:
        from longform_engine.creative_sandbox import create_sandbox_artifact
        if set(payload) != {"title", "text"} or any(not isinstance(payload[key], str) or not payload[key].strip() for key in payload):
            raise StudioContentError("请填写备忘标题与内容")
        if len(payload["title"]) > 160 or len(payload["text"].encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise StudioContentError("备忘内容过长")
        config = load_project_config(self.root / "project.yaml")
        with acquire_project_lock(config, command="studio save note"):
            return create_sandbox_artifact(config, document_type="memo", title=payload["title"], body=payload["text"], created_by="human")

    def versions(self, number: int) -> dict[str, Any]:
        from longform_engine.agent_tasks import list_manifests, manifest_output
        from longform_engine.artifacts import read_chapter_audit_artifact
        manuscript_chapter_path(self.root, number, lane="draft")
        sources: dict[str, str] = {}
        issues: list[str] = []
        for lane, title in (("final", "当前正式稿"), ("draft", "最近提交稿")):
            sources[f"40_manuscript/{lane}/ch{number:03d}.md"] = title
        for task in list_manifests(self.root, chapter_number=number):
            if task.get("task_type") not in {"chapter_write", "chapter_coedit_rewrite", "repair", "prose_naturalness"}:
                continue
            output = manifest_output(task).get("path", "")
            if output:
                sources[output] = f"创作候选 · {task.get('status', '')}"
        from longform_engine.human_author_revision import TASK_SCHEMA
        revision_directory = self.root / f"50_workbench/human_author_revisions/ch{number:03d}"
        for task_file in sorted(revision_directory.glob("*.task.json")):
            try:
                revision = json.loads(task_file.read_text(encoding="utf-8"))
                if not isinstance(revision, dict) or revision.get("schema") != TASK_SCHEMA or revision.get("chapter_number") != number:
                    raise ValueError("人工修订记录不属于当前章节")
                source = str(revision.get("source_file") or "")
                source_path = (self.root / source).resolve()
                if not source_path.is_relative_to((self.root / "50_workbench/candidate_blobs").resolve()) or source_path.suffix != ".md":
                    raise ValueError("人工修订原稿不在候选存档中")
                if sha256(source_path.read_bytes()).hexdigest() != revision.get("source_candidate_sha256"):
                    raise ValueError("人工修订原稿内容绑定失效")
                sources.setdefault(source, "人工修改前冻结原稿")
                candidate = str(revision.get("candidate_file") or "")
                candidate_path = (self.root / candidate).resolve()
                if candidate not in sources and (not candidate_path.is_relative_to(revision_directory.resolve()) or candidate_path.suffix != ".md"):
                    raise ValueError("人工修改稿不在本章工作区中")
                sources.setdefault(candidate, "人工修改工作稿（未视为正式正文）")
            except (ValueError, OSError) as exc:
                issues.append(str(exc))
        archive = self.root / "70_runtime/artifacts/chapters" / f"ch{number:03d}.zip"
        if archive.is_file():
            try:
                with zipfile.ZipFile(archive) as handle:
                    manifest = json.loads(handle.read("_audit/manifest.json"))
                    if manifest.get("schema") != "chapter_artifact_archive_v3" or manifest.get("chapter_number") != number:
                        raise StudioContentError("归档不属于当前章节")
                    for entry in manifest.get("entries", []):
                        relative = entry.get("path", "")
                        if relative.endswith(".md") and relative.startswith(("50_workbench/candidate_blobs/", "50_workbench/human_author_revisions/")):
                            sources.setdefault(relative, "归档候选（未恢复）")
            except (ValueError, KeyError, OSError, zipfile.BadZipFile) as exc:
                issues.append(str(exc))
        rows = []
        for relative, label in sources.items():
            try:
                data = read_chapter_audit_artifact(self.root, number, relative)
            except FileNotFoundError:
                continue
            except ValueError as exc:
                issues.append(str(exc))
                continue
            rows.append({"id": "version_" + sha256(relative.encode()).hexdigest()[:24], "label": label,
                         "relative": relative, "sha256": sha256(data).hexdigest(), "bytes": len(data),
                         "archived": not (self.root / relative).is_file()})
        return {"chapter_number": number, "versions": rows, "issues": issues}

    def version(self, number: int, version_id: str) -> dict[str, Any]:
        from longform_engine.artifacts import read_chapter_audit_artifact
        for row in self.versions(number)["versions"]:
            if row["id"] == version_id:
                data = read_chapter_audit_artifact(self.root, number, row["relative"])
                if sha256(data).hexdigest() != row["sha256"]:
                    raise StudioContentError("版本在读取时变化，请重试")
                return {**row, "text": data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")}
        raise StudioContentError("本章没有这个历史版本")

    def document(self, document_id: str) -> dict[str, Any]:
        for row in self.documents():
            if row["id"] == document_id:
                data = self._read(self.root / row["relative"])
                text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                notice = "规划内容，不代表正文中已发生" if row["group"] == "planning" else ""
                consequence_history: dict[str, Any] | None = None
                if row["format"] == "json":
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        payload = None
                    if isinstance(payload, dict) and payload.get("schema") == "semantic_document_v1":
                        from longform_engine.semantic_protocols import validate_semantic_document
                        artifact = payload.get("artifact")
                        semantic_errors = validate_semantic_document(payload)
                        if semantic_errors:
                            notice = "资料结构或内容绑定无效，仅供查看原文"
                        elif isinstance(artifact, dict) and artifact.get("state") != "approved":
                            notice = "尚未批准的资料，不作为创作事实"
                        if isinstance(payload.get("extensions"), dict) and payload["extensions"].get("fixture"):
                            notice = "合成资料阅读夹具，不代表已批准原著或真实小说事实"
                        if (not semantic_errors and isinstance(artifact, dict)
                                and artifact.get("state") == "approved"):
                            claims = [claim for claim in payload.get("claims", [])
                                      if claim.get("extensions", {}).get("semantic_type") == "跨卷延续后果"]
                            if claims:
                                from longform_engine.fanfiction_context import read_evidenced_consequence_states
                                through = max((number for path in (self.root / FINAL_MANUSCRIPT_DIRECTORY).glob("*.md")
                                               if (number := parse_canonical_chapter_number(path)) is not None), default=0)
                                consequence_history = {"through_chapter": through, "items": [], "issues": []}
                                try:
                                    history = read_evidenced_consequence_states(
                                        self.root, {claim["claim_id"] for claim in claims}, through_chapter=through,
                                    )
                                    consequence_history["items"] = [
                                        {"claim_id": claim["claim_id"], "plan": claim["statement"],
                                         "scope": claim.get("extensions", {}), "actual": history["latest"].get(claim["claim_id"])}
                                        for claim in claims
                                    ]
                                except (ValueError, OSError) as exc:
                                    consequence_history["issues"].append(str(exc))
                source_names = {}
                if row["group"] == "fanfiction":
                    config = load_project_config(self.root / "project.yaml")
                    source_names = {source["source_id"]: source["title"] for source in config.data.get("fanfiction", {}).get("sources", [])}
                return {**row, "text": text, "sha256": sha256(data).hexdigest(), "notice": notice,
                        "source_names": source_names, "consequence_history": consequence_history}
        raise StudioContentError("本作品中没有这份资料")

    def search(self, query: str, *, offset: int = 0, limit: int = 40) -> dict[str, Any]:
        query = query.strip()
        if not 1 <= len(query) <= 120 or offset < 0 or not 1 <= limit <= 100:
            raise StudioContentError("请输入 1–120 字的搜索词，并使用有效分页")
        sources = [("chapter", n, path) for path in sorted((self.root / FINAL_MANUSCRIPT_DIRECTORY).glob("*.md"))
                   if (n := parse_canonical_chapter_number(path)) is not None]
        sources.extend(("document", row["id"], self.root / row["relative"]) for row in self.documents())
        hits: list[dict[str, Any]] = []
        issues: list[str] = []
        for kind, key, path in sources:
            try:
                data = self._read(path)
                text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except (OSError, UnicodeError, StudioContentError) as exc:
                issues.append(f"{path.name}: {exc}")
                continue
            # Search exact Unicode text; offsets are code points, never browser UTF-16 units.
            start = 0
            while (start := text.find(query, start)) >= 0:
                end = start + len(query)
                hits.append({"kind": kind, "id": key, "title": path.stem,
                             "start": start, "end": end, "quote": text[start:end],
                             "sha256": sha256(data).hexdigest(), "excerpt": text[max(0, start - 45):end + 70]})
                start = end
        return {"query": query, "results": hits[offset:offset + limit], "total": len(hits), "issues": issues,
                "next_offset": offset + limit if offset + limit < len(hits) else None}

    def draft(self, number: int) -> dict[str, Any]:
        manuscript_chapter_path(self.root, number, lane="draft")  # validate chapter scope
        path = self.root / "50_workbench/studio/drafts" / f"ch{number:03d}.json"
        return self._json(path) if path.is_file() else {"revision": 0, "text": "", "source_sha256": None}

    def save_draft(self, number: int, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"revision", "source_sha256", "text"} or type(payload["revision"]) is not int or not isinstance(payload["text"], str):
            raise StudioContentError("修改稿字段无效")
        if len(payload["text"].encode("utf-8")) > MAX_DOCUMENT_BYTES:
            raise StudioContentError("修改稿过长")
        with acquire_project_lock(load_project_config(self.root / "project.yaml"), command="studio_save_draft"):
            current = self.draft(number)
            if current["revision"] != payload["revision"]:
                raise StudioDraftConflict("draft_revision_conflict：另一页面已保存，请保留本地文字并比较版本")
            source = self.chapter(number)
            if source["sha256"] != payload["source_sha256"]:
                raise StudioDraftConflict("draft_source_stale：正文已变化，请比较新版本后再保存")
            result = {"schema": "studio_edit_draft_v1", "chapter_number": number,
                      "revision": current["revision"] + 1, "source_sha256": payload["source_sha256"], "text": payload["text"]}
            path = self.root / "50_workbench/studio/drafts" / f"ch{number:03d}.json"
            atomic_write_text(path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        return result
