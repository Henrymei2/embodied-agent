# Demo runbook

Everything needed to give the presentation, in the order you need it, with a
recovery path for each beat. [DEMO_MODE.md](DEMO_MODE.md) covers recording the
offline fixtures; this covers the day itself.

The governing rule: **every beat has a way to fail, and every failure has a
next sentence.** Rehearse the sentences, not just the beats.

---

## 1. The day before

```powershell
uv run python scripts/record_fixtures.py     # needs network, once
uv run python scripts/demo_preflight.py      # go / no-go
uv run python scripts/demo_crash_resume.py --all
```

Then rehearse **with the safety net off**, so you find out now whether the real
model is running:

```powershell
$env:EMBODIED_AGENT_REQUIRE_LIVE_LLM = "1"
uv run run_server.py
```

If the agent answers, the model is real. Remove that variable before the demo
so the net is back.

### VR, the day before — not on the day

WebXR needs a **trusted** HTTPS origin. A certificate warning is not enough:
Quest Browser will refuse an immersive session outright. Follow
[VR_MODE.md §7](VR_MODE.md), then confirm on the headset that
`https://<host>:12393/` opens with **no** warning. `demo_preflight.py` reports
`vr https` as degraded until a certificate is configured.

Budget an hour for this. It is the single most likely thing to fail, it fails
silently until you are standing in front of people, and it cannot be fixed in
the room.

---

## 2. Starting up

```powershell
$env:EMBODIED_AGENT_FIXTURES = "replay"   # room tools work with no network
uv run run_server.py
```

Open two windows before anyone arrives:

| Window | URL | For |
| --- | --- | --- |
| The room | `https://<host>:12393/` | the demo itself |
| Inspector | `https://<host>:12393/inspector.html` | beat 3 |

Leave the inspector on a second screen. Do not open it for the first time in
front of the audience.

---

## 3. The beats

### Beat 1 — The walk is the safety mechanism

Ask for something that needs the computer: *"Can you read my notes?"*

The avatar walks there before touching anything. Say why that matters:

> Nothing consequential happens without you seeing it start. The tools bound to
> the computer are unavailable from anywhere else in the room, and the walk is
> how you know an action is coming before it arrives.

**Be honest about the claim.** This is an *observability* mechanism, not a
security boundary — an agent that wanted the computer could simply walk to it.
What it guarantees is that it cannot happen off-screen. Say that; someone will
ask, and the answer is stronger than the overclaim.

*If the walk does not happen:* the proximity gate refused. Say so, click the
object directly, and continue. A refusal is the system working.

### Beat 2 — Crash and resume

```powershell
uv run python scripts/demo_crash_resume.py
```

Twenty seconds, narrated, no dependencies. It kills a process after the side
effect has reached the outside world, restarts from disk only, and shows the
effect count is still exactly one.

> The recovery came from state written before the crash. The idempotency key is
> what stopped the second attempt.

`--all` runs all five crash transitions if someone asks whether you picked a
convenient one.

*If it fails:* it will not — it needs no model, no network, nothing external.
That is why it is beat 2 and why it is the beat to fall back to if anything
else is misbehaving.

### Beat 3 — The provenance inspector

Switch to `inspector.html`, click the most recent decision.

> Observed, believed with this confidence and on whose authority, planned,
> authorized on these grounds, verified. That is the whole causal chain, and
> there is no message text in it — the ledger stores digests and opaque
> references, which is why this is safe to put on a projector.

Two things to point at:
- **Authority**, not just confidence. A model reporting 1.0 cannot displace
  something the user said.
- **A gap is shown as a gap.** If evidence was deleted, the chain says
  incomplete rather than quietly closing over the hole.

*If the list is empty:* nothing has been recorded yet this session. Run one
conversational turn first, then reload. It polls every 15 seconds.

### Beat 4 — VR (only if the headset check passed)

Enter immersive VR, walk to an object, use it.

> The same object grants the same tools in both renderers. That is checked, not
> asserted: 15 objects, identical capabilities, zero cross-unlocks.

*If the headset misbehaves:* take it off and say *"the screen renderer is the
same system"* — because it is, and that is a point in your favour, not an
excuse. Never spend more than 30 seconds debugging a headset in front of an
audience.

---

## 4. When something breaks

| Symptom | What it means | What to say and do |
| --- | --- | --- |
| Replies begin "I'm answering from a recorded line" | The model is unreachable; the fallback engaged | Say it out loud — the system is telling you the truth. Move to beat 2, which needs no model. |
| No audio, subtitles only | TTS failed both remote and local, or ffmpeg is missing | Keep going; the transcript carries the demo. |
| The agent refuses a tool | The proximity gate, or the authorization kernel | This is the system working. Show the refusal, then walk to the object. |
| A room tool hangs | Fixture replay is off and it is calling the network | Note it, skip the beat. Check `EMBODIED_AGENT_FIXTURES=replay` next time. |
| Immersive VR button missing | The origin is not trusted | Continue on screen. Do not debug certificates live. |
| Anything else | — | Beat 2 always works. Go there, then come back. |

---

## 5. Questions you will be asked

**"Is the proximity gate real security?"**
No, and we do not claim it is. An agent that wants the computer walks to it.
What it provides is that no consequential action happens without a visible,
pre-action signal. The security boundary is the authorization kernel: one
policy decision per side effect, with a terminal outcome for each.

**"Is the LLM doing this or is it scripted?"**
The model forms the intention and chooses how to respond. Demo scenarios make
the starting context reproducible, but their documented sequence is only an
example of what useful behavior might look like; it is not placed in the model's
prompt or used as a required action trace. A different grounded and helpful
response is a valid result. There is a recorded fallback for when the model is
unreachable, it announces itself in the reply, and
`EMBODIED_AGENT_REQUIRE_LIVE_LLM=1` disables it entirely — which is how we
rehearse.

**"How do you know the two renderers behave the same?"**
Measured, not assumed: for all 15 objects, standing at an object unlocks an
identical set of tools in both, with zero cases of one object granting
another's capabilities.

**"What does it not do yet?"**
Say it plainly — it is a stronger answer than a hedge. No benchmark results
against a real model yet; the 100-scenario suite and scorer exist and have not
been run. No human study. Headset performance is not characterized. Those are
the next steps, and the plan says so.
