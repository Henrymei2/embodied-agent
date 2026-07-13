"""Tests for consent-aware autonomy budgeting, receipts, and feedback."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.autonomy import (
    ACT_NOTHING,
    AutonomyPlanner,
    AutonomySettings,
    AutonomyState,
    Observation,
    PresenceLedger,
)
from src.open_llm_vtuber.autonomy.observation import (
    clear_client_presence,
    get_client_presence,
    mark_user_activity,
    update_client_presence,
)
from src.open_llm_vtuber.autonomy.presence import (
    get_or_create_presence_ledger,
    unregister_presence_ledger,
)
from src.open_llm_vtuber.websocket_handler import WebSocketHandler


class ActRng:
    """Avoid exploration and always pass the planner's rest gate."""

    def __init__(self):
        self.values = iter([0.99, 0.0])

    def random(self):
        return next(self.values)

    def randrange(self, _n):
        return 0


def _observation(**overrides):
    values = {
        "idle_seconds": 1800,
        "user_present": True,
        "seconds_since_last_spoke": 5000,
        "hour_of_day": 14,
        "focus_mode": False,
    }
    values.update(overrides)
    return Observation(**values)


def _record(ledger, direction="care", action="ask", now=None):
    return ledger.record_action(
        direction=direction,
        action=action,
        score=0.81,
        reason=f"acting on {direction} via {action}",
        observation=_observation(),
        now=now,
    )


def test_attention_budget_refills_over_wall_time(tmp_path):
    settings = AutonomySettings(
        attention_budget=6,
        attention_refill_per_hour=1.5,
    )
    ledger = PresenceLedger.load(tmp_path, settings)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ledger.budget_balance = 0
    ledger.last_refill_at = start.isoformat()

    assert ledger.refill(start + timedelta(hours=2)) == pytest.approx(3.0)
    assert ledger.refill(start + timedelta(hours=20)) == 6


def test_receipt_spends_budget_and_only_keeps_coarse_signals(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    before = ledger.budget_balance

    receipt = _record(ledger)

    assert ledger.budget_balance == pytest.approx(before - 1.25)
    assert receipt.attention_cost == 1.25
    assert receipt.signals == {
        "user_present": True,
        "activity": "away",
        "focus_mode": False,
        "quiet_minutes": 83.3,
        "hour_of_day": 14,
    }
    assert set(receipt.signals) == {
        "user_present",
        "activity",
        "focus_mode",
        "quiet_minutes",
        "hour_of_day",
    }


def test_helpful_feedback_learns_and_persists_direction_affinity(tmp_path):
    settings = AutonomySettings(attention_refill_per_hour=0)
    ledger = PresenceLedger.load(tmp_path, settings)
    receipt = _record(ledger, direction="care")

    ok, message = ledger.apply_feedback(receipt.receipt_id, "helpful")

    assert ok is True and message == "feedback recorded"
    assert ledger.affinity("care") == pytest.approx(0.15)
    reloaded = PresenceLedger.load(tmp_path, settings)
    assert reloaded.affinity("care") == pytest.approx(0.15)
    assert reloaded.receipts[-1].feedback == "helpful"


def test_feedback_is_idempotent_and_cannot_double_train(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    receipt = _record(ledger)
    assert ledger.apply_feedback(receipt.receipt_id, "dismissed")[0] is True
    first_affinity = ledger.affinity("care")

    ok, message = ledger.apply_feedback(receipt.receipt_id, "dismissed")

    assert ok is False
    assert "already recorded" in message
    assert ledger.affinity("care") == first_affinity


def test_snooze_blocks_speech_but_allows_quiet_background_work(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    now = datetime.now(timezone.utc)
    receipt = _record(ledger, action="speak", now=now)
    assert ledger.apply_feedback(
        receipt.receipt_id, "snoozed", snooze_minutes=30, now=now
    )[0]

    spoken = ledger.can_afford(2.0, user_facing=True, now=now)
    silent = ledger.can_afford(0.25, user_facing=False, now=now)

    assert spoken == (False, "proactive speech is snoozed")
    assert silent[0] is True


def test_focus_mode_is_a_hard_gate_for_user_facing_actions(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())

    assert ledger.can_afford(1.25, user_facing=True, focus_mode=True) == (
        False,
        "focus mode is active",
    )
    assert ledger.can_afford(0.25, user_facing=False, focus_mode=True)[0]


def test_planner_stops_when_attention_budget_is_empty(tmp_path):
    settings = AutonomySettings(
        confidence_threshold="loose",
        attention_refill_per_hour=0,
    )
    ledger = PresenceLedger.load(tmp_path, settings)
    ledger.budget_balance = 0
    state = AutonomyState.load(tmp_path / "autonomy")

    decision = AutonomyPlanner().propose_and_choose(
        _observation(), state, settings, ActRng(), presence=ledger
    )

    assert decision.action == ACT_NOTHING
    assert decision.reason == "attention budget exhausted"
    assert decision.attention_cost > 0


def test_receipt_retention_is_bounded(tmp_path):
    settings = AutonomySettings(
        attention_budget=20,
        attention_refill_per_hour=0,
        receipt_retention=5,
    )
    ledger = PresenceLedger.load(tmp_path, settings)
    for _ in range(8):
        _record(ledger, direction="growth", action="silent")

    snapshot = ledger.snapshot()
    assert len(ledger.receipts) == 5
    assert len(snapshot["receipts"]) == 5


def test_live_presence_signal_validation_and_activity_tracking():
    client_uid = "presence-test-client"
    try:
        signal = update_client_presence(
            client_uid,
            {"idle_seconds": 400, "user_present": True, "focus_mode": True},
        )
        assert signal.idle_seconds == 400
        assert get_client_presence(client_uid).focus_mode is True

        mark_user_activity(client_uid)
        active = get_client_presence(client_uid)
        assert active.idle_seconds == 0
        assert active.focus_mode is True

        with pytest.raises(ValueError, match="idle_seconds"):
            update_client_presence(client_uid, {"idle_seconds": "a while"})
    finally:
        clear_client_presence(client_uid)


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))


async def test_websocket_feedback_returns_result_and_updated_state(tmp_path):
    client_uid = "presence-websocket-client"
    websocket = FakeWebSocket()
    handler = WebSocketHandler(default_context_cache=None)
    ledger = get_or_create_presence_ledger(
        client_uid, tmp_path, AutonomySettings(attention_refill_per_hour=0)
    )
    receipt = _record(ledger, direction="growth", action="silent")

    try:
        await handler._handle_autonomy_feedback(
            websocket,
            client_uid,
            {
                "type": "autonomy-feedback",
                "receipt_id": receipt.receipt_id,
                "feedback": "helpful",
            },
        )

        assert websocket.messages[0] == {
            "type": "autonomy-feedback-result",
            "ok": True,
            "receipt_id": receipt.receipt_id,
            "message": "feedback recorded",
        }
        assert websocket.messages[1]["type"] == "presence-state"
        assert websocket.messages[1]["enabled"] is True
        assert websocket.messages[1]["direction_affinity"]["growth"] == 0.15
    finally:
        unregister_presence_ledger(client_uid)


async def test_presence_context_reports_disabled_contract():
    client_uid = "presence-disabled-client"
    websocket = FakeWebSocket()
    handler = WebSocketHandler(default_context_cache=None)
    try:
        await handler._handle_presence_context(
            websocket,
            client_uid,
            {
                "type": "presence-context",
                "idle_seconds": 10,
                "user_present": True,
                "focus_mode": True,
            },
        )
        assert websocket.messages == [
            {"type": "presence-state", "enabled": False, "receipts": []}
        ]
    finally:
        clear_client_presence(client_uid)


async def test_invalid_presence_context_is_rejected_without_mutating_state():
    client_uid = "presence-invalid-client"
    websocket = FakeWebSocket()
    handler = WebSocketHandler(default_context_cache=None)
    try:
        await handler._handle_presence_context(
            websocket,
            client_uid,
            {
                "type": "presence-context",
                "idle_seconds": "not-a-number",
                "user_present": True,
            },
        )
        assert websocket.messages == [
            {"type": "error", "message": "idle_seconds must be a number or null"}
        ]
        assert get_client_presence(client_uid).idle_seconds is None
    finally:
        clear_client_presence(client_uid)
