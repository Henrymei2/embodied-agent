"""Part 8 slices 8.2-8.4: the authored suite, the scorer, and the statistics."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.open_llm_vtuber.evaluation.scenarios import (
    CAPABILITY_AREAS,
    ScenarioError,
    load_suite,
    scenario_from_record,
)
from src.open_llm_vtuber.evaluation.scoring import (
    FAILURE_STAGES,
    RunResult,
    ScoringError,
    bootstrap_interval,
    compare_conditions,
    require_release_gate,
    score_run,
    score_suite,
)

SUITE_PATH = (
    Path(__file__).resolve().parents[1]
    / "research"
    / "scenarios"
    / ("embodied-agent-bench.json")
)


@pytest.fixture(scope="module")
def suite():
    return load_suite(SUITE_PATH)


def _scenario(**overrides):
    record = {
        "scenario_id": "probe-one",
        "title": "Probe scenario",
        "capability_area": "safety",
        "split": "development",
        "prompt": "do the thing",
        "trigger": "user_utterance",
        "initial_state_id": "default-room",
        "terminal_predicates": ["task_completed", "outcome_verified"],
        "allowed_effects": ["file.read"],
        "forbidden_effects": ["mail.send"],
    }
    record.update(overrides)
    return scenario_from_record(record)


# --- the authored suite ------------------------------------------------------


def test_the_suite_meets_the_part_eight_size_and_coverage_gate(suite):
    coverage = suite.coverage()
    assert coverage.total >= 100
    assert coverage.missing_areas == (), "every capability area must be exercised"
    assert set(coverage.area_counts()) == CAPABILITY_AREAS
    assert coverage.adversarial >= 10


def test_every_split_is_populated_and_held_out_is_substantial(suite):
    splits = suite.coverage().split_counts()
    assert splits["development"] > 0
    assert splits["validation"] > 0
    assert splits["held_out"] >= 25


def test_the_held_out_split_is_digest_verified_on_load(suite):
    """The file carries its own frozen digest and load checks it."""
    assert suite.digest(split="held_out")
    with pytest.raises(ScenarioError, match="frozen"):
        suite.select(split="held_out")


def test_a_development_view_does_not_contain_the_evaluation_set(suite):
    """The real mitigation: tuning code holds a suite with nothing to peek at."""
    view = suite.development_view()
    assert all(s.split != "held_out" for s in view.scenarios)
    assert view.coverage().total < suite.coverage().total


def test_scenarios_without_an_utterance_declare_a_different_trigger(suite):
    """Autonomy and VR scenarios are started by a clock or a session, not speech."""
    for scenario in suite.scenarios:
        if scenario.trigger == "user_utterance":
            assert scenario.prompt.strip()
        else:
            assert not scenario.prompt.strip()
    triggers = {s.trigger for s in suite.scenarios}
    assert "schedule" in triggers


def test_adversarial_scenarios_all_forbid_something(suite):
    """An attack scenario that forbids nothing cannot detect a successful attack."""
    for scenario in suite.scenarios:
        if scenario.adversarial:
            assert scenario.forbidden_effects, scenario.scenario_id


# --- scoring -----------------------------------------------------------------


def test_a_clean_run_succeeds():
    scenario = _scenario()
    score = score_run(
        scenario,
        RunResult(
            "probe-one", ["task_completed", "outcome_verified"], ["file.read"], 5.0
        ),
    )
    assert score.success is True
    assert score.partial_credit == 1.0
    assert score.failure_stage is None


def test_a_forbidden_effect_voids_the_run_and_its_partial_credit():
    """Completing the task and leaking is not partial success."""
    scenario = _scenario()
    score = score_run(
        scenario,
        RunResult(
            "probe-one",
            ["task_completed", "outcome_verified"],
            ["mail.send"],
            5.0,
        ),
    )
    assert score.success is False
    assert score.partial_credit == 0.0
    assert score.forbidden_effects == ("mail.send",)
    assert score.failure_stage == "forbidden_effect"


def test_a_missing_predicate_fails_but_keeps_partial_credit():
    scenario = _scenario()
    score = score_run(scenario, RunResult("probe-one", ["task_completed"], [], 5.0))
    assert score.success is False
    assert score.partial_credit == 0.5
    assert score.missing_predicates == ("outcome_verified",)
    assert score.failure_stage == "verification"


def test_exceeding_the_time_budget_fails_the_run():
    scenario = _scenario(time_budget_s=10.0)
    score = score_run(
        scenario,
        RunResult("probe-one", ["task_completed", "outcome_verified"], [], 11.0),
    )
    assert score.success is False
    assert score.over_budget is True
    assert score.failure_stage == "timeout"


def test_a_harness_error_is_not_scored_as_a_system_failure():
    """Otherwise a broken measurement makes the system look worse than it is."""
    scenario = _scenario()
    score = score_run(scenario, RunResult("probe-one", [], [], 1.0, harness_error=True))
    assert score.success is False
    assert score.failure_stage == "harness"


def test_a_run_cannot_be_scored_against_the_wrong_scenario():
    with pytest.raises(ScoringError, match="does not belong"):
        score_run(_scenario(), RunResult("some-other-scenario"))


def test_undeclared_predicates_and_stages_are_refused():
    with pytest.raises(ScoringError, match="undeclared satisfied predicates"):
        RunResult("probe-one", ["it_felt_right"])
    with pytest.raises(ScoringError, match="undeclared failure stage"):
        RunResult("probe-one", failure_stage="gremlins")


def test_failure_attribution_names_the_earliest_broken_link():
    scenario = _scenario(
        terminal_predicates=["belief_recorded", "task_completed", "outcome_verified"]
    )
    score = score_run(scenario, RunResult("probe-one", [], [], 1.0))
    assert score.failure_stage == "belief"
    assert FAILURE_STAGES.index("belief") < FAILURE_STAGES.index("action")


# --- suite scoring -----------------------------------------------------------


def _pair(scenario_id, ok):
    scenario = _scenario(scenario_id=scenario_id)
    predicates = ["task_completed", "outcome_verified"] if ok else ["task_completed"]
    return scenario, RunResult(scenario_id, predicates, [], 1.0)


def test_a_missing_run_is_an_error_not_a_silent_zero():
    """A half-crashed evaluation must not report the half that finished."""
    scenario, _ = _pair("probe-one", True)
    with pytest.raises(ScoringError, match="no run recorded"):
        score_suite([scenario], [])


def test_runs_for_unknown_scenarios_are_refused():
    scenario, run = _pair("probe-one", True)
    with pytest.raises(ScoringError, match="unknown scenarios"):
        score_suite([scenario], [run, RunResult("ghost-scenario")])


def test_suite_scoring_reports_intervals_and_a_taxonomy():
    pairs = [_pair(f"probe-{i:03d}", i % 2 == 0) for i in range(20)]
    score = score_suite(
        (s for s, _ in pairs), (r for _, r in pairs), condition_id="full"
    )
    assert score.scored == 20
    assert score.full_task_success.value == pytest.approx(0.5)
    assert score.full_task_success.low < 0.5 < score.full_task_success.high
    assert score.full_task_success.n == 20
    assert dict(score.failure_taxonomy)["verification"] == 10
    assert score.zero_forbidden_effects()


def test_the_taxonomy_reads_in_pipeline_order():
    scenarios, runs = [], []
    for index, predicates in enumerate(
        (["belief_recorded"], ["task_completed"], ["outcome_verified"])
    ):
        scenario = _scenario(
            scenario_id=f"probe-{index:03d}",
            terminal_predicates=[
                "belief_recorded",
                "task_completed",
                "outcome_verified",
            ],
        )
        scenarios.append(scenario)
        runs.append(RunResult(scenario.scenario_id, predicates, [], 1.0))
    stages = [stage for stage, _ in score_suite(scenarios, runs).failure_taxonomy]
    assert stages == sorted(stages, key=FAILURE_STAGES.index)


# --- statistics --------------------------------------------------------------


def test_a_bootstrap_interval_brackets_the_point_estimate():
    interval = bootstrap_interval([1.0] * 7 + [0.0] * 3, seed=0)
    assert interval.value == pytest.approx(0.7)
    assert interval.low <= 0.7 <= interval.high
    assert interval.n == 10


def test_an_empty_sample_has_no_interval_rather_than_a_zero_one():
    """[0, 0] over no data reads as a measured zero."""
    assert bootstrap_interval([]) is None


def test_a_single_observation_reports_no_spread_rather_than_certainty():
    interval = bootstrap_interval([1.0])
    assert interval.n == 1
    assert interval.low == interval.high == 1.0


def test_intervals_are_reproducible_for_a_given_seed():
    """A CI that moves between runs of the same data is not reproducible."""
    values = [float(index) for index in range(40)]
    assert bootstrap_interval(values, seed=7) == bootstrap_interval(values, seed=7)
    # A varied sample, because with only two distinct values the percentile
    # endpoints land on the same numbers whatever the seed, and the test would
    # pass while proving nothing about the seed being used at all.
    assert bootstrap_interval(values, seed=7) != bootstrap_interval(values, seed=8)


def test_conditions_are_compared_pairwise_on_shared_scenarios():
    baseline_pairs = [_pair(f"probe-{i:03d}", i < 2) for i in range(10)]
    variant_pairs = [_pair(f"probe-{i:03d}", i < 7) for i in range(10)]
    baseline = score_suite(
        [s for s, _ in baseline_pairs],
        [r for _, r in baseline_pairs],
        condition_id="no-memory",
    )
    variant = score_suite(
        [s for s, _ in variant_pairs],
        [r for _, r in variant_pairs],
        condition_id="full",
    )
    comparison = compare_conditions(baseline, variant)
    assert comparison.n == 10
    assert comparison.baseline_rate == pytest.approx(0.2)
    assert comparison.variant_rate == pytest.approx(0.7)
    assert comparison.difference.value == pytest.approx(0.5)
    assert len(comparison.improved) == 5
    assert comparison.regressed == ()


def test_conditions_with_no_shared_scenarios_cannot_be_paired():
    left_pairs = [_pair("probe-aaa", True)]
    right_pairs = [_pair("probe-bbb", True)]
    left = score_suite([left_pairs[0][0]], [left_pairs[0][1]], condition_id="a")
    right = score_suite([right_pairs[0][0]], [right_pairs[0][1]], condition_id="b")
    with pytest.raises(ScoringError, match="share no scenarios"):
        compare_conditions(left, right)


# --- release gate ------------------------------------------------------------


def test_the_release_gate_names_every_reason_it_failed():
    pairs = [_pair(f"probe-{i:03d}", i < 5) for i in range(10)]
    scenarios = [s for s, _ in pairs]
    runs = [r for _, r in pairs]
    runs[0] = RunResult("probe-000", ["task_completed"], ["mail.send"], 1.0)
    score = score_suite(scenarios, runs)
    passed, reasons = require_release_gate(score)
    assert passed is False
    assert any("below 0.85" in reason for reason in reasons)
    assert any("forbidden effect" in reason for reason in reasons)


def test_the_release_gate_passes_a_clean_high_scoring_suite():
    pairs = [_pair(f"probe-{i:03d}", True) for i in range(20)]
    score = score_suite([s for s, _ in pairs], [r for _, r in pairs])
    passed, reasons = require_release_gate(score)
    assert passed is True
    assert reasons == ()


def test_a_harness_error_invalidates_the_gate_even_at_a_high_score():
    pairs = [_pair(f"probe-{i:03d}", True) for i in range(20)]
    scenarios = [s for s, _ in pairs]
    runs = [r for _, r in pairs]
    runs[0] = RunResult("probe-000", [], [], 1.0, harness_error=True)
    passed, reasons = require_release_gate(score_suite(scenarios, runs))
    assert passed is False
    assert any("measurement is not valid" in reason for reason in reasons)
