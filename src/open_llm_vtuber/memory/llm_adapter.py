"""Adapter turning a streaming StatelessLLM into a simple async completion call.

The memory system only needs "give these messages, get back one finished
string" for its summarization / topic-detection prompts. The project's
`StatelessLLMInterface.chat_completion` is a streaming async iterator that
yields either plain `str` chunks (OpenAI-compatible backends) or dict events
like `{"type": "text_delta", "text": ...}` (Claude backend). This adapter
collapses either form into a single string.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Protocol

from loguru import logger

from ..agent.stateless_llm.stateless_llm_interface import StatelessLLMInterface


class LLMComplete(Protocol):
    """A minimal async text-completion callable used by the memory system."""

    async def __call__(
        self, messages: List[Dict[str, Any]], system: str = ""
    ) -> str: ...


def make_llm_complete(
    llm: StatelessLLMInterface,
    *,
    raise_errors: bool = False,
    json_object: bool = False,
) -> Callable[..., Awaitable[str]]:
    """Wrap a StatelessLLM into an `LLMComplete` callable.

    Args:
        llm: The stateless LLM to drive.
        raise_errors: Propagate provider failures instead of returning the memory
            subsystem's empty-string fallback.
        json_object: Use the provider's structured-output path and return as soon
            as one complete visible JSON object has streamed.

    Returns:
        An async callable `(messages, system) -> str` that returns the
        concatenated visible text. JSON mode may finish before the stream tail.
    """

    async def _complete(messages: List[Dict[str, Any]], system: str = "") -> str:
        chunks: List[str] = []
        try:
            stream = (
                llm.json_completion(messages, system or None)
                if json_object
                else llm.chat_completion(messages, system or None)
            )
            async for event in stream:
                if isinstance(event, str):
                    chunks.append(event)
                elif isinstance(event, dict):
                    event_type = event.get("type")
                    # Only provider-visible text belongs in a completion. In
                    # particular, reasoning_content is protocol state for
                    # thinking-mode tool replay and must never leak into JSON
                    # planner responses or memory summaries.
                    if event_type == "text_delta":
                        chunks.append(str(event.get("text", "")))
                    elif event_type == "error":
                        raise RuntimeError(
                            str(
                                event.get(
                                    "message", "The language model request failed."
                                )
                            )
                        )
                if json_object and _has_complete_json_object("".join(chunks)):
                    return "".join(chunks).strip()
                # Anything else (tool events, sentinels) is ignored for memory.
        except Exception as exc:  # noqa: BLE001 - memory should never crash the agent
            if raise_errors:
                raise
            logger.error(f"Memory LLM completion failed: {exc}")
            return ""
        return "".join(chunks).strip()

    return _complete


def _has_complete_json_object(text: str) -> bool:
    """Recognize a complete root object without mistaking a nested object for it."""
    candidate = text.lstrip()
    fence = re.match(r"```(?:json)?\s*", candidate, re.I)
    if fence is not None:
        candidate = candidate[fence.end() :].lstrip()
    if not candidate.startswith("{"):
        return False
    try:
        value, _end = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        return False
    return isinstance(value, dict)
