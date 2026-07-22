# Furniture Tools

## The rule

Every skill the agent has is bound to a piece of furniture, and the furniture
has to answer one question: *why would the agent walk there?* A tool is only
offered to the model when the avatar is within 150 stage units of its object,
and the same check runs again immediately before the call executes, so a stale
schema or an injected tool call cannot skip the walk.

Three objects were added alongside the `computer`, `calendar`, and `bed`.

| Object | Server | Tools | State |
| --- | --- | --- | --- |
| `todo_list` | `todo` | `list_todos`, `add_todo`, `update_todo`, `complete_todo`, `remove_todo` | `<workspace>/todo/todos.json` |
| `hourglass` | `timer` | `set_timer`, `list_timers`, `cancel_timer` | `<workspace>/scheduler_tasks.json` |
| `window` | `weather` | `get_current_weather`, `get_weather_forecast` | none (live API) |

Gating is declared per server in `SERVER_PROXIMITY_REQUIREMENTS`
(`src/open_llm_vtuber/mcpp/tool_manager.py`), so a new tool added to one of
these servers inherits the object requirement automatically.

## Todo list

Items carry a `high`/`normal`/`low` priority and an optional due date, which
must be ISO 8601 with an explicit UTC offset (`2026-07-22T17:00:00-04:00`) and is
stored normalized to UTC. Listing returns open items first, then by priority,
then by soonest due date. Writes are atomic, IDs are stable, the list is capped
at 500 items, and query results are bounded.

## Timers

A timer is a one-shot scheduled task written into the scheduler's store, so the
background loop that already polls that file is what fires it. When a timer
comes due the agent is woken once, tells the user, and the task is deleted.

That loop ticks every 30 seconds, so a timer can fire up to one tick late —
these are reminders, not precise timekeeping. Durations run from 0.25 to 1440
minutes and at most 50 timers may be pending. `cancel_timer` only accepts
entries tagged as timers, so it cannot delete an ordinary scheduled task.

## Weather

Open-Meteo serves both geocoding and forecasts with no account or API key. The
agent may pass a place name or a `latitude,longitude` pair; coordinates skip the
geocoding request entirely. Units are `fahrenheit` (default) or `celsius`, which
also switches wind speed between mph and km/h.

Only the two fixed Open-Meteo hosts are contacted and the location is always
sent as an encoded query parameter, never as part of the host or path. Responses
are read with a timeout and a size cap.

## Hover windows

`todo_list` and `hourglass` declare a `uiWindow` provider, so pointing at the
object opens a panel showing the current list or a live countdown. Those panels
read `/api/furniture/todo` and `/api/furniture/timers`, which are read-only
projections — the agent remains the only writer, and only from the right spot in
the room. See `frontend/frontend_api_doc.md` for the stage document contract.

## Configuration

```yaml
mcp_enabled_servers:
  - bash
  - scene
  - calendar
  - scheduler
  - todo
  - timer
  - weather
  - time
  - ddg-search
```

Existing rooms pick the new furniture up through the v4 stage migration; no
manual reset is needed.
