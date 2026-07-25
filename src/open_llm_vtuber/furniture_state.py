"""Persistent state behind room furniture.

Four stores live here:

* the todo list shown on the `todo_list` object, in `<workspace>/todo/todos.json`
* the scheduler task file, `<workspace>/scheduler_tasks.json`, which also holds
  the one-shot timers set at the `hourglass` object
* the library, `<workspace>/library/library.json`, holding the books shelved at
  the `bookshelf` and the reading sessions continued at the `reading_chair`
* the plans drafted at the `writing_desk`, in `<workspace>/plans/plans.json`,
  which the user approves or declines from that object's hover window

All of them are read from three places — the MCP tool subprocesses, the
in-process scheduler loop, and the read-only routes that back the furniture
hover windows — so the file format, the ordering rules, and the atomic writer
are defined once here rather than reimplemented per caller.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from filelock import FileLock, Timeout
from loguru import logger

from .workspace import DEFAULT_WORKSPACE_DIR
from .utils.timezone import now_local

TODO_PRIORITIES = ("high", "normal", "low")
_PRIORITY_ORDER = {priority: index for index, priority in enumerate(TODO_PRIORITIES)}

PLAN_STATUSES = ("pending", "approved", "declined", "withdrawn")

# How many rows a hover window shows before it reports a remainder.
_SNAPSHOT_LIMIT = 8
_TASK_LOCK_TIMEOUT = 5
_MutationResult = TypeVar("_MutationResult")


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


# ── Library store (bookshelf + reading chair) ─────────────────────────────────


def library_path() -> Path:
    return workspace_root() / "library" / "library.json"


def book_text_path(source_id: str) -> Path:
    """Local cache for one downloaded book, keyed by its numeric source ID."""
    if not re.fullmatch(r"[0-9]{1,12}", str(source_id)):
        raise ValueError("book source id must be numeric")
    return workspace_root() / "library" / "texts" / f"{source_id}.txt"


def load_library() -> dict[str, Any]:
    """Return ``{"shelf": [...], "sessions": [...]}`` from the library store."""
    path = library_path()
    if not path.exists():
        return {"shelf": [], "sessions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"library is unreadable: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("library has an invalid format")
    return {
        "shelf": [item for item in payload.get("shelf", []) if isinstance(item, dict)]
        if isinstance(payload.get("shelf"), list)
        else [],
        "sessions": [
            item for item in payload.get("sessions", []) if isinstance(item, dict)
        ]
        if isinstance(payload.get("sessions"), list)
        else [],
    }


def save_library(library: dict[str, Any]) -> None:
    _write_json_atomic(
        library_path(),
        {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "shelf": library.get("shelf", []),
            "sessions": library.get("sessions", []),
        },
    )


def active_session(library: dict[str, Any]) -> dict[str, Any] | None:
    """Return the most recently touched unfinished reading session."""
    reading = [
        session
        for session in library.get("sessions", [])
        if session.get("status") == "reading"
    ]
    if not reading:
        return None
    return max(reading, key=lambda session: str(session.get("updated_at", "")))


# ── Plan store (writing desk) ─────────────────────────────────────────────────


def plans_path() -> Path:
    return workspace_root() / "plans" / "plans.json"


def load_plans() -> list[dict[str, Any]]:
    """Return stored plans, raising ValueError if the store is unreadable."""
    path = plans_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"plan store is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("plans"), list):
        raise ValueError("plan store has an invalid format")
    return [plan for plan in payload["plans"] if isinstance(plan, dict)]


def save_plans(plans: list[dict[str, Any]]) -> None:
    _write_json_atomic(
        plans_path(),
        {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "plans": plans,
        },
    )


def record_plan_decision(plan_id: str, decision: str) -> dict[str, Any] | None:
    """Approve or decline a pending plan on the user's behalf.

    The hover window at the writing desk is the only path that calls this: the
    agent drafts and revises plans, but never decides on its own. Returns the
    updated plan, or None when no pending plan carries that ID.
    """
    if decision not in {"approved", "declined"}:
        raise ValueError("decision must be 'approved' or 'declined'")
    if not isinstance(plan_id, str) or not plan_id or len(plan_id) > 80:
        raise ValueError("plan_id must be a non-empty string")

    plans = load_plans()
    index = next(
        (
            position
            for position, plan in enumerate(plans)
            if plan.get("plan_id") == plan_id and plan.get("status") == "pending"
        ),
        None,
    )
    if index is None:
        return None

    now = datetime.now(timezone.utc).isoformat()
    plan = {
        **plans[index],
        "status": decision,
        "decided_at": now,
        "updated_at": now,
        "decided_by": "user",
    }
    plans[index] = plan
    save_plans(plans)
    return plan


# ── Scheduler task store (timers live here too) ───────────────────────────────


def tasks_path() -> Path:
    return workspace_root() / "scheduler_tasks.json"


def tasks_lock_path() -> Path:
    return workspace_root() / "scheduler_tasks.lock"


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
    """Replace the task store under its cross-process lock."""
    try:
        lock_path = tasks_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path), timeout=_TASK_LOCK_TIMEOUT):
            _write_json_atomic(tasks_path(), tasks)
    except (OSError, Timeout) as exc:
        logger.error(f"Scheduler: could not save tasks file: {exc}")


def mutate_tasks(
    mutation: Callable[[dict[str, dict[str, Any]]], _MutationResult],
    *,
    save_if: Callable[[_MutationResult], bool] | None = None,
) -> _MutationResult:
    """Atomically apply a read-modify-write operation to the shared task store.

    Scheduler and timer MCP servers run in subprocesses while the scheduler loop
    and HTTP API run in the main process. The lock prevents any of those writers
    from restoring a deleted task or dropping a concurrently created task.
    """
    lock_path = tasks_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(lock_path), timeout=_TASK_LOCK_TIMEOUT):
            tasks = load_tasks()
            result = mutation(tasks)
            if save_if is None or save_if(result):
                _write_json_atomic(tasks_path(), tasks)
            return result
    except Timeout as exc:
        raise OSError("scheduler task store is busy") from exc


def delete_task(
    task_id: str,
    predicate: Callable[[dict[str, Any]], bool] | None = None,
) -> bool:
    """Delete one task, optionally only when it matches ``predicate``."""

    def delete(tasks: dict[str, dict[str, Any]]) -> bool:
        task = tasks.get(task_id)
        if task is None or (predicate is not None and not predicate(task)):
            return False
        del tasks[task_id]
        return True

    return mutate_tasks(delete, save_if=bool)


def is_timer(task: Any) -> bool:
    return isinstance(task, dict) and task.get("kind") == "timer"


def timer_remaining_seconds(task: dict[str, Any], now: datetime | None = None) -> int:
    """Whole seconds until a timer is due, floored at zero."""
    now = now or now_local()
    try:
        due = datetime.fromisoformat(task["next_run"])
    except (KeyError, TypeError, ValueError):
        return 0
    if due.tzinfo is None:
        # Legacy timers persisted before timestamps were made timezone-aware.
        due = due.replace(tzinfo=now.tzinfo)
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
    now = now_local()
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


def bookshelf_snapshot(limit: int = _SNAPSHOT_LIMIT) -> dict[str, Any]:
    """Shelved books, newest first, for the bookshelf hover window."""
    try:
        shelf = load_library()["shelf"]
    except ValueError as exc:
        return {"available": False, "message": str(exc), "count": 0, "books": []}

    shelf.sort(key=lambda book: str(book.get("added_at", "")), reverse=True)
    return {
        "available": True,
        "count": len(shelf),
        "books": [
            {
                "title": str(book.get("title", ""))[:160],
                "author": str(book.get("author") or "")[:120],
                "year": book.get("year"),
                "readable": bool(book.get("text_source_id")),
            }
            for book in shelf[:limit]
        ],
        "remaining": max(0, len(shelf) - limit),
    }


def reading_snapshot(note_limit: int = 3) -> dict[str, Any]:
    """Current reading progress and latest notes, for the reading chair window."""
    try:
        library = load_library()
    except ValueError as exc:
        return {"available": False, "message": str(exc), "session": None}

    session = active_session(library)
    if session is None:
        finished = [
            item
            for item in library.get("sessions", [])
            if item.get("status") == "finished"
        ]
        return {
            "available": True,
            "session": None,
            "finished_count": len(finished),
        }

    notes = [note for note in session.get("notes", []) if isinstance(note, dict)]
    return {
        "available": True,
        "session": {
            "title": str(session.get("title", ""))[:160],
            "author": str(session.get("author") or "")[:120],
            "section": session.get("section", 0),
            "progress_percent": session.get("progress_percent", 0),
            "updated_at": session.get("updated_at"),
            "notes": [
                {
                    "section": note.get("section"),
                    "summary": str(note.get("summary", ""))[:400],
                }
                for note in notes[-note_limit:][::-1]
            ],
        },
        "finished_count": sum(
            1
            for item in library.get("sessions", [])
            if item.get("status") == "finished"
        ),
    }


def plan_snapshot(limit: int = 3) -> dict[str, Any]:
    """Pending plans awaiting a decision, plus the last decided one."""
    try:
        plans = load_plans()
    except ValueError as exc:
        return {"available": False, "message": str(exc), "pending": [], "recent": None}

    def as_window_plan(plan: dict[str, Any]) -> dict[str, Any]:
        steps = plan.get("steps")
        return {
            "plan_id": str(plan.get("plan_id", ""))[:80],
            "title": str(plan.get("title", ""))[:160],
            "goal": str(plan.get("goal", ""))[:600],
            "steps": [str(step)[:200] for step in steps[:8]]
            if isinstance(steps, list)
            else [],
            "status": plan.get("status", "pending"),
            "created_at": plan.get("created_at"),
        }

    pending = sorted(
        (plan for plan in plans if plan.get("status") == "pending"),
        key=lambda plan: str(plan.get("created_at", "")),
    )
    decided = sorted(
        (plan for plan in plans if plan.get("status") in {"approved", "declined"}),
        key=lambda plan: str(plan.get("decided_at", "")),
    )
    return {
        "available": True,
        "pending": [as_window_plan(plan) for plan in pending[:limit]],
        "pending_count": len(pending),
        "recent": as_window_plan(decided[-1]) if decided else None,
    }


def scheduler_events_snapshot() -> dict[str, Any]:
    """Return normalized pending tasks for the scheduler management UI."""
    events: list[dict[str, Any]] = []
    for task_id, task in load_tasks().items():
        if not isinstance(task, dict) or not isinstance(task.get("next_run"), str):
            continue
        raw_interval = task.get("interval_seconds")
        interval = (
            raw_interval
            if isinstance(raw_interval, (int, float))
            and not isinstance(raw_interval, bool)
            and raw_interval > 0
            else None
        )
        priority = task.get("priority", 10)
        if not isinstance(priority, int) or isinstance(priority, bool):
            priority = 10
        run_count = task.get("run_count", 0)
        if not isinstance(run_count, int) or isinstance(run_count, bool):
            run_count = 0
        created_at = task.get("created_at")
        if not isinstance(created_at, str):
            created_at = None
        kind = "timer" if is_timer(task) else "recurring" if interval else "one_time"
        events.append(
            {
                "id": str(task_id),
                "kind": kind,
                "label": str(task.get("label") or ""),
                "prompt": str(task.get("prompt") or ""),
                "priority": priority,
                "interval_seconds": interval,
                "report": bool(task.get("report", True)),
                "next_run": task["next_run"],
                "created_at": created_at,
                "run_count": run_count,
            }
        )

    def next_run_key(event: dict[str, Any]) -> float:
        try:
            return datetime.fromisoformat(event["next_run"]).timestamp()
        except (TypeError, ValueError, OverflowError):
            return float("inf")

    events.sort(key=next_run_key)
    return {"count": len(events), "events": events}
