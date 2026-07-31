# Quality Benchmark Runbook

本文档用于执行 Codex、Claude Code 与 `novel-skill` 的真实 5/10 章对比。CLI 只记录、校验和汇总指标，不调用 LLM，也不保存章节正文。

## 公平性约束

- 建立两条对照线：同宿主同模型的 longform 对 novel-skill，以及各产品默认工作流的对照。
- 每组使用相同开书设定、章节目标、章节数、模型版本、人工确认和返修规则。
- `--scenario-id` 必须一致；正式 run 必须同时使用 `--scenario-file` 固化同一文件的 SHA-256。
- 正式盲评不能只在 `benchmark record` 中自报三个评审 ID，必须走 public pack、独立 submission 与 aggregate。
- 六项指标都使用 1-10 分；只有 `ai-taste` 反向，1 表示 AI 味最低。
- 如实记录 gate、repair、`need-human`、P0 矛盾、canonical 污染、上下文文件数与字符数。
- 不向 benchmark JSON 写正文、长摘录、prompt、API key 或个人信息。

## 5 章 Smoke

为两个宿主分别初始化运行：

```powershell
longform-engine benchmark init project.yaml --run-id codex-smoke-5 --agent-product codex --chapters 5 --scenario-id setting-v1 --agent-model MODEL --host-version VERSION
longform-engine benchmark init project.yaml --run-id claude-smoke-5 --agent-product claude-code --chapters 5 --scenario-id setting-v1 --agent-model MODEL --host-version VERSION
```

每章完成 submit、gate 和必要 repair 后记录一次：

```powershell
longform-engine benchmark record project.yaml --run-id codex-smoke-5 --chapter 1 --continuity 8 --character-consistency 8 --foreshadowing-control 8 --pacing 7 --reader-payoff 8 --ai-taste 3 --gate-passed --repair-count 0 --need-human-count 0 --context-file-count 6 --context-character-count 18000 --p0-contradiction-count 0 --canonical-pollution-count 0 --judge editor-a --judge editor-b --judge editor-c --notes "盲评摘要"
```

发生问题时可重复短标签参数，不得粘贴正文：

```powershell
longform-engine benchmark record project.yaml --run-id codex-smoke-5 --chapter 2 --continuity 6 --character-consistency 7 --foreshadowing-control 5 --pacing 6 --reader-payoff 6 --ai-taste 6 --gate-failed --repair-count 1 --need-human-count 1 --context-file-count 7 --context-character-count 19800 --p0-contradiction-count 0 --canonical-pollution-count 0 --judge editor-a --judge editor-b --judge editor-c --foreshadowing-leak "提前说明幕后身份" --ai-taste-issue "段尾总结重复"
```

检查完整性并生成单组报告：

```powershell
longform-engine benchmark validate project.yaml --run-id codex-smoke-5 --json
longform-engine benchmark report project.yaml --run-id codex-smoke-5
```

5 章 smoke 只证明生产链可跑通，不能用于“质量优于”声明。

## 10 章正式盲评

同宿主同模型至少需要四组运行：Codex + longform、Codex + novel-skill、Claude + longform、Claude + novel-skill。完整命令、固定场景和证据步骤见 [`PHASE6_QUALITY_PROOF_RUNBOOK.md`](PHASE6_QUALITY_PROOF_RUNBOOK.md)。

正式运行先使用 `benchmark technical-record` 记录工程指标，再执行 `source-attach -> blind-pack -> blind-template -> blind-submit -> blind-aggregate`。生产者不得预填正式文学分。完成后分别比较：

```powershell
longform-engine benchmark compare project.yaml --comparison-id codex-longform-vs-novel-skill-10 --run-id codex-longform-10 --run-id codex-novel-skill-10
longform-engine benchmark compare project.yaml --comparison-id claude-longform-vs-novel-skill-10 --run-id claude-longform-10 --run-id claude-novel-skill-10
```

需要观察中间结果时可以加 `--allow-incomplete`，但报告会标为 provisional，不能用于 README 质量声明。

## 500 章 RAG 证据

Phase 5 先在 500 章固定种子数据集上验证索引工程行为：

```powershell
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 500 --backend local_hnsw
```

最低工程门槛是 500 章、recall@k 不低于 `0.85`、事实错误率不高于 `0.02`、P95 查询不高于 `1000ms`。50 章和 200 章运行用于发现增长曲线问题。该命令输出 `evidence_grade=synthetic_engineering` 和 `claim_eligible=false`，不能替代真实中文章节、正式 embedding/reranker、冲突事实、别名、时间与伏笔查询组成的 `production_model` 证据。旧 `benchmark rag-record` 只兼容记录手工指标，comparison 会拒绝把它作为公开声明证据。

Phase 6 的 production-model 证据使用：

```powershell
longform-engine benchmark rag-production-template project.yaml
longform-engine benchmark rag-production-run project.yaml --run-id codex-longform-10 --dataset rag-production-dataset.json --top-k 10
```

runner 会要求至少 500 个不重复 final、50 条带来源文件 hash 与短 span 的真实查询、七类检索风险、可加载的正式 embedding/reranker 和关闭 fallback。

## 质量声明门槛

comparison 只有同时满足以下条件才会输出 `claim_eligible: true`：

- longform 候选与 `novel-skill` baseline 使用相同宿主、模型、场景和 10 章记录。
- 综合盲评分领先至少 `0.5/10`，且不少于 7 章胜出。
- 任一核心文学维度落后不超过 `0.3`。
- P0 连贯性、人物或事实矛盾为零，canonical 污染为零。
- repair 和 `need-human` 次数不高于 baseline。
- 候选与 baseline 都有至少三名独立评审。
- 候选具有引擎 runner 生成、达到门槛且标记为 `production_model` 的 500 章 RAG 证据。

## 产物与边界

- 运行记录：`70_runtime/benchmarks/<run_id>/run.json`。
- 章节指标：`70_runtime/benchmarks/<run_id>/chapter_records.json`。
- 来源证明：`70_runtime/benchmarks/<run_id>/source_manifest.json`。
- 盲评公开包与私有映射：`70_runtime/benchmarks/blind_reviews/<comparison_id>/`。
- 工程 RAG 记录：`70_runtime/benchmarks/rag-scale-phase5-v1/<backend>/chNNN/result.json`。
- 正式 RAG 声明证据：`70_runtime/benchmarks/<run_id>/rag_scale_evidence.json`，必须来自 Phase 6 production-model runner。
- 单组报告：`70_runtime/benchmarks/<run_id>/report.json` 与 `report.md`。
- 对比报告：`70_runtime/benchmarks/comparisons/<comparison_id>.json` 与 `.md`。
- benchmark 产物默认不提交，不进入 final、RAG、graph、TCS 或 SQLite。

`best_by_metric` 和综合分只是固定规则汇总，不是自动文学裁决。正式结论仍需保存匿名盲评表、模型与宿主版本、上下文规模、失败记录和人工介入信息，使实验可以复核。
