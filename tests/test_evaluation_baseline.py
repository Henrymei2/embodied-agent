from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.open_llm_vtuber.evaluation import (
    BaselineValidationError,
    MetricMeasurement,
    build_baseline_report,
    build_run_manifest,
    canonical_digest,
    finish_run_manifest,
    load_research_contract,
    measurements_from_replay,
    run_replay,
    validate_baseline_report,
    validate_run_manifest,
)

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
_FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "research"
    / "fixtures"
    / "development"
    / "part2-smoke.json"
)
_BASELINE = (
    Path(__file__).resolve().parent.parent
    / "research"
    / "baselines"
    / "part2-development.json"
)


def manifest(*, condition: str = "full", terminal: bool = True) -> dict:
    contract = load_research_contract()
    running = build_run_manifest(
        run_id=f"baseline-{condition}",
        code_revision="a" * 40,
        worktree_dirty=True,
        contract_revision=contract["contract_revision"],
        condition_id=condition,
        scenario_set_revision="part2-smoke-v1",
        scenario_split="development",
        fixture_digest="b" * 64,
        configuration_digest="c" * 64,
        model_identifiers=["fixture-model-v1"],
        engine_identifiers=["fixture-engine-v1"],
        platform={
            "system": "Windows",
            "release": "11",
            "machine": "AMD64",
            "python": "3.11",
        },
        seed=0,
        started_at=_START,
    )
    return (
        finish_run_manifest(
            running,
            status="completed",
            finished_at=_START + timedelta(seconds=1),
        )
        if terminal
        else running
    )


def replay_measurements() -> dict[str, MetricMeasurement]:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = run_replay(fixture)
    return measurements_from_replay(
        result,
        failure_directives=3,
        duplicate_directives=1,
    )


def report(*, condition: str = "full") -> dict:
    return build_baseline_report(
        manifest(condition=condition),
        replay_measurements() if condition == "full" else {},
        report_id=f"report-{condition}",
        generated_at="2026-08-03T12:00:01+00:00",
    )


def test_baseline_lists_every_metric_and_never_turns_missing_into_zero():
    baseline = report()
    results = {item["metric_id"]: item for item in baseline["results"]}

    assert baseline["summary"] == {
        "fail": 0,
        "not_applicable": 0,
        "pass": 4,
        "unavailable": 13,
        "total": 17,
    }
    assert results["recovery_rate"]["state"] == "pass"
    assert results["trace_privacy_violation_count"]["value"] == 0
    assert results["unauthorized_side_effect_count"] == {
        "metric_id": "unauthorized_side_effect_count",
        "state": "unavailable",
        "value": None,
        "target": {"operator": "==", "threshold": 0},
        "evidence_digest": None,
        "reason_code": "not_measured",
    }


def test_ablated_condition_is_not_misreported_as_zero_or_failure():
    baseline = report(condition="no-autonomy")
    results = {item["metric_id"]: item for item in baseline["results"]}

    assert results["proactive_help_acceptance_rate"]["state"] == "not_applicable"
    assert results["unwanted_intervention_rate"]["state"] == "not_applicable"
    assert baseline["summary"]["not_applicable"] == 2


def test_a_measured_value_is_evaluated_against_the_frozen_target():
    measurement = MetricMeasurement(
        value=0.1,
        evidence_digest="d" * 64,
        reason_code="development_measurement",
    )
    baseline = build_baseline_report(
        manifest(),
        {"recovery_rate": measurement},
        report_id="failed-target-report",
        generated_at="2026-08-03T12:00:01+00:00",
    )
    recovery = next(
        item for item in baseline["results"] if item["metric_id"] == "recovery_rate"
    )

    assert recovery["state"] == "fail"


def test_baseline_rejects_unknown_metrics_and_nonterminal_runs():
    measurement = MetricMeasurement(1, "d" * 64)
    with pytest.raises(BaselineValidationError, match="unknown metrics"):
        build_baseline_report(
            manifest(),
            {"invented_metric": measurement},
            report_id="bad-report",
            generated_at="2026-08-03T12:00:01+00:00",
        )
    with pytest.raises(BaselineValidationError, match="terminal run"):
        build_baseline_report(
            manifest(terminal=False),
            {},
            report_id="early-report",
            generated_at="2026-08-03T12:00:01+00:00",
        )


def test_baseline_integrity_detects_tampering():
    baseline = report()
    baseline["summary"]["pass"] = 99

    with pytest.raises(BaselineValidationError, match="must equal"):
        validate_baseline_report(baseline)


def test_report_is_cryptographically_bound_to_its_run_manifest():
    run_manifest = manifest()
    baseline = build_baseline_report(
        run_manifest,
        {},
        report_id="bound-report",
        generated_at="2026-08-03T12:00:01+00:00",
    )
    validate_baseline_report(baseline, manifest=run_manifest)
    different_manifest = copy.deepcopy(run_manifest)
    different_manifest["seed"] = 1

    with pytest.raises(BaselineValidationError, match="bound manifest"):
        validate_baseline_report(baseline, manifest=different_manifest)


def test_report_cannot_mark_an_applicable_metric_not_applicable():
    baseline = report()
    candidate = copy.deepcopy(baseline)
    candidate["results"][0]["state"] = "not_applicable"
    candidate["results"][0]["value"] = None
    candidate["results"][0]["evidence_digest"] = None

    with pytest.raises(BaselineValidationError, match="cannot be not_applicable"):
        validate_baseline_report(candidate)


def test_committed_development_baseline_remains_bound_and_reproducible():
    artifact = json.loads(_BASELINE.read_text(encoding="utf-8"))
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    result = run_replay(fixture, run_id=artifact["manifest"]["run_id"])

    validate_run_manifest(artifact["manifest"])
    validate_baseline_report(
        artifact["report"],
        manifest=artifact["manifest"],
    )
    assert artifact["manifest"]["fixture_digest"] == canonical_digest(fixture)
    assert artifact["replay"] == {
        "runs": 3,
        "terminal_state_digest": result.terminal_state_digest,
        "action_outcomes_digest": result.action_outcomes_digest,
        "event_chain_digest": result.event_chain_digest,
    }
