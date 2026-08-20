# Architecture

本文描述 `longform-novel-engine` v0.6.0 Release Candidate 的当前开发边界。发生冲突时，以源码、`AGENTS.md`、本文件、`STORAGE_MODEL.md` 和 `V0_6_0_RELEASE_CHECKLIST.md` 为准。

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

当前协议面包含 29 个角色、27 类任务和 4 类 Agent 输出协议。所有流程均为单进程顺序执行；引擎不创建 Agent 子进程、worker pool 或后台写入者。

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
| 智能设计 | `intelligence/pipeline.py` | 设计候选、rolling outline、章节方向 Markdown 与 `chapter_direction_selection_v1` 联合 validate/apply |
| 叙事规划状态 | `reader_promises.py`、`arc_simulation.py` | 读者期待窗口、角色场外行动和逐章因果义务；不进入事实层 |
| 审稿与修复 | `editorial/pipeline.py`、`gates/pipeline.py`、`human_story_review.py`、`human_review_consultation.py`、`review_server.py`、`repair_coordination.py` | 确定性 gate、每章场景审稿、人工十项深审、只读咨询、本地审稿台、修复计划与候选替换 |
| 编辑模式 | `quality/editorial_patterns.py` | 从结构化 role/finding 建立无正文复发注册表；只服务编辑与 repair |
| 章节语义 | `semantic/pipeline.py` | final 证据验证、semantic ledger 与 materialized views |
| RAG | `rag/pipeline.py` | chunk、full embedding rebuild、chapter/memory delta 与 context |
| 向量层 | `vectorstore/pipeline.py`、`vector_backends.py` | 本地 SQLite/HNSW upsert、source replace、query 与 health |
| 派生数据库 | `db/sqlite_index.py` | 显式 full sync/rebuild 与 chapter semantic source delta |
| 存储事务 | `storage/project.py` | 原子写、项目锁、transaction v3、文件/SQLite 快照 |
| 崩溃恢复 | `storage/recovery.py`、`cli_recovery.py` | 只读诊断、hash/审批绑定的显式恢复和审计 |
| 发布表面 | `release_readiness.py`、`distribution.py` | RC/public 通道、Skill/资源一致性、安装诊断 |

`cli.py` 负责顶层命令组合与公共异常策略；领域命令组应由领域模块注册。只有拥有验证、错误策略、生命周期或兼容边界的抽象才应保留。

## 4. 唯一章节合同

`20_outline/chapter_cards/chNNN.json` 使用 `chapter_contract_v3`，并绑定当前 `reader_promise_ledger_v1` 动作与已批准的 `arc_causal_simulation_v1`。核心 canonical 字段为：

```text
chapter_number, title, book_goal, volume_goal, protagonist_goal,
chapter_duty, platform_promise, immediate_desire, opposition_force,
dramatic_question, conflict, key_failure, irreversible_choice,
chapter_turn, reveal_boundary, scene_chain, must_dramatize, may_summarize,
primary_story_engine, scene_carriers, protected_story_outcomes, prohibited_drift,
featured_character_ids, reader_gain, cost, state_change_kind, dramatic_method,
exposition_carrier,
relationship_move, canon_refs, world_rule_refs, foreshadow_refs,
forbidden_reveals, reader_promise_actions, arc_simulation_ref
```

`information_release`、`duty`、`information`、`reader_payoff` 是已移除字段。发现它们时返回 `chapter_contract_inconsistent` 或候选校验错误，不做静默双读。

写作、Humanizer、节奏、收益、人物、场景和编辑任务消费同一合同投影及 `chapter_contract_hash`。章节方向 apply 必须同时更新章节卡和 rolling plan 的 canonical 字段。

同人滚动章节计划逐章提供非空 `protected_canon_outcomes`；章节卡保留该权威列表，方向候选必须原样保留。缺失或改写该列表都视为改纲范围，不能靠局部场景方案绕过；新增长期事实也必须进入 `outline_revision`。改纲 apply 通过一个 transaction v3 同步更新纲要与承诺账本、截断受影响编辑模式、失效因果模拟/章节卡/作者工作单/Agent 任务并重建 SQLite 投影；范围内已有正式章节时必须先 rollback。

Book Design 还必须提供 `story_engine_contract_v1`。规划侧通过读者承诺账本管理期待窗口，通过角色因果模拟约束滚动纲要；二者都不是世界事实。作者不会读取完整事实清单或规划控制面：CLI 将约束编译为 `chapter_story_brief_v2`，只展示欲望、动作、阻力、选择、代价、读者收益和离场状态；fact ID、source hash、promise ID、模式代码与检索来源保留在内部。

章节方向 Markdown 必须给出 2–3 个稳定 option ID。人工选择单独写成 `chapter_direction_selection_v1`，绑定方向文档 hash、所选 option、调整说明与载体重复理由；方向批准和语义编译必须同时消费两者，任何 hash 漂移都拒绝继续。

独立审稿完成后冻结不可变 review bundle。唯一当前人工协议 `human_story_review_v3` 同时绑定候选、章节合同、承诺账本、因果模拟与 review bundle 五类 hash；`accept` 要求十项全部通过，并用精确正文 span 分别证明关键转折、人物选择/情绪和读者收益。`repair` 至少包含一条结构化批注并进入既有两轮不可变修章预算；`redirect` 明确回到章节方向或改纲。

`human_review_advisor` 复用 `design_document_v1`，咨询任务只读取当前候选、Story Brief、冻结 bundle、用户 span 与同候选历史。咨询记录位于非 canonical 工作区，不能修改正文、批准章节或写 final；候选变化后旧会话与建议全部 stale。本地三栏审稿台只监听 `127.0.0.1`，使用一次性 token、Host/Origin/CSRF/CSP 与预期 hash。人工改稿只能提交 repair plan 对应的完整替代稿，随后重跑全部 gate 与独立审稿。

`scene_prose_editor` 对每个核心转折分别提供正文 span，证明 `attempt → counteraction → choice → visible_cost → state_delta → reader_gain`。载体名称、关键词和 3/5、4/5 统计只能触发 P2 诊断；没有正文 span 的确定性规则不能单独形成 P1。当前 P0/P1 始终进入同一候选的不可变 repair bundle，不能由跨章模式“带到下一章”来替代修复。

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

工程门禁验证协议正确性、零污染、可恢复性和可解释证据，不等价于文学质量证明。`quality status` 分别报告协议就绪、作者接受和文学证据；`author_acceptance_ready` 不会改写 `literary_evidence_ready`。`agent_data_pipeline_readiness_v5` 从 `literary_evidence_manifest_v1` 计算文学证据状态；只有起点前三章、番茄前三章和十五章纵向盲评三类 scope 的来源、聚合和阈值结论全部有效才可就绪。当前仓库不包含真实盲评 manifest，因此保持 `literary_evidence_ready=false`。

发布检查分为：

- `release check --channel rc`：验证未发布源码、稳定 README 安装通道、无意外 RC tag、资源与工程合同。
- `release check --channel public`：默认严格模式，要求当前包版本、README 安装 tag、HEAD tag 和远程发布面一致。

任何 commit、push、tag、Release 或全局 Skill 更新仍需要用户明确授权。
