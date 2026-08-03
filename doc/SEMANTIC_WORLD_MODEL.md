# Semantic world model 2.0

Part 6 begins with one shared semantic frame for screen, VR, tools, planners,
and authorization. `semantic_world.py` defines strict version-2 entities with
3D position, normalized orientation, physical bounds, containment, support,
visibility evidence, occupancy, ownership, affordances, and observation/motion
times.

The contract deliberately contains no mesh, material, texture, sprite, style,
or z-order field. Those belong to renderer projections and are rejected by the
strict record loader. A renderer can present semantic state; it cannot create
an authoritative action outcome merely by changing presentation data.

Coordinates use a right-handed shared frame: positive X is right, positive Y
is forward, and positive Z is up. `units_per_meter` converts scene units to
physical distance. Version-2 axis relations use semantic axis-aligned bounds;
orientation is validated now and will feed oriented collision/navigation work
in the next Part 6 slice.

Validated relations currently include near, left/right, above/below,
front/behind, transitive inside/on graphs, explicit observer visibility, and a
bounded recently-moved predicate. Every binary query returns typed,
timestamped evidence with the geometric or graph basis used for the result.

This first slice is intentionally below the renderer adapters. Next work will
project both persisted screen and VR scenes into this contract, introduce
obstacle-aware navigation and line-of-sight evidence, and freeze a cross-mode
spatial-language benchmark.
