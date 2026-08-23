# longform-novel-engine v0.10.0 Release Checklist

本清单记录 v0.10.0 的破坏性协议接线和经用户明确授权的发布例外。历史 release 文档保持原样。

## 协议收口

- [x] `chapter_contract_v5` 是唯一正式章合同；v4 章节卡不再由生产主链读取。
- [x] 写作链统一为 `human_chapter_intent_v2`、`chapter_story_brief_basis_v3`、`chapter_story_brief_v5`、`chapter_writing_task_v7` 和 renderer v5。
- [x] `chapter_coedit_session_v2`、`human_author_revision_v4`、`human_story_review_v7` 拒绝 v0.9 证据。
- [x] 活动卷、20 章滚动窗口、firm 三章、独立规划语义审查和逐节点人工决定已接入 `production next`。
- [x] semantic apply 后逐项确认事件实现；非 defer promise 动作需要 final/semantic ledger 精确 span；未完成阻断 close。
- [x] chapter close 写入 `chapter_closure_v2` 和 `planning_cursor_v1`。
- [x] `canonical_fact_v2`、设定提案、确定性 dependency impact、独立语义审查和人工决定已接入。
- [x] `must_stale` 只由稳定 ID/显式依赖闭包生成，不能被降级；未来变更 stale 下游，历史变更路由 `revision_branch_v2`。
- [x] 人工读者反馈保持 non-canonical，只能形成 planning proposal 或 canon-change seed。
- [x] 起点为主要编辑画像，番茄为非阻断观察；不声明检测规避、平台必过或人工写作比例。
- [x] `literary_evidence_ready=false`。

## 版本与活动文档

- [x] 包、运行时、release channel、工作流默认 tag、README、安装文档、架构/存储/配置、仓库 Skill 统一为 0.10.0。
- [x] 新增 `docs/releases/v0.10.0.md`，历史 release 文档未改写。
- [x] 测试与发布守卫期望值已更新到 v0.10 schema，但本次不执行测试。

## 明确的验证例外

用户明确要求最终发布阶段不运行 pytest、Ruff、Mypy、构建审计、安装 smoke、doctor 或语义 smoke，也不等待主分支 CI。

第一阶段曾有 `415 passed in 1254s`，但发生在最终 v0.10 接线之前，只作为历史开发证据；不得表述为本 release commit 的测试结果、回归结果或发布门禁。

本次只允许运行以下非测试型一致性诊断：

- [x] `python scripts/sync_skill_references.py --check`（退出码 0）
- [x] `python scripts/build_resource_manifest.py --check`（退出码 0）
- [x] `longform-engine release check --repository . --channel rc --json`（退出码 0；18 pass，1 个安装版本预期 warning，0 failure）

提交前综合诊断的全部合同子检查已通过；当时 `HEAD` 仍精确位于历史 `v0.9.0`，因此 RC 的 `head_untagged` 检查按设计等待本次发布提交后重跑。

## 提交与远程发布

- [x] 全部当前工作树改动提交为 `release: publish v0.10.0`。
- [ ] 推送 `master`，不等待 CI。
- [ ] 创建并推送 annotated immutable tag `v0.10.0`；失败时不移动或覆盖 tag。
- [ ] 等待 GitHub Release，确认 wheel、sdist、`SHA256SUMS` 三项制品存在。

## 本机同步

Release 成功后：

- [ ] 使用现有 pipx 和 Python 3.12 从不可变 `v0.10.0` tag 强制升级全局 CLI，只记录退出状态。
- [ ] 执行 `longform-engine skills update --tool codex`，只记录退出状态。
- [ ] 不运行 `--version`、Skill status、doctor 或任何 smoke；不更新 Claude Skill。
