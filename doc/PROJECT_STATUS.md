# Project Status and Architecture

## 1. Problem statement

Most assistants wait for a prompt, answer, and disappear. This project tests
whether persistent memory, spatial embodiment, real-time voice, and carefully
bounded initiative can create a stronger sense of presence without sacrificing
user control.

Research question: can an AI companion be continuously useful and spatially
legible while making every autonomous interruption finite, explainable, and
reversible?

The system can now be run with any one of those pillars switched off, and
records what the user did in each case, so the question can be answered with
evidence rather than a demonstration. See [STUDY_MODE.md](STUDY_MODE.md) —
including what that evidence cannot settle on its own.

## 2. Implemented system

### Agent module

- Modular LLM/ASR/TTS/VAD engines through existing factory interfaces.
- Priority queue serializes user, scheduled, and autonomous LLM work; direct
  user requests retain priority 0.
- Workspace-sandboxed shell and file tools strip inherited credentials and
  reject unverifiable/out-of-workspace paths.
- MCP tools are dynamically discovered and formatted for native tool-calling
  models.

### Memory and personality

- Contextual memory records the active topic and embodied tool actions.
- Daily memories live under `memory/YYYY-MM-DD.md`.
- Important summaries compact into `MEMORY.md`; workspace knowledge is
  retrieved into prompt context by keyword overlap blended with local
  embeddings, one passage at a time, degrading to keywords when no embedding
  provider is installed.
- Soft evolution tracks bounded mood, familiarity, interaction counts, and
  milestones without rewriting executable code.
- Skills drafted at the tool rack extend the agent's own instructions once the
  user approves them; drafts are never retrieved into the prompt.

### Embodiment

- Persistent 960x540 stage v8 laid out in wall, back, and front bands: window,
  todo list, calendar, hourglass, design board, mailbox, tool rack, computer,
  archive shelf, bookshelf, writing desk, reading chair, bed, and sofa.
- A renderer-agnostic world model holds position, reach, paths, and arrival, so
  tools and the spatial gate no longer read presentation data. Distances are
  world units at a declared scale, and each object derives its own reach from
  its footprint.
- Sprite walk/idle/seated animation, textures, validated external/relative
  assets, movement, object queries, and reachability.
- Walking is a waypoint path timed by distance and confirmed against the saved
  room, so an interrupted walk is reported rather than assumed to have
  finished.
- Safe room modification through `place_object`, `move_object`, and
  `remove_object`, all bound to the design board.
- Clicking an object starts a conversation turn about it, composed on the
  backend from the stored stage rather than from client text.
- Stage state synchronizes on connection and persists after live
  frontend actions.

### Spatial safety

- Every skill is bound to an object: computer, calendar, todo list, hourglass,
  window, archive shelf, mailbox, bookshelf, reading chair, writing desk, sofa,
  design board, and tool rack.
- The default room keeps each gated object more than 150 units — the reach
  ceiling, which no object may exceed — from every other one and starts the
  avatar clear of all of them, so one object never unlocks another's tools and
  the first use of any is preceded by a walk.
- Gating occurs at schema exposure and again at execution time.
- Object labels are unique, so a second object cannot answer for a gated one:
  a decoy `computer` placed beside the agent would otherwise hand it the real
  computer's tools without the walk. Refused on save by both validators, and
  withheld from the gate by the world model if a room reaches it anyway.
- Missing/invalid scene identity fails closed for object-gated tools.
- Objects whose removal would strand their tools cannot be removed.
- Frontend actions are routed by exact client and character; replies from a
  different client cannot complete or persist the request.

### Scheduling and autonomy

- One-time and recurring tasks support priorities and silent/reported results.
- Autonomous directions cover curiosity, care, housekeeping, growth, social
  presence, and project work.
- Planner scores usefulness, novelty, intrusiveness, risk, cadence, and learned
  direction affinity.
- Presence Contract adds a finite attention budget, focus mode, snooze, coarse
  presence signals, receipts, and explicit helpful/dismissed feedback.
- A server-owned supervisor survives frontend disconnects, elects one active
  client for each workspace, and wakes immediately on meaningful presence or
  screen-context changes while retaining a periodic safety heartbeat.
- Silent work bypasses transcript/TTS output, user work preempts background
  requests, and state/habit learning advances only after successful execution.

### Voice and visual presence

- Real-time WebSocket audio, VAD interruption, modular ASR/TTS, Live2D, and the
  dynamic 2D room remain integrated with the upstream application surfaces.
- The frontend exposes the Presence Contract indicator and receipt controls in
  window mode.

### Measurement

- Each pillar — embodiment, memory, autonomy — can be ablated for a session
  from the environment, and each ablation removes the pillar without removing
  the agent's capabilities or its character.
- An append-only session log records turn sources, reply latency, object
  clicks, recall hits, blocked tool calls, and initiative verdicts, in
  categories rather than content: no message text is ever written.
- `scripts/study_report.py` reduces the logs to one row per session and a
  per-condition comparison. See [STUDY_MODE.md](STUDY_MODE.md).

## 3. Component flow

```text
Frontend (voice + room + presence)
        | WebSocket
        v
WebSocket Handler ----> per-client Service Context
        |                         |
        |                         +--> Agent + layered memory
        |                         +--> priority LLM queue
        |                         +--> MCP tools
        v                                  |
Persistent stage <---- validated result ---+
        |
        +--> schema-time proximity filter
        +--> execution-time proximity authorization
```

## 4. Deliverable status

| Original deliverable | Status | Evidence |
| --- | --- | --- |
| 2D room and navigation | Complete | stage v8, world model, furniture windows, sprite transition, scene MCP, persistence, click-to-interact |
| Memory and personality | Complete | contextual/daily/long-term memory, hybrid keyword/embedding retrieval, evolution state |
| Scheduler and serialized LLM work | Complete | scheduler MCP and priority queue |
| Autonomous behavior MVP | Complete | scored planner, directions, cadence, Presence Contract |
| Spatial modification | Complete | validated placement/removal and persisted stage patches |
| Calendar object/tool | Complete | Google OAuth, primary-calendar CRUD, connection window, dual proximity enforcement |
| Just-in-time contextual help | Complete | coarse screen context, confidence/frequency gating, daily-memory task changes |
| Browser | Complete | Playwright MCP registered and gated on the computer; visible session and result plumbing |
| Books, plans, idling, room editing | Complete | bookshelf, reading chair, writing desk, sofa, and design board, each with its own tools and window |
| Self-evolution | Complete | agent-authored skills that change its own prompt, gated on user approval and enforced by a read-only skills mount; unattended executable self-rewrite is excluded |
| Evidence for the research question | Instrumented | per-pillar ablations, an append-only content-free session log, and a per-condition report; self-report from participants is still required, see [STUDY_MODE.md](STUDY_MODE.md) |
| 3D embodiment | Deferred, unblocked | the renderer coupling that made it a rewrite is gone; see [WORLD_MODEL.md](WORLD_MODEL.md) |

## 5. External integration boundaries

The remaining partial items require authority or infrastructure outside this
repository rather than unfinished local code:

- Google Calendar, Drive, and Gmail require an operator-supplied OAuth client and
  explicit user consent; the application implements token storage, refresh, and
  reauthorization.
- The browser server runs `npx @playwright/mcp`, so it needs Node on the PATH and
  any account credentials the sites themselves require. A server whose runtime is
  missing is skipped at load rather than failing the session.
- Book text comes from Project Gutenberg, so only public-domain titles can
  actually be read; other books can be shelved but not opened.
- Raw screen/application awareness would materially expand privacy scope. The
  current protocol deliberately accepts only coarse presence signals.
- 3D/Unity remains a separate renderer and asset pipeline. What it is no longer
  is a rewrite of everything above the renderer: spatial reasoning now lives in
  a world model that a 3D scene can supply, walking is a waypoint contract
  rather than a CSS tween, and furniture windows are anchored to a projected
  room position rather than to a DOM box. What is left is renderer work —
  assets, a loader for `World`, an elevation axis, pathfinding, and a camera
  projection — none of which the research question needs, which is why it stays
  deferred. [WORLD_MODEL.md](WORLD_MODEL.md) has the checklist.

## 6. Acceptance gates

- Python lint and formatting pass.
- Full Python test suite passes.
- Frontend lint, node/web TypeScript checks, and production web build pass.
- Multi-client bridge routing, stage persistence, unsafe assets, workspace
  traversal, dual proximity enforcement, calendar CRUD, and Presence Contract
  behavior have regression coverage.
