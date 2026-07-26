# Frontend Action Protocol

## 1. Purpose

The backend uses a request/result protocol over `/client-ws` to inspect or
change the live 2D stage. Every request has a unique `request_id`; the result is
accepted only from the client that owns that ID. Character and client identity
are also carried through the internal MCP bridge, preventing actions from being
routed to a different connected frontend.

## 2. Request and result envelopes

Backend to frontend:

```json
{
  "type": "frontend_action_request",
  "request_id": "2c9cb643-20ad-4b22-93bc-ec50d1b34d40",
  "action": "move_to",
  "args": {
    "target": "calendar"
  }
}
```

Frontend to backend:

```json
{
  "type": "frontend_action_result",
  "request_id": "2c9cb643-20ad-4b22-93bc-ec50d1b34d40",
  "result": {
    "ok": true,
    "action": "move_to",
    "state": {}
  }
}
```

Failed actions return `"ok": false` and a human-readable `error`. State-changing
results include the complete validated `state`; the backend persists it under
the active character configuration before resolving the MCP call. If backend
validation fails, the frontend receives the last authoritative `stage-state`.

## 3. Spatial actions

| Action | Arguments | Result |
| --- | --- | --- |
| `get_object_position` | `name: string` | `position: {x, y}` |
| `move_to` | `target: string` or numeric `x`, `y`, plus optional `waypoints` | validated stage `state`, `arrived`, `duration_ms`, `position` |
| `list_objects` | optional numeric `distance` | `objects: StageNode[]` |
| `get_scene_status` | none | `agent_position`, `reachable_objects` |

Reachability is per object: each one is usable from within its own `reach`,
which it may declare and otherwise derives from its footprint, so a bed can be
used from further away than an hourglass. No object's reach may exceed
`world.reach_ceiling`. See [the world model](../doc/WORLD_MODEL.md).

### `move_to`

`args.waypoints` is a list of `{x, y}` the avatar walks in order; without it
the destination is the only leg. Each leg lasts its own distance divided by
`world.move_speed`, clamped to 250–1800 ms, so a short hop no longer takes as
long as crossing the room.

```json
{
  "type": "frontend_action_request",
  "request_id": "walk-1",
  "action": "move_to",
  "args": {
    "x": 600,
    "y": 370,
    "waypoints": [{ "x": 600, "y": 370 }]
  }
}
```

```json
{
  "type": "frontend_action_result",
  "request_id": "walk-1",
  "result": {
    "ok": true,
    "arrived": true,
    "duration_ms": 620,
    "position": { "x": 600, "y": 370 },
    "state": {}
  }
}
```

The backend does not take `ok` for an answer: after the stage is persisted it
re-reads the room and confirms the avatar is actually within two units of the
destination, so a walk that was interrupted is reported as one rather than
silently treated as an arrival.

### `get_object_position`

```json
{
  "type": "frontend_action_request",
  "request_id": "position-1",
  "action": "get_object_position",
  "args": { "name": "bed" }
}
```

```json
{
  "type": "frontend_action_result",
  "request_id": "position-1",
  "result": {
    "ok": true,
    "position": { "x": 700, "y": 270 }
  }
}
```

### `list_objects`

```json
{
  "type": "frontend_action_request",
  "request_id": "list-1",
  "action": "list_objects",
  "args": { "distance": 200 }
}
```

```json
{
  "type": "frontend_action_result",
  "request_id": "list-1",
  "result": {
    "ok": true,
    "objects": [
      { "id": "calendar", "label": "calendar", "x": 480, "y": 420 }
    ]
  }
}
```

### `get_scene_status`

```json
{
  "type": "frontend_action_result",
  "request_id": "status-1",
  "result": {
    "ok": true,
    "agent_position": { "x": 480, "y": 270 },
    "reachable_objects": ["calendar"]
  }
}
```

## 4. Stage document actions

| Action | Arguments | Behavior |
| --- | --- | --- |
| `stage_get_state` | none | Return the current stage document |
| `stage_set_state` | `state` | Replace the stage after validation |
| `stage_patch_state` | `patch` | Merge canvas/world/nodes/metadata or remove node IDs |
| `stage_reset_state` | optional `conf_uid` | Restore the default stage |
| `stage_list_capabilities` | none | Return supported actions, nodes, and limits |

Patch shape:

```json
{
  "canvas": { "background": "#0f172a" },
  "world": { "move_speed": 300 },
  "nodes": [],
  "remove_node_ids": ["plant_stand"],
  "metadata": { "updated_by": "example" }
}
```

Patch nodes are merged field by field into the node with the same ID, so a patch
may carry a single field. This is how the avatar is seated without resending its
spritesheet:

```json
{ "nodes": [{ "id": "avatar", "pose": "sitting" }] }
```

New IDs are appended as complete nodes. `move_to` clears `pose` as part of the
same update that moves the avatar, so a seated sprite is never shown mid-stride.

## 5. Stage document v8

```json
{
  "version": 8,
  "canvas": {
    "width": 960,
    "height": 540,
    "background": "linear-gradient(135deg, #dbeafe, #fef3c7)"
  },
  "world": {
    "units_per_meter": 100,
    "reach_ceiling": 150,
    "move_speed": 400
  },
  "nodes": [],
  "metadata": {
    "conf_uid": "mao_pro_001",
    "updated_at": "2026-07-13T20:00:00+00:00",
    "updated_by": "frontend_action"
  }
}
```

`canvas` describes the picture; `world` describes the room. Distance, reach,
and walking speed are read from `world`, never from the canvas, which is what
lets a different renderer draw the same room. One unit is a centimetre at the
shipped scale, so the room is 9.6m by 5.4m and a reach of 105 is just over an
arm's length. The block is optional: a document without it is read with the
shipped defaults, which are the values the room has always been gated on.

Supported node types: `shape`, `text`, `image`, `avatar`, and `panel`. A node
requires a unique safe ID, numeric `x`, `y`, `width`, and `height`, and may have
label/text/style, a safe image source, a furniture texture, an avatar
spritesheet, a declarative `uiWindow`, a `pose` of `standing` or `sitting`, an
`interaction` of `{ "on_click": true }`, or a `reach`. The stage is capped at
80 nodes.

`reach` is how close the agent must stand to use the object, in world units. A
node may lower its own; a node claiming more than `world.reach_ceiling` is
rejected by both validators, because an object usable from across the room
would hand the agent every other object's tools from where it stands. Omitted,
it is derived from the footprint as `60 + max(width, height) / 2`, capped at
the ceiling.

Nodes are painted by `zIndex`: wall and back-band furniture at 2, front-band
furniture at 3, and the avatar at 4, so the avatar is never hidden behind the
room.

Asset sources may be `http(s)` URLs, `data:image` base64 URLs, or relative
public paths such as `./stage-assets/chair.png`. `javascript:` URLs and unknown
style/asset fields are rejected in both the frontend and backend.

Furniture example:

```json
{
  "id": "plant_stand",
  "type": "panel",
  "label": "plant stand",
  "x": 320,
  "y": 180,
  "width": 120,
  "height": 90,
  "zIndex": 2,
  "text": "plant stand",
  "texture": {
    "src": "./stage-assets/plant.png",
    "fit": "contain",
    "alt": "plant stand"
  },
  "uiWindow": {
    "title": "Plant stand",
    "body": "Something green in the corner."
  }
}
```

`uiWindow` accepts `title`, optional `body`, and an optional allowlisted
`provider`. Arbitrary HTML, URLs, scripts, and provider names are rejected by
both validators. Supported dynamic providers:

| Provider | Object | Route polled while open | Shows |
| --- | --- | --- | --- |
| `google_calendar_status` | `calendar` | `/api/integrations/google/status` | Connection state and an authorize button |
| `google_gmail_unread` | `mailbox` | `/api/integrations/google/gmail/unread` | Unread senders and subjects |
| `todo_list` | `todo_list` | `/api/furniture/todo` | Open items by priority, plus an overflow count |
| `timer_status` | `hourglass` | `/api/furniture/timers` | Pending timers with a live countdown |
| `bookshelf` | `bookshelf` | `/api/furniture/bookshelf` | Shelved books, newest first |
| `reading_status` | `reading chair` | `/api/furniture/reading` | The open book, its progress bar, and recent notes |
| `plan_status` | `writing desk` | `/api/furniture/plans` | Pending plans with Approve and Decline buttons |
| `room_layout` | `design board` | none | The stage document the frontend already holds |
| `skill_status` | `tool rack` | `/api/furniture/skills` | Draft skills with Approve and Decline, and the skills in use |

The `/api/furniture/*` GET routes are read-only projections of the same stores
the agent edits through proximity-gated MCP tools. They return only what the
panel renders — no file paths — and expose no way to mutate state.

`POST /api/furniture/plans/{plan_id}/decision` and
`POST /api/furniture/skills/{slug}/decision`, each with `{"decision": "approved"}`
or `{"decision": "declined"}`, are the only exceptions — and the only writers of
those verdicts anywhere in the system. Both apply to pending items only, so a
decided plan or skill cannot be flipped, and no MCP tool can reach either route.

Migration: a stored v1 through v7 stage is upgraded on load. Older documents also
gain any furniture added by newer default rooms, because a tool stays hidden
while the object it is bound to is missing from the scene. A custom layout,
including moved or removed objects, is otherwise preserved — except when the
default room's `theme_revision` changes, which rebuilds the room from the
defaults. v6 did exactly that: with a dozen gated objects, the layout had to be
rebuilt for each to keep its own 150-unit radius. v7 adds the tool rack in the
last wall position that still satisfies it, so v6 rooms migrate in place. v8
adds the `world` block and per-object `reach`, both of which are read with
defaults when absent, so v7 rooms migrate in place too — the block is merged
field by field rather than replaced, and nothing moves.

## 6. Clicking an object

A node with `interaction: { "on_click": true }` answers a click by telling the
agent the user pointed at it:

```json
{ "type": "object-interaction", "object_id": "bookshelf" }
```

That is the entire message. The prompt is composed on the backend from the
*stored* stage document, so this channel carries an object id and nothing else —
a click cannot put words in the user's mouth, and `interaction` deliberately
has no field for text. An id that is not in the room is ignored.

The turn is queued at user priority, and the transcript shows the gesture
(`*points at the bookshelf*`) rather than the instructions sent to the model.
The agent is told whether it is standing near that object, so it can walk over
before using it. Repeat clicks on the same object inside three seconds are
dropped, on both sides, so a room full of clickable things cannot become a
queue of replies.

Hovering still opens the object's window; the click is free to mean the more
interesting thing.

## 7. Connection synchronization

On connection, the backend sends:

```json
{
  "type": "stage-state",
  "stage": {}
}
```

The frontend validates this document before rendering it. Scene MCP reads use
the same persisted document, so the LLM's spatial view, execution-time safety
gate, and visible avatar stay consistent across restarts.
