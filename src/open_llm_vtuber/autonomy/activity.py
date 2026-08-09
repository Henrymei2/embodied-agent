"""Execution of validated agenda steps with checkpointing and visible pacing."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Awaitable, Callable

from .agenda import AgendaEntry, AgendaStore, ExecutionCheckpoint
from .action_context import AgendaApprovalRequired


class ActivityStepTimeout(TimeoutError):
    """A typed, descriptive timeout that preserves the affected step."""


@dataclass(frozen=True)
class ActivityRunResult:
    """A completed queued Agent turn and its content-free timing breakdown."""

    summary: str
    queue_wait_seconds: float
    execution_seconds: float


class ActivityExecutor:
    """Runs one typed step through the existing tool-capable agent pipeline.

    Tool results are consumed by the agent as soon as they arrive.  Any remaining
    plausible duration is applied only after reasoning completes, so cancellation
    never hides a result from the executive.
    """

    def __init__(
        self,
        store: AgendaStore,
        run_step: Callable[
            [AgendaEntry, ExecutionCheckpoint],
            Awaitable[str | ActivityRunResult],
        ],
        emit: Callable[[dict], Awaitable[None]],
        tracer=None,
        max_plausible_duration_seconds: float = 0.0,
        run_step_enforces_timeout: bool = False,
    ) -> None:
        self.store = store
        self.run_step = run_step
        self.emit = emit
        self.tracer = tracer
        self.max_plausible_duration_seconds = max(
            0.0, min(60.0, float(max_plausible_duration_seconds))
        )
        self.run_step_enforces_timeout = run_step_enforces_timeout

    async def execute(self, entry: AgendaEntry, checkpoint: ExecutionCheckpoint) -> str:
        checkpoint.state = "running"
        self.store.transition(entry.id, "active", checkpoint=checkpoint)
        await self.emit(
            {
                "type": "agenda-step-updated",
                "version": 1,
                "entry_id": entry.id,
                "checkpoint": asdict(checkpoint),
            }
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        requested_pacing = checkpoint.plausible_duration_seconds
        applied_pacing = min(
            requested_pacing,
            self.max_plausible_duration_seconds,
        )
        if entry.budget.deadline_at:
            try:
                deadline = datetime.fromisoformat(entry.budget.deadline_at)
                remaining_budget = max(
                    0.0,
                    (deadline - datetime.now(deadline.tzinfo)).total_seconds(),
                )
                applied_pacing = min(applied_pacing, remaining_budget)
            except (TypeError, ValueError):
                pass
        queue_wait_seconds = 0.0
        execution_seconds = 0.0
        result_ready = False
        span = None
        try:
            trace = (
                self.tracer.span(
                    "agenda.activity.step",
                    trace_id=entry.metadata.get("trace_id"),
                    attributes={
                        "agenda.entry_id": entry.id,
                        "agenda.step_id": checkpoint.step_id,
                        "agenda.step_kind": checkpoint.kind,
                        "agenda.capability": checkpoint.required_capability,
                        "agenda.side_effecting": checkpoint.side_effecting,
                    },
                )
                if self.tracer is not None
                else nullcontext()
            )
            with trace as span:
                try:
                    operation = self.run_step(entry, checkpoint)
                    run_result = (
                        await operation
                        if self.run_step_enforces_timeout
                        else await asyncio.wait_for(
                            operation, timeout=checkpoint.timeout_seconds
                        )
                    )
                except asyncio.TimeoutError as exc:
                    if span is not None:
                        span.set_attribute(
                            "agenda.queue_wait_seconds",
                            round(float(getattr(exc, "queue_wait_seconds", 0.0)), 6),
                        )
                        span.set_attribute(
                            "agenda.execution_seconds",
                            round(float(getattr(exc, "execution_seconds", 0.0)), 6),
                        )
                        span.set_attribute(
                            "agenda.requested_pacing_seconds", requested_pacing
                        )
                        span.set_attribute(
                            "agenda.applied_pacing_seconds", applied_pacing
                        )
                        span.set_attribute("agenda.pacing_sleep_seconds", 0.0)
                        span.set_attribute(
                            "agenda.execution_timeout_seconds",
                            checkpoint.timeout_seconds,
                        )
                        span.set_attribute(
                            "agenda.timeout_stage",
                            getattr(exc, "timeout_stage", "execution"),
                        )
                    raise
                if isinstance(run_result, ActivityRunResult):
                    result = run_result.summary
                    queue_wait_seconds = run_result.queue_wait_seconds
                    execution_seconds = run_result.execution_seconds
                else:
                    result = run_result
                    execution_seconds = loop.time() - started
                if span is not None:
                    span.set_attribute(
                        "agenda.queue_wait_seconds", round(queue_wait_seconds, 6)
                    )
                    span.set_attribute(
                        "agenda.execution_seconds", round(execution_seconds, 6)
                    )
                    span.set_attribute(
                        "agenda.requested_pacing_seconds", requested_pacing
                    )
                    span.set_attribute("agenda.applied_pacing_seconds", applied_pacing)
                    span.set_attribute(
                        "agenda.execution_timeout_seconds",
                        checkpoint.timeout_seconds,
                    )
                checkpoint.state = "result_ready"
                checkpoint.result_summary = result[:1000]
                self.store.transition(entry.id, "active", checkpoint=checkpoint)
                result_ready = True

                remaining = max(0.0, applied_pacing - execution_seconds)
                actual_pacing_sleep = 0.0
                if remaining > 0:
                    pacing_started = loop.time()
                    try:
                        await asyncio.sleep(remaining)
                    except asyncio.CancelledError:
                        if span is not None:
                            span.set_attribute(
                                "agenda.pacing_sleep_seconds",
                                round(loop.time() - pacing_started, 6),
                            )
                        raise
                    actual_pacing_sleep = loop.time() - pacing_started
                if span is not None:
                    span.set_attribute(
                        "agenda.pacing_sleep_seconds",
                        round(actual_pacing_sleep, 6),
                    )
        except AgendaApprovalRequired as exc:
            checkpoint.state = "planned"
            checkpoint.result_summary = str(exc)[:1000]
            scope = exc.scope
            self.store.transition(entry.id, "active", checkpoint=checkpoint)
            self.store.block_for_authorization(
                entry.id,
                transaction_id=str(scope.pending_transaction_id),
                capability=scope.capability or "external_action",
                summary=scope.summary,
            )
            raise
        except asyncio.TimeoutError as exc:
            checkpoint.state = "uncertain" if checkpoint.side_effecting else "planned"
            checkpoint.result_summary = (
                f"Step timed out after {checkpoint.timeout_seconds} seconds."
            )
            self.store.transition(
                entry.id,
                "suspended" if checkpoint.side_effecting else "queued",
                progress_summary=checkpoint.result_summary,
                checkpoint=checkpoint,
            )
            raise ActivityStepTimeout(checkpoint.result_summary) from exc
        except asyncio.CancelledError:
            if result_ready:
                checkpoint.state = "completed"
                progress_summary = (
                    "Step completed before interruption: "
                    f"{checkpoint.result_summary or 'result recorded'}"
                )
            else:
                checkpoint.state = (
                    "uncertain" if checkpoint.side_effecting else "planned"
                )
                progress_summary = "Interrupted at a safe activity boundary."
            self.store.transition(
                entry.id,
                "suspended",
                progress_summary=progress_summary,
                checkpoint=checkpoint,
            )
            if result_ready:
                await self.emit(
                    {
                        "type": "agenda-step-updated",
                        "version": 1,
                        "entry_id": entry.id,
                        "checkpoint": asdict(checkpoint),
                    }
                )
            raise
        except Exception as exc:
            checkpoint.state = "uncertain" if checkpoint.side_effecting else "planned"
            checkpoint.result_summary = str(exc)[:500]
            self.store.transition(
                entry.id,
                "suspended",
                progress_summary=f"Step failed: {exc}",
                checkpoint=checkpoint,
            )
            raise

        checkpoint.state = "completed"
        self.store.transition(
            entry.id,
            "active",
            progress_summary=checkpoint.result_summary,
            checkpoint=checkpoint,
        )
        await self.emit(
            {
                "type": "agenda-step-updated",
                "version": 1,
                "entry_id": entry.id,
                "checkpoint": asdict(checkpoint),
            }
        )
        return result
