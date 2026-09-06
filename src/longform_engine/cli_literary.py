"""CLI ownership for deterministic local literary evaluation actions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from longform_engine.config import load_project_config
from longform_engine.storage import resolve_project_root
from longform_engine.fanfiction_literary_trial import (
    STAGES, aggregate_literary_trial, create_literary_trial, export_literary_pack,
    literary_reviewer_state, literary_trial_status, register_literary_reviewer,
    resolve_literary_issues, save_literary_review, record_literary_effort,
)


def register_literary_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("literary", help="Create anonymous trials and collect independent human scores.")
    commands = parser.add_subparsers(dest="literary_command", required=True)
    for name in ("create", "status", "reviewer-add", "review", "save", "submit", "import-review", "report", "resolve", "export-pack", "effort-record"):
        command = commands.add_parser(name)
        command.add_argument("config", nargs="?", default="project.yaml")
        command.add_argument("--json", action="store_true")
        command.set_defaults(func=run_literary_command, mutates_project=name in {"create", "reviewer-add", "save", "submit", "import-review", "resolve", "effort-record"})
        if name not in {"status", "effort-record"}:
            command.add_argument("--trial-id", required=True)
        if name == "effort-record":
            command.add_argument("--chapter-number", type=int, required=True)
            command.add_argument("--human-review-minutes", type=float)
            command.add_argument("--human-edit-minutes", type=float)
        if name == "create":
            command.add_argument("--stage", required=True, choices=list(STAGES))
            command.add_argument("--samples-file", required=True, help="JSON list of config_path, chapter_start, chapter_end.")
        if name in {"reviewer-add", "review", "save", "submit", "import-review"}:
            command.add_argument("--reviewer-id", required=True)
        if name in {"save", "submit", "import-review", "resolve"}:
            command.add_argument("--file", required=True)
        if name in {"save", "submit"}:
            command.add_argument("--expected-sha256", required=True)
        if name in {"export-pack", "report"}:
            command.add_argument("--output", required=name == "export-pack")


def run_literary_command(args: argparse.Namespace) -> int:
    config = load_project_config(Path(args.config).expanduser().resolve())
    root = resolve_project_root(config)
    action = args.literary_command
    if action == "effort-record":
        result = record_literary_effort(config, chapter_number=args.chapter_number,
            human_review_minutes=args.human_review_minutes, human_edit_minutes=args.human_edit_minutes)
    elif action == "create":
        result = create_literary_trial(config, trial_id=args.trial_id, stage=args.stage,
                                      samples=json.loads(Path(args.samples_file).read_text(encoding="utf-8")))
    elif action == "status":
        result = literary_trial_status(root)
    elif action == "reviewer-add":
        result = register_literary_reviewer(root, args.trial_id, args.reviewer_id)
    elif action == "review":
        result = literary_reviewer_state(root, args.trial_id, args.reviewer_id)
    elif action in {"save", "submit", "import-review"}:
        draft = json.loads(Path(args.file).read_text(encoding="utf-8"))
        expected = (literary_reviewer_state(root, args.trial_id, args.reviewer_id)["draft_sha256"]
                    if action == "import-review" else args.expected_sha256)
        result = save_literary_review(root, args.trial_id, args.reviewer_id, draft,
                                     expected_sha256=expected, submit=action != "save")
    elif action == "resolve":
        decision = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if set(decision) != {"submission_sha256", "decisions", "decided_by"}:
            raise ValueError("resolution file requires submission_sha256, decisions and decided_by")
        result = resolve_literary_issues(root, args.trial_id, **decision)
    elif action == "export-pack":
        path = Path(args.output).resolve()
        content = export_literary_pack(root, args.trial_id)
        with path.open("xb") as handle:
            handle.write(content)
        result = {"exported": str(path)}
    else:
        result = aggregate_literary_trial(root, args.trial_id)
        if args.output:
            with Path(args.output).open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0
