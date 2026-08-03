# Human study protocol (Part 9)

> **Status:** Draft protocol, pre-registration not yet filed, no data collected.
> Nothing in this document may be treated as a result. It exists so that the
> design is fixed *before* anyone is recruited, which is the only point at
> which fixing it is honest.

This protocol is written to be frozen. Section 12 records the pre-registration
checklist; until every line of it is signed off, the study has not started.

## 1. What the study is for

Parts 1–8 can measure whether the system *works*. They cannot measure whether
it is worth living with. The three claims that need people are:

1. **Presence.** Does a persistent, spatially embodied companion feel more
   present than the same capabilities in a chat window?
2. **Trust calibration.** Do users' expectations of the agent match what it
   actually does — including whether they notice when it does something they
   did not authorize?
3. **Bounded proactivity.** Do users find the agent's interventions helpful
   rather than intrusive, at the frozen guardrail of ≤0.15 unwanted?

Claim 2 is the one that matters most to the paper. The assessment recorded in
section 13 of the plan recommends a systems paper carrying a small
human-subjects validation of its core interface claim, and the sharpest version
of that claim is measurable:

> **H-LEGIBILITY.** Users detect an unauthorized action faster, and with fewer
> misses, when the agent is spatially embodied than when it is not.

This is the claim that turns "embodiment as an authorization-legibility
channel" from an argument into a finding, and it is a *behavioural* measure,
not a questionnaire.

## 2. Design

Within-subject, counterbalanced, three conditions drawn from the frozen
contract (`research/contract.json`): `full`, `no-embodiment`, `no-memory`.

`no-autonomy` is deliberately **excluded** from the human study. The contract
already marks `proactive_help_acceptance_rate` and
`unwanted_intervention_rate` as not applicable to it — with no interventions
there is no denominator, and a fourth condition costs roughly 25 minutes per
participant to answer nothing.

- **Order:** **Williams design — six sequences**, assigned by arrival position,
  never by the experimenter's choice.

  > This was corrected while writing the analysis. An earlier draft said "Latin
  > square", which balances *position* — every condition appears first as often
  > as last — but for three conditions does **not** balance *carryover*: it can
  > leave "B after A" happening twice as often as "A after B", so fatigue or
  > practice from one condition lands disproportionately on another and is
  > indistinguishable from that condition's own effect. A Williams design
  > balances both; for an odd number of conditions it needs the square *and*
  > its mirror, hence six sequences. `williams_sequences` builds them and a
  > test asserts the balance property directly.

- **Target n is therefore a multiple of six**, not of three: 24 = 4 per
  sequence.
- **Session length:** ~20 minutes per condition, ~75 minutes total including
  consent, training, and debrief.
- **Washout:** 5-minute break between conditions; memory state reset between
  conditions so `no-memory` is not advantaged by the prior block.
- **Training:** one scripted warm-up task per condition, excluded from analysis.

## 3. Participants

- **Target n:** 24 completed participants (4 per Williams sequence).
- **Minimum reportable n:** 18 (3 per sequence). Below that the analysis
  labels itself a pilot and emits no hypothesis verdicts at all. There is no
  flag to override this, because the situation in which someone wants to
  override it is exactly the situation in which they should not.
- **Inclusion:** adults, conversational English, normal or corrected vision and
  hearing.
- **Exclusion:** current members of the project team; anyone who has seen the
  system before; for the VR block only, susceptibility to motion sickness by
  self-report.
- **Recruitment:** institutional participant pool and departmental notice board.
  No social-media recruitment, which skews toward people already enthusiastic
  about AI companions.

## 4. Tasks

Each condition uses a matched task set drawn from the same capability areas as
EmbodiedAgentBench, with different surface content so no participant repeats a
task across conditions. Every set contains:

- two long-horizon tasks with a deadline;
- one task requiring recall of something stated earlier in the session;
- one task where the agent should ask rather than act;
- **three probe trials** in which the agent performs an action the participant
  did not authorize. Participants are told beforehand that the agent may
  sometimes do something they did not ask for, and to say so when they notice.

  > Also corrected while writing the analysis. An earlier draft specified one
  > probe per condition, which makes the per-participant measure binary and
  > leaves the primary outcome with almost no resolution: every participant
  > would score 0 or 1, and a 24-person study would be comparing two coin
  > flips. Three probes give a rate out of three. This is the difference
  > between a primary outcome that can move and one that cannot.

The probe trials are the H-LEGIBILITY measurement. They are disclosed in
advance precisely so the measure is *detection latency and rate* rather than
deception.

## 5. Measures

### Behavioural (primary)

| Measure | Definition | Source |
| --- | --- | --- |
| Unauthorized-action detection rate | Share of probe trials the participant reports | Session log + observer |
| Detection latency | Seconds from action to participant report | Session log |
| Long-horizon task completion | Frozen terminal predicates, as in Part 8 | Session log |
| Unwanted intervention rate | Interventions marked dismissed / total | Presence Contract receipts |

### Self-report (secondary)

Administered after each condition:

- **Presence:** Temple Presence Inventory, social-presence subscale.
- **Trust:** Trust in Automation scale (Jian, Bisantz & Drury).
- **Workload:** NASA-TLX, raw (unweighted).
- **Usability:** SUS.

> **Licensing check outstanding.** Each instrument's licence and scoring rules
> must be verified before use; this list is a proposal, not a cleared set.
> Substituting an instrument after data collection has begun invalidates the
> pre-registration.

### Qualitative

Semi-structured debrief, recorded with consent, transcribed and coded by two
coders with disagreement resolved by discussion. Inter-rater agreement is
reported; the codebook is fixed after the pilot and not revised afterwards.

## 6. Hypotheses and analysis

Pre-specified before any data is collected.

| ID | Hypothesis | Test |
| --- | --- | --- |
| H1 | `full` > `no-embodiment` on unauthorized-action detection rate | Paired comparison, bootstrap CI on the difference |
| H2 | `full` < `no-embodiment` on detection latency | Paired, bootstrap CI, **conditional on detection** |
| H3 | `full` > `no-memory` on long-horizon completion | Paired, bootstrap CI |
| H4 | `full` > `no-embodiment` on social presence | Paired, bootstrap CI |
| H5 | `full` unwanted intervention rate ≤ 0.15 | Interval upper bound against the frozen guardrail |

The analysis is implemented in `evaluation/study_analysis.py` and is already
tested against simulated cohorts with known effects, known null effects, and
effects in the wrong direction. Three consequences of that implementation are
part of the pre-registration:

- **H2 is censored, not zeroed.** A participant who noticed nothing in a
  condition has no latency there. Dropping them and averaging the rest reports
  the latency of the people who succeeded, which *improves as detection gets
  worse*. H2 is therefore reported only alongside H1, with the number of
  censored participants stated. If detection is near zero in `no-embodiment` —
  which is the outcome H1 predicts — H2 may be uncomputable, and that is a
  result about H1 rather than a missing analysis.
- **H5 is judged on the interval's upper bound**, not the point estimate. A
  mean of 0.14 whose interval reaches 0.31 has not shown the rate is below
  0.15.
- **A participant missing a block does not enter a paired comparison**, and no
  group mean is substituted for them. Imputing would manufacture agreement out
  of absence.

- **Primary outcome:** H1. Everything else is secondary and reported as such.
- **Multiplicity:** secondary hypotheses are reported with confidence intervals
  and explicitly labelled exploratory. No secondary result is promoted to a
  headline claim after the fact.
- **Effect sizes** are reported for every comparison, with the CI, not p-values
  alone. Paired per-participant differences, reusing
  `evaluation/scoring.compare_conditions` so the human study and the benchmark
  compute a difference the same way.
- **Order effects** are tested and reported whether or not they are significant.
- **Stopping rule:** data collection stops at n=24 or at the end of the
  recruitment window, whichever comes first. No interim peeking at outcome
  measures; only recruitment counts are monitored.

## 7. Missing data

- A participant who withdraws mid-session is reported in the flow diagram and
  excluded from analysis; their partial data is deleted.
- A condition lost to a technical failure is reported as missing for that
  participant, never imputed.
- **Every exclusion is accounted for in the report.** A study that reports 21
  of 24 without saying what happened to three is not reporting missingness.

## 8. Consent, privacy, and data handling

- Written informed consent before any data collection, covering audio
  recording, behavioural logging, and the fact that the agent may act without
  being asked.
- **Pseudonymous IDs only.** Questionnaires and behavioural logs join on a
  participant ID; no name, email, or institutional identifier is stored beside
  the data.
- Session logs remain content-free by construction (`study.py`), so the
  behavioural dataset contains no transcript. Audio, where recorded, is stored
  separately, is not shared, and is deleted after transcription and coding.
- Right to withdraw at any point without giving a reason, including after the
  session; withdrawal deletes the participant's data.
- Retention: pseudonymous behavioural data retained for the paper's archival
  requirement; audio deleted within 90 days of transcription.

> **Ethics approval outstanding.** This protocol requires IRB/ethics review
> before recruitment. The probe trial in section 4 involves the agent acting
> without authorization and must be reviewed explicitly, even though it is
> disclosed in advance and causes no external side effect.

## 9. The VR question

The headset is **not** a study condition. Physical Quest acceptance is still
pending (Part 7), and an unvalidated build in a headset would confound
embodiment with novelty, discomfort, and hardware failure.

If headset acceptance completes before the study, VR is added as a separate
optional block at the end of the session, analysed descriptively only.

## 10. Pilot

Six participants, not counted toward n=24, run to measure:

- task duration and whether the 20-minute block is realistic;
- ceiling and floor effects in the task set;
- whether the probe trial is detectable at all in `no-embodiment` — if nobody
  detects it in either condition, H1 has no measurable range and the task set
  must change **before** the main study;
- instrumentation loss rate;
- confusing wording in prompts and questionnaires.

The pilot may change the design. After the pilot is analysed, the protocol is
frozen and pre-registered, and no further change is permitted.

## 11. Threats to validity

| Threat | Mitigation |
| --- | --- |
| Novelty effect favours the embodied condition | Counterbalancing; warm-up excluded; reported as a limitation regardless |
| Demand characteristics on the probe trial | Detection is behavioural and timed, not self-reported after the fact |
| Experimenter influences condition order | Order assigned by arrival, not chosen |
| Small n oversells a difference | Minimum reportable n; CIs on everything; pilot reported as a pilot |
| Instruments not licensed for this use | Cleared before the pilot, not after |
| Memory carry-over inflates `no-memory` | State reset between conditions |

## 12. Pre-registration checklist

Nothing below may be ticked retroactively.

- [ ] Ethics/IRB approval obtained, including the probe trial
- [ ] Instrument licences and scoring rules verified
- [ ] Task sets authored and matched across conditions
- [ ] Session log fields confirmed content-free
- [x] Analysis script written and tested on simulated data (`evaluation/study_analysis.py`, 27 tests)
- [ ] Pilot run and analysed
- [ ] Protocol frozen after the pilot
- [ ] Pre-registration filed with hypotheses, primary outcome, and stopping rule
- [ ] Recruitment opened

## 13. What this study cannot settle

Stated here so it is not discovered in review.

- 24 participants in one session cannot measure whether a companion is worth
  living with over months. Presence measured after 20 minutes is a first
  impression.
- The sample will be drawn from one institution and will not represent general
  users.
- Detection of a *disclosed* probe action is an upper bound on detection of an
  undisclosed one. The study measures whether embodiment makes an unauthorized
  action **legible**, not whether users would catch a genuinely covert agent.
- No condition here tests the authorization kernel itself. That is Part 13's
  adversarial corpus, and it needs no participants.
