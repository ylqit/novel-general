# Revision Model

修订、回滚与多章草稿管理用于支持长篇连载中的常见情况：后续几章已经写出，但前面某章需要重写、回滚或重新规划。

## 1. 设计原则

- 文件仍是事实源，SQLite 只从文件同步章节状态。
- 日常章节版本管理使用事务分支，不依赖 Git branch。
- 回滚不删除后续内容，而是移动到 detached draft。
- 回滚后不假装索引仍然可信，必须标记章节卡、RAG、图谱和事件矩阵 stale。
- 回滚必须生成 impact report，提示受影响的设定、伏笔、人物状态和摘要。

## 2. 章节状态

`status` 命令会从文件和 `40_manuscript/chapter_meta.jsonl` 推导章节状态。

支持状态：

| 状态 | 含义 |
| --- | --- |
| `draft` | `40_manuscript/draft/` 下存在草稿 |
| `reviewed` | 草稿门禁通过，但尚未形成 final |
| `final` | `40_manuscript/final/` 下存在定稿 |
| `rewrite_candidate` | `40_manuscript/rewrite/` 下存在重写候选 |
| `detached` | 回滚后被移入 `40_manuscript/detached/` |
| `stale` | 受回滚、改纲或资料入库影响，需要重算 |

命令：

```powershell
longform-engine status project.yaml --json
```

## 3. 重写分支

```powershell
longform-engine revision branch project.yaml --chapter 12
```

该命令会：

- 从 `final` 或 `draft` 复制目标章节。
- 写入 `40_manuscript/rewrite/ch012_rewrite_candidate.md`。
- 不覆盖原始 `final` 或 `draft`。
- 写入 `50_workbench/revision_reports/branch_ch012.json`。
- 更新 `40_manuscript/chapter_meta.jsonl`。
- 同步 SQLite。

## 4. 回滚

```powershell
longform-engine revision rollback project.yaml --to-chapter 12
```

该命令会：

- 创建轻量 snapshot 到 `70_runtime/snapshots/`。
- 将 `to_chapter` 之后的 final、draft、summaries 移入 `40_manuscript/detached/rollback_to_chNNN_<timestamp>/`。
- 将后续章节卡标记为 `stale`。
- 写入 `30_state/stale_indexes.json`、`30_state/event_matrix_stale.json`、`60_rag/stale.json`。
- 更新 `30_state/novel_state.json` 的 `current_chapter`、`last_finalized_chapter`、`stale`、`stale_chapters` 和 `last_rollback`。
- 写入 rollback impact report。
- 同步 SQLite。

## 5. 回滚影响分析

```powershell
longform-engine impact-analyze project.yaml --after-rollback
```

报告写入：

```text
50_workbench/impact_reports/rollback_to_chNNN.md
50_workbench/impact_reports/rollback_to_chNNN.json
```

报告覆盖：

- 受影响的设定文件。
- 受影响的伏笔和大纲锚点。
- 受影响的人物状态。
- 被 detached 的章节摘要。
- 被标记 stale 的章节卡。
- 受影响的 story graph、event matrix、timeline。

## 6. Agent 使用规则

- 发现前文需要重写时，先执行 `revision branch`，不要直接覆盖 final。
- 确认要切断后续连载方向时，执行 `revision rollback`。
- 回滚后必须查看 `impact-analyze --after-rollback` 的报告。
- 回滚后继续写作前，应先处理 stale chapter cards，并在需要时重建 RAG 与图谱。
