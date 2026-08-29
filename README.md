# longform-novel-engine

面向 Codex App、Codex CLI 与 Claude Code：Agent 负责创作、修订与语义判断，CLI 负责任务编译、校验、canonical 写入与回滚。`longform-novel-engine = Python engine + Codex Skill + Claude Code Skill`

- 面向百万字中文长篇与数百章连续生产。
- 默认使用宿主产品当前会话，不要求 provider API key。
- 本地文件是事实源；SQLite、RAG 和图谱是受控或可重建派生状态。
- Agent 只能写 manifest 声明的 workbench 候选，不能直接写 canonical。

> 当前公开稳定版为 `v0.13.0`。它在 v0.12 语义优先底座上完成国内平台同人长篇生产架构：三类路线、章节上下文 v2、跨界拓扑、起点主档/番茄兼容档，以及仅约束具体平台导出的人工权利决定。发布不等于文学质量、平台接受、取得原著授权、原著行为百分之百还原或 AI 检测规避证明。

当前公共语义边界是 `artifact_envelope_v1`、`source_asset_v1`、`evidence_reference_v1`、`workflow_record_v1`、`semantic_document_v1`、`human_decision_v1` 与 `agent_task_manifest_v5`。资料索引和格式处理记录仍可使用确定性内部结构，但不再为人物、事件、关系、能力、外观或跨界规则建立封闭内容 Schema。`fanfiction_source_canon_v1/v2/v3` 不双读；旧项目只能显式审计并非原地导入，旧事实必须重新形成证据可回溯的语义候选并获人工批准。

## 产品边界

本项目解决的是长篇生产协议、上下文一致性、人工修订证据和失败恢复，不自动证明文学优秀，也不承诺平台接受。

| 环节 | 当前实现 |
| --- | --- |
| 故事规划 | 全书 Spine、分卷骨架、活动卷、20 章滚动窗口与 firm 3 章合同 |
| 章节合同 | `chapter_contract_v5` 绑定拓扑、语义义务、批准节点与承诺动作 |
| 人类写前意图 | 空白 `human_chapter_intent_v2` 绑定 v5 合同与完整节点审批 |
| 作者工作单 | `chapter_story_brief_v5` 只展示作者可执行的故事信息 |
| 编译依据 | `chapter_story_brief_basis_v3` 绑定合同、节点、义务、事实、声音与历史 |
| 写作任务 | `chapter_writing_task_v7` 与活动 manifest 严格一致才可复用 |
| 对话共编 | `chapter_coedit_session_v2` 的方案、选择和完整候选只写 workbench |
| 独立审稿 | `scene_prose_editor`、`anti_template_editor` 每章必审，风险角色按需增加 |
| 人工终稿 | `human_author_revision_v4` 绑定最终锁、真实改动及双稿语义保真 |
| 人工深审 | `human_story_review_v7` 绑定当前协议证据后才允许 finalize |
| 发布预检 | 内容观察只提示风险；同人平台导出要求目标级人工权利决定 |
| 同人资料 | 用户级中文原著证据库、项目固定哈希绑定、需求驱动分层覆盖与项目独立语义 Canon |
| 恢复 | canonical 写入使用事务、锁、证据和显式恢复命令 |

## 两套 Skill

| Skill | 宿主 | Agent 允许写入 |
| --- | --- | --- |
| `longform-novel-codex` | Codex App / CLI | manifest 声明的 `50_workbench/` 候选 |
| `longform-novel-claude` | Claude Code | manifest 声明的 `50_workbench/` 候选 |

两套 Skill 都自带 `references/`。仓库内 `shared/` 是引用事实源；发布前同步到两套副本。

## 安装稳定版

仓库地址：<https://github.com/ylqit/novel-general>

Windows：

```powershell
py -3 -m pip install --user pipx
py -3 -m pipx ensurepath
py -3 -m pipx install --force `
  'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.13.0'
longform-engine skills install --tool codex --force
longform-engine doctor --tool codex
```

macOS / Linux：

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
python3 -m pipx install --force \
  'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.13.0'
longform-engine skills install --tool codex --force
longform-engine doctor --tool codex
```

Claude Code 用户把 `--tool codex` 换成 `--tool claude-code`。

安装或升级 Skill 后重启宿主会话，让新协议生效。

## 快速开始

创建项目：

```bash
longform-engine init --template qidian-longform --output my-novel
cd my-novel
longform-engine open-book project.yaml
longform-engine production next project.yaml
```

每轮始终先运行 `production next`。它会报告当前阻塞原因和唯一安全的下一步。

从开书到投稿的逐步命令、动态占位符和失败恢复见 [Operator Guide](docs/OPERATOR_GUIDE.md)。

首次设计由 Agent 工单与 CLI 校验组成：

```bash
longform-engine intelligence task project.yaml --task-type book_ideation
longform-engine agent-task brief project.yaml TASK_ID
# Agent 写入工单声明的候选文件
longform-engine agent-task validate project.yaml TASK_ID --result-file FILE
longform-engine intelligence apply project.yaml --task-type book_ideation --candidate FILE
```

继续按 `production next` 完成 Book Design、纲要、人物表达和因果模拟。设计输出不会直接写 Bible；CLI 先验证，再原子物化 canonical 视图。

### 同人项目资料启动

同人模式不会把原著全文复制进小说项目。完整原件只进入当前用户的共享 `原著资料库/`；项目内的 `50_workbench/同人原著资料/` 只保存固定 asset/bundle/规范化/提取 hash、批准提取、短证据和动态覆盖需求。每部 crossover 原著独立管理版本、截止点和覆盖：`design_core` 未满足时不能批准正式路线，当前 `chapter_dependency` 未满足时只阻断依赖它的章节；`whole_to_cutoff` 全作覆盖是人工可选模式。正式项目原著基线使用人工批准的 `semantic_document_v1`。

```bash
longform-engine source-library init
longform-engine source-library work-register --name 作品名 --creator 作者 --version 版本 --approved-by human
longform-engine source-library item-import --work-id WORK_ID --name 资料名 --source-type 小说卷册 --version 版本 --unit-range 范围 --source-method 用户本地导入 --rights-status user_claimed_authorized --retention-mode full_text --file FILE --approved-by human
longform-engine source-library extraction-template --item-id ITEM_ID
longform-engine source-library extraction-approve --item-id ITEM_ID --file EXTRACTION.json --approved-by human
longform-engine fanfiction pack-init project.yaml
longform-engine fanfiction item-bind project.yaml --source-id SOURCE --item-id ITEM_ID --approved-by human
longform-engine fanfiction coverage-apply project.yaml --source-id SOURCE --file 全作覆盖计划.yaml --approved-by human
longform-engine fanfiction canon-task project.yaml
longform-engine fanfiction story-engine-task project.yaml
longform-engine fanfiction story-engine-validate project.yaml --file STORY_ENGINE.json
longform-engine fanfiction story-engine-apply project.yaml --file STORY_ENGINE.json --approved-by human
longform-engine fanfiction design-task project.yaml
longform-engine fanfiction design-validate project.yaml --file ROUTE.json
longform-engine fanfiction design-review-task project.yaml --file ROUTE.json
longform-engine fanfiction design-review-validate project.yaml --file REVIEW.json
longform-engine fanfiction design-apply project.yaml --file ROUTE.json --review REVIEW.json --approved-by human
longform-engine fanfiction context-status project.yaml --chapter N --json
```

故事发动机先选择 `oc_si_progression | canon_character_centered | hybrid`，确认主角与原著关系、首卷读者识别承诺、移除原著既有事件后仍能运转的原创主线，并继续固定唯一初始变量、独立长期目标、持续阻力、原著人物自主性和原作事件结束后的故事来源。路线再明确故事切入点、人物知识边界、未来知识退化、原著事件命运、职责承担者及一阶/二阶影响，并由隔离会话独立复核。每项获批重大分歧按稳定触发身份独立、幂等地产生未来知识重估，输出“仍可靠、部分可靠、已失效、反向误导”的适用范围。

跨作品路线显式选择 `fixed_host | fusion_world | sequential_worlds`。适配需求由实际 `transfers[].payload_kinds` 产生，通过“来源适配器 → 项目跨界宪法 → 当前卷例外”解释能量、身体/灵魂、能力作用对象、成本、反制、身份、组织、死亡与不可逆后果；顺序诸天按卷编译并保留跨卷关系、债务、身体状态或总目标进度，不预建 N×N 世界矩阵。章节上下文按稳定 claim 依赖和 Token 预算编译，不使用姓名匹配或“无命中取前几条”；必需事实超出预算时明确阻断。用户提供的同人案例与技法只进入仓库 Prompt、质量规则、抽象夹具和文档，不进入项目数据、Canon、RAG 或图谱。

原创、灵感原创或改编研究仅提及作品名时，只能先创建 `research external-request`；人工批准前不会联网，也不会写入全局资料库。实际使用原著人物、世界或事件会要求把项目改为同人模式。普通网页不得被搜索结果拼接为小说、字幕或剧本全文。
写作时若 `chapter_contract_v5` 引用了尚未进入项目 Canon 的原著 ID，`continue-write` 会先创建 `fanfiction_incremental_source_request_v1`（`network_performed=false`）并阻断。本次缺口必须依次人工批准、逐项搜索/导入、项目绑定和人工核销；模型记忆不能替代证据。

## 一章的完整闭环

```bash
longform-engine production next project.yaml
# 完成章节方向、人工选择及设计语义编译
longform-engine chapter human-intent-task project.yaml --chapter N
# 人工填写空白 human_chapter_intent_v2 表单
longform-engine chapter human-intent-validate project.yaml --chapter N --file INTENT_FILE
longform-engine chapter human-intent-apply project.yaml --chapter N --file INTENT_FILE --approved-by human
longform-engine continue-write project.yaml --chapter N
longform-engine agent-task brief project.yaml TASK_OR_PATH
# Agent 只读 chapter_story_brief_v5，只写 manifest 的 io.output.path
longform-engine draft submit project.yaml --chapter N --file AGENT_CANDIDATE --agent codex
# 按 production next 完成独立审稿及必要 repair
longform-engine chapter human-revision-task project.yaml --chapter N
# 人工完成全文改稿、记录和独立双稿语义复核，再以 agent=human 提交
longform-engine chapter human-review-task project.yaml --chapter N
# validate / apply 当前 v6 决定；accept 后才可 finalize
longform-engine chapter finalize project.yaml --chapter N --approved-by human
longform-engine chapter semantic-task project.yaml --chapter N
# Agent 输出 canonical_delta_v1
longform-engine chapter semantic-validate project.yaml --chapter N --file SEMANTIC_FILE
longform-engine chapter semantic-apply project.yaml --chapter N --file SEMANTIC_FILE
longform-engine chapter close project.yaml --chapter N --approved-by human
longform-engine production next project.yaml
```

`TASK_OR_PATH`、候选路径、session、turn 和 hash 必须取自当前 CLI 输出，不手工猜测。作者只读 `50_workbench/writing_tasks/chNNN.md`；有 P0/P1 时必须先走不可变 repair plan，不得用咨询或人工勾选绕过。

## Story Brief 一致性

`chapter_contract_v5` 绑定真正具有规范性的章节义务：

- 欲望、阻力、戏剧问题与本章职责；
- 关键失败、不可逆选择、转折、代价与读者收益；
- 情绪余波、结尾方式、结尾意图；
- 必须保护的悬念和允许兑现的标记；
- 场景链、保护结果、禁止偏移、承诺动作与因果模拟引用。

合同不吞入全部 RAG、历史正文或内部控制数据。

`chapter_story_brief_basis_v3` 另外绑定所有会改变作者工作单的编译依据：

- 当前 `human_chapter_intent_v2`；
- 当前合同、滚动窗口、批准节点表与语义义务；
- 筛选后的 canonical / RAG 事实；
- 本章人物声音与人工批准的作者声音样本；
- 最近结构和载体历史；
- 有效质量合同与 renderer 版本。

合同义务变化必须同时改变合同 hash 和 basis。人物声音、事实投影或结构历史变化可以保持合同 hash 不变，但必须改变 basis，并让旧任务、咨询、审稿和接受记录 stale。

只有任务状态、合同、basis、Markdown 字节和活动 manifest 全部一致，旧写作任务才可复用。

## 作者工作单的信息边界

Story Brief 只提供当前章节可以直接使用的信息：

- 人物欲望、阻力、行动、选择和代价；
- 场景离场状态、读者收益与情绪余波；
- 必要的人物感知、决策、说话方式与情绪泄漏；
- 与本章直接相关的事实、能力边界和关系阶段；
- 受保护结果、禁止偏移和有限创作自由。

Story Brief 不暴露：

- hash、内部 ID、finding code 或任务控制字段；
- 原始 RAG 包、Graph、SQLite、TCS 和完整历史正文；
- 禁词表、检测词典、平台算法猜测或模型 Prompt 日志。

## 人类作者修订

AI 候选不能直接定稿。共编中的圈选、方案与选择只产生完整 workbench 候选，不能写 draft、final 或 canonical。已有 P0/P1 时只能使用当前 repair plan；人工终稿锁定后，AI 只能只读咨询。

每章必须提交当前候选对应的 `human_author_revision_v4`，并绑定人类意图、共编来源和最终锁。

人工记录至少证明两个真实影响维度，其中至少一个属于场景因果或人物声音/情绪。每项包含精确修改前后 span、`intent_ref`、预期读者影响、修改意图和必须保护项。

以下内容不能独立满足人工修订门禁：

- 只查看或勾选；
- 只改空白、排版或标点；
- 自动生成相同的通过理由；
- 把 AI 建议直接当成人工决定。

人工修订不得改变章节合同、知识边界、能力代价、关系阶段和保护结果。需要改变时必须 redirect 到章节方向或纲要。

双稿由独立 `prose_revision_semantic_review` 验证保真。人工终稿后的任何 Agent、`prose_naturalness` 或 repair 正文变换都会让旧记录 stale，并退回共编与全量复审阶段。

## 去 AI 味与低质治理

本项目不实现 AI 概率、检测器评分、规避检测或“检测通过”声明。

确定性 gate 只阻断可直接证明的问题，例如：

- 空正文；
- Prompt、任务或创作说明残留；
- 大段精确重复；
- 明确格式损坏。

常见词、句长、对白率、感官密度、慢章、无悬崖结尾和多个情节词只能作为 P2 定位信号，不能单独产生 P1。

模板化 P1 必须由 `anti_template_editor` 引用至少两个精确 span，说明重复的叙事功能、对读者的损害以及必须保护的内容。

细节是否有效取决于它是否改变感知、判断、行动或关系，不使用感官配额、对白配额或 A/B/C 关键词配额。

## 人工深审

`human_story_review_v7` 必须绑定当前协议证据：

1. 候选正文；
2. `chapter_contract_v5`；
3. `human_chapter_intent_v2`；
4. `chapter_story_brief_basis_v3`；
5. Promise Ledger；
6. 批准剧情节点与语义义务；
7. `human_review_bundle_v2`；
8. `human_author_revision_v4` 与最终锁。

人类强制确认三组核心证据：关键转折、人物选择/情绪、读者收益/离场状态。其他维度显示独立审稿覆盖与当前风险；存在 finding 时必须 repair、接受 P2 或 redirect。

审稿台不能自动填写人工理由。咨询只读，不能修改正文、批准章节或写入 final；候选或 basis 变化后立即 stale。

## Canonical 边界

canonical 主要包括：

```text
10_bible/
20_outline/
30_state/
40_manuscript/final/
project.yaml
```

Agent 不直接写这些路径。候选、任务、diff、审稿和咨询位于 `50_workbench/`。

`draft submit` 只能把经过相应协议验证的完整候选提交到 draft；`chapter finalize` 只能消费当前 gate、独立审稿、人工修订和人工接受证据。定稿后的统一语义任务输出 `canonical_delta_v1`，再由 CLI 显式应用。

canonical 写入使用项目锁和事务。崩溃后先运行：

```bash
longform-engine recovery status project.yaml
longform-engine production next project.yaml
```

不要手工修改 SQLite、派生 RAG 索引或聚合状态来绕过事实源。

## 起点与番茄边界

共享的 `cn_longform_fanfiction` 合同之上有两个独立 P2 质量层：起点男频同人层检查长主线、卷级增长、持续阻力、原著人物价值和原作事件耗尽后的故事来源；番茄免费同人层检查移动阅读清晰度、较快兑现、反填充、反粗制批量结构和章节可读性。两者都不编造字数、更新量、签约、推荐或付费阈值。

跨平台共同核心由章节合同表达：卖点进入事件、人物推动因果、行动产生选择与代价、阶段性收益、关系变化、信息释放和长线兑现。

平台启发式不等于平台规则。前三章、收益频率、句长、对白率和尾钩强度都不得提升为违规门禁。

平台预检把每条官方事实拆为“分类存在、投稿资格、签约资格、特定激励、内容治理、权利风险或披露要求”，保存适用范围、状态、来源、验证日期、下次复核日期和未知项。分类或个别作品存在不能推导签约、激励或推荐规律。

```bash
longform-engine publication preflight project.yaml --target qidian_male --json
longform-engine publication rights-decision project.yaml --target qidian_male --decision proceed --approved-by HUMAN --note "已理解具体风险"
longform-engine publication export project.yaml --target qidian_male
```

内容质量观察仍然只提示真实生产方式、人工修订覆盖、政策快照与公开低质治理类别；起点的公开全面 AI 禁令状态保持“未发现可核验来源/内部未知”。系统不承诺审核通过，也不自动插入或删除披露标识。

同人项目的普通写作、审阅、Canon、人工修订和定稿不受权利门禁。只有具体平台发布包导出要求当前 `fanfiction_publication_rights_decision_v1=proceed`，且决定仍绑定当前有效配置、来源 Canon、逐来源权利声明和目标平台政策快照；决定缺失、`hold`、任一 hash 变化或政策超过 `next_review_at` 都只阻断该目标导出。该决定是风险知情与流程责任确认，不是法律意见、授权或平台接受保证；原创模式不触发同人权利门禁。

## 质量状态

```bash
longform-engine quality status project.yaml --json
```

三项状态不能相互替代：

- `protocol_ready`：可执行生产协议结构有效；
- `author_acceptance_ready`：每个已定稿章节都有当前协议的可验证人工接受；
- `literary_evidence_ready`：独立盲评证据满足文学证据 manifest。

当前仓库没有满足要求的真实盲评 manifest，因此 `literary_evidence_ready=false`。自动测试、作者接受和工程协议就绪都不能改写这一结论。

同人文学验收另提供两条各 20 章的盲审协议：一条 OC/SI 成长线，一条原著角色改命/续写线；每条先绑定无 P1、无不可追溯 Canon 断言、无连续原文复现、无伪造授权的 hash-only gate report，再由三名未参与生成的独立人类评审匿名评分。保真、自主性、原创主线和持续阅读欲望的三人中位数须不低于 4/5，其余维度不得低于 3.5/5；实质分歧必须逐项人工记录，系统只保留中位数，不选择有利意见。没有真实 40 章材料与三人审阅时仍保持 `false`；命令顺序见 [Quality Benchmark Runbook](docs/QUALITY_BENCHMARK_RUNBOOK.md)。

## 开发与发布

源码开发、单进程测试、资源清单、构建、分发审计和隔离安装命令集中在 [Release Runbook](docs/RELEASE_RUNBOOK.md)。

v0.11.0 发布包含动态原著资料库、全作覆盖门禁、项目独立 Canon、升级提案与中文操作入口；tag 工作流构建 wheel、sdist 和 `SHA256SUMS`。

活动发布面必须通过版本与 schema 守卫；历史 release checklist 保留原文，不批量改写。

## 文档

- [Operator Guide](docs/OPERATOR_GUIDE.md)
- [v0.11 动态同人原著资料库](docs/V0_11_0_IMPLEMENTATION.md)
- [v0.13 国内平台同人架构](docs/V0_13_FANFICTION_ARCHITECTURE.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Storage Model](docs/STORAGE_MODEL.md)
- [Configuration](docs/CONFIGURATION.md)
- [Quality Benchmark Runbook](docs/QUALITY_BENCHMARK_RUNBOOK.md)
- [Release Runbook](docs/RELEASE_RUNBOOK.md)
- [Release History](docs/RELEASE_HISTORY.md)
- [v0.11.0 发布 Checklist](docs/V0_11_0_RELEASE_CHECKLIST.md)
- [v0.13.0 发布 Checklist](docs/V0_13_0_RELEASE_CHECKLIST.md)

## License

[MIT](LICENSE)
