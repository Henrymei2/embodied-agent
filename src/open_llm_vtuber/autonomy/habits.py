"""The Habit Engine: a self-expanding repertoire of autonomous behaviors.

The built-in ``CANDIDATES`` in ``directions.py`` are the agent's innate
behaviors. This module lets the agent *grow new ones from lived experience*:
a periodic LLM reflection over presence receipts, user feedback, and memory
proposes a new "habit"; the habit runs a probationary trial through the same
planner/attention-budget gates as every built-in; explicit user feedback
promotes it to established or retires it.

Safety model:
  - Habits are pure data (JSON), never code. Their context-relevance is a set
    of *declarative gates* (idle range, hour window, presence) compiled to a
    relevance function by trusted code — the LLM never authors logic.
  - Trial habits carry a skepticism prior: usefulness is capped, risk is
    boosted, and ``speak`` delivery is downgraded to ``ask`` until the habit
    earns promotion.
  - Hard caps bound the repertoire; retired habits are kept forever as
    negative knowledge so reflection never reinvents a rejected behavior.
  - Everything persists to a human-readable ``habits.json`` in the workspace,
    and each receipt records which habit drove it, so the whole system stays
    user-inspectable.

Like the memory system, all LLM use is optional: without an ``llm_complete``
no habits are ever invented, but existing ones keep working.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from loguru import logger

from .directions import ASK, Candidate, Direction, SILENT, SPEAK
from .observation import Observation

_STORE_FILENAME = "habits.json"
_SCHEMA_VERSION = 1

# Lifecycle states.
TRIAL = "trial"
ESTABLISHED = "established"
RETIRED = "retired"

# Repertoire bounds.
MAX_ACTIVE_HABITS = 8  # trial + established
MAX_TRIAL_HABITS = 3

# Promotion / demotion evidence thresholds.
ESTABLISH_HELPFUL_COUNT = 2  # helpful verdicts to promote a trial habit
ESTABLISH_SILENT_RUNS = 4  # quiet survivals to promote a silent trial habit
RETIRE_DISMISSED_COUNT = 2  # dismissals to retire a habit at any stage

# Skepticism prior applied to trial habits.
TRIAL_USEFULNESS_CAP = 0.5
TRIAL_RISK_BOOST = 0.15

_VALID_DELIVERIES = {SILENT, SPEAK, ASK}
_VALID_DIRECTIONS = {d.value for d in Direction}

LLMCompleteFn = Callable[..., Awaitable[str]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _normalize_prompt(prompt: str) -> str:
    """Normalize a habit prompt for duplicate detection."""
    return re.sub(r"[^a-z0-9]+", " ", prompt.lower()).strip()


# --------------------------------------------------------------------------- #
# Declarative relevance gates
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RelevanceGates:
    """Context conditions under which a habit applies.

    Purely declarative so learned habits never carry executable logic; the
    planner-facing relevance function is synthesized here from these fields.
    Unset fields (None) mean "no constraint".
    """

    requires_user_present: bool = True
    min_idle_seconds: Optional[float] = None
    max_idle_seconds: Optional[float] = None
    # Hour-of-day window [hour_start, hour_end); wraps midnight when
    # hour_start > hour_end (e.g. 22 -> 6 means "night").
    hour_start: Optional[int] = None
    hour_end: Optional[int] = None
    # Minimum seconds since the agent last spoke (quiet period).
    min_quiet_seconds: Optional[float] = None

    def evaluate(self, obs: Observation) -> float:
        """Return a relevance multiplier in [0, 1] for the observation.

        Unknown signals (None in the observation) are treated leniently with a
        dampened 0.5 rather than a hard 0, matching how the built-in
        candidates degrade gracefully on missing context.
        """
        if self.requires_user_present and not obs.user_present:
            return 0.0

        relevance = 1.0

        if self.min_idle_seconds is not None or self.max_idle_seconds is not None:
            if obs.idle_seconds is None:
                relevance *= 0.5
            else:
                if (
                    self.min_idle_seconds is not None
                    and obs.idle_seconds < self.min_idle_seconds
                ):
                    return 0.0
                if (
                    self.max_idle_seconds is not None
                    and obs.idle_seconds > self.max_idle_seconds
                ):
                    return 0.0

        if self.hour_start is not None and self.hour_end is not None:
            if obs.hour_of_day is None:
                relevance *= 0.5
            elif self.hour_start <= self.hour_end:
                if not (self.hour_start <= obs.hour_of_day < self.hour_end):
                    return 0.0
            else:  # window wraps midnight
                if not (
                    obs.hour_of_day >= self.hour_start
                    or obs.hour_of_day < self.hour_end
                ):
                    return 0.0

        if self.min_quiet_seconds is not None:
            if obs.seconds_since_last_spoke is None:
                relevance *= 0.5
            elif obs.seconds_since_last_spoke < self.min_quiet_seconds:
                return 0.0

        return relevance

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RelevanceGates":
        def _num(key: str) -> Optional[float]:
            value = raw.get(key)
            if value is None or isinstance(value, bool):
                return None
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                return None

        def _hour(key: str) -> Optional[int]:
            value = raw.get(key)
            if value is None or isinstance(value, bool):
                return None
            try:
                return max(0, min(23, int(value)))
            except (TypeError, ValueError):
                return None

        return cls(
            requires_user_present=bool(raw.get("requires_user_present", True)),
            min_idle_seconds=_num("min_idle_seconds"),
            max_idle_seconds=_num("max_idle_seconds"),
            hour_start=_hour("hour_start"),
            hour_end=_hour("hour_end"),
            min_quiet_seconds=_num("min_quiet_seconds"),
        )


# --------------------------------------------------------------------------- #
# LearnedHabit
# --------------------------------------------------------------------------- #
@dataclass
class LearnedHabit:
    """One autonomous behavior the agent invented and is (or was) practicing."""

    habit_id: str
    name: str
    direction: str  # a Direction value; habits map onto existing directions
    prompt: str
    usefulness: float
    intrusiveness: float
    risk: float
    delivery: str  # silent / speak / ask
    gates: RelevanceGates = field(default_factory=RelevanceGates)
    status: str = TRIAL
    origin: str = "reflection"
    created_at: str = field(default_factory=_utc_now_iso)
    retired_reason: Optional[str] = None
    # Evidence.
    runs: int = 0
    helpful_count: int = 0
    dismissed_count: int = 0
    last_run_at: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.status in (TRIAL, ESTABLISHED)

    def to_candidate(self) -> Candidate:
        """Compile this habit into a planner candidate.

        Trial habits carry a skepticism prior: capped usefulness, boosted
        risk, and no outright proactive speech (downgraded to asking).
        """
        usefulness = self.usefulness
        risk = self.risk
        delivery = self.delivery
        if self.status == TRIAL:
            usefulness = min(usefulness, TRIAL_USEFULNESS_CAP)
            risk = _clamp(risk + TRIAL_RISK_BOOST)
            if delivery == SPEAK:
                delivery = ASK
        return Candidate(
            direction=Direction(self.direction),
            prompt=self.prompt,
            usefulness=usefulness,
            intrusiveness=self.intrusiveness,
            risk=risk,
            delivery=delivery,
            relevance=self.gates.evaluate,
            habit_id=self.habit_id,
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["gates"] = self.gates.to_dict()
        return data

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Optional["LearnedHabit"]:
        try:
            direction = str(raw["direction"])
            delivery = str(raw["delivery"])
            if direction not in _VALID_DIRECTIONS:
                return None
            if delivery not in _VALID_DELIVERIES:
                return None
            status = str(raw.get("status", TRIAL))
            if status not in (TRIAL, ESTABLISHED, RETIRED):
                status = TRIAL
            return cls(
                habit_id=str(raw["habit_id"]),
                name=str(raw.get("name", ""))[:60],
                direction=direction,
                prompt=str(raw["prompt"]),
                usefulness=_clamp(float(raw.get("usefulness", 0.4))),
                intrusiveness=_clamp(float(raw.get("intrusiveness", 0.3))),
                risk=_clamp(float(raw.get("risk", 0.3))),
                delivery=delivery,
                gates=RelevanceGates.from_dict(dict(raw.get("gates") or {})),
                status=status,
                origin=str(raw.get("origin", "reflection")),
                created_at=str(raw.get("created_at") or _utc_now_iso()),
                retired_reason=raw.get("retired_reason"),
                runs=max(0, int(raw.get("runs", 0))),
                helpful_count=max(0, int(raw.get("helpful_count", 0))),
                dismissed_count=max(0, int(raw.get("dismissed_count", 0))),
                last_run_at=raw.get("last_run_at"),
            )
        except (KeyError, TypeError, ValueError):
            return None


# --------------------------------------------------------------------------- #
# HabitStore
# --------------------------------------------------------------------------- #
class HabitStore:
    """Persistent collection of learned habits with an evidence-based lifecycle.

    Lifecycle:
        trial -> established   after enough positive evidence
        any   -> retired       after repeated dismissals or a user request

    Retired habits are never deleted: they are the agent's negative knowledge
    and are fed back into reflection so rejected behaviors aren't reinvented.
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._habits: list[LearnedHabit] = []

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls, workspace_root: Path) -> "HabitStore":
        path = workspace_root / _STORE_FILENAME
        store = cls(path)
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                for item in raw.get("habits", []):
                    if not isinstance(item, dict):
                        continue
                    habit = LearnedHabit.from_dict(item)
                    if habit is not None:
                        store._habits.append(habit)
            except Exception as exc:  # noqa: BLE001 - corrupt state is recoverable
                logger.warning(f"habits: could not read {path} ({exc}); starting fresh")
        return store

    def save(self) -> None:
        if self._path is None:
            return
        data = {
            "schema_version": _SCHEMA_VERSION,
            "habits": [habit.to_dict() for habit in self._habits],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:  # noqa: BLE001 - persistence is best effort
            logger.warning(f"habits: failed to save state: {exc}")

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #
    @property
    def habits(self) -> list[LearnedHabit]:
        return list(self._habits)

    def get(self, habit_id: str) -> Optional[LearnedHabit]:
        return next((h for h in self._habits if h.habit_id == habit_id), None)

    def active_habits(self) -> list[LearnedHabit]:
        return [h for h in self._habits if h.is_active]

    def retired_habits(self) -> list[LearnedHabit]:
        return [h for h in self._habits if h.status == RETIRED]

    def active_candidates(self) -> list[Candidate]:
        """The planner-ready candidates for every live habit."""
        return [habit.to_candidate() for habit in self.active_habits()]

    def snapshot(self) -> dict[str, Any]:
        """Frontend-safe state for the habit transparency panel."""
        return {
            "type": "habit-state",
            "enabled": True,
            "max_active": MAX_ACTIVE_HABITS,
            "habits": [habit.to_dict() for habit in self._habits],
        }

    # ------------------------------------------------------------------ #
    # Mutations
    # ------------------------------------------------------------------ #
    def add(self, habit: LearnedHabit) -> tuple[bool, str]:
        """Admit a new habit, enforcing dedupe and repertoire caps."""
        key = _normalize_prompt(habit.prompt)
        if not key:
            return False, "habit prompt is empty"
        for existing in self._habits:  # includes retired: negative knowledge
            if _normalize_prompt(existing.prompt) == key:
                return False, f"duplicate of habit {existing.habit_id}"

        active = self.active_habits()
        if len(active) >= MAX_ACTIVE_HABITS:
            return False, "active habit cap reached"
        if (
            habit.status == TRIAL
            and sum(1 for h in active if h.status == TRIAL) >= MAX_TRIAL_HABITS
        ):
            return False, "trial habit cap reached"

        self._habits.append(habit)
        self.save()
        logger.info(
            f"habits: adopted new {habit.status} habit '{habit.name}' "
            f"({habit.direction}/{habit.delivery}): {habit.prompt[:80]}"
        )
        return True, "habit adopted"

    def record_run(self, habit_id: str, now: Optional[datetime] = None) -> None:
        """Record one execution; silent trial habits earn promotion by surviving."""
        habit = self.get(habit_id)
        if habit is None or not habit.is_active:
            return
        habit.runs += 1
        habit.last_run_at = (now or datetime.now(timezone.utc)).isoformat(
            timespec="seconds"
        )
        if (
            habit.status == TRIAL
            and habit.delivery == SILENT
            and habit.runs >= ESTABLISH_SILENT_RUNS
            and habit.dismissed_count == 0
        ):
            habit.status = ESTABLISHED
            logger.info(f"habits: '{habit.name}' established after quiet trial runs")
        self.save()

    def apply_feedback(self, habit_id: str, feedback: str) -> None:
        """Fold a receipt verdict (helpful/dismissed/snoozed) into the habit."""
        habit = self.get(habit_id)
        if habit is None or not habit.is_active:
            return
        if feedback == "helpful":
            habit.helpful_count += 1
            if habit.status == TRIAL and habit.helpful_count >= ESTABLISH_HELPFUL_COUNT:
                habit.status = ESTABLISHED
                logger.info(f"habits: '{habit.name}' established via user feedback")
        elif feedback == "dismissed":
            habit.dismissed_count += 1
            if habit.dismissed_count >= RETIRE_DISMISSED_COUNT:
                habit.status = RETIRED
                habit.retired_reason = "dismissed repeatedly by the user"
                logger.info(f"habits: '{habit.name}' retired after dismissals")
        # snoozed is a mood signal, not a verdict on the habit itself.
        self.save()

    def retire(self, habit_id: str, reason: str = "retired by the user") -> bool:
        habit = self.get(habit_id)
        if habit is None or habit.status == RETIRED:
            return False
        habit.status = RETIRED
        habit.retired_reason = reason
        self.save()
        logger.info(f"habits: '{habit.name}' retired ({reason})")
        return True


# --------------------------------------------------------------------------- #
# Shared store registry (mirrors the presence ledger: one store per workspace)
# --------------------------------------------------------------------------- #
_stores_by_root: dict[str, HabitStore] = {}


def get_or_create_habit_store(workspace_root: Path) -> HabitStore:
    key = str(workspace_root.resolve())
    store = _stores_by_root.get(key)
    if store is None:
        store = HabitStore.load(workspace_root)
        _stores_by_root[key] = store
    return store


# --------------------------------------------------------------------------- #
# Reflection: invent a new habit from lived experience
# --------------------------------------------------------------------------- #
_REFLECTION_SYSTEM = (
    "You are the self-improvement process of an embodied AI companion. From the "
    "evidence of the companion's recent autonomous actions, the user's feedback, "
    "and its memory, decide whether ONE genuinely new autonomous habit is "
    "warranted. A habit is a short standing instruction the companion will "
    "occasionally act on by itself.\n\n"
    "Rules:\n"
    "- Propose a habit only if the evidence suggests it would be welcome.\n"
    "- Never repropose anything similar to a retired habit or an existing one.\n"
    "- Prefer quiet, low-intrusiveness habits unless feedback shows the user "
    "enjoys being spoken to.\n"
    "- Respond with ONLY a JSON object (no prose, no code fences) with exactly "
    "these keys: name (short), direction (one of: curiosity, care, "
    "housekeeping, growth, social, work), prompt (the standing instruction, "
    "one to three sentences, second person), usefulness (0-1), intrusiveness "
    "(0-1), risk (0-1), delivery (one of: silent, ask, speak), gates (object "
    "with optional keys: requires_user_present (bool), min_idle_seconds, "
    "max_idle_seconds, hour_start (0-23), hour_end (0-23), "
    "min_quiet_seconds).\n"
    "- If no new habit is warranted, respond with the single word NONE."
)


def build_reflection_prompt(
    receipts: list[dict[str, Any]],
    memory_excerpt: str,
    store: HabitStore,
) -> str:
    """Assemble the evidence bundle the reflection LLM reasons over."""
    lines: list[str] = ["## Recent autonomous actions (newest last)"]
    if receipts:
        for receipt in receipts[-20:]:
            lines.append(
                "- direction={direction} action={action} feedback={feedback} "
                "signals={signals}".format(
                    direction=receipt.get("direction"),
                    action=receipt.get("action"),
                    feedback=receipt.get("feedback") or "none",
                    signals=json.dumps(receipt.get("signals", {}), ensure_ascii=False),
                )
            )
    else:
        lines.append("- (none yet)")

    lines.append("\n## Current habits (do not duplicate)")
    active = store.active_habits()
    if active:
        for habit in active:
            lines.append(f"- [{habit.status}] {habit.name}: {habit.prompt}")
    else:
        lines.append("- (none)")

    lines.append("\n## Retired habits (rejected — never repropose these)")
    retired = store.retired_habits()
    if retired:
        for habit in retired:
            lines.append(f"- {habit.name}: {habit.prompt}")
    else:
        lines.append("- (none)")

    if memory_excerpt.strip():
        lines.append("\n## Memory excerpt")
        lines.append(memory_excerpt.strip()[:3000])

    lines.append(
        "\nPropose one new habit as JSON, or answer NONE if nothing is warranted."
    )
    return "\n".join(lines)


def parse_habit_proposal(text: str) -> Optional[LearnedHabit]:
    """Parse and strictly validate an LLM habit proposal.

    Returns None for NONE answers, malformed JSON, unknown directions or
    deliveries, or prompts outside sane length bounds. All numeric fields are
    clamped rather than trusted.
    """
    cleaned = text.strip()
    if not cleaned or cleaned.upper().startswith("NONE"):
        return None

    # Tolerate accidental code fences or surrounding prose: take the first
    # top-level {...} block.
    match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if match is None:
        return None
    try:
        raw = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None

    direction = str(raw.get("direction", "")).strip().lower()
    delivery = str(raw.get("delivery", "")).strip().lower()
    prompt = str(raw.get("prompt", "")).strip()
    name = str(raw.get("name", "")).strip()[:60]

    if direction not in _VALID_DIRECTIONS:
        return None
    if delivery not in _VALID_DELIVERIES:
        return None
    if not (20 <= len(prompt) <= 600):
        return None
    if not name:
        name = prompt[:40]

    def _score(key: str, default: float) -> float:
        try:
            return _clamp(float(raw.get(key, default)))
        except (TypeError, ValueError):
            return default

    gates_raw = raw.get("gates")
    gates = RelevanceGates.from_dict(
        dict(gates_raw) if isinstance(gates_raw, dict) else {}
    )

    return LearnedHabit(
        habit_id=uuid4().hex,
        name=name,
        direction=direction,
        prompt=prompt,
        usefulness=_score("usefulness", 0.4),
        intrusiveness=_score("intrusiveness", 0.3),
        risk=_score("risk", 0.3),
        delivery=delivery,
        gates=gates,
        status=TRIAL,
    )


async def reflect_and_propose(
    llm_complete: LLMCompleteFn,
    store: HabitStore,
    receipts: list[dict[str, Any]],
    memory_excerpt: str,
) -> Optional[LearnedHabit]:
    """Run one reflection pass; adopt and return the new habit, if any."""
    prompt = build_reflection_prompt(receipts, memory_excerpt, store)
    try:
        answer = await llm_complete(
            [{"role": "user", "content": prompt}], _REFLECTION_SYSTEM
        )
    except Exception as exc:  # noqa: BLE001 - reflection must never crash autonomy
        logger.error(f"habits: reflection LLM call failed: {exc}")
        return None

    habit = parse_habit_proposal(answer or "")
    if habit is None:
        logger.debug("habits: reflection proposed no new habit")
        return None

    ok, message = store.add(habit)
    if not ok:
        logger.debug(f"habits: reflection proposal rejected: {message}")
        return None
    return habit
