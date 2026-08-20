"""Read-only protocol, author-acceptance, and literary-evidence readiness."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from longform_engine.agent_protocol_readiness import check_agent_data_pipeline_readiness
from longform_engine.blind_review import literary_evidence_status
from longform_engine.config import ConfigDocument
from longform_engine.storage import resolve_project_root
from longform_engine.storage.layout import list_finalized_chapter_files


def quality_status(config: ConfigDocument) -> dict[str, Any]:
    """Report three independent readiness claims without allowing one to imply another."""

    root = resolve_project_root(config)
    protocol = check_agent_data_pipeline_readiness()
    author_ready, author_blockers, chapters = author_acceptance_status(root)
    literary_ready, literary_blockers = literary_evidence_status(root)
    return {
        "schema": "quality_status_v1",
        "protocol_ready": bool(protocol.get("protocol_ready")),
        "author_acceptance_ready": author_ready,
        "literary_evidence_ready": literary_ready,
        "author_acceptance": {
            "finalized_chapter_count": len(chapters),
            "chapters": chapters,
            "blockers": author_blockers,
        },
        "protocol_blockers": list(protocol.get("blocking_reasons") or []),
        "literary_evidence_blockers": literary_blockers,
        "claim_boundaries": {
            "protocol_ready": "The executable production protocol is structurally valid.",
            "author_acceptance_ready": (
                "Every finalized chapter has a verifiable current-protocol human accept record."
            ),
            "literary_evidence_ready": (
                "Independent blind-review evidence satisfies the literary evidence manifest."
            ),
        },
    }


def author_acceptance_status(root: Path) -> tuple[bool, list[str], list[dict[str, Any]]]:
    from longform_engine.human_story_review import CHECK_FIELDS, EVIDENCE_KINDS, SCHEMA

    finalized = list_finalized_chapter_files(root)
    if not finalized:
        return False, ["no_finalized_chapters"], []
    blockers: list[str] = []
    chapters: list[dict[str, Any]] = []
    for chapter_number, final_path in finalized:
        chapter_errors: list[str] = []
        finalization_path = final_path.with_suffix(".finalization.json")
        finalization = _read_json(finalization_path)
        binding = (
            finalization.get("human_story_review")
            if isinstance(finalization, dict) and isinstance(finalization.get("human_story_review"), dict)
            else {}
        )
        decision_path = _decision_path(root, chapter_number, binding)
        decision = _read_json(decision_path) if decision_path is not None else None
        if not isinstance(finalization, dict) or finalization.get("chapter_number") != chapter_number:
            chapter_errors.append("finalization_missing_or_invalid")
        if not isinstance(decision, dict):
            chapter_errors.append("human_accept_decision_missing")
        else:
            if decision.get("schema") != SCHEMA:
                chapter_errors.append("human_accept_schema_not_v3")
            if decision.get("chapter_number") != chapter_number:
                chapter_errors.append("human_accept_chapter_mismatch")
            if decision.get("decision") != "accept" or decision.get("approved_by") != "human":
                chapter_errors.append("human_accept_not_accepted")
            checks = decision.get("checks")
            if not isinstance(checks, dict) or set(checks) != CHECK_FIELDS or any(
                not isinstance(checks.get(field), dict) or checks[field].get("passed") is not True
                for field in CHECK_FIELDS
            ):
                chapter_errors.append("human_accept_ten_checks_incomplete")
            annotations = decision.get("annotations")
            if not isinstance(annotations, list) or any(
                isinstance(item, dict) and item.get("severity") in {"P0", "P1"}
                for item in annotations or []
            ):
                chapter_errors.append("human_accept_has_P0_or_P1")
            final_text = final_path.read_text(encoding="utf-8")
            evidence_kinds: set[str] = set()
            for span in decision.get("evidence_spans") or []:
                if not isinstance(span, dict):
                    continue
                start, end = span.get("start"), span.get("end")
                if (
                    isinstance(start, int)
                    and isinstance(end, int)
                    and 0 <= start < end <= len(final_text)
                    and span.get("text") == final_text[start:end]
                    and span.get("kind") in EVIDENCE_KINDS
                ):
                    evidence_kinds.add(str(span["kind"]))
            if EVIDENCE_KINDS - evidence_kinds:
                chapter_errors.append("human_accept_evidence_not_readable_from_final")
        if decision_path is not None and decision_path.is_file():
            decision_hash = sha256(decision_path.read_bytes()).hexdigest()
            if binding.get("decision_sha256") != decision_hash:
                chapter_errors.append("human_accept_decision_hash_mismatch")
        required_hashes = (
            "candidate_sha256",
            "chapter_contract_sha256",
            "reader_promise_ledger_sha256",
            "arc_causal_simulation_sha256",
            "review_bundle_sha256",
        )
        if not binding or binding.get("schema") != "human_story_review_finalization_binding_v1":
            chapter_errors.append("human_accept_finalization_binding_missing")
        elif isinstance(decision, dict) and any(
            not str(binding.get(field) or "")
            or str(binding.get(field)) != str(decision.get(field) or "")
            for field in required_hashes
        ):
            chapter_errors.append("human_accept_five_hash_binding_mismatch")
        record = {
            "chapter_number": chapter_number,
            "accepted": not chapter_errors,
            "final_file": final_path.resolve().relative_to(root.resolve()).as_posix(),
            "decision_file": (
                decision_path.resolve().relative_to(root.resolve()).as_posix()
                if decision_path is not None
                else ""
            ),
            "errors": chapter_errors,
        }
        chapters.append(record)
        blockers.extend(f"ch{chapter_number:03d}:{error}" for error in chapter_errors)
    return not blockers, blockers, chapters


def _decision_path(root: Path, chapter_number: int, binding: dict[str, Any]) -> Path | None:
    relative = str(binding.get("decision_file") or "")
    if not relative:
        return None
    path = (root / relative).resolve()
    review_root = (root / "50_workbench" / "human_story_reviews").resolve()
    try:
        path.relative_to(review_root)
    except ValueError:
        return None
    expected_prefix = f"ch{chapter_number:03d}."
    if not path.name.startswith(expected_prefix) or not path.name.endswith(".decision.json"):
        return None
    return path


def _read_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
