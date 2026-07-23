"""Screen-context tool for the embodied agent.

`get_screen_context` lets the agent look at what the user is doing on screen —
the foreground application, the window/document title, and idle time — as a short
text summary. It belongs to the computer: the tool is only exposed, and only
runs, when the avatar is near the `computer` object.

The frontend pushes this coarse context to the backend on an interval; this tool
reads the latest pushed value through the same loopback, bridge-token-guarded
internal channel the scene tools use. The context is coarse text only — no
screenshot or raw screen contents are ever involved.

Run standalone for a quick sanity check:
    uv run python mcp_servers/screen_tools.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

_DEFAULT_BACKEND_URL = "http://localhost:12393"
_REQUEST_TIMEOUT_SECONDS = 8.0

mcp = FastMCP("screen")


def _get_screen_context_sync() -> tuple[bool, dict[str, Any] | None, str | None]:
    backend_url = os.environ.get("EMBODIED_AGENT_BACKEND_URL", _DEFAULT_BACKEND_URL)
    url = f"{backend_url.rstrip('/')}/internal/screen-context"
    body = json.dumps(
        {"client_uid": os.environ.get("EMBODIED_AGENT_CLIENT_UID")}
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    # The backend injects this per-run secret into our subprocess env; the
    # internal endpoint rejects requests without it.
    bridge_token = os.environ.get("EMBODIED_AGENT_BRIDGE_TOKEN")
    if bridge_token:
        headers["X-Bridge-Token"] = bridge_token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(
            request, timeout=_REQUEST_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
            return False, None, payload.get("error") or f"HTTP {exc.code}"
        except Exception:  # noqa: BLE001
            return False, None, f"backend returned HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, None, f"could not reach the screen-context bridge: {exc}"

    if not isinstance(payload, dict):
        return False, None, "unexpected response from backend"
    return True, payload, None


@mcp.tool()
async def get_screen_context() -> str:
    """Look at what the user is currently doing on screen.

    Requires standing near the computer. Returns a short summary of the
    foreground application, window/document title, and idle time — coarse labels
    only, never a screenshot or the screen's contents. Use it to make help
    timely and specific, and respect the user's focus.

    Returns:
        A short description of the current activity, or a note that no recent
        screen context is available.
    """
    ok, payload, error = await asyncio.to_thread(_get_screen_context_sync)
    if not ok:
        return f"Error: {error}"
    if not payload.get("available"):
        return (
            "No recent screen context is available "
            "(the desktop client may not be sharing it right now)."
        )
    return json.dumps(
        {
            "activity": payload.get("summary"),
            "app": payload.get("app"),
            "title": payload.get("title"),
            "idle_seconds": payload.get("idle_seconds"),
        },
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()
