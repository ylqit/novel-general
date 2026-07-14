# Agent App Workflow Productization

本文档定义 Codex App、Codex CLI 和 Claude Code 直接使用 `longform-novel-engine` 的产品化工作流。目标不是在 Python CLI 里调用 LLM，而是让宿主 Agent 产品成为写作、修章、润色、语义抽取和审稿执行者；CLI 继续负责任务包、校验、门禁、apply、finalize、回滚和索引。

## 1. Product Positioning

`agent_skill` 是默认生产模式。Codex / Claude Code 使用当前产品会话完成智能输出，`longform-engine` 只提供可审计的控制面。

标准工作流：

```text
production next
-> agent-task brief
-> Agent writes only the declared output
-> validate or submit command
-> apply/finalize only when explicitly requested
```

这套流程不新增 GUI/API 专用 workflow，也不要求 OpenAI、Anthropic 或其他外部 provider API key。

## 2. Productized Commands

中文工程指令应优先使用生产体验入口：

| 指令 | CLI | 用途 |
| --- | --- | --- |
| `/工程下一步` | `longform-engine production next project.yaml` | 查看当前最高优先级安全动作。 |
| `/工程工单 TASK` | `longform-engine agent-task brief project.yaml TASK` | 渲染可交给 Agent 的工作单。 |
| `/工程生产状态` | `longform-engine production status project.yaml` | 查看 GUI/API 稳定状态摘要。 |
| `/工程生产看板` | `longform-engine production board project.yaml` | 按章节查看生产状态。 |
| `/工程推进` | `longform-engine production loop project.yaml --no-apply` | 自动推进确定性步骤，直到遇到 Agent、人工或 apply/finalize 阻断。 |

`production loop` 默认不得自动 finalize，也不得自动把 graph/memory/pacing 的 validated 输出 apply 到 canonical state。

## 3. Codex And Claude Output Lanes

Codex 写正文草稿：

```text
50_workbench/agent_drafts/chNNN.codex.md
```

Claude Code 写正文草稿：

```text
50_workbench/agent_drafts/chNNN.claude.md
```

修章、润色、扩写、图谱、记忆、角色记忆、审稿和节奏审查必须使用 manifest 声明的 `allowed_output_paths`。Agent 不得直接写：

```text
40_manuscript/final/
60_rag/
30_state/story_graph.json
30_state/tcs/
70_runtime/db/
```

## 4. Work Order Shape

`agent-task brief` 渲染的工作单必须把机器契约转成人类和 Agent 都能执行的说明：

- Agent role and output goal.
- Task id, task type, chapter, status, and manifest file.
- Context budget rules.
- Input files.
- Allowed output paths.
- Output schema.
- Validate, apply, and failure next commands.
- Hard boundaries and forbidden direct writes.
- Completion report template.

不同 task type 应有不同角色说明：章节作者、修章作者、Humanizer、扩写作者、图谱抽取员、语义记忆抽取员、角色记忆员、编辑角色、节奏读者。

## 5. Context Budget Rules

Agent 默认只读取 manifest 的 `input_files` 和工作单文本。不要为了“更懂项目”主动扫描整个项目。

允许读取：

- 当前工作单。
- manifest 声明的 input files。
- 用户在当前请求中明确补充的文件。

禁止默认读取或当成 canon：

- 未声明的 draft、repair candidate、research inbox、validation reports。
- 全量 final 正文目录。
- SQLite、模型缓存、运行时快照。
- 未经 promote 的研究资料。

如果任务需要证据，证据必须来自声明输入文件。

## 6. Feedback Carryover

`continue-write` 生成下一章任务包时，应把上一章受控报告摘要写入 `feedback_carryover`：

- `gate_result.json`
- `repair_plan.md`
- `humanize_check.json`
- `semantic_pacing_result.json`
- `editorial aggregate`

feedback 只作为下一章写作提醒，不直接修改 final、RAG、graph、TCS 或 SQLite。下一章 Agent 应使用它避免重复上一章的门禁、节奏、AI 味和审稿问题。

## 7. Quality Benchmark

质量基准用于验证 Agent App 工作流是否真的提升长篇理解。

推荐先跑 5 章 smoke，再跑 10 章 quality benchmark。记录字段：

- Agent product: Codex App, Codex CLI, or Claude Code.
- Chapters generated.
- Gate failure rate.
- Repair count.
- Humanizer P0/P1 count.
- Character drift count.
- Premature reveal count.
- Pacing score.
- Editorial need-human count.
- Notes and accepted tradeoffs.

可选 legacy baseline 缺失时记录为 `not_run`，不要阻断当前 no-key Agent workflow。

## 8. Acceptance

完成状态以 `docs/AGENT_APP_WORKFLOW_PRODUCTIZATION_CHECKLIST.md` 为准。新增能力必须保持：

- no LLM in Python CLI.
- no hidden external API key.
- no direct final/RAG/graph/TCS/SQLite write by Agent.
- no GUI/API-only workflow.
- no automatic chapter finalize.
