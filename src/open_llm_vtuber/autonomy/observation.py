"""Context snapshots and privacy-minimal live presence signals."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic
from typing import Optional


@dataclass(frozen=True)
class Observation:
    # Seconds since the user last did anything (typed/spoke). None if unknown.
    idle_seconds: Optional[float] = None
    # Whether a client/user is currently connected/present.
    user_present: bool = True
    # Seconds since the agent last spoke to the user. None if it never has.
    seconds_since_last_spoke: Optional[float] = None
    # Local hour of day (0-23), if known; lets greetings be time-aware.
    hour_of_day: Optional[int] = None
    # Explicit user-controlled do-not-interrupt signal from the frontend.
    focus_mode: bool = False
    # False when the live presence heartbeat is missing or expired. User-facing
    # initiative must fail closed, while safe silent work may still continue.
    presence_fresh: bool = True
    # One-line coarse summary of what the user is doing on screen (app + title),
    # if a recent screen-context push is available. Lets help be actionable.
    screen_context: Optional[str] = None
    # Durable, content-free work signals used to avoid generic make-work.
    approved_plan_count: int = 0
    open_todo_count: int = 0
    has_active_reading: bool = False
    has_recent_context: bool = False


@dataclass(frozen=True)
class ClientPresenceSignal:
    """Ephemeral, deliberately minimal context supplied by the frontend."""

    idle_seconds: Optional[float] = None
    user_present: bool = True
    focus_mode: bool = False
    updated_at: float = 0.0
    fresh: bool = False


_CLIENT_SIGNALS: dict[str, ClientPresenceSignal] = {}
_CLIENT_CHANGE_EVENTS: dict[str, asyncio.Event] = {}
_SIGNAL_TTL_SECONDS = 180.0


def _idle_band(idle_seconds: Optional[float]) -> str:
    if idle_seconds is None:
        return "unknown"
    if idle_seconds < 60:
        return "active"
    if idle_seconds < 300:
        return "recently-active"
    if idle_seconds < 1800:
        return "idle"
    return "away"


def signal_observation_change(client_uid: str) -> None:
    """Wake the client's planner after a meaningful observation change."""
    event = _CLIENT_CHANGE_EVENTS.get(client_uid)
    if event is not None:
        event.set()


async def wait_for_observation_change(client_uid: str, timeout: float) -> bool:
    """Wait for meaningful context or fall back to the periodic heartbeat."""
    event = _CLIENT_CHANGE_EVENTS.setdefault(client_uid, asyncio.Event())
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        event.clear()


def update_client_presence(client_uid: str, data: dict) -> ClientPresenceSignal:
    """Validate and cache coarse presence signals; raw screen data is rejected."""
    previous = _CLIENT_SIGNALS.get(client_uid, ClientPresenceSignal())
    previous_fresh = (
        previous.fresh and monotonic() - previous.updated_at <= _SIGNAL_TTL_SECONDS
    )
    idle = data.get("idle_seconds", previous.idle_seconds)
    if idle is not None:
        if isinstance(idle, bool) or not isinstance(idle, (int, float)):
            raise ValueError("idle_seconds must be a number or null")
        idle = max(0.0, min(float(idle), 604800.0))

    user_present = data.get("user_present", previous.user_present)
    focus_mode = data.get("focus_mode", previous.focus_mode)
    if not isinstance(user_present, bool):
        raise ValueError("user_present must be a boolean")
    if not isinstance(focus_mode, bool):
        raise ValueError("focus_mode must be a boolean")

    signal = ClientPresenceSignal(
        idle_seconds=idle,
        user_present=user_present,
        focus_mode=focus_mode,
        updated_at=monotonic(),
        fresh=True,
    )
    _CLIENT_SIGNALS[client_uid] = signal
    if (
        not previous_fresh
        or previous.user_present != signal.user_present
        or previous.focus_mode != signal.focus_mode
        or _idle_band(previous.idle_seconds) != _idle_band(signal.idle_seconds)
    ):
        signal_observation_change(client_uid)
    return signal


def mark_user_activity(client_uid: str) -> None:
    previous = _CLIENT_SIGNALS.get(client_uid, ClientPresenceSignal())
    previous_fresh = (
        previous.fresh and monotonic() - previous.updated_at <= _SIGNAL_TTL_SECONDS
    )
    _CLIENT_SIGNALS[client_uid] = ClientPresenceSignal(
        idle_seconds=0.0,
        user_present=True,
        focus_mode=previous.focus_mode,
        updated_at=monotonic(),
        fresh=True,
    )
    if (
        not previous_fresh
        or not previous.user_present
        or _idle_band(previous.idle_seconds) != "active"
    ):
        signal_observation_change(client_uid)


def get_client_presence(client_uid: str) -> ClientPresenceSignal:
    signal = _CLIENT_SIGNALS.get(client_uid)
    if signal is None or monotonic() - signal.updated_at > _SIGNAL_TTL_SECONDS:
        return ClientPresenceSignal(user_present=False, fresh=False)
    return signal


def clear_client_presence(client_uid: str) -> None:
    _CLIENT_SIGNALS.pop(client_uid, None)
    event = _CLIENT_CHANGE_EVENTS.pop(client_uid, None)
    if event is not None:
        event.set()
