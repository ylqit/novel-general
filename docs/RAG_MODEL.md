# RAG 模型

本地 RAG 以文件为事实源，SQLite 为粗筛和查询层；启用语义能力时可使用默认 BGE embedding 和 reranker。

## 命令

```powershell
python -m longform_engine.cli rag build project.yaml
python -m longform_engine.cli rag query project.yaml "旧钟声"
python -m longform_engine.cli rag context project.yaml --chapter 12 --query "旧钟声 山门"
```

## Build

`rag build` 从 `40_manuscript/final/*.md` 和 `*.txt` 构建 paragraph-aware chunks：

- 优先保留段落边界。
- 长段落按中文标点和长度拆分。
- 支持 overlap，默认 120 字符。
- 每章输出一个 JSON 文件到 `60_rag/chunks/chNNN.json`。
- 构建后自动执行 `db sync`。

chunk 字段包括：

- `id`
- `chapter_number`
- `chunk_index`
- `title`
- `text`
- `keywords`
- `word_count`
- `token_estimate`
- `metadata.source`

## Query

`rag query` 使用 SQLite hybrid v1：

- 从 `chapter_chunks` 读取候选池。
- 对正文、关键词、metadata 执行词项重叠和 exact phrase 打分。
- 使用 `rag.semantic_weight`、`rag.keyword_weight`、`rag.metadata_weight` 做分数融合。
- 返回可解释命中：chunk id、章节号、分数、source path、原因和关键词。
- 写入 `60_rag/query_cache/<signature>.json`。
- 写入 cache 后自动同步 `rag_queries` 表。

基础查询可使用本地混合评分；语义查询会在候选池内加入 embedding 与 reranker 结果，而不改变命令接口。

## Semantic Model Defaults

Plain `rag build` still builds canonical paragraph chunks and does not download models. Semantic commands are stricter:

- `rag build project.yaml --with-embeddings` verifies the semantic model layer first.
- Default embedding model: `BAAI/bge-m3`.
- Default reranker model: `BAAI/bge-reranker-v2-m3`.
- If models are missing and `semantic.allow_network_download=true`, the command may auto-install the default BGE profile into `70_runtime/models/`.
- If real embedding is unavailable and `semantic.allow_fallback=false`, `rag query/context --semantic` fails instead of returning local-hash results.
- Deterministic `local-hash` remains available for tests/development only when `semantic.allow_fallback=true`.

## Character Graph-RAG

Semantic RAG now has a graph-aware path in addition to chunk and memory retrieval:

- `rag query project.yaml "query" --semantic --chapter N` can fuse final chunks, scene/chapter memory, Character Memory Cards, and local graph traversal hits.
- `graph retrieve project.yaml --query "..." --chapter N --json` performs entity/alias seed matching, 1-2 hop relationship/event traversal, foreshadow and ability expansion, and chapter/status/stale filtering.
- Graph hits expose `graph_score`, `hop_distance`, `path_reason`, `evidence_span`, and `source_path` so Agent reviewers can see why a relationship, event, clue, or ability constraint was retrieved.
- Character memory lives at `60_rag/memory/characters/<character_id>.json` and is only written by `memory character-apply` after workbench validation.
- TCS state machine files at `30_state/tcs/current.json` and `30_state/tcs/transitions/chNNN.json` provide reader progress, known facts, character knowledge, relationship state, spoiler guard, and state transitions for RAG and semantic gates.

## Creative Operator Inputs

`continue-write` now treats RAG as one part of a larger Agent writing package. The writing task also includes:

- `10_bible/creative_brief.json`,
- Writer Craft Brief,
- Humanizer v2 rules,
- Style Memory,
- TCS,
- Character Memory,
- graph constraints and traversal-derived context,
- recent gate history.

This does not change canonical RAG safety. Humanizer output, repair candidates, and editorial review notes remain workbench artifacts until `draft submit` and `chapter finalize` approve them.

## Context

`rag context` 生成：

```text
60_rag/context/next_plot_context.md
```

文档包含：

- query 和目标章节。
- 最近章节摘要。
- 检索命中及来源。
- 小说状态文件引用。
- 使用注意事项，包括 research inbox 不能当作 canon。

这个文件是给 `continue-write` 的可解释上下文输入，不是正式正文。
