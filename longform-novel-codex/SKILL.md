---
name: longform-novel-codex
description: Codex App / Codex CLI 中文长篇小说生产 Skill；用户说“/工程下一步”或需要 production next、同人/AU/续写、修章、审稿、图谱与记忆任务时触发。Codex 只写任务清单允许的候选文件，longform-engine 负责校验、显式 apply/finalize 与持久化。
---

# Longform Novel Codex

在 Codex App 或 Codex CLI 中使用本 Skill 操作 `longform-novel-engine`。默认是 no-key `agent_skill`：当前 Codex 会话负责需要语义理解的内容，CLI 负责确定性编排和 canonical 写入；不要索要 OpenAI、Anthropic 或 provider API key。

## 每轮入口

1. 运行 `longform-engine production next project.yaml`（`/工程下一步`）。
2. 返回 Agent task 时，运行 `longform-engine agent-task brief project.yaml TASK_OR_PATH`（`/工程工单`）。
3. 只读取工作单及 manifest 的 `io.inputs`，禁止扫描整个项目补上下文。
4. 只写 `io.output.path`，并严格遵守 `io.output.protocol`。
5. 运行 `commands.validate`；成功后等待用户明确执行 `commands.apply`，失败则执行 `commands.failure`。

完整中文命令映射见 `references/command_protocol.md`，任务顺序见 `references/workflow_mapping.md`，写作操作见 `references/creative_operator_protocol.md`。

严格执行工作单的 `session`：项目开书/卷级规划可继续协调会话；每章 `chapter_write` 新开作者会话；`repair` 可继续本章作者会话；Humanizer、人物/节奏/收益/连贯/同人审稿与 final 后语义档案均新开隔离会话。CLI 不会自动开子进程，必须由用户或宿主显式开启新会话，并以 `session.first_command` 为第一条命令。

上下文采用 `compact/standard/large` 自适应容量。字符数和文件数只是诊断；遇到顺序批次时按清单读取，不把范围证据一次塞满。章节正文始终一次输出完整正文；工作单出现 `prompt_budget_exceeded` 或 `need_human` 时停止，不静默截断核心事实。

开书阶段按 `book_ideation -> book_design -> outline_design` 推进。`book_ideation` 每轮只问一个问题，必须先取得用户明确选择再写权威 Markdown；不得替用户默选。每个尚未应用方向的章节都必须先完成 `chapter_direction`：给出 2-3 个因果不同且带代价的方向并记录人工选择。写正文时遵守工作单内的 `effective_quality_contract_v1`，但不能把平台画像机械化为统一短句、对白率、快节奏或悬崖结尾。

同人项目允许使用 manifest 声明来源中的角色名、关系、世界观、能力和时间线。先完成 `fanfiction canon-task` 与 `fanfiction design-task`，再进入纲要和章节；不得扫描未声明原作，也不得在 canon JSON 或正文中搬运、拆分重构连续 `source prose`。`rights status` 只记录和提示，不由 Agent 擅自阻断工作流。正文与修章遵守 `Humanizer v4` 和 `character_expression_packet_v1`；人物差异来自感知、决策、欲望、面具、身体和关系压力，不得强制统一对白或外貌配额。润色触发 `humanize_semantic_review` 时，必须由独立审稿角色比较来源稿与候选稿并通过 CLI 校验，不能由润色写作者自审放行。gate 通过后若出现 `reader_payoff_review`，必须按 span 证明实际收益与代价，再等待显式 finalize。

发现正文问题后不要直接改稿。继续执行 `production next`，让语义、收益、节奏和风险编辑审稿全部绑定同一候选 hash；CLI 冻结 review bundle 后，由独立 `repair_coordinator` 生成不可变 `rNN.plan.md`，再由修章作者输出完整替代稿。修复主编不得删除或降级有效 P0/P1；两轮替代稿均失败时停止在 `repair_budget_exhausted`，不得创建第三轮。

## 写入边界

章节正文只能写入 `50_workbench/agent_drafts/chNNN.codex.md` 或 manifest 明确允许的路径。不得直接写：

- `40_manuscript/final/`
- `60_rag/`
- `30_state/story_graph.json`
- `30_state/tcs/`
- `70_runtime/db/`
- `10_bible/`、`20_outline/`、`10_bible/research_canon.jsonl`

这些目标只能由 CLI 在 validate 后通过显式 apply/finalize 和事务机制更新。门禁 `passed=false` 时停止下一章，按工作单进入修章、人工放行、分支或回滚。

## 最小闭环

```text
production next
-> agent-task brief
-> Codex output
-> validate / draft submit
-> explicit apply or chapter finalize --approved-by human
-> chapter semantic-task / Agent unified JSON / semantic-validate
-> explicit semantic-apply / chapter close --approved-by human
```

章节提交示例：

```text
longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex
longform-engine chapter finalize project.yaml --chapter N --approved-by human
longform-engine chapter semantic-task project.yaml --chapter N
longform-engine chapter close project.yaml --chapter N --approved-by human
```

定稿后不要再分别创建 graph、memory 和 character-memory 抽取任务。按 `production next` 只完整读取 final 一次并输出 `canonical_delta_v1`；CLI 校验证据后规范化内部语义账本，并统一物化图谱、角色当前状态、伏笔、TCS、RAG 与 SQLite，关闭章节后才能续写。

遵守 `references/iron_laws.md`。最终回复按 `references/artifact_reporting.md` 报告产物、校验状态、阻断原因和下一条安全命令，不粘贴无关命令噪声。
