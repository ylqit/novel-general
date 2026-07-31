# Codex Original Smoke V1

## Outcome

- Run ID: `codex-original-smoke-5-v1`
- Scenario: `照骨司夜录`
- Host: `Codex App 26.721.11231.0`
- CLI: `codex-cli 0.145.0`
- Model label: `gpt-5-codex-session`
- Scenario SHA-256: `3645238ed76a8ed5f39dbf92bac9f4e424497c6dacd4dd7a8f6b647db401c868`
- Structural result: five chapter records, five PASS gates, and five explicit finals.
- Acceptance result: **failed**.
- Blocking evidence: one transient canonical graph pollution incident occurred after chapter 4.
- Review status: `codex-self-review`, non-blind, not literary superiority evidence.

Runtime evidence is stored under the ignored directory
`novels/benchmark-codex-original-smoke-v1/70_runtime/benchmarks/codex-original-smoke-5-v1/`.
No manuscript body is copied into this document or the benchmark records.

## Chapter Evidence

| Chapter | Duty | Gate | Content repairs | Semantic reviews | Context files | Context chars |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 1 | 异常发现 | PASS | 0 | 1 | 7 | 8,781 |
| 2 | 关系冲突 | PASS | 1 | 0 | 7 | 10,546 |
| 3 | 错误判断 | PASS | 0 | 1 | 7 | 11,650 |
| 4 | 代价兑现 | PASS | 0 | 0 | 7 | 10,177 |
| 5 | 小案闭环 | PASS | 1 | 0 | 7 | 11,086 |

Aggregate diagnostics:

- Average context: `7 files / 10,448 characters`.
- P0 contradictions: `0`.
- Need-human count: `0`.
- Content repair count: `2`.
- Canonical pollution incidents: `1`.
- Chapter 1 and chapter 3 semantic reviews were validated and applied.
- `production next` safely points to chapter 6.

The six literary scores in the runtime report are non-blind self-review diagnostics. They must not be used for a public quality comparison.

## Defects Found

1. Finalization left an earlier invalid writing task active after a later semantic review made the gate pass. Finalization now applies the actually submitted candidate and supersedes unused chapter candidates.
2. Event-type inference uses broad Chinese lexical markers. Chapter 2 treated ordinary occurrences such as `刀`, `冲突`, `承诺`, and `信任` as event types. The chapter required a real repair candidate; the classifier remains an open precision issue.
3. Feedback carryover can preserve stale pre-semantic-review warnings after the final gate becomes PASS. This remains open.
4. Chapter finalization still auto-applies deterministic graph suggestions. That path treated `吻合` as a romance marker and broad family words as kinship evidence, causing the recorded canonical pollution incident. Relationship typing now requires both entities in one clause, uses multi-character markers, and reconciles stale relationships on re-apply; separating semantic graph apply from finalization remains an architectural follow-up.
5. Complete-reveal detection combined `全部` and `秘密` from unrelated paragraphs. Chapter 5 required a real repair candidate; cross-span reveal detection remains an open precision issue.
6. Benchmark `complete` currently means structurally complete records. It does not enforce a zero-pollution smoke acceptance policy, so this run is explicitly marked `acceptance_failed` in runtime metadata and this document.

## Code And Test Evidence

- Finalization lifecycle regression: `tests/test_agent_task_protocol.py`.
- Relationship precision and stale cleanup regressions: `tests/test_graph.py`.
- `python scripts/validate_skills.py`: passed.
- `python scripts/release_surface_guards.py`: passed.
- `python -m pytest tests/test_benchmark.py tests/test_production_experience.py tests/test_e2e_agent_skill.py tests/test_graph.py tests/test_agent_task_protocol.py -q`: `43 passed`.
- `python -m pytest -q`: `223 passed`.
- Final graph validation, graph check, and chapter 5 TCS validation: passed.

## Rerun Gate

The original run remains failed evidence. Its clean-rerun gate was closed by
`codex-original-smoke-5-clean-v2`; see
`docs/benchmarks/CODEX_ORIGINAL_SMOKE_CLEAN_V2_SUMMARY.md`.

The clean replay has canonical pollution count `0`, five PASS gates and explicit finals, applied chapter 1/3 semantic reviews, average work orders below 20K with seven files, no stale failed feedback, and a safe chapter 6 next action.

Claude Code smoke, fanfiction smoke, 10-chapter blind review, and `novel-skill` comparison were not run.
