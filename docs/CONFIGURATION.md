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
- `writing.api.enabled`: 默认 `false`。只有显式选择 `writing.mode: api_provider` 时才会进入独立 Python provider 路线。
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

## 写作模式与 API key

默认写作模式是 `agent_skill`，Codex / ClaudeCode 负责正文生成，CLI 负责上下文、RAG、图谱、门禁、修订、SQLite 和落盘。因此默认配置不要求 OpenAI、Anthropic 或其他 LLM API key。

支持的 `writing.mode`：

- `agent_skill`: 默认生产路径，无需 API key。
- `api_provider`: 可选增强，独立 Python 写作引擎才需要 provider、模型和 API key 或本地模型服务。
- `template_dry_run`: 测试和演示路径，不作为正式创作方式。

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
