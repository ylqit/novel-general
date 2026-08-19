"""Command line interface for longform-novel-engine."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from longform_engine import __version__
from longform_engine.artifacts import artifact_status, compact_artifacts, restore_artifacts, verify_artifacts
from longform_engine.benchmark import (
    compare_benchmarks,
    init_benchmark,
    record_benchmark_chapter,
    record_rag_benchmark,
    report_benchmark,
    validate_benchmark,
)
from longform_engine.blind_review import (
    aggregate_blind_reviews,
    attach_benchmark_source,
    create_blind_review_pack,
    create_blind_review_template,
    submit_blind_review,
)
from longform_engine.character_expression import approve_voice_samples
from longform_engine.cli_recovery import register_recovery_commands

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_tasks import (
    list_manifests,
    load_manifest,
    manifest_chapter_number,
    manifest_commands,
    manifest_input_paths,
    manifest_output,
    reconcile_task_lineage,
    status_summary,
    task_reconciliation_status,
    validate_manifest_strict,
)
from longform_engine.agent_protocol_readiness import (
    check_agent_data_pipeline_readiness,
    render_agent_data_pipeline_readiness,
)
from longform_engine.config import ConfigDocument, ConfigError, config_field_registry, load_project_config
from longform_engine.completion import approve_completion, completion_status
from longform_engine.creative import (
    expand_check,
    expand_task,
    humanize_check,
    humanize_semantic_task,
    humanize_semantic_validate,
    humanize_task,
    init_creative_brief,
    style_extract,
    validate_creative_brief,
)
from longform_engine.db import init_database, query_table, rebuild_database, status as db_status, sync_database
from longform_engine.distribution import (
    doctor_payload,
    install_skills,
    render_doctor,
    render_status,
    skill_status_payload,
    uninstall_skills,
    update_skills,
)
from longform_engine.editorial import (
    editorial_aggregate,
    editorial_batch_review,
    editorial_need_human,
    editorial_review,
    editorial_status,
    editorial_submit_review,
)
from longform_engine.gates import (
    GateError,
    gate_check,
    pacing_review,
    record_waiver,
    semantic_pacing_apply,
    semantic_pacing_task,
    semantic_pacing_validate,
    semantic_review_apply,
    semantic_review_task,
    semantic_review_validate,
)
from longform_engine.release_readiness import check_release_readiness, render_release_readiness
from longform_engine.repair_coordination import (
    RepairCoordinationError,
    create_repair_candidate_task,
    create_repair_synthesis_task,
    repair_attempt_status,
    repair_plan_status,
    review_barrier_status,
    validate_repair_plan,
)
from longform_engine.graph import (
    check_graph,
    retrieve_graph,
    update_graph,
    validate_graph,
)
from longform_engine.intelligence import (
    DESIGN_INTELLIGENCE_TASK_TYPES,
    INTELLIGENCE_TASK_TYPES,
    apply_compiled_design,
    apply_intelligence_candidate,
    approve_design_document,
    create_design_compile_task,
    create_intelligence_task,
    fanfiction_status,
    validate_intelligence_candidate,
    validate_design_compile_delta,
)
from longform_engine.lengths import compile_length_forecast
from longform_engine.story_profiles import BUILTIN_MARKET_IDS, compile_story_profile
from longform_engine.memory import (
    build_tcs,
    build_tcs_transition,
    character_check,
    compress_memory,
    validate_tcs,
    validate_memory,
)
from longform_engine.models import (
    cache_status_payload,
    install_model_profile,
    list_profiles,
    verify_models,
)
from longform_engine.orchestration import (
    auto_write_plan,
    auto_write_progress,
    auto_write_report,
    auto_write_run,
    WorkflowError,
    batch_write,
    continue_write,
    finalize_chapter,
    generate_beat_sheet,
    open_book,
    plan_chapter,
    submit_agent_draft,
)
from longform_engine.planning import revise_outline
from longform_engine.publication import export_publication_bundle, publication_risk_report
from longform_engine.production import agent_task_brief, production_board, production_loop, production_next, production_status
from longform_engine.prompting import validate_project_prompt_overlay
from longform_engine.quality import (
    approve_style_baseline,
    compile_effective_quality_contract,
    feedback_registry_status,
    reader_payoff_task,
    reader_payoff_validate,
    transition_feedback,
)
from longform_engine.rag import (
    build_chunks,
    build_context,
    query as rag_query,
    run_rag_production_benchmark,
    run_rag_scale_benchmark,
    write_rag_production_template,
)
from longform_engine.research import (
    detect_knowledge_gaps,
    ResearchError,
    add_research,
    impact_analyze,
    promote_research,
    search_research,
)
from longform_engine.revision import (
    RevisionError,
    create_revision_branch,
    project_status,
    rollback,
    rollback_impact,
)
from longform_engine.storage import (
    acquire_project_lock,
    init_project,
    recovery_status,
    resolve_project_root,
    snapshot_project,
)
from longform_engine.semantic import chapter_close, semantic_apply as chapter_semantic_apply
from longform_engine.semantic import semantic_rebuild as chapter_semantic_rebuild
from longform_engine.semantic import semantic_task as chapter_semantic_task
from longform_engine.semantic import semantic_validate as chapter_semantic_validate
from longform_engine.vectorstore import healthcheck as vector_healthcheck, rebuild_from_files as vector_rebuild


SCALE_PRESETS: dict[str, dict[str, Any]] = {
    "million": {
        "label": "100 万字",
        "length": {
            "metric": "content_characters_v1",
            "target_total_characters": 1_000_000,
            "completion_tolerance": [0.90, 1.10],
            "chapter": {"target_characters": 3000, "soft_min": 2400, "soft_max": 3600, "hard_min": 2000, "hard_max": 4200},
            "volume": {"target_characters": 200_000},
            "planning": {"mode": "rolling", "detailed_horizon": 20, "refill_threshold": 8},
        },
    },
    "standard": {
        "label": "150 万字",
        "length": {
            "metric": "content_characters_v1",
            "target_total_characters": 1_500_000,
            "completion_tolerance": [0.90, 1.10],
            "chapter": {"target_characters": 3000, "soft_min": 2400, "soft_max": 3600, "hard_min": 2000, "hard_max": 4200},
            "volume": {"target_characters": 250_000},
            "planning": {"mode": "rolling", "detailed_horizon": 20, "refill_threshold": 8},
        },
    },
    "extended": {
        "label": "200 万字正式上限",
        "length": {
            "metric": "content_characters_v1",
            "target_total_characters": 2_000_000,
            "completion_tolerance": [0.90, 1.10],
            "chapter": {"target_characters": 3000, "soft_min": 2400, "soft_max": 3600, "hard_min": 2000, "hard_max": 4200},
            "volume": {"target_characters": 250_000},
            "planning": {"mode": "rolling", "detailed_horizon": 20, "refill_threshold": 8},
        },
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="longform-engine",
        description="Engineering-first workflow engine for million-word Chinese longform novels.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-config", help="Validate a project config or template.")
    validate.add_argument("config", nargs="?", help="Path to project.yaml.")
    validate.add_argument("--template", help="Template name, for example qidian-longform.")
    add_scale_arguments(validate)
    validate.add_argument("--explain", action="store_true", help="Print config source precedence.")
    validate.set_defaults(func=cmd_validate_config)

    init = subparsers.add_parser("init-project", help="Create a novel project layout.")
    init.add_argument("config", nargs="?", help="Path to project.yaml.")
    init.add_argument("--template", default=None, help="Template name, for example qidian-longform.")
    init.add_argument("--output", help="Target project directory.")
    init.add_argument("--interactive", action="store_true", help="Run the project creation wizard.")
    add_scale_arguments(init)
    init.add_argument("--force", action="store_true", help="Overwrite seed files if they already exist.")
    init.set_defaults(func=cmd_init_project)

    status = subparsers.add_parser("status", help="Show project bootstrap status.")
    status.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    status.set_defaults(func=cmd_status)

    book = subparsers.add_parser("book", help="Inspect and explicitly approve whole-book completion.")
    book_subparsers = book.add_subparsers(dest="book_command", required=True)
    completion_status_cmd = book_subparsers.add_parser(
        "completion-status", help="Check ending, character budget, promises, gates, and closure."
    )
    completion_status_cmd.add_argument("config", nargs="?", default="project.yaml")
    completion_status_cmd.add_argument("--json", action="store_true")
    completion_status_cmd.set_defaults(func=cmd_book_completion_status)
    completion_approve_cmd = book_subparsers.add_parser(
        "completion-approve", help="Record explicit human approval after completion evidence passes."
    )
    completion_approve_cmd.add_argument("config", nargs="?", default="project.yaml")
    completion_approve_cmd.add_argument("--approved-by", required=True)
    completion_approve_cmd.add_argument("--ending-summary", required=True)
    completion_approve_cmd.add_argument("--json", action="store_true")
    completion_approve_cmd.set_defaults(func=cmd_book_completion_approve)

    skills = subparsers.add_parser("skills", help="Install and maintain bundled Codex/Claude Code Skills.")
    skills_subparsers = skills.add_subparsers(dest="skills_command", required=True)
    for action, help_text, handler in (
        ("install", "Install bundled Skills using safe copy mode.", cmd_skills_install),
        ("status", "Compare installed Skills with this engine version.", cmd_skills_status),
        ("update", "Update Skills owned by longform-novel-engine.", cmd_skills_update),
        ("uninstall", "Remove Skills owned by longform-novel-engine.", cmd_skills_uninstall),
    ):
        command = skills_subparsers.add_parser(action, help=help_text)
        command.add_argument("--tool", choices=["codex", "claude-code", "all"], default="all")
        command.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
        if action == "install":
            command.add_argument("--force", action="store_true", help="Replace an older or unowned target after explicit review.")
        if action == "uninstall":
            command.add_argument("--yes", action="store_true", help="Confirm removal of owned Skill directories.")
        command.set_defaults(func=handler)

    doctor = subparsers.add_parser("doctor", help="Check installed resources, Skills, Semantic dependencies, and project state.")
    doctor.add_argument("--tool", choices=["codex", "claude-code", "all"], default="all")
    doctor.add_argument("--project", help="Optional project.yaml to include model and project checks.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable doctor_v1 JSON.")
    doctor.set_defaults(func=cmd_doctor)

    release = subparsers.add_parser("release", help="Inspect public release readiness without publishing anything.")
    release_subparsers = release.add_subparsers(dest="release_command", required=True)
    release_check = release_subparsers.add_parser("check", help="Check version, Git, resources, Skills, CI, and tag readiness.")
    release_check.add_argument("--repository", default=".", help="Repository root to inspect.")
    release_check.add_argument("--tag", default="", help="Optional exact release tag expected at HEAD.")
    release_check.add_argument("--check-remote", action="store_true", help="Query public origin for master and optional tag refs.")
    release_check.add_argument("--allow-detached", action="store_true", help="Allow a detached CI checkout when no release tag is supplied.")
    release_check.add_argument("--skip-contracts", action="store_true", help="Skip resource and Skill contract subprocesses.")
    release_check.add_argument("--json", action="store_true", help="Print machine-readable release_readiness_v1 JSON.")
    release_check.add_argument(
        "--channel",
        choices=("public", "rc"),
        default="public",
        help="Validate public-release or unpublished release-candidate surfaces.",
    )
    release_check.set_defaults(func=cmd_release_check)

    benchmark = subparsers.add_parser("benchmark", help="Create and summarize no-LLM quality benchmark records.")
    benchmark_subparsers = benchmark.add_subparsers(dest="benchmark_command", required=True)

    benchmark_init = benchmark_subparsers.add_parser("init", help="Create a benchmark run template without calling an LLM.")
    benchmark_init.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_init.add_argument("--run-id", required=True)
    benchmark_init.add_argument("--host-product", required=True, choices=["codex", "claude-code"])
    benchmark_init.add_argument("--chapters", type=positive_int_arg, required=True)
    benchmark_init.add_argument("--scenario-id", default="", help="Stable setting id shared by comparable runs.")
    benchmark_init.add_argument("--scenario-file", help="Scenario JSON whose SHA-256 anchors comparable runs.")
    benchmark_init.add_argument("--agent-model", default="", help="Model label used by the host Agent product.")
    benchmark_init.add_argument("--host-version", default="", help="Codex or Claude Code host version label.")
    benchmark_init.add_argument("--workflow-version", default="", help="Engine workflow version used for this run.")
    benchmark_init.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_init.set_defaults(func=cmd_benchmark_init)

    benchmark_record = benchmark_subparsers.add_parser("record", help="Record one real chapter result without storing manuscript text.")
    benchmark_record.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_record.add_argument("--run-id", required=True)
    benchmark_record.add_argument("--chapter", type=positive_int_arg, required=True)
    for metric in ("continuity", "character-consistency", "foreshadowing-control", "pacing", "reader-payoff", "ai-taste"):
        benchmark_record.add_argument(f"--{metric}", type=score_arg, required=True)
    for metric in (
        "canon-fidelity",
        "ooc-control",
        "original-contribution",
        "divergence-causality",
        "source-prose-originality",
        "crossover-consistency",
    ):
        benchmark_record.add_argument(
            f"--{metric}",
            type=score_arg,
            help="Fanfiction benchmark score; all six are required when creation.mode=fanfiction.",
        )
    gate_group = benchmark_record.add_mutually_exclusive_group(required=True)
    gate_group.add_argument("--gate-passed", dest="gate_passed", action="store_true")
    gate_group.add_argument("--gate-failed", dest="gate_passed", action="store_false")
    benchmark_record.add_argument("--repair-count", type=non_negative_int_arg, default=0)
    benchmark_record.add_argument("--need-human-count", type=non_negative_int_arg, default=0)
    benchmark_record.add_argument("--context-file-count", type=non_negative_int_arg, required=True)
    benchmark_record.add_argument("--context-character-count", type=non_negative_int_arg, required=True)
    benchmark_record.add_argument("--p0-contradiction-count", type=non_negative_int_arg, default=0)
    benchmark_record.add_argument("--canonical-pollution-count", type=non_negative_int_arg, default=0)
    benchmark_record.add_argument("--judge", action="append", default=[], help="Repeat for each blinded evaluator id.")
    benchmark_record.add_argument("--character-drift", action="append", default=[])
    benchmark_record.add_argument("--foreshadowing-leak", action="append", default=[])
    benchmark_record.add_argument("--ai-taste-issue", action="append", default=[])
    benchmark_record.add_argument("--notes", default="")
    benchmark_record.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_record.set_defaults(func=cmd_benchmark_record)

    benchmark_technical = benchmark_subparsers.add_parser(
        "technical-record",
        help="Record production metrics before formal literary blind review.",
    )
    benchmark_technical.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_technical.add_argument("--run-id", required=True)
    benchmark_technical.add_argument("--chapter", type=positive_int_arg, required=True)
    technical_gate = benchmark_technical.add_mutually_exclusive_group(required=True)
    technical_gate.add_argument("--gate-passed", dest="gate_passed", action="store_true")
    technical_gate.add_argument("--gate-failed", dest="gate_passed", action="store_false")
    benchmark_technical.add_argument("--repair-count", type=non_negative_int_arg, default=0)
    benchmark_technical.add_argument("--need-human-count", type=non_negative_int_arg, default=0)
    benchmark_technical.add_argument("--context-file-count", type=non_negative_int_arg, required=True)
    benchmark_technical.add_argument("--context-character-count", type=non_negative_int_arg, required=True)
    benchmark_technical.add_argument("--p0-contradiction-count", type=non_negative_int_arg, default=0)
    benchmark_technical.add_argument("--canonical-pollution-count", type=non_negative_int_arg, default=0)
    benchmark_technical.add_argument("--notes", default="")
    benchmark_technical.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_technical.set_defaults(func=cmd_benchmark_technical_record)

    benchmark_rag = benchmark_subparsers.add_parser("rag-record", help="Record 500-chapter RAG scale evidence for a quality claim.")
    benchmark_rag.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_rag.add_argument("--run-id", required=True)
    benchmark_rag.add_argument("--scale-chapters", type=positive_int_arg, required=True)
    benchmark_rag.add_argument("--recall-at-k", type=float, required=True)
    benchmark_rag.add_argument("--fact-error-rate", type=float, required=True)
    benchmark_rag.add_argument("--p95-query-ms", type=float, required=True)
    benchmark_rag.add_argument("--incremental-index-ms", type=float, required=True)
    benchmark_rag.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_rag.set_defaults(func=cmd_benchmark_rag_record)

    benchmark_rag_run = benchmark_subparsers.add_parser(
        "rag-scale-run",
        help="Run the fixed 50/200/500 chapter vector-store engineering benchmark.",
    )
    benchmark_rag_run.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_rag_run.add_argument("--scale-chapters", type=int, choices=(50, 200, 500, 667), required=True)
    benchmark_rag_run.add_argument("--backend", choices=("local_sqlite", "local_hnsw"))
    benchmark_rag_run.add_argument("--query-count", type=positive_int_arg, default=60)
    benchmark_rag_run.add_argument("--top-k", type=positive_int_arg, default=10)
    benchmark_rag_run.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_rag_run.set_defaults(func=cmd_benchmark_rag_scale_run)

    benchmark_rag_template = benchmark_subparsers.add_parser(
        "rag-production-template",
        help="Write a claim-grade real-manuscript RAG query dataset template.",
    )
    benchmark_rag_template.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_rag_template.add_argument("--output", help="Optional output JSON path.")
    benchmark_rag_template.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_rag_template.set_defaults(func=cmd_benchmark_rag_production_template)

    benchmark_rag_production = benchmark_subparsers.add_parser(
        "rag-production-run",
        help="Measure production embedding/reranker retrieval over at least 500 final chapters.",
    )
    benchmark_rag_production.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_rag_production.add_argument("--run-id", required=True)
    benchmark_rag_production.add_argument("--dataset", required=True, help="Validated rag_production_dataset_v1 JSON.")
    benchmark_rag_production.add_argument("--top-k", type=positive_int_arg, default=10)
    benchmark_rag_production.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_rag_production.set_defaults(func=cmd_benchmark_rag_production_run)

    benchmark_source = benchmark_subparsers.add_parser(
        "source-attach",
        help="Attach SHA-256 provenance for the exact reviewed manuscript chapters.",
    )
    benchmark_source.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_source.add_argument("--run-id", required=True)
    benchmark_source.add_argument("--source-dir", required=True)
    benchmark_source.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_source.set_defaults(func=cmd_benchmark_source_attach)

    benchmark_blind_pack = benchmark_subparsers.add_parser(
        "blind-pack",
        help="Create a randomized two-run public review pack and private mapping.",
    )
    benchmark_blind_pack.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_blind_pack.add_argument("--comparison-id", required=True)
    benchmark_blind_pack.add_argument("--run-id", action="append", required=True)
    benchmark_blind_pack.add_argument("--seed", required=True, help="Non-empty deterministic randomization seed.")
    benchmark_blind_pack.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_blind_pack.set_defaults(func=cmd_benchmark_blind_pack)

    benchmark_blind_template = benchmark_subparsers.add_parser(
        "blind-template",
        help="Create one identity-free scoring template for an independent judge.",
    )
    benchmark_blind_template.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_blind_template.add_argument("--comparison-id", required=True)
    benchmark_blind_template.add_argument("--judge-id", required=True)
    benchmark_blind_template.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_blind_template.set_defaults(func=cmd_benchmark_blind_template)

    benchmark_blind_submit = benchmark_subparsers.add_parser(
        "blind-submit",
        help="Validate and store one complete independent blind-review submission.",
    )
    benchmark_blind_submit.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_blind_submit.add_argument("--comparison-id", required=True)
    benchmark_blind_submit.add_argument("--judge-id", required=True)
    benchmark_blind_submit.add_argument("--file", required=True)
    benchmark_blind_submit.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_blind_submit.set_defaults(func=cmd_benchmark_blind_submit)

    benchmark_blind_aggregate = benchmark_subparsers.add_parser(
        "blind-aggregate",
        help="Aggregate at least three independent blind reviews into paired run records.",
    )
    benchmark_blind_aggregate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_blind_aggregate.add_argument("--comparison-id", required=True)
    benchmark_blind_aggregate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_blind_aggregate.set_defaults(func=cmd_benchmark_blind_aggregate)

    benchmark_validate = benchmark_subparsers.add_parser("validate", help="Validate benchmark structure and completion state.")
    benchmark_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_validate.add_argument("--run-id", required=True)
    benchmark_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_validate.set_defaults(func=cmd_benchmark_validate)

    benchmark_report = benchmark_subparsers.add_parser("report", help="Aggregate benchmark records without literary model scoring.")
    benchmark_report.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_report.add_argument("--run-id", required=True)
    benchmark_report.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_report.set_defaults(func=cmd_benchmark_report)

    benchmark_compare = benchmark_subparsers.add_parser("compare", help="Compare two or more runs from the same setting and chapter count.")
    benchmark_compare.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    benchmark_compare.add_argument("--comparison-id", required=True)
    benchmark_compare.add_argument("--run-id", action="append", required=True, help="Repeat for each run to compare.")
    benchmark_compare.add_argument("--allow-incomplete", action="store_true", help="Write a provisional comparison for incomplete runs.")
    benchmark_compare.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    benchmark_compare.set_defaults(func=cmd_benchmark_compare)

    agent_task = subparsers.add_parser("agent-task", help="Inspect current AgentTaskManifest v4 task packages.")
    agent_task_subparsers = agent_task.add_subparsers(dest="agent_task_command", required=True)

    agent_task_list = agent_task_subparsers.add_parser("list", help="List indexed agent task manifests.")
    agent_task_list.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    agent_task_list.add_argument("--chapter", type=int, help="Filter by chapter number.")
    agent_task_list.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    agent_task_list.set_defaults(func=cmd_agent_task_list)

    agent_task_status = agent_task_subparsers.add_parser("status", help="Summarize agent task status.")
    agent_task_status.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    agent_task_status.add_argument("--chapter", type=int, help="Filter by chapter number.")
    agent_task_status.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    agent_task_status.set_defaults(func=cmd_agent_task_status)

    agent_task_show = agent_task_subparsers.add_parser("show", help="Show one manifest by task_id or path.")
    agent_task_show.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    agent_task_show.add_argument("task", help="Task id or manifest path.")
    agent_task_show.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    agent_task_show.set_defaults(func=cmd_agent_task_show)

    agent_task_brief_cmd = agent_task_subparsers.add_parser("brief", help="Render one manifest as an Agent work order.")
    agent_task_brief_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    agent_task_brief_cmd.add_argument("task", help="Task id or manifest path.")
    agent_task_brief_cmd.add_argument(
        "--host", choices=("codex", "claude-code"), default="codex", help="Host-specific display adapter."
    )
    agent_task_brief_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    agent_task_brief_cmd.set_defaults(func=cmd_agent_task_brief)

    agent_task_overlay_validate = agent_task_subparsers.add_parser(
        "overlay-validate",
        help="Validate the optional human-approved project Prompt overlay without changing project state.",
    )
    agent_task_overlay_validate.add_argument(
        "config", nargs="?", default="project.yaml", help="Path to project.yaml."
    )
    agent_task_overlay_validate.add_argument(
        "--file", default="00_governance/agent_prompt_overlay.json", help="Project-relative overlay JSON."
    )
    agent_task_overlay_validate.add_argument(
        "--role-id", default="chapter_author", help="Role whose overlay allowlist should be checked."
    )
    agent_task_overlay_validate.add_argument(
        "--json", action="store_true", help="Print machine-readable JSON."
    )
    agent_task_overlay_validate.set_defaults(func=cmd_agent_task_overlay_validate)

    agent_task_validate = agent_task_subparsers.add_parser("validate", help="Validate one AgentTaskManifest v4 contract.")
    agent_task_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    agent_task_validate.add_argument("task", help="Task id or manifest path.")
    agent_task_validate.add_argument("--strict", action="store_true", help="Check task type, lanes, schemas, commands, and hard boundaries.")
    agent_task_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    agent_task_validate.set_defaults(func=cmd_agent_task_validate)

    agent_result_validate = agent_task_subparsers.add_parser(
        "result-validate",
        help="Normalize and verify one Agent result without changing lifecycle or canonical state.",
    )
    agent_result_validate.add_argument(
        "config", nargs="?", default="project.yaml", help="Path to project.yaml."
    )
    agent_result_validate.add_argument("task", help="Task id or manifest path.")
    agent_result_validate.add_argument("--file", required=True, help="Declared Agent result path.")
    agent_result_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    agent_result_validate.set_defaults(func=cmd_agent_task_result_validate)

    agent_task_reconcile = agent_task_subparsers.add_parser(
        "reconcile",
        help="Reconcile explicit hash-proven parent-child task projections.",
    )
    agent_task_reconcile.add_argument(
        "config", nargs="?", default="project.yaml", help="Path to project.yaml."
    )
    agent_task_reconcile.add_argument("--chapter", type=int, required=True)
    agent_task_reconcile.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    agent_task_reconcile.set_defaults(func=cmd_agent_task_reconcile)

    agent_task_readiness = agent_task_subparsers.add_parser(
        "readiness",
        help="Check the installed Manifest v4, Chinese role, and single-process Agent protocol.",
    )
    agent_task_readiness.add_argument(
        "--repository", default=".", help="Engine repository root."
    )
    agent_task_readiness.add_argument(
        "--json", action="store_true", help="Print agent_data_pipeline_readiness_v3 JSON."
    )
    agent_task_readiness.set_defaults(func=cmd_agent_task_readiness)

    production = subparsers.add_parser("production", help="Inspect production experience orchestration state.")
    production_subparsers = production.add_subparsers(dest="production_command", required=True)

    production_status_cmd = production_subparsers.add_parser("status", help="Show stable GUI/API production status.")
    production_status_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    production_status_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    production_status_cmd.set_defaults(func=cmd_production_status)

    production_next_cmd = production_subparsers.add_parser("next", help="Show the highest-priority safe next action.")
    production_next_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    production_next_cmd.add_argument("--editorial", action="store_true", help="Include editorial role details in text output.")
    production_next_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    production_next_cmd.set_defaults(func=cmd_production_next)

    production_board_cmd = production_subparsers.add_parser("board", help="Show a chapter production board.")
    production_board_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    production_board_cmd.add_argument("--from", dest="from_chapter", type=int, help="First chapter to show.")
    production_board_cmd.add_argument("--to", dest="to_chapter", type=int, help="Last chapter to show.")
    production_board_cmd.add_argument("--editorial", action="store_true", help="Expand editorial role fan-out/fan-in details in text output.")
    production_board_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    production_board_cmd.set_defaults(func=cmd_production_board)

    production_loop_cmd = production_subparsers.add_parser("loop", help="Advance deterministic production steps until the next blocker.")
    production_loop_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    production_loop_cmd.add_argument("--max-steps", type=positive_int_arg, default=10, help="Maximum deterministic steps to execute.")
    production_loop_cmd.add_argument("--no-apply", action="store_true", default=True, help="Keep canonical apply/finalize disabled (default).")
    production_loop_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    production_loop_cmd.set_defaults(func=cmd_production_loop)

    intelligence = subparsers.add_parser("intelligence", help="Manage project-level Agent intelligence candidates.")
    intelligence_subparsers = intelligence.add_subparsers(dest="intelligence_command", required=True)

    intelligence_task = intelligence_subparsers.add_parser("task", help="Create a project/range Agent task and workbench candidate lane.")
    intelligence_task.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    intelligence_task.add_argument("--task-type", required=True, choices=INTELLIGENCE_TASK_TYPES)
    intelligence_task.add_argument("--input", dest="input_files", action="append", default=[], help="Declared input file; repeat as needed.")
    intelligence_task.add_argument("--chapter", type=positive_int_arg, help="Chapter number for chapter-scoped intelligence tasks.")
    intelligence_task.add_argument("--from-chapter", type=int)
    intelligence_task.add_argument("--to-chapter", type=int)
    intelligence_task.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    intelligence_task.set_defaults(func=cmd_intelligence_task)

    intelligence_validate = intelligence_subparsers.add_parser("validate", help="Strictly validate one intelligence candidate without canonical writes.")
    intelligence_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    intelligence_validate.add_argument("--task-type", required=True, choices=INTELLIGENCE_TASK_TYPES)
    intelligence_validate.add_argument("--file", required=True, help="Candidate JSON under 50_workbench/intelligence_candidates/.")
    intelligence_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    intelligence_validate.set_defaults(func=cmd_intelligence_validate)

    intelligence_approve = intelligence_subparsers.add_parser(
        "approve",
        help="Approve one validated authoritative Markdown design document.",
    )
    intelligence_approve.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    intelligence_approve.add_argument("--task-type", required=True, choices=DESIGN_INTELLIGENCE_TASK_TYPES)
    intelligence_approve.add_argument("--document", required=True, help="Validated design_document_v1 Markdown.")
    intelligence_approve.add_argument("--approved-by", required=True, choices=["human"])
    intelligence_approve.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    intelligence_approve.set_defaults(func=cmd_intelligence_approve)

    intelligence_compile_task = intelligence_subparsers.add_parser(
        "compile-task",
        help="Create a canonical_delta_v1 semantic compilation task for an approved design document.",
    )
    intelligence_compile_task.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    intelligence_compile_task.add_argument("--task-type", required=True, choices=DESIGN_INTELLIGENCE_TASK_TYPES)
    intelligence_compile_task.add_argument("--document", required=True)
    intelligence_compile_task.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    intelligence_compile_task.set_defaults(func=cmd_intelligence_compile_task)

    intelligence_compile_validate = intelligence_subparsers.add_parser(
        "compile-validate",
        help="Validate one design canonical delta against its approved Markdown source.",
    )
    intelligence_compile_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    intelligence_compile_validate.add_argument("--task-type", required=True, choices=DESIGN_INTELLIGENCE_TASK_TYPES)
    intelligence_compile_validate.add_argument("--document", required=True)
    intelligence_compile_validate.add_argument("--delta", required=True)
    intelligence_compile_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    intelligence_compile_validate.set_defaults(func=cmd_intelligence_compile_validate)

    intelligence_apply = intelligence_subparsers.add_parser("apply", help="Explicitly apply a validated canonical delta through a transaction.")
    intelligence_apply.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    intelligence_apply.add_argument("--task-type", required=True, choices=INTELLIGENCE_TASK_TYPES)
    intelligence_apply.add_argument("--document", help="Approved Markdown source for design tasks.")
    intelligence_apply.add_argument("--delta", required=True, help="Validated canonical_delta_v1 JSON.")
    intelligence_apply.add_argument("--approved-by", choices=["human"], help="Required for book and outline mutations.")
    intelligence_apply.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    intelligence_apply.set_defaults(func=cmd_intelligence_apply)

    character_expression = subparsers.add_parser(
        "character",
        help="Design and audit project-level character expression contracts.",
    )
    character_expression_subparsers = character_expression.add_subparsers(
        dest="character_expression_command",
        required=True,
    )
    character_design_task = character_expression_subparsers.add_parser(
        "design-task",
        help="Create a character_expression_profile_v1 Agent task.",
    )
    character_design_task.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_design_task.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_design_task.set_defaults(func=cmd_character_design_task)

    character_design_validate = character_expression_subparsers.add_parser(
        "design-validate",
        help="Validate a character expression profile without Bible writes.",
    )
    character_design_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_design_validate.add_argument("--file", required=True, help="character_expression_profile_v1 candidate JSON.")
    character_design_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_design_validate.set_defaults(func=cmd_character_design_validate)

    character_design_apply = character_expression_subparsers.add_parser(
        "design-apply",
        help="Apply a validated character expression profile with explicit human approval.",
    )
    character_design_apply.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_design_apply.add_argument("--document", required=True, help="Approved design_document_v1 Markdown.")
    character_design_apply.add_argument("--delta", required=True, help="Validated canonical_delta_v1 JSON.")
    character_design_apply.add_argument("--approved-by", required=True, choices=["human"])
    character_design_apply.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_design_apply.set_defaults(func=cmd_character_design_apply)

    character_audit_task = character_expression_subparsers.add_parser(
        "audit-task",
        help="Create an evidence-bound cross-chapter character performance review task.",
    )
    character_audit_task.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_audit_task.add_argument("--from-chapter", type=positive_int_arg, required=True)
    character_audit_task.add_argument("--to-chapter", type=positive_int_arg, required=True)
    character_audit_task.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_audit_task.set_defaults(func=cmd_character_audit_task)

    character_audit_validate = character_expression_subparsers.add_parser(
        "audit-validate",
        help="Validate review coverage, current hashes, and every cited source span.",
    )
    character_audit_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_audit_validate.add_argument("--file", required=True, help="character_expression_review_v1 candidate JSON.")
    character_audit_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_audit_validate.set_defaults(func=cmd_character_audit_validate)

    character_audit_apply = character_expression_subparsers.add_parser(
        "audit-apply",
        help="Archive a validated review under workbench without canonical story writes.",
    )
    character_audit_apply.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_audit_apply.add_argument("--file", required=True, help="Validated character expression review JSON.")
    character_audit_apply.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_audit_apply.set_defaults(func=cmd_character_audit_apply)

    character_samples_approve = character_expression_subparsers.add_parser(
        "samples-approve",
        help="Approve exact final-chapter spans as bounded positive/negative voice examples.",
    )
    character_samples_approve.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_samples_approve.add_argument("--file", required=True, help="character_voice_sample_approval_v1 JSON.")
    character_samples_approve.add_argument("--approved-by", required=True, choices=["human"])
    character_samples_approve.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_samples_approve.set_defaults(func=cmd_character_samples_approve)

    fanfiction = subparsers.add_parser("fanfiction", help="Manage first-class canon-aware fanfiction workflows.")
    fanfiction_subparsers = fanfiction.add_subparsers(dest="fanfiction_command", required=True)

    fanfiction_canon_task = fanfiction_subparsers.add_parser(
        "canon-task",
        help="Create an evidence-backed fanfiction canon extraction task.",
    )
    fanfiction_canon_task.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    fanfiction_canon_task.add_argument(
        "--input",
        dest="input_files",
        action="append",
        required=True,
        help="Declared source file under the project root; repeat as needed.",
    )
    fanfiction_canon_task.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    fanfiction_canon_task.set_defaults(func=cmd_fanfiction_canon_task)

    fanfiction_canon_validate = fanfiction_subparsers.add_parser(
        "canon-validate",
        help="Validate canon hashes, evidence spans, namespaces, and schema.",
    )
    fanfiction_canon_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    fanfiction_canon_validate.add_argument("--file", required=True, help="fanfiction_source_canon_v1 candidate JSON.")
    fanfiction_canon_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    fanfiction_canon_validate.set_defaults(func=cmd_fanfiction_canon_validate)

    fanfiction_canon_apply = fanfiction_subparsers.add_parser(
        "canon-apply",
        help="Apply validated source canon with explicit human approval.",
    )
    fanfiction_canon_apply.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    fanfiction_canon_apply.add_argument("--file", required=True, help="Validated fanfiction canon candidate JSON.")
    fanfiction_canon_apply.add_argument("--approved-by", required=True, choices=["human"])
    fanfiction_canon_apply.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    fanfiction_canon_apply.set_defaults(func=cmd_fanfiction_canon_apply)

    fanfiction_design_task = fanfiction_subparsers.add_parser(
        "design-task",
        help="Create a canon-aware fanfiction design task.",
    )
    fanfiction_design_task.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    fanfiction_design_task.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    fanfiction_design_task.set_defaults(func=cmd_fanfiction_design_task)

    fanfiction_design_validate = fanfiction_subparsers.add_parser(
        "design-validate",
        help="Validate divergence, voice, originality, crossover, and book design contracts.",
    )
    fanfiction_design_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    fanfiction_design_validate.add_argument("--file", required=True, help="fanfiction_design_candidate_v1 JSON.")
    fanfiction_design_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    fanfiction_design_validate.set_defaults(func=cmd_fanfiction_design_validate)

    fanfiction_design_apply = fanfiction_subparsers.add_parser(
        "design-apply",
        help="Apply validated fanfiction design with explicit human approval.",
    )
    fanfiction_design_apply.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    fanfiction_design_apply.add_argument("--document", required=True, help="Approved design_document_v1 Markdown.")
    fanfiction_design_apply.add_argument("--delta", required=True, help="Validated canonical_delta_v1 JSON.")
    fanfiction_design_apply.add_argument("--approved-by", required=True, choices=["human"])
    fanfiction_design_apply.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    fanfiction_design_apply.set_defaults(func=cmd_fanfiction_design_apply)

    fanfiction_status_cmd = fanfiction_subparsers.add_parser(
        "status",
        help="Show fanfiction workflow and advisory rights status.",
    )
    fanfiction_status_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    fanfiction_status_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    fanfiction_status_cmd.set_defaults(func=cmd_fanfiction_status)

    publication = subparsers.add_parser("publication", help="Generate advisory publication reports and export finalized prose.")
    publication_subparsers = publication.add_subparsers(dest="publication_command", required=True)

    publication_report = publication_subparsers.add_parser(
        "report",
        help="Write publication_risk_report_v1 without blocking export.",
    )
    publication_report.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    publication_report.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    publication_report.set_defaults(func=cmd_publication_report)

    publication_export = publication_subparsers.add_parser(
        "export",
        help="Export finalized chapters and generate a non-blocking risk report.",
    )
    publication_export.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    publication_export.add_argument("--output", help="Bundle path under 80_exports/.")
    publication_export.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    publication_export.set_defaults(func=cmd_publication_export)

    db = subparsers.add_parser("db", help="Manage the derived SQLite index.")
    db_subparsers = db.add_subparsers(dest="db_command", required=True)

    db_init = db_subparsers.add_parser("init", help="Create SQLite schema.")
    db_init.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    db_init.set_defaults(func=cmd_db_init)

    db_sync = db_subparsers.add_parser("sync", help="Sync derived SQLite rows from files.")
    db_sync.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    db_sync.set_defaults(func=cmd_db_sync)

    db_rebuild = db_subparsers.add_parser("rebuild", help="Delete and rebuild SQLite from files.")
    db_rebuild.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    db_rebuild.set_defaults(func=cmd_db_rebuild)

    db_status_cmd = db_subparsers.add_parser("status", help="Show SQLite index status.")
    db_status_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    db_status_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    db_status_cmd.set_defaults(func=cmd_db_status)

    db_query = db_subparsers.add_parser("query", help="Query a whitelisted SQLite table.")
    db_query.add_argument("config", help="Path to project.yaml.")
    db_query.add_argument("table", help="Table name, for example schema_meta.")
    db_query.add_argument("--limit", type=int, default=20, help="Maximum rows to return.")
    db_query.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    db_query.set_defaults(func=cmd_db_query)

    models = subparsers.add_parser("models", help="Manage optional local semantic models.")
    models_subparsers = models.add_subparsers(dest="models_command", required=True)

    models_list = models_subparsers.add_parser("list", help="List supported semantic model profiles.")
    models_list.set_defaults(func=cmd_models_list)

    models_install = models_subparsers.add_parser("install", help="Prepare or download a semantic model profile.")
    models_install.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    models_install.add_argument("--profile", default="bge-m3", choices=["bge-m3", "qwen3", "local-hash"], help="Model profile.")
    models_install.add_argument("--download", action="store_true", help="Explicitly download Hugging Face models.")
    models_install.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    models_install.set_defaults(func=cmd_models_install)

    models_verify = models_subparsers.add_parser("verify", help="Verify semantic model cache readiness.")
    models_verify.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    models_verify.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    models_verify.set_defaults(func=cmd_models_verify)

    models_cache_status = models_subparsers.add_parser("cache-status", help="Inspect the shared semantic model cache.")
    models_cache_status.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    models_cache_status.set_defaults(func=cmd_models_cache_status)

    vector_store = subparsers.add_parser("vector-store", help="Verify and rebuild pluggable vector indexes.")
    vector_subparsers = vector_store.add_subparsers(dest="vector_command", required=True)

    vector_verify = vector_subparsers.add_parser("verify", help="Verify vector store backend configuration.")
    vector_verify.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    vector_verify.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    vector_verify.set_defaults(func=cmd_vector_store_verify)

    vector_rebuild_cmd = vector_subparsers.add_parser("rebuild", help="Rebuild vector store from embedding file facts.")
    vector_rebuild_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    vector_rebuild_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    vector_rebuild_cmd.set_defaults(func=cmd_vector_store_rebuild)

    creative = subparsers.add_parser("creative", help="Manage creative brief, style playbooks, and Humanizer tasks.")
    creative_subparsers = creative.add_subparsers(dest="creative_command", required=True)

    creative_brief = creative_subparsers.add_parser("brief", help="Initialize or validate the canonical creative brief.")
    creative_brief.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    creative_brief.add_argument("--init", action="store_true", help="Create or refresh 10_bible/creative_brief.json.")
    creative_brief.add_argument("--validate", action="store_true", help="Validate the existing creative brief.")
    creative_brief.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    creative_brief.set_defaults(func=cmd_creative_brief)

    style_extract_cmd = creative_subparsers.add_parser("style-extract", help="Extract a style profile from sample chapters.")
    style_extract_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    style_extract_cmd.add_argument("--file", dest="sample_files", action="append", help="Sample chapter file. Repeat for multiple samples.")
    style_extract_cmd.add_argument("--name", default="sample", help="Profile name.")
    style_extract_cmd.add_argument("--source-project", default="", help="Project or authorization source for the sample.")
    style_extract_cmd.add_argument("--library", help="Optional existing style profile JSON to import or annotate.")
    style_extract_cmd.add_argument("--no-activate", action="store_true", help="Write the profile without making it current.")
    style_extract_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    style_extract_cmd.set_defaults(func=cmd_creative_style_extract)

    humanize_task_cmd = creative_subparsers.add_parser("humanize-task", help="Generate a Humanizer v4 workbench task.")
    humanize_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    humanize_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    humanize_task_cmd.add_argument("--source", choices=["draft", "repair-candidate"], default="draft", help="Source lane.")
    humanize_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    humanize_task_cmd.set_defaults(func=cmd_creative_humanize_task)

    humanize_check_cmd = creative_subparsers.add_parser("humanize-check", help="Check a Humanizer v4 candidate.")
    humanize_check_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    humanize_check_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    humanize_check_cmd.add_argument("--file", required=True, help="Candidate file to check.")
    humanize_check_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    humanize_check_cmd.set_defaults(func=cmd_creative_humanize_check)

    humanize_semantic_task_cmd = creative_subparsers.add_parser(
        "humanize-semantic-task",
        help="Generate an independent Humanizer semantic-preservation review task.",
    )
    humanize_semantic_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    humanize_semantic_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    humanize_semantic_task_cmd.add_argument("--file", help="Humanized candidate path; defaults to the managed candidate.")
    humanize_semantic_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    humanize_semantic_task_cmd.set_defaults(func=cmd_creative_humanize_semantic_task)

    humanize_semantic_validate_cmd = creative_subparsers.add_parser(
        "humanize-semantic-validate",
        help="Validate Humanizer source/candidate semantic-preservation evidence.",
    )
    humanize_semantic_validate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    humanize_semantic_validate_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    humanize_semantic_validate_cmd.add_argument("--file", required=True, help="Semantic review JSON path.")
    humanize_semantic_validate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    humanize_semantic_validate_cmd.set_defaults(func=cmd_creative_humanize_semantic_validate)

    expand_task_cmd = creative_subparsers.add_parser("expand-task", help="Generate a content expansion workbench task.")
    expand_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    expand_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    expand_task_cmd.add_argument(
        "--source",
        choices=["draft", "repair-candidate", "agent-draft"],
        default="draft",
        help="Source lane.",
    )
    expand_task_cmd.add_argument(
        "--type",
        dest="expansion_types",
        choices=["scene", "dialogue", "psychology", "action", "transition"],
        action="append",
        help="Expansion type to require. Repeat for multiple types; omitted means all.",
    )
    expand_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    expand_task_cmd.set_defaults(func=cmd_creative_expand_task)

    expand_check_cmd = creative_subparsers.add_parser("expand-check", help="Check a content expansion candidate.")
    expand_check_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    expand_check_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    expand_check_cmd.add_argument("--file", required=True, help="Candidate file to check.")
    expand_check_cmd.add_argument(
        "--type",
        dest="expansion_types",
        choices=["scene", "dialogue", "psychology", "action", "transition"],
        action="append",
        help="Expansion type to require. Repeat for multiple types; omitted means all.",
    )
    expand_check_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    expand_check_cmd.set_defaults(func=cmd_creative_expand_check)

    quality = subparsers.add_parser("quality", help="Manage semantic reader-payoff and craft-structure checks.")
    quality_subparsers = quality.add_subparsers(dest="quality_command", required=True)

    quality_contract_cmd = quality_subparsers.add_parser(
        "contract",
        help="Compile the effective market + story facets + phase + approved-baseline quality contract.",
    )
    quality_contract_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    quality_contract_cmd.add_argument("--chapter", type=positive_int_arg, required=True)
    quality_contract_cmd.add_argument(
        "--compare-market",
        action="append",
        default=[],
        help="Add a non-blocking compatibility market view. Repeat for multiple markets.",
    )
    quality_contract_cmd.add_argument(
        "--explain",
        action="store_true",
        help="Print merge precedence, overridden fields, compatibility observations, and blocking policy.",
    )
    quality_contract_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    quality_contract_cmd.set_defaults(func=cmd_quality_contract)

    story_profile_cmd = quality_subparsers.add_parser(
        "story-profile",
        help="Compile selected story facets and report conflicts requiring human resolution.",
    )
    story_profile_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    story_profile_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    story_profile_cmd.set_defaults(func=cmd_quality_story_profile)

    baseline_approve_cmd = quality_subparsers.add_parser(
        "baseline-approve",
        help="Explicitly approve one finalized chapter's prose-free craft fingerprint.",
    )
    baseline_approve_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    baseline_approve_cmd.add_argument("--chapter", type=positive_int_arg, required=True)
    baseline_approve_cmd.add_argument("--approved-by", required=True, help="Human approver identifier.")
    baseline_approve_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    baseline_approve_cmd.set_defaults(func=cmd_quality_baseline_approve)

    payoff_task_cmd = quality_subparsers.add_parser(
        "payoff-task",
        help="Generate a bounded reader-payoff review task after gate pass.",
    )
    payoff_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    payoff_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    payoff_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    payoff_task_cmd.set_defaults(func=cmd_quality_payoff_task)

    payoff_validate_cmd = quality_subparsers.add_parser(
        "payoff-validate",
        help="Validate observed reader gain, cost, evidence spans, and structure repetition.",
    )
    payoff_validate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    payoff_validate_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    payoff_validate_cmd.add_argument("--file", required=True, help="Reader payoff review JSON path.")
    payoff_validate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    payoff_validate_cmd.set_defaults(func=cmd_quality_payoff_validate)

    feedback_status_cmd = quality_subparsers.add_parser(
        "feedback-status",
        help="Inspect feedback lifecycle counts and optionally advance TTL for a target chapter.",
    )
    feedback_status_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    feedback_status_cmd.add_argument("--chapter", type=positive_int_arg, help="Optional target chapter for TTL evaluation.")
    feedback_status_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    feedback_status_cmd.set_defaults(func=cmd_quality_feedback_status)

    feedback_resolve_cmd = quality_subparsers.add_parser(
        "feedback-resolve",
        help="Resolve one feedback item with explicit evidence.",
    )
    feedback_resolve_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    feedback_resolve_cmd.add_argument("--id", required=True, dest="feedback_id", help="Stable feedback_id.")
    feedback_resolve_cmd.add_argument("--evidence", required=True, help="Short resolution evidence.")
    feedback_resolve_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    feedback_resolve_cmd.set_defaults(func=cmd_quality_feedback_resolve)

    feedback_suppress_cmd = quality_subparsers.add_parser(
        "feedback-suppress",
        help="Suppress one feedback item with an explicit reason.",
    )
    feedback_suppress_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    feedback_suppress_cmd.add_argument("--id", required=True, dest="feedback_id", help="Stable feedback_id.")
    feedback_suppress_cmd.add_argument("--evidence", required=True, help="Short suppression reason.")
    feedback_suppress_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    feedback_suppress_cmd.set_defaults(func=cmd_quality_feedback_suppress)

    rag = subparsers.add_parser("rag", help="Build and query local RAG context.")
    rag_subparsers = rag.add_subparsers(dest="rag_command", required=True)

    rag_build = rag_subparsers.add_parser("build", help="Build paragraph-aware chunks from final manuscripts.")
    rag_build.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    rag_build.add_argument("--max-chars", type=int, help="Maximum characters per chunk.")
    rag_build.add_argument("--overlap-chars", type=int, help="Trailing overlap characters between chunks.")
    rag_build.add_argument("--with-embeddings", action="store_true", help="Build semantic embedding file/index for canonical sources.")
    rag_build.set_defaults(func=cmd_rag_build)

    rag_query_cmd = rag_subparsers.add_parser("query", help="Query SQLite hybrid RAG chunks.")
    rag_query_cmd.add_argument("config", help="Path to project.yaml.")
    rag_query_cmd.add_argument("query", help="Search query.")
    rag_query_cmd.add_argument("--top-k", type=int, help="Number of hits to return.")
    rag_query_cmd.add_argument("--candidate-pool", type=int, help="Candidate pool size.")
    rag_query_cmd.add_argument("--chapter", type=int, help="Target/current chapter for graph/TCS filtering.")
    rag_query_cmd.add_argument("--semantic", action="store_true", help="Enable semantic/memory retrieval fallback.")
    rag_query_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    rag_query_cmd.set_defaults(func=cmd_rag_query)

    rag_context = rag_subparsers.add_parser("context", help="Write next_plot_context.md.")
    rag_context.add_argument("config", help="Path to project.yaml.")
    rag_context.add_argument("--chapter", type=int, help="Target chapter number.")
    rag_context.add_argument("--query", help="Override context query.")
    rag_context.add_argument("--top-k", type=int, help="Number of hits to include.")
    rag_context.add_argument("--semantic", action="store_true", help="Enable semantic/memory retrieval fallback.")
    rag_context.set_defaults(func=cmd_rag_context)

    graph = subparsers.add_parser("graph", help="Validate, update, and check the story graph.")
    graph_subparsers = graph.add_subparsers(dest="graph_command", required=True)

    graph_validate = graph_subparsers.add_parser("validate", help="Validate story_graph.json.")
    graph_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    graph_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    graph_validate.set_defaults(func=cmd_graph_validate)

    graph_update = graph_subparsers.add_parser("update", help="Update story graph from a finalized chapter.")
    graph_update.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    graph_update.add_argument("--chapter", type=int, required=True, help="Finalized chapter number.")
    graph_update.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    graph_update.set_defaults(func=cmd_graph_update)

    graph_check = graph_subparsers.add_parser("check", help="Write graph conflict report.")
    graph_check.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    graph_check.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    graph_check.set_defaults(func=cmd_graph_check)

    graph_retrieve = graph_subparsers.add_parser("retrieve", help="Run local graph traversal retrieval.")
    graph_retrieve.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    graph_retrieve.add_argument("--query", required=True, help="Graph retrieval query.")
    graph_retrieve.add_argument("--chapter", type=int, required=True, help="Target/current chapter number.")
    graph_retrieve.add_argument("--top-k", type=int, default=12, help="Maximum graph hits.")
    graph_retrieve.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    graph_retrieve.set_defaults(func=cmd_graph_retrieve)

    memory = subparsers.add_parser("memory", help="Validate narrative memory and Codex semantic extraction tasks.")
    memory_subparsers = memory.add_subparsers(dest="memory_command", required=True)

    memory_validate = memory_subparsers.add_parser("validate", help="Validate canonical Memory v2 files.")
    memory_validate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    memory_validate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    memory_validate.set_defaults(func=cmd_memory_validate)

    memory_tcs = memory_subparsers.add_parser("tcs", help="Build a Temporal Context State snapshot.")
    memory_tcs.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    memory_tcs.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    memory_tcs.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    memory_tcs.set_defaults(func=cmd_memory_tcs)

    memory_compress = memory_subparsers.add_parser("compress", help="Compress scene/chapter/arc memory into canonical memory.")
    memory_compress.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    memory_compress.add_argument("--scope", required=True, choices=["chapter", "arc", "volume"], help="Compression scope.")
    memory_compress.add_argument("--from-chapter", type=int, required=True, help="First chapter in range.")
    memory_compress.add_argument("--to-chapter", type=int, required=True, help="Last chapter in range.")
    memory_compress.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    memory_compress.set_defaults(func=cmd_memory_compress)

    character_check_cmd = memory_subparsers.add_parser("character-check", help="Check draft against Character Memory Cards.")
    character_check_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    character_check_cmd.add_argument("--chapter", type=int, required=True, help="Chapter number.")
    character_check_cmd.add_argument("--file", required=True, help="Draft/candidate file under project root.")
    character_check_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    character_check_cmd.set_defaults(func=cmd_memory_character_check)

    tcs_transition_cmd = memory_subparsers.add_parser("tcs-transition", help="Advance TCS state machine from a finalized chapter.")
    tcs_transition_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    tcs_transition_cmd.add_argument("--chapter", type=int, required=True, help="Finalized chapter number.")
    tcs_transition_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    tcs_transition_cmd.set_defaults(func=cmd_memory_tcs_transition)

    tcs_validate_cmd = memory_subparsers.add_parser("tcs-validate", help="Validate TCS state for future-fact leakage.")
    tcs_validate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    tcs_validate_cmd.add_argument("--chapter", type=int, required=True, help="TCS chapter number.")
    tcs_validate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    tcs_validate_cmd.set_defaults(func=cmd_memory_tcs_validate)

    open_book_cmd = subparsers.add_parser("open-book", help="Confirm five opening items and write governance files.")
    open_book_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    open_book_cmd.add_argument("--target-audience", help="Confirmed target reader.")
    open_book_cmd.add_argument("--writing-style", help="Confirmed writing style.")
    open_book_cmd.add_argument("--forbidden-zone", action="append", help="Confirmed forbidden reader experience. Can repeat.")
    open_book_cmd.add_argument("--automation-level", help="Confirmed automation level.")
    open_book_cmd.add_argument("--target-scale", help="Confirmed target scale.")
    open_book_cmd.add_argument("--interactive", action="store_true", help="Create a project first when project.yaml is missing.")
    open_book_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    open_book_cmd.set_defaults(func=cmd_open_book)

    plan = subparsers.add_parser("plan-chapter", help="Generate a chapter card.")
    plan.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    plan.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    plan.add_argument("--overwrite", action="store_true", help="Overwrite existing chapter card.")
    plan.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    plan.set_defaults(func=cmd_plan_chapter)

    beat = subparsers.add_parser("beat", help="Generate a beat sheet from a chapter card.")
    beat.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    beat.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    beat.add_argument("--overwrite", action="store_true", help="Overwrite existing beat sheet.")
    beat.add_argument("--auto-plan", action="store_true", help="Create the chapter card if missing.")
    beat.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    beat.set_defaults(func=cmd_beat)

    continue_cmd = subparsers.add_parser("continue-write", help="Generate the next chapter writing task and draft workflow artifacts.")
    continue_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    continue_cmd.add_argument("--chapter", type=int, help="Target chapter number. Defaults to next chapter.")
    continue_cmd.add_argument("--overwrite", action="store_true", help="Overwrite generated draft/card/beat artifacts.")
    continue_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    continue_cmd.set_defaults(func=cmd_continue_write)

    batch = subparsers.add_parser("batch-write", help="Safely schedule multiple chapter continuations.")
    batch.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    batch.add_argument("--chapters", type=int, required=True, help="Number of chapters to attempt.")
    batch.add_argument("--stop-on-gate-failure", action="store_true", help="Stop at first blocking gate failure.")
    batch.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    batch.set_defaults(func=cmd_batch_write)

    auto_write = subparsers.add_parser("auto-write", help="Plan, run, and inspect the persistent auto-write scheduler.")
    auto_subparsers = auto_write.add_subparsers(dest="auto_command", required=True)

    auto_plan = auto_subparsers.add_parser("plan", help="Create or inspect the auto-write plan.")
    auto_plan.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    auto_plan.add_argument("--start-chapter", type=positive_int_arg, help="Chapter where the scheduler should start.")
    auto_plan.add_argument("--overwrite", action="store_true", help="Reset an existing auto-write state file.")
    auto_plan.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    auto_plan.set_defaults(func=cmd_auto_write_plan)

    auto_run = auto_subparsers.add_parser("run", help="Run auto-write until the next safe pause.")
    auto_run.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    auto_run.add_argument("--chapters", type=positive_int_arg, help="Maximum chapters/tasks to attempt in this invocation.")
    auto_run.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    auto_run.set_defaults(func=cmd_auto_write_run)

    auto_progress = auto_subparsers.add_parser("progress", help="Show auto-write state without mutating the project.")
    auto_progress.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    auto_progress.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    auto_progress.set_defaults(func=cmd_auto_write_progress)

    auto_report = auto_subparsers.add_parser("report", help="Write a readable auto-write progress report.")
    auto_report.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    auto_report.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    auto_report.set_defaults(func=cmd_auto_write_report)

    draft = subparsers.add_parser("draft", help="Submit and inspect Agent-authored drafts.")
    draft_subparsers = draft.add_subparsers(dest="draft_command", required=True)

    draft_submit = draft_subparsers.add_parser("submit", help="Submit a Codex/ClaudeCode draft into manuscript draft.")
    draft_submit.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    draft_submit.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    draft_submit.add_argument("--file", required=True, help="Agent draft/candidate file under controlled workbench lanes.")
    draft_submit.add_argument("--agent", default="codex", help="Submitting agent name, for example codex or claude.")
    draft_submit.add_argument("--overwrite", action="store_true", help="Replace an existing manuscript draft.")
    draft_submit.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    draft_submit.set_defaults(func=cmd_draft_submit)

    chapter = subparsers.add_parser("chapter", help="Manage chapter lifecycle transitions.")
    chapter_subparsers = chapter.add_subparsers(dest="chapter_command", required=True)

    chapter_finalize = chapter_subparsers.add_parser("finalize", help="Promote a gate-approved draft into final manuscript.")
    chapter_finalize.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    chapter_finalize.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    chapter_finalize.add_argument("--approved-by", required=True, help="Reviewer identity approving finalization.")
    chapter_finalize.add_argument("--overwrite", action="store_true", help="Replace an existing final manuscript.")
    chapter_finalize.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    chapter_finalize.set_defaults(func=cmd_chapter_finalize)

    chapter_semantic_task_cmd = chapter_subparsers.add_parser(
        "semantic-task",
        help="Create one unified evidence-bound semantic task for a finalized chapter.",
    )
    chapter_semantic_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    chapter_semantic_task_cmd.add_argument("--chapter", type=int, required=True, help="Finalized chapter number.")
    chapter_semantic_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    chapter_semantic_task_cmd.set_defaults(func=cmd_chapter_semantic_task)

    chapter_semantic_validate_cmd = chapter_subparsers.add_parser(
        "semantic-validate",
        help="Validate a canonical_delta_v1 chapter result against final prose and current state.",
    )
    chapter_semantic_validate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    chapter_semantic_validate_cmd.add_argument("--chapter", type=int, required=True, help="Finalized chapter number.")
    chapter_semantic_validate_cmd.add_argument("--file", required=True, help="Agent semantic JSON under 50_workbench/.")
    chapter_semantic_validate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    chapter_semantic_validate_cmd.set_defaults(func=cmd_chapter_semantic_validate)

    chapter_semantic_apply_cmd = chapter_subparsers.add_parser(
        "semantic-apply",
        help="Apply a validated semantic bundle and rebuild materialized views.",
    )
    chapter_semantic_apply_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    chapter_semantic_apply_cmd.add_argument("--chapter", type=int, required=True, help="Finalized chapter number.")
    chapter_semantic_apply_cmd.add_argument("--file", required=True, help="Validated semantic JSON under 50_workbench/.")
    chapter_semantic_apply_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    chapter_semantic_apply_cmd.set_defaults(func=cmd_chapter_semantic_apply)

    chapter_semantic_rebuild_cmd = chapter_subparsers.add_parser(
        "semantic-rebuild",
        help="Rebuild graph, current views, TCS, RAG, and SQLite from canonical semantic ledgers.",
    )
    chapter_semantic_rebuild_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    chapter_semantic_rebuild_cmd.add_argument("--through", type=int, required=True, help="Last continuous ledger chapter.")
    chapter_semantic_rebuild_cmd.add_argument("--approved-by", required=True, help="Reviewer approving materialized-view rebuild.")
    chapter_semantic_rebuild_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    chapter_semantic_rebuild_cmd.set_defaults(func=cmd_chapter_semantic_rebuild)

    chapter_close_cmd = chapter_subparsers.add_parser(
        "close",
        help="Close a materialized chapter and archive artifacts outside the active buffer.",
    )
    chapter_close_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    chapter_close_cmd.add_argument("--chapter", type=int, required=True, help="Chapter number.")
    chapter_close_cmd.add_argument("--approved-by", required=True, help="Reviewer identity approving close.")
    chapter_close_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    chapter_close_cmd.set_defaults(func=cmd_chapter_close)

    register_recovery_commands(subparsers)

    artifacts = subparsers.add_parser("artifacts", help="Inspect, compact, verify, and restore chapter audit artifacts.")
    artifacts_subparsers = artifacts.add_subparsers(dest="artifacts_command", required=True)
    artifacts_status_cmd = artifacts_subparsers.add_parser("status", help="Report loose files, archives, and committed snapshots.")
    artifacts_status_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    artifacts_status_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    artifacts_status_cmd.set_defaults(func=cmd_artifacts_status)

    artifacts_compact_cmd = artifacts_subparsers.add_parser("compact", help="Archive chapter or project-setup workbench artifacts.")
    artifacts_compact_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    artifacts_compact_cmd.add_argument("--scope", choices=["chapters", "project-setup"], default="chapters", help="Artifact lifecycle scope.")
    artifacts_compact_cmd.add_argument("--through", type=int, help="Archive chapters through this number when scope=chapters.")
    artifacts_compact_cmd.add_argument("--approved-by", help="Reviewer identity required for a mutating compaction.")
    artifacts_compact_cmd.add_argument("--dry-run", action="store_true", help="Only report candidates; do not write or delete.")
    artifacts_compact_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    artifacts_compact_cmd.set_defaults(func=cmd_artifacts_compact)

    artifacts_verify_cmd = artifacts_subparsers.add_parser("verify", help="Verify archive and entry hashes.")
    artifacts_verify_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    artifacts_verify_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    artifacts_verify_cmd.set_defaults(func=cmd_artifacts_verify)

    artifacts_restore_cmd = artifacts_subparsers.add_parser("restore", help="Restore one chapter archive without overwriting changed files.")
    artifacts_restore_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    artifacts_restore_cmd.add_argument("--chapter", type=int, required=True, help="Archived chapter number.")
    artifacts_restore_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    artifacts_restore_cmd.set_defaults(func=cmd_artifacts_restore)

    gate = subparsers.add_parser("gate-check", help="Run deterministic chapter gates.")
    gate.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    gate.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    gate.add_argument("--source", choices=["draft", "final"], default="draft", help="Chapter source.")
    gate.add_argument("--semantic", action="store_true", help="Run semantic continuity checks using TCS/memory.")
    gate.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    gate.set_defaults(func=cmd_gate_check)

    semantic_gate = subparsers.add_parser("gate", help="Manage Agent semantic gate review tasks.")
    semantic_gate_subparsers = semantic_gate.add_subparsers(dest="gate_command", required=True)

    semantic_gate_task_cmd = semantic_gate_subparsers.add_parser("semantic-task", help="Generate an evidence-backed semantic review task.")
    semantic_gate_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    semantic_gate_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    semantic_gate_task_cmd.add_argument("--source", choices=["draft", "final"], default="draft", help="Chapter source.")
    semantic_gate_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    semantic_gate_task_cmd.set_defaults(func=cmd_gate_semantic_task)

    semantic_gate_validate_cmd = semantic_gate_subparsers.add_parser("semantic-validate", help="Validate an Agent semantic review result.")
    semantic_gate_validate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    semantic_gate_validate_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    semantic_gate_validate_cmd.add_argument("--file", required=True, help="semantic_review_result.json under gate artifacts.")
    semantic_gate_validate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    semantic_gate_validate_cmd.set_defaults(func=cmd_gate_semantic_validate)

    semantic_gate_apply_cmd = semantic_gate_subparsers.add_parser("semantic-apply", help="Apply a validated semantic review to gate artifacts.")
    semantic_gate_apply_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    semantic_gate_apply_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    semantic_gate_apply_cmd.add_argument("--file", required=True, help="semantic_review_result.json under gate artifacts.")
    semantic_gate_apply_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    semantic_gate_apply_cmd.set_defaults(func=cmd_gate_semantic_apply)

    waiver = subparsers.add_parser("gate-waiver", help="Record a human waiver for PASS/P2 gate outcomes.")
    waiver.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    waiver.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    waiver.add_argument("--reason", required=True, help="Reason for waiver.")
    waiver.add_argument("--approved-by", default="human", help="Reviewer identity.")
    waiver.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    waiver.set_defaults(func=cmd_gate_waiver)

    pacing = subparsers.add_parser("pacing-review", help="Run deterministic pacing review.")
    pacing.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    pacing.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    pacing.add_argument("--source", choices=["draft", "final"], default="draft", help="Chapter source.")
    pacing.add_argument("--semantic-reader", action="store_true", help="Include reader experience pacing checks.")
    pacing.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    pacing.set_defaults(func=cmd_pacing_review)

    pacing_group = subparsers.add_parser("pacing", help="Manage semantic pacing agent tasks.")
    pacing_subparsers = pacing_group.add_subparsers(dest="pacing_command", required=True)

    semantic_pacing_task_cmd = pacing_subparsers.add_parser("semantic-task", help="Generate a semantic pacing Agent task.")
    semantic_pacing_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    semantic_pacing_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    semantic_pacing_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    semantic_pacing_task_cmd.set_defaults(func=cmd_pacing_semantic_task)

    semantic_pacing_validate_cmd = pacing_subparsers.add_parser("semantic-validate", help="Validate semantic pacing result JSON.")
    semantic_pacing_validate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    semantic_pacing_validate_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    semantic_pacing_validate_cmd.add_argument("--file", required=True, help="semantic_pacing_result.json under gate artifacts.")
    semantic_pacing_validate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    semantic_pacing_validate_cmd.set_defaults(func=cmd_pacing_semantic_validate)

    semantic_pacing_apply_cmd = pacing_subparsers.add_parser("semantic-apply", help="Apply semantic pacing result into gate artifacts.")
    semantic_pacing_apply_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    semantic_pacing_apply_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    semantic_pacing_apply_cmd.add_argument("--file", required=True, help="validated semantic_pacing_result.json under gate artifacts.")
    semantic_pacing_apply_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    semantic_pacing_apply_cmd.set_defaults(func=cmd_pacing_semantic_apply)

    repair_group = subparsers.add_parser("repair", help="Coordinate evidence-complete immutable repair rounds.")
    repair_subparsers = repair_group.add_subparsers(dest="repair_command", required=True)

    repair_status_cmd = repair_subparsers.add_parser("status", help="Show review barrier and repair-attempt status.")
    repair_status_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    repair_status_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    repair_status_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    repair_status_cmd.set_defaults(func=cmd_repair_status)

    repair_synthesis_task_cmd = repair_subparsers.add_parser("synthesis-task", help="Freeze reviews and create a repair-plan synthesis task.")
    repair_synthesis_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    repair_synthesis_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    repair_synthesis_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    repair_synthesis_task_cmd.set_defaults(func=cmd_repair_synthesis_task)

    repair_synthesis_validate_cmd = repair_subparsers.add_parser("synthesis-validate", help="Validate an evidence-complete repair plan.")
    repair_synthesis_validate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    repair_synthesis_validate_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    repair_synthesis_validate_cmd.add_argument("--file", required=True, help="Repair plan Markdown path.")
    repair_synthesis_validate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    repair_synthesis_validate_cmd.set_defaults(func=cmd_repair_synthesis_validate)

    repair_candidate_task_cmd = repair_subparsers.add_parser("candidate-task", help="Create the immutable repair-author task for a validated plan.")
    repair_candidate_task_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    repair_candidate_task_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    repair_candidate_task_cmd.add_argument("--agent", default="codex", help="Agent name for candidate task.")
    repair_candidate_task_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    repair_candidate_task_cmd.set_defaults(func=cmd_repair_candidate_task)

    research = subparsers.add_parser("research", help="Manage research inbox and canon promotion.")
    research_subparsers = research.add_subparsers(dest="research_command", required=True)

    research_add = research_subparsers.add_parser("add", help="Add a local note to research inbox.")
    research_add.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    research_add.add_argument("--file", required=True, help="Local note file to ingest.")
    research_add.add_argument("--title", help="Override research title.")
    research_add.add_argument("--source-url", help="Optional source URL for the note.")
    research_add.add_argument("--tag", action="append", default=[], help="Tag for the research item. Can repeat.")
    research_add.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    research_add.set_defaults(func=cmd_research_add)

    research_search = research_subparsers.add_parser("search", help="Search web references into research inbox.")
    research_search.add_argument("config", help="Path to project.yaml.")
    research_search.add_argument("query", help="Search query.")
    research_search.add_argument("--limit", type=int, help="Maximum result count.")
    research_search.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    research_search.set_defaults(func=cmd_research_search)

    research_gaps = research_subparsers.add_parser("gaps", help="Detect chapter/project knowledge gaps.")
    research_gaps.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    research_gaps.add_argument("--chapter", type=int, help="Target chapter number.")
    research_gaps.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    research_gaps.set_defaults(func=cmd_research_gaps)

    research_promote = research_subparsers.add_parser("promote", help="Promote an inbox item into canon.")
    research_promote.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    research_promote.add_argument("--item", required=True, help="Research item id or JSON path.")
    research_promote.add_argument("--approved-by", default="cli", help="Reviewer identity recorded in canon.")
    research_promote.add_argument("--review-note", help="Optional review note.")
    research_promote.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    research_promote.set_defaults(func=cmd_research_promote)

    impact = subparsers.add_parser("impact-analyze", help="Analyze research impact before promotion.")
    impact.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    impact.add_argument("--research-item", help="Research item id or JSON path.")
    impact.add_argument("--after-rollback", action="store_true", help="Analyze latest rollback impact.")
    impact.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    impact.set_defaults(func=cmd_impact_analyze)

    revision = subparsers.add_parser("revision", help="Manage rewrite branches and rollbacks.")
    revision_subparsers = revision.add_subparsers(dest="revision_command", required=True)

    revision_branch = revision_subparsers.add_parser("branch", help="Create a rewrite candidate for a chapter.")
    revision_branch.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    revision_branch.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    revision_branch.add_argument("--overwrite", action="store_true", help="Overwrite existing rewrite candidate.")
    revision_branch.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    revision_branch.set_defaults(func=cmd_revision_branch)

    revision_rollback = revision_subparsers.add_parser("rollback", help="Roll back to a chapter and detach later drafts.")
    revision_rollback.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    revision_rollback.add_argument("--to-chapter", type=int, required=True, help="Chapter to keep as current head.")
    revision_rollback.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    revision_rollback.set_defaults(func=cmd_revision_rollback)

    revision_snapshot = revision_subparsers.add_parser("snapshot", help="Create a lightweight project snapshot.")
    revision_snapshot.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    revision_snapshot.add_argument("--label", default="manual", help="Snapshot label.")
    revision_snapshot.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    revision_snapshot.set_defaults(func=cmd_revision_snapshot)

    revise = subparsers.add_parser("revise-outline", help="Recalculate outline anchors and mark dependent artifacts stale.")
    revise.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    revise.add_argument("--from-chapter", type=int, required=True, help="First affected chapter.")
    revise.add_argument("--change-description", required=True, help="Outline change description.")
    revise.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    revise.set_defaults(func=cmd_revise_outline)

    editorial = subparsers.add_parser("editorial", help="Generate and inspect editorial review artifacts.")
    editorial_subparsers = editorial.add_subparsers(dest="editorial_command", required=True)

    editorial_review_cmd = editorial_subparsers.add_parser("review", help="Review one chapter.")
    editorial_review_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    editorial_review_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    editorial_review_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    editorial_review_cmd.set_defaults(func=cmd_editorial_review)

    editorial_batch = editorial_subparsers.add_parser("batch-review", help="Review a chapter range.")
    editorial_batch.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    editorial_batch.add_argument("--chapter-start", type=int, required=True, help="First chapter.")
    editorial_batch.add_argument("--chapter-end", type=int, required=True, help="Last chapter.")
    editorial_batch.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    editorial_batch.set_defaults(func=cmd_editorial_batch_review)

    editorial_status_cmd = editorial_subparsers.add_parser("status", help="Show editorial review status.")
    editorial_status_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    editorial_status_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    editorial_status_cmd.set_defaults(func=cmd_editorial_status)

    editorial_submit = editorial_subparsers.add_parser("submit-review", help="Validate and submit one role review result.")
    editorial_submit.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    editorial_submit.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    editorial_submit.add_argument("--role", required=True, help="Editorial role id.")
    editorial_submit.add_argument("--file", required=True, help="Role result JSON under editorial_reviews/results/.")
    editorial_submit.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    editorial_submit.set_defaults(func=cmd_editorial_submit_review)

    editorial_aggregate_cmd = editorial_subparsers.add_parser("aggregate", help="Aggregate accepted role review results.")
    editorial_aggregate_cmd.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    editorial_aggregate_cmd.add_argument("--chapter", type=int, required=True, help="Target chapter number.")
    editorial_aggregate_cmd.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    editorial_aggregate_cmd.set_defaults(func=cmd_editorial_aggregate)

    editorial_need = editorial_subparsers.add_parser("need-human", help="Escalate editorial status to human review.")
    editorial_need.add_argument("config", nargs="?", default="project.yaml", help="Path to project.yaml.")
    editorial_need.add_argument("--chapter", type=int, help="Optional chapter number for the human review request.")
    editorial_need.add_argument("--reason", help="Reason for human review escalation.")
    editorial_need.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    editorial_need.set_defaults(func=cmd_editorial_need_human)

    for command in (
        init,
        db_init,
        db_sync,
        db_rebuild,
        models_install,
        vector_rebuild_cmd,
        creative_brief,
        style_extract_cmd,
        payoff_task_cmd,
        payoff_validate_cmd,
        humanize_task_cmd,
        humanize_check_cmd,
        humanize_semantic_task_cmd,
        humanize_semantic_validate_cmd,
        expand_task_cmd,
        expand_check_cmd,
        rag_build,
        rag_query_cmd,
        rag_context,
        graph_update,
        graph_check,
        memory_validate,
        memory_tcs,
        memory_compress,
        character_check_cmd,
        tcs_transition_cmd,
        open_book_cmd,
        plan,
        beat,
        continue_cmd,
        batch,
        auto_plan,
        auto_run,
        auto_report,
        draft_submit,
        chapter_finalize,
        chapter_semantic_task_cmd,
        chapter_semantic_validate_cmd,
        chapter_semantic_apply_cmd,
        chapter_semantic_rebuild_cmd,
        chapter_close_cmd,
        artifacts_compact_cmd,
        artifacts_restore_cmd,
        gate,
        semantic_gate_task_cmd,
        semantic_gate_validate_cmd,
        semantic_gate_apply_cmd,
        waiver,
        pacing,
        semantic_pacing_task_cmd,
        semantic_pacing_validate_cmd,
        semantic_pacing_apply_cmd,
        repair_synthesis_task_cmd,
        repair_synthesis_validate_cmd,
        repair_candidate_task_cmd,
        research_add,
        research_search,
        research_gaps,
        research_promote,
        impact,
        revision_branch,
        revision_rollback,
        revision_snapshot,
        revise,
        editorial_review_cmd,
        editorial_batch,
        editorial_status_cmd,
        editorial_submit,
        editorial_aggregate_cmd,
        editorial_need,
        production_loop_cmd,
        agent_result_validate,
        intelligence_task,
        intelligence_validate,
        intelligence_apply,
        character_design_task,
        character_design_validate,
        character_design_apply,
        character_audit_task,
        character_audit_validate,
        character_audit_apply,
        character_samples_approve,
        baseline_approve_cmd,
        feedback_status_cmd,
        feedback_resolve_cmd,
        feedback_suppress_cmd,
        fanfiction_canon_task,
        fanfiction_canon_validate,
        fanfiction_canon_apply,
        fanfiction_design_task,
        fanfiction_design_validate,
        fanfiction_design_apply,
        publication_report,
        publication_export,
        benchmark_init,
        benchmark_record,
        benchmark_technical,
        benchmark_rag,
        benchmark_rag_run,
        benchmark_rag_template,
        benchmark_rag_production,
        benchmark_source,
        benchmark_blind_pack,
        benchmark_blind_template,
        benchmark_blind_submit,
        benchmark_blind_aggregate,
        benchmark_report,
        benchmark_compare,
    ):
        command.set_defaults(mutates_project=True)

    return parser


def add_scale_arguments(command: argparse.ArgumentParser) -> None:
    command.add_argument("--scale-preset", choices=sorted(SCALE_PRESETS), help="Project scale preset.")
    command.add_argument("--target-total-characters", type=positive_int_arg, help="Target manuscript characters.")
    command.add_argument("--chapter-target-characters", type=positive_int_arg, help="Target characters per chapter.")
    command.add_argument("--chapter-soft-min", type=positive_int_arg, help="Soft minimum characters per chapter.")
    command.add_argument("--chapter-soft-max", type=positive_int_arg, help="Soft maximum characters per chapter.")
    command.add_argument("--volume-target-characters", type=positive_int_arg, help="Target characters per volume.")
    command.add_argument("--planning-horizon", type=positive_int_arg, help="Detailed rolling-outline horizon.")
    command.add_argument("--refill-threshold", type=positive_int_arg, help="Remaining plans that trigger refill.")


def positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def non_negative_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("value must be a non-negative integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return parsed


def score_arg(value: str) -> int:
    parsed = positive_int_arg(value)
    if parsed > 10:
        raise argparse.ArgumentTypeError("score must be between 1 and 10")
    return parsed


def scale_overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    preset = getattr(args, "scale_preset", None)
    if preset:
        overrides = json.loads(json.dumps({"length": SCALE_PRESETS[preset]["length"]}, ensure_ascii=False))
    length: dict[str, Any] = dict(overrides.get("length", {}))
    chapter: dict[str, Any] = dict(length.get("chapter", {}))
    volume: dict[str, Any] = dict(length.get("volume", {}))
    planning: dict[str, Any] = dict(length.get("planning", {}))
    target_total = getattr(args, "target_total_characters", None)
    if target_total is not None:
        length["target_total_characters"] = target_total
    for attr, key in (
        ("chapter_target_characters", "target_characters"),
        ("chapter_soft_min", "soft_min"),
        ("chapter_soft_max", "soft_max"),
    ):
        value = getattr(args, attr, None)
        if value is not None:
            chapter[key] = value
    if getattr(args, "volume_target_characters", None) is not None:
        volume["target_characters"] = args.volume_target_characters
    if getattr(args, "planning_horizon", None) is not None:
        planning["detailed_horizon"] = args.planning_horizon
    if getattr(args, "refill_threshold", None) is not None:
        planning["refill_threshold"] = args.refill_threshold
    if not length and not chapter and not volume and not planning:
        return {}
    if chapter:
        length["chapter"] = chapter
    if volume:
        length["volume"] = volume
    if planning:
        length["planning"] = planning
    return {"length": length}


def prepared_init_context(args: argparse.Namespace) -> tuple[ConfigDocument, str | None]:
    prepared = getattr(args, "_prepared_init_context", None)
    if prepared:
        return prepared
    if getattr(args, "interactive", False):
        prepared = interactive_init_context(args)
        args._prepared_init_context = prepared
        return prepared

    template = getattr(args, "template", None)
    config_path = getattr(args, "config", None)
    if not template and not config_path:
        template = "qidian-longform"
    config = load_project_config(config_path, template=template, cli_overrides=scale_overrides_from_args(args))
    prepared = (config, getattr(args, "output", None))
    args._prepared_init_context = prepared
    return prepared


def interactive_init_context(args: argparse.Namespace) -> tuple[ConfigDocument, str | None]:
    print("Longform project creation wizard")
    title = prompt_text("小说标题", "未命名长篇小说")
    slug = prompt_text("项目 slug", safe_slug(title))
    output = prompt_text("输出目录", getattr(args, "output", None) or f"novels/{slug}")
    template = prompt_text("模板风格", getattr(args, "template", None) or "qidian-longform")
    scale = prompt_scale_choice()
    if scale == "custom":
        length = prompt_custom_length()
    else:
        length = json.loads(json.dumps(SCALE_PRESETS[scale]["length"], ensure_ascii=False))

    overrides = {
        "project": {
            "title": title,
            "slug": slug,
            "root_dir": output,
        },
        "length": length,
    }
    config = load_project_config(template=template, cli_overrides=overrides)
    print_creation_summary(config, output, template, scale)
    if not prompt_confirm("确认创建项目并写入配置？", default=True):
        raise ValueError("Interactive project creation cancelled.")
    return config, output


def prompt_text(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def prompt_positive_int(label: str, default: int) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            parsed = int(raw)
        except ValueError:
            print("请输入正整数。")
            continue
        if parsed > 0:
            return parsed
        print("请输入正整数。")


def prompt_scale_choice() -> str:
    options = [
        ("million", "百万字", "100 万正文字符 / 约 333 章 / 滚动细纲"),
        ("standard", "标准长篇", "150 万正文字符 / 约 500 章 / 滚动细纲"),
        ("extended", "超长篇", "200 万正文字符 / 约 667 章 / 正式支持上限"),
        ("custom", "自定义", "手动填写总字符、单章与单卷容量，不锁定总章数"),
    ]
    print("规模预设:")
    for index, (_, label, detail) in enumerate(options, start=1):
        print(f"  {index}. {label} - {detail}")
    aliases = {
        "1": "million",
        "百万字": "million",
        "百万": "million",
        "2": "standard",
        "标准长篇": "standard",
        "标准": "standard",
        "3": "extended",
        "超长篇": "extended",
        "超长": "extended",
        "4": "custom",
        "自定义": "custom",
    }
    valid = {key for key, _, _ in options}
    while True:
        raw = input("请选择规模预设 [standard]: ").strip()
        if not raw:
            return "standard"
        normalized = aliases.get(raw, raw)
        if normalized in valid:
            return normalized
        print("请输入 million、standard、extended、custom 或对应序号。")


def prompt_custom_length() -> dict[str, Any]:
    total_characters = prompt_positive_int("目标正文字符数", 1_000_000)
    target = prompt_positive_int("单章目标字符数", 3000)
    minimum = prompt_positive_int("单章软下限", max(1, int(target * 0.8)))
    maximum = prompt_positive_int("单章软上限", max(target, int(target * 1.2)))
    volume_target = prompt_positive_int("单卷目标字符数", 200_000)
    horizon = prompt_positive_int("滚动细纲章数", 20)
    refill = prompt_positive_int("细纲补充阈值", 8)
    return {
        "metric": "content_characters_v1",
        "target_total_characters": total_characters,
        "completion_tolerance": [0.90, 1.10],
        "chapter": {
            "target_characters": target,
            "soft_min": minimum,
            "soft_max": maximum,
            "hard_min": max(1, int(minimum * 0.8)),
            "hard_max": max(maximum, int(maximum * 1.2)),
        },
        "volume": {"target_characters": volume_target},
        "planning": {"mode": "rolling", "detailed_horizon": horizon, "refill_threshold": refill},
    }


def prompt_confirm(label: str, *, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def safe_slug(value: str) -> str:
    safe = []
    last_was_separator = False
    for char in value.lower():
        if char.isascii() and (char.isalnum() or char in {"-", "_"}):
            safe.append(char)
            last_was_separator = False
        elif not last_was_separator:
            safe.append("_")
            last_was_separator = True
    slug = "".join(safe).strip("_-")
    return slug or "longform_novel"


def print_creation_summary(config: ConfigDocument, output: str, template: str, scale: str) -> None:
    project = config.data["project"]
    length = config.data["length"]
    chapter = length["chapter"]
    forecast = compile_length_forecast(length)
    scale_label = "自定义" if scale == "custom" else SCALE_PRESETS[scale]["label"]
    print("")
    print("项目创建摘要")
    print(f"Title: {project['title']}")
    print(f"Slug: {project['slug']}")
    print(f"Output: {output}")
    print(f"Template: {template}")
    print(f"Scale: {scale_label}")
    print(f"Target content characters: {forecast.target_total_characters}")
    print(
        "Forecast chapters: "
        f"{forecast.estimated_chapters} "
        f"({forecast.minimum_reasonable_chapters}-{forecast.maximum_reasonable_chapters})"
    )
    print(f"Characters per chapter: {chapter['target_characters']} ({chapter['soft_min']}-{chapter['soft_max']})")
    print(f"Forecast volumes: {forecast.estimated_volumes}")
    print(f"Support: {forecast.support_status}")
    print("")


def cmd_validate_config(args: argparse.Namespace) -> int:
    config = load_project_config(args.config, template=args.template, cli_overrides=scale_overrides_from_args(args))
    print("OK: configuration is valid")
    title = config.data["project"]["title"]
    forecast = compile_length_forecast(config.data["length"])
    print(f"Project: {title}")
    print(
        f"Scale: {forecast.target_total_characters} target characters / "
        f"about {forecast.estimated_chapters} chapters / {forecast.support_status}"
    )
    if args.explain:
        print("Fields:")
        for item in config_field_registry(config):
            print(
                f"  - {item['path']}: value={json.dumps(item['value'], ensure_ascii=False)}; "
                f"type={item['type']}; source={item['source']}; owner={item['owner']}"
            )
    return 0


def cmd_init_project(args: argparse.Namespace) -> int:
    config, output = prepared_init_context(args)
    result = init_project(config, output=output, force=args.force)
    print(f"OK: project initialized at {result.root}")
    print(f"Project config: {result.project_config}")
    print(f"Created directories: {len(result.created_dirs)}")
    print(f"Created files: {len(result.created_files)}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_project_config(config_path)
    root = resolve_project_root(config)
    revision_status = project_status(config)
    payload = {
        "title": config.data["project"]["title"],
        "slug": config.data["project"]["slug"],
        "root": str(root),
        "exists": root.exists(),
        "project_config": str(config_path),
        "length_forecast": compile_length_forecast(config.data["length"]).to_dict(),
        "state_status": revision_status.state_status,
        "current_chapter": revision_status.current_chapter,
        "last_finalized_chapter": revision_status.last_finalized_chapter,
        "stale": revision_status.stale,
        "stale_chapters": revision_status.stale_chapters,
        "chapter_states": [asdict(chapter) for chapter in revision_status.chapters],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Project: {payload['title']} ({payload['slug']})")
        print(f"Root: {payload['root']}")
        print(f"Exists: {payload['exists']}")
        forecast = payload["length_forecast"]
        print(
            f"Scale: {forecast['target_total_characters']} target characters / "
            f"about {forecast['estimated_chapters']} chapters / {forecast['support_status']}"
        )
        print(f"State: {payload['state_status']}")
        print(f"Current chapter: {payload['current_chapter']}")
        print(f"Last finalized chapter: {payload['last_finalized_chapter']}")
        print(f"Stale: {', '.join(payload['stale']) if payload['stale'] else 'none'}")
        if payload["chapter_states"]:
            print("Chapter states:")
            for chapter in payload["chapter_states"]:
                statuses = ", ".join(chapter["statuses"])
                print(f"  - ch{chapter['chapter_number']:03d}: {chapter['status']} ({statuses})")
    return 0


def cmd_book_completion_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = completion_status(config).to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Ready for approval: {str(payload['ready_for_human_approval']).lower()}")
        print(f"Approved: {str(payload['approved']).lower()}")
        print(f"Content characters: {payload['total_content_characters']}")
        print(f"Completion range: {payload['completion_range'][0]}-{payload['completion_range'][1]}")
        print(f"Length status: {payload['length_status']}")
        print(f"Recommended action: {payload['recommended_action']}")
        print(f"Blockers: {', '.join(payload['blockers']) or 'none'}")
        print(f"Next command: {payload['next_command'] or 'none'}")
    return 0 if payload["ready_for_human_approval"] or payload["approved"] else 2


def cmd_book_completion_approve(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = approve_completion(
        config,
        approved_by=args.approved_by,
        ending_summary=args.ending_summary,
    ).to_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: whole-book completion explicitly approved")
        print(f"Content characters: {payload['total_content_characters']}")
        print(f"Latest final chapter: {payload['latest_final_chapter']}")
    return 0


def _print_distribution_payload(payload: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_status(payload))


def cmd_skills_install(args: argparse.Namespace) -> int:
    payload = install_skills(args.tool, force=args.force)
    _print_distribution_payload(payload, json_output=args.json)
    return 0


def cmd_skills_status(args: argparse.Namespace) -> int:
    payload = skill_status_payload(args.tool)
    _print_distribution_payload(payload, json_output=args.json)
    return 0 if all(result["state"] == "current" for result in payload["results"]) else 1


def cmd_skills_update(args: argparse.Namespace) -> int:
    payload = update_skills(args.tool)
    _print_distribution_payload(payload, json_output=args.json)
    return 0


def cmd_skills_uninstall(args: argparse.Namespace) -> int:
    payload = uninstall_skills(args.tool, confirmed=args.yes)
    _print_distribution_payload(payload, json_output=args.json)
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = doctor_payload(args.tool, project=args.project)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_doctor(payload))
    return 0 if payload["ok"] else 1


def cmd_release_check(args: argparse.Namespace) -> int:
    payload = check_release_readiness(
        args.repository,
        tag=args.tag,
        run_contracts=not args.skip_contracts,
        check_remote=args.check_remote,
        allow_detached=args.allow_detached,
        channel=args.channel,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_release_readiness(payload))
    return 0 if payload["ok"] else 1


def cmd_benchmark_init(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = init_benchmark(
        config,
        run_id=args.run_id,
        host_product=args.host_product,
        chapters=args.chapters,
        scenario_id=args.scenario_id,
        scenario_file=args.scenario_file,
        agent_model=args.agent_model,
        host_version=args.host_version,
        workflow_version=args.workflow_version,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: benchmark run initialized")
        print(f"Run: {result.run_id}")
        print(f"Records: {result.records_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_benchmark_record(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = record_benchmark_chapter(
        config,
        run_id=args.run_id,
        chapter_number=args.chapter,
        scores={
            "continuity": args.continuity,
            "character_consistency": args.character_consistency,
            "foreshadowing_control": args.foreshadowing_control,
            "pacing": args.pacing,
            "reader_payoff": args.reader_payoff,
            "ai_taste": args.ai_taste,
        },
        gate_passed=args.gate_passed,
        repair_count=args.repair_count,
        need_human_count=args.need_human_count,
        context_file_count=args.context_file_count,
        context_character_count=args.context_character_count,
        p0_contradiction_count=args.p0_contradiction_count,
        canonical_pollution_count=args.canonical_pollution_count,
        judge_ids=args.judge,
        character_drift=args.character_drift,
        foreshadowing_leaks=args.foreshadowing_leak,
        ai_taste_issues=args.ai_taste_issue,
        notes=args.notes,
        fanfiction_scores={
            "canon_fidelity": args.canon_fidelity,
            "ooc_control": args.ooc_control,
            "original_contribution": args.original_contribution,
            "divergence_causality": args.divergence_causality,
            "source_prose_originality": args.source_prose_originality,
            "crossover_consistency": args.crossover_consistency,
        } if any(
            value is not None
            for value in (
                args.canon_fidelity,
                args.ooc_control,
                args.original_contribution,
                args.divergence_causality,
                args.source_prose_originality,
                args.crossover_consistency,
            )
        ) else None,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: benchmark chapter recorded")
        print(f"Run: {result.run_id}")
        print(f"Chapter: {result.chapter_number}")
        print(f"Complete: {result.complete}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_benchmark_technical_record(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = record_benchmark_chapter(
        config,
        run_id=args.run_id,
        chapter_number=args.chapter,
        scores=None,
        gate_passed=args.gate_passed,
        repair_count=args.repair_count,
        need_human_count=args.need_human_count,
        context_file_count=args.context_file_count,
        context_character_count=args.context_character_count,
        p0_contradiction_count=args.p0_contradiction_count,
        canonical_pollution_count=args.canonical_pollution_count,
        notes=args.notes,
        review_status="technical_pending",
        require_artifact_hashes=True,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: benchmark technical chapter record written")
        print(f"Run: {result.run_id}")
        print(f"Chapter: {result.chapter_number}")
        print(f"Complete: {result.complete}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_benchmark_rag_record(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = record_rag_benchmark(
        config,
        run_id=args.run_id,
        scale_chapters=args.scale_chapters,
        recall_at_k=args.recall_at_k,
        fact_error_rate=args.fact_error_rate,
        p95_query_ms=args.p95_query_ms,
        incremental_index_ms=args.incremental_index_ms,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: RAG scale evidence recorded")
        print(f"Evidence: {result.evidence_file}")
        print(f"Meets thresholds: {result.meets_thresholds}")
        for error in result.errors:
            print(f"- {error}")
    return 0 if result.meets_thresholds else 1


def cmd_benchmark_rag_scale_run(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = run_rag_scale_benchmark(
        config,
        scale_chapters=args.scale_chapters,
        backend=args.backend,
        query_count=args.query_count,
        top_k=args.top_k,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: RAG scale benchmark completed" if result.meets_thresholds else "ERROR: RAG scale benchmark failed")
        print(f"Dataset: {result.dataset_id}")
        print(f"Scale: {result.scale_chapters} chapters / {result.vector_count} vectors")
        print(f"Backend: {result.backend}")
        print(f"Recall@{result.top_k}: {result.recall_at_k:.3f}")
        print(f"Fact error rate: {result.fact_error_rate:.3f}")
        print(f"P95 query: {result.p95_query_ms:.3f} ms")
        print(f"Incremental index: {result.incremental_index_ms:.3f} ms")
        print(f"Result: {result.result_file}")
        for error in result.threshold_errors:
            print(f"- {error}")
    return 0 if result.meets_thresholds else 1


def cmd_benchmark_rag_production_template(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = write_rag_production_template(config, output=args.output)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: production RAG dataset template written")
        print(f"Template: {result.template_file}")
        print(f"Minimum queries: {result.minimum_query_count}")
        print(f"Required categories: {', '.join(result.required_categories)}")
    return 0


def cmd_benchmark_rag_production_run(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = run_rag_production_benchmark(
        config,
        run_id=args.run_id,
        dataset_file=args.dataset,
        top_k=args.top_k,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: production RAG benchmark completed" if result.meets_thresholds else "ERROR: production RAG benchmark failed")
        print(f"Evidence: {result.evidence_file}")
        print(f"Scale: {result.scale_chapters} chapters / {result.query_count} queries")
        print(f"Recall@{args.top_k}: {result.recall_at_k:.3f}")
        print(f"Fact error rate: {result.fact_error_rate:.3f}")
        print(f"P95 query: {result.p95_query_ms:.3f} ms")
        print(f"Incremental index: {result.incremental_index_ms:.3f} ms")
        for error in result.errors:
            print(f"- {error}")
    return 0 if result.meets_thresholds else 1


def cmd_benchmark_source_attach(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = attach_benchmark_source(
        config,
        run_id=args.run_id,
        source_dir=args.source_dir,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: benchmark manuscript source attached")
        print(f"Run: {result.run_id}")
        print(f"Chapters: {result.chapter_count}")
        print(f"Source merkle root: {result.source_merkle_root}")
        print(f"Manifest: {result.manifest_file}")
    return 0


def cmd_benchmark_blind_pack(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = create_blind_review_pack(
        config,
        comparison_id=args.comparison_id,
        run_ids=args.run_id,
        seed=args.seed,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: blind review pack created")
        print(f"Comparison: {result.comparison_id}")
        print(f"Public pack: {result.public_dir}")
        print(f"Private mapping: {result.private_mapping_file}")
        print(f"Pack hash: {result.pack_hash}")
    return 0


def cmd_benchmark_blind_template(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = create_blind_review_template(
        config,
        comparison_id=args.comparison_id,
        judge_id=args.judge_id,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: blind review template created")
        print(f"Comparison: {result.comparison_id}")
        print(f"Judge: {result.judge_id}")
        print(f"Template: {result.template_file}")
    return 0


def cmd_benchmark_blind_submit(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = submit_blind_review(
        config,
        comparison_id=args.comparison_id,
        judge_id=args.judge_id,
        file_path=args.file,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: blind review submission accepted")
        print(f"Comparison: {result.comparison_id}")
        print(f"Judge: {result.judge_id}")
        print(f"Submission: {result.submission_file}")
        print(f"SHA-256: {result.submission_sha256}")
    return 0


def cmd_benchmark_blind_aggregate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = aggregate_blind_reviews(config, comparison_id=args.comparison_id)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: blind reviews aggregated")
        print(f"Comparison: {result.comparison_id}")
        print(f"Judges: {result.judge_count}")
        print(f"Runs: {', '.join(result.run_ids)}")
        print(f"Aggregate: {result.aggregate_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_benchmark_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = validate_benchmark(config, run_id=args.run_id)
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: benchmark structure valid" if result.ok else "ERROR: benchmark invalid")
        print(f"Complete: {result.complete}")
        print(f"Acceptance passed: {result.acceptance_passed}")
        for failure in result.acceptance_failures:
            print(f"  - ACCEPTANCE: {failure}")
        for error in result.errors:
            print(f"  - {error}")
        for warning in result.warnings:
            print(f"  - WARN: {warning}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok else 1


def cmd_benchmark_report(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = report_benchmark(config, run_id=args.run_id)
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: benchmark report written")
        print(f"JSON: {result.report_json}")
        print(f"Markdown: {result.report_markdown}")
        print(f"Chapters recorded: {result.chapters_recorded}")
        print(f"Complete: {result.complete}")
        print(f"Acceptance passed: {result.acceptance_passed}")
        for failure in result.acceptance_failures:
            print(f"  - ACCEPTANCE: {failure}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_benchmark_compare(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = compare_benchmarks(
        config,
        comparison_id=args.comparison_id,
        run_ids=args.run_id,
        allow_incomplete=args.allow_incomplete,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: benchmark comparison written")
        print(f"JSON: {result.comparison_json}")
        print(f"Markdown: {result.comparison_markdown}")
        print(f"Runs: {', '.join(result.run_ids)}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_agent_task_list(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    root = resolve_project_root(config)
    items = list_manifests(root, chapter_number=args.chapter)
    payload = {"tasks": items, "count": len(items), "chapter_number": args.chapter}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Agent tasks: {len(items)}")
        for item in items:
            print(
                f"- {item.get('task_id')} "
                f"ch{manifest_chapter_number(item):03d} "
                f"{item.get('task_type')} {item.get('status')}"
            )
    return 0


def cmd_agent_task_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    root = resolve_project_root(config)
    payload = status_summary(root, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Agent tasks: {payload['tasks']}")
        print(f"By status: {json.dumps(payload['by_status'], ensure_ascii=False)}")
        print(f"By type: {json.dumps(payload['by_type'], ensure_ascii=False)}")
    return 0


def cmd_agent_task_show(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    root = resolve_project_root(config)
    payload = load_manifest(root, args.task)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Task: {payload.get('task_id')}")
        print(f"Type: {payload.get('task_type')}")
        print(f"Chapter: {manifest_chapter_number(payload)}")
        print(f"Status: {payload.get('status')}")
        print("Inputs:")
        for item in manifest_input_paths(payload):
            print(f"  - {item}")
        output = manifest_output(payload)
        commands = manifest_commands(payload)
        print(f"Output: {output.get('path')}")
        print(f"Protocol: {output.get('protocol')}")
        print(f"Validate: {commands.get('validate')}")
        print(f"Apply: {commands.get('apply')}")
        print(f"On failure: {commands.get('failure')}")
    return 0


def cmd_agent_task_brief(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = agent_task_brief(config, args.task, host=args.host)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload["work_order_markdown"], end="")
    return 0


def cmd_agent_task_overlay_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = validate_project_prompt_overlay(
        resolve_project_root(config),
        file_path=args.file,
        role_id=args.role_id,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Project Prompt overlay: {'OK' if payload['ok'] else 'INVALID'}")
        print(f"File: {payload['subject']}")
        provenance = payload.get("provenance") or {}
        if provenance.get("overlay_hash"):
            print(f"SHA-256: {provenance['overlay_hash']}")
        if provenance.get("conflict_report"):
            for item in provenance["conflict_report"].get("conflicts") or []:
                print(f"Conflict [{item.get('field')}]: {item.get('reason')}")
            print(f"Repair: {payload['next_command']}")
    return 0 if payload["ok"] else 1


def cmd_agent_task_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    root = resolve_project_root(config)
    payload = load_manifest(root, args.task)
    result = validate_manifest_strict(root, payload, strict=args.strict)
    output = asdict(result)
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        mode = "strict" if args.strict else "shape"
        print(f"Agent task validation: {mode}")
        print(f"Task: {result.task_id}")
        print(f"Type: {result.task_type}")
        print(f"OK: {result.ok}")
        if result.errors:
            print("Errors:")
            for item in result.errors:
                print(f"  - {item}")
        if result.warnings:
            print("Warnings:")
            for item in result.warnings:
                print(f"  - {item}")
    return 0 if result.ok else 1


def cmd_agent_task_result_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    root = resolve_project_root(config)
    manifest = load_manifest(root, args.task)
    result = validate_production_agent_result(
        root,
        manifest,
        result_file=args.file,
    )
    output = asdict(result)
    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"Agent result validation: {result.status}")
        print(f"Task: {result.task_id}")
        print(f"Lifecycle: {result.lifecycle_status}")
        print(f"Source schema: {result.normalization.source_schema or '<unknown>'}")
        print(f"Adapter: {result.normalization.adapter}")
        print(f"Diagnostic: {result.diagnostic_file}")
        for item in result.normalization.errors:
            print(f"Error: {item}")
        for item in result.normalization.need_human_reasons:
            print(f"Need human: {item}")
        for item in result.normalization.warnings:
            print(f"Warning: {item}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok else 1


def cmd_agent_task_reconcile(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    root = resolve_project_root(config)
    before = task_reconciliation_status(root, chapter_number=args.chapter)
    if before.get("errors"):
        payload = before
        exit_code = 1
    else:
        payload = reconcile_task_lineage(root, chapter_number=args.chapter)
        exit_code = 0
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Agent task reconciliation: {payload.get('status')}")
        print(f"Chapter: {args.chapter}")
        for item in payload.get("reconciled") or []:
            print(f"- {item.get('parent_task_id')} -> {item.get('child_task_id')}")
        for item in payload.get("errors") or []:
            print(f"Error: {item}")
        if payload.get("next_command"):
            print(f"Next command: {payload['next_command']}")
    return exit_code


def cmd_agent_task_readiness(args: argparse.Namespace) -> int:
    report = check_agent_data_pipeline_readiness(args.repository)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_agent_data_pipeline_readiness(report))
    return 0 if report["ready_for_data_pipeline"] else 1


def cmd_production_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = production_status(config)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        current = payload.get("current") or {}
        board = payload.get("board") or {}
        tasks = payload.get("agent_tasks") or {}
        print("OK: production status ready")
        print(f"Version: {payload.get('status_version')}")
        print(f"Path style: {payload.get('path_style')}")
        print(f"Command style: {payload.get('command_style')}")
        print(f"Next status: {current.get('next_status')}")
        print(f"Blocked by: {current.get('blocked_by')}")
        print(f"Waiting for: {current.get('waiting_for')}")
        print(f"Next command: {current.get('next_command')}")
        print(f"Board range: ch{int(board.get('from_chapter') or 0):03d}-ch{int(board.get('to_chapter') or 0):03d}")
        print(f"Board totals: {json.dumps(board.get('totals') or {}, ensure_ascii=False)}")
        print(f"Agent tasks: {tasks.get('tasks')}")
    return 0


def cmd_production_next(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = production_next(config)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: production next action ready")
        print(f"Chapter: {payload.get('chapter_number')}")
        print(f"Status: {payload.get('status')}")
        print(f"Blocked by: {payload.get('blocked_by')}")
        print(f"Waiting for: {payload.get('waiting_for')}")
        print(f"Next command: {payload.get('next_command')}")
        session = payload.get("session") or {}
        if session:
            print(f"Session: {session.get('action')} ({session.get('scope')})")
            print(f"Session first command: {session.get('first_command')}")
        if payload.get("human_summary"):
            print(f"Summary: {payload.get('human_summary')}")
        if payload.get("input_files"):
            print("Inputs:")
            for item in payload.get("input_files") or []:
                print(f"  - {item}")
        if payload.get("allowed_output_paths"):
            print("Allowed outputs:")
            for item in payload.get("allowed_output_paths") or []:
                print(f"  - {item}")
        if payload.get("output_schema"):
            print(f"Schema: {payload.get('output_schema')}")
        if payload.get("validate_command"):
            print(f"Validate: {payload.get('validate_command')}")
        if payload.get("apply_command"):
            print(f"Apply: {payload.get('apply_command')}")
        if payload.get("failure_next_command"):
            print(f"On failure: {payload.get('failure_next_command')}")
        if payload.get("need_human_reasons"):
            print("Need-human reasons:")
            for item in payload.get("need_human_reasons") or []:
                print(f"  - {item}")
        if getattr(args, "editorial", False):
            print_editorial_next_details(payload)
    return 0


def cmd_production_board(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = production_board(config, from_chapter=args.from_chapter, to_chapter=args.to_chapter)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: production board ready")
        print(f"Range: ch{payload['from_chapter']:03d}-ch{payload['to_chapter']:03d}")
        print(f"Totals: {json.dumps(payload['totals'], ensure_ascii=False)}")
        for row in payload["chapters"]:
            gate = row.get("gate_status") or {}
            editorial = row.get("editorial") or {}
            print(
                f"ch{int(row.get('chapter_number') or 0):03d} "
                f"draft={row.get('draft_status')} "
                f"final={row.get('final_status')} "
                f"gate={gate.get('status')} "
                f"repair={(row.get('repair_status') or {}).get('status')} "
                f"humanize={(row.get('humanize_status') or {}).get('status')} "
                f"expand={(row.get('expand_status') or {}).get('status')} "
                f"semantic={(row.get('chapter_semantic_status') or {}).get('status')} "
                f"pacing={(row.get('semantic_pacing_status') or {}).get('status')} "
                f"editorial={editorial.get('status')} "
                f"need_human={editorial.get('need_human')}"
            )
            if getattr(args, "editorial", False):
                print_editorial_board_details(editorial)
    return 0


def print_editorial_next_details(payload: dict[str, Any]) -> None:
    role = payload.get("editorial_role")
    if isinstance(role, dict) and role:
        print("Editorial role:")
        print(f"  - Role: {role.get('display_name')} ({role.get('role_id')})")
        print(f"  - Focus: {role.get('focus')}")
        print(f"  - Work order: {role.get('work_order_file')}")
        print(f"  - Context metadata: {role.get('context_file')}")
        print(f"  - Reviewer instance: {role.get('reviewer_instance_id')}")
        print(f"  - Context digest: {role.get('context_digest_hash')}")
        print(f"  - Result file: {role.get('result_file')}")
        print(f"  - Validate: {role.get('validate_command')}")
        print(f"  - Apply: {role.get('apply_command')}")
    if payload.get("expected_roles") or payload.get("missing_roles"):
        print("Editorial aggregate:")
        print(f"  - Expected roles: {', '.join(payload.get('expected_roles') or []) or 'None'}")
        print(f"  - Accepted roles: {', '.join(payload.get('accepted_roles') or []) or 'None'}")
        print(f"  - Missing roles: {', '.join(payload.get('missing_roles') or []) or 'None'}")
        print(f"  - Duplicate role results: {len(payload.get('duplicate_role_results') or [])}")
        print(f"  - Invalid role results: {len(payload.get('invalid_results') or [])}")
        print(f"  - Next command: {payload.get('next_command')}")
    readable = payload.get("need_human_reasons_readable") or []
    if readable:
        print("Readable need-human reasons:")
        for item in readable:
            if isinstance(item, dict):
                print(f"  - {item.get('code')}: {item.get('message')}")


def print_editorial_board_details(editorial: dict[str, Any]) -> None:
    print(f"    expected_roles={', '.join(editorial.get('expected_roles') or []) or 'None'}")
    print(f"    accepted_roles={', '.join(editorial.get('accepted_roles') or []) or 'None'}")
    print(f"    missing_roles={', '.join(editorial.get('missing_roles') or []) or 'None'}")
    print(f"    duplicate_role_results={len(editorial.get('duplicate_role_results') or [])}")
    print(f"    invalid_results={len(editorial.get('invalid_results') or [])}")
    if editorial.get("need_human_reasons"):
        print(f"    need_human_reasons={', '.join(editorial.get('need_human_reasons') or [])}")
    if editorial.get("next_command"):
        print(f"    next_command={editorial.get('next_command')}")
    for role in editorial.get("role_statuses") or []:
        if not isinstance(role, dict):
            continue
        print(
            "    role="
            f"{role.get('role_id')} "
            f"status={role.get('status')} "
            f"task={role.get('task_id') or 'None'} "
            f"output={role.get('result_file') or 'None'}"
        )


def cmd_production_loop(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = production_loop(config, max_steps=args.max_steps, no_apply=args.no_apply)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: production loop stopped at safe boundary")
        print(f"Status: {payload.get('status')}")
        print(f"Pause reason: {payload.get('pause_reason')}")
        print(f"Steps executed: {payload.get('steps_executed')}")
        for step in payload.get("steps") or []:
            print(
                f"  - step {step.get('step')}: {step.get('action')} "
                f"ch{int(step.get('chapter_number') or 0):03d} [{step.get('status')}]"
            )
        next_action = payload.get("next_action") or {}
        print(f"Next status: {next_action.get('status')}")
        print(f"Next command: {next_action.get('next_command')}")
    return 0 if payload.get("status") in {"paused", "max_steps_reached"} else 1


def cmd_intelligence_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = create_intelligence_task(
        config,
        task_type=args.task_type,
        input_files=args.input_files,
        chapter_number=getattr(args, "chapter", None),
        from_chapter=args.from_chapter,
        to_chapter=args.to_chapter,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: intelligence Agent task created")
        print(f"Task: {result.task_id}")
        print(f"Manifest: {result.manifest_file}")
        print(f"Instructions: {result.instruction_file}")
        print(f"Candidate: {result.candidate_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_intelligence_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = validate_intelligence_candidate(config, task_type=args.task_type, file_path=args.file)
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: intelligence candidate validated" if result.ok else "ERROR: intelligence candidate is invalid")
        print(f"Candidate: {result.candidate_file}")
        print(f"Report: {result.report_file}")
        for error in result.errors:
            print(f"  - {error}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok else 1


def cmd_intelligence_approve(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = approve_design_document(
        config,
        task_type=args.task_type,
        document_path=args.document,
        approved_by=args.approved_by,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: design document approved")
        print(f"Document: {result.document_file}")
        print(f"Approval: {result.approval_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_intelligence_compile_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = create_design_compile_task(
        config,
        task_type=args.task_type,
        document_path=args.document,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: design semantic compile task created")
        print(f"Task: {result.task_id}")
        print(f"Candidate: {result.candidate_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_intelligence_compile_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = validate_design_compile_delta(
        config,
        task_type=args.task_type,
        document_path=args.document,
        delta_path=args.delta,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: design delta validated" if result.ok else "ERROR: design delta is invalid")
        print(f"Delta: {result.candidate_file}")
        print(f"Report: {result.report_file}")
        for error in result.errors:
            print(f"  - {error}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok else 1


def cmd_intelligence_apply(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    if args.task_type in DESIGN_INTELLIGENCE_TASK_TYPES:
        if not args.document:
            raise ValueError("Design apply requires --document and --delta.")
        result = apply_compiled_design(
            config,
            task_type=args.task_type,
            document_path=args.document,
            delta_path=args.delta,
            approved_by=args.approved_by or "",
        )
    else:
        result = apply_intelligence_candidate(
            config,
            task_type=args.task_type,
            file_path=args.delta,
            approved_by=args.approved_by,
        )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: intelligence candidate applied")
        print(f"Type: {result.task_type}")
        print(f"Transaction: {result.transaction_report}")
        for path in result.touched_paths:
            print(f"  - {path}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_character_design_task(args: argparse.Namespace) -> int:
    args.task_type = "character_expression_design"
    args.input_files = []
    args.chapter = None
    args.from_chapter = None
    args.to_chapter = None
    return cmd_intelligence_task(args)


def cmd_character_design_validate(args: argparse.Namespace) -> int:
    args.task_type = "character_expression_design"
    return cmd_intelligence_validate(args)


def cmd_character_design_apply(args: argparse.Namespace) -> int:
    args.task_type = "character_expression_design"
    return cmd_intelligence_apply(args)


def cmd_character_audit_task(args: argparse.Namespace) -> int:
    args.task_type = "character_expression_review"
    args.input_files = []
    args.chapter = None
    return cmd_intelligence_task(args)


def cmd_character_audit_validate(args: argparse.Namespace) -> int:
    args.task_type = "character_expression_review"
    return cmd_intelligence_validate(args)


def cmd_character_audit_apply(args: argparse.Namespace) -> int:
    args.task_type = "character_expression_review"
    args.approved_by = None
    args.delta = args.file
    args.document = None
    return cmd_intelligence_apply(args)


def cmd_character_samples_approve(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = approve_voice_samples(
        resolve_project_root(config),
        file_path=args.file,
        approved_by=args.approved_by,
    )
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: character voice samples approved")
        print(f"Samples: {result.sample_count}")
        print(f"Profile: {result.profile_file}")
        print(f"Transaction: {result.transaction_report}")
    return 0


def cmd_fanfiction_canon_task(args: argparse.Namespace) -> int:
    args.task_type = "fanfiction_canon"
    args.from_chapter = None
    args.to_chapter = None
    return cmd_intelligence_task(args)


def cmd_fanfiction_canon_validate(args: argparse.Namespace) -> int:
    args.task_type = "fanfiction_canon"
    return cmd_intelligence_validate(args)


def cmd_fanfiction_canon_apply(args: argparse.Namespace) -> int:
    args.task_type = "fanfiction_canon"
    args.delta = args.file
    args.document = None
    return cmd_intelligence_apply(args)


def cmd_fanfiction_design_task(args: argparse.Namespace) -> int:
    args.task_type = "fanfiction_design"
    args.input_files = []
    args.from_chapter = None
    args.to_chapter = None
    return cmd_intelligence_task(args)


def cmd_fanfiction_design_validate(args: argparse.Namespace) -> int:
    args.task_type = "fanfiction_design"
    return cmd_intelligence_validate(args)


def cmd_fanfiction_design_apply(args: argparse.Namespace) -> int:
    args.task_type = "fanfiction_design"
    return cmd_intelligence_apply(args)


def cmd_fanfiction_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = fanfiction_status(config)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Creation mode: {payload['creation_mode']}")
        print(f"Continuity mode: {payload['continuity_mode']}")
        print(f"Sources: {payload['source_count']}")
        print(f"Canon: {payload['canon_status']}")
        print(f"Design: {payload['design_status']}")
        print(f"Ready: {payload['ready']}")
        print("Rights status is advisory only and never blocks creation or export.")
    return 0


def cmd_publication_report(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = publication_risk_report(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: publication risk report written")
        print(f"Report: {result.report_file}")
        print(f"Warnings: {result.warning_count}")
        print("Blocking: false")
    return 0


def cmd_publication_export(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = export_publication_bundle(config, output=args.output)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: publication bundle exported")
        print(f"Bundle: {result.bundle_file}")
        print(f"Chapters: {result.chapter_count}")
        print(f"Risk report: {result.report_file}")
        print("Blocking: false")
    return 0


def cmd_db_init(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    path = init_database(config)
    print(f"OK: database initialized at {path}")
    return 0


def cmd_db_sync(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    stats = sync_database(config)
    print("OK: database synchronized")
    for key, value in asdict(stats).items():
        print(f"{key}: {value}")
    return 0


def cmd_db_rebuild(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    stats = rebuild_database(config)
    print("OK: database rebuilt")
    for key, value in asdict(stats).items():
        print(f"{key}: {value}")
    return 0


def cmd_db_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = asdict(db_status(config))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Database: {payload['db_path']}")
        print(f"Exists: {payload['exists']}")
        print(f"Schema version: {payload['schema_version']}")
        print(f"Chapters: {payload['chapters']}")
        print(f"Chunks: {payload['chapter_chunks']}")
        print(f"Draft submissions: {payload['draft_submissions']}")
        print(f"Entities: {payload['entities']}")
        print(f"Events: {payload['events']}")
        print(f"Gate results: {payload['gate_results']}")
        print(f"Stale: {', '.join(payload['stale']) if payload['stale'] else 'none'}")
    return 0


def cmd_db_query(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    rows = query_table(config, args.table, limit=args.limit)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def cmd_models_list(args: argparse.Namespace) -> int:
    _ = args
    for profile in list_profiles():
        print(f"{profile.name}: embedding={profile.embedding_repo} reranker={profile.reranker_repo}")
        print(f"  {profile.description}")
    return 0


def cmd_models_install(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = install_model_profile(config, profile=args.profile, download=args.download)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic model profile prepared")
        print(f"Profile: {result.profile}")
        print(f"Models dir: {result.models_dir}")
        print(f"Manifest: {result.manifest_file}")
        print(f"Downloaded: {result.downloaded}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
    return 0


def cmd_models_verify(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = verify_models(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic model verification completed")
        print(f"Status: {result.status}")
        print(f"Profile: {result.profile}")
        print(f"Embedding: {result.embedding_model} cached={result.embedding_cached}")
        print(f"Reranker: {result.reranker_model} cached={result.reranker_cached}")
        print(f"Embedding loadable: {result.embedding_loadable}")
        print(f"Reranker loadable: {result.reranker_loadable}")
        print(f"Download required: {result.download_required}")
        print(f"Can auto download: {result.can_auto_download}")
        print(f"Provider ready: {result.provider_ready}")
        print(f"Fallback allowed: {result.fallback_allowed}")
        print(f"Fallback active: {result.fallback_active}")
        print(f"Fallback: {result.fallback or 'none'}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
    return 0


def cmd_models_cache_status(args: argparse.Namespace) -> int:
    payload = cache_status_payload()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Shared cache: {payload['shared_path']}")
        print(f"Bytes: {payload['total_bytes']}")
        print(f"Pending lock: {payload['pending_lock']}")
        for profile in payload["profiles"]:
            print(f"- {profile['profile']}: bytes={profile['bytes']} manifest_ok={profile['manifest_ok']}")
    return 1 if payload["pending_lock"] else 0


def cmd_vector_store_verify(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = vector_healthcheck(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: vector store verification completed" if result.ok else "ERROR: vector store verification failed")
        print(f"Backend: {result.backend}")
        print(f"URL: {result.url}")
        print(f"Collection: {result.collection}")
        print(f"Active records: {result.record_count}")
        print(f"Stale records: {result.stale_count}")
        if result.index_path:
            print(f"Index: {result.index_path}")
        print(f"Message: {result.message}")
        if result.recommendation:
            print(f"Next: {result.recommendation}")
    return 0 if result.ok else 1


def cmd_vector_store_rebuild(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = vector_rebuild(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: vector store rebuilt")
        print(f"Backend: {result.backend}")
        print(f"Records: {result.records}")
        print(f"Source: {result.source_file}")
        print(f"Store: {result.store_path}")
    return 0


def cmd_creative_brief(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    if args.init:
        result = init_creative_brief(config, overwrite=True)
    else:
        result = validate_creative_brief(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: creative brief checked" if result.ok else "BLOCKED: creative brief needs confirmation")
        print(f"Brief: {result.brief_file}")
        if result.task_file:
            print(f"Task: {result.task_file}")
        print(f"Errors: {len(result.errors)}")
    return 0 if result.ok else 1


def cmd_creative_style_extract(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = style_extract(
        config,
        sample_files=args.sample_files,
        name=args.name,
        source_project=args.source_project,
        library_profile=args.library,
        activate=not args.no_activate,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: sample style profile extracted")
        print(f"Name: {result.name}")
        print(f"Profile: {result.profile_file}")
        print(f"Current profile: {result.current_profile_file or 'not activated'}")
        print(f"Library: {result.library_file}")
        print(f"Source project: {result.source_project}")
        print(f"Samples: {', '.join(result.sample_files) or 'library import'}")
        print(f"Activated: {result.activated}")
    return 0


def cmd_creative_humanize_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = humanize_task(config, chapter_number=args.chapter, source=args.source)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: Humanizer v4 task written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Source: {result.source_file}")
        print(f"Task: {result.task_file}")
        print(f"Manifest: {result.manifest_file}")
        print(f"Candidate: {result.candidate_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_creative_humanize_check(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = humanize_check(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: Humanizer v4 check completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Passed: {result.passed}")
        print(f"Report: {result.report_file}")
        print(f"Markdown: {result.markdown_report}")
        print(f"Issues: {len(result.issues)}")
        print(f"Warnings: {len(result.warnings)}")
        print(f"Next command: {result.next_command}")
    return 0 if result.passed else 1


def cmd_creative_humanize_semantic_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = humanize_semantic_task(
        config,
        chapter_number=args.chapter,
        candidate_file=args.file,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: Humanizer semantic review task written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Source: {result.source_file}")
        print(f"Candidate: {result.candidate_file}")
        print(f"Task: {result.task_file}")
        print(f"Manifest: {result.manifest_file}")
        print(f"Output: {result.output_file}")
        print(f"Reasons: {', '.join(result.reasons) or 'manual request'}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_creative_humanize_semantic_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = humanize_semantic_validate(
        config,
        chapter_number=args.chapter,
        file_path=args.file,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: Humanizer semantic review validated" if result.ok else "ERROR: Humanizer semantic review is invalid")
        print(f"Chapter: {result.chapter_number}")
        print(f"Structurally valid: {result.ok}")
        print(f"Candidate passed: {result.passed}")
        print(f"Need human: {result.need_human}")
        print(f"Report: {result.report_file}")
        print(f"Errors: {len(result.errors)}")
        print(f"Blocking findings: {len(result.blocking_findings)}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok and result.passed else 1


def cmd_quality_payoff_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = reader_payoff_task(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: reader payoff review task written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Task: {result.task_file}")
        print(f"Manifest: {result.manifest_file}")
        print(f"Output: {result.output_file}")
        print(f"Reasons: {', '.join(result.reasons) or 'manual request'}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_quality_contract(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = compile_effective_quality_contract(
        config,
        chapter_number=args.chapter,
        compare_markets=getattr(args, "compare_market", []),
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: effective quality contract compiled")
        print(f"Chapter: {args.chapter}")
        facet_ids = [
            f"{item.get('kind')}:{item.get('id')}" for item in payload.get("active_facets", [])
        ]
        print(
            f"Profile: {payload['primary_market']} + {', '.join(facet_ids)} + {payload['phase']} "
            f"[{payload['strictness']}]"
        )
        print(
            "Platform deviations: "
            f"{payload['blocking_policy']['primary_deviation']} "
            f"(can block: {str(payload['blocking_policy']['primary_can_block']).lower()})"
        )
        print(f"Compatibility observations: {len(payload['compatibility_observations'])} (always advisory)")
        print(
            "Approved baseline chapters: "
            + ", ".join(str(item) for item in payload["approved_style_baseline"]["approved_chapters"])
        )
        if getattr(args, "explain", False):
            print("Merge trace:")
            for item in payload["merge_trace"]:
                changed = ", ".join(item.get("changed_fields", [])) or "none"
                overridden = ", ".join(item.get("overridden_fields", [])) or "none"
                print(f"- {item['layer']}: changed={changed}; overridden={overridden}; source={item['source']}")
            print("Overridden fields: " + (", ".join(payload["overridden_fields"]) or "none"))
            for observation in payload["compatibility_observations"]:
                print(
                    f"- [{observation['severity']}] {observation['market']} "
                    f"{observation['code']}: {observation['message']} (non-blocking)"
                )
    return 0


def cmd_quality_story_profile(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    payload = compile_story_profile(config.data["story_profile"], market_ids=set(BUILTIN_MARKET_IDS))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: story profile compiled" if payload["ready"] else "BLOCKED: story profile needs human resolution")
        print(f"Primary market: {payload['market']['primary']}")
        print("Selected facets:")
        for item in payload["selected_facets"]:
            print(f"- {item['kind']}:{item['id']} [{item['level']}]")
        for item in payload["unresolved_conflicts"]:
            print(f"- unresolved {item['conflict_id']}: {', '.join(item['facets'])}")
        for conflict_id in payload["unused_resolution_ids"]:
            print(f"- unused resolution: {conflict_id}")
        if not payload["ready"]:
            print("Next: edit project.yaml story_profile.resolutions with an explicit human decision and rationale.")
    return 0 if payload["ready"] else 1


def cmd_quality_baseline_approve(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = approve_style_baseline(
        config,
        chapter_number=args.chapter,
        approved_by=args.approved_by,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: finalized chapter approved for the style baseline")
        print(f"Chapter: {result.chapter_number}")
        print(f"Baseline: {result.baseline_file}")
        print(f"Approved by: {result.approved_by}")
        print(f"Transaction: {result.transaction_report}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_quality_payoff_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = reader_payoff_validate(
        config,
        chapter_number=args.chapter,
        file_path=args.file,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: reader payoff review validated" if result.ok else "ERROR: reader payoff review is invalid")
        print(f"Chapter: {result.chapter_number}")
        print(f"Structurally valid: {result.ok}")
        print(f"Payoff passed: {result.passed}")
        print(f"Need human: {result.need_human}")
        print(f"Report: {result.report_file}")
        print(f"Errors: {len(result.errors)}")
        print(f"Blocking findings: {len(result.blocking_findings)}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok else 1


def cmd_quality_feedback_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = feedback_registry_status(config, target_chapter=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: quality feedback registry inspected")
        print(f"Registry: {result.registry_file}")
        print(f"Total: {result.total}")
        print(f"Active: {result.active}")
        print(f"Carried: {result.carried}")
        print(f"Resolved: {result.resolved}")
        print(f"Suppressed: {result.suppressed}")
        print(f"Expired: {result.expired}")
    return 0


def cmd_quality_feedback_resolve(args: argparse.Namespace) -> int:
    return cmd_quality_feedback_transition(args, status="resolved")


def cmd_quality_feedback_suppress(args: argparse.Namespace) -> int:
    return cmd_quality_feedback_transition(args, status="suppressed")


def cmd_quality_feedback_transition(args: argparse.Namespace, *, status: str) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = transition_feedback(
        config,
        feedback_id=args.feedback_id,
        status=status,
        evidence=args.evidence,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(f"OK: quality feedback marked {status}")
        print(f"Feedback: {result.updated_feedback_id}")
        print(f"Registry: {result.registry_file}")
        print(f"Active: {result.active}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_creative_expand_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = expand_task(
        config,
        chapter_number=args.chapter,
        source=args.source,
        expansion_types=args.expansion_types,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: content expansion task written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Source: {result.source_file}")
        print(f"Task: {result.task_file}")
        print(f"Candidate: {result.candidate_file}")
        print(f"Expansion types: {', '.join(result.expansion_types)}")
        print(f"Metric: {result.metric}")
        print(f"Current content characters: {result.current_content_characters}")
        print(f"Minimum content characters: {result.minimum_content_characters}")
        print(f"Missing content characters: {result.missing_content_characters}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_creative_expand_check(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = expand_check(
        config,
        chapter_number=args.chapter,
        file_path=args.file,
        expansion_types=args.expansion_types,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: content expansion check completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Passed: {result.passed}")
        print(f"Metric: {result.metric}")
        print(f"Content characters: {result.content_characters}")
        print(f"Minimum content characters: {result.minimum_content_characters}")
        print(f"Report: {result.report_file}")
        print(f"Markdown: {result.markdown_report}")
        print(f"Issues: {len(result.issues)}")
        print(f"Warnings: {len(result.warnings)}")
        print(f"Next command: {result.next_command}")
    return 0 if result.passed else 1


def cmd_rag_build(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    stats = build_chunks(
        config,
        max_chars=args.max_chars,
        overlap_chars=args.overlap_chars,
        with_embeddings=args.with_embeddings,
    )
    print("OK: RAG chunks built")
    print(f"Chapters: {stats.chapters}")
    print(f"Chunks: {stats.chunks}")
    print(f"Embeddings: {stats.embeddings}")
    print(f"Output: {stats.output_dir}")
    return 0


def cmd_rag_query(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = rag_query(
        config,
        args.query,
        top_k=args.top_k,
        candidate_pool=args.candidate_pool,
        semantic=args.semantic,
        chapter_number=args.chapter,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(f"Query: {result.query}")
        print(f"Cache: {result.cache_file}")
        print(f"Hits: {len(result.hits)}")
        for hit in result.hits:
            print(f"- {hit.id} score={hit.score:.3f} chapter={hit.chapter_number} source={hit.source_path}")
            if args.semantic:
                print(f"  semantic={hit.semantic_score:.3f} rerank={hit.rerank_score:.3f} reason={hit.source_reason}")
            print(f"  reasons: {', '.join(hit.reasons) if hit.reasons else 'metadata match'}")
            print(f"  text: {hit.text[:120].replace(chr(10), ' ')}")
    return 0


def cmd_rag_context(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = build_context(config, chapter_number=args.chapter, query_text=args.query, top_k=args.top_k, semantic=args.semantic)
    print("OK: RAG context written")
    print(f"Context: {result.context_file}")
    print(f"Hits: {result.hit_count}")
    return 0


def cmd_graph_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = validate_graph(config)
    payload = asdict(result)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Graph: {result.graph_file}")
        print(f"Entities: {result.entities}")
        print(f"Relationships: {result.relationships}")
        print(f"Events: {result.events}")
        print(f"Errors: {len(result.errors)}")
        print(f"Warnings: {len(result.warnings)}")
        for error in result.errors:
            print(f"ERROR: {error}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
    return 1 if result.errors else 0


def cmd_graph_update(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = update_graph(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: story graph updated")
        print(f"Chapter: {result.chapter_number}")
        print(f"Graph: {result.graph_file}")
        print(f"Matched entities: {result.matched_entities}")
        print(f"Mentions added: {result.mentions_added}")
        print(f"Events added: {result.events_added}")
        print(f"SQLite entities: {result.db_entities}")
        print(f"SQLite events: {result.db_events}")
    return 0


def cmd_graph_check(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = check_graph(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: graph check written")
        print(f"Report: {result.report_file}")
        print(f"Issues: {len(result.issues)}")
        print(f"Warnings: {len(result.warnings)}")
        for issue in result.issues:
            print(f"ISSUE: {issue}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
    return 1 if result.issues else 0


def cmd_graph_retrieve(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = retrieve_graph(config, query_text=args.query, chapter_number=args.chapter, top_k=args.top_k)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: graph retrieval completed")
        print(f"Query: {result.query}")
        print(f"Chapter: {result.chapter_number}")
        print(f"Hits: {len(result.hits)}")
        for hit in result.hits:
            print(f"- {hit.kind}:{hit.id} score={hit.graph_score:.3f} hop={hit.hop_distance}")
            print(f"  path: {hit.path_reason}")
            if hit.evidence_span:
                print(f"  evidence: {hit.evidence_span}")
    return 0


def cmd_memory_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = validate_memory(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: memory validation completed")
        print(f"Scene memories: {result.scene_memories}")
        print(f"Chapter memories: {result.chapter_memories}")
        print(f"Arc memories: {result.arc_memories}")
        print(f"Character memories: {result.character_memories}")
        print(f"TCS snapshots: {result.tcs_snapshots}")
        print(f"Errors: {len(result.errors)}")
        print(f"Warnings: {len(result.warnings)}")
        for error in result.errors:
            print(f"ERROR: {error}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
    return 0 if result.ok else 1


def cmd_memory_tcs(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = build_tcs(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: TCS snapshot written")
        print(f"Chapter: {result.chapter_number}")
        print(f"TCS: {result.tcs_file}")
        print(f"Characters: {', '.join(result.current_characters) if result.current_characters else 'none'}")
        print(f"Recent events: {', '.join(result.recent_events) if result.recent_events else 'none'}")
    return 0


def cmd_memory_compress(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = compress_memory(
        config,
        scope=args.scope,
        from_chapter=args.from_chapter,
        to_chapter=args.to_chapter,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: memory compressed")
        print(f"Scope: {result.scope}")
        print(f"Range: ch{result.from_chapter:03d}-ch{result.to_chapter:03d}")
        print(f"Output: {result.output_file}")
        print(f"Sources: {result.source_count}")
        print(f"SQLite synced: {result.db_synced}")
    return 0


def cmd_memory_character_check(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = character_check(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: character consistency check completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Passed: {result.passed}")
        print(f"Report: {result.report_file}")
        print(f"Findings: {len(result.findings)}")
    return 0 if result.passed else 1


def cmd_memory_tcs_transition(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = build_tcs_transition(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: TCS transition written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Transition: {result.transition_file}")
        print(f"Current: {result.current_file}")
        print(f"Next chapter: {result.next_chapter}")
    return 0


def cmd_memory_tcs_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = validate_tcs(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: TCS validation completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Valid: {result.ok}")
        print(f"File: {result.file}")
        for error in result.errors:
            print(f"ERROR: {error}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
    return 0 if result.ok else 1


def cmd_open_book(args: argparse.Namespace) -> int:
    if getattr(args, "_open_book_needs_init", False):
        config, output = prepared_init_context(args)
        init_result = init_project(config, output=output, force=False)
        print(f"OK: project initialized at {init_result.root}")
        print(f"Project config: {init_result.project_config}")
        config = load_project_config(init_result.project_config)
    else:
        config = load_project_config(Path(args.config).expanduser().resolve())
    confirmations = {
        "target_audience": args.target_audience,
        "writing_style": args.writing_style,
        "core_forbidden_zone": args.forbidden_zone,
        "automation_level": args.automation_level,
        "target_scale": args.target_scale,
    }
    result = open_book(config, confirmations)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: open-book confirmed")
        print(f"Idea seed: {result.idea_seed}")
        print(f"Reader contract: {result.reader_contract}")
        print(f"Book outline: {result.book_outline}")
        print(f"State: {result.state_file}")
    return 0


def cmd_plan_chapter(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = plan_chapter(config, chapter_number=args.chapter, overwrite=args.overwrite)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: chapter card ready")
        print(f"Chapter: {result.chapter_number}")
        print(f"JSON: {result.json_file}")
        print(f"Markdown: {result.markdown_file}")
    return 0


def cmd_beat(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = generate_beat_sheet(
        config,
        chapter_number=args.chapter,
        overwrite=args.overwrite,
        auto_plan=args.auto_plan,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: beat sheet ready")
        print(f"Chapter: {result.chapter_number}")
        print(f"JSON: {result.json_file}")
        print(f"Markdown: {result.markdown_file}")
    return 0


def cmd_continue_write(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = continue_write(config, chapter_number=args.chapter, overwrite=args.overwrite)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        if result.status == "task_ready":
            print("OK: continue-write task package ready")
        else:
            print("OK: continue-write draft pipeline completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Status: {result.status}")
        print(f"Context: {result.context_file}")
        print(f"Chapter card: {result.chapter_card}")
        print(f"Beat sheet: {result.beat_sheet}")
        if result.writing_task_markdown:
            print(f"Writing task: {result.writing_task_markdown}")
        if result.recommended_agent_draft:
            print(f"Recommended agent draft: {result.recommended_agent_draft}")
        if result.next_command:
            print(f"Next command: {result.next_command}")
        if result.draft_file:
            print(f"Draft: {result.draft_file}")
        print(f"Run report: {result.run_report}")
    return 0


def cmd_batch_write(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = batch_write(
        config,
        chapters=args.chapters,
        stop_on_gate_failure=args.stop_on_gate_failure,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: batch-write scheduler completed")
        print(f"Status: {result.status}")
        print(f"Attempted: {result.chapters_attempted}/{result.chapters_requested}")
        print(f"Failed: {result.failed}")
        print(f"Skipped: {result.skipped}")
        if result.stopped_reason:
            print(f"Stopped: {result.stopped_reason}")
        if result.next_command:
            print(f"Next command: {result.next_command}")
        print(f"Run report: {result.run_report}")
    return 0 if result.failed == 0 else 1


def cmd_auto_write_plan(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = auto_write_plan(
        config,
        start_chapter=args.start_chapter,
        overwrite=args.overwrite,
    )
    print_auto_write_result(result, json_output=args.json)
    return 0


def cmd_auto_write_run(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = auto_write_run(config, chapters=args.chapters)
    print_auto_write_result(result, json_output=args.json)
    return 0 if result.status not in {"blocked", "paused_gate_failed"} else 1


def cmd_auto_write_progress(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = auto_write_progress(config)
    print_auto_write_result(result, json_output=args.json)
    return 0


def cmd_auto_write_report(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = auto_write_report(config)
    print_auto_write_result(result, json_output=args.json)
    return 0


def print_auto_write_result(result: Any, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return
    print(f"OK: auto-write {result.action}")
    print(f"Status: {result.status}")
    print(f"Summary: {result.summary}")
    print(f"Forecast chapters: {result.forecast_chapters}")
    print(f"Target content characters: {result.target_characters}")
    print(f"Current chapter: {result.current_chapter}")
    print(f"Last finalized chapter: {result.last_finalized_chapter}")
    print(f"Chapters attempted: {result.chapters_attempted}")
    print(f"Failure count: {result.failure_count}")
    if result.pause_reason:
        print(f"Pause reason: {result.pause_reason}")
    if result.next_command:
        print(f"Next command: {result.next_command}")
    print(f"State: {result.state_file}")
    if result.report_file:
        print(f"Report: {result.report_file}")


def cmd_draft_submit(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = submit_agent_draft(
        config,
        chapter_number=args.chapter,
        file_path=args.file,
        agent=args.agent,
        overwrite=args.overwrite,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: agent draft submitted")
        print(f"Chapter: {result.chapter_number}")
        print(f"Passed: {result.passed}")
        print(f"Severity: {result.severity}")
        print(f"Draft: {result.draft_file}")
        print(f"Submission: {result.submission_file}")
        print(f"Gate result: {result.gate_result}")
        print(f"Pacing review: {result.pacing_review}")
        print(f"SQLite synced: {result.db_synced}")
        print(f"Next command: {result.next_command}")
        print(f"Run report: {result.run_report}")
    return 0 if result.passed else 1


def cmd_chapter_finalize(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = finalize_chapter(
        config,
        chapter_number=args.chapter,
        approved_by=args.approved_by,
        overwrite=args.overwrite,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: chapter finalized")
        print(f"Chapter: {result.chapter_number}")
        print(f"Approved by: {result.approved_by}")
        print(f"Final: {result.final_file}")
        print(f"Summary: {result.summary_file}")
        print(f"Finalization: {result.finalization_file}")
        print(f"Gate result: {result.gate_result}")
        print(f"Graph: {result.graph_file}")
        print(f"RAG chunks: {result.rag_chunks_dir}")
        print(f"Next plot context: {result.context_file}")
        print(f"SQLite synced: {result.db_synced}")
        print(f"Next command: {result.next_command}")
        print(f"Run report: {result.run_report}")
    return 0


def cmd_chapter_semantic_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = chapter_semantic_task(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: unified chapter semantic task written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Task: {result.task_file}")
        print(f"Manifest: {result.manifest_file}")
        print(f"Output: {result.output_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_chapter_semantic_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = chapter_semantic_validate(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: chapter semantic bundle validated" if result.ok else "BLOCKED: chapter semantic bundle is invalid")
        print(f"Chapter: {result.chapter_number}")
        print(f"Need human: {result.need_human}")
        print(f"Errors: {len(result.errors)}")
        print(f"Warnings: {len(result.warnings)}")
        print(f"Validation: {result.report_file}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok else 1


def cmd_chapter_semantic_apply(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = chapter_semantic_apply(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: chapter semantic bundle applied")
        print(f"Chapter: {result.chapter_number}")
        print(f"Ledger: {result.ledger_file}")
        print(f"Graph: {result.graph_file}")
        print(f"Foreshadow state: {result.foreshadow_state_file}")
        print(f"TCS: {result.tcs_file}")
        print(f"Character views: {len(result.character_files)}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_chapter_semantic_rebuild(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = chapter_semantic_rebuild(config, through=args.through, approved_by=args.approved_by)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic materialized views rebuilt")
        print(f"Through: {result.through}")
        print(f"Approved by: {result.approved_by}")
        print(f"Ledgers: {len(result.ledger_files)}")
        print(f"Character views: {result.character_files}")
        print(f"TCS files: {result.tcs_files}")
        print(f"RAG chapters: {result.rag_chapters}")
        print(f"Transaction: {result.transaction_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_chapter_close(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = chapter_close(config, chapter_number=args.chapter, approved_by=args.approved_by)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: chapter closed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Approved by: {result.approved_by}")
        print(f"Closure: {result.closure_file}")
        print(f"Archived through: {result.archived_through}")
        print(f"Archives: {len(result.archive_files)}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_artifacts_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = artifact_status(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: artifact status inspected")
        print(f"Loose files: {result.loose_files} ({result.loose_bytes} bytes)")
        print(f"Archives: {result.archive_files} ({result.archive_bytes} bytes)")
        print(f"Committed snapshots: {result.committed_snapshot_dirs} ({result.committed_snapshot_bytes} bytes)")
        print(f"Pending transactions: {result.pending_transactions}")
        print(f"Retained failure snapshots: {result.retained_failure_snapshots}")
        print(f"Reclaimable snapshot bytes: {result.reclaimable_snapshot_bytes}")
        print(f"Orphan task artifacts: {result.orphan_task_artifacts}")
        for path in result.orphan_task_files:
            print(f"- {path}")
        print(f"Compacted through: ch{result.compacted_through:03d}" if result.compacted_through else "Compacted through: none")
        print(
            "Active buffer: "
            + (", ".join(f"ch{chapter:03d}" for chapter in result.active_buffer_chapters) or "none")
        )
        print(f"Archived loose duplicates: {result.archived_loose_duplicates}")
        for path in result.archived_loose_duplicate_files:
            print(f"- {path}")
        print(f"Duplicate hash groups: {result.duplicate_hash_groups} ({result.duplicate_content_files} files)")
        print(f"Reclaimable: {result.reclaimable_files} files ({result.reclaimable_bytes} bytes)")
        print("Retention classes: " + json.dumps(result.retention_classes, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_artifacts_compact(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    if args.scope == "chapters" and args.through is None:
        raise ValueError("--through is required when --scope=chapters")
    if not args.dry_run and not str(args.approved_by or "").strip():
        raise ValueError("--approved-by is required for artifact compaction")
    result = compact_artifacts(
        config,
        through=int(args.through or 0),
        dry_run=args.dry_run,
        scope=args.scope,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(
            ("OK: artifact compaction dry-run" if result.eligible else "BLOCKED: artifact compaction dry-run")
            if result.dry_run
            else "OK: artifacts compacted"
        )
        print(f"Eligible: {result.eligible}")
        for blocker in result.blockers:
            print(f"BLOCKED: {blocker}")
        print(f"Scope: {result.scope}")
        if result.scope == "chapters":
            print(f"Through chapter: {result.through}")
        print(f"Candidates: {result.candidate_files} ({result.candidate_bytes} bytes)")
        print(
            f"Unique content: {result.unique_content_files} blobs ({result.unique_content_bytes} bytes); "
            f"duplicates collapsed: {result.deduplicated_files}"
        )
        print(f"Removed: {result.removed_files} ({result.removed_bytes} bytes)")
        print(f"Committed snapshots: {result.committed_snapshots} ({result.committed_snapshot_bytes} bytes)")
        print(f"Archives: {len(result.archive_files)}")
    return 0 if result.eligible else 1


def cmd_artifacts_verify(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = verify_artifacts(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: artifact archives verified" if result.ok else "ERROR: artifact archive verification failed")
        print(f"Status: {result.status}")
        print(f"Archives: {result.archives}")
        print(f"Entries: {result.entries}")
        print(f"Project setup archive: {'present' if result.project_setup_archive else 'not created'}")
        for error in result.errors:
            print(f"- {error}")
    return 0 if result.ok else 1


def cmd_artifacts_restore(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = restore_artifacts(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: chapter artifacts restored")
        print(f"Chapter: {result.chapter_number}")
        print(f"Archive: {result.archive_file}")
        print(f"Restored: {len(result.restored_files)}")
        print(f"Skipped identical: {len(result.skipped_files)}")
    return 0


def cmd_gate_check(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = gate_check(config, chapter_number=args.chapter, source=args.source, semantic=args.semantic)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: gate-check completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Passed: {result.passed}")
        print(f"Severity: {result.severity}")
        print(f"Gate result: {result.gate_result}")
        print(f"Failures: {len(result.failures)}")
        print(f"Allowed actions: {', '.join(result.allowed_actions)}")
    return 0 if result.passed else 1


def cmd_gate_semantic_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = semantic_review_task(config, chapter_number=args.chapter, source=args.source)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic gate task created")
        print(f"Chapter: {result.chapter_number}")
        print(f"Task: {result.task_markdown}")
        print(f"Output: {result.output_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_gate_semantic_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = semantic_review_validate(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic gate result validated" if result.ok else "ERROR: semantic gate result is invalid")
        print(f"Report: {result.report_file}")
        print(f"Next command: {result.next_command}")
        for error in result.errors:
            print(f"- {error}")
    return 0 if result.ok else 1


def cmd_gate_semantic_apply(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = semantic_review_apply(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic gate result applied")
        print(f"Application: {result.application_file}")
        print(f"Gate result: {result.gate_result}")
        print(f"Blocking findings: {result.blocking_findings}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_gate_waiver(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = record_waiver(
        config,
        chapter_number=args.chapter,
        reason=args.reason,
        approved_by=args.approved_by,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: gate waiver recorded")
        print(f"Chapter: {result.chapter_number}")
        print(f"Severity: {result.severity}")
        print(f"Waiver: {result.waiver_file}")
        print(f"Gate result: {result.gate_result}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_pacing_review(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = pacing_review(config, chapter_number=args.chapter, source=args.source, semantic_reader=args.semantic_reader)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: pacing review completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Tier: {result.tier}")
        print(f"Report: {result.report_file}")
        if result.reader_experience_report:
            print(f"Reader experience: {result.reader_experience_report}")
        print(f"Issues: {len(result.issues)}")
        print(f"Warnings: {len(result.warnings)}")
    return 0 if not result.issues else 1


def cmd_pacing_semantic_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = semantic_pacing_task(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic pacing task written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Task JSON: {result.task_json}")
        print(f"Task Markdown: {result.task_markdown}")
        print(f"Manifest: {result.manifest_file}")
        print(f"Output: {result.output_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_pacing_semantic_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = semantic_pacing_validate(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic pacing validation completed")
        print(f"Chapter: {result.chapter_number}")
        print(f"Valid: {result.ok}")
        print(f"Report: {result.report_file}")
        for error in result.errors:
            print(f"ERROR: {error}")
        for warning in result.warnings:
            print(f"WARN: {warning}")
        print(f"Next command: {result.next_command}")
    return 0 if result.ok else 1


def cmd_pacing_semantic_apply(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = semantic_pacing_apply(config, chapter_number=args.chapter, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: semantic pacing applied")
        print(f"Chapter: {result.chapter_number}")
        print(f"Gate result: {result.gate_result}")
        print(f"Pacing review: {result.pacing_review}")
        print(f"Escalated failures: {result.escalated_failures}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_repair_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = {
        "barrier": review_barrier_status(config, chapter_number=args.chapter),
        "attempts": repair_attempt_status(config, chapter_number=args.chapter),
        "plan": repair_plan_status(config, chapter_number=args.chapter),
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Review barrier: {result['barrier']['status']}")
        print(f"Repair attempts: {result['attempts']['used']}/{result['attempts']['maximum']}")
        print(f"Repair plan: {result['plan']['status']}")
    return 0


def cmd_repair_synthesis_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    try:
        result = create_repair_synthesis_task(config, chapter_number=args.chapter)
    except RepairCoordinationError as exc:
        raise GateError(str(exc)) from exc
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("OK: review bundle frozen and repair synthesis task ready")
        print(f"Task: {result['task_id']}")
        print(f"Review bundle: {result['review_bundle']}")
        print(f"Plan: {result['plan_file']}")
        print(f"Next command: {result['next_command']}")
    return 0


def cmd_repair_synthesis_validate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    try:
        result = validate_repair_plan(
            config,
            chapter_number=args.chapter,
            file_path=args.file,
        )
    except RepairCoordinationError as exc:
        raise GateError(str(exc)) from exc
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("OK: repair plan validated" if result["ok"] else "INVALID: repair plan")
        print(f"Report: {result['report_file']}")
        print(f"Next command: {result['next_command']}")
    return 0 if result["ok"] else 1


def cmd_repair_candidate_task(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    try:
        result = create_repair_candidate_task(
            config,
            chapter_number=args.chapter,
            agent=args.agent,
        )
    except RepairCoordinationError as exc:
        raise GateError(str(exc)) from exc
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("OK: immutable repair candidate task ready")
        print(f"Task: {result['task_id']}")
        print(f"Candidate: {result['candidate_draft']}")
        print(f"Next command: {result['next_command']}")
    return 0


def cmd_research_add(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = add_research(
        config,
        file_path=args.file,
        title=args.title,
        source_url=args.source_url,
        tags=args.tag,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: research item added to inbox")
        print(f"Item: {result.item_id}")
        print(f"Status: {result.status}")
        print(f"JSON: {result.item_file}")
        print(f"Content: {result.content_file}")
    return 0


def cmd_research_search(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = search_research(config, args.query, limit=args.limit)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: research search saved to inbox")
        print(f"Item: {result.item_id}")
        print(f"Status: {result.status}")
        print(f"JSON: {result.item_file}")
        print(f"Sources: {len(result.sources)}")
    return 0


def cmd_research_gaps(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = detect_knowledge_gaps(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: knowledge gap plan written")
        print(f"Report: {result.report_file}")
        print(f"Plan: {result.plan_file}")
        print(f"Gaps: {len(result.gaps)}")
    return 0


def cmd_research_promote(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = promote_research(
        config,
        research_item=args.item,
        approved_by=args.approved_by,
        review_note=args.review_note,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: research item promoted to canon")
        print(f"Item: {result.item_id}")
        print(f"Canon: {result.canon_file}")
        print(f"Impact: {result.impact_report}")
        print(f"RAG chunk: {result.rag_chunk_file}")
        print(f"Context: {result.context_file}")
        print(f"Graph: {result.graph_file}")
        print(f"SQLite chunks: {result.db_chunks}")
    return 0


def cmd_impact_analyze(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    if args.after_rollback:
        result = rollback_impact(config)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        else:
            print("OK: rollback impact report written")
            print(f"Report: {result.report_file}")
            print(f"To chapter: {result.to_chapter}")
            print(f"Affected chapters: {len(result.affected_chapters)}")
            print(f"Settings: {len(result.affected_settings)}")
            print(f"Summaries: {len(result.affected_summaries)}")
        return 0
    if not args.research_item:
        raise ValueError("impact-analyze requires --research-item or --after-rollback.")
    result = impact_analyze(config, research_item=args.research_item)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: research impact report written")
        print(f"Item: {result.item_id}")
        print(f"Report: {result.report_file}")
        print(f"Characters: {len(result.impacted_characters)}")
        print(f"Chapters: {len(result.impacted_chapters)}")
        print(f"Graph nodes: {len(result.impacted_graph_nodes)}")
        print(f"Future cards: {len(result.impacted_future_cards)}")
    return 0


def cmd_revision_branch(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = create_revision_branch(config, chapter_number=args.chapter, overwrite=args.overwrite)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: rewrite candidate created")
        print(f"Chapter: {result.chapter_number}")
        print(f"Status: {result.status}")
        print(f"Source: {result.source_path}")
        print(f"Candidate: {result.candidate_path}")
        print(f"Report: {result.report_file}")
    return 0


def cmd_revision_rollback(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = rollback(config, to_chapter=args.to_chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: rollback completed")
        print(f"To chapter: {result.to_chapter}")
        print(f"Snapshot: {result.snapshot_dir or 'none'}")
        print(f"Detached dir: {result.detached_dir}")
        print(f"Detached files: {len(result.detached_files)}")
        print(f"Stale chapters: {', '.join(str(item) for item in result.stale_chapters) if result.stale_chapters else 'none'}")
        print(f"Stale report: {result.stale_report}")
        print(f"Impact report: {result.impact_report}")
    return 0


def cmd_revision_snapshot(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = snapshot_project(config, label=args.label)
    payload = {
        "snapshot_dir": str(result.snapshot_dir),
        "copied_paths": [str(path) for path in result.copied_paths],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("OK: snapshot created")
        print(f"Snapshot: {result.snapshot_dir}")
        print(f"Copied paths: {len(result.copied_paths)}")
    return 0


def cmd_revise_outline(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = revise_outline(
        config,
        from_chapter=args.from_chapter,
        change_description=args.change_description,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: outline revision markers written")
        print(f"From chapter: {result.from_chapter}")
        print(f"Anchors: {result.anchor_file}")
        print(f"Backup: {result.anchor_backup}")
        print(f"Cascade: {result.cascade_report}")
        print(f"RAG stale: {result.rag_stale_file}")
        print(f"Stale indexes: {result.stale_index_file}")
        print(f"Report: {result.report_file}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_editorial_review(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = editorial_review(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: editorial review written")
        print(f"Chapter: {result.chapter_number}")
        print(f"Status: {result.status}")
        print(f"Review: {result.review_file}")
        print(f"Task: {result.task_file}")
        print(f"Need human: {result.need_human}")
    return 0 if not result.need_human else 1


def cmd_editorial_batch_review(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = editorial_batch_review(config, chapter_start=args.chapter_start, chapter_end=args.chapter_end)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: editorial batch review written")
        print(f"Range: ch{result.chapter_start:03d}-ch{result.chapter_end:03d}")
        print(f"Reviews: {result.reviews}")
        print(f"Batch: {result.batch_file}")
        print(f"Need human: {result.need_human}")
    return 0 if not result.need_human else 1


def cmd_editorial_status(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = editorial_status(config)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: editorial status written")
        print(f"Status: {result.status_file}")
        print(f"Unresolved items: {result.unresolved_items}")
        print(f"Conditional passes: {result.conditional_passes}")
        print(f"Need human: {result.need_human}")
    return 0 if not result.need_human else 1


def cmd_editorial_submit_review(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = editorial_submit_review(config, chapter_number=args.chapter, role=args.role, file_path=args.file)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: editorial role result submitted")
        print(f"Chapter: {result.chapter_number}")
        print(f"Role: {result.role}")
        print(f"Accepted: {result.accepted}")
        print(f"Validation: {result.validation_file}")
        print(f"Aggregate: {result.aggregate_file}")
        print(f"Need human: {result.need_human}")
        print(f"Next command: {result.next_command}")
    return 0 if result.accepted else 1


def cmd_editorial_aggregate(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = editorial_aggregate(config, chapter_number=args.chapter)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: editorial results aggregated")
        print(f"Chapter: {result.chapter_number}")
        print(f"Aggregate: {result.aggregate_file}")
        print(f"Results: {len(result.result_files)}")
        print(f"Unresolved items: {result.unresolved_items}")
        print(f"Missing roles: {len(result.missing_roles)}")
        print(f"Duplicate role results: {len(result.duplicate_role_results)}")
        print(f"Invalid results: {len(result.invalid_results)}")
        print(f"Conditional passes: {result.conditional_passes}")
        print(f"Need human: {result.need_human}")
        print(f"Next command: {result.next_command}")
    return 0


def cmd_editorial_need_human(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    result = editorial_need_human(config, chapter_number=args.chapter, reason=args.reason)
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print("OK: editorial human review requested")
        print(f"Status: {result.status_file}")
        if result.human_request_file:
            print(f"Request: {result.human_request_file}")
    return 0


def cli_relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def cmd_reserved(args: argparse.Namespace) -> int:
    print(
        f"Command '{args.command}' is reserved by the workflow contract and is not available in this build.",
        file=sys.stderr,
    )
    return 2


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if getattr(args, "recovery_bypasses_project_lock", False):
            return int(args.func(args))
        if getattr(args, "mutates_project", False):
            config, output = _lock_context(args)
            if output is None and args.command != "recovery":
                recovery = recovery_status(config)
                lock_state = str(recovery.get("lock", {}).get("state") or "")
                transaction_blocked = any(
                    str(item).startswith("transaction:")
                    for item in recovery.get("blockers", [])
                )
                if lock_state in {"confirmed_dead", "unknown", "invalid"} or (
                    lock_state == "absent" and transaction_blocked
                ):
                    raise WorkflowError(
                        "storage_recovery_required: "
                        + str(recovery.get("next_command") or "longform-engine recovery status project.yaml --json")
                    )
            with acquire_project_lock(config, command=_command_label(args), output=output):
                if output is None and args.command != "recovery":
                    recovery = recovery_status(config)
                    if recovery["blocked"]:
                        raise WorkflowError(
                            "storage_recovery_required: "
                            + str(recovery.get("next_command") or "longform-engine recovery status project.yaml --json")
                        )
                return int(args.func(args))
        return int(args.func(args))
    except (ConfigError, GateError, ResearchError, RevisionError, WorkflowError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _configure_stdio() -> None:
    """Keep Chinese CLI output readable in tool and redirected environments."""

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _lock_context(args: argparse.Namespace) -> tuple[ConfigDocument, str | None]:
    if args.command == "init-project":
        config, output = prepared_init_context(args)
        return config, output
    if args.command == "open-book" and getattr(args, "interactive", False):
        config_path = Path(args.config).expanduser()
        if not config_path.exists():
            args._open_book_needs_init = True
            config, output = prepared_init_context(args)
            return config, output
    return load_project_config(Path(args.config).expanduser().resolve()), None


def _command_label(args: argparse.Namespace) -> str:
    parts = [args.command]
    for attr in (
        "skills_command",
        "benchmark_command",
        "intelligence_command",
        "fanfiction_command",
        "publication_command",
        "db_command",
        "models_command",
        "vector_command",
        "agent_task_command",
        "production_command",
        "rag_command",
        "graph_command",
        "memory_command",
        "creative_command",
        "pacing_command",
        "auto_command",
        "draft_command",
        "chapter_command",
        "recovery_command",
        "artifacts_command",
        "research_command",
        "revision_command",
        "editorial_command",
    ):
        value = getattr(args, attr, None)
        if value:
            parts.append(value)
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
