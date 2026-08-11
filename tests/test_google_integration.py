from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from oauthlib.oauth2 import InvalidClientError, InvalidGrantError
from requests import ConnectionError as RequestsConnectionError

from src.open_llm_vtuber.google_integration import (
    GOOGLE_SCOPES,
    GoogleIntegrationError,
    GoogleIntegrationService,
    GoogleIntegrationSettings,
)
from src.open_llm_vtuber.request_security import build_trusted_hosts
from src.open_llm_vtuber.routes import init_google_integration_routes


def _service(tmp_path: Path, client_file: Path) -> GoogleIntegrationService:
    return GoogleIntegrationService(
        GoogleIntegrationSettings(
            enabled=True,
            client_secrets_file=str(client_file),
            redirect_uri="http://localhost:12393/api/integrations/google/oauth/callback",
            token_file=tmp_path / "private" / "token.json",
        )
    )


def test_missing_or_installed_client_is_not_configured(tmp_path):
    missing = _service(tmp_path, tmp_path / "missing.json")
    assert missing.status()["status"] == "unconfigured"

    installed = tmp_path / "installed.json"
    installed.write_text(json.dumps({"installed": {"client_id": "id"}}))
    assert _service(tmp_path, installed).configured is False

    malformed = tmp_path / "malformed.json"
    malformed.write_text("not-json")
    assert _service(tmp_path, malformed).configured is False


def test_authorization_uses_exact_redirect_all_scopes_and_state(tmp_path):
    client = tmp_path / "client.json"
    client.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "example.apps.googleusercontent.com",
                    "client_secret": "rotated-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [
                        "http://localhost:12393/api/integrations/google/oauth/callback"
                    ],
                }
            }
        )
    )
    service = _service(tmp_path, client)
    query = parse_qs(urlparse(service.authorization_url()).query)

    assert query["redirect_uri"] == [service.settings.redirect_uri]
    assert set(query["scope"][0].split()) == set(GOOGLE_SCOPES)
    assert query["access_type"] == ["offline"]
    assert "include_granted_scopes" not in query
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0] in service._states
    assert service._states[query["state"][0]][1]


def test_callback_state_is_single_use(tmp_path, monkeypatch):
    client = tmp_path / "client.json"
    client.write_text(
        json.dumps({"web": {"client_id": "id", "client_secret": "secret"}})
    )
    service = _service(tmp_path, client)
    service._states["state"] = (float("inf"), "pkce-verifier")

    class FakeCredentials:
        def to_json(self):
            return '{"token":"redacted"}'

    class FakeFlow:
        credentials = FakeCredentials()

        def fetch_token(self, code):
            assert code == "code"

    def fake_flow(state=None, code_verifier=None):
        assert state == "state"
        assert code_verifier == "pkce-verifier"
        return FakeFlow()

    monkeypatch.setattr(
        service,
        "_flow",
        fake_flow,
    )
    service.exchange_code("state", "code")
    assert service.settings.token_file.exists()
    if os.name != "nt":
        assert service.settings.token_file.stat().st_mode & 0o777 == 0o600
    assert "state" not in service._states

    with pytest.raises(GoogleIntegrationError, match="invalid or expired"):
        service.exchange_code("state", "code")


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (InvalidClientError(), "rejected the OAuth client credentials"),
        (InvalidGrantError(), "rejected the authorization code"),
        (RequestsConnectionError(), "token service could not be reached"),
    ],
)
def test_callback_reports_safe_token_exchange_failures(
    tmp_path, monkeypatch, failure, message
):
    client = tmp_path / "client.json"
    client.write_text(
        json.dumps({"web": {"client_id": "id", "client_secret": "secret"}})
    )
    service = _service(tmp_path, client)
    service._states["state"] = (float("inf"), "pkce-verifier")

    class FailingFlow:
        def fetch_token(self, code):
            raise failure

    monkeypatch.setattr(
        service,
        "_flow",
        lambda state=None, code_verifier=None: FailingFlow(),
    )

    with pytest.raises(GoogleIntegrationError, match=message):
        service.exchange_code("state", "sensitive-code")


def test_google_routes_sanitize_callback_and_status():
    class FakeService:
        def status(self):
            return {
                "configured": True,
                "connected": False,
                "status": "authorization_required",
                "message": "Authorize Google Calendar to continue.",
            }

        def authorization_url(self):
            return "https://accounts.google.com/o/oauth2/v2/auth?state=safe"

        def exchange_code(self, state, code):
            assert (state, code) == ("safe", "sensitive-code")

    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.include_router(init_google_integration_routes(FakeService()))
    client = TestClient(app, headers={"origin": "http://testserver"})

    status = client.get("/api/integrations/google/status")
    assert status.json()["status"] == "authorization_required"
    redirect = client.get("/api/integrations/google/authorize", follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"].startswith("https://accounts.google.com/")

    callback = client.get(
        "/api/integrations/google/oauth/callback",
        params={"state": "safe", "code": "sensitive-code"},
    )
    assert callback.status_code == 200
    assert "sensitive-code" not in callback.text
    assert callback.headers["referrer-policy"] == "no-referrer"
    assert callback.headers["cache-control"] == "no-store"


def test_gmail_unread_routes_page_and_sanitize_failures():
    class FakeService:
        def unread_status(self):
            return {"has_unread": True}

        def unread_messages(self, *, page_size, page_token):
            assert (page_size, page_token) == (20, "next")
            return {
                "messages": [
                    {
                        "message_id": "m1",
                        "thread_id": "t1",
                        "sender": "Sender <sender@example.com>",
                        "subject": "Hello",
                        "received_at": "today",
                        "snippet": "Preview",
                    }
                ],
                "next_page_token": None,
                "has_unread": True,
            }

        def recent_messages(self, *, limit):
            assert limit == 3
            return {
                "messages": [
                    {"message_id": f"m{index}", "subject": f"Subject {index}"}
                    for index in range(1, 4)
                ],
                "count": 3,
            }

        def upcoming_events(self, *, days):
            assert days == 7
            return {
                "events": [{"event_id": "e1", "title": "Review"}],
                "count": 1,
                "range_start": "2026-08-11",
                "range_end": "2026-08-17",
            }

        def status(self):
            return {}

    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.include_router(init_google_integration_routes(FakeService()))
    client = TestClient(app, headers={"origin": "http://testserver"})

    assert client.get("/api/integrations/google/gmail/unread-status").json() == {
        "has_unread": True
    }
    response = client.get(
        "/api/integrations/google/gmail/unread",
        params={"page_size": 20, "page_token": "next"},
    )
    assert response.status_code == 200
    assert response.json()["messages"][0]["message_id"] == "m1"
    recent = client.get("/api/integrations/google/gmail/recent")
    assert [item["subject"] for item in recent.json()["messages"]] == [
        "Subject 1", "Subject 2", "Subject 3"
    ]
    upcoming = client.get("/api/integrations/google/calendar/upcoming")
    assert upcoming.json()["events"][0]["title"] == "Review"
    assert (
        client.get(
            "/api/integrations/google/gmail/unread", params={"page_size": 51}
        ).status_code
        == 400
    )

    def fail():
        raise RuntimeError("sensitive upstream response")

    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    failing = FakeService()
    failing.unread_status = fail
    app.include_router(init_google_integration_routes(failing))
    failed = TestClient(app, headers={"origin": "http://testserver"}).get(
        "/api/integrations/google/gmail/unread-status"
    )
    assert failed.status_code == 502
    assert "sensitive upstream response" not in failed.text


def test_recent_inbox_messages_include_read_mail_metadata(tmp_path, monkeypatch):
    client_file = tmp_path / "client.json"
    client_file.write_text("{}")
    service = _service(tmp_path, client_file)
    calls = []

    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Messages:
        def list(self, **kwargs):
            calls.append(kwargs)
            return Request({"messages": [{"id": "m1"}, {"id": "m2"}]})

        def get(self, **kwargs):
            index = kwargs["id"][-1]
            return Request(
                {
                    "id": kwargs["id"],
                    "threadId": f"t{index}",
                    "payload": {"headers": [
                        {"name": "Subject", "value": f"Subject {index}"},
                        {"name": "From", "value": f"Sender {index}"},
                        {"name": "Date", "value": f"Date {index}"},
                    ]},
                    "snippet": f"Preview {index}",
                }
            )

    messages = Messages()
    monkeypatch.setattr(
        service,
        "gmail",
        lambda: type("Gmail", (), {"users": lambda self: type("Users", (), {"messages": lambda self: messages})()})(),
    )

    result = service.recent_messages(limit=3)
    assert calls[0]["labelIds"] == ["INBOX"]
    assert "UNREAD" not in calls[0]["labelIds"]
    assert [item["subject"] for item in result["messages"]] == ["Subject 1", "Subject 2"]


def test_upcoming_events_follows_google_pagination_and_preserves_event_details(tmp_path, monkeypatch):
    client_file = tmp_path / "client.json"
    client_file.write_text("{}")
    service = _service(tmp_path, client_file)
    page_tokens = []

    class Request:
        def __init__(self, value):
            self.value = value

        def execute(self):
            return self.value

    class Events:
        def list(self, **kwargs):
            page_tokens.append(kwargs.get("pageToken"))
            if kwargs.get("pageToken") is None:
                return Request({
                    "items": [{
                        "id": "e1", "summary": "All day", "location": "Room",
                        "description": "Full description",
                        "start": {"date": "2026-08-11"}, "end": {"date": "2026-08-12"},
                    }],
                    "nextPageToken": "next",
                })
            return Request({
                "items": [{
                    "id": "e2", "summary": "Timed",
                    "start": {"dateTime": "2026-08-12T09:00:00-05:00"},
                    "end": {"dateTime": "2026-08-12T10:00:00-05:00"},
                }]
            })

    events = Events()
    monkeypatch.setattr(
        service,
        "calendar",
        lambda: type("Calendar", (), {"events": lambda self: events})(),
    )

    result = service.upcoming_events(days=7)
    assert page_tokens == [None, "next"]
    assert result["count"] == 2
    assert result["events"][0]["all_day"] is True
    assert result["events"][0]["description"] == "Full description"
    assert result["events"][1]["all_day"] is False
