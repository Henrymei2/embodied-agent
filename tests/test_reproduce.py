"""Part 10 slice 10.1: the one-command reproduction entry point."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "reproduce", ROOT / "scripts" / "reproduce.py"
)
reproduce = importlib.util.module_from_spec(SPEC)
# Registered before execution: `@dataclass` resolves annotations through
# `sys.modules[cls.__module__]`, which does not exist yet for a module loaded
# straight from a path.
sys.modules["reproduce"] = reproduce
SPEC.loader.exec_module(reproduce)


def test_the_three_outcomes_are_distinct():
    """Confusing 'unmeasured' with 'passing' is the failure this guards against."""
    assert len({reproduce.PASS, reproduce.FAIL, reproduce.UNAVAILABLE}) == 3


def test_a_check_that_raises_becomes_a_failure_not_a_crash():
    def explode():
        raise RuntimeError("boom")

    check = reproduce._run("probe", "a check that explodes", explode)
    assert check.state == reproduce.FAIL
    assert "RuntimeError" in check.detail
    assert check.seconds >= 0


def test_the_contract_and_suite_checks_pass_here():
    assert reproduce.check_research_contract()[0] == reproduce.PASS
    assert reproduce.check_scenario_suite()[0] == reproduce.PASS
    assert reproduce.check_held_out_is_frozen()[0] == reproduce.PASS


def test_the_secret_scan_can_fail_and_ignores_prose(tmp_path, monkeypatch):
    """It searched for bare "sk-" and fired on the word "risk-"."""
    state, detail = reproduce.check_published_artifacts_carry_no_secrets()
    assert state == reproduce.PASS, detail
    assert "scanned" in detail

    planted = tmp_path / "research"
    planted.mkdir()
    (planted / "leak.json").write_text(
        '{"token": "sk-' + "A" * 32 + '"}', encoding="utf-8"
    )
    (planted / "prose.md").write_text(
        "This is a risk-based approach; set client_secret in your config.",
        encoding="utf-8",
    )
    monkeypatch.setattr(reproduce, "ROOT", tmp_path)
    state, detail = reproduce.check_published_artifacts_carry_no_secrets()
    assert state == reproduce.FAIL
    assert "leak.json" in detail
    assert "prose.md" not in detail, "documentation must not trip the scanner"


def test_the_integration_check_detects_an_orphaned_component():
    """Part 6 shipped 1,546 lines the running server never reaches."""
    state, detail = reproduce.check_integration()
    assert state in (reproduce.PASS, reproduce.FAIL)
    if state == reproduce.FAIL:
        assert "not reachable from server.py" in detail


def test_the_latency_check_reads_an_export_not_this_process():
    """The first version read an in-process recorder and could never pass."""
    state, detail = reproduce.check_latency_budget()
    assert state in (reproduce.PASS, reproduce.FAIL, reproduce.UNAVAILABLE)
    if state == reproduce.UNAVAILABLE:
        assert "exported" in detail or "producer" in detail


def test_unmeasured_results_report_unavailable_with_a_reason():
    for check in (
        reproduce.check_benchmark_results,
        reproduce.check_study_analysis,
    ):
        state, detail = check()
        assert state == reproduce.UNAVAILABLE
        assert len(detail) > 20, "an unavailable check must say why"


def test_the_study_check_verifies_the_design_is_carryover_balanced():
    _, detail = reproduce.check_study_analysis()
    assert "carryover-balanced" in detail


def test_git_provenance_detects_untracked_research_code():
    """Part 11's defect must be visible from the reproduction command."""
    state, detail = reproduce.check_git_provenance()
    assert state in (reproduce.PASS, reproduce.FAIL, reproduce.UNAVAILABLE)
    if state == reproduce.FAIL:
        assert "does not contain" in detail


def test_the_report_writes_machine_readable_output(tmp_path, monkeypatch):
    target = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["reproduce.py", "--fast", "--json", str(target)])
    code = reproduce.main()
    assert code in (0, 1)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert {"pass", "fail", "unavailable"} == set(payload["summary"])
    assert len(payload["checks"]) >= 8
    for check in payload["checks"]:
        assert check["state"] in {"pass", "fail", "unavailable"}
        assert check["detail"]


def test_unavailable_alone_does_not_fail_the_run(monkeypatch):
    """An honest gap is not a broken build; treating it as one trains people
    to ignore it."""
    for name in ("check_git_provenance", "check_integration"):
        monkeypatch.setattr(reproduce, name, lambda: (reproduce.PASS, "ok"))
    monkeypatch.setattr("sys.argv", ["reproduce.py", "--fast"])
    assert reproduce.main() == 0, "only a genuine failure may fail the run"
