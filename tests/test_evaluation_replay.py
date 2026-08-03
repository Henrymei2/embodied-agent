from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from src.open_llm_vtuber.evaluation import (
    ReplayError,
    ReplayMismatchError,
    freeze_replay_fixture,
    run_replay,
    validate_event_chain,
    validate_replay_fixture,
    verify_replay_determinism,
)

_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "research"
    / "fixtures"
    / "development"
    / "part2-smoke.json"
)


def fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_frozen_fixture_is_valid_and_repeats_identically_three_times():
    candidate = fixture()
    validate_replay_fixture(candidate)

    result = verify_replay_determinism(candidate, runs=3)

    assert result.status == "completed"
    assert result.terminal_state == {
        "revision": 5,
        "agent_location": "bookshelf",
        "plan_status": "ready",
        "network_status": "available",
        "audio_state": "ready",
        "object_state": "open",
    }
    assert (
        result.terminal_state_digest == candidate["expected"]["terminal_state_digest"]
    )
    assert (
        result.action_outcomes_digest == candidate["expected"]["action_outcomes_digest"]
    )
    validate_event_chain(result.events)


def test_timeout_malformed_response_and_duplicate_event_recover_deterministically():
    result = run_replay(fixture())
    outcomes = {item["adapter"]: item for item in result.action_outcomes}

    assert outcomes["network"]["status"] == "recovered"
    assert outcomes["network"]["attempts"] == 2
    assert outcomes["network"]["failure_kind"] == "timeout"
    assert outcomes["audio"]["failure_kind"] == "malformed_response"
    assert outcomes["frontend"]["duplicate_suppressed"] is True
    assert result.terminal_state["revision"] == 5


def test_replay_has_no_live_fallback_when_a_response_is_missing():
    candidate = fixture()
    del candidate["adapters"]["network"]["network-request"]

    with pytest.raises(ReplayError, match="missing frozen response"):
        validate_replay_fixture(candidate)


def test_fixture_cannot_overwrite_the_engine_revision_counter():
    candidate = fixture()
    candidate["adapters"]["llm"]["plan-request"]["key"] = "revision"

    with pytest.raises(ReplayError, match="maintained by the replay engine"):
        validate_replay_fixture(candidate)


def test_retry_budget_produces_an_explicit_terminal_failure():
    candidate = fixture()
    candidate["steps"][0]["max_attempts"] = 1
    candidate["failures"] = [
        {
            "adapter": "llm",
            "request_id": "plan-request",
            "attempt": 1,
            "kind": "timeout",
        }
    ]
    frozen = freeze_replay_fixture(
        {key: value for key, value in candidate.items() if key != "expected"}
    )

    result = run_replay(frozen)

    assert result.status == "failed"
    assert result.action_outcomes == [
        {
            "step_id": "step-plan",
            "adapter": "llm",
            "request_id": "plan-request",
            "status": "failed",
            "attempts": 1,
            "outcome": "failed",
            "failure_kind": "timeout",
            "effect_committed": False,
            "duplicate_suppressed": False,
        }
    ]


def test_expected_digest_drift_is_not_silently_refrozen():
    candidate = fixture()
    candidate["expected"]["terminal_state_digest"] = "0" * 64

    with pytest.raises(ReplayMismatchError, match="outcome drifted"):
        run_replay(candidate)


def test_freezing_is_explicit_and_does_not_mutate_the_source():
    candidate = fixture()
    candidate.pop("expected")
    original = copy.deepcopy(candidate)

    frozen = freeze_replay_fixture(candidate)

    assert candidate == original
    assert frozen["expected"]["status"] == "completed"


def test_failure_directive_cannot_escape_the_declared_retry_budget():
    candidate = fixture()
    candidate["failures"][0]["attempt"] = 3

    with pytest.raises(ReplayError, match="beyond the step retry budget"):
        validate_replay_fixture(candidate)


def test_determinism_verification_cannot_be_weakened_below_three_runs():
    with pytest.raises(ReplayError, match="at least three"):
        verify_replay_determinism(fixture(), runs=2)
