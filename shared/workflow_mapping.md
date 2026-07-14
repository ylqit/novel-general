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
-> agent-task brief when an Agent task exists
-> continue-write
-> /工程续章 pre-write guide:
   user preference, automatic fallback, pacing precheck, tail-hook declaration, forbidden reveal confirmation, failure repair path
-> plan-chapter
-> beat
-> read Creative Brief / Writer Craft Brief / RAG / Graph / TCS / Character Memory / Style Memory / Event Matrix / Reverse Brake
-> Agent writes 50_workbench/agent_drafts/chNNN.codex.md or chNNN.claude.md
-> Agent runs Humanizer v2 self-check
-> draft submit
-> gate-check
-> repair-chapter --plan-only if failed
-> creative humanize-task / humanize-check when prose cleanup is needed
-> repair-chapter --candidate-only --agent codex when rewrite task is needed
-> chapter finalize only after pass or valid waiver
-> db sync
```

## Productized Agent App Flow

```text
/工程下一步 -> production next
-> /工程工单 -> agent-task brief for the selected AgentTaskManifest
-> Agent reads only the work order and declared input_files
-> Agent writes only allowed_output_paths
-> validate or draft submit
-> production next
-> apply/finalize only after explicit user command
```

## Five-Step Chapter Loop

```text
1. /工程续章 -> continue-write task package
2. Agent draft -> 50_workbench/agent_drafts/chNNN.codex.md or chNNN.claude.md
3. /工程提交稿 -> draft submit
4. /工程验稿 -> gate-check, including pacing precheck artifacts and reverse_brake_report.md
5. /工程定稿 -> chapter finalize, or repair/waiver/branch/rollback when gate blocks
```

## Creative Operator Flow

```text
creative brief --init/--validate
-> continue-write task package
-> /工程续章 pre-write guide
-> write draft in workbench only
-> humanizer v2 self-check
-> draft submit
-> pacing-review --semantic-reader / gate-check --semantic when needed
-> repair plan or finalize
```

## Editorial Flow

```text
editorial review
-> planning_chief_editor / writing_agent / anti_ai_editor / serial_verifier / executive_editor task files
-> record severity_counts, review_round, unresolved_items, conditional_pass_streak, need_human_reasons
-> editorial batch-review every configured range
-> batch pacing / logic / AI taste health reports
-> need-human when repeated conditional passes or unresolved P0/P1 issues accumulate
```

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
