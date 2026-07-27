"""A renderer-agnostic view of the room.

The stage document is a 2D drawing: textures, spritesheet rows, z-order,
panel styling. Where things *are* — how far apart, what the agent can reach
from where, how long it takes to walk there — is a different question, and
this module answers it without referring to any of that. Tools and the
proximity gate read the world through here rather than reaching into node
dictionaries, so replacing the renderer means writing a new loader for these
types instead of rewriting every tool and every test.

Distances are world units, and `units_per_meter` says what a unit is worth.
At the shipped scale one unit is a centimetre, which is what makes a reach of
105 legible: just over a metre, an arm's length from a small object's edge.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from loguru import logger

# One world unit is a centimetre, so the shipped 960x540 room is 9.6m x 5.4m.
UNITS_PER_METER = 100.0

# How far the agent can reach past the edge of a thing it is standing at.
PERSONAL_REACH = 60.0

# No object may claim a reach beyond this, however it is written into the
# document. The default room keeps every tool-gated object more than this far
# from every other one, so the ceiling is what guarantees that standing at one
# object can never unlock another object's tools.
REACH_CEILING = 150.0

# Walking pace, in world units per second: 4 m/s. Fast for a person, but this
# is a companion crossing a room on demand, and a demo should not wait.
MOVE_SPEED = 400.0

# How close to the destination counts as having arrived.
ARRIVAL_TOLERANCE = 2.0

AGENT_ID = "avatar"


def _number(value: Any) -> float | None:
    """Coerce a JSON value to a float, rejecting bools and non-finite numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


# Complaints already made. A broken room is read several times a turn, and the
# same warning on every read buries everything else in the log.
_warned: set[Any] = set()


def warn_once(key: Any, message: str) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(message)


def effective_label(node: Any) -> str | None:
    """The name an object actually answers to, or None if it has none.

    The gate matches on this string, so every layer that reasons about names —
    validation, repair, the world itself — has to agree on what it is. A blank
    or whitespace-only label is not a name, so the id stands in; runs of
    whitespace are collapsed, so ``"reading  chair"`` cannot quietly become a
    second object that the room's own furniture is already called.
    """
    if not isinstance(node, dict):
        return None
    label = node.get("label")
    if isinstance(label, str):
        normalized = " ".join(label.split())
        if normalized:
            return normalized
    node_id = node.get("id")
    return node_id if isinstance(node_id, str) and node_id else None


def reach_for(
    width: float,
    height: float,
    *,
    ceiling: float = REACH_CEILING,
) -> float:
    """Derive an object's reach from its footprint.

    An arm's length from the edge of the thing, so a bed is usable from
    further away than an hourglass, capped at the room's separation budget.
    """
    half_span = max(abs(width), abs(height)) / 2.0
    return min(ceiling, round(PERSONAL_REACH + half_span, 1))


@dataclass(frozen=True)
class WorldPoint:
    x: float
    y: float

    def distance_to(self, other: "WorldPoint") -> float:
        return math.dist((self.x, self.y), (other.x, other.y))


@dataclass(frozen=True)
class WorldObject:
    """Something in the room, described without reference to how it is drawn."""

    id: str
    label: str
    position: WorldPoint
    footprint: tuple[float, float]
    reach: float
    interactive: bool

    def is_reachable_from(
        self,
        point: WorldPoint,
        *,
        within: float | None = None,
    ) -> bool:
        """Whether this object can be used from ``point``.

        ``within`` overrides this object's own reach, which is how a caller
        asks a different question ("what is within 300 units?") without
        changing what the gate itself allows.
        """
        limit = self.reach if within is None else within
        return self.position.distance_to(point) <= limit


@dataclass(frozen=True)
class World:
    """The room as the agent reasons about it."""

    units_per_meter: float
    reach_ceiling: float
    move_speed: float
    extent: tuple[float, float]
    objects: tuple[WorldObject, ...]
    agent: WorldObject | None

    @property
    def agent_position(self) -> WorldPoint | None:
        return self.agent.position if self.agent else None

    def find(self, name: str) -> WorldObject | None:
        """Look an object up by id or by label, the way a tool names it."""
        if not isinstance(name, str) or not name:
            return None
        for obj in self.objects:
            if obj.id == name or obj.label == name:
                return obj
        return None

    def reachable_from(
        self,
        point: WorldPoint | None,
        *,
        within: float | None = None,
    ) -> tuple[WorldObject, ...]:
        """Objects usable from ``point``; nothing at all without a position."""
        if point is None:
            return ()
        return tuple(
            obj for obj in self.objects if obj.is_reachable_from(point, within=within)
        )

    def ambiguous_labels(self) -> frozenset[str]:
        """Names worn by more than one object, which therefore name nothing.

        Case-folded, because that is the collision the save-time validators
        refuse. Comparing exactly would leave this weaker than the rule it
        exists to back up: a room holding `computer` and `Computer` would look
        unambiguous here while being unsavable there.
        """
        seen: set[str] = set()
        repeated: set[str] = set()
        for obj in self.objects:
            key = obj.label.casefold()
            (repeated if key in seen else seen).add(key)
        return frozenset(repeated)

    def reachable_labels(self, *, within: float | None = None) -> list[str]:
        """Labels the proximity gate should currently admit, failing closed.

        A label shared by two objects is withheld even when the agent is
        standing at one of them. The gate asks "is the agent near the
        computer?", and a room containing two computers cannot answer that;
        refusing costs a tool until the room is fixed, while trusting would let
        a decoy placed next to the agent stand in for the real object.
        """
        ambiguous = self.ambiguous_labels()
        if ambiguous:
            # Once per distinct set of names: the gate resolves reachability
            # several times a turn, and a room that cannot be repaired would
            # otherwise fill the log for the rest of the session.
            warn_once(
                ambiguous,
                "World: refusing ambiguous object label(s) "
                f"{sorted(ambiguous)}; the tools bound to them stay locked.",
            )
        return [
            obj.label
            for obj in self.reachable_from(self.agent_position, within=within)
            if obj.label.casefold() not in ambiguous
        ]

    def path_to(self, destination: WorldPoint) -> tuple[WorldPoint, ...]:
        """Waypoints the agent should walk through to reach ``destination``.

        One leg today: the room has no obstacle map, and a straight line is
        what the 2D renderer has always drawn. The contract is what matters —
        callers hand the renderer a *path* and are told where it ended up, so
        adding real pathfinding later changes this function and nothing else.
        """
        return (destination,)

    def travel_seconds(self, path: Sequence[WorldPoint]) -> float:
        """How long walking ``path`` from the agent's position should take."""
        start = self.agent_position
        if start is None or not path:
            return 0.0
        total = 0.0
        previous = start
        for waypoint in path:
            total += previous.distance_to(waypoint)
            previous = waypoint
        speed = self.move_speed if self.move_speed > 0 else MOVE_SPEED
        return total / speed

    def has_arrived_at(
        self,
        destination: WorldPoint,
        *,
        tolerance: float = ARRIVAL_TOLERANCE,
    ) -> bool:
        """Whether the agent is actually standing at ``destination``."""
        position = self.agent_position
        return position is not None and position.distance_to(destination) <= tolerance


def _object_from_node(node: Any, ceiling: float) -> WorldObject | None:
    """Build a world object from a stage node, or None if it has no place.

    A node whose geometry cannot be read is left out of the world entirely,
    which means the gate never admits it: unreadable is treated as unreachable.
    """
    if not isinstance(node, dict):
        return None
    node_id = node.get("id")
    if not isinstance(node_id, str) or not node_id:
        return None

    x = _number(node.get("x"))
    y = _number(node.get("y"))
    if x is None or y is None:
        return None

    width = _number(node.get("width")) or 0.0
    height = _number(node.get("height")) or 0.0

    declared = _number(node.get("reach"))
    reach = (
        min(declared, ceiling)
        if declared is not None and declared > 0
        else reach_for(width, height, ceiling=ceiling)
    )

    interaction = node.get("interaction")

    return WorldObject(
        id=node_id,
        label=effective_label(node) or node_id,
        position=WorldPoint(x, y),
        footprint=(abs(width), abs(height)),
        reach=reach,
        interactive=bool(
            isinstance(interaction, dict) and interaction.get("on_click") is True
        ),
    )


def _setting(block: Any, key: str, fallback: float, *, minimum: float = 0.0) -> float:
    if not isinstance(block, dict):
        return fallback
    value = _number(block.get(key))
    if value is None or value <= minimum:
        return fallback
    return value


def world_from_stage(stage: Any) -> World:
    """Read the world out of a stage document.

    Every setting has a fallback, so a document written before the `world`
    block existed still yields the same room the gate has always enforced.
    """
    stage = stage if isinstance(stage, dict) else {}
    block = stage.get("world")
    canvas = stage.get("canvas") if isinstance(stage.get("canvas"), dict) else {}

    ceiling = _setting(block, "reach_ceiling", REACH_CEILING)
    objects: list[WorldObject] = []
    agent: WorldObject | None = None

    nodes: Iterable[Any] = (
        stage.get("nodes") if isinstance(stage.get("nodes"), list) else []
    )
    for node in nodes:
        obj = _object_from_node(node, ceiling)
        if obj is None:
            continue
        if obj.id == AGENT_ID:
            agent = obj
        else:
            objects.append(obj)

    return World(
        units_per_meter=_setting(block, "units_per_meter", UNITS_PER_METER),
        reach_ceiling=ceiling,
        move_speed=_setting(block, "move_speed", MOVE_SPEED),
        extent=(
            _setting(canvas, "width", 0.0),
            _setting(canvas, "height", 0.0),
        ),
        objects=tuple(objects),
        agent=agent,
    )


def default_world_block() -> dict[str, float]:
    """The `world` block a freshly built stage document carries."""
    return {
        "units_per_meter": UNITS_PER_METER,
        "reach_ceiling": REACH_CEILING,
        "move_speed": MOVE_SPEED,
    }
