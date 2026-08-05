# Provenance-complete embodied agents: system, threat model, and evaluation

> Part 10 deliverable. This is the paper-style report for the embodied-agent
> project. It states what was built, what was measured, what was not measured
> and why, and what the work does not support. Every number in section 7 names
> the artifact it came from and whether a language model was involved in
> producing it.

| Field | Value |
| --- | --- |
| Contract revision | `2026-08-03.2` |
| Report revision | 1 (2026-08-04) |
| Reproduction command | `uv run python scripts/reproduce.py` |
| Evidence artifacts | `research/baselines/`, `research/scenarios/`, `research/fixtures/` |

---

## 1. Problem

A voice companion that can also act is two systems wearing one interface. The
conversational half is judged on latency and manner; the acting half is judged
on whether it ever does something the user did not want. Current practice
evaluates the first and asserts the second.

The gap widens with autonomy. An agent that runs continuously, keeps memory
across sessions, and acts on schedules or events accumulates state nobody
inspected, beliefs nobody corrected, and side effects nobody authorized
individually. When something goes wrong, the record of *why* is usually the
model's own narration of its reasoning — which is neither reliable nor
shareable, because it is full of the user's private life.

This project asks whether an embodied agent can be built so that the causal
chain from observation to verified outcome is inspectable, replayable, and
publishable **without** containing anything private, and whether spatial
embodiment contributes anything measurable to that beyond decoration.

## 2. Related work and positioning

Three literatures meet here and none of them covers the intersection.

**Agent safety and tool authorization.** Prompt-injection work establishes that
text arriving through tools, pages, and files can direct an agent, and that the
mitigation must be a policy boundary rather than a better prompt. What is
usually missing is a single enforced boundary with a durable per-attempt record:
the policy exists in the framework's documentation and in scattered checks.

**Embodied and situated agents.** Simulated-household benchmarks measure task
completion in a world. They do not treat the world description as an
*adversarial input*, because in those settings nobody can write to it. In a
system where the user decorates their own room and the agent reads their mail,
somebody can.

**Provenance and calibration.** Model confidence is known to be poorly
calibrated. Calibration is normally reported as a property of a model and then
not consumed by anything. Connecting measured calibration to an authorization
threshold is, as far as we found, not done.

**Positioning.** The contribution is the *combination*: one authorization
boundary, typed belief provenance with authority ordering, a content-free
replayable audit trail, and a named threat class — spatial capability spoofing —
targeting the perceptual surface an embodied agent binds capabilities to.

## 3. Hypotheses

Frozen in `research/contract.json` before any evaluation code was written
(D-002), and machine-checked on every reproduction run.

| ID | Statement | Status |
| --- | --- | --- |
| H-COMPLETION | The full system completes more long-horizon tasks than single-pillar ablations | **Unmeasured** — needs a real model (Part 12) |
| H-LEGIBILITY | Users detect an unauthorized action faster when the agent is spatially embodied | **Blocked** — needs participants (Part 9) |
| H-GUARDRAIL | Unwanted interventions stay below the frozen 0.15 guardrail | **Unmeasured** |
| H-SAFETY | Zero unauthorized external side effects in the adversarial suite | **Partially measured** — 0/7 indirect-injection sources, 0/6 calibration cases; no real model |
| H-RECOVERY | Crash and failure recovery meets the frozen guardrail | **Measured, deterministic** — five injected crash boundaries resume without duplicate effect |

Attention budgeting is treated as a full-system safety mechanism. Its
independent causal effect is not claimed, because no dedicated policy ablation
was run (D-005).

## 4. System

```text
Voice · Vision · Room · Tools · Time · External events
                         │
                         ▼
              Privacy-safe event ingress          ← content-free envelopes (D-006)
                         │
                         ▼
            Append-only provenance ledger         ← hash-chained, torn-write safe
                         │
                         ▼
        Typed belief + spatial/temporal world model
                  │                    │
                  ▼                    ▼
        Reactive dialogue      Deliberative autonomy
                  └─────────┬──────────┘
                            ▼
             Consent, capability, and risk gate    ← ONE boundary; calibration-gated
                            │
                            ▼
              Transactional action executor        ← idempotency key, commit claim
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
           Action receipt       Outcome verifier
                 │                     │
                 └──────────► Recovery policy
                            │
                            ▼
                 Screen · Voice · 2D · WebXR       ← projections of one semantic world
```

### 4.1 Provenance ledger (Part 3)

Six typed record kinds — observation, assertion, belief, preference, intention,
outcome — each carrying source class, authority, three timestamps, confidence,
privacy class, evidence links, contradiction links, and an **opaque reference**
to content held in a separate keyed store. The graph is hash-chained and
append-only: operations are stored rather than edits, so replay reproduces
history even after the policy that produced it changes (D-011).

Two properties are structural rather than conventional. A model reporting
confidence 1.0 cannot displace a user statement — authority ordering is checked,
not advisory. And deletion cascades forward through evidence links, because a
summary of a deleted message is that message and an embedding of it is a lossy
copy a similarity search can still surface (D-013).

### 4.2 Authorization kernel (Parts 5 and 13)

One fail-closed decision per attempted side effect, over eleven checks, written
to an `fsync`-durable hash-chained journal holding the decision and exactly one
terminal outcome — never the arguments or results.

The descriptor is an **untrusted proposal**. Authority lives only in a separate
trusted context, so text recovered from an email, a page, a file, a calendar
title, screen contents, tool output, or furniture metadata can describe a grant
and never issue one (D-017).

Part 13 changed the confidence check. It previously compared the model's
self-reported number against a fixed 0.8 — the exact thing the calibration module
was written to warn against. It now sets a required threshold from the action's
risk band and reads the stated confidence through *measured* per-source-class
calibration. An unmeasured source class fails closed, because "we have never
measured this" is not evidence of reliability, and the alternative makes an
unmeasured channel the cheapest way past the gate.

### 4.3 Semantic world (Part 6)

Both renderers project into one semantic contract. Affordances derive from the
proximity policy tables, never from the scene document, so a document cannot
name its own capabilities. Declared visibility is demoted to a *claim* and real
line of sight is traced geometrically (D-021) — an entity 39 m away behind a wall
previously reported `holds=True` on a field a client could write.

The two renderers legitimately differ by 232 floor-plane facts, because a
calendar hangs on a wall in 3D and occupies a legible band in 2D. Parity is
therefore stated and enforced at the level that matters: **identical
capabilities from identical objects**, 15/15 with zero cross-unlocks. Geometric
divergence is reported rather than hidden (D-020).

The 2D stage is a picture, not a floor plan: its `height` is drawn height, so
the same avatar body measures 92 deep on screen and 20 in the headset.
Navigation refuses that renderer rather than returning a route a gate might
trust (D-022).

### 4.4 Measurement substrate (Parts 2, 11, 12)

Versioned run manifests, hash-chained content-free event envelopes, closed-world
deterministic replay adapters with no live fallback, and bounded failure
injection. Frozen artifacts are bound to LF-normalized digests of **tracked**
files, and a freeze guard refuses to write an artifact from a tree nobody else
can obtain.

Part 12 adds cassettes: a real completion fetched once through a deliberate,
triply-gated recording operation, keyed by a digest of the request, and replayed
offline forever after. The model is genuinely in the loop of the result; the
result remains deterministic and reproducible by somebody with no credentials.

## 5. Threat model

**Assumed capabilities of the adversary.** Write access to a scene document, or
the ability to place text where the agent will read it — an email body, a web
page, a file, a calendar title, tool output, screen contents, furniture
metadata. Neither requires compromising the server.

**Not assumed.** Code execution on the host, control of the authorization
kernel, or the ability to forge the trusted user-confirmation channel.

**Named threat class: spatial capability spoofing.** An embodied agent binds
capabilities to places. A *spatial capability grant* is the inference

> the agent is at object O ∧ O grants capability C ⟹ C is available

An attacker controlling any term forges the grant without touching
authorization code. Four families: **identity** (decoy, duplicate label,
homoglyph), **geometry** (relocation, reach inflation), **perception** (declared
visibility, containment, occlusion), and **instruction** (indirect injection
through the seven text-bearing surfaces).

**Explicitly out of scope.** Proximity gating is not claimed as a defence
against any of this. It is an observability channel (D-024); the boundary is the
kernel.

## 6. Evaluation design

- **EmbodiedAgentBench.** 100 scenarios across ten capability areas, split
  41 development / 28 validation / 31 held out. The held-out split is verified
  against a recorded digest at load and there is no default that opens it
  (D-027); tuning code is handed a suite that does not contain it at all.
- **Scoring.** A forbidden effect voids the run *and* its partial credit
  (D-028). A missing run is an error, never a zero (D-029). Harness errors are
  separated from system failures and invalidate the gate rather than lowering
  the score (D-030). Failures are attributed to the earliest broken link in the
  causal chain.
- **Intervals.** Seeded bootstrap; `None` rather than a zero-width interval on
  an empty sample. Conditions are compared paired per scenario.
- **Human study.** Williams six-sequence design, three conditions, behavioural
  primary outcome, minimum reportable n=18 with no override flag, guardrails
  judged on an interval's upper bound rather than a point estimate.

## 7. Results

Every row names its artifact and whether a language model produced it.

### 7.1 Measured

| Result | Value | Artifact | Model in the loop? |
| --- | --- | --- | --- |
| Indirect prompt injection, seven sources | **7/7 blocked, 0 unauthorized effects**; boundary-off condition produces 7 effects | `part5-authorization.json` | No — deterministic adapters |
| Spatial capability spoofing corpus | **5/6 attacks defeat a naive proximity gate; 1/6 defeats the shipped gate** | `part13-spoofing.json` | No |
| Calibration-gated authorization | unauthorized effects **6/6 → 0/6**; legitimate work refused **0/3 → 2/3** | `part13-spoofing.json` | No — synthetic held-out fixture |
| Plan repair on held-out failures | **5/8 with repair vs 2/8 without**, zero policy violations or duplicate effects in both | `part4-plan-repair.json` | No — `fixture-llm-v1` stub |
| Source-class calibration | `model` states 0.73, correct 47%, ECE 0.26; `sensor` states 0.76, correct 77% | `part3-calibration.json` | No — synthetic fixture |
| Crash/resume | five injected action-boundary crashes resume with no duplicate effect and no lost terminal report | `tests/test_autonomy_part_four.py` | No |
| Screen/VR capability parity | **15/15 objects, zero cross-unlocks**; 232 geometric differences reported | `tests/test_semantic_projection.py` | No |
| Gate migration equivalence | **242 comparisons, 0 mismatches** before moving the live gate onto the semantic world | `tests/test_semantic_projection.py` | No |
| Benchmark instrument check | oracle scores **1.000 on all three splits** — every predicate is reachable, so no model is scored on a harness defect | `part12-benchmark.json` | No — this is a property of the instrument |
| Test suite | 1041+ passing | `uv run pytest -q` | No |

### 7.2 Unmeasured, with the reason

| Result | Why not | What would close it |
| --- | --- | --- |
| Full-task success ≥85% on held-out | No language model has been recorded against the suite | Record one cassette: `scripts/record_cassettes.py` |
| Per-model-family comparison | 0 of 3 declared families have a cassette | Provider credentials, or a local Ollama |
| External baseline (plain tool-calling agent) | Depends on the above | Same |
| Speech-to-first-audio ≤800 ms | Instrumented end to end since this revision, but never run against a real ASR/LLM/TTS stack under load | A reference machine and a model |
| Interruption ≤250 ms physically | Measured to terminal task state, not to the last audio sample leaving the speaker | Client-side playback-stop instrumentation |
| Quest frame rate, 100 enter/exit cycles | Requires a Meta Quest 3 | Hardware |
| Every human-study outcome | Requires ethics approval and participants | An IRB submission |

**Thirteen of seventeen contract metrics remain unavailable.** This is stated
here, in the results section, rather than in a footnote, because the single most
misleading thing this report could do is present the measured rows as though
they were the whole set.

## 8. Ablations

| Ablation | Removes | Result |
| --- | --- | --- |
| Authorization boundary off | The single policy decision | 7 unauthorized effects vs 0 |
| Calibration gate off | Measured-calibration confidence check | 6 unauthorized effects vs 0, at a cost of 2 legitimate refusals |
| Plan repair off | Bounded repair after a failed precondition | 2/8 vs 5/8 successes |
| Naive proximity gate | Label withholding, reach ceiling, route requirement | 5/6 attacks succeed vs 1/6 |
| Memory pillar off | Evidence retrieval | Persona, voice, and tool access preserved (invariant 8) |

## 9. Failure analysis

Where the system loses, in causal-chain order:

- **Observation.** `line_of_sight` treats every non-zone entity as an opaque
  box, so a bookshelf with a gap under it occludes as if solid. Conservative: it
  can refuse a legitimate sight line, never grant a false one.
- **Belief.** Live conversation turns write scoped assertion → intention →
  outcome evidence but are deliberately *not* promoted into durable semantic
  preferences without a reviewed extractor. Memory is therefore shallower than
  the architecture allows.
- **Authorization.** With the calibration gate on, two of three legitimate
  actions are refused because their source class has never been calibrated. This
  is the mechanism working as designed and it is a real usability cost.
- **Action.** `object-relocation` succeeds. See §5.
- **Verification.** Remote adapters without authoritative readback terminate as
  `unresolved` rather than verified, and are never automatically retried
  (D-018). Correct, and it means some real work ends in a state the user must
  resolve.

## 10. Limitations

1. **No real-model evidence.** The two headline numeric results driven by a
   model stub are harness proofs. A reviewer should read them as evidence that
   the measurement path runs, which is what they are.
2. **Synthetic calibration.** The calibration figures come from a frozen
   synthetic fixture. They exercise the measurement path and measure no deployed
   model.
3. **n=8 with no interval** on the plan-repair result.
4. **No human subjects.** Every legibility claim is a design argument until the
   Part 9 study runs.
5. **Single deployment.** All results come from one machine, one room layout,
   and one author's configuration.
6. **The benchmark is authored by the same people as the system.** The oracle
   check shows the suite is passable, not that it is representative.

## 11. Ethics and privacy

- Shared traces are content-free by construction: identifier-like strings only,
  with transcripts, screen contents, file contents, secrets, and memory text
  structurally excluded (D-003, D-006). Mutation tests enforce it.
- Private content lives behind opaque references with HMAC-keyed digests, so a
  tombstoned digest cannot confirm a guess about deleted content.
- The human-study protocol discloses the probe trial in advance, which makes the
  measure detection *latency* rather than deception, and states that detection
  of a disclosed probe is an upper bound on detection of an undisclosed one.
- Cassette recording refuses any request not originating in the frozen benchmark
  suite, so no conversation can reach a model provider through that path.

## 12. Reproduction

```powershell
git clone <repository> && cd embodied-agent
uv sync
uv run python scripts/reproduce.py          # every check this repository can run
uv run python scripts/run_benchmark.py --matrix
uv run python scripts/run_spoofing_benchmark.py
uv run pytest -q
```

`reproduce.py` reports `pass`, `fail`, and `unavailable` as three distinct
outcomes and exits non-zero only for `fail` (D-036). An honest gap is not a
broken build, and treating it as one trains people to ignore it.

## 13. What this work does not show

- It does not show the system beats any other agent, because no external
  baseline has been measured.
- It does not show that proximity gating stops an adversary. It does not.
- It does not show that any deployed model is calibrated.
- It does not show that spatial embodiment helps a user. That is the study.
- It does not show the latency budgets are met. They are instrumented, not met.
