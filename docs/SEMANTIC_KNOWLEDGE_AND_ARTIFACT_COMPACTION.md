# 章节语义知识库与产物精简架构

## 1. 目标

本架构解决同一章被图谱、章节记忆、角色记忆和节奏任务重复读取的问题。新生产链以 final 为唯一正文证据，以每章一个 `chapter_semantic_bundle_v1` 为事实增量，以图谱、角色当前视图、伏笔状态、TCS、SQLite 和向量库为可重建物化视图。

该调整不在 Python 内调用 LLM。Codex 或 Claude Code 负责语义理解，CLI 负责路径、hash、span、前置状态、事务、索引和污染边界。

## 2. 四层数据模型

| 层 | 主要路径 | 责任 |
| --- | --- | --- |
| 正文证据层 | `40_manuscript/final/chNNN.md` | 唯一完整正文事实来源，永久保留 |
| 章节语义账本 | `30_state/semantic_ledger/chNNN.json` | 证据化摘要、场景、事件、关系、人物、伏笔、世界和时间线增量 |
| 当前状态视图 | `story_graph.json`、`foreshadowing_state.json`、角色当前视图、TCS | 面向下一章写作的有限当前状态，可由账本重建 |
| 派生检索层 | SQLite、RAG chunks/context、向量索引 | 用于候选筛选和证据定位，不拥有 canonical 事实 |

摘要只负责把查询路由到正确章节。关系变化、人物知情、伏笔兑现和世界规则变化必须回到 final 的精确 `start/end/excerpt` 证据。

## 3. 统一语义包

`chapter_semantic_bundle_v1` 包含：

- final 相对路径和 SHA-256。
- 语义摘要、因果变化、读者收益和代价。
- 场景、事件、参与角色、地点、目标、结果和精确证据。
- 关系前置状态、新状态、类型、原因和证据。
- 角色目标、信念、知识来源、承诺、能力、资源和情绪变化。
- 使用计划 `thread_id` 的伏笔埋设、强化、误导、兑现或过期。
- 世界状态、时间线、检索标签、实体 ID 和焦点。
- 本章登场角色和活跃伏笔的 changed/unchanged 完整性声明。

校验失败只写 workbench validation report。SHA-256 以 final 的磁盘原始字节为准，避免 Windows CRLF 与 LF 规范化产生两个 hash。候选必须是 semantic task manifest 声明的唯一输出；`backfill` 权限只信 manifest，不信文件名。final hash 不一致、证据 span 不精确、实体 ID 未知、delta ID 重复、关系旧状态不匹配、人物知识无来源、伏笔越过窗口、覆盖声明缺失或前一章账本尚未 apply 时，不得 apply。

## 4. 章节关闭流程

```text
draft submit
-> deterministic/semantic/payoff/editorial gates
-> chapter finalize --approved-by human
-> chapter semantic-task
-> Agent writes one chapter_semantic_bundle_v1
-> chapter semantic-validate
-> explicit chapter semantic-apply
-> verify graph/character/foreshadow/TCS/RAG/SQLite
-> chapter close --approved-by human
-> next chapter
```

`finalize` 不再截取正文前 240 字作为摘要，也不提前建立图谱、RAG 或 SQLite。语义账本或 closure 已存在后，final 成为不可覆盖的证据；修订必须走显式迁移流程。`semantic-apply` 在一个事务中更新全部物化视图；任一步失败时回滚所有 touched paths。相同候选可重跑以修复漂移的物化视图，并去重角色证据和伏笔动作；不同候选不能覆盖已存在的章节语义账本。

`chapter close` 要求 final、语义账本、图谱、伏笔状态、下一章 TCS、RAG chunk、通过的 gate、无 P0/P1、无 need-human 和无活动 Agent task。相同证据重复 close 返回现有结果；证据 hash 漂移时拒绝关闭。

## 5. 检索策略

检索分两阶段：

1. 使用实体 ID、关系、伏笔、章节范围、语义摘要和索引元数据筛选相关章节。
2. 读取命中 final 的 scene/span 或 RAG chunk，向写作 Agent 提供可核对原文证据。

账本无词面命中时也只读取有界的 recent + lexical SQLite 候选，不回退到全表 chunk 扫描。普通 query 不再每次完整重建 SQLite；数据库不存在时才初始化。单章 semantic apply 只重切当前章 RAG chunk，再同步一次派生数据库。写作工作单默认携带当前状态、开放关系、活跃伏笔和少量命中证据，不线性拼接全部角色历史或全部章节摘要。SQLite 和向量索引损坏时应由 final 与语义账本重建，不得反向覆盖它们。

## 6. 产物生命周期

`chapter close` 保留最近两章为活动工作区。更早章节中可审计但不参与正常读取的工作单、Agent 输出、门禁中间件和审稿材料，可进入：

`70_runtime/artifacts/chapters/chNNN.zip`

每个 ZIP 配套 manifest，记录每个条目的相对路径、SHA-256 和大小。旧章逐任务 `*.agent_task.json` 随工作单归档，全局 `agent_task_index.json` 保持 loose 状态索引。归档文件不可静默改写；删除 loose file 前必须校验 ZIP、manifest、条目 hash 和待删除文件内容，恢复时拒绝覆盖内容不同的 loose file。`--dry-run` 可预览任意范围，真实 compact 只允许已有 closure 且位于最近两章活动缓冲之前的章节。

```text
longform-engine artifacts status project.yaml
longform-engine artifacts compact project.yaml --through 13 --dry-run
longform-engine artifacts compact project.yaml --through 13
longform-engine artifacts verify project.yaml
longform-engine artifacts restore project.yaml --chapter 1
```

final、章节语义账本、计划伏笔账本、图谱和当前状态视图不进入 ZIP。成功事务在 commit 后立即删除回滚快照；失败事务保留诊断证据。

## 7. 旧项目兼容与十五章迁移

旧 graph semantic、memory semantic、character memory 和旧摘要继续可读，相关 CLI 继续存在。新 `production next` 不再为同一 final 分别创建这些任务。

十五章项目迁移必须逐章执行，不能从旧派生状态猜测新事实：

1. 保存 final、图谱、角色记忆、伏笔账本和 TCS 的迁移前 hash 清单。
2. 对第 1-15 章依次运行 `chapter semantic-task ... --backfill`。
3. Agent 每次只读该章 final、章节卡、计划伏笔和前一章状态，输出统一语义包。
4. 逐章 validate/apply；冲突进入人工处理。历史无计划伏笔只能使用 `unplanned:<stable-id>`。
5. 执行 `chapter semantic-rebuild project.yaml --through 15 --approved-by human`，只从连续 canonical ledgers 事务化重建物化视图和派生索引，并比较迁移报告。
6. 先运行 `artifacts compact --through 13 --dry-run`，人工确认后再真正归档。
7. 校验第 14-15 章仍为活动工作区，`production next` 安全指向第 16 章。

回填任务需要真实 Agent 阅读十五章 final，本次代码实现不会伪造这些语义 JSON，也不会自动删除现有运行产物。

## 8. 外部数据库边界

单机写作继续使用文件、SQLite 和本地向量后端。MySQL、Milvus、Neo4j 不会提高语义抽取本身的准确率，反而增加部署、事务一致性和事实源冲突成本。只有出现多人并发、远程共享、千万级 chunk 或独立图查询服务需求时，才评估外部数据库；即使引入，它们仍只能是可重建服务层。

## 9. 安全边界

- Agent 只读 manifest `input_files`，只写 `allowed_output_paths`。
- Agent 不直接写 final、semantic ledger、graph、character views、foreshadow state、TCS、RAG 或 SQLite。
- `semantic-apply` 与 `chapter close` 必须显式执行。
- 不自动 finalize，不使用摘要代替证据，不恢复脚本内 LLM/provider。
- invalid Agent JSON、迁移冲突和归档校验失败不得污染 canonical state。
