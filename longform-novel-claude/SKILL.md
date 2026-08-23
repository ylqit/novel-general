---
name: longform-novel-claude
description: Claude Code 中文长篇小说生产 Skill；用户说“/工程下一步”或需要 production next、同人/AU/续写、修章、审稿、图谱与记忆任务时触发。Claude Code 只写任务清单允许的候选文件，longform-engine 负责校验、显式 apply/finalize 与持久化。
---

# Longform Novel Claude Code

在 Claude Code 中使用本 Skill 操作 `longform-novel-engine`。默认是 no-key `agent_skill`：当前 Claude Code 会话负责需要语义理解的内容，CLI 负责确定性编排和 canonical 写入；不要索要 OpenAI、Anthropic 或 provider API key。

## 每轮入口

1. 运行 `longform-engine production next project.yaml`（`/工程下一步`）。
2. 返回 Agent task 时，运行 `longform-engine agent-task brief project.yaml TASK_OR_PATH`（`/工程工单`）。
3. 只读取工作单及 manifest 的 `io.inputs`，禁止扫描整个项目补上下文。
4. 只写 `io.output.path`，并严格遵守 `io.output.protocol`。
5. 运行 `commands.validate`；成功后等待用户明确执行 `commands.apply`，失败则执行 `commands.failure`。

完整中文命令映射见 `references/command_protocol.md`，任务顺序见 `references/workflow_mapping.md`，写作操作见 `references/creative_operator_protocol.md`。

严格执行工作单的 `session`：项目开书/卷级规划可继续协调会话；每章 `chapter_write` 新开作者会话；`repair` 可继续本章作者会话；自然度修订、人物/节奏/收益/连贯/同人审稿与 final 后语义档案均新开隔离会话。CLI 不会自动开子进程，必须由用户或宿主显式开启新会话，并以 `session.first_command` 为第一条命令。

上下文采用 `compact/standard/large` 自适应容量。字符数和文件数只是诊断；遇到顺序批次时按清单读取，不把范围证据一次塞满。章节正文始终一次输出完整正文；工作单出现 `prompt_budget_exceeded` 或 `need_human` 时停止，不静默截断核心事实。

开书阶段按 `book_ideation -> book_design -> outline_design` 推进。Book Design 建立 `story_engine_contract_v1`；纲要由 `planning_bundle_v1` 生成活动卷、滚动窗口、显式 `reader_promise_ledger_v2`、语义义务和 Plot Node 表。`production next` 只有在活动卷有效、未来至少三章为 firm、规划 basis 未漂移、独立语义审查通过且全部情节节点获得人工决定后才进入写作。人类从空白表单填写 `human_chapter_intent_v2`；不得代填。作者只读取 `chapter_story_brief_v5`；`chapter_story_brief_basis_v3` 绑定唯一 `chapter_contract_v5`、批准节点、语义义务、滚动窗口、必要事实与 renderer，内部任务为 `chapter_writing_task_v7`。内部 ID、hash、原始 RAG、平台诊断和编辑代码不得进入作者工作单。

同人项目允许使用 manifest 声明来源中的角色名、关系、世界观、能力和时间线。先完成 `fanfiction canon-task` 与 `fanfiction design-task`，再进入纲要和章节；不得扫描未声明原作，也不得在 canon JSON 或正文中搬运、拆分重构连续 `source prose`。`rights status` 只记录和提示，不由 Agent 擅自阻断工作流。正文与修章遵守 `character_expression_packet_v1`。自然度、人工修订或其他双稿变换触发 `prose_revision_semantic_review` 时，必须由独立角色比较来源稿与候选稿并通过 CLI 校验，不能由改稿者自审放行。

AI 初稿可通过 `chapter_coedit_session_v2` 反复共编：顾问给 2–3 个方案及影响，人类记录选择后才生成新的完整 workbench 候选；不得直接写 canonical，也不得绕过 P0/P1 repair。每章由 `scene_prose_editor` 和 `anti_template_editor` 独立审稿，阻断项进入不可变 `human_review_bundle_v2`；`reader_payoff_review` 用当前 span 证明实际收益。无阻断后，人类完成最终全文修改并锁定，以 `human_author_revision_v4` 绑定意图、共编来源、前后 span、读者影响和保护项；独立双稿复核后以 `agent=human` 提交并全量复审。`human_story_review_v7` 绑定当前合同、节点、义务及审稿证据。人工锁定后 AI 仅可只读咨询；任何 AI 正文变换都会让终稿、咨询和接受 stale。

## 写入边界

章节正文只能写入 `50_workbench/agent_drafts/chNNN.claude.md` 或 manifest 明确允许的路径。不得直接写：

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
-> Claude Code output
-> validate / draft submit
-> mandatory scene / anti-template reviews and P0/P1 repair
-> coedit options / human selection / complete workbench candidate
-> human_author_revision_v4 / final lock / semantic review / human submit / full re-review
-> optional human-final read-only consultation
-> mandatory human_story_review_v7 accept / repair / redirect
-> explicit apply or chapter finalize --approved-by human after accept
-> chapter semantic-task / Agent unified JSON / semantic-validate
-> explicit semantic-apply
-> confirm every approved event and every non-defer promise with exact final spans
-> chapter close --approved-by human / advance rolling window
```

章节提交示例：

```text
longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.claude.md --agent claude
longform-engine chapter human-review-task project.yaml --chapter N
longform-engine review serve project.yaml --chapter N --port 8765
longform-engine quality status project.yaml --json
longform-engine chapter finalize project.yaml --chapter N --approved-by human
longform-engine chapter semantic-task project.yaml --chapter N
longform-engine chapter close project.yaml --chapter N --approved-by human
```

定稿后不要再分别创建 graph、memory 和 character-memory 抽取任务。按 `production next` 只完整读取 final 一次并输出 `canonical_delta_v1`；CLI 校验证据后规范化内部语义账本，并统一物化图谱、角色当前状态、伏笔、TCS、RAG 与 SQLite。`chapter close` 还会核对批准事件和承诺的精确终稿 span；关闭成功后才推进滚动窗口。设定变更使用稳定事实 ID、独立语义影响审查和人工决定；涉及已定稿章节时必须进入 `revision_branch_v2`，不得自动全文替换。读者反馈保持非 canonical，只能转成规划或设定变更提案。

遵守 `references/iron_laws.md`。最终回复按 `references/artifact_reporting.md` 报告产物、校验状态、阻断原因和下一条安全命令，不粘贴无关命令噪声。
