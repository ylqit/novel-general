# Pipeline Model

v0.12.0 语义优先稳定版的正式生产主链只接受当前协议。创作沙盒是独立非 Canon 通道，只有人工提升、独立复核和正式审批后才能进入本链：

同人项目在通用规划链之前增加：

```text
动态资料覆盖 / 项目原著基线 Canon
-> 同人故事发动机 / 人工 apply
-> 同人路线 / 隔离独立复核 / 人工 apply
-> 活动卷同人投影、原著事件命运和重大节点决定
-> fanfiction_context_bundle_v1
```

```text
planning_bundle_v1
-> structural validate
-> independent planning semantic review
-> human planning decision
-> every firm state-changing plot-node human decision
-> atomic planning apply
-> chapter_contract_v5
-> human_chapter_intent_v2
-> chapter_story_brief_basis_v3
-> chapter_story_brief_v5 / chapter_writing_task_v7
-> chapter_write / optional chapter_coedit_session_v2
-> deterministic gate + independent reviews
-> human_author_revision_v4 + isolated semantic preservation review
-> human_story_review_v7
-> finalize
-> canonical_delta_v1 semantic apply
-> human-confirmed event realization
-> exact reader-promise evidence
-> chapter_closure_v2 / planning_cursor advance
```

## 规划

规划候选同时包含 Book Spine、分卷骨架、活动卷、20 章滚动窗口、forecast、语义义务、Plot Node 表和 firm v5 合同。结构校验不作文学判断；独立语义审查必须绑定 exact bytes；整体批准和重大状态变化节点的逐项批准分别保存。微观对话、动作、过渡和局部节拍由章节合同约束，不创建审批配额。

任一 `reject|defer` 节点阻断 apply。活动卷只能有一个；firm 层始终以三个有效合同为目标。少于三个、换卷或 basis 漂移时 `production next` 返回 `planning_refresh_required`。

## 写作

`continue-write` 不生成旧章节卡或 Beat Sheet。它验证 v5 合同、批准节点、语义义务、滚动 basis 和空白填写的人工意图，然后编译 renderer v5。

作者 Markdown 只含可读故事信息。控制面 ID、hash、原始 RAG、平台诊断、Graph、TCS 和 SQLite 不进入作者工作单。

## 审稿与 Final

Gate 的词频、句长、对白率、慢章和尾钩信号最多是 P2。P0/P1 必须有可解释证据，并进入不可变 repair。AI 候选必须经过真实人工完整修订、最终锁和独立双稿语义复核；最终 v7 深审绑定当前 v0.10 规划证据。

## 语义应用与关闭

semantic apply 只物化章节事实和派生视图，不自动兑现承诺。之后逐项确认获批事件状态，并为每个非 defer reader-promise 动作提供当前 final 精确 span。缺失、部分实现、矛盾或 stale hash 均阻断 close。

## 设定和反馈

未来设定变更通过 stable fact ID 的依赖闭包使 forecast、合同、节点和任务 stale；影响已有 final 时创建 `revision_branch_v2`。读者反馈只形成非 canonical 假设与 proposal。
