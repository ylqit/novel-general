import json
import os
from hashlib import sha256
from pathlib import Path

import pytest
from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import (
    CANONICAL_DELTA_SCHEMA,
    DESIGN_REQUIRED_HEADINGS,
)
from longform_engine.agent_tasks import list_manifests, load_manifest, validate_manifest_strict
from longform_engine.arc_simulation import current_basis_hashes, load_active_arc_simulation, write_arc_causal_simulation
from longform_engine.config import load_project_config
from longform_engine.creative import prose_naturalness_check, prose_naturalness_task
from longform_engine.gates.pipeline import check_fanfiction_source_reproduction
from longform_engine.fanfiction_sources import (
    library_item_texts,
    project_source_contract,
    source_library_root,
)
from longform_engine.intelligence import (
    apply_compiled_design,
    apply_intelligence_candidate,
    approve_design_document,
    assess_chapter_direction,
    assess_project_readiness,
    create_design_compile_task,
    create_intelligence_task,
    fanfiction_status,
    record_chapter_direction_selection,
    validate_design_compile_delta,
    validate_intelligence_candidate,
)
from longform_engine.intelligence.pipeline import validate_crossover_rules, validate_fanfiction_canon
from longform_engine.orchestration import open_book
from longform_engine.orchestration.pipeline import load_fanfiction_writing_contract
from longform_engine.publication import export_publication_bundle, publication_risk_report
from longform_engine.storage import init_project
from tests.test_fanfiction_source_library import approved_library_item, complete_project_pack
from tests.project_fixtures import build_outline_candidate, write_json


def seed_fanfiction_project(tmp_path: Path):
    source = {
        "source_id": "classic",
        "title": "Public Domain Adventure",
        "creator": "Example Author",
        "canon_cutoff": "volume-1-end",
        "allowed_elements": ["characters", "relationships", "world", "abilities", "timeline"],
        "rights_status": "unverified",
        "commercial_intent": True,
        "platform_policy_url": "",
    }
    template = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "creation": {"mode": "fanfiction"},
            "fanfiction": {"continuity_mode": "canon_divergent", "sources": [source]},
            "semantic": {"profile": "local-hash", "allow_fallback": True},
            "length": {
                "target_total_characters": 100_000,
                "volume": {"target_characters": 50_000},
                "planning": {"detailed_horizon": 4, "refill_threshold": 2},
            },
        },
    )
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    class Environment:
        @staticmethod
        def setenv(key: str, value: str) -> None:
            os.environ[key] = value

    _work, item, _source_text = approved_library_item(
        tmp_path,
        Environment(),
        name="Public Domain Adventure",
        creator="Example Author",
    )
    open_book(config)
    (project.root / "30_state" / "reader_promise_ledger.json").write_text(
        json.dumps(
            {"schema": "reader_promise_ledger_v1", "items": [], "updated_at": "test"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    complete_project_pack(config, tmp_path, item)
    registered = project_source_contract(config, "classic")["binding"]["items"][0]
    library_item_dir = next(
        path.parent
        for path in source_library_root().rglob("来源说明.yaml")
        if registered["item_id"] in path.read_text(encoding="utf-8")
    )
    source_path = next((library_item_dir / "内容").iterdir())
    return config, project.root, source_path


def valid_canon(config, source_path: Path) -> dict:
    contract = project_source_contract(config, "classic")
    binding = contract["binding"]["items"][0]
    evidence_by_key = contract["evidence"]
    evidence_ids = {
        key: f"classic:e{index:03d}"
        for index, key in enumerate(evidence_by_key, start=1)
    }
    evidence = [
        {
            "evidence_id": evidence_ids[key],
            "item_id": record["item_id"],
            "content_sha256": record["content_sha256"],
            "content_file": record["content_file"],
            "evidence_span": {"start": record["start"], "end": record["end"]},
            "excerpt": record["excerpt"],
        }
        for key, record in evidence_by_key.items()
    ]
    first_ref, second_ref = list(evidence_ids.values())
    return {
        "schema": "fanfiction_source_canon_v2",
        "continuity_mode": "canon_divergent",
        "sources": [
            {
                "source_id": "classic",
                "work_id": contract["setting"]["作品ID"],
                "title": "Public Domain Adventure",
                "creator": "Example Author",
                "canon_cutoff": "volume-1-end",
                "binding_sha256": contract["binding_sha256"],
                "coverage_plan_sha256": contract["coverage_sha256"],
                "item_bindings": [
                    {
                        "item_id": binding["item_id"],
                        "content_sha256": binding["content_sha256"],
                        "extraction_sha256": binding["extraction_sha256"],
                    },
                ],
                "facts": [
                    {
                        "type": "人物",
                        "id": "classic:gatekeeper",
                        "name": "守门人",
                        "summary": "A keeper who communicates rules through warnings.",
                        "attributes": {
                            "motivation": "Prevent an unprepared crossing.",
                            "voice_traits": ["indirect", "ritualized"],
                        },
                        "evidence_refs": [first_ref],
                    },
                    {
                        "type": "人物",
                        "id": "classic:lin_zhou",
                        "name": "林舟",
                        "summary": "A guarded key bearer who tests claims before committing.",
                        "attributes": {
                            "motivation": "Learn who controls the bronze gate.",
                            "voice_traits": ["brief", "evidence-led"],
                        },
                        "evidence_refs": [first_ref],
                    },
                    {
                        "type": "关系",
                        "id": "classic:rel_gate",
                        "name": "林舟与守门人的关系",
                        "summary": "The keeper controls access while the bearer withholds trust.",
                        "attributes": {
                            "source_character_id": "classic:lin_zhou",
                            "target_character_id": "classic:gatekeeper",
                            "stage": "mutual testing",
                        },
                        "evidence_refs": [first_ref],
                    },
                    {
                        "type": "世界规则",
                        "id": "classic:rule_fire",
                        "name": "门后之火",
                        "summary": "The gate fire does not obey ordinary water.",
                        "attributes": {},
                        "evidence_refs": [second_ref],
                    },
                    {
                        "type": "能力",
                        "id": "classic:star_key",
                        "name": "星纹钥匙",
                        "summary": "A key associated with the bronze gate.",
                        "attributes": {"limits": ["Its opening conditions remain unresolved."]},
                        "evidence_refs": [first_ref],
                    },
                    {
                        "type": "时间线",
                        "id": "classic:time_bell",
                        "name": "警告之前",
                        "summary": "The old bell sounds three times before the warning.",
                        "attributes": {"order": 1},
                        "evidence_refs": [first_ref],
                    },
                    {
                        "type": "术语",
                        "id": "classic:term_bronze_gate",
                        "name": "青铜门",
                        "summary": "A guarded threshold tied to unusual fire.",
                        "attributes": {},
                        "evidence_refs": [first_ref],
                    },
                    {
                        "type": "事件",
                        "id": "classic:event_warning",
                        "name": "守门警告",
                        "summary": "The keeper warns the key bearer about the gate fire.",
                        "attributes": {"order": 1},
                        "evidence_refs": [second_ref],
                    },
                    {
                        "type": "未解决问题",
                        "id": "classic:q_controller",
                        "name": "开门权限",
                        "summary": "Who determines when the gate may open remains unresolved.",
                        "attributes": {},
                        "evidence_refs": [first_ref],
                    },
                ],
                "evidence": evidence,
            }
        ],
    }


def book_design() -> dict:
    expression = valid_character_expression()
    return {
        "schema": "book_design_candidate_v2",
        "creative_brief": {
            "target_audience": "Chinese fanfiction serial readers.",
            "writing_style": "Concrete scene-led prose with distinct voices.",
            "automation_level": "agent_skill with explicit human apply.",
            "target_scale": "4 chapters.",
            "story_profile": load_project_config(template="qidian-longform").data["story_profile"],
            "story_engine_contract": {
                "schema": "story_engine_contract_v1",
                "reader_fantasy": "See familiar characters retain agency while a changed choice opens a new route.",
                "repeatable_action_loop": "Test a canon rule, face a character refusal, choose a cost, and follow the divergence.",
                "progression_loop": "Earn access and understanding without making canon abilities irrelevant.",
                "relationship_loop": "Divergence changes trust through choices owned by each canon character.",
                "mystery_or_question_loop": "Each gate answer changes who controls the next choice.",
                "expected_payoffs": {
                    "opening_three": "A recognizable canon choice, one visible divergence cost, and a new gate problem.",
                    "early_serial": "The changed route pays off without displacing canon character agency.",
                    "volume_end": "The new gate resolves while the protected canon question remains owned by its characters.",
                },
                "carrier_palette": ["exploration", "rescue", "negotiation", "relationship conflict"],
                "theme_carrier_limits": "Canon explanation cannot replace character action or make ritual discussion the only carrier.",
            },
            "design_decisions": {
                "core_hook": "The key opens a different gate after one changed choice.",
                "world_rule": "Every divergence must create a visible consequence.",
                "protagonist_desire": "Learn who controls the gate without losing agency.",
                "long_conflict": "Canon duty conflicts with the new mainline.",
                "volume_escalation": "The changed gate alters relationships before power.",
                "ending_boundary": "Resolve the new gate while preserving the canon question.",
            },
            "reader_contract": {"core_promise": "Canon voice plus a causally earned new plot."},
            "core_taboo": ["Do not turn canon characters into props."],
            "status": "candidate",
        },
        "world_markdown": "# World\n\nCanon rules remain active unless a declared divergence changes them.",
        "power_system_markdown": "# Power\n\nThe star key always has an opening cost.",
        "characters": [
            {
                "id": "classic:lin_zhou",
                "name": "林舟",
                "goal": "Learn who controls the gate.",
                "flaw": "Withholds trust after evidence is sufficient.",
                "arc_stages": ["guarded", "tested", "chooses"],
            },
            {
                "id": "classic:gatekeeper",
                "name": "守门人",
                "goal": "Preserve the threshold rule.",
                "flaw": "Explains danger only through ritual.",
                "arc_stages": ["keeper", "challenged", "revealed"],
            },
        ],
        "relationships": [
            {
                "id": "classic:rel_gate",
                "source_id": "classic:lin_zhou",
                "target_id": "classic:gatekeeper",
                "type": "mutual testing",
                "stage": "guarded",
            }
        ],
        "narrative_expression_profile": expression["narrative_expression_profile"],
        "character_expression_contracts": expression["character_expression_contracts"],
    }


def valid_design() -> dict:
    return {
        "schema": "fanfiction_design_candidate_v1",
        "continuity_mode": "canon_divergent",
        "canon_cutoff": "volume-1-end",
        "divergence_point": "林舟 answers the gatekeeper instead of hiding the key.",
        "ooc_tolerance": "bounded",
        "character_voice_contracts": [
            {
                "character_id": "classic:lin_zhou",
                "baseline_voice": "Brief, skeptical, and evidence-led.",
                "invariants": ["tests claims", "protects agency"],
                "allowed_changes": ["speaks more directly after earned trust"],
                "forbidden_shortcuts": ["instant trust", "serves only a new protagonist"],
            },
            {
                "character_id": "classic:gatekeeper",
                "baseline_voice": "Indirect and ritualized.",
                "invariants": ["protects threshold rules"],
                "allowed_changes": ["reveals one motive under cost"],
                "forbidden_shortcuts": ["forgets the gate rules"],
            },
        ],
        "original_mainline": {
            "premise": "One answer redirects the key to an undocumented gate.",
            "central_conflict": "The changed path threatens both characters' existing duties.",
            "reader_promise": "A new causal plot that keeps canon voices and rules active.",
        },
        "original_characters": [],
        "world_rule_changes": ["Only the declared alternate gate changes destination logic."],
        "butterfly_effects": [
            {"cause": "林舟 answers", "effect": "the key records his voice", "chapter_window": [1, 2]}
        ],
        "ending_boundary": "Close the alternate gate conflict without claiming an official continuation.",
        "original_contribution": ["alternate gate mechanism", "new duty conflict"],
        "protected_reveals": ["identity of the original gate controller"],
        "cross_source_rules": [],
        "book_design": book_design(),
    }


def valid_character_expression() -> dict:
    return {
        "schema": "character_expression_profile_v1",
        "narrative_expression_profile": {
            "narrative_distance": "close",
            "expression_mode": "balanced",
            "description_density": "selective",
            "dialogue_mode": "balanced",
            "voice_separation": "clear",
            "ensemble_mode": "dual",
        },
        "character_expression_contracts": [
            {
                "character_id": "classic:lin_zhou",
                "perception_bias": "Notices changed rules before accepting stated motives.",
                "decision_bias": "Tests a boundary while preserving one route of retreat.",
                "speech_register": "Brief skeptical questions grounded in visible evidence.",
                "conversation_tactics": ["narrows the claim", "withholds one inference"],
                "emotional_leaks": ["turns the star key inside his sleeve when cornered"],
                "physical_presence": "Economical movement with attention fixed on thresholds.",
                "social_masks": ["unimpressed traveler"],
                "private_wants": "Wants agency without abandoning the people behind the gate.",
                "contradictions": "Distrusts ritual authority but protects its vulnerable keepers.",
                "voice_examples": [],
                "contrast_with": ["classic:gatekeeper"],
            },
            {
                "character_id": "classic:gatekeeper",
                "perception_bias": "Reads every choice as a change to threshold risk.",
                "decision_bias": "Reveals only the rule needed to prevent the next breach.",
                "speech_register": "Indirect ritual clauses that conceal personal stakes.",
                "conversation_tactics": ["answers with a condition", "tests declared intent"],
                "emotional_leaks": ["touches the door seam before admitting uncertainty"],
                "physical_presence": "Measured posture that keeps one hand near the gate.",
                "social_masks": ["impersonal keeper of rules"],
                "private_wants": "Wants the gate protected without becoming its last sacrifice.",
                "contradictions": "Claims rules are impersonal while bending them to protect Lin Zhou.",
                "voice_examples": [],
                "contrast_with": ["classic:lin_zhou"],
            },
        ],
    }


def test_fanfiction_design_compiles_realistic_canon_into_bounded_context(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    canon = valid_canon(config, source_path)
    source = canon["sources"][0]
    template = next(fact for fact in source["facts"] if fact["type"] == "人物")
    source["facts"] = [fact for fact in source["facts"] if fact["type"] != "人物"] + [
        {
            **template,
            "id": f"classic:character_{index:03d}",
            "name": f"Character {index}",
            "summary": "A source-backed character description with distinct motive and pressure. " * 8,
            "attributes": {
                **template["attributes"],
                "motivation": "Protect a bounded choice while preserving canon causality. " * 5,
            },
        }
        for index in range(40)
    ]
    canon_path = root / "10_bible" / "fanfiction" / "source_canon.json"
    canon_path.parent.mkdir(parents=True, exist_ok=True)
    canon_path.write_text(json.dumps(canon, ensure_ascii=False, indent=2), encoding="utf-8")
    decisions_path = root / "10_bible" / "creative_decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "schema": "book_ideation_decisions_v1",
                "decisions": {
                    "target_reader_and_reading_context": "Serial readers who expect causally earned divergence.",
                    "core_hook": "A changed choice creates a new duty rather than free power.",
                    "world_core_rule": "Canon constraints remain active.",
                    "protagonist_desire_and_flaw": "Protect agency while learning not to control allies.",
                    "long_conflict": "Competing groups disagree over who may choose the route home.",
                    "volume_escalation": "Escalate relationships and institutions before raw power.",
                    "ending_boundary": "Preserve the canon protagonist's protected final responsibility.",
                    "taboos_and_unwanted_tropes": "No system shortcut, canon demotion, or prose copying.",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    assert len(canon_path.read_text(encoding="utf-8")) > 20_000

    task = create_intelligence_task(config, task_type="fanfiction_design")
    manifest = load_manifest(root, task.manifest_file)
    validation = validate_manifest_strict(root, manifest)
    context_path = root / "50_workbench" / "intelligence_context" / "fanfiction_design.project.context.json"
    context = json.loads(context_path.read_text(encoding="utf-8"))

    assert validation.ok
    assert [item["path"] for item in manifest["io"]["inputs"]] == [
        "50_workbench/intelligence_context/fanfiction_design.project.context.json",
        "50_workbench/intelligence_tasks/fanfiction_design.project.md",
    ]
    assert context["selection_report"]["estimated_units"] > 0
    assert context["selection_report"]["estimated_units"] < context["selection_report"]["capacity_units"]
    assert context["schema"] == "fanfiction_design_context_v1"
    assert context["selection_report"]["omitted_counts"]["classic:characters"] == 28
    assert {item["path"] for item in context["canonical_provenance"]} == {
        "10_bible/fanfiction/source_canon.json",
        "10_bible/creative_decisions.json",
        "project.yaml",
    }


def valid_outline(config) -> dict:
    outline = build_outline_candidate(config)
    for chapter in outline["chapter_plan"]:
        chapter.update(
            {
                "canon_refs": ["classic:event_warning"],
                "divergence_effects": ["The changed answer redirects the star key."],
                "voice_refs": ["classic:lin_zhou", "classic:gatekeeper"],
                "original_contribution": "Advance the alternate-gate duty conflict.",
                "protected_reveals": ["identity of the original gate controller"],
                "protected_canon_outcomes": [
                    "Lin Zhou owns the decision to accept or refuse the threshold duty."
                ],
            }
        )
    return outline


def canonical_snapshot(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for relative_dir in (
        "10_bible",
        "20_outline",
        "30_state",
        "40_manuscript/final",
        "60_rag",
        "70_runtime/db",
    ):
        directory = root / relative_dir
        for path in directory.rglob("*"):
            if path.is_file():
                snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
    return snapshot


def write_design_candidate(path: Path, task_type: str, payload: dict) -> None:
    def scalar_lines(value) -> list[str]:
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [line for item in value for line in scalar_lines(item)]
        if isinstance(value, dict):
            return [line for item in value.values() for line in scalar_lines(item)]
        return []

    facts = scalar_lines({key: value for key, value in payload.items() if key != "schema"})
    sections: list[str] = []
    for index, heading in enumerate(DESIGN_REQUIRED_HEADINGS[task_type]):
        body = ["本节内容已经由用户审阅。"]
        if index == 0:
            body.extend(f"- {fact}" for fact in facts)
        if task_type == "chapter_direction" and heading == "方向选项":
            direction_id = str(payload["selected_direction"]["id"])
            body = [
                f"### option:{direction_id} — {payload['selected_direction']['title']}",
                "沿当前证据链推进并承担明确代价。",
                "",
                "### option:alternate_route — 改由关系压力切入",
                "保留章节保护结果，但改变场景进入和冲突承担者。",
            ]
        sections.extend((f"## {heading}", "", *body, ""))
    path.write_text(f"# {task_type} 设计文档\n\n" + "\n".join(sections), encoding="utf-8")


def compile_design_output(config, root: Path, task_type: str, candidate: Path, payload: dict):
    assert validate_intelligence_output(config, root, task_type, candidate).ok
    if task_type == "chapter_direction":
        record_chapter_direction_selection(
            config,
            document_path=candidate,
            selected_option_id=str(payload["selected_direction"]["id"]),
            user_adjustments=dict(payload["selection"]["user_adjustments"]),
            repetition_reason=str(payload["selection"]["repetition_reason"]),
            selected_by="human",
        )
    approve_design_document(
        config,
        task_type=task_type,
        document_path=candidate,
        approved_by="human",
    )
    compile_task = create_design_compile_task(
        config,
        task_type=task_type,
        document_path=candidate,
    )
    delta = root / compile_task.candidate_file
    source = candidate.relative_to(root).as_posix()
    text = candidate.read_text(encoding="utf-8")
    changes = {key: value for key, value in payload.items() if key != "schema"}
    for cli_field in {
        "book_ideation": ("round", "dimension"),
        "chapter_direction": (
            "chapter_number", "chapter_card_sha256", "trigger_reasons", "selection",
        ),
        "outline_revision": ("from_chapter", "to_chapter"),
    }.get(task_type, ()):
        changes.pop(cli_field, None)
    delta.write_text(
        json.dumps(
            {
                "schema": CANONICAL_DELTA_SCHEMA,
                "delta_type": "design_document",
                "coverage": {key: "changed" for key in changes},
                "changes": changes,
                "evidence": {
                    f"/changes/{key.replace('~', '~0').replace('/', '~1')}": [
                        f"{source}@0:{len(text)}"
                    ]
                    for key in changes
                },
                "uncertainties": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, compile_task.task_id),
        result_file=delta,
    )
    assert control.ok, control.normalization.errors
    validation = validate_design_compile_delta(
        config,
        task_type=task_type,
        document_path=candidate,
        delta_path=delta,
    )
    return delta, validation


def apply_design_output(config, root: Path, task_type: str, candidate: Path, payload: dict) -> None:
    delta, validation = compile_design_output(config, root, task_type, candidate, payload)
    assert validation.ok, validation.errors
    applied = apply_compiled_design(
        config,
        task_type=task_type,
        document_path=candidate,
        delta_path=delta,
        approved_by="human",
    )
    assert applied.status == "applied"


def write_canon_delta(path: Path, payload: dict, source_path: Path) -> None:
    root = next(parent for parent in path.parents if (parent / "project.yaml").is_file())
    extraction = next((root / "50_workbench" / "同人原著资料").rglob("提取结果.json"))
    source_rel = extraction.relative_to(root).as_posix()
    evidence_ref = f"{source_rel}@0:{min(48, len(extraction.read_text(encoding='utf-8')))}"
    source = payload["sources"][0]
    item_id = source["item_bindings"][0]["item_id"]
    evidence_key_by_id = {
        record["evidence_id"]: f"{item_id}:e{index}"
        for index, record in enumerate(source["evidence"], start=1)
    }
    compact_source = {"source_id": source["source_id"], "facts": []}
    evidence: dict[str, list[str]] = {}
    for index, record in enumerate(source["facts"]):
        compact_source["facts"].append(
            {
                key: value
                for key, value in record.items()
                if key != "evidence_refs"
            }
            | {
                "evidence_keys": [
                    evidence_key_by_id[ref]
                    for ref in record["evidence_refs"]
                ]
            }
        )
        evidence[f"/changes/sources/0/facts/{index}"] = [evidence_ref]
    path.write_text(
        json.dumps(
            {
                "schema": CANONICAL_DELTA_SCHEMA,
                "delta_type": "fanfiction_canon",
                "coverage": {"source_canon": "changed"},
                "changes": {"sources": [compact_source]},
                "evidence": evidence,
                "uncertainties": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def validate_intelligence_output(config, root: Path, task_type: str, candidate: Path):
    task = next(
        item
        for item in reversed(list_manifests(root))
        if item.get("task_type") == task_type
        and candidate.relative_to(root).as_posix() == (item.get("io") or {}).get("output", {}).get("path")
    )
    control = validate_production_agent_result(
        root,
        load_manifest(root, task["task_id"]),
        result_file=candidate,
    )
    assert control.ok, control.normalization.errors
    return validate_intelligence_candidate(config, task_type=task_type, file_path=candidate)


def apply_fanfiction_canon(config, root: Path, source_path: Path) -> None:
    canon_task = create_intelligence_task(config, task_type="fanfiction_canon")
    canon_candidate = root / canon_task.candidate_file
    write_canon_delta(canon_candidate, valid_canon(config, source_path), source_path)
    assert validate_intelligence_output(config, root, "fanfiction_canon", canon_candidate).ok
    apply_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=canon_candidate,
        approved_by="human",
    )


def apply_fanfiction_foundation(config, root: Path, source_path: Path) -> None:
    canon_task = create_intelligence_task(config, task_type="fanfiction_canon")
    canon_candidate = root / canon_task.candidate_file
    write_canon_delta(canon_candidate, valid_canon(config, source_path), source_path)
    assert validate_intelligence_output(config, root, "fanfiction_canon", canon_candidate).ok
    apply_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=canon_candidate,
        approved_by="human",
    )

    dimensions = (
        "target_reader_and_reading_context",
        "core_hook",
        "world_core_rule",
        "protagonist_desire_and_flaw",
        "long_conflict",
        "volume_escalation",
        "ending_boundary",
        "taboos_and_unwanted_tropes",
    )
    for round_number, dimension in enumerate(dimensions, start=1):
        ideation_task = create_intelligence_task(config, task_type="book_ideation")
        ideation_candidate = root / ideation_task.candidate_file
        ideation_payload = {
            "schema": "book_ideation_candidate_v1",
            "round": round_number,
            "question": f"Choose {dimension}.",
            "options": [
                {
                    "id": "canon_focused",
                    "proposal": f"Canon-aware decision for {dimension}.",
                    "tradeoffs": ["Higher fidelity.", "Narrower divergence."],
                },
                {
                    "id": "original_focused",
                    "proposal": f"Original-mainline decision for {dimension}.",
                    "tradeoffs": ["More novelty.", "Higher continuity burden."],
                },
            ],
            "selection": {
                "mode": "selected_option",
                "option_id": "canon_focused",
                "answer": "",
            },
        }
        write_design_candidate(
            ideation_candidate,
            "book_ideation",
            ideation_payload,
        )
        apply_design_output(
            config, root, "book_ideation", ideation_candidate, ideation_payload
        )

    design_task = create_intelligence_task(config, task_type="fanfiction_design")
    design_candidate = root / design_task.candidate_file
    design_payload = valid_design()
    write_design_candidate(design_candidate, "fanfiction_design", design_payload)
    apply_design_output(config, root, "fanfiction_design", design_candidate, design_payload)

    outline_task = create_intelligence_task(config, task_type="outline_design")
    outline_candidate = root / outline_task.candidate_file
    outline_payload = valid_outline(config)
    write_design_candidate(outline_candidate, "outline_design", outline_payload)
    apply_design_output(config, root, "outline_design", outline_candidate, outline_payload)

    window = outline_payload["planning_window"]
    simulation = {
        "schema": "arc_causal_simulation_v1",
        "from_chapter": window["start_chapter"],
        "to_chapter": window["end_chapter"],
        "basis_hashes": current_basis_hashes(root),
        "protagonist_goal": "Test the alternate gate without surrendering agency.",
        "opposition_agenda": "Bind the protagonist to the threshold before revealing its controller.",
        "character_drives": [
            {"character_id": "classic:lin_zhou", "private_goal": "Preserve a route of retreat.", "refusal_point": "Refuses an open-ended oath.", "offscreen_intent": "Tests one physical limit before bargaining."},
            {"character_id": "classic:gatekeeper", "private_goal": "Protect the threshold.", "refusal_point": "Refuses proof without duty.", "offscreen_intent": "Closes the safe route before the second test."},
        ],
        "knowledge_boundaries": ["Neither character knows the final controller's identity."],
        "offstage_actions": ["The controller changes one threshold condition after each test."],
        "resource_shifts": ["Every test consumes a safe route or bargaining option."],
        "relationship_shifts": ["A witnessed condition can create a bounded operational bargain."],
        "collision_points": [{"chapter_number": number, "participants": ["classic:lin_zhou", "classic:gatekeeper"], "collision": "Proof requires a duty the protagonist will not grant freely.", "required_change": "One route or condition becomes unavailable."} for number in range(window["start_chapter"], window["end_chapter"] + 1)],
        "causal_obligations": [{"chapter_number": number, "cause": "The prior test changes the threshold.", "pressure": "The safe route is closing.", "choice": "Lin Zhou narrows the offered duty.", "consequence": "The bargain binds both parties to one condition."} for number in range(window["start_chapter"], window["end_chapter"] + 1)],
        "approved_by": "human",
        "status": "approved",
    }
    write_arc_causal_simulation(root, simulation)

    direction_task = create_intelligence_task(
        config,
        task_type="chapter_direction",
        chapter_number=1,
    )
    direction_candidate = root / direction_task.candidate_file
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card_payload = json.loads(card_path.read_text(encoding="utf-8"))
    reasons = assess_chapter_direction(config, 1)["reasons"]
    direction = {
        "book_goal": "Resolve who controls the alternate gate.",
        "volume_goal": "Make the first divergence create a visible obligation.",
        "protagonist_goal": "Test the gate without surrendering agency.",
        "featured_character_ids": card_payload["featured_character_ids"],
        "scene_chain": [
            {
                "scene_id": "test_threshold",
                "location": "alternate gate",
                "participants": ["classic:lin_zhou", "classic:gatekeeper"],
                "carrier": "exploration",
                "desire_collision": "Lin Zhou wants proof while the keeper wants compliance.",
                "action": "Lin Zhou turns the key against the keeper's warning and tests the threshold.",
                "reaction": "The gate records his voice and seals the route behind him.",
                "choice": "Lin Zhou tests one boundary before answering.",
                "cost": "The key records his voice and closes the safe route.",
                "turn": "The keeper must reveal one rule to prevent a breach.",
                "exit_state": "Lin Zhou is inside the threshold without the safe route back.",
            },
            {
                "scene_id": "accept_condition",
                "location": "inside the threshold",
                "participants": ["classic:lin_zhou", "classic:gatekeeper"],
                "carrier": "negotiation",
                "desire_collision": "Lin Zhou wants an exit while the keeper needs a binding witness.",
                "action": "The keeper offers an open oath; Lin Zhou names a narrower duty.",
                "reaction": "The keeper rejects the first limit but accepts a witnessed condition.",
                "choice": "Lin Zhou accepts one named duty but refuses an open-ended oath.",
                "cost": "The safe route stays closed until the duty is discharged.",
                "turn": "Their guarded relationship becomes a temporary operational bargain.",
                "exit_state": "Both characters own one enforceable condition of the bargain.",
            },
        ],
        "cast_desires": {
            "classic:lin_zhou": "Preserve a route of retreat while testing the claim.",
            "classic:gatekeeper": "Protect the threshold without revealing its controller.",
        },
        "dialogue_ownership": "Lin Zhou narrows claims; the keeper answers with conditions.",
        "embodiment_plan": "Use the key turning in a sleeve and a hand resting on the gate seam.",
        "interiority_function": "Expose the urge to withhold trust before the costly answer.",
        "immediate_desire": "Test the alternate gate while preserving a route of retreat.",
        "opposition_force": "The gatekeeper refuses proof without a binding duty and the gate itself records choices.",
        "dramatic_question": "Can Lin Zhou test the gate without surrendering his future choices?",
        "conflict": "Testing the gate consumes the only safe retreat window.",
        "key_failure": "The first test records Lin Zhou's voice and closes the safe route.",
        "irreversible_choice": "Lin Zhou accepts one named duty and refuses the open oath.",
        "chapter_turn": card_payload["chapter_turn"],
        "reveal_boundary": "Reveal the voice rule without revealing the original controller.",
        "must_dramatize": ["the failed gate test", "the keeper's refusal", "the bounded oath"],
        "may_summarize": ["routine movement within the threshold"],
        "primary_story_engine": "rule_test_and_bargain",
        "scene_carriers": ["exploration", "negotiation"],
        "protected_story_outcomes": card_payload["protected_story_outcomes"],
        "prohibited_drift": ["Do not let the original protagonist solve the gate for Lin Zhou."],
        "state_change_kind": card_payload["state_change_kind"],
        "dramatic_method": "failed_test_then_bounded_bargain",
        "exposition_carrier": "rule_revealed_by_consequence",
        "local_payoff": card_payload["reader_gain"],
        "character_cost": "Lin Zhou loses the unrecorded route back.",
        "mainline_move": "The divergence becomes an active duty conflict.",
        "character_arc_move": "Lin Zhou chooses a bounded test instead of passive distrust.",
        "foreshadow_move": "The hidden controller remains protected while its method appears.",
        "relationship_move": card_payload["relationship_move"],
        "ending_mode": "changed_problem",
        "ending_intent": "The bounded duty changes the gate problem while leaving the controller unknown.",
        "emotional_aftereffect": "Lin Zhou accepts responsibility without yielding the rest of his agency.",
        "must_preserve_suspense": ["identity of the original gate controller"],
        "resolution_markers": [],
        "main_risks": ["Canon terminology could replace visible consequence."],
        "canon_refs": ["classic:event_warning"],
        "world_rule_refs": ["classic:rule_fire"],
        "foreshadow_refs": [],
        "forbidden_reveals": ["identity of the original gate controller"],
        "protected_canon_outcomes": ["Lin Zhou owns the decision to accept or refuse the threshold duty."],
        "changed_scene_means": "The alternate gate records voice instead of opening through the original ritual sequence.",
        "canon_character_agency": "Lin Zhou tests, refuses, and narrows the bargain; the gatekeeper independently sets the threshold cost.",
        "new_long_term_facts": [],
        "outline_revision_required": False,
    }
    selected_direction = {
        "id": "test_gate",
        "title": "Test the gate",
        "chapter_duty": card_payload["chapter_duty"],
        **direction,
    }
    selected_direction["reader_gain"] = selected_direction.pop("local_payoff")
    selected_direction["cost"] = selected_direction.pop("character_cost")
    current_simulation, simulation_path, simulation_hash = load_active_arc_simulation(root, chapter_number=1)
    selected_direction["reader_promise_actions"] = [{
        "promise_id": "story_engine:opening_three",
        "action": "setup",
        "stage_id": None,
        "intended_reader_gain": selected_direction["reader_gain"],
        "evidence_requirement": "Show the changed gate condition in final prose.",
        "defer_reason": "",
    }]
    selected_direction["arc_simulation_ref"] = {
        "path": simulation_path.relative_to(root).as_posix(),
        "sha256": simulation_hash,
        "from_chapter": current_simulation["from_chapter"],
        "to_chapter": current_simulation["to_chapter"],
    }
    direction_payload = {
        "schema": "chapter_direction_candidate_v5",
        "chapter_number": 1,
        "chapter_card_sha256": sha256(card_path.read_bytes()).hexdigest(),
        "trigger_reasons": reasons,
        "selected_direction": selected_direction,
        "selection": {"direction_id": "test_gate", "user_adjustments": {}, "repetition_reason": ""},
        "canonical_refs": selected_direction["canon_refs"],
        "introduced_elements": [],
    }
    write_design_candidate(
        direction_candidate,
        "chapter_direction",
        direction_payload,
    )
    apply_design_output(config, root, "chapter_direction", direction_candidate, direction_payload)

    applied_card = json.loads(card_path.read_text(encoding="utf-8"))
    direction_selection = applied_card["direction_selection"]
    write_json(
        root / "20_outline" / "chapter_intents" / "ch001.json",
        {
            "schema": "human_chapter_intent_v1",
            "chapter_number": 1,
            "chapter_contract_sha256": applied_card["chapter_contract_hash"],
            "direction_selection_sha256": direction_selection["selection_sha256"],
            "story_intent": "Make the alternate gate test become a costly human choice.",
            "key_character_choice": "Lin Zhou accepts one bounded duty and refuses the open oath.",
            "emotional_truth": "Responsibility is accepted without surrendering the rest of his agency.",
            "pov_voice_intent": "Lin Zhou narrows claims and reveals distrust through concrete conditions.",
            "protected_items": ["The original gate controller remains unknown."],
            "completed_by": "human",
            "status": "approved",
            "approved_by": "human",
            "approved_at": "fixture",
        },
    )

    readiness = assess_project_readiness(config)
    assert readiness.ready


def test_unverified_commercial_fanfiction_reaches_writing_and_export_without_rights_block(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    apply_fanfiction_canon(config, root, source_path)

    status = fanfiction_status(config)
    assert status["rights_advisory_only"] is True
    assert status["rights_warnings"][0]["blocking"] is False

    final = root / "40_manuscript" / "final" / "ch001.md"
    final.write_text("# 第一章\n\n林舟握住星纹钥匙，决定回答守门人的问题。\n", encoding="utf-8")
    report = publication_risk_report(config)
    exported = export_publication_bundle(config)
    risk = json.loads((root / report.report_file).read_text(encoding="utf-8"))
    assert report.blocking is False
    assert exported.blocking is False
    assert risk["schema"] == "publication_risk_report_v2"
    assert risk["blocking"] is False
    assert risk["engine_performed_legal_verification"] is False
    assert "Rights" not in (root / exported.bundle_file).read_text(encoding="utf-8")


def test_fanfiction_writing_contract_ignores_names_in_global_forbidden_rules(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    apply_fanfiction_canon(config, root, source_path)
    fanfiction_bible = root / "10_bible" / "fanfiction" / "fanfiction_bible.json"
    fanfiction_bible.parent.mkdir(parents=True, exist_ok=True)
    fanfiction_bible.write_text(
        json.dumps(valid_design(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    contract = load_fanfiction_writing_contract(
        config,
        root,
        card={
            "title": "林舟的选择",
            "featured_character_ids": ["classic:lin_zhou"],
            "forbidden": ["不得削弱守门人的主体性"],
        },
        character_packet={"featured_character_ids": ["classic:lin_zhou"]},
    )

    assert [item["character_id"] for item in contract["voice_contracts"]] == [
        "classic:lin_zhou"
    ]


def test_fanfiction_manifest_and_invalid_evidence_do_not_pollute_bible(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    task = create_intelligence_task(config, task_type="fanfiction_canon")
    manifest = load_manifest(root, task.task_id)
    assert validate_manifest_strict(root, manifest).ok
    before = (root / "10_bible" / "creative_brief.json").read_bytes()
    candidate = root / task.candidate_file
    write_canon_delta(candidate, valid_canon(config, source_path), source_path)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    first_pointer = next(iter(payload["evidence"]))
    payload["evidence"][first_pointer] = [
        "50_workbench/同人原著资料/Public Domain Adventure/资料项/第一卷合法原件/提取结果.json@0:9999"
    ]
    candidate.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    control = validate_production_agent_result(
        root,
        manifest,
        result_file=candidate,
    )
    assert not control.ok
    assert not (root / "10_bible" / "fanfiction" / "source_canon.json").exists()
    assert (root / "10_bible" / "creative_brief.json").read_bytes() == before


def test_fanfiction_canon_rejects_source_prose_reconstructed_across_fields(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    task = create_intelligence_task(config, task_type="fanfiction_canon")
    candidate = root / task.candidate_file
    payload = valid_canon(config, source_path)
    source_text = source_path.read_text(encoding="utf-8")
    midpoint = len(source_text) // 2
    world_rule = next(
        fact for fact in payload["sources"][0]["facts"] if fact["type"] == "世界规则"
    )
    unresolved = next(
        fact for fact in payload["sources"][0]["facts"] if fact["type"] == "未解决问题"
    )
    world_rule["summary"] = source_text[:midpoint]
    unresolved["summary"] = source_text[midpoint:]
    write_canon_delta(candidate, payload, source_path)
    before = canonical_snapshot(root)

    validation = validate_intelligence_output(config, root, "fanfiction_canon", candidate)

    assert not validation.ok
    assert any("reconstructs source prose" in error for error in validation.errors)
    assert not (root / "10_bible" / "fanfiction" / "source_canon.json").exists()
    assert canonical_snapshot(root) == before


def test_fanfiction_canon_rejects_evidence_from_unbound_global_item(tmp_path):
    config, _root, source_path = seed_fanfiction_project(tmp_path)

    class Environment:
        @staticmethod
        def setenv(key: str, value: str) -> None:
            os.environ[key] = value

    _work, other_item, _source_text = approved_library_item(
        tmp_path,
        Environment(),
        name="Unbound Reference Work",
        creator="Another Author",
    )
    other_content_file, other_text = next(iter(library_item_texts(other_item["item_id"]).items()))
    payload = valid_canon(config, source_path)
    payload["sources"][0]["evidence"][0] = {
        "evidence_id": "classic:e001",
        "item_id": other_item["item_id"],
        "content_sha256": other_item["content_sha256"],
        "content_file": other_content_file,
        "evidence_span": {"start": 0, "end": min(20, len(other_text))},
        "excerpt": other_text[:20],
    }
    errors: list[str] = []

    validate_fanfiction_canon(config, payload, errors)

    assert any("pinned project binding" in error for error in errors)


def test_fanfiction_canon_rejects_evidence_span_beyond_source_content(tmp_path):
    config, _root, source_path = seed_fanfiction_project(tmp_path)
    payload = valid_canon(config, source_path)
    payload["sources"][0]["evidence"][0]["evidence_span"]["end"] = 999_999
    errors: list[str] = []

    validate_fanfiction_canon(config, payload, errors)

    assert any("outside source content" in error for error in errors)


def test_invalid_fanfiction_design_does_not_pollute_canonical_state(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    canon_task = create_intelligence_task(config, task_type="fanfiction_canon")
    canon_candidate = root / canon_task.candidate_file
    write_canon_delta(canon_candidate, valid_canon(config, source_path), source_path)
    assert validate_intelligence_output(config, root, "fanfiction_canon", canon_candidate).ok
    apply_intelligence_candidate(
        config,
        task_type="fanfiction_canon",
        file_path=canon_candidate,
        approved_by="human",
    )
    design_task = create_intelligence_task(config, task_type="fanfiction_design")
    design_candidate = root / design_task.candidate_file
    payload = valid_design()
    payload["character_voice_contracts"] = []
    write_design_candidate(design_candidate, "fanfiction_design", payload)
    before = canonical_snapshot(root)

    _delta, validation = compile_design_output(
        config,
        root,
        "fanfiction_design",
        design_candidate,
        payload,
    )

    assert not validation.ok
    assert not (root / "10_bible" / "fanfiction" / "fanfiction_bible.json").exists()
    assert canonical_snapshot(root) == before


def test_fanfiction_similarity_excludes_names_but_detects_continuous_source_prose(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    apply_fanfiction_canon(config, root, source_path)
    terms_only = "林舟握住星纹钥匙，绕过青铜门，守门人仍旧没有回答。"
    failures, _ = check_fanfiction_source_reproduction(config, root, terms_only)
    assert failures == []

    copied = source_path.read_text(encoding="utf-8")
    failures, _ = check_fanfiction_source_reproduction(config, root, copied)
    assert any(item["code"] == "fanfiction_source_prose_reproduction" for item in failures)


def test_prose_naturalness_v4_blocks_fact_drift_without_rewrite_percentage(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# 第一章\n\n林舟在第12层青铜门前握住星纹钥匙。\n", encoding="utf-8")
    task = prose_naturalness_task(config, chapter_number=1)
    candidate = Path(task.candidate_file)
    candidate.write_text("# 第一章\n\n守门人在第13层转身离开，另一场战争已经开始。\n", encoding="utf-8")

    result = prose_naturalness_check(config, chapter_number=1, file_path=candidate)
    report = json.loads(Path(result.report_file).read_text(encoding="utf-8"))
    assert result.passed is False
    assert result.need_human is True
    assert report["schema"] == "prose_naturalness_check_v1"
    assert {item["code"] for item in result.issues} == {"prose_naturalness_number_drift"}
    assert "rewrite_ratio" not in json.dumps(report, ensure_ascii=False)


def test_publication_export_rejects_output_outside_exports(tmp_path):
    config, root, source_path = seed_fanfiction_project(tmp_path)
    final = root / "40_manuscript" / "final" / "ch001.md"
    final.write_text("# 第一章\n\n林舟握住星纹钥匙。\n", encoding="utf-8")

    with pytest.raises(ValueError, match="80_exports"):
        export_publication_bundle(config, output="../outside.md")


def test_crossover_rules_require_conflict_power_and_terminology_policies():
    configured = {
        "sources": [
            {"source_id": "work_a"},
            {"source_id": "work_b"},
        ]
    }
    errors: list[str] = []
    validate_crossover_rules(
        configured,
        [
            {
                "source_ids": ["work_a", "work_b"],
                "conflict_rule": "When rules conflict, the host world's physical limit wins.",
                "power_conversion": "",
                "terminology_collision_policy": "Keep source-scoped display names.",
            }
        ],
        errors,
    )
    assert any("power_conversion" in error for error in errors)

    errors = []
    validate_crossover_rules(
        configured,
        [
            {
                "source_ids": ["work_a", "work_b"],
                "conflict_rule": "When rules conflict, the host world's physical limit wins.",
                "power_conversion": "Compare demonstrated cost and range, never title rank alone.",
                "terminology_collision_policy": "Keep source-scoped display names.",
            }
        ],
        errors,
    )
    assert errors == []
