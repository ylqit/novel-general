# Agent Collaboration Hardening

本文档固化 `longform-novel-engine` 的 Agent 协作层硬化方向：在默认生产路径中，不要求用户提供外部 LLM API key，不在 Python CLI 内部调用 OpenAI、Anthropic 或其他 provider 来写正文、修章或做语义判断。CLI 只负责生成任务包、声明边界、校验输出、apply、finalize、门禁、回滚和索引；Codex、Claude Code 等宿主 Agent 负责所有需要创作智能和语义判断的内容。

## 1. 背景与目标

`novel-skill` 的优势是像一个已经能干活的小说生产脚本包，可以一键推进章节草稿、质量报告、RAG 重建、约束注入、图谱更新和指标汇总。但这类脚本包如果在 Python 内部直接调用 LLM，就会把创作智能、语义判断和状态写入混在一起，审计边界会变弱。

`longform-novel-engine` 的目标是反过来做：

- `agent_skill` 是默认生产模式。
- CLI 不直接生成小说正文，不伪造语义判断。
- Agent 只写入 workbench 允许路径。
- 所有 Agent 输出必须先 validate，再由 apply 或 finalize 命令进入 canonical state。
- `api_provider` 只能作为未来保留模式，默认路径必须显式禁用。
- 任何进入 `40_manuscript/final/`、`60_rag/`、`30_state/story_graph.json`、`30_state/tcs/`、`70_runtime/db/` 的内容都必须经过受控命令。

## 2. 非目标

本硬化方案不做以下事情：

- 不在 CLI 内置 OpenAI、Anthropic 或其他外部 LLM 调用。
- 不让 CLI 自动 spawn 模型或强依赖某个宿主 Agent 产品。
- 不允许 Agent 绕过 task manifest 直接写 final、RAG、graph、TCS 或 SQLite。
- 不把 deterministic Python 规则包装成真正语义判断。
- 不复制 `novel-skill` 的脚本内 LLM 调用模式。
- 不在本阶段要求操作系统级沙箱；先通过 manifest、路径白名单、validate/apply、事务、release guard 和测试建立工程边界。

## 3. AgentTaskManifest v1 强语义契约

所有需要 Agent 创作或语义判断的任务都必须生成 `AgentTaskManifest v1`。现有字段保持兼容，但要从形状校验升级到强语义校验。

标准字段：

```json
{
  "schema_version": 1,
  "task_id": "graph_extract:ch001:v1",
  "task_type": "chapter_write",
  "chapter_number": 1,
  "input_files": [],
  "allowed_output_paths": [],
  "output_schema": "markdown_chapter_only",
  "validate_command": "longform-engine ...",
  "apply_command": "longform-engine ...",
  "failure_next_command": "longform-engine ...",
  "hard_boundaries": ["no final", "no rag", "no graph direct", "no sqlite direct"],
  "status": "awaiting_agent",
  "created_at": "ISO-8601"
}
```

强语义校验要求：

- `task_type` 必须属于闭合集合：`chapter_write`、`repair`、`humanize`、`content_expand`、`graph_extract`、`memory_extract`、`character_memory`、`editorial_review`、`pacing_review`。
- 每个 `task_type` 必须绑定固定的 allowed output lane。
- 每个 `task_type` 必须绑定可识别的 `output_schema`。
- `validate_command` 必须匹配任务类型对应的 validate/check/submit 命令。
- `apply_command` 只能是受控 apply、aggregate、draft submit 或 chapter finalize 命令。
- `failure_next_command` 必须可执行，并指向重新生成任务、修章、人工升级或 need-human 分支。
- `allowed_output_paths` 禁止指向 canonical state。
- `hard_boundaries` 必须至少包含 `no final`、`no rag`、`no graph direct`、`no sqlite direct`。

推荐新增接口：

```powershell
longform-engine agent-task validate project.yaml TASK_OR_PATH --strict
longform-engine agent-task history project.yaml --chapter N
```

## 4. Canonical 写入硬边界

Agent 输出必须是候选产物，不是系统事实源。以下路径属于 canonical state 或 derived canonical index，不能由 Agent 直接写：

```text
40_manuscript/final/
60_rag/
30_state/story_graph.json
30_state/tcs/
60_rag/memory/
70_runtime/db/
```

允许 Agent 写入的路径必须位于 `50_workbench/` 下：

```text
50_workbench/agent_drafts/
50_workbench/repair_candidates/
50_workbench/graph_updates/
50_workbench/memory_tasks/
50_workbench/editorial_reviews/results/
50_workbench/gate_artifacts/chNNN/
```

硬边界规则：

- `draft submit` 可以把 Agent 草稿复制进 `40_manuscript/draft/`，并立即触发 gate。
- `chapter finalize` 是正文进入 `40_manuscript/final/` 的唯一正式入口。
- `graph semantic-apply` 是语义图谱更新进入 story graph 的唯一正式入口。
- `memory semantic-apply` 和 `memory character-apply` 是语义记忆进入 memory/RAG 派生区的正式入口。
- `pacing semantic-apply` 只允许更新 gate artifacts，不允许直接 finalize。
- editorial aggregate 只能给出 `need-human` 或下一步命令，不允许直接改 final/RAG/graph/SQLite。
- 每个 apply/finalize 受控写入口都必须写入 `70_runtime/transactions/` 审计报告，记录 source paths、touched paths、command、chapter 和 no-pollution boundary。

## 5. Agent task 生命周期状态机

当前 Agent task 生命周期已落地为闭合状态集合，并通过 `agent_task_index.json` 与 `events.jsonl` 给 GUI/API 提供可审计队列。

状态集合：

```text
awaiting_agent
submitted
validated
invalid
applied
superseded
rolled_back
```

状态含义：

| 状态 | 含义 |
| --- | --- |
| `awaiting_agent` | CLI 已生成任务包，等待 Agent 写输出。 |
| `submitted` | Agent 输出已被提交命令发现或登记。 |
| `validated` | 输出通过 schema、路径和业务校验，但还未 apply。 |
| `invalid` | 输出未通过校验，canonical state 未变化。 |
| `applied` | 输出已通过受控 apply/finalize 进入正式状态或 gate artifacts。 |
| `superseded` | 任务被更新版本、重生成任务或覆盖提交取代。 |
| `rolled_back` | apply/finalize 失败或人工回滚后，该任务对应状态已撤销。 |

审计文件：

```text
50_workbench/agent_tasks/agent_task_index.json
50_workbench/agent_tasks/events.jsonl
```

`events.jsonl` 每行记录：

```json
{
  "schema_version": 1,
  "task_id": "chapter_write:ch001:v1",
  "from_status": "awaiting_agent",
  "to_status": "submitted",
  "command": "draft submit",
  "artifact": "50_workbench/agent_drafts/ch001.codex.md",
  "result": "40_manuscript/draft/ch001.md",
  "created_at": "ISO-8601"
}
```

已接入的状态推进入口：

- `draft submit`: `awaiting_agent -> submitted -> validated|invalid`
- `chapter finalize`: `validated -> applied`
- `graph/memory/character/pacing semantic-validate`: `awaiting_agent|submitted -> validated|invalid`
- `graph/memory/character/pacing semantic-apply`: `validated -> applied`
- `editorial submit-review`: `awaiting_agent|submitted -> validated|invalid`
- `editorial aggregate`: `validated -> applied`
- `revision rollback`: affected tasks -> `rolled_back`

## 6. 统一 apply transaction

所有 canonical 写入已进入统一事务辅助层。事务不是为了替代 Git，而是为了让单次 CLI apply/finalize 在失败时可恢复。

事务边界：

- `chapter finalize`
- `graph semantic-apply`
- `memory semantic-apply`
- `memory character-apply`
- `pacing semantic-apply`
- RAG rebuild 或 sync
- SQLite sync/rebuild

事务流程：

1. 收集将被修改的 canonical paths。
2. 在 `70_runtime/transactions/snapshots/` 中快照 touched paths。
3. 执行 validate。
4. 执行 apply/finalize。
5. 成功后写 applied transaction report。
6. 失败时恢复 touched paths，并写 `.rollback.json` rollback report。
7. 相关 Agent task 保持 `invalid`，或在 revision rollback 场景被标记为 `rolled_back`。

推荐产物：

```text
70_runtime/transactions/YYYYMMDDTHHMMSSffffffZ_chapter_finalize_ch001.json
70_runtime/transactions/YYYYMMDDTHHMMSSffffffZ_graph_semantic_apply_ch001.json
70_runtime/transactions/YYYYMMDDTHHMMSSffffffZ_memory_semantic_apply_ch001.json
70_runtime/transactions/YYYYMMDDTHHMMSSffffffZ_pacing_semantic_apply_ch001.json
70_runtime/transactions/YYYYMMDDTHHMMSSffffffZ_<command>_chNNN.rollback.json
```

## 7. 七类智能任务编排

### 章节写作

```text
continue-write
  -> write chapter_write manifest
  -> Agent writes 50_workbench/agent_drafts/chNNN.codex.md
  -> draft submit
  -> gate-check
  -> chapter finalize or repair branch
```

要求：

- 输入必须包含 writing task、RAG context、chapter card、beat sheet、story graph facts、TCS、event matrix、pacing history、style profile、reverse brake。
- 输出只能是 `50_workbench/agent_drafts/chNNN.<agent>.md`。
- validate 命令是 `draft submit`。
- apply 命令是 `chapter finalize`。

### 修章

```text
repair-chapter --plan-only or candidate task
  -> Agent writes 50_workbench/repair_candidates/chNNN.<agent>.repair_candidate.md
  -> draft submit --overwrite
  -> gate-check
```

修章任务必须读取 gate result、repair plan、原 draft、chapter card、constraint packet 和必要的 pacing/reverse-brake artifacts。

### Humanizer

```text
creative humanize-task
  -> Agent writes 50_workbench/repair_candidates/chNNN.humanized_candidate.md
  -> creative humanize-check
  -> draft submit --overwrite
```

Humanizer 只负责候选润色，不进入 final。Python deterministic 规则可以检查 AI 味、模板化表达、TODO、重复、弱化副词等问题，但不能替代 Agent 的文学判断。

### Content Expansion

```text
creative expand-task
  -> Agent writes 50_workbench/repair_candidates/chNNN.expanded_candidate.md
  -> creative expand-check
  -> draft submit --overwrite
```

`content_expand` 已进入同一 manifest 协议。它用于短章、场景不足、对白单薄、心理缺失、动作细节不足和转场生硬等问题。Manifest 必须声明 source draft、gate result、repair plan、writing task、style/creative inputs，输出限制在 `50_workbench/repair_candidates/`，validate 命令为 `creative expand-check`，apply 命令为 `draft submit --overwrite`，失败后回到 `creative expand-task`。

### 图谱抽取

```text
graph semantic-task
  -> Agent writes 50_workbench/graph_updates/chNNN.semantic.json
  -> graph semantic-validate
  -> graph semantic-apply
```

validate 必须检查 source 是 final、chapter 匹配、evidence_span、confidence、from_chapter 和输出路径。apply 前不得修改 `30_state/story_graph.json`。

`graph semantic-validate` 必须把匹配的 `graph_extract` manifest 推进到 `validated` 或 `invalid`；`graph semantic-apply` 必须在事务中写 `30_state/story_graph.json` 和 SQLite 派生索引，并把任务推进到 `applied`。

### 语义记忆

```text
memory semantic-task
  -> Agent writes 50_workbench/memory_tasks/chNNN.semantic.codex.json
  -> memory semantic-validate
  -> memory semantic-apply
```

语义记忆只能基于 final chapter、TCS snapshot 和 graph facts。validate 必须拒绝 draft、agent draft、research inbox 等非 canonical source。apply 只能通过 transaction 写入 `60_rag/memory/`、reviewable graph update 文件和 SQLite 派生索引，并把 `memory_extract` manifest 推进到 `applied`。

### 角色记忆

```text
memory character-task
  -> Agent writes 50_workbench/memory_tasks/chNNN.character.codex.json
  -> memory character-validate
  -> memory character-apply
```

角色记忆只能基于 final chapter、当前 character memory、TCS 和 graph facts。Agent 输出 JSON 后，CLI 负责 schema 和 evidence 校验。

`memory character-validate` 必须把匹配的 `character_memory` manifest 推进到 `validated` 或 `invalid`；`memory character-apply` 必须在事务中写入 `60_rag/memory/characters/` 和 SQLite 派生索引，并把任务推进到 `applied`。

### 节奏审查

```text
pacing semantic-task
  -> Agent writes 50_workbench/gate_artifacts/chNNN/semantic_pacing_result.json
  -> pacing semantic-validate
  -> pacing semantic-apply
```

semantic pacing 的 apply 只能固化到 gate artifacts 和 `gate_result.json`，不能直接进入 final/RAG/graph/SQLite。

已落地的 semantic pacing 边界：

- `pacing semantic-validate` 将匹配的 `pacing_review` manifest 推进到 `validated` 或 `invalid`。
- invalid semantic pacing result 只写 `semantic_pacing_validation.json`，不修改 `gate_result.json`。
- `pacing semantic-apply` 进入 apply transaction，touched paths 只覆盖 `50_workbench/gate_artifacts/chNNN/`、`gate_result.json`、`pacing_review.md` 和 SQLite 派生索引。
- `pacing semantic-apply` 成功后将 manifest 推进到 `applied`。
- 如果 Agent 给出 P0/P1 节奏问题，apply 可以把 gate 改为 failed，并把下一步导向 repair，而不是 finalize。

## 8. 编辑团队与多角色任务

编辑团队采用 fan-out/fan-in 协议，但 CLI 不负责调用模型。

fan-out：

```text
editorial review
  -> planning_chief_editor task
  -> writing_agent task
  -> anti_ai_editor task
  -> serial_verifier task
  -> executive_editor task
```

Agent 每个角色写一个结构化 JSON：

```text
50_workbench/editorial_reviews/results/chNNN.<role>.json
```

fan-in：

```text
editorial submit-review --role ROLE --file result.json
editorial aggregate --chapter N
```

aggregate 必须汇总：

- P0/P1/P2 数量。
- blocking verdict。
- unresolved items。
- repeated conditional pass。
- role result 缺失或无效。
- duplicate role result。
- `need-human` 原因。

已落地的 fan-in 报告字段：

- `missing_roles`: editorial team 中还没有 accepted normalized result 的角色。
- `duplicate_role_results`: 同一 role 出现多个原始 Agent result 文件时的文件清单。
- `invalid_results`: `editorial submit-review` 生成的 rejected validation report。
- `conditional_passes`: 本章 accepted role results 中的 conditional pass 数量。
- `need_human_reasons`: `unresolved_P0`、`unresolved_P1`、`editorial_blocking_verdict`、`repeated_conditional_pass`、`missing_editorial_roles`、`duplicate_role_results`、`invalid_role_results` 等原因码。

如果存在 unresolved P0/P1、blocking verdict、重复 conditional pass、缺失角色、重复角色输出或 invalid role result，aggregate 必须输出 `editorial need-human`。一旦 `chNNN.aggregate.json` 存在且 `need_human=true`，`chapter finalize` 必须阻断，不允许把该章节写入 final/RAG/graph/SQLite。

生命周期要求：

- `editorial submit-review`: 对应 role manifest 进入 `validated` 或 `invalid`。
- `editorial aggregate`: 已 validated 的 role manifest 进入 `applied`。
- 缺失角色保持 `awaiting_agent`，invalid role 保持 `invalid`，供 GUI/API 展示。

## 9. Auto-Write Scheduler

`auto-write run` 是 scheduler，不是 writer。它不调用 LLM，不生成正文，不绕过 Agent task，也不直接 finalize。

已落地的暂停状态：

- `awaiting_agent_draft`: 当前章节有 `chapter_write` manifest 等待 Agent draft。
- `awaiting_repair_candidate`: 当前章节有 `repair`、`humanize` 或 `content_expand` manifest 等待候选稿。
- `awaiting_semantic_output`: 当前章节有 `pacing_review`、`graph_extract`、`memory_extract` 或 `character_memory` manifest 等待 Agent JSON。
- `awaiting_editorial_result`: 当前章节有 `editorial_review` manifest 等待角色审稿 JSON。
- `paused_gate_failed`: gate 已失败且还没有更具体的 Agent task。
- `awaiting_finalize`: gate 已通过或已 waiver，但章节还没有进入 final。

`70_runtime/auto_write_state.json` 必须包含 `agent_task_status`，用于 GUI/API 读取最新任务状态：

- `current.by_status`
- `current.by_type`
- `project.by_status`
- `project.by_type`
- `latest`
- `waiting`
- `waiting_kinds`
- `current_task_ids`

`auto-write report` 必须把 Agent task 摘要渲染到 Markdown。这样用户能看到调度器暂停是因为等待正文、修章候选、语义 JSON 还是编辑团队结果。

## 10. content expansion / style / event matrix / reverse brake 统一方式

### Content expansion

`creative expand-task/check` 已升级为 `content_expand` manifest。输出仍然是候选稿，必须经过 `draft submit --overwrite`。

### Style fingerprint

Style fingerprint 主要是 deterministic 分析和写作约束注入。它不需要默认变成 Agent 语义任务。若未来需要 Agent 做主观风格审查，应新增独立 task type，例如 `style_review`，并保持相同 validate/apply 边界。

### Event matrix

事件矩阵是 planning/gate 的状态约束，不应由 Agent 直接修改。它应该作为以下任务的输入：

- `chapter_write`
- `repair`
- `content_expand`
- `pacing_review`

### Reverse brake

反向刹车是防止提前揭露、提前解决核心悬念和 A/B/C 加速超配额的硬约束。它应该进入 writing task、repair task、pacing task 和 gate artifacts。Agent 可以基于它判断风险，但不能直接改 outline anchor 或 canonical graph。

## 11. Release Guard 合约

`scripts/release_surface_guards.py` 不只检查脚本内 LLM 调用，还必须检查关键协议入口没有被意外删掉。

Release guard 必须覆盖：

- strict manifest validation: `validate_manifest_strict` 的源码入口和 `test_strict_manifest_validation_rejects_unknown_type_and_canonical_output` 测试入口。
- `content_expand`: `TASK_CONTRACTS`、`creative expand-task/check` manifest、`markdown_expanded_candidate` schema 和测试覆盖。
- lifecycle states: `AGENT_TASK_STATUSES` 必须包含 `awaiting_agent`、`submitted`、`validated`、`invalid`、`applied`、`superseded`、`rolled_back`。
- transaction rollback: `apply_transaction` 必须写 `canonical_write_transaction_rollback`，transaction boundary 必须包含 `rollback_restores_touched_paths`，测试必须覆盖 rollback report。

这些检查属于 release-surface contract：即使单个功能测试还在，删除文档锚点、测试名、schema 名或 lifecycle 状态也会让 guard 失败。

## 12. No-Key E2E 合约

`agent_skill` 模式的端到端验收必须在没有 OpenAI、Anthropic、Gemini、DeepSeek、OpenRouter 或其他 provider API key 的环境中完成。CLI 只生成任务、接收宿主 Agent fixture 输出、校验、apply/finalize 和同步派生索引，不得在测试路径中隐式调用外部模型。

当前 no-key E2E 覆盖以下链路：

- `continue-write -> Agent fixture draft -> draft submit -> gate -> chapter finalize`。
- `chapter finalize -> graph semantic-task -> fixture semantic graph JSON -> semantic-validate -> semantic-apply`。
- `chapter finalize -> memory character-task -> fixture character memory JSON -> character-validate -> character-apply`。
- invalid graph/memory/editorial/pacing Agent 输出只写 validation/report，不污染 `40_manuscript/final/`、`60_rag/`、`30_state/story_graph.json`、`30_state/tcs/` 或 `70_runtime/db/`。
- gate fail 后的 `repair-chapter --plan-only`、`repair-chapter --candidate-only`、`creative humanize-task/check`、`creative expand-task/check`、`draft submit --overwrite` 分支。

这些测试不是替代 schema/unit 测试，而是证明“无外部 key 的宿主 Agent 协作系统”在真实 CLI 命令流下可运行。后续新增智能任务时，必须补同类 fixture E2E：任务生成、Agent 输出、validate、apply/finalize、失败 no-pollution 和 next command。

## 13. GUI/API 未来接入方式

GUI/API 不应发明第二套工作流，应直接消费这些文件和命令：

- `AgentTaskManifest v1`
- `50_workbench/agent_tasks/agent_task_index.json`
- `50_workbench/agent_tasks/events.jsonl`
- validate report
- gate artifacts
- transaction reports
- `next_command`

推荐 GUI/API 状态视图：

- 等待 Agent 的任务。
- 已提交但未校验的输出。
- 校验失败的输出和失败原因。
- 已通过但未 apply/finalize 的输出。
- need-human 阻断。
- 可继续执行的 next command。

体验层编排的下一阶段设计见 `docs/AGENT_EXPERIENCE_ORCHESTRATION.md`，验收清单见 `docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md`。该层只负责 Next Action Center、Agent Work Order Renderer、Production Board、Safe Loop Driver 和 GUI/API JSON contract，不改变本硬化文档定义的 no LLM、no final、no rag、no graph direct、no sqlite direct 边界。

## 14. Definition of Done 对齐

当前硬化完成标准由 `docs/AGENT_COLLABORATION_HARDENING_CHECKLIST.md` 第 13 项统一验收。完成状态必须同时满足：

- Agent 智能任务全部生成 `AgentTaskManifest v1`，并通过 `validate_manifest_strict` 的 task type、schema、lane、command 和 hard boundary 校验。
- Agent 输出只能落在 `50_workbench/` 的声明 lane，canonical 写入只能由 `draft submit`、`chapter finalize` 或各类 `semantic/character/pacing apply` 命令推进。
- apply/finalize 命令必须进入 `apply_transaction` 或写出等价可恢复报告，失败时能回滚 touched paths。
- invalid graph、memory、editorial、pacing 输出只生成 validation/report，不污染 final、RAG、graph、TCS 或 SQLite。
- Agent task lifecycle 可通过 task index 和 events 读取，至少覆盖 `awaiting_agent`、`submitted`、`validated`、`invalid`、`applied`、`superseded`、`rolled_back`。
- release guard 覆盖脚本内 LLM 调用、隐藏 API key 需求、`api_provider` 默认禁用、strict manifest、content expansion、lifecycle states 和 transaction rollback 合约。
- no-key E2E 覆盖主链路、semantic apply、invalid no-pollution 和 repair/humanize/expand 分支。
- README、项目级 AGENTS 和根 AGENTS 均引用本硬化文档和 checklist。

## 15. 风险与后续硬化点

仍需继续硬化的风险点：

- manifest 已有 strict semantic validation，后续还可继续补更细的业务 schema fixture。
- release guard 是源码扫描，不是运行时沙箱。
- apply/finalize 已进入文件级 transaction，后续可继续强化 DB/RAG 专项恢复和跨进程锁。
- editorial team 现在是任务协议，不是真实自动多 Agent 调度器。
- content expansion 已进入同构 manifest；style fingerprint、event matrix、reverse brake 等辅助创作工具还需要继续产品化到统一协议输入和报告面。
- Agent task index 已有完整生命周期，后续需要 GUI/API 消费 `events.jsonl` 并展示任务队列。
- 体验层编排第一阶段已按 `docs/AGENT_EXPERIENCE_ORCHESTRATION_CHECKLIST.md` 落地统一 next action、work order、production board 和 safe loop；后续重点是 GUI/API 消费和更细的真实生产体验。
- GUI/API 必须复用同一协议，不应绕过 CLI 命令。

最终验收标准见 `docs/AGENT_COLLABORATION_HARDENING_CHECKLIST.md`。
