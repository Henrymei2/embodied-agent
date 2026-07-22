from __future__ import annotations

import importlib
import json
import sys

import pytest


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class Files:
    def __init__(self):
        self.calls = []

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return Request(
            {
                "files": [{"id": "f1", "name": "notes.txt", "mimeType": "text/plain"}],
                "nextPageToken": "next",
            }
        )

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        if kwargs.get("fields") == "parents":
            return Request({"parents": ["old"]})
        mime = (
            "application/octet-stream" if kwargs["fileId"] == "binary" else "text/plain"
        )
        return Request(
            {"id": kwargs["fileId"], "name": "notes", "mimeType": mime, "size": "5"}
        )

    def get_media(self, **kwargs):
        self.calls.append(("get_media", kwargs))
        return Request(b"hello")

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return Request({"id": "new", **kwargs["body"]})

    def update(self, **kwargs):
        self.calls.append(("update", kwargs))
        return Request(
            {
                "id": kwargs["fileId"],
                "name": "updated",
                "mimeType": "text/plain",
                "parents": [kwargs.get("addParents", "folder")],
            }
        )


class Drive:
    def __init__(self):
        self.files_api = Files()

    def files(self):
        return self.files_api


class Service:
    def __init__(self):
        self.api = Drive()

    def drive(self):
        return self.api


@pytest.fixture
def drive():
    sys.modules.pop("mcp_servers.drive_tools", None)
    module = importlib.import_module("mcp_servers.drive_tools")
    module = importlib.reload(module)
    module._drive_service = Service()
    return module


def decode(value):
    assert not value.startswith("Error:")
    return json.loads(value)


def test_drive_listing_read_and_crud(drive):
    page = decode(drive.list_drive_files("name contains 'note'", "folder", limit=500))
    assert page["files"][0]["file_id"] == "f1"
    assert page["next_page_token"] == "next"
    args = drive._drive_service.api.files_api.calls[0][1]
    assert args["pageSize"] == 100
    assert "trashed = false" in args["q"]
    assert "'folder' in parents" in args["q"]

    assert decode(drive.get_drive_file("f1"))["content"] == "hello"
    assert (
        "Binary content"
        in decode(drive.get_drive_file("binary"))["content_unavailable"]
    )
    assert decode(drive.create_drive_text_file("notes.txt", "hello"))["ok"]
    assert decode(drive.update_drive_text_file("f1", content="updated"))["ok"]
    moved = decode(drive.move_drive_file("f1", "new-folder"))
    assert moved["file"]["parents"] == ["new-folder"]


def test_drive_validates_mutations(drive):
    assert drive.update_drive_text_file("f1").startswith("Error:")
    assert drive.create_drive_text_file("notes", "x" * 1_000_001).startswith("Error:")
