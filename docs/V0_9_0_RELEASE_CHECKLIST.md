# v0.9.0 实现与发布检查清单

本清单记录 v0.9.0 不兼容协议的实现证据和发布决策。实现阶段曾完成下列单进程验证；2026-08-23 用户另行授权直接发布，并明确要求本次不重跑本地验证、不等待主分支 CI，也不运行远程测试或 smoke。该例外不得被表述为“完整发布验证通过”。

## 章节合同与 Story Brief

- [x] `human_chapter_intent_v1` 从空白表单开始，绑定人工方向选择与 `chapter_contract_v4`。
- [x] Story Brief basis v2、Brief v4、writing task v6 绑定当前人工意图。
- [x] 旧任务、合同、basis、意图或活动 manifest 漂移时禁止复用写作工作单。

## 人工修订、审稿与咨询

- [x] `chapter_coedit_session_v1` 与 turn v1 只生成完整 workbench 候选。
- [x] `human_author_revision_v3` 保存 intent ref、读者影响、双侧 span 与 `human_final_lock_v1`。
- [x] `human_story_review_v6` 绑定八类当前证据。
- [x] coedit 可生成完整替代稿任务；human final 咨询保持只读。
- [x] `anti_template_editor` 与 `prose_naturalness` 替代旧公开名称，旧名称仅作为 tombstone 报错。

## 文学证据

- [x] blind review v4 使用“文字自然度”，所有分数方向统一为越高越好。
- [x] 三个不同题材覆盖起点开篇、番茄开篇与十五章连载三类 scope。
- [x] 绝对中位数、相对提升、三名独立人类评审、三分之二偏好和长期失败均有明确门槛。
- [x] 未导入真实盲评时保持 `literary_evidence_ready=false`。

## 过时代码

- [x] 移除活动面的 `anti_ai_editor`、Humanizer 静默别名和 AI taste 反向评分。
- [x] 历史 release checklist 保留为审计记录，不改写历史事实。

## README 与活动文档

- [x] README 保持约 280–320 行，并说明 v0.9 未发布、人类主导边界和八类证据。
- [x] Architecture、Storage、Configuration、Pipeline 与两套仓库内 Skill 已同步 v0.9 流程。
- [x] 活动文档不再把 Humanizer、七类 hash 或平台固定章数写入作者流程。

## 回归测试

- [x] 空白人工意图、意图/合同/basis 漂移与旧任务失效均有覆盖。
- [x] coedit 多轮完整候选、repair 预算、人工终稿只读阶段与 hash stale 均有覆盖。
- [x] 自然度 P2 信号、反模板 P1 精确证据与跨章结构观察均有覆盖。

## 单进程验证

- [x] Ruff 全源码通过。
- [x] 关键协议 Mypy 通过。
- [x] 单进程完整 pytest 通过（395 passed）。
- [x] Skill、资源清单和 Markdown 守卫通过。
- [x] wheel 与 sdist 构建审计通过（wheel 178 项、sdist 271 项）。
- [x] 隔离安装验证通过（wheel 安装、CLI 版本与内置模板配置验证）。

## 发布授权与无测试例外

- [x] 用户已明确授权推送 `master`、创建不可变 `v0.9.0` tag、创建 GitHub Release，并在 Release 成功后更新本机 Codex Skill。
- [x] 本次发布不重跑本地 Ruff、Mypy、pytest、构建审计或隔离安装。
- [x] 本次发布不等待 Windows Python 3.10/3.11/3.12、semantic Skill/doctor 或 macOS pipx smoke。
- [x] tag 工作流只构建 wheel/sdist、生成 `SHA256SUMS` 并上传 Release；它不提供测试或分发审计结论。
- [x] `master` CI 可异步执行，但无论结果如何均不作为本次 tag 或 Release 的门禁。
- [x] 作品和旧项目不迁移；仓库外仅按授权更新本机 Codex CLI/Skill。
- [x] 不宣称规避检测、平台必过、人工写作占比、法律作者身份或 `literary_evidence_ready=true`。

远程 tag、Actions run 和 Release assets 是外部发布记录，不通过移动 tag 或发布后证据提交回写。本次完成条件仅为制品构建上传成功及本机更新命令成功，不代表质量验证。
