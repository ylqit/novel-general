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
-> character_expression_design when a compatible v1 book design needs enrichment
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
-> repair-chapter --plan-only if failed
-> creative humanize-task / humanize-check when prose cleanup is needed
-> conditional humanize-semantic-task / humanize-semantic-validate
-> draft submit only when current source/candidate hashes pass required semantic review
-> repair-chapter --candidate-only --agent codex when rewrite task is needed
-> quality payoff-task / payoff-validate after gate pass when required
-> chapter finalize only after gate pass or valid waiver and a current required payoff review
-> reward_ledger v2 / structure_history written only inside finalize
-> chapter semantic-task: Agent reads final once and writes chapter_semantic_bundle_v1
-> chapter semantic-validate / explicit semantic-apply
-> optional chapter semantic-rebuild --through N after a completed legacy backfill range
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
-> Agent reads only the work order and declared input_files
-> Agent writes only allowed_output_paths
-> validate or draft submit
-> production next
-> apply/finalize/semantic-apply/chapter-close only after explicit user command
```

## Five-Step Chapter Loop

```text
1. /工程续章 -> continue-write task package
2. Agent draft -> 50_workbench/agent_drafts/chNNN.codex.md or chNNN.claude.md
3. /工程提交稿 -> draft submit
4. /工程验稿 -> gate-check, including pacing precheck artifacts and reverse_brake_report.md
5. /工程收益审稿 -> payoff-task / payoff-validate when production next requires it
6. /工程定稿 -> chapter finalize, or repair/waiver/branch/rollback when a gate or payoff review blocks
7. /工程章节语义任务 -> one final read and one chapter_semantic_bundle_v1
8. /工程章节语义应用 -> atomically materialize knowledge, then /工程关闭章节
```

## Creative Operator Flow

```text
creative brief --init/--validate
-> continue-write task package
-> /工程续章 pre-write guide
-> write draft in workbench only
-> humanizer v3 self-check
-> draft submit
-> pacing-review --semantic-reader / gate-check --semantic when needed
-> repair plan or finalize
```

## Editorial Flow

```text
editorial review
-> planning_chief_editor / writing_agent / character_editor / anti_ai_editor / serial_verifier / reader_quality_reviewer / executive_editor task files
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
