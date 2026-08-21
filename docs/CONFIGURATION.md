# longform-novel-engine 配置说明

源码主线使用 schema v2 项目协议。配置由唯一默认文件、模板或项目 `project.yaml`、命令行显式覆盖三层组成：

```text
config/default.engine.yaml
-> templates/<template>/project.yaml 或项目 project.yaml
-> 命令行显式覆盖
```

当前配置契约要求 `schema_version: 2`，不读取已删除的固定章节数配置或非当前纲要 Schema。

`config/default.engine.yaml` 是默认值的唯一事实源；Python 不保留第二份内置默认字典。打包资源缺失或 YAML 非 mapping 时直接失败。

项目文件和 CLI overlay 会在合并前校验字段。未知字段直接失败；已删除字段返回替代建议。使用 `longform-engine validate-config project.yaml --explain` 可以查看每个生效字段的值、类型、来源和运行时所有者。

## 字数合同

目标总字数是第一规模事实源。默认合同：

```yaml
length:
  metric: content_characters_v1
  target_total_characters: 2000000
  completion_tolerance: [0.90, 1.10]
  chapter:
    target_characters: 3000
    soft_min: 2400
    soft_max: 3600
    hard_min: 2000
    hard_max: 4200
  volume:
    target_characters: 250000
  planning:
    mode: rolling
    detailed_horizon: 20
    refill_threshold: 8
```

`content_characters_v1` 只统计正文中的 Unicode 字母和数字，不统计空白、标点、Markdown 标记和标题。`display_characters` 统计除空白外的显示字符，只用于诊断。工作单、审稿结果、运行日志和其他产物不进入正文规模。

200 万字符按每章 3000 字预测约 667 章；按软区间预测约 556 至 834 章；按每卷 25 万字预测约 8 卷。这些都是 forecast，不是章节或卷数硬约束。200 万及以下为正式工程支持，超过 200 万允许配置，但 doctor 报告 `experimental`。

200 万字新项目默认使用 `semantic.vector_store.backend: local_hnsw`。`local_sqlite` 仍可用于较小索引和元数据存储，但超过 `hnsw_threshold` 后不作为正式规模的向量查询后端；667 章工程基准应使用 `local_hnsw`。

公开配置只接受已实现 query/upsert 驱动的 `local_sqlite` 与 `local_hnsw`。

引擎不再接受：

```text
length.total_chapters
length.target_total_words
length.volume_count
length.chapter_word_count
```

全书完成需要人工批准结局、关闭必要承诺、正文字符数进入容差并清除 P0/P1。低于目标时扩展故事弧必须由人决定，CLI 不自动注水；超过目标时只提示重估目标，不机械压缩正文。

## 滚动纲要

全书层保存主题、结局边界、人物弧、故事弧和卷级字数预算。`outline_design_candidate_v2` 只生成首个详细窗口，默认 20 章。窗口剩余不超过 8 章时，`production next` 创建 `outline_extension_candidate_v1`，且每次只补一个受限窗口。

每章仍有稳定章节号，但最终总章节数可变化。伏笔优先使用 `arc_id + progress_window`，当前章节范围由 CLI 在运行时解析。故事阶段优先采用已批准故事弧，缺失时才按已完成正文字符比例推断。

## 可组合故事画像

单值 `quality.profile.genre` 已删除。不同概念必须放入正交分面：

```yaml
story_profile:
  market:
    primary: qidian_male
    compatibility: [fanqie_free]
  setting:
    primary: game_fantasy
    secondary: [urban]
  plot_engines:
    primary: survival
    supporting: [progression, mystery, romance]
  narrative_forms: [light_novel, ensemble]
  premise_devices: [transmigration]
  relationship_modes: [team, friendship, romance]
  tone: [adventure, suspense]
  resolutions: []

creation:
  mode: fanfiction
```

| 分面 | 示例 |
| --- | --- |
| `setting` | `xuanhuan`、`xianxia`、`wuxia`、`urban`、`history`、`science_fiction`、`game_fantasy` |
| `plot_engines` | `progression`、`survival`、`revenge`、`mystery`、`political_intrigue`、`war`、`business`、`romance` |
| `narrative_forms` | `light_novel`、`ensemble`、`single_lead`、`multi_pov`、`episodic`、`road_novel` |
| `premise_devices` | `transmigration`、`rebirth`、`system`、`time_loop`、`infinite_flow`、`identity_swap` |
| `relationship_modes` | `romance`、`friendship`、`family`、`team`、`rivalry`、`master_disciple` |
| `creation.mode` | `original`、`fanfiction`、`inspired_original`、`adaptation_study` |
| `tone` | `lighthearted`、`humorous`、`dark`、`hot_blooded`、`suspense`、`warm`、`tragic` |

`setting` 和 `plot_engines` 使用 `primary/supporting/accent`；其他分面可以使用简写 ID 列表，或显式 `{id, level}`。编译器输出带来源的 `requirements`、`preferences`、`risks` 和 `review_questions`。互斥 POV、语气或结构不会按数组位置覆盖，必须在 `resolutions` 中由人记录选择与理由。

查看生效画像：

```powershell
longform-engine quality story-profile project.yaml --json
longform-engine quality contract project.yaml --chapter 1 --explain
longform-engine quality contract project.yaml --chapter 1 --compare-market fanqie_free
```

每章工作单只激活最多三个相关分面，不把全书所有标签塞进 Prompt。质量合同合并顺序为：

```text
事实与安全边界
-> 市场
-> 主世界类型
-> 剧情引擎
-> 叙事形式
-> 前提装置
-> 关系重点
-> 当前故事弧
-> 人工批准风格
-> 项目覆盖
```

## Agent 自适应上下文

```yaml
writing:
  agent:
    context:
      mode: adaptive
      host_profile: standard
      capacity_override_units: null
      overflow_policy: split_context
```

`compact`、`standard`、`large` 的资源档位分别为 24K、48K、96K engine-controlled units。它们是保守、模型无关的估算，不是 Codex 或 Claude 的真实 tokenizer 数值。默认至少保留 25% 给正文输出、宿主指令和交接；控制 Prompt 的软/硬目标为容量的 12%/20%，输入证据为 45%/55%。

超限时按“去重 -> 移除 calibration/reference -> 移除未触发模块 -> 编辑任务移除已解决/抑制模式 -> 低优先级证据按需读取 -> 范围任务顺序拆分”处理。作者任务从一开始就不接收编辑模式。章节正文始终只有一个作者输出；核心事实仍放不下时返回 `prompt_budget_exceeded` 和 `need-human`，不会静默截断。

会话策略由角色注册表控制：开书与卷级规划可继续项目协调会话；每章作者新开会话，repair 可继续该章作者会话；Humanizer、独立审稿和 final 后语义档案均新开隔离会话。CLI 只在 `production next` 与 `agent-task brief` 中声明动作、范围和第一条命令。

## 每章人工方向

每章方向选择是 schema v4 的固定工作流，不提供跳过开关。每章写作前，`chapter_direction_candidate_v4` 必须引用覆盖当前章节、basis hash 有效且经人工批准的因果模拟，并为相关读者承诺声明 setup/escalate/partial_payoff/payoff/defer 动作。Markdown 必须提供 2–3 个稳定 option ID；用户选择另存为 `chapter_direction_selection_v1`，绑定文档 hash、选项、调整和重复载体理由。方向合同同时包含目标阶梯、当下欲望、对抗力量、最早失败、不可逆选择、`chapter_turn`、逐场行动/反应/离场状态、故事引擎、场景载体、状态变化和最近五章重复理由。

用户选择后，CLI 才能生成章节卡、beat 和 writing task。普通章节也不跳过此步骤。

## 平台证据注册表

`config/quality_profiles/market_evidence_registry.yaml` 使用 `market_evidence_registry_v2`，是起点、番茄画像观察的唯一证据索引。每条证据必须声明具体 `claims`、`source_type`、`publisher`、发布日期、`verified_at`、`applicability`、证据等级与执行级别。跨平台共同叙事核心仍由 `chapter_contract_v3` 约束；`qidian_male` 是主合同，`fanqie_free` 只提供 P2 非阻断兼容观察。禁止从公开经验推断推荐算法、留存或真实读者行为。

官方发布治理使用独立的 `config/platform_publication_policy_registry.json`（`platform_publication_policy_registry_v1`），不读取项目 YAML 覆盖，也不把市场启发式升级为违规规则。每条记录包含 claim、未知项、发布者、适用范围、核验日和 `next_review_at`；到期后 `publication preflight` 返回 `policy_verification_required`。所有平台预检固定非阻断。

人工作者修订、v4 深审和作者声音没有可跳过配置：

- 每章 final 前必须有当前 `human_author_revision_v1` 与独立双稿语义复核。
- `scene_prose_editor` 和 `anti_ai_editor` 固定每章必审；`editorial.review_mode=off` 只关闭附加风险角色。
- 词语、句长、对白率、感官密度、慢章和尾钩不提供 P1 配额配置。
- 作者声音 bank 最多 12 个 active pair；第一至第三章关闭前每章必须人工批准一个真实修改 pair，替换由人显式选择。

本地审稿台没有远程监听配置。`review serve` 固定绑定 `127.0.0.1`，使用一次性 token、Host/Origin/CSRF/CSP 和预期 hash；网页不能直接写 canonical、批准章节或 finalize。`quality status --json` 分开报告 `protocol_ready`、`author_acceptance_ready` 与 `literary_evidence_ready`。

## 创作模式与同人

`creation.mode` 支持 `original|fanfiction|adaptation_study|inspired_original`。同人配置示例：

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

权利状态是用户声明，只生成发布风险提示，不阻断生产。来源正文不得被连续搬运；canon 事实必须转述并保留 hash/span 证据。

## 写作、存储与安全边界

- `writing.mode`：正式生产固定为 `agent_skill`；`template_dry_run` 只用于测试。
- 存储目录和 SQLite 路径由 `storage.layout` 固定，不接受项目级改名。
- 本地 vector `url/index_url` 允许项目根内的相对路径或绝对路径；解析后越出项目根会在写入前失败。
- `rag` 控制分块、召回数量和混合检索权重；`semantic.profile` 是 embedding/reranker 模型组合的唯一配置入口。
- `quality.profile.strictness` 使用 `light|balanced|strict`，控制风险型语义审稿。
- `quality.semantic_pacing.review_mode` 使用 `off|risk_based|required`；`pacing.default_mode` 使用 `balanced|fast|measured`。
- `gates.forbidden_reveals` 和 `gates.mainline_reveal_warning_hits` 分别定义项目级禁揭示词与主线揭示密度警告阈值。
- `editorial.review_mode=off` 只关闭附加风险策略，不会跳过每章必需的 `scene_prose_editor` 与 `anti_ai_editor`；开篇三章、重大兑现、同人事件与载体重复仍会追加对应审稿角色。
- 人工批准风格样本通过 quality baseline CLI 管理，不在项目 YAML 中维护重复章节列表。
- repair 正文候选固定最多两轮，研究提升固定要求显式批准，这些安全边界不可配置。

Agent 只能读取 manifest 的 `input_files` 并写 `allowed_output_paths`。Bible、outline、final、semantic ledger、RAG、graph、foreshadow state、TCS 和 SQLite 只能经 CLI validate 后显式 apply/finalize/close。

默认生产不需要 OpenAI、Anthropic 或 provider API key，也不提供脚本内 LLM provider。

## 校验与初始化

```powershell
$env:PYTHONPATH=".\src"
python -m longform_engine.cli validate-config --template qidian-longform --explain
python -m longform_engine.cli init-project --template qidian-longform --output novels/demo
python -m longform_engine.cli quality story-profile novels/demo/project.yaml --json
```

当前公开稳定配置是 v0.7.0，验收记录见 [`V0_7_0_RELEASE_CHECKLIST.md`](V0_7_0_RELEASE_CHECKLIST.md)；v0.6.0 仅保留为历史发布事实。
