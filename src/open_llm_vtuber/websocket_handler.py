from typing import Dict, List, Optional, Callable, TypedDict
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import json
import math
import time
from enum import Enum
import numpy as np
from loguru import logger

from .service_context import ServiceContext
from .chat_group import (
    ChatGroupManager,
    handle_group_operation,
    handle_client_disconnect,
    broadcast_to_group,
)
from .message_handler import message_handler
from .utils.stream_audio import prepare_audio_payload
from .chat_history_manager import (
    create_new_history,
    get_history,
    delete_history,
    get_history_list,
)
from .config_manager.utils import scan_bg_directory
from .object_interaction import CLICK_COOLDOWN_SECONDS
from .conversations.conversation_handler import (
    handle_conversation_trigger,
    handle_group_interrupt,
    handle_individual_interrupt,
)


class MessageType(Enum):
    """Enum for WebSocket message types"""

    GROUP = ["add-client-to-group", "remove-client-from-group"]
    HISTORY = [
        "fetch-history-list",
        "fetch-and-set-history",
        "create-new-history",
        "delete-history",
    ]
    CONVERSATION = [
        "mic-audio-end",
        "text-input",
        "ai-speak-signal",
        "object-interaction",
    ]
    CONTROL = ["interrupt-signal", "audio-play-start"]
    DATA = ["mic-audio-data"]


class WSMessage(TypedDict, total=False):
    """Type definition for WebSocket messages"""

    type: str
    action: Optional[str]
    text: Optional[str]
    audio: Optional[List[float]]
    images: Optional[List[str]]
    history_uid: Optional[str]
    file: Optional[str]
    display_text: Optional[dict]


class WebSocketHandler:
    """Handles WebSocket connections and message routing"""

    def __init__(self, default_context_cache: ServiceContext):
        """Initialize the WebSocket handler with default context"""
        self.client_connections: Dict[str, WebSocket] = {}
        self.client_contexts: Dict[str, ServiceContext] = {}
        self.chat_group_manager = ChatGroupManager()
        self.current_conversation_tasks: Dict[str, Optional[asyncio.Task]] = {}
        self.default_context_cache = default_context_cache
        self.received_data_buffers: Dict[str, np.ndarray] = {}
        self.autonomy_tasks: Dict[str, asyncio.Task] = {}
        self.scheduler_tasks: Dict[str, asyncio.Task] = {}
        # (client, object) -> when it was last clicked, for the cooldown.
        self._object_click_times: Dict[tuple[str, str], float] = {}
        self.connection_lock = asyncio.Lock()

        # Message handlers mapping
        self._message_handlers = self._init_message_handlers()

    def _init_message_handlers(self) -> Dict[str, Callable]:
        """Initialize message type to handler mapping"""
        return {
            "add-client-to-group": self._handle_group_operation,
            "remove-client-from-group": self._handle_group_operation,
            "request-group-info": self._handle_group_info,
            "fetch-history-list": self._handle_history_list_request,
            "fetch-and-set-history": self._handle_fetch_history,
            "create-new-history": self._handle_create_history,
            "delete-history": self._handle_delete_history,
            "interrupt-signal": self._handle_interrupt,
            "mic-audio-data": self._handle_audio_data,
            "mic-audio-end": self._handle_conversation_trigger,
            "raw-audio-data": self._handle_raw_audio_data,
            "text-input": self._handle_conversation_trigger,
            "object-interaction": self._handle_object_interaction,
            "ai-speak-signal": self._handle_conversation_trigger,
            "fetch-backgrounds": self._handle_fetch_backgrounds,
            "audio-play-start": self._handle_audio_play_start,
            "request-init-config": self._handle_init_config_request,
            "heartbeat": self._handle_heartbeat,
            "frontend_action_result": self._handle_frontend_action_result,
            "presence-context": self._handle_presence_context,
            "screen-context": self._handle_screen_context,
            "vr-user-pose": self._handle_vr_user_pose,
            "vr-physical-events": self._handle_vr_physical_events,
            "vr-scenario-get-state": self._handle_vr_scenario_get_state,
            "vr-scenario-start": self._handle_vr_scenario_start,
            "vr-scenario-cancel": self._handle_vr_scenario_cancel,
            "autonomy-feedback": self._handle_autonomy_feedback,
            "request-presence-state": self._handle_presence_state_request,
            "turn-timing": self._handle_turn_timing,
        }

    async def _handle_turn_timing(
        self,
        websocket: WebSocket,
        client_uid: str,
        data: WSMessage,
    ) -> None:
        """Take the browser's capture and endpoint timings for the next turn.

        `capture` and `endpoint` happen before the server sees the turn, so
        without this message three of the seven stages in the speech-to-first-
        audio budget have no producer and the product figure is `unavailable`
        however fast the system actually is.

        Nothing here can fail the connection: an implausible or malformed
        timing is dropped and the stage stays absent, which the latency report
        already knows how to say.
        """
        from .latency import client_timings, parse_client_timings

        client_timings().offer(client_uid, parse_client_timings(data))

    async def handle_new_connection(
        self,
        websocket: WebSocket,
        client_uid: str,
        scene_mode: str = "normal",
    ) -> None:
        """
        Handle new WebSocket connection setup

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client

        Raises:
            Exception: If initialization fails
        """
        try:
            session_service_context = await self._init_service_context(
                websocket.send_text,
                client_uid,
                scene_mode=scene_mode,
            )

            if scene_mode == "vr":
                from .browser_session import clear_browser_session

                # A fresh VR session must not open the computer panel onto
                # browser activity left behind by an earlier session.
                clear_browser_session(client_uid)

            await self._store_client_data(
                websocket, client_uid, session_service_context
            )

            await self._send_initial_messages(
                websocket, client_uid, session_service_context
            )

            logger.info(f"Connection established for client {client_uid}")

        except Exception as e:
            logger.error(
                f"Failed to initialize connection for client {client_uid}: {e}"
            )
            await self._cleanup_failed_connection(client_uid)
            raise

    def can_connect(self, scene_mode: str, conf_uid: str | None) -> tuple[bool, str]:
        """Enforce exclusive same-character ownership while a VR client exists."""
        matching = [
            context
            for context in self.client_contexts.values()
            if getattr(getattr(context, "character_config", None), "conf_uid", None)
            == conf_uid
        ]
        if scene_mode == "vr" and matching:
            return False, "Close the other client before entering VR."
        if scene_mode == "normal" and any(
            getattr(context, "scene_mode", "normal") == "vr" for context in matching
        ):
            return False, "Close the VR client before opening another client."
        return True, ""

    async def _store_client_data(
        self,
        websocket: WebSocket,
        client_uid: str,
        session_service_context: ServiceContext,
    ):
        """Store client data and initialize group status"""
        self.client_connections[client_uid] = websocket
        self.client_contexts[client_uid] = session_service_context
        self.received_data_buffers[client_uid] = np.array([])

        from .frontend_bridge import register_client_sender

        register_client_sender(
            client_uid,
            websocket.send_text,
            session_service_context.character_config.conf_uid,
            scene_mode=session_service_context.scene_mode,
        )
        from .client_registry import register_client

        register_client(client_uid, session_service_context, websocket.send_text)
        # The agenda owns both continuous behavior and reliable due-time claims.
        # There is deliberately no independent scheduler loop.
        from .autonomy import AutonomySettings, attach_autonomy_client
        from .autonomy.presence import get_or_create_presence_ledger
        from .workspace import ensure_workspace

        autonomy_settings = AutonomySettings.from_config(
            getattr(session_service_context, "system_config", None)
        )
        if autonomy_settings.enabled:
            presence = get_or_create_presence_ledger(
                client_uid, ensure_workspace().root, autonomy_settings
            )
            await attach_autonomy_client(
                client_uid,
                ensure_workspace().root,
                autonomy_settings,
                presence,
            )
            from .autonomy.welcome_back import register_welcome_session

            register_welcome_session(client_uid, autonomy_settings)

        self.chat_group_manager.client_group_map[client_uid] = ""
        await self.send_group_update(websocket, client_uid)

    async def _send_initial_messages(
        self,
        websocket: WebSocket,
        client_uid: str,
        session_service_context: ServiceContext,
    ):
        """Send initial connection messages to the client"""
        await websocket.send_text(
            json.dumps({"type": "full-text", "text": "Connection established"})
        )

        from .study import conditions, log_event

        log_event("client_connected")
        # The frontend needs this before it renders: an embodiment ablation
        # that still drew the room would not be an ablation.
        await websocket.send_text(
            json.dumps(
                {
                    "type": "study-condition",
                    "condition": conditions().label,
                    **conditions().as_dict(),
                }
            )
        )

        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "conf_name": session_service_context.character_config.conf_name,
                    "conf_uid": session_service_context.character_config.conf_uid,
                    "client_uid": client_uid,
                }
            )
        )

        # Send initial group status
        await self.send_group_update(websocket, client_uid)

        # Start microphone
        await websocket.send_text(json.dumps({"type": "control", "text": "start-mic"}))

        from .autonomy.presence import get_presence_ledger

        presence = get_presence_ledger(client_uid)
        if presence is not None:
            await websocket.send_text(json.dumps(presence.snapshot()))

        from .scene_state import load_scene_state

        await websocket.send_text(
            json.dumps(
                {
                    "type": (
                        "vr-stage-state"
                        if session_service_context.scene_mode == "vr"
                        else "stage-state"
                    ),
                    "stage": load_scene_state(
                        session_service_context.character_config.conf_uid,
                        session_service_context.scene_mode,
                    ),
                }
            )
        )

        if session_service_context.scene_mode == "vr":
            from .autonomy.agenda import get_agenda_store
            from .demo_scenarios import scenario_state_payload

            await websocket.send_text(
                json.dumps(scenario_state_payload(get_agenda_store()))
            )

    async def _handle_vr_scenario_get_state(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        from .autonomy.agenda import get_agenda_store
        from .demo_scenarios import scenario_state_payload

        context = self.client_contexts[client_uid]
        error = (
            None
            if context.scene_mode == "vr"
            else "VR scenarios are only available in immersive mode."
        )
        await websocket.send_text(
            json.dumps(scenario_state_payload(get_agenda_store(), error=error))
        )

    async def _handle_vr_scenario_start(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        from .autonomy.agenda import get_agenda_store
        from .autonomy.executive import get_executive
        from .demo_scenarios import (
            ScenarioConflictError,
            scenario_state_payload,
            start_scenario,
        )
        from .scene_state import load_scene_state

        context = self.client_contexts[client_uid]
        error = None
        if context.scene_mode != "vr":
            error = "VR scenarios are only available in immersive mode."
        elif data.get("version") != 1:
            error = "Unsupported VR scenario protocol version."
        else:
            try:
                await start_scenario(
                    scenario_id=str(data.get("scenario_id") or ""),
                    request_id=str(data.get("request_id") or ""),
                    conf_uid=context.character_config.conf_uid,
                    agenda=get_agenda_store(),
                    executive=get_executive(client_uid),
                )
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "vr-stage-state",
                            "stage": load_scene_state(
                                context.character_config.conf_uid, "vr"
                            ),
                            "reset_transients": True,
                        }
                    )
                )
            except (OSError, ValueError, ScenarioConflictError) as exc:
                error = str(exc)
        await websocket.send_text(
            json.dumps(scenario_state_payload(get_agenda_store(), error=error))
        )

    async def _handle_vr_scenario_cancel(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        from .autonomy.agenda import get_agenda_store
        from .autonomy.executive import get_executive
        from .demo_scenarios import (
            ScenarioConflictError,
            cancel_scenario,
            scenario_state_payload,
        )

        context = self.client_contexts[client_uid]
        error = None
        if context.scene_mode != "vr":
            error = "VR scenarios are only available in immersive mode."
        elif data.get("version") != 1:
            error = "Unsupported VR scenario protocol version."
        else:
            try:
                await cancel_scenario(
                    run_id=str(data.get("run_id") or ""),
                    request_id=str(data.get("request_id") or ""),
                    agenda=get_agenda_store(),
                    executive=get_executive(client_uid),
                )
            except (OSError, ValueError, ScenarioConflictError) as exc:
                error = str(exc)
        await websocket.send_text(
            json.dumps(scenario_state_payload(get_agenda_store(), error=error))
        )

    async def _init_service_context(
        self,
        send_text: Callable,
        client_uid: str,
        scene_mode: str = "normal",
    ) -> ServiceContext:
        """Initialize service context for a new session by cloning the default context"""
        session_service_context = ServiceContext()
        try:
            await session_service_context.load_cache(
                config=self.default_context_cache.config.model_copy(deep=True),
                system_config=self.default_context_cache.system_config.model_copy(
                    deep=True
                ),
                character_config=self.default_context_cache.character_config.model_copy(
                    deep=True
                ),
                asr_engine=self.default_context_cache.asr_engine,
                tts_engine=self.default_context_cache.tts_engine,
                vad_engine=self.default_context_cache.vad_engine,
                agent_engine=self.default_context_cache.agent_engine,
                translate_engine=self.default_context_cache.translate_engine,
                mcp_server_registery=self.default_context_cache.mcp_server_registery,
                tool_adapter=self.default_context_cache.tool_adapter,
                send_text=send_text,
                client_uid=client_uid,
                scene_mode=scene_mode,
            )
        except BaseException:
            try:
                await session_service_context.close()
            except Exception as exc:  # noqa: BLE001 - preserve initialization failure
                logger.warning(
                    f"Error closing partially initialized context for {client_uid}: {exc}"
                )
            raise
        return session_service_context

    async def handle_websocket_communication(
        self, websocket: WebSocket, client_uid: str
    ) -> None:
        """
        Handle ongoing WebSocket communication

        Args:
            websocket: The WebSocket connection
            client_uid: Unique identifier for the client
        """
        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    message_handler.handle_message(client_uid, data)
                    await self._route_message(websocket, client_uid, data)
                except WebSocketDisconnect:
                    raise
                except json.JSONDecodeError:
                    logger.error("Invalid JSON received")
                    continue
                except Exception as e:
                    logger.error(f"Error processing message: {e}")
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": str(e)})
                    )
                    continue

        except WebSocketDisconnect:
            logger.info(f"Client {client_uid} disconnected")
            raise
        except Exception as e:
            logger.error(f"Fatal error in WebSocket communication: {e}")
            raise

    async def _route_message(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """
        Route incoming message to appropriate handler

        Args:
            websocket: The WebSocket connection
            client_uid: Client identifier
            data: Message data
        """
        msg_type = data.get("type")
        if not msg_type:
            logger.warning("Message received without type")
            return

        if msg_type in {"text-input", "mic-audio-end", "object-interaction"}:
            from .autonomy.observation import mark_user_activity
            from .autonomy.welcome_back import cancel_welcome_for_user

            cancel_welcome_for_user(client_uid)
            mark_user_activity(client_uid)

        handler = self._message_handlers.get(msg_type)
        if handler:
            await handler(websocket, client_uid, data)
        else:
            if msg_type != "frontend-playback-complete":
                logger.warning(f"Unknown message type: {msg_type}")

    async def _handle_group_operation(
        self, websocket: WebSocket, client_uid: str, data: dict
    ) -> None:
        """Handle group-related operations"""
        operation = data.get("type")
        target_uid = data.get(
            "invitee_uid" if operation == "add-client-to-group" else "target_uid"
        )

        await handle_group_operation(
            operation=operation,
            client_uid=client_uid,
            target_uid=target_uid,
            chat_group_manager=self.chat_group_manager,
            client_connections=self.client_connections,
            send_group_update=self.send_group_update,
        )

    @staticmethod
    async def _cancel_and_await_tasks(
        client_uid: str,
        named_tasks: list[tuple[str, asyncio.Task | None]],
    ) -> None:
        """Cancel connection-owned tasks and wait for their finalizers."""

        tasks = [(name, task) for name, task in named_tasks if task is not None]
        for _name, task in tasks:
            if not task.done():
                task.cancel()

        if not tasks:
            return

        results = await asyncio.gather(
            *(task for _name, task in tasks),
            return_exceptions=True,
        )
        for (name, _task), result in zip(tasks, results):
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.warning(
                    f"{name} task cleanup failed for client {client_uid}: {result}"
                )

    async def handle_disconnect(self, client_uid: str) -> None:
        """Handle client disconnection"""
        try:
            # Pending client timings are per-connection and must not outlive it.
            # An unbounded per-client dictionary is the same growth defect slice
            # 7.2 had to fix in the recorder, arriving one connection at a time.
            from .latency import client_timings

            client_timings().forget(client_uid)
        except Exception as exc:  # noqa: BLE001 - cleanup must continue
            logger.debug(f"Failed clearing client timings for {client_uid}: {exc}")
        try:
            group = self.chat_group_manager.get_client_group(client_uid)
        except Exception as exc:  # noqa: BLE001 - cleanup must still detach resources
            logger.warning(f"Failed reading group for disconnect {client_uid}: {exc}")
            group = None
        if group:
            try:
                await handle_group_interrupt(
                    group_id=group.group_id,
                    heard_response="",
                    current_conversation_tasks=self.current_conversation_tasks,
                    chat_group_manager=self.chat_group_manager,
                    client_contexts=self.client_contexts,
                    broadcast_to_group=self.broadcast_to_group,
                )
            except Exception as exc:  # noqa: BLE001 - disconnect cleanup must continue
                logger.warning(f"Failed interrupting group for {client_uid}: {exc}")

        try:
            await handle_client_disconnect(
                client_uid=client_uid,
                chat_group_manager=self.chat_group_manager,
                client_connections=self.client_connections,
                send_group_update=self.send_group_update,
            )
        except Exception as exc:  # noqa: BLE001 - peer notification is best-effort
            logger.warning(
                f"Failed notifying group of disconnect for {client_uid}: {exc}"
            )

        from .study import end_session, log_event

        try:
            log_event("client_disconnected")
        except Exception as exc:  # noqa: BLE001 - telemetry must not block cleanup
            logger.warning(f"Failed logging disconnect for {client_uid}: {exc}")

        await self._cleanup_failed_connection(client_uid)

        if not self.client_connections:
            # A study session is one sitting with one participant, so it closes
            # when the last window does rather than on every reconnect.
            try:
                end_session()
            except Exception as exc:  # noqa: BLE001 - resources are already detached
                logger.warning(f"Failed ending study session: {exc}")
        # Click times are keyed per (client, object), so a reconnecting client
        # would otherwise leave one entry per object it ever touched.
        for key in [key for key in self._object_click_times if key[0] == client_uid]:
            del self._object_click_times[key]

        logger.info(f"Client {client_uid} disconnected")

    async def _cleanup_failed_connection(self, client_uid: str) -> None:
        """Detach and best-effort clean every resource owned by a connection."""
        self.client_connections.pop(client_uid, None)
        context = self.client_contexts.pop(client_uid, None)
        self.received_data_buffers.pop(client_uid, None)
        from .autonomy import detach_autonomy_client
        from .client_registry import unregister_client
        from .frontend_bridge import unregister_client_sender

        # Mark the transport unavailable before cancelling autonomy. Cancellation
        # finalizers may emit planning/idle updates and must observe disconnection.
        unregister_client_sender(client_uid)
        unregister_client(client_uid)

        try:
            await detach_autonomy_client(client_uid)
        except Exception as exc:  # noqa: BLE001 - cleanup must continue
            logger.warning(f"Failed detaching autonomy runtime for {client_uid}: {exc}")

        # Every step below is independent, and the context close at the end is
        # the one that releases MCP subprocesses. Nothing here may raise past
        # it: a step that throws would strand those for the life of the server.
        try:
            await self._cancel_and_await_tasks(
                client_uid,
                [
                    # Kept for compatibility with callers that attached a
                    # connection-local task before the workspace runtime.
                    ("autonomy", self.autonomy_tasks.pop(client_uid, None)),
                    ("scheduler", self.scheduler_tasks.pop(client_uid, None)),
                    (
                        "conversation",
                        self.current_conversation_tasks.pop(client_uid, None),
                    ),
                ],
            )
        except Exception as exc:  # noqa: BLE001 - continue remaining cleanup
            logger.warning(f"Failed cancelling tasks for {client_uid}: {exc}")

        # Mirror handle_disconnect so a connection that fails mid-setup doesn't
        # leak the queue/worker, bridge sender, client registry, or context.
        from .autonomy.observation import clear_client_presence
        from .autonomy.presence import unregister_presence_ledger
        from .autonomy.screen_context import clear_screen_context
        from .autonomy.welcome_back import unregister_welcome_session
        from .browser_session import clear_browser_session
        from .conversations.llm_request_queue import remove_queue
        from .physical_context import clear_physical_context

        cleanup_steps = [
            (
                "group registration",
                getattr(self.chat_group_manager, "remove_client", None),
            ),
            # After `remove_client`, which reads this map to find the group.
            (
                "group map",
                lambda uid: self.chat_group_manager.client_group_map.pop(uid, None),
            ),
            ("welcome-back session", unregister_welcome_session),
            ("presence observation", clear_client_presence),
            ("screen context", clear_screen_context),
            ("VR physical context", clear_physical_context),
            ("VR browser session", clear_browser_session),
            ("presence ledger", unregister_presence_ledger),
            ("request queue", remove_queue),
            ("message handler", message_handler.cleanup_client),
        ]
        for name, cleanup in cleanup_steps:
            if cleanup is None:
                continue
            try:
                cleanup(client_uid)
            except Exception as exc:  # noqa: BLE001 - continue remaining cleanup
                logger.warning(f"Failed cleaning {name} for {client_uid}: {exc}")

        if context:
            try:
                await context.close()
            except Exception as exc:  # noqa: BLE001 - best-effort cleanup
                logger.warning(f"Error closing context for failed {client_uid}: {exc}")

    async def broadcast_to_group(
        self, group_members: list[str], message: dict, exclude_uid: str = None
    ) -> None:
        """Broadcasts a message to group members"""
        await broadcast_to_group(
            group_members=group_members,
            message=message,
            client_connections=self.client_connections,
            exclude_uid=exclude_uid,
        )

    async def send_group_update(self, websocket: WebSocket, client_uid: str):
        """Sends group information to a client"""
        group = self.chat_group_manager.get_client_group(client_uid)
        if group:
            current_members = self.chat_group_manager.get_group_members(client_uid)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "group-update",
                        "members": current_members,
                        "is_owner": group.owner_uid == client_uid,
                    }
                )
            )
        else:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "group-update",
                        "members": [],
                        "is_owner": False,
                    }
                )
            )

    async def _handle_interrupt(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle conversation interruption"""
        heard_response = data.get("text", "")
        context = self.client_contexts[client_uid]
        group = self.chat_group_manager.get_client_group(client_uid)

        if group and len(group.members) > 1:
            await handle_group_interrupt(
                group_id=group.group_id,
                heard_response=heard_response,
                current_conversation_tasks=self.current_conversation_tasks,
                chat_group_manager=self.chat_group_manager,
                client_contexts=self.client_contexts,
                broadcast_to_group=self.broadcast_to_group,
            )
        else:
            await handle_individual_interrupt(
                client_uid=client_uid,
                current_conversation_tasks=self.current_conversation_tasks,
                context=context,
                heard_response=heard_response,
            )

    async def _handle_history_list_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for chat history list"""
        context = self.client_contexts[client_uid]
        histories = get_history_list(context.character_config.conf_uid)
        await websocket.send_text(
            json.dumps({"type": "history-list", "histories": histories})
        )

    async def _handle_fetch_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle fetching and setting specific chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        # Update history_uid in service context
        context.history_uid = history_uid
        context.agent_engine.set_memory_from_history(
            conf_uid=context.character_config.conf_uid,
            history_uid=history_uid,
        )

        messages = [
            msg
            for msg in get_history(
                context.character_config.conf_uid,
                history_uid,
            )
            if msg["role"] != "system"
        ]
        await websocket.send_text(
            json.dumps({"type": "history-data", "messages": messages})
        )
        from .autonomy.welcome_back import maybe_queue_welcome

        await maybe_queue_welcome(
            client_uid,
            context=context,
            send=websocket.send_text,
        )

    async def _handle_create_history(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle creation of new chat history"""
        context = self.client_contexts[client_uid]
        history_uid = create_new_history(context.character_config.conf_uid)
        if history_uid:
            context.history_uid = history_uid
            context.agent_engine.set_memory_from_history(
                conf_uid=context.character_config.conf_uid,
                history_uid=history_uid,
            )
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "new-history-created",
                        "history_uid": history_uid,
                    }
                )
            )
            from .autonomy.welcome_back import maybe_queue_welcome

            await maybe_queue_welcome(
                client_uid,
                context=context,
                send=websocket.send_text,
            )

    async def _handle_delete_history(
        self, websocket: WebSocket, client_uid: str, data: dict
    ):
        """Handle deletion of chat history"""
        history_uid = data.get("history_uid")
        if not history_uid:
            return

        context = self.client_contexts[client_uid]
        success = delete_history(
            context.character_config.conf_uid,
            history_uid,
        )
        await websocket.send_text(
            json.dumps(
                {
                    "type": "history-deleted",
                    "success": success,
                    "history_uid": history_uid,
                }
            )
        )
        if history_uid == context.history_uid:
            context.history_uid = None

    async def _handle_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming audio data"""
        audio_data = data.get("audio", [])
        if audio_data:
            self.received_data_buffers[client_uid] = np.append(
                self.received_data_buffers[client_uid],
                np.array(audio_data, dtype=np.float32),
            )

    async def _handle_raw_audio_data(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle incoming raw audio data for VAD processing"""
        context = self.client_contexts[client_uid]
        chunk = data.get("audio", [])
        if chunk:
            for audio_bytes in context.vad_engine.detect_speech(chunk):
                if audio_bytes == b"<|PAUSE|>":
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "interrupt"})
                    )
                elif audio_bytes == b"<|RESUME|>":
                    pass
                elif len(audio_bytes) > 1024:
                    # Detected audio activity (voice)
                    self.received_data_buffers[client_uid] = np.append(
                        self.received_data_buffers[client_uid],
                        np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32),
                    )
                    await websocket.send_text(
                        json.dumps({"type": "control", "text": "mic-audio-end"})
                    )

    async def _handle_conversation_trigger(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle triggers that start a conversation"""
        await handle_conversation_trigger(
            msg_type=data.get("type", ""),
            data=data,
            client_uid=client_uid,
            context=self.client_contexts[client_uid],
            websocket=websocket,
            client_contexts=self.client_contexts,
            client_connections=self.client_connections,
            chat_group_manager=self.chat_group_manager,
            received_data_buffers=self.received_data_buffers,
            current_conversation_tasks=self.current_conversation_tasks,
            broadcast_to_group=self.broadcast_to_group,
        )

    async def _handle_object_interaction(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle the user clicking an object in the room.

        Rate-limited per object: a room is a thing people poke at, and every
        click costs an LLM turn. Repeated taps on the same object inside the
        cooldown are dropped rather than queued, so an impatient user does not
        end up with five replies about the bookshelf.
        """
        object_id = str(data.get("object_id", ""))
        now = time.monotonic()
        key = (client_uid, object_id)
        last_click = self._object_click_times.get(key, 0.0)
        if now - last_click < CLICK_COOLDOWN_SECONDS:
            logger.debug(f"Ignoring a repeat click on {object_id!r}.")
            return
        self._object_click_times[key] = now

        from .study import log_event

        # Reaching into the room is the embodiment pillar's clearest behavioral
        # signal: a diorama gets looked at, a place gets touched.
        log_event("object_click", object_id=object_id)

        await self._handle_conversation_trigger(websocket, client_uid, data)

    async def _handle_fetch_backgrounds(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle fetching available background images"""
        bg_files = scan_bg_directory()
        await websocket.send_text(
            json.dumps({"type": "background-files", "files": bg_files})
        )

    async def _handle_audio_play_start(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """
        Handle audio playback start notification
        """
        group_members = self.chat_group_manager.get_group_members(client_uid)
        if len(group_members) > 1:
            display_text = data.get("display_text")
            if display_text:
                silent_payload = prepare_audio_payload(
                    audio_path=None,
                    display_text=display_text,
                    actions=None,
                    forwarded=True,
                )
                await self.broadcast_to_group(
                    group_members, silent_payload, exclude_uid=client_uid
                )

    async def _handle_group_info(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle group info request"""
        await self.send_group_update(websocket, client_uid)

    async def _handle_init_config_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle request for initialization configuration"""
        context = self.client_contexts.get(client_uid)
        if not context:
            context = self.default_context_cache

        await websocket.send_text(
            json.dumps(
                {
                    "type": "set-model-and-conf",
                    "conf_name": context.character_config.conf_name,
                    "conf_uid": context.character_config.conf_uid,
                    "client_uid": client_uid,
                }
            )
        )

    async def _handle_heartbeat(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle heartbeat messages from clients"""
        try:
            await websocket.send_json({"type": "heartbeat-ack"})
        except Exception as e:
            logger.error(f"Error sending heartbeat acknowledgment: {e}")

    async def _handle_frontend_action_result(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Handle a frontend_action_result reply, completing a pending move_to/etc."""
        from .frontend_bridge import (
            owns_pending_request,
            pending_request_action,
            resolve_action_result,
        )

        request_id = data.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            logger.warning("frontend-action-result received without request_id")
            return
        raw_result = data.get("result")
        if not isinstance(raw_result, dict):
            error = "frontend action result must be an object"
            resolve_action_result(
                request_id,
                False,
                {"ok": False, "error": error},
                error,
                client_uid=client_uid,
            )
            return
        result = raw_result
        ok = bool(result.get("ok", False))
        error = result.get("error")
        pending_action = pending_request_action(request_id, client_uid)
        if ok and pending_action in {"move_to", "move_to_user"}:
            position = result.get("position")

            def finite_number(value: object) -> bool:
                return (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and math.isfinite(value)
                )

            valid_position = (
                isinstance(position, dict)
                and finite_number(position.get("x"))
                and finite_number(position.get("y"))
            )
            valid_duration = finite_number(result.get("duration_ms"))
            if (
                result.get("arrived") is not True
                or not valid_position
                or not valid_duration
                or not isinstance(result.get("state"), dict)
            ):
                ok = False
                error = "move_to result is missing verified arrival data"
                result = {**result, "ok": False, "error": error}
        logger.info(
            "frontend action result received "
            f"request={request_id} client={client_uid} ok={ok}"
        )
        if (
            ok
            and owns_pending_request(request_id, client_uid)
            and isinstance(result.get("state"), dict)
        ):
            from .scene_state import load_scene_state, save_scene_state

            context = self.client_contexts.get(client_uid)
            if context is None:
                ok = False
                error = "client context is no longer available"
            else:
                saved, errors = save_scene_state(
                    context.character_config.conf_uid,
                    result["state"],
                    mode=getattr(context, "scene_mode", "normal"),
                    updated_by="frontend_action",
                )
                if not saved:
                    ok = False
                    error = "stage state was rejected: " + "; ".join(errors)
                    result = {**result, "ok": False, "error": error}
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": (
                                    "vr-stage-state"
                                    if getattr(context, "scene_mode", "normal") == "vr"
                                    else "stage-state"
                                ),
                                "stage": load_scene_state(
                                    context.character_config.conf_uid,
                                    getattr(context, "scene_mode", "normal"),
                                ),
                            }
                        )
                    )
                else:
                    logger.info(
                        "frontend action state persisted "
                        f"request={request_id} mode={getattr(context, 'scene_mode', 'normal')}"
                    )
        if not ok and result.get("ok") is not False:
            result = {**result, "ok": False, "error": error}
        resolve_action_result(
            request_id,
            ok,
            result,
            error,
            client_uid=client_uid,
        )

    async def _handle_presence_context(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Accept coarse activity/focus signals without screen or app contents."""
        from .autonomy.observation import update_client_presence
        from .autonomy.presence import get_presence_ledger

        try:
            signal = update_client_presence(client_uid, data)
        except ValueError as exc:
            logger.warning(
                f"Rejected invalid presence context from {client_uid}: {exc}"
            )
            await websocket.send_text(
                json.dumps({"type": "error", "message": str(exc)})
            )
            return
        presence = get_presence_ledger(client_uid)
        if presence is not None:
            await websocket.send_text(json.dumps(presence.snapshot()))
        else:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "presence-state",
                        "enabled": False,
                        "receipts": [],
                    }
                )
            )
        from .autonomy.welcome_back import observe_welcome_presence

        await observe_welcome_presence(
            client_uid,
            user_present=signal.user_present,
            context=self.client_contexts[client_uid],
            send=websocket.send_text,
        )

    async def _handle_screen_context(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Accept a coarse screen-context push for live autonomy decisions.

        Only short labels (foreground app, window/document title, idle time) are
        accepted; raw screen contents are rejected by the validator. This
        transient observation is not copied into the user's memory files.
        """
        from .autonomy.screen_context import update_screen_context

        try:
            update_screen_context(client_uid, data)
        except ValueError as exc:
            logger.warning(f"Rejected invalid screen context from {client_uid}: {exc}")
            await websocket.send_text(
                json.dumps({"type": "error", "message": str(exc)})
            )
            return

    async def _handle_vr_user_pose(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Accept a bounded, ephemeral headset pose from the owning VR client."""
        context = self.client_contexts.get(client_uid)
        if context is None or getattr(context, "scene_mode", "normal") != "vr":
            await websocket.send_json(
                {
                    "type": "vr-physical-result",
                    "ok": False,
                    "error": "VR pose is only accepted from a VR session.",
                }
            )
            return
        from .physical_context import update_user_pose

        try:
            pose = update_user_pose(
                client_uid, context.character_config.conf_uid, data.get("pose")
            )
        except ValueError as exc:
            await websocket.send_json(
                {"type": "vr-physical-result", "ok": False, "error": str(exc)}
            )
            return
        await websocket.send_json(
            {"type": "vr-user-pose-ack", "sequence": pose.sequence}
        )

    async def _handle_vr_physical_events(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Validate discrete VR observations and authoritatively save prop releases."""
        context = self.client_contexts.get(client_uid)
        if context is None or getattr(context, "scene_mode", "normal") != "vr":
            await websocket.send_json(
                {
                    "type": "vr-physical-result",
                    "ok": False,
                    "error": "Physical events are only accepted from a VR session.",
                }
            )
            return
        from .physical_context import record_events
        from .scene_state import load_scene_state

        try:
            accepted = record_events(
                client_uid,
                context.character_config.conf_uid,
                data.get("sequence"),
                data.get("events"),
            )
        except (OSError, TypeError, ValueError) as exc:
            await websocket.send_json(
                {
                    "type": "vr-physical-result",
                    "ok": False,
                    "sequence": data.get("sequence"),
                    "error": str(exc),
                    "stage": load_scene_state(context.character_config.conf_uid, "vr"),
                }
            )
            return
        await websocket.send_json(
            {
                "type": "vr-physical-result",
                "ok": True,
                "sequence": data.get("sequence"),
                "accepted": len(accepted),
                "stage": load_scene_state(context.character_config.conf_uid, "vr")
                if any(event.kind == "prop_relocated" for event in accepted)
                else None,
            }
        )

    async def _handle_autonomy_feedback(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Learn from a user's helpful/dismissed/snoozed receipt verdict."""
        from .autonomy.presence import get_presence_ledger

        presence = get_presence_ledger(client_uid)
        receipt_id = data.get("receipt_id")
        feedback = data.get("feedback")
        if presence is None:
            ok, message = False, "presence contract is not enabled"
        elif not isinstance(receipt_id, str) or not isinstance(feedback, str):
            ok, message = False, "receipt_id and feedback are required strings"
        else:
            snooze_minutes = data.get("snooze_minutes")
            if isinstance(snooze_minutes, bool) or (
                snooze_minutes is not None
                and not isinstance(snooze_minutes, (int, float))
            ):
                ok, message = False, "snooze_minutes must be a number"
            else:
                ok, message = presence.apply_feedback(
                    receipt_id,
                    feedback,
                    snooze_minutes=(
                        int(snooze_minutes) if snooze_minutes is not None else None
                    ),
                )
                if ok:
                    from .study import log_event

                    # Whether initiative was welcome is the autonomy pillar's
                    # outcome measure, and the user says so directly.
                    log_event(
                        "autonomy_feedback",
                        receipt_id=receipt_id,
                        feedback=feedback,
                    )

        await websocket.send_text(
            json.dumps(
                {
                    "type": "autonomy-feedback-result",
                    "ok": ok,
                    "receipt_id": receipt_id,
                    "message": message,
                }
            )
        )
        if presence is not None:
            await websocket.send_text(json.dumps(presence.snapshot()))

    async def _handle_presence_state_request(
        self, websocket: WebSocket, client_uid: str, data: WSMessage
    ) -> None:
        """Return the attention budget, snooze state, and recent receipts."""
        from .autonomy.presence import get_presence_ledger

        presence = get_presence_ledger(client_uid)
        if presence is None:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "presence-state",
                        "enabled": False,
                        "receipts": [],
                    }
                )
            )
            return
        await websocket.send_text(json.dumps(presence.snapshot()))
