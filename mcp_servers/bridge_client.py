"""Client side of the backend's frontend-action bridge.

Most tools only read the persisted stage, but anything that has to change what
the running frontend shows — walking somewhere, sitting down, moving a piece of
furniture — must go through the live WebSocket channel. The backend exposes that
as an internal HTTP endpoint and injects a per-run token plus this session's
client and character identity into each MCP subprocess's environment, so a
request cannot be routed to a different connected frontend.

See `src/open_llm_vtuber/frontend_bridge.py` for the server side.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_CONF_UID = "mao_pro_001"
DEFAULT_BACKEND_URL = "http://localhost:12393"
ACTION_TIMEOUT_SECONDS = 15.0


def get_conf_uid() -> str:
    """Return this MCP subprocess's session-bound character configuration."""
    return os.environ.get("EMBODIED_AGENT_CONF_UID", DEFAULT_CONF_UID)


def post_frontend_action_sync(
    action: str,
    args: dict[str, Any],
) -> tuple[bool, str | None]:
    backend_url = os.environ.get("EMBODIED_AGENT_BACKEND_URL", DEFAULT_BACKEND_URL)
    url = f"{backend_url.rstrip('/')}/internal/frontend-action"
    body = json.dumps(
        {
            "action": action,
            "args": args,
            "timeout": ACTION_TIMEOUT_SECONDS,
            "client_uid": os.environ.get("EMBODIED_AGENT_CLIENT_UID"),
            "conf_uid": get_conf_uid(),
        }
    ).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    # The backend injects this per-run secret into our subprocess env; the
    # bridge endpoint rejects requests without it.
    bridge_token = os.environ.get("EMBODIED_AGENT_BRIDGE_TOKEN")
    if bridge_token:
        headers["X-Bridge-Token"] = bridge_token
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(
            request,
            timeout=ACTION_TIMEOUT_SECONDS + 2.0,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return False, f"backend returned HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"could not reach backend frontend-action bridge: {exc}"

    if payload.get("ok"):
        return True, None
    return False, payload.get("error") or "frontend action failed"


async def post_frontend_action(
    action: str,
    args: dict[str, Any],
) -> tuple[bool, str | None]:
    return await asyncio.to_thread(post_frontend_action_sync, action, args)
