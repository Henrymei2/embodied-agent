"""Timer tools for the embodied companion.

These tools belong to the `hourglass` object in the room: the agent has to walk
to the hourglass to set, inspect, or cancel a timer.

A timer is just a one-time scheduled task, so it is stored in the scheduler's
task file and fired by the background loop that already polls it. When a timer
comes due the loop hands the agent a prompt, the agent tells the user, and the
task is removed. That loop ticks every 30 seconds, so a timer can fire up to one
tick late — these are reminders, not precise timekeeping.

Run standalone for a quick sanity check:
    uv run python mcp_servers/timer_tools.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.autonomy.agenda import (
    ScheduleSpec,
    create_entry,
    get_agenda_store,
)
from src.open_llm_vtuber.utils.timezone import now_local

_MAX_TIMERS = 50
_MAX_MINUTES = 24 * 60
_MIN_MINUTES = 0.25
_MAX_LABEL = 120
# Matches PRIORITY_SCHEDULED in llm_request_queue.py: ahead of passive work,
# behind anything the user asked for directly.
_TIMER_PRIORITY = 10

mcp = FastMCP("timer")


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _describe(task: Any, now: datetime) -> dict[str, Any]:
    due = datetime.fromisoformat(task.schedule.next_run)
    if due.tzinfo is None:
        due = due.replace(tzinfo=now.tzinfo)
    remaining = max(0, int((due - now).total_seconds()))
    return {
        "timer_id": task.id,
        "label": task.metadata.get("label", ""),
        "due_at": task.schedule.next_run,
        "remaining_seconds": remaining,
        "remaining_human": f"{remaining // 60}m {remaining % 60}s",
    }


def _validate_minutes(minutes: float) -> float:
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
        raise ValueError("minutes must be a number")
    if minutes < _MIN_MINUTES or minutes > _MAX_MINUTES:
        raise ValueError(f"minutes must be between {_MIN_MINUTES} and {_MAX_MINUTES}")
    return float(minutes)


def _validate_label(label: str) -> str:
    if not isinstance(label, str):
        raise ValueError("label must be a string")
    cleaned = label.strip()
    if len(cleaned) > _MAX_LABEL:
        raise ValueError(f"label cannot exceed {_MAX_LABEL} characters")
    return cleaned


@mcp.tool()
def set_timer(minutes: float, label: str = "") -> str:
    """Set a one-shot timer. Requires standing near the hourglass.

    When it expires the agent is woken once and tells the user the timer is up.
    Firing is accurate to about 30 seconds.

    Args:
        minutes: How long from now the timer should fire (0.25 to 1440).
        label: Optional short description, e.g. "tea" or "stretch break".
    """
    try:
        duration = _validate_minutes(minutes)
        clean_label = _validate_label(label)

        now = now_local()
        spoken = f"'{clean_label}' " if clean_label else ""
        prompt = (
                f"The {spoken}timer you set {duration:g} minute(s) ago just "
                "finished. Tell the user briefly that it is up."
        )
        store = get_agenda_store()
        pending = [
            entry
            for entry in store.load().entries
            if entry.schedule and entry.schedule.timer and entry.status == "queued"
        ]
        if len(pending) >= _MAX_TIMERS:
            raise ValueError(f"at most {_MAX_TIMERS} timers can be pending")
        timer = create_entry(
            kind="scheduled",
            title=clean_label or "Timer",
            description=prompt,
            origin="timer",
            evidence_refs=["timer:user-or-agent-request"],
            completion_condition="Notify the user once when the timer expires.",
            time_budget_seconds=120,
            status="queued",
            report_policy="on_completion",
            schedule=ScheduleSpec(
                next_run=_iso(now + timedelta(minutes=duration)),
                priority=_TIMER_PRIORITY,
                report=True,
                timer=True,
                prompt=prompt,
            ),
            metadata={"label": clean_label},
        )
        store.add(timer)
        return json.dumps(
            {"ok": True, "timer": _describe(timer, now)},
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def list_timers() -> str:
    """List pending timers and how long each has left.

    Requires standing near the hourglass.
    """
    try:
        now = now_local()
        timers = [
            _describe(task, now)
            for task in get_agenda_store().load().entries
            if task.schedule and task.schedule.timer and task.status == "queued"
        ]
        timers.sort(key=lambda timer: timer["remaining_seconds"])
        return json.dumps({"timers": timers}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def cancel_timer(timer_id: str) -> str:
    """Cancel a pending timer by ID. Requires standing near the hourglass."""
    try:
        if not isinstance(timer_id, str) or not timer_id or len(timer_id) > 80:
            raise ValueError("timer_id must be a non-empty string")
        entry = next(
            (
                item
                for item in get_agenda_store().load().entries
                if item.id == timer_id and item.schedule and item.schedule.timer
            ),
            None,
        )
        if entry is None:
            raise ValueError(f"timer '{timer_id}' was not found")
        get_agenda_store().remove_queued(timer_id)
        return json.dumps({"ok": True, "timer_id": timer_id})
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
