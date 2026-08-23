from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json

import pytest

from longform_engine.chapter_contract import (
    load_verified_chapter_contract,
    validate_chapter_contract,
)
from longform_engine.config import load_project_config
from longform_engine.orchestration import open_book
from longform_engine.planning import (
    STRUCTURAL_VALIDATION_SCHEMA,
    apply_planning_bundle,
    build_human_node_decisions,
    build_human_planning_approval,
    build_planning_semantic_application,
    validate_human_node_decisions,
    validate_planning_bundle,
    validate_planning_semantic_application,
)
from longform_engine.storage import init_project


def digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def seed_project(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    open_book(config)
    return config, project.root


def planning_bundle() -> dict:
    obligations = [
        {
            "schema": "semantic_obligation_v1",
            "obligation_id": "obligation:trust-choice",
            "domain": "relationship",
            "subject_refs": ["character:ari", "character:mira"],
            "prior_state_refs": ["relationship:ari-mira:v1"],
            "preconditions": [{"type": "semantic", "ref": "relationship:ari-mira:v1"}],
            "intended_change": "Ari gives Mira control of the only route copy.",
            "reader_value": "Trust becomes an observable tactical liability.",
            "evidence_requirement": "The final text must show transfer and lost control.",
            "protected_invariants": ["Ari does not trust without a bounded reason."],
            "dependency_refs": [],
        }
    ]
    forecasts = []
    for chapter in range(1, 21):
        tier = "firm" if chapter <= 3 else "directional" if chapter <= 10 else "horizon"
        forecasts.append(
            {
                "schema": "chapter_forecast_v1",
                "forecast_id": f"forecast:ch{chapter:03d}",
                "chapter_number": chapter,
                "tier": tier,
                "chapter_duty": f"Advance causal step {chapter}.",
                "likely_change": "The archive investigation becomes more constrained.",
                "reader_value": "A concrete answer creates a narrower question.",
                "obligation_refs": ["obligation:trust-choice"] if chapter <= 3 else [],
                "flexibility": "Carrier and location may change if dependencies remain intact.",
            }
        )
    tables = []
    for chapter in range(1, 4):
        first = f"node:ch{chapter:03d}:arrival"
        second = f"node:ch{chapter:03d}:choice"
        nodes = [
            plot_node(chapter, first, "scene:arrival", 1, [], "micro"),
            plot_node(chapter, second, "scene:arrival", 2, [first], "state_change"),
        ]
        tables.append(
            {
                "schema": "plot_node_table_v1",
                "table_id": f"node-table:ch{chapter:03d}",
                "chapter_number": chapter,
                "forecast_ref": f"forecast:ch{chapter:03d}",
                "nodes": nodes,
                "candidate_sha256": digest(f"chapter {chapter} design"),
            }
        )
    contracts = [
        {
            "schema": "chapter_contract_v5",
            "contract_id": f"contract:ch{chapter:03d}",
            "chapter_number": chapter,
            "forecast_ref": f"forecast:ch{chapter:03d}",
            "topology": "relationship",
            "chapter_duty": "Turn investigation pressure into a bounded trust decision.",
            "observable_change": "Mira gains control of the route copy.",
            "reader_value": "Trust changes who can act next.",
            "failure": {
                "applicability": "optional",
                "description": "The direct pursuit may fail.",
                "reason": "Relationship movement, not defeat, owns the chapter.",
            },
            "choice": {
                "applicability": "required",
                "description": "Ari gives Mira the route copy.",
                "reason": "The choice creates the observable relationship change.",
            },
            "cost": {
                "applicability": "required",
                "description": "Ari loses sole control of the evidence.",
                "reason": "The trust move must narrow his later choices.",
            },
            "aftermath": {
                "applicability": "optional",
                "description": "The next chapter may carry the emotional processing.",
                "reason": "This chapter ends on the tactical transfer.",
            },
            "plot_node_table_ref": {
                "table_id": f"node-table:ch{chapter:03d}",
                "candidate_sha256": digest(f"chapter {chapter} design"),
            },
            "semantic_obligation_refs": ["obligation:trust-choice"],
            "reader_promise_actions": [],
            "protected_invariants": ["Ari has a bounded reason for sharing control."],
            "prohibited_drift": ["Do not turn the transfer into a consequence-free gesture."],
        }
        for chapter in range(1, 4)
    ]
    return {
        "schema": "planning_bundle_v1",
        "book_spine": {
            "schema": "book_spine_v1",
            "spine_id": "spine:archive-memory",
            "premise": "An archivist discovers that public memory is being edited.",
            "central_conflict": "Proof requires using the institution that destroys proof.",
            "ending_boundary": "The editor is exposed without erasing the cost of remembered lies.",
            "protagonist_arc": "Ari moves from controlling evidence to sharing bounded agency.",
            "reader_value": "Each answer changes who can act and what trust costs.",
            "protected_invariants": ["Evidence changes choices; it is not decorative lore."],
        },
        "volume_skeletons": {
            "schema": "volume_skeletons_v1",
            "items": [
                {
                    "volume_id": "volume:001",
                    "order": 1,
                    "title": "The Altered Archive",
                    "chapter_range": [1, 30],
                    "volume_goal": "Prove the archive is being changed from inside.",
                    "entry_state": "Ari believes corruption is external.",
                    "exit_state": "Ari can prove internal access but loses sole control.",
                    "target_characters": 90000,
                    "status": "proposed",
                },
                {
                    "volume_id": "volume:002",
                    "order": 2,
                    "title": "The Borrowed Verdict",
                    "chapter_range": [31, 60],
                    "volume_goal": "Trace institutional beneficiaries.",
                    "entry_state": "Internal access is proven.",
                    "exit_state": "The public verdict becomes contested.",
                    "target_characters": 90000,
                    "status": "proposed",
                },
            ],
        },
        "active_volume_plan": {
            "schema": "volume_plan_v1",
            "volume_id": "volume:001",
            "chapter_range": [1, 30],
            "entry_state": "Ari believes corruption is external.",
            "exit_state": "Ari can prove internal access but loses sole control.",
            "event_graph": [{"id": "event-line:archive-access"}],
            "character_arcs": [{"id": "arc:ari-trust"}],
            "promise_threads": [],
            "foreshadow_threads": [],
            "flex_zones": [{"chapter_range": [11, 20], "reason": "investigation carrier"}],
            "lifecycle": "proposed",
            "approved_by": None,
        },
        "rolling_window": {
            "schema": "rolling_window_plan_v2",
            "window_id": "window:ch001-ch020",
            "start_chapter": 1,
            "end_chapter": 20,
            "tiers": {
                "firm": [1, 3],
                "directional": [4, 10],
                "horizon": [11, 20],
            },
            "basis_refs": ["spine:archive-memory", "volume:001"],
            "basis_sha256": digest("spine and volume basis"),
        },
        "chapter_forecasts": forecasts,
        "chapter_contracts": contracts,
        "semantic_obligations": obligations,
        "plot_node_tables": tables,
    }


def plot_node(
    chapter: int,
    node_id: str,
    scene_id: str,
    sequence: int,
    dependencies: list[str],
    kind: str,
) -> dict:
    return {
        "schema": "plot_node_v1",
        "node_id": node_id,
        "chapter_number": chapter,
        "scene_id": scene_id,
        "sequence": sequence,
        "actors": ["character:ari", "character:mira"],
        "location_ref": "location:archive-gate",
        "dramatic_function": "Turn investigation pressure into a relationship choice.",
        "preconditions": [
            {
                "type": "semantic",
                "ref": "relationship:ari-mira:v1",
                "requirement": "Ari still controls the route copy.",
            }
        ],
        "dependency_refs": dependencies,
        "obligation_refs": ["obligation:trust-choice"],
        "action_or_exchange": "Mira demands the route copy before continuing pursuit.",
        "expected_changes": [{"domain": "relationship", "change": "control is shared"}],
        "reader_effect": "The alliance becomes operational and costly.",
        "requirement": "required",
        "condition": None,
        "node_kind": kind,
        "protected_invariants": ["Ari retains a bounded reason for the choice."],
        "allowed_deviation": "Exact dialogue and sensory texture remain free.",
        "human_decision": None,
    }


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def evidence_review(subject_relative: str) -> dict:
    evidence_id = f"{subject_relative}@0:1"
    dimensions = (
        "causal_coherence",
        "character_motivation",
        "volume_progression",
        "reader_value",
        "protected_invariants",
    )
    return {
        "schema": "evidence_review_v2",
        "verdict": "pass",
        "coverage": {
            dimension: {
                "status": "checked",
                "evidence_ids": [evidence_id],
                "canonical_refs": ["book_spine"] if dimension == "protected_invariants" else [],
            }
            for dimension in dimensions
        },
        "findings": [],
    }


def test_structural_validation_is_explicitly_not_a_semantic_verdict():
    validation = validate_planning_bundle(planning_bundle())

    assert validation.ok, validation.errors
    assert validation.as_dict()["schema"] == STRUCTURAL_VALIDATION_SCHEMA
    assert validation.as_dict()["semantic_verdict"] == "not_evaluated"


def test_chapter_contract_v5_uses_topology_specific_applicability():
    contract = planning_bundle()["chapter_contracts"][0]
    contract["topology"] = "aftermath"
    contract["failure"] = {
        "applicability": "not_applicable",
        "description": "",
        "reason": "The prior chapter already contains the failed attempt.",
    }
    contract["choice"] = {
        "applicability": "optional",
        "description": "Ari may decide how much of the cost to name.",
        "reason": "Processing the existing consequence owns this chapter.",
    }
    contract["cost"] = {
        "applicability": "not_applicable",
        "description": "",
        "reason": "The visible cost was paid in the preceding action chapter.",
    }
    contract["aftermath"] = {
        "applicability": "required",
        "description": "Mira tests the practical boundary of her new agency.",
        "reason": "The chapter must turn consequence into a changed relationship condition.",
    }

    assert validate_chapter_contract(contract) == []


def test_rolling_window_and_dependency_closure_are_structural_blockers():
    bundle = planning_bundle()
    bundle["rolling_window"]["tiers"]["firm"] = [1, 4]
    bundle["plot_node_tables"][0]["nodes"][0]["dependency_refs"] = ["node:future"]

    validation = validate_planning_bundle(bundle)

    assert not validation.ok
    assert any("tiers.firm" in error for error in validation.errors)
    assert any("reference earlier nodes" in error for error in validation.errors)


def test_isolated_semantic_review_rejects_same_author_and_reviewer(tmp_path: Path):
    _config, root = seed_project(tmp_path)
    bundle_path = write_json(
        root / "50_workbench" / "planning" / "bundle.json", planning_bundle()
    )
    subject_relative = bundle_path.relative_to(root).as_posix()
    review_path = write_json(
        root / "50_workbench" / "planning" / "review.json",
        evidence_review(subject_relative),
    )
    application = build_planning_semantic_application(
        root,
        subject_path=bundle_path,
        profile="architecture",
        author_task_id="task:author",
        author_role_id="story_architect",
        reviewer_task_id="task:reviewer",
        reviewer_role_id="story_architect",
        reviewer_version="v1",
        review_result_path=review_path,
    )

    validation = validate_planning_semantic_application(root, application)

    assert not validation.ok
    assert "reviewer role must differ from the author role" in validation.errors


def test_every_plot_node_requires_an_explicit_human_decision(tmp_path: Path):
    _config, root = seed_project(tmp_path)
    bundle_path = write_json(
        root / "50_workbench" / "planning" / "bundle.json", planning_bundle()
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    node_ids = [
        node["node_id"]
        for table in bundle["plot_node_tables"]
        for node in table["nodes"]
    ]
    decisions = [
        {"node_id": node_id, "decision": "approve", "adjustment": "", "reason": "Keep."}
        for node_id in node_ids[:-1]
    ]
    payload = {
        "schema": "human_plot_node_decisions_v1",
        "subject_path": bundle_path.relative_to(root).as_posix(),
        "subject_sha256": sha256(bundle_path.read_bytes()).hexdigest(),
        "decided_by": "human",
        "decisions": decisions,
    }

    errors = validate_human_node_decisions(root, payload, bundle=bundle)

    assert any("every plot node requires an explicit decision" in error for error in errors)


def test_semantic_and_node_approved_bundle_applies_atomically(tmp_path: Path):
    config, root = seed_project(tmp_path)
    bundle_path = write_json(
        root / "50_workbench" / "planning" / "bundle.json", planning_bundle()
    )
    subject_relative = bundle_path.relative_to(root).as_posix()
    review_path = write_json(
        root / "50_workbench" / "planning" / "review.json",
        evidence_review(subject_relative),
    )
    application = build_planning_semantic_application(
        root,
        subject_path=bundle_path,
        profile="architecture",
        author_task_id="task:author",
        author_role_id="story_architect",
        reviewer_task_id="task:reviewer",
        reviewer_role_id="continuity_reviewer",
        reviewer_version="v1",
        review_result_path=review_path,
    )
    application_path = write_json(
        root / "50_workbench" / "planning" / "application.json", application
    )
    semantic_validation = validate_planning_semantic_application(root, application)
    assert semantic_validation.ok, semantic_validation.errors
    approval_path = write_json(
        root / "50_workbench" / "planning" / "approval.json",
        build_human_planning_approval(
            root,
            application_path=application_path,
            decision="approve",
            reason="The evidence-bound architecture is acceptable.",
            approved_by="human",
        ),
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    node_decisions = build_human_node_decisions(
        root,
        bundle_path=bundle_path,
        decisions=[
            {
                "node_id": node["node_id"],
                "decision": "approve",
                "adjustment": "",
                "reason": "Keep this causal step.",
            }
            for table in bundle["plot_node_tables"]
            for node in table["nodes"]
        ],
        decided_by="human",
    )
    decisions_path = write_json(
        root / "50_workbench" / "planning" / "node-decisions.json", node_decisions
    )

    result = apply_planning_bundle(
        config,
        bundle_path=bundle_path,
        application_path=application_path,
        approval_path=approval_path,
        node_decisions_path=decisions_path,
        approved_by="human",
    )

    assert result.planned_events == 3
    assert result.micro_nodes == 3
    assert (root / "20_outline" / "book_spine.json").is_file()
    contract, contract_hash = load_verified_chapter_contract(root, 1)
    assert contract["schema"] == "chapter_contract_v5"
    assert contract["reader_promise_actions"] == []
    assert len(contract_hash) == 64
    event_ledger = json.loads(
        (root / "30_state" / "narrative_events" / "ch001.json").read_text(
            encoding="utf-8"
        )
    )
    assert event_ledger["events"][0]["state"] == "planned_approved"
    assert event_ledger["events"][0]["realization_evidence"] is None
    transaction = json.loads((root / result.transaction_report).read_text(encoding="utf-8"))
    assert transaction["status"] == "applied"


def test_rejected_node_blocks_canonical_apply(tmp_path: Path):
    config, root = seed_project(tmp_path)
    bundle_path = write_json(
        root / "50_workbench" / "planning" / "bundle.json", planning_bundle()
    )
    subject_relative = bundle_path.relative_to(root).as_posix()
    review_path = write_json(
        root / "50_workbench" / "planning" / "review.json",
        evidence_review(subject_relative),
    )
    application_path = write_json(
        root / "50_workbench" / "planning" / "application.json",
        build_planning_semantic_application(
            root,
            subject_path=bundle_path,
            profile="architecture",
            author_task_id="task:author",
            author_role_id="story_architect",
            reviewer_task_id="task:reviewer",
            reviewer_role_id="continuity_reviewer",
            reviewer_version="v1",
            review_result_path=review_path,
        ),
    )
    approval_path = write_json(
        root / "50_workbench" / "planning" / "approval.json",
        build_human_planning_approval(
            root,
            application_path=application_path,
            decision="approve",
            reason="Architecture passes, pending row decisions.",
            approved_by="human",
        ),
    )
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    decisions = [
        {
            "node_id": node["node_id"],
            "decision": "reject" if node["node_id"].endswith(":choice") else "approve",
            "adjustment": "",
            "reason": "Reject choice." if node["node_id"].endswith(":choice") else "Keep.",
        }
        for table in bundle["plot_node_tables"]
        for node in table["nodes"]
    ]
    decisions_path = write_json(
        root / "50_workbench" / "planning" / "node-decisions.json",
        build_human_node_decisions(
            root,
            bundle_path=bundle_path,
            decisions=decisions,
            decided_by="human",
        ),
    )

    with pytest.raises(ValueError, match="rejected/deferred"):
        apply_planning_bundle(
            config,
            bundle_path=bundle_path,
            application_path=application_path,
            approval_path=approval_path,
            node_decisions_path=decisions_path,
            approved_by="human",
        )

    assert not (root / "20_outline" / "book_spine.json").exists()
