import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROVIDER_KEY_NAMES = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "LLM_API_KEY",
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONUTF8"] = "1"
    for key in PROVIDER_KEY_NAMES:
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, "-m", "longform_engine.cli", *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_e2e_agent_skill_no_api_key_full_chapter_lifecycle(tmp_path):
    project_dir = tmp_path / "agent-skill-book"
    project_yaml = project_dir / "project.yaml"

    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(project_dir))
    assert init.returncode == 0, init.stderr
    assert project_yaml.exists()

    config_text = project_yaml.read_text(encoding="utf-8")
    assert "mode: agent_skill" in config_text or 'mode: "agent_skill"' in config_text

    (project_dir / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (project_dir / "10_bible" / "locations.json").write_text(
        json.dumps([{"id": "location:north_gate", "name": "North Gate", "type": "location"}], ensure_ascii=False),
        encoding="utf-8",
    )

    open_book = run_cli("open-book", str(project_yaml))
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")

    task_md = project_dir / "50_workbench" / "writing_tasks" / "ch001.md"
    task_json = project_dir / "50_workbench" / "writing_tasks" / "ch001.json"
    agent_draft = project_dir / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    draft_exists_after_continue = (project_dir / "40_manuscript" / "draft" / "ch001.md").exists()
    agent_draft.write_text(passing_agent_draft("AGENTSKILLFINALMARKER"), encoding="utf-8")

    submit = run_cli(
        "draft",
        "submit",
        str(project_yaml),
        "--chapter",
        "1",
        "--file",
        str(agent_draft),
        "--agent",
        "codex",
    )
    finalize = run_cli("chapter", "finalize", str(project_yaml), "--chapter", "1", "--approved-by", "human")
    rebuild = run_cli("db", "rebuild", str(project_yaml))
    db_status = run_cli("db", "status", str(project_yaml), "--json")
    chapters = run_cli("db", "query", str(project_yaml), "chapters", "--json")
    chunks = run_cli("db", "query", str(project_yaml), "chapter_chunks", "--json")
    submissions = run_cli("db", "query", str(project_yaml), "draft_submissions", "--json")
    gates = run_cli("db", "query", str(project_yaml), "gate_results", "--json")
    events = run_cli("db", "query", str(project_yaml), "events", "--json")
    mentions = run_cli("db", "query", str(project_yaml), "entity_mentions", "--json")

    assert open_book.returncode == 0, open_book.stderr
    assert "OK: open-book confirmed" in open_book.stdout
    assert continue_write.returncode == 0, continue_write.stderr
    assert "OK: continue-write task package ready" in continue_write.stdout
    assert task_md.exists()
    assert task_json.exists()
    assert not draft_exists_after_continue
    assert "draft submit" in task_md.read_text(encoding="utf-8")
    assert "50_workbench/agent_drafts/ch001.codex.md" in task_md.read_text(encoding="utf-8")

    assert submit.returncode == 0, submit.stdout + submit.stderr
    assert "OK: agent draft submitted" in submit.stdout
    assert "Passed: True" in submit.stdout
    assert finalize.returncode == 0, finalize.stderr
    assert "OK: chapter finalized" in finalize.stdout
    assert "Next command: continue-write --chapter 2" in finalize.stdout
    assert rebuild.returncode == 0, rebuild.stderr
    assert "OK: database rebuilt" in rebuild.stdout

    final_file = project_dir / "40_manuscript" / "final" / "ch001.md"
    context_file = project_dir / "60_rag" / "context" / "next_plot_context.md"
    graph_file = project_dir / "30_state" / "story_graph.json"
    assert final_file.exists()
    assert (project_dir / "40_manuscript" / "final" / "ch001.finalization.json").exists()
    assert (project_dir / "60_rag" / "chunks" / "ch001.json").exists()
    assert context_file.exists()
    assert "AGENTSKILLFINALMARKER" in context_file.read_text(encoding="utf-8")

    graph = json.loads(graph_file.read_text(encoding="utf-8"))
    assert any(event.get("chapter_number") == 1 for event in graph["events"])
    assert any(entity.get("id") == "character:ari" and entity.get("mentions") for entity in graph["entities"])

    status_payload = json.loads(db_status.stdout)
    chapter_rows = json.loads(chapters.stdout)
    chunk_rows = json.loads(chunks.stdout)
    submission_rows = json.loads(submissions.stdout)
    gate_rows = json.loads(gates.stdout)
    event_rows = json.loads(events.stdout)
    mention_rows = json.loads(mentions.stdout)

    assert db_status.returncode == 0, db_status.stderr
    assert status_payload["exists"] is True
    assert status_payload["chapters"] == 1
    assert status_payload["chapter_chunks"] >= 1
    assert status_payload["draft_submissions"] == 1
    assert status_payload["gate_results"] == 1
    assert any(row["chapter_number"] == 1 and row["status"] == "final" for row in chapter_rows)
    assert any(row["chapter_number"] == 1 and row["source_path"] == "40_manuscript/final/ch001.md" for row in chunk_rows)
    assert submission_rows[0]["agent"] == "codex"
    assert submission_rows[0]["source_file"] == "50_workbench/agent_drafts/ch001.codex.md"
    assert gate_rows[0]["passed"] == 1
    assert any(row["chapter_number"] == 1 for row in event_rows)
    assert any(row["chapter_number"] == 1 and row["entity_id"] == "character:ari" for row in mention_rows)


def test_e2e_no_key_finalize_to_graph_and_character_memory_apply(tmp_path):
    project_dir, project_yaml = finalized_agent_skill_project(tmp_path, "agent-skill-semantic", "GRAPHMEMORYMARKER")
    evidence = "Ari climbs toward North Gate"

    graph_task = run_cli("graph", "semantic-task", str(project_yaml), "--chapter", "1")
    graph_output = project_dir / "50_workbench" / "graph_updates" / "ch001.semantic.json"
    graph_output.write_text(
        json.dumps(semantic_graph_event_payload(evidence), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    graph_validate = run_cli("graph", "semantic-validate", str(project_yaml), "--chapter", "1", "--file", str(graph_output), "--json")
    graph_apply = run_cli("graph", "semantic-apply", str(project_yaml), "--chapter", "1", "--file", str(graph_output), "--json")

    assert graph_task.returncode == 0, graph_task.stderr
    assert graph_output.exists()
    assert graph_validate.returncode == 0, graph_validate.stdout + graph_validate.stderr
    assert json.loads(graph_validate.stdout)["ok"] is True
    assert graph_apply.returncode == 0, graph_apply.stdout + graph_apply.stderr
    assert json.loads(graph_apply.stdout)["applied"] == 1

    graph = json.loads((project_dir / "30_state" / "story_graph.json").read_text(encoding="utf-8"))
    assert any(event.get("title") == "Ari protects the North Gate witness" for event in graph["events"])

    character_task = run_cli("memory", "character-task", str(project_yaml), "--chapter", "1")
    character_output = project_dir / "50_workbench" / "memory_tasks" / "ch001.character.codex.json"
    character_output.write_text(
        json.dumps(character_memory_payload(evidence), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    character_validate = run_cli(
        "memory",
        "character-validate",
        str(project_yaml),
        "--chapter",
        "1",
        "--file",
        str(character_output),
        "--json",
    )
    character_apply = run_cli(
        "memory",
        "character-apply",
        str(project_yaml),
        "--chapter",
        "1",
        "--file",
        str(character_output),
        "--json",
    )

    assert character_task.returncode == 0, character_task.stderr
    assert character_validate.returncode == 0, character_validate.stdout + character_validate.stderr
    assert json.loads(character_validate.stdout)["ok"] is True
    assert character_apply.returncode == 0, character_apply.stdout + character_apply.stderr
    applied_payload = json.loads(character_apply.stdout)
    assert applied_payload["db_synced"] is True
    assert applied_payload["character_files"]
    assert any(
        json.loads(path.read_text(encoding="utf-8")).get("character_id") == "character:ari"
        for path in (project_dir / "60_rag" / "memory" / "characters").glob("*.json")
    )


def test_e2e_invalid_agent_outputs_do_not_pollute_canonical_boundaries(tmp_path):
    project_dir, project_yaml = finalized_agent_skill_project(tmp_path, "agent-skill-invalids", "INVALIDBOUNDARYMARKER")
    evidence = "Ari climbs toward North Gate"

    graph_task = run_cli("graph", "semantic-task", str(project_yaml), "--chapter", "1")
    character_task = run_cli("memory", "character-task", str(project_yaml), "--chapter", "1")
    editorial_task = run_cli("editorial", "review", str(project_yaml), "--chapter", "1")
    pacing_task = run_cli("pacing", "semantic-task", str(project_yaml), "--chapter", "1")
    assert graph_task.returncode == 0, graph_task.stderr
    assert character_task.returncode == 0, character_task.stderr
    assert pacing_task.returncode == 0, pacing_task.stderr
    assert editorial_task.returncode in (0, 1), editorial_task.stdout + editorial_task.stderr

    graph_output = project_dir / "50_workbench" / "graph_updates" / "ch001.semantic.json"
    graph_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "source": "final",
                "source_path": "40_manuscript/final/ch001.md",
                "updates": [{"type": "event", "title": "bad", "from_chapter": 1, "confidence": "not-a-number", "evidence_span": ""}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    character_output = project_dir / "50_workbench" / "memory_tasks" / "ch001.character.codex.json"
    character_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "source_path": "40_manuscript/final/ch001.md",
                "characters": [{**character_card(evidence), "evidence": []}],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    editorial_output = project_dir / "50_workbench" / "editorial_reviews" / "results" / "ch001.writing_agent.json"
    editorial_output.parent.mkdir(parents=True, exist_ok=True)
    editorial_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "chapter_number": 1,
                "role_id": "writing_agent",
                "verdict": "pass",
                "items": [
                    {
                        "code": "open_p1_should_block",
                        "severity": "P1",
                        "status": "open",
                        "message": "unresolved P1 cannot be hidden behind pass",
                        "evidence": [evidence],
                        "recommendation": "rewrite the scene before final approval",
                    }
                ],
                "summary": "intentionally invalid role result",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    pacing_output = project_dir / "50_workbench" / "gate_artifacts" / "ch001" / "semantic_pacing_result.json"
    pacing_output.write_text(
        json.dumps({"schema_version": 1, "chapter_number": 1, "verdict": "maybe", "issues": "bad"}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    before = canonical_snapshot(project_dir)

    graph_validate = run_cli("graph", "semantic-validate", str(project_yaml), "--chapter", "1", "--file", str(graph_output), "--json")
    graph_apply = run_cli("graph", "semantic-apply", str(project_yaml), "--chapter", "1", "--file", str(graph_output), "--json")
    character_validate = run_cli(
        "memory",
        "character-validate",
        str(project_yaml),
        "--chapter",
        "1",
        "--file",
        str(character_output),
        "--json",
    )
    character_apply = run_cli(
        "memory",
        "character-apply",
        str(project_yaml),
        "--chapter",
        "1",
        "--file",
        str(character_output),
        "--json",
    )
    editorial_submit = run_cli(
        "editorial",
        "submit-review",
        str(project_yaml),
        "--chapter",
        "1",
        "--role",
        "writing_agent",
        "--file",
        str(editorial_output),
        "--json",
    )
    pacing_validate = run_cli("pacing", "semantic-validate", str(project_yaml), "--chapter", "1", "--file", str(pacing_output), "--json")
    pacing_apply = run_cli("pacing", "semantic-apply", str(project_yaml), "--chapter", "1", "--file", str(pacing_output), "--json")

    assert graph_validate.returncode == 1
    assert json.loads(graph_validate.stdout)["ok"] is False
    assert graph_apply.returncode != 0
    assert character_validate.returncode == 1
    assert json.loads(character_validate.stdout)["ok"] is False
    assert character_apply.returncode != 0
    assert editorial_submit.returncode != 0
    assert not (project_dir / "50_workbench" / "editorial_reviews" / "results" / "ch001.writing_agent.normalized.json").exists()
    assert pacing_validate.returncode == 1
    assert json.loads(pacing_validate.stdout)["ok"] is False
    assert pacing_apply.returncode != 0

    assert canonical_snapshot(project_dir) == before


def test_e2e_no_key_repair_humanize_expand_branch(tmp_path):
    project_dir, project_yaml = finalized_agent_skill_project(tmp_path, "agent-skill-repair-branch", "REPAIRROOTMARKER")

    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "2")
    weak_draft = project_dir / "50_workbench" / "agent_drafts" / "ch002.codex.md"
    weak_draft.write_text("# Chapter 2\n\nToo short.\n", encoding="utf-8")
    failed_submit = run_cli("draft", "submit", str(project_yaml), "--chapter", "2", "--file", str(weak_draft), "--agent", "codex")

    assert continue_write.returncode == 0, continue_write.stderr
    assert failed_submit.returncode == 1
    assert "Passed: False" in failed_submit.stdout

    repair_plan = run_cli("repair-chapter", str(project_yaml), "--chapter", "2", "--plan-only")
    repair_task = run_cli("repair-chapter", str(project_yaml), "--chapter", "2", "--candidate-only", "--agent", "codex")
    repair_candidate = project_dir / "50_workbench" / "repair_candidates" / "ch002.codex.repair_candidate.md"
    repair_candidate.write_text(passing_agent_draft("REPAIRCANDIDATEMARKER"), encoding="utf-8")

    assert repair_plan.returncode == 0, repair_plan.stderr
    assert repair_task.returncode == 0, repair_task.stderr
    assert (project_dir / "50_workbench" / "repair_candidates" / "ch002.codex.repair_task.agent_task.json").exists()

    humanize_task = run_cli("creative", "humanize-task", str(project_yaml), "--chapter", "2", "--source", "repair-candidate")
    humanized_candidate = project_dir / "50_workbench" / "repair_candidates" / "ch002.humanized_candidate.md"
    humanized_candidate.write_text(passing_agent_draft("HUMANIZEDCANDIDATEMARKER"), encoding="utf-8")
    humanize_check = run_cli("creative", "humanize-check", str(project_yaml), "--chapter", "2", "--file", str(humanized_candidate), "--json")

    assert humanize_task.returncode == 0, humanize_task.stderr
    assert humanize_check.returncode == 0, humanize_check.stdout + humanize_check.stderr
    assert json.loads(humanize_check.stdout)["passed"] is True

    expand_task = run_cli(
        "creative",
        "expand-task",
        str(project_yaml),
        "--chapter",
        "2",
        "--source",
        "repair-candidate",
        "--type",
        "scene",
        "--type",
        "dialogue",
        "--type",
        "psychology",
        "--type",
        "action",
        "--type",
        "transition",
    )
    expanded_candidate = project_dir / "50_workbench" / "repair_candidates" / "ch002.expanded_candidate.md"
    expanded_candidate.write_text(expanded_agent_draft("EXPANDEDCANDIDATEMARKER"), encoding="utf-8")
    expand_check = run_cli(
        "creative",
        "expand-check",
        str(project_yaml),
        "--chapter",
        "2",
        "--file",
        str(expanded_candidate),
        "--type",
        "scene",
        "--type",
        "dialogue",
        "--type",
        "psychology",
        "--type",
        "action",
        "--type",
        "transition",
        "--json",
    )
    repaired_submit = run_cli(
        "draft",
        "submit",
        str(project_yaml),
        "--chapter",
        "2",
        "--file",
        str(expanded_candidate),
        "--agent",
        "codex",
        "--overwrite",
    )

    assert expand_task.returncode == 0, expand_task.stderr
    assert expand_check.returncode == 0, expand_check.stdout + expand_check.stderr
    assert json.loads(expand_check.stdout)["passed"] is True
    assert repaired_submit.returncode == 0, repaired_submit.stdout + repaired_submit.stderr
    assert "Passed: True" in repaired_submit.stdout


def passing_agent_draft(marker: str) -> str:
    sentence = (
        f"{marker} Ari climbs toward North Gate while the caravan waits below; "
        "she chooses the harder road, protects the witness, keeps the promise, "
        "and turns the local conflict into a sharper chapter hook. "
    )
    return "# Chapter 1: North Gate\n\n" + sentence * 22 + "\n"


def expanded_agent_draft(marker: str) -> str:
    sentence = (
        f"{marker} Ari: hold the gate. She stepped through the stone door, grabbed the chain, "
        "turned toward the witness, and pushed the cart aside when the wind cut across the road. "
        "Then, after the bell sounded, she hesitated because her heart wanted safety but her mind "
        "realized the oath had become the only path left. "
    )
    return "# Chapter 2: North Gate Repair\n\n" + sentence * 15 + "\n"


def finalized_agent_skill_project(tmp_path, name: str, marker: str) -> tuple[Path, Path]:
    project_dir = tmp_path / name
    project_yaml = project_dir / "project.yaml"
    init = run_cli("init-project", "--template", "qidian-longform", "--output", str(project_dir))
    assert init.returncode == 0, init.stderr
    assert project_yaml.exists()
    assert "mode: agent_skill" in project_yaml.read_text(encoding="utf-8") or 'mode: "agent_skill"' in project_yaml.read_text(
        encoding="utf-8"
    )
    (project_dir / "10_bible" / "characters.json").write_text(
        json.dumps([{"id": "character:ari", "name": "Ari", "type": "character"}], ensure_ascii=False),
        encoding="utf-8",
    )
    (project_dir / "10_bible" / "locations.json").write_text(
        json.dumps([{"id": "location:north_gate", "name": "North Gate", "type": "location"}], ensure_ascii=False),
        encoding="utf-8",
    )
    open_book = run_cli("open-book", str(project_yaml))
    continue_write = run_cli("continue-write", str(project_yaml), "--chapter", "1")
    agent_draft = project_dir / "50_workbench" / "agent_drafts" / "ch001.codex.md"
    agent_draft.write_text(passing_agent_draft(marker), encoding="utf-8")
    submit = run_cli("draft", "submit", str(project_yaml), "--chapter", "1", "--file", str(agent_draft), "--agent", "codex")
    finalize = run_cli("chapter", "finalize", str(project_yaml), "--chapter", "1", "--approved-by", "human")

    assert open_book.returncode == 0, open_book.stderr
    assert continue_write.returncode == 0, continue_write.stderr
    assert submit.returncode == 0, submit.stdout + submit.stderr
    assert finalize.returncode == 0, finalize.stderr
    assert (project_dir / "40_manuscript" / "final" / "ch001.md").exists()
    return project_dir, project_yaml


def semantic_graph_event_payload(evidence: str) -> dict:
    return {
        "schema_version": 1,
        "chapter_number": 1,
        "source": "final",
        "source_path": "40_manuscript/final/ch001.md",
        "updates": [
            {
                "type": "event",
                "title": "Ari protects the North Gate witness",
                "participants": ["character:ari"],
                "locations": ["location:north_gate"],
                "summary": "Ari protects the witness and keeps the chapter hook alive.",
                "from_chapter": 1,
                "confidence": 0.9,
                "evidence_span": evidence,
            }
        ],
    }


def character_memory_payload(evidence: str) -> dict:
    return {
        "schema_version": 1,
        "chapter_number": 1,
        "source_path": "40_manuscript/final/ch001.md",
        "characters": [character_card(evidence)],
    }


def character_card(evidence: str) -> dict:
    return {
        "character_id": "character:ari",
        "name": "Ari",
        "aliases": ["Ari"],
        "personality_baseline": ["protective", "decisive under pressure"],
        "current_beliefs": ["The North Gate witness must survive before the larger conflict can move."],
        "knowledge_scope": ["knows the caravan is waiting below North Gate"],
        "relationship_map": {"witness": "protective obligation"},
        "speech_style": {"tone": "plain", "rhythm": "short under pressure"},
        "forbidden_actions": ["cannot abandon the witness without a visible reversal"],
        "state_history": [
            {
                "chapter": 1,
                "state": "protecting the North Gate witness",
                "evidence": evidence,
            }
        ],
        "evidence": [evidence],
        "source_chapters": [1],
        "status": "canonical",
    }


def canonical_snapshot(project_dir: Path) -> dict[str, dict[str, bytes]]:
    return {
        "final": tree_snapshot(project_dir / "40_manuscript" / "final"),
        "rag": tree_snapshot(project_dir / "60_rag"),
        "graph": tree_snapshot(project_dir / "30_state" / "story_graph.json"),
        "tcs": tree_snapshot(project_dir / "30_state" / "tcs"),
        "sqlite": tree_snapshot(project_dir / "70_runtime" / "db"),
    }


def tree_snapshot(path: Path) -> dict[str, bytes]:
    if not path.exists():
        return {}
    if path.is_file():
        return {path.name: path.read_bytes()}
    return {str(child.relative_to(path)).replace("\\", "/"): child.read_bytes() for child in sorted(path.rglob("*")) if child.is_file()}
