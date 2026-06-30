"""Bridge for sending live action requests to a connected frontend and
awaiting the result, used by scene/spatial MCP tools (e.g. move_to).

This complements stage_state_manager.py, which handles the *persisted*
scene state. This module handles *live* round-trips to a connected client:
"animate the avatar to this position, tell me when you're done."

How it works:
  1. websocket_handler.py registers each connected client's send function
     here via `register_client_sender` when the connection is established.
  2. A tool calls `request_move_to(conf_uid, x, y)`, which creates a Future,
     sends a "frontend-action-request" message with a unique request_id, and
     awaits the Future.
  3. When the frontend replies with "frontend-action-result" (same
     request_id), websocket_handler.py calls `resolve_action_result(...)`,
     which sets the Future's result, unblocking the waiting tool call.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Awaitable, Callable, Optional

from loguru import logger

# client_uid -> "send_text" callable (the same one ServiceContext uses)
_client_senders: dict[str, Callable[[str], Awaitable[None]]] = {}

# request_id -> Future that the waiting tool call is blocked on
_pending_requests: dict[str, asyncio.Future] = {}

# How long to wait for the frontend to report back before giving up.
_DEFAULT_TIMEOUT_SECONDS = 15.0

# TODO: this assumes a single connected client (fine for the MVP / local
# desktop-pet use case described in the project doc). If multi-client support
# is ever needed, route by conf_uid -> client_uid instead of "most recent".
_active_client_uid: Optional[str] = None


def register_client_sender(
    client_uid: str, send_text: Callable[[str], Awaitable[None]]
) -> None:
    """Called by websocket_handler.py when a client connects."""
    _client_senders[client_uid] = send_text
    global _active_client_uid
    _active_client_uid = client_uid
    logger.debug(f"frontend_bridge: registered sender for client {client_uid}")


def unregister_client_sender(client_uid: str) -> None:
    """Called by websocket_handler.py when a client disconnects."""
    _client_senders.pop(client_uid, None)
    global _active_client_uid
    if _active_client_uid == client_uid:
        _active_client_uid = None


def resolve_action_result(request_id: str, ok: bool, result: dict, error: Optional[str] = None) -> None:
    """Called by websocket_handler.py when a "frontend-action-result" message arrives."""
    future = _pending_requests.pop(request_id, None)
    if future is None:
        logger.warning(f"frontend_bridge: no pending request for id {request_id}")
        return
    if not future.done():
        future.set_result((ok, result, error))


async def _send_action_request(
    action: str, args: dict, timeout: float = _DEFAULT_TIMEOUT_SECONDS
) -> tuple[bool, dict, Optional[str]]:
    """Low-level: send a frontend-action-request and await its result."""
    if _active_client_uid is None or _active_client_uid not in _client_senders:
        return False, {}, "no connected frontend client to send the action to"

    request_id = str(uuid.uuid4())
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_requests[request_id] = future

    message = json.dumps(
        {
            "type": "frontend_action_request",
            "request_id": request_id,
            "action": action,
            "args": args,
        }
    )

    send_text = _client_senders[_active_client_uid]
    try:
        await send_text(message)
    except Exception as exc:  # noqa: BLE001
        _pending_requests.pop(request_id, None)
        return False, {}, f"failed to send action request: {exc}"

    try:
        ok, result, error = await asyncio.wait_for(future, timeout=timeout)
        return ok, result, error
    except asyncio.TimeoutError:
        _pending_requests.pop(request_id, None)
        return False, {}, f"frontend did not respond within {timeout}s"


async def request_move_to(conf_uid: str, x: float, y: float) -> tuple[bool, Optional[str]]:
    """Ask the frontend to move the avatar to (x, y); wait until it confirms.

    Returns (ok, error_message_or_None).
    """
    ok, _result, error = await _send_action_request("move_to", {"x": x, "y": y})
    return ok, error