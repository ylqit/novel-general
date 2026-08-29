# Storage Model

本文定义 0.13.0 国内平台同人长篇稳定版的落盘合同。人物、事件、关系、能力、外观和跨界规则不再各自拥有封闭内容表；它们使用开放 `semantic_document_v1`。`fanfiction_source_canon_v1/v2/v3` 不能作为当前协议证据。

用户级资料库包含 `原件对象/`、`暂存区/`、`派生索引/`，每个动态资料项保存原件清单、不可变规范化版本、证据分段、语义候选和处理回执。一个资料项可以绑定多个 `source_asset_v1`；排序后的 asset 清单决定 bundle 哈希。项目只保存固定绑定、批准语义主张、短证据与 Canon，不复制完整原件或完整规范化全文。内部索引/作业记录服务确定性存储，不是公共人物或剧情本体。

原始位置使用 `text_span`、`structured_path`、`document_block`、`image_region`、`subtitle_cue`、`audio_time_range` 或 `video_time_range`。中文目录改名不改变 work、item、asset、segment 或 fact ID。

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
| `30_state/future_knowledge_provenance_pins.json` | `future_knowledge_provenance_pins_v1`；绑定已批准未来知识结果及其 workflow、上下文、事件、终稿、语义账本、任务 manifest/指令和候选的精确路径与哈希 |
| `70_runtime/artifacts/future_knowledge/<trigger-digest>.zip` | 每个已批准未来知识结果的不可变、内容寻址 provenance archive；保存批准文档、全部 pin 证据字节和 applied task audit，供 live 证据到期清理后的审计验证 |
| `40_manuscript/final/chNNN.md` | 唯一正文事实源 |
| `50_workbench/` | 候选、任务、审稿、反馈与审批证据，非 canonical |
| `50_workbench/同人原著资料/<作品名>/` | 项目级中文资料包、覆盖计划、固定绑定、批准提取和短证据；不含完整原件 |
| `10_bible/fanfiction/story_engine.json` | 人工批准的同人故事发动机语义文档 |
| `10_bible/fanfiction/fanfiction_bible.json` | 绑定独立复核 hash 的正式同人连续性与路线语义文档 |
| `50_workbench/fanfiction_context/chNNN.json` | 可重建且仅接受当前版本的内部 `fanfiction_context_bundle_v2`；包含显式必需 claim、带理由依赖闭包、结构化分区、作者/审阅双投影、命名冲突和预算，不是作者稿 |
| `50_workbench/fanfiction_knowledge_impacts/chNNN.<trigger-digest>.workflow.json` | 每项经人工确认的重大分歧各自对应的未来知识可靠性待审工作流；按触发身份幂等，不自动改变 Canon 或路线 |
| `50_workbench/publication/rights_decisions/<target>.decision.json` | `fanfiction_publication_rights_decision_v1`；仅保存人工 `proceed/hold`、风险说明及配置/Canon/逐来源权利声明/目标政策快照 hash，不保存原著、Prompt 或正文 |
| `70_runtime/literary_evidence/fanfiction_trials/<trial-id>/` | 两条各 20 章同人路线的匿名公开包、私有路线映射、三份独立人类评审、不可改写中位数聚合和实质分歧人工处理；正文只存在项目运行时公开盲审包，不进入仓库 |
| `50_workbench/创作沙盒/` | 可自由试验的非 Canon `semantic_document_v1` |
| `50_workbench/语义候选/` | 沙盒提升或 Host Agent 生成、仍待复核/审批的候选 |

正式正文只接受 `ch{chapter:03d}.md`；四位及以上自然扩展。任何旧命名、`.txt` 或别名均不搜索、不迁移。

## 1.1 用户级原著资料库

默认根目录位于操作系统用户数据目录的 `longform-novel-engine/原著资料库/`；`LONGFORM_SOURCE_LIBRARY` 可指定绝对路径。其中文物理结构只有 `作品/<作品名>/资料项/<动态资料名>/`，不硬编码媒介目录。完整原件只允许用户合法导入、公版或明确许可；普通网页仅保留定位、结构化事实和必要短证据。

用户资料库不进入 Git、出版包、项目审计包或 Skill。项目绑定固定作品、资料项、asset/bundle、规范化和语义候选哈希；全局文件更新只产生升级提案，不能静默改变项目。

## 2. 作者工作单

`50_workbench/writing_tasks/` 保存：

- `chNNN.json`：`chapter_writing_task_v7`；
- `chNNN.md`：作者唯一可读的 `chapter_story_brief_v5`；
- `chNNN.basis.json`：`chapter_story_brief_basis_v3`；
- `chNNN.agent_task.json`：活动 Agent manifest；
- `chNNN.fact_inventory.json`：控制面事实投影。

basis 绑定 v5 合同、人工意图、滚动窗口、Plot Node 表、语义义务、筛选事实、人物/作者声音、同人上下文 bundle hash、结构历史和 renderer v5。任一来源变化即 stale。作者 Markdown 不包含 bundle 内部 ID、hash、来源位置或检索诊断。

## 3. 事件、承诺与关闭

事件实现和 promise evidence 只能在 semantic apply 后写入，并必须绑定当前 final 和 semantic ledger hash。实现/兑现 span 使用 Python Unicode codepoint offset，`excerpt == final[start:end]`。

`chapter_closure_v2` 固定四项 SHA：final、semantic ledger、event ledger、reader-promise ledger。已有 closure 的任一证据漂移都会阻断幂等 close。

未来知识重估的“章节适用范围”与“证据保留期限”是两个合同。只有 `from_chapter <= 目标章节 <= to_chapter`（无 `to_chapter` 表示无限期）的结果进入目标章上下文；live 证据从人工批准时起保留到下一待写章节超过 `to_chapter`，因此延迟生效不会提前丢证。人工 apply 在同一 canonical transaction 内登记 provenance pin，并写入独立的不可变 provenance archive，保存批准文档、全部证据字节和 applied task audit。压缩保留仍在期限内的 live 证据和任务索引，同时正常归档其他同章工件；结果到期后可删除 live 证据而不改写既有章节归档或 provenance archive。canonical 批准文档存在但 pin 注册表缺失、覆盖不完整、哈希漂移或归档审计无效时，压缩在任何写入/删除前阻断。

发布决定是可陈旧的 workbench 状态，不是 Canon。目标平台、有效配置、当前 `source_canon.json`、任一来源的 `rights_status/commercial_intent/platform_policy_url` 或政策快照变化后，旧决定仍保留供审计但不再允许该目标导出。原创项目和全部非导出写作动作不读取该门禁。

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
