"""Tests for the three-layer memory system."""

from __future__ import annotations

from datetime import date

import pytest

from src.open_llm_vtuber.memory import MemoryManager


class FakeLLM:
    """Scriptable async completion stub.

    `responses` maps a substring of the system prompt to the reply to return,
    so individual prompts (topic-change, summary, extract, compact) can be
    controlled independently. Falls back to `default`.
    """

    def __init__(self, responses: dict[str, str] | None = None, default: str = ""):
        self.responses = responses or {}
        self.default = default
        self.calls: list[tuple[list[dict], str]] = []

    async def __call__(self, messages, system: str = "") -> str:
        self.calls.append((messages, system))
        for needle, reply in self.responses.items():
            if needle.lower() in (system or "").lower():
                return reply
        return self.default


@pytest.fixture
def workspace(tmp_path):
    return tmp_path / "embodied-agent"


# --------------------------------------------------------------------------- #
# Contextual layer
# --------------------------------------------------------------------------- #
async def test_records_context_turns_and_actions(workspace):
    mm = MemoryManager(workspace_dir=workspace)
    mm.add_user_message("hi")
    mm.record_action("moved to calendar")
    mm.add_assistant_message("hello!")

    rendered = mm.render_context()
    assert "user: hi" in rendered
    assert "[action] moved to calendar" in rendered
    assert "assistant: hello!" in rendered


async def test_empty_messages_are_ignored(workspace):
    mm = MemoryManager(workspace_dir=workspace)
    mm.add_user_message("   ")
    mm.add_assistant_message("")
    assert mm.context == []


# --------------------------------------------------------------------------- #
# Contextual -> Daily compaction
# --------------------------------------------------------------------------- #
async def test_topic_change_triggers_compaction_to_daily(workspace):
    llm = FakeLLM(
        responses={
            "decide whether a conversation has changed topic": "YES",
            "compress a short conversation": "- user greeted the agent",
        }
    )
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)
    mm.add_user_message("hello there")
    mm.add_assistant_message("hi!")

    compacted = await mm.maybe_compact_context("totally different question about taxes")

    assert compacted is True
    assert mm.context == []  # context cleared after compaction
    daily = (workspace / "memory" / f"{date.today().isoformat()}.md").read_text("utf-8")
    assert "user greeted the agent" in daily


async def test_same_topic_does_not_compact(workspace):
    llm = FakeLLM(responses={"decide whether a conversation has changed topic": "NO"})
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)
    mm.add_user_message("tell me about cats")
    mm.add_assistant_message("cats are great")

    compacted = await mm.maybe_compact_context("tell me more about cats")

    assert compacted is False
    assert len(mm.context) == 2


async def test_buffer_overflow_forces_compaction_without_llm(workspace):
    # No LLM -> heuristic summary; compaction still triggers on size.
    mm = MemoryManager(workspace_dir=workspace, max_context_entries=4)
    for i in range(4):
        mm.add_user_message(f"message {i}")

    compacted = await mm.maybe_compact_context("another message")

    assert compacted is True
    assert mm.context == []
    daily = (workspace / "memory" / f"{date.today().isoformat()}.md").read_text("utf-8")
    assert "message 0" in daily


async def test_force_compact_context_at_session_end(workspace):
    mm = MemoryManager(workspace_dir=workspace)
    mm.add_user_message("remember to water plants")
    assert await mm.compact_context_to_daily() is True
    assert await mm.compact_context_to_daily() is False  # nothing left


# --------------------------------------------------------------------------- #
# Daily -> Long-term promotion
# --------------------------------------------------------------------------- #
async def test_promote_daily_to_longterm(workspace):
    llm = FakeLLM(
        responses={"extract only durable, important facts": "- User likes tea"}
    )
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)

    yesterday = mm._yesterday()
    daily_path = mm._daily_path(yesterday)
    daily_path.write_text(
        mm._daily_header(yesterday) + "## 10:00\n- chatted about tea\n", "utf-8"
    )

    promoted = await mm.compact_daily_to_longterm(yesterday)

    assert promoted is True
    longterm = (workspace / "MEMORY.md").read_text("utf-8")
    assert "User likes tea" in longterm
    # Re-running is a no-op thanks to the promoted marker.
    assert await mm.compact_daily_to_longterm(yesterday) is False


async def test_user_facts_routed_to_user_md(workspace):
    llm = FakeLLM(
        responses={
            "about the user": "- User prefers dark mode\n- User's name is Sam",
        }
    )
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)

    day = mm._yesterday()
    mm._daily_path(day).write_text(
        mm._daily_header(day) + "## 10:00\n- talked about UI\n", "utf-8"
    )

    await mm.compact_daily_to_longterm(day)

    user_md = mm._workspace.user_file.read_text("utf-8")
    assert "## Learned about the user" in user_md
    assert "prefers dark mode" in user_md
    assert "name is Sam" in user_md


async def test_user_facts_deduplicated(workspace):
    llm = FakeLLM(
        responses={"about the user": "- user prefers DARK mode\n- User likes tea"}
    )
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)
    # USER.md already knows one of the facts (different casing/bullet).
    mm._workspace.user_file.write_text(
        "# USER.md\n\n## Learned about the user\n- User prefers dark mode\n", "utf-8"
    )

    day = mm._yesterday()
    mm._daily_path(day).write_text(
        mm._daily_header(day) + "## 10:00\n- stuff\n", "utf-8"
    )
    await mm.compact_daily_to_longterm(day)

    user_md = mm._workspace.user_file.read_text("utf-8")
    assert user_md.lower().count("dark mode") == 1  # not duplicated
    assert "User likes tea" in user_md  # new fact added


def test_user_fact_wording_variants_are_deduplicated(workspace):
    mm = MemoryManager(workspace_dir=workspace)
    mm._workspace.user_file.write_text(
        "# USER.md\n\n"
        "- Name: Mei\n"
        "- Preferred name: Mei\n"
        "- Mei refers to the writing desk as the planning desk.\n",
        "utf-8",
    )

    added = mm._append_user_facts(
        "- User's preferred name is Mei\n"
        "- User calls the writing desk the planning desk\n"
    )

    assert added == 0


async def test_user_facts_skipped_when_none(workspace):
    llm = FakeLLM(responses={"about the user": "NONE"})
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)
    day = mm._yesterday()
    mm._daily_path(day).write_text(
        mm._daily_header(day) + "## 10:00\n- small talk\n", "utf-8"
    )
    await mm.compact_daily_to_longterm(day)

    user_md = mm._workspace.user_file.read_text("utf-8")
    assert "## Learned about the user" not in user_md


async def test_startup_compaction_promotes_past_days_only(workspace):
    from datetime import timedelta

    llm = FakeLLM(responses={"extract only durable, important facts": "- durable fact"})
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)

    today = date.today()
    two_days_ago = today - timedelta(days=2)
    one_day_ago = today - timedelta(days=1)

    for day in (two_days_ago, one_day_ago):
        mm._daily_path(day).write_text(
            mm._daily_header(day) + f"## 09:00\n- stuff on {day}\n", "utf-8"
        )
    # Today's file must NOT be promoted (still being written).
    mm._daily_path(today).write_text(
        mm._daily_header(today) + "## 09:00\n- today stuff\n", "utf-8"
    )

    promoted = await mm.startup_compaction()

    assert promoted == 2
    today_text = mm._daily_path(today).read_text("utf-8")
    assert "promoted-to-longterm" not in today_text
    # Running again is a no-op (everything past is already marked).
    assert await mm.startup_compaction() == 0


async def test_promote_skips_when_nothing_important(workspace):
    llm = FakeLLM(responses={"extract only durable, important facts": "NONE"})
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace)
    day = mm._yesterday()
    mm._daily_path(day).write_text(mm._daily_header(day) + "small talk\n", "utf-8")

    assert await mm.compact_daily_to_longterm(day) is False
    assert not (workspace / "MEMORY.md").exists() or "small talk" not in (
        workspace / "MEMORY.md"
    ).read_text("utf-8")


# --------------------------------------------------------------------------- #
# Long-term compaction
# --------------------------------------------------------------------------- #
async def test_longterm_compaction_when_oversized(workspace):
    llm = FakeLLM(
        responses={"rewrite the following long-term memory": "# compact\n- key fact"}
    )
    mm = MemoryManager(llm_complete=llm, workspace_dir=workspace, max_longterm_chars=50)
    mm._workspace.memory_file.write_text("x" * 200, "utf-8")

    assert await mm.compact_longterm_if_needed() is True
    assert (
        mm._workspace.memory_file.read_text("utf-8").strip() == "# compact\n- key fact"
    )


async def test_longterm_compaction_noop_when_small(workspace):
    llm = FakeLLM()
    mm = MemoryManager(
        llm_complete=llm, workspace_dir=workspace, max_longterm_chars=10000
    )
    mm._workspace.memory_file.write_text("short", "utf-8")
    assert await mm.compact_longterm_if_needed() is False


# --------------------------------------------------------------------------- #
# Context assembly for prompts
# --------------------------------------------------------------------------- #
async def test_build_memory_context_combines_today_and_current_context(workspace):
    mm = MemoryManager(workspace_dir=workspace)
    mm._workspace.memory_file.write_text("- long term fact", "utf-8")
    today = mm._daily_path(date.today())
    today.write_text(mm._daily_header(date.today()) + "- today entry\n", "utf-8")
    yesterday = date.fromordinal(date.today().toordinal() - 1)
    mm._daily_path(yesterday).write_text(
        mm._daily_header(yesterday) + "- yesterday entry\n", "utf-8"
    )
    mm.add_user_message("current question")

    block = mm.build_memory_context()

    assert "Long-term memory" not in block and "long term fact" not in block
    assert "Today's memory" in block and "today entry" in block
    assert "yesterday entry" not in block
    assert "Current conversation context" in block and "current question" in block
