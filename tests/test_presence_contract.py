from src.open_llm_vtuber.autonomy import AutonomySettings, PresenceLedger


def test_presence_contract_blocks_user_facing_work_in_focus_mode(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    allowed, reason = ledger.can_afford(1.0, user_facing=True, focus_mode=True)
    assert not allowed
    assert reason == "focus mode is active"


def test_presence_contract_does_not_block_silent_work_in_focus_mode(tmp_path):
    ledger = PresenceLedger.load(tmp_path, AutonomySettings())
    allowed, _ = ledger.can_afford(0.25, user_facing=False, focus_mode=True)
    assert allowed
