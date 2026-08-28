import json
from copy import deepcopy
from hashlib import sha256
import pytest

from longform_engine.creative_sandbox import (
    create_sandbox_artifact,
    promote_sandbox_candidate,
)
from longform_engine.migration_v012 import (
    MigrationV012Error,
    audit_v011,
    migrate_v011_to_v012,
)
from longform_engine.semantic_protocols import (
    EVIDENCE_REFERENCE_SCHEMA,
    build_semantic_document,
    build_workflow_record,
    validate_semantic_document,
    validate_workflow_record,
)
from tests.test_fanfiction_source_library import project_config


def evidence_reference() -> dict:
    excerpt = "人物抬起右手，向后退了半步。"
    return {
        "schema": EVIDENCE_REFERENCE_SCHEMA,
        "evidence_id": "item_demo:evidence_frame_12",
        "item_id": "item_demo",
        "asset_id": "asset_video_01",
        "segment_id": "segment_82400_89100",
        "locator": {
            "kind": "video_time_range",
            "start_ms": 82_400,
            "end_ms": 89_100,
            "frame_sha256": ["a" * 64, "b" * 64],
        },
        "excerpt": excerpt,
        "excerpt_sha256": sha256(excerpt.encode("utf-8")).hexdigest(),
    }


def semantic_document() -> dict:
    evidence = evidence_reference()
    return build_semantic_document(
        document_id="sem_open_chinese_type",
        document_type="镜头中的空间压迫与衣着变化",
        title="交流会前的场景观察",
        scope={"kind": "source_item", "item_id": "item_demo"},
        continuity="动画版当前时间段",
        body="正文保留观察、解释与场景使用建议，不要求拆成固定人物或服装表。",
        claims=[
            {
                "claim_id": "claim_observed_step",
                "statement": "画面直接显示人物抬手并后退半步。",
                "applicability": "82.4 至 89.1 秒的当前镜头",
                "evidence_refs": [evidence["evidence_id"]],
                "uncertainty": "该动作的心理动机不能由画面直接确定。",
                "extensions": {
                    "自定义类型": "动作与服饰联动",
                    "场景使用建议": ["只写可观察动作", "不要把表情直接解释成恐惧"],
                },
            }
        ],
        evidence_references=[evidence],
        extensions={"作品特有观察维度": {"制服袖口状态": "画面可见"}},
    )


def test_unknown_chinese_document_type_and_extensions_are_open():
    document = semantic_document()

    assert validate_semantic_document(document) == []
    assert document["document_type"] == "镜头中的空间压迫与衣着变化"
    assert document["claims"][0]["extensions"]["自定义类型"] == "动作与服饰联动"


def test_missing_or_unreachable_hard_fields_are_rejected():
    document = semantic_document()
    document["claims"][0]["evidence_refs"] = ["evidence_missing"]

    errors = validate_semantic_document(document)

    assert any("undeclared evidence id" in error for error in errors)
    assert any("content_sha256" in error for error in errors)


def test_agent_cannot_forge_approved_state_without_human_decision():
    document = semantic_document()
    document["artifact"]["state"] = "approved"

    errors = validate_semantic_document(document, require_approved=True)

    assert any("human_decision" in error for error in errors)


def test_workflow_state_machine_rejects_unknown_state():
    record = build_workflow_record(
        workflow_id="workflow_search_01",
        workflow_kind="source_discovery",
        scope={"kind": "project", "project": "demo"},
        state="awaiting_human",
    )
    invalid = deepcopy(record)
    invalid["state"] = "silently_promoted"

    assert validate_workflow_record(record) == []
    assert "state is not a supported workflow state" in validate_workflow_record(invalid)


def test_sandbox_promotion_creates_candidate_without_mutating_canon(tmp_path, monkeypatch):
    config, root = project_config(tmp_path, mode="original")
    sandbox_result = create_sandbox_artifact(
        config,
        document_type="分歧点试验",
        title="如果守门人先说出代价",
        body="比较公开代价与隐瞒代价两种路线，不进入正式大纲。",
    )
    candidate = build_semantic_document(
        document_id="sem_promoted_route_candidate",
        document_type="故事路线候选",
        title="公开代价路线",
        scope={"kind": "project_candidate", "project": root.name},
        continuity="原创候选",
        body="保留公开代价后产生的信任债务，等待独立语义复核。",
    )
    candidate_file = root / "50_workbench" / "candidate.json"
    candidate_file.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
    before_bible = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in (root / "10_bible").rglob("*")
        if path.is_file()
    }

    result = promote_sandbox_candidate(
        config,
        sandbox_path=sandbox_result["file"],
        candidate_path=candidate_file.relative_to(root),
        approved_by="human",
        reason="只保留公开代价这一语义方向，尚不批准为 Canon。",
    )

    after_bible = {
        path.relative_to(root).as_posix(): sha256(path.read_bytes()).hexdigest()
        for path in (root / "10_bible").rglob("*")
        if path.is_file()
    }
    assert result["canonical_mutated"] is False
    assert before_bible == after_bible
    promoted = json.loads((root / result["candidate_file"]).read_text(encoding="utf-8"))
    assert promoted["artifact"]["state"] == "candidate"
    assert promoted["extensions"]["promotion_status"].startswith("awaiting_")


def test_v011_migration_is_non_in_place_and_creates_review_candidate(tmp_path):
    source = tmp_path / "old-project"
    destination = tmp_path / "new-project"
    (source / "10_bible" / "fanfiction").mkdir(parents=True)
    (source / "40_manuscript" / "final").mkdir(parents=True)
    (source / "30_state").mkdir(parents=True)
    (source / "project.yaml").write_text("schema_version: 2\n", encoding="utf-8")
    old_canon = {
        "schema": "fanfiction_source_canon_v3",
        "sources": [{"source_id": "classic", "facts": [{"type": "人物"}]}],
    }
    old_canon_path = source / "10_bible" / "fanfiction" / "source_canon.json"
    old_canon_path.write_text(json.dumps(old_canon, ensure_ascii=False), encoding="utf-8")
    final_path = source / "40_manuscript" / "final" / "ch001.md"
    final_path.write_text("# 第一章\n\n旧版已经定稿的正文。\n", encoding="utf-8")
    (source / "30_state" / "story_graph.json").write_text("{}\n", encoding="utf-8")
    source_final_hash = sha256(final_path.read_bytes()).hexdigest()

    audit = audit_v011(source)
    result = migrate_v011_to_v012(source, destination, approved_by="human")

    assert audit["legacy_schemas"]["fanfiction_source_canon_v3"]
    assert old_canon_path.is_file()
    assert not (destination / "10_bible" / "fanfiction" / "source_canon.json").exists()
    assert sha256((destination / "40_manuscript" / "final" / "ch001.md").read_bytes()).hexdigest() == source_final_hash
    candidate = json.loads(
        (destination / result["legacy_semantic_candidate"]).read_text(encoding="utf-8")
    )
    assert candidate["schema"] == "semantic_document_v1"
    assert candidate["claims"] == []
    assert candidate["artifact"]["state"] == "candidate"


def test_v011_migration_refuses_in_place_operation(tmp_path):
    source = tmp_path / "old-project"
    source.mkdir()
    (source / "project.yaml").write_text("schema_version: 2\n", encoding="utf-8")

    with pytest.raises(MigrationV012Error, match="never modifies"):
        migrate_v011_to_v012(source, source, approved_by="human")
