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
from src.open_llm_vtuber.autonomy.agenda import (
    ScheduleSpec,
    create_entry,
    get_agenda_store,
)
from src.open_llm_vtuber.utils.timezone import now_local

mcp = FastMCP("scheduler")

# How often the background loop checks for due tasks (seconds)
_TICK_INTERVAL = 30


def _now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


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
            (e.g. "Check today's calendar and summarize upcoming events").
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

    next_run = _iso(now_local() + timedelta(seconds=delay_seconds))
    entry = create_entry(
        kind="scheduled",
        title=prompt.strip()[:120] or "Scheduled task",
        description=prompt,
        origin="scheduled",
        evidence_refs=["schedule:user-or-agent-request"],
        completion_condition="Execute the scheduled prompt once.",
        time_budget_seconds=900,
        status="queued",
        report_policy="on_completion" if report else "silent",
        schedule=ScheduleSpec(
            next_run=next_run,
            interval_seconds=interval_seconds,
            priority=priority,
            report=report,
            prompt=prompt,
        ),
    )
    get_agenda_store().add(entry)

    kind = f"every {interval_seconds}s" if interval_seconds else "one-time"
    return (
        f"OK: scheduled task {entry.id} ({kind}) at priority {priority}. "
        f"First run: {next_run}. Report to user: {report}."
    )


@mcp.tool()
def list_tasks() -> str:
    """List all currently scheduled tasks.

    Returns:
        A summary of every pending task, or a message if none exist.
    """
    tasks = [
        entry
        for entry in get_agenda_store().load().entries
        if entry.schedule and entry.status in {"queued", "suspended"}
    ]
    if not tasks:
        return "No tasks currently scheduled."

    lines = []
    for task in sorted(tasks, key=lambda item: item.schedule.priority):
        kind = (
            f"every {task.schedule.interval_seconds}s"
            if task.schedule.interval_seconds
            else "one-time"
        )
        lines.append(
            f"[{task.id}] priority={task.schedule.priority} {kind} "
            f"next={task.schedule.next_run} report={task.schedule.report}\n"
            f"  prompt: {task.schedule.prompt}"
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
    try:
        get_agenda_store().remove_queued(task_id)
    except (KeyError, ValueError):
        return f"Error: no task found with id '{task_id}'."
    return f"OK: removed task {task_id}."


# ── Default tasks (loaded on startup) ─────────────────────────────────────────

DEFAULT_TASKS = []


def _initialize_default_tasks() -> None:
    """Add default tasks on first launch if they don't already exist."""

    return None


def _claim_due_tasks(now: datetime) -> list[tuple[str, dict]]:
    """Advance/remove due tasks atomically before they are enqueued."""

    return [
        (entry.id, {"prompt": entry.description, "priority": 10, "report": True})
        for entry in get_agenda_store().claim_due(now)
    ]


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
            now = now_local()
            due_tasks = _claim_due_tasks(now)

            for task_id, task in due_tasks:
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
                    coro_factory=lambda task_prompt=task["prompt"],
                    task_metadata=metadata: _run_scheduled_task(
                        client_uid=client_uid,
                        prompt=task_prompt,
                        metadata=task_metadata,
                    ),
                    priority=task.get("priority", PRIORITY_SCHEDULED),
                    label=f"scheduled:{task_id}",
                    dedupe_key=f"scheduled:{task_id}",
                )

        except asyncio.CancelledError:
            logger.info("Scheduler: background loop canceled.")
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
