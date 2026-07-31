# longform-novel-engine 配置说明

配置系统目前由三层组成：

```text
内置默认值
-> config/default.engine.yaml
-> templates/<template>/project.yaml 或项目 project.yaml
-> 命令行覆盖
```

当前默认模板：

```text
templates/qidian-longform/project.yaml
```

## 必需字段

`project`:

- `slug`: 项目标识。
- `title`: 小说标题。
- `root_dir`: 小说项目落盘目录。
- `language`: 默认 `zh-CN`。
- `timezone`: 默认 `Asia/Hong_Kong`。

`length`:

- `total_chapters`: 总章节数。
- `target_total_words`: 目标总字数。
- `volume_count`: 卷数。
- `chapter_word_count.target`: 每章目标字数。
- `chapter_word_count.min`: 每章软下限。
- `chapter_word_count.max`: 每章软上限。

`creation`:

- `mode`: `original|fanfiction|adaptation_study|inspired_original`。

`fanfiction`:

- `continuity_mode`: `canon_compliant|canon_divergent|alternate_universe|continuation|prequel|crossover`。
- `sources`: 同人来源声明列表；`fanfiction` 模式至少一个来源，`crossover` 至少两个。
- `sources[].source_id`: 2-80 位稳定来源 ID，用于实体命名空间。
- `sources[].title` / `creator` / `canon_cutoff`: 作品、作者和 canon 截止点。
- `sources[].allowed_elements`: 声明可用于任务包的角色、关系、世界、能力、时间线等元素。
- `sources[].rights_status`: `user_claimed_authorized|public_domain_claimed|platform_permitted_claimed|unverified`。
- `sources[].commercial_intent`: 布尔值，只进入发布风险提示，不阻断生产。
- `sources[].platform_policy_url`: 目标平台同人政策链接，可为空。

权利字段全部是用户声明。配置校验只验证结构，不把 `unverified` 或 `commercial_intent: true` 当作开书、写作、定稿或导出阻断条件。

`storage.directories`:

- `governance`: 默认 `00_governance`。
- `bible`: 默认 `10_bible`。
- `outline`: 默认 `20_outline`。
- `state`: 默认 `30_state`。
- `manuscript`: 默认 `40_manuscript`。
- `workbench`: 默认 `50_workbench`。
- `rag`: 默认 `60_rag`。
- `runtime`: 默认 `70_runtime`。
- `exports`: 默认 `80_exports`。

## 主要策略字段

- `rag.backend`: v1 默认 `sqlite_hybrid`。
- `rag.top_k`: 默认检索命中数。
- `rag.candidate_pool_size`: SQLite 粗筛候选池大小。
- `rag.keyword_weight`: 关键词命中权重。
- `rag.metadata_weight`: 元数据命中权重。
- `rag.semantic_weight`: v1 使用词项重叠作为轻量 semantic placeholder，后续可替换为 embedding rerank。
- `rag.chunk_max_chars`: 章节 chunk 最大字符数，未配置时默认 900。
- `rag.chunk_overlap_chars`: chunk 重叠字符数，未配置时默认 120。
- `writing.mode`: 默认 `agent_skill`，表示 Codex / ClaudeCode 作为写作模型，CLI 只负责任务包、提交、门禁和落盘。
- `writing.agent.task_dir`: Agent-Skill 模式的写作任务包目录，默认 `50_workbench/writing_tasks`。
- `writing.agent.draft_dir`: Codex / ClaudeCode 草稿目录，默认 `50_workbench/agent_drafts`。
- `writing.agent.require_submit_command`: 默认 `true`，要求草稿必须通过 `draft submit` 进入 draft。
- `writing.agent.default_agent`: 默认 `codex`。
- `writing.template_dry_run.enabled`: 默认 `false`。`template_dry_run` 只用于测试和演示，不是正式创作路径。
- `graph.mirror_to_sqlite`: 图谱是否镜像到 SQLite。
- `graph.entity_types`: 图谱实体类型，v1 固定支持 `character`、`location`、`organization`、`ability`、`item`、`secret`、`foreshadowing`、`event`。
- `gates.block_on_previous_failure`: 上一章门禁失败时是否阻断续写。
- `research.default_ingestion`: 默认 `reviewed_inbox`，资料先入 inbox。
- `research.inbox_dir`: research inbox 目录，默认 `50_workbench/research_inbox`。
- `research.impact_report_dir`: 影响分析报告目录，默认 `50_workbench/impact_reports`。
- `research.canon_file`: promote 后写入的 canon JSONL，默认 `10_bible/research_canon.jsonl`。
- `research.impact_ledger`: promote 后写入的大纲影响账本，默认 `20_outline/research_impact_ledger.jsonl`。
- `research.search_provider`: 默认 `zh_wikipedia`，作为轻量联网检索来源。
- `research.search_limit`: 联网检索候选数量。
- `research.promote_requires_approval`: promote 命令表示资料已经通过审核，记录 `approved_by`。
- `revision.default_strategy`: 默认 `transaction_branch`。
- `revision.keep_detached_drafts`: 回滚时保留后续内容为 detached draft。
- `revision.mark_indexes_stale_after_rollback`: 回滚后标记 RAG、图谱、事件矩阵和章节卡 stale。
- `revision.snapshot_before_rollback`: 回滚前创建轻量 snapshot。
- `codex.default_workflow`: 默认 `command_driven`。
- `quality.profile.market`: `qidian_male|fanqie_free|jinjiang_female|general_cn`；旧配置的 `quality.market_profile` 仍兼容。
- `quality.profile.genre`: `xuanhuan|urban|suspense|romance|history`；旧配置的 `quality.genre_profile` 仍兼容。
- `quality.profile.phase`: `auto|opening|early_serial|stable_serial|volume_climax|aftermath`。
- `quality.profile.strictness`: `light|balanced|strict`。
- `quality.profile.overrides`: 项目级深合并覆盖；不得用于制造统一短句、统一高对白或统一悬崖模板。
- `quality.approved_style_baseline.chapters`: 预留的批准章节列表；运行时正式基线只通过 `quality baseline-approve` 显式更新。
- `quality.approved_style_baseline.update_requires_human`: 必须为 `true`；引擎不会自动扩充基线。
- `quality.creative_guidance.mode`: `automatic|guided|off`；`automatic` 只在抽象纲要、卷边界、重大转折、连续返修或多合法剧情线时触发 `chapter_direction`。
- `quality.assurance_mode`: `light|balanced|strict`；控制风险型语义质量任务的触发强度。
- `quality.semantic_review_milestones`: 强制 Agent 语义审稿的章节号，默认 `[1, 3, 10, 30]`。
- `quality.semantic_review_boundaries`: 卷首卷末是否强制语义审稿。
- `quality.reader_payoff.review_mode`: `risk_based|always`；决定读者收益语义审稿的触发方式。
- `quality.reader_payoff.structure_window`: 结构观察窗口，必须在 `10-20` 章之间。
- `quality.reader_payoff.language_similarity_threshold`: 结构、语言和收益组合重复时使用的语言相似阈值。
- `quality.humanizer.changed_character_warning_ratio`: Humanizer 改写比例警告阈值。
- `quality.humanizer.changed_character_human_ratio`: Humanizer 需要人工复核的改写比例阈值。
- `quality.humanizer.semantic_review_mode`: `risk_based|always`；按风险触发或每次执行独立语义保真审稿。
- `quality.humanizer.semantic_review_change_ratio`: `balanced` 模式下的触发比例，默认 `0.15`；`light` 模式最低按 `0.20` 触发。

Humanizer v4 语义审稿读取来源稿和润色候选，输出 `humanizer_semantic_review_v1`。CLI
校验双侧路径、hash、span、声明的 canonical 引用、实体 ID、七类事实维度、章节职责和人物声音。
通过后仍需显式 `draft submit`；复核后修改过的候选不能复用旧审稿结果。

编辑团队默认按风险选择角色；只有显式设置 `editorial.review_roles` 时才固定使用该列表。每个角色使用
`editorial_role_review_v2` 和独立 context digest。质量反馈保存在
`50_workbench/quality_feedback/registry.jsonl`，不是配置或 canonical story fact。使用
`quality feedback-status|feedback-resolve|feedback-suppress` 管理 TTL、解决和抑制状态。

## 向量存储与规模阈值

`semantic.vector_store`:

- `backend`: `local_sqlite|local_hnsw|milvus|pgvector|elasticsearch`。只有两个本地后端已实现，远程后端仍是 experimental contract。
- `url`: SQLite metadata/status 数据库；留空时使用 `70_runtime/db/vector_store.sqlite`。
- `index_url`: HNSW index 文件；留空时从 SQLite 路径派生 `.hnsw`。
- `metric`: `cosine|l2|ip`。
- `dim`: 配置模型的向量维度；实际写入仍会校验同批维度一致。
- `hnsw_threshold`: `local_sqlite` active vector 达到该数量时 doctor 建议切换 HNSW，默认 `10000`。
- `hnsw_m` / `hnsw_ef_construction` / `hnsw_ef_search`: HNSW 建图和检索参数。
- `hnsw_candidate_multiplier`: ANN 候选过采样倍数，候选随后仍受 owner、章节、stale 等 SQLite metadata 过滤。

`local_hnsw` 不取代 SQLite：SQLite 继续保存 record metadata、content hash、stable label 和 dirty state，HNSW 只负责近邻索引。manifest、index 和 SQLite 状态不一致时，query 不会假装 semantic 可用；执行 `longform-engine vector-store rebuild project.yaml` 修复。

`rag build --with-embeddings` 按 content hash 增量同步；没有变化的向量不会重写，canonical 源消失或 revision rollback 时记录进入 stale。固定工程规模验证使用：

```text
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 500 --backend local_hnsw
```

该命令只写 `70_runtime/benchmarks/`，其 `synthetic_engineering` 结果不能用于文学质量声明。

## 同人配置示例

```yaml
creation:
  mode: fanfiction
fanfiction:
  continuity_mode: alternate_universe
  sources:
    - source_id: work_a
      title: 作品名
      creator: 原作者
      canon_cutoff: 第一卷结束
      allowed_elements:
        - characters
        - relationships
        - world
        - abilities
        - timeline
      rights_status: unverified
      commercial_intent: true
      platform_policy_url: ""
quality:
  assurance_mode: balanced
  profile:
    market: jinjiang_female
    genre: romance
    phase: auto
    strictness: balanced
    overrides: {}
  approved_style_baseline:
    chapters: []
    update_requires_human: true
  creative_guidance:
    mode: automatic
  semantic_review_milestones: [1, 3, 10, 30]
  semantic_review_boundaries: true
  reader_payoff:
    review_mode: risk_based
    structure_window: 20
    language_similarity_threshold: 0.72
  humanizer:
    changed_character_warning_ratio: 0.35
    changed_character_human_ratio: 0.60
    semantic_review_mode: risk_based
    semantic_review_change_ratio: 0.15
```

项目由 `production next` 编排逐轮 `book_ideation -> human apply`；原创随后进入 `book_design`，同人则在 `fanfiction_canon` 后完成 ideation，再进入 `fanfiction_design`。Agent 只能读取 manifest 声明文件；所有 Bible/outline 变更仍由 CLI 显式事务 apply。

质量画像来自 wheel 内置 `config/quality_profiles/`，按 market、genre、phase 深合并。`quality contract --chapter N` 只读编译结果；`quality baseline-approve --chapter N --approved-by NAME` 仅保存已定稿章节的 prose-free 结构观察，不保存正文。

## 写作模式与 API key

默认写作模式是 `agent_skill`，Codex / ClaudeCode 负责正文生成，CLI 负责上下文、RAG、图谱、门禁、修订、SQLite 和落盘。因此默认配置不要求 OpenAI、Anthropic 或其他 LLM API key。

支持的 `writing.mode`：

- `agent_skill`: 默认生产路径，无需 API key。
- `template_dry_run`: 测试和演示路径，不作为正式创作方式。

公开运行时不提供脚本内 provider 模式。未来若评估 provider 路线，必须作为单独路线图设计，不能在当前配置中接受后到运行阶段才失败的占位值。

## 校验命令

```powershell
$env:PYTHONPATH=".\src"
python -m longform_engine.cli validate-config --template qidian-longform --explain
```

## 初始化命令

```powershell
$env:PYTHONPATH=".\src"
python -m longform_engine.cli init-project --template qidian-longform --output novels/demo
```
