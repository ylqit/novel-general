# v0.4.3 Protocol And Production Hardening Checklist

本清单是未发布 `v0.4.3` Release Candidate 的统一验收事实源。它同时覆盖 repair 生命周期、Semantic RAG 正确性、有证据审稿、唯一章节合同、上下文去重、通用任务对账和产物归档。`v0.4.2` tag、Release 与失败证据保持不可变。

状态：`[ ]` 未实现，`[~]` 已实现但证据未收口，`[x]` 已实现且有自动化或真实运行证据。

## 1. Repair Lifecycle

- [x] `repair candidate-task` 在创建新轮次前查找同章同轮 repair 子任务。
- [x] 已存在子任务时校验 plan、review bundle、candidate snapshot、manifest、输入和输出路径的完整 hash lineage。
- [x] lineage 唯一且完整时把父 `repair_plan_synthesis` 投影为 `applied`，重复命令返回同一子任务。
- [x] 首次注册子任务后立即消费父计划，协议失败和任务重建不消耗修复额度。
- [x] 缺失或歧义 lineage 返回 `need-human`，不猜测、不宽松关闭章节。
- [x] `production next` 只读并返回唯一 reconciliation 命令。
- [x] `chapter close` 仅接受 live lineage 全部终态，不维护任务类型忽略名单。

## 2. Semantic Protocol And RAG Correctness

- [x] 章节语义模板使用 `delta_type: chapter_semantic`。
- [x] 证据 ID 使用 `40_manuscript/final/chNNN.md@start:end`。
- [x] 同 hash ledger 可幂等补建派生索引，不重写 final 或 ledger。
- [x] `semantic-apply` 依次执行 chunks、SQLite、embeddings、vector store、vector verification 和 semantic context。
- [x] embeddings metadata、vector SQLite、HNSW、query cache 和 semantic context 位于同一事务边界。
- [x] `allow_fallback=false` 时模型 fallback、零 active vector、零 semantic hit 或非 semantic context 均阻断 apply。
- [x] vector 写入失败会回滚 ledger、graph、TCS、RAG 和 SQLite 参与者。
- [x] 章节关闭前验证当前 final 对应的 chunks、vectors 与下一章 semantic context。

## 3. Evidence-Bound Review

- [x] 正式审稿协议为 `evidence_review_v2`，四类 Agent 输出协议总数不变。
- [x] 每个 `checked` 维度必须提供一至两个正文 evidence ID。
- [x] 连贯性、同人和人物状态等维度可以要求 canonical ref。
- [x] `not_applicable` 只允许角色注册表声明的可选维度。
- [x] 必审维度 `insufficient` 时禁止 `pass`。
- [x] P0/P1 必须为 confirmed 且证据可回读。
- [x] 候选 hash 改变后旧审稿结果自动失效。

## 4. Unique Chapter Contract And Context

- [x] `20_outline/chapter_cards/chNNN.json` 是唯一章节合同。
- [x] 合同保存全书目标、卷目标、主角目标、章节职责、场景链、人物、收益、代价、关系和稳定引用。
- [x] 写作、Humanizer、收益、节奏和编辑上下文使用同一 `chapter_contract_hash`。
- [x] 合同分裂返回 `chapter_contract_inconsistent`。
- [x] 核心引用缺失或包含 `[depth-limited]` 时返回 `context_evidence_incomplete`。
- [x] 写作上下文在内存编译为 `chapter_fact_inventory_v1`，同一事实只渲染一次。
- [x] 不再持久化 Creative Brief、Writable Brief、TCS、Character Packet 和反馈的完整重复副本。
- [x] `chapter_direction` delta 只保存选中方向、人工选择、canonical refs 和新增元素。
- [x] 未选方向只留在批准 Markdown 和审计包，不进入结构化章节状态。

## 5. Generic Task Reconciliation

- [x] task event/index 支持 `consumes_task_id`、`consumed_by_task_id`、`satisfied_by_result_sha256` 和 `supersedes_task_ids`。
- [x] 子任务注册后原子消费父任务，父结果、子输入和 hash lineage 必须一致。
- [x] `agent-task reconcile` 只修复能由 manifest 与结果 hash 唯一证明的投影。
- [x] 新候选提交后，旧候选和绑定旧 hash 的审稿任务进入 `superseded`。
- [x] 错误 lineage 进入 need-human，且不修改 canonical 或派生状态。

## 6. Artifact Lifecycle

- [x] 候选快照按正文 hash 存入 `50_workbench/candidate_blobs/`，相同内容只保存一次。
- [x] 章节审计包先写入并验证引用，再删除不再使用的共享 blob。
- [x] `artifacts compact --scope project-setup` 归档已完成项目任务的工作单、候选、诊断、manifest 和事件投影。
- [x] canonical Markdown、Bible、outline、章节计划和当前状态保持 loose 可读。
- [x] `artifacts status` 报告 retention class、重复 hash 与可回收字节。
- [x] `artifacts verify` 验证 project-setup archive、任务投影和 loose duplicate。

## 7. Readiness And Automated Verification

- [x] 单一 YAML fixture 登记八个早期 SAO 失败模式、严重级别、误报边界和责任审稿角色。
- [x] readiness 分为 `protocol_ready`、`production_chain_ready` 和 `literary_evidence_ready`。
- [x] 自动化结构通过不能推导文学质量结论。
- [x] 唯一精简专项测试文件覆盖 repair 生命周期、Semantic RAG 回滚、有证据 pass、核心引用截断、通用父子消费和 project-setup compact。
- [x] 最近一次完整单进程 pytest 通过（306 passed）。
- [x] Skill、resource manifest、readiness 和 release guards 通过。
- [x] 收口后的 v0.4.3 wheel/sdist 与临时 pipx 环境通过。

本地 RC 证据：专项测试 `8 passed`，完整单进程测试 `306 passed`；wheel 审计 `161 entries`，sdist 审计 `283 entries`；临时 pipx 中 CLI、module、metadata 均为 `0.4.3`，Codex 与 Claude Code Skill 均为 `current` 且资源 hash 匹配。

## 8. Runtime And Literary Evidence

- [x] 当前 SAO 运行冻结在第 10 章，不执行 semantic apply、close 或第 11 章生产。
- [ ] 使用全新原创五章与同人五章完成人工验收。
- [ ] 人工盲评完成并形成可复核文学证据。
- [ ] `literary_evidence_ready=true`。

## 9. Release Boundary

- [x] 源码、版本和发布说明收敛为 `0.4.3` Release Candidate。
- [x] 公开稳定版本继续指向真实存在的 `v0.4.2`。
- [ ] 创建不可变 annotated tag `v0.4.3`。
- [ ] GitHub Release 包含 wheel、sdist 和源码资产。
- [ ] 从 GitHub tag 更新本机 pipx Engine 与 Codex/Claude Skill metadata 到 `0.4.3/current`。

## Definition Of Done

本地 RC 收口要求第 1 至 7 节通过，并保持 SAO 运行冻结、canonical 零污染。公开发布还必须完成 wheel/pipx 审计并得到明确发布授权。人工文学证据可以晚于工程版本发布，但在 `literary_evidence_ready=false` 时不得宣称文学质量全面超越 `novel-skill`。
