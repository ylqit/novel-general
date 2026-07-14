# Longform Novel Iron Laws

These rules apply to Codex, ClaudeCode, and any other Agent-Skill user of `longform-novel-engine`.

1. Agent-Skill mode is the default production path. `writing.mode = agent_skill` means Codex or ClaudeCode writes prose, and the CLI controls context, gates, RAG, graph, SQLite, rollback, and persistence.
2. Skill mode does not require an extra LLM API key. Do not ask the user for provider API keys unless they explicitly choose optional `api_provider` mode or a local model service.
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
