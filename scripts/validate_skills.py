"""Validate longform-novel-engine skill packages and public command surface."""

from __future__ import annotations

from pathlib import Path
import re
import sys


SKILL_NAMES = ("longform-novel-codex", "longform-novel-claude")
REQUIRED_SHARED = (
    "iron_laws.md",
    "command_protocol.md",
    "workflow_mapping.md",
    "creative_operator_protocol.md",
    "artifact_reporting.md",
)
REQUIRED_ENGINE_SLASH_COMMANDS = (
    "/工程开书",
    "/工程下一步",
    "/工程工单",
    "/工程生产状态",
    "/工程生产看板",
    "/工程推进",
    "/工程续章",
    "/工程章节卡",
    "/工程分镜",
    "/工程提交稿",
    "/工程定稿",
    "/工程修章",
    "/工程改纲",
    "/工程验稿",
    "/工程入库",
    "/工程回滚",
    "/工程审稿",
)
REQUIRED_ARTIFACT_TERMS = (
    "gate_result.json",
    "repair_plan.md",
    "research_canon.jsonl",
    "stale_indexes.json",
    "next safe action",
)
REQUIRED_AGENT_SKILL_TERMS = (
    "agent_skill",
    "does not require an extra LLM API key",
    "50_workbench/agent_drafts",
    "draft submit",
    "chapter finalize",
    "/工程续章",
    "Pre-Write Guide",
    "pacing precheck",
    "tail hook",
    "forbidden reveal",
    "five-step closed loop",
    "production next",
    "agent-task brief",
    "production loop --no-apply",
)
REQUIRED_IRON_LAW_TERMS = (
    "40_manuscript/final/",
    "60_rag/",
    "30_state/story_graph.json",
    "70_runtime/db/",
    "draft submit",
    "chapter finalize",
    "passed=false",
)
REQUIRED_README_POSITIONING_TERMS = (
    "项目定位",
    "核心亮点",
    "百万字长篇设计目标",
    "长线一致性方案",
    "去 AI 味与审稿门禁",
    "Agent 协作写作流程",
    "快速开始",
    "新书创建向导",
    "中文工程指令",
    "项目目录与写入边界",
    "Semantic RAG 默认能力",
    "安装与环境准备",
    "FAQ",
)
REQUIRED_README_CREATIVE_TERMS = (
    "百万字中文长篇小说",
    "规模模板",
    "章节职责",
    "伏笔回收",
    "门禁闭环",
    "RAG",
    "Story Graph",
    "TCS",
    "Character Memory",
    "Outline Anchors",
    "Research Canon",
    "草稿提交",
    "修章",
    "人工放行",
    "定稿入库",
)
REQUIRED_README_AGENT_TERMS = (
    "Codex App",
    "Codex CLI",
    "ClaudeCode",
    "No API key",
    "/工程开书",
    "/工程续章",
    "/工程提交稿",
    "/工程定稿",
    "50_workbench/writing_tasks/ch001.md",
    "50_workbench/agent_drafts/ch001.codex.md",
    "50_workbench/agent_drafts/ch001.claude.md",
    "40_manuscript/final/",
    "60_rag/",
    "30_state/",
    "70_runtime/",
)
REQUIRED_README_SEMANTIC_TERMS = (
    "BAAI/bge-m3",
    "BAAI/bge-reranker-v2-m3",
    "semantic.allow_network_download: true",
    "semantic.allow_fallback: false",
    "自动下载",
    "70_runtime/models/",
    'pip install -e ".[semantic]"',
)
FORBIDDEN_README_HEADINGS = (
    "## 常用" + "命令",
    "## GitHub 发布" + "边界",
    "## 公开" + "入口",
    "## 开发" + "与测试",
)
REQUIRED_GITIGNORE_TERMS = (
    ".venv/",
    "novels/",
    "70_runtime/db/*.sqlite",
    "70_runtime/db/*.sqlite-*",
    "70_runtime/models/",
    "novels/*/70_runtime/db/*.sqlite",
    "novels/*/70_runtime/models/",
    ".env",
    ".env.*",
)
FORBIDDEN_RUNTIME_NAMING_TERMS = (
    "00_bible",
    "01_outline",
    "02_memory",
    "03_manuscript",
)
FORBIDDEN_ASSOCIATION_TERMS = (
    "novel" + "-skill",
    "novel" + "_skill",
    "NOVEL" + "_SKILL",
    "_".join(["novel", "flow", "executor"]),
    "qidian" + "_500_demo",
    "500 " + "章级",
    "500" + "-chapter",
    "/一键" + "开书",
    "/继续" + "写",
    "/提交" + "草稿",
    "/定稿" + "章节",
    "/工程" + "新建",
    "/工程" + "建项目",
)
ALLOWED_FORBIDDEN_RUNTIME_FILES = {
    Path("src/longform_engine/config/loader.py"),
}
CAPABILITY_GAP_CHECKLIST = Path("docs") / ("_".join(["NOVEL", "SKILL", "CAPABILITY", "GAP", "CHECKLIST"]) + ".md")
AGENT_COLLABORATION_HARDENING = Path("docs") / "AGENT_COLLABORATION_HARDENING.md"
AGENT_EXPERIENCE_ORCHESTRATION = Path("docs") / "AGENT_EXPERIENCE_ORCHESTRATION.md"
AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST = Path("docs") / "AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md"
ALLOWED_FORBIDDEN_ASSOCIATION_FILES = {
    CAPABILITY_GAP_CHECKLIST,
    AGENT_COLLABORATION_HARDENING,
    AGENT_EXPERIENCE_ORCHESTRATION,
    AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST,
}
ALLOWED_FORBIDDEN_ASSOCIATION_TOKENS = (
    CAPABILITY_GAP_CHECKLIST.name,
)
REQUIRED_AGENTS_TERMS = (
    "agent_skill",
    "50_workbench/writing_tasks/chNNN.md",
    "50_workbench/agent_drafts/chNNN.codex.md",
    "50_workbench/agent_drafts/chNNN.claude.md",
    "40_manuscript/final/",
    "60_rag/",
    "30_state/story_graph.json",
    "70_runtime/db/",
    "draft submit",
    "chapter finalize",
)
REQUIRED_COMMAND_PROTOCOL_TERMS = (
    "中文工程指令协议",
    "Codex App",
    "Codex CLI",
    "ClaudeCode",
    "没有项目配置时",
    "交互式创建向导",
    "50_workbench/agent_drafts/chNNN.codex.md",
    "50_workbench/agent_drafts/chNNN.claude.md",
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
    "/工程下一步",
    "/工程工单",
    "/工程生产状态",
    "/工程生产看板",
    "/工程推进",
    "production next",
    "agent-task brief",
    "production loop",
)
REQUIRED_CREATIVE_PROTOCOL_TERMS = (
    "/工程续章",
    "Pre-Write Guide",
    "user preference",
    "automatic fallback",
    "pacing precheck",
    "tail-hook declaration",
    "forbidden reveal confirmation",
    "failure repair path",
    "five-step closed loop",
    "Reverse Brake",
    "Event Matrix",
)
REQUIRED_EDITORIAL_PROTOCOL_TERMS = (
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
    "AI taste",
    "editorial need-human",
)
REQUIRED_SKILL_INSTALL_DOC_TERMS = (
    "longform-novel-codex",
    "longform-novel-claude",
    "longform-engine",
    "AGENT_APP_WORKFLOW_PRODUCTIZATION.md",
    "production next",
    "agent-task brief",
    "Copy-Item",
    "New-Item -ItemType Junction",
    "install-agent-skills.ps1",
    "install-agent-skills.sh",
    "--mode symlink",
    "python -m longform_engine.cli",
    "50_workbench/agent_drafts",
    "draft submit",
    "chapter finalize",
)
REQUIRED_SKILL_COMMAND_TERMS = (
    "longform-engine draft submit",
    "longform-engine chapter finalize",
    "longform-engine production next",
    "longform-engine agent-task brief",
    "python -m longform_engine.cli",
)

REQUIRED_PRODUCTIZATION_DOC_TERMS = (
    "Agent App Workflow Productization",
    "production next",
    "agent-task brief",
    "production loop",
    "Context Budget",
    "Feedback Carryover",
    "Quality Benchmark",
    "Codex App",
    "Codex CLI",
    "Claude Code",
)

REQUIRED_INSTALL_EXPERIENCE_README_TERMS = (
    "Copy-Paste Install",
    "Python engine + Codex skill + Claude Code skill",
    "Windows PowerShell",
    "macOS / Linux",
    ".\\scripts\\install-agent-skills.ps1 -Tool all -Mode copy",
    "bash scripts/install-agent-skills.sh --tool all --mode copy",
    "Copy-Item -Recurse -Force .\\shared\\*",
    "cp -R ./shared/.",
    "不需要额外配置 OpenAI、Anthropic 或其他 provider API key",
    "重启 Codex / Claude Code 会话",
)

REQUIRED_DIRECT_INSTALL_README_TERMS = (
    "https://github.com/<owner>/longform-novel-engine.git",
    "Agent 对话式安装提示",
    "请从 https://github.com/<owner>/longform-novel-engine.git 安装 longform-novel-engine",
    "Windows PowerShell 直接安装",
    "macOS / Linux 直接安装",
    "git clone --depth 1",
    "[guid]::NewGuid()",
    "mktemp -d",
    "$repoUrl",
    "REPO_URL",
    "$workRoot",
    "WORK_ROOT",
    "开发者本地安装",
    "~/.codex/skills",
    "~/.claude/skills",
    "不要复制小说项目正文、API key、runtime db 或模型缓存",
    "不推荐把远程脚本直接 `curl | bash` 作为主安装方式",
)

REQUIRED_PUBLIC_SKILL_README_TERMS = (
    "Skill 包定位",
    "包含的 Skills",
    "核心生产能力",
    "Agent 对话式安装提示",
    "首次使用",
    "安全边界",
    "longform-novel-codex",
    "longform-novel-claude",
    "shared",
    "章节写作",
    "修章",
    "Humanizer",
    "图谱抽取",
    "角色记忆",
    "编辑团队",
    "节奏审查",
    "Agent task",
    "agent-task brief",
    "allowed output path",
    "submit/gate",
    "30_state/tcs/",
    "CLI 的 submit、validate、apply、finalize",
)

REQUIRED_INSTALL_EXPERIENCE_DOC_TERMS = (
    "Recommended Windows PowerShell Installer",
    ".\\scripts\\install-agent-skills.ps1 -Tool all -Mode copy",
    ".\\scripts\\install-agent-skills.ps1 -Tool all -Mode junction -Force",
    "Recommended macOS / Linux Bash Installer",
    "bash scripts/install-agent-skills.sh --tool all --mode copy",
    "bash scripts/install-agent-skills.sh --tool all --mode symlink --force",
    "It does not copy novel projects, manuscripts, runtime databases, model caches, `.env` files, or API keys.",
    "40_manuscript/final/",
    "60_rag/",
    "30_state/story_graph.json",
    "30_state/tcs/",
    "70_runtime/db/",
)

REQUIRED_INSTALL_EXPERIENCE_CHECKLIST_TERMS = (
    "Skill Installation Productization Checklist",
    "README Copy-Paste Install",
    "Skill Installation Guide",
    "PowerShell Installer",
    "Bash Installer",
    "Skill Package Contracts",
    "Encoding And Documentation Quality",
    "Validation And Guards",
    "Definition Of Done",
    "README.md Public Skill Package Format",
    "不新增 `README.zh-CN.md`",
    "Direct GitHub Install Experience",
)

REQUIRED_POWERSHELL_INSTALLER_TERMS = (
    '[ValidateSet("codex", "claude-code", "all")]',
    '[ValidateSet("copy", "junction")]',
    '$CodexSkillRoot = (Join-Path $env:USERPROFILE ".codex\\skills")',
    '$ClaudeSkillRoot = (Join-Path $env:USERPROFILE ".claude\\skills")',
    "Assert-NotDangerousPath",
    "Assert-SafeInstallTarget",
    "Remove-ExistingTarget",
    "longform-novel-codex",
    "longform-novel-claude",
    "shared",
    "It does not copy novel projects, manuscripts, runtime databases, model caches",
    "Restart Codex / Claude Code",
)

REQUIRED_BASH_INSTALLER_TERMS = (
    "--tool codex|claude-code|all",
    "--mode copy|symlink",
    'CODEX_SKILL_ROOT="${HOME}/.codex/skills"',
    'CLAUDE_SKILL_ROOT="${HOME}/.claude/skills"',
    "normalize_input_path",
    "assert_not_dangerous_path",
    "assert_safe_install_target",
    "remove_existing_target",
    "longform-novel-codex",
    "longform-novel-claude",
    "shared",
    "It does not copy novel projects, manuscripts, runtime databases, model caches",
    "Restart Codex / Claude Code",
)


def validate_all(root: Path) -> list[str]:
    """Return validation errors for all bundled skills."""

    errors: list[str] = []
    shared = root / "shared"
    for name in SKILL_NAMES:
        errors.extend(validate_skill(root / name, name))
    for filename in REQUIRED_SHARED:
        if not (shared / filename).exists():
            errors.append(f"missing shared reference: {filename}")

    command_protocol = read_text(shared / "command_protocol.md")
    for command in REQUIRED_ENGINE_SLASH_COMMANDS:
        if command not in command_protocol:
            errors.append(f"command_protocol missing slash command: {command}")
    command_protocol_lower = command_protocol.lower()
    for term in REQUIRED_COMMAND_PROTOCOL_TERMS:
        if term.lower() not in command_protocol_lower:
            errors.append(f"command_protocol missing Agent-Skill term: {term}")

    creative_protocol_lower = read_text(shared / "creative_operator_protocol.md").lower()
    for term in REQUIRED_CREATIVE_PROTOCOL_TERMS:
        if term.lower() not in creative_protocol_lower:
            errors.append(f"creative_operator_protocol missing pre-write term: {term}")

    editorial_protocol_lower = "\n".join(
        read_text(shared / filename)
        for filename in ("creative_operator_protocol.md", "command_protocol.md", "workflow_mapping.md")
    ).lower()
    for term in REQUIRED_EDITORIAL_PROTOCOL_TERMS:
        if term.lower() not in editorial_protocol_lower:
            errors.append(f"shared protocols missing editorial term: {term}")

    errors.extend(validate_no_forbidden_associations(root))
    errors.extend(validate_required_terms(root))
    return errors


def validate_required_terms(root: Path) -> list[str]:
    errors: list[str] = []
    shared = root / "shared"

    artifact_reporting = read_text(shared / "artifact_reporting.md").lower()
    for term in REQUIRED_ARTIFACT_TERMS:
        if term.lower() not in artifact_reporting:
            errors.append(f"artifact_reporting missing term: {term}")

    iron_laws = read_text(shared / "iron_laws.md").lower()
    for term in REQUIRED_IRON_LAW_TERMS:
        if term.lower() not in iron_laws:
            errors.append(f"iron_laws missing term: {term}")

    readme = read_text(root / "README.md")
    readme_lower = readme.lower()
    for heading in FORBIDDEN_README_HEADINGS:
        if heading.lower() in readme_lower:
            errors.append(f"README should not include maintenance heading: {heading}")
    for label, terms in (
        ("creator-facing structure", REQUIRED_README_POSITIONING_TERMS),
        ("novel-design content", REQUIRED_README_CREATIVE_TERMS),
        ("Agent-Skill usage", REQUIRED_README_AGENT_TERMS),
        ("semantic model path", REQUIRED_README_SEMANTIC_TERMS),
    ):
        for term in terms:
            if term.lower() not in readme_lower:
                errors.append(f"README missing {label} term: {term}")

    quick_start = section_text(readme, "## 快速开始", "## 新书创建向导")
    if "longform-engine " in quick_start:
        errors.append("README quick start should use Chinese engineering commands, not CLI command blocks")

    gitignore = read_text(root / ".gitignore")
    for term in REQUIRED_GITIGNORE_TERMS:
        if term not in gitignore:
            errors.append(f".gitignore missing release ignore term: {term}")

    errors.extend(validate_naming_isolation(root))

    agents = read_text(root / "AGENTS.md").lower()
    for term in REQUIRED_AGENTS_TERMS:
        if term.lower() not in agents:
            errors.append(f"AGENTS missing Agent boundary term: {term}")

    skill_installation = read_text(root / "docs" / "SKILL_INSTALLATION.md").lower()
    for term in REQUIRED_SKILL_INSTALL_DOC_TERMS:
        if term.lower() not in skill_installation:
            errors.append(f"SKILL_INSTALLATION missing term: {term}")

    productization = read_text(root / "docs" / "AGENT_APP_WORKFLOW_PRODUCTIZATION.md").lower()
    productization_checklist = read_text(root / "docs" / "AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md").lower()
    for term in REQUIRED_PRODUCTIZATION_DOC_TERMS:
        if term.lower() not in productization:
            errors.append(f"AGENT_APP_WORKFLOW_PRODUCTIZATION missing term: {term}")
        if term.lower() not in productization_checklist:
            errors.append(f"AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST missing term: {term}")

    errors.extend(validate_installation_productization(root, readme, skill_installation))
    return errors


def validate_installation_productization(root: Path, readme: str, skill_installation: str) -> list[str]:
    """Validate copy-paste skill installation productization surfaces."""

    errors: list[str] = []
    readme_lower = readme.lower()
    skill_installation_lower = skill_installation.lower()

    if (root / "README.zh-CN.md").exists():
        errors.append("README.zh-CN.md should not be added; public Chinese skill package content belongs in README.md")

    for term in REQUIRED_INSTALL_EXPERIENCE_README_TERMS:
        if term.lower() not in readme_lower:
            errors.append(f"README missing install productization term: {term}")

    for term in REQUIRED_DIRECT_INSTALL_README_TERMS:
        if term.lower() not in readme_lower:
            errors.append(f"README missing direct GitHub install term: {term}")

    for term in REQUIRED_PUBLIC_SKILL_README_TERMS:
        if term.lower() not in readme_lower:
            errors.append(f"README missing public skill package term: {term}")

    copy_paste = section_text(readme, "## Copy-Paste Install", "## Agent Collaboration Hardening Docs")
    if not copy_paste:
        errors.append("README missing Copy-Paste Install section before Agent Collaboration Hardening Docs")
    else:
        for term in (
            "```powershell",
            "```bash",
            "python scripts/validate_skills.py",
            "https://github.com/<owner>/longform-novel-engine.git",
            "git clone --depth 1",
            "mktemp -d",
            ".\\scripts\\install-agent-skills.ps1 -Tool all -Mode copy",
            "bash scripts/install-agent-skills.sh --tool all --mode copy",
            "Agent 对话式安装提示",
            "开发者本地安装",
            "/工程下一步",
            "/工程工单",
            "40_manuscript/final/",
            "60_rag/",
            "30_state/story_graph.json",
            "30_state/tcs/",
            "70_runtime/db/",
        ):
            if term.lower() not in copy_paste.lower():
                errors.append(f"README Copy-Paste Install section missing term: {term}")

    direct_install_pos = readme_lower.find("git clone --depth 1")
    local_install_pos = readme_lower.find("开发者本地安装".lower())
    if direct_install_pos == -1 or local_install_pos == -1 or local_install_pos < direct_install_pos:
        errors.append("README should present direct GitHub install before local developer install")
    if "curl | bash" in readme_lower and "不推荐" not in readme_lower:
        errors.append("README must not recommend curl | bash as the primary install path")

    for term in REQUIRED_INSTALL_EXPERIENCE_DOC_TERMS:
        if term.lower() not in skill_installation_lower:
            errors.append(f"SKILL_INSTALLATION missing install productization term: {term}")

    checklist = read_text(root / "docs" / "SKILL_INSTALLATION_PRODUCTIZATION_CHECKLIST.md")
    checklist_lower = checklist.lower()
    for term in REQUIRED_INSTALL_EXPERIENCE_CHECKLIST_TERMS:
        if term.lower() not in checklist_lower:
            errors.append(f"SKILL_INSTALLATION_PRODUCTIZATION_CHECKLIST missing term: {term}")

    powershell_installer = read_text(root / "scripts" / "install-agent-skills.ps1")
    for term in REQUIRED_POWERSHELL_INSTALLER_TERMS:
        if term.lower() not in powershell_installer.lower():
            errors.append(f"install-agent-skills.ps1 missing term: {term}")

    bash_installer = read_text(root / "scripts" / "install-agent-skills.sh")
    for term in REQUIRED_BASH_INSTALLER_TERMS:
        if term.lower() not in bash_installer.lower():
            errors.append(f"install-agent-skills.sh missing term: {term}")

    forbidden_copy_terms = (
        "Copy-Item -Recurse -Force .\\novels",
        "Copy-Item -Recurse -Force .\\40_manuscript",
        "Copy-Item -Recurse -Force .\\60_rag",
        "Copy-Item -Recurse -Force .\\70_runtime",
        "cp -R ./novels",
        "cp -R ./40_manuscript",
        "cp -R ./60_rag",
        "cp -R ./70_runtime",
    )
    install_surface = "\n".join((readme, skill_installation, powershell_installer, bash_installer)).lower()
    for term in forbidden_copy_terms:
        if term.lower() in install_surface:
            errors.append(f"install productization surface must not copy project/runtime data: {term}")

    return errors


def validate_naming_isolation(root: Path) -> list[str]:
    """Reject retired startup path names on runtime surfaces."""

    errors: list[str] = []
    scan_roots = [
        root / "README.md",
        root / "AGENTS.md",
        root / "longform-novel-codex",
        root / "longform-novel-claude",
        root / "shared",
        root / "src",
        root / "templates",
    ]
    for path in iter_text_files(scan_roots):
        rel = path.relative_to(root)
        if rel in ALLOWED_FORBIDDEN_RUNTIME_FILES:
            continue
        text = read_text(path)
        for term in FORBIDDEN_RUNTIME_NAMING_TERMS:
            if term in text:
                errors.append(f"{rel}: retired runtime term is not allowed: {term}")
    return errors


def validate_no_forbidden_associations(root: Path) -> list[str]:
    """Reject retired project references, old startup labels, and retired slash-command names."""

    errors: list[str] = []
    scan_roots = [
        root / "README.md",
        root / "AGENTS.md",
        root / "docs",
        root / "longform-novel-codex",
        root / "longform-novel-claude",
        root / "shared",
        root / "scripts",
        root / "tests",
        root / "src",
        root / "templates",
        root / "config",
    ]
    for path in iter_text_files(scan_roots):
        rel = path.relative_to(root)
        if rel in ALLOWED_FORBIDDEN_ASSOCIATION_FILES:
            continue
        text = read_text(path)
        for allowed_token in ALLOWED_FORBIDDEN_ASSOCIATION_TOKENS:
            text = text.replace(allowed_token, "")
        for term in FORBIDDEN_ASSOCIATION_TERMS:
            if term in text:
                errors.append(f"{rel}: forbidden retired reference remains: {term}")
    return errors


def iter_text_files(paths: list[Path]) -> list[Path]:
    """Return text-like files under the provided paths."""

    result: list[Path] = []
    allowed_suffixes = {".md", ".py", ".yaml", ".yml", ".json", ".toml", ".txt"}
    for path in paths:
        if not path.exists():
            continue
        if path.is_file():
            result.append(path)
            continue
        for child in path.rglob("*"):
            if child.is_file() and child.suffix.lower() in allowed_suffixes:
                result.append(child)
    return result


def validate_skill(skill_dir: Path, expected_name: str) -> list[str]:
    """Validate one skill package."""

    errors: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return [f"{expected_name}: missing SKILL.md"]
    text = read_text(skill_md)
    frontmatter, body = parse_frontmatter(text)
    if frontmatter.get("name") != expected_name:
        errors.append(f"{expected_name}: frontmatter name mismatch")
    description = frontmatter.get("description", "")
    if len(description) < 80:
        errors.append(f"{expected_name}: description is too short")
    extra_keys = sorted(set(frontmatter) - {"name", "description"})
    if extra_keys:
        errors.append(f"{expected_name}: unsupported frontmatter keys: {', '.join(extra_keys)}")
    if "TODO" in text:
        errors.append(f"{expected_name}: TODO placeholder remains")
    for reference in REQUIRED_SHARED:
        if f"../shared/{reference}" not in body:
            errors.append(f"{expected_name}: missing reference to ../shared/{reference}")
    body_lower = body.lower()
    for term in REQUIRED_AGENT_SKILL_TERMS:
        if term.lower() not in body_lower:
            errors.append(f"{expected_name}: missing Agent-Skill term: {term}")
    for term in REQUIRED_SKILL_COMMAND_TERMS:
        if term.lower() not in body_lower:
            errors.append(f"{expected_name}: missing installed/fallback command term: {term}")
    openai_yaml = skill_dir / "agents" / "openai.yaml"
    if not openai_yaml.exists():
        errors.append(f"{expected_name}: missing agents/openai.yaml")
    else:
        openai_text = read_text(openai_yaml)
        if f"${expected_name}" not in openai_text:
            errors.append(f"{expected_name}: default_prompt must mention ${expected_name}")
        if "interface:" not in openai_text:
            errors.append(f"{expected_name}: agents/openai.yaml missing interface")
    return errors


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse the required simple YAML frontmatter without external dependencies."""

    if not text.startswith("---\n"):
        return {}, text
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, text
    raw = parts[1]
    body = parts[2]
    result: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not match:
            continue
        result[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return result, body


def section_text(text: str, start_heading: str, end_heading: str) -> str:
    start = text.find(start_heading)
    if start == -1:
        return ""
    end = text.find(end_heading, start + len(start_heading))
    if end == -1:
        return text[start:]
    return text[start:end]


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").lstrip("\ufeff")


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else Path(__file__).resolve().parents[1]
    errors = validate_all(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print("OK: skill packages validated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
