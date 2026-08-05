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
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from loguru import logger


@dataclass(frozen=True)
class _ConnectedClient:
    send_text: Callable[[str], Awaitable[None]]
    conf_uid: str | None
    scene_mode: str = "normal"


@dataclass(frozen=True)
class _PendingRequest:
    client_uid: str
    action: str
    future: asyncio.Future


# client_uid -> connection metadata
_clients: dict[str, _ConnectedClient] = {}

# request_id -> owning client and Future that the waiting tool call awaits
_pending_requests: dict[str, _PendingRequest] = {}

# How long to wait for the frontend to report back before giving up.
_DEFAULT_TIMEOUT_SECONDS = 15.0


def register_client_sender(
    client_uid: str,
    send_text: Callable[[str], Awaitable[None]],
    conf_uid: str | None = None,
    scene_mode: str = "normal",
) -> None:
    """Called by websocket_handler.py when a client connects."""
    _clients[client_uid] = _ConnectedClient(
        send_text=send_text,
        conf_uid=conf_uid,
        scene_mode=scene_mode,
    )
    logger.debug(f"frontend_bridge: registered sender for client {client_uid}")


def update_client_configuration(client_uid: str, conf_uid: str) -> None:
    """Update the character binding after a connected client switches config."""
    client = _clients.get(client_uid)
    if client is None:
        logger.warning(f"frontend_bridge: cannot update unknown client {client_uid}")
        return
    _clients[client_uid] = _ConnectedClient(
        send_text=client.send_text,
        conf_uid=conf_uid,
        scene_mode=client.scene_mode,
    )


def get_client_scene_mode(client_uid: str) -> str | None:
    client = _clients.get(client_uid)
    return client.scene_mode if client else None


def unregister_client_sender(client_uid: str) -> None:
    """Remove a client and immediately fail requests that were routed to it."""
    _clients.pop(client_uid, None)
    for request_id, pending in list(_pending_requests.items()):
        if pending.client_uid != client_uid:
            continue
        _pending_requests.pop(request_id, None)
        if not pending.future.done():
            pending.future.set_result(
                (False, {}, "frontend client disconnected before responding")
            )


def resolve_action_result(
    request_id: str,
    ok: bool,
    result: dict,
    error: Optional[str] = None,
    client_uid: str | None = None,
) -> bool:
    """Complete a pending request only when the replying client owns it."""
    pending = _pending_requests.get(request_id)
    if pending is None:
        logger.warning(f"frontend_bridge: no pending request for id {request_id}")
        return False
    if client_uid is not None and pending.client_uid != client_uid:
        logger.warning(
            "frontend_bridge: ignored action result from non-owning client "
            f"{client_uid} for request {request_id}"
        )
        return False

    _pending_requests.pop(request_id, None)
    if not pending.future.done():
        pending.future.set_result((ok, result, error))
    return True


def owns_pending_request(request_id: str, client_uid: str) -> bool:
    """Return whether a live frontend request belongs to ``client_uid``."""
    pending = _pending_requests.get(request_id)
    return pending is not None and pending.client_uid == client_uid


def pending_request_action(request_id: str, client_uid: str) -> str | None:
    """Return the action only to the client that owns the pending request."""
    pending = _pending_requests.get(request_id)
    if pending is None or pending.client_uid != client_uid:
        return None
    return pending.action


def _select_client(
    client_uid: str | None,
    conf_uid: str | None,
) -> tuple[str | None, str | None]:
    if client_uid is not None:
        client = _clients.get(client_uid)
        if client is None:
            return None, f"frontend client '{client_uid}' is not connected"
        if conf_uid is not None and client.conf_uid != conf_uid:
            return None, "frontend client is not using the requested character config"
        return client_uid, None

    candidates = [
        uid
        for uid, client in _clients.items()
        if conf_uid is None or client.conf_uid == conf_uid
    ]
    if not candidates:
        qualifier = f" for character config '{conf_uid}'" if conf_uid else ""
        return None, f"no connected frontend client{qualifier}"
    if len(candidates) > 1:
        return None, "multiple frontend clients match; client_uid is required"
    return candidates[0], None


async def _send_action_request(
    action: str,
    args: dict,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    client_uid: str | None = None,
    conf_uid: str | None = None,
) -> tuple[bool, dict, Optional[str]]:
    """Low-level: send a frontend-action-request and await its result."""
    selected_client_uid, selection_error = _select_client(client_uid, conf_uid)
    if selected_client_uid is None:
        return False, {}, selection_error

    request_id = str(uuid.uuid4())
    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _pending_requests[request_id] = _PendingRequest(
        client_uid=selected_client_uid,
        action=action,
        future=future,
    )

    message = json.dumps(
        {
            "type": "frontend_action_request",
            "request_id": request_id,
            "action": action,
            "args": args,
        }
    )

    send_text = _clients[selected_client_uid].send_text
    logger.info(
        "frontend_bridge: dispatching action "
        f"{action} request={request_id} client={selected_client_uid}"
    )
    try:
        await send_text(message)
    except Exception as exc:  # noqa: BLE001
        _pending_requests.pop(request_id, None)
        logger.warning(
            "frontend_bridge: action dispatch failed "
            f"action={action} request={request_id}"
        )
        return False, {}, f"failed to send action request: {exc}"

    try:
        ok, result, error = await asyncio.wait_for(future, timeout=timeout)
        logger.info(
            "frontend_bridge: action completed "
            f"action={action} request={request_id} ok={ok}"
        )
        return ok, result, error
    except asyncio.TimeoutError:
        _pending_requests.pop(request_id, None)
        logger.warning(
            "frontend_bridge: action timed out "
            f"action={action} request={request_id} timeout={timeout}s"
        )
        return False, {}, f"frontend did not respond within {timeout}s"


async def request_frontend_action(
    action: str,
    args: dict,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    client_uid: str | None = None,
    conf_uid: str | None = None,
) -> tuple[bool, dict, Optional[str]]:
    """Ask one unambiguously selected frontend to run an action."""
    return await _send_action_request(
        action,
        args,
        timeout=timeout,
        client_uid=client_uid,
        conf_uid=conf_uid,
    )


async def request_move_to(
    conf_uid: str,
    x: float,
    y: float,
    client_uid: str | None = None,
) -> tuple[bool, Optional[str]]:
    """Ask the frontend to move the avatar to (x, y); wait until it confirms.

    Returns (ok, error_message_or_None).
    """
    ok, _result, error = await request_frontend_action(
        "move_to",
        {"x": x, "y": y},
        client_uid=client_uid,
        conf_uid=conf_uid,
    )
    return ok, error
