# Release reviews

> Part 10 assignment 6: dependency, secret, licence, accessibility, and
> data-retention reviews, with remaining risks documented rather than closed by
> assertion. Reviewed 2026-08-04 at contract revision `2026-08-03.2`.
>
> Where a review could not be completed in this environment, it says so and
> names what would complete it. A review that reports "clean" because it did not
> run is worse than no review, because it stops anyone else from running one.

## 1. Secret review

**Status: pass, automated and enforced.**

`scripts/reproduce.py` includes a `secrets` check that scans every publishable
artifact — the research contract, scenario suite, baselines, fixtures, and
documentation — for credential-shaped values. Current result: **37 files
scanned, no credential markers**.

The scanner requires a pattern to match a credential-*shaped value*, not a
mention (D-040). An earlier version fired on the word "risk-" and on prose
naming an OAuth `client_secret` field, and a scanner that cries wolf gets
switched off, which is strictly worse than not having one.

**Residual risk.** This is a pattern scanner, not a reviewed secret-scanning
tool. It would not catch a credential that does not look like one — a plain
password in a config comment, for example. Replacing it with a maintained
scanner is recorded as future work, not as done.

**Confirmed by design, not just by scan:**

- Evidence envelopes admit only identifier-like strings, so a secret cannot
  reach a shareable trace even if one existed in memory (D-006).
- Cassettes refuse to record any request that did not originate in the frozen
  benchmark suite, so no conversation or credential can reach a provider through
  the recording path.
- The authorization journal stores decisions and outcomes, never arguments or
  results.

## 2. Dependency review

**Status: partial. Inventory complete; vulnerability scan not run.**

| Item | Value |
| --- | --- |
| Python | `>=3.10,<3.13` (running 3.10.20) |
| Direct dependencies | 40 in `[project.dependencies]`, plus a `bilibili` extra |
| Lock file | `uv.lock`, committed |
| Generated | `requirements.txt` (1191 lines, transitively resolved) |

**Not done and why.** No CVE scan was run: this environment has no network
access, so `pip-audit`, `safety`, and equivalents cannot reach an advisory
database. Reporting a clean dependency review from an offline machine would be
a fabricated result.

**What to run before tagging a release:**

```powershell
uv run pip install pip-audit
uv run pip-audit --requirement requirements.txt
```

**Standing policy (§5.3 of the upgrade plan).** Findings are not auto-fixed with
breaking changes to make a report green. Exploitability and migration risk are
assessed first. Two dependencies deserve attention when that scan is run:

- `torch` is pinned per-platform (`==2.2.2` on Intel macOS, `>=2.6.0`
  elsewhere). The Intel macOS pin cannot move without dropping that platform.
- `numpy>=1.26.4,<2` is held below 2.0 by downstream compatibility. This is a
  known upgrade debt, not an oversight.

**Deprecation notice already surfacing.** `google-api-core` warns that Python
3.10 support ends 2026-10-04. Not urgent; recorded so it is not a surprise.

## 3. Licence review

**Status: pass, with one restriction that governs redistribution.**

| Component | Licence | Effect |
| --- | --- | --- |
| This project | MIT (`LICENSE`, © 2025 Yi-Ting Chiu) | Permissive |
| Live2D Cubism SDK and models | `LICENSE-Live2D.md` — **proprietary** | Restricts redistribution of the Live2D runtime and bundled models |
| Python dependencies | Mixed permissive (MIT / BSD / Apache-2.0) | No copyleft obligation identified in the direct set |

**Consequence for the research artifact.** The publishable research package —
`research/contract.json`, `research/scenarios/`, `research/baselines/`,
`research/fixtures/`, and the evaluation modules — contains no Live2D asset and
is redistributable under MIT. **A public benchmark release must not bundle
`live2d-models/`.** This is the one licence constraint that changes what can be
published, and it is why the benchmark is packaged separately from the app.

**Not done.** No automated licence-compatibility scan of the full transitive
dependency tree. `pip-licenses` over `requirements.txt` would close this.

## 4. Accessibility review

**Status: fail. Documented rather than claimed.**

This is the weakest review in the set and it is reported as such.

| Surface | Finding |
| --- | --- |
| `frontend/inspector.html` | **No ARIA roles, no `<label>` elements, no landmark structure.** The provenance inspector is the newest UI and has had no accessibility work at all. |
| Main client | Not audited. Lives in the `frontend/` submodule and was not reviewed for this release. |
| Voice interaction | Inherently a strength — the primary interaction mode does not require sight or precise pointing — but this has never been tested with assistive technology. |
| VR mode | Not audited. Headset accessibility is a distinct and larger problem. |

**Concrete remaining work**, in the order it should be done:

1. Add landmark roles, labelled controls, and a visible focus order to
   `inspector.html`. It is small, self-contained, and shown on a projector, so
   it is the cheapest meaningful win.
2. Verify captions in VR mode are readable at the device's default text scale.
3. Run an automated audit (axe, Lighthouse) against the main client and record
   the result here, including the failures.

**Why this is not blocking the research artifact.** The contribution is the
agent architecture and the benchmark, neither of which is a user interface. It
*is* blocking any claim that the system is usable by everyone, and no such
claim is made.

## 5. Data-retention review

**Status: pass for governed stores; documented gaps outside them.**

### Governed by construction

| Store | Retention | Deletion path |
| --- | --- | --- |
| Provenance ledger | Append-only, hash-chained; operations are never edited | `provenance/lifecycle.py` — inspect, correct, export, delete, with referentially safe tombstones |
| Private content store | Opaque references, HMAC-keyed digests | Deletion is permanent: a retired reference cannot be rewritten, and a tombstoned digest cannot confirm a guess about the deleted content |
| Derived artifacts | Registered via `register_derived` | Deletion cascades forward through evidence links; `EmbeddingCache.purge` reports how many vectors it actually removed (D-013) |
| Evidence traces | Content-free | Nothing to retain: identifier-like strings only |
| Authorization journal | Decisions and terminal outcomes only | No arguments, no results |

### Gaps, stated plainly

- **`register_derived` is a contract on callers, not an enforced invariant.**
  Anything derived from private content without registering is outside the reach
  of cascading deletion by construction. This is the most important open item in
  this review.
- **`chat_history/` and `cache/` are not governed by the provenance lifecycle.**
  They are the legacy conversation store and the audio cache. Neither has an
  automatic retention window; both hold user content; deleting a memory through
  the provenance lifecycle does not remove a transcript from `chat_history/`.
- **`logs/` has no retention policy.** Log content is bounded by
  `trace_max_bytes` (10 MB) per trace, but nothing ages old logs out.

### Recommended before any deployment beyond a single developer machine

1. Bring `chat_history/` under the lifecycle, or document that it is a separate
   store the user must clear themselves.
2. Add an age-based sweep for `cache/` and `logs/`.
3. Make `register_derived` enforced rather than conventional — for instance by
   routing all derivation through a helper that registers as a side effect.

## 6. Network exposure review

**Status: pass, defaults are safe.**

- `conf.yaml` binds `localhost` by default; `0.0.0.0` is opt-in and commented as
  such.
- CORS is scoped to `["null", "file://"]` with `allow_credentials=False` — a
  wildcard origin combined with credentials was an earlier finding and is no
  longer present.
- The operator console, agenda, and provenance inspector endpoints sit behind
  the same trusted-request dependency.
- `execute_bash` runs in a fail-closed Docker sandbox. Static command filtering
  is a usability affordance, not the boundary.

**Residual risk.** Setting `host: 0.0.0.0` exposes an interface with no
authentication beyond the trusted-request dependency. That is an appropriate
default for a local companion and an inappropriate one for a shared network, and
the configuration comment should say so more strongly than it currently does.

## 7. Summary

| Review | Status |
| --- | --- |
| Secret | Pass, automated, with a stated limitation |
| Dependency | Partial — inventory complete, CVE scan blocked offline |
| Licence | Pass, with a redistribution restriction on Live2D assets |
| Accessibility | **Fail** — inspector has no accessibility work; client unaudited |
| Data retention | Pass for governed stores; three named gaps outside them |
| Network exposure | Pass, defaults safe |

Two of six are not clean. Both say what would close them.
