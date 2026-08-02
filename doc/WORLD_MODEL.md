# The world model

## 1. Why this exists

The room used to be pixels all the way down. Reachability was 150 of the same
units the canvas was measured in, duplicated across the backend and two
frontend contexts. Semantics and presentation shared one node object, so every
tool that asked "where is the bookshelf?" was reading a structure that also
held its texture, its spritesheet row, and its z-order. Swapping the renderer
would have meant rewriting every tool, every test, and the room-layout
invariant along with it.

`src/open_llm_vtuber/world_model.py` is the seam. It answers where things are,
how far apart they are, what the agent can reach, and how long a walk takes,
without referring to how any of it is drawn. Tools and the proximity gate read
the world through it. A different renderer supplies a different presentation
and a loader for the same types; nothing above the seam changes.

## 2. What the document says

The stage document now carries two separate descriptions of the room:

| Block | Answers | Read by |
| --- | --- | --- |
| `canvas` | How big is the picture, what is behind it | The 2D renderer |
| `world` | What is a unit worth, how far can the agent reach, how fast does it walk | Everything else |

```json
"world": {
  "units_per_meter": 100,
  "reach_ceiling": 150,
  "move_speed": 400
}
```

One unit is a centimetre, so the shipped room is 9.6m by 5.4m and the agent
walks at 4 m/s. The block is optional: a document written before it existed is
read with these same defaults, which are the values the room has always been
gated on.

## 3. Reach is a property of each object

A bed is not an hourglass. Each object is usable from within its own `reach`,
in world units:

```
reach = min(reach_ceiling, 60 + max(width, height) / 2)
```

Sixty units is an arm — a real one, at the shipped scale. The rest is half the
object's footprint, so reach is measured from the edge of the thing rather
than from a point at its centre. In the default room that produces 105 for the
hourglass and 150 for the bed, the computer, the window, and the sofa.

A node may write `reach` explicitly to lower its own. A node claiming more
than `reach_ceiling` is rejected by both validators, and the world model clamps
anything that reaches it anyway. This is a safety rule, not a tidiness one: an
object usable from across the room would hand the agent every other object's
tools from where it stands. The default room keeps every gated object more than
`reach_ceiling` from every other one, which is what makes the ceiling
sufficient — [FURNITURE_TOOLS.md](FURNITURE_TOOLS.md) covers the gate itself.

A node whose position cannot be read is left out of the world entirely, so
unreadable geometry is treated as unreachable rather than as reachable.

Reach is resolved by label, so labels have to name exactly one thing. If two
objects share a name, `reachable_labels` withholds it from the gate rather than
trusting whichever is nearer: the gate is asked "is the agent near the
computer?", and a room with two computers cannot answer. Refusing costs a tool
until the room is fixed; trusting would let a decoy stand in for the real
object. Saving such a room is refused outright — see
[FURNITURE_TOOLS.md](FURNITURE_TOOLS.md).

`effective_label` is what all of that means by "name". A blank or whitespace-only
label is not a name, so the id stands in; runs of whitespace are collapsed, so
`reading  chair` cannot become a second object beside `reading chair`. It lives
here rather than in the validator because the world is what the gate reads: when
the two disagreed about what an object was called, a room could pass validation
and still arrive here ambiguous — a node labelled `widget` beside another whose
*id* was `widget` saved cleanly and then locked both objects' tools for good.

`ambiguous_labels` folds case for the same reason. Comparing exactly would make
this backstop weaker than the rule it backs up, since `computer` and `Computer`
are a collision the validators refuse but an exact comparison would wave
through.

## 4. Walking is a contract, not an animation

`move_to` used to set a position and wait 900 ms, which meant a short hop took
as long as crossing the room and the tool learned only that its request had
been accepted.

Now the tool sends a **path** — a list of waypoints — and the renderer walks the
legs in order, each timed by its own distance over `move_speed` and clamped to
250–1800 ms so the demo neither stalls nor teleports. The renderer reports
`arrived`, `duration_ms`, and the final position.

The backend then checks the claim. The frontend's stage is persisted before the
tool call resolves, so `move_to` re-reads the saved room and confirms the avatar
is within two units of where it was sent. A walk that was interrupted is
reported as one:

```
Error: the walk to bookshelf did not finish; the agent is at x=60, y=515.
```

`World.path_to` returns a single leg today, because the room has no obstacle
map and a straight line is what the 2D renderer has always drawn. The point is
that the contract is already the one a navmesh needs: adding pathfinding
changes that one function, and the protocol, the tool, and the renderer stay
as they are.

## 5. Projection

Furniture windows used to be positioned from the object's own DOM rectangle,
which is the one thing a 3D renderer could not supply. `DynamicStage` now
exposes a single projection from a room position to a screen position, used
both to place objects and to anchor the windows that point at them. Replace it
with a camera and the windows keep following their objects.

## 6. What 3D would still need

The decoupling above is what had to happen first. What remains is renderer
work rather than surgery on the rest of the system:

- **A renderer and assets.** Models or billboards for fifteen objects, a room,
  and an avatar with the animation states the spritesheet currently covers.
- **A loader.** Build `World` from whatever the 3D scene stores. Everything
  above the seam — the gate, the tools, the click channel, the layout
  invariant — reads only these types.
- **An elevation axis.** `WorldPoint` is 2D because the room is. A third
  coordinate belongs here rather than in any tool.
- **Pathfinding.** Fill in `World.path_to`. The waypoint contract is already
  carried end to end.
- **A camera projection.** Replace the viewport projection described above.

None of that is required by the research question the 2D room already
demonstrates, which is why 3D remains deferred — see
[PROJECT_STATUS.md](PROJECT_STATUS.md).

The experimental Meta Quest 3 MVP deliberately narrows this checklist. It keeps
world reasoning on the horizontal `x/y` floor plane, adds elevation only to a
separate VR presentation document, uses straight paths with collision-free
endpoints rather than a navmesh, and renders existing assets on cuboids and an
avatar billboard. Its implementation contract and pending hardware checks are in
[VR_MODE.md](VR_MODE.md).

## 7. Where to look

| Concern | File |
| --- | --- |
| World types, reach, paths, arrival | `src/open_llm_vtuber/world_model.py` |
| Document, validation, migration | `src/open_llm_vtuber/stage_state_manager.py` |
| Proximity gate | `src/open_llm_vtuber/mcpp/tool_manager.py` |
| Spatial tools | `mcp_servers/scene_tools.py` |
| Renderer, projection, motion timing | `frontend/.../canvas/dynamic-stage.tsx` |
| Document contract, `move_to` legs | `frontend/.../context/dynamic-stage-context.tsx` |
| Protocol | [frontend/frontend_api_doc.md](../frontend/frontend_api_doc.md) |
