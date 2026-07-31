# Phase 6 正式质量证明运行手册

本手册只编排真实运行和证据，不调用脚本内 LLM。所有正文由 Codex 或 Claude Code 宿主 Agent 生成；CLI 只创建任务、校验、显式 apply/finalize、记录工程指标并组织盲评。

## 1. 对照矩阵

必须完成四个独立 run：

| Run ID | 宿主 | 工作流 |
| --- | --- | --- |
| `codex-longform-phase6-10` | Codex | longform-novel-engine |
| `codex-novel-skill-phase6-10` | Codex | novel-skill |
| `claude-longform-phase6-10` | Claude Code | longform-novel-engine |
| `claude-novel-skill-phase6-10` | Claude Code | novel-skill |

同一宿主内的两条 run 必须使用同一模型标签、宿主版本、场景文件 SHA-256 和 10 章规模。Codex 与 Claude Code 分别比较，不把跨宿主模型差异归因给工作流。

固定场景：`docs/benchmark_scenarios/PHASE6_ORIGINAL_COMPARISON_V1.json`。

## 2. 初始化运行

在一个隔离的 benchmark 证据项目中为每条 run 执行：

```powershell
longform-engine benchmark init project.yaml `
  --run-id codex-longform-phase6-10 `
  --agent-product codex `
  --host-product codex `
  --chapters 10 `
  --scenario-id phase6-original-comparison-v1 `
  --scenario-file docs/benchmark_scenarios/PHASE6_ORIGINAL_COMPARISON_V1.json `
  --agent-model <exact-model-label> `
  --host-version <exact-host-version> `
  --workflow-version <exact-workflow-version>
```

`novel-skill` run 使用 `--agent-product novel-skill`，并显式填写相同的 `--host-product`。Claude 对照把宿主改为 `claude-code`。

## 3. 生产与工程记录

正文必须在各自隔离的小说项目中真实生成。每章完成门禁后，只记录工程数据，不预填文学分：

```powershell
longform-engine benchmark technical-record project.yaml `
  --run-id codex-longform-phase6-10 `
  --chapter 1 `
  --gate-passed `
  --repair-count 0 `
  --need-human-count 0 `
  --context-file-count 7 `
  --context-character-count 18000 `
  --p0-contradiction-count 0 `
  --canonical-pollution-count 0
```

十章完成后，为评审实际读取的正文目录建立来源证明：

```powershell
longform-engine benchmark source-attach project.yaml `
  --run-id codex-longform-phase6-10 `
  --source-dir <reviewed-manuscript-directory>
```

来源 manifest 只保存路径、字符数、逐章 SHA-256 和 Merkle root，不保存正文。

## 4. 创建盲评包

Codex 和 Claude Code 各创建一个两路对照包：

```powershell
longform-engine benchmark blind-pack project.yaml `
  --comparison-id codex-phase6-formal `
  --run-id codex-longform-phase6-10 `
  --run-id codex-novel-skill-phase6-10 `
  --seed <private-random-seed>
```

只把 `70_runtime/benchmarks/blind_reviews/<comparison-id>/public/` 和单独的评审模板交给评审者。运行 ID 与盲号对应关系只存在于 `private_mapping.json`，评审者不得读取。

## 5. 三名独立评审

为每位评审创建模板：

```powershell
longform-engine benchmark blind-template project.yaml `
  --comparison-id codex-phase6-formal `
  --judge-id judge-a
```

每位评审必须是独立人工评审；Agent 自动评分只能另存为诊断材料，不能进入正式 aggregate。每位人工评审必须：

- 独立阅读并逐章评分。
- 使用不同的 `reviewer.instance_id` 和 `reviewer.session_id`。
- 声明未看 private mapping、未创作任一候选、无利益冲突。
- 对连贯性、角色一致性、伏笔控制、节奏、读者收益和 AI 味逐章给出 1-10 分。

提交与聚合：

```powershell
longform-engine benchmark blind-submit project.yaml `
  --comparison-id codex-phase6-formal `
  --judge-id judge-a `
  --file <completed-judge-a.json>

longform-engine benchmark blind-aggregate project.yaml `
  --comparison-id codex-phase6-formal
```

少于三份有效提交、重复评审实例/会话、身份泄漏、来源正文被修改或 hash 不一致都会阻断聚合。

## 6. Production-model RAG

先生成查询集模板：

```powershell
longform-engine benchmark rag-production-template project.yaml
```

填写至少 50 条真实中文定稿查询，覆盖：

- entity alias
- temporal conflict
- foreshadowing
- causal
- ability boundary
- relationship state
- fact conflict

每个 expected/forbidden evidence 都必须包含 final 文件路径、完整文件 SHA-256 和可复核短 span。运行项目必须至少有 500 个不重复定稿章节，正式 embedding 与 reranker 均可加载，fallback 必须关闭：

```powershell
longform-engine benchmark rag-production-run project.yaml `
  --run-id codex-longform-phase6-10 `
  --dataset <rag-production-dataset.json> `
  --top-k 10
```

该命令会写 `rag_scale_evidence_v1`。固定哈希向量工程结果不能替代此证据。

## 7. 比较与公开声明

```powershell
longform-engine benchmark compare project.yaml `
  --comparison-id codex-phase6-formal-result `
  --run-id codex-longform-phase6-10 `
  --run-id codex-novel-skill-phase6-10
```

Claude Code 对照重复同样步骤。只有两组 comparison 都完成复核，且目标 comparison 的 `claim_eligible=true`，才允许修改 README 的文学质量表述。

任何一项未完成时，准确表述仍是：工程边界、工作流和可复现质量门槛更强，文学效果仍在验证。
