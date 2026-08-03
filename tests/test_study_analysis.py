"""Part 9 slice 9.2: the pre-registered analysis, tested on simulated data.

The pre-registration checklist requires the analysis script to be written and
tested before recruitment. These tests are that step: they run the real
analysis over synthetic participants with known effects, so the first real
dataset is not also the first time the statistics execute.
"""

from __future__ import annotations

import random

import pytest

from src.open_llm_vtuber.evaluation.study_analysis import (
    MINIMUM_REPORTABLE_N,
    STUDY_CONDITIONS,
    UNWANTED_INTERVENTION_GUARDRAIL,
    ConditionOutcome,
    ParticipantRecord,
    StudyAnalysisError,
    analyse_study,
    assign_sequence,
    hypothesis_verdicts,
    missingness,
    order_effect,
    paired_comparison,
    williams_sequences,
)

PROBES_PER_CONDITION = 3


def _outcome(
    condition,
    *,
    detected=0,
    latency=8.0,
    completed=4,
    presented=4,
    interventions=10,
    dismissed=1,
    presence=4.0,
    lost=False,
):
    return ConditionOutcome(
        condition_id=condition,
        probes_presented=PROBES_PER_CONDITION,
        probes_detected=detected,
        detection_latencies_s=tuple([latency] * detected),
        tasks_presented=presented,
        tasks_completed=completed,
        interventions=interventions,
        interventions_dismissed=dismissed,
        presence_score=presence,
        lost_to_technical_failure=lost,
    )


def _cohort(n=24, *, full_detect=3, ablated_detect=1, seed=0, **overrides):
    """Simulated participants with a known embodiment effect on detection."""
    rng = random.Random(seed)
    sequences = williams_sequences(STUDY_CONDITIONS)
    records = []
    for index in range(n):
        sequence = assign_sequence(index, sequences)
        outcomes = []
        for condition in sequence:
            if condition == "full":
                detected = full_detect
                latency = 6.0 + rng.random()
                completed = 4
            elif condition == "no-embodiment":
                detected = ablated_detect
                latency = 14.0 + rng.random()
                completed = 4
            else:  # no-memory
                detected = full_detect
                latency = 6.0 + rng.random()
                completed = 2
            outcomes.append(
                _outcome(
                    condition,
                    detected=detected,
                    latency=latency,
                    completed=completed,
                    presence=5.0 if condition == "full" else 3.0,
                    **overrides,
                )
            )
        records.append(
            ParticipantRecord(f"P{index:03d}", tuple(sequence), tuple(outcomes))
        )
    return records


# --- counterbalancing --------------------------------------------------------


def test_three_conditions_need_six_sequences_not_three():
    """A plain Latin square balances position but not carryover."""
    sequences = williams_sequences(STUDY_CONDITIONS)
    assert len(sequences) == 6
    assert len(set(sequences)) == 6
    for sequence in sequences:
        assert set(sequence) == set(STUDY_CONDITIONS)


def test_every_condition_follows_every_other_equally_often():
    """This is the property a plain Latin square does not give for t=3."""
    pairs: dict[tuple[str, str], int] = {}
    for sequence in williams_sequences(STUDY_CONDITIONS):
        for first, second in zip(sequence, sequence[1:]):
            pairs[(first, second)] = pairs.get((first, second), 0) + 1
    assert len(set(pairs.values())) == 1, f"carryover is unbalanced: {pairs}"
    assert len(pairs) == len(STUDY_CONDITIONS) * (len(STUDY_CONDITIONS) - 1)


def test_every_condition_appears_in_every_position_equally_often():
    positions: dict[tuple[int, str], int] = {}
    for sequence in williams_sequences(STUDY_CONDITIONS):
        for index, condition in enumerate(sequence):
            positions[(index, condition)] = positions.get((index, condition), 0) + 1
    assert len(set(positions.values())) == 1


def test_an_even_number_of_conditions_needs_only_the_square():
    assert len(williams_sequences(("a", "b", "c", "d"))) == 4


def test_sequences_are_assigned_by_arrival_not_by_choice():
    sequences = williams_sequences(STUDY_CONDITIONS)
    assert assign_sequence(0, sequences) == sequences[0]
    assert assign_sequence(6, sequences) == sequences[0]
    with pytest.raises(StudyAnalysisError):
        assign_sequence(-1, sequences)


# --- data validation ---------------------------------------------------------


def test_impossible_outcomes_are_refused():
    with pytest.raises(StudyAnalysisError, match="more probes detected"):
        ConditionOutcome(
            "full",
            probes_presented=1,
            probes_detected=2,
            detection_latencies_s=(1.0, 2.0),
        )
    with pytest.raises(StudyAnalysisError, match="more tasks completed"):
        ConditionOutcome("full", tasks_presented=1, tasks_completed=2)
    with pytest.raises(StudyAnalysisError, match="more interventions dismissed"):
        ConditionOutcome("full", interventions=1, interventions_dismissed=2)
    with pytest.raises(StudyAnalysisError, match="undeclared condition"):
        ConditionOutcome("no-autonomy")


def test_a_latency_is_required_for_each_detection_and_no_others():
    """A miss has no latency; inventing one is the censoring problem in a float."""
    with pytest.raises(StudyAnalysisError, match="latency is required"):
        ConditionOutcome(
            "full", probes_presented=3, probes_detected=2, detection_latencies_s=(1.0,)
        )
    with pytest.raises(StudyAnalysisError, match="latency is required"):
        ConditionOutcome(
            "full", probes_presented=3, probes_detected=0, detection_latencies_s=(1.0,)
        )


def test_a_participant_cannot_have_two_blocks_of_one_condition():
    with pytest.raises(StudyAnalysisError, match="two blocks"):
        ParticipantRecord(
            "P001", ("full", "no-memory"), (_outcome("full"), _outcome("full"))
        )


def test_a_block_outside_the_assigned_sequence_is_refused():
    with pytest.raises(StudyAnalysisError, match="not in their assigned sequence"):
        ParticipantRecord("P001", ("full",), (_outcome("no-memory"),))


# --- rates -------------------------------------------------------------------


def test_a_rate_with_no_denominator_is_none_not_zero():
    outcome = ConditionOutcome("full")
    assert outcome.detection_rate is None
    assert outcome.completion_rate is None
    assert outcome.unwanted_rate is None
    assert outcome.mean_detection_latency_s is None


# --- the pre-registered comparisons -----------------------------------------


def test_the_primary_outcome_recovers_a_known_embodiment_effect():
    report = analyse_study(_cohort(24, full_detect=3, ablated_detect=1))
    assert report.n_analysed == 24
    assert report.is_pilot is False
    assert report.primary.hypothesis_id == "H1"
    assert report.primary.n == 24
    assert report.primary.baseline_mean == pytest.approx(1 / 3)
    assert report.primary.variant_mean == pytest.approx(1.0)
    assert report.primary.difference.value == pytest.approx(2 / 3)
    assert report.primary.supported is True


def test_no_true_effect_is_reported_as_inconclusive_not_as_a_finding():
    report = analyse_study(_cohort(24, full_detect=2, ablated_detect=2))
    assert report.primary.difference.value == pytest.approx(0.0)
    assert report.primary.supported is None


def test_an_effect_in_the_wrong_direction_is_reported_honestly():
    report = analyse_study(_cohort(24, full_detect=0, ablated_detect=3))
    assert report.primary.supported is False


def test_participants_who_missed_every_probe_are_censored_not_zeroed():
    """Averaging latency over successes only would improve as detection worsens."""
    records = _cohort(24, full_detect=3, ablated_detect=0)
    report = analyse_study(records)
    latency = next(r for r in report.secondary if r.hypothesis_id == "H2")
    assert latency.n == 0, "nobody has a latency in both conditions"
    assert latency.censored == 24
    assert latency.difference is None
    # Detection itself is still measurable and still shows the effect.
    assert report.primary.difference.value == pytest.approx(1.0)


def test_the_memory_hypothesis_uses_the_memory_ablation():
    report = analyse_study(_cohort(24))
    completion = next(r for r in report.secondary if r.hypothesis_id == "H3")
    assert completion.baseline_condition == "no-memory"
    assert completion.variant_condition == "full"
    assert completion.difference.value == pytest.approx(0.5)


# --- pilot discipline --------------------------------------------------------


def test_below_the_minimum_the_report_is_a_pilot_and_claims_nothing():
    report = analyse_study(_cohort(12))
    assert report.n_analysed == 12
    assert report.is_pilot is True
    assert hypothesis_verdicts(report) == {}
    assert "Pilot" in report.headline()
    # The numbers are still computed and reported; only the verdicts vanish.
    assert report.primary.difference is not None


def test_the_minimum_is_the_pre_registered_one():
    assert analyse_study(_cohort(MINIMUM_REPORTABLE_N)).is_pilot is False
    assert analyse_study(_cohort(MINIMUM_REPORTABLE_N - 1)).is_pilot is True


# --- missingness -------------------------------------------------------------


def test_every_recruited_participant_is_accounted_for():
    records = _cohort(20)
    records.append(
        ParticipantRecord(
            "P900", williams_sequences(STUDY_CONDITIONS)[0], (), withdrew=True
        )
    )
    flow = missingness(records)
    assert flow.recruited == 21
    assert flow.withdrew == 1
    assert flow.analysed == 20
    assert "P900" in flow.excluded_ids
    assert flow.accounted_for() is True


def test_a_block_lost_to_a_technical_failure_is_reported_not_imputed():
    records = _cohort(6)
    broken = records[0]
    outcomes = list(broken.outcomes)
    outcomes[0] = _outcome(outcomes[0].condition_id, detected=0, lost=True)
    records[0] = ParticipantRecord(
        broken.participant_id, broken.sequence, tuple(outcomes)
    )
    flow = missingness(records)
    assert flow.lost_blocks == 1
    assert flow.analysed == 5
    assert broken.participant_id in flow.excluded_ids


def test_incomplete_participants_do_not_enter_the_paired_comparisons():
    records = _cohort(19)
    partial = records[0]
    records[0] = ParticipantRecord(
        partial.participant_id, partial.sequence, partial.outcomes[:1]
    )
    report = analyse_study(records)
    assert report.n_analysed == 18
    assert report.primary.n == 18


# --- guardrail and order effects --------------------------------------------


def test_the_guardrail_is_judged_on_the_interval_not_the_point_estimate():
    """A mean of 0.14 whose interval reaches 0.31 has not shown the rate is low."""
    calm = analyse_study(_cohort(24, interventions=10, dismissed=0))
    assert calm.guardrail_unwanted_rate.value == 0.0
    assert calm.guardrail_met is True

    noisy = analyse_study(_cohort(24, interventions=10, dismissed=5))
    assert noisy.guardrail_unwanted_rate.value == pytest.approx(0.5)
    assert noisy.guardrail_met is False
    assert UNWANTED_INTERVENTION_GUARDRAIL == 0.15


def test_a_pilot_reaches_no_guardrail_verdict():
    assert analyse_study(_cohort(6)).guardrail_met is None


def test_order_effects_are_reported_whether_or_not_they_are_interesting():
    report = analyse_study(_cohort(24))
    detection = next(e for e in report.order_effects if e.measure == "detection_rate")
    assert len(detection.by_position) == 3
    assert sum(count for _, _, count in detection.by_position) == 72


def test_all_six_sequences_are_used_by_a_full_cohort():
    report = analyse_study(_cohort(24))
    assert len(report.sequences_used) == 6


def test_order_effect_ignores_blocks_lost_to_failure():
    records = _cohort(6)
    first = records[0]
    outcomes = list(first.outcomes)
    outcomes[0] = _outcome(outcomes[0].condition_id, detected=3, lost=True)
    records[0] = ParticipantRecord(
        first.participant_id, first.sequence, tuple(outcomes)
    )
    effect = order_effect(records, "detection_rate")
    assert sum(count for _, _, count in effect.by_position) == 17


# --- reuse -------------------------------------------------------------------


def test_the_study_and_the_benchmark_compute_a_difference_the_same_way():
    """One implementation, so the two cannot drift apart."""
    from src.open_llm_vtuber.evaluation import scoring, study_analysis

    assert study_analysis.bootstrap_interval is scoring.bootstrap_interval


def test_a_comparison_with_no_shared_participants_yields_no_interval():
    result = paired_comparison(
        [],
        hypothesis_id="H1",
        measure="detection_rate",
        baseline="no-embodiment",
        variant="full",
    )
    assert result.n == 0
    assert result.difference is None
    assert result.supported is None
