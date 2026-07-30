# Level 3 Autonomy Operations

Level 3 keeps the behavioral chain available across UI disconnects and server
process restarts without permitting two durable agenda writers.

## Files

All operational state is workspace-local:

- `agenda/agenda.json` — entries, event inbox, report outbox, quotas, dead
  letters, planner health, revision, and last committed fencing token;
- `agenda/lease.json` — current process owner, priority, expiry, and fencing
  token;
- `agenda/traces.jsonl` — current content-free span file;
- `agenda/traces.1.jsonl` — one bounded rotated span file.

The JSON state files use a file lock and atomic replacement. Do not edit them
while a server is running.

## Ownership and recovery

A live-client runtime uses priority 100 and a headless runtime uses priority 10.
A live runtime may fence a remote headless runtime immediately. Two equal
priority runtimes do not preempt one another; a standby process acquires the
lease only after expiry. Every agenda commit holds the lease fence stable until
its atomic state replacement completes.

The defaults are a 30-second lifetime, renewal every 10 seconds, and a
2-second standby retry. Tune `lease_ttl_seconds`, `lease_renew_seconds`, and
`lease_retry_seconds` together. Keep renewal comfortably below half the lease
lifetime.

An unreadable lease fails closed: the runtime stays in standby and exposes the
error through `GET /api/agenda`. Repair or restore the file only after stopping
every server process, because discarding its fencing token while a writer may
still exist is unsafe.

## Publishing external events

Trusted local integrations publish JSON to `POST /api/agenda/events`:

```json
{
  "idempotency_key": "message-42-v1",
  "source": "mail",
  "kind": "message_arrived",
  "summary": "A new message may need bounded review.",
  "evidence_ref": "external:mail:message-42-v1",
  "payload": {
    "importance": "normal"
  }
}
```

Idempotency is scoped to `source`; replaying the same source/key returns the
existing event without advancing the agenda revision. Summaries are limited to
500 characters and payloads to 10 KB. Pending plus claimed events cannot exceed
`event_backlog_limit`.

The executive recovers an abandoned or fenced claim, then either:

- atomically creates an evidence-grounded entry and acknowledges the event;
- defers it while a quota or planner circuit is unavailable; or
- moves it to the dead-letter store after `event_retry_limit` failures.

Inspect events with `GET /api/agenda/events?status=pending`. Requeue a reviewed
dead event with `POST /api/agenda/events/{event_id}/retry`.

## Quotas and dead letters

`planner_calls_per_window` limits all intention, step, completion, and
reconsideration calls. `capability_calls_per_window` applies independently to
each capability. Both reset after `quota_window_seconds`; exhausted work is
persisted as deferred rather than repeatedly retried.

Activity execution and policy failures stop after
`execution_failure_limit`. Inspect terminal operational failures at
`GET /api/agenda/dead-letters`. Requeueing an agenda activity is intentionally
an explicit user decision through the normal agenda controls.

## Telemetry and reports

Spans contain timing, status, IDs, counters, and capability names. They omit
prompts, tool results, agenda descriptions, and report text. Trace rotation is
bounded by `trace_max_bytes`.

Generate a content-free quality report through
`GET /api/agenda/report?hours=168` or:

```bash
uv run python scripts/autonomy_report.py --hours 168
```

The report covers completion, grounding, idle/repetition ratios, chain length,
duration, event/report state, dead letters, quota use, planner health, and the
current committed fencing token.
