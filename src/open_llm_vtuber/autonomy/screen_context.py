"""Coarse, privacy-minimal screen context for just-in-time help.

The agent decides *whether* to offer help in the autonomy planner; this module
is the perception that feeds that decision. It accepts a short textual summary
of what the user is doing — the foreground application and window/document
title, plus idle time — and nothing else.

Deliberate boundaries, matching the presence channel in ``observation.py``:

* Only short text is accepted. Image bytes, base64 blobs, and structured screen
  dumps are rejected, and every field is length-capped and stripped of control
  characters. Raw screen contents never enter the backend.
* Context lives in memory only, per client, with a TTL. It is never written to
  the stage or any persisted store. The one durable trace is a one-line note in
  daily memory, and only when the *task* changes (see ``update_screen_context``).

The latest value is read in two places: the autonomy loop (to make help
screen-aware) and the proximity-gated ``get_screen_context`` tool (so the agent
can look at the screen on demand when it is near the computer).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic
from typing import Optional

_MAX_APP_LEN = 120
_MAX_TITLE_LEN = 200
_MAX_IDLE_SECONDS = 604800.0  # one week
_SIGNAL_TTL_SECONDS = 900.0  # a pushed context is "current" for 15 minutes

# Leading markers editors prepend to an unsaved document ("● file.py"). Stripping
# them keeps a save/edit from looking like a task change.
_LEADING_MARKERS = "•●○*‣▪◦∙· \t"
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ScreenContext:
    """A coarse snapshot of what the user is doing, as short text."""

    app: Optional[str] = None
    title: Optional[str] = None
    idle_seconds: Optional[float] = None
    updated_at: float = 0.0

    def summary(self) -> str:
        """One-line human description for prompts, memory, and the tool."""
        if self.app and self.title:
            base = f"{self.app} — {self.title}"
        else:
            base = self.app or self.title or "unknown activity"
        if self.idle_seconds is not None and self.idle_seconds >= 60:
            minutes = int(self.idle_seconds // 60)
            return f"{base} (idle {minutes}m)"
        return base


@dataclass
class _StoredContext:
    context: ScreenContext
    # Normalized (app, title) of the last value we recorded to daily memory, so
    # small title churn does not re-record the same task.
    recorded_signature: Optional[tuple[str, str]] = None


_CLIENT_CONTEXT: dict[str, _StoredContext] = {}


def _clean_text(value: object, field: str, maximum: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string or null")
    collapsed = _WHITESPACE.sub(" ", _CONTROL_CHARS.sub(" ", value)).strip()
    if not collapsed:
        return None
    if len(collapsed) > maximum:
        # Reject rather than silently truncate: an over-long value usually means
        # raw screen text was sent to a channel that only accepts coarse labels.
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return collapsed


def _clean_idle(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("idle_seconds must be a number or null")
    return max(0.0, min(float(value), _MAX_IDLE_SECONDS))


def _signature(context: ScreenContext) -> tuple[str, str]:
    app = (context.app or "").casefold()
    title = (context.title or "").casefold().lstrip(_LEADING_MARKERS).strip()
    return app, title


def update_screen_context(client_uid: str, data: dict) -> tuple[ScreenContext, bool]:
    """Validate and store a pushed context; report whether the task changed.

    Returns ``(context, task_changed)``. ``task_changed`` is True the first time
    a given (app, normalized-title) pair is seen for this client, which is the
    signal to record a line in daily memory. Idle-only updates and cosmetic
    title churn return False.

    Raises ValueError if the payload is malformed or looks like raw screen data.
    """
    app = _clean_text(data.get("app"), "app", _MAX_APP_LEN)
    title = _clean_text(data.get("title"), "title", _MAX_TITLE_LEN)
    idle_seconds = _clean_idle(data.get("idle_seconds"))
    if app is None and title is None:
        raise ValueError("screen context requires an app or title")

    context = ScreenContext(
        app=app,
        title=title,
        idle_seconds=idle_seconds,
        updated_at=monotonic(),
    )

    stored = _CLIENT_CONTEXT.get(client_uid)
    signature = _signature(context)
    task_changed = stored is None or stored.recorded_signature != signature
    _CLIENT_CONTEXT[client_uid] = _StoredContext(
        context=context,
        recorded_signature=signature if task_changed else stored.recorded_signature,
    )
    if task_changed:
        from .observation import signal_observation_change

        signal_observation_change(client_uid)
    return context, task_changed


def get_screen_context(client_uid: str) -> Optional[ScreenContext]:
    """Return the latest context for a client if it is still current."""
    stored = _CLIENT_CONTEXT.get(client_uid)
    if stored is None:
        return None
    if monotonic() - stored.context.updated_at > _SIGNAL_TTL_SECONDS:
        return None
    return stored.context


def clear_screen_context(client_uid: str) -> None:
    _CLIENT_CONTEXT.pop(client_uid, None)
