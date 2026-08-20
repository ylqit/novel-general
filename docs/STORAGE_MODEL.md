# Storage Model

本文定义 v0.6.0 的项目落盘合同、事务恢复语义和派生数据边界。

## 1. 标准目录

| 目录 | 数据等级 | 写入者 | 恢复/重建策略 |
| --- | --- | --- | --- |
| `00_governance/` | canonical | 经批准的 CLI apply | 事务回滚 |
| `10_bible/` | canonical | intelligence/research apply | 事务回滚 |
| `20_outline/` | canonical | planning/intelligence apply | 事务回滚 |
| `20_outline/arc_simulations/` | approved planning constraint | intelligence apply + human approval | basis 变化标 stale；改纲/redirect/rollback 事务同步失效 |
| `30_state/reader_promise_ledger.json` | author-side planning ledger | outline/semantic apply | transaction v3；从规划与已批准正文证据推进 |
| `30_state/semantic_ledger/` | canonical evidence | semantic apply | final + candidate hash 验证后事务写入 |
| `30_state/` 其余文件 | materialized canonical view | semantic/planning apply | 可从批准设计、final、ledger 重建 |
| `40_manuscript/draft/` | submitted working state | draft submit | 可替换，不能冒充 final |
| `40_manuscript/final/` | canonical prose | chapter finalize | hash 审批与事务回滚 |
| `50_workbench/` | non-canonical evidence | Agent + CLI | 可归档；不得被查询层当 canonical |
| `50_workbench/human_story_reviews/bundles/` | immutable review evidence | CLI | 冻结当前候选的完整独立审稿；hash 漂移使决定失效 |
| `50_workbench/human_story_reviews/` | five-hash-bound human evidence | CLI + human | v3 决定和十项证据按候选 hash 不可变保留；latest 仅指向当前决定 |
| `50_workbench/human_story_reviews/consultations/` | non-canonical advisory records | CLI + human | 同候选复用会话；候选变化后全部 stale；永不写 final/canonical |
| `50_workbench/intelligence_selections/` | hash-bound human selection | CLI + human | `chapter_direction_selection_v1` 与方向 Markdown 联合批准和编译 |
| `50_workbench/editorial_patterns/registry.jsonl` | derived editorial diagnostics | editorial aggregate / explicit commands | 不承担门禁；损坏由 doctor 警告并显式 rebuild |
| `60_rag/chunks/` | derived | RAG builder | 从 final/ledger 重建 |
| `60_rag/metadata/embeddings.jsonl` | derived full snapshot | explicit full rebuild | 从 chunks/memory 重建 |
| `60_rag/memory/` | materialized view | chapter semantic apply / memory compression | 从 ledger 或 memory compression source 重建 |
| `60_rag/context/`、`query_cache/` | ephemeral derived | RAG query/context | 可删除重建 |
| `70_runtime/db/*.sqlite` | derived runtime | DB/vector layer | 从 canonical 文件/full embedding snapshot 重建 |
| `70_runtime/locks/` | lifecycle | lock manager/recovery | 只可按恢复协议回收 |
| `70_runtime/transactions/` | audit + recovery authority | transaction manager | 不得手工改写 |
| `70_runtime/tx/` | temporary recovery data | transaction manager | 仅由 commit/rollback/recovery 清理 |
| `70_runtime/recovery/` | immutable recovery audit | recovery commands | 保留审计 |
| `70_runtime/artifacts/` | compacted audit | artifact subsystem | hash verify 后可恢复工作材料 |
| `70_runtime/literary_evidence/` | prose-free external evidence manifest | blind-review aggregate | 验证 pack/source/aggregate hash；缺失或篡改即不就绪 |
| `80_exports/` | publication output | publication subsystem | 从 final 重建 |

## 2. 路径与文件名合同

正式正文与摘要只接受 `ch{chapter:03d}.md`：`ch001.md`、`ch999.md`、`ch1000.md`。`chapter_001.md`、`1.md`、中文章名和任何 `.txt` 都会被直接拒绝，不执行别名搜索或自动迁移。章节卡、语义账本和其他结构化产物仍按各自 schema 使用 `chNNN.json`。非章节 JSON 不参与正文枚举。

Agent 只能写 manifest 中唯一声明的 `io.output.path`。它不能直接写 Bible、outline、state、final、RAG 或 runtime DB。CLI 在 validate 成功后才可通过 apply/finalize 将候选物化到 canonical 路径。

网页审稿和咨询记录都属于 non-canonical evidence。人工正文修改也不能直接编辑 draft/final：它只允许写已验证 repair task 的完整候选输出，并以 `agent=human` 走正常 submit、hash 更新、修章额度、gate 和独立审稿生命周期。

`reader_promise_ledger_v1` 是作者向读者建立的期待窗口，不是实际读者行为；`arc_causal_simulation_v1` 是经人工批准的滚动规划约束，不是世界事实；`editorial_pattern_item_v1` 是无正文的编辑复发诊断，不是事实或作者提示。这三个层面禁止相互混写。因果模拟的角色状态 basis 直接哈希 semantic apply 维护的 `60_rag/memory/characters/`，不再读取旧的单文件 character-memory 投影。

所有写路径在进入事务前解析为绝对路径并验证位于项目根目录下。事务快照引用必须位于 `70_runtime/tx/<transaction-id>/`；恢复报告必须位于 `70_runtime/transactions/`。

## 3. Transaction v3 报告

`canonical_write_transaction_report_v3` 包含：

- `command`、`chapter_number`、`source_paths`、`touched_paths`。
- `status` 与稳定 `created_at`。
- `snapshot_dir`、预期 `inventory_targets`、文件 `snapshots`、`sqlite_backups`。
- `before_state` / `after_state` 的路径类型、字节数和 SHA-256。
- `cleanup_complete`、`snapshots_retained`、`cleanup_errors`。
- 不允许 Agent 输出直接成为 canonical 的 boundary 声明。

普通文件/目录通过复制快照参与；项目根内所有声明为 transaction participant 的 `.sqlite`、`.sqlite3` 和 `.db` 文件都使用 SQLite backup API，避免复制 WAL 中间态。`70_runtime/db` 目录作为 participant 时会枚举这些数据库文件；父目录已参与时不重复加入普通子路径。恢复前会重新验证 `touched_paths` 与 filesystem/SQLite inventory 一一覆盖，重复、缺失或漂移均进入 need-human。SQLite restore 会先清除 WAL、SHM 和 rollback-journal sidecar，再用 backup API 恢复并执行 `PRAGMA integrity_check`。

提交顺序是：先原子写 `status=applied` 和 `after_state`，再删除快照，最后写清理结果。这样任何崩溃点都有唯一恢复语义。

## 4. 项目锁 v2

`70_runtime/locks/project.lock` 使用 `O_EXCL` 创建，记录：

```text
schema, owner, owner_token, command, created_at, root,
pid, hostname, process_identity
```

释放时只有 owner token 仍匹配的持有者可以删除锁。`recovery status` 对同主机进程检查 PID 与启动 identity，以区分 active、PID reuse 和 confirmed-dead。远程主机、权限不足或 identity 不可得时是 unknown，不允许自动回收。

stale lock 回收使用独立 `recovery.lock`，避免两个恢复者同时处理同一 stale lock。transaction discard/rollback/cleanup 则在普通项目写锁内串行执行。

## 5. 恢复矩阵

| 观察状态 | 是否自动判断 | 允许动作 |
| --- | --- | --- |
| `preparing` + 安全 snapshot dir | 是 | `recovery discard-preparing` |
| `prepared` + 完整 inventory | 是 | `recovery rollback-transaction` |
| `applied` + `cleanup_complete=false` + 安全 snapshot 路径 | 是 | `recovery cleanup-committed`；允许清理中断后幂等重试，不读取 snapshot inventory 做回滚 |
| `rolled_back` / `aborted_before_apply` / clean `applied` | 是 | 无，终态 |
| `recovery_failed` | 否 | need-human，保留快照和错误清单 |
| 非当前 schema 的 pending/prepared、坏 JSON、越界路径、缺失快照 | 否 | need-human |
| project lock `confirmed_dead` | 是 | `recovery reclaim-lock` |
| lock `active` / `unknown` / `invalid` | 否 | 等待或人工诊断，禁止删除 |

标准操作：

```powershell
longform-engine recovery status project.yaml --json
# 复制 status 给出的 report/lock SHA，不得自行计算后跳过复查
longform-engine recovery discard-preparing project.yaml --report <path> --expected-sha256 <sha> --approved-by <name>
longform-engine recovery rollback-transaction project.yaml --report <path> --expected-sha256 <sha> --approved-by <name>
longform-engine recovery cleanup-committed project.yaml --report <path> --expected-sha256 <sha> --approved-by <name>
longform-engine recovery reclaim-lock project.yaml --expected-sha256 <sha> --approved-by <name>
```

`production next` 和 doctor 会优先暴露恢复 blocker；存在 blocker 时不能继续普通生产。普通 mutation 在取锁前识别 confirmed-dead/unknown/invalid stale lock，在取锁后复查 transaction blocker；活跃持锁者仍由 `O_EXCL` 并发互斥直接阻断。

## 6. RAG 与向量存储

逐章 delta 的 replace key 是 canonical `source_path`：章节向量绑定 final 路径，memory 向量绑定具体 memory JSON。写 SQLite 前会逐条验证 chunk 的 chapter owner、canonical final path 与 `source_sha256`，避免被错误 payload 写入其他章节。delta 只读取这些来源的 active vector，复用相同 `model + content_hash + source_sha256` 的向量，upsert 变化项，并将同来源但本次缺失的旧 ID 标 stale。Style Memory 保存 per-source fingerprint/hash 小样本，逐章只合并当前 final；完整历史扫描只属于 explicit semantic rebuild。

HNSW 的 label metadata 保存在 vector SQLite；mutation 前标记 dirty，索引和 manifest 成功持久化后清除。查询在 dirty、manifest 不一致或依赖缺失时不把索引报告为健康。

runtime database、vector SQLite、HNSW index 与 manifest 必须解析在所属小说项目根目录内；绝对路径可以使用，但越出项目根会在任何写入前失败。

Full rebuild 会重写 `embeddings.jsonl` 并全量同步 vector store。它是恢复/回填工具，不得被逐章 semantic apply 隐式调用。

## 7. 保留与隐私

- 成功事务快照应立即清理；残留由 recovery 显式处理。
- `artifacts compact` 不删除 transaction v3 快照；事务清理由精确 SHA 与审批绑定的 recovery 命令独占。
- 失败恢复材料在完成或人工处置前保留。
- 章节工作台默认保留最近两章，其余经 `artifacts compact` + `artifacts verify` 后归档。
- 不在日志或报告中写 API key、完整 Prompt、完整未发布正文或不必要的模型输入。
- `novels/`、模型缓存、SQLite、query cache、`dist/` 不进入源码发布包。
