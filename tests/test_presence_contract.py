from src.open_llm_vtuber.autonomy import AutonomySettings, PresenceLedger
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
    allowed, reason = ledger.start_action(
        receipt.receipt_id, observation=observation
    )

    assert allowed
    assert reason == "started"
    assert receipt.override_reason == "focus mode is active"
    assert ledger.snapshot()["mode"] == "observe_only"
