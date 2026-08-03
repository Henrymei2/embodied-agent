"""Run the offline replay three times and emit an explicit baseline report.

Usage:
    uv run python scripts/run_evaluation_baseline.py
    uv run python scripts/run_evaluation_baseline.py --output baseline.json
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.open_llm_vtuber.evaluation import (  # noqa: E402
    build_run_manifest,
    canonical_digest,
    finish_run_manifest,
    load_research_contract,
)
from src.open_llm_vtuber.evaluation.baseline import (  # noqa: E402
    build_baseline_report,
    measurements_from_replay,
)
from src.open_llm_vtuber.evaluation.replay import (  # noqa: E402
    validate_replay_fixture,
    verify_replay_determinism,
)

_DEFAULT_FIXTURE = (
    _PROJECT_ROOT / "research" / "fixtures" / "development" / "part2-smoke.json"
)
_TOKEN_CLEANER = re.compile(r"[^A-Za-z0-9._:-]+")


def _token(value: str, fallback: str) -> str:
    cleaned = _TOKEN_CLEANER.sub("-", value.strip()).strip("-._:")
    return cleaned[:120] or fallback


def _manifest(fixture: dict, fixture_digest: str) -> dict:
    contract = load_research_contract()
    # `git rev-parse` needs an argument; keeping all subprocess calls explicit
    # makes it impossible for fixture content to become a shell command.
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    configuration = {
        "replay_schema": fixture["schema_version"],
        "fixture_revision": fixture["revision"],
        "adapter_kinds": sorted(fixture["adapters"]),
    }
    return build_run_manifest(
        run_id="part2-development-baseline",
        code_revision=revision,
        worktree_dirty=dirty,
        contract_revision=contract["contract_revision"],
        condition_id="full",
        scenario_set_revision=fixture["revision"],
        scenario_split="development",
        fixture_digest=fixture_digest,
        configuration_digest=canonical_digest(configuration),
        model_identifiers=["fixture-llm-v1"],
        engine_identifiers=[
            "deterministic-clock-v1",
            "fixture-adapters-v1",
            "failure-plan-v1",
        ],
        platform={
            "system": _token(platform.system(), "unknown-system"),
            "release": _token(platform.release(), "unknown-release"),
            "machine": _token(platform.machine(), "unknown-machine"),
            "python": _token(platform.python_version(), "unknown-python"),
        },
        seed=0,
        started_at=fixture["started_at"],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=_DEFAULT_FIXTURE)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    validate_replay_fixture(fixture)
    result = verify_replay_determinism(
        fixture,
        runs=args.runs,
        run_id="part2-development-baseline",
    )
    running = _manifest(fixture, canonical_digest(fixture))
    terminal = finish_run_manifest(
        running,
        status="completed",
        finished_at=result.events[-1]["timestamp"],
    )
    failure_scenarios = len(
        {(item["adapter"], item["request_id"]) for item in fixture["failures"]}
    )
    duplicate_scenarios = sum(
        item["kind"] == "duplicate_event" for item in fixture["failures"]
    )
    measurements = measurements_from_replay(
        result,
        failure_directives=failure_scenarios,
        duplicate_directives=duplicate_scenarios,
    )
    report = build_baseline_report(
        terminal,
        measurements,
        report_id="part2-development-baseline",
        generated_at=result.events[-1]["timestamp"],
    )
    artifact = {
        "schema_version": 1,
        "manifest": terminal,
        "replay": {
            "runs": args.runs,
            "terminal_state_digest": result.terminal_state_digest,
            "action_outcomes_digest": result.action_outcomes_digest,
            "event_chain_digest": result.event_chain_digest,
        },
        "report": report,
    }
    rendered = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        destination = args.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(destination)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
