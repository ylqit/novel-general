# 中文网文质量、去 AI 味与同人创作支持 Checklist

本文档是 `longform-novel-engine` 对中文网络小说质量、Humanizer 和一等同人工作流的验收标准。`[x]` 表示已有代码与自动测试，`[~]` 表示工具链已具备但仍缺真实章节证据，`[ ]` 表示尚未完成。

通用人物表现与场景化叙事的实现和真实生产证据状态见 [`CHARACTER_EXPRESSION_AND_SCENE_NARRATIVE_CHECKLIST.md`](CHARACTER_EXPRESSION_AND_SCENE_NARRATIVE_CHECKLIST.md)。

章节关系、伏笔、角色记忆、语义摘要与产物精简的统一架构和迁移状态见 [`SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION_CHECKLIST.md`](SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION_CHECKLIST.md)。该清单中十五章回填未完成前，不得把旧项目描述为已经完成统一语义迁移。

下一阶段 Humanizer v4、读者收益验真、平台质量合同、反公式化、编辑独立性、feedback 生命周期与 RAG 规模优化的设计源见 [`CHINESE_WEBNOVEL_QUALITY_OPTIMIZATION_ARCHITECTURE.md`](CHINESE_WEBNOVEL_QUALITY_OPTIMIZATION_ARCHITECTURE.md)。该文档中的设计项在实现和测试完成前不得标记为本 checklist 的已有能力。

产品政策：角色、关系、世界观、力量体系、时间线、续写、前传、AU、原作分歧和 crossover 都允许进入正式创作。权利状态和商业意图只提示、记录和进入 provenance，不阻断任务、校验、定稿或导出。允许同人不等于允许连续来源正文搬运。

## 1. Creation Modes

- [x] `creation.mode` 支持 `original|fanfiction|adaptation_study|inspired_original`。
- [x] `fanfiction.continuity_mode` 支持 `canon_compliant|canon_divergent|alternate_universe|continuation|prequel|crossover`。
- [x] fanfiction 至少声明一个来源，crossover 至少声明两个来源。
- [x] 每个来源包含稳定 `source_id`、作品、作者、canon 截止点、允许元素、权利状态、商业意图和平台政策链接。
- [x] `unverified` 与 `commercial_intent: true` 不构成配置或生产阻断条件。
- [x] `adaptation_study` 继续只保存抽象结构技法，不承担同人创作。

## 2. Fanfiction Agent Workflow

- [x] `production next` 在同人项目中编排 `fanfiction_canon -> human apply -> fanfiction_design -> human apply -> outline_design`。
- [x] CLI 提供 `fanfiction canon-task|canon-validate|canon-apply`。
- [x] CLI 提供 `fanfiction design-task|design-validate|design-apply|status`。
- [x] canon 与 design 使用 `AgentTaskManifest v2`，明确 inputs、allowed outputs、schema、validate、apply、failure command 和 canonical targets。
- [x] Agent 只读取 manifest 声明来源与 canon digest，不主动扫描整部原作、其他小说项目或 research inbox。
- [x] canon/design apply 必须 `--approved-by human`，`production loop --no-apply` 不越过人工边界。

## 3. Canon Evidence And Source Prose

- [x] `fanfiction_source_canon_v1` 覆盖角色、关系、世界规则、能力、时间线、术语、canon 事件和未解问题。
- [x] 所有实体 ID 使用 `source_id:` 命名空间；关系只能引用同来源角色。
- [x] evidence 校验当前文件 hash、manifest 路径和字符 span。
- [x] canonical canon 只保存转述事实、hash 与 span，不保存 evidence 原文。
- [x] 角色名、招式名、世界术语从相似度警报中排除。
- [x] 单字段连续原文、跨字段拆分重构和章节级连续复现均会触发修复。
- [x] 相似度阈值只作为工程警报，不表述为法律安全字数线。

## 4. Fanfiction Design And Fidelity

- [x] `fanfiction_design_candidate_v1` 包含 canon 截止点、分歧点、OOC 容忍度、声音合同、原创主线、世界变化、蝴蝶效应和结局边界。
- [x] crossover 规则必须覆盖所有来源，并声明冲突规则、力量换算与术语碰撞处理。
- [x] 章节卡包含 `canon_refs`、`divergence_effects`、`voice_refs`、`original_contribution` 和 `protected_reveals`。
- [x] fanfiction 默认进入 Agent semantic review。
- [x] `canon_fidelity_reviewer` 检查动机、声音、关系阶段、能力、地点、时代知识、原角色主体性和原创贡献。
- [x] AU/原作分歧不因“不同于原作”直接判错，只检查声明分歧与后续因果是否支撑变化。
- [x] 审稿工作单覆盖只套角色皮、原角色集体降智、原角色只为原创角色服务、设定留名但规则失效。

## 5. Humanizer v3

- [x] 空文本直接失败。
- [x] 重复模式按实际出现次数统计，不再只统计命中的模式种类。
- [x] 中文题材别名正确映射到风格画像。
- [x] 检查信息轰炸、流水账升级、纸片人、工具人、对白同质、伪细节、情绪标签、意义膨胀、强制钩子和固定章节模板。
- [x] Humanizer 候选比较数字事实、角色保留和字符改写比例。
- [x] 超过警告阈值给出 warning，超过人工阈值或改变事实时 `need-human=true`。
- [x] Humanizer 输出仍是候选稿，必须重新 submit/gate，不能直接进入 final。

## 5A. Humanizer v4 Semantic Safety

- [x] `humanize_semantic_review` 使用统一 `AgentTaskManifest v2`，声明双稿输入、唯一 JSON 输出、schema、validate、submit apply、失败命令和 6 文件/28K 字符预算。
- [x] Humanizer 写作者与语义保真审稿者角色分离；审稿者必须比较来源稿和候选稿。
- [x] CLI 严格校验 source/candidate 路径与 SHA-256、双侧字符 span、manifest 声明的 canonical refs 和已知 entity IDs。
- [x] `fact_preservation` 必须且只能覆盖人物行为、事件结果、因果、时间、关系、能力代价和禁揭露七个维度。
- [x] 章节职责、读者收益、代价、禁揭露与人物声音进入结构化保真检查。
- [x] `verdict=pass` 不能覆盖 changed/uncertain 事实、失败章节合同、声音漂移或 P0/P1 finding。
- [x] `balanced` 按改写比例和风险触发，`strict` 每次触发；里程碑、卷边界、同人和声明高风险章节强制触发。
- [x] `production next` 和 `production loop --no-apply` 能发现并校验任务，但停在显式 `draft submit` 边界。
- [x] `draft submit` 在写入 draft 前拒绝缺失、失败或 hash 过期的 Humanizer 检查与语义审稿结果。
- [x] 错误路径/hash/span/ref/entity、事实改变和复核后修改候选只污染 workbench validation，不污染 draft/final/RAG/graph/TCS/SQLite。
- [~] Humanizer v4 是否显著降低中文网文 AI 味且不损伤人物声音，仍需原创/同人 5-10 章盲评。

## 6. Chinese Web-Novel Structure

- [x] 平台画像区分 `qidian_male|fanqie_free|jinjiang_female|general_cn`。
- [x] 不把短句、高对白、快节奏和悬崖结尾设为所有平台的统一标准。
- [x] 每章声明 `chapter_duty`、`reader_gain`、代价、章节拓扑、结尾方式和承诺引用。
- [x] 章节拓扑按开篇、冲突、关系、揭露、余波、探索和兑现动态选择，不固定为同一种五段模板。
- [x] finalize 后向 `30_state/reward_ledger.jsonl` 写入 `reader_reward_entry_v2`，明确区分 planned、observed 与 observation status。
- [x] 第 1/3/10/30 章与卷首卷末强制语义审稿；重大揭露和关系转折可由章节风险触发。
- [x] repair、Humanizer、editorial 和 pacing 反馈压缩后回流下一章，已解决问题不重复灌入上下文。

## 6A. Reader Payoff And Anti-Formula Phase 2

- [x] `reader_payoff_review` 使用统一 `AgentTaskManifest v2`，声明 6 文件/20K 字符预算、唯一 JSON 输出、schema、validate、finalize apply 和 repair/human failure command。
- [x] `production next` 在 gate 通过后、finalize 前按 assurance/risk 编排 payoff task；`production loop --no-apply` 只能建任务和校验，不能自动定稿。
- [x] CLI 严格绑定当前 draft 路径/hash、章节卡 planned 字段、精确 evidence span 与 review JSON 自身 hash。
- [x] 工作单只读取一条上一章收益和最多八条相关承诺的 context digest，不把完整 reward/foreshadowing ledger 塞入 Agent 上下文。
- [x] observed 结果覆盖 duty、reader gain、cost、promise progress 和 ending；P0/P1 fake payoff 不能由 `verdict=pass` 覆盖。
- [x] `reader_reward_entry_v2` 只由显式 finalize 写入；canonical evidence 只保存 hash/offset/supports，不复制正文 span 文本。
- [x] `structure_history.jsonl` 记录 opening、topology、ending、scene、payoff position、emotional curve、dialogue acts、句段节奏、身体反应、伪细节和 hash n-gram。
- [x] 单一结构维度重复只警告；仅在结构、语言与收益位置同时重复时升级 P1，不强制悬崖、战斗、反转、升级、短句或高对白模板。
- [x] finalize 失败会原子回滚 reward/structure；revision rollback 会截断撤销章节记录并报告 rebuilt indexes。
- [x] 错误路径/hash/span、伪兑现、审稿后篡改与结构 P1 只污染 workbench validation，不污染 final/RAG/graph/TCS/SQLite。
- [~] payoff 审稿准确率、反公式化误报率及真实阅读收益改善仍需原创/同人 5-10 章盲评。

## 6B. Platform Contract And Creative Interaction Phase 3

- [x] 四种 market、五种 genre、五种 story phase 画像作为 wheel 资源进入 `config/quality_profiles/`，不再只依赖硬编码提示。
- [x] `effective_quality_contract_v1` 按 market + genre + phase + user-approved baseline + project overrides 的固定顺序深合并。
- [x] 生效合同进入章节卡与单一写作者 brief，并明确不是统一句长、对白率、快节奏或悬崖结尾模板。
- [x] `quality contract --chapter N` 可复核资源路径与 SHA-256，不写 canonical state。
- [x] `quality baseline-approve` 只接受已定稿章节和明确批准者，只保存 prose-free 结构观察且不会自动扩充。
- [x] `book_ideation_candidate_v1` 每轮只处理一个维度，提供 2-3 个带取舍选项，只保存用户明确选择或提供的答案。
- [x] 未完成目标读者、卖点、世界规则、主角欲望/缺陷、长线矛盾、卷级升级、结局边界和禁区前，不进入 book/fanfiction design。
- [x] `chapter_direction_candidate_v1` 为每个方向声明局部收益、角色代价、主线/伏笔/关系影响、章节职责、信息释放、结尾方式和风险。
- [x] `chapter_direction` 只在 guided、抽象纲要、卷边界、重大转折、连续返修或多合法剧情线触发，普通稳定章节不强制。
- [x] 两类任务均使用 AgentTaskManifest v2、strict validate、human apply、事务回滚和 no-pollution 边界。
- [x] Codex/Claude Skill 明确先取得人工选择，不替用户默选，也不绕过 manifest。
- [~] 不同平台/题材组合是否提升真实章节质量，仍需相同设定 5/10 章盲评。

## 6C. Independent Editorial And Feedback Governance Phase 4

- [x] `editorial_role_review_v2` 包含 `reviewer_instance_id`、Agent 产品/版本、`context_digest_hash`、`independence_mode`、`review_round` 和 `confidence`。
- [x] v2 提交严格校验角色 context、当前文件 hash、轮次和元数据；v1 只作为 `legacy_unknown` 兼容输入，不伪装成独立评审证据。
- [x] v2 的 P0/P1 必须引用当前章节精确正文证据；只有校验通过的阻断证据才有资格进入少数派保护。
- [x] 普通章节、AI 味复发、事实/关系风险、卷边界/重大兑现、同人和 P0/P1 风险按规则选择不同编辑角色，不再默认每章全员审稿。
- [x] 每个角色拥有独立 manifest、工作单和 `.context.json`；input files 不包含 peer result 或 aggregate，提交前禁止读取其他角色结论。
- [x] anti-ai、serial-verifier、planning、reader-quality 和 canon-fidelity 使用不同的最小上下文集合，均受 7 文件/18K 字符预算约束。
- [x] aggregate 输出一致 finding、冲突 finding、证据 span 重合度、严重度差异、少数派 P0/P1 和 human decision。
- [x] 有有效证据的少数派 P0/P1 不能被多数票覆盖，并进入 `need-human` 原因。
- [x] feedback registry 固定为 `50_workbench/quality_feedback/registry.jsonl`，记录 stable ID、open/carried/resolved/suppressed/expired、复发计数、TTL 和 owner task。
- [x] P0 不自动过期；P1 两个完整章节无复发后由 CLI 解决；P2 默认携带三章；每个任务最多携带五条相关 active 反馈。
- [x] 相同 issue code 复发会合并并递增；证据 hash 改变的语义复发标记 `gate_gaming_risk`，避免只换词绕过。
- [x] `quality feedback-status|feedback-resolve|feedback-suppress` 提供可执行治理命令，resolved/suppressed/expired 不进入新工作单。
- [x] revision rollback 会清理撤销章节产生的 feedback；registry 失败只产生 warning/fallback，不影响 final/RAG/graph/TCS/SQLite。
- [x] invalid v2 元数据、错误 context hash 和 feedback 生命周期操作不污染 canonical state。
- [~] 同宿主隔离上下文是否足以降低自写自审偏差、跨宿主评审是否进一步改善判断，仍需 5/10 章盲评。

## 6D. RAG Scale Engineering Phase 5

- [x] 固定数据集规格覆盖 50/200/500 章和 1,000/4,000/10,000 向量，生成算法、query count、top-k 与 dataset hash 可复现。
- [x] `local_hnsw` 使用成熟 `hnswlib`，SQLite 保留 metadata/hash/stable label；远程后端未实现时仍保持 experimental。
- [x] HNSW index、manifest 与 SQLite dirty state 不一致时 health check 失败，并给出唯一 rebuild 命令。
- [x] embedding 构建按 content hash 增量 upsert，未变化向量不重写，消失记录进入 stale。
- [x] revision rollback 会将受影响 chapter/vector stale；后续 canonical sync 可恢复同一 stable record ID。
- [x] `benchmark rag-scale-run` 真实记录 recall、错误事实率、P95、初始索引、单章增量、硬件、Python、模型和 backend config hash。
- [x] `local_sqlite` 与 `local_hnsw` 的 50/200/500 工程 smoke 均通过 stale/rollback/no-pollution。
- [x] 500 章固定工程集达到 Recall@10 >= 0.85、错误事实率 <= 0.02、P95 <= 1000ms。
- [x] `benchmark rag-production-template|rag-production-run` 已实现 production-model 证据协议：500 个不重复 final、50 条以上 hash/span 查询、七类风险、正式 embedding/reranker、禁用 fallback。
- [~] 真实 500 章中文定稿语料和正式模型尚未执行；当前只有 runner 与自动测试，仍不得用于文学质量声明。

## 7. Publication And Provenance

- [x] `publication report` 生成 `publication_risk_report_v1`。
- [x] 报告记录同人来源、用户权利声明、商业意图、来源混淆和 AI 辅助标识提醒。
- [x] 报告明确 `engine_performed_legal_verification=false` 与 `blocking=false`。
- [x] `publication export` 不因未核验权利或商业意图而失败。
- [x] 导出路径被限制在 `80_exports/`。
- [x] 引擎不向小说正文自动插入版权、授权或 AI 声明。
- [x] provenance 只保存产物路径与 hash，不保存小说正文。
- [x] 未经用户明确提供，不把作品描述为官方续作、已授权或原作者参与。

参考：[《著作权法》](https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html)、[同人案件说明](https://www.sdcourt.gov.cn/dyzy/372897/372899/44482953/index.html)、[人工智能生成合成内容标识办法答问](https://www.cac.gov.cn/2025-03/14/c_1743654685896173.htm)。

## 8. Benchmark Evidence

- [x] fanfiction benchmark 增加 canon fidelity、OOC control、原创贡献、分歧因果、来源原文原创性和 crossover 一致性六项指标。
- [x] fanfiction 每章缺少任一专用指标时，在写 benchmark 记录前失败。
- [x] benchmark 不保存章节正文，不写 canonical 状态。
- [x] 正式 run 记录 scenario SHA-256、host product、模型/宿主版本和 workflow version。
- [x] `technical-record` 在盲评前只记录 gate/repair/context/P0/pollution，不允许预填文学分。
- [x] `source-attach`、随机盲包、私有映射、独立提交和中位数聚合已实现；公开 manifest 不暴露 run ID。
- [x] 正式聚合只接受至少三名人工评审，要求不同 reviewer instance/session 并复验来源正文 hash；Agent 自动评分只能诊断。
- [x] 正式 claim gate 拒绝 diagnostic/self-review、身份泄漏、来源漂移、宿主/场景/模型不一致和缺失 production-model RAG。
- [x] Codex 原创当前协议已完成 15 章受控生产，其中第 6-15 章为本轮新增；工程记录为零 P0、零 canonical 污染、零最终 `need-human`，并保持文学评分为空。
- [~] 上述 15 章只证明连续生产、反馈回流和状态边界，没有独立盲评，不能计入正式原创质量胜出证据。
- [~] 使用公版、用户自有或明确授权样本完成 Codex 5 章同人 smoke。
- [~] 使用同一设定完成 Claude Code 5 章同人 smoke。
- [~] 完成 10 章原创质量、同人还原度、AI 味与连续生产成本盲评。
- [~] 至少三名独立评审完成逐章匿名评分。

## 9. No-Pollution And Regression

- [x] invalid canon/design 输出不改变 Bible、outline、state、final、RAG 或 SQLite。
- [x] 原文重构失败只写 workbench validation，不创建 `source_canon.json`。
- [x] 未核验商业同人可完成 canon、设计、writing task 和 publication export。
- [x] 专名、招式名与世界术语不会单独触发来源原文复现门禁。
- [x] release guard 继续禁止脚本内 LLM/provider 与默认 API key 要求。
- [x] Codex/Claude Skill 都保留 `production next -> agent-task brief -> Agent output -> validate -> explicit apply/finalize`。

## 10. Documentation And Discovery

- [x] README 公开说明四种创作模式、同人工作流、CLI、权利提示和原文复现边界。
- [x] `docs/CONFIGURATION.md` 说明 fanfiction 与 quality 配置字段。
- [x] `shared/command_protocol.md` 提供同人和发布中文指令映射。
- [x] `shared/iron_laws.md` 固化同人允许范围、advisory policy 与 Humanizer v3 边界。
- [x] 两个 Skill description 包含同人/AU/续写触发词。
- [x] Skill references 通过同步脚本复制，wheel 内不依赖外部 `shared/`。

## Verification

```powershell
python -m pytest tests/test_config.py tests/test_fanfiction_workflow.py tests/test_reader_payoff_review.py tests/test_quality_contract_and_creative_interaction.py tests/test_editorial_independence_and_feedback.py tests/test_benchmark.py tests/test_blind_review_phase6.py tests/test_rag_scale_phase5.py tests/test_rag_production_phase6.py
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/validate_skills.py
python scripts/release_surface_guards.py
python -m pytest
```

## Definition Of Done

功能层完成要求第 1-7、9-10 节及 5A、6D 工程项全部为 `[x]`。文学质量完成要求第 8 节、5A 的真实 5/10 章证据和 6D 的 production-model RAG 证据也全部为 `[x]`。在此之前只能宣称“同人工作流、中文网文质量约束、去 AI 味与 RAG 规模工程边界已实现”，不能宣称正文或真实语义质量已经稳定优于 `novel-skill`。
