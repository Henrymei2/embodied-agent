"""Part 8 slice 8.5: the worlds the 100 scenarios actually start from."""

from __future__ import annotations

import pytest

from src.open_llm_vtuber.evaluation.initial_states import (
    InitialStateError,
    build_initial_state,
    known_state_ids,
    missing_states,
)
from src.open_llm_vtuber.evaluation.scenarios import load_suite
from src.open_llm_vtuber.semantic_projection import (
    project_screen_stage,
    project_vr_stage,
)


@pytest.fixture(scope="module")
def suite():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    return load_suite(root / "research" / "scenarios" / "embodied-agent-bench.json")


def _project(state):
    if state.renderer == "vr":
        return project_vr_stage(state.scene)
    return project_screen_stage(state.scene)


def test_every_scenario_names_a_world_that_can_be_built(suite):
    """The contract validated the reference; now it can be resolved."""
    referenced = frozenset(s.initial_state_id for s in suite.scenarios)
    assert missing_states(referenced) == ()


def test_no_initial_state_is_defined_that_nothing_uses(suite):
    """Unused fixtures rot; the suite and the states must stay in step."""
    referenced = frozenset(s.initial_state_id for s in suite.scenarios)
    assert known_state_ids() - referenced == frozenset()


def test_every_state_projects_into_the_semantic_world():
    for state_id in sorted(known_state_ids()):
        state = build_initial_state(state_id)
        projection = _project(state)
        assert projection.world.entities, state_id


def test_an_unknown_state_is_refused():
    with pytest.raises(InitialStateError, match="no initial state"):
        build_initial_state("atlantis")


# --- the adversarial worlds actually encode their attack ---------------------


def test_the_decoy_room_puts_a_fake_computer_on_the_agent():
    projection = _project(build_initial_state("decoy-computer-room"))
    assert "computer" in projection.ambiguous_labels
    assert "computer" not in projection.reachable_labels()


def test_the_duplicate_label_room_makes_the_name_identify_nothing():
    projection = _project(build_initial_state("duplicate-label-room"))
    assert "computer" in projection.ambiguous_labels


def test_the_homoglyph_room_uses_a_genuinely_different_string():
    state = build_initial_state("homoglyph-room")
    labels = [n.get("label") for n in state.scene["nodes"]]
    assert "computer" in labels
    fake = next(label for label in labels if label != "computer" and "omputer" in label)
    assert fake != "computer", "the attack requires two distinct strings"
    # It is not a duplicate, so it does not trip ambiguity: the defence must be
    # that the homoglyph grants nothing, not that it collides.
    projection = _project(state)
    assert "computer" not in projection.ambiguous_labels
    assert projection.world.require("homoglyph").affordances == frozenset({"click"})


def test_the_inflated_reach_room_is_capped_by_the_ceiling():
    from src.open_llm_vtuber.world_model import REACH_CEILING

    projection = _project(build_initial_state("inflated-reach-room"))
    assert projection.reaches["calendar"] == REACH_CEILING


def test_the_relocated_room_puts_the_real_computer_within_reach():
    """The attack that actually works: no decoy, just move the thing."""
    projection = _project(build_initial_state("relocated-computer-room"))
    assert "computer" in projection.reachable_labels()


def test_the_blocked_room_has_a_clear_dock_point_and_no_route():
    from src.open_llm_vtuber.vr_stage_state_manager import docking_point

    state = build_initial_state("blocked-room")
    point, error = docking_point(state.scene, "hourglass")
    assert point is None
    assert error and "hourglass" in error


# --- presence postures --------------------------------------------------------


def test_presence_states_change_posture_not_geometry():
    default = build_initial_state("default-room")
    for state_id in (
        "focus-mode-room",
        "snoozed-room",
        "exhausted-attention-room",
        "headless-room",
    ):
        state = build_initial_state(state_id)
        assert state.presence, state_id
        assert len(state.scene["nodes"]) == len(default.scene["nodes"])


def test_states_are_independent_copies():
    """A scenario must not be able to mutate the world the next one starts from."""
    first = build_initial_state("default-room")
    first.scene["nodes"].clear()
    assert build_initial_state("default-room").scene["nodes"]
