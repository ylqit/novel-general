# longform-novel-engine

面向 Codex App、Codex CLI 与 Claude Code：Agent 负责创作、修订与语义判断，CLI 负责任务编译、校验、canonical 写入与回滚。`longform-novel-engine = Python engine + Codex Skill + Claude Code Skill`

- 面向百万字中文长篇与数百章连续生产。
- 默认使用宿主产品当前会话，不要求 provider API key。
- 本地文件是事实源；SQLite、RAG 和图谱是受控或可重建派生状态。
- Agent 只能写 manifest 声明的 workbench 候选，不能直接写 canonical。

> 当前公开稳定版为 `v0.9.0`。这是不兼容旧项目的协议升级；发布不等于文学质量或平台接受证明。

v0.9 明确拒绝 v0.8 及更早项目，不做双读、自动迁移或字段别名。请新建 v0.9 项目，再人工导入经确认的 Bible、纲要和必要资料。

## 产品边界

本项目解决的是长篇生产协议、上下文一致性、人工修订证据和失败恢复，不自动证明文学优秀，也不承诺平台接受。

| 环节 | 当前实现 |
| --- | --- |
| 故事规划 | Story Engine、Promise Ledger、滚动纲要与因果模拟 |
| 章节合同 | `chapter_contract_v4` 统一绑定故事义务与结尾语义 |
| 人类写前意图 | 空白 `human_chapter_intent_v1` 绑定方向选择与合同 |
| 作者工作单 | `chapter_story_brief_v4` 只展示作者可执行的故事信息 |
| 编译依据 | `chapter_story_brief_basis_v2` 绑定合同、人类意图、事实、声音与历史 |
| 写作任务 | `chapter_writing_task_v6` 与活动 manifest 严格一致才可复用 |
| 对话共编 | `chapter_coedit_session_v1` 的方案、选择和完整候选只写 workbench |
| 独立审稿 | `scene_prose_editor`、`anti_template_editor` 每章必审，风险角色按需增加 |
| 人工终稿 | `human_author_revision_v3` 绑定最终锁、真实改动及双稿语义保真 |
| 人工深审 | `human_story_review_v6` 绑定八类当前证据后才允许 finalize |
| 发布预检 | 起点、番茄政策快照只提示风险，不输出“检测通过” |
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
  'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.9.0'
longform-engine skills install --tool codex --force
longform-engine doctor --tool codex
```

macOS / Linux：

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
python3 -m pipx install --force \
  'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.9.0'
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

`production next` 是默认入口。它会报告当前阻塞原因和唯一安全的下一步。

首次设计由 Agent 工单与 CLI 校验组成：

```bash
longform-engine intelligence task project.yaml --task-type book_ideation
longform-engine agent-task brief project.yaml TASK_ID
# Agent 写入工单声明的候选文件
longform-engine agent-task validate project.yaml TASK_ID --result-file FILE
longform-engine intelligence apply project.yaml --task-type book_ideation --candidate FILE
```

继续按 `production next` 完成 Book Design、纲要、人物表达和因果模拟。设计输出不会直接写 Bible；CLI 先验证，再原子物化 canonical 视图。

## 一章的完整闭环

```text
章节方向候选
→ 人工选择 option ID
→ 设计批准与语义编译
→ chapter_contract_v4
→ 空白表单完成人类章节意图
→ chapter_story_brief_basis_v2
→ chapter_story_brief_v4
→ AI 完整候选
→ 对话共编：2–3 个方案 → 人工选择 → 新完整候选
→ deterministic gate
→ scene_prose_editor + anti_template_editor
→ 必要 repair
→ 冻结 human_review_bundle_v2
→ 人类最终完整修订并锁定 + human_author_revision_v3
→ 双稿语义保真
→ draft submit --agent human --overwrite
→ 全量 gate 与独立复审
→ human_story_review_v6
→ accept
→ finalize
→ semantic apply / chapter close
```

准备作者工作单：

```bash
longform-engine chapter human-intent-task project.yaml --chapter 1
# 人工填写、validate 并 apply 当前意图记录
longform-engine continue-write project.yaml --chapter 1
longform-engine agent-task brief project.yaml TASK_ID
```

作者 Agent 只读：

```text
50_workbench/writing_tasks/ch001.md
```

作者只把完整小说正文写到工单声明的候选路径，然后提交：

```bash
longform-engine draft submit project.yaml \
  --chapter 1 \
  --file 50_workbench/agent_drafts/ch001.codex.md \
  --agent codex
longform-engine production next project.yaml
```

有 P0/P1 时必须先走不可变 repair plan；不得用 waiver、咨询或人工勾选绕过。

## Story Brief 一致性

`chapter_contract_v4` 绑定真正具有规范性的章节义务：

- 欲望、阻力、戏剧问题与本章职责；
- 关键失败、不可逆选择、转折、代价与读者收益；
- 情绪余波、结尾方式、结尾意图；
- 必须保护的悬念和允许兑现的标记；
- 场景链、保护结果、禁止偏移、承诺动作与因果模拟引用。

合同不吞入全部 RAG、历史正文或内部控制数据。

`chapter_story_brief_basis_v2` 另外绑定所有会改变作者工作单的编译依据：

- 当前 `human_chapter_intent_v1`；
- 当前合同与因果模拟；
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

每章必须提交当前候选对应的 `human_author_revision_v3`，并绑定人类意图、共编来源和最终锁。

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

`human_story_review_v6` 必须绑定当前八类证据：

1. 候选正文；
2. `chapter_contract_v4`；
3. `human_chapter_intent_v1`；
4. `chapter_story_brief_basis_v2`；
5. Promise Ledger；
6. 因果模拟；
7. `human_review_bundle_v2`；
8. `human_author_revision_v3` 与最终锁。

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
longform-engine recover status project.yaml
longform-engine production next project.yaml
```

不要手工修改 SQLite、派生 RAG 索引或聚合状态来绕过事实源。

## 起点与番茄边界

默认画像是起点男频主合同，番茄免费仅提供 P2 非阻断兼容观察。

跨平台共同核心由章节合同表达：卖点进入事件、人物推动因果、行动产生选择与代价、阶段性收益、关系变化、信息释放和长线兑现。

平台启发式不等于平台规则。前三章、收益频率、句长、对白率和尾钩强度都不得提升为违规门禁。

平台预检：

- 报告真实生产方式、人工修订覆盖和政策快照；
- 番茄映射公开的低质治理类别；
- 起点明确显示未发现可核验的公开全面 AI 禁令，内部判定未知；
- 只提示，不承诺通过，也不自动插入或删除披露标识。

政策快照超过 `next_review_at` 后显示 `policy_verification_required`。

## 质量状态

```bash
longform-engine quality status project.yaml --json
```

三项状态不能相互替代：

- `protocol_ready`：可执行生产协议结构有效；
- `author_acceptance_ready`：每个已定稿章节都有当前协议的可验证人工接受；
- `literary_evidence_ready`：独立盲评证据满足文学证据 manifest。

当前仓库没有满足要求的真实盲评 manifest，因此 `literary_evidence_ready=false`。自动测试、作者接受和工程协议就绪都不能改写这一结论。

## 开发与发布

源码开发、单进程测试、资源清单、构建、分发审计和隔离安装命令集中在 [Release Runbook](docs/RELEASE_RUNBOOK.md)。

v0.9.0 的发布清单保留实现阶段验证证据，并单独记录本次经用户授权的无测试发布例外。tag 发布工作流只构建和上传制品，不把 CI 或 smoke 结果解释为发布质量证明。

活动发布面必须通过版本与 schema 守卫；历史 release checklist 保留原文，不批量改写。

## 文档

- [Architecture](docs/ARCHITECTURE.md)
- [Storage Model](docs/STORAGE_MODEL.md)
- [Configuration](docs/CONFIGURATION.md)
- [Quality Benchmark Runbook](docs/QUALITY_BENCHMARK_RUNBOOK.md)
- [Release Runbook](docs/RELEASE_RUNBOOK.md)
- [Release History](docs/RELEASE_HISTORY.md)
- [v0.9.0 发布 Checklist](docs/V0_9_0_RELEASE_CHECKLIST.md)

## License

[MIT](LICENSE)
