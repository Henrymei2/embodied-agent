"""Lifecycle owner for the continuous global agenda."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from loguru import logger

from .activity import ActivityExecutor, ActivityStepTimeout
from .agenda import (
    AgendaEntry,
    AgendaStore,
    ProposalRejection,
    create_entry,
    utc_now,
)
from .agenda_context import build_agenda_context
from .agenda_planner import StructuredAgendaPlanner
from .agenda_policy import AgendaPolicy
from .settings import AutonomySettings


class AgendaExecutive:
    def __init__(
        self,
        *,
        client_uid: str,
        store: AgendaStore,
        planner: StructuredAgendaPlanner,
        settings: AutonomySettings,
        run_step: Callable[..., Awaitable[str]],
        emit: Callable[[dict], Awaitable[None]],
        capabilities: Optional[set[str]] = None,
        object_names: Optional[set[str]] = None,
    ) -> None:
        self.client_uid = client_uid
        self.store = store
        self.planner = planner
        self.settings = settings
        self.emit = emit
        self.capabilities = capabilities or set()
        self.object_names = object_names or set()
        self.policy = AgendaPolicy(settings.maximum_pursuit_seconds)
        self.activity = ActivityExecutor(store, run_step, emit)
        self._wake = asyncio.Event()
        self._activity_task: Optional[asyncio.Task] = None
        self._memory_digest_writer: Optional[Callable[[str], bool]] = None

    async def run(self) -> None:
        recovered = self.store.recover_interrupted()
        if recovered:
            logger.info(f"agenda: recovered {len(recovered)} interrupted entries")
        await self.broadcast_snapshot()
        while True:
            try:
                if self._memory_digest_writer is not None:
                    self.store.compact_terminal_history(
                        self._memory_digest_writer,
                        retention_hours=self.settings.terminal_history_hours,
                        retention_entries=self.settings.terminal_history_entries,
                    )
                progressed = await self._advance()
                if progressed:
                    # Completing, queueing, or generating an entry must lead
                    # directly to the next lifecycle pass. Waiting here breaks
                    # the continuous-chain invariant and makes the agenda look
                    # idle for the full fallback cadence.
                    continue
                self._wake.clear()
                try:
                    await asyncio.wait_for(
                        self._wake.wait(),
                        timeout=self._next_wake_seconds(),
                    )
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                if self._activity_task and not self._activity_task.done():
                    self._activity_task.cancel()
                raise
            except ActivityStepTimeout as exc:
                logger.warning(f"agenda: {exc}")
                await self.broadcast_snapshot()
                await asyncio.sleep(self.settings.planner_retry_backoff_seconds)
            except Exception as exc:
                logger.exception(
                    f"agenda: executive pass failed: "
                    f"{type(exc).__name__}: {exc or '<no message>'}"
                )
                await asyncio.sleep(self.settings.planner_retry_backoff_seconds)

    def _next_wake_seconds(self) -> float:
        timeout = float(self.settings.fallback_reconsideration_seconds)
        now = utc_now()
        for entry in self.store.load().entries:
            if not entry.schedule or entry.status not in {"queued", "suspended"}:
                continue
            due = datetime.fromisoformat(entry.schedule.next_run)
            if due.tzinfo is None:
                due = due.replace(tzinfo=now.tzinfo)
            timeout = min(timeout, max(0.1, (due - now).total_seconds()))
        return max(0.1, timeout)

    def wake(self) -> None:
        self._wake.set()

    def set_memory_digest_writer(self, writer: Callable[[str], bool]) -> None:
        self._memory_digest_writer = writer

    async def broadcast_snapshot(self) -> None:
        await self.emit(
            {
                "type": "agenda-snapshot",
                "agenda": self.store.snapshot(),
            }
        )

    async def preempt_for_user(self, title: str) -> AgendaEntry:
        if self._activity_task and not self._activity_task.done():
            self._activity_task.cancel()
        state = self.store.load()
        for active in state.entries:
            if active.status == "active":
                self.store.transition(
                    active.id,
                    "suspended",
                    progress_summary="Suspended for a user request.",
                )
        user = create_entry(
            kind="user",
            title=title[:160] or "Help the user",
            description="Foreground user-authored conversation task.",
            origin="user",
            evidence_refs=[],
            completion_condition="The conversation turn finishes.",
            time_budget_seconds=self.settings.maximum_pursuit_seconds,
            status="active",
            report_policy="on_completion",
        )
        self.store.add(user)
        await self.broadcast_snapshot()
        return user

    async def finish_user(self, entry_id: str, *, failed: bool = False) -> None:
        self.store.transition(entry_id, "failed" if failed else "completed")
        await self._reconsider_suspended()
        self.wake()
        await self.broadcast_snapshot()

    async def pause(self, paused: bool) -> None:
        if paused and self._activity_task and not self._activity_task.done():
            self._activity_task.cancel()
        self.store.pause(paused)
        self.wake()
        await self.emit(
            {"type": "agenda-paused" if paused else "agenda-resumed", "version": 1}
        )
        await self.broadcast_snapshot()

    async def abandon(self, entry_id: str) -> None:
        state = self.store.load()
        entry = next(item for item in state.entries if item.id == entry_id)
        if entry.status == "active" and self._activity_task:
            self._activity_task.cancel()
        self.store.transition(entry_id, "abandoned", progress_summary="Stopped by user.")
        self.wake()
        await self.broadcast_snapshot()

    async def _advance(self) -> bool:
        state = self.store.load()
        due = self.store.claim_due()
        if due:
            state = self.store.load()
        active = next((item for item in state.entries if item.status == "active"), None)
        if active:
            if active.kind != "user":
                await self._advance_entry(active)
                return True
            return False

        queued = sorted(
            (
                item
                for item in state.entries
                if item.status == "queued" and item.schedule is None
            ),
            key=lambda item: (
                item.schedule.priority if item.schedule else 10,
                item.queue_position,
                item.created_at,
            ),
        )
        if queued:
            entry = self.store.transition(queued[0].id, "active")
            await self.broadcast_snapshot()
            await self._advance_entry(entry)
            return True
        if any(
            item.status in {"proposed", "blocked_approval"} for item in state.entries
        ):
            # Exactly one generated successor is retained. Do not generate
            # additional work while that successor awaits user approval.
            return False
        suspended = [
            item
            for item in state.entries
            if item.status == "suspended" and item.origin == "autonomous"
        ]
        if suspended:
            await self._reconsider_suspended()
            return True
        if state.paused:
            return False
        await self._generate_successor()
        return True

    async def _generate_successor(self) -> None:
        context = build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
        )
        for _ in range(self.settings.planner_retry_limit):
            proposal = await self.planner.propose_intention(context)
            state = self.store.load()
            verdict = self.policy.validate(
                proposal,
                supplied_evidence_refs=context.evidence_refs,
                capabilities=context.capabilities,
                object_names=context.object_names,
                comparison_entries=[
                    item
                    for item in state.entries
                    if item.status not in {"failed", "abandoned"}
                ],
            )
            if verdict.accepted:
                proposal.status = (
                    "blocked_approval" if proposal.requires_approval else "queued"
                )
                if proposal.requires_approval:
                    proposal.plan_id = self._draft_approval_plan(proposal)
                self.store.add(proposal)
                logger.info(
                    f"agenda: accepted {proposal.kind} proposal "
                    f"{proposal.id!r}: {proposal.title!r}"
                )
                await self.broadcast_snapshot()
                return

            def reject(current):
                current.rejections.append(
                    ProposalRejection(
                        created_at=utc_now().isoformat(),
                        title=proposal.title,
                        reason=verdict.reason,
                    )
                )
                current.rejections = current.rejections[-50:]

            self.store.mutate(reject)
            logger.warning(
                f"agenda: rejected proposal {proposal.title!r}: {verdict.reason}"
            )
            await asyncio.sleep(self.settings.planner_retry_backoff_seconds)

        idle = create_entry(
            kind="idle",
            title="Pause and reconsider",
            description="Remain available before another grounded planning pass.",
            origin="recovery",
            evidence_refs=["system:proposal-retries-exhausted"],
            completion_condition="Remain available until reconsideration.",
            time_budget_seconds=self.settings.idle_min_seconds,
            status="queued",
        )
        self.store.add(idle)
        logger.warning(
            "agenda: proposal retry limit exhausted; queued bounded reconsideration"
        )
        self.store.mutate(
            lambda state: setattr(
                state,
                "next_reconsideration_at",
                (utc_now() + timedelta(seconds=self.settings.idle_min_seconds)).isoformat(),
            )
        )
        await self.broadcast_snapshot()

    @staticmethod
    def _draft_approval_plan(proposal: AgendaEntry) -> str:
        import json

        from mcp_servers.plan_tools import write_plan

        result = write_plan(
            proposal.title,
            proposal.description,
            [
                "Approve this agenda intention at the writing desk.",
                proposal.completion_condition.description,
            ],
            f"Risk: {proposal.risk}. Agenda entry: {proposal.id}",
        )
        payload = json.loads(result)
        if not payload.get("ok"):
            raise ValueError("could not create the required approval plan")
        return str(payload["plan"]["plan_id"])

    async def _advance_entry(self, entry: AgendaEntry) -> None:
        deadline = entry.budget.deadline_at
        if deadline and utc_now() >= datetime.fromisoformat(deadline):
            self.store.transition(entry.id, "failed", progress_summary="Time budget expired.")
            await self.broadcast_snapshot()
            return
        if entry.kind == "idle":
            await asyncio.sleep(min(entry.budget.time_budget_seconds, self.settings.idle_max_seconds))
            self.store.transition(entry.id, "completed")
            await self.broadcast_snapshot()
            return

        context = build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
        )
        checkpoint = await self.planner.next_step(asdict(entry), context)
        # The configured timeout is the operational minimum for an LLM/tool
        # round trip. Models may request more time for a step, but may not
        # accidentally cancel a healthy provider request after only a few
        # seconds. The entry deadline remains the outer wall-clock bound.
        checkpoint.timeout_seconds = max(
            checkpoint.timeout_seconds,
            self.settings.step_timeout_seconds,
        )
        if deadline:
            remaining_budget = max(
                1, int((datetime.fromisoformat(deadline) - utc_now()).total_seconds())
            )
            checkpoint.timeout_seconds = min(
                checkpoint.timeout_seconds,
                remaining_budget,
            )
        self._activity_task = asyncio.create_task(self.activity.execute(entry, checkpoint))
        result = await self._activity_task
        complete, summary = await self.planner.evaluate_completion(
            asdict(entry), [result]
        )
        self.store.transition(
            entry.id,
            "completed" if complete else "queued",
            progress_summary=summary,
        )
        await self.broadcast_snapshot()
        if complete:
            self.wake()

    async def _reconsider_suspended(self) -> None:
        state = self.store.load()
        candidates = [
            entry
            for entry in state.entries
            if entry.status == "suspended" and entry.origin == "autonomous"
        ]
        if not candidates:
            return
        entry = candidates[-1]
        context = build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
        )
        decision = await self.planner.reconsider(asdict(entry), context)
        target = {"resume": "queued", "queue": "queued", "abandon": "abandoned"}[decision]
        self.store.transition(entry.id, target)


_executives: dict[str, AgendaExecutive] = {}


def register_executive(client_uid: str, executive: AgendaExecutive) -> None:
    _executives[client_uid] = executive


def get_executive(client_uid: str) -> Optional[AgendaExecutive]:
    return _executives.get(client_uid)


def get_connected_executive() -> Optional[AgendaExecutive]:
    return next(iter(_executives.values()), None)


def unregister_executive(client_uid: str) -> None:
    _executives.pop(client_uid, None)
