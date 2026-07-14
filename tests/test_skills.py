import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def test_agent_skill_docs_define_no_api_key_boundaries():
    expected_terms = (
        "agent_skill",
        "does not require an extra LLM API key",
        "50_workbench/agent_drafts",
        "draft submit",
        "chapter finalize",
    )
    for skill in ("longform-novel-codex", "longform-novel-claude"):
        text = (ROOT / skill / "SKILL.md").read_text(encoding="utf-8").lower()
        for term in expected_terms:
            assert term.lower() in text

    iron_laws = (ROOT / "shared" / "iron_laws.md").read_text(encoding="utf-8").lower()
    for term in (
        "40_manuscript/final/",
        "60_rag/",
        "30_state/story_graph.json",
        "70_runtime/db/",
        "passed=false",
    ):
        assert term.lower() in iron_laws


def test_capability_gap_checklist_is_linked_and_guarded():
    checklist = (ROOT / "docs" / "NOVEL_SKILL_CAPABILITY_GAP_CHECKLIST.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    assert "NOVEL_SKILL_CAPABILITY_GAP_CHECKLIST.md" in readme
    assert "NOVEL_SKILL_CAPABILITY_GAP_CHECKLIST.md" in agents

    for term in (
        "auto-write plan",
        "creative expand-task",
        "style_profiles",
        "humanize-task",
        "conflict_thrill",
        "forbidden_reveals",
        "editorial batch-review",
        "planning_chief_editor",
        "conditional_pass_streak",
        "need_human_reasons",
        "batch_reports",
        "draft submit -> gate-check -> chapter finalize",
        "50_workbench/agent_drafts/",
        "40_manuscript/final/",
        "60_rag/",
        "30_state/story_graph.json",
        "70_runtime/db/",
    ):
        assert term in checklist


def test_readme_is_creator_facing_and_chinese_command_first():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    readme_lower = readme.lower()

    for heading in (
        "## 项目定位",
        "## 核心亮点",
        "## 百万字长篇设计目标",
        "## 长线一致性方案",
        "## 去 AI 味与审稿门禁",
        "## Agent 协作写作流程",
        "## 快速开始",
        "## 新书创建向导",
        "## 中文工程指令",
        "## 项目目录与写入边界",
        "## Semantic RAG 默认能力",
        "## 安装与环境准备",
        "## FAQ",
    ):
        assert heading in readme

    for forbidden_heading in (
        "## 常用" + "命令",
        "## GitHub 发布" + "边界",
        "## 公开" + "入口",
        "## 开发" + "与测试",
    ):
        assert forbidden_heading not in readme

    for term in (
        "codex app",
        "codex cli",
        "claudecode",
        "no api key",
        "/工程开书",
        "/工程下一步",
        "/工程工单",
        "/工程生产状态",
        "/工程生产看板",
        "/工程推进",
        "/工程续章",
        "/工程提交稿",
        "/工程定稿",
        "百万字中文长篇小说",
        "门禁闭环",
        "story graph",
        "character memory",
        "outline anchors",
        "research canon",
        "50_workbench/writing_tasks/ch001.md",
        "50_workbench/agent_drafts/ch001.codex.md",
        "50_workbench/agent_drafts/ch001.claude.md",
        "40_manuscript/final/",
        "60_rag/",
        "30_state/",
        "70_runtime/",
        "baai/bge-m3",
        "baai/bge-reranker-v2-m3",
    ):
        assert term.lower() in readme_lower

    quick_start = readme.split("## 快速开始", 1)[1].split("## 新书创建向导", 1)[0]
    assert "longform-engine " not in quick_start


def test_app_cli_manuals_define_agent_skill_workflows():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()
    for term in (
        "agent_skill",
        "50_workbench/agent_drafts/chnnn.codex.md",
        "50_workbench/agent_drafts/chnnn.claude.md",
        "40_manuscript/final/",
        "60_rag/",
        "30_state/story_graph.json",
        "70_runtime/db/",
        "draft submit",
        "chapter finalize",
    ):
        assert term in agents

    command_protocol = (ROOT / "shared" / "command_protocol.md").read_text(encoding="utf-8").lower()
    for term in (
        "中文工程指令协议",
        "codex app",
        "codex cli",
        "claudecode",
        "/工程开书",
        "交互式创建向导",
        "50_workbench/agent_drafts/chnnn.codex.md",
        "50_workbench/agent_drafts/chnnn.claude.md",
        "draft submit",
        "chapter finalize",
        "/工程续章",
        "写前引导",
        "用户偏好",
        "自动兜底",
        "节奏预检",
        "章末钩子声明",
        "禁揭露确认",
        "五步闭环",
        "production next",
        "agent-task brief",
        "production loop",
    ):
        assert term in command_protocol


def test_agent_app_workflow_productization_docs_are_linked_and_guarded():
    product = (ROOT / "docs" / "AGENT_APP_WORKFLOW_PRODUCTIZATION.md").read_text(encoding="utf-8").lower()
    checklist = (ROOT / "docs" / "AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md").read_text(encoding="utf-8").lower()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    skill_install = (ROOT / "docs" / "SKILL_INSTALLATION.md").read_text(encoding="utf-8")

    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION.md" in readme
    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md" in readme
    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION.md" in agents
    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md" in agents
    assert "AGENT_APP_WORKFLOW_PRODUCTIZATION.md" in skill_install
    for term in (
        "agent app workflow productization",
        "production next",
        "agent-task brief",
        "context budget",
        "feedback carryover",
        "quality benchmark",
        "codex app",
        "codex cli",
        "claude code",
    ):
        assert term in product
        assert term in checklist


def test_creative_operator_protocol_defines_continue_write_preflight():
    creative = (ROOT / "shared" / "creative_operator_protocol.md").read_text(encoding="utf-8").lower()
    workflow = (ROOT / "shared" / "workflow_mapping.md").read_text(encoding="utf-8").lower()
    skill_text = "\n".join(
        (ROOT / skill / "SKILL.md").read_text(encoding="utf-8").lower()
        for skill in ("longform-novel-codex", "longform-novel-claude")
    )

    for term in (
        "/工程续章",
        "pre-write guide",
        "user preference",
        "automatic fallback",
        "pacing precheck",
        "tail-hook declaration",
        "forbidden reveal confirmation",
        "failure repair path",
        "five-step closed loop",
        "event matrix",
        "reverse brake",
    ):
        assert term in creative

    for term in (
        "five-step chapter loop",
        "pacing precheck",
        "tail-hook declaration",
        "forbidden reveal confirmation",
    ):
        assert term in workflow

    for term in (
        "/工程续章",
        "/工程下一步",
        "/工程工单",
        "pre-write guide",
        "pacing precheck",
        "tail hook",
        "forbidden reveal",
        "five-step closed loop",
        "production next",
        "agent-task brief",
    ):
        assert term in skill_text


def test_editorial_team_protocol_defines_multi_role_review_contract():
    creative = (ROOT / "shared" / "creative_operator_protocol.md").read_text(encoding="utf-8").lower()
    workflow = (ROOT / "shared" / "workflow_mapping.md").read_text(encoding="utf-8").lower()
    command_protocol = (ROOT / "shared" / "command_protocol.md").read_text(encoding="utf-8").lower()
    combined = "\n".join([creative, workflow, command_protocol])

    for term in (
        "planning_chief_editor",
        "writing_agent",
        "anti_ai_editor",
        "serial_verifier",
        "executive_editor",
        "severity_counts",
        "review_round",
        "unresolved_items",
        "conditional_pass_streak",
        "need_human_reasons",
        "batch_reports",
        "ai taste",
        "editorial need-human",
    ):
        assert term in combined


def test_skill_creator_quick_validate_compatible():
    default_validator = Path.home() / ".codex" / "skills" / ".system" / "skill-creator" / "scripts" / "quick_validate.py"
    validator = Path(os.environ.get("SKILL_CREATOR_VALIDATE", str(default_validator)))
    if not validator.exists():
        return

    for skill in ("longform-novel-codex", "longform-novel-claude"):
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
