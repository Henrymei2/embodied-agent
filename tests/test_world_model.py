"""The room as the agent reasons about it, separate from how it is drawn.

These tests pin the seam a 3D renderer would be swapped in behind: reach,
distance, and arrival are answered from the world model, so they must hold
without reference to textures, sprite rows, or pixels.
"""

from __future__ import annotations

import math

import pytest

from src.open_llm_vtuber.mcpp.tool_manager import (
    PROXIMITY_REQUIREMENTS,
    PROXIMITY_THRESHOLD,
    SERVER_PROXIMITY_REQUIREMENTS,
)
from src.open_llm_vtuber.stage_state_manager import (
    STAGE_VERSION,
    get_default_stage,
    get_reachable_objects,
    migrate_stage,
    save_stage_state,
    validate_stage,
)
from src.open_llm_vtuber.world_model import (
    REACH_CEILING,
    WorldPoint,
    reach_for,
    world_from_stage,
)


GATED = set(PROXIMITY_REQUIREMENTS.values()) | set(
    SERVER_PROXIMITY_REQUIREMENTS.values()
)


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return get_default_stage("character")


def _node(stage: dict, node_id: str) -> dict:
    return next(node for node in stage["nodes"] if node["id"] == node_id)


def _place_avatar(stage: dict, x: float, y: float) -> None:
    avatar = _node(stage, "avatar")
    avatar["x"], avatar["y"] = x, y
    assert save_stage_state("character", stage)[0]


def test_a_bed_is_not_an_hourglass(room):
    """Reach comes from the object's own size, not one number for the room."""
    world = world_from_stage(room)

    hourglass = world.find("hourglass")
    bed = world.find("bed")

    assert hourglass.reach < bed.reach
    assert hourglass.reach == reach_for(*hourglass.footprint)
    assert bed.reach == REACH_CEILING


def test_reach_is_an_arms_length_from_the_edge():
    # 60 units past the half-span, which at 100 units per metre is a real arm.
    assert reach_for(80, 40) == 100.0
    assert reach_for(40, 80) == 100.0
    assert reach_for(1000, 20) == REACH_CEILING


def test_an_object_may_ask_for_less_but_never_for_more(room):
    """The ceiling is what stops a placed object reaching across the room."""
    _node(room, "hourglass")["reach"] = 40
    _node(room, "bookshelf")["reach"] = 9000

    world = world_from_stage(room)

    assert world.find("hourglass").reach == 40
    assert world.find("bookshelf").reach == REACH_CEILING


def test_a_stage_claiming_an_oversized_reach_is_rejected(room):
    _node(room, "bookshelf")["reach"] = 400

    ok, errors = validate_stage(room)

    assert ok is False
    assert "Node bookshelf reach cannot exceed 150." in errors


def test_reach_must_be_a_positive_number(room):
    _node(room, "bookshelf")["reach"] = -5
    assert "Node bookshelf reach must be a positive number." in validate_stage(room)[1]

    _node(room, "bookshelf")["reach"] = "far"
    assert "Node bookshelf reach must be a positive number." in validate_stage(room)[1]


def test_the_world_block_is_validated(room):
    room["world"]["move_speed"] = 0
    assert "Stage world.move_speed must be 1-10000." in validate_stage(room)[1]

    room["world"] = {"units_per_meter": 100, "gravity": 9.8}
    assert "Stage world.gravity is not allowed." in validate_stage(room)[1]


def test_an_object_whose_position_cannot_be_read_is_not_in_the_world():
    """Unreadable geometry is treated as unreachable, never as reachable."""
    world = world_from_stage(
        {
            "nodes": [
                {"id": "avatar", "x": 0, "y": 0, "width": 70, "height": 92},
                {"id": "ghost", "x": None, "y": 10, "width": 50, "height": 50},
                {"id": "real", "x": 10, "y": 10, "width": 50, "height": 50},
            ]
        }
    )

    assert world.find("ghost") is None
    assert world.find("real") is not None
    assert world.reachable_labels() == ["real"]


def test_a_document_without_a_world_block_keeps_the_shipped_rules():
    """A room saved before the block existed is gated exactly as it always was."""
    world = world_from_stage({"canvas": {"width": 960, "height": 540}, "nodes": []})

    assert world.reach_ceiling == REACH_CEILING
    assert world.units_per_meter == 100.0
    assert world.move_speed > 0


def test_standing_at_one_object_never_unlocks_another(room):
    """The invariant the room layout exists to satisfy, now per object."""
    world = world_from_stage(room)

    for obj in world.objects:
        if obj.label not in GATED:
            continue
        reachable = {
            other.label
            for other in world.reachable_from(obj.position)
            if other.label in GATED
        }
        assert reachable == {obj.label}, f"{obj.label} unlocks {reachable}"


def test_no_object_can_be_reached_from_where_the_agent_starts(room):
    assert world_from_stage(room).reachable_labels() == []


def test_the_gate_honors_each_objects_own_reach(room):
    """The behavior change: the same distance is close enough for one and not the other."""
    hourglass = _node(room, "hourglass")
    computer = _node(room, "computer")
    gap = 130.0
    assert world_from_stage(room).find("hourglass").reach < gap
    assert world_from_stage(room).find("computer").reach > gap

    _place_avatar(room, hourglass["x"], hourglass["y"] + gap)
    assert "hourglass" not in get_reachable_objects("character")

    _place_avatar(room, computer["x"], computer["y"] - gap)
    assert "computer" in get_reachable_objects("character")


def test_a_caller_can_still_ask_about_a_flat_distance(room):
    """`list_objects(distance=...)` asks a different question than the gate does."""
    _place_avatar(
        room, _node(room, "hourglass")["x"], _node(room, "hourglass")["y"] + 130
    )

    assert "hourglass" in get_reachable_objects("character", distance=200)
    assert get_reachable_objects("character", distance=0) == []

    with pytest.raises(ValueError):
        get_reachable_objects("character", distance=-1)


def test_the_reach_ceiling_is_what_the_room_layout_is_measured_against():
    """Objects further apart than any reach can ever be cannot share a radius."""
    assert PROXIMITY_THRESHOLD == REACH_CEILING

    nodes = {
        node.get("label") or node["id"]: node
        for node in get_default_stage("conf-a")["nodes"]
    }
    for first in GATED:
        for second in GATED:
            if first >= second:
                continue
            distance = math.dist(
                (nodes[first]["x"], nodes[first]["y"]),
                (nodes[second]["x"], nodes[second]["y"]),
            )
            assert distance > REACH_CEILING, f"{first} overlaps {second}"


def test_walking_is_timed_from_distance_not_a_fixed_animation(room):
    world = world_from_stage(room)
    near = WorldPoint(world.agent_position.x, world.agent_position.y + 100)
    far = WorldPoint(world.agent_position.x + 800, world.agent_position.y)

    assert world.travel_seconds([near]) < world.travel_seconds([far])
    assert world.travel_seconds([far]) == pytest.approx(800 / world.move_speed)
    # A path costs the sum of its legs, not the straight line to the end.
    assert world.travel_seconds([near, far]) > world.travel_seconds([far])


def test_a_path_is_a_list_of_waypoints_ending_at_the_destination(room):
    world = world_from_stage(room)
    destination = world.find("bookshelf").position

    path = world.path_to(destination)

    assert path[-1] == destination


def test_arrival_is_a_question_about_the_room_not_about_the_request(room):
    bookshelf = world_from_stage(room).find("bookshelf").position

    assert world_from_stage(room).has_arrived_at(bookshelf) is False

    _place_avatar(room, bookshelf.x, bookshelf.y)
    assert world_from_stage(room).has_arrived_at(bookshelf) is True

    # Landing a pixel off still counts; landing across the room does not.
    _place_avatar(room, bookshelf.x + 1, bookshelf.y)
    assert world_from_stage(room).has_arrived_at(bookshelf) is True
    _place_avatar(room, bookshelf.x + 40, bookshelf.y)
    assert world_from_stage(room).has_arrived_at(bookshelf) is False


def test_a_v7_room_gains_the_world_block_without_being_rearranged():
    stage = get_default_stage("conf-a")
    stage["version"] = 7
    stage.pop("world")
    _node(stage, "sofa")["x"] = 705

    migrated = migrate_stage(stage)

    assert migrated["version"] == STAGE_VERSION
    assert migrated["world"]["reach_ceiling"] == REACH_CEILING
    assert _node(migrated, "sofa")["x"] == 705
    assert validate_stage(migrated) == (True, [])


def test_a_partial_world_block_is_completed_rather_than_replaced():
    stage = get_default_stage("conf-a")
    stage["version"] = 7
    stage["world"] = {"move_speed": 200}

    migrated = migrate_stage(stage)

    assert migrated["world"]["move_speed"] == 200
    assert migrated["world"]["reach_ceiling"] == REACH_CEILING


def test_a_decoy_object_cannot_stand_in_for_a_gated_one():
    """The spatial gate matches on label, so two objects may not share one.

    Without this, an agent at the design board could place a panel labelled
    "computer" beside itself and hold the real computer's shell tools without
    ever walking there - which is the whole of the spatial-safety claim.
    """
    from src.open_llm_vtuber.world_model import world_from_stage

    stage = {
        "version": 8,
        "canvas": {"width": 960, "height": 540},
        "nodes": [
            {
                "id": "avatar",
                "label": "avatar",
                "x": 100,
                "y": 100,
                "width": 40,
                "height": 60,
            },
            {
                "id": "computer",
                "label": "computer",
                "x": 105,
                "y": 105,
                "width": 100,
                "height": 100,
            },
        ],
    }
    assert world_from_stage(stage).reachable_labels() == ["computer"]

    # A second object wearing the same name makes the name meaningless, so
    # the gate refuses it rather than trusting whichever is nearer.
    stage["nodes"].append(
        {
            "id": "decoy",
            "label": "computer",
            "x": 900,
            "y": 500,
            "width": 40,
            "height": 40,
        }
    )
    world = world_from_stage(stage)

    assert world.reachable_labels() == []
    assert world.ambiguous_labels() == frozenset({"computer"})


def test_a_blank_or_padded_label_resolves_to_the_name_the_gate_matches():
    """Every layer has to agree on what an object is called.

    The world model used to take the label verbatim while validation compared
    a normalized one, so clearing a label handed the id back as a name that
    nothing had checked for collisions, and a padded label matched no gate at
    all.
    """
    from src.open_llm_vtuber.world_model import effective_label

    assert effective_label({"id": "computer", "label": ""}) == "computer"
    assert effective_label({"id": "computer", "label": "   "}) == "computer"
    assert effective_label({"id": "computer"}) == "computer"
    assert effective_label({"id": "chair", "label": " reading  chair "}) == (
        "reading chair"
    )
    assert effective_label({"id": "x", "label": 12345}) == "x"
    assert effective_label({"label": "orphan"}) == "orphan"
    assert effective_label("not a node") is None


def test_two_names_differing_only_in_case_are_still_ambiguous():
    """The world model is the backstop for the rule the validators enforce.

    Those fold case, so comparing exactly here left the last line of defence
    weaker than the rule it backs up.
    """
    from src.open_llm_vtuber.world_model import world_from_stage

    stage = {
        "version": 8,
        "canvas": {"width": 960, "height": 540},
        "nodes": [
            {
                "id": "avatar",
                "label": "avatar",
                "x": 100,
                "y": 100,
                "width": 40,
                "height": 60,
            },
            {
                "id": "computer",
                "label": "computer",
                "x": 105,
                "y": 105,
                "width": 100,
                "height": 100,
            },
            {
                "id": "decoy",
                "label": "Computer",
                "x": 108,
                "y": 108,
                "width": 40,
                "height": 40,
            },
        ],
    }
    world = world_from_stage(stage)

    assert world.ambiguous_labels() == frozenset({"computer"})
    assert world.reachable_labels() == []


def test_an_unrepairable_room_is_complained_about_once(caplog):
    """The gate resolves reachability several times a turn."""
    from src.open_llm_vtuber import world_model

    world_model._warned.clear()
    stage = {
        "version": 8,
        "canvas": {"width": 960, "height": 540},
        "nodes": [
            {
                "id": "avatar",
                "label": "avatar",
                "x": 100,
                "y": 100,
                "width": 40,
                "height": 60,
            },
            {"id": "a", "label": "twin", "x": 105, "y": 105, "width": 40, "height": 40},
            {"id": "b", "label": "twin", "x": 108, "y": 108, "width": 40, "height": 40},
        ],
    }
    messages = []
    handle = world_model.logger.add(
        lambda record: messages.append(record), level="WARNING"
    )
    try:
        for _ in range(5):
            world_model.world_from_stage(stage).reachable_labels()
    finally:
        world_model.logger.remove(handle)

    assert len(messages) == 1, messages
