# Research Model

资料入库与联网检索的核心规则是：外部资料永远先进入 `research_inbox`，只有执行 `research promote` 后才会成为 canon，并进入 RAG、图谱和 SQLite。

## 1. 设计目标

- 防止未经审核的外部资料污染正式设定。
- 支持手动资料和联网检索两种入口。
- 入库前生成影响分析，帮助作者判断会影响哪些人物、章节、伏笔、图谱节点和未来章节卡。
- promote 后在一个 transaction v3 中同步 `10_bible/`、`20_outline/`、inbox 状态、`30_state/story_graph.json`、RAG context/cache 和 SQLite。
- 所有数据库内容都可从文件重建。

## 2. 命令

```powershell
longform-engine research add project.yaml --file note.md
longform-engine research search project.yaml "宋代市舶司"
longform-engine impact-analyze project.yaml --research-item research_xxx
longform-engine research promote project.yaml --item research_xxx
```

`research add` 读取本地文件，只写入 inbox。

`research search` 默认使用轻量 zh.wikipedia search API；如果网络不可用，会写入 fallback inbox item，要求人工继续审核。

`impact-analyze` 不改变 canon，只写影响报告。

`research promote` 表示资料已经通过审核，会把 inbox item 提升为 canon。

## 3. Inbox 产物

每条资料会生成：

```text
50_workbench/research_inbox/
  research_<hash>.json
  research_<hash>.md
```

JSON 是机器可读状态，Markdown 是可读内容。关键字段包括：

```json
{
  "id": "research_xxx",
  "status": "inbox",
  "title": "宋代市舶司",
  "source_type": "manual_file",
  "source_url": "https://example.test",
  "sources": [],
  "summary": "...",
  "credibility": "user_provided",
  "candidate_impact_scope": {},
  "content_file": "50_workbench/research_inbox/research_xxx.md"
}
```

未 promote 的 item 不会写入 `10_bible/`，也不会生成 `60_rag/chunks/`，因此不会被 RAG 当作正式事实引用。

## 4. 影响分析

影响报告写入：

```text
50_workbench/impact_reports/research_<hash>.md
50_workbench/impact_reports/research_<hash>.json
```

分析维度：

- `impacted_characters`: 与资料关键词相关的人物。
- `impacted_chapters`: 已有定稿和摘要中可能受影响的章节。
- `impacted_foreshadowing`: 可能受影响的伏笔账本项。
- `impacted_graph_nodes`: 可能受影响的图谱实体或事件。
- `impacted_future_cards`: 未来章节卡中可能受影响的任务。

该分析是 deterministic 粗筛，用于提示作者/Agent 审查，不替代人工判断。

## 5. Promote 后的 canon 写入

执行 `research promote` 后会写入：

```text
10_bible/research_canon.jsonl
20_outline/research_impact_ledger.jsonl
30_state/story_graph.json
60_rag/chunks/research_<hash>.json
```

同时 inbox item 会更新为：

```json
{
  "status": "promoted",
  "promoted_at": "...",
  "approved_by": "cli",
  "canon_paths": [
    "10_bible/research_canon.jsonl",
    "20_outline/research_impact_ledger.jsonl",
    "60_rag/chunks/research_xxx.json",
    "30_state/story_graph.json"
  ]
}
```

SQLite 同步后，promoted research 会进入：

- `chapter_chunks`: 用于 RAG 查询。
- `events`: 以 `research:<id>` 事件记录进入 story graph 镜像。
- `rag_queries`: 后续 query cache 仍按文件同步。

成功结果同时返回项目相对的 `transaction_report`。promote 后段任一步骤失败时，transaction v3 会恢复 canon、impact ledger、inbox、RAG、graph、query cache 和 SQLite；事务开始前生成的 workbench impact report仍保留供诊断。

## 6. Agent 使用规则

- Agent 可以执行 `research add` 和 `research search` 收集资料，但不能直接改 Bible。
- Agent 在 `research promote` 前必须先执行或查看 `impact-analyze`。
- 若影响报告显示章节卡、伏笔或图谱冲突，先进入改纲或修订流程。
- 未 promote 的 inbox item 不能出现在 `next_plot_context.md` 的正式事实部分。
