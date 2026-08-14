# Agent-First Document Protocol Phase 2 Evidence

## Scope

Phase 2 implements only Prompt compilation, restricted project overlays, context deduplication, and context-budget failure behavior. It does not activate the Phase 7 production data pipeline and does not change apply/finalize authority.

## Prompt Compiler

- `src/longform_engine/prompting.py` compiles six immutable layers in this order: safety and fact boundaries, task role contract, human-approved overlay, current task and deduplicated context, controlled feedback, output and handoff.
- Optional project overlays live at `00_governance/agent_prompt_overlay.json` and use `agent_prompt_overlay_v1`.
- The overlay allowlist remains owned by `config/agent_roles/registry.json`.
- Protected fields and instruction-like values fail with `prompt_conflict_report_v1`.
- Every conflict reports the field, higher and lower sources, both priorities, reason, and one repair command:

```text
longform-engine agent-task overlay-validate project.yaml --file 00_governance/agent_prompt_overlay.json
```

- Manifest creation records the approved overlay hash. Strict validation rejects stale overlay hashes before registration.
- Input-file contents are not copied into the Prompt control plane. Manuscript, research, and canon text are explicitly untrusted evidence rather than instructions.

## Reader Payoff Context

`reader_payoff_review` now declares exactly three required inputs:

1. `chNNN.reader_payoff.task.md`
2. the current draft
3. `chNNN.reader_payoff.context.json`

The compact `reader_payoff_context_v2` contains one selected representation of chapter expectations, deterministic gate confirmation, the latest prior reward, relevant promises, and bounded quality guidance. Full chapter cards, gate results, quality contracts, and ledgers are excluded.

Every selected source has a path, SHA-256, selection reason, and truncation reason. The context is limited to 6,000 characters and the complete three-input set to 15,000 characters. Task generation fails before manifest registration when either budget is exceeded.

## Writing Context

- Chapter writing continues to register one compiled Markdown input with a seven-file policy ceiling and a 20,000-character ceiling.
- Context plans now record source hashes, selection reasons, and truncation reasons.
- More than six required featured characters, more than four active relationships per featured character, more than eight referenced abilities, unknown explicit abilities, or omitted active foreshadows fail explicitly.
- Character contracts and approved voice samples fail when they exceed their reserved allocation; they are no longer silently trimmed.

## Tests

Dedicated coverage is in `tests/test_agent_document_protocol_phase2.py` and includes:

- fixed Prompt layer order and reproducible overlay hash;
- protected-field and embedded-instruction overlay rejection;
- source-file Prompt injection isolation;
- stale overlay hash rejection;
- realistic large chapter-card and gate deduplication;
- 6K/15K payoff limits and no-pollution failure;
- character, relationship, ability, and foreshadow overflow failures.

Final Phase 2 verification on 2026-08-13:

- `python -m pytest -q`: 342 passed.
- `python scripts/sync_skill_references.py --check`: passed.
- `python scripts/build_resource_manifest.py --check`: passed.
- `python scripts/validate_skills.py`: passed.
- `python scripts/release_surface_guards.py`: passed.
- `git diff --check`: passed.

Phase 3-8 remain locked. Completing Phase 2 does not imply `ready_for_data_pipeline: true`.
