"""Live or headless driver for the persistent global agenda."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from loguru import logger

from ..agent.output_types import DisplayText
from ..chat_history_manager import store_message
from ..conversations.types import AgentTurnResult
from .activity import ActivityRunResult
from .agenda import get_agenda_store
from .agenda_planner import StructuredAgendaPlanner
from .executive import AgendaExecutive, register_executive, unregister_executive
from .idle_relocation import idle_scene_snapshot, relocate_for_idle
from .settings import AutonomySettings


def _agenda_step_text(result: AgentTurnResult | str) -> str:
    """Translate the conversation result into the activity string contract."""
    if isinstance(result, str):
        return result
    if not isinstance(result, AgentTurnResult):
        raise TypeError(
            f"agenda step returned an unsupported result type: {type(result).__name__}"
        )
    if not result.success:
        raise RuntimeError(result.error or "Agenda conversation step failed.")
    return str(result.response or "")


def _agenda_step_instruction(entry, checkpoint) -> str:
    """Render every validated execution field for the tool-capable agent."""
    return (
        "Execute exactly this validated agenda step. Do not expand its scope. "
        "Use furniture movement and tools normally, respecting every approval "
        "and proximity gate. Return a concise execution result.\n"
        f"Intention: {entry.title}\nCompletion condition: "
        f"{entry.completion_condition.description}\nValidated step: "
        f"{json.dumps(asdict(checkpoint), ensure_ascii=False)}\n"
        f"Purpose: {checkpoint.purpose}"
    )


def _agenda_step_metadata(entry, checkpoint) -> dict:
    """Keep autonomous execution out of user memory while retaining its trace."""
    return {
        "agenda_entry_id": entry.id,
        "agenda_step_id": checkpoint.step_id,
        "autonomous": True,
        "report": False,
        "skip_history": True,
        "skip_memory": True,
    }


async def _publish_completion(
    *,
    send,
    context,
    conf_uid: str,
    entry,
    summary: str,
) -> None:
    """Append one Presence-approved autonomous result to the conversation."""
    result = summary.strip() or "The autonomous task finished."
    message = f"{entry.title}\n\n{result}"[:4000]
    report_key = str(entry.metadata.get("report_delivery_key", "completion"))
    suffix = (
        ""
        if report_key == "completion"
        else ":" + hashlib.sha256(report_key.encode("utf-8")).hexdigest()[:12]
    )
    message_id = f"agenda-report:{entry.id}{suffix}"
    display = DisplayText(
        text=message,
        name=context.character_config.character_name,
        avatar=context.character_config.avatar,
    )
    await send(
        json.dumps(
            {
                "type": "autonomous-message",
                "message_id": message_id,
                "display_text": display.to_dict(),
            },
            ensure_ascii=False,
        )
    )
    if context.history_uid:
        store_message(
            conf_uid=conf_uid,
            history_uid=context.history_uid,
            role="ai",
            content=message,
            name=context.character_config.character_name,
            avatar=context.character_config.avatar,
            message_id=message_id,
        )


async def agenda_loop(
    client_uid: str,
    settings: AutonomySettings | None = None,
    presence=None,
) -> None:
    settings = settings or AutonomySettings()
    if not settings.enabled:
        return

    from ..client_registry import get_client_context, get_client_send
    from ..memory import make_llm_complete
    from .lease import WorkspaceLease, current_lease
    from .runtime import HEADLESS_CLIENT_UID

    context = get_client_context(client_uid)
    send = get_client_send(client_uid)
    connected_client = client_uid != HEADLESS_CLIENT_UID
    lease_handle = current_lease()
    llm = getattr(getattr(context, "agent_engine", None), "_llm", None)
    if context is None or send is None or llm is None:
        logger.warning("agenda: configured agent does not expose a stateless LLM")
        return

    raw_complete = make_llm_complete(llm)

    async def complete(prompt: str) -> str:
        return await raw_complete([{"role": "user", "content": prompt}], "")

    async def emit(payload: dict) -> None:
        current_send = get_client_send(client_uid)
        if current_send is None:
            logger.debug(
                f"agenda: skipped frontend update for disconnected client {client_uid}"
            )
            return
        try:
            await current_send(json.dumps(payload, ensure_ascii=False))
        except RuntimeError as exc:
            # Starlette raises RuntimeError when a disconnect races a final
            # planning/idle update. Runtime ownership cleanup will persist work.
            if "websocket" not in str(exc).lower():
                raise
            logger.debug(
                f"agenda: skipped frontend update after disconnect for {client_uid}"
            )

    async def run_step(entry, checkpoint) -> ActivityRunResult:
        import asyncio

        from .action_context import AgendaApprovalRequired, agenda_action_scope
        from ..conversations.single_conversation import process_single_conversation
        from ..conversations.llm_request_queue import (
            PRIORITY_PASSIVE,
            PRIORITY_SCHEDULED,
            get_queue,
        )

        if lease_handle is not None:
            WorkspaceLease(lease_handle.workspace_root).assert_current(
                lease_handle,
                minimum_remaining_seconds=1,
            )
        instruction = _agenda_step_instruction(entry, checkpoint)

        async def execute_step():
            return await process_single_conversation(
                context=context,
                websocket_send=send,
                client_uid=client_uid,
                user_input=instruction,
                metadata=_agenda_step_metadata(entry, checkpoint),
            )

        priority = (
            PRIORITY_SCHEDULED
            if entry.kind == "scheduled" or entry.metadata.get("trigger_foreground")
            else PRIORITY_PASSIVE
        )
        loop = asyncio.get_running_loop()
        queued_at = loop.time()
        execution_started_at: float | None = None

        def mark_started() -> None:
            nonlocal execution_started_at
            execution_started_at = loop.time()

        with agenda_action_scope(entry.id, checkpoint.step_id) as action_scope:
            result = await get_queue(client_uid).enqueue_and_wait(
                coro_factory=execute_step,
                priority=priority,
                label=f"agenda:{entry.id}:{checkpoint.step_id}",
                dedupe_key=f"agenda:{entry.id}:{checkpoint.step_id}",
                entry_id=entry.id,
                work_class="autonomous",
                execution_timeout_seconds=checkpoint.timeout_seconds,
                on_started=mark_started,
            )
            if action_scope.pending_transaction_id is not None:
                raise AgendaApprovalRequired(action_scope)
        finished_at = loop.time()
        started_at = execution_started_at or finished_at
        return ActivityRunResult(
            summary=_agenda_step_text(result),
            queue_wait_seconds=max(0.0, started_at - queued_at),
            execution_seconds=max(0.0, finished_at - started_at),
        )

    async def run_report(entry, summary: str) -> None:
        from .reporting import ReportClientUnavailable

        if lease_handle is not None:
            WorkspaceLease(lease_handle.workspace_root).assert_current(
                lease_handle,
                minimum_remaining_seconds=1,
            )
        current_send = get_client_send(client_uid)
        if current_send is None:
            raise ReportClientUnavailable("frontend client disconnected")
        await _publish_completion(
            send=current_send,
            context=context,
            conf_uid=context.character_config.conf_uid,
            entry=entry,
            summary=summary,
        )

    tool_manager = getattr(context.agent_engine, "_tool_manager", None)
    capabilities = set(getattr(tool_manager, "tools", {}).keys())
    from ..mcpp.tool_manager import (
        PROXIMITY_REQUIREMENTS,
        SERVER_PROXIMITY_REQUIREMENTS,
    )

    object_names = set(PROXIMITY_REQUIREMENTS.values()) | set(
        SERVER_PROXIMITY_REQUIREMENTS.values()
    )
    conf_uid = context.character_config.conf_uid
    scene_mode = getattr(context, "scene_mode", "normal")

    async def relocate_idle(placement) -> bool:
        if lease_handle is not None:
            WorkspaceLease(lease_handle.workspace_root).assert_current(
                lease_handle,
                minimum_remaining_seconds=1,
            )
        return await relocate_for_idle(
            client_uid=client_uid,
            conf_uid=conf_uid,
            placement=placement,
            scene_mode=scene_mode,
        )

    capability_objects = (
        {
            capability: required
            for capability in capabilities
            if (required := tool_manager.get_required_object(capability)) is not None
        }
        if tool_manager is not None
        else {}
    )
    reporter = None
    if presence is not None:
        from .reporting import AgendaReporter

        reporter = AgendaReporter(client_uid, presence, emit, run_report)

    executive = AgendaExecutive(
        client_uid=client_uid,
        store=get_agenda_store(),
        planner=StructuredAgendaPlanner(
            complete,
            default_pursuit_seconds=settings.default_pursuit_seconds,
        ),
        settings=settings,
        run_step=run_step,
        emit=emit,
        capabilities=capabilities,
        object_names=object_names,
        capability_objects=capability_objects,
        report_result=reporter.deliver if reporter is not None else None,
        connected_client=connected_client,
        lease_handle=lease_handle,
        room_snapshot=(
            (lambda: idle_scene_snapshot(conf_uid, scene_mode))
            if connected_client
            else None
        ),
        relocate_idle=relocate_idle if connected_client else None,
        run_step_enforces_timeout=True,
    )
    register_executive(client_uid, executive)
    logger.info(
        f"agenda: loop started for {client_uid} "
        f"(mode={'live' if connected_client else 'headless'}, "
        f"fallback={settings.fallback_reconsideration_seconds}s, "
        f"proposal_retries={settings.planner_retry_limit})"
    )
    try:
        await executive.run()
    finally:
        unregister_executive(client_uid)
        logger.info(f"agenda: loop stopped for {client_uid}")


# Temporary import name for callers while the internals are replaced in one change.
autonomy_loop = agenda_loop
