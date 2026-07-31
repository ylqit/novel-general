import json

from longform_engine.config import load_project_config
from longform_engine.db import query_table
from longform_engine.graph import check_graph, update_graph, validate_graph
from longform_engine.graph.pipeline import infer_relation_type
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


def test_relation_inference_does_not_turn_unrelated_family_or_word_fragments_into_relationships():
    shen = {"id": "char_shen", "name": "沈阙"}
    ning = {"id": "char_ning", "name": "宁昭"}

    kinship, _, _ = infer_relation_type(
        "沈阙想起父亲的旧案。隔了两段查验记录，宁昭才推门进来。",
        shen,
        ning,
    )
    romance, _, _ = infer_relation_type(
        "沈阙递上拓片。宁昭核对缺口，确认两枚轮印正好吻合。",
        shen,
        ning,
    )
    explicit, _, _ = infer_relation_type(
        "宁昭是沈阙的姐姐，这层关系从未写进公档。",
        shen,
        ning,
    )

    assert kinship == "co_occurs"
    assert romance == "co_occurs"
    assert explicit == "kinship"


def test_graph_update_removes_stale_deterministic_relationship_for_same_chapter(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root
    (root / "10_bible" / "characters.json").write_text(
        json.dumps(
            [
                {"id": "char_shen", "name": "沈阙", "type": "character"},
                {"id": "char_ning", "name": "宁昭", "type": "character"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    final = root / "40_manuscript" / "final" / "ch001.md"
    summary = root / "40_manuscript" / "summaries" / "ch001.md"
    final.write_text("# 第一章\n\n宁昭是沈阙的姐姐。\n", encoding="utf-8")
    summary.write_text("宁昭与沈阙核对身份。\n", encoding="utf-8")
    project_config = load_project_config(project.project_config)

    update_graph(project_config, chapter_number=1)
    graph_path = root / "30_state" / "story_graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert any(item.get("type") == "kinship" for item in graph["relationships"])

    final.write_text("# 第一章\n\n沈阙递上拓片，宁昭确认两枚轮印吻合。\n", encoding="utf-8")
    update_graph(project_config, chapter_number=1)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))

    assert not any(item.get("type") == "kinship" for item in graph["relationships"])
    assert not any(item.get("type") == "romantic_tension" for item in graph["relationships"])
    assert any(item.get("type") == "co_occurs" for item in graph["relationships"])


def test_graph_status_inference_binds_injury_to_the_named_character(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root
    (root / "10_bible" / "characters.json").write_text(
        json.dumps(
            [
                {"id": "char_luo", "name": "罗砚", "type": "character"},
                {"id": "char_zhao", "name": "赵戍", "type": "character"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "40_manuscript" / "final" / "ch001.md").write_text(
        "# 第一章\n\n罗砚从值房出来。他没受伤。罗砚救下受伤的赵戍。赵戍正好受伤，无法押车。\n",
        encoding="utf-8",
    )
    (root / "40_manuscript" / "summaries" / "ch001.md").write_text("赵戍重伤，罗砚仍在值守。\n", encoding="utf-8")

    update_graph(load_project_config(project.project_config), chapter_number=1)
    graph = json.loads((root / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    entities = {item["id"]: item for item in graph["entities"]}

    assert "status" not in entities["char_luo"]
    assert entities["char_zhao"]["status"] == "injured"


def test_graph_update_removes_stale_deterministic_status_for_same_chapter(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    root = project.root
    (root / "10_bible" / "characters.json").write_text(
        json.dumps(
            [
                {"id": "char_luo", "name": "罗砚", "type": "character"},
                {"id": "char_zhao", "name": "赵戍", "type": "character"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    final = root / "40_manuscript" / "final" / "ch001.md"
    summary = root / "40_manuscript" / "summaries" / "ch001.md"
    final.write_text("# 第一章\n\n罗砚受了重伤。\n", encoding="utf-8")
    summary.write_text("罗砚受伤。\n", encoding="utf-8")
    project_config = load_project_config(project.project_config)

    update_graph(project_config, chapter_number=1)
    graph_path = root / "30_state" / "story_graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    entities = {item["id"]: item for item in graph["entities"]}
    assert entities["char_luo"]["status"] == "injured"

    final.write_text("# 第一章\n\n罗砚继续值守。赵戍受了重伤。\n", encoding="utf-8")
    summary.write_text("赵戍受伤，罗砚继续值守。\n", encoding="utf-8")
    update_graph(project_config, chapter_number=1)
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    entities = {item["id"]: item for item in graph["entities"]}

    assert "status" not in entities["char_luo"]
    assert entities["char_zhao"]["status"] == "injured"
    assert not entities["char_luo"].get("status_history")


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
