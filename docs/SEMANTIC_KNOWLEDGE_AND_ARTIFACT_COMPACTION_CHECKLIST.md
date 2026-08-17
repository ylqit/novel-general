# 章节语义知识库与产物精简 Checklist

状态约定：`[x]` 已有实现和自动测试，`[~]` 工具已实现但真实项目数据尚未完成，`[ ]` 尚未完成。

架构说明见 [`SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION.md`](SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION.md)。

## Phase 1. Unified Semantic Contract

- [x] Agent 输出收敛为 `canonical_delta_v1`，CLI 内存规范化并持久化内部 `chapter_semantic_bundle_v1`。
- [x] 包含 final 路径/hash、语义摘要、因果、收益、代价、场景、事件和精确证据 span。
- [x] 包含 relationship、character、foreshadow、world、timeline deltas。
- [x] 包含检索标签、实体 ID、focus 和 changed/unchanged coverage。
- [x] 语义任务 manifest 明确 input files、唯一 output、`canonical_delta_v1`、validate/apply/failure command 和 canonical targets。
- [x] 任务最多声明 7 个输入文件，不在 Markdown 中重复嵌入 final 正文。

## Phase 2. Strict Validation

- [x] 校验 final 相对路径和 SHA-256。
- [x] final SHA-256 固定使用磁盘原始字节；候选必须由 semantic task manifest 声明，backfill 权限不能通过文件名伪造。
- [x] 第 N 章 semantic apply 要求第 N-1 章 canonical ledger 已存在，禁止乱序物化。
- [x] 校验每个 scene/delta 的 `start/end/excerpt` 精确匹配 final。
- [x] 校验场景、事件、人物、世界和时间线字段级 schema。
- [x] 校验稳定实体 ID、关系 prior state 和人物知识 route/evidence。
- [x] 校验计划伏笔 `thread_id`、动作、状态和兑现窗口。
- [x] 仅 backfill 允许显式 `unplanned:<stable-id>`。
- [x] 校验登场人物、变化人物、活跃伏笔的 changed/unchanged 完整性和互斥性。
- [x] invalid bundle 只产生 validation report，不写 semantic ledger 或派生状态。

## Phase 3. Atomic Materialization

- [x] `chapter semantic-apply` 显式、事务化更新 semantic ledger。
- [x] 同一事务更新 story graph、foreshadow state、world state 和 timeline。
- [x] 角色文件只保存有界当前状态、近期证据和未解决承诺，不再累积全部历史。
- [x] TCS 只保存下一章需要的摘要、活跃关系、开放伏笔和角色当前状态。
- [x] 语义摘要替代“正文前 240 字”伪摘要。
- [x] RAG、context、style 与 SQLite 在统一 apply 后重建。
- [x] 相同候选重复 apply 幂等；不同候选不得覆盖 canonical semantic ledger。
- [x] 相同候选重跑 apply 可修复漂移的 graph/RAG/TCS 等物化视图，且不重复累积角色证据或伏笔动作。
- [x] 成功事务快照在 commit 后删除，失败事务仍可回滚。

## Phase 4. Production Orchestration

- [x] `production next` 在 final 后优先返回 `chapter_semantic` 生命周期动作。
- [x] 新生产链不再分别创建 graph semantic、memory semantic 和 character memory 抽取任务。
- [x] 旧任务和旧命令继续可读/可执行，并可在统一 apply 时 supersede。
- [x] 前章缺 semantic ledger 或 chapter closure 时，`continue-write` 阻断下一章。
- [x] `production loop --no-apply` 可创建/校验语义任务，但停在显式 apply/close 边界。
- [x] 中文命令协议与 Codex/Claude Skill 包含统一语义和 close 流程。

## Phase 5. Chapter Close

- [x] 新增 `chapter close --approved-by`。
- [x] close 校验 final、semantic ledger、graph、foreshadow state、TCS 和 RAG chunk。
- [x] close 拒绝未通过 gate、P0/P1、editorial need-human 和活动 Agent tasks。
- [x] closure 保存 final/ledger hash 和人工批准者。
- [x] 相同证据重复 close 幂等，证据漂移时失败。
- [x] semantic ledger 或 closure 存在后禁止 `chapter finalize --overwrite` 覆盖 final 证据源。
- [x] chapter close 成功后下一命令安全指向下一章。

## Phase 6. Artifact Compaction

- [x] 新增 `artifacts status|compact|verify|restore`。
- [x] compact 默认支持 `--dry-run` 预览，并将旧章工作单、逐任务 manifest、Agent 输出和审稿材料按章创建 ZIP 与 hash manifest；全局 `agent_task_index.json` 保持 loose 状态索引。
- [x] final、semantic ledger、计划账本和当前状态视图不进入归档候选。
- [x] verify 校验 ZIP hash、条目存在性和条目 SHA-256。
- [x] compact 删除 loose file 前重验现有 ZIP、manifest 和 loose 内容；损坏或旧版本归档不得触发删除。
- [x] restore 防目录穿越，拒绝覆盖不同内容。
- [x] 最近两章保持活动工作区。
- [x] 真实 compact 要求 closure 且不得进入最近两章活动缓冲；`--dry-run` 仍允许迁移预览。
- [x] 事务成功快照残留清理有自动测试。

## Phase 7. Retrieval And Scale

- [x] 语义摘要与账本进入本地 RAG/SQLite 的可重建派生层。
- [x] TCS 与角色当前视图采用有界集合，不线性携带全部历史。
- [x] 本地单机不强制 MySQL、Milvus 或 Neo4j。
- [x] `rag query` 先用 semantic ledger 的摘要、关系、人物、伏笔、世界和标签路由章节，再加载命中 final chunk；结果解释标记 `semantic ledger routed chapter`。
- [x] 账本无词面命中时使用有界 recent + lexical 候选，不全表加载 chunks；普通 query 不再重复完整 sync SQLite。
- [x] 单章 semantic apply 只重建当前章 RAG chunk，并在构建下一章 context 前同步一次派生 SQLite。
- [ ] 在 50/200/500 章真实语义账本数据上记录关系/伏笔查询 Recall、错误事实率和 P95。

## Phase 8. Existing 15-Chapter Migration

- [x] `chapter semantic-task --backfill` 支持逐章迁移任务。
- [x] backfill 输入仍受 7 文件预算和 workbench 写入边界限制。
- [x] 新增 `chapter semantic-rebuild --through N --approved-by`，校验连续 final/ledger/hash 后，从账本事务化清空并重建所有章节物化视图。
- [x] 已对十五章项目执行只读 status 和 `compact --through 13 --dry-run`：当前为 3697 个 loose files/463105315 bytes，89 个成功快照/440267417 bytes，852 个按章候选/2958577 bytes；`removed_files=0`，未删除或归档生产文件。
- [ ] 冻结现有十五章 final、图谱、角色记忆、伏笔和 TCS hash 清单。
- [ ] 第 1-15 章真实 Agent semantic bundle 全部 validate/apply。
- [ ] 旧图谱与新语义结果冲突全部生成人工迁移决定。
- [ ] 无计划伏笔明确标为 `unplanned:*`，未伪装为计划线程。
- [ ] 从十五章语义账本重建 graph、角色当前视图、foreshadow state、TCS、SQLite 和 RAG。
- [ ] `artifacts compact --through 13 --dry-run` 人工确认后显式执行。
- [ ] 迁移后 loose files 不超过约 650、项目体积不超过约 35 MB、成功快照残留为零。
- [ ] 第 14-15 章保持活动状态，`production next` 安全指向第 16 章。

## Phase 9. Regression And Release

- [x] unified semantics 和 artifact lifecycle 有独立自动测试。
- [x] no-pollution 覆盖 hash/span 错误与事务失败。
- [x] 完整 pytest 通过：300 passed。
- [x] `python scripts/sync_skill_references.py --check` 通过。
- [x] `python scripts/build_resource_manifest.py --check` 通过。
- [x] `python scripts/validate_skills.py` 通过。
- [x] `python scripts/release_surface_guards.py` 通过。

## Definition Of Done

新项目生产链完成要求 Phase 1-6 和 Phase 9 全部为 `[x]`。现有十五章精简完成要求 Phase 8 全部为 `[x]`。检索规模声明还要求 Phase 7 的真实数据项完成。在此之前不得声称十五章已经迁移或文件规模目标已经达到。
