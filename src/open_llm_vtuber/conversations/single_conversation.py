from typing import Union, List, Dict, Any, Optional
import asyncio
import json
from loguru import logger
import numpy as np

from .conversation_utils import (
    collect_agent_output,
    create_batch_input,
    process_agent_output,
    send_conversation_start_signals,
    process_user_input,
    finalize_conversation_turn,
    cleanup_conversation,
    EMOJI_LIST,
)
from .types import AgentTurnResult, WebSocketSend
from .tts_manager import TTSTaskManager
from .agent_activity import AgentActivityTracker
from ..chat_history_manager import store_message
from ..service_context import ServiceContext

# Import necessary types from agent outputs
from ..agent.output_types import SentenceOutput, AudioOutput


async def process_single_conversation(
    context: ServiceContext,
    websocket_send: WebSocketSend,
    client_uid: str,
    user_input: Union[str, np.ndarray],
    images: Optional[List[Dict[str, Any]]] = None,
    session_emoji: str = np.random.choice(EMOJI_LIST),
    metadata: Optional[Dict[str, Any]] = None,
) -> AgentTurnResult:
    """Process a single-user conversation turn

    Args:
        context: Service context containing all configurations and engines
        websocket_send: WebSocket send function
        client_uid: Client unique identifier
        user_input: Text or audio input from user
        images: Optional list of image data
        session_emoji: Emoji identifier for the conversation
        metadata: Optional metadata for special processing flags

    Returns:
        AgentTurnResult: Structured completion state and accumulated response
    """
    # Create TTSTaskManager for this conversation
    tts_manager = TTSTaskManager()
    activity = AgentActivityTracker()
    activity_visible = False
    # Timing runs beside the turn, never in front of it: every call is
    # swallowing, so a measurement failure costs a sample and not a reply.
    from ..latency import TurnLatency, client_timings

    latency = TurnLatency(f"{client_uid}:{session_emoji}")
    # `capture` and `endpoint` were measured in the browser before this process
    # saw the turn. Without them the speech-to-first-audio budget cannot be
    # evaluated at all, however fast the rest of the pipeline is.
    latency.adopt_client_timings(client_timings().take(client_uid))
    first_output_seen = False
    tts_requested = False
    full_response = ""  # Initialize full_response here
    report = not metadata or bool(metadata.get("report", True))
    turn_error: Optional[str] = None
    provenance_task: Optional[asyncio.Task] = None
    provenance_bridge = None
    provenance_finished = False

    try:
        # A silent turn still executes the model and tools, but never enters the
        # transcript, TTS pipeline, or frontend conversation lifecycle.
        if report:
            await send_conversation_start_signals(websocket_send)
        logger.info(f"New Conversation Chain {session_emoji} started!")

        # Process user input
        input_text = await process_user_input(
            user_input, context.asr_engine, websocket_send
        )
        latency.complete("asr")

        # Provenance I/O runs beside, not in front of, the latency-sensitive
        # dialogue loop. Raw text goes only to the governed private store; the
        # inspectable ledger receives opaque references and digests.
        skip_memory = bool(metadata and metadata.get("skip_memory", False))
        synthetic_user = bool(metadata and metadata.get("synthetic_user", False))
        if not skip_memory and not synthetic_user:
            from ..study import conditions

            if conditions().memory:
                from ..provenance import get_conversation_provenance_bridge
                from ..workspace import ensure_workspace

                provenance_bridge = get_conversation_provenance_bridge(
                    ensure_workspace().root
                )
                provenance_task = asyncio.create_task(
                    asyncio.to_thread(
                        provenance_bridge.begin_turn,
                        input_text,
                        source_id=client_uid,
                    ),
                    name=f"turn-provenance:{client_uid}",
                )

        # Create batch input
        batch_input = create_batch_input(
            input_text=input_text,
            images=images,
            from_name=context.character_config.human_name,
            metadata=metadata,
        )

        # Store user message (check if we should skip storing to history)
        skip_history = bool(metadata and metadata.get("skip_history", False))
        skip_user_history = skip_history or synthetic_user
        if context.history_uid and not skip_user_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="human",
                # A composed turn (clicking an object) carries the phrasing the
                # transcript should show; the instructions behind it are for the
                # model only.
                content=(metadata or {}).get("display_text") or input_text,
                name=context.character_config.human_name,
            )

        if skip_user_history:
            logger.debug("Skipping synthetic/private user input in chat history")

        if synthetic_user:
            logger.info("Processing an internal companion-initiative prompt")
        else:
            logger.info(f"User input: {input_text}")
        if images:
            logger.info(f"With {len(images)} images")

        try:
            # agent.chat yields Union[SentenceOutput, Dict[str, Any]]
            # Activity is transient UI state and is also shown for silent
            # autonomous turns (report=False), so the head bubble reflects
            # plan execution. It never enters the transcript or TTS pipeline.
            await websocket_send(json.dumps(activity.thinking()))
            activity_visible = True
            # Everything between the transcript and the model request is
            # context assembly: provenance ingestion dispatch, batch input,
            # history. That is what `retrieve` names, and it was the last stage
            # in the budget with no producer.
            latency.complete("retrieve")
            agent_output_stream = context.agent_engine.chat(batch_input)
            latency.complete("plan")

            async for output_item in agent_output_stream:
                if (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "agent_activity"
                ):
                    activity_message = activity.replace_tools(
                        output_item.get("tools", [])
                    )
                    await websocket_send(json.dumps(activity_message))

                elif (
                    isinstance(output_item, dict)
                    and output_item.get("type") == "tool_call_status"
                ):
                    # Handle tool status event: send WebSocket message
                    output_item["name"] = context.character_config.character_name
                    logger.debug(f"Sending tool status update: {output_item}")

                    if report:
                        await websocket_send(json.dumps(output_item))
                    # The raw tool_call_status carries tool inputs and lands in
                    # the frontend chat history. Silent autonomous turns only
                    # get the sanitized activity event (name/status, no inputs).
                    await websocket_send(
                        json.dumps(activity.update_tool(output_item))
                    )

                elif isinstance(output_item, (SentenceOutput, AudioOutput)):
                    if not first_output_seen:
                        # The first speakable output, which is the boundary the
                        # product budget is stated against. Tool calls and
                        # activity frames before it belong to planning.
                        latency.complete("first_token")
                        first_output_seen = True
                    if activity_visible:
                        await websocket_send(json.dumps(activity.idle()))
                        activity_visible = False
                    if report:
                        response_part = await process_agent_output(
                            output=output_item,
                            character_config=context.character_config,
                            tts_engine=context.tts_engine,
                            websocket_send=websocket_send,
                            tts_manager=tts_manager,
                            translate_engine=context.translate_engine,
                        )
                        if not tts_requested:
                            latency.complete("tts_request")
                            tts_requested = True
                    else:
                        response_part = await collect_agent_output(output_item)
                    # Ensure response_part is treated as a string before concatenation
                    response_part_str = (
                        str(response_part) if response_part is not None else ""
                    )
                    full_response += response_part_str  # Accumulate text response
                else:
                    logger.warning(
                        f"Received unexpected item type from agent chat stream: {type(output_item)}"
                    )
                    logger.debug(f"Unexpected item content: {output_item}")

        except Exception as e:
            turn_error = str(e)
            logger.exception(
                f"Error processing agent response stream: {e}"
            )  # Log with stack trace
            if report:
                await websocket_send(
                    json.dumps(
                        {
                            "type": "error",
                            "message": f"Error processing agent response: {str(e)}",
                        }
                    )
                )
            # full_response will contain partial response before error
        # --- End processing agent response ---

        # Wait for any pending TTS tasks
        if report and tts_manager.task_list:
            # First audio is when the *first* synthesis lands, not the last:
            # the companion starts speaking then, which is what the product
            # budget is about. Gathering the whole list afterwards preserves
            # the original exception behaviour exactly.
            _, unfinished = await asyncio.wait(
                set(tts_manager.task_list), return_when=asyncio.FIRST_COMPLETED
            )
            latency.complete("first_audio")
            if unfinished:
                await asyncio.gather(*unfinished)
            await asyncio.gather(*tts_manager.task_list)
            await websocket_send(json.dumps({"type": "backend-synth-complete"}))

        if report:
            await finalize_conversation_turn(
                tts_manager=tts_manager,
                websocket_send=websocket_send,
                client_uid=client_uid,
            )

        # Store the AI response too, unless this turn opted out of history
        # (e.g. proactive/autonomous silent actions). Mirrors the user-message
        # guard above so skip_history keeps both sides out of the chat log.
        if context.history_uid and full_response and not skip_history:
            store_message(
                conf_uid=context.character_config.conf_uid,
                history_uid=context.history_uid,
                role="ai",
                content=full_response,
                name=context.character_config.character_name,
                avatar=context.character_config.avatar,
            )
            logger.info(f"AI response: {full_response}")

        result = AgentTurnResult(
            success=turn_error is None,
            response=full_response,
            error=turn_error,
            reported=report,
        )
        if provenance_task is not None and provenance_bridge is not None:
            try:
                evidence = await provenance_task
                await asyncio.to_thread(
                    provenance_bridge.finish_turn,
                    evidence,
                    success=result.success,
                )
                provenance_finished = True
            except Exception as exc:
                # Memory evidence must not make a live conversation unavailable.
                # The error is visible to operators without logging private text.
                logger.warning(
                    "Conversation provenance could not be persisted: "
                    f"{type(exc).__name__}"
                )
        return result

    except asyncio.CancelledError:
        logger.info(f"🤡👍 Conversation {session_emoji} canceled because interrupted.")
        raise
    except Exception as e:
        logger.error(f"Error in conversation chain: {e}")
        if report:
            await websocket_send(
                json.dumps(
                    {"type": "error", "message": f"Conversation error: {str(e)}"}
                )
            )
        raise
    finally:
        if (
            provenance_task is not None
            and provenance_bridge is not None
            and not provenance_finished
        ):
            try:
                evidence = await provenance_task
                await asyncio.to_thread(
                    provenance_bridge.finish_turn,
                    evidence,
                    success=False,
                )
            except Exception as exc:
                logger.warning(
                    "Interrupted conversation provenance could not be persisted: "
                    f"{type(exc).__name__}"
                )
        try:
            await websocket_send(json.dumps(activity.idle()))
        except Exception:
            logger.debug(
                "Unable to clear agent activity during connection cleanup."
            )
        from ..study import log_turn_finished

        # Closes the turn whether it completed, errored, or was interrupted, so
        # the reply-latency measure is never left waiting on a turn that ended
        # badly. A turn that never reached audio contributes its stage timings
        # and no end-to-end figure, which is the point: an interrupted turn is
        # not a fast turn.
        latency.finish()
        log_turn_finished(len(full_response))
        cleanup_conversation(tts_manager, session_emoji)
