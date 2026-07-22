"""Persistent state behind room furniture.

Two stores live here:

* the todo list shown on the `todo_list` object, in `<workspace>/todo/todos.json`
* the scheduler task file, `<workspace>/scheduler_tasks.json`, which also holds
  the one-shot timers set at the `hourglass` object

Both are read from three places — the MCP tool subprocesses, the in-process
scheduler loop, and the read-only routes that back the furniture hover windows —
so the file format, the ordering rules, and the atomic writer are defined once
here rather than reimplemented per caller.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from .workspace import DEFAULT_WORKSPACE_DIR

TODO_PRIORITIES = ("high", "normal", "low")
_PRIORITY_ORDER = {priority: index for index, priority in enumerate(TODO_PRIORITIES)}

# How many rows a hover window shows before it reports a remainder.
_SNAPSHOT_LIMIT = 8


def workspace_root() -> Path:
    """Resolve the active workspace without creating it."""
    configured = os.environ.get("EMBODIED_AGENT_WORKSPACE")
    return Path(configured).expanduser() if configured else DEFAULT_WORKSPACE_DIR


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Replace ``path`` atomically.

    These files are written by the MCP subprocesses and the in-process scheduler
    loop at the same time, so a plain truncate-then-write can be observed (or
    collided with) mid-write. A reader would then see invalid JSON and treat the
    store as empty, silently discarding every task or todo.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


# ── Todo store ────────────────────────────────────────────────────────────────


def todos_path() -> Path:
    return workspace_root() / "todo" / "todos.json"


def load_todos() -> list[dict[str, Any]]:
    """Return stored todo items, raising ValueError if the store is unreadable."""
    path = todos_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # ValueError covers both malformed JSON and undecodable bytes.
        raise ValueError(f"todo store is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("todo store has an invalid format")
    return [item for item in payload["items"] if isinstance(item, dict)]


def save_todos(items: list[dict[str, Any]]) -> None:
    _write_json_atomic(
        todos_path(),
        {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": items,
        },
    )


def todo_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    """Open items first, then by priority, then by soonest due date."""
    return (
        0 if item.get("status") == "open" else 1,
        _PRIORITY_ORDER.get(item.get("priority", "normal"), 1),
        item.get("due") or "9999",
        item.get("created_at", ""),
    )


# ── Scheduler task store (timers live here too) ───────────────────────────────


def tasks_path() -> Path:
    return workspace_root() / "scheduler_tasks.json"


def load_tasks() -> dict[str, dict[str, Any]]:
    """Return scheduled tasks, treating an unreadable store as empty."""
    path = tasks_path()
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
            logger.warning("Scheduler: task store is not an object; ignoring it.")
    except (OSError, ValueError) as exc:
        # Never propagate: this runs inside the scheduler's background loop.
        logger.warning(f"Scheduler: could not load tasks file: {exc}")
    return {}


def save_tasks(tasks: dict[str, dict[str, Any]]) -> None:
    try:
        _write_json_atomic(tasks_path(), tasks)
    except OSError as exc:
        logger.error(f"Scheduler: could not save tasks file: {exc}")


def is_timer(task: Any) -> bool:
    return isinstance(task, dict) and task.get("kind") == "timer"


def timer_remaining_seconds(task: dict[str, Any], now: datetime | None = None) -> int:
    """Whole seconds until a timer is due, floored at zero."""
    now = now or datetime.now()
    try:
        due = datetime.fromisoformat(task["next_run"])
    except (KeyError, TypeError, ValueError):
        return 0
    return max(0, int((due - now).total_seconds()))


# ── Hover-window snapshots ────────────────────────────────────────────────────


def todo_snapshot(limit: int = _SNAPSHOT_LIMIT) -> dict[str, Any]:
    """Compact open-todo view for the todo_list hover window."""
    try:
        items = [item for item in load_todos() if item.get("status") == "open"]
    except ValueError as exc:
        return {"available": False, "message": str(exc), "open_count": 0, "items": []}

    items.sort(key=todo_sort_key)
    return {
        "available": True,
        "open_count": len(items),
        "items": [
            {
                "text": str(item.get("text", ""))[:200],
                "priority": item.get("priority", "normal"),
                "due": item.get("due"),
            }
            for item in items[:limit]
        ],
        "remaining": max(0, len(items) - limit),
    }


def timer_snapshot(limit: int = _SNAPSHOT_LIMIT) -> dict[str, Any]:
    """Compact pending-timer view for the hourglass hover window."""
    now = datetime.now()
    timers = [
        {
            "label": str(task.get("label") or "")[:120],
            "due_at": task.get("next_run"),
            "remaining_seconds": timer_remaining_seconds(task, now),
        }
        for task in load_tasks().values()
        if is_timer(task)
    ]
    timers.sort(key=lambda timer: timer["remaining_seconds"])
    return {
        "available": True,
        "count": len(timers),
        "timers": timers[:limit],
        "remaining": max(0, len(timers) - limit),
    }
