from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.open_llm_vtuber.chat_history_manager import (
    _get_safe_history_path,
    _is_safe_filename,
    create_new_history,
    get_history,
    store_message,
)


@pytest.mark.parametrize(
    "component",
    [
        ".",
        "..",
        "../escape",
        r"..\escape",
        "folder/name",
        r"folder\name",
        "c:escape",
        "name*",
        "name?",
        " name",
        "name ",
        "null\x00byte",
    ],
)
def test_chat_history_rejects_unsafe_path_components(component):
    assert _is_safe_filename(component) is False


def test_chat_history_creation_cannot_escape_its_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError):
        create_new_history("..")

    assert not list(tmp_path.glob("*.json"))


def test_history_uid_cannot_escape_character_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError):
        _get_safe_history_path("character", "../outside")

    assert not (tmp_path / "outside.json").exists()


def test_valid_history_is_created_under_character_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    history_uid = create_new_history("character-1")
    path = Path(_get_safe_history_path("character-1", history_uid))

    assert path.is_file()
    assert path.parent == (tmp_path / "chat_history" / "character-1").resolve()


def test_stable_message_id_makes_history_delivery_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    history_uid = create_new_history("character-1")

    for _ in range(2):
        store_message(
            conf_uid="character-1",
            history_uid=history_uid,
            role="ai",
            content="A retry-safe autonomous result.",
            message_id="agenda-report:entry-1",
        )

    messages = get_history("character-1", history_uid)
    assert len(messages) == 1
    assert messages[0]["id"] == "agenda-report:entry-1"


def test_regular_history_messages_receive_frontend_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    history_uid = create_new_history("character-1")
    store_message(
        conf_uid="character-1",
        history_uid=history_uid,
        role="human",
        content="A normal message also needs a React key.",
    )

    messages = get_history("character-1", history_uid)
    assert len(messages) == 1
    assert isinstance(messages[0]["id"], str)
    assert messages[0]["id"]


def test_legacy_history_without_ids_gets_stable_frontend_ids(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    history_uid = create_new_history("character-1")
    path = Path(_get_safe_history_path("character-1", history_uid))
    path.write_text(
        json.dumps(
            [
                {"role": "metadata", "timestamp": "2026-01-01T00:00:00"},
                {
                    "role": "ai",
                    "timestamp": "2026-01-01T00:00:01",
                    "content": "Legacy message",
                },
            ]
        ),
        encoding="utf-8",
    )

    first = get_history("character-1", history_uid)
    second = get_history("character-1", history_uid)
    assert first[0]["id"] == second[0]["id"]
    assert first[0]["id"]
