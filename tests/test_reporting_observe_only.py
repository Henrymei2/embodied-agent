from __future__ import annotations

import pytest

import src.open_llm_vtuber.autonomy.reporting as reporting
from src.open_llm_vtuber.autonomy.agenda import create_entry
from src.open_llm_vtuber.autonomy.observation import Observation
from src.open_llm_vtuber.autonomy.presence import PresenceLedger
from src.open_llm_vtuber.autonomy.reporting import AgendaReporter
from src.open_llm_vtuber.autonomy.settings import AutonomySettings


def _entry(**overrides):
    values = {
        "kind": "pursuit",
        "title": "Archive one old read email",
        "description": "Call archive_email on a verified candidate.",
        "origin": "autonomous",
        "evidence_refs": ["memory:MEMORY.md"],
        "completion_condition": "One email is archived.",
        "time_budget_seconds": 120,
        "status": "completed",
        "report_policy": "on_completion",
    }
    values.update(overrides)
    return create_entry(**values)


async def _deliver(ledger, *, entry=None, run_report=None):
    calls: list[tuple[str, str]] = []
    emitted: list[dict] = []

    async def emit(payload: dict) -> None:
        emitted.append(payload)

    async def default_run_report(_entry, summary: str) -> None:
        calls.append((_entry.id, summary))

    reporter = AgendaReporter(
        client_uid="client",
        presence=ledger,
        emit=emit,
        run_report=run_report or default_run_report,
    )
    outcome = await reporter.deliver(
        entry or _entry(), "Archived the email successfully."
    )
    return outcome, calls, emitted


@pytest.mark.asyncio
async def test_observe_only_delivers_speak_in_focus_mode(monkeypatch, tmp_path):
    ledger = PresenceLedger.load(
        tmp_path, AutonomySettings(presence_mode="observe_only")
    )
    monkeypatch.setattr(
        reporting,
        "build_observation",
        lambda _uid: Observation(focus_mode=True, presence_fresh=True),
    )

    outcome, calls, emitted = await _deliver(ledger)

    assert outcome.delivered is True
    assert outcome.counts_as_failure is False
    assert len(calls) == 1
    receipt = ledger.receipts[-1]
    assert receipt.status == "succeeded"
    assert receipt.override_reason == "focus mode is active"
    assert any(item["type"] == "autonomy-receipt" for item in emitted)


@pytest.mark.asyncio
async def test_observe_only_delivers_with_stale_presence(monkeypatch, tmp_path):
    ledger = PresenceLedger.load(
        tmp_path, AutonomySettings(presence_mode="observe_only")
    )
    monkeypatch.setattr(
        reporting,
        "build_observation",
        lambda _uid: Observation(user_present=False, presence_fresh=False),
    )

    outcome, calls, _emitted = await _deliver(ledger)

    assert outcome.delivered is True
    assert len(calls) == 1
    assert ledger.receipts[-1].override_reason == "presence signal is stale"


@pytest.mark.asyncio
async def test_observe_only_delivers_when_budget_exhausted(monkeypatch, tmp_path):
    ledger = PresenceLedger.load(
        tmp_path,
        AutonomySettings(
            presence_mode="observe_only",
            attention_budget=1.0,
            attention_refill_per_hour=0.0,
        ),
    )
    monkeypatch.setattr(
        reporting,
        "build_observation",
        lambda _uid: Observation(user_present=True, presence_fresh=True),
    )

    outcome, calls, _emitted = await _deliver(ledger)

    assert outcome.delivered is True
    assert len(calls) == 1
    assert ledger.receipts[-1].override_reason == "attention budget exhausted"


@pytest.mark.asyncio
async def test_enforced_focus_mode_defers_without_failure(monkeypatch, tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    monkeypatch.setattr(
        reporting,
        "build_observation",
        lambda _uid: Observation(focus_mode=True, presence_fresh=True),
    )

    outcome, calls, emitted = await _deliver(ledger)

    assert outcome.delivered is False
    assert outcome.counts_as_failure is False
    assert outcome.reason == "focus mode is active"
    assert calls == []
    assert ledger.receipts[-1].status == "canceled"
    assert any(item["type"] == "autonomy-receipt" for item in emitted)


@pytest.mark.asyncio
async def test_silent_policy_never_invokes_run_report(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())

    outcome, calls, _emitted = await _deliver(
        ledger, entry=_entry(report_policy="silent")
    )

    assert outcome.delivered is True
    assert calls == []
    assert ledger.receipts[-1].status == "succeeded"
