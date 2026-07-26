"""Library tools for the embodied companion.

Two objects share this server, and each tool is gated on the one it belongs to:

* the `bookshelf`, where the agent searches for books and shelves them
  (`list_books`, `shelve_book`, `list_shelf`, `remove_from_shelf`)
* the `reading chair`, where a shelved book is actually read a passage at a time
  (`read_book`, `save_reading_note`, `reading_progress`, `finish_book`)

Browsing uses Open Library and readable copies come from Project Gutenberg via
Gutendex; both are public APIs that need no account or key. Only those fixed
hosts are contacted, and a search string is always sent as an encoded query
parameter, never as part of a host or path. A book's text is downloaded once,
under a size cap, into `<workspace>/library/texts/` so continuing a book is a
local read rather than another download.

Run standalone for a quick sanity check:
    uv run python mcp_servers/library_tools.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from mcp_servers.bridge_client import post_frontend_action
from mcp_servers.fixtures import cached_json, cached_text, fixture_key
from src.open_llm_vtuber.furniture_state import (
    active_session,
    book_text_path,
    load_library,
    save_library,
)

_OPEN_LIBRARY_URL = "https://openlibrary.org/search.json"
_GUTENDEX_URL = "https://gutendex.com/books"
# Only these hosts may be contacted, including for a book's text download.
_ALLOWED_TEXT_HOSTS = frozenset({"gutenberg.org", "www.gutenberg.org"})

# Gutendex answers 403 to the default `Python-urllib/x.y` agent, which silently
# cost every book its readable copy. Identify the project instead.
_USER_AGENT = "embodied-agent/1.0 (+https://github.com/Henrymei2/embodied-agent)"
_TIMEOUT_SECONDS = 20.0
_MAX_JSON_BYTES = 512 * 1024
_MAX_TEXT_BYTES = 2 * 1024 * 1024
_MAX_QUERY = 200
_MAX_TITLE = 200
_MAX_AUTHOR = 160
_MAX_SUMMARY = 4_000
_MAX_RESULTS = 10
_MAX_SHELF = 100
_MAX_NOTES = 200
# A "section" is one sitting's worth of reading, cut at a paragraph break.
_SECTION_CHARS = 4_000
_MAX_SECTIONS_PER_CALL = 3
_GUTENBERG_START = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG[^\n]*\n"
)
_GUTENBERG_END = re.compile(r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG")

mcp = FastMCP("library")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return cleaned


def _fetch_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Fetch from the book services, or from a recording when demoing offline."""
    endpoint = "openlibrary" if url == _OPEN_LIBRARY_URL else "gutendex"
    key = fixture_key(
        endpoint, *(f"{name}={value}" for name, value in sorted(params.items()))
    )
    return cached_json("library", key, lambda: _request_json(url, params))


def _request_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{url}?{urllib.parse.urlencode(params)}",
        headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            # Read one byte past the cap so an oversized body is rejected rather
            # than silently truncated into unparseable JSON.
            raw = response.read(_MAX_JSON_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"book service returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"could not reach the book service: {exc}") from exc

    if len(raw) > _MAX_JSON_BYTES:
        raise ValueError("book service response was too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("book service returned malformed data") from exc
    if not isinstance(payload, dict):
        raise ValueError("book service returned an unexpected payload")
    return payload


def _normalize_title(title: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(title or "").lower()).strip()


def _title_keys(title: Any) -> list[str]:
    """Normalized forms a title may be matched on, most specific first.

    The two services disagree about subtitles — Open Library lists
    "Frankenstein" where Gutendex has "Frankenstein; Or, The Modern
    Prometheus" — so the part before a subtitle separator is matched as well.
    Dropping the subtitle is safe in a way that loose containment is not:
    "Frankenstein in Baghdad" still reduces to itself and cannot collide.
    """
    full = _normalize_title(title)
    keys = [full] if full else []
    head = _normalize_title(re.split(r"[;:]", str(title or ""), maxsplit=1)[0])
    if head and head != full:
        keys.append(head)
    return keys


def _gutendex_matches(query: str) -> dict[str, dict[str, Any]]:
    """Map normalized title -> readable Gutenberg copy for one search."""
    try:
        payload = _fetch_json(_GUTENDEX_URL, {"search": query})
    except ValueError as exc:
        # Browsing must still work when only the full-text service is down, but
        # a silent {} here reads as "nothing is readable", which is the same
        # answer an outage gives — so say which it was.
        print(f"library: full-text lookup unavailable ({exc})", file=sys.stderr)
        return {}

    matches: dict[str, dict[str, Any]] = {}
    results = payload.get("results")
    if not isinstance(results, list):
        return matches
    for book in results[:32]:
        if not isinstance(book, dict) or not isinstance(book.get("id"), int):
            continue
        text_url = _plain_text_url(book.get("formats"))
        if not text_url:
            continue
        entry = {"text_source_id": book["id"], "text_url": text_url}
        for key in _title_keys(book.get("title")):
            matches.setdefault(key, entry)
    return matches


def _plain_text_url(formats: Any) -> str | None:
    """Pick a plain-text download on an allowed host, preferring UTF-8."""
    if not isinstance(formats, dict):
        return None
    candidates = [
        value
        for key, value in formats.items()
        if isinstance(key, str)
        and isinstance(value, str)
        and key.startswith("text/plain")
        and not value.endswith(".zip")
    ]
    candidates.sort(key=lambda url: 0 if "utf-8" in url.lower() else 1)
    for url in candidates:
        if _is_allowed_text_url(url):
            return url
    return None


def _is_allowed_text_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in _ALLOWED_TEXT_HOSTS


def _download_text(url: str) -> str:
    if not _is_allowed_text_url(url):
        raise ValueError("book text is not hosted on an allowed service")
    # A book is downloaded once per reader anyway; recording it lets a demo
    # open one with the network unplugged.
    return cached_text("library", fixture_key("text", url), lambda: _request_text(url))


def _request_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/plain", "User-Agent": _USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_TEXT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"book text returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"could not download the book text: {exc}") from exc
    if len(raw) > _MAX_TEXT_BYTES:
        raise ValueError("book text is too large to read here")

    text = raw.decode("utf-8", errors="replace")
    start = _GUTENBERG_START.search(text)
    if start:
        text = text[start.end() :]
    end = _GUTENBERG_END.search(text)
    if end:
        text = text[: end.start()]
    return text.strip()


def _resolve_text_url(source_id: int) -> str:
    """Look up the plain-text download for one Gutenberg book ID."""
    payload = _fetch_json(_GUTENDEX_URL, {"ids": str(source_id)})
    results = payload.get("results")
    entry = results[0] if isinstance(results, list) and results else None
    text_url = (
        _plain_text_url(entry.get("formats")) if isinstance(entry, dict) else None
    )
    if not text_url:
        raise ValueError("no plain-text copy of this book is available")
    return text_url


def _book_text(source_id: int) -> str:
    """Return the cached book text, downloading it the first time."""
    path = book_text_path(str(source_id))
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"cached book text is unreadable: {exc}") from exc

    text = _download_text(_resolve_text_url(source_id))
    if not text:
        raise ValueError("the downloaded book text was empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return text


def _next_section(text: str, offset: int) -> tuple[str, int]:
    """Slice one section from `offset`, ending at a paragraph break when possible."""
    remaining = text[offset:]
    if len(remaining) <= _SECTION_CHARS:
        return remaining.strip(), len(text)
    window = remaining[:_SECTION_CHARS]
    cut = window.rfind("\n\n")
    if cut < _SECTION_CHARS // 2:
        cut = window.rfind("\n")
    if cut < _SECTION_CHARS // 2:
        cut = window.rfind(" ")
    if cut <= 0:
        cut = _SECTION_CHARS
    return remaining[:cut].strip(), offset + cut


def _find_book(shelf: list[dict[str, Any]], book_id: str) -> dict[str, Any]:
    if not isinstance(book_id, str) or not book_id or len(book_id) > 80:
        raise ValueError("book_id must be a non-empty string")
    book = next((item for item in shelf if item.get("book_id") == book_id), None)
    if book is None:
        raise ValueError(f"book '{book_id}' is not on the shelf")
    return book


# ── Bookshelf ─────────────────────────────────────────────────────────────────


@mcp.tool()
async def list_books(query: str, limit: int = 5) -> str:
    """Search online books by title, author, or subject. Requires the bookshelf.

    Results come from Open Library and are annotated with whether a free full
    text exists that `read_book` can actually read.

    Args:
        query: What to search for, for example "ursula le guin" or "beekeeping".
        limit: How many results to return (1-10).
    """
    return await asyncio.to_thread(_list_books, query, limit)


def _list_books(query: str, limit: int) -> str:
    try:
        cleaned = _validate_text(query, "query", _MAX_QUERY)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, _MAX_RESULTS))

        payload = _fetch_json(
            _OPEN_LIBRARY_URL,
            {
                "q": cleaned,
                "limit": limit,
                "fields": "title,author_name,first_publish_year",
            },
        )
        readable = _gutendex_matches(cleaned)

        books = []
        for document in payload.get("docs", [])[:limit]:
            if not isinstance(document, dict):
                continue
            authors = document.get("author_name")
            match = next(
                (
                    readable[key]
                    for key in _title_keys(document.get("title"))
                    if key in readable
                ),
                None,
            )
            books.append(
                {
                    "title": str(document.get("title", ""))[:_MAX_TITLE],
                    "author": str(authors[0])[:_MAX_AUTHOR]
                    if isinstance(authors, list) and authors
                    else "",
                    "year": document.get("first_publish_year"),
                    "readable": bool(match),
                    "text_source_id": match["text_source_id"] if match else None,
                }
            )
        return json.dumps(
            {
                "query": cleaned,
                "count": len(books),
                "books": books,
                "hint": (
                    "Pass a result's title, author, year and text_source_id to "
                    "shelve_book. Only readable books can be opened at the "
                    "reading chair."
                ),
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def shelve_book(
    title: str,
    author: str = "",
    year: int | None = None,
    text_source_id: int | None = None,
    note: str = "",
) -> str:
    """Put a book on the shelf so it can be read later. Requires the bookshelf.

    Args:
        title: The book's title.
        author: The author, when known.
        year: First publication year, when known.
        text_source_id: The `text_source_id` from `list_books`; without it the
            book is only a reminder, because there is no readable text.
        note: Optional short reason for shelving it.
    """
    try:
        library = load_library()
        shelf = library["shelf"]
        if len(shelf) >= _MAX_SHELF:
            raise ValueError(f"the shelf holds at most {_MAX_SHELF} books")

        cleaned_title = _validate_text(title, "title", _MAX_TITLE)
        cleaned_author = (
            _validate_text(author, "author", _MAX_AUTHOR) if str(author).strip() else ""
        )
        if year is not None and (isinstance(year, bool) or not isinstance(year, int)):
            raise ValueError("year must be an integer")
        if text_source_id is not None and (
            isinstance(text_source_id, bool)
            or not isinstance(text_source_id, int)
            or text_source_id <= 0
        ):
            raise ValueError("text_source_id must be a positive integer")
        if any(
            item.get("title", "").lower() == cleaned_title.lower() for item in shelf
        ):
            raise ValueError(f"'{cleaned_title}' is already on the shelf")

        book = {
            "book_id": uuid.uuid4().hex[:8],
            "title": cleaned_title,
            "author": cleaned_author,
            "year": year,
            "text_source_id": text_source_id,
            "note": " ".join(str(note).split())[:500],
            "added_at": _now(),
        }
        shelf.append(book)
        save_library(library)
        return json.dumps({"ok": True, "book": book}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def list_shelf() -> str:
    """List the books currently on the shelf. Requires the bookshelf."""
    try:
        library = load_library()
        shelf = sorted(
            library["shelf"],
            key=lambda book: str(book.get("added_at", "")),
            reverse=True,
        )
        session = active_session(library)
        return json.dumps(
            {
                "count": len(shelf),
                "books": shelf,
                "currently_reading": session.get("title") if session else None,
            },
            ensure_ascii=False,
        )
    except (OSError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def remove_from_shelf(book_id: str) -> str:
    """Take a book off the shelf. Requires the bookshelf.

    An unfinished reading session for that book is closed as well.
    """
    try:
        library = load_library()
        book = _find_book(library["shelf"], book_id)
        library["shelf"] = [
            item for item in library["shelf"] if item.get("book_id") != book_id
        ]
        for session in library["sessions"]:
            if session.get("book_id") == book_id and session.get("status") == "reading":
                session["status"] = "abandoned"
                session["updated_at"] = _now()
        save_library(library)
        return json.dumps(
            {"ok": True, "removed": book.get("title")}, ensure_ascii=False
        )
    except (OSError, ValueError) as exc:
        return f"Error: {exc}"


# ── Reading chair ─────────────────────────────────────────────────────────────


def _session_for(library: dict[str, Any], book: dict[str, Any]) -> dict[str, Any]:
    """Return the open session for `book`, creating one if needed."""
    existing = next(
        (
            session
            for session in library["sessions"]
            if session.get("book_id") == book["book_id"]
            and session.get("status") == "reading"
        ),
        None,
    )
    if existing is not None:
        return existing

    session = {
        "book_id": book["book_id"],
        "title": book.get("title", ""),
        "author": book.get("author", ""),
        "text_source_id": book.get("text_source_id"),
        "offset": 0,
        "section": 0,
        "progress_percent": 0,
        "status": "reading",
        "notes": [],
        "started_at": _now(),
        "updated_at": _now(),
    }
    library["sessions"].append(session)
    return session


@mcp.tool()
async def read_book(book_id: str = "", sections: int = 1) -> str:
    """Read the next passage of a book. Requires standing at the reading chair.

    Reading resumes exactly where the last sitting stopped. Follow a passage
    with `save_reading_note` so the summary is kept, and use the scheduler to
    come back for the next one after an interval.

    Args:
        book_id: Which shelved book to read. Omit to continue the open book.
        sections: How many passages to read in one sitting (1-3).
    """
    # Settle into the chair first; a room without a live frontend still reads.
    await post_frontend_action(
        "stage_patch_state",
        {
            "patch": {
                "nodes": [{"id": "avatar", "pose": "sitting"}],
                "metadata": {"updated_by": "read_book"},
            }
        },
    )
    return await asyncio.to_thread(_read_book, book_id, sections)


def _read_book(book_id: str, sections: int) -> str:
    try:
        if isinstance(sections, bool) or not isinstance(sections, int):
            raise ValueError("sections must be an integer")
        sections = max(1, min(sections, _MAX_SECTIONS_PER_CALL))

        library = load_library()
        if book_id:
            book = _find_book(library["shelf"], book_id)
        else:
            session = active_session(library)
            if session is None:
                raise ValueError(
                    "no book is open; pass a book_id from list_shelf to start one"
                )
            book = _find_book(library["shelf"], str(session.get("book_id", "")))

        source_id = book.get("text_source_id")
        if not isinstance(source_id, int):
            raise ValueError(
                f"'{book.get('title')}' has no readable text; shelve a copy with a "
                "text_source_id from list_books"
            )

        session = _session_for(library, book)
        text = _book_text(source_id)

        offset = int(session.get("offset", 0) or 0)
        if offset >= len(text):
            session["status"] = "finished"
            session["progress_percent"] = 100
            session["updated_at"] = _now()
            save_library(library)
            return json.dumps(
                {"ok": True, "finished": True, "title": session["title"]},
                ensure_ascii=False,
            )

        passages = []
        for _ in range(sections):
            if offset >= len(text):
                break
            passage, offset = _next_section(text, offset)
            session["section"] = int(session.get("section", 0)) + 1
            passages.append({"section": session["section"], "text": passage})

        session["offset"] = offset
        session["progress_percent"] = min(100, round(offset * 100 / max(1, len(text))))
        session["updated_at"] = _now()
        if offset >= len(text):
            session["status"] = "finished"
        save_library(library)

        return json.dumps(
            {
                "ok": True,
                "title": session["title"],
                "author": session.get("author", ""),
                "progress_percent": session["progress_percent"],
                "finished": session["status"] == "finished",
                "passages": passages,
                "hint": "Call save_reading_note with a short summary of what you read.",
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def save_reading_note(summary: str) -> str:
    """Record a summary of the passage just read. Requires the reading chair.

    Args:
        summary: A short summary, in your own words, of the latest passage.
    """
    try:
        cleaned = _validate_text(summary, "summary", _MAX_SUMMARY)
        library = load_library()
        session = active_session(library)
        if session is None:
            # A book finished on the last passage still deserves its final note.
            finished = [
                item for item in library["sessions"] if item.get("status") == "finished"
            ]
            if not finished:
                raise ValueError("no book has been read yet")
            session = max(finished, key=lambda item: str(item.get("updated_at", "")))

        notes = session.setdefault("notes", [])
        notes.append(
            {
                "section": session.get("section", len(notes) + 1),
                "summary": cleaned,
                "created_at": _now(),
            }
        )
        del notes[:-_MAX_NOTES]
        session["updated_at"] = _now()
        save_library(library)
        return json.dumps(
            {
                "ok": True,
                "title": session.get("title"),
                "section": notes[-1]["section"],
                "note_count": len(notes),
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def reading_progress() -> str:
    """Show the open book, how far it has got, and its notes. Reading chair only."""
    try:
        library = load_library()
        session = active_session(library)
        if session is None:
            return json.dumps(
                {
                    "reading": None,
                    "finished": [
                        {
                            "title": item.get("title"),
                            "notes": len(item.get("notes", [])),
                        }
                        for item in library["sessions"]
                        if item.get("status") == "finished"
                    ],
                },
                ensure_ascii=False,
            )
        return json.dumps({"reading": session}, ensure_ascii=False)
    except (OSError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def finish_book() -> str:
    """Close the open book, keeping its notes. Requires the reading chair."""
    try:
        library = load_library()
        session = active_session(library)
        if session is None:
            raise ValueError("no book is currently open")
        session["status"] = "finished"
        session["updated_at"] = _now()
        save_library(library)
        return json.dumps(
            {
                "ok": True,
                "title": session.get("title"),
                "sections_read": session.get("section", 0),
                "notes": len(session.get("notes", [])),
            },
            ensure_ascii=False,
        )
    except (OSError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
