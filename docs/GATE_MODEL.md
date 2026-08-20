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
longform-engine production next project.yaml
longform-engine repair synthesis-task project.yaml --chapter 12
longform-engine review serve project.yaml --chapter 12 --port 8765
```

`gate-check` 在通过时返回 0；失败时仍会完整落盘产物，但 CLI 返回 1，方便 Agent 和 CI 判断阻断状态。

gate 的 P0/P1 只是一类已验证 finding，不会跳过其他独立审稿。全部必审角色完成且绑定同一候选 hash 后，CLI 才冻结 review bundle；`repair synthesis-task` 让修复主编编排根因、依赖、最小修改半径与保护项，验证通过后再使用 `repair candidate-task` 创建完整替代稿任务。

冻结后必须完成 `human_story_review_v3` 十项深审。accept 需要关键转折、人物选择/情绪和读者收益三类精确 span；repair 需要结构化批注；redirect 必须选择回到章节方向或改纲。任何五类绑定 hash 漂移都使决定失效，未完成深审绝不能 finalize。

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
```

不可变修复产物位于 `50_workbench/repair_plans/chNNN/rNN.*`；gate 目录不再自行生成第二份 `repair_plan.md`。

`gate_result.json` 是机器可读事实源，核心字段为：

```json
{
  "chapter_number": 12,
  "passed": false,
  "severity": "P0",
  "failures": [],
  "warnings": [],
  "allowed_actions": ["complete_reviews"],
  "next_command": "longform-engine production next project.yaml",
  "artifact_dir": "50_workbench/gate_artifacts/ch012",
  "source_path": "40_manuscript/draft/ch012.md",
  "updated_at": "2026-06-28T00:00:00+00:00"
}
```

## 4. 检查项

当前已实现以下确定性检查：

- 正文 meta 污染：检测 `TODO`、`写作说明`、`作者按`、`角色定位`、`[说明]`、AI 自述等 prompt/草稿残留，触发 P0。
- 正文字符硬阈值：根据 `length.chapter.hard_min` 和 `hard_max`，使用 `content_characters_v1` 判断 P1；标题、空白、标点和 Markdown 标记不计入生产规模。
- 章节卡完整性：检查欲望、阻力、失败、不可逆选择、`chapter_turn`、`reveal_boundary`、`reader_gain`、载体与状态变化；发现 `information_release` 或任何 v1 别名时直接返回 `chapter_contract_inconsistent`。
- 图谱一致性：复用 `graph check` 的人物位置、能力边界、时间线等冲突报告。
- 节奏失衡：检测连续快章、A/B/C 重大事件超配额、核心秘密过早完整揭露风险。

## 5. Severity

| 等级 | 含义 | 默认动作 |
| --- | --- | --- |
| `PASS` | 无 P0/P1 失败 | 可进入下一步 |
| `P0` | 正文污染、严重流程污染或不可发布内容 | 必须修复或回滚 |
| `P1` | 正文字符数、结构、图谱、节奏等硬性质量失败 | 必须修复或回滚 |
| `P2` | 警告级风险 | 记录到报告，不阻断 |

确定性 gate 只要存在 P0/P1 即 `passed=false`；语义审稿尚未完成则由 `workflow_stage=reviews_pending` 表达，不再伪造一个正文 failure。

## 6. 流水线集成

`continue-write` 会在生成草稿后自动执行 `gate-check`，并把结果写入 run report。之后由 `production next` 依次完成语义、收益、节奏和风险编辑审稿；只有完整屏障得出无阻断 finding 时才允许 finalize，有阻断 finding 时才允许冻结修复主编任务。

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

## 9. 修复额度

每轮产物使用不可变 `r01`、`r02` 编号。只有完整替代稿成功提交才消耗一次内容额度；无效审稿 JSON、无效计划或任务重建不计次。两轮后仍存在 P0/P1 时进入 `repair_budget_exhausted`，CLI 不生成第三轮命令。

人工改稿没有旁路：审稿台只允许保存到当前已验证 repair task 的完整候选文件，提交时标记 `agent=human`，然后与 Agent 候选一样重跑 gate、所有独立审稿、冻结 bundle 和人工深审。旧决定与旧咨询同时 stale。
