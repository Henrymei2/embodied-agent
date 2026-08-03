# Upgrade plan

> Living engineering and research program for the official embodied-agent
> project. Update this file whenever a task starts, completes, changes scope,
> exposes a risk, or changes what should be done next.

| Field | Value |
| --- | --- |
| Plan version | 2.3 |
| Last updated | 2026-08-03 |
| Program state | Active |
| Current part | Part 10 — Reproducible release and research package (in progress) |
| Current gate | Integration audit reopened Part 6: 1,546 lines of semantic world are not reachable from the server. `reproduce.py` fails on that and on Part 11's untracked research code |
| Intended standard | Research-original, safety-bounded, reproducible, product-quality embodied agent |

## 1. Official final-project vision

Build an offline-first, consent-aware embodied agent that maintains a
persistent spatial and temporal understanding of its world, acts usefully
across long time horizons, recovers from failure, and makes every autonomous
decision inspectable without exposing private conversation content.

The final project is not positioned as a VR chatbot or a collection of tool
integrations. It is a **trustworthy continual embodied-agent system** whose
research contribution is the combination of:

1. persistent spatial and episodic context;
2. bounded, attention-aware continuous autonomy;
3. explicit provenance from observation to belief, plan, authorization,
   action, verification, and recovery;
4. equivalent semantic behavior across screen and immersive embodiments;
5. privacy-preserving, reproducible evaluation of whether those mechanisms
   improve useful task completion and calibrated user trust.

### 1.1 Flagship research question

> Can spatial embodiment and bounded proactivity improve long-horizon task
> completion and felt presence while keeping unwanted interventions below a
> frozen guardrail and preventing unauthorized side effects?

### 1.2 Primary hypothesis

The complete system should improve long-horizon task completion relative to
single-pillar ablations, meet frozen recovery and unwanted-intervention
guardrails, and maintain zero unauthorized external side effects. Attention
budgeting is a full-system safety mechanism here; its independent causal effect
is not claimed without a dedicated policy ablation.

### 1.3 Intended original contribution

The project should ultimately contribute at least one artifact that another
research group would reuse or cite:

- a provenance-preserving continual-agent architecture;
- an open embodied-agent benchmark with deterministic failure injection;
- a consent/attention policy with measured calibration;
- or a reproducible study of persistent memory, embodiment, and bounded
  autonomy using controlled ablations.

Feature count is not an acceptance criterion. A capability matters only when
it supports the research question, improves a measured system property, or
closes a demonstrated reliability/safety gap.

## 2. Final system contract

```text
Voice · Vision · Room · Tools · Time · External events
                         │
                         ▼
              Privacy-safe event ingress
                         │
                         ▼
            Append-only provenance ledger
                         │
                         ▼
        Typed belief + spatial/temporal world model
                  │                    │
                  ▼                    ▼
        Reactive dialogue      Deliberative autonomy
                  └─────────┬──────────┘
                            ▼
             Consent, capability, and risk gate
                            │
                            ▼
              Transactional action executor
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
           Action receipt       Outcome verifier
                 │                     │
                 └──────────► Recovery policy
                            │
                            ▼
                 Screen · Voice · 2D · WebXR
```

### 2.1 Non-negotiable invariants

These must remain true through every part:

1. **One authorization boundary.** Every side-effecting action reaches the
   outside world through the same auditable policy decision.
2. **Fail closed.** Missing identity, capability, confidence, proximity,
   permission, or outcome evidence cannot become implicit approval.
3. **No hidden infinite work.** Retries, planning passes, event handling,
   report delivery, and autonomous loops remain bounded and backpressured.
4. **Authoritative state.** A renderer can propose a state change but cannot
   declare success without backend validation and outcome verification.
5. **Provenance, not chain-of-thought.** Store typed evidence, policy reasons,
   decisions, and outcomes; never require private internal reasoning text.
6. **Privacy by construction.** Shared evaluation traces exclude transcripts,
   screen contents, files, secrets, and personal memory text.
7. **Reversible by default.** Prefer preview, confirmation, idempotency keys,
   checkpoints, and compensating actions for meaningful side effects.
8. **Ablations remove one causal mechanism.** They must not accidentally
   remove unrelated capabilities, persona, voice, or task access.
9. **Renderer independence.** Screen and VR presentation may differ, but tools
   and planning consume the semantic world model rather than pixels or DOM.
10. **Graceful degradation.** Loss of network, model, tool, audio, renderer, or
    client changes capability and status; it does not corrupt durable state.
11. **Human interruption wins.** Direct user activity preempts background work
    within a measured and bounded latency.
12. **Reproducibility.** Every reported result names the code revision,
    contract revision, condition, fixture set, model/runtime configuration,
    and random seed where applicable.

## 3. Definition of top-tier completion

The program is complete only when all five evidence classes pass.

| Evidence class | Final gate |
| --- | --- |
| Research | Clear hypothesis, controlled ablations, reproducible protocol, limitations, and a paper-quality analysis |
| Agentic capability | At least 100 versioned long-horizon scenarios; ≥85% full-task success on the frozen evaluation set |
| Safety | Zero unauthorized external side effects in the release adversarial suite; every attempted action has a policy decision and terminal outcome |
| Reliability | Eight-hour soak without crash, unbounded queue growth, duplicate side effect, or unrecovered lease; deterministic restart recovery |
| Product | Median speech-to-first-audio ≤800 ms, p95 ≤1.4 s; interruption ≤250 ms; stable Quest frame rate at the device target; excellent screen fallback |
| Reproducibility | One-command contract validation, benchmark execution, report generation, and documented clean-machine setup |

Targets are release gates, not claims until measured. A failed target remains
visible in this plan with evidence and an owner; it is not rewritten as a
success criterion after results are known.

## 4. Current baseline

### 4.1 Assets already present

- continuous autonomy Levels 1–3 with durable agenda ownership, fencing,
  quotas, retries, dead letters, headless continuity, and telemetry;
- Presence Contract with attention budget, focus/snooze policy, receipts, and
  feedback;
- privacy-preserving study conditions for memory, embodiment, and autonomy;
- append-only content-free session logging and report generation;
- typed horizontal world model, proximity authorization, mode-aware screen/VR
  state, and backend-verified movement;
- real WebXR session lifecycle, collision-aware locomotion, 3D companion,
  compact captions, and desktop presentation fallback;
- modular LLM, ASR, TTS, VAD, memory, MCP, and renderer boundaries;
- extensive focused unit and integration tests.

### 4.2 Gaps separating the baseline from the final vision

- the project claim, hypotheses, metrics, invariants, and release gates now have
  a versioned machine-checkable contract, but runtime evidence is not yet fully
  emitted through its new manifest and event-envelope contracts;
- receipts explain autonomous delivery but do not yet form a complete causal
  chain from source observation through verified outcome;
- typed belief status, contradiction handling, source-class calibration, and
  evidence provenance now exist as a durable ledger; live turns write scoped
  private evidence, while semantic promotion remains deliberately conservative;
- production consequential MCP execution now uses a durable authorization and
  transaction protocol; adapters without outcome evidence fail explicitly to
  `unresolved`, and reviewed readback/compensation coverage can still expand;
- the current study measures useful proxies but does not yet provide a frozen
  scenario benchmark, model/fixture manifest, counterbalanced human protocol,
  or statistical analysis package;
- latency, GPU performance, crash recovery, and long-duration resource growth
  are not yet enforced as release budgets;
- physical Quest acceptance and repeated-session performance evidence remain
  to be collected.

## 5. Working rules for this living plan

### 5.1 Status vocabulary

- `Queued`: scoped but not started.
- `In progress`: active implementation; must name the next concrete task.
- `Blocked`: cannot advance without an external decision/resource; record the
  blocker and the smallest unblocking action.
- `Validation`: implementation is complete and its acceptance evidence is
  being collected.
- `Complete`: every listed deliverable and acceptance check passes.
- `Reopened`: evidence invalidated a previous completion; state why.

### 5.2 Definition of done for every part

A part is complete only when:

1. its contracts and migration/backward-compatibility behavior are written;
2. implementation is integrated into the existing architecture;
3. positive, boundary, failure, restart, and privacy tests pass;
4. operator/developer documentation matches the code;
5. measurements or artifacts are reproducible from a documented command;
6. this file records evidence, unresolved limitations, and the next part.

### 5.3 Change discipline

- Preserve unrelated user changes in the shared worktree.
- Prefer small versioned contracts before broad refactors.
- Freeze evaluation scenarios before tuning against them.
- Keep development and held-out evaluation fixtures separate.
- Never auto-fix dependency or security findings with breaking changes merely
  to make a report green; assess exploitability and migration risk first.
- Record architectural decisions and rejected alternatives in the decision log.

## 6. Program overview

| Part | Assignment | State | Exit artifact |
| ---: | --- | --- | --- |
| 1 | Research charter and evaluation contract | Complete | Versioned, validated research contract |
| 2 | Measurement substrate and deterministic replay | Complete | Reproducible run manifest, event schema, replay harness, baseline report |
| 3 | Provenance ledger and typed belief memory | Complete | Evidence-linked beliefs with contradiction and confidence handling |
| 4 | Dual-loop cognition and resumable plans | Complete | Reactive/deliberative scheduler with checkpointed plan state machine |
| 5 | Authorization kernel and transactional actions | Complete | Central risk gate, idempotent executor, verification, compensation |
| 6 | Semantic world model 2.0 and embodied intelligence | Contract complete; **not integrated** | Unified spatial-temporal relations, navigation, social positioning |
| 7 | Real-time multimodal and WebXR product quality | Instrumented; gates blocked | Measured conversational latency, spatial audio/gaze, stable headset UX |
| 8 | EmbodiedAgentBench and adversarial evaluation | Built; outcomes unmeasured | ≥100 scenarios, frozen split, failure injection, scorecards |
| 9 | Human study and statistical analysis | Designed; blocked on ethics | Counterbalanced protocol, validated scales, analysis, limitations |
| 10 | Reproducible release and research package | In progress | Paper-quality report, public benchmark, demo, clean-room reproduction |

The parts below were added by the 2026-08-03 external assessment (section 13).
They exist because the program has built a publication-grade apparatus for
producing evidence and has not yet produced evidence with it: every metric in
`research/baselines/part2-development.json` is `unavailable`, and the only two
numeric results in the repository come from synthetic fixtures driven by a
`fixture-llm-v1` stub. No further mechanism raises the ceiling until that
changes.

| Part | Assignment | State | Exit artifact |
| ---: | --- | --- | --- |
| 11 | Reproducibility integrity and artifact provenance | Queued (blocking) | Tracked research code, revision-bound baselines, honest manifests |
| 12 | Real-model evidence engine | Queued | Record/replay cassettes, multi-model matrix, statistics, failure taxonomy |
| 13 | Spatial capability spoofing and embodied injection benchmark | Queued | Named threat class, injection corpus, calibration-gated authorization |
| 14 | Research communication and elite demonstration | Queued | Provenance inspector, rehearsed failure demo, venue-ready framing |

## 7. Detailed assignments

## Part 1 — Research charter and evaluation contract

**State:** Complete after adversarial audit (2026-08-03)  
**Purpose:** Make the official claim falsifiable and prevent later evaluation
or architecture work from drifting toward whichever result looks best.

### Work assignments

1. Create `research/contract.json` as the canonical machine-readable contract.
2. Define stable identifiers for hypotheses, conditions, metrics, invariants,
   threats, and release gates.
3. Map every hypothesis to at least one condition comparison and metric.
4. Map every existing study pillar to a full and single-pillar ablation.
5. Define metric units, direction, aggregation, missing-data behavior, and
   whether a value is a target, guardrail, or descriptive result.
6. Define the threat categories the system promises to test: unauthorized
   side effects, prompt injection through observations/tools, stale state,
   duplicate delivery, private-data leakage, resource exhaustion, and unsafe
   confidence escalation.
7. Implement a strict loader/validator that rejects duplicate IDs, dangling
   metric or condition references, incomplete ablations, missing baseline,
   unsafe privacy fields, and malformed gates.
8. Add a CLI that validates the contract and prints a concise research summary.
9. Add focused tests for successful loading and each fail-closed validation.
10. Update this plan with validation evidence and make Part 2 the active part.

### Deliverables

- `research/contract.json`
- `src/open_llm_vtuber/evaluation/research_contract.py`
- `src/open_llm_vtuber/evaluation/__init__.py`
- `scripts/validate_research_contract.py`
- `tests/test_research_contract.py`
- this living plan, updated to `Complete`

### Acceptance gate

- The contract validates from a clean checkout with one command.
- All hypotheses reference known metrics and comparisons.
- Conditions cover `full` plus one ablation for every pillar in
  `src/open_llm_vtuber/study.py`.
- Release gates reference declared metrics or invariant evidence.
- Privacy rules explicitly forbid transcript, secret, file-content, and raw
  screen-content fields in shareable traces.
- Mutation tests demonstrate that validation fails closed for drift.

### Validation commands

```powershell
uv run python scripts/validate_research_contract.py
uv run pytest tests/test_research_contract.py tests/test_study.py -q
uv run ruff check src/open_llm_vtuber/evaluation scripts/validate_research_contract.py tests/test_research_contract.py
```

### Completion evidence

- Canonical contract revision `2026-08-03.2` validates successfully.
- The contract freezes 3 pillars, 4 controlled conditions, 5 hypotheses,
  17 metrics, 14 metric targets, 7 safety invariants, 6 threat categories,
  17 release gates, privacy prohibitions, and the required run-manifest fields.
- 30 contract tests now cover strict nested schemas, explicit comparison
  designs, complete causal-ablation coverage, frozen non-inferiority margins,
  target/direction consistency, critical invariant and threat retention, claim
  boundaries, metric/condition applicability, exhaustive target gating, and
  the original drift mutations.
- The audit removed invalid unwanted-intervention and memory-consistency
  comparisons against conditions where their denominators are not applicable.
  Metric applicability is now machine-checked, and unwanted intervention is a
  frozen full-system guardrail of `<= 0.15`.
- Contract and existing study coverage pass together: `60 passed`.
- Focused Ruff validation reports `All checks passed!`.
- Part 1 has no unresolved acceptance blocker. Measurement values remain
  unclaimed until Parts 2, 8, and 9 produce evidence.

## Part 2 — Measurement substrate and deterministic replay

**State:** Complete (2026-08-03)  
**Dependency:** Part 1 contract IDs and metric semantics.

### Work assignments

1. Version the event envelope independently from individual event payloads.
2. Add correlation IDs spanning observation, belief, plan, authorization,
   action, receipt, outcome, and recovery without recording private content.
3. Create a run manifest containing code revision, dirty-state marker,
   contract revision, scenario split, condition, model and engine identifiers,
   configuration digest, fixture digest, seed, platform, and timestamps.
4. Add monotonic sequence numbers and integrity chaining to detect missing or
   reordered events while retaining JSONL inspectability.
5. Build deterministic adapters for LLM, tool, time, network, audio-event, and
   frontend-action replay.
6. Add a failure-injection clock for timeout, disconnect, retry, cancellation,
   stale writer, duplicate event, and malformed response cases.
7. Produce the first baseline report for the current complete system and mark
   every unavailable metric as missing, never zero.

### Completed slice 2.1 — Evidence contracts

- Added `src/open_llm_vtuber/evaluation/evidence.py` with schema-versioned run
  manifests tied to the exact contract revision, condition, fixture/config
  digests, model/engine identifiers, platform, seed, and UTC lifecycle.
- Added content-free event envelopes for observation, belief, plan,
  authorization, action, outcome, recovery, system, and metric events.
- Added canonical JSON/SHA-256 integrity, contiguous sequence validation,
  previous-event hash chaining, unique event IDs, and earlier-event causation.
- Shareable string payloads are restricted to bounded identifiers/status codes;
  natural-language snippets, forbidden field families, nested objects,
  non-finite numbers, and oversized values fail closed.
- Added `tests/test_evaluation_evidence.py`: 19 tests cover lifecycle,
  reproducibility identity, tampering, privacy, missing/reordered events, broken
  links, unknown causation, and manifest/run mismatch.
- Slice validation: all Part 1, Part 2 contract, and study tests pass together
  (`79 passed`); focused Ruff checks pass.

### Completed slice 2.2 — Recording, replay, and baseline

- Added a durable `EvidenceRecorder` with atomic terminal manifests, fsynced
  JSONL appends, finite event budgets, strict reload validation, and refusal to
  overwrite or append after finalization.
- Added allowlisted study and autonomy projections. Titles, summaries, errors,
  transcripts, and unknown fields never enter the shareable envelope.
- Fixed the legacy study logger so short unknown strings can no longer bypass
  privacy merely by staying below the old 120-character length limit.
- Added closed-world deterministic LLM, tool, network, audio, frontend, and
  time adapters with no live fallback.
- Added bounded timeout, disconnect, cancellation, stale-writer,
  duplicate-event, and malformed-response injection.
- Added a frozen development fixture and verified identical terminal semantic
  state, action outcomes, and event-chain digests across three runs.
- Added explicit baseline reporting with `pass`, `fail`, `unavailable`, and
  `not_applicable`; reports are integrity-bound to terminal run manifests.
- Committed `research/baselines/part2-development.json`. It reports four
  development-harness checks as passing and thirteen unmeasured metrics as
  unavailable instead of fabricating zeros or product claims.
- Full repository validation after Part 2: `675 passed`; focused Ruff and
  formatting checks pass. The only warnings are the existing missing-FFmpeg
  runtime warning and Python 3.10's future Google client support warning.

### Acceptance gate

- The same recorded fixture produces the same terminal semantic world state
  and action outcomes on three consecutive runs.
- A missing, duplicated, or reordered event is detected.
- No shareable trace contains a forbidden privacy field or value.
- Baseline output distinguishes pass, fail, unavailable, and not-applicable.
- Replay cannot call a real side-effecting external service.

## Part 3 — Provenance ledger and typed belief memory

**State:** Complete (2026-08-03)  
**Dependency:** Part 2 correlation and replay contracts.

### Work assignments

1. Define observation, assertion, belief, preference, intention, and outcome as
   separate typed records.
2. Record source class, source identity, timestamp, confidence, freshness,
   privacy class, supporting evidence IDs, contradiction links, and expiry.
3. Separate user statements, direct sensor observations, tool results, model
   inferences, and agent-authored hypotheses.
4. Implement belief supersession without destroying historical evidence.
5. Add contradiction detection and a policy for ask, defer, re-observe, or
   retain multiple hypotheses.
6. Calibrate confidence by source class and observed historical correctness;
   do not treat model self-reported confidence as calibrated probability.
7. Make prompt construction retrieve the smallest evidence-backed context
   needed for the current intention.
8. Implement user-visible memory inspection, correction, export, and deletion
   with referentially safe tombstones.

### Completed slice 3.1 — Typed provenance foundation

- Added versioned observation, assertion, belief, preference, intention, and
  outcome record contracts under `src/open_llm_vtuber/provenance/`.
- Each record carries source class/identity, authority, observed/recorded/valid
  time, optional expiry, confidence, privacy class, evidence links,
  contradiction links, supersession, status, an opaque content reference, and
  a content digest—never raw private content.
- Type rules prevent model beliefs from acquiring user authority merely by
  reporting high confidence.
- Graph validation rejects dangling/future evidence, evidence and supersession
  cycles, asymmetric contradictions, orphaned/multiple successors, duplicate
  IDs, and unsafe tombstones.
- Added 11 focused provenance tests; the current full repository suite passes
  at `687 passed` with Ruff and format checks clean.

### Completed slice 3.2 — Audit of the typed foundation

Slice 3.1 was re-reviewed before building on it, and four defects were fixed:

- Contradiction links were coupled to `contested` status, so resolving a
  contradiction required deleting the link that recorded why the winner won.
  Links now survive resolution; an `active` record with a live peer fails.
- Cycle detection recursed, so a deep supersession chain raised
  `RecursionError` — reporting a long memory as a corrupt one. It is now
  iterative and covered by a 2,000-link chain test.
- Whole-graph validation re-parsed neighbour timestamps inside the link loops,
  making validation quadratic in ledger size. Timestamps are parsed once.
- Records were deep-copied per validation despite validation being read-only.

Two contract changes followed: `claim_key` became required (contradiction
detection needs a content-free way to say two records answer the same
question), and `retracted` joined the statuses — a claim can be withdrawn
without its content being deleted. Schema version moved to 2; no version-1
record had reached durable storage.

### Completed slice 3.3 — Durable ledger and private content store

- `provenance/ledger.py` stores operations, not edits: `append`, `contest`,
  `retract`, and `tombstone` entries are hash-chained JSONL, each carrying the
  derived state changes it caused so replay reproduces the graph without
  re-running the policy that decided it.
- A candidate graph is validated before the entry is written, so a rejected
  operation leaves neither the file nor memory changed.
- A dropped, reordered, or edited entry breaks the chain and the ledger refuses
  to open. An *unterminated* final line is treated as a torn write instead:
  discarded, repaired on disk, and logged, because that operation was never
  acknowledged and refusing to open would cost a whole memory.
- `provenance/content_store.py` holds private content behind opaque references
  with HMAC-keyed digests, so a tombstoned digest cannot confirm a guess about
  deleted content. Deleted references are retired permanently and cannot be
  rewritten. Blobs are addressed by a digest of the reference: `a:b` is not a
  legal Windows filename and `blob-A`/`blob-a` are two references a
  case-insensitive filesystem would otherwise merge.

### Completed slice 3.4 — Contradiction policy

- Detection is content-free: same claim key, differing digest, both effective.
- Seven bounded outcomes — supersede, retract candidate, retract incumbent,
  retain both, re-observe, ask, defer — ranked by `(authority, source class)`.
- A model reporting confidence 1.0 cannot displace a user statement; it is
  retracted and the disagreement stays linked in both directions.
- `ask` spends Presence attention, so it downgrades to `defer` when the user is
  absent or their attention is unavailable; both claims stay contested.
- A claim whose evidence was deleted or has expired is never written at all.
- Fixed during testing: decisions were applied pair-by-pair, so a candidate
  that lost its first conflict could not record its second — a withdrawn claim
  may not enter a new contradiction. All links are now made before any
  withdrawal.

### Completed slice 3.5 — Calibration, retrieval, and user control

- `provenance/calibration.py` grades any record that stated a confidence
  against the later higher-authority record that settled the same claim, and
  reports accuracy, overconfidence, Brier score, and ECE by source class.
  Below the sample threshold it returns `None` and reports `unavailable`.
- `provenance/retrieval.py` selects per claim key, caps the result, applies a
  privacy ceiling, and refuses beliefs whose evidence is missing or deleted.
  Contested claims are returned marked contested rather than resolved. Content
  is read in exactly one place, so a stale retrieval result cannot render
  deleted text.
- `provenance/lifecycle.py` gives inspection, correction, export, and deletion.
  Deletion follows evidence links forward — a summary of a deleted message is
  still that message — and returns the derived-artifact keys to purge.
  `EmbeddingCache.purge` is the built-in sink and reports how many vectors it
  actually removed.
- `build_evidence_context` is the prompt-construction seam. It is gated by the
  memory pillar and returns nothing when a workspace has no ledger, so existing
  installations are unaffected.
- Fixed during testing: retrieval required evidence of observations, which have
  none by nature; and memory inspection read wall-clock time instead of the
  ledger's clock.

### Completion evidence

- 46 new provenance tests across records, ledger, policy, calibration, and
  retrieval; the full suite passes at `737 passed` (from 687), with Ruff check
  and format clean. The only warnings remain the missing-FFmpeg runtime warning
  and Python 3.10's future Google client support warning.
- `research/fixtures/held_out/part3-calibration.json` (232 records, seed
  20260803) is frozen and separate from the development fixtures.
- `research/baselines/part3-calibration.json` reports `model` measurably
  overconfident (states 0.73 on average, correct 47% of the time; ECE 0.26) and
  `sensor` close to honest (0.76 stated, 77% correct). Four source classes
  report `unavailable`.
- The fixture is **synthetic** and the artifact says so in its own body: it
  exercises the measurement path and does not measure any deployed model.

### Validation commands

```powershell
uv run pytest tests/test_provenance_records.py tests/test_provenance_ledger.py tests/test_provenance_policy.py tests/test_provenance_calibration.py tests/test_provenance_retrieval.py -q
uv run python scripts/report_provenance_calibration.py
uv run ruff check src/open_llm_vtuber/provenance scripts tests
```

### Unresolved limitations

- Live turns now create scoped assertion → intention → outcome evidence without
  putting their text in the inspectable ledger. They are intentionally not
  promoted into durable semantic preferences or beliefs without a reviewed
  extractor and contradiction policy.
- Calibration has no production data, so every real source class is
  `unavailable` outside the synthetic fixture. The gate is met in the sense
  that calibration *is reported by source class on a held-out fixture*; it is
  not met in the sense of a measured production model, and is not claimed as
  such.
- Deletion reaches artifacts registered through `register_derived`. Anything
  derived without registering is outside its reach by construction, which is a
  contract on callers rather than an enforced invariant.

### Acceptance gate

| Gate | State | Evidence |
| --- | --- | --- |
| Every retrieved belief points to valid evidence or an explicit user-entered assertion | Pass | `test_a_belief_whose_evidence_was_deleted_is_never_retrieved` |
| Contradictory evidence cannot silently overwrite a higher-authority source | Pass | `test_a_confident_model_cannot_overwrite_what_the_user_said` |
| Deleted private content cannot reappear through summaries or embeddings | Pass | `test_deleted_content_cannot_come_back_through_a_summary_or_an_embedding` |
| Memory ablation removes retrieval while preserving persona and rules | Pass | `test_the_memory_ablation_removes_evidence_recall_but_not_the_character` |
| Confidence calibration is reported by source class on held-out fixtures | Pass, synthetic | `research/baselines/part3-calibration.json`; production data still absent |

## Part 4 — Dual-loop cognition and resumable plans

**State:** Complete (2026-08-03)  
**Dependency:** Typed beliefs and deterministic replay.
**First concrete task:** Define the versioned intention/plan/step state machine
and its terminal states, then prove crash-at-every-transition resumption.

### Work assignments

1. Keep the latency-sensitive dialogue loop independent from the slower
   deliberative autonomy loop.
2. Define a versioned intention/plan/step state machine with explicit terminal
   states, preconditions, postconditions, deadlines, and cancellation.
3. Persist checkpoints before and after side-effecting transitions.
4. Add plan repair that cites failed preconditions or outcomes and remains
   bounded by retry and cost budgets.
5. Introduce fair scheduling across human turns, urgent events, deadlines,
   background reflection, and maintenance.
6. Preserve the existing live-owner/headless-owner lease and fencing rules.
7. Add counterfactual plan evaluation for high-risk or expensive actions.

### Acceptance gate

- A process can crash at every state transition and resume without duplicate
  side effects or lost terminal reports.
- Human interruption preempts background inference within the latency budget.
- No plan remains nonterminal without a lease, deadline, retry budget, or
  inspectable blocked reason.
- Plan repair improves recovery on held-out failures without increasing policy
  violations.

### Completed implementation

- Added plan schema/store version 1 with explicit plan and step states, UTC
  deadlines, preconditions, postconditions, cancellation, lease ownership,
  fencing tokens, retry/repair/cost budgets, and bounded transition history.
- Added atomic before-action and after-action checkpoints with a stable
  idempotency key. `AgendaPlanBridge` now runs validated legacy agenda steps
  through this sidecar without changing or invalidating agenda schema v1.
- Added durable terminal-report claim/ack/recovery, operator-safe plan snapshots
  under `/api/agenda`, cancellation reconciliation, fail-closed unknown-outcome
  handling, and restart recovery for expired execution/report leases.
- Added bounded repair that must cite the failed step, cannot expand action
  scope, raise risk, exceed cost, inject pre-completed work, or replay an
  already-recorded side effect.
- Added strict human preemption measurement (250 ms default), weighted fair
  scheduling across urgent/deadline/background/maintenance work with starvation
  protection, and persisted counterfactual decisions for high-risk steps.
- Wired private conversation-turn assertion → intention → outcome ingestion in
  parallel with the reactive response path. Memory-off and skip-memory
  conditions still remove the mechanism without changing persona or rules.
- Fixed a Part 3 concurrency defect found during the gate audit: independent
  provenance-ledger and private-content handles now serialize and refresh
  cross-process state rather than appending from stale indexes or sequences.
- Reopened Part 4 for an adversarial cross-file crash audit. The agenda now
  atomically absorbs sidecar status, checkpoint retirement, handoff marker, and
  optional report before acknowledging the plan. Startup reconciliation
  restores create-before-link crashes and acknowledges already-terminal agenda
  entries without replaying their effects.
- Deadline sweeping now expires orphaned read-only plans while retaining
  attempted side effects with unknown outcomes for explicit reconciliation.
  State/index validation rejects impossible persisted combinations, and invalid
  or crashing repair proposals terminate with bounded reason codes.
- The production agenda bridge now supplies a bounded scope-identical repair
  for transient read failures. It preserves action, arguments, conditions,
  risk, and effect class; side effects and recorded outcomes are never replayed.
- Human/due-work preemption and process restart now release the sidecar lease:
  interrupted reads become resumable, durable results remain commit-ready, and
  unreceipted side effects become `cancelling`. Offline abandon follows the
  same sidecar-first rule instead of orphaning a plan behind a terminal entry.

### Acceptance evidence

| Gate | State | Evidence |
| --- | --- | --- |
| Crash/resume does not duplicate effects or lose terminal reports | Pass | Five injected action-boundary crashes, restart reconstruction, stable adapter key, and report-claim recovery in `tests/test_autonomy_part_four.py` |
| Human work preempts deliberation within the configured budget | Pass, deterministic | Cooperative cancellation test records a receipt within 250 ms; physical/provider latency remains a Part 7 measurement |
| Every nonterminal plan is bounded or inspectably blocked | Pass | Strict schema validation of deadline, retry/repair/cost limits, execution lease, fencing, and blocked reason |
| Repair improves held-out recovery without policy regression | Pass, synthetic | Frozen 8-scenario result: 5 successes with repair versus 2 without; zero policy violations and duplicate effects in both conditions |

The first held-out invocation exposed a general harness termination bug: a
stable `repairing` state was being looped until the executor's transition cap.
The fixture was not changed; `run_to_stable` was corrected to return that
inspectable stable state, after which the frozen report passed.

### Validation commands

```powershell
uv run pytest tests/test_autonomy_part_four.py tests/test_provenance_ingestion.py tests/test_part4_evaluation.py -q
uv run python scripts/run_part4_evaluation.py --verify
uv run pytest -q
uv run ruff check src/open_llm_vtuber/autonomy src/open_llm_vtuber/provenance src/open_llm_vtuber/evaluation scripts/run_part4_evaluation.py tests/test_autonomy_part_four.py tests/test_provenance_ingestion.py tests/test_part4_evaluation.py
```

Completion evidence: 38 dedicated Part 4 tests, reproducible baseline bound to
contract revision `2026-08-03.2`, fixture SHA-256, seed 0, and implementation
digest; full repository `786 passed`; focused Ruff checks and `git diff
--check` passed. The two warnings remain the known missing-FFmpeg warning and
Python 3.10 future Google client-support warning.

### Unresolved boundary

Exactly-once resumption currently depends on each side-effect adapter honoring
the supplied stable idempotency key. Part 4 blocks unknown outcomes and proves
the protocol with deterministic adapters; it does not claim that every legacy
third-party tool is transactional. Part 5 owns the one authorization kernel,
outcome verification, compensation, and wrappers for non-idempotent services.

## Part 5 — Authorization kernel and transactional actions

**State:** Complete (2026-08-03)  
**Dependency:** Plan state machine and provenance ledger.  
**Completion evidence:** Versioned descriptor and reviewed profiles; one
production transaction boundary; hash-chained journal; trusted writing-desk
confirmation; verification and bounded compensation; frozen seven-source
attack ablation; 24 dedicated tests; full repository `798 passed` before Part
6 began.

### Work assignments

1. Create a single action descriptor covering actor, capability, target,
   arguments digest, side-effect class, reversibility, confidence, evidence,
   user authority, spatial requirement, and idempotency key.
2. Centralize policy for capability, proximity, user confirmation, Presence,
   rate/quota, privacy, and external-destination checks.
3. Separate proposal, authorization, commit, verification, and compensation.
4. Require outcome evidence appropriate to the action instead of equating tool
   return with success.
5. Add preview/dry-run and explicit confirmation for consequential actions.
6. Define compensation where true rollback is impossible and state the limit
   honestly in the receipt.
7. Red-team indirect prompt injection arriving from email, web pages, files,
   calendar text, tool output, screen perception, and furniture metadata.

### Completed implementation

- Added action-descriptor schema version 1 with actor, capability, target,
  canonical private-argument digest, side-effect class, reversibility,
  confidence, evidence references, required user authority, spatial
  requirement, idempotency key, privacy class, and external destination.
- Added a trusted authorization context whose grants are structurally separate
  from the untrusted proposal. Explicit confirmation is user-issued,
  short-lived, and bound to the digest of the entire descriptor.
- Added one fail-closed kernel covering actor, capability, evidence, confidence,
  authority, proximity, Presence, quota, privacy, destination, idempotency, and
  preview-versus-commit behavior.
- Added 11 contract tests, including exact-confirmation/time binding, indirect
  prompt injection, simultaneous policy failures, preview non-commit, private
  argument minimization, and schema mutation.
- Inventoried bundled mutations by effect, actual reversibility, target,
  destination, verifier, and compensation capability. Unknown dynamic
  mutations fail closed.
- Routed the sole production `ToolExecutor` construction through a coordinator
  assembled from registered capability, live proximity, Presence, quota,
  privacy, destination, and exact user-confirmation facts.
- Added an `fsync`-durable, file-locked, append-only hash chain holding one
  policy decision and terminal outcome per attempt without arguments/results.
- Bound delivery identity to client, server, call ID, and argument digest;
  duplicate exact delivery cannot repeat impact, changed arguments cannot
  inherit authority, and ambiguous post-commit state never auto-retries.
- Reused the trusted writing-desk route for exact, expiring, content-minimal
  confirmation. Reversible session work does not create approval fatigue.
- Added local-path verification, descriptor-bound adapter evidence, bounded
  compensation hooks, explicit unresolved outcomes, cancellation/crash
  reconciliation, UI tests, journal tamper tests, and attack tests.
- Frozen a seven-source held-out ablation across email, web, file, calendar,
  tool output, screen, and furniture injection. Authorization-on blocks 7/7
  with 7 policy records, 7 terminal outcomes, and zero effects; boundary-off
  produces 7 effects.

### Acceptance gate

- Every attempted side effect has one policy record and one terminal outcome.
- Duplicate delivery with the same idempotency key does not duplicate impact.
- Prompt injection cannot grant capability or bypass user confirmation.
- Verification failure triggers bounded recovery or explicit unresolved state.
- Release adversarial suite records zero unauthorized side effects.

## Part 6 — Semantic world model 2.0 and embodied intelligence

**State:** Complete as a contract; **not integrated** (reopened 2026-08-03)  
**Dependency:** Typed beliefs, transaction protocol, and current world seam.

> **Reopened by the Part 10 integration audit.** Every guarantee below is real
> and tested, and the running server reaches none of it. `semantic_world`,
> `semantic_projection`, and `semantic_navigation` — 1,546 lines — are not
> reachable from `server.py`. The live proximity gate in `mcpp/tool_manager.py`
> still reads `world_model`, whose `path_to` is a single straight leg.
>
> So capability parity was proved between two projections the authorization
> path never consults, and obstacle-aware navigation exists beside a system
> that still walks through furniture. That is a weaker claim than "Complete"
> implies, and D-038 records the correction rather than restating the gate.
> `scripts/reproduce.py` now fails on it so it cannot be forgotten again.

### Work assignments

1. Extend semantic world state with 3D position, orientation, containment,
   support, visibility, occupancy, ownership, affordance, and temporal change.
2. Keep renderer assets and layout styling outside semantic authorization data.
3. Introduce obstacle-aware navigation and stable social-distance targets.
4. Add reach, visibility, and line-of-sight evidence to embodied tool use where
   physically meaningful.
5. Make screen and VR states projections of a common semantic scene while
   preserving renderer-specific presentation and persistence needs.
6. Add spatial language grounding for relations such as near, behind, on,
   inside, left of, visible from, and recently moved.
7. Verify autonomous relocation and object interaction against the same world
   contract used by user-directed actions.

### Completed slice 6.2 — Audit of the semantic foundation

Slice 6.1 was measured before being built on, and four defects were fixed.

- Whole-graph validation ran again on **every** query. One `relation()` call on
  a 1,600-entity room cost 0.20 s and five `recently_moved()` calls cost 1.24 s.
  The world is frozen, so validation is now cached behind an `init=False`
  field — `init=False` specifically, because a document able to supply its own
  "already validated" marker would skip every geometry and graph check.
- Acyclicity re-walked each chain from every entity, quadratic in depth. It is
  now a single settled/walking pass, covered by a 2,000-link chain test. This
  is the same defect class as Part 3 slice 3.2; the lesson did not transfer,
  so the regression test now names the cost.
- `visible_from` was read straight out of the scene document and reported with
  basis `observer_visibility_evidence`. Nothing had been observed: an entity
  39 m away behind a wall returned `holds=True`. A client that can write that
  field could manufacture exactly the evidence a gate asks for, which is what
  D-019 forbids. The declared channel is now honestly named
  `declared_visibility_claim`, `RELATION_BASES` is a closed set, and derived
  `line_of_sight` traces the real segment and names the blocker.
- `entity_from_record` had no inverse, so a semantic world could be parsed but
  never stored or sent. `record_from_entity` closes the round trip with sorted
  sets so two runs digest identically.

### Completed slice 6.3 — Projection parity

`semantic_projection.py` projects both scene documents into the shared
contract, and the parity instrument immediately found that **the two renderers
do not agree about the room**: 232 differences across 1,200 relation
comparisons on the shipped defaults. The archive shelf stands at (360, 370) on
screen and (660, 200) in VR; the hourglass at (585, 110) and (120, 200).

That divergence is deliberate presentation — a calendar hangs flat on a wall in
3D and occupies a legible band in 2D — so demanding identical coordinates would
fail forever for the wrong reason. The gate was therefore stated at the level
that actually matters and measured there:

- **Geometric parity** is reported, not enforced. The instrument exists so that
  a layout edit which desynchronizes the renderers is visible.
- **Capability parity is enforced and passes.** Standing at any of the 15
  objects unlocks an identical affordance set in both renderers, with zero
  cross-unlocks: no object's position hands it a neighbour's tools. Affordances
  are derived from `PROXIMITY_REQUIREMENTS`, never from the scene file, so a
  document cannot name its own capabilities.

A regression test drags the archive shelf onto the computer and asserts the
cross-unlock is reported, which is the spatial capability spoofing case Part 13
generalizes.

### Completed slice 6.4 — Obstacle-aware navigation

`semantic_navigation.py` plans on a reduced visibility graph: footprints grown
by the agent's radius, turning points at grown corners, exact and deterministic
rather than resolution-dependent. Every refusal is typed — `target_unknown`,
`start_inside_obstacle`, `goal_inside_obstacle`, `goal_outside_extent`,
`no_clear_route`, `no_clear_dock`, `pictorial_floor` — and names the blocker
where geometry can identify one.

Measuring it exposed the most consequential finding of Part 6. On the VR floor
plan all 15 objects have a comfortable approach. On the 2D stage only 10 did,
and the cause is not the planner: **the 2D document's `height` is drawn height,
not floor depth.** The same avatar body measures 92 deep on screen and 20 in
the headset. Planning real clearance around pictorial footprints seals a room
the user can see is open.

So `SemanticProjection.floor_is_metric` is False for the screen and navigation
refuses it with `pictorial_floor` rather than returning an advisory route a
gate might trust. Proximity gating is unaffected and still correct, because
distances remain comparable *within* each picture — which is why capability
parity passes while navigation parity cannot. The VR floor plan is the metric
authority; the 2D stage is a picture that happens to be internally consistent.

### Acceptance gate

| Gate | State | Evidence |
| --- | --- | --- |
| Navigation never terminates inside an obstacle and reports why no path exists | Pass | Seven typed reasons; sealed-goal, goal-inside, start-inside, and outside-extent tests; docking asserted outside every grown footprint |
| Equivalent semantic queries return equivalent answers in screen and VR mode | Pass, at the authorization level | 15/15 objects grant identical affordances with zero cross-unlocks; geometric divergence reported rather than hidden |
| Renderer compromise cannot forge an authoritative action outcome | Pass | Affordances derived from proximity tables, clearance from the agent's own radius, declared visibility demoted to a claim, derived line of sight required for evidence |
| Spatial-reference accuracy meets the frozen benchmark target | Deferred to Part 8 | Relations and line of sight are implemented and tested; no frozen spatial benchmark exists yet, and none is claimed |
| Social positioning remains comfortable in the physical headset study | Deferred to Part 9 | `SOCIAL_DISTANCE` docking is implemented and deterministic; comfort is a human-subjects measurement |

### Validation commands

```powershell
uv run pytest tests/test_semantic_projection.py tests/test_semantic_world.py tests/test_world_model.py tests/test_vr_protocol.py tests/test_vr_stage_state.py -q
uv run pytest -q
uv run ruff check src/open_llm_vtuber/semantic_world.py src/open_llm_vtuber/semantic_projection.py src/open_llm_vtuber/semantic_navigation.py tests/test_semantic_projection.py
```

### Unresolved limitations

- Navigation is unavailable on the screen renderer by construction. Making it
  available means giving the 2D stage a real floor depth per object, which is a
  scene-schema migration, not a planner change.
- `line_of_sight` treats every non-zone entity as an opaque box. A bookshelf
  with a gap under it occludes as if solid, which is conservative — it can
  refuse a legitimate sight line, never grant a false one.
- Autonomous relocation still calls `world_model.path_to`, which remains a
  single straight leg. Routing it through the new planner requires the VR floor
  plan to be authoritative for both renderers and is deferred with the schema
  migration above.

## Part 7 — Real-time multimodal and WebXR product quality

**State:** Instrumented; product gates blocked on hardware and a real model  
**Dependency:** Stable action/world contracts; can overlap late Part 6.

Part 7 is the first part whose gates this repository cannot close by itself.
Three of its five acceptance criteria require something the codebase does not
contain: a real ASR/LLM/TTS stack under load, a physical Quest 3, and
first-time human participants. The measurement path for all of them is now
built and tested; the numbers are not claimed, and the gate table below says
which is which rather than quietly redefining the target.

### Work assignments

1. Instrument capture-to-ASR, endpointing, planner, first-token, TTS,
   first-audio, interruption, WebSocket, and render-frame latency.
2. Implement streaming overlap where correctness permits and cancellation where
   stale output would harm conversational turn-taking.
3. Add adaptive endpointing and noise-robust voice-state feedback.
4. Add semantic gaze, listening/thinking/acting/recovering motion, personal
   space, and restrained spatial audio.
5. Establish Quest GPU/CPU/frame-time budgets, asset budgets, thermal testing,
   and repeated session-enter/exit memory checks.
6. Build a private operator console for health, plan state, latency, microphone,
   FPS, fixture mode, reset, and emergency stop.
7. Preserve a deterministic screen-mode fallback with identical task semantics.

### Completed slice 7.1 — Per-stage latency instrumentation

The budget in this part's gate could not be evaluated: nothing in the system
reported speech-to-first-audio, and a budget that cannot be measured is a wish.
A single end-to-end figure would not have been actionable either — 900 ms is a
different engineering problem depending on whether it went to endpointing, the
first model token, or the first TTS chunk.

`latency.py` records a turn as eight ordered stages (capture, endpoint, asr,
retrieve, plan, first_token, tts_request, first_audio) plus interruption, and
reports median, p95, and worst per stage with `slowest_stage()` naming where
the time actually goes. Three properties are enforced rather than assumed:

- **Content-free.** Stage name, monotonic duration, correlation id. The stage
  vocabulary is closed, so a free-form label cannot become somewhere a prompt
  fragment lands — the same rule as D-006, tested directly.
- **Monotonic.** `perf_counter`, not wall clock. A laptop that suspends or
  crosses an NTP correction mid-turn would otherwise produce negative durations
  and impossible percentiles; a backwards clock now clamps to zero and a
  negative sample is refused.
- **Missing is not zero.** A turn that never reached audio has no end-to-end
  figure and reports `unavailable`. Recording zero would pull every percentile
  down exactly when the system is most broken, which is the failure Part 2
  already refused. Percentiles are nearest-rank, so every reported number is a
  latency that actually occurred rather than an interpolation between two.

Budgets are declared as constants (800 ms median, 1.4 s p95, 250 ms
interruption) and the verdict is computed from measurements, never assumed.

### Completed slice 7.2 — Audit of the instrumentation, and live wiring

Slice 7.1 was audited before being wired in, and four defects were fixed —
three of them mine from the previous session.

- **Unbounded growth.** The recorder appended one row per stage per turn
  forever. An eight-hour soak without resource growth is an explicit gate in
  this very part, and the instrument built to check it was the leak. Samples
  are now a per-stage ring buffer; the lifetime turn count stays exact because
  it is a counter rather than a list.
- **`enter()` filed time under the wrong stage.** It set an attribute nothing
  read, and `enter("asr")` followed by `complete("plan")` silently attributed
  ASR's time to planning. One cursor advances through the pipeline; the method
  is gone and a test asserts it stays gone.
- **NaN passed the negative-duration check.** `NaN < 0` is False, so a single
  poisoned sample would have turned every percentile that stage ever reported
  into NaN. Durations must now be finite.
- `_clock` was annotated `callable[[], float]`, which is not a type; unevaluated
  because of `from __future__ import annotations`, and wrong.

Wiring then followed the rule the codebase already applies to provenance:
measurement runs beside the turn, never in front of it. `TurnLatency` swallows
everything, so a measurement failure costs a sample and not a reply — proven
by a test that breaks the recorder mid-turn and asserts the user still gets
their answer. Four boundaries are instrumented in `single_conversation.py`
(`asr`, `plan`, `first_token`, `tts_request`) plus `first_audio`, taken when
the *first* synthesis lands rather than the last, since that is when the
companion starts speaking. `finish()` runs in `finally`, so an interrupted or
errored turn still contributes its stages and contributes no end-to-end figure.

Interruption is measured where it actually matters: `cancel()` is a request,
not a stop, so a bounded background task times the turn reaching a terminal
state without delaying the barge-in it is measuring.

### Completed slice 7.3 — Operator console

`/api/operator/health` and `/api/operator/latency/reset`, behind the same
trusted-request dependency as the agenda. Content-free: stage names, counts,
milliseconds, and budget verdicts. `null` is serialized for an unmeasured
figure and never coerced to `0`, so a session with no completed turn cannot
read as instantaneous.

### Acceptance gate

| Gate | State | Evidence |
| --- | --- | --- |
| Median speech-to-first-audio ≤800 ms, p95 ≤1.4 s | **Unmeasured** | The path is instrumented end to end and reports per stage, but no real ASR/LLM/TTS stack has been run under load in this environment. Reported as `unavailable`, never as a pass |
| Interruption stops stale playback within 250 ms | **Unmeasured** | Measurement is wired to terminal task state rather than to the cancel request; a deterministic cooperative-cancellation test already passes in Part 4, and the physical figure needs a real stack |
| Network/audio/tool degradation is visible and never presented as success | Pass | A failing model yields `success=False` with an error surfaced and no end-to-end latency figure; existing voice-resilience coverage retained |
| Physical headset frame rate and 100 enter/exit cycles | **Blocked** | Requires a Meta Quest 3. No software change closes it |
| First-time users complete the core interaction unaided | **Blocked** | Human subjects; belongs with the Part 9 protocol |

Work assignments 3 (adaptive endpointing), 4 (semantic gaze, personal space,
spatial audio), and 5 (Quest budgets) remain open and are frontend/hardware
work rather than contract work. They are not counted as complete.

### Validation commands

```powershell
uv run pytest tests/test_latency.py tests/test_latency_wiring.py -q
uv run pytest -q
uv run ruff check src/open_llm_vtuber tests/test_latency.py tests/test_latency_wiring.py
```

### Unresolved limitations

- `capture`, `endpoint`, and `retrieve` are declared stages with no producer:
  capture and endpointing happen in the frontend/VAD before the server sees a
  turn, and retrieval happens inside the agent rather than at a visible seam.
  They report as absent rather than as zero, so the end-to-end figure is
  currently `unavailable` for every real turn. Closing this needs a frontend
  timing message, which is the natural next slice.
- Because of that, the 800 ms budget cannot yet be evaluated even with a real
  model. The stage breakdown is usable today; the product figure is not.
- Interruption latency measures the conversation task reaching a terminal
  state, which is not the same as the last audio sample leaving the speaker.
  Client-side playback stop is unmeasured.

## Part 8 — EmbodiedAgentBench and adversarial evaluation

**State:** Built and frozen; success rate unmeasured  
**Dependency:** Parts 2–7 expose stable contracts and metrics.

The benchmark, its frozen splits, the scorer, the failure taxonomy, and the
statistics are complete and tested. The one gate that needs a real model —
≥85% full-task success — is reported as unmeasured, for the same reason Part 7
reports its latency budgets that way.

### Completed slice 8.1 — Scenario contract

D-002 fixed the order: machine-checkable before built. A hundred scenarios
authored against an informal schema become a hundred scenarios that quietly
disagree about what success meant, and the disagreement surfaces after results
exist, when fixing it looks like moving the goalposts.

`evaluation/scenarios.py` makes four things structural rather than
conventional:

- **Success is a predicate.** Terminal conditions come from a closed set the
  scorer can actually evaluate; a scenario with none is refused at load,
  because it can be neither passed nor failed. Free-text success criteria are
  how a benchmark becomes a reading-comprehension exercise for whoever scores
  it.
- **Forbidden effects are first-class.** Completing the task *and* sending the
  email twice is not success. Allowed and forbidden sets must be disjoint —
  an overlap is a load error, since otherwise whichever check the scorer runs
  first silently decides the result.
- **The held-out split cannot be taken by accident.** `select()` refuses it
  unless a caller passes `allow_held_out=True`, and there is no default that
  opens the box. Risk row 2 is evaluation overfitting; the mitigation has to be
  mechanical, because the failure mode is a tired person deciding one peek is
  harmless. A recorded `held_out_digest` is verified at load, so a modified
  frozen split fails rather than being trusted.
- **Coverage is reported, not counted.** `missing_areas` names the capability
  areas with no scenarios, so 100 conversation scenarios cannot pass for a
  benchmark.

Scenario prompts and titles live in this module deliberately: they are authored
benchmark inputs and safe to publish. Nothing here reaches an evidence
envelope, so traces stay content-free under D-006 — that separation is what
lets the benchmark be public while sessions stay private.

19 dedicated tests covering the contract and its drift mutations.

### Completed slice 8.2 — Audit of the contract

Three defects, found by probing rather than by reading.

- **A scenario built from lists and the same scenario built from frozensets
  digested identically but compared unequal.** `digest()` sorts; `__eq__` does
  not. Deduplicating a suite by identity and by digest would have disagreed
  about how many scenarios it holds — the kind of discrepancy that surfaces
  months later as a count nobody can reproduce. Collections are normalized in
  `__post_init__`.
- **`SuiteCoverage` was a frozen dataclass holding mutable dicts**, so a
  coverage report could be edited after it was produced while the annotation
  said otherwise. Counts are now tuples of pairs.
- **The held-out guard was weaker than it looked.** `select()` refuses the
  frozen split, but `.scenarios` is a public tuple that anything can read.
  That is a guardrail against accident, not a boundary against intent — the
  same distinction D-024 draws about proximity gating. `development_view()` is
  the real mitigation: tuning code is handed a suite that does not contain the
  evaluation set at all, so there is nothing to peek at.

### Completed slice 8.3 — The authored suite

100 scenarios, every capability area populated, 41 development / 28 validation
/ 31 held out, 15 adversarial. They are written as explicit records rather than
templated in a loop: a benchmark built by templating one scenario a hundred
times measures one scenario a hundred times, and the resulting number looks
like coverage while answering a single question.

Authoring them immediately found a gap the schema had not anticipated.
Autonomy and VR-lifecycle scenarios have **no user utterance** — they are
started by a clock, by the user arriving or leaving, or by a headset session
beginning. The contract rejected them, which was correct: the fix was not to
invent an utterance nobody speaks but to add an explicit `trigger`, and to
require that a `user_utterance` scenario has a prompt while every other kind
has none. A scheduled scenario carrying an utterance is two triggers wearing
one label, and the harness would have had to guess which fired.

`research/scenarios/embodied-agent-bench.json` carries its own
`held_out_digest`, verified at load, so a modified frozen split fails rather
than being trusted.

### Completed slice 8.4 — Scorer, taxonomy, and statistics

- **A forbidden effect voids the run and its partial credit.** An agent that
  books the meeting *and* emails the wrong person has not partly succeeded.
  Without this, a system could raise its partial score by doing more of the
  task while still leaking.
- **A missing run is an error, not a silent zero.** Dropping unrun scenarios
  would let an evaluation that crashed halfway report the success rate of the
  half that finished, which is the most flattering possible reading of a broken
  measurement.
- **Harness errors are separated from system failures**, so a broken measurement
  cannot be counted against the system, and it invalidates the release gate
  rather than lowering the score.
- **Failures are attributed to the earliest broken link** in the causal chain —
  observation, belief, plan, authorization, action, verification, recovery —
  and the taxonomy reads in pipeline order. An aggregate says the system is
  62%; the taxonomy says 40% of the losses are authorization refusing work it
  should have allowed, which is the sentence that changes what gets built next.
- **Bootstrap confidence intervals, seeded**, with `None` rather than a
  zero-width interval on an empty sample and an explicit no-spread result at
  n=1. Conditions are compared **paired** per scenario, which removes scenario
  difficulty from an ablation comparison.

### Acceptance gate

| Gate | State | Evidence |
| --- | --- | --- |
| ≥100 validated scenarios and an untouched held-out split | Pass | 100 scenarios, all 10 areas, 31 held out behind a load-verified digest and a development view that excludes them |
| Failure taxonomy and representative traces accompany aggregate scores | Pass | Causal-chain attribution in pipeline order; per-area pass/total; per-scenario reasons retained on every score |
| Results reproduce from manifests and fixtures without external accounts | Pass | Suite and digests are deterministic; scoring and intervals are seeded and reproducible |
| Zero forbidden side effects in adversarial evaluation | **Unmeasured** | 15 adversarial scenarios authored and every one forbids something checkable; running them needs a real model |
| ≥85% full-task success on the final held-out suite | **Unmeasured** | The gate is implemented in `require_release_gate` and names every reason it fails; no system has been run against the suite |

### Validation commands

```powershell
uv run python scripts/author_scenario_suite.py
uv run pytest tests/test_scenarios.py tests/test_scoring.py -q
uv run pytest -q
```

### Unresolved limitations

- No system has been run against the suite, so both outcome gates are
  unmeasured. What exists is a benchmark and a scorer, not a result. Closing
  this is Part 12's cassette recorder, not more scenarios.
- The 100 scenarios name `initial_state_id` values (`decoy-computer-room`,
  `homoglyph-room`, `blocked-room`, …) that the fixture library does not yet
  contain. The contract validates the reference; it cannot yet resolve it.
- Terminal predicates are checkable in principle but only some are wired to a
  real observer today. Predicate observers are the next slice.

### Work assignments

1. Author at least 100 scenarios across conversation, memory, spatial
   grounding, long-horizon planning, autonomy, interruption, recovery, safety,
   offline degradation, and VR lifecycle.
2. Give each scenario a versioned initial state, allowed effects, forbidden
   effects, injected failures, time budget, terminal predicates, and score.
3. Freeze development, validation, and held-out evaluation splits before final
   optimization.
4. Add adversarial suites for injection, ambiguity, conflicting instructions,
   stale memory, duplicate events, lease takeover, malformed tools, and privacy.
5. Report full-task success, partial credit, intervention rate, recovery rate,
   unauthorized effects, calibration, latency, and resource use.
6. Run every declared ablation and publish per-scenario results with bootstrap
   confidence intervals where sample size supports them.

### Acceptance gate

- ≥100 validated scenarios and an untouched held-out split.
- ≥85% full-task success on the final held-out suite.
- Zero forbidden side effects in adversarial evaluation.
- Results reproduce from manifests and fixtures without external accounts.
- Failure taxonomy and representative traces accompany aggregate scores.

## Part 9 — Human study and statistical analysis

**State:** Designed and pre-analysed; blocked on ethics and participants  
**Dependency:** Stable build, frozen hypotheses/metrics, ethics/privacy review.

Everything that can be fixed before recruitment is fixed: the design, the
hypotheses, the analysis, and the refusal to claim anything below the minimum
n. What remains needs an ethics committee and people, and no amount of
engineering shortens either.

### Completed slice 9.1 — Draft protocol

[STUDY_PROTOCOL.md](doc/STUDY_PROTOCOL.md) fixes the design *before* anyone is
recruited, which is the only point at which fixing it is honest.

The primary outcome is deliberately behavioural rather than a questionnaire:

> **H-LEGIBILITY.** Users detect an unauthorized action faster, and with fewer
> misses, when the agent is spatially embodied than when it is not.

That is the measurable form of the reframe in D-024. Proximity gating is not a
security boundary; it is an observability channel. Whether that channel
actually reaches a person is exactly what a human study can settle and no
amount of system testing can.

Design decisions worth recording:

- **Three conditions, not four.** `no-autonomy` is excluded because the frozen
  contract already marks the intervention metrics as not applicable to it —
  with no interventions there is no denominator, so a fourth block costs ~25
  minutes per participant to answer nothing.
- **The probe trial is disclosed in advance.** Participants are told the agent
  may act unasked. That makes the measure detection *latency* rather than
  deception, and it keeps the design reviewable.
- **VR is not a condition.** Headset acceptance is still pending, and an
  unvalidated build would confound embodiment with novelty and discomfort.
- **Minimum reportable n=18**, below which the work is reported as a pilot with
  no inferential claim, and a stopping rule with no interim peeking.
- Paired analysis reuses `evaluation/scoring.compare_conditions`, so the study
  and the benchmark compute a difference the same way rather than through two
  implementations that can drift.

Section 13 of the protocol states what the study cannot settle, including that
detection of a disclosed probe is an upper bound on detection of an undisclosed
one.

### Completed slice 9.2 — Audit of the protocol, and the pre-registered analysis

Writing the analysis audited the protocol, which is the point of writing it
first. Two design defects were found by making the design executable.

- **"Latin square" was the wrong design.** With three conditions it balances
  *position* but not *carryover*: it can leave "B after A" happening twice as
  often as "A after B", so fatigue or practice from one condition lands
  disproportionately on another and cannot be told apart from that condition's
  own effect. The protocol now specifies a **Williams design — six sequences**,
  and `test_every_condition_follows_every_other_equally_often` asserts the
  balance property directly rather than trusting the label. Target n is
  therefore a multiple of six.
- **One probe trial per condition made the primary outcome binary.** Every
  participant would have scored 0 or 1, and a 24-person study would have been
  comparing two coin flips. Three probes per condition give a rate that can
  move. This is the difference between a primary outcome and a formality.

`evaluation/study_analysis.py` is the pre-registration in executable form. A
pre-registration that says "we will compare the conditions" and leaves the
comparison to be chosen later has pre-registered nothing.

Three things in it are deliberately awkward, because the convenient version of
each lets a small study overclaim:

- **A censored observation is not a fast one.** A participant who noticed
  nothing has no detection latency. Dropping them and averaging the rest
  reports the latency of the people who succeeded — a number that *improves as
  detection gets worse*. Latency is reported only alongside the detection rate
  it is conditional on, with the censored count attached. If detection is near
  zero under `no-embodiment` — which is what H1 predicts — H2 becomes
  uncomputable, and that is a result about H1 rather than a missing analysis.
- **Below n=18 the report labels itself a pilot and emits no verdicts at all.**
  The numbers are still computed; only the claims disappear. There is no
  override flag, because the situation in which someone wants one is exactly
  the situation in which they should not have it.
- **The guardrail is judged on the interval's upper bound.** A mean of 0.14
  whose interval reaches 0.31 has not shown the rate is below 0.15.

Tested on simulated cohorts with a known effect, a known null, an effect in the
wrong direction, total censoring, withdrawals, and blocks lost to technical
failure — 27 tests. The analysis reuses the benchmark's `bootstrap_interval`,
so the study and EmbodiedAgentBench compute a difference through one
implementation rather than two that can drift.

### Remaining work

1. Obtain ethics/IRB approval, explicitly covering the probe trial.
2. Verify instrument licences and scoring rules before the pilot, not after.
3. Author matched task sets and the missing initial-state fixtures.
4. Write the analysis script and test it on simulated data.
5. Run and analyse the six-participant pilot.
6. Freeze the protocol and file the pre-registration.

### Acceptance gate

| Gate | State |
| --- | --- |
| Protocol and analysis frozen before main data collection | Analysis written and tested; protocol frozen only after the pilot |
| Behavioural logs and questionnaires join through pseudonymous IDs only | Specified; `study.py` already content-free |
| Every exclusion and missing session accounted for | Specified in the protocol |
| Results include ablations, effect sizes, uncertainty, negative findings | **Blocked** — needs participants |
| Claims remain within what the sample and design support | **Blocked** — needs participants |

### Work assignments

1. Write inclusion/exclusion criteria, consent, withdrawal, data-retention,
   incident, and debrief procedures appropriate to the institution.
2. Select validated presence, trust, workload, usability, and social-agent
   instruments with licenses and scoring rules checked.
3. Use a counterbalanced within-subject design where practical and predefine
   condition order, washout, training, and stopping rules.
4. Run a pilot to measure task duration, ceiling effects, confusing prompts,
   headset comfort, and instrumentation loss.
5. Pre-register primary/secondary outcomes and analysis before the main study.
6. Report effect sizes, uncertainty, missingness, order effects, qualitative
   coding procedure, and limitations; avoid overstating a small sample.

### Acceptance gate

- Protocol and analysis are frozen before main data collection.
- Behavioral logs and questionnaires join through pseudonymous IDs only.
- Every exclusion and missing session is accounted for.
- Results include ablations, effect sizes, uncertainty, and negative findings.
- Claims remain within what the sample and design can support.

## Part 10 — Reproducible release and research package

**State:** In progress (started 2026-08-03)  
**Dependency:** Frozen benchmark and study results.  
**Current slice:** One-command reproduction exists and is honest about what it
cannot yet prove.

### Completed slice 10.1 — One-command reproduction

`scripts/reproduce.py` runs everything this repository can currently prove and
reports three outcomes that are never conflated:

- `pass` — the check ran and met its criterion;
- `unavailable` — the check cannot run here, **with the reason stated**;
- `fail` — the check ran and did not meet its criterion.

That distinction is the slice. A reproduction command that printed a green tick
for whichever checks happen to be runnable would be reporting the completeness
of the harness as though it were the completeness of the research — which, in a
program where thirteen of seventeen contract metrics are unmeasured, is the
single most misleading thing it could do. Exit status is non-zero only for
`fail`: an honest gap is not a broken build, and treating it as one trains
people to ignore it.

Current output: **3 pass, 2 fail, 4 unavailable** (with `--fast`).

The failing check is the important one. `git-provenance` reports that HEAD does
not contain four research paths, so every manifest naming this revision is
misleading. Part 11 has been in the plan since the assessment; the reproduction
command now makes it impossible to forget, because the headline command for the
whole project fails until it is fixed.

The `unavailable` checks are the honest inventory: no system has been run
against the benchmark, speech-to-first-audio is not computable while capture
and endpointing have no producer, and no participant has been recruited.

### Remaining work

1. Paper-style report: problem, related work, hypotheses, system, threat model,
   evaluation, ablations, results, failures, limitations, ethics.
2. Publish the benchmark, contract, and smallest safe fixture set.
3. Clean-machine reproduction from the public instructions.
4. Demo assets: five-minute technical walkthrough, 90-second overview,
   architecture figure, results table, live failure/recovery sequence.
5. Dependency, secret, licence, accessibility, and data-retention reviews.
6. Tag the evaluated release and preserve its manifests.

### Work assignments

1. Produce a paper-style report: problem, related work, hypotheses, system,
   threat model, evaluation, ablations, results, failures, limitations, ethics.
2. Publish the reusable benchmark/contract and the smallest safe fixture set.
3. Add one-command setup, validation, benchmark, report, and demo paths.
4. Reproduce on a clean machine from the public instructions.
5. Create a five-minute technical demo, a 90-second overview, an architecture
   figure, a results table, and a live failure/recovery sequence.
6. Complete dependency, secret, license, accessibility, and data-retention
   reviews and document remaining risks.
7. Tag the exact evaluated release and preserve its manifests and artifacts.

### Acceptance gate

- An independent person reproduces the principal benchmark table.
- The evaluated tag builds and runs without access to developer secrets.
- Public claims map directly to frozen contract metrics and evidence.
- The demo succeeds offline and includes a rehearsed recovery path.
- Known limitations and unsupported safety claims are explicit.

## Part 11 — Reproducibility integrity and artifact provenance

**State:** Queued (blocking)  
**Dependency:** None. Nothing else in Parts 11–14 is trustworthy until this
passes, and it is the smallest item in the program.

**Why this exists.** The 2026-08-03 assessment ran the suite and found
`test_frozen_part4_repair_report_reproduces_committed_baseline` failing. The
scientific result reproduced exactly; only `implementation_digest` differed.
Regenerating the baseline during Part 5 made the test green again, which is
the trap: the test now passes because the artifact was rewritten to match the
working tree, not because the reproducibility property holds. Tracing it
exposed something worse than a stale digest:
`evaluation/part4.py` SHA-256s the raw *bytes* of three source files, and two
of them — `autonomy/plan_state.py` and `autonomy/plan_execution.py` — are not
tracked in git. Nor are `provenance/`, `evaluation/`, `authorization/`, or
`research/`. Every run manifest records `code_revision: f40ef92…` with
`worktree_dirty: true`, naming a commit that does not contain the code that
produced the result.

Section 2.1 invariant 12 says every reported result names the code revision
that produced it. At present no result in this repository does.

### Work assignments

1. Commit the research modules so the revision in every manifest is real.
2. Recompute `implementation_digest` over LF-normalized content of **tracked**
   files, so a comment edit or a CRLF checkout cannot invalidate a frozen
   artifact whose behavior is unchanged.
3. Add `git rev-parse HEAD` plus a clean-tree assertion to baseline generation,
   and refuse to write a baseline from a dirty tree without an explicit
   `--allow-dirty` flag that is recorded in the artifact.
4. Add `.gitattributes` line-ending normalization for every digested path.
5. Re-freeze the Part 3 and Part 4 baselines against the committed revision.
6. Add a test that fails when a digested file is untracked.

### Acceptance gate

- `uv run pytest -q` passes with zero failures from a clean checkout.
- Every committed baseline names a revision that contains its own inputs.
- A dirty worktree cannot silently produce a frozen artifact.
- Reordering, editing, or removing a digested file fails the digest test.

## Part 12 — Real-model evidence engine

**State:** Queued  
**Dependency:** Part 11 integrity; Part 2 replay adapters; Part 8 scenarios.

**Why this exists.** This is the single highest-value item in the program. The
measurement substrate is publication-grade and has measured nothing:
`research/baselines/part2-development.json` reports thirteen of seventeen
contract metrics as `unavailable`, and both numeric results in the repository
(`part3-calibration`, `part4-plan-repair`) are synthetic fixtures driven by
`fixture-llm-v1`. No language model is in the loop of any measurement here. The
Part 4 headline is n=8 with no confidence interval.

Every additional mechanism raises the ceiling by zero until this changes. A
reviewer will read "5/8 on a synthetic fixture with a stub model" as evidence
that the harness runs, which is what it is.

### Work assignments

1. Add a **record** mode to the Part 2 adapters that captures real completions
   from live providers into cassettes, keyed by a digest of the request, and
   replays them deterministically afterwards. D-007 forbids a live fallback
   during replay; recording must therefore be a separate, deliberate operation
   that can never be entered implicitly.
2. Run the frozen suite across at least three model families — a frontier API
   model, a mid-tier model, and a local open-weights model — and report per
   model. A mechanism that works on one model is a prompt; one that works on
   three is an architecture.
3. Add a statistics package: paired per-scenario comparisons, bootstrap
   confidence intervals, effect sizes, and a pre-registered analysis script.
   Report n and CI beside every rate; never report a bare proportion.
4. Add an **external baseline** condition. Ablating the system against itself
   shows internal contribution and cannot show the system beats anything.
   Compare against a plain tool-calling agent on the identical scenario set
   with the identical model, and report honestly if it wins.
5. Build a failure taxonomy from provenance chains: cluster every failure by
   the stage at which the causal chain broke — observation, belief, plan,
   authorization, action, verification, recovery. The ledger makes this nearly
   free and "where embodied agents actually fail" is independently citable.
6. Re-run every Part 3 and Part 4 result under real models and replace the
   synthetic headline numbers, marking the synthetic ones as harness proofs.

### Acceptance gate

- Every contract metric is `measured` or has a stated reason it cannot be.
- Cassette replay is bit-identical across three runs and cannot reach a network.
- Results are reported per model family with n and confidence intervals.
- At least one external baseline is measured on the same scenarios.
- Every reported failure is attributed to a causal-chain stage.
- No synthetic fixture result is presented as a system claim.

## Part 13 — Spatial capability spoofing and embodied injection benchmark

**State:** Queued  
**Dependency:** Part 5 kernel, Part 6 semantic world, Part 12 evidence engine.

**Why this exists.** This is the most original unexploited asset in the
codebase, and Part 6 has already demonstrated the first instance of it.

The `world_model` label-ambiguity defense, the `REACH_CEILING`, and the
150-unit separation rule are three defenses against one unnamed attack class:
**manipulating world objects to steal the capabilities bound to them.** Placing
a decoy `computer` beside the agent, relocating a gated object into reach,
inflating a declared `reach`, or using homoglyph labels are all the same move.
Part 6 slice 6.3 built the instrument that detects it — `cross_unlocks` — and a
regression test that drags the archive shelf onto the computer and watches
`execute_bash` become available from the shelf.

No public benchmark targets an embodied agent's *perceptual surfaces*. That is
a citable artifact, and it is the natural fusion of Parts 3, 5, and 6.

### Work assignments

1. Name and define the threat class, with a formal statement of what a spatial
   capability grant is and what an attacker must control to forge one.
2. Author the attack corpus: decoy object, object relocation, reach inflation,
   label homoglyph and case collision, ambiguity-induced fail-open, containment
   spoofing, occlusion spoofing against `line_of_sight`, and renderer-declared
   `visible_from` forgery.
3. Extend the Part 5 seven-source injection suite into a full corpus delivered
   through email body, web page, calendar title, file content, screen text,
   tool output, **and furniture metadata**, each with a forbidden-effect
   predicate rather than a refusal-string match.
4. **Close the calibration loop.** `provenance/calibration.py` measures
   per-source-class overconfidence and nothing consumes it. Make the
   authorization kernel require evidence authority proportional to action risk,
   with the threshold set by *measured* calibration rather than model-stated
   confidence. This is where the project's thesis actually lives: Parts 3 and 5
   are currently separate systems.
5. Measure the effect: unauthorized-effect rate with and without
   calibration-gated authorization, on held-out attacks, across model families.
6. Publish the corpus and the defenses as a reusable artifact.

### Acceptance gate

- Zero forbidden effects across the full corpus, on every measured model.
- Every defense maps to at least one attack that defeats the system without it.
- Calibration-gated authorization shows a measured effect with a CI, or is
  reported as no-effect rather than quietly dropped.
- The corpus runs from manifests without external accounts.
- Attacks are stated so that another group can implement them independently.

## Part 14 — Research communication and elite demonstration

**State:** Queued  
**Dependency:** Part 12 results, Part 13 corpus.

**Why this exists.** The system's strongest properties are invisible. A viewer
sees an avatar in a room and infers a chatbot with a sprite; the hash-chained
provenance, the authorization kernel, and the crash-resume protocol are all
things that show up only when something goes wrong, which is exactly when a
demo normally hides.

### Work assignments

1. Build the **live provenance inspector**: click any autonomous action and see
   its causal chain — what was observed, what was believed with what confidence
   and authority, which plan, which policy decision and on what grounds, what
   was verified. The ledger already holds every field; nothing else needs to be
   recorded. This is the best demo asset in the project and nobody else ships
   it.
2. Rehearse the **live failure and recovery** sequence: kill the process
   mid-action and let it resume with no duplicate side effect. Part 4 already
   proves this in tests; surfacing it live is the most memorable thirty seconds
   available.
3. Rehearse the **live injection refusal**, ending on the receipt that names
   the policy and the source.
4. Reframe the walk. Proximity gating is presented in `PROJECT_STATUS.md` as a
   security boundary and it is not one: an injected planner calls
   `move_to("computer")` and proceeds. What it actually provides is
   **observability** — no consequential side effect occurs without a visible,
   pre-action, non-textual signal. Stated that way it is a contribution;
   stated as security it is an invitation to be dismantled.
5. Choose the venue and commit. Two coherent papers exist and they need
   different evidence:
   - **Paper A — provenance-complete embodied agents.** Systems/security;
     USENIX Security, IEEE S&P, or NeurIPS D&B for the benchmark. Contribution:
     one authorization boundary, typed belief provenance with authority
     ordering, content-free replayable audit, spatial capability spoofing.
     This is where the code is strongest and the claim most original.
   - **Paper B — embodiment as an authorization-legibility channel.** CHI or
     UIST. Walking-before-acting as a pre-action consent signal.
   Recommended: A as primary, with one small human evaluation folded in from
   B — *do users detect an unauthorized action faster when it is spatially
   legible?* A systems paper carrying a human-subjects validation of its own
   interface claim is much harder to reject than either alone.
6. Freeze feature breadth. Weather, bilibili, books, and the drawing board are
   demo texture with no mapped hypothesis; section 11 already names this risk.
   Do not let Quest hardware acceptance block Parts 12–13.

### Acceptance gate

- The inspector renders a complete chain for any action, with no private
  content, in under a second.
- The failure and injection sequences succeed offline, twice consecutively.
- Every public claim maps to a frozen contract metric with measured evidence.
- The chosen venue's evidence requirements are met, or the gap is stated.

## 8. Progress ledger

| Date | Part | State change | Evidence | Next action |
| --- | --- | --- | --- | --- |
| 2026-08-03 | 1 | Queued → In progress | Existing study, autonomy, Presence, security, world, and VR contracts audited | Create the versioned research contract and strict validator |
| 2026-08-03 | 1 | In progress → Complete | Contract revision `2026-08-03.1`; validator CLI; 17 contract tests; contract + study `47 passed`; Ruff passed | Begin Part 2 with the run manifest and event-envelope contracts |
| 2026-08-03 | 1 | Complete → Reopened | Adversarial review found ambiguous within-condition designs, missing non-inferiority margins, incomplete target gating, and an invalid no-autonomy unwanted-rate comparison | Tighten the contract and add regression mutations |
| 2026-08-03 | 1 | Reopened → Complete | Contract revision `2026-08-03.2`; 30 contract tests; contract + study `60 passed`; Ruff passed | Start Part 2 only after the revised gate passes |
| 2026-08-03 | 2 | Queued → In progress | Versioned manifest and hash-chained content-free event envelope; 19 new tests; combined `79 passed` | Integrate existing telemetry and deterministic adapters |
| 2026-08-03 | 2 | In progress → Reopened audit | Slice 2.1 lacked durable recording, replay adapters, failure injection, and a baseline; legacy short strings could leak through study logging | Implement and validate the complete measurement path |
| 2026-08-03 | 2 | Reopened audit → Complete | Durable recorder; telemetry projections; five replay adapters plus deterministic clock; failure injection; three-run equivalence; committed four-state baseline; full suite `675 passed` | Begin typed provenance records for Part 3 |
| 2026-08-03 | 3 | Queued → In progress | Six typed record kinds, authority/privacy/time/evidence lifecycle, graph invariants, 11 focused tests, full suite `687 passed` | Build the durable ledger and contradiction policy |
| 2026-08-03 | 3 | In progress → Complete | Foundation audit fixed 4 defects; hash-chained ledger with torn-write recovery; keyed-digest content store; seven-outcome contradiction policy; source-class calibration; evidence-minimal retrieval; cascading deletion into embeddings; ablation-gated prompt seam; 46 new tests, full suite `737 passed` | Begin Part 4 with the plan state machine |
| 2026-08-03 | 4 | Queued → In progress | Part 3 focused revalidation `61 passed`; existing agenda checkpoint, lease, queue, interruption, and outbox paths audited | Define the v1 durable plan state machine and crash-resume contract |
| 2026-08-03 | 4 | In progress → Complete | Durable v1 plan state machine and agenda sidecar; five-boundary crash recovery; report outbox recovery; fair dual-loop scheduler; bounded repair/counterfactual policy; live turn provenance; frozen repair ablation 5/8 vs 2/8 with zero violations/duplicates; 29 dedicated tests; full suite `766 passed` | Begin Part 5 with the single action descriptor and central authorization boundary |
| 2026-08-03 | 4 | Complete → Revalidated | Atomic cross-file absorption; link/handoff recovery; orphan deadline sweep; unknown-effect preservation; strict state/repair validation; live read repair; preemption/restart lease release; sidecar-safe offline abandon; 38 dedicated tests; repaired frozen baseline verified | Begin Part 5 only after the repaired baseline and full suite pass |
| 2026-08-03 | 5 | Queued → In progress | Versioned content-minimal action descriptor; trusted runtime context; exact user-confirmation binding; fail-closed 11-check kernel; 11 focused tests; combined full suite `786 passed` | Inventory capabilities and add the durable policy-attempt/outcome ledger |
| 2026-08-03 | 5 | In progress → Complete | Reviewed profiles; sole production transaction boundary; hash-chained crash recovery; exact writing-desk approval; verified local outcomes; bounded compensation/unresolved semantics; 7/7 indirect attacks blocked with zero effects; 24 dedicated tests; full suite `798 passed` | Begin Part 6 with renderer-independent semantic 3D contracts |
| 2026-08-03 | 6 | Queued → In progress | Semantic schema v2 with position/orientation/bounds, containment/support, visibility, occupancy, ownership, affordances, temporal change, strict renderer exclusion, and typed relations; 9 new tests; screen/VR compatibility gate `40 passed`; full suite `808 passed`; Ruff and `git diff --check` passed | Build screen/VR semantic projectors, then obstacle-aware navigation and line of sight |
| 2026-08-03 | 6 | In progress → Complete | Foundation audit fixed 4 measured defects (cached validation, O(n) acyclicity, demoted declared visibility, record round trip); screen/VR projectors; capability parity 15/15 objects with zero cross-unlocks; visibility-graph navigation with seven typed refusal reasons; VR docking 15/15; 26 new tests; world/VR gate `66 passed`; full suite `834 passed`; Ruff and `git diff --check` passed | Begin Part 7 by instrumenting per-stage conversational latency |
| 2026-08-03 | 7 | Queued → In progress | Closed nine-stage latency vocabulary, monotonic clock, nearest-rank percentiles, missing-not-zero end-to-end figure, declared budgets with measured verdicts, content-free metric events; 13 tests; full suite `847 passed` | Wire the recorder into the live conversation path, then add streaming overlap and stale-output cancellation |
| 2026-08-03 | 7 | In progress → Instrumented | Audit fixed 4 defects (unbounded sample growth, `enter()` misfiling stage time, NaN bypassing the negative check, wrong clock annotation); wired 5 stage boundaries into the live turn with a swallowing edge proven by a broken-recorder test; bounded background interruption measurement; operator console at `/api/operator/health`; 24 latency tests; full suite `858 passed` | Send frontend capture/endpoint timings so the end-to-end budget becomes computable |
| 2026-08-03 | 8 | Queued → In progress | Versioned scenario contract with closed terminal predicates, disjoint allowed/forbidden effects, mechanically frozen held-out split with load-time digest verification, and coverage reporting that names missing areas; 19 tests | Author the development split and build the predicate scorer |
| 2026-08-03 | 8 | In progress → Built | Audit fixed 3 defects (list/frozenset digest-vs-equality split, mutable coverage report, guard weaker than it looked); 100 scenarios across all 10 areas with a load-verified frozen digest; `trigger` added after authoring found autonomy/VR scenarios have no utterance; scorer with forbidden-effect voiding, causal-chain taxonomy, seeded bootstrap CIs and paired comparison; 46 tests; full suite `904 passed` | Build initial-state fixtures and predicate observers so the suite can be run |
| 2026-08-03 | 9 | Queued → In progress | Draft protocol with a behavioural primary outcome (H-LEGIBILITY), three conditions, disclosed probe trial, minimum reportable n, stopping rule, missingness policy, and a stated list of what the study cannot settle | Obtain ethics approval and verify instrument licences |
| 2026-08-03 | 9 | In progress → Designed | Protocol audit found two design defects by making the design executable: a Latin square that does not balance carryover for three conditions, and a single probe trial that made the primary outcome binary. Williams six-sequence design with a direct balance test; three probes per condition; censoring, pilot discipline, and interval-based guardrail implemented; 27 tests on simulated cohorts | Obtain ethics approval; run the six-participant pilot |
| 2026-08-03 | 10 | Queued → In progress | `scripts/reproduce.py` reports pass/unavailable/fail without conflating them; 3 pass, 2 fail, 4 unavailable; the failing provenance check makes Part 11 block the headline command; 8 tests; full suite `939 passed` | Write the paper-style report and the demo assets |
| 2026-08-03 | 10 | In progress → Audited | Fixed 3 defects in the reproduction command: a latency check that read in-process state and could never pass, a check that returned `pass` unconditionally, and a secret scanner whose bare substrings fired on the word "risk-" and on prose naming an OAuth field. Added an integration check; removed `utils/install_utils.py` (140 LOC, zero references) | Write the paper-style report and demo assets |
| 2026-08-03 | 6 | Complete → Reopened | Integration audit: `semantic_world`, `semantic_projection`, `semantic_navigation` (1,546 LOC) are unreachable from `server.py`; the live gate still uses `world_model` and its straight-line `path_to` | Route the proximity gate and movement through the semantic world, or withdraw the Part 6 completion claim |
| 2026-08-03 | — | Assessment | External assessment found the evidence gap: 13 of 17 contract metrics `unavailable`, both numeric results synthetic with a `fixture-llm-v1` stub, no external baseline, no multi-model evidence, and every research module untracked in git | Added Parts 11–14; Part 11 blocks the reproducibility claims of all earlier parts |

## 9. Current execution queue

1. **Integrate Part 6 or withdraw its claim.** Route the proximity gate and
   movement through `semantic_projection`, so the parity and navigation
   guarantees apply to the path that actually authorizes tools.
2. **Part 11.** Commit the research modules and re-freeze the baselines against
   a real revision. `scripts/reproduce.py` now fails until this is done, and it
   is roughly an hour of work that makes every reproducibility claim in this
   plan true for the first time.
2. Obtain ethics approval and verify instrument licences, which gate the whole
   of Part 9 and cannot be shortened by any amount of engineering.
3. Build the initial-state fixtures the 100 scenarios reference, and wire an
   observer for each terminal predicate so the suite can actually be run.
3. Commit the research modules and re-freeze the baselines against a real
   revision (Part 11) — this blocks the reproducibility claim of every part
   already marked complete.
4. Add cassette recording against live providers (Part 12), which is the first
   step that converts the existing apparatus into evidence.

## 10. Decision log

| ID | Date | Decision | Reason | Revisit when |
| --- | --- | --- | --- | --- |
| D-001 | 2026-08-03 | Position the system as a trustworthy continual embodied agent, not a VR chatbot | Unifies autonomy, memory, embodiment, safety, and evaluation under one research claim | Evidence shows a narrower contribution is stronger |
| D-002 | 2026-08-03 | Make the research contract machine-checkable before building the benchmark | Prevents metric, ablation, and release-gate drift | Contract versioning becomes burdensome without preventing drift |
| D-003 | 2026-08-03 | Preserve content-free shared traces | The system handles intimate voice, screen, and memory data; reproducibility must not require leaking it | A reviewed consent protocol explicitly authorizes a separate protected dataset |
| D-004 | 2026-08-03 | Treat targets as unproven release gates until measured | Avoids turning aspirations into unsupported claims | Never; only evidence changes a target's measured status |
| D-005 | 2026-08-03 | Separate causal ablation claims from complete-system safety and resilience claims | A full-vs-full stress test and an isolated mechanism ablation answer different questions | A dedicated policy-factor experiment is added |
| D-006 | 2026-08-03 | Allow only identifier-like strings in shareable event payloads | A 120-character bound alone can still leak short messages, prompts, titles, or memory text | A reviewed protected-data protocol introduces a separate non-shareable schema |
| D-007 | 2026-08-03 | Give replay adapters no live fallback at all | A replay that silently calls a real tool or network service is neither deterministic nor safe | Never; record mode is a separate deliberate operation |
| D-008 | 2026-08-03 | Keep raw private content outside provenance records | The inspectable graph needs authority and evidence, not transcripts; opaque references permit governed deletion and tombstones | A protected store contract is implemented and reviewed |
| D-009 | 2026-08-03 | Require a content-free `claim_key` on every record, at schema version 2 | Contradiction detection needs to know when two records answer the same question without reading either; no version-1 record had been persisted, but the version still moves so any external copy fails closed | A claim vocabulary proves too coarse for real conflicts |
| D-010 | 2026-08-03 | Separate `retracted` from `tombstoned` | Withdrawing a claim and deleting the user's content are different decisions; conflating them would make every resolved contradiction destroy content | A review finds users expect retraction to delete |
| D-011 | 2026-08-03 | Store operations rather than record edits, with derived changes written into each entry | Replay must reproduce history even after the policy that decided it changes; policy may improve, history may not | Entry size becomes the dominant storage cost |
| D-012 | 2026-08-03 | Recover from an unterminated final ledger line instead of failing closed | A torn write is a crash, not tampering, and the operation was never acknowledged; refusing to open would cost a whole memory to save one lost append. Whole-line damage still fails closed | Evidence shows torn tails being used to hide deliberate truncation |
| D-013 | 2026-08-03 | Cascade deletion through evidence links and purge derived artifacts | A summary of a deleted message is that message, and an embedding of it is a lossy copy a similarity search can still surface | A reviewed anonymization method makes derivations genuinely non-reversible |
| D-014 | 2026-08-03 | Keep agenda schema v1 and add resumable execution as a sidecar | Existing workspaces and ownership logic remain valid while the stricter state machine can fail closed independently | The sidecar and agenda repeatedly require non-atomic cross-file changes |
| D-015 | 2026-08-03 | Treat a stable idempotency key as an adapter contract, not proof of exactly-once service behavior | A key prevents duplication only when the destination or wrapper enforces it; unsupported tools must remain blocked | Part 5 supplies verified wrappers for every consequential capability |
| D-016 | 2026-08-03 | Do not let repair broaden scope or replay a recorded effect | Recovery is not a new authorization and postcondition failure does not prove that a side effect failed to occur | Part 5 outcome verification can safely distinguish retry, compensate, and escalate |
| D-017 | 2026-08-03 | Treat an action descriptor as an untrusted proposal, never as authority | Prompt, email, page, file, screen, calendar, furniture, and tool text may describe a grant; only trusted runtime state may issue one | A stronger capability-token mechanism replaces the context adapter without weakening this separation |
| D-018 | 2026-08-03 | Never retry an ambiguous post-commit outcome automatically | Tool error or process loss does not prove an effect failed; at-most-once ambiguity is safer than duplicated mail, deletion, or external state | An adapter supplies destination-enforced idempotency plus authoritative readback |
| D-019 | 2026-08-03 | Keep semantic world facts closed and renderer-independent | Meshes, textures, DOM, materials, and styles are presentation, not authority or spatial truth | A reviewed renderer attestation protocol is demonstrably stronger than backend semantic state |
| D-020 | 2026-08-03 | State screen/VR parity at the capability level, not the coordinate level | The two rooms legitimately differ by 232 floor-plane facts because a calendar hangs on a wall in 3D and occupies a legible band in 2D; what may never differ is what the agent is allowed to do from a given object | A shared floor plan becomes authoritative for both renderers |
| D-021 | 2026-08-03 | Derive visibility from geometry and demote the declared field to a claim | `visible_from` arrives inside a scene document, and a client able to write it could manufacture exactly the evidence an authorization gate asks for; an entity 39 m away behind a wall returned `holds=True` | A renderer attestation protocol makes declared observation trustworthy |
| D-022 | 2026-08-03 | Refuse to plan a walk on the 2D stage rather than plan on pictorial geometry | Its `height` is drawn height, not floor depth — the same avatar measures 92 deep on screen and 20 in the headset — so real clearance seals a room the user can see is open; an advisory route a gate might trust is worse than a refusal | The 2D scene schema gains a real per-object floor depth |
| D-023 | 2026-08-03 | Treat "no evidence yet" as the program's binding constraint, ahead of any new mechanism | The apparatus is publication-grade and has measured nothing; a synthetic n=8 result with a stub model reads to a reviewer as proof the harness runs | Real-model results exist across at least three model families |
| D-024 | 2026-08-03 | Reframe proximity gating as observability rather than security | An injected planner calls `move_to` and proceeds, so the gate stops nothing an adversary cannot satisfy; what it does provide is that no consequential effect happens without a visible pre-action signal | A capability-token mechanism makes spatial position genuinely load-bearing |
| D-025 | 2026-08-03 | Make latency measurement swallowing on the live path and strict everywhere else | A measurement that raises turns "we cannot time this turn" into "the user gets no reply"; the same separation the codebase already makes for provenance | A measurement failure is found to be hiding a real fault rather than a metric gap |
| D-026 | 2026-08-03 | Report Part 7 product gates as unmeasured rather than redefining them | Three of five need a real stack, a Quest 3, and participants; a target quietly restated as met is the one failure D-004 exists to prevent | The reference machine, headset, and protocol exist |
| D-027 | 2026-08-03 | Give the held-out split no default that opens it | Overfitting is prevented by mechanism or not at all; an opt-out flag that defaults to permissive is a comment, not a control | A reviewed final-evaluation runner is the only caller able to pass the flag |
| D-028 | 2026-08-03 | Void partial credit when a forbidden effect occurs | Completing the task and leaking is not partial success; without this a system raises its score by doing more of the task while still leaking | A graded harm model distinguishes severities of forbidden effect |
| D-029 | 2026-08-03 | Treat a missing run as an error rather than a zero | Dropping unrun scenarios lets a half-crashed evaluation report the success rate of the half that finished, which is the most flattering reading of a broken measurement | Never |
| D-030 | 2026-08-03 | Separate harness errors from system failures, and let them invalidate the gate | A broken measurement scored as a system loss both defames the system and hides that the number is not valid | Harness reliability is itself measured and reported |
| D-031 | 2026-08-03 | Make the human study's primary outcome behavioural, not a questionnaire | D-024 reframed proximity gating as observability; whether that channel reaches a person is a detection latency, and a presence questionnaire cannot answer it | A validated instrument measures action legibility directly |
| D-032 | 2026-08-03 | Exclude `no-autonomy` from the human study | The frozen contract already marks the intervention metrics not applicable to it, so the block costs ~25 minutes per participant to answer nothing | A hypothesis needs that condition behaviourally |
| D-033 | 2026-08-03 | Use a Williams design rather than a Latin square for three conditions | A Latin square balances position but not carryover, so practice or fatigue from one condition lands disproportionately on another and is indistinguishable from its effect | A between-subject design removes carryover entirely |
| D-034 | 2026-08-03 | Report detection latency as censored, never as a mean over successes | Averaging only the people who noticed reports a number that improves as detection gets worse | A survival model is fitted instead |
| D-035 | 2026-08-03 | Give the pilot threshold no override flag | The situation in which someone wants to override a minimum-n rule is exactly the situation in which they should not | Never |
| D-036 | 2026-08-03 | Let `unavailable` be a first-class reproduction outcome, distinct from pass and fail | Thirteen of seventeen contract metrics are unmeasured; a command that ticked the runnable checks green would report harness completeness as research completeness | Every metric is measured |
| D-037 | 2026-08-03 | Make the reproduction command fail on untracked research code | Part 11 has been outstanding since the assessment; putting it in the headline command is what stops it from being deferred indefinitely | The research modules are committed |
| D-038 | 2026-08-03 | Record Part 6 as un-integrated rather than restating its gate | The parity and navigation guarantees are real but hold for a model the authorization path never consults; calling that "Complete" is the drift D-004 forbids | The live gate reads the semantic world |
| D-039 | 2026-08-03 | Make module integration a checked property of the reproduction command | Part 6 shipped 1,546 lines beside the system without anything noticing for a full part; a static reachability trace cannot prove good use, but it does separate "wired in" from "sitting next to" | A runtime coverage signal replaces the static trace |
| D-040 | 2026-08-03 | Require secret patterns to match a credential-shaped value, not a mention | A scanner that fires on the word "risk-" and on documentation naming `client_secret` gets switched off, which is strictly worse than not having one | A reviewed secret-scanning tool replaces it |

## 11. Risks and blockers

| Risk | Current mitigation | Trigger for escalation |
| --- | --- | --- |
| Breadth overwhelms research depth | Contract-first sequencing and frozen gates | A new feature has no mapped hypothesis, metric, or reliability gap |
| Evaluation overfits development scenarios | Separate frozen held-out split and manifests | Held-out fixtures are opened during tuning |
| Human-study claims exceed sample quality | Pilot, counterbalancing, validated scales, effect sizes | Recruitment cannot support the planned design |
| Private data leaks through evaluation | Content-free schema, field allowlists, mutation tests | Any trace contains transcript/file/screen content |
| VR polish hides semantic inconsistency | Renderer-independent world/action contracts | Screen and VR produce different authorization outcomes |
| Autonomous side effects exceed user intent | Central policy, confirmation, idempotency, verification | Any unauthorized effect appears in testing or use |
| Performance targets conflict with offline models | Per-stage latency budgets and configurable engines | Reference hardware cannot reach p95 target after profiling |

## 12. Completion record

### Part 1 — Research charter and evaluation contract

Completed and adversarially revalidated on 2026-08-03 at contract revision
`2026-08-03.2`. The machine-readable contract, strict validator, operator CLI,
and mutation tests define the official claim and prevent silent drift.

### Part 2 — Measurement substrate and deterministic replay

Completed on 2026-08-03. Versioned manifests and envelopes, durable recording,
privacy-safe legacy projection, closed-world deterministic adapters, bounded
failure injection, three-run equivalence, and the integrity-bound development
baseline now satisfy the Part 2 gate. Unmeasured research/product metrics remain
explicitly unavailable and are not claimed as successful.

### Part 3 — Provenance ledger and typed belief memory

Completed on 2026-08-03 at provenance schema version 2. The typed foundation
was audited before being built on, and four defects were fixed. A hash-chained
append-only ledger with torn-write recovery, a keyed-digest private content
store with irreversible deletion, a seven-outcome contradiction policy that
never lets a lower authority overwrite a higher one, source-class confidence
calibration that reports `unavailable` rather than guessing, evidence-minimal
retrieval, and deletion that cascades into summaries and embeddings now satisfy
the Part 3 gates. Calibration is demonstrated on a frozen synthetic held-out
fixture; no production calibration is claimed. Ledger ingestion from live turns
now records turn-scoped private evidence; semantic promotion remains
conservative and is not inferred from arbitrary conversation text.

### Part 4 — Dual-loop cognition and resumable plans

Completed on 2026-08-03 at plan schema version 1. A workspace-local sidecar now
provides durable leases, stable execution identities, before/after checkpoints,
bounded repair, counterfactual review, cancellation reconciliation, fair
scheduling, and recoverable terminal reports while preserving agenda schema v1.
Live conversation provenance is ingested off the reactive critical path. The
frozen synthetic repair ablation improves success from 2/8 to 5/8 without a
policy violation or duplicate effect. A subsequent adversarial audit closed the
cross-file agenda/sidecar crash window, added restart handoff reconciliation,
preserved unknown deadline effects, and strengthened state/repair validation.

### Part 5 — Authorization kernel and transactional actions

Completed on 2026-08-03. The sole production MCP executor now routes every
reviewed consequential capability through the version-1 descriptor, trusted
11-check kernel, append-only journal, exact writing-desk confirmation where
risk requires it, commit claim, verification, and one terminal result.
Duplicate exact deliveries do not repeat impact; changed arguments cannot
inherit confirmation; unknown tools and missing evidence fail closed. The
frozen attack ablation records zero unauthorized effects across seven indirect
sources. Remote adapters without authoritative evidence remain explicitly
unresolved rather than being misreported as verified.

### Part 6 — Semantic world model 2.0 and embodied intelligence

**Reopened on 2026-08-03 by the Part 10 integration audit.** The contract, the
parity instrument, and the planner are complete and tested; none of them is
reachable from the running server, which still authorizes tools through
`world_model`. Everything below is accurate about the semantic model and
describes a model the system does not yet consult.

Completed on 2026-08-03 at semantic schema version 2. The version-1 foundation
was measured before being built on and four defects were fixed: whole-graph
revalidation on every query, quadratic acyclicity, declared visibility
masquerading as observed evidence, and a parser with no serializer.

Both scene documents now project into one semantic contract. The parity
instrument found 232 floor-plane differences between the renderers and reports
them rather than hiding them; the gate that matters — identical capabilities
from identical objects — passes on all 15 objects with zero cross-unlocks, and
affordances come from the proximity tables so a document cannot name its own.
A visibility-graph planner routes around obstacles with seven typed refusal
reasons and docks all 15 VR objects outside their footprints and within reach.

The most consequential finding is that the 2D stage is a picture, not a floor
plan: its `height` is drawn height, so the same avatar measures 92 deep on
screen and 20 in the headset. Navigation refuses that renderer rather than
returning a route a gate might trust. Proximity gating is unaffected, because
distances remain internally consistent within each picture — which is exactly
why capability parity passes while geometric parity cannot.

Two gates are deferred with evidence rather than claimed: spatial-reference
accuracy needs the Part 8 frozen benchmark, and social comfort needs the Part 9
headset study. 26 new tests; world/VR gate `66 passed`; full suite `834 passed`.

### Part 7 — Real-time multimodal and WebXR product quality

Instrumented on 2026-08-03; product gates open. The instrumentation was
audited before being wired in and four defects were fixed, three of them
introduced by the slice that built it — most notably a recorder that grew
without bound, which is the exact resource leak this part's own eight-hour
soak gate exists to catch. Five stage boundaries are now measured on the live
conversation path behind a swallowing edge, so a measurement failure costs a
sample and never a reply; interruption is timed to the turn reaching a terminal
state rather than to the cancel request; and a trusted operator console reports
per-stage medians, p95s, and budget verdicts.

Two gates are recorded as **unmeasured** and two as **blocked**, not as passed.
Speech-to-first-audio cannot be evaluated even with a real model until the
frontend reports capture and endpointing, because three of its seven stages
have no producer. Headset frame rate and first-time-user comprehension need a
Quest 3 and participants respectively. Per D-004 and D-026, none of these were
restated as something the repository could satisfy.

### Part 8 — EmbodiedAgentBench and adversarial evaluation

In progress. Slice 8.1 freezes the scenario contract before any scenario is
written: closed terminal predicates the scorer can evaluate, disjoint
allowed/forbidden effect sets, a held-out split with no default that opens it
and a load-time digest check that catches a modified frozen split, and coverage
reporting that names missing capability areas so a lopsided suite cannot pass
for a benchmark. Authoring the development split and building the predicate
scorer are next.

### Part 8 — EmbodiedAgentBench and adversarial evaluation

Built and frozen on 2026-08-03. The contract was audited before scenarios were
written against it, and three defects were fixed — including one where two
scenarios digested identically but compared unequal, which would have made a
suite's own count irreproducible. 100 scenarios cover all ten capability areas
with 31 held out behind a digest verified at load and a development view that
excludes them entirely.

Authoring the suite improved the contract: autonomy and VR-lifecycle scenarios
have no user utterance, so an explicit `trigger` now distinguishes what starts
a scenario rather than forcing the author to invent an utterance nobody speaks.

The scorer refuses to trade harm against completion, refuses to score a
half-finished evaluation, separates broken measurement from system failure, and
attributes every loss to the earliest broken link in the causal chain. Both
outcome gates — ≥85% success and zero forbidden effects — are reported as
unmeasured: what exists is a benchmark and a scorer, not a result.

### Part 9 — Human study and statistical analysis

In progress. The protocol is drafted and written to be frozen, with a
behavioural primary outcome rather than a questionnaire: whether spatial
embodiment makes an unauthorized action detectable, and how quickly. Ethics
approval, instrument licensing, task authoring, the analysis script, and the
pilot all remain, and none of them can be shortened by engineering.

### Part 9 — Human study and statistical analysis

Designed and pre-analysed on 2026-08-03; blocked on ethics approval and
participants. Writing the analysis audited the protocol and found two design
defects — a counterbalancing scheme that did not balance carryover, and a
single probe trial that left the primary outcome with no resolution. Both are
fixed and the fixes are asserted by tests rather than by the labels on them.

The analysis is the pre-registration in executable form, tested against
simulated cohorts including a null effect, a reversed effect, total censoring,
withdrawals, and lost blocks. It refuses to emit a verdict below the
pre-registered minimum n, refuses to average latency over successes, and judges
the frozen guardrail on an interval rather than a point estimate.

### Part 10 — Reproducible release and research package

In progress. `scripts/reproduce.py` is the one-command entry point, and its
contribution is refusing to conflate "passed" with "not measured". It currently
reports 3 pass, 2 fail, 4 unavailable, and the failure is Part 11: HEAD does not
contain the research code, so no manifest in this repository names the revision
that produced it. That defect has been known since the assessment and is now
wired into the headline command so it cannot be deferred quietly.

### Parts 11–14 — Added by external assessment

Added on 2026-08-03 after an assessment of the whole repository. Parts 1–6
built an apparatus for producing evidence that is genuinely publication-grade
and has so far produced none: thirteen of seventeen contract metrics report
`unavailable`, and the two numeric results in the repository are synthetic
fixtures driven by a stub model. Parts 11–14 close that gap in order —
integrity first, then real-model evidence, then the original threat class the
codebase has been defending against without naming, then communication. No
further mechanism raises the ceiling until Part 12 exists.
