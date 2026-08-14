"""Static guards for release-surface safety invariants."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from longform_engine.agent_protocol_readiness import protocol_surface_hash  # noqa: E402


SRC = ROOT / "src" / "longform_engine"

LEGACY_PATHS = (
    "00_bible",
    "01_outline",
    "02_memory",
    "03_manuscript",
    "04_editing",
    "05_rag",
    "06_runtime",
)

ALLOW_LEGACY = {
    "src/longform_engine/config/loader.py",
}

# agent_tasks.py references canonical paths only to reject them during strict
# AgentTaskManifest validation; it must not become an apply/finalize writer.
ALLOW_FINAL_WRITES = {
    "src/longform_engine/artifacts.py",
    "src/longform_engine/agent_tasks.py",
    "src/longform_engine/character_expression.py",
    # Completion verifies final hashes but never writes manuscript files.
    "src/longform_engine/completion.py",
    "src/longform_engine/orchestration/pipeline.py",
    "src/longform_engine/intelligence/pipeline.py",
    "src/longform_engine/legacy.py",
    "src/longform_engine/revision/pipeline.py",
    "src/longform_engine/rag/pipeline.py",
    "src/longform_engine/memory/pipeline.py",
    "src/longform_engine/db/sqlite_index.py",
    "src/longform_engine/graph/pipeline.py",
    "src/longform_engine/research/pipeline.py",
    "src/longform_engine/gates/pipeline.py",
    "src/longform_engine/semantic/pipeline.py",
    "src/longform_engine/storage/layout.py",
    "src/longform_engine/publication.py",
}

ALLOW_GRAPH_WRITES = {
    "src/longform_engine/agent_tasks.py",
    "src/longform_engine/graph/pipeline.py",
    "src/longform_engine/research/pipeline.py",
    "src/longform_engine/semantic/pipeline.py",
    "src/longform_engine/storage/layout.py",
}

ALLOW_AGENT_TO_CANON = {
    "src/longform_engine/agent_tasks.py",
    "src/longform_engine/config/loader.py",
    "src/longform_engine/db/sqlite_index.py",
    "src/longform_engine/orchestration/pipeline.py",
    "src/longform_engine/gates/pipeline.py",
    "src/longform_engine/graph/pipeline.py",
    "src/longform_engine/memory/pipeline.py",
    "src/longform_engine/rag/pipeline.py",
    "src/longform_engine/storage/layout.py",
    "src/longform_engine/vectorstore/pipeline.py",
    "src/longform_engine/cli.py",
    "src/longform_engine/intelligence/pipeline.py",
    "src/longform_engine/legacy.py",
    "src/longform_engine/semantic/pipeline.py",
}

# These modules may read current canonical evidence, but their only permitted
# write is a fixed workbench diagnostic. They are checked separately below.
READ_ONLY_CANONICAL_VALIDATORS = {
    "src/longform_engine/agent_normalization.py",
}

DIRECT_LLM_PATTERNS = (
    r"\bfrom\s+openai\b",
    r"\bimport\s+openai\b",
    r"\bfrom\s+anthropic\b",
    r"\bimport\s+anthropic\b",
    r"chat\.completions\.create",
    r"messages\.create",
    r"responses\.create",
)

EXTERNAL_LLM_KEY_PATTERNS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MOONSHOT_API_KEY",
    "GLM_API_KEY",
    "MINIMAX_API_KEY",
)

DIRECT_WRITER_PATTERNS = (
    r"\batomic_write_text\b",
    r"\bwrite_json\b",
    r"\bwrite_manifest\b",
    r"\bupdate_task_status\b",
    r"\bmark_tasks_for_output\b",
    r"\bmark_tasks_for_chapter_type\b",
    r"\bsync_database\b",
    r"\bapply_transaction\b",
    r"\.write_text\s*\(",
    r"\.write_bytes\s*\(",
    r"\bshutil\.copy\b",
    r"INSERT\s+INTO",
    r"\bsqlite3\.",
)

REQUIRED_RELEASE_CONTRACT_MARKERS = (
    (
        "docs/AGENT_COLLABORATION_HARDENING_CHECKLIST.md",
        (
            "strict manifest validation",
            "release guard 增加 strict manifest validation 文档/测试入口",
            "release guard 增加 `content_expand` manifest 覆盖检查",
            "release guard 增加 lifecycle states 覆盖检查",
            "release guard 增加 transaction rollback 覆盖检查",
        ),
    ),
    (
        "docs/AGENT_COLLABORATION_HARDENING.md",
        (
            "AgentTaskManifest v1/v2",
            "content_expand",
            "`revision rollback`: affected tasks -> `rolled_back`",
            "rollback_restores_touched_paths",
        ),
    ),
    (
        "src/longform_engine/agent_tasks.py",
        (
            "AGENT_TASK_STATUSES",
            '"awaiting_agent"',
            '"submitted"',
            '"validated"',
            '"invalid"',
            '"applied"',
            '"superseded"',
            '"rolled_back"',
            '"content_expand"',
            "validate_manifest_strict",
        ),
    ),
    (
        "src/longform_engine/creative/pipeline.py",
        (
            'task_type="content_expand"',
            'output_schema="markdown_expanded_candidate"',
            'command="creative expand-check"',
        ),
    ),
    (
        "src/longform_engine/storage/project.py",
        (
            "apply_transaction",
            "rollback_restores_touched_paths",
            "canonical_write_transaction_rollback",
        ),
    ),
    (
        "tests/test_agent_task_protocol.py",
        (
            "test_strict_manifest_validation_rejects_unknown_type_and_canonical_output",
            "validate_manifest_strict",
            "content_expand",
            "AGENT_TASK_STATUSES",
            '"superseded"',
            '"rolled_back"',
        ),
    ),
    (
        "tests/test_creative_operator.py",
        (
            "test_expand_task_and_check_repair_short_chapter_without_pollution",
            'manifest["task_type"] == "content_expand"',
            "validate_manifest_strict",
        ),
    ),
    (
        "tests/test_storage.py",
        (
            "test_apply_transaction_writes_report_and_rolls_back_touched_paths",
            "rollback_restores_touched_paths",
            "chapter_finalize_ch001.rollback.json",
        ),
    ),
    (
        "docs/AGENT_EXPERIENCE_ORCHESTRATION.md",
        (
            "production_status_v1",
            "experience layer release guard",
            "no LLM in Python CLI",
            "no automatic chapter finalize",
        ),
    ),
    (
        "docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md",
        (
            "- [x] release guard 增加体验层命令 guard marker。",
            "- [x] release guard 检查 `production loop` 不 import OpenAI/Anthropic。",
            "- [x] release guard 检查 `production loop` 不直接写 final/RAG/graph/SQLite。",
            "- [x] release guard 检查 `agent-task brief` 是只读渲染。",
            "- [x] no-pollution E2E 覆盖 production loop 暂停路径。",
        ),
    ),
    (
        "tests/test_agent_skill_integrity.py",
        (
            "test_release_guard_covers_experience_orchestration_contracts",
            "check_experience_layer_guards",
            "DIRECT_WRITER_PATTERNS",
        ),
    ),
    (
        "tests/test_production_experience.py",
        (
            "test_production_loop_no_pollution_pause_path",
            "PRODUCTIONLOOP_NOPOLLUTION",
            "query_table",
        ),
    ),
    (
        "src/longform_engine/semantic/pipeline.py",
        (
            'SCHEMA = "chapter_semantic_bundle_v1"',
            "apply_transaction",
            "candidate_sha256",
            "Cannot replace canonical semantic ledger",
            "def semantic_rebuild",
            "source_of_truth\": \"semantic_ledger",
            "chapter close",
        ),
    ),
    (
        "tests/test_semantic_knowledge.py",
        (
            "test_unified_semantic_bundle_materializes_evidence_bound_views",
            "test_semantic_validation_rejects_hash_and_evidence_mismatch",
            "semantic ledger routed chapter",
            "event:stale-derived-fact",
        ),
    ),
    (
        "src/longform_engine/artifacts.py",
        (
            "ensure_compaction_boundary",
            "failed verification before compaction",
            "contains an older version of loose artifact",
        ),
    ),
    (
        "docs/SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION_CHECKLIST.md",
        (
            "chapter_semantic_bundle_v1",
            "invalid bundle",
            "SQLite",
            "Definition Of Done",
        ),
    ),
)


def main() -> int:
    failures: list[str] = []
    for relative in ("config/default.engine.yaml", "templates/qidian-longform/project.yaml"):
        text = (ROOT / relative).read_text(encoding="utf-8", errors="ignore")
        for forbidden in ("api_provider", "OPENAI_API_KEY", "OPENAI_BASE_URL", "default_provider"):
            if forbidden in text:
                failures.append(f"public config must not expose provider placeholder `{forbidden}`: {relative}")
    public_runtime = "\n".join(
        (SRC / relative).read_text(encoding="utf-8", errors="ignore")
        for relative in (Path("config/loader.py"), Path("orchestration/pipeline.py"))
    )
    if "api_provider" in public_runtime:
        failures.append("api_provider must not remain a public runtime mode or late-failure branch.")
    for path in iter_text_files(SRC):
        rel = relpath(path)
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in DIRECT_LLM_PATTERNS:
            if re.search(pattern, text):
                failures.append(f"direct external LLM call/import pattern `{pattern}` appears in {rel}")
        for key in EXTERNAL_LLM_KEY_PATTERNS:
            if key in text:
                failures.append(f"external LLM API key requirement `{key}` appears in production source: {rel}")
        if rel not in ALLOW_LEGACY:
            for legacy in LEGACY_PATHS:
                if legacy in text:
                    failures.append(f"legacy path `{legacy}` appears in {rel}")
        if (
            rel not in ALLOW_FINAL_WRITES
            and rel not in READ_ONLY_CANONICAL_VALIDATORS
            and re.search(r"40_manuscript[\"'/\\ ]+[/\\]?final|40_manuscript/final", text)
        ):
            failures.append(f"final manuscript path referenced outside allowed modules: {rel}")
        if rel not in ALLOW_GRAPH_WRITES and re.search(r"story_graph\\.json.*write|write_.*story_graph|30_state/story_graph\\.json", text):
            failures.append(f"story graph write/reference outside allowed modules: {rel}")
        if rel.endswith("rag/pipeline.py") and "research_inbox" in text:
            failures.append("RAG pipeline must not reference research_inbox directly.")
        if (
            rel not in ALLOW_AGENT_TO_CANON
            and rel not in READ_ONLY_CANONICAL_VALIDATORS
            and re.search(r"agent_drafts|repair_candidates|editorial_reviews/results|semantic_pacing_result", text)
        ):
            has_write_call = re.search(r"atomic_write_text|write_json|shutil\.copy|sync_database|INSERT\s+INTO", text)
            if has_write_call and re.search(r"40_manuscript[/\\]final|60_rag|story_graph\.json|70_runtime[/\\]db", text):
                failures.append(f"agent output path and canonical path are coupled outside apply/finalize modules: {rel}")
        if rel in READ_ONLY_CANONICAL_VALIDATORS:
            failures.extend(check_read_only_canonical_validator(rel, text))
    failures.extend(check_experience_layer_guards())
    failures.extend(check_agent_first_protocol_isolation_guards())
    failures.extend(check_agent_data_pipeline_readiness_guards())
    failures.extend(check_agent_first_production_pipeline_guards())
    failures.extend(check_artifact_compaction_guards())
    failures.extend(check_public_distribution_guards())
    failures.extend(check_required_release_contract_markers())

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("OK: release surface guards passed")
    return 0


def iter_text_files(root: Path):
    for path in root.rglob("*.py"):
        if "__pycache__" not in path.parts:
            yield path


def relpath(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def check_required_release_contract_markers() -> list[str]:
    failures: list[str] = []
    for relative_file, markers in REQUIRED_RELEASE_CONTRACT_MARKERS:
        path = ROOT / relative_file
        if not path.exists():
            failures.append(f"release guard required contract file is missing: {relative_file}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in markers:
            if marker not in text:
                failures.append(f"release guard contract marker `{marker}` missing from {relative_file}")
    return failures


def check_experience_layer_guards() -> list[str]:
    failures: list[str] = []
    production_path = SRC / "production.py"
    cli_path = SRC / "cli.py"
    if not production_path.exists():
        return ["experience layer module is missing: src/longform_engine/production.py"]
    if not cli_path.exists():
        return ["CLI module is missing: src/longform_engine/cli.py"]
    production_text = production_path.read_text(encoding="utf-8", errors="ignore")
    cli_text = cli_path.read_text(encoding="utf-8", errors="ignore")

    for marker in (
        "def production_status",
        "def production_next",
        "def production_board",
        "def production_loop",
        "def agent_task_brief",
        "normalize_contract_json",
    ):
        if marker not in production_text:
            failures.append(f"experience layer guard marker `{marker}` missing from production.py")
    for marker in (
        "production_status_cmd",
        "production_next_cmd",
        "production_board_cmd",
        "production_loop_cmd",
        "agent_task_brief_cmd",
    ):
        if marker not in cli_text:
            failures.append(f"experience layer CLI marker `{marker}` missing from cli.py")

    for pattern in DIRECT_LLM_PATTERNS:
        if re.search(pattern, production_text):
            failures.append(f"production experience layer must not import/call external LLM pattern `{pattern}`")
    for key in EXTERNAL_LLM_KEY_PATTERNS:
        if key in production_text:
            failures.append(f"production experience layer must not require external LLM key `{key}`")

    if re.search("|".join(DIRECT_WRITER_PATTERNS), production_text):
        failures.append("production.py must not perform direct file/database writes; call existing CLI pipeline functions instead.")

    brief_body = function_body(production_text, "agent_task_brief")
    if '"read_only": True' not in brief_body:
        failures.append("agent_task_brief JSON contract must remain read_only.")
    if re.search("|".join(DIRECT_WRITER_PATTERNS), brief_body):
        failures.append("agent_task_brief must remain a read-only renderer.")

    loop_body = function_body(production_text, "production_loop")
    if re.search("|".join(DIRECT_WRITER_PATTERNS), loop_body):
        failures.append("production_loop must not directly write final/RAG/graph/SQLite; it may only call deterministic pipeline commands.")
    return failures


def check_agent_first_protocol_isolation_guards() -> list[str]:
    """Keep Phase 5 parsers isolated behind the authorized Phase 7 integration owner."""

    failures: list[str] = []
    production_path = SRC / "production.py"
    production_text = production_path.read_text(encoding="utf-8", errors="ignore")
    for module_name in ("agent_isolation", "agent_results"):
        if re.search(rf"(?:from|import)\s+longform_engine\.{module_name}\b", production_text):
            failures.append(
                f"production.py must not import Phase 5 isolated protocol module `{module_name}` before readiness."
            )

    for relative in (
        "src/longform_engine/agent_isolation.py",
        "src/longform_engine/agent_results.py",
    ):
        path = ROOT / relative
        if not path.is_file():
            failures.append(f"Phase 5 isolated protocol module is missing: {relative}")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if re.search("|".join(DIRECT_WRITER_PATTERNS), text):
            failures.append(f"Phase 5 isolated protocol module must remain write-free: {relative}")
        for pattern in DIRECT_LLM_PATTERNS:
            if re.search(pattern, text):
                failures.append(
                    f"Phase 5 isolated protocol module calls an external LLM pattern `{pattern}`: {relative}"
                )
    isolation_text = (SRC / "agent_isolation.py").read_text(encoding="utf-8", errors="ignore")
    for marker in (
        "LEGACY_COMPATIBILITY_TASK_TYPES",
        "compile_isolated_agent_package",
        "validate_isolated_agent_submission",
        "legacy task `{task_type}` is compatibility-read-only",
    ):
        if marker not in isolation_text:
            failures.append(f"Phase 5 isolation marker `{marker}` is missing from agent_isolation.py")
    return failures


def check_agent_first_production_pipeline_guards() -> list[str]:
    """Require one readiness-bound owner for Phase 7 work orders and lifecycle mutation."""

    failures: list[str] = []
    integration_path = SRC / "agent_pipeline.py"
    production_path = SRC / "production.py"
    authorization_path = ROOT / "config" / "agent_data_pipeline_authorization.json"
    evidence_path = ROOT / "docs" / "baselines" / "AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_EVIDENCE.json"
    for path in (integration_path, production_path, authorization_path, evidence_path):
        if not path.is_file():
            failures.append(f"Agent-first production pipeline asset is missing: {relpath(path)}")
    if failures:
        return failures

    integration = integration_path.read_text(encoding="utf-8", errors="ignore")
    production = production_path.read_text(encoding="utf-8", errors="ignore")
    for marker in (
        "require_agent_data_pipeline_readiness",
        "compile_production_agent_package",
        "validate_production_agent_result",
        "controlled_feedback",
        "agent-task result-validate",
    ):
        if marker not in integration:
            failures.append(f"Phase 7 integration marker `{marker}` is missing from agent_pipeline.py")
    for marker in (
        "require_agent_first_production_pipeline",
        "compile_production_agent_package",
        "chapter_stage_task_types",
    ):
        if marker not in production:
            failures.append(f"Phase 7 production marker `{marker}` is missing from production.py")
    for pattern in DIRECT_LLM_PATTERNS:
        if re.search(pattern, integration):
            failures.append(f"Phase 7 integration must not call an external LLM pattern `{pattern}`")
    try:
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        failures.append(f"Phase 7 authorization JSON is unreadable: {exc}")
        return failures
    expected_hash = protocol_surface_hash(ROOT)
    if authorization.get("schema") != "agent_data_pipeline_authorization_v1":
        failures.append("Phase 7 runtime authorization schema is invalid")
    if authorization.get("authorized") is not True:
        failures.append("Phase 7 runtime authorization is not enabled")
    if authorization.get("protocol_surface_sha256") != expected_hash:
        failures.append("Phase 7 runtime authorization does not match the readiness report surface hash")
    expected_evidence_hash = sha256(evidence_path.read_bytes()).hexdigest()
    if authorization.get("phase6_evidence_sha256") != expected_evidence_hash:
        failures.append("Phase 7 runtime authorization does not match the Phase 6 evidence hash")
    return failures


def check_artifact_compaction_guards() -> list[str]:
    """Allow final evidence reads only behind a non-canonical archive member allowlist."""

    relative = "src/longform_engine/artifacts.py"
    path = ROOT / relative
    if not path.is_file():
        return [f"artifact compaction module is missing: {relative}"]
    text = path.read_text(encoding="utf-8", errors="ignore")
    failures: list[str] = []
    for marker in (
        "chapter_artifact_archive_v3",
        "AUDIT_MANIFEST_MEMBER",
        "AUDIT_BLOB_PREFIX",
        "RETAINED_EVIDENCE",
        "ARCHIVABLE_PREFIXES",
        "ensure_archivable_chapter_path(relative, chapter_number)",
        "Archive entry is outside non-canonical chapter artifact lanes",
        'scan_root == "40_manuscript/final" and re.fullmatch',
    ):
        if marker not in text:
            failures.append(f"artifact compaction safety marker `{marker}` is missing")
    restore_body = function_body(text, "restore_artifacts")
    if "ensure_archivable_chapter_path(relative, chapter_number)" not in restore_body:
        failures.append("artifact restore must validate every member against the chapter-lane allowlist")
    if re.search(r"40_manuscript/final/ch\{chapter.*\.md", restore_body):
        failures.append("artifact restore must not construct a final manuscript target")
    return failures


def check_agent_data_pipeline_readiness_guards() -> list[str]:
    """Require one local/CI gate before Phase 7 can alter production routing."""

    failures: list[str] = []
    readiness_path = SRC / "agent_protocol_readiness.py"
    script_path = ROOT / "scripts" / "check_agent_data_pipeline_readiness.py"
    ci_path = ROOT / ".github" / "workflows" / "ci.yml"
    for path in (readiness_path, script_path, ci_path):
        if not path.is_file():
            failures.append(f"Agent data-pipeline readiness guard file is missing: {relpath(path)}")
    if failures:
        return failures
    readiness_text = readiness_path.read_text(encoding="utf-8", errors="ignore")
    script_text = script_path.read_text(encoding="utf-8", errors="ignore")
    ci_text = ci_path.read_text(encoding="utf-8", errors="ignore")
    for marker in (
        "agent_data_pipeline_readiness_v1",
        "ready_for_data_pipeline",
        "require_agent_data_pipeline_readiness",
        "AgentDataPipelineBlocked",
        "protocol_surface_sha256",
        "dirty_tree_sha256",
    ):
        if marker not in readiness_text:
            failures.append(f"Agent readiness marker `{marker}` is missing from agent_protocol_readiness.py")
    if "check_agent_data_pipeline_readiness" not in script_text:
        failures.append("local Agent readiness script does not call the shared checker")
    if "python scripts/check_agent_data_pipeline_readiness.py --json" not in ci_text:
        failures.append("CI does not enforce the Agent data-pipeline readiness checker")
    for path, text in ((readiness_path, readiness_text), (script_path, script_text)):
        for pattern in DIRECT_LLM_PATTERNS:
            if re.search(pattern, text):
                failures.append(
                    f"Agent readiness guard must not call an external LLM pattern `{pattern}`: {relpath(path)}"
                )
    return failures


def check_public_distribution_guards() -> list[str]:
    failures: list[str] = []
    benchmark_path = SRC / "benchmark.py"
    readiness_path = SRC / "release_readiness.py"
    cli_path = SRC / "cli.py"
    for path in (benchmark_path, readiness_path, cli_path):
        if not path.exists():
            failures.append(f"public distribution module is missing: {relpath(path)}")
    if failures:
        return failures

    benchmark_text = benchmark_path.read_text(encoding="utf-8", errors="ignore")
    readiness_text = readiness_path.read_text(encoding="utf-8", errors="ignore")
    cli_text = cli_path.read_text(encoding="utf-8", errors="ignore")
    for marker in (
        "BENCHMARK_RECORD_SCHEMA",
        "BENCHMARK_COMPARISON_SCHEMA",
        "def record_benchmark_chapter",
        "def compare_benchmarks",
        '"stores_manuscript_body": False',
        '"manuscript_bodies_included": False',
    ):
        if marker not in benchmark_text:
            failures.append(f"benchmark no-body/comparison marker `{marker}` is missing")
    for canonical in ("40_manuscript/final", "60_rag", "story_graph.json", "30_state/tcs", "70_runtime/db"):
        if canonical in benchmark_text:
            failures.append(f"benchmark module must not reference canonical storage `{canonical}`")

    for marker in ("release_readiness_v1", "EXPECTED_REMOTE", "def check_release_readiness"):
        if marker not in readiness_text:
            failures.append(f"release readiness marker `{marker}` is missing")
    forbidden_git_mutation = re.compile(r'run_git\(root,\s*["\'](?:commit|push|tag|reset|checkout|clean|add)["\']')
    if forbidden_git_mutation.search(readiness_text):
        failures.append("release readiness must remain diagnostic and must not execute mutating Git commands")

    for marker in ("cmd_release_check", "cmd_benchmark_record", "cmd_benchmark_compare"):
        if marker not in cli_text:
            failures.append(f"public distribution CLI marker `{marker}` is missing")
    return failures


def check_read_only_canonical_validator(relative: str, text: str) -> list[str]:
    """Allow canonical reads only when every write is a fixed workbench diagnostic."""

    failures: list[str] = []
    diagnostic_body = function_body(text, "write_agent_result_diagnostic")
    required_path = '"50_workbench" / "agent_tasks" / "diagnostics"'
    if required_path not in diagnostic_body:
        failures.append(f"read-only canonical validator lacks a fixed diagnostic lane: {relative}")
    if "atomic_write_text" not in diagnostic_body:
        failures.append(f"read-only canonical validator diagnostic must use atomic write: {relative}")
    remainder = text.replace(diagnostic_body, "")
    remainder = re.sub(
        r"from\s+longform_engine\.storage\s+import\s+atomic_write_text\s*",
        "",
        remainder,
    )
    if re.search("|".join(DIRECT_WRITER_PATTERNS), remainder):
        failures.append(f"read-only canonical validator writes outside its diagnostic function: {relative}")
    for forbidden in (
        "apply_transaction",
        "sync_database",
        "update_task_status",
        "mark_tasks_for_output",
        "mark_tasks_for_chapter_type",
    ):
        if re.search(rf"\b{forbidden}\b", text):
            failures.append(f"read-only canonical validator imports/calls `{forbidden}`: {relative}")
    return failures


def function_body(text: str, name: str) -> str:
    match = re.search(rf"^def\s+{re.escape(name)}\b.*?(?=^def\s+|\Z)", text, flags=re.M | re.S)
    return match.group(0) if match else ""


if __name__ == "__main__":
    raise SystemExit(main())
