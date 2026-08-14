# Agent-First Document Protocol Phase 5: Isolated Functional Completion

## Scope

Phase 5 completes the Agent-first document protocol in a read-only isolation harness. It does not connect the protocol to `production next`, task lifecycle mutation, apply/finalize, chapter close, or canonical materialization.

The implementation boundary is:

```text
manifest
-> strict role/context compilation
-> host-neutral Prompt and work order rendering
-> declared output parsing
-> structural result validation
-> CLI fact normalization and evidence validation
-> read-only result
```

No step registers a task, advances a lifecycle state, calls a provider, or writes final/Bible/outline/graph/memory/TCS/RAG/SQLite.

## Implementation

`src/longform_engine/agent_isolation.py` owns the Phase 5 composition boundary:

- Exhaustive objectives for every non-legacy task type; no generic fallback exists.
- Complete coverage for every specialist editorial role.
- SHA-256 context deduplication and deterministic required/optional ordering.
- Exact source path, hash, character count, tier, and selection-reason records.
- Source Prompt-injection quarantine: source contents are evidence and are never embedded into the control Prompt.
- Isolated-review rejection for author reasoning, chain-of-thought files, peer results, and editorial aggregate inputs.
- Identical semantic Prompt and hash for Codex and Claude Code; only a host display comment differs.
- Explicit refusal to compile new `graph_extract`, `memory_extract`, or `character_memory` work packages.

`src/longform_engine/agent_results.py` now parses declared output files with exact-path, UTF-8, duplicate-key-safe JSON, and document-companion checks.

`src/longform_engine/agent_normalization.py` retains compatibility readers for:

- `semantic_graph_update_v1`
- `semantic_memory_v1`
- `character_memory_cards_v1`

The adapters resolve evidence against the current declared source. Missing historical source hashes produce an explicit warning; a supplied wrong hash is rejected; non-unique text fragments enter `need-human`. Compatibility normalization does not apply data or revive those tasks in the new production chain.

## Output Modes

All four modes have normal, boundary, and failure coverage:

| Mode | Positive/boundary evidence | Failure evidence |
| --- | --- | --- |
| `markdown_prose` | complete multi-paragraph candidate; exact minimum boundary | JSON/control material, analysis heading, wrong path, invalid UTF-8 |
| `compact_review_json` | pass review and 500-character evidence boundary | unsupported fields, P0/P1 without exact evidence, wrong span/ref |
| `strict_delta_json` | changed and unchanged coverage | changed state without evidence, invalid current source/hash |
| `document_index_bundle` | substantive Markdown plus compact exact-heading index | missing heading, wrong companion path, duplicate JSON keys |

## Responsibility Isolation

The same source compiles to distinct role IDs, Prompt hashes, missions, and output responsibilities for:

- chapter author
- repair author
- Humanizer
- expansion writer
- semantic reviewer

Role metadata tampering fails strict validation. Review contexts cannot contain author reasoning, peer review results, or aggregate results. Approved project overlays remain additive and cannot replace output paths, evidence duty, lifecycle commands, schema, or canonical boundaries.

## Compatibility And Production Lock

The release guard checks that:

- `production.py` does not import `agent_isolation` or `agent_results`.
- Phase 5 isolation modules contain no direct writers or external LLM calls.
- Legacy task types remain compatibility-read-only in the isolated protocol.
- The v0.3.1 production, apply/finalize, and canonical behavior remains unchanged.

Phase 6 remains unexecuted. The new data pipeline is still locked until a separate readiness checker and all Phase 6 evidence pass.

## Verification

Primary regression coverage is in `tests/test_agent_document_protocol_phase5.py`:

- every non-legacy task type and all eight specialist editorial roles compile, render, write a fixture output, parse, normalize, and validate in isolation;
- all four output modes cover normal, boundary, and failure behavior;
- legacy graph/memory/character payloads normalize read-only and cannot compile new packages;
- Prompt injection, role swapping, review leakage, budget overflow, duplicate content, bad hash/span/ref, path mismatch, invalid UTF-8, duplicate JSON keys, and overlay escalation are rejected;
- canonical hashes and task index/event files remain unchanged;
- Codex and Claude Code share the same semantic work-order hash.

Verified results:

- Phase 5 focused matrix: `8 passed`.
- Phase 0-5, task protocol, production lock, and integrity combination: `80 passed`.
- Complete pytest suite: `367 passed`.
- Ruff: passed.
- Skill validation: passed.
- Resource manifest check: current.
- Skill reference synchronization: passed.
- Release surface guards: passed.
- Fresh `0.3.1` wheel build and resource audit: passed; all Phase 1-5 protocol modules are present.
- Git diff whitespace check: passed.
