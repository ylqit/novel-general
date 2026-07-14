import json

from longform_engine.config import load_project_config
from longform_engine.db import query_table
from longform_engine.graph import check_graph, update_graph, validate_graph
from longform_engine.storage import init_project


def test_graph_validate_update_check_and_sqlite_mirror(tmp_path):
    project_config = seed_graph_project(tmp_path)

    initial = validate_graph(project_config)
    assert initial.errors == ()

    update = update_graph(project_config, chapter_number=1)
    assert update.matched_entities == 2
    assert update.mentions_added == 2
    assert update.events_added == 1

    graph_path = tmp_path / "novel" / "30_state" / "story_graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert len(graph["entities"]) >= 2
    assert len(graph["events"]) == 1

    entities = query_table(project_config, "entities", limit=20)
    mentions = query_table(project_config, "entity_mentions", limit=20)
    events = query_table(project_config, "events", limit=20)

    assert {entity["name"] for entity in entities} >= {"林迟", "云门"}
    assert len(mentions) == 2
    assert events[0]["title"] == "第一章 山门之前"

    check = check_graph(project_config)
    assert check.issues == ()
    assert (tmp_path / "novel" / "50_workbench" / "graph_reports" / "graph_check.md").exists()


def test_graph_validate_reports_bad_entity_type(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    graph_path = project.root / "30_state" / "story_graph.json"
    graph_path.write_text(
        json.dumps(
            {
                "entities": [{"id": "x", "name": "错误实体", "type": "unsupported"}],
                "relationships": [],
                "events": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = validate_graph(load_project_config(project.project_config))

    assert result.errors
    assert "unsupported type" in result.errors[0]


def test_graph_check_reports_agent_draft_risks_without_mutating_graph(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    root = project.root

    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}]),
        encoding="utf-8",
    )
    (root / "10_bible" / "locations.json").write_text(
        json.dumps([{"id": "location:north_gate", "name": "North Gate", "type": "location"}]),
        encoding="utf-8",
    )
    (root / "10_bible" / "abilities.json").write_text(
        json.dumps([{"id": "ability:star_step", "name": "Star Step", "type": "ability"}]),
        encoding="utf-8",
    )
    (root / "40_manuscript" / "draft" / "ch001.md").write_text(
        "# Chapter 1\n\nAri crosses North Gate and uses Star Step without paying its cost.\n",
        encoding="utf-8",
    )
    gate_dir = root / "50_workbench" / "gate_artifacts" / "ch001"
    gate_dir.mkdir(parents=True, exist_ok=True)
    (gate_dir / "gate_result.json").write_text(
        json.dumps({"chapter_number": 1, "passed": False, "failures": ["ability boundary"]}),
        encoding="utf-8",
    )
    graph_path = root / "30_state" / "story_graph.json"
    before = graph_path.read_text(encoding="utf-8")

    result = check_graph(project_config)

    after = graph_path.read_text(encoding="utf-8")
    report = (root / "50_workbench" / "graph_reports" / "graph_check.md").read_text(encoding="utf-8")

    assert after == before
    assert any("Agent draft timeline risk ch001" in warning for warning in result.warnings)
    assert any("Agent draft character risk ch001" in warning for warning in result.warnings)
    assert any("Agent draft location risk ch001" in warning for warning in result.warnings)
    assert any("Agent draft ability boundary risk ch001" in warning for warning in result.warnings)
    assert "## Agent Draft Risks" in report


def seed_graph_project(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root

    (root / "10_bible" / "characters.json").write_text(
        json.dumps(
            [
                {
                    "id": "character:lin_chi",
                    "name": "林迟",
                    "type": "character",
                    "aliases": ["少年"],
                    "description": "山门外的主角。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "10_bible" / "locations.json").write_text(
        json.dumps(
            [
                {
                    "id": "location:cloud_gate",
                    "name": "云门",
                    "type": "location",
                    "description": "试炼山门。",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# 第一章 山门之前\n\n林迟抵达云门，旧钟声第一次响起。\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "summaries" / "ch001.md").write_text(
        "林迟抵达云门，旧钟声打开第一层秘密。\n",
        encoding="utf-8",
    )
    return load_project_config(project.project_config)
