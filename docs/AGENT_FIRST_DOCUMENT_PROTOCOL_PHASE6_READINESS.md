# Agent-First Document Protocol Phase 6: Data Pipeline Readiness

## Decision

Phase 6 is complete. The shared checker returns:

```json
{
  "schema": "agent_data_pipeline_readiness_v1",
  "ready_for_data_pipeline": true
}
```

This result authorized implementation of Phase 7. Phase 7 has since connected the Agent-first document protocol to `production next` while preserving explicit apply/finalize, chapter close, and canonical materialization boundaries. See `AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE7_PRODUCTION_PIPELINE.md`.

## Readiness Contract

`src/longform_engine/agent_protocol_readiness.py` owns a read-only, stable report containing:

- Git commit and deterministic dirty-tree SHA-256.
- Explicit exclusions for the evidence and report files, avoiding self-referential hashes.
- Engine version.
- Codex and Claude Code Skill versions and tree hashes.
- Prompt role resource hash.
- Protocol implementation/test/CI surface hash.
- Phase 0-5 checklist completion.
- Full pytest and quick contract command evidence.
- Realistic reader-payoff context metrics.
- Prompt injection, role overreach, self-review, bad evidence, rollback, and no-pollution test references.

Evidence is bound to current code by `protocol_surface_sha256`. A change to the Agent protocol modules, role resources, Phase 0-6 tests, CI workflow, or readiness/release guard scripts makes the evidence stale and changes readiness to blocked.

## Interfaces

Repository CLI:

```text
longform-engine agent-task readiness --repository . --json
```

Local/CI guard:

```text
python scripts/check_agent_data_pipeline_readiness.py --json
```

Phase 7 code calls the shared readiness boundary before enabling the new route. Missing, malformed, stale, or incomplete authorization raises `AgentDataPipelineBlocked` or `AgentProductionPipelineError`.

CI runs the same guard after the complete pytest suite. Release guards verify the checker, local script, CI command, current protocol surface hash, and bound Phase 6 evidence hash. `agent_pipeline.py` now owns the deliberate Phase 7 integration boundary.

## Realistic Payoff Evidence

The Phase 6 fixture uses deliberately oversized source artifacts:

| Input | Characters |
| --- | ---: |
| Full chapter card | 71,643 |
| Full gate result | 126,346 |
| Draft chapter | 3,643 |

The compiled reader-payoff task produces:

| Metric | Result | Limit |
| --- | ---: | ---: |
| Input files | 3 | 3 |
| Compact context | 4,404 | 6,000 |
| Total input characters | 10,161 | 15,000 |

The large unused card, gate, and quality-contract material is not duplicated into the work order.

## Security And Integrity Evidence

- Prompt-injection text is quarantined as untrusted source evidence and does not enter the control Prompt.
- The chapter-author role cannot replace an isolated semantic reviewer; role metadata tampering fails strict validation.
- Overlay escalation cannot change evidence, schema, paths, severity, lifecycle, or canonical authority.
- Wrong source hash, exact span, source ref, duplicate JSON key, UTF-8, and output path are rejected.
- Existing transaction tests restore touched canonical paths after apply failure.
- Invalid result validation and isolated task matrices leave final, Bible, outline, graph, memory, TCS, RAG, SQLite, task index, and lifecycle events unchanged.

## Machine Evidence

- Test evidence: `docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_EVIDENCE.json`
- Readiness report: `docs/baselines/AGENT_FIRST_DOCUMENT_PROTOCOL_PHASE6_READINESS.json`
- Phase 6 tests: `tests/test_agent_document_protocol_phase6.py`

Verified command results:

- Complete v0.4.0 pytest: `435 passed in 590.55s`.
- Skill reference synchronization: passed.
- Resource manifest check: passed.
- Skill validation: passed.
- Release surface guards: passed.
- Ruff: passed.
- Wheel and sdist audit: passed.
- Git diff whitespace check: passed.

## Remaining Lock

Phase 6 itself claims no production route or 1/5/20 chapter acceptance result. Phase 7 now supplies the production-route and semantic-transaction evidence; Phase 8 remains responsible for artifact and 1/5/20 chapter acceptance.
