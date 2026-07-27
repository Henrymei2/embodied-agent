from __future__ import annotations

from pathlib import Path

import pytest

from src.open_llm_vtuber.chat_history_manager import (
    _get_safe_history_path,
    _is_safe_filename,
    create_new_history,
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
