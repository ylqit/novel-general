# Agent-First Document Protocol Phase 7: Full Production Data Pipeline

## Status

Phase 7 已完成。通过 Phase 6 准入的角色化文档协议已经接入正式生产入口：

```text
production next
-> agent-task brief
-> Agent role output
-> agent-task result-validate
-> domain validate / submit / gate
-> explicit apply or chapter finalize
-> chapter semantic validate/apply
-> explicit chapter close
```

这表示协议与数据链路已经接通，不表示 Phase 8 的第 1 章、5 章或 20 章真实生产验收已经完成。

## Integration Owner

`src/longform_engine/agent_pipeline.py` 是角色工作包和 Agent 输出进入正式生命周期的唯一集成边界。它负责：

- 在生成工作包和验证结果前检查运行授权。
- 调用 Phase 5 的上下文、Prompt、宿主渲染和输出规范化能力。
- 为 Codex 与 Claude Code 生成语义相同、展示层不同的工作单。
- 将结构有效的 Agent 输出原子登记为 `submitted`，无效输出登记为 `invalid`。
- 只修改 Agent task 控制面；协议校验不直接写 canonical 数据。

源码运行授权同时绑定 Phase 6 证据文件 SHA-256 与当前协议 surface SHA-256。wheel 运行读取同一内置授权资源。任一绑定失效时，正式生产入口立即阻断。

## Candidate Lifecycle

角色输出校验和领域校验是两个不同层次：

1. `agent-task result-validate` 检查角色、输出模式、路径、Schema、证据和上下文 hash，并推进 task lifecycle。
2. 正文类候选仍必须执行 `draft submit` 和 deterministic gate。
3. 项目设计、研究和语义增量仍必须执行各自 domain validate，并由用户显式 apply。
4. final 仍只能由 `chapter finalize --approved-by` 生成。

v0.3.1 单 JSON 的 project design manifest 通过明确 Schema 白名单进入 `legacy_document_json` 兼容模式。新 document/index 合同缺少任一文件时仍然失败。旧 `graph_extract`、`memory_extract` 和 `character_memory` 只允许兼容读取，不再生成新的重复 Agent 工作包。

## Chapter Stage Routing

`production next` 先从 final、closure、gate source hash、当前 draft 和当前阶段任务推导章节状态，再选择该阶段允许的角色。项目级通用优先级不能覆盖未闭环章节。

阶段包括：

- `writing_pending`
- `pre_gate_candidate_review`
- `gate_pending`
- `repair_pending`
- `semantic_review_pending`
- `payoff_pending`
- `editorial_pending`
- `ready_to_finalize`
- `finalized_needs_semantic_bundle`
- `finalized_needs_close`
- `closed`

`pre_gate_candidate_review` 只容纳 Humanizer 候选的独立语义保持审稿。旧 writing/repair task 即使仍有审计记录，也不能越过当前章节阶段重新成为 next action。

## Canonical Transaction

`chapter semantic-apply` 在一个 `apply_transaction` 中物化：

- `30_state/semantic_ledger/chNNN.json`
- `30_state/story_graph.json`
- `30_state/foreshadowing_state.json`
- timeline 与 world state
- 角色当前状态视图
- 章节语义摘要
- 下一章 TCS
- style/RAG chunks/RAG context
- SQLite 索引和 novel state

任一 RAG、SQLite 或其他物化步骤失败时，上述 touched paths 全部回滚。final 不在该事务的写集合中。成功后旧 graph/memory/character 三类重复任务被标为 `superseded`。

## Controlled Feedback

Reader payoff、Humanizer、编辑、节奏和 gate 报告只通过 `controlled_agent_feedback_v1` 回流。工作单只接收 CLI 生成的来源类型、状态、严重级别计数、issue code 和来源 hash；报告中的自由文本、正文 excerpt、命令和 Prompt-like 内容不会进入控制 Prompt。

## Evidence

- Phase 7 专项：`tests/test_agent_document_protocol_phase7.py`，5 passed。
- 语义成功/回滚组合：`tests/test_semantic_knowledge.py` 与 `tests/test_orchestration.py::test_semantic_apply_rolls_back_touched_paths_on_index_failure`，8 passed（与 Phase 7 专项合并运行）。
- Humanizer/production/task routing 组合：45 passed。
- 完整回归：380 passed。
- Ruff、Skill 引用同步、资源清单、Skill 校验、release guards 和 readiness checker 均通过。
- v0.3.1 wheel/sdist 构建成功；wheel 141 entries、sdist 261 entries 的资源审计通过。
- 机器证据：`docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_EVIDENCE.json`。

## Remaining Gate

Phase 8 仍负责 loose artifact 精简、审计包 compact/verify/restore，以及第 1 章、5 章和 20 章递进实产验收。在 Phase 8 完成前，不恢复 20 章 SAO 生产，也不声称完整生产验收已经通过。
