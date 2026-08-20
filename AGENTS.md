# longform-novel-engine Agent Guide

本文件是 Codex、Claude Code 和其他工程 Agent 进入本项目时的首要说明。它同时记录长期架构约束与当前本地交接状态。不要仅凭旧阶段文档、已安装 Skill 版本或历史小说运行推断当前源码行为。

## 1. 新会话先做什么

进入仓库后先执行只读检查：

```powershell
git status --short
git log -1 --oneline
git tag --list "v0.6.*"
longform-engine --version
longform-engine skills status --tool codex --json
python scripts/check_agent_data_pipeline_readiness.py
```

先判断当前任务属于哪一类：

- **引擎开发**：以本仓库源码、测试和当前 diff 为准。先阅读本文件与当前版本 checklist，不要自动更新全局 Skill。
- **小说生产**：以目标小说的 `project.yaml`、`production next` 和 AgentTaskManifest 为准。不要扫描整个项目补上下文。
- **发布工作**：额外阅读 `docs/RELEASE_RUNBOOK.md`，核对版本、资源、构建产物、远程 tag 和安装环境，不得把本地 RC 写成已公开版本。

工作区可能包含用户或上一会话尚未提交的修改。不要 reset、checkout、覆盖或删除未知改动；先理解 diff，再在其上继续。

## 2. 当前发布状态

- 当前稳定版为不兼容旧人工审稿协议的 `v0.6.0`。它在故事引擎基础上增加方向选择 sidecar、五类 hash 绑定的十项人工深审、非 canonical 咨询、本地审稿台与三态质量状态。
- 协议与生产合同 readiness 以 `scripts/check_agent_data_pipeline_readiness.py` 的输出为准。
- `literary_evidence_ready` 保持 `false`，直到真实章节与独立盲评证据完整。
- 不要把任一本地小说运行、全局 Skill 状态或历史阶段文档当作源码事实源。

## 3. 产品与架构定位

本项目是面向百万字、数百章中文网文的本地工程化生产引擎，不是单次 Prompt 包，也不是脚本内 LLM 客户端。

```text
Host Agent
  负责创作、设计、修订、独立审稿和语义抽取
        |
        v
CLI control plane
  负责路径、hash、协议、证据、状态机、事务和显式 apply/finalize
        |
        v
Canonical filesystem
  保存批准的设计 Markdown、Bible、outline、final 和 semantic ledger
        |
        v
Derived views
  graph、character state、foreshadow state、TCS、RAG、vector store、SQLite
```

核心原则：

- 默认 `writing.mode = agent_skill`，使用 Codex/Claude 当前宿主会话，不需要 OpenAI、Anthropic 或 provider API key。
- Python 代码不得调用 LLM 代替 Host Agent，也不得新增多进程、worker 或并行 Agent 编排。
- CLI 是正式状态变更入口；Agent 只能写 manifest 允许的候选文件。
- 文件是事实源；SQLite、向量索引和查询缓存是可重建派生状态。
- final 是唯一正文证据源。摘要、RAG 命中、图谱和 Agent 推断不能覆盖 final 中的事实。
- 设计 Markdown 是创作事实权威；`canonical_delta_v1` 是经证据绑定的机器解释。
- 只接受当前项目协议，不新增已删除字段或路径的迁移、双读或双写路径。

## 4. 数据层与目录所有权

```text
00_governance/   开书确认、读者合同和生产规则
10_bible/        世界、人物、风格、同人 canon 和 research canon
20_outline/      全书/卷级规划、章节卡、锚点和伏笔计划
30_state/        semantic ledger、图谱、角色/伏笔当前状态和 TCS
40_manuscript/   submitted draft 与 final
50_workbench/    Agent task、brief、候选、审稿、修复和诊断
60_rag/          chunks、embeddings、vector store、上下文和检索缓存
70_runtime/      锁、事务、SQLite、审计包、benchmark 和验证报告
80_exports/      正文导出与发布风险报告
```

事实层级：

1. 人工批准的治理与设计 Markdown。
2. `40_manuscript/final/` 正文。
3. `30_state/semantic_ledger/chNNN.json` 中经 final 证据验证的章节增量。
4. 由上述事实物化的 graph、memory、foreshadow、TCS。
5. 可重建的 RAG、向量库和 SQLite。

Agent 禁止直接修改：

- `10_bible/`
- `20_outline/`
- `30_state/`
- `40_manuscript/final/`
- `60_rag/`
- `70_runtime/db/`

这些目录只能由 CLI 在 validate 通过后通过事务化 apply/finalize 更新。

## 5. AgentTaskManifest v4

当前磁盘协议只接受 Manifest v4。顶层字段固定为：

```text
schema_version
task_id
task_type
scope
role
io
policy
commands
created_at
```

规则：

- `io.inputs[]` 是 Agent 唯一允许读取的项目文件集合。
- `io.output` 只有一个路径和一个协议；每个 Agent task 默认只产出一个文件。
- `policy.boundary_profile` 引用版本化硬边界，不在 manifest 复制规则全文。
- manifest 创建后不可变；生命周期、current result 和消费关系只写 task index/event。
- manifest 必须在注册 index/event 前严格校验。无效任务不得成为活动任务。
- `production next` 只读；发现可确定修复的状态分裂时只返回 `agent-task reconcile` 命令。

四类 Agent 输出协议：

| 协议 | 用途 |
| --- | --- |
| `prose_markdown_v1` | 章节正文、修章、Humanizer、功能性扩写 |
| `design_document_v1` | 开书、人物、纲要、章节方向、同人和风格设计 Markdown |
| `evidence_review_v2` | 连贯性、收益、节奏、人物、场景、反 AI 和同人审稿 |
| `canonical_delta_v1` | 设计语义编译、章节事实、研究事实和同人 canon 增量 |

CLI 已知的章节号、路径、hash、角色和时间不得要求 Agent 机械回填。磁盘校验报告统一使用 `validation_report_v1`。

## 6. 标准 Agent 工作流

任何小说生产轮次都从这里开始：

```text
longform-engine production next project.yaml
-> longform-engine agent-task brief project.yaml TASK_OR_PATH
-> Agent reads only io.inputs
-> Agent writes only io.output.path
-> commands.validate
-> explicit commands.apply or chapter finalize
```

失败时只执行 manifest 的 `commands.failure`。不得手工猜测下一条命令、直接改 task index、使用 `--force`、跳章或自动批准 canonical 写入。

设计任务采用 Markdown 权威链：

```text
design task
-> Agent writes authoritative Markdown
-> result-validate
-> domain validate
-> human approve
-> design_semantic_compile task
-> Agent writes canonical_delta_v1
-> compile-validate
-> atomic apply of Markdown, delta and current views
```

章节闭环采用：

```text
story engine and rolling carrier plan
-> reader promise ledger / arc causal simulation / chapter direction options
-> chapter_direction_selection_v1 / approve / semantic compile / chapter_contract_v3
-> chapter_write
-> draft submit and deterministic gate
-> independent review barrier
-> immutable review bundle
-> mandatory human_story_review_v3 ten-dimension accept / repair / redirect
-> optional non-canonical consultation or full human repair candidate
-> conditional repair plan and replacement candidate
-> explicit chapter finalize
-> chapter semantic task
-> semantic validate/apply
-> chapter close
-> artifact compact/verify
```

章节未 close 时不得进入下一章。

## 7. 唯一章节合同与上下文编译

`20_outline/chapter_cards/chNNN.json` 是唯一 `chapter_contract_v3` 章节合同。它必须包含当前章节需要的承诺动作和当前有效因果模拟引用，并同时包含：

- 全书目标、卷目标和主角近期目标。
- 当下欲望、对抗力量、戏剧问题、最早失败、不可逆选择和可见代价。
- `chapter_turn`、`reveal_boundary`、`reader_gain`、必须演出/允许压缩过程、故事引擎、场景载体和状态变化。
- 登场人物稳定 ID。
- 受保护结果、禁止偏移、canon、世界规则、伏笔和禁止揭示引用。

内部事实仍编译为 `chapter_fact_inventory_v1`，承诺账本、因果模拟和编辑模式分别留在规划/编辑控制面；作者只读取 `chapter_story_brief_v2` Markdown。fact ID、来源 hash、promise ID、模式代码、RAG、Graph、TCS 与 SQLite 词汇不得进入作者工作单。Humanizer、收益、节奏、人物、场景、同人和人工故事审稿必须绑定同一 `chapter_contract_hash` 与候选 hash。发现职责、人物名单或约束来源分裂时返回 `chapter_contract_inconsistent`。

事实清单中同一事实只出现一次，并保存来源 hash、优先级和选择理由。核心 canon/world-rule 引用必须完整解析；`[depth-limited]`、缺失来源或必要证据无法装入预算时返回 `context_evidence_incomplete` 或 `prompt_budget_exceeded`，不得在证据不完整时生成可 pass 的审稿任务。

## 8. Prompt、角色与会话

当前注册表包含 29 个专业角色、27 类任务、4 类输出协议、12 个渐进式 Playbook 和 44 个正交故事分面。`repair_coordinator` 编排修复，`human_review_advisor` 只提供不能直接写 canonical 的咨询。

运行时 Prompt 按以下顺序编译：

```text
安全与事实边界
-> 角色核心区段
-> 当前唯一任务
-> 任务专属执行区段
-> 最多三个相关 Playbook
-> 最多三个活跃故事分面
-> 去重后的正文/canonical 证据
-> 输出与交接要求
```

不设置为了填充内容的 Prompt 字符下限。容量由 `config/agent_context_profiles.yaml` 的 adaptive profile 决定；校准案例、未触发方法和已解决 feedback 不进入普通生产 Prompt。

会话策略：

- 开书和卷级规划可持续使用项目协调会话。
- 每章 `chapter_write` 使用新的作者会话。
- repair 可继续本章作者会话，但只能依据已验证 repair plan。
- Humanizer 使用独立修订会话。
- 连贯性、人物、收益、节奏、场景、反 AI 和同人审稿使用隔离审稿会话。
- final 后语义档案使用独立档案会话。
- 同一候选的人工咨询复用章节咨询会话；候选变化后旧咨询全部 stale。

CLI 不创建 Codex/Claude 子进程，聊天记录也不是长期状态。交接只依赖 manifest、brief、canonical 文件和审计事件。

## 9. 审稿屏障与 Repair

确定性 gate 与 Agent 语义审稿是两层不同机制。普通 finding 不能阻止其他必审角色完成；只有全部必审结果绑定同一候选 hash 后，CLI 才能冻结 review bundle。

`evidence_review_v2` 要求：

- 每个必审维度必须为 `checked`、`insufficient` 或允许的 `not_applicable`。
- `checked` 必须提供一至两个可回读正文 evidence ID。
- 连贯性、人物状态和同人 canon 等维度还需角色合同要求的 canonical ref。
- `insufficient` 不得得到 `pass`。
- P0/P1 必须为 `confirmed` 且有有效证据。
- 审美偏好、固定句长、对白率或悬崖结尾不能单独构成 P0/P1。

存在阻断 finding 时：

```text
CLI freezes complete review_bundle
-> repair_plan_synthesis by repair_coordinator
-> repair plan validate
-> repair candidate task
-> full replacement prose
-> rerun complete review barrier
```

修复主编只归并根因、依赖顺序、最小修改半径、保护项和回归维度。它不能删除、降级或投票否决有效 P0/P1，也不能写正文。只有有效替代稿提交才消耗一次修复额度；两轮后仍有 P0/P1 必须进入 `repair_budget_exhausted`。

冻结 bundle 后，`human_story_review_v3` 必须完成十项检查；accept 需要关键转折、人物选择/情绪、读者收益三类精确 span，并绑定候选、章节合同、承诺账本、因果模拟和 bundle 五类 hash。人工改稿只能发生在已验证 repair task 的完整候选中，以 `agent=human` 提交后重跑 gate 和全部独立审稿。本地网页与咨询不得直接 apply、finalize 或写 canonical。

## 10. 生命周期、事务与无污染

task event/index 记录：

- `consumes_task_id`
- `consumed_by_task_id`
- `satisfied_by_result_sha256`
- `supersedes_task_ids`

子任务注册后原子消费父任务。新候选提交后，旧候选及绑定旧 hash 的审稿任务进入 `superseded`。无法由完整 lineage 唯一证明时进入 need-human，不得猜测。

所有 canonical apply 必须位于 transaction v3 中。`preparing` 不开放写边界，`prepared` 后才允许 mutation，`applied` 必须先于快照清理落盘。任一步失败必须回滚文件和 SQLite 参与者；成功事务不能残留快照。异常退出后先执行 `recovery status`，再按精确 SHA 和人工审批执行 discard/rollback/cleanup/reclaim；禁止手工删除锁或快照。失败 Agent 输出、无效 review、错误 delta、RAG 结果或 repair plan 不得污染 final、Bible、outline、graph、TCS、RAG 或 SQLite。

## 11. 章节语义与派生状态

章节 final 后，Agent 只完整读取正文一次并输出 `canonical_delta_v1`。CLI 验证 final hash、evidence span、实体 ID、前置关系状态、人物知识来源和伏笔窗口后，生成内部 semantic ledger，并原子物化：

- story graph 与 relationship state
- character current state
- foreshadow actual state
- TCS 和下一章约束
- RAG chunks、embeddings、vector store 和 semantic context
- SQLite 查询索引

这些物化视图都可从 final、批准设计和 semantic ledger 重建。摘要只用于定位证据，不能单独证明事实变化。

## 12. 产物生命周期

- 候选正文按 hash 存入 `50_workbench/candidate_blobs/`，相同内容只保存一次。
- book、character、fanfiction、outline 等项目准备任务完成后，可使用 `artifacts compact --scope project-setup` 归档非 canonical 工作材料。
- 章节 close 后归档工作单、候选、审稿、validation、transaction 和 event；保留 final、semantic ledger、closure 和当前状态。
- 默认只保留最近两章为活动工作区。
- compact 前必须 dry-run，真实执行后必须运行 `artifacts verify`。
- restore 只能恢复审计材料，不能把旧候选静默提升为 canonical。

## 13. 修改代码时的所有权

优先从这些入口定位：

- CLI 顶层组合：`src/longform_engine/cli.py`；recovery 命令组：`cli_recovery.py`
- Agent 协议与任务：`agent_protocols.py`、`agent_tasks.py`、`agent_results.py`
- 角色和 Prompt：`roles.py`、`config/agent_roles/`
- 生产状态机：`production.py`、`orchestration/pipeline.py`
- 章节合同：`chapter_contract.py`
- 门禁与审稿：`gates/pipeline.py`、`editorial/pipeline.py`
- repair 与人工深审：`repair_coordination.py`、`human_story_review.py`
- 人工咨询与本地审稿台：`human_review_consultation.py`、`review_server.py`
- 三态质量状态：`quality/status.py`
- 章节语义：`semantic/pipeline.py`
- RAG 与向量：`rag/pipeline.py`、`vectorstore/pipeline.py`、`vector_backends.py`
- SQLite full/delta 索引：`db/sqlite_index.py`
- 存储与恢复：`storage/project.py`、`storage/recovery.py`
- 归档：`artifacts.py`

修改要求：

- 修改 Agent 输出格式时同步 renderer、normalizer、validator、任务合同和测试。
- 修改状态机时覆盖成功、失败、幂等、stale、superseded、reconcile 和 rollback。
- 修改 canonical 写入时验证事务失败零污染。
- 简单路径拼装或转发不要新增单次 wrapper；优先使用已有直接 API。
- 不提交正文、API key、完整 Prompt 日志、模型、SQLite、缓存、`dist/` 或 `novels/`。
- 测试保持单进程，新增测试文件必须少而聚焦。

## 14. 当前事实源

优先阅读：

1. `README.md`
2. `docs/ARCHITECTURE.md`
3. `docs/STORAGE_MODEL.md`
4. `docs/V0_6_0_RELEASE_CHECKLIST.md`
5. `docs/GATE_MODEL.md`
6. `docs/SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION.md`
7. `docs/CONFIGURATION.md`
8. `docs/RAG_MODEL.md`
9. `docs/QUALITY_BENCHMARK_RUNBOOK.md`
10. `docs/SKILL_INSTALLATION.md`
11. `docs/RELEASE_RUNBOOK.md`

历史发布说明只记录版本变化，不得覆盖当前 Manifest v4、`evidence_review_v2`、29 角色、配置注册表或 `chNNN.md` 存储契约。

## 15. 验证与发布纪律

代码收口至少运行：

```powershell
python -m ruff check src tests
python -m mypy src/longform_engine/vector_backends.py src/longform_engine/chapter_contract.py src/longform_engine/storage/recovery.py
python -m pytest --cov=longform_engine --cov-report=term-missing
python scripts/validate_skills.py
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/check_markdown_links.py
python scripts/check_agent_data_pipeline_readiness.py
python scripts/release_surface_guards.py
longform-engine release check --repository . --channel rc --json
```

发布候选还需构建并审计 wheel/sdist、执行临时 pipx smoke 和远程版本检查。只有用户明确授权后才能创建 tag、推送或发布。tag 不得移动或覆盖；发现缺陷时使用新补丁版本。
