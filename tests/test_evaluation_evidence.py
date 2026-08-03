from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.evaluation import (
    EvidenceValidationError,
    build_event_envelope,
    build_run_manifest,
    canonical_digest,
    finish_run_manifest,
    load_research_contract,
    validate_event_chain,
    validate_event_envelope,
    validate_run_manifest,
)

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
_CODE_REVISION = "a" * 40
_FIXTURE_DIGEST = "b" * 64
_CONFIGURATION_DIGEST = "c" * 64


def manifest() -> dict:
    contract = load_research_contract()
    return build_run_manifest(
        run_id="run-001",
        code_revision=_CODE_REVISION,
        worktree_dirty=False,
        contract_revision=contract["contract_revision"],
        condition_id="full",
        scenario_set_revision="scenario-set-v1",
        scenario_split="development",
        fixture_digest=_FIXTURE_DIGEST,
        configuration_digest=_CONFIGURATION_DIGEST,
        model_identifiers=["local-model-v1"],
        engine_identifiers=["mock-clock-v1", "mock-tool-v1"],
        platform={
            "system": "Windows",
            "release": "11",
            "machine": "AMD64",
            "python": "3.11.9",
        },
        seed=7,
        started_at=_START,
    )


def chain() -> list[dict]:
    first = build_event_envelope(
        event_id="event-001",
        run_id="run-001",
        sequence=0,
        timestamp=_START,
        category="observation",
        event_type="fixture_received",
        correlation_id="task-001",
        payload={"source_id": "fixture-001", "available": True},
    )
    second = build_event_envelope(
        event_id="event-002",
        run_id="run-001",
        sequence=1,
        timestamp=_START + timedelta(milliseconds=1),
        category="plan",
        event_type="plan_created",
        correlation_id="task-001",
        causation_id=first["event_id"],
        payload={"plan_id": "plan-001", "step_count": 2},
        previous_event_hash=first["event_hash"],
    )
    return [first, second]


def test_run_manifest_is_strict_and_can_reach_a_terminal_state():
    running = manifest()
    validate_run_manifest(running)

    completed = finish_run_manifest(
        running,
        status="completed",
        finished_at=_START + timedelta(seconds=3),
    )

    assert running["status"] == "running"
    assert running["finished_at"] is None
    assert completed["status"] == "completed"
    assert completed["finished_at"].endswith("+00:00")


def test_terminal_manifest_requires_a_finish_after_its_start():
    running = manifest()

    with pytest.raises(EvidenceValidationError, match="cannot precede started_at"):
        finish_run_manifest(
            running,
            status="failed",
            finished_at=_START - timedelta(seconds=1),
        )


def test_terminal_manifest_cannot_be_rewritten():
    completed = finish_run_manifest(
        manifest(),
        status="completed",
        finished_at=_START + timedelta(seconds=1),
    )

    with pytest.raises(EvidenceValidationError, match="only a running manifest"):
        finish_run_manifest(
            completed,
            status="failed",
            finished_at=_START + timedelta(seconds=2),
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("condition_id", "unknown", "must be one of"),
        ("contract_revision", "old", "validated contract revision"),
        ("fixture_digest", "ABC", "lowercase SHA-256"),
        ("scenario_split", "training", "must be one of"),
    ],
)
def test_manifest_rejects_unreproducible_identity_fields(
    field: str,
    value: object,
    message: str,
):
    candidate = manifest()
    candidate[field] = value

    with pytest.raises(EvidenceValidationError, match=message):
        validate_run_manifest(candidate)


def test_manifest_rejects_unversioned_extra_fields():
    candidate = manifest()
    candidate["operator_note"] = "not-versioned"

    with pytest.raises(EvidenceValidationError, match=r"extra=\['operator_note'\]"):
        validate_run_manifest(candidate)


def test_canonical_digest_is_order_independent_and_value_sensitive():
    first = canonical_digest({"b": [2, 3], "a": 1})

    assert first == canonical_digest({"a": 1, "b": [2, 3]})
    assert first != canonical_digest({"a": 1, "b": [2, 4]})


def test_event_chain_validates_with_manifest_identity():
    events = chain()

    validate_event_chain(events, manifest=manifest())

    assert events[0]["previous_event_hash"] is None
    assert events[1]["previous_event_hash"] == events[0]["event_hash"]
    assert events[1]["causation_id"] == events[0]["event_id"]


def test_event_tampering_is_detected_by_the_canonical_hash():
    event = chain()[0]
    event["payload"]["available"] = False

    with pytest.raises(EvidenceValidationError, match="canonical event content"):
        validate_event_envelope(event)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"raw_email_body": "private"}, "forbidden by the privacy contract"),
        ({"summary": "a short private sentence"}, "content-free identifier"),
        ({"nested": {"status": "ok"}}, "JSON scalar"),
        ({"score": float("inf")}, "finite number"),
    ],
)
def test_event_payload_rejects_content_and_unsafe_shapes(
    payload: dict,
    message: str,
):
    with pytest.raises(EvidenceValidationError, match=message):
        build_event_envelope(
            event_id="event-unsafe",
            run_id="run-001",
            sequence=0,
            timestamp=_START,
            category="system",
            event_type="unsafe_event",
            correlation_id="task-001",
            payload=payload,
        )


def test_event_chain_detects_missing_or_reordered_sequence_numbers():
    events = chain()
    skipped = copy.deepcopy(events[1])
    skipped["sequence"] = 2
    skipped["event_hash"] = canonical_digest(
        {key: value for key, value in skipped.items() if key != "event_hash"}
    )

    with pytest.raises(EvidenceValidationError, match="expected 1, got 2"):
        validate_event_chain([events[0], skipped])
    with pytest.raises(EvidenceValidationError, match="expected 0, got 1"):
        validate_event_chain(list(reversed(events)))


def test_event_chain_detects_broken_integrity_links():
    events = chain()
    events[1]["previous_event_hash"] = "d" * 64
    events[1]["event_hash"] = canonical_digest(
        {key: value for key, value in events[1].items() if key != "event_hash"}
    )

    with pytest.raises(EvidenceValidationError, match="preceding event"):
        validate_event_chain(events)


def test_event_causation_must_reference_an_earlier_event():
    events = chain()
    events[1]["causation_id"] = "event-unknown"
    events[1]["event_hash"] = canonical_digest(
        {key: value for key, value in events[1].items() if key != "event_hash"}
    )

    with pytest.raises(EvidenceValidationError, match="earlier event"):
        validate_event_chain(events)


def test_event_chain_must_belong_to_the_manifest_run():
    events = chain()
    events[0]["run_id"] = "run-other"
    events[0]["event_hash"] = canonical_digest(
        {key: value for key, value in events[0].items() if key != "event_hash"}
    )

    with pytest.raises(EvidenceValidationError, match="does not match the chain run"):
        validate_event_chain(events, manifest=manifest())
