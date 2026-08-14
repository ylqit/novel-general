import pytest

from longform_engine.config import ConfigError, load_project_config


def test_template_config_loads_with_defaults():
    config = load_project_config(template="qidian-longform")

    assert config.data["project"]["slug"] == "longform_200w_demo"
    assert config.data["length"]["target_total_characters"] == 2000000
    assert config.data["length"]["planning"]["detailed_horizon"] == 20
    assert "total_chapters" not in config.data["length"]
    assert config.data["storage"]["directories"]["runtime"] == "70_runtime"
    assert config.data["rag"]["backend"] == "sqlite_hybrid"
    assert config.data["writing"]["mode"] == "agent_skill"
    assert config.data["writing"]["agent"]["task_dir"] == "50_workbench/writing_tasks"
    assert config.data["writing"]["agent"]["draft_dir"] == "50_workbench/agent_drafts"
    assert "api" not in config.data["writing"]
    assert "models" not in config.data
    assert config.data["quality"]["assurance_mode"] == "balanced"
    assert config.data["quality"]["reader_payoff"]["review_mode"] == "risk_based"
    assert config.data["quality"]["reader_payoff"]["structure_window"] == 20
    assert config.data["quality"]["reader_payoff"]["language_similarity_threshold"] == 0.72
    assert config.data["quality"]["humanizer"]["semantic_review_mode"] == "risk_based"
    assert config.data["quality"]["humanizer"]["semantic_review_change_ratio"] == 0.15
    assert config.data["semantic"]["vector_store"]["backend"] == "local_hnsw"
    assert config.data["semantic"]["vector_store"]["hnsw_threshold"] == 10000
    assert config.data["semantic"]["vector_store"]["hnsw_ef_search"] == 80


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"quality": {"assurance_mode": "unsafe"}}, "quality.assurance_mode"),
        ({"quality": {"humanizer": {"semantic_review_mode": "skip"}}}, "semantic_review_mode"),
        ({"quality": {"reader_payoff": {"review_mode": "skip"}}}, "reader_payoff.review_mode"),
        ({"quality": {"reader_payoff": {"structure_window": 9}}}, "reader_payoff.structure_window"),
        ({"semantic": {"vector_store": {"backend": "fake"}}}, "semantic.vector_store.backend"),
        ({"semantic": {"vector_store": {"hnsw_threshold": 0}}}, "hnsw_threshold"),
        (
            {
                "quality": {
                    "humanizer": {
                        "semantic_review_change_ratio": 0.7,
                        "changed_character_human_ratio": 0.6,
                    }
                }
            },
            "semantic_review_change_ratio",
        ),
    ],
)
def test_invalid_humanizer_semantic_review_config_fails(overrides, message):
    with pytest.raises(ConfigError, match=message):
        load_project_config(template="qidian-longform", cli_overrides=overrides)


def test_invalid_word_count_range_fails(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        """
project:
  slug: bad
  title: Bad
  root_dir: novels/bad
length:
  chapter:
    target_characters: 100
    soft_min: 200
    soft_max: 300
""",
        encoding="utf-8",
    )

    try:
        load_project_config(config_path)
    except ConfigError as exc:
        assert "hard_min <= soft_min <= target_characters" in str(exc)
    else:
        raise AssertionError("Expected ConfigError")


def test_project_config_overrides_template_and_uses_default_fallbacks(tmp_path):
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        """
project:
  slug: custom
  title: 自定义项目
  root_dir: novel
length:
  target_total_characters: 360000
  chapter:
    target_characters: 2600
    soft_min: 2000
    soft_max: 3200
""",
        encoding="utf-8",
    )

    config = load_project_config(config_path)

    assert config.data["project"]["title"] == "自定义项目"
    assert config.data["length"]["target_total_characters"] == 360000
    assert config.data["rag"]["backend"] == "sqlite_hybrid"
    assert config.data["storage"]["directories"]["runtime"] == "70_runtime"
    assert config.data["writing"]["mode"] == "agent_skill"


def test_invalid_writing_mode_fails(tmp_path):
    config_path = tmp_path / "bad_writing.yaml"
    config_path.write_text(
        """
project:
  slug: bad
  title: Bad
  root_dir: novels/bad
writing:
  mode: direct_prompt
""",
        encoding="utf-8",
    )

    try:
        load_project_config(config_path)
    except ConfigError as exc:
        assert "writing.mode" in str(exc)
    else:
        raise AssertionError("Expected ConfigError")


def test_legacy_storage_paths_are_rejected(tmp_path):
    config_path = tmp_path / "legacy_paths.yaml"
    config_path.write_text(
        """
project:
  slug: bad
  title: Bad
  root_dir: novels/bad
storage:
  directories:
    manuscript: "03_manuscript"
""",
        encoding="utf-8",
    )

    try:
        load_project_config(config_path)
    except ConfigError as exc:
        assert "legacy path" in str(exc)
    else:
        raise AssertionError("Expected ConfigError")


def test_cli_overrides_win_over_project_config(tmp_path):
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        """
project:
  slug: custom
  title: 自定义项目
  root_dir: novel
length:
  target_total_characters: 360000
""",
        encoding="utf-8",
    )

    config = load_project_config(
        config_path,
        cli_overrides={
            "project": {"title": "命令行标题"},
            "rag": {"top_k": 5},
        },
    )

    assert config.data["project"]["title"] == "命令行标题"
    assert config.data["rag"]["top_k"] == 5
    assert config.sources[-1] == "cli overrides"


def test_scale_preset_style_overrides_template_length():
    config = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "length": {
                "target_total_characters": 1000000,
                "volume": {"target_characters": 200000},
            },
        },
    )

    assert config.data["length"]["target_total_characters"] == 1000000
    assert config.data["length"]["volume"]["target_characters"] == 200000


def test_fanfiction_creation_modes_accept_advisory_rights_and_require_crossover_sources():
    source = {
        "source_id": "source_a",
        "title": "Source A",
        "creator": "Creator A",
        "canon_cutoff": "volume-1",
        "allowed_elements": ["characters", "relationships", "world", "abilities", "timeline"],
        "rights_status": "unverified",
        "commercial_intent": True,
        "platform_policy_url": "",
    }
    for continuity_mode in (
        "canon_compliant",
        "canon_divergent",
        "alternate_universe",
        "continuation",
        "prequel",
    ):
        config = load_project_config(
            template="qidian-longform",
            cli_overrides={
                "creation": {"mode": "fanfiction"},
                "fanfiction": {
                    "continuity_mode": continuity_mode,
                    "sources": [source],
                },
            },
        )
        assert config.data["fanfiction"]["sources"][0]["rights_status"] == "unverified"
        assert config.data["fanfiction"]["sources"][0]["commercial_intent"] is True

    second_source = {
        **source,
        "source_id": "source_b",
        "title": "Source B",
        "creator": "Creator B",
    }
    crossover = load_project_config(
        template="qidian-longform",
        cli_overrides={
            "creation": {"mode": "fanfiction"},
            "fanfiction": {
                "continuity_mode": "crossover",
                "sources": [source, second_source],
            },
        },
    )
    assert len(crossover.data["fanfiction"]["sources"]) == 2

    try:
        load_project_config(
            template="qidian-longform",
            cli_overrides={
                "creation": {"mode": "fanfiction"},
                "fanfiction": {
                    "continuity_mode": "crossover",
                    "sources": [source],
                },
            },
        )
    except ConfigError as exc:
        assert "at least two sources" in str(exc)
    else:
        raise AssertionError("Expected crossover with one source to fail.")
