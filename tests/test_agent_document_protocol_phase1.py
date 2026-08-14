import copy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from longform_engine.agent_tasks import (
    TASK_CONTRACTS,
    AgentTaskContractError,
    build_manifest,
    list_manifests,
    normalize_manifest,
    validate_manifest_strict,
    write_manifest,
)
from longform_engine.roles import (
    EMPTY_PROJECT_OVERLAY_HASH,
    ROLE_METADATA_FIELDS,
    ROLE_PROMPT_HEADINGS,
    RoleRegistryError,
    load_role_registry,
    validate_role_task_coverage,
)


ROOT = Path(__file__).resolve().parents[1]
NON_LEGACY_ROLE_IDS = {
    "adaptation_analyst",
    "anti_ai_editor",
    "book_architect",
    "canon_fidelity_reviewer",
    "chapter_author",
    "chapter_semantic_archivist",
    "chapter_story_editor",
    "character_editor",
    "character_performance_architect",
    "character_performance_reviewer",
    "continuity_outline_editor",
    "creative_facilitator",
    "executive_editor",
    "expansion_writer",
    "fanfiction_architect",
    "fanfiction_canon_archivist",
    "humanizer",
    "humanizer_semantic_reviewer",
    "longform_outline_architect",
    "planning_chief_editor",
    "reader_payoff_reviewer",
    "reader_quality_reviewer",
    "repair_author",
    "research_synthesizer",
    "semantic_continuity_reviewer",
    "semantic_pacing_reviewer",
    "semantic_style_analyst",
    "serial_verifier",
    "writing_agent",
}


def test_phase1_registry_covers_every_task_and_every_role_contract_is_complete():
    registry = validate_role_task_coverage(set(TASK_CONTRACTS), root=ROOT)

    assert set(registry.task_role_map) == set(TASK_CONTRACTS) - {"editorial_review"}
    assert set(registry.editorial_role_map) == {
        "planning_chief_editor",
        "writing_agent",
        "character_editor",
        "anti_ai_editor",
        "serial_verifier",
        "reader_quality_reviewer",
        "canon_fidelity_reviewer",
        "executive_editor",
    }
    assert NON_LEGACY_ROLE_IDS <= set(registry.roles)
    assert not {"generic_agent", "expert", "reviewer"} & set(registry.roles)
    for role in registry.roles.values():
        assert role.prompt_hash == sha256(role.prompt_text.encode("utf-8")).hexdigest()
        assert role.prompt_path.startswith("config/agent_roles/prompts/")
        for heading in ROLE_PROMPT_HEADINGS:
            assert role.prompt_text.count(f"## {heading}\n") == 1


def test_phase1_editorial_role_requires_a_declared_registered_specialist():
    registry = load_role_registry(ROOT)

    with pytest.raises(RoleRegistryError, match="declared specialized role_id"):
        registry.resolve("editorial_review")
    with pytest.raises(RoleRegistryError, match="Unknown editorial role_id"):
        registry.resolve("editorial_review", declared_role_id="generic_editor")
    for role_id in registry.editorial_role_map:
        role = registry.resolve("editorial_review", declared_role_id=role_id)
        assert role.role_id == role_id
        assert role.independence_mode == "isolated_review"


def test_phase1_manifest_records_reproducible_host_neutral_role_metadata(tmp_path):
    root = seed_minimal_project(tmp_path)
    codex = chapter_manifest(root, output="50_workbench/agent_drafts/ch001.codex.md")
    claude = chapter_manifest(
        root,
        output="50_workbench/agent_drafts/ch001.claude.md",
        task_id="chapter_write:ch001:claude-role-fixture",
    )

    for field in ROLE_METADATA_FIELDS:
        assert codex[field]
        assert codex[field] == claude[field]
    assert codex["role_id"] == "chapter_author"
    assert codex["independence_mode"] == "author_context"
    assert codex["project_overlay_hash"] == EMPTY_PROJECT_OVERLAY_HASH
    assert validate_manifest_strict(root, codex).ok is True

    manifest_file = root / "50_workbench" / "writing_tasks" / "ch001.agent_task.json"
    write_manifest(root, codex, manifest_file)
    indexed = list_manifests(root, chapter_number=1)[0]
    for field in ROLE_METADATA_FIELDS:
        assert indexed[field] == codex[field]


def test_phase1_invalid_role_metadata_fails_before_manifest_index_or_event(tmp_path):
    mutations = (
        lambda payload: payload.pop("role_version"),
        lambda payload: payload.__setitem__("role_id", "unknown_role"),
        lambda payload: payload.__setitem__("role_prompt_hash", "0" * 64),
        lambda payload: payload.__setitem__("project_overlay_hash", "f" * 64),
    )
    for index, mutate in enumerate(mutations):
        root = seed_minimal_project(tmp_path / str(index))
        manifest = chapter_manifest(root)
        mutate(manifest)
        manifest_file = root / "50_workbench" / "writing_tasks" / "ch001.agent_task.json"

        with pytest.raises(AgentTaskContractError):
            write_manifest(root, manifest, manifest_file)

        assert not manifest_file.exists()
        assert not (root / "50_workbench" / "agent_tasks" / "agent_task_index.json").exists()
        assert not (root / "50_workbench" / "agent_tasks" / "events.jsonl").exists()


def test_phase1_registry_rejects_duplicate_missing_and_unknown_role_resources(tmp_path):
    source = ROOT / "config" / "agent_roles"

    duplicate_root = tmp_path / "duplicate"
    shutil.copytree(source, duplicate_root / "config" / "agent_roles")
    duplicate_registry = duplicate_root / "config" / "agent_roles" / "registry.json"
    duplicate_payload = json.loads(duplicate_registry.read_text(encoding="utf-8"))
    duplicate_payload["roles"].append(copy.deepcopy(duplicate_payload["roles"][0]))
    duplicate_registry.write_text(json.dumps(duplicate_payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(RoleRegistryError, match="Duplicate Prompt role_id"):
        load_role_registry(duplicate_root)

    missing_root = tmp_path / "missing"
    shutil.copytree(source, missing_root / "config" / "agent_roles")
    (missing_root / "config" / "agent_roles" / "prompts" / "chapter_author.md").unlink()
    with pytest.raises(RoleRegistryError, match="Prompt is missing"):
        load_role_registry(missing_root)

    unknown_root = tmp_path / "unknown"
    shutil.copytree(source, unknown_root / "config" / "agent_roles")
    unknown_registry = unknown_root / "config" / "agent_roles" / "registry.json"
    unknown_payload = json.loads(unknown_registry.read_text(encoding="utf-8"))
    unknown_payload["task_role_map"]["chapter_write"] = "unknown_role"
    unknown_registry.write_text(json.dumps(unknown_payload, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(RoleRegistryError, match="missing contracts"):
        load_role_registry(unknown_root)


def test_phase1_role_registry_cache_invalidates_when_prompt_content_changes(tmp_path):
    root = tmp_path / "cache"
    shutil.copytree(ROOT / "config" / "agent_roles", root / "config" / "agent_roles")
    before = load_role_registry(root).resolve("chapter_write")
    prompt = root / before.prompt_path
    prompt.write_text(prompt.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    after = load_role_registry(root).resolve("chapter_write")

    assert after.prompt_hash != before.prompt_hash


def test_phase1_old_v2_manifest_is_read_compatible_but_cannot_be_registered_as_new(tmp_path):
    root = seed_minimal_project(tmp_path)
    legacy_v2 = chapter_manifest(root)
    for field in ROLE_METADATA_FIELDS:
        legacy_v2.pop(field)

    normalized = normalize_manifest(legacy_v2)
    assert normalized["role_id"] == "chapter_author"
    assert normalized["role_prompt_hash"]
    with pytest.raises(AgentTaskContractError, match="cannot infer Prompt role metadata"):
        write_manifest(
            root,
            legacy_v2,
            root / "50_workbench" / "writing_tasks" / "ch001.legacy.agent_task.json",
        )


def seed_minimal_project(tmp_path: Path) -> Path:
    root = tmp_path / "novel"
    root.mkdir(parents=True)
    (root / "project.yaml").write_text("project: phase1-fixture\n", encoding="utf-8")
    return root


def chapter_manifest(
    root: Path,
    *,
    output: str = "50_workbench/agent_drafts/ch001.codex.md",
    task_id: str = "chapter_write:ch001:phase1-fixture",
) -> dict:
    return build_manifest(
        root,
        task_type="chapter_write",
        chapter_number=1,
        input_files=[root / "project.yaml"],
        allowed_output_paths=[root / output],
        output_schema="markdown_chapter_only",
        validate_command=(
            f"longform-engine draft submit project.yaml --chapter 1 --file {output} --agent codex"
        ),
        apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
        failure_next_command="longform-engine repair-chapter project.yaml --chapter 1 --plan-only",
        task_id=task_id,
    )
