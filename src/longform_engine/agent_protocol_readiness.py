"""Read-only readiness gate for the Agent-first production data pipeline."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable
import json
import re
import subprocess
import sys

from longform_engine import __version__
from longform_engine.agent_isolation import assert_phase5_coverage
from longform_engine.distribution import tree_hash
from longform_engine.roles import reject_duplicate_json_keys


SCHEMA = "agent_data_pipeline_readiness_v1"
EVIDENCE_SCHEMA = "agent_data_pipeline_phase6_evidence_v1"
DEFAULT_CHECKLIST = Path("docs/AGENT_FIRST_DOCUMENT_PROTOCOL_AND_DATA_PIPELINE_CHECKLIST.md")
DEFAULT_EVIDENCE = Path("docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_EVIDENCE.json")
DEFAULT_REPORT = Path("docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.json")
SELF_REFERENTIAL_FILES = frozenset({DEFAULT_EVIDENCE.as_posix(), DEFAULT_REPORT.as_posix()})
REQUIRED_COMMAND_EVIDENCE = (
    "full_pytest",
    "skill_reference_sync",
    "resource_manifest",
    "skill_validation",
    "release_guards",
)
REQUIRED_SECURITY_EVIDENCE = (
    "prompt_injection",
    "role_overreach",
    "self_review",
    "bad_evidence",
    "transaction_rollback",
    "no_pollution",
)
CONTRACT_COMMANDS = (
    ("skill_reference_sync", (sys.executable, "scripts/sync_skill_references.py", "--check")),
    ("resource_manifest", (sys.executable, "scripts/build_resource_manifest.py", "--check")),
    ("skill_validation", (sys.executable, "scripts/validate_skills.py")),
    ("release_guards", (sys.executable, "scripts/release_surface_guards.py")),
)
PROTOCOL_SURFACE_FILES = (
    ".github/workflows/ci.yml",
    "scripts/check_agent_data_pipeline_readiness.py",
    "scripts/release_surface_guards.py",
    "src/longform_engine/agent_pipeline.py",
    "src/longform_engine/agent_isolation.py",
    "src/longform_engine/agent_normalization.py",
    "src/longform_engine/agent_protocol_readiness.py",
    "src/longform_engine/agent_results.py",
    "src/longform_engine/agent_tasks.py",
    "src/longform_engine/artifacts.py",
    "src/longform_engine/cli.py",
    "src/longform_engine/gates/pipeline.py",
    "src/longform_engine/orchestration/pipeline.py",
    "src/longform_engine/prompting.py",
    "src/longform_engine/production.py",
    "src/longform_engine/quality/review.py",
    "src/longform_engine/roles.py",
    "src/longform_engine/semantic/pipeline.py",
)


class AgentDataPipelineBlocked(RuntimeError):
    """Raised when a caller attempts to enable an unqualified data pipeline."""


def check_agent_data_pipeline_readiness(
    repository: str | Path,
    *,
    checklist_file: str | Path = DEFAULT_CHECKLIST,
    evidence_file: str | Path = DEFAULT_EVIDENCE,
    run_contracts: bool = True,
) -> dict[str, Any]:
    """Return a stable, read-only Phase 6 readiness report."""

    root = Path(repository).expanduser().resolve()
    checklist_path = resolve_repository_path(root, checklist_file)
    evidence_path = resolve_repository_path(root, evidence_file)
    checks: list[dict[str, Any]] = []

    phase_status = phase_zero_to_five_status(checklist_path)
    add_check(
        checks,
        "phase_0_to_5_complete",
        phase_status["ok"],
        phase_status,
        "Complete every Phase 0-5 checklist item before running Phase 6.",
    )

    role_error = ""
    try:
        assert_phase5_coverage()
    except ValueError as exc:
        role_error = str(exc)
    add_check(
        checks,
        "role_and_task_coverage",
        not role_error,
        {"status": "complete" if not role_error else "incomplete", "error": role_error},
        "Repair the task/role/output matrix and rerun Phase 5 isolation tests.",
    )

    role_resource_hash = directory_hash(root / "config" / "agent_roles")
    skill_records = {
        "codex": {
            "version": __version__,
            "sha256": directory_hash(root / "longform-novel-codex"),
        },
        "claude_code": {
            "version": __version__,
            "sha256": directory_hash(root / "longform-novel-claude"),
        },
    }
    surface_hash = protocol_surface_hash(root)
    evidence, evidence_error = load_evidence(evidence_path)
    evidence_errors = validate_evidence(
        evidence,
        repository=root,
        expected_surface_hash=surface_hash,
        expected_role_hash=role_resource_hash,
        expected_skill_records=skill_records,
    )
    if evidence_error:
        evidence_errors.insert(0, evidence_error)
    add_check(
        checks,
        "phase6_test_evidence",
        not evidence_errors,
        {
            "file": relative_or_absolute(root, evidence_path),
            "sha256": file_hash(evidence_path),
            "errors": evidence_errors,
        },
        "Run the complete Phase 6 test plan and refresh the evidence JSON.",
    )
    authorization_errors = validate_runtime_authorization(
        root,
        expected_surface_hash=surface_hash,
        expected_phase6_evidence_hash=file_hash(evidence_path),
    )
    add_check(
        checks,
        "runtime_pipeline_authorization",
        not authorization_errors,
        {
            "file": "config/agent_data_pipeline_authorization.json",
            "errors": authorization_errors,
        },
        "Refresh config/agent_data_pipeline_authorization.json from the current passing protocol surface.",
    )

    contract_results: dict[str, dict[str, Any]] = {}
    if run_contracts:
        for check_id, command in CONTRACT_COMMANDS:
            result = run_command(root, command)
            contract_results[check_id] = result
            add_check(
                checks,
                f"current_{check_id}",
                result["exit_code"] == 0,
                result,
                "Run `" + " ".join(command) + "` and fix the reported failure.",
            )

    git = git_provenance(root)
    failures = [item for item in checks if item["status"] == "fail"]
    report = {
        "schema": SCHEMA,
        "ready_for_data_pipeline": not failures,
        "repository": str(root),
        "provenance": {
            "git_commit": git["commit"],
            "dirty_tree_sha256": git["dirty_tree_sha256"],
            "dirty_file_count": git["dirty_file_count"],
            "dirty_tree_exclusions": sorted(SELF_REFERENTIAL_FILES),
            "engine_version": __version__,
            "skills": skill_records,
            "role_resource_sha256": role_resource_hash,
            "protocol_surface_sha256": surface_hash,
            "evidence_file": relative_or_absolute(root, evidence_path),
            "evidence_sha256": file_hash(evidence_path),
        },
        "test_evidence": evidence if isinstance(evidence, dict) else {},
        "checks": checks,
        "summary": {
            "passed": sum(item["status"] == "pass" for item in checks),
            "failures": len(failures),
        },
        "blocking_reasons": [item["id"] for item in failures],
        "next_command": (
            failures[0]["next_command"]
            if failures
            else "Phase 7 is authorized; continue with production next and keep apply/finalize explicit."
        ),
    }
    return report


def require_agent_data_pipeline_readiness(
    repository: str | Path,
    *,
    requested: bool,
    checklist_file: str | Path = DEFAULT_CHECKLIST,
    evidence_file: str | Path = DEFAULT_EVIDENCE,
) -> dict[str, Any] | None:
    """Block a future Phase 7 enablement request unless Phase 6 is current."""

    if not requested:
        return None
    report = check_agent_data_pipeline_readiness(
        repository,
        checklist_file=checklist_file,
        evidence_file=evidence_file,
        run_contracts=False,
    )
    if not report["ready_for_data_pipeline"]:
        reasons = ", ".join(report["blocking_reasons"]) or "unknown readiness failure"
        raise AgentDataPipelineBlocked(
            f"Agent-first data pipeline is blocked: {reasons}. Next: {report['next_command']}"
        )
    return report


def render_agent_data_pipeline_readiness(report: dict[str, Any]) -> str:
    state = "READY" if report.get("ready_for_data_pipeline") else "BLOCKED"
    lines = [
        f"Agent-first data pipeline readiness: {state}",
        f"Engine: {report.get('provenance', {}).get('engine_version') or 'unknown'}",
        f"Commit: {report.get('provenance', {}).get('git_commit') or 'unknown'}",
    ]
    for item in report.get("checks") or []:
        lines.append(f"[{str(item.get('status')).upper()}] {item.get('id')}")
        if item.get("status") == "fail":
            lines.append(f"  Next: {item.get('next_command')}")
    lines.append(f"Next command: {report.get('next_command')}")
    return "\n".join(lines)


def phase_zero_to_five_status(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    phases: list[dict[str, Any]] = []
    for number in range(6):
        match = re.search(
            rf"(?ms)^## Phase {number}\.[^\n]*\n(.*?)(?=^## Phase {number + 1}\.|^## Required Tests|\Z)",
            text,
        )
        states = re.findall(r"(?m)^- \[([ x~])\] ", match.group(1) if match else "")
        phases.append(
            {
                "phase": number,
                "items": len(states),
                "complete": states.count("x"),
                "pending": states.count(" "),
                "partial": states.count("~"),
            }
        )
    return {
        "ok": all(item["items"] > 0 and item["items"] == item["complete"] for item in phases),
        "checklist": str(path),
        "phases": phases,
    }


def validate_evidence(
    evidence: Any,
    *,
    repository: Path,
    expected_surface_hash: str,
    expected_role_hash: str,
    expected_skill_records: dict[str, dict[str, str]],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(evidence, dict) or evidence.get("schema") != EVIDENCE_SCHEMA:
        return [f"evidence schema must be {EVIDENCE_SCHEMA}."]
    if evidence.get("engine_version") != __version__:
        errors.append("evidence engine_version does not match the current Engine.")
    if evidence.get("protocol_surface_sha256") != expected_surface_hash:
        errors.append("evidence protocol_surface_sha256 is stale.")
    if evidence.get("role_resource_sha256") != expected_role_hash:
        errors.append("evidence role_resource_sha256 is stale.")
    if evidence.get("skills") != expected_skill_records:
        errors.append("evidence Skill versions or hashes are stale.")
    commit = str(evidence.get("git_commit_at_test") or "")
    dirty_hash = str(evidence.get("dirty_tree_sha256_at_test") or "")
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        errors.append("evidence git_commit_at_test is missing or invalid.")
    if not re.fullmatch(r"[0-9a-f]{64}", dirty_hash):
        errors.append("evidence dirty_tree_sha256_at_test is missing or invalid.")

    commands = evidence.get("commands")
    records = {
        str(item.get("id") or ""): item
        for item in commands
        if isinstance(item, dict)
    } if isinstance(commands, list) else {}
    for check_id in REQUIRED_COMMAND_EVIDENCE:
        item = records.get(check_id)
        if not item or item.get("status") != "pass" or item.get("exit_code") != 0:
            errors.append(f"missing passing command evidence: {check_id}.")
    full_pytest = records.get("full_pytest") or {}
    if not re.search(r"\b\d+ passed\b", str(full_pytest.get("summary") or "")):
        errors.append("full_pytest evidence must record the observed passed count.")

    payoff = evidence.get("realistic_payoff_fixture")
    if not isinstance(payoff, dict):
        errors.append("realistic_payoff_fixture evidence is missing.")
    else:
        if payoff.get("input_file_count") != 3 or payoff.get("max_files") != 3:
            errors.append("realistic payoff evidence must use exactly three inputs.")
        total = payoff.get("total_input_characters")
        context = payoff.get("context_characters")
        if not isinstance(total, int) or total > 15_000:
            errors.append("realistic payoff total input characters exceed 15K.")
        if not isinstance(context, int) or context > 6_000:
            errors.append("realistic payoff compact context exceeds 6K.")
        payoff_test = str(payoff.get("test_reference") or "")
        if not test_reference_exists(repository, payoff_test):
            errors.append("realistic payoff fixture test_reference is missing or stale.")

    security = evidence.get("security_tests")
    if not isinstance(security, dict):
        errors.append("security_tests evidence is missing.")
    else:
        for category in REQUIRED_SECURITY_EVIDENCE:
            tests = security.get(category)
            if not isinstance(tests, list) or not tests or not all(
                isinstance(item, str) and item.startswith("tests/") for item in tests
            ):
                errors.append(f"security test evidence is missing for {category}.")
            elif not all(test_reference_exists(repository, item) for item in tests):
                errors.append(f"security test evidence contains a stale reference for {category}.")
    return errors


def test_reference_exists(root: Path, reference: str) -> bool:
    if "::" not in reference:
        return False
    path_text, test_name = reference.split("::", 1)
    if not path_text.startswith("tests/") or not re.fullmatch(r"test_[A-Za-z0-9_]+", test_name):
        return False
    path = root / path_text
    return path.is_file() and f"def {test_name}(" in path.read_text(encoding="utf-8", errors="ignore")


def validate_runtime_authorization(
    root: Path,
    *,
    expected_surface_hash: str,
    expected_phase6_evidence_hash: str,
) -> list[str]:
    path = root / "config" / "agent_data_pipeline_authorization.json"
    if not path.is_file():
        return ["runtime authorization asset is missing."]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return [f"runtime authorization asset is invalid UTF-8 JSON: {exc}"]
    errors: list[str] = []
    if not isinstance(payload, dict) or payload.get("schema") != "agent_data_pipeline_authorization_v1":
        errors.append("runtime authorization schema is invalid.")
        return errors
    if payload.get("authorized") is not True:
        errors.append("runtime authorization is not enabled.")
    if payload.get("engine_version") != __version__:
        errors.append("runtime authorization engine_version is stale.")
    if payload.get("protocol_surface_sha256") != expected_surface_hash:
        errors.append("runtime authorization protocol_surface_sha256 is stale.")
    if payload.get("phase6_evidence_sha256") != expected_phase6_evidence_hash:
        errors.append("runtime authorization phase6_evidence_sha256 is stale.")
    return errors


def protocol_surface_hash(root: Path) -> str:
    paths: set[Path] = set()
    for relative in PROTOCOL_SURFACE_FILES:
        path = root / relative
        if path.is_file():
            paths.add(path)
    for directory in (root / "config" / "agent_roles",):
        if directory.is_dir():
            paths.update(item for item in directory.rglob("*") if item.is_file())
    tests = root / "tests"
    if tests.is_dir():
        paths.update(tests.glob("test_agent_document_protocol_phase*.py"))
        for name in (
            "test_agent_skill_integrity.py",
            "test_storage.py",
            "test_quality_contract_and_creative_interaction.py",
        ):
            path = tests / name
            if path.is_file():
                paths.add(path)
    return hash_paths(root, paths)


def directory_hash(path: Path) -> str:
    return tree_hash(path) if path.is_dir() else ""


def hash_paths(root: Path, paths: Iterable[Path]) -> str:
    digest = sha256()
    for path in sorted(set(paths), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def git_provenance(root: Path) -> dict[str, Any]:
    commit_result = run_git(root, "rev-parse", "HEAD")
    commit = commit_result.stdout.strip() if commit_result.returncode == 0 else ""
    changed: set[str] = set()
    for args in (
        ("diff", "--name-only", "HEAD", "--"),
        ("diff", "--cached", "--name-only", "--"),
        ("ls-files", "--others", "--exclude-standard"),
    ):
        result = run_git(root, *args)
        if result.returncode == 0:
            changed.update(line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip())
    changed.difference_update(SELF_REFERENTIAL_FILES)
    digest = sha256()
    for relative in sorted(changed):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        path = root / relative
        if path.is_file():
            digest.update(sha256(path.read_bytes()).digest())
        else:
            digest.update(b"<deleted>")
        digest.update(b"\0")
    return {
        "commit": commit,
        "dirty_tree_sha256": digest.hexdigest(),
        "dirty_file_count": len(changed),
    }


def load_evidence(path: Path) -> tuple[Any, str]:
    if not path.is_file():
        return {}, f"Phase 6 evidence file is missing: {path}"
    try:
        return (
            json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_json_keys),
            "",
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        return {}, f"Phase 6 evidence is invalid UTF-8 JSON: {exc}"


def run_command(root: Path, command: tuple[str, ...]) -> dict[str, Any]:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": list(command), "exit_code": 1, "summary": str(exc)}
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return {
        "command": list(command),
        "exit_code": result.returncode,
        "summary": output.splitlines()[-1] if output else "completed",
    }


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ("git", *args),
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(("git", *args), 1, "", str(exc))


def resolve_repository_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    resolved = path.expanduser().resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Readiness path must remain inside the repository: {value}") from exc
    return resolved


def add_check(
    checks: list[dict[str, Any]],
    check_id: str,
    ok: bool,
    detail: Any,
    next_command: str,
) -> None:
    checks.append(
        {
            "id": check_id,
            "status": "pass" if ok else "fail",
            "detail": detail,
            "next_command": "" if ok else next_command,
        }
    )


def file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
