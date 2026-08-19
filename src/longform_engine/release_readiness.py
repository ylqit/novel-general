"""Release readiness checks for the public novel-general repository."""

from __future__ import annotations

import re
import subprocess
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any


SCHEMA = "release_readiness_v1"
EXPECTED_REMOTE = "https://github.com/ylqit/novel-general"


def check_release_readiness(
    repository: str | Path,
    *,
    tag: str = "",
    run_contracts: bool = True,
    check_remote: bool = False,
    allow_detached: bool = False,
    channel: str = "public",
) -> dict[str, Any]:
    if channel not in {"public", "rc"}:
        raise ValueError("release channel must be public or rc")
    root = Path(repository).expanduser().resolve()
    checks: list[dict[str, str]] = []

    version = read_project_version(root / "pyproject.toml")
    expected_tag = f"v{version}" if version else ""
    add_check(checks, "project_version", bool(version), f"Package version: {version or 'missing'}", "Set project.version in pyproject.toml.")
    runtime_version = read_runtime_version(root / "src" / "longform_engine" / "__init__.py")
    add_check(
        checks,
        "runtime_version",
        bool(version) and runtime_version == version,
        f"Runtime version: {runtime_version or 'missing'}",
        "Keep src/longform_engine/__init__.py aligned with pyproject.toml.",
    )
    if tag:
        add_check(
            checks,
            "release_tag",
            tag == expected_tag,
            f"Requested tag: {tag}; expected: {expected_tag or 'unknown'}",
            f"Use the exact tag {expected_tag}." if expected_tag else "Fix package version before tagging.",
        )
    elif channel == "public":
        add_warning(checks, "release_tag", f"No tag supplied; expected release tag is {expected_tag or 'unknown'}.", f"Re-run with --tag {expected_tag}." if expected_tag else "Fix package version first.")
    else:
        add_check(
            checks,
            "release_tag",
            True,
            f"RC channel intentionally has no release tag; future public tag: {expected_tag or 'unknown'}.",
            "",
        )

    add_check(checks, "license", (root / "LICENSE").is_file(), "MIT LICENSE file is present." if (root / "LICENSE").is_file() else "LICENSE is missing.", "Add the MIT LICENSE file.")
    readme = read_text(root / "README.md")
    if channel == "public":
        install_marker = f"git+https://github.com/ylqit/novel-general.git@{expected_tag}" if expected_tag else ""
        add_check(
            checks,
            "readme_install_url",
            bool(install_marker) and install_marker in readme,
            "README stable install URL matches the package version." if install_marker in readme else "README stable install URL is missing or stale.",
            f"Use {install_marker} in README." if install_marker else "Fix package version first.",
        )
    else:
        stable_version = read_readme_stable_version(readme)
        stable_marker = (
            f"git+https://github.com/ylqit/novel-general.git@v{stable_version}"
            if stable_version
            else ""
        )
        add_check(
            checks,
            "readme_rc_channel",
            bool(stable_version)
            and stable_version != version
            and stable_marker in readme
            and "Release Candidate" in readme,
            f"README stable channel: v{stable_version or 'missing'}; source RC: v{version or 'missing'}.",
            "Keep stable install commands on the published tag and label the source version as Release Candidate.",
        )
        installed_version = installed_package_version()
        if installed_version == version:
            add_check(checks, "installed_version", True, f"Installed package matches source RC: {version}.", "")
        else:
            add_warning(
                checks,
                "installed_version",
                f"Installed package {installed_version or 'not found'} does not match source RC {version or 'unknown'}.",
                "Build and install the reviewed RC artifact before final package validation.",
            )
    add_check(checks, "ci_workflow", (root / ".github" / "workflows" / "ci.yml").is_file(), "CI workflow is present.", "Add .github/workflows/ci.yml.")
    add_check(checks, "release_workflow", (root / ".github" / "workflows" / "release.yml").is_file(), "Release workflow is present.", "Add .github/workflows/release.yml.")

    git_dir_ok = run_git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
    add_check(checks, "git_repository", git_dir_ok, "Git repository detected." if git_dir_ok else "Not a Git repository.", "Initialize or clone the public repository.")
    if git_dir_ok:
        head = run_git(root, "rev-parse", "--verify", "HEAD")
        add_check(checks, "git_commit", head.returncode == 0, "Repository has at least one commit." if head.returncode == 0 else "Repository has no commits.", "Create and review the initial commit before release.")
        status = run_git(root, "status", "--porcelain", "--untracked-files=all")
        clean = status.returncode == 0 and not status.stdout.strip()
        if channel == "rc" and not clean:
            add_warning(
                checks,
                "git_clean",
                "RC worktree has tracked or untracked changes; public release remains blocked.",
                "Review and commit the candidate before switching to --channel public.",
            )
        else:
            add_check(
                checks,
                "git_clean",
                clean,
                "Git worktree is clean." if clean else "Git worktree has tracked or untracked changes.",
                "Review, commit, or intentionally remove pending files before release.",
            )
        remote = run_git(root, "remote", "get-url", "origin")
        normalized_remote = normalize_remote_url(remote.stdout.strip()) if remote.returncode == 0 else ""
        add_check(
            checks,
            "origin_remote",
            normalized_remote == EXPECTED_REMOTE,
            f"origin: {normalized_remote or 'missing'}",
            f"Configure origin as {EXPECTED_REMOTE}.git after reviewing repository ownership.",
        )
        branch = run_git(root, "branch", "--show-current").stdout.strip()
        branch_ok = (
            branch == "master"
            or (channel == "rc" and branch.startswith("codex/"))
            or (not branch and (bool(tag) or allow_detached))
        )
        add_check(checks, "git_ref", branch_ok, f"Current branch/ref: {branch or tag or 'detached'}", "Run release readiness from master or the matching release tag checkout.")
        if tag and head.returncode == 0:
            exact_tag = run_git(root, "describe", "--tags", "--exact-match").stdout.strip()
            add_check(checks, "head_tag", exact_tag == tag, f"HEAD exact tag: {exact_tag or 'none'}", f"Ensure HEAD is exactly tagged {tag}.")
        elif channel == "rc" and head.returncode == 0:
            exact_tag = run_git(root, "describe", "--tags", "--exact-match").stdout.strip()
            add_check(
                checks,
                "head_untagged",
                not exact_tag,
                f"HEAD exact tag: {exact_tag or 'none'}.",
                "Use an untagged RC commit until public release is explicitly approved.",
            )
        if check_remote and normalized_remote == EXPECTED_REMOTE:
            remote_refs = run_git(root, "ls-remote", "--heads", "--tags", "origin", timeout=30)
            remote_ok = remote_refs.returncode == 0
            add_check(checks, "remote_reachable", remote_ok, "Public origin is reachable." if remote_ok else "Public origin could not be queried.", "Check network access and repository visibility.")
            if remote_ok:
                refs = remote_refs.stdout
                add_check(checks, "remote_master", "refs/heads/master" in refs, "Remote master exists.", "Push the reviewed master branch before release.")
                if tag:
                    add_check(checks, "remote_tag", f"refs/tags/{tag}" in refs, f"Remote tag {tag} exists." if f"refs/tags/{tag}" in refs else f"Remote tag {tag} is missing.", f"Push {tag} only after explicit release approval.")

    if run_contracts:
        for check_id, command in (
            ("skill_reference_sync", [sys.executable, "scripts/sync_skill_references.py", "--check"]),
            ("resource_manifest", [sys.executable, "scripts/build_resource_manifest.py", "--check"]),
            ("skill_validation", [sys.executable, "scripts/validate_skills.py"]),
            ("markdown_links", [sys.executable, "scripts/check_markdown_links.py"]),
            ("release_guards", [sys.executable, "scripts/release_surface_guards.py"]),
        ):
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120, check=False)
            detail = last_output_line(result) or "completed"
            add_check(checks, check_id, result.returncode == 0, detail, f"Run {' '.join(command)} and fix the reported contract failure.")

    failures = [item for item in checks if item["status"] == "fail"]
    warnings = [item for item in checks if item["status"] == "warn"]
    return {
        "schema": SCHEMA,
        "channel": channel,
        "ok": not failures,
        "repository": str(root),
        "version": version,
        "expected_tag": expected_tag,
        "requested_tag": tag,
        "expected_remote": EXPECTED_REMOTE,
        "checks": checks,
        "summary": {"passed": sum(item["status"] == "pass" for item in checks), "warnings": len(warnings), "failures": len(failures)},
        "next_command": next_command(failures, warnings, expected_tag),
    }


def render_release_readiness(payload: dict[str, Any]) -> str:
    lines = [
        "Release readiness: READY" if payload["ok"] else "Release readiness: BLOCKED",
        f"Channel: {payload.get('channel') or 'public'}",
        f"Version: {payload.get('version') or 'unknown'}",
        f"Expected tag: {payload.get('expected_tag') or 'unknown'}",
    ]
    for item in payload.get("checks", []):
        lines.append(f"[{item['status'].upper()}] {item['id']}: {item['detail']}")
        if item["status"] != "pass" and item.get("next_command"):
            lines.append(f"  Next: {item['next_command']}")
    lines.append(f"Next command: {payload.get('next_command')}")
    return "\n".join(lines)


def read_project_version(path: Path) -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', read_text(path), flags=re.MULTILINE)
    return match.group(1) if match else ""


def read_runtime_version(path: Path) -> str:
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', read_text(path), flags=re.MULTILINE)
    return match.group(1) if match else ""


def normalize_remote_url(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.removeprefix("git@github.com:")
    if normalized.startswith("ssh://git@github.com/"):
        normalized = "https://github.com/" + normalized.removeprefix("ssh://git@github.com/")
    return normalized.removesuffix("/").removesuffix(".git")


def read_readme_stable_version(readme: str) -> str:
    match = re.search(r"当前公开稳定版为\s*`?v(\d+\.\d+\.\d+)`?", readme)
    return match.group(1) if match else ""


def installed_package_version() -> str:
    try:
        return importlib_metadata.version("longform-novel-engine")
    except importlib_metadata.PackageNotFoundError:
        return ""


def run_git(root: Path, *args: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


def add_check(checks: list[dict[str, str]], check_id: str, ok: bool, detail: str, command: str) -> None:
    checks.append({"id": check_id, "status": "pass" if ok else "fail", "detail": detail, "next_command": "" if ok else command})


def add_warning(checks: list[dict[str, str]], check_id: str, detail: str, command: str) -> None:
    checks.append({"id": check_id, "status": "warn", "detail": detail, "next_command": command})


def last_output_line(result: subprocess.CompletedProcess[str]) -> str:
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return output.splitlines()[-1] if output else ""


def next_command(failures: list[dict[str, str]], warnings: list[dict[str, str]], expected_tag: str) -> str:
    if failures:
        return failures[0]["next_command"]
    if warnings:
        return warnings[0]["next_command"]
    return f"Await explicit approval before pushing {expected_tag} and creating the GitHub Release."


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
