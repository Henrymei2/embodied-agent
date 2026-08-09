# Demo mode

Two things in this project depend on someone else's network: the voice comes
from a remote speech service, and the bookshelf, reading chair and window call
public APIs. Both have an offline path, and both are worth exercising *before*
the room is full of people.

## Before you travel

```powershell
uv run python scripts/check_voice.py            # can it speak and listen?
uv run python scripts/check_voice.py --offline  # can it still speak with no network?
uv run python scripts/record_fixtures.py        # save the demo's API responses
```

## On the day

```powershell
$env:EMBODIED_AGENT_FIXTURES = "replay"
uv run run_server.py
```

## VR headset presentation

WebXR on a standalone headset requires a trusted HTTPS origin. An IP-address
certificate warning is not sufficient: install the issuing local CA on the
headset and confirm the page opens without a browser warning before the demo.
Use the TLS, host, and `trusted_hosts` configuration described in
[VR_MODE.md](VR_MODE.md#7-https-and-lan-deployment).

Run this pass before guests arrive:

1. Put the computer and headset on the same stable 5 GHz or wired-access-point
   network and prevent both devices from sleeping.
2. Start the server, then open `https://<trusted-host>:12393/` in Quest Browser.
3. Allow microphone access. The polished headset-ready screen should offer
   **Enter immersive VR** and **Continue on screen**.
4. Enter VR and confirm the companion starts at a comfortable conversation
   distance, centered in view, with no caption panel while idle.
5. Speak one short prompt, interrupt one response, toggle the microphone with
   left X, walk with the left stick, snap-turn with the right stick, approach
   one furniture object to open its panel, and use the right trigger on a panel
   control.
6. Confirm locomotion slides along furniture rather than entering it, captions
   stay below the companion, and **Exit VR** returns to the desktop workspace.
7. Re-enter VR once. This catches session cleanup, microphone, texture, and
   WebSocket ownership problems before the live presentation.

Keep a laptop tab open in normal screen mode as the fallback presentation. On
a browser without immersive WebXR, the **Spatial** control opens the same
headset-safe 16:9 composition in fullscreen, so the conversation and room demo
can continue even if the headset is unavailable.

## Voice

`tts_config.tts_fallback_model` (default `pyttsx3_tts`) names a local engine
that speaks when the configured one cannot be reached. It uses the operating
system's own voices, so it needs no account, no download, and no network. The
first failure is logged, the rest are counted, and the moment the primary engine
recovers it takes over again.

If both engines fail the turn is still delivered — as subtitles, without audio.
Speech recognition is treated the same way: if the ASR model cannot load, the
server starts anyway with voice *input* disabled and says so, rather than
refusing to run at all.

`--offline` on the voice check refuses every non-loopback connection for the
duration of the run, so the remote engine fails exactly as it would on a hotel
network and the fallback has to carry the sentence. It reports three things
separately: synthesis, packaging for the browser, and transcription. Packaging
matters more than it sounds — audio in a wrongly named container decodes to
nothing without FFmpeg installed, which is silent in the worst way.

### Speech recognition

`faster_whisper` with the `base` model is the configured default: about 145 MB,
downloaded once, and from then on entirely local — voice input does not depend
on the venue's network either. Swap `model_path` for `small` or `medium` if the
room is noisy and the machine can afford it.

### If ASR will not load

A partly downloaded model leaves a directory that looks finished but holds a
truncated `model.onnx`, and the engine reports it as `No graph was found in the
protobuf`. The loader now names the two paths to delete; removing them and
restarting downloads a fresh copy.

### If FFmpeg is missing

`edge_tts` returns MP3, and without FFmpeg nothing downstream can decode it —
the file is written, the payload is empty, and the user gets subtitles in
silence. The fallback treats undecodable audio as a failed synthesis and hands
the sentence to the local engine, which writes WAV that pydub reads on its own.
The check reports this as `TTS spoke through the local fallback`. Installing
FFmpeg restores the nicer remote voice; nothing breaks without it.

## Fixtures

`EMBODIED_AGENT_FIXTURES` selects how the network-backed tools answer:

| Value | Behavior |
| --- | --- |
| `off` (default) | always call the real service |
| `record` | call it, and save the response under `fixtures/` |
| `replay` | never touch the network; serve the saved response |

Covered today: Open-Meteo geocoding and forecasts (the `window`), Open Library
search and Gutendex lookups (the `bookshelf`), and Project Gutenberg book text
(the `reading chair`). A recorded book is the whole text, so the reading chair
can turn pages offline all the way through.

Recording is a deliberate online step rather than hand-written JSON, so what the
demo shows is a real response from the real service.

In replay mode a request nobody recorded returns
`Error: no recorded response for <server>/<key>` — a normal tool failure the
agent can talk about, not a crash. Widen the recording with repeated flags:

```powershell
uv run python scripts/record_fixtures.py --place "Madison" --query "dracula"
```

`EMBODIED_AGENT_FIXTURES_DIR` moves the store somewhere other than `fixtures/`.

### What fixtures do not cover

- **Google Calendar, Drive and Gmail.** OAuth refresh needs the network, so
  these cannot be made to work offline; disable them for a demo with
  `mcp_disabled_servers: [drive, gmail, calendar]` rather than letting the agent
  walk to furniture that will fail.
- **The browser.** Playwright drives a real browser against real sites.
- **The LLM.** The default `ollama_llm` already runs on localhost, so the model
  itself is unaffected by the venue's network. A hosted provider is not — check
  before relying on one.
