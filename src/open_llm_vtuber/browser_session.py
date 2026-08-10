"""Ephemeral, in-memory snapshot of the latest browser/web-search activity.

The computer furniture panel (VR) and the computer hover window (normal mode)
render this snapshot, so a headset user can watch the agent browse and search.
The snapshot is deliberately not persisted and never logged: like panel text
elsewhere in the room, page text and screenshots stay out of the audit trail.
Only the most recent activity is kept; the next browser or web_search call
replaces it, and a VR session clears it on connect and disconnect.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

MAX_TEXT_CHARS = 2000
MAX_IMAGE_BYTES = 2 * 1024 * 1024
DEFAULT_MIME_TYPE = "image/png"

_latest: dict[str, Any] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:MAX_TEXT_CHARS]


def record_browser_activity(
    *,
    server: str,
    tool: str,
    text: str,
    url: str | None = None,
    image_bytes: bytes | None = None,
    mime_type: str = DEFAULT_MIME_TYPE,
) -> None:
    """Replace the latest snapshot with this completed browser/search call."""
    global _latest
    has_image = bool(image_bytes)
    if image_bytes and len(image_bytes) > MAX_IMAGE_BYTES:
        logger.warning(
            f"Browser snapshot for {server}/{tool} dropped an oversized "
            f"screenshot ({len(image_bytes)} bytes)."
        )
        image_bytes = None
        has_image = False
    _latest = {
        "available": True,
        "server": str(server),
        "tool": str(tool),
        "url": _clip_text(url) if url else None,
        "text": _clip_text(text),
        "has_image": has_image,
        "mime_type": mime_type if has_image else None,
        "image_bytes": image_bytes if has_image else None,
        "updated_at": _now_iso(),
    }


def browser_snapshot() -> dict[str, Any]:
    """Public view of the latest activity, safe for the furniture panel route."""
    latest = _latest
    if not latest:
        return {
            "available": False,
            "tool": None,
            "url": None,
            "text": "",
            "has_image": False,
            "image_url": "/api/furniture/browser/image",
            "updated_at": None,
        }
    return {
        "available": True,
        "server": latest["server"],
        "tool": latest["tool"],
        "url": latest["url"],
        "text": latest["text"],
        "has_image": latest["has_image"],
        "image_url": "/api/furniture/browser/image",
        "updated_at": latest["updated_at"],
    }


def browser_image_bytes() -> tuple[bytes, str] | None:
    """Return the latest screenshot bytes and MIME type, or None."""
    latest = _latest
    if not latest or not latest.get("has_image"):
        return None
    return latest["image_bytes"], latest.get("mime_type") or DEFAULT_MIME_TYPE


def clear_browser_session(client_uid: str | None = None) -> None:
    """Drop the snapshot; idempotent and safe to call for any client."""
    global _latest
    _latest = None
