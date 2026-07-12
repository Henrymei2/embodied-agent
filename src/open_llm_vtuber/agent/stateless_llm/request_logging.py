"""Shared debug logging helpers for outbound LLM requests."""

from __future__ import annotations

from typing import Any

from loguru import logger

_MAX_STRING_CHARS = 2_000
_IMAGE_PLACEHOLDER = "[image data omitted]"


def _truncate(value: str, max_chars: int = _MAX_STRING_CHARS) -> str:
    if len(value) <= max_chars:
        return value
    return f"{value[:max_chars]}... [truncated {len(value) - max_chars} chars]"


def sanitize_for_llm_request_log(value: Any) -> Any:
    """Return a log-friendly copy of an LLM request value."""
    if isinstance(value, str):
        if value.startswith("data:image"):
            return _IMAGE_PLACEHOLDER
        return _truncate(value)

    if isinstance(value, list):
        return [sanitize_for_llm_request_log(item) for item in value]

    if isinstance(value, tuple):
        return tuple(sanitize_for_llm_request_log(item) for item in value)

    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if key in {"data", "image_url"}:
                sanitized[key] = _IMAGE_PLACEHOLDER
            else:
                sanitized[key] = sanitize_for_llm_request_log(item)
        return sanitized

    return value


def _tool_names(tools: Any) -> list[str]:
    if not isinstance(tools, list):
        return []

    names = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        if "function" in tool and isinstance(tool["function"], dict):
            name = tool["function"].get("name")
        else:
            name = tool.get("name")
        if name:
            names.append(str(name))
    return names


def log_llm_request(
    *,
    provider: str,
    model: str | None = None,
    endpoint: str | None = None,
    messages: list[dict[str, Any]] | None = None,
    system: str | None = None,
    tools: Any = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Emit a consistent debug log for an outbound LLM request."""
    request_summary = {
        "provider": provider,
        "model": model,
        "endpoint": endpoint,
        "message_count": len(messages or []),
        "system_chars": len(system or ""),
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "tool_names": _tool_names(tools),
        "extra": extra or {},
    }
    request_payload = {
        "system": sanitize_for_llm_request_log(system),
        "messages": sanitize_for_llm_request_log(messages or []),
        "tools": sanitize_for_llm_request_log(tools),
    }

    logger.debug(f"LLM request summary: {request_summary}")
    logger.debug(f"LLM request payload: {request_payload}")
