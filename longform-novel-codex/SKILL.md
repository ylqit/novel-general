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

严格执行工作单的 `session`：项目开书/卷级规划可继续协调会话；每章 `chapter_write` 新开作者会话；`repair` 可继续本章作者会话；自然度修订、人物/节奏/收益/连贯/同人审稿与 final 后语义档案均新开隔离会话。CLI 不会自动开子进程，必须由用户或宿主显式开启新会话，并以 `session.first_command` 为第一条命令。

上下文采用 `compact/standard/large` 自适应容量。字符数和文件数只是诊断；遇到顺序批次时按清单读取，不把范围证据一次塞满。章节正文始终一次输出完整正文；工作单出现 `prompt_budget_exceeded` 或 `need_human` 时停止，不静默截断核心事实。

开书阶段按 `book_ideation -> book_design -> outline_design` 推进。Book Design 必须建立 `story_engine_contract_v1`，纲要建立 `reader_promise_ledger_v1`，滚动窗口在章节方向前必须有人工批准且 basis hash 有效的 `arc_causal_simulation_v1`。每章先完成带 2–3 个稳定 option ID 的 `chapter_direction` 和 `chapter_direction_selection_v1`，方向应用后再由人从空白表单填写并应用 `human_chapter_intent_v1`；不得代填。作者只读取 `chapter_story_brief_v4`；`chapter_story_brief_basis_v2` 绑定合同、人类意图、筛选事实、人物声音、最近五章结构与 renderer。内部 ID、hash、原始 RAG、平台诊断和编辑代码不得进入作者工作单。

同人项目允许使用 manifest 声明来源中的角色名、关系、世界观、能力和时间线。先完成 `fanfiction canon-task` 与 `fanfiction design-task`，再进入纲要和章节；不得扫描未声明原作，也不得在 canon JSON 或正文中搬运、拆分重构连续 `source prose`。`rights status` 只记录和提示，不由 Agent 擅自阻断工作流。正文与修章遵守 `character_expression_packet_v1`。自然度、人工修订或其他双稿变换触发 `prose_revision_semantic_review` 时，必须由独立角色比较来源稿与候选稿并通过 CLI 校验，不能由改稿者自审放行。

AI 初稿可通过 `chapter_coedit_session_v1` 反复共编：顾问给 2–3 个方案及影响，人类记录选择后才生成新的完整 workbench 候选；不得直接写 canonical，也不得绕过 P0/P1 repair。每章由 `scene_prose_editor` 和 `anti_template_editor` 独立审稿，阻断项进入不可变 `human_review_bundle_v2`；`reader_payoff_review` 用当前 span 证明实际收益。无阻断后，人类完成最终全文修改并锁定，以 `human_author_revision_v3` 绑定意图、共编来源、前后 span、读者影响和保护项；独立双稿复核后以 `agent=human` 提交并全量复审。`human_story_review_v6` 绑定八类当前证据。人工锁定后 AI 仅可只读咨询；任何 AI 正文变换都会让终稿、咨询和接受 stale。

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
-> mandatory scene / anti-template reviews and P0/P1 repair
-> coedit options / human selection / complete workbench candidate
-> human_author_revision_v3 / final lock / semantic review / human submit / full re-review
-> optional human-final read-only consultation
-> mandatory human_story_review_v6 accept / repair / redirect
-> explicit apply or chapter finalize --approved-by human after accept
-> chapter semantic-task / Agent unified JSON / semantic-validate
-> explicit semantic-apply / chapter close --approved-by human
```

章节提交示例：

```text
longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex
longform-engine chapter human-review-task project.yaml --chapter N
longform-engine review serve project.yaml --chapter N --port 8765
longform-engine quality status project.yaml --json
longform-engine chapter finalize project.yaml --chapter N --approved-by human
longform-engine chapter semantic-task project.yaml --chapter N
longform-engine chapter close project.yaml --chapter N --approved-by human
```

定稿后不要再分别创建 graph、memory 和 character-memory 抽取任务。按 `production next` 只完整读取 final 一次并输出 `canonical_delta_v1`；CLI 校验证据后规范化内部语义账本，并统一物化图谱、角色当前状态、伏笔、TCS、RAG 与 SQLite，关闭章节后才能续写。

遵守 `references/iron_laws.md`。最终回复按 `references/artifact_reporting.md` 报告产物、校验状态、阻断原因和下一条安全命令，不粘贴无关命令噪声。
