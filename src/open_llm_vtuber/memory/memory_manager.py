"""Three-layer memory / context management for the embodied agent.

Design (6/23 meeting):

    Layer 1 - Contextual memory (in-memory, this session)
        The current conversation topic plus the agent's recent embodied
        *actions* (what it was doing in the world: moving, reading the
        calendar, ...). Lives only in RAM and represents "right now".

    Layer 2 - Daily memory  -> ~/embodied-agent/memory/YYYY-MM-DD.md
        Compacted summaries of finished conversation topics, one dated file
        per day.

    Layer 3 - Long-term memory -> ~/embodied-agent/MEMORY.md
        The durable, important facts distilled from many days.

Compaction flows downward:

    Contextual -> Daily
        When the conversation topic changes (an LLM judges this) or the
        context buffer grows too large, the current context is summarized and
        appended to today's daily file, then the buffer is cleared.

    Daily -> Long-term
        A day's memory is summarized by the LLM, and only the genuinely
        important, durable items are appended to MEMORY.md.

    Long-term -> compacted
        If MEMORY.md grows past a size threshold, the LLM rewrites it more
        concisely so it stays cheap to keep in context.

All LLM use is optional: if no `llm_complete` is supplied the manager falls
back to deterministic heuristics so it stays usable (and testable) offline.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from loguru import logger

from ..workspace import ensure_workspace

# Marker appended to a daily file once it has been promoted to long-term memory,
# so it is not promoted again.
_PROMOTED_MARKER = "<!-- promoted-to-longterm -->"

LLMCompleteFn = Callable[..., Awaitable[str]]


@dataclass
class ContextEntry:
    """A single item in the contextual (current-conversation) buffer."""

    role: str  # "user", "assistant", or "action"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)

    def render(self) -> str:
        if self.role == "action":
            return f"[action] {self.content}"
        return f"{self.role}: {self.content}"


class MemoryManager:
    """Manages the agent's three memory layers and the compaction between them."""

    def __init__(
        self,
        llm_complete: Optional[LLMCompleteFn] = None,
        workspace_dir: Optional[str | Path] = None,
        *,
        max_context_entries: int = 30,
        max_longterm_chars: int = 8000,
    ) -> None:
        """Create a MemoryManager.

        Args:
            llm_complete: Optional async `(messages, system) -> str` callable
                used for summarization and topic-change detection. If omitted,
                heuristic fallbacks are used.
            workspace_dir: Override for the workspace location (defaults to
                ~/embodied-agent/).
            max_context_entries: Size at which the context buffer is force-
                compacted to daily memory even without a topic change.
            max_longterm_chars: Size above which MEMORY.md is compacted.
        """
        self._llm_complete = llm_complete
        self._workspace = ensure_workspace(workspace_dir)
        self._workspace_dir = workspace_dir
        self._max_context_entries = max_context_entries
        self._max_longterm_chars = max_longterm_chars

        # Layer 1: contextual memory (current conversation + actions).
        self._context: List[ContextEntry] = []
        self._lock = asyncio.Lock()

        # Soft-evolution: persistent familiarity/mood companion state.
        from .evolution import EvolutionState

        self._evolution = EvolutionState.load(self._workspace.root)

    # ------------------------------------------------------------------ #
    # Layer 1: contextual memory
    # ------------------------------------------------------------------ #
    def add_user_message(self, text: str) -> None:
        """Record a user message in the current context."""
        if text and text.strip():
            self._context.append(ContextEntry("user", text.strip()))
            # Each user turn nudges the companion's familiarity and mood.
            self._evolution.record_interaction(text.strip())

    @property
    def evolution(self):
        """The persistent soft-evolution companion state."""
        return self._evolution

    def note_session_start(self) -> None:
        """Mark the start of a session (bumps session count, decays idle mood)."""
        self._evolution.note_session_start()

    def add_assistant_message(self, text: str) -> None:
        """Record an assistant message in the current context."""
        if text and text.strip():
            self._context.append(ContextEntry("assistant", text.strip()))

    def record_action(self, action: str) -> None:
        """Record an embodied action the agent took (e.g. 'moved to calendar')."""
        if action and action.strip():
            self._context.append(ContextEntry("action", action.strip()))

    @property
    def context(self) -> List[ContextEntry]:
        return list(self._context)

    def render_context(self) -> str:
        """Render the current context buffer as text."""
        return "\n".join(entry.render() for entry in self._context)

    # ------------------------------------------------------------------ #
    # Compaction: contextual -> daily
    # ------------------------------------------------------------------ #
    async def maybe_compact_context(self, next_user_text: Optional[str] = None) -> bool:
        """Compact context to daily memory if the topic changed or buffer is full.

        Call this when a new user message arrives (passing it as
        `next_user_text`) to let the LLM decide whether the topic changed.

        Returns:
            True if a compaction happened.
        """
        async with self._lock:
            if not self._context:
                return False

            force = len(self._context) >= self._max_context_entries
            topic_changed = False
            if not force and next_user_text:
                topic_changed = await self._detect_topic_change(next_user_text)

            if not force and not topic_changed:
                return False

            await self._compact_context_to_daily_locked()
            return True

    async def compact_context_to_daily(self) -> bool:
        """Force-compact the current context into today's daily memory.

        Useful at session end. Returns True if anything was written.
        """
        async with self._lock:
            if not self._context:
                return False
            await self._compact_context_to_daily_locked()
            return True

    async def _compact_context_to_daily_locked(self) -> None:
        summary = await self._summarize_context()
        if summary:
            stamp = datetime.now().strftime("%H:%M")
            entry = f"## {stamp}\n{summary}\n\n"
            self._append_daily(entry)
            logger.info("Memory: compacted context -> daily memory.")
        self._context.clear()

    async def _detect_topic_change(self, next_user_text: str) -> bool:
        """Ask the LLM whether `next_user_text` starts a new topic."""
        if self._llm_complete is None:
            return False  # heuristic: never auto-detect without an LLM
        current = self.render_context()
        if not current:
            return False

        system = (
            "You decide whether a conversation has changed topic. "
            "Answer with a single word: YES if the new user message starts a "
            "clearly different topic from the prior conversation, otherwise NO."
        )
        prompt = (
            f"Prior conversation:\n{current}\n\n"
            f"New user message:\n{next_user_text}\n\n"
            "Has the topic changed? Answer YES or NO."
        )
        try:
            answer = await self._llm_complete(
                [{"role": "user", "content": prompt}], system
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Memory: topic-change detection failed: {exc}")
            return False
        return answer.strip().upper().startswith("YES")

    async def _summarize_context(self) -> str:
        """Summarize the contextual buffer into a concise daily entry."""
        rendered = self.render_context()
        if not rendered:
            return ""
        if self._llm_complete is None:
            return self._heuristic_summary(rendered)

        system = (
            "You compress a short conversation (and the agent's actions) into a "
            "few concise bullet points capturing what happened, what the user "
            "wanted, decisions made, and anything worth remembering later. "
            "Be terse. Output only the bullets."
        )
        try:
            summary = await self._llm_complete(
                [{"role": "user", "content": rendered}], system
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Memory: context summarization failed: {exc}")
            summary = ""
        return summary.strip() or self._heuristic_summary(rendered)

    # ------------------------------------------------------------------ #
    # Compaction: daily -> long-term
    # ------------------------------------------------------------------ #
    async def compact_daily_to_longterm(self, day: Optional[date] = None) -> bool:
        """Promote important items from a day's memory into long-term memory.

        Args:
            day: Which daily file to promote. Defaults to *yesterday* (today's
                memory is usually still being written).

        Returns:
            True if anything was promoted.
        """
        async with self._lock:
            target_day = day or self._yesterday()
            daily_path = self._daily_path(target_day)
            if not daily_path.exists():
                return False

            content = daily_path.read_text(encoding="utf-8")
            if _PROMOTED_MARKER in content:
                logger.debug(f"Memory: {daily_path.name} already promoted; skipping.")
                return False
            body = content.replace(self._daily_header(target_day), "").strip()
            if not body:
                return False

            important = await self._extract_longterm(body, target_day)
            if important:
                self._append_longterm(important, target_day)
                logger.info(f"Memory: promoted {daily_path.name} -> long-term memory.")

            # Mark as promoted regardless, so we don't reprocess it.
            with daily_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n{_PROMOTED_MARKER}\n")

            await self._compact_longterm_if_needed_locked()
            return bool(important)

    async def _extract_longterm(self, daily_body: str, day: date) -> str:
        if self._llm_complete is None:
            return ""  # without an LLM we don't guess at importance
        system = (
            "From the day's memory below, extract only durable, important facts "
            "worth remembering long-term: stable user preferences, important "
            "personal facts, ongoing goals, commitments, and significant shared "
            "experiences. Ignore small talk and transient details. Output a "
            "short bulleted list, or the single word NONE if nothing qualifies."
        )
        try:
            result = await self._llm_complete(
                [{"role": "user", "content": daily_body}], system
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Memory: long-term extraction failed: {exc}")
            return ""
        result = result.strip()
        if not result or result.upper().startswith("NONE"):
            return ""
        return result

    async def startup_compaction(self) -> int:
        """Promote any past, un-promoted daily memory into long-term memory.

        The Contextual -> Daily flow runs continuously during a conversation,
        but Daily -> Long-term and Long-term -> compact need a trigger. Running
        this once at agent startup makes those flows execute without depending
        on an external scheduler: every daily file older than today that hasn't
        been promoted yet is processed (and then long-term memory is compacted
        if it grew too large, which `compact_daily_to_longterm` handles).

        Returns:
            The number of daily files from which something was promoted.
        """
        mem_dir = self._workspace.daily_memory_dir
        if not mem_dir.exists():
            return 0

        today = date.today()
        promoted = 0
        for path in sorted(mem_dir.glob("*.md")):
            day = self._parse_daily_filename(path)
            if day is None or day >= today:
                continue
            try:
                if await self.compact_daily_to_longterm(day):
                    promoted += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"Memory: startup compaction failed for {path.name}: {exc}"
                )
        if promoted:
            logger.info(f"Memory: startup compaction promoted {promoted} day(s).")
        return promoted

    @staticmethod
    def _parse_daily_filename(path: Path) -> Optional[date]:
        try:
            return date.fromisoformat(path.stem)
        except ValueError:
            return None

    # ------------------------------------------------------------------ #
    # Compaction: long-term -> compacted
    # ------------------------------------------------------------------ #
    async def compact_longterm_if_needed(self) -> bool:
        """Public wrapper to compact MEMORY.md if it has grown too large."""
        async with self._lock:
            return await self._compact_longterm_if_needed_locked()

    async def _compact_longterm_if_needed_locked(self) -> bool:
        path = self._workspace.memory_file
        if not path.exists():
            return False
        content = path.read_text(encoding="utf-8")
        if len(content) <= self._max_longterm_chars:
            return False
        if self._llm_complete is None:
            return False  # don't risk lossy truncation without an LLM

        system = (
            "Rewrite the following long-term memory file to be more concise "
            "without losing any important, durable information. Merge "
            "duplicates, drop trivia, keep it well-organized in Markdown. "
            "Output only the rewritten file content."
        )
        try:
            compacted = await self._llm_complete(
                [{"role": "user", "content": content}], system
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"Memory: long-term compaction failed: {exc}")
            return False
        compacted = compacted.strip()
        if not compacted:
            return False
        path.write_text(compacted + "\n", encoding="utf-8")
        logger.info("Memory: compacted long-term memory (MEMORY.md).")
        return True

    # ------------------------------------------------------------------ #
    # Context assembly for prompt injection
    # ------------------------------------------------------------------ #
    def build_memory_context(self, max_context_chars: int = 4000) -> str:
        """Assemble a memory block for injection into the agent's prompt.

        Combines long-term memory (MEMORY.md), today's daily memory, and the
        current contextual buffer.
        """
        sections: List[str] = []

        evolution_block = self._evolution.render().strip()
        if evolution_block:
            sections.append(evolution_block)

        longterm = self._read_if_exists(self._workspace.memory_file).strip()
        if longterm:
            sections.append(f"# Long-term memory\n{longterm}")

        today_path = self._daily_path(date.today())
        today = self._read_if_exists(today_path)
        today = today.replace(_PROMOTED_MARKER, "").strip()
        if today:
            sections.append(f"# Today's memory\n{today}")

        current = self.render_context().strip()
        if current:
            if len(current) > max_context_chars:
                current = current[-max_context_chars:]
            sections.append(f"# Current conversation context\n{current}")

        return "\n\n".join(sections).strip()

    # ------------------------------------------------------------------ #
    # Filesystem helpers
    # ------------------------------------------------------------------ #
    def _daily_path(self, day: date) -> Path:
        return self._workspace.daily_memory_dir / f"{day.isoformat()}.md"

    @staticmethod
    def _daily_header(day: date) -> str:
        return f"# Daily Memory - {day.isoformat()}\n\n"

    def _append_daily(self, text: str) -> None:
        day = date.today()
        path = self._daily_path(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(self._daily_header(day), encoding="utf-8")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def _append_longterm(self, text: str, day: date) -> None:
        path = self._workspace.memory_file
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                "# MEMORY.md\n\nCore memory for the agent.\n", encoding="utf-8"
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n## From {day.isoformat()}\n{text}\n")

    @staticmethod
    def _read_if_exists(path: Path) -> str:
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
        return ""

    @staticmethod
    def _yesterday() -> date:
        from datetime import timedelta

        return date.today() - timedelta(days=1)

    @staticmethod
    def _heuristic_summary(rendered: str, max_lines: int = 20) -> str:
        lines = [line for line in rendered.splitlines() if line.strip()]
        bullets = [f"- {line}" for line in lines[:max_lines]]
        if len(lines) > max_lines:
            bullets.append(f"- ... ({len(lines) - max_lines} more lines)")
        return "\n".join(bullets)
