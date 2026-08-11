from __future__ import annotations

import base64
import importlib
import json
import sys

import pytest


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


def _message(message_id="m1"):
    body = base64.urlsafe_b64encode(b"Hello from the body").decode()
    return {
        "id": message_id,
        "threadId": "t1",
        "labelIds": ["INBOX", "UNREAD"],
        "snippet": "Hello",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "A subject"},
                {"name": "Date", "value": "Tue, 21 Jul 2026 12:00:00 -0500"},
            ],
            "body": {"data": body},
        },
    }


class Messages:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return Request({"messages": [{"id": "m1"}], "nextPageToken": "next"})

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return Request(_message(kwargs["id"]))

    def send(self, **kwargs):
        self.calls.append(("send", kwargs))
        return Request({"id": "sent", "threadId": "sent-thread"})

    def modify(self, **kwargs):
        self.calls.append(("modify", kwargs))
        return Request({"id": kwargs["id"]})


class Drafts:
    def create(self, **kwargs):
        return Request({"id": "draft", "message": {"id": "draft-message"}})


class Users:
    def __init__(self):
        self.messages_api = Messages()

    def messages(self):
        return self.messages_api

    def drafts(self):
        return Drafts()


class Gmail:
    def __init__(self):
        self.users_api = Users()

    def users(self):
        return self.users_api


class Service:
    def __init__(self):
        self.api = Gmail()

    def gmail(self):
        return self.api


@pytest.fixture
def gmail(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path))
    sys.modules.pop("mcp_servers.gmail_tools", None)
    module = importlib.import_module("mcp_servers.gmail_tools")
    module = importlib.reload(module)
    module._gmail_service = Service()
    return module


def decode(value):
    assert not value.startswith("Error:")
    return json.loads(value)


def test_gmail_search_read_send_and_mutations(gmail):
    page = decode(gmail.search_emails("is:unread", limit=500))
    assert page["messages"][0]["subject"] == "A subject"
    assert page["next_page_token"] == "next"
    listed = gmail._gmail_service.api.users_api.messages_api.calls[0]
    assert listed[1]["maxResults"] == 100
    assert listed[1]["includeSpamTrash"] is False

    message = decode(gmail.get_email("m1"))
    assert message["body"] == "Hello from the body"
    assert decode(gmail.send_email("person@example.com", "Hi", "Body"))["ok"]
    assert (
        decode(gmail.create_email_draft("person@example.com", "Hi", "Body"))["draft_id"]
        == "draft"
    )
    assert decode(gmail.mark_email_read("m1"))["ok"]
    assert decode(gmail.mark_email_unread("m1"))["ok"]
    assert decode(gmail.archive_email("m1"))["ok"]

    modify_bodies = [
        call[1]["body"]
        for call in gmail._gmail_service.api.users_api.messages_api.calls
        if call[0] == "modify"
    ]
    assert modify_bodies == [
        {"addLabelIds": [], "removeLabelIds": ["UNREAD"]},
        {"addLabelIds": ["UNREAD"], "removeLabelIds": []},
        {"addLabelIds": [], "removeLabelIds": ["INBOX"]},
    ]


def test_gmail_rejects_invalid_recipients_and_large_body(gmail):
    assert gmail.send_email("not-an-address", "Hi", "Body").startswith("Error:")
    assert gmail.send_email("a@example.com", "Hi", "x" * 100_001).startswith("Error:")
