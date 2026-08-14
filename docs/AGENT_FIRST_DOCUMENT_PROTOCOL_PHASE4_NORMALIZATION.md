# Agent-First 文档协议 Phase 4 规范化与校验证据

## 范围

Phase 4 新增一层独立、只读的 Agent 结果规范化器。它负责把当前 `agent_result_envelope_v1` 和受支持的 v1/v2 历史结果整理为 `normalized_agent_result_v1`，并重新核验当前磁盘文件与 canonical 前置状态。

本阶段不修改正式 `production next` 路由，不替换现有 task-specific validator，不执行 apply/finalize，也不更新任务生命周期。唯一允许新增的文件是受控诊断：

```text
50_workbench/agent_tasks/diagnostics/<task-id>.<result-hash>.json
```

实现与测试入口：

- `src/longform_engine/agent_normalization.py`
- `longform-engine agent-task result-validate`
- `tests/test_agent_document_protocol_phase4.py`

## CLI 接口

```text
longform-engine agent-task result-validate project.yaml <task-id-or-manifest> \
  --file <declared-agent-result> \
  [--document <declared-markdown-companion>] \
  [--json]
```

命令先严格校验 manifest，再读取结果。无效 manifest 不会进入结果解析；有效结果只获得原 manifest 的 `validate_command`，无效或歧义结果返回 `failure_next_command`。该命令不会把任务标记为 submitted、validated 或 invalid，正式生命周期仍由 Phase 7 以后的数据链路负责。

## CLI 补全字段

Agent 不再机械回填以下事实。规范化器从 manifest、当前文件和 canonical state 补齐：

- `chapter_number` 与 scope。
- 每条 evidence 的真实 `source_path` 和当前 SHA-256。
- 章节卡中的 `chapter_duty`、`reader_gain`、`cost`、`promise_refs`、`platform_promise`、`relationship_move` 和 `canon_refs`。
- 经验证的 `allowed_canonical_refs`。
- manifest、结果、章节卡和每个声明来源的当前 hash。
- manifest 声明的 canonical targets；Agent 不能扩大该集合。

Agent 输出中的 planned facts 不会覆盖当前章节卡。自然语言 notes 仍为非权威字段，不会被解析为事实增量。

## 来源与证据规则

来源注册表只接受：

1. manifest 的 `input_files`。
2. 已声明 context packet 中的 `source_catalog`、`provenance`、`canonical_source_provenance`。
3. context packet 明确列出的 `allowed_canonical_refs`，且路径必须属于可读 canonical lane。

每个来源均回读当前 UTF-8 文件并计算 SHA-256。context packet 的 hash 与当前文件不一致时直接失败，context packet 本身不能成为事实源。

每条 evidence 的 `start/end/excerpt` 都会回读当前来源文件，并执行严格的：

```text
current_text[start:end] == excerpt
```

`source_ref` 无法解析时为 invalid；同一别名命中多个来源时进入 `need_human`，CLI 不猜测证据来自哪个文件。旧 editorial 结果只有摘录而没有 offset 时，只有摘录在当前章节中唯一出现才能补齐 span；零次或多次出现均进入 `need_human`。

## Canonical 前置条件

规范化器继续严格检查：

- 实体与角色必须使用当前 Bible/graph 中的稳定 ID。
- 关系 delta 的 `old_state` 必须与当前关系状态一致。
- 人物知识变化必须引用当前正文的精确证据；旧语义包的 knowledge route 必须属于受支持来源类型。
- 伏笔必须使用计划账本中的 `thread_id`；提前埋设、窗口外兑现进入 `need_human`，未埋设就强化/误导/兑现/过期为 invalid。
- Agent 不能写入 `allowed_output_paths`、validate/apply/failure command、hard boundaries 或 human-apply 标志。
- document index 中的 canonical targets 只能是 manifest targets 的子集，其他角色结果不得声明 canonical targets。

这些检查只形成规范化报告，不执行 canonical 写入。

## 兼容策略

当前已提供确定性适配：

- `agent_result_envelope_v1`
- Markdown prose only
- `reader_payoff_review_v1`
- `semantic_review_result_v1`
- `chapter_semantic_bundle_v1`
- `editorial_role_review_v1`
- `editorial_role_review_v2`

AgentTaskManifest v1 与 v2 均先规范化为当前内部 manifest，再执行同一套严格检查。无法确定转换语义的历史结果不会被猜测转换，而是返回 `need_human`。完整 task type 矩阵仍属于 Phase 5，不能因本阶段存在若干适配器就宣称全部角色已经具备隔离闭环。

## 注册与污染边界

manifest 注册仍由 `write_manifest()` 在写 manifest、index 和 lifecycle event 前执行严格校验。注册失败时：

- 不写 manifest。
- 不写 `agent_task_index.json`。
- 不写 lifecycle event。
- 已存在的非 canonical 工作单由 `artifacts status` 的 orphan 检测识别。

结果规范化失败只允许写 diagnostics。测试冻结并比较了 final、Bible、outline、graph、memory/TCS、RAG 和 SQLite lane 的文件 hash，同时比较 Agent task index/event，确认均未变化。

## 自动验证

本阶段完成时的验证结果：

```text
python -m pytest -q tests/test_agent_document_protocol_phase4.py
9 passed

python -m pytest -q \
  tests/test_agent_document_protocol_phase3.py \
  tests/test_agent_task_protocol.py
26 passed

python -m pytest -q \
  tests/test_agent_document_protocol_phase0.py \
  tests/test_agent_document_protocol_phase1.py \
  tests/test_agent_document_protocol_phase2.py \
  tests/test_agent_document_protocol_phase3.py \
  tests/test_agent_document_protocol_phase4.py \
  tests/test_cli.py::test_cli_mutating_commands_are_marked_for_project_lock \
  tests/test_production_experience.py
58 passed

python -m ruff check \
  src/longform_engine/agent_normalization.py \
  tests/test_agent_document_protocol_phase4.py \
  src/longform_engine/cli.py
All checks passed

python -m pytest -q
358 passed

python scripts/validate_skills.py
OK: skill packages validated

python scripts/release_surface_guards.py
OK: release surface guards passed

python scripts/build_resource_manifest.py --check
Resource manifest is current

python scripts/sync_skill_references.py --check
Skill references are synchronized

git diff --check
passed
```

## 准入结论

Phase 4 已完成，但正式数据链路仍锁定。Phase 5 必须证明所有非 legacy task type 的 renderer、compiler、parser、normalizer 和 validator 均能在隔离测试中闭环，并覆盖四种输出模式的正常、边界和失败 fixture。Phase 5 完成前，Phase 6 必须保持 `ready_for_data_pipeline: false`，Phase 7 不得接入生产路由或 canonical apply。
