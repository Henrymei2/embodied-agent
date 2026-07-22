"""Todo-list tools for the embodied companion.

These tools belong to the `todo_list` object in the room: the agent has to walk
to the todo list before it can read or edit it. Items live in
`<workspace>/todo/todos.json`; no account or network access is involved, and the
same file backs the hover window shown when the user points at the object.

Run standalone for a quick sanity check:
    uv run python mcp_servers/todo_tools.py
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.furniture_state import (
    TODO_PRIORITIES,
    load_todos,
    save_todos,
    todo_sort_key,
)

_MAX_ITEMS = 500
_MAX_RESULTS = 200
_MAX_TEXT = 500
_MAX_NOTES = 2_000

mcp = FastMCP("todo")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_text(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return cleaned


def _validate_priority(value: str) -> str:
    if not isinstance(value, str) or value.strip().lower() not in TODO_PRIORITIES:
        raise ValueError(f"priority must be one of: {', '.join(TODO_PRIORITIES)}")
    return value.strip().lower()


def _validate_due(value: str | None) -> str | None:
    """Normalize an optional due date to UTC, matching the calendar contract."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError("due must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("due must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("due must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat()


def _find(items: list[dict[str, Any]], todo_id: str) -> int:
    if not isinstance(todo_id, str) or not todo_id or len(todo_id) > 80:
        raise ValueError("todo_id must be a non-empty string")
    index = next(
        (i for i, item in enumerate(items) if item.get("todo_id") == todo_id),
        None,
    )
    if index is None:
        raise ValueError(f"todo '{todo_id}' was not found")
    return index


@mcp.tool()
def list_todos(include_done: bool = False, limit: int = 50) -> str:
    """List todo items, open ones first. Requires standing near the todo_list.

    Args:
        include_done: Also return completed items (default False).
        limit: Maximum number of items to return.
    """
    try:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, _MAX_RESULTS))
        items = load_todos()
        open_count = sum(1 for item in items if item.get("status") == "open")
        if not include_done:
            items = [item for item in items if item.get("status") == "open"]
        items.sort(key=todo_sort_key)
        return json.dumps(
            {"open_count": open_count, "items": items[:limit]},
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def add_todo(text: str, priority: str = "normal", due: str | None = None) -> str:
    """Add an item to the todo list. Requires standing near the todo_list.

    Args:
        text: What needs doing.
        priority: "high", "normal" (default), or "low".
        due: Optional ISO 8601 due date with a UTC offset, for example
            "2026-07-22T17:00:00-04:00".
    """
    try:
        items = load_todos()
        if len(items) >= _MAX_ITEMS:
            raise ValueError(f"todo list is limited to {_MAX_ITEMS} items")
        now = _now()
        item = {
            "todo_id": uuid.uuid4().hex[:8],
            "text": _validate_text(text, "text", _MAX_TEXT),
            "status": "open",
            "priority": _validate_priority(priority),
            "due": _validate_due(due),
            "created_at": now,
            "updated_at": now,
            "completed_at": None,
        }
        items.append(item)
        save_todos(items)
        return json.dumps({"ok": True, "item": item}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def update_todo(
    todo_id: str,
    text: str | None = None,
    priority: str | None = None,
    due: str | None = None,
) -> str:
    """Edit an existing todo item. Requires standing near the todo_list.

    Only the supplied fields change. Pass due="" to clear a due date.
    """
    try:
        items = load_todos()
        index = _find(items, todo_id)
        item = dict(items[index])
        if text is not None:
            item["text"] = _validate_text(text, "text", _MAX_TEXT)
        if priority is not None:
            item["priority"] = _validate_priority(priority)
        if due is not None:
            item["due"] = _validate_due(due)
        item["updated_at"] = _now()
        items[index] = item
        save_todos(items)
        return json.dumps({"ok": True, "item": item}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def complete_todo(todo_id: str, notes: str = "") -> str:
    """Mark a todo item as done. Requires standing near the todo_list.

    Args:
        todo_id: ID returned by add_todo or list_todos.
        notes: Optional short note about how it was finished.
    """
    try:
        if not isinstance(notes, str):
            raise ValueError("notes must be a string")
        items = load_todos()
        index = _find(items, todo_id)
        item = dict(items[index])
        if item.get("status") == "done":
            raise ValueError(f"todo '{todo_id}' is already done")
        now = _now()
        item["status"] = "done"
        item["completed_at"] = now
        item["updated_at"] = now
        if notes.strip():
            item["notes"] = notes.strip()[:_MAX_NOTES]
        items[index] = item
        save_todos(items)
        return json.dumps({"ok": True, "item": item}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def remove_todo(todo_id: str) -> str:
    """Delete a todo item outright. Requires standing near the todo_list."""
    try:
        items = load_todos()
        index = _find(items, todo_id)
        del items[index]
        save_todos(items)
        return json.dumps({"ok": True, "todo_id": todo_id})
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
