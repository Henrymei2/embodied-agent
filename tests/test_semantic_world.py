from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from src.open_llm_vtuber.semantic_world import (
    AABB,
    Bounds3,
    Quaternion,
    SemanticEntity,
    SemanticWorld,
    SemanticWorldValidationError,
    Vec3,
    entity_from_record,
)

NOW = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)


def _entity(entity_id: str, **overrides) -> SemanticEntity:
    values = {
        "entity_id": entity_id,
        "label": entity_id.replace("_", " "),
        "kind": "item",
        "position": Vec3(100, 100, 100),
        "orientation": Quaternion(),
        "bounds": Bounds3(20, 20, 20),
        "observed_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }
    values.update(overrides)
    return SemanticEntity(**values)


def _world(*entities: SemanticEntity) -> SemanticWorld:
    return SemanticWorld(
        frame_id="room:main",
        units_per_meter=100,
        extent=AABB(Vec3(0, 0, 0), Vec3(1000, 1000, 500)),
        entities=entities,
        observed_at=NOW.isoformat(),
    )


def test_world_validates_full_semantic_graph_without_renderer_data():
    table = _entity(
        "table",
        kind="furniture",
        position=Vec3(300, 300, 40),
        bounds=Bounds3(160, 100, 80),
        occupancy="occupied",
        occupants=frozenset({"avatar"}),
        owner_ref="user:primary",
        affordances=frozenset({"support", "approach"}),
    )
    cup = _entity(
        "cup",
        position=Vec3(300, 300, 90),
        support_parent="table",
        visible_from=frozenset({"avatar"}),
        affordances=frozenset({"grasp", "fill"}),
    )
    avatar = _entity(
        "avatar",
        kind="agent",
        position=Vec3(300, 200, 90),
        bounds=Bounds3(40, 40, 180),
    )
    world = _world(table, cup, avatar)

    world.validate()
    assert world.require("cup").owner_ref is None
    assert world.relation("cup", "on", "table", evaluated_at=NOW).holds is True
    assert world.relation("cup", "visible_from", "avatar", evaluated_at=NOW).holds


def test_grounded_axis_and_near_relations_use_bounding_boxes():
    left = _entity("left", position=Vec3(100, 200, 50))
    right = _entity("right", position=Vec3(250, 200, 50))
    front = _entity("front", position=Vec3(250, 400, 50))
    high = _entity("high", position=Vec3(250, 200, 160))
    world = _world(left, right, front, high)

    assert world.relation("left", "left_of", "right", evaluated_at=NOW).holds
    assert world.relation("right", "right_of", "left", evaluated_at=NOW).holds
    assert world.relation("front", "in_front_of", "right", evaluated_at=NOW).holds
    assert world.relation("right", "behind", "front", evaluated_at=NOW).holds
    assert world.relation("high", "above", "right", evaluated_at=NOW).holds
    assert world.relation("right", "below", "high", evaluated_at=NOW).holds
    assert world.relation("left", "near", "right", evaluated_at=NOW).holds
    assert (
        world.relation(
            "left", "near", "right", near_meters=0.5, evaluated_at=NOW
        ).holds
        is False
    )


def test_nested_containment_is_transitive_and_must_match_geometry():
    cabinet = _entity(
        "cabinet",
        kind="container",
        position=Vec3(300, 300, 150),
        bounds=Bounds3(300, 300, 300),
    )
    drawer = _entity(
        "drawer",
        kind="container",
        position=Vec3(300, 300, 100),
        bounds=Bounds3(200, 200, 100),
        containment_parent="cabinet",
    )
    key = _entity(
        "key",
        position=Vec3(300, 300, 100),
        bounds=Bounds3(10, 10, 10),
        containment_parent="drawer",
    )
    world = _world(cabinet, drawer, key)
    world.validate()

    assert world.relation("key", "inside", "drawer", evaluated_at=NOW).holds
    assert world.relation("key", "inside", "cabinet", evaluated_at=NOW).holds

    invalid = _world(cabinet, replace(drawer, position=Vec3(900, 900, 100)), key)
    with pytest.raises(SemanticWorldValidationError, match="not inside"):
        invalid.validate()


def test_graph_cycles_unknown_references_and_duplicate_labels_fail_closed():
    first = _entity("first", containment_parent="second")
    second = _entity("second", containment_parent="first")
    with pytest.raises(SemanticWorldValidationError, match="cycles"):
        _world(first, second).validate()

    with pytest.raises(SemanticWorldValidationError, match="unknown"):
        _world(_entity("orphan", support_parent="missing")).validate()

    duplicate = replace(_entity("second"), label="first")
    with pytest.raises(SemanticWorldValidationError, match="labels"):
        _world(_entity("first"), duplicate).validate()


def test_orientation_geometry_and_occupancy_are_strict():
    with pytest.raises(SemanticWorldValidationError, match="normalized"):
        replace(_entity("tilted"), orientation=Quaternion(0, 0, 0, 2)).validate()
    with pytest.raises(SemanticWorldValidationError, match="positive"):
        replace(_entity("flat"), bounds=Bounds3(1, 1, 0)).validate()
    with pytest.raises(SemanticWorldValidationError, match="must agree"):
        replace(_entity("seat"), occupancy="occupied").validate()


def test_temporal_movement_is_bounded_and_never_predicts_the_future():
    moved = _entity(
        "moved",
        moved_at=(NOW - timedelta(seconds=10)).isoformat(),
    )
    world = _world(moved)

    assert world.recently_moved("moved", at=NOW, within=timedelta(seconds=30))
    assert not world.recently_moved(
        "moved", at=NOW + timedelta(minutes=1), within=timedelta(seconds=30)
    )
    assert not world.recently_moved(
        "moved", at=NOW - timedelta(seconds=20), within=timedelta(seconds=30)
    )


def test_semantic_record_rejects_renderer_assets_styles_and_unknown_fields():
    record = {
        "entity_id": "lamp",
        "label": "lamp",
        "kind": "item",
        "position": {"x": 10, "y": 10, "z": 10},
        "orientation": {"x": 0, "y": 0, "z": 0, "w": 1},
        "bounds": {"width": 10, "depth": 10, "height": 20},
        "containment_parent": None,
        "support_parent": None,
        "visible_from": [],
        "occupancy": "free",
        "occupants": [],
        "owner_ref": None,
        "affordances": ["switch"],
        "observed_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "moved_at": None,
    }
    assert entity_from_record(record).affordances == frozenset({"switch"})

    for renderer_field in ("texture", "mesh", "material", "style", "zIndex"):
        with pytest.raises(SemanticWorldValidationError, match="fields"):
            entity_from_record({**record, renderer_field: {}})


def test_relation_evidence_is_typed_and_timestamped():
    world = _world(
        _entity("one", position=Vec3(100, 100, 100)),
        _entity("two", position=Vec3(300, 100, 100)),
    )
    evidence = world.relation("one", "left_of", "two", evaluated_at=NOW)

    evidence.validate()
    assert evidence.basis == "world_x_axis"
    assert evidence.evaluated_at == NOW.isoformat()


def test_invalid_relation_and_self_relation_are_rejected():
    world = _world(_entity("one"), _entity("two", position=Vec3(300, 300, 100)))
    with pytest.raises(SemanticWorldValidationError, match="relation type"):
        world.relation("one", "overlaps", "two", evaluated_at=NOW)
    with pytest.raises(SemanticWorldValidationError, match="different"):
        world.relation("one", "near", "one", evaluated_at=NOW)
    with pytest.raises(SemanticWorldValidationError, match="explicit offset"):
        world.relation("one", "near", "two", evaluated_at=datetime(2026, 8, 3))
