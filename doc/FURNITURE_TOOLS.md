# Furniture Tools

## The rule

Every skill the agent has is bound to a piece of furniture, and the furniture
has to answer one question: *why would the agent walk there?* A tool is only
offered to the model when the avatar is within 150 stage units of its object,
and the same check runs again immediately before the call executes, so a stale
schema or an injected tool call cannot skip the walk.

Every skill lives at an object, alongside the `computer`, `calendar`, and `bed`.

| Object | Server | Tools | State |
| --- | --- | --- | --- |
| `todo_list` | `todo` | `list_todos`, `add_todo`, `update_todo`, `complete_todo`, `remove_todo` | `<workspace>/todo/todos.json` |
| `hourglass` | `timer` | `set_timer`, `list_timers`, `cancel_timer` | `<workspace>/scheduler_tasks.json` |
| `window` | `weather` | `get_current_weather`, `get_weather_forecast` | none (live API) |
| `archive shelf` | `drive` | Google Drive tools | Google account |
| `mailbox` | `gmail` | Gmail tools | Google account |
| `bookshelf` | `library` | `list_books`, `shelve_book`, `list_shelf`, `remove_from_shelf` | `<workspace>/library/library.json` |
| `reading chair` | `library` | `read_book`, `save_reading_note`, `reading_progress`, `finish_book` | same file, plus `library/texts/` |
| `writing desk` | `plan` | `write_plan`, `list_plans`, `get_plan`, `revise_plan`, `withdraw_plan` | `<workspace>/plans/plans.json` |
| `sofa` | `idle` | `sit_down`, `stand_up` | none (avatar pose) |
| `design board` | `scene` | `describe_room`, `place_object`, `move_object`, `remove_object` | the stage document |

Gating is declared per server in `SERVER_PROXIMITY_REQUIREMENTS`
(`src/open_llm_vtuber/mcpp/tool_manager.py`), so a new tool added to one of
these servers inherits the object requirement automatically. Two servers serve
more than one object — `library` and `scene` — so their tools are named
individually in `PROXIMITY_REQUIREMENTS`, which takes precedence.

The default room keeps every gated object more than 150 units from every other
one, and starts the avatar clear of all of them; both are enforced by
`tests/test_stage_state_manager.py`. Standing at one object therefore never
unlocks another object's tools, and the first use of any object-bound tool is
always preceded by a walk.

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

## Books

Browsing at the bookshelf searches Open Library, and each result is annotated
with whether a free full text exists — the readable copies come from Project
Gutenberg through Gutendex. Neither service needs an account. Only those hosts
are contacted, and a book's text is downloaded once, under a 2 MB cap, into
`<workspace>/library/texts/<id>.txt`, so continuing a book is a local read.

`read_book` returns the next passage (about 4,000 characters, cut at a paragraph
break) and remembers the offset, so reading resumes exactly where it stopped.
The agent writes its own summary with `save_reading_note`; scheduling the next
sitting is left to the scheduler tool, which is how "a chapter every interval"
works without a second timer implementation. Reading also seats the avatar.

## Plans

A plan is a proposal. `write_plan` stores it as `pending`, and the only way it
becomes `approved` or `declined` is the user pressing a button in the writing
desk's hover window, which posts to `/api/furniture/plans/{id}/decision`. There
is deliberately no MCP tool that decides a plan, so the agent cannot authorize
its own work. Revising a declined plan returns it to `pending` rather than
inheriting the old verdict, and an approved plan cannot be edited at all.

## Sitting

`sit_down` patches one field — the avatar's `pose` — and the frontend renders
the seated row of the spritesheet. Sitting costs nothing and blocks nothing:
messages, scheduled tasks, and timers all still arrive. Walking clears the pose
in the same stage update that moves the avatar, so a seated sprite can never be
animated mid-stride.

## Room layout

`describe_room`, `place_object`, `move_object`, and `remove_object` are bound to
the design board, so rearranging the room is itself something the agent walks
over to do. The avatar, the computer, and the design board cannot be removed,
because the tools reachable at them would become permanently unreachable.

## Hover windows

`todo_list`, `hourglass`, `bookshelf`, `reading chair`, `writing desk`, the
`calendar`, and the `mailbox` declare a `uiWindow` provider, so pointing at the
object opens a panel showing what is behind it. Those panels read
`/api/furniture/*`, which are read-only projections apart from the plan
decision — the agent remains the only writer of the stores themselves, and only
from the right spot in the room. The design board's panel needs no route at all:
it renders the stage document the frontend already holds. See
`frontend/frontend_api_doc.md` for the stage document contract.

## Configuration

Every server in `mcp_servers.json` is available unless it is named in
`mcp_disabled_servers`:

```yaml
mcp_disabled_servers: []
```

Leaving this empty does not widen what the agent can reach, because an
object-gated server stays hidden until the agent walks to its furniture.
`drive` and `gmail` additionally need Google authorization, and `playwright`
needs `npx` on the PATH; a server whose runtime is missing is skipped at load.

Stage v6 re-lays the room into wall, back, and front bands to keep the radius
rule satisfiable with this much furniture, so its theme revision is bumped and
stored rooms are rebuilt from the defaults on load.
