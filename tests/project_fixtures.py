import json
from hashlib import sha256
from pathlib import Path

import yaml

from longform_engine.semantic import chapter_close, semantic_apply, semantic_task
from longform_engine.semantic.pipeline import active_planned_thread_ids, foreshadow_state_threads, planned_threads


def mark_project_ready(root: Path, config, *, preserve_existing_characters: bool = False) -> None:
    """Seed canonical book/outline state for tests that start after human apply."""

    # Legacy chapter-flow fixtures predate mandatory milestone Agent reviews.
    # Dedicated semantic/fanfiction tests opt into those reviews explicitly.
    config.data.setdefault("quality", {})["semantic_review_milestones"] = []
    config.data["quality"]["semantic_review_boundaries"] = False
    config.data["quality"]["assurance_mode"] = "light"
    config.data["quality"].setdefault("creative_guidance", {})["mode"] = "off"
    project_yaml = (root / "project.yaml").resolve()
    if config.path is not None and config.path.resolve() == project_yaml and project_yaml.is_file():
        payload = yaml.safe_load(project_yaml.read_text(encoding="utf-8"))
        payload.setdefault("quality", {})["semantic_review_milestones"] = []
        payload["quality"]["semantic_review_boundaries"] = False
        payload["quality"]["assurance_mode"] = "light"
        payload["quality"].setdefault("creative_guidance", {})["mode"] = "off"
        project_yaml.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    total_chapters = int(config.data["length"]["total_chapters"])
    volume_count = int(config.data["length"]["volume_count"])
    characters = [
        {
            "id": "lead_ari",
            "name": "Ari",
            "goal": "Protect the border archive.",
            "flaw": "Distrusts allies.",
            "arc_stages": ["isolated", "tested alliance", "earned trust"],
        },
        {
            "id": "ally_mira",
            "name": "Mira",
            "goal": "Expose the false treaty.",
            "flaw": "Takes reckless risks.",
            "arc_stages": ["outsider", "uneasy ally", "trusted partner"],
        },
    ]
    if preserve_existing_characters:
        existing_path = root / "10_bible" / "characters.json"
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            existing = []
        if isinstance(existing, list) and existing:
            characters = []
            for item in existing:
                if not isinstance(item, dict) or not item.get("id") or not item.get("name"):
                    continue
                enriched = dict(item)
                enriched.setdefault("goal", "Protect the current evidence chain.")
                enriched.setdefault("flaw", "Misjudges an ally under pressure.")
                enriched.setdefault("arc_stages", ["guarded", "tested", "changed"])
                characters.append(enriched)
            if len(characters) == 1:
                characters.append(
                    {
                        "id": "support_mira",
                        "name": "Mira",
                        "goal": "Verify the second witness.",
                        "flaw": "Moves before consensus.",
                        "arc_stages": ["outsider", "tested ally", "trusted ally"],
                    }
                )
    relationships = [
        {
            "id": "rel_ari_mira",
            "source_id": "lead_ari",
            "target_id": "ally_mira",
            "type": "alliance",
            "stage": "uneasy",
        }
    ]
    if len(characters) >= 2:
        relationships = [
            {
                "id": "rel_primary_pair",
                "source_id": characters[0]["id"],
                "target_id": characters[1]["id"],
                "type": "alliance",
                "stage": "uneasy",
            }
        ]
    brief = {
        "target_audience": "Chinese longform serial readers.",
        "writing_style": "Concrete, continuous, evidence-led prose.",
        "automation_level": "agent_skill with human approval for canonical apply.",
        "target_scale": f"{total_chapters} chapters.",
        "genre_style_profile": {"genre": "mystery fantasy", "tone": "restrained"},
        "design_decisions": {
            "core_hook": "A border clerk discovers history is being edited overnight.",
            "world_rule": "Every supernatural correction erases a witnessed memory.",
            "protagonist_desire": "Preserve the archive and the people recorded in it.",
            "long_conflict": "The court needs controlled forgetting to preserve its rule.",
            "volume_escalation": "Each volume widens the cost from one town to the realm.",
            "ending_boundary": "The ending must resolve who controls collective memory.",
        },
        "reader_contract": {"core_promise": "Evidence-led mystery and costly growth."},
        "core_taboo": ["Do not reveal the final editor before the last volume."],
        "status": "confirmed",
    }
    volumes = []
    chapter_plan = []
    start = 1
    for number in range(1, volume_count + 1):
        remaining = total_chapters - start + 1
        remaining_volumes = volume_count - number + 1
        size = remaining // remaining_volumes
        end = start + size - 1
        volume_id = f"vol_{number:02d}"
        volumes.append(
            {
                "id": volume_id,
                "number": number,
                "title": f"Volume {number}",
                "from_chapter": start,
                "to_chapter": end,
                "goal": f"Resolve escalation layer {number}.",
                "escalation": f"Raise the institutional cost at layer {number}.",
                "ending_turn": f"Change the evidence model at turn {number}.",
            }
        )
        for chapter_number in range(start, end + 1):
            chapter_plan.append(
                {
                    "chapter_number": chapter_number,
                    "title": f"Evidence {chapter_number}",
                    "duty": "Advance the active investigation.",
                    "conflict": "Ari must choose between speed and verified evidence.",
                    "information_release": "Release one bounded clue.",
                    "hook": "The clue points to a larger contradiction.",
                    "reader_payoff": "A prior detail gains a concrete new meaning.",
                    "volume_id": volume_id,
                    "forbidden_reveals": ["final editor identity"],
                }
            )
        start = end + 1
    ledger = [
        {
            "id": "thread_false_treaty",
            "description": "The treaty contains a deliberately altered witness line.",
            "plant_chapter": 1,
            "payoff_window": [max(2, total_chapters - 2), total_chapters],
            "status": "planned",
        }
    ]
    write_json(root / "10_bible" / "creative_brief.json", brief)
    decisions = {
        "schema": "book_ideation_decisions_v1",
        "dimensions": [
            "target_reader_and_reading_context",
            "core_hook",
            "world_core_rule",
            "protagonist_desire_and_flaw",
            "long_conflict",
            "volume_escalation",
            "ending_boundary",
            "taboos_and_unwanted_tropes",
        ],
        "decisions": {
            "target_reader_and_reading_context": "Chinese longform serial readers seeking evidence-led mystery.",
            "core_hook": "A border clerk discovers history is being edited overnight.",
            "world_core_rule": "Every supernatural correction erases a witnessed memory.",
            "protagonist_desire_and_flaw": "Ari protects the archive but distrusts allies.",
            "long_conflict": "The court depends on controlled forgetting.",
            "volume_escalation": "Each volume widens the cost from one town to the realm.",
            "ending_boundary": "Resolve who controls collective memory.",
            "taboos_and_unwanted_tropes": "No premature final reveal or cost-free correction.",
        },
        "rounds": [],
        "complete": True,
    }
    write_json(root / "10_bible" / "creative_decisions.json", decisions)
    (root / "10_bible" / "world.md").write_text("# World\n\nMemory edits always leave physical evidence.\n", encoding="utf-8")
    (root / "10_bible" / "power_system.md").write_text("# Power\n\nEvery correction consumes a witnessed memory.\n", encoding="utf-8")
    write_json(root / "10_bible" / "characters.json", characters)
    write_json(root / "10_bible" / "relationships.json", relationships)
    (root / "20_outline" / "book_outline.md").write_text("# Book Outline\n\nTen escalating evidence arcs.\n", encoding="utf-8")
    write_json(root / "20_outline" / "volumes.json", volumes)
    write_json(root / "20_outline" / "chapter_plan.json", chapter_plan)
    write_json(root / "20_outline" / "foreshadowing_ledger.json", ledger)
    state_path = root / "30_state" / "novel_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "project_ready"
    state["project_intelligence"] = {
        "book_ideation": {"status": "applied", "candidate_hash": "test-ideation"},
        "book_design": {"status": "applied", "candidate_hash": "test-book"},
        "outline_design": {"status": "applied", "candidate_hash": "test-outline"},
    }
    write_json(state_path, state)


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_unified_semantic_bundle(root: Path, config, chapter_number: int) -> Path:
    """Write a minimal valid Agent result for tests that exercise post-finalize plumbing."""

    task = semantic_task(config, chapter_number=chapter_number)
    final = root / "40_manuscript" / "final" / f"ch{chapter_number:03d}.md"
    text = final.read_text(encoding="utf-8")
    start = next((index for index, character in enumerate(text) if not character.isspace()), 0)
    end = min(len(text), max(start + 1, start + 24))
    evidence = {"start": start, "end": end, "excerpt": text[start:end]}
    active_threads = sorted(
        active_planned_thread_ids(
            planned_threads(root),
            foreshadow_state_threads(root),
            chapter_number,
        )
    )
    output = Path(task.output_file)
    write_json(
        output,
        {
            "schema": "chapter_semantic_bundle_v1",
            "chapter_number": chapter_number,
            "source": {
                "path": f"40_manuscript/final/ch{chapter_number:03d}.md",
                "sha256": sha256(final.read_bytes()).hexdigest(),
            },
            "chapter_digest": {
                "summary": "The chapter advances the immediate conflict through a concrete choice.",
                "causal_change": "The protagonist's action changes the next available decision.",
                "reader_payoff": "The immediate chapter question receives a concrete answer.",
                "cost": "The action narrows the protagonist's safe options.",
            },
            "scenes": [
                {
                    "scene_id": f"ch{chapter_number:03d}:scene:1",
                    **evidence,
                    "participants": [],
                    "location_id": "",
                    "goal": "Advance the immediate chapter conflict.",
                    "outcome": "The next decision becomes unavoidable.",
                }
            ],
            "events": [],
            "relationship_deltas": [],
            "character_deltas": [],
            "foreshadow_deltas": [],
            "world_deltas": [],
            "timeline_deltas": [],
            "retrieval": {"tags": ["chapter progression"], "entity_ids": [], "focus": ["causal change"]},
            "coverage": {
                "featured_character_ids": [],
                "unchanged_character_ids": [],
                "active_thread_ids": active_threads,
                "unchanged_thread_ids": active_threads,
            },
        },
    )
    return output


def complete_unified_semantic_lifecycle(root: Path, config, chapter_number: int, *, approved_by: str = "human") -> None:
    ledger = root / "30_state" / "semantic_ledger" / f"ch{chapter_number:03d}.json"
    if not ledger.exists():
        output = prepare_unified_semantic_bundle(root, config, chapter_number)
        semantic_apply(config, chapter_number=chapter_number, file_path=output)
    gate = root / "50_workbench" / "gate_artifacts" / f"ch{chapter_number:03d}" / "gate_result.json"
    if not gate.exists():
        write_json(gate, {"chapter_number": chapter_number, "passed": True, "severity_counts": {"P0": 0, "P1": 0}})
    chapter_close(config, chapter_number=chapter_number, approved_by=approved_by)
