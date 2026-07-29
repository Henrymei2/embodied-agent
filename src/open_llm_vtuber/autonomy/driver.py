"""Runtime driver that ticks the autonomy planner and carries out its decision.

This is the thin glue between the pure planner and the rest of the system:
every ``tick_seconds`` it builds an Observation, asks the planner what to do,
and — if the planner chose to act — enqueues the chosen prompt through the same
per-client LLM priority queue the scheduler uses, so autonomous work never
preempts a live user request.
"""

from __future__ import annotations

import asyncio
import json
from typing import Callable, Optional

from loguru import logger

from .habits import HabitStore, get_or_create_habit_store, reflect_and_propose
from .observation import (
    Observation,
    get_client_presence,
    wait_for_observation_change,
)
from .planner import AutonomyPlanner, PlannerDecision
from .presence import PresenceLedger, get_or_create_presence_ledger
from .settings import AutonomySettings
from .state import AutonomyState, get_or_create_autonomy_state
from ..utils.timezone import now_local

# Reflection uses wall time rather than tick counts, so changing responsiveness
# does not accidentally turn reflection into a high-frequency LLM workload.
_REFLECTION_INTERVAL_SECONDS = 4 * 60 * 60
_REFLECTION_MIN_RECEIPTS = 3

# One workspace represents one persistent agent. Multiple frontend windows may
# observe it, but they must not launch simultaneous autonomous turns.
_PENDING_WORKSPACES: set[str] = set()


class AutonomySupervisor:
    """One long-lived autonomy owner for a persistent agent workspace.

    WebSocket clients are replaceable delivery endpoints. The supervisor
    survives disconnects, waits without spinning when no endpoint is attached,
    and hands control to another connected client instead of launching a second
    planner loop against the same state.
    """

    def __init__(self, workspace_root) -> None:
        self.workspace_root = workspace_root
        self._clients: dict[str, tuple[AutonomySettings, PresenceLedger]] = {}
        self._wake = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._selected_uid: Optional[str] = None

    @property
    def task(self) -> Optional[asyncio.Task]:
        return self._task

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return self._task

    def attach(
        self,
        client_uid: str,
        settings: AutonomySettings,
        presence: PresenceLedger,
    ) -> asyncio.Task:
        previous_uid = self._selected_uid
        self._clients[client_uid] = (settings, presence)
        self._selected_uid = client_uid
        self._wake.set()
        if previous_uid and previous_uid != client_uid:
            from .observation import signal_observation_change

            signal_observation_change(previous_uid)
        return self.start()

    def detach(self, client_uid: str) -> None:
        self._clients.pop(client_uid, None)
        if self._selected_uid == client_uid:
            self._selected_uid = next(reversed(self._clients), None)
        self._wake.set()
        from .observation import signal_observation_change

        signal_observation_change(client_uid)

    async def _run(self) -> None:
        logger.info(f"autonomy: supervisor started for {self.workspace_root}")
        try:
            while True:
                if not self._clients:
                    self._selected_uid = None
                    self._wake.clear()
                    if not self._clients:
                        await self._wake.wait()
                    continue

                # Dict insertion order makes the most recently connected window
                # the active embodiment when several observe the same agent.
                client_uid = self._selected_uid or next(reversed(self._clients))
                settings, presence = self._clients[client_uid]
                await autonomy_loop(
                    client_uid,
                    settings=settings,
                    presence=presence,
                    is_attached=lambda uid=client_uid: (
                        uid in self._clients and self._selected_uid == uid
                    ),
                    propagate_cancel=True,
                )
        except asyncio.CancelledError:
            logger.info(f"autonomy: supervisor stopped for {self.workspace_root}")
            raise


_SUPERVISORS_BY_ROOT: dict[str, AutonomySupervisor] = {}
_CLIENT_SUPERVISORS: dict[str, AutonomySupervisor] = {}


def _get_or_create_supervisor(workspace_root) -> AutonomySupervisor:
    key = str(workspace_root.resolve())
    supervisor = _SUPERVISORS_BY_ROOT.get(key)
    if supervisor is None:
        supervisor = AutonomySupervisor(workspace_root)
        _SUPERVISORS_BY_ROOT[key] = supervisor
    return supervisor


def start_autonomy_supervisor(workspace_root) -> asyncio.Task:
    """Start the server-owned supervisor before a frontend connects."""
    return _get_or_create_supervisor(workspace_root).start()


def attach_autonomy_client(
    client_uid: str,
    workspace_root,
    settings: AutonomySettings,
    presence: PresenceLedger,
) -> asyncio.Task:
    """Attach a replaceable client endpoint to the workspace supervisor."""
    supervisor = _get_or_create_supervisor(workspace_root)
    _CLIENT_SUPERVISORS[client_uid] = supervisor
    return supervisor.attach(client_uid, settings, presence)


def detach_autonomy_client(client_uid: str) -> None:
    """Detach a client without stopping the persistent workspace supervisor."""
    supervisor = _CLIENT_SUPERVISORS.pop(client_uid, None)
    if supervisor is not None:
        supervisor.detach(client_uid)


async def shutdown_autonomy_supervisors() -> None:
    """Cancel all server-owned autonomy tasks during application shutdown."""
    tasks = [
        supervisor.task
        for supervisor in _SUPERVISORS_BY_ROOT.values()
        if supervisor.task is not None and not supervisor.task.done()
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _CLIENT_SUPERVISORS.clear()
    _SUPERVISORS_BY_ROOT.clear()
    _PENDING_WORKSPACES.clear()


def _build_observation(client_uid: str, state: AutonomyState) -> Observation:
    """Best-effort snapshot of the world for the planner.

    Signals we can't cheaply obtain are left as None; the planner degrades
    gracefully. `user_present` is true whenever the client is still registered.
    """
    from datetime import date

    from ..client_registry import get_client_context
    from ..furniture_state import active_session, load_library, load_plans, load_todos
    from ..workspace import ensure_workspace
    from .screen_context import get_screen_context

    connected = get_client_context(client_uid) is not None
    presence = get_client_presence(client_uid)
    screen = get_screen_context(client_uid)
    now = now_local()
    approved_plan_count = 0
    open_todo_count = 0
    has_active_reading = False
    has_recent_context = False
    try:
        approved_plan_count = sum(
            1 for plan in load_plans() if plan.get("status") == "approved"
        )
        open_todo_count = sum(
            1 for todo in load_todos() if todo.get("status") == "open"
        )
        has_active_reading = active_session(load_library()) is not None
        workspace = ensure_workspace()
        daily_memory = workspace.daily_memory_dir / f"{date.today().isoformat()}.md"
        has_recent_context = daily_memory.exists() and daily_memory.stat().st_size > 0
    except (OSError, ValueError) as exc:
        logger.debug(f"autonomy: durable work signals unavailable: {exc}")
    return Observation(
        idle_seconds=presence.idle_seconds,
        user_present=connected and presence.user_present,
        seconds_since_last_spoke=state.seconds_since_last_spoke(now),
        hour_of_day=now.hour,
        focus_mode=presence.focus_mode,
        presence_fresh=presence.fresh,
        screen_context=screen.summary() if screen is not None else None,
        approved_plan_count=approved_plan_count,
        open_todo_count=open_todo_count,
        has_active_reading=has_active_reading,
        has_recent_context=has_recent_context,
    )


def _workspace_root() -> "object":
    from ..workspace import ensure_workspace

    return ensure_workspace().root


def _get_llm_complete(client_uid: str):
    """Best-effort handle on the client's LLM for habit reflection.

    Mirrors how the memory system rides the agent's own stateless LLM; when
    the agent type doesn't expose one, reflection is silently skipped and the
    habit engine keeps running with its current repertoire.
    """
    from ..client_registry import get_client_context
    from ..memory import make_llm_complete

    context = get_client_context(client_uid)
    llm = getattr(getattr(context, "agent_engine", None), "_llm", None)
    if llm is None:
        return None
    try:
        return make_llm_complete(llm)
    except Exception:  # noqa: BLE001 - reflection is strictly optional
        return None


def _memory_excerpt() -> str:
    """Short long-term + today's memory excerpt as reflection evidence."""
    from datetime import date

    from ..workspace import ensure_workspace

    workspace = ensure_workspace()
    parts: list[str] = []
    for path in (
        workspace.memory_file,
        workspace.daily_memory_dir / f"{date.today().isoformat()}.md",
    ):
        try:
            if path.exists():
                content = path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
        except Exception:  # noqa: BLE001 - excerpt is best effort
            continue
    return "\n\n".join(parts)[:3000]


async def _maybe_reflect(
    client_uid: str, presence: PresenceLedger, habits: HabitStore
) -> None:
    """Enqueue one habit-reflection pass behind live user requests."""
    from ..conversations.llm_request_queue import PRIORITY_PASSIVE, get_queue

    llm_complete = _get_llm_complete(client_uid)
    if llm_complete is None:
        logger.debug("autonomy: no LLM reachable for habit reflection; skipping")
        return

    receipts = [
        receipt.to_dict()
        for receipt in presence.receipts
        if receipt.status == "succeeded"
    ]
    if len(receipts) < _REFLECTION_MIN_RECEIPTS:
        logger.debug("autonomy: not enough receipt evidence for habit reflection")
        return

    async def _run() -> None:
        habit = await reflect_and_propose(
            llm_complete, habits, receipts, _memory_excerpt()
        )
        if habit is not None:
            logger.info(f"autonomy: reflection adopted new trial habit '{habit.name}'")

    queue = get_queue(client_uid)
    await queue.enqueue(
        coro_factory=_run,
        priority=PRIORITY_PASSIVE,
        label="autonomy:reflection",
        dedupe_key="autonomy:reflection",
    )


async def _dispatch(
    client_uid: str,
    decision: PlannerDecision,
    observation: Observation,
    presence: PresenceLedger,
    state: AutonomyState,
    workspace_key: str,
    habits: Optional[HabitStore] = None,
) -> bool:
    """Enqueue one outcome-tracked autonomous action."""
    from ..client_registry import get_client_context, get_client_send
    from ..conversations.llm_request_queue import (
        PRIORITY_PASSIVE,
        PRIORITY_PROACTIVE,
        get_queue,
    )

    context = get_client_context(client_uid)
    send_text = get_client_send(client_uid)
    if context is None or send_text is None:
        logger.debug(f"autonomy: no live client {client_uid}; skipping dispatch")
        return False

    if decision.direction is None:
        return False

    if workspace_key in _PENDING_WORKSPACES:
        logger.debug("autonomy: workspace already has queued/running work")
        return False

    try:
        receipt = presence.queue_action(
            direction=decision.direction.value,
            action=decision.action,
            score=decision.score,
            reason=decision.reason,
            observation=observation,
            habit_id=decision.habit_id,
        )
    except ValueError as exc:
        logger.debug(f"autonomy: presence contract blocked dispatch: {exc}")
        return False

    metadata = {
        "autonomous": True,
        "direction": decision.direction.value if decision.direction else None,
        "report": decision.report,
        "skip_history": not decision.report,
        "skip_memory": False,
        "presence_receipt_id": receipt.receipt_id,
        "habit_id": decision.habit_id,
    }
    priority = PRIORITY_PROACTIVE if decision.spoke else PRIORITY_PASSIVE

    # Make autonomous help screen-aware: when a recent screen context is known,
    # hand it to the agent alongside the action prompt so any offer of help is
    # about what the user is actually doing rather than generic.
    prompt = decision.prompt
    if observation.screen_context:
        prompt = (
            f"Current screen context: {observation.screen_context}.\n{decision.prompt}"
        )

    async def _send_receipt() -> None:
        try:
            await send_text(
                json.dumps(
                    {
                        "type": "autonomy-receipt",
                        "receipt": receipt.to_dict(),
                        "attention_budget_remaining": round(presence.budget_balance, 2),
                    },
                    ensure_ascii=False,
                )
            )
        except Exception as exc:  # noqa: BLE001 - accounting must outlive the UI
            logger.debug(f"autonomy: could not publish receipt update: {exc}")

    async def _run() -> None:
        from ..conversations.single_conversation import process_single_conversation
        from ..study import log_event

        started, gate_reason = presence.start_action(
            receipt.receipt_id, observation=observation
        )
        if not started:
            presence.fail_action(receipt.receipt_id, gate_reason)
            await _send_receipt()
            return

        log_event(
            "autonomy_action",
            receipt_id=receipt.receipt_id,
            direction=decision.direction.value,
            action=decision.action,
            score=round(decision.score, 3),
            cost=receipt.attention_cost,
            budget_left=round(presence.budget_balance, 2),
        )
        await _send_receipt()

        try:
            result = await process_single_conversation(
                context=context,
                websocket_send=send_text,
                client_uid=client_uid,
                user_input=prompt,
                metadata=metadata,
            )
        except asyncio.CancelledError:
            presence.cancel_action(receipt.receipt_id)
            await _send_receipt()
            raise
        except Exception as exc:
            presence.fail_action(receipt.receipt_id, str(exc))
            await _send_receipt()
            raise

        if not result.success:
            error = result.error or "agent turn failed"
            presence.fail_action(receipt.receipt_id, error)
            await _send_receipt()
            raise RuntimeError(error)

        presence.complete_action(receipt.receipt_id)
        state.record_action(decision.direction, spoke=decision.spoke)
        if habits is not None and decision.habit_id:
            habits.record_run(decision.habit_id)
        await _send_receipt()

    def _on_done(status: str, error: Optional[BaseException]) -> None:
        _PENDING_WORKSPACES.discard(workspace_key)
        if receipt.status == "queued":
            if status in {"canceled", "dropped"}:
                presence.discard_action(receipt.receipt_id)
            else:
                presence.fail_action(
                    receipt.receipt_id,
                    str(error or "queued action did not start"),
                )

    _PENDING_WORKSPACES.add(workspace_key)
    try:
        queue = get_queue(client_uid)
        enqueued = await queue.enqueue(
            coro_factory=_run,
            priority=priority,
            label=f"autonomy:{decision.direction.value}",
            dedupe_key="autonomy:action",
            on_done=_on_done,
        )
        return enqueued
    except Exception:
        _PENDING_WORKSPACES.discard(workspace_key)
        presence.discard_action(receipt.receipt_id)
        raise


async def autonomy_loop(
    client_uid: str,
    settings: Optional[AutonomySettings] = None,
    planner: Optional[AutonomyPlanner] = None,
    presence: Optional[PresenceLedger] = None,
    is_attached: Optional[Callable[[], bool]] = None,
    propagate_cancel: bool = False,
) -> None:
    """Evaluate one attached client until handoff, disconnect, or shutdown."""
    settings = settings or AutonomySettings()
    if not settings.enabled:
        logger.info("autonomy: disabled; loop not started.")
        return

    planner = planner or AutonomyPlanner()
    workspace_root = _workspace_root()
    workspace_key = str(workspace_root.resolve())
    state = get_or_create_autonomy_state(workspace_root)
    presence = presence or get_or_create_presence_ledger(
        client_uid, workspace_root, settings
    )
    # Habit Engine: learned behaviors join the planner's candidate pool and
    # a periodic reflection pass may grow the repertoire.
    habits = get_or_create_habit_store(workspace_root)
    loop_clock = asyncio.get_running_loop()
    next_reflection_at = loop_clock.time() + _REFLECTION_INTERVAL_SECONDS
    logger.info(
        f"autonomy: loop started for {client_uid} "
        f"(freq={settings.interruption_frequency}, style={settings.help_style}, "
        f"confidence={settings.confidence_threshold}, tick={settings.tick_seconds}s, "
        f"learned_habits={len(habits.active_habits())})"
    )

    while True:
        try:
            observation = _build_observation(client_uid, state)
            from ..client_registry import get_client_context

            if (is_attached is not None and not is_attached()) or (
                get_client_context(client_uid) is None
            ):
                logger.info(f"autonomy: client {client_uid} is gone; loop stopped")
                break

            # Periodic habit reflection: consider growing the repertoire.
            if loop_clock.time() >= next_reflection_at:
                next_reflection_at = loop_clock.time() + _REFLECTION_INTERVAL_SECONDS
                try:
                    await _maybe_reflect(client_uid, presence, habits)
                except Exception as exc:  # noqa: BLE001 - never break the loop
                    logger.error(f"autonomy: habit reflection failed: {exc}")

            decision = planner.propose_and_choose(
                observation,
                state,
                settings,
                presence=presence,
                extra_candidates=habits.active_candidates(),
            )
            logger.debug(
                f"autonomy: decision={decision.action} "
                f"direction={decision.direction} score={decision.score:.3f} "
                f"habit={decision.habit_id or '-'} ({decision.reason})"
            )

            if decision.is_act:
                await _dispatch(
                    client_uid,
                    decision,
                    observation,
                    presence,
                    state,
                    workspace_key,
                    habits=habits,
                )

            # Context changes wake the planner promptly; the configured tick is
            # retained as a safety heartbeat. The planner's wall-clock action
            # cooldown prevents one-minute presence pulses from creating work.
            await wait_for_observation_change(client_uid, timeout=settings.tick_seconds)

        except asyncio.CancelledError:
            logger.info(f"autonomy: loop canceled for {client_uid}")
            if propagate_cancel:
                raise
            break
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on errors
            logger.error(f"autonomy: error in loop for {client_uid}: {exc}")
            await asyncio.sleep(min(5.0, float(settings.tick_seconds)))
