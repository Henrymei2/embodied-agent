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
from typing import Callable, Coroutine, Dict, Optional

from loguru import logger

# Priority constants — use these everywhere instead of magic numbers
PRIORITY_USER = 0  # user typed or spoke something
PRIORITY_PROACTIVE = 1  # agent-initiated speak
PRIORITY_SCHEDULED = 10  # scheduled background task
PRIORITY_PASSIVE = 20  # memory compaction, housekeeping, etc.


@dataclass(order=True)
class _QueueItem:
    """Wrapper that makes coroutines orderable by priority."""

    priority: int
    sequence: int
    coro: Optional[Coroutine] = field(compare=False, default=None)
    coro_factory: Optional[Callable[[], Coroutine]] = field(compare=False, default=None)
    label: str = field(compare=False, default="")
    dedupe_key: Optional[str] = field(compare=False, default=None)
    on_done: Optional[Callable[[str, Optional[BaseException]], None]] = field(
        compare=False, default=None
    )


class ClientRequestQueue:
    """Manages a priority queue of LLM requests for a single client."""

    def __init__(self, client_uid: str) -> None:
        self.client_uid = client_uid
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._worker_task: Optional[asyncio.Task] = None
        self._current_task: Optional[asyncio.Task] = None
        self._current_priority: Optional[int] = None
        self._pending_keys: set[str] = set()
        self._sequence = 0
        self._stopping = False

    def start(self) -> None:
        """Start the background worker that drains the queue."""
        if self._worker_task is None or self._worker_task.done():
            self._stopping = False
            self._worker_task = asyncio.create_task(self._worker())
            logger.debug(f"LLMQueue: worker started for {self.client_uid}")

    def stop(self) -> None:
        """Stop the worker and cancel any in-progress request."""
        self._stopping = True
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._dispose_item(item, "canceled", None)
            self._queue.task_done()
        logger.debug(f"LLMQueue: worker stopped for {self.client_uid}")

    def interrupt(self) -> None:
        """Cancel the currently running request (user interrupted)."""
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            logger.info(f"LLMQueue: interrupted current request for {self.client_uid}")

    async def join(self) -> None:
        """Wait until every currently queued item has finished or been canceled."""
        await self._queue.join()

    async def enqueue(
        self,
        coro: Optional[Coroutine] = None,
        priority: int = PRIORITY_USER,
        label: str = "",
        *,
        coro_factory: Optional[Callable[[], Coroutine]] = None,
        dedupe_key: Optional[str] = None,
        on_done: Optional[Callable[[str, Optional[BaseException]], None]] = None,
    ) -> bool:
        """Add work to the queue, optionally deduplicated until completion.

        Factories are preferred for background work: they avoid creating a
        coroutine that may never be awaited if a queued item is canceled.
        """
        if (coro is None) == (coro_factory is None):
            raise ValueError("provide exactly one of coro or coro_factory")
        if dedupe_key and dedupe_key in self._pending_keys:
            if coro is not None:
                coro.close()
            if on_done is not None:
                on_done("dropped", None)
            logger.debug(f"LLMQueue: dropped duplicate '{label}' for {self.client_uid}")
            return False

        self._sequence += 1
        item = _QueueItem(
            priority=priority,
            sequence=self._sequence,
            coro=coro,
            coro_factory=coro_factory,
            label=label,
            dedupe_key=dedupe_key,
            on_done=on_done,
        )
        if dedupe_key:
            self._pending_keys.add(dedupe_key)
        await self._queue.put(item)
        if (
            self._current_task is not None
            and not self._current_task.done()
            and self._current_priority is not None
            and priority < self._current_priority
        ):
            self._current_task.cancel()
            logger.info(
                f"LLMQueue: preempting priority {self._current_priority} work "
                f"for newly queued priority {priority} work on {self.client_uid}"
            )
        logger.debug(
            f"LLMQueue: enqueued '{label}' at priority {priority} "
            f"for {self.client_uid} (queue size: {self._queue.qsize()})"
        )
        return True

    def _dispose_item(
        self,
        item: _QueueItem,
        status: str,
        error: Optional[BaseException],
    ) -> None:
        if item.coro is not None:
            item.coro.close()
            item.coro = None
        if item.dedupe_key:
            self._pending_keys.discard(item.dedupe_key)
        if item.on_done is not None:
            try:
                item.on_done(status, error)
            except Exception as exc:  # noqa: BLE001 - queue cleanup must continue
                logger.warning(
                    f"LLMQueue: completion callback failed for '{item.label}': {exc}"
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
                status = "completed"
                error: Optional[BaseException] = None
                try:
                    work = (
                        item.coro_factory()
                        if item.coro_factory is not None
                        else item.coro
                    )
                    item.coro = None
                    if work is None:
                        raise RuntimeError("queued work did not produce a coroutine")
                    self._current_priority = item.priority
                    self._current_task = asyncio.create_task(work)
                    await self._current_task
                except asyncio.CancelledError:
                    status = "canceled"
                    logger.info(
                        f"LLMQueue: '{item.label}' was canceled for {self.client_uid}"
                    )
                    if self._stopping:
                        raise
                except Exception as e:
                    status = "failed"
                    error = e
                    logger.error(
                        f"LLMQueue: error in '{item.label}' for {self.client_uid}: {e}"
                    )
                finally:
                    self._current_task = None
                    self._current_priority = None
                    self._dispose_item(item, status, error)
                    self._queue.task_done()
            except asyncio.CancelledError:
                logger.debug(f"LLMQueue: worker canceled for {self.client_uid}")
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
