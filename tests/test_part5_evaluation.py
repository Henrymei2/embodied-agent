import json
from pathlib import Path

import pytest

from src.open_llm_vtuber.evaluation.part5 import (
    load_part5_fixture,
    run_part5_authorization_evaluation,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "research" / "fixtures" / "held_out" / "part5-authorization.json"
BASELINE = ROOT / "research" / "baselines" / "part5-authorization.json"


@pytest.mark.asyncio
async def test_frozen_part5_report_reproduces_committed_baseline():
    report = await run_part5_authorization_evaluation(FIXTURE)
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert report == expected
    assert all(report["acceptance"].values())
    assert report["conditions"]["authorization_on"]["unauthorized_effect_count"] == 0


def test_part5_fixture_rejects_schema_drift(tmp_path):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["scenarios"][0]["untrusted_authority"] = True
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid keys"):
        load_part5_fixture(path)


def test_part5_fixture_requires_every_frozen_injection_source(tmp_path):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload["scenarios"].pop()
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="every frozen"):
        load_part5_fixture(path)
