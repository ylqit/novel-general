from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

from longform_engine.agent_tasks import (
    TASK_CONTRACTS,
    normalize_manifest,
    validate_manifest_shape,
    validate_manifest_strict,
)
from longform_engine import production
from longform_engine.production import TASK_OUTPUT_GUIDANCE, TASK_WORK_SCOPES
from longform_engine.quality.review import payoff_output_template
from longform_engine.semantic.pipeline import semantic_output_template


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_FILE = REPO_ROOT / "docs" / "baselines" / "AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE0_V031.json"
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "agent_document_protocol_v031"
V031_SCHEMA_SNAPSHOTS = {
    "book_design": ["book_design_candidate_v1", "book_design_candidate_v2"],
    "outline_design": ["outline_design_candidate_v1"],
    "chapter_direction": ["chapter_direction_candidate_v1"],
    "pacing_review": ["semantic_pacing_result_v1"],
}


def test_phase0_inventory_covers_every_v031_task_contract_and_output_schema():
    baseline = read_json(BASELINE_FILE)
    inventory = {item["task_type"]: item for item in baseline["task_inventory"]}

    legacy_task_types = set(TASK_CONTRACTS) - {"outline_extension"}
    assert set(inventory) == legacy_task_types
    for task_type in sorted(legacy_task_types):
        contract = TASK_CONTRACTS[task_type]
        assert inventory[task_type]["scopes"] == list(contract["scope_kinds"])
        expected_schemas = V031_SCHEMA_SNAPSHOTS.get(task_type, list(contract["schemas"]))
        assert inventory[task_type]["schemas"] == expected_schemas
        assert inventory[task_type]["validator"]
        assert inventory[task_type]["apply"]
        assert inventory[task_type]["canonical_owner"]

    classified_schemas = {
        schema
        for group in baseline["field_ownership"]["schema_groups"]
        for schema in group["schemas"]
    }
    contract_schemas = set()
    for task_type in sorted(legacy_task_types):
        contract = TASK_CONTRACTS[task_type]
        contract_schemas.update(V031_SCHEMA_SNAPSHOTS.get(task_type, contract["schemas"]))
    assert classified_schemas == contract_schemas

    assert list(TASK_CONTRACTS["pacing_review"]["schemas"]) == ["semantic_pacing_result_v2"]


def test_phase0_legacy_role_mapping_is_frozen_until_role_registry_exists():
    baseline = read_json(BASELINE_FILE)
    registry = REPO_ROOT / "config" / "agent_roles" / "registry.json"
    if registry.exists():
        return
    legacy_role_briefs = getattr(production, "TASK_ROLE_BRIEFS", {})

    snapshot = {
        task_type: {
            "contract": TASK_CONTRACTS[task_type],
            "role_brief": legacy_role_briefs.get(task_type, ""),
            "work_scope": TASK_WORK_SCOPES.get(task_type, ""),
            "output_guidance": TASK_OUTPUT_GUIDANCE.get(task_type, ""),
        }
        for task_type in sorted(TASK_CONTRACTS)
    }
    encoded = json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(snapshot) == baseline["legacy_role_mapping"]["task_count"]
    assert sha256(encoded).hexdigest() == baseline["legacy_role_mapping"]["mapping_sha256"]


def test_frozen_manifest_v1_and_v2_remain_readable_and_strict(tmp_path):
    task_file = tmp_path / "50_workbench" / "writing_tasks" / "ch001.md"
    task_file.parent.mkdir(parents=True)
    task_file.write_text("# Synthetic writing task\n", encoding="utf-8")

    v1 = normalize_manifest(read_json(FIXTURE_ROOT / "manifest_v1.json"))
    v2 = normalize_manifest(read_json(FIXTURE_ROOT / "manifest_v2.json"))
    validate_manifest_shape(v1)
    validate_manifest_shape(v2)

    assert v1["schema_version"] == 2
    assert v1["source_schema_version"] == 1
    assert v1["scope"] == {"kind": "chapter", "chapter_number": 1}
    assert v2["schema_version"] == 2
    assert "source_schema_version" not in v2
    assert validate_manifest_strict(tmp_path, v1).ok
    assert validate_manifest_strict(tmp_path, v2).ok


def test_frozen_submission_v2_adds_candidate_identity_without_losing_v1_fields():
    v1 = read_json(FIXTURE_ROOT / "submission_v1.json")
    v2 = read_json(FIXTURE_ROOT / "submission_v2.json")
    candidate_fields = {
        "candidate_task_id",
        "candidate_task_type",
        "candidate_revision",
        "candidate_source_path",
        "candidate_source_hash",
        "candidate_status",
        "replaces_task_ids",
    }

    assert set(v1) == set(v2) - candidate_fields
    assert v1["schema_version"] == 1
    assert v2["schema_version"] == 2
    assert v2["candidate_source_path"] == v2["source_file"]
    assert v2["candidate_task_id"] == "repair:ch001:v1"


def test_frozen_review_and_semantic_bundle_match_v031_template_shapes(tmp_path):
    text = "# Chapter 1\n\nAri chooses the north gate.\n"
    draft = tmp_path / "40_manuscript" / "draft" / "ch001.md"
    final = tmp_path / "40_manuscript" / "final" / "ch001.md"
    draft.parent.mkdir(parents=True)
    final.parent.mkdir(parents=True)
    draft.write_text(text, encoding="utf-8")
    final.write_text(text, encoding="utf-8")
    card = {
        "chapter_duty": "Force a costly route decision.",
        "reader_gain": "Reveal which route remains open.",
        "cost": "Ari loses the safer option.",
        "promise_refs": [],
        "topology_id": "costly_choice",
    }

    payoff_fixture = read_json(FIXTURE_ROOT / "reader_payoff_review_v1.json")
    payoff_template = payoff_output_template(tmp_path, 1, draft, text, card)
    assert set(payoff_fixture) == set(payoff_template)
    assert set(payoff_fixture["planned"]) == set(payoff_template["planned"])
    assert set(payoff_fixture["observed"]) == set(payoff_template["observed"])
    assert set(payoff_fixture["craft_observation"]) == set(payoff_template["craft_observation"])
    assert payoff_fixture["source_hash"] == sha256(text.encode("utf-8")).hexdigest()

    semantic_fixture = read_json(FIXTURE_ROOT / "chapter_semantic_bundle_v1.json")
    semantic_template = semantic_output_template(tmp_path, final, 1)
    assert set(semantic_fixture) == set(semantic_template)
    assert set(semantic_fixture["chapter_digest"]) == set(semantic_template["chapter_digest"])
    assert set(semantic_fixture["retrieval"]) == set(semantic_template["retrieval"])
    assert set(semantic_fixture["coverage"]) == set(semantic_template["coverage"])
    assert semantic_fixture["source"]["sha256"] == sha256(text.encode("utf-8")).hexdigest()


def test_phase0_context_measurements_preserve_the_known_budget_failures():
    baseline = read_json(BASELINE_FILE)
    rows = {item["task_type"]: item for item in baseline["context_measurements"]}

    assert rows["reader_payoff_review"]["total_chars"] > rows["reader_payoff_review"]["max_chars"]
    assert rows["editorial_review"]["total_chars"] > rows["editorial_review"]["max_chars"]
    assert rows["semantic_review"]["total_chars"] <= rows["semantic_review"]["max_chars"]
    assert rows["chapter_semantic"]["total_chars"] <= rows["chapter_semantic"]["max_chars"]
    assert rows["reader_payoff_review"]["repeated_long_line_ratio"] >= 0.20
    assert baseline["data_pipeline_locked"] is True


def test_local_sao_failure_evidence_and_canonical_snapshot_are_unchanged_when_present():
    baseline = read_json(BASELINE_FILE)
    evidence = baseline["failure_evidence"]
    project = REPO_ROOT / evidence["project"]
    if not project.exists():
        return

    for key in ("validation_report", "issue_log"):
        record = evidence[key]
        path = project / record["path"]
        assert path.stat().st_size == record["bytes"]
        assert sha256(path.read_bytes()).hexdigest() == record["sha256"]

    snapshot = evidence["protected_snapshot"]
    records: list[tuple[str, str, int]] = []
    for prefix in snapshot["prefixes"]:
        path = project / prefix
        if path.is_file():
            paths = [path]
        elif path.exists():
            paths = sorted(item for item in path.rglob("*") if item.is_file())
        else:
            paths = []
        for item in paths:
            payload = item.read_bytes()
            records.append(
                (
                    item.relative_to(project).as_posix(),
                    sha256(payload).hexdigest(),
                    len(payload),
                )
            )
    encoded = "\n".join(f"{path}\t{digest}\t{size}" for path, digest, size in records).encode("utf-8")

    assert len(records) == snapshot["file_count"]
    assert sum(size for _, _, size in records) == snapshot["total_bytes"]
    assert sha256(encoded).hexdigest() == snapshot["aggregate_sha256"]


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
