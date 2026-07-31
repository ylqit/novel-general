import json
import os
import subprocess
import sys
from pathlib import Path

from longform_engine.agent_tasks import build_manifest, update_task_status, write_manifest
from longform_engine.config import load_project_config
from longform_engine.db import query_table
from tests.project_fixtures import mark_project_ready


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_KEY_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "LLM_API_KEY",
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUTF8"] = "1"
    for key in PROVIDER_KEY_NAMES:
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, "-m", "longform_engine.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def create_open_project(tmp_path: Path) -> tuple[Path, Path]:
    project_dir = tmp_path / "book"
    project_yaml = project_dir / "project.yaml"
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(project_dir))
    assert init.returncode == 0, init.stderr
    open_book = run_cli("open-book", str(project_yaml))
    assert open_book.returncode == 0, open_book.stderr
    mark_project_ready(project_dir, load_project_config(project_yaml))
    return project_dir, project_yaml


def test_empty_project_requires_open_book_then_book_ideation(tmp_path):
    project_dir = tmp_path / "empty-book"
    project_yaml = project_dir / "project.yaml"
    initialized = run_cli("init-project", "--template", "qidian-longform", "--output", str(project_dir))
    assert initialized.returncode == 0, initialized.stderr

    before_open = json.loads(run_cli("production", "next", str(project_yaml), "--json").stdout)
    assert before_open["status"] == "ready_for_open_book"
    assert before_open["next_command"] == "longform-engine open-book project.yaml"

    opened = run_cli("open-book", str(project_yaml))
    assert opened.returncode == 0, opened.stderr
    after_open = json.loads(run_cli("production", "next", str(project_yaml), "--json").stdout)
    assert after_open["status"] == "ready_for_intelligence_task"
    assert after_open["task_type"] == "book_ideation"

    loop = run_cli("production", "loop", str(project_yaml), "--max-steps", "1", "--json")
    assert loop.returncode == 0, loop.stdout + loop.stderr
    loop_payload = json.loads(loop.stdout)
    assert loop_payload["steps"][0]["action"] == "intelligence_task"
    assert loop_payload["steps"][0]["task_type"] == "book_ideation"
    assert not (project_dir / "40_manuscript" / "draft" / "ch001.md").exists()


def test_production_status_json_contract_for_gui_api(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    assert continue_write.returncode == 0, continue_write.stderr

    result = run_cli("production", "status", str(project_yaml), "--json")
    text_result = run_cli("production", "status", str(project_yaml))

    assert result.returncode == 0, result.stderr
    assert text_result.returncode == 0, text_result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["status_version"] == "production_status_v1"
    assert payload["read_only"] is True
    assert payload["path_style"] == "project_relative"
    assert payload["command_style"] == "longform-engine"
    assert payload["redaction"] == {
        "no_chapter_body": True,
        "no_api_keys": True,
        "no_full_prompt_logs": True,
    }
    assert payload["current"]["next_status"] == "agent_task_awaiting_agent"
    assert payload["current"]["next_command"].startswith("longform-engine draft submit ")
    assert payload["next_action"]["allowed_output_paths"] == ["50_workbench/agent_drafts/ch001.codex.md"]
    assert payload["agent_tasks"]["tasks"] >= 1
    assert payload["agent_tasks"]["event_file"] == "50_workbench/agent_tasks/events.jsonl"
    assert payload["board"]["board_version"] == "production_board_v1"
    assert payload["resources"]["production_status"] == "GET /production/status"
    assert payload["resources"]["production_loop"] == "POST /production/loop"
    assert "OK: production status ready" in text_result.stdout
    assert_json_contract_safe(payload, project_dir)
    assert_copyable_commands(payload)


def test_production_next_returns_continue_write_when_no_blocker(tmp_path):
    _, project_yaml = create_open_project(tmp_path)

    result = run_cli("production", "next", str(project_yaml), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["status"] == "ready_for_continue_write"
    assert payload["chapter_number"] == 1
    assert payload["blocked_by"] == "none"
    assert payload["waiting_for"] == "cli"
    assert payload["next_command"] == "longform-engine continue-write project.yaml --chapter 1"
    assert payload["input_files"] == []
    assert payload["allowed_output_paths"] == []


def test_production_next_reports_agent_task_contract_after_continue_write(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    assert continue_write.returncode == 0, continue_write.stderr

    result = run_cli("production", "next", str(project_yaml), "--json")
    text_result = run_cli("production", "next", str(project_yaml))

    assert result.returncode == 0, result.stderr
    assert text_result.returncode == 0, text_result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "agent_task_awaiting_agent"
    assert payload["chapter_number"] == 1
    assert payload["blocked_by"] == "agent_task"
    assert payload["waiting_for"] == "agent_draft"
    assert payload["task_type"] == "chapter_write"
    assert payload["output_schema"] == "markdown_chapter_only"
    assert "50_workbench/writing_tasks/ch001.json" not in payload["input_files"]
    assert "50_workbench/writing_tasks/ch001.md" in payload["input_files"]
    assert len(payload["input_files"]) <= 7
    assert payload["context_policy"]["max_chars"] == 20000
    assert payload["allowed_output_paths"] == ["50_workbench/agent_drafts/ch001.codex.md"]
    assert payload["validate_command"].startswith("longform-engine draft submit ")
    assert payload["apply_command"] == "longform-engine chapter finalize project.yaml --chapter 1 --approved-by human"
    assert payload["failure_next_command"] == "longform-engine repair-chapter project.yaml --chapter 1 --plan-only"
    assert len((project_dir / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")) <= 20000
    assert payload["next_command"] == payload["validate_command"]
    assert {"no final", "no rag", "no graph direct", "no sqlite direct"}.issubset(set(payload["hard_boundaries"]))
    assert "OK: production next action ready" in text_result.stdout
    assert "Next command: longform-engine draft submit" in text_result.stdout
    assert "Allowed outputs:" in text_result.stdout


def test_agent_task_brief_renders_work_order_without_mutation(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    assert continue_write.returncode == 0, continue_write.stderr
    manifest_file = project_dir / "50_workbench" / "writing_tasks" / "ch001.agent_task.json"
    index_file = project_dir / "50_workbench" / "agent_tasks" / "agent_task_index.json"
    manifest_before = manifest_file.read_text(encoding="utf-8")
    index_before = index_file.read_text(encoding="utf-8")

    result = run_cli("agent-task", "brief", str(project_yaml), "chapter_write:ch001:v1", "--json")
    text_result = run_cli("agent-task", "brief", str(project_yaml), "chapter_write:ch001:v1")

    assert result.returncode == 0, result.stderr
    assert text_result.returncode == 0, text_result.stderr
    payload = json.loads(result.stdout)
    assert payload["renderer"] == "agent_task_brief_v1"
    assert payload["read_only"] is True
    assert payload["task_id"] == "chapter_write:ch001:v1"
    assert payload["task_type"] == "chapter_write"
    assert payload["chapter_number"] == 1
    assert payload["status"] == "awaiting_agent"
    assert payload["input_files"]
    assert payload["allowed_output_paths"] == ["50_workbench/agent_drafts/ch001.codex.md"]
    assert payload["output_schema"] == "markdown_chapter_only"
    assert payload["validate_command"].startswith("longform-engine draft submit ")
    assert payload["apply_command"] == "longform-engine chapter finalize project.yaml --chapter 1 --approved-by human"
    assert payload["failure_next_command"] == "longform-engine repair-chapter project.yaml --chapter 1 --plan-only"
    assert {"no final", "no rag", "no graph direct", "no sqlite direct"}.issubset(set(payload["hard_boundaries"]))
    assert payload["agent_role"].startswith("Chapter author")
    assert payload["output_guidance"].startswith("Write Markdown chapter prose")
    assert "Read only the manifest input_files" in payload["context_budget_rules"][0]
    assert "40_manuscript/final/" in payload["forbidden_paths"]
    assert payload["manifest_validation"]["ok"] is True
    assert "# Agent Work Order: chapter_write:ch001:v1" in payload["work_order_markdown"]
    assert "## Role And Goal" in payload["work_order_markdown"]
    assert "## Context Budget" in payload["work_order_markdown"]
    assert "## Required Input Files" in payload["work_order_markdown"]
    assert "## Optional Input Files" in payload["work_order_markdown"]
    assert "## Allowed Output Paths" in payload["work_order_markdown"]
    assert "Validate command:" in payload["work_order_markdown"]
    assert "## Forbidden Direct Writes" in payload["work_order_markdown"]
    assert "## Completion Report" in payload["work_order_markdown"]
    assert text_result.stdout == payload["work_order_markdown"]
    assert manifest_file.read_text(encoding="utf-8") == manifest_before
    assert index_file.read_text(encoding="utf-8") == index_before


def test_agent_task_brief_supports_all_manifest_task_types(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    specs = agent_task_manifest_specs()
    for task_type, spec in specs.items():
        manifest = build_manifest(
            project_dir,
            task_type=task_type,
            chapter_number=1,
            input_files=[project_dir / "project.yaml"],
            allowed_output_paths=[project_dir / spec["output"]],
            output_schema=spec["schema"],
            validate_command=spec["validate"],
            apply_command=spec["apply"],
            failure_next_command=spec["failure"],
        )
        write_manifest(
            project_dir,
            manifest,
            project_dir / "50_workbench" / "agent_tasks" / f"{task_type}.agent_task.json",
        )

    for task_type, spec in specs.items():
        result = run_cli("agent-task", "brief", str(project_yaml), f"{task_type}:ch001:v1", "--json")

        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["task_type"] == task_type
        assert payload["allowed_output_paths"] == [spec["output"]]
        assert payload["output_schema"] == spec["schema"]
        assert payload["manifest_validation"]["ok"] is True
        assert payload["work_scope"]
        assert payload["agent_role"]
        assert payload["output_guidance"]
        assert "## Hard Boundaries" in payload["work_order_markdown"]


def test_production_json_contract_uses_relative_paths_and_redacts_body(tmp_path):
    body_marker = "GUIAPICONTRACT_BODY_SHOULD_NOT_LEAK"
    project_dir, project_yaml = create_open_project(tmp_path / "draft_case")
    draft_dir = project_dir / "40_manuscript" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "ch001.md").write_text(f"# Chapter 1\n\n{body_marker}\n", encoding="utf-8")

    next_result = run_cli("production", "next", str(project_yaml), "--json")
    board_result = run_cli("production", "board", str(project_yaml), "--from", "1", "--to", "1", "--json")
    loop_result = run_cli("production", "loop", str(project_yaml), "--max-steps", "1", "--json")
    assert next_result.returncode == 0, next_result.stdout + next_result.stderr
    assert board_result.returncode == 0, board_result.stdout + board_result.stderr
    assert loop_result.returncode == 0, loop_result.stdout + loop_result.stderr
    next_payload = json.loads(next_result.stdout)
    board_payload = json.loads(board_result.stdout)
    loop_payload = json.loads(loop_result.stdout)

    for payload in (next_payload, board_payload, loop_payload):
        assert_json_contract_safe(payload, project_dir, forbidden_markers=(body_marker,))
        assert_copyable_commands(payload)

    task_project_dir, task_project_yaml = create_open_project(tmp_path / "task_case")
    loop = run_cli("production", "loop", str(task_project_yaml), "--max-steps", "1", "--json")
    assert loop.returncode == 0, loop.stdout + loop.stderr
    loop_payload = json.loads(loop.stdout)
    brief = run_cli("agent-task", "brief", str(task_project_yaml), "chapter_write:ch001:v1", "--json")
    assert brief.returncode == 0, brief.stdout + brief.stderr
    brief_payload = json.loads(brief.stdout)

    for payload in (loop_payload, brief_payload):
        assert_json_contract_safe(payload, task_project_dir)
        assert_copyable_commands(payload)
    assert "writing_tasks/ch001.json" in json.dumps(loop_payload, ensure_ascii=False)
    assert "D:" not in json.dumps(loop_payload, ensure_ascii=False)


def test_production_board_summarizes_chapter_lanes_and_reports(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    assert continue_write.returncode == 0, continue_write.stderr
    draft_file = project_dir / "40_manuscript" / "draft" / "ch001.md"
    draft_file.write_text("# Chapter 1\n\nDraft that still needs repair.\n", encoding="utf-8")
    gate_dir = project_dir / "50_workbench" / "gate_artifacts" / "ch001"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "gate_result.json").write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "passed": False,
                "severity": "P1",
                "failures": [{"code": "pacing", "severity": "P1", "message": "too flat"}],
                "warnings": [{"code": "style", "message": "watch rhythm"}],
                "next_command": "repair-chapter --chapter 1 --plan-only",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    for task_type, spec in agent_task_manifest_specs().items():
        if task_type == "chapter_write":
            continue
        manifest = build_manifest(
            project_dir,
            task_type=task_type,
            chapter_number=1,
            input_files=[project_dir / "project.yaml"],
            allowed_output_paths=[project_dir / spec["output"]],
            output_schema=spec["schema"],
            validate_command=spec["validate"],
            apply_command=spec["apply"],
            failure_next_command=spec["failure"],
        )
        write_manifest(
            project_dir,
            manifest,
            project_dir / "50_workbench" / "agent_tasks" / f"{task_type}.board.agent_task.json",
        )
    aggregate_dir = project_dir / "50_workbench" / "editorial_reviews"
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    (aggregate_dir / "ch001.aggregate.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "expected_roles": ["serial_verifier", "anti_ai_editor"],
                "accepted_roles": ["serial_verifier"],
                "missing_roles": ["anti_ai_editor"],
                "duplicate_role_results": [{"role_id": "serial_verifier", "files": ["a.json", "b.json"]}],
                "invalid_results": [{"role_id": "anti_ai_editor", "error": "bad schema"}],
                "result_count": 1,
                "severity_counts": {"P0": 0, "P1": 1, "P2": 0},
                "unresolved_items": 1,
                "conditional_passes": 0,
                "need_human": True,
                "need_human_reasons": ["missing_editorial_roles", "invalid_role_results"],
                "next_command": "longform-engine editorial need-human project.yaml --chapter 1 --reason editorial_aggregate",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    transactions = project_dir / "70_runtime" / "transactions"
    transactions.mkdir(parents=True, exist_ok=True)
    (transactions / "20260630_chapter_finalize_ch001.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "report_type": "canonical_write_transaction_report",
                "status": "applied",
                "command": "chapter finalize",
                "chapter_number": 1,
                "created_at": "2026-06-30T00:00:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    run_reports = project_dir / "70_runtime" / "run_reports"
    run_reports.mkdir(parents=True, exist_ok=True)
    (run_reports / "draft_submit_ch001.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "status": "submitted",
                "last_pipeline": "draft submit",
                "updated_at": "2026-06-30T00:00:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_cli("production", "board", str(project_yaml), "--from", "1", "--to", "1", "--json")
    text_result = run_cli("production", "board", str(project_yaml), "--from", "1", "--to", "1")
    editorial_text = run_cli("production", "board", str(project_yaml), "--from", "1", "--to", "1", "--editorial")

    assert result.returncode == 0, result.stderr
    assert text_result.returncode == 0, text_result.stderr
    assert editorial_text.returncode == 0, editorial_text.stderr
    payload = json.loads(result.stdout)
    row = payload["chapters"][0]
    assert payload["board_version"] == "production_board_v1"
    assert payload["read_only"] is True
    assert row["draft_status"] == "gate_failed"
    assert row["final_status"] == "missing"
    assert row["gate_status"]["status"] == "failed"
    assert row["gate_status"]["severity"] == "P1"
    assert row["gate_status"]["next_command"] == "longform-engine repair-chapter --chapter 1 --plan-only"
    assert row["repair_status"]["status"] == "awaiting_agent"
    assert row["humanize_status"]["status"] == "awaiting_agent"
    assert row["expand_status"]["status"] == "awaiting_agent"
    assert row["graph_status"]["status"] == "awaiting_agent"
    assert row["memory_status"]["status"] == "awaiting_agent"
    assert row["character_memory_status"]["status"] == "awaiting_agent"
    assert row["semantic_pacing_status"]["status"] == "awaiting_agent"
    assert row["editorial"]["status"] == "need_human"
    assert row["editorial"]["expected_roles"] == ["serial_verifier", "anti_ai_editor"]
    assert row["editorial"]["accepted_roles"] == ["serial_verifier"]
    assert row["editorial"]["missing_roles"] == ["anti_ai_editor"]
    assert row["editorial"]["duplicate_role_results"][0]["role_id"] == "serial_verifier"
    assert row["editorial"]["invalid_results"][0]["role_id"] == "anti_ai_editor"
    assert row["editorial"]["severity_counts"]["P1"] == 1
    assert row["editorial"]["role_statuses"]
    assert any(role["role_id"] == "serial_verifier" and role["status"] == "accepted" for role in row["editorial"]["role_statuses"])
    assert any(role["role_id"] == "anti_ai_editor" and role["status"] == "invalid" for role in row["editorial"]["role_statuses"])
    assert row["latest_transaction"]["command"] == "chapter finalize"
    assert row["latest_report"]["command"] == "draft submit"
    assert payload["totals"]["gate_failed"] == 1
    assert payload["totals"]["need_human"] == 1
    assert payload["totals"]["active_agent_tasks"] >= 8
    assert "OK: production board ready" in text_result.stdout
    assert "ch001 draft=gate_failed final=missing gate=failed" in text_result.stdout
    assert "editorial=need_human need_human=True" in text_result.stdout
    assert "expected_roles=serial_verifier, anti_ai_editor" in editorial_text.stdout
    assert "missing_roles=anti_ai_editor" in editorial_text.stdout
    assert "duplicate_role_results=1" in editorial_text.stdout
    assert "invalid_results=1" in editorial_text.stdout
    assert "role=serial_verifier status=accepted" in editorial_text.stdout


def test_production_fixture_matrix_covers_blocking_states(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    draft_dir = project_dir / "40_manuscript" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    gate_root = project_dir / "50_workbench" / "gate_artifacts"

    # ch001: gate failed.
    (draft_dir / "ch001.md").write_text("# Chapter 1\n\nGate failed fixture.\n", encoding="utf-8")
    ch001_gate = gate_root / "ch001"
    ch001_gate.mkdir(parents=True, exist_ok=True)
    (ch001_gate / "gate_result.json").write_text(
        json.dumps(
            {
                "chapter_number": 1,
                "passed": False,
                "severity": "P1",
                "failures": [{"code": "fixture_gate_failed", "severity": "P1", "message": "fixture"}],
                "warnings": [],
                "next_command": "longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # ch002: awaiting repair candidate.
    write_fixture_manifest(
        project_dir,
        task_type="repair",
        chapter_number=2,
        output="50_workbench/repair_candidates/ch002.codex.repair_candidate.md",
        schema="markdown_repair_candidate",
        validate=(
            "longform-engine draft submit project.yaml --chapter 2 "
            "--file 50_workbench/repair_candidates/ch002.codex.repair_candidate.md --agent codex --overwrite"
        ),
        apply="longform-engine chapter finalize project.yaml --chapter 2 --approved-by human",
        failure="longform-engine editorial need-human project.yaml --chapter 2 --reason repair_failed",
    )

    # ch003: awaiting semantic outputs.
    write_fixture_manifest(
        project_dir,
        task_type="pacing_review",
        chapter_number=3,
        output="50_workbench/gate_artifacts/ch003/semantic_pacing_result.json",
        schema="semantic_pacing_result_v1",
        validate=(
            "longform-engine pacing semantic-validate project.yaml --chapter 3 "
            "--file 50_workbench/gate_artifacts/ch003/semantic_pacing_result.json"
        ),
        apply=(
            "longform-engine pacing semantic-apply project.yaml --chapter 3 "
            "--file 50_workbench/gate_artifacts/ch003/semantic_pacing_result.json"
        ),
        failure="longform-engine pacing semantic-task project.yaml --chapter 3",
    )
    write_fixture_manifest(
        project_dir,
        task_type="graph_extract",
        chapter_number=3,
        output="50_workbench/graph_updates/ch003.semantic_graph.json",
        schema="semantic_graph_update_v1",
        validate=(
            "longform-engine graph semantic-validate project.yaml --chapter 3 "
            "--file 50_workbench/graph_updates/ch003.semantic_graph.json"
        ),
        apply=(
            "longform-engine graph semantic-apply project.yaml --chapter 3 "
            "--file 50_workbench/graph_updates/ch003.semantic_graph.json"
        ),
        failure="longform-engine graph semantic-task project.yaml --chapter 3",
    )

    # ch004: awaiting editorial role result.
    write_fixture_manifest(
        project_dir,
        task_type="editorial_review",
        chapter_number=4,
        output="50_workbench/editorial_reviews/results/ch004.serial_verifier.json",
        schema="editorial_role_review_v1",
        validate=(
            "longform-engine editorial submit-review project.yaml --chapter 4 --role serial_verifier "
            "--file 50_workbench/editorial_reviews/results/ch004.serial_verifier.json"
        ),
        apply="longform-engine editorial aggregate project.yaml --chapter 4",
        failure="longform-engine editorial need-human project.yaml --chapter 4 --reason editorial_failed",
        task_id="editorial_review:serial_verifier:ch004:v1",
    )

    # ch005: awaiting human finalize after passed gate.
    (draft_dir / "ch005.md").write_text("# Chapter 5\n\nGate passed fixture.\n", encoding="utf-8")
    ch005_gate = gate_root / "ch005"
    ch005_gate.mkdir(parents=True, exist_ok=True)
    (ch005_gate / "gate_result.json").write_text(
        json.dumps(
            {
                "chapter_number": 5,
                "passed": True,
                "severity": "PASS",
                "failures": [],
                "warnings": [],
                "next_command": "longform-engine chapter finalize project.yaml --chapter 5 --approved-by human",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_cli("production", "board", str(project_yaml), "--from", "1", "--to", "5", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    rows = {row["chapter_number"]: row for row in payload["chapters"]}
    assert rows[1]["gate_status"]["status"] == "failed"
    assert rows[1]["draft_status"] == "gate_failed"
    assert rows[2]["repair_status"]["status"] == "awaiting_agent"
    assert rows[3]["semantic_pacing_status"]["status"] == "awaiting_agent"
    assert rows[3]["graph_status"]["status"] == "awaiting_agent"
    assert rows[4]["editorial"]["status"] == "awaiting_results"
    assert rows[4]["editorial"]["expected_roles"] == ["serial_verifier"]
    assert rows[4]["editorial"]["missing_roles"] == ["serial_verifier"]
    assert rows[5]["gate_status"]["status"] == "passed"
    assert rows[5]["draft_status"] == "gate_passed"
    assert rows[5]["final_status"] == "missing"
    assert payload["totals"]["gate_failed"] == 1
    assert payload["totals"]["active_agent_tasks"] >= 4


def test_production_next_prioritizes_need_human(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    aggregate_dir = project_dir / "50_workbench" / "editorial_reviews"
    aggregate_dir.mkdir(parents=True, exist_ok=True)
    aggregate_file = aggregate_dir / "ch001.aggregate.json"
    aggregate_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "expected_roles": ["serial_verifier", "anti_ai_editor"],
                "accepted_roles": ["serial_verifier"],
                "missing_roles": ["anti_ai_editor"],
                "duplicate_role_results": [],
                "invalid_results": [],
                "severity_counts": {"P0": 0, "P1": 1, "P2": 0},
                "conditional_passes": 0,
                "result_count": 1,
                "need_human": True,
                "need_human_reasons": ["unresolved_P1"],
                "next_command": "longform-engine editorial need-human project.yaml --chapter 1 --reason unresolved_P1",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = run_cli("production", "next", str(project_yaml), "--json")
    text_result = run_cli("production", "next", str(project_yaml), "--editorial")

    assert result.returncode == 0, result.stderr
    assert text_result.returncode == 0, text_result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "need_human"
    assert payload["chapter_number"] == 1
    assert payload["blocked_by"] == "editorial_need_human"
    assert payload["waiting_for"] == "human_review"
    assert payload["need_human_reasons"] == ["unresolved_P1"]
    assert payload["need_human_reasons_readable"][0]["message"].startswith("Unresolved P1")
    assert payload["expected_roles"] == ["serial_verifier", "anti_ai_editor"]
    assert payload["missing_roles"] == ["anti_ai_editor"]
    assert payload["next_command"] == "longform-engine editorial need-human project.yaml --chapter 1 --reason unresolved_P1"
    assert payload["sources"] == ["50_workbench/editorial_reviews/ch001.aggregate.json"]
    assert "Readable need-human reasons:" in text_result.stdout
    assert "unresolved_P1: Unresolved P1" in text_result.stdout
    assert "Missing roles: anti_ai_editor" in text_result.stdout


def test_production_next_points_to_finalize_after_validated_draft(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    assert continue_write.returncode == 0, continue_write.stderr
    agent_draft = project_dir / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_agent_draft("PRODUCTIONNEXTFINALIZE"), encoding="utf-8")
    submit = run_cli(
        "draft",
        "submit",
        str(project_yaml),
        "--chapter",
        "1",
        "--file",
        str(agent_draft),
        "--agent",
        "codex",
    )
    assert submit.returncode == 0, submit.stdout + submit.stderr

    result = run_cli("production", "next", str(project_yaml), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "agent_task_validated"
    assert payload["waiting_for"] == "apply_command"
    assert payload["apply_command"] == "longform-engine chapter finalize project.yaml --chapter 1 --approved-by human"
    assert payload["next_command"] == payload["apply_command"]
    assert payload["allowed_output_paths"] == ["50_workbench/agent_drafts/ch001.codex.md"]


def test_production_next_editorial_task_includes_role_specific_work_order(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    draft = project_dir / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text(
        "# Chapter 1\n\nAri checks the north gate clue while the caravan waits for an editorial pass.\n",
        encoding="utf-8",
    )
    review = run_cli("editorial", "review", str(project_yaml), "--chapter", "1", "--json")
    assert review.returncode in {0, 1}, review.stdout + review.stderr

    result = run_cli("production", "next", str(project_yaml), "--json")
    text_result = run_cli("production", "next", str(project_yaml), "--editorial")

    assert result.returncode == 0, result.stderr
    assert text_result.returncode == 0, text_result.stderr
    payload = json.loads(result.stdout)
    role = payload["editorial_role"]
    assert payload["status"] == "agent_task_awaiting_agent"
    assert payload["task_type"] == "editorial_review"
    assert payload["role_id"] == role["role_id"]
    assert role["role_id"] in {
        "planning_chief_editor",
        "writing_agent",
        "anti_ai_editor",
        "serial_verifier",
        "executive_editor",
    }
    assert role["display_name"]
    assert role["focus"]
    assert role["work_order_file"].startswith("50_workbench/editorial_reviews/agent_tasks/ch001/")
    assert role["result_file"].startswith("50_workbench/editorial_reviews/results/ch001.")
    assert role["output_schema"] == "editorial_role_review_v2"
    assert role["context_file"].endswith(".context.json")
    assert role["reviewer_instance_id"]
    assert len(role["context_digest_hash"]) == 64
    assert role["validate_command"].startswith("longform-engine editorial submit-review ")
    assert role["apply_command"] == "longform-engine editorial aggregate project.yaml --chapter 1"
    assert "no final" in role["hard_boundaries"]
    assert "Editorial role:" in text_result.stdout
    assert "Work order: 50_workbench/editorial_reviews/agent_tasks/ch001/" in text_result.stdout
    assert "Validate: longform-engine editorial submit-review" in text_result.stdout


def test_production_loop_generates_writing_task_and_pauses_for_agent(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)

    result = run_cli("production", "loop", str(project_yaml), "--max-steps", "3", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["loop_version"] == "production_loop_v1"
    assert payload["status"] == "paused"
    assert payload["pause_reason"] == "awaiting_agent_output"
    assert payload["steps_executed"] == 1
    assert payload["steps"][0]["action"] == "continue_write"
    assert payload["next_action"]["status"] == "agent_task_awaiting_agent"
    assert (project_dir / "50_workbench" / "writing_tasks" / "ch001.json").exists()
    assert (project_dir / "50_workbench" / "writing_tasks" / "ch001.agent_task.json").exists()
    assert not (project_dir / "40_manuscript" / "final" / "ch001.md").exists()


def test_production_loop_submits_existing_agent_draft_runs_gate_and_pauses_for_finalize(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    assert continue_write.returncode == 0, continue_write.stderr
    agent_draft = project_dir / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_agent_draft("PRODUCTIONLOOPSUBMIT"), encoding="utf-8")

    result = run_cli("production", "loop", str(project_yaml), "--max-steps", "3", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "paused"
    assert payload["pause_reason"] == "apply_or_finalize_required"
    assert payload["steps_executed"] == 1
    assert payload["steps"][0]["action"] == "draft_submit_existing_agent_output"
    assert payload["next_action"]["status"] == "agent_task_validated"
    assert (project_dir / "40_manuscript" / "draft" / "ch001.md").exists()
    assert (project_dir / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").exists()
    assert not (project_dir / "40_manuscript" / "final" / "ch001.md").exists()


def test_production_loop_no_pollution_pause_path(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    assert continue_write.returncode == 0, continue_write.stderr
    graph_path = project_dir / "30_state" / "story_graph.json"
    graph_before = graph_path.read_text(encoding="utf-8")
    agent_draft = project_dir / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_agent_draft("PRODUCTIONLOOP_NOPOLLUTION"), encoding="utf-8")

    result = run_cli("production", "loop", str(project_yaml), "--max-steps", "3", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    project_config = load_project_config(project_yaml)
    chapters = query_table(project_config, "chapters", limit=20)
    chunks = query_table(project_config, "chapter_chunks", limit=20)
    events = query_table(project_config, "events", limit=20)
    mentions = query_table(project_config, "entity_mentions", limit=20)
    assert payload["pause_reason"] == "apply_or_finalize_required"
    assert payload["next_action"]["status"] == "agent_task_validated"
    assert not (project_dir / "40_manuscript" / "final" / "ch001.md").exists()
    assert graph_path.read_text(encoding="utf-8") == graph_before
    assert not any((project_dir / "60_rag" / "chunks").glob("ch001*.json"))
    assert not any(row.get("status") == "final" for row in chapters)
    assert chunks == []
    assert events == []
    assert mentions == []


def test_production_loop_runs_gate_for_existing_draft_and_pauses_on_failure(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    draft_dir = project_dir / "40_manuscript" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / "ch001.md").write_text("# Chapter 1\n\nToo short.\n", encoding="utf-8")

    result = run_cli("production", "loop", str(project_yaml), "--max-steps", "2", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "paused"
    assert payload["pause_reason"] == "gate_failed"
    assert payload["steps_executed"] == 1
    assert payload["steps"][0]["action"] == "gate_check"
    gate_file = project_dir / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json"
    assert gate_file.exists()
    assert json.loads(gate_file.read_text(encoding="utf-8"))["passed"] is False
    assert not (project_dir / "40_manuscript" / "final" / "ch001.md").exists()


def test_production_loop_aggregates_validated_editorial_task_and_pauses_need_human(tmp_path):
    project_dir, project_yaml = create_open_project(tmp_path)
    result_dir = project_dir / "50_workbench" / "editorial_reviews" / "results"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "ch001.serial_verifier.normalized.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "role_id": "serial_verifier",
                "verdict": "pass",
                "items": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (project_dir / "50_workbench" / "editorial_reviews" / "ch001.review.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "chapter_number": 1,
                "editorial_team": [
                    {"id": "serial_verifier"},
                    {"id": "writing_agent"},
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    spec = agent_task_manifest_specs()["editorial_review"]
    manifest = build_manifest(
        project_dir,
        task_type="editorial_review",
        chapter_number=1,
        input_files=[project_dir / "project.yaml"],
        allowed_output_paths=[project_dir / spec["output"]],
        output_schema=spec["schema"],
        validate_command=spec["validate"],
        apply_command=spec["apply"],
        failure_next_command=spec["failure"],
    )
    write_manifest(
        project_dir,
        manifest,
        project_dir / "50_workbench" / "editorial_reviews" / "agent_tasks" / "ch001.serial_verifier.agent_task.json",
    )
    update_task_status(
        project_dir,
        "editorial_review:ch001:v1",
        to_status="validated",
        command="test fixture",
    )

    result = run_cli("production", "loop", str(project_yaml), "--max-steps", "3", "--json")

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "paused"
    assert payload["pause_reason"] == "need_human"
    assert payload["steps_executed"] == 1
    assert payload["steps"][0]["action"] == "editorial_aggregate"
    aggregate_file = project_dir / "50_workbench" / "editorial_reviews" / "ch001.aggregate.json"
    aggregate = json.loads(aggregate_file.read_text(encoding="utf-8"))
    assert aggregate["accepted_roles"] == ["serial_verifier"]
    assert aggregate["need_human"] is True
    assert not (project_dir / "40_manuscript" / "final" / "ch001.md").exists()


def assert_json_contract_safe(
    payload: dict,
    project_dir: Path,
    *,
    forbidden_markers: tuple[str, ...] = (),
) -> None:
    strings = list(iter_json_strings(payload))
    root_variants = {
        str(project_dir),
        project_dir.as_posix(),
        str(project_dir).replace("\\", "/"),
    }
    forbidden = (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "DEEPSEEK_API_KEY",
        "OPENROUTER_API_KEY",
        "LLM_API_KEY",
        *forbidden_markers,
    )
    for value in strings:
        assert not any(variant and variant in value for variant in root_variants), value
        assert not any(marker and marker in value for marker in forbidden), value


def write_fixture_manifest(
    project_dir: Path,
    *,
    task_type: str,
    chapter_number: int,
    output: str,
    schema: str,
    validate: str,
    apply: str,
    failure: str,
    task_id: str | None = None,
) -> None:
    manifest = build_manifest(
        project_dir,
        task_type=task_type,
        chapter_number=chapter_number,
        input_files=[project_dir / "project.yaml"],
        allowed_output_paths=[project_dir / output],
        output_schema=schema,
        validate_command=validate,
        apply_command=apply,
        failure_next_command=failure,
        task_id=task_id,
    )
    write_manifest(
        project_dir,
        manifest,
        project_dir / "50_workbench" / "agent_tasks" / f"ch{chapter_number:03d}.{task_type}.fixture.agent_task.json",
    )


def assert_copyable_commands(payload: dict) -> None:
    command_keys = {"next_command", "validate_command", "apply_command", "failure_next_command"}
    for path, value in iter_json_items(payload):
        if not isinstance(value, str) or not value.strip():
            continue
        key = path[-1] if path else ""
        is_step_command = key == "command" and "steps" in path
        if key in command_keys or is_step_command:
            assert value.startswith("longform-engine "), (path, value)


def iter_json_strings(value):
    for _, item in iter_json_items(value):
        if isinstance(item, str):
            yield item


def iter_json_items(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from iter_json_items(item, (*path, str(key)))
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_json_items(item, (*path, str(index)))
        return
    yield path, value


def passing_agent_draft(marker: str) -> str:
    sentence = (
        f"{marker} Ari climbs toward North Gate while the caravan waits below; "
        "she chooses the harder road, protects the witness, keeps the promise, "
        "and turns the local conflict into a sharper chapter hook. "
    )
    return "# Chapter 1: North Gate\n\n" + sentence * 22 + "\n\nBut a second witness is already waiting outside the archive.\n"


def agent_task_manifest_specs() -> dict[str, dict[str, str]]:
    return {
        "chapter_write": {
            "schema": "markdown_chapter_only",
            "output": "50_workbench/agent_drafts/ch001.codex.md",
            "validate": (
                "longform-engine draft submit project.yaml --chapter 1 "
                "--file 50_workbench/agent_drafts/ch001.codex.md --agent codex"
            ),
            "apply": "longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
            "failure": "longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
        },
        "repair": {
            "schema": "markdown_repair_candidate",
            "output": "50_workbench/repair_candidates/ch001.codex.repair_candidate.md",
            "validate": (
                "longform-engine draft submit project.yaml --chapter 1 "
                "--file 50_workbench/repair_candidates/ch001.codex.repair_candidate.md --agent codex --overwrite"
            ),
            "apply": "longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
            "failure": "longform-engine editorial need-human project.yaml --chapter 1 --reason repair_failed",
        },
        "humanize": {
            "schema": "markdown_humanized_candidate",
            "output": "50_workbench/repair_candidates/ch001.humanized_candidate.md",
            "validate": (
                "longform-engine creative humanize-check project.yaml --chapter 1 "
                "--file 50_workbench/repair_candidates/ch001.humanized_candidate.md"
            ),
            "apply": (
                "longform-engine draft submit project.yaml --chapter 1 "
                "--file 50_workbench/repair_candidates/ch001.humanized_candidate.md --agent codex --overwrite"
            ),
            "failure": "longform-engine creative humanize-task project.yaml --chapter 1 --source draft",
        },
        "content_expand": {
            "schema": "markdown_expanded_candidate",
            "output": "50_workbench/repair_candidates/ch001.expanded_candidate.md",
            "validate": (
                "longform-engine creative expand-check project.yaml --chapter 1 "
                "--file 50_workbench/repair_candidates/ch001.expanded_candidate.md"
            ),
            "apply": (
                "longform-engine draft submit project.yaml --chapter 1 "
                "--file 50_workbench/repair_candidates/ch001.expanded_candidate.md --agent codex --overwrite"
            ),
            "failure": "longform-engine creative expand-task project.yaml --chapter 1 --source draft",
        },
        "graph_extract": {
            "schema": "semantic_graph_update_v1",
            "output": "50_workbench/graph_updates/ch001.semantic_graph.json",
            "validate": (
                "longform-engine graph semantic-validate project.yaml --chapter 1 "
                "--file 50_workbench/graph_updates/ch001.semantic_graph.json"
            ),
            "apply": (
                "longform-engine graph semantic-apply project.yaml --chapter 1 "
                "--file 50_workbench/graph_updates/ch001.semantic_graph.json"
            ),
            "failure": "longform-engine graph semantic-task project.yaml --chapter 1",
        },
        "memory_extract": {
            "schema": "semantic_memory_v1",
            "output": "50_workbench/memory_tasks/ch001.semantic.codex.json",
            "validate": (
                "longform-engine memory semantic-validate project.yaml --chapter 1 "
                "--file 50_workbench/memory_tasks/ch001.semantic.codex.json"
            ),
            "apply": (
                "longform-engine memory semantic-apply project.yaml --chapter 1 "
                "--file 50_workbench/memory_tasks/ch001.semantic.codex.json"
            ),
            "failure": "longform-engine memory semantic-task project.yaml --chapter 1",
        },
        "character_memory": {
            "schema": "character_memory_cards_v1",
            "output": "50_workbench/memory_tasks/ch001.character.codex.json",
            "validate": (
                "longform-engine memory character-validate project.yaml --chapter 1 "
                "--file 50_workbench/memory_tasks/ch001.character.codex.json"
            ),
            "apply": (
                "longform-engine memory character-apply project.yaml --chapter 1 "
                "--file 50_workbench/memory_tasks/ch001.character.codex.json"
            ),
            "failure": "longform-engine memory character-task project.yaml --chapter 1",
        },
        "editorial_review": {
            "schema": "editorial_role_review_v1",
            "output": "50_workbench/editorial_reviews/results/ch001.serial_verifier.json",
            "validate": (
                "longform-engine editorial submit-review project.yaml --chapter 1 --role serial_verifier "
                "--file 50_workbench/editorial_reviews/results/ch001.serial_verifier.json"
            ),
            "apply": "longform-engine editorial aggregate project.yaml --chapter 1",
            "failure": "longform-engine editorial need-human project.yaml --chapter 1 --reason editorial_failed",
        },
        "pacing_review": {
            "schema": "semantic_pacing_result_v1",
            "output": "50_workbench/gate_artifacts/ch001/semantic_pacing_result.json",
            "validate": (
                "longform-engine pacing semantic-validate project.yaml --chapter 1 "
                "--file 50_workbench/gate_artifacts/ch001/semantic_pacing_result.json"
            ),
            "apply": (
                "longform-engine pacing semantic-apply project.yaml --chapter 1 "
                "--file 50_workbench/gate_artifacts/ch001/semantic_pacing_result.json"
            ),
            "failure": "longform-engine pacing semantic-task project.yaml --chapter 1",
        },
    }
