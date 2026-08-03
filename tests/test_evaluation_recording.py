from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.evaluation import (
    EvidenceRecorder,
    EvidenceValidationError,
    build_run_manifest,
    load_evidence_bundle,
    load_research_contract,
    project_autonomy_event,
    project_study_event,
)

_START = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


def running_manifest() -> dict:
    contract = load_research_contract()
    return build_run_manifest(
        run_id="recording-run",
        code_revision="a" * 40,
        worktree_dirty=True,
        contract_revision=contract["contract_revision"],
        condition_id="full",
        scenario_set_revision="recording-test-v1",
        scenario_split="development",
        fixture_digest="b" * 64,
        configuration_digest="c" * 64,
        model_identifiers=["fixture-model-v1"],
        engine_identifiers=["fixture-engine-v1"],
        platform={
            "system": "Windows",
            "release": "11",
            "machine": "AMD64",
            "python": "3.11",
        },
        seed=0,
        started_at=_START,
    )


def test_recorder_durably_writes_and_reloads_a_terminal_hash_chain(tmp_path):
    times = iter(
        [_START, _START + timedelta(milliseconds=1), _START + timedelta(seconds=1)]
    )
    identifiers = iter(["event-a", "event-b"])
    recorder = EvidenceRecorder(
        tmp_path / "run",
        running_manifest(),
        clock=lambda: next(times),
        id_factory=lambda: next(identifiers),
    )

    first = recorder.append(
        category="observation",
        event_type="fixture_seen",
        correlation_id="scenario-1",
        payload={"fixture_id": "fixture-1"},
    )
    second = recorder.append(
        category="outcome",
        event_type="fixture_verified",
        correlation_id="scenario-1",
        causation_id=first["event_id"],
        payload={"status": "verified"},
    )
    recorder.finish(status="completed")

    manifest, events = load_evidence_bundle(tmp_path / "run")
    assert manifest["status"] == "completed"
    assert [event["event_id"] for event in events] == ["event-a", "event-b"]
    assert second["previous_event_hash"] == first["event_hash"]


def test_recorder_will_not_overwrite_or_append_after_finalization(tmp_path):
    root = tmp_path / "run"
    recorder = EvidenceRecorder(root, running_manifest())
    recorder.finish(status="failed", finished_at=_START + timedelta(seconds=1))

    with pytest.raises(EvidenceValidationError, match="already finalized"):
        recorder.finish(status="failed", finished_at=_START + timedelta(seconds=2))
    with pytest.raises(EvidenceValidationError, match="finalized"):
        recorder.append(
            category="system",
            event_type="too_late",
            correlation_id="scenario-1",
            payload={},
        )
    with pytest.raises(EvidenceValidationError, match="will not be overwritten"):
        EvidenceRecorder(root, running_manifest())


def test_recorder_enforces_a_finite_event_budget(tmp_path):
    recorder = EvidenceRecorder(tmp_path / "run", running_manifest(), max_events=1)
    recorder.append(
        category="system",
        event_type="first",
        correlation_id="scenario-1",
        payload={},
    )

    with pytest.raises(EvidenceValidationError, match="budget exhausted"):
        recorder.append(
            category="system",
            event_type="second",
            correlation_id="scenario-1",
            payload={},
        )


def test_truncated_or_tampered_recordings_fail_closed(tmp_path):
    root = tmp_path / "run"
    recorder = EvidenceRecorder(root, running_manifest())
    recorder.append(
        category="system",
        event_type="valid",
        correlation_id="scenario-1",
        payload={},
    )
    recorder.finish(status="completed", finished_at=_START + timedelta(seconds=1))
    with (root / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("{truncated")

    with pytest.raises(EvidenceValidationError, match="truncated evidence"):
        load_evidence_bundle(root)


def test_study_projection_is_an_allowlist_not_a_short_string_filter():
    projection = project_study_event(
        {
            "ts": "2026-08-03T12:00:00+00:00",
            "session": "session-1",
            "condition": "full",
            "event": "turn_started",
            "source": "text",
            "transcript": "short-secret",
            "oops": "another-secret",
        }
    )

    assert projection.payload == {"condition": "full", "source": "text"}
    assert "secret" not in json.dumps(projection.payload)


def test_autonomy_projection_excludes_content_and_preserves_operational_ids():
    projection = project_autonomy_event(
        entry_id="entry-1",
        kind="transition",
        trace_id="trace-1",
        data={
            "status": "completed",
            "attempt": 2,
            "title": "private task title",
            "summary": "private summary",
            "error": "private failure text",
        },
    )

    assert projection.category == "plan"
    assert projection.payload == {
        "attempt": 2,
        "status": "completed",
        "entry_id": "entry-1",
    }
    assert "private" not in json.dumps(projection.payload)
