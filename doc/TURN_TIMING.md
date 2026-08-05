# `turn-timing` — client-reported turn stages

The speech-to-first-audio budget (median ≤800 ms, p95 ≤1.4 s) is stated across
seven stages. Three of them used to have no producer at all, so the product
figure reported `unavailable` for every real turn no matter how fast the system
was — a budget that cannot be evaluated is a wish.

Two of those three happen entirely in the browser, before the server sees a byte
of the turn:

| Stage | What it measures |
| --- | --- |
| `capture` | microphone open → utterance captured |
| `endpoint` | utterance end → VAD end-of-speech decision |

The third, `retrieve`, is now produced server-side in
`conversations/single_conversation.py`.

## Message

Send **before** `mic-audio-end`, on the same websocket:

```json
{
  "type": "turn-timing",
  "capture_ms": 1840.2,
  "endpoint_ms": 218.7
}
```

Both fields are optional; send whichever you measured. The server holds the most
recent report per client and consumes it when the next turn starts.

## Rules the server applies

These are enforced in `latency.parse_client_timings`, and they exist because the
client is not a trusted measurement source.

- **Only `capture` and `endpoint` are accepted.** A message naming `first_audio`
  or `asr` is ignored; a client cannot overwrite a figure the server measured.
- **Implausible values are dropped, never clamped.** `capture` above 120 s or
  `endpoint` above 10 s is treated as a browser clock problem — a suspended
  laptop, a backgrounded tab — rather than a slow turn. Clamping would file a
  fabricated number as a real measurement; dropping leaves the stage absent, and
  absent is something the report already knows how to say.
- **Negative, `NaN`, `Infinity`, boolean, and non-numeric values are refused.**
  The only leverage a client has is to make its own turn look *slower*; there is
  no path by which a client report subtracts time.
- **One report is consumed by one turn.** Reports are not reused, so a single
  fast capture cannot be counted as a session-wide property.
- **Nothing here can fail the connection.** A malformed message costs a sample,
  never a reply (D-025).
- **Pending reports are dropped on disconnect**, so the per-client store cannot
  grow across a long-lived server.

## Measuring the stages in the client

```js
// microphone opens
const captureStart = performance.now();

// VAD reports speech has ended
const speechEnd = performance.now();

// endpointing decision is final and the utterance is about to be sent
const endpointDecided = performance.now();

socket.send(JSON.stringify({
  type: "turn-timing",
  capture_ms: speechEnd - captureStart,
  endpoint_ms: endpointDecided - speechEnd,
}));
socket.send(JSON.stringify({ type: "mic-audio-end" }));
```

Use `performance.now()`, not `Date.now()`: a wall clock that crosses an NTP
correction mid-turn produces negative durations, which the server will refuse.

## Verifying it works

```powershell
uv run python run_server.py
# hold a few voice conversations
curl http://localhost:12393/api/operator/health
```

`latency.speech_to_first_audio.median` reports `"unavailable"` until every stage
in the budget has been observed at least once for a completed turn. Once it
returns a number, save the response to
`research/baselines/latency-report.json` and `scripts/reproduce.py` will
evaluate it against the frozen budget.

## Status

The server side is implemented and tested (`tests/test_client_timings.py`). The
client-side emitter is a change to the `frontend/` submodule and has **not**
been made; until it is, `capture` and `endpoint` stay absent and the end-to-end
figure stays `unavailable`. That is the remaining Part 7 engineering item.
