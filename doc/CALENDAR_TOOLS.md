# Google Calendar Tools

## Google Cloud setup

1. Enable the Google Calendar API and Gmail API in a Google Cloud project.
2. Configure the OAuth consent screen and add test users while the app remains
   in testing. The requested Calendar and Gmail scopes can require Google
   verification before broader distribution.
3. Create a **Web application** OAuth client and register this exact callback:
   `http://localhost:12393/api/integrations/google/oauth/callback`.
4. Download the client JSON to a private location outside this repository.
5. Rotate any client secret that has appeared in source, chat, logs, or issue
   text, then put the fixed file path directly in `conf.yaml`:

   ```yaml
   system_config:
     google_integration:
       enabled: true
       client_secrets_file: '/private/google-client.json'
       redirect_uri: 'http://localhost:12393/api/integrations/google/oauth/callback'
   ```

If the server origin or port changes, update both `conf.yaml` and the OAuth
client's registered redirect URI. The values must match exactly.

## Authorization and storage

Hover, focus, or tap the calendar furniture. The panel reports whether the
server is configured and connected. When authorization is required, its button
opens Google consent in the system browser. The flow requests offline access
and these application-wide scopes:

- Calendar list read-only
- Calendar event free/busy
- Calendar event read/write
- Gmail compose
- Gmail read-only

Gmail data is not accessed by this version. Tokens are stored app-wide in the
platform user-data directory, outside the repository and agent workspace, with
restrictive file permissions where the operating system supports them. Never
commit the downloaded client JSON or token file.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_calendar_events` | List expanded primary-calendar events over a range |
| `add_calendar_event` | Create a timed primary-calendar event |
| `update_calendar_event` | Update selected fields by Google event ID |
| `remove_calendar_event` | Delete an event by Google event ID |

Input timestamps require ISO 8601 offsets. Listed all-day events use date-valued
`start`/`end` fields and `all_day: true`. Calendar tools return an authorization
error until Google is connected; the legacy workspace `events.json` is neither
read nor modified.

Calendar tools remain exposed only when the avatar is within reach of the
calendar — 125 world units, derived from its size — and proximity is checked
again immediately before execution. See [the world model](WORLD_MODEL.md).
