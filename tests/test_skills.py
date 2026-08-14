import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("longform-novel-codex", "longform-novel-claude")
REFERENCES = (
    "artifact_reporting.md",
    "command_protocol.md",
    "creative_operator_protocol.md",
    "iron_laws.md",
    "workflow_mapping.md",
)


def test_skill_packages_validate():
    result = subprocess.run(
        [sys.executable, "scripts/validate_skills.py"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "OK: skill packages validated" in result.stdout


def test_skills_are_self_contained_compact_and_no_key():
    for skill in SKILLS:
        text = (ROOT / skill / "SKILL.md").read_text(encoding="utf-8")
        body = text.split("\n---\n", 1)[1]
        assert len(body.split()) <= 500
        assert "agent_skill" in text
        assert "no-key" in text.lower()
        assert "/工程下一步" in text
        assert "production next" in text
        assert "agent-task brief" in text
        assert "allowed_output_paths" in text
        assert "output_schema" in text
        assert "failure_next_command" in text
        assert "../shared" not in text
        assert ".venv" not in text
        for reference in REFERENCES:
            installed = ROOT / skill / "references" / reference
            shared = ROOT / "shared" / reference
            assert installed.read_bytes() == shared.read_bytes()
            assert f"references/{reference}" in text


def test_platform_descriptions_are_mutually_exclusive():
    codex = (ROOT / "longform-novel-codex" / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    claude = (ROOT / "longform-novel-claude" / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]

    assert "Codex App / Codex CLI" in codex
    assert "Claude" not in codex
    assert "Claude Code" in claude
    assert "Codex" not in claude
    for description in (codex, claude):
        assert "中文长篇" in description[:300]
        assert "/工程下一步" in description[:300]
        assert "production next" in description[:300]


def test_readme_is_public_pipx_skill_package_homepage():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lower = readme.lower()

    for term in (
        "longform-novel-engine = Python engine + Codex skill + Claude Code skill",
        "https://github.com/ylqit/novel-general",
        "git+https://github.com/ylqit/novel-general.git@v0.3.2",
        "longform-novel-engine[semantic]",
        "pipx",
        "PIPX_BIN_DIR",
        "longform-engine skills install --tool all",
        "longform-engine doctor --tool all",
        "longform-engine release check --repository . --check-remote",
        "longform-engine benchmark record",
        "longform-engine benchmark compare",
        "longform-engine skills update --tool all",
        "longform-engine skills uninstall --tool all --yes",
        "/工程下一步",
        "/工程工单",
        "50_workbench/agent_drafts/chNNN.codex.md",
        "50_workbench/agent_drafts/chNNN.claude.md",
        "10_bible/",
        "20_outline/",
        "40_manuscript/final/",
        "60_rag/",
        "70_runtime/db/",
        "不宣称文学质量优于",
    ):
        assert term.lower() in lower
    for forbidden in ("<owner>", "README.zh-CN.md", "clone 到临时目录", "curl | bash"):
        assert forbidden.lower() not in lower
    assert readme.count("\n## 安装\n") == 1


def test_public_distribution_checklist_and_workflow_docs_are_linked():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    install = (ROOT / "docs" / "SKILL_INSTALLATION.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "PUBLIC_DISTRIBUTION_PRODUCTIZATION_CHECKLIST.md").read_text(encoding="utf-8")

    for document in (
        "AGENT_APP_WORKFLOW_PRODUCTIZATION.md",
        "PUBLIC_DISTRIBUTION_PRODUCTIZATION_CHECKLIST.md",
    ):
        assert document in readme
    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION.md" in agents
    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md" in agents
    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION.md" in install
    for section in (
        "Repository And License",
        "Wheel Resources",
        "Skill Lifecycle",
        "AgentTaskManifest v2",
        "Quality Evidence",
        "CI And Release",
        "Definition Of Done",
    ):
        assert section in checklist


def test_shared_protocols_keep_chapter_and_editorial_contracts():
    creative = (ROOT / "shared" / "creative_operator_protocol.md").read_text(encoding="utf-8").lower()
    workflow = (ROOT / "shared" / "workflow_mapping.md").read_text(encoding="utf-8").lower()
    command = (ROOT / "shared" / "command_protocol.md").read_text(encoding="utf-8").lower()
    combined = "\n".join((creative, workflow, command))

    for term in (
        "/工程续章",
        "pre-write guide",
        "pacing precheck",
        "tail-hook declaration",
        "forbidden reveal confirmation",
        "five-step closed loop",
        "planning_chief_editor",
        "anti_ai_editor",
        "serial_verifier",
        "need_human_reasons",
        "editorial need-human",
        "production loop",
    ):
        assert term in combined


def test_skill_creator_quick_validate_compatible():
    default_validator = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
    validator = Path(os.environ.get("SKILL_CREATOR_VALIDATE", str(default_validator)))
    if not validator.exists():
        return

    for skill in SKILLS:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [sys.executable, str(validator), str(ROOT / skill)],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
