"""Adapter turning a streaming StatelessLLM into a simple async completion call.

The memory system only needs "give these messages, get back one finished
string" for its summarization / topic-detection prompts. The project's
`StatelessLLMInterface.chat_completion` is a streaming async iterator that
yields either plain `str` chunks (OpenAI-compatible backends) or dict events
like `{"type": "text_delta", "text": ...}` (Claude backend). This adapter
collapses either form into a single string.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Protocol

from loguru import logger

from ..agent.stateless_llm.stateless_llm_interface import StatelessLLMInterface


class LLMComplete(Protocol):
    """A minimal async text-completion callable used by the memory system."""

    async def __call__(
        self, messages: List[Dict[str, Any]], system: str = ""
    ) -> str: ...


def make_llm_complete(llm: StatelessLLMInterface) -> Callable[..., Awaitable[str]]:
    """Wrap a StatelessLLM into an `LLMComplete` callable.

    Args:
        llm: The stateless LLM to drive.

    Returns:
        An async callable `(messages, system) -> str` that fully consumes the
        stream and returns the concatenated text.
    """

    async def _complete(messages: List[Dict[str, Any]], system: str = "") -> str:
        chunks: List[str] = []
        try:
            async for event in llm.chat_completion(messages, system or None):
                if isinstance(event, str):
                    chunks.append(event)
                elif isinstance(event, dict):
                    # Claude-style events: only text_delta carries text.
                    if event.get("type") == "text_delta":
                        chunks.append(event.get("text", ""))
                    elif "text" in event:
                        chunks.append(str(event["text"]))
                # Anything else (tool events, sentinels) is ignored for memory.
        except Exception as exc:  # noqa: BLE001 - memory should never crash the agent
            logger.error(f"Memory LLM completion failed: {exc}")
            return ""
        return "".join(chunks).strip()

    return _complete
