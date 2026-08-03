# Provenance ledger and typed belief memory

The agent remembers things about the person using it. This document describes
where those memories live, what the agent is allowed to conclude from them, and
what happens when the user wants them corrected or gone.

The design answers one question: *why does the agent think this?* — for every
claim, at any time, without the answer requiring the conversation itself.

## The split

Two stores, governed separately.

| | Provenance ledger | Private content store |
| --- | --- | --- |
| Holds | who claimed what, on whose authority, from which evidence | the actual text |
| Shape | append-only, hash-chained JSONL | content-addressed blobs |
| Inspectable | yes — it is content-free | only by the user who owns it |
| Deletion | tombstone, record retained | bytes removed, reference retired forever |

A record never contains content. It carries an opaque `content_ref` and a
digest. Reading a memory means holding the reference and asking the store.

Digests in a real store are keyed with a store-local secret. An unkeyed
SHA-256 of a short private value is a constant anyone can compute, which would
let a tombstoned digest still confirm a guess about deleted content.

## Record types

Six, because these are different kinds of claim and must not be conflated:

| Type | Made by | Means |
| --- | --- | --- |
| `observation` | sensor, tool, system | something was directly perceived |
| `assertion` | user, external | somebody stated it |
| `belief` | model, system | the agent concluded it |
| `preference` | user | the user wants it this way |
| `intention` | model, system | the agent plans to do it |
| `outcome` | sensor, tool, system | an action's verified result |

A belief cannot acquire user authority by reporting high confidence. The type
rules make that unrepresentable rather than merely discouraged.

Each record also carries a **claim key**: a content-free name for the question
it answers. Two records with the same claim key and different digests disagree.
Use `derive_claim_key(namespace, subject)` when the subject is itself private.

## Statuses

`active` · `contested` · `superseded` · `retracted` · `tombstoned`

`retracted` is not `tombstoned`. A claim can be withdrawn — because a
higher-authority source contradicted it — while the content it referred to is
still the user's and still there.

An active record may not carry an unresolved contradiction; while a peer is
still live, it must say `contested`. The link itself survives resolution, so
the ledger keeps showing what the surviving claim beat.

## What the ledger guarantees

- **Nothing is rewritten.** Status changes are appended as operations, each
  carrying the state changes it caused, so replay reproduces the graph without
  re-running the policy that decided it.
- **A rejected operation leaves no trace.** The candidate graph is validated
  before the entry is written.
- **Damage fails closed.** A dropped, reordered, or edited entry breaks the
  hash chain and the ledger refuses to open.
- **A crash costs one operation.** An unterminated final line is a write that
  was cut in half and never acknowledged; it is discarded and repaired on disk,
  because losing a whole memory over one lost append would be worse.

## Contradiction policy

Detection is content-free: same claim key, different digest, both effective.

Resolution never overwrites. In preference order:

| Decision | When |
| --- | --- |
| `supersede` | a source updating itself, or a strictly higher authority |
| `retract_candidate` | the new claim is outranked; the incumbent is untouched |
| `retract_incumbent` | a higher authority of a different record type wins |
| `retain_both` | two holders can both be right (preferences) |
| `re_observe` | neither side is trustworthy enough; look again |
| `ask` | only the user can settle it — and only if they are present |
| `defer` | asking would interrupt, or the evidence is not in yet |

Authority ranks by `(authority, source class)`: user preferences outrank user
statements, which outrank sensor and tool observations, which outrank system
records, which outrank model inferences.

`ask` is a cost against the Presence attention budget. When the user is absent
or their attention is unavailable, the decision downgrades to `defer` and both
claims stay visibly contested.

## Confidence calibration

Reported confidence is an input feature, never a probability.

A claim is verified when a later, higher-authority record answers the same
claim key: agreeing digests mean it was right. `SourceCalibrator` reports
accuracy, mean stated confidence, overconfidence, Brier score, and expected
calibration error per source class.

Below the sample threshold the answer is `None` and the report says
`unavailable`. An uncalibrated source is not 50/50.

```powershell
uv run python scripts/report_provenance_calibration.py
```

The committed fixture is **synthetic**. It exercises the measurement path; it
does not measure any deployed model, and the report says so in its own body.

## Retrieval

`retrieve_context` selects per claim key, capped, and refuses any belief whose
supporting evidence is missing or deleted. Contested claims are returned marked
contested rather than resolved — a retrieval layer that silently picked a
winner would undo the contradiction policy.

`render_context_block` is the only place content is read. A reference whose
content was deleted renders nothing, so deletion holds even if a stale
retrieval result is rendered afterwards.

## User control

| Operation | Effect |
| --- | --- |
| `inspect_memory` | list claims; content only when a store is passed |
| `correct_memory` | append the user's version; supersede or retract the old |
| `export_memory` | the user's own data, content included |
| `delete_memory` | tombstone, cascade, and return artifact keys to purge |

Deletion follows the evidence links forward. A summary written from a deleted
message is still that message, and an embedding of it is still that message, so
dependents are tombstoned too and every derived artifact key is returned.
`EmbeddingCache.purge` is the built-in sink; pass it as the `purge` callback.

The count of artifacts actually removed is reported, so a deletion that could
not reach a cache is visible rather than assumed.

## Study conditions

`build_evidence_context` returns nothing when the memory pillar is ablated
(`EMBODIED_AGENT_CONDITION=memory=off`), and nothing when the workspace has no
ledger. Persona (`AGENT.md`) and rules (`RULE.md`) are unaffected in every
condition — only memory varies.

## Layout

```
<workspace>/provenance/
    ledger.jsonl          append-only, hash-chained entries
    content/
        blobs/            private content, addressed by hashed reference
        index.json        references, tombstones, derived-artifact links
        digest.key        store-local HMAC key
```

## Limits

- Ingestion is not yet wired into the conversation path; claim keys are
  supplied by the caller. Prompt construction consumes the ledger through
  `build_evidence_context` when one exists.
- Calibration has no production data yet, so every real source class reports
  `unavailable` outside the synthetic fixture.
- Deletion reaches artifacts that were registered with `register_derived`.
  Anything derived without registering is out of its reach by construction.
