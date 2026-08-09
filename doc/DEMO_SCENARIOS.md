Embodied Autonomous Agent Demo Plan
Goal

Demonstrate an embodied agent that does more than respond to explicit user commands.

The key idea to communicate is that the agent:

continuously perceives its environment,
maintains persistent intentions and memory,
creates new goals from context and environmental changes,
acts through tools and objects in the environment,
resumes interrupted tasks,
and makes its internal state legible through movement, attention, and spatial behavior.

The demo should avoid feeling like “an LLM with an avatar.” The strongest moments are those where the user does not explicitly tell the agent what to do.

Part I — Pre-Recorded Scenarios

These scenarios can be more scripted, complex, and polished. They should emphasize autonomy, continuity, and the relationship between the agent and its environment.

1. An Email Changes the Agent’s Plan
Setup

The agent has:

a desk,
a computer,
a calendar,
a place for notes or pending tasks.

The agent is initially doing something unrelated.

Sequence
A new email arrives.
The agent notices the notification without being prompted.
It walks to the computer and reads the email.
It realizes that the email affects an existing meeting or deadline.
It walks to the calendar to inspect the conflict.
It returns to the computer and prepares a possible response.
It leaves a visible note or pending action for the user.

Example outcome:

“The meeting was moved earlier and now conflicts with your review. I prepared a reply asking whether 3:30 works instead.”

What It Demonstrates
event-driven behavior,
autonomous intention formation,
cross-tool reasoning,
persistent tasks,
environment → intention → action.
2. Remembering an Unfinished User Intention
Setup

Earlier, the user casually told the agent:

“When the paper is ready, remind me to send it to Alex.”

Later, the paper becomes available.

Sequence
The agent notices that the final paper now exists.
It recalls the earlier unfinished intention.
It retrieves the relevant file.
It moves toward the email interface.
It prepares the message.
Before sending, it waits for user approval.

Example:

“The paper is ready now. You previously wanted to send it to Alex. I have the message ready if you want to send it.”

What It Demonstrates
long-term memory,
opportunity detection,
linking past intentions to new environmental states,
proactive behavior,
permission boundaries.
3. The Room as the Agent’s Memory
Setup

Different pieces of furniture represent different categories of state.

For example:

desk = active tasks,
drawer = waiting for someone else,
shelf = long-term projects,
table = things requiring user attention.
Sequence

The user asks:

“What am I still waiting on?”

Instead of generating a text list:

The agent walks to a drawer.
It opens it.
It retrieves several objects representing pending items.
It places them on the table.
It explains them spatially.
What It Demonstrates
spatial memory,
embodied information organization,
persistent world state,
the environment functioning as part of cognition.
4. Recovering After an Interruption
Setup

The agent is already working on a task.

Sequence
The agent is inspecting something on the computer.
The user interrupts with an unrelated question.
The agent stops and comes to the user.
It answers the question.
The conversation ends.
Without being told to continue, the agent remembers the interrupted task.
It says something like:

“I was still checking your schedule.”

It returns to the computer and resumes.
What It Demonstrates
persistent intentions,
interruption handling,
task resumption,
behavior beyond request-response interaction.
5. Detecting an Unexpected Problem
Setup

The agent has access to some changing state, such as:

a failed build,
a changed deadline,
a new message,
a modified file,
a failed background process.
Sequence
Something in the environment changes.
The agent notices it.
It stops its current activity.
It investigates using multiple tools or locations.
It determines the likely cause.
It performs only low-risk actions automatically.
It approaches the user before taking a consequential action.

Example:

“The build failed after the dependency update. I found a likely fix, but it would modify the environment, so I have not applied it yet.”

What It Demonstrates
anomaly detection,
investigation,
self-generated goals,
autonomous restraint,
calibrated authority.
6. Preparing for the User Before They Arrive
Setup

Before leaving, the user says:

“I have an important presentation tomorrow.”

Sequence

When the user returns later:

The agent has reorganized the room.
Relevant presentation materials are already on the desk.
Important metrics are on a board.
Possible questions are grouped nearby.
The agent explains:

“I thought these were the things you were most likely to need tomorrow.”

What It Demonstrates
asynchronous continuity,
proactive preparation,
environment manipulation,
inference from vague user goals.
7. Competing Intentions
Setup

The agent is preparing something for the user.

During the task, an important new event appears.

Sequence
The agent is working on Task A.
A new email or notification arrives.
The agent notices it.
It looks between its current workspace and the new event.
It decides that Task B is temporarily more important.
It explains:

“This affects today’s deadline. I’m going to check it first, then return to the presentation.”

It later resumes Task A.
What It Demonstrates
prioritization,
competing goals,
autonomous task switching,
explicit behavioral legibility.
8. The Agent Organizes Its Own Space
Setup

The user enters while the agent is already doing something.

Sequence

The agent is:

reorganizing notes,
moving related task objects together,
cleaning up its workspace,
restructuring memory objects.

The user asks:

“What are you doing?”

The agent responds:

“I noticed that these tasks usually happen together, so I’m keeping them in the same place.”

What It Demonstrates
agent-initiated behavior,
self-organization,
learned routines,
a sense that the agent inhabits the space rather than only waiting inside it.
9. Consequential Action Requires Approval
Setup

The agent can perform an action that has external consequences, such as sending a message.

Sequence
The agent independently prepares the action.
It walks to the relevant interface.
It completes all reversible preparation.
At the final irreversible step, it stops.
Its body language visibly communicates hesitation or waiting.
It asks the user for approval.

Example:

“Everything is ready. Sending this will contact them externally. Should I send it?”

What It Demonstrates
autonomy without uncontrolled authority,
physical representation of permission boundaries,
trust,
reversible versus consequential actions.
Part II — Live Demo Scenarios

Live demos should be short, robust, and easy to understand.

The goal is not to demonstrate maximum technical complexity. The goal is to create a moment where the participant feels:

“This agent noticed what I did and formed its own response.”

Ideally, each interaction should produce that feeling within 30–90 seconds.

Live Demo 1. The Agent Notices the User Arriving
Experience

When the participant enters the room, the agent is already doing something.

It does not begin in an idle assistant pose.

The agent notices the participant, stops what it is doing, looks toward them, and says something simple:

“Oh, you’re here.”

Why It Works

This immediately establishes that the agent existed before the interaction began.

It feels like entering someone else’s space rather than opening an application.

Live Demo 2. Spatial Memory
Experience
Ask the participant to pick up an object.
Let them place it anywhere in the room.
Continue with another interaction.
Later, ask:

“Where did I put that object?”

The agent walks directly toward the remembered location.

Optional Extension

The participant can move multiple objects and later ask about one of them.

What It Demonstrates
shared spatial memory,
persistent perception,
grounding in the environment.
Live Demo 3. Give the Agent a Vague Situation
Experience

Ask the participant to say something like:

“I have an important presentation tomorrow.”

Do not instruct them to ask for help.

The agent should independently interpret this as a situation that may require preparation.

Possible behavior:

look around,
walk toward relevant materials,
inspect existing presentation objects,
start organizing them,
ask a context-specific question only if necessary.

Example:

“I’ll first see what you already have prepared.”

What It Demonstrates

The user provides a situation, not a command.

The agent converts:

context → inferred goal → action.

Live Demo 4. Interrupt and Resume
Experience
The agent begins working on something.
The participant interrupts it with an unrelated question.
The agent stops and engages with the participant.
After the conversation ends, the participant says nothing else.
The agent independently remembers the previous task and returns to it.

Example:

“Right, I was still checking your presentation.”

What It Demonstrates
persistent intention,
task continuity,
interruption recovery.

This can be especially effective because the participant can deliberately try to distract the agent.

Live Demo 5. Communicating Through Physical Action
Experience

Place several objects on a desk.

Ask the participant to move one object somewhere else without saying anything.

The agent notices the action and interprets it as potentially meaningful.

For example:

“You moved this away from the others. Are you done with it, or should I keep it separate?”

Important Detail

The agent does not need to infer the intention correctly every time.

The important behavior is recognizing that physical actions may be communicative.

What It Demonstrates
nonverbal interaction,
environment-aware interpretation,
shared attention.
Live Demo 6. The Agent Creates a Task From an Event
Experience

While the participant is doing something else:

trigger a new environmental event,
such as a notification or message,
let the agent notice it without being prompted,
let it investigate,
let it decide whether the event matters.

The participant should initially have no role.

Only after the agent understands the situation should it approach the participant.

Example:

“Something just changed that may affect what we were doing.”

What It Demonstrates

This may be the clearest demonstration of genuine autonomy.

Live Demo 7. Permission Boundary
Experience

Give the agent access to an action that is conceptually consequential.

The agent can independently:

gather information,
prepare the action,
organize the required materials,
create a draft.

However, at the final step it stops.

For example, the agent places its hand near a virtual Send button and looks back at the participant.

“This will actually send it. Do you want me to continue?”

What It Demonstrates
autonomous preparation,
explicit authority boundaries,
understandable trust mechanisms.
Live Demo 8. Teach the Agent a Temporary Rule
Experience

The participant says something like:

“Anything I put on this shelf means I’m done with it.”

Later, the participant places an object there without giving another instruction.

The agent reacts according to the newly learned convention.

For example, it archives the associated task or moves it out of the active workspace.

What It Demonstrates
rapid adaptation,
user-created environmental conventions,
shared semantics of space.
Live Demo 9. Ask the Agent What It Is Doing
Experience

The agent should occasionally be performing an autonomous activity.

The participant can naturally ask:

“What are you doing?”

The agent should be able to explain its current intention at an appropriate level:

“I’m checking whether the new message changes your schedule.”

Not:

“I am executing tool call number four.”

What It Demonstrates
legible autonomy,
intention awareness,
natural supervision.
Recommended Signature Demo

If only one scenario can be made extremely polished, prioritize this one.

Environment → Intention → Action
Sequence
The agent is quietly doing something in its room.
The participant is present but gives no instruction.
A notification arrives on the computer.
The agent notices it.
It stops what it is doing.
It walks to the computer.
It reads the new information.
It walks to another object, such as the calendar.
It discovers a consequence.
It performs one or two reversible actions.
It approaches the participant.
It explains:

“Something just happened that affects your plan for tomorrow.”

Why This Should Be the Signature Interaction

Within roughly twenty seconds, it communicates nearly the entire concept:

The agent perceives.
The agent cares about some things more than others.
The agent forms intentions.
The agent uses its environment.
The agent acts without being commanded.
The agent knows when to involve the human.

Suggested Implementation Priority
Tier 1 — Highest Value / Lower Complexity
agent notices user arrival,
persistent current intention,
interruption and task resumption,
object location memory,
autonomous response to a triggered notification,
walking between relevant furniture,
explicit approval before consequential actions.
Tier 2 — Stronger Autonomy
vague goal → self-generated subtasks,
multiple competing intentions,
environmental changes triggering new goals,
spatial task organization,
short-term learned spatial conventions.
Tier 3 — More Experimental
long-term episodic memory,
agent reorganizing its own room,
learned behavioral routines,
autonomous preparation before the user arrives,
agent-generated spatial representations of knowledge,
richer nonverbal communication through gaze, posture, hesitation, and movement.
Design Principle

For every demo, ask:

What would happen if the user said nothing?

If the answer is “nothing,” the interaction will probably still feel like a conventional assistant.

The strongest demonstrations are those where something changes in the world, the agent notices it, forms an intention, and begins acting before the user explicitly asks for anything.

---

# Current Implementation Assessment (2026-08-08)

## Assessment standard

This section distinguishes an end-to-end demo from a collection of available
components.

- **Supported**: the scenario can be run through the normal product path without
  changing code. Model wording may vary, but the required trigger, state
  transition, action, and safety boundary exist.
- **Controlled demo only**: most of the scenario exists, but a facilitator must
  inject an event, prepare state manually, or accept missing physical behavior.
- **Not supported**: a central interaction channel or state model is absent, so
  prompting or scripting the model alone would only simulate the capability.

External credentials and services are not treated as implemented product
capabilities. Gmail and Google Calendar require configured OAuth and a working
network. The browser requires Node and Playwright. LLM-dependent choices also
need rehearsal; passing deterministic infrastructure tests does not guarantee
that every model will choose the desired action on every run.

## Summary

Of the 18 numbered scenarios, **7 are supported end to end, 7 are suitable only
as controlled or reduced-scope demos, and 4 are not currently supported**. The
signature demo is supported when the opt-in Gmail producer and Google OAuth are
configured; the built-in simulated fixtures provide the offline rehearsal path.

The strongest implemented chain today is:

> injected external event -> persistent agenda intention -> visible movement ->
> gated tool use -> user interruption -> suspended work -> automatic
> reconsideration and resumption -> approval before an external side effect

The event-driven perception path now covers newly unread Gmail messages,
Google Calendar changes, and files in one explicitly configured workspace demo
directory. Drive, remote CI/build systems, supervised processes, and participant
movement are not event sources in this release.

## Part I — Pre-Recorded Scenarios

| # | Scenario | Status | What works now | What is still missing |
| --- | --- | --- | --- | --- |
| 1 | An Email Changes the Agent's Plan | **Supported** | Real and simulated unread Gmail events now use the same mandatory foreground policy at timer priority: they suspend safe background work, issue a dedicated inspect-and-handle request, always retain an outcome report, and durably block on exact authorization before external writes. In VR, right A opens the simulated launcher without touching Google. | The simulated launcher is deterministic infrastructure, but model wording and tool choice still require rehearsal. The real Gmail version continues to require Google OAuth/network setup. |
| 2 | Remembering an Unfinished User Intention | **Controlled demo only** | Persistent memory, Gmail tools, approval, and an opt-in watcher for a dedicated workspace demo directory can now detect that the paper file became ready without facilitator event injection. | There is still no structured conditional-commitment matcher for “when X happens, do Y.” The LLM must recover the earlier sentence from memory, and reliable recipient/contact resolution is absent. Drive is not part of the live demo path. |
| 3 | The Room as the Agent's Memory | **Not supported** | The room and avatar position persist, and furniture already represents broad capability categories. | There is no container/inventory model, drawer open/close interaction, portable task objects, object-to-task binding, or operation for retrieving several objects and placing them on a table. The room is currently a capability map, not a spatialized task-memory store. |
| 4 | Recovering After an Interruption | **Supported** | User turns preempt active autonomous work, persist the checkpoint as suspended, receive highest queue priority, and trigger reconsideration of suspended work when the turn finishes. Result-ready steps are not replayed. | For a polished demo, select a short, visibly embodied activity and rehearse the model's spoken resumption wording. The lifecycle itself is implemented. |
| 5 | Detecting an Unexpected Problem | **Supported** | The dedicated workspace watcher and Calendar producer publish typed, idempotent create/update/delete events; event prioritization, multi-step investigation, checkpoints, low-risk execution, and approval gates handle the response. | Use a workspace-file or calendar-change version of this demo. Remote build/CI results and supervised process failures remain future integrations. |
| 6 | Preparing for the User Before They Arrive | **Controlled demo only** | Headless autonomy can continue safe reflection and retain completion reports while the user is absent; memory and agenda state survive reconnection. | Headless execution deliberately forbids room movement and object-bound tool work, so the agent cannot reorganize the visible room or place materials while no embodied client is connected. A deferred “prepare on next connection” workflow and artifact-to-room placement model are missing. |
| 7 | Competing Intentions | **Supported** | The agenda has one foreground intention, priority/fair scheduling, suspension, event claims, and later reconsideration. A real newly unread Gmail message or Calendar change can now provide the competing event. | Google OAuth/network setup and model rehearsal are required for the live external-service version; simulated presets are visibly labeled. |
| 8 | The Agent Organizes Its Own Space | **Controlled demo only** | The agent can describe, place, move, and remove room objects from the design board, and the autonomous agenda can propose grounded activities. | There is no learned co-occurrence model, task-object relationship graph, or routine learner that can justify “these tasks usually happen together.” Current rearrangement would be a one-off LLM decision rather than learned self-organization. |
| 9 | Consequential Action Requires Approval | **Controlled demo only** | The authorization kernel creates a durable pending confirmation for external state changes and communication, binds approval to the exact action, and exposes pending decisions in the UI. The agenda also blocks approval-required plans. | The current rule asks for confirmation for all external-state writes, including creating a Gmail draft, so the exact “autonomously finish every reversible remote preparation, stop only at Send” sequence is not available. The avatar also has only standing/sitting plus walk/idle animation—no hesitation, hand-near-button, or look-back pose. |

## Part II — Live Demo Scenarios

| # | Scenario | Status | What works now | What is still missing |
| --- | --- | --- | --- | --- |
| 1 | The Agent Notices the User Arriving | **Controlled demo only** | The frontend sends coarse `user_present`/idle signals; a presence change wakes the agenda; idle relocation means the avatar need not begin at a fixed assistant pose. | “Arrival” is inferred from page visibility and activity, not a room sensor. There is no deterministic arrival policy that interrupts the current activity, turns toward the user, and greets exactly once. Gaze/turn-to-user animation is absent. |
| 2 | Spatial Memory | **Not supported** | Stage object and avatar coordinates persist, and the agent can resolve room objects and walk to them. | Participants cannot pick up and drag/drop portable objects in the current UI, and there is no user-object-motion event, portable-object identity/history, or “last placed by user” memory. Agent-driven furniture movement is not equivalent to observing the participant. |
| 3 | Give the Agent a Vague Situation | **Supported** | The user's statement is stored in conversation memory; after the user turn, the continuous agenda reconsiders suspended/new work using memory, presence, available capabilities, and room objects, and can generate a bounded follow-up intention without another command. | Behavior is model-dependent. Rehearse with the selected model and seed a presentation-related plan, todo, or file so the inferred action is grounded rather than generic. |
| 4 | Interrupt and Resume | **Supported** | This is the live form of Part I scenario 4 and uses the same implemented preemption, suspension, checkpoint, and reconsideration lifecycle. | Only rehearsal and a short robust task are needed. |
| 5 | Communicating Through Physical Action | **Not supported** | None of the existing stage interaction channels reports participant-driven object movement. | Add direct object manipulation, motion events with actor attribution, shared-attention tracking, change interpretation, and a clarification policy. |
| 6 | The Agent Creates a Task From an Event | **Supported** | Gmail, Calendar, and the dedicated workspace watcher publish the same bounded typed trigger shape, wake the executive, and let the planner claim and investigate it. Stable source revisions deduplicate repeats; failures retain the existing retry/dead-letter behavior. | Drive, CI/build, and process sources are not included. The Agenda panel offers clearly labeled simulated presets for offline demos. |
| 7 | Permission Boundary | **Controlled demo only** | Exact-action confirmation, pending state, approve/decline controls, destination binding, proximity gating, and post-action verification are implemented. | Rich nonverbal waiting behavior and a virtual Send-button reach animation are absent. The external-write policy also blocks remote draft creation before approval, so the live beat should demonstrate a pending proposed action rather than claim the remote draft is already created. |
| 8 | Teach the Agent a Temporary Rule | **Not supported** | The conversation memory can retain the spoken rule. | There is no typed temporary-rule store, rule lifetime/scope, shelf-placement event, portable-object association, or automatic rule execution. Memory recall alone cannot make the physical trigger observable. |
| 9 | Ask the Agent What It Is Doing | **Controlled demo only** | The agenda sidebar makes active/suspended entries and progress visible, and autonomous step summaries enter memory. | The normal conversational agent is not given a direct authoritative snapshot of the preempted agenda entry. Asking the question first suspends that entry, so a reliable natural-language answer needs a read-only “current/previous intention” context or tool. |

## Recommended Signature Demo

**Status: Supported when configured.** The opt-in Gmail history producer detects
a newly unread message, publishes only a typed message ID/revision with a fixed
trusted summary, and immediately wakes a mandatory priority-10 agenda pursuit.
The agent then walks to
the mailbox to read the actual message, checks Calendar, prepares reversible
reasoning, and stops at the exact approval boundary for external writes. It
reports completion, no-action, approval-blocked, declined, and failure outcomes;
offline reports remain in the persistent outbox until a client reconnects.

The Agenda panel includes a visibly labeled simulated unread-email preset for
offline rehearsal. It uses the same production trigger schema but is not a
claim that Gmail was contacted. For the real signature demo, authorize Google,
enable the Gmail and Calendar producers, let them establish their initial
baselines, and only then deliver the demo email.

## Missing Capability Backlog

### P0 — Reliable event-driven demos (implemented)

1. **Opt-in external event producers** for newly unread Gmail messages, Google
   Calendar changes, and a dedicated relative directory inside the agent
   workspace. Gmail/Calendar persist incremental cursors; the workspace watcher
   intentionally rebuilds its baseline on every server start.
2. **Typed trigger references and deduplication** based on stable message IDs,
   Calendar event revisions, and workspace path/content fingerprints. Producers
   publish fixed summaries and references rather than external content.
3. **An Agenda demo trigger panel** that publishes the production event shape
   and forces a visible `simulated` label for offline presets.

This delivered scope unlocks I-1, a workspace-backed form of I-5, I-7, Live-6,
and the signature demo. It supplies the real file opportunity signal for I-2,
but I-2 remains controlled until conditional commitments are structured.

### Deferred optional event sources

1. Google Drive change ingestion and its additional OAuth/reauthorization work.
2. Provider-specific or signed generic build/CI result ingestion.
3. Lifecycle events from configured supervised background processes.

These are not completion conditions for the current live demo because its file
scenario uses the local workspace watcher and it does not present Drive, CI, or
background-process behavior.

### P1 — Unlock shared physical interaction

1. Portable object entities distinct from fixed furniture.
2. Participant drag/drop or tracked-object input with actor attribution.
3. Persistent object-location history and object-to-task associations.
4. Spatial event interpretation and clarification when intent is ambiguous.
5. Container and surface operations such as open, retrieve, place, and group.

These items unlock scenarios I-3, Live-2, Live-5, and the physical half of
Live-8.

### P1 — Make autonomy directly explainable in conversation

1. Inject a bounded, authoritative agenda snapshot into user turns: interrupted
   intention, current checkpoint, purpose, and intended resume action.
2. Add a deterministic intent-explanation path for “what are you doing?” rather
   than relying on memory retrieval.
3. Add a structured conditional-commitment store and opportunity matcher for
   “when X happens, remind/prepare Y.”

These items complete I-2 and Live-9 and make interruption demos more legible.

### P2 — Improve embodied expression and learned organization

1. Gaze target, facing direction, attention shift, waiting/hesitation, reach,
   and simple object-carry animations.
2. A small behavior state machine connecting approval and interruption states
   to those animations.
3. Explicit temporary spatial rules with expiration and user-visible review.
4. A bounded routine/co-occurrence learner for explainable room organization.
5. A reconnect queue for visible preparation that was planned while headless
   but must wait for an embodied client.

These items complete I-6, I-8, I-9, Live-1, Live-7, and Live-8.

## Evidence Used for This Assessment

- Persistent agenda, event inbox, preemption, resumption, and scheduling:
  `src/open_llm_vtuber/autonomy/agenda.py`, `executive.py`, `activity.py`, and
  `agenda_context.py`.
- Presence signals and attention policy: `autonomy/observation.py` and
  `autonomy/presence.py`.
- Spatial movement and persistent room state: `world_model.py`,
  `stage_state_manager.py`, `autonomy/idle_relocation.py`, and
  `mcp_servers/scene_tools.py`.
- Gmail and Calendar operations: `mcp_servers/gmail_tools.py` and
  `mcp_servers/calendar_tools.py`.
- Exact-action approval and verification: `authorization/` and
  `autonomy/step_policy.py`.
- Typed external producers, cursor persistence, and workspace baselining:
  `src/open_llm_vtuber/autonomy/event_producers.py` and `triggers.py`.
- Offline simulated trigger controls: the Agenda drawer and `/api/agenda/demo-fixtures`.

The event-producer suites use fake Google APIs and temporary workspaces so
baseline, deduplication, failure isolation, and simulated fixtures are verified
without network access. Deterministic infrastructure tests do not replace an
end-to-end rehearsal with the configured LLM, OAuth account, frontend, and
voice stack.
