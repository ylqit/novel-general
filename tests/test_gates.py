import json

from longform_engine.config import load_project_config
from longform_engine.gates import GateError, gate_check, pacing_review, record_waiver, repair_plan
from longform_engine.orchestration import continue_write, open_book, plan_chapter
from longform_engine.storage import init_project


def test_gate_check_writes_failed_schema_for_meta_pollution(tmp_path):
    project_config = seed_gate_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(
        "# 第一章\n\nTODO 写作说明：这里需要补剧情。\n",
        encoding="utf-8",
    )

    result = gate_check(project_config, chapter_number=1)
    payload = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))

    assert result.passed is False
    assert result.severity == "P0"
    assert payload["passed"] is False
    assert payload["failures"][0]["code"] == "meta_pollution"
    assert "repair_chapter" in payload["allowed_actions"]
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "repair_plan.md").exists()


def test_gate_check_passes_reasonable_draft(tmp_path):
    project_config = seed_gate_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    text = "# 第一章\n\n" + "林迟沿着山门前的石阶前行，旧钟声在远处回荡。他必须面对新的阻力，并在短暂犹豫后选择继续前进。" * 60
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(text, encoding="utf-8")

    result = gate_check(project_config, chapter_number=1)

    assert result.passed is True
    assert result.severity == "PASS"


def test_pacing_review_and_repair_plan(tmp_path):
    project_config = seed_gate_project(tmp_path)
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    text = "# 第一章\n\n" + "秘密 真相 决战 爆发 核心矛盾 全部 揭露。" * 20
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(text, encoding="utf-8")

    pacing = pacing_review(project_config, chapter_number=1)
    gate = gate_check(project_config, chapter_number=1)
    repair = repair_plan(project_config, chapter_number=1)

    assert pacing.tier == "fast"
    assert gate.passed is False
    assert repair.next_command == "repair-chapter --chapter 1"
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "pacing_review.md").exists()


def test_gate_event_matrix_blocks_cooldown_and_fast_quota(tmp_path):
    project_config = seed_gate_project(tmp_path)
    project_config.data["length"]["chapter_word_count"]["hard_min"] = 20
    project_config.data["pacing"]["fast_chapter_quota_per_volume"] = 1
    project_config.data["pacing"]["max_consecutive_fast_chapters"] = 1
    root = tmp_path / "novel"
    (root / "30_state" / "pacing_history.json").write_text(
        json.dumps(
            [{"chapter_number": 1, "tier": "fast", "event_types": ["conflict_thrill"]}],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    plan_chapter(project_config, chapter_number=2)
    draft = "# Chapter 2\n\n" + ("Ari enters the battle as the secret trap tightens. " * 30)
    (root / "40_manuscript" / "draft" / "ch002.md").write_text(draft, encoding="utf-8")

    result = gate_check(project_config, chapter_number=2)
    messages = " ".join(str(item.get("message", "")) for item in result.failures)

    assert result.passed is False
    assert "event_cooldown" in messages
    assert "fast_quota" in messages


def test_pacing_review_warns_when_soft_event_gap_persists(tmp_path):
    project_config = seed_gate_project(tmp_path)
    root = tmp_path / "novel"
    (root / "30_state" / "pacing_history.json").write_text(
        json.dumps(
            [
                {"chapter_number": 1, "tier": "fast", "event_types": ["conflict_thrill"]},
                {"chapter_number": 2, "tier": "fast", "event_types": ["tension_escalation"]},
                {"chapter_number": 3, "tier": "fast", "event_types": ["conflict_thrill"]},
                {"chapter_number": 4, "tier": "fast", "event_types": ["tension_escalation"]},
                {"chapter_number": 5, "tier": "fast", "event_types": ["conflict_thrill"]},
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    plan_chapter(project_config, chapter_number=6)
    draft = "# Chapter 6\n\n" + ("The battle pressure rises while the secret trap tightens. " * 12)
    (root / "40_manuscript" / "draft" / "ch006.md").write_text(draft, encoding="utf-8")

    result = pacing_review(project_config, chapter_number=6)

    assert any("soft event gap persists" in warning for warning in result.warnings)
    assert any("soft event required" in warning for warning in result.warnings)


def test_reverse_brake_blocks_complete_core_secret_reveal(tmp_path):
    project_config = seed_gate_project(tmp_path)
    project_config.data["length"]["chapter_word_count"]["hard_min"] = 20
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    draft = "# Chapter 1\n\n" + ("Ari states the final truth and the ultimate secret is revealed. " * 24)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(draft, encoding="utf-8")

    result = gate_check(project_config, chapter_number=1)
    payload = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))
    report = (root / "50_workbench" / "gate_artifacts" / "ch001" / "reverse_brake_report.md").read_text(encoding="utf-8")
    codes = {failure["code"] for failure in result.failures}

    assert result.passed is False
    assert "core_secret_complete_reveal" in codes
    assert payload["reverse_brake"]["summary"]["complete_reveal"] is True
    assert "complete_core_secret_reveal" in report


def test_reverse_brake_requires_tail_hook_when_anchor_demands_it(tmp_path):
    project_config = seed_gate_project(tmp_path)
    project_config.data["length"]["chapter_word_count"]["hard_min"] = 20
    root = tmp_path / "novel"
    (root / "20_outline" / "outline_anchors.json").write_text(
        json.dumps(
            [
                {
                    "chapter_number": 1,
                    "status": "rising",
                    "requires_tail_suspense": True,
                    "must_preserve_suspense": ["who controls the gate"],
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    plan_chapter(project_config, chapter_number=1)
    draft = "# Chapter 1\n\n" + ("Ari guards the gate and pays a small cost. The scene settles into quiet certainty. " * 20)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(draft, encoding="utf-8")

    result = gate_check(project_config, chapter_number=1)
    codes = {failure["code"] for failure in result.failures}

    assert result.passed is False
    assert "missing_tail_suspense" in codes
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "reverse_brake_report.md").exists()


def test_reverse_brake_reports_abc_quota_overflow(tmp_path):
    project_config = seed_gate_project(tmp_path)
    project_config.data["length"]["chapter_word_count"]["hard_min"] = 20
    project_config.data["pacing"]["max_major_quota_triggers_per_chapter"] = 1
    root = tmp_path / "novel"
    plan_chapter(project_config, chapter_number=1)
    draft = "# Chapter 1\n\n" + ("The core conflict, relationship betrayal, and secret truth all reveal at once. " * 18)
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(draft, encoding="utf-8")

    result = gate_check(project_config, chapter_number=1)
    codes = {failure["code"] for failure in result.failures}

    assert result.passed is False
    assert "plot_quota_overflow" in codes


def test_continue_write_generates_gate_artifacts(tmp_path):
    project_config = seed_gate_project(tmp_path, writing_mode="template_dry_run")
    open_book(project_config)

    result = continue_write(project_config, chapter_number=1)
    root = tmp_path / "novel"

    assert result.status.startswith("draft_ready_gate_")
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").exists()
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "consistency_report.md").exists()
    assert (root / "50_workbench" / "gate_artifacts" / "ch001" / "style_review.md").exists()


def test_gate_waiver_records_for_p2_and_refuses_p0(tmp_path):
    project_config = seed_gate_project(tmp_path)
    root = tmp_path / "novel"
    artifact_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    gate_path = artifact_dir / "gate_result.json"
    gate_path.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "passed": False,
                "severity": "P2",
                "failures": [{"code": "style_warning", "severity": "P2", "message": "可人工确认的风格警告。"}],
                "warnings": [],
                "allowed_actions": ["human_review"],
                "next_command": "gate-waiver --chapter 1",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    waiver = record_waiver(project_config, chapter_number=1, reason="风格警告可接受", approved_by="tester")
    payload = json.loads(gate_path.read_text(encoding="utf-8"))

    assert waiver.allowed is True
    assert (artifact_dir / "waiver.json").exists()
    assert payload["waived"] is True
    assert payload["waiver"]["approved_by"] == "tester"
    assert "continue_write_with_waiver" in payload["allowed_actions"]

    gate_path.write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "passed": False,
                "severity": "P0",
                "failures": [{"code": "meta_pollution", "severity": "P0", "message": "TODO"}],
                "allowed_actions": ["repair_chapter"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    try:
        record_waiver(project_config, chapter_number=1, reason="不应允许", approved_by="tester")
    except GateError as exc:
        assert "Cannot waive blocking severity P0" in str(exc)
    else:
        raise AssertionError("Expected GateError")


def seed_gate_project(tmp_path, *, writing_mode: str = "agent_skill"):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    return load_project_config(project.project_config, cli_overrides={"writing": {"mode": writing_mode}})
