# Agent-First Document Protocol Phase 0 Baseline

本报告冻结 `Agent 文档协议、Prompt 角色与数据链路准入` 改造前的 v0.3.1 状态。机器可读事实源为 [`baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE0_V031.json`](baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE0_V031.json)。

## Frozen Runtime

- Engine：`0.3.1`。
- 当前 HEAD：`5259cba2eab512953c9e969409279d183ce440e2`。
- `v0.3.1` release commit：`a0cfe1895e15751b8a251b447575986a5d89dd62`。
- AgentTaskManifest：当前写入 v2，兼容读取 v1/v2。
- 现有 task contract：25 类。
- 现有 role/work-scope/output-guidance 合成 hash：`bbdcc99cca4ce3010b6bc10661200d880cf44982951e329d4c5fc3c1f26564c8`。
- Codex Skill hash：`94784b55da6264ff2bd26edfca7180e3732d6b9f0506e2192705208a04d1673a`。
- Claude Code Skill hash：`d908d8a575977dffe7bd9d27b3591a7854d266fe4305ce375c647eb85ef2f681`。

这些值用于说明迁移从哪里开始，不要求未来版本继续等于它们。后续版本必须保留本文件和 v0.3.1 fixtures，而不是改写基线数字。

## Preserved Failure

SAO v0.3.1 失败运行继续保留在被 `.gitignore` 隔离的项目目录中，没有复制正文到仓库文档或 fixtures。

- 验证报告 SHA-256：`4ec7414866741e539d86e2a770504c61cb5314171e36757f3d73d6d7e4f8179b`。
- issue log SHA-256：`2ce496890e31a554a1cad82561588d95baa33569b18af47afd95bb27731eb754`。
- Bible、outline、state、final、RAG、SQLite 六类受保护前缀共 35 个文件，聚合 SHA-256 为 `813a7a6b2baa85240fdb7603ae218d418cb58a739fc9679a68fa586ea721df78`。

聚合 hash 按相对路径、单文件 SHA-256 和字节数排序后计算。该快照证明 Phase 0 没有借机修改失败项目的 canonical 状态。

## Ownership Inventory

机器基线逐项记录全部 25 类任务的 scope、输入类别、输出 schema、validator、apply 命令和 canonical owner。字段归属固定为四类：

| 类别 | 含义 |
| --- | --- |
| `agent_judgment` | Agent 提供的创作正文、观察、结论、证据选择和建议 |
| `cli_known` | CLI 已知或必须重算的任务身份、路径、hash、计划事实、命令和生命周期 |
| `canonical_delta` | 只有 validate 后经显式 apply 才可成为正式状态的候选事实或设计 |
| `presentation_only` | 只供 Agent/人阅读，禁止脚本解析为 canonical 的角色说明、标签、示例和 notes |

当前协议最明显的问题是 review 结果让 Agent 重填部分 `cli_known` 字段；这是 Phase 3-4 的迁移对象，本阶段只冻结事实，不改变 validator。

## Context Measurements

重复度采用固定的“跨输入重复长行字符占比”：按 manifest 顺序读取 UTF-8 文本，规范空白，忽略短于 16 字符的行；某长行已在前一个输入出现时计为重复。它不会把转述或语义相同的不同 JSON 表达算作重复，因此是保守下界。

| Task | 输入 | 实际字符 | 上限 | 重复长行占比 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| `reader_payoff_review` | 5 | 25,631 | 20,000 | 23.62% | 注册被严格校验正确拒绝；card、gate 和 payoff context 重复明显 |
| `semantic_review` | 3 | 12,706 | 18,000 | 5.83% | v0.3.1 三输入编译有效，仍可继续压缩 context |
| `editorial_review` | 7 | 29,214 | 18,000 | 3.67% | 历史 manifest 超预算，主要体积来自完整 reward ledger 和 chapter card |
| `chapter_semantic` | 7 | 22,486 | 28,000 | 10.77% | 未超当前预算，但完整 card、人物和伏笔账本仍偏重 |

`editorial_review` 样本来自早于注册前严格校验落地的历史 15 章项目。它作为迁移证据保留，不代表当前引擎应继续接受同样的无效 manifest。

## Compatibility Fixtures

仓库冻结以下 prose-free fixtures：

- AgentTaskManifest v1 和 v2。
- chapter submission v1 和 v2。
- `reader_payoff_review_v1`。
- `chapter_semantic_bundle_v1`。

Fixture 只包含合成英文短句和协议字段，不包含用户小说正文。专项测试验证 v1 manifest 可规范化、v2 manifest 可严格校验、submission 增量字段可区分，以及 review/semantic bundle 顶层结构仍与 v0.3.1 模板兼容。

## Data Pipeline Lock

本阶段没有修改 `src/longform_engine`、Skill、`production next`、validate/apply/finalize 或任何小说项目。新 Agent-first 协议仍未接入正式数据链路。

只有主 checklist 的 Phase 0-5 全部为 `[x]` 且 Phase 6 readiness 输出 `ready_for_data_pipeline: true` 后，才允许开始 Phase 7。

## Verification

2026-08-13 本地验证结果：

- `python -m pytest -q tests/test_agent_document_protocol_phase0.py`：7 passed。
- `python -m pytest -q`：325 passed。
- `python scripts/sync_skill_references.py --check`：通过。
- `python scripts/build_resource_manifest.py --check`：通过。
- `python scripts/validate_skills.py`：通过。
- `python scripts/release_surface_guards.py`：通过。

完整 pytest 仅报告既有 `pytest-asyncio` 默认 loop scope 弃用警告，没有测试失败。
