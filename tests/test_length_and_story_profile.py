from itertools import combinations
import json

import pytest
import yaml

from longform_engine.completion import approve_completion, completion_status, fast_completion_marker
from longform_engine.config import load_project_config
from longform_engine.intelligence import assess_project_readiness
from longform_engine.lengths import compile_length_forecast
from longform_engine.resources import resource_path
from longform_engine.storage import init_project
from longform_engine.story_profiles import BUILTIN_MARKET_IDS, FACET_KINDS, compile_story_profile
from longform_engine.semantic.pipeline import (
    materialize_character_views,
    materialize_foreshadow_state,
    materialize_tcs,
)
from longform_engine.text_metrics import measure_manuscript_text


def length_contract(target: int) -> dict:
    return {
        "metric": "content_characters_v1",
        "target_total_characters": target,
        "completion_tolerance": [0.9, 1.1],
        "chapter": {
            "target_characters": 3000,
            "soft_min": 2400,
            "soft_max": 3600,
            "hard_min": 2000,
            "hard_max": 4200,
        },
        "volume": {"target_characters": 250000},
        "planning": {"mode": "rolling", "detailed_horizon": 20, "refill_threshold": 8},
    }


@pytest.mark.parametrize(
    ("target", "chapters", "minimum", "maximum", "volumes", "formal"),
    (
        (100_000, 33, 28, 42, 1, True),
        (1_000_000, 333, 278, 417, 4, True),
        (2_000_000, 667, 556, 834, 8, True),
        (3_000_000, 1000, 834, 1250, 12, False),
    ),
)
def test_length_forecast_is_character_budget_first(target, chapters, minimum, maximum, volumes, formal):
    result = compile_length_forecast(length_contract(target))

    assert result.estimated_chapters == chapters
    assert result.minimum_reasonable_chapters == minimum
    assert result.maximum_reasonable_chapters == maximum
    assert result.estimated_volumes == volumes
    assert result.formal_support is formal
    assert result.support_status == ("formal" if formal else "experimental")


def test_manuscript_metric_excludes_title_markdown_whitespace_and_punctuation():
    result = measure_manuscript_text("# 第一章 标题\n\n**沈阙**说：‘走。’\nRook-01")

    assert result.metric == "content_characters_v1"
    assert result.content_characters == len("沈阙说走Rook01")
    assert result.display_characters > result.content_characters


def test_story_profile_fixture_matrix_compiles_and_exposes_human_conflicts():
    payload = yaml.safe_load(resource_path("config", "story_profile_fixtures.yaml").read_text(encoding="utf-8"))
    compiled = {
        item["id"]: compile_story_profile(item["profile"], market_ids=set(BUILTIN_MARKET_IDS))
        for item in payload["fixtures"]
    }

    assert compiled["game_fanfiction_mix"]["ready"] is True
    assert len(compiled["game_fanfiction_mix"]["selected_facets"]) > 8
    assert compiled["unresolved_conflict"]["ready"] is False
    assert len(compiled["unresolved_conflict"]["unresolved_conflicts"]) == 2


def test_fixture_matrix_pairwise_covers_nonempty_facet_kinds():
    payload = yaml.safe_load(resource_path("config", "story_profile_fixtures.yaml").read_text(encoding="utf-8"))
    observed: set[tuple[str, str]] = set()
    for fixture in payload["fixtures"]:
        if fixture.get("expect_ready") is False:
            continue
        compiled = compile_story_profile(fixture["profile"], market_ids=set(BUILTIN_MARKET_IDS))
        kinds = {item["kind"] for item in compiled["selected_facets"]}
        observed.update(tuple(sorted(pair)) for pair in combinations(kinds, 2))

    expected = {tuple(sorted(pair)) for pair in combinations(FACET_KINDS, 2)}
    assert observed == expected


def test_human_resolution_is_explicit_and_reproducible():
    payload = yaml.safe_load(resource_path("config", "story_profile_fixtures.yaml").read_text(encoding="utf-8"))
    profile = next(item["profile"] for item in payload["fixtures"] if item["id"] == "unresolved_conflict")
    unresolved = compile_story_profile(profile, market_ids=set(BUILTIN_MARKET_IDS))
    profile["resolutions"] = [
        {
            "conflict_id": item["conflict_id"],
            "decision": "Use ensemble agency and allow tonal release.",
            "rationale": "The approved story contract prioritizes supporting-character causality.",
        }
        for item in unresolved["unresolved_conflicts"]
    ]

    resolved = compile_story_profile(profile, market_ids=set(BUILTIN_MARKET_IDS))
    assert resolved["ready"] is True
    assert resolved["unresolved_conflicts"] == []


def test_unused_story_profile_resolution_is_not_silently_accepted():
    config = load_project_config(template="qidian-longform")
    profile = config.data["story_profile"]
    profile["resolutions"] = [
        {
            "conflict_id": "conflict:not-selected",
            "decision": "Prefer a single lead.",
            "rationale": "This decision belongs to another profile and must not leak into this project.",
        }
    ]

    compiled = compile_story_profile(profile, market_ids=set(BUILTIN_MARKET_IDS))

    assert compiled["ready"] is False
    assert compiled["unused_resolution_ids"] == ["conflict:not-selected"]


def test_project_readiness_stops_on_unresolved_story_profile_conflict(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    state_path = project.root / "30_state" / "novel_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "awaiting_project_intelligence"
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config.data["story_profile"]["narrative_forms"] = ["ensemble", "single_lead_only"]

    readiness = assess_project_readiness(config)

    assert readiness.ready is False
    assert readiness.stage == "story_profile_conflict"
    assert readiness.errors[0].startswith("unresolved story-profile conflict:")


def test_book_completion_requires_character_budget_closure_and_human_approval(tmp_path):
    template = load_project_config(
        template="qidian-longform",
        cli_overrides={"length": length_contract(100_000)},
    )
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    final = project.root / "40_manuscript" / "final" / "ch033.md"
    final.write_text("# 终章\n\n" + "终" * 90_000, encoding="utf-8")
    closure = project.root / "30_state" / "chapter_closures" / "ch033.json"
    closure.parent.mkdir(parents=True, exist_ok=True)
    closure.write_text('{"schema":"chapter_closure_v1","chapter_number":33}\n', encoding="utf-8")

    before = completion_status(config)
    assert before.ready_for_human_approval is True
    assert before.approved is False
    assert before.total_content_characters == 90_000

    after = approve_completion(config, approved_by="human", ending_summary="The approved ending closes the main promise.")
    assert after.approved is True
    assert fast_completion_marker(config)[0] == "approved"

    final.write_text(final.read_text(encoding="utf-8") + "改", encoding="utf-8")
    assert fast_completion_marker(config)[0] == "invalid"


def test_completion_does_not_treat_forecast_chapter_number_as_an_ending(tmp_path):
    template = load_project_config(
        template="qidian-longform",
        cli_overrides={"length": length_contract(100_000)},
    )
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config)
    final_dir = project.root / "40_manuscript" / "final"
    closure_dir = project.root / "30_state" / "chapter_closures"
    closure_dir.mkdir(parents=True, exist_ok=True)
    for chapter in range(1, 34):
        (final_dir / f"ch{chapter:03d}.md").write_text("章" * 100, encoding="utf-8")
    (closure_dir / "ch033.json").write_text('{"schema":"chapter_closure_v1"}\n', encoding="utf-8")

    status = completion_status(config)

    assert status.latest_final_chapter == 33
    assert status.ready_for_human_approval is False
    assert "content_character_target_below_tolerance" in status.blockers
    assert status.length_status == "below_tolerance"
    assert "do not add filler" in status.recommended_action


def test_two_million_character_current_views_remain_bounded_across_667_updates(tmp_path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    root = project.root
    write_json(
        root / "10_bible" / "characters.json",
        [{"id": "lead_ari", "name": "Ari"}],
    )
    write_json(
        root / "20_outline" / "foreshadowing_ledger.json",
        [
            {
                "id": "thread_return_route",
                "description": "The route remains unresolved.",
                "status": "planned",
            }
        ],
    )
    graph = {"relationships": []}
    latest_tcs = {}
    for chapter in range(1, 668):
        evidence = {"start": 0, "end": 1, "excerpt": "证"}
        payload = {
            "source": {"path": f"40_manuscript/final/ch{chapter:03d}.md"},
            "chapter_digest": {"summary": f"Bounded update {chapter}."},
            "scenes": [],
            "events": [
                {
                    "event_id": f"event:{chapter}",
                    "title": f"Decision {chapter}",
                    "locations": ["archive"],
                }
            ],
            "relationship_deltas": [],
            "character_deltas": [
                {
                    "character_id": "lead_ari",
                    "status": "active",
                    "goal": f"Resolve bounded step {chapter}.",
                    "emotion": "focused",
                    "beliefs_added": [f"belief-{chapter}"],
                    "beliefs_removed": [],
                    "knowledge_gained": [{"fact": f"fact-{chapter}"}],
                    "knowledge_removed": [],
                    "commitments_added": [f"commitment-{chapter}"],
                    "commitments_removed": [],
                    "abilities_added": [],
                    "abilities_removed": [],
                    "inventory_added": [],
                    "inventory_removed": [],
                    "evidence": evidence,
                }
            ],
            "foreshadow_deltas": [
                {
                    "thread_id": "thread_return_route",
                    "action": "reinforce",
                    "description": f"Echo {chapter}.",
                    "resulting_status": "active",
                    "evidence": evidence,
                }
            ],
            "coverage": {"featured_character_ids": ["lead_ari"]},
            "retrieval": {"tags": ["return-route"]},
        }
        materialize_character_views(root, payload, chapter)
        foreshadow = materialize_foreshadow_state(root, payload, chapter)
        write_json(root / "30_state" / "foreshadowing_state.json", foreshadow)
        latest_tcs = materialize_tcs(root, payload, chapter, graph, foreshadow)

    character = json.loads(
        (root / "60_rag" / "memory" / "characters" / "lead_ari.json").read_text(encoding="utf-8")
    )
    thread = json.loads(
        (root / "30_state" / "foreshadowing_state.json").read_text(encoding="utf-8")
    )["threads"]["thread_return_route"]
    assert "state_history" not in character
    assert len(character["current_beliefs"]) == 20
    assert len(character["knowledge_scope"]) == 24
    assert len(character["commitments"]) == 12
    assert len(character["recent_evidence"]) == 12
    assert len(thread["recent_actions"]) == 5
    assert len(latest_tcs["current_characters"]) == 1
    assert len(latest_tcs["recent_events"]) == 1


def write_json(path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
