"""Reactive/deliberative separation and deterministic fair plan scheduling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Iterable, TypeVar

from .plan_state import ResumablePlan

_T = TypeVar("_T")


@dataclass(frozen=True)
class ScheduleDecision:
    plan_id: str
    work_class: str
    reason_code: str


class FairPlanScheduler:
    """Strict human priority plus starvation-safe weighted background service."""

    def __init__(
        self,
        *,
        weights: dict[str, int] | None = None,
        starvation_seconds: float = 300.0,
    ) -> None:
        self.weights = weights or {
            "urgent": 8,
            "deadline": 6,
            "background": 3,
            "maintenance": 1,
        }
        expected = {"urgent", "deadline", "background", "maintenance"}
        if set(self.weights) != expected or any(
            not isinstance(value, int) or value <= 0 for value in self.weights.values()
        ):
            raise ValueError("scheduler weights must cover every non-human work class")
        self.starvation_seconds = max(1.0, float(starvation_seconds))
        self._served = {name: 0 for name in expected}

    def choose(
        self,
        plans: Iterable[ResumablePlan],
        *,
        now: datetime | None = None,
    ) -> ScheduleDecision | None:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        available = [
            plan for plan in plans if not plan.terminal and plan.state == "ready"
        ]
        if not available:
            return None

        human = [plan for plan in available if plan.work_class == "human"]
        if human:
            selected = min(human, key=lambda plan: (plan.created_at, plan.plan_id))
            return ScheduleDecision(selected.plan_id, "human", "human_preemption")

        # Any non-human class eventually ages past strict priority. This keeps a
        # stream of urgent work from making maintenance immortal.
        starved = [
            plan
            for plan in available
            if moment - _parse(plan.created_at)
            >= timedelta(seconds=self.starvation_seconds)
        ]
        if starved:
            selected = min(starved, key=lambda plan: (plan.created_at, plan.plan_id))
            self._served[selected.work_class] += 1
            return ScheduleDecision(
                selected.plan_id, selected.work_class, "starvation_prevention"
            )

        deadlines = [
            plan
            for plan in available
            if plan.work_class == "deadline"
            and _parse(plan.deadline_at) - moment <= timedelta(minutes=5)
        ]
        if deadlines:
            selected = min(deadlines, key=lambda plan: (plan.deadline_at, plan.plan_id))
            self._served["deadline"] += 1
            return ScheduleDecision(selected.plan_id, "deadline", "deadline_imminent")

        # Weighted fair queueing: the class with the smallest normalized service
        # count wins, then the oldest plan in that class. The counters are small
        # and periodically normalized so a multi-day process cannot grow them
        # without bound.
        populated = {
            name: [plan for plan in available if plan.work_class == name]
            for name in self.weights
        }
        populated = {name: values for name, values in populated.items() if values}
        if not populated:
            return None
        selected_class = min(
            populated,
            key=lambda name: (self._served[name] / self.weights[name], name),
        )
        selected = min(
            populated[selected_class], key=lambda plan: (plan.created_at, plan.plan_id)
        )
        self._served[selected_class] += 1
        floor = min(self._served.values())
        if floor >= 10_000:
            self._served = {name: count - floor for name, count in self._served.items()}
        return ScheduleDecision(selected.plan_id, selected_class, "weighted_fair_turn")

    def choose_agenda_entries(
        self,
        entries: Iterable[object],
        *,
        now: datetime | None = None,
    ) -> ScheduleDecision | None:
        """Apply the same policy before an agenda entry has a concrete plan."""

        candidates = []
        for entry in entries:
            kind = str(getattr(entry, "kind", "pursuit"))
            origin = str(getattr(entry, "origin", "autonomous"))
            metadata = getattr(entry, "metadata", {}) or {}
            if kind == "user":
                work_class = "human"
            elif kind == "scheduled" or metadata.get("schedule_occurrence"):
                work_class = "deadline"
            elif origin == "external_event" or metadata.get("external_event_id"):
                work_class = "urgent"
            elif kind == "idle":
                work_class = "maintenance"
            else:
                work_class = "background"
            budget = getattr(entry, "budget")
            deadline_at = getattr(budget, "deadline_at", None)
            if not deadline_at:
                created = _parse(str(getattr(entry, "created_at")))
                seconds = max(1, int(getattr(budget, "time_budget_seconds", 1)))
                deadline_at = (created + timedelta(seconds=seconds)).isoformat()
            candidates.append(
                _ScheduleView(
                    plan_id=str(getattr(entry, "id")),
                    work_class=work_class,
                    created_at=str(getattr(entry, "created_at")),
                    deadline_at=str(deadline_at),
                )
            )
        return self.choose(candidates, now=now)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("scheduler timestamps must include timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class _ScheduleView:
    plan_id: str
    work_class: str
    created_at: str
    deadline_at: str
    state: str = "ready"
    terminal: bool = False


@dataclass(frozen=True)
class PreemptionReceipt:
    latency_ms: float
    budget_ms: float
    within_budget: bool
    task_stopped: bool


class DualLoopCoordinator:
    """Let reactive user work preempt, but never share, background inference."""

    def __init__(self, *, preemption_budget_seconds: float = 0.25) -> None:
        self.preemption_budget_seconds = max(
            0.01, min(5.0, float(preemption_budget_seconds))
        )
        self._deliberative_task: asyncio.Task | None = None
        self._reactive_lock = asyncio.Lock()

    @property
    def deliberating(self) -> bool:
        return (
            self._deliberative_task is not None and not self._deliberative_task.done()
        )

    def start_deliberation(self, operation: Awaitable[_T]) -> asyncio.Task[_T]:
        if self.deliberating:
            if inspect_coroutine := getattr(operation, "close", None):
                inspect_coroutine()
            raise RuntimeError("a deliberative operation is already active")
        task = asyncio.create_task(operation, name="deliberative-plan-pass")
        self._deliberative_task = task

        def clear(completed: asyncio.Task) -> None:
            if self._deliberative_task is completed:
                self._deliberative_task = None
            if not completed.cancelled():
                try:
                    completed.exception()
                except asyncio.CancelledError:
                    pass

        task.add_done_callback(clear)
        return task

    async def preempt_for_human(self) -> PreemptionReceipt:
        task = self._deliberative_task
        budget_ms = self.preemption_budget_seconds * 1000.0
        if task is None or task.done():
            return PreemptionReceipt(0.0, budget_ms, True, True)
        loop = asyncio.get_running_loop()
        started = loop.time()
        task.cancel()
        done, _pending = await asyncio.wait(
            {task}, timeout=self.preemption_budget_seconds
        )
        latency_ms = (loop.time() - started) * 1000.0
        stopped = task in done
        return PreemptionReceipt(
            latency_ms=latency_ms,
            budget_ms=budget_ms,
            within_budget=stopped and latency_ms <= budget_ms * 1.1,
            task_stopped=stopped,
        )

    async def run_reactive(
        self, operation: Callable[[], Awaitable[_T]]
    ) -> tuple[_T, PreemptionReceipt]:
        receipt = await self.preempt_for_human()
        async with self._reactive_lock:
            return await operation(), receipt

    async def close(self) -> None:
        task = self._deliberative_task
        if task is None or task.done():
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
