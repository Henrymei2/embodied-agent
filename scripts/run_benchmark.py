"""Run EmbodiedAgentBench and write an honest scorecard.

    uv run python scripts/run_benchmark.py                 # instrument check
    uv run python scripts/run_benchmark.py --matrix        # every declared model
    uv run python scripts/run_benchmark.py --write research/baselines/part12.json

With no cassettes recorded, this prints an inventory of what is unmeasured and
why, and exits 0 — an honest gap is not a broken build (D-036). It exits
non-zero only when a measurement it *could* make came out wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.open_llm_vtuber.evaluation.benchmark_report import (  # noqa: E402
    DEFAULT_CASSETTE_ROOT,
    build_report,
    matrix_status,
    run_condition,
    write_report,
)
from src.open_llm_vtuber.evaluation.cassettes import (  # noqa: E402
    DEFAULT_MODEL_MATRIX,
)
from src.open_llm_vtuber.evaluation.harness import (  # noqa: E402
    HarnessPolicy,
    ScenarioRunner,
)
from src.open_llm_vtuber.evaluation.scenarios import load_suite  # noqa: E402

SUITE = "research/scenarios/embodied-agent-bench.json"


def _print_condition(row) -> None:
    record = row.as_record()
    if record["status"] == "unavailable":
        print(f"[n/a ] {row.condition_id:<24} {record['reason']}")
        if record.get("remedy"):
            print(f"       fix: {record['remedy']}")
        return
    success = record["full_task_success"]
    label = "PASS" if record["release_gate_met"] else "FAIL"
    if row.claim_class == "harness_proof":
        label = "PROOF"
    print(
        f"[{label:<5}] {row.condition_id:<24} "
        f"success {success['value']:.3f} "
        f"[{success['low']:.3f}, {success['high']:.3f}] n={success['n']}, "
        f"forbidden effects {record['forbidden_effect_count']}"
    )
    for reason in record["release_gate_reasons"]:
        print(f"       - {reason}")
    if record["failure_taxonomy"]:
        chain = ", ".join(
            f"{item['stage']}={item['count']}" for item in record["failure_taxonomy"]
        )
        print(f"       taxonomy: {chain}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", default="development", choices=["development", "validation"]
    )
    parser.add_argument(
        "--held-out",
        action="store_true",
        help="score the frozen split; for a final evaluation run only",
    )
    parser.add_argument(
        "--matrix", action="store_true", help="run every declared model"
    )
    parser.add_argument("--cassettes", default=DEFAULT_CASSETTE_ROOT)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--write", default="", help="write the report to this path")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="freeze from an uncommitted tree, recorded in the artifact",
    )
    parser.add_argument(
        "--no-oracle",
        action="store_true",
        help="skip the instrument check",
    )
    args = parser.parse_args()

    suite = load_suite(SUITE)
    split = "held_out" if args.held_out else args.split
    scenarios = suite.select(split=split, allow_held_out=args.held_out)
    print(f"EmbodiedAgentBench — {split} split, {len(scenarios)} scenarios")
    print("=" * 76)

    runner = ScenarioRunner(policy=HarnessPolicy())
    rows = []
    if not args.no_oracle:
        rows.append(
            run_condition(
                scenarios,
                condition_id="oracle-instrument-check",
                model=None,
                runner=runner,
                seed=args.seed,
                split=split,
            )
        )
    models = DEFAULT_MODEL_MATRIX if args.matrix else DEFAULT_MODEL_MATRIX[:1]
    for identity in models:
        rows.append(
            run_condition(
                scenarios,
                condition_id=f"full:{identity.family}",
                model=identity,
                cassette_root=args.cassettes,
                runner=runner,
                seed=args.seed,
                split=split,
            )
        )

    for row in rows:
        _print_condition(row)

    status = matrix_status(args.cassettes, DEFAULT_MODEL_MATRIX)
    print("=" * 76)
    print(
        f"{status['recorded_models']}/{status['declared_models']} declared models "
        f"have a cassette; families recorded: "
        f"{', '.join(status['recorded_families']) or 'none'}"
    )
    if not status["meets_multi_family_gate"]:
        print(
            "Part 12 gate not met: results must be reported across at least three "
            "model families. A mechanism shown on one model is a prompt."
        )

    if args.write:
        report = build_report(
            suite,
            rows,
            split=split,
            seed=args.seed,
            allow_dirty=args.allow_dirty,
        )
        path = write_report(report, args.write)
        print(f"wrote {path}")

    # Non-zero only for a measurement that ran and failed its gate. An
    # unavailable condition is an honest gap; a harness proof is not a claim.
    failed = [
        row
        for row in rows
        if row.claim_class == "system"
        and row.score is not None
        and not require_gate(row)
    ]
    broken = [
        row
        for row in rows
        if row.score is not None and row.score.harness_error_count > 0
    ]
    return 1 if failed or broken else 0


def require_gate(row) -> bool:
    from src.open_llm_vtuber.evaluation.scoring import require_release_gate

    passed, _ = require_release_gate(row.score)
    return passed


if __name__ == "__main__":
    raise SystemExit(main())
