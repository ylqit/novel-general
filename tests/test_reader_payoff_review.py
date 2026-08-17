import hashlib
import json
from pathlib import Path

import pytest

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import EVIDENCE_REVIEW_SCHEMA
from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.orchestration import WorkflowError, finalize_chapter, open_book, plan_chapter
from longform_engine.production import production_loop, production_next
from longform_engine.quality import (
    build_structure_observation,
    reader_payoff_review_status,
    reader_payoff_task,
    reader_payoff_validate,
)
from longform_engine.revision import rollback
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


def test_production_schedules_strict_bounded_reader_payoff_task(tmp_path):
    config, root, text = seed_payoff_project(tmp_path)

    action = production_next(config)
    assert action["status"] == "ready_for_reader_payoff_task"
    assert action["next_command"] == "longform-engine quality payoff-task project.yaml --chapter 1"

    result = reader_payoff_task(config, chapter_number=1)
    manifest = load_manifest(root, "reader_payoff_review:ch001:v4")
    strict = validate_manifest_strict(root, manifest)
    inputs = [item["path"] for item in manifest["io"]["inputs"]]

    assert strict.ok, strict.errors
    assert manifest["io"]["output"] == {
        "path": "50_workbench/quality_reviews/ch001.reader_payoff.json",
        "protocol": EVIDENCE_REVIEW_SCHEMA,
    }
    assert manifest["policy"]["context"]["budget_profile"] == "standard"
    assert manifest["policy"]["context"]["capacity_units"] == 48_000
    assert manifest["policy"]["context"]["overflow_policy"] == "split_context"
    assert inputs == [
        "50_workbench/quality_reviews/ch001.reader_payoff.task.md",
        "40_manuscript/draft/ch001.md",
        "50_workbench/quality_reviews/ch001.reader_payoff.context.json",
    ]
    assert "30_state/reward_ledger.jsonl" not in inputs
    assert "20_outline/foreshadowing_ledger.json" not in inputs
    assert "50_workbench/quality_reviews/ch001.reader_payoff.context.json" in inputs
    input_characters = sum(
        len((root / path).read_text(encoding="utf-8")) for path in inputs
    )
    assert input_characters <= 15_000
    context = read_json(Path(result.context_file))
    assert context["selection"]["full_ledgers_excluded"] is True
    assert context["selection"]["previous_reward_limit"] == 1
    assert context["selection"]["related_promise_limit"] == 8
    assert len(Path(result.context_file).read_text(encoding="utf-8")) <= 6_000
    assert context["schema"] == "reader_payoff_context_v2"
    assert context["chapter_contract"]["platform_promise"]
    assert context["quality_guidance"]["primary_market"] == "qidian_male"
    assert len(context["quality_guidance"]["compatibility_observations"]) <= 3
    assert all(
        item["severity"] == "P2" and item["blocking"] is False
        for item in context["quality_guidance"]["compatibility_observations"]
    )
    assert hashlib.sha256((root / "40_manuscript" / "draft" / "ch001.md").read_bytes()).hexdigest() in Path(
        result.task_file
    ).read_text(encoding="utf-8")
    assert "Compatibility-market observations are non-blocking P2 advice" in Path(result.task_file).read_text(
        encoding="utf-8"
    )
    waiting = production_next(config)
    assert waiting["status"] == "agent_task_awaiting_agent"
    assert waiting["task_type"] == "reader_payoff_review"


def test_payoff_validation_rejects_stale_hash_and_span_without_canonical_pollution(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)
    task = reader_payoff_task(config, chapter_number=1)
    payload = valid_review_payload(root, chapter_number=1)
    payload["findings"][0]["evidence_ids"] = ["ch001.md@0:99999"]
    output = Path(task.output_file)
    write_json(output, payload)
    reward_before = (root / "30_state" / "reward_ledger.jsonl").read_text(encoding="utf-8")
    structure_before = (root / "30_state" / "quality" / "structure_history.jsonl").read_text(encoding="utf-8")

    result = validate_payoff_output(config, root, task, output)

    assert result.ok is False
    assert result.passed is False
    assert any("outside current source bounds" in item or "out of bounds" in item for item in result.errors)
    assert (root / "30_state" / "reward_ledger.jsonl").read_text(encoding="utf-8") == reward_before
    assert (root / "30_state" / "quality" / "structure_history.jsonl").read_text(encoding="utf-8") == structure_before
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_finalize_requires_current_passing_payoff_review(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)

    with pytest.raises(WorkflowError, match="reader payoff review"):
        finalize_chapter(config, chapter_number=1, approved_by="human")

    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    write_json(output, valid_review_payload(root, chapter_number=1))
    validation = validate_payoff_output(config, root, task, output)
    assert validation.passed is True
    output.write_text(output.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(WorkflowError, match="reader payoff review"):
        finalize_chapter(config, chapter_number=1, approved_by="human")


def test_replacing_draft_invalidates_payoff_task_and_finalize_until_regenerated(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)
    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    write_json(output, valid_review_payload(root, chapter_number=1))
    assert validate_payoff_output(config, root, task, output).passed is True

    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(draft.read_text(encoding="utf-8") + "\n沈阙把新证据压在账册底下。", encoding="utf-8")

    action = production_next(config)
    assert action["status"] == "awaiting_gate"
    assert action["next_command"] == "longform-engine gate-check project.yaml --chapter 1"

    gate_path = root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json"
    gate = read_json(gate_path)
    gate["source_sha256"] = hashlib.sha256(draft.read_bytes()).hexdigest()
    write_json(gate_path, gate)
    action = production_next(config)
    assert action["status"] == "ready_for_reader_payoff_task"
    assert action["next_command"] == "longform-engine quality payoff-task project.yaml --chapter 1"
    with pytest.raises(WorkflowError, match="reader payoff review"):
        finalize_chapter(config, chapter_number=1, approved_by="human")

    reader_payoff_task(config, chapter_number=1)
    waiting = production_next(config)
    assert waiting["status"] == "agent_task_awaiting_agent"
    assert waiting["task_type"] == "reader_payoff_review"


def test_payoff_finalize_records_observed_reward_and_structure_atomically(tmp_path):
    config, root, text = seed_payoff_project(tmp_path)
    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    payload = valid_review_payload(root, chapter_number=1)
    write_json(output, payload)
    assert validate_payoff_output(config, root, task, output).passed is True

    result = finalize_chapter(config, chapter_number=1, approved_by="human")

    rewards = read_jsonl(root / "30_state" / "reward_ledger.jsonl")
    structures = read_jsonl(root / "30_state" / "quality" / "structure_history.jsonl")
    assert Path(result.final_file).exists()
    assert len(rewards) == 1
    assert rewards[0]["schema"] == "reader_reward_entry_v2"
    assert rewards[0]["chapter_number"] == 1
    assert rewards[0]["observed_gain"] == positive_diagnosis(payload, "PAYOFF_DELIVERED")
    assert rewards[0]["duty_fulfilled"] is True
    assert rewards[0]["observation_status"] == "semantic_reviewed"
    assert rewards[0]["finalized"] is True
    card = read_json(root / "20_outline" / "chapter_cards" / "ch001.json")
    assert rewards[0]["planned_gain"] == (card.get("reader_gain") or card.get("reader_payoff"))
    assert rewards[0]["observed_cost"] == positive_diagnosis(payload, "COST_VISIBLE")
    assert rewards[0]["evidence_source_hash"] == hashlib.sha256((text + "\n").encode("utf-8")).hexdigest()
    assert structures[0]["schema"] == "structure_observation_v1"
    assert structures[0]["chapter_number"] == 1
    assert structures[0]["opening_mode"] == "discovery"
    assert structures[0]["language_metrics"]["ngram_signature"]
    assert "text" not in json.dumps(rewards[0]["evidence_spans"], ensure_ascii=False)
    assert reader_payoff_review_status(config, chapter_number=1)["passed"] is True
    next_action = production_next(config)
    assert next_action["status"] == "ready_for_chapter_semantic_task"
    assert next_action["task_type"] == "chapter_semantic"


def test_p1_fake_payoff_cannot_be_overridden_by_pass_verdict(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)
    task = reader_payoff_task(config, chapter_number=1)
    payload = valid_review_payload(root, chapter_number=1)
    payload["verdict"] = "pass"
    payload["findings"].append(
        {
            "code": "FALSE_PAYOFF",
            "severity": "P1",
            "certainty": "confirmed",
            "diagnosis": "The victory has no visible obligation or loss.",
            "evidence_ids": payload["findings"][0]["evidence_ids"],
            "reader_impact": "The reward feels unearned.",
            "repair_target": "Make the gain narrow a later choice.",
            "preserve": ["existing clue"],
        }
    )
    output = Path(task.output_file)
    write_json(output, payload)

    result = validate_payoff_output(config, root, task, output)

    assert result.ok is False
    assert result.passed is False
    assert "FALSE_PAYOFF" in result.blocking_findings


def test_combined_structure_language_and_payoff_repetition_is_p1(tmp_path):
    config, root, text = seed_payoff_project(tmp_path, chapter_number=3)
    task = reader_payoff_task(config, chapter_number=3)
    payload = valid_review_payload(root, chapter_number=3)
    current = build_structure_observation(
        chapter_number=3,
        text=text,
        card=read_json(root / "20_outline" / "chapter_cards" / "ch003.json"),
        review=payload,
    )
    prior = []
    for chapter_number in (1, 2):
        item = json.loads(json.dumps(current))
        item["chapter_number"] = chapter_number
        prior.append(item)
    write_jsonl(root / "30_state" / "quality" / "structure_history.jsonl", prior)
    output = Path(task.output_file)
    write_json(output, payload)

    result = validate_payoff_output(config, root, task, output)

    assert result.ok is True
    assert result.passed is True
    report = read_json(Path(result.report_file))
    assert report["provenance"]["structure_analysis"]["status"] == "deferred_to_serial_history"


def test_finalize_failure_rolls_back_reward_and_structure_history(tmp_path, monkeypatch):
    config, root, _text = seed_payoff_project(tmp_path)
    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    write_json(output, valid_review_payload(root, chapter_number=1))
    assert validate_payoff_output(config, root, task, output).passed is True

    def fail_quality_history(*args, **kwargs):
        raise RuntimeError("simulated quality transaction failure")

    monkeypatch.setattr("longform_engine.orchestration.pipeline.record_quality_history", fail_quality_history)
    with pytest.raises(RuntimeError, match="quality transaction"):
        finalize_chapter(config, chapter_number=1, approved_by="human")

    assert read_jsonl(root / "30_state" / "reward_ledger.jsonl") == []
    assert read_jsonl(root / "30_state" / "quality" / "structure_history.jsonl") == []
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_revision_rollback_rebuilds_quality_histories(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)
    for chapter_number in (1, 2):
        (root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md").write_text(
            f"# Chapter {chapter_number}\n\nBody {chapter_number}.\n",
            encoding="utf-8",
        )
    write_jsonl(
        root / "30_state" / "reward_ledger.jsonl",
        [
            {"schema": "reader_reward_entry_v2", "chapter_number": 1},
            {"schema": "reader_reward_entry_v2", "chapter_number": 2},
        ],
    )
    write_jsonl(
        root / "30_state" / "quality" / "structure_history.jsonl",
        [
            {"schema": "structure_observation_v1", "chapter_number": 1},
            {"schema": "structure_observation_v1", "chapter_number": 2},
        ],
    )
    state_path = root / "30_state" / "novel_state.json"
    state = read_json(state_path)
    state.update({"last_finalized_chapter": 2, "current_chapter": 2})
    write_json(state_path, state)

    rollback(config, to_chapter=1)

    assert [item["chapter_number"] for item in read_jsonl(root / "30_state" / "reward_ledger.jsonl")] == [1]
    assert [
        item["chapter_number"]
        for item in read_jsonl(root / "30_state" / "quality" / "structure_history.jsonl")
    ] == [1]
    stale = read_json(root / "30_state" / "stale_indexes.json")
    assert set(stale["rebuilt_quality_indexes"]) == {
        "30_state/reward_ledger.jsonl",
        "30_state/quality/structure_history.jsonl",
    }


def test_production_loop_creates_and_validates_payoff_without_finalize(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)

    created = production_loop(config, max_steps=1, no_apply=True)
    assert created["steps"][0]["action"] == "reader_payoff_task"
    output = root / "50_workbench" / "quality_reviews" / "ch001.reader_payoff.json"
    write_json(output, valid_review_payload(root, chapter_number=1))

    registered = production_loop(config, max_steps=1, no_apply=True)
    assert registered["steps"][0]["action"] == "agent_result_validate"
    validated = production_loop(config, max_steps=1, no_apply=True)
    assert validated["steps"][0]["action"] == "reader_payoff_validate"
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert production_next(config)["status"] == "awaiting_finalize"


def test_milestone_payoff_pass_requires_editorial_review_before_finalize(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)
    config.data["editorial"]["review_mode"] = "risk_based"
    config.data["quality"]["semantic_review_milestones"] = [1]
    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    write_json(output, valid_review_payload(root, chapter_number=1))
    assert validate_payoff_output(config, root, task, output).passed is True

    action = production_next(config)

    assert action["status"] == "ready_for_editorial_review"
    assert "quality_milestone" in action["trigger_reasons"]
    assert "character_expression_risk" in action["trigger_reasons"]
    with pytest.raises(WorkflowError, match="editorial_review_missing"):
        finalize_chapter(config, chapter_number=1, approved_by="human")

    created = production_loop(config, max_steps=1, no_apply=True)
    assert created["steps"][0]["action"] == "editorial_review"
    assert production_next(config)["task_type"] == "editorial_review"
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def seed_payoff_project(tmp_path, *, chapter_number=1):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = project.root
    open_book(config)
    mark_project_ready(root, config)
    config.data["quality"]["assurance_mode"] = "balanced"
    config.data["quality"]["semantic_review_milestones"] = []
    config.data["quality"]["semantic_review_boundaries"] = False
    config.data.setdefault("editorial", {})["review_mode"] = "off"
    plan_chapter(config, chapter_number=chapter_number)
    text = (
        f"# 第{chapter_number}章 旧账的新缺口\n\n"
        "沈阙在封泥背面发现一道逆着指纹生长的裂纹。他没有宣布答案，只把军粮车的交接时辰重新排了一遍。\n\n"
        "巡卒催他结案，他却用自己的夜巡名额换来半刻查账时间。账页证明车队并未出城，也让他背上天亮前交出嫌疑人的约束。\n\n"
        "他合上账册，拒绝了立刻抓人的命令，决定先去核对那名已经死去三年的押车官。"
    )
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    draft.write_text(text, encoding="utf-8")
    gate = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    write_json(
        gate,
        {
            "schema_version": 1,
            "chapter_number": chapter_number,
            "passed": True,
            "severity": "PASS",
            "failures": [],
            "warnings": [],
            "source_path": f"40_manuscript/draft/ch{chapter_number:03d}.md",
            "source_sha256": hashlib.sha256(draft.read_bytes()).hexdigest(),
            "agent_semantic_review": {"required": False, "status": "not_requested"},
            "workflow_stage": "review_barrier",
        },
    )
    return config, root, text


def valid_review_payload(root: Path, *, chapter_number: int):
    draft = root / "40_manuscript" / "draft" / f"ch{chapter_number:03d}.md"
    card = read_json(root / "20_outline" / "chapter_cards" / f"ch{chapter_number:03d}.json")
    text = draft.read_text(encoding="utf-8")
    body_start = text.index("账页证明")
    body_end = text.index("。", body_start) + 1
    ending_start = text.index("他合上账册")
    promises = [str(item) for item in card.get("promise_refs", [])]
    evidence_id = f"ch{chapter_number:03d}.md@{body_start}:{body_end}"
    ending_id = f"ch{chapter_number:03d}.md@{ending_start}:{len(text)}"
    findings = [
        {
            "code": "PAYOFF_DELIVERED",
            "severity": "P3",
            "certainty": "confirmed",
            "diagnosis": "账页把失踪案改写为城内调包，并打开死亡押车官这条可验证的新线索。",
            "evidence_ids": [evidence_id],
            "reader_impact": "读者获得可验证的新判断和下一步问题。",
            "repair_target": "无需修复。",
            "preserve": ["账页线索", "死亡押车官"],
        },
        {
            "code": "COST_VISIBLE",
            "severity": "P3",
            "certainty": "confirmed",
            "diagnosis": "沈阙失去夜巡名额，并承担天亮前交出嫌疑人的期限。",
            "evidence_ids": [evidence_id],
            "reader_impact": "收益伴随明确义务，不是无代价胜利。",
            "repair_target": "无需修复。",
            "preserve": ["夜巡名额", "时限"],
        },
    ]
    if promises:
        findings.append(
            {
                "code": "PROMISE_ADVANCED",
                "severity": "P3",
                "certainty": "confirmed",
                "diagnosis": "结尾选择让既有承诺进入下一条可执行调查线。",
                "evidence_ids": [ending_id],
                "reader_impact": "承诺有进展但没有提前闭环。",
                "repair_target": "无需修复。",
                "preserve": promises,
            }
        )
    return {
        "schema": EVIDENCE_REVIEW_SCHEMA,
        "verdict": "pass",
        "coverage": {"reader_gain": "checked", "cost": "checked", "promise_progress": "checked"},
        "findings": findings,
    }


def validate_payoff_output(config, root: Path, task, output: Path):
    manifest = load_manifest(root, task.manifest_file)
    validate_production_agent_result(root, manifest, result_file=output)
    return reader_payoff_validate(config, chapter_number=task.chapter_number, file_path=output)


def positive_diagnosis(payload: dict, code: str) -> str:
    return next(item["diagnosis"] for item in payload["findings"] if item["code"] == code)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
