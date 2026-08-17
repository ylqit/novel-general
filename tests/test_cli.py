import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from longform_engine.cli import build_parser
from longform_engine.config import load_project_config
from longform_engine.storage import acquire_project_lock
from tests.project_fixtures import mark_project_ready


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "longform_engine.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def run_cli_with_input(args: tuple[str, ...], stdin: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "longform_engine.cli", *args],
        cwd=ROOT,
        env=env,
        input=stdin,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def mark_cli_project_ready(project_yaml: Path) -> None:
    payload = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
    payload.setdefault("editorial", {})["review_mode"] = "off"
    project_yaml.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    config = load_project_config(project_yaml)
    mark_project_ready(project_yaml.parent, config, preserve_existing_characters=True)


def test_cli_validate_template():
    result = run_cli("validate-config", "--template", "qidian-longform", "--explain")

    assert result.returncode == 0
    assert "OK: configuration is valid" in result.stdout
    assert "qidian-longform" in result.stdout


def test_cli_validate_template_without_api_keys():
    env = os.environ.copy()
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MOONSHOT_API_KEY",
        "GLM_API_KEY",
        "MINIMAX_API_KEY",
        "OPENAI_BASE_URL",
    ):
        env.pop(key, None)
    env["PYTHONPATH"] = str(ROOT / "src")

    result = subprocess.run(
        [sys.executable, "-m", "longform_engine.cli", "validate-config", "--template", "qidian-longform"],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "OK: configuration is valid" in result.stdout


def test_cli_mutating_commands_are_marked_for_project_lock():
    parser = build_parser()
    mutating_cases = [
        ("init-project", "--template", "qidian-longform", "--output", "novel"),
        ("db", "init", "project.yaml"),
        ("db", "sync", "project.yaml"),
        ("db", "rebuild", "project.yaml"),
        ("rag", "build", "project.yaml"),
        ("rag", "query", "project.yaml", "目标"),
        ("rag", "context", "project.yaml"),
        ("graph", "update", "project.yaml", "--chapter", "1"),
        ("graph", "check", "project.yaml"),
        ("open-book", "project.yaml"),
        ("open-book", "--interactive"),
        ("plan-chapter", "project.yaml", "--chapter", "1"),
        ("beat", "project.yaml", "--chapter", "1"),
        ("continue-write", "project.yaml"),
        ("auto-write", "plan", "project.yaml"),
        ("auto-write", "run", "project.yaml"),
        ("auto-write", "report", "project.yaml"),
        ("draft", "submit", "project.yaml", "--chapter", "1", "--file", "50_workbench/agent_drafts/ch001.codex.md"),
        ("chapter", "finalize", "project.yaml", "--chapter", "1", "--approved-by", "human"),
        ("chapter", "semantic-task", "project.yaml", "--chapter", "1"),
        ("chapter", "semantic-validate", "project.yaml", "--chapter", "1", "--file", "50_workbench/semantic_tasks/ch001.semantic.json"),
        ("chapter", "semantic-apply", "project.yaml", "--chapter", "1", "--file", "50_workbench/semantic_tasks/ch001.semantic.json"),
        ("gate-check", "project.yaml", "--chapter", "1"),
        ("gate-waiver", "project.yaml", "--chapter", "1", "--reason", "人工确认"),
        ("pacing-review", "project.yaml", "--chapter", "1"),
        ("pacing", "semantic-task", "project.yaml", "--chapter", "1"),
        ("pacing", "semantic-validate", "project.yaml", "--chapter", "1", "--file", "50_workbench/gate_artifacts/ch001/semantic_pacing_result.json"),
        ("pacing", "semantic-apply", "project.yaml", "--chapter", "1", "--file", "50_workbench/gate_artifacts/ch001/semantic_pacing_result.json"),
        ("repair", "synthesis-task", "project.yaml", "--chapter", "1"),
        ("repair", "synthesis-validate", "project.yaml", "--chapter", "1", "--file", "50_workbench/repair_plans/ch001/r01.plan.md"),
        ("repair", "candidate-task", "project.yaml", "--chapter", "1"),
        ("creative", "brief", "project.yaml", "--init"),
        ("creative", "style-extract", "project.yaml", "--file", "sample.md", "--name", "sample"),
        ("creative", "humanize-task", "project.yaml", "--chapter", "1", "--source", "draft"),
        ("creative", "humanize-check", "project.yaml", "--chapter", "1", "--file", "50_workbench/repair_candidates/ch001.md"),
        ("creative", "humanize-semantic-task", "project.yaml", "--chapter", "1"),
        (
            "creative",
            "humanize-semantic-validate",
            "project.yaml",
            "--chapter",
            "1",
            "--file",
            "50_workbench/humanizer_tasks/ch001.semantic_review.json",
        ),
        ("quality", "payoff-task", "project.yaml", "--chapter", "1"),
        (
            "quality",
            "payoff-validate",
            "project.yaml",
            "--chapter",
            "1",
            "--file",
            "50_workbench/quality_reviews/ch001.reader_payoff.json",
        ),
        ("quality", "feedback-status", "project.yaml", "--chapter", "2"),
        ("quality", "feedback-resolve", "project.yaml", "--id", "feedback:test", "--evidence", "fixed"),
        ("quality", "feedback-suppress", "project.yaml", "--id", "feedback:test", "--evidence", "not applicable"),
        ("creative", "expand-task", "project.yaml", "--chapter", "1", "--source", "draft"),
        ("creative", "expand-check", "project.yaml", "--chapter", "1", "--file", "50_workbench/repair_candidates/ch001.md"),
        ("research", "add", "project.yaml", "--file", "note.md"),
        ("research", "search", "project.yaml", "市舶司"),
        ("research", "promote", "project.yaml", "--item", "research_001"),
        ("impact-analyze", "project.yaml", "--research-item", "research_001"),
        ("revision", "branch", "project.yaml", "--chapter", "1"),
        ("revision", "rollback", "project.yaml", "--to-chapter", "1"),
        ("revision", "snapshot", "project.yaml"),
        ("editorial", "submit-review", "project.yaml", "--chapter", "1", "--role", "anti_ai_editor", "--file", "50_workbench/editorial_reviews/results/ch001.anti_ai_editor.json"),
        ("editorial", "aggregate", "project.yaml", "--chapter", "1"),
        ("production", "loop", "project.yaml"),
        ("intelligence", "task", "project.yaml", "--task-type", "book_design"),
        ("intelligence", "validate", "project.yaml", "--task-type", "book_design", "--file", "50_workbench/intelligence_candidates/book_design.project.candidate.json"),
        ("intelligence", "apply", "project.yaml", "--task-type", "book_design", "--document", "50_workbench/intelligence_candidates/book_design.project.candidate.md", "--delta", "50_workbench/intelligence_candidates/design_semantic_compile.book_design.project.delta.json", "--approved-by", "human"),
        ("character", "design-task", "project.yaml"),
        ("character", "design-validate", "project.yaml", "--file", "50_workbench/intelligence_candidates/character_expression_design.project.candidate.json"),
        ("character", "design-apply", "project.yaml", "--document", "50_workbench/intelligence_candidates/character_expression_design.project.candidate.md", "--delta", "50_workbench/intelligence_candidates/design_semantic_compile.character_expression_design.project.delta.json", "--approved-by", "human"),
        ("character", "audit-task", "project.yaml", "--from-chapter", "1", "--to-chapter", "15"),
        ("character", "audit-validate", "project.yaml", "--file", "50_workbench/intelligence_candidates/character_expression_review.ch001-ch015.candidate.json"),
        ("character", "audit-apply", "project.yaml", "--file", "50_workbench/intelligence_candidates/character_expression_review.ch001-ch015.candidate.json"),
        ("character", "samples-approve", "project.yaml", "--file", "50_workbench/character_reviews/voice_samples.json", "--approved-by", "human"),
        ("benchmark", "init", "project.yaml", "--run-id", "smoke-5", "--agent-product", "codex", "--chapters", "5"),
        ("benchmark", "record", "project.yaml", "--run-id", "smoke-5", "--chapter", "1", "--continuity", "4", "--character-consistency", "4", "--foreshadowing-control", "4", "--pacing", "4", "--reader-payoff", "4", "--ai-taste", "2", "--gate-passed", "--context-file-count", "6", "--context-character-count", "18000"),
        ("benchmark", "technical-record", "project.yaml", "--run-id", "formal-10", "--chapter", "1", "--gate-passed", "--context-file-count", "6", "--context-character-count", "18000"),
        ("benchmark", "rag-scale-run", "project.yaml", "--scale-chapters", "50", "--backend", "local_sqlite"),
        ("benchmark", "rag-production-template", "project.yaml"),
        ("benchmark", "rag-production-run", "project.yaml", "--run-id", "codex-10", "--dataset", "rag-dataset.json"),
        ("benchmark", "source-attach", "project.yaml", "--run-id", "codex-10", "--source-dir", "40_manuscript/final"),
        ("benchmark", "blind-pack", "project.yaml", "--comparison-id", "codex-vs-baseline", "--run-id", "codex-10", "--run-id", "baseline-10", "--seed", "private-seed"),
        ("benchmark", "blind-template", "project.yaml", "--comparison-id", "codex-vs-baseline", "--judge-id", "judge-a"),
        ("benchmark", "blind-submit", "project.yaml", "--comparison-id", "codex-vs-baseline", "--judge-id", "judge-a", "--file", "judge-a.json"),
        ("benchmark", "blind-aggregate", "project.yaml", "--comparison-id", "codex-vs-baseline"),
        ("benchmark", "report", "project.yaml", "--run-id", "smoke-5"),
        ("benchmark", "compare", "project.yaml", "--comparison-id", "quality-compare", "--run-id", "codex-10", "--run-id", "claude-10"),
    ]
    read_only_cases = [
        ("validate-config", "--template", "qidian-longform"),
        ("status", "project.yaml"),
        ("agent-task", "list", "project.yaml"),
        ("agent-task", "status", "project.yaml"),
        ("agent-task", "show", "project.yaml", "chapter_write:ch001:v4"),
        ("agent-task", "brief", "project.yaml", "chapter_write:ch001:v4"),
        ("agent-task", "validate", "project.yaml", "chapter_write:ch001:v4", "--strict"),
        ("production", "status", "project.yaml"),
        ("production", "next", "project.yaml"),
        ("production", "next", "project.yaml", "--editorial"),
        ("repair", "status", "project.yaml", "--chapter", "1"),
        ("quality", "contract", "project.yaml", "--chapter", "1"),
        ("quality", "story-profile", "project.yaml"),
        ("production", "board", "project.yaml"),
        ("production", "board", "project.yaml", "--editorial"),
        ("db", "status", "project.yaml"),
        ("db", "query", "project.yaml", "schema_meta"),
        ("graph", "validate", "project.yaml"),
        ("auto-write", "progress", "project.yaml"),
        ("benchmark", "validate", "project.yaml", "--run-id", "smoke-5"),
        ("release", "check", "--repository", ".", "--skip-contracts"),
        ("skills", "status", "--tool", "all"),
        ("doctor", "--tool", "all"),
    ]

    for command in mutating_cases:
        assert getattr(parser.parse_args(command), "mutates_project", False), command
    for command in read_only_cases:
        assert not getattr(parser.parse_args(command), "mutates_project", False), command


def test_cli_init_project(tmp_path):
    result = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))

    assert result.returncode == 0
    assert "OK: project initialized" in result.stdout
    assert (tmp_path / "novel" / "project.yaml").exists()


def test_cli_init_project_scale_preset(tmp_path):
    result = run_cli(
        "init-project",
        "--template",
        "qidian-longform",
        "--scale-preset",
        "million",
        "--output",
        str(tmp_path / "novel"),
    )

    assert result.returncode == 0, result.stderr
    config = load_project_config(tmp_path / "novel" / "project.yaml")
    assert config.data["length"]["target_total_characters"] == 1_000_000
    assert config.data["length"]["planning"]["mode"] == "rolling"
    assert config.data["length"]["volume"]["target_characters"] == 200_000


def test_cli_init_project_explicit_scale_overrides_preset(tmp_path):
    result = run_cli(
        "init-project",
        "--template",
        "qidian-longform",
        "--scale-preset",
        "million",
        "--target-total-characters",
        "1200000",
        "--chapter-target-characters",
        "2800",
        "--volume-target-characters",
        "180000",
        "--output",
        str(tmp_path / "novel"),
    )

    assert result.returncode == 0, result.stderr
    config = load_project_config(tmp_path / "novel" / "project.yaml")
    assert config.data["length"]["target_total_characters"] == 1_200_000
    assert config.data["length"]["chapter"]["target_characters"] == 2800
    assert config.data["length"]["volume"]["target_characters"] == 180_000


def test_cli_open_book_interactive_creates_project_and_opens(tmp_path):
    project_dir = tmp_path / "interactive_novel"
    stdin = "\n".join(
        [
            "Interactive Longform",
            "interactive_longform",
            str(project_dir),
            "qidian-longform",
            "million",
            "y",
            "",
        ]
    )
    result = run_cli_with_input(("open-book", "--interactive"), stdin)

    assert result.returncode == 0, result.stderr
    assert "OK: project initialized" in result.stdout
    assert "OK: open-book confirmed" in result.stdout
    project_yaml = project_dir / "project.yaml"
    assert project_yaml.exists()
    config = load_project_config(project_yaml)
    assert config.data["project"]["slug"] == "interactive_longform"
    assert config.data["length"]["target_total_characters"] == 1_000_000
    assert (project_dir / "00_governance" / "idea_seed.md").exists()
    assert (project_dir / "00_governance" / "reader_contract.md").exists()


def test_cli_db_init_and_status(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    db_init = run_cli("db", "init", str(project_yaml))
    db_status = run_cli("db", "status", str(project_yaml), "--json")

    assert db_init.returncode == 0
    assert "OK: database initialized" in db_init.stdout
    assert db_status.returncode == 0
    assert '"schema_version": "1"' in db_status.stdout


def test_cli_rag_build_query_and_context(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    final_dir = tmp_path / "novel" / "40_manuscript" / "final"
    summary_dir = tmp_path / "novel" / "40_manuscript" / "summaries"
    (final_dir / "ch001.md").write_text(
        "# 第一章 山门\n\n林迟听见旧钟声，青铜铃也随之震动。\n",
        encoding="utf-8",
    )
    (summary_dir / "ch001.md").write_text("林迟在山门听见旧钟声。\n", encoding="utf-8")

    project_yaml = tmp_path / "novel" / "project.yaml"
    build = run_cli("rag", "build", str(project_yaml), "--max-chars", "80")
    query = run_cli("rag", "query", str(project_yaml), "旧钟声", "--top-k", "2")
    context = run_cli("rag", "context", str(project_yaml), "--chapter", "2", "--query", "旧钟声")

    assert build.returncode == 0
    assert "OK: RAG chunks built" in build.stdout
    assert query.returncode == 0
    assert "Hits: 1" in query.stdout
    assert context.returncode == 0
    assert (tmp_path / "novel" / "60_rag" / "context" / "next_plot_context.md").exists()


def test_cli_graph_validate_update_and_check(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    bible = tmp_path / "novel" / "10_bible"
    final_dir = tmp_path / "novel" / "40_manuscript" / "final"
    summary_dir = tmp_path / "novel" / "40_manuscript" / "summaries"
    (bible / "characters.json").write_text(
        '[{"id":"character:lin","name":"林迟","type":"character"}]',
        encoding="utf-8",
    )
    (final_dir / "ch001.md").write_text("# 第一章 山门\n\n林迟听见旧钟声。\n", encoding="utf-8")
    (summary_dir / "ch001.md").write_text("林迟听见旧钟声。\n", encoding="utf-8")

    project_yaml = tmp_path / "novel" / "project.yaml"
    validate = run_cli("graph", "validate", str(project_yaml))
    update = run_cli("graph", "update", str(project_yaml), "--chapter", "1")
    check = run_cli("graph", "check", str(project_yaml))

    assert validate.returncode == 0
    assert "Errors: 0" in validate.stdout
    assert update.returncode == 0
    assert "OK: story graph updated" in update.stdout
    assert check.returncode == 0
    assert (tmp_path / "novel" / "50_workbench" / "graph_reports" / "graph_check.md").exists()


def test_cli_unified_chapter_semantic_task_prints_manifest(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    final_dir = tmp_path / "novel" / "40_manuscript" / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    (final_dir / "ch001.md").write_text(
        "# 第一章 山门\n\n林迟听见旧钟声，仍然没有说出青铜铃的来历。\n",
        encoding="utf-8",
    )

    project_yaml = tmp_path / "novel" / "project.yaml"
    semantic = run_cli("chapter", "semantic-task", str(project_yaml), "--chapter", "1")

    assert semantic.returncode == 0
    assert "Manifest:" in semantic.stdout
    assert "ch001.semantic.agent_task.json" in semantic.stdout


def test_cli_open_plan_beat_continue(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    open_book = run_cli("open-book", str(project_yaml))
    mark_cli_project_ready(project_yaml)
    plan = run_cli("plan-chapter", str(project_yaml), "--chapter", "1")
    beat = run_cli("beat", str(project_yaml), "--chapter", "1")
    cont = run_cli("continue-write", str(project_yaml), "--chapter", "1")

    assert open_book.returncode == 0
    assert "OK: open-book confirmed" in open_book.stdout
    assert plan.returncode == 0
    assert "OK: chapter card ready" in plan.stdout
    assert beat.returncode == 0
    assert "OK: beat sheet ready" in beat.stdout
    assert cont.returncode == 0
    assert "OK: continue-write task package ready" in cont.stdout
    assert "Writing task:" in cont.stdout
    assert "Next command:" in cont.stdout
    assert (tmp_path / "novel" / "50_workbench" / "writing_tasks" / "ch001.md").exists()
    assert not (tmp_path / "novel" / "40_manuscript" / "draft" / "ch001.md").exists()


def test_cli_agent_task_validate_strict_json(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    assert run_cli("open-book", str(project_yaml)).returncode == 0
    mark_cli_project_ready(project_yaml)
    assert run_cli("continue-write", str(project_yaml), "--chapter", "1").returncode == 0
    validate = run_cli(
        "agent-task",
        "validate",
        str(project_yaml),
        "chapter_write:ch001:v4",
        "--strict",
        "--json",
    )
    payload = json.loads(validate.stdout)

    assert validate.returncode == 0, validate.stderr
    assert payload["ok"] is True
    assert payload["strict"] is True
    assert payload["task_type"] == "chapter_write"
    assert payload["errors"] == []


def test_cli_auto_write_plan_run_progress_report(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    assert run_cli("open-book", str(project_yaml)).returncode == 0
    mark_cli_project_ready(project_yaml)
    plan = run_cli("auto-write", "plan", str(project_yaml))
    run = run_cli("auto-write", "run", str(project_yaml))
    progress = run_cli("auto-write", "progress", str(project_yaml))
    report = run_cli("auto-write", "report", str(project_yaml))
    state = json.loads((tmp_path / "novel" / "70_runtime" / "auto_write_state.json").read_text(encoding="utf-8"))

    assert plan.returncode == 0, plan.stderr
    assert "OK: auto-write plan" in plan.stdout
    assert run.returncode == 0, run.stderr
    assert "Status: awaiting_agent_draft" in run.stdout
    assert "Next command:" in run.stdout
    assert progress.returncode == 0, progress.stderr
    assert "Summary: Auto-write awaiting_agent_draft" in progress.stdout
    assert report.returncode == 0, report.stderr
    assert "auto_write_report.md" in report.stdout
    assert state["target_characters"] == 2_000_000
    assert state["forecast_chapters"] == 667
    assert state["status"] == "awaiting_agent_draft"
    assert (tmp_path / "novel" / "50_workbench" / "writing_tasks" / "ch001.md").exists()
    assert (tmp_path / "novel" / "70_runtime" / "run_reports" / "auto_write_report.md").exists()
    assert not (tmp_path / "novel" / "40_manuscript" / "final" / "ch001.md").exists()


def test_cli_draft_submit_agent_draft(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    open_book = run_cli("open-book", str(project_yaml))
    mark_cli_project_ready(project_yaml)
    cont = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    agent_draft = tmp_path / "novel" / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
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

    assert open_book.returncode == 0
    assert cont.returncode == 0
    assert submit.returncode == 0, submit.stderr
    assert "OK: agent draft submitted" in submit.stdout
    assert "Next command: longform-engine production next project.yaml" in submit.stdout
    assert (tmp_path / "novel" / "40_manuscript" / "draft" / "ch001.md").exists()
    assert (tmp_path / "novel" / "40_manuscript" / "draft" / "ch001.submission.json").exists()
    assert (tmp_path / "novel" / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").exists()
    assert (tmp_path / "novel" / "50_workbench" / "gate_artifacts" / "ch001" / "pacing_review.md").exists()


def test_cli_chapter_finalize_agent_draft(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    assert run_cli("open-book", str(project_yaml)).returncode == 0
    mark_cli_project_ready(project_yaml)
    assert run_cli("continue-write", str(project_yaml), "--chapter", "1").returncode == 0
    agent_draft = tmp_path / "novel" / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_draft_text(), encoding="utf-8")
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
    finalize = run_cli("chapter", "finalize", str(project_yaml), "--chapter", "1", "--approved-by", "human")

    assert submit.returncode == 0, submit.stderr
    assert finalize.returncode == 0, finalize.stderr
    assert "OK: chapter finalized" in finalize.stdout
    assert "Next command: longform-engine chapter semantic-task project.yaml --chapter 1" in finalize.stdout
    assert (tmp_path / "novel" / "40_manuscript" / "final" / "ch001.md").exists()
    assert (tmp_path / "novel" / "40_manuscript" / "final" / "ch001.finalization.json").exists()
    assert (tmp_path / "novel" / "40_manuscript" / "summaries" / "ch001.md").exists()
    assert not (tmp_path / "novel" / "60_rag" / "chunks" / "ch001.json").exists()
    assert (tmp_path / "novel" / "60_rag" / "context" / "next_plot_context.md").exists()


def test_cli_project_lock_blocks_mutating_command_but_not_read_only(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    project_config = load_project_config(project_yaml)

    with acquire_project_lock(project_config, owner="test", command="held-by-test"):
        blocked = run_cli("continue-write", str(project_yaml), "--chapter", "1")
        blocked_auto = run_cli("auto-write", "run", str(project_yaml))
        read_only = run_cli("status", str(project_yaml), "--json")

    assert blocked.returncode == 1
    assert "Project lock already exists" in blocked.stderr
    assert blocked_auto.returncode == 1
    assert "Project lock already exists" in blocked_auto.stderr
    assert read_only.returncode == 0
    assert '"exists": true' in read_only.stdout


def test_cli_failed_gate_reports_review_barrier_before_repair(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    plan = run_cli("plan-chapter", str(project_yaml), "--chapter", "1")
    assert plan.returncode == 0
    draft = tmp_path / "novel" / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# 第一章\n\nTODO 写作说明：这里需要补剧情。\n", encoding="utf-8")

    gate = run_cli("gate-check", str(project_yaml), "--chapter", "1")
    pacing = run_cli("pacing-review", str(project_yaml), "--chapter", "1")
    repair = run_cli("repair", "status", str(project_yaml), "--chapter", "1", "--json")

    assert gate.returncode == 1
    assert "Passed: False" in gate.stdout
    assert pacing.returncode in (0, 1)
    assert "OK: pacing review completed" in pacing.stdout
    assert repair.returncode == 0
    assert '"status": "reviews_pending"' in repair.stdout


def test_cli_creative_style_extract_json(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    sample = tmp_path / "sample_style.md"
    sample.write_text(
        '"Hold the gate bell," Lin said.\n\n"The gate bell is awake."\n\nHe stepped once and listened.\n',
        encoding="utf-8",
    )
    extract = run_cli(
        "creative",
        "style-extract",
        str(project_yaml),
        "--file",
        str(sample),
        "--name",
        "sharp_dialogue",
        "--source-project",
        "reference-book",
        "--json",
    )
    payload = json.loads(extract.stdout)
    current = json.loads((tmp_path / "novel" / "10_bible" / "style_profiles" / "current_style_profile.json").read_text(encoding="utf-8"))

    assert extract.returncode == 0, extract.stderr
    assert payload["name"] == "sharp_dialogue"
    assert payload["activated"] is True
    assert payload["fingerprint"]["dialogue_ratio"] > 0
    assert current["profile_type"] == "sample_extract"
    assert current["sample_sources"][0]["source_project"] == "reference-book"


def test_cli_creative_humanize_check_chinese_json(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    candidate = tmp_path / "novel" / "50_workbench" / "repair_candidates" / "ch001.humanized_candidate.md"
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(
        "# 第一章\n\nTODO：这里还没有写完。林远仿佛不禁意识到，这件事意义深远，他嘴角微扬。\n",
        encoding="utf-8",
    )
    check = run_cli("creative", "humanize-check", str(project_yaml), "--chapter", "1", "--file", str(candidate), "--json")
    payload = json.loads(check.stdout)
    codes = {item["code"] for item in payload["issues"]}

    assert check.returncode == 1
    assert "humanizer_meta_residue" in codes
    assert "humanizer_inflated_significance" in codes
    assert "humanizer_cliche_action" in codes
    assert payload["issue_summary"]["by_category"]["TODO/占位符"] == 1
    assert not (tmp_path / "novel" / "40_manuscript" / "final" / "ch001.md").exists()


def test_cli_creative_expand_task_and_check(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    assert run_cli("plan-chapter", str(project_yaml), "--chapter", "1").returncode == 0
    draft = tmp_path / "novel" / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nShort draft at the gate.\n", encoding="utf-8")
    gate = run_cli("gate-check", str(project_yaml), "--chapter", "1")
    task = run_cli("creative", "expand-task", str(project_yaml), "--chapter", "1", "--source", "draft")

    candidate = tmp_path / "novel" / "50_workbench" / "repair_candidates" / "ch001.expanded_candidate.md"
    expansion_beat = (
        'At the north gate, wind scraped the stone road and the iron bell gave one dry sound. '
        '"Hold the line," Lin said, but his breath caught when the locked door answered from behind him. '
        "He hesitated, thought of the debt on his father's name, grabbed the bell rope, then stepped through "
        "the hall before the patrol arrived. "
    )
    candidate.write_text("# Chapter 1\n\n" + expansion_beat * 40 + "\nSecret behind the door?\n", encoding="utf-8")
    check = run_cli("creative", "expand-check", str(project_yaml), "--chapter", "1", "--file", str(candidate))

    assert gate.returncode == 1
    assert task.returncode == 0, task.stderr
    assert "OK: content expansion task written" in task.stdout
    assert "50_workbench" in task.stdout
    assert check.returncode == 0, check.stderr
    assert "Passed: True" in check.stdout
    assert "draft submit project.yaml" in check.stdout
    assert "--overwrite" in check.stdout
    assert not (tmp_path / "novel" / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (tmp_path / "novel" / "60_rag" / "chunks" / "ch001.json").exists()


def test_cli_editorial_review_and_need_human_request(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    draft = tmp_path / "novel" / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nTODO verify continuity before publication.\n", encoding="utf-8")

    review = run_cli("editorial", "review", str(project_yaml), "--chapter", "1", "--json")
    payload = json.loads(review.stdout)
    need = run_cli(
        "editorial",
        "need-human",
        str(project_yaml),
        "--chapter",
        "1",
        "--reason",
        "chief editor requested",
        "--json",
    )
    status = json.loads(need.stdout)
    request_file = tmp_path / "novel" / "50_workbench" / "editorial_reviews" / "need_human_ch001.json"
    request = json.loads(request_file.read_text(encoding="utf-8"))

    assert review.returncode == 1
    assert payload["need_human"] is True
    assert payload["severity_counts"]["P0"] == 1
    assert need.returncode == 0, need.stderr
    assert status["need_human"] is True
    assert status["human_request_file"].endswith("need_human_ch001.json")
    assert request["reason"] == "chief editor requested"
    assert not (tmp_path / "novel" / "40_manuscript" / "final" / "ch001.md").exists()
    assert not (tmp_path / "novel" / "60_rag" / "chunks" / "ch001.json").exists()
    assert not any((tmp_path / "novel" / "70_runtime" / "db").glob("*.sqlite"))


def test_cli_research_add_impact_promote(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    note = tmp_path / "note.md"
    note.write_text("# 市舶司制度\n\n林迟可以借鉴市舶司的抽分制度设计商路冲突。\n", encoding="utf-8")

    add = run_cli("research", "add", str(project_yaml), "--file", str(note), "--json")
    assert add.returncode == 0
    payload = json.loads(add.stdout)
    item_id = payload["item_id"]

    impact = run_cli("impact-analyze", str(project_yaml), "--research-item", item_id)
    promote = run_cli("research", "promote", str(project_yaml), "--item", item_id)
    rag_query = run_cli("rag", "query", str(project_yaml), "市舶司", "--top-k", "2")

    assert impact.returncode == 0
    assert "OK: research impact report written" in impact.stdout
    assert promote.returncode == 0
    assert "OK: research item promoted to canon" in promote.stdout
    assert rag_query.returncode == 0
    assert "Hits: 1" in rag_query.stdout


def test_cli_revision_branch_rollback_and_impact(tmp_path):
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(tmp_path / "novel"))
    assert init.returncode == 0

    project_yaml = tmp_path / "novel" / "project.yaml"
    final_dir = tmp_path / "novel" / "40_manuscript" / "final"
    summary_dir = tmp_path / "novel" / "40_manuscript" / "summaries"
    cards = tmp_path / "novel" / "20_outline" / "chapter_cards"
    for number in (1, 2):
        (final_dir / f"ch{number:03d}.md").write_text(f"# 第{number}章\n\n林迟推进主线。\n", encoding="utf-8")
        (summary_dir / f"ch{number:03d}.md").write_text(f"ch{number:03d} 摘要。\n", encoding="utf-8")
        (cards / f"ch{number:03d}.json").write_text(
            json.dumps({"chapter_number": number, "status": "planned", "title": f"第{number}章"}, ensure_ascii=False),
            encoding="utf-8",
        )

    branch = run_cli("revision", "branch", str(project_yaml), "--chapter", "2")
    rollback = run_cli("revision", "rollback", str(project_yaml), "--to-chapter", "1")
    impact = run_cli("impact-analyze", str(project_yaml), "--after-rollback")
    status = run_cli("status", str(project_yaml), "--json")

    assert branch.returncode == 0
    assert "OK: rewrite candidate created" in branch.stdout
    assert rollback.returncode == 0
    assert "OK: rollback completed" in rollback.stdout
    assert impact.returncode == 0
    assert "OK: rollback impact report written" in impact.stdout
    assert status.returncode == 0
    payload = json.loads(status.stdout)
    assert payload["current_chapter"] == 1
    assert "rag_chunks" in payload["stale"]
    assert any(item["chapter_number"] == 2 and item["status"] == "detached" for item in payload["chapter_states"])


def test_quality_contract_cli_explains_primary_and_compatibility_markets():
    config = ROOT / "templates" / "qidian-longform" / "project.yaml"

    result = run_cli(
        "quality",
        "contract",
        str(config),
        "--chapter",
        "1",
        "--compare-market",
        "fanqie_free",
        "--explain",
    )

    assert result.returncode == 0, result.stderr
    assert "Profile: qidian_male + setting:xuanhuan, plot_engines:progression" in result.stdout
    assert "+ opening" in result.stdout
    assert "Merge trace:" in result.stdout
    assert "market_phase" in result.stdout
    assert "fanqie_free" in result.stdout
    assert "non-blocking" in result.stdout


def passing_draft_text() -> str:
    sentence = "林迟沿着山门石阶向上，旧钟声在雾里回荡，他记住师父留下的规矩，也看见山下灯火一步步逼近。"
    return "# 第一章 山门\n\n" + sentence * 80 + "\n\n然而，封死三年的山门忽然从里面开了。\n"
