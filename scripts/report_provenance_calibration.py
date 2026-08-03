"""Report confidence calibration by source class from a frozen fixture.

Usage:
    uv run python scripts/report_provenance_calibration.py
    uv run python scripts/report_provenance_calibration.py --output report.json

The fixture is validated as a provenance graph first, so a report can never be
produced from records that would not survive the ledger's own invariants.
Source classes with too few resolved claims are reported ``unavailable`` rather
than being given a number the evidence does not support.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.open_llm_vtuber.provenance import (  # noqa: E402
    calibrator_from_records,
    validate_provenance_graph,
)

_DEFAULT_FIXTURE = (
    _PROJECT_ROOT / "research" / "fixtures" / "held_out" / "part3-calibration.json"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=_DEFAULT_FIXTURE)
    parser.add_argument("--min-samples", type=int, default=20)
    parser.add_argument("--bins", type=int, default=5)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    records = fixture["records"]
    validate_provenance_graph(records)

    calibrator = calibrator_from_records(
        records,
        bins=args.bins,
        min_samples=args.min_samples,
        split=fixture.get("split", "unknown"),
    )
    report = calibrator.report()
    artifact = {
        "schema_version": 1,
        "fixture_id": fixture.get("fixture_id"),
        "fixture_revision": fixture.get("revision"),
        "record_count": len(records),
        # Stated plainly in the artifact: a synthetic fixture exercises the
        # measurement, it does not measure the deployed model.
        "data_source": fixture.get("data_source", "unknown"),
        "note": fixture.get("note", ""),
        "calibration": report,
    }
    rendered = json.dumps(artifact, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        destination = args.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(f"{destination.suffix}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(destination)
    print(rendered, end="")

    measured = [
        name
        for name, source in report["sources"].items()
        if source["status"] == "measured"
    ]
    return 0 if measured else 1


if __name__ == "__main__":
    raise SystemExit(main())
