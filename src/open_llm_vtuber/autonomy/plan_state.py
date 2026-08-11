"""Durable contracts for resumable, evidence-linked plans.

The agenda answers *what deserves attention next*.  This module answers the
different question *how does a selected intention survive a crash while it is
being carried out?*  Keeping those responsibilities separate preserves the
existing live/headless agenda lease while giving every action a stable
execution identity and an atomic before/after checkpoint.

The store is local and may contain private action arguments.  Its ``snapshot``
method deliberately omits those arguments so operator and research views do
not accidentally become a second private-memory store.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, TypeVar

from filelock import FileLock, Timeout

PLAN_SCHEMA_VERSION = 1
PLAN_STORE_SCHEMA_VERSION = 1
PLAN_STATES = frozenset(
    {
        "ready",
        "running",
        "blocked",
        "repairing",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
    }
)
TERMINAL_PLAN_STATES = frozenset({"completed", "failed", "cancelled"})
STEP_STATES = frozenset(
    {
        "pending",
        "ready",
        "executing",
        "result_ready",
        "completed",
        "failed",
        "blocked",
        "cancelled",
    }
)
WORK_CLASSES = frozenset({"human", "urgent", "deadline", "background", "maintenance"})
RISK_LEVELS = frozenset({"low", "medium", "high"})
COUNTERFACTUAL_DECISIONS = frozenset({"allow", "block", "revise"})
REPORT_STATES = frozenset({"none", "pending", "delivering", "delivered", "dead_letter"})

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_MAX_STEPS = 256
_MAX_PLANS = 10_000
_MAX_ARGUMENT_BYTES = 50_000
_MAX_TRANSITIONS = 2_000
_T = TypeVar("_T")


class PlanValidationError(ValueError):
    """A persisted plan violates the closed state-machine contract."""


class PlanConflictError(RuntimeError):
    """A stale owner or invalid transition attempted to mutate a plan."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat()


def _parse_utc(value: str, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise PlanValidationError(f"{field_name} must be a UTC timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PlanValidationError(f"{field_name} must be a UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PlanValidationError(f"{field_name} must have an explicit UTC offset")
    return parsed


def _token(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise PlanValidationError(f"{field_name} must be a bounded opaque identifier")
    return value


def _bounded_reason(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise PlanValidationError(f"{field_name} must contain at most 500 characters")
    return value


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise PlanValidationError(f"plan data is not canonical JSON: {exc}") from exc


def result_digest(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
    return hashlib.sha256(raw).hexdigest()


@dataclass
class CounterfactualAssessment:
    decision: Literal["allow", "block", "revise"]
    reason_code: str
    alternatives_evaluated: int
    evaluated_at: str = field(default_factory=_iso)

    def validate(self) -> None:
        if self.decision not in COUNTERFACTUAL_DECISIONS:
            raise PlanValidationError("counterfactual decision is invalid")
        _token(self.reason_code, "counterfactual.reason_code")
        if not 1 <= int(self.alternatives_evaluated) <= 16:
            raise PlanValidationError(
                "counterfactual assessment must evaluate 1 to 16 alternatives"
            )
        _parse_utc(self.evaluated_at, "counterfactual.evaluated_at")


@dataclass
class PlanStep:
    step_id: str
    action_ref: str
    arguments: dict[str, Any] = field(default_factory=dict)
    precondition_refs: list[str] = field(default_factory=list)
    postcondition_refs: list[str] = field(default_factory=list)
    state: str = "pending"
    side_effecting: bool = False
    risk: str = "low"
    estimated_cost: float = 0.0
    max_attempts: int = 1
    attempts: int = 0
    idempotency_key: str = ""
    outcome_ref: str | None = None
    outcome_digest: str | None = None
    blocked_reason: str | None = None
    last_error: str | None = None
    counterfactual: CounterfactualAssessment | None = None
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str = field(default_factory=_iso)

    def validate(self) -> None:
        _token(self.step_id, "step.step_id")
        _token(self.action_ref, "step.action_ref")
        if self.state not in STEP_STATES:
            raise PlanValidationError(f"invalid step state: {self.state}")
        if not isinstance(self.arguments, dict):
            raise PlanValidationError("step arguments must be an object")
        if len(_canonical_json(self.arguments).encode("utf-8")) > _MAX_ARGUMENT_BYTES:
            raise PlanValidationError("step arguments exceed the local storage bound")
        for name, values in (
            ("precondition_refs", self.precondition_refs),
            ("postcondition_refs", self.postcondition_refs),
        ):
            if not isinstance(values, list) or len(values) > 64:
                raise PlanValidationError(f"step {name} must contain at most 64 refs")
            checked = [_token(value, f"step.{name}") for value in values]
            if len(checked) != len(set(checked)):
                raise PlanValidationError(f"step {name} cannot contain duplicates")
        if self.risk not in RISK_LEVELS:
            raise PlanValidationError(f"invalid step risk: {self.risk}")
        if (
            isinstance(self.estimated_cost, bool)
            or not isinstance(self.estimated_cost, (int, float))
            or not math.isfinite(float(self.estimated_cost))
            or self.estimated_cost < 0
        ):
            raise PlanValidationError(
                "step estimated_cost must be finite and nonnegative"
            )
        if not 1 <= int(self.max_attempts) <= 20:
            raise PlanValidationError("step max_attempts must be between 1 and 20")
        if not 0 <= int(self.attempts) <= int(self.max_attempts):
            raise PlanValidationError("step attempts exceed the step retry bound")
        if self.side_effecting:
            _token(self.idempotency_key, "step.idempotency_key")
        elif self.idempotency_key:
            _token(self.idempotency_key, "step.idempotency_key")
        if self.outcome_ref is not None:
            _token(self.outcome_ref, "step.outcome_ref")
        if self.outcome_digest is not None and not _DIGEST.fullmatch(
            self.outcome_digest
        ):
            raise PlanValidationError("step outcome_digest must be SHA-256")
        if (self.outcome_ref is None) != (self.outcome_digest is None):
            raise PlanValidationError("step outcome reference and digest move together")
        if self.state in {"result_ready", "completed"} and self.outcome_ref is None:
            raise PlanValidationError(f"{self.state} step requires a durable outcome")
        if self.state == "blocked":
            if self.blocked_reason is None:
                raise PlanValidationError("blocked step requires an inspectable reason")
            _bounded_reason(self.blocked_reason, "step.blocked_reason")
        _bounded_reason(self.last_error, "step.last_error")
        if self.counterfactual is not None:
            self.counterfactual.validate()
        if self.risk == "high" and self.state in {
            "executing",
            "result_ready",
            "completed",
        }:
            if self.counterfactual is None or self.counterfactual.decision != "allow":
                raise PlanValidationError(
                    "high-risk step cannot execute without an allowing counterfactual"
                )
        for value, name in (
            (self.started_at, "step.started_at"),
            (self.completed_at, "step.completed_at"),
            (self.updated_at, "step.updated_at"),
        ):
            if value is not None:
                _parse_utc(value, name)


@dataclass
class PlanTransition:
    sequence: int
    from_state: str
    to_state: str
    reason_code: str
    recorded_at: str
    step_id: str | None = None

    def validate(self) -> None:
        if self.sequence < 1:
            raise PlanValidationError("transition sequence must be positive")
        _token(self.from_state, "transition.from_state")
        _token(self.to_state, "transition.to_state")
        _token(self.reason_code, "transition.reason_code")
        if self.step_id is not None:
            _token(self.step_id, "transition.step_id")
        _parse_utc(self.recorded_at, "transition.recorded_at")


@dataclass
class RepairRecord:
    repair_id: str
    failure_ref: str
    replacement_step_ids: list[str]
    recorded_at: str = field(default_factory=_iso)

    def validate(self) -> None:
        _token(self.repair_id, "repair.repair_id")
        _token(self.failure_ref, "repair.failure_ref")
        if not self.replacement_step_ids:
            raise PlanValidationError("repair must contain replacement steps")
        for step_id in self.replacement_step_ids:
            _token(step_id, "repair.replacement_step_ids")
        _parse_utc(self.recorded_at, "repair.recorded_at")


@dataclass
class ResumablePlan:
    plan_id: str
    intention_record_id: str
    goal_ref: str
    work_class: str
    steps: list[PlanStep]
    deadline_at: str
    retry_budget: int
    repair_budget: int
    cost_budget: float
    state: str = "ready"
    current_step_index: int = 0
    cost_spent: float = 0.0
    owner_id: str | None = None
    lease_expires_at: str | None = None
    fencing_token: int = 0
    blocked_reason: str | None = None
    cancellation_reason: str | None = None
    terminal_report_state: str = "none"
    report_attempts: int = 0
    report_claimed_at: str | None = None
    report_error: str | None = None
    created_at: str = field(default_factory=_iso)
    updated_at: str = field(default_factory=_iso)
    transitions: list[PlanTransition] = field(default_factory=list)
    repairs: list[RepairRecord] = field(default_factory=list)

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_PLAN_STATES

    @property
    def current_step(self) -> PlanStep | None:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def validate(self) -> None:
        _token(self.plan_id, "plan.plan_id")
        _token(self.intention_record_id, "plan.intention_record_id")
        _token(self.goal_ref, "plan.goal_ref")
        if self.work_class not in WORK_CLASSES:
            raise PlanValidationError(f"invalid work class: {self.work_class}")
        if self.state not in PLAN_STATES:
            raise PlanValidationError(f"invalid plan state: {self.state}")
        created = _parse_utc(self.created_at, "plan.created_at")
        deadline = _parse_utc(self.deadline_at, "plan.deadline_at")
        _parse_utc(self.updated_at, "plan.updated_at")
        if deadline <= created:
            raise PlanValidationError("plan deadline must follow creation")
        if not self.steps or len(self.steps) > _MAX_STEPS:
            raise PlanValidationError(f"plan must contain 1 to {_MAX_STEPS} steps")
        step_ids: set[str] = set()
        for step in self.steps:
            step.validate()
            if step.step_id in step_ids:
                raise PlanValidationError(f"duplicate step id: {step.step_id}")
            step_ids.add(step.step_id)
        if not 0 <= self.current_step_index <= len(self.steps):
            raise PlanValidationError("current step index is outside the plan")
        if self.terminal:
            if self.state == "completed" and self.current_step_index != len(
                self.steps
            ):
                raise PlanValidationError(
                    "completed plan must advance beyond its final step"
                )
            if self.state != "completed" and self.current_step is None:
                raise PlanValidationError(
                    "failed or cancelled plan must retain its terminal step"
                )
        elif self.current_step_index >= len(self.steps):
            raise PlanValidationError("nonterminal plan requires a current step")

        for index, step in enumerate(self.steps):
            if index < self.current_step_index and step.state not in {
                "completed",
                "failed",
                "cancelled",
            }:
                raise PlanValidationError("past plan steps must be settled")
            if index > self.current_step_index and step.state not in {
                "pending",
                "cancelled",
            }:
                raise PlanValidationError("future plan steps cannot be active")
        current = self.current_step
        expected_current_states = {
            "ready": {"ready", "executing", "result_ready"},
            "running": {"ready", "executing", "result_ready"},
            "blocked": {"blocked"},
            "repairing": {"failed"},
            "cancelling": {"executing"},
            "failed": {"failed"},
            "cancelled": {"cancelled", "failed"},
        }
        allowed_current = expected_current_states.get(self.state)
        if current is not None and allowed_current is not None:
            if current.state not in allowed_current:
                raise PlanValidationError(
                    f"{self.state} plan has incompatible current step state"
                )
        if self.state == "completed" and any(
            step.state not in {"completed", "failed", "cancelled"}
            for step in self.steps
        ):
            raise PlanValidationError("completed plan contains unsettled work")
        for name, value in (
            ("retry_budget", self.retry_budget),
            ("repair_budget", self.repair_budget),
            ("report_attempts", self.report_attempts),
            ("fencing_token", self.fencing_token),
        ):
            if not isinstance(value, int) or value < 0:
                raise PlanValidationError(f"plan {name} must be a nonnegative integer")
        if (
            isinstance(self.cost_budget, bool)
            or not isinstance(self.cost_budget, (int, float))
            or not math.isfinite(float(self.cost_budget))
            or self.cost_budget <= 0
        ):
            raise PlanValidationError("plan cost budget must be finite and positive")
        if (
            not math.isfinite(float(self.cost_spent))
            or self.cost_spent < 0
            or self.cost_spent > self.cost_budget + 1e-9
        ):
            raise PlanValidationError("plan cost spent exceeds its budget")
        if self.state == "running":
            _token(self.owner_id, "plan.owner_id")
            if self.lease_expires_at is None:
                raise PlanValidationError("running plan requires a lease expiry")
            _parse_utc(self.lease_expires_at, "plan.lease_expires_at")
        elif self.owner_id is not None or self.lease_expires_at is not None:
            raise PlanValidationError("only a running plan may hold an execution lease")
        if self.state in {"blocked", "repairing", "cancelling"}:
            if self.blocked_reason is None:
                raise PlanValidationError(
                    f"{self.state} plan requires an inspectable blocked reason"
                )
            _bounded_reason(self.blocked_reason, "plan.blocked_reason")
        if self.cancellation_reason is not None:
            _bounded_reason(self.cancellation_reason, "plan.cancellation_reason")
        if self.terminal_report_state not in REPORT_STATES:
            raise PlanValidationError("invalid terminal report state")
        if self.terminal and self.terminal_report_state == "none":
            raise PlanValidationError("terminal plan must durably queue its report")
        if not self.terminal and self.terminal_report_state != "none":
            raise PlanValidationError("nonterminal plan cannot have a terminal report")
        if self.report_claimed_at is not None:
            _parse_utc(self.report_claimed_at, "plan.report_claimed_at")
        if (self.terminal_report_state == "delivering") != (
            self.report_claimed_at is not None
        ):
            raise PlanValidationError(
                "only a delivering terminal report may hold a claim timestamp"
            )
        _bounded_reason(self.report_error, "plan.report_error")
        if len(self.transitions) > _MAX_TRANSITIONS:
            raise PlanValidationError("plan transition history exceeds its bound")
        for index, transition in enumerate(self.transitions, start=1):
            transition.validate()
            if transition.sequence != index:
                raise PlanValidationError("plan transition sequence is not contiguous")
            if transition.step_id is not None and transition.step_id not in step_ids:
                raise PlanValidationError("plan transition cites an unknown step")
        repair_ids: set[str] = set()
        for repair in self.repairs:
            repair.validate()
            if repair.repair_id in repair_ids:
                raise PlanValidationError("duplicate plan repair id")
            repair_ids.add(repair.repair_id)
            if repair.failure_ref not in step_ids or any(
                step_id not in step_ids for step_id in repair.replacement_step_ids
            ):
                raise PlanValidationError("plan repair cites an unknown step")


@dataclass
class PlanStoreState:
    version: int = PLAN_STORE_SCHEMA_VERSION
    revision: int = 0
    plans: list[ResumablePlan] = field(default_factory=list)
    updated_at: str = field(default_factory=_iso)


def _step_from_dict(raw: dict[str, Any]) -> PlanStep:
    value = dict(raw)
    if value.get("counterfactual") is not None:
        value["counterfactual"] = CounterfactualAssessment(**value["counterfactual"])
    step = PlanStep(**value)
    step.validate()
    return step


def _plan_from_dict(raw: dict[str, Any]) -> ResumablePlan:
    value = dict(raw)
    value["steps"] = [_step_from_dict(item) for item in value.get("steps", [])]
    value["transitions"] = [
        PlanTransition(**item) for item in value.get("transitions", [])
    ]
    value["repairs"] = [RepairRecord(**item) for item in value.get("repairs", [])]
    plan = ResumablePlan(**value)
    plan.validate()
    return plan


def _state_from_dict(raw: dict[str, Any]) -> PlanStoreState:
    if not isinstance(raw, dict):
        raise PlanValidationError("plan store root must be an object")
    if int(raw.get("version", 0)) != PLAN_STORE_SCHEMA_VERSION:
        raise PlanValidationError("unsupported plan store schema version")
    state = PlanStoreState(
        version=PLAN_STORE_SCHEMA_VERSION,
        revision=int(raw.get("revision", 0)),
        plans=[_plan_from_dict(item) for item in raw.get("plans", [])],
        updated_at=str(raw.get("updated_at") or _iso()),
    )
    _parse_utc(state.updated_at, "store.updated_at")
    if state.revision < 0:
        raise PlanValidationError("plan store revision cannot be negative")
    ids = [plan.plan_id for plan in state.plans]
    if len(ids) != len(set(ids)):
        raise PlanValidationError("plan store contains duplicate plan ids")
    if len(ids) > _MAX_PLANS:
        raise PlanValidationError("plan store exceeds its plan bound")
    return state


def build_plan_step(
    *,
    step_id: str,
    action_ref: str,
    arguments: dict[str, Any] | None = None,
    precondition_refs: Iterable[str] = (),
    postcondition_refs: Iterable[str] = (),
    side_effecting: bool = False,
    risk: str = "low",
    estimated_cost: float = 0.0,
    max_attempts: int = 1,
) -> PlanStep:
    """Construct a step with a stable execution identity before it is stored."""

    step = PlanStep(
        step_id=step_id,
        action_ref=action_ref,
        arguments=dict(arguments or {}),
        precondition_refs=list(precondition_refs),
        postcondition_refs=list(postcondition_refs),
        side_effecting=side_effecting,
        risk=risk,
        estimated_cost=float(estimated_cost),
        max_attempts=int(max_attempts),
        idempotency_key=f"exec:{step_id}",
    )
    step.validate()
    return step


def build_plan(
    *,
    plan_id: str,
    intention_record_id: str,
    goal_ref: str,
    work_class: str,
    steps: Iterable[PlanStep],
    deadline_at: str,
    retry_budget: int = 3,
    repair_budget: int = 1,
    cost_budget: float = 10.0,
    created_at: str | None = None,
) -> ResumablePlan:
    step_list = list(steps)
    for step in step_list:
        if (
            step.state != "pending"
            or step.attempts != 0
            or step.outcome_ref is not None
            or step.started_at is not None
            or step.completed_at is not None
        ):
            raise PlanValidationError("new plan steps must be pristine and pending")
    if step_list:
        step_list[0].state = "ready"
    plan = ResumablePlan(
        plan_id=plan_id,
        intention_record_id=intention_record_id,
        goal_ref=goal_ref,
        work_class=work_class,
        steps=step_list,
        deadline_at=deadline_at,
        retry_budget=int(retry_budget),
        repair_budget=int(repair_budget),
        cost_budget=float(cost_budget),
        created_at=created_at or _iso(),
        updated_at=created_at or _iso(),
    )
    plan.validate()
    return plan


class ResumablePlanStore:
    """Atomic workspace-local materialization of all executable plan states."""

    def __init__(
        self,
        workspace_root: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.root = Path(workspace_root).resolve()
        self.path = self.root / "agenda" / "plans.json"
        self.lock = FileLock(str(self.root / "agenda" / "plans.lock"), timeout=5)
        self._clock = clock

    def now(self) -> datetime:
        return self._clock().astimezone(timezone.utc)

    def load(self) -> PlanStoreState:
        if not self.path.exists():
            return PlanStoreState(updated_at=_iso(self.now()))
        try:
            return _state_from_dict(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise PlanValidationError(f"plan store is unreadable: {exc}") from exc

    def _save(self, state: PlanStoreState) -> None:
        _state_from_dict(asdict(state))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    asdict(state),
                    handle,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def mutate(self, operation: Callable[[PlanStoreState], _T]) -> _T:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.lock:
                state = self.load()
                before = _canonical_json(asdict(state))
                result = operation(state)
                if _canonical_json(asdict(state)) != before:
                    state.revision += 1
                    state.updated_at = _iso(self.now())
                    self._save(state)
                return result
        except Timeout as exc:
            raise OSError("plan store is busy") from exc

    def reset_for_debug(self) -> int:
        """Clear resumable plans paired with a debug agenda reset."""

        def operation(state: PlanStoreState) -> int:
            removed = len(state.plans)
            state.plans.clear()
            return removed

        return self.mutate(operation)

    @staticmethod
    def _find(state: PlanStoreState, plan_id: str) -> ResumablePlan:
        try:
            return next(plan for plan in state.plans if plan.plan_id == plan_id)
        except StopIteration as exc:
            raise KeyError(plan_id) from exc

    def get(self, plan_id: str) -> ResumablePlan | None:
        state = self.load()
        plan = next((item for item in state.plans if item.plan_id == plan_id), None)
        return _plan_from_dict(asdict(plan)) if plan is not None else None

    def list(self) -> list[ResumablePlan]:
        return [_plan_from_dict(asdict(plan)) for plan in self.load().plans]

    def snapshot(self) -> dict[str, Any]:
        """Content-free operator view; action arguments never leave the store."""

        state = self.load()
        return {
            "version": state.version,
            "revision": state.revision,
            "updated_at": state.updated_at,
            "plans": [
                {
                    "plan_id": plan.plan_id,
                    "intention_record_id": plan.intention_record_id,
                    "goal_ref": plan.goal_ref,
                    "work_class": plan.work_class,
                    "state": plan.state,
                    "current_step_index": plan.current_step_index,
                    "step_count": len(plan.steps),
                    "deadline_at": plan.deadline_at,
                    "retry_budget": plan.retry_budget,
                    "repair_budget": plan.repair_budget,
                    "cost_budget": plan.cost_budget,
                    "cost_spent": plan.cost_spent,
                    "owner_id": plan.owner_id,
                    "lease_expires_at": plan.lease_expires_at,
                    "fencing_token": plan.fencing_token,
                    "blocked_reason": plan.blocked_reason,
                    "terminal_report_state": plan.terminal_report_state,
                    "steps": [
                        {
                            "step_id": step.step_id,
                            "action_ref": step.action_ref,
                            "state": step.state,
                            "side_effecting": step.side_effecting,
                            "risk": step.risk,
                            "attempts": step.attempts,
                            "max_attempts": step.max_attempts,
                            "outcome_ref": step.outcome_ref,
                            "blocked_reason": step.blocked_reason,
                        }
                        for step in plan.steps
                    ],
                }
                for plan in state.plans
            ],
        }

    def create(self, plan: ResumablePlan) -> ResumablePlan:
        plan.validate()
        if (
            plan.state != "ready"
            or plan.current_step_index != 0
            or plan.transitions
            or plan.repairs
            or plan.terminal_report_state != "none"
            or any(
                step.state != ("ready" if index == 0 else "pending")
                or step.attempts != 0
                or step.outcome_ref is not None
                for index, step in enumerate(plan.steps)
            )
        ):
            raise PlanValidationError("create accepts only a pristine ready plan")

        def operation(state: PlanStoreState) -> ResumablePlan:
            if len(state.plans) >= _MAX_PLANS:
                raise PlanValidationError("plan store is at capacity")
            if any(item.plan_id == plan.plan_id for item in state.plans):
                raise PlanConflictError(f"plan already exists: {plan.plan_id}")
            stored = _plan_from_dict(asdict(plan))
            self._transition(stored, "plan:none", "plan:ready", "plan_created")
            state.plans.append(stored)
            return _plan_from_dict(asdict(stored))

        return self.mutate(operation)

    def claim(
        self,
        plan_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        lease_seconds: float,
    ) -> ResumablePlan:
        _token(owner_id, "owner_id")
        lease_seconds = max(1.0, min(300.0, float(lease_seconds)))

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            now = self.now()
            if plan.terminal or plan.state in {"blocked", "repairing", "cancelling"}:
                raise PlanConflictError(f"plan cannot be claimed from {plan.state}")
            if fencing_token < plan.fencing_token:
                raise PlanConflictError("plan claim has a stale fencing token")
            if plan.state == "running" and plan.owner_id != owner_id:
                expiry = _parse_utc(plan.lease_expires_at or "", "lease_expires_at")
                if expiry > now:
                    raise PlanConflictError("plan is leased by another owner")
            old = plan.state
            plan.state = "running"
            plan.owner_id = owner_id
            plan.fencing_token = int(fencing_token)
            plan.lease_expires_at = _iso(now + timedelta(seconds=lease_seconds))
            plan.blocked_reason = None
            plan.updated_at = _iso(now)
            self._transition(plan, f"plan:{old}", "plan:running", "lease_claimed")
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def heartbeat(
        self,
        plan_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        lease_seconds: float,
    ) -> ResumablePlan:
        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            self._assert_owner(plan, owner_id, fencing_token)
            plan.lease_expires_at = _iso(
                self.now()
                + timedelta(seconds=max(1.0, min(300.0, float(lease_seconds))))
            )
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def begin_step(
        self, plan_id: str, *, owner_id: str, fencing_token: int
    ) -> PlanStep:
        """Persist the before-action checkpoint and return its stable key."""

        def operation(state: PlanStoreState) -> PlanStep:
            plan = self._find(state, plan_id)
            self._assert_owner(plan, owner_id, fencing_token)
            step = plan.current_step
            if step is None:
                raise PlanConflictError("plan has no current step")
            if step.state in {"executing", "result_ready"}:
                return _step_from_dict(asdict(step))
            if step.state not in {"pending", "ready"}:
                raise PlanConflictError(f"step cannot begin from {step.state}")
            if step.attempts >= step.max_attempts:
                raise PlanConflictError("step attempt budget is exhausted")
            if plan.cost_spent + step.estimated_cost > plan.cost_budget + 1e-9:
                raise PlanConflictError("plan cost budget would be exceeded")
            if step.risk == "high" and (
                step.counterfactual is None or step.counterfactual.decision != "allow"
            ):
                raise PlanConflictError(
                    "high-risk step requires an allowing counterfactual"
                )
            old = step.state
            step.state = "executing"
            step.attempts += 1
            step.started_at = step.started_at or _iso(self.now())
            step.updated_at = _iso(self.now())
            plan.cost_spent += step.estimated_cost
            plan.updated_at = _iso(self.now())
            self._transition(
                plan,
                f"step:{old}",
                "step:executing",
                "before_action_checkpoint",
                step.step_id,
            )
            plan.validate()
            return _step_from_dict(asdict(step))

        return self.mutate(operation)

    def record_counterfactual(
        self,
        plan_id: str,
        assessment: CounterfactualAssessment,
        *,
        owner_id: str,
        fencing_token: int,
    ) -> PlanStep:
        assessment.validate()

        def operation(state: PlanStoreState) -> PlanStep:
            plan = self._find(state, plan_id)
            self._assert_owner(plan, owner_id, fencing_token)
            step = plan.current_step
            if step is None or step.state not in {"pending", "ready"}:
                raise PlanConflictError("counterfactual is too late for this step")
            step.counterfactual = CounterfactualAssessment(**asdict(assessment))
            step.updated_at = _iso(self.now())
            self._transition(
                plan,
                "step:counterfactual_pending",
                f"step:counterfactual_{assessment.decision}",
                assessment.reason_code,
                step.step_id,
            )
            plan.validate()
            return _step_from_dict(asdict(step))

        return self.mutate(operation)

    def record_result(
        self,
        plan_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        outcome_ref: str,
        outcome_digest: str,
    ) -> PlanStep:
        _token(outcome_ref, "outcome_ref")
        if not _DIGEST.fullmatch(outcome_digest):
            raise PlanValidationError("outcome_digest must be SHA-256")

        def operation(state: PlanStoreState) -> PlanStep:
            plan = self._find(state, plan_id)
            self._assert_owner(plan, owner_id, fencing_token)
            step = plan.current_step
            if step is None:
                raise PlanConflictError("plan has no current step")
            if step.state == "result_ready":
                if (step.outcome_ref, step.outcome_digest) != (
                    outcome_ref,
                    outcome_digest,
                ):
                    raise PlanConflictError("step already has a different outcome")
                return _step_from_dict(asdict(step))
            if step.state != "executing":
                raise PlanConflictError(
                    f"step cannot record a result from {step.state}"
                )
            step.state = "result_ready"
            step.outcome_ref = outcome_ref
            step.outcome_digest = outcome_digest
            step.updated_at = _iso(self.now())
            self._transition(
                plan,
                "step:executing",
                "step:result_ready",
                "after_action_checkpoint",
                step.step_id,
            )
            plan.validate()
            return _step_from_dict(asdict(step))

        return self.mutate(operation)

    def commit_step(
        self, plan_id: str, *, owner_id: str, fencing_token: int
    ) -> ResumablePlan:
        """Commit the postcondition boundary and terminal report atomically."""

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            self._assert_owner(plan, owner_id, fencing_token)
            step = plan.current_step
            if step is None or step.state != "result_ready":
                raise PlanConflictError("only a result-ready step can commit")
            step.state = "completed"
            step.completed_at = _iso(self.now())
            step.updated_at = step.completed_at
            self._transition(
                plan,
                "step:result_ready",
                "step:completed",
                "postconditions_satisfied",
                step.step_id,
            )
            plan.current_step_index += 1
            if plan.current_step_index >= len(plan.steps):
                plan.state = "completed"
                plan.owner_id = None
                plan.lease_expires_at = None
                plan.terminal_report_state = "pending"
                self._transition(
                    plan,
                    "plan:running",
                    "plan:completed",
                    "all_steps_committed",
                )
            else:
                next_step = plan.current_step
                assert next_step is not None
                if next_step.state == "pending":
                    next_step.state = "ready"
                    next_step.updated_at = _iso(self.now())
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def fail_current(
        self,
        plan_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        reason_code: str,
        error: str,
        retryable: bool,
    ) -> ResumablePlan:
        _token(reason_code, "reason_code")

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            self._assert_owner(plan, owner_id, fencing_token)
            step = plan.current_step
            if step is None or step.state in {"completed", "cancelled"}:
                raise PlanConflictError("plan has no failable current step")
            previous = step.state
            step.last_error = str(error)[:500] or reason_code
            step.updated_at = _iso(self.now())
            plan.retry_budget = max(0, plan.retry_budget - 1)
            safe_retry = (
                retryable
                and plan.retry_budget > 0
                and step.attempts < step.max_attempts
                and (not step.side_effecting or step.outcome_ref is not None)
            )
            if safe_retry:
                step.state = "ready"
                step.outcome_ref = None
                step.outcome_digest = None
                plan.state = "running"
                self._transition(
                    plan,
                    f"step:{previous}",
                    "step:ready",
                    reason_code,
                    step.step_id,
                )
            else:
                step.state = "failed"
                if plan.repair_budget > 0:
                    plan.state = "repairing"
                    plan.blocked_reason = f"repair_pending:{reason_code}"[:500]
                    plan.owner_id = None
                    plan.lease_expires_at = None
                    self._transition(
                        plan,
                        f"step:{previous}",
                        "plan:repairing",
                        reason_code,
                        step.step_id,
                    )
                else:
                    self._finish_terminal(plan, "failed", reason_code)
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def block_current(
        self,
        plan_id: str,
        *,
        owner_id: str,
        fencing_token: int,
        reason_code: str,
        detail: str,
    ) -> ResumablePlan:
        _token(reason_code, "reason_code")

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            self._assert_owner(plan, owner_id, fencing_token)
            step = plan.current_step
            if step is None:
                raise PlanConflictError("plan has no current step")
            previous = step.state
            step.state = "blocked"
            step.blocked_reason = f"{reason_code}:{detail}"[:500]
            step.updated_at = _iso(self.now())
            plan.state = "blocked"
            plan.blocked_reason = step.blocked_reason
            plan.owner_id = None
            plan.lease_expires_at = None
            plan.updated_at = _iso(self.now())
            self._transition(
                plan,
                f"step:{previous}",
                "plan:blocked",
                reason_code,
                step.step_id,
            )
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def apply_repair(
        self,
        plan_id: str,
        *,
        failure_ref: str,
        replacement_steps: Iterable[PlanStep],
    ) -> ResumablePlan:
        """Replace only the uncommitted suffix, preserving failed history."""

        replacements = list(replacement_steps)
        _token(failure_ref, "failure_ref")
        if not replacements or len(replacements) > 32:
            raise PlanValidationError("repair must contain 1 to 32 replacement steps")
        for step in replacements:
            step.validate()
            if (
                step.state != "pending"
                or step.attempts != 0
                or step.outcome_ref is not None
                or step.started_at is not None
                or step.completed_at is not None
            ):
                raise PlanValidationError(
                    "replacement steps must be pristine and pending"
                )

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            if plan.state != "repairing" or plan.repair_budget <= 0:
                raise PlanConflictError("plan is not awaiting a bounded repair")
            failed = plan.current_step
            if (
                failed is None
                or failed.step_id != failure_ref
                or failed.state != "failed"
            ):
                raise PlanConflictError("repair does not cite the failed current step")
            existing_ids = {step.step_id for step in plan.steps}
            if existing_ids.intersection(step.step_id for step in replacements):
                raise PlanConflictError("repair step ids must be new")
            risk_rank = {"low": 0, "medium": 1, "high": 2}
            if any(
                risk_rank[step.risk] > risk_rank[failed.risk] for step in replacements
            ):
                raise PlanConflictError("repair cannot increase the failed step's risk")
            if not failed.side_effecting and any(
                step.side_effecting for step in replacements
            ):
                raise PlanConflictError(
                    "repair cannot introduce a side effect into read-only work"
                )
            if (
                failed.side_effecting
                and failed.outcome_ref is not None
                and any(step.side_effecting for step in replacements)
            ):
                raise PlanConflictError(
                    "repair cannot replay a side effect with a recorded outcome"
                )
            added_cost = sum(step.estimated_cost for step in replacements)
            if plan.cost_spent + added_cost > plan.cost_budget + 1e-9:
                raise PlanConflictError("repair exceeds the remaining cost budget")
            for step in plan.steps[plan.current_step_index + 1 :]:
                if step.state in {"pending", "ready"}:
                    step.state = "cancelled"
                    step.updated_at = _iso(self.now())
            start = len(plan.steps)
            replacements[0].state = "ready"
            plan.steps.extend(_step_from_dict(asdict(step)) for step in replacements)
            plan.current_step_index = start
            plan.repair_budget -= 1
            plan.state = "ready"
            plan.blocked_reason = None
            plan.repairs.append(
                RepairRecord(
                    repair_id=f"repair-{uuid.uuid4().hex}",
                    failure_ref=failure_ref,
                    replacement_step_ids=[step.step_id for step in replacements],
                    recorded_at=_iso(self.now()),
                )
            )
            self._transition(
                plan,
                "plan:repairing",
                "plan:ready",
                "bounded_repair_applied",
                replacements[0].step_id,
            )
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def abandon_repair(self, plan_id: str, *, reason_code: str) -> ResumablePlan:
        _token(reason_code, "reason_code")

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            if plan.state != "repairing":
                raise PlanConflictError("plan is not awaiting repair")
            self._finish_terminal(plan, "failed", reason_code)
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def cancel(self, plan_id: str, *, reason: str) -> ResumablePlan:
        cleaned = _bounded_reason(reason, "cancellation reason")
        assert cleaned is not None

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            if plan.terminal:
                return _plan_from_dict(asdict(plan))
            step = plan.current_step
            plan.cancellation_reason = cleaned
            unknown_side_effect = bool(
                step is not None
                and step.side_effecting
                and step.attempts > 0
                and step.outcome_ref is None
                and step.state in {"executing", "blocked"}
            )
            if unknown_side_effect:
                assert step is not None
                old = plan.state
                step.state = "executing"
                plan.state = "cancelling"
                plan.blocked_reason = "outcome_reconciliation_required"
                plan.owner_id = None
                plan.lease_expires_at = None
                self._transition(
                    plan,
                    f"plan:{old}",
                    "plan:cancelling",
                    "side_effect_outcome_unknown",
                    step.step_id,
                )
            else:
                for item in plan.steps[plan.current_step_index :]:
                    if item.state not in {"completed", "failed"}:
                        item.state = "cancelled"
                        item.updated_at = _iso(self.now())
                self._finish_terminal(plan, "cancelled", "user_cancelled")
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def release_for_preemption(
        self,
        plan_id: str,
        *,
        fencing_token: int,
        reason_code: str = "human_preemption",
    ) -> ResumablePlan:
        """Release an interrupted lease without replaying an unknown effect."""

        _token(reason_code, "reason_code")

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            if plan.terminal or plan.state != "running":
                return _plan_from_dict(asdict(plan))
            if int(fencing_token) < plan.fencing_token:
                raise PlanConflictError("plan release has a stale fencing token")
            plan.fencing_token = int(fencing_token)
            step = plan.current_step
            if self._has_unknown_side_effect(step):
                assert step is not None
                step.state = "executing"
                plan.state = "cancelling"
                plan.cancellation_reason = "execution preempted"
                plan.blocked_reason = "outcome_reconciliation_required"
                target = "plan:cancelling"
                transition_reason = "preempted_side_effect_outcome_unknown"
            elif step is not None and step.state == "executing":
                if step.attempts < step.max_attempts:
                    step.state = "ready"
                    step.updated_at = _iso(self.now())
                    plan.state = "ready"
                    plan.blocked_reason = None
                    target = "plan:ready"
                    transition_reason = reason_code
                elif plan.repair_budget > 0:
                    step.state = "failed"
                    step.last_error = "preempted_attempt_budget_exhausted"
                    step.updated_at = _iso(self.now())
                    plan.state = "repairing"
                    plan.blocked_reason = "repair_pending:preempted"
                    target = "plan:repairing"
                    transition_reason = "preempted_attempt_budget_exhausted"
                else:
                    step.state = "failed"
                    step.last_error = "preempted_attempt_budget_exhausted"
                    step.updated_at = _iso(self.now())
                    self._finish_terminal(
                        plan, "failed", "preempted_attempt_budget_exhausted"
                    )
                    target = "plan:failed"
                    transition_reason = "preempted_attempt_budget_exhausted"
            else:
                plan.state = "ready"
                plan.blocked_reason = None
                target = "plan:ready"
                transition_reason = reason_code
            plan.owner_id = None
            plan.lease_expires_at = None
            plan.updated_at = _iso(self.now())
            if not plan.terminal:
                self._transition(
                    plan,
                    "plan:running",
                    target,
                    transition_reason,
                    step.step_id if step is not None else None,
                )
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def expire(self, plan_id: str) -> ResumablePlan:
        """Expire a plan without declaring an unknown external effect absent."""

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            if plan.terminal:
                return _plan_from_dict(asdict(plan))
            if _parse_utc(plan.deadline_at, "plan.deadline_at") > self.now():
                raise PlanConflictError("plan deadline has not passed")
            step = plan.current_step
            if self._has_unknown_side_effect(step):
                assert step is not None
                old = plan.state
                step.state = "executing"
                step.last_error = "plan_deadline_expired_outcome_unknown"
                step.updated_at = _iso(self.now())
                plan.state = "cancelling"
                plan.cancellation_reason = "plan deadline expired"
                plan.blocked_reason = "deadline_expired_outcome_reconciliation_required"
                plan.owner_id = None
                plan.lease_expires_at = None
                self._transition(
                    plan,
                    f"plan:{old}",
                    "plan:cancelling",
                    "deadline_expired_outcome_unknown",
                    step.step_id,
                )
            else:
                if step is not None and step.state not in {"completed", "cancelled"}:
                    step.state = "failed"
                    step.last_error = "plan_deadline_expired"
                    step.updated_at = _iso(self.now())
                self._finish_terminal(plan, "failed", "deadline_expired")
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def reconcile_cancellation(
        self,
        plan_id: str,
        *,
        outcome_ref: str,
        outcome_digest: str,
    ) -> ResumablePlan:
        _token(outcome_ref, "outcome_ref")
        if not _DIGEST.fullmatch(outcome_digest):
            raise PlanValidationError("outcome_digest must be SHA-256")

        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            if plan.state != "cancelling":
                raise PlanConflictError(
                    "plan is not awaiting cancellation reconciliation"
                )
            step = plan.current_step
            if step is None or step.state != "executing":
                raise PlanConflictError("cancelling plan has no executing step")
            step.outcome_ref = outcome_ref
            step.outcome_digest = outcome_digest
            step.state = "cancelled"
            step.updated_at = _iso(self.now())
            for item in plan.steps[plan.current_step_index + 1 :]:
                if item.state in {"pending", "ready"}:
                    item.state = "cancelled"
            self._finish_terminal(plan, "cancelled", "outcome_reconciled")
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def recover_expired(self, at: datetime | None = None) -> list[str]:
        moment = (at or self.now()).astimezone(timezone.utc)

        def operation(state: PlanStoreState) -> list[str]:
            recovered: list[str] = []
            for plan in state.plans:
                if plan.state != "running" or plan.lease_expires_at is None:
                    continue
                if _parse_utc(plan.lease_expires_at, "lease_expires_at") > moment:
                    continue
                plan.state = "ready"
                plan.owner_id = None
                plan.lease_expires_at = None
                plan.updated_at = _iso(moment)
                self._transition(
                    plan,
                    "plan:running",
                    "plan:ready",
                    "expired_lease_recovered",
                    plan.current_step.step_id if plan.current_step else None,
                )
                recovered.append(plan.plan_id)
                plan.validate()
            for plan in state.plans:
                if plan.terminal:
                    continue
                if _parse_utc(plan.deadline_at, "plan.deadline_at") > moment:
                    continue
                step = plan.current_step
                if self._has_unknown_side_effect(step):
                    if plan.state == "cancelling":
                        continue
                    assert step is not None
                    old = plan.state
                    step.state = "executing"
                    step.last_error = "plan_deadline_expired_outcome_unknown"
                    step.updated_at = _iso(moment)
                    plan.state = "cancelling"
                    plan.cancellation_reason = "plan deadline expired"
                    plan.blocked_reason = (
                        "deadline_expired_outcome_reconciliation_required"
                    )
                    plan.owner_id = None
                    plan.lease_expires_at = None
                    self._transition(
                        plan,
                        f"plan:{old}",
                        "plan:cancelling",
                        "deadline_expired_outcome_unknown",
                        step.step_id,
                    )
                else:
                    if step is not None and step.state not in {
                        "completed",
                        "cancelled",
                    }:
                        step.state = "failed"
                        step.last_error = "plan_deadline_expired"
                        step.updated_at = _iso(moment)
                    self._finish_terminal(plan, "failed", "deadline_expired")
                plan.updated_at = _iso(moment)
                if plan.plan_id not in recovered:
                    recovered.append(plan.plan_id)
                plan.validate()
            for plan in state.plans:
                if plan.terminal_report_state != "delivering":
                    continue
                claimed = _parse_utc(
                    plan.report_claimed_at or plan.updated_at,
                    "report_claimed_at",
                )
                if claimed + timedelta(minutes=5) <= moment:
                    plan.terminal_report_state = "pending"
                    plan.report_claimed_at = None
                    plan.report_error = "report_delivery_lease_expired"
                    plan.updated_at = _iso(moment)
            return recovered

        return self.mutate(operation)

    @staticmethod
    def _has_unknown_side_effect(step: PlanStep | None) -> bool:
        return bool(
            step is not None
            and step.side_effecting
            and step.attempts > 0
            and step.outcome_ref is None
            and step.state in {"executing", "blocked"}
        )

    def claim_terminal_report(
        self, plan_id: str | None = None, *, at: datetime | None = None
    ) -> ResumablePlan | None:
        moment = (at or self.now()).astimezone(timezone.utc)

        def operation(state: PlanStoreState) -> ResumablePlan | None:
            candidates = [
                plan
                for plan in state.plans
                if plan.terminal
                and plan.terminal_report_state == "pending"
                and (plan_id is None or plan.plan_id == plan_id)
            ]
            if not candidates:
                return None
            plan = sorted(candidates, key=lambda item: (item.updated_at, item.plan_id))[
                0
            ]
            plan.terminal_report_state = "delivering"
            plan.report_attempts += 1
            plan.report_claimed_at = _iso(moment)
            plan.report_error = None
            plan.updated_at = _iso(moment)
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    def finish_terminal_report(
        self,
        plan_id: str,
        *,
        delivered: bool,
        error: str | None = None,
        max_attempts: int = 5,
    ) -> ResumablePlan:
        def operation(state: PlanStoreState) -> ResumablePlan:
            plan = self._find(state, plan_id)
            if plan.terminal_report_state != "delivering":
                raise PlanConflictError("terminal report is not claimed")
            plan.report_claimed_at = None
            plan.report_error = None if delivered else (str(error or "failed")[:500])
            if delivered:
                plan.terminal_report_state = "delivered"
            elif plan.report_attempts >= max(1, min(20, int(max_attempts))):
                plan.terminal_report_state = "dead_letter"
            else:
                plan.terminal_report_state = "pending"
            plan.updated_at = _iso(self.now())
            plan.validate()
            return _plan_from_dict(asdict(plan))

        return self.mutate(operation)

    @staticmethod
    def _assert_owner(plan: ResumablePlan, owner_id: str, fencing_token: int) -> None:
        if plan.state != "running":
            raise PlanConflictError(f"plan is not running: {plan.state}")
        if plan.owner_id != owner_id or plan.fencing_token != int(fencing_token):
            raise PlanConflictError("plan mutation has a stale owner or fencing token")

    @staticmethod
    def _transition(
        plan: ResumablePlan,
        from_state: str,
        to_state: str,
        reason_code: str,
        step_id: str | None = None,
    ) -> None:
        if len(plan.transitions) >= _MAX_TRANSITIONS:
            raise PlanConflictError("plan transition history is at capacity")
        plan.transitions.append(
            PlanTransition(
                sequence=len(plan.transitions) + 1,
                from_state=from_state,
                to_state=to_state,
                reason_code=reason_code,
                recorded_at=plan.updated_at,
                step_id=step_id,
            )
        )

    def _finish_terminal(
        self, plan: ResumablePlan, target: str, reason_code: str
    ) -> None:
        old = plan.state
        plan.state = target
        plan.owner_id = None
        plan.lease_expires_at = None
        plan.blocked_reason = None
        plan.terminal_report_state = "pending"
        self._transition(plan, f"plan:{old}", f"plan:{target}", reason_code)
