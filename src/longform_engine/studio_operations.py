"""Explicit publication and recovery actions sharing the existing CLI domains."""

from dataclasses import asdict
from hashlib import sha256
from typing import Any

from longform_engine.config import ConfigDocument
from longform_engine.execution_origin import execution_origin
from longform_engine.local_web import LocalWebError
from longform_engine.publication import publication_preflight, publication_preflight_status, export_publication_bundle
from longform_engine.storage import acquire_project_lock, resolve_project_root
from longform_engine.storage.recovery import (
    recovery_status, reclaim_project_lock, rollback_prepared_transaction,
    discard_preparing_transaction, cleanup_committed_transaction,
)


def studio_operation_state(config: ConfigDocument, section: str) -> dict[str, Any]:
    if section == "recovery":
        result = recovery_status(config)
        for row in result["transactions"]:
            row["id"] = "transaction_" + sha256(row["path"].encode()).hexdigest()[:24]
        return result
    if section == "publication":
        return {"origin": execution_origin(resolve_project_root(config)), "targets": {
            target: publication_preflight_status(config, target=target) for target in ("qidian_male", "fanqie_free")}}
    raise LocalWebError("工作页无效")


def execute_studio_operation(config: ConfigDocument, section: str, body: dict[str, Any]) -> dict[str, Any]:
    if section == "publication":
        if set(body) != {"action", "target"} or body["action"] not in {"preflight", "export"} or body["target"] not in {"qidian_male", "fanqie_free"}:
            raise LocalWebError("发布材料动作无效")
        with acquire_project_lock(config, owner="workspace-studio", command="publication " + body["action"]):
            if body["action"] == "preflight":
                _, result = publication_preflight(config, target=body["target"], write=True)
                return result
            return asdict(export_publication_bundle(config, target=body["target"]))
    if section != "recovery" or set(body) != {"action", "id", "expected_sha256", "acknowledge"} or body["acknowledge"] is not True:
        raise LocalWebError("恢复操作必须确认当前精确版本")
    if body["action"] == "reclaim_lock":
        if body["id"] != "project_lock":
            raise LocalWebError("恢复目标无效")
        return reclaim_project_lock(config, expected_sha256=body["expected_sha256"], approved_by="human")
    actions = {"rollback": ("recoverable_rollback", rollback_prepared_transaction),
               "discard": ("recoverable_discard", discard_preparing_transaction),
               "cleanup": ("recoverable_cleanup", cleanup_committed_transaction)}
    if body["action"] not in actions:
        raise LocalWebError("恢复动作无效")
    with acquire_project_lock(config, owner="workspace-studio", command="recovery " + body["action"]):
        status = studio_operation_state(config, "recovery")
        state, operation = actions[body["action"]]
        matches = [row for row in status["transactions"] if row["id"] == body["id"]]
        if len(matches) != 1 or matches[0]["state"] != state or matches[0]["sha256"] != body["expected_sha256"]:
            raise LocalWebError("恢复对象已变化或不满足恢复条件，请重新检查")
        return operation(config, report=matches[0]["path"], expected_sha256=body["expected_sha256"], approved_by="human")
