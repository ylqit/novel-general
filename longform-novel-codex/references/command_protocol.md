# 中文工程指令协议

中文斜杠指令只用于 Codex App、Codex CLI 和 ClaudeCode 的交互层。所有正式执行必须落到 `longform-engine ...` CLI；Agent 只能写入 `50_workbench/agent_drafts/`，不能直接写 final、RAG、story graph、memory、TCS 或 SQLite。

## 使用规则

- `project.yaml` 表示当前小说项目配置文件。
- `N` 表示章节号，`A/B` 表示章节范围。
- `/工程开书` 是用户侧唯一新书启动入口：没有项目配置时进入创建向导并随后开书；已有项目配置时只执行开书初始化。
- 候选稿、修复稿、Humanizer 输出和审稿输出都是 workbench 产物，必须重新经过 `draft submit` 和 `chapter finalize` 才能进入正式正文。

## 项目与配置

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程开书` | `longform-engine open-book --interactive` | 无 | 新建项目目录、`project.yaml`、`00_governance/`、`30_state/` | 当前目录没有 `project.yaml` 时，进入交互式创建向导，询问标题、slug、输出目录、模板风格、总字数、章节数、单章字数和卷数；确认后创建项目并执行开书初始化。不得直接写 final、RAG、SQLite。 |
| `/工程开书 project.yaml` | `longform-engine open-book project.yaml` | `project.yaml` | `00_governance/`、`30_state/` | 已有项目时只写入开书确认、读者契约和生产规则，不覆盖项目配置。 |
| `/工程校验` | `longform-engine validate-config project.yaml --explain` | `project.yaml` 或 `--template qidian-longform` | 只读 | 校验项目配置或模板配置。 |
| `/工程状态` | `longform-engine status project.yaml` | `project.yaml` | 只读 | 查看当前章节、门禁、stale 和项目状态。 |

## 生产体验编排

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程下一步` | `longform-engine production next project.yaml` | `project.yaml` | 只读 | 读取 Agent task、gate、draft/final 和 editorial 状态，输出当前最高优先级安全动作。 |
| `/工程工单` | `longform-engine agent-task brief project.yaml TASK_OR_PATH` | `project.yaml`、`TASK_OR_PATH` | 只读 | 将当前 `AgentTaskManifest v4` 渲染成 Codex / Claude Code 可执行中文工作单。 |
| `/工程生产状态` | `longform-engine production status project.yaml` | `project.yaml` | 只读 | 输出 GUI/API 稳定状态摘要，包含 next action、Agent task 统计和 board totals。 |
| `/工程生产看板` | `longform-engine production board project.yaml` | `project.yaml` | 只读 | 按章节显示 draft、final、gate、repair、graph、memory、pacing 和 editorial 状态。 |
| `/工程推进` | `longform-engine production loop project.yaml --no-apply` | `project.yaml` | 确定性流程产物；不自动 apply/finalize | 推进确定性步骤，遇到 Agent 输出、人工确认或 canonical apply/finalize 时暂停。 |
| `/工程创意工单` | `longform-engine intelligence task project.yaml --task-type book_ideation` | `project.yaml` | workbench 候选 | 每轮只处理一个创意维度，Agent 给 2-3 个带取舍的选项；必须记录用户明确选择。 |
| `/工程章节方向` | `longform-engine intelligence task project.yaml --task-type chapter_direction --chapter N` | `--chapter N` | workbench 候选 | 仅在 production next 判定需要时生成 2-3 个方向；人工 apply 后由 CLI 更新章节卡。 |
| `/工程人物设计` | `longform-engine character design-task project.yaml` | `project.yaml` | workbench 候选 | 生成 `character_expression_profile_v1` 工单；旧 Book Design v1 会在写第一章前进入此补全步骤。 |
| `/工程人物设计校验` | `longform-engine character design-validate project.yaml --file ...` | `--file` | validation 报告 | 校验叙事表达画像、人物覆盖、声音/行为/身体/面具/反差合同，不写 Bible。 |
| `/工程人物设计应用` | `longform-engine character design-apply project.yaml --file ... --approved-by human` | `--file`、人工确认 | `10_bible/character_expression.json` | 事务应用人物表达合同；Agent 不能直接写 Bible。 |
| `/工程人物审稿` | `longform-engine character audit-task project.yaml --from-chapter A --to-chapter B` | 章节范围 | workbench 候选 | 跨章检查声音适配、对白可交换性、工具人化、身体在场、旁白代讲和说明式对白。 |
| `/工程人物审稿校验` | `longform-engine character audit-validate project.yaml --file ...` | `--file` | validation 报告 | 每章和每个被审人物都必须有当前 hash/span 证据，pass 也不能空审。 |
| `/工程人物样本批准` | `longform-engine character samples-approve project.yaml --file ... --approved-by human` | 定稿 span、人工确认 | `10_bible/character_expression.json` | 只允许把 final 精确片段批准为有界正/反例；不复制整章，不由 Agent 自批。 |
| `/工程质量合同` | `longform-engine quality contract project.yaml --chapter N --explain` | `--chapter N`；可选 `--compare-market fanqie_free` | 只读 | 编译起点主合同、题材、全局/平台阶段、人工批准基线和项目覆盖；番茄比较始终非阻断。 |
| `/工程批准风格基线` | `longform-engine quality baseline-approve project.yaml --chapter N --approved-by NAME` | 已定稿章节、批准者 | `10_bible/style_profiles/approved_style_baseline.json` | 只保存 prose-free 结构指纹；不会自动扩充。 |

生产体验入口规则：

- Codex / ClaudeCode 每轮优先执行 `/工程下一步`。
- 如果下一步是 Agent task，必须执行 `/工程工单` 并只读取工作单与 manifest `io.inputs`。
- `/工程推进` 不能替代 Agent 写正文、修章、润色、语义 JSON 或审稿 JSON。
- `/工程推进` 默认不自动进入 `chapter finalize`，也不自动 apply graph/memory/pacing。

## 创作模式、同人与发布

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程同人Canon任务` | `longform-engine fanfiction canon-task project.yaml --input 50_workbench/fanfiction_sources/source.txt` | 一个或多个 `--input` | `50_workbench/intelligence_tasks/`、候选路径 | 只读取 manifest 声明来源，生成 `fanfiction_source_canon_v1` 工作单。 |
| `/工程同人Canon校验` | `longform-engine fanfiction canon-validate project.yaml --file ...` | `--file` | 校验报告 | 校验来源 hash/span、命名空间和原文复现，不写 Bible。 |
| `/工程同人Canon应用` | `longform-engine fanfiction canon-apply project.yaml --file ... --approved-by human` | `--file`、人工确认 | `10_bible/fanfiction/source_canon.json` | 事务写入转述 canon；不保存连续来源正文。 |
| `/工程同人设计任务` | `longform-engine fanfiction design-task project.yaml` | `project.yaml` | `50_workbench/intelligence_tasks/`、候选路径 | 生成声音合同、分歧点、原创主线、蝴蝶效应和 crossover 规则工作单。 |
| `/工程同人设计校验` | `longform-engine fanfiction design-validate project.yaml --file ...` | `--file` | 校验报告 | 校验角色引用、分歧因果、原创贡献和跨来源规则。 |
| `/工程同人设计应用` | `longform-engine fanfiction design-apply project.yaml --file ... --approved-by human` | `--file`、人工确认 | `10_bible/fanfiction/` 与受控 Bible | 事务应用同人设计，不修改来源文件。 |
| `/工程同人状态` | `longform-engine fanfiction status project.yaml` | `project.yaml` | 只读 | 查看 canon/design 状态与非阻断权利提示。 |
| `/工程发布风险` | `longform-engine publication report project.yaml` | `project.yaml` | `80_exports/publication_reports/`、provenance | 生成 `publication_risk_report_v1`；所有提醒均为 advisory。 |
| `/工程发布导出` | `longform-engine publication export project.yaml` | `project.yaml` | `80_exports/` | 导出 final 正文并生成风险报告；不向正文插入声明。 |

同人模式允许使用声明来源的角色名、关系、世界观、力量体系、时间线、续写、前传、AU、分歧和 crossover。`rights_status` 与 `commercial_intent` 只记录和提示，不阻断 Agent task、validate、finalize 或 export。整段来源正文、跨 JSON 字段重构和章节拼接仍必须失败。

## 章节生产

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程章节卡` | `longform-engine plan-chapter project.yaml --chapter N` | `--chapter N` | `20_outline/chapter_cards/` | 生成或刷新章节卡。 |
| `/工程分镜` | `longform-engine beat project.yaml --chapter N` | `--chapter N` | `50_workbench/beats/` | 生成 Beat Sheet。 |
| `/工程续章` | `longform-engine continue-write project.yaml --chapter N` | `--chapter N` | `50_workbench/writing_tasks/` | 生成给 Agent 的章节任务包。 |
| `/工程批量续章` | `longform-engine batch-write project.yaml --chapters N --stop-on-gate-failure` | `--chapters N` | `50_workbench/writing_tasks/`、run reports | 安全调度多章任务，遇到门禁失败停止。 |

## 草稿与门禁

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程提交稿` | `longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex` | `--chapter N`、`--file`、`--agent` | `40_manuscript/draft/`、`50_workbench/gate_artifacts/` | 提交 Codex 草稿并触发受控 draft 流程。 |
| `/工程提交稿` | `longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.claude.md --agent claude` | `--chapter N`、`--file`、`--agent` | `40_manuscript/draft/`、`50_workbench/gate_artifacts/` | 提交 ClaudeCode 草稿并触发受控 draft 流程。 |
| `/工程验稿` | `longform-engine gate-check project.yaml --chapter N` | `--chapter N` | `50_workbench/gate_artifacts/` | 执行章节门禁。 |
| `/工程语义验稿` | `longform-engine gate-check project.yaml --chapter N --semantic` | `--chapter N` | `50_workbench/gate_artifacts/` | 执行 deterministic evidence gate；高风险章节会生成 Agent 语义审查阻断。 |
| `/工程语义审查任务` | `longform-engine gate semantic-task project.yaml --chapter N --source draft` | `--chapter N` | `50_workbench/gate_artifacts/` | 为动机、空间、能力、关系、伏笔和因果生成证据化 Agent 审查任务。 |
| `/工程语义审查校验` | `longform-engine gate semantic-validate project.yaml --chapter N --file ...` | `--chapter N`、`--file` | 只读 | 校验正文 span、canonical 引用、实体 ID 与结果 schema。 |
| `/工程语义审查应用` | `longform-engine gate semantic-apply project.yaml --chapter N --file ...` | `--chapter N`、`--file` | `50_workbench/gate_artifacts/` | 仅把已校验审查结论应用到 gate lane，再重新计算门禁；不直接写 canonical state。 |
| `/工程放行` | `longform-engine gate-waiver project.yaml --chapter N --reason "reason"` | `--chapter N`、`--reason` | `50_workbench/gate_artifacts/` | 记录人工放行理由。 |
| `/工程节奏` | `longform-engine pacing-review project.yaml --chapter N` | `--chapter N` | `50_workbench/gate_artifacts/` | 执行章节节奏检查。 |
| `/工程语义节奏` | `longform-engine pacing-review project.yaml --chapter N --semantic-reader` | `--chapter N` | `50_workbench/gate_artifacts/` | 生成语义读者视角节奏检查。 |
| `/工程修复状态` | `longform-engine repair status project.yaml --chapter N` | `--chapter N` | 只读 | 查看审稿屏障、当前不可变计划和已消耗修复轮次。 |
| `/工程修复主编` | `longform-engine repair synthesis-task project.yaml --chapter N` | `--chapter N` | `50_workbench/repair_plans/chNNN/` | 仅在全部必审结果绑定同一候选 hash 后冻结 review bundle，并生成修复主编任务。 |
| `/工程校验修复计划` | `longform-engine repair synthesis-validate project.yaml --chapter N --file ...` | `--chapter N`、`--file` | validation report | 确认全部 P0/P1、稳定 finding ID、严重级别、最小修改范围和 preserve 冲突。 |
| `/工程候选修章` | `longform-engine repair candidate-task project.yaml --chapter N --agent codex` | `--chapter N`、`--agent` | `50_workbench/repair_candidates/` | 根据已验证的不可变 rNN 计划生成完整替代稿任务，不直接进入 final。 |
| `/工程定稿` | `longform-engine chapter finalize project.yaml --chapter N --approved-by human` | `--chapter N`、`--approved-by` | `40_manuscript/final/`、收益与结构账本 | 将通过或有效放行的章节写入唯一正文证据层；不会根据正文开头伪造摘要，也不会提前更新图谱、TCS、RAG 或 SQLite。 |
| `/工程章节语义任务` | `longform-engine chapter semantic-task project.yaml --chapter N` | `--chapter N` | `50_workbench/semantic_tasks/` | 让 Agent 只完整读取一次 final，输出统一章节语义 delta。 |
| `/工程章节语义校验` | `longform-engine chapter semantic-validate project.yaml --chapter N --file ...` | `--chapter N`、`--file` | validation report | 校验 final hash、精确 span、实体 ID、关系旧状态、角色知识来源、伏笔 ID/窗口和完整性声明。 |
| `/工程章节语义应用` | `longform-engine chapter semantic-apply project.yaml --chapter N --file ...` | `--chapter N`、`--file` | 语义账本、graph、角色当前视图、伏笔状态、TCS、RAG、SQLite | 显式、事务化物化全部章节知识；不同候选不得覆盖已落盘语义账本。 |
| `/工程语义重建` | `longform-engine chapter semantic-rebuild project.yaml --through N --approved-by human` | `--through N`、`--approved-by` | graph、角色当前视图、伏笔状态、world、timeline、TCS、RAG、SQLite | 只从连续 canonical semantic ledgers 重建派生视图；用于回填完成后的迁移收口，不读取旧派生状态作为事实。 |
| `/工程关闭章节` | `longform-engine chapter close project.yaml --chapter N --approved-by human` | `--chapter N`、`--approved-by` | 章节关闭记录、按章审计 ZIP | 验证语义与所有派生视图后关闭章节；保留最近两章活动工作区，才允许进入下一章。 |

## RAG / Semantic / Memory / Graph

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程模型列表` | `longform-engine models list` | 无 | 只读 | 列出支持的语义模型 profile。 |
| `/工程模型安装` | `longform-engine models install project.yaml --profile bge-m3 --download` | `--profile` | `70_runtime/models/` | 准备或下载默认 BGE 模型。 |
| `/工程模型检查` | `longform-engine models verify project.yaml` | `project.yaml` | 只读 | 检查 embedding / reranker 缓存状态。 |
| `/工程向量检查` | `longform-engine vector-store verify project.yaml` | `project.yaml` | 只读 | 检查向量后端配置。 |
| `/工程向量重建` | `longform-engine vector-store rebuild project.yaml` | `project.yaml` | 向量派生索引 | 从 embedding 文件事实重建向量索引。 |
| `/工程RAG规模验证` | `longform-engine benchmark rag-scale-run project.yaml --scale-chapters 500 --backend local_hnsw` | `--scale-chapters` | `70_runtime/benchmarks/` | 运行固定工程数据集；结果不可替代文学质量证据。 |
| `/工程构建RAG` | `longform-engine rag build project.yaml` | `project.yaml` | `60_rag/chunks/`、SQLite | 从 final 正文构建 RAG chunk。 |
| `/工程语义构建RAG` | `longform-engine rag build project.yaml --with-embeddings` | `project.yaml` | `60_rag/`、`70_runtime/models/` | 构建含 embedding 的 RAG 产物，必要时自动下载默认模型。 |
| `/工程检索` | `longform-engine rag query project.yaml "query"` | `query` | 只读或 query cache | 查询本地 RAG。 |
| `/工程语义检索` | `longform-engine rag query project.yaml "query" --semantic` | `query` | 只读或 query cache | 使用语义召回/重排查询 RAG。 |
| `/工程上下文` | `longform-engine rag context project.yaml --chapter N` | `--chapter N` | `60_rag/context/` | 写入下一章上下文。 |
| `/工程语义上下文` | `longform-engine rag context project.yaml --chapter N --semantic` | `--chapter N` | `60_rag/context/` | 写入语义增强上下文。 |
| `/工程记忆检查` | `longform-engine memory validate project.yaml` | `project.yaml` | 只读 | 校验长期记忆文件。 |
| `/工程TCS` | `longform-engine memory tcs project.yaml --chapter N` | `--chapter N` | `30_state/tcs/` | 生成 Temporal Context State 快照。 |
| `/工程TCS推进` | `longform-engine memory tcs-transition project.yaml --chapter N` | `--chapter N` | `30_state/tcs/` | 根据定稿章节推进 TCS。 |
| `/工程TCS校验` | `longform-engine memory tcs-validate project.yaml --chapter N` | `--chapter N` | 只读 | 检查未来事实泄漏和状态一致性。 |
| `/工程记忆压缩` | `longform-engine memory compress project.yaml --scope arc --from-chapter A --to-chapter B` | `--scope`、`--from-chapter`、`--to-chapter` | `60_rag/memory/` | 压缩 scene/chapter/arc 记忆。 |
| `/工程角色检查` | `longform-engine memory character-check project.yaml --chapter N --file draft.md` | `--chapter N`、`--file` | 只读 | 对照角色记忆检查草稿。 |
| `/工程图谱校验` | `longform-engine graph validate project.yaml` | `project.yaml` | 只读 | 校验 `story_graph.json`。 |
| `/工程图谱更新` | `longform-engine graph update project.yaml --chapter N` | `--chapter N` | `30_state/story_graph.json`、SQLite | 从定稿章节更新图谱。 |
| `/工程图谱检查` | `longform-engine graph check project.yaml` | `project.yaml` | `50_workbench/graph_reports/` | 写入图谱冲突报告。 |
| `/工程图谱检索` | `longform-engine graph retrieve project.yaml --query "query" --chapter N --json` | `--query`、`--chapter N` | 只读 | 执行图谱遍历检索。 |

新生产链只使用 `chapter semantic-*` 一次抽取并统一物化，不再提供 graph、memory 或 character-memory 的独立 Agent 抽取任务。

## 产物归档

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程产物状态` | `longform-engine artifacts status project.yaml` | `project.yaml` | 只读 | 统计 loose files、审计 ZIP 和遗留成功快照。 |
| `/工程产物精简` | `longform-engine artifacts compact project.yaml --through N --dry-run` | `--through N` | dry-run 只读；显式去掉 `--dry-run` 后写审计 ZIP | 先预览后归档；final、语义账本、计划账本和当前状态视图永不归档。 |
| `/工程产物校验` | `longform-engine artifacts verify project.yaml` | `project.yaml` | 只读 | 校验 ZIP、manifest 与每个条目的 SHA-256。 |
| `/工程产物恢复` | `longform-engine artifacts restore project.yaml --chapter N` | `--chapter N` | 原工作路径 | 校验 hash 后恢复；拒绝覆盖内容不同的现有文件。 |

## 研究入库

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程添加资料` | `longform-engine research add project.yaml --file note.md` | `--file` | `50_workbench/research_inbox/` | 把本地资料加入研究 inbox。 |
| `/工程联网检索` | `longform-engine research search project.yaml "query"` | `query` | `50_workbench/research_inbox/` | 搜索外部资料并进入 inbox。 |
| `/工程资料缺口` | `longform-engine research gaps project.yaml --chapter N` | `--chapter N` | `50_workbench/research_inbox/` 或报告 | 检测章节或项目资料缺口。 |
| `/工程影响分析` | `longform-engine impact-analyze project.yaml --research-item research_id` | `--research-item` | `50_workbench/impact_reports/` | 入库前分析资料影响。 |
| `/工程回滚影响` | `longform-engine impact-analyze project.yaml --after-rollback` | `--after-rollback` | `50_workbench/impact_reports/` | 回滚后分析影响范围。 |
| `/工程入库` | `longform-engine research promote project.yaml --item research_id` | `--item` | `10_bible/research_canon.jsonl`、RAG/图谱派生 | 将审核后的资料提升为 canon。 |

## 修订回滚

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程重写分支` | `longform-engine revision branch project.yaml --chapter N` | `--chapter N` | `40_manuscript/rewrite/` | 创建章节重写候选分支。 |
| `/工程回滚` | `longform-engine revision rollback project.yaml --to-chapter N` | `--to-chapter N` | `40_manuscript/detached/`、stale 标记 | 回滚到指定章节并保留脱离稿。 |
| `/工程快照` | `longform-engine revision snapshot project.yaml --label label` | `--label` | `70_runtime/snapshots/` | 创建轻量项目快照。 |
| `/工程改纲` | `longform-engine revise-outline project.yaml --from-chapter N --change-description "..."` | `--from-chapter`、`--change-description` | `20_outline/`、stale 标记 | 重算大纲锚点并标记受影响产物。 |

## 创作与审稿

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程创作简报` | `longform-engine creative brief project.yaml --init` | `project.yaml` | `10_bible/creative_brief.json` | 初始化创作简报。 |
| `/工程校验创作简报` | `longform-engine creative brief project.yaml --validate` | `project.yaml` | 只读 | 校验创作简报。 |
| `/工程风格档案` | `longform-engine creative style-profile project.yaml --genre "..." --target-audience "..."` | `--genre`、`--target-audience` | `10_bible/style_profiles/` | 写入题材风格矩阵。 |
| `/工程润色任务` | `longform-engine creative humanize-task project.yaml --chapter N --source draft` | `--chapter N`、`--source` | `50_workbench/humanizer_tasks/` | 生成 Humanizer 任务。 |
| `/工程润色检查` | `longform-engine creative humanize-check project.yaml --chapter N --file ...` | `--chapter N`、`--file` | `50_workbench/gate_artifacts/` | 检查润色候选稿。 |
| `/工程润色语义审稿` | `longform-engine creative humanize-semantic-task project.yaml --chapter N` | `--chapter N`、可选 `--file` | `50_workbench/humanizer_tasks/` | 生成来源稿与润色候选的独立语义保真审稿任务。 |
| `/工程校验润色语义` | `longform-engine creative humanize-semantic-validate project.yaml --chapter N --file ...` | `--chapter N`、`--file` | `50_workbench/humanizer_tasks/` | 校验双侧 hash/span、事实维度、人物声音和阻断 finding；通过后仍需 `draft submit`。 |
| `/工程收益审稿` | `longform-engine quality payoff-task project.yaml --chapter N` | `--chapter N` | `50_workbench/quality_reviews/` | 在 gate 通过后生成读者收益、代价、承诺进度与章节结构观察工作单。 |
| `/工程校验收益` | `longform-engine quality payoff-validate project.yaml --chapter N --file ...` | `--chapter N`、`--file` | `50_workbench/quality_reviews/` | 校验当前 draft hash、计划字段、精确 span、伪兑现 finding 与结构重复；通过后仍需显式 finalize。 |
| `/工程反馈状态` | `longform-engine quality feedback-status project.yaml --chapter N` | 可选 `--chapter N` | `50_workbench/quality_feedback/` | 查看 active/carried/resolved/suppressed/expired，并按目标章节计算 TTL。 |
| `/工程解决反馈` | `longform-engine quality feedback-resolve project.yaml --id ... --evidence "..."` | `--id`、`--evidence` | `50_workbench/quality_feedback/registry.jsonl` | 用明确证据解决一条反馈，后续工作单不再携带。 |
| `/工程抑制反馈` | `longform-engine quality feedback-suppress project.yaml --id ... --evidence "..."` | `--id`、`--evidence` | `50_workbench/quality_feedback/registry.jsonl` | 明确抑制误报或不适用反馈；不改小说 canonical state。 |
| `/工程审稿` | `longform-engine editorial review project.yaml --chapter N` | `--chapter N` | `50_workbench/editorial_reviews/` | 生成单章审稿。 |
| `/工程批审` | `longform-engine editorial batch-review project.yaml --chapter-start A --chapter-end B` | `--chapter-start`、`--chapter-end` | `50_workbench/editorial_reviews/` | 生成章节范围审稿。 |
| `/工程审稿状态` | `longform-engine editorial status project.yaml` | `project.yaml` | 只读 | 查看审稿状态。 |
| `/工程人工审阅` | `longform-engine editorial need-human project.yaml --chapter N --reason "reason"` | `--chapter N`、`--reason` | `50_workbench/editorial_reviews/` | 标记需要人工审阅。 |

Editorial review contract:

- `editorial review` selects only risk-relevant roles and writes a separate manifest, work order, and context digest for each.
- `editorial_role_review_v2` records reviewer instance, Agent product/version, context digest, independence mode, round, and confidence; P0/P1 must cite exact current-chapter excerpts.
- Roles cannot read peer review results before submission; aggregate is the first stage allowed to compare normalized results.
- Aggregate preserves consensus, conflicts, evidence overlap, severity differences, minority P0/P1 findings, and human decisions.
- `editorial batch-review` writes pacing, logic, and AI taste health reports for the selected chapter range.
- `editorial need-human` records an escalation request only; it does not mutate final/RAG/graph/memory/TCS/SQLite.

## SQLite

| 中文指令 | CLI 命令 | 必填参数 | 写入边界 | 说明 |
| --- | --- | --- | --- | --- |
| `/工程初始化索引` | `longform-engine db init project.yaml` | `project.yaml` | `70_runtime/db/` | 创建 SQLite schema。 |
| `/工程同步索引` | `longform-engine db sync project.yaml` | `project.yaml` | `70_runtime/db/` | 从文件事实同步派生行。 |
| `/工程重建索引` | `longform-engine db rebuild project.yaml` | `project.yaml` | `70_runtime/db/` | 删除并重建 SQLite 索引。 |
| `/工程索引状态` | `longform-engine db status project.yaml` | `project.yaml` | 只读 | 查看 SQLite 索引状态。 |
| `/工程查询索引` | `longform-engine db query project.yaml table_name` | `table_name` | 只读 | 查询白名单 SQLite 表。 |

## `/工程续章` 写前引导

`/工程续章` 是续写章节的主入口，对应 `longform-engine continue-write project.yaml --chapter N`。它只生成或刷新 Agent 写作任务包，不直接写 final、RAG、story graph、memory、TCS 或 SQLite。不要把旧项目里的旧命令名作为用户主协议；中文工程命令保持为唯一主入口。

执行 `/工程续章` 前，Agent 必须完成以下预检：

1. 用户偏好：确认 Creative Brief 或任务包里的目标读者、文风、禁忌体验、自动化等级和最近用户要求。
2. 自动兜底：偏好缺失时使用任务包默认值，不要求用户提供额外 API key；只有章节号、项目路径等安全信息缺失时才询问。
3. 节奏预检：读取 `event_recommendation`、`constraint_packet.event_matrix`、最近 5 章节奏、柔和事件要求、快档配额和 gate history。
4. 章末钩子声明：读取 `writing_brief.chapter_hook`、`reverse_brake.must_keep_suspense`，写稿前明确本章最后要保留的压力、线索或反转。
5. 禁揭露确认：读取 `reverse_brake.forbidden_reveals`、`this_chapter_must_not_solve`、`allowed_reveal_level`，非终局章节不得完整解决核心秘密。
6. 失败兜底：上一章未 final、上一章 gate failed、stale indexes、人工暂停或任务包缺失时，停止续章并转入 `/工程修章`、`/工程验稿`、`/工程放行`、`/工程重写分支`、`/工程回滚` 或 rebuild 路径。

五步闭环：

1. `/工程续章` -> `continue-write` 生成 `50_workbench/writing_tasks/chNNN.md` 和 JSON。
2. Agent 只写 `50_workbench/agent_drafts/chNNN.codex.md` 或 `chNNN.claude.md`。
3. `/工程提交稿` -> `draft submit` 把候选稿送入受控 draft。
4. `/工程验稿` -> `gate-check` 检查节奏、反向刹车、风格、Humanizer、图谱、记忆和语义风险。
5. `/工程定稿` -> `chapter finalize` 只在 gate 通过或有效放行后写入正式正文、收益和结构观察；失败则修章、润色、审稿、放行、重写分支或回滚。
6. `/工程章节语义任务` -> Agent 对 final 做一次证据化统一抽取，CLI validate 后由用户显式 `/工程章节语义应用`。
7. `/工程关闭章节` -> 验证图谱、角色当前状态、伏笔、TCS 与派生索引完整后关闭；关闭前不得续写下一章。
