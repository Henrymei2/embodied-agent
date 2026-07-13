from __future__ import annotations

import importlib
import json
import sys

import pytest


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    sys.modules.pop("mcp_servers.calendar_tools", None)
    module = importlib.import_module("mcp_servers.calendar_tools")
    return importlib.reload(module)


def _decode(result: str) -> dict:
    assert not result.startswith("Error:")
    return json.loads(result)


def test_calendar_crud_roundtrip(calendar):
    created = _decode(
        calendar.add_calendar_event(
            "Project review",
            "2026-07-14T14:00:00-04:00",
            "2026-07-14T15:00:00-04:00",
            "Bring the demo notes",
        )
    )["event"]

    listed = _decode(
        calendar.list_calendar_events(
            "2026-07-14T00:00:00-04:00",
            "2026-07-15T00:00:00-04:00",
        )
    )["events"]
    assert [event["event_id"] for event in listed] == [created["event_id"]]
    assert listed[0]["start"] == "2026-07-14T18:00:00+00:00"

    updated = _decode(
        calendar.update_calendar_event(
            created["event_id"],
            title="Final project review",
            end="2026-07-14T15:30:00-04:00",
        )
    )["event"]
    assert updated["title"] == "Final project review"
    assert updated["created_at"] == created["created_at"]

    removed = _decode(calendar.remove_calendar_event(created["event_id"]))
    assert removed == {"ok": True, "event_id": created["event_id"]}
    assert _decode(
        calendar.list_calendar_events(
            "2026-07-14T00:00:00-04:00",
            "2026-07-15T00:00:00-04:00",
        )
    ) == {"events": []}


def test_calendar_requires_unambiguous_timezone(calendar):
    result = calendar.add_calendar_event(
        "Ambiguous",
        "2026-07-14T14:00:00",
        "2026-07-14T15:00:00",
    )

    assert result == "Error: start must include a UTC offset"


def test_calendar_rejects_inverted_range(calendar):
    result = calendar.add_calendar_event(
        "Backwards",
        "2026-07-14T16:00:00Z",
        "2026-07-14T15:00:00Z",
    )

    assert result == "Error: end must be later than start"


def test_calendar_limits_query_results(calendar):
    for index in range(3):
        _decode(
            calendar.add_calendar_event(
                f"Event {index}",
                f"2026-07-1{index + 4}T14:00:00Z",
                f"2026-07-1{index + 4}T15:00:00Z",
            )
        )

    events = _decode(
        calendar.list_calendar_events(
            "2026-07-14T00:00:00Z",
            "2026-07-20T00:00:00Z",
            limit=2,
        )
    )["events"]
    assert len(events) == 2


def test_calendar_corruption_fails_safely(calendar):
    calendar._events_path().write_text("not-json", encoding="utf-8")

    assert calendar.list_calendar_events().startswith(
        "Error: calendar store is unreadable"
    )
