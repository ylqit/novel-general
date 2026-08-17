#!/usr/bin/env python
"""Validate distributable Skills, public installation docs, and bundled assets."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from longform_engine.resources import RESOURCE_HASH_POLICY, resource_integrity_bytes  # noqa: E402


SKILL_SPECS = {
    "longform-novel-codex": {"platform": "Codex", "forbidden_platform": "Claude"},
    "longform-novel-claude": {"platform": "Claude Code", "forbidden_platform": "Codex"},
}
REFERENCES = (
    "artifact_reporting.md",
    "command_protocol.md",
    "creative_operator_protocol.md",
    "iron_laws.md",
    "workflow_mapping.md",
)
PUBLIC_URL = "https://github.com/ylqit/novel-general"
ABSOLUTE_WINDOWS_PATH = re.compile(r"(?i)(?:^|[\s`'\"])[a-z]:[\\/]")


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
    if not match:
        raise ValueError("pyproject.toml is missing project.version")
    return match.group(1)


def public_install_version() -> str:
    channel_path = ROOT / "config" / "release-channel.json"
    payload = json.loads(channel_path.read_text(encoding="utf-8"))
    version = project_version()
    if payload.get("schema") != "release_channel_v1":
        raise ValueError("config/release-channel.json has the wrong schema")
    if payload.get("development_version") != version:
        raise ValueError("config/release-channel.json does not match project.version")
    if payload.get("status") == "stable":
        return version
    stable = str(payload.get("public_stable_version") or "").strip()
    if not stable:
        raise ValueError("config/release-channel.json is missing public_stable_version")
    return stable


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    try:
        raw, body = text[4:].split("\n---\n", 1)
    except ValueError:
        return {}, text
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values, body


def validate_skill(name: str, platform: str, forbidden_platform: str) -> list[str]:
    errors: list[str] = []
    skill_dir = ROOT / name
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        return [f"{name}: missing SKILL.md"]

    text = skill_file.read_text(encoding="utf-8")
    frontmatter, body = parse_frontmatter(text)
    description = frontmatter.get("description", "")
    if frontmatter.get("name") != name:
        errors.append(f"{name}: frontmatter name mismatch")
    if set(frontmatter) != {"name", "description"}:
        errors.append(f"{name}: frontmatter must contain only name and description")
    prefix = description[:250]
    for term in (platform, "中文长篇", "/工程下一步", "production next"):
        if term.lower() not in prefix.lower():
            errors.append(f"{name}: description prefix missing {term!r}")
    if forbidden_platform.lower() in description.lower():
        errors.append(f"{name}: description conflicts with {forbidden_platform} platform")

    word_count = len(re.findall(r"\S+", body))
    if word_count > 500:
        errors.append(f"{name}: SKILL.md body exceeds 500 words ({word_count})")
    for forbidden in ("../shared", ".venv", "api_provider"):
        if forbidden.lower() in text.lower():
            errors.append(f"{name}: forbidden distributable text {forbidden!r}")
    if ABSOLUTE_WINDOWS_PATH.search(text):
        errors.append(f"{name}: contains a user-machine absolute path")

    for reference in REFERENCES:
        path = skill_dir / "references" / reference
        if not path.is_file():
            errors.append(f"{name}: missing references/{reference}")
        if f"references/{reference}" not in body:
            errors.append(f"{name}: SKILL.md does not link references/{reference}")
        shared = ROOT / "shared" / reference
        if path.is_file() and shared.is_file() and path.read_bytes() != shared.read_bytes():
            errors.append(f"{name}: references/{reference} drifted from shared source")

    for term in (
        "agent_skill",
        "production next",
        "agent-task brief",
        "io.inputs",
        "io.output.path",
        "io.output.protocol",
        "commands.failure",
        "explicit apply",
        "chapter finalize",
        "40_manuscript/final/",
        "60_rag/",
        "30_state/story_graph.json",
        "30_state/tcs/",
        "70_runtime/db/",
        "10_bible/",
        "20_outline/",
        "research_canon.jsonl",
        "fanfiction canon-task",
        "fanfiction design-task",
        "Humanizer v4",
        "character_expression_packet_v1",
        "reader_payoff_review",
        "rights status",
        "source prose",
    ):
        if term.lower() not in text.lower():
            errors.append(f"{name}: missing workflow/boundary term {term!r}")

    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if not openai_yaml.is_file() or "interface:" not in openai_yaml.read_text(encoding="utf-8"):
        errors.append(f"{name}: missing valid agents/openai.yaml")
    return errors


def validate_readme() -> list[str]:
    errors: list[str] = []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    version = project_version()
    stable_version = public_install_version()
    required = (
        "longform-novel-engine = Python engine + Codex skill + Claude Code skill",
        PUBLIC_URL,
        f"git+https://github.com/ylqit/novel-general.git@v{stable_version}",
        version,
        "longform-novel-engine[semantic]",
        "PIPX_BIN_DIR",
        "longform-engine skills install --tool all",
        "longform-engine skills update --tool all",
        "longform-engine skills uninstall --tool all --yes",
        "longform-engine doctor --tool all",
        "longform-engine release check --repository . --check-remote",
        "longform-engine benchmark record",
        "longform-engine benchmark compare",
        "/工程下一步",
        "/工程工单",
        "production next",
        "agent-task brief",
        "40_manuscript/final/",
        "60_rag/",
        "30_state/story_graph.json",
        "30_state/tcs/",
        "70_runtime/db/",
        "10_bible/",
        "20_outline/",
        "research_canon.jsonl",
        "不宣称文学质量优于",
        "creation.mode",
        "fanfiction canon-task",
        "fanfiction design-task",
        "publication report",
        "publication export",
        "Humanizer v3",
        "Humanizer v4",
        "design_document_v1",
        "canonical_delta_v1",
        "content_characters_v1",
        "character audit-task",
        "reader_payoff_review",
        "rights_status",
        "commercial_intent",
        "只生成提示",
        "多个 JSON 字段",
        "CHINESE_WEBNOVEL_AND_FANFICTION_QUALITY_CHECKLIST.md",
    )
    for term in required:
        if term.lower() not in readme.lower():
            errors.append(f"README.md: missing {term!r}")
    for forbidden in ("<owner>", "README.zh-CN.md", "curl | bash", "clone 到临时目录"):
        if forbidden.lower() in readme.lower():
            errors.append(f"README.md: forbidden public-install text {forbidden!r}")
    if len(re.findall(r"(?m)^## 安装\s*$", readme)) != 1:
        errors.append("README.md: public install must use exactly one '## 安装' section")
    if (ROOT / "README.zh-CN.md").exists():
        errors.append("README.zh-CN.md must not be added; Chinese public content belongs in README.md")
    return errors


def validate_config_surface() -> list[str]:
    errors: list[str] = []
    for relative in (Path("config/default.engine.yaml"), Path("templates/qidian-longform/project.yaml")):
        text = (ROOT / relative).read_text(encoding="utf-8")
        for forbidden in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "default_provider", "api_provider"):
            if forbidden in text:
                errors.append(f"{relative.as_posix()}: public config contains {forbidden}")
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for term in (f'version = "{project_version()}"', "hatchling.build", PUBLIC_URL, "force-include", "LICENSE"):
        if term not in pyproject:
            errors.append(f"pyproject.toml: missing {term!r}")
    return errors


def validate_resource_manifest() -> list[str]:
    errors: list[str] = []
    path = ROOT / "resource-manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"resource-manifest.json: {exc}"]
    if payload.get("schema") != "longform_resource_manifest_v1":
        errors.append("resource-manifest.json: wrong schema")
    if payload.get("engine_version") != project_version():
        errors.append("resource-manifest.json: wrong engine version")
    if payload.get("hash_policy") != RESOURCE_HASH_POLICY:
        errors.append("resource-manifest.json: wrong hash policy")
    listed: set[str] = set()
    for entry in payload.get("assets", []):
        if not isinstance(entry, dict):
            errors.append("resource-manifest.json: invalid asset entry")
            continue
        relative = str(entry.get("path", ""))
        listed.add(relative)
        asset = ROOT / relative
        if not asset.is_file():
            errors.append(f"resource-manifest.json: missing asset {relative}")
        elif sha256(resource_integrity_bytes(asset)).hexdigest() != entry.get("sha256"):
            errors.append(f"resource-manifest.json: stale hash {relative}")
    for required in (
        "config/default.engine.yaml",
        "config/agent_roles/registry.json",
        "templates/qidian-longform/project.yaml",
        "longform-novel-codex/SKILL.md",
        "longform-novel-claude/SKILL.md",
    ):
        if required not in listed:
            errors.append(f"resource-manifest.json: missing listing {required}")
    registry_path = ROOT / "config" / "agent_roles" / "registry.json"
    if registry_path.is_file():
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        for role in registry.get("roles", []):
            relative = str(role.get("prompt_path") or "") if isinstance(role, dict) else ""
            if relative and relative not in listed:
                errors.append(f"resource-manifest.json: missing role Prompt listing {relative}")
    return errors


def validate_agent_roles() -> list[str]:
    errors: list[str] = []
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from longform_engine.agent_tasks import TASK_CONTRACTS
        from longform_engine.roles import validate_role_task_coverage

        registry = validate_role_task_coverage(set(TASK_CONTRACTS), root=ROOT)
    except (ImportError, ValueError) as exc:
        return [f"agent role registry: {exc}"]
    finally:
        if sys.path and sys.path[0] == str(ROOT / "src"):
            sys.path.pop(0)
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if '"config" = "longform_engine/resources/config"' not in pyproject:
        errors.append("pyproject.toml: agent role resources are not force-included in the wheel")
    for skill in SKILL_SPECS:
        text = (ROOT / skill / "SKILL.md").read_text(encoding="utf-8")
        if "agent-task brief" not in text:
            errors.append(f"{skill}: host Skill does not use the shared role-aware agent-task brief")
    if len(registry.roles) != len(set(registry.roles)):
        errors.append("agent role registry: duplicate role IDs")
    return errors


def main() -> int:
    errors: list[str] = []
    for name, spec in SKILL_SPECS.items():
        errors.extend(validate_skill(name, spec["platform"], spec["forbidden_platform"]))
    errors.extend(validate_readme())
    errors.extend(validate_config_surface())
    errors.extend(validate_agent_roles())
    errors.extend(validate_resource_manifest())
    if errors:
        print("Skill validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("OK: skill packages validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
