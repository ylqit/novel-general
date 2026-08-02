import hashlib
import json
from pathlib import Path

import pytest

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
    manifest = load_manifest(root, "reader_payoff_review:ch001:v1")
    strict = validate_manifest_strict(root, manifest)

    assert strict.ok, strict.errors
    assert manifest["output_schema"] == "reader_payoff_review_v1"
    assert manifest["allowed_output_paths"] == ["50_workbench/quality_reviews/ch001.reader_payoff.json"]
    assert manifest["context_policy"]["max_files"] == 6
    assert manifest["context_policy"]["max_chars"] == 20_000
    assert len(manifest["input_files"]) <= 6
    assert "30_state/reward_ledger.jsonl" not in manifest["input_files"]
    assert "20_outline/foreshadowing_ledger.json" not in manifest["input_files"]
    assert "50_workbench/quality_reviews/ch001.reader_payoff.context.json" in manifest["input_files"]
    assert Path(result.task_file).stat().st_size < 20_000
    context = read_json(Path(result.context_file))
    assert context["selection"]["full_ledgers_excluded"] is True
    assert context["selection"]["previous_reward_limit"] == 1
    assert context["selection"]["related_promise_limit"] == 8
    assert context["quality_contract"]["primary_market"] == "qidian_male"
    assert context["quality_contract"]["contract"]["platform_promise"]
    assert len(context["quality_contract"]["compatibility_observations"]) <= 3
    assert all(
        item["severity"] == "P2" and item["blocking"] is False
        for item in context["quality_contract"]["compatibility_observations"]
    )
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() in Path(result.task_file).read_text(encoding="utf-8")
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
    payload["source_hash"] = "0" * 64
    payload["evidence_spans"][0]["text"] = "tampered"
    output = Path(task.output_file)
    write_json(output, payload)
    reward_before = (root / "30_state" / "reward_ledger.jsonl").read_text(encoding="utf-8")
    structure_before = (root / "30_state" / "quality" / "structure_history.jsonl").read_text(encoding="utf-8")

    result = reader_payoff_validate(config, chapter_number=1, file_path=output)

    assert result.ok is False
    assert result.passed is False
    assert any("source_hash" in item for item in result.errors)
    assert any(".text" in item for item in result.errors)
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
    validation = reader_payoff_validate(config, chapter_number=1, file_path=output)
    assert validation.passed is True
    output.write_text(output.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(WorkflowError, match="reader payoff review"):
        finalize_chapter(config, chapter_number=1, approved_by="human")


def test_payoff_finalize_records_observed_reward_and_structure_atomically(tmp_path):
    config, root, text = seed_payoff_project(tmp_path)
    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    payload = valid_review_payload(root, chapter_number=1)
    write_json(output, payload)
    assert reader_payoff_validate(config, chapter_number=1, file_path=output).passed is True

    result = finalize_chapter(config, chapter_number=1, approved_by="human")

    rewards = read_jsonl(root / "30_state" / "reward_ledger.jsonl")
    structures = read_jsonl(root / "30_state" / "quality" / "structure_history.jsonl")
    assert Path(result.final_file).exists()
    assert len(rewards) == 1
    assert rewards[0]["schema"] == "reader_reward_entry_v2"
    assert rewards[0]["chapter_number"] == 1
    assert rewards[0]["observed_gain"] == payload["observed"]["reader_gain"]
    assert rewards[0]["duty_fulfilled"] is True
    assert rewards[0]["observation_status"] == "semantic_reviewed"
    assert rewards[0]["finalized"] is True
    assert rewards[0]["planned_gain"] == payload["planned"]["reader_gain"]
    assert rewards[0]["observed_cost"] == payload["observed"]["cost"]
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
    payload["fake_payoff_flags"] = [
        {
            "code": "cost_free_victory",
            "severity": "P1",
            "message": "The victory has no visible obligation or loss.",
            "evidence_span_indices": [0],
            "recommendation": "Make the gain narrow a later choice.",
        }
    ]
    output = Path(task.output_file)
    write_json(output, payload)

    result = reader_payoff_validate(config, chapter_number=1, file_path=output)

    assert result.ok is False
    assert result.passed is False
    assert "fake_payoff:P1:cost_free_victory" in result.blocking_findings


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

    result = reader_payoff_validate(config, chapter_number=3, file_path=output)

    assert result.ok is False
    assert "structure:combined_formula_repetition" in result.blocking_findings
    report = read_json(Path(result.report_file))
    assert report["structure_analysis"]["blocking"] is True
    assert any(item["code"] == "combined_formula_repetition" for item in report["structure_analysis"]["findings"])


def test_finalize_failure_rolls_back_reward_and_structure_history(tmp_path, monkeypatch):
    config, root, _text = seed_payoff_project(tmp_path)
    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    write_json(output, valid_review_payload(root, chapter_number=1))
    assert reader_payoff_validate(config, chapter_number=1, file_path=output).passed is True

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

    validated = production_loop(config, max_steps=1, no_apply=True)
    assert validated["steps"][0]["action"] == "reader_payoff_validate"
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert production_next(config)["status"] == "agent_task_validated"


def test_milestone_payoff_pass_requires_editorial_review_before_finalize(tmp_path):
    config, root, _text = seed_payoff_project(tmp_path)
    config.data["quality"]["semantic_review_milestones"] = [1]
    task = reader_payoff_task(config, chapter_number=1)
    output = Path(task.output_file)
    write_json(output, valid_review_payload(root, chapter_number=1))
    assert reader_payoff_validate(config, chapter_number=1, file_path=output).passed is True

    action = production_next(config)

    assert action["status"] == "ready_for_editorial_review"
    assert action["trigger_reasons"] == ["quality_milestone"]
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
    evidence = [
        {
            "start": body_start,
            "end": body_end,
            "text": text[body_start:body_end],
            "supports": ["duty", "reader_gain", "cost"],
        },
        {
            "start": ending_start,
            "end": len(text),
            "text": text[ending_start:],
            "supports": ["ending"],
        },
    ]
    return {
        "schema": "reader_payoff_review_v1",
        "chapter_number": chapter_number,
        "source_path": f"40_manuscript/draft/ch{chapter_number:03d}.md",
        "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "planned": {
            "chapter_duty": card.get("chapter_duty") or card.get("duty"),
            "reader_gain": card.get("reader_gain") or card.get("reader_payoff"),
            "cost": card.get("cost"),
            "promise_refs": promises,
        },
        "observed": {
            "duty_fulfilled": True,
            "reader_gain": "账页把失踪案改写为城内调包，并打开死亡押车官这条可验证的新线索。",
            "cost": "沈阙失去夜巡名额，并承担天亮前交出嫌疑人的期限。",
            "promise_progress": [
                {
                    "promise_ref": promise,
                    "status": "advanced",
                    "evidence_span_indices": [0],
                    "message": "The account evidence advances this promise.",
                }
                for promise in promises
            ],
            "ending_mode": "decision",
        },
        "evidence_spans": evidence,
        "fake_payoff_flags": [],
        "craft_observation": {
            "opening_mode": "discovery",
            "topology_id": card.get("topology_id") or "conflict_escalation",
            "ending_mode": "decision",
            "scene_count": 1,
            "dominant_scene_type": "investigation",
            "reader_gain_position": "middle",
            "dialogue_acts": ["pressure", "refusal"],
            "emotional_curve": ["suspicious", "pressured", "decisive"],
        },
        "verdict": "pass",
        "recommendations": [],
    }


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
