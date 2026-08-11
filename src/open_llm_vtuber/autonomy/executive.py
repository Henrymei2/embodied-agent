"""Lifecycle owner for the continuous global agenda."""

from __future__ import annotations

import asyncio
import copy
import time
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
    TERMINAL_STATUSES,
    create_entry,
    utc_now,
)
from .agenda_context import build_agenda_context
from .agenda_planner import StructuredAgendaPlanner
from .agenda_policy import AgendaPolicy
from .agenda_plan_bridge import AgendaPlanBridge, reconcile_cancelling_plan
from .dual_loop import FairPlanScheduler
from .plan_state import PlanConflictError
from .idle_relocation import (
    IdlePlacement,
    IdleScene,
    validate_idle_placement,
)
from .lease import LeaseHandle, LeaseLostError, WorkspaceLease, lease_context
from .settings import AutonomySettings
from .step_policy import StepPolicy
from .telemetry import AutonomyTracer
from .triggers import trigger_handling_policy, trigger_from_event

RoomSnapshot = Callable[[], IdleScene]
IdleRelocator = Callable[[IdlePlacement], Awaitable[bool]]


class PlannerQuotaExceeded(RuntimeError):
    def __init__(self, reset_at: str) -> None:
        super().__init__(f"planner quota exhausted until {reset_at}")
        self.reset_at = reset_at


class PlannerCallTimeout(TimeoutError):
    """A planner model call exceeded its bounded execution window."""

    def __init__(self, phase: str, timeout_seconds: float) -> None:
        super().__init__(f"planner {phase} timed out after {timeout_seconds:g} seconds")
        self.phase = phase
        self.timeout_seconds = timeout_seconds


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
        run_intention: Optional[Callable[[AgendaEntry], Awaitable[object]]] = None,
        discard_intention: Optional[Callable[[str], None]] = None,
        capabilities: Optional[set[str]] = None,
        object_names: Optional[set[str]] = None,
        capability_objects: Optional[dict[str, str]] = None,
        tool_schemas: Optional[dict[str, dict]] = None,
        report_result: Optional[Callable[[AgendaEntry, str], Awaitable[object]]] = None,
        connected_client: bool = True,
        lease_handle: LeaseHandle | None = None,
        room_snapshot: Optional[RoomSnapshot] = None,
        relocate_idle: Optional[IdleRelocator] = None,
        run_step_enforces_timeout: bool = False,
    ) -> None:
        self.client_uid = client_uid
        self.store = store
        self.planner = planner
        self.settings = settings
        self.emit = emit
        self.run_intention = run_intention
        self.discard_intention = discard_intention
        self.capabilities = capabilities or set()
        self.object_names = object_names or set()
        self.capability_objects = capability_objects or {}
        self.tool_schemas = dict(tool_schemas or {})
        self.connected_client = connected_client
        self.lease_handle = lease_handle
        self.room_snapshot = room_snapshot
        self.relocate_idle = relocate_idle
        self.policy = AgendaPolicy(settings.maximum_pursuit_seconds)
        self.step_policy = StepPolicy(
            capabilities=self.capabilities,
            object_names=self.object_names,
            capability_objects=self.capability_objects,
            tool_schemas=self.tool_schemas,
            connected_client=connected_client,
        )
        self.tracer = AutonomyTracer(
            store.root,
            max_bytes=settings.trace_max_bytes,
        )
        self.activity = ActivityExecutor(
            store,
            run_step,
            emit,
            tracer=self.tracer,
            max_plausible_duration_seconds=(settings.max_plausible_duration_seconds),
            run_step_enforces_timeout=run_step_enforces_timeout,
        )
        self.plan_bridge = AgendaPlanBridge(
            store,
            self.activity,
            lease_seconds=settings.lease_ttl_seconds,
            retry_backoff_seconds=settings.retry_backoff_seconds,
            max_retry_backoff_seconds=settings.max_retry_backoff_seconds,
        )
        self.fair_scheduler = FairPlanScheduler(
            starvation_seconds=max(60.0, settings.fallback_reconsideration_seconds)
        )
        self._wake = asyncio.Event()
        self._activity_task: Optional[asyncio.Task] = None
        self._entry_task: Optional[asyncio.Task] = None
        self._entry_task_entry_id: Optional[str] = None
        self._reconsider_task: Optional[asyncio.Task] = None
        self._reconsider_task_entry_id: Optional[str] = None
        self._detached_planner_tasks: set[asyncio.Task] = set()
        self._settling_task: Optional[asyncio.Task] = None
        self._settling_interrupted = False
        self._report_result = report_result

    async def run(self) -> None:
        from .change_bus import subscribe_agenda_changes, unsubscribe_agenda_changes

        subscription = subscribe_agenda_changes(self.store.root, self.wake)
        try:
            recovered = self.store.recover_interrupted()
            recovered_plans = self.plan_bridge.store.recover_expired()
            reconciliation_fence = (
                self.lease_handle.fencing_token
                if self.lease_handle is not None
                else self.store.load().fencing_token
            )
            reconciled_plans = self.plan_bridge.reconcile_startup(
                fencing_token=reconciliation_fence
            )
            if recovered:
                logger.info(f"agenda: recovered {len(recovered)} interrupted entries")
                if self.settings.restart_recovery_grace_seconds:
                    await asyncio.sleep(self.settings.restart_recovery_grace_seconds)
            if recovered_plans:
                logger.info(
                    f"agenda: recovered {len(recovered_plans)} expired plan leases"
                )
            if any(reconciled_plans.values()):
                logger.info(
                    f"agenda: reconciled resumable plan handoffs {reconciled_plans}"
                )
            await self.broadcast_snapshot()
            while True:
                try:
                    self.store.prune_terminal_history(
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
                    await self._cancel_reconsideration()
                    if self._entry_task and not self._entry_task.done():
                        self._entry_task.cancel()
                    if self._activity_task and not self._activity_task.done():
                        self._activity_task.cancel()
                    if self._settling_task and not self._settling_task.done():
                        self._settling_task.cancel()
                    await asyncio.gather(
                        *(
                            task
                            for task in (
                                self._entry_task,
                                self._activity_task,
                                self._settling_task,
                            )
                            if task is not None
                        ),
                        return_exceptions=True,
                    )
                    self._release_event_claims(
                        "Agenda ownership changed during successor planning."
                    )
                    raise
                except LeaseLostError:
                    raise
                except ActivityStepTimeout as exc:
                    logger.warning(f"agenda: {exc}")
                    await self.broadcast_snapshot()
                    await asyncio.sleep(self.settings.planner_retry_backoff_seconds)
                except Exception as exc:
                    self._release_event_claims(
                        f"Successor planning was interrupted: {type(exc).__name__}."
                    )
                    logger.exception(
                        f"agenda: executive pass failed: "
                        f"{type(exc).__name__}: {exc or '<no message>'}"
                    )
                    await asyncio.sleep(self.settings.planner_retry_backoff_seconds)
        finally:
            unsubscribe_agenda_changes(subscription)

    def _next_wake_seconds(self) -> float:
        timeout = min(
            float(self.settings.fallback_reconsideration_seconds),
            self.settings.store_poll_seconds,
        )
        now = utc_now()
        state = self.store.load()
        if state.next_reconsideration_at:
            try:
                reconsider = datetime.fromisoformat(state.next_reconsideration_at)
                if reconsider.tzinfo is None:
                    reconsider = reconsider.replace(tzinfo=now.tzinfo)
                timeout = min(timeout, max(0.1, (reconsider - now).total_seconds()))
            except (TypeError, ValueError):
                pass
        for entry in state.entries:
            if (
                entry.kind == "idle"
                and entry.status == "active"
                and entry.budget.deadline_at
            ):
                due = datetime.fromisoformat(entry.budget.deadline_at)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=now.tzinfo)
                timeout = min(timeout, max(0.1, (due - now).total_seconds()))
            if not entry.schedule or entry.status not in {"queued", "suspended"}:
                continue
            due = datetime.fromisoformat(entry.schedule.next_run)
            if due.tzinfo is None:
                due = due.replace(tzinfo=now.tzinfo)
            timeout = min(timeout, max(0.1, (due - now).total_seconds()))
        if self._report_result is not None:
            for report in state.reports:
                if report.status != "pending":
                    continue
                if not self.connected_client:
                    entry = next(
                        (
                            candidate
                            for candidate in state.entries
                            if candidate.id == report.entry_id
                        ),
                        None,
                    )
                    if entry is None or entry.metadata.get(
                        "terminal_voice_report"
                    ) or entry.report_policy != "silent":
                        continue
                due = datetime.fromisoformat(report.next_attempt_at)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=now.tzinfo)
                timeout = min(timeout, max(0.1, (due - now).total_seconds()))
        for event in state.inbox:
            if event.status != "pending":
                continue
            due = datetime.fromisoformat(event.available_at)
            if due.tzinfo is None:
                due = due.replace(tzinfo=now.tzinfo)
            timeout = min(timeout, max(0.1, (due - now).total_seconds()))
        for entry in state.entries:
            deferred_until = entry.metadata.get(
                "reconsider_deferred_until"
            ) or entry.metadata.get("quota_deferred_until")
            if entry.status != "suspended" or not deferred_until:
                continue
            try:
                due = datetime.fromisoformat(str(deferred_until))
            except (TypeError, ValueError):
                continue
            if due.tzinfo is None:
                due = due.replace(tzinfo=now.tzinfo)
            timeout = min(timeout, max(0.1, (due - now).total_seconds()))
        return max(0.1, timeout)

    def wake(self) -> None:
        self._wake.set()

    def _detach_planner_task(self, task: asyncio.Task) -> None:
        """Keep a timed-out/cancelled provider task alive only for safe cleanup."""
        self._detached_planner_tasks.add(task)

        def consume(done: asyncio.Task) -> None:
            self._detached_planner_tasks.discard(done)
            if done.cancelled():
                return
            try:
                done.exception()
            except (asyncio.CancelledError, Exception):
                pass

        task.add_done_callback(consume)

    async def _run_planner_call(self, awaitable, *, phase: str, span):
        """Run one planner call with a hard deadline and content-free tracing."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        timeout = self.settings.planner_timeout_seconds
        task = asyncio.create_task(awaitable, name=f"agenda-planner:{phase}")
        outcome = "failed"
        try:
            done, _pending = await asyncio.wait({task}, timeout=timeout)
            if task not in done:
                outcome = "timed_out"
                task.cancel()
                self._detach_planner_task(task)
                raise PlannerCallTimeout(phase, timeout)
            result = task.result()
            outcome = "completed"
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            if not task.done():
                task.cancel()
                self._detach_planner_task(task)
            raise
        finally:
            span.set_attribute(
                "agenda.planner_duration_seconds", round(loop.time() - started, 6)
            )
            span.set_attribute("agenda.planner_timeout_seconds", timeout)
            span.set_attribute("agenda.planner_phase", phase)
            span.set_attribute("agenda.planner_outcome", outcome)

    def observation_changed(self) -> None:
        if self.connected_client:
            self.store.expedite_pending_reports()
        self.wake()

    async def broadcast_snapshot(self) -> None:
        await self.emit(
            {
                "type": "agenda-snapshot",
                "agenda": self.store.snapshot(),
            }
        )

    async def preempt_for_user(self, title: str) -> AgendaEntry:
        loop = asyncio.get_running_loop()
        started = loop.time()
        cancellation = asyncio.create_task(
            self._cancel_entry_work(), name="agenda-human-preemption"
        )
        done, _pending = await asyncio.wait(
            {cancellation}, timeout=self.settings.human_preemption_timeout_seconds
        )
        within_budget = cancellation in done
        if not within_budget:
            cancellation.cancel()
            cancellation.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None
            )
            logger.error(
                "agenda: background cancellation exceeded the human preemption "
                f"budget of {self.settings.human_preemption_timeout_seconds:.3f}s"
            )
        else:
            cancellation.result()
        preemption_latency_ms = (loop.time() - started) * 1000.0
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
            metadata={
                "preemption_latency_ms": round(preemption_latency_ms, 3),
                "preemption_budget_ms": round(
                    self.settings.human_preemption_timeout_seconds * 1000.0, 3
                ),
                "preemption_within_budget": within_budget,
            },
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
            await self._cancel_entry_work()
        self.store.pause(paused)
        self.wake()
        await self.emit(
            {"type": "agenda-paused" if paused else "agenda-resumed", "version": 1}
        )
        await self.broadcast_snapshot()

    async def abandon(self, entry_id: str) -> None:
        await self._cancel_reconsideration(entry_id)
        state = self.store.load()
        entry = next(item for item in state.entries if item.id == entry_id)
        if entry.status == "active":
            await self._cancel_entry_work()
        plan_id = entry.metadata.get("resumable_plan_id")
        cancelled_plan = None
        if isinstance(plan_id, str) and self.plan_bridge.store.get(plan_id) is not None:
            cancelled_plan = self.plan_bridge.store.cancel(
                plan_id, reason="user abandoned agenda entry"
            )
            if not cancelled_plan.terminal:
                cancelled_plan = reconcile_cancelling_plan(
                    self.plan_bridge.store, cancelled_plan
                )
            if cancelled_plan.terminal:
                report_summary = (
                    "Stopped by the user; the last step's outcome could not be "
                    "confirmed and was not replayed."
                    if entry.kind != "user" and entry.report_policy != "silent"
                    else None
                )
                self.store.absorb_resumable_plan(
                    entry.id,
                    plan_id=plan_id,
                    status="abandoned",
                    progress_summary="Stopped by user.",
                    report_summary=report_summary,
                )
        if cancelled_plan is None:
            self.store.transition(
                entry_id, "abandoned", progress_summary="Stopped by user."
            )
        elif not cancelled_plan.terminal:
            self.store.transition(
                entry_id,
                "suspended",
                progress_summary=(
                    "Stop requested; the side-effect outcome requires reconciliation."
                ),
            )
        if cancelled_plan is not None and cancelled_plan.terminal:
            if self.plan_bridge.acknowledge_absorbed(cancelled_plan.plan_id):
                self.store.clear_absorbed_resumable_plan(
                    entry_id, plan_id=cancelled_plan.plan_id
                )
        if self.discard_intention is not None:
            self.discard_intention(entry_id)
        self.wake()
        await self.broadcast_snapshot()

    async def _advance(self) -> bool:
        self._assert_lease()
        state = self.store.load()
        if state.paused:
            return False
        if await self._deliver_due_report():
            return True
        due = self.store.claim_due()
        if due:
            await self._cancel_entry_work()
            state = self.store.load()
            active = next(
                (item for item in state.entries if item.status == "active"),
                None,
            )
            if active is not None and active.kind != "user":
                self.store.transition(
                    active.id,
                    "suspended",
                    progress_summary="Suspended for due scheduled work.",
                )
                state = self.store.load()

        now = utc_now()
        pending_events = [
            event
            for event in state.inbox
            if event.status == "pending"
            and datetime.fromisoformat(event.available_at) <= now
        ]
        pending_external_event = bool(pending_events)
        pending_foreground_event = next(
            (
                event
                for event in pending_events
                if (policy := trigger_handling_policy(event)) is not None
                and policy.foreground
            ),
            None,
        )
        foreground_policy = (
            trigger_handling_policy(pending_foreground_event)
            if pending_foreground_event is not None
            else None
        )
        active_for_event = next(
            (item for item in state.entries if item.status == "active"), None
        )
        if (
            pending_foreground_event is not None
            and active_for_event is not None
            and active_for_event.kind != "user"
            and active_for_event.interruptibility == "high"
            and foreground_policy is not None
            and foreground_policy.preempt_interruptible_background
        ):
            await self._cancel_entry_work()
            current = next(
                (
                    item
                    for item in self.store.load().entries
                    if item.id == active_for_event.id
                ),
                None,
            )
            if current is not None and current.status == "active":
                self.store.transition(
                    current.id,
                    "suspended",
                    progress_summary="Suspended for an urgent external event.",
                )
            state = self.store.load()

        if self._reconsider_task is not None:
            if not self._reconsider_task.done():
                newly_runnable = pending_external_event or any(
                    item.status == "queued" for item in state.entries
                )
                if not newly_runnable:
                    return False
                await self._cancel_reconsideration()
                state = self.store.load()
            else:
                task = self._reconsider_task
                self._reconsider_task = None
                self._reconsider_task_entry_id = None
                try:
                    task.result()
                except asyncio.CancelledError:
                    pass
                except LeaseLostError:
                    raise
                return True

        if self._entry_task is not None:
            if not self._entry_task.done():
                return False
            task = self._entry_task
            entry_id = self._entry_task_entry_id
            self._entry_task = None
            self._entry_task_entry_id = None
            try:
                return task.result()
            except asyncio.CancelledError:
                return True
            except LeaseLostError:
                raise
            except PlannerCallTimeout as exc:
                if entry_id is not None:
                    self._defer_entry_for_planner_timeout(entry_id, exc)
                    await self.broadcast_snapshot()
                    return True
                raise
            except Exception as exc:
                if entry_id is not None:
                    self.store.record_execution_failure(
                        entry_id,
                        error=f"{type(exc).__name__}: {str(exc)[:400]}",
                        max_attempts=self.settings.execution_failure_limit,
                        retry_backoff_seconds=self.settings.retry_backoff_seconds,
                        max_retry_backoff_seconds=(
                            self.settings.max_retry_backoff_seconds
                        ),
                    )
                    await self.broadcast_snapshot()
                    return True
                raise

        active = next((item for item in state.entries if item.status == "active"), None)
        if active:
            if active.kind != "user":
                self._entry_task = asyncio.create_task(
                    self._advance_entry(active),
                    name=f"agenda-entry:{active.id}",
                )
                self._entry_task_entry_id = active.id
                self._entry_task.add_done_callback(lambda _task: self.wake())
                return True
            return False

        queued = sorted(
            (
                item
                for item in state.entries
                if item.status == "queued" and item.schedule is None
            ),
            key=lambda item: (
                int(
                    item.metadata.get(
                        "queue_priority",
                        10 if item.kind == "scheduled" else 20,
                    )
                ),
                item.queue_position,
                item.created_at,
            ),
        )
        if queued and not pending_external_event:
            foreground = [
                item
                for item in queued
                if item.metadata.get("trigger_foreground")
                or item.metadata.get("schedule_occurrence")
                or item.kind == "scheduled"
            ]
            if foreground:
                selected_id = min(
                    foreground,
                    key=lambda item: (
                        int(item.metadata.get("queue_priority", 10)),
                        item.created_at,
                        item.id,
                    ),
                ).id
            else:
                decision = self.fair_scheduler.choose_agenda_entries(
                    queued, now=utc_now()
                )
                selected_id = decision.plan_id if decision is not None else queued[0].id
            self.store.transition(selected_id, "active")
            await self.broadcast_snapshot()
            return True
        held = [
            item
            for item in state.entries
            if item.status in {"proposed", "blocked_approval"}
        ]
        if held:
            # Exactly one generated successor is retained. Do not generate
            # additional work or cosmetic movement while it awaits approval.
            return False
        suspended = [
            item
            for item in state.entries
            if item.status == "suspended"
            and item.kind != "user"
            and (
                self.connected_client
                or not bool(item.metadata.get("waiting_for_client"))
            )
            and self._suspension_defer_is_due(item, now)
        ]
        if suspended and not pending_external_event:
            entry = suspended[-1]
            self._reconsider_task = asyncio.create_task(
                self._reconsider_entry(entry.id),
                name=f"agenda-reconsider:{entry.id}",
            )
            self._reconsider_task_entry_id = entry.id
            self._reconsider_task.add_done_callback(lambda _task: self.wake())
            return True
        if not self.connected_client and any(
            item.status == "suspended" and bool(item.metadata.get("waiting_for_client"))
            for item in state.entries
        ):
            return False
        if any(
            item.status == "suspended"
            and (
                item.metadata.get("quota_deferred_until")
                or item.metadata.get("reconsider_deferred_until")
            )
            and not self._suspension_defer_is_due(item, now)
            for item in state.entries
        ):
            return False
        entries_by_id = {entry.id: entry for entry in state.entries}
        pending_user_reports = sum(
            1
            for report in state.reports
            if report.status in {"pending", "delivering"}
            and (
                (entry := entries_by_id.get(report.entry_id)) is not None
                and (
                    entry.metadata.get("terminal_voice_report")
                    or entry.report_policy != "silent"
                )
            )
        )
        if pending_user_reports >= self.settings.report_backlog_limit:
            backpressure = create_entry(
                kind="idle",
                title="Wait for report delivery capacity",
                description=(
                    "Remain available while completed user-facing reports await "
                    "Presence-gated delivery."
                ),
                origin="recovery",
                evidence_refs=["system:report-backpressure"],
                completion_condition="Wait until the bounded capacity review.",
                time_budget_seconds=max(
                    self.settings.idle_min_seconds,
                    min(
                        self.settings.idle_max_seconds,
                        self.settings.fallback_reconsideration_seconds,
                    ),
                ),
                status="queued",
                report_policy="silent",
                metadata={
                    "queue_priority": 20,
                    "report_backpressure": True,
                },
            )
            self.store.add(backpressure)
            await self.broadcast_snapshot()
            return True
        if state.next_reconsideration_at:
            try:
                reconsider_at = datetime.fromisoformat(state.next_reconsideration_at)
                if reconsider_at.tzinfo is None:
                    reconsider_at = reconsider_at.replace(tzinfo=utc_now().tzinfo)
                if utc_now() < reconsider_at:
                    return False
            except (TypeError, ValueError):
                logger.warning("agenda: clearing invalid reconsideration timestamp")
            self.store.set_reconsideration(None)
        if self.connected_client:
            await self._generate_live_successor()
        else:
            await self._run_settling_task(self._generate_successor())
        return True

    def _agenda_context(self):
        return build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
            execution_mode="live" if self.connected_client else "headless",
        )

    def _room_snapshot(self) -> IdleScene:
        if not self.connected_client or self.room_snapshot is None:
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
        if not self.connected_client or not scene.objects:
            return IdlePlacement(None, "No live room objects are available.")
        self._assert_lease()
        try:
            self._consume_planner_quota()
        except PlannerQuotaExceeded:
            return validate_idle_placement(
                IdlePlacement(None, "Idle placement planner quota is exhausted."),
                scene,
            )
        try:
            with self.tracer.span(
                "agenda.planner.idle_placement",
                attributes={
                    "agenda.execution_mode": "live",
                    "agenda.room_object_count": len(scene.objects),
                },
            ) as span:
                placement = await self._run_planner_call(
                    self.planner.choose_idle_placement(context, scene),
                    phase="idle_placement",
                    span=span,
                )
        except asyncio.CancelledError:
            raise
        except LeaseLostError:
            raise
        except PlannerCallTimeout as exc:
            logger.warning(f"agenda: idle placement planner failed: {exc}")
            return IdlePlacement(None, "Idle placement planning timed out.")
        except Exception as exc:
            logger.warning(f"agenda: idle placement planner failed: {exc}")
            placement = IdlePlacement(None, "The idle placement planner failed.")
        return validate_idle_placement(placement, scene)

    async def _relocate_idle_safely(self, placement: IdlePlacement) -> None:
        if (
            not self.connected_client
            or placement.object_id is None
            or self.relocate_idle is None
        ):
            return
        self._assert_lease()
        quota = self.store.consume_quota(
            category="capability",
            key="idle_relocation",
            limit=self.settings.capability_calls_per_window,
            window_seconds=self.settings.quota_window_seconds,
        )
        if not quota.allowed:
            logger.info(
                "agenda: idle relocation skipped because its capability quota "
                f"is exhausted until {quota.reset_at}"
            )
            return
        try:
            with self.tracer.span(
                "agenda.activity.idle_relocation",
                attributes={
                    "agenda.destination_object": placement.object_id,
                    "agenda.execution_mode": "live",
                },
            ):
                await self.relocate_idle(placement)
            self._assert_lease()
        except asyncio.CancelledError:
            raise
        except LeaseLostError:
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
            if not self._settling_interrupted:
                raise
            logger.info("agenda: settling or successor planning was preempted")
            return False
        finally:
            if self._settling_task is task:
                self._settling_task = None
                self._settling_interrupted = False

    async def _generate_live_successor(self) -> None:
        """Generate live work without putting cosmetic relocation in its path."""

        async def generate() -> None:
            await self.emit(AgentActivityTracker.planning())
            try:
                await self._generate_successor()
            finally:
                if not self._settling_interrupted:
                    await self.emit(AgentActivityTracker.idle())

        await self._run_settling_task(generate())

    async def _deliver_due_report(self) -> bool:
        self._assert_lease()
        if self._report_result is None:
            return False
        report = self.store.claim_due_report(silent_only=not self.connected_client)
        if report is None:
            return False
        try:
            state = self.store.load()
            entry = next(item for item in state.entries if item.id == report.entry_id)
            delivery_entry = copy.copy(entry)
            delivery_entry.metadata = dict(entry.metadata)
            delivery_entry.metadata["report_delivery_key"] = report.report_key
            with self.tracer.span(
                "agenda.report.deliver",
                trace_id=entry.metadata.get("trace_id"),
                attributes={
                    "agenda.entry_id": entry.id,
                    "agenda.report_id": report.id,
                    "agenda.report_attempt": report.attempts,
                    "agenda.report_policy": entry.report_policy,
                },
            ):
                outcome = await self._report_result(delivery_entry, report.summary)
            if isinstance(outcome, bool):
                delivered = outcome
                error = None if delivered else "Presence Contract deferred delivery."
                counts_as_failure = False
            else:
                delivered = bool(getattr(outcome, "delivered", False))
                error = getattr(outcome, "reason", None)
                counts_as_failure = bool(getattr(outcome, "counts_as_failure", False))
        except asyncio.CancelledError:
            self.store.finish_report(
                report.id,
                delivered=False,
                error="delivery owner changed",
                retry_seconds=self.settings.report_retry_seconds,
                max_attempts=self.settings.report_retry_limit,
                counts_as_failure=False,
            )
            raise
        except Exception as exc:
            delivered = False
            error = f"{type(exc).__name__}: {str(exc)[:400]}"
            counts_as_failure = True
            logger.warning(f"agenda: report delivery failed: {error}")
        retry_seconds = min(
            self.settings.planner_circuit_max_seconds,
            self.settings.report_retry_seconds * (2 ** max(0, report.attempts - 1)),
        )
        self.store.finish_report(
            report.id,
            delivered=delivered,
            error=error,
            retry_seconds=retry_seconds,
            max_attempts=self.settings.report_retry_limit,
            counts_as_failure=counts_as_failure,
        )
        await self.broadcast_snapshot()
        return True

    async def _cancel_entry_work(self) -> None:
        await self._cancel_reconsideration()
        settling = self._settling_task
        if settling is not None and not settling.done():
            self._settling_interrupted = True
            settling.cancel()
            await asyncio.gather(settling, return_exceptions=True)
        if self._settling_task is settling:
            self._settling_task = None
        task = self._entry_task
        entry_id = self._entry_task_entry_id
        try:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            if entry_id is not None:
                self._release_interrupted_plan(entry_id)
            if self._entry_task is task:
                self._entry_task = None
                self._entry_task_entry_id = None
        activity = self._activity_task
        if activity is not None and not activity.done():
            activity.cancel()
            await asyncio.gather(activity, return_exceptions=True)
        self._release_event_claims(
            "Agenda work was interrupted before successor planning completed."
        )

    async def _cancel_reconsideration(self, entry_id: str | None = None) -> None:
        task = self._reconsider_task
        owned_entry_id = self._reconsider_task_entry_id
        if task is None or (entry_id is not None and owned_entry_id != entry_id):
            return
        self._reconsider_task = None
        self._reconsider_task_entry_id = None
        if task is asyncio.current_task():
            return
        if task.done():
            try:
                task.result()
            except (asyncio.CancelledError, Exception):
                pass
            return
        task.cancel()
        done, _pending = await asyncio.wait(
            {task}, timeout=self.settings.human_preemption_timeout_seconds
        )
        if task not in done:
            logger.warning(
                "agenda: reconsideration cancellation exceeded the foreground "
                f"budget for entry_id={owned_entry_id}"
            )
            self._detach_planner_task(task)

    def _release_interrupted_plan(self, entry_id: str) -> None:
        try:
            state = self.store.load()
            entry = next((item for item in state.entries if item.id == entry_id), None)
            if entry is None:
                return
            plan_id = entry.metadata.get("resumable_plan_id")
            if not isinstance(plan_id, str):
                return
            fencing_token = (
                self.lease_handle.fencing_token
                if self.lease_handle is not None
                else state.fencing_token
            )
            released = self.plan_bridge.store.release_for_preemption(
                plan_id,
                fencing_token=fencing_token,
                reason_code="agenda_preempted",
            )
            if released.state == "cancelling":
                # The side-effecting step was interrupted with an unknown
                # outcome. Terminate the plan now so the agenda can absorb it
                # and report the interruption instead of parking forever.
                reconcile_cancelling_plan(self.plan_bridge.store, released)
        except (KeyError, OSError, PlanConflictError, ValueError) as exc:
            logger.warning(f"agenda: could not release interrupted plan: {exc}")

    @staticmethod
    def _terminal_plan_summary(plan) -> str:
        """Honest user-facing summary for a plan that ended without success."""
        if plan.state == "cancelled":
            return (
                "Interrupted before completion; the last step's outcome could "
                "not be confirmed and was not replayed."
            )
        return plan.blocked_reason or "The resumable plan could not complete."

    def _release_event_claims(self, reason: str) -> int:
        if self.lease_handle is None:
            return 0
        try:
            WorkspaceLease(self.store.root).assert_current(self.lease_handle)
            with lease_context(self.lease_handle):
                return self.store.release_claimed_events(
                    owner_id=self.lease_handle.owner_id,
                    fencing_token=self.lease_handle.fencing_token,
                    reason=reason,
                )
        except LeaseLostError:
            # A newer fencing token can reclaim the older owner's event
            # immediately, so the stale owner must not attempt another write.
            return 0
        except Exception as exc:
            logger.warning(f"agenda: could not release interrupted event claim: {exc}")
            return 0

    async def _generate_successor(self) -> None:
        self._assert_lease()
        claimed_event = None
        try:
            self._consume_planner_quota()
        except PlannerQuotaExceeded as exc:
            await self._queue_quota_idle(exc.reset_at)
            return
        if self.lease_handle is not None:
            with self.tracer.span(
                "agenda.event.claim",
                attributes={
                    "agenda.fencing_token": self.lease_handle.fencing_token,
                },
            ):
                claimed_event = self.store.claim_next_event(
                    owner_id=self.lease_handle.owner_id,
                    fencing_token=self.lease_handle.fencing_token,
                    claim_timeout_seconds=self.settings.event_claim_timeout_seconds,
                    priority=lambda event: (
                        0
                        if (
                            (policy := trigger_handling_policy(event)) is not None
                            and policy.foreground
                        )
                        else 1,
                        event.available_at,
                        event.created_at,
                    ),
                )
        handling_policy = (
            trigger_handling_policy(claimed_event)
            if claimed_event is not None
            else None
        )
        context = build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
            execution_mode="live" if self.connected_client else "headless",
        )
        last_reason = "planner retry limit exhausted"
        for attempt in range(self.settings.planner_retry_limit):
            if attempt:
                try:
                    self._consume_planner_quota()
                except PlannerQuotaExceeded as exc:
                    if claimed_event is not None and self.lease_handle is not None:
                        self.store.defer_event(
                            claimed_event.id,
                            fencing_token=self.lease_handle.fencing_token,
                            available_at=exc.reset_at,
                            reason="planner quota exhausted",
                        )
                    await self._queue_quota_idle(exc.reset_at)
                    return
            try:
                with self.tracer.span(
                    "agenda.planner.propose",
                    attributes={
                        "agenda.planner_attempt": attempt + 1,
                        "agenda.event_id": (
                            claimed_event.id if claimed_event is not None else None
                        ),
                        "agenda.execution_mode": (
                            "live" if self.connected_client else "headless"
                        ),
                    },
                ) as span:
                    planner_started = time.perf_counter()
                    if handling_policy is not None and handling_policy.foreground:
                        proposal = await self._run_planner_call(
                            self.planner.propose_triggered_intention(
                                context, claimed_event, handling_policy
                            ),
                            phase="propose",
                            span=span,
                        )
                    else:
                        proposal = await self._run_planner_call(
                            self.planner.propose_intention(context),
                            phase="propose",
                            span=span,
                        )
                    planner_seconds = time.perf_counter() - planner_started
                    span.set_attribute(
                        "agenda.planner_duration_seconds",
                        round(planner_seconds, 6),
                    )
                    logger.info(
                        f"agenda.planner_timing kind=propose seconds={planner_seconds:.3f}"
                    )
                    proposal.metadata.setdefault("trace_id", span.trace_id)
            except Exception as exc:
                reason = (
                    f"planner failure: {type(exc).__name__}: "
                    f"{str(exc)[:300] or '<no message>'}"
                )
                last_reason = reason
                self._record_rejection("<planner failure>", reason)
                logger.warning(f"agenda: {reason}")
                if isinstance(exc, PlannerCallTimeout):
                    # A hard timeout ends this planning pass. Repeating the same
                    # remote call immediately can otherwise block for N×timeout.
                    break
                if attempt + 1 < self.settings.planner_retry_limit:
                    await asyncio.sleep(
                        min(
                            60.0,
                            self.settings.planner_retry_backoff_seconds * (2**attempt),
                        )
                    )
                continue
            state = self.store.load()
            verdict = self.policy.validate(
                proposal,
                supplied_evidence_refs=context.evidence_refs,
                capabilities=context.capabilities,
                object_names=context.object_names,
                comparison_entries=[
                    item
                    for item in state.entries
                    if item.status not in TERMINAL_STATUSES
                ],
            )
            event_grounded = (
                claimed_event is None
                or claimed_event.evidence_ref in proposal.motivation.evidence_refs
                or f"event:{claimed_event.id}" in proposal.motivation.evidence_refs
            )
            if verdict.accepted and event_grounded:
                if proposal.kind == "idle":
                    proposal.budget.time_budget_seconds = max(
                        self.settings.idle_min_seconds,
                        min(
                            proposal.budget.time_budget_seconds,
                            self.settings.idle_max_seconds,
                        ),
                    )
                else:
                    # A remote planner and one tool-capable agent round trip must
                    # both fit before the high-level deadline. Tiny model-proposed
                    # budgets previously collapsed healthy actions to one second.
                    proposal.budget.time_budget_seconds = min(
                        self.settings.maximum_pursuit_seconds,
                        max(
                            proposal.budget.time_budget_seconds,
                            self.settings.step_timeout_seconds * 2,
                        ),
                    )
                proposal.status = (
                    "blocked_approval" if proposal.requires_approval else "queued"
                )
                proposal.metadata["queue_priority"] = (
                    handling_policy.queue_priority
                    if handling_policy is not None
                    else 20
                )
                if handling_policy is not None:
                    trigger = trigger_from_event(claimed_event) or {}
                    proposal.report_policy = (
                        "on_completion"
                        if handling_policy.force_report
                        else proposal.report_policy
                    )
                    proposal.metadata.update(
                        {
                            "trigger_foreground": handling_policy.foreground,
                            "external_event_id": claimed_event.id,
                            "trigger_resource_id": trigger.get("resource_id"),
                            "trigger_revision": trigger.get("revision"),
                            "trigger_simulated": bool(trigger.get("simulated", False)),
                            "trigger_idempotency_key": claimed_event.idempotency_key,
                        }
                    )
                if proposal.requires_approval:
                    proposal.plan_id = self._draft_approval_plan(proposal)
                if claimed_event is not None and self.lease_handle is not None:
                    self.store.add_from_event(
                        proposal,
                        event_id=claimed_event.id,
                        fencing_token=self.lease_handle.fencing_token,
                    )
                else:
                    self.store.add(proposal)
                self.store.record_planner_success()
                logger.info(
                    f"agenda: accepted {proposal.kind} proposal "
                    f"{proposal.id!r}: {proposal.title!r}"
                )
                await self.broadcast_snapshot()
                return

            rejection_reason = (
                verdict.reason
                if not verdict.accepted
                else "proposal did not cite the claimed external event"
            )
            self._record_rejection(proposal.title, rejection_reason)
            last_reason = rejection_reason
            logger.warning(
                f"agenda: rejected proposal {proposal.title!r}: {rejection_reason}"
            )
            if attempt + 1 < self.settings.planner_retry_limit:
                await asyncio.sleep(
                    min(
                        60.0,
                        self.settings.planner_retry_backoff_seconds * (2**attempt),
                    )
                )

        state = self.store.load()
        failure_number = state.planner_health.consecutive_failures + 1
        threshold = self.settings.planner_circuit_failure_threshold
        if failure_number >= threshold:
            cooldown = min(
                self.settings.planner_circuit_max_seconds,
                self.settings.fallback_reconsideration_seconds
                * (2 ** max(0, failure_number - threshold)),
            )
            circuit_open_until = (utc_now() + timedelta(seconds=cooldown)).isoformat()
        else:
            cooldown = self.settings.idle_min_seconds
            circuit_open_until = None
        next_reconsideration = (
            circuit_open_until
            or (
                utc_now() + timedelta(seconds=self.settings.idle_min_seconds)
            ).isoformat()
        )
        self.store.record_planner_failure(
            last_reason,
            circuit_open_until=circuit_open_until,
        )
        if claimed_event is not None and self.lease_handle is not None:
            self.store.fail_event(
                claimed_event.id,
                fencing_token=self.lease_handle.fencing_token,
                error=last_reason,
                retry_seconds=min(
                    self.settings.planner_circuit_max_seconds,
                    self.settings.event_retry_seconds
                    * (2 ** max(0, claimed_event.attempts - 1)),
                ),
                max_attempts=self.settings.event_retry_limit,
            )
        self.store.set_reconsideration(next_reconsideration)
        idle = create_entry(
            kind="idle",
            title="Pause and reconsider",
            description="Remain available before another grounded planning pass.",
            origin="recovery",
            evidence_refs=["system:proposal-retries-exhausted"],
            completion_condition="Remain available until reconsideration.",
            time_budget_seconds=max(self.settings.idle_min_seconds, cooldown),
            status="queued",
            metadata={"queue_priority": 20},
        )
        self.store.add(idle)
        logger.warning(
            "agenda: proposal retry limit exhausted; queued bounded reconsideration"
        )
        await self.broadcast_snapshot()

    def _record_rejection(self, title: str, reason: str) -> None:
        def reject(current):
            current.rejections.append(
                ProposalRejection(
                    created_at=utc_now().isoformat(),
                    title=title[:160],
                    reason=reason[:500],
                )
            )
            current.rejections = current.rejections[-50:]

        self.store.mutate(reject)

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

    async def _advance_entry(self, entry: AgendaEntry) -> bool:
        self._assert_lease()
        absorbed_plan_id = entry.metadata.get("resumable_plan_absorbed")
        if isinstance(absorbed_plan_id, str):
            if self.plan_bridge.acknowledge_absorbed(absorbed_plan_id):
                self.store.clear_absorbed_resumable_plan(
                    entry.id, plan_id=absorbed_plan_id
                )
                entry = next(
                    item for item in self.store.load().entries if item.id == entry.id
                )
            else:
                self.store.transition(
                    entry.id,
                    "suspended",
                    progress_summary=(
                        "Resumable plan handoff requires operator reconciliation."
                    ),
                )
                await self.broadcast_snapshot()
                return True
        deadline = entry.budget.deadline_at
        if entry.kind == "idle":
            if deadline is None:
                entry = self.store.transition(entry.id, "active")
                deadline = entry.budget.deadline_at
            if self.connected_client and not entry.metadata.get(
                "idle_relocation_attempted"
            ):
                scene = self._room_snapshot()
                if scene.objects:
                    self.store.update_metadata(entry.id, idle_relocation_attempted=True)
                    placement = await self._choose_idle_placement(
                        self._agenda_context(), scene
                    )
                    await self._relocate_idle_safely(placement)
                    return True
            if deadline and utc_now() < datetime.fromisoformat(deadline):
                return False
            self.store.transition(entry.id, "completed")
            await self.broadcast_snapshot()
            return True
        if deadline and utc_now() >= datetime.fromisoformat(deadline):
            reason = "Time budget expired."
            self.store.transition(
                entry.id,
                "failed",
                progress_summary=reason,
            )
            await self.broadcast_snapshot()
            return True
        if not self.connected_client and (
            entry.preferred_object is not None
            or any(
                capability in self.capability_objects
                for capability in entry.required_capabilities
            )
        ):
            self.store.update_metadata(entry.id, waiting_for_client=True)
            self.store.transition(
                entry.id,
                "suspended",
                progress_summary="Waiting for a connected client and embodied room.",
            )
            await self.broadcast_snapshot()
            return True

        if self.run_intention is not None and not entry.metadata.get(
            "resumable_plan_id"
        ):
            return await self._run_intention_entry(entry)

        context = build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
            tool_schemas=self.tool_schemas,
            execution_mode="live" if self.connected_client else "headless",
        )
        existing_plan = self.plan_bridge.restore_plan_reference(entry)
        recoverable_planned_checkpoint = bool(
            existing_plan is None
            and entry.checkpoint is not None
            and entry.checkpoint.state == "planned"
            and not entry.checkpoint.side_effecting
        )
        resuming = existing_plan is not None or recoverable_planned_checkpoint
        if resuming:
            checkpoint = entry.checkpoint
            if checkpoint is None:
                self.store.transition(
                    entry.id,
                    "suspended",
                    progress_summary="Resumable plan is missing its agenda checkpoint.",
                )
                await self.broadcast_snapshot()
                return True
        else:
            try:
                self._consume_planner_quota()
            except PlannerQuotaExceeded as exc:
                self._defer_entry_for_quota(
                    entry,
                    reset_at=exc.reset_at,
                    reason="activity planner quota exhausted",
                )
                await self.broadcast_snapshot()
                return True
            with self.tracer.span(
                "agenda.planner.next_step",
                trace_id=entry.metadata.get("trace_id"),
                attributes={"agenda.entry_id": entry.id},
            ) as span:
                planner_started = time.perf_counter()
                checkpoint = await self._run_planner_call(
                    self.planner.next_step(asdict(entry), context),
                    phase="next_step",
                    span=span,
                )
                planner_seconds = time.perf_counter() - planner_started
                span.set_attribute(
                    "agenda.planner_duration_seconds",
                    round(planner_seconds, 6),
                )
                logger.info(
                    f"agenda.planner_timing kind=next_step entry={entry.id} "
                    f"seconds={planner_seconds:.3f}"
                )
            if entry.metadata.get("planner_timeout_failures"):
                self.store.update_metadata(entry.id, planner_timeout_failures=0)
            verdict = self.step_policy.validate(entry, checkpoint)
            if not verdict.accepted:
                if verdict.retryable:
                    self.store.update_metadata(entry.id, waiting_for_client=True)
                    self.store.transition(
                        entry.id,
                        "suspended",
                        progress_summary=verdict.reason,
                    )
                    await self.broadcast_snapshot()
                    return True
                rejection_count = int(entry.metadata.get("step_rejections", 0)) + 1
                self.store.update_metadata(entry.id, step_rejections=rejection_count)
                terminal = rejection_count >= self.settings.planner_retry_limit
                self.store.transition(
                    entry.id,
                    "failed" if terminal else "queued",
                    progress_summary=f"Rejected activity step: {verdict.reason}",
                )
                if terminal:
                    self.store.add_dead_letter(
                        category="activity_policy",
                        reference_id=entry.id,
                        reason=verdict.reason,
                        attempts=rejection_count,
                        data={"step_kind": checkpoint.kind},
                    )
                await self.broadcast_snapshot()
                return True
            if entry.metadata.get("step_rejections"):
                self.store.update_metadata(entry.id, step_rejections=0)
            if checkpoint.required_capability:
                capability_quota = self.store.consume_quota(
                    category="capability",
                    key=checkpoint.required_capability,
                    limit=self.settings.capability_calls_per_window,
                    window_seconds=self.settings.quota_window_seconds,
                )
                if not capability_quota.allowed:
                    self._defer_entry_for_quota(
                        entry,
                        reset_at=capability_quota.reset_at,
                        reason=(
                            "capability quota exhausted: "
                            f"{checkpoint.required_capability}"
                        ),
                    )
                    await self.broadcast_snapshot()
                    return True
        # The configured timeout is a hard execution ceiling. Queue wait is
        # measured separately by the request queue and does not consume it.
        checkpoint.timeout_seconds = self.settings.step_timeout_seconds
        if deadline:
            remaining_budget = int(
                (datetime.fromisoformat(deadline) - utc_now()).total_seconds()
            )
            if remaining_budget <= 0:
                reason = (
                    "Execution window expired before tool dispatch; no action was "
                    "started."
                )
                self.store.transition(
                    entry.id,
                    "failed",
                    progress_summary=reason,
                )
                self.store.add_dead_letter(
                    category="execution_window",
                    reference_id=entry.id,
                    reason=reason,
                    attempts=1,
                    data={"step_kind": checkpoint.kind},
                )
                await self.broadcast_snapshot()
                return True
            checkpoint.timeout_seconds = min(
                checkpoint.timeout_seconds,
                max(1, remaining_budget),
            )
        owner_id = (
            self.lease_handle.owner_id if self.lease_handle else "agenda-executive"
        )
        fencing_token = (
            self.lease_handle.fencing_token
            if self.lease_handle
            else self.store.load().fencing_token
        )
        # Surface the executing intention above the agent while the step runs.
        # The finally guarantees the bubble is cleared on completion, failure,
        # cancellation, or timeout alike.
        await self.emit(AgentActivityTracker.plan(entry.title))
        try:
            plan_result = await self.plan_bridge.execute(
                entry,
                checkpoint,
                owner_id=owner_id,
                fencing_token=fencing_token,
                capabilities=self.capabilities,
                object_names=self.object_names,
                retry_budget=self.settings.execution_failure_limit,
            )
        finally:
            await self.emit(AgentActivityTracker.idle())
        if plan_result.plan.state != "completed":
            current_entry = next(
                (item for item in self.store.load().entries if item.id == entry.id),
                None,
            )
            if current_entry is not None and current_entry.status == "blocked_approval":
                await self.broadcast_snapshot()
                self.wake()
                return True
            terminal = plan_result.plan.state in {"failed", "cancelled"}
            target = "failed" if terminal else "suspended"
            if terminal:
                failure_summary = self._terminal_plan_summary(plan_result.plan)
                self.store.absorb_resumable_plan(
                    entry.id,
                    plan_id=plan_result.plan.plan_id,
                    status=target,
                    progress_summary=failure_summary,
                    report_summary=failure_summary,
                )
            else:
                self.store.transition(
                    entry.id,
                    target,
                    progress_summary=(
                        plan_result.plan.blocked_reason
                        or f"Resumable plan is {plan_result.plan.state}."
                    ),
                )
            if terminal and self.plan_bridge.acknowledge_absorbed(
                plan_result.plan.plan_id
            ):
                self.store.clear_absorbed_resumable_plan(
                    entry.id, plan_id=plan_result.plan.plan_id
                )
            await self.broadcast_snapshot()
            return True
        result = plan_result.summary
        if entry.metadata.get("execution_failures"):
            self.store.update_metadata(entry.id, execution_failures=0)
        try:
            self._consume_planner_quota()
        except PlannerQuotaExceeded as exc:
            self._defer_entry_for_quota(
                entry,
                reset_at=exc.reset_at,
                reason="completion evaluation quota exhausted",
            )
            await self.broadcast_snapshot()
            return True
        with self.tracer.span(
            "agenda.planner.evaluate_completion",
            trace_id=entry.metadata.get("trace_id"),
            attributes={"agenda.entry_id": entry.id},
        ) as span:
            planner_started = time.perf_counter()
            complete, summary = await self._run_planner_call(
                self.planner.evaluate_completion(asdict(entry), [result]),
                phase="evaluate_completion",
                span=span,
            )
            planner_seconds = time.perf_counter() - planner_started
            span.set_attribute(
                "agenda.planner_duration_seconds",
                round(planner_seconds, 6),
            )
            logger.info(
                f"agenda.planner_timing kind=evaluate_completion entry={entry.id} "
                f"seconds={planner_seconds:.3f}"
            )
        if entry.metadata.get("planner_timeout_failures"):
            self.store.update_metadata(entry.id, planner_timeout_failures=0)
        target = "completed" if complete else "queued"
        self.store.absorb_resumable_plan(
            entry.id,
            plan_id=plan_result.plan.plan_id,
            status=target,
            progress_summary=summary,
            report_summary=summary if complete else None,
        )
        if self.plan_bridge.acknowledge_absorbed(plan_result.plan.plan_id):
            self.store.clear_absorbed_resumable_plan(
                entry.id, plan_id=plan_result.plan.plan_id
            )
        await self.broadcast_snapshot()
        if complete:
            self.wake()
        return True

    async def _run_intention_entry(self, entry: AgendaEntry) -> bool:
        """Run the production single-chain path without per-step planning."""
        checkpoint = entry.checkpoint
        if checkpoint is not None and checkpoint.state == "uncertain":
            self.store.transition(
                entry.id,
                "suspended",
                progress_summary=(
                    "An interrupted side effect requires reconciliation before "
                    "the intention can resume."
                ),
            )
            await self.broadcast_snapshot()
            return True
        if checkpoint is not None:
            self.store.set_checkpoint_if_status(entry.id, "active", None)

        await self.emit(AgentActivityTracker.plan(entry.title))
        started = time.perf_counter()
        try:
            with self.tracer.span(
                "agenda.intention.session",
                trace_id=entry.metadata.get("trace_id"),
                attributes={"agenda.entry_id": entry.id},
            ) as span:
                result = await self.run_intention(entry)
                span.set_attribute("agenda.intention.outcome", result.outcome)
                span.set_attribute("agenda.intention.session_id", result.session_id)
                span.set_attribute("agenda.intention.model_turns", result.model_turns)
                span.set_attribute("agenda.intention.tool_calls", result.tool_calls)
                span.set_attribute(
                    "agenda.intention.model_seconds", round(result.model_seconds, 6)
                )
                span.set_attribute(
                    "agenda.intention.tool_seconds", round(result.tool_seconds, 6)
                )
                span.set_attribute(
                    "agenda.intention.queue_wait_seconds",
                    round(result.queue_wait_seconds, 6),
                )
                span.set_attribute(
                    "agenda.intention.execution_seconds",
                    round(result.execution_seconds, 6),
                )
                span.set_attribute(
                    "agenda.intention.relocation_seconds",
                    round(result.relocation_seconds, 6),
                )
        finally:
            await self.emit(AgentActivityTracker.idle())

        current = next(
            (item for item in self.store.load().entries if item.id == entry.id),
            None,
        )
        if current is None or current.status != "active":
            logger.info(
                "agenda.intention stale_discarded entry={} seconds={:.3f}",
                entry.id,
                time.perf_counter() - started,
            )
            if self.discard_intention is not None and (
                current is None or current.status in TERMINAL_STATUSES
            ):
                self.discard_intention(entry.id)
            return True
        target = "failed" if result.status == "failed" else "completed"
        transitioned = self.store.transition_if_status(
            entry.id,
            "active",
            target,
            progress_summary=result.summary,
            metadata_updates={
                "intention_outcome": result.outcome,
                "intention_session_id": result.session_id,
                "intention_model_turns": result.model_turns,
                "intention_tool_calls": result.tool_calls,
            },
        )
        if transitioned is None:
            logger.info("agenda.intention stale_discarded entry={}", entry.id)
            if self.discard_intention is not None:
                self.discard_intention(entry.id)
            return True
        if self.discard_intention is not None:
            self.discard_intention(entry.id)
        await self.broadcast_snapshot()
        self.wake()
        return True

    async def _reconsider_suspended(self) -> None:
        self._assert_lease()
        state = self.store.load()
        candidates = [
            entry
            for entry in state.entries
            if entry.status == "suspended"
            and entry.kind != "user"
            and self._suspension_defer_is_due(entry, utc_now())
        ]
        if not candidates:
            return
        await self._reconsider_entry(candidates[-1].id)

    async def _reconsider_entry(self, entry_id: str) -> None:
        self._assert_lease()
        state = self.store.load()
        entry = next((item for item in state.entries if item.id == entry_id), None)
        if entry is None or entry.status != "suspended":
            return
        if self.run_intention is not None and not entry.metadata.get(
            "resumable_plan_id"
        ):
            deadline = entry.budget.deadline_at
            if deadline and utc_now() >= datetime.fromisoformat(deadline):
                self.store.transition_if_status(
                    entry.id,
                    "suspended",
                    "failed",
                    progress_summary=(
                        "Time budget expired while the intention was suspended."
                    ),
                )
                if self.discard_intention is not None:
                    self.discard_intention(entry.id)
                await self.broadcast_snapshot()
                return
            if entry.checkpoint is not None and entry.checkpoint.state == "uncertain":
                return
            if not self.connected_client and entry.metadata.get("waiting_for_client"):
                return
            self.store.transition_if_status(
                entry.id,
                "suspended",
                "queued",
                metadata_updates={
                    "reconsider_deferred_until": None,
                    "reconsider_deferred_reason": None,
                    "waiting_for_client": None,
                },
                progress_summary="Resuming the private intention conversation.",
            )
            await self.broadcast_snapshot()
            return
        linked = self.plan_bridge.restore_plan_reference(entry)
        if linked is not None and linked.state in {"failed", "cancelled"}:
            # A preempted plan was already reconciled as terminal (for example
            # by _release_interrupted_plan). Absorb it deterministically here
            # instead of spending a planner call to decide something already
            # decided, and report the interruption for non-silent entries.
            terminal_summary = self._terminal_plan_summary(linked)
            self.store.absorb_resumable_plan(
                entry.id,
                plan_id=linked.plan_id,
                status="failed",
                progress_summary=terminal_summary,
                report_summary=(
                    terminal_summary
                    if entry.kind != "user" and entry.report_policy != "silent"
                    else None
                ),
            )
            if self.plan_bridge.acknowledge_absorbed(linked.plan_id):
                self.store.clear_absorbed_resumable_plan(
                    entry.id, plan_id=linked.plan_id
                )
            await self.broadcast_snapshot()
            return
        deadline = entry.budget.deadline_at
        if deadline:
            try:
                expired = utc_now() >= datetime.fromisoformat(deadline)
            except (TypeError, ValueError):
                expired = False
            if expired:
                uncertain = bool(
                    entry.checkpoint and entry.checkpoint.state == "uncertain"
                )
                reason = (
                    "Expired uncertain step requires operator reconciliation; it "
                    "was not replayed."
                    if uncertain
                    else "Time budget expired while the entry was suspended."
                )
                self.store.transition(entry.id, "failed", progress_summary=reason)
                if uncertain:
                    self.store.add_dead_letter(
                        category="activity_reconciliation",
                        reference_id=entry.id,
                        reason=reason,
                        attempts=1,
                        data={
                            "step_kind": entry.checkpoint.kind,
                            "step_id": entry.checkpoint.step_id,
                        },
                    )
                await self.broadcast_snapshot()
                return
        if entry.metadata.get("quota_deferred_until"):
            self.store.update_metadata(
                entry.id,
                quota_deferred_until=None,
                quota_deferred_reason=None,
            )
        if entry.metadata.get("reconsider_deferred_until"):
            self.store.update_metadata(
                entry.id,
                reconsider_deferred_until=None,
                reconsider_deferred_reason=None,
            )
        if self.connected_client and entry.metadata.get("waiting_for_client"):
            self.store.update_metadata(entry.id, waiting_for_client=False)
        if entry.kind in {"idle", "scheduled"}:
            self.store.transition(entry.id, "queued")
            return
        checkpoint = entry.checkpoint
        if (
            checkpoint is not None
            and checkpoint.state == "planned"
            and not checkpoint.side_effecting
        ):
            # No action has started (or its result is already durable), so this
            # boundary is safe to resume without spending another LLM call.
            self.store.update_metadata(
                entry.id,
                reconsider_failures=0,
                reconsider_deferred_until=None,
                reconsider_deferred_reason=None,
            )
            self.store.transition(entry.id, "queued")
            return
        context = build_agenda_context(
            self.store,
            client_uid=self.client_uid,
            capabilities=self.capabilities,
            object_names=self.object_names,
            execution_mode="live" if self.connected_client else "headless",
        )
        try:
            self._consume_planner_quota()
        except PlannerQuotaExceeded as exc:
            self._defer_entry_for_quota(
                entry,
                reset_at=exc.reset_at,
                reason="reconsideration quota exhausted",
            )
            return
        try:
            with self.tracer.span(
                "agenda.planner.reconsider",
                trace_id=entry.metadata.get("trace_id"),
                attributes={"agenda.entry_id": entry.id},
            ) as span:
                decision = await self._run_planner_call(
                    self.planner.reconsider(asdict(entry), context),
                    phase="reconsider",
                    span=span,
                )
                current = next(
                    (item for item in self.store.load().entries if item.id == entry.id),
                    None,
                )
                if current is None or current.status != "suspended":
                    span.set_attribute("agenda.planner_outcome", "stale_discarded")
                    logger.info(
                        "agenda: discarded stale reconsideration result "
                        f"entry_id={entry.id}"
                    )
                    return
                if decision != "abandon":
                    target = {"resume": "queued", "queue": "queued"}[decision]
                    applied = self.store.transition_if_status(
                        entry.id,
                        "suspended",
                        target,
                        metadata_updates={
                            "reconsider_failures": 0,
                            "reconsider_deferred_until": None,
                            "reconsider_deferred_reason": None,
                            "planner_timeout_failures": 0,
                        },
                    )
                    if applied is None:
                        span.set_attribute("agenda.planner_outcome", "stale_discarded")
                        logger.info(
                            "agenda: discarded reconsideration result after state "
                            f"changed entry_id={entry.id}"
                        )
                    return
        except (asyncio.CancelledError, LeaseLostError):
            raise
        except Exception as exc:
            current = next(
                (item for item in self.store.load().entries if item.id == entry.id),
                None,
            )
            if current is None or current.status != "suspended":
                logger.info(
                    "agenda: ignored reconsideration failure after state changed "
                    f"entry_id={entry.id}"
                )
                return
            failures = int(entry.metadata.get("reconsider_failures", 0)) + 1
            reason = f"Reconsideration failed: {type(exc).__name__}."
            if failures >= self.settings.planner_retry_limit:
                applied = self.store.transition_if_status(
                    entry.id,
                    "suspended",
                    "failed",
                    metadata_updates={"reconsider_failures": failures},
                    progress_summary=reason,
                )
                if applied is not None:
                    self.store.add_dead_letter(
                        category="reconsideration",
                        reference_id=entry.id,
                        reason=reason,
                        attempts=failures,
                        data={
                            "checkpoint_state": checkpoint.state if checkpoint else None
                        },
                    )
            else:
                delay = max(
                    5.0,
                    self.settings.planner_retry_backoff_seconds * (2 ** (failures - 1)),
                )
                retry_at = (utc_now() + timedelta(seconds=delay)).isoformat()
                self.store.transition_if_status(
                    entry.id,
                    "suspended",
                    "suspended",
                    metadata_updates={
                        "reconsider_failures": failures,
                        "reconsider_deferred_until": retry_at,
                        "reconsider_deferred_reason": reason,
                    },
                    progress_summary=f"{reason} Retry deferred until {retry_at}.",
                )
            logger.warning(
                "agenda: suspended entry reconsideration failed "
                f"entry_id={entry.id} attempt={failures} "
                f"error_type={type(exc).__name__}"
            )
            await self.broadcast_snapshot()
            return
        if decision == "abandon":
            if entry.metadata.get("resumable_plan_id"):
                await self.abandon(entry.id)
            else:
                self.store.transition_if_status(
                    entry.id,
                    "suspended",
                    "abandoned",
                    metadata_updates={
                        "reconsider_failures": 0,
                        "reconsider_deferred_until": None,
                        "reconsider_deferred_reason": None,
                        "planner_timeout_failures": 0,
                    },
                )
            return

    def _assert_lease(self) -> None:
        if self.lease_handle is not None:
            WorkspaceLease(self.store.root).assert_current(self.lease_handle)

    @staticmethod
    def _quota_defer_is_due(entry: AgendaEntry, now: datetime) -> bool:
        value = entry.metadata.get("quota_deferred_until")
        if not value:
            return True
        try:
            due = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return True
        if due.tzinfo is None:
            due = due.replace(tzinfo=now.tzinfo)
        return due <= now

    @classmethod
    def _suspension_defer_is_due(cls, entry: AgendaEntry, now: datetime) -> bool:
        return cls._quota_defer_is_due(entry, now) and cls._metadata_time_is_due(
            entry.metadata.get("reconsider_deferred_until"), now
        )

    @staticmethod
    def _metadata_time_is_due(value: object, now: datetime) -> bool:
        if not value:
            return True
        try:
            due = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return True
        if due.tzinfo is None:
            due = due.replace(tzinfo=now.tzinfo)
        return due <= now

    def _consume_planner_quota(self) -> None:
        decision = self.store.consume_quota(
            category="planner",
            limit=self.settings.planner_calls_per_window,
            window_seconds=self.settings.quota_window_seconds,
        )
        if not decision.allowed:
            raise PlannerQuotaExceeded(decision.reset_at)

    def _defer_entry_for_quota(
        self,
        entry: AgendaEntry,
        *,
        reset_at: str,
        reason: str,
    ) -> None:
        self.store.update_metadata(
            entry.id,
            quota_deferred_until=reset_at,
            quota_deferred_reason=reason[:300],
        )
        self.store.transition(
            entry.id,
            "suspended",
            progress_summary=f"Deferred by autonomy quota until {reset_at}.",
        )

    def _defer_entry_for_planner_timeout(
        self, entry_id: str, error: PlannerCallTimeout
    ) -> None:
        current = next(
            (item for item in self.store.load().entries if item.id == entry_id), None
        )
        if current is None or current.status != "active":
            return
        failures = int(current.metadata.get("planner_timeout_failures", 0)) + 1
        delay = min(
            self.settings.planner_circuit_max_seconds,
            max(
                5.0,
                self.settings.planner_retry_backoff_seconds * (2 ** (failures - 1)),
            ),
        )
        retry_at = (utc_now() + timedelta(seconds=delay)).isoformat()
        self.store.transition_if_status(
            entry_id,
            "active",
            "suspended",
            metadata_updates={
                "planner_timeout_failures": failures,
                "reconsider_deferred_until": retry_at,
                "reconsider_deferred_reason": str(error)[:300],
            },
            progress_summary=(
                f"Planner {error.phase} timed out; retry deferred until {retry_at}."
            ),
        )

    async def _queue_quota_idle(self, reset_at: str) -> None:
        try:
            reset = datetime.fromisoformat(reset_at)
            if reset.tzinfo is None:
                reset = reset.replace(tzinfo=utc_now().tzinfo)
            wait_seconds = max(1, int((reset - utc_now()).total_seconds()))
        except (TypeError, ValueError):
            wait_seconds = self.settings.idle_min_seconds
        idle = create_entry(
            kind="idle",
            title="Wait for autonomy quota",
            description="Remain available until bounded planner capacity returns.",
            origin="recovery",
            evidence_refs=["system:autonomy-quota"],
            completion_condition="Wait until the next quota capacity review.",
            time_budget_seconds=max(
                self.settings.idle_min_seconds,
                min(self.settings.idle_max_seconds, wait_seconds),
            ),
            status="queued",
            report_policy="silent",
            metadata={"queue_priority": 20, "quota_backpressure": True},
        )
        self.store.add(idle)
        await self.broadcast_snapshot()


_executives: dict[str, AgendaExecutive] = {}


def register_executive(client_uid: str, executive: AgendaExecutive) -> None:
    _executives[client_uid] = executive


def get_executive(client_uid: str) -> Optional[AgendaExecutive]:
    return _executives.get(client_uid) or get_connected_executive()


def get_connected_executive() -> Optional[AgendaExecutive]:
    return next(iter(_executives.values()), None)


def unregister_executive(client_uid: str) -> None:
    _executives.pop(client_uid, None)


def wake_executive(client_uid: str) -> bool:
    """Wake only the executive bound to this client, without fallback routing."""
    executive = _executives.get(client_uid)
    if executive is None:
        return False
    executive.observation_changed()
    return True
