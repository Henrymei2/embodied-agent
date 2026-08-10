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

furnitures
- gmail
- calendar

The agent is initially doing something unrelated.

Sequence
A new email arrives.
The agent notices the notification without being prompted.
It walks to the computer and reads the email.
It realizes that the email affects an existing meeting or deadline.
It walks to the calendar to inspect the conflict.
It drafts a response and go to planning desk, leaving a pending action for the user.

After sequence is finished, speak for example:

“The meeting was moved earlier and now conflicts with your review. I prepared a reply asking whether 3:30 works instead.”

What It Demonstrates
event-driven behavior,
autonomous intention formation,
cross-tool reasoning,
persistent tasks,
environment → intention → action.

2. Remembering an Unfinished User Intention
Setup

Earlier daily memory:
- “When the paper is ready, remind me to send it to Alex.”

Agent worksapce:
- paper file exists in agent's workspace

Sequence
The agent recalls the earlier unfinished intention.
It decides to check if the paper now exists
The agent went to the computer, and notices that the final paper now exists.
It retrieves the relevant file.
It moves toward the email interface.
It prepares the message.
Before sending, it waits for user approval.

After sequence is finished, speak for example:

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