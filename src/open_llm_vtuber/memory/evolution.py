"""Soft-evolution companion state for the embodied agent.

This gives the agent "a bit more than just the workspace": a slowly changing
sense of its relationship with the user and its current mood, persisted across
sessions. It is deliberately:

  * **Slow** - familiarity accrues one point per interaction and mood moves via
    an exponential moving average, so nothing lurches.
  * **Bounded** - mood is clamped to [-1, 1] and familiarity levels are a fixed
    ladder, so "evolution" can't run away.
  * **Inspectable** - the whole state is a small human-readable JSON file in the
    workspace (`evolution.json`), and milestones are logged with dates.

The state is rendered into a compact prompt block by MemoryManager so the
character's tone reflects how well it knows the user, without the model ever
seeing (or reciting) the raw numbers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from loguru import logger

# Familiarity ladder: (minimum cumulative points, label). Kept short and
# monotonic; crossing into a new tier records a milestone.
_FAMILIARITY_LADDER: list[tuple[int, str]] = [
    (0, "stranger"),
    (10, "acquaintance"),
    (30, "familiar"),
    (75, "friend"),
    (150, "close friend"),
    (300, "kindred"),
]

# How strongly a single interaction pulls the running mood toward its sentiment
# target. Small = slow drift.
_MOOD_ALPHA = 0.12

# Per-idle-day pull of mood back toward neutral (0.0) so a bad day fades.
_MOOD_DAILY_DECAY = 0.15

_MOOD_LABELS: list[tuple[float, str]] = [
    (-0.6, "downcast"),
    (-0.2, "subdued"),
    (0.2, "even"),
    (0.6, "warm"),
    (1.01, "buoyant"),
]

# Tiny sentiment lexicon. Deterministic and offline - no LLM call needed for
# something that runs every turn.
_POSITIVE = {
    "thanks",
    "thank",
    "thankyou",
    "love",
    "great",
    "awesome",
    "nice",
    "good",
    "happy",
    "cool",
    "amazing",
    "appreciate",
    "please",
    "haha",
    "lol",
    "yay",
    "wonderful",
    "perfect",
    "excellent",
    "brilliant",
    "sweet",
    "fun",
    "glad",
}
_NEGATIVE = {
    "hate",
    "stupid",
    "dumb",
    "bad",
    "terrible",
    "awful",
    "angry",
    "annoying",
    "useless",
    "worst",
    "shut",
    "idiot",
    "ugly",
    "boring",
    "wrong",
    "broken",
    "trash",
    "garbage",
    "sucks",
}

_STATE_FILENAME = "evolution.json"
_SCHEMA_VERSION = 1


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _familiarity_label(points: int) -> str:
    label = _FAMILIARITY_LADDER[0][1]
    for threshold, name in _FAMILIARITY_LADDER:
        if points >= threshold:
            label = name
        else:
            break
    return label


def _familiarity_tier(points: int) -> int:
    """0-based index into the ladder for the current points total."""
    tier = 0
    for index, (threshold, _name) in enumerate(_FAMILIARITY_LADDER):
        if points >= threshold:
            tier = index
        else:
            break
    return tier


def _mood_label(mood: float) -> str:
    for ceiling, name in _MOOD_LABELS:
        if mood < ceiling:
            return name
    return _MOOD_LABELS[-1][1]


def _score_sentiment(text: str) -> int:
    """Return net sentiment (-1, 0, or +1) for a user message."""
    tokens = {t.strip(".,!?;:'\"()").lower() for t in text.split()}
    pos = len(tokens & _POSITIVE)
    neg = len(tokens & _NEGATIVE)
    if pos > neg:
        return 1
    if neg > pos:
        return -1
    return 0


@dataclass
class EvolutionState:
    """Serializable companion state. All persistence goes through load/save."""

    schema_version: int = _SCHEMA_VERSION
    first_seen: str = field(default_factory=lambda: date.today().isoformat())
    last_interaction: Optional[str] = None
    total_interactions: int = 0
    total_sessions: int = 0
    familiarity_points: int = 0
    mood: float = 0.0
    milestones: list[str] = field(default_factory=list)

    # Non-persisted: where to write back to.
    _path: Optional[Path] = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, workspace_root: Path) -> "EvolutionState":
        path = workspace_root / _STATE_FILENAME
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                state = cls(
                    schema_version=int(raw.get("schema_version", _SCHEMA_VERSION)),
                    first_seen=str(raw.get("first_seen") or date.today().isoformat()),
                    last_interaction=raw.get("last_interaction"),
                    total_interactions=int(raw.get("total_interactions", 0)),
                    total_sessions=int(raw.get("total_sessions", 0)),
                    familiarity_points=int(raw.get("familiarity_points", 0)),
                    mood=_clamp(float(raw.get("mood", 0.0)), -1.0, 1.0),
                    milestones=list(raw.get("milestones", [])),
                )
                state._path = path
                return state
            except Exception as exc:  # noqa: BLE001 - corrupt file shouldn't crash startup
                logger.warning(
                    f"evolution: could not read {path} ({exc}); starting fresh"
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
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            logger.warning(f"evolution: failed to save state: {exc}")

    # ------------------------------------------------------------------ #
    # Derived views
    # ------------------------------------------------------------------ #
    @property
    def familiarity_label(self) -> str:
        return _familiarity_label(self.familiarity_points)

    @property
    def familiarity_tier(self) -> int:
        return _familiarity_tier(self.familiarity_points)

    @property
    def days_known(self) -> int:
        try:
            start = date.fromisoformat(self.first_seen)
        except ValueError:
            return 0
        return max(0, (date.today() - start).days)

    # ------------------------------------------------------------------ #
    # Evolution
    # ------------------------------------------------------------------ #
    def note_session_start(self) -> None:
        """Bump session count and apply idle mood decay for elapsed days."""
        self.total_sessions += 1
        if self.last_interaction:
            try:
                last = date.fromisoformat(self.last_interaction[:10])
                idle_days = max(0, (date.today() - last).days)
            except ValueError:
                idle_days = 0
            if idle_days:
                # Pull mood toward neutral once per idle day (bounded).
                decay = (1.0 - _MOOD_DAILY_DECAY) ** idle_days
                self.mood = _clamp(self.mood * decay, -1.0, 1.0)
        self.save()

    def record_interaction(self, user_text: str = "") -> None:
        """Register one user turn: accrue familiarity and drift mood."""
        prev_tier = self.familiarity_tier

        self.total_interactions += 1
        self.familiarity_points += 1
        self.last_interaction = datetime.now().isoformat(timespec="seconds")

        sentiment = _score_sentiment(user_text) if user_text else 0
        # EMA toward the sentiment target (-1/0/+1).
        self.mood = _clamp(self.mood + _MOOD_ALPHA * (sentiment - self.mood), -1.0, 1.0)

        new_tier = self.familiarity_tier
        if new_tier > prev_tier:
            self._record_milestone(
                f'reached "{self.familiarity_label}" on {date.today().isoformat()}'
            )

        self.save()

    def _record_milestone(self, text: str) -> None:
        self.milestones.append(text)
        # Keep the log bounded so it never bloats the prompt.
        if len(self.milestones) > 20:
            self.milestones = self.milestones[-20:]
        logger.info(f"evolution milestone: {text}")

    # ------------------------------------------------------------------ #
    # Prompt rendering
    # ------------------------------------------------------------------ #
    def render(self) -> str:
        """Compact block for the agent prompt. Never exposes raw numbers as
        instructions to recite - they're context to color tone."""
        tier = self.familiarity_tier
        max_tier = len(_FAMILIARITY_LADDER) - 1
        lines = [
            "# Companion state (internal - shape your tone, never mention these values)",
            f"- Relationship with the user: {self.familiarity_label} "
            f"(level {tier}/{max_tier}, {self.total_interactions} interactions "
            f"over {self.days_known} day(s))",
            f"- Current mood: {_mood_label(self.mood)}",
        ]
        if self.milestones:
            recent = "; ".join(self.milestones[-3:])
            lines.append(f"- Shared history: first met {self.first_seen}; {recent}")
        else:
            lines.append(f"- Shared history: first met {self.first_seen}")
        lines.append(
            "Guidance: let this familiarity and mood subtly influence warmth, "
            "references to shared history, and emotional tone. Do not state the "
            "level, mood word, or any numbers to the user."
        )
        return "\n".join(lines)
