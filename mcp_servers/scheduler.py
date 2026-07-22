"""Scheduler MCP server for the embodied agent.

Allows the agent to schedule tasks (one-time or recurring), view scheduled
tasks, and remove them. Tasks are persisted to the agent workspace so they
survive restarts.

Each task has:
  - prompt: what the agent should do when the task fires
  - priority: how urgently it should run (lower = higher priority)
  - interval_seconds: if set, task repeats on this interval; if None, one-time
  - report: if True, agent speaks the result to the user; if False, stays silent
  - next_run: ISO timestamp of next scheduled execution

Priority convention (matches llm_request_queue.py):
  0  = user request (never assign to scheduled tasks)
  10 = normal scheduled task (default)
  20 = passive/background task

Run standalone for a quick sanity check:
    uv run python mcp_servers/scheduler.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP
from loguru import logger

# The task file is shared with the timer tools and with the read-only routes
# behind the furniture hover windows, so its location, format, and atomic
# writer live in one place.
from src.open_llm_vtuber.furniture_state import (
    load_tasks as _load_tasks,
    save_tasks as _save_tasks,
)

mcp = FastMCP("scheduler")

# How often the background loop checks for due tasks (seconds)
_TICK_INTERVAL = 30


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ── MCP tools ─────────────────────────────────────────────────────────────────


@mcp.tool()
def schedule_task(
    prompt: str,
    priority: int = 10,
    interval_seconds: Optional[float] = None,
    delay_seconds: float = 0,
    report: bool = True,
) -> str:
    """Schedule a task for the agent to execute.

    Args:
        prompt: What the agent should do when this task fires
            (e.g. "Check today's calendar and summarise upcoming events").
        priority: How urgently to run the task relative to others.
            10 = normal (default), 20 = passive/background.
            Never use 0 — that is reserved for live user requests.
        interval_seconds: If set, the task repeats every this many seconds.
            If None (default), the task runs once and is removed.
        delay_seconds: How many seconds from now before the first run.
            Defaults to 0 (run as soon as possible).
        report: If True (default), the agent speaks the result to the user.
            If False, the task runs silently in the background.

    Returns:
        Confirmation string with the new task's ID.
    """
    if priority == 0:
        return "Error: priority 0 is reserved for live user requests. Use 10 or higher."

    task_id = str(uuid.uuid4())[:8]
    next_run = _iso(datetime.now() + timedelta(seconds=delay_seconds))

    task = {
        "id": task_id,
        "prompt": prompt,
        "priority": priority,
        "interval_seconds": interval_seconds,
        "report": report,
        "next_run": next_run,
        "created_at": _now_iso(),
        "run_count": 0,
    }

    tasks = _load_tasks()
    tasks[task_id] = task
    _save_tasks(tasks)

    kind = f"every {interval_seconds}s" if interval_seconds else "one-time"
    return (
        f"OK: scheduled task {task_id} ({kind}) at priority {priority}. "
        f"First run: {next_run}. Report to user: {report}."
    )


@mcp.tool()
def list_tasks() -> str:
    """List all currently scheduled tasks.

    Returns:
        A summary of every pending task, or a message if none exist.
    """
    tasks = _load_tasks()
    if not tasks:
        return "No tasks currently scheduled."

    lines = []
    for task in sorted(tasks.values(), key=lambda t: t["priority"]):
        kind = (
            f"every {task['interval_seconds']}s"
            if task.get("interval_seconds")
            else "one-time"
        )
        lines.append(
            f"[{task['id']}] priority={task['priority']} {kind} "
            f"next={task['next_run']} report={task['report']}\n"
            f"  prompt: {task['prompt']}"
        )
    return "\n".join(lines)


@mcp.tool()
def remove_task(task_id: str) -> str:
    """Remove a scheduled task by its ID.

    Args:
        task_id: The short ID returned when the task was created
            (e.g. "a1b2c3d4").

    Returns:
        Confirmation that the task was removed, or an error if not found.
    """
    tasks = _load_tasks()
    if task_id not in tasks:
        return f"Error: no task found with id '{task_id}'."
    del tasks[task_id]
    _save_tasks(tasks)
    return f"OK: removed task {task_id}."


# ── Default tasks (loaded on startup) ─────────────────────────────────────────

DEFAULT_TASKS = [
    {
        "prompt": "Check what time it is and greet the user if this is the first interaction of the day.",
        "priority": 10,
        "interval_seconds": 3600,  # every hour
        "delay_seconds": 0,
        "report": True,
        "description": "hourly_greeting",
    },
    {
        "prompt": "Review the user's recent activity and check if they seem idle or stuck. If so, offer help quietly.",
        "priority": 20,
        "interval_seconds": 1800,  # every 30 minutes
        "delay_seconds": 300,  # start after 5 minutes
        "report": False,
        "description": "idle_check",
    },
]


def _initialize_default_tasks() -> None:
    """Add default tasks on first launch if they don't already exist."""
    tasks = _load_tasks()

    # Check by description field to avoid duplicating on restart
    existing_descriptions = {t.get("description") for t in tasks.values()}

    added = 0
    for default in DEFAULT_TASKS:
        if default["description"] in existing_descriptions:
            continue

        task_id = str(uuid.uuid4())[:8]
        next_run = _iso(
            datetime.now() + timedelta(seconds=default.get("delay_seconds", 0))
        )
        tasks[task_id] = {
            "id": task_id,
            "prompt": default["prompt"],
            "priority": default["priority"],
            "interval_seconds": default["interval_seconds"],
            "report": default["report"],
            "next_run": next_run,
            "created_at": _now_iso(),
            "run_count": 0,
            "description": default["description"],
        }
        added += 1

    if added:
        _save_tasks(tasks)
        logger.info(f"Scheduler: initialized {added} default task(s).")


# ── Background execution loop ──────────────────────────────────────────────────


async def _scheduler_loop(client_uid: str) -> None:
    """Background loop that fires due tasks through the LLM priority queue."""
    from src.open_llm_vtuber.conversations.llm_request_queue import (
        get_queue,
        PRIORITY_SCHEDULED,
    )

    logger.info("Scheduler: background loop started.")

    while True:
        try:
            await asyncio.sleep(_TICK_INTERVAL)
            now = datetime.now()
            tasks = _load_tasks()
            changed = False

            for task_id, task in list(tasks.items()):
                next_run = datetime.fromisoformat(task["next_run"])
                if now < next_run:
                    continue

                logger.info(
                    f"Scheduler: firing task {task_id} "
                    f"(priority={task['priority']}): {task['prompt'][:60]}..."
                )

                # Build metadata so single_conversation knows this is scheduled
                metadata = {
                    "scheduled_task": True,
                    "task_id": task_id,
                    "report": task.get("report", True),
                    "skip_history": not task.get("report", True),
                    "skip_memory": False,
                }

                # Get the service context for this client

                # Enqueue through the priority queue
                queue = get_queue(client_uid)
                await queue.enqueue(
                    coro=_run_scheduled_task(
                        client_uid=client_uid,
                        prompt=task["prompt"],
                        metadata=metadata,
                    ),
                    priority=task.get("priority", PRIORITY_SCHEDULED),
                    label=f"scheduled:{task_id}",
                )

                # Update run count and next_run
                task["run_count"] = task.get("run_count", 0) + 1
                interval = task.get("interval_seconds")
                if interval:
                    task["next_run"] = _iso(now + timedelta(seconds=interval))
                    tasks[task_id] = task
                else:
                    # One-time task — remove after firing
                    del tasks[task_id]
                changed = True

            if changed:
                _save_tasks(tasks)

        except asyncio.CancelledError:
            logger.info("Scheduler: background loop cancelled.")
            break
        except Exception as e:
            logger.error(f"Scheduler: error in loop: {e}")


async def _run_scheduled_task(
    client_uid: str,
    prompt: str,
    metadata: dict,
) -> None:
    """Actually run a scheduled task — gets a context and calls process_single_conversation."""
    try:
        # Get context via the websocket handler's client_contexts
        # We import lazily to avoid circular imports

        # service_context is stored per-client in websocket_handler
        # Access it through the module-level registry we'll add in websocket_handler
        from src.open_llm_vtuber.client_registry import (
            get_client_context,
            get_client_send,
        )

        context = get_client_context(client_uid)
        send_text = get_client_send(client_uid)

        if context is None or send_text is None:
            logger.warning(
                f"Scheduler: no context for client {client_uid}, skipping task."
            )
            return

        from src.open_llm_vtuber.conversations.single_conversation import (
            process_single_conversation,
        )

        await process_single_conversation(
            context=context,
            websocket_send=send_text,
            client_uid=client_uid,
            user_input=prompt,
            metadata=metadata,
        )
    except Exception as e:
        logger.error(f"Scheduler: error running task for {client_uid}: {e}")


if __name__ == "__main__":
    _initialize_default_tasks()
    mcp.run()
