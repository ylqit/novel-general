# Agent-first Document Protocol Phase 8 Acceptance

## Scope

Phase 8 verifies the chapter artifact lifecycle and the production state machine after the Phase 7 data pipeline was connected. It separates two claims:

- **Protocol acceptance**: deterministic fixtures can complete the current Agent role protocol through chapter 20 without bypassing validate, finalize, semantic apply, or close.
- **Literary production acceptance**: a real Codex-authored SAO fanfiction run must still be executed and reviewed. Protocol fixtures are not literary evidence.

The preserved v0.3.1 SAO failure project under `novels/sao-aincrad-return-route-v031` was not edited or reused.

## Artifact Contract

Closed chapters outside the two-chapter active buffer now move these non-canonical materials into `70_runtime/artifacts/chapters/chNNN.zip`:

- draft and submitted prose copies;
- work orders, manifests, context packets, Agent outputs, normalization diagnostics and validation reports;
- gate, Humanizer, payoff, editorial, pacing and semantic task materials;
- finalization metadata, historical TCS projections, run reports and chapter transaction reports.

The loose evidence retained for every archived chapter is bound by SHA-256 in `chapter_artifact_archive_v3`:

- `40_manuscript/final/chNNN.md`;
- `30_state/semantic_ledger/chNNN.json`;
- `30_state/chapter_closures/chNNN.json`.

Current graph, character state, foreshadow state, current TCS, RAG and SQLite remain rebuildable materialized views. Global task index and event files remain compact control-plane projections; full chapter task bodies live in the audit package.

The v3 package is content-addressed. A logical artifact that is byte-identical to retained final evidence records a hash-bound `retained_role` reference and stores no second prose copy. Other byte-identical logical paths share one `_audit/blobs/<sha256>` member while all restore paths remain available. `_audit/manifest.json` is embedded in the ZIP and must exactly match the external sidecar except for the ZIP's own hash. This prevents a rewritten sidecar from silently redefining the package. `artifacts compact --dry-run` reports logical files, unique blobs, bytes and duplicates before it writes or removes anything. Existing v2 packages remain verifiable and restorable.

`artifacts verify` rejects an archive with a wrong hash, a missing or mismatched embedded manifest, an invalid content-addressed member, undeclared or duplicate ZIP members, duplicate logical paths, wrong sizes, stale retained evidence, a missing old-chapter archive, an archived active-buffer chapter, or an archived file that still exists loose. `artifacts restore` intentionally makes verification fail with loose duplicates until the chapter is compacted again.

## Production Evidence

The current protocol completed a real API-level chapter-one sequence:

```text
production next
-> continue_write / agent-task brief
-> chapter_author Markdown / agent-task result-validate
-> draft submit / deterministic gate
-> reader_payoff_reviewer JSON / result-validate / payoff-validate
-> explicit finalize
-> chapter_semantic_archivist JSON / result-validate / semantic-apply
-> explicit chapter close
-> production next chapter 2
```

The 20-chapter engineering replay then used the same write, result validation, submit, finalize, semantic apply and close APIs in strict order. Observed milestones:

| Milestone | Audit archives | Active buffer | Loose archived duplicates | Next chapter |
| --- | ---: | --- | ---: | ---: |
| chapter 1 | 0 | 1 | 0 | 2 |
| chapter 5 | 3 | 4-5 | 0 | 6 |
| chapter 20 | 18 | 19-20 | 0 | 21 |

All fixture gates passed without P0/P1 findings and every semantic ledger was applied before close. The test restores chapter 1 after the chapter-20 assertion and confirms that loose duplicates become visible and verification changes to failed, proving restore is observable rather than silent.

## Defect Found During Acceptance

The first chapter-one run found that payoff context correctly recorded built-in quality profile hashes, but the shared normalizer incorrectly resolved `config/quality_profiles/...` under the novel project root. The source registry now distinguishes `project` from `engine_resource` authority. Engine resources are hash-verified against the packaged resource root, cannot escape that root, and never become Agent-usable canonical references.

## Remaining SAO Gate

The deterministic chapter-20 replay proves the engine can reopen the 20-chapter lane and route to chapter 21. A separate real Codex SAO run has now completed chapters 1-5 with five finals, semantic ledgers and closures; three v3 audit archives; a two-chapter active buffer; no unresolved P0/P1; and `production next` routed to chapter 6. Its no-prose audit is recorded in `docs/audits/V0_3_2_FIVE_CHAPTER_ENGINEERING_SMOKE.json`.

This evidence is the `v0.3.2` engineering release gate. It does not replace 20 chapters of real production, fanfiction/OOC human review, or literary acceptance. Those remain deferred and cannot support a superiority claim over `novel-skill`.

## Final Validation

- Phase 8 and artifact tests: 7 passed.
- Complete project regression: 386 passed.
- Current v0.3.2 release regression after the pacing/editorial/benchmark fixes: 416 passed.
- Skill reference sync, resource manifest, Skill validation and release surface guards: passed.
- Agent data-pipeline readiness: `ready_for_data_pipeline: true` with 8 checks passed and 0 failures.

## Reproduction

```powershell
python -m pytest -q tests/test_artifacts.py tests/test_agent_document_protocol_phase8.py
python -m pytest -q
python scripts/check_agent_data_pipeline_readiness.py --json
python scripts/sync_skill_references.py --check
python scripts/build_resource_manifest.py --check
python scripts/validate_skills.py
python scripts/release_surface_guards.py
```
