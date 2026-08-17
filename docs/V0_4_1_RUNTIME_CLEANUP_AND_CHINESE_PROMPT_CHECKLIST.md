# v0.4.1 Runtime Cleanup And Chinese Prompt Checklist

本清单约束 v0.4.1 的旧链路清理、中文专业 Prompt、单进程生产和真实质量准入。状态含义：`[ ]` 未完成，`[~]` 已实现但缺验证，`[x]` 已实现并有可重复证据。

## Phase 0. Baseline And Destructive Audit

- [x] 冻结 v0.4.0 commit、tag、资源版本和全局安装版本。
- [x] 枚举 `novels/` 下全部项目并识别非 `schema_version: 2` 项目。
- [x] 删除前记录 resolved path、文件数、字节数、配置 hash 和 final hash，不保存正文副本。
- [x] 删除旧项目后验证目录不存在且其他工作区未受影响。

## Phase 1. Remove Legacy Runtime

- [x] 删除 legacy status/backfill/compact、旧模型迁移和旧 init 别名。
- [x] 删除 AgentTaskManifest v1/v2、旧结果适配器和 `legacy_document_json`。
- [x] 删除 graph/memory/character-memory 的独立 Agent 任务与三个 legacy 角色。
- [x] 新生产链只允许 `chapter_semantic` 物化 graph、memory、foreshadow、TCS、RAG 和 SQLite。
- [x] release guard 能阻止 legacy runtime 符号重新进入 wheel。

## Phase 2. Manifest V4 And Sequential Lifecycle

- [x] 新任务只注册不兼容旧格式的 `AgentTaskManifest v4`，磁盘顶层固定为 scope、role、io、policy、commands 等八组字段。
- [x] manifest 只记录角色合同、实际区段、Playbook 区段、overlay 与唯一 selection hash；完整编译 Prompt hash 只写 brief/event。
- [x] `production next` 每次只返回一个动作，不启动子进程、worker 或并行 Agent。
- [x] 每章只有一个当前正文候选，替代稿成为当前候选后旧稿 superseded。
- [x] unified semantic apply 失败时所有 canonical 物化全部回滚。
- [x] 关闭章节只松散保留 final、semantic ledger、closure 和必要当前状态。

## Phase 3. Progressive Chinese Role Registry

- [x] `agent_role_registry_v3` 注册 27 个活动角色，不含 legacy、generic fallback 或 Agent 总编辑。
- [x] 所有活动角色使用中文主合同，机器 ID、schema 和命令保持英文。
- [x] 每个角色源文件按 `always/task/trigger/reference_only/calibration_only` 分段，不设置最小字符数。
- [x] 每个角色记录 required/optional playbook、最多激活数、finding codes、review dimensions 和受限 overlay。
- [x] 运行时 manifest 记录实际选择的角色区段、Playbook 区段及 hash，不再保存 bundle hash 或独立 reasons 数组。
- [x] Reader Payoff、Reader Experience、单章人物审稿和跨章人物审稿职责不重叠。
- [x] CLI 确定性 aggregate 取代 Agent 总编辑投票。
- [x] Codex/Claude 渲染保持同一角色语义；真实生产只使用 Codex 单宿主。

## Phase 4. Chinese Craft Playbooks

- [x] 覆盖开篇主线、场景、人物、对白、心理、世界设定、关系、伏笔、节奏、反 AI、群像和同人。
- [x] 每个 Playbook 均分离核心、创作、审稿、修复、题材适配、校准微例和误报控制区段。
- [x] 题材继续按 setting、plot engine、narrative form、premise、relationship 和 tone 正交组合。
- [x] 每章最多激活三个题材分面，不把全部标签塞入 Prompt。
- [x] 创作任务最多激活三个 Playbook，独立审稿最多两个；未触发案例和参考区段不进入工作单。
- [x] 每个角色和 Playbook 都有自有正反校准例，但 `calibration_only` 默认不进入生产工作单。
- [x] 固定 8K/20K/18K 阈值已由 `compact/standard/large` 自适应容量档取代，项目可显式覆盖容量。
- [x] Prompt 只加载当前角色、当前步骤、最多三个 Playbook 和最多三个中文故事分面适配器。
- [x] 27 个角色具有专属专业判定表；11 个审稿角色逐项定义 finding code、证据、严重级别和误报边界。
- [x] 12 个 Playbook 的题材适配段不重复，分别定义创作、诊断、最小修复和保护项。
- [x] 混合会话策略已覆盖项目协调、每章作者、独立修订、独立审稿和独立档案。
- [x] 不使用机械禁词、固定短句率、固定对白率或强制悬崖结尾。

## Phase 5. Four Agent Output Protocols

- [x] 24 类任务严格映射到 `prose_markdown_v1`、`design_document_v1`、`evidence_review_v1`、`canonical_delta_v1` 四类协议。
- [x] 每个 Agent 任务默认只有一个输出文件，Agent 不机械回填 CLI 已知路径、hash、章节号或命令。
- [x] 设计任务只输出纯 Markdown；禁止 YAML front matter、JSON sidecar 和 CLI 已知字段。
- [x] 审稿顶层只保留 schema、verdict、coverage、findings；finding 删除可由角色推导的 dimension 和非权威 notes。
- [x] 章节语义只完整读取 final 一次；CLI 将 `canonical_delta_v1` 规范化为内部账本并原子物化 graph、memory、foreshadow、TCS、RAG 和 SQLite。
- [x] 删除无调用者的旧审稿/节奏/Humanizer/semantic bundle Agent 输出适配器和成功 normalized 副本路径。
- [x] `insufficient_evidence` 不等于通过，P0/P1 必须 confirmed 且有可回读证据。

## Phase 6. Minimal Automated Acceptance

- [x] 不新增测试文件；v0.4.1 综合验收并入现有 `test_agent_skill_integrity.py` 等测试。
- [x] 其他检查放入现有 task protocol、production、semantic 和领域测试文件。
- [x] 所有 v0.4.1 场景 fixture 集中在一个 YAML 文件。
- [x] 综合测试覆盖旧协议拒绝、中文 Prompt、越权、单章闭环、事务回滚和产物压缩。
- [x] 不增加 multiprocessing、进程池、后台 worker、并发 Agent 或产品内 LLM 子进程。
- [x] 自适应预算与专业 Prompt 改造后的完整 pytest、Skill、资源 manifest、readiness 和 release guards 全部通过。

### Automated Evidence (2026-08-17)

- 自适应预算与混合会话专项：`2 passed`。
- 完整单进程 pytest：最终发布候选回归为 `293 passed in 619.84s`。
- readiness：11/11，通过并报告 `agent_role_registry_v3`、27 roles、12 playbooks、44 unique facet adapters、24 tasks、4 protocols、`single_process_sequential`。
- Skill references 同步与校验通过，resource manifest 已重建且 `--check` 通过，Skill 包校验与 release guards 均通过。
- 当前源码版本统一为 `0.4.1`；允许作为协议与运维稳定版发布，真实文学验收继续作为质量声明门禁。

## Phase 7. Real Chinese Production Acceptance

- [ ] 东方玄幻、都市调查、现代言情、历史武侠、科幻无限流、原创游戏轻小说各完成 5 章。
- [ ] 每组由用户按统一量表验收，平均不低于 7.0，目标/人物/对白/场景不低于 7.5。
- [ ] 全新 SAO 同人项目顺序完成 20 章，canon/OOC 平均不低于 8.0。
- [ ] 全部 50 章 P0/P1、canonical 污染和连续原文复现为零。
- [ ] 第 20 章关闭后 `production next` 指向第 21 章。
- [ ] 未经用户真实评分，不得把本阶段标为完成或伪造文学证据。

## Phase 8. Release

- [ ] 版本、README、安装文档、资源和 Release notes 统一为 0.4.1。
- [ ] wheel/sdist、临时 pipx `[semantic]` 和首章工作单 smoke 通过。
- [x] 工程发布与文学验收解耦；完整自动验证通过后可创建 `release: publish v0.4.1` commit、tag 和 GitHub Release，50 章验收仍决定文学质量声明。
- [ ] 发布后卸载全局 0.3.2，并从 GitHub tag 安装 0.4.1。
- [ ] CLI、module、metadata、doctor、Codex Skill 和 Claude Skill 全部报告 `0.4.1/current`。
- [x] 不改写历史 tag/Release，不新增 pnpm/npm 包。
