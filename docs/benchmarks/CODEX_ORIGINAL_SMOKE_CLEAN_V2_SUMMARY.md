# Codex Original Smoke Clean V2

## Outcome

- Run ID: `codex-original-smoke-5-clean-v2`
- Scenario: `照骨司夜录`
- Host: `Codex App 26.721.11231.0`
- CLI: `codex-cli 0.145.0`
- Model label: `gpt-5-codex-session`
- Scenario SHA-256: `3645238ed76a8ed5f39dbf92bac9f4e424497c6dacd4dd7a8f6b647db401c868`
- Structural result: `complete=true`
- Smoke acceptance: `acceptance_passed=true`
- Review status: `codex-self-review`, non-blind

Runtime evidence is stored under the ignored directory
`novels/benchmark-codex-original-smoke-clean-v2/70_runtime/benchmarks/codex-original-smoke-5-clean-v2/`.
The benchmark records contain metrics, provenance, commands, and hashes, but no manuscript body.

## Method Boundary

This run is a clean replay of the Codex-authored book design, outline, five chapter candidates, and two semantic review results from `codex-original-smoke-5-v1`. All tasks, validation reports, transactions, final files, RAG chunks, graph state, TCS files, and SQLite rows were rebuilt in a new project.

It proves that the corrected production pipeline accepts and safely applies the existing Agent outputs. It is not a second independent literary sample, blind review, or superiority result.

## Chapter Evidence

| Chapter | Duty | Final gate | Content repairs | Semantic reviews | Input files | Work order chars |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | 异常发现 | PASS | 0 | 1 | 7 | 8,787 |
| 2 | 关系冲突 | PASS | 0 | 0 | 7 | 10,139 |
| 3 | 错误判断 | PASS | 0 | 1 | 7 | 11,116 |
| 4 | 代价兑现 | PASS | 0 | 0 | 7 | 9,681 |
| 5 | 小案闭环 | PASS | 0 | 0 | 7 | 10,630 |

Aggregate evidence:

- Average work order: `7 files / 10,070.6 characters`.
- Final chapters, gate results, RAG chunks, and TCS files: `5` each.
- Applied milestone semantic reviews: chapters `1` and `3`.
- P0 contradictions: `0`.
- Canonical pollution incidents: `0`.
- Content repairs: `0`.
- Need-human count: `0`.
- Graph validate/check: no errors, issues, or warnings.
- Agent tasks: `9 applied / 0 active`.
- SQLite: `5` final chapters, `21` chunks, `5` gate results, no stale rows.
- `production next` points to chapter `6`.
- Chapter 2-5 work orders contain no stale gate-failed, graph-frozen, finalize-waiting, or repair-plan feedback.

## Defect Closure

1. Chinese event inference now uses meaningful phrases instead of single-character markers such as `刀`, `杀`, `救`, `盟`, `城`, and `雨`.
2. Complete-reveal detection requires final/reveal evidence in the same local clause and ignores explicitly unresolved wording.
3. A passed gate no longer carries an obsolete repair plan or pre-finalization graph warning into the next chapter.
4. A chapter waiting only for mandatory Agent semantic review remains validated instead of becoming an invalid writing task.
5. Benchmark validation now separates structural `complete` from smoke `acceptance_passed` and rejects nonzero P0, canonical pollution, or final gate failures at the acceptance layer.
6. Deterministic relationship extraction no longer treats `吻合` as romance evidence; the clean graph contains no `romantic_tension` relationship.
7. Finalization clears obsolete semantic-review state and upserts the per-chapter reward record, so an approved overwrite cannot leave a false blocker or duplicate the reward ledger.

## Verification

- `python scripts/validate_skills.py`: passed.
- `python scripts/release_surface_guards.py`: passed.
- `python -m pytest -q`: `226 passed`.
- `benchmark validate`: `ok=true`, `complete=true`, `acceptance_passed=true`.
- `benchmark report`: `5/5` chapters, `gate_failure_rate=0`, `canonical_pollution_count=0`.
- `graph validate` and `graph check`: passed without errors, issues, or warnings.

## Remaining Evidence

- Public pipx installation and Skill discovery from a tagged GitHub release are not tested by this source-tree run.
- Claude Code 5-chapter smoke is not run.
- Fanfiction smoke is not run.
- Codex/Claude/novel-skill 10-chapter blind comparisons are not run.
- The non-blind self-review scores remain diagnostic only and cannot support a literary superiority claim.
