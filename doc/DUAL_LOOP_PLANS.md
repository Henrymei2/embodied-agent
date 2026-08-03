# Dual-loop cognition and resumable plans

Part 4 separates the latency-sensitive conversation loop from slower
deliberative work and gives every executed agenda step a durable state machine.
It extends the existing agenda; it does not replace its live/headless ownership
lease, Presence policy, approval rules, or quotas.

## Runtime contract

The agenda selects an intention. `AgendaPlanBridge` writes its validated step to
`<workspace>/agenda/plans.json`, then `ResumablePlanExecutor` advances one
bounded transition at a time:

```text
plan: ready -> running -> completed | failed | cancelled
                    |-> blocked | repairing | cancelling

step: ready -> executing -> result_ready -> completed
          |             |-> blocked/failed
          |-> failed -> bounded repair -> new ready step
```

`executing` is persisted before an adapter is called. `result_ready`, with an
opaque outcome reference and digest, is persisted after the adapter returns.
Only then may postconditions commit the step. A side-effecting step has one
stable idempotency key for its whole lifetime. If a process dies after the
external effect but before recording the result, the replacement process calls
the adapter with that same key. An adapter that cannot honor idempotency must
not be registered as a side-effecting adapter.

A completed/failed/cancelled plan atomically creates a pending terminal report.
Delivery uses claim/ack states and recovers a claim after five minutes, so a
crash cannot silently consume the report. The agenda acknowledges a per-step
sidecar report only after it has absorbed the result. The agenda status,
handoff marker, checkpoint retirement, and optional user report are one atomic
agenda mutation. Startup reconciliation restores a deterministic plan link if
the process died after sidecar creation, and acknowledges absorbed sidecars
even when their agenda entries are already terminal.

## Bounds and fail-closed behavior

- Every plan has a UTC deadline, retry budget, repair budget, cost budget, at
  most 256 steps, and at most 2,000 recorded transitions.
- Running plans require an owner, lease expiry, and fencing token. A stale token
  cannot mutate or take over a live plan.
- Missing precondition or postcondition evidence is failure, not success.
- A repair must cite the failed step, stay within the original action scope,
  not increase risk or cost beyond the plan budget, and cannot replay a
  side-effecting step that already has an outcome.
- The live agenda adapter may add one bounded retry step only for read-only
  transient failure. The replacement preserves the exact action, arguments,
  preconditions, postconditions, risk, and effect class.
- High-risk work cannot enter `executing` without a persisted counterfactual
  assessment. Counterfactual evaluation is not authorization; the existing
  approval rule remains in force and Part 5 adds the central authorization
  kernel.
- An exception after an external request makes the outcome unknown. The plan
  blocks for reconciliation instead of guessing that retry is safe.
- Deadline sweeping terminates abandoned read-only work, but preserves an
  attempted side effect with no receipt in `cancelling` until its outcome is
  reconciled.
- Human preemption, due-work preemption, restart, and offline abandon release or
  cancel the sidecar before changing agenda ownership. Interrupted reads may
  resume; result-ready steps commit without replay; unreceipted effects remain
  `cancelling` for reconciliation.
- Impossible plan/step/index combinations fail schema validation, and an
  invalid or crashing repair proposal terminates with an inspectable reason
  instead of remaining indefinitely in `repairing`.
- Operator snapshots omit action arguments. They expose identifiers, states,
  budgets, blocked reasons, and outcome references only.

## Reactive and deliberative loops

`DualLoopCoordinator` owns at most one deliberative inference task. Direct user
work cancels it before entering the reactive lock and emits a preemption receipt
with measured latency and budget status. The production agenda applies the same
bounded cancellation rule through `human_preemption_timeout_seconds` (default
250 ms) and records the measurement on the user agenda entry.

`FairPlanScheduler` gives direct human work strict priority. Non-human work is
weighted across urgent events, deadlines, background reflection, and
maintenance, with an age threshold that prevents starvation.

## Provenance integration

After ASR/text normalization, the conversation loop starts private provenance
ingestion in a worker thread while the model response proceeds. The public
ledger stores only an assertion reference, a responding intention, and a
terminal outcome code. Raw turn text remains in the governed private content
store. `memory=off` and `skip_memory` both disable this path, preserving the
memory ablation without removing persona, rules, voice, or task access.

## Backward compatibility

Agenda schema v1 is unchanged. Existing workspaces without `agenda/plans.json`
load normally. A new agenda entry opts into Part 4 with a
`resumable_plan_id` metadata field; its private arguments and transition history
live only in the sidecar. Removing the sidecar feature therefore does not make
the legacy agenda unreadable. Unknown sidecar schema versions fail closed.

## Reproducible validation

```powershell
uv run pytest tests/test_autonomy_part_four.py tests/test_provenance_ingestion.py -q
uv run python scripts/run_part4_evaluation.py --verify
uv run ruff check src/open_llm_vtuber/autonomy src/open_llm_vtuber/provenance scripts/run_part4_evaluation.py tests/test_autonomy_part_four.py tests/test_provenance_ingestion.py
```

The frozen held-out fixture is
`research/fixtures/held_out/part4-repair.json`; its committed report is
`research/baselines/part4-plan-repair.json`. The fixture is synthetic and
content-free. It demonstrates mechanism behavior, not production task success.

## Known boundary

Part 4 proves deterministic resumption when an action adapter honors the stable
idempotency key. It deliberately does not claim that every existing third-party
tool is transactional. Part 5 owns the single authorization boundary,
capability tokens, outcome verification, compensation, and adapters for
services without native idempotency.
