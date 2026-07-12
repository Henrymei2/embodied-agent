"""Lightweight registry mapping client_uid to their ServiceContext and send_text.

Used by the scheduler to fire tasks without importing websocket_handler
directly (which would create a circular import).

websocket_handler.py registers clients here on connect/disconnect,
mirroring what it already does for frontend_bridge.py.
"""

from __future__ import annotations

from typing import Callable, Awaitable, Optional, Dict

from loguru import logger

_contexts: Dict[str, object] = {}
_senders: Dict[str, Callable[[str], Awaitable[None]]] = {}


def register_client(
    client_uid: str,
    context: object,
    send_text: Callable[[str], Awaitable[None]],
) -> None:
    """Called by websocket_handler when a client connects."""
    _contexts[client_uid] = context
    _senders[client_uid] = send_text
    logger.debug(f"ClientRegistry: registered {client_uid}")


def unregister_client(client_uid: str) -> None:
    """Called by websocket_handler when a client disconnects."""
    _contexts.pop(client_uid, None)
    _senders.pop(client_uid, None)
    logger.debug(f"ClientRegistry: unregistered {client_uid}")


def get_client_context(client_uid: str) -> Optional[object]:
    return _contexts.get(client_uid)


def get_client_send(client_uid: str) -> Optional[Callable]:
    return _senders.get(client_uid)