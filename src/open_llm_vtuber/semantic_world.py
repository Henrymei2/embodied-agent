"""Renderer-independent semantic 3D world contracts and spatial grounding."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

SEMANTIC_WORLD_SCHEMA_VERSION = 2
ENTITY_KINDS = frozenset({"agent", "furniture", "container", "item", "surface", "zone"})
OCCUPANCY_STATES = frozenset({"free", "occupied", "reserved", "unknown"})
SPATIAL_RELATIONS = frozenset(
    {
        "near",
        "left_of",
        "right_of",
        "above",
        "below",
        "in_front_of",
        "behind",
        "inside",
        "on",
        "visible_from",
    }
)

# Every way this module is allowed to justify a relation. The set is closed so
# that a basis string is a claim about *how* something was established rather
# than free text: an authorization gate can then refuse a relation it does not
# consider strong enough. `declared_visibility_claim` is the weak one — it
# repeats what a scene document asserted, which per D-019 is a renderer's
# proposal and not spatial authority. `clear_line_of_sight` is derived here
# from geometry and is the only visibility basis a gate should accept.
RELATION_BASES = frozenset(
    {
        "aabb_surface_distance",
        "world_x_axis",
        "world_z_axis",
        "positive_world_y_axis",
        "validated_containment_graph",
        "validated_support_graph",
        "declared_visibility_claim",
        "clear_line_of_sight",
        "occluded_by_entity",
    }
)

# Bases that were computed here from validated geometry. A declared field that
# merely arrived in a document never joins this set.
DERIVED_RELATION_BASES = RELATION_BASES - {"declared_visibility_claim"}

_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_ENTITY_FIELDS = {
    "entity_id",
    "label",
    "kind",
    "position",
    "orientation",
    "bounds",
    "containment_parent",
    "support_parent",
    "visible_from",
    "occupancy",
    "occupants",
    "owner_ref",
    "affordances",
    "observed_at",
    "updated_at",
    "moved_at",
}


class SemanticWorldValidationError(ValueError):
    """Semantic geometry or graph data violates the closed world contract."""


def _finite(value: Any, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise SemanticWorldValidationError(f"{field_name} must be a finite number")
    return float(value)


def _positive(value: Any, field_name: str) -> float:
    number = _finite(value, field_name)
    if number <= 0:
        raise SemanticWorldValidationError(f"{field_name} must be positive")
    return number


def _token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _TOKEN.fullmatch(value):
        raise SemanticWorldValidationError(
            f"{field_name} must be a bounded opaque identifier"
        )
    return value


def _timestamp(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise SemanticWorldValidationError(f"{field_name} must be a timestamp")
    normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SemanticWorldValidationError(
            f"{field_name} must be an ISO 8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise SemanticWorldValidationError(
            f"{field_name} must include an explicit offset"
        )
    return parsed.astimezone(timezone.utc)


def _utc_datetime(value: datetime | None, field_name: str) -> datetime:
    moment = value or datetime.now(timezone.utc)
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise SemanticWorldValidationError(
            f"{field_name} must include an explicit offset"
        )
    return moment.astimezone(timezone.utc)


@dataclass(frozen=True)
class Vec3:
    """A position or vector in the shared right-handed world frame."""

    x: float
    y: float
    z: float

    def validate(self, field_name: str = "vector") -> None:
        _finite(self.x, f"{field_name}.x")
        _finite(self.y, f"{field_name}.y")
        _finite(self.z, f"{field_name}.z")

    def distance_to(self, other: "Vec3") -> float:
        self.validate("vector.start")
        other.validate("vector.end")
        return math.dist((self.x, self.y, self.z), (other.x, other.y, other.z))


@dataclass(frozen=True)
class Quaternion:
    """A normalized world-space orientation quaternion."""

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0

    def validate(self, field_name: str = "orientation") -> None:
        components = tuple(
            _finite(value, f"{field_name}.{axis}")
            for axis, value in zip("xyzw", (self.x, self.y, self.z, self.w))
        )
        norm = math.sqrt(sum(value * value for value in components))
        if not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise SemanticWorldValidationError(
                f"{field_name} quaternion must be normalized"
            )


@dataclass(frozen=True)
class Bounds3:
    """Axis-aligned semantic size, independent of meshes and sprites."""

    width: float
    depth: float
    height: float

    def validate(self, field_name: str = "bounds") -> None:
        _positive(self.width, f"{field_name}.width")
        _positive(self.depth, f"{field_name}.depth")
        _positive(self.height, f"{field_name}.height")


@dataclass(frozen=True)
class AABB:
    minimum: Vec3
    maximum: Vec3

    def validate(self, field_name: str = "aabb") -> None:
        self.minimum.validate(f"{field_name}.minimum")
        self.maximum.validate(f"{field_name}.maximum")
        if not (
            self.minimum.x < self.maximum.x
            and self.minimum.y < self.maximum.y
            and self.minimum.z < self.maximum.z
        ):
            raise SemanticWorldValidationError(
                f"{field_name} minimum must be below maximum on every axis"
            )

    def contains(self, other: "AABB", *, tolerance: float = 0.0) -> bool:
        return (
            self.minimum.x - tolerance <= other.minimum.x
            and self.minimum.y - tolerance <= other.minimum.y
            and self.minimum.z - tolerance <= other.minimum.z
            and self.maximum.x + tolerance >= other.maximum.x
            and self.maximum.y + tolerance >= other.maximum.y
            and self.maximum.z + tolerance >= other.maximum.z
        )

    def surface_distance(self, other: "AABB") -> float:
        gaps = (
            max(self.minimum.x - other.maximum.x, other.minimum.x - self.maximum.x, 0),
            max(self.minimum.y - other.maximum.y, other.minimum.y - self.maximum.y, 0),
            max(self.minimum.z - other.maximum.z, other.minimum.z - self.maximum.z, 0),
        )
        return math.sqrt(sum(gap * gap for gap in gaps))

    def overlaps_xy(self, other: "AABB", *, tolerance: float = 0.0) -> bool:
        return (
            self.minimum.x <= other.maximum.x + tolerance
            and self.maximum.x + tolerance >= other.minimum.x
            and self.minimum.y <= other.maximum.y + tolerance
            and self.maximum.y + tolerance >= other.minimum.y
        )


def _segment_intersects_aabb(start: Vec3, end: Vec3, box: AABB) -> bool:
    """Whether the segment ``start``→``end`` passes through ``box``.

    The slab method, clamped to the segment rather than an infinite ray: a
    cabinet standing *behind* the thing being looked at is not between the
    eye and it, and must not be reported as blocking the view.
    """
    near_t, far_t = 0.0, 1.0
    for axis in ("x", "y", "z"):
        origin = getattr(start, axis)
        delta = getattr(end, axis) - origin
        low = getattr(box.minimum, axis)
        high = getattr(box.maximum, axis)
        if abs(delta) <= 1e-12:
            # Parallel to this slab: it can only ever miss, never clip.
            if origin < low or origin > high:
                return False
            continue
        inverse = 1.0 / delta
        entry = (low - origin) * inverse
        exit_ = (high - origin) * inverse
        if entry > exit_:
            entry, exit_ = exit_, entry
        near_t = max(near_t, entry)
        far_t = min(far_t, exit_)
        if near_t > far_t:
            return False
    return True


@dataclass(frozen=True)
class SemanticEntity:
    """One semantic thing; no mesh, material, texture, sprite, or UI styling."""

    entity_id: str
    label: str
    kind: Literal["agent", "furniture", "container", "item", "surface", "zone"]
    position: Vec3
    orientation: Quaternion
    bounds: Bounds3
    containment_parent: str | None = None
    support_parent: str | None = None
    visible_from: frozenset[str] = field(default_factory=frozenset)
    occupancy: Literal["free", "occupied", "reserved", "unknown"] = "unknown"
    occupants: frozenset[str] = field(default_factory=frozenset)
    owner_ref: str | None = None
    affordances: frozenset[str] = field(default_factory=frozenset)
    observed_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    moved_at: str | None = None

    @property
    def aabb(self) -> AABB:
        half_width = self.bounds.width / 2
        half_depth = self.bounds.depth / 2
        half_height = self.bounds.height / 2
        return AABB(
            minimum=Vec3(
                self.position.x - half_width,
                self.position.y - half_depth,
                self.position.z - half_height,
            ),
            maximum=Vec3(
                self.position.x + half_width,
                self.position.y + half_depth,
                self.position.z + half_height,
            ),
        )

    def validate(self) -> None:
        _token(self.entity_id, "entity.entity_id")
        if (
            not isinstance(self.label, str)
            or not self.label.strip()
            or self.label != " ".join(self.label.split())
            or len(self.label) > 160
        ):
            raise SemanticWorldValidationError(
                "entity.label must be normalized and contain 1 to 160 characters"
            )
        if self.kind not in ENTITY_KINDS:
            raise SemanticWorldValidationError("entity.kind is invalid")
        self.position.validate("entity.position")
        self.orientation.validate("entity.orientation")
        self.bounds.validate("entity.bounds")
        self.aabb.validate("entity.aabb")
        for field_name, reference in (
            ("containment_parent", self.containment_parent),
            ("support_parent", self.support_parent),
            ("owner_ref", self.owner_ref),
        ):
            if reference is not None:
                _token(reference, f"entity.{field_name}")
        if (
            self.containment_parent == self.entity_id
            or self.support_parent == self.entity_id
        ):
            raise SemanticWorldValidationError(
                "an entity cannot contain or support itself"
            )
        for field_name, values in (
            ("visible_from", self.visible_from),
            ("occupants", self.occupants),
            ("affordances", self.affordances),
        ):
            if not isinstance(values, frozenset) or len(values) > 256:
                raise SemanticWorldValidationError(
                    f"entity.{field_name} must be a bounded frozen set"
                )
            for value in values:
                _token(value, f"entity.{field_name}")
        if self.entity_id in self.visible_from or self.entity_id in self.occupants:
            raise SemanticWorldValidationError(
                "visibility and occupancy relationships cannot reference self"
            )
        if self.occupancy not in OCCUPANCY_STATES:
            raise SemanticWorldValidationError("entity.occupancy is invalid")
        if (self.occupancy == "occupied") != bool(self.occupants):
            raise SemanticWorldValidationError(
                "occupied state and occupants must agree"
            )
        observed = _timestamp(self.observed_at, "entity.observed_at")
        updated = _timestamp(self.updated_at, "entity.updated_at")
        if updated < observed:
            raise SemanticWorldValidationError(
                "entity.updated_at cannot precede observed_at"
            )
        if self.moved_at is not None:
            moved = _timestamp(self.moved_at, "entity.moved_at")
            if moved > updated:
                raise SemanticWorldValidationError(
                    "entity.moved_at cannot follow updated_at"
                )


@dataclass(frozen=True)
class RelationEvidence:
    subject_id: str
    relation: str
    reference_id: str
    holds: bool
    basis: str
    evaluated_at: str
    # Which entity broke the relation, when the geometry can name one. A gate
    # that refuses an action owes the user more than "no": `occluded_by_entity`
    # says the sight line failed, and this says the wardrobe is what failed it.
    occluder_id: str | None = None

    def validate(self) -> None:
        _token(self.subject_id, "relation.subject_id")
        _token(self.reference_id, "relation.reference_id")
        if self.relation not in SPATIAL_RELATIONS:
            raise SemanticWorldValidationError("relation type is invalid")
        if not isinstance(self.holds, bool):
            raise SemanticWorldValidationError("relation result must be boolean")
        _token(self.basis, "relation.basis")
        if self.basis not in RELATION_BASES:
            raise SemanticWorldValidationError("relation basis is not a declared basis")
        if self.occluder_id is not None:
            _token(self.occluder_id, "relation.occluder_id")
        _timestamp(self.evaluated_at, "relation.evaluated_at")


@dataclass(frozen=True)
class SemanticWorld:
    """Validated semantic scene shared by screen, VR, tools, and planners."""

    frame_id: str
    units_per_meter: float
    extent: AABB
    entities: tuple[SemanticEntity, ...]
    observed_at: str
    schema_version: int = SEMANTIC_WORLD_SCHEMA_VERSION
    # Validation result for this immutable world, kept so that asking a scene
    # a hundred spatial questions costs one validation instead of a hundred.
    # `init=False` is the point: if a caller could pass this in, a document
    # could arrive carrying its own "already validated" marker and skip every
    # geometry and graph check in this file.
    _index: dict[str, SemanticEntity] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def validate(self) -> None:
        self._validated_index()

    def _validated_index(self) -> dict[str, SemanticEntity]:
        """Validate once, then answer from the cache.

        The world is frozen and its entities are frozen, so a scene that
        validated a moment ago is still valid now. Re-deriving that on every
        relation query made a single question on a large room cost as much as
        loading the room.
        """
        cached = self._index
        if cached is not None:
            return cached
        index = self._validate_now()
        object.__setattr__(self, "_index", index)
        return index

    def _validate_now(self) -> dict[str, SemanticEntity]:
        if self.schema_version != SEMANTIC_WORLD_SCHEMA_VERSION:
            raise SemanticWorldValidationError("unsupported semantic world schema")
        _token(self.frame_id, "world.frame_id")
        units = _positive(self.units_per_meter, "world.units_per_meter")
        if units > 1_000_000:
            raise SemanticWorldValidationError("world.units_per_meter is unreasonable")
        self.extent.validate("world.extent")
        _timestamp(self.observed_at, "world.observed_at")
        if not isinstance(self.entities, tuple) or len(self.entities) > 10_000:
            raise SemanticWorldValidationError("world.entities must be a bounded tuple")
        by_id: dict[str, SemanticEntity] = {}
        labels: set[str] = set()
        for entity in self.entities:
            entity.validate()
            if entity.entity_id in by_id:
                raise SemanticWorldValidationError("entity ids must be unique")
            label = entity.label.casefold()
            if label in labels:
                raise SemanticWorldValidationError("entity labels must be unique")
            by_id[entity.entity_id] = entity
            labels.add(label)
            if not self.extent.contains(entity.aabb, tolerance=1e-6):
                raise SemanticWorldValidationError(
                    f"entity {entity.entity_id} lies outside the world extent"
                )

        for entity in self.entities:
            for field_name, reference in (
                ("containment_parent", entity.containment_parent),
                ("support_parent", entity.support_parent),
            ):
                if reference is not None and reference not in by_id:
                    raise SemanticWorldValidationError(
                        f"entity {entity.entity_id} has an unknown {field_name}"
                    )
            for reference in entity.visible_from | entity.occupants:
                if reference not in by_id:
                    raise SemanticWorldValidationError(
                        f"entity {entity.entity_id} references an unknown entity"
                    )

        self._validate_acyclic(by_id, "containment_parent")
        self._validate_acyclic(by_id, "support_parent")
        tolerance = max(1e-6, self.units_per_meter * 0.02)
        for entity in self.entities:
            if entity.containment_parent is not None:
                parent = by_id[entity.containment_parent]
                if not parent.aabb.contains(entity.aabb, tolerance=tolerance):
                    raise SemanticWorldValidationError(
                        f"entity {entity.entity_id} is not inside its containment parent"
                    )
            if entity.support_parent is not None:
                support = by_id[entity.support_parent]
                gap = abs(entity.aabb.minimum.z - support.aabb.maximum.z)
                if gap > tolerance or not entity.aabb.overlaps_xy(
                    support.aabb, tolerance=tolerance
                ):
                    raise SemanticWorldValidationError(
                        f"entity {entity.entity_id} is not on its support parent"
                    )
        return by_id

    @staticmethod
    def _validate_acyclic(by_id: dict[str, SemanticEntity], field_name: str) -> None:
        """Prove no containment or support chain loops, in one pass overall.

        Walking the whole chain from every entity re-walks the same tail once
        per ancestor, so a deep shelf-inside-cabinet-inside-alcove room cost
        time quadratic in its own depth. Nodes proven acyclic stay proven:
        `settled` is never revisited, and `walking` marks the chain currently
        under the cursor, which is the only place a cycle can close.
        """
        settled: set[str] = set()
        for start in by_id:
            if start in settled:
                continue
            walking: dict[str, None] = {}
            current: str | None = start
            while current is not None and current not in settled:
                if current in walking:
                    raise SemanticWorldValidationError(
                        f"{field_name} relationships cannot contain cycles"
                    )
                walking[current] = None
                current = getattr(by_id[current], field_name)
            settled.update(walking)

    def find(self, name: str) -> SemanticEntity | None:
        index = self._validated_index()
        direct = index.get(name)
        if direct is not None:
            return direct
        for entity in self.entities:
            if entity.label == name:
                return entity
        return None

    def require(self, name: str) -> SemanticEntity:
        entity = self.find(name)
        if entity is None:
            raise KeyError(f"semantic entity does not exist: {name}")
        return entity

    def relation(
        self,
        subject_name: str,
        relation: str,
        reference_name: str,
        *,
        near_meters: float = 1.5,
        evaluated_at: datetime | None = None,
    ) -> RelationEvidence:
        self.validate()
        if relation not in SPATIAL_RELATIONS:
            raise SemanticWorldValidationError("relation type is invalid")
        near_distance = _positive(near_meters, "relation.near_meters")
        subject = self.require(subject_name)
        reference = self.require(reference_name)
        if subject.entity_id == reference.entity_id:
            raise SemanticWorldValidationError(
                "a binary spatial relation requires two different entities"
            )
        first = subject.aabb
        second = reference.aabb
        tolerance = max(1e-6, self.units_per_meter * 0.02)
        if relation == "near":
            holds = (
                first.surface_distance(second) <= near_distance * self.units_per_meter
            )
            basis = "aabb_surface_distance"
        elif relation == "left_of":
            holds = first.maximum.x <= second.minimum.x + tolerance
            basis = "world_x_axis"
        elif relation == "right_of":
            holds = first.minimum.x + tolerance >= second.maximum.x
            basis = "world_x_axis"
        elif relation == "above":
            holds = first.minimum.z + tolerance >= second.maximum.z
            basis = "world_z_axis"
        elif relation == "below":
            holds = first.maximum.z <= second.minimum.z + tolerance
            basis = "world_z_axis"
        elif relation == "in_front_of":
            holds = first.minimum.y + tolerance >= second.maximum.y
            basis = "positive_world_y_axis"
        elif relation == "behind":
            holds = first.maximum.y <= second.minimum.y + tolerance
            basis = "positive_world_y_axis"
        elif relation == "inside":
            holds = self._has_ancestor(
                subject.entity_id, reference.entity_id, "containment_parent"
            )
            basis = "validated_containment_graph"
        elif relation == "on":
            holds = self._has_ancestor(
                subject.entity_id, reference.entity_id, "support_parent"
            )
            basis = "validated_support_graph"
        else:
            # Only what the scene document claimed. Nothing here was checked
            # against geometry, so the basis says so and `line_of_sight` is
            # what an authorization gate is expected to ask instead.
            holds = reference.entity_id in subject.visible_from
            basis = "declared_visibility_claim"
        moment = _utc_datetime(evaluated_at, "relation.evaluated_at")
        evidence = RelationEvidence(
            subject_id=subject.entity_id,
            relation=relation,
            reference_id=reference.entity_id,
            holds=holds,
            basis=basis,
            evaluated_at=moment.isoformat(),
        )
        evidence.validate()
        return evidence

    def line_of_sight(
        self,
        observer_name: str,
        target_name: str,
        *,
        evaluated_at: datetime | None = None,
    ) -> RelationEvidence:
        """Derive visibility from geometry rather than believing the document.

        `visible_from` is a field in a scene file, and a scene file is a
        renderer's proposal (D-019). A client that could write
        ``visible_from: [safe]`` onto an entity forty metres away and behind a
        wall would otherwise hand itself the evidence a gate asks for. This
        traces the actual segment between the two entities and reports the
        first thing standing in it.

        Zones are skipped: a named region such as "reading corner" is a label
        for an area, not a wall, and treating it as one would blind the agent
        to everything standing inside it.
        """
        index = self._validated_index()
        observer = self.require(observer_name)
        target = self.require(target_name)
        if observer.entity_id == target.entity_id:
            raise SemanticWorldValidationError(
                "a binary spatial relation requires two different entities"
            )
        occluder: str | None = None
        for entity in self.entities:
            if entity.entity_id in (observer.entity_id, target.entity_id):
                continue
            if entity.kind == "zone":
                continue
            if _segment_intersects_aabb(
                observer.position, target.position, entity.aabb
            ):
                # Deterministic: nearest blocker to the observer wins, so the
                # same room always names the same obstruction.
                if occluder is None or observer.position.distance_to(
                    index[entity.entity_id].position
                ) < observer.position.distance_to(index[occluder].position):
                    occluder = entity.entity_id
        moment = _utc_datetime(evaluated_at, "relation.evaluated_at")
        evidence = RelationEvidence(
            subject_id=observer.entity_id,
            relation="visible_from",
            reference_id=target.entity_id,
            holds=occluder is None,
            basis="clear_line_of_sight" if occluder is None else "occluded_by_entity",
            evaluated_at=moment.isoformat(),
            occluder_id=occluder,
        )
        evidence.validate()
        return evidence

    def _has_ancestor(self, subject_id: str, ancestor_id: str, field_name: str) -> bool:
        by_id = self._validated_index()
        current = getattr(by_id[subject_id], field_name)
        while current is not None:
            if current == ancestor_id:
                return True
            current = getattr(by_id[current], field_name)
        return False

    def recently_moved(
        self,
        name: str,
        *,
        at: datetime | None = None,
        within: timedelta = timedelta(seconds=30),
    ) -> bool:
        self.validate()
        if not isinstance(within, timedelta) or within <= timedelta(0):
            raise SemanticWorldValidationError(
                "recent movement window must be positive"
            )
        entity = self.require(name)
        if entity.moved_at is None:
            return False
        moment = _utc_datetime(at, "movement.evaluated_at")
        moved = _timestamp(entity.moved_at, "entity.moved_at")
        return moved <= moment < moved + within


def entity_from_record(record: dict[str, Any]) -> SemanticEntity:
    """Build a strict semantic entity and reject renderer or unknown fields."""

    if not isinstance(record, dict) or set(record) != _ENTITY_FIELDS:
        raise SemanticWorldValidationError("semantic entity record fields are invalid")
    try:
        entity = SemanticEntity(
            **{
                **record,
                "position": Vec3(**record["position"]),
                "orientation": Quaternion(**record["orientation"]),
                "bounds": Bounds3(**record["bounds"]),
                "visible_from": frozenset(record["visible_from"]),
                "occupants": frozenset(record["occupants"]),
                "affordances": frozenset(record["affordances"]),
            }
        )
    except (KeyError, TypeError) as exc:
        raise SemanticWorldValidationError(
            "semantic entity record has invalid nested geometry"
        ) from exc
    entity.validate()
    return entity


def record_from_entity(entity: SemanticEntity) -> dict[str, Any]:
    """Serialize an entity back to the exact closed record shape.

    `entity_from_record` demands the full field set and nothing else, so
    without its inverse a semantic world could be parsed but never written,
    stored, or sent to another process. Sets are sorted so two runs of the
    same scene produce byte-identical records and can be digested.
    """
    entity.validate()
    return {
        "entity_id": entity.entity_id,
        "label": entity.label,
        "kind": entity.kind,
        "position": {
            "x": entity.position.x,
            "y": entity.position.y,
            "z": entity.position.z,
        },
        "orientation": {
            "x": entity.orientation.x,
            "y": entity.orientation.y,
            "z": entity.orientation.z,
            "w": entity.orientation.w,
        },
        "bounds": {
            "width": entity.bounds.width,
            "depth": entity.bounds.depth,
            "height": entity.bounds.height,
        },
        "containment_parent": entity.containment_parent,
        "support_parent": entity.support_parent,
        "visible_from": sorted(entity.visible_from),
        "occupancy": entity.occupancy,
        "occupants": sorted(entity.occupants),
        "owner_ref": entity.owner_ref,
        "affordances": sorted(entity.affordances),
        "observed_at": entity.observed_at,
        "updated_at": entity.updated_at,
        "moved_at": entity.moved_at,
    }
