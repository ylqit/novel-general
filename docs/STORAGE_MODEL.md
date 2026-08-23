# Storage Model

本文定义 v0.11.0 的落盘合同。v0.9 文件及 `fanfiction_source_canon_v1` 不能作为当前协议证据。

## 1. canonical 与 workbench

| 路径 | 角色 |
| --- | --- |
| `10_bible/canonical_facts.json` | `canonical_fact_registry_v2`，稳定事实 ID 的设定事实源 |
| `20_outline/book_spine.json` | 全书主轴 |
| `20_outline/volume_skeletons.json` | 全书分卷骨架 |
| `20_outline/volumes/volNNN.json` | 活动卷/历史卷计划 |
| `20_outline/rolling_window.json` | 当前 `rolling_window_plan_v2` |
| `20_outline/chapter_forecasts/chNNN.json` | 逐章滚动预测 |
| `20_outline/chapter_contracts/chNNN.json` | 唯一 `chapter_contract_v5` |
| `20_outline/plot_nodes/chNNN.json` | 逐节点人工审批表 |
| `20_outline/chapter_intents/chNNN.json` | `human_chapter_intent_v2` |
| `30_state/semantic_obligations.json` | 语义义务账本 |
| `30_state/narrative_events/chNNN.json` | 获批事件与实现证据 |
| `30_state/reader_promise_ledger.json` | `reader_promise_ledger_v2` |
| `30_state/planning_basis.json` | 规划 basis 文件绑定 |
| `30_state/planning_cursor.json` | 章节关闭后的滚动游标 |
| `30_state/stale_artifacts.json` | 设定传播 stale 注册表 |
| `30_state/semantic_ledger/chNNN.json` | final 精确证据的章节语义 |
| `30_state/chapter_closures/chNNN.json` | `chapter_closure_v2` |
| `40_manuscript/final/chNNN.md` | 唯一正文事实源 |
| `50_workbench/` | 候选、任务、审稿、反馈与审批证据，非 canonical |
| `50_workbench/同人原著资料/<作品名>/` | 项目级中文资料包、覆盖计划、固定绑定、批准提取和短证据；不含完整原件 |

正式正文只接受 `ch{chapter:03d}.md`；四位及以上自然扩展。任何旧命名、`.txt` 或别名均不搜索、不迁移。

## 1.1 用户级原著资料库

默认根目录位于操作系统用户数据目录的 `longform-novel-engine/原著资料库/`；`LONGFORM_SOURCE_LIBRARY` 可指定绝对路径。其中文物理结构只有 `作品/<作品名>/资料项/<动态资料名>/`，不硬编码媒介目录。完整原件只允许用户合法导入、公版或明确许可；普通网页仅保留定位、结构化事实和必要短证据。

用户资料库不进入 Git、出版包、项目审计包或 Skill。项目绑定固定 `作品ID + 资料项ID + 内容哈希 + 提取哈希`；全局文件更新只产生升级提案，不能静默改变项目。

## 2. 作者工作单

`50_workbench/writing_tasks/` 保存：

- `chNNN.json`：`chapter_writing_task_v7`；
- `chNNN.md`：作者唯一可读的 `chapter_story_brief_v5`；
- `chNNN.basis.json`：`chapter_story_brief_basis_v3`；
- `chNNN.agent_task.json`：活动 Agent manifest；
- `chNNN.fact_inventory.json`：控制面事实投影。

basis 绑定 v5 合同、人工意图、滚动窗口、Plot Node 表、语义义务、筛选事实、人物/作者声音、结构历史和 renderer v5。任一来源变化即 stale。

## 3. 事件、承诺与关闭

事件实现和 promise evidence 只能在 semantic apply 后写入，并必须绑定当前 final 和 semantic ledger hash。实现/兑现 span 使用 Python Unicode codepoint offset，`excerpt == final[start:end]`。

`chapter_closure_v2` 固定四项 SHA：final、semantic ledger、event ledger、reader-promise ledger。已有 closure 的任一证据漂移都会阻断幂等 close。

## 4. 设定变更与反馈

设定变更证据位于 `50_workbench/`；未来变更 apply 后更新 canonical facts 和 stale registry。影响已定稿章节时只创建 `50_workbench/revision_branches/<branch-id>/`，不改 mainline。

`50_workbench/reader_feedback/` 永远非 canonical。反馈只能形成假设、人工决定和 proposal，不能成为正文或账本事实。

## 5. Transaction v3 与恢复

transaction 报告保存 source/touched paths、before/after hash、文件快照、SQLite backup 和清理状态。`preparing` 不开放 mutation；`prepared` 才允许写；`applied` 先落盘再清理快照。

恢复只允许：

- `discard-preparing`；
- `rollback-transaction`；
- `cleanup-committed`；
- `reclaim-lock`。

所有动作都绑定 `recovery status` 返回的精确 SHA 与人工审批。

## 6. 派生视图与隐私

graph、角色状态、伏笔状态、TCS、RAG、vector 和 SQLite 都可从批准设计、final 与 semantic ledger 重建。逐章 delta 只替换本章/source owner；全量 rebuild 只由显式命令或 revision promotion 执行。

不提交小说正文、API key、完整 Prompt 日志、模型、SQLite、缓存、`dist/` 或 `novels/`。
