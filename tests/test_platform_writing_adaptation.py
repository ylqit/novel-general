import json
from pathlib import Path

import pytest

from longform_engine.agent_tasks import load_manifest
from longform_engine.config import ConfigError, load_project_config
from longform_engine.creative import humanize_task
import longform_engine.editorial.pipeline as editorial_pipeline
import longform_engine.config.loader as config_loader
from longform_engine.orchestration import continue_write, open_book
from longform_engine.quality import (
    compact_effective_quality_contract,
    compile_effective_quality_contract,
)
from longform_engine.storage import init_project
from tests.project_fixtures import mark_project_ready


def test_default_config_uses_the_unified_quality_profile_shape():
    quality = config_loader.BUILTIN_DEFAULTS["quality"]

    assert quality["profile"]["market"] == "qidian_male"
    assert quality["profile"]["compatibility_markets"] == ["fanqie_free"]
    assert quality["profile"]["genre"] == "xuanhuan"
    assert "market_profile" not in quality
    assert "genre_profile" not in quality


@pytest.mark.parametrize(
    ("phase", "expected_fragment"),
    (
        ("opening", "前三章"),
        ("early_serial", "第4-30章"),
        ("stable_serial", "轮换调查"),
        ("volume_climax", "卷级承诺"),
        ("aftermath", "允许完整余波"),
    ),
)
def test_qidian_phase_contracts_have_distinct_serial_promises(tmp_path, phase, expected_fragment):
    config = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "project": {"root_dir": str(tmp_path / phase)},
            "quality": {"profile": {"phase": phase}},
        },
    )

    payload = compile_effective_quality_contract(config, chapter_number=31)

    assert payload["primary_market"] == "qidian_male"
    assert payload["phase"] == phase
    assert expected_fragment in payload["contract"]["platform_promise"]
    assert payload["market_phase"]["applied"] is True
    assert payload["blocking_policy"]["primary_deviation"] == "P2_advisory"
    assert payload["blocking_policy"]["primary_can_block"] is False


def test_fanqie_compatibility_is_bounded_non_blocking_and_does_not_mutate_project(tmp_path):
    config = load_project_config(
        template="qidian-longform",
        cli_overrides={"project": {"root_dir": str(tmp_path / "compatibility")}},
    )
    root = Path(config.data["project"]["root_dir"])
    root.mkdir(parents=True)
    sentinel = root / "sentinel.txt"
    sentinel.write_text("unchanged", encoding="utf-8")
    before = sentinel.read_bytes()

    payload = compile_effective_quality_contract(
        config,
        chapter_number=1,
        compare_markets=["fanqie_free"],
    )

    observations = payload["compatibility_observations"]
    assert 1 <= len(observations) <= 3
    assert all(item["market"] == "fanqie_free" for item in observations)
    assert all(item["severity"] == "P2" and item["blocking"] is False for item in observations)
    assert payload["blocking_policy"]["compatibility_can_block"] is False
    assert sentinel.read_bytes() == before
    assert list(root.iterdir()) == [sentinel]


def test_compact_contract_omits_explanation_payload_but_keeps_agent_boundaries(tmp_path):
    config = load_project_config(
        template="qidian-longform",
        cli_overrides={"project": {"root_dir": str(tmp_path / "compact")}},
    )
    full = compile_effective_quality_contract(config, chapter_number=1)

    compact = compact_effective_quality_contract(full)

    assert "merge_trace" not in compact
    assert "sources" not in compact
    assert compact["contract"]["platform_promise"]
    assert len(compact["compatibility_observations"]) <= 3
    assert compact["blocking_policy"]["deterministic_P0_P1_unchanged"] is True


@pytest.mark.parametrize(
    ("profile_override", "message"),
    (
        ({"compatibility_markets": "fanqie_free"}, "compatibility_markets must be a list"),
        ({"compatibility_markets": ["unknown_market"]}, "compatibility_markets must contain only"),
        (
            {"overrides": {"platform_policy": {"primary_deviation": "P0_blocking"}}},
            "primary_deviation",
        ),
    ),
)
def test_platform_contract_config_rejects_invalid_values(profile_override, message):
    with pytest.raises(ConfigError, match=message):
        load_project_config(
            template="qidian-longform",
            cli_overrides={"quality": {"profile": profile_override}},
        )


def test_chapter_card_writer_brief_and_humanizer_share_one_bounded_contract(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    root = project.root
    open_book(config)
    mark_project_ready(root, config)

    continue_write(config, chapter_number=1)

    card = json.loads((root / "20_outline" / "chapter_cards" / "ch001.json").read_text(encoding="utf-8"))
    task = json.loads((root / "50_workbench" / "writing_tasks" / "ch001.json").read_text(encoding="utf-8"))
    task_markdown = (root / "50_workbench" / "writing_tasks" / "ch001.md").read_text(encoding="utf-8")
    manifest = load_manifest(root, "chapter_write:ch001:v1")

    assert card["platform_promise"] == card["effective_quality_contract"]["contract"]["platform_promise"]
    assert card["chapter_duty"]
    assert card["reader_gain"]
    assert card["cost"]
    assert "relationship_move" in card
    assert task["writing_brief"]["quality_contract"] == card["effective_quality_contract"]
    assert "Compatibility advisory only" in task_markdown
    assert "not a fixed sentence, dialogue, pace, or cliffhanger template" in task_markdown
    assert len(manifest["input_files"]) <= 7
    assert len(task_markdown) <= 20_000

    draft = root / "40_manuscript" / "draft" / "ch001.md"
    draft.write_text("# Chapter 1\n\nA concrete scene with a consequential choice.\n", encoding="utf-8")
    humanizer = humanize_task(config, chapter_number=1)
    humanizer_text = Path(humanizer.task_file).read_text(encoding="utf-8")
    assert "Primary market: qidian_male" in humanizer_text
    assert "Advisory only [fanqie_free/" in humanizer_text
    assert "fixed platform quotas" in humanizer_text


def test_editorial_roles_treat_platform_fit_as_sustainable_and_advisory():
    planning = editorial_pipeline.role_instruction("planning_chief_editor")
    reader = editorial_pipeline.role_instruction("reader_quality_reviewer")

    assert "platform promise remains sustainable" in planning
    assert "Do not require a payoff or cliffhanger quota" in planning
    assert "medium-term reading motivation" in reader
    assert "non-blocking P2 advice" in reader
