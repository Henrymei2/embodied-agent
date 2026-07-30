from __future__ import annotations

import importlib
import sys


def test_scheduler_remove_task_uses_shared_store(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    for name in ("src.open_llm_vtuber.furniture_state", "mcp_servers.scheduler"):
        sys.modules.pop(name, None)
    scheduler = importlib.import_module("mcp_servers.scheduler")

    result = scheduler.schedule_task("Check the calendar", delay_seconds=60)
    task_id = result.split("task ", 1)[1].split(" ", 1)[0]

    assert scheduler.remove_task(task_id) == f"OK: removed task {task_id}."
    assert scheduler.remove_task(task_id).startswith("Error: no task found")


def test_scheduler_rejects_invalid_priority_and_intervals(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    for name in ("src.open_llm_vtuber.furniture_state", "mcp_servers.scheduler"):
        sys.modules.pop(name, None)
    scheduler = importlib.import_module("mcp_servers.scheduler")

    assert scheduler.schedule_task("Unsafe", priority=0).startswith("Error: priority")
    assert scheduler.schedule_task("Unsafe", priority=-1).startswith("Error: priority")
    assert scheduler.schedule_task("Unsafe", interval_seconds=0).startswith(
        "Error: interval_seconds"
    )
