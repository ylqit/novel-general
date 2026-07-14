---
name: longform-novel-codex
description: Command-driven Agent-Skill workflow for using longform-novel-engine in Codex to write million-word Chinese longform novels without an extra LLM API key; Codex writes drafts, while the CLI controls project creation, writing tasks, draft submit, chapter finalize, gates, RAG, story graph, research, rollback, SQLite, and filesystem persistence.
---

# Longform Novel Codex

Use this skill when the user wants Codex to operate `longform-novel-engine` as a production workflow for a million-word Chinese longform novel.

Default mode is `writing.mode = agent_skill`. Codex is the writing model. The engine CLI is the control plane for context, RAG, story graph, gates, SQLite, research promotion, revision, rollback, and file persistence. This default workflow does not require an extra LLM API key. API keys are only relevant for the optional future `api_provider` mode or an external local model service.

## Required Context

Read these shared references when the task touches the topic:

- Iron laws: `../shared/iron_laws.md`
- Command mapping: `../shared/command_protocol.md`
- Workflow order: `../shared/workflow_mapping.md`
- Creative operator protocol: `../shared/creative_operator_protocol.md`
- Final response format: `../shared/artifact_reporting.md`

## Non-Negotiable Boundary

Codex may draft prose, but Codex must not directly write or edit:

- `40_manuscript/final/`
- `60_rag/`
- `30_state/story_graph.json`
- `30_state/tcs/`
- `70_runtime/db/`

Codex may only write chapter prose to `50_workbench/agent_drafts/chNNN.codex.md`, or the exact non-prose output path declared by the rendered AgentTaskManifest work order. The draft becomes managed project state only through:

```powershell
longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex
```

The chapter becomes final only through:

```powershell
longform-engine chapter finalize project.yaml --chapter N --approved-by human
```

If `gate_result.json` has `passed=false`, stop the next-chapter flow. Run repair, waiver, branch, or rollback commands instead of continuing.

## No API Key Workflow

This is the no-key Agent workflow. For Codex App or Codex CLI, do not ask the user for an OpenAI, Anthropic, or other provider API key. Use the product's current Codex model to write the draft file and use the project CLI for every state-changing operation.

Recommended installed shell setup:

```powershell
cd D:\soft\code\OpenGit\novel-generator\longform-novel-engine
.\.venv\Scripts\Activate.ps1
longform-engine validate-config --template qidian-longform
```

Development fallback from an uninstalled source checkout:

```powershell
$env:PYTHONPATH=".;.\src"
python -m longform_engine.cli validate-config --template qidian-longform
```

## Chinese Command First

When the user asks to create a novel project, configure a million-word longform novel, or open a new book, present `/工程开书` first. If the current directory does not contain `project.yaml`, map it to:

```powershell
longform-engine open-book --interactive
```

If the user already provides a project config, map it to:

```powershell
longform-engine open-book project.yaml
```

Do not make a separate project-creation slash command the quick-start path. `init-project` remains an advanced CLI path for creating a project without immediately running open-book.

Common user-facing flow:

```text
/工程开书
/工程下一步
/工程工单
/工程续章
/工程提交稿
/工程定稿
```

## Productized Production Entry

For normal Codex App or Codex CLI production work, start each turn from the production experience layer instead of guessing the next command:

```powershell
longform-engine production next project.yaml
```

If the next action contains an Agent task id or manifest path, render the work order before writing:

```powershell
longform-engine agent-task brief project.yaml TASK_OR_PATH
```

Follow the rendered work order exactly:

- read only the work order and its declared `input_files`,
- write only the manifest-declared `allowed_output_paths`,
- obey the output schema,
- run the validate or submit command after writing,
- use the failure next command if validation fails,
- never scan the whole project as a substitute for the work order.

For every Agent task type, the manifest is the write boundary. Do not write a nearby workbench path, guessed filename, or canonical output path that is not listed in `allowed_output_paths`.

Use `/工程生产状态`, `/工程生产看板`, and `/工程推进` for status, board, and deterministic safe-loop operations. `/工程推进` maps to `production loop --no-apply`; it must pause at Agent, human approval, apply, and finalize boundaries.

## Chapter Task Flow

Prepare a chapter task:

```powershell
longform-engine continue-write novels/my-book/project.yaml --chapter 1
```

Read:

```text
novels/my-book/50_workbench/writing_tasks/ch001.md
```

Before writing, apply the `/工程续章` Pre-Write Guide in `../shared/creative_operator_protocol.md`:

- confirm user preference from Creative Brief and the task package,
- use automatic fallback defaults instead of asking for an extra API key,
- run the pacing precheck from `event_recommendation` and `constraint_packet.event_matrix`,
- declare the intended tail hook from `writing_brief.chapter_hook`,
- confirm `reverse_brake.forbidden_reveals`, `this_chapter_must_not_solve`, and `must_keep_suspense`,
- follow the five-step closed loop: task package -> Agent draft -> `draft submit` -> `gate-check` -> `chapter finalize` or repair.

Write Codex draft only to:

```text
novels/my-book/50_workbench/agent_drafts/ch001.codex.md
```

Submit and finalize:

```powershell
longform-engine draft submit novels/my-book/project.yaml --chapter 1 --file novels/my-book/50_workbench/agent_drafts/ch001.codex.md --agent codex
longform-engine chapter finalize novels/my-book/project.yaml --chapter 1 --approved-by human
```

## Operating Rules

- Work from the `longform-novel-engine` directory unless the user gives a specific project path.
- Prefer installed `longform-engine ...` commands. Use `python -m longform_engine.cli ...` with `PYTHONPATH=.;.\src` only as a development fallback.
- Treat files as the source of truth and SQLite as a rebuildable derived index.
- Do not put unpromoted `50_workbench/research_inbox/` material into canon, RAG, story graph, or writing tasks.
- Only `research promote` can move reviewed material into `10_bible/research_canon.jsonl`.
- If a previous chapter is not final, do not run next-chapter `continue-write`.
- If a rollback marks chapter cards, RAG, graph, or writing tasks stale, report the stale files before continuing.
- Do not overwrite `40_manuscript/final/` during ordinary revision; use `revision branch` or `revision rollback`.
- When writing, repairing, humanizing, or reviewing prose, read `../shared/creative_operator_protocol.md` first.

## Core Workflows

Bootstrap:

```text
/工程开书
-> /工程下一步
-> status
```

Agent-Skill chapter loop:

```text
production next
-> agent-task brief
-> continue-write
-> apply /工程续章 pre-write guide: user preference, automatic fallback, pacing precheck, tail-hook declaration, forbidden reveal confirmation, failure repair path
-> read writing task, Creative Brief, Writer Craft Brief, RAG, Graph, TCS, Character Memory, Style Memory, Event Matrix, Reverse Brake, and Humanizer v2 rules
-> Codex writes 50_workbench/agent_drafts/chNNN.codex.md
-> Codex runs Humanizer v2 self-check
-> draft submit
-> inspect gate_result.json
-> repair-chapter --plan-only if failed
-> chapter finalize if gate passed or waived
-> db sync/status
```

## Response Discipline

In the final answer, summarize command results and artifact paths. For gate failures, include `passed`, `severity`, `failures`, `allowed_actions`, and `next_command`. For research and rollback, include inbox/canon/impact/stale paths and say whether the next safe action is continue, repair, promote, rebuild, or review.
