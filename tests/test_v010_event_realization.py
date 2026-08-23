from __future__ import annotations

from pathlib import Path
import json

from longform_engine.config import load_project_config
from longform_engine.narrative_events import (
    apply_event_realization,
    build_event_realization_application,
    validate_event_realization_application,
)
from longform_engine.orchestration import open_book
from longform_engine.storage import init_project


def seed_event_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    text = "# 第一章\n\n阿梨把路线副本递给米拉，自己留在门外。\n"
    final = project.root / "40_manuscript" / "final" / "ch001.md"
    final.write_text(text, encoding="utf-8")
    write_json(
        project.root / "30_state" / "semantic_ledger" / "ch001.json",
        {"schema": "chapter_semantic_bundle_v1", "chapter_number": 1, "canonical": True},
    )
    write_json(
        project.root / "30_state" / "narrative_events" / "ch001.json",
        {
            "schema": "narrative_event_ledger_v1",
            "chapter_number": 1,
            "events": [
                {
                    "schema": "narrative_event_v1",
                    "event_id": "event:ch001:route-transfer",
                    "source_node_id": "node:ch001:route-transfer",
                    "chapter_number": 1,
                    "preconditions": [],
                    "dependency_refs": [],
                    "expected_changes": [{"domain": "relationship"}],
                    "reader_effect": "Mira gains agency.",
                    "state": "planned_approved",
                    "realization_evidence": None,
                }
            ],
            "source_plot_node_table_sha256": "0" * 64,
        },
    )
    return config, project.root, text


def write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def observation(text: str) -> dict:
    start = text.index("阿梨")
    end = text.index("。", start) + 1
    return {
        "event_id": "event:ch001:route-transfer",
        "state": "realized",
        "evidence": {"start": start, "end": end, "excerpt": text[start:end]},
        "semantic_reason": "The exact action transfers control of the only route copy.",
    }


def test_realization_requires_exact_final_span_and_human_confirmation(tmp_path: Path):
    _config, root, text = seed_event_project(tmp_path)
    application = build_event_realization_application(
        root,
        chapter_number=1,
        observations=[observation(text)],
        discovered_causal_nodes=[],
        confirmed_by="human",
    )
    application["observations"][0]["evidence"]["excerpt"] = "不匹配"

    validation = validate_event_realization_application(root, application)

    assert not validation.ok
    assert any("does not match final text" in error for error in validation.errors)


def test_unapproved_causal_node_redirects_instead_of_silent_realization(tmp_path: Path):
    _config, root, text = seed_event_project(tmp_path)
    application = build_event_realization_application(
        root,
        chapter_number=1,
        observations=[observation(text)],
        discovered_causal_nodes=[
            {"action": "Mira secretly destroys the copy", "expected_change": "evidence is lost"}
        ],
        confirmed_by="human",
    )

    validation = validate_event_realization_application(root, application)

    assert not validation.ok
    assert validation.redirect_required
    assert any("redirect to plot-node approval" in warning for warning in validation.warnings)


def test_realization_apply_binds_final_and_semantic_ledger_hashes(tmp_path: Path):
    config, root, text = seed_event_project(tmp_path)
    application = build_event_realization_application(
        root,
        chapter_number=1,
        observations=[observation(text)],
        discovered_causal_nodes=[],
        confirmed_by="human",
    )
    application_path = write_json(
        root / "50_workbench" / "event_realizations" / "ch001.json", application
    )

    result = apply_event_realization(config, application_path=application_path)

    ledger = json.loads((root / result.event_ledger).read_text(encoding="utf-8"))
    event = ledger["events"][0]
    assert event["state"] == "realized"
    assert event["realization_evidence"]["final_sha256"] == application["final"]["sha256"]
    assert (
        event["realization_evidence"]["semantic_ledger_sha256"]
        == application["semantic_ledger"]["sha256"]
    )
    assert event["realization_evidence"]["confirmed_by"] == "human"


def test_lexical_mentions_cannot_be_submitted_as_unknown_event_ids(tmp_path: Path):
    _config, root, text = seed_event_project(tmp_path)
    unknown = observation(text)
    unknown["event_id"] = "event:lexical:battle"
    application = build_event_realization_application(
        root,
        chapter_number=1,
        observations=[unknown],
        discovered_causal_nodes=[],
        confirmed_by="human",
    )

    validation = validate_event_realization_application(root, application)

    assert not validation.ok
    assert any("not an approved planned event" in error for error in validation.errors)
