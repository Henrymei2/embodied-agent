"""Connection-scoped driver for the persistent global agenda."""

from __future__ import annotations

import json

from loguru import logger

from .agenda import get_agenda_store
from .agenda_planner import StructuredAgendaPlanner
from .executive import AgendaExecutive, register_executive, unregister_executive
from .settings import AutonomySettings


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

    context = get_client_context(client_uid)
    send = get_client_send(client_uid)
    llm = getattr(getattr(context, "agent_engine", None), "_llm", None)
    if context is None or send is None or llm is None:
        logger.warning("agenda: configured agent does not expose a stateless LLM")
        return

    raw_complete = make_llm_complete(llm)

    async def complete(prompt: str) -> str:
        return await raw_complete([{"role": "user", "content": prompt}], "")

    async def emit(payload: dict) -> None:
        await send(json.dumps(payload, ensure_ascii=False))

    async def run_step(entry, checkpoint) -> str:
        from ..conversations.single_conversation import process_single_conversation

        instruction = (
            "Execute exactly this validated agenda step. Do not expand its scope. "
            "Use furniture movement and tools normally, respecting every approval "
            "and proximity gate. Return a concise execution result.\n"
            f"Intention: {entry.title}\nCompletion condition: "
            f"{entry.completion_condition.description}\nStep: "
            f"{json.dumps(checkpoint.arguments, ensure_ascii=False)}\n"
            f"Purpose: {checkpoint.purpose}"
        )
        return await process_single_conversation(
            context=context,
            websocket_send=send,
            client_uid=client_uid,
            user_input=instruction,
            metadata={
                "agenda_entry_id": entry.id,
                "agenda_step_id": checkpoint.step_id,
                "autonomous": True,
                "report": False,
                "skip_history": True,
                "skip_memory": False,
            },
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
    executive = AgendaExecutive(
        client_uid=client_uid,
        store=get_agenda_store(),
        planner=StructuredAgendaPlanner(complete),
        settings=settings,
        run_step=run_step,
        emit=emit,
        capabilities=capabilities,
        object_names=object_names,
    )
    memory_manager = getattr(context.agent_engine, "_memory_manager", None)
    if memory_manager is not None:
        executive.set_memory_digest_writer(memory_manager.note_daily)
    register_executive(client_uid, executive)
    logger.info(
        f"agenda: loop started for {client_uid} "
        f"(fallback={settings.fallback_reconsideration_seconds}s, "
        f"proposal_retries={settings.planner_retry_limit})"
    )
    try:
        await executive.run()
    finally:
        unregister_executive(client_uid)
        logger.info(f"agenda: loop stopped for {client_uid}")


# Temporary import name for callers while the internals are replaced in one change.
autonomy_loop = agenda_loop
