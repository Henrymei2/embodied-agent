"""Keep the companion answering when its model is unreachable.

`fallback_tts` already exists for exactly this reason: bad conference wifi
turns a voice-first companion silent, and that is the one failure a live demo
cannot absorb. The model has the same problem and a worse consequence — no
voice means subtitles, but no model means nothing at all. Ollama not running,
a machine that slept, a GPU that did not come back: any of them ends a
presentation before the first beat.

So this wraps the configured model the same way. It streams from the real one,
and only if that fails before producing a single token does it fall back to a
recorded reply.

Two rules keep this honest, and both matter more than the convenience:

**It never silently replaces a working model.** The fallback engages only when
the real one raises before emitting anything. A model that produces a bad
answer is still the model's answer, and the audience is seeing the system.

**It says so, loudly and visibly.** Every fallback reply is logged at warning
level and the run is marked degraded, so nobody demonstrates a canned response
believing it came from the agent. A demo that quietly fakes its own headline is
worse than a demo that fails.

The replies live in `demo/fallback_replies.json` and are matched by keyword,
with a generic honest one when nothing matches. They exist to keep a room
moving to the next beat, not to simulate the agent.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List

from loguru import logger

from .stateless_llm_interface import StatelessLLMInterface

# Set to "1" to make the fallback refuse to engage, so a rehearsal proves the
# real model is being used rather than quietly resting on the safety net.
STRICT_ENV = "EMBODIED_AGENT_REQUIRE_LIVE_LLM"

_DEFAULT_REPLY = (
    "I can't reach my language model right now, so I'm answering from a "
    "recorded line. Everything else in the room still works."
)


class FallbackLLM(StatelessLLMInterface):
    """Stream from the configured model; use a recorded reply if it cannot start."""

    def __init__(
        self,
        primary: StatelessLLMInterface,
        *,
        replies_path: Path | None = None,
    ) -> None:
        self._primary = primary
        self._replies_path = replies_path or (
            Path(__file__).resolve().parents[3].parent
            / "demo"
            / "fallback_replies.json"
        )
        self._replies: list[dict[str, Any]] | None = None
        self.degraded = False

    # -- recorded replies ----------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        """Read the recorded replies lazily, so a missing file costs nothing."""
        if self._replies is not None:
            return self._replies
        replies: list[dict[str, Any]] = []
        try:
            payload = json.loads(self._replies_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                replies = [entry for entry in payload if isinstance(entry, dict)]
        except (OSError, ValueError):
            logger.debug("No demo fallback replies available.")
        self._replies = replies
        return replies

    def _reply_for(self, messages: List[Dict[str, Any]]) -> str:
        last = ""
        for message in reversed(messages or []):
            if message.get("role") == "user":
                content = message.get("content")
                last = content if isinstance(content, str) else ""
                break
        lowered = last.casefold()
        for entry in self._load():
            keywords = entry.get("keywords")
            reply = entry.get("reply")
            if not isinstance(keywords, list) or not isinstance(reply, str):
                continue
            if any(isinstance(k, str) and k.casefold() in lowered for k in keywords):
                return reply
        return _DEFAULT_REPLY

    # -- interface -----------------------------------------------------------

    def get_tool_mode(self):
        """Preserve the primary model's native tool protocol through wrapping."""
        return self._primary.get_tool_mode()

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
        tools: List[Dict[str, Any]] = None,
    ) -> AsyncIterator[Any]:
        started = False
        try:
            async for token in self._primary.chat_completion(
                messages, system=system, tools=tools
            ):
                started = True
                yield token
            return
        except Exception as exc:  # noqa: BLE001 - any model failure is the case here
            if started:
                # Mid-stream failure: the model did answer, and swapping to a
                # recorded line here would stitch two different voices into one
                # reply. Let the partial answer stand.
                logger.warning(f"Model stream failed after starting: {exc}")
                return
            if os.environ.get(STRICT_ENV, "").strip() == "1":
                logger.error(
                    f"Model unavailable and {STRICT_ENV}=1, so the fallback is "
                    "refusing to answer."
                )
                raise
            logger.warning(
                f"Model unavailable ({type(exc).__name__}); answering from a "
                "RECORDED demo line. This reply did not come from the agent."
            )

        self.degraded = True
        for word in self._reply_for(messages).split(" "):
            yield word + " "


def wrap_with_fallback(primary: StatelessLLMInterface) -> StatelessLLMInterface:
    """Wrap a model unless the operator has demanded a live one."""
    if os.environ.get(STRICT_ENV, "").strip() == "1":
        return primary
    return FallbackLLM(primary)
