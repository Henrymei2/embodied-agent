from __future__ import annotations

import time

import pytest

from src.open_llm_vtuber.autonomy.agenda import AgendaStore
from src.open_llm_vtuber.autonomy.event_producers import (
    CalendarEventProducer,
    EventProducerManager,
    GmailEventProducer,
    ProducerStateStore,
    WorkspaceEventProducer,
)
from src.open_llm_vtuber.autonomy.triggers import (
    TriggerEnvelope,
    TriggerReference,
    publish_trigger,
)


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def test_typed_trigger_is_content_free_and_idempotent(tmp_path):
    store = AgendaStore(tmp_path)
    envelope = TriggerEnvelope(
        TriggerReference(
            source="gmail",
            resource_type="message",
            resource_id="message-1",
            revision="history-1",
            change_type="unread",
        )
    )

    first = publish_trigger(store, envelope)
    replay = publish_trigger(store, envelope)

    assert first.deduplicated is False
    assert replay.deduplicated is True
    assert replay.event.id == first.event.id
    serialized = str(replay.event)
    assert "message-1" in serialized
    assert "subject" not in serialized.lower()
    assert replay.event.summary == "The mailbox received a new unread Gmail message."


@pytest.mark.parametrize(
    "reference",
    [
        TriggerReference("gmail", "file", "id", "rev", "unread"),
        TriggerReference("workspace", "file", "../escape", "rev", "unknown"),
        TriggerReference("google_calendar", "calendar_event", "id\n", "rev", "updated"),
    ],
)
def test_typed_trigger_rejects_invalid_references(reference):
    with pytest.raises(ValueError):
        TriggerEnvelope(reference).validate()


class GmailMessages:
    def get(self, **kwargs):
        return Request({"id": kwargs["id"], "historyId": "102", "labelIds": ["UNREAD"]})


class GmailHistory:
    def __init__(self, users):
        self.users = users

    def list(self, **kwargs):
        self.users.history_calls.append(kwargs)
        return Request(self.users.history_response)


class GmailUsers:
    def __init__(self):
        self.profile_id = "100"
        self.history_calls = []
        self.history_response = {
            "historyId": "102",
            "history": [{"messagesAdded": [{"message": {"id": "m-1"}}]}],
        }

    def getProfile(self, **kwargs):  # noqa: N802 - mirrors Google client
        return Request({"historyId": self.profile_id})

    def history(self):
        return GmailHistory(self)

    def messages(self):
        return GmailMessages()


class GmailApi:
    def __init__(self):
        self.users_api = GmailUsers()

    def users(self):
        return self.users_api


class GmailService:
    def __init__(self):
        self.api = GmailApi()

    def gmail(self):
        return self.api


def test_gmail_baselines_then_publishes_new_unread_once(tmp_path):
    store = AgendaStore(tmp_path)
    service = GmailService()
    producer = GmailEventProducer(service, store, ProducerStateStore(tmp_path))

    producer.poll_once()
    assert store.load().inbox == []
    producer.poll_once()
    producer.poll_once()

    events = store.load().inbox
    assert len(events) == 1
    assert events[0].payload["trigger"]["resource_id"] == "m-1"
    assert "subject" not in events[0].payload["trigger"]


class CalendarEvents:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(kwargs)
        if "syncToken" not in kwargs:
            return Request({"items": [{"id": "existing"}], "nextSyncToken": "sync-1"})
        return Request(
            {
                "items": [
                    {
                        "id": "event-1",
                        "created": "2026-08-08T10:00:00Z",
                        "updated": "2026-08-08T11:00:00Z",
                        "summary": "UNTRUSTED TITLE",
                    },
                    {
                        "id": "event-2",
                        "updated": "2026-08-08T12:00:00Z",
                        "status": "cancelled",
                    },
                ],
                "nextSyncToken": "sync-2",
            }
        )


class CalendarApi:
    def __init__(self):
        self.events_api = CalendarEvents()

    def events(self):
        return self.events_api


class CalendarService:
    def __init__(self):
        self.api = CalendarApi()

    def calendar(self):
        return self.api


def test_calendar_baselines_and_publishes_changes_without_title(tmp_path):
    store = AgendaStore(tmp_path)
    producer = CalendarEventProducer(
        CalendarService(), store, ProducerStateStore(tmp_path)
    )

    producer.poll_once()
    assert store.load().inbox == []
    producer.poll_once()

    events = store.load().inbox
    assert [event.kind for event in events] == [
        "calendar_event.updated",
        "calendar_event.deleted",
    ]
    assert "UNTRUSTED TITLE" not in str(events)


def test_workspace_rebaselines_then_debounces_changes(tmp_path):
    watched = tmp_path / "demo-inbox"
    watched.mkdir()
    existing = watched / "existing.txt"
    existing.write_text("baseline")
    store = AgendaStore(tmp_path)
    producer = WorkspaceEventProducer(tmp_path, "demo-inbox", 0.1, store)

    producer.poll_once()
    assert store.load().inbox == []

    created = watched / "ready.txt"
    created.write_text("ready")
    producer.poll_once()
    assert store.load().inbox == []
    time.sleep(0.11)
    producer.poll_once()
    assert [event.kind for event in store.load().inbox] == ["file.created"]

    created.write_text("updated")
    producer.poll_once()
    time.sleep(0.11)
    producer.poll_once()
    created.unlink()
    producer.poll_once()
    assert [event.kind for event in store.load().inbox] == [
        "file.created",
        "file.modified",
        "file.deleted",
    ]


def test_workspace_rejects_escape_and_ignores_symlink(tmp_path):
    with pytest.raises(ValueError):
        WorkspaceEventProducer(tmp_path, "../outside", 0.1, AgendaStore(tmp_path))

    watched = tmp_path / "demo-inbox"
    watched.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside")
    try:
        (watched / "link.txt").symlink_to(outside)
        producer = WorkspaceEventProducer(
            tmp_path, "demo-inbox", 0.1, AgendaStore(tmp_path)
        )
        producer.poll_once()
        assert producer.known == {}
    finally:
        outside.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_manager_isolates_a_failing_producer():
    manager = EventProducerManager()

    class Failing:
        def poll_once(self):
            raise RuntimeError("secret external failure")

    manager.add("gmail", Failing(), 1.0)
    await asyncio_sleep_until(lambda: manager.health["gmail"].status == "degraded")
    assert manager.health["gmail"].last_error == (
        "RuntimeError: external source unavailable"
    )
    await manager.close()


async def asyncio_sleep_until(predicate, timeout: float = 1.0):
    import asyncio

    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.01)
