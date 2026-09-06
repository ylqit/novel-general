import json
from pathlib import Path

from longform_engine.agent_tasks import load_manifest, status_summary, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.creative import (
    detect_prose_naturalness_issues,
    expand_check,
    expand_task,
    prose_naturalness_check,
    prose_naturalness_task,
    style_extract,
)
from longform_engine.gates import gate_check, pacing_review
from longform_engine.orchestration import continue_write, open_book as engine_open_book
from longform_engine.storage import init_project
from tests.project_fixtures import (
    mark_project_ready,
    compile_chapter_brief_fixture,
    rebind_human_intent_fixture,
    refresh_arc_simulation_fixture,
    update_chapter_contract_fixture,
)


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
    assert task["fact_inventory_summary"]["categories"]["chapter_contract"] == 1
    assert task["fact_inventory_summary"]["categories"]["methods"] >= 2
    assert "本章正在发生" in task_md
    assert "## 逐场行动" in task_md
    assert "章节合同" not in task_md
    assert "## Creative Brief" not in task_md


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
    contract = update_chapter_contract_fixture(
        root,
        1,
        chapter_duty="plant the bell debt without solving it",
        protected_invariants=[
            "Dragon Crown remains unrevealed.",
            "The ultimate patron remains unresolved.",
            "Suspense over who controls the bell remains active.",
        ],
    )
    rebind_human_intent_fixture(root, 1, contract)
    refresh_arc_simulation_fixture(root)

    continue_write(project_config, chapter_number=1)

    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8"))
    task_md = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")
    assert task["fact_inventory_summary"]["categories"]["chapter_contract"] == 1
    assert task["fact_inventory_summary"]["categories"]["historical_evidence"] >= 1
    assert "plant the bell debt without solving it" in task_md
    assert "Dragon Crown" in task_md
    assert "ultimate patron" in task_md
    assert "who controls the bell" in task_md
    assert task_md.count("本章正在发生") == 1


def test_continue_write_reinitializes_missing_creative_brief(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    open_book(project_config)
    (root / "10_bible" / "creative_brief.json").unlink()

    result = continue_write(project_config, chapter_number=1)

    assert result.status == "task_ready"
    assert (root / "10_bible" / "creative_brief.json").exists()
    assert not (root / "40_manuscript" / "draft" / "ch001.md").exists()


def test_prose_naturalness_task_and_check_stay_in_workbench(tmp_path):
    project_config = seed_project(tmp_path)
    project_config.data["quality"]["semantic_review_milestones"] = []
    project_config.data["quality"]["semantic_review_boundaries"] = False
    project_config.data["quality"]["profile"]["strictness"] = "light"
    root = tmp_path / "novel"
    mark_project_ready(root, project_config)
    mark_project_ready(project_config.path.parent, project_config, preserve_existing_characters=True)
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nThis stands as a pivotal moment. TODO: keep prompt residue.\n", encoding="utf-8")

    compile_chapter_brief_fixture(root, project_config)
    task = prose_naturalness_task(project_config, chapter_number=1, source="draft")
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.prose_naturalness_candidate.md"
    candidate.write_text(
        "# Chapter 1\n\nThis stands as a pivotal and crucial moment, a significant tapestry that serves as a showcase.\n",
        encoding="utf-8",
    )
    check = prose_naturalness_check(project_config, chapter_number=1, file_path=candidate)

    assert "50_workbench" in task.task_file
    assert Path(task.candidate_file).name == "ch001.prose_naturalness_candidate.md"
    assert Path(task.candidate_file).parent.name == "repair_candidates"
    manifest = load_manifest(root, "prose_naturalness:ch001:v5")
    strict = validate_manifest_strict(root, manifest)
    assert strict.ok, strict.errors
    assert check.passed is True
    assert status_summary(root, chapter_number=1)["by_status"]["validated"] >= 1
    assert any(item["code"] == "template_diction_signal" for item in check.issues)
    assert all(item["severity"] == "P2" for item in check.issues)
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_chinese_prose_naturalness_detects_webnovel_ai_categories(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# 第一章\n\nTODO 写作说明：这里需要改成正文。\n", encoding="utf-8")

    mark_project_ready(root, project_config)
    compile_chapter_brief_fixture(root, project_config)
    task = prose_naturalness_task(project_config, chapter_number=1, source="draft")
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.prose_naturalness_candidate.md"
    candidate.write_text(
        (
            "# 第一章\n\n"
            "TODO：这里还没有写完。林远仿佛不禁意识到，这件事意义深远。"
            "总之，可以看出，他嘴角微扬，眼神复杂。"
            "他似乎有些微微迟疑，却不仅要守住城门，还要守住旧债，更要守住命运。"
        ),
        encoding="utf-8",
    )
    check = prose_naturalness_check(project_config, chapter_number=1, file_path=candidate)
    issues = {item["code"]: item for item in check.issues}
    report_text = Path(check.markdown_report).read_text(encoding="utf-8")
    task_text = Path(task.task_file).read_text(encoding="utf-8")

    assert check.passed is False
    assert "Pass 1: 模板化功能清理" in task_text
    assert "Pass 2: 中文网文质感增强" in task_text
    assert issues["prose_naturalness_meta_residue"]["severity"] == "P0"
    assert issues["prose_naturalness_inflated_significance"]["category"] == "意义膨胀"
    assert issues["prose_naturalness_summary_voice"]["severity"] == "P2"
    assert issues["prose_naturalness_cliche_action"]["category"] == "套话动作"
    assert issues["prose_naturalness_high_frequency_words"]["severity"] == "P2"
    assert issues["prose_naturalness_weak_adverbs"]["category"] == "弱化副词"
    assert issues["prose_naturalness_template_triad"]["category"] == "模板三连"
    assert issues["prose_naturalness_inflated_significance"]["evidence"]
    assert issues["prose_naturalness_cliche_action"]["suggestion"]
    assert "Evidence:" in report_text
    assert "Fix:" in report_text
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_chinese_prose_naturalness_detects_uniform_sentence_length(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.prose_naturalness_candidate.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("# 第一章\n\n他推门。她回头。他停步。钟声响。雨落下。火光动。刀出鞘。门合上。", encoding="utf-8")

    check = prose_naturalness_check(project_config, chapter_number=1, file_path=candidate)

    assert any(item["code"] == "prose_naturalness_uniform_sentence_length" and item["category"] == "等长句" for item in check.issues)


def test_prose_naturalness_v4_rejects_empty_text_and_counts_repeated_same_pattern(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    candidate = root / "50_workbench" / "repair_candidates" / "ch001.prose_naturalness_candidate.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(" \n\t", encoding="utf-8")

    empty = prose_naturalness_check(project_config, chapter_number=1, file_path=candidate)
    assert any(item["code"] == "prose_naturalness_empty_candidate" for item in empty.issues)

    candidate.write_text(
        "# 第一章\n\n雨仿佛压低了城门，林远仿佛忘了自己为何而来。",
        encoding="utf-8",
    )
    repeated = prose_naturalness_check(project_config, chapter_number=1, file_path=candidate)
    issue = next(item for item in repeated.issues if item["code"] == "prose_naturalness_high_frequency_words")
    assert issue["evidence"][0]["pattern"] == "仿佛"
    assert issue["evidence"][0]["count"] == 2


def test_gate_keeps_isolated_significance_language_as_nonblocking_p2_signal(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    mark_project_ready(project_config.path.parent, project_config, preserve_existing_characters=True)
    scene = "林远站在城门前，听见旧钟压过雨声。他看见守卫换岗，也看见债牌被人翻到背面。"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# 第一章\n\n" + scene * 90 + "这一刻意义深远，却没人敢把原因说出口。", encoding="utf-8")

    gate = gate_check(project_config, chapter_number=1)
    artifact_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    prose_naturalness_report = (artifact_dir / "prose_naturalness_report.md").read_text(encoding="utf-8")

    assert not any(item["code"] == "prose_naturalness_inflated_significance" for item in gate.failures)
    assert "意义膨胀" in prose_naturalness_report
    assert not (artifact_dir / "repair_plan.md").exists()
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()


def test_slow_scene_without_dialogue_or_cliffhanger_is_not_a_deterministic_blocker():
    text = """# 第一章

林远沿着雨后的石阶慢慢下山，鞋底沾着祠堂前新翻的泥。

他在旧桥边停了一会儿，把昨夜记下的药名逐一同野草核对。

河水没有异象，只有上游漂来的松针贴着桥墩打转。

等雾气散开，他才发现背篓里的布包被潮气浸湿了一角。

那封没有寄出的家书仍在原处，墨迹却比清晨更淡了些。

他重新包好纸页，没有催促同行人，也没有为沉默寻找解释。

午后的山风吹干衣袖时，远处村庄已经升起平常的炊烟。

林远记住药草生长的位置，随后踏上回程，准备先救眼前的病人。
"""

    issues, _warnings = detect_prose_naturalness_issues(text)

    assert not any(str(issue.get("severity") or "").upper() in {"P0", "P1"} for issue in issues)


def test_expand_task_and_check_repair_short_chapter_without_pollution(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    mark_project_ready(project_config.path.parent, project_config, preserve_existing_characters=True)
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
    task_md = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")
    inventory_text = (root / task["internal_fact_inventory"]["path"]).read_text(encoding="utf-8")
    assert task["fact_inventory_summary"]["categories"]["methods"] >= 2
    assert "sample_extract" in inventory_text
    assert "current_style_profile.json" not in task_md


def test_gate_reports_style_drift_from_active_sample_profile_as_p2_signal(tmp_path):
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
    mark_project_ready(project_config.path.parent, project_config, preserve_existing_characters=True)
    long_sentence = (
        "Lin considered the geography of the gate, the unfinished debt, the weathered road, "
        "the council's older promises, the private fear behind every delayed answer, and the "
        "slow consequence of choosing silence over speech in a city that mistook hesitation for loyalty. "
    )
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\n" + long_sentence * 12, encoding="utf-8")

    result = gate_check(project_config, chapter_number=1)
    gate_payload = json.loads(
        (root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(
            encoding="utf-8"
        )
    )
    style_review = (root / "50_workbench" / "gate_artifacts" / "ch001" / "style_review.md").read_text(encoding="utf-8")

    assert not any(item["code"] == "style_drift" for item in result.failures)
    assert any("style_drift [P2]" in warning for warning in gate_payload["warnings"])
    assert "Active Style Baseline" in style_review
    assert "style drift from active sample profile" in style_review


def test_semantic_reader_pacing_review_writes_reader_experience_artifact(tmp_path):
    project_config = seed_project(tmp_path)
    mark_project_ready(project_config.path.parent, project_config)
    root = tmp_path / "novel"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nHe walked across the room. He waited. He stopped.\n", encoding="utf-8")

    result = pacing_review(project_config, chapter_number=1, semantic_reader=True)

    assert result.reader_experience_report.endswith("reader_experience_review.md")
    assert not any("ending hook" in issue for issue in result.issues)
    assert any("ending hook" in warning for warning in result.warnings)
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "reader_experience_review.md").exists()


def test_semantic_reader_recognizes_chinese_deadline_as_concrete_tail_pressure(tmp_path):
    project_config = seed_project(tmp_path)
    mark_project_ready(project_config.path.parent, project_config)
    root = tmp_path / "novel"
    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# 第一章\n\n他选择把旧卷列入公开追索。午时封库，距离共同签验只剩两个时辰。",
        encoding="utf-8",
    )

    result = pacing_review(project_config, chapter_number=1, semantic_reader=True)

    assert not any("ending hook" in issue for issue in result.issues)
    assert not any("ending hook" in warning for warning in result.warnings)


def test_gate_defers_repair_plan_until_review_barrier(tmp_path):
    project_config = seed_project(tmp_path)
    root = tmp_path / "novel"
    mark_project_ready(project_config.path.parent, project_config, preserve_existing_characters=True)
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
