"""Measuring whether embodiment and memory actually create presence.

The project's claim is that a persistent, spatially embodied companion feels
more present than a chat window. Nothing in the system could answer that: the
agent works, but there was no way to say what it did *for* anyone, and no way
to run it without one of the pillars to see whether that pillar mattered.

This module supplies the two halves of an answer.

**Conditions** ablate one pillar at a time, from the environment, so a session
can be launched in a condition without editing anything:

    EMBODIED_AGENT_CONDITION=memory=off uv run run_server.py

**An append-only event log** records what happened, in categories rather than
content. No message text, no transcripts, no file contents — turn counts,
object ids, tool names, scores, and timings. A log that cannot embarrass a
participant is one that can actually be shared with a supervisor.

`scripts/study_report.py` turns the logs into a per-session table.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

# The pillars the project claims create presence. Each can be switched off on
# its own; the log records which were on, so sessions can be compared.
PILLARS = ("embodiment", "memory", "autonomy")

_CONDITION_ENV = "EMBODIED_AGENT_CONDITION"
_PARTICIPANT_ENV = "EMBODIED_AGENT_PARTICIPANT"
_LOG_ENV = "EMBODIED_AGENT_STUDY_LOG"

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}

# A backstop, not the policy: call sites pass categories, and anything longer
# than this is recorded as a length so a transcript can never leak in by
# accident.
MAX_VALUE_CHARS = 120


_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def safe_participant(raw: str | None) -> str:
    """Reduce a participant id to something that is certainly a filename.

    The id comes from a launcher script or a hurried operator, and it lands in
    a path. A `P<1>` used to mean the file could not be opened at all, which
    lost the entire session and said so only in a log line; a `../..` used to
    put it somewhere nobody would look for it.
    """
    cleaned = _UNSAFE_IN_NAME.sub("-", (raw or "").strip()).strip("-.")
    return cleaned[:48] or "anonymous"


@dataclass(frozen=True)
class Conditions:
    """Which pillars are switched on for this run."""

    embodiment: bool = True
    memory: bool = True
    autonomy: bool = True

    @classmethod
    def parse(cls, spec: str | None) -> "Conditions":
        """Read a spec like ``memory=off,autonomy=off``.

        Unknown names and unparseable values are ignored with a warning rather
        than failing the run: a typo in a launcher should not cost a
        participant's session, and the log records what was actually applied.
        """
        values = {pillar: True for pillar in PILLARS}
        for clause in (spec or "").replace(";", ",").split(","):
            clause = clause.strip()
            if not clause:
                continue
            name, _, raw = clause.partition("=")
            name = name.strip().lower()
            raw = raw.strip().lower()
            if name not in values:
                logger.warning(f"Study: ignoring unknown condition '{name}'.")
                continue
            if raw in _TRUTHY or raw == "":
                values[name] = True
            elif raw in _FALSY:
                values[name] = False
            else:
                logger.warning(
                    f"Study: ignoring unreadable value for '{name}': '{raw}'."
                )
        return cls(**values)

    def as_dict(self) -> dict[str, bool]:
        return {pillar: getattr(self, pillar) for pillar in PILLARS}

    @property
    def label(self) -> str:
        """A short name for the condition, for grouping sessions in a report."""
        off = [pillar for pillar in PILLARS if not getattr(self, pillar)]
        if not off:
            return "full"
        return "-".join(f"no-{pillar}" for pillar in off)

    @property
    def is_ablated(self) -> bool:
        return self.label != "full"


# Reentrant on purpose: logging an event opens the session, which asks for the
# conditions, and each of those guards the same state.
_lock = threading.RLock()
_conditions: Conditions | None = None
_session: "_Session | None" = None


@dataclass
class _Session:
    session_id: str
    participant: str
    started_at: datetime
    path: Path
    conditions: Conditions
    warned: bool = False


def conditions() -> Conditions:
    """The conditions in force, read once per process."""
    global _conditions
    with _lock:
        if _conditions is None:
            _conditions = Conditions.parse(os.environ.get(_CONDITION_ENV))
            if _conditions.is_ablated:
                logger.info(f"Study: running ablated condition '{_conditions.label}'.")
        return _conditions


def logging_enabled() -> bool:
    """Whether events are being written.

    On when a participant is named or a pillar is switched off, because both
    say plainly that this run is a study session. Naming a participant has to
    be enough on its own: the control condition is the one every comparison is
    measured against, and it is the one where nothing else looks unusual.

    Off otherwise, so ordinary use writes nothing at all. `EMBODIED_AGENT_STUDY_LOG`
    overrides in either direction.
    """
    override = os.environ.get(_LOG_ENV, "").strip().lower()
    if override in _TRUTHY:
        return True
    if override in _FALSY:
        return False
    return bool(os.environ.get(_PARTICIPANT_ENV, "").strip()) or conditions().is_ablated


def _study_dir() -> Path:
    from .workspace import DEFAULT_WORKSPACE_DIR

    override = os.environ.get("EMBODIED_AGENT_WORKSPACE")
    root = Path(override).expanduser() if override else DEFAULT_WORKSPACE_DIR
    return root / "study"


def _open_session() -> "_Session | None":
    """Start a session on the first event, or return the running one."""
    global _session
    if _session is not None:
        return _session
    if not logging_enabled():
        return None

    started = _utc_now()
    session_id = uuid4().hex
    participant = safe_participant(os.environ.get(_PARTICIPANT_ENV))
    active = conditions()

    try:
        directory = _study_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = started.strftime("%Y%m%dT%H%M%SZ")
        path = directory / f"{stamp}-{participant}-{session_id[:8]}.jsonl"
        # Prove the file can actually be written now, while somebody is still
        # watching the console. Finding out at the end of a session that every
        # event was dropped is the one failure this module must not have.
        with path.open("a", encoding="utf-8"):
            pass
    except Exception as exc:  # noqa: BLE001
        logger.error(
            f"Study: cannot write a session log, so this run records nothing: {exc}"
        )
        return None

    _session = _Session(
        session_id=session_id,
        participant=participant,
        started_at=started,
        path=path,
        conditions=active,
    )
    logger.info(f"Study: logging session {session_id[:8]} to {path}")
    _write(
        _session,
        "session_start",
        {
            "condition": active.label,
            **{f"pillar_{name}": value for name, value in active.as_dict().items()},
        },
    )
    return _session


def _clean(value: Any) -> Any:
    """Reduce a value to something a shareable log may contain."""
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    text = str(value)
    if len(text) > MAX_VALUE_CHARS:
        # Length, never the text: this is the guard that keeps transcripts out.
        return {"chars": len(text)}
    return text


def _write(session: "_Session", event: str, data: dict[str, Any]) -> None:
    record = {
        "ts": _utc_now().isoformat(timespec="milliseconds"),
        "session": session.session_id,
        "participant": session.participant,
        "condition": session.conditions.label,
        "event": event,
        **{key: _clean(value) for key, value in data.items()},
    }
    try:
        with session.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        # Instrumentation must never take the session down with it. Keep
        # trying, in case the cause was transient, but say so only once: a
        # warning per event would bury the rest of the log.
        if not session.warned:
            session.warned = True
            logger.warning(f"Study: dropped a '{event}' event and possibly more: {exc}")


def log_event(event: str, **data: Any) -> None:
    """Record one thing that happened. A no-op when logging is off."""
    with _lock:
        session = _open_session()
        if session is None:
            return
        _write(session, event, data)


_turn_started: datetime | None = None
_agent_finished: datetime | None = None


def log_turn_started(source: str, **data: Any) -> None:
    """Record a turn beginning, and how long the user took to start it.

    The gap since the agent last stopped talking is the closest thing to a
    behavioral measure of engagement available without reading content: a
    companion someone is present with gets answered sooner.
    """
    global _turn_started
    now = _utc_now()
    since_agent = (
        round((now - _agent_finished).total_seconds(), 2) if _agent_finished else None
    )
    _turn_started = now
    log_event(
        "turn_started",
        source=source,
        seconds_since_agent_finished=since_agent,
        **data,
    )


def log_turn_finished(reply_chars: int) -> None:
    """Record a turn ending, and how long the agent took over it."""
    global _turn_started, _agent_finished
    now = _utc_now()
    seconds = round((now - _turn_started).total_seconds(), 2) if _turn_started else None
    _turn_started = None
    _agent_finished = now
    log_event("turn_finished", duration_seconds=seconds, reply_chars=reply_chars)


def end_session() -> None:
    """Record how long the sitting has lasted so far.

    The session itself is deliberately *not* closed. A participant who reloads
    the page disconnects and reconnects, and starting a second log there would
    split one sitting into two short ones and halve every per-session measure.
    Later activity keeps appending to the same file, and each `session_end`
    carries the duration since the sitting began, so the last one written is
    the true length however many times the window was reopened.
    """
    global _turn_started, _agent_finished
    with _lock:
        if _session is None:
            return
        seconds = (_utc_now() - _session.started_at).total_seconds()
        _write(_session, "session_end", {"duration_seconds": round(seconds, 1)})
        # The sitting continues, but the turn clocks do not: whatever the
        # participant was doing while the window was closed is not them taking
        # their time to answer, and one such gap can move a median.
        _turn_started = None
        _agent_finished = None


def session_path() -> Path | None:
    """Where the running session is being written, if it is."""
    return _session.path if _session else None


def reset_for_tests() -> None:
    global _conditions, _session, _turn_started, _agent_finished
    with _lock:
        _conditions = None
        _session = None
        _turn_started = None
        _agent_finished = None
