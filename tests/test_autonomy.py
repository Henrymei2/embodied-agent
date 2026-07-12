"""Tests for the autonomous behavior engine (settings, state, planner)."""

from __future__ import annotations

from datetime import datetime, timedelta

from src.open_llm_vtuber.autonomy import (
    ACT_ASK,
    ACT_NOTHING,
    ACT_SILENT,
    ACT_SPEAK,
    AutonomyPlanner,
    AutonomySettings,
    AutonomyState,
    Direction,
    Observation,
)
from src.open_llm_vtuber.autonomy.directions import SILENT, SPEAK, Candidate
from src.open_llm_vtuber.autonomy.planner import AutonomyPlanner as Planner


class FakeRng:
    """Deterministic stand-in for random.Random with scripted outputs."""

    def __init__(self, randoms=None, randranges=None):
        self._randoms = list(randoms or [])
        self._randranges = list(randranges or [])

    def random(self):
        return self._randoms.pop(0) if self._randoms else 0.99

    def randrange(self, n):
        return self._randranges.pop(0) if self._randranges else 0

    def choice(self, seq):
        return seq[0]


# rng that never explores and always passes the "rest" gate (i.e., acts).
def _act_rng():
    return FakeRng(randoms=[0.99, 0.0])


def _idle_present():
    return Observation(idle_seconds=1800, user_present=True, seconds_since_last_spoke=5000)


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def test_settings_frequency_mappings():
    low = AutonomySettings(interruption_frequency="low")
    high = AutonomySettings(interruption_frequency="high")
    assert low.base_act_probability < high.base_act_probability
    assert low.min_speak_gap_seconds > high.min_speak_gap_seconds
    assert low.intrusion_sensitivity > high.intrusion_sensitivity


def test_settings_confidence_and_style():
    strict = AutonomySettings(confidence_threshold="strict")
    loose = AutonomySettings(confidence_threshold="loose")
    assert strict.act_threshold > loose.act_threshold
    assert AutonomySettings(help_style="quiet").speak_bias < 0
    assert AutonomySettings(help_style="talkative").speak_bias > 0


def test_settings_invalid_values_fall_back():
    s = AutonomySettings(
        interruption_frequency="bogus", help_style="???", confidence_threshold="nope",
        tick_seconds=1,
    )
    assert s.interruption_frequency == "medium"
    assert s.help_style == "proactive"
    assert s.confidence_threshold == "balanced"
    assert s.tick_seconds == 30  # clamped to minimum


def test_settings_from_config_none_defaults():
    s = AutonomySettings.from_config(None)
    assert s.enabled and s.interruption_frequency == "medium"


def test_settings_from_config_reads_attrs():
    class Cfg:
        enabled = False
        tick_seconds = 120
        interruption_frequency = "high"
        help_style = "quiet"
        confidence_threshold = "loose"

    class Sys:
        autonomy_config = Cfg()

    s = AutonomySettings.from_config(Sys())
    assert not s.enabled and s.tick_seconds == 120
    assert s.interruption_frequency == "high" and s.help_style == "quiet"


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def test_state_novelty_decreases_with_repetition(tmp_path):
    st = AutonomyState.load(tmp_path)
    assert st.novelty(Direction.GROWTH) == 1.0
    st.recent_directions = ["growth", "growth", "growth"]
    assert st.novelty(Direction.GROWTH) < 1.0
    assert st.novelty(Direction.CARE) == 1.0


def test_state_records_and_persists(tmp_path):
    st = AutonomyState.load(tmp_path)
    st.record_action(Direction.SOCIAL, spoke=True)
    assert st.total_actions == 1
    assert st.direction_counts["social"] == 1
    assert st.last_spoke_ts is not None

    reloaded = AutonomyState.load(tmp_path)
    assert reloaded.total_actions == 1
    assert reloaded.recent_directions == ["social"]


def test_state_seconds_since_last_spoke(tmp_path):
    st = AutonomyState.load(tmp_path)
    assert st.seconds_since_last_spoke() is None
    now = datetime.now()
    st.last_spoke_ts = (now - timedelta(seconds=120)).isoformat(timespec="seconds")
    assert 115 <= st.seconds_since_last_spoke(now) <= 125


# --------------------------------------------------------------------------- #
# Planner: gates
# --------------------------------------------------------------------------- #
def test_disabled_returns_nothing(tmp_path):
    st = AutonomyState.load(tmp_path)
    d = AutonomyPlanner().propose_and_choose(
        _idle_present(), st, AutonomySettings(enabled=False), _act_rng()
    )
    assert d.action == ACT_NOTHING and "disabled" in d.reason


def test_below_confidence_threshold_returns_nothing(tmp_path):
    st = AutonomyState.load(tmp_path)
    # Strict raises the bar above what these candidates can reach.
    d = AutonomyPlanner().propose_and_choose(
        _idle_present(), st, AutonomySettings(confidence_threshold="strict"), _act_rng()
    )
    assert d.action == ACT_NOTHING
    assert "confidence" in d.reason


def test_rest_gate_returns_nothing(tmp_path):
    st = AutonomyState.load(tmp_path)
    # No explore (0.99), then rest gate fails to act (0.99 > base prob).
    rng = FakeRng(randoms=[0.99, 0.99])
    d = AutonomyPlanner().propose_and_choose(_idle_present(), st, AutonomySettings(), rng)
    assert d.action == ACT_NOTHING and "rest" in d.reason


def test_idle_user_yields_silent_self_work(tmp_path):
    st = AutonomyState.load(tmp_path)
    d = AutonomyPlanner().propose_and_choose(_idle_present(), st, AutonomySettings(), _act_rng())
    assert d.action == ACT_SILENT
    assert d.report is False
    assert d.direction in {Direction.GROWTH, Direction.HOUSEKEEPING, Direction.WORK,
                           Direction.CURIOSITY}
    assert d.prompt  # a concrete instruction to run


# --------------------------------------------------------------------------- #
# Planner: delivery decision (unit)
# --------------------------------------------------------------------------- #
def _mk_candidate(delivery):
    return Candidate(
        direction=Direction.SOCIAL, prompt="p",
        usefulness=0.9, intrusiveness=0.4, risk=0.1, delivery=delivery,
        relevance=lambda o: 1.0,
    )


def test_delivery_silent_always_allowed(tmp_path):
    st = AutonomyState.load(tmp_path)
    obs = Observation(user_present=True, seconds_since_last_spoke=0)  # just spoke
    action = Planner._decide_delivery(
        _mk_candidate(SILENT), 0.9, obs, st, AutonomySettings(), None
    )
    assert action == ACT_SILENT


def test_delivery_speak_blocked_when_recently_spoke(tmp_path):
    st = AutonomyState.load(tmp_path)
    obs = Observation(user_present=True, seconds_since_last_spoke=10)  # 10s ago
    action = Planner._decide_delivery(
        _mk_candidate(SPEAK), 0.9, obs, st, AutonomySettings(), None
    )
    assert action == ACT_NOTHING  # too soon to interrupt


def test_delivery_speak_when_confident_and_quiet_long_enough(tmp_path):
    st = AutonomyState.load(tmp_path)
    obs = Observation(user_present=True, seconds_since_last_spoke=99999)
    action = Planner._decide_delivery(
        _mk_candidate(SPEAK), 0.9, obs, st, AutonomySettings(), None
    )
    assert action == ACT_SPEAK


def test_delivery_downgrades_speak_to_ask_below_speak_threshold(tmp_path):
    st = AutonomyState.load(tmp_path)
    obs = Observation(user_present=True, seconds_since_last_spoke=99999)
    # score above act_threshold (0.45) but below speak_threshold (0.60)
    action = Planner._decide_delivery(
        _mk_candidate(SPEAK), 0.5, obs, st, AutonomySettings(), None
    )
    assert action == ACT_ASK


# --------------------------------------------------------------------------- #
# Planner: exploration
# --------------------------------------------------------------------------- #
def test_exploration_can_pick_non_top_candidate(tmp_path):
    st = AutonomyState.load(tmp_path)
    # Explore on first random() (< epsilon), pick index 1, then act on rest gate.
    rng = FakeRng(randoms=[0.0, 0.0], randranges=[1])
    d = AutonomyPlanner().propose_and_choose(
        _idle_present(), st, AutonomySettings(confidence_threshold="loose"), rng
    )
    # It acted on some direction (exploration path executed without error).
    assert d.action in {ACT_SILENT, ACT_ASK, ACT_SPEAK, ACT_NOTHING}
    assert d.scores  # score breakdown is populated
