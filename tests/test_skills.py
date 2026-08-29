import json
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


def test_readme_is_public_pipx_skill_package_homepage():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lower = readme.lower()
    release_channel = json.loads((ROOT / "config" / "release-channel.json").read_text(encoding="utf-8"))
    public_version = release_channel["public_stable_version"]

    for term in (
        "longform-novel-engine = Python engine + Codex skill + Claude Code skill",
        "https://github.com/ylqit/novel-general",
        f"git+https://github.com/ylqit/novel-general.git@v{public_version}",
        "longform-novel-engine[semantic]",
        "pipx",
        "longform-engine skills install --tool codex",
        "longform-engine doctor --tool codex",
        "production next",
        "agent-task brief",
        "chapter_contract_v5",
        "chapter_story_brief_basis_v3",
        "chapter_story_brief_v5",
        "chapter_writing_task_v7",
        "human_author_revision_v4",
        "human_story_review_v7",
        "10_bible/",
        "20_outline/",
        "40_manuscript/final/",
        "literary_evidence_ready=false",
    ):
        assert term.lower() in lower
    for forbidden in ("<owner>", "README.zh-CN.md", "clone 到临时目录", "curl | bash"):
        assert forbidden.lower() not in lower
    assert readme.count("\n## 安装稳定版\n") == 1
    assert 280 <= len(readme.splitlines()) <= 340


def test_current_release_checklist_and_management_docs_are_linked():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    install = (ROOT / "docs" / "SKILL_INSTALLATION.md").read_text(encoding="utf-8")
    history = (ROOT / "docs" / "RELEASE_HISTORY.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "V0_13_0_RELEASE_CHECKLIST.md").read_text(encoding="utf-8")

    for document in (
        "ARCHITECTURE.md",
        "STORAGE_MODEL.md",
        "OPERATOR_GUIDE.md",
        "RELEASE_HISTORY.md",
        "V0_13_FANFICTION_ARCHITECTURE.md",
        "V0_13_0_RELEASE_CHECKLIST.md",
    ):
        assert document in readme
    assert "OPERATOR_GUIDE.md" in agents
    assert "V0_13_0_RELEASE_CHECKLIST.md" in agents
    assert "OPERATOR_GUIDE.md" in install
    for historical in (
        "V0_4_4_RELEASE_CHECKLIST.md",
        "V0_5_0_RELEASE_CHECKLIST.md",
        "V0_6_0_RELEASE_CHECKLIST.md",
        "V0_7_0_RELEASE_CHECKLIST.md",
    ):
        assert historical in history
    assert "V0_4_4_RELEASE_CHECKLIST.md" in install
    assert "V0_5_0_RELEASE_CHECKLIST.md" in install
    assert "V0_6_0_RELEASE_CHECKLIST.md" in install
    assert "V0_7_0_RELEASE_CHECKLIST.md" in install
    assert "V0_13_0_RELEASE_CHECKLIST.md" in install
    assert "协议收口" in checklist
    assert "semantic_document_v1" in checklist
    for section in (
        "协议收口",
        "版本与活动文档",
        "本地发布验证",
        "提交与远程发布",
        "本机同步",
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
        "human_chapter_intent_v2",
        "chapter_coedit_session_v2",
        "chapter_story_brief_v5",
        "chapter_story_brief_basis_v3",
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
