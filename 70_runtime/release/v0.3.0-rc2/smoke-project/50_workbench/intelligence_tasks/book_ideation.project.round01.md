# book_ideation Agent Task

Output schema: `book_ideation_candidate_v1`
Scope: `{"kind": "project", "round": 1, "dimension": "target_reader_and_reading_context"}`
Allowed output: `50_workbench/intelligence_candidates/book_ideation.project.candidate.json`

Read only:
- `project.yaml`
- `00_governance/idea_seed.md`
- `00_governance/reader_contract.md`

Validation requirements:
- Ask exactly one core question for the declared dimension. Return two or three materially different options with explicit tradeoffs. selection must record the user's explicit option or provided answer; do not infer consent or answer additional dimensions.

Do not write Bible, outline, research canon, final, RAG, graph, TCS, or SQLite directly.
Return JSON only. The CLI validates before any explicit apply.
