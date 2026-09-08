import json
from pathlib import Path

from longform_engine.agent_pipeline import validate_production_agent_result
from longform_engine.agent_protocols import EVIDENCE_REVIEW_SCHEMA
from longform_engine.agent_tasks import load_manifest, validate_manifest_strict
from longform_engine.config import load_project_config
from longform_engine.gates import gate_check, semantic_review_apply, semantic_review_validate
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.production import production_next
from longform_engine.storage import init_project
from tests.project_fixtures import checked_review_coverage, mark_project_ready


def seed_high_risk_chapter(tmp_path: Path, *, extra_rule: str = ""):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = tmp_path / "novel"
    open_book(config)
    mark_project_ready(root, config)
    if extra_rule:
        (root / "10_bible/power_system.md").write_text(extra_rule, encoding="utf-8")
        obligations_path = root / "30_state/semantic_obligations.json"
        obligations = json.loads(obligations_path.read_text(encoding="utf-8"))
        for obligation in obligations["items"]:
            obligation.setdefault("prior_state_refs", []).append("10_bible/power_system.md")
        obligations_path.write_text(json.dumps(obligations), encoding="utf-8")
        from tests.project_fixtures import refresh_planning_fixture_basis
        refresh_planning_fixture_basis(root)
    config.data["length"]["chapter"]["hard_min"] = 20
    config.data["quality"]["semantic_review_milestones"] = [1]
    continue_write(config, chapter_number=1)
    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(
        "# Chapter 1\n\n"
        + ("Ari checks the archive seal and records one bounded clue. " * 20)
        + "But the final seal names a second archive. Who opens it at midnight?",
        encoding="utf-8",
    )
    submit_agent_draft(config, chapter_number=1, file_path=agent_draft, agent="codex")
    chapter = root / "40_manuscript" / "draft" / "ch001.md"
    return config, root, chapter


def test_high_risk_gate_creates_strict_semantic_review_task(tmp_path):
    config, root, _ = seed_high_risk_chapter(tmp_path)

    result = gate_check(config, chapter_number=1, semantic=True)
    manifest = load_manifest(root, "semantic_review:ch001:v5")
    strict = validate_manifest_strict(root, manifest)

    assert result.passed
    assert not any(item["code"] == "semantic_review_required" for item in result.failures)
    assert json.loads(Path(result.gate_result).read_text(encoding="utf-8"))["workflow_stage"] == "reviews_pending"
    assert strict.ok, strict.errors
    assert manifest["io"]["output"]["protocol"] == EVIDENCE_REVIEW_SCHEMA
    assert len(manifest["io"]["inputs"]) <= 7
    assert manifest["policy"]["context"]["budget_profile"] == "standard"
    assert manifest["policy"]["context"]["capacity_units"] == 48_000


def test_semantic_review_validates_spans_and_applies_only_gate_artifacts(tmp_path):
    config, root, chapter = seed_high_risk_chapter(tmp_path)
    gate_check(config, chapter_number=1, semantic=True)
    output = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_result.json"
    output.write_text(
        json.dumps(
            {
                "schema": EVIDENCE_REVIEW_SCHEMA,
                "verdict": "pass",
                "coverage": checked_review_coverage(
                    root,
                    chapter,
                    ("canonical_fact", "motivation", "space_time_ability"),
                    canonical_dimensions=("canonical_fact", "motivation", "space_time_ability"),
                ),
                "findings": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    protected = snapshot_protected(root)

    manifest = load_manifest(root, "semantic_review:ch001:v5")
    control = validate_production_agent_result(root, manifest, result_file=output)
    validation = semantic_review_validate(config, chapter_number=1, file_path=output)
    next_action = production_next(config)
    assert next_action["task_type"] == "semantic_review"
    assert next_action["status"] == "agent_task_validated"
    assert next_action["next_command"].startswith("longform-engine gate semantic-apply")
    applied = semantic_review_apply(config, chapter_number=1, file_path=output)
    gate = json.loads(Path(applied.gate_result).read_text(encoding="utf-8"))

    assert control.ok, control.normalization.errors
    assert validation.ok, validation.errors
    assert gate["agent_semantic_review"]["status"] == "applied"
    assert not any(item["code"] == "semantic_review_required" for item in gate["failures"])
    assert snapshot_protected(root) == protected
    application_before = Path(applied.application_file).read_bytes()
    repeated = semantic_review_apply(config, chapter_number=1, file_path=output)
    assert repeated.applied
    assert Path(applied.application_file).read_bytes() == application_before
    assert snapshot_protected(root) == protected


def test_semantic_review_contains_exact_required_rule_and_expires_on_source_change(tmp_path):
    from longform_engine.gates.pipeline import gate_review_context_is_current

    rule = "# 能力边界\n必须接触实物才能感知；污染可能误导判断。\n" + "每次过用的伤势均须延续。" * 40
    config, root, _chapter = seed_high_risk_chapter(tmp_path, extra_rule=rule)
    gate_check(config, chapter_number=1, semantic=True)
    artifact = root / "50_workbench/gate_artifacts/ch001"
    context = json.loads((artifact / "semantic_review_context.json").read_text(encoding="utf-8"))
    excerpts = context["sections"]["canonical_source_excerpts"]
    selected = [item for item in excerpts if item["source"] == "10_bible/power_system.md"]
    assert len(selected) == 1
    assert selected[0]["value"] == (root / "10_bible/power_system.md").read_bytes().decode("utf-8")
    assert "10_bible/power_system.md" in context["allowed_canonical_refs"]
    assert gate_review_context_is_current(root, 1, task_type="semantic_review")
    (root / "10_bible/power_system.md").write_text(rule + "\n后续批准改变规则。", encoding="utf-8")
    assert not gate_review_context_is_current(root, 1, task_type="semantic_review")


def test_semantic_review_rejects_fabricated_span_without_pollution(tmp_path):
    config, root, chapter = seed_high_risk_chapter(tmp_path)
    gate_check(config, chapter_number=1, semantic=True)
    output = root / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_review_result.json"
    output.write_text(
        json.dumps(
            {
                "schema": EVIDENCE_REVIEW_SCHEMA,
                "verdict": "repair",
                "coverage": checked_review_coverage(
                    root,
                    chapter,
                    ("canonical_fact", "motivation", "space_time_ability"),
                    canonical_dimensions=("canonical_fact", "motivation", "space_time_ability"),
                ),
                "findings": [
                    {
                        "code": "CANONICAL_CONFLICT",
                        "severity": "P1",
                        "certainty": "confirmed",
                        "diagnosis": "Unsupported claim.",
                        "evidence_ids": ["ch001.md@0:99999"],
                        "reader_impact": "The transition contradicts the established fact.",
                        "repair_target": "Repair the causal bridge.",
                        "preserve": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    protected = snapshot_protected(root, include_db=True)

    manifest = load_manifest(root, "semantic_review:ch001:v5")
    control = validate_production_agent_result(root, manifest, result_file=output)
    validation = semantic_review_validate(config, chapter_number=1, file_path=output)

    assert not control.ok
    assert any("outside current source bounds" in error for error in control.normalization.errors)
    assert not validation.ok
    assert any("control-plane status" in error for error in validation.errors)
    assert snapshot_protected(root, include_db=True) == protected
    from longform_engine.gates import semantic_review_task
    from hashlib import sha256
    old_output = output.read_bytes()
    old_manifest = (root / manifest["manifest_file"]).read_bytes()
    replacement = semantic_review_task(config, chapter_number=1)
    new_task = load_manifest(root, replacement.manifest_file)
    assert new_task["task_id"] != manifest["task_id"]
    assert new_task["status"] == "awaiting_agent"
    assert not output.exists()  # A failed old answer cannot be consumed by the replacement.
    old_task = load_manifest(root, manifest["task_id"])
    assert old_task["status"] == "superseded"
    assert (root / old_task["manifest_file"]).read_bytes() == old_manifest
    assert (root / "50_workbench/agent_tasks/results" / (sha256(old_output).hexdigest() + ".json")).read_bytes() == old_output
    assert snapshot_protected(root, include_db=True) == protected


def snapshot_protected(root: Path, *, include_db: bool = False) -> dict[str, bytes]:
    paths = [
        root / "40_manuscript" / "final",
        root / "60_rag",
        root / "30_state" / "story_graph.json",
        root / "30_state" / "tcs",
    ]
    if include_db:
        paths.append(root / "70_runtime" / "db")
    snapshot: dict[str, bytes] = {}
    for path in paths:
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = path.read_bytes()
        elif path.is_dir():
            for file in sorted(item for item in path.rglob("*") if item.is_file()):
                snapshot[file.relative_to(root).as_posix()] = file.read_bytes()
    return snapshot
