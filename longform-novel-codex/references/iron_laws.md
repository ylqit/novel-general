# Longform Novel Iron Laws

These rules apply to Codex, ClaudeCode, and any other Agent-Skill user of `longform-novel-engine`.

1. Agent-Skill mode is the default production path. `writing.mode = agent_skill` means Codex or ClaudeCode writes prose, and the CLI controls context, gates, RAG, graph, SQLite, rollback, and persistence.
2. Skill mode does not require an extra LLM API key. Do not ask the user for provider API keys; the public runtime exposes no script-internal provider mode.
3. Files are the source of truth. SQLite is only a rebuildable derived index.
4. Agents must not free-write official chapters directly in chat when a project exists. Always use the command workflow.
5. Agents may write chapter prose only under `50_workbench/agent_drafts/`.
6. Agents must not directly write or edit `40_manuscript/final/`, `60_rag/`, `30_state/story_graph.json`, or `70_runtime/db/`.
7. `draft submit` is the only legal path from Agent draft to managed draft state.
8. `chapter finalize` is the only legal path from managed draft to final manuscript.
9. A failed gate blocks the next chapter. If `gate_result.json` has `passed=false`, run repair, waiver, branch, or rollback workflow before continuing.
10. A previous chapter that is not final blocks the next `continue-write`.
11. Failed drafts do not enter RAG, story graph, final manuscript, or final SQLite state.
12. Unpromoted `50_workbench/research_inbox/` material must not enter canon, RAG, story graph, or writing tasks.
13. Only `research promote` can move reviewed material into `10_bible/research_canon.jsonl`.
14. Rollback must preserve later material as detached drafts and mark chapter cards, writing tasks, RAG, graph, summaries, and SQLite status stale.
15. Official chapter prose must not contain TODO notes, writing instructions, character labels, AI self-reference, or prompt residue.
16. `creation.mode = fanfiction` is a first-class production mode. Declared character names, relationships, worlds, abilities, timelines, continuations, prequels, AU, divergences, and crossovers are valid creative inputs.
17. Fanfiction rights status and commercial intent are user-claimed advisory metadata. They must produce warnings and provenance, but must not block task creation, validation, finalization, or export.
18. Allowing fanfiction does not allow continuous source-prose reproduction. Canon files store paraphrased facts plus source hash/span; chapters must not copy, split across fields, or reconstruct source passages.
19. Fanfiction Agents read only manifest-declared source files and canon digests. They must not scan an undeclared source work, another novel project, or a research inbox for extra canon.
20. Humanizer v3 must preserve facts, characters, chapter duty, and reader payoff. A candidate that is empty, changes numeric facts, drops canonical characters, or exceeds the configured rewrite ratio must fail or request human review.
21. A planned `reader_gain` is not an observed payoff. When `production next` requires `reader_payoff_review`, the Agent must cite the current draft and pass `quality payoff-validate` before explicit finalization.
22. Unfinalized payoff reviews remain in `50_workbench/quality_reviews/`. Only `chapter finalize` may write `reader_reward_entry_v2` and `30_state/quality/structure_history.jsonl`.
23. Structure observation must not impose a universal cliffhanger, battle, reversal, upgrade, short-sentence, or dialogue template. One repeated dimension is a warning; only combined structure, language, and payoff repetition may block.
24. `book_ideation` asks one question per round. Agents may offer options, but only a user's explicit selection or provided answer may enter `10_bible/creative_decisions.json`.
25. `chapter_direction` is conditional. It may interrupt guided, abstract, boundary, major-turn, repeated-repair, or multi-plotline chapters, but must not add a mandatory choice to every stable chapter.
26. The effective quality contract is compiled from resource profiles and explicit project state. Agents must not treat it as a fixed sentence, dialogue, pace, or ending template.
27. Approved style baselines never auto-expand. Only an explicit CLI command with a named human approver may add a finalized chapter's prose-free craft observation.
28. Editorial roles are risk-selected and context-isolated. A role reads only its manifest inputs and must not read peer results before submitting `editorial_role_review_v2`.
29. Editorial aggregate must preserve evidence-backed disagreement. A minority P0/P1 cannot be removed by majority vote and requires an explicit human decision.
30. Quality feedback is workbench guidance under `50_workbench/quality_feedback/`, never a canonical fact source. At most five active task-relevant items may be carried; resolved, suppressed, and expired items must not enter new work orders.
31. P0 feedback never expires automatically, P1 may resolve only after two completed chapters without recurrence or explicit evidence, and P2 has a bounded TTL. Registry failure must not block or roll back final/RAG/graph/TCS/SQLite.
32. Final manuscript is the prose evidence source; a semantic summary routes retrieval but cannot prove a relationship, memory, foreshadow, timeline, or world-state change by itself.
33. Each newly finalized chapter must use one Agent-authored `canonical_delta_v1`; only the CLI may normalize it into the internal semantic ledger. The default production path must not create separate graph, chapter-memory, and character-memory Agent extraction tasks for the same final.
34. `chapter semantic-apply` is the only default path that materializes the chapter semantic ledger into graph, character current views, foreshadow state, TCS, RAG, and SQLite. It must validate exact source spans and apply transactionally.
35. `chapter close` is required before continuing to the next chapter. It preserves final, semantic ledgers, planning ledgers, and current materialized state while allowing older workbench artifacts to move into verified chapter audit archives.
36. SQLite and vector indexes are rebuildable derivatives. They must never override final text or the evidence-bound semantic ledger.
