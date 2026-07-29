"""Persistent internal state for the autonomy planner.

Tracks what the agent has recently done so the planner can favor *novelty*
(varying its behavior instead of repeating one direction) and respect
cadence (how long since it last acted or spoke). Persisted as a small,
human-readable JSON file in the workspace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

from loguru import logger

from .directions import Direction
from ..utils.timezone import now_local

_STATE_FILENAME = "autonomy_state.json"

# How many recent choices to remember for novelty scoring.
_RECENT_WINDOW = 6


@dataclass
class AutonomyState:
    last_action_ts: Optional[str] = None
    last_spoke_ts: Optional[str] = None
    total_actions: int = 0
    recent_directions: list[str] = field(default_factory=list)
    direction_counts: dict[str, int] = field(default_factory=dict)

    _path: Optional[Path] = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, workspace_root: Path) -> "AutonomyState":
        path = workspace_root / _STATE_FILENAME
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                state = cls(
                    last_action_ts=raw.get("last_action_ts"),
                    last_spoke_ts=raw.get("last_spoke_ts"),
                    total_actions=int(raw.get("total_actions", 0)),
                    recent_directions=list(raw.get("recent_directions", [])),
                    direction_counts=dict(raw.get("direction_counts", {})),
                )
                state._path = path
                return state
            except Exception as exc:  # noqa: BLE001 - corrupt file shouldn't crash
                logger.warning(
                    f"autonomy: could not read {path} ({exc}); starting fresh"
                )
        state = cls()
        state._path = path
        return state

    def save(self) -> None:
        if self._path is None:
            return
        data = {k: v for k, v in asdict(self).items() if not k.startswith("_")}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(f".{uuid4().hex}.tmp")
            temporary.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(self._path)
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            logger.warning(f"autonomy: failed to save state: {exc}")
        finally:
            if "temporary" in locals():
                temporary.unlink(missing_ok=True)

    # ------------------------------------------------------------------ #
    def novelty(self, direction: Direction) -> float:
        """Return a novelty score in [0, 1]: 1.0 if the direction hasn't been
        used recently, decreasing with how recently/often it appears."""
        recent = self.recent_directions[-_RECENT_WINDOW:]
        if not recent:
            return 1.0
        occurrences = recent.count(direction.value)
        # Each recent occurrence subtracts a chunk of novelty.
        return max(
            0.0,
            1.0
            - occurrences / _RECENT_WINDOW
            - (0.25 if recent and recent[-1] == direction.value else 0.0),
        )

    def seconds_since_last_spoke(
        self, now: Optional[datetime] = None
    ) -> Optional[float]:
        return self._seconds_since(self.last_spoke_ts, now)

    def seconds_since_last_action(
        self, now: Optional[datetime] = None
    ) -> Optional[float]:
        return self._seconds_since(self.last_action_ts, now)

    @staticmethod
    def _seconds_since(ts: Optional[str], now: Optional[datetime]) -> Optional[float]:
        if not ts:
            return None
        try:
            then = datetime.fromisoformat(ts)
        except ValueError:
            return None
        resolved_now = now or now_local()
        if then.tzinfo is None:
            # Legacy state persisted before timestamps were made timezone-aware.
            then = then.replace(tzinfo=resolved_now.tzinfo)
        return max(0.0, (resolved_now - then).total_seconds())

    def record_action(
        self, direction: Direction, spoke: bool, now: Optional[datetime] = None
    ) -> None:
        """Record that the agent acted along `direction` (and whether it spoke)."""
        stamp = (now or now_local()).isoformat(timespec="seconds")
        self.last_action_ts = stamp
        if spoke:
            self.last_spoke_ts = stamp
        self.total_actions += 1
        self.recent_directions.append(direction.value)
        if len(self.recent_directions) > _RECENT_WINDOW * 2:
            self.recent_directions = self.recent_directions[-_RECENT_WINDOW * 2 :]
        self.direction_counts[direction.value] = (
            self.direction_counts.get(direction.value, 0) + 1
        )
        self.save()


_states_by_root: dict[str, AutonomyState] = {}


def get_or_create_autonomy_state(workspace_root: Path) -> AutonomyState:
    """Share one mutable planner state across clients using one workspace."""
    key = str(workspace_root.resolve())
    state = _states_by_root.get(key)
    if state is None:
        state = AutonomyState.load(workspace_root)
        _states_by_root[key] = state
    return state
