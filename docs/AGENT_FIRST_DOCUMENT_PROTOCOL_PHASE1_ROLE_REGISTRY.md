# Agent-First Document Protocol Phase 1: Role Resources And Registry

## Scope

Phase 1 only establishes host-neutral Prompt role resources and reproducible manifest identity. It does not implement project overlays, context deduplication, new result schemas, or the Phase 7 production data pipeline.

The v0.3.1 `production next`, validate, apply, finalize, chapter close, graph, memory, TCS, RAG, and SQLite behavior remains unchanged except that newly registered AgentTaskManifest v2 tasks must carry valid role metadata.

## Role Inventory

- Registry: `config/agent_roles/registry.json`
- Prompt directory: `config/agent_roles/prompts/`
- Registered contracts: 32
- Non-legacy contracts: 29
- Legacy compatibility contracts: 3
- Direct task mappings: 24
- Specialized editorial mappings: 8
- Generic Agent/editorial fallback: none

Each Markdown contract contains exactly one of every required section:

```text
Identity
Serves
Single Mission
Cognitive Lens
Source Authority
Creative Freedom
Forbidden Actions
Evidence Duty
Output Contract
Stop And Escalate
Handoff
Observable Self Check
```

The registry stores stable role identity, role version, Prompt resource path, output mode, independence mode, and the project-overlay allowlist. It rejects duplicate JSON keys, duplicate role IDs, missing Prompt files, invalid UTF-8, incomplete sections, unknown mappings, unused contracts, and generic editorial fallback.

## Manifest Contract

Every newly built AgentTaskManifest v2 records:

```text
role_id
role_version
role_prompt_hash
independence_mode
project_overlay_hash
```

`role_prompt_hash` is the SHA-256 of the normalized UTF-8 Markdown contract used by both Codex and Claude Code. Host-specific draft paths do not change role semantics or hash. Until Phase 2 implements approved overlays, `project_overlay_hash` is the fixed SHA-256 of an empty payload.

New v2 manifests cannot infer missing fields during registration. Missing, unknown, conflicting, or drifted metadata fails before the manifest file, task index, or lifecycle event is written. Historical v1/v2 manifests remain readable through the frozen task-to-role mapping, but a historical v2 payload must be rebuilt before it can be registered as a new task.

`editorial_review` additionally requires one of the eight declared specialist IDs. The business review result keeps its existing role ID, while the Agent manifest now records the same identity as a versioned Prompt contract.

## Work Order Rendering

`agent-task brief` now renders the role ID/version, Prompt hash, independence mode, empty overlay hash, output mode, and full role contract. The renderer reads the shared engine registry, so Codex and Claude Code do not maintain separate semantic Prompt copies.

The old single-line `TASK_ROLE_BRIEFS` table was removed. The remaining work-scope and output-guidance tables describe task presentation only and are not role authority.

## Distribution

The existing Hatchling force-include for `config/` packages the registry and all Prompt Markdown into the wheel. `resource-manifest.json`, `scripts/validate_skills.py`, and `scripts/audit_wheel.py` now require the registry and every registered Prompt resource.

## Evidence

- `tests/test_agent_document_protocol_phase1.py`: registry coverage, complete sections, specialist editorial resolution, host-neutral hashes, manifest metadata, pre-registration rejection, cache invalidation, resource corruption, and old-v2 compatibility.
- Phase 0/1 plus protocol/orchestration compatibility regression: 45 passed.
- Full regression: 332 passed.
- `python scripts/validate_skills.py`: passed.
- `python scripts/build_resource_manifest.py --check`: passed.
- Wheel build: `longform_novel_engine-0.3.1-py3-none-any.whl`.
- Wheel resource audit: passed with 134 entries.

Release surface guards and synchronized Skill references passed. Phase 6 readiness remains false because Phases 2-5 are not complete.
