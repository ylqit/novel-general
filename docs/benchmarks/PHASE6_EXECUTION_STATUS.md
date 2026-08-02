# Phase 6 Execution Status

- Status date: `2026-07-31`
- Formal scenario: `docs/benchmark_scenarios/PHASE6_ORIGINAL_COMPARISON_V1.json`
- Claim status: `not eligible`

## Completed Infrastructure

- `[x]` Benchmark run metadata records scenario SHA-256, host product, exact host/model labels and workflow version.
- `[x]` `benchmark technical-record` records production metrics without producer-supplied literary scores.
- `[x]` `benchmark source-attach` records exact reviewed chapter hashes and a source Merkle root.
- `[x]` `benchmark blind-pack` randomizes two 10-chapter entries and separates the public pack from the private mapping.
- `[x]` `benchmark blind-template`, `blind-submit` and `blind-aggregate` require complete per-chapter scores, independence attestations, three unique reviewer instances and three unique sessions.
- `[x]` Aggregation revalidates current manuscript hashes and writes per-chapter median scores with provenance.
- `[x]` `benchmark compare` rejects diagnostic/self-declared scores, mismatched host/scenario/model metadata and missing blind provenance.
- `[x]` `benchmark rag-production-template` and `rag-production-run` enforce 500 real final chapters, at least 50 source-verified queries, seven retrieval categories, production embedding/reranker and no fallback.
- `[x]` Unit/integration fixtures cover identity leakage, source mutation, insufficient/duplicate judges, formal aggregation and production RAG evidence.

## Available Real Evidence

- `[x]` Current-protocol Codex + longform five-chapter smoke completed at `novels/benchmark-codex-original-phase6-current-v1`.
- `[x]` Run `codex-longform-phase6-smoke-5-current-v1` completed all eight `book_ideation` decisions, `book_design`, `outline_design`, five explicit finalizations and the post-final graph/memory/character/pacing/RAG updates.
- `[x]` `benchmark validate` reports `complete=true`, `acceptance_passed=true`, zero P0 contradictions, zero residual canonical pollution, one candidate repair and zero final `need-human` decisions.
- `[x]` All five writing manifests use seven input files. Average compiled context is `12,608` characters and the maximum is `14,437`, below the `20K / 7 files` budget.
- `[x]` Every technical record contains SHA-256 for its writing work order, AgentTaskManifest, reviewed final manuscript and gate result. The five-final source Merkle root is `7cb01756d6b1d13a2abf7704373ca39b7d0d4c952aa4dc3cfc1ca60187c4dd18`.
- `[x]` The local `bge-m3` profile built 21 chunks and 42 persisted embeddings for the five final chapters; graph and memory validation both completed with zero errors and zero warnings.
- `[x]` After chapter 5, `production next` returns `ready_for_continue_write` for chapter 6.
- `[x]` The same current-protocol project was extended by ten real chapters, from chapter 6 through chapter 15, under run `codex-longform-phase6-extended-15-current-v1`.
- `[x]` The 15-chapter extension report has `complete=true` and `acceptance_passed=true`; all 15 chapters have real draft, gate and explicit final artifacts, and the board reports `15 finalized / 0 gate_failed / 0 need_human / 0 active_agent_tasks`.
- `[x]` The 15-chapter technical records report zero P0 contradictions, zero canonical pollution and zero final `need-human` decisions. The run contains six recorded repairs in total; five occurred in the ten-chapter extension at chapters 7, 8, 10, 13 and 15.
- `[x]` All 15 writing tasks stay within `7 files / 20K chars`; average compiled context is `14,405.667` characters and the maximum is `16,087`.
- `[x]` Chapter 15 completed conditional direction selection, mandatory semantic review, observed reader-payoff review, four isolated editorial roles, explicit finalize, and graph/memory/character/pacing apply with no unresolved editorial item.
- `[x]` The 15-final source manifest has Merkle root `e57a21eb97e8a7a200cf06db7b990c822430ff5a79cc8fb34fdddea42463d2ca`; Semantic RAG contains 57 chunks and 112 persisted embeddings.
- `[x]` After chapter 15, `production next` returns `ready_for_continue_write` for chapter 16.
- `[~]` The extension has no literary scores or independent judges. It validates longer controlled production and feedback/state continuity, not superiority over `novel-skill`.
- `[~]` The smoke exposed two implementation defects: deterministic status extraction could attach an injury to nearby characters, and Chinese “小案闭环/调查权限” was not classified as a major payoff. Both were fixed with regression tests and replayed before downstream use; the run records the incidents instead of hiding them.
- `[~]` This run has no literary scores or independent judges. It proves current-protocol controlled production, not literary superiority.
- `[x]` Codex + longform has a historical five-chapter original smoke at `novels/benchmark-codex-original-smoke-clean-v2`; its benchmark still validates as complete with `acceptance_passed=true`.
- `[~]` The project predates the later mandatory `book_ideation` protocol. Under the current engine, `production next` requests that missing project task instead of pointing directly to chapter 6, so this is not a current-version continuation smoke.
- `[~]` The smoke is self-reviewed and only five chapters; it is diagnostic, not a formal blind comparison.
- `[x]` Production RAG preflight on this project reports missing formal models and only five final chapters, and writes no claim evidence.
- `[x]` Phase 5 has 50/200/500-chapter synthetic engineering RAG measurements.
- `[~]` Phase 5 uses fixed hash vectors and is not production-model semantic evidence.

## v0.3.0 Local Release Candidate

- `[x]` Package/runtime/README install version is unified at `0.3.0`; resource manifest and self-contained Skill validation pass.
- `[x]` Full regression passes with `288 passed`; release surface guards remain green.
- `[x]` Current-source `rc2` builds `longform_novel_engine-0.3.0-py3-none-any.whl` and `longform_novel_engine-0.3.0.tar.gz`; wheel audit reports 96 entries and sdist audit reports 175 entries.
- `[x]` A fresh isolated pipx install with `[semantic]` reports engine `0.3.0`, both bundled Skills current, `doctor_v1.ok=true`, valid template resources and a strict first `book_ideation` work order.
- `[x]` The real user Codex Skill is updated to engine version `0.3.0`; the benchmark project has Semantic models ready, fallback inactive and 112 persisted embeddings after chapter 15.
- `[~]` `release check` reports 16 passes, one expected missing-tag warning and one dirty-worktree failure. No commit, tag, push or GitHub Release was performed.
- `[~]` Codex App must be restarted before claiming the updated `0.3.0` Skill was rediscovered by the host UI.

## Still Required

- `[ ]` Pair at least 10 exact longform chapters with a same-host, same-model `novel-skill` run and complete the formal blind protocol; the completed 15-chapter longform-only run is controlled production evidence, not a paired comparison.
- `[ ]` Codex + novel-skill 10-chapter run under the same Codex host/model.
- `[ ]` Claude Code + longform five-chapter smoke and 10-chapter formal run.
- `[ ]` Claude Code + novel-skill 10-chapter run under the same Claude host/model.
- `[ ]` Three genuinely independent, identity-blind reviewers for every paired chapter.
- `[ ]` A 500-final-chapter Chinese corpus with source-annotated production-model RAG queries.
- `[ ]` Both formal comparison reports and external evidence review.
- `[ ]` Any comparison with `claim_eligible=true`.

The repository must not claim that literary quality is already superior to `novel-skill` while any item above remains open.
