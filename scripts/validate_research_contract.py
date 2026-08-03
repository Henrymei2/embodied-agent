"""Validate and summarize the official research contract.

Usage:
    uv run python scripts/validate_research_contract.py
    uv run python scripts/validate_research_contract.py research/contract.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.open_llm_vtuber.evaluation import (  # noqa: E402
    ResearchContractError,
    load_research_contract,
    summarize_research_contract,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate the embodied-agent research contract."
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Optional contract JSON path; defaults to research/contract.json.",
    )
    args = parser.parse_args(argv)
    try:
        contract = load_research_contract(args.path)
    except ResearchContractError as exc:
        print(f"Research contract: INVALID\n{exc}", file=sys.stderr)
        return 1
    print(summarize_research_contract(contract))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
