"""Print a content-free long-horizon autonomy quality report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.open_llm_vtuber.autonomy.agenda import AgendaStore  # noqa: E402
from src.open_llm_vtuber.autonomy.telemetry import build_chain_report  # noqa: E402
from src.open_llm_vtuber.workspace import ensure_workspace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace root; defaults to the configured embodied-agent workspace.",
    )
    parser.add_argument("--hours", type=int, default=168)
    args = parser.parse_args()
    root = args.workspace.resolve() if args.workspace else ensure_workspace().root
    report = build_chain_report(AgendaStore(root), horizon_hours=args.hours)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
