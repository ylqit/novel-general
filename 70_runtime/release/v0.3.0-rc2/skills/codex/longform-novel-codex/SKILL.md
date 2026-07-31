---
name: longform-novel-codex
description: Codex App / Codex CLI 中文长篇小说生产 Skill；用户说“/工程下一步”或需要 production next、同人/AU/续写、修章、审稿、图谱与记忆任务时触发。Codex 只写任务清单允许的候选文件，longform-engine 负责校验、显式 apply/finalize 与持久化。
---

# Longform Novel Codex

在 Codex App 或 Codex CLI 中使用本 Skill 操作 `longform-novel-engine`。默认是 no-key `agent_skill`：当前 Codex 会话负责需要语义理解的内容，CLI 负责确定性编排和 canonical 写入；不要索要 OpenAI、Anthropic 或 provider API key。

## 每轮入口

1. 运行 `longform-engine production next project.yaml`（`/工程下一步`）。
2. 返回 Agent task 时，运行 `longform-engine agent-task brief project.yaml TASK_OR_PATH`（`/工程工单`）。
3. 只读取工作单及 manifest 的 `input_files`，禁止扫描整个项目补上下文。
4. 只写 `allowed_output_paths`，并严格遵守 `output_schema`。
5. 运行工作单给出的 validate/submit；成功后等待用户明确执行 apply/finalize，失败则执行 `failure_next_command`。

完整中文命令映射见 `references/command_protocol.md`，任务顺序见 `references/workflow_mapping.md`，写作操作见 `references/creative_operator_protocol.md`。

开书阶段按 `book_ideation -> book_design -> outline_design` 推进。`book_ideation` 每轮只问一个问题，必须先取得用户明确选择再写候选 JSON；不得替用户默选。章节出现 `chapter_direction` 时同样先给 2-3 个因果方向并记录人工选择。写正文时遵守工作单内的 `effective_quality_contract_v1`，但不能把平台画像机械化为统一短句、对白率、快节奏或悬崖结尾。

同人项目允许使用 manifest 声明来源中的角色名、关系、世界观、能力和时间线。先完成 `fanfiction canon-task` 与 `fanfiction design-task`，再进入纲要和章节；不得扫描未声明原作，也不得在 canon JSON 或正文中搬运、拆分重构连续 `source prose`。`rights status` 只记录和提示，不由 Agent 擅自阻断工作流。正文与修章遵守 `Humanizer v3`；润色触发 `humanize_semantic_review` 时，必须由独立审稿角色比较来源稿与候选稿并通过 CLI 校验，不能由润色写作者自审放行。gate 通过后若出现 `reader_payoff_review`，必须按 span 证明实际收益与代价，再等待显式 finalize。

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
```

章节提交示例：

```text
longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex
longform-engine chapter finalize project.yaml --chapter N --approved-by human
```

遵守 `references/iron_laws.md`。最终回复按 `references/artifact_reporting.md` 报告产物、校验状态、阻断原因和下一条安全命令，不粘贴无关命令噪声。
