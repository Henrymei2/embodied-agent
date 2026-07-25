# Project Status and Architecture

## 1. Problem statement

Most assistants wait for a prompt, answer, and disappear. This project tests
whether persistent memory, spatial embodiment, real-time voice, and carefully
bounded initiative can create a stronger sense of presence without sacrificing
user control.

Research question: can an AI companion be continuously useful and spatially
legible while making every autonomous interruption finite, explainable, and
reversible?

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
  retrieved into prompt context.
- Soft evolution tracks bounded mood, familiarity, interaction counts, and
  milestones without rewriting executable code.

### Embodiment

- Persistent 960x540 stage v6 laid out in wall, back, and front bands: window,
  todo list, calendar, hourglass, design board, mailbox, computer, archive
  shelf, bookshelf, writing desk, reading chair, bed, and sofa.
- Sprite walk/idle/seated animation, textures, validated external/relative
  assets, movement, object queries, and reachability.
- Safe room modification through `place_object`, `move_object`, and
  `remove_object`, all bound to the design board.
- Stage state synchronizes on connection and persists after live
  frontend actions.

### Spatial safety

- Every skill is bound to an object: computer, calendar, todo list, hourglass,
  window, archive shelf, mailbox, bookshelf, reading chair, writing desk, sofa,
  and design board.
- The default room keeps each gated object more than 150 units from every other
  one and starts the avatar clear of all of them, so one object never unlocks
  another's tools and the first use of any is preceded by a walk.
- Gating occurs at schema exposure and again at execution time.
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

### Voice and visual presence

- Real-time WebSocket audio, VAD interruption, modular ASR/TTS, Live2D, and the
  dynamic 2D room remain integrated with the upstream application surfaces.
- The frontend exposes the Presence Contract indicator and receipt controls in
  window mode.

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
| 2D room and navigation | Complete | stage v3, furniture windows, sprite transition, scene MCP, persistence |
| Memory and personality | Complete | contextual/daily/long-term memory, retrieval, evolution state |
| Scheduler and serialized LLM work | Complete | scheduler MCP and priority queue |
| Autonomous behavior MVP | Complete | scored planner, directions, cadence, Presence Contract |
| Spatial modification | Complete | validated placement/removal and persisted stage patches |
| Calendar object/tool | Complete | Google OAuth, primary-calendar CRUD, connection window, dual proximity enforcement |
| Just-in-time contextual help | Complete | coarse screen context, confidence/frequency gating, daily-memory task changes |
| Browser | Complete | Playwright MCP registered and gated on the computer; visible session and result plumbing |
| Books, plans, idling, room editing | Complete | bookshelf, reading chair, writing desk, sofa, and design board, each with its own tools and window |
| Self-evolution | Safe subset complete | memory, affinities, mood/familiarity; unattended executable self-rewrite is excluded |
| 3D embodiment | Deferred | 2D is the supported production path |

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
- 3D/Unity would be a separate renderer and asset pipeline, not a safe incremental
  change to the validated 2D stage.

## 6. Acceptance gates

- Python lint and formatting pass.
- Full Python test suite passes.
- Frontend lint, node/web TypeScript checks, and production web build pass.
- Multi-client bridge routing, stage persistence, unsafe assets, workspace
  traversal, dual proximity enforcement, calendar CRUD, and Presence Contract
  behavior have regression coverage.
