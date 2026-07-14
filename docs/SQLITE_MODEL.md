# SQLite 派生索引模型

SQLite 是 `longform-novel-engine` 的派生索引和查询层，不是唯一事实源。正文、Bible、大纲、门禁报告、图谱、RAG 产物仍以文件为准；数据库可以随时通过文件重建。

默认路径：

```text
70_runtime/db/longform_engine.sqlite
```

## 命令

```powershell
python -m longform_engine.cli db init project.yaml
python -m longform_engine.cli db sync project.yaml
python -m longform_engine.cli db rebuild project.yaml
python -m longform_engine.cli db status project.yaml --json
python -m longform_engine.cli db query project.yaml schema_meta
```

## 表

- `schema_meta`: schema 版本和元信息。
- `chapters`: 章节编号、标题、路径、摘要、状态和字数。
- `chapter_chunks`: RAG 正文片段和关键词。
- `entities`: 人物、地点、组织、道具、能力、秘密、伏笔等实体。
- `entity_mentions`: 实体出现章节和出现原因。
- `events`: 事件、参与者、后果和打开/关闭线程。
- `outline_anchors`: 主线锚点、卷锚点和伏笔回收点。
- `gate_results`: 每章门禁结果和失败后允许动作。
- `pacing_history`: 节奏档位、事件类型和 A/B/C 配额使用。
- `rag_queries`: query cache 和命中片段。
- `embeddings`: 可选向量缓存。
- `audit_events`: DB init/sync/rebuild 等审计事件。

## 同步来源

`db sync` 当前会读取：

- `40_manuscript/final/*.md`
- `40_manuscript/chapter_meta.jsonl`
- `60_rag/chunks/*.json`
- `60_rag/query_cache/*.json`
- `30_state/story_graph.json`
- `20_outline/outline_anchors.json`
- `50_workbench/gate_artifacts/**/gate_result.json`
- `30_state/pacing_history.json`

同步是幂等的：每次会清空派生索引表后从文件重新装载，保留 `schema_meta` 和 `audit_events`。

## Rebuild

`db rebuild` 会删除 SQLite 主文件以及 `-wal`、`-shm` sidecar 文件，再从事实源文件完整重建。正文和正式记忆文件不会被修改。
