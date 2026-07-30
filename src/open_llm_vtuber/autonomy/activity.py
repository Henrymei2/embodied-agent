"""Execution of validated agenda steps with checkpointing and visible pacing."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import asdict
from typing import Awaitable, Callable

from .agenda import AgendaEntry, AgendaStore, ExecutionCheckpoint


class ActivityStepTimeout(TimeoutError):
    """A typed, descriptive timeout that preserves the affected step."""


class ActivityExecutor:
    """Runs one typed step through the existing tool-capable agent pipeline.

    Tool results are consumed by the agent as soon as they arrive.  Any remaining
    plausible duration is applied only after reasoning completes, so cancellation
    never hides a result from the executive.
    """

    def __init__(
        self,
        store: AgendaStore,
        run_step: Callable[[AgendaEntry, ExecutionCheckpoint], Awaitable[str]],
        emit: Callable[[dict], Awaitable[None]],
        tracer=None,
    ) -> None:
        self.store = store
        self.run_step = run_step
        self.emit = emit
        self.tracer = tracer

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
        started = asyncio.get_running_loop().time()
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
            with trace:
                result = await asyncio.wait_for(
                    self.run_step(entry, checkpoint),
                    timeout=checkpoint.timeout_seconds,
                )
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
            checkpoint.state = "uncertain" if checkpoint.side_effecting else "planned"
            self.store.transition(
                entry.id,
                "suspended",
                progress_summary="Interrupted at a safe activity boundary.",
                checkpoint=checkpoint,
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

        checkpoint.state = "result_ready"
        checkpoint.result_summary = result[:1000]
        self.store.transition(entry.id, "active", checkpoint=checkpoint)

        elapsed = asyncio.get_running_loop().time() - started
        remaining = checkpoint.plausible_duration_seconds - elapsed
        try:
            if remaining > 0:
                await asyncio.sleep(remaining)
        except asyncio.CancelledError:
            # The tool result is already known and was consumed by the agent.
            # Preserve that safe boundary instead of marking a completed side
            # effect uncertain or inviting an automatic replay.
            checkpoint.state = "completed"
            self.store.transition(
                entry.id,
                "suspended",
                progress_summary=(
                    "Step completed before interruption: "
                    f"{checkpoint.result_summary or 'result recorded'}"
                ),
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
