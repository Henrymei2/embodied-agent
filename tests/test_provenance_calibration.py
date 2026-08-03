from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.open_llm_vtuber.provenance import (
    SourceCalibrator,
    build_provenance_record,
    calibration_samples,
    calibrator_from_records,
    content_digest,
)

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def ts(seconds: int) -> str:
    return (_START + timedelta(seconds=seconds)).isoformat()


def claim_pair(index: int, *, confidence: float, correct: bool) -> list[dict]:
    """An observation, the belief drawn from it, and the outcome that graded it."""

    claim = f"claim.{index}"
    guessed = content_digest(f"value-{index}")
    truth = guessed if correct else content_digest(f"other-{index}")
    # Deliberately a different claim key: support for a belief is not a verdict
    # on it, and must not be mistaken for one.
    support = build_provenance_record(
        record_id=f"observation-{index:04d}",
        record_type="observation",
        claim_key=f"{claim}.support",
        source_class="sensor",
        source_id="camera-1",
        authority="observed",
        observed_at=ts(index * 10 - 1),
        recorded_at=ts(index * 10 - 1),
        valid_from=ts(index * 10 - 1),
        privacy_class="internal",
        content_ref=f"blob-support-{index}",
        content_digest=content_digest(f"support-{index}"),
    )
    belief = build_provenance_record(
        record_id=f"belief-{index:04d}",
        record_type="belief",
        claim_key=claim,
        source_class="model",
        source_id="llm-1",
        authority="inferred",
        observed_at=ts(index * 10),
        recorded_at=ts(index * 10),
        valid_from=ts(index * 10),
        privacy_class="internal",
        content_ref=f"blob-belief-{index}",
        content_digest=guessed,
        evidence_ids=[f"observation-{index:04d}"],
        confidence=confidence,
    )
    outcome = build_provenance_record(
        record_id=f"outcome-{index:04d}",
        record_type="outcome",
        claim_key=claim,
        source_class="tool",
        source_id="verifier-1",
        authority="observed",
        observed_at=ts(index * 10 + 5),
        recorded_at=ts(index * 10 + 5),
        valid_from=ts(index * 10 + 5),
        privacy_class="internal",
        content_ref=f"blob-outcome-{index}",
        content_digest=truth,
        evidence_ids=[f"belief-{index:04d}"],
    )
    return [support, belief, outcome]


def records_for(pattern: list[tuple[float, bool]]) -> list[dict]:
    records: list[dict] = []
    for index, (confidence, correct) in enumerate(pattern):
        records.extend(claim_pair(index, confidence=confidence, correct=correct))
    return records


def test_a_stated_confidence_is_evidence_not_a_probability():
    """The model says 0.9 every time and is right half of them."""

    pattern = [(0.9, index % 2 == 0) for index in range(40)]
    calibrator = calibrator_from_records(records_for(pattern), split="held_out")

    report = calibrator.report(generated_at=_START)["sources"]["model"]
    assert report["status"] == "measured"
    assert report["samples"] == 40
    assert report["mean_reported_confidence"] == 0.9
    assert report["accuracy"] == 0.5
    assert report["overconfidence"] == 0.4
    # What the caller gets is the measured rate, not the claimed one.
    calibrated = calibrator.calibrate("model", 0.9)
    assert 0.45 < calibrated < 0.55


def test_an_unmeasured_source_is_unavailable_rather_than_a_coin_flip():
    calibrator = calibrator_from_records(
        records_for([(0.8, True), (0.8, False)]), min_samples=20
    )

    assert calibrator.calibrate("model", 0.8) is None
    assert not calibrator.is_measured("model")
    source = calibrator.report(generated_at=_START)["sources"]["model"]
    assert source == {
        "status": "unavailable",
        "reason": "insufficient_samples",
        "samples": 2,
        "required_samples": 20,
    }
    # Every declared source class is accounted for, present or not.
    assert calibrator.report(generated_at=_START)["sources"]["sensor"]["samples"] == 0


def test_a_thin_bin_cannot_claim_certainty():
    pattern = [(0.9, index % 2 == 0) for index in range(40)]
    pattern.append((0.1, True))  # one lonely low-confidence hit
    calibrator = calibrator_from_records(records_for(pattern))

    assert calibrator.calibrate("model", 0.1) < 0.7


def test_a_claim_cannot_be_graded_by_its_own_authority_or_by_hindsight():
    records = records_for([(0.9, True)])
    _support, belief, outcome = records

    # Equal standing: a second model belief may not verify the first.
    peer = dict(outcome, record_id="peer-1", source_class="model", authority="inferred")
    peer["record_type"] = "belief"
    peer["confidence"] = 0.5
    assert calibration_samples([belief, peer]) == []

    # Earlier evidence is not a verification of a later claim either.
    earlier = dict(outcome, record_id="earlier-1", recorded_at=ts(-100))
    assert calibration_samples([belief, earlier]) == []

    assert len(calibration_samples(records)) == 1


def test_the_calibration_report_is_shareable_and_content_free():
    pattern = [(0.7, index % 3 != 0) for index in range(30)]
    report = calibrator_from_records(records_for(pattern), split="held_out").report(
        generated_at=_START
    )

    encoded = json.dumps(report)
    assert "blob-" not in encoded
    assert report["split"] == "held_out"
    assert report["total_samples"] == 30
    assert report["sources"]["model"]["brier_score"] > 0
    assert report["sources"]["model"]["expected_calibration_error"] >= 0


def test_samples_carry_the_pair_that_produced_them():
    samples = calibration_samples(records_for([(0.6, False)]))

    assert len(samples) == 1
    assert samples[0].record_id == "belief-0000"
    assert samples[0].verifier_id == "outcome-0000"
    assert samples[0].correct is False
    assert SourceCalibrator(samples, min_samples=1).is_measured("model")
