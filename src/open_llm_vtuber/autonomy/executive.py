"""Lifecycle owner for the continuous global agenda."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Optional

from loguru import logger

from ..conversations.agent_activity import AgentActivityTracker
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
from .idle_relocation import (
    IdlePlacement,
    IdleScene,
    validate_idle_placement,
)
from .settings import AutonomySettings

RoomSnapshot = Callable[[], IdleScene]
IdleRelocator = Callable[[IdlePlacement], Awaitable[bool]]
CompletionReporter = Callable[[AgendaEntry, str], Awaitable[None]]


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
        room_snapshot: Optional[RoomSnapshot] = None,
        relocate_idle: Optional[IdleRelocator] = None,
        report_completion: Optional[CompletionReporter] = None,
    ) -> None:
        self.client_uid = client_uid
        self.store = store
        self.planner = planner
        self.settings = settings
        self.emit = emit
        self.capabilities = capabilities or set()
        self.object_names = object_names or set()
        self.room_snapshot = room_snapshot
        self.relocate_idle = relocate_idle
        self.report_completion = report_completion
        self.policy = AgendaPolicy(settings.maximum_pursuit_seconds)
        self.activity = ActivityExecutor(store, run_step, emit)
        self._wake = asyncio.Event()
        self._activity_task: Optional[asyncio.Task] = None
        self._activity_interrupted = False
        self._settling_task: Optional[asyncio.Task] = None
        self._settling_interrupted = False
        self._settled_hold_key: Optional[str] = None
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
                if self._settling_task and not self._settling_task.done():
                    self._settling_task.cancel()
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

    def _interrupt_activity(self) -> None:
        if self._activity_task and not self._activity_task.done():
            self._activity_interrupted = True
            self._activity_task.cancel()

    def _interrupt_settling(self) -> None:
        if self._settling_task and not self._settling_task.done():
            self._settling_interrupted = True
            self._settling_task.cancel()

    async def broadcast_snapshot(self) -> None:
        await self.emit(
            {
                "type": "agenda-snapshot",
                "agenda": self.store.snapshot(),
            }
        )

    async def preempt_for_user(self, title: str) -> AgendaEntry:
        self._interrupt_activity()
        self._interrupt_settling()
        self._settled_hold_key = None
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
        if paused:
            self._interrupt_activity()
            self._interrupt_settling()
        if not paused:
            self._settled_hold_key = None
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
            self._interrupt_activity()
        self.store.transition(
            entry_id, "abandoned", progress_summary="Stopped by user."
        )
        self.wake()
        await self.broadcast_snapshot()

    async def _advance(self) -> bool:
        state = self.store.load()
        active = next((item for item in state.entries if item.status == "active"), None)
        if state.paused:
            if active and active.kind == "user":
                return False
            return not await self._settle_for_hold("paused")

        due = self.store.claim_due()
        if due:
            state = self.store.load()
        active = next((item for item in state.entries if item.status == "active"), None)
        if active:
            self._settled_hold_key = None
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
            self._settled_hold_key = None
            entry = self.store.transition(queued[0].id, "active")
            await self.broadcast_snapshot()
            await self._advance_entry(entry)
            return True
        held = [
            item
            for item in state.entries
            if item.status in {"proposed", "blocked_approval"}
        ]
        if held:
            # Exactly one generated successor is retained. Do not generate
            # additional work while that successor awaits user approval.
            key = "held:" + ",".join(
                f"{item.id}:{item.status}" for item in sorted(held, key=lambda x: x.id)
            )
            return not await self._settle_for_hold(key)
        suspended = [
            item
            for item in state.entries
            if item.status == "suspended" and item.origin == "autonomous"
        ]
        if suspended:
            self._settled_hold_key = None
            await self._reconsider_suspended()
            return True
        self._settled_hold_key = None
        await self._settle_while_generating_successor()
        return True

    def _agenda_context(self):
        return build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
        )

    def _room_snapshot(self) -> IdleScene:
        if self.room_snapshot is None:
            return IdleScene(current_position=None, objects=())
        try:
            return self.room_snapshot()
        except Exception as exc:
            logger.warning(f"agenda: could not inspect room for idle relocation: {exc}")
            return IdleScene(current_position=None, objects=())

    async def _choose_idle_placement(
        self,
        context,
        scene: IdleScene,
    ) -> IdlePlacement:
        if not scene.objects:
            return IdlePlacement(None, "No room objects are available.")
        try:
            placement = await self.planner.choose_idle_placement(context, scene)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"agenda: idle placement planner failed: {exc}")
            placement = IdlePlacement(None, "The idle placement planner failed.")
        return validate_idle_placement(placement, scene)

    async def _relocate_idle_safely(self, placement: IdlePlacement) -> None:
        if placement.object_id is None or self.relocate_idle is None:
            return
        try:
            await self.relocate_idle(placement)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f"agenda: idle relocation failed without blocking work: {exc}"
            )

    async def _run_settling_task(self, operation: Awaitable[None]) -> bool:
        task = asyncio.create_task(operation)
        self._settling_task = task
        self._settling_interrupted = False
        try:
            await task
            return True
        except asyncio.CancelledError:
            # User preemption cancels this child task, while shutting down the
            # agenda cancels the current task itself. Only swallow the former.
            if not self._settling_interrupted:
                raise
            logger.info("agenda: idle relocation was preempted")
            return False
        finally:
            if self._settling_task is task:
                self._settling_task = None
                self._settling_interrupted = False

    async def _settle_for_hold(self, key: str) -> bool:
        if self._settled_hold_key == key:
            return True
        self._settled_hold_key = key
        context = self._agenda_context()
        scene = self._room_snapshot()

        async def settle() -> None:
            placement = await self._choose_idle_placement(context, scene)
            await self._relocate_idle_safely(placement)

        return await self._run_settling_task(settle())

    async def _settle_while_generating_successor(self) -> None:
        context = self._agenda_context()
        scene = self._room_snapshot()

        async def settle_and_generate() -> None:
            await self.emit(AgentActivityTracker.planning())
            try:
                placement = await self._choose_idle_placement(context, scene)
                relocation = asyncio.create_task(self._relocate_idle_safely(placement))
                generation = asyncio.create_task(self._generate_successor(context))
                try:
                    await asyncio.gather(relocation, generation)
                except BaseException:
                    for task in (relocation, generation):
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(relocation, generation, return_exceptions=True)
                    raise
            finally:
                # A user turn owns the status display after preemption and must
                # not be cleared by the background planner being canceled.
                if not self._settling_interrupted:
                    await self.emit(AgentActivityTracker.idle())

        await self._run_settling_task(settle_and_generate())
        state = self.store.load()
        held = [
            item
            for item in state.entries
            if item.status in {"proposed", "blocked_approval"}
        ]
        if held:
            self._settled_hold_key = "held:" + ",".join(
                f"{item.id}:{item.status}" for item in sorted(held, key=lambda x: x.id)
            )

    async def _generate_successor(self, context=None) -> None:
        context = context or self._agenda_context()
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
                (
                    utc_now() + timedelta(seconds=self.settings.idle_min_seconds)
                ).isoformat(),
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
            self.store.transition(
                entry.id, "failed", progress_summary="Time budget expired."
            )
            await self.broadcast_snapshot()
            return
        if entry.kind == "idle":
            await asyncio.sleep(
                min(entry.budget.time_budget_seconds, self.settings.idle_max_seconds)
            )
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
        self._activity_task = asyncio.create_task(
            self.activity.execute(entry, checkpoint)
        )
        self._activity_interrupted = False
        try:
            result = await self._activity_task
        except asyncio.CancelledError:
            if not self._activity_interrupted:
                raise
            logger.info(f"agenda: activity for {entry.id!r} was preempted")
            return
        finally:
            self._activity_task = None
            self._activity_interrupted = False
        complete, summary = await self.planner.evaluate_completion(
            asdict(entry), [result]
        )
        updated = self.store.transition(
            entry.id,
            "completed" if complete else "queued",
            progress_summary=summary,
        )
        await self.broadcast_snapshot()
        if complete:
            await self._report_completed_entry(updated, summary, context)
            self.wake()

    async def _report_completed_entry(
        self,
        entry: AgendaEntry,
        summary: str,
        context,
    ) -> None:
        if self.report_completion is None or entry.report_policy == "silent":
            return
        should_report = entry.report_policy in {"on_completion", "ask_first"}
        if entry.report_policy == "if_noteworthy":
            try:
                should_report = await self.planner.is_noteworthy(
                    asdict(entry),
                    summary,
                    context,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"agenda: could not evaluate report worthiness for {entry.id!r}: "
                    f"{exc}"
                )
                should_report = False
        if not should_report:
            return
        try:
            await self.report_completion(entry, summary)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                f"agenda: could not publish completion for {entry.id!r}: {exc}"
            )

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
        target = {"resume": "queued", "queue": "queued", "abandon": "abandoned"}[
            decision
        ]
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
