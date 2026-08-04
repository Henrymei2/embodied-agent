"""The provenance inspector: the demo beat that makes invisible work visible."""

from __future__ import annotations

import pytest

from src.open_llm_vtuber.provenance.inspection import (
    build_chain,
    recent_roots,
)


def _record(
    record_id,
    record_type,
    *,
    evidence=(),
    claim="topic:x",
    at="2026-08-03T12:00:00+00:00",
    confidence=None,
    status="active",
    authority="model",
):
    return {
        "record_id": record_id,
        "record_type": record_type,
        "claim_key": claim,
        "source_class": "model",
        "source_id": "agent",
        "authority": authority,
        "status": status,
        "confidence": confidence,
        "recorded_at": at,
        "evidence_ids": list(evidence),
        "content_digest": "d" * 64,
    }


def _story():
    return [
        _record("obs1", "observation", at="2026-08-03T12:00:00+00:00"),
        _record(
            "bel1",
            "belief",
            evidence=["obs1"],
            confidence=0.8,
            at="2026-08-03T12:00:01+00:00",
        ),
        _record("int1", "intention", evidence=["bel1"], at="2026-08-03T12:00:02+00:00"),
        _record("out1", "outcome", evidence=["int1"], at="2026-08-03T12:00:03+00:00"),
        _record("unrelated", "outcome", at="2026-08-03T11:00:00+00:00"),
    ]


def test_a_chain_reads_as_a_story_from_observation_to_outcome():
    chain = build_chain(_story(), "out1")
    assert [link.record_type for link in chain.links] == [
        "observation",
        "belief",
        "intention",
        "outcome",
    ]
    assert [link.stage_label for link in chain.links] == [
        "Observed",
        "Believed",
        "Planned",
        "Result",
    ]
    assert chain.complete is True


def test_an_unrelated_record_is_not_dragged_in():
    chain = build_chain(_story(), "out1")
    assert "unrelated" not in {link.record_id for link in chain.links}


def test_deleted_evidence_shows_a_gap_rather_than_a_plausible_chain():
    """A chain that looks whole and is not would make this untrustworthy."""
    records = [r for r in _story() if r["record_id"] != "obs1"]
    chain = build_chain(records, "out1")
    assert chain.complete is False
    belief = next(link for link in chain.links if link.record_id == "bel1")
    assert belief.missing_evidence == ("obs1",)


def test_the_chain_carries_no_private_content():
    """This goes on a projector; it must be safe by construction."""
    chain = build_chain(_story(), "out1")
    payload = chain.as_dict()
    for link in payload["links"]:
        assert set(link) <= {
            "record_id",
            "record_type",
            "stage",
            "claim_key",
            "source_class",
            "source_id",
            "authority",
            "status",
            "confidence",
            "recorded_at",
            "evidence_ids",
            "content_digest",
            "missing_evidence",
        }
        # Digests are truncated for display and never a full content hash.
        assert len(link["content_digest"]) <= 16


def test_a_cycle_cannot_hang_the_inspector():
    records = [
        _record("a", "belief", evidence=["b"]),
        _record("b", "belief", evidence=["a"]),
    ]
    chain = build_chain(records, "a")
    assert {link.record_id for link in chain.links} == {"a", "b"}


def test_a_long_chain_is_bounded_and_says_so():
    records = [_record("r0", "observation")]
    for index in range(1, 60):
        records.append(_record(f"r{index}", "belief", evidence=[f"r{index - 1}"]))
    chain = build_chain(records, "r59", limit=10)
    assert chain.truncated is True
    assert len(chain.links) <= 10


def test_an_unknown_record_is_refused():
    with pytest.raises(KeyError):
        build_chain(_story(), "nope")


def test_recent_roots_offer_decisions_not_raw_observations():
    """An audience points at what the agent did, not at a leaf."""
    roots = recent_roots(_story())
    kinds = {root["record_type"] for root in roots}
    assert "observation" not in kinds
    assert {"outcome", "intention", "belief"} >= kinds
    # Newest first, so the thing that just happened is at the top.
    assert roots[0]["record_id"] == "out1"


def test_recent_roots_respects_its_limit():
    records = [
        _record(f"o{i}", "outcome", at=f"2026-08-03T12:00:{i:02d}+00:00")
        for i in range(30)
    ]
    assert len(recent_roots(records, limit=5)) == 5


def test_malformed_records_do_not_break_the_inspector():
    records = _story() + ["not a record", {}, {"record_id": None}]
    chain = build_chain(records, "out1")
    assert chain.links
