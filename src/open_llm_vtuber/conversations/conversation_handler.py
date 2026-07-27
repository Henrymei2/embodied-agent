import asyncio
import json
from typing import Dict, Optional, Callable

import numpy as np
from fastapi import WebSocket
from loguru import logger

from ..chat_group import ChatGroupManager
from ..chat_history_manager import store_message
from ..object_interaction import describe_click
from ..service_context import ServiceContext
from .group_conversation import process_group_conversation
from .single_conversation import process_single_conversation
from .conversation_utils import EMOJI_LIST
from .types import GroupConversationState
from .llm_request_queue import (
    get_queue,
    remove_queue,
    interrupt_queue,
    PRIORITY_USER,
    PRIORITY_PROACTIVE,
)
from prompts import prompt_loader


async def handle_conversation_trigger(
    msg_type: str,
    data: dict,
    client_uid: str,
    context: ServiceContext,
    websocket: WebSocket,
    client_contexts: Dict[str, ServiceContext],
    client_connections: Dict[str, WebSocket],
    chat_group_manager: ChatGroupManager,
    received_data_buffers: Dict[str, np.ndarray],
    current_conversation_tasks: Dict[str, Optional[asyncio.Task]],
    broadcast_to_group: Callable,
) -> None:
    """Handle triggers that start a conversation"""
    metadata = None

    if msg_type == "ai-speak-signal":
        try:
            prompt_name = "proactive_speak_prompt"
            prompt_file = context.system_config.tool_prompts.get(prompt_name)
            if prompt_file:
                user_input = prompt_loader.load_util(prompt_file)
            else:
                logger.warning("Proactive speak prompt not configured, using default")
                user_input = "Please say something."
        except Exception as e:
            logger.error(f"Error loading proactive speak prompt: {e}")
            user_input = "Please say something."

        metadata = {
            "proactive_speak": True,
            "skip_memory": True,
            "skip_history": True,
        }

        await websocket.send_text(
            json.dumps(
                {
                    "type": "full-text",
                    "text": "AI wants to speak something...",
                }
            )
        )
    elif msg_type == "object-interaction":
        # The client sends only which object was clicked; the prompt is built
        # from the stored stage, so this channel cannot inject text.
        interaction = describe_click(
            context.character_config.conf_uid, str(data.get("object_id", ""))
        )
        if interaction is None:
            logger.info("Ignoring a click on an object that is not in the room.")
            return
        user_input = interaction["prompt"]
        # Remembered like any other exchange, but under the phrasing a person
        # would recognise rather than the instructions sent to the model.
        metadata = {"display_text": interaction["display_text"]}

        await websocket.send_text(
            json.dumps(
                {
                    "type": "user-input-transcription",
                    "text": interaction["display_text"],
                }
            )
        )
    elif msg_type == "text-input":
        user_input = data.get("text", "")
    else:  # mic-audio-end
        user_input = received_data_buffers[client_uid]
        received_data_buffers[client_uid] = np.array([])

    images = data.get("images")
    session_emoji = np.random.choice(EMOJI_LIST)

    from ..study import log_turn_started

    # How the turn was started, never what it said. Whether a participant
    # reaches for the room, the microphone, or the text box is exactly the
    # comparison an embodiment condition is trying to make.
    log_turn_started(
        {
            "ai-speak-signal": "autonomous",
            "object-interaction": "object_click",
            "text-input": "text",
        }.get(msg_type, "voice"),
        has_images=bool(images),
    )

    # Determine priority: user-initiated requests beat proactive/background ones
    priority = PRIORITY_PROACTIVE if msg_type == "ai-speak-signal" else PRIORITY_USER

    group = chat_group_manager.get_client_group(client_uid)
    if group and len(group.members) > 1:
        task_key = group.group_id
        if (
            task_key not in current_conversation_tasks
            or current_conversation_tasks[task_key].done()
        ):
            logger.info(f"Starting new group conversation for {task_key}")
            current_conversation_tasks[task_key] = asyncio.create_task(
                process_group_conversation(
                    client_contexts=client_contexts,
                    client_connections=client_connections,
                    broadcast_func=broadcast_to_group,
                    group_members=group.members,
                    initiator_client_uid=client_uid,
                    user_input=user_input,
                    images=images,
                    session_emoji=session_emoji,
                    metadata=metadata,
                )
            )
    else:
        # Route through the priority queue instead of creating a task directly
        queue = get_queue(client_uid)
        agenda_entry = None
        executive = None
        if msg_type != "ai-speak-signal":
            from ..autonomy.executive import get_executive

            executive = get_executive(client_uid)
            if executive is not None:
                if isinstance(user_input, str):
                    title = " ".join(user_input.strip().split())[:120]
                else:
                    title = "Voice request"
                agenda_entry = await executive.preempt_for_user(
                    title or "Help the user"
                )

        async def agenda_finished(failed: bool) -> None:
            if executive is not None and agenda_entry is not None:
                await executive.finish_user(agenda_entry.id, failed=failed)

        conversation = process_single_conversation(
                context=context,
                websocket_send=websocket.send_text,
                client_uid=client_uid,
                user_input=user_input,
                images=images,
                session_emoji=session_emoji,
                metadata=metadata,
            )
        if agenda_entry is None:
            await queue.enqueue(
                coro=conversation,
                priority=priority,
                label=f"{msg_type} ({session_emoji})",
            )
        else:
            await queue.enqueue(
                coro=conversation,
                priority=priority,
                label=f"{msg_type} ({session_emoji})",
                entry_id=agenda_entry.id,
                work_class="user",
                on_finished=agenda_finished,
            )


async def handle_individual_interrupt(
    client_uid: str,
    current_conversation_tasks: Dict[str, Optional[asyncio.Task]],
    context: ServiceContext,
    heard_response: str,
):
    # Interrupt via queue (cancels current in-flight request)
    interrupt_queue(client_uid)

    if client_uid in current_conversation_tasks:
        task = current_conversation_tasks[client_uid]
        if task and not task.done():
            task.cancel()
            logger.info("🛑 Conversation task was successfully interrupted")

    try:
        context.agent_engine.handle_interrupt(heard_response)
    except Exception as e:
        logger.error(f"Error handling interrupt: {e}")

    if context.history_uid:
        store_message(
            conf_uid=context.character_config.conf_uid,
            history_uid=context.history_uid,
            role="ai",
            content=heard_response,
            name=context.character_config.character_name,
            avatar=context.character_config.avatar,
        )
        store_message(
            conf_uid=context.character_config.conf_uid,
            history_uid=context.history_uid,
            role="system",
            content="[Interrupted by user]",
        )


async def handle_client_queue_cleanup(client_uid: str) -> None:
    """Call this when a client disconnects to clean up their queue."""
    remove_queue(client_uid)


async def handle_group_interrupt(
    group_id: str,
    heard_response: str,
    current_conversation_tasks: Dict[str, Optional[asyncio.Task]],
    chat_group_manager: ChatGroupManager,
    client_contexts: Dict[str, ServiceContext],
    broadcast_to_group: Callable,
) -> None:
    """Handles interruption for a group conversation"""
    task = current_conversation_tasks.get(group_id)
    if not task or task.done():
        return

    state = GroupConversationState.get_state(group_id)
    current_speaker_uid = state.current_speaker_uid if state else None

    context = None
    group = chat_group_manager.get_group_by_id(group_id)
    if current_speaker_uid:
        context = client_contexts.get(current_speaker_uid)
        logger.info(f"Found current speaker context for {current_speaker_uid}")
    if not context and group and group.members:
        logger.warning(f"No context found for group {group_id}, using first member")
        context = client_contexts.get(next(iter(group.members)))

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        logger.info(f"🛑 Group conversation {group_id} canceled successfully.")

    current_conversation_tasks.pop(group_id, None)
    GroupConversationState.remove_state(group_id)

    if context and group:
        for member_uid in group.members:
            if member_uid in client_contexts:
                try:
                    member_ctx = client_contexts[member_uid]
                    member_ctx.agent_engine.handle_interrupt(heard_response)
                    store_message(
                        conf_uid=member_ctx.character_config.conf_uid,
                        history_uid=member_ctx.history_uid,
                        role="ai",
                        content=heard_response,
                        name=context.character_config.character_name,
                        avatar=context.character_config.avatar,
                    )
                    store_message(
                        conf_uid=member_ctx.character_config.conf_uid,
                        history_uid=member_ctx.history_uid,
                        role="system",
                        content="[Interrupted by user]",
                    )
                except Exception as e:
                    logger.error(f"Error handling interrupt for {member_uid}: {e}")

    await broadcast_to_group(
        list(group.members),
        {
            "type": "interrupt-signal",
            "text": "conversation-interrupted",
        },
    )
