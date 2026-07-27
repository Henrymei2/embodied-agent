"""Turn study session logs into a table you can actually compare.

Reads the append-only JSONL written by `src/open_llm_vtuber/study.py`, reduces
each session to one row, and prints the rows grouped by condition. With
`--csv` it writes the same rows for a spreadsheet or R.

    uv run python scripts/study_report.py
    uv run python scripts/study_report.py --csv results.csv
    uv run python scripts/study_report.py --dir ~/embodied-agent/study

The measures are behavioral proxies, not a presence score. Nothing here
substitutes for asking participants how present the companion felt; it tells
you what they *did*, which is the part a questionnaire cannot recover after
the fact.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# Columns in the order they are printed. Each is one question a reader of the
# report is likely to be asking.
COLUMNS = (
    ("session", "session"),
    ("participant", "participant"),
    ("condition", "condition"),
    ("minutes", "minutes"),
    ("turns", "turns"),
    ("clicks", "obj clicks"),
    ("click_share", "click %"),
    ("voice_turns", "voice"),
    ("median_reply_wait", "reply wait"),
    ("recall_hit_rate", "recall hit %"),
    ("mean_top_score", "recall score"),
    ("tools_blocked", "walks needed"),
    ("autonomy_actions", "initiatives"),
    ("autonomy_helpful", "helpful"),
    ("autonomy_dismissed", "dismissed"),
)


@dataclass
class Session:
    session: str = ""
    participant: str = ""
    condition: str = ""
    minutes: float = 0.0
    turns: int = 0
    clicks: int = 0
    voice_turns: int = 0
    text_turns: int = 0
    autonomous_turns: int = 0
    reply_waits: list[float] = field(default_factory=list)
    recalls: int = 0
    recall_hits: int = 0
    top_scores: list[float] = field(default_factory=list)
    tools_called: int = 0
    tools_blocked: int = 0
    autonomy_actions: int = 0
    autonomy_helpful: int = 0
    autonomy_dismissed: int = 0
    autonomy_snoozed: int = 0

    # Each of these is None when nothing was measured, never 0. A recall hit
    # rate of 0% means memory was asked and found nothing; no recall at all is
    # a different fact, and a study table that renders both as 0 invites the
    # wrong conclusion about the pillar being tested.
    @property
    def click_share(self) -> float | None:
        """Share of user-started turns that began by touching the room."""
        started = self.clicks + self.voice_turns + self.text_turns
        return 100.0 * self.clicks / started if started else None

    @property
    def median_reply_wait(self) -> float | None:
        return statistics.median(self.reply_waits) if self.reply_waits else None

    @property
    def recall_hit_rate(self) -> float | None:
        return 100.0 * self.recall_hits / self.recalls if self.recalls else None

    @property
    def mean_top_score(self) -> float | None:
        return statistics.fmean(self.top_scores) if self.top_scores else None

    def as_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {}
        for key, _heading in COLUMNS:
            value = getattr(self, key)
            row[key] = round(value, 2) if isinstance(value, float) else value
        return row


def read_events(path: Path) -> Iterable[dict[str, Any]]:
    """Yield the readable events in one log, skipping any truncated tail."""
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:
        # One unreadable file must not cost the report every other session.
        print(f"Skipping {path.name}: {exc}", file=sys.stderr)
        return
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A session killed mid-write leaves a partial last line. That
                # is not a reason to lose the rest of the sitting.
                continue
            if isinstance(event, dict):
                yield event


def summarize(path: Path) -> Session | None:
    summary = Session()
    seen = False

    for event in read_events(path):
        kind = event.get("event")
        summary.session = summary.session or str(event.get("session", ""))[:8]
        summary.participant = summary.participant or str(event.get("participant", ""))
        summary.condition = summary.condition or str(event.get("condition", ""))
        seen = True

        if kind == "session_end":
            summary.minutes = float(event.get("duration_seconds") or 0.0) / 60.0
        elif kind == "turn_started":
            summary.turns += 1
            source = event.get("source")
            if source == "object_click":
                summary.clicks += 1
            elif source == "voice":
                summary.voice_turns += 1
            elif source == "text":
                summary.text_turns += 1
            elif source == "autonomous":
                summary.autonomous_turns += 1
            wait = event.get("seconds_since_agent_finished")
            # Only a user answering the agent measures engagement; the agent
            # starting its own turn says nothing about the participant.
            if isinstance(wait, (int, float)) and source != "autonomous":
                summary.reply_waits.append(float(wait))
        elif kind == "memory_recall":
            if event.get("enabled"):
                summary.recalls += 1
                if (event.get("documents") or 0) > 0:
                    summary.recall_hits += 1
                    summary.top_scores.append(float(event.get("top_score") or 0.0))
        elif kind == "tool_call":
            summary.tools_called += 1
            if not event.get("allowed"):
                summary.tools_blocked += 1
        elif kind == "autonomy_action":
            summary.autonomy_actions += 1
        elif kind == "autonomy_feedback":
            verdict = event.get("feedback")
            if verdict == "helpful":
                summary.autonomy_helpful += 1
            elif verdict == "dismissed":
                summary.autonomy_dismissed += 1
            elif verdict == "snoozed":
                summary.autonomy_snoozed += 1

    return summary if seen else None


def _cell(value: Any) -> str:
    """Render one value, distinguishing "nothing measured" from zero."""
    return "-" if value is None else str(value)


def print_table(sessions: list[Session]) -> None:
    headings = [heading for _key, heading in COLUMNS]
    rows = [
        [_cell(session.as_row()[key]) for key, _heading in COLUMNS]
        for session in sessions
    ]
    widths = [
        max(len(headings[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headings[index])
        for index in range(len(headings))
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells))

    print(line(headings))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(line(row))


def print_by_condition(sessions: list[Session]) -> None:
    """The comparison the study exists to make."""
    conditions: dict[str, list[Session]] = {}
    for session in sessions:
        conditions.setdefault(session.condition or "unknown", []).append(session)

    print("\nBy condition")
    print("=" * 12)
    for condition in sorted(conditions):
        group = conditions[condition]

        def mean(attribute: str) -> float | None:
            """Average over the sessions that measured this, or None if none did."""
            values = [
                float(getattr(item, attribute))
                for item in group
                if getattr(item, attribute) is not None
            ]
            return statistics.fmean(values) if values else None

        def show(attribute: str, spec: str = "8.1f", width: int = 8) -> str:
            value = mean(attribute)
            return "-".rjust(width) if value is None else format(value, spec)

        print(
            f"\n{condition}  (n={len(group)})\n"
            f"  minutes per session   {show('minutes')}\n"
            f"  turns per session     {show('turns')}\n"
            f"  object clicks         {show('clicks')}"
            f"   ({show('click_share', '.0f', 1)}% of user turns)\n"
            f"  median reply wait     {show('median_reply_wait')}s\n"
            f"  recall hit rate       {show('recall_hit_rate', '8.0f')}%\n"
            f"  initiatives           {show('autonomy_actions')}"
            f"   ({show('autonomy_helpful', '.1f', 1)} helpful,"
            f" {show('autonomy_dismissed', '.1f', 1)} dismissed)"
        )

    if len(conditions) < 2:
        print(
            "\nOnly one condition is present. Run an ablated session to compare, e.g.\n"
            "  EMBODIED_AGENT_PARTICIPANT=P01 EMBODIED_AGENT_CONDITION=memory=off \\\n"
            "    uv run run_server.py"
        )


def default_directory() -> Path:
    from src.open_llm_vtuber.workspace import DEFAULT_WORKSPACE_DIR

    return DEFAULT_WORKSPACE_DIR / "study"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Directory of session logs (default: <workspace>/study).",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Also write one row per session to this CSV file.",
    )
    args = parser.parse_args()

    directory = (args.dir or default_directory()).expanduser()
    if not directory.is_dir():
        print(f"No study logs at {directory}.")
        print(
            "Run a session with EMBODIED_AGENT_STUDY_LOG=1 (or in any ablated "
            "condition) and try again."
        )
        return 1

    logs = sorted(directory.glob("*.jsonl"))
    sessions = [summary for path in logs if (summary := summarize(path)) is not None]
    if not sessions:
        print(f"No readable sessions in {directory}.")
        return 1

    print(f"{len(sessions)} session(s) from {directory}\n")
    print_table(sessions)
    print_by_condition(sessions)

    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        with args.csv.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[key for key, _ in COLUMNS])
            writer.writeheader()
            for session in sessions:
                writer.writerow(session.as_row())
        print(f"\nWrote {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
