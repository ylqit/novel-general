from longform_engine.config import ConfigError, load_project_config


def test_template_config_loads_with_defaults():
    config = load_project_config(template="qidian-longform")

    assert config.data["project"]["slug"] == "longform_150w_demo"
    assert config.data["length"]["total_chapters"] == 500
    assert config.data["length"]["target_total_words"] == 1500000
    assert config.data["storage"]["directories"]["runtime"] == "70_runtime"
    assert config.data["rag"]["backend"] == "sqlite_hybrid"
    assert config.data["writing"]["mode"] == "agent_skill"
    assert config.data["writing"]["agent"]["task_dir"] == "50_workbench/writing_tasks"
    assert config.data["writing"]["agent"]["draft_dir"] == "50_workbench/agent_drafts"
    assert config.data["writing"]["api"]["enabled"] is False


def test_invalid_word_count_range_fails(tmp_path):
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(
        """
project:
  slug: bad
  title: Bad
  root_dir: novels/bad
length:
  total_chapters: 10
  volume_count: 1
  chapter_word_count:
    target: 100
    min: 200
    max: 300
""",
        encoding="utf-8",
    )

    try:
        load_project_config(config_path)
    except ConfigError as exc:
        assert "chapter_word_count target" in str(exc)
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
  total_chapters: 120
  volume_count: 3
  chapter_word_count:
    target: 2600
    min: 2000
    max: 3200
""",
        encoding="utf-8",
    )

    config = load_project_config(config_path)

    assert config.data["project"]["title"] == "自定义项目"
    assert config.data["length"]["total_chapters"] == 120
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
length:
  total_chapters: 10
  volume_count: 1
  chapter_word_count:
    target: 2600
    min: 2000
    max: 3200
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
length:
  total_chapters: 10
  volume_count: 1
  chapter_word_count:
    target: 2600
    min: 2000
    max: 3200
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
  total_chapters: 120
  volume_count: 3
  chapter_word_count:
    target: 2600
    min: 2000
    max: 3200
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
                "total_chapters": 330,
                "target_total_words": 1000000,
                "volume_count": 5,
                "chapter_word_count": {
                    "target": 3000,
                    "min": 2400,
                    "max": 3600,
                },
            },
        },
    )

    assert config.data["length"]["total_chapters"] == 330
    assert config.data["length"]["target_total_words"] == 1000000
    assert config.data["length"]["volume_count"] == 5
