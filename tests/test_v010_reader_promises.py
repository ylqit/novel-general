from __future__ import annotations

from pathlib import Path
import json
import pytest

from longform_engine.config import load_project_config
from longform_engine.orchestration import open_book
from longform_engine.reader_promises_v2 import (
    apply_promise_evidence,
    build_promise_evidence_application,
    materialize_explicit_reader_promises,
    validate_promise_actions_v2,
)
from longform_engine.storage import init_project


def promise_candidate() -> dict:
    return {
        "schema": "reader_promise_v2",
        "promise_id": "promise:archive-editor",
        "reader_expectation": "The archive editor's access method will be proven.",
        "owner_ref": "arc:archive-investigation",
        "payoff_window": {"earliest": 8, "target": 12, "latest": 16},
        "staged_payoffs": [
            {
                "stage_id": "payoff:access-method",
                "description": "Prove how internal access works.",
                "window": [8, 12],
            },
            {
                "stage_id": "payoff:editor-identity",
                "description": "Reveal the responsible editor.",
                "window": [13, 16],
            },
        ],
        "selected_by": None,
    }


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    final = project.root / "40_manuscript" / "final" / "ch001.md"
    final.write_text("# 第一章\n\n门锁内侧留下了只有内部人员能制造的切痕。\n", encoding="utf-8")
    write_json(
        project.root / "30_state" / "semantic_ledger" / "ch001.json",
        {"schema": "chapter_semantic_bundle_v1", "chapter_number": 1, "canonical": True},
    )
    write_json(
        project.root / "30_state" / "reader_promise_ledger.json",
        materialize_explicit_reader_promises([promise_candidate()], approved_by="human"),
    )
    return config, project.root, final.read_text(encoding="utf-8")


def test_empty_promise_actions_are_valid_for_an_ordinary_chapter():
    ledger = materialize_explicit_reader_promises([], approved_by="human")

    assert ledger["schema"] == "reader_promise_ledger_v2"
    assert ledger["items"] == []
    assert validate_promise_actions_v2([], ledger) == []


def test_ledger_contains_only_explicit_human_selected_promises():
    ledger = materialize_explicit_reader_promises([promise_candidate()], approved_by="human")

    assert [item["promise_id"] for item in ledger["items"]] == ["promise:archive-editor"]
    assert ledger["items"][0]["selected_by"] == "human"
    assert not any(item["promise_id"].startswith("story_engine:") for item in ledger["items"])


def test_rolling_plan_preserves_actual_promise_progress_and_omitted_promises():
    ledger = materialize_explicit_reader_promises([promise_candidate()], approved_by="human")
    item = ledger["items"][0]
    item.update(status="paid", completed_stage_ids=["payoff:access-method", "payoff:editor-identity"],
                actual_evidence=[{"chapter_number": 16, "confirmed_by": "human", "quote": "身份已查明"}])
    assert materialize_explicit_reader_promises([], approved_by="human", existing=ledger) == ledger
    assert materialize_explicit_reader_promises([promise_candidate()], approved_by="human", existing=ledger) == ledger
    changed = promise_candidate()
    changed["reader_expectation"] = "A different promise under the same ID."
    with pytest.raises(ValueError, match="observed_promise_requires_revision"):
        materialize_explicit_reader_promises([changed], approved_by="human", existing=ledger)
    assert item["status"] == "paid"


def test_promise_progress_requires_stable_stage_id_and_exact_final_evidence(tmp_path: Path):
    config, root, text = seed_project(tmp_path)
    action = {
        "promise_id": "promise:archive-editor",
        "action": "partial_payoff",
        "stage_id": "payoff:access-method",
        "intended_reader_gain": "Internal access becomes provable.",
        "evidence_requirement": "Show the inside-only cut.",
        "defer_reason": "",
    }
    start = text.index("门锁")
    end = text.index("。", start) + 1
    application = build_promise_evidence_application(
        root,
        chapter_number=1,
        actions=[action],
        evidence=[
            {
                "promise_id": "promise:archive-editor",
                "start": start,
                "end": end,
                "excerpt": text[start:end],
                "semantic_reason": "The cut proves the access method rather than merely mentioning it.",
            }
        ],
        confirmed_by="human",
    )
    application_path = write_json(
        root / "50_workbench" / "promise_evidence" / "ch001.json", application
    )

    result = apply_promise_evidence(config, application_path=application_path)

    ledger = json.loads((root / result.ledger_file).read_text(encoding="utf-8"))
    promise = ledger["items"][0]
    assert promise["status"] == "partially_paid"
    assert promise["completed_stage_ids"] == ["payoff:access-method"]
    actual = promise["actual_evidence"][0]
    assert actual["final_sha256"] == application["final"]["sha256"]
    assert actual["semantic_ledger_sha256"] == application["semantic_ledger"]["sha256"]
    assert actual["confirmed_by"] == "human"
