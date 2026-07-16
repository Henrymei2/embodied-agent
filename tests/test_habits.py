"""Tests for the Habit Engine (learned autonomous behaviors)."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from src.open_llm_vtuber.autonomy import (
    AutonomyPlanner,
    AutonomySettings,
    AutonomyState,
    HabitStore,
    LearnedHabit,
    Observation,
    RelevanceGates,
    parse_habit_proposal,
    reflect_and_propose,
)
from src.open_llm_vtuber.autonomy.directions import ASK, SILENT, SPEAK
from src.open_llm_vtuber.autonomy.habits import (
    ESTABLISH_HELPFUL_COUNT,
    ESTABLISH_SILENT_RUNS,
    ESTABLISHED,
    MAX_ACTIVE_HABITS,
    MAX_TRIAL_HABITS,
    RETIRE_DISMISSED_COUNT,
    RETIRED,
    TRIAL,
    TRIAL_USEFULNESS_CAP,
)


def _habit(**overrides) -> LearnedHabit:
    base = dict(
        habit_id=uuid4().hex,
        name="test habit",
        direction="curiosity",
        prompt=f"Do a small helpful autonomous thing ({uuid4().hex}).",
        usefulness=0.9,
        intrusiveness=0.1,
        risk=0.1,
        delivery=SILENT,
        gates=RelevanceGates(),
        status=TRIAL,
    )
    base.update(overrides)
    return LearnedHabit(**base)


# --------------------------------------------------------------------------- #
# Relevance gates
# --------------------------------------------------------------------------- #
def test_gates_user_presence_required():
    gates = RelevanceGates(requires_user_present=True)
    assert gates.evaluate(Observation(user_present=False)) == 0.0
    assert gates.evaluate(Observation(user_present=True)) == 1.0


def test_gates_idle_bounds():
    gates = RelevanceGates(min_idle_seconds=300, max_idle_seconds=3600)
    assert gates.evaluate(Observation(idle_seconds=100)) == 0.0
    assert gates.evaluate(Observation(idle_seconds=600)) == 1.0
    assert gates.evaluate(Observation(idle_seconds=7200)) == 0.0
    # Unknown idle dampens instead of hard-zeroing.
    assert gates.evaluate(Observation(idle_seconds=None)) == 0.5


def test_gates_hour_window_wraps_midnight():
    night = RelevanceGates(hour_start=22, hour_end=6)
    assert night.evaluate(Observation(hour_of_day=23)) == 1.0
    assert night.evaluate(Observation(hour_of_day=3)) == 1.0
    assert night.evaluate(Observation(hour_of_day=12)) == 0.0

    day = RelevanceGates(hour_start=9, hour_end=17)
    assert day.evaluate(Observation(hour_of_day=10)) == 1.0
    assert day.evaluate(Observation(hour_of_day=20)) == 0.0


def test_gates_quiet_period():
    gates = RelevanceGates(min_quiet_seconds=1800)
    assert gates.evaluate(Observation(seconds_since_last_spoke=60)) == 0.0
    assert gates.evaluate(Observation(seconds_since_last_spoke=3600)) == 1.0


# --------------------------------------------------------------------------- #
# Trial skepticism prior
# --------------------------------------------------------------------------- #
def test_trial_candidate_carries_skepticism_prior():
    habit = _habit(usefulness=0.9, risk=0.1, delivery=SPEAK, status=TRIAL)
    candidate = habit.to_candidate()
    assert candidate.usefulness == TRIAL_USEFULNESS_CAP
    assert candidate.risk > 0.1
    assert candidate.delivery == ASK  # trial habits may not speak outright
    assert candidate.habit_id == habit.habit_id


def test_established_candidate_uses_full_scores():
    habit = _habit(usefulness=0.9, risk=0.1, delivery=SPEAK, status=ESTABLISHED)
    candidate = habit.to_candidate()
    assert candidate.usefulness == 0.9
    assert candidate.risk == 0.1
    assert candidate.delivery == SPEAK


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_helpful_feedback_establishes_trial_habit(tmp_path):
    store = HabitStore.load(tmp_path)
    habit = _habit(delivery=ASK)
    assert store.add(habit)[0]
    for _ in range(ESTABLISH_HELPFUL_COUNT):
        store.apply_feedback(habit.habit_id, "helpful")
    assert store.get(habit.habit_id).status == ESTABLISHED


def test_dismissals_retire_habit(tmp_path):
    store = HabitStore.load(tmp_path)
    habit = _habit(delivery=ASK)
    store.add(habit)
    for _ in range(RETIRE_DISMISSED_COUNT):
        store.apply_feedback(habit.habit_id, "dismissed")
    retired = store.get(habit.habit_id)
    assert retired.status == RETIRED
    assert retired.retired_reason
    assert habit.habit_id not in {c.habit_id for c in store.active_candidates()}


def test_silent_habit_establishes_by_surviving_runs(tmp_path):
    store = HabitStore.load(tmp_path)
    habit = _habit(delivery=SILENT)
    store.add(habit)
    for _ in range(ESTABLISH_SILENT_RUNS):
        store.record_run(habit.habit_id)
    assert store.get(habit.habit_id).status == ESTABLISHED


def test_user_retire_and_snoozed_neutrality(tmp_path):
    store = HabitStore.load(tmp_path)
    habit = _habit()
    store.add(habit)
    store.apply_feedback(habit.habit_id, "snoozed")  # mood signal, no verdict
    assert store.get(habit.habit_id).status == TRIAL
    assert store.retire(habit.habit_id)
    assert store.get(habit.habit_id).status == RETIRED
    assert not store.retire(habit.habit_id)  # already retired


# --------------------------------------------------------------------------- #
# Caps and dedupe
# --------------------------------------------------------------------------- #
def test_duplicate_prompts_rejected_even_after_retirement(tmp_path):
    store = HabitStore.load(tmp_path)
    prompt = "Water the virtual plants in the scene every quiet evening."
    first = _habit(prompt=prompt)
    assert store.add(first)[0]
    store.retire(first.habit_id)
    ok, message = store.add(_habit(prompt=prompt))
    assert not ok  # retired habits are negative knowledge
    assert "duplicate" in message


def test_trial_and_active_caps(tmp_path):
    store = HabitStore.load(tmp_path)
    for _ in range(MAX_TRIAL_HABITS):
        assert store.add(_habit(status=TRIAL))[0]
    ok, message = store.add(_habit(status=TRIAL))
    assert not ok and "trial" in message

    while len(store.active_habits()) < MAX_ACTIVE_HABITS:
        assert store.add(_habit(status=ESTABLISHED))[0]
    ok, message = store.add(_habit(status=ESTABLISHED))
    assert not ok and "cap" in message


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def test_store_round_trips_through_disk(tmp_path):
    store = HabitStore.load(tmp_path)
    habit = _habit(
        delivery=ASK,
        gates=RelevanceGates(min_idle_seconds=300, hour_start=9, hour_end=17),
    )
    store.add(habit)
    store.apply_feedback(habit.habit_id, "helpful")

    reloaded = HabitStore.load(tmp_path)
    loaded = reloaded.get(habit.habit_id)
    assert loaded is not None
    assert loaded.helpful_count == 1
    assert loaded.gates.min_idle_seconds == 300
    assert loaded.gates.hour_start == 9


def test_corrupt_store_starts_fresh(tmp_path):
    (tmp_path / "habits.json").write_text("{not json", encoding="utf-8")
    store = HabitStore.load(tmp_path)
    assert store.habits == []


# --------------------------------------------------------------------------- #
# Proposal parsing (the LLM boundary)
# --------------------------------------------------------------------------- #
def _valid_proposal(**overrides) -> str:
    payload = {
        "name": "sunset check-in",
        "direction": "social",
        "prompt": "In the evening, briefly ask the user how their day went.",
        "usefulness": 0.6,
        "intrusiveness": 0.4,
        "risk": 0.2,
        "delivery": "ask",
        "gates": {"hour_start": 18, "hour_end": 22, "min_quiet_seconds": 1800},
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_parse_valid_proposal():
    habit = parse_habit_proposal(_valid_proposal())
    assert habit is not None
    assert habit.status == TRIAL
    assert habit.direction == "social"
    assert habit.delivery == ASK
    assert habit.gates.hour_start == 18


def test_parse_tolerates_fences_and_clamps_scores():
    text = "```json\n" + _valid_proposal(usefulness=7, risk=-3) + "\n```"
    habit = parse_habit_proposal(text)
    assert habit is not None
    assert habit.usefulness == 1.0
    assert habit.risk == 0.0


def test_parse_rejects_bad_proposals():
    assert parse_habit_proposal("NONE") is None
    assert parse_habit_proposal("") is None
    assert parse_habit_proposal("not json at all") is None
    assert parse_habit_proposal(_valid_proposal(direction="world_domination")) is None
    assert parse_habit_proposal(_valid_proposal(delivery="shout")) is None
    assert parse_habit_proposal(_valid_proposal(prompt="hi")) is None  # too short


# --------------------------------------------------------------------------- #
# Reflection end-to-end (fake LLM)
# --------------------------------------------------------------------------- #
def test_reflect_and_propose_adopts_valid_habit(tmp_path):
    store = HabitStore.load(tmp_path)

    async def fake_llm(messages, system=""):
        assert "Recent autonomous actions" in messages[0]["content"]
        return _valid_proposal()

    habit = asyncio.run(
        reflect_and_propose(
            fake_llm,
            store,
            receipts=[
                {"direction": "social", "action": "speak", "feedback": "helpful"}
            ],
            memory_excerpt="- user likes evening chats",
        )
    )
    assert habit is not None
    assert store.get(habit.habit_id) is not None


def test_reflect_handles_none_and_llm_failure(tmp_path):
    store = HabitStore.load(tmp_path)

    async def says_none(messages, system=""):
        return "NONE"

    async def blows_up(messages, system=""):
        raise RuntimeError("provider down")

    assert asyncio.run(reflect_and_propose(says_none, store, [], "")) is None
    assert asyncio.run(reflect_and_propose(blows_up, store, [], "")) is None
    assert store.habits == []


def test_reflection_prompt_lists_retired_habits(tmp_path):
    store = HabitStore.load(tmp_path)
    habit = _habit(name="pun of the day", prompt="Tell the user a pun every hour.")
    store.add(habit)
    store.retire(habit.habit_id)

    from src.open_llm_vtuber.autonomy.habits import build_reflection_prompt

    prompt = build_reflection_prompt([], "", store)
    assert "never repropose" in prompt
    assert "pun of the day" in prompt


# --------------------------------------------------------------------------- #
# Planner integration
# --------------------------------------------------------------------------- #
class _ActRng:
    """Never explores; always passes the rest gate."""

    def random(self):
        return 0.0 if not hasattr(self, "_first") else 0.0

    def randrange(self, n):
        return 0


def test_planner_can_choose_learned_habit_and_reports_habit_id():
    habit = _habit(
        usefulness=1.0,
        intrusiveness=0.0,
        risk=0.0,
        delivery=SILENT,
        status=ESTABLISHED,
    )
    planner = AutonomyPlanner()
    settings = AutonomySettings(confidence_threshold="loose")

    class NoExploreRng:
        def __init__(self):
            self._values = [0.99, 0.0]  # skip exploration, then act

        def random(self):
            return self._values.pop(0) if self._values else 0.0

        def randrange(self, n):
            return 0

    decision = planner.propose_and_choose(
        Observation(idle_seconds=1800, user_present=True),
        AutonomyState(),
        settings,
        rng=NoExploreRng(),
        extra_candidates=[habit.to_candidate()],
    )
    assert decision.is_act
    assert decision.habit_id == habit.habit_id
    assert decision.prompt == habit.prompt


def test_planner_without_extra_candidates_unchanged():
    planner = AutonomyPlanner()
    decision = planner.propose_and_choose(
        Observation(idle_seconds=1800, user_present=True),
        AutonomyState(),
        AutonomySettings(),
    )
    assert decision.habit_id is None
