import json
from pathlib import Path
import pytest

from longform_engine.agent_tasks import load_manifest, status_summary, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.creative import expand_check, expand_task, humanize_check, humanize_task, style_extract
from longform_engine.gates import gate_check, pacing_review
from longform_engine.orchestration import WorkflowError, continue_write, open_book as engine_open_book, plan_chapter
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


def open_book(config):
    result = engine_open_book(config)
    mark_project_ready(config.path.parent, config)
    return result


def test_open_book_creates_creative_brief_and_continue_write_injects_craft_inputs(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"

    open_book(project_config)
    result = continue_write(project_config, chapter_number=1)

    brief = json.loads((root / "10_bible" / "creative_brief.json").read_text(encoding="utf-8"))
    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8"))
    task_md = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")

    assert result.status == "task_ready"
    assert brief["target_audience"]
    assert task["creative_brief"]["status"] == "confirmed"
    assert task["writer_craft_brief"]["reader_payoff"]
    assert task["humanizer_rules"]["two_pass_workflow"]["pass_1_remove_ai_templates"]
    assert "Creative Brief" in task_md
    assert "Writer Craft Brief" in task_md
    assert "Humanizer v2" in task_md


def test_continue_write_writes_writable_brief_beat_expansion_and_constraints(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    (root / "20_outline" / "outline_anchors.json").write_text(
        json.dumps(
            [
                {
                    "chapter_number": 1,
                    "duty": "plant the bell debt without solving it",
                    "status": "rising",
                    "forbidden_reveals": ["Dragon Crown"],
                    "resolution_markers": ["ultimate patron"],
                    "must_preserve_suspense": ["who controls the bell"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "30_state" / "character_state.json").write_text(
        json.dumps(
            [
                {
                    "id": "character:lin",
                    "name": "Lin",
                    "status": "guarded",
                    "current_goal": "protect the gate clue",
                    "forbidden_actions": ["fully trust the patron"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    card_path = root / "20_outline" / "chapter_cards" / "ch001.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card.update(
        {
            "duty": "plant the bell debt without solving it",
            "chapter_duty": "plant the bell debt without solving it",
            "forbidden_reveals": ["Dragon Crown"],
            "resolution_markers": ["ultimate patron"],
            "must_preserve_suspense": ["who controls the bell"],
        }
    )
    card_path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    continue_write(project_config, chapter_number=1)

    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8"))
    task_md = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")
    writing_brief = task["writing_brief"]
    beat_requirements = task["beat_expansion_requirements"]
    constraints = task["constraint_packet"]
    reverse_brake = writing_brief["reverse_brake"]

    assert writing_brief["stage"]["strategy"]
    assert writing_brief["chapter_duty"] == "plant the bell debt without solving it"
    assert writing_brief["pacing_tier"]
    assert writing_brief["scene_entry"]["mode"] == "in_scene"
    assert "Dragon Crown" in writing_brief["forbidden_reveals"]
    assert "ultimate patron" in writing_brief["do_not_resolve"]
    assert "who controls the bell" in writing_brief["must_preserve_suspense"]
    assert "ultimate patron" in writing_brief["this_chapter_must_not_solve"]
    assert "who controls the bell" in writing_brief["must_keep_suspense"]
    assert reverse_brake["allowed_reveal_level"] == "hint"
    assert constraints["reverse_brake"]["allowed_reveal_level"] == "hint"
    assert len(beat_requirements) == 5
    assert all(item["scene_goal"] for item in beat_requirements)
    assert all(item["conflict_point"] for item in beat_requirements)
    assert all(item["information_release"] for item in beat_requirements)
    assert all("psychology" in item["expansion_requirements"] for item in beat_requirements)
    assert all("Dragon Crown" in item["forbidden_reveals"] for item in beat_requirements)
    assert constraints["rag"]["source"].endswith("next_plot_context.md")
    assert "story_graph" in constraints
    assert "tcs" in constraints
    assert constraints["character_memory"]["status"] == "available"
    assert constraints["event_matrix"]["source"].endswith("event_matrix.json")
    assert constraints["style_profile"]["source"].endswith("style_bible.md")
    assert "Writable Brief" in task_md
    assert "Beat Expansion Requirements" in task_md
    assert "Constraint Packet" in task_md
    assert "Reverse Brake" in task_md
    assert "Forbidden reveals: Dragon Crown" in task_md


def test_continue_write_blocks_missing_applied_creative_brief(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    (root / "10_bible" / "creative_brief.json").unlink()

    with pytest.raises(WorkflowError, match="Project is not ready for chapter writing"):
        continue_write(project_config, chapter_number=1)

    assert not (root / "10_bible" / "creative_brief.json").exists()
    assert not (root / "40_manuscript" / "draft" / "ch001.md").exists()


def test_humanizer_task_and_check_stay_in_workbench(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nThis stands as a pivotal moment. TODO: keep prompt residue.\n", encoding="utf-8")

    task = humanize_task(project_config, chapter_number=1, source="draft")
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    candidate.write_text(
        "# Chapter 1\n\nThis stands as a pivotal and crucial moment, a significant tapestry that serves as a showcase.\n",
        encoding="utf-8",
    )
    check = humanize_check(project_config, chapter_number=1, file_path=candidate)

    assert "50_workbench" in task.task_file
    assert Path(task.candidate_file).name == "ch001.humanized_candidate.md"
    assert Path(task.candidate_file).parent.name == "repair_candidates"
    manifest = load_manifest(root, "humanize:ch001:v4")
    strict = validate_manifest_strict(root, manifest)
    assert strict.ok, strict.errors
    assert check.passed is False
    assert status_summary(root, chapter_number=1)["by_status"]["invalid"] >= 1
    assert any(item["code"] == "generic_ai_diction" for item in check.issues)
    assert "creative humanize-task" in check.next_command
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_chinese_humanizer_detects_webnovel_ai_categories(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# 第一章\n\nTODO 写作说明：这里需要改成正文。\n", encoding="utf-8")

    task = humanize_task(project_config, chapter_number=1, source="draft")
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    candidate.write_text(
        (
            "# 第一章\n\n"
            "TODO：这里还没有写完。林远仿佛不禁意识到，这件事意义深远。"
            "总之，可以看出，他嘴角微扬，眼神复杂。"
            "他似乎有些微微迟疑，却不仅要守住城门，还要守住旧债，更要守住命运。"
        ),
        encoding="utf-8",
    )
    check = humanize_check(project_config, chapter_number=1, file_path=candidate)
    issues = {item["code"]: item for item in check.issues}
    report_text = Path(check.markdown_report).read_text(encoding="utf-8")
    task_text = Path(task.task_file).read_text(encoding="utf-8")

    assert check.passed is False
    assert "Pass 1: 中文 AI 痕迹清理" in task_text
    assert "Pass 2: 中文网文质感增强" in task_text
    assert issues["humanizer_meta_residue"]["severity"] == "P0"
    assert issues["humanizer_inflated_significance"]["category"] == "意义膨胀"
    assert issues["humanizer_summary_voice"]["severity"] == "P1"
    assert issues["humanizer_cliche_action"]["category"] == "套话动作"
    assert issues["humanizer_high_frequency_words"]["severity"] == "P2"
    assert issues["humanizer_weak_adverbs"]["category"] == "弱化副词"
    assert issues["humanizer_template_triad"]["category"] == "模板三连"
    assert issues["humanizer_inflated_significance"]["evidence"]
    assert issues["humanizer_cliche_action"]["suggestion"]
    assert "Evidence:" in report_text
    assert "Fix:" in report_text
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_chinese_humanizer_detects_uniform_sentence_length(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("# 第一章\n\n他推门。她回头。他停步。钟声响。雨落下。火光动。刀出鞘。门合上。", encoding="utf-8")

    check = humanize_check(project_config, chapter_number=1, file_path=candidate)

    assert any(item["code"] == "humanizer_uniform_sentence_length" and item["category"] == "等长句" for item in check.issues)


def test_humanizer_v3_rejects_empty_text_and_counts_repeated_same_pattern(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(" \n\t", encoding="utf-8")

    empty = humanize_check(project_config, chapter_number=1, file_path=candidate)
    assert any(item["code"] == "humanizer_empty_candidate" for item in empty.issues)

    candidate.write_text(
        "# 第一章\n\n雨仿佛压低了城门，林远仿佛忘了自己为何而来。",
        encoding="utf-8",
    )
    repeated = humanize_check(project_config, chapter_number=1, file_path=candidate)
    issue = next(item for item in repeated.issues if item["code"] == "humanizer_high_frequency_words")
    assert issue["evidence"][0]["pattern"] == "仿佛"
    assert issue["evidence"][0]["count"] == 2


def test_gate_uses_humanizer_report_for_chinese_p1_failures(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    scene = "林远站在城门前，听见旧钟压过雨声。他看见守卫换岗，也看见债牌被人翻到背面。"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# 第一章\n\n" + scene * 90 + "这一刻意义深远，却没人敢把原因说出口。", encoding="utf-8")

    gate = gate_check(project_config, chapter_number=1)
    artifact_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    humanize_report = (artifact_dir / "humanize_report.md").read_text(encoding="utf-8")

    assert gate.passed is False
    assert any(item["code"] == "humanizer_inflated_significance" and item["severity"] == "P1" for item in gate.failures)
    assert "意义膨胀" in humanize_report
    assert not (artifact_dir / "repair_plan.md").exists()
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_expand_task_and_check_repair_short_chapter_without_pollution(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nShort draft at the gate.\n", encoding="utf-8")

    gate = gate_check(project_config, chapter_number=1)
    task = expand_task(project_config, chapter_number=1, source="draft")
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.expanded_candidate.md"
    expansion_beat = (
        'At the north gate, wind scraped the stone road and the iron bell gave one dry sound. '
        '"Hold the line," Lin said, but his breath caught when the locked door answered from behind him. '
        "He hesitated, thought of the debt on his father's name, grabbed the bell rope, then stepped through "
        "the hall before the patrol arrived. "
    )
    candidate.write_text("# Chapter 1\n\n" + expansion_beat * 40 + "\nSecret behind the door?\n", encoding="utf-8")
    check = expand_check(project_config, chapter_number=1, file_path=candidate)

    assert gate.passed is False
    assert any(item["code"] == "content_character_count" for item in gate.failures)
    assert task.missing_content_characters > 0
    assert task.expansion_types == ("scene", "dialogue", "psychology", "action", "transition")
    assert Path(task.candidate_file).parent.name == "repair_candidates"
    assert Path(task.manifest_file).exists()
    assert "Content Expansion Task" in Path(task.task_file).read_text(encoding="utf-8")
    manifest = load_manifest(root, task.manifest_file)
    strict = validate_manifest_strict(root, manifest)
    assert manifest["task_type"] == "content_expand"
    assert strict.ok, strict.errors
    assert check.passed is True
    assert status_summary(root, chapter_number=1)["by_status"]["validated"] >= 1
    assert "draft submit" in check.next_command
    assert "--overwrite" in check.next_command
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (root / "60_rag" / "chunks" / "ch001.json").exists()


def test_style_extract_writes_sample_profile_and_continue_write_uses_it(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    sample = tmp_path / "sharp_dialogue_sample.md"
    sample.write_text(
        "\n\n".join(
            [
                '"Hold the gate bell," Lin said.',
                '"The gate bell is already awake."',
                "He stepped once, stopped, and listened.",
                '"Then we move," she said.',
            ]
        ),
        encoding="utf-8",
    )

    result = style_extract(project_config, sample_files=[sample], name="sharp_dialogue", source_project="reference-book")
    continue_write(project_config, chapter_number=1)
    current = json.loads((root / "10_bible" / "style_profiles" / "current_style_profile.json").read_text(encoding="utf-8"))
    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8"))

    assert result.name == "sharp_dialogue"
    assert result.activated is True
    assert current["profile_type"] == "sample_extract"
    assert current["sample_sources"][0]["source_project"] == "reference-book"
    assert current["profile"]["fingerprint"]["dialogue_ratio"] > 0
    assert current["profile"]["common_phrases"]
    assert task["style_context"]["source"].endswith("current_style_profile.json")
    assert task["style_context"]["profile_type"] == "sample_extract"
    assert task["constraint_packet"]["style_profile"]["fingerprint"]["avg_sentence_chars"] == result.fingerprint["avg_sentence_chars"]


def test_gate_detects_obvious_style_drift_from_active_sample_profile(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    sample = tmp_path / "short_sample.md"
    sample.write_text(
        "\n\n".join(
            [
                '"Run," Lin said.',
                '"Now."',
                "He moved.",
                '"Listen."',
            ]
        ),
        encoding="utf-8",
    )
    style_extract(project_config, sample_files=[sample], name="short_dialogue", source_project="reference-book")
    plan_chapter(project_config, chapter_number=1)
    long_sentence = (
        "Lin considered the geography of the gate, the unfinished debt, the weathered road, "
        "the council's older promises, the private fear behind every delayed answer, and the "
        "slow consequence of choosing silence over speech in a city that mistook hesitation for loyalty. "
    )
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\n" + long_sentence * 12, encoding="utf-8")

    result = gate_check(project_config, chapter_number=1)
    style_review = (root / "50_workbench" / "gate_artifacts" / "ch001" / "style_review.md").read_text(encoding="utf-8")

    assert result.passed is False
    assert any(item["code"] == "style_drift" and item["severity"] == "P1" for item in result.failures)
    assert "Active Style Baseline" in style_review
    assert "style drift from active sample profile" in style_review


def test_semantic_reader_pacing_review_writes_reader_experience_artifact(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nHe walked across the room. He waited. He stopped.\n", encoding="utf-8")

    result = pacing_review(project_config, chapter_number=1, semantic_reader=True)

    assert result.reader_experience_report.endswith("reader_experience_review.md")
    assert any("ending hook" in issue for issue in result.issues)
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "reader_experience_review.md").exists()


def test_semantic_reader_recognizes_chinese_deadline_as_concrete_tail_pressure(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# 第一章\n\n他选择把旧卷列入公开追索。午时封库，距离共同签验只剩两个时辰。",
        encoding="utf-8",
    )

    result = pacing_review(project_config, chapter_number=1, semantic_reader=True)

    assert not any("ending hook" in issue for issue in result.issues)


def test_gate_defers_repair_plan_until_review_barrier(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nTODO: write later. as an ai language model\n", encoding="utf-8")

    result = gate_check(project_config, chapter_number=1)
    gate_payload = json.loads(
        (root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8")
    )

    assert result.passed is False
    assert gate_payload["next_command"] == "longform-engine production next project.yaml"
    assert not (root / "50_workbench" / "gate_artifacts" / "ch001" / "repair_plan.md").exists()


def seed_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    return load_project_config(project.project_config)
