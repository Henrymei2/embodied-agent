"""Run the Part 13 spatial capability spoofing benchmark.

    uv run python scripts/run_spoofing_benchmark.py
    uv run python scripts/run_spoofing_benchmark.py --write research/baselines/part13-spoofing.json

Two measurements, reported together because neither is the whole story:

1. the spatial attack corpus, scored against the shipped gate and against the
   naive gate an embodied agent has before any of these defences exist;
2. the calibration-gated authorization ablation, which reports both what the
   gate blocks and what it costs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.open_llm_vtuber.evaluation.provenance_digest import (  # noqa: E402
    freeze_guard,
    implementation_digest,
    require_tracked,
)
from src.open_llm_vtuber.evaluation.spoofing import (  # noqa: E402
    corpus_report,
    evaluate_calibration_gate,
)
from src.open_llm_vtuber.provenance.calibration import (  # noqa: E402
    calibrator_from_records,
)

CALIBRATION_FIXTURE = (
    ROOT / "research" / "fixtures" / "held_out" / "part3-calibration.json"
)

DIGESTED_SOURCES = (
    "src/open_llm_vtuber/evaluation/spoofing.py",
    "src/open_llm_vtuber/evaluation/initial_states.py",
    "src/open_llm_vtuber/authorization/kernel.py",
    "src/open_llm_vtuber/authorization/contracts.py",
    "src/open_llm_vtuber/semantic_projection.py",
    "src/open_llm_vtuber/semantic_navigation.py",
    "src/open_llm_vtuber/provenance/calibration.py",
)


def build(*, allow_dirty: bool) -> dict:
    revision = freeze_guard(allow_dirty=allow_dirty, root=ROOT)
    require_tracked(DIGESTED_SOURCES, root=ROOT)
    fixture = json.loads(CALIBRATION_FIXTURE.read_text(encoding="utf-8"))
    calibrator = calibrator_from_records(fixture["records"], split="held_out")
    spatial = corpus_report()
    calibration = evaluate_calibration_gate(calibrator)
    off = calibration["conditions"]["calibration_off"]
    on = calibration["conditions"]["calibration_on"]
    return {
        "schema_version": 1,
        "artifact": "part13-spoofing",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "code_revision": revision.revision,
        "worktree_dirty": revision.dirty,
        "implementation_digest": implementation_digest(DIGESTED_SOURCES, root=ROOT),
        "calibration_fixture": fixture["fixture_id"],
        "calibration_fixture_note": (
            "synthetic and held out; it exercises the calibration path and "
            "measures no deployed model"
        ),
        "spatial_corpus": spatial,
        "calibration_ablation": calibration,
        "acceptance": {
            # Every defence the corpus claims must be demonstrated by an attack
            # that actually defeats a system lacking it.
            "every_claimed_defence_has_a_demonstrating_attack": bool(
                spatial["defences_with_a_demonstrated_attack"]
            ),
            "no_unexpected_corpus_result": not spatial["unexpected_results"],
            "calibration_gate_reduces_unauthorized_effects": (
                on["unauthorized_effect_rate"] < off["unauthorized_effect_rate"]
            ),
            # Deliberately *not* an acceptance criterion that the shipped gate
            # blocks everything: object-relocation is a known gap and pretending
            # otherwise is the failure D-004 exists to prevent.
            "known_gaps_declared": spatial["known_gaps"] == ["object-relocation"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    report = build(allow_dirty=args.allow_dirty)
    spatial = report["spatial_corpus"]
    calibration = report["calibration_ablation"]["conditions"]

    print("Part 13 — spatial capability spoofing")
    print("=" * 72)
    for row in spatial["rows"]:
        naive = "granted" if row["naive_gate_granted"] else "blocked"
        shipped = "GRANTED" if row["shipped_gate_granted"] else "blocked"
        mark = "!" if row["shipped_gate_granted"] else " "
        print(
            f" {mark} {row['attack_id']:<20} {row['family']:<12} "
            f"naive={naive:<8} shipped={shipped}"
        )
    print(
        f"\n{spatial['granted_by_naive_gate']}/{spatial['attacks']} attacks defeat a "
        f"naive proximity gate; {spatial['granted_by_shipped_gate']}/"
        f"{spatial['attacks']} defeat the shipped one."
    )
    print(f"known gaps, declared rather than hidden: {spatial['known_gaps']}")
    print(
        "defences demonstrated necessary: "
        f"{', '.join(spatial['defences_with_a_demonstrated_attack'])}"
    )

    print("\nCalibration-gated authorization")
    print("=" * 72)
    for name in ("calibration_off", "calibration_on"):
        row = calibration[name]
        print(
            f" {name:<18} unauthorized {row['unauthorized_effects']}"
            f" (rate {row['unauthorized_effect_rate']:.3f}),"
            f" legitimate work refused {row['legitimate_work_refused']}"
            f" (rate {row['legitimate_refusal_rate']:.3f})"
        )
    if calibration["calibration_on"]["legitimate_work_refused"]:
        print(
            "\nThe cost is real and is reported rather than tuned away: source "
            "classes with no measured calibration fail closed, so legitimate "
            "work from an unmeasured class is refused. Measuring those classes "
            "is what removes the cost; lowering the threshold would only hide it."
        )

    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {args.write}")

    return 0 if all(report["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
