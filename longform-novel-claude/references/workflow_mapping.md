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
-> book_design_candidate_v2 with story_engine_contract_v1 and character expression contracts
-> character_expression_design when the current book design requests expression enrichment
-> planning task / current planning bundle
-> explicit reader_promise_ledger_v2 expectation windows
-> planning task renders planning_generation_task_v1 with exact original/fanfiction claim-channel ownership
-> planning_bundle_v1 establishes active volume and a three-chapter firm rolling window
-> independent planning semantic review
-> human decisions for every state-changing plot node; micro dialogue/action/transition beats remain inside the chapter contract
-> agent-task brief when an Agent task exists
-> blank human_chapter_intent_v3 / validate / human apply binds approved nodes and contract
-> continue-write
-> unique firm chapter_contract_v5 and chapter_story_brief_basis_v4
-> chapter_story_brief_v5 author work order; readable topology, obligations, approved nodes, character choices, reader value and bounded facts are compiled while raw packets remain separated
-> /工程续章 pre-write guide:
   approved topology, observable change, reader value, applicable failure/choice/cost, scene exchanges, protected outcomes and meaningful repetition risk
-> CLI compiles canonical constraints and retrieval evidence into the internal fact inventory and author Story Brief
-> author reads only chapter_story_brief_v5, never control-plane packets, basis JSON or the task JSON
-> Agent writes 50_workbench/agent_drafts/chNNN.codex.md or chNNN.claude.md
-> Agent runs a bounded natural-prose self-check without detector tricks or word quotas
-> optional chapter_coedit_session_v2: selected span / 2–3 options / human selection / complete workbench candidate
-> draft submit
-> gate-check
-> complete semantic / payoff / pacing / editorial reviews for the same candidate hash
-> scene_prose_editor and anti_template_editor are mandatory; risk roles are additive
-> repair synthesis-task when the CLI review barrier freezes blocking findings
-> Agent repair coordinator writes and validates one immutable rNN plan
-> creative prose-naturalness-task / prose-naturalness-check when prose cleanup is needed
-> conditional prose-naturalness-semantic-task / prose-naturalness-semantic-validate
-> draft submit only when current source/candidate hashes pass required semantic review
-> repair candidate-task --agent codex when the repair plan is validated
-> submit the immutable rNN replacement and rerun the complete review barrier
-> quality payoff-task / payoff-validate after gate pass when required
-> freeze human_review_bundle_v2
-> chapter human-revision-task / validate as human_author_revision_v4 with intent refs, reader effects, exact spans, final lock and independent semantic review
-> submit the complete human candidate as agent=human, invalidating old review evidence
-> rerun the complete gate and independent-review barrier
-> optional human_final review consultation; advice remains read-only and candidate-and-basis-bound
-> chapter human-review-task / validate / apply for risk-layered v7 accept, repair, or redirect
-> chapter finalize only after current evidence-bound v7 acceptance
-> reward_ledger v2 / structure_history written only inside finalize
-> chapter semantic-task: Agent reads final once and writes canonical_delta_v1
-> chapter semantic-validate / explicit semantic-apply
-> graph / character current view / foreshadow state / TCS / RAG / SQLite materialized atomically
-> every approved event reaches realized/deferred/cancelled with human confirmation
-> every non-defer reader-promise action binds an exact final span and semantic-ledger hash
-> chapter close --approved-by human
-> rolling window advances; missing firm coverage or basis drift returns to planning/review/node approval
-> compact audit artifacts outside the two-chapter active buffer
```

## Fanfiction Bootstrap

```text
creation.mode = fanfiction
-> open-book
-> source-library register/import/extraction approval (user-level, non-Canon)
-> fanfiction pack-init (one dynamic Chinese pack per configured work)
-> human selects authoritative version and explicit cutoff
-> bind approved item IDs/bundle hashes/normalization hashes/extraction hashes into this project
-> LLM proposes natural-language identity/design_core/volume_scope/chapter_dependency needs
-> coverage-apply; every work independently passes identity + design_core before route approval
-> fanfiction canon-task with automatically declared approved project-pack inputs
-> agent-task brief
-> Agent writes semantic_document_v1 Chinese body, evidence-backed claims and uncertainties
-> canon-validate
-> CLI hydrates the project baseline semantic document with pinned bindings and exact short evidence
-> canon-apply --approved-by human
-> fanfiction story-engine-task
-> Agent follows the compiled continuity-mode and route-family requirements: character agency, canon relationship, recognition, new reading value, sustained narrative drive and ownership of choices; initial divergence is required only when applicable
-> story-engine-validate
-> story-engine-apply --approved-by human
-> book_ideation rounds with explicit human selection
-> fanfiction design-task
-> Agent writes a semantic_document_v1 route candidate with entry point, character knowledge boundaries, canon-character duties and canon-event fate claims
-> design-validate
-> fanfiction design-review-task in a separate isolated session
-> design-review-validate
-> design-apply --review REVIEW --approved-by human
-> planning task / current planning bundle
-> active volume projects canon range, character stage, event fates, original problem and crossover rules
-> major state-changing nodes receive human decisions; dialogue, transitions and local beats do not become approval quotas
-> current chapter compiles fanfiction_context_bundle_v3 from explicit refs, reasoned dependencies, structured scope, namespaces and an explainable budget
-> chapter_story_brief_v5 exposes only readable Chinese fanfiction context
-> chapter writing and approved-baseline fidelity / new reading value review
```

Rights status and commercial intent are advisory only. Names, relationships, worlds, abilities, and timelines are allowed; continuous source prose and cross-field reconstruction are not. AU and canon-divergent work is reviewed against its declared divergence and causal consequences, not against literal canon sameness.

The active retrieval domains are only `source_evidence`, `project_canon`, and `project_story`. User-provided fanfiction cases and craft techniques are distilled into repository role prompts, quality rules and abstract fixtures; they are not a runtime fanwork corpus and never enter project Canon, RAG or graph.

Global library updates never alter a project silently. `upgrade-status -> upgrade-propose -> independent semantic review -> human decision -> upgrade-apply` either marks explicit future dependencies stale or routes historical impact to `revision_branch_v2`. In non-fanfiction modes, a detected work name only creates `external_work_research_request_v1`; no network call occurs before human approval, and using original characters/world/events requires a mode change to fanfiction.

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

`production next` 与 `agent-task brief` 会返回 `session.policy/action/scope/first_command`。开书和卷级规划可继续项目协调会话；每章 `chapter_write` 必须新开作者会话，`repair` 可继续本章作者会话；自然度修订、所有独立审稿和 final 后语义档案必须新开隔离会话。CLI 不自动创建 Codex/Claude 子进程，也不读取聊天历史作为 canonical。

上下文预算按项目 `writing.agent.context` 自适应。文件数和字符数只用于诊断；工作单显示 engine unit 估算、顺序读取批次与阻断原因。范围/项目证据可以顺序拆分，章节正文始终由一个作者任务完整输出；核心事实无法装入时停止在 `prompt_budget_exceeded`，不得静默截断。

## Chapter Loop

```text
1. /工程章节意图 -> blank v2 intent task / validate / human apply
2. /工程续章 -> continue-write task package
3. Agent draft and optional coedit full candidates in workbench
4. /工程提交稿 -> draft submit; /工程验稿 -> full review barrier
5. /工程人工终稿 -> revision v4 / final lock / human submit / full re-review
6. /工程故事简审 -> v7 accept, repair, or redirect
7. /工程定稿 -> chapter finalize only after current human accept
8. /工程章节语义任务 -> one final read and one canonical_delta_v1
9. /工程章节语义应用 -> atomically materialize knowledge, confirm event/promise evidence, then /工程关闭章节
```

## Creative Operator Flow

```text
creative brief --init/--validate
-> continue-write task package
-> /工程续章 pre-write guide
-> write draft in workbench only
-> prose-naturalness self-check
-> draft submit
-> pacing-review --semantic-reader / gate-check --semantic when needed
-> repair plan or finalize
```

## Editorial Flow

```text
editorial review
-> planning_chief_editor / scene_prose_editor / character_editor / anti_template_editor / reader_experience_editor / canon_fidelity_reviewer task files
-> scene_prose_editor and anti_template_editor on every chapter; other roles are additive
-> canon_fidelity_reviewer task for fanfiction
-> record severity_counts, review_round, unresolved_items, conditional_pass_streak, need_human_reasons
-> editorial batch-review every configured range
-> batch pacing / logic / prose-naturalness health reports
-> need-human when repeated conditional passes or unresolved P0/P1 issues accumulate
```

## Character Expression Flow

```text
production next
-> character design-task when expression enrichment is required
-> Agent writes character_expression_profile_v1
-> design-validate
-> design-apply --approved-by human
-> chapter work orders compile character_expression_packet_v2 within the existing seven-file budget
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
