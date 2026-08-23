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
-> rolling outline / chapter_direction_candidate_v5 Markdown
-> chapter_direction_selection_v1 / approve / semantic compile
-> blank human_chapter_intent_v1 / validate / human apply
-> chapter_contract_v4 / chapter_story_brief_basis_v2 / chapter_story_brief_v4
-> AI draft / chapter_coedit_session_v1 iterative full workbench candidates
-> selected candidate frozen and submitted to draft
-> deterministic signals + mandatory scene_prose_editor / anti_template_editor
-> P0/P1 repair when required; otherwise freeze pre-revision bundle
-> human_author_revision_v3 final complete candidate + final lock + prose_revision_semantic_review
-> draft submit --agent human / full gate and independent review rerun
-> human_story_review_v6 risk-layered accept / repair / redirect
-> optional human_final read-only consultation bound to the locked candidate
-> finalize / semantic apply (promise materialization) / close
-> blind_review_pack_v4 evidence outside ordinary chapter production
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
- ending mode / ending intent / emotional aftereffect
- suspense protection / resolution markers
- forbidden
- required context files
- reader promise actions
- approved causal simulation reference

`information_release`、`duty`、`information`、`reader_payoff`、`hook`、`hook_mode`、`plot_obligation`、`irreversible_action` 和卡片级 `dramatic_freedom` 不再是章节合同兼容字段；输入中出现这些遗留字段会被拒绝。

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
- Exit intent

## Continue Write

`continue-write` 在方向已应用、当前 `human_chapter_intent_v1` 已人工 apply 且承诺/模拟引用有效后执行：

```text
load_config / verify previous chapter closed
verify chapter_contract_v4, promise actions and causal simulation basis
verify human intent binds the current direction selection and contract
compile internal chapter_fact_inventory
compile planning/editor context separately
compile chapter_story_brief_basis_v2
render author-only chapter_story_brief_v4
write Agent task manifest and run report
```

输出：

```text
20_outline/chapter_cards/chNNN.json
50_workbench/beats/chNNN.md
50_workbench/writing_tasks/chNNN.json
50_workbench/writing_tasks/chNNN.md
50_workbench/writing_tasks/chNNN.basis.json
50_workbench/writing_tasks/chNNN.agent_task.json
70_runtime/run_reports/continue_write_chNNN.json
```

注意：`continue-write` 只生成任务包，不生成正文，也不直接写入 draft/final。Agent 只能写 manifest 声明的 `50_workbench/agent_drafts/` 候选，再由 `draft submit` 进入受控 draft。定稿、门禁产物、修复计划和正式语义更新必须由对应 CLI 命令完成。

作者 Markdown 只渲染 `chapter_story_brief_v4`，并包含人类意图、本章必要人物声音与筛选事实。内部 `chapter_fact_inventory` 继续供 canonical、RAG、Graph、TCS 与语义校验使用；承诺账本、因果模拟、平台诊断和编辑模式不作为作者原始上下文。只有任务状态、合同、意图、basis、Markdown 和活动 manifest 全部一致时才复用旧工作单。

共编阶段只保存结构化 span、方案、人工选择、修改意图和完整候选 hash；不保存完整 Prompt，不能直接写 canonical。`phase=coedit` 可从选中方案生成完整候选，`phase=human_final` 永远只读。全部独立审稿后冻结 `human_review_bundle_v2`，再完成人工终稿、最终锁、全量复审与风险分层深审；只有三组核心精确证据齐全、finding 已处置，并绑定候选、合同、人类意图、Story Brief basis、承诺账本、因果模拟、review bundle 和人工终稿八类证据的 `accept` 可进入 `chapter finalize`。

## Gate Blocking

如果上一章存在：

```text
50_workbench/gate_artifacts/chNNN/gate_result.json
```

且其中 `passed=false`，`continue-write` 会拒绝进入下一章，并要求先执行修复流程。
