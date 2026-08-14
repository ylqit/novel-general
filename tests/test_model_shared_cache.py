import json
from pathlib import Path

import pytest

from longform_engine.config import load_project_config
from longform_engine.models import (
    ModelError,
    cache_status_payload,
    install_model_profile,
    migrate_models_to_shared,
    models_dir,
    shared_model_cache_root,
    verify_models,
)
from longform_engine.storage import init_project


def project_config(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


def seed_legacy_profile(config) -> Path:
    root = Path(config.data["project"]["root_dir"])
    profile = root / "70_runtime" / "models" / "bge-m3"
    (profile / "embedding").mkdir(parents=True)
    (profile / "reranker").mkdir(parents=True)
    (profile / "embedding" / "model.bin").write_bytes(b"embedding")
    (profile / "reranker" / "model.bin").write_bytes(b"reranker")
    return profile


def test_default_model_cache_is_shared_and_install_writes_project_reference(monkeypatch, tmp_path):
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    config = project_config(tmp_path)

    assert shared_model_cache_root() == shared.resolve()
    assert models_dir(config) == shared.resolve()
    result = install_model_profile(config, profile="local-hash")

    reference = Path(config.data["project"]["root_dir"]) / "70_runtime" / "semantic_model_cache_ref.json"
    payload = json.loads(reference.read_text(encoding="utf-8"))
    assert result.models_dir == str(shared.resolve())
    assert payload["schema"] == "semantic_model_cache_ref_v1"
    assert payload["shared_path"] == str(shared.resolve())
    assert payload["profile_manifest_sha256"]
    assert cache_status_payload()["profiles"][0]["manifest_ok"]


def test_legacy_model_migration_is_copy_verify_reference_then_delete(monkeypatch, tmp_path):
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    config = project_config(tmp_path)
    legacy_profile = seed_legacy_profile(config)

    dry_run = migrate_models_to_shared(config, dry_run=True, confirmed=False)
    assert dry_run["eligible"]
    assert not dry_run["migrated"]
    assert legacy_profile.exists()
    assert not shared.exists()

    result = migrate_models_to_shared(config, dry_run=False, confirmed=True)
    assert result["migrated"]
    assert result["legacy_removed"]
    assert not legacy_profile.parent.exists()
    assert models_dir(config) == shared.resolve()
    assert (shared / "bge-m3" / "embedding" / "model.bin").read_bytes() == b"embedding"
    assert (shared / "bge-m3" / "reranker" / "model.bin").read_bytes() == b"reranker"


def test_legacy_model_migration_reuses_identical_valid_shared_profile(monkeypatch, tmp_path):
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    first = project_config(tmp_path / "first")
    second = project_config(tmp_path / "second")
    seed_legacy_profile(first)
    second_profile = seed_legacy_profile(second)

    first_result = migrate_models_to_shared(first, dry_run=False, confirmed=True)
    shared_model = shared / "bge-m3" / "embedding" / "model.bin"
    shared_mtime = shared_model.stat().st_mtime_ns
    second_result = migrate_models_to_shared(second, dry_run=False, confirmed=True)

    assert first_result["migrated"]
    assert not first_result["reused_existing"]
    assert second_result["migrated"]
    assert second_result["reused_existing"]
    assert shared_model.stat().st_mtime_ns == shared_mtime
    assert not second_profile.parent.exists()


def test_migration_is_idempotent_and_reference_is_scoped_to_profile(monkeypatch, tmp_path):
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    config = project_config(tmp_path)
    seed_legacy_profile(config)
    migrate_models_to_shared(config, dry_run=False, confirmed=True)

    manifest_path = shared / "semantic_models.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unrelated_cache_metadata"] = "changed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    verification = verify_models(config)
    assert verification.embedding_cached
    assert verification.reranker_cached
    assert not any("profile manifest hash does not match" in warning for warning in verification.warnings)
    dry_run = migrate_models_to_shared(config, dry_run=True, confirmed=False)
    assert dry_run["eligible"]
    assert not dry_run["source_present"]
    result = migrate_models_to_shared(config, dry_run=False, confirmed=True)
    assert result["migrated"]
    assert result["reused_existing"]
    assert result["legacy_removed"]


def test_shared_cache_lock_refuses_concurrent_install(monkeypatch, tmp_path):
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    config = project_config(tmp_path)
    shared.mkdir(parents=True)
    (shared / ".install.lock").write_text("busy\n", encoding="utf-8")

    with pytest.raises(ModelError, match="locked"):
        install_model_profile(config, profile="local-hash")


def test_damaged_shared_profile_manifest_disables_provider(monkeypatch, tmp_path):
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    config = project_config(tmp_path)
    seed_legacy_profile(config)
    migrate_models_to_shared(config, dry_run=False, confirmed=True)
    (shared / "bge-m3" / "embedding" / "model.bin").write_bytes(b"damaged")

    result = verify_models(config)

    assert not result.provider_ready
    assert any("damaged or stale" in warning for warning in result.warnings)
