"""Tests for the soft-evolution companion state."""

from __future__ import annotations

from datetime import date, timedelta

from src.open_llm_vtuber.memory.evolution import EvolutionState


def test_starts_as_stranger_with_neutral_mood(tmp_path):
    state = EvolutionState.load(tmp_path)
    assert state.familiarity_label == "stranger"
    assert state.familiarity_tier == 0
    assert state.mood == 0.0
    assert state.total_interactions == 0


def test_interactions_accrue_and_persist(tmp_path):
    state = EvolutionState.load(tmp_path)
    for _ in range(5):
        state.record_interaction("hello there")
    assert state.total_interactions == 5
    assert state.familiarity_points == 5

    # Reloading from disk preserves the accrued state.
    reloaded = EvolutionState.load(tmp_path)
    assert reloaded.total_interactions == 5
    assert reloaded.familiarity_points == 5


def test_positive_sentiment_lifts_mood_negative_lowers(tmp_path):
    pos = EvolutionState.load(tmp_path / "pos")
    for _ in range(10):
        pos.record_interaction("thanks, this is awesome and great")
    assert pos.mood > 0.2

    neg = EvolutionState.load(tmp_path / "neg")
    for _ in range(10):
        neg.record_interaction("this is terrible and stupid, i hate it")
    assert neg.mood < -0.2


def test_mood_stays_bounded(tmp_path):
    state = EvolutionState.load(tmp_path)
    for _ in range(500):
        state.record_interaction("awesome great love amazing perfect")
    assert -1.0 <= state.mood <= 1.0


def test_crossing_tier_records_milestone(tmp_path):
    state = EvolutionState.load(tmp_path)
    # 10 points crosses stranger -> acquaintance.
    for _ in range(10):
        state.record_interaction("hi")
    assert state.familiarity_label == "acquaintance"
    assert any("acquaintance" in m for m in state.milestones)


def test_idle_days_decay_mood_toward_neutral(tmp_path):
    state = EvolutionState.load(tmp_path)
    for _ in range(10):
        state.record_interaction("love this, awesome")
    high = state.mood
    assert high > 0

    # Pretend the last interaction was 10 days ago, then start a session.
    ten_days_ago = (date.today() - timedelta(days=10)).isoformat()
    state.last_interaction = ten_days_ago
    state.note_session_start()
    assert 0 <= state.mood < high


def test_render_hides_raw_values_instruction(tmp_path):
    state = EvolutionState.load(tmp_path)
    state.record_interaction("hi")
    block = state.render()
    assert "Companion state" in block
    assert "never mention" in block.lower()
    # The mood word and relationship label should be present as tone guidance.
    assert "Relationship with the user" in block


def test_corrupt_state_file_starts_fresh(tmp_path):
    (tmp_path / "evolution.json").write_text("{not valid json", encoding="utf-8")
    state = EvolutionState.load(tmp_path)
    assert state.total_interactions == 0
    assert state.familiarity_label == "stranger"
