from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_capability_gap_test_plan_is_fully_checked():
    checklist = (ROOT / "docs" / "NOVEL_SKILL_CAPABILITY_GAP_CHECKLIST.md").read_text(encoding="utf-8")
    test_plan = checklist.split("## Test Plan", 1)[1].split("## Definition of Done", 1)[0]

    assert "- [ ]" not in test_plan
    for term in (
        "CLI 测试",
        "端到端测试",
        "no-pollution",
        "Windows 兼容检查",
        "python -B -m pytest -q",
    ):
        assert term in test_plan


def test_test_plan_keeps_required_capability_coverage():
    tests_text = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "tests").glob("test_*.py")))

    for marker in (
        "test_cli_auto_write_plan_run_progress_report",
        "test_cli_creative_expand_task_and_check",
        "test_cli_creative_style_extract_json",
        "test_cli_creative_humanize_check_chinese_json",
        "test_cli_editorial_review_and_need_human_request",
        "test_editorial_batch_review_generates_editorial_team_health_reports",
        "test_e2e_agent_skill_no_api_key_full_chapter_lifecycle",
        "test_full_baseline_e2e_no_failed_pollution_and_rebuild",
        "conflict_thrill",
        "reverse_brake_report",
        "INBOXLEAK",
        "70_runtime\" / \"db",
    ):
        assert marker in tests_text


def test_windows_compatible_tests_do_not_hardcode_fixed_python_binary():
    checked: list[Path] = []
    for directory in (ROOT / "tests", ROOT / "scripts"):
        checked.extend(sorted(directory.glob("*.py")))

    forbidden = "python" + "3"
    for path in checked:
        text = path.read_text(encoding="utf-8")
        assert forbidden not in text, path
        if "subprocess.run(" in text:
            assert "sys.executable" in text, path
