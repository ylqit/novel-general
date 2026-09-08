import json
from pathlib import Path

import pytest

from longform_engine.agent_tasks import load_manifest, update_task_status
from longform_engine.chapter_contract import (
    ChapterContractError,
    load_verified_chapter_contract,
)
from longform_engine.config import load_project_config
from longform_engine.human_review_consultation import (
    consultation_status,
    create_human_review_consult_task,
    mark_stale_human_consultations,
)
from longform_engine.human_story_review import (
    apply_human_story_review,
    create_human_story_review_task,
    human_story_review_status,
    validate_human_story_review,
)
from longform_engine.orchestration import continue_write, open_book
from longform_engine.storage import init_project
from longform_engine.story_brief import (
    load_current_story_brief_binding,
    story_brief_paths,
    story_brief_status,
)
from tests.project_fixtures import mark_project_ready, update_chapter_contract_fixture
from tests.test_story_architecture_v050 import seed_candidate, write_review


def seed_story_brief(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    continue_write(config, chapter_number=1)
    return config, root


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_contract_v5_obligation_changes_contract_and_story_brief_basis(tmp_path: Path):
    config, root = seed_story_brief(tmp_path)
    before = load_current_story_brief_binding(root, 1)
    current_contract = read_json(root / "20_outline" / "chapter_contracts" / "ch001.json")
    aftermath = dict(current_contract["aftermath"])
    aftermath["description"] = "胜利感被失去退路的愧疚压住。"
    contract = update_chapter_contract_fixture(root, 1, aftermath=aftermath)

    stale = story_brief_status(root, 1)
    assert stale["status"] == "stale"
    assert stale["contract_current"] is True
    assert stale["human_chapter_intent_current"] is False

    intent_path = root / "20_outline" / "chapter_intents" / "ch001.json"
    intent = read_json(intent_path)
    intent["chapter_contract_sha256"] = contract["chapter_contract_hash"]
    intent["approved_at"] = "fixture-reapproved-after-contract-change"
    write_json(intent_path, intent)

    continue_write(config, chapter_number=1)
    after = load_current_story_brief_binding(root, 1)
    assert after["chapter_contract_sha256"] != before["chapter_contract_sha256"]
    assert after["story_brief_basis_sha256"] != before["story_brief_basis_sha256"]
    markdown = story_brief_paths(root, 1)["markdown"].read_text(encoding="utf-8")
    assert "胜利感被失去退路的愧疚压住" in markdown
    task = read_json(story_brief_paths(root, 1)["task"])
    assert len(task["story_brief"]["scenes"]) == 1
    assert markdown.count("### 场景 ") == 1
    assert "- 选择：" not in markdown  # Chapter-level choices are not forced onto each beat.
    assert "- 代价：" not in markdown


def test_required_fact_text_is_not_cut_to_eight_short_summaries():
    from longform_engine.orchestration.pipeline import author_relevant_facts

    statements = [f"第{i}项：" + "只有接触实物才能判断，污染会影响判断。" * 25 for i in range(11)]
    values = author_relevant_facts(constraint_packet={},
        resolved_contract_refs=[{"value": text} for text in statements])
    assert values == statements


def test_voice_projection_changes_only_basis_and_rebuilds_author_brief(tmp_path: Path):
    config, root = seed_story_brief(tmp_path)
    before = load_current_story_brief_binding(root, 1)
    expression_path = root / "10_bible" / "character_expression.json"
    expression = read_json(expression_path)
    expression["character_expression_contracts"][0]["speech_register"] = (
        "先指出可见证据，再用半句反问掩住不安。"
    )
    write_json(expression_path, expression)

    stale = story_brief_status(root, 1)
    assert stale["status"] == "stale"
    assert stale["contract_current"] is True
    assert stale["human_chapter_intent_current"] is True
    assert stale["basis_current"] is False

    continue_write(config, chapter_number=1)
    after = load_current_story_brief_binding(root, 1)
    assert after["chapter_contract_sha256"] == before["chapter_contract_sha256"]
    assert after["story_brief_basis_sha256"] != before["story_brief_basis_sha256"]
    markdown = story_brief_paths(root, 1)["markdown"].read_text(encoding="utf-8")
    assert "先指出可见证据，再用半句反问掩住不安" in markdown
    lowered = markdown.casefold()
    for internal_token in ("sha256", "finding_code", "fact_id", "promise_id"):
        assert internal_token not in lowered


def test_superseded_writer_manifest_is_rebuilt_instead_of_reused(tmp_path: Path):
    config, root = seed_story_brief(tmp_path)
    paths = story_brief_paths(root, 1)
    manifest = load_manifest(root, paths["manifest"])
    update_task_status(
        root,
        str(manifest["task_id"]),
        to_status="superseded",
        command="test stale Story Brief",
        artifact=paths["manifest"],
    )
    assert load_manifest(root, paths["manifest"])["status"] == "superseded"

    from longform_engine.production import production_next
    action = production_next(config)
    assert action["status"] == "ready_for_continue_write"
    assert action["blocked_by"] == "writing_task_stale"
    assert action["next_command"] == "longform-engine continue-write project.yaml --chapter 1"
    assert action["sources"] == ["50_workbench/writing_tasks/ch001.json"]

    original_bytes = paths["manifest"].read_bytes()
    original_id = load_manifest(root, paths["manifest"])["task_id"]
    continue_write(config, chapter_number=1)

    replacement = load_manifest(root, paths["manifest"])
    assert replacement["status"] == "awaiting_agent"
    assert replacement["task_id"] != original_id
    assert original_id in replacement["supersedes_task_ids"]
    original = load_manifest(root, original_id)
    assert original["status"] == "superseded"
    assert (root / original["manifest_file"]).read_bytes() == original_bytes
    assert story_brief_status(root, 1)["status"] == "current"


def test_pre_v5_chapter_contract_is_explicitly_rejected(tmp_path: Path):
    _config, root = seed_story_brief(tmp_path)
    contract_path = root / "20_outline" / "chapter_contracts" / "ch001.json"
    contract = read_json(contract_path)
    contract["schema"] = "chapter_contract_v3"
    write_json(contract_path, contract)

    with pytest.raises(ChapterContractError) as exc_info:
        load_verified_chapter_contract(root, 1)

    assert "schema must be chapter_contract_v5" in str(exc_info.value)


def test_manifest_replacement_failure_restores_manifest_index_and_events(tmp_path, monkeypatch):
    import longform_engine.agent_tasks as tasks

    _config, root = seed_story_brief(tmp_path)
    path = story_brief_paths(root, 1)["manifest"]
    manifest = read_json(path)
    tracked = [path, *tasks.agent_task_lifecycle_mutation_paths(root)]
    before = {item: item.read_bytes() if item.is_file() else None for item in tracked}
    tasks.write_manifest(root, manifest, path, preserve_replaced=True)
    assert {item: item.read_bytes() if item.is_file() else None for item in tracked} == before
    manifest["created_at"] = "2026-09-06T16:00:00+00:00"
    register = tasks.register_manifest

    def fail_after_register(*args, **kwargs):
        register(*args, **kwargs)
        raise OSError("injected registration failure")

    monkeypatch.setattr(tasks, "register_manifest", fail_after_register)
    with pytest.raises(OSError, match="injected registration failure"):
        tasks.write_manifest(root, manifest, path, preserve_replaced=True)
    assert {item: item.read_bytes() if item.is_file() else None for item in tracked} == before
    assert not list((root / "50_workbench/agent_tasks/manifests").glob("*.json"))


def test_basis_only_drift_stales_acceptance_and_consultation(tmp_path: Path):
    config, root, _task = seed_candidate(tmp_path)
    review_task = create_human_story_review_task(config, chapter_number=1)
    review = write_review(root, review_task.template_file, decision="accept")
    validated = validate_human_story_review(config, chapter_number=1, file_path=review)
    assert validated.ok, validated.errors
    apply_human_story_review(
        config,
        chapter_number=1,
        file_path=review,
        approved_by="human",
    )
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft_text = draft.read_text(encoding="utf-8")
    create_human_review_consult_task(
        config,
        chapter_number=1,
        start=0,
        end=min(40, len(draft_text)),
        question="人物声音的压力来源是否清楚？",
    )
    before = load_current_story_brief_binding(root, 1)

    expression_path = root / "10_bible" / "character_expression.json"
    expression = read_json(expression_path)
    expression["character_expression_contracts"][0]["speech_register"] = (
        "只在看见对方退路时压低声音，并用具体代价结束争论。"
    )
    write_json(expression_path, expression)

    assert human_story_review_status(config, chapter_number=1)["status"] == "stale"
    stale_sessions = mark_stale_human_consultations(root, chapter_number=1)
    assert stale_sessions
    consult = consultation_status(config, chapter_number=1)
    assert consult["sessions"][0]["status"] == "stale"
    assert consult["sessions"][0]["story_brief_basis_sha256"] == before[
        "story_brief_basis_sha256"
    ]
