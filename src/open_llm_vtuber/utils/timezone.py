"""Shared timezone helpers.

All user-facing timestamps in this project (scheduling, timers, chat history,
memory logs, etc.) share one timezone so they stay consistent with each other.
By default that timezone is auto-detected from the machine's system clock, so
the agent naturally follows wherever the user's device is. Setting
`system_config.timezone` in conf.yaml (an IANA name, e.g. "America/Chicago")
overrides the auto-detected value.
"""

from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_CONF_PATH = _PROJECT_ROOT / "conf.yaml"

_cached_tz: tzinfo | None = None
_cached_source: str | None = None  # conf.yaml path we last read, for cache busting


def _read_configured_timezone() -> str:
    """Best-effort read of system_config.timezone from conf.yaml, without
    depending on the full pydantic config stack (this module is imported by
    standalone MCP-server subprocesses too)."""
    try:
        import yaml

        with open(_CONF_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return str((data.get("system_config") or {}).get("timezone") or "").strip()
    except (OSError, yaml.YAMLError, AttributeError):
        return ""


def _detect_system_timezone() -> tzinfo:
    """Fall back to the local system timezone (whatever the OS clock reports)."""
    local_tz = datetime.now().astimezone().tzinfo
    return local_tz or ZoneInfo("UTC")


def _resolve_tz() -> tzinfo:
    global _cached_tz, _cached_source
    if _cached_tz is not None and _cached_source == str(_CONF_PATH):
        return _cached_tz

    configured = _read_configured_timezone()
    if configured:
        try:
            _cached_tz = ZoneInfo(configured)
        except Exception:
            _cached_tz = _detect_system_timezone()
    else:
        _cached_tz = _detect_system_timezone()

    _cached_source = str(_CONF_PATH)
    return _cached_tz


def get_local_tz() -> tzinfo:
    """Return the resolved timezone: the conf.yaml override if set, otherwise
    the system's local timezone."""
    return _resolve_tz()


def get_timezone_override() -> str:
    """Return the raw `system_config.timezone` override from conf.yaml, or an
    empty string if unset (meaning: auto-detect from the system)."""
    return _read_configured_timezone()


def now_local() -> datetime:
    """Return the current time as a timezone-aware datetime in the resolved
    local timezone."""
    return datetime.now(_resolve_tz())


def to_local(dt: datetime) -> datetime:
    """Convert a datetime to the resolved local timezone, assuming naive
    datetimes are already UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(_resolve_tz())
