# Creative Operator Protocol

This protocol applies when Codex or ClaudeCode writes, repairs, humanizes, or reviews a chapter in `longform-novel-engine`.

## Non-Negotiable Boundary

Agents may create prose and review artifacts only in workbench lanes:

- `50_workbench/agent_drafts/`
- `50_workbench/repair_candidates/`
- `50_workbench/humanizer_tasks/`
- `50_workbench/editorial_reviews/`

Agents must not directly edit:

- `40_manuscript/final/`
- `60_rag/`
- `30_state/story_graph.json`
- `30_state/tcs/`
- `70_runtime/db/`

Canonical state changes must go through CLI commands: `draft submit`, `chapter finalize`, `chapter semantic-apply`, `chapter close`, `research promote`, `revision rollback`, or `db rebuild`.

## Creative Decisions And Quality Contract

- After `open-book`, follow `production next` through `book_ideation`. Each work order asks one core question and offers two or three options with tradeoffs. Do not infer a selection from silence.
- Write only `book_ideation_candidate_v1`; the CLI saves one explicitly selected/provided answer through `intelligence apply --approved-by human`.
- Every unapplied chapter reaches `chapter_direction` before prose: offer two or three causally distinct directions with costs and record the user's explicit selection. Do not write chapter prose in this task.
- Read the `effective_quality_contract_v1` embedded in the chapter card/writing brief. It combines market, genre, phase, approved baseline, and project overrides, but is not a universal sentence-length, dialogue-density, pace, or cliffhanger template.
- Never add a finalized chapter to the approved style baseline automatically. Only the explicit `quality baseline-approve` CLI command may add its prose-free craft fingerprint.

## `/工程续章` Pre-Write Guide

Use this guide before writing any new chapter draft. `/工程续章` is the primary Chinese engineering entry for continuing a chapter; it maps to `longform-engine continue-write project.yaml --chapter N`.

Before prose is written, the author Agent reads only `50_workbench/writing_tasks/chNNN.md`, the rendered `chapter_story_brief_v2`. The paired JSON, fact inventory, reader-promise ledger, causal simulation, editorial-pattern registry and retrieval/control-plane packets are CLI/editor inputs, not author inputs.

- Story pressure: confirm what is happening, what the protagonist wants, who or what refuses, the earliest failure, irreversible choice and visible cost.
- Scene execution: follow each declared action, reaction, choice, cost and exit state; fully dramatize the required turns and compress only the allowed connective process.
- Story boundaries: preserve protected outcomes, obey prohibited drift, deliver the declared reader gain, emotional aftereffect and relationship change.
- Carrier variation: use the recent-five-chapter carrier warning to change pressure, character ownership or dramatic method when needed; an approved repetition reason is authority, not a quota exemption invented by the author.
- Ending condition: land on the declared changed state and chapter pressure without forcing a universal cliffhanger.
- Failure repair path: follow `production next` until semantic, payoff, pacing and editorial reviews all bind to the same candidate hash. `scene_prose_editor` is mandatory. After independent reviews, run the human story review: `accept` permits finalize, `repair` joins the immutable review bundle, and `redirect` returns to direction or outline revision.

Required production closed loop:

1. Generate or read the `continue-write` task package.
2. Agent writes only to `50_workbench/agent_drafts/chNNN.codex.md` or `chNNN.claude.md`.
3. Submit the candidate with `draft submit`.
4. Run or inspect `gate-check`, including pacing, reverse brake, style, humanizer, graph, memory, and semantic checks when enabled.
5. Finalize only through `chapter finalize`; a chapter with findings completes the review barrier, repair synthesis and a full re-review before it can finalize.
6. After finalize, complete exactly one `canonical_delta_v1`, validate it, explicitly apply the CLI-normalized semantic ledger, and run `chapter close`. Do not start the next chapter before close succeeds.

## Write One Chapter

1. Run or read the latest `continue-write` task package.
2. Read only `50_workbench/writing_tasks/chNNN.md`; do not open the paired JSON, fact inventory, RAG, Graph, TCS, ledger or database artifacts as author context.
3. Apply the `/工程续章` Pre-Write Guide to the Story Brief's desire, opposition, failure, choice, cost, scene actions, protected outcomes and carrier warning.
4. If the Story Brief lacks a required story pressure or protected boundary, stop and return to CLI validation instead of reconstructing control-plane context yourself.
5. Write the draft only to `50_workbench/agent_drafts/chNNN.codex.md` or `chNNN.claude.md`.
6. Before submit, run the Humanizer v4 two-pass self-check mentally:
   - Pass 1 removes meta residue, AI templates, generic significance language, summary lecture, and same-shape paragraphs.
   - Pass 2 preserves each declared perception/decision bias and social mask while strengthening opposing wants, subtext, embodied presence, relationship movement, and emotional aftereffect.
   - Do not manufacture difference with a universal dialogue quota, catchphrases, forced dialect, or fixed appearance paragraphs.
   - Preserve numeric facts, named characters, chapter duty, reader gain, cost, promise payoff, and declared canon/divergence constraints.
   - Do not force every platform into short sentences, dense dialogue, fast pacing, or a cliffhanger; follow the task's market profile.
7. Submit with `longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex`.
8. After all independent reviews, complete `chapter human-review-task`, `human-review-validate`, and `human-review-apply`. Finalize only after a current `accept` decision.
9. Follow `production next` into `chapter semantic-task`; read the final once, write only the declared semantic JSON, then run validate and wait for explicit apply.
10. Close the chapter only with `longform-engine chapter close project.yaml --chapter N --approved-by human` after all materialized views verify.
11. If the gate or semantic validation fails, stop the next-chapter flow and repair the current chapter or semantic candidate.

## Repair A Failed Chapter

1. Run `longform-engine production next project.yaml` until every required independent review is complete for the current candidate hash.
2. Run `longform-engine repair synthesis-task project.yaml --chapter N`; read only its immutable candidate snapshot, `rNN.review_bundle.json`, compact chapter constraints and work order.
3. As `repair_coordinator`, write `rNN.plan.md`: preserve every valid P0/P1 ID and severity, cluster shared roots, order dependencies, define the smallest repair radius and merge all preservation entries. Do not write prose or rejudge reviewers.
4. Run `longform-engine repair synthesis-validate project.yaml --chapter N --file 50_workbench/repair_plans/chNNN/rNN.plan.md`. A repair/preserve conflict must stop at `need-human`.
5. Run `longform-engine repair candidate-task project.yaml --chapter N --agent codex`, then write one complete replacement only to the declared immutable `chNNN.rNN.codex.md` path.
6. Re-submit through `draft submit --overwrite`; only this successful candidate submission consumes one of the two repair rounds.
7. Rerun the complete review barrier. Invalid review JSON or task regeneration does not consume a round; after two submitted rounds with P0/P1, stop at `repair_budget_exhausted` without a third repair command.

## Humanize A Draft

1. Run `longform-engine creative humanize-task project.yaml --chapter N --source draft`.
2. Write the candidate only to the path named in `50_workbench/repair_candidates/`.
3. Run `longform-engine creative humanize-check project.yaml --chapter N --file ...`.
4. If it passes, submit it with `draft submit --overwrite`.
5. If it fails, repeat the humanizer task or write a repair candidate.

## Review A Chapter

Use `editorial review` or `editorial batch-review` for formal review artifacts. The available roles are:

- planning_chief_editor / 策划主编,
- scene_prose_editor / 场景与正文编辑,
- anti_ai_editor / 反 AI 编辑,
- reader_experience_editor / 读者体验编辑,
- canon_fidelity_reviewer / 同人还原编辑（仅同人项目）.

Fanfiction adds `canon_fidelity_reviewer / 同人还原审查员`, which checks voice, relationship stage, ability and world rules, declared divergence causality, canon-character agency, original contribution, collective irrationality, and character-skin-only writing. AU or canon divergence is not an error when the declared change and its consequences support it.

`scene_prose_editor` is selected for every chapter. Other roles remain risk-selected: AI-flavor recurrence adds anti-ai, continuity risk adds planning, opening chapters/major payoff/volume boundaries/carrier repetition add reader experience or planning, and fanfiction adds canon fidelity.

Each selected role receives an isolated manifest and `editorial_context_isolation_v1` metadata file. `editorial_role_review_v2` records `reviewer_instance_id`, host/model identifiers, `context_digest_hash`, `independence_mode`, `review_round`, and confidence. P0/P1 items must cite exact current-chapter excerpts. A role must not read peer results before submission. The aggregate phase alone may read normalized results, and it must retain consensus, conflicts, evidence overlap, severity differences, validated minority P0/P1 findings, and human decisions.

P0/P1 findings stay in the immutable review bundle and must be repaired on the current candidate. Structured `role_id + finding_code` recurrences may update `50_workbench/editorial_patterns/registry.jsonl`; this derived registry is available only to repair coordination, editor risk selection and editor prompts. It never enters author tasks, facts, RAG or Graph and never replaces the current-candidate gate. Use `editorial pattern-status`, `editorial pattern-resolve`, `editorial pattern-suppress`, or explicit `editorial pattern-rebuild`; P1 requires evidence to close, while P2 may expire after three complete chapters.

`editorial need-human` is triggered by unresolved P0/P1 issues or repeated conditional passes. It writes a human-review request artifact; it does not approve, finalize, index, or repair the chapter.

Review output may point to problems, but it must not mutate canonical manuscript, RAG, graph, memory, TCS, or SQLite.

## Hard Stops

- Do not continue to chapter N+1 after `passed=false`.
- Do not skip an unfinalized previous chapter.
- Do not use unpromoted research inbox material as canon.
- Do not trust stale memory, graph, RAG, or TCS after rollback or revise-outline.
- Do not bypass `draft submit` and `chapter finalize`.
