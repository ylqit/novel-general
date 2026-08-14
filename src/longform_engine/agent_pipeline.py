"""Authorized integration boundary for the Agent-first production protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import re

from longform_engine import __version__
from longform_engine.agent_isolation import (
    IsolatedAgentPackage,
    compile_isolated_agent_package,
    render_host_work_order,
    validate_isolated_agent_submission,
)
from longform_engine.agent_normalization import (
    AgentResultNormalization,
    write_agent_result_diagnostic,
)
from longform_engine.agent_protocol_readiness import require_agent_data_pipeline_readiness
from longform_engine.agent_tasks import list_manifests, update_task_status, utc_now
from longform_engine.resources import resource_path, resource_root
from longform_engine.prompting import PromptCompilation
from longform_engine.storage import apply_transaction


PIPELINE_SCHEMA = "agent_first_production_pipeline_v1"
AUTHORIZATION_SCHEMA = "agent_data_pipeline_authorization_v1"
FEEDBACK_SCHEMA = "controlled_agent_feedback_v1"
SAFE_CODE_PATTERN = re.compile(r"[^A-Za-z0-9_.:-]+")
FEEDBACK_FILES = (
    ("gate", "50_workbench/gate_artifacts/ch{chapter:03d}/gate_result.json"),
    ("humanizer", "50_workbench/humanizer_tasks/ch{chapter:03d}.humanize_check.json"),
    ("payoff", "50_workbench/quality_reviews/ch{chapter:03d}.reader_payoff.validation.json"),
    ("pacing", "50_workbench/gate_artifacts/ch{chapter:03d}/semantic_pacing_result.json"),
    ("editorial", "50_workbench/editorial_reviews/ch{chapter:03d}.aggregate.json"),
)


class AgentProductionPipelineError(ValueError):
    """Raised before an unauthorized or invalid Agent result can advance lifecycle state."""


@dataclass(frozen=True)
class ProductionAgentResult:
    schema: str
    ok: bool
    status: str
    task_id: str
    task_type: str
    lifecycle_status: str
    diagnostic_file: str
    normalization: AgentResultNormalization
    next_command: str


@lru_cache(maxsize=1)
def require_agent_first_production_pipeline() -> dict[str, Any]:
    """Require source-tree Phase 6 evidence or an installed-wheel authorization asset."""

    root = resource_root()
    checklist = root / "docs" / "AGENT_FIRST_DOCUMENT_PROTOCOL_AND_DATA_PIPELINE_CHECKLIST.md"
    if checklist.is_file():
        report = require_agent_data_pipeline_readiness(root, requested=True)
        if report is None:
            raise AgentProductionPipelineError("Phase 6 readiness unexpectedly returned no report.")
        return {
            "schema": AUTHORIZATION_SCHEMA,
            "authorized": True,
            "source": "source_tree_phase6_report",
            "engine_version": __version__,
            "protocol_surface_sha256": report["provenance"]["protocol_surface_sha256"],
        }

    path = resource_path("config", "agent_data_pipeline_authorization.json")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AgentProductionPipelineError(f"Installed Agent pipeline authorization is unreadable: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != AUTHORIZATION_SCHEMA
        or payload.get("authorized") is not True
        or payload.get("engine_version") != __version__
        or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("protocol_surface_sha256") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("phase6_evidence_sha256") or ""))
    ):
        raise AgentProductionPipelineError("Installed Agent pipeline authorization is missing, stale, or invalid.")
    return dict(payload) | {"source": "installed_release_asset"}


def compile_production_agent_package(
    root: Path,
    manifest: dict[str, Any],
    *,
    host: str,
) -> IsolatedAgentPackage:
    """Compile the production work order only after the shared readiness gate authorizes it."""

    require_agent_first_production_pipeline()
    feedback = controlled_feedback(root.resolve(), manifest)
    package = compile_isolated_agent_package(
        root.resolve(),
        manifest,
        host=host,
        controlled_feedback=feedback["items"],
    )
    result_path = package.output_contract.companion_output_path or package.output_contract.primary_output_path
    document_arg = (
        f" --document {package.output_contract.primary_output_path}"
        if package.output_contract.companion_output_path
        else ""
    )
    protocol_command = (
        f"longform-engine agent-task result-validate project.yaml {package.task_id} "
        f"--file {result_path}{document_arg}"
    )
    handoff = (
        "\n## Protocol Validation Order\n\n"
        f"1. Register and structurally validate the role output: `{protocol_command}`\n"
        f"2. Run domain validation: `{package.output_contract.validate_command}`\n"
        "3. Stop before apply/finalize unless the user explicitly runs the declared command.\n"
    )
    semantic_markdown = package.prompt.markdown.rstrip() + "\n" + handoff
    prompt_hash = sha256(semantic_markdown.encode("utf-8")).hexdigest()
    return replace(
        package,
        prompt=PromptCompilation(payload=package.prompt.payload, markdown=semantic_markdown),
        prompt_hash=prompt_hash,
        host_work_order=render_host_work_order(
            host=host,
            semantic_markdown=semantic_markdown,
            semantic_hash=prompt_hash,
        ),
    )


def validate_production_agent_result(
    root: Path,
    manifest: dict[str, Any],
    *,
    result_file: str | Path,
    document_file: str | Path | None = None,
) -> ProductionAgentResult:
    """Validate one role output and atomically advance only its control-plane lifecycle."""

    require_agent_first_production_pipeline()
    project_root = root.resolve()
    task_id = str(manifest.get("task_id") or "")
    indexed = next((item for item in list_manifests(project_root) if item.get("task_id") == task_id), None)
    if indexed is None:
        raise AgentProductionPipelineError("Agent result task is not registered in the project task index.")

    validation = validate_isolated_agent_submission(
        project_root,
        manifest,
        result_file=result_file,
        document_file=document_file,
    )
    normalization = validation.normalization
    if normalization is None:
        raise AgentProductionPipelineError("Agent result could not be normalized: " + "; ".join(validation.errors))

    lifecycle_status = "submitted" if validation.ok else "invalid"
    agent_task_dir = project_root / "50_workbench" / "agent_tasks"
    output_path = (project_root / normalization.result_file).resolve()
    with apply_transaction(
        project_root,
        command="agent-task result-validate",
        chapter_number=int(manifest.get("chapter_number") or 0) or None,
        source_paths=[output_path],
        touched_paths=[agent_task_dir],
        metadata={
            "task_id": task_id,
            "task_type": str(manifest.get("task_type") or ""),
            "canonical_mutated": False,
            "lifecycle_target": lifecycle_status,
        },
    ):
        normalized = write_agent_result_diagnostic(project_root, normalization)
        update_task_status(
            project_root,
            task_id,
            to_status=lifecycle_status,
            command="agent-task result-validate",
            artifact=normalization.result_file,
            result=normalized.diagnostic_file,
            current_result={
                "ok": validation.ok,
                "path": normalization.result_file,
                "sha256": normalization.result_sha256,
                "diagnostic_file": normalized.diagnostic_file,
                "source_schema": normalization.source_schema,
                "validated_at": normalized.normalized_result.get("validated_at") or utc_now(),
            },
        )

    next_command = (
        str(manifest.get("validate_command") or "")
        if validation.ok
        else str(manifest.get("failure_next_command") or "")
    )
    return ProductionAgentResult(
        schema=PIPELINE_SCHEMA,
        ok=validation.ok,
        status=validation.status,
        task_id=task_id,
        task_type=str(manifest.get("task_type") or ""),
        lifecycle_status=lifecycle_status,
        diagnostic_file=normalized.diagnostic_file,
        normalization=normalized,
        next_command=next_command,
    )


def production_package_payload(package: IsolatedAgentPackage) -> dict[str, Any]:
    """Return a JSON-safe work-order contract without embedding source file bodies."""

    return {
        "schema": PIPELINE_SCHEMA,
        "task_id": package.task_id,
        "task_type": package.task_type,
        "role_id": package.role_id,
        "role_version": package.role_version,
        "independence_mode": package.independence_mode,
        "role_prompt_hash": package.role_prompt_hash,
        "project_overlay_hash": package.project_overlay_hash,
        "prompt_hash": package.prompt_hash,
        "context": package.context.as_dict(),
        "output_contract": asdict(package.output_contract),
        "result_template": package.result_template,
        "host": package.host_work_order.host,
        "work_order_markdown": package.host_work_order.markdown,
    }


def controlled_feedback(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Compile code-only review carryover; never forward prose, evidence spans, or commands."""

    chapter_number = int(manifest.get("chapter_number") or 0)
    task_type = str(manifest.get("task_type") or "")
    source_chapter = chapter_number - 1 if task_type == "chapter_write" else chapter_number
    if source_chapter <= 0:
        return {"schema": FEEDBACK_SCHEMA, "source_chapter": 0, "items": []}

    items: list[dict[str, Any]] = []
    for kind, template in FEEDBACK_FILES:
        path = root / template.format(chapter=source_chapter)
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        codes = feedback_codes(payload)
        counts = payload.get("severity_counts") if isinstance(payload.get("severity_counts"), dict) else {}
        status = safe_code(payload.get("status") or payload.get("verdict") or payload.get("severity") or "recorded")
        item = {
            "kind": kind,
            "source_chapter": source_chapter,
            "source_sha256": sha256(path.read_bytes()).hexdigest(),
            "status": status,
            "passed": payload.get("passed") if isinstance(payload.get("passed"), bool) else None,
            "need_human": payload.get("need_human") is True,
            "severity_counts": {
                key: int(counts.get(key) or 0) for key in ("P0", "P1", "P2", "P3")
            },
            "codes": codes[:8],
            "summary": f"{kind} status={status}; codes={','.join(codes[:8]) or 'none'}",
            "authority": "advisory_only",
        }
        items.append(item)
    return {"schema": FEEDBACK_SCHEMA, "source_chapter": source_chapter, "items": items[:5]}


def feedback_codes(payload: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    for key in ("failures", "warnings", "issues", "findings", "unresolved_items", "need_human_reasons"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        for value in values:
            raw = (
                value.get("code") or value.get("id") or value.get("kind") or value.get("severity")
                if isinstance(value, dict)
                else value
            )
            code = safe_code(raw)
            if code and code not in codes:
                codes.append(code)
    return codes


def safe_code(value: Any) -> str:
    text = SAFE_CODE_PATTERN.sub("_", str(value or "").strip()).strip("_")[:80]
    return text or "unspecified"
