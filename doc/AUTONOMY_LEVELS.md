# Continuous Autonomy: 3× Level-Up

This roadmap keeps continuous behavior incremental: each level has an explicit
safety and reliability boundary, and the next level does not weaken the gates
established by the previous one.

## Level 1× — Reliable connected runtime (complete)

- one agenda owner per workspace, with deterministic live-client handoff;
- persistent agenda revisions and atomic scheduled-task claims;
- queue-serialized, cancellable LLM work;
- nonblocking bounded idle and scheduled preemption;
- deterministic capability, object, side-effect, and approval policy;
- bounded planner retries and Presence-gated result reporting.

Acceptance evidence lives in `tests/test_autonomy_level_one.py`.

## Level 2× — Durable server-lifetime continuity (complete)

- a server-lifetime headless owner keeps safe continuity when every UI client
  disconnects, then yields to the first live client and resumes after the last
  one leaves;
- headless execution parks movement and object-bound work without invoking the
  activity planner, while safe reflection, evaluation, idle, schedule tracking,
  and memory work may continue;
- completion reports use a persistent outbox, recover interrupted delivery,
  retry with bounded exponential delay, and retain spoken reports until a live
  Presence Contract can authorize them; bounded backlog backpressure pauses new
  planning without dropping reports;
- in-process agenda and presence changes wake the executive immediately, while
  persisted revision polling remains the cross-process fallback;
- repeated planning-pass failures open a persisted exponential cooldown so a
  provider outage cannot become an unbounded request loop;
- the agenda API and drawer expose runtime mode, store revision, completion
  rate, pending reports, planner failure streak, cooldown, and last error.

Acceptance evidence lives in `tests/test_autonomy_level_two.py`.

## Level 3× — Multi-process operational autonomy (complete)

- a renewable workspace lease serializes server processes, gives live owners
  priority over remote headless owners, and fences stale agenda mutations with
  a monotonically increasing token;
- a bounded durable event inbox accepts idempotent external signals, recovers
  abandoned claims, requires evidence-grounded consumption, and atomically
  links an accepted agenda entry to its acknowledgement;
- rolling planner and per-capability quotas apply durable backpressure; bounded
  event and activity retries end in an inspectable dead-letter store rather
  than an infinite retry loop;
- content-free OpenTelemetry-compatible JSONL spans cover planning, event
  handling, report delivery, and activity execution; a CLI and HTTP endpoint
  produce bounded long-horizon chain-quality reports;
- the agenda API and drawer expose the lease state, fencing token, event
  backlog, dead-letter count, and current quota usage;
- acceptance tests exercise stale-writer rejection, live/headless fencing,
  spawned-process takeover, claim/ack recovery, retry exhaustion, quota-window
  reset, content-free traces, chain reports, and replay-soak idempotency.

All approval, spatial, queue-priority, attention, and bounded-work invariants
from Levels 1 and 2 remain in force.

Acceptance evidence lives in `tests/test_autonomy_level_three.py`.
Operational guidance lives in
[AUTONOMY_OPERATIONS.md](AUTONOMY_OPERATIONS.md).
