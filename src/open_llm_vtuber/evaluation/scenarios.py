"""EmbodiedAgentBench scenario contract.

D-002 settled the order for this program: make the thing machine-checkable
before building it. A hundred scenarios written against an informal schema
become a hundred scenarios that quietly disagree about what "success" meant,
and the disagreement surfaces after the results are in, when fixing it looks
like moving the goalposts.

So the schema comes first, and it is strict in the places where a benchmark
usually rots:

**Success is a predicate, not a vibe.** Every scenario names the terminal
conditions that must hold. A scenario with no predicate cannot be passed or
failed and is refused at load.

**Forbidden effects are first-class.** A run that completes the task *and*
sends the email twice has not succeeded. Allowed and forbidden effects are
disjoint sets and the overlap is a load error, because a benchmark that both
permits and forbids an effect will score whichever way the harness happens to
check first.

**Splits are frozen by construction.** `held_out` scenarios carry a digest and
the suite refuses to hand them to anything running in development mode. Risk
row 2 of this program is "evaluation overfits development scenarios"; the
mitigation has to be mechanical, because the failure mode is a tired person
at 2 a.m. deciding one peek is harmless.

**Scenario text is not trace text.** Prompts and titles live here because they
are authored benchmark inputs, and they are safe to publish. Nothing in this
module goes into an evidence envelope — the traces stay content-free under
D-006, and that separation is what lets the benchmark be public while the
sessions stay private.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

SCENARIO_SCHEMA_VERSION = 1

# The capability areas Part 8 promises to cover. Closed, so that a suite can be
# checked for coverage rather than merely counted: 100 conversation scenarios
# and nothing else would satisfy a bare count and answer no research question.
CAPABILITY_AREAS = frozenset(
    {
        "conversation",
        "memory",
        "spatial_grounding",
        "long_horizon_planning",
        "autonomy",
        "interruption",
        "recovery",
        "safety",
        "offline_degradation",
        "vr_lifecycle",
    }
)

SPLITS = frozenset({"development", "validation", "held_out"})

# What starts a scenario. Added after authoring the first suite: the schema
# originally assumed every scenario opens with something the user said, and
# autonomy and VR-lifecycle scenarios do not — they are started by a clock, by
# the user arriving or leaving, or by a headset session beginning. Requiring a
# prompt for those would have meant inventing an utterance nobody speaks, which
# is a benchmark measuring a request that never happens.
TRIGGERS = frozenset(
    {
        "user_utterance",
        "schedule",
        "presence_change",
        "session_event",
        "external_event",
    }
)

# Failure classes the harness knows how to inject. Part 2 already built these;
# naming them here keeps a scenario from asking for one that does not exist.
INJECTABLE_FAILURES = frozenset(
    {
        "none",
        "timeout",
        "disconnect",
        "retry",
        "cancellation",
        "stale_writer",
        "duplicate_event",
        "malformed_response",
        "tool_unavailable",
        "model_unavailable",
    }
)

# Terminal predicates the scorer can evaluate. A scenario may only assert
# things the harness can actually check; a free-text success criterion is how a
# benchmark becomes a reading-comprehension exercise for whoever scores it.
TERMINAL_PREDICATES = frozenset(
    {
        "task_completed",
        "belief_recorded",
        "belief_superseded",
        "plan_reached_terminal_state",
        "plan_repaired",
        "object_reached",
        "spatial_reference_resolved",
        "authorization_denied",
        "authorization_granted",
        "confirmation_requested",
        "outcome_verified",
        "compensation_applied",
        "unresolved_reported",
        "report_delivered",
        "report_suppressed",
        "degraded_mode_reported",
        "session_recovered",
        "no_duplicate_effect",
    }
)

_ID = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_EFFECT = re.compile(r"^[a-z][a-z0-9_.:-]{2,79}$")

MAX_TIME_BUDGET_S = 1800.0


class ScenarioError(ValueError):
    """A scenario or suite violates the benchmark contract."""


def _identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ScenarioError(f"{field_name} must be a lowercase kebab-case identifier")
    return value


def _effect_set(values: Any, field_name: str) -> frozenset[str]:
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ScenarioError(f"{field_name} must be a collection of effect names")
    result = set()
    for value in values:
        if not isinstance(value, str) or not _EFFECT.fullmatch(value):
            raise ScenarioError(f"{field_name} contains an invalid effect name")
        result.add(value)
    if len(result) > 64:
        raise ScenarioError(f"{field_name} is unreasonably large")
    return frozenset(result)


@dataclass(frozen=True)
class Scenario:
    """One versioned, scorable benchmark scenario."""

    scenario_id: str
    title: str
    capability_area: str
    split: Literal["development", "validation", "held_out"]
    prompt: str
    initial_state_id: str
    terminal_predicates: frozenset[str]
    trigger: str = "user_utterance"
    allowed_effects: frozenset[str] = frozenset()
    forbidden_effects: frozenset[str] = frozenset()
    injected_failure: str = "none"
    time_budget_s: float = 120.0
    score_weight: float = 1.0
    seed: int = 0
    adversarial: bool = False

    def __post_init__(self) -> None:
        """Normalize the collection fields to frozensets.

        Without this, a scenario built from lists and the same scenario built
        from frozensets produced *the same digest but compared unequal* —
        `digest()` sorts, `__eq__` does not. Deduplicating a suite by identity
        and deduplicating it by digest would then disagree about how many
        scenarios it contains, which is the kind of discrepancy that surfaces
        as an unreproducible count months later.
        """
        for name in ("terminal_predicates", "allowed_effects", "forbidden_effects"):
            value = getattr(self, name)
            if not isinstance(value, frozenset):
                if not isinstance(value, (list, tuple, set)):
                    raise ScenarioError(f"scenario.{name} must be a collection")
                object.__setattr__(self, name, frozenset(value))

    def validate(self) -> None:
        _identifier(self.scenario_id, "scenario.scenario_id")
        _identifier(self.initial_state_id, "scenario.initial_state_id")
        if not isinstance(self.title, str) or not 3 <= len(self.title.strip()) <= 160:
            raise ScenarioError("scenario.title must be 3 to 160 characters")
        if self.capability_area not in CAPABILITY_AREAS:
            raise ScenarioError(
                f"scenario.capability_area is not declared: {self.capability_area}"
            )
        if self.split not in SPLITS:
            raise ScenarioError("scenario.split is not declared")
        if self.trigger not in TRIGGERS:
            raise ScenarioError(f"scenario.trigger is not declared: {self.trigger}")
        if not isinstance(self.prompt, str) or len(self.prompt) > 4000:
            raise ScenarioError("scenario.prompt must be at most 4000 characters")
        if self.trigger == "user_utterance" and not self.prompt.strip():
            raise ScenarioError(
                "a user_utterance scenario needs something the user actually said"
            )
        if self.trigger != "user_utterance" and self.prompt.strip():
            # A scheduled scenario carrying an utterance is two triggers wearing
            # one label, and the harness would have to guess which one fired.
            raise ScenarioError(
                f"a {self.trigger} scenario must not carry a user prompt"
            )
        if self.injected_failure not in INJECTABLE_FAILURES:
            raise ScenarioError(
                f"scenario.injected_failure is not injectable: {self.injected_failure}"
            )
        if (
            not isinstance(self.terminal_predicates, frozenset)
            or not self.terminal_predicates
        ):
            raise ScenarioError(
                "a scenario with no terminal predicate can be neither passed nor failed"
            )
        unknown = self.terminal_predicates - TERMINAL_PREDICATES
        if unknown:
            raise ScenarioError(f"undeclared terminal predicates: {sorted(unknown)}")

        allowed = _effect_set(self.allowed_effects, "scenario.allowed_effects")
        forbidden = _effect_set(self.forbidden_effects, "scenario.forbidden_effects")
        overlap = allowed & forbidden
        if overlap:
            # Whichever check the scorer happens to run first would decide the
            # result, which is not a decision a harness gets to make.
            raise ScenarioError(
                f"effects are both allowed and forbidden: {sorted(overlap)}"
            )

        if (
            isinstance(self.time_budget_s, bool)
            or not isinstance(self.time_budget_s, (int, float))
            or not 0 < self.time_budget_s <= MAX_TIME_BUDGET_S
        ):
            raise ScenarioError(
                f"scenario.time_budget_s must be within (0, {MAX_TIME_BUDGET_S}]"
            )
        if (
            isinstance(self.score_weight, bool)
            or not isinstance(self.score_weight, (int, float))
            or not 0 < self.score_weight <= 10
        ):
            raise ScenarioError("scenario.score_weight must be within (0, 10]")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or self.seed < 0
        ):
            raise ScenarioError("scenario.seed must be a non-negative integer")
        if not isinstance(self.adversarial, bool):
            raise ScenarioError("scenario.adversarial must be boolean")

    def digest(self) -> str:
        """A stable content digest, so a frozen scenario can be proven frozen."""
        self.validate()
        payload = json.dumps(
            {
                "schema_version": SCENARIO_SCHEMA_VERSION,
                "scenario_id": self.scenario_id,
                "title": self.title,
                "capability_area": self.capability_area,
                "split": self.split,
                "prompt": self.prompt,
                "trigger": self.trigger,
                "initial_state_id": self.initial_state_id,
                "terminal_predicates": sorted(self.terminal_predicates),
                "allowed_effects": sorted(self.allowed_effects),
                "forbidden_effects": sorted(self.forbidden_effects),
                "injected_failure": self.injected_failure,
                "time_budget_s": float(self.time_budget_s),
                "score_weight": float(self.score_weight),
                "seed": self.seed,
                "adversarial": self.adversarial,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_SCENARIO_FIELDS = {
    "scenario_id",
    "title",
    "capability_area",
    "split",
    "prompt",
    "trigger",
    "initial_state_id",
    "terminal_predicates",
    "allowed_effects",
    "forbidden_effects",
    "injected_failure",
    "time_budget_s",
    "score_weight",
    "seed",
    "adversarial",
}

_REQUIRED_FIELDS = {
    "scenario_id",
    "title",
    "capability_area",
    "split",
    "prompt",
    "initial_state_id",
    "terminal_predicates",
}


def scenario_from_record(record: Any) -> Scenario:
    """Build a scenario from a record, refusing unknown or missing fields."""
    if not isinstance(record, dict):
        raise ScenarioError("scenario record must be an object")
    unknown = set(record) - _SCENARIO_FIELDS
    if unknown:
        raise ScenarioError(f"scenario record has unknown fields: {sorted(unknown)}")
    missing = _REQUIRED_FIELDS - set(record)
    if missing:
        raise ScenarioError(f"scenario record is missing fields: {sorted(missing)}")
    payload = dict(record)
    for key in ("terminal_predicates", "allowed_effects", "forbidden_effects"):
        if key in payload:
            value = payload[key]
            if not isinstance(value, (list, tuple, set, frozenset)):
                raise ScenarioError(f"scenario.{key} must be a collection")
            payload[key] = frozenset(value)
    scenario = Scenario(**payload)
    scenario.validate()
    return scenario


@dataclass(frozen=True)
class SuiteCoverage:
    """How a suite is distributed, so gaps are visible before results are.

    The counts are tuples of pairs rather than dicts: a `dict` field inside a
    frozen dataclass is still mutable, so a caller could edit a coverage report
    after it was produced and the "frozen" annotation would say otherwise.
    """

    total: int
    by_area: tuple[tuple[str, int], ...]
    by_split: tuple[tuple[str, int], ...]
    adversarial: int
    missing_areas: tuple[str, ...]

    def area_counts(self) -> dict[str, int]:
        return dict(self.by_area)

    def split_counts(self) -> dict[str, int]:
        return dict(self.by_split)


@dataclass(frozen=True)
class ScenarioSuite:
    """A validated, split-aware collection of scenarios."""

    revision: str
    scenarios: tuple[Scenario, ...]
    schema_version: int = SCENARIO_SCHEMA_VERSION
    _index: dict[str, Scenario] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def validate(self) -> None:
        self._validated_index()

    def _validated_index(self) -> dict[str, Scenario]:
        cached = self._index
        if cached is not None:
            return cached
        if self.schema_version != SCENARIO_SCHEMA_VERSION:
            raise ScenarioError("unsupported scenario schema version")
        if not isinstance(self.revision, str) or not self.revision.strip():
            raise ScenarioError("suite.revision must be a non-empty string")
        if not isinstance(self.scenarios, tuple) or not self.scenarios:
            raise ScenarioError("a suite must contain at least one scenario")
        index: dict[str, Scenario] = {}
        for scenario in self.scenarios:
            scenario.validate()
            if scenario.scenario_id in index:
                raise ScenarioError(f"duplicate scenario id: {scenario.scenario_id}")
            index[scenario.scenario_id] = scenario
        object.__setattr__(self, "_index", index)
        return index

    def coverage(self) -> SuiteCoverage:
        self.validate()
        by_area: dict[str, int] = {}
        by_split: dict[str, int] = {}
        adversarial = 0
        for scenario in self.scenarios:
            by_area[scenario.capability_area] = (
                by_area.get(scenario.capability_area, 0) + 1
            )
            by_split[scenario.split] = by_split.get(scenario.split, 0) + 1
            adversarial += scenario.adversarial
        return SuiteCoverage(
            total=len(self.scenarios),
            by_area=tuple(sorted(by_area.items())),
            by_split=tuple(sorted(by_split.items())),
            adversarial=adversarial,
            missing_areas=tuple(sorted(CAPABILITY_AREAS - set(by_area))),
        )

    def development_view(self) -> "ScenarioSuite":
        """The suite with the frozen split removed entirely.

        `select()` refuses to hand out held-out scenarios, but `.scenarios` is
        a public tuple and anything determined enough can read it. That guard
        is a guardrail against accident, not a boundary against intent — the
        same distinction D-024 draws about proximity gating. This is the real
        mitigation: tuning code is given a suite that does not contain the
        evaluation set at all, so there is nothing to peek at.
        """
        self.validate()
        kept = tuple(s for s in self.scenarios if s.split != "held_out")
        if not kept:
            raise ScenarioError("a development view would be empty")
        return ScenarioSuite(revision=f"{self.revision}+dev", scenarios=kept)

    def select(
        self,
        *,
        split: str,
        allow_held_out: bool = False,
        area: str | None = None,
    ) -> tuple[Scenario, ...]:
        """Take one split, refusing to hand out held-out data by accident.

        `allow_held_out` has no default that opens the box. Tuning code calls
        this without thinking about splits; that call must fail rather than
        silently return the evaluation set.
        """
        self.validate()
        if split not in SPLITS:
            raise ScenarioError(f"unknown split: {split}")
        if split == "held_out" and not allow_held_out:
            raise ScenarioError(
                "the held-out split is frozen; pass allow_held_out=True from a "
                "final evaluation run only"
            )
        if area is not None and area not in CAPABILITY_AREAS:
            raise ScenarioError(f"unknown capability area: {area}")
        return tuple(
            scenario
            for scenario in self.scenarios
            if scenario.split == split
            and (area is None or scenario.capability_area == area)
        )

    def digest(self, *, split: str | None = None) -> str:
        """Digest of the suite, or of one split, for freezing and manifests."""
        self.validate()
        chosen = sorted(
            (s for s in self.scenarios if split is None or s.split == split),
            key=lambda s: s.scenario_id,
        )
        if not chosen and split is not None:
            raise ScenarioError(f"no scenarios in split: {split}")
        rolling = hashlib.sha256()
        rolling.update(f"{SCENARIO_SCHEMA_VERSION}:{self.revision}".encode("utf-8"))
        for scenario in chosen:
            rolling.update(scenario.digest().encode("utf-8"))
        return rolling.hexdigest()


def suite_from_records(revision: str, records: Iterable[Any]) -> ScenarioSuite:
    suite = ScenarioSuite(
        revision=revision,
        scenarios=tuple(scenario_from_record(record) for record in records),
    )
    suite.validate()
    return suite


def load_suite(path: str | Path) -> ScenarioSuite:
    """Load and validate a suite file, failing closed on any drift."""
    location = Path(path)
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ScenarioError(f"suite could not be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise ScenarioError("suite file must contain an object")
    if payload.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise ScenarioError("suite file declares an unsupported schema version")
    revision = payload.get("revision")
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ScenarioError("suite file has no scenario list")
    suite = suite_from_records(revision if isinstance(revision, str) else "", scenarios)
    declared = payload.get("held_out_digest")
    if declared is not None:
        # A frozen split that no longer matches its recorded digest is the one
        # failure this contract exists to catch, so it is checked at load and
        # not left to whoever remembers.
        actual = suite.digest(split="held_out")
        if declared != actual:
            raise ScenarioError(
                "held-out split does not match its frozen digest; it has been "
                f"modified (declared {declared[:12]}…, actual {actual[:12]}…)"
            )
    return suite
