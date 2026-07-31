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

Canonical state changes must go through CLI commands: `draft submit`, `chapter finalize`, `memory ... apply`, `graph ... apply`, `research promote`, `revision rollback`, or `db rebuild`.

## Creative Decisions And Quality Contract

- After `open-book`, follow `production next` through `book_ideation`. Each work order asks one core question and offers two or three options with tradeoffs. Do not infer a selection from silence.
- Write only `book_ideation_candidate_v1`; the CLI saves one explicitly selected/provided answer through `intelligence apply --approved-by human`.
- When `production next` requests `chapter_direction`, offer two or three causally distinct directions and record the user's explicit selection. Do not write chapter prose in this task.
- Read the `effective_quality_contract_v1` embedded in the chapter card/writing brief. It combines market, genre, phase, approved baseline, and project overrides, but is not a universal sentence-length, dialogue-density, pace, or cliffhanger template.
- Never add a finalized chapter to the approved style baseline automatically. Only the explicit `quality baseline-approve` CLI command may add its prose-free craft fingerprint.

## `/工程续章` Pre-Write Guide

Use this guide before writing any new chapter draft. `/工程续章` is the primary Chinese engineering entry for continuing a chapter; it maps to `longform-engine continue-write project.yaml --chapter N` and must not be replaced by legacy command names.

Before prose is written, the Agent must confirm these inputs from `50_workbench/writing_tasks/chNNN.json` and `chNNN.md`:

- User preference: target audience, writing style, taboo experience, automation level, and any latest user instruction from Creative Brief or the current task.
- Automatic fallback: if a preference is missing, use the task package defaults instead of asking for a model/API key; record uncertainty in the draft notes only if the task asks for it.
- Pacing precheck: read `event_recommendation`, `constraint_packet.event_matrix`, recent pacing history, soft-event requirement, fast quota, and any `pacing_review` or gate history warnings.
- Tail-hook declaration: state the intended chapter-end hook in the draft plan and preserve it in the final scene.
- Forbidden reveal confirmation: read `reverse_brake`, `forbidden_reveals`, `this_chapter_must_not_solve`, and `must_keep_suspense`; do not close these items unless `closure_allowed=true` and `allowed_reveal_level=full`.
- Failure repair path: if the task or previous gate says blocked, do not continue; run `repair-chapter --plan-only`, `creative humanize-task`, `editorial review`, `gate-waiver`, `revision branch`, or `revision rollback` according to the artifact.

Required five-step closed loop:

1. Generate or read the `continue-write` task package.
2. Agent writes only to `50_workbench/agent_drafts/chNNN.codex.md` or `chNNN.claude.md`.
3. Submit the candidate with `draft submit`.
4. Run or inspect `gate-check`, including pacing, reverse brake, style, humanizer, graph, memory, and semantic checks when enabled.
5. Finalize only through `chapter finalize`; failed or conditional chapters go to repair, waiver, branch, or rollback instead of next-chapter writing.

## Write One Chapter

1. Run or read the latest `continue-write` task package.
2. Read `50_workbench/writing_tasks/chNNN.md` and the paired JSON.
3. Apply the `/工程续章` Pre-Write Guide, including user preference, automatic fallback, pacing precheck, tail-hook declaration, forbidden reveal confirmation, and failure repair path.
4. Confirm the task includes Creative Brief, Writer Craft Brief, RAG, Graph, TCS, Character Memory, Style Memory, Event Matrix, Reverse Brake, gate history, and Humanizer v3 rules.
5. Write the draft only to `50_workbench/agent_drafts/chNNN.codex.md` or `chNNN.claude.md`.
6. Before submit, run the Humanizer v3 self-check mentally:
   - Pass 1 removes meta residue, AI templates, generic significance language, summary lecture, and same-shape paragraphs.
   - Pass 2 strengthens dialogue difference, action-carried psychology, paragraph rhythm, sensory anchors, and ending hook.
   - Preserve numeric facts, named characters, chapter duty, reader gain, cost, promise payoff, and declared canon/divergence constraints.
   - Do not force every platform into short sentences, dense dialogue, fast pacing, or a cliffhanger; follow the task's market profile.
7. Submit with `longform-engine draft submit project.yaml --chapter N --file 50_workbench/agent_drafts/chNNN.codex.md --agent codex`.
8. If the gate passes, finalize only with `longform-engine chapter finalize project.yaml --chapter N --approved-by human`.
9. If the gate fails, stop the next-chapter flow and repair the current chapter.

## Repair A Failed Chapter

1. Read `50_workbench/gate_artifacts/chNNN/gate_result.json`.
2. Run or read `longform-engine repair-chapter project.yaml --chapter N --plan-only`.
3. Use the Creative Rewrite Brief in `repair_plan.md`:
   - preserve canonical facts and chapter duty,
   - delete or reduce failed material,
   - add evidence spans for motivation, relationship, ability, and foreshadow changes,
   - align Character Memory and TCS,
   - apply Humanizer v3.
4. For a candidate task, run `longform-engine repair-chapter project.yaml --chapter N --candidate-only --agent codex`.
5. Write only to the candidate path named by the task.
6. Re-submit through `draft submit`; never copy the repair candidate into final.

## Humanize A Draft

1. Run `longform-engine creative humanize-task project.yaml --chapter N --source draft`.
2. Write the candidate only to the path named in `50_workbench/repair_candidates/`.
3. Run `longform-engine creative humanize-check project.yaml --chapter N --file ...`.
4. If it passes, submit it with `draft submit --overwrite`.
5. If it fails, repeat the humanizer task or write a repair candidate.

## Review A Chapter

Use `editorial review` or `editorial batch-review` for formal review artifacts. The available roles are:

- planning_chief_editor / 策划主编,
- writing_agent / 写作特工,
- anti_ai_editor / 反 AI 编辑,
- serial_verifier / 连载核实官,
- reader_quality_reviewer / 读者质量审查员,
- executive_editor / 总编辑.

Fanfiction adds `canon_fidelity_reviewer / 同人还原审查员`, which checks voice, relationship stage, ability and world rules, declared divergence causality, canon-character agency, original contribution, collective irrationality, and character-skin-only writing. AU or canon divergence is not an error when the declared change and its consequences support it.

Roles are selected by current risk, not by an all-roles-every-chapter rule. Normal chapters use one writing or reader-quality role; AI-flavor recurrence adds anti-ai, continuity risk adds serial-verifier, major payoff or volume boundaries add planning plus reader-quality, fanfiction adds canon-fidelity, and P0/P1 risk adds executive review.

Each selected role receives an isolated manifest and `editorial_context_isolation_v1` metadata file. `editorial_role_review_v2` records `reviewer_instance_id`, Agent product/version, `context_digest_hash`, `independence_mode`, `review_round`, and confidence. P0/P1 items must cite exact current-chapter excerpts. A role must not read peer results before submission. The aggregate phase alone may read normalized results, and it must retain consensus, conflicts, evidence overlap, severity differences, validated minority P0/P1 findings, and human decisions.

Open findings enter `50_workbench/quality_feedback/registry.jsonl` with stable IDs, recurrence, TTL, and resolution state. At most five active task-relevant items enter a later work order. Use `quality feedback-status`, `quality feedback-resolve`, or `quality feedback-suppress` to inspect or close them. Registry failure is non-blocking because feedback is workbench guidance, not canonical story state.

`editorial need-human` is triggered by unresolved P0/P1 issues or repeated conditional passes. It writes a human-review request artifact; it does not approve, finalize, index, or repair the chapter.

Review output may point to problems, but it must not mutate canonical manuscript, RAG, graph, memory, TCS, or SQLite.

## Hard Stops

- Do not continue to chapter N+1 after `passed=false`.
- Do not skip an unfinalized previous chapter.
- Do not use unpromoted research inbox material as canon.
- Do not trust stale memory, graph, RAG, or TCS after rollback or revise-outline.
- Do not bypass `draft submit` and `chapter finalize`.
