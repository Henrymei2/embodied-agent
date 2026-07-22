from __future__ import annotations

import importlib
import json
import sys

import pytest


@pytest.fixture
def todo(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    for name in ("src.open_llm_vtuber.furniture_state", "mcp_servers.todo_tools"):
        sys.modules.pop(name, None)
    return importlib.import_module("mcp_servers.todo_tools")


def _decode(result: str) -> dict:
    assert not result.startswith("Error:"), result
    return json.loads(result)


def test_todo_crud_roundtrip(todo):
    created = _decode(todo.add_todo("Water the plants", priority="high"))["item"]

    listed = _decode(todo.list_todos())
    assert listed["open_count"] == 1
    assert [item["todo_id"] for item in listed["items"]] == [created["todo_id"]]

    updated = _decode(
        todo.update_todo(created["todo_id"], text="Water the ferns", priority="low")
    )["item"]
    assert updated["text"] == "Water the ferns"
    assert updated["priority"] == "low"
    assert updated["created_at"] == created["created_at"]

    completed = _decode(todo.complete_todo(created["todo_id"], notes="done early"))
    assert completed["item"]["status"] == "done"
    assert completed["item"]["notes"] == "done early"

    assert _decode(todo.list_todos())["items"] == []
    assert len(_decode(todo.list_todos(include_done=True))["items"]) == 1

    assert _decode(todo.remove_todo(created["todo_id"]))["ok"] is True
    assert _decode(todo.list_todos(include_done=True))["items"] == []


def test_todo_orders_open_items_by_priority_then_due(todo):
    todo.add_todo("normal no due")
    todo.add_todo("low", priority="low")
    todo.add_todo("high later", priority="high", due="2026-08-02T09:00:00Z")
    todo.add_todo("high sooner", priority="high", due="2026-08-01T09:00:00Z")

    texts = [item["text"] for item in _decode(todo.list_todos())["items"]]

    assert texts == ["high sooner", "high later", "normal no due", "low"]


def test_todo_completed_items_sort_after_open_ones(todo):
    first = _decode(todo.add_todo("finish me", priority="high"))["item"]
    todo.add_todo("still open", priority="low")
    todo.complete_todo(first["todo_id"])

    texts = [
        item["text"] for item in _decode(todo.list_todos(include_done=True))["items"]
    ]

    assert texts == ["still open", "finish me"]


def test_todo_rejects_bad_input(todo):
    assert todo.add_todo("   ") == "Error: text cannot be empty"
    assert todo.add_todo("x", priority="urgent").startswith(
        "Error: priority must be one of"
    )
    assert todo.add_todo("x", due="2026-08-01T09:00:00") == (
        "Error: due must include a UTC offset"
    )
    assert todo.complete_todo("nope") == "Error: todo 'nope' was not found"


def test_todo_cannot_be_completed_twice(todo):
    item = _decode(todo.add_todo("only once"))["item"]
    todo.complete_todo(item["todo_id"])

    assert todo.complete_todo(item["todo_id"]) == (
        f"Error: todo '{item['todo_id']}' is already done"
    )


def test_todo_corruption_fails_safely(todo):
    from src.open_llm_vtuber.furniture_state import todos_path

    path = todos_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not-json", encoding="utf-8")

    assert todo.list_todos().startswith("Error: todo store is unreadable")
    assert todo.add_todo("blocked").startswith("Error: todo store is unreadable")
