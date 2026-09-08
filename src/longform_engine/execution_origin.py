"""Persistent test provenance is disqualifying evidence, never a production bypass."""

from pathlib import Path
import json
from typing import Any


def execution_origin(root: Path) -> dict[str, Any]:
    path = root / "00_governance/execution_origin.json"
    if not path.exists():
        return {"kind": "author_workflow", "simulated_human": False, "run_id": None}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or record.get("schema") != "execution_origin_v1"
                or record.get("kind") != "automated_rehearsal" or record.get("simulated_human") is not True
                or not isinstance(record.get("run_id"), str) or not record["run_id"]):
            raise ValueError("invalid provenance")
        return record
    except (OSError, ValueError):
        return {"kind": "invalid_provenance", "simulated_human": True, "run_id": None}
