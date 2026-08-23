from __future__ import annotations

from longform_engine.canon_changes import (
    validate_canon_change_proposal,
    validate_canonical_fact,
)
from longform_engine.reader_feedback import (
    validate_reader_feedback_batch,
    validate_reader_feedback_decision,
)


def test_canon_change_uses_stable_fact_ids_and_human_owned_proposals():
    fact = {
        "schema": "canonical_fact_v2",
        "fact_id": "world.harbor.curfew",
        "statement": "The harbor closes at the second bell.",
        "status": "active",
        "dependency_fact_ids": [],
        "source_refs": ["bible.harbor"],
    }
    proposal = {
        "schema": "canon_change_proposal_v1",
        "proposal_id": "canon.harbor.curfew",
        "reason": "Move the curfew after the volume-one consequence review.",
        "effective_from_chapter": 8,
        "operations": [{"operation": "upsert", "fact": fact}],
        "dependency_fact_ids": ["world.harbor.curfew"],
        "created_by": "human",
    }

    assert validate_canonical_fact(fact) == []
    assert validate_canon_change_proposal(proposal) == []


def test_reader_feedback_requires_a_human_decision_for_every_noncanonical_hypothesis():
    batch = {
        "schema": "reader_feedback_batch_v1",
        "batch_id": "feedback.volume1",
        "scope": {"from_chapter": 1, "to_chapter": 6},
        "observations": ["The relationship consequence is difficult to locate."],
        "hypotheses": [
            {
                "hypothesis_id": "feedback.relationship.consequence",
                "statement": "The rolling plan may need a clearer aftermath node.",
                "evidence_observation_indexes": [0],
                "possible_targets": ["planning"],
            }
        ],
        "recorded_by": "human",
    }
    missing_decision = {
        "schema": "human_reader_feedback_decision_v1",
        "batch_sha256": "0" * 64,
        "decisions": [],
        "decided_by": "human",
        "reason": "Review every hypothesis before conversion.",
    }

    assert validate_reader_feedback_batch(batch) == []
    errors = validate_reader_feedback_decision(batch, missing_decision)
    assert "batch_sha256 is stale" in errors
    assert "every feedback hypothesis requires one explicit human decision" in errors
