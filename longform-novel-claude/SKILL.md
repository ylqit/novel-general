---
name: longform-novel-claude
description: Slash-command Agent-Skill wrapper for using longform-novel-engine from ClaudeCode without an extra LLM API key; ClaudeCode writes agent drafts, while the CLI enforces project creation, writing tasks, draft submit, chapter finalize, gates, RAG, story graph, research promotion, rollback, SQLite, and filesystem persistence.
---

# Longform Novel ClaudeCode

Use this skill to translate Chinese engineering slash-command requests into `longform-engine` CLI commands. Keep the interaction command-driven, file-backed, and gate-controlled.

Default mode is `writing.mode = agent_skill`. ClaudeCode is the writing model. The Python CLI owns context assembly, RAG, story graph, gate checks, chapter finalization, SQLite sync, research promotion, revision, rollback, and all official persistence. This default workflow does not require an extra LLM API key. API keys are only for optional future `api_provider` mode or a local model service.

## Required Context

Before executing slash commands, read:

- `../shared/iron_laws.md`
- `../shared/command_protocol.md`
- `../shared/workflow_mapping.md`
- `../shared/creative_operator_protocol.md`
- `../shared/artifact_reporting.md`

## Agent Draft Boundary

ClaudeCode may write prose only to:

```text
50_workbench/agent_drafts/chNNN.claude.md
```

ClaudeCode must not directly write or edit:

- `40_manuscript/final/`
- `60_rag/`
- `30_state/story_graph.json`
- `30_state/tcs/`
- `70_runtime/db/`

The only legal promotion path is:

```powershell
longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.claude.md --agent claude
longform-engine chapter finalize project.yaml --chapter N --approved-by human
```

If `gate_result.json` has `passed=false`, do not run `/工程续章` for the next chapter. Run `/工程修章`, `/工程放行`, `/工程重写分支`, or `/工程回滚` according to the gate report.

## No API Key Workflow

This is the no-key Agent workflow. Do not ask the user to fill an OpenAI, Anthropic, or other provider API key for ClaudeCode skill mode. ClaudeCode writes the draft using the current ClaudeCode product session, then the CLI submits, checks, finalizes, indexes, and persists.

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

## Slash Command Handling

- Map the user's slash command through `../shared/command_protocol.md`.
- Start normal production turns with `/工程下一步`, which maps to `longform-engine production next project.yaml`.
- When `/工程下一步` returns an Agent task, run `/工程工单`, which maps to `longform-engine agent-task brief project.yaml TASK_OR_PATH`, before writing any output.
- `/工程开书` is the recommended new-book entry. If no `project.yaml` exists, run `longform-engine open-book --interactive`; if a config exists or is provided, run `longform-engine open-book project.yaml`.
- Do not present a separate project-creation slash command as the quick-start path. If the user explicitly wants to create only the directory and config without open-book, use the advanced `init-project` mapping.
- Fill `N`, `query`, `note.md`, or `research_id` from the user's request. If missing and unsafe to infer, ask for the missing value.
- Execute the CLI command from the `longform-novel-engine` directory.
- After each command, report the important artifacts, not raw command noise.
- Prefer installed `longform-engine ...` commands. Use `python -m longform_engine.cli ...` with `PYTHONPATH=.;.\src` only as a development fallback.

## Chapter Task Flow

Before preparing a new task, inspect the production next action:

```powershell
longform-engine production next project.yaml
```

If an Agent task exists, render its work order and follow it instead of scanning the whole project:

```powershell
longform-engine agent-task brief project.yaml TASK_OR_PATH
```

The work order controls the allowed input files, allowed output paths, schema, validate command, apply command, and failure next command.

Follow the rendered work order exactly:

- read only the work order and its declared `input_files`,
- write only the manifest-declared `allowed_output_paths`,
- obey the output schema,
- run the validate or submit command after writing,
- use the failure next command if validation fails,
- never scan the whole project as a substitute for the work order.

For every Agent task type, the manifest is the write boundary. Do not write a nearby workbench path, guessed filename, or canonical output path that is not listed in `allowed_output_paths`.

Prepare a task:

```powershell
longform-engine continue-write novels/my-book/project.yaml --chapter 1
```

Read:

```text
novels/my-book/50_workbench/writing_tasks/ch001.md
```

Before writing or repairing, also read `../shared/creative_operator_protocol.md` and apply the `/工程续章` Pre-Write Guide:

- confirm user preference from Creative Brief and the task package,
- use automatic fallback defaults instead of asking for an extra API key,
- run the pacing precheck from `event_recommendation` and `constraint_packet.event_matrix`,
- declare the intended tail hook from `writing_brief.chapter_hook`,
- perform forbidden reveal confirmation from `reverse_brake.forbidden_reveals`, `this_chapter_must_not_solve`, and `must_keep_suspense`,
- follow the five-step closed loop: task package -> Agent draft -> `draft submit` -> `gate-check` -> `chapter finalize` or repair.

Confirm the writing task includes Creative Brief, Writer Craft Brief, RAG, Graph, TCS, Character Memory, Style Memory, Event Matrix, Reverse Brake, and Humanizer v2 rules.

Write ClaudeCode draft:

```text
novels/my-book/50_workbench/agent_drafts/ch001.claude.md
```

Submit and finalize:

```powershell
longform-engine draft submit novels/my-book/project.yaml --chapter 1 --file novels/my-book/50_workbench/agent_drafts/ch001.claude.md --agent claude
longform-engine chapter finalize novels/my-book/project.yaml --chapter 1 --approved-by human
```

## Hard Stops

- `/工程开书` must not overwrite an existing `project.yaml`; existing projects use normal `open-book`.
- `/工程续章` must not bypass a failed previous chapter gate.
- `/工程续章` must not continue from a previous chapter that is not final.
- `/工程提交稿` must submit only files under `50_workbench/agent_drafts/`.
- `/工程定稿` must run only through `chapter finalize`; direct final edits are forbidden.
- `/工程入库` must not run before the item has been reviewed; run `/工程影响分析` first when unsure.
- `/工程回滚` must keep detached drafts and must be followed by `/工程回滚影响`.
- Direct edits to canon manuscript, RAG, story graph, TCS, or SQLite are never a replacement for CLI commands.
- Humanizer, repair, and editorial outputs are candidates or workbench review artifacts only; they must still go through `draft submit` and `chapter finalize`.

## Common Mappings

```text
/工程开书      -> longform-engine open-book --interactive
/工程开书      -> longform-engine open-book project.yaml
/工程下一步    -> longform-engine production next project.yaml
/工程工单      -> longform-engine agent-task brief project.yaml TASK_OR_PATH
/工程生产状态  -> longform-engine production status project.yaml
/工程生产看板  -> longform-engine production board project.yaml
/工程推进      -> longform-engine production loop project.yaml --no-apply
/工程续章      -> longform-engine continue-write project.yaml --chapter N
/工程章节卡    -> longform-engine plan-chapter project.yaml --chapter N
/工程分镜      -> longform-engine beat project.yaml --chapter N
/工程提交稿    -> longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.claude.md --agent claude
/工程定稿      -> longform-engine chapter finalize project.yaml --chapter N --approved-by human
/工程修章      -> longform-engine repair-chapter project.yaml --chapter N --plan-only
/工程验稿      -> longform-engine gate-check project.yaml --chapter N
/工程放行      -> longform-engine gate-waiver project.yaml --chapter N --reason "reason"
/工程添加资料  -> longform-engine research add project.yaml --file note.md
/工程联网检索  -> longform-engine research search project.yaml "query"
/工程影响分析  -> longform-engine impact-analyze project.yaml --research-item research_id
/工程入库      -> longform-engine research promote project.yaml --item research_id
/工程重写分支  -> longform-engine revision branch project.yaml --chapter N
/工程回滚      -> longform-engine revision rollback project.yaml --to-chapter N
/工程回滚影响  -> longform-engine impact-analyze project.yaml --after-rollback
```

`/工程推进` is the production loop --no-apply path; it advances deterministic steps only and must pause at Agent, human approval, apply, and finalize boundaries.

## Response Discipline

Use `../shared/artifact_reporting.md` for final response content. Always include the command executed, key files written, gate/research/revision status, and the next safe command.
