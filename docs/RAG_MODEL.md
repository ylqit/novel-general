# RAG 模型

本地 RAG 以文件为事实源，SQLite 为粗筛和查询层；启用语义能力时可使用默认 BGE embedding 和 reranker。

## 命令

```powershell
python -m longform_engine.cli rag build project.yaml
python -m longform_engine.cli rag query project.yaml "旧钟声"
python -m longform_engine.cli rag context project.yaml --chapter 12 --query "旧钟声 山门"
```

## Build

`rag build` 只从通过章节路径契约校验的 `40_manuscript/final/chNNN.md` 构建 paragraph-aware chunks：

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
- `metadata.source_sha256`

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

## Full Rebuild 与逐章 Delta

`rag build --with-embeddings` 是显式 full rebuild：扫描全部 canonical chunks/memory，重写 `60_rag/metadata/embeddings.jsonl`，再同步整个本地向量库。该 JSONL 是重建/导出快照，不是逐章在线写入日志。

`chapter semantic-apply` 使用 source-bounded delta：只读取当前 final/章节 chunk、变化的 Character Memory、TCS 和 Style Memory per-source 小样本；`sync_semantic_delta` 更新这些 SQLite owner，vector 再按 canonical `source_path` upsert 变化记录并淘汰该来源的旧 ID。它不会遍历历史正文或重写全量 JSONL。Style bible 或旧格式 Style Memory provenance 改变时要求显式 `chapter semantic-rebuild`，不在逐章路径偷偷回扫历史正文。

chapter close 会同时验证 final SHA、chunk 的 `source_sha256`、chunk 数量、active vector 数量以及 vector metadata 的 `source_sha256`。只有记录数而没有当前来源 hash 不能通过关闭门禁。

向量配置只接受已实现 query/upsert 的 `local_sqlite` 和 `local_hnsw`。

## Character Graph-RAG

Semantic RAG now has a graph-aware path in addition to chunk and memory retrieval:

- `rag query project.yaml "query" --semantic --chapter N` can fuse final chunks, scene/chapter memory, Character Memory Cards, and local graph traversal hits.
- `graph retrieve project.yaml --query "..." --chapter N --json` performs entity/alias seed matching, 1-2 hop relationship/event traversal, foreshadow and ability expansion, and chapter/status/stale filtering.
- Graph hits expose `graph_score`, `hop_distance`, `path_reason`, `evidence_span`, and `source_path` so Agent reviewers can see why a relationship, event, clue, or ability constraint was retrieved.
- Character memory lives at `60_rag/memory/characters/<character_id>.json` and is materialized from the evidence-bound chapter semantic ledger by explicit `chapter semantic-apply`.
- TCS state machine files at `30_state/tcs/current.json` and `30_state/tcs/transitions/chNNN.json` provide reader progress, known facts, character knowledge, relationship state, spoiler guard, and state transitions for RAG and semantic gates.

## Creative Operator Inputs

`continue-write` now treats RAG as one part of a larger Agent writing package. The writing task also includes:

- `10_bible/creative_brief.json`,
- Writer Craft Brief,
- Humanizer v4 rules,
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
