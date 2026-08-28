"""Non-canonical creative sandbox and explicit promotion boundary."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.semantic_protocols import (
    SEMANTIC_DOCUMENT_SCHEMA,
    build_human_decision,
    build_semantic_document,
    build_workflow_record,
    canonical_json_hash,
    seal_semantic_document,
    validate_semantic_document,
)
from longform_engine.storage import atomic_write_text, resolve_project_root


class CreativeSandboxError(ValueError):
    """Raised when sandbox content attempts to bypass formal promotion."""


def create_sandbox_artifact(
    config: ConfigDocument,
    *,
    document_type: str,
    title: str,
    body: str,
    created_by: str = "human",
) -> dict[str, Any]:
    if created_by not in {"human", "host_agent"}:
        raise CreativeSandboxError("sandbox creator must be human or host_agent")
    root = resolve_project_root(config)
    digest = sha256(
        f"{document_type}\0{title}\0{body}".encode("utf-8")
    ).hexdigest()
    document = build_semantic_document(
        document_id=f"sandbox_{digest[:24]}",
        document_type=document_type,
        title=title,
        scope={"kind": "creative_sandbox", "project": root.name},
        continuity="创作沙盒（非 Canon）",
        body=body,
        extensions={
            "sandbox": True,
            "canonical": False,
            "materialize_graph": False,
            "materialize_rag": False,
        },
        created_by=created_by,
    )
    path = root / "50_workbench" / "创作沙盒" / f"{document['artifact']['artifact_id']}.json"
    atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return {
        "schema": SEMANTIC_DOCUMENT_SCHEMA,
        "artifact_id": document["artifact"]["artifact_id"],
        "file": path.relative_to(root).as_posix(),
        "canonical": False,
        "next_command": (
            "longform-engine sandbox promote project.yaml --sandbox "
            f"{path.relative_to(root).as_posix()} --candidate CANDIDATE.json --approved-by human"
        ),
    }


def promote_sandbox_candidate(
    config: ConfigDocument,
    *,
    sandbox_path: str | Path,
    candidate_path: str | Path,
    approved_by: str,
    reason: str,
) -> dict[str, Any]:
    """Promote selected sandbox meaning to a formal *candidate*, never directly to Canon."""

    if approved_by != "human":
        raise CreativeSandboxError("sandbox promotion requires --approved-by human")
    if not reason.strip():
        raise CreativeSandboxError("sandbox promotion requires a human reason")
    root = resolve_project_root(config)
    sandbox_file = _project_file(root, sandbox_path)
    candidate_file = _project_file(root, candidate_path)
    sandbox = _read_json_object(sandbox_file, "sandbox artifact")
    candidate = _read_json_object(candidate_file, "promotion candidate")
    sandbox_errors = validate_semantic_document(sandbox)
    candidate_errors = validate_semantic_document(candidate)
    if sandbox_errors or sandbox.get("extensions", {}).get("sandbox") is not True:
        raise CreativeSandboxError("invalid sandbox artifact: " + "; ".join(sandbox_errors))
    if candidate_errors:
        raise CreativeSandboxError("invalid semantic promotion candidate: " + "; ".join(candidate_errors))
    sandbox_hash = sha256(sandbox_file.read_bytes()).hexdigest()
    candidate = json.loads(json.dumps(candidate, ensure_ascii=False))
    extensions = candidate.get("extensions")
    if not isinstance(extensions, dict):
        raise CreativeSandboxError("promotion candidate extensions must be an object")
    if extensions.get("sandbox") is True:
        raise CreativeSandboxError("promoted candidate must leave sandbox scope")
    extensions.update(
        {
            "source_sandbox_id": sandbox["artifact"]["artifact_id"],
            "source_sandbox_sha256": sandbox_hash,
            "promotion_status": "awaiting_formal_canon_or_planning_approval",
        }
    )
    candidate["artifact"]["scope"] = {
        "kind": "project_semantic_candidate",
        "project": root.name,
    }
    candidate["artifact"]["state"] = "candidate"
    candidate = seal_semantic_document(candidate)
    decision = build_human_decision(
        decision_id="decision_" + canonical_json_hash(
            {
                "sandbox": sandbox_hash,
                "candidate": candidate["artifact"]["content_sha256"],
                "reason": reason,
            }
        )[:24],
        target_id=str(sandbox["artifact"]["artifact_id"]),
        target_sha256=str(sandbox["artifact"]["content_sha256"]),
        decision="approve",
        decided_by="human",
        reason=reason.strip(),
        scope={"kind": "sandbox_promotion", "project": root.name},
    )
    workflow = build_workflow_record(
        workflow_id="sandbox_promotion_" + candidate["artifact"]["artifact_id"][-20:],
        workflow_kind="sandbox_promotion",
        scope={"kind": "project", "project": root.name},
        state="completed",
        inputs=[
            {"artifact_id": sandbox["artifact"]["artifact_id"], "sha256": sandbox_hash}
        ],
        outputs=[
            {
                "artifact_id": candidate["artifact"]["artifact_id"],
                "sha256": candidate["artifact"]["content_sha256"],
            }
        ],
        authorization=decision,
        extensions={"canonical_mutated": False},
    )
    token = _safe_token(str(candidate["artifact"]["artifact_id"]))
    target = root / "50_workbench" / "语义候选" / f"{token}.json"
    workflow_file = root / "50_workbench" / "语义候选" / f"{token}.promotion.json"
    atomic_write_text(target, json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")
    atomic_write_text(workflow_file, json.dumps(workflow, ensure_ascii=False, indent=2) + "\n")
    return {
        "schema": "workflow_record_v1",
        "workflow_id": workflow["workflow_id"],
        "candidate_file": target.relative_to(root).as_posix(),
        "workflow_file": workflow_file.relative_to(root).as_posix(),
        "canonical_mutated": False,
        "next_step": "对该候选执行独立语义复核，再进入对应 Canon 或规划人工审批。",
    }


def _project_file(root: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise CreativeSandboxError("sandbox paths must stay inside the project") from exc
    if not path.is_file():
        raise CreativeSandboxError(f"sandbox file does not exist: {path}")
    return path


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CreativeSandboxError(f"{label} must be UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise CreativeSandboxError(f"{label} must be an object")
    return payload


def _safe_token(value: str) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return token[:120] or "semantic_candidate"


__all__ = [
    "CreativeSandboxError",
    "create_sandbox_artifact",
    "promote_sandbox_candidate",
]
