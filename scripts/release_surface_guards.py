"""Static guards for release-surface safety invariants."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
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
    "src/longform_engine/agent_tasks.py",
    "src/longform_engine/orchestration/pipeline.py",
    "src/longform_engine/revision/pipeline.py",
    "src/longform_engine/rag/pipeline.py",
    "src/longform_engine/memory/pipeline.py",
    "src/longform_engine/db/sqlite_index.py",
    "src/longform_engine/graph/pipeline.py",
    "src/longform_engine/research/pipeline.py",
    "src/longform_engine/gates/pipeline.py",
    "src/longform_engine/storage/layout.py",
}

ALLOW_GRAPH_WRITES = {
    "src/longform_engine/agent_tasks.py",
    "src/longform_engine/graph/pipeline.py",
    "src/longform_engine/research/pipeline.py",
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
            "AgentTaskManifest v1",
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
)


def main() -> int:
    failures: list[str] = []
    orchestration_text = (SRC / "orchestration" / "pipeline.py").read_text(encoding="utf-8", errors="ignore")
    if "writing.mode api_provider is reserved for a future provider implementation" not in orchestration_text:
        failures.append("api_provider mode must remain explicitly disabled in orchestration.")
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
        if rel not in ALLOW_FINAL_WRITES and re.search(r"40_manuscript[\"'/\\ ]+[/\\]?final|40_manuscript/final", text):
            failures.append(f"final manuscript path referenced outside allowed modules: {rel}")
        if rel not in ALLOW_GRAPH_WRITES and re.search(r"story_graph\\.json.*write|write_.*story_graph|30_state/story_graph\\.json", text):
            failures.append(f"story graph write/reference outside allowed modules: {rel}")
        if rel.endswith("rag/pipeline.py") and "research_inbox" in text:
            failures.append("RAG pipeline must not reference research_inbox directly.")
        if rel not in ALLOW_AGENT_TO_CANON and re.search(r"agent_drafts|repair_candidates|editorial_reviews/results|semantic_pacing_result", text):
            has_write_call = re.search(r"atomic_write_text|write_json|shutil\.copy|sync_database|INSERT\s+INTO", text)
            if has_write_call and re.search(r"40_manuscript[/\\]final|60_rag|story_graph\.json|70_runtime[/\\]db", text):
                failures.append(f"agent output path and canonical path are coupled outside apply/finalize modules: {rel}")
    failures.extend(check_experience_layer_guards())
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


def function_body(text: str, name: str) -> str:
    match = re.search(rf"^def\s+{re.escape(name)}\b.*?(?=^def\s+|\Z)", text, flags=re.M | re.S)
    return match.group(0) if match else ""


if __name__ == "__main__":
    raise SystemExit(main())
