"""Read-only evidence audit of the five-chapter, explicitly simulated rehearsal.

Uses the normal literary sample validator; never produces missing production
evidence, approvals, prose, scores, or chapter state. Reports belong outside Git.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from longform_engine import __version__
from longform_engine.config import load_project_config
from longform_engine.execution_origin import execution_origin
from longform_engine.fanfiction_literary_trial import collect_literary_sample
from longform_engine.production import production_next
from longform_engine.quality.status import quality_status
from longform_engine.storage import atomic_write_text, resolve_project_root


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[1]
    project = args.project.resolve()
    report_path = args.report.resolve()
    if project.is_relative_to(repository) or report_path.is_relative_to(repository):
        parser.error("Novel and full rehearsal evidence must stay outside the repository")
    config = load_project_config(project)
    root = resolve_project_root(config)
    origin = execution_origin(root)
    if origin["kind"] != "automated_rehearsal" or not origin["simulated_human"] or origin.get("fixture"):
        parser.error("Audit only a marked automatic rehearsal with actual model prose")
    if report_path.is_relative_to(root):
        parser.error("Save audit reports in the external evidence directory, not inside the novel")

    failures = []
    sample = None
    try:
        sample = collect_literary_sample(config, 1, 5, rehearsal=True)
    except (ValueError, OSError, KeyError) as exc:
        failures.append(f"chapter_loop_evidence: {exc}")
    chapters = []
    for number in range(1, 6):
        path = root / f"40_manuscript/final/ch{number:03d}.md"
        if not path.is_file():
            failures.append(f"chapter_{number}: final missing")
            continue
        body = path.read_bytes()
        text = body.decode("utf-8")
        chinese_count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]", text))
        chapters.append({"chapter_number": number, "sha256": sha256(body).hexdigest(),
                         "character_count": len(text), "chinese_character_count": chinese_count})
        if not 2500 <= chinese_count <= 3500:
            failures.append(f"chapter_{number}: Chinese character count {chinese_count} outside 2500–3500")
    if len({chapter["sha256"] for chapter in chapters}) != len(chapters):
        failures.append("duplicate_final_chapter_hashes")
    if (root / "40_manuscript/final/ch006.md").exists():
        failures.append("unauthorized_sixth_final")

    jobs = []
    for path in (root / "70_runtime/agent_jobs").glob("job_*/job.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        # No session identifiers, prompts, credentials, stdout or tool logs.
        jobs.append({key: record.get(key) for key in (
            "job_id", "task_id", "task_type", "status", "created_at", "started_at",
            "finished_at", "result_sha256", "exit_code",
        )})
    jobs.sort(key=lambda row: row["created_at"] or "")
    for number in range(1, 6):
        token = f":ch{number:03d}:"
        for task_type in ("chapter_write", "prose_revision_semantic_review"):
            if not any(job["task_type"] == task_type and token in (job["task_id"] or "")
                       and job["status"] == "completed" and job["result_sha256"] for job in jobs):
                failures.append(f"chapter_{number}: completed actual {task_type} job missing")
    next_action = production_next(config)
    if next_action.get("chapter_number") != 6:
        failures.append("not_at_chapter_six_preparation")
    quality = quality_status(config)
    if quality.get("author_acceptance_ready"):
        failures.append("simulated_human_incorrectly_counted_as_real_acceptance")
    try:
        collect_literary_sample(config, 1, 5)
    except ValueError as exc:
        if "automated_rehearsal_ineligible" not in str(exc):
            failures.append(f"formal_sample_refusal: {exc}")
    else:
        failures.append("simulated_sample_incorrectly_eligible_for_formal_acceptance")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
                            capture_output=True, text=True, check=True).stdout.strip()
    result = {
        "schema": "web_studio_rehearsal_audit_v1", "run_id": origin["run_id"],
        "created_at": datetime.now(timezone.utc).isoformat(), "engine_version": __version__,
        "commit": commit, "project_path": str(project), "execution_origin": origin,
        "actual_model": None, "human_review_minutes": None, "human_edit_minutes": None,
        "evidence_current": not failures, "failures": failures, "chapters": chapters,
        "sample": sample, "jobs": jobs, "next_action": next_action,
        "quality": quality, "formal_literary_eligible": False,
        "literary_quality_status": "unverified", "platform_acceptance_status": "unverified",
        "limits": ["模拟人工记录仅验证工程流程", "没有三名独立人类的文学评分",
                   "本报告不替代 Playwright 点击证据或逐章文本问题报告"],
    }
    atomic_write_text(report_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"report": str(report_path), "evidence_current": not failures,
                      "final_count": len(chapters), "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
