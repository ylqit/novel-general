# Gate Model

`longform-novel-engine` 的门禁系统采用可解释、可重建的 deterministic gate。它不依赖 LLM 才能运行，适合作为 Codex / ClaudeCode 的硬性流程边界：章节草稿必须先产出门禁报告，失败时只能进入修复或回滚流程，不能直接续写下一章。

## 1. 设计目标

- 每章生成固定门禁产物，便于 Agent、作者和后续 GUI/API 审查。
- 在没有真实 LLM 的 dry-run 环境下也能检查关键风险。
- 与 SQLite 同步，方便查询门禁历史和阻断 `continue-write`。
- 与知识图谱、章节卡、节奏历史联动，形成长篇连载的最低质量线。

## 2. 命令

```powershell
longform-engine gate-check project.yaml --chapter 12
longform-engine gate-waiver project.yaml --chapter 12 --reason "P2 风险已人工确认"
longform-engine pacing-review project.yaml --chapter 12
longform-engine repair-chapter project.yaml --chapter 12 --plan-only
```

`gate-check` 在通过时返回 0；失败时仍会完整落盘产物，但 CLI 返回 1，方便 Agent 和 CI 判断阻断状态。

`repair-chapter --plan-only` 只生成修复计划；需要候选改写时使用 `repair-chapter --candidate-only --agent ...`，候选稿仍必须重新提交和定稿。

## 3. 产物契约

每章门禁目录固定为：

```text
50_workbench/gate_artifacts/chNNN/
  gate_result.json
  consistency_report.md
  pacing_review.md
  style_review.md
  quality_report.md
  publish_ready.md
  repair_plan.md
```

`gate_result.json` 是机器可读事实源，核心字段为：

```json
{
  "chapter_number": 12,
  "passed": false,
  "severity": "P0",
  "failures": [],
  "warnings": [],
  "allowed_actions": ["repair_chapter", "rollback_chapter"],
  "next_command": "repair-chapter --chapter 12",
  "artifact_dir": "50_workbench/gate_artifacts/ch012",
  "source_path": "40_manuscript/draft/ch012.md",
  "updated_at": "2026-06-28T00:00:00+00:00"
}
```

## 4. 检查项

当前已实现以下确定性检查：

- 正文 meta 污染：检测 `TODO`、`写作说明`、`作者按`、`角色定位`、`[说明]`、AI 自述等 prompt/草稿残留，触发 P0。
- 字数硬阈值：根据 `length.chapter_word_count.hard_min` 和 `hard_max` 判断 P1。
- 章节卡完整性：检查 `duty`、`conflict`、`information`、`hook` 是否存在。
- 图谱一致性：复用 `graph check` 的人物位置、能力边界、时间线等冲突报告。
- 节奏失衡：检测连续快章、A/B/C 重大事件超配额、核心秘密过早完整揭露风险。

## 5. Severity

| 等级 | 含义 | 默认动作 |
| --- | --- | --- |
| `PASS` | 无 P0/P1 失败 | 可进入下一步 |
| `P0` | 正文污染、严重流程污染或不可发布内容 | 必须修复或回滚 |
| `P1` | 字数、结构、图谱、节奏等硬性质量失败 | 必须修复或回滚 |
| `P2` | 警告级风险 | 记录到报告，不阻断 |

当前版本只要存在 P0/P1 即 `passed=false`。

## 6. 流水线集成

`continue-write` 会在生成草稿后自动执行 `gate-check`，并把结果写入 run report。下一次 `continue-write` 会读取上一章 `gate_result.json`；如果上一章 `passed=false`，命令会阻断并提示先执行修复流程。

这保证了长篇连载不会在已经失败的章节上继续堆叠错误。

## 7. SQLite 同步

`gate-check` 写入文件后会调用数据库同步，将 `gate_result.json` 镜像到 `gate_results` 表。SQLite 仍是派生索引；如果数据库损坏，可通过：

```powershell
longform-engine db rebuild project.yaml
```

从文件完整重建。

## 8. Waiver

`gate-waiver` 只用于 `PASS` 或 `P2` 级别的人工确认。`P0/P1` 属于阻断级失败，命令会拒绝 waiver，必须修复或回滚。

waiver 会写入：

```text
50_workbench/gate_artifacts/chNNN/waiver.json
```

并把 `gate_result.json` 标记为 `waived=true`，同时追加 `continue_write_with_waiver` 到 `allowed_actions`。

## 9. 后续扩展

- 接入 LLM editorial review，补充风格、爽点、人物动机和读者契约检查。
- 接入正文改写执行器，让 `repair-chapter` 不止生成计划，也能生成 rewrite candidate。
- 将 `publish_ready.md` 接入 finalization，让通过门禁的章节进入定稿发布流程。
