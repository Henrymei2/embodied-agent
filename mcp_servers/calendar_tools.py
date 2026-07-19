"""Google Calendar tools for the embodied companion."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from googleapiclient.errors import HttpError
from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.google_integration import (
    GoogleAuthorizationRequired,
    GoogleIntegrationError,
    GoogleIntegrationService,
    calendar_subprocess_settings,
)

_MAX_RESULTS = 200
mcp = FastMCP("calendar")
_calendar_service: GoogleIntegrationService | None = None


def _get_calendar_service() -> GoogleIntegrationService:
    global _calendar_service
    if _calendar_service is None:
        _calendar_service = GoogleIntegrationService(calendar_subprocess_settings())
    return _calendar_service


def _parse_time(value: str, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError(f"{field} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return parsed


def _validate_text(value: str, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return cleaned


def _event_time(value: Any) -> tuple[str, bool]:
    if not isinstance(value, dict):
        return "", False
    if isinstance(value.get("dateTime"), str):
        return value["dateTime"], False
    if isinstance(value.get("date"), str):
        return value["date"], True
    return "", False


def _serialize_google_event(event: dict[str, Any]) -> dict[str, Any]:
    start, start_all_day = _event_time(event.get("start"))
    end, end_all_day = _event_time(event.get("end"))
    return {
        "event_id": event.get("id", ""),
        "title": event.get("summary") or "(untitled)",
        "start": start,
        "end": end,
        "all_day": start_all_day and end_all_day,
        "notes": event.get("description") or "",
        "created_at": event.get("created"),
        "updated_at": event.get("updated"),
        "html_link": event.get("htmlLink"),
    }


def _timed_event_body(
    title: str, start: datetime, end: datetime, notes: str
) -> dict[str, Any]:
    if end <= start:
        raise ValueError("end must be later than start")
    if not isinstance(notes, str):
        raise ValueError("notes must be a string")
    return {
        "summary": _validate_text(title, "title", 200),
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "description": notes.strip()[:2_000],
    }


def _error(exc: Exception) -> str:
    if isinstance(exc, GoogleAuthorizationRequired):
        return f"Error: {exc}"
    if isinstance(exc, HttpError) and exc.resp.status == 401:
        return "Error: Google authorization is invalid. Please authorize again."
    if isinstance(exc, HttpError) and exc.resp.status == 404:
        return "Error: calendar event was not found"
    if isinstance(exc, (HttpError, OSError)):
        return "Error: Google Calendar request failed"
    return f"Error: {exc}"


@mcp.tool()
def list_calendar_events(
    start: str | None = None,
    end: str | None = None,
    limit: int = 50,
) -> str:
    """List Google Calendar events overlapping a range (defaults to 7 days)."""
    try:
        start_at = _parse_time(start, "start") if start else datetime.now(timezone.utc)
        end_at = _parse_time(end, "end") if end else start_at + timedelta(days=7)
        if end_at <= start_at:
            raise ValueError("end must be later than start")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, _MAX_RESULTS))
        response = (
            _get_calendar_service()
            .calendar()
            .events()
            .list(
                calendarId="primary",
                timeMin=start_at.isoformat(),
                timeMax=end_at.isoformat(),
                maxResults=limit,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        events = [
            _serialize_google_event(item)
            for item in response.get("items", [])
            if isinstance(item, dict)
        ]
        return json.dumps({"events": events}, ensure_ascii=False)
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def add_calendar_event(title: str, start: str, end: str, notes: str = "") -> str:
    """Create a timed event on the primary Google Calendar."""
    try:
        body = _timed_event_body(
            title, _parse_time(start, "start"), _parse_time(end, "end"), notes
        )
        event = (
            _get_calendar_service()
            .calendar()
            .events()
            .insert(calendarId="primary", body=body)
            .execute()
        )
        return json.dumps(
            {"ok": True, "event": _serialize_google_event(event)}, ensure_ascii=False
        )
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def update_calendar_event(
    event_id: str,
    title: str | None = None,
    start: str | None = None,
    end: str | None = None,
    notes: str | None = None,
) -> str:
    """Update selected fields of a primary Google Calendar event."""
    try:
        if not isinstance(event_id, str) or not event_id or len(event_id) > 1024:
            raise ValueError("event_id must be a non-empty string")
        events_api = _get_calendar_service().calendar().events()
        current = events_api.get(calendarId="primary", eventId=event_id).execute()
        current_start, current_all_day = _event_time(current.get("start"))
        current_end, _ = _event_time(current.get("end"))
        if current_all_day and (start is None) != (end is None):
            raise ValueError(
                "start and end must both be provided when converting an all-day event"
            )
        if current_all_day and start is None and end is None:
            if title is not None:
                current["summary"] = _validate_text(title, "title", 200)
            if notes is not None:
                current["description"] = notes.strip()[:2_000]
            body = current
        else:
            body = _timed_event_body(
                title if title is not None else current.get("summary", "(untitled)"),
                _parse_time(start if start is not None else current_start, "start"),
                _parse_time(end if end is not None else current_end, "end"),
                notes if notes is not None else current.get("description", ""),
            )
        event = events_api.patch(
            calendarId="primary", eventId=event_id, body=body
        ).execute()
        return json.dumps(
            {"ok": True, "event": _serialize_google_event(event)}, ensure_ascii=False
        )
    except (GoogleIntegrationError, HttpError, OSError, TypeError, ValueError) as exc:
        return _error(exc)


@mcp.tool()
def remove_calendar_event(event_id: str) -> str:
    """Delete an event from the primary Google Calendar."""
    try:
        if not isinstance(event_id, str) or not event_id or len(event_id) > 1024:
            raise ValueError("event_id must be a non-empty string")
        (
            _get_calendar_service()
            .calendar()
            .events()
            .delete(calendarId="primary", eventId=event_id)
            .execute()
        )
        return json.dumps({"ok": True, "event_id": event_id})
    except (GoogleIntegrationError, HttpError, OSError, ValueError) as exc:
        return _error(exc)


if __name__ == "__main__":
    mcp.run()
