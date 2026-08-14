# longform-novel-engine 配置说明

源码主线使用 v0.4.0 项目协议。配置由内置默认值、模板或项目 `project.yaml`、命令行显式覆盖三层组成：

```text
config/default.engine.yaml
-> templates/<template>/project.yaml 或项目 project.yaml
-> 命令行显式覆盖
```

v0.4.0 是破坏性升级：`schema_version` 必须为 `2`，不会读取 v0.3.x 的固定章节数配置或旧纲要 Schema。

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

## 每章人工方向

`quality.creative_guidance.mode` 必须保持 `guided`。每章写作前，`chapter_direction_candidate_v2` 必须提供 2 至 3 个方向以及各自代价，由用户显式选择。方向工作单包含全书/卷/主角目标阶梯、场景链、角色欲望、对白归属、关系变化、主线和伏笔回响。

用户选择后，CLI 才能生成章节卡、beat 和 writing task。普通章节也不跳过此步骤。

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
- `storage.directories`：定义 governance、Bible、outline、state、manuscript、workbench、RAG、runtime 和 exports。
- `rag` / `semantic`：控制检索、embedding、reranker 和向量后端；SQLite 与向量库都是可重建派生索引。
- `quality.assurance_mode`：`light|balanced|strict`，控制风险型语义审稿，不把句长、对白率或悬崖结尾变成统一配额。
- `quality.approved_style_baseline`：只接受人工批准的 prose-free 风格观察。

Agent 只能读取 manifest 的 `input_files` 并写 `allowed_output_paths`。Bible、outline、final、semantic ledger、RAG、graph、foreshadow state、TCS 和 SQLite 只能经 CLI validate 后显式 apply/finalize/close。

默认生产不需要 OpenAI、Anthropic 或 provider API key，也不提供脚本内 LLM provider。

## 校验与初始化

```powershell
$env:PYTHONPATH=".\src"
python -m longform_engine.cli validate-config --template qidian-longform --explain
python -m longform_engine.cli init-project --template qidian-longform --output novels/demo
python -m longform_engine.cli quality story-profile novels/demo/project.yaml --json
```

v0.4.0 正式发布前的验收状态见 [`V0_4_0_WORD_BUDGET_AND_COMPOSABLE_PROFILE_CHECKLIST.md`](V0_4_0_WORD_BUDGET_AND_COMPOSABLE_PROFILE_CHECKLIST.md)。
