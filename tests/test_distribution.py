import json
from pathlib import Path

import pytest

from longform_engine import __version__
from longform_engine.distribution import (
    doctor_payload,
    install_skills,
    skill_status_payload,
    uninstall_skills,
    update_skills,
)
from longform_engine.resources import load_resource_manifest, resource_path


def configure_skill_roots(monkeypatch, tmp_path: Path):
    codex = tmp_path / "codex-skills"
    claude = tmp_path / "claude-skills"
    monkeypatch.setenv("LONGFORM_CODEX_SKILL_ROOT", str(codex))
    monkeypatch.setenv("LONGFORM_CLAUDE_SKILL_ROOT", str(claude))
    return codex, claude


def test_version_and_bundled_resource_manifest_are_aligned():
    manifest = load_resource_manifest()
    asset_paths = [item["path"] for item in manifest["assets"]]

    assert __version__ == "0.3.1"
    assert manifest["engine_version"] == __version__
    for prefix in ("config/", "templates/", "longform-novel-codex/", "longform-novel-claude/", "shared/"):
        group = [path for path in asset_paths if path.startswith(prefix)]
        assert group == sorted(group, key=str.casefold)
    assert resource_path("config", "default.engine.yaml").is_file()
    assert resource_path("templates", "qidian-longform", "project.yaml").is_file()
    assert resource_path("longform-novel-codex", "references", "command_protocol.md").is_file()
    assert resource_path("longform-novel-claude", "references", "command_protocol.md").is_file()


def test_skill_lifecycle_is_owned_hashed_and_atomic(monkeypatch, tmp_path):
    codex_root, claude_root = configure_skill_roots(monkeypatch, tmp_path)

    installed = install_skills("all")
    assert {item["state"] for item in installed["results"]} == {"current"}
    assert (codex_root / "longform-novel-codex" / ".longform-install.json").is_file()
    assert (claude_root / "longform-novel-claude" / ".longform-install.json").is_file()
    assert not (codex_root / "shared").exists()

    skill_file = codex_root / "longform-novel-codex" / "SKILL.md"
    skill_file.write_text(skill_file.read_text(encoding="utf-8") + "\nlocal drift\n", encoding="utf-8")
    status = skill_status_payload("codex")
    assert status["schema"] == "skill_install_status_v1"
    assert status["results"][0]["state"] == "outdated"

    updated = update_skills("codex")
    assert updated["results"][0]["state"] == "current"
    removed = uninstall_skills("all", confirmed=True)
    assert {item["state"] for item in removed["results"]} == {"missing"}


def test_legacy_skill_requires_force_and_uninstall_refuses_unowned(monkeypatch, tmp_path):
    codex_root, _ = configure_skill_roots(monkeypatch, tmp_path)
    legacy = codex_root / "longform-novel-codex"
    legacy.mkdir(parents=True)
    (legacy / "SKILL.md").write_text("legacy", encoding="utf-8")

    with pytest.raises(ValueError, match="Legacy Skill"):
        install_skills("codex")
    with pytest.raises(ValueError, match="unowned or legacy"):
        uninstall_skills("codex", confirmed=True)

    migrated = install_skills("codex", force=True)
    assert migrated["results"][0]["state"] == "current"


def test_dangerous_home_skill_root_is_rejected(monkeypatch):
    monkeypatch.setenv("LONGFORM_CODEX_SKILL_ROOT", str(Path.home()))

    with pytest.raises(ValueError, match="unsafe Skill target"):
        install_skills("codex", force=True)


def test_doctor_json_contract_reports_actionable_checks(monkeypatch, tmp_path):
    configure_skill_roots(monkeypatch, tmp_path)
    install_skills("all")
    monkeypatch.setattr("longform_engine.distribution.importlib.util.find_spec", lambda _name: None)

    payload = doctor_payload("all")

    assert payload["schema"] == "doctor_v1"
    assert payload["engine_version"] == "0.3.1"
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["bundled_resources"]["ok"]
    assert checks["skill_codex"]["ok"]
    assert checks["skill_claude-code"]["ok"]
    assert f"@v{__version__}" in checks["semantic_dependencies"]["next_command"]
    json.dumps(payload)
