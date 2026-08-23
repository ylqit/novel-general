from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import json

import pytest

from longform_engine.config import load_project_config
from longform_engine.orchestration import open_book
from longform_engine.production import production_next
from longform_engine.revision import (
    abandon_revision_branch,
    active_revision_branch,
    create_versioned_revision_branch,
    promote_revision_branch,
    record_revision_chapter,
)
from longform_engine.storage import init_project


def seed_project(tmp_path: Path, chapters: int = 2):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    for chapter in range(1, chapters + 1):
        path = project.root / "40_manuscript" / "final" / f"ch{chapter:03d}.md"
        path.write_text(f"# 第{chapter}章\n\n旧版本正文 {chapter}。\n", encoding="utf-8")
    return config, project.root


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def make_receipt(root: Path, branch_id: str, chapter: int) -> Path:
    directory = root / "50_workbench" / "revision_branches" / branch_id
    candidate = directory / "workbench" / f"ch{chapter:03d}.candidate.md"
    candidate.write_text(
        f"# 第{chapter}章\n\n阿梨把唯一的路线副本递给米拉，门外的脚步声因此远去。\n",
        encoding="utf-8",
    )
    text = candidate.read_text(encoding="utf-8")
    start = text.index("阿梨")
    end = len(text.rstrip())
    evidence = {"start": start, "end": end, "excerpt": text[start:end]}
    semantic = write_json(
        directory / "workbench" / f"ch{chapter:03d}.semantic.json",
        {
            "schema": "chapter_semantic_bundle_v1",
            "chapter_number": chapter,
            "source": {
                "path": candidate.relative_to(root).as_posix(),
                "sha256": file_hash(candidate),
            },
            "chapter_digest": {
                "summary": "Ari shares the route copy.",
                "causal_change": "Mira gains tactical control.",
                "reader_payoff": "Trust changes the pursuit.",
                "cost": "The witness gains distance.",
            },
            "scenes": [
                {
                    "scene_id": f"scene:ch{chapter:03d}:gate",
                    "participants": [],
                    "location_id": "",
                    "goal": "Transfer control.",
                    "outcome": "Mira owns the route.",
                    **evidence,
                }
            ],
            "events": [
                {
                    "event_id": f"event:ch{chapter:03d}:route-transfer",
                    "title": "Route control changes",
                    "participants": [],
                    "locations": [],
                    "consequences": "The pursuit changes owner.",
                    "evidence": evidence,
                }
            ],
            "relationship_deltas": [],
            "character_deltas": [],
            "foreshadow_deltas": [],
            "world_deltas": [],
            "timeline_deltas": [],
            "entity_coverage": {
                "featured_character_ids": [],
                "unchanged_character_ids": [],
                "active_thread_ids": [],
                "unchanged_thread_ids": [],
            },
            "retrieval": {"tags": [], "entity_ids": [], "focus": []},
        },
    )
    evidence_rows = []
    for kind in (
        "direction_approval",
        "plot_node_approval",
        "planning_semantic_review",
        "human_lock",
        "final_gate",
        "semantic_realization",
        "chapter_close",
    ):
        path = write_json(
            directory / "workbench" / f"ch{chapter:03d}.{kind}.json",
            {"schema": f"{kind}_test_v1", "chapter_number": chapter, "ok": True},
        )
        evidence_rows.append(
            {"kind": kind, "path": path.relative_to(root).as_posix(), "sha256": file_hash(path)}
        )
    return write_json(
        directory / "workbench" / f"ch{chapter:03d}.receipt.json",
        {
            "schema": "revision_chapter_receipt_v1",
            "branch_id": branch_id,
            "chapter_number": chapter,
            "candidate_path": candidate.relative_to(root).as_posix(),
            "candidate_sha256": file_hash(candidate),
            "semantic_bundle_path": semantic.relative_to(root).as_posix(),
            "semantic_bundle_sha256": file_hash(semantic),
            "workflow_evidence": evidence_rows,
            "approved_by": "human",
        },
    )


def test_branch_creation_and_abandonment_do_not_mutate_mainline(tmp_path: Path):
    config, root = seed_project(tmp_path)
    final_hashes = {
        path.name: file_hash(path) for path in (root / "40_manuscript" / "final").glob("ch*.md")
    }

    branch = create_versioned_revision_branch(
        config,
        from_chapter=1,
        to_chapter=2,
        reason="Change the relationship premise from the beginning.",
        created_by="human",
    )

    assert branch.status == "open"
    assert active_revision_branch(root)["branch_id"] == branch.branch_id
    action = production_next(config)
    assert action["status"] == "revision_branch_active"
    assert action["blocked_by"] == "versioned_historical_revision"
    assert not (root / "70_runtime" / "db" / "novel.sqlite").exists()
    abandoned = abandon_revision_branch(
        config,
        branch_id=branch.branch_id,
        reason="The original causal chain is stronger.",
        abandoned_by="human",
    )
    assert abandoned.status == "abandoned"
    assert active_revision_branch(root) is None
    assert final_hashes == {
        path.name: file_hash(path) for path in (root / "40_manuscript" / "final").glob("ch*.md")
    }


def test_branch_chapters_must_be_recorded_sequentially_with_full_evidence(tmp_path: Path):
    config, root = seed_project(tmp_path)
    branch = create_versioned_revision_branch(
        config,
        from_chapter=1,
        to_chapter=2,
        reason="Rebuild the opening choice.",
        created_by="human",
    )
    chapter_two = make_receipt(root, branch.branch_id, 2)

    with pytest.raises(ValueError, match="expected 1"):
        record_revision_chapter(config, branch_id=branch.branch_id, receipt_path=chapter_two)

    chapter_one = make_receipt(root, branch.branch_id, 1)
    recorded = record_revision_chapter(
        config,
        branch_id=branch.branch_id,
        receipt_path=chapter_one,
    )
    assert recorded.status == "rewriting"
    assert recorded.next_chapter == 2


def test_mainline_drift_marks_branch_unusable_before_record(tmp_path: Path):
    config, root = seed_project(tmp_path)
    branch = create_versioned_revision_branch(
        config,
        from_chapter=1,
        to_chapter=2,
        reason="Rebuild the opening choice.",
        created_by="human",
    )
    receipt = make_receipt(root, branch.branch_id, 1)
    (root / "40_manuscript" / "final" / "ch002.md").write_text(
        "# 漂移\n\n主线被并发修改。\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="canonical head drifted"):
        record_revision_chapter(config, branch_id=branch.branch_id, receipt_path=receipt)


def test_promotion_replaces_range_and_emits_manual_platform_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config, root = seed_project(tmp_path, chapters=1)
    branch = create_versioned_revision_branch(
        config,
        from_chapter=1,
        to_chapter=1,
        reason="Replace the opening causal choice.",
        created_by="human",
    )
    receipt = make_receipt(root, branch.branch_id, 1)
    ready = record_revision_chapter(config, branch_id=branch.branch_id, receipt_path=receipt)
    assert ready.status == "ready_for_promotion"

    @dataclass(frozen=True)
    class RebuildResult:
        transaction_file: str = "70_runtime/transactions/semantic-rebuild-test.json"

    monkeypatch.setattr("longform_engine.revision.branches.semantic_rebuild", lambda *_args, **_kwargs: RebuildResult())

    promoted = promote_revision_branch(config, branch_id=branch.branch_id, approved_by="human")

    assert "阿梨把唯一的路线副本" in (
        root / "40_manuscript" / "final" / "ch001.md"
    ).read_text(encoding="utf-8")
    manifest = json.loads((root / promoted.publication_manifest).read_text(encoding="utf-8"))
    assert manifest["schema"] == "publication_revision_manifest_v1"
    assert manifest["external_platform_sync"] == "manual_upload_required"
    assert manifest["platform_acceptance"] == "unknown_until_platform_submission"
    transaction = json.loads((root / promoted.transaction_report).read_text(encoding="utf-8"))
    assert transaction["status"] == "applied"


def test_promotion_rebuild_failure_rolls_back_mainline_and_branch_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config, root = seed_project(tmp_path, chapters=1)
    original = (root / "40_manuscript" / "final" / "ch001.md").read_bytes()
    branch = create_versioned_revision_branch(
        config,
        from_chapter=1,
        to_chapter=1,
        reason="Verify atomic promotion rollback.",
        created_by="human",
    )
    receipt = make_receipt(root, branch.branch_id, 1)
    record_revision_chapter(config, branch_id=branch.branch_id, receipt_path=receipt)

    def fail_rebuild(*_args, **_kwargs):
        raise RuntimeError("semantic rebuild failed")

    monkeypatch.setattr("longform_engine.revision.branches.semantic_rebuild", fail_rebuild)

    with pytest.raises(RuntimeError, match="semantic rebuild failed"):
        promote_revision_branch(config, branch_id=branch.branch_id, approved_by="human")

    assert (root / "40_manuscript" / "final" / "ch001.md").read_bytes() == original
    assert active_revision_branch(root)["status"] == "ready_for_promotion"
