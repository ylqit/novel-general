import json
from pathlib import Path

import pytest

from longform_engine.config import load_project_config
from longform_engine.models import (
    ModelError,
    cache_status_payload,
    install_model_profile,
    models_dir,
    shared_model_cache_root,
)
from longform_engine.storage import init_project


def project_config(tmp_path: Path):
    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    return load_project_config(project.project_config)


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


def test_shared_cache_lock_refuses_concurrent_install(monkeypatch, tmp_path):
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    config = project_config(tmp_path)
    shared.mkdir(parents=True)
    (shared / ".install.lock").write_text("busy\n", encoding="utf-8")

    with pytest.raises(ModelError, match="locked"):
        install_model_profile(config, profile="local-hash")


def test_cached_models_without_runtime_do_not_attempt_another_download(monkeypatch, tmp_path):
    from longform_engine.models import pipeline

    shared = tmp_path / "shared-models"
    monkeypatch.setenv("LONGFORM_MODEL_CACHE", str(shared))
    config = project_config(tmp_path)
    config.data["semantic"].update(profile="bge-m3", require_real_model=True,
        allow_fallback=False, allow_network_download=True)
    for kind in ("embedding", "reranker"):
        folder = shared / "bge-m3" / kind
        folder.mkdir(parents=True)
        (folder / "cached.fixture").write_text("isolated cached-file fixture", encoding="utf-8")
    lock = shared / ".install.lock"
    lock.write_text("existing operation", encoding="utf-8")
    monkeypatch.setattr(pipeline, "can_load_sentence_transformer", lambda path: False)
    calls = []
    monkeypatch.setattr(pipeline, "install_model_profile", lambda *args, **kwargs: calls.append(kwargs))

    status = pipeline.verify_models(config)
    assert status.status == "runtime_unavailable"
    assert not status.can_auto_download
    with pytest.raises(ModelError, match="local runtime is unavailable"):
        pipeline.ensure_models_ready(config)
    assert calls == []
    assert lock.read_text(encoding="utf-8") == "existing operation"
