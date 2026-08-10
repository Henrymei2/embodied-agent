# VR presentation mode

## 1. Status and objective

This document is the implementation and acceptance contract for the VR
presentation experience. The software foundation and automated checks are in
place, while Meta Quest 3 hardware acceptance in section 9 remains pending.
The validated 2D stage remains the default renderer outside an immersive
session.

The MVP turns the browser frontend into an immersive 3D room for Meta Quest 3
and Touch Plus controllers. It preserves the companion's voice, persistent
spatial embodiment, furniture-bound tools, and object interactions while
replacing the former pixel billboard with a softly animated 3D companion and
a presentation-quality WebXR room.

The implementation is complete when a Quest 3 user can enter the room over a
trusted HTTPS connection, move and turn with the controllers, talk to the
agent, inspect furniture, and watch the agent move between independently
persisted VR furniture positions. No implementation decision should require a
new asset or a physics engine.

## 2. Product contract

### 2.1 Entry and session lifecycle

On a WebXR-capable browser, startup must not immediately open the existing
WebSocket. The frontend first calls:

```ts
await navigator.xr?.isSessionSupported("immersive-vr");
```

When the result is `true`, it shows an **Enter immersive VR** button and a
secondary **Continue on screen** action. Pressing Enter immersive VR is the
user gesture that requests
an `immersive-vr` session with a `local-floor` reference space. The frontend
then connects `/client-ws?renderer_mode=vr`. Selecting normal mode connects the
unchanged client without that query parameter.

The same Enter immersive VR gesture also starts a tiny local silent WAV on the
shared media element before either activation-gated request yields. Later TTS
segments reuse that element, which preserves Quest Browser's playback grant.
Audio unlock failure does not prevent room entry, but remains visible in the
headset ribbon until playback succeeds or the user exits and enters VR again.

If WebXR is absent or `immersive-vr` is unsupported, the application connects
in normal mode as it does today. Ending the XR session closes the VR WebSocket,
disposes the Three.js renderer and textures, and returns to the full desktop
workspace; it does not silently reuse that connection as a 2D client.

WebXR session creation must remain inside the Enter VR click handler. Immersive
WebXR requires a secure context, explicit user intent, and the
`xr-spatial-tracking` permission policy; see the
[WebXR security requirements](https://developer.mozilla.org/en-US/docs/Web/API/WebXR_Device_API/Permissions_and_security).

### 2.2 In-headset surfaces

VR mode keeps:

- microphone/VAD voice input and interruption;
- streamed TTS playback and the existing audio queue;
- the current subtitle, connection state, and agent-activity summary;
- the animated agent and every normal-mode furniture object;
- object pointing and the existing furniture data/decision surfaces.

The audio queue distinguishes autoplay rejection, an empty payload, decode
failure, and an ordinary playback failure. Every failure completes its queue
item and playback handshake so one bad segment cannot strand later speech.

Subtitle, connection, activity, microphone, and transient error messages use a
compact, high-resolution camera-relative ribbon below the center of view. It
is limited to three lines, truncates safely, and disappears while idle so it
cannot obscure the companion. Only the latest subtitle and current activity
are visible; VR mode has no full chat history.

VR mode excludes the normal sidebar and footer, text entry, direct fixed-
furniture grabbing, teleportation, hand tracking, passthrough/AR, and multiplayer.
Grabbable `prop` nodes and session-only panels/cards use the squeeze control.

### 2.3 Quest Touch Plus controls

Read controllers from `XRSession.inputSources`, select by `handedness`, and
ignore input sources without a gamepad. The WebXR `xr-standard` mapping assigns
the primary thumbstick to axes 2 and 3; see the
[WebXR Gamepads mapping](https://immersive-web.github.io/webxr-gamepads-module/).
Cache button state between frames because the browser updates the same Gamepad
object in place.

| Input | MVP behavior |
| --- | --- |
| Left thumbstick axes 2/3 | Head-relative continuous locomotion |
| Right thumbstick axis 2 | 30-degree snap turn |
| Left X, `buttons[4]` | Toggle existing microphone/VAD on the press edge |
| Right A, `buttons[4]` | Toggle the camera-relative VR test-scenario menu |
| Right primary trigger / `select` | Select an in-world panel control |
| Right `squeezestart` / `squeezeend` | Grab or release a nearby prop, or a ray-targeted card/panel within 3 m |

Quest 3 is the only supported controller profile. A left controller without
`buttons[4]`, or either controller without axes 2/3, must produce a visible
unsupported-controller diagnostic instead of guessing another mapping.

Locomotion rules:

- Apply a radial `0.2` thumbstick dead zone and rescale the remaining magnitude
  from `[0.2, 1]` to `[0, 1]`.
- Project the headset's forward vector onto the floor plane and normalize it.
  Derive right from that vector. Read orientation directly from the current
  animation callback's `XRFrame.getViewerPose()` and compose it with the
  locomotion rig yaw. Three.js does not merge the current viewer pose into its
  render camera until `renderer.render()`, which occurs after locomotion is
  calculated. Do not use the previous render camera or controller orientation.
- Move the camera rig by `(right * axisX - forward * axisY) * 1.5 m/s * dt`.
- Normalize Quest Touch Plus `xr-standard` input at the controller boundary:
  axis 2 is left/right and axis 3 is vertical with forward negative. Convert it
  to semantic `{x: axis2, y: -axis3}` before applying head-relative movement;
  do not leak device ordering into world helpers.
- Keep the rig at least 25 cm inside the room and outside the 25 cm clearance
  zone around furniture and the companion. If a diagonal move is blocked, try
  each axis independently so the user slides along an edge instead of sticking.

Snap-turn rules:

- When right axis 2 first reaches `>= 0.7`, rotate the rig by `-30` degrees.
- When it first reaches `<= -0.7`, rotate the rig by `+30` degrees.
- Disarm turning after a snap and rearm only after `abs(axis) <= 0.3`.
- Rotate around the headset's current floor position, not the room origin, so
  snapping never translates the user.

Left X toggles the same `startMic`/`stopMic` functions used by the ordinary UI.
It uses a rising-edge check, follows the persisted `micOn` preference on entry,
and displays **Microphone on** or **Microphone off** for 1.5 seconds. It must
not overwrite the preference merely because an XR session ends.

Right A uses an independent rising-edge latch. While the scenario menu is
open, locomotion and snap turning are suspended, its controls take raycast
priority, and the right trigger selects Details, Start, Back, or Cancel. The
first shipped entry is **An Email Changes the Agent's Plan**. It is visibly
marked simulated, confirms that the VR room and unfinished autonomous work
will reset, and reports live agenda phases without presenting a pass score.

The scenario protocol is versioned independently from the stage document:
`vr-scenario-get-state`, `vr-scenario-start`, and `vr-scenario-cancel` are
client messages; `vr-scenario-state` is the authoritative catalogue/run
snapshot. Request IDs are idempotent and snapshots carry a monotonic agenda
revision so reconnects and reordered messages cannot repeat a launch.

### 2.4 Pointing and furniture panels

Render a simple ray from the right controller's target-ray space. Do not fetch
remote controller models. On `select`, raycast in this order:

1. visible panel controls;
2. the panel background;
3. furniture meshes.

The target-ray spaces and headset camera must share the locomotion rig. WebXR
reports both in reference-space coordinates; parenting controllers at the scene
root while moving only the camera rig leaves the ray behind at its initial
world position and makes interaction disagree with locomotion.

Furniture panels are driven by proximity rather than selecting fixed furniture.
A panel opens when either the user or agent enters 150% of the furniture's
semantic reach: an explicit bounded `reach`, or 60 cm beyond the furniture's
largest half-span. It remains visible through a further 25 cm exit buffer and
closes once both occupants leave that buffered area. Multiple panels may be
visible when the user and agent are near different furniture. Furniture without
a configured `uiWindow` still opens a generic panel naming the object and
stating that no additional data is available.

A panel is positioned above or beside its furniture with a fixed room-relative
orientation; it does not rotate toward the headset. Selecting its enlarged
Close control hides that panel until both occupants leave the buffered area;
returning later opens it again. Opening the VR test-scenario menu temporarily
hides furniture panels. Fixed furniture is not trigger-selectable and does not
receive a selectable hover highlight.

Panel content is rendered to local canvas textures and raycast as Three.js
planes; DOM Overlay is not required. Reuse the existing read routes for todo,
timers, Gmail, books, reading, plans, skills, and the drawing board. The room
layout panel reads the active `VrStageDocument`. Approve/Decline controls reuse
the existing plan and skill decision routes and retain their pending-only
semantics. Google connection state may be shown, but authorization must say
**Exit VR to connect Google** rather than opening an OAuth flow in-headset.

## 3. Rendering architecture

### 3.1 Technology choice

Add `three` and `@types/webxr` to the web frontend. Use Three.js directly from
a React-owned component; do not add React Three Fiber. The component owns one
`WebGLRenderer`, enables `renderer.xr`, requests `local-floor`, and uses
`renderer.setAnimationLoop`. This follows the
[Three.js WebXR integration](https://threejs.org/manual/en/how-to-create-vr-content.html).

Add a renderer-mode context separate from the existing Electron `window`/`pet`
mode. Its states are `choosing`, `normal`, `vr-starting`, and `vr`. Keep the
normal `DynamicStageProvider` intact and introduce a separate
`VrStageProvider`; neither provider accepts the other document type.

The VR renderer uses:

- a hemisphere key, warm shadow-casting directional light, and restrained
  cool room/presence lights;
- physically based furniture, wall, floor, and companion materials;
- soft dynamic shadows, subtle object edges, floor depth cues, and base trim;
- the native XR framebuffer scale with moderate foveation for legible detail;
- `renderer.outputColorSpace = THREE.SRGBColorSpace` for PNG textures.

All document geometry is in centimetres. The renderer multiplies positions
and dimensions by `0.01` before passing them to Three.js. Document `(x, y)` maps
to Three.js `(x - room.width/2, elevation, room.depth/2 - y)`. This keeps the
backend's horizontal coordinates and distance semantics intact while using a
right-handed 3D scene.

### 3.2 Room and furniture

Build a solid floor and four solid-color walls from the room block. The origin
of document coordinates is the back-left floor corner: `x` increases to the
right and `y` increases toward the front. Nodes are axis-aligned; VR furniture
rotation is not represented in v1.

Each furniture node becomes a box with dimensions `width`, `height`, and
`depth`. `elevation` is already the centre height of the box and is passed to
Three.js directly; renderers must not add another half-height. Its existing PNG
is aspect-contained on slightly offset decals on all four vertical faces; the
top, bottom, and box itself use `color`. Preserve alpha on every decal. An
object without a texture, such as `drawing_board`, is a solid box. State
textures such as the unread mailbox replace the four decals together.

Texture load failure falls back to the solid material and records one visible
warning; it does not remove the object from the world. Sanitize asset sources
with the same relative/data-image/HTTP(S) allowlist as normal mode.

### 3.3 Spatial companion and animation

VR renders a code-native 3D companion from lightweight Three.js geometry. Its
feet remain anchored at the authoritative avatar floor position, and the body
turns only around the vertical axis to face the headset. A glass face, emissive
eyes, and chest presence light maintain legibility without requiring a remote
model download.

Idle breathing, head float, presence pulse, walking arm swing, and seated body
height are driven inside the XR animation loop. Authoritative movement still
uses the existing distance-derived duration, so rendering cannot change scene
state or arrival semantics.

## 4. VR stage document

### 4.1 Type and storage

The VR document is a separate schema, not stage v10:

```ts
type VrNodeType = "furniture" | "avatar" | "prop";

interface VrStageDocument {
  version: 3;
  mode: "vr";
  room: {
    width: number;
    depth: number;
    height: number;
    floor_color: string;
    wall_color: string;
  };
  world: {
    units_per_meter: number;
    reach_ceiling: number;
    move_speed: number;
  };
  nodes: VrStageNode[];
  metadata: {
    conf_uid?: string;
    created_at?: string;
    updated_at?: string;
    updated_by?: string;
    theme_revision?: string;
  };
}

interface VrStageNode {
  id: string;
  type: VrNodeType;
  label?: string;
  x: number;
  y: number;
  width: number;
  depth: number;
  height: number;
  elevation: number;
  color: string;
  texture?: StageTexture;
  stateTextures?: Partial<Record<"unread", StageTexture>>;
  spritesheet?: StageSpritesheet;
  uiWindow?: FurnitureWindowConfig;
  pose?: "standing" | "sitting";
  interaction?: { on_click?: boolean; grabbable?: boolean };
  reach?: number;
  yaw?: number;
  support_id?: "floor" | string;
}
```

`elevation` is the height of the node's center above the floor. Floor furniture
normally uses `height / 2`; wall furniture uses an explicit mounting height.
Elevation affects rendering and endpoint volume checks, but MVP reach and path
distance use floor-plane `x/y` only.

A successful VR `move_to` has a stronger postcondition than merely reaching a
clear endpoint: after the frontend result is saved and reloaded, the target
must be admitted by the same proximity projection used by tool authorization.
If it is not, the move reports the final coordinates, centre distance, and
effective reach instead of claiming arrival. Mounted height never changes this
floor-plane authorization rule.

Store the document at `stage_state/<sanitized-conf_uid>/vr_stage.json`, beside
the current `stage.json`. Use the existing path sanitization, atomic write, and
metadata conventions. A missing file creates the fixed default below. A
malformed or unsupported-version file logs the validation errors and loads the
default without overwriting the bad file. Stored v1/v2 rooms are intentionally
reset once to the fixed v3 room and persisted. There is no normal-to-VR
migration, live synchronization, or shared position field.

### 4.2 Validation

Backend and frontend validators enforce the same rules:

- `version === 3`, `mode === "vr"`, and no more than 80 nodes; stored v1/v2
  rooms are reset to the fixed v3 layout on load;
- finite positive room dimensions, `units_per_meter`, reach ceiling, and move
  speed, with the existing upper bounds where applicable;
- unique safe IDs and unique effective labels under whitespace normalization
  and case-folding;
- finite position and dimensions; positive width/depth/height; the complete
  AABB must lie within the room and between floor and ceiling;
- avatar ID is exactly `avatar`, appears once, and uses type `avatar`;
- furniture cannot use the avatar spritesheet field; avatar cannot use a
  furniture window;
- `reach` is positive and no greater than `world.reach_ceiling`;
- colors, textures, state textures, interactions, windows, poses, labels, and
  spritesheets use the current allowlists and length limits;
- props are explicitly grabbable, have horizontal `yaw`, rest on the floor or
  a furniture support with a 2 cm footprint margin, and cannot carry `reach`
  or `uiWindow` capability anchors;
- no furniture/avatar rotation, scripts, arbitrary HTML, extra window
  providers, or unknown material fields.

Furniture is allowed to overlap because the existing room editor does not
promise collision-free layouts. Movement docking handles overlaps by rejecting
blocked candidates. Validation nevertheless rejects an avatar whose saved
floor point starts inside a furniture footprint.

### 4.3 Fixed default

The default room is `1920 x 1080 x 280` cm, uses `100` units per metre, the
current `150` reach ceiling, and a `200` units/second agent speed.
The user rig chooses a collision-safe point 175 cm from the companion, prefers
the room interior, and faces the companion. It is session-local and is not part
of the document.

The VR default is authored independently even where a coordinate resembles the
canonical 2D room. It contains the current stage v9 furniture IDs and avatar:

| ID | x | y | width | depth | height | elevation | Presentation |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `window` | 173 | 8 | 185 | 16 | 155 | 165 | north wall, `window.png` |
| `todo_list` | 384 | 6 | 105 | 12 | 125 | 165 | north wall, `todo-list.png` |
| `calendar` | 557 | 6 | 110 | 12 | 130 | 165 | north wall, `calendar.png` |
| `design_board` | 738 | 6 | 120 | 12 | 110 | 160 | north wall, `design-board.png` |
| `mailbox` | 912 | 12 | 96 | 24 | 88 | 140 | north wall, mailbox state texture |
| `hourglass` | 1064 | 10 | 65 | 20 | 90 | 45 | north wall, `hourglass.png` |
| `drawing_board` | 1215 | 6 | 115 | 12 | 125 | 100 | north wall, solid color |
| `tool_rack` | 1398 | 17.5 | 120 | 35 | 100 | 50 | north wall, `tool-rack.png` |
| `archive_shelf` | 1587 | 27.5 | 125 | 55 | 170 | 85 | north wall, `archive-shelf.png` |
| `bookshelf` | 1775 | 22.5 | 120 | 45 | 190 | 95 | north wall, `bookshelf.png` |
| `computer` | 265 | 1025 | 230 | 110 | 140 | 70 | south wall, `computer.png` |
| `reading_chair` | 620 | 1027.5 | 120 | 105 | 100 | 50 | south wall, `reading-chair.png` |
| `bed` | 965 | 1005 | 210 | 150 | 65 | 32.5 | south wall, `bed.png` |
| `sofa` | 1345 | 1027.5 | 190 | 105 | 85 | 42.5 | south wall, `sofa.png` |
| `writing_desk` | 1695 | 1045 | 150 | 70 | 85 | 42.5 | south wall, `writing-desk.png` |
| `avatar` | 960 | 540 | 70 | 20 | 92 | 46 | companion, `avatar.png` |

Copy labels, interactions, state textures, furniture windows, spritesheet
animations, and protected-object semantics from the canonical v9 definitions
when authoring these constants, but do not generate them by reading a saved 2D
stage. Assign an explicit opaque fallback color to every row. This layout keeps
every pair of tool-gated furniture centers more than the 150 cm reach ceiling
apart, keeps the boxes non-overlapping, and starts the avatar outside every
object's derived reach. Pin all three properties in backend tests.

## 5. Mode-aware backend

### 5.1 Authoritative scene mode

Introduce a `SceneMode` value with `normal` and `vr`. Normal is the default for
all existing call sites. Record the selected mode on the frontend-client
registration and the per-client service context. Propagate it to the scene MCP
subprocess as `EMBODIED_AGENT_SCENE_MODE` and into `ToolManager`.

All of these operations must load the selected document rather than choosing
from `conf_uid` alone:

- schema-time and execution-time proximity checks;
- scene MCP reads and writes;
- object-click lookup and effective-label resolution;
- autonomous idle relocation and sitting pose patches;
- frontend-action result validation and persistence;
- post-movement arrival verification.

Provide mode-aware `load_scene`, `save_scene`, `reset_scene`, `validate_scene`,
and `load_world` entry points. Keep the current stage-manager functions as
normal-mode wrappers so existing imports and tests do not change merely to
support VR.

The common `World` remains horizontal in the MVP. Its VR loader maps node
`width/depth` to the footprint and ignores elevation for distance and reach.
Normal mode continues to map node `width/height` as it does today. Effective
labels, ambiguous-label fail-closed behavior, reach derivation, and arrival
tolerance are shared.

### 5.2 Scene tools and furniture editing

Keep `get_object_position`, `list_objects`, `get_scene_status`, `move_to`,
`describe_room`, `place_object`, `move_object`, and `remove_object`. Their
process reads `EMBODIED_AGENT_SCENE_MODE`; the model cannot select a different
mode in a tool argument.

In VR, `place_object` exposes these additional optional arguments:

```text
depth=90, height=90, elevation=height/2,
color="#334155", image_url=null
```

Here `x/y/width/depth` describe the floor footprint and `height/elevation`
describe the vertical box. Normal mode preserves the existing parameter
meaning and rejects VR-only arguments. `move_object` changes only `x/y` in
both modes. Resizing and rotation are not MVP operations.

Room mutation remains available only at the design board. `avatar`, `computer`,
and `design_board` preserve their current removal protections. Every successful
VR mutation returns and persists the complete VR document and cannot touch
`stage.json`.

### 5.3 Docking and endpoint validation

Normal mode retains its current center-target behavior. VR `move_to(target)`
never selects the target's center. Let the target footprint have half-width
`hw`, half-depth `hd`, the avatar radius be `max(avatar.width,
avatar.depth) / 2`, and the clearance be 10 cm. Generate candidates in this
fixed tie-break order:

```text
north: (target.x, target.y - hd - avatar_radius - 10)
east:  (target.x + hw + avatar_radius + 10, target.y)
south: (target.x, target.y + hd + avatar_radius + 10)
west:  (target.x - hw - avatar_radius - 10, target.y)
```

A candidate is valid only when the avatar's full circular floor footprint is
inside the room and its center is outside every non-avatar node's inclusive
floor AABB. Sort valid candidates by Euclidean distance from the current avatar
position, retaining the generation order for equal distances. If none remain,
return `Error: no clear stopping point is available near <target>` without
sending a frontend action.

`World.path_to` may still return a straight line, so the displayed path may
cross a box. Validate every requested waypoint before applying it and validate
the final saved avatar position again on the backend. A point on an AABB edge
counts as inside; reject it. Endpoint rejection returns the authoritative
unchanged VR state. Arrival is successful only when the saved position is
within the existing two-unit tolerance of the selected docking point.

## 6. WebSocket and frontend-action protocol

### 6.1 Connection ownership

Parse `renderer_mode` before registering a client. Missing means `normal`; any
value other than `normal` or `vr` closes with WebSocket policy code `1008`.
Because this server uses one selected character configuration, ownership is
checked against that character before initial messages are sent.

- If a normal client for the character exists, reject a new VR connection.
- If a VR client exists, reject every additional connection for the character.
- Multiple normal clients retain their existing behavior when no VR client is
  present.

Send a machine-readable error message before closing where the protocol
permits it, and display: **Close the other client before entering VR.** Never
disconnect an existing client to make room for a new one.

### 6.2 State messages

Normal mode continues to receive:

```json
{"type": "stage-state", "stage": {}}
```

VR mode receives:

```json
{"type": "vr-stage-state", "stage": {"version": 3, "mode": "vr"}}
```

The VR provider alone handles `vr-stage-state`. When backend validation rejects
a VR action result, send the last authoritative `vr-stage-state`, just as
normal mode currently sends `stage-state`.

Frontend action names and request/result envelopes remain unchanged. VR
`get_object_position`, `move_to`, `list_objects`, and `get_scene_status` use
floor-plane `{x, y}`. Mode-specific `stage_get_state`, `stage_set_state`,
`stage_patch_state`, `stage_reset_state`, and `stage_list_capabilities` operate
on `VrStageDocument` for the owning VR client. Capabilities must report
`mode: "vr"` and schema version 3.

The backend resolves a pending request to its registered owner, looks up that
owner's stored mode, validates the returned state with that mode's validator,
and persists to that mode's path. A `mode` field in the result is ignored; a VR
document returned by a normal owner, or the reverse, fails validation.

### 6.3 Physical affordances and observations

VR exposes composable actions rather than an email-delivery state machine:
listing physical entities, materializing a mailbox-gated email card, showing a
panel, picking up, putting down, offering, dismissing, reading the fresh user
pose, and moving near the user. Each action validates only its local physical
preconditions. The embodied presentation policy asks the model to choose the
least intrusive suitable channel; deterministic code does not choose that
channel or assume that an offer was accepted.

The right trigger retains ray selection. Right-hand `squeezestart` grabs a prop
within 20 cm or a transient targeted by the 3 m ray; `squeezeend` releases it. Persistent props are
authoritatively snapped to a furniture top within 12 cm or to the floor,
uprighted while preserving yaw, and rolled back to the last server state when
support or collision validation fails. Session-only `panel` and `email_card`
entities are capped at three and disappear when the VR session ends.

The client reports ground position, head height, yaw, and a monotonic sequence
at no more than 2 Hz, immediately after a 25 cm move or 15-degree turn, plus a
two-second heartbeat. Poses older than three seconds are not used for offers or
approach. A bounded event ledger folds repeated prop moves and injects at most
12 content-free sensor observations into the next serialized model request.
These observations are explicitly non-instructional and cannot grant tool
authority. Email bodies, panel text, and raw grab trajectories never enter the
ledger or long-term audit log.

## 7. HTTPS and LAN deployment

Add optional fields under `system_config`:

```yaml
host: "0.0.0.0"
port: 12393
trusted_hosts:
  - "192.168.1.10"
ssl_certfile: "/absolute/path/to/server-cert.pem"
ssl_keyfile: "/absolute/path/to/server-key.pem"
```

Both TLS paths must be absent/blank or both present. Expand user paths, require
readable regular files during configuration validation, and pass them to
Uvicorn as `ssl_certfile` and `ssl_keyfile`. Do not store certificates, private
keys, or generated CAs in the repository. HTTP remains the default when both
fields are blank, preserving local Electron behavior.

Add `Permissions-Policy: xr-spatial-tracking=(self)` to frontend responses.
Existing Host and Origin checks remain authoritative: operators must bind to
`0.0.0.0` and list the exact hostname or LAN IP used in the Quest URL under
`trusted_hosts`.

For browser builds, derive defaults from `window.location`:

```text
base URL = window.location.origin
WebSocket scheme = wss when location.protocol is https, otherwise ws
WebSocket host = window.location.host
```

Electron keeps its explicit `http://127.0.0.1:12393` and
`ws://127.0.0.1:12393/client-ws` defaults. Do not let an old locally persisted
HTTP endpoint downgrade a same-origin HTTPS Quest session; VR always uses the
current secure origin.

The operator guide must include these steps:

1. Generate a LAN certificate whose subject alternative names include the
   exact hostname/IP used by Quest.
2. Install and trust the issuing local CA on the Quest device.
3. Configure TLS paths, `host`, and `trusted_hosts`.
4. Open `https://<trusted-host>:12393/` in Quest Browser and accept microphone
   permission.
5. Confirm there is no certificate warning before testing `navigator.xr`.

A certificate exception page is not an acceptable MVP deployment because
WebXR and microphone APIs require a trustworthy secure context.

## 8. Implementation sequence

1. **Contracts and storage:** add the VR types, fixed defaults, dual validators,
   independent persistence, mode-aware world loader, and TLS configuration.
2. **Connection routing:** add renderer selection, deferred connection, mode
   registration, exclusive VR ownership, mode-aware MCP/tool context, and
   authoritative VR result persistence.
3. **Renderer:** add the Three.js lifecycle, lit room, cuboid furniture,
   textures, animated spatial companion, compact captions, and status surfaces.
4. **Input and interaction:** add Quest locomotion, snap turn, right-ray
   selection, X-button microphone toggle, provider panels, decision buttons,
   and voice-directed VR furniture mutations.
5. **Movement and hardening:** add docking, waypoint/endpoint validation,
   mode-aware idle/object behavior, performance measurement, deployment docs,
   and Quest hardware acceptance.

Each step must leave normal mode usable. Keep VR marked experimental until all
acceptance checks below pass on Quest 3.

## 9. Test plan

### 9.1 Backend automated tests

Add pytest coverage for:

- the exact default node set, dimensions, metadata, and one avatar;
- VR validation success plus malformed types, bounds, unsafe assets, excessive
  reach, duplicate labels, missing avatar, and an avatar inside furniture;
- independent normal/VR save, load, reset, and corrupt-file fallback;
- VR world footprints, reachability, duplicate-label fail-closed behavior, and
  mode-aware tool authorization;
- scene reads/writes targeting only the registered mode and preserving
  protected furniture;
- four docking candidates, deterministic ties, room-edge rejection, candidates
  blocked by other boxes, no-candidate failure, and persisted arrival checks;
- frontend-result spoofing, wrong-schema results, and authoritative VR state
  recovery;
- normal/VR client exclusion and close code 1008 while retaining normal-only
  multi-client behavior;
- TLS pair validation, unreadable paths, and Uvicorn option propagation;
- mode-aware object clicks and autonomous idle relocation.

Run the existing full Python suite to catch regressions in stage v9, proximity
gating, frontend routing, study ablation, and autonomy.

### 9.2 Frontend automated tests

Extract pure helpers and cover them with the frontend test runner:

- VR document validation and patch merging;
- centimetre-to-Three.js coordinate conversion;
- AABB containment and docking selection;
- radial dead-zone rescaling and head-relative movement vectors;
- room-boundary clamping;
- snap-turn threshold, direction, and rearming;
- left X rising-edge toggling without repeat while held;
- right-ray hit priority and object-click cooldown;
- normal versus VR endpoint derivation and secure `wss` URL construction.

Frontend lint, node/web TypeScript checks, and the production web build must
pass. The ordinary web and Electron renderers must still load a stage v9 room.

### 9.3 Quest 3 acceptance

On a physical Meta Quest 3 over trusted HTTPS:

1. Enter and exit immersive VR without a permission-policy, certificate,
   microphone, or WebSocket error.
2. Maintain at least 72 rendered frames per second for five minutes in the
   default room while the agent animates and audio plays.
3. Verify head-relative left-stick movement, room clamping, 30-degree
   right-stick turns, turn rearming, furniture collision, and edge sliding.
4. Verify left X toggles the microphone exactly once per press, preserves the
   preference, and shows the correct status.
5. Complete a voice conversation with streamed audio, subtitle updates, and
   interruption. Confirm the caption never covers the companion and disappears
   while idle.
6. Approach every furniture class as the user or agent, verify dynamic panels
   open and close by distance, and exercise one plan or skill decision with the
   right ray.
7. Ask the agent to move furniture at the design board; reconnect and restart
   the server, then confirm only the VR layout changed.
8. Confirm an agent path may visibly cross a box but no waypoint or final
   position can remain inside one.
9. Confirm a simultaneous desktop/Quest connection is refused with the stated
   message and that reconnect succeeds after the other client closes.
10. Grab and release a prop on the floor and on furniture; verify illegal
    overlaps roll back, furniture carries supported props, and reconnect keeps
    only the accepted final transform.
11. Exercise email-card, panel, offer/take/ignore, and user-walk-away scenarios.
    Judge whether the Agent's chosen channel fits privacy and attention rather
    than requiring a particular tool sequence.

## 10. Non-goals and future work

The first hardware acceptance target is Meta Quest 3, Touch Plus controllers,
and the current Quest Browser. It intentionally does not include:

- downloaded 3D models, skeletal animation, inverse kinematics, or spatial audio;
- a physics engine, navmesh, or obstacle-avoiding agent paths;
- hand tracking, teleportation, passthrough, AR, multiplayer, or remote avatars;
- direct fixed-furniture grab/reposition controls, furniture rotation, or resizing;
- in-headset OAuth, the complete desktop UI, full chat history, or text entry;
- migration from normal stage state or ongoing layout synchronization;
- support guarantees for other WebXR headsets or controller profiles.

Likely follow-up work is art-directed 3D assets, navmesh pathfinding, delayed
world-anchored caption easing, controller-profile abstraction, direct
fixed-furniture manipulation, and richer spatial UI. None of those may change the MVP's independent
persistence, authoritative mode routing, or dual proximity authorization.
