# longform-novel-engine

本地工程化中文长篇小说生产 skill 包。面向 Codex App、Codex CLI 和 Claude Code，把章节写作、修章、润色、审稿、语义抽取和节奏审查拆成受控 Agent 工单；Agent 负责需要智能判断的草稿产物，CLI 负责 validate、apply、finalize、索引和回滚。

`longform-novel-engine = Python engine + Codex skill + Claude Code skill`。它不是单次 prompt 生成器，而是一套面向百万字中文长篇小说的 no-key Agent 协作工作流：普通 Agent-Skill 写作使用当前 Codex / Claude Code 产品会话，不需要额外配置 OpenAI、Anthropic 或其他 provider API key。

## Skill 包定位

| 对象 | 说明 |
| --- | --- |
| 适用作品 | 中文长篇网文、数百章连载、百万字规模、需要稳定人物和长期伏笔的项目 |
| 宿主产品 | Codex App、Codex CLI、Claude Code |
| 默认模式 | `agent_skill`：宿主 Agent 写候选稿，`longform-engine` 负责工程状态和门禁 |
| 安全原则 | Agent 只写 workbench 产物，不直接污染 final、RAG、story graph、TCS 或 SQLite |
| 首次入口 | `/工程下一步` 查看当前安全动作，返回 Agent task 时用 `/工程工单` 渲染工作单 |

## 包含的 Skills

| Skill | 平台 | 用途 |
| --- | --- | --- |
| `longform-novel-codex` | Codex App / Codex CLI | 读取工程下一步和 Agent 工单，只写 Codex 草稿或结构化候选输出 |
| `longform-novel-claude` | Claude Code | 读取同一套工程协议，只写 Claude Code 草稿或结构化候选输出 |
| `shared` | 两端共享 | 中文工程指令、铁律边界、工作流映射、创作协议和产物报告规则 |

两个宿主 skill 共享同一套 hard boundaries：每次 Agent 任务必须有明确输入文件、允许写入路径、输出 schema、validate 命令、apply 命令和失败后的 next command。

## 核心生产能力

| 环节 | 编排方式 | 受控产物 |
| --- | --- | --- |
| 章节写作 | CLI 生成任务包，Agent 写正文，CLI 提交与门禁 | `writing_tasks/`、`agent_drafts/`、`gate_result.json` |
| 修章 | CLI 生成 repair task，Agent 写候选稿，CLI 重新 submit/gate | `repair_plan.md`、修复候选稿 |
| Humanizer | CLI 生成润色任务，Agent 写润色候选，CLI 检查 | 润色候选与 humanize check |
| 图谱抽取 | CLI 生成语义抽取任务，Agent 输出 JSON，CLI validate/apply | story graph 候选 JSON |
| 角色记忆 | Agent 输出角色状态 JSON，CLI validate/apply | character memory 候选 JSON |
| 编辑团队 | CLI 生成多角色审稿任务，Agent 输出结构化审稿意见，CLI 汇总 | editorial aggregate、need-human 判断 |
| 节奏审查 | Agent 读正文做语义判断，CLI 固化报告和阻断结果 | semantic pacing report |

## Copy-Paste Install

面向普通用户时，不需要先手动 clone 仓库。直接把下面的安装提示发给 Codex / Claude Code，或复制 GitHub 直接安装命令即可。安装流程会先安装 Python engine，再把内置 skill 复制到 Codex / Claude Code 的用户 skill 目录。

发布前仓库地址集中使用这个占位，正式发布时只需要替换这一处：

```text
https://github.com/<owner>/longform-novel-engine.git
```

不推荐把远程脚本直接 `curl | bash` 作为主安装方式；下面的命令会先 clone 到临时目录，让用户和 Agent 都能审阅本地脚本后再执行。

### 1. Agent 对话式安装提示

Codex：

```text
请从 https://github.com/<owner>/longform-novel-engine.git 安装 longform-novel-engine：先 clone 到临时目录，安装 Python engine，运行 longform-engine validate-config --template qidian-longform，然后把 longform-novel-codex 和 shared 安装到 ~/.codex/skills。请运行 python scripts/validate_skills.py 验证，并提醒我重启 Codex 会话以刷新 skill discovery。普通 Agent-Skill 写作不需要额外 OpenAI、Anthropic 或 provider API key；不要复制小说项目正文、API key、runtime db 或模型缓存。
```

Claude Code：

```text
请从 https://github.com/<owner>/longform-novel-engine.git 安装 longform-novel-engine：先 clone 到临时目录，安装 Python engine，运行 longform-engine validate-config --template qidian-longform，然后把 longform-novel-claude 和 shared 安装到 ~/.claude/skills。请运行 python scripts/validate_skills.py 验证，并提醒我重启 Claude Code 会话以刷新 skill discovery。普通 Agent-Skill 写作不需要额外 OpenAI、Anthropic 或 provider API key；不要复制小说项目正文、API key、runtime db 或模型缓存。
```

### 2. Windows PowerShell 直接安装

```powershell
$repoUrl = "https://github.com/<owner>/longform-novel-engine.git"
$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("longform-novel-engine-" + [guid]::NewGuid().ToString("N"))
git clone --depth 1 $repoUrl $workRoot
Set-Location $workRoot
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform
.\scripts\install-agent-skills.ps1 -Tool all -Mode copy
python scripts/validate_skills.py
```

### 3. macOS / Linux 直接安装

```bash
REPO_URL="https://github.com/<owner>/longform-novel-engine.git"
WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/longform-novel-engine.XXXXXX")"
git clone --depth 1 "$REPO_URL" "$WORK_ROOT"
cd "$WORK_ROOT"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform
bash scripts/install-agent-skills.sh --tool all --mode copy
python scripts/validate_skills.py
```

安装后请重启 Codex / Claude Code 会话以刷新 skill discovery。首次使用时从 `/工程下一步` 开始；如果返回 Agent task，再运行 `/工程工单` 渲染工作单。

### 4. 开发者本地安装

如果你已经 clone 了仓库并正在本地开发，可以在 `longform-novel-engine` 源码目录中直接运行安装脚本：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform
.\scripts\install-agent-skills.ps1 -Tool all -Mode copy
python scripts/validate_skills.py
```

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform
bash scripts/install-agent-skills.sh --tool all --mode copy
python scripts/validate_skills.py
```

需要完全透明的复制命令时，可以使用下方手动版本。

Windows PowerShell 手动复制版：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform

$codexSkillRoot = Join-Path $env:USERPROFILE ".codex\skills"
$claudeSkillRoot = Join-Path $env:USERPROFILE ".claude\skills"
$codexSkill = Join-Path $codexSkillRoot "longform-novel-codex"
$claudeSkill = Join-Path $claudeSkillRoot "longform-novel-claude"
$codexShared = Join-Path $codexSkillRoot "shared"
$claudeShared = Join-Path $claudeSkillRoot "shared"
New-Item -ItemType Directory -Force $codexSkill | Out-Null
New-Item -ItemType Directory -Force $claudeSkill | Out-Null
New-Item -ItemType Directory -Force $codexShared | Out-Null
New-Item -ItemType Directory -Force $claudeShared | Out-Null
Copy-Item -Recurse -Force .\longform-novel-codex\* $codexSkill
Copy-Item -Recurse -Force .\longform-novel-claude\* $claudeSkill
Copy-Item -Recurse -Force .\shared\* $codexShared
Copy-Item -Recurse -Force .\shared\* $claudeShared

python scripts/validate_skills.py
```

macOS / Linux 手动复制版：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
longform-engine validate-config --template qidian-longform

mkdir -p "$HOME/.codex/skills/longform-novel-codex" "$HOME/.codex/skills/shared"
mkdir -p "$HOME/.claude/skills/longform-novel-claude" "$HOME/.claude/skills/shared"
cp -R ./longform-novel-codex/. "$HOME/.codex/skills/longform-novel-codex/"
cp -R ./longform-novel-claude/. "$HOME/.claude/skills/longform-novel-claude/"
cp -R ./shared/. "$HOME/.codex/skills/shared/"
cp -R ./shared/. "$HOME/.claude/skills/shared/"

python scripts/validate_skills.py
```

## 首次使用

安装并重启宿主产品后，不要让 Agent 扫全项目来猜下一步。标准入口是：

```text
/工程下一步
/工程工单
```

当 `/工程下一步` 返回 Agent task 时，继续运行 `/工程工单`，让 CLI 把 `agent-task brief` 渲染成写作者可执行的工作单。Agent 只读取工作单声明的 input files，只写工作单声明的 allowed output path。

典型章节流：

1. `/工程续章` 生成下一章任务包。
2. `/工程下一步` 确认当前安全动作。
3. `/工程工单` 渲染写作者工作单。
4. Agent 写入 `50_workbench/agent_drafts/chNNN.codex.md` 或 `50_workbench/agent_drafts/chNNN.claude.md`。
5. `/工程提交稿` 触发 submit/gate。
6. `/工程修章` 或 `/工程定稿` 处理门禁结果。

## 安全边界

Agent 不得直接写入：

```text
40_manuscript/final/
60_rag/
30_state/story_graph.json
30_state/tcs/
70_runtime/db/
```

章节草稿、修复候选、Humanizer 候选、图谱 JSON、角色记忆 JSON、编辑审稿意见和节奏审查意见都必须先落在 workbench 或任务声明的 allowed output path。只有 CLI 的 submit、validate、apply、finalize 流程可以改变 final/RAG/graph/TCS/SQLite。

## Agent Collaboration Hardening Docs

`longform-novel-engine` 的默认生产方向是 no-key Agent collaboration：`agent_skill` 模式下，Codex、Claude Code 等宿主 Agent 负责写稿、修章、润色和语义判断；CLI 负责任务包、校验、门禁、apply、finalize、回滚和索引。

相关设计与验收清单：

- `docs/AGENT_COLLABORATION_HARDENING.md`
- `docs/AGENT_COLLABORATION_HARDENING_CHECKLIST.md`
- `docs/AGENT_EXPERIENCE_ORCHESTRATION.md`
- `docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md`
- `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION.md`
- `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md`

本地工程化中文长篇小说创作引擎。

Local-first longform fiction engine for agent-assisted Chinese novels.

`longform-novel-engine` 面向百万字中文长篇小说项目，不是一次性 prompt 生成器。它把新书设计、长线记忆、章节推进、草稿提交、质量门禁、修章、定稿、RAG、故事图谱和 SQLite 派生索引收敛为一套本地工程流程，让 Codex App、Codex CLI、ClaudeCode 等 Agent 产品可以用中文工程指令协作写作。

## 项目定位

这套引擎适合需要长期连载、持续修订、多人或多 Agent 协作的中文长篇小说项目。它关注的不是“生成一章”，而是让一本百万字级作品在数百章推进中保持设定、人物、伏笔、节奏和读者承诺的一致性。

- 面向百万字长篇：默认 `standard` 模板为 150 万字 / 500 章，也支持 100 万字、200 万字和自定义规模。
- 本地文件为事实源：正文、任务、图谱、RAG、门禁报告和运行状态都落在小说项目目录。
- 中文指令优先：用户通过 `/工程开书`、`/工程续章`、`/工程提交稿`、`/工程定稿` 推进流程。
- Agent 安全边界清晰：Codex / ClaudeCode 只能写 workbench 草稿，不能直接写 final、RAG、story graph 或 SQLite。
- 默认 Semantic RAG：首次需要真实语义模型时，可自动下载默认 BGE embedding 与 BGE reranker 模型。

## 核心亮点

| 能力 | 解决的问题 | 产物 |
| --- | --- | --- |
| 新书创建向导 | 开书前一次性确认规模、题材、目录和读者契约 | `project.yaml`、开书治理文件 |
| 百万字规模模板 | 不把章节数写死，按总字数、章节数、单章字数、卷数组织长篇 | `million` / `standard` / `extended` |
| 章节任务包 | Agent 写作前获得明确章节职责、上下文和禁区 | `50_workbench/writing_tasks/` |
| 草稿提交门禁 | 草稿先进入受控 draft，再接受字数、节奏、连续性和 AI 痕迹检查 | `gate_result.json`、`repair_plan.md` |
| 定稿入库 | 只有通过或人工放行的章节才能进入正式正文和索引 | `40_manuscript/final/`、RAG、图谱 |
| 长线记忆 | 用 RAG、图谱、TCS、角色记忆和研究 canon 管理长期连续性 | `60_rag/`、`30_state/` |
| 修订回滚 | 支持重写分支、快照、回滚和 stale 标记 | rewrite、detached、impact reports |

## 百万字长篇设计目标

百万字长篇的难点不是单章文笔，而是长期结构控制。`longform-novel-engine` 默认把作品拆成可检查的工程对象：

- **规模**：100 万、150 万、200 万字模板，或自定义总字数、章节数、单章字数和卷数。
- **卷结构**：每卷承担阶段目标，避免中段失焦。
- **章节职责**：每章都有推进义务、场景重心、人物状态和伏笔约束。
- **节奏控制**：通过 pacing review 管理高潮密度、事件冷却、过渡章和读者疲劳。
- **伏笔回收**：通过 outline anchors、story graph 和 research impact 标记长期悬念。
- **门禁闭环**：失败章节不会进入 final，也不会污染 RAG、图谱或 SQLite。

规模模板：

| 预设 | 总字数 | 章节数 | 单章目标 | 卷数 | 适用场景 |
| --- | ---: | ---: | ---: | ---: | --- |
| `million` | 100 万 | 330 | 3000 | 5 | 百万字长篇 |
| `standard` | 150 万 | 500 | 3000 | 6 | 默认标准长篇 |
| `extended` | 200 万 | 650 | 3000 | 8 | 超长篇连载 |

## 长线一致性方案

引擎用多层记忆机制降低长篇写作中常见的设定遗忘、角色漂移、伏笔断裂和时间线错乱：

| 层级 | 机制 | 用途 |
| --- | --- | --- |
| RAG | 从 final 正文、摘要和 canon 资料构建检索上下文 | 写下一章前召回相关事实 |
| Story Graph | 管理角色、地点、组织、道具、事件、关系和伏笔 | 检查图谱冲突与状态变化 |
| TCS | Temporal Context State | 防止未来事实泄漏和时间状态错位 |
| Character Memory | 角色状态、动机、关系、能力边界 | 防止人物性格和战力漂移 |
| Outline Anchors | 大纲锚点、卷目标、阶段推进 | 防止中途改纲破坏全局结构 |
| Research Canon | 审核后的资料入库 | 避免未确认资料污染正文和索引 |

## 去 AI 味与审稿门禁

长篇小说需要稳定、具体、可读的文字，而不是安全、抽象、模板化的段落。引擎把“写作”和“入库”分开：

1. Agent 只写草稿，位置限定在 `50_workbench/agent_drafts/`。
2. `/工程提交稿` 把草稿提升到受控 draft，并触发门禁。
3. 门禁检查字数、连续性、节奏、AI 痕迹、禁区和发布可读性。
4. 失败时生成 `repair_plan.md`，走 `/工程修章` 或人工放行。
5. `/工程定稿` 通过后才写入 `40_manuscript/final/`，并同步 RAG、图谱和 SQLite。

这意味着候选稿、修复稿、润色稿和审稿输出都只是 workbench 产物，不会绕过正式入库流程。

## Agent 协作写作流程

普通 Agent-Skill 写作采用 No API key 工作流，不要求额外 OpenAI、Anthropic 或其他 provider API key。Codex App、Codex CLI、ClaudeCode 使用当前产品会话生成章节正文草稿，`longform-engine` 负责上下文、门禁、定稿、RAG、图谱、SQLite 和落盘。

| 产品 | 读取任务 | 允许写入 | 提交身份 |
| --- | --- | --- | --- |
| Codex App | `50_workbench/writing_tasks/ch001.md` | `50_workbench/agent_drafts/ch001.codex.md` | `codex` |
| Codex CLI | `50_workbench/writing_tasks/ch001.md` | `50_workbench/agent_drafts/ch001.codex.md` | `codex` |
| ClaudeCode | `50_workbench/writing_tasks/ch001.md` | `50_workbench/agent_drafts/ch001.claude.md` | `claude` |

内置 skill 包：

```text
longform-novel-codex/
longform-novel-claude/
```

后续补齐隔壁技能包创作现场能力的路线图见 `docs/NOVEL_SKILL_CAPABILITY_GAP_CHECKLIST.md`。新增自动写书、扩写、风格提取、中文 Humanizer、事件矩阵、反向刹车或编辑团队能力时，应先在该 checklist 中确认验收标准，并保持 workbench-only 写入边界。

Codex 读取 `longform-novel-codex/SKILL.md`，ClaudeCode 读取 `longform-novel-claude/SKILL.md`。安装和全局链接说明见 `docs/SKILL_INSTALLATION.md`。

产品化 Agent App 工作流默认从 `/工程下一步` 开始：先运行 `production next` 判断当前卡点，再用 `/工程工单` 渲染 `agent-task brief`，最后只写工作单声明的输出路径。详细设计见 `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION.md`。

## 快速开始

使用中文工程指令即可开始，不需要先记底层 CLI。

```text
/工程开书
/工程续章
/工程提交稿
/工程定稿
```

`/工程开书` 是唯一新书启动入口。没有 `project.yaml` 时，它会进入新书创建向导；已有项目配置时，它只执行开书初始化，不覆盖现有配置。

系统会自动完成：

- 生成或读取项目配置。
- 创建标准小说项目目录。
- 写入开书确认、读者契约和生产规则。
- 准备后续章节任务所需的状态文件。
- 保持 final、RAG、story graph、SQLite 的写入边界。

## 新书创建向导

首次 `/工程开书` 会引导确认：

| 配置项 | 说明 |
| --- | --- |
| 小说标题 | 用于项目展示和开书治理文件 |
| 项目 slug | 用于稳定目录名和文件标识 |
| 输出目录 | 小说项目落盘位置 |
| 模板风格 | 默认 `qidian-longform` |
| 规模预设 | 百万字、标准长篇、超长篇或自定义 |
| 自定义规模 | 总字数、章节数、单章目标字数、软上下限、卷数 |

推荐选择 `standard` 作为初始模板，再根据题材密度和更新节奏调整章节规模。

## 中文工程指令

README 只列出日常写作最常用入口，完整映射见 `shared/command_protocol.md`。

| 中文指令 | 用途 |
| --- | --- |
| `/工程开书` | 新建或打开一本小说项目，确认开书治理信息 |
| `/工程下一步` | 查看当前最高优先级安全动作 |
| `/工程工单` | 将 AgentTaskManifest 渲染成 Codex / ClaudeCode 工作单 |
| `/工程生产状态` | 查看生产状态 JSON/文本摘要 |
| `/工程生产看板` | 按章节查看 draft、final、gate、Agent task 和审稿状态 |
| `/工程推进` | 推进确定性步骤，遇到 Agent、人工或 apply/finalize 阻断时暂停 |
| `/工程续章` | 生成下一章任务包 |
| `/工程章节卡` | 单独生成或刷新章节卡 |
| `/工程分镜` | 生成 Beat Sheet |
| `/工程提交稿` | 提交 Agent 草稿并触发门禁 |
| `/工程验稿` | 单独执行章节门禁 |
| `/工程修章` | 根据门禁失败生成修复计划或候选稿 |
| `/工程定稿` | 将通过或放行的章节写入 final |
| `/工程改纲` | 中途改纲并标记受影响产物 |
| `/工程入库` | 将审核后的研究资料提升为 canon |
| `/工程回滚` | 回滚章节并保留脱离稿 |
| `/工程审稿` | 生成审稿产物 |

## 项目目录与写入边界

| 路径 | 用途 | 写入边界 |
| --- | --- | --- |
| `project.yaml` | 单本小说配置和运行契约 | 由模板或交互向导生成，之后通过配置编辑和校验维护 |
| `00_governance/` | 开书确认、读者契约、生产规则 | 开书流程和人工确认产物 |
| `10_bible/` | Bible、世界观、人物设定、研究 canon、creative brief | 资料必须经审核流程进入 |
| `20_outline/` | 总纲、卷纲、章节卡、锚点和改纲报告 | 规划、改纲和回滚流程维护 |
| `30_state/` | 小说状态、故事图谱、TCS、时间线 | 不允许 Agent 直接写 |
| `40_manuscript/draft/` | 已提交但未定稿的章节草稿 | 只能由草稿提交流程提升 |
| `40_manuscript/final/` | 正式定稿章节 | 只能由定稿流程写入 |
| `50_workbench/writing_tasks/` | 给 Codex / ClaudeCode 的写作任务包 | 续章流程生成 |
| `50_workbench/agent_drafts/` | Agent 直接写正文草稿的唯一位置 | Codex 写 `chNNN.codex.md`，ClaudeCode 写 `chNNN.claude.md` |
| `60_rag/` | RAG chunk、上下文、语义记忆、向量派生文件 | 只由受控流程维护 |
| `70_runtime/` | SQLite、锁、快照、模型缓存和运行时索引 | 可重建运行时目录 |

## Semantic RAG 默认能力

模板默认启用 Semantic RAG，并使用默认 BGE profile：

- embedding: `BAAI/bge-m3`
- reranker: `BAAI/bge-reranker-v2-m3`
- 模型缓存目录：`70_runtime/models/`
- `semantic.allow_network_download: true`
- `semantic.allow_fallback: false`

首次执行需要真实语义模型的流程时，如果本地缓存缺失且允许联网，引擎会自动下载默认 BGE embedding 和 BGE reranker 模型。不能联网的环境可提前预热缓存，详见 `docs/RAG_MODEL.md` 和 `docs/SKILL_INSTALLATION.md`。

## 安装与环境准备

推荐使用项目内 `.venv`：

```powershell
git clone https://github.com/<owner>/longform-novel-engine.git
cd longform-novel-engine
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -e .
```

macOS / Linux 使用：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
pip install -e .
```

需要真实语义模型能力时：

```powershell
pip install -e ".[semantic]"
```

详细 skill 安装、目录链接和开发 fallback 见 `docs/SKILL_INSTALLATION.md`。

## FAQ

### Q: `/工程开书` 和创建项目是什么关系？

`/工程开书` 是用户侧的新书启动入口。没有项目配置时，它会先引导创建项目，再执行开书初始化；已有 `project.yaml` 时，它不会覆盖配置，只写入开书治理产物。

### Q: 可以不是 500 章吗？

可以。默认 `standard` 是 150 万字 / 500 章，但也可以选择 100 万字、200 万字，或自定义总字数、章节数、单章字数和卷数。

### Q: Codex / ClaudeCode 会直接写最终正文吗？

不会。Agent 只能写 `50_workbench/agent_drafts/`。最终正文必须经过提交、门禁、人工确认或放行，再由定稿流程写入 `40_manuscript/final/`。

### Q: Semantic RAG 是否需要手动配置模型？

默认模板启用 BGE profile。首次需要真实语义模型且允许联网时，会自动下载默认 BGE embedding 和 reranker；离线环境可以提前预热模型缓存。

### Q: 门禁失败后怎么办？

使用 `/工程修章` 生成修复计划或候选修复稿。失败稿不会进入 final，也不会污染 RAG、图谱或 SQLite。
