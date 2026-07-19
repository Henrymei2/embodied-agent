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
| `move_to` | `target: string` or numeric `x`, `y` | validated stage `state` after the 900 ms movement |
| `list_objects` | optional numeric `distance` | `objects: StageNode[]` |
| `get_scene_status` | none | `agent_position`, `reachable_objects` |

Reachability means a center-to-center distance of at most 150 stage units.

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
| `stage_patch_state` | `patch` | Merge canvas/nodes/metadata or remove node IDs |
| `stage_reset_state` | optional `conf_uid` | Restore the default stage |
| `stage_list_capabilities` | none | Return supported actions, nodes, and limits |

Patch shape:

```json
{
  "canvas": { "background": "#0f172a" },
  "nodes": [],
  "remove_node_ids": ["reading_chair"],
  "metadata": { "updated_by": "example" }
}
```

## 5. Stage document v3

```json
{
  "version": 3,
  "canvas": {
    "width": 960,
    "height": 540,
    "background": "linear-gradient(135deg, #dbeafe, #fef3c7)"
  },
  "nodes": [],
  "metadata": {
    "conf_uid": "mao_pro_001",
    "updated_at": "2026-07-13T20:00:00+00:00",
    "updated_by": "frontend_action"
  }
}
```

Supported node types: `shape`, `text`, `image`, `avatar`, and `panel`. A node
requires a unique safe ID, numeric `x`, `y`, `width`, and `height`, and may have
label/text/style, a safe image source, a furniture texture, an avatar
spritesheet, or a declarative `uiWindow`. The stage is capped at 80 nodes.

Asset sources may be `http(s)` URLs, `data:image` base64 URLs, or relative
public paths such as `./stage-assets/chair.png`. `javascript:` URLs and unknown
style/asset fields are rejected in both the frontend and backend.

Furniture example:

```json
{
  "id": "reading_chair",
  "type": "panel",
  "label": "reading_chair",
  "x": 320,
  "y": 180,
  "width": 120,
  "height": 90,
  "zIndex": 2,
  "text": "reading chair",
  "texture": {
    "src": "./stage-assets/chair.png",
    "fit": "contain",
    "alt": "reading chair"
  },
  "uiWindow": {
    "title": "Reading chair",
    "body": "A comfortable place to read."
  }
}
```

`uiWindow` accepts `title`, optional `body`, and an optional allowlisted
`provider`. The currently supported dynamic provider is
`google_calendar_status`; arbitrary HTML, URLs, scripts, and provider names are
rejected by both validators.

## 6. Connection synchronization

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
