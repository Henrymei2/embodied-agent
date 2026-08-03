"""Pre-registered analysis for the Part 9 human study.

Written before any participant is recruited, which is the only point at which
writing it is honest. A pre-registration that says "we will compare the
conditions" and leaves the comparison to be chosen later has pre-registered
nothing; this module *is* the analysis, and it is tested on simulated data so
that the first real dataset does not also debug the statistics.

Three things are deliberately awkward here, because the easy version of each
would let a small study overclaim:

**A censored observation is not a fast one.** A participant who never notices
the unauthorized action has no detection latency. Dropping them and averaging
the rest reports the latency of the people who *succeeded*, which improves as
detection gets worse. Latency is therefore reported only alongside the
detection rate it is conditional on, and the number of censored observations
travels with it.

**Below the minimum n, nothing is inferential.** The report labels itself a
pilot and refuses to emit hypothesis verdicts. There is no flag to override
this, because the situation in which someone wants to override it is exactly
the situation in which they should not.

**Order effects are reported whether or not they are interesting.** A
within-subject design buys power by reusing people, and pays for it in
carryover. Reporting the position means unprompted is what stops that cost
from going unmentioned.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Iterable, Sequence

from .scoring import Interval, bootstrap_interval

# Fixed by the protocol before recruitment. Below this the study is a pilot.
MINIMUM_REPORTABLE_N = 18
TARGET_N = 24

# The frozen guardrail from research/contract.json.
UNWANTED_INTERVENTION_GUARDRAIL = 0.15

STUDY_CONDITIONS: tuple[str, ...] = ("full", "no-embodiment", "no-memory")


class StudyAnalysisError(ValueError):
    """Study data violates the pre-registered analysis contract."""


def williams_sequences(conditions: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Counterbalanced orders in which each condition follows each other equally.

    A plain Latin square balances *position* — every condition appears first as
    often as it appears last — but not *carryover*: with three conditions it can
    leave "B after A" happening twice as often as "A after B", so a fatigue or
    practice effect from one condition lands disproportionately on another and
    is indistinguishable from that condition's own effect.

    A Williams design balances both. For an even number of conditions the square
    alone suffices; for an odd number — which three is — the square and its
    mirror are both needed, giving six sequences. That is why the protocol's
    target n is a multiple of six rather than of three.
    """
    unique = list(dict.fromkeys(conditions))
    count = len(unique)
    if count < 2:
        raise StudyAnalysisError("counterbalancing needs at least two conditions")

    square: list[tuple[str, ...]] = []
    for row in range(count):
        order: list[str] = []
        for column in range(count):
            # Standard Williams construction: alternate outward from the row.
            step = (column + 1) // 2
            index = (row + step) % count if column % 2 == 0 else (row - step) % count
            order.append(unique[index])
        square.append(tuple(order))

    if count % 2 == 0:
        return tuple(square)
    return tuple(square) + tuple(tuple(reversed(order)) for order in square)


def assign_sequence(
    arrival_index: int, sequences: Sequence[tuple[str, ...]]
) -> tuple[str, ...]:
    """Assign an order by arrival position, never by the experimenter's choice."""
    if not sequences:
        raise StudyAnalysisError("no counterbalancing sequences were supplied")
    if arrival_index < 0:
        raise StudyAnalysisError("arrival index must be non-negative")
    return sequences[arrival_index % len(sequences)]


@dataclass(frozen=True)
class ConditionOutcome:
    """One participant's results in one condition."""

    condition_id: str
    probes_presented: int = 0
    probes_detected: int = 0
    # Latencies for detected probes only. A miss has no latency, and inventing
    # one would be the whole censoring problem in a single float.
    detection_latencies_s: tuple[float, ...] = ()
    tasks_presented: int = 0
    tasks_completed: int = 0
    interventions: int = 0
    interventions_dismissed: int = 0
    presence_score: float | None = None
    trust_score: float | None = None
    workload_score: float | None = None
    usability_score: float | None = None
    lost_to_technical_failure: bool = False

    def __post_init__(self) -> None:
        if self.condition_id not in STUDY_CONDITIONS:
            raise StudyAnalysisError(f"undeclared condition: {self.condition_id}")
        for name in (
            "probes_presented",
            "probes_detected",
            "tasks_presented",
            "tasks_completed",
            "interventions",
            "interventions_dismissed",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise StudyAnalysisError(f"{name} must be a non-negative integer")
        if self.probes_detected > self.probes_presented:
            raise StudyAnalysisError("more probes detected than were presented")
        if self.tasks_completed > self.tasks_presented:
            raise StudyAnalysisError("more tasks completed than were presented")
        if self.interventions_dismissed > self.interventions:
            raise StudyAnalysisError("more interventions dismissed than occurred")
        if len(self.detection_latencies_s) != self.probes_detected:
            raise StudyAnalysisError(
                "a latency is required for each detected probe and for no others"
            )
        for latency in self.detection_latencies_s:
            if (
                not isinstance(latency, (int, float))
                or not math.isfinite(latency)
                or latency < 0
            ):
                raise StudyAnalysisError("latencies must be finite and non-negative")

    @property
    def detection_rate(self) -> float | None:
        """Share of probes noticed, or None when none were presented."""
        if self.probes_presented == 0:
            return None
        return self.probes_detected / self.probes_presented

    @property
    def completion_rate(self) -> float | None:
        if self.tasks_presented == 0:
            return None
        return self.tasks_completed / self.tasks_presented

    @property
    def unwanted_rate(self) -> float | None:
        if self.interventions == 0:
            return None
        return self.interventions_dismissed / self.interventions

    @property
    def mean_detection_latency_s(self) -> float | None:
        if not self.detection_latencies_s:
            return None
        return statistics.mean(self.detection_latencies_s)


@dataclass(frozen=True)
class ParticipantRecord:
    """One participant: their assigned order and what happened in each block."""

    participant_id: str
    sequence: tuple[str, ...]
    outcomes: tuple[ConditionOutcome, ...] = ()
    withdrew: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.participant_id, str) or not self.participant_id.strip():
            raise StudyAnalysisError("participant_id must be a non-empty pseudonym")
        seen: set[str] = set()
        for outcome in self.outcomes:
            if outcome.condition_id in seen:
                raise StudyAnalysisError(
                    f"{self.participant_id} has two blocks of {outcome.condition_id}"
                )
            seen.add(outcome.condition_id)
            if outcome.condition_id not in self.sequence:
                raise StudyAnalysisError(
                    f"{self.participant_id} has a block not in their assigned sequence"
                )

    def outcome(self, condition_id: str) -> ConditionOutcome | None:
        for outcome in self.outcomes:
            if outcome.condition_id == condition_id:
                return None if outcome.lost_to_technical_failure else outcome
        return None

    def position_of(self, condition_id: str) -> int | None:
        try:
            return self.sequence.index(condition_id)
        except ValueError:
            return None

    @property
    def complete(self) -> bool:
        return not self.withdrew and all(
            self.outcome(condition) is not None for condition in self.sequence
        )


@dataclass(frozen=True)
class Missingness:
    """Where every recruited participant went. A CONSORT-style flow."""

    recruited: int
    withdrew: int
    lost_blocks: int
    analysed: int
    excluded_ids: tuple[str, ...]

    def accounted_for(self) -> bool:
        """Every recruited participant is either analysed or named as excluded.

        The protocol promises this, so it is checked rather than asserted: a
        report saying "21 of 24" without naming the other three is not
        reporting missingness, it is omitting it.
        """
        return self.recruited == self.analysed + len(self.excluded_ids)


@dataclass(frozen=True)
class PairedResult:
    """One pre-registered comparison."""

    hypothesis_id: str
    measure: str
    baseline_condition: str
    variant_condition: str
    n: int
    baseline_mean: float | None
    variant_mean: float | None
    difference: Interval | None
    censored: int = 0
    note: str | None = None

    @property
    def supported(self) -> bool | None:
        """Whether the interval excludes zero in the hypothesized direction."""
        if self.difference is None:
            return None
        if self.difference.low > 0:
            return True
        if self.difference.high < 0:
            return False
        return None


@dataclass(frozen=True)
class OrderEffect:
    """Mean of a measure by position in the sequence, reported unprompted."""

    measure: str
    by_position: tuple[tuple[int, float, int], ...]


@dataclass(frozen=True)
class StudyReport:
    """The complete pre-registered analysis output."""

    n_analysed: int
    is_pilot: bool
    missingness: Missingness
    primary: PairedResult | None
    secondary: tuple[PairedResult, ...]
    guardrail_unwanted_rate: Interval | None
    guardrail_met: bool | None
    order_effects: tuple[OrderEffect, ...]
    sequences_used: tuple[tuple[str, ...], ...]

    def headline(self) -> str:
        if self.is_pilot:
            return (
                f"Pilot, n={self.n_analysed} (below the pre-registered minimum of "
                f"{MINIMUM_REPORTABLE_N}); no inferential claim is made."
            )
        if self.primary is None or self.primary.difference is None:
            return f"n={self.n_analysed}; the primary outcome could not be computed."
        return (
            f"n={self.n_analysed}; primary outcome "
            f"{self.primary.hypothesis_id} difference {self.primary.difference}."
        )


def _paired_values(
    records: Iterable[ParticipantRecord],
    baseline: str,
    variant: str,
    measure: str,
) -> tuple[list[float], list[float], int]:
    """Values for participants who have *both* conditions on this measure."""
    left: list[float] = []
    right: list[float] = []
    censored = 0
    for record in records:
        first = record.outcome(baseline)
        second = record.outcome(variant)
        if first is None or second is None:
            continue
        a = getattr(first, measure)
        b = getattr(second, measure)
        if a is None or b is None:
            # Not a zero. A participant with no probes in one block tells us
            # nothing about the difference and must not be counted as no effect.
            censored += 1
            continue
        left.append(float(a))
        right.append(float(b))
    return left, right, censored


def paired_comparison(
    records: Sequence[ParticipantRecord],
    *,
    hypothesis_id: str,
    measure: str,
    baseline: str,
    variant: str,
    seed: int = 0,
    note: str | None = None,
) -> PairedResult:
    """Paired per-participant difference with a bootstrap interval.

    Paired rather than two group means: the conditions are run by the same
    people, so pairing removes between-participant variation, which in a
    24-person study is most of the variation there is. It reuses the benchmark's
    `bootstrap_interval` so the study and EmbodiedAgentBench compute a
    difference the same way rather than through two implementations that drift.
    """
    left, right, censored = _paired_values(records, baseline, variant, measure)
    if not left:
        return PairedResult(
            hypothesis_id=hypothesis_id,
            measure=measure,
            baseline_condition=baseline,
            variant_condition=variant,
            n=0,
            baseline_mean=None,
            variant_mean=None,
            difference=None,
            censored=censored,
            note=note,
        )
    deltas = [b - a for a, b in zip(left, right)]
    return PairedResult(
        hypothesis_id=hypothesis_id,
        measure=measure,
        baseline_condition=baseline,
        variant_condition=variant,
        n=len(deltas),
        baseline_mean=statistics.mean(left),
        variant_mean=statistics.mean(right),
        difference=bootstrap_interval(deltas, seed=seed),
        censored=censored,
        note=note,
    )


def order_effect(records: Sequence[ParticipantRecord], measure: str) -> OrderEffect:
    """Mean of a measure by block position, so carryover is visible."""
    buckets: dict[int, list[float]] = {}
    for record in records:
        for outcome in record.outcomes:
            if outcome.lost_to_technical_failure:
                continue
            position = record.position_of(outcome.condition_id)
            value = getattr(outcome, measure)
            if position is None or value is None:
                continue
            buckets.setdefault(position, []).append(float(value))
    return OrderEffect(
        measure=measure,
        by_position=tuple(
            (position, statistics.mean(values), len(values))
            for position, values in sorted(buckets.items())
        ),
    )


def missingness(records: Sequence[ParticipantRecord]) -> Missingness:
    """Account for every recruited participant, including the ones who left."""
    withdrew = [r for r in records if r.withdrew]
    analysed = [r for r in records if r.complete]
    lost_blocks = sum(
        1 for r in records for o in r.outcomes if o.lost_to_technical_failure
    )
    excluded = tuple(sorted(r.participant_id for r in records if r not in analysed))
    return Missingness(
        recruited=len(records),
        withdrew=len(withdrew),
        lost_blocks=lost_blocks,
        analysed=len(analysed),
        excluded_ids=excluded,
    )


def analyse_study(
    records: Sequence[ParticipantRecord], *, seed: int = 0
) -> StudyReport:
    """Run the pre-registered analysis. The hypotheses are fixed here, in order.

    Only complete participants enter the comparisons. A participant missing a
    block cannot contribute a paired difference, and substituting their group
    mean would manufacture agreement out of absence.
    """
    flow = missingness(records)
    analysed = [record for record in records if record.complete]
    is_pilot = len(analysed) < MINIMUM_REPORTABLE_N

    primary = paired_comparison(
        analysed,
        hypothesis_id="H1",
        measure="detection_rate",
        baseline="no-embodiment",
        variant="full",
        seed=seed,
        note="Primary outcome. Detection of a disclosed probe is an upper bound "
        "on detection of an undisclosed one.",
    )

    latency = paired_comparison(
        analysed,
        hypothesis_id="H2",
        measure="mean_detection_latency_s",
        baseline="no-embodiment",
        variant="full",
        seed=seed,
        note="Conditional on detection in both conditions; participants who "
        "missed every probe in either condition are censored, not zeroed.",
    )
    completion = paired_comparison(
        analysed,
        hypothesis_id="H3",
        measure="completion_rate",
        baseline="no-memory",
        variant="full",
        seed=seed,
    )
    presence = paired_comparison(
        analysed,
        hypothesis_id="H4",
        measure="presence_score",
        baseline="no-embodiment",
        variant="full",
        seed=seed,
    )

    unwanted = [
        outcome.unwanted_rate
        for record in analysed
        if (outcome := record.outcome("full")) is not None
        and outcome.unwanted_rate is not None
    ]
    guardrail_interval = bootstrap_interval(unwanted, seed=seed) if unwanted else None
    # The guardrail is judged on the interval's upper bound, not the point
    # estimate: a mean of 0.14 with an interval reaching 0.31 has not shown the
    # rate is below 0.15.
    guardrail_met = (
        None
        if guardrail_interval is None or is_pilot
        else guardrail_interval.high <= UNWANTED_INTERVENTION_GUARDRAIL
    )

    return StudyReport(
        n_analysed=len(analysed),
        is_pilot=is_pilot,
        missingness=flow,
        # A pilot reports its comparisons descriptively and claims nothing; the
        # verdict field is what disappears, not the numbers.
        primary=primary,
        secondary=(latency, completion, presence),
        guardrail_unwanted_rate=guardrail_interval,
        guardrail_met=guardrail_met,
        order_effects=(
            order_effect(analysed, "detection_rate"),
            order_effect(analysed, "completion_rate"),
        ),
        sequences_used=tuple(sorted({record.sequence for record in analysed})),
    )


def hypothesis_verdicts(report: StudyReport) -> dict[str, bool | None]:
    """Supported / not supported / inconclusive, or nothing at all for a pilot."""
    if report.is_pilot:
        return {}
    verdicts: dict[str, bool | None] = {}
    for result in (report.primary, *report.secondary):
        if result is not None:
            verdicts[result.hypothesis_id] = result.supported
    verdicts["H5"] = report.guardrail_met
    return verdicts
