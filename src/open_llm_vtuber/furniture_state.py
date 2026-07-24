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

import re
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar

from filelock import FileLock, Timeout
from loguru import logger

from .workspace import DEFAULT_WORKSPACE_DIR

TODO_PRIORITIES = ("high", "normal", "low")
_PRIORITY_ORDER = {priority: index for index, priority in enumerate(TODO_PRIORITIES)}

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

# ── Diary memory snapshots ────────────────────────────────────────────────────

_DIARY_ENTRY_LIMIT = 30
_DIARY_PREVIEW_CHARS = 500
_DIARY_FULL_CHARS = 8_000
_DIARY_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.md$")


def diary_memory_dir() -> Path:
    return workspace_root() / "memory"


def _diary_files() -> list[Path]:
    root = diary_memory_dir()
    if not root.exists():
        return []
    return sorted(
        (
            path
            for path in root.iterdir()
            if path.is_file() and _DIARY_DATE_RE.fullmatch(path.name)
        ),
        key=lambda path: path.stem,
        reverse=True,
    )


def _clean_diary_text(text: str, limit: int) -> str:
    cleaned = text.strip()
    if cleaned.startswith("# Daily Memory"):
        cleaned = "\n".join(cleaned.splitlines()[1:]).strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def diary_snapshot(limit: int = _SNAPSHOT_LIMIT) -> dict[str, Any]:
    """Compact recent-memory view for the diary hover window."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        limit = _SNAPSHOT_LIMIT
    limit = max(1, min(limit, _DIARY_ENTRY_LIMIT))

    entries: list[dict[str, Any]] = []
    for path in _diary_files()[:limit]:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        preview = _clean_diary_text(raw, _DIARY_PREVIEW_CHARS)
        if not preview:
            preview = "(no notes yet)"
        entries.append(
            {
                "date": path.stem,
                "preview": preview,
            }
        )

    total = len(_diary_files())
    return {
        "available": True,
        "count": total,
        "entries": entries,
        "remaining": max(0, total - limit),
    }


def read_diary_entry(entry_date: str) -> dict[str, Any]:
    """Read one daily memory file by YYYY-MM-DD date."""
    if not isinstance(entry_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry_date):
        raise ValueError("entry_date must use YYYY-MM-DD format")
    path = diary_memory_dir() / f"{entry_date}.md"
    if not path.exists():
        raise ValueError(f"diary entry '{entry_date}' was not found")
    raw = path.read_text(encoding="utf-8")
    return {
        "date": entry_date,
        "content": _clean_diary_text(raw, _DIARY_FULL_CHARS),
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
