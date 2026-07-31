# Agent Collaboration Hardening Checklist

本文档用于校验 `longform-novel-engine` 的 Agent 协作层是否真正达到“无外部 Key、宿主 Agent 执行智能、CLI 验收和落盘”的生产级边界。

## 1. Status Legend

- `[ ]` 未开始。
- `[~]` 已有雏形，但未达到强验收标准。
- `[x]` 已实现，并有可重复验证方式。

## 2. Manifest Strict Validation

目标：`AgentTaskManifest v1` 不只做字段形状校验，还要做 strict manifest validation。

- [x] 已有 `AgentTaskManifest v1` 基础字段。
- [x] 已有 `hard_boundaries`，包含 `no final`、`no rag`、`no graph direct`、`no sqlite direct`。
- [x] `task_type` 已被各任务使用，并有闭合集合强校验。
- [x] `task_type` 必须限制为 `chapter_write`、`repair`、`humanize`、`content_expand`、`graph_extract`、`memory_extract`、`character_memory`、`editorial_review`、`pacing_review`。
- [x] 每种 `task_type` 必须有固定 allowed output lane。
- [x] 每种 `task_type` 必须有固定 `output_schema` 名称或 schema family。
- [x] `validate_command` 必须匹配该任务类型。
- [x] `apply_command` 必须匹配该任务类型。
- [x] `failure_next_command` 必须存在且可执行。
- [x] 新增或强化 `longform-engine agent-task validate project.yaml TASK_OR_PATH --strict`。

推荐测试：

- `tests/test_agent_task_protocol.py`
- `tests/test_cli.py`
- `scripts/release_surface_guards.py`

验收查询：

```powershell
rg -n "AgentTaskManifest|strict|task_type|allowed_output_paths|validate_command|apply_command|failure_next_command" longform-novel-engine/src longform-novel-engine/tests
```

## 3. No-Pollution Boundary

目标：Agent 输出不得污染 canonical state，形成 canonical no-pollution boundary。

- [x] Agent 正文草稿被限制在 `50_workbench/agent_drafts/`。
- [x] repair、humanizer、expand 候选稿使用 `50_workbench/repair_candidates/`。
- [x] graph、memory、editorial、pacing 语义输出使用 `50_workbench/` 下对应目录。
- [x] `draft submit` 会把候选草稿提升到 `40_manuscript/draft/` 并跑 gate。
- [x] `chapter finalize` 是进入 `40_manuscript/final/` 的正式入口。
- [~] 已有 release guard、strict validator 和 no-pollution 测试，但还不是 OS 级沙箱。
- [x] strict validator 必须拒绝任何指向 `40_manuscript/final/`、`60_rag/`、`30_state/story_graph.json`、`30_state/tcs/`、`70_runtime/db/` 的 Agent output。
- [x] 所有 apply/finalize 命令都必须写入 transaction report，当前覆盖 `chapter finalize`、`graph semantic-apply`、`memory semantic-apply`、`memory character-apply`、`pacing semantic-apply`。

推荐测试：

- `tests/test_agent_task_protocol.py`
- `tests/test_engine_capability_baseline.py`
- `tests/test_e2e_agent_skill.py`

验收查询：

```powershell
rg -n "no final|no rag|no graph direct|no sqlite direct|final|60_rag|story_graph|SQLite|agent_drafts|repair_candidates" longform-novel-engine/docs longform-novel-engine/src longform-novel-engine/tests
```

## 4. Lifecycle State Machine

目标：Agent task index 能支撑 GUI/API 级别的任务队列和审计。

- [x] 已有 `50_workbench/agent_tasks/agent_task_index.json`。
- [x] 已有基础 `status` 字段。
- [x] 状态机已扩展为闭合集合，不再只停留在 `awaiting_agent`。
- [x] 支持 `awaiting_agent`。
- [x] 支持 `submitted`。
- [x] 支持 `validated`。
- [x] 支持 `invalid`。
- [x] 支持 `applied`。
- [x] 支持 `superseded`。
- [x] 支持 `rolled_back`。
- [x] 新增 `50_workbench/agent_tasks/events.jsonl`。
- [x] 每次状态变化记录 task_id、from_status、to_status、command、artifact、result、created_at。

推荐测试：

- `tests/test_agent_task_protocol.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "awaiting_agent|submitted|validated|invalid|applied|superseded|rolled_back|events.jsonl|agent_task_index" longform-novel-engine/src longform-novel-engine/tests
```

## 5. Apply Transaction

目标：canonical 写入有统一事务和 transaction rollback。

- [x] apply/finalize 已有受控命令边界。
- [x] 新增统一 transaction helper。
- [x] `chapter finalize` 进入 transaction。
- [x] `graph semantic-apply` 进入 transaction。
- [x] `memory semantic-apply` 进入 transaction。
- [x] `memory character-apply` 进入 transaction。
- [x] `pacing semantic-apply` 进入 transaction。
- [x] RAG rebuild/sync 进入 transaction 或记录可恢复报告。
- [x] SQLite sync/rebuild 进入 transaction 或记录可重建边界。
- [x] apply 失败时恢复 touched paths。
- [x] apply 成功时写 `70_runtime/transactions/YYYYMMDDTHHMMSSffffffZ_<command>_chNNN.json`。
- [x] rollback 时写 `70_runtime/transactions/YYYYMMDDTHHMMSSffffffZ_<command>_chNNN.rollback.json`。

推荐测试：

- `tests/test_graph.py`
- `tests/test_semantic_memory.py`
- `tests/test_orchestration.py`
- `tests/test_revision.py`

验收查询：

```powershell
rg -n "transaction|rollback|touched_paths|semantic-apply|finalize_chapter|sync_database" longform-novel-engine/src longform-novel-engine/tests
```

## 6. Chapter Write / Repair / Humanizer / Expand

目标：章节写作、修章、润色、扩写全部使用同构任务协议。

- [x] `continue-write` 生成 chapter writing task。
- [x] `chapter_write` manifest 已存在。
- [x] Agent draft 输出到 `50_workbench/agent_drafts/`。
- [x] `draft submit` 负责验收并跑 gate。
- [x] `repair` manifest 已有雏形。
- [x] `humanize` manifest 已有雏形。
- [x] `creative expand-task/check` 已升级为 `content_expand` manifest。
- [x] `content_expand` manifest 必须声明 input files。
- [x] `content_expand` manifest 必须声明 allowed output lane。
- [x] `content_expand` validate 必须是 `creative expand-check`。
- [x] `content_expand` apply 必须是 `draft submit --overwrite`。
- [x] repair/humanize/expand 失败后必须给出 next command。

推荐测试：

- `tests/test_agent_task_protocol.py`
- `tests/test_creative_operator.py`
- `tests/test_orchestration.py`

验收查询：

```powershell
rg -n "chapter_write|repair|humanize|content_expand|expand-task|expand-check|draft submit|repair_candidates" longform-novel-engine/src longform-novel-engine/tests
```

## 7. Graph / Memory / Character Memory

目标：图谱抽取、语义记忆、角色记忆全部遵守 Agent JSON -> validate -> apply。

- [x] `graph semantic-task` 生成 Agent 任务。
- [x] `graph semantic-validate` 校验 JSON。
- [x] `graph semantic-apply` 才修改 canonical graph。
- [x] `memory semantic-task` 生成 Agent 任务。
- [x] `memory semantic-validate` 校验 JSON。
- [x] `memory semantic-apply` 才写 memory/RAG 派生区。
- [x] `memory character-task` 生成角色记忆任务。
- [x] `memory character-validate` 校验角色记忆 JSON。
- [x] `memory character-apply` 才写角色记忆。
- [x] 上述 validate/apply 必须更新 Agent task lifecycle。
- [x] 上述 apply 必须进入 transaction。

推荐测试：

- `tests/test_graph.py`
- `tests/test_semantic_memory.py`
- `tests/test_agent_task_protocol.py`

验收查询：

```powershell
rg -n "graph_extract|memory_extract|character_memory|semantic-task|semantic-validate|semantic-apply|character-validate|character-apply" longform-novel-engine/src longform-novel-engine/tests
```

## 8. Editorial Team

目标：编辑团队成为可审计 fan-out/fan-in 协议，而不是 CLI 内部 LLM 调用。

- [x] `editorial review` 可生成多角色任务。
- [x] `editorial_review` manifest 已有雏形。
- [x] Agent 角色输出写入 `50_workbench/editorial_reviews/results/`。
- [x] `editorial submit-review` 校验单角色 JSON。
- [x] `editorial aggregate` 汇总 P0/P1/P2 和 need-human。
- [x] aggregate 必须显式报告缺失角色。
- [x] aggregate 必须显式报告重复角色结果。
- [x] aggregate 必须显式报告 invalid role result。
- [x] unresolved P0/P1 必须阻断 finalize。
- [x] repeated conditional pass 必须触发 need-human。
- [x] editorial submit/aggregate 必须更新 Agent task lifecycle。

推荐测试：

- `tests/test_agent_task_protocol.py`
- `tests/test_engine_capability_baseline.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "editorial_review|submit-review|aggregate|need-human|P0|P1|conditional_pass|unresolved" longform-novel-engine/src longform-novel-engine/tests
```

## 9. Semantic Pacing

目标：节奏语义判断由 Agent 输出 JSON，CLI 固化 gate 结果。

- [x] `pacing semantic-task` 生成 Agent 任务。
- [x] `pacing semantic-validate` 校验 `semantic_pacing_result.json`。
- [x] `pacing semantic-apply` 更新 gate artifacts。
- [x] P0/P1 节奏问题可以让 gate failed。
- [x] apply 不直接写 final/RAG/graph/SQLite。
- [x] pacing validate/apply 必须更新 Agent task lifecycle。
- [x] pacing apply 必须进入 transaction 或轻量 gate-artifact transaction。

推荐测试：

- `tests/test_agent_task_protocol.py`
- `tests/test_gates.py`
- `tests/test_creative_operator.py`

验收查询：

```powershell
rg -n "pacing_review|semantic_pacing|semantic-validate|semantic-apply|gate_result|tail_hook|reverse_brake" longform-novel-engine/src longform-novel-engine/tests
```

## 10. Auto-Write Scheduler

目标：`auto-write run` 是 scheduler，不是 writer。

- [x] `auto-write plan` 创建调度状态。
- [x] `auto-write run` 遇到 Agent task 会暂停。
- [x] `auto-write run` 不自动调用 LLM。
- [x] gate failed 会暂停并给 repair next command。
- [x] gate-approved but not finalized 会暂停并给 finalize next command。
- [x] auto-write 状态应能引用最新 Agent task status。
- [x] auto-write 应能识别 awaiting semantic output、awaiting repair candidate、awaiting editorial result。

推荐测试：

- `tests/test_orchestration.py`
- `tests/test_cli.py`

验收查询：

```powershell
rg -n "auto_write|awaiting_agent_draft|awaiting_finalize|paused_gate_failed|next_command|agent_skill_scheduler" longform-novel-engine/src longform-novel-engine/tests
```

## 11. Release Guard

目标：CI 阻止脚本内 LLM 调用、隐藏 API key 需求和 Agent output 直连 canonical 写入。

- [x] release guard 检查 direct external LLM call/import pattern。
- [x] release guard 检查外部 LLM API key 字符串。
- [x] release guard 检查公开运行时不包含 provider 占位模式。
- [x] release guard 检查部分 Agent output/canonical coupling。
- [x] release guard 增加 strict manifest validation 文档/测试入口。
- [x] release guard 增加 `content_expand` manifest 覆盖检查。
- [x] release guard 增加 lifecycle states 覆盖检查。
- [x] release guard 增加 transaction rollback 覆盖检查。

推荐测试：

- `scripts/release_surface_guards.py`
- `tests/test_agent_skill_integrity.py`
- `tests/test_capability_gap_test_plan.py`

验收查询：

```powershell
python longform-novel-engine/scripts/release_surface_guards.py
```

## 12. No-Key E2E

目标：完整章节主链路不需要 OpenAI、Anthropic 或其他外部 provider API key。

- [x] no-key chapter loop 已有测试雏形。
- [x] provider 占位模式已从公开配置和运行时移除。
- [x] `agent_skill` 默认依赖宿主 Agent。
- [x] E2E 覆盖 `continue-write -> Agent fixture draft -> draft submit -> gate -> chapter finalize`。
- [x] E2E 覆盖 `finalize -> graph semantic-task -> fixture JSON -> validate/apply`。
- [x] E2E 覆盖 `finalize -> memory character-task -> fixture JSON -> validate/apply`。
- [x] E2E 覆盖 invalid graph/memory/editorial/pacing 不污染 canonical。
- [x] E2E 覆盖 repair/humanize/expand 分支。

推荐测试：

- `tests/test_e2e_agent_skill.py`
- `tests/test_agent_task_protocol.py`

验收查询：

```powershell
rg -n "OPENAI_API_KEY|ANTHROPIC_API_KEY|api_provider|agent_skill|no key|no-key" longform-novel-engine/src longform-novel-engine/tests
```

## 13. Definition of Done

本硬化方案完成时，必须同时满足：

- [x] 所有 Agent 智能任务都有 `AgentTaskManifest v1`。
- [x] 所有 manifest 通过 strict validation。
- [x] 所有 Agent 输出路径都有 allowed output lane。
- [x] 所有任务都有 validate/apply/failure command。
- [x] 所有 canonical 写入都只能由 apply/finalize 命令完成。
- [x] 所有 apply/finalize 都有 transaction 或可恢复报告。
- [x] invalid Agent 输出不会污染 final/RAG/graph/TCS/SQLite。
- [x] Agent task lifecycle 可被 GUI/API 读取。
- [x] release guard 能挡住脚本内 LLM 调用和隐藏 API key 需求。
- [x] no-key E2E 通过。
- [x] 文档、checklist、README/AGENTS 引用保持同步。
