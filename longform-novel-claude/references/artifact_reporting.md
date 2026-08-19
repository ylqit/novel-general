# Artifact Reporting

When finishing a `longform-engine` workflow, report concise command results and the files the user should inspect.

## Standard Fields

- Command executed.
- Project config path.
- Key artifacts written.
- Gate, research, revision, or database status.
- Next safe action.

## Continue Write

Include:

- `60_rag/context/next_plot_context.md`
- chapter card path
- beat sheet path
- draft path
- `gate_result.json`
- run report path

## Gate Result

Include:

- `passed`
- `severity`
- failure count or short failure list
- `allowed_actions`
- `next_command`
- the next review-barrier command when failed
- the immutable `repair_plans/chNNN/rNN.plan.md` only after repair-plan validation

Do not suggest continuing to the next chapter while `passed=false`.

## Research

For inbox items, include JSON and Markdown paths plus status `inbox`.

For promoted items, include:

- `10_bible/research_canon.jsonl`
- `20_outline/research_impact_ledger.jsonl`
- `50_workbench/impact_reports/<id>.md`
- `60_rag/chunks/<id>.json`
- graph file
- SQLite sync status
- transaction report

## Revision

For rewrite branches, include source path, candidate path, report path, and status `rewrite_candidate`.

For rollback, include:

- detached directory
- detached file count
- stale chapters
- `30_state/stale_indexes.json`
- `60_rag/stale.json`
- rollback impact report
- transaction report

## Failure

If a command fails, report the failing command, the error message, and the safest next command. Do not continue with a later workflow step after a hard failure.
