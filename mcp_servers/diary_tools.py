"""Diary tools for the embodied companion.

These tools belong to the `diary` object in the room: the agent has to walk
to the diary before it can review recent daily memories.

Daily memories live in `<workspace>/memory/YYYY-MM-DD.md`; no account or
network access is involved, and the same files back the hover window shown
when the user points at the Diary furniture.

Run standalone for a quick sanity check:
    uv run python mcp_servers/diary_tools.py
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.furniture_state import diary_snapshot, read_diary_entry

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

mcp = FastMCP("diary")


def _validate_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("limit must be an integer")
    return max(1, min(limit, 30))


def _validate_date(value: str) -> str:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value.strip()):
        raise ValueError("entry_date must use YYYY-MM-DD format")
    try:
        date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError("entry_date must be a real calendar date") from exc
    return value.strip()


@mcp.tool()
def get_recent_memories(limit: int = 5) -> str:
    """Review recent daily memories. Requires standing near the diary.

    Args:
        limit: Maximum number of daily memory files to return, 1 to 30.
    """
    try:
        return json.dumps(diary_snapshot(limit=_validate_limit(limit)), ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def read_memory_day(entry_date: str) -> str:
    """Read one daily memory entry. Requires standing near the diary.

    Args:
        entry_date: Date to read in YYYY-MM-DD format.
    """
    try:
        entry = read_diary_entry(_validate_date(entry_date))
        return json.dumps(entry, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
