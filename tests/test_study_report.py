"""Reducing session logs to the table the study is actually read from.

The report is the last step before a conclusion, so its failure mode is the
expensive one: a number that looks like a finding but is an artifact of how
the logs were summarized.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "study_report.py"


def _load_report_module():
    spec = importlib.util.spec_from_file_location("study_report", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Registered before executing: @dataclass resolves annotations through
    # sys.modules, and fails on a module that is not there yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


report = _load_report_module()


def _write_log(directory: Path, name: str, events: list) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.jsonl"
    path.write_text(
        "\n".join(
            item if isinstance(item, str) else json.dumps(item) for item in events
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _event(session: str, condition: str, event: str, **data) -> dict:
    return {
        "ts": "2026-07-26T10:00:00.000+00:00",
        "session": session,
        "participant": "P01",
        "condition": condition,
        "event": event,
        **data,
    }


def test_nothing_measured_is_not_reported_as_zero(tmp_path):
    """The bug this pins: 0% recall read as "memory never worked"."""
    path = _write_log(tmp_path, "quiet", [_event("s1", "full", "session_start")])

    summary = report.summarize(path)

    assert summary is not None
    assert summary.recall_hit_rate is None
    assert summary.median_reply_wait is None
    assert summary.click_share is None
    assert summary.mean_top_score is None


def test_a_real_zero_is_still_a_zero(tmp_path):
    """Memory asked and finding nothing is a finding; it must not read as absent."""
    path = _write_log(
        tmp_path,
        "asked",
        [
            _event("s2", "full", "session_start"),
            _event("s2", "full", "turn_started", source="text"),
            _event("s2", "full", "memory_recall", enabled=True, documents=0),
            _event("s2", "full", "turn_finished", duration_seconds=2.0, reply_chars=40),
        ],
    )

    summary = report.summarize(path)

    assert summary.recalls == 1
    assert summary.recall_hit_rate == 0.0


def test_an_ablated_pillar_reports_no_measurement_rather_than_failure(tmp_path):
    """With memory off, recall cannot be scored - that is not a 0% hit rate."""
    path = _write_log(
        tmp_path,
        "ablated",
        [
            _event("s3", "no-memory", "session_start"),
            _event("s3", "no-memory", "turn_started", source="text"),
            _event("s3", "no-memory", "memory_recall", enabled=False, documents=0),
        ],
    )

    summary = report.summarize(path)

    assert summary.recalls == 0
    assert summary.recall_hit_rate is None


def test_a_torn_line_does_not_cost_the_session(tmp_path):
    """A log is appended to live, so its tail can be half-written."""
    path = _write_log(
        tmp_path,
        "torn",
        [
            _event("s4", "full", "session_start"),
            _event("s4", "full", "turn_started", source="object_click"),
            '{"ts": "2026-07-26T10:00:0',
        ],
    )

    summary = report.summarize(path)

    assert summary is not None
    assert summary.clicks == 1


def test_a_file_that_is_not_a_log_is_skipped(tmp_path):
    path = _write_log(tmp_path, "notes", ["shopping list", "milk"])

    assert report.summarize(path) is None


def test_the_agent_starting_a_turn_is_not_the_participant_replying(tmp_path):
    """Reply wait measures engagement, so only the participant's turns count."""
    path = _write_log(
        tmp_path,
        "mixed",
        [
            _event("s5", "full", "session_start"),
            _event(
                "s5",
                "full",
                "turn_started",
                source="autonomous",
                seconds_since_agent_finished=900.0,
            ),
            _event(
                "s5",
                "full",
                "turn_started",
                source="voice",
                seconds_since_agent_finished=4.0,
            ),
        ],
    )

    summary = report.summarize(path)

    assert summary.reply_waits == [4.0]
    assert summary.median_reply_wait == 4.0


def test_sessions_group_by_condition_for_the_comparison(tmp_path, capsys):
    paths = [
        _write_log(tmp_path, "a", [_event("s6", "full", "session_start")]),
        _write_log(tmp_path, "b", [_event("s7", "no-memory", "session_start")]),
    ]

    report.print_by_condition([report.summarize(path) for path in paths])

    printed = capsys.readouterr().out
    assert "full  (n=1)" in printed
    assert "no-memory  (n=1)" in printed
    # An unmeasured average must not print as a zero either. Match on the
    # value rather than the column padding, which is free to change.
    recall_lines = [line for line in printed.splitlines() if "recall hit rate" in line]
    assert recall_lines, printed
    assert all(line.rstrip().endswith("-%") for line in recall_lines), recall_lines


@pytest.mark.parametrize("value, expected", [(None, "-"), (0.0, "0.0"), (12, "12")])
def test_a_cell_tells_absent_from_zero(value, expected):
    assert report._cell(value) == expected
