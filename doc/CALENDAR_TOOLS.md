# Local Calendar Tools

## Feature description

The calendar object now owns a private, persistent calendar MCP server. Events
are stored locally in `~/embodied-agent/calendar/events.json`; no account,
network request, or cloud synchronization is required.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_calendar_events` | List events overlapping a range; defaults to the next seven days |
| `add_calendar_event` | Create an event with title, start, end, and optional notes |
| `update_calendar_event` | Update selected fields by event ID |
| `remove_calendar_event` | Remove an event by event ID |

All timestamps must be ISO 8601 values with an explicit UTC offset, for example
`2026-07-14T14:00:00-04:00`. Events are normalized to UTC, event IDs are stable,
writes are atomic, query results are bounded, and the store is capped at 5,000
events.

## Embodiment rule

Calendar tools are exposed only when the avatar is within 150 stage units of
the calendar. The same check runs again immediately before every call, so a
stale schema or injected tool call cannot bypass the spatial rule.

## Configuration

The default configuration enables the `calendar` MCP server:

```yaml
mcp_enabled_servers:
  - bash
  - scene
  - calendar
  - scheduler
  - time
  - ddg-search
```

## Cloud calendar boundary

Google Calendar and other providers require OAuth consent, token storage, API
credentials, and provider-specific revocation handling. Those are intentionally
not impersonated by the local tool. A future cloud adapter should preserve the
same tool contract and add an explicit connected-account indicator and consent
flow.
