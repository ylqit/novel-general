# Workflow Mapping

## Bootstrap

```text
validate-config
-> /工程开书
-> open-book --interactive when project.yaml is missing
-> open-book project.yaml when project.yaml exists
-> creative brief --validate
-> creative style-profile when genre profile needs confirmation
-> status
```

## Current Chapter Flow

```text
open-book
-> production next
-> book_ideation one question / two or three options / explicit human selection
-> book_design_candidate_v2, including character expression contracts, after all creative decisions are applied
-> character_expression_design when the current book design requests expression enrichment
-> outline_design
-> agent-task brief when an Agent task exists
-> conditional chapter_direction before chapter card/write task
-> continue-write
-> /工程续章 pre-write guide:
   user preference, automatic fallback, pacing precheck, tail-hook declaration, forbidden reveal confirmation, failure repair path
-> plan-chapter
-> beat
-> read the compiled Character Performance Packet / Creative Brief / RAG / Graph / TCS / Style Memory / Event Matrix / Reverse Brake
-> Agent writes 50_workbench/agent_drafts/chNNN.codex.md or chNNN.claude.md
-> Agent runs Humanizer v4 two-pass self-check
-> draft submit
-> gate-check
-> complete semantic / payoff / pacing / editorial reviews for the same candidate hash
-> repair synthesis-task when the CLI review barrier freezes blocking findings
-> Agent repair coordinator writes and validates one immutable rNN plan
-> creative humanize-task / humanize-check when prose cleanup is needed
-> conditional humanize-semantic-task / humanize-semantic-validate
-> draft submit only when current source/candidate hashes pass required semantic review
-> repair candidate-task --agent codex when the repair plan is validated
-> submit the immutable rNN replacement and rerun the complete review barrier
-> quality payoff-task / payoff-validate after gate pass when required
-> chapter finalize only after gate pass or valid waiver and a current required payoff review
-> reward_ledger v2 / structure_history written only inside finalize
-> chapter semantic-task: Agent reads final once and writes canonical_delta_v1
-> chapter semantic-validate / explicit semantic-apply
-> graph / character current view / foreshadow state / TCS / RAG / SQLite materialized atomically
-> chapter close --approved-by human
-> compact audit artifacts outside the two-chapter active buffer
```

## Fanfiction Bootstrap

```text
creation.mode = fanfiction
-> open-book
-> fanfiction canon-task with explicitly declared source files
-> agent-task brief
-> Agent writes fanfiction_source_canon_v1
-> canon-validate
-> canon-apply --approved-by human
-> book_ideation rounds with explicit human selection
-> fanfiction design-task
-> Agent writes fanfiction_design_candidate_v1
-> design-validate
-> design-apply --approved-by human
-> outline_design
-> chapter writing
```

Rights status and commercial intent are advisory only. Names, relationships, worlds, abilities, and timelines are allowed; continuous source prose and cross-field reconstruction are not. AU and canon-divergent work is reviewed against its declared divergence and causal consequences, not against literal canon sameness.

## Productized Agent App Flow

```text
/工程下一步 -> production next
-> /工程工单 -> agent-task brief for the selected AgentTaskManifest
-> Agent reads only the work order and manifest io.inputs
-> Agent writes only io.output.path using io.output.protocol
-> validate or draft submit
-> production next
-> apply/finalize/semantic-apply/chapter-close only after explicit user command
```

`production next` 与 `agent-task brief` 会返回 `session.policy/action/scope/first_command`。开书和卷级规划可继续项目协调会话；每章 `chapter_write` 必须新开作者会话，`repair` 可继续本章作者会话；Humanizer、所有独立审稿和 final 后语义档案必须新开隔离会话。CLI 不自动创建 Codex/Claude 子进程，也不读取聊天历史作为 canonical。

上下文预算按项目 `writing.agent.context` 自适应。文件数和字符数只用于诊断；工作单显示 engine unit 估算、顺序读取批次与阻断原因。范围/项目证据可以顺序拆分，章节正文始终由一个作者任务完整输出；核心事实无法装入时停止在 `prompt_budget_exceeded`，不得静默截断。

## Five-Step Chapter Loop

```text
1. /工程续章 -> continue-write task package
2. Agent draft -> 50_workbench/agent_drafts/chNNN.codex.md or chNNN.claude.md
3. /工程提交稿 -> draft submit
4. /工程验稿 -> gate-check, including pacing precheck artifacts and reverse_brake_report.md
5. /工程收益审稿 -> payoff-task / payoff-validate when production next requires it
6. /工程定稿 -> chapter finalize, or repair/waiver/branch/rollback when a gate or payoff review blocks
7. /工程章节语义任务 -> one final read and one canonical_delta_v1; CLI materializes the internal semantic ledger
8. /工程章节语义应用 -> atomically materialize knowledge, then /工程关闭章节
```

## Creative Operator Flow

```text
creative brief --init/--validate
-> continue-write task package
-> /工程续章 pre-write guide
-> write draft in workbench only
-> humanizer v4 self-check
-> draft submit
-> pacing-review --semantic-reader / gate-check --semantic when needed
-> repair plan or finalize
```

## Editorial Flow

```text
editorial review
-> planning_chief_editor / scene_prose_editor / character_editor / anti_ai_editor / reader_experience_editor / canon_fidelity_reviewer task files
-> canon_fidelity_reviewer task for fanfiction
-> record severity_counts, review_round, unresolved_items, conditional_pass_streak, need_human_reasons
-> editorial batch-review every configured range
-> batch pacing / logic / AI taste health reports
-> need-human when repeated conditional passes or unresolved P0/P1 issues accumulate
```

## Character Expression Flow

```text
production next
-> character design-task when expression enrichment is required
-> Agent writes character_expression_profile_v1
-> design-validate
-> design-apply --approved-by human
-> chapter work orders compile character_expression_packet_v1 within the existing seven-file budget
-> character_editor requires exact evidence for every featured character, including pass verdicts
-> character audit-task --from-chapter A --to-chapter B for cross-chapter voice and scene review
-> audit-validate / audit-apply archives only to workbench
-> samples-approve --approved-by human may promote exact final-chapter spans into the bounded voice sample bank
```

No project-wide dialogue, appearance, interiority, dialect, or catchphrase quota is allowed. Genre and scene intent control density; the engine reports evidence and swapability risk.

## Current Research Flow

```text
research add/search
-> research inbox
-> impact-analyze
-> research promote
-> graph/rag/db sync
```

## Current Revision Flow

```text
revision branch
-> rewrite candidate
-> gate-check
-> revision rollback if old direction is rejected
-> mark stale indexes and detach later drafts
-> impact-analyze --after-rollback
-> rebuild indexes after new direction is accepted
```
