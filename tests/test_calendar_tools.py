from __future__ import annotations

import importlib
import json
import sys
from datetime import timedelta

import pytest

from src.open_llm_vtuber.google_integration import GoogleAuthorizationRequired


class FakeRequest:
    def __init__(self, value=None):
        self.value = value

    def execute(self):
        return self.value


class FakeEvents:
    def __init__(self):
        self.calls = []
        self.events = {
            "event-1": {
                "id": "event-1",
                "summary": "Project review",
                "start": {"dateTime": "2026-07-14T14:00:00-04:00"},
                "end": {"dateTime": "2026-07-14T15:00:00-04:00"},
                "description": "Bring notes",
            },
            "all-day": {
                "id": "all-day",
                "summary": "Holiday",
                "start": {"date": "2026-07-15"},
                "end": {"date": "2026-07-16"},
            },
        }

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return FakeRequest({"items": list(self.events.values())})

    def insert(self, **kwargs):
        self.calls.append(("insert", kwargs))
        return FakeRequest({"id": "new", **kwargs["body"]})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return FakeRequest(self.events[kwargs["eventId"]])

    def patch(self, **kwargs):
        self.calls.append(("patch", kwargs))
        return FakeRequest({"id": kwargs["eventId"], **kwargs["body"]})

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))
        return FakeRequest(None)


class FakeCalendar:
    def __init__(self):
        self.events_api = FakeEvents()

    def events(self):
        return self.events_api


class FakeService:
    def __init__(self):
        self.api = FakeCalendar()

    def calendar(self):
        return self.api


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    sys.modules.pop("mcp_servers.calendar_tools", None)
    module = importlib.import_module("mcp_servers.calendar_tools")
    module = importlib.reload(module)
    module._calendar_service = FakeService()
    return module


def _decode(result: str) -> dict:
    assert not result.startswith("Error:")
    return json.loads(result)


def test_calendar_crud_targets_primary_and_maps_fields(calendar):
    listed = _decode(
        calendar.list_calendar_events(
            "2026-07-14T00:00:00-04:00", "2026-07-16T00:00:00-04:00"
        )
    )["events"]
    assert listed[0]["event_id"] == "event-1"
    assert listed[1]["all_day"] is True
    assert _decode(calendar.get_calendar_event("event-1"))["event_id"] == "event-1"

    created = _decode(
        calendar.add_calendar_event(
            "New event",
            "2026-07-14T16:00:00-04:00",
            "2026-07-14T17:00:00-04:00",
            "Notes",
        )
    )["event"]
    assert created["title"] == "New event"

    updated = _decode(calendar.update_calendar_event("event-1", title="Updated"))[
        "event"
    ]
    assert updated["title"] == "Updated"
    assert _decode(calendar.remove_calendar_event("event-1"))["ok"] is True

    calls = calendar._calendar_service.api.events_api.calls
    assert all(call[1]["calendarId"] == "primary" for call in calls)
    list_args = calls[0][1]
    assert list_args["singleEvents"] is True
    assert list_args["orderBy"] == "startTime"


def test_calendar_requires_unambiguous_timezone(calendar):
    result = calendar.add_calendar_event(
        "Ambiguous", "2026-07-14T14:00:00", "2026-07-14T15:00:00"
    )
    assert result == "Error: start must include a UTC offset"


def test_calendar_rejects_inverted_range(calendar):
    result = calendar.add_calendar_event(
        "Backwards", "2026-07-14T16:00:00Z", "2026-07-14T15:00:00Z"
    )
    assert result == "Error: end must be later than start"


def test_calendar_clamps_query_limit(calendar):
    _decode(calendar.list_calendar_events(limit=5_000))
    args = calendar._calendar_service.api.events_api.calls[0][1]
    assert args["maxResults"] == 200


def test_calendar_requires_authorization(calendar):
    class Unauthorized:
        def calendar(self):
            raise GoogleAuthorizationRequired("Google Calendar is not authorized.")

    calendar._calendar_service = Unauthorized()
    assert calendar.list_calendar_events() == (
        "Error: Google Calendar is not authorized."
    )


def test_timestamp_parser_preserves_offsets(calendar):
    parsed = calendar._parse_time("2026-07-14T14:00:00-04:00", "start")
    assert parsed.hour == 14
    assert parsed.utcoffset() == -timedelta(hours=4)
