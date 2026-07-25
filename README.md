# Embodied Agent

Embodied Agent extends Open-LLM-VTuber into a persistent desktop companion with
voice interaction, a modifiable 2D room, spatially gated tools, layered memory,
scheduling, and consent-aware autonomous behavior.

## Current capabilities

- **Embodied room:** persistent stage v6 with an animated avatar and thirteen
  pieces of furniture — window, todo list, calendar, hourglass, design board,
  mailbox, computer, archive shelf, bookshelf, writing desk, reading chair,
  bed, and sofa — with validated textures and safe placement/removal.
- **Spatial tools:** `describe_room`, `get_object_position`, `list_objects`,
  `get_scene_status`, `move_to`, `place_object`, `move_object`, and
  `remove_object` through the scene MCP server, with room editing bound to the
  design board.
- **Every skill is furniture-gated:** each tool server is hidden until the
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
  no network access, a read-only rootfs, and a workspace-only mount.
- **Furniture windows:** safe, read-only hover/focus panels backed by
  `/api/furniture/*` routes, so the agent remains the only writer of the
  underlying stores.
- **Persistent context:** contextual, daily, and long-term memory plus workspace
  knowledge retrieval and embodied-action recording.
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

## Quick start

Requirements: Python 3.10-3.12, `uv`, and Node.js for frontend development.
FFmpeg is recommended for audio formats handled through pydub.

```powershell
uv sync
Copy-Item config_templates/conf.default.yaml conf.yaml
uv run run_server.py
```

The server uses one character from `conf.yaml`; alternate-character switching
is not part of the runtime.
Every server in `mcp_servers.json` — bash, scene, screen, calendar, scheduler,
todo, timer, weather, library, plan, idle, drive, gmail, playwright, time, and
search — is available by default (`mcp_disabled_servers: []`); leaving this
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

Set-Location frontend/src/Open-LLM-VTuber-Web
npm run lint
npm run typecheck
npm run build:web
```

## Documentation

- [Project status and architecture](doc/PROJECT_STATUS.md)
- [Furniture tools](doc/FURNITURE_TOOLS.md)
- [Frontend action protocol](frontend/frontend_api_doc.md)
- [Presence Contract](doc/PRESENCE_CONTRACT.md)
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
- Self-evolution is limited to memory, preferences, mood/familiarity, and
  user-reviewed workspace artifacts. Unattended self-modification of executable
  code is intentionally outside the trusted runtime.
- 3D embodiment remains deferred; the production path is the validated 2D stage.
