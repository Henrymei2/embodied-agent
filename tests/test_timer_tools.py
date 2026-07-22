from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def timer(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    for name in ("src.open_llm_vtuber.furniture_state", "mcp_servers.timer_tools"):
        sys.modules.pop(name, None)
    return importlib.import_module("mcp_servers.timer_tools")


def _decode(result: str) -> dict:
    assert not result.startswith("Error:"), result
    return json.loads(result)


def test_timer_set_list_cancel_roundtrip(timer):
    created = _decode(timer.set_timer(10, label="tea"))["timer"]
    assert created["label"] == "tea"
    assert 9 * 60 <= created["remaining_seconds"] <= 10 * 60

    listed = _decode(timer.list_timers())["timers"]
    assert [item["timer_id"] for item in listed] == [created["timer_id"]]

    assert _decode(timer.cancel_timer(created["timer_id"]))["ok"] is True
    assert _decode(timer.list_timers())["timers"] == []


def test_timers_are_listed_soonest_first(timer):
    timer.set_timer(30, label="later")
    timer.set_timer(2, label="sooner")

    labels = [item["label"] for item in _decode(timer.list_timers())["timers"]]

    assert labels == ["sooner", "later"]


def test_timer_is_a_one_shot_scheduler_task(timer):
    """The scheduler loop fires and then deletes it, so it must not repeat."""
    from src.open_llm_vtuber.furniture_state import load_tasks

    created = _decode(timer.set_timer(5, label="stretch"))["timer"]
    task = load_tasks()[created["timer_id"]]

    assert task["interval_seconds"] is None
    assert task["report"] is True
    assert task["priority"] == 10
    assert "stretch" in task["prompt"]
    due = datetime.fromisoformat(task["next_run"])
    assert timedelta(minutes=4) < due - datetime.now() <= timedelta(minutes=5)


def test_timer_rejects_bad_durations(timer):
    assert timer.set_timer(0).startswith("Error: minutes must be between")
    assert timer.set_timer(10_000).startswith("Error: minutes must be between")
    assert timer.set_timer("ten") == "Error: minutes must be a number"


def test_cancel_timer_ignores_non_timer_tasks(timer):
    """A scheduled task is not a timer and must not be cancellable as one."""
    from src.open_llm_vtuber.furniture_state import load_tasks, save_tasks

    save_tasks(
        {
            "task1": {
                "id": "task1",
                "prompt": "daily review",
                "priority": 10,
                "interval_seconds": 3600,
                "report": True,
                "next_run": datetime.now().isoformat(timespec="seconds"),
            }
        }
    )

    assert timer.cancel_timer("task1") == "Error: timer 'task1' was not found"
    assert _decode(timer.list_timers())["timers"] == []
    assert "task1" in load_tasks()


def test_timer_limit_is_enforced(timer):
    for index in range(timer._MAX_TIMERS):
        assert not timer.set_timer(5, label=f"t{index}").startswith("Error:")

    assert timer.set_timer(5).startswith("Error: at most")
