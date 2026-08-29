# v0.13.0 发布 Checklist

> 状态（2026-08-29）：功能提交 `b8c4a91` 已完成一次完整单进程本地链路验证。用户明确要求发布准备阶段不重复运行本地 pytest、Ruff、Mypy、smoke 或构建；最终 `master` 的远程 CI 是 annotated `v0.13.0` tag 前唯一保留的测试门禁。远程 CI、不可变 tag、GitHub Release 制品和本机同步必须以实际执行结果为准，不能预先宣称完成。

## 协议收口

- [x] v0.12 的 `semantic_document_v1`、`agent_task_manifest_v5`、稳定 claim、独立复核、人工决定和事务 v3 边界保持不变。
- [x] 同人故事发动机只接受 `oc_si_progression | canon_character_centered | hybrid`，并要求主角与原著关系、读者识别承诺和原创主线承诺。
- [x] 原著事件命运包含职责承担者、一阶/二阶影响和卷章范围；引用只指向当前批准的稳定语义声明。
- [x] 每项获批重大分歧独立、幂等地触发未来知识重估，重复执行不重复创建。
- [x] `fanfiction_context_bundle_v2` 是唯一接受版本；v1 不双读、不静默迁移。
- [x] `platform_publication_preflight_v2` 与 `fanfiction_publication_rights_decision_v1` 已进入公共 CLI、存储、陈旧检测和导出门禁。

## 资料、语义引擎与小说生产

- [x] OC/SI、原著角色中心和混合路线的设计问题、规范化、校验、任务合同和审阅重点已同步。
- [x] 原著事件保留、结果改变、换人承担、取消和待决定均保留职责与因果链。
- [x] 连续重大分歧按稳定触发身份形成多份未来知识重估，并保持幂等和 provenance。
- [x] 章节上下文 v2 按全局规则、显式引用、依赖闭包、当前结构化范围、可选 RAG 排序；显式引用优先于姓名或关键词。
- [x] 作者投影保持自然中文；多来源或命名冲突时显示来源标签；审阅投影只包含精确 claim/evidence 闭包。
- [x] 必需声明超预算时列出最大占用项和缩小范围建议，并在任何半成品写入前阻断。
- [x] `fixed_host | fusion_world | sequential_worlds` 及实际 `payload_kinds` 驱动的来源适配器、跨界宪法和当前卷例外已完成。
- [x] 起点同人主档与番茄同人兼容档分开编译；平台观察不声称预测审核、签约或激励。
- [x] 同人发布决定只阻断目标平台导出；创作、审阅、Canon、人工定稿和原创导出不误触。
- [x] Canon、路线、上下文、审阅和发布决定继续使用原子写入、hash 绑定、可解释证据与可回滚事务。

## 版本与活动文档

- [x] 包版本、运行时、release channel、README、活动架构/存储/配置/流程文档和两套仓库 Skill 均指向 0.13.0 当前协议。
- [x] 新增 `V0_13_FANFICTION_ARCHITECTURE.md`、本 Checklist 和 `docs/releases/v0.13.0.md`。
- [x] v0.12 release note、语义架构和 checklist 已恢复为 tag 中的历史原文，不用 v0.13 行为回写旧发布记录。
- [x] sdist 审计、Skill 校验、发布面守卫、测试版本断言和 release workflow 默认版本已切换到 v0.13。
- [x] Skill references 和资源 manifest 在版本面稳定后确定性同步。

## 本地发布验证

- [x] 功能提交 `b8c4a91` 的 Ruff 通过。
- [x] 功能提交 `b8c4a91` 的 20 个关键 Mypy 模块通过。
- [x] 功能提交 `b8c4a91` 的完整 pytest 单进程通过：`665 passed in 38m12s`。
- [x] Skill/资源 manifest、引用一致性、Markdown 链接、Agent readiness、release surface guards 和 RC readiness 在功能提交上通过；RC 结果为 19 passed、0 warning、0 failure。
- [x] 本次 release metadata 提交按用户明确要求不重复本地回归、smoke 或构建；最终提交由远程 CI 完整验证。
- [x] 文学试写和盲审材料不进入仓库；当前 `literary_evidence_ready=false`。

## 提交与远程发布

- [x] 发布提交只包含引擎、抽象夹具、配置、文档、Skill 和发行元数据，不包含原著、小说正文、API key、完整 Prompt、SQLite、模型、缓存或构建目录。
- [ ] `master` 以 `--ff-only` 收敛到发布提交并推送。
- [ ] 最终发布提交的 GitHub Actions CI 全部成功；失败或状态不可验证时停止，不创建 tag。
- [ ] annotated `v0.13.0` tag 指向与远程 `master` 相同的唯一发布提交并已推送；tag 不移动、不覆盖。
- [ ] GitHub Release 已由 tag workflow 创建，并包含 wheel、sdist 和 `SHA256SUMS`。

## 本机同步

- [ ] 只有 GitHub Release 制品完整后，才从不可变 `v0.13.0` tag 强制安装 pipx `[semantic]` CLI。
- [ ] 本地 `longform-engine --version` 为 0.13.0，安装来源精确固定到 `v0.13.0`。
- [ ] 只执行 `skills update/status --tool codex` 与 `doctor --tool codex`；Codex Skill 为 current。
- [ ] Claude Skill 按用户明确范围保持不变，不以 `doctor --tool all` 作为本次验收。

完成远程 CI、不可变 tag、Release 资产与本机同步前，不得声称 v0.13.0 已完整发布。即使全部工程发布步骤完成，也不得声称取得原著授权、保证平台接受、规避 AI 检测或 `literary_evidence_ready=true`。
