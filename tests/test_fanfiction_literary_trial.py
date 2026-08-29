import json
from hashlib import sha256
from pathlib import Path

from longform_engine.blind_review import literary_evidence_status
from longform_engine.config import ConfigDocument
from longform_engine.fanfiction_literary_trial import (
    SCORE_METRICS,
    aggregate_fanfiction_literary_trial,
    create_fanfiction_literary_review_template,
    create_fanfiction_literary_trial,
    resolve_fanfiction_literary_disagreements,
    submit_fanfiction_literary_review,
)


def test_abstract_source_adapter_fixtures_cover_distinct_rule_shapes():
    fixture_file = Path(__file__).parent / "fixtures" / "fanfiction_source_adapter_archetypes.json"
    payload = json.loads(fixture_file.read_text(encoding="utf-8"))

    assert payload["schema"] == "fanfiction_source_adapter_archetype_fixture_v1"
    assert len(payload["fixtures"]) == 8
    assert len({item["fixture_id"] for item in payload["fixtures"]}) == 8
    assert all(len(item["dimensions"]) >= 5 for item in payload["fixtures"])
    all_dimensions = {dimension for item in payload["fixtures"] for dimension in item["dimensions"]}
    assert {
        "activation_condition",
        "irreversible_death",
        "system_boundary",
        "knowledge_decay",
        "travel_time",
        "soul",
        "long_timescale",
        "backlash",
    } <= all_dimensions


def test_dual_route_twenty_chapter_blind_review_requires_human_disagreement_resolution(tmp_path):
    root = tmp_path / "project"
    config = ConfigDocument(
        data={
            "creation": {"mode": "fanfiction"},
            "project": {"root_dir": str(root)},
        },
        path=root / "project.yaml",
        sources=(),
    )
    route_sources: dict[str, Path] = {}
    gate_reports: dict[str, Path] = {}
    for route_family in ("oc_si_progression", "canon_character_centered"):
        source_dir = tmp_path / route_family
        source_dir.mkdir()
        chapter_hashes = []
        for chapter_number in range(1, 21):
            body = f"# 第{chapter_number}章\n\n{route_family} 的抽象文学验收文本 {chapter_number}。\n"
            chapter = source_dir / f"ch{chapter_number:03d}.md"
            chapter.write_text(body, encoding="utf-8")
            chapter_hashes.append(
                {
                    "chapter_number": chapter_number,
                    "sha256": sha256(body.encode("utf-8")).hexdigest(),
                }
            )
        gate_report = tmp_path / f"{route_family}.gate.json"
        gate_report.write_text(
            json.dumps(
                {
                    "schema": "fanfiction_literary_trial_gate_report_v1",
                    "route_family": route_family,
                    "chapter_count": 20,
                    "chapter_hashes": chapter_hashes,
                    "p1_blockers": [],
                    "untraceable_canon_assertions": [],
                    "continuous_source_reproduction_findings": [],
                    "fabricated_authorization_findings": [],
                    "generated_at": "2026-08-29T00:00:00+00:00",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        route_sources[route_family] = source_dir
        gate_reports[route_family] = gate_report

    trial = create_fanfiction_literary_trial(
        config,
        trial_id="dual-route-20",
        oc_si_source_dir=route_sources["oc_si_progression"],
        oc_si_gate_report=gate_reports["oc_si_progression"],
        canon_character_source_dir=route_sources["canon_character_centered"],
        canon_character_gate_report=gate_reports["canon_character_centered"],
        seed="private-seed",
    )
    assert len(trial.blind_ids) == 2

    reviewer_scores = (5.0, 3.0, 4.0)
    for index, score in enumerate(reviewer_scores, start=1):
        reviewer_id = f"reviewer-{index}"
        template_path = root / create_fanfiction_literary_review_template(
            config, trial_id=trial.trial_id, reviewer_id=reviewer_id
        )
        review = json.loads(template_path.read_text(encoding="utf-8"))
        review["reviewer"]["instance_id"] = f"human-instance-{index}"
        review["attestation_note"] = "本人未参与生成，只阅读匿名公开包并独立评分。"
        review["submitted_at"] = f"2026-08-29T0{index}:00:00+00:00"
        for entry in review["entries"]:
            entry["scores"] = {
                metric: score if metric in {
                    "canon_fidelity",
                    "character_agency",
                    "original_mainline_ownership",
                    "continued_reading_desire",
                } else 4.0
                for metric in SCORE_METRICS
            }
        review_file = tmp_path / f"{reviewer_id}.json"
        review_file.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")
        submit_fanfiction_literary_review(
            config,
            trial_id=trial.trial_id,
            reviewer_id=reviewer_id,
            file_path=review_file,
        )

    aggregate = aggregate_fanfiction_literary_trial(config, trial_id=trial.trial_id)
    assert aggregate.threshold_conclusion == "pass"
    assert aggregate.material_disagreement_count == 8
    assert aggregate.literary_evidence_ready is False
    assert literary_evidence_status(root)[0] is False

    aggregate_payload = json.loads((root / aggregate.aggregate_file).read_text(encoding="utf-8"))
    resolutions = [
        {
            "disagreement_id": disagreement_id,
            "decision": "acknowledge_panel_median_without_score_override",
            "note": "记录三名评审的分歧并保留中位数，不选择更有利的个别分数。",
        }
        for disagreement_id in aggregate_payload["material_disagreement_ids"]
    ]
    resolve_fanfiction_literary_disagreements(
        config,
        trial_id=trial.trial_id,
        decided_by="literary-panel-chair",
        resolutions=resolutions,
    )

    ready, blockers = literary_evidence_status(root)
    assert ready is True
    assert blockers == []
