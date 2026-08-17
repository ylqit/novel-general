#!/usr/bin/env python
"""Fail unless the installed Agent-first structural contract is current."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from longform_engine.agent_protocol_readiness import (  # noqa: E402
    check_agent_data_pipeline_readiness,
    render_agent_data_pipeline_readiness,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = check_agent_data_pipeline_readiness(args.repository)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_agent_data_pipeline_readiness(report))
    return 0 if report["ready_for_data_pipeline"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
