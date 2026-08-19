# Architecture

本文描述 `longform-novel-engine` v0.4.4 的当前实现边界。发生冲突时，以源码、`AGENTS.md`、本文件、`STORAGE_MODEL.md` 和 `V0_4_4_RELEASE_CHECKLIST.md` 为准。

## 1. 系统定位

引擎是 Host Agent 的本地控制平面，不是内置 LLM 客户端。Codex 或 Claude Code 负责需要生成式判断的创作、设计、语义抽取与审稿；Python 负责确定性的任务协议、路径约束、hash、状态机、事务、索引和审计。

```text
Host Agent
  -> AgentTaskManifest v4 + bounded inputs
  -> one declared workbench output
  -> deterministic validate
  -> explicit human-approved apply/finalize
  -> canonical filesystem
  -> rebuildable SQLite / RAG / vector / cache views
```

当前协议面包含 28 个角色、25 类任务和 4 类 Agent 输出协议。所有流程均为单进程顺序执行；引擎不创建 Agent 子进程、worker pool 或后台写入者。

## 2. 事实层级

事实优先级从高到低为：

1. 人工批准的治理文件和设计 Markdown。
2. `40_manuscript/final/chNNN.md` 定稿正文。
3. 与 final SHA 和 evidence span 绑定的 `30_state/semantic_ledger/chNNN.json`。
4. graph、角色状态、伏笔状态、时间线、世界状态和 TCS 等 materialized view。
5. chunks、向量、SQLite、查询缓存和上下文等可重建派生状态。

低层数据不得覆盖高层事实。摘要、检索命中和模型推断只能定位或投影证据，不能自行成为正文事实。

## 3. 领域所有权

| 领域 | 主要模块 | 所有权 |
| --- | --- | --- |
| 配置 | `config/loader.py`、`config/default.engine.yaml` | 默认 YAML 是唯一默认值源；配置注册表记录字段类型、默认值、最终值、覆盖来源和负责模块 |
| Agent 协议 | `agent_protocols.py`、`agent_tasks.py`、`agent_results.py` | Manifest、四类输出协议、lineage 与生命周期 |
| 生产编排 | `production.py`、`orchestration/pipeline.py` | 下一动作、章节卡、写作、定稿与批次边界 |
| 章节合同 | `chapter_contract.py` | 唯一字段、hash、引用完整性；拒绝已删除别名 |
| 智能设计 | `intelligence/pipeline.py` | 设计候选、rolling outline、章节方向 validate/apply |
| 审稿与修复 | `editorial/pipeline.py`、`gates/pipeline.py`、`repair_coordination.py` | 确定性 gate、有证据审稿、修复计划与候选替换 |
| 章节语义 | `semantic/pipeline.py` | final 证据验证、semantic ledger 与 materialized views |
| RAG | `rag/pipeline.py` | chunk、full embedding rebuild、chapter/memory delta 与 context |
| 向量层 | `vectorstore/pipeline.py`、`vector_backends.py` | 本地 SQLite/HNSW upsert、source replace、query 与 health |
| 派生数据库 | `db/sqlite_index.py` | 显式 full sync/rebuild 与 chapter semantic source delta |
| 存储事务 | `storage/project.py` | 原子写、项目锁、transaction v3、文件/SQLite 快照 |
| 崩溃恢复 | `storage/recovery.py`、`cli_recovery.py` | 只读诊断、hash/审批绑定的显式恢复和审计 |
| 发布表面 | `release_readiness.py`、`distribution.py` | RC/public 通道、Skill/资源一致性、安装诊断 |

`cli.py` 负责顶层命令组合与公共异常策略；领域命令组应由领域模块注册。只有拥有验证、错误策略、生命周期或兼容边界的抽象才应保留。

## 4. 唯一章节合同

`20_outline/chapter_cards/chNNN.json` 只接受 canonical 字段：

```text
chapter_number, title, book_goal, volume_goal, protagonist_goal,
chapter_duty, platform_promise, conflict, information_release,
scene_chain, featured_character_ids, reader_gain, cost,
relationship_move, canon_refs, world_rule_refs, foreshadow_refs,
forbidden_reveals
```

`duty`、`information`、`reader_payoff` 是已移除的章节卡/rolling plan alias。发现它们时返回 `chapter_contract_inconsistent` 或候选校验错误，不做静默双读。Outline anchor 自身的 `duty` 和 semantic chapter digest 的 `reader_payoff` 属于不同协议，不是章节合同 alias。

写作、Humanizer、节奏、收益、人物、场景和编辑任务消费同一合同投影及 `chapter_contract_hash`。章节方向 apply 必须同时更新章节卡和 rolling plan 的 canonical 字段。

## 5. 写入与恢复状态机

canonical mutation 必须在项目写锁和 `ApplyTransaction` 内执行，并生成 `canonical_write_transaction_report_v3`：

```text
preparing --快照清单逐项落盘--> prepared --开放 mutation 边界--> applied
                                                   \--异常--> rolled_back
```

- `preparing`：canonical 写入尚未开始；崩溃后只能丢弃预备快照。
- `prepared`：快照 inventory 已持久化；崩溃后必须按 inventory 回滚。
- `applied`：提交证据先于快照清理落盘；崩溃后只能清理残留快照，不能回滚已提交状态。
- `rolled_back` / `aborted_before_apply`：终态。

恢复从 `recovery status` 开始。锁或报告的 SHA 变化、未知主机、无法确认的 process identity、空或非当前 schema inventory、touched path 未被 inventory 完整覆盖、越界路径或缺失快照均进入 need-human。恢复动作要求 `--approved-by` 和 status 返回的精确 SHA，并在 `70_runtime/recovery/` 写审计报告。SQLite 回滚清除 sidecar、使用 backup API，并以 `integrity_check` 收口。

除 recovery 自身外，所有普通 CLI mutation 会在取锁前路由 stale/unknown/invalid lock，并在取得项目锁后再次执行恢复预检；存在 preparing/prepared/applied-cleanup 或 need-human transaction 时，在领域 handler 开始前即阻断。另一个活跃进程持锁时保持普通并发互斥语义，不把正在运行的 transaction 误报为崩溃恢复。

## 6. Semantic RAG

逐章语义应用与全量重建使用不同 API：

- `apply_style_memory_delta` / `sync_semantic_delta` / `apply_embedding_delta`：只读取当前 final、当前章节 chunk 和本次变化的 memory/TCS owner；style 使用已有 per-source 小样本聚合，SQLite 与 vector 只替换声明 owner 下的记录。逐章路径不扫描历史正文，也不重写 `embeddings.jsonl`。
- `rebuild_embedding_index`：扫描全部 canonical chunk/memory，重写完整 embedding snapshot，并同步整个向量库。只用于显式 rebuild/backfill/基准初始构建。

本地 vector SQLite 是在线向量事实；`60_rag/metadata/embeddings.jsonl` 是可重建的完整导出快照。章节关闭要求当前 final SHA、chunk source SHA、chunk 数量和 active vector source SHA 全部一致，并要求下一章 semantic context 可用。

配置只接受已经实现 query/upsert 的 `local_sqlite` 与 `local_hnsw`。其他后端不属于公开配置契约。

## 7. 质量和发布边界

工程门禁验证协议正确性、零污染、可恢复性和可解释证据，不等价于文学质量证明。当前保持 `literary_evidence_ready=false`。

发布检查分为：

- `release check --channel rc`：验证未发布源码、稳定 README 安装通道、无意外 RC tag、资源与工程合同。
- `release check --channel public`：默认严格模式，要求当前包版本、README 安装 tag、HEAD tag 和远程发布面一致。

任何 commit、push、tag、Release 或全局 Skill 更新仍需要用户明确授权。
