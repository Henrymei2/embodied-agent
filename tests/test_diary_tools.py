from __future__ import annotations

import importlib
import json
import sys

import pytest


@pytest.fixture
def diary(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(tmp_path / "workspace"))
    for name in ("src.open_llm_vtuber.furniture_state", "mcp_servers.diary_tools"):
        sys.modules.pop(name, None)
    module = importlib.import_module("mcp_servers.diary_tools")
    memory_dir = tmp_path / "workspace" / "memory"
    memory_dir.mkdir(parents=True)
    return module, memory_dir


def _decode(result: str) -> dict:
    assert not result.startswith("Error:"), result
    return json.loads(result)


def test_recent_memories_lists_newest_first(diary):
    diary_tools, memory_dir = diary
    (memory_dir / "2026-07-20.md").write_text(
        "# Daily Memory - 2026-07-20\n\nOlder note",
        encoding="utf-8",
    )
    (memory_dir / "2026-07-22.md").write_text(
        "# Daily Memory - 2026-07-22\n\nNewest note",
        encoding="utf-8",
    )

    payload = _decode(diary_tools.get_recent_memories(limit=5))

    assert payload["available"] is True
    assert payload["count"] == 2
    assert [entry["date"] for entry in payload["entries"]] == [
        "2026-07-22",
        "2026-07-20",
    ]
    assert payload["entries"][0]["preview"] == "Newest note"


def test_recent_memories_respects_limit(diary):
    diary_tools, memory_dir = diary
    for day in range(1, 5):
        (memory_dir / f"2026-07-0{day}.md").write_text(
            f"# Daily Memory - 2026-07-0{day}\n\nNote {day}",
            encoding="utf-8",
        )

    payload = _decode(diary_tools.get_recent_memories(limit=2))

    assert len(payload["entries"]) == 2
    assert payload["remaining"] == 2


def test_read_memory_day_returns_content(diary):
    diary_tools, memory_dir = diary
    (memory_dir / "2026-07-22.md").write_text(
        "# Daily Memory - 2026-07-22\n\nWorked on Diary furniture.",
        encoding="utf-8",
    )

    payload = _decode(diary_tools.read_memory_day("2026-07-22"))

    assert payload == {
        "date": "2026-07-22",
        "content": "Worked on Diary furniture.",
    }


def test_diary_rejects_bad_inputs(diary):
    diary_tools, _memory_dir = diary

    assert diary_tools.get_recent_memories(limit="many").startswith("Error:")
    assert diary_tools.read_memory_day("07-22-2026") == (
        "Error: entry_date must use YYYY-MM-DD format"
    )
    assert diary_tools.read_memory_day("2026-13-99") == (
        "Error: entry_date must be a real calendar date"
    )
    assert diary_tools.read_memory_day("2026-07-22") == (
        "Error: diary entry '2026-07-22' was not found"
    )
