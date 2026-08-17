# v0.4.2 Repair Coordination Checklist

本清单验收统一审稿屏障、修复主编、不可变修章轮次与 no-pollution 边界。`v0.4.1` SAO 运行保持只读失败证据；本轮不修改其正文、任务或报告。用户已于 2026-08-18 明确授权创建 `v0.4.2` release commit、tag 与 GitHub Release。

状态：`[ ]` 未实现，`[~]` 已实现但证据未收口，`[x]` 已实现且有自动化证据。

## Phase 1: Review Barrier

- [x] 确定性检查、语义连贯、Reader Payoff、节奏和风险编辑审稿分别记录完成状态与内容 verdict。
- [x] 普通内容 P0/P1 不会提前终止后续独立审稿。
- [x] `semantic_review_required` 只作为流程状态，不进入 repair finding。
- [x] 所有必审结果必须绑定同一候选 SHA-256，陈旧、缺失、重复或无效结果不能形成 review bundle。
- [x] 少数派且证据有效的 P0/P1 保留在 CLI aggregate 中，不能由多数票抹除。
- [x] 空正文、不可读文件和无效协议仍可在任务边界提前拒绝。

## Phase 2: Repair Coordinator

- [x] 角色注册表包含第 28 个角色 `repair_coordinator`，任务唯一映射为 `repair_plan_synthesis`。
- [x] 角色合同包含 finding admission、根因聚类、依赖排序、修复半径、保护账本、冲突处理和回归控制。
- [x] 修复主编只读取不可变候选、冻结 review bundle 和紧凑章节约束。
- [x] 修复主编不得写正文、重审 reviewer、删除或降级有效 P0/P1、修改 canonical 状态。
- [x] 角色使用现有 `design_document_v1`，没有新增第五类 Agent 输出协议。

## Phase 3: Repair Plan Contract

- [x] 计划包含候选 hash、轮次、完整 finding、根因、依赖、最小范围、保护项、允许变化、冲突、回归清单和完成判据。
- [x] CLI 为 finding 分配稳定 `RF-xxxxxxxxxxxx` ID。
- [x] 所有策略接纳的 finding 必须各出现一次；未知或策略未选择的 P2 被拒绝。
- [x] P0/P1 严重级别必须原样保留。
- [x] repair target 与 preserve 冲突时生成明确 `need-human`，不能创建修章任务。
- [x] 已验证计划按 hash 幂等；任何字节变化都会触发 immutable 拒绝。
- [x] plan、review bundle、候选快照和活动 draft 绑定同一 SHA-256。

## Phase 4: Immutable Attempts

- [x] 计划任务 ID 使用 `repair_plan_synthesis:chNNN:rNN:v4`。
- [x] 修章任务 ID 使用 `repair:chNNN:rNN:v4`。
- [x] review bundle、plan、manifest 和 candidate 分轮保存，不覆盖上一轮。
- [x] manifest 读取不可变候选快照，不把可变活动 draft 当作修章事实输入。
- [x] 只有完整替代稿成功提交才消耗一次内容修复额度。
- [x] 无效审稿、无效计划、Prompt 重跑和任务重建不消耗修复额度。
- [x] 每轮替代稿提交后，全部审稿结果因候选 hash 变化而失效并重新执行。
- [x] 两轮后仍有 P0/P1 时进入 `repair_budget_exhausted`，不返回第三轮命令。

## Phase 5: State And Safety

- [x] 状态机可区分 `reviews_pending`、`review_bundle_ready`、`repair_synthesis_pending`、`repair_plan_validated`、`repair_candidate_pending` 和 `repair_budget_exhausted`。
- [x] `production next` 先按证据推导章节阶段，旧 `chapter_write` 或 repair task 不能跨阶段抢占。
- [x] 旧 `repair-chapter --plan-only` 双事实源已从运行时 CLI 和 gate 模块删除。
- [x] 修复主编与修章候选只能写 workbench。
- [x] 任一失败不得写入 final、Bible、outline、graph、RAG、TCS 或 SQLite。
- [x] `repair status` 为只读命令；synthesis/validate/candidate task 才获取项目锁。

## Phase 6: Product Surface

- [x] Codex 与 Claude Code 共享协议改为 review barrier 与 repair coordinator 工作流。
- [x] `/工程修复状态`、`/工程修复主编`、`/工程校验修复计划`、`/工程候选修章` 有直接命令映射。
- [x] Skill 引用同步后不再出现 `repair-chapter`。
- [x] 角色/任务/readiness 计数更新为 28 个角色、25 类任务、84 项专业对象。

## Phase 7: Verification

- [x] 扩展现有综合测试，没有新增测试文件。
- [x] 覆盖先完成全部审稿、流程状态不进入 finding、不可变 r01/r02、冲突 need-human、额度只在提交时消耗和无第三轮命令。
- [x] 完整单进程 pytest 通过（296 passed）。
- [x] Skill 同步与 `validate_skills.py` 通过。
- [x] readiness、resource manifest 与 release guards 通过。
- [x] 用户已明确授权创建 v0.4.2 release commit、tag 与 GitHub Release。
- [~] 远端 tag、Release workflow 与资产状态由本轮发布执行后外部核验。

## Definition Of Done

- [x] 一项正文缺陷只有一个冻结 finding 集合和一份当前轮次修复计划。
- [x] 修复主编能合并同根问题，但不能隐藏任何有效阻断项。
- [x] 两轮修复失败会诚实停在人工决策点，且 canonical 污染为零。
- [x] `v0.4.2` 发布动作已获得用户明确授权。
