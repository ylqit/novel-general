import json
from pathlib import Path

import pytest
import yaml

from longform_engine.agent_isolation import IsolatedContextSource, assert_current_protocol_coverage, plan_context_batches
from longform_engine.agent_protocol_readiness import check_agent_data_pipeline_readiness
from longform_engine.agent_protocols import AGENT_OUTPUT_PROTOCOLS, EVIDENCE_REVIEW_SCHEMA, validate_evidence_review
from longform_engine.agent_tasks import TASK_CONTRACTS, build_manifest, load_manifest, normalize_manifest, write_manifest
from longform_engine.config import load_project_config
from longform_engine.db import query_table, rebuild_database
from longform_engine.graph import check_graph
from longform_engine.orchestration import continue_write, open_book, submit_agent_draft
from longform_engine.production import agent_task_brief
from longform_engine.rag import build_chunks, build_context, query
from longform_engine.roles import load_role_registry, session_directive
from longform_engine.storage import init_project
from longform_engine.story_profiles import load_facet_registries
from tests.project_fixtures import mark_project_ready


ROOT = Path(__file__).resolve().parents[1]


def test_progressive_prompts_cover_four_protocols_without_pollution(tmp_path):
    registry = load_role_registry(ROOT)
    assert_current_protocol_coverage(registry)
    assert registry.registry_version == 3
    assert len(registry.roles) == 28
    assert len(registry.playbooks) == 12
    assert "executive_editor" not in registry.roles
    assert not {"graph_extract", "memory_extract", "character_memory"} & set(TASK_CONTRACTS)
    for role in registry.roles.values():
        assert role.always_sections
        assert role.task_sections
        assert role.contract_hash
        assert {"decision_model", "workflow", "diagnostics", "failure_modes", "calibration"} <= set(
            role.prompt_sections
        )
        assert all(marker in role.prompt_sections["calibration"] for marker in ("正例", "反例", "边界"))
        if role.role_family == "review":
            assert role.max_active_playbooks <= 2
            assert all(code in role.prompt_text for code in role.finding_codes)
        else:
            assert "专业判定表" in role.prompt_sections["diagnostics"]
        assert role.session_policy in {
            "project_coordinator",
            "chapter_author",
            "isolated_revision",
            "isolated_review",
            "isolated_archival",
        }
    assert len(TASK_CONTRACTS) == 25
    assert {contract["schemas"][0] for contract in TASK_CONTRACTS.values()} == set(AGENT_OUTPUT_PROTOCOLS)
    assert all(len(contract["schemas"]) == 1 for contract in TASK_CONTRACTS.values())
    facet_registries = load_facet_registries()
    assert sum(len(items) for items in facet_registries.values()) == 44
    assert all(
        any("\u3400" <= char <= "\u9fff" for char in facet["prompt_adapter"])
        for items in facet_registries.values()
        for facet in items.values()
    )
    facet_sections = [
        playbook.source.sections["facets"]
        for playbook in registry.playbooks.values()
    ]
    assert len(set(facet_sections)) == 12

    readiness = check_agent_data_pipeline_readiness(ROOT)
    assert readiness["ready_for_data_pipeline"] is True
    assert readiness["professional_prompt_ready"] is True
    assert readiness["provenance"]["execution_model"] == "single_process_sequential"

    fixtures = yaml.safe_load(
        (ROOT / "config" / "agent_protocol_acceptance_fixtures.yaml").read_text(encoding="utf-8")
    )
    professional = fixtures["professional_prompt_calibration"]
    assert set(professional["roles"]) == set(registry.roles)
    assert set(professional["playbooks"]) == set(registry.playbooks)
    assert sum(len(items) for items in professional["facets"].values()) == 44
    calibration_texts = []
    for cases in (
        professional["roles"],
        professional["playbooks"],
        {
            f"{kind}:{facet_id}": case
            for kind, items in professional["facets"].items()
            for facet_id, case in items.items()
        },
    ):
        for case in cases.values():
            assert set(case) == {"positive", "negative", "boundary"}
            assert all(any("\u3400" <= char <= "\u9fff" for char in text) for text in case.values())
            calibration_texts.extend("".join(text.split()) for text in case.values())
    assert len(calibration_texts) == 84 * 3
    assert len(calibration_texts) == len(set(calibration_texts))

    professional_check = next(
        item for item in readiness["checks"] if item["id"] == "professional_prompt_calibration"
    )
    inventory = professional_check["detail"]["inventory"]
    assert professional_check["status"] == "pass"
    assert inventory["item_count"] == 84
    assert len(inventory["roles"]) == 28
    assert len(inventory["playbooks"]) == 12
    assert len(inventory["facets"]) == 44
    assert all(item["estimated_units"] > 0 and len(item["contract_hash"]) == 64 for item in inventory["roles"])
    assert all(item["loaded_role_sections"] for item in inventory["roles"])
    assert all(item["estimated_units"] > 0 and len(item["source_hash"]) == 64 for item in inventory["playbooks"])
    assert all(item["estimated_units"] > 0 and len(item["adapter_hash"]) == 64 for item in inventory["facets"])
    for playbook in registry.playbooks.values():
        assert "诊断分支" in playbook.source.sections["review"]
        assert playbook.source.sections["review"].count("\n- ") >= 3
        assert "保护项" in playbook.source.sections["repair"]
    assert len(fixtures["original_scenarios"]) == 6
    assert fixtures["fanfiction_scenario"]["chapter_count"] == 20
    regressions = fixtures["v043_quality_regression_cases"]
    assert {item["id"] for item in regressions} == {
        "mainline_invisible",
        "unnamed_functional_cast",
        "same_voice_dialogue",
        "speaker_ambiguity",
        "summary_replaces_scene",
        "inert_interiority",
        "undeclared_rule_exception",
        "foreshadow_without_echo",
    }
    assert all(item["reviewer"] in registry.roles for item in regressions)
    assert {item["severity"] for item in regressions} <= {"P1", "P2"}
    assert all(item["negative"] and item["boundary"] for item in regressions)
    for case in fixtures["prompt_selection_cases"]:
        selection = registry.select_prompt(
            case["task_type"],
            declared_role_id=case["role_id"],
            quality_focus=case["quality_focus"],
        )
        assert case["expected_playbook"] in {item.playbook_id for item in selection.playbooks}
        for selected in selection.playbooks:
            source = registry.playbooks[selected.playbook_id].source
            assert all(
                source.section_modes[section] not in {"reference_only", "calibration_only"}
                for section in selected.sections
            )

    semantic_role = registry.resolve("semantic_review")
    character_role = registry.resolve("editorial_review", declared_role_id="character_editor")
    assert set(semantic_role.review_dimensions) != set(character_role.review_dimensions)
    assert set(semantic_role.finding_codes).isdisjoint(character_role.finding_codes)

    unsupported_blocker = {
        "schema": EVIDENCE_REVIEW_SCHEMA,
        "verdict": "repair",
        "coverage": {
            "motivation": {
                "status": "checked",
                "evidence_ids": ["40_manuscript/draft/ch001.md@0:1"],
                "canonical_refs": ["20_outline/chapter_cards/ch001.json"],
            }
        },
        "findings": [
            {
                "code": "MOTIVATION_JUMP",
                "severity": "P1",
                "certainty": "probable",
                "diagnosis": "动机变化缺少场景依据。",
                "evidence_ids": [],
                "reader_impact": "人物行为显得由作者推动。",
                "repair_target": "补足触发选择的场景证据。",
                "preserve": ["既有事件结果"],
            }
        ],
    }
    assert any(
        "P0/P1 requires confirmed certainty and evidence IDs" in item
        for item in validate_evidence_review(unsupported_blocker)
    )

    template = load_project_config(template="qidian-longform")
    project = init_project(template, output=tmp_path / "novel")
    config = load_project_config(project.project_config, cli_overrides={"editorial": {"review_mode": "off"}})
    open_book(config)
    mark_project_ready(project.root, config)
    continue_write(config, chapter_number=1)

    manifest = load_manifest(project.root, "chapter_write:ch001:v4")
    brief = agent_task_brief(config, manifest["task_id"], host="codex")
    assert manifest["schema_version"] == 4
    persisted = json.loads((project.root / manifest["manifest_file"]).read_text(encoding="utf-8"))
    assert set(persisted) == {
        "schema_version",
        "task_id",
        "task_type",
        "scope",
        "role",
        "io",
        "policy",
        "commands",
        "created_at",
    }
    assert not {
        "input_files",
        "allowed_output_paths",
        "output_schema",
        "context_policy",
        "validate_command",
        "apply_command",
        "failure_next_command",
    } & set(persisted)
    assert len(manifest["role"]["contract_hash"]) == 64
    assert "playbook_bundle_hash" not in manifest
    assert "prompt_selection_reasons" not in manifest
    assert brief["renderer"] == "agent_task_brief_v4"
    assert brief["role"]["id"] == "chapter_author"
    assert len(brief["role"]["compiled_prompt_hash"]) == 64
    assert "## 角色与目标" in brief["work_order_markdown"]
    assert "当前角色：**角色身份**" not in brief["work_order_markdown"]
    assert "当前角色：你依据已批准方向写一章可直接试读的完整中文网络小说正文。" in brief["work_order_markdown"]
    assert "交付目标：按 `prose_markdown_v1` 写入" in brief["work_order_markdown"]
    assert brief["work_order_markdown"].count("## 7. 输出与交接") == 1
    assert brief["work_order_markdown"].count("输出协议：`prose_markdown_v1`") == 1
    assert "中文小说专业方法包" in brief["work_order_markdown"]
    assert "## 允许写入路径" in brief["work_order_markdown"]
    assert len(brief["io"]["inputs"]) <= 7
    assert brief["budget"]["profile"] == "standard"
    assert brief["budget"]["capacity_units"] == 48_000
    assert brief["budget"]["is_exact_token_count"] is False
    assert brief["session"]["action"] == "new_session_required"
    assert brief["session"]["scope"] == "ch001:author"
    assert len(manifest["policy"]["context"]["active_facets"]) <= 3
    assert "当前故事分面适配" in brief["work_order_markdown"]
    output_contract = brief["pipeline"]["output_contract"]
    assert set(output_contract) == {
        "schema",
        "task_id",
        "task_type",
        "role_id",
        "protocol",
        "output_path",
        "validate_command",
        "apply_command",
        "failure_command",
        "cli_prefilled_fields",
    }
    assert "calibration_only" not in brief["work_order_markdown"]
    assert "reference_only" not in brief["work_order_markdown"]
    for selected in manifest["role"]["playbooks"]:
        source = registry.playbooks[selected["id"]].source
        for section, mode in source.section_modes.items():
            if mode in {"reference_only", "calibration_only"}:
                assert source.sections[section] not in brief["work_order_markdown"]

    repair_selection = registry.select_prompt("repair")
    humanize_selection = registry.select_prompt("humanize")
    review_selection = registry.select_prompt("semantic_review")
    for selection in (repair_selection, humanize_selection):
        for selected in selection.playbooks:
            source = registry.playbooks[selected.playbook_id].source
            assert {source.section_modes[item] for item in selected.sections} <= {"always", "trigger"}
            assert "repair" in selected.sections
    for selected in review_selection.playbooks:
        assert set(selected.sections) == {"core", "review", "false_positives"}

    graph = project.root / "30_state" / "story_graph.json"
    graph_before = graph.read_bytes()
    with pytest.raises(ValueError, match="schema_version must be 4"):
        normalize_manifest(dict(manifest, schema_version=2))
    assert graph.read_bytes() == graph_before
    assert not list((project.root / "40_manuscript" / "final").glob("ch*.md"))

    cli_text = (ROOT / "src" / "longform_engine" / "cli.py").read_text(encoding="utf-8")
    assert 'add_parser("legacy"' not in cli_text
    assert 'add_parser("init-novel"' not in cli_text
    assert "models migrate" not in cli_text
    assert "multiprocessing" not in cli_text
    assert "ProcessPoolExecutor" not in cli_text
    assert "--document" in cli_text


def test_adaptive_context_profiles_and_hybrid_sessions(tmp_path):
    reports = {}
    briefs = {}
    for profile in ("compact", "standard", "large"):
        template = load_project_config(template="qidian-longform")
        project = init_project(template, output=tmp_path / profile)
        payload = yaml.safe_load(project.project_config.read_text(encoding="utf-8"))
        payload["writing"]["agent"]["context"]["host_profile"] = profile
        project.project_config.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        config = load_project_config(project.project_config)
        task_file = project.root / "50_workbench" / "agent_tasks" / "adaptive.md"
        task_file.parent.mkdir(parents=True, exist_ok=True)
        task_file.write_text("# 自适应任务\n\n" + "角色必须依据场景证据作出选择。" * 1_800, encoding="utf-8")
        output_file = project.root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
        manifest = build_manifest(
            project.root,
            task_type="chapter_write",
            chapter_number=1,
            input_files=[task_file],
            allowed_output_paths=[output_file],
            output_schema="prose_markdown_v1",
            validate_command="longform-engine draft submit project.yaml --chapter 1 --file 50_workbench/agent_drafts/ch001.codex.md --agent codex",
            apply_command="longform-engine chapter finalize project.yaml --chapter 1 --approved-by human",
            failure_next_command="longform-engine production next project.yaml",
            context_policy={"required_files": [task_file], "compiled_brief": task_file},
        )
        manifest_file = project.root / "50_workbench" / "agent_tasks" / "adaptive.agent_task.json"
        write_manifest(project.root, manifest, manifest_file)
        first = agent_task_brief(config, manifest["task_id"], host="codex")
        second = agent_task_brief(config, manifest["task_id"], host="codex")
        assert first["budget"] == second["budget"]
        assert first["session"]["action"] == "new_session_required"
        assert first["io"]["output"]["protocol"] == "prose_markdown_v1"
        assert len(first["io"]["inputs"]) == 1
        assert first["work_order_markdown"].count("## 自适应上下文预算") == 1
        reports[profile] = first["budget"]
        briefs[profile] = first

    assert [reports[item]["capacity_units"] for item in ("compact", "standard", "large")] == [
        24_000,
        48_000,
        96_000,
    ]
    assert reports["compact"]["status"] == "need_human"
    assert reports["large"]["status"] in {"within_soft_target", "advisory"}
    assert briefs["compact"]["executable"] is False
    assert briefs["compact"]["next_command"] == briefs["compact"]["commands"]["failure"]
    assert briefs["large"]["executable"] is True
    assert briefs["large"]["next_command"] == briefs["large"]["commands"]["result_validate"]

    registry = load_role_registry(ROOT)
    session_cases = {
        "book_design": "continue_project_session",
        "chapter_write": "new_session_required",
        "repair": "continue_chapter_session",
        "humanize": "new_session_required",
        "semantic_review": "new_session_required",
        "chapter_semantic": "new_session_required",
    }
    for task_type, expected_action in session_cases.items():
        role = registry.resolve(task_type)
        directive = session_directive(
            role,
            task_type=task_type,
            scope={"kind": "chapter", "chapter_number": 7},
            task_id=f"{task_type}:ch007:v4",
        )
        assert directive["action"] == expected_action

    sources = [
        IsolatedContextSource(
            path=f"evidence/{index}.md",
            sha256=str(index) * 64,
            characters=9_000,
            estimated_units=9_000,
            tier="optional",
            selection_reason="range_evidence",
            instruction_like_content=False,
        )
        for index in range(1, 4)
    ]
    batches, blockers = plan_context_batches(
        sources,
        input_hard_units=10_000,
        prose_output=False,
        scope_kind="range",
    )
    assert blockers == []
    assert len(batches) == 3
    assert {item["aggregation"] for item in batches} == {"deterministic_source_hash_and_evidence_id"}


def test_release_guard_tracks_current_v043_contracts():
    guard = (ROOT / "scripts" / "release_surface_guards.py").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "V0_4_3_RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
    production = (ROOT / "src" / "longform_engine" / "production.py").read_text(encoding="utf-8")

    for marker in (
        "REQUIRED_RELEASE_CONTRACT_MARKERS",
        "check_experience_layer_guards",
        "DIRECT_WRITER_PATTERNS",
        "test_strict_manifest_validation_rejects_unknown_type_and_canonical_output",
        "AGENT_TASK_STATUSES",
        "canonical_write_transaction_rollback",
        "rollback_restores_touched_paths",
    ):
        assert marker in guard
    for section in (
        "配置与覆盖来源",
        "统一章节路径",
        "内部质量证据",
        "单进程完整验证",
        "发布前本地证据",
        "远程发布证据",
    ):
        assert section in checklist
    for marker in (
        "def production_loop",
        "def agent_task_brief",
        '"read_only": True',
        "normalize_contract_json",
    ):
        assert marker in production


def test_release_guard_keeps_agent_protocol_isolated_and_current():
    guard = (ROOT / "scripts" / "release_surface_guards.py").read_text(encoding="utf-8")
    production = (ROOT / "src" / "longform_engine" / "production.py").read_text(encoding="utf-8")

    for marker in (
        "check_agent_first_protocol_isolation_guards",
        "CURRENT_TASK_TYPES",
        "compile_isolated_agent_package",
        "validate_isolated_agent_submission",
        "Agent protocol module must remain write-free",
        "check_removed_runtime_guards",
    ):
        assert marker in guard
    assert "longform_engine.agent_isolation" not in production
    assert "longform_engine.agent_results" not in production


def test_release_guard_covers_agent_data_pipeline_readiness_gate():
    guard = (ROOT / "scripts" / "release_surface_guards.py").read_text(encoding="utf-8")

    for marker in (
        "check_agent_data_pipeline_readiness_guards",
        "agent_data_pipeline_readiness_v3",
        "ready_for_data_pipeline",
        "professional_prompt_ready",
        "professional_prompt_calibration",
        "require_agent_data_pipeline_readiness",
        "single_process_sequential",
        "adaptive_context_profiles",
        "hybrid_session_boundaries",
        "chinese_story_facet_adapters",
        "fixed_prompt_budget_removed",
        "python scripts/check_agent_data_pipeline_readiness.py --json",
    ):
        assert marker in guard


def test_release_guard_covers_benchmark_and_readiness_contracts():
    guard = (ROOT / "scripts" / "release_surface_guards.py").read_text(encoding="utf-8")

    for marker in (
        "check_public_distribution_guards",
        "BENCHMARK_RECORD_SCHEMA",
        "BENCHMARK_COMPARISON_SCHEMA",
        "stores_manuscript_body",
        "forbidden_git_mutation",
        "cmd_release_check",
        "cmd_benchmark_record",
        "cmd_benchmark_compare",
    ):
        assert marker in guard


def test_failed_agent_draft_does_not_pollute_long_term_memory_or_indexes(tmp_path):
    config = load_project_config(template="qidian-longform")
    project = init_project(config, output=tmp_path / "novel")
    project_config = load_project_config(project.project_config)
    root = project.root

    (root / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )

    open_book(project_config)
    mark_project_ready(root, project_config, preserve_existing_characters=True)
    continue_write(project_config, chapter_number=1)
    graph_path = root / "30_state" / "story_graph.json"
    graph_before = graph_path.read_text(encoding="utf-8")

    agent_draft = root / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(
        "# Chapter 1: Failed Draft\n\n"
        "TODO DRAFTLEAKPHRASE Ari should not become canon from a failed draft.\n",
        encoding="utf-8",
    )

    result = submit_agent_draft(project_config, chapter_number=1, file_path=agent_draft, agent="codex")
    stale_chunk = root / "60_rag" / "chunks" / "ch001.json"
    stale_chunk.write_text(
        json.dumps(
            {
                "source_path": "40_manuscript/draft/ch001.md",
                "chunks": [
                    {
                        "id": "ch001:draft-leak",
                        "chapter_number": 1,
                        "chunk_index": 0,
                        "text": "DRAFTLEAKPHRASE must not enter final RAG.",
                        "keywords": ["DRAFTLEAKPHRASE"],
                        "metadata": {"source": "40_manuscript/draft/ch001.md"},
                    }
                ],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    graph_check = check_graph(project_config)
    graph_after_check = graph_path.read_text(encoding="utf-8")
    rag_stats = build_chunks(project_config)
    rag_result = query(project_config, "DRAFTLEAKPHRASE", top_k=3)
    context = build_context(project_config, chapter_number=2, query_text="safe context", top_k=3)
    rebuild = rebuild_database(project_config)

    state = json.loads((root / "30_state" / "novel_state.json").read_text(encoding="utf-8"))
    gate_result = json.loads((root / "50_workbench" / "gate_artifacts" / "ch001" / "gate_result.json").read_text(encoding="utf-8"))
    graph_after_rebuild = json.loads(graph_path.read_text(encoding="utf-8"))
    context_text = (root / "60_rag" / "context" / "next_plot_context.md").read_text(encoding="utf-8")
    chapters = query_table(project_config, "chapters", limit=20)
    chunks = query_table(project_config, "chapter_chunks", limit=20)
    events = query_table(project_config, "events", limit=20)
    mentions = query_table(project_config, "entity_mentions", limit=20)
    gates = query_table(project_config, "gate_results", limit=20)

    assert result.passed is False
    assert gate_result["passed"] is False
    assert state["status"] == "reviews_pending"
    assert state["last_finalized_chapter"] == 0
    assert not (root / "40_manuscript" / "final" / "ch001.md").exists()

    assert graph_after_check == graph_before
    assert any("Agent draft timeline risk ch001" in warning for warning in graph_check.warnings)
    assert graph_after_rebuild["events"] == []
    assert all(not entity.get("mentions") for entity in graph_after_rebuild.get("entities", []))

    assert rag_stats.chapters == 0
    assert not stale_chunk.exists()
    assert rag_result.hits == ()
    assert context.hit_count == 0
    assert "DRAFTLEAKPHRASE" not in context_text

    assert rebuild.chapters == 1
    assert rebuild.chapter_chunks == 0
    assert rebuild.events == 0
    assert any(row["chapter_number"] == 1 and row["status"] == "reviews_pending" for row in chapters)
    assert not any(row["chapter_number"] == 1 and row["status"] == "final" for row in chapters)
    assert chunks == []
    assert events == []
    assert mentions == []
    assert gates[0]["passed"] == 0
