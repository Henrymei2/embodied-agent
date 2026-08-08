import json
from pathlib import Path

import pytest

from scripts import export_agent_prompts as exporter


def test_latest_history_uses_newest_file_and_stops_at_last_human(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(exporter, "PROJECT_ROOT", tmp_path)
    history_dir = tmp_path / "chat_history" / "character-1"
    history_dir.mkdir(parents=True)
    older = history_dir / "older.json"
    older.write_text(
        json.dumps([{"role": "human", "content": "old"}]), encoding="utf-8"
    )
    newest = history_dir / "newest.json"
    newest.write_text(
        json.dumps(
            [
                {"role": "metadata"},
                {"role": "human", "content": "first"},
                {"role": "ai", "content": "answer"},
                {"role": "human", "content": "latest question"},
                {"role": "ai", "content": "must not be in reconstructed request"},
            ]
        ),
        encoding="utf-8",
    )
    older.touch()
    newest.touch()

    path, messages = exporter._latest_history("character-1")

    assert path == newest
    assert messages == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "latest question"},
    ]


def test_markdown_contains_all_request_families_and_native_shapes():
    keys = [
        "conversation",
        "agenda_idle",
        "agenda_intention",
        "agenda_next_step",
        "agenda_completion",
        "agenda_noteworthy",
        "agenda_reconsider",
        "memory_topic",
        "memory_summary",
        "memory_longterm",
        "memory_user",
        "memory_compaction",
    ]
    requests = []
    for key in keys:
        is_main = key == "conversation"
        requests.append(
            exporter.PromptRequest(
                key=key,
                title=key,
                active=True,
                trigger="test",
                source="test.py",
                system="system text" if is_main else "",
                messages=[{"role": "user", "content": "user prompt"}],
                tools=[{"type": "function"}] if is_main else None,
                segments=[(key, "test", "a repeated segment " * 10)],
            )
        )

    report = exporter._render(requests, {"network_calls": 0})

    for key in keys:
        assert f"## {key}" in report
    assert "### System" in report
    assert "### Messages" in report
    assert "### Tools" in report
    assert '"role": "user"' in report
    assert "## Duplication index" in report


def test_memory_prompt_constants_preserve_runtime_text():
    assert exporter.TOPIC_CHANGE_SYSTEM_PROMPT == (
        "You decide whether a conversation has changed topic. "
        "Answer with a single word: YES if the new user message starts a "
        "clearly different topic from the prior conversation, otherwise NO."
    )
    assert exporter.CONTEXT_SUMMARY_SYSTEM_PROMPT.endswith(
        "Be terse. Output only the bullets."
    )
    assert exporter.LONGTERM_EXTRACTION_SYSTEM_PROMPT.endswith(
        "single word NONE if nothing qualifies."
    )
    assert exporter.USER_FACT_EXTRACTION_SYSTEM_PROMPT.endswith("qualifies.")
    assert exporter.LONGTERM_COMPACTION_SYSTEM_PROMPT.endswith(
        "Output only the rewritten file content."
    )


def test_duplicate_index_reports_exact_and_similar_segments():
    repeated = "This is a sufficiently long prompt segment. " * 4
    requests = [
        exporter.PromptRequest(
            "one",
            "one",
            True,
            "test",
            "test",
            repeated,
            [],
            segments=[("first", "one", repeated)],
        ),
        exporter.PromptRequest(
            "two",
            "two",
            True,
            "test",
            "test",
            repeated,
            [],
            segments=[("second", "two", repeated)],
        ),
    ]

    exact, similar = exporter._duplicate_index(requests)

    assert exact == [f"- `one/first` = `two/second` ({len(repeated)} chars)"]
    assert similar == []
