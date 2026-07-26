"""Record the network responses a demo needs, while you still have a network.

Run this once, online, before travelling to the venue:

    uv run python scripts/record_fixtures.py

It walks the requests a scripted demo makes — a weather lookup at the window, a
book search at the bookshelf, and opening that book at the reading chair — and
saves each real response under `fixtures/`. On the day, set

    EMBODIED_AGENT_FIXTURES=replay

and those tools answer from disk, identically, with the network unplugged.

Pass extra topics or places to widen the recording:

    uv run python scripts/record_fixtures.py --place "Madison" --query "dracula"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Must be set before the tool modules read it.
os.environ["EMBODIED_AGENT_FIXTURES"] = "record"

from mcp_servers import library_tools, weather_tools  # noqa: E402
from mcp_servers.fixtures import fixtures_dir  # noqa: E402

DEFAULT_PLACES = ["Madison, Wisconsin"]
DEFAULT_QUERIES = ["frankenstein"]


def _summarize(label: str, payload: str) -> bool:
    """Print one line per recorded call; return False when it failed."""
    if payload.startswith("Error:"):
        print(f"  !! {label}: {payload}")
        return False
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        print(f"  ok {label}")
        return True
    if isinstance(parsed, dict) and "books" in parsed:
        readable = sum(1 for book in parsed["books"] if book.get("readable"))
        print(f"  ok {label}: {len(parsed['books'])} results, {readable} readable")
    else:
        print(f"  ok {label}")
    return True


async def record(places: list[str], queries: list[str]) -> int:
    failures = 0

    for place in places:
        print(f"weather · {place}")
        failures += not _summarize(
            "current", await weather_tools.get_current_weather(place)
        )
        failures += not _summarize(
            "forecast", await weather_tools.get_weather_forecast(place, days=3)
        )

    for query in queries:
        print(f"library · {query}")
        listed = await library_tools.list_books(query)
        if not _summarize("search", listed):
            failures += 1
            continue

        books = json.loads(listed).get("books", [])
        readable = next((book for book in books if book.get("text_source_id")), None)
        if readable is None:
            print("  -- no free full text for this query; nothing to read")
            continue

        # Shelve into a scratch library so the real workspace is untouched, then
        # read one passage, which is what pulls the book's text through.
        print(f"  .. downloading '{readable['title']}' for the reading chair")
        shelved = json.loads(
            library_tools.shelve_book(
                readable["title"],
                author=readable.get("author", ""),
                year=readable.get("year"),
                text_source_id=readable["text_source_id"],
            )
        )
        if not shelved.get("ok"):
            print(f"  !! could not shelve: {shelved}")
            failures += 1
            continue
        failures += not _summarize(
            "read", await library_tools.read_book(shelved["book"]["book_id"])
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--place", action="append", dest="places", default=None)
    parser.add_argument("--query", action="append", dest="queries", default=None)
    parser.add_argument(
        "--workspace",
        default=None,
        help="scratch workspace for the shelved demo book "
        "(default: a temporary directory)",
    )
    args = parser.parse_args()

    # Recording must not touch the real workspace's library or shelve a book
    # into the room the user actually sees.
    workspace = args.workspace or tempfile.mkdtemp(prefix="embodied-fixture-rec-")
    os.environ["EMBODIED_AGENT_WORKSPACE"] = workspace
    os.chdir(_PROJECT_ROOT)

    print(f"\nRecording into {fixtures_dir()}\n")
    failures = asyncio.run(
        record(args.places or DEFAULT_PLACES, args.queries or DEFAULT_QUERIES)
    )

    recorded = list(fixtures_dir().rglob("*.json"))
    print(f"\n{len(recorded)} fixture file(s) on disk.")
    if failures:
        print(
            f"{failures} call(s) failed and were not recorded — replay would fail "
            "on those.\n"
        )
        return 1
    print("Replay them with EMBODIED_AGENT_FIXTURES=replay.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
