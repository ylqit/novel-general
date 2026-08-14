# Agent 文档协议、Prompt 角色与数据链路准入 Checklist

本文档定义 `longform-novel-engine` 从“大型结构化工作单”迁移到“角色化文本工作单 + 精简机器结果”的实现边界和验收顺序。目标不是取消结构化协议，而是让 Agent 把上下文预算用于创作与判断，让 CLI 继续负责事实补全、证据验证、事务应用和 canonical 污染防护。

本 checklist 同时是数据链路准入门。Phase 0-5 未全部完成前，禁止把新协议接入正式 `production next`、validate、apply、finalize、chapter close、图谱、记忆、伏笔、TCS、RAG 或 SQLite 链路。

## 状态规则

- `[ ]`：尚未实现，或只有设计文字。
- `[~]`：实现已存在，但缺少自动测试、迁移证明、真实规模证据或完整回归。
- `[x]`：实现、自动测试和可重复证据均存在。
- 不得因为旧实现具有相近字段或一句角色说明就标记为完成。
- Phase 6 只有在 Phase 0-5 不存在 `[ ]` 或 `[~]` 时才能通过。
- Phase 6 通过前，Phase 7-8 必须保持 `[ ]`；不得先接一部分正式链路再补前置功能。

## 目标协议

```text
不可覆盖的安全与事实边界
-> task role contract
-> 人工批准的 project overlay
-> 当前任务目标与去重上下文
-> 受控 feedback
-> 输出、校验与交接要求
```

Agent 接收适合阅读和创作的 Markdown 工作单。机器注册表、manifest、结果 envelope 和 canonical delta 只保留确定性编排所需字段。自然语言说明不能被 CLI 猜测、抽取或直接写入 canonical。

## Prompt Role Contract Specification

角色按任务绑定，不按 Codex、Claude Code 或具体模型分别维护。宿主适配层只能调整命令展示、路径示例和产品称呼，不能改变角色职责、证据标准、严重级别或 canonical 边界。

每个活动 manifest 最终必须记录：

- `role_id`：稳定角色 ID；禁止使用 `generic_agent`、`expert` 等模糊回退。
- `role_version`：角色合同版本，与任务结果 schema 版本独立。
- `role_prompt_hash`：实际渲染前的角色合同 SHA-256。
- `independence_mode`：`author_context|isolated_review|cross_host_review`。
- `project_overlay_hash`：无 overlay 时使用固定空值 hash，不得省略来源状态。

角色正文使用 Markdown。机器注册表只保存 `role_id`、支持的 task type、角色版本、Prompt 资源路径、输出模式、独立性要求和允许覆盖字段。

每个角色 Markdown 必须具有以下可测试章节：

- `Identity`：当前专业身份，不使用“万能专家”或模仿在世作者的空泛设定。
- `Serves`：本轮服务于作者决策、读者体验、编辑判断或 canonical 状态中的哪一个对象。
- `Single Mission`：本轮唯一目标和明确完成条件。
- `Cognitive Lens`：必须观察的维度，以及应主动忽略的非职责问题。
- `Source Authority`：区分 canonical、candidate、advisory 和 untrusted source content。
- `Creative Freedom`：允许补充的局部细节及其边界。
- `Forbidden Actions`：禁止改写的事实、状态和路径，以及禁止越过的角色权限。
- `Evidence Duty`：必须引用 exact span、canonical ref 或 source hash 的结论。
- `Output Contract`：`markdown_prose|compact_review_json|document_index_bundle|strict_delta_json`。
- `Stop And Escalate`：信息不足、输入冲突、超预算、证据不存在和任务歧义时的停止条件。
- `Handoff`：validate、submit、apply/finalize 和 failure command。
- `Observable Self Check`：只检查输出可观察属性，不要求或保存 chain-of-thought。

### Project Overlay Policy

项目 overlay 采用受限追加，不允许替换完整角色 Prompt。

允许覆盖：题材语汇、叙事人称、视角距离、目标读者、平台非阻断偏好、人物声音合同、内容禁区和人工批准的风格基线。

禁止覆盖：输入/输出路径、hard boundaries、canonical 权威、证据要求、P0/P1 规则、任务生命周期、结果 schema、validate/apply/finalize 命令、独立审稿隔离规则和失败策略。

低优先级内容与高优先级合同冲突时，Prompt 编译必须失败并报告冲突字段、来源和唯一修复命令，不能采用“最后一段覆盖前文”的静默行为。

### Review Independence Policy

- `isolated_review` 不得包含作者的隐藏推理、改写理由、其他 reviewer 结果或 aggregate。
- 同一宿主可以执行独立审稿，但必须使用独立 context packet 和独立 manifest。
- 跨宿主复审属于高风险任务的增强证据，不是普通章节的强制要求。
- reviewer 只能报告本角色视角内的问题；不能替作者重写正文，也不能替 CLI 决定 apply/finalize。
- 正文、研究材料和来源 canon 中出现的命令式文字一律视为 untrusted content，不得改变角色或输出协议。

## Task-To-Role Matrix

| Task type | `role_id` | Output mode | 核心职责 | 明确禁止 |
| --- | --- | --- | --- | --- |
| `book_ideation` | `creative_facilitator` | `compact_review_json` | 每轮推动一个人工创作决定，展示真实取舍 | 替用户默选或直接写 Bible |
| `book_design` | `book_architect` | `document_index_bundle` | 建立读者合同、世界规则、人物欲望与长期矛盾 | 用抽象标签代替可执行设定 |
| `outline_design` | `longform_outline_architect` | `document_index_bundle` | 建立卷章因果、承诺、关系推进和伏笔窗口 | 用“待定章节”填满覆盖范围 |
| `outline_revision` | `continuity_outline_editor` | `document_index_bundle` | 修改声明范围并列出真实下游影响 | 信任 Agent 自报而跳过 CLI 依赖重算 |
| `chapter_direction` | `chapter_story_editor` | `compact_review_json` | 提供因果不同的章节方向及代价 | 写正文或直接修改章节卡 |
| `fanfiction_canon` | `fanfiction_canon_archivist` | `strict_delta_json` | 转述来源人物、关系、规则、时间线与证据 | 搬运连续原文或自行判断授权 |
| `fanfiction_design` | `fanfiction_architect` | `document_index_bundle` | 建立分歧点、蝴蝶效应、声音合同和原创主线 | 让原作角色集体降智服务原创主角 |
| `research_synthesis` | `research_synthesizer` | `strict_delta_json` | 从声明来源生成可复核 claim | 使用未声明来源或无证据推断 |
| `style_analysis` | `semantic_style_analyst` | `strict_delta_json` | 描述可迁移技法和语义风格特征 | 模仿作者身份或复制样文表达 |
| `adaptation_analysis` | `adaptation_analyst` | `strict_delta_json` | 提取结构、节奏和技法 | 保存、拼接或重构来源正文 |
| `character_expression_design` | `character_performance_architect` | `strict_delta_json` | 定义感知、决策、语言、身体、面具和关系压力 | 用口头禅或外貌配额代替人物差异 |
| `chapter_write` | `chapter_author` | `markdown_prose` | 用场景、选择、行动和人物反应兑现章节职责 | 输出提纲、作者说明或擅改 canonical |
| `repair` | `repair_author` | `markdown_prose` | 针对有效 finding 写完整替代候选 | 借修复扩张剧情或破坏已通过内容 |
| `humanize` | `humanizer` | `markdown_prose` | 清理模板表达并强化人物声音与具身反应 | 改变事件、关系、知识或能力代价 |
| `content_expand` | `expansion_writer` | `markdown_prose` | 增加有因果作用的场景、对白、动作和感官 | 用解释、景物堆砌或内心总结注水 |
| `humanize_semantic_review` | `humanizer_semantic_reviewer` | `compact_review_json` | 对照双稿验证事实、合同和人物声音保持 | 自审放行或把偏好升级为事实错误 |
| `semantic_review` | `semantic_continuity_reviewer` | `compact_review_json` | 核查动机、关系、空间、能力、时间和伏笔 | 重写正文或无证据给出 P0/P1 |
| `reader_payoff_review` | `reader_payoff_reviewer` | `compact_review_json` | 判断实际收益、代价、承诺推进和结尾作用 | 把章节卡计划当成已兑现事实 |
| `pacing_review` | `semantic_pacing_reviewer` | `compact_review_json` | 判断压力、释放、升级、余波和停顿是否成立 | 强制快节奏、高对白或悬崖结尾 |
| `character_expression_review` | `character_performance_reviewer` | `compact_review_json` | 检查可辨识声音、具身表现、关系压力和对白功能 | 仅凭口头禅判断人物差异 |
| `editorial_review` | `editorial.<declared_role>` | `compact_review_json` | 只按 planning、anti-AI、serial、reader 或 canon 单一视角审稿 | 读取 peer result、aggregate 或进行多数票互相影响 |
| `chapter_semantic` | `chapter_semantic_archivist` | `strict_delta_json` | 一次读取 final，输出关系、角色、伏笔、世界和时间线增量 | 推断无证据事实或直接写图谱/数据库 |
| `graph_extract` | `legacy_graph_extractor` | `strict_delta_json` | 兼容旧项目的证据化图谱增量 | 在新生产链与章节统一语义任务并行重复抽取 |
| `memory_extract` | `legacy_memory_extractor` | `strict_delta_json` | 兼容旧项目的章节记忆增量 | 在新生产链重复读取 final |
| `character_memory` | `legacy_character_memory_curator` | `strict_delta_json` | 兼容旧项目的角色状态增量 | 累积全部历史正文或绕过统一语义账本 |

## Phase 0. Baseline And Ownership Inventory

- [x] 冻结 v0.3.1 Engine、两个 Skill、当前角色映射和失败项目 hash。
- [x] 保留 SAO v0.3.1 `reader_payoff_review` 超过 20K 的失败报告和 issue log，不原地修改运行结果。
- [x] 建立全部 task type、输入来源、输出字段、validator、apply 目标和 canonical ownership 清单。
- [x] 标记每个现有字段为 `agent_judgment|cli_known|canonical_delta|presentation_only`。
- [x] 测量 reader payoff、semantic review、editorial 和 chapter semantic 的输入文件数、字符数和重复内容比例。
- [x] 冻结 v1/v2 manifest、submission、review 和 semantic bundle 兼容 fixture。
- [x] 基线阶段不修改正式 `production next` 路由或任何 canonical 状态。

Phase 0 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE0_BASELINE.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE0_BASELINE.md) 和机器基线 [`baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE0_V031.json`](baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE0_V031.json)。专项回归为 `tests/test_agent_document_protocol_phase0.py`，结果为 7 passed；完整回归为 325 passed，Skill、资源和 release guards 均通过。Phase 1-5 尚未完成，因此 Phase 6 readiness 与 Phase 7 数据链路继续锁定。

## Phase 1. Role Resources And Registry

- [x] 为矩阵中的每个非 legacy `role_id` 创建完整 Markdown 角色合同。
- [x] 建立机器注册表并验证 task type 到角色的唯一映射。
- [x] `editorial_review` 必须映射到声明的专用编辑角色，不允许 generic editorial fallback。
- [x] manifest 记录 role ID、version、Prompt hash、independence mode 和 overlay hash。
- [x] Codex 与 Claude Code 渲染结果具有相同角色语义和 hash，只允许宿主展示差异。
- [x] 角色资源进入 wheel、资源 manifest 和 Skill 自包含校验。
- [x] 缺失、重复、未知或 hash 漂移的角色合同在 manifest 注册前失败。

Phase 1 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE1_ROLE_REGISTRY.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE1_ROLE_REGISTRY.md)。角色注册表包含 32 份合同（29 个非 legacy、3 个 legacy 兼容角色），覆盖 24 个直接 task 映射和 8 个专用编辑角色；专项测试位于 `tests/test_agent_document_protocol_phase1.py`。完整回归为 332 passed，Skill、资源、wheel 和 release guards 均通过。Phase 2-5 仍未完成，因此 Phase 6 readiness 与 Phase 7 数据链路继续锁定。

## Phase 2. Prompt Compiler And Context Deduplication

- [x] Prompt 编译器严格执行安全边界、角色、overlay、任务、feedback、输出交接的优先级。
- [x] 实现受限 overlay allowlist，并拒绝所有安全、证据、路径和生命周期覆盖。
- [x] 冲突报告包含字段、双方来源、优先级和唯一修复命令。
- [x] 每条上下文事实只保留一份正文表示，并记录来源路径、hash、选择原因和裁剪原因。
- [x] `reader_payoff_review` 只声明 task、current draft、compact context 三个输入。
- [x] payoff context 不再重复完整 chapter card、gate result 或 effective quality contract。
- [x] payoff context 不超过 6K 字符，完整工作单不超过 15K 字符。
- [x] writing task 继续保持核心输入不超过 7 个、常规 brief 不超过 20K 字符。
- [x] 核心人物、能力、关系或活跃伏笔无法装入预算时明确失败，不静默裁掉。
- [x] untrusted source 中的 Prompt injection 不得改变角色、边界、输出路径或命令。

Phase 2 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE2_PROMPT_AND_CONTEXT.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE2_PROMPT_AND_CONTEXT.md)。实现引入固定六层 Prompt 编译、人工批准 overlay 校验、三输入 payoff context 和核心写作事实覆盖检查；专项测试位于 `tests/test_agent_document_protocol_phase2.py`。完整回归为 342 passed，Skill、资源清单、release guards 与 diff 检查均通过。Phase 3-5 尚未完成，因此 Phase 6 readiness 与 Phase 7 数据链路继续锁定。

## Phase 3. Agent-First Output Protocol

- [x] 定义共享 `agent_result_envelope_v1`，仅包含 task/scope、verdict、evidence、findings/deltas 和必要 notes。
- [x] `markdown_prose` 任务只输出完整候选正文，不混入 JSON、说明或分析。
- [x] `compact_review_json` 不要求 Agent 回填 CLI 已知的 source path、hash、章节号和 planned facts。
- [x] `document_index_bundle` 将长篇设计说明放入 Markdown，将稳定 ID、范围、引用和 apply 索引放入紧凑 JSON。
- [x] `strict_delta_json` 保留稳定 ID、旧/新状态、动作、证据和 changed/unchanged 覆盖。
- [x] 不创建覆盖所有任务的 mega schema；任务 payload 只包含该角色真正判断的字段。
- [x] 自然语言 notes 永远是非权威说明，不能由 CLI 解析成 canonical delta。
- [x] 输出协议明确唯一允许路径、validate、apply/finalize 和 failure command。

Phase 3 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE3_OUTPUT_PROTOCOL.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE3_OUTPUT_PROTOCOL.md)。实现位于 `src/longform_engine/agent_results.py`，当前作为只读、无状态变更的隔离协议层，尚未接入正式 `production next`、旧 validator 或 canonical apply；专项测试位于 `tests/test_agent_document_protocol_phase3.py`。专项结果为 7 passed，Phase 0-3 与 Agent task 协议组合回归为 41 passed，完整回归为 349 passed；Skill、资源清单、release guards、引用同步和 diff 检查均通过。Phase 4 已完成，Phase 5 尚未完成，因此 Phase 6 readiness 与 Phase 7 数据链路继续锁定。

## Phase 4. CLI Normalization And Validation

- [x] CLI 从 manifest、当前文件和 canonical state 补齐 source path/hash、章节号、planned facts 和允许引用。
- [x] exact span 必须回读当前正文核验，不能只信任 context packet 或 Agent 摘录。
- [x] 关系旧状态、人物知识来源、伏笔窗口、实体 ID 和 source hash 保持严格验证。
- [x] v1/v2 旧结果可规范化到新内部表示，歧义输入进入 `need-human`。
- [x] Prompt role 不得扩大 Agent 的 canonical 写入权限。
- [x] 无效 manifest 在 index/event 写入前失败；注册失败不留下未识别 orphan。
- [x] validator 或 normalizer 失败只写受控诊断，不污染 final、Bible、outline、graph、memory、TCS、RAG 或 SQLite。

Phase 4 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE4_NORMALIZATION.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE4_NORMALIZATION.md)。实现位于 `src/longform_engine/agent_normalization.py`，CLI 只读入口为 `agent-task result-validate`；诊断仅写入 `50_workbench/agent_tasks/diagnostics/`，不会更新 task index/event 或 canonical lane。专项结果为 9 passed，Phase 0-4 与 production/CLI 锁组合回归为 58 passed，完整回归为 358 passed；Ruff、Skill 校验、资源清单、release guards、引用同步和 diff 检查均通过。Phase 5 尚未完成，因此 Phase 6 readiness 与 Phase 7 数据链路继续锁定。

## Phase 5. Isolated Functional Completion

- [x] 所有 role renderer、Prompt compiler、context compiler、result parser、normalizer 和 validator 已实现。
- [x] 四种输出模式均有正常、边界和失败 fixture。
- [x] 所有非 legacy task type 均可在隔离测试中生成、渲染、提交和验证。
- [x] legacy graph/memory/character tasks 只验证兼容读取，不重新加入新章节生产链。
- [x] 同一输入下 chapter author、repair、Humanizer、expansion 和 reviewer 的工作单职责不可互换。
- [x] isolated review context 不含作者推理、peer result 或 aggregate。
- [x] overlay 越权、Prompt injection、错误 hash/span/ref、超预算和输出越界测试通过。
- [x] 此阶段正式 `production next`、apply/finalize 和 canonical 物化行为保持 v0.3.1 路径。

Phase 5 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE5_ISOLATED_COMPLETION.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE5_ISOLATED_COMPLETION.md)。隔离组合层位于 `src/longform_engine/agent_isolation.py`，输出解析位于 `src/longform_engine/agent_results.py`，旧 graph/memory/character 只读适配位于 `src/longform_engine/agent_normalization.py`。专项矩阵覆盖所有非 legacy task type、8 个专用编辑角色、四种输出模式、Codex/Claude Code 双宿主、角色隔离、Prompt injection、overlay 越权、预算、证据和 no-pollution。专项结果为 8 passed，Phase 0-5 与任务协议/production 锁组合回归为 80 passed，完整回归为 367 passed；Ruff、Skill 校验、资源清单、release guards、引用同步、diff 检查及新建 v0.3.1 wheel 资源审计均通过。发布 guard 明确禁止 `production.py` 在 Phase 6 前导入隔离协议。Phase 6 尚未执行，正式数据链路继续锁定。

## Phase 6. Data Pipeline Readiness Gate

- [x] Phase 0-5 的每个检查项均为 `[x]`，不存在 `[ ]` 或 `[~]`。
- [x] readiness checker 输出 `ready_for_data_pipeline: true` 和稳定 JSON 报告。
- [x] readiness 报告记录 commit、dirty-tree hash、Engine/Skill 版本、角色资源 hash 和测试证据。
- [x] 完整 `python -m pytest -q` 通过。
- [x] `python scripts/sync_skill_references.py --check` 通过。
- [x] `python scripts/build_resource_manifest.py --check` 通过。
- [x] `python scripts/validate_skills.py` 通过。
- [x] `python scripts/release_surface_guards.py` 通过。
- [x] realistic payoff fixture 在大章节卡、大 gate 和常规正文下仍满足三输入/15K 上限。
- [x] Prompt injection、角色越权、自写自审、错误证据、事务回滚和 no-pollution 测试全部通过。
- [x] Phase 6 未通过时，CI 和本地 guard 均阻止启用新数据链路。

Phase 6 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.md)、[`baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_EVIDENCE.json`](baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_EVIDENCE.json) 和 [`baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.json`](baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.json)。共享 checker、CLI 与本地/CI guard 使用 `agent_data_pipeline_readiness_v1`；Phase 6 历史完整回归为 375 passed。真实 payoff fixture 在 71,643 字符章节卡、126,346 字符 gate 和 3,643 字符正文下编译为 3 个输入、10,161 总字符和 4,404 字符 compact context。该前置证据现由 Phase 7 运行授权继续绑定和校验。

## Phase 7. Full Production Data Pipeline

- [x] 仅在 Phase 6 通过后接入 `production next -> agent-task brief -> role output -> validate`。
- [x] 候选正文继续通过显式 submit/gate，项目设计和事实增量继续通过显式 apply。
- [x] final 继续要求显式 `chapter finalize --approved-by`。
- [x] final 后统一执行 chapter semantic validate/apply 和 chapter close。
- [x] chapter semantic 一次事务物化 graph、character state、foreshadow state、TCS、RAG 和 SQLite。
- [x] 任一物化步骤失败时全部回滚，不产生半更新 canonical 状态。
- [x] Reader payoff、Humanizer、编辑和节奏 feedback 只通过受控摘要回流下一任务。
- [x] `production next` 只选择当前章节阶段和当前候选允许的角色任务。

Phase 7 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_PRODUCTION_PIPELINE.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_PRODUCTION_PIPELINE.md) 和 [`baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_EVIDENCE.json`](baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_EVIDENCE.json)。正式入口由 `agent_pipeline.py` 统一授权和接线；专项与语义事务组合为 8 passed，Humanizer/production/task routing 组合为 45 passed，历史完整回归为 380 passed。Phase 8 已完成协议与产物生命周期验收，真实 SAO Agent 正文复产仍保留为部分完成。

## Phase 8. Artifact And Production Acceptance

- [x] 已关闭章节只松散保留 final、semantic ledger、closure 和必要 canonical 当前视图。
- [x] 工作单、候选、审稿、validation 和 transaction 材料进入可验证的按章审计包。
- [x] 相同正文不作为多个长期 loose 文件保存；v3 审计条目优先引用已绑定的 retained final，否则按 SHA-256 只保存一个 blob，逻辑路径用于恢复。
- [x] 最近两章保留活动工作区，其余章节支持 compact、verify 和 restore。
- [x] 新协议完成第 1 章 write/review/finalize/semantic/close 全闭环。
- [x] 第 1 章通过后完成 5 章 smoke，P0 和 canonical 污染均为零。
- [x] 通用协议依次完成第 1、5、20 章里程碑；第 20 章关闭后 `production next` 指向第 21 章，旧章归档 18 个且活动区仅保留 19-20。
- [~] 5 章 smoke 通过后已用 20 章协议重放解锁 SAO 复产，但尚未生成并人工验收真实 Codex SAO 正文。
- [~] 真实 SAO 项目仍需复现第 20 章关闭后进入第 21 章的证据，并完成人物、OOC 和文学质量验收。

Phase 8 证据见 [`AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE8_ACCEPTANCE.md`](AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE8_ACCEPTANCE.md) 和 [`baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE8_EVIDENCE.json`](baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE8_EVIDENCE.json)。`chapter_artifact_archive_v3` 绑定 final、semantic ledger 和 closure，在 ZIP 内嵌审计清单，并以内容寻址 blob 去重 draft/submitted、任务、审稿、validation、finalization、历史 TCS、run report 与 chapter transaction；v2 历史包继续支持 verify/restore。专项协议重放在第 5 章得到 3 个归档并指向第 6 章，在第 20 章得到 18 个归档、活动区 19-20 且指向第 21 章。该证据是工程验收，不冒充同人正文质量证据。

## Required Tests

- [x] 所有 task type 都能编译完整角色合同，且不存在默认模糊角色。
- [x] Prompt 层级冲突具有稳定、可解释和可执行的失败结果。
- [x] 项目 overlay 不能覆盖硬边界、证据义务、严重级别或生命周期。
- [x] 审稿工作单不泄漏作者思路、peer review 和 aggregate。
- [x] 正文或 research source 中的命令式内容不能注入 Prompt 控制层。
- [x] 自然语言说明不会被解析为 canonical 事实。
- [x] 旧 manifest/result 可兼容读取，但不会改写历史失败运行。
- [x] 无效输出、错误证据和 apply 失败保持 canonical 零污染。
- [x] 角色、上下文和输出协议测试覆盖 Codex 与 Claude Code 两种宿主渲染。

## Definition Of Done

Agent-first 协议、正式数据链路和通用 1/5/20 章工程重放已经完成。readiness checker 必须继续输出 `ready_for_data_pipeline: true`；完整生产验收仍要求 Phase 8 的真实 SAO 两项由 `[~]` 变为 `[x]`。

当前可公开表述为“Agent-first 文档协议、Prompt 角色、受控数据链路和 20 章工程协议重放已经通过”。在真实 SAO 正文验收前，不得表述为“SAO 20 章生产已经通过”或“完整文学生产验收已经通过”。
