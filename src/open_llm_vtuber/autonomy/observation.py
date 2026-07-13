"""Context snapshots and privacy-minimal live presence signals."""

from __future__ import annotations

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


@dataclass(frozen=True)
class ClientPresenceSignal:
    """Ephemeral, deliberately minimal context supplied by the frontend."""

    idle_seconds: Optional[float] = None
    user_present: bool = True
    focus_mode: bool = False
    updated_at: float = 0.0


_CLIENT_SIGNALS: dict[str, ClientPresenceSignal] = {}
_SIGNAL_TTL_SECONDS = 180.0


def update_client_presence(client_uid: str, data: dict) -> ClientPresenceSignal:
    """Validate and cache coarse presence signals; raw screen data is rejected."""
    previous = _CLIENT_SIGNALS.get(client_uid, ClientPresenceSignal())
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
    )
    _CLIENT_SIGNALS[client_uid] = signal
    return signal


def mark_user_activity(client_uid: str) -> None:
    previous = _CLIENT_SIGNALS.get(client_uid, ClientPresenceSignal())
    _CLIENT_SIGNALS[client_uid] = ClientPresenceSignal(
        idle_seconds=0.0,
        user_present=True,
        focus_mode=previous.focus_mode,
        updated_at=monotonic(),
    )


def get_client_presence(client_uid: str) -> ClientPresenceSignal:
    signal = _CLIENT_SIGNALS.get(client_uid)
    if signal is None or monotonic() - signal.updated_at > _SIGNAL_TTL_SECONDS:
        return ClientPresenceSignal()
    return signal


def clear_client_presence(client_uid: str) -> None:
    _CLIENT_SIGNALS.pop(client_uid, None)
