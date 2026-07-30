"""Thread-safe, process-local wakeups for agenda store mutations.

The agenda file remains authoritative and the executive still polls for writes
made by MCP subprocesses. This bus removes needless poll latency for mutations
made inside the server process.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable


@dataclass(frozen=True)
class _Subscription:
    root: str
    loop: asyncio.AbstractEventLoop
    callback: Callable[[], None]


_subscriptions: dict[str, _Subscription] = {}
_lock = Lock()


def subscribe_agenda_changes(
    workspace_root: Path,
    callback: Callable[[], None],
) -> str:
    """Subscribe the current event loop to mutations for one workspace."""
    token = uuid.uuid4().hex
    subscription = _Subscription(
        root=str(workspace_root.resolve()),
        loop=asyncio.get_running_loop(),
        callback=callback,
    )
    with _lock:
        _subscriptions[token] = subscription
    return token


def unsubscribe_agenda_changes(token: str) -> None:
    with _lock:
        _subscriptions.pop(token, None)


def notify_agenda_changed(workspace_root: Path) -> None:
    """Schedule matching callbacks safely, including from worker threads."""
    root = str(workspace_root.resolve())
    with _lock:
        subscriptions = [
            subscription
            for subscription in _subscriptions.values()
            if subscription.root == root
        ]
    for subscription in subscriptions:
        if subscription.loop.is_closed():
            continue
        subscription.loop.call_soon_threadsafe(subscription.callback)
