from pathlib import Path
import subprocess

from longform_engine.release_readiness import check_release_readiness, normalize_remote_url


def git(root: Path, *args: str) -> None:
    subprocess.check_call(["git", *args], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def seed_release_repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    (root / "src" / "longform_engine").mkdir(parents=True)
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
    (root / "src" / "longform_engine" / "__init__.py").write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    (root / "README.md").write_text(
        "git+https://github.com/ylqit/novel-general.git@v0.2.0\n",
        encoding="utf-8",
    )
    (root / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (root / ".github" / "workflows" / "ci.yml").write_text("name: CI\n", encoding="utf-8")
    (root / ".github" / "workflows" / "release.yml").write_text("name: Release\n", encoding="utf-8")
    git(root, "init", "-b", "master")
    git(root, "remote", "add", "origin", "git@github.com:ylqit/novel-general.git")
    git(root, "add", ".")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "seed")
    return root


def test_release_readiness_accepts_reviewed_clean_repository(tmp_path):
    root = seed_release_repository(tmp_path)

    payload = check_release_readiness(root, run_contracts=False)

    assert payload["schema"] == "release_readiness_v1"
    assert payload["ok"]
    assert payload["expected_tag"] == "v0.2.0"
    assert payload["summary"]["failures"] == 0
    assert payload["summary"]["warnings"] == 1


def test_release_readiness_blocks_dirty_repository_and_wrong_tag(tmp_path):
    root = seed_release_repository(tmp_path)
    (root / "pending.txt").write_text("pending\n", encoding="utf-8")

    payload = check_release_readiness(root, tag="v9.9.9", run_contracts=False)
    failures = {item["id"] for item in payload["checks"] if item["status"] == "fail"}

    assert not payload["ok"]
    assert {"release_tag", "git_clean", "head_tag"}.issubset(failures)


def test_remote_url_normalization_supports_https_and_ssh():
    assert normalize_remote_url("https://github.com/ylqit/novel-general.git") == "https://github.com/ylqit/novel-general"
    assert normalize_remote_url("git@github.com:ylqit/novel-general.git") == "https://github.com/ylqit/novel-general"
    assert normalize_remote_url("ssh://git@github.com/ylqit/novel-general.git") == "https://github.com/ylqit/novel-general"
