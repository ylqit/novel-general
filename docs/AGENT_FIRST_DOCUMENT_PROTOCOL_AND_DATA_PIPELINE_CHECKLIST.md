# Agent 文档协议、Prompt 角色与数据链路准入 Checklist

本文档是 v0.4.1 Agent-first 协议的当前事实源。状态含义：`[ ]` 未实现，`[~]` 已实现但缺最终证据，`[x]` 已实现并有可重复校验。

实施顺序是硬门：先完成 Schema 去冗余，再完成 27 个角色和 12 个 Playbook 的专业化。两层全部通过前，不运行真实 30/20 章生产，也不发布 v0.4.1。

## 不可变边界

- Agent 负责创作、设计、语义抽取和独立审稿；CLI 负责路径、hash、证据、状态机、事务和 canonical 物化。
- 不恢复脚本内 LLM，不要求 provider API key，不新增 worker、多进程或并行 Agent。
- Agent 不能直接写 final、Bible、outline、graph、memory、foreshadow、TCS、RAG 或 SQLite。
- Markdown 设计文档是创作事实权威；结构化 delta 是经证据绑定的机器解释。
- canonical JSON 保持为状态机与检索的稳定机器视图，不能被自然语言或 RAG 检索结果直接覆盖。
- v0.4.1 不读取、转换、双写或迁移旧 manifest/result schema。

## Phase 1. Four Agent Protocols

- [x] 24 类活动任务只映射四种 Agent 输出协议。
- [x] `prose_markdown_v1` 只承载章节写作、修章、Humanizer 和扩写的完整 Markdown 正文。
- [x] `design_document_v1` 是纯 Markdown，拒绝 YAML front matter、JSON sidecar 和 CLI 已知字段。
- [x] `evidence_review_v1` 顶层严格为 `schema`、`verdict`、`coverage`、`findings`。
- [x] `canonical_delta_v1` 顶层严格为 `schema`、`delta_type`、`coverage`、`changes`、`evidence`、`uncertainties`。
- [x] 每个 Agent task 只有一个 `io.output.path` 和一个 `io.output.protocol`。
- [x] CLI 输出合同不再重复 output mode/schema、单路径/路径数组或非权威 notes 字段。

### Review Contract

- [x] `coverage` 使用 `dimension -> checked|insufficient|not_applicable`。
- [x] finding 只含 `code`、`severity`、`certainty`、`diagnosis`、`evidence_ids`、`reader_impact`、`repair_target`、`preserve`。
- [x] finding 不重复角色注册表已知的 dimension，不包含 notes。
- [x] P0/P1 必须为 `confirmed` 且有唯一可回读证据。
- [x] 任一必审维度为 `insufficient` 时不得 pass。
- [x] 审美偏好、固定短句率、对白率或悬崖结尾不能成为 P0/P1。

### Canonical Delta Contract

- [x] delta 不含与 coverage 重复的 unchanged 数组。
- [x] evidence 使用 `/changes/...` JSON Pointer 到 evidence ID 列表的映射。
- [x] `changes` 内递归禁止 `evidence_id`、`evidence_ids` 和 `evidence_refs`。
- [x] 章节语义使用独立 `entity_coverage` 声明登场实体和活跃 thread 的变化/无变化。
- [x] `uncertainties` 非空或 coverage 为 insufficient 时禁止 canonical apply。
- [x] Agent 不回填章节号、路径、hash、角色、时间、planned facts 或 canonical target。

## Phase 2. Markdown Design Compile Chain

- [x] 十类设计任务统一输出权威 Markdown。
- [x] 每类设计文档有任务专属中文必需标题，缺失或空标题不能批准。
- [x] 未经 `--approved-by human` 的文档不能创建 `design_semantic_compile`。
- [x] 编译 Agent 只输出 `canonical_delta_v1`，不能修改批准文档。
- [x] delta 的每项事实必须由批准 Markdown 中可回读 span 支持。
- [x] Markdown hash 改变后旧 delta 立即失效。
- [x] apply 在同一事务中提交 canonical Markdown、已验证 delta 和结构化当前视图。
- [x] 任一写入或物化失败时全部回滚，不留下半更新状态。

标准链路：

```text
design task
-> Agent writes authoritative Markdown
-> agent-task result-validate
-> intelligence validate
-> intelligence approve --approved-by human
-> design_semantic_compile task
-> Agent writes canonical_delta_v1
-> agent-task result-validate
-> intelligence compile-validate
-> intelligence apply --document ... --delta ... --approved-by human
```

## Phase 3. AgentTaskManifest V4

- [x] 磁盘顶层严格为 `schema_version`、`task_id`、`task_type`、`scope`、`role`、`io`、`policy`、`commands`、`created_at`。
- [x] `scope` 是章节、范围和项目范围的唯一表达，不再重复顶层 chapter number。
- [x] `role` 保存角色、实际区段、Playbook 区段、overlay 与唯一 selection hash。
- [x] 删除 playbook bundle hash 和独立 selection reasons 数组。
- [x] `io.inputs[]` 一次记录 path、requirement、hash、字符数和选择理由。
- [x] `policy.boundary_profile` 引用版本化边界，不重复整组边界文本。
- [x] 生命周期和 current result 只写 task index/event，不回写 immutable manifest。
- [x] 完整编译 Prompt hash 只写实际 brief/event，不混入静态任务合同。
- [x] Manifest v1/v2/v3 明确拒绝，不存在兼容读取。

## Phase 4. Validation And No-Pollution

- [x] 磁盘校验结果统一为 `validation_report_v1`：`ok`、`stage`、`subject`、`errors`、`warnings`、`blockers`、`provenance`、`next_command`。
- [x] 无效 manifest 在 index/event 注册前失败。
- [x] 无效输出只保留一个受控诊断文件，不写 normalized 成功副本。
- [x] evidence span、source hash、实体 ID、关系前置状态、角色知识来源和伏笔窗口由 CLI 回读验证。
- [x] 设计 delta 引入文档不存在的事实、未知 ID 或错误 span 时拒绝 apply。
- [x] Agent 输出和失败事务不污染 final、Bible、outline、graph、RAG、TCS 或 SQLite。
- [x] 现有 canonical 消费者继续读取稳定 JSON 视图，不解析自然语言 Markdown。

## Phase 5. Twenty-Seven Professional Roles

- [x] 注册表包含 27 个活动角色，无 generic fallback、legacy extractor 或 Agent 总编辑。
- [x] 每个角色源文档都含 `core`、`decision_model`、`workflow`、`diagnostics`、`failure_modes`、`calibration`。
- [x] 每个角色有独立判断模型、诊断边界和角色自有正例、反例、边界案例。
- [x] calibration 为 `calibration_only`，普通生产不加载。
- [x] generic `quality_risk`、`need_human`、`insufficient_evidence` trigger 被拒绝。
- [x] 审稿角色声明独立 review dimensions 和 finding codes，最多激活两个 Playbook。
- [x] `canonical_semantic_archivist` 分离章节事实抽取与设计 Markdown 编译，两类方法不串用。
- [x] Codex 与 Claude Code 只存在宿主展示差异，角色语义和 selection hash 相同。

角色族专业要求：

| 角色族 | 必须具备 | 禁止 |
| --- | --- | --- |
| 创意 | 单一决策、差异选项、真实代价、人工记录 | 替用户默选 |
| 规划 | 目标阶梯、因果引擎、滚动窗口、依赖影响 | 空泛章节占位 |
| 创作 | 场景、欲望、选择、反应、代价与余波 | 提纲代正文、旁白代人物 |
| 修订 | finding 根因、保留项、最小影响面 | 借修复擅改剧情 |
| 审稿 | 诊断树、证据、严重级别、误报控制 | 无证据 P0/P1、自写自审 |
| 档案 | 来源权威、不确定性、原子增量 | 推断未写事实 |

## Phase 6. Twelve Progressive Playbooks

- [x] 覆盖开篇主线、场景因果、人物自主性、对白潜台词、心理情绪、世界规则、关系推进、伏笔悬疑、连载节奏、反模板表达、群像视角和同人 canon。
- [x] 每个 Playbook 分离 core、creation、review、repair、facets、examples、false_positives、calibration。
- [x] 每个 Playbook 至少有三组自有正反微例和边界校准材料。
- [x] generation 只加载 creation；review 只加载 review 与 false-positive；repair/Humanizer 只加载 repair。
- [x] 创作任务最多激活三个 Playbook，独立审稿最多两个。
- [x] reference/calibration 区段默认永不进入生产 Prompt。
- [x] task type、有效 finding、人工 quality focus 和故事阶段风险共同决定模块选择。
- [x] 固定 8K/20K/18K 失败阈值已删除，容量档位和分配比例只由 `config/agent_context_profiles.yaml` 提供。
- [x] 字符/文件数只作诊断；工作单记录保守 token-unit 估算、软硬目标、顺序上下文批次和阻断原因。
- [x] 章节正文保持单一作者输出；范围/项目证据可顺序拆分并按 source hash/evidence ID 确定性汇总。
- [x] 核心事实仍无法容纳时返回 `prompt_budget_exceeded` / `need-human`，不静默截断。
- [x] 每章作者、独立修订、独立审稿、独立档案和项目协调会话策略进入 brief 与 `production next`。
- [x] 核心事实或必要方法超限时返回 `prompt_budget_exceeded`，不静默截断。

## Phase 7. Minimal Verification

- [x] 不新增测试文件，测试并入现有综合测试和单一 YAML fixture。
- [x] 角色注册、四协议、纯 Markdown 设计、Manifest v4、独立审稿和 no-pollution 专项通过。
- [x] 修章/Humanizer 不加载 creation，审稿不加载 repair，校准材料不进入工作单。
- [x] readiness 当前报告 27 roles、12 playbooks、24 tasks、4 protocols 和 single-process sequential。
- [x] 本轮自适应预算、会话与专业 Prompt 改造后的完整单进程 pytest 通过；最终发布候选回归为 `293 passed in 619.84s`。
- [x] Skill 引用同步、Skill 校验和 resource manifest 在最终 hash 固定后全部通过。
- [x] release guards 禁止旧协议、脚本内 LLM 和多进程编排。

### Verification Evidence (2026-08-17)

- 自适应预算与混合会话专项：`2 passed`。
- 完整单进程 pytest：最终发布候选回归为 `293 passed in 619.84s`。
- readiness：11/11，报告 27 roles、12 playbooks、44 unique facet adapters、24 tasks、4 protocols 和 `single_process_sequential`。
- `sync_skill_references.py --check`、`build_resource_manifest.py --check`、`validate_skills.py`、`release_surface_guards.py` 全部通过。

## Phase 8. Production And Release Boundary

- [ ] 完成两组不同混合题材各 5 章人工盲评。
- [ ] 完成六类原创 30 章和 SAO 同人 20 章真实生产验收。
- [ ] 用户完成人物声音、主线、场景、OOC、节奏和 AI 味评分。
- [ ] 版本、README、资源、wheel/sdist 和临时 pipx 验证统一为 v0.4.1。
- [ ] 真实质量准入通过后才创建 release commit、tag 和 GitHub Release。

在 Phase 8 真实文学验收前，只能说明协议、边界和 Prompt 架构完成，不得宣称文学质量全面超越 `novel-skill`。
