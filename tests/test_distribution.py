from hashlib import sha256
import json
from pathlib import Path

import pytest

from longform_engine import __version__
from longform_engine.config import load_project_config
from longform_engine.distribution import (
    distribution_version_payload,
    doctor_payload,
    install_skills,
    skill_status_payload,
    tree_hash,
    uninstall_skills,
    update_skills,
)
from longform_engine.resources import RESOURCE_HASH_POLICY, load_resource_manifest, resource_integrity_bytes, resource_path
from longform_engine.storage import init_project


def configure_skill_roots(monkeypatch, tmp_path: Path):
    codex = tmp_path / "codex-skills"
    claude = tmp_path / "claude-skills"
    monkeypatch.setenv("LONGFORM_CODEX_SKILL_ROOT", str(codex))
    monkeypatch.setenv("LONGFORM_CLAUDE_SKILL_ROOT", str(claude))
    return codex, claude


def test_version_and_bundled_resource_manifest_are_aligned(tmp_path):
    manifest = load_resource_manifest()
    asset_paths = [item["path"] for item in manifest["assets"]]

    assert __version__ == "0.5.0"
    assert manifest["engine_version"] == __version__
    assert manifest["hash_policy"] == RESOURCE_HASH_POLICY
    for prefix in ("config/", "templates/", "longform-novel-codex/", "longform-novel-claude/", "shared/"):
        group = [path for path in asset_paths if path.startswith(prefix)]
        assert group == sorted(group, key=str.casefold)
    assert resource_path("config", "default.engine.yaml").is_file()
    assert resource_path("templates", "qidian-longform", "project.yaml").is_file()
    assert resource_path("longform-novel-codex", "references", "command_protocol.md").is_file()
    assert resource_path("longform-novel-claude", "references", "command_protocol.md").is_file()

    unix_text = tmp_path / "unix.json"
    windows_text = tmp_path / "windows.json"
    binary = tmp_path / "payload.bin"
    unix_text.write_bytes(b'{\n  "ok": true\n}\n')
    windows_text.write_bytes(b'{\r\n  "ok": true\r\n}\r\n')
    binary.write_bytes(b"a\r\nb\r")

    assert resource_integrity_bytes(unix_text) == resource_integrity_bytes(windows_text)
    assert resource_integrity_bytes(binary) == b"a\r\nb\r"

    tree = tmp_path / "skill"
    (tree / "references").mkdir(parents=True)
    (tree / "SKILL.md").write_bytes(b"role\r\n")
    (tree / "references" / "contract.md").write_bytes(b"contract\n")
    expected = sha256()
    for relative in ("references/contract.md", "SKILL.md"):
        expected.update(relative.encode("utf-8"))
        expected.update(b"\0")
        expected.update(resource_integrity_bytes(tree / relative))
        expected.update(b"\0")
    assert tree_hash(tree) == expected.hexdigest()


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


def test_unowned_skill_requires_force_and_uninstall_refuses_unowned(monkeypatch, tmp_path):
    codex_root, _ = configure_skill_roots(monkeypatch, tmp_path)
    unowned = codex_root / "longform-novel-codex"
    unowned.mkdir(parents=True)
    (unowned / "SKILL.md").write_text("unowned", encoding="utf-8")

    with pytest.raises(ValueError, match="Unowned Skill"):
        install_skills("codex")
    with pytest.raises(ValueError, match="unowned Skill"):
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

    monkeypatch.setattr("longform_engine.distribution.importlib_metadata.version", lambda _name: __version__)
    payload = doctor_payload("all")

    assert payload["schema"] == "doctor_v1"
    assert payload["engine_version"] == "0.5.0"
    checks = {item["name"]: item for item in payload["checks"]}
    assert checks["distribution_version"]["ok"]
    assert checks["bundled_resources"]["ok"]
    assert checks["skill_codex"]["ok"]
    assert checks["skill_claude-code"]["ok"]
    assert f"@v{__version__}" in checks["semantic_dependencies"]["next_command"]
    json.dumps(payload)


def test_distribution_version_mismatch_blocks_doctor_with_one_reinstall_command(monkeypatch, tmp_path):
    configure_skill_roots(monkeypatch, tmp_path)
    install_skills("all")
    monkeypatch.setattr("longform_engine.distribution.importlib_metadata.version", lambda _name: "0.3.0")
    monkeypatch.setattr("longform_engine.distribution.importlib.util.find_spec", lambda _name: object())

    versions = distribution_version_payload("all")
    payload = doctor_payload("all")
    check = next(item for item in payload["checks"] if item["name"] == "distribution_version")

    assert not versions["ok"]
    assert versions["mismatches"] == {"distribution_metadata": "0.3.0"}
    assert check["next_command"] == versions["next_command"]
    assert not payload["ok"]


def test_doctor_reports_corrupt_editorial_pattern_registry_as_rebuildable_warning(monkeypatch, tmp_path):
    configure_skill_roots(monkeypatch, tmp_path)
    install_skills("all")
    monkeypatch.setattr("longform_engine.distribution.importlib_metadata.version", lambda _name: __version__)
    monkeypatch.setattr("longform_engine.distribution.importlib.util.find_spec", lambda _name: object())
    project = init_project(
        load_project_config(template="qidian-longform"),
        output=tmp_path / "novel",
    )
    registry = project.root / "50_workbench" / "editorial_patterns" / "registry.jsonl"
    registry.write_text("{broken\n", encoding="utf-8")

    payload = doctor_payload("all", project=str(project.project_config))
    check = next(item for item in payload["checks"] if item["name"] == "editorial_pattern_registry")

    assert not check["ok"]
    assert check["blocking"] is False
    assert "pattern-rebuild" in check["next_command"]
