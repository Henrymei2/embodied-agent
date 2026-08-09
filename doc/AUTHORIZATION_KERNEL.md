# Authorization kernel and action contract

Part 5 begins with one rule: an action proposal is data, never authority. The
version-1 `ActionDescriptor` records the proposed actor, capability, target,
private-argument digest, side-effect class, reversibility, confidence,
evidence, required user authority, spatial requirement, privacy class,
external destination, and stable idempotency key.

Private arguments remain with the executor. The descriptor carries only their
canonical SHA-256 digest, so a policy record can bind the exact proposal
without becoming another copy of an email body, file content, or conversation.

## Trusted boundary

`AuthorizationKernel` accepts two objects:

1. an untrusted `ActionDescriptor`, which may originate with a planner; and
2. an `AuthorizationContext`, assembled only from trusted runtime state.

The trusted context contains granted capabilities, exact evidence references,
reachable spatial targets, session authority, a Presence decision, remaining
quota, privacy ceiling, allowed external destinations, execution mode, and an
optional user-confirmation receipt. No field from a page, email, calendar
description, file, tool result, screen observation, or model response can add
itself to those sets.

An explicit confirmation is short-lived and bound to the digest of the entire
descriptor. Changing the target, arguments, destination, capability, evidence,
or idempotency key therefore invalidates the confirmation. Reusing the same
descriptor preserves the same idempotency identity instead of authorizing a
different effect.

## Fail-closed policy checks

Every decision records checks for:

- actor binding;
- capability grant;
- trusted evidence;
- minimum confidence for consequential work;
- user authority;
- spatial reachability;
- Presence permission for user-directed communication;
- quota;
- privacy ceiling;
- external destination; and
- idempotency identity.

All checks must pass. Preview mode produces `preview_only`, whose `may_commit`
property is false even though the proposal is otherwise policy-valid. External
state changes, communication, and irreversible or unknown effects require an
exact user confirmation. Reversible local and physical changes require a
trusted session grant unless the descriptor asks for stronger authority.

## Transaction boundary

Production `ServiceContext` instances construct an
`ActionTransactionCoordinator` and inject it into `ToolExecutor`. Read-only
tools retain their proximity gate. Every reviewed consequential MCP call goes
through proposal, policy, commit claim, invocation, verification, and one
terminal outcome. Unknown dynamic mutations remain blocked until reviewed.

The workspace-local `authorization/transactions.jsonl` journal is append-only,
hash chained, file locked, flushed with `fsync`, and strictly replayed. It holds
argument/target/destination and evidence digests, never raw arguments or tool
output. An interrupted post-commit attempt becomes `unresolved`; it is not
guessed safe or automatically retried.

Duplicate identity binds client, server, tool-call ID, and exact argument
digest. An exact replay has no effect. Changed arguments create a different
proposal even if a caller incorrectly reuses the tool-call ID.

Autonomous foreground email work links a pending transaction back to its agenda
entry without copying private arguments into the agenda. The entry records only
the transaction ID, capability, and bounded status summary, moves to
`blocked_approval`, and emits a Presence-gated report. Approval gives the same
checkpoint a fresh execution identity while the authorization journal retains
the exact descriptor binding; decline abandons the entry without invoking the
adapter. Restart and reconnect preserve both records.

## User authority and outcome evidence

Reversible local and embodied actions can use a reviewed session grant, so
navigation and drafting do not cause approval fatigue. External,
communication, irreversible, and unknown effects require short-lived exact
confirmation at the writing desk's trusted user-only route. Expired authority
returns to pending.

Presence is an attention boundary, not a renderer-visibility switch. It remains
fail-closed for communication directed at the user, while silent avatar actions
such as walking, sitting, and standing may proceed when the page is hidden, the
coarse heartbeat is stale, or focus mode is active. Those actions remain
physical: they still require a reviewed session grant, pass the physical-risk
confidence threshold, consume quota, and must be verified against the scene
state persisted from the frontend's action result.

Local file effects use direct postcondition checks. Other adapters must return
structured evidence bound to the descriptor digest or provide a verifier.
Missing verification becomes explicit `unresolved`; a compensatable profile
may run one bounded, verified compensator. A success string alone is never
proof of impact.

The optional coordinator constructor argument remains only as a unit-test seam
for legacy proximity tests. The sole production construction site always
supplies it.

## Validation

```powershell
uv run pytest -q tests/test_authorization_kernel.py tests/test_action_transactions.py tests/test_part5_evaluation.py tests/test_furniture_routes.py
uv run ruff check src/open_llm_vtuber/authorization src/open_llm_vtuber/evaluation/part5.py tests/test_authorization_kernel.py tests/test_action_transactions.py tests/test_part5_evaluation.py
```
