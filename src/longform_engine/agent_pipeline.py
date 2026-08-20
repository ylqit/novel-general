"""Authorized integration boundary for the Agent-first production protocol."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any

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
from longform_engine.agent_tasks import (
    list_manifests,
    manifest_chapter_number,
    manifest_commands,
    update_task_status,
    utc_now,
)
from longform_engine.resources import resource_root
from longform_engine.quality import editorial_patterns_for_task
from longform_engine.prompting import (
    PromptCompilation,
    refresh_prompt_compilation,
    strip_budget_report,
)
from longform_engine.storage import apply_transaction


PIPELINE_SCHEMA = "agent_first_production_pipeline_v1"
AUTHORIZATION_SCHEMA = "agent_first_pipeline_authorization_v2"
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
    """Require the same structural protocol contract in a checkout or installed wheel."""

    root = resource_root()
    report = require_agent_data_pipeline_readiness(root, requested=True)
    if report is None:
        raise AgentProductionPipelineError("Agent protocol readiness unexpectedly returned no report.")
    return {
        "schema": AUTHORIZATION_SCHEMA,
        "authorized": True,
        "source": "current_structural_contract",
        "protocol_surface_sha256": report["provenance"]["protocol_surface_sha256"],
        "execution_model": report["provenance"]["execution_model"],
    }

def compile_production_agent_package(
    root: Path,
    manifest: dict[str, Any],
    *,
    host: str,
) -> IsolatedAgentPackage:
    """Compile the production work order only after the shared readiness gate authorizes it."""

    require_agent_first_production_pipeline()
    role = manifest.get("role") if isinstance(manifest.get("role"), dict) else {}
    advisories = editorial_patterns_for_task(
        root.resolve(),
        task_type=str(manifest.get("task_type") or ""),
        role_id=str(role.get("id") or ""),
    )
    package = compile_isolated_agent_package(
        root.resolve(),
        manifest,
        host=host,
        review_advisories=advisories,
    )
    protocol_command = (
        f"longform-engine agent-task result-validate project.yaml {package.task_id} "
        f"--file {package.output_contract.output_path}"
    )
    budget = package.prompt.payload.get("budget") or {}
    if str(budget.get("status") or "") == "need_human":
        handoff = (
            "\n## 当前阻断与交接\n\n"
            "当前核心 Prompt 或必要证据无法安全装入所选容量档。本任务不得生成或提交候选结果。\n"
            f"下一命令：`{package.output_contract.failure_command}`\n"
        )
    else:
        handoff = (
            "\n## 协议验收顺序\n\n"
            f"1. 登记并校验角色输出：`{protocol_command}`\n"
            f"2. 执行领域校验：`{package.output_contract.validate_command}`\n"
            "3. 除非用户明确执行声明命令，否则停在 apply/finalize 之前。\n"
        )
    semantic_markdown = strip_budget_report(package.prompt.markdown) + "\n" + handoff
    refreshed_prompt = refresh_prompt_compilation(
        root.resolve(),
        manifest,
        markdown=semantic_markdown,
        payload=package.prompt.payload,
        input_units=package.context.total_estimated_units,
        context_batches=package.context.budget_report.get("context_batches") or [],
        blocking_reasons=package.context.budget_report.get("blocking_reasons") or [],
    )
    if not refreshed_prompt.payload.get("executable", True) and str(budget.get("status") or "") != "need_human":
        blocked_handoff = (
            "\n## 当前阻断与交接\n\n"
            "追加宿主交接后，核心 Prompt 已超过所选容量档。本任务不得生成或提交候选结果。\n"
            f"下一命令：`{package.output_contract.failure_command}`\n"
        )
        semantic_markdown = strip_budget_report(package.prompt.markdown) + "\n" + blocked_handoff
        refreshed_prompt = refresh_prompt_compilation(
            root.resolve(),
            manifest,
            markdown=semantic_markdown,
            payload=package.prompt.payload,
            input_units=package.context.total_estimated_units,
            context_batches=package.context.budget_report.get("context_batches") or [],
            blocking_reasons=[
                *(package.context.budget_report.get("blocking_reasons") or []),
                "host_handoff_exceeds_control_budget",
            ],
        )
    semantic_markdown = refreshed_prompt.markdown
    prompt_payload = refreshed_prompt.payload
    prompt_hash = sha256(semantic_markdown.encode("utf-8")).hexdigest()
    return replace(
        package,
        prompt=PromptCompilation(payload=prompt_payload, markdown=semantic_markdown),
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
        chapter_number=manifest_chapter_number(manifest) or None,
        source_paths=[output_path],
        touched_paths=[agent_task_dir],
        metadata={
            "task_id": task_id,
            "task_type": str(manifest.get("task_type") or ""),
            "canonical_mutated": False,
            "lifecycle_target": lifecycle_status,
        },
    ):
        normalized = (
            replace(normalization, diagnostic_file="")
            if validation.ok
            else write_agent_result_diagnostic(project_root, normalization)
        )
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

    commands = manifest_commands(manifest)
    next_command = str(commands.get("validate") if validation.ok else commands.get("failure") or "")
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
        "role_contract_hash": package.role_contract_hash,
        "project_overlay_hash": package.project_overlay_hash,
        "compiled_prompt_hash": package.prompt_hash,
        "context": package.context.as_dict(),
        "output_contract": asdict(package.output_contract),
        "result_template": package.result_template,
        "host": package.host_work_order.host,
        "work_order_markdown": package.host_work_order.markdown,
    }
