"""Run the frozen Part 4 repair ablation and optionally verify a baseline."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_FIXTURE = ROOT / "research" / "fixtures" / "held_out" / "part4-repair.json"
DEFAULT_BASELINE = ROOT / "research" / "baselines" / "part4-plan-repair.json"


def main() -> int:
    from src.open_llm_vtuber.evaluation.part4 import run_part4_repair_evaluation

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(run_part4_repair_evaluation(args.fixture))
    rendered = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    if args.verify:
        expected = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
        if report != expected:
            print("Part 4 baseline mismatch")
            return 1
    print(rendered, end="")
    return 0 if all(report["acceptance"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
