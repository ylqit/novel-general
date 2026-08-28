"""Explicit, non-in-place v0.11 audit and v0.12 import support."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Iterable

from longform_engine.semantic_protocols import build_semantic_document
from longform_engine.storage import atomic_write_text


AUDIT_SCHEMA = "v011_migration_audit_v1"
IMPORT_SCHEMA = "v011_to_v012_import_v1"
OLD_SOURCE_SCHEMAS = frozenset(
    {
        "source_library_index_v1",
        "source_library_item_v1",
        "source_extraction_candidate_v1",
        "fanfiction_work_binding_v1",
        "fanfiction_coverage_plan_v1",
        "fanfiction_source_canon_v1",
        "fanfiction_source_canon_v2",
        "fanfiction_source_canon_v3",
    }
)
DERIVED_PROJECT_PATHS = (
    "30_state/story_graph.json",
    "60_rag",
    "70_runtime/db",
)


class MigrationV012Error(ValueError):
    """Raised when the explicit migration boundary is unsafe or incompatible."""


def audit_v011(source: str | Path) -> dict[str, Any]:
    root = Path(source).expanduser().resolve()
    _require_project_root(root)
    files: list[dict[str, Any]] = []
    legacy_schemas: dict[str, list[str]] = {}
    final_hashes: dict[str, str] = {}
    symlinks: list[str] = []
    for path in _project_files(root):
        relative = path.relative_to(root).as_posix()
        if _is_link_or_reparse(path):
            symlinks.append(relative)
            continue
        digest = _file_hash(path)
        files.append({"path": relative, "size_bytes": path.stat().st_size, "sha256": digest})
        if relative.startswith("40_manuscript/final/"):
            final_hashes[relative] = digest
        schema = _read_schema(path)
        if schema in OLD_SOURCE_SCHEMAS:
            legacy_schemas.setdefault(str(schema), []).append(relative)
    return {
        "schema": AUDIT_SCHEMA,
        "source": str(root),
        "compatible_source": bool(legacy_schemas) or (root / "project.yaml").is_file(),
        "file_count": len(files),
        "total_bytes": sum(int(item["size_bytes"]) for item in files),
        "files_sha256": _records_hash(files),
        "final_chapter_hashes": final_hashes,
        "legacy_schemas": legacy_schemas,
        "unmappable": [
            {
                "kind": "legacy_evidence_locator",
                "reason": "v0.11 character spans are not guessed into page, region, cue, or time locators",
            }
        ]
        if legacy_schemas
        else [],
        "symlinks_or_reparse_points": symlinks,
        "requires_human_reapproval": [
            "legacy source facts",
            "project source Canon",
            "derived graph/RAG/SQLite projections",
        ],
        "audited_at": datetime.now(timezone.utc).isoformat(),
    }


def migrate_v011_to_v012(
    source: str | Path,
    destination: str | Path,
    *,
    approved_by: str,
) -> dict[str, Any]:
    if approved_by != "human":
        raise MigrationV012Error("v0.11 import requires --approved-by human")
    source_root = Path(source).expanduser().resolve()
    destination_root = Path(destination).expanduser().resolve()
    _require_project_root(source_root)
    if source_root == destination_root:
        raise MigrationV012Error("v0.11 import never modifies a project in place")
    if _is_within(destination_root, source_root) or _is_within(source_root, destination_root):
        raise MigrationV012Error("source and destination must not contain one another")
    if destination_root.exists():
        raise MigrationV012Error("migration destination must not already exist")
    audit = audit_v011(source_root)
    if audit["symlinks_or_reparse_points"]:
        raise MigrationV012Error(
            "migration source contains symlinks or reparse points: "
            + ", ".join(audit["symlinks_or_reparse_points"][:5])
        )
    shutil.copytree(source_root, destination_root, symlinks=False)
    quarantine = destination_root / "50_workbench" / "v011_import"
    quarantine.mkdir(parents=True, exist_ok=True)
    moved: list[str] = []
    for relative in DERIVED_PROJECT_PATHS:
        active = destination_root / relative
        if not active.exists():
            continue
        target = quarantine / "派生缓存" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(active), str(target))
        moved.append(relative)
    old_canon = destination_root / "10_bible" / "fanfiction" / "source_canon.json"
    legacy_candidate_file = ""
    if old_canon.is_file():
        target = quarantine / "待审旧Canon" / "source_canon.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old_canon), str(target))
        moved.append("10_bible/fanfiction/source_canon.json")
        legacy_candidate = _legacy_canon_candidate(
            project=destination_root.name,
            quarantined_file=target,
            source_schema=_read_schema(target),
        )
        candidate_target = quarantine / "待审语义候选" / "原著Canon候选.json"
        atomic_write_text(
            candidate_target,
            json.dumps(legacy_candidate, ensure_ascii=False, indent=2) + "\n",
        )
        legacy_candidate_file = candidate_target.relative_to(destination_root).as_posix()
    report = {
        "schema": IMPORT_SCHEMA,
        "source": str(source_root),
        "destination": str(destination_root),
        "source_audit_sha256": _records_hash(audit),
        "approved_by": "human",
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "preserved_final_chapter_hashes": audit["final_chapter_hashes"],
        "quarantined_paths": moved,
        "legacy_semantic_status": "awaiting_human",
        "legacy_semantic_candidate": legacy_candidate_file,
        "derived_indexes": "stale",
        "next_actions": [
            "重新导入并规范化原著资料",
            "由 Host Agent 生成 semantic_document_v1 候选",
            "人工重新批准项目 Canon 与设计",
            "重建图谱、RAG 与 SQLite",
        ],
    }
    atomic_write_text(
        quarantine / "迁移报告.json",
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    _verify_preserved_finals(destination_root, audit["final_chapter_hashes"])
    return report


def _legacy_canon_candidate(
    *, project: str, quarantined_file: Path, source_schema: str
) -> dict[str, Any]:
    """Create a review stub without guessing old fields into new semantic claims."""

    digest = _file_hash(quarantined_file)
    return build_semantic_document(
        document_id=f"sem_v011_import_{digest[:24]}",
        document_type="v0.11原著Canon待审导入",
        title="旧版原著Canon语义重建候选",
        scope={"kind": "project_migration", "project": project},
        continuity="旧版内容待人工重建",
        body=(
            "旧版结构化原著事实已隔离保存。本候选不把旧人物、事件、关系或字符区间"
            "自动解释成 v0.12 Canon；Host Agent 只能以它作为重建任务线索，重新绑定"
            "可回溯证据后再提交独立语义复核和人工批准。"
        ),
        uncertainties=[
            "旧字段尚未映射为开放语义主张。",
            "旧字符位置不能推断为页面、区域、字幕 cue 或媒体时间段。",
        ],
        extensions={
            "migration": "v011_to_v012",
            "legacy_schema": source_schema or "unknown",
            "legacy_payload_sha256": digest,
            "legacy_payload_file": quarantined_file.parent.name
            + "/"
            + quarantined_file.name,
            "promotion_status": "awaiting_semantic_reconstruction_and_human_reapproval",
        },
        created_by="migration_tool",
        input_hashes=[digest],
    )


def _require_project_root(root: Path) -> None:
    if not root.is_dir() or not (root / "project.yaml").is_file():
        raise MigrationV012Error("migration source must be a project directory containing project.yaml")


def _project_files(root: Path) -> Iterable[Path]:
    for directory, names, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        names[:] = sorted(
            name for name in names if name not in {".git", "__pycache__", ".pytest_cache"}
        )
        for filename in sorted(filenames):
            yield directory_path / filename


def _read_schema(path: Path) -> str:
    if path.suffix.lower() != ".json" or path.stat().st_size > 8 * 1024 * 1024:
        return ""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    return str(payload.get("schema") or "") if isinstance(payload, dict) else ""


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _records_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _verify_preserved_finals(root: Path, expected: dict[str, str]) -> None:
    mismatches = [
        relative
        for relative, digest in expected.items()
        if not (root / relative).is_file() or _file_hash(root / relative) != digest
    ]
    if mismatches:
        raise MigrationV012Error(
            "finalized chapter hash changed during migration: " + ", ".join(mismatches)
        )


__all__ = [
    "AUDIT_SCHEMA",
    "IMPORT_SCHEMA",
    "MigrationV012Error",
    "audit_v011",
    "migrate_v011_to_v012",
]
