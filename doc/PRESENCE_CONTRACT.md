# Presence Contract: Consent-Aware Autonomous Behavior

## Feature description

The Presence Contract lets the desktop companion take initiative without
treating the user's attention as unlimited. Every autonomous action spends from
a refillable attention budget, produces an inspectable **why-I-acted receipt**,
and can receive explicit `helpful`, `dismissed`, or `snoozed` feedback. That
feedback changes the future score of the same behavior direction while remaining
bounded and fully local.

This turns proactivity into a legible relationship:

- The agent may act, but it has a finite initiative budget.
- Focus mode and snooze are hard gates for proactive speech.
- Every action explains its direction, delivery, confidence, cost, and coarse
  context signals.
- The user can shape future behavior without editing prompts.
- User-authored chat and voice requests never spend this budget and retain the
  highest LLM queue priority.

## Why this belongs in an embodied companion

A reactive assistant needs only an answer policy. A persistent companion also
needs a **presence policy**: when to enter the user's attention, how often to do
so, how to explain the decision, and how to gracefully become quieter. The
Presence Contract makes that policy a first-class, inspectable part of the
agent instead of leaving interruption behavior entirely to an LLM prompt.

## Behavior model

Autonomous actions use a token-bucket budget:

| Delivery | Cost | Example |
| --- | ---: | --- |
| Silent | 0.25 | Write a diary note or tidy the room |
| Ask | 1.25 | Offer contextual help or ask permission |
| Speak | 2.00 | Greet the user or share an observation |

The default budget is 6 points and refills at 1 point per hour. A planner tick
still evaluates usefulness, novelty, intrusiveness, risk, and user settings.
The Presence Contract then adds:

1. A bounded affinity adjustment learned from receipt feedback.
2. A hard focus-mode/snooze gate for `ask` and `speak` actions.
3. A final budget/presence check when queued work actually starts.
4. A durable receipt lifecycle (`queued`, `running`, `succeeded`, `failed`, or
   `canceled`) with failed/canceled attempts refunded.

Silent actions may continue during focus mode or snooze, but they still cost a
small amount so background work cannot grow without limit. Setting
`user_present` to `false` blocks user-facing initiative while allowing safe
silent work. Expired presence fails closed in the same way: it can never
authorize `ask` or `speak`.

## Configuration

Add these fields under `system_config.autonomy_config` in `conf.yaml`:

```yaml
system_config:
  autonomy_config:
    enabled: true
    headless_enabled: true
    default_pursuit_seconds: 900
    maximum_pursuit_seconds: 1800
    fallback_reconsideration_seconds: 300
    store_poll_seconds: 1
    planner_circuit_failure_threshold: 3
    planner_circuit_max_seconds: 1800

    # Presence Contract
    attention_budget: 6.0
    attention_refill_per_hour: 1.0
    receipt_retention: 50
    default_snooze_minutes: 60
    report_retry_seconds: 60
    report_retry_limit: 5
    report_backlog_limit: 20
```

Completion delivery is backed by the agenda's persistent report outbox.
Headless silent work can record its receipt immediately. User-facing reports
remain pending until a live, fresh Presence Contract permits delivery; focus,
snooze, stale presence, and exhausted budget never authorize speech.

Limits enforced by configuration validation:

| Setting | Range | Meaning |
| --- | ---: | --- |
| `attention_budget` | 1-100 | Maximum stored attention points |
| `attention_refill_per_hour` | 0-100 | Points restored per elapsed hour |
| `receipt_retention` | 5-500 | Maximum locally stored receipts |
| `default_snooze_minutes` | 5-1440 | Snooze duration when the client omits one |
| `report_retry_seconds` | 5-86400 | Base delay for deferred delivery |
| `report_retry_limit` | 1-20 | Maximum failed delivery attempts; policy deferrals do not consume it |
| `report_backlog_limit` | 1-500 | Pending user-facing reports before planning yields to bounded idle |

## WebSocket protocol

### 1. Publish coarse presence context

The frontend should send this message when activity state changes and at least
once every three minutes while it wants the signal to remain active:

```json
{
  "type": "presence-context",
  "idle_seconds": 42,
  "user_present": true,
  "focus_mode": false
}
```

All fields after `type` are optional and update the previous live value.
`idle_seconds` is clamped to 0-604800. Presence signals expire after three
minutes so a crashed or disconnected frontend cannot leave focus state stuck.
Typing or speaking to the agent also resets the backend's live idle value to
zero while preserving focus mode.

The backend intentionally does not accept or persist window titles, application
names, screenshots, keystrokes, or raw screen content in this protocol.

### 2. Receive an autonomous action receipt

When an autonomous request starts and when it finishes, the backend sends:

```json
{
  "type": "autonomy-receipt",
  "receipt": {
    "receipt_id": "9f6871c9a0b44fa4bc9c8fe73536589b",
    "created_at": "2026-07-13T16:20:00+00:00",
    "direction": "care",
    "action": "ask",
    "score": 0.81,
    "attention_cost": 1.25,
    "reason": "acting on care via ask",
    "status": "running",
    "started_at": "2026-07-13T16:20:01+00:00",
    "completed_at": null,
    "error": null,
    "budget_charged": true,
    "signals": {
      "user_present": true,
      "activity": "idle",
      "focus_mode": false,
      "quiet_minutes": 47.2,
      "hour_of_day": 12
    },
    "feedback": null,
    "feedback_at": null
  },
  "attention_budget_remaining": 4.75
}
```

Suggested UI: show a small receipt icon on proactive messages. Expanding it can
display “Why now?”, the cost, and three feedback controls.

### 3. Send feedback

Helpful:

```json
{
  "type": "autonomy-feedback",
  "receipt_id": "9f6871c9a0b44fa4bc9c8fe73536589b",
  "feedback": "helpful"
}
```

Dismissed:

```json
{
  "type": "autonomy-feedback",
  "receipt_id": "9f6871c9a0b44fa4bc9c8fe73536589b",
  "feedback": "dismissed"
}
```

Snooze proactive speech:

```json
{
  "type": "autonomy-feedback",
  "receipt_id": "9f6871c9a0b44fa4bc9c8fe73536589b",
  "feedback": "snoozed",
  "snooze_minutes": 120
}
```

The backend answers with an `autonomy-feedback-result` followed by an updated
`presence-state`. Feedback is accepted once per receipt; repeated submissions
are rejected so they cannot accidentally train the preference twice.

### 4. Request current state

Frontend request:

```json
{
  "type": "request-presence-state"
}
```

Backend response:

```json
{
  "type": "presence-state",
  "enabled": true,
  "attention_budget": 4.75,
  "attention_budget_max": 6.0,
  "attention_refill_per_hour": 1.0,
  "snoozed": false,
  "snoozed_until": null,
  "direction_affinity": {
    "care": 0.15,
    "social": -0.25
  },
  "receipts": []
}
```

The real response contains the retained receipts in newest-first order. The
same state message is also sent after connection, presence updates, and feedback.

## Feedback semantics

Direction affinities stay in `[-1, 1]` and affect planner scores rather than
becoming prompt instructions:

| Feedback | Affinity change | Additional effect |
| --- | ---: | --- |
| `helpful` | +0.15 | Prefer similar directions slightly more |
| `dismissed` | -0.25 | Prefer similar directions less |
| `snoozed` | -0.10 | Block proactive speech until snooze expires |

This deliberately learns **categories of initiative**, not private content.
For example, dismissing a social greeting teaches the planner to reduce social
initiations; it does not add the text of that greeting to memory.

## Persistence and privacy

The companion stores `presence_ledger.json` in the existing agent workspace,
normally `~/embodied-agent/`. It contains:

- the current budget and refill timestamp;
- an optional snooze deadline;
- bounded direction affinities;
- the configured number of receipts.

Receipts contain only coarse activity bands (`active`, `recently-active`,
`idle`, `away`, or `unknown`), not precise activity seconds. The live precise
idle value exists only in memory and expires after three minutes. No network
service is required for budgeting, receipt creation, or feedback learning.

## Backend implementation map

- `src/open_llm_vtuber/autonomy/presence.py`: token bucket, receipts, feedback,
  persistence, and shared-workspace ledger registry.
- `src/open_llm_vtuber/autonomy/observation.py`: validated ephemeral presence
  signals.
- `src/open_llm_vtuber/autonomy/planner.py`: preference scoring and final
  focus/snooze/budget gate.
- `src/open_llm_vtuber/autonomy/driver.py`: receipt emission and queue dispatch.
- `src/open_llm_vtuber/websocket_handler.py`: frontend presence, feedback, and
  state messages.
- `tests/test_presence_contract.py`: behavioral and persistence coverage.

## Acceptance checklist

- A spoken autonomous action stops when the budget has fewer than 2 points.
- Focus mode blocks `ask` and `speak` but allows budgeted silent work.
- Snooze blocks proactive speech until its UTC deadline.
- Every dispatched autonomous action has a unique receipt.
- Feedback changes only the receipt's direction affinity and is applied once.
- User text and voice requests remain priority 0 and bypass the attention budget.
- Restarting the server preserves budget, snooze, affinity, and retained receipts.
