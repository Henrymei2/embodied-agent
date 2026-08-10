from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.autonomy import welcome_back
from src.open_llm_vtuber.autonomy.observation import Observation
from src.open_llm_vtuber.autonomy.presence import PresenceLedger
from src.open_llm_vtuber.autonomy.settings import AutonomySettings
from src.open_llm_vtuber.conversations.types import AgentTurnResult


@pytest.fixture(autouse=True)
def clear_welcome_sessions():
    welcome_back._sessions.clear()
    yield
    welcome_back._sessions.clear()


class FakeQueue:
    def __init__(self):
        self.items = []
        self.canceled = []

    async def enqueue(self, **kwargs):
        self.items.append(kwargs)
        return True

    def cancel_work(self, key):
        self.canceled.append(key)
        return True


async def _send(_payload: str) -> None:
    return None


async def test_first_visible_return_waits_for_history(monkeypatch, tmp_path):
    settings = AutonomySettings()
    ledger = PresenceLedger.load(tmp_path, settings)
    queue = FakeQueue()
    context = SimpleNamespace(history_uid=None)
    monkeypatch.setattr(welcome_back, "get_presence_ledger", lambda _uid: ledger)
    monkeypatch.setattr(welcome_back, "get_queue", lambda _uid: queue)
    monkeypatch.setattr(
        welcome_back,
        "build_observation",
        lambda _uid: Observation(user_present=True, presence_fresh=True),
    )

    welcome_back.register_welcome_session("client", settings)
    await welcome_back.observe_welcome_presence(
        "client", user_present=True, context=context, send=_send
    )
    assert queue.items == []

    context.history_uid = "history"
    assert await welcome_back.maybe_queue_welcome("client", context=context, send=_send)
    assert queue.items[0]["dedupe_key"] == "welcome-back-greeting"
    assert queue.items[0]["work_class"] == "autonomous"


async def test_enforced_focus_blocks_but_observe_only_queues(monkeypatch, tmp_path):
    queue = FakeQueue()
    context = SimpleNamespace(history_uid="history")
    monkeypatch.setattr(welcome_back, "get_queue", lambda _uid: queue)
    monkeypatch.setattr(
        welcome_back,
        "build_observation",
        lambda _uid: Observation(
            user_present=True, presence_fresh=True, focus_mode=True
        ),
    )

    enforced = PresenceLedger.load(tmp_path / "enforced", AutonomySettings())
    monkeypatch.setattr(welcome_back, "get_presence_ledger", lambda _uid: enforced)
    welcome_back.register_welcome_session("enforced", AutonomySettings())
    welcome_back._sessions["enforced"].eligible_return = True
    assert not await welcome_back.maybe_queue_welcome(
        "enforced", context=context, send=_send
    )

    observed_settings = AutonomySettings(presence_mode="observe_only")
    observed = PresenceLedger.load(tmp_path / "observed", observed_settings)
    monkeypatch.setattr(welcome_back, "get_presence_ledger", lambda _uid: observed)
    welcome_back.register_welcome_session("observed", observed_settings)
    welcome_back._sessions["observed"].eligible_return = True
    assert await welcome_back.maybe_queue_welcome(
        "observed", context=context, send=_send
    )


@pytest.mark.parametrize(("away_minutes", "expected"), [(10, False), (31, True)])
async def test_only_a_meaningful_absence_queues_again(
    monkeypatch, tmp_path, away_minutes, expected
):
    settings = AutonomySettings()
    ledger = PresenceLedger.load(tmp_path, settings)
    now = datetime.now(timezone.utc)
    ledger.mark_welcome_back_complete(now=now - timedelta(days=1))
    ledger.user_away_since = (now - timedelta(minutes=away_minutes)).isoformat()
    ledger.last_user_seen_at = ledger.user_away_since
    ledger.save()
    queue = FakeQueue()
    monkeypatch.setattr(welcome_back, "get_presence_ledger", lambda _uid: ledger)
    monkeypatch.setattr(welcome_back, "get_queue", lambda _uid: queue)
    monkeypatch.setattr(
        welcome_back,
        "build_observation",
        lambda _uid: Observation(user_present=True, presence_fresh=True),
    )
    welcome_back.register_welcome_session("client", settings)

    await welcome_back.observe_welcome_presence(
        "client",
        user_present=True,
        context=SimpleNamespace(history_uid="history"),
        send=_send,
    )

    assert bool(queue.items) is expected


async def test_continuous_presence_does_not_become_a_return(monkeypatch, tmp_path):
    settings = AutonomySettings()
    ledger = PresenceLedger.load(tmp_path, settings)
    ledger.mark_welcome_back_complete()
    queue = FakeQueue()
    monkeypatch.setattr(welcome_back, "get_presence_ledger", lambda _uid: ledger)
    monkeypatch.setattr(welcome_back, "get_queue", lambda _uid: queue)
    monkeypatch.setattr(
        welcome_back,
        "build_observation",
        lambda _uid: Observation(user_present=True, presence_fresh=True),
    )
    context = SimpleNamespace(history_uid="history")
    welcome_back.register_welcome_session("client", settings)
    await welcome_back.observe_welcome_presence(
        "client", user_present=True, context=context, send=_send
    )
    ledger.last_user_seen_at = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat()
    await welcome_back.observe_welcome_presence(
        "client", user_present=True, context=context, send=_send
    )

    assert queue.items == []


async def test_successful_welcome_spends_ask_budget_and_marks_return(
    monkeypatch, tmp_path
):
    settings = AutonomySettings()
    ledger = PresenceLedger.load(tmp_path, settings)
    sent = []
    captured = {}
    monkeypatch.setattr(welcome_back, "get_presence_ledger", lambda _uid: ledger)
    monkeypatch.setattr(
        welcome_back,
        "build_observation",
        lambda _uid: Observation(user_present=True, presence_fresh=True),
    )
    monkeypatch.setattr(
        welcome_back,
        "_agenda_context_for",
        lambda _uid, _context: SimpleNamespace(payload={"memory_passages": []}),
    )

    async def conversation(**kwargs):
        captured.update(kwargs)
        return AgentTurnResult(True, "Welcome back—how are you feeling?", None, True)

    async def send(payload):
        sent.append(payload)

    monkeypatch.setattr(welcome_back, "process_single_conversation", conversation)
    welcome_back.register_welcome_session("client", settings)
    welcome_back._sessions["client"].eligible_return = True

    await welcome_back._run_welcome("client", context=SimpleNamespace(), send=send)

    assert captured["metadata"]["synthetic_user"] is True
    assert ledger.receipts[-1].action == "ask"
    assert ledger.receipts[-1].status == "succeeded"
    assert ledger.budget_balance == pytest.approx(4.75)
    assert ledger.has_welcomed_user is True
    assert sent


async def test_failed_welcome_retries_once_and_refunds(monkeypatch, tmp_path):
    settings = AutonomySettings()
    ledger = PresenceLedger.load(tmp_path, settings)
    monkeypatch.setattr(welcome_back, "get_presence_ledger", lambda _uid: ledger)
    monkeypatch.setattr(
        welcome_back,
        "build_observation",
        lambda _uid: Observation(user_present=True, presence_fresh=True),
    )
    monkeypatch.setattr(
        welcome_back,
        "_agenda_context_for",
        lambda _uid, _context: SimpleNamespace(payload={}),
    )

    async def failed(**_kwargs):
        return AgentTurnResult(False, "", "model unavailable", True)

    monkeypatch.setattr(welcome_back, "process_single_conversation", failed)
    welcome_back.register_welcome_session("client", settings)
    welcome_back._sessions["client"].eligible_return = True

    await welcome_back._run_welcome("client", context=SimpleNamespace(), send=_send)

    assert [receipt.status for receipt in ledger.receipts] == ["failed", "failed"]
    assert ledger.budget_balance == pytest.approx(settings.attention_budget)
    assert welcome_back._sessions["client"].handled_return is True
