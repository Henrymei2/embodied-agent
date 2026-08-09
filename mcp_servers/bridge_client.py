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
import ssl
import urllib.parse
import urllib.error
import urllib.request
from typing import Any

DEFAULT_CONF_UID = "mao_pro_001"
DEFAULT_BACKEND_URL = "http://localhost:12393"
ACTION_TIMEOUT_SECONDS = 30.0


def get_conf_uid() -> str:
    """Return this MCP subprocess's session-bound character configuration."""
    return os.environ.get("EMBODIED_AGENT_CONF_UID", DEFAULT_CONF_UID)


def get_scene_mode() -> str:
    value = os.environ.get("EMBODIED_AGENT_SCENE_MODE", "normal")
    return "vr" if value == "vr" else "normal"


def post_frontend_action_result_sync(
    action: str,
    args: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    """Run a frontend action and return what the frontend reported back.

    The third element is the frontend's own result object — how far the avatar
    actually walked, where it ended up — which is what lets a tool verify an
    outcome instead of trusting that the request was accepted.
    """
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
        context = None
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme == "https" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            # This is the token-authenticated, loopback-only subprocess bridge.
            # The public Quest connection still requires a trusted certificate.
            context = ssl._create_unverified_context()
        open_kwargs = {"timeout": ACTION_TIMEOUT_SECONDS + 2.0}
        if context is not None:
            open_kwargs["context"] = context
        with urllib.request.urlopen(request, **open_kwargs) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return False, f"backend returned HTTP {exc.code}", {}
    except Exception as exc:  # noqa: BLE001
        return False, f"could not reach backend frontend-action bridge: {exc}", {}

    result = payload.get("result")
    result = result if isinstance(result, dict) else {}
    if payload.get("ok"):
        return True, None, result
    return False, payload.get("error") or "frontend action failed", result


def post_frontend_action_sync(
    action: str,
    args: dict[str, Any],
) -> tuple[bool, str | None]:
    ok, error, _result = post_frontend_action_result_sync(action, args)
    return ok, error


async def post_frontend_action_result(
    action: str,
    args: dict[str, Any],
) -> tuple[bool, str | None, dict[str, Any]]:
    return await asyncio.to_thread(post_frontend_action_result_sync, action, args)


async def post_frontend_action(
    action: str,
    args: dict[str, Any],
) -> tuple[bool, str | None]:
    return await asyncio.to_thread(post_frontend_action_sync, action, args)
