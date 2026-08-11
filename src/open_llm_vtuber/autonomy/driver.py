"""Live or headless driver for the persistent global agenda."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import asdict
from dataclasses import replace

from loguru import logger

from ..agent.output_types import DisplayText
from ..chat_history_manager import store_message
from ..conversations.agent_activity import AgentActivityTracker
from ..conversations.types import AgentTurnResult
from ..utils.stream_audio import prepare_audio_payload
from .activity import ActivityRunResult
from .agenda import ExecutionCheckpoint, get_agenda_store
from .agenda_planner import StructuredAgendaPlanner
from .executive import AgendaExecutive, register_executive, unregister_executive
from .intention_conversation import IntentionConversationRunner, IntentionResult
from .idle_relocation import idle_scene_snapshot, relocate_for_idle
from .settings import AutonomySettings
from .tool_schemas import build_tool_schema_manifest, validate_tool_arguments


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
        "and proximity gate. Return a concise execution result. When this is a "
        "care or social intention whose result will be shared, phrase the result "
        "warmly as a companion and, when there is a useful next step, end with one "
        "natural question the user can easily answer.\n"
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
        "synthetic_user": True,
        "report": False,
        "skip_history": True,
        "skip_memory": True,
    }


async def _run_tool_calls(
    tool_executor, calls: list[dict], on_update=None
) -> list[dict]:
    """Run validated tool calls and raise on the first tool-level error.

    ``on_update`` (optional async callable) receives every non-final update so
    callers can surface sanitized tool activity without exposing tool inputs.
    """
    final_results: list[dict] | None = None
    async for update in tool_executor.execute_tools(calls, caller_mode="Prompt"):
        if update.get("type") == "final_tool_results":
            final_results = update.get("results", [])
            continue
        if on_update is not None:
            if update.get("type") == "tool_call_status":
                # Tool inputs must never leave the server; only the caller of
                # execute_tools sees the original update.
                sanitized = dict(update)
                sanitized.pop("content", None)
                await on_update(sanitized)
            else:
                await on_update(update)
    if not final_results:
        raise RuntimeError("Tool execution returned no result.")
    for result in final_results:
        if result.get("is_error"):
            content = str(result.get("content", "")).strip()
            raise RuntimeError(content or "Tool execution failed.")
    return final_results


async def _ensure_tool_proximity(
    tool_executor,
    tool_manager,
    capability: str,
    step_id: str,
    *,
    on_update=None,
) -> None:
    """Move to a tool's required object before execution when it is out of reach.

    The agenda planner is not given the agent's live position, so a validated
    step can name a gated tool while the agent stands elsewhere.  Deterministic
    proximity handling restores the "walk before using a gated tool" property
    without another planner round trip.
    """
    if tool_executor is None or tool_manager is None or not capability:
        return
    required_object = tool_manager.get_required_object(capability)
    if not required_object:
        return
    reachable = set(tool_manager.get_reachable_objects())
    if required_object in reachable:
        return
    await _run_tool_calls(
        tool_executor,
        [
            {
                "id": f"agenda_tool_{step_id}_move",
                "name": "move_to",
                "args": {"target": required_object},
            }
        ],
        on_update=on_update,
    )


async def _execute_validated_tool(
    tool_executor,
    checkpoint,
    *,
    tool_schemas: dict[str, dict] | None = None,
    on_update=None,
) -> str:
    """Execute an already-authorized tool choice without another LLM turn."""
    capability = str(checkpoint.required_capability or "").strip()
    if not capability:
        raise RuntimeError("Validated tool step is missing its capability.")
    if tool_executor is None:
        raise RuntimeError("Validated tool step cannot run without ToolExecutor.")

    if tool_schemas:
        argument_error = validate_tool_arguments(
            capability,
            dict(checkpoint.arguments or {}),
            tool_schemas,
        )
        if argument_error:
            raise RuntimeError(argument_error)
    call = {
        "id": f"agenda_tool_{checkpoint.step_id}",
        "name": capability,
        "args": dict(checkpoint.arguments or {}),
    }
    final_results = await _run_tool_calls(tool_executor, [call], on_update=on_update)
    result = final_results[0]
    content = str(result.get("content", "")).strip()
    return content or f"Tool '{capability}' completed successfully."


async def _publish_completion(
    *,
    send,
    context,
    conf_uid: str,
    entry,
    summary: str,
) -> None:
    """Publish and speak one Presence-approved autonomous result without an LLM."""
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

    spoken_text = result
    translate_engine = getattr(context, "translate_engine", None)
    if translate_engine is not None:
        spoken_text = translate_engine.translate(spoken_text)

    tts_engine = getattr(context, "tts_engine", None)
    if tts_engine is None:
        raise RuntimeError("Autonomous report TTS is unavailable.")
    audio_path = None
    try:
        audio_path = await tts_engine.async_generate_audio(text=spoken_text)
        if not audio_path:
            raise RuntimeError("Autonomous report TTS returned no audio.")
        audio_payload = prepare_audio_payload(
            audio_path=audio_path,
            # The idempotent autonomous-message above owns transcript display.
            # Omitting display text here prevents the audio handler appending a
            # duplicate chat message while still allowing normal playback.
            display_text=None,
            actions=None,
        )
        await send(
            json.dumps(
                {"type": "full-text", "text": result},
                ensure_ascii=False,
            )
        )
        await send(json.dumps(audio_payload, ensure_ascii=False))
    finally:
        if audio_path:
            try:
                tts_engine.remove_file(audio_path)
            except Exception as exc:
                logger.warning(
                    "agenda: could not remove autonomous report audio cache: "
                    f"{type(exc).__name__}"
                )

    # Audio has already reached the frontend, so a lost completion marker must
    # not cause the durable outbox to replay the spoken result.
    try:
        await send(json.dumps({"type": "backend-synth-complete"}))
    except Exception as exc:
        logger.warning(
            "agenda: audio delivered but synthesis completion marker failed: "
            f"{type(exc).__name__}"
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

        activity_tracker = AgentActivityTracker()

        async def forward_tool_update(update: dict) -> None:
            """Surface sanitized tool activity (name/status only, no inputs)."""
            if update.get("type") == "agent_activity":
                await emit(activity_tracker.replace_tools(update.get("tools", [])))
            elif update.get("type") == "tool_call_status":
                await emit(
                    activity_tracker.update_tool(
                        {
                            "tool_id": update.get("tool_id"),
                            "tool_name": update.get("tool_name"),
                            "status": update.get("status"),
                        }
                    )
                )

        async def execute_step():
            if checkpoint.kind == "invoke_tool":
                tool_executor = getattr(context, "tool_executor", None)
                await _ensure_tool_proximity(
                    tool_executor,
                    tool_manager,
                    str(checkpoint.required_capability or "").strip(),
                    checkpoint.step_id,
                    on_update=forward_tool_update,
                )
                return await _execute_validated_tool(
                    tool_executor,
                    checkpoint,
                    tool_schemas=tool_schemas,
                    on_update=forward_tool_update,
                )
            instruction = _agenda_step_instruction(entry, checkpoint)
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
                timeout_stage=(
                    "tool_execution"
                    if checkpoint.kind == "invoke_tool"
                    else "agent_generation"
                ),
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
    tool_schemas = (
        build_tool_schema_manifest(tool_manager.tools)
        if tool_manager is not None
        else {}
    )
    from ..mcpp.tool_manager import (
        PROXIMITY_REQUIREMENTS,
        SERVER_PROXIMITY_REQUIREMENTS,
    )

    object_names = set(PROXIMITY_REQUIREMENTS.values()) | set(
        SERVER_PROXIMITY_REQUIREMENTS.values()
    )
    conf_uid = context.character_config.conf_uid
    scene_mode = getattr(context, "scene_mode", "normal")
    agenda_store = get_agenda_store()
    relocation_seconds: dict[str, float] = {}

    async def run_intention_tool(
        entry_id: str,
        capability: str,
        arguments: dict,
        tool_id: str,
        caller_mode: str,
    ) -> dict:
        """Execute one chain-selected tool behind durable safety boundaries."""
        from ..authorization.profiles import profile_for

        tool_executor = getattr(context, "tool_executor", None)
        if tool_executor is None or tool_manager is None:
            raise RuntimeError("Autonomous intention tools are unavailable.")
        tool = tool_manager.get_tool(capability)
        profile = profile_for(
            capability,
            server_name=getattr(tool, "related_server", None),
        )
        checkpoint = ExecutionCheckpoint(
            step_id=tool_id,
            kind="invoke_tool",
            state="running",
            purpose=f"Private intention conversation selected {capability}.",
            required_object=tool_manager.get_required_object(capability),
            required_capability=capability,
            arguments=dict(arguments),
            timeout_seconds=settings.step_timeout_seconds,
            interruptible=not profile.consequential,
            side_effecting=profile.consequential,
        )
        if (
            agenda_store.set_checkpoint_if_status(entry_id, "active", checkpoint)
            is None
        ):
            raise asyncio.CancelledError

        async def forward(update: dict) -> None:
            if update.get("type") == "tool_call_status":
                update = dict(update)
                update.pop("content", None)
            await emit(update)

        try:
            relocation_started = time.perf_counter()
            await _ensure_tool_proximity(
                tool_executor,
                tool_manager,
                capability,
                tool_id,
                on_update=forward,
            )
            relocation_seconds[entry_id] = relocation_seconds.get(entry_id, 0.0) + (
                time.perf_counter() - relocation_started
            )
            final_results = None
            async for update in tool_executor.execute_tools(
                [{"id": tool_id, "name": capability, "args": arguments}],
                caller_mode=caller_mode,
            ):
                if update.get("type") == "final_tool_results":
                    final_results = update.get("results", [])
                else:
                    await forward(update)
            if not final_results:
                raise RuntimeError("Tool execution returned no result.")
            from .action_context import current_action_scope

            scope = current_action_scope()
            if scope is None or scope.pending_transaction_id is None:
                agenda_store.set_checkpoint_if_status(entry_id, "active", None)
            return final_results[0]
        except asyncio.CancelledError:
            checkpoint.state = "uncertain" if profile.consequential else "planned"
            agenda_store.set_checkpoint_if_status(entry_id, "active", checkpoint)
            raise

    intention_runner = IntentionConversationRunner(
        llm=llm,
        tool_manager=tool_manager,
        run_tool=run_intention_tool,
        model_timeout_seconds=settings.intention_model_timeout_seconds,
        tool_timeout_seconds=settings.step_timeout_seconds,
        max_turns=settings.intention_max_turns,
    )

    async def run_intention(entry) -> IntentionResult:
        from .action_context import agenda_action_scope
        from ..conversations.llm_request_queue import (
            PRIORITY_PASSIVE,
            PRIORITY_SCHEDULED,
            get_queue,
        )

        priority = (
            PRIORITY_SCHEDULED
            if entry.kind == "scheduled" or entry.metadata.get("trigger_foreground")
            else PRIORITY_PASSIVE
        )
        loop = asyncio.get_running_loop()
        queued_at = loop.time()
        execution_started_at: float | None = None

        async def execute():
            nonlocal execution_started_at
            execution_started_at = loop.time()
            with agenda_action_scope(entry.id, f"intention:{entry.id}") as scope:
                result = await intention_runner.run(entry)
                return result, scope

        result, scope = await get_queue(client_uid).enqueue_and_wait(
            coro_factory=execute,
            priority=priority,
            label=f"agenda-intention:{entry.id}",
            dedupe_key=f"agenda-intention:{entry.id}",
            entry_id=entry.id,
            work_class="autonomous",
        )
        finished_at = loop.time()
        started_at = execution_started_at or finished_at
        result = replace(
            result,
            queue_wait_seconds=max(0.0, started_at - queued_at),
            execution_seconds=max(0.0, finished_at - started_at),
            relocation_seconds=relocation_seconds.pop(entry.id, 0.0),
        )
        if scope.pending_transaction_id is not None:
            agenda_store.block_for_authorization(
                entry.id,
                transaction_id=scope.pending_transaction_id,
                capability=scope.capability or "unknown",
                summary=scope.summary,
            )
            return IntentionResult(
                status="blocked_approval",
                summary=scope.summary,
                outcome="approval_required",
                session_id=result.session_id,
                model_turns=result.model_turns,
                tool_calls=result.tool_calls,
                model_seconds=result.model_seconds,
                tool_seconds=result.tool_seconds,
                queue_wait_seconds=result.queue_wait_seconds,
                execution_seconds=result.execution_seconds,
                relocation_seconds=result.relocation_seconds,
            )
        return result

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
        store=agenda_store,
        planner=StructuredAgendaPlanner(
            complete,
            default_pursuit_seconds=settings.default_pursuit_seconds,
        ),
        settings=settings,
        run_step=run_step,
        run_intention=run_intention,
        discard_intention=intention_runner.discard,
        emit=emit,
        capabilities=capabilities,
        object_names=object_names,
        capability_objects=capability_objects,
        tool_schemas=tool_schemas,
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
