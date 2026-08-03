"""How much a source's confidence is actually worth.

A model that says 0.9 is not right nine times in ten; it is right however often
it has been right, and that number has to be measured rather than accepted. So
reported confidence is treated as an *input feature* here, never as a
probability. What comes out is an empirical posterior for "claims from this
source class, at this stated confidence, in this fixture set".

Correctness is derived without reading content. A claim is verified when a
later, higher-authority record answers the same claim key: agreeing digests
mean the earlier claim was right, differing digests mean it was wrong.

When there is not enough evidence to say, the answer is ``None`` and the report
says ``unavailable``. Part 2 established that an unmeasured number is not zero;
an uncalibrated source is not 50/50 either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from .records import (
    AUTHORITY_RANK,
    SOURCE_CLASSES,
    SOURCE_RANK,
    ProvenanceValidationError,
    parse_timestamp,
)

CALIBRATION_SCHEMA_VERSION = 1
DEFAULT_BINS = 5
DEFAULT_MIN_SAMPLES = 20
# Weight of the source-class prior when a single bin is thin. Five pseudo-counts
# keep a bin holding two samples from claiming 100% accuracy.
_PRIOR_WEIGHT = 5.0


@dataclass(frozen=True)
class CalibrationSample:
    """One resolved claim: what was stated, and whether it held up."""

    source_class: str
    reported_confidence: float
    correct: bool
    claim_key: str
    record_id: str
    verifier_id: str


def _rank(record: dict[str, Any]) -> tuple[int, int]:
    return (
        AUTHORITY_RANK.get(record["authority"], 0),
        SOURCE_RANK.get(record["source_class"], 0),
    )


def calibration_samples(
    records: Iterable[dict[str, Any]],
) -> list[CalibrationSample]:
    """Pair every confident claim with the record that later settled it.

    The verifier must outrank the claim and must have been recorded after it;
    a claim cannot be graded by itself, by a peer of equal standing, or by
    hindsight it produced.
    """

    indexed = list(records)
    by_claim: dict[str, list[dict[str, Any]]] = {}
    for record in indexed:
        by_claim.setdefault(record["claim_key"], []).append(record)

    samples: list[CalibrationSample] = []
    for record in indexed:
        # Anything that stated a confidence can be graded on it, whatever kind
        # of record it is. A sensor that reports certainty about a reading is
        # making the same sort of claim a model does.
        confidence = record.get("confidence")
        if confidence is None:
            continue
        recorded_at = parse_timestamp(record["recorded_at"])
        verifier: dict[str, Any] | None = None
        verifier_time: datetime | None = None
        for candidate in by_claim.get(record["claim_key"], []):
            if candidate["record_id"] == record["record_id"]:
                continue
            if candidate["record_type"] not in {"observation", "outcome", "assertion"}:
                continue
            if _rank(candidate) <= _rank(record):
                continue
            candidate_time = parse_timestamp(candidate["recorded_at"])
            if candidate_time <= recorded_at:
                continue
            if verifier_time is None or candidate_time < verifier_time:
                verifier, verifier_time = candidate, candidate_time
        if verifier is None:
            continue
        samples.append(
            CalibrationSample(
                source_class=record["source_class"],
                reported_confidence=float(confidence),
                correct=record["content_digest"] == verifier["content_digest"],
                claim_key=record["claim_key"],
                record_id=record["record_id"],
                verifier_id=verifier["record_id"],
            )
        )
    return sorted(samples, key=lambda sample: sample.record_id)


def _bin_index(confidence: float, bins: int) -> int:
    return min(bins - 1, max(0, int(confidence * bins)))


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class SourceCalibrator:
    """Maps (source class, reported confidence) to a measured probability."""

    def __init__(
        self,
        samples: Iterable[CalibrationSample],
        *,
        bins: int = DEFAULT_BINS,
        min_samples: int = DEFAULT_MIN_SAMPLES,
        split: str = "development",
    ) -> None:
        if not 1 <= int(bins) <= 20:
            raise ProvenanceValidationError("bins must be between 1 and 20")
        self.bins = int(bins)
        self.min_samples = max(1, int(min_samples))
        self.split = split
        self._samples: dict[str, list[CalibrationSample]] = {}
        for sample in samples:
            if sample.source_class not in SOURCE_CLASSES:
                raise ProvenanceValidationError(
                    f"unknown source class {sample.source_class!r}"
                )
            self._samples.setdefault(sample.source_class, []).append(sample)

    def is_measured(self, source_class: str) -> bool:
        return len(self._samples.get(source_class, [])) >= self.min_samples

    def calibrate(self, source_class: str, reported_confidence: float) -> float | None:
        """The measured probability, or ``None`` when it is not yet knowable.

        Returning ``None`` is the point: a caller that needs a number must
        decide what to do without evidence, rather than inheriting a guess
        dressed up as a measurement.
        """

        samples = self._samples.get(source_class, [])
        if len(samples) < self.min_samples:
            return None
        prior = _mean([1.0 if sample.correct else 0.0 for sample in samples])
        index = _bin_index(float(reported_confidence), self.bins)
        in_bin = [
            sample
            for sample in samples
            if _bin_index(sample.reported_confidence, self.bins) == index
        ]
        successes = sum(1.0 for sample in in_bin if sample.correct)
        return (successes + _PRIOR_WEIGHT * prior) / (len(in_bin) + _PRIOR_WEIGHT)

    def report(self, *, generated_at: datetime | None = None) -> dict[str, Any]:
        """A shareable, content-free calibration report by source class."""

        sources: dict[str, Any] = {}
        for source_class in sorted(SOURCE_CLASSES):
            samples = self._samples.get(source_class, [])
            if len(samples) < self.min_samples:
                sources[source_class] = {
                    "status": "unavailable",
                    "reason": "insufficient_samples",
                    "samples": len(samples),
                    "required_samples": self.min_samples,
                }
                continue
            correct = [1.0 if sample.correct else 0.0 for sample in samples]
            reported = [sample.reported_confidence for sample in samples]
            accuracy = _mean(correct)
            brier = _mean(
                [
                    (sample.reported_confidence - (1.0 if sample.correct else 0.0)) ** 2
                    for sample in samples
                ]
            )
            bin_rows: list[dict[str, Any]] = []
            calibration_error = 0.0
            for index in range(self.bins):
                in_bin = [
                    sample
                    for sample in samples
                    if _bin_index(sample.reported_confidence, self.bins) == index
                ]
                if not in_bin:
                    continue
                bin_accuracy = _mean(
                    [1.0 if sample.correct else 0.0 for sample in in_bin]
                )
                bin_confidence = _mean(
                    [sample.reported_confidence for sample in in_bin]
                )
                calibration_error += (
                    len(in_bin) / len(samples) * abs(bin_accuracy - bin_confidence)
                )
                bin_rows.append(
                    {
                        "lower": round(index / self.bins, 4),
                        "upper": round((index + 1) / self.bins, 4),
                        "samples": len(in_bin),
                        "mean_reported_confidence": round(bin_confidence, 4),
                        "accuracy": round(bin_accuracy, 4),
                        "calibrated_confidence": round(
                            self.calibrate(source_class, (index + 0.5) / self.bins)
                            or 0.0,
                            4,
                        ),
                    }
                )
            sources[source_class] = {
                "status": "measured",
                "samples": len(samples),
                "accuracy": round(accuracy, 4),
                "mean_reported_confidence": round(_mean(reported), 4),
                "overconfidence": round(_mean(reported) - accuracy, 4),
                "brier_score": round(brier, 4),
                "expected_calibration_error": round(calibration_error, 4),
                "bins": bin_rows,
            }
        total = sum(len(items) for items in self._samples.values())
        return {
            "schema_version": CALIBRATION_SCHEMA_VERSION,
            "split": self.split,
            "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
            "bins": self.bins,
            "min_samples": self.min_samples,
            "total_samples": total,
            "sources": sources,
        }


def calibrator_from_records(
    records: Iterable[dict[str, Any]],
    *,
    bins: int = DEFAULT_BINS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    split: str = "development",
) -> SourceCalibrator:
    return SourceCalibrator(
        calibration_samples(records),
        bins=bins,
        min_samples=min_samples,
        split=split,
    )
