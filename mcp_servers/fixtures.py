"""Recorded responses, so a demo does not depend on the venue's wifi.

Most furniture reaches outside: the bookshelf and reading chair call Open
Library, Gutendex and Project Gutenberg, and the window calls Open-Meteo. On a
hotel network that is four ways for a scripted demo to stall on a spinner.

Three modes, chosen with `EMBODIED_AGENT_FIXTURES`:

* `off` (default) — always call the real service.
* `record` — call it, and save the response under `fixtures/`.
* `replay` — never touch the network; serve the saved response, and fail with a
  clear message when one is missing.

Recording is a deliberate, online step (`scripts/record_fixtures.py`), so the
saved answers are real service responses rather than invented ones, and the
demo shows the same thing the live system would.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from loguru import logger

MODE_OFF = "off"
MODE_RECORD = "record"
MODE_REPLAY = "replay"
_MODES = (MODE_OFF, MODE_RECORD, MODE_REPLAY)

_DEFAULT_DIR = Path(__file__).resolve().parent.parent / "fixtures"
# Keys become file names, so they are restricted to a safe, readable subset.
_SAFE_KEY_RE = re.compile(r"[^a-z0-9._-]+")
_MAX_KEY_CHARS = 80


class FixtureMissingError(ValueError):
    """Raised in replay mode when nothing was recorded for a request.

    Deliberately a ValueError: every tool here already turns one into an
    "Error: ..." string for the model, so an un-recorded request during a demo
    reads as a normal tool failure instead of an unhandled exception.
    """


def mode() -> str:
    """Current fixture mode, defaulting to off for normal operation."""
    configured = (os.environ.get("EMBODIED_AGENT_FIXTURES") or MODE_OFF).strip().lower()
    if configured not in _MODES:
        logger.warning(
            f"Fixtures: unknown mode {configured!r}; expected one of "
            f"{', '.join(_MODES)}. Treating it as {MODE_OFF}."
        )
        return MODE_OFF
    return configured


def fixtures_dir() -> Path:
    configured = os.environ.get("EMBODIED_AGENT_FIXTURES_DIR")
    return Path(configured).expanduser() if configured else _DEFAULT_DIR


def fixture_key(*parts: Any) -> str:
    """Build a stable, readable file stem from the parts of a request.

    A long or awkward value keeps a readable prefix and gains a short digest,
    so two different queries never collide and a human can still tell which
    recording belongs to which request.
    """
    raw = "-".join(str(part).strip().lower() for part in parts if str(part).strip())
    slug = _SAFE_KEY_RE.sub("-", raw)
    # Single dots are kept because they keep keys readable ("84.txt.utf-8"), but
    # a run of them is the one shape that could read as a parent directory.
    slug = re.sub(r"\.{2,}", "-", slug).strip("-.") or "request"
    if len(slug) <= _MAX_KEY_CHARS:
        return slug
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{slug[:_MAX_KEY_CHARS].rstrip('-')}-{digest}"


def fixture_path(server: str, key: str) -> Path:
    return fixtures_dir() / server / f"{key}.json"


def cached_json(server: str, key: str, fetch: Callable[[], Any]) -> Any:
    """Return `fetch()`, recorded or replayed according to the current mode."""
    current = mode()
    if current == MODE_OFF:
        return fetch()

    path = fixture_path(server, key)
    if current == MODE_REPLAY:
        if not path.exists():
            raise FixtureMissingError(
                f"no recorded response for {server}/{key}. Record it with "
                "EMBODIED_AGENT_FIXTURES=record while online, or unset "
                "EMBODIED_AGENT_FIXTURES to use the live service."
            )
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise FixtureMissingError(
                f"recorded response {path} is unreadable: {exc}"
            ) from exc

    payload = fetch()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(f"Fixtures: recorded {server}/{key}")
    except (OSError, TypeError) as exc:
        logger.warning(f"Fixtures: could not record {server}/{key}: {exc}")
    return payload


def cached_text(server: str, key: str, fetch: Callable[[], str]) -> str:
    """Same contract as `cached_json`, for plain-text bodies such as book text."""
    return cached_json(server, key, lambda: {"text": fetch()})["text"]
