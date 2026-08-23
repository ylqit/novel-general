# Architecture

本文描述 `longform-novel-engine` v0.10.0 的活动架构。v0.10 是破坏性协议，不读取或迁移 v0.9 项目。

## 1. 系统定位

Host Agent 负责创作、规划、语义判断和独立审稿；CLI 负责协议、路径、hash、状态机、事务和 canonical 写入。Python 不在后台调用 LLM，也不创建并行写入者。

```text
Host Agent
  -> bounded AgentTaskManifest v4
  -> one declared workbench output
  -> deterministic structural validation
  -> independent semantic review
  -> explicit human decision
  -> transaction v3 canonical apply
  -> rebuildable graph / RAG / vector / SQLite views
```

## 2. 事实层级

1. 人工批准的治理、`canonical_fact_v2`、Book Spine、分卷和滚动规划。
2. `40_manuscript/final/chNNN.md`。
3. 与 final 精确 span 绑定的 semantic ledger、事件实现和承诺证据。
4. graph、角色状态、伏笔状态、TCS 等物化视图。
5. RAG、向量、SQLite 和缓存等可重建视图。

摘要、关键词命中、平台启发式或 Agent 推断不能覆盖高层事实。

## 3. 语义规划与分卷滚动

`planning_bundle_v1` 同时提供：

- `book_spine_v1` 与 `volume_skeletons_v1`；
- 唯一活动 `volume_plan_v1`；
- 最多 20 章的 `rolling_window_plan_v2`；
- firm 1–3 章、directional 4–10 章、horizon 11–20 章；
- 逐章 forecast、语义义务、Plot Node 表和 `chapter_contract_v5`；
- 明确选入的 `reader_promise_v2`，不从文本猜测承诺。

结构校验只判断字段、范围、稳定 ID、引用与 hash。独立语义审查必须绑定原规划 bytes，并与作者/编译者角色隔离；人工随后批准整体规划，并对 firm 层每个 Plot Node 逐项 `approve|adjust|reject|defer`。只有全部节点获批后，CLI 才能事务化写入 canonical。

章节关闭推进 `planning_cursor_v1`。下一章不足三个有效 firm 合同、进入新卷或 planning basis 漂移时，`production next` 必须返回重新规划；重新规划仍需独立语义审查和全节点审批。

## 4. 唯一章节合同与作者上下文

正式章节合同只位于 `20_outline/chapter_contracts/chNNN.json`，schema 只有 `chapter_contract_v5`。v4 章节卡、别名、双读和自动迁移均不存在。

合同绑定：

- forecast、章节拓扑、章节职责、可观察变化与读者价值；
- `failure|choice|cost|aftermath` 的显式 applicability；
- 当前人工批准的 Plot Node 表；
- 语义义务、承诺动作、保护项和禁止偏移。

写作链只有：

```text
human_chapter_intent_v2
-> chapter_story_brief_basis_v3
-> chapter_story_brief_v5
-> chapter_writing_task_v7
-> chapter_story_brief_renderer_v5
```

作者稿只展示可读拓扑、剧情义务、批准节点、人物选择、读者价值、必要事实与声音。内部 ID、hash、原始 RAG、Graph、SQLite、平台诊断和 Prompt 日志不得进入作者工作单。

共编使用 `chapter_coedit_session_v2`；人工终稿使用 `human_author_revision_v4`；最终深审使用 `human_story_review_v7`。任何 v0.9 schema 直接不兼容。

## 5. 章节关闭

final 后的 `canonical_delta_v1` 先生成 semantic ledger 和派生视图。语义应用不再自动推进承诺。

关闭前必须满足：

- 每个获批 state-changing event 已由人确认 `realized|deferred|cancelled`；realized 必须引用当前 final 的 Unicode 精确 span，并绑定 semantic ledger hash；
- 每个非 defer reader-promise 动作都有当前 final 精确 span、semantic ledger hash 和人工确认；
- defer 动作有逐章人工原因；
- gate、独立审稿、人工终稿、深审、任务 lineage 和作者声音门禁均为当前状态。

`chapter_closure_v2` 固化 final、semantic ledger、event ledger 与 reader-promise ledger hash。未完成时 `chapter close` 阻断。

## 6. 设定传播与版本化回溯

活动设定协议为：

```text
canonical_fact_v2
-> canon_change_proposal_v1
-> dependency_impact_v1
-> canon_change_semantic_review_v1
-> human_canon_change_decision_v1
```

依赖闭包只读取稳定事实 ID 和显式 dependency/obligation refs，不使用关键词猜测。确定性闭包中的 `must_stale` 不允许被语义审查或人工决定降级。

未来生效的变更事务化更新 `10_bible/canonical_facts.json`，并在 `30_state/stale_artifacts.json` 标记受影响 forecast、合同、节点、Story Brief 和任务。重新规划写入新证据和新 hash 后才能解除。

影响已定稿章节的变更不直接修改 mainline，而是创建覆盖 `E..当前头` 的 `revision_branch_v2`。提升分支时统一重建 semantic ledger、图谱、RAG、向量和 SQLite。

## 7. 人工读者反馈

`reader_feedback_batch_v1` 与 `human_reader_feedback_decision_v1` 永远位于 workbench，不能直接修改正文、承诺账本、平台策略或 canonical。被人接受的假设只能转换为 planning proposal 或 canon-change seed，再进入各自完整审批链。

## 8. 写入、恢复与派生状态

所有 canonical mutation 使用项目锁和 transaction v3：

```text
preparing -> prepared -> applied
                 \-> rolled_back
```

文件是事实源；SQLite、vector、RAG 和查询缓存可重建。普通命令发现 pending transaction 或 stale lock 时必须先路由 `recovery status`。恢复动作绑定精确报告 SHA 和人工审批，禁止手工删锁或快照。

## 9. 平台与质量边界

起点男频是主要编辑画像；番茄免费只提供 P2 非阻断观察。平台快照和启发式不能升级为作者配额或合规门禁。项目不实现 AI 概率、规避检测、平台必过或人工写作比例声明。

`protocol_ready`、`author_acceptance_ready`、`literary_evidence_ready` 独立报告。当前仍无合格真实盲评 manifest，因此 `literary_evidence_ready=false`。
