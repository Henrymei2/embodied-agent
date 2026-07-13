# Embodied Agent

Embodied Agent extends Open-LLM-VTuber into a persistent desktop companion with
voice interaction, a modifiable 2D room, spatially gated tools, layered memory,
scheduling, and consent-aware autonomous behavior.

## Current capabilities

- **Embodied room:** persistent stage v2 with an animated avatar, bed, computer,
  calendar, validated textures, and safe object placement/removal.
- **Spatial tools:** `get_object_position`, `list_objects`, `get_scene_status`,
  `move_to`, `place_object`, and `remove_object` through the scene MCP server.
- **Hard proximity gates:** computer and calendar tools are hidden when the
  avatar is far away and re-authorized immediately before execution.
- **Private calendar:** persistent local event list/add/update/remove tools tied
  to the calendar object; no cloud account or network service is required.
- **Persistent context:** contextual, daily, and long-term memory plus workspace
  knowledge retrieval and embodied-action recording.
- **Agent runtime:** priority-queued user, scheduled, and autonomous work with
  workspace-sandboxed file and shell tools.
- **Presence Contract:** autonomous actions spend a refillable attention budget,
  respect focus/snooze state, produce why-I-acted receipts, and learn bounded
  preferences from explicit feedback.
- **Multi-client-safe routing:** live frontend actions are bound to the exact
  client and character configuration that initiated them.

## Quick start

Requirements: Python 3.10-3.12, `uv`, and Node.js for frontend development.
FFmpeg is recommended for audio formats handled through pydub.

```powershell
uv sync
Copy-Item config_templates/conf.default.yaml conf.yaml
uv run run_server.py
```

The server uses `conf.yaml`; character alternatives live under `characters/`.
The default MCP set includes scene, calendar, scheduler, sandboxed workspace,
time, and search tools.

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
- [Frontend action protocol](frontend/frontend_api_doc.md)
- [Presence Contract](doc/PRESENCE_CONTRACT.md)
- [Local calendar tools](doc/CALENDAR_TOOLS.md)
- [Configuration templates](config_templates/README.md)

## Deliberate boundaries

- The bundled calendar is local-first. Google Calendar requires a separate
  OAuth adapter and user credentials; the project does not pretend that
  integration is configured.
- Browser UI plumbing is present, but a third-party browser MCP provider and
  its credentials/runtime must be configured separately.
- Presence signals contain idle/present/focus values only. Window titles,
  keystrokes, screenshots, and application contents are not collected by the
  Presence Contract.
- Self-evolution is limited to memory, preferences, mood/familiarity, and
  user-reviewed workspace artifacts. Unattended self-modification of executable
  code is intentionally outside the trusted runtime.
- 3D embodiment remains deferred; the production path is the validated 2D stage.
