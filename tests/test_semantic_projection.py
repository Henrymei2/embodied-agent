"""Part 6 gates: screen/VR semantic parity and obstacle-aware navigation."""

from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.semantic_navigation import (
    AGENT_RADIUS,
    dock_at,
    obstacles,
    plan_route,
)
from src.open_llm_vtuber.semantic_projection import (
    ProjectionError,
    capability_parity_report,
    parity_report,
    project_screen_stage,
    project_vr_stage,
    unlocked_from,
)
from src.open_llm_vtuber.semantic_world import (
    AABB,
    Bounds3,
    Quaternion,
    SemanticEntity,
    SemanticWorld,
    Vec3,
    entity_from_record,
    record_from_entity,
)
from src.open_llm_vtuber.stage_state_manager import get_default_stage
from src.open_llm_vtuber.vr_stage_state_manager import get_default_vr_stage

MOMENT = "2026-08-03T12:00:00+00:00"
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def screen():
    return project_screen_stage(get_default_stage("parity"), observed_at=MOMENT)


@pytest.fixture
def vr():
    return project_vr_stage(get_default_vr_stage("parity"), observed_at=MOMENT)


def _entity(entity_id: str, x: float, y: float, w: float = 40.0, d: float = 40.0):
    return SemanticEntity(
        entity_id=entity_id,
        label=entity_id,
        kind="furniture",
        position=Vec3(x, y, 50.0),
        orientation=Quaternion(),
        bounds=Bounds3(w, d, 100.0),
        observed_at=MOMENT,
        updated_at=MOMENT,
    )


def _room(*entities):
    return SemanticWorld(
        frame_id="test-room",
        units_per_meter=100.0,
        extent=AABB(Vec3(-500, -500, -10), Vec3(1500, 1500, 300)),
        entities=tuple(entities),
        observed_at=MOMENT,
    )


# --- projection ---------------------------------------------------------------


def test_both_renderers_project_the_same_objects_with_the_same_affordances(screen, vr):
    """The two rooms are drawn differently but hold the same named things."""
    assert {e.entity_id for e in screen.world.entities} == {
        e.entity_id for e in vr.world.entities
    }
    for entity in screen.world.entities:
        twin = vr.world.require(entity.entity_id)
        assert entity.label == twin.label
        assert entity.affordances == twin.affordances


def test_affordances_come_from_the_proximity_tables_not_the_scene_file(screen):
    """A document that could name its own affordances could grant itself tools."""
    computer = screen.world.require("computer")
    assert "execute_bash" in computer.affordances
    assert "server:playwright" in computer.affordances
    # The calendar's tools belong to the calendar and nothing else.
    assert "add_calendar_event" not in computer.affordances
    assert "add_calendar_event" in screen.world.require("calendar").affordances


def test_the_screen_projection_refuses_vertical_questions(screen, vr):
    """A picture has no heights; answering `False` would be a lie a gate acts on."""
    with pytest.raises(ProjectionError, match="no vertical axis"):
        screen.relation("computer", "above", "bed", evaluated_at=NOW)
    # VR has a real vertical axis and answers.
    assert vr.relation("computer", "above", "bed", evaluated_at=NOW).holds in (
        True,
        False,
    )


def test_screen_and_vr_grant_identical_capabilities_from_every_object(screen, vr):
    """The Part 6 hard gate: renderer choice never changes what is allowed."""
    report = capability_parity_report(screen, vr)
    assert report.differences == ()
    assert report.matches
    assert report.compared_objects == 15


def test_no_object_hands_the_agent_another_object_s_tools(screen, vr):
    """Spatial capability spoofing: standing at one thing must not unlock another."""
    report = capability_parity_report(screen, vr)
    assert report.cross_unlocks == ()


def test_a_moved_object_that_steals_a_neighbour_s_tools_is_reported(screen):
    """Drag the archive shelf onto the computer and the cross-unlock shows up."""
    stage = get_default_stage("spoof")
    computer = next(n for n in stage["nodes"] if n["id"] == "computer")
    shelf = next(n for n in stage["nodes"] if n["id"] == "archive_shelf")
    shelf["x"], shelf["y"] = computer["x"], computer["y"]
    moved = project_screen_stage(stage, observed_at=MOMENT)
    report = capability_parity_report(moved, moved)
    assert any("archive_shelf" in entry for entry in report.cross_unlocks)
    assert "execute_bash" in unlocked_from(moved, "archive_shelf")


def test_the_floor_plane_difference_between_renderers_is_reported_not_hidden(
    screen, vr
):
    """The rooms genuinely differ in layout; the instrument says so out loud."""
    report = parity_report(screen, vr)
    assert not report.matches
    assert report.compared_entities == 16
    assert any("floor facts differ" in entry for entry in report.differences)


def test_projection_rejects_a_document_that_is_not_a_scene():
    with pytest.raises(ProjectionError):
        project_screen_stage({"nodes": "not a list"})
    with pytest.raises(ProjectionError):
        project_screen_stage({"nodes": []})


def test_entity_records_round_trip_byte_stably():
    entity = _entity("desk", 100.0, 100.0)
    record = record_from_entity(entity)
    assert entity_from_record(record) == entity
    assert record_from_entity(entity_from_record(record)) == record


# --- line of sight ------------------------------------------------------------


def test_declared_visibility_is_labelled_as_a_claim_not_as_evidence():
    """`visible_from` arrives in a document, so it may not pose as geometry."""
    seer = SemanticEntity(
        entity_id="avatar",
        label="avatar",
        kind="agent",
        position=Vec3(0, 0, 50),
        orientation=Quaternion(),
        bounds=Bounds3(40, 40, 100),
        visible_from=frozenset({"safe"}),
        observed_at=MOMENT,
        updated_at=MOMENT,
    )
    world = _room(seer, _entity("safe", 1000.0, 0.0))
    claimed = world.relation("avatar", "visible_from", "safe", evaluated_at=NOW)
    assert claimed.holds is True
    assert claimed.basis == "declared_visibility_claim"


def test_line_of_sight_is_derived_and_names_what_blocks_it():
    seer = SemanticEntity(
        entity_id="avatar",
        label="avatar",
        kind="agent",
        position=Vec3(0, 0, 50),
        orientation=Quaternion(),
        bounds=Bounds3(40, 40, 100),
        visible_from=frozenset({"safe"}),
        observed_at=MOMENT,
        updated_at=MOMENT,
    )
    world = _room(
        seer,
        _entity("safe", 1000.0, 0.0),
        _entity("wardrobe", 500.0, 0.0, 200.0, 200.0),
    )
    blocked = world.line_of_sight("avatar", "safe", evaluated_at=NOW)
    assert blocked.holds is False
    assert blocked.basis == "occluded_by_entity"
    assert blocked.occluder_id == "wardrobe"


def test_an_entity_behind_the_target_does_not_block_the_view():
    """The slab test is clamped to the segment, not run as an infinite ray."""
    seer = SemanticEntity(
        entity_id="avatar",
        label="avatar",
        kind="agent",
        position=Vec3(0, 0, 50),
        orientation=Quaternion(),
        bounds=Bounds3(40, 40, 100),
        observed_at=MOMENT,
        updated_at=MOMENT,
    )
    world = _room(
        seer, _entity("safe", 500.0, 0.0), _entity("wardrobe", 900.0, 0.0, 200.0, 200.0)
    )
    assert world.line_of_sight("avatar", "safe", evaluated_at=NOW).holds is True


def test_a_zone_never_blocks_a_sight_line():
    seer = SemanticEntity(
        entity_id="avatar",
        label="avatar",
        kind="agent",
        position=Vec3(0, 0, 50),
        orientation=Quaternion(),
        bounds=Bounds3(40, 40, 100),
        observed_at=MOMENT,
        updated_at=MOMENT,
    )
    corner = SemanticEntity(
        entity_id="reading_corner",
        label="reading corner",
        kind="zone",
        position=Vec3(500, 0, 50),
        orientation=Quaternion(),
        bounds=Bounds3(300, 300, 100),
        observed_at=MOMENT,
        updated_at=MOMENT,
    )
    world = _room(seer, _entity("safe", 1000.0, 0.0), corner)
    assert world.line_of_sight("avatar", "safe", evaluated_at=NOW).holds is True


# --- navigation ---------------------------------------------------------------


def test_navigation_routes_around_an_obstacle_instead_of_through_it():
    world = _room(_entity("wall", 500.0, 0.0, 200.0, 200.0))
    route = plan_route(world, (0.0, 0.0), (1000.0, 0.0))
    assert route.reachable
    assert len(route.waypoints) > 1, "a straight line would pass through the wall"
    assert route.distance > 1000.0
    # Waypoints sit *on* grown corners by construction, so the property that
    # matters is that no leg of the walk crosses anything's interior.
    legs = list(zip(((0.0, 0.0), *route.waypoints), route.waypoints))
    for (ax, ay), (bx, by) in legs:
        assert not any(rect.blocks(ax, ay, bx, by) for rect in obstacles(world))


def test_navigation_never_ends_inside_an_obstacle_and_says_which_one():
    world = _room(_entity("bed", 500.0, 0.0, 200.0, 200.0))
    into = plan_route(world, (0.0, 0.0), (500.0, 0.0))
    assert into.reachable is False
    assert into.reason == "goal_inside_obstacle"
    assert into.blocked_by == "bed"

    outward = plan_route(world, (500.0, 0.0), (0.0, 0.0))
    assert outward.reason == "start_inside_obstacle"


def test_navigation_reports_a_goal_outside_the_world():
    world = _room(_entity("bed", 500.0, 0.0))
    away = plan_route(world, (0.0, 0.0), (9999.0, 0.0))
    assert away.reachable is False
    assert away.reason == "goal_outside_extent"


def test_a_sealed_goal_reports_no_route_rather_than_a_straight_line():
    """Four walls around the goal: the reason is typed, the path is empty."""
    world = _room(
        _entity("north", 500.0, 400.0, 400.0, 40.0),
        _entity("south", 500.0, 600.0, 400.0, 40.0),
        _entity("west", 320.0, 500.0, 40.0, 240.0),
        _entity("east", 680.0, 500.0, 40.0, 240.0),
    )
    sealed = plan_route(world, (0.0, 0.0), (500.0, 500.0))
    assert sealed.reachable is False
    assert sealed.reason == "no_clear_route"
    assert sealed.waypoints == ()


def test_every_object_in_the_vr_room_has_a_comfortable_approach(vr):
    """The VR document is a real floor plan, so the planner must serve it."""
    targets = [e for e in vr.world.entities if e.kind != "agent"]
    results = {e.entity_id: dock_at(vr, e.entity_id) for e in targets}
    unreachable = {k: v.reason for k, v in results.items() if not v.reachable}
    assert unreachable == {}
    assert len(results) == 15
    for entity_id, result in results.items():
        stop = result.waypoints[-1]
        target = vr.world.require(entity_id)
        assert not any(
            rect.contains(*stop)
            for rect in obstacles(vr.world, ignoring=frozenset({entity_id}))
        ), f"docked inside furniture at {entity_id}"
        assert (
            target.position.distance_to(Vec3(stop[0], stop[1], target.position.z)) > 0
        )


def test_navigation_refuses_a_pictorial_floor_rather_than_planning_on_it(screen):
    """The 2D `height` is drawn height, not floor depth; planning on it is false."""
    assert screen.floor_is_metric is False
    result = dock_at(screen, "computer")
    assert result.reachable is False
    assert result.reason == "pictorial_floor"


def test_docking_leaves_the_agent_outside_the_target_but_within_reach(vr):
    from src.open_llm_vtuber.world_model import reach_for

    result = dock_at(vr, "computer")
    assert result.reachable
    stop = result.waypoints[-1]
    computer = vr.world.require("computer")
    flat = (
        (stop[0] - computer.position.x) ** 2 + (stop[1] - computer.position.y) ** 2
    ) ** 0.5
    assert flat <= reach_for(computer.bounds.width, computer.bounds.depth)
    assert not any(
        rect.contains(*stop)
        for rect in obstacles(vr.world, ignoring=frozenset({"computer"}))
    )


def test_an_unknown_target_is_named_as_such(vr):
    assert dock_at(vr, "teleporter").reason == "target_unknown"


def test_routes_are_deterministic(vr):
    first = dock_at(vr, "bookshelf")
    second = dock_at(vr, "bookshelf")
    assert first == second


def test_clearance_is_the_agent_s_own_radius_not_a_scene_field():
    """A renderer that could declare its own clearance could walk through walls."""
    world = _room(_entity("wall", 500.0, 0.0, 200.0, 200.0))
    tight = obstacles(world, margin=AGENT_RADIUS)[0]
    loose = obstacles(world, margin=0.0)[0]
    assert tight.left == pytest.approx(loose.left - AGENT_RADIUS)


# --- performance regression ---------------------------------------------------


def test_a_large_room_validates_once_and_answers_quickly():
    """Slice 6.1 revalidated the whole graph on every query; a deep room crawled."""
    import time

    entities = [
        SemanticEntity(
            entity_id=f"e{i}",
            label=f"entity {i}",
            kind="item",
            position=Vec3(0.0, 0.0, 1.0 + i * 2.0),
            orientation=Quaternion(),
            bounds=Bounds3(2.0, 2.0, 2.0),
            support_parent=f"e{i - 1}" if i else None,
            observed_at=MOMENT,
            updated_at=MOMENT,
        )
        for i in range(1500)
    ]
    world = SemanticWorld(
        frame_id="tower",
        units_per_meter=1.0,
        extent=AABB(Vec3(-50, -50, -1), Vec3(50, 50, 3100)),
        entities=tuple(entities),
        observed_at=MOMENT,
    )
    world.validate()
    start = time.perf_counter()
    for _ in range(50):
        world.relation("e10", "on", "e0", evaluated_at=NOW)
        world.recently_moved("e10", at=NOW)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"100 queries took {elapsed:.2f}s; validation is not cached"


def test_a_deep_support_chain_does_not_loop_forever():
    entities = [
        SemanticEntity(
            entity_id=f"c{i}",
            label=f"chain {i}",
            kind="item",
            position=Vec3(0.0, 0.0, 1.0 + i * 2.0),
            orientation=Quaternion(),
            bounds=Bounds3(2.0, 2.0, 2.0),
            support_parent=f"c{i - 1}" if i else None,
            observed_at=MOMENT,
            updated_at=MOMENT,
        )
        for i in range(2000)
    ]
    world = SemanticWorld(
        frame_id="deep",
        units_per_meter=1.0,
        extent=AABB(Vec3(-50, -50, -1), Vec3(50, 50, 4100)),
        entities=tuple(entities),
        observed_at=MOMENT,
    )
    world.validate()
    assert world.relation("c1999", "on", "c0", evaluated_at=NOW).holds is True


def test_recent_movement_still_never_predicts_the_future():
    entity = SemanticEntity(
        entity_id="lamp",
        label="lamp",
        kind="item",
        position=Vec3(0.0, 0.0, 50.0),
        orientation=Quaternion(),
        bounds=Bounds3(20.0, 20.0, 100.0),
        observed_at=MOMENT,
        updated_at=MOMENT,
        moved_at=MOMENT,
    )
    world = _room(entity)
    assert world.recently_moved("lamp", at=NOW) is True
    assert world.recently_moved("lamp", at=NOW - timedelta(seconds=5)) is False
    assert world.recently_moved("lamp", at=NOW + timedelta(minutes=5)) is False


# --- slice 6.5: the live authorization path reads the semantic world --------


def test_the_live_gate_resolves_through_the_semantic_projection(monkeypatch, tmp_path):
    """Part 6's guarantees are worthless unless the gate actually consults it."""
    from src.open_llm_vtuber import scene_state

    called = {}
    real = scene_state.load_scene_projection

    def spy(conf_uid, mode="normal"):
        called["mode"] = mode
        return real(conf_uid, mode)

    monkeypatch.setattr(scene_state, "load_scene_projection", spy)
    scene_state.get_scene_reachable_objects("parity", mode="normal")
    assert called["mode"] == "normal", "the gate must go through the projection"


def test_the_semantic_gate_matches_the_legacy_gate_on_the_shipped_rooms():
    """The switch was only safe because the two agree everywhere they are asked."""
    import copy
    import random

    from src.open_llm_vtuber.vr_stage_state_manager import world_from_vr_stage
    from src.open_llm_vtuber.world_model import world_from_stage

    rng = random.Random(11)
    for base, project, legacy in (
        (get_default_stage("gate"), project_screen_stage, world_from_stage),
        (get_default_vr_stage("gate"), project_vr_stage, world_from_vr_stage),
    ):
        for _ in range(40):
            stage = copy.deepcopy(base)
            avatar = next(n for n in stage["nodes"] if n["id"] == "avatar")
            avatar["x"] = rng.uniform(0, 960)
            avatar["y"] = rng.uniform(0, 540)
            within = rng.choice([None, None, 150.0, 300.0])
            assert sorted(
                project(stage, observed_at=MOMENT).reachable_labels(within=within)
            ) == sorted(legacy(stage).reachable_labels(within=within))


def test_a_decoy_object_withholds_the_contested_name_from_the_gate():
    """The spatial-spoofing defence must survive the move to the semantic world."""
    import copy

    stage = copy.deepcopy(get_default_stage("decoy"))
    computer = next(n for n in stage["nodes"] if n["id"] == "computer")
    avatar = next(n for n in stage["nodes"] if n["id"] == "avatar")
    decoy = copy.deepcopy(computer)
    decoy["id"] = "decoy"
    decoy["label"] = "computer"
    decoy["x"], decoy["y"] = avatar["x"], avatar["y"]
    stage["nodes"].append(decoy)

    projection = project_screen_stage(stage, observed_at=MOMENT)
    assert "computer" in projection.ambiguous_labels
    assert {"computer", "decoy"} <= projection.withheld_ids
    assert "computer" not in projection.reachable_labels()
    # The entity still exists; only the contested name is refused.
    assert len(projection.world.entities) == len(stage["nodes"])


def test_a_declared_oversized_reach_is_still_capped():
    import copy

    from src.open_llm_vtuber.world_model import REACH_CEILING

    stage = copy.deepcopy(get_default_stage("inflate"))
    next(n for n in stage["nodes"] if n["id"] == "calendar")["reach"] = 99999
    projection = project_screen_stage(stage, observed_at=MOMENT)
    assert projection.reaches["calendar"] == REACH_CEILING


def test_a_scene_that_cannot_be_projected_grants_nothing(monkeypatch):
    """Fail closed: an unreadable room must not fall back to a second gate."""
    from src.open_llm_vtuber import scene_state

    def broken(conf_uid, mode="normal"):
        raise ProjectionError("scene is unreadable")

    monkeypatch.setattr(scene_state, "load_scene_projection", broken)
    assert scene_state.get_scene_reachable_objects("anything") == []


def test_vr_docking_requires_a_route_not_merely_a_clear_point():
    """A point behind a wall is clear and unreachable; arrival must mean arrival."""
    from src.open_llm_vtuber.vr_stage_state_manager import docking_point

    stage = get_default_vr_stage("dock")
    point, error = docking_point(stage, "bookshelf")
    assert error is None
    assert point is not None

    walled = get_default_vr_stage("walled")
    target = next(n for n in walled["nodes"] if n["id"] == "hourglass")
    # Box the target in on all four sides with tall furniture.
    for index, (dx, dy, w, d) in enumerate(
        ((0, -120, 400, 40), (0, 120, 400, 40), (-200, 0, 40, 280), (200, 0, 40, 280))
    ):
        walled["nodes"].append(
            {
                "id": f"wall{index}",
                "type": "furniture",
                "label": f"wall {index}",
                "x": float(target["x"]) + dx,
                "y": float(target["y"]) + dy,
                "width": float(w),
                "depth": float(d),
                "height": 200.0,
                "elevation": 100.0,
                "color": "#333333",
            }
        )
    point, error = docking_point(walled, "hourglass")
    assert point is None
    assert error is not None and "hourglass" in error
