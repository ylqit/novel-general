# Agent-First 文档协议 Phase 3 输出协议证据

## 范围

Phase 3 只实现 Agent 输出协议，不改变 v0.3.1 的生产路由、旧结果 validator、任务生命周期或 canonical 写入行为。新模块不读写项目文件，不调用 apply/finalize，也不更新 final、Bible、outline、graph、memory、TCS、RAG 或 SQLite。

实现入口：

- `src/longform_engine/agent_results.py`
- `tests/test_agent_document_protocol_phase3.py`

## 共享 Envelope

结构化 Agent 结果使用 `agent_result_envelope_v1`。顶层字段严格限制为：

```text
schema
task
scope
verdict
evidence
findings 或 deltas
notes
```

`task` 与 `scope` 由 CLI 根据 manifest 预填并在校验时逐字段匹配。Agent 只负责 verdict、证据和本角色需要的 findings 或 deltas。额外的 chapter card、planned facts、source path、source hash 或整份设计对象会被拒绝，避免重新形成 mega schema。

Evidence 使用稳定 `source_ref` 和 span，不要求 Agent 回填 CLI 已知的真实路径、文件 hash 或章节号。Phase 4 将负责把 `source_ref` 解析回当前文件并核验 exact span；Phase 3 不提前实现这一数据补全。

## 四种输出模式

### `markdown_prose`

- 只允许一个 `.md` 候选路径。
- 必须是完整、多段候选正文。
- 拒绝 JSON 文档、JSON code block、Analysis/Reasoning/Self Check/作者说明等控制材料。
- 不能写入 manifest 未声明路径或 canonical 路径。

### `compact_review_json`

- 只允许一个 `.json` 结果路径。
- envelope 只使用 `findings` lane。
- finding 只包含稳定 ID、code、severity、summary、evidence refs 和 recommendation。
- P0/P1 必须引用 evidence；`pass` 不能同时保留 P0/P1。
- Agent-owned evidence/findings 中出现 source path/hash、chapter number 或 planned facts 会失败。

### `document_index_bundle`

- 必须精确声明一个 Markdown 文档和一个 JSON apply index，不能共用一个大型 JSON。
- Markdown 保存长篇读者合同、人物弧线、世界规则和卷章说明。
- JSON index 使用 delta envelope，只保存稳定 ID、精确 Markdown heading、局部 scope、evidence refs 和 manifest 允许的 canonical targets。
- index heading 不存在、重复、target 越界或双文件路径不唯一时校验失败。

### `strict_delta_json`

- 只允许一个 `.json` 结果路径。
- 每条 delta 明确 `entity_id`、`field`、`action`、`old_state`、`new_state`、`evidence_refs` 和 `coverage`。
- `unchanged` 必须使用 `observe` 且 old/new 完全一致。
- `changed` 不能使用 `observe`；普通 canonical delta 必须具有 evidence ref。
- 不使用自然语言推测状态变化。

## Notes 权威边界

`notes` 只允许有限数量的短字符串，始终为 `non_authoritative`。`authoritative_delta_records()` 只复制显式 `deltas` 对象，从不解析 notes、Markdown、JSON 字符串或说明文字。测试覆盖了在 notes 中放入伪造 delta JSON 的情况，结果不会进入权威 delta 集合。

## 输出与交接合同

`compile_agent_output_contract()` 从 manifest 和角色注册表编译：

- 唯一 task/role/output mode。
- 唯一允许输出路径；document bundle 为精确且互异的 Markdown/JSON 两路径。
- 输出 schema 和 findings/deltas lane。
- validate 命令。
- apply/finalize 命令。
- failure next command。
- notes 非权威声明。

任一路径重复、越界、指向 canonical、后缀不匹配或命令缺失都会在协议编译阶段失败。`render_agent_output_instructions()` 可供 Phase 5 工作单 renderer 使用，但 Phase 3 不把它接入正式工作单。

## 自动验证

执行结果：

```text
python -m pytest -q tests/test_agent_document_protocol_phase3.py
7 passed

python -m pytest -q tests/test_agent_document_protocol_phase0.py tests/test_agent_document_protocol_phase1.py tests/test_agent_document_protocol_phase2.py tests/test_agent_document_protocol_phase3.py tests/test_agent_task_protocol.py
41 passed

python -m pytest -q
349 passed

python scripts/validate_skills.py
OK: skill packages validated

python scripts/release_surface_guards.py
OK: release surface guards passed

python scripts/build_resource_manifest.py --check
Resource manifest is current.

python scripts/sync_skill_references.py --check
Skill references are synchronized.

git diff --check
passed
```

专项测试覆盖四种模式的正常路径、mega payload、CLI-known 字段回填、正文夹带分析、changed/unchanged 错误、文档锚点错误、canonical 输出越界、notes 伪造 delta 和失败零污染。

## 准入结论

Phase 3 已完成，但新结果协议仍是隔离能力。Phase 4 必须完成 CLI 补全、真实文件 hash/span/ref 校验和旧结果规范化；Phase 5 必须完成所有 task type 的 renderer/parser/validator 隔离闭环。两阶段完成前，Phase 6 必须保持 `ready_for_data_pipeline: false`，Phase 7 不得接入正式生产数据链路。
