import json
from pathlib import Path

import pytest

from src.open_llm_vtuber.evaluation.part4 import (
    load_part4_fixture,
    run_part4_repair_evaluation,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "research" / "fixtures" / "held_out" / "part4-repair.json"
BASELINE = ROOT / "research" / "baselines" / "part4-plan-repair.json"


@pytest.mark.asyncio
async def test_frozen_part4_repair_report_reproduces_committed_baseline():
    report = await run_part4_repair_evaluation(FIXTURE)
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert report == expected
    assert all(report["acceptance"].values())
    assert report["conditions"]["repair_on"]["success_count"] == 5
    assert report["conditions"]["repair_off"]["success_count"] == 2


def test_part4_fixture_rejects_schema_drift(tmp_path):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["scenarios"][0]["private_prompt"] = "must never be accepted"
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid keys"):
        load_part4_fixture(path)


def test_part4_fixture_rejects_duplicate_scenarios(tmp_path):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["scenarios"].append(dict(payload["scenarios"][0]))
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_part4_fixture(path)
