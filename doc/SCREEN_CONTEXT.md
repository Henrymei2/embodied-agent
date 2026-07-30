# Screen Context (Just-In-Time Help)

## What it is

The agent decides *whether* to offer help in the autonomy planner. Screen
context is the perception that makes those decisions timely and specific: a
short, coarse summary of what the user is doing — the foreground application, the
window/document title, and idle time.

It is deliberately **coarse text only**. No screenshot or raw screen content is
ever captured, sent, stored, or interpreted. This matches the existing presence
channel, which also refuses raw screen data.

## Data flow

```
frontend (every 5 min + on focus change)
   │  { type: "screen-context", app, title, idle_seconds }
   ▼
websocket_handler._handle_screen_context
   │  validate (short text, no images) → update_screen_context()
   │  on a *task change* → MemoryManager.note_daily()
   ▼
autonomy/screen_context.py  (in-memory, per client, TTL 15 min, never persisted)
   ├─▶ autonomy loop: Observation.screen_context → screen-aware help prompt
   └─▶ get_screen_context tool  ← /internal/screen-context ← MCP subprocess
```

## Task-change dedup

Only *task changes* are recorded to daily memory. `update_screen_context`
compares the incoming `(app, normalized-title)` against the last recorded pair;
idle-only updates and cosmetic title churn (for example an editor's unsaved-dot
marker) do not re-record. `MemoryManager.note_daily` additionally drops a line
that would repeat the most recent note, so a flapping signal cannot fill the
file. A recorded line looks like:

```
- [14:05] Screen context: VS Code — memory_manager.py
```

## The tool

`get_screen_context` (server `screen`) is gated on the **computer**: it is only
offered to the model, and only executes, when the avatar is near the `computer`
object — the same proximity rule as the bash tools. It reads the latest pushed
context through the loopback, bridge-token-guarded `/internal/screen-context`
route (the same trust boundary the scene tools use), so it never reaches into
backend memory directly and returns only coarse labels.

## Privacy boundaries

- Only `app`, `title`, and `idle_seconds` are accepted. Every text field is
  length-capped and stripped of control characters; an over-long value is
  rejected rather than truncated, because that usually means raw screen text was
  sent to a channel meant for coarse labels.
- Screen context lives in memory with a TTL and is cleared on disconnect. The
  only durable trace is the deduplicated one-line daily-memory note.
- `focus_mode` still applies: the planner's attention gate can refuse to
  interrupt regardless of what the screen shows.

## Interruption controls

The when/how of help is governed by the agenda entry's reporting policy plus
the Presence Contract's freshness, focus mode, snooze, and attention-budget
checks. Screen context feeds those decisions; it does not bypass them.

## Live desktop capture (remaining step)

A plain browser cannot see other applications, so the frontend publisher only
sends context when the electron main process exposes a coarse foreground-window
reader as `window.api.getActiveWindow()` returning `{ app, title }`. The whole
backend pipeline — validation, dedup, daily-memory recording, planner
integration, the internal route, and the proximity-gated tool — is in place and
tested; wiring that reader (via an OS/native call in the electron main process)
is the one remaining step for live capture. Until then the pipeline can be
driven directly, which is how the tests exercise it end to end.
