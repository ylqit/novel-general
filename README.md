# longform-novel-engine

面向 Codex App、Codex CLI 与 Claude Code 的中文长篇小说生产引擎和平台专用 Skill。Agent 负责写作、修章、润色与语义判断，CLI 负责任务包、严格校验、显式 apply/finalize、事务、索引与回滚。

`longform-novel-engine = Python engine + Codex skill + Claude Code skill`

- 面向百万字中文长篇小说与数百章连续生产。
- 默认 `agent_skill` / No API key：使用宿主产品当前会话，不要求 OpenAI、Anthropic 或 provider API key。
- 本地文件是事实源；SQLite、RAG 和图谱是受控写入或可重建派生状态。
- 每个 Agent 工单声明输入文件、允许输出、schema、validate、apply、失败命令与硬边界。

> 当前稳定版为 `v0.4.4`，将 research promote 与 revision rollback 纳入 transaction v3，统一每章方向合同，并收敛 readiness 与语义物化说明。发布门禁见 [v0.4.4 Release Checklist](docs/V0_4_4_RELEASE_CHECKLIST.md)。

当前实现边界见 [Architecture](docs/ARCHITECTURE.md)，落盘、transaction v3 与崩溃恢复合同见 [Storage Model](docs/STORAGE_MODEL.md)。

## Skills

| Skill | 宿主 | Agent 允许写入 |
| --- | --- | --- |
| `longform-novel-codex` | Codex App / Codex CLI | `50_workbench/agent_drafts/chNNN.codex.md` 或 manifest 声明路径 |
| `longform-novel-claude` | Claude Code | `50_workbench/agent_drafts/chNNN.claude.md` 或 manifest 声明路径 |

两个 Skill 都是自包含包，各自带有 `references/`；公开安装不再创建一个容易与其他项目冲突的全局 `shared/` Skill。

## 核心能力

| 环节 | Agent 与 CLI 分工 |
| --- | --- |
| 章节写作 | CLI 生成任务包，Agent 写正文，CLI submit/gate/finalize |
| 修章 | CLI 生成 repair task，Agent 写候选稿，CLI 重新提交与门禁 |
| Humanizer | CLI 生成润色任务，Agent 写候选；风险改写由独立 Agent 做双稿语义保真审稿，CLI 校验后才能 submit |
| 图谱与记忆 | Agent 对 final 输出一次 `canonical_delta_v1`，CLI validate 后由 `chapter semantic-apply` 统一物化 |
| 编辑团队 | CLI 按风险选角色并隔离上下文；Agent 独立审稿，aggregate 保留分歧和少数派 P0/P1 |
| 节奏审查 | Agent 做语义判断，CLI 固化报告与阻断结果 |
| 项目级智能任务 | 开书、纲要、改纲、研究、风格与改编分析均走候选/校验/显式 apply |
| 人物表现与场景化叙事 | Book Design v2 建立人物表达合同；每章编译最小人物包，人物编辑按证据检查声音可交换性、工具人化和旁白代讲 |
| 同人创作 | 支持原角色、关系、世界观、力量体系、续写、前传、AU、分歧与 crossover |
| 中文网文质量 | Humanizer v4 词面检查与语义保真审稿、动态章节职责、读者收益/代价、兑现账本与里程碑语义审稿 |

## 安装

唯一发布源是 [ylqit/novel-general](https://github.com/ylqit/novel-general)。公开安装使用 pipx，把 engine 放入隔离环境，再由 engine 原子复制对应 Skill。无需先 clone 仓库，也不执行远程 `curl | shell`。

> 当前公开稳定版为 `v0.4.4`。以下命令固定到不可变 tag。

`v0.4.4` 不兼容历史项目协议，也不提供自动迁移；请使用新建项目或由人工审核后重新导入权威资料。当前 `literary_evidence_ready=false`。

### 让 Agent 安装

复制到 Codex：

```text
请从 https://github.com/ylqit/novel-general 安装 longform-novel-engine v0.4.4。使用 pipx 安装 longform-novel-engine[semantic]，不要 clone 临时源码或 editable install；然后运行 longform-engine skills install --tool codex --force 和 longform-engine doctor --tool codex。普通 Agent-Skill 写作不需要 OpenAI、Anthropic 或 provider API key。完成后提醒我重启 Codex 会话，并从 /工程下一步 开始。
```

复制到 Claude Code：

```text
请从 https://github.com/ylqit/novel-general 安装 longform-novel-engine v0.4.4。使用 pipx 安装 longform-novel-engine[semantic]，不要 clone 临时源码或 editable install；然后运行 longform-engine skills install --tool claude-code --force 和 longform-engine doctor --tool claude-code。普通 Agent-Skill 写作不需要 OpenAI、Anthropic 或 provider API key。完成后提醒我重启 Claude Code 会话，并从 /工程下一步 开始。
```

### Windows PowerShell

```powershell
py -3 -m pip install --user --upgrade pipx
py -3 -m pipx ensurepath
$env:PIPX_BIN_DIR = if ($env:PIPX_BIN_DIR) { $env:PIPX_BIN_DIR } else { Join-Path $env:USERPROFILE ".local\bin" }
$env:PATH = "$env:PIPX_BIN_DIR;$env:PATH"
py -3 -m pipx install --force 'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.4.4'
longform-engine skills install --tool all --force
longform-engine doctor --tool all
```

只安装一个宿主时，把 `all` 改成 `codex` 或 `claude-code`。

Windows 如果自定义 `PIPX_HOME`，请使用较短的绝对路径。Semantic 完整依赖包含 Torch；把虚拟环境放在过深的源码子目录中可能触发 Windows `WinError 206`。README 的默认 pipx 用户目录不需要调整。

### macOS / Linux Bash

```bash
python3 -m pip install --user --upgrade pipx
python3 -m pipx ensurepath
export PIPX_BIN_DIR="${PIPX_BIN_DIR:-$HOME/.local/bin}"
export PATH="$PIPX_BIN_DIR:$PATH"
python3 -m pipx install --force 'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.4.4'
longform-engine skills install --tool all --force
longform-engine doctor --tool all
```

安装后重启 Codex / Claude Code 会话，让宿主刷新 Skill discovery。

### 升级与卸载

```powershell
py -3 -m pipx install --force 'longform-novel-engine[semantic] @ git+https://github.com/ylqit/novel-general.git@v0.4.4'
longform-engine skills update --tool all
longform-engine doctor --tool all
```

```powershell
longform-engine skills uninstall --tool all --yes
py -3 -m pipx uninstall longform-novel-engine
```

安装器只删除带合法 `.longform-install.json` 元数据的目录；任何不属于本引擎的目录都不会被自动删除。

## 首次使用

先验证模板并创建一本书：

```powershell
longform-engine --version
longform-engine validate-config --template qidian-longform
longform-engine open-book --interactive
```

之后每轮从同一条生产入口开始：

```text
/工程下一步
-> longform-engine production next project.yaml

/工程工单
-> longform-engine agent-task brief project.yaml TASK_OR_PATH
```

Agent 只读取工作单与 manifest `io.inputs`，只写 `io.output.path` 并遵守 `io.output.protocol`，然后运行 `commands.validate`。`commands.apply` 与 finalize 必须显式执行；`production loop --no-apply` 遇到 Agent、人工、apply 或 finalize 边界会暂停。

空白项目不会直接进入第一章。`production next` 会先要求完成下面的项目级闭环：

```text
open-book
-> book_ideation 每轮一个问题 -> Agent 给 2-3 个选项 -> human selection/apply
-> book_design 工作单 -> Agent 权威 Markdown -> human approve -> canonical delta 编译 -> explicit apply
-> outline_design 工作单 -> Agent 权威 Markdown -> human approve -> canonical delta 编译 -> explicit apply
-> 角色、全书故事弧、卷级字数预算、未来 20 章详细窗口和伏笔账本就绪
-> 第一章 writing 工作单
```

`book_ideation` 依次确认目标读者与阅读场景、核心卖点、世界规则、主角欲望与缺陷、长期矛盾、卷级升级、结局边界以及禁区。CLI 每轮只保存用户明确选择或明确提供的一项答案。缺少这些决定、稳定角色 ID、人物弧线、全书故事弧、卷级预算、首个详细窗口或伏笔窗口时，CLI 会阻止第一章任务生成。详细窗口只覆盖未来 20 章，剩余计划不足 8 章时才安排下一轮 `outline_extension`。项目级 apply 仍由人显式确认，Agent 不能直接写 Bible 或 outline。

典型章节闭环：

```text
/工程续章 -> /工程下一步 -> /工程工单
-> Agent 写 chNNN.codex.md 或 chNNN.claude.md
-> /工程提交稿 -> gate
-> /工程修章，或 gate 通过后执行 /工程收益审稿
-> /工程校验收益 -> 显式 /工程定稿
-> /工程章节语义任务 -> Agent 一次读取 final 并输出统一语义 JSON
-> /工程章节语义校验 -> 显式 /工程章节语义应用
-> /工程关闭章节 -> 下一章
```

`reader_gain` 只是章节卡中的计划。`balanced`/`strict` 流程会在定稿前创建
`reader_payoff_review`，要求 Agent 用当前 draft 的 hash 和精确 span 证明实际收益、代价与承诺进度。
CLI 通过后，`chapter finalize` 才在同一事务中写入 `reader_reward_entry_v2` 和
`30_state/quality/structure_history.jsonl`；失败或过期审稿不会污染正式状态。finalize 只确立正文证据，
不再用正文开头冒充摘要，也不提前更新图谱、角色记忆、伏笔、TCS、RAG 或 SQLite。定稿后由
Agent 以 `canonical_delta_v1` 一次提交全部证据化变化；CLI 校验后规范化为内部章节语义账本并事务化物化当前状态，`chapter close`
成功后才允许进入下一章。graph、memory 与 character-memory 不再各自重复读取正文，全部由统一章节语义 delta 物化。

## 创作模式与同人

`creation.mode` 支持四种边界不同的创作方式：

| 模式 | 用途 |
| --- | --- |
| `original` | 完全原创长篇 |
| `fanfiction` | 使用原作角色、关系、世界、能力与时间线创作续写、前传、AU、原作分歧或多作品联动 |
| `adaptation_study` | 拆解结构、节奏与技法，不保存或重构来源正文 |
| `inspired_original` | 借鉴抽象题材机制后全面原创，不保留可识别专名和具体剧情 |

同人是正式一等工作流，不再借用 `adaptation_analysis` 承担创作。项目配置示例：

```yaml
creation:
  mode: fanfiction
fanfiction:
  continuity_mode: canon_divergent
  sources:
    - source_id: work_a
      title: 作品名
      creator: 原作者
      canon_cutoff: 第一卷结束
      allowed_elements: [characters, relationships, world, abilities, timeline]
      rights_status: unverified
      commercial_intent: true
      platform_policy_url: ""
```

`continuity_mode` 可为 `canon_compliant`、`canon_divergent`、`alternate_universe`、`continuation`、`prequel` 或 `crossover`。权利状态是用户声明，可为 `user_claimed_authorized`、`public_domain_claimed`、`platform_permitted_claimed` 或 `unverified`。无论是否核验、是否商业使用，CLI 都只生成提示，不阻断 canon、设计、写作、定稿或导出。

同人开书顺序：

```text
open-book
-> fanfiction canon-task -> Agent 输出 fanfiction_source_canon_v1
-> canon-validate -> canon-apply --approved-by human
-> book_ideation 逐轮人工确认原创贡献与创作边界
-> fanfiction design-task -> Agent 输出 fanfiction_design_candidate_v1
-> design-validate -> design-apply --approved-by human
-> outline_design -> writing
```

常用命令：

```powershell
longform-engine fanfiction canon-task project.yaml --input 50_workbench/fanfiction_sources/source.txt
longform-engine fanfiction canon-validate project.yaml --file 50_workbench/intelligence_candidates/fanfiction_canon.project.candidate.json
longform-engine fanfiction canon-apply project.yaml --file 50_workbench/intelligence_candidates/fanfiction_canon.project.candidate.json --approved-by human
longform-engine fanfiction design-task project.yaml
longform-engine fanfiction status project.yaml
longform-engine publication report project.yaml
longform-engine publication export project.yaml
```

允许使用角色名、关系、招式名和世界术语，不会仅因与原作设定一致而判为 OOC。AU 与原作分歧项目检查的是“变化是否由声明的分歧点和后续因果支撑”。来源正文仍不能被整段搬运、拆到多个 JSON 字段重构或拼成章节；canon 只保存转述事实、来源 hash 和字符 span。

`publication_risk_report_v1` 会记录来源、用户声明、商业意图、来源混淆和 AI 辅助标识提醒，但 `blocking` 固定为 `false`，也不会向小说正文自动插入版权、授权或 AI 声明。引擎允许创作不等于对具体发布行为完成法律核验，参考[《著作权法》](https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html)与[同人案件说明](https://www.sdcourt.gov.cn/dyzy/372897/372899/44482953/index.html)。

## 写入边界

Agent 不得直接写入：

```text
10_bible/
20_outline/
10_bible/research_canon.jsonl
30_state/story_graph.json
30_state/semantic_ledger/
30_state/foreshadowing_state.json
30_state/tcs/
40_manuscript/final/
60_rag/
70_runtime/db/
```

Bible、outline、research canon、final、semantic ledger、RAG、graph、foreshadow state、TCS 与 SQLite 只能由 CLI 在候选产物通过 validate 后，通过显式 apply/finalize/close 和事务机制改变。无效输出、反馈摘要和 benchmark 记录不能进入 canonical 状态。SQLite 与向量库是可重建派生索引，不能覆盖 final 和证据化语义账本。语义与产物规则见 [章节语义知识库与产物精简](docs/SEMANTIC_KNOWLEDGE_AND_ARTIFACT_COMPACTION.md)。

## 中文网文质量与去 AI 味

去 AI 味不等于追逐检测器，也不等于把所有平台都改成短句、高对白和悬崖结尾。引擎从 wheel 内置资源按
`事实边界 -> 市场 -> 主世界类型 -> 剧情引擎 -> 叙事形式 -> 前提装置 -> 关系重点 -> 当前故事弧 -> 人工风格 -> 项目覆盖`
编译质量合同。玄幻、游戏异界、都市属于世界分面；成长、求生、调查、言情属于剧情引擎；轻小说、群像、多视角属于叙事形式；穿越、重生、系统属于前提装置；同人则由 `creation.mode` 独立表达。冲突不会按数组顺序静默覆盖，必须由人明确解决。

- Humanizer v4 检查空文本、重复模板、信息轰炸、流水账升级、纸片人/工具人、对白同质、伪细节、情绪标签、意义膨胀和强制钩子。
- Humanizer v4 的第二遍改写读取 `character_expression_packet_v1`，保护人物的感知偏向、决策偏向、话语层级、社交面具和情绪泄漏，并强化相反欲望、隐藏议程、不可逆行动和情绪余波。
- 经批准的设计 Markdown 必须先编译为 `canonical_delta_v1`，再由 CLI 原子物化人物与关系视图；v0.4.4 不接收旧 book design 项目协议，也不允许 Agent 直接写 Bible。
- v0.4.4 工作区使用自适应上下文预算：`compact/standard/large` 分别提供 24K/48K/96K engine-controlled units，也可由项目覆盖。字符数和文件数只作诊断；CLI 依次去重、移除未触发参考、按需加载证据并拆分范围上下文，只有核心事实仍无法容纳时才进入 `need-human`。
- 每章作者使用新的章节会话；repair 可继续该章作者会话；Humanizer、连贯/人物/节奏/收益/同人审稿和 final 语义档案使用独立新会话。CLI 只给出会话要求和第一条命令，不创建子进程，也不把聊天历史当长期状态。
- 28 个任务角色、25 类任务、4 类输出协议与 12 个中文写作 Playbook 按区段渐进加载；其中 `repair_coordinator` 只编排已验证 finding 的根因、顺序、最小修复半径和保护项。44 个故事分面均有独立中文适配器，每轮最多激活三个。当前只能称为“高级专业候选 Prompt”，真实混合题材人工盲评完成前不作文学等级承诺。
- 第 1-3 章、人物初登场、POV 切换、关系转折和对白同质复发会选择 `character_editor`；即使 pass，也必须给每个 featured character 提供正文证据。
- 润色候选会与来源稿比较数字事实、角色保留和改写比例；过度重写会触发 `need-human`，避免“去 AI 味”把剧情和人物一起洗掉。
- Humanizer v4 Phase 1 在风险改写、里程碑、卷边界、strict 和同人场景创建独立语义审稿任务，校验人物、事件、因果、时间、关系、能力和禁揭露七类事实；复核后修改候选会使旧结果失效。
- 章节卡声明 `chapter_duty`、`reader_gain`、代价、章节拓扑、结尾方式、承诺引用与兑现；finalize 后写入读者收益账本。
- 第 1/3/10/30 章、卷首卷末、重大揭露、关系转折和所有同人章节默认要求 Agent semantic review。
- 同人语义审稿额外检查人物声音、关系阶段、能力与世界规则、分歧因果、原角色主体性、原创贡献、集体降智和“只套角色皮”。
- repair、Humanizer、编辑团队和节奏审查的反馈进入带 stable ID、TTL、复发与解决状态的 workbench registry；每次最多五条相关 active 反馈回流，不把整份报告反复塞进上下文。
- 章节卡和写作者工作单携带生效质量合同；它允许慢章、完整收束和余波章，不把画像变成固定句长、对白率或钩子模板。
- `qidian_male` 是默认主合同；`fanqie_free` 只输出最多三条非阻断兼容建议，不会自动改章或阻断定稿。
- 章节卡明确区分平台承诺、章节职责、读者收益、代价与关系变化；Reader Payoff 必须从正文证据验证，不能把计划当成事实。
- `quality baseline-approve` 只能显式批准已定稿章节的 prose-free 结构指纹，CLI 不会自动把新章节加入风格基线。
- 每章方向使用 `design_document_v1`：Agent 提供 2 至 3 个有明确代价的方向，用户显式批准后再编译为 `canonical_delta_v1`，随后生成章节卡和正文工作单。CLI 不替作者决定关键剧情。

查看本章实际合同：

```powershell
longform-engine quality contract project.yaml --chapter N
longform-engine quality contract project.yaml --chapter N --explain
longform-engine quality contract project.yaml --chapter N --compare-market fanqie_free
longform-engine quality baseline-approve project.yaml --chapter N --approved-by editor
longform-engine character audit-task project.yaml --from-chapter 1 --to-chapter 15
longform-engine character samples-approve project.yaml --file 50_workbench/character_reviews/voice_samples.json --approved-by human
```

这些约束针对中文网文常见的开篇拖沓、流水账、角色扁平、对白失真和机械钩子，不承诺单一风格模板适合所有平台。公开写作课程背景可参考[番茄作家课堂](https://fanqienovel.com/writer/zone/tutorial?tab=1)。

## 长篇一致性

v0.4.4 延续 `content_characters_v1` 作为唯一生产规模口径：只统计正文中的 Unicode 字母和数字，不计空白、标点、Markdown 标记、标题、工作单或审稿文件。默认目标为 200 万正文字符，每章目标 3000、软区间 2400 至 3600，预测约 667 章和 8 卷；章节数与卷数会随实际章长和故事密度重估，不再是硬约束。200 万以内属于正式工程支持，超过 200 万可配置但 doctor 标记为 experimental。

全书完成条件是“人工批准的结局完成 + 必要承诺闭环 + 正文字符数进入容差 + 无 P0/P1”，不是抵达某个固定章节号。低于目标时只能由人批准扩展故事弧，禁止自动注水；超过目标时提示重估，不机械压缩已经成立的剧情。核心状态层包括：

| 机制 | 作用 |
| --- | --- |
| RAG | 从 final、摘要和 canon 召回相关事实 |
| Story Graph | 管理人物、地点、组织、关系、事件与伏笔 |
| TCS | 防止未来事实泄漏和时间状态错位 |
| Character Memory | 约束角色动机、关系和能力边界 |
| Outline Anchors | 约束卷目标、阶段推进和改纲影响 |
| Research Canon | 只接纳经引用检查和显式提升的研究结论 |

章节候选必须通过字数、连续性、节奏、AI 味、禁揭露和发布可读性门禁。deterministic evidence gate 负责可复核的短语、状态和路径证据；高风险章节还必须完成 Agent semantic review，检查动机、空间、能力、关系、伏笔和因果，并引用正文 span 与 canonical state。CLI 校验证据后才重算门禁。失败时生成 `gate_result.json` 与 `repair_plan.md`，不会进入 final，也不会污染 RAG、图谱或 SQLite。

## Semantic RAG

公开完整版安装包含 Semantic RAG Python 依赖。默认 profile：

- embedding：`BAAI/bge-m3`
- reranker：`BAAI/bge-reranker-v2-m3`
- 缓存：`70_runtime/models/`
- `semantic.allow_network_download: true`
- `semantic.allow_fallback: false`

doctor 只检查依赖和缓存，不会下载模型。需要预热时显式运行：

```powershell
longform-engine models install project.yaml --profile bge-m3 --download
longform-engine doctor --tool all --project project.yaml
```

## 中文工程指令

| 指令 | 作用 |
| --- | --- |
| `/工程开书` | 创建或打开小说项目 |
| `/工程下一步` | 返回最高优先级安全动作 |
| `/工程工单` | 渲染 AgentTaskManifest 工作单 |
| `/工程生产状态` | 查看稳定生产状态 |
| `/工程生产看板` | 查看章节与任务看板 |
| `/工程推进` | 仅推进确定性、无 apply 的步骤 |
| `/工程续章` | 生成下一章任务包 |
| `/工程提交稿` | 提交 Agent 草稿并运行门禁 |
| `/工程修章` | 生成或处理修章任务 |
| `/工程定稿` | 人工确认后进入 final |
| `/工程章节语义任务` | 对 final 做一次统一证据抽取 |
| `/工程章节语义应用` | 显式物化关系、伏笔、角色状态、TCS 与索引 |
| `/工程关闭章节` | 完整性校验、关闭章节并保留两章活动区 |
| `/工程产物精简` | dry-run 后将旧工作材料归档为可验证 ZIP |
| `/工程改纲` | 生成范围改纲候选与影响标记 |
| `/工程入库` | 显式提升审核后的研究资料 |
| `/工程回滚` | 事务回滚并标记派生产物 stale |
| `/工程同人状态` | 查看 canon、设计和权利提示状态 |
| `/工程同人Canon任务` | 创建来源证据约束的同人 canon 工作单 |
| `/工程同人设计任务` | 创建人物声音、分歧因果与原创主线工作单 |
| `/工程发布风险` | 生成仅提示、不阻断的发布风险报告 |
| `/工程发布导出` | 导出 final 正文并同时生成风险报告 |

完整映射在 Skill 自带的 `references/command_protocol.md`。

## 项目目录

```text
00_governance/   开书确认和生产规则
10_bible/        世界观、人物、风格与 research canon
20_outline/      总纲、卷纲、章节计划、锚点与伏笔账本
30_state/        图谱、TCS、时间线和角色状态
40_manuscript/   draft、final、summary 与 revision 工作区
50_workbench/    Agent tasks、候选稿、审稿与修复产物
60_rag/          检索和记忆派生状态
70_runtime/      SQLite、锁、快照、模型和 benchmark
80_exports/      导出产物
```

## 开发者安装

源码开发才需要 clone、venv 与 editable install：

```powershell
git clone https://github.com/ylqit/novel-general.git
Set-Location novel-general
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[semantic,dev]"
python -m ruff check src tests
python -m mypy src/longform_engine/vector_backends.py src/longform_engine/chapter_contract.py src/longform_engine/storage/recovery.py
python scripts\sync_skill_references.py --check
python scripts\build_resource_manifest.py --check
python scripts\validate_skills.py
python scripts\check_markdown_links.py
python scripts\release_surface_guards.py
python -m pytest --cov=longform_engine --cov-report=term-missing
python -m build
python scripts\audit_wheel.py
python scripts\audit_sdist.py
longform-engine release check --repository . --check-remote
longform-engine release check --repository . --channel rc
```

开发时可使用 `scripts/install-agent-skills.ps1 -Mode junction` 或 `scripts/install-agent-skills.sh --mode symlink`。公开安装固定使用 CLI 的安全 copy 模式。

## 质量基准

真实 5/10 章实验通过 CLI 记录，不手工修改 JSON，也不保存正文。正式质量记录使用 1-10 分、至少三名盲评者，并记录工作单实际字符数：

```powershell
longform-engine benchmark init project.yaml --run-id codex-smoke-5 --host-product codex --chapters 5 --scenario-id setting-v1 --agent-model MODEL --host-version VERSION
longform-engine benchmark record project.yaml --run-id codex-smoke-5 --chapter 1 --continuity 8 --character-consistency 8 --foreshadowing-control 8 --pacing 7 --reader-payoff 8 --ai-taste 3 --gate-passed --context-file-count 6 --context-character-count 18000 --judge editor-a --judge editor-b --judge editor-c
longform-engine benchmark report project.yaml --run-id codex-smoke-5
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 667 --backend local_hnsw
longform-engine benchmark compare project.yaml --comparison-id workflow-a-b-10 --run-id workflow-a-10 --run-id workflow-b-10
```

`rag-scale-run` 使用固定种子验证 50/200/500/667 章下的向量索引、增量更新、stale 和 rollback；667 章正式规模使用 `local_hnsw`。结果明确标记为 `synthetic_engineering`，不等于真实中文语义质量证据。内部回归报告要求运行使用相同 `scenario-id`、相同章节数并全部完成；正式证据还要求十章记录、三名独立评审和 production-model RAG，并通过 `quality_evidence_complete` 校验。

## 文档

- [Skill 安装与开发方式](docs/SKILL_INSTALLATION.md)
- [架构](docs/ARCHITECTURE.md)
- [存储模型](docs/STORAGE_MODEL.md)
- [配置契约](docs/CONFIGURATION.md)
- [质量证据管理](docs/QUALITY_BENCHMARK_RUNBOOK.md)
- [公开发布 Runbook](docs/RELEASE_RUNBOOK.md)
- [v0.4.4 发布 Checklist](docs/V0_4_4_RELEASE_CHECKLIST.md)

## 质量声明

工程化边界、上下文约束和反馈回流为长篇一致性提供可验证基础。目前 `literary_evidence_ready=false`；接口完成、自动测试通过和工具链 smoke 都不能替代真实章节与独立盲评证据。

## License

[MIT](LICENSE)
