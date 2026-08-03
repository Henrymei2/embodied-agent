"""Part 8 slice 8.1: the EmbodiedAgentBench scenario contract."""

from __future__ import annotations

import json

import pytest

from src.open_llm_vtuber.evaluation.scenarios import (
    CAPABILITY_AREAS,
    SCENARIO_SCHEMA_VERSION,
    Scenario,
    ScenarioError,
    ScenarioSuite,
    load_suite,
    scenario_from_record,
    suite_from_records,
)


def _record(scenario_id="walk-to-computer", **overrides):
    record = {
        "scenario_id": scenario_id,
        "title": "Walk to the computer before using its tools",
        "capability_area": "spatial_grounding",
        "split": "development",
        "prompt": "Please check my files.",
        "initial_state_id": "default-room",
        "terminal_predicates": ["object_reached", "authorization_granted"],
        "allowed_effects": ["file.read"],
        "forbidden_effects": ["mail.send"],
    }
    record.update(overrides)
    return record


def _scenario(scenario_id="walk-to-computer", **overrides) -> Scenario:
    return scenario_from_record(_record(scenario_id, **overrides))


# --- scenario contract -------------------------------------------------------


def test_a_valid_scenario_loads_and_digests_stably():
    first = _scenario()
    second = _scenario()
    assert first == second
    assert first.digest() == second.digest()
    assert len(first.digest()) == 64


def test_a_scenario_with_no_terminal_predicate_is_refused():
    """It could be neither passed nor failed, so it is not a scenario."""
    with pytest.raises(ScenarioError, match="neither passed nor failed"):
        _scenario(terminal_predicates=[])


def test_an_effect_cannot_be_both_allowed_and_forbidden():
    """Otherwise whichever check runs first decides the result."""
    with pytest.raises(ScenarioError, match="both allowed and forbidden"):
        _scenario(allowed_effects=["mail.send"], forbidden_effects=["mail.send"])


def test_undeclared_predicates_areas_splits_and_failures_fail_closed():
    with pytest.raises(ScenarioError, match="undeclared terminal predicates"):
        _scenario(terminal_predicates=["vibes_were_good"])
    with pytest.raises(ScenarioError, match="capability_area"):
        _scenario(capability_area="general_intelligence")
    with pytest.raises(ScenarioError, match="split"):
        _scenario(split="test")
    with pytest.raises(ScenarioError, match="injectable"):
        _scenario(injected_failure="cosmic_ray")


def test_unknown_and_missing_record_fields_are_refused():
    """A misspelled field must not silently become a default."""
    misspelled = _record()
    misspelled["forbiden_effects"] = ["mail.send"]
    with pytest.raises(ScenarioError, match="unknown fields"):
        scenario_from_record(misspelled)

    incomplete = _record()
    del incomplete["prompt"]
    with pytest.raises(ScenarioError, match="missing fields"):
        scenario_from_record(incomplete)


def test_budgets_and_weights_are_bounded():
    with pytest.raises(ScenarioError, match="time_budget_s"):
        _scenario(time_budget_s=0)
    with pytest.raises(ScenarioError, match="time_budget_s"):
        _scenario(time_budget_s=999999)
    with pytest.raises(ScenarioError, match="score_weight"):
        _scenario(score_weight=0)
    with pytest.raises(ScenarioError, match="seed"):
        _scenario(seed=-1)


def test_booleans_are_not_accepted_as_numbers():
    """True is an int in Python and would pass a naive range check."""
    with pytest.raises(ScenarioError, match="time_budget_s"):
        _scenario(time_budget_s=True)


# --- suite -------------------------------------------------------------------


def _suite(*scenarios) -> ScenarioSuite:
    return ScenarioSuite(revision="2026-08-03.1", scenarios=tuple(scenarios))


def test_duplicate_scenario_ids_are_refused():
    with pytest.raises(ScenarioError, match="duplicate scenario id"):
        _suite(_scenario(), _scenario()).validate()


def test_coverage_names_the_areas_that_are_missing():
    """A hundred conversation scenarios would satisfy a count and nothing else."""
    coverage = _suite(_scenario()).coverage()
    assert coverage.total == 1
    assert coverage.area_counts() == {"spatial_grounding": 1}
    assert "memory" in coverage.missing_areas
    assert len(coverage.missing_areas) == len(CAPABILITY_AREAS) - 1


def test_the_held_out_split_cannot_be_taken_by_accident():
    """Risk row 2 is evaluation overfitting; the mitigation must be mechanical."""
    suite = _suite(
        _scenario("dev-one"),
        _scenario("eval-one", split="held_out"),
    )
    assert len(suite.select(split="development")) == 1
    with pytest.raises(ScenarioError, match="frozen"):
        suite.select(split="held_out")
    assert len(suite.select(split="held_out", allow_held_out=True)) == 1


def test_split_digests_change_when_a_scenario_changes():
    original = _suite(_scenario("eval-one", split="held_out"))
    edited = _suite(
        _scenario("eval-one", split="held_out", prompt="Please check my email.")
    )
    assert original.digest(split="held_out") != edited.digest(split="held_out")


def test_a_digest_of_an_empty_split_is_an_error_not_a_hash_of_nothing():
    with pytest.raises(ScenarioError, match="no scenarios in split"):
        _suite(_scenario()).digest(split="held_out")


def test_a_suite_index_cannot_be_supplied_to_skip_validation():
    with pytest.raises(TypeError):
        ScenarioSuite(revision="r", scenarios=(), _index={})  # type: ignore[call-arg]


def test_an_empty_suite_is_refused():
    with pytest.raises(ScenarioError, match="at least one scenario"):
        _suite().validate()


# --- file loading ------------------------------------------------------------


def _write(tmp_path, payload):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_suite_file_round_trips(tmp_path):
    payload = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "revision": "2026-08-03.1",
        "scenarios": [_record(), _record("eval-one", split="held_out")],
    }
    suite = load_suite(_write(tmp_path, payload))
    assert suite.coverage().total == 2
    assert suite.revision == "2026-08-03.1"


def test_a_modified_held_out_split_is_caught_at_load(tmp_path):
    """The frozen split must prove it is still frozen, not be trusted to be."""
    frozen = suite_from_records("2026-08-03.1", [_record("eval-one", split="held_out")])
    digest = frozen.digest(split="held_out")

    tampered = _record("eval-one", split="held_out", prompt="An easier task.")
    payload = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "revision": "2026-08-03.1",
        "scenarios": [tampered],
        "held_out_digest": digest,
    }
    with pytest.raises(ScenarioError, match="does not match its frozen digest"):
        load_suite(_write(tmp_path, payload))


def test_an_unmodified_held_out_split_loads(tmp_path):
    frozen = suite_from_records("2026-08-03.1", [_record("eval-one", split="held_out")])
    payload = {
        "schema_version": SCENARIO_SCHEMA_VERSION,
        "revision": "2026-08-03.1",
        "scenarios": [_record("eval-one", split="held_out")],
        "held_out_digest": frozen.digest(split="held_out"),
    }
    assert load_suite(_write(tmp_path, payload)).coverage().total == 1


def test_a_wrong_schema_version_fails_closed(tmp_path):
    payload = {
        "schema_version": 99,
        "revision": "r",
        "scenarios": [_record()],
    }
    with pytest.raises(ScenarioError, match="unsupported schema version"):
        load_suite(_write(tmp_path, payload))


def test_a_missing_file_is_an_error_not_an_empty_suite(tmp_path):
    with pytest.raises(ScenarioError, match="could not be read"):
        load_suite(tmp_path / "absent.json")
