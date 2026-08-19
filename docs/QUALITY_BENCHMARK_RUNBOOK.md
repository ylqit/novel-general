# Quality Evidence Runbook

本流程只验证 `longform-novel-engine` 自身。不同运行可以使用不同引擎版本、配置、模型或宿主环境，但必须共享同一场景、章节范围和评分协议。CLI 只保存指标、hash 与短评，不保存章节正文。

## 运行记录

使用 `host_product` 标明承载 Agent 会话的宿主：

```powershell
longform-engine benchmark init project.yaml --run-id current-codex-10 --host-product codex --chapters 10 --scenario-id setting-v1 --scenario-file scenario.json --agent-model MODEL --host-version VERSION --workflow-version 0.4.4
longform-engine benchmark init project.yaml --run-id variant-codex-10 --host-product codex --chapters 10 --scenario-id setting-v1 --scenario-file scenario.json --agent-model MODEL --host-version VERSION --workflow-version variant-a
```

每章完成 submit、gate 和必要 repair 后记录工程数据。正式盲评前使用 `technical-record`，不得由生产者预填文学分：

```powershell
longform-engine benchmark technical-record project.yaml --run-id current-codex-10 --chapter 1 --gate-passed --repair-count 0 --need-human-count 0 --context-file-count 6 --context-character-count 18000
longform-engine benchmark validate project.yaml --run-id current-codex-10 --json
longform-engine benchmark report project.yaml --run-id current-codex-10
```

## 匿名盲评

两组运行必须具有相同 `scenario_id`、`scenario_sha256` 和章节数。分别附加只读来源目录，再生成匿名包：

```powershell
longform-engine benchmark source-attach project.yaml --run-id current-codex-10 --source-dir SOURCE_A
longform-engine benchmark source-attach project.yaml --run-id variant-codex-10 --source-dir SOURCE_B
longform-engine benchmark blind-pack project.yaml --comparison-id internal-regression-10 --run-id current-codex-10 --run-id variant-codex-10
longform-engine benchmark blind-template project.yaml --comparison-id internal-regression-10 --judge-id reviewer-a
longform-engine benchmark blind-submit project.yaml --comparison-id internal-regression-10 --judge-id reviewer-a --file REVIEW_A
longform-engine benchmark blind-aggregate project.yaml --comparison-id internal-regression-10
```

至少需要三名相互独立的评审实例与会话。公开包不得泄露 run id、宿主、模型或工作流身份；来源文件改变后，既有聚合结果失效。

## RAG 数据逻辑

固定数据集用于验证 50、200、500 和 667 章的索引增长、增量同步、stale 与 rollback：

```powershell
longform-engine benchmark rag-scale-run project.yaml --scale-chapters 667 --backend local_hnsw
```

工程门槛为 recall@k 不低于 `0.85`、事实错误率不高于 `0.02`、P95 查询不高于 `1000ms`。`synthetic_engineering` 结果只证明索引行为。

真实章节证据使用正式模型 runner：

```powershell
longform-engine benchmark rag-production-template project.yaml
longform-engine benchmark rag-production-run project.yaml --run-id current-codex-10 --dataset rag-production-dataset.json --top-k 10
```

runner 要求至少 500 个规范 final、50 条带来源 hash 与短 span 的查询、全部七类检索风险、可加载的 embedding/reranker，并且 fallback 关闭。

## 证据完整性

```powershell
longform-engine benchmark compare project.yaml --comparison-id internal-regression-10 --run-id current-codex-10 --run-id variant-codex-10
```

报告中的 `quality_evidence_complete` 仅表示下列证据齐全：

- 每组至少 10 章，场景 hash 和章节范围一致。
- 每章工程验收通过，P0 矛盾与 canonical 污染均为零。
- 正式分数来自同一匿名盲评包和至少三名独立评审。
- 每组都有达到门槛的 `production_model` RAG 证据。
- 来源 hash、宿主、模型和工作流版本仍与运行记录一致。

文学证据未完成时保持 `literary_evidence_ready=false`。工程测试、单次 smoke 或合成 RAG 指标不能代替真实章节盲评。

## 产物

- 运行与章节指标：`70_runtime/benchmarks/<run_id>/`。
- 匿名包和私有映射：`70_runtime/benchmarks/blind_reviews/<comparison_id>/`。
- 内部回归报告：`70_runtime/benchmarks/comparisons/<comparison_id>.json` 与 `.md`。
- 工程 RAG：`70_runtime/benchmarks/rag-scale-v1/`。
- 正式模型 RAG：`70_runtime/benchmarks/<run_id>/rag_scale_evidence.json`。

这些产物默认不提交，也不得写入 final、Bible、outline、graph、TCS、RAG 正式索引或 SQLite。
