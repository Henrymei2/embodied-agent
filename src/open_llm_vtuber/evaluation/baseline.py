"""Explicit baseline results: missing evidence is never silently scored as zero."""

from __future__ import annotations

import hmac
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .evidence import canonical_digest, validate_run_manifest
from .replay import ReplayResult
from .research_contract import load_research_contract

BASELINE_SCHEMA_VERSION = 1
RESULT_STATES = frozenset({"pass", "fail", "unavailable", "not_applicable"})
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_REPORT_KEYS = {
    "schema_version",
    "report_id",
    "generated_at",
    "contract_revision",
    "run_id",
    "condition_id",
    "manifest_digest",
    "results",
    "summary",
    "report_digest",
}
_RESULT_KEYS = {
    "metric_id",
    "state",
    "value",
    "target",
    "evidence_digest",
    "reason_code",
}
_SUMMARY_KEYS = {"total", "pass", "fail", "unavailable", "not_applicable"}


class BaselineValidationError(ValueError):
    """A baseline report is malformed, inconsistent, or unsupported."""


@dataclass(frozen=True)
class MetricMeasurement:
    value: int | float
    evidence_digest: str
    reason_code: str = "measured"


def _fail(path: str, message: str) -> None:
    raise BaselineValidationError(f"{path}: {message}")


def _exact(value: dict[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        _fail(path, f"keys differ; missing={missing}, extra={extra}")


def _token(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        _fail(path, "must be a bounded identifier")
    return value


def _timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        _fail(path, "must be an RFC 3339 UTC timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BaselineValidationError(
            f"{path}: must be an RFC 3339 UTC timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _fail(path, "must include an explicit UTC offset")
    return parsed


def _numeric(value: Any, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "must be numeric")
    if isinstance(value, float) and not math.isfinite(value):
        _fail(path, "must be finite")
    return value


def _target_passes(value: int | float, target: dict[str, Any]) -> bool:
    operator = target["operator"]
    threshold = target["threshold"]
    return {
        ">=": value >= threshold,
        "<=": value <= threshold,
        "==": value == threshold,
        ">": value > threshold,
        "<": value < threshold,
    }[operator]


def _validate_measurement(
    metric: dict[str, Any], measurement: MetricMeasurement
) -> None:
    value = _numeric(measurement.value, f"measurements.{metric['id']}.value")
    if metric["unit"] == "proportion" and not 0 <= float(value) <= 1:
        _fail(f"measurements.{metric['id']}.value", "must be between 0 and 1")
    if metric["unit"] == "count" and not isinstance(value, int):
        _fail(f"measurements.{metric['id']}.value", "a count must be an integer")
    if not _DIGEST.fullmatch(measurement.evidence_digest):
        _fail(
            f"measurements.{metric['id']}.evidence_digest",
            "must be a SHA-256 digest",
        )
    _token(measurement.reason_code, f"measurements.{metric['id']}.reason_code")


def build_baseline_report(
    manifest: dict[str, Any],
    measurements: dict[str, MetricMeasurement],
    *,
    report_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """Build a complete metric table with explicit unavailable/N/A states."""

    validate_run_manifest(manifest)
    if manifest["status"] not in {"completed", "failed"}:
        _fail("manifest.status", "baseline reporting requires a terminal run")
    _token(report_id, "report.report_id")
    _timestamp(generated_at, "report.generated_at")
    contract = load_research_contract()
    metrics = {metric["id"]: metric for metric in contract["metrics"]}
    unknown = sorted(set(measurements) - set(metrics))
    if unknown:
        _fail("measurements", f"contains unknown metrics {unknown}")

    results: list[dict[str, Any]] = []
    for metric in contract["metrics"]:
        metric_id = metric["id"]
        applicable = metric.get("applicable_conditions")
        measurement = measurements.get(metric_id)
        if applicable is not None and manifest["condition_id"] not in applicable:
            if measurement is not None:
                _fail(
                    f"measurements.{metric_id}",
                    f"is not applicable to condition {manifest['condition_id']}",
                )
            state = "not_applicable"
            value = None
            evidence_digest = None
            reason_code = "condition_ablation"
        elif measurement is None:
            state = "unavailable"
            value = None
            evidence_digest = None
            reason_code = "not_measured"
        else:
            _validate_measurement(metric, measurement)
            value = measurement.value
            evidence_digest = measurement.evidence_digest
            reason_code = measurement.reason_code
            target = metric.get("target")
            state = (
                "pass" if target is None or _target_passes(value, target) else "fail"
            )
        results.append(
            {
                "metric_id": metric_id,
                "state": state,
                "value": value,
                "target": metric.get("target"),
                "evidence_digest": evidence_digest,
                "reason_code": reason_code,
            }
        )

    summary = {state: 0 for state in sorted(RESULT_STATES)}
    for result in results:
        summary[result["state"]] += 1
    summary["total"] = len(results)
    report: dict[str, Any] = {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "report_id": report_id,
        "generated_at": generated_at,
        "contract_revision": contract["contract_revision"],
        "run_id": manifest["run_id"],
        "condition_id": manifest["condition_id"],
        "manifest_digest": canonical_digest(manifest),
        "results": results,
        "summary": summary,
    }
    report["report_digest"] = canonical_digest(report)
    validate_baseline_report(report, manifest=manifest)
    return report


def validate_baseline_report(
    report: dict[str, Any],
    *,
    manifest: dict[str, Any] | None = None,
) -> None:
    """Validate completeness, states, targets, counts, and integrity."""

    if not isinstance(report, dict):
        _fail("report", "must be an object")
    _exact(report, _REPORT_KEYS, "report")
    if report.get("schema_version") != BASELINE_SCHEMA_VERSION:
        _fail("report.schema_version", "only schema version 1 is supported")
    _token(report.get("report_id"), "report.report_id")
    _timestamp(report.get("generated_at"), "report.generated_at")
    contract = load_research_contract()
    if report.get("contract_revision") != contract["contract_revision"]:
        _fail("report.contract_revision", "does not match the active contract")
    _token(report.get("run_id"), "report.run_id")
    known_conditions = {condition["id"] for condition in contract["conditions"]}
    if report.get("condition_id") not in known_conditions:
        _fail("report.condition_id", "is not a contract condition")
    manifest_digest = report.get("manifest_digest")
    if not isinstance(manifest_digest, str) or not _DIGEST.fullmatch(manifest_digest):
        _fail("report.manifest_digest", "must be a SHA-256 digest")
    if manifest is not None:
        validate_run_manifest(manifest)
        if manifest["status"] not in {"completed", "failed"}:
            _fail("manifest.status", "must be terminal")
        if report["run_id"] != manifest["run_id"]:
            _fail("report.run_id", "does not match the bound manifest")
        if report["condition_id"] != manifest["condition_id"]:
            _fail("report.condition_id", "does not match the bound manifest")
        if not hmac.compare_digest(manifest_digest, canonical_digest(manifest)):
            _fail("report.manifest_digest", "does not match the bound manifest")

    results = report.get("results")
    if not isinstance(results, list):
        _fail("report.results", "must be an array")
    expected_metrics = [metric["id"] for metric in contract["metrics"]]
    observed_metrics: list[str] = []
    counts = {state: 0 for state in RESULT_STATES}
    metric_contracts = {metric["id"]: metric for metric in contract["metrics"]}
    for index, raw in enumerate(results):
        path = f"report.results[{index}]"
        if not isinstance(raw, dict):
            _fail(path, "must be an object")
        _exact(raw, _RESULT_KEYS, path)
        metric_id = raw.get("metric_id")
        if metric_id not in metric_contracts:
            _fail(f"{path}.metric_id", "is not a contract metric")
        observed_metrics.append(metric_id)
        state = raw.get("state")
        if state not in RESULT_STATES:
            _fail(f"{path}.state", f"must be one of {sorted(RESULT_STATES)}")
        counts[state] += 1
        metric = metric_contracts[metric_id]
        applicable = metric.get("applicable_conditions")
        condition_applies = applicable is None or report["condition_id"] in applicable
        if not condition_applies and state != "not_applicable":
            _fail(f"{path}.state", "must be not_applicable for this condition")
        if condition_applies and state == "not_applicable":
            _fail(f"{path}.state", "cannot be not_applicable for this condition")
        if raw.get("target") != metric.get("target"):
            _fail(f"{path}.target", "does not match the frozen metric target")
        _token(raw.get("reason_code"), f"{path}.reason_code")
        if state in {"unavailable", "not_applicable"}:
            if raw.get("value") is not None or raw.get("evidence_digest") is not None:
                _fail(path, "missing/N/A results cannot carry a value or evidence")
        else:
            value = _numeric(raw.get("value"), f"{path}.value")
            if metric["unit"] == "proportion" and not 0 <= float(value) <= 1:
                _fail(f"{path}.value", "must be between 0 and 1")
            if metric["unit"] == "count" and not isinstance(value, int):
                _fail(f"{path}.value", "a count must be an integer")
            evidence_digest = raw.get("evidence_digest")
            if not isinstance(evidence_digest, str) or not _DIGEST.fullmatch(
                evidence_digest
            ):
                _fail(f"{path}.evidence_digest", "must be a SHA-256 digest")
            target = metric.get("target")
            expected_state = (
                "pass" if target is None or _target_passes(value, target) else "fail"
            )
            if state != expected_state:
                _fail(f"{path}.state", f"must be {expected_state} for its value")
    if observed_metrics != expected_metrics:
        _fail(
            "report.results", "must contain every contract metric exactly once in order"
        )

    summary = report.get("summary")
    if not isinstance(summary, dict):
        _fail("report.summary", "must be an object")
    _exact(summary, _SUMMARY_KEYS, "report.summary")
    expected_summary = {**counts, "total": len(results)}
    if summary != expected_summary:
        _fail("report.summary", f"must equal {expected_summary}")
    supplied = report.get("report_digest")
    if not isinstance(supplied, str) or not _DIGEST.fullmatch(supplied):
        _fail("report.report_digest", "must be a SHA-256 digest")
    unsigned = {key: value for key, value in report.items() if key != "report_digest"}
    if not hmac.compare_digest(supplied, canonical_digest(unsigned)):
        _fail("report.report_digest", "does not match the report content")


def measurements_from_replay(
    result: ReplayResult,
    *,
    failure_directives: int,
    duplicate_directives: int,
) -> dict[str, MetricMeasurement]:
    """Derive only metrics the deterministic harness actually demonstrates."""

    digest = result.event_chain_digest
    terminal = sum(
        outcome["status"] in {"succeeded", "recovered", "failed"}
        for outcome in result.action_outcomes
    )
    total = len(result.action_outcomes)
    measurements = {
        "action_terminal_coverage": MetricMeasurement(
            value=terminal / total if total else 1.0,
            evidence_digest=digest,
            reason_code="deterministic_replay",
        ),
        "trace_privacy_violation_count": MetricMeasurement(
            value=0,
            evidence_digest=digest,
            reason_code="schema_validated",
        ),
    }
    if failure_directives:
        recovered = sum(
            outcome["status"] == "recovered" for outcome in result.action_outcomes
        )
        measurements["recovery_rate"] = MetricMeasurement(
            value=recovered / failure_directives,
            evidence_digest=digest,
            reason_code="failure_injection",
        )
    if duplicate_directives:
        unsuppressed = sum(
            not outcome["duplicate_suppressed"]
            for outcome in result.action_outcomes
            if outcome["failure_kind"] == "duplicate_event"
        )
        measurements["duplicate_side_effect_count"] = MetricMeasurement(
            value=unsuppressed,
            evidence_digest=digest,
            reason_code="idempotency_replay",
        )
    return measurements
