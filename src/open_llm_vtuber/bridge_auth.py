"""Shared-secret auth for the internal frontend-action bridge.

The `/internal/frontend-action` HTTP endpoint exists so that local MCP tool
subprocesses (see `mcp_servers/scene_tools.py`) can ask the running frontend
to perform actions. It must NOT be callable by anything else: the server adds
permissive CORS headers globally, so without auth any website open in the
user's browser could POST to localhost and drive the stage.

A random token is generated once per server run. It is handed to MCP
subprocesses via the `EMBODIED_AGENT_BRIDGE_TOKEN` environment variable and
required on every bridge request via the `X-Bridge-Token` header. The token
never touches disk.
"""

from __future__ import annotations

import hmac
import secrets

_ENV_VAR = "EMBODIED_AGENT_BRIDGE_TOKEN"
_HEADER = "X-Bridge-Token"

_token: str | None = None


def get_bridge_token() -> str:
    """Return this server run's bridge token, generating it on first use."""
    global _token
    if _token is None:
        _token = secrets.token_urlsafe(32)
    return _token


def verify_bridge_token(candidate: str | None) -> bool:
    """Constant-time comparison of a presented token against the real one."""
    if not candidate:
        return False
    return hmac.compare_digest(candidate, get_bridge_token())


BRIDGE_TOKEN_ENV_VAR = _ENV_VAR
BRIDGE_TOKEN_HEADER = _HEADER
