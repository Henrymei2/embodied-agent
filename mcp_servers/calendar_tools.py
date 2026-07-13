"""Private, workspace-local calendar tools for the embodied companion."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.workspace import ensure_workspace

_WORKSPACE_DIR = os.environ.get("EMBODIED_AGENT_WORKSPACE") or None
_MAX_EVENTS = 5_000
_MAX_RESULTS = 200

mcp = FastMCP("calendar")


def _events_path() -> Path:
    workspace = ensure_workspace(_WORKSPACE_DIR)
    workspace.calendar_dir.mkdir(parents=True, exist_ok=True)
    return workspace.calendar_dir / "events.json"


def _load_events() -> list[dict[str, Any]]:
    path = _events_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"calendar store is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise ValueError("calendar store has an invalid format")
    return [event for event in payload["events"] if isinstance(event, dict)]


def _save_events(events: list[dict[str, Any]]) -> None:
    path = _events_path()
    temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
    payload = {
        "version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "events": events,
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _parse_time(value: str, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError(f"{field} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _validate_text(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return cleaned


def _serialize_event(
    title: str,
    start: datetime,
    end: datetime,
    notes: str,
    event_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if end <= start:
        raise ValueError("end must be later than start")
    if not isinstance(notes, str):
        raise ValueError("notes must be a string")
    now = datetime.now(timezone.utc).isoformat()
    return {
        "event_id": event_id or uuid.uuid4().hex,
        "title": _validate_text(title, "title", 200),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "notes": notes.strip()[:2_000],
        "created_at": created_at or now,
        "updated_at": now,
    }


@mcp.tool()
def list_calendar_events(
    start: str | None = None,
    end: str | None = None,
    limit: int = 50,
) -> str:
    """List local events overlapping a time range (defaults to the next 7 days)."""
    try:
        start_at = _parse_time(start, "start") if start else datetime.now(timezone.utc)
        end_at = _parse_time(end, "end") if end else start_at + timedelta(days=7)
        if end_at <= start_at:
            raise ValueError("end must be later than start")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, _MAX_RESULTS))
        matches = [
            event
            for event in _load_events()
            if _parse_time(event["start"], "stored start") < end_at
            and _parse_time(event["end"], "stored end") > start_at
        ]
        matches.sort(key=lambda event: event["start"])
        return json.dumps({"events": matches[:limit]}, ensure_ascii=False)
    except (KeyError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def add_calendar_event(
    title: str,
    start: str,
    end: str,
    notes: str = "",
) -> str:
    """Add an event to the private local calendar using offset-aware ISO times."""
    try:
        events = _load_events()
        if len(events) >= _MAX_EVENTS:
            raise ValueError(f"calendar is limited to {_MAX_EVENTS} events")
        event = _serialize_event(
            title,
            _parse_time(start, "start"),
            _parse_time(end, "end"),
            notes,
        )
        events.append(event)
        events.sort(key=lambda item: item["start"])
        _save_events(events)
        return json.dumps({"ok": True, "event": event}, ensure_ascii=False)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def update_calendar_event(
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    notes: str | None = None,
) -> str:
    """Update selected fields of an existing local calendar event."""
    try:
        if not isinstance(event_id, str) or not event_id or len(event_id) > 80:
            raise ValueError("event_id must be a non-empty string")
        events = _load_events()
        index = next(
            (i for i, event in enumerate(events) if event.get("event_id") == event_id),
            None,
        )
        if index is None:
            raise ValueError(f"calendar event '{event_id}' was not found")
        current = events[index]
        event = _serialize_event(
            title if title is not None else current["title"],
            _parse_time(start if start is not None else current["start"], "start"),
            _parse_time(end if end is not None else current["end"], "end"),
            notes if notes is not None else current.get("notes", ""),
            event_id=current["event_id"],
            created_at=current.get("created_at"),
        )
        events[index] = event
        events.sort(key=lambda item: item["start"])
        _save_events(events)
        return json.dumps({"ok": True, "event": event}, ensure_ascii=False)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def remove_calendar_event(event_id: str) -> str:
    """Remove an event from the private local calendar by ID."""
    try:
        if not isinstance(event_id, str) or not event_id or len(event_id) > 80:
            raise ValueError("event_id must be a non-empty string")
        events = _load_events()
        remaining = [event for event in events if event.get("event_id") != event_id]
        if len(remaining) == len(events):
            raise ValueError(f"calendar event '{event_id}' was not found")
        _save_events(remaining)
        return json.dumps({"ok": True, "event_id": event_id})
    except (OSError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
