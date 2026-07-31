# Skill Installation Productization Checklist

> 历史说明：本文档记录旧的源码 copy/junction 安装阶段。`v0.2.0` 起的 wheel/pipx、自包含 Skill、lifecycle CLI 与公开发布验收，以 `docs/PUBLIC_DISTRIBUTION_PRODUCTIZATION_CHECKLIST.md` 为准；旧的全局 `shared/` 和 `<owner>` 占位结论不再代表当前公开安装契约。

本文档用于后续验证 `longform-novel-engine` 的 README、Codex skill、Claude Code skill、安装文档和安装脚本是否达到“复制即用”的 skill 包体验。

本 checklist 只约束文档与安装体验，不改变 `agent_skill` 默认模式，不引入脚本内 LLM 调用，不改变 final/RAG/graph/SQLite 硬边界。

## 1. Status Legend

- `[ ]` 未开始或尚未验收。
- `[~]` 已有基础，但还没有达到复制即用验收标准。
- `[x]` 已实现，并有可重复验证方式。

## 2. README Copy-Paste Install

- [x] README 顶部包含 `Copy-Paste Install` 快速安装区。
- [x] README 明确说明 `longform-novel-engine = Python engine + Codex skill + Claude Code skill`。
- [x] README 给出 Windows PowerShell 一键复制安装命令。
- [x] README 给出 macOS / Linux bash 安装命令。
- [x] README 明确先安装 engine，再安装 skill。
- [x] README 明确普通 Agent-Skill 写作不需要 OpenAI / Anthropic / provider API key。
- [x] README 明确安装后重启 Codex / Claude Code 会话以刷新 skill discovery。
- [x] README 不再只引用安装文档，而是在首页给出可直接复制执行的命令。

## 3. Skill Installation Guide

- [x] `docs/SKILL_INSTALLATION.md` 同时提供 Codex copy 命令和 junction/symlink 命令。
- [x] `docs/SKILL_INSTALLATION.md` 同时提供 Claude Code copy 命令和 junction/symlink 命令。
- [x] Codex 与 Claude Code 的安装说明结构对称。
- [x] 安装说明包含 `python -m venv`。
- [x] 安装说明包含 `pip install -e .`。
- [x] 安装说明包含 `longform-engine validate-config --template qidian-longform`。
- [x] 安装说明包含可选 `pip install -e ".[semantic]"`。
- [x] 安装说明包含 `python scripts/validate_skills.py` 验证步骤。
- [x] 安装说明包含 `longform-engine production next ...` 的首次使用示例。
- [x] 安装说明包含 `longform-engine agent-task brief ...` 的首次使用示例。
- [x] 安装说明明确 Codex 草稿路径为 `50_workbench/agent_drafts/chNNN.codex.md`。
- [x] 安装说明明确 Claude Code 草稿路径为 `50_workbench/agent_drafts/chNNN.claude.md`。
- [x] 安装说明明确 Agent 不得直接写 `40_manuscript/final/`。
- [x] 安装说明明确 Agent 不得直接写 `60_rag/`。
- [x] 安装说明明确 Agent 不得直接写 `30_state/story_graph.json`。
- [x] 安装说明明确 Agent 不得直接写 `30_state/tcs/`。
- [x] 安装说明明确 Agent 不得直接写 `70_runtime/db/`。

## 4. PowerShell Installer

- [x] 新增或规划 `scripts/install-agent-skills.ps1`。
- [x] PowerShell 安装脚本支持 `-Tool codex`。
- [x] PowerShell 安装脚本支持 `-Tool claude-code`。
- [x] PowerShell 安装脚本支持 `-Tool all`。
- [x] PowerShell 安装脚本支持 `-Mode copy`。
- [x] PowerShell 安装脚本支持 `-Mode junction`。
- [x] PowerShell 安装脚本默认安装 Codex skill 到 `%USERPROFILE%\.codex\skills`。
- [x] PowerShell 安装脚本默认安装 Claude Code skill 到 `%USERPROFILE%\.claude\skills`。
- [x] PowerShell 安装脚本创建目标 skill root 时使用安全、幂等方式。
- [x] PowerShell 安装脚本包含危险路径保护，不能删除 home、repo root、空路径或根目录。
- [x] PowerShell 安装脚本只复制 skill 包所需文件，不复制用户小说项目正文、API key、runtime db 或模型缓存。
- [x] PowerShell 安装脚本输出安装目标和下一步验证命令。

## 5. Bash Installer

- [x] 可选新增 `scripts/install-agent-skills.sh`。
- [x] bash 安装脚本支持 `--tool codex`。
- [x] bash 安装脚本支持 `--tool claude-code`。
- [x] bash 安装脚本支持 `--tool all`。
- [x] bash 安装脚本支持 `--mode copy`。
- [x] bash 安装脚本支持 `--mode symlink`。
- [x] bash 安装脚本默认安装 Codex skill 到 `$HOME/.codex/skills`。
- [x] bash 安装脚本默认安装 Claude Code skill 到 `$HOME/.claude/skills`。
- [x] bash 安装脚本包含危险路径保护，不能删除 home、repo root、空路径或根目录。
- [x] bash 安装脚本只复制 skill 包所需文件，不复制用户小说项目正文、API key、runtime db 或模型缓存。
- [x] bash 安装脚本输出安装目标和下一步验证命令。

## 6. Skill Package Contracts

- [x] `longform-novel-codex/SKILL.md` 保持默认从 `/工程下一步` / `production next` 开始。
- [x] `longform-novel-claude/SKILL.md` 保持默认从 `/工程下一步` / `production next` 开始。
- [x] 两个 skill 都要求有 Agent task 时先运行 `/工程工单` / `agent-task brief`。
- [x] 两个 skill 都保留 no-key Agent workflow 说明。
- [x] 两个 skill 都明确普通写作不要求 OpenAI / Anthropic / provider API key。
- [x] 两个 skill 都明确 Agent 只能写 manifest 声明的输出路径。
- [x] 两个 skill 都明确不能直接写 final/RAG/graph/TCS/SQLite。
- [x] 两个 skill 都优先展示 `longform-engine ...` 命令，只把 `python -m longform_engine.cli ...` 作为开发 fallback。

## 7. Encoding And Documentation Quality

- [x] README 保存为 UTF-8。
- [x] `docs/SKILL_INSTALLATION.md` 保存为 UTF-8。
- [x] `docs/SKILL_INSTALLATION_PRODUCTIZATION_CHECKLIST.md` 保存为 UTF-8。
- [x] 中文在 PowerShell 中查看不出现不可恢复乱码。
- [x] 中文在 Git diff 中查看不出现不可恢复乱码。
- [x] 中文在 Markdown 预览中查看不出现不可恢复乱码。
- [x] 文档中的命令块可直接复制，不依赖隐藏上下文。
- [x] 文档中的 Windows 命令和 macOS / Linux 命令分区清晰。

## 8. Validation And Guards

- [x] `scripts/validate_skills.py` 能识别安装体验关键文本。
- [x] `python scripts/validate_skills.py` 通过。
- [x] `python scripts/release_surface_guards.py` 继续通过。
- [x] no-pollution E2E 不因安装文档或脚本变化而改变。
- [x] 安装脚本实现后，copy 模式可在临时目录跑通。
- [x] 安装脚本实现后，junction/symlink 模式可在临时目录跑通。
- [x] 安装脚本实现后，危险路径保护有测试或手动验证记录。
- [x] 安装脚本实现后，确认不会复制 `novels/`、`40_manuscript/`、`60_rag/`、`70_runtime/db/`、`.env` 或模型缓存。

Phase 8 verification record:

- `python scripts/validate_skills.py`
- `python scripts/release_surface_guards.py`
- `python -m pytest tests/test_e2e_agent_skill.py::test_e2e_invalid_agent_outputs_do_not_pollute_canonical_boundaries tests/test_production_experience.py::test_production_loop_no_pollution_pause_path`
- PowerShell installer copy smoke in `%TEMP%`, including no-forbidden-copy scan.
- PowerShell installer junction smoke in `%TEMP%`.
- PowerShell installer dangerous path check rejecting `$HOME`.
- bash installer copy smoke in `%TEMP%`, including no-forbidden-copy scan.
- bash installer symlink smoke in `/tmp`.
- bash installer dangerous path check rejecting `$HOME`.

## 9. Definition Of Done

- [x] README 与 `docs/SKILL_INSTALLATION.md` 都提供可直接复制执行的安装命令。
- [x] Codex 与 Claude Code 的安装路径、复制命令、链接命令和首次使用命令保持对称。
- [x] 安装体验文档明确 engine 与 skill 的两段式关系。
- [x] 安装体验文档明确 no-key Agent workflow 和 canonical 写入边界。
- [x] checklist 完成后能作为后续 PR 的验收依据。

Phase 9 final acceptance record:

- README exposes a top-level `Copy-Paste Install` section for Windows PowerShell and macOS / Linux.
- `docs/SKILL_INSTALLATION.md` exposes installer, copy, junction, and symlink flows for Codex and Claude Code.
- Codex and Claude Code skills both depend on the installed `shared/` directory and start normal production from `production next` / `agent-task brief`.
- Installers copy or link only `longform-novel-codex/`, `longform-novel-claude/`, and `shared/`.
- `scripts/validate_skills.py` guards the install productization surface against missing install commands, missing boundary text, and accidental project/runtime copy commands.

## 10. Recommended Verification Commands

```powershell
python scripts/validate_skills.py
python scripts/release_surface_guards.py
python -m pytest tests/test_skills.py tests/test_agent_task_protocol.py tests/test_e2e_agent_skill.py
python -m pytest tests/test_production_experience.py tests/test_cli.py::test_cli_mutating_commands_are_marked_for_project_lock
```

Phase 10 execution record:

- `python scripts/validate_skills.py`: passed.
- `python scripts/release_surface_guards.py`: passed.
- `python -m pytest tests/test_skills.py tests/test_agent_task_protocol.py tests/test_e2e_agent_skill.py`: 22 passed.
- `python -m pytest tests/test_production_experience.py tests/test_cli.py::test_cli_mutating_commands_are_marked_for_project_lock`: 17 passed.

## 11. README.md Public Skill Package Format

- [x] 不新增 `README.zh-CN.md`，公开中文首页直接整合进 `README.md`。
- [x] `README.md` 第一屏包含项目标题和一句话定位。
- [x] `README.md` 明确 `longform-novel-engine = Python engine + Codex skill + Claude Code skill`。
- [x] `README.md` 明确适用场景为中文长篇、数百章连载、Codex / Claude Code no-key Agent 协作。
- [x] `README.md` 包含 `longform-novel-codex`、`longform-novel-claude`、`shared` 的 Skill 列表。
- [x] `README.md` 包含章节写作、修章、Humanizer、图谱抽取、角色记忆、编辑团队、节奏审查的核心生产能力表。
- [x] `README.md` 包含 Windows PowerShell 与 macOS / Linux 的 Copy-Paste Install。
- [x] `README.md` 包含 Codex 与 Claude Code 的 Agent 对话式安装提示。
- [x] `README.md` 包含首次使用流程：`/工程下一步` -> `/工程工单` -> Agent 写稿 -> CLI submit/gate。
- [x] `README.md` 明确 Agent 不得直接写 `40_manuscript/final/`、`60_rag/`、`30_state/story_graph.json`、`30_state/tcs/`、`70_runtime/db/`。
- [x] `README.md` 保留原有工程内容：项目定位、百万字长篇设计目标、长线一致性方案、中文工程指令、项目目录与写入边界、Semantic RAG、开发与验证说明。
- [x] `scripts/validate_skills.py` 校验 README 公开 skill 包首页关键文本，防止后续退回到只有工程说明或只有安装命令的格式。

Phase 11 acceptance record:

- README now behaves as the public Chinese skill package homepage and the engineering overview in one file.
- The page is not split into `README.zh-CN.md`.
- The first-screen flow explains what the package is, which skills it installs, how to install it, how to start production, and which paths Agent must not write.

## 12. Direct GitHub Install Experience

- [x] README 的 `Copy-Paste Install` 默认面向尚未 clone 仓库的用户。
- [x] README 使用 `https://github.com/<owner>/longform-novel-engine.git` 作为集中发布 URL 占位。
- [x] README 将 Codex / Claude Code 对话式安装提示放在直接安装命令之前。
- [x] Codex 安装提示要求从 GitHub clone、安装 engine、安装 `longform-novel-codex` 和 `shared` 到 `~/.codex/skills`。
- [x] Claude Code 安装提示要求从 GitHub clone、安装 engine、安装 `longform-novel-claude` 和 `shared` 到 `~/.claude/skills`。
- [x] README 提供 Windows PowerShell 直接安装命令，使用唯一临时目录、`git clone --depth 1`、`.venv`、`pip install -e .`、`install-agent-skills.ps1`。
- [x] README 提供 macOS / Linux 直接安装命令，使用 `mktemp -d`、`git clone --depth 1`、`.venv`、`pip install -e .`、`install-agent-skills.sh`。
- [x] README 不把 `curl | bash` 作为主推荐安装方式。
- [x] README 将已 clone 源码目录安装降级为开发者本地安装。
- [x] README 继续明确普通 Agent-Skill 写作不需要 OpenAI / Anthropic / provider API key。
- [x] README 继续明确安装脚本不复制小说项目正文、API key、runtime db 或模型缓存。
- [x] `scripts/validate_skills.py` 校验 README 直接安装体验，防止后续退回到“必须先 clone 源码目录”的开发者视角。

Phase 12 acceptance record:

- README now supports direct GitHub install first, matching public skill package expectations.
- The source-checkout install path is retained only for local development.
- The install experience remains engine + skill, with no script-internal LLM provider and no canonical state pollution.
