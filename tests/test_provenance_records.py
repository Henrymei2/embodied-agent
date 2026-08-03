from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.provenance import (
    ProvenanceValidationError,
    build_provenance_record,
    content_digest,
    validate_provenance_graph,
    validate_provenance_record,
)

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def ts(seconds: int) -> str:
    return (_START + timedelta(seconds=seconds)).isoformat()


def record(
    record_id: str,
    record_type: str,
    *,
    second: int,
    source_class: str,
    authority: str,
    claim_key: str | None = None,
    evidence_ids: list[str] | None = None,
    confidence: float | None = None,
    status: str = "active",
    contradiction_ids: list[str] | None = None,
    supersedes_id: str | None = None,
    content_ref: str | None = None,
) -> dict:
    return build_provenance_record(
        record_id=record_id,
        record_type=record_type,
        claim_key=claim_key or f"claim.{record_type}",
        source_class=source_class,
        source_id=f"source-{record_id}",
        authority=authority,
        observed_at=ts(second),
        recorded_at=ts(second),
        valid_from=ts(second),
        privacy_class="private",
        content_ref=(
            content_ref
            if content_ref is not None
            else None
            if status == "tombstoned"
            else f"content-{record_id}"
        ),
        content_digest=content_digest(f"private value for {record_id}"),
        evidence_ids=evidence_ids,
        contradiction_ids=contradiction_ids,
        supersedes_id=supersedes_id,
        confidence=confidence,
        status=status,
    )


def complete_graph() -> list[dict]:
    assertion = record(
        "assertion-1",
        "assertion",
        second=0,
        source_class="user",
        authority="reported",
    )
    observation = record(
        "observation-1",
        "observation",
        second=1,
        source_class="sensor",
        authority="observed",
    )
    belief = record(
        "belief-1",
        "belief",
        second=2,
        source_class="model",
        authority="inferred",
        evidence_ids=["assertion-1", "observation-1"],
        confidence=0.8,
    )
    preference = record(
        "preference-1",
        "preference",
        second=3,
        source_class="user",
        authority="user_authoritative",
        evidence_ids=["assertion-1"],
    )
    intention = record(
        "intention-1",
        "intention",
        second=4,
        source_class="system",
        authority="system_authoritative",
        evidence_ids=["belief-1", "preference-1"],
    )
    outcome = record(
        "outcome-1",
        "outcome",
        second=5,
        source_class="tool",
        authority="observed",
        evidence_ids=["intention-1"],
    )
    return [assertion, observation, belief, preference, intention, outcome]


def test_all_six_record_types_form_a_valid_evidence_graph():
    records = complete_graph()

    validate_provenance_graph(records)

    assert {item["record_type"] for item in records} == {
        "observation",
        "assertion",
        "belief",
        "preference",
        "intention",
        "outcome",
    }


def test_provenance_record_contains_only_an_opaque_reference_and_digest():
    private = "The user said something private."
    assertion = build_provenance_record(
        record_id="assertion-private",
        record_type="assertion",
        claim_key="claim.assertion",
        source_class="user",
        source_id="participant-1",
        authority="reported",
        observed_at=ts(0),
        recorded_at=ts(0),
        valid_from=ts(0),
        privacy_class="restricted",
        content_ref="vault-item-1",
        content_digest=content_digest(private),
    )

    encoded = json.dumps(assertion)
    assert private not in encoded
    assert assertion["content_digest"] == content_digest(private.encode("utf-8"))


def test_belief_requires_evidence_and_calibrated_confidence():
    with pytest.raises(ProvenanceValidationError, match="required for a belief"):
        record(
            "belief-no-evidence",
            "belief",
            second=1,
            source_class="model",
            authority="inferred",
            confidence=0.8,
        )
    candidate = record(
        "belief-confidence",
        "belief",
        second=1,
        source_class="model",
        authority="inferred",
        evidence_ids=["assertion-1"],
        confidence=0.8,
    )
    candidate["confidence"] = 1.1

    with pytest.raises(ProvenanceValidationError, match="from 0 to 1"):
        validate_provenance_record(candidate)


def test_model_confidence_cannot_escalate_source_authority():
    candidate = record(
        "belief-1",
        "belief",
        second=1,
        source_class="model",
        authority="inferred",
        evidence_ids=["assertion-1"],
        confidence=1.0,
    )
    candidate["authority"] = "user_authoritative"

    with pytest.raises(ProvenanceValidationError, match="invalid for belief"):
        validate_provenance_record(candidate)


def test_dangling_and_future_evidence_fail_closed():
    records = complete_graph()
    records[2]["evidence_ids"] = ["missing"]
    with pytest.raises(ProvenanceValidationError, match="missing record"):
        validate_provenance_graph(records)

    records = complete_graph()
    records[2]["evidence_ids"] = ["outcome-1"]
    with pytest.raises(ProvenanceValidationError, match="recorded later"):
        validate_provenance_graph(records)


def test_contradictions_are_explicit_symmetric_and_contested():
    assertion = complete_graph()[0]
    first = record(
        "belief-a",
        "belief",
        second=2,
        source_class="model",
        authority="inferred",
        evidence_ids=["assertion-1"],
        confidence=0.6,
        status="contested",
        contradiction_ids=["belief-b"],
    )
    second = record(
        "belief-b",
        "belief",
        second=2,
        source_class="model",
        authority="inferred",
        evidence_ids=["assertion-1"],
        confidence=0.5,
        status="contested",
        contradiction_ids=["belief-a"],
    )
    validate_provenance_graph([assertion, first, second])
    second["contradiction_ids"] = []

    with pytest.raises(ProvenanceValidationError, match="required for contested"):
        validate_provenance_graph([assertion, first, second])


def test_supersession_preserves_history_and_requires_one_later_successor():
    old = record(
        "assertion-old",
        "assertion",
        second=0,
        source_class="user",
        authority="reported",
        status="superseded",
    )
    new = record(
        "assertion-new",
        "assertion",
        second=1,
        source_class="user",
        authority="reported",
        supersedes_id="assertion-old",
    )
    validate_provenance_graph([old, new])

    with pytest.raises(ProvenanceValidationError, match="lack successors"):
        validate_provenance_graph([old])


def test_tombstone_removes_content_reference_but_retains_identity_and_digest():
    tombstone = record(
        "assertion-deleted",
        "assertion",
        second=0,
        source_class="user",
        authority="reported",
        status="tombstoned",
    )

    assert tombstone["content_ref"] is None
    assert len(tombstone["content_digest"]) == 64
    validate_provenance_graph([tombstone])


def test_record_shapes_are_versioned_and_strict():
    candidate = complete_graph()[0]
    candidate["raw_content"] = "must never be accepted"

    with pytest.raises(ProvenanceValidationError, match=r"extra=\['raw_content'\]"):
        validate_provenance_record(candidate)


def test_validity_can_begin_in_the_future_but_expiry_must_follow_it():
    intention = complete_graph()[4]
    intention["valid_from"] = ts(60)
    intention["expires_at"] = ts(120)
    validate_provenance_record(intention)
    intention["expires_at"] = ts(30)

    with pytest.raises(ProvenanceValidationError, match="must follow valid_from"):
        validate_provenance_record(intention)


def test_graph_validation_does_not_mutate_caller_records():
    records = complete_graph()
    original = copy.deepcopy(records)

    validate_provenance_graph(records)

    assert records == original


def test_claim_key_is_required_and_derivable_without_exposing_the_subject():
    from src.open_llm_vtuber.provenance import derive_claim_key

    candidate = complete_graph()[0]
    del candidate["claim_key"]
    with pytest.raises(ProvenanceValidationError, match=r"missing=\['claim_key'\]"):
        validate_provenance_record(candidate)

    key = derive_claim_key("pet.name", "the user's cat Pep")
    assert "Pep" not in key
    assert key == derive_claim_key("pet.name", "the user's cat Pep")
    assert key != derive_claim_key("pet.name", "the user's cat Bo")


def test_supersession_must_answer_the_same_claim():
    old = record(
        "assertion-old",
        "assertion",
        second=0,
        source_class="user",
        authority="reported",
        status="superseded",
        claim_key="pet.name",
    )
    new = record(
        "assertion-new",
        "assertion",
        second=1,
        source_class="user",
        authority="reported",
        supersedes_id="assertion-old",
        claim_key="pet.breed",
    )

    with pytest.raises(ProvenanceValidationError, match="same claim"):
        validate_provenance_graph([old, new])


def test_a_contradiction_link_survives_the_contradiction_being_resolved():
    """Resolution must not require erasing why the winner won."""

    winner = record(
        "belief-winner",
        "belief",
        second=2,
        source_class="model",
        authority="inferred",
        evidence_ids=["assertion-1"],
        confidence=0.6,
        status="contested",
        contradiction_ids=["belief-loser"],
    )
    loser = record(
        "belief-loser",
        "belief",
        second=2,
        source_class="model",
        authority="inferred",
        evidence_ids=["assertion-1"],
        confidence=0.5,
        status="contested",
        contradiction_ids=["belief-winner"],
    )
    graph = [complete_graph()[0], winner, loser]

    # While both are live, neither may present itself as settled.
    winner["status"] = "active"
    with pytest.raises(ProvenanceValidationError, match="is unresolved"):
        validate_provenance_graph(graph)

    # Once the loser is retracted, the winner becomes active and keeps the link.
    loser["status"] = "retracted"
    validate_provenance_graph(graph)
    assert winner["contradiction_ids"] == ["belief-loser"]


def test_a_long_supersession_chain_does_not_exhaust_the_stack():
    """A deep memory is not a corrupt one."""

    depth = 2_000
    chain = [
        record(
            "assertion-0",
            "assertion",
            second=0,
            source_class="user",
            authority="reported",
            status="superseded",
            claim_key="pet.name",
        )
    ]
    for step in range(1, depth):
        chain.append(
            record(
                f"assertion-{step}",
                "assertion",
                second=step,
                source_class="user",
                authority="reported",
                status="superseded" if step < depth - 1 else "active",
                supersedes_id=f"assertion-{step - 1}",
                claim_key="pet.name",
            )
        )

    validate_provenance_graph(chain)


def test_only_live_records_speak_for_a_claim():
    from src.open_llm_vtuber.provenance import is_effective

    active = complete_graph()[0]
    assert is_effective(active, _START + timedelta(seconds=10))

    expiring = record(
        "assertion-expiring",
        "assertion",
        second=0,
        source_class="user",
        authority="reported",
    )
    expiring["expires_at"] = ts(30)
    assert is_effective(expiring, _START + timedelta(seconds=10))
    assert not is_effective(expiring, _START + timedelta(seconds=30))

    for retired in ("superseded", "retracted", "tombstoned"):
        candidate = dict(active, status=retired)
        assert not is_effective(candidate, _START + timedelta(seconds=10))
