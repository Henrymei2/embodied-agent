"""Obstacle-aware navigation over the semantic floor plane.

`world_model.path_to` returned a single straight leg, which was honest about
being a placeholder and wrong as soon as anything stood in the way: the agent
would report arriving at the reading chair having walked through the bed. A
gate that authorizes tools on arrival then authorizes them on the strength of
a walk that could not physically happen.

This module plans on the shared semantic world instead, so both renderers get
the same route, and it refuses rather than improvises: when there is no way
through, callers get a typed reason and the object that blocked it, not a
straight line and an optimistic arrival.

The planner is a reduced visibility graph. Obstacles are the furniture
footprints grown by the agent's own radius, so the agent is planned as a point
and never clips a corner it could not actually round. Candidate turning points
are the grown corners — for axis-aligned rectangles an optimal route only ever
turns at one — plus the start and the goal. That makes the result exact and
deterministic rather than resolution-dependent, which matters because a grid
that quietly rounds a doorway shut is a navigation bug that looks like a
policy refusal.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .semantic_projection import SemanticProjection
from .semantic_world import SemanticEntity, SemanticWorld

# How far the agent keeps from furniture it is not using. Enough to round a
# corner without the renderer showing an elbow through a bookshelf.
AGENT_RADIUS = 35.0

# Where the agent stops when it walks to something on purpose. Closer than a
# reach so the tool gate opens, far enough that a companion is not standing
# inside the furniture it is using.
SOCIAL_DISTANCE = 45.0

# Grazing tolerance. Two grown rectangles that share an edge must still admit
# the path that runs along it, or a room whose furniture is flush against a
# wall becomes unnavigable.
_EPSILON = 1e-6

NO_PATH_REASONS = frozenset(
    {
        "target_unknown",
        "start_inside_obstacle",
        "goal_inside_obstacle",
        "goal_outside_extent",
        "no_clear_route",
        "no_clear_dock",
        "pictorial_floor",
    }
)


@dataclass(frozen=True)
class Rect:
    """A grown floor footprint the agent must not enter."""

    entity_id: str
    left: float
    right: float
    back: float
    front: float

    def contains(self, x: float, y: float) -> bool:
        return (
            self.left - _EPSILON <= x <= self.right + _EPSILON
            and self.back - _EPSILON <= y <= self.front + _EPSILON
        )

    def corners(self) -> tuple[tuple[float, float], ...]:
        return (
            (self.left, self.back),
            (self.left, self.front),
            (self.right, self.back),
            (self.right, self.front),
        )

    def blocks(self, ax: float, ay: float, bx: float, by: float) -> bool:
        """Whether the segment passes through this rectangle's interior.

        Shrunk by epsilon so a route that runs exactly along an edge — the
        common case once footprints are grown until they touch — is allowed.
        """
        left, right = self.left + _EPSILON, self.right - _EPSILON
        back, front = self.back + _EPSILON, self.front - _EPSILON
        if left >= right or back >= front:
            return False
        near_t, far_t = 0.0, 1.0
        for origin, delta, low, high in (
            (ax, bx - ax, left, right),
            (ay, by - ay, back, front),
        ):
            if abs(delta) <= _EPSILON:
                if origin < low or origin > high:
                    return False
                continue
            inverse = 1.0 / delta
            entry, exit_ = (low - origin) * inverse, (high - origin) * inverse
            if entry > exit_:
                entry, exit_ = exit_, entry
            near_t, far_t = max(near_t, entry), min(far_t, exit_)
            if near_t > far_t:
                return False
        return True


@dataclass(frozen=True)
class NavigationResult:
    """A planned route, or a typed reason there is none."""

    reachable: bool
    waypoints: tuple[tuple[float, float], ...]
    distance: float
    reason: str | None = None
    blocked_by: str | None = None

    def __post_init__(self) -> None:
        if self.reason is not None and self.reason not in NO_PATH_REASONS:
            raise ValueError(f"undeclared navigation reason: {self.reason}")


def _footprint(entity: SemanticEntity, *, margin: float) -> Rect:
    return Rect(
        entity_id=entity.entity_id,
        left=entity.position.x - entity.bounds.width / 2 - margin,
        right=entity.position.x + entity.bounds.width / 2 + margin,
        back=entity.position.y - entity.bounds.depth / 2 - margin,
        front=entity.position.y + entity.bounds.depth / 2 + margin,
    )


def obstacles(
    world: SemanticWorld,
    *,
    ignoring: frozenset[str] = frozenset(),
    margin: float = AGENT_RADIUS,
) -> tuple[Rect, ...]:
    """Grown footprints of everything solid the agent must route around.

    Zones are regions rather than objects — "the reading corner" is somewhere
    to stand, not something to walk around — and the agent never obstructs
    itself.
    """
    return tuple(
        _footprint(entity, margin=margin)
        for entity in world.entities
        if entity.kind not in ("agent", "zone") and entity.entity_id not in ignoring
    )


def _blocker(
    field: tuple[Rect, ...], ax: float, ay: float, bx: float, by: float
) -> str | None:
    for rect in field:
        if rect.blocks(ax, ay, bx, by):
            return rect.entity_id
    return None


def plan_route(
    world: SemanticWorld,
    start: tuple[float, float],
    goal: tuple[float, float],
    *,
    ignoring: frozenset[str] = frozenset(),
    margin: float = AGENT_RADIUS,
) -> NavigationResult:
    """Plan a floor route from ``start`` to ``goal``, or explain why there is none."""
    world.validate()
    field = obstacles(world, ignoring=ignoring, margin=margin)

    inside_start = next((r for r in field if r.contains(*start)), None)
    if inside_start is not None:
        return NavigationResult(
            False, (), math.inf, "start_inside_obstacle", inside_start.entity_id
        )
    inside_goal = next((r for r in field if r.contains(*goal)), None)
    if inside_goal is not None:
        return NavigationResult(
            False, (), math.inf, "goal_inside_obstacle", inside_goal.entity_id
        )
    extent = world.extent
    if not (
        extent.minimum.x <= goal[0] <= extent.maximum.x
        and extent.minimum.y <= goal[1] <= extent.maximum.y
    ):
        return NavigationResult(False, (), math.inf, "goal_outside_extent", None)

    nodes: list[tuple[float, float]] = [start, goal]
    for rect in field:
        nodes.extend(
            corner
            for corner in rect.corners()
            if not any(other.contains(*corner) for other in field if other is not rect)
        )
    # Deterministic order so two runs of the same room return the same route.
    nodes = [start, goal] + sorted(set(nodes[2:]))

    count = len(nodes)
    best = [math.inf] * count
    previous: list[int | None] = [None] * count
    best[0] = 0.0
    visited = [False] * count
    queue: list[tuple[float, int]] = [(0.0, 0)]

    while queue:
        cost, index = heapq.heappop(queue)
        if visited[index]:
            continue
        visited[index] = True
        if index == 1:
            break
        ax, ay = nodes[index]
        for other in range(count):
            if visited[other]:
                continue
            bx, by = nodes[other]
            if _blocker(field, ax, ay, bx, by) is not None:
                continue
            step = math.dist((ax, ay), (bx, by))
            if cost + step < best[other]:
                best[other] = cost + step
                previous[other] = index
                heapq.heappush(queue, (best[other], other))

    if not math.isfinite(best[1]):
        # Name what stood in the way of the direct line: it is the most useful
        # thing to say, even when the true obstruction is the whole layout.
        return NavigationResult(
            False,
            (),
            math.inf,
            "no_clear_route",
            _blocker(field, start[0], start[1], goal[0], goal[1]),
        )

    route: list[tuple[float, float]] = []
    cursor: int | None = 1
    while cursor is not None:
        route.append(nodes[cursor])
        cursor = previous[cursor]
    route.reverse()
    return NavigationResult(True, tuple(route[1:]), best[1], None, None)


def dock_at(
    projection: SemanticProjection,
    target_name: str,
    *,
    social_distance: float = SOCIAL_DISTANCE,
    margin: float = AGENT_RADIUS,
) -> NavigationResult:
    """Route the agent to a comfortable standing point beside ``target_name``.

    The target itself is removed from the obstacle field — the agent is walking
    *to* it, and leaving it in would make every destination unreachable by
    construction. Candidates are the four cardinal approaches; the nearest one
    with a real route wins, and ties resolve north/east/south/west so the same
    room always produces the same walk.
    """
    if not projection.floor_is_metric:
        # Refuse rather than plan on a picture. Measured on the shipped 2D
        # room, real clearance around pictorial footprints leaves 5 of 15
        # objects with no approach at all — the planner would be reporting
        # "you cannot reach the bookshelf" about a room the user can see is
        # open. An advisory route a gate might trust is worse than a refusal.
        return NavigationResult(False, (), math.inf, "pictorial_floor", None)
    world = projection.world
    target = world.find(target_name)
    if target is None:
        return NavigationResult(False, (), math.inf, "target_unknown", None)
    agent = next((e for e in world.entities if e.kind == "agent"), None)
    if agent is None:
        return NavigationResult(False, (), math.inf, "target_unknown", None)

    start = (agent.position.x, agent.position.y)
    half_width = target.bounds.width / 2
    half_depth = target.bounds.depth / 2
    offset = social_distance
    candidates = (
        (target.position.x, target.position.y - half_depth - offset),
        (target.position.x + half_width + offset, target.position.y),
        (target.position.x, target.position.y + half_depth + offset),
        (target.position.x - half_width - offset, target.position.y),
    )

    ignoring = frozenset({target.entity_id})
    best: NavigationResult | None = None
    for candidate in candidates:
        result = plan_route(world, start, candidate, ignoring=ignoring, margin=margin)
        if result.reachable and (best is None or result.distance < best.distance):
            best = result
    if best is None:
        return NavigationResult(False, (), math.inf, "no_clear_dock", target.entity_id)
    return best
