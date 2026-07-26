# Embodied Agent

Embodied Agent extends Open-LLM-VTuber into a persistent desktop companion with
voice interaction, a modifiable 2D room, spatially gated tools, layered memory,
scheduling, and consent-aware autonomous behavior.

## Current capabilities

- **Embodied room:** persistent stage v8 with an animated avatar and fourteen
  pieces of furniture — window, todo list, calendar, hourglass, design board,
  mailbox, tool rack, computer, archive shelf, bookshelf, writing desk, reading
  chair, bed, and sofa — with validated textures and safe placement/removal.
  Every gated object keeps its own radius and the avatar starts clear of all of
  them, so using any object's tools always begins with a walk.
- **A room, not a picture of one:** position, reach, paths, and arrival live in
  a renderer-agnostic world model rather than in pixels. Each object derives
  its own reach from its size — an arm's length from the edge, so the bed is
  usable from further away than the hourglass — capped by a ceiling no object
  may exceed. See [the world model](doc/WORLD_MODEL.md).
- **Spatial tools:** `describe_room`, `get_object_position`, `list_objects`,
  `get_scene_status`, `move_to`, `place_object`, `move_object`, and
  `remove_object` through the scene MCP server, with room editing bound to the
  design board. Walking follows a waypoint path timed by distance, and arrival
  is confirmed against the saved room rather than assumed.
- **Every tool is furniture-gated:** each tool server is hidden until the
  avatar is within range of its object and re-authorized immediately before
  execution, so a stale schema or injected tool call can never skip the walk.
  See [Furniture tools](doc/FURNITURE_TOOLS.md) for the full object -> tool
  map.
- **Todo list, timers, and weather:** `todo_list` (priority + due dates),
  `hourglass` (one-shot timers riding the scheduler), and the `window`
  (Open-Meteo forecasts), each with its own hover window.
- **Library and reading:** browse and shelve books at the `bookshelf`
  (Open Library), then read locally cached, public-domain full text at the
  `reading chair` (Project Gutenberg via Gutendex), with resumable progress
  and agent-written reading notes.
- **Plans the user approves:** the agent drafts proposals at the `writing
  desk`; only the user can approve or decline them from the hover window —
  there is no tool that lets the agent authorize its own plan.
- **Idling:** the agent can sit at the `sofa` to represent "nothing to do
  right now" without blocking messages, timers, or scheduled tasks.
- **Skills the agent writes for itself:** at the `tool rack` it drafts a
  procedure — what it is for, when it applies, the steps — and the user approves
  or declines it from the hover window. Approved skills are retrieved into the
  system prompt, so approval genuinely changes later behavior; drafts never are.
  The gate is enforced, not just instructed: file tools refuse to write under
  `skills/` and the shell sandbox mounts it read-only. See
  [Skills](doc/SKILLS.md).
- **Google Calendar, Drive, and Gmail:** OAuth-backed tools tied to the
  calendar, archive shelf, and mailbox objects respectively, each with
  connection status in its furniture window.
- **Browser automation:** a Playwright MCP server is registered and gated to
  the computer for visible browser sessions (requires Node/`npx`).
- **Just-in-time screen help:** coarse, privacy-bounded screen context (no
  keystrokes, screenshots, or page contents) feeds confidence- and
  frequency-gated proactive help. See
  [Screen context](doc/SCREEN_CONTEXT.md).
- **Sandboxed execution:** shell/file tools run inside a Docker sandbox with
  no network access, a read-only rootfs, and a workspace-only mount, within
  which the approved-skills directory is itself read-only.
- **Furniture windows:** hover/focus panels backed by `/api/furniture/*` routes.
  They are read-only projections of stores the agent alone writes, with exactly
  two exceptions that run the other way: approving a plan or a skill, which only
  the user can do.
- **Persistent context:** contextual, daily, and long-term memory plus workspace
  knowledge retrieval and embodied-action recording.
- **Recall that survives paraphrase:** retrieval blends keyword overlap with a
  local embedding model, so "did I ever mention my sister?" finds the note
  about a sibling. Passages are indexed one memory at a time, weak matches are
  refused, and with no model installed it degrades to the previous keyword
  behavior. See [Memory recall](doc/MEMORY_RECALL.md).
- **A room you can point at:** clicking any object tells the agent you pointed
  at it, and it answers about that object — the room reads as shared rather
  than as scenery. The click carries only an object id; the prompt is composed
  from the stored stage.
- **Agent runtime:** priority-queued user, scheduled, and autonomous work with
  workspace-sandboxed file and shell tools.
- **Autonomous behavior:** a scored planner (usefulness, novelty,
  intrusiveness, risk, cadence, learned direction affinity) chooses among
  curiosity, care, housekeeping, growth, social, and work directions.
- **Presence Contract:** autonomous actions spend a refillable attention budget,
  respect focus/snooze state, produce why-I-acted receipts, and learn bounded
  preferences from explicit feedback.
- **Multi-client-safe routing:** live frontend actions are bound to the exact
  client and the single configured character identity that initiated them.
- **Survives a bad network:** a local TTS engine speaks when the remote one
  cannot be reached, a failed speech-recognition model disables voice input
  instead of the server, and the API-backed furniture can replay recorded
  responses. See [Demo mode](doc/DEMO_MODE.md).
- **Measurable claims:** each pillar — embodiment, memory, autonomy — can be
  switched off for a session, and an append-only log records what happened
  (turn counts, object clicks, reply latency, recall hits, initiative verdicts)
  in categories rather than content, so no transcript is ever written.
  `scripts/study_report.py` turns the logs into a per-condition table. See
  [Study mode](doc/STUDY_MODE.md).

## Quick start

Requirements: Python 3.10-3.12, `uv`, and Node.js for frontend development.
FFmpeg is recommended: without it the remote TTS engine's MP3 cannot be
decoded, and the companion falls back to the local system voice.

```powershell
uv sync
Copy-Item config_templates/conf.default.yaml conf.yaml
uv run run_server.py
```

The server uses one character from `conf.yaml`; alternate-character switching
is not part of the runtime.
Every server in `mcp_servers.json` — bash, scene, screen, calendar, scheduler,
todo, timer, weather, library, plan, idle, skills, drive, gmail, playwright,
time, and search — is available by default (`mcp_disabled_servers: []`); leaving this
list empty does not widen what the agent can reach, since object-gated tools
stay hidden until the avatar walks to the matching furniture.

Frontend development:

```powershell
Set-Location frontend/src/Open-LLM-VTuber-Web
npm install
npm run dev:web
```

## Quality gates

```powershell
ruff check .
ruff format --check .
pytest -q

# before a demo: can it speak, and can it speak without a network?
uv run python scripts/check_voice.py
uv run python scripts/check_voice.py --offline

Set-Location frontend/src/Open-LLM-VTuber-Web
npm run lint
npm run typecheck
npm run build:web
```

## Documentation

- [Project status and architecture](doc/PROJECT_STATUS.md)
- [Study mode: measuring whether the pillars matter](doc/STUDY_MODE.md)
- [The world model, and what 3D would still need](doc/WORLD_MODEL.md)
- [Furniture tools](doc/FURNITURE_TOOLS.md)
- [Frontend action protocol](frontend/frontend_api_doc.md)
- [Presence Contract](doc/PRESENCE_CONTRACT.md)
- [Skills and the approval gate](doc/SKILLS.md)
- [Memory recall](doc/MEMORY_RECALL.md)
- [Demo mode: voice fallback and offline fixtures](doc/DEMO_MODE.md)
- [Screen context](doc/SCREEN_CONTEXT.md)
- [Google Calendar tools and OAuth setup](doc/CALENDAR_TOOLS.md)
- [Configuration templates](config_templates/README.md)

## Deliberate boundaries

- Google Calendar, Drive, and Gmail remain unavailable until the operator
  explicitly supplies an external OAuth client JSON and the user completes
  consent from the relevant furniture window.
- The Playwright browser server needs Node/`npx` on the PATH; a server whose
  runtime is missing is skipped at load rather than failing the session.
- Only public-domain books (served through Project Gutenberg) can actually be
  read at the reading chair; other titles can be shelved but not opened.
- Presence signals contain idle/present/focus values only. Window titles,
  keystrokes, screenshots, and application contents are not collected by the
  Presence Contract or the screen-context pipeline.
- Self-evolution goes through the user: the agent can propose skills that change
  its own instructions, but only an approval at the tool rack activates one.
  Unattended self-modification of executable code, tool definitions, or the
  avatar's own spritesheet remains outside the trusted runtime.
- 3D embodiment remains deferred; the production path is the validated 2D
  stage. The coupling that would have made a renderer swap a rewrite is gone —
  what is left is renderer work, listed in [the world model](doc/WORLD_MODEL.md).
