from src.open_llm_vtuber.autonomy import AutonomySettings, PresenceLedger
from datetime import datetime, timedelta, timezone

from src.open_llm_vtuber.autonomy.observation import Observation


def test_presence_contract_blocks_user_facing_work_in_focus_mode(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    allowed, reason = ledger.can_afford(1.0, user_facing=True, focus_mode=True)
    assert not allowed
    assert reason == "focus mode is active"


def test_presence_contract_does_not_block_silent_work_in_focus_mode(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    allowed, _ = ledger.can_afford(0.25, user_facing=False, focus_mode=True)
    assert allowed


def test_observe_only_records_focus_override_without_blocking(tmp_path):
    ledger = PresenceLedger.load(
        tmp_path, AutonomySettings(presence_mode="observe_only")
    )
    observation = Observation(focus_mode=True, presence_fresh=True)

    receipt = ledger.queue_action(
        direction="care",
        action="speak",
        score=1.0,
        reason="Completed work",
        observation=observation,
    )
    allowed, reason = ledger.start_action(receipt.receipt_id, observation=observation)

    assert allowed
    assert reason == "started"
    assert receipt.override_reason == "focus mode is active"
    assert ledger.snapshot()["mode"] == "observe_only"


def test_presence_ledger_persists_return_timing(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    start = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)

    assert ledger.note_user_presence(True, now=start) is None
    ledger.note_user_presence(False, now=start + timedelta(minutes=1))
    absence = ledger.note_user_presence(True, now=start + timedelta(minutes=32))
    ledger.mark_welcome_back_complete(now=start + timedelta(minutes=32))

    assert absence == 31 * 60
    reloaded = PresenceLedger.load(tmp_path, AutonomySettings())
    assert reloaded.has_welcomed_user is True
    assert reloaded.user_away_since is None
