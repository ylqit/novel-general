# 写作流水线模型

写作流水线负责把开书、章节卡、Beat Sheet、Agent 任务包、门禁阻断和运行报告串成可恢复流程。它不会把草稿伪装成定稿；章节进入正式正文必须经过 `draft submit` 和 `chapter finalize`。

## 命令

```powershell
python -m longform_engine.cli open-book project.yaml
python -m longform_engine.cli plan-chapter project.yaml --chapter 1
python -m longform_engine.cli beat project.yaml --chapter 1
python -m longform_engine.cli continue-write project.yaml --chapter 1
```

生产控制面使用 `production status` 输出 `production_status_v1`，使用 `production next` 推导唯一下一动作，使用 `agent-task brief` 只读渲染工单。三者不直接调用模型、不写 final，也不绕过 `chapter finalize`。

当前主链为：

```text
story_engine_contract_v1
-> reader_promise_ledger_v1
-> human-approved arc_causal_simulation_v1
-> rolling outline / chapter_direction_candidate_v4 Markdown
-> chapter_direction_selection_v1 / approve / semantic compile
-> chapter_contract_v3 / chapter_story_brief_v2
-> draft
-> span-backed scene review + risk editors
-> immutable review bundle
-> human_story_review_v3 ten-dimension accept / repair / redirect
-> optional non-canonical consultation / complete human repair candidate
-> finalize / semantic apply (promise materialization) / close
-> blind_review_pack_v3 evidence outside ordinary chapter production
```

承诺超过目标章产生 P2；超过最迟章时，下一次方向选择必须进入人工延期或改纲。因果模拟必须覆盖当前滚动窗口且 basis hashes 与故事引擎、承诺规划、角色状态和宏观纲要一致；任一依据改变都会使模拟 stale，并在 transaction v3 中同步失效下游卡片、任务和 SQLite 投影。

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
- chapter duty (`chapter_duty`)
- immediate desire / opposition / dramatic question
- conflict
- failed attempt / irreversible choice / visible cost
- chapter turn (`chapter_turn`) and reveal boundary (`reveal_boundary`)
- primary story engine / scene carriers / state change / dramatic method
- reader gain (`reader_gain`)
- hook
- forbidden
- required context files
- reader promise actions
- approved causal simulation reference

`information_release`、`duty`、`information` 和 `reader_payoff` 不再是章节合同兼容字段；输入中出现这些遗留字段会被拒绝。

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

`continue-write` 在方向已应用且承诺/模拟引用有效后执行：

```text
load_config / verify previous chapter closed
verify chapter_contract_v3, promise actions and causal simulation basis
compile internal chapter_fact_inventory
compile planning/editor context separately
render author-only chapter_story_brief_v2
write Agent task manifest and run report
```

输出：

```text
20_outline/chapter_cards/chNNN.json
50_workbench/beats/chNNN.md
50_workbench/writing_tasks/chNNN.json
50_workbench/writing_tasks/chNNN.md
50_workbench/writing_tasks/chNNN.agent_task.json
70_runtime/run_reports/continue_write_chNNN.json
```

注意：`continue-write` 只生成任务包，不生成正文，也不直接写入 draft/final。Agent 只能写 manifest 声明的 `50_workbench/agent_drafts/` 候选，再由 `draft submit` 进入受控 draft。定稿、门禁产物、修复计划和正式语义更新必须由对应 CLI 命令完成。

作者 Markdown 只渲染 `chapter_story_brief_v2`。内部 `chapter_fact_inventory` 继续供 canonical、RAG、Graph、TCS 与语义校验使用；承诺账本、因果模拟和编辑模式使用独立的 planning/editorial context，均不作为作者工作单。全部独立审稿后必须冻结 review bundle，再执行 `chapter human-review-task / human-review-validate / human-review-apply`；只有十项全过、三类精确 span 齐全，并同时绑定候选、章节合同、承诺账本、因果模拟和 review bundle 五类 hash 的 `accept` 决定可进入 `chapter finalize`。`review serve` 只提供本地可视化校验与咨询，不能自行 apply 或 finalize。

## Gate Blocking

如果上一章存在：

```text
50_workbench/gate_artifacts/chNNN/gate_result.json
```

且其中 `passed=false`，`continue-write` 会拒绝进入下一章，并要求先执行修复流程。
