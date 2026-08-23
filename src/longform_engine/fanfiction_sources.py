"""User-level source library and project-scoped fanfiction source contracts.

The library is deliberately non-canonical.  A novel project pins immutable item
and extraction hashes, then separately promotes paraphrased facts into its own
fanfiction canon through the intelligence workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Iterable

import yaml

from longform_engine.config import ConfigDocument
from longform_engine.research import ResearchItemResult, search_research
from longform_engine.research.pipeline import WebFetcher, search_web_candidates
from longform_engine.storage import atomic_write_text, resolve_project_root
from longform_engine.storage.layout import list_finalized_chapter_files


LIBRARY_INDEX_SCHEMA = "source_library_index_v1"
LIBRARY_WORK_SCHEMA = "source_library_work_v1"
LIBRARY_ITEM_SCHEMA = "source_library_item_v1"
EXTRACTION_SCHEMA = "source_extraction_candidate_v1"
PROJECT_WORK_SCHEMA = "同人作品资料_第1版"
PROJECT_BINDING_SCHEMA = "fanfiction_work_binding_v1"
COVERAGE_SCHEMA = "fanfiction_coverage_plan_v1"
EXTERNAL_REQUEST_SCHEMA = "external_work_research_request_v1"
INCREMENTAL_REQUEST_SCHEMA = "fanfiction_incremental_source_request_v1"
SOURCE_UPGRADE_PROPOSAL_SCHEMA = "fanfiction_source_upgrade_proposal_v1"
SOURCE_UPGRADE_REVIEW_SCHEMA = "fanfiction_source_upgrade_semantic_review_v1"
SOURCE_UPGRADE_DECISION_SCHEMA = "human_fanfiction_source_upgrade_decision_v1"
VERSION_CONFLICT_DECISION_SCHEMA = "fanfiction_version_conflict_decision_v1"
CANON_SCHEMA = "fanfiction_source_canon_v2"

PROJECT_PACK_ROOT = Path("50_workbench") / "同人原著资料"
EXTERNAL_REQUEST_ROOT = Path("50_workbench") / "research_inbox" / "外部作品研究申请"

FULL_RETENTION_RIGHTS = frozenset(
    {"user_claimed_authorized", "public_domain_claimed", "platform_permitted_claimed"}
)
RIGHTS_STATUSES = FULL_RETENTION_RIGHTS | {"unverified"}
RETENTION_MODES = frozenset({"full_text", "short_evidence", "metadata_only"})
COVERAGE_MODES = frozenset({"全作到截止点", "创作范围", "逐章补全"})
COVERAGE_STATES = frozenset({"待补全", "已覆盖", "不适用", "冲突"})
EXTERNAL_PURPOSES = frozenset(
    {"factual_reference", "technique_analysis", "inspiration", "use_original_elements"}
)
FACT_TYPE_ALIASES = {
    "character": {"character", "人物"},
    "relationship": {"relationship", "关系"},
    "world_rule": {"world_rule", "世界规则"},
    "ability": {"ability", "能力"},
    "event": {"event", "事件"},
    "timeline": {"timeline", "时间线"},
    "location": {"location", "地点"},
    "organization": {"organization", "组织"},
    "item": {"item", "物品"},
    "terminology": {"terminology", "术语"},
    "unresolved_question": {"unresolved_question", "未解决问题"},
    "version_conflict": {"version_conflict", "版本冲突"},
}


class FanfictionSourceError(ValueError):
    """Raised when a source-library or project binding contract is invalid."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_library_root() -> Path:
    """Return the current user's source library or an explicit absolute override."""

    override = os.environ.get("LONGFORM_SOURCE_LIBRARY")
    if override:
        path = Path(override).expanduser()
        if not path.is_absolute():
            raise FanfictionSourceError("LONGFORM_SOURCE_LIBRARY must be an absolute path")
        return path.resolve()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return (base / "longform-novel-engine" / "原著资料库").resolve()


def initialize_source_library() -> dict[str, Any]:
    root = source_library_root()
    (root / "作品").mkdir(parents=True, exist_ok=True)
    index_path = root / "资料库索引.json"
    index = _read_json(index_path, {})
    if not isinstance(index, dict) or index.get("schema") != LIBRARY_INDEX_SCHEMA:
        index = {
            "schema": LIBRARY_INDEX_SCHEMA,
            "works": [],
            "items": [],
            "updated_at": utc_now(),
        }
        _write_json(index_path, index)
    return {
        "schema": LIBRARY_INDEX_SCHEMA,
        "library_root": str(root),
        "index_file": str(index_path),
        "work_count": len(index.get("works") or []),
        "item_count": len(index.get("items") or []),
    }


def source_library_status() -> dict[str, Any]:
    root = source_library_root()
    index = _library_index(create=False)
    return {
        "schema": "source_library_status_v1",
        "library_root": str(root),
        "exists": (root / "资料库索引.json").is_file(),
        "work_count": len(index.get("works") or []),
        "item_count": len(index.get("items") or []),
    }


def register_source_work(
    *,
    name: str,
    creator: str,
    aliases: Iterable[str] = (),
    versions: Iterable[str] = (),
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("source work registration requires --approved-by human")
    visible_name = _visible_name(name, field="name")
    creator = str(creator).strip()
    if not creator:
        raise FanfictionSourceError("source work creator is required")
    normalized_aliases = _string_list(aliases, field="aliases")
    normalized_versions = _string_list(versions, field="versions")
    root = source_library_root()
    index = _library_index(create=True)
    for record in index["works"]:
        if not isinstance(record, dict):
            continue
        if str(record.get("name") or "").casefold() == visible_name.casefold() and str(
            record.get("creator") or ""
        ).casefold() == creator.casefold():
            current = library_work(str(record.get("work_id") or ""))
            return {**current, "library_root": str(root), "created": False}
    work_id = "work_" + sha256(f"{visible_name}\0{creator}".encode("utf-8")).hexdigest()[:16]
    directory = _unique_named_directory(root / "作品", visible_name, stable_id=work_id)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "资料项").mkdir()
    payload = {
        "协议版本": LIBRARY_WORK_SCHEMA,
        "作品ID": work_id,
        "作品名称": visible_name,
        "原作者": creator,
        "作品别名": normalized_aliases,
        "版本列表": normalized_versions,
        "人工批准": "human",
        "创建时间": utc_now(),
    }
    _write_yaml(directory / "作品身份.yaml", payload)
    record = {
        "work_id": work_id,
        "name": visible_name,
        "creator": creator,
        "path": directory.relative_to(root).as_posix(),
        "identity_sha256": _file_hash(directory / "作品身份.yaml"),
    }
    index["works"].append(record)
    _write_library_index(index)
    return {**record, "library_root": str(root), "created": True}


def import_source_item(
    *,
    work_id: str,
    name: str,
    source_type: str,
    version: str,
    unit_range: str,
    source_method: str,
    rights_status: str,
    retention_mode: str,
    approved_by: str,
    file_path: str | Path | None = None,
    source_locator: str = "",
    supersedes_item_id: str = "",
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("source item import requires --approved-by human")
    if rights_status not in RIGHTS_STATUSES:
        raise FanfictionSourceError("unknown source rights_status")
    if retention_mode not in RETENTION_MODES:
        raise FanfictionSourceError("retention_mode must be full_text, short_evidence, or metadata_only")
    if retention_mode == "full_text" and rights_status not in FULL_RETENTION_RIGHTS:
        raise FanfictionSourceError(
            "full_text retention requires a user-claimed authorized, public-domain, or platform-permitted source"
        )
    work = library_work(work_id)
    root = source_library_root()
    work_dir = root / str(work["path"])
    visible_name = _visible_name(name, field="name")
    source_type = str(source_type).strip()
    version = str(version).strip()
    unit_range = str(unit_range).strip()
    source_method = str(source_method).strip()
    if not all((source_type, version, unit_range, source_method)):
        raise FanfictionSourceError("source type, version, unit range, and source method are required")

    content_bytes = b""
    source_name = ""
    if file_path is not None:
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise FanfictionSourceError(f"source item file does not exist: {source}")
        content_bytes = source.read_bytes()
        source_name = _visible_filename(source.name)
    elif retention_mode != "metadata_only":
        raise FanfictionSourceError(f"{retention_mode} retention requires --file")
    if retention_mode == "metadata_only" and not source_locator.strip() and not content_bytes:
        raise FanfictionSourceError("metadata_only retention requires --source-locator or --file")
    if retention_mode == "short_evidence" and len(content_bytes.decode("utf-8", errors="ignore")) > 2000:
        raise FanfictionSourceError("short_evidence content must not exceed 2000 Unicode characters")

    content_sha = sha256(content_bytes or source_locator.strip().encode("utf-8")).hexdigest()
    item_id = "item_" + sha256(f"{work_id}\0{content_sha}".encode("utf-8")).hexdigest()[:16]
    index = _library_index(create=True)
    existing = next(
        (item for item in index["items"] if isinstance(item, dict) and item.get("item_id") == item_id),
        None,
    )
    if existing is not None:
        current = library_item(str(existing.get("item_id") or ""))
        return {**current, "library_root": str(root), "created": False}
    if supersedes_item_id:
        old = library_item(supersedes_item_id)
        if old.get("work_id") != work_id:
            raise FanfictionSourceError("superseded source item must belong to the same work")

    item_dir = _unique_named_directory(work_dir / "资料项", visible_name, stable_id=item_id)
    item_dir.mkdir(parents=True, exist_ok=False)
    content_files: list[str] = []
    if content_bytes and retention_mode != "metadata_only":
        content_dir = item_dir / "内容"
        content_dir.mkdir()
        target = content_dir / source_name
        target.write_bytes(content_bytes)
        content_files.append(target.relative_to(root).as_posix())
    payload = {
        "协议版本": LIBRARY_ITEM_SCHEMA,
        "资料项ID": item_id,
        "作品ID": work_id,
        "资料名称": visible_name,
        "资料类型": source_type,
        "版本名称": version,
        "覆盖范围": unit_range,
        "来源方式": source_method,
        "来源定位": source_locator.strip(),
        "权利状态": rights_status,
        "留存方式": retention_mode,
        "内容哈希": content_sha,
        "内容文件": content_files,
        "替代资料项ID": supersedes_item_id,
        "人工批准": "human",
        "创建时间": utc_now(),
    }
    _write_yaml(item_dir / "来源说明.yaml", payload)
    _write_json(
        item_dir / "提取结果.json",
        {
            "schema": EXTRACTION_SCHEMA,
            "item_id": item_id,
            "content_sha256": content_sha,
            "status": "pending_extraction",
            "facts": [],
            "evidence": [],
        },
    )
    _write_json(item_dir / "证据索引.json", {"schema": "source_evidence_index_v1", "items": []})
    record = {
        "item_id": item_id,
        "work_id": work_id,
        "name": visible_name,
        "path": item_dir.relative_to(root).as_posix(),
        "content_sha256": content_sha,
        "extraction_sha256": "",
        "retention_mode": retention_mode,
        "supersedes_item_id": supersedes_item_id,
    }
    index["items"].append(record)
    _write_library_index(index)
    return {**record, "library_root": str(root), "created": True}


def approve_source_extraction(*, item_id: str, file_path: str | Path, approved_by: str) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("source extraction approval requires --approved-by human")
    candidate_path = Path(file_path).expanduser().resolve()
    candidate = _read_json(candidate_path, None)
    if not isinstance(candidate, dict):
        raise FanfictionSourceError("source extraction candidate must be a JSON object")
    item = library_item(item_id)
    errors = validate_source_extraction(candidate, item)
    if errors:
        raise FanfictionSourceError("invalid source extraction: " + "; ".join(errors))
    canonical = dict(candidate)
    canonical["status"] = "approved_candidate"
    canonical["approved_by"] = "human"
    canonical["approved_at"] = utc_now()
    extraction_bytes = (json.dumps(canonical, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    extraction_sha = sha256(extraction_bytes).hexdigest()
    root = source_library_root()
    item_dir = root / str(item["path"])
    version_dir = item_dir / "提取版本"
    version_dir.mkdir(parents=True, exist_ok=True)
    version_file = version_dir / f"{extraction_sha}.json"
    if not version_file.exists():
        version_file.write_bytes(extraction_bytes)
    atomic_write_text(item_dir / "提取结果.json", extraction_bytes.decode("utf-8"))
    evidence_index = {
        "schema": "source_evidence_index_v1",
        "item_id": item_id,
        "content_sha256": item["content_sha256"],
        "extraction_sha256": extraction_sha,
        "items": canonical["evidence"],
    }
    _write_json(item_dir / "证据索引.json", evidence_index)
    index = _library_index(create=False)
    for record in index["items"]:
        if isinstance(record, dict) and record.get("item_id") == item_id:
            record["extraction_sha256"] = extraction_sha
            record["extraction_path"] = version_file.relative_to(root).as_posix()
    _write_library_index(index)
    return {
        "schema": EXTRACTION_SCHEMA,
        "item_id": item_id,
        "content_sha256": item["content_sha256"],
        "extraction_sha256": extraction_sha,
        "extraction_file": str(version_file),
        "evidence_count": len(canonical["evidence"]),
        "fact_count": len(canonical["facts"]),
    }


def create_source_extraction_template(*, item_id: str) -> dict[str, Any]:
    """Create a non-Canon extraction candidate without overwriting author work."""

    item = library_item(item_id)
    item_dir = _resolve_library_item_directory(item)
    target = item_dir / "提取候选.json"
    created = not target.exists()
    if created:
        _write_json(
            target,
            {
                "schema": EXTRACTION_SCHEMA,
                "item_id": item_id,
                "content_sha256": item["content_sha256"],
                "facts": [],
                "evidence": [],
            },
        )
    return {
        "schema": EXTRACTION_SCHEMA,
        "item_id": item_id,
        "content_sha256": item["content_sha256"],
        "candidate_file": str(target),
        "content_files": list(item.get("content_files") or []),
        "retention_mode": item.get("retention_mode"),
        "created": created,
        "non_canonical": True,
    }


def validate_source_extraction(candidate: dict[str, Any], item: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {"schema", "item_id", "content_sha256", "facts", "evidence"}
    if set(candidate) != required:
        errors.append("candidate must contain schema, item_id, content_sha256, facts, evidence only")
        return errors
    if candidate.get("schema") != EXTRACTION_SCHEMA:
        errors.append(f"schema must be {EXTRACTION_SCHEMA}")
    if candidate.get("item_id") != item.get("item_id"):
        errors.append("item_id does not match the registered source item")
    if candidate.get("content_sha256") != item.get("content_sha256"):
        errors.append("content_sha256 does not match the registered source item")
    content_texts = _library_item_content_texts(item, errors)
    evidence = candidate.get("evidence")
    evidence_ids: set[str] = set()
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be a non-empty list")
        evidence = []
    for index, record in enumerate(evidence):
        prefix = f"evidence[{index}]"
        fields = {"id", "content_file", "start", "end", "excerpt"}
        if not isinstance(record, dict) or set(record) != fields:
            errors.append(f"{prefix} must contain exactly {sorted(fields)}")
            continue
        evidence_id = str(record.get("id") or "")
        if not _stable_id(evidence_id) or evidence_id in evidence_ids:
            errors.append(f"{prefix}.id must be stable and unique")
        evidence_ids.add(evidence_id)
        content_file = str(record.get("content_file") or "")
        text = content_texts.get(content_file)
        if text is None:
            errors.append(f"{prefix}.content_file is not registered for this item")
            continue
        start, end = record.get("start"), record.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end <= start or end > len(text):
            errors.append(f"{prefix} has an invalid Unicode span")
        elif text[start:end] != record.get("excerpt"):
            errors.append(f"{prefix}.excerpt does not match the source span")
        if len(str(record.get("excerpt") or "")) > 400:
            errors.append(f"{prefix}.excerpt exceeds the 400-character evidence limit")
    facts = candidate.get("facts")
    fact_ids: set[str] = set()
    if not isinstance(facts, list) or not facts:
        errors.append("facts must be a non-empty list")
        facts = []
    for index, record in enumerate(facts):
        prefix = f"facts[{index}]"
        fields = {"id", "type", "name", "summary", "attributes", "evidence_refs"}
        if not isinstance(record, dict) or set(record) != fields:
            errors.append(f"{prefix} must contain exactly {sorted(fields)}")
            continue
        fact_id = str(record.get("id") or "")
        if not _stable_id(fact_id) or fact_id in fact_ids:
            errors.append(f"{prefix}.id must be stable and unique")
        fact_ids.add(fact_id)
        if not str(record.get("type") or "").strip() or not str(record.get("summary") or "").strip():
            errors.append(f"{prefix}.type and summary are required")
        if not isinstance(record.get("attributes"), dict):
            errors.append(f"{prefix}.attributes must be an object")
        refs = record.get("evidence_refs")
        if not isinstance(refs, list) or not refs or any(str(ref) not in evidence_ids for ref in refs):
            errors.append(f"{prefix}.evidence_refs must reference this candidate evidence")
    return errors


def initialize_project_source_packs(
    config: ConfigDocument,
    *,
    source_id: str | None = None,
) -> dict[str, Any]:
    _require_fanfiction_mode(config)
    root = resolve_project_root(config)
    sources = _configured_sources(config)
    if source_id and source_id not in sources:
        raise FanfictionSourceError(f"unknown configured fanfiction source_id: {source_id}")
    selected = [sources[source_id]] if source_id else list(sources.values())
    created: list[str] = []
    existing: list[str] = []
    for source in selected:
        directory = project_source_pack_dir(root, str(source["source_id"]), create=False, config=config)
        setting_path = directory / "作品资料设定.yaml"
        if setting_path.is_file():
            existing.append(directory.relative_to(root).as_posix())
            continue
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "资料项").mkdir(exist_ok=True)
        global_work = _find_library_work(str(source["title"]), str(source["creator"]))
        setting = {
            "协议版本": PROJECT_WORK_SCHEMA,
            "资料源ID": str(source["source_id"]),
            "作品ID": str(global_work.get("work_id") or "") if global_work else "",
            "作品名称": str(source["title"]),
            "原作者": str(source["creator"]),
            "创作连续性": str(config.data["fanfiction"]["continuity_mode"]),
            "Canon截止点": str(source["canon_cutoff"]),
            "覆盖模式": "全作到截止点",
            "创建时间": utc_now(),
        }
        _write_yaml(setting_path, setting)
        _write_yaml(
            directory / "资料绑定.yaml",
            _binding_to_chinese(
                {
                    "schema": PROJECT_BINDING_SCHEMA,
                    "source_id": str(source["source_id"]),
                    "work_id": setting["作品ID"],
                    "items": [],
                    "updated_at": utc_now(),
                }
            ),
        )
        _write_yaml(
            directory / "全作覆盖计划.yaml",
            _coverage_to_chinese(
                {
                    "schema": COVERAGE_SCHEMA,
                    "source_id": str(source["source_id"]),
                    "work_id": setting["作品ID"],
                    "coverage_mode": "全作到截止点",
                    "authoritative_versions": [],
                    "canon_cutoff": str(source["canon_cutoff"]),
                    "units": [],
                    "mode_change_reason": "",
                    "approval": {"status": "draft", "approved_by": "", "approved_at": ""},
                }
            ),
        )
        atomic_write_text(directory / "版本冲突清单.md", "# 版本冲突清单\n\n当前没有已批准的版本冲突处理。\n")
        _write_coverage_report(config, str(source["source_id"]))
        created.append(directory.relative_to(root).as_posix())
    return {
        "schema": "fanfiction_source_pack_init_v1",
        "created": created,
        "existing": existing,
        "next_command": "longform-engine fanfiction coverage-apply project.yaml --source-id SOURCE --file PLAN --approved-by human",
    }


def apply_coverage_plan(
    config: ConfigDocument,
    *,
    source_id: str,
    file_path: str | Path,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("coverage plan apply requires --approved-by human")
    source = _configured_source(config, source_id)
    candidate = _read_yaml_or_json(Path(file_path).expanduser().resolve())
    if not isinstance(candidate, dict):
        raise FanfictionSourceError("coverage plan must be a YAML or JSON object")
    normalized = _normalize_coverage_plan(candidate, source)
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    if not pack.is_dir():
        raise FanfictionSourceError("create the project source pack before applying a coverage plan")
    setting = _read_yaml(pack / "作品资料设定.yaml", {})
    setting_work_id = str(setting.get("作品ID") or "") if isinstance(setting, dict) else ""
    if normalized.get("work_id") and setting_work_id and normalized["work_id"] != setting_work_id:
        raise FanfictionSourceError("coverage plan work_id does not match the project source pack")
    normalized["work_id"] = normalized.get("work_id") or setting_work_id
    errors = validate_coverage_plan(normalized, source=source, require_complete=False)
    if errors:
        raise FanfictionSourceError("invalid coverage plan: " + "; ".join(errors))
    normalized["approval"] = {"status": "approved", "approved_by": "human", "approved_at": utc_now()}
    _write_yaml(pack / "全作覆盖计划.yaml", _coverage_to_chinese(normalized))
    _write_coverage_report(config, source_id)
    gaps = coverage_gaps(config, source_id=source_id)
    return {
        "schema": COVERAGE_SCHEMA,
        "source_id": source_id,
        "plan_file": (pack / "全作覆盖计划.yaml").relative_to(root).as_posix(),
        "unit_count": len(normalized["units"]),
        "complete": gaps["complete"],
        "gaps": gaps["gaps"],
    }


def bind_library_item(
    config: ConfigDocument,
    *,
    source_id: str,
    item_id: str,
    approved_by: str,
    upgrade_authorization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("project source binding requires --approved-by human")
    source = _configured_source(config, source_id)
    item = library_item(item_id)
    work = library_work(str(item["work_id"]))
    if str(work.get("name") or "").casefold() != str(source["title"]).casefold() or str(
        work.get("creator") or ""
    ).casefold() != str(source["creator"]).casefold():
        raise FanfictionSourceError("library item work does not match the configured project source")
    if not str(item.get("extraction_sha256") or ""):
        raise FanfictionSourceError("source item must have an approved extraction candidate before binding")
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    if not pack.is_dir():
        raise FanfictionSourceError("create the project source pack before binding source items")
    setting_path = pack / "作品资料设定.yaml"
    setting = _read_yaml(setting_path, {})
    existing_work_id = str(setting.get("作品ID") or "") if isinstance(setting, dict) else ""
    if existing_work_id and existing_work_id != item["work_id"]:
        raise FanfictionSourceError("project source pack is already bound to another global work")
    setting["作品ID"] = item["work_id"]
    _write_yaml(setting_path, setting)

    binding_path = pack / "资料绑定.yaml"
    binding = _read_project_binding(binding_path)
    if not isinstance(binding, dict) or binding.get("schema") != PROJECT_BINDING_SCHEMA:
        raise FanfictionSourceError("project source binding file is invalid")
    binding["work_id"] = item["work_id"]
    entries = [entry for entry in binding.get("items") or [] if isinstance(entry, dict)]
    old = next((entry for entry in entries if entry.get("item_id") == item_id), None)
    entry = {
        "item_id": item_id,
        "content_sha256": item["content_sha256"],
        "extraction_sha256": item["extraction_sha256"],
        "library_uri": f"source-library://{item['work_id']}/{item_id}",
        "project_item_name": str(item["name"]),
        "approved_by": "human",
        "bound_at": utc_now(),
    }
    binding_changes = old is None or any(
        old.get(field) != entry.get(field)
        for field in ("content_sha256", "extraction_sha256", "library_uri")
    )
    applied_canon = root / "10_bible" / "fanfiction" / "source_canon.json"
    if binding_changes and applied_canon.is_file() and upgrade_authorization is None:
        raise FanfictionSourceError(
            "an applied project Canon cannot be rebound directly; use an approved source upgrade proposal, "
            "independent semantic review, and human upgrade decision"
        )
    if upgrade_authorization is not None and (
        upgrade_authorization.get("schema") != SOURCE_UPGRADE_PROPOSAL_SCHEMA
        or upgrade_authorization.get("source_id") != source_id
        or upgrade_authorization.get("change", {}).get("to_item_id") != item_id
    ):
        raise FanfictionSourceError("source upgrade authorization does not match this binding change")
    if old is None:
        entries.append(entry)
    else:
        entries[entries.index(old)] = entry
    binding["items"] = entries
    binding["updated_at"] = utc_now()
    _write_yaml(binding_path, _binding_to_chinese(binding))

    project_item_dir = _unique_project_item_directory(pack / "资料项", str(item["name"]), item_id)
    project_item_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(
        project_item_dir / "来源绑定.yaml",
        {
            "协议版本": PROJECT_BINDING_SCHEMA,
            "资料源ID": source_id,
            "作品ID": item["work_id"],
            "资料项ID": item_id,
            "内容哈希": entry["content_sha256"],
            "提取哈希": entry["extraction_sha256"],
            "资料库引用": entry["library_uri"],
            "项目资料名称": entry["project_item_name"],
            "人工批准": "human",
            "绑定时间": entry["bound_at"],
        },
    )
    library_item_dir = source_library_root() / str(item["path"])
    shutil.copy2(library_item_dir / "提取结果.json", project_item_dir / "提取结果.json")
    shutil.copy2(library_item_dir / "证据索引.json", project_item_dir / "证据索引.json")
    if binding_changes and applied_canon.is_file():
        _mark_source_binding_dependents_stale(
            root,
            source_id=source_id,
            item_id=item_id,
            artifact_paths=list((upgrade_authorization or {}).get("must_stale_artifacts") or []),
        )
    _write_coverage_report(config, source_id)
    return {
        "schema": PROJECT_BINDING_SCHEMA,
        "source_id": source_id,
        "work_id": item["work_id"],
        "item_id": item_id,
        "content_sha256": item["content_sha256"],
        "extraction_sha256": item["extraction_sha256"],
        "project_item_dir": project_item_dir.relative_to(root).as_posix(),
    }


def coverage_gaps(config: ConfigDocument, *, source_id: str | None = None) -> dict[str, Any]:
    sources = _configured_sources(config)
    selected = [source_id] if source_id else list(sources)
    work_results: list[dict[str, Any]] = []
    all_gaps: list[str] = []
    for selected_id in selected:
        source = _configured_source(config, selected_id)
        root = resolve_project_root(config)
        pack = project_source_pack_dir(root, selected_id, create=False, config=config)
        gaps: list[str] = []
        plan = _read_coverage_plan(pack / "全作覆盖计划.yaml", source)
        binding = _read_project_binding(pack / "资料绑定.yaml")
        if not pack.is_dir():
            gaps.append("作品资料包尚未创建")
        elif not isinstance(binding, dict) or binding.get("schema") != PROJECT_BINDING_SCHEMA:
            gaps.append("资料绑定合同缺失或无效")
        else:
            gaps.extend(_binding_errors(binding, require_available=True))
        if not isinstance(plan, dict):
            gaps.append("全作覆盖计划缺失")
        else:
            gaps.extend(validate_coverage_plan(plan, source=source, require_complete=True, binding=binding))
        gaps.extend(_version_conflict_gaps(pack, selected_id))
        gaps.extend(_incremental_request_gaps(pack))
        all_gaps.extend(f"{selected_id}: {gap}" for gap in gaps)
        work_results.append(
            {
                "source_id": selected_id,
                "title": source["title"],
                "complete": not gaps,
                "gaps": gaps,
            }
        )
    return {
        "schema": "fanfiction_coverage_status_v1",
        "complete": not all_gaps,
        "works": work_results,
        "gaps": all_gaps,
    }


def fanfiction_source_readiness(config: ConfigDocument) -> dict[str, Any]:
    if str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        return {"ready": True, "errors": [], "next_command": ""}
    status = coverage_gaps(config)
    next_command = "longform-engine fanfiction canon-task project.yaml"
    if status["gaps"]:
        if any("资料包尚未创建" in gap for gap in status["gaps"]):
            next_command = "longform-engine fanfiction pack-init project.yaml"
        else:
            next_command = "longform-engine fanfiction coverage-gaps project.yaml --json"
    return {"ready": status["complete"], "errors": status["gaps"], "next_command": next_command}


def apply_version_conflict_decisions(
    config: ConfigDocument,
    *,
    source_id: str,
    file_path: str | Path,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("version conflict decisions require --approved-by human")
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    payload = _read_yaml_or_json(Path(file_path).expanduser().resolve())
    if not isinstance(payload, dict):
        raise FanfictionSourceError("version conflict decision must be a YAML or JSON object")
    normalized = _normalize_version_conflict_decisions(payload, source_id=source_id)
    expected_ids = _version_conflict_ids(pack)
    errors = _validate_version_conflict_decisions(
        normalized,
        expected_ids=expected_ids,
        require_approval=False,
    )
    if normalized.get("source_id") != source_id:
        errors.append("source_id 与目标资料包不一致")
    if errors:
        raise FanfictionSourceError("invalid version conflict decision: " + "; ".join(errors))
    normalized["approved_by"] = "human"
    normalized["approved_at"] = utc_now()
    _write_yaml(pack / "版本冲突决定.yaml", _version_conflict_decisions_to_chinese(normalized))
    _write_version_conflict_report(pack, normalized)
    _write_coverage_report(config, source_id)
    return {
        "schema": VERSION_CONFLICT_DECISION_SCHEMA,
        "source_id": source_id,
        "decision_count": len(normalized["decisions"]),
        "complete": not _version_conflict_gaps(pack, source_id),
        "decision_file": (pack / "版本冲突决定.yaml").relative_to(root).as_posix(),
    }


def create_incremental_source_request(
    config: ConfigDocument,
    *,
    source_id: str,
    need: str,
    reason: str,
    chapter_number: int | None = None,
) -> dict[str, Any]:
    _require_fanfiction_mode(config)
    _configured_source(config, source_id)
    if chapter_number is not None and chapter_number <= 0:
        raise FanfictionSourceError("chapter_number must be positive")
    need = str(need).strip()
    reason = str(reason).strip()
    if not need or not reason:
        raise FanfictionSourceError("incremental source need and reason are required")
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    request_id = "source_gap_" + sha256(
        f"{source_id}\0{chapter_number or 0}\0{need}\0{reason}".encode("utf-8")
    ).hexdigest()[:16]
    path = pack / "待审资料需求" / f"{request_id}.json"
    payload = {
        "schema": INCREMENTAL_REQUEST_SCHEMA,
        "request_id": request_id,
        "source_id": source_id,
        "chapter_number": chapter_number,
        "need": need,
        "reason": reason,
        "status": "pending_human_approval",
        "network_performed": False,
        "created_at": utc_now(),
    }
    _write_json(path, payload)
    _write_coverage_report(config, source_id)
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def approve_incremental_source_request(
    config: ConfigDocument,
    *,
    request_id: str,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("incremental source requests require --approved-by human")
    root = resolve_project_root(config)
    path, payload = _incremental_request(root, request_id)
    if payload.get("status") not in {"pending_human_approval", "approved_for_search"}:
        raise FanfictionSourceError("incremental source request is not awaiting approval")
    payload["status"] = "approved_for_search"
    payload["approved_by"] = "human"
    payload["approved_at"] = utc_now()
    _write_json(path, payload)
    _write_coverage_report(config, str(payload["source_id"]))
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def resolve_incremental_source_request(
    config: ConfigDocument,
    *,
    request_id: str,
    item_id: str,
    reason: str,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("incremental source resolution requires --approved-by human")
    reason = str(reason).strip()
    if not reason:
        raise FanfictionSourceError("incremental source resolution reason is required")
    root = resolve_project_root(config)
    path, payload = _incremental_request(root, request_id)
    if payload.get("status") not in {"approved_for_search", "candidates_recorded"}:
        raise FanfictionSourceError("incremental source request is not ready for resolution")
    contract = project_source_contract(config, str(payload["source_id"]))
    bound_ids = {
        str(item.get("item_id") or "")
        for item in contract["binding"].get("items") or []
        if isinstance(item, dict)
    }
    if item_id not in bound_ids:
        raise FanfictionSourceError("incremental source resolution must cite a currently bound item")
    payload["status"] = "resolved"
    payload["resolved_item_id"] = item_id
    payload["resolution_reason"] = reason
    payload["resolved_by"] = "human"
    payload["resolved_at"] = utc_now()
    _write_json(path, payload)
    _write_coverage_report(config, str(payload["source_id"]))
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def fanfiction_canon_input_files(config: ConfigDocument) -> list[Path]:
    readiness = fanfiction_source_readiness(config)
    if not readiness["ready"]:
        raise FanfictionSourceError("fanfiction source coverage is incomplete: " + "; ".join(readiness["errors"]))
    root = resolve_project_root(config)
    inputs: list[Path] = []
    for source_id in _configured_sources(config):
        pack = project_source_pack_dir(root, source_id, create=False, config=config)
        inputs.extend((pack / "作品资料设定.yaml", pack / "资料绑定.yaml", pack / "全作覆盖计划.yaml"))
        for item_dir in (pack / "资料项").iterdir():
            if not item_dir.is_dir():
                continue
            for filename in ("来源绑定.yaml", "提取结果.json", "证据索引.json"):
                path = item_dir / filename
                if path.is_file():
                    inputs.append(path)
    return inputs


def project_source_contract(config: ConfigDocument, source_id: str) -> dict[str, Any]:
    root = resolve_project_root(config)
    source = _configured_source(config, source_id)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    setting = _read_yaml(pack / "作品资料设定.yaml", {})
    binding = _read_project_binding(pack / "资料绑定.yaml")
    plan = _read_coverage_plan(pack / "全作覆盖计划.yaml", source)
    evidence: dict[str, dict[str, Any]] = {}
    for item_dir in (pack / "资料项").iterdir() if (pack / "资料项").is_dir() else ():
        index = _read_json(item_dir / "证据索引.json", {})
        for record in index.get("items") or [] if isinstance(index, dict) else []:
            if not isinstance(record, dict):
                continue
            evidence_id = str(record.get("id") or "")
            if evidence_id:
                evidence[f"{str(index.get('item_id') or '')}:{evidence_id}"] = {
                    **record,
                    "item_id": str(index.get("item_id") or ""),
                    "content_sha256": str(index.get("content_sha256") or ""),
                }
    return {
        "source": source,
        "setting": setting,
        "binding": binding,
        "coverage": plan,
        "evidence": evidence,
        "binding_sha256": _file_hash(pack / "资料绑定.yaml"),
        "coverage_sha256": _file_hash(pack / "全作覆盖计划.yaml"),
    }


def source_upgrade_status(
    config: ConfigDocument,
    *,
    source_id: str | None = None,
) -> dict[str, Any]:
    """Report newer library candidates without changing pinned project bindings."""

    _require_fanfiction_mode(config)
    source_ids = [source_id] if source_id else list(_configured_sources(config))
    index = _library_index(create=False)
    indexed_items = [item for item in index.get("items") or [] if isinstance(item, dict)]
    works: list[dict[str, Any]] = []
    for selected_id in source_ids:
        contract = project_source_contract(config, selected_id)
        changes: list[dict[str, Any]] = []
        for binding in contract["binding"].get("items") or []:
            if not isinstance(binding, dict):
                continue
            pinned_id = str(binding.get("item_id") or "")
            current = library_item(pinned_id)
            if current.get("extraction_sha256") != binding.get("extraction_sha256"):
                changes.append(
                    {
                        "kind": "new_extraction",
                        "from_item_id": pinned_id,
                        "to_item_id": pinned_id,
                        "from_extraction_sha256": str(binding.get("extraction_sha256") or ""),
                        "to_extraction_sha256": str(current.get("extraction_sha256") or ""),
                    }
                )
            for candidate in indexed_items:
                if (
                    candidate.get("work_id") == current.get("work_id")
                    and candidate.get("supersedes_item_id") == pinned_id
                    and candidate.get("extraction_sha256")
                ):
                    changes.append(
                        {
                            "kind": "replacement_item",
                            "from_item_id": pinned_id,
                            "to_item_id": str(candidate.get("item_id") or ""),
                            "from_extraction_sha256": str(binding.get("extraction_sha256") or ""),
                            "to_extraction_sha256": str(candidate.get("extraction_sha256") or ""),
                        }
                    )
        works.append(
            {
                "source_id": selected_id,
                "work_id": str(contract["binding"].get("work_id") or ""),
                "binding_sha256": contract["binding_sha256"],
                "upgrade_available": bool(changes),
                "changes": changes,
            }
        )
    return {
        "schema": "fanfiction_source_upgrade_status_v1",
        "upgrade_available": any(item["upgrade_available"] for item in works),
        "works": works,
        "boundary": (
            "The project remains pinned to its current IDs and hashes. "
            "An upgrade proposal, independent semantic review, and human binding decision are required."
        ),
    }


def create_source_upgrade_proposal(
    config: ConfigDocument,
    *,
    source_id: str,
    created_by: str,
    target_item_id: str = "",
) -> dict[str, Any]:
    """Write a project-owned proposal; never update a binding or Canon here."""

    if created_by != "human":
        raise FanfictionSourceError("source upgrade proposals must be human-created")
    status = source_upgrade_status(config, source_id=source_id)
    work = status["works"][0]
    candidates = [
        item
        for item in work["changes"]
        if not target_item_id or item["to_item_id"] == target_item_id
    ]
    if not candidates and target_item_id:
        contract = project_source_contract(config, source_id)
        target = library_item(target_item_id)
        bound_ids = {
            str(item.get("item_id") or "")
            for item in contract["binding"].get("items") or []
            if isinstance(item, dict)
        }
        if (
            target.get("work_id") == work["work_id"]
            and target_item_id not in bound_ids
            and target.get("extraction_sha256")
        ):
            candidates = [
                {
                    "kind": "addition_item",
                    "from_item_id": "",
                    "to_item_id": target_item_id,
                    "from_extraction_sha256": "",
                    "to_extraction_sha256": str(target["extraction_sha256"]),
                }
            ]
    if not candidates:
        raise FanfictionSourceError("no matching source-library upgrade is available")
    if len(candidates) > 1 and not target_item_id:
        raise FanfictionSourceError("multiple upgrades are available; select --target-item-id")
    selected = candidates[0]
    root = resolve_project_root(config)
    canon = _read_json(root / "10_bible" / "fanfiction" / "source_canon.json", {})
    affected_fact_ids = _source_upgrade_fact_ids(
        canon,
        source_id=source_id,
        item_id=str(selected["from_item_id"]),
    )
    must_stale = _explicit_source_dependency_paths(root, affected_fact_ids)
    token = sha256(
        json.dumps(
            {
                "source_id": source_id,
                "binding_sha256": work["binding_sha256"],
                "change": selected,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    proposal = {
        "schema": SOURCE_UPGRADE_PROPOSAL_SCHEMA,
        "proposal_id": f"source_upgrade_{token}",
        "source_id": source_id,
        "work_id": work["work_id"],
        "current_binding_sha256": work["binding_sha256"],
        "change": selected,
        "affected_fact_ids": affected_fact_ids,
        "must_stale_artifacts": must_stale,
        "impact_method": "stable_fact_ids_and_explicit_refs_only",
        "status": "awaiting_independent_semantic_review_and_human_decision",
        "created_by": "human",
        "created_at": utc_now(),
        "binding_changed": False,
        "canon_changed": False,
    }
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    path = pack / "资料升级提案" / f"{proposal['proposal_id']}.json"
    _write_json(path, proposal)
    return {**proposal, "proposal_file": path.relative_to(root).as_posix()}


def apply_source_upgrade(
    config: ConfigDocument,
    *,
    proposal_path: str | Path,
    review_path: str | Path,
    decision_path: str | Path,
) -> dict[str, Any]:
    """Apply a future-only upgrade or route historical impact to revision_branch_v2."""

    root = resolve_project_root(config)
    proposal_file = _resolve_project_file(root, proposal_path)
    review_file = _resolve_project_file(root, review_path)
    decision_file = _resolve_project_file(root, decision_path)
    proposal = _read_json(proposal_file, None)
    review = _read_json(review_file, None)
    decision = _read_json(decision_file, None)
    errors = _validate_source_upgrade_bundle(config, proposal, review, decision)
    if errors:
        raise FanfictionSourceError("invalid source upgrade approval bundle: " + "; ".join(errors))
    source_id = str(proposal["source_id"])
    finalized = list_finalized_chapter_files(root)
    head = max((chapter for chapter, _path in finalized), default=0)
    earliest = review["earliest_affected_chapter"]
    if head and isinstance(earliest, int) and earliest <= head:
        from longform_engine.revision import create_versioned_revision_branch

        branch = create_versioned_revision_branch(
            config,
            from_chapter=earliest,
            to_chapter=head,
            reason=(
                f"source upgrade {proposal['proposal_id']}: "
                f"{decision['reason']}"
            ),
            created_by="human",
        )
        receipt = {
            "schema": "fanfiction_source_upgrade_receipt_v1",
            "proposal_id": proposal["proposal_id"],
            "status": "routed_to_revision_branch_v2",
            "revision_branch_id": branch.branch_id,
            "binding_changed": False,
            "canon_changed": False,
            "recorded_at": utc_now(),
        }
        receipt_file = proposal_file.with_name(proposal_file.stem + ".receipt.json")
        _write_json(receipt_file, receipt)
        return {**receipt, "receipt_file": receipt_file.relative_to(root).as_posix()}

    change = proposal["change"]
    result = bind_library_item(
        config,
        source_id=source_id,
        item_id=str(change["to_item_id"]),
        approved_by="human",
        upgrade_authorization=proposal,
    )
    if change["kind"] == "replacement_item" and change["from_item_id"] != change["to_item_id"]:
        _replace_project_binding_reference(
            config,
            source_id=source_id,
            from_item_id=str(change["from_item_id"]),
            to_item_id=str(change["to_item_id"]),
        )
    receipt = {
        "schema": "fanfiction_source_upgrade_receipt_v1",
        "proposal_id": proposal["proposal_id"],
        "status": "applied_future_source_upgrade",
        "source_id": source_id,
        "item_id": result["item_id"],
        "binding_changed": True,
        "canon_changed": False,
        "stale_artifacts": proposal["must_stale_artifacts"],
        "recorded_at": utc_now(),
    }
    receipt_file = proposal_file.with_name(proposal_file.stem + ".receipt.json")
    _write_json(receipt_file, receipt)
    return {**receipt, "receipt_file": receipt_file.relative_to(root).as_posix()}


def search_source_gap(
    config: ConfigDocument,
    *,
    source_id: str,
    gap: str,
    query: str,
    limit: int | None = None,
    fetcher: WebFetcher | None = None,
) -> dict[str, Any]:
    _configured_source(config, source_id)
    root = resolve_project_root(config)
    request_path: Path | None = None
    request_payload: dict[str, Any] | None = None
    if gap.startswith("source_gap_"):
        request_path, request_payload = _incremental_request(root, gap)
        if request_payload.get("source_id") != source_id:
            raise FanfictionSourceError("incremental source request belongs to another source pack")
        if request_payload.get("status") != "approved_for_search":
            raise FanfictionSourceError(
                "incremental source request requires human approval before network search"
            )
    status = coverage_gaps(config, source_id=source_id)
    if not any(gap in item for item in status["gaps"]):
        raise FanfictionSourceError("search must name a current approved coverage gap")
    result = search_web_candidates(config, query, limit=limit, fetcher=fetcher)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    token = sha256(f"{source_id}\0{gap}\0{query}".encode("utf-8")).hexdigest()[:16]
    path = pack / "搜索候选" / f"search_{token}.json"
    payload = {
        "schema": "fanfiction_source_search_candidates_v1",
        "source_id": source_id,
        "gap": gap,
        "query": query,
        "network_status": result["network_status"],
        "provider": result["provider"],
        "candidates": result["sources"],
        "status": "awaiting_human_source_selection",
        "created_at": utc_now(),
    }
    _write_json(path, payload)
    if request_path is not None and request_payload is not None:
        request_payload["status"] = "candidates_recorded"
        request_payload["network_performed"] = True
        request_payload["candidate_file"] = path.relative_to(root).as_posix()
        request_payload["searched_at"] = utc_now()
        _write_json(request_path, request_payload)
    return {**payload, "candidate_file": path.relative_to(root).as_posix()}


def create_external_work_request(
    config: ConfigDocument,
    *,
    work_name: str,
    purpose: str,
    reason: str,
) -> dict[str, Any]:
    if purpose not in EXTERNAL_PURPOSES:
        raise FanfictionSourceError("unsupported external work research purpose")
    if str(config.data.get("creation", {}).get("mode") or "") == "fanfiction":
        raise FanfictionSourceError("fanfiction projects must use the fanfiction source package workflow")
    work_name = str(work_name).strip()
    reason = str(reason).strip()
    if not work_name or not reason:
        raise FanfictionSourceError("external work name and research reason are required")
    root = resolve_project_root(config)
    request_id = "external_" + sha256(f"{work_name}\0{purpose}\0{reason}".encode("utf-8")).hexdigest()[:16]
    path = root / EXTERNAL_REQUEST_ROOT / f"{request_id}.json"
    payload = {
        "schema": EXTERNAL_REQUEST_SCHEMA,
        "request_id": request_id,
        "work_name": work_name,
        "purpose": purpose,
        "reason": reason,
        "status": "pending_human_approval",
        "network_performed": False,
        "created_at": utc_now(),
    }
    _write_json(path, payload)
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def approve_external_work_request(
    config: ConfigDocument,
    *,
    request: str,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise FanfictionSourceError("external work research approval requires --approved-by human")
    root = resolve_project_root(config)
    path, payload = _external_request(root, request)
    if payload["status"] not in {"pending_human_approval", "approved"}:
        raise FanfictionSourceError("external work request is not awaiting approval")
    if payload["purpose"] == "use_original_elements":
        payload["status"] = "mode_change_required"
        next_command = "edit project.yaml: set creation.mode=fanfiction and declare fanfiction.sources"
    else:
        payload["status"] = "approved"
        next_command = (
            "longform-engine research external-search project.yaml "
            f"--request {payload['request_id']} --query QUERY"
        )
    payload["approved_by"] = "human"
    payload["approved_at"] = utc_now()
    payload["next_command"] = next_command
    _write_json(path, payload)
    return {**payload, "request_file": path.relative_to(root).as_posix()}


def search_approved_external_work(
    config: ConfigDocument,
    *,
    request: str,
    query: str,
    limit: int | None = None,
    fetcher: WebFetcher | None = None,
) -> ResearchItemResult:
    root = resolve_project_root(config)
    request_path, payload = _external_request(root, request)
    if payload.get("status") != "approved":
        raise FanfictionSourceError("external work request requires explicit human approval before search")
    if payload.get("purpose") == "use_original_elements":
        raise FanfictionSourceError("original elements require creation.mode=fanfiction")
    result = search_research(config, query, limit=limit, fetcher=fetcher)
    item_path = Path(result.item_file)
    item = _read_json(item_path, {})
    item["external_work_request_id"] = payload["request_id"]
    item["external_work_purpose"] = payload["purpose"]
    _write_json(item_path, item)
    payload["status"] = "searched"
    payload["network_performed"] = True
    payload["research_item_id"] = result.item_id
    payload["searched_at"] = utc_now()
    _write_json(request_path, payload)
    return result


def validate_coverage_plan(
    plan: dict[str, Any],
    *,
    source: dict[str, Any],
    require_complete: bool,
    binding: dict[str, Any] | None = None,
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema",
        "source_id",
        "work_id",
        "coverage_mode",
        "authoritative_versions",
        "canon_cutoff",
        "units",
        "mode_change_reason",
        "approval",
    }
    if set(plan) != required:
        return ["全作覆盖计划字段不完整或包含未知字段"]
    if plan.get("schema") != COVERAGE_SCHEMA:
        errors.append(f"schema 必须为 {COVERAGE_SCHEMA}")
    if plan.get("source_id") != source.get("source_id"):
        errors.append("source_id 与 project.yaml 不一致")
    if not str(plan.get("work_id") or ""):
        errors.append("尚未选择并绑定全局作品ID")
    if binding and plan.get("work_id") != binding.get("work_id"):
        errors.append("覆盖计划作品ID与资料绑定不一致")
    if plan.get("canon_cutoff") != source.get("canon_cutoff"):
        errors.append("canon_cutoff 与 project.yaml 不一致")
    mode = str(plan.get("coverage_mode") or "")
    if mode not in COVERAGE_MODES:
        errors.append("coverage_mode 无效")
    if mode != "全作到截止点" and not str(plan.get("mode_change_reason") or "").strip():
        errors.append("非全作覆盖必须记录人工模式变更理由")
    versions = plan.get("authoritative_versions")
    if not isinstance(versions, list) or not versions or any(not str(item).strip() for item in versions):
        errors.append("authoritative_versions 必须包含至少一个权威版本")
    approval = plan.get("approval")
    if require_complete and (
        not isinstance(approval, dict)
        or approval.get("status") != "approved"
        or approval.get("approved_by") != "human"
    ):
        errors.append("全作覆盖计划尚未由人工批准")
    units = plan.get("units")
    if not isinstance(units, list) or not units:
        errors.append("units 必须包含人工批准的卷、章、集或番外目录")
        units = []
    bound_ids = {
        str(item.get("item_id"))
        for item in (binding or {}).get("items") or []
        if isinstance(item, dict) and item.get("item_id")
    }
    unit_ids: set[str] = set()
    for index, unit in enumerate(units):
        prefix = f"units[{index}]"
        fields = {
            "unit_id",
            "name",
            "version",
            "required_dimensions",
            "covered_dimensions",
            "status",
            "item_ids",
            "not_applicable_reason",
        }
        if not isinstance(unit, dict) or set(unit) != fields:
            errors.append(f"{prefix} 字段不完整或包含未知字段")
            continue
        unit_id = str(unit.get("unit_id") or "")
        if not _stable_id(unit_id) or unit_id in unit_ids:
            errors.append(f"{prefix}.unit_id 必须稳定且唯一")
        unit_ids.add(unit_id)
        if not str(unit.get("name") or "").strip() or not str(unit.get("version") or "").strip():
            errors.append(f"{prefix} 缺少名称或版本")
        required_dimensions = unit.get("required_dimensions")
        covered_dimensions = unit.get("covered_dimensions")
        if not isinstance(required_dimensions, list) or not required_dimensions:
            errors.append(f"{prefix}.required_dimensions 不能为空")
            required_dimensions = []
        if not isinstance(covered_dimensions, list):
            errors.append(f"{prefix}.covered_dimensions 必须为列表")
            covered_dimensions = []
        status = str(unit.get("status") or "")
        if status not in COVERAGE_STATES:
            errors.append(f"{prefix}.status 无效")
        item_ids = unit.get("item_ids")
        if not isinstance(item_ids, list):
            errors.append(f"{prefix}.item_ids 必须为列表")
            item_ids = []
        if status == "已覆盖":
            if not item_ids:
                errors.append(f"{prefix} 标记已覆盖但没有资料项")
            if require_complete and any(str(item_id) not in bound_ids for item_id in item_ids):
                errors.append(f"{prefix} 引用了未绑定的资料项")
            missing = sorted(set(map(str, required_dimensions)) - set(map(str, covered_dimensions)))
            if missing:
                errors.append(f"{prefix} 缺少必需维度：{', '.join(missing)}")
        elif status == "不适用":
            if not str(unit.get("not_applicable_reason") or "").strip():
                errors.append(f"{prefix} 标记不适用但没有人工理由")
        elif require_complete:
            errors.append(f"{prefix} 尚未完成：{status or '未设置'}")
    return errors


def project_source_pack_dir(
    root: Path,
    source_id: str,
    *,
    create: bool,
    config: ConfigDocument,
) -> Path:
    source = _configured_source(config, source_id)
    base = root / PROJECT_PACK_ROOT
    if base.is_dir():
        for setting in base.glob("*/作品资料设定.yaml"):
            payload = _read_yaml(setting, {})
            if isinstance(payload, dict) and payload.get("资料源ID") == source_id:
                return setting.parent
    path = base / _visible_name(str(source["title"]), field="title")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def library_work(work_id: str) -> dict[str, Any]:
    index = _library_index(create=False)
    record = next(
        (item for item in index.get("works") or [] if isinstance(item, dict) and item.get("work_id") == work_id),
        None,
    )
    if record is None:
        raise FanfictionSourceError(f"unknown source library work_id: {work_id}")
    directory = _resolve_library_work_directory(record)
    raw_identity = _read_yaml(directory / "作品身份.yaml", {})
    identity = _normalize_library_work(raw_identity)
    if identity.get("schema") != LIBRARY_WORK_SCHEMA:
        raise FanfictionSourceError(f"source library work identity is invalid: {work_id}")
    return {
        **record,
        **identity,
        "path": directory.relative_to(source_library_root()).as_posix(),
    }


def library_item(item_id: str) -> dict[str, Any]:
    index = _library_index(create=False)
    record = next(
        (item for item in index.get("items") or [] if isinstance(item, dict) and item.get("item_id") == item_id),
        None,
    )
    if record is None:
        raise FanfictionSourceError(f"unknown source library item_id: {item_id}")
    directory = _resolve_library_item_directory(record)
    raw_source = _read_yaml(directory / "来源说明.yaml", {})
    source = _normalize_library_item(raw_source)
    if source.get("schema") != LIBRARY_ITEM_SCHEMA:
        raise FanfictionSourceError(f"source library item metadata is invalid: {item_id}")
    return {
        **record,
        **source,
        "path": directory.relative_to(source_library_root()).as_posix(),
    }


def source_fact_records(source: dict[str, Any], *types: str) -> list[dict[str, Any]]:
    """Select v2 dynamic facts by stable semantic type or its Chinese author label."""

    accepted: set[str] = set()
    for fact_type in types:
        accepted.update(FACT_TYPE_ALIASES.get(fact_type, {fact_type}))
    return [
        item
        for item in source.get("facts") or []
        if isinstance(item, dict) and str(item.get("type") or "") in accepted
    ]


def library_item_texts(item_id: str) -> dict[str, str]:
    """Read currently available user-owned source text for deterministic similarity checks."""

    item = library_item(item_id)
    errors: list[str] = []
    texts = _library_item_content_texts(item, errors)
    if errors:
        raise FanfictionSourceError("; ".join(errors))
    return texts


def _normalize_coverage_plan(candidate: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("schema") == COVERAGE_SCHEMA:
        normalized = dict(candidate)
        normalized.setdefault("approval", {"status": "draft", "approved_by": "", "approved_at": ""})
        return normalized
    if candidate.get("协议版本") not in {"同人全作覆盖计划_第1版", COVERAGE_SCHEMA}:
        raise FanfictionSourceError("coverage plan protocol is not recognized")
    units = []
    for unit in candidate.get("目录单元") or []:
        if not isinstance(unit, dict):
            continue
        units.append(
            {
                "unit_id": unit.get("单元ID"),
                "name": unit.get("名称"),
                "version": unit.get("版本"),
                "required_dimensions": unit.get("必需维度") or [],
                "covered_dimensions": unit.get("已覆盖维度") or [],
                "status": unit.get("状态") or "待补全",
                "item_ids": unit.get("资料项ID列表") or [],
                "not_applicable_reason": unit.get("不适用理由") or "",
            }
        )
    chinese_approval: dict[str, Any] = (
        candidate["人工批准"] if isinstance(candidate.get("人工批准"), dict) else {}
    )
    return {
        "schema": COVERAGE_SCHEMA,
        "source_id": candidate.get("资料源ID") or source["source_id"],
        "work_id": candidate.get("作品ID") or "",
        "coverage_mode": candidate.get("覆盖模式") or "全作到截止点",
        "authoritative_versions": candidate.get("权威版本") or [],
        "canon_cutoff": candidate.get("截止点") or source["canon_cutoff"],
        "units": units,
        "mode_change_reason": candidate.get("模式变更理由") or "",
        "approval": {
            "status": chinese_approval.get("状态", "draft"),
            "approved_by": chinese_approval.get("批准人", ""),
            "approved_at": chinese_approval.get("批准时间", ""),
        },
    }


def _read_coverage_plan(path: Path, source: dict[str, Any]) -> dict[str, Any]:
    payload = _read_yaml(path, {})
    if not isinstance(payload, dict):
        return {}
    try:
        return _normalize_coverage_plan(payload, source)
    except FanfictionSourceError:
        return payload


def _coverage_to_chinese(plan: dict[str, Any]) -> dict[str, Any]:
    approval: dict[str, Any] = plan["approval"] if isinstance(plan.get("approval"), dict) else {}
    return {
        "协议版本": COVERAGE_SCHEMA,
        "资料源ID": plan.get("source_id", ""),
        "作品ID": plan.get("work_id", ""),
        "覆盖模式": plan.get("coverage_mode", "全作到截止点"),
        "权威版本": plan.get("authoritative_versions") or [],
        "截止点": plan.get("canon_cutoff", ""),
        "目录单元": [
            {
                "单元ID": unit.get("unit_id", ""),
                "名称": unit.get("name", ""),
                "版本": unit.get("version", ""),
                "必需维度": unit.get("required_dimensions") or [],
                "已覆盖维度": unit.get("covered_dimensions") or [],
                "状态": unit.get("status", "待补全"),
                "资料项ID列表": unit.get("item_ids") or [],
                "不适用理由": unit.get("not_applicable_reason", ""),
            }
            for unit in plan.get("units") or []
            if isinstance(unit, dict)
        ],
        "模式变更理由": plan.get("mode_change_reason", ""),
        "人工批准": {
            "状态": approval.get("status", "draft"),
            "批准人": approval.get("approved_by", ""),
            "批准时间": approval.get("approved_at", ""),
        },
    }


def _read_project_binding(path: Path) -> dict[str, Any]:
    payload = _read_yaml(path, {})
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == PROJECT_BINDING_SCHEMA:
        return payload
    if payload.get("协议版本") != PROJECT_BINDING_SCHEMA:
        return payload
    items = []
    for entry in payload.get("固定资料") or []:
        if not isinstance(entry, dict):
            continue
        items.append(
            {
                "item_id": entry.get("资料项ID", ""),
                "content_sha256": entry.get("内容哈希", ""),
                "extraction_sha256": entry.get("提取哈希", ""),
                "library_uri": entry.get("资料库引用", ""),
                "project_item_name": entry.get("项目资料名称", ""),
                "approved_by": entry.get("人工批准", ""),
                "bound_at": entry.get("绑定时间", ""),
            }
        )
    return {
        "schema": PROJECT_BINDING_SCHEMA,
        "source_id": payload.get("资料源ID", ""),
        "work_id": payload.get("作品ID", ""),
        "items": items,
        "updated_at": payload.get("更新时间", ""),
    }


def _version_conflict_ids(pack: Path) -> list[str]:
    conflict_ids: set[str] = set()
    items_dir = pack / "资料项"
    for extraction_file in items_dir.glob("*/提取结果.json") if items_dir.is_dir() else ():
        extraction = _read_json(extraction_file, {})
        for fact in extraction.get("facts") or [] if isinstance(extraction, dict) else []:
            if not isinstance(fact, dict):
                continue
            if str(fact.get("type") or "") in FACT_TYPE_ALIASES["version_conflict"] and fact.get("id"):
                conflict_ids.add(str(fact["id"]))
    return sorted(conflict_ids)


def _version_conflict_gaps(pack: Path, source_id: str) -> list[str]:
    conflict_ids = _version_conflict_ids(pack)
    if not conflict_ids:
        return []
    raw = _read_yaml(pack / "版本冲突决定.yaml", {})
    normalized = _normalize_version_conflict_decisions(raw, source_id=source_id)
    errors = _validate_version_conflict_decisions(
        normalized,
        expected_ids=conflict_ids,
        require_approval=True,
    )
    if normalized.get("source_id") != source_id:
        errors.append("source_id 与目标资料包不一致")
    return [f"版本冲突未解决：{error}" for error in errors]


def _normalize_version_conflict_decisions(payload: Any, *, source_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == VERSION_CONFLICT_DECISION_SCHEMA:
        return payload
    decisions = []
    for item in payload.get("决定") or []:
        if not isinstance(item, dict):
            continue
        decisions.append(
            {
                "conflict_fact_id": item.get("冲突事实ID", ""),
                "resolution": item.get("处理", ""),
                "selected_version": item.get("采用版本", ""),
                "reason": item.get("说明", ""),
            }
        )
    approval: dict[str, Any] = payload["人工批准"] if isinstance(payload.get("人工批准"), dict) else {}
    return {
        "schema": payload.get("协议版本", ""),
        "source_id": payload.get("资料源ID", source_id),
        "decisions": decisions,
        "approved_by": approval.get("批准人", ""),
        "approved_at": approval.get("批准时间", ""),
    }


def _validate_version_conflict_decisions(
    payload: dict[str, Any],
    *,
    expected_ids: list[str],
    require_approval: bool,
) -> list[str]:
    fields = {"schema", "source_id", "decisions", "approved_by", "approved_at"}
    if not isinstance(payload, dict) or set(payload) != fields:
        return ["决定文件字段无效"]
    errors: list[str] = []
    if payload.get("schema") != VERSION_CONFLICT_DECISION_SCHEMA:
        errors.append(f"schema 必须为 {VERSION_CONFLICT_DECISION_SCHEMA}")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        return [*errors, "decisions 必须为列表"]
    ids: list[str] = []
    for index, item in enumerate(decisions):
        prefix = f"decisions[{index}]"
        if not isinstance(item, dict) or set(item) != {
            "conflict_fact_id",
            "resolution",
            "selected_version",
            "reason",
        }:
            errors.append(f"{prefix} 字段无效")
            continue
        fact_id = str(item.get("conflict_fact_id") or "")
        ids.append(fact_id)
        if item.get("resolution") not in {"选择版本", "并存隔离", "排除"}:
            errors.append(f"{prefix}.resolution 必须为选择版本、并存隔离或排除")
        if item.get("resolution") == "选择版本" and not str(item.get("selected_version") or "").strip():
            errors.append(f"{prefix}.selected_version 不能为空")
        if not str(item.get("reason") or "").strip():
            errors.append(f"{prefix}.reason 不能为空")
    if len(ids) != len(set(ids)):
        errors.append("冲突事实决定不能重复")
    if set(ids) != set(expected_ids):
        errors.append("决定必须精确覆盖当前全部版本冲突事实")
    if require_approval and payload.get("approved_by") != "human":
        errors.append("版本冲突决定尚未由人工批准")
    return errors


def _version_conflict_decisions_to_chinese(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "协议版本": VERSION_CONFLICT_DECISION_SCHEMA,
        "资料源ID": payload.get("source_id", ""),
        "决定": [
            {
                "冲突事实ID": item.get("conflict_fact_id", ""),
                "处理": item.get("resolution", ""),
                "采用版本": item.get("selected_version", ""),
                "说明": item.get("reason", ""),
            }
            for item in payload.get("decisions") or []
            if isinstance(item, dict)
        ],
        "人工批准": {
            "批准人": payload.get("approved_by", ""),
            "批准时间": payload.get("approved_at", ""),
        },
    }


def _write_version_conflict_report(pack: Path, payload: dict[str, Any]) -> None:
    lines = ["# 版本冲突清单", ""]
    for item in payload.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        lines.extend(
            [
                f"- 冲突事实：{item.get('conflict_fact_id', '')}",
                f"  - 人工处理：{item.get('resolution', '')}",
                f"  - 采用版本：{item.get('selected_version') or '不适用'}",
                f"  - 说明：{item.get('reason', '')}",
            ]
        )
    atomic_write_text(pack / "版本冲突清单.md", "\n".join(lines) + "\n")


def _binding_to_chinese(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "协议版本": PROJECT_BINDING_SCHEMA,
        "资料源ID": binding.get("source_id", ""),
        "作品ID": binding.get("work_id", ""),
        "固定资料": [
            {
                "资料项ID": entry.get("item_id", ""),
                "内容哈希": entry.get("content_sha256", ""),
                "提取哈希": entry.get("extraction_sha256", ""),
                "资料库引用": entry.get("library_uri", ""),
                "项目资料名称": entry.get("project_item_name", ""),
                "人工批准": entry.get("approved_by", ""),
                "绑定时间": entry.get("bound_at", ""),
            }
            for entry in binding.get("items") or []
            if isinstance(entry, dict)
        ],
        "更新时间": binding.get("updated_at", ""),
    }


def _normalize_library_work(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == LIBRARY_WORK_SCHEMA:
        return payload
    return {
        "schema": payload.get("协议版本"),
        "work_id": payload.get("作品ID"),
        "name": payload.get("作品名称"),
        "creator": payload.get("原作者"),
        "aliases": payload.get("作品别名") or [],
        "versions": payload.get("版本列表") or [],
        "approved_by": payload.get("人工批准"),
        "created_at": payload.get("创建时间"),
    }


def _normalize_library_item(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if payload.get("schema") == LIBRARY_ITEM_SCHEMA:
        return payload
    return {
        "schema": payload.get("协议版本"),
        "item_id": payload.get("资料项ID"),
        "work_id": payload.get("作品ID"),
        "name": payload.get("资料名称"),
        "source_type": payload.get("资料类型"),
        "version": payload.get("版本名称"),
        "unit_range": payload.get("覆盖范围"),
        "source_method": payload.get("来源方式"),
        "source_locator": payload.get("来源定位"),
        "rights_status": payload.get("权利状态"),
        "retention_mode": payload.get("留存方式"),
        "content_sha256": payload.get("内容哈希"),
        "content_files": payload.get("内容文件") or [],
        "supersedes_item_id": payload.get("替代资料项ID", ""),
        "approved_by": payload.get("人工批准"),
        "created_at": payload.get("创建时间"),
    }


def _binding_errors(binding: dict[str, Any], *, require_available: bool) -> list[str]:
    errors: list[str] = []
    if binding.get("schema") != PROJECT_BINDING_SCHEMA:
        return ["资料绑定 schema 无效"]
    if not str(binding.get("work_id") or ""):
        errors.append("尚未绑定全局作品ID")
    items = binding.get("items")
    if not isinstance(items, list) or not items:
        errors.append("尚未绑定任何已批准资料项")
        return errors
    for index, entry in enumerate(items):
        if not isinstance(entry, dict):
            errors.append(f"资料绑定 items[{index}] 无效")
            continue
        try:
            item = library_item(str(entry.get("item_id") or ""))
        except FanfictionSourceError as exc:
            errors.append(str(exc))
            continue
        if entry.get("content_sha256") != item.get("content_sha256"):
            errors.append(f"资料项 {item['item_id']} 内容哈希漂移")
        pinned_extraction = str(entry.get("extraction_sha256") or "")
        item_dir = source_library_root() / str(item["path"])
        if not pinned_extraction or not (item_dir / "提取版本" / f"{pinned_extraction}.json").is_file():
            errors.append(f"资料项 {item['item_id']} 固定提取版本不可用")
        if require_available:
            content_errors: list[str] = []
            _library_item_content_texts(item, content_errors)
            errors.extend(content_errors)
    return errors


def _source_upgrade_fact_ids(
    canon: Any,
    *,
    source_id: str,
    item_id: str,
) -> list[str]:
    if not isinstance(canon, dict) or canon.get("schema") != CANON_SCHEMA:
        return []
    source = next(
        (
            item
            for item in canon.get("sources") or []
            if isinstance(item, dict) and item.get("source_id") == source_id
        ),
        {},
    )
    evidence_ids = {
        str(item.get("evidence_id") or "")
        for item in source.get("evidence") or []
        if isinstance(item, dict) and item.get("item_id") == item_id
    }
    return sorted(
        {
            str(fact.get("id") or "")
            for fact in source.get("facts") or []
            if isinstance(fact, dict)
            and evidence_ids.intersection(str(ref) for ref in fact.get("evidence_refs") or [])
            and fact.get("id")
        }
    )


def _mark_source_binding_dependents_stale(
    root: Path,
    *,
    source_id: str,
    item_id: str,
    artifact_paths: list[str],
) -> None:
    state_path = root / "30_state" / "novel_state.json"
    state = _read_json(state_path, {})
    markers = state.get("project_intelligence") if isinstance(state, dict) else None
    if not isinstance(markers, dict):
        markers = {}
    now = utc_now()
    marker_keys = {"fanfiction_canon"}
    if "10_bible/fanfiction/fanfiction_bible.json" in artifact_paths:
        marker_keys.add("fanfiction_design")
    if any(
        path in artifact_paths
        for path in ("10_bible/characters.json", "10_bible/character_expression.json")
    ):
        marker_keys.update(("book_design", "character_expression_design"))
    if any(path.startswith("20_outline/") for path in artifact_paths):
        marker_keys.add("outline_design")
    for key in marker_keys:
        marker = markers.get(key)
        if not isinstance(marker, dict) or marker.get("status") != "applied":
            continue
        markers[key] = {
            **marker,
            "status": "stale",
            "stale_reason": "fanfiction_source_binding_changed",
            "stale_at": now,
            "source_id": source_id,
            "item_id": item_id,
        }
    state["project_intelligence"] = markers
    _write_json(state_path, state)
    stale_path = root / "30_state" / "stale_artifacts.json"
    stale = _read_json(stale_path, {"schema": "stale_artifact_registry_v1", "items": []})
    items = stale.get("items") if isinstance(stale, dict) else None
    if not isinstance(items, list):
        items = []
    by_path = {
        str(row.get("artifact_path") or ""): row
        for row in items
        if isinstance(row, dict) and row.get("artifact_path")
    }
    for path in artifact_paths:
        by_path[path] = {
            "artifact_path": path,
            "classification": "must_stale",
            "dependency_fact_ids": [],
            "source": "fanfiction_source_binding",
            "source_id": source_id,
            "item_id": item_id,
            "state": "stale",
            "stale_at": now,
        }
    _write_json(
        stale_path,
        {"schema": "stale_artifact_registry_v1", "items": list(by_path.values())},
    )


def _validate_source_upgrade_bundle(
    config: ConfigDocument,
    proposal: Any,
    review: Any,
    decision: Any,
) -> list[str]:
    errors: list[str] = []
    proposal_fields = {
        "schema",
        "proposal_id",
        "source_id",
        "work_id",
        "current_binding_sha256",
        "change",
        "affected_fact_ids",
        "must_stale_artifacts",
        "impact_method",
        "status",
        "created_by",
        "created_at",
        "binding_changed",
        "canon_changed",
    }
    if not isinstance(proposal, dict) or set(proposal) != proposal_fields:
        return ["source upgrade proposal fields are invalid"]
    if proposal.get("schema") != SOURCE_UPGRADE_PROPOSAL_SCHEMA:
        errors.append(f"proposal schema must be {SOURCE_UPGRADE_PROPOSAL_SCHEMA}")
    if proposal.get("created_by") != "human":
        errors.append("source upgrade proposal must be human-created")
    if proposal.get("binding_changed") is not False or proposal.get("canon_changed") is not False:
        errors.append("source upgrade proposal must be non-applying")
    if proposal.get("impact_method") != "stable_fact_ids_and_explicit_refs_only":
        errors.append("source upgrade impact must use stable fact IDs and explicit refs only")
    source_id = str(proposal.get("source_id") or "")
    try:
        contract = project_source_contract(config, source_id)
    except (FanfictionSourceError, KeyError, OSError) as exc:
        errors.append(f"current project source contract is unavailable: {exc}")
        contract = {}
    if proposal.get("current_binding_sha256") != contract.get("binding_sha256"):
        errors.append("source upgrade proposal binding is stale")
    change = proposal.get("change")
    change_fields = {
        "kind",
        "from_item_id",
        "to_item_id",
        "from_extraction_sha256",
        "to_extraction_sha256",
    }
    if not isinstance(change, dict) or set(change) != change_fields:
        errors.append("source upgrade change fields are invalid")
    else:
        try:
            target = library_item(str(change.get("to_item_id") or ""))
        except FanfictionSourceError as exc:
            errors.append(str(exc))
        else:
            if change.get("to_extraction_sha256") != target.get("extraction_sha256"):
                errors.append("source upgrade target extraction changed after proposal")
            if target.get("work_id") != proposal.get("work_id"):
                errors.append("source upgrade target belongs to another work")
    canon = _read_json(
        resolve_project_root(config) / "10_bible" / "fanfiction" / "source_canon.json",
        {},
    )
    expected_fact_ids = _source_upgrade_fact_ids(
        canon,
        source_id=source_id,
        item_id=str((change or {}).get("from_item_id") or ""),
    )
    expected_stale = _explicit_source_dependency_paths(resolve_project_root(config), expected_fact_ids)
    if proposal.get("affected_fact_ids") != expected_fact_ids:
        errors.append("affected_fact_ids do not match current project Canon evidence")
    if proposal.get("must_stale_artifacts") != expected_stale:
        errors.append("must_stale_artifacts cannot omit or downgrade explicit dependencies")

    review_fields = {
        "schema",
        "proposal_sha256",
        "independent_from_proposer",
        "verdict",
        "affected_fact_ids",
        "must_stale_artifacts",
        "earliest_affected_chapter",
        "reason",
        "reviewed_by",
    }
    if not isinstance(review, dict) or set(review) != review_fields:
        errors.append("source upgrade semantic review fields are invalid")
    else:
        if review.get("schema") != SOURCE_UPGRADE_REVIEW_SCHEMA:
            errors.append(f"review schema must be {SOURCE_UPGRADE_REVIEW_SCHEMA}")
        if review.get("proposal_sha256") != _json_hash(proposal):
            errors.append("source upgrade review proposal hash is stale")
        if review.get("independent_from_proposer") is not True:
            errors.append("source upgrade semantic review must be independent")
        if review.get("verdict") != "pass":
            errors.append("source upgrade apply requires a passing semantic review")
        if review.get("affected_fact_ids") != expected_fact_ids:
            errors.append("semantic review cannot omit deterministic affected facts")
        if review.get("must_stale_artifacts") != expected_stale:
            errors.append("semantic review cannot downgrade deterministic must_stale artifacts")
        earliest = review.get("earliest_affected_chapter")
        if earliest is not None and (
            not isinstance(earliest, int) or isinstance(earliest, bool) or earliest <= 0
        ):
            errors.append("earliest_affected_chapter must be positive or null")
        for field in ("reason", "reviewed_by"):
            if not isinstance(review.get(field), str) or not review[field].strip():
                errors.append(f"source upgrade review {field} must be non-empty")

    decision_fields = {
        "schema",
        "proposal_sha256",
        "semantic_review_sha256",
        "decision",
        "reason",
        "decided_by",
    }
    if not isinstance(decision, dict) or set(decision) != decision_fields:
        errors.append("source upgrade human decision fields are invalid")
    else:
        if decision.get("schema") != SOURCE_UPGRADE_DECISION_SCHEMA:
            errors.append(f"decision schema must be {SOURCE_UPGRADE_DECISION_SCHEMA}")
        if decision.get("proposal_sha256") != _json_hash(proposal):
            errors.append("source upgrade decision proposal hash is stale")
        if isinstance(review, dict) and decision.get("semantic_review_sha256") != _json_hash(review):
            errors.append("source upgrade decision semantic review hash is stale")
        if decision.get("decision") != "approve":
            errors.append("source upgrade is not human-approved")
        if decision.get("decided_by") != "human":
            errors.append("source upgrade decision must be human-owned")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            errors.append("source upgrade decision reason must be non-empty")
    return errors


def _replace_project_binding_reference(
    config: ConfigDocument,
    *,
    source_id: str,
    from_item_id: str,
    to_item_id: str,
) -> None:
    root = resolve_project_root(config)
    source = _configured_source(config, source_id)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    binding_path = pack / "资料绑定.yaml"
    binding = _read_project_binding(binding_path)
    binding["items"] = [
        item
        for item in binding.get("items") or []
        if isinstance(item, dict) and item.get("item_id") != from_item_id
    ]
    binding["updated_at"] = utc_now()
    _write_yaml(binding_path, _binding_to_chinese(binding))
    plan_path = pack / "全作覆盖计划.yaml"
    plan = _read_coverage_plan(plan_path, source)
    for unit in plan.get("units") or []:
        if not isinstance(unit, dict):
            continue
        unit["item_ids"] = list(
            dict.fromkeys(
                to_item_id if item_id == from_item_id else item_id
                for item_id in unit.get("item_ids") or []
            )
        )
    _write_yaml(plan_path, _coverage_to_chinese(plan))
    _write_coverage_report(config, source_id)


def _resolve_project_file(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    resolved = (path if path.is_absolute() else root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise FanfictionSourceError("source upgrade approval files must live under the project root") from exc
    if not resolved.is_file():
        raise FanfictionSourceError(f"source upgrade approval file does not exist: {resolved}")
    return resolved


def _json_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _explicit_source_dependency_paths(root: Path, fact_ids: list[str]) -> list[str]:
    paths = {"10_bible/fanfiction/source_canon.json"}
    if not fact_ids:
        return sorted(paths)
    for relative_dir in ("10_bible", "20_outline", "30_state", "50_workbench/writing_tasks"):
        directory = root / relative_dir
        if not directory.is_dir():
            continue
        for path in directory.rglob("*.json"):
            if path == root / "10_bible" / "fanfiction" / "source_canon.json":
                continue
            payload = _read_json(path, None)
            if any(_contains_exact_value(payload, fact_id) for fact_id in fact_ids):
                paths.add(path.relative_to(root).as_posix())
    return sorted(paths)


def _contains_exact_value(value: Any, expected: str) -> bool:
    if isinstance(value, str):
        return value == expected
    if isinstance(value, list):
        return any(_contains_exact_value(item, expected) for item in value)
    if isinstance(value, dict):
        return any(_contains_exact_value(item, expected) for item in value.values())
    return False


def _library_item_content_texts(item: dict[str, Any], errors: list[str]) -> dict[str, str]:
    root = source_library_root()
    texts: dict[str, str] = {}
    content_files = item.get("content_files")
    if not isinstance(content_files, list):
        errors.append(f"source item {item.get('item_id')} content_files is invalid")
        return texts
    if item.get("retention_mode") == "metadata_only":
        return texts
    combined = bytearray()
    for relative in content_files:
        path = root / str(relative)
        if not path.is_file():
            path = root / str(item.get("path") or "") / "内容" / Path(str(relative)).name
        if not path.is_file():
            errors.append(f"source item {item.get('item_id')} content is unavailable")
            continue
        try:
            text = path.read_text(encoding="utf-8").lstrip("\ufeff")
        except UnicodeDecodeError:
            errors.append(f"source item {item.get('item_id')} content must be UTF-8 text")
            continue
        texts[str(relative)] = text
        combined.extend(path.read_bytes())
    if content_files and sha256(bytes(combined)).hexdigest() != item.get("content_sha256"):
        errors.append(f"source item {item.get('item_id')} content hash does not match")
    return texts


def _resolve_library_work_directory(record: dict[str, Any]) -> Path:
    root = source_library_root()
    configured = root / str(record.get("path") or "")
    if configured.is_dir():
        return configured
    work_id = str(record.get("work_id") or "")
    for identity_file in (root / "作品").glob("*/作品身份.yaml"):
        identity = _normalize_library_work(_read_yaml(identity_file, {}))
        if identity.get("work_id") == work_id:
            return identity_file.parent
    raise FanfictionSourceError(f"source library work directory is unavailable: {work_id}")


def _resolve_library_item_directory(record: dict[str, Any]) -> Path:
    root = source_library_root()
    configured = root / str(record.get("path") or "")
    if configured.is_dir():
        return configured
    item_id = str(record.get("item_id") or "")
    for source_file in (root / "作品").glob("*/资料项/*/来源说明.yaml"):
        source = _normalize_library_item(_read_yaml(source_file, {}))
        if source.get("item_id") == item_id:
            return source_file.parent
    raise FanfictionSourceError(f"source library item directory is unavailable: {item_id}")


def _write_coverage_report(config: ConfigDocument, source_id: str) -> None:
    root = resolve_project_root(config)
    pack = project_source_pack_dir(root, source_id, create=False, config=config)
    if not pack.is_dir():
        return
    status = coverage_gaps(config, source_id=source_id)
    lines = ["# 资料覆盖情况", "", f"- 资料源ID：{source_id}", f"- 全量门禁：{'通过' if status['complete'] else '未通过'}", ""]
    if status["gaps"]:
        lines.extend(["## 当前缺口", "", *[f"- {item}" for item in status["gaps"]], ""])
    else:
        lines.extend(["全部人工批准单元已经由固定资料项覆盖。", ""])
    atomic_write_text(pack / "资料覆盖情况.md", "\n".join(lines))


def _external_request(root: Path, request: str) -> tuple[Path, dict[str, Any]]:
    candidate = Path(request)
    if candidate.suffix:
        path = candidate if candidate.is_absolute() else root / candidate
    else:
        path = root / EXTERNAL_REQUEST_ROOT / f"{request}.json"
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise FanfictionSourceError("external work request must live under the project root") from exc
    payload = _read_json(path, None)
    if not isinstance(payload, dict) or payload.get("schema") != EXTERNAL_REQUEST_SCHEMA:
        raise FanfictionSourceError(f"invalid external work research request: {request}")
    return path, payload


def _incremental_request_gaps(pack: Path) -> list[str]:
    gaps: list[str] = []
    directory = pack / "待审资料需求"
    for path in sorted(directory.glob("source_gap_*.json")) if directory.is_dir() else ():
        payload = _read_json(path, {})
        if not isinstance(payload, dict) or payload.get("schema") != INCREMENTAL_REQUEST_SCHEMA:
            gaps.append(f"增量资料需求文件无效：{path.name}")
            continue
        status = str(payload.get("status") or "")
        if status == "resolved":
            continue
        gaps.append(
            f"增量资料需求 {payload.get('request_id', path.stem)}："
            f"{payload.get('need', '未说明')}（{status or '状态缺失'}）"
        )
    return gaps


def _incremental_request(root: Path, request_id: str) -> tuple[Path, dict[str, Any]]:
    matches = list((root / PROJECT_PACK_ROOT).glob(f"*/待审资料需求/{request_id}.json"))
    if len(matches) != 1:
        raise FanfictionSourceError(f"unknown or ambiguous incremental source request: {request_id}")
    payload = _read_json(matches[0], {})
    if not isinstance(payload, dict) or payload.get("schema") != INCREMENTAL_REQUEST_SCHEMA:
        raise FanfictionSourceError(f"invalid incremental source request: {request_id}")
    return matches[0], payload


def _configured_sources(config: ConfigDocument) -> dict[str, dict[str, Any]]:
    fanfiction: dict[str, Any] = (
        config.data["fanfiction"] if isinstance(config.data.get("fanfiction"), dict) else {}
    )
    return {
        str(item["source_id"]): item
        for item in fanfiction.get("sources") or []
        if isinstance(item, dict) and item.get("source_id")
    }


def _configured_source(config: ConfigDocument, source_id: str) -> dict[str, Any]:
    source = _configured_sources(config).get(source_id)
    if source is None:
        raise FanfictionSourceError(f"unknown configured fanfiction source_id: {source_id}")
    return source


def _require_fanfiction_mode(config: ConfigDocument) -> None:
    if str(config.data.get("creation", {}).get("mode") or "") != "fanfiction":
        raise FanfictionSourceError("fanfiction source packages require creation.mode=fanfiction")


def _library_index(*, create: bool) -> dict[str, Any]:
    root = source_library_root()
    path = root / "资料库索引.json"
    if create and not path.is_file():
        initialize_source_library()
    payload = _read_json(path, {"schema": LIBRARY_INDEX_SCHEMA, "works": [], "items": []})
    if not isinstance(payload, dict) or payload.get("schema") != LIBRARY_INDEX_SCHEMA:
        raise FanfictionSourceError(f"source library index is invalid: {path}")
    if not isinstance(payload.get("works"), list) or not isinstance(payload.get("items"), list):
        raise FanfictionSourceError(f"source library index collections are invalid: {path}")
    return payload


def _write_library_index(index: dict[str, Any]) -> None:
    index["updated_at"] = utc_now()
    _write_json(source_library_root() / "资料库索引.json", index)


def _find_library_work(name: str, creator: str) -> dict[str, Any] | None:
    for work in _library_index(create=False).get("works") or []:
        if not isinstance(work, dict):
            continue
        if str(work.get("name") or "").casefold() == name.casefold() and str(
            work.get("creator") or ""
        ).casefold() == creator.casefold():
            return work
    return None


def _unique_project_item_directory(base: Path, name: str, item_id: str) -> Path:
    if base.is_dir():
        for binding in base.glob("*/来源绑定.yaml"):
            payload = _read_yaml(binding, {})
            if isinstance(payload, dict) and (payload.get("item_id") or payload.get("资料项ID")) == item_id:
                return binding.parent
    return _unique_named_directory(base, _visible_name(name, field="item name"), stable_id=item_id)


def _unique_named_directory(base: Path, name: str, *, stable_id: str) -> Path:
    candidate = base / name
    if not candidate.exists():
        return candidate
    for identity_name in ("作品身份.yaml", "来源说明.yaml", "来源绑定.yaml"):
        identity = _read_yaml(candidate / identity_name, {})
        if isinstance(identity, dict) and stable_id in {
            identity.get("work_id"),
            identity.get("item_id"),
            identity.get("作品ID"),
            identity.get("资料项ID"),
        }:
            return candidate
    return base / f"{name}（{stable_id[-8:]}）"


def _visible_name(value: str, *, field: str) -> str:
    cleaned = " ".join(str(value).split()).strip(" .")
    if not cleaned or cleaned in {".", ".."}:
        raise FanfictionSourceError(f"{field} is required")
    if any(character in cleaned for character in '<>:"/\\|?*'):
        raise FanfictionSourceError(f"{field} contains characters that are unsafe in a directory name")
    return cleaned[:120]


def _visible_filename(value: str) -> str:
    name = _visible_name(value, field="filename")
    if name in {"作品身份.yaml", "来源说明.yaml", "提取结果.json", "证据索引.json"}:
        return "原件_" + name
    return name


def _string_list(values: Iterable[str], *, field: str) -> list[str]:
    result = [str(value).strip() for value in values]
    if any(not value for value in result):
        raise FanfictionSourceError(f"{field} must not contain empty values")
    return list(dict.fromkeys(result))


def _stable_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{1,159}", value))


def _read_yaml_or_json(path: Path) -> Any:
    if not path.is_file():
        raise FanfictionSourceError(f"input file does not exist: {path}")
    if path.suffix.lower() == ".json":
        return _read_json(path, None)
    return _read_yaml(path, None)


def _read_yaml(path: Path, default: Any) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, yaml.YAMLError):
        return default


def _write_yaml(path: Path, payload: Any) -> None:
    atomic_write_text(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""
