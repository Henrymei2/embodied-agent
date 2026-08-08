"""Projecting the screen and VR scene documents into one semantic world.

Two renderers draw this room. The 2D stage is a 960x540 picture with `width`
and `height` in screen pixels; the VR stage is a 960x540 floor with `depth`,
`height`, and `elevation` in the same units. They share ids, labels, and a
floor plane, and they disagree about everything above it — the screen document
has no vertical axis at all.

Authorization must not be able to tell which one is connected. That is the
whole point of the seam: if walking to the computer grants the computer's
tools on screen, it must grant exactly the same tools in the headset, and a
question the screen cannot answer must be *refused* there rather than guessed.

So this module projects both documents into `SemanticWorld` and then states
the parity contract precisely:

- the **floor plane** — identity, label, kind, affordances, occupancy, x/y
  position, and width/depth footprint — must be identical, and
- the **vertical axis** is renderer-supplied. VR has one. The screen does not,
  and asking it a vertical question raises instead of inventing a height.

`parity_report` compares two projections and returns every difference it finds,
so a scene edit that silently desynchronizes the renderers fails a test rather
than becoming a divergence nobody notices until the headset authorizes
something the screen would have refused.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from .world_model import REACH_CEILING, reach_for
from .semantic_world import (
    AABB,
    Bounds3,
    Quaternion,
    RelationEvidence,
    SemanticEntity,
    SemanticWorld,
    SemanticWorldValidationError,
    Vec3,
)

# Relations that read the vertical axis. A screen projection refuses these.
VERTICAL_RELATIONS = frozenset({"above", "below"})

# The screen document says nothing about how tall anything is. Rather than
# invent a height per object — which would be a renderer fact dressed up as a
# semantic one — every screen entity gets the same nominal slab on the floor,
# and `vertical_axis` is False so nothing may read it.
SCREEN_NOMINAL_HEIGHT = 1.0

# How far above the floor the sight line runs when the renderer has no
# vertical axis. Everything is one nominal slab tall, so any positive value
# inside that slab gives the same answer; the constant exists so the screen
# and VR line-of-sight calls are visibly making different assumptions.
SCREEN_EYE_HEIGHT = SCREEN_NOMINAL_HEIGHT / 2

_AGENT_ID = "avatar"


class ProjectionError(ValueError):
    """A scene document cannot be projected into the semantic contract."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProjectionError(f"{field_name} must be a number")
    return float(value)


def _affordances(label: str, node: dict[str, Any]) -> frozenset[str]:
    """The tools this object actually unlocks, plus whether it can be clicked.

    Affordances are read from the proximity tables rather than from the scene
    file, because what an object lets the agent *do* is an authorization fact.
    A document that could name its own affordances would be a document that
    could grant itself capabilities.
    """
    from .mcpp.tool_manager import (
        PROXIMITY_REQUIREMENTS,
        SERVER_PROXIMITY_REQUIREMENTS,
    )

    granted = {
        tool for tool, required in PROXIMITY_REQUIREMENTS.items() if required == label
    }
    granted |= {
        f"server:{server}"
        for server, required in SERVER_PROXIMITY_REQUIREMENTS.items()
        if required == label
    }
    interaction = node.get("interaction")
    if isinstance(interaction, dict) and interaction.get("on_click") is True:
        granted.add("click")
    # Tool names are already identifier-like; anything that is not stays out
    # rather than failing the whole room over one odd server name.
    return frozenset(name for name in granted if name and len(name) <= 200)


def _declared_label(node: dict[str, Any]) -> str:
    """The name a scene node asks to be known by, normalized."""
    node_id = node.get("id")
    label = node.get("label")
    if isinstance(label, str) and label.strip():
        return " ".join(label.split())
    return node_id if isinstance(node_id, str) else ""


def _resolve_labels(
    nodes: list[dict[str, Any]],
) -> tuple[dict[str, str], frozenset[str], frozenset[str]]:
    """Give every node a unique semantic name, and report the names that aren't.

    A label worn by two objects names neither of them: the gate asks "is the
    agent near the computer?" and a room with two computers cannot answer.
    The semantic world requires unique labels, so rather than refusing to
    project such a room at all — which would lock every honest object's tools
    over one bad node — the duplicated name is replaced by the node's own id
    and reported as ambiguous. `reachable_labels` then withholds it, which is
    exactly what `world_model` already does and why a decoy object placed
    beside the agent cannot answer for the real one.
    """
    counts: dict[str, int] = {}
    for node in nodes:
        label = _declared_label(node)
        if label:
            counts[label.casefold()] = counts.get(label.casefold(), 0) + 1
    ambiguous = frozenset(name for name, count in counts.items() if count > 1)

    resolved: dict[str, str] = {}
    withheld: set[str] = set()
    taken: set[str] = set()
    for node in nodes:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ProjectionError("scene node has no id")
        label = _declared_label(node)
        if label.casefold() in ambiguous:
            # Renamed to its own id so the world keeps unique labels, and
            # recorded so the gate withholds it: the entity survives, the
            # contested *name* does not.
            withheld.add(node_id)
            label = node_id
        elif not label:
            label = node_id
        # Ids are unique, but an id can still collide with another node's
        # label; the suffix keeps the world's uniqueness invariant intact.
        while label.casefold() in taken:
            label = f"{label}#{node_id}"
        taken.add(label.casefold())
        resolved[node_id] = label
    return resolved, ambiguous, frozenset(withheld)


def _reach_of(node: dict[str, Any], width: float, depth: float) -> float:
    """The same reach the live gate has always used, read the same way."""
    declared = node.get("reach")
    if (
        not isinstance(declared, bool)
        and isinstance(declared, (int, float))
        and declared > 0
    ):
        return min(float(declared), REACH_CEILING)
    return reach_for(width, depth)


def _entity(
    node: dict[str, Any],
    *,
    depth_key: str,
    height: float,
    centre_z: float,
    observed_at: str,
    label: str,
) -> SemanticEntity:
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        raise ProjectionError("scene node has no id")
    return SemanticEntity(
        entity_id=node_id,
        label=label,
        kind="agent" if node_id == _AGENT_ID else "furniture",
        position=Vec3(
            _number(node.get("x"), f"{node_id}.x"),
            _number(node.get("y"), f"{node_id}.y"),
            centre_z,
        ),
        orientation=Quaternion(),
        bounds=Bounds3(
            _number(node.get("width"), f"{node_id}.width"),
            _number(node.get(depth_key), f"{node_id}.{depth_key}"),
            height,
        ),
        affordances=_affordances(_declared_label(node) or label, node),
        observed_at=observed_at,
        updated_at=observed_at,
    )


@dataclass(frozen=True)
class SemanticProjection:
    """One renderer's scene, expressed in the shared semantic contract."""

    renderer: Literal["screen", "vr"]
    world: SemanticWorld
    vertical_axis: bool
    # Whether floor distances mean metres. The VR document is a floor plan and
    # they do. The 2D document is a *picture*: its `height` is how tall a thing
    # is drawn, not how much floor it stands on, so the avatar measures 92 deep
    # on screen and 20 in the headset for the same body. Distances are still
    # comparable within the picture — which is why proximity gating works — but
    # planning a walk with real clearance around pictorial footprints seals the
    # room, so navigation asks for this before it will plan.
    floor_is_metric: bool
    # Per-entity reach, read the way the live gate has always read it: a
    # declared `reach` capped at the ceiling, otherwise derived from the
    # footprint. Held here rather than on the entity because how far the agent
    # can stretch is an authorization policy, not a fact about the furniture.
    reaches: dict[str, float] = field(default_factory=dict)
    # Names worn by more than one object, and therefore naming none of them.
    ambiguous_labels: frozenset[str] = frozenset()
    # Entities whose declared name was contested. They keep their place in the
    # world but the gate refuses them, so a decoy cannot answer for the real
    # object and the real object cannot be reached by the contested name either.
    withheld_ids: frozenset[str] = frozenset()

    def relation(
        self, subject: str, relation: str, reference: str, **kwargs: Any
    ) -> RelationEvidence:
        """Answer a spatial question, refusing the ones this renderer cannot.

        A screen scene has no heights, so "is the lamp above the desk?" has no
        answer here. Returning `False` would be a lie a gate could act on;
        raising makes the missing axis the caller's problem, which is where it
        belongs.
        """
        if relation in VERTICAL_RELATIONS and not self.vertical_axis:
            raise ProjectionError(
                f"the {self.renderer} projection has no vertical axis; "
                f"'{relation}' cannot be evaluated"
            )
        return self.world.relation(subject, relation, reference, **kwargs)

    def line_of_sight(
        self, observer: str, target: str, **kwargs: Any
    ) -> RelationEvidence:
        return self.world.line_of_sight(observer, target, **kwargs)

    def agent(self) -> SemanticEntity | None:
        for entity in self.world.entities:
            if entity.kind == "agent":
                return entity
        return None

    def reachable_labels(self, *, within: float | None = None) -> list[str]:
        """The labels the proximity gate should currently admit, failing closed.

        This is the authorization question, answered from the semantic world
        rather than from a renderer's node dictionaries. Distance is measured
        on the floor plane in both renderers, because the agent walks the floor
        and an object mounted high on a wall is still at arm's length.

        `within` overrides every object's own reach, which is how a caller asks
        a different question — "what is within 300 units?" — without changing
        what the gate itself allows. The gate always passes None.
        """
        agent = self.agent()
        if agent is None:
            # No avatar means no position, and no position means nothing is
            # reachable. Unreadable is treated as unreachable.
            return []
        admitted: list[str] = []
        for entity in self.world.entities:
            if entity.kind == "agent":
                continue
            if entity.entity_id in self.withheld_ids:
                continue
            limit = (
                self.reaches.get(entity.entity_id, 0.0) if within is None else within
            )
            flat = math.dist(
                (agent.position.x, agent.position.y),
                (entity.position.x, entity.position.y),
            )
            if flat <= limit:
                admitted.append(entity.label)
        return admitted


def _project(
    stage: Any,
    *,
    renderer: Literal["screen", "vr"],
    depth_key: str,
    extent_height: float,
    observed_at: str | None,
) -> SemanticProjection:
    if not isinstance(stage, dict):
        raise ProjectionError("scene document must be an object")
    nodes = stage.get("nodes")
    if not isinstance(nodes, list):
        raise ProjectionError("scene document has no nodes")
    world_block = stage.get("world") if isinstance(stage.get("world"), dict) else {}
    units = world_block.get("units_per_meter", 100.0)
    moment = observed_at or _now_iso()

    scene_nodes = [node for node in nodes if isinstance(node, dict)]
    labels, ambiguous, withheld_ids = _resolve_labels(scene_nodes)
    withheld = set(withheld_ids)
    entities: list[SemanticEntity] = []
    reaches: dict[str, float] = {}
    for node in scene_nodes:
        if renderer == "vr":
            height = _number(node.get("height"), "node.height")
            centre_z = _number(node.get("elevation"), "node.elevation")
        else:
            height = SCREEN_NOMINAL_HEIGHT
            centre_z = SCREEN_NOMINAL_HEIGHT / 2
        entity = _entity(
            node,
            depth_key=depth_key,
            height=height,
            centre_z=centre_z,
            observed_at=moment,
            label=labels[node["id"]],
        )
        entities.append(entity)
        reaches[entity.entity_id] = _reach_of(
            node, entity.bounds.width, entity.bounds.depth
        )
        # VR props are manipulable scene contents, never capability anchors.
        # They remain in the semantic world for collision and spatial reasoning,
        # but moving one beside the avatar must not unlock an object-bound tool.
        if renderer == "vr" and node.get("type") == "prop":
            withheld.add(entity.entity_id)

    if not entities:
        raise ProjectionError("scene document projected to an empty world")

    # The extent is derived from what is actually in the room rather than from
    # the declared canvas: a saved scene may hold an object the canvas has
    # since shrunk away from, and refusing to project it would lock its tools
    # over a presentation detail.
    margin = 1.0
    lows = [entity.aabb.minimum for entity in entities]
    highs = [entity.aabb.maximum for entity in entities]
    extent = AABB(
        minimum=Vec3(
            min(point.x for point in lows) - margin,
            min(point.y for point in lows) - margin,
            min(min(point.z for point in lows) - margin, -margin),
        ),
        maximum=Vec3(
            max(point.x for point in highs) + margin,
            max(point.y for point in highs) + margin,
            max(max(point.z for point in highs) + margin, extent_height),
        ),
    )

    world = SemanticWorld(
        frame_id=f"{renderer}-room",
        units_per_meter=float(units) if isinstance(units, (int, float)) else 100.0,
        extent=extent,
        entities=tuple(entities),
        observed_at=moment,
    )
    try:
        world.validate()
    except SemanticWorldValidationError as exc:
        raise ProjectionError(
            f"{renderer} scene is not a valid semantic world: {exc}"
        ) from exc
    return SemanticProjection(
        renderer=renderer,
        world=world,
        vertical_axis=renderer == "vr",
        floor_is_metric=renderer == "vr",
        reaches=reaches,
        ambiguous_labels=ambiguous,
        withheld_ids=frozenset(withheld),
    )


def project_screen_stage(
    stage: Any, *, observed_at: str | None = None
) -> SemanticProjection:
    """Project the 2D stage document; its `height` is a floor depth, not a height."""
    return _project(
        stage,
        renderer="screen",
        depth_key="height",
        extent_height=SCREEN_NOMINAL_HEIGHT + 1.0,
        observed_at=observed_at,
    )


def project_vr_stage(
    stage: Any, *, observed_at: str | None = None
) -> SemanticProjection:
    """Project the VR stage document, which carries a real vertical axis."""
    room = (
        stage.get("room")
        if isinstance(stage, dict) and isinstance(stage.get("room"), dict)
        else {}
    )
    ceiling = room.get("height", 280.0)
    return _project(
        stage,
        renderer="vr",
        depth_key="depth",
        extent_height=float(ceiling) if isinstance(ceiling, (int, float)) else 280.0,
        observed_at=observed_at,
    )


# --- parity ------------------------------------------------------------------

# Relations that depend only on the floor plane, and are therefore required to
# agree between the two renderers.
FLOOR_RELATIONS = ("near", "left_of", "right_of", "in_front_of", "behind")


@dataclass(frozen=True)
class ParityReport:
    """What the two renderers disagree about on the shared floor plane."""

    matches: bool
    differences: tuple[str, ...]
    compared_entities: int
    compared_relations: int

    def require_parity(self) -> None:
        if not self.matches:
            raise ProjectionError(
                "screen and VR projections disagree: " + "; ".join(self.differences)
            )


def _floor_facts(entity: SemanticEntity) -> tuple[Any, ...]:
    return (
        entity.label,
        entity.kind,
        round(entity.position.x, 6),
        round(entity.position.y, 6),
        round(entity.bounds.width, 6),
        round(entity.bounds.depth, 6),
        entity.occupancy,
        tuple(sorted(entity.affordances)),
        entity.owner_ref,
    )


def parity_report(
    screen: SemanticProjection,
    vr: SemanticProjection,
    *,
    relations: Iterable[str] = FLOOR_RELATIONS,
    evaluated_at: datetime | None = None,
) -> ParityReport:
    """Compare what the two renderers claim about the floor plane.

    Only the floor plane: VR knows how tall the bookshelf is and the screen
    never will, so demanding agreement there would fail forever for a reason
    that has nothing to do with correctness. Everything an object-bound tool
    gate actually consults — which objects exist, what they are called, what
    they afford, where they stand, and how they sit relative to each other on
    the floor — must agree exactly.
    """
    differences: list[str] = []
    screen_by_id = {entity.entity_id: entity for entity in screen.world.entities}
    vr_by_id = {entity.entity_id: entity for entity in vr.world.entities}

    only_screen = sorted(set(screen_by_id) - set(vr_by_id))
    only_vr = sorted(set(vr_by_id) - set(screen_by_id))
    for entity_id in only_screen:
        differences.append(f"{entity_id}: present on screen, absent in VR")
    for entity_id in only_vr:
        differences.append(f"{entity_id}: present in VR, absent on screen")

    shared = sorted(set(screen_by_id) & set(vr_by_id))
    for entity_id in shared:
        left = _floor_facts(screen_by_id[entity_id])
        right = _floor_facts(vr_by_id[entity_id])
        if left != right:
            differences.append(f"{entity_id}: floor facts differ {left} != {right}")

    compared_relations = 0
    moment = evaluated_at or datetime.now(timezone.utc)
    for relation in relations:
        for subject in shared:
            for reference in shared:
                if subject == reference:
                    continue
                compared_relations += 1
                on_screen = screen.relation(
                    subject, relation, reference, evaluated_at=moment
                ).holds
                in_vr = vr.relation(
                    subject, relation, reference, evaluated_at=moment
                ).holds
                if on_screen != in_vr:
                    differences.append(
                        f"{subject} {relation} {reference}: screen={on_screen} vr={in_vr}"
                    )

    return ParityReport(
        matches=not differences,
        differences=tuple(differences),
        compared_entities=len(shared),
        compared_relations=compared_relations,
    )


def unlocked_from(projection: SemanticProjection, standing_at: str) -> frozenset[str]:
    """Every affordance the agent obtains by standing at one object.

    This is the question the proximity gate actually asks, so it is the
    question parity has to answer. Reach is derived from each object's own
    footprint by the same rule the live gate uses, rather than being read from
    the scene document, so a renderer cannot widen its own reach.
    """
    from .world_model import reach_for

    origin = projection.world.require(standing_at)
    unlocked: set[str] = set()
    for entity in projection.world.entities:
        if entity.kind == "agent":
            continue
        reach = reach_for(entity.bounds.width, entity.bounds.depth)
        # Floor-plane distance: the agent walks the floor, and an object being
        # mounted high on a wall does not put it out of arm's reach.
        flat = (
            (entity.position.x - origin.position.x) ** 2
            + (entity.position.y - origin.position.y) ** 2
        ) ** 0.5
        if flat <= reach:
            unlocked |= entity.affordances
    return frozenset(unlocked)


@dataclass(frozen=True)
class CapabilityParityReport:
    """Whether the two renderers grant the same capabilities from the same object."""

    matches: bool
    differences: tuple[str, ...]
    compared_objects: int
    cross_unlocks: tuple[str, ...]

    def require_parity(self) -> None:
        if not self.matches:
            raise ProjectionError(
                "screen and VR grant different capabilities: "
                + "; ".join(self.differences)
            )


def capability_parity_report(
    screen: SemanticProjection, vr: SemanticProjection
) -> CapabilityParityReport:
    """The hard gate: identical capabilities from identical positions.

    The two renderers legitimately draw the room differently — a calendar hangs
    flat on a wall in VR and occupies a legible band on screen — so demanding
    identical coordinates would fail for presentation reasons. What may never
    differ is what the agent is *allowed to do*. Standing at the computer must
    unlock the computer's tools and nothing else, in both renderers; if one of
    them lets the archive shelf answer for the computer, an attacker picks that
    renderer.

    `cross_unlocks` records any object whose position hands it a second
    object's affordances, which is the spatial-spoofing failure the 150-unit
    separation rule exists to prevent.
    """
    differences: list[str] = []
    cross: list[str] = []
    screen_ids = {entity.entity_id for entity in screen.world.entities}
    vr_ids = {entity.entity_id for entity in vr.world.entities}
    shared = sorted(screen_ids & vr_ids)

    for entity_id in sorted(screen_ids ^ vr_ids):
        differences.append(f"{entity_id}: exists in only one renderer")

    compared = 0
    for entity_id in shared:
        entity = screen.world.require(entity_id)
        if entity.kind == "agent":
            continue
        compared += 1
        on_screen = unlocked_from(screen, entity_id)
        in_vr = unlocked_from(vr, entity_id)
        if on_screen != in_vr:
            only_screen = sorted(on_screen - in_vr)
            only_vr = sorted(in_vr - on_screen)
            differences.append(
                f"standing at {entity_id}: screen-only={only_screen} vr-only={only_vr}"
            )
        for renderer, unlocked in (("screen", on_screen), ("vr", in_vr)):
            foreign = unlocked - entity.affordances
            if foreign:
                cross.append(
                    f"{renderer}: standing at {entity_id} also unlocks {sorted(foreign)}"
                )

    return CapabilityParityReport(
        matches=not differences,
        differences=tuple(differences),
        compared_objects=compared,
        cross_unlocks=tuple(cross),
    )
