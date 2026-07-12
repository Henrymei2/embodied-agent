"""Priority queue for LLM requests.

Ensures the agent works on one LLM request at a time, with active user
requests (chat) always prioritized over passive background requests
(scheduled tasks, memory compaction, etc.).

Priority convention (lower number = higher priority):
    0  — active user request (text-input, mic-audio-end)
    1  — proactive speak (ai-speak-signal)
    10 — scheduled background tasks
    20 — memory compaction / other passive work

The queue is per-client, stored in a module-level dict keyed by client_uid.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, Optional

from loguru import logger

# Priority constants — use these everywhere instead of magic numbers
PRIORITY_USER = 0        # user typed or spoke something
PRIORITY_PROACTIVE = 1   # agent-initiated speak
PRIORITY_SCHEDULED = 10  # scheduled background task
PRIORITY_PASSIVE = 20    # memory compaction, housekeeping, etc.


@dataclass(order=True)
class _QueueItem:
    """Wrapper that makes coroutines orderable by priority."""
    priority: int
    # Exclude the coroutine from ordering comparisons
    coro: Coroutine = field(compare=False)
    label: str = field(compare=False, default="")


class ClientRequestQueue:
    """Manages a priority queue of LLM requests for a single client."""

    def __init__(self, client_uid: str) -> None:
        self.client_uid = client_uid
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._worker_task: Optional[asyncio.Task] = None
        self._current_task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Start the background worker that drains the queue."""
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())
            logger.debug(f"LLMQueue: worker started for {self.client_uid}")

    def stop(self) -> None:
        """Stop the worker and cancel any in-progress request."""
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        logger.debug(f"LLMQueue: worker stopped for {self.client_uid}")

    def interrupt(self) -> None:
        """Cancel the currently running request (user interrupted)."""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            logger.info(f"LLMQueue: interrupted current request for {self.client_uid}")

    async def enqueue(
        self,
        coro: Coroutine,
        priority: int = PRIORITY_USER,
        label: str = "",
    ) -> None:
        """Add a coroutine to the queue at the given priority."""
        item = _QueueItem(priority=priority, coro=coro, label=label)
        await self._queue.put(item)
        logger.debug(
            f"LLMQueue: enqueued '{label}' at priority {priority} "
            f"for {self.client_uid} (queue size: {self._queue.qsize()})"
        )

    async def _worker(self) -> None:
        """Drain the queue one item at a time."""
        while True:
            try:
                item: _QueueItem = await self._queue.get()
                logger.info(
                    f"LLMQueue: executing '{item.label}' "
                    f"(priority {item.priority}) for {self.client_uid}"
                )
                self._current_task = asyncio.create_task(item.coro)
                try:
                    await self._current_task
                except asyncio.CancelledError:
                    logger.info(
                        f"LLMQueue: '{item.label}' was cancelled for {self.client_uid}"
                    )
                except Exception as e:
                    logger.error(
                        f"LLMQueue: error in '{item.label}' for {self.client_uid}: {e}"
                    )
                finally:
                    self._current_task = None
                    self._queue.task_done()
            except asyncio.CancelledError:
                logger.debug(f"LLMQueue: worker cancelled for {self.client_uid}")
                break


# Module-level registry: client_uid -> ClientRequestQueue
_queues: Dict[str, ClientRequestQueue] = {}


def get_queue(client_uid: str) -> ClientRequestQueue:
    """Get or create the request queue for a client."""
    if client_uid not in _queues:
        _queues[client_uid] = ClientRequestQueue(client_uid)
        _queues[client_uid].start()
        logger.debug(f"LLMQueue: created new queue for {client_uid}")
    return _queues[client_uid]


def remove_queue(client_uid: str) -> None:
    """Stop and remove the queue when a client disconnects."""
    q = _queues.pop(client_uid, None)
    if q:
        q.stop()
        logger.debug(f"LLMQueue: removed queue for {client_uid}")


def interrupt_queue(client_uid: str) -> None:
    """Cancel the current request for a client (called on interrupt signal)."""
    q = _queues.get(client_uid)
    if q:
        q.interrupt()