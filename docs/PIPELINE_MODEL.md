# 写作流水线模型

写作流水线负责把开书、章节卡、Beat Sheet、Agent 任务包、门禁阻断和运行报告串成可恢复流程。它不会把草稿伪装成定稿；章节进入正式正文必须经过 `draft submit` 和 `chapter finalize`。

## 命令

```powershell
python -m longform_engine.cli open-book project.yaml
python -m longform_engine.cli plan-chapter project.yaml --chapter 1
python -m longform_engine.cli beat project.yaml --chapter 1
python -m longform_engine.cli continue-write project.yaml --chapter 1
```

## Open Book

`open-book` 强制解析五要素：

- target audience
- writing style
- core forbidden zone
- automation level
- target scale

这些值可以从 `project.yaml` 推导，也可以通过 CLI 参数显式传入。命令会写入：

```text
00_governance/idea_seed.md
00_governance/reader_contract.md
20_outline/book_outline.md
30_state/novel_state.json
```

## Chapter Card

`plan-chapter` 写入：

```text
20_outline/chapter_cards/chNNN.json
20_outline/chapter_cards/chNNN.md
```

章节卡包含：

- chapter number
- title
- volume
- duty
- conflict
- information
- hook
- forbidden
- required context files

## Beat Sheet

`beat` 基于章节卡写入：

```text
50_workbench/beats/chNNN.json
50_workbench/beats/chNNN.md
```

v1 生成五段式 Beat：

- Opening image
- Pressure
- Choice
- Turn
- Hook

## Continue Write

`continue-write` 当前执行：

```text
load_config
verify_previous_gate
query_rag_context
validate_graph
make_chapter_card
generate_beat_sheet
draft_chapter
sync_indexes
write_run_report
```

输出：

```text
60_rag/context/next_plot_context.md
20_outline/chapter_cards/chNNN.json
50_workbench/beats/chNNN.md
40_manuscript/draft/chNNN.md
70_runtime/run_reports/continue_write_chNNN.json
```

注意：`continue-write` 只生成任务包和受控草稿相关产物，不直接写入 `40_manuscript/final/`。定稿、门禁产物、修复计划和正式记忆更新必须由对应 CLI 命令完成。

## Gate Blocking

如果上一章存在：

```text
50_workbench/gate_artifacts/chNNN/gate_result.json
```

且其中 `passed=false`，`continue-write` 会拒绝进入下一章，并要求先执行修复流程。
