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
from datetime import datetime
from typing import Optional

from loguru import logger

from .observation import Observation, get_client_presence
from .planner import AutonomyPlanner, PlannerDecision
from .presence import PresenceLedger, get_or_create_presence_ledger
from .settings import AutonomySettings
from .state import AutonomyState


def _build_observation(client_uid: str, state: AutonomyState) -> Observation:
    """Best-effort snapshot of the world for the planner.

    Signals we can't cheaply obtain are left as None; the planner degrades
    gracefully. `user_present` is true whenever the client is still registered.
    """
    from ..client_registry import get_client_context

    connected = get_client_context(client_uid) is not None
    presence = get_client_presence(client_uid)
    now = datetime.now()
    return Observation(
        idle_seconds=presence.idle_seconds,
        user_present=connected and presence.user_present,
        seconds_since_last_spoke=state.seconds_since_last_spoke(now),
        hour_of_day=now.hour,
        focus_mode=presence.focus_mode,
    )


def _workspace_root() -> "object":
    from ..workspace import ensure_workspace

    return ensure_workspace().root


async def _dispatch(
    client_uid: str,
    decision: PlannerDecision,
    observation: Observation,
    presence: PresenceLedger,
) -> bool:
    """Enqueue the chosen autonomous action for the client (like the scheduler)."""
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

    try:
        receipt = presence.record_action(
            direction=decision.direction.value,
            action=decision.action,
            score=decision.score,
            reason=decision.reason,
            observation=observation,
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
    }
    priority = PRIORITY_PROACTIVE if decision.spoke else PRIORITY_PASSIVE

    async def _run() -> None:
        from ..conversations.single_conversation import process_single_conversation

        await process_single_conversation(
            context=context,
            websocket_send=send_text,
            client_uid=client_uid,
            user_input=decision.prompt,
            metadata=metadata,
        )

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
        queue = get_queue(client_uid)
        await queue.enqueue(
            coro=_run(),
            priority=priority,
            label=f"autonomy:{decision.direction.value}",
        )
        return True
    except Exception:
        presence.cancel_action(receipt.receipt_id)
        raise


async def autonomy_loop(
    client_uid: str,
    settings: Optional[AutonomySettings] = None,
    planner: Optional[AutonomyPlanner] = None,
    presence: Optional[PresenceLedger] = None,
) -> None:
    """Background loop: tick the planner and act on its decisions.

    Safe to launch per client with `asyncio.create_task`; it exits cleanly when
    cancelled (on disconnect).
    """
    settings = settings or AutonomySettings()
    if not settings.enabled:
        logger.info("autonomy: disabled; loop not started.")
        return

    planner = planner or AutonomyPlanner()
    workspace_root = _workspace_root()
    state = AutonomyState.load(workspace_root)
    presence = presence or get_or_create_presence_ledger(
        client_uid, workspace_root, settings
    )
    logger.info(
        f"autonomy: loop started for {client_uid} "
        f"(freq={settings.interruption_frequency}, style={settings.help_style}, "
        f"confidence={settings.confidence_threshold}, tick={settings.tick_seconds}s)"
    )

    while True:
        try:
            await asyncio.sleep(settings.tick_seconds)

            observation = _build_observation(client_uid, state)
            if not observation.user_present:
                from ..client_registry import get_client_context

                if get_client_context(client_uid) is None:
                    logger.info(f"autonomy: client {client_uid} is gone; loop stopped")
                    break
                continue  # connected but marked away by the frontend

            decision = planner.propose_and_choose(
                observation, state, settings, presence=presence
            )
            logger.debug(
                f"autonomy: decision={decision.action} "
                f"direction={decision.direction} score={decision.score:.3f} "
                f"({decision.reason})"
            )

            if not decision.is_act:
                continue

            dispatched = await _dispatch(client_uid, decision, observation, presence)
            if dispatched and decision.direction is not None:
                state.record_action(decision.direction, spoke=decision.spoke)

        except asyncio.CancelledError:
            logger.info(f"autonomy: loop cancelled for {client_uid}")
            break
        except Exception as exc:  # noqa: BLE001 - keep the loop alive on errors
            logger.error(f"autonomy: error in loop for {client_uid}: {exc}")
