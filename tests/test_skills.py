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
        assert "io.inputs" in text
        assert "io.output.path" in text
        assert "io.output.protocol" in text
        assert "commands.failure" in text
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


def test_readme_describes_the_current_product_and_web_entry():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lower = readme.lower()

    for term in (
        "本地生产控制面",
        '".[semantic]"',
        "longform-engine skills install --tool codex",
        "longform-engine doctor --tool codex",
        "studio serve --workspace",
        "studio shortcut-install",
        "原创小说",
        "同人小说",
        "production next",
        "chapter_contract_v5",
        "chapter_story_brief_basis_v4",
        "chapter_story_brief_v5",
        "chapter_writing_task_v8",
        "human_author_revision_v4",
        "human_story_review_v7",
        "canonical_delta_v1",
        "chapter close",
        "recovery status",
        "web_studio.md",
    ):
        assert term.lower() in lower
    for forbidden in (
        "<owner>",
        "README.zh-CN.md",
        "clone 到临时目录",
        "curl | bash",
        "--compare-market",
        "longform-engine benchmark",
        "literary_evidence_ready",
        "release_checklist",
        "quality_benchmark_runbook",
    ):
        assert forbidden.lower() not in lower


def test_current_timeless_management_docs_are_linked():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    install = (ROOT / "docs" / "SKILL_INSTALLATION.md").read_text(encoding="utf-8")
    history = (ROOT / "docs" / "RELEASE_HISTORY.md").read_text(encoding="utf-8")

    for document in (
        "ARCHITECTURE.md",
        "STORAGE_MODEL.md",
        "CONFIGURATION.md",
        "OPERATOR_GUIDE.md",
        "WEB_STUDIO.md",
        "RELEASE_HISTORY.md",
    ):
        assert document in readme
    for document in ("ARCHITECTURE.md", "STORAGE_MODEL.md", "CONFIGURATION.md", "OPERATOR_GUIDE.md"):
        assert document in agents
        assert document in install
    for public_text in (readme, agents, install, history):
        assert "RELEASE_CHECKLIST" not in public_text
        assert "QUALITY_BENCHMARK_RUNBOOK" not in public_text
        assert "V0_13_FANFICTION_ARCHITECTURE" not in public_text


def test_shared_protocols_keep_chapter_and_editorial_contracts():
    creative = (ROOT / "shared" / "creative_operator_protocol.md").read_text(encoding="utf-8").lower()
    workflow = (ROOT / "shared" / "workflow_mapping.md").read_text(encoding="utf-8").lower()
    command = (ROOT / "shared" / "command_protocol.md").read_text(encoding="utf-8").lower()
    combined = "\n".join((creative, workflow, command))

    for term in (
        "/工程续章",
        "pre-write guide",
        "human_chapter_intent_v3",
        "chapter_coedit_session_v2",
        "chapter_story_brief_v5",
        "chapter_story_brief_basis_v4",
        "protected outcomes",
        "required production closed loop",
        "human-review-task",
        "evidence-bound v7 acceptance",
        "human_author_revision_v4",
        "planning_chief_editor",
        "anti_template_editor",
        "scene_prose_editor",
        "reader_experience_editor",
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
