# Agent Experience Orchestration

本文档定义 `longform-novel-engine` 的 Agent 体验层编排方向。底层 Agent 协作层已经通过兼容 v1 的 `AgentTaskManifest v2`、strict validation、no-pollution boundary、apply transaction、release guard 和 no-key E2E 固化为安全底座；体验层要让用户、Codex、Claude、未来 GUI/API 能一眼看出当前卡点、下一步动作、允许写入位置和验收命令。

体验层不是新的写作引擎。它只汇总状态、渲染任务、推进确定性命令和提示下一步，不在 Python 内部调用 LLM，不生成正文，不绕过 manifest、validate、apply、finalize。

## 1. 背景

当前 longform 的底层协议已经解决了“安全地让宿主 Agent 执行智能工作”的问题：

- 章节写作、修章、Humanizer、图谱抽取、角色记忆、编辑团队、节奏审查都已收敛到 Agent task。
- Agent 输出只能进入 `50_workbench/` 对应 lane。
- canonical 写入只能由 `draft submit`、`chapter finalize` 或各类 validated apply 命令完成。
- invalid Agent 输出不会污染 final/RAG/graph/TCS/SQLite。

但用户体验仍偏工程管线。信息分散在 manifest、task markdown、gate artifacts、validation reports、transaction reports、auto-write state 和 `next_command` 中。体验层要把这些散点组织成生产现场指挥台。

## 2. 非目标

体验层明确不做以下事情：

- 不调用 OpenAI、Anthropic、Gemini、DeepSeek、OpenRouter 或其他外部 LLM。
- 不在 Python 脚本内自动写正文、修章、润色、审稿意见或语义判断。
- 不绕过兼容 v1 的 `AgentTaskManifest v2`。
- 不绕过 validate/apply/finalize。
- 不直接写 `40_manuscript/final/`、`60_rag/`、`30_state/story_graph.json`、`30_state/tcs/`、`70_runtime/db/`。
- 不发明 GUI/API 专用的第二套 workflow。

体验层可以运行确定性命令，例如 status、validate、aggregate、gate-check、report 渲染；一旦遇到需要 Agent 创作或语义判断的环节，必须暂停并输出 work order。

## 3. 体验层目标

体验层的核心目标是把“安全协议”变成“可执行现场”：

- 从项目状态中推断当前章节、阻断原因、等待任务和下一步命令。
- 将任意 Agent task 渲染成人和 Agent 都能直接执行的 work order。
- 以章节为单位展示 final、draft、gate、repair、semantic、editorial、memory、graph 状态。
- 安全推进确定性步骤，直到遇到需要 Agent 输出、人工确认或 canonical apply/finalize 的阻断点。
- 为未来 GUI/API 提供稳定 JSON contract，而不是让 GUI/API 自己解析散落文件。

## 4. Next Action Center

Next Action Center 是体验层的统一“下一步”入口。它应该回答：

- 当前卡在哪一章。
- 当前阻断类型是什么。
- 等待谁输出：Agent draft、repair candidate、semantic JSON、editorial role result、human approval。
- Agent 必须读取哪些输入文件。
- Agent 只能写入哪些路径。
- 输出 schema 是什么。
- 写完后运行哪条 validate 或 submit 命令。
- validate/apply 失败后运行哪条 fallback 命令。
- 哪些 canonical boundary 不允许触碰。

建议 CLI：

```powershell
longform-engine production next project.yaml
longform-engine production next project.yaml --json
```

已落地的第一阶段实现是 `longform-engine production next project.yaml [--json]`。它是只读 Next Action Center：读取 `AgentTaskManifest v1/v2`、`agent_task_index.json`、`gate_result.json`、draft/final 文件存在性和 editorial aggregate，不执行 submit、validate、apply、finalize，也不写 final/RAG/graph/SQLite。

当前优先级顺序：

1. editorial `need_human`。
2. active Agent task：`invalid`、`awaiting_agent`、`submitted`、`validated`。
3. gate failed / awaiting finalize。
4. draft without gate。
5. existing writing task awaiting Agent draft。
6. no blocker 时输出下一章 `continue-write`。

建议 JSON 顶层字段：

- `status`
- `chapter_number`
- `blocked_by`
- `waiting_for`
- `task_id`
- `task_type`
- `input_files`
- `allowed_output_paths`
- `output_schema`
- `validate_command`
- `apply_command`
- `failure_next_command`
- `hard_boundaries`
- `next_command`
- `human_summary`

优先级建议：

1. failed/invalid canonical-risk state。
2. awaiting Agent output。
3. validated but not applied semantic output。
4. gate passed but not finalized。
5. no task exists and next chapter can generate `continue-write`。
6. completed target。

## 5. Agent Work Order Renderer

Work Order Renderer 将 `AgentTaskManifest v1/v2` 渲染为可直接交给宿主 Agent 的 Markdown。它不能改变任务，只能解释任务。

建议 CLI：

```powershell
longform-engine agent-task brief project.yaml TASK_OR_PATH
longform-engine agent-task brief project.yaml TASK_OR_PATH --json
```

已落地的第一阶段实现是 `longform-engine agent-task brief project.yaml TASK_OR_PATH [--json]`。它是只读 Work Order Renderer：读取指定 `AgentTaskManifest v1/v2`，输出同源的 JSON payload 和 Markdown work order，不修改 manifest、agent task index、events、final/RAG/graph/SQLite。

当前 JSON 顶层字段：

- `renderer`
- `read_only`
- `manifest_file`
- `task_id`
- `task_type`
- `chapter_number`
- `status`
- `work_scope`
- `input_files`
- `allowed_output_paths`
- `output_schema`
- `validate_command`
- `apply_command`
- `failure_next_command`
- `hard_boundaries`
- `manifest_validation`
- `completion_report_template`
- `work_order_markdown`

Markdown 必须包含：

- Task id、task type、chapter number、status。
- 本次只允许完成的工作。
- 明确输入文件列表。
- 明确允许写入路径。
- 输出 schema 或 markdown-only 约束。
- validate 命令。
- apply 命令。
- 失败后的 next command。
- hard boundaries：至少包含 no final、no rag、no graph direct、no sqlite direct。
- 完成后向用户汇报的最短格式。

不同任务的 brief 只改变“角色说明”和“输出要求”，不改变安全边界。例如：

- `chapter_write`: 写正文 Markdown 到 `50_workbench/agent_drafts/`。
- `repair`: 写候选稿到 `50_workbench/repair_candidates/`。
- `graph_extract`: 输出 semantic graph JSON。
- `editorial_review`: 输出 role review JSON。
- `pacing_review`: 输出 semantic pacing result JSON。

## 6. Production Board

Production Board 是章节生产看板。它按章节展示用户关心的状态，而不是暴露一堆文件路径。

建议 CLI：

```powershell
longform-engine production board project.yaml
longform-engine production board project.yaml --from 1 --to 20
longform-engine production board project.yaml --json
```

已落地的第一阶段实现是 `longform-engine production board project.yaml [--from N --to M --json]`。它是只读 Production Board：按章节聚合 draft/final/gate、Agent task lanes、editorial aggregate、latest transaction 和 latest run report，不执行 validate、apply、finalize，也不写 final/RAG/graph/SQLite。

当前 JSON 顶层字段：

- `board_version`
- `read_only`
- `from_chapter`
- `to_chapter`
- `chapters`
- `totals`
- `sources`

每个 `chapters[]` 行包含：

- `chapter_number`
- `draft_status`
- `final_status`
- `gate_status`
- `repair_status`
- `humanize_status`
- `expand_status`
- `graph_status`
- `memory_status`
- `character_memory_status`
- `semantic_pacing_status`
- `editorial`
- `agent_tasks`
- `latest_transaction`
- `latest_report`

每章建议展示：

- chapter number。
- draft status：none、agent_task、draft_submitted、gate_passed、gate_failed。
- final status：missing、finalized。
- gate severity 和 next command。
- repair/humanize/expand status。
- graph semantic status。
- memory semantic status。
- character memory status。
- editorial status 和 need-human。
- pacing semantic status。
- transaction/report summary。

Board 的作用是帮助用户理解“这本书现在生产到哪里”，不是替代具体 validate/apply 命令。

## 7. Safe Loop Driver

Safe Loop Driver 是比 `auto-write` 更靠近用户体验的安全推进器。`auto-write` 是 scheduler；`production loop` 是生产现场的“推进到下一个安全阻断点”。

建议 CLI：

```powershell
longform-engine production loop project.yaml
longform-engine production loop project.yaml --max-steps 10
longform-engine production loop project.yaml --no-apply
```

允许自动执行的动作：

- 读取状态、刷新 status/report。
- 在无任务且安全时生成 `continue-write`。
- 对已经存在的 Agent 输出运行 validate 或 submit。
- 聚合已 accepted 的 editorial role result。
- 运行 deterministic gate-check。
- 写 Markdown/JSON 报告。

必须暂停的动作：

- 需要 Agent 写正文、修章、润色、扩写、语义 JSON 或审稿 JSON。
- 需要人工确认 finalize。
- 需要 canonical apply，且默认 `--no-apply` 生效。
- validate 失败。
- gate P0/P1 失败。
- need-human。
- release guard 或 manifest strict validation 失败。

默认策略：

- `--no-apply` 默认为 true。
- 即使未来支持自动 apply，也只允许对已 validated、低风险、非 final 的 gate-artifact 类任务开放。
- `chapter finalize` 永远要求显式人工命令或 GUI/API 明确确认。

当前实现：

- `production loop` 已落地为 `production_loop_v1`，输出 `steps`、`pause_reason`、`next_action` 和 `hard_boundaries`。
- 它复用 `production next` 选择下一阻断点，不维护第二套优先级。
- 可自动执行 `continue_write`、`gate_check`、已有 Agent 输出的 `draft submit`、humanize/expand/graph/memory/character/pacing validate、editorial submit-review，以及 validated editorial task 的 aggregate。
- 遇到 awaiting Agent output、invalid task、gate failed、need-human、human finalize、canonical apply/finalize 时暂停。
- 默认 `no_apply=true`，当前不提供自动 canonical apply/finalize 开关。
- 它不得绕过既有 CLI 边界直接写 final/RAG/graph/SQLite；派生 SQLite 同步只能来自被调用的 deterministic CLI 流程。
- 测试覆盖见 `tests/test_production_experience.py` 和 `tests/test_cli.py`。

## 8. Editorial Team UX

编辑团队底层已经是 fan-out/fan-in 协议。体验层需要让用户看到“还缺哪个编辑角色”和“为什么 need-human”。

建议展示：

- expected roles。
- accepted roles。
- missing roles。
- duplicate role results。
- invalid role results。
- unresolved P0/P1/P2 counts。
- blocking verdict。
- conditional pass streak。
- need-human reasons。
- next command。

建议 CLI：

```powershell
longform-engine production board project.yaml --editorial
longform-engine production next project.yaml --editorial
```

体验层不得自动伪造任何 role result。缺失角色必须继续显示为 awaiting Agent output。

当前实现：

- `production board --json` 的每个章节 `editorial` 字段已包含 `expected_roles`、`accepted_roles`、`missing_roles`、`duplicate_role_results`、`invalid_results`、`severity_counts`、`conditional_passes`、`need_human_reasons_readable` 和 `role_statuses`。
- `role_statuses` 按角色列出 `role_id`、`display_name`、`focus`、`status`、`task_id`、`work_order_file`、`result_file`、`validate_command`、`accepted`、`missing`、`invalid_result` 和 `duplicate_result`。
- `production board --editorial` 会在文本视图中展开角色缺口、duplicate/invalid 数量、need-human 原因和每个 role 的当前状态。
- `production next --json` 遇到 `editorial_review` Agent task 时，会输出 `editorial_role` 工作单摘要，包括 role focus、work order、result file、schema、validate/apply/failure command 和 hard boundaries。
- `production next --editorial` 会把当前 role-specific work order 或 need-human aggregate 摘要渲染成人类可读文本。
- need-human 原因同时保留机器可读 code 和 `need_human_reasons_readable`，下一步仍指向 `editorial need-human` 或 repair/finalize 命令。
- 测试覆盖见 `tests/test_production_experience.py` 和 `tests/test_cli.py`。

## 9. GUI/API 接入

GUI/API 必须复用同一批事实源：

- `AgentTaskManifest v1/v2`
- `50_workbench/agent_tasks/agent_task_index.json`
- `50_workbench/agent_tasks/events.jsonl`
- `gate_result.json`
- validation reports
- transaction reports
- `70_runtime/auto_write_state.json`
- `40_manuscript/chapter_meta.jsonl`

GUI/API 可缓存和聚合这些数据，但不能把缓存当 canonical state。所有状态变更仍必须经 CLI 或同等服务端命令路径进入 validate/apply/finalize。

建议 API 资源：

- `GET /production/status`
- `GET /production/next`
- `GET /production/board?from=N&to=M`
- `GET /agent-tasks/{task_id}/brief`
- `POST /production/loop`

这些 API 是 CLI JSON contract 的远程包装，不是第二套 workflow。

当前 JSON contract：

- `production status --json` 已落地为 `production_status_v1`，输出 `schema_version`、`status_version`、`read_only`、`path_style`、`command_style`、`redaction`、`current`、`next_action`、`agent_tasks`、`board`、`resources` 和 `sources`。
- `production next --json`、`production board --json`、`agent-task brief --json`、`production loop --json` 均保留显式 version/renderer 字段，供 GUI/API 做兼容判断。
- JSON 中的项目文件路径必须使用项目相对路径；`production loop` 会把 deterministic step result 中的绝对路径归一化为项目相对路径。
- 可执行命令字段必须是可复制的 `longform-engine ...` 字符串，包括 `next_command`、`validate_command`、`apply_command`、`failure_next_command` 和 loop step command。
- GUI/API contract 不返回章节正文全文、不返回外部 provider API key、不返回完整 prompt 日志。需要正文时，GUI/API 只能按 manifest 的 `input_files` 显式读取，并继续遵守 lane 权限。
- 建议资源固定为 `GET /production/status`、`GET /production/next`、`GET /production/board?from=N&to=M`、`GET /agent-tasks/{task_id}/brief`、`POST /production/loop`。
- 测试覆盖见 `tests/test_production_experience.py` 的 JSON contract 断言，以及 `tests/test_cli.py` 的只读/可变命令分类。

## 10. CLI 建议接口

体验层第一阶段建议实现以下 CLI：

```powershell
longform-engine production status project.yaml [--json]
longform-engine production next project.yaml [--json]
longform-engine production board project.yaml [--from N --to M --json]
longform-engine agent-task brief project.yaml TASK_OR_PATH [--json]
longform-engine production loop project.yaml [--max-steps N --no-apply]
```

接口边界：

- `production status`: 只读或写可恢复 report，不改变 canonical。
- `production next`: 只读，输出最高优先级 next action。
- `production board`: 只读，聚合章节状态。
- `agent-task brief`: 只读，将 manifest 渲染成工作单。
- `production loop`: 可执行确定性命令，但遇到 Agent output、human approval、apply/finalize 时暂停。

## 11. Tests and Fixtures

体验层测试必须独立存在，不能只依赖底层 Agent task、gate、editorial 或 orchestration 测试间接兜底。

当前实现：

- `tests/test_production_experience.py` 覆盖 `production status/next/board/loop` 和 `agent-task brief` 的主要 JSON/text contract。
- `tests/test_cli.py` 覆盖 `production status/next/board/loop` 与 `agent-task brief` 的 read-only / mutating command 分类。
- `tests/test_agent_skill_integrity.py` 覆盖 release guard 对体验层 no-LLM/no-pollution contract 的静态约束。
- `test_production_fixture_matrix_covers_blocking_states` 显式构造 gate failed、awaiting repair、awaiting semantic、awaiting editorial、awaiting finalize 五类生产现场 fixture。
- `test_production_json_contract_uses_relative_paths_and_redacts_body` 覆盖 JSON contract 的相对路径、命令可复制、正文/API key/prompt 日志不泄漏。
- `test_production_loop_no_pollution_pause_path` 覆盖 loop 在 gate passed 但未 finalize 时暂停，且不写 final/RAG/story graph/canonical SQLite rows。

## 12. Definition of Done

第一阶段体验层编排已达到以下完成标准：

- `production status/next/board/loop` CLI 已实现，并有 JSON/text 双输出。
- `agent-task brief` CLI 已实现，并可渲染所有 `AgentTaskManifest v1/v2` 支持的 task type。
- Next Action Center、Work Order Renderer、Production Board、Safe Loop Driver、Editorial Team UX 和 GUI/API JSON contract 均已进入同一套 `src/longform_engine/production.py` 编排模块。
- release guard 已覆盖体验层 no LLM、no hidden API key、no direct canonical write、brief read-only 等边界。
- 体验层测试和 fixture 覆盖 gate failed、awaiting repair、awaiting semantic、awaiting editorial、awaiting finalize 等阻断状态。
- README、项目级 AGENTS、仓库级 AGENTS、协作层硬化文档和体验层 checklist 已同步引用本设计。

最终验收以 `docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md` 第 10 项为准。

## 13. 风险与边界

体验层的最大风险是为了追求 one-click 现场感而重新引入脚本内 LLM 或绕过验收边界。必须坚持：

- no LLM in Python CLI。
- no final direct write。
- no rag direct write。
- no graph direct write。
- no sqlite direct write。
- no hidden API key requirement。
- no GUI/API-only workflow。
- no automatic chapter finalize。

当前实现：

- `scripts/release_surface_guards.py` 已包含 experience layer release guard，固定检查 `production status/next/board/loop` 和 `agent-task brief` 的 CLI/module marker。
- release guard 会继续扫描生产源码中的直接外部 LLM import/call pattern 和隐藏 API key 字符串。
- release guard 会检查 `production.py` 不出现直接 writer pattern，包括 `atomic_write_text`、`write_json`、`.write_text()`、`sync_database`、`apply_transaction`、`INSERT INTO`、`sqlite3` 等。
- `agent_task_brief` 必须保持 `"read_only": True`，且不得出现直接 writer pattern。
- `production_loop` 不得直接写 final/RAG/graph/SQLite；它只能调用既有 deterministic pipeline 命令，并在 Agent/human/apply/finalize 阻断点暂停。
- no-pollution E2E 已覆盖 `production loop` 通过 gate 后暂停 finalize 的路径：不得写 final，不得写 RAG chunk，不得改 story graph，不得在派生 SQLite 视图中产生 final/chunk/event/entity canon 行。

体验层成功的标志不是“全自动写完一本书”，而是“每次暂停都清楚、安全、可继续，并且所有智能输出都可审计”。

后续验收标准见 `docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md`。
