# longform-novel-engine 超越 novel-skill 验收 Checklist

> v0.4.0 字数主导、滚动纲要与混合题材能力的独立验收见 [`V0_4_0_WORD_BUDGET_AND_COMPOSABLE_PROFILE_CHECKLIST.md`](V0_4_0_WORD_BUDGET_AND_COMPOSABLE_PROFILE_CHECKLIST.md)。两组真实混合题材五章盲评与 200 万规模证据完成前，不能据此升级文学质量领先声明。

v0.4.3 RC 的协议硬化与产物精简状态见 [`V0_4_3_PROTOCOL_HOTFIX_CHECKLIST.md`](V0_4_3_PROTOCOL_HOTFIX_CHECKLIST.md)。其中 `literary_evidence_ready=false` 时不得把生产链稳定性表述为文学效果领先。

本文档是“功能、效率与生成质量全面超越 `novel-skill`”的可执行验收标准。状态含义：`[x]` 已有代码和自动测试证据，`[~]` 已具备工具但仍需真实运行证据，`[ ]` 尚未完成。工程能力完成不等于文学质量已经领先。

起点优先的平台合同与番茄非阻断兼容视图见 [`PLATFORM_WRITING_ADAPTATION_CHECKLIST.md`](PLATFORM_WRITING_ADAPTATION_CHECKLIST.md)。平台适配接口通过测试不能替代真实题材盲评，也不能作为平台推荐效果证明。

中文网文、Humanizer v3 与同人创作的细分验收见 [`CHINESE_WEBNOVEL_AND_FANFICTION_QUALITY_CHECKLIST.md`](CHINESE_WEBNOVEL_AND_FANFICTION_QUALITY_CHECKLIST.md)。其中真实 5/10 章证据未完成前，同样不能升级公开文学质量声明。

人物声音、身体在场、场景欲望和对白可交换性的专项准入见 [`CHARACTER_EXPRESSION_AND_SCENE_NARRATIVE_CHECKLIST.md`](CHARACTER_EXPRESSION_AND_SCENE_NARRATIVE_CHECKLIST.md)；现有 15 章 audit 与独立盲评完成前，不得据此宣称人物质量已经超过 `novel-skill`。

Humanizer v4、正文事实差分、读者收益验真、平台质量合同、创意交互、编辑独立性、feedback 生命周期和 RAG 规模优化的实施架构见 [`CHINESE_WEBNOVEL_QUALITY_OPTIMIZATION_ARCHITECTURE.md`](CHINESE_WEBNOVEL_QUALITY_OPTIMIZATION_ARCHITECTURE.md)。该架构是后续实施依据，不等于相关项目已经完成。

## 1. Project Intelligence Readiness

- [x] 空项目的 `production next` 首先返回 `open-book`，不会直接生成第一章。
- [x] 开书后先逐轮编排 `book_ideation -> human apply`，完整后才进入 `book_design -> human apply -> outline_design -> human apply`。
- [x] `book_ideation` 每轮只保存一个用户明确选择，八类核心创意决定未齐全时阻止后续设计。
- [x] 未出现有效 applied marker 时，不把骨架文件误判为已完成项目设计。
- [x] 第一章前必须具备 creative brief、稳定角色 ID、人物弧线、关系引用、卷纲、连续章节计划和伏笔账本。
- [x] `continue-write` 在项目智能状态不完整时返回唯一可执行 next command。
- [x] book/outline apply 均要求显式人工确认，不能由 `production loop --no-apply` 自动通过。

## 2. Strict Candidate Validation

- [x] `book_design_candidate_v1` 校验核心卖点、世界规则、人物欲望、长期矛盾、卷级升级和结局边界六类创意决策。
- [x] 角色必须包含稳定 ID、目标、缺陷、阶段弧线，并使用有效角色 ID 表达关系。
- [x] `outline_design_candidate_v1` 必须覆盖配置中的全部卷和目标章节，章号连续且卷范围一致。
- [x] 伏笔 plant/payoff 窗口必须有效且位于配置章节范围内。
- [x] `outline_revision` 的影响范围由 CLI 根据 canonical 依赖重新计算，不完全信任 Agent 自报。
- [x] research claim 必须携带来源 hash、字符 span 和与原文一致的 evidence。
- [x] adaptation analysis 使用 n-gram、跨字段拼接和来源片段去重检查阻止变相复制。
- [x] invalid candidate、越界路径和 apply 失败不污染 Bible、outline、research canon 或其他 canonical 状态。

## 3. Context Compiler

- [x] AgentTaskManifest 声明 must-read、on-demand、forbidden 三层上下文策略。
- [x] writing、repair、Humanizer、editorial 等任务拥有独立字符和文件预算。
- [x] 工作单不再重复内嵌完整 task JSON、task Markdown、feedback 和源文件正文。
- [x] writing manifest 的核心输入不超过 7 个文件。
- [x] 常规首章写作者工作单硬限制为不超过 20K 字符。
- [x] selection report 记录选择理由、截断、按需输入和被排除的重复内容。
- [x] Agent 工作单明确禁止主动扫描全项目及读取未声明 draft/research inbox。
- [x] 当前 `book_ideation` 协议下 Codex 连续 5 章已确认平均上下文为 `7 files / 12,608 chars`，最大工作单为 `14,437 chars`，均低于硬预算。
- [x] 同一当前协议项目已继续完成第 6-15 章；15 章平均上下文为 `7 files / 14,405.667 chars`，最大工作单为 `16,087 chars`，仍低于硬预算。
- [~] Claude Code 与跨宿主主观理解负担仍待验证。

## 4. Semantic RAG At Scale

- [x] 索引阶段预计算并持久化 embedding，内容 hash 未变化时复用已有向量。
- [x] 查询阶段从 vector store 获取候选，不再对 SQLite 中全部章节逐条重新 embedding。
- [x] 候选融合 vector、关键词、实体、时间、伏笔和最近章节信号，再对小候选集 rerank。
- [x] 向量索引不可用时仅允许显式标记的 lexical fallback，不伪装成 semantic retrieval。
- [x] Milvus/pgvector/Elasticsearch 未实现真实 query/upsert 时标记为 experimental 且 health check 不通过。
- [x] 本地 `hnswlib` backend 使用 SQLite metadata/hash/stable label、HNSW index、manifest 和 dirty-state 一致性协议。
- [x] content-hash 增量 upsert、消失记录 stale、revision rollback stale 和后续 restore 均有自动测试。
- [x] 50 章固定工程数据集完成 recall、事实错误率、P95 延迟和增量索引成本测量。
- [x] 200 章固定工程数据集完成同口径测量并记录增长曲线。
- [x] 500 章/10,000 向量固定工程数据集达到 recall@10 = 1.0、事实错误率 = 0、SQLite P95 约 962ms、HNSW P95 约 105ms。
- [~] 上述证据等级为 `synthetic_engineering`；真实中文章节、正式模型、冲突事实、别名、时间和伏笔检索仍未完成，因此不能作为生成质量胜出证据。

## 5. Dual-Layer Quality Gate

- [x] 原 `semantic gate` 明确命名为 deterministic evidence gate，保留兼容入口但不冒充 LLM 语义推理。
- [x] 高风险章节会生成独立 `semantic_review` Agent 任务并阻断 finalize。
- [x] Agent 必须检查动机、空间、能力、关系、伏笔泄露和因果断裂六类问题。
- [x] 每条 finding 必须引用当前正文精确字符 span 和声明的 canonical reference。
- [x] CLI 校验 source hash、span 内容、canonical 路径和实体 ID 后才允许 apply。
- [x] semantic review apply 只写 gate artifact 并重新计算门禁，不直接写 final/RAG/graph/TCS/SQLite。
- [x] repair、Humanizer、editorial 和 pacing 的受控反馈可压缩回流到下一章任务。
- [x] 已排除或重复反馈不会以原始报告形式反复塞入写作者上下文。
- [x] Humanizer 风险改写会创建独立 `humanize_semantic_review`，而不是让润色写作者自审。
- [x] Humanizer 语义结果必须通过双稿 hash/span、声明引用、实体 ID、七类事实、章节合同和声音校验。
- [x] `draft submit` 会拒绝缺少语义复核或复核后再次修改的受管 Humanizer 候选。
- [x] gate 通过后可编排独立 `reader_payoff_review`，以当前 draft hash/span 验证实际收益、代价和承诺进度，而不是把章节卡计划当成兑现事实。
- [x] `reader_reward_entry_v2` 和 structure history 只在显式 finalize 事务中写入；invalid/stale review 与事务失败不会污染 canonical 状态。
- [x] 结构观察仅在结构、语言和收益位置组合重复时升级 P1，单一重复只警告且不强制统一章节模板。
- [~] Humanizer v4 的人物声音保真和 AI 味改善仍缺同条件 5/10 章盲评证据。

## 6. Safe Production Efficiency

- [x] `production next` 对 open-book、项目智能任务、章节任务、语义审查和人工边界给出唯一 next command。
- [x] `production loop --no-apply` 只推进无 canonical 写入的确定性 task/validate/status 步骤。
- [x] 遇到 Agent output、human apply、graph/memory apply 或 finalize 时立即暂停。
- [x] 批量准备不会自动确认开书、改纲、研究 canon 或章节定稿。
- [x] action 输出包含 task type、input files、context policy、allowed outputs、schema、validate/apply/failure command。
- [x] 当前协议 Codex 原创已由 5 章继续扩展到 15 章，逐章记录 work order/manifest/reviewed draft/gate SHA-256、显式 final 和 post-final apply；15 章报告 `complete=true`、`acceptance_passed=true`。
- [~] 15 章受控生产尚缺 `novel-skill` 同条件效率对比，不能由单边运行推导相对优势。证据见 `docs/benchmarks/PHASE6_EXECUTION_STATUS.md`。

## 6A. Platform Quality Contract And Creative Choice

- [x] 平台、题材和故事阶段画像是 wheel 内置可哈希资源，而非散落在 prompt 中的硬编码文案。
- [x] 章节质量合同合并 market、genre、phase、人工批准风格基线和项目覆盖，并进入章节卡/写作者 brief。
- [x] 人工风格基线只通过显式 CLI 从已定稿的 prose-free 结构观察扩充。
- [x] 条件式 `chapter_direction` 在高分歧/高风险章节提供人工方向选择，普通稳定章节不增加每章交互。
- [x] 无效创意/方向 JSON 与 apply 失败不会污染 Bible、outline、final、RAG、graph、TCS 或 SQLite。
- [~] 平台合同和创意交互相对 `novel-skill` 的正文收益与额外操作成本仍需同模型 10 章对照。

## 6B. Independent Editorial And Feedback Governance

- [x] `editorial_role_review_v2` 记录角色实例、Agent 产品/版本、上下文 digest、独立模式、轮次和置信度，并保留 v1 低证据等级兼容。
- [x] v2 P0/P1 必须匹配当前章节精确证据；无证据严重度标签不能冒充受保护的少数派意见。
- [x] 编辑角色由章节风险选择，不再用每章全员审稿制造虚假多 Agent 完整度。
- [x] 每个角色只能读取独立 manifest 输入，peer result 和 aggregate 在提交前均被排除。
- [x] aggregate 固化 consensus、conflict、证据重合、严重度差异、少数派 P0/P1 和 human decision。
- [x] 少数派 P0/P1 不会被多数投票覆盖；缺角色、重复角色、无效结果和证据冲突均保持可见。
- [x] feedback registry 拥有 stable ID、任务相关筛选、最多五条回流、P2 TTL、P1 无复发解决、resolve/suppress 和 rollback。
- [x] 相同语义问题复发会增加 recurrence；只换证据表述时记录 `gate_gaming_risk`。
- [x] feedback 始终位于 workbench，registry 失败不影响 final、RAG、graph、TCS 或 SQLite。
- [~] 编辑独立性和 feedback 衰减相对 `novel-skill` 的误报率、返修次数与正文收益仍需同模型 10 章对照。

## 7. Reproducible Quality Benchmark

- [x] benchmark v2 记录 1-10 分的连贯性、角色一致性、伏笔控制、节奏、读者收益和 AI 味。
- [x] 每章记录 gate、repair、need-human、P0 矛盾、canonical 污染、上下文文件数和字符数。
- [x] 记录匿名独立评审 ID、Agent 产品、模型版本、宿主版本、scenario ID 和章节数。
- [x] comparison 只接受相同 scenario 和完整章节记录；incomplete 输出只能标记 provisional。
- [x] `benchmark rag-scale-run` 由引擎实测并固化 500 章 recall、错误事实率、P95、初始与增量索引成本；旧 `rag-record` 仅保留兼容记录用途。
- [x] run metadata 固化 scenario 文件 SHA-256、host product、模型/宿主版本和 workflow version，同宿主 baseline 才能参与 claim。
- [x] `technical-record` 将工程记录与文学评分分离；正式分数只能由 `blind-aggregate` 写入。
- [x] `source-attach` 与随机 blind pack 固化逐章来源 hash，公开包和私有 run 映射分离。
- [x] 三份以上人工独立 submission 必须使用不同 reviewer instance/session，并声明未看映射、未参与创作、无利益冲突；Agent 自动评分不能进入正式 aggregate。
- [x] `rag-production-run` 要求 500 个真实 final、50 条来源验证查询、七类风险、正式 embedding/reranker 和无 fallback。
- [x] 自动 claim gate 实现 +0.5 综合分、至少 7 章胜出、单项落后不超过 0.3 等门槛。
- [x] benchmark 记录拒绝正文和长文本，不进入 canonical 状态。

## 8. Real Literary Evidence

- [x] Codex + longform 已按当前 mandatory `book_ideation` 协议完成《照骨司夜录》5 章 smoke：五章显式 final、零 P0、零残留 canonical 污染、`acceptance_passed=true`，且 `production next` 正确指向第 6 章。
- [x] 同一 Codex + longform 项目已新增第 6-15 章并完成 15 章受控生产：15 个显式 final、零 P0、零 canonical 污染、零最终 `need-human`，15 章 benchmark 验收通过，`production next` 正确指向第 16 章。
- [~] 该 15 章运行是 Codex 同宿主自审的生产链证据，文学分数保持为空；尚不能替代匿名盲评或 `novel-skill` 对照。
- [~] Claude Code + longform 完成同一设定 5 章 smoke。
- [~] Codex + longform 与 Codex + novel-skill 完成同模型 10 章盲评。
- [~] Claude Code + longform 与 Claude + novel-skill 完成同模型 10 章盲评。
- [~] 每组每章至少有三名不知道引擎来源的独立评审。
- [~] longform 综合分至少领先 0.5/10，且不少于 7 章胜出。
- [~] 任一核心文学维度落后不超过 0.3。
- [~] P0 连贯性、人物或事实矛盾为零，canonical 污染为零。
- [~] repair 和 need-human 次数不高于对应 novel-skill baseline。
- [~] Phase 6 固定场景、运行命令和真实缺口已落盘；当前状态仍为 `not eligible`，详见 `docs/benchmarks/PHASE6_EXECUTION_STATUS.md`。

## 9. Public Claim Governance

- [x] README 明确当前不宣称文学质量已经优于 novel-skill。
- [x] README 将 `claim_eligible: true` 设为升级质量表述的必要条件。
- [x] 5 章 smoke、单元测试和接口覆盖不能被描述为文学胜出证据。
- [x] `docs/QUALITY_BENCHMARK_RUNBOOK.md` 固化两条对照线、盲评规则和复现字段。
- [x] `docs/PHASE6_QUALITY_PROOF_RUNBOOK.md` 固化 technical record、来源证明、盲包、独立提交、聚合、production RAG 与 claim 命令。
- [ ] 两组正式 comparison 和匿名评审材料完成复核后，才允许修改公开质量声明。

## 10. Regression And No-Pollution

- [x] 项目就绪、严格校验、上下文预算、向量候选、双层门禁和 claim gate 均有自动测试。
- [x] release guard 继续禁止脚本内 LLM/provider 和默认 API key 要求。
- [x] invalid Agent output、feedback digest、semantic review 和 benchmark 记录不污染 canonical state。
- [x] Windows 使用 `sys.executable`/当前 Python 入口，不引入 novel-skill 的硬编码 `python3` 回归。
- [x] 完整 pytest、Skill 自包含校验、资源 manifest 和 release surface guard 全部通过。

## 11. Chinese Web-Novel And Fanfiction

- [x] 同人是正式一等模式，允许角色、关系、世界、能力、时间线、续写、前传、AU、分歧与 crossover。
- [x] 权利状态与商业意图只提示和记录，不阻断生产或导出。
- [x] canon/design 走 Agent task、strict validate、human apply 与事务边界。
- [x] 专名与术语可用，连续原文和跨字段重构仍被检测。
- [x] Humanizer v3、动态章节职责、读者收益账本和平台画像已落地。
- [x] 同人语义审稿与编辑角色覆盖 canon fidelity、OOC、分歧因果、原角色主体性和原创贡献。
- [~] 同人 5/10 章真实盲评与连续生产成本证据仍待执行。

## Definition Of Superiority

章节关系、伏笔、角色记忆和产物精简的后续验收见 [`SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION_CHECKLIST.md`](SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION_CHECKLIST.md)。现有十五章未完成真实 Agent backfill 前，不得把统一语义协议的单元测试等同于既有运行数据迁移完成。

只有第 1-7、9-10 节全部完成，并且第 8 节真实证据全部通过，才可称为“功能、效率与生成质量全面超越 `novel-skill`”。在此之前，准确表述是：longform 的任务隔离、显式状态变更、上下文契约和可复现质量门槛已经更强，文学效果仍在验证。
