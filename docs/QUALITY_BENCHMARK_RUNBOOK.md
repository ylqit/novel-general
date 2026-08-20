# Quality Evidence Runbook

本流程只验证 `longform-novel-engine` 自身。不同运行可以使用不同引擎版本、配置、模型或宿主环境，但必须共享同一场景、章节范围和评分协议。CLI 只保存指标、hash 与短评，不保存章节正文。

## 运行记录

使用 `host_product` 标明承载 Agent 会话的宿主：

```powershell
longform-engine benchmark init project.yaml --run-id candidate-qidian-3 --host-product codex --chapters 3 --scenario-id setting-v1 --scenario-file scenario.json --agent-model MODEL --host-version VERSION --workflow-version SAME_CONDITIONS
longform-engine benchmark init project.yaml --run-id baseline-qidian-3 --host-product codex --chapters 3 --scenario-id setting-v1 --scenario-file scenario.json --agent-model MODEL --host-version VERSION --workflow-version SAME_CONDITIONS
```

每章完成 submit、gate 和必要 repair 后记录工程数据。正式盲评前使用 `technical-record`，不得由生产者预填文学分：

```powershell
longform-engine benchmark technical-record project.yaml --run-id candidate-qidian-3 --chapter 1 --gate-passed --repair-count 0 --need-human-count 0 --context-file-count 6 --context-character-count 18000
longform-engine benchmark validate project.yaml --run-id candidate-qidian-3 --json
longform-engine benchmark report project.yaml --run-id candidate-qidian-3
```

## 匿名盲评

每组两次运行必须使用相同宿主、模型、宿主版本、工作流/生成条件、场景、创作模式和章节数，并且恰好是一组 v0.5.0 候选与一组 v0.4.4 基线。分别附加只读来源目录，再生成匿名包：

```powershell
longform-engine benchmark source-attach project.yaml --run-id candidate-qidian-3 --source-dir SOURCE_A
longform-engine benchmark source-attach project.yaml --run-id baseline-qidian-3 --source-dir SOURCE_B
longform-engine benchmark blind-pack project.yaml --comparison-id qidian-opening-v050 --run-id candidate-qidian-3 --run-id baseline-qidian-3 --review-scope qidian_opening_3 --seed PRIVATE_SEED
longform-engine benchmark blind-template project.yaml --comparison-id qidian-opening-v050 --judge-id reviewer-a
longform-engine benchmark blind-submit project.yaml --comparison-id qidian-opening-v050 --judge-id reviewer-a --file REVIEW_A
longform-engine benchmark blind-aggregate project.yaml --comparison-id qidian-opening-v050
```

对 `qidian_opening_3`、`fanqie_opening_3` 和 `serial_arc_15` 各执行一组。至少需要三名相互独立并完成声明的人工评审；公开包不得泄露 run id、宿主、模型或工作流身份。候选须获得不少于三分之二总体偏好，关键转折、人物主动性和读者收益中位提升至少 1 分，连续性与人物一致性不得下降；十五章组中任何长期失败模式被两人确认即失败。

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

每个通过的聚合会生成不含正文的 scope evidence；只有三类 scope 的 pack hash、来源 Merkle root、aggregate hash、评审数和结论全部有效，并且仍能回验对应 aggregate、source manifest 与原始章节 hash 时，`70_runtime/literary_evidence/manifest.json` 才能令 `literary_evidence_ready=true`。不能复制或手写一个孤立 manifest 绕过 live provenance。缺失、失败或篡改均保持 `false`。工程测试、单次 smoke 或合成 RAG 指标不能代替真实章节盲评。

## 产物

- 运行与章节指标：`70_runtime/benchmarks/<run_id>/`。
- 匿名包和私有映射：`70_runtime/benchmarks/blind_reviews/<comparison_id>/`。
- 文学证据 scope 与 manifest：`70_runtime/literary_evidence/`。
- 内部回归报告：`70_runtime/benchmarks/comparisons/<comparison_id>.json` 与 `.md`。
- 工程 RAG：`70_runtime/benchmarks/rag-scale-v1/`。
- 正式模型 RAG：`70_runtime/benchmarks/<run_id>/rag_scale_evidence.json`。

这些产物默认不提交，也不得写入 final、Bible、outline、graph、TCS、RAG 正式索引或 SQLite。
