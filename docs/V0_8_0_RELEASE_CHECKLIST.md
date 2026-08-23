# v0.8.0 Story Brief 一致性与过时代码清理 Checklist

本文记录 `v0.8.0` 不兼容源码阶段的实现与本地验证状态。本轮没有发布授权：不得推送 tag、创建 Release、迁移旧项目、覆盖仓库外全局 Skill，或把 `v0.8.0` 宣称为公开稳定版。

## 1. 章节合同与 Story Brief

- [x] `chapter_contract_v4` 纳入 `emotional_aftereffect`、`ending_mode`、`ending_intent`、`must_preserve_suspense`、`resolution_markers`。
- [x] 删除 `hook`、`hook_mode`、`plot_obligation`、`irreversible_action`、卡片级 `dramatic_freedom` 影子字段。
- [x] 旧别名进入 tombstone 校验，不能被静默归一化。
- [x] 合法收束章节允许空悬念保护和空兑现标记。
- [x] 新增 `chapter_story_brief_basis_v1`，绑定合同、因果模拟、事实、人物声音、作者声音、结构历史、质量合同与 renderer。
- [x] 升级 `chapter_story_brief_v3` 与 `chapter_writing_task_v5`。
- [x] Story Brief 仅显示作者可执行信息，不暴露 hash、内部 ID、finding code、原始 RAG 包或控制面术语。
- [x] 写作任务只有在状态、合同、basis、Markdown 与活动 manifest 全部一致时才可复用。
- [x] stale、superseded、文件漂移或编译依据变化会原子化重建工作单。

## 2. 人工修订、审稿与咨询

- [x] 升级 `human_review_bundle_v2`。
- [x] 升级 `human_author_revision_v2`，绑定当前 Story Brief basis。
- [x] 升级 `human_story_review_v5`，人工接受绑定七类当前证据。
- [x] 咨询 session、请求、验证与记录升级为 v2，并绑定当前候选与 basis。
- [x] 候选或 basis 变化会使旧咨询、审稿和人工接受 stale。
- [x] `quality status --json` 报告合同、basis、manifest 三项当前性。
- [x] v0.7 卡片、人工修订和人工接受协议被明确拒绝，并给出新建项目、人工导入说明。
- [x] 已定稿同稿 `--overwrite` 只在既有人工证据完整且未 semantic apply / close 时幂等返回；不会因本章定稿后结构历史追加而伪造新修订要求。

## 3. 过时代码

- [x] 删除 `detect_quota_usage()` 与不可达重复 `return`。
- [x] 删除 `plot_quota_overflow`、A/B/C 关键词推断及对应 P1。
- [x] 删除 A/B/C 专用默认配置、Prompt 文案与正向测试。
- [x] 旧 A/B/C 配置字段给出明确 v0.8 修复错误。
- [x] 保留基于场景证据的语义节奏审稿和 fast-event 限额。
- [x] 保留配置 tombstone、旧协议拒绝、`reader_payoff`、`rc` 通道和历史 release 记录。

## 4. README 与活动文档

- [x] README 压缩到约 280–320 行，只保留定位、安装、单一路径、章节闭环、边界和质量声明。
- [x] README 标明 v0.7 是稳定版、v0.8 是未发布源码阶段。
- [x] 新增发布历史索引；历史 release checklist 不批量改写。
- [x] Architecture、Pipeline、Storage、Configuration、Gate 和 SQLite 活动文档改为当前协议。
- [x] `shared/` 创作操作协议、命令映射、工作流与铁律改为当前协议。
- [x] 两套仓库内 Skill 引用由同步脚本生成并通过一致性检查。
- [x] 活动源码与文档 schema 守卫通过；历史与显式拒绝逻辑进入允许列表。

## 5. 回归测试

- [x] 修改合同 v4 义务会同时改变合同 hash 与 Story Brief basis。
- [x] 只修改人物声音会保持合同 hash、改变 basis，并重建作者 Markdown。
- [x] superseded 写作 manifest 不能被复用。
- [x] Story Brief 包含必要人物声音，且不暴露内部 hash / code。
- [x] v0.7 章节卡给出明确不兼容错误。
- [x] 多个情节词同时出现不会触发已删除的关键词配额 P1。
- [x] 缺少旧写作任务时，人工修订状态接口返回 pending 而不是抛出文件异常。
- [x] 咨询、审稿、人工接受在 basis-only 漂移时全部 stale。
- [x] 空稿、Prompt 残留和大段精确重复仍阻断；慢章、无对白与非悬崖结尾不自动产生 P1。

## 6. 单进程验证

- [x] Ruff lint。
- [x] Mypy 关键协议模块类型检查。
- [x] 完整 pytest 单进程通过。
- [x] 仓库内 Skill 同步与校验通过。
- [x] Markdown 链接检查通过。
- [x] 资源清单重建与 check 通过。
- [x] wheel / sdist 构建成功。
- [x] wheel 与 sdist 分发审计通过。
- [x] 隔离安装与 CLI smoke 通过。

2026-08-22 本地证据（除下述 RC 状态诊断外均为退出码 0）：

- `uv run ruff check .`：`All checks passed!`。
- 发布运行手册的九个关键协议模块执行 `mypy --follow-imports=skip`：`Success: no issues found in 9 source files`。编排与门禁超大模块仍有既存类型债务，本清单不宣称全仓库严格 Mypy 通过。
- `uv run --extra dev --extra semantic pytest -q -p no:xdist`：`390 passed in 1186.97s`。
- Skill 同步、Skill 校验、资源 manifest、Markdown 链接、活动发布面守卫和数据管线 readiness 全部通过；Markdown 检查覆盖 177 个文档、44 个本地链接，`literary_evidence_ready=false`。
- 构建生成 `longform_novel_engine-0.8.0-py3-none-any.whl` 与 `longform_novel_engine-0.8.0.tar.gz`；wheel 176 项、sdist 267 项资源审计通过，`SHA256SUMS` 写入后复核为 current。
- 临时虚拟环境从本地 wheel 安装成功，`longform-engine --version` 返回 `0.8.0`，`validate-config --template qidian-longform --explain` 通过；未写入全局 Skill。
- `release check --channel rc` 仅用于状态诊断：源码尚位于 `v0.7.0` 标签对应的未提交工作树，因此 RC 的“HEAD 必须未打 tag”条件不满足。本轮禁止 commit/tag，未把该状态伪装成发布就绪。

## 7. 明确未执行

- [x] 未发布 v0.8.0。
- [x] 未推送 tag 或 Release。
- [x] 未自动迁移 v0.7 项目。
- [x] 未同步仓库外全局 Skill。
- [x] 未把 `literary_evidence_ready` 改为 true。

当前文学证据状态保持 `literary_evidence_ready=false`。
