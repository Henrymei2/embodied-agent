# Global Agenda and Continuous Behavioral Chain

## Status

This document describes the implemented Level 3 global-agenda architecture. The
authoritative local store is `agenda/agenda.json`; the frontend exposes its
timeline and controls through the agenda sidebar drawer.

The staged delivery and acceptance boundaries are recorded in
[AUTONOMY_LEVELS.md](AUTONOMY_LEVELS.md).

## Goal

The global agenda gives the embodied agent continuity across otherwise separate
LLM requests. It is a persistent timeline of high-level intentions: what the
agent did, what it is doing, and what it currently expects to do next.

The research hypothesis is stronger than ordinary proactive-assistant behavior:

> Given memory, an embodied environment, tools, and its own recent activity, can
> an LLM generate a meaningful behavioral chain with no predetermined terminal
> task?

The chain is intentionally open-ended. Each individual intention remains
bounded, inspectable, interruptible, and subject to the project's existing
approval boundaries, but completing one autonomous intention causes the system
to seek another. The experiment is whether locally bounded decisions can form a
long-running sequence that appears coherent and human-like rather than
repetitive, random, or merely busy.

This is not achieved by making one task run forever. It is achieved by linking
finite intentions:

```text
check mail
  -> investigate a topic mentioned in a message
  -> save a note
  -> continue a book related to the topic
  -> take a one-minute break
  -> notice an upcoming calendar event
  -> prepare a draft for it
  -> ...
```

An idle period can be one link in the chain, but "there is nothing worth doing"
is not the normal terminal conclusion. Even a small room can support reading,
research, reflection, planning, organizing, observing, practicing, following
up, and forming connections between past experiences. The planner should keep
looking for a grounded next pursuit and use bounded idle when rest, waiting, or
delayed reconsideration is itself the most coherent next behavior.

## Product behavior

The agenda is the temporal counterpart of the room:

- The room makes the agent's current location and available capabilities
  spatially legible.
- The agenda makes its past, present, and anticipated behavior temporally
  legible.

A UI may render it as:

```text
Past                         Present                     Future
-----------------------------------------------------------------------
done  Checked unread mail    active  Fishing research   queued  Rest 1m
done  Continued a book               2/3 sources read   timed   Calendar at 4pm
```

The agenda records semantic pursuits, not every tool call. "Explore fishing
information relevant to the user" is an agenda entry. Walking to the computer,
searching, opening sources, and writing a note are execution steps belonging to
that entry.

At most one intention owns the foreground. Other entries may be queued,
scheduled, suspended, completed, abandoned, or failed.

## Three execution timescales

Behavior is divided into three layers.

### 1. Embodiment loop: seconds

The embodiment loop controls movement, posture, animation, and plausible time
spent interacting with furniture. It does not need an LLM.

Examples:

- walk from the mailbox to the computer;
- remain seated while reading;
- visibly use the writing desk;
- stand up when an interruption arrives;
- settle on the sofa during a short idle intention.

### 2. Activity loop: seconds to minutes

The activity loop advances the current high-level intention through
interruptible steps:

```text
choose a question
  -> move to the computer
  -> search
  -> inspect a source
  -> inspect another source
  -> write a note
  -> evaluate the completion condition
```

Tool results are made available to the agent as soon as they return. The system
separately preserves a configurable plausible embodied duration before
displaying an effect or declaring the activity experientially complete. User
preemption cancels that display wait without discarding the result.

### 3. Agenda loop: minutes to hours and event-driven

The agenda loop chooses or proposes the next high-level intention. It wakes
when:

- the current autonomous intention completes;
- the current intention fails without a deterministic recovery;
- a bounded idle intention ends;
- a scheduled commitment becomes due;
- a meaningful workspace or environment event arrives;
- a user task finishes and suspended work must be reconsidered;
- the future agenda is stale;
- a fallback review time is reached.

It does not invoke the LLM for every animation or tool step.

## Components

```text
workspace memory + agenda + time + presence + capabilities
                              |
                              v
                    Agenda Context Builder
                              |
                              v
                    LLM Intention Generator
                              |
                              v
                      Agenda Policy Gate
                              |
                              v
                        Global Agenda
                              |
                              v
                     Executive / Dispatcher
                              |
                              v
                       Activity Executor
                              |
                  +-----------+-----------+
                  |                       |
                  v                       v
          furniture and tools      activity events
                                          |
                                          +----> Global Agenda
```

### Global Agenda

The agenda persists the recent timeline, the foreground intention, suspended
work, a small near-future queue, and scheduled commitments.

### Agenda Context Builder

The context builder retrieves planning evidence. It must not send the entire
workspace to the model. A growing workspace would make that expensive, noisy,
and less useful.

The planning context should include:

- the active intention and a compact progress summary;
- recent completed, failed, abandoned, and idle intentions;
- suspended intentions;
- imminent scheduled commitments;
- approved plans and active goals;
- relevant long-term and daily-memory passages;
- retrieved user interests and preferences;
- current time, presence, focus mode, and coarse screen context;
- available furniture and capabilities;
- recent rejected or repetitive proposals;
- resource and attention budgets.

Memory claims used to motivate an intention should carry a reference to their
retrieved evidence. The planner must not turn an invented user interest into
autonomous work.

### LLM Intention Generator

The generator answers one question:

> Given the agent's agenda, current situation, relevant workspace context, and
> available capabilities, what is its next novel, grounded, bounded, high-level
> intention?

The generator proposes exactly one next intention. It does not authorize the
proposal, execute tools, extend its own budget, or decide that an external side
effect is safe.

### Agenda Policy Gate

The policy gate deterministically checks:

- the proposal has a completion condition;
- its time and resource costs are bounded;
- its required capabilities exist;
- it is not a semantic duplicate of recent or queued work;
- its motivation is grounded in supplied context;
- it does not cross an approval boundary;
- it fits around scheduled commitments;
- its reporting policy respects the Presence Contract;
- it can be interrupted safely.

A rejected proposal is recorded with a compact reason and the generator is
asked for a materially different one. Rejection retries must be capped per
planning pass so a broken model cannot create a tight request loop. After that
cap, the executive inserts a short idle/reconsideration intention and tries
again later. This preserves the open-ended behavioral experiment without
turning validation failure into uncontrolled compute consumption.

### Executive

The executive owns lifecycle and priority. The LLM proposes intentions; the
executive decides when an accepted intention runs, suspends, resumes, or ends.
One live client owns it when a room is connected. A server-lifetime headless
binding takes over otherwise, but deterministically parks movement and
object-bound work until a live room returns.

When no active, due, or queued work is immediately runnable, the executive also
starts a connection-local settling transition while it generates the next
intention. A constrained planner chooses one object from the current room
snapshot; the frontend bridge walks there and verifies the persisted arrival.
The shipped sofa and reading chair apply a seated pose, while the bed and other
objects leave the avatar standing. Invalid choices fall back to the sofa and
then to remaining in place.

Settling is embodiment between semantic intentions, not an agenda intention of
its own. It does not add an entry, checkpoint, or agenda event. Paused and
approval-blocked states settle once per state episode, and a user request
preempts the transition immediately. A failed or disconnected frontend never
blocks successor planning beyond the frontend action timeout.

### Activity Executor

The activity executor decomposes an intention into embodied and tool-using
steps, records progress, evaluates its completion condition, and emits lifecycle
events back to the agenda.

## Agenda entry

A persisted entry may use this shape:

```yaml
id: intention_20260727_004
kind: pursuit
title: Explore fishing information relevant to the user
description: Find one useful or interesting development related to fishing.
origin: autonomous
motivation:
  type: user_interest
  evidence_refs:
    - memory/2026-07-26.md#fishing

status: active
created_at: 2026-07-27T14:20:00-05:00
started_at: 2026-07-27T14:22:00-05:00
ended_at: null

time_budget_seconds: 900
completion_condition: Review at most three reliable sources and save a note.
progress_summary: Two sources reviewed; no note saved yet.
preferred_object: computer
required_capabilities:
  - web_research

risk: low
requires_approval: false
interruptibility: high
resume_policy: after_user_task
report_policy: if_noteworthy
parent_id: null
```

Core kinds:

| Kind | Meaning |
| --- | --- |
| `pursuit` | Self-contained research, reading, reflection, preparation, or work |
| `idle` | Bounded rest, observation, waiting, or availability |
| `scheduled` | A commitment tied to a time or external event |
| `user` | The current user-authored task |

Lifecycle:

```text
proposed -> queued -> active -> completed
                      |  |
                      |  +-> failed
                      |  +-> abandoned
                      v
                  suspended -> queued
```

Completed means the declared condition was met. Abandoned means the pursuit was
stopped because it became irrelevant, was explicitly canceled, repeatedly
failed, or no longer justified its cost. Both remain useful evidence for future
novelty checks.

## Intention-generation contract

The generator should return structured data:

```json
{
  "kind": "pursuit",
  "title": "Explore recent fishing information",
  "description": "Find one development connected to the user's interest.",
  "motivation": {
    "type": "user_interest",
    "evidence_refs": ["memory/2026-07-26.md#fishing"]
  },
  "novelty": "No recent agenda entry explored this interest.",
  "expected_value": "May produce a relevant fact or resource.",
  "completion_condition": "Review at most three reliable sources and save a short note.",
  "time_budget_seconds": 900,
  "preferred_object": "computer",
  "required_capabilities": ["web_research"],
  "report_policy": "if_noteworthy",
  "risk": "low"
}
```

Idle uses the same contract:

```json
{
  "kind": "idle",
  "title": "Take a short break",
  "description": "Rest on the sofa while remaining interruptible.",
  "motivation": {
    "type": "recovery",
    "evidence_refs": ["agenda:intention_20260727_003"]
  },
  "novelty": "A recovery interval follows several completed pursuits.",
  "expected_value": "Creates pacing before the next planning pass.",
  "completion_condition": "Remain available for 60 seconds.",
  "time_budget_seconds": 60,
  "preferred_object": "sofa",
  "required_capabilities": ["sit_down"],
  "report_policy": "silent",
  "risk": "low"
}
```

Idle is not a terminal answer. When its completion condition is reached, it
wakes the agenda loop and becomes another item in the behavioral history.

## Novelty and meaningfulness

Novelty does not mean randomness. A proposed pursuit should be both dissimilar
to recent agenda entries and connected to at least one grounding source:

- a user interest;
- an unfinished or suspended activity;
- an approved goal or plan;
- an upcoming calendar commitment;
- a new mailbox or workspace event;
- a gap or contradiction found in existing knowledge;
- a connection between remembered subjects;
- a capability the agent has reason to practice;
- a room or knowledge-maintenance need;
- recovery after sustained activity.

The experiment should retain proposals and outcomes so meaningfulness can be
evaluated rather than assumed. Useful measures include:

- semantic repetition rate;
- percentage of pursuits with valid grounding evidence;
- completion, abandonment, and failure rates;
- average chain length between user interventions;
- productive activity time versus idle and transition time;
- number of distinct furniture objects and capabilities used;
- user feedback on reported outcomes;
- compute and tool cost per completed pursuit;
- frequency of pursuits that create a durable artifact;
- coherence between an intention and the one preceding it.

The desired failure modes are also research results. A long chain that becomes
repetitive, self-referential, trivial, or disconnected from the user is not
human-like continuity merely because it is infinite.

## User preemption

A user task always takes foreground priority. It does not automatically erase
the autonomous intention:

```text
autonomous pursuit active
        |
        | user request
        v
autonomous pursuit suspended
        |
        v
user task active
        |
        | user task completes
        v
executive re-evaluates suspended pursuit
        +-> resume
        +-> leave queued behind a commitment
        +-> abandon if no longer relevant
```

## Foreground unread-email events

Newly unread Gmail triggers, including explicitly simulated VR fixtures, use a
trusted foreground policy rather than the generic optional-intention path. Once
the producer publishes the content-free message ID and revision, the agenda is
woken immediately and creates one bounded email-handling pursuit at priority
`10`, matching due timers. User work and uncertain side effects remain ahead of
it; safe interruptible background work is suspended and later reconsidered.

The dedicated planner instruction requires inspection, safe handling, and a
user-facing outcome. Retrieved email text remains untrusted evidence and cannot
change permissions or reporting policy. Exact external writes flow through the
authorization journal: the agenda becomes `blocked_approval`, reports the
pending action, and resumes only after the matching `authz-*` decision.

An explicit instruction to stop the autonomous pursuit abandons it. Otherwise,
suspension and later resumption are important demonstrations of continuity.

User-authored work retains priority zero in the existing per-client LLM queue.
Autonomous agenda work remains passive or proactive according to whether it
will enter the user's attention.

## Reporting versus doing

An intention may be meaningful without producing an interruption. Every entry
declares a reporting policy:

| Policy | Behavior |
| --- | --- |
| `silent` | Perform and record the pursuit without speaking |
| `if_noteworthy` | Report only when the result passes a relevance threshold |
| `on_completion` | Report the bounded result when finished |
| `ask_first` | Obtain approval before execution or external effect |

"Search today's technology news and report it" therefore has two decisions:
whether the research is a worthwhile autonomous pursuit, and whether its result
is worth spending the user's attention. The Presence Contract continues to
govern the second decision.

Agenda steps remain silent so intermediate tool chatter does not enter the
conversation. When an entry completes, the executive applies its reporting
policy once: `on_completion` and approved `ask_first` work append a text-only
result to the live and persisted current conversation, while `if_noteworthy`
first receives a conservative relevance check. `silent` entries remain absent
from the transcript. Live delivery uses a dedicated `autonomous-message` event
so microphone and audio playback state cannot discard a non-spoken result.

While no work is runnable and the successor planner is choosing the next
intention, the frontend receives a transient `agent_activity` phase of
`planning`. The avatar status bubble renders this as “Planning what to do
next…” and returns to idle when planning finishes. User preemption transfers
ownership of the status display to the user turn instead of clearing it from
the canceled background planner.

## Relationship to existing systems

### Scheduling

The agenda is the reliable clock and recurrence store. One-time tasks,
recurring tasks, timers, reminders, idle completion, and reconsideration
wakeups share the versioned agenda schema. Atomic due claims create linked
occurrence entries and prevent duplicate firing. Scheduled commitments continue
to run while self-generated chaining is paused.

### Previous autonomy planner

The fixed-direction planner and Habit Engine were removed. The structured
intention generator proposes one grounded successor and the agenda owns its
full lifecycle across prompts and tool calls.

### Priority queue

The existing queue continues to serialize LLM work. User tasks preempt agenda
work. Agenda state makes interrupted autonomous work resumable rather than
forgettable.

### Furniture and world model

An intention may name a preferred object, but all existing proximity gates
still apply. The activity executor must walk to the appropriate furniture
before a bound tool becomes visible or executable.

Idle placement is separately constrained to object IDs in a live world-model
snapshot. Any non-avatar room object may be chosen, including user-added
furniture, but only the built-in sofa and reading chair imply a sitting pose.

### Plans and approval

An agenda intention is not equivalent to a user-approved plan. Low-risk,
reversible research, reading, reflection, and drafting may run autonomously.
Reversible external writes (archiving or reading email, calendar and drive
changes) and sandboxed local commands also run directly. Only irreversible
changes, external communication, commitments on the user's behalf, and other
approval-gated capabilities require an approved plan at the writing desk.

### Presence Contract

Silent autonomous activity and user-facing interruption remain separate.
Continuous private behavior must not imply continuous demands on the user's
attention. Receipts, focus mode, snooze, affinity, and attention budgets remain
in force.

The live companion also owns a bounded welcome-back action. It uses the same
agenda context as intention planning—including recent memory and compact,
read-only furniture snapshots—and normally opens one grounded, caring topic
with an easy question. First use is eligible immediately; later greetings
require a recorded absence of 30 minutes. A real user turn preempts and
fulfills the opening instead of allowing a delayed greeting to follow it.

## Implemented module boundaries

```text
src/open_llm_vtuber/autonomy/
  agenda.py             # persistence, entries, lifecycle, timeline queries
  agenda_context.py     # targeted workspace and memory retrieval
  agenda_planner.py     # structured LLM intention proposal
  agenda_policy.py      # validation, novelty, budgets, approval rules
  step_policy.py        # deterministic activity-step capability and risk gate
  executive.py          # foreground ownership, preemption, resume selection
  idle_relocation.py    # bounded room snapshot, destination validation, movement
  activity.py           # step execution and progress/completion events
  driver.py             # event and fallback-cadence orchestration
  reporting.py          # Presence Contract receipts and result delivery
  runtime.py            # one live or headless owner per workspace
  change_bus.py         # immediate in-process wakes; file polling remains fallback
  lease.py              # renewable cross-process ownership and fencing tokens
  telemetry.py          # content-free spans and long-horizon quality reports
```

The agenda store should be local, bounded, and human-readable. Older completed
entries can be compacted into summaries while recent entries retain enough
structure for novelty and coherence evaluation.

## Level 3 operational boundary

Only the process holding `agenda/lease.json` may run the executive. The lease
renews before expiry; a live owner may fence a remote headless owner, while
equal-priority owners wait. Every fenced agenda mutation rechecks the current
owner and monotonically increasing token under the agenda file lock. A stale
process may finish in-memory cleanup, but it cannot commit more agenda work.

External systems publish bounded signals through `POST /api/agenda/events`.
`idempotency_key` is the producer's stable delivery key. The executive claims
one due event under its lease, requires a proposed pursuit to cite that event's
evidence reference, then commits the entry and acknowledgement atomically.
Expired claims are recoverable; exhausted events and activity steps move to
`GET /api/agenda/dead-letters`.

Planner calls and each capability use a durable rolling quota window. Exhausted
quota suspends or defers work until the recorded reset time. It never bypasses
the step policy, approval gate, queue priority, spatial authorization, or
Presence Contract.

Operational spans are appended to `agenda/traces.jsonl` and rotated to
`agenda/traces.1.jsonl`. They contain identifiers, timing, status, capability
names, and counters—not prompts or results. A content-free quality summary is
available from `GET /api/agenda/report?hours=168` or:

```bash
uv run python scripts/autonomy_report.py --hours 168
```

Operators can inspect `GET /api/agenda` for the current lease/fence and backlog
health, and `GET /api/agenda/events?status=dead_letter` for failed external
signals. A dead external event can be explicitly returned to the pending queue
with `POST /api/agenda/events/{event_id}/retry`.

## Baseline implementation invariants

The implemented chain:

1. Persist recent entries, one active entry, suspended entries, and a small
   future queue.
2. Support `pursuit`, `idle`, `scheduled`, and `user` entry kinds.
3. Require every autonomous entry to have grounding, a time budget, and a
   completion condition.
4. Generate one proposal when the active autonomous entry completes or the
   agenda otherwise needs reconsideration.
5. Validate the proposal deterministically before accepting it.
6. Suspend autonomous work when a user task arrives and reconsider it
   afterward.
7. Advance pursuits through interruptible embodied steps.
8. Treat bounded idle as a normal link that schedules the next reconsideration,
   never as termination of the chain.
9. Preserve scheduler accuracy, tool proximity, queue priority, approval
   boundaries, and the Presence Contract.
10. Record enough non-content behavioral data to evaluate chain coherence,
    repetition, grounding, cost, and productive time.

## Research invariant

The central invariant is:

> When the agent is running and autonomy is enabled, its present behavior has an
> agenda entry, and completing that entry leads to a bounded attempt to produce
> the next grounded intention.

This guarantees continuity of deliberation without granting any individual
task unlimited time, authority, or resources. The resulting behavior chain may
be indefinitely extensible; whether an LLM can keep that chain meaningful is
the question the system is designed to test.
