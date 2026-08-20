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

开书阶段按 `book_ideation -> book_design -> outline_design` 推进。Book Design 必须建立 `story_engine_contract_v1`，纲要建立 `reader_promise_ledger_v1`，滚动窗口在章节方向前必须有人工批准且 basis hash 有效的 `arc_causal_simulation_v1`。每个尚未应用方向的章节都必须先完成 `chapter_direction`，声明承诺动作并检查最近五章载体。作者只读取 `chapter_story_brief_v2`；事实 ID、promise ID、模式代码、hash、RAG、Graph 与 SQLite 词汇留在 CLI/规划/编辑控制面。写正文时遵守质量合同，但不能把平台画像机械化为统一短句、对白率、快节奏或悬崖结尾。

同人项目允许使用 manifest 声明来源中的角色名、关系、世界观、能力和时间线。先完成 `fanfiction canon-task` 与 `fanfiction design-task`，再进入纲要和章节；不得扫描未声明原作，也不得在 canon JSON 或正文中搬运、拆分重构连续 `source prose`。`rights status` 只记录和提示，不由 Agent 擅自阻断工作流。正文与修章遵守 `Humanizer v4` 和 `character_expression_packet_v1`；人物差异来自感知、决策、欲望、面具、身体和关系压力，不得强制统一对白或外貌配额。润色触发 `humanize_semantic_review` 时，必须由独立审稿角色比较来源稿与候选稿并通过 CLI 校验，不能由润色写作者自审放行。gate 通过后若出现 `reader_payoff_review`，必须按 span 证明实际收益与代价，再等待显式 finalize。

每章必须由 `scene_prose_editor` 以正文 span 证明 attempt → counteraction → choice → visible_cost → state_delta → reader_gain，其他角色按风险追加。P0/P1 必须在当前不可变 review bundle 中修复，`editorial_pattern_registry` 只供编辑风险与修章协调，永不进入作者工作单。全部独立审稿完成后执行 `chapter human-review-task / validate / apply`：`accept` 必须绑定候选、合同、承诺账本和因果模拟四类 hash 并提供转折/人物归属 span；`repair` 并入 review bundle，`redirect` 返回章节方向或改纲。两轮替代稿均失败时停止在 `repair_budget_exhausted`。

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
-> mandatory human story review accept / repair / redirect
-> explicit apply or chapter finalize --approved-by human after accept
-> chapter semantic-task / Agent unified JSON / semantic-validate
-> explicit semantic-apply / chapter close --approved-by human
```

章节提交示例：

```text
longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex
longform-engine chapter human-review-task project.yaml --chapter N
longform-engine chapter finalize project.yaml --chapter N --approved-by human
longform-engine chapter semantic-task project.yaml --chapter N
longform-engine chapter close project.yaml --chapter N --approved-by human
```

定稿后不要再分别创建 graph、memory 和 character-memory 抽取任务。按 `production next` 只完整读取 final 一次并输出 `canonical_delta_v1`；CLI 校验证据后规范化内部语义账本，并统一物化图谱、角色当前状态、伏笔、TCS、RAG 与 SQLite，关闭章节后才能续写。

遵守 `references/iron_laws.md`。最终回复按 `references/artifact_reporting.md` 报告产物、校验状态、阻断原因和下一条安全命令，不粘贴无关命令噪声。
