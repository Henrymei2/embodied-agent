from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.provenance import (
    PolicyContext,
    ProvenanceLedger,
    ProvenanceValidationError,
    ingest_record,
)

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


class Clock:
    """One clock for the ledger and the policy, because two would drift."""

    def __init__(self) -> None:
        self.now = _START

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


def make_ledger(tmp_path, clock: Clock) -> ProvenanceLedger:
    counter = {"value": 0}

    def ids() -> str:
        counter["value"] += 1
        return f"{counter['value']:06d}"

    return ProvenanceLedger(tmp_path / "ledger", clock=clock, id_factory=ids)


class Session:
    """Ingest helper that keeps time moving forward the way a session does."""

    def __init__(self, tmp_path, **context) -> None:
        self.clock = Clock()
        self.ledger = make_ledger(tmp_path, self.clock)
        self.offset = 0
        self.context_kwargs = context

    def context(self, **overrides) -> PolicyContext:
        kwargs = {**self.context_kwargs, **overrides}
        kwargs.setdefault("now", self.clock.now)
        return PolicyContext(**kwargs)

    def ingest(self, claim, content, *, observed_minutes_ago=0, **fields):
        self.offset += 1
        context = fields.pop("context", None) or self.context()
        ref = f"blob-{self.offset}"
        stored = self.ledger.content_store.put(ref, content)
        defaults = {
            "record_type": "assertion",
            "source_class": "user",
            "source_id": "participant-1",
            "authority": "reported",
            "privacy_class": "private",
        }
        defaults.update(fields)
        return ingest_record(
            self.ledger,
            context=context,
            claim_key=claim,
            content_ref=stored.content_ref,
            content_digest=stored.content_digest,
            observed_at=(
                context.now - timedelta(minutes=observed_minutes_ago)
            ).isoformat(),
            **defaults,
        )


def belief(**overrides):
    fields = {
        "record_type": "belief",
        "source_class": "model",
        "source_id": "llm-1",
        "authority": "inferred",
        "privacy_class": "internal",
        "confidence": 0.95,
    }
    fields.update(overrides)
    return fields


def test_a_confident_model_cannot_overwrite_what_the_user_said(tmp_path):
    """The gate: contradictory evidence never silently outranks the user."""

    session = Session(tmp_path)
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    guess = session.ingest(
        "pet.name",
        "The cat is called Bo.",
        **belief(evidence_ids=[stated.record["record_id"]]),
    )

    assert [item.decision for item in guess.decisions] == ["retract_candidate"]
    assert [item.reason for item in guess.decisions] == ["lower_authority"]
    assert guess.status == "retracted"
    # The user's statement is untouched, and the disagreement is on the record.
    incumbent = session.ledger.get(stated.record["record_id"])
    assert incumbent["status"] == "active"
    assert incumbent["contradiction_ids"] == [guess.record["record_id"]]


def test_the_user_correcting_a_model_conclusion_retires_the_conclusion(tmp_path):
    session = Session(tmp_path)
    seed = session.ingest("pet.name", "I have a cat.")
    concluded = session.ingest(
        "pet.mood",
        "The cat is anxious.",
        **belief(evidence_ids=[seed.record["record_id"]]),
    )
    corrected = session.ingest("pet.mood", "She is just loud, not anxious.")

    assert [item.decision for item in corrected.decisions] == ["retract_incumbent"]
    assert session.ledger.get(concluded.record["record_id"])["status"] == "retracted"
    assert corrected.status == "active"


def test_a_source_may_update_itself(tmp_path):
    session = Session(tmp_path)
    first = session.ingest("pet.name", "Her cat is called Pep.")
    second = session.ingest("pet.name", "Actually, it is Bo now.")

    assert [item.decision for item in second.decisions] == ["supersede"]
    assert second.record["supersedes_id"] == first.record["record_id"]
    assert session.ledger.get(first.record["record_id"])["status"] == "superseded"


def test_two_sensors_that_disagree_are_asked_to_look_again(tmp_path):
    session = Session(tmp_path)
    observation = {
        "record_type": "observation",
        "source_class": "sensor",
        "authority": "observed",
        "privacy_class": "internal",
    }
    session.ingest("lamp.state", "on", source_id="camera-1", **observation)
    second = session.ingest("lamp.state", "off", source_id="camera-2", **observation)

    assert [item.decision for item in second.decisions] == ["re_observe"]
    assert [item.reason for item in second.decisions] == ["conflicting_observations"]
    assert second.status == "contested"


def test_two_people_may_prefer_different_things(tmp_path):
    session = Session(tmp_path)
    preference = {
        "record_type": "preference",
        "source_class": "user",
        "authority": "user_authoritative",
    }
    session.ingest("music.volume", "quiet", source_id="participant-1", **preference)
    other = session.ingest(
        "music.volume", "loud", source_id="participant-2", **preference
    )

    assert [item.decision for item in other.decisions] == ["retain_both"]
    assert other.status == "contested"
    assert len(session.ledger.records_for_claim("music.volume")) == 2


def test_asking_is_a_cost_that_needs_the_user_to_be_there(tmp_path):
    present = Session(tmp_path / "present", user_present=True, attention_available=True)
    present.ingest("meeting.time", "three o'clock", source_id="participant-1")
    asked = present.ingest("meeting.time", "four o'clock", source_id="participant-2")
    assert [item.decision for item in asked.decisions] == ["ask"]
    assert asked.questions

    absent = Session(tmp_path / "absent", user_present=False)
    absent.ingest("meeting.time", "three o'clock", source_id="participant-1")
    deferred = absent.ingest("meeting.time", "four o'clock", source_id="participant-2")
    assert [item.decision for item in deferred.decisions] == ["defer"]
    assert deferred.decisions[0].reason == "user_absent"
    assert deferred.decisions[0].defer_seconds == 900
    # Deferring settles nothing, so both claims stay live and visibly contested.
    assert deferred.status == "contested"


def test_a_stale_claim_earns_a_second_look_but_not_an_overwrite(tmp_path):
    session = Session(tmp_path)
    stated = session.ingest("lamp.state", "on", observed_minutes_ago=48 * 60)
    observed = session.ingest(
        "lamp.state",
        "off",
        record_type="observation",
        source_class="sensor",
        source_id="camera-1",
        authority="observed",
        privacy_class="internal",
    )

    assert [item.decision for item in observed.decisions] == ["re_observe"]
    assert [item.reason for item in observed.decisions] == ["stale_incumbent"]
    assert session.ledger.get(stated.record["record_id"])["status"] == "contested"


def test_a_claim_whose_evidence_was_deleted_is_never_written(tmp_path):
    session = Session(tmp_path)
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    session.ledger.tombstone(stated.record["record_id"])

    before = session.ledger.sequence
    deferred = session.ingest(
        "pet.mood",
        "The cat is happy.",
        **belief(evidence_ids=[stated.record["record_id"]]),
    )

    assert deferred.record is None
    assert deferred.status == "deferred"
    assert deferred.decisions[0].reason == "evidence_unavailable"
    assert session.ledger.sequence == before


def test_evidence_that_does_not_exist_is_refused_outright(tmp_path):
    session = Session(tmp_path)

    with pytest.raises(ProvenanceValidationError, match="not in the ledger"):
        session.ingest("pet.mood", "happy", **belief(evidence_ids=["rec-nope"]))


def test_agreeing_records_are_corroboration_rather_than_conflict(tmp_path):
    """Same claim, same answer: two sources saying it is not a disagreement."""

    session = Session(tmp_path)
    first = session.ingest("pet.name", "Her cat is called Pep.")
    agreeing = session.ingest(
        "pet.name", "Her cat is called Pep.", source_id="participant-2"
    )

    assert first.record["content_digest"] == agreeing.record["content_digest"]
    assert agreeing.decisions == []
    assert agreeing.status == "active"


def test_a_claim_that_loses_one_conflict_still_records_the_others(tmp_path):
    """Losing a conflict must not stop the ledger recording the rest of them."""

    session = Session(tmp_path)
    # Two people say the same thing, so they corroborate rather than conflict,
    # and both are still live when the model disagrees with both of them.
    stated = session.ingest("pet.name", "Her cat is called Pep.")
    agreed = session.ingest(
        "pet.name", "Her cat is called Pep.", source_id="participant-2"
    )
    guess = session.ingest(
        "pet.name",
        "The cat is called Bo.",
        **belief(evidence_ids=[stated.record["record_id"]]),
    )

    assert sorted(item.decision for item in guess.decisions) == [
        "retract_candidate",
        "retract_candidate",
    ]
    assert guess.status == "retracted"
    # Both conflicts are on the record, not just whichever came first.
    assert sorted(guess.record["contradiction_ids"]) == sorted(
        [stated.record["record_id"], agreed.record["record_id"]]
    )
    session.ledger.verify()


def test_only_one_incumbent_can_be_superseded_and_the_rest_are_retracted(tmp_path):
    session = Session(tmp_path)
    sensor = {
        "record_type": "observation",
        "source_class": "sensor",
        "authority": "observed",
        "privacy_class": "internal",
    }
    first = session.ingest("lamp.state", "on", source_id="camera-1", **sensor)
    second = session.ingest("lamp.state", "dim", source_id="camera-2", **sensor)
    # Same identity as the first source, so it supersedes that one; the other
    # live claim it conflicts with cannot also be superseded.
    third = session.ingest("lamp.state", "off", source_id="camera-1", **sensor)

    assert third.record["supersedes_id"] == first.record["record_id"]
    assert session.ledger.get(first.record["record_id"])["status"] == "superseded"
    assert session.ledger.get(second.record["record_id"])["status"] in {
        "contested",
        "retracted",
    }
    session.ledger.verify()
