"""Offline deterministic replay with bounded, scripted failure injection."""

from __future__ import annotations

import math
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .evidence import (
    build_event_envelope,
    canonical_digest,
    canonical_json,
    validate_event_chain,
)

REPLAY_SCHEMA_VERSION = 1
ADAPTER_KINDS = frozenset({"llm", "tool", "network", "audio", "frontend"})
FAILURE_KINDS = frozenset(
    {
        "timeout",
        "disconnect",
        "cancellation",
        "stale_writer",
        "duplicate_event",
        "malformed_response",
    }
)
_OPERATIONS = frozenset({"set", "increment", "append_unique", "noop"})
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FIXTURE_KEYS = {
    "schema_version",
    "scenario_id",
    "revision",
    "started_at",
    "initial_state",
    "adapters",
    "steps",
    "failures",
    "expected",
}
_STEP_KEYS = {
    "id",
    "adapter",
    "request_id",
    "max_attempts",
    "advance_ms",
}
_FAILURE_KEYS = {"adapter", "request_id", "attempt", "kind"}
_EXPECTED_KEYS = {"status", "terminal_state_digest", "action_outcomes_digest"}
_MAX_FIXTURE_CHARS = 250_000


class ReplayError(ValueError):
    """A replay fixture or deterministic execution is invalid."""


class ReplayMismatchError(ReplayError):
    """Replay output differs from the fixture's frozen expected outcome."""


class InjectedReplayFailure(ReplayError):
    def __init__(self, kind: str) -> None:
        super().__init__(f"injected replay failure: {kind}")
        self.kind = kind


def _fail(path: str, message: str) -> None:
    raise ReplayError(f"{path}: {message}")


def _exact(value: dict[str, Any], expected: set[str], path: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing or extra:
        _fail(path, f"keys differ; missing={missing}, extra={extra}")


def _mapping(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    return value


def _token(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        _fail(path, "must be a bounded content-free identifier")
    return value


def _positive_int(value: Any, path: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(path, f"must be an integer >= {minimum}")
    return value


def _validate_semantic_value(value: Any, path: str, *, depth: int = 0) -> None:
    if depth > 5:
        _fail(path, "exceeds the maximum semantic nesting depth")
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            _fail(path, "must be a signed 64-bit integer")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail(path, "must be finite")
        return
    if isinstance(value, str):
        _token(value, path)
        return
    if isinstance(value, list):
        if len(value) > 256:
            _fail(path, "contains too many items")
        for index, item in enumerate(value):
            _validate_semantic_value(item, f"{path}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 256:
            _fail(path, "contains too many fields")
        for key, item in value.items():
            if not isinstance(key, str) or not _KEY.fullmatch(key):
                _fail(path, f"unsupported semantic key {key!r}")
            _validate_semantic_value(item, f"{path}.{key}", depth=depth + 1)
        return
    _fail(path, "must be canonical semantic JSON")


def _validate_response(response: Any, path: str) -> dict[str, Any]:
    response = _mapping(response, path)
    operation = response.get("operation")
    if operation not in _OPERATIONS:
        _fail(f"{path}.operation", f"must be one of {sorted(_OPERATIONS)}")
    if operation == "noop":
        _exact(response, {"operation", "outcome"}, path)
        _token(response.get("outcome"), f"{path}.outcome")
        return response
    _exact(
        response,
        {"operation", "key", "value", "idempotency_key", "outcome"},
        path,
    )
    key = response.get("key")
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        _fail(f"{path}.key", "must be a semantic state key")
    if key == "revision":
        _fail(f"{path}.key", "revision is maintained by the replay engine")
    _token(response.get("idempotency_key"), f"{path}.idempotency_key")
    _token(response.get("outcome"), f"{path}.outcome")
    _validate_semantic_value(response.get("value"), f"{path}.value")
    if operation == "increment" and (
        isinstance(response.get("value"), bool)
        or not isinstance(response.get("value"), (int, float))
    ):
        _fail(f"{path}.value", "increment requires a numeric value")
    return response


def validate_replay_fixture(
    fixture: dict[str, Any],
    *,
    require_expected: bool = True,
) -> None:
    """Validate a bounded fixture without executing it."""

    fixture = _mapping(fixture, "fixture")
    expected_keys = _FIXTURE_KEYS if require_expected else _FIXTURE_KEYS - {"expected"}
    _exact(fixture, expected_keys, "fixture")
    try:
        fixture_chars = len(canonical_json(fixture))
    except ValueError as exc:
        raise ReplayError(f"fixture: is not canonical JSON: {exc}") from exc
    if fixture_chars > _MAX_FIXTURE_CHARS:
        _fail("fixture", "is too large")
    if fixture.get("schema_version") != REPLAY_SCHEMA_VERSION:
        _fail("fixture.schema_version", "only schema version 1 is supported")
    _token(fixture.get("scenario_id"), "fixture.scenario_id")
    _token(fixture.get("revision"), "fixture.revision")
    _parse_utc(fixture.get("started_at"), "fixture.started_at")
    state = _mapping(fixture.get("initial_state"), "fixture.initial_state")
    _validate_semantic_value(state, "fixture.initial_state")
    revision = state.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        _fail("fixture.initial_state.revision", "must be a non-negative integer")

    adapters = _mapping(fixture.get("adapters"), "fixture.adapters")
    _exact(adapters, set(ADAPTER_KINDS), "fixture.adapters")
    for adapter_kind in sorted(ADAPTER_KINDS):
        responses = _mapping(adapters[adapter_kind], f"fixture.adapters.{adapter_kind}")
        for request_id, response in responses.items():
            _token(request_id, f"fixture.adapters.{adapter_kind}.request_id")
            _validate_response(
                response, f"fixture.adapters.{adapter_kind}.{request_id}"
            )

    steps = fixture.get("steps")
    if not isinstance(steps, list) or not steps:
        _fail("fixture.steps", "must be a non-empty array")
    seen_steps: set[str] = set()
    referenced_requests: set[tuple[str, str]] = set()
    for index, raw in enumerate(steps):
        path = f"fixture.steps[{index}]"
        step = _mapping(raw, path)
        _exact(step, _STEP_KEYS, path)
        step_id = _token(step.get("id"), f"{path}.id")
        if step_id in seen_steps:
            _fail("fixture.steps", f"duplicate step id {step_id!r}")
        seen_steps.add(step_id)
        adapter = step.get("adapter")
        if adapter not in ADAPTER_KINDS:
            _fail(f"{path}.adapter", f"must be one of {sorted(ADAPTER_KINDS)}")
        request_id = _token(step.get("request_id"), f"{path}.request_id")
        if request_id not in adapters[adapter]:
            _fail(path, f"missing frozen response for {adapter}/{request_id}")
        request_identity = (adapter, request_id)
        if request_identity in referenced_requests:
            _fail("fixture.steps", f"duplicate adapter request {request_identity}")
        referenced_requests.add(request_identity)
        attempts = _positive_int(step.get("max_attempts"), f"{path}.max_attempts")
        if attempts > 20:
            _fail(f"{path}.max_attempts", "cannot exceed 20")
        advance = _positive_int(
            step.get("advance_ms"), f"{path}.advance_ms", allow_zero=True
        )
        if advance > 3_600_000:
            _fail(f"{path}.advance_ms", "cannot exceed one hour")

    failures = fixture.get("failures")
    if not isinstance(failures, list):
        _fail("fixture.failures", "must be an array")
    seen_failures: set[tuple[str, str, int]] = set()
    for index, raw in enumerate(failures):
        path = f"fixture.failures[{index}]"
        failure = _mapping(raw, path)
        _exact(failure, _FAILURE_KEYS, path)
        adapter = failure.get("adapter")
        if adapter not in ADAPTER_KINDS:
            _fail(f"{path}.adapter", f"must be one of {sorted(ADAPTER_KINDS)}")
        request_id = _token(failure.get("request_id"), f"{path}.request_id")
        attempt = _positive_int(failure.get("attempt"), f"{path}.attempt")
        if (adapter, request_id) not in referenced_requests:
            _fail(path, "must target a declared replay step")
        if failure.get("kind") not in FAILURE_KINDS:
            _fail(f"{path}.kind", f"must be one of {sorted(FAILURE_KINDS)}")
        identity = (adapter, request_id, attempt)
        if identity in seen_failures:
            _fail("fixture.failures", f"duplicate directive for {identity}")
        seen_failures.add(identity)
        matching_attempts = [
            step["max_attempts"]
            for step in steps
            if step["adapter"] == adapter and step["request_id"] == request_id
        ]
        if attempt > max(matching_attempts):
            _fail(path, "targets an attempt beyond the step retry budget")

    if require_expected:
        expected = _mapping(fixture.get("expected"), "fixture.expected")
        _exact(expected, _EXPECTED_KEYS, "fixture.expected")
        if expected.get("status") not in {"completed", "failed"}:
            _fail("fixture.expected.status", "must be completed or failed")
        for key in ("terminal_state_digest", "action_outcomes_digest"):
            value = expected.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                _fail(f"fixture.expected.{key}", "must be a SHA-256 digest")


def _parse_utc(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        _fail(path, "must be an RFC 3339 UTC timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReplayError(f"{path}: must be an RFC 3339 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _fail(path, "must include an explicit UTC offset")
    return parsed.astimezone(timezone.utc)


class DeterministicClock:
    """A replay-only clock that never reads wall time or sleeps."""

    def __init__(self, started_at: str) -> None:
        self._current = _parse_utc(started_at, "clock.started_at")

    def now(self) -> datetime:
        return self._current

    def advance(self, milliseconds: int) -> datetime:
        self._current += timedelta(milliseconds=milliseconds)
        return self._current


class FailurePlan:
    def __init__(self, directives: list[dict[str, Any]]) -> None:
        self._directives = {
            (item["adapter"], item["request_id"], item["attempt"]): item["kind"]
            for item in directives
        }

    def kind(self, adapter: str, request_id: str, attempt: int) -> str | None:
        return self._directives.get((adapter, request_id, attempt))


class FixtureAdapter:
    """A closed-world adapter: a missing response is an error, never a live call."""

    def __init__(
        self,
        kind: str,
        responses: dict[str, dict[str, Any]],
        failures: FailurePlan,
    ) -> None:
        self.kind = kind
        self._responses = deepcopy(responses)
        self._failures = failures
        self.calls = 0

    def invoke(
        self, request_id: str, attempt: int
    ) -> tuple[dict[str, Any], str | None]:
        self.calls += 1
        if request_id not in self._responses:
            raise ReplayError(f"no frozen response for {self.kind}/{request_id}")
        failure = self._failures.kind(self.kind, request_id, attempt)
        if failure in {"timeout", "disconnect", "cancellation", "stale_writer"}:
            raise InjectedReplayFailure(failure)
        if failure == "malformed_response":
            return {"malformed": True}, failure
        return deepcopy(self._responses[request_id]), failure


class DeterministicLLMAdapter(FixtureAdapter):
    pass


class DeterministicToolAdapter(FixtureAdapter):
    pass


class DeterministicNetworkAdapter(FixtureAdapter):
    pass


class DeterministicAudioAdapter(FixtureAdapter):
    pass


class DeterministicFrontendAdapter(FixtureAdapter):
    pass


_ADAPTER_CLASSES = {
    "llm": DeterministicLLMAdapter,
    "tool": DeterministicToolAdapter,
    "network": DeterministicNetworkAdapter,
    "audio": DeterministicAudioAdapter,
    "frontend": DeterministicFrontendAdapter,
}


@dataclass(frozen=True)
class ReplayResult:
    status: str
    terminal_state: dict[str, Any]
    action_outcomes: list[dict[str, Any]]
    events: list[dict[str, Any]]
    terminal_state_digest: str
    action_outcomes_digest: str
    event_chain_digest: str


def _apply_response(
    state: dict[str, Any],
    response: dict[str, Any],
    applied_effects: set[str],
) -> tuple[bool, str]:
    _validate_response(response, "response")
    operation = response["operation"]
    if operation == "noop":
        return False, response["outcome"]
    identity = response["idempotency_key"]
    if identity in applied_effects:
        return False, "duplicate_suppressed"
    key = response["key"]
    value = deepcopy(response["value"])
    if operation == "set":
        state[key] = value
    elif operation == "increment":
        current = state.get(key, 0)
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            raise ReplayError(f"state.{key}: cannot increment a non-number")
        updated = current + value
        if not math.isfinite(float(updated)):
            raise ReplayError(f"state.{key}: increment produced a non-finite value")
        state[key] = updated
    elif operation == "append_unique":
        current = state.setdefault(key, [])
        if not isinstance(current, list):
            raise ReplayError(f"state.{key}: append_unique requires an array")
        if value not in current:
            current.append(value)
    applied_effects.add(identity)
    revision = state.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ReplayError("state.revision: must be an integer")
    state["revision"] = revision + 1
    return True, response["outcome"]


def _event_category(adapter: str, status: str) -> str:
    if status == "recovered":
        return "recovery"
    if adapter == "llm":
        return "plan"
    return "outcome"


def _execute_replay(fixture: dict[str, Any], *, run_id: str) -> ReplayResult:
    _token(run_id, "run_id")
    clock = DeterministicClock(fixture["started_at"])
    failures = FailurePlan(fixture["failures"])
    adapters = {
        kind: _ADAPTER_CLASSES[kind](kind, fixture["adapters"][kind], failures)
        for kind in sorted(ADAPTER_KINDS)
    }
    state = deepcopy(fixture["initial_state"])
    state.setdefault("revision", 0)
    applied_effects: set[str] = set()
    outcomes: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    def emit(
        category: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        previous = events[-1] if events else None
        event = build_event_envelope(
            event_id=f"event-{len(events):04d}",
            run_id=run_id,
            sequence=len(events),
            timestamp=clock.now(),
            category=category,
            event_type=event_type,
            correlation_id=fixture["scenario_id"],
            causation_id=previous["event_id"] if previous else None,
            payload=payload,
            previous_event_hash=previous["event_hash"] if previous else None,
        )
        events.append(event)

    emit("system", "replay_started", {"fixture_revision": fixture["revision"]})
    terminal_status = "completed"
    for step in fixture["steps"]:
        clock.advance(step["advance_ms"])
        adapter = adapters[step["adapter"]]
        last_failure: str | None = None
        duplicate_suppressed = False
        outcome_code = "failed"
        committed = False
        attempts_used = 0
        for attempt in range(1, step["max_attempts"] + 1):
            attempts_used = attempt
            try:
                response, directive = adapter.invoke(step["request_id"], attempt)
                if directive is not None:
                    last_failure = directive
                committed, outcome_code = _apply_response(
                    state, response, applied_effects
                )
                duplicate_suppressed = (
                    not committed and outcome_code == "duplicate_suppressed"
                )
                if directive == "duplicate_event":
                    duplicate_committed, duplicate_outcome = _apply_response(
                        state, response, applied_effects
                    )
                    duplicate_suppressed = (
                        not duplicate_committed
                        and duplicate_outcome == "duplicate_suppressed"
                    )
                break
            except InjectedReplayFailure as exc:
                last_failure = exc.kind
            except ReplayError:
                last_failure = last_failure or "invalid_response"
            if attempt == step["max_attempts"]:
                terminal_status = "failed"
        status = (
            "failed"
            if terminal_status == "failed" and not committed
            else "recovered"
            if attempts_used > 1 or duplicate_suppressed
            else "succeeded"
        )
        outcome = {
            "step_id": step["id"],
            "adapter": step["adapter"],
            "request_id": step["request_id"],
            "status": status,
            "attempts": attempts_used,
            "outcome": outcome_code,
            "failure_kind": last_failure,
            "effect_committed": committed,
            "duplicate_suppressed": duplicate_suppressed,
        }
        outcomes.append(outcome)
        emit(
            _event_category(step["adapter"], status),
            f"replay_{step['adapter']}_{status}",
            outcome,
        )
        if status == "failed":
            break

    emit(
        "metric",
        "replay_finished",
        {
            "status": terminal_status,
            "steps_completed": len(outcomes),
            "effect_count": len(applied_effects),
        },
    )
    validate_event_chain(events)
    state_digest = canonical_digest(state)
    outcomes_digest = canonical_digest(outcomes)
    return ReplayResult(
        status=terminal_status,
        terminal_state=state,
        action_outcomes=outcomes,
        events=events,
        terminal_state_digest=state_digest,
        action_outcomes_digest=outcomes_digest,
        event_chain_digest=canonical_digest(events),
    )


def freeze_replay_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with expected digests calculated from offline execution."""

    candidate = deepcopy(fixture)
    candidate.pop("expected", None)
    validate_replay_fixture(candidate, require_expected=False)
    result = _execute_replay(candidate, run_id="freeze-run")
    candidate["expected"] = {
        "status": result.status,
        "terminal_state_digest": result.terminal_state_digest,
        "action_outcomes_digest": result.action_outcomes_digest,
    }
    validate_replay_fixture(candidate)
    return candidate


def run_replay(
    fixture: dict[str, Any],
    *,
    run_id: str = "replay-run",
) -> ReplayResult:
    """Execute only frozen responses and enforce the fixture's expected result."""

    validate_replay_fixture(fixture)
    result = _execute_replay(fixture, run_id=run_id)
    expected = fixture["expected"]
    observed = {
        "status": result.status,
        "terminal_state_digest": result.terminal_state_digest,
        "action_outcomes_digest": result.action_outcomes_digest,
    }
    if observed != expected:
        raise ReplayMismatchError(
            f"replay outcome drifted; expected={expected}, observed={observed}"
        )
    return result


def verify_replay_determinism(
    fixture: dict[str, Any],
    *,
    runs: int = 3,
    run_id: str = "determinism-run",
) -> ReplayResult:
    """Run the same fixture repeatedly and require identical semantic evidence."""

    if isinstance(runs, bool) or not isinstance(runs, int) or runs < 3:
        raise ReplayError("determinism verification requires at least three runs")
    results = [run_replay(fixture, run_id=run_id) for _ in range(runs)]
    identities = {
        (
            result.status,
            result.terminal_state_digest,
            result.action_outcomes_digest,
            result.event_chain_digest,
        )
        for result in results
    }
    if len(identities) != 1:
        raise ReplayMismatchError("repeated runs produced different evidence")
    return results[0]
