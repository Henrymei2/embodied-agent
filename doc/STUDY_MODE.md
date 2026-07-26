# Study mode: measuring whether the pillars matter

## 1. The gap this fills

The research question is whether persistent memory and spatial embodiment make
a companion feel more present than a chat window. Until now nothing in the
system could speak to it. The agent worked, and a demo showed that it worked,
but there was no way to say what it did *for* anyone — and no way to run it
with a pillar removed to see whether that pillar was doing any work.

Study mode adds the two things that turn a demo into a result:

1. **Conditions** that switch off one pillar at a time.
2. **An append-only event log** of what happened in each session, which
   `scripts/study_report.py` reduces to a comparable table.

## 2. Running a session

Everything comes from the environment, so nothing has to be edited between
participants:

```powershell
# Control: the whole companion.
$env:EMBODIED_AGENT_PARTICIPANT = "P01"; uv run run_server.py

# Without accumulated memory.
$env:EMBODIED_AGENT_PARTICIPANT = "P01"
$env:EMBODIED_AGENT_CONDITION   = "memory=off"; uv run run_server.py

# Without the room.
$env:EMBODIED_AGENT_CONDITION = "embodiment=off"; uv run run_server.py

# Two at once.
$env:EMBODIED_AGENT_CONDITION = "memory=off,autonomy=off"; uv run run_server.py
```

| Variable | Meaning |
| --- | --- |
| `EMBODIED_AGENT_PARTICIPANT` | Participant id. Recorded, and names the log file. Anything awkward for a filename is reduced to `-`, so `P<1>` logs as `P-1` rather than failing to open. |
| `EMBODIED_AGENT_CONDITION` | `pillar=on\|off`, comma separated. Omitted pillars stay on. |
| `EMBODIED_AGENT_STUDY_LOG` | Force logging `1`/`0`, overriding the default. |

Logging turns itself on when a participant is named or a pillar is switched
off. Naming a participant is enough on its own, deliberately: the control
condition is what every comparison is measured against, and it is the run where
nothing else looks unusual enough to remind you. Ordinary use — no participant,
no ablation — writes nothing at all.

A typo in a condition is logged and ignored rather than failing the run. The
session log records the conditions that were actually applied, so a mistyped
launcher shows up in the data instead of silently mislabelling it.

## 3. What each ablation removes

This is the methodological crux, so it is worth being explicit. Each condition
must remove *one pillar* and leave the companion otherwise identical.

### `memory=off`

Removes what makes the companion persistent: `MEMORY.md` (accumulated
summaries), `USER.md` (learned facts about the user), and all retrieved
passages from past days and knowledge pages.

**Keeps** `AGENT.md` and `RULE.md`, so the character, its voice, and its rules
are the same in every condition. Ablating those too would test whether *having
a persona* creates presence, which is a different question.

Memory is still *written* during an ablated session. Only recall is removed, so
a participant's later sessions are not damaged by an earlier ablated one.

### `embodiment=off`

The frontend does not render the room: no avatar, no furniture, no windows to
hover, nothing to click. Same conversation surface, same voice, no place.

**Also removes proximity gating.** This matters. If tools stayed bound to
objects that are no longer on screen, the ablation would be removing the
agent's capabilities rather than its embodiment, and any difference measured
would be about a crippled assistant. With the room gone, every tool is
available everywhere.

### `autonomy=off`

The planner never dispatches. No proactive speech, no offers of help, no silent
background work.

The ablation is one-directional: it can switch initiative off for a session,
never on. A `conf.yaml` that disables autonomy stays disabled even under
`autonomy=on`.

## 4. What is recorded

One JSONL file per session at `<workspace>/study/`, one event per line,
appended and never rewritten:

```json
{"ts": "2026-07-26T02:20:27.722+00:00", "session": "e12e2a3b", "participant": "P01",
 "condition": "no-memory", "event": "turn_started", "source": "object_click",
 "seconds_since_agent_finished": 4.1}
```

| Event | Recorded |
| --- | --- |
| `session_start` / `session_end` | Conditions applied, duration |
| `client_connected` / `client_disconnected` | Window opened and closed |
| `turn_started` | How the turn began (`text`, `voice`, `object_click`, `autonomous`), and how long the user left the agent waiting |
| `turn_finished` | How long the turn took, and the reply's length |
| `object_click` | Which object |
| `memory_recall` | Whether recall was enabled, how many passages it found, the best score, and which shelf it came from |
| `tool_call` | Tool name, the object it required, and whether it was allowed |
| `autonomy_action` | Direction, delivery, score, attention cost, budget left |
| `autonomy_feedback` | The user's `helpful` / `dismissed` / `snoozed` verdict |

### Nothing that was said

No message text, no transcripts, no file contents, no window titles — counts,
categories, ids, scores, and timings only. Call sites pass categories, and the
writer replaces any string over 120 characters with its length, so a transcript
cannot leak in by accident. `tests/test_study.py` pins that guarantee.

That restraint is what makes these logs shareable with a supervisor or an
ethics board without a separate scrubbing step, and it matches the stance the
rest of the system already takes — see [the Presence Contract](PRESENCE_CONTRACT.md),
whose receipts also record only coarse activity bands.

Instrumentation never interferes: a log that cannot be opened or written is
reported once and skipped, and the session continues.

## 5. Reading the results

```powershell
uv run python scripts/study_report.py
uv run python scripts/study_report.py --csv results.csv
```

One row per session, then the same rows averaged by condition:

```
session   participant  condition  minutes  turns  obj clicks  click %  recall hit %  initiatives  helpful
96a7e439  P01          full       18.4     23     9           47.37    83.33         4            3
33fc039e  P01          no-memory  11.2     14     3           23.08    -             4            1
```

A `-` means nothing was measured, and is never printed as `0`. The two say
opposite things: a recall hit rate of `0` means memory was asked and came back
empty, while `-` above means memory was switched off and the question was never
put to it. In the CSV the same cell is empty, so it loads as `NaN` rather than
as a zero that would drag an average down.

| Measure | Speaks to |
| --- | --- |
| Minutes and turns per session | Engagement overall |
| Object clicks, and their share of user-started turns | Whether the room is used or merely looked at |
| Median reply wait | How promptly the participant answers — a proxy for being *with* someone |
| Recall hit rate and mean score | Whether memory actually surfaced anything |
| Walks needed (blocked tool calls) | How often the room made the agent travel |
| Initiatives, helpful, dismissed | Whether initiative was welcome |

## 6. What this cannot tell you

These are behavioral proxies, and they are the half of the picture a
questionnaire cannot recover afterwards. They are not a presence score.

- **Ask people too.** A validated self-report scale administered after each
  condition is what turns these counts into evidence about *presence*. The log
  says a participant clicked the bookshelf nine times; only they can say whether
  the companion felt like it was there.
- **Counterbalance.** Run conditions in different orders across participants.
  Novelty inflates object clicks in whichever condition comes first.
- **Expect small n.** With a handful of participants this is descriptive, not
  inferential. Report the per-session table, not a p-value.
- **Ablations are not silent.** A participant who sees the room in one session
  and not the next knows something changed. That is unavoidable here and worth
  stating in any write-up.
- **One machine, one sitting.** A session is a run of the server. Reconnecting
  continues the same session; restarting starts a new one.

## 7. Where the code is

| Concern | File |
| --- | --- |
| Conditions, session log, privacy guard | `src/open_llm_vtuber/study.py` |
| Memory ablation, recall events | `src/open_llm_vtuber/workspace/workspace_prompt_builder.py` |
| Embodiment ablation, tool events | `src/open_llm_vtuber/mcpp/tool_manager.py` |
| Autonomy ablation | `src/open_llm_vtuber/autonomy/settings.py` |
| Initiative events | `src/open_llm_vtuber/autonomy/driver.py` |
| Session, click, and feedback events | `src/open_llm_vtuber/websocket_handler.py` |
| Turn events | `src/open_llm_vtuber/conversations/` |
| Hiding the room | `frontend/.../context/study-condition-context.tsx` |
| Report and CSV export | `scripts/study_report.py` |
| Coverage | `tests/test_study.py` |
