"""Turning a benchmark run into an artifact somebody else can check.

The rule this module exists to enforce is D-004 and D-023 together: a number is
a claim about a system only when a real model produced it, and a number from a
scripted driver is a claim about the harness. Both are worth writing down; they
must never be written down the same way.

So every report carries a `claim_class`:

- `system` — a real model was in the loop, through a cassette. This may be cited.
- `harness_proof` — a scripted or oracle driver produced it. It says the
  measurement path works. It says nothing about any model, and the artifact
  says so in its own body rather than in a README somebody may not read.
- `unavailable` — the run could not happen, with the reason and the exact
  command that would fix it.

The third one is the whole reason this file is not just `json.dump`. Thirteen of
seventeen contract metrics in this program are unmeasured, and a report format
with no way to say "not measured" turns every gap into either a fabricated zero
or a silent omission.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .cassettes import (
    DEFAULT_MODEL_MATRIX,
    RECORDING_ENV,
    CassetteError,
    ModelIdentity,
    cassette_path,
    load_cassette,
)
from .harness import (
    CassetteDriver,
    OracleDriver,
    ScenarioRunner,
    SuiteRun,
    run_suite,
)
from .provenance_digest import (
    code_revision,
    freeze_guard,
    implementation_digest,
    require_tracked,
)
from .scenarios import Scenario, ScenarioSuite
from .scoring import SuiteScore, require_release_gate, score_suite

BENCHMARK_REPORT_SCHEMA_VERSION = 1

CLAIM_CLASSES = ("system", "harness_proof", "unavailable")

# The sources whose behaviour a benchmark number depends on. If any of these
# change, the frozen result should be regenerated rather than trusted.
BENCHMARK_DIGESTED_SOURCES = (
    "src/open_llm_vtuber/evaluation/harness.py",
    "src/open_llm_vtuber/evaluation/scenarios.py",
    "src/open_llm_vtuber/evaluation/scoring.py",
    "src/open_llm_vtuber/evaluation/initial_states.py",
    "src/open_llm_vtuber/evaluation/benchmark_report.py",
    "src/open_llm_vtuber/authorization/kernel.py",
    "src/open_llm_vtuber/authorization/contracts.py",
    "src/open_llm_vtuber/authorization/profiles.py",
    "src/open_llm_vtuber/semantic_projection.py",
)

DEFAULT_CASSETTE_ROOT = "research/cassettes"


class BenchmarkReportError(RuntimeError):
    """A report cannot be produced honestly, so it is not produced."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _contract_revision() -> str:
    contract = json.loads(
        (_repo_root() / "research" / "contract.json").read_text("utf-8")
    )
    return str(contract["contract_revision"])


def _interval(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "value": round(value.value, 4),
        "low": round(value.low, 4),
        "high": round(value.high, 4),
        "n": value.n,
    }


@dataclass(frozen=True)
class ConditionReport:
    """One condition's outcome, or the reason it has none."""

    condition_id: str
    claim_class: str
    driver_id: str
    score: SuiteScore | None = None
    unrecorded: tuple[str, ...] = ()
    reason: str = ""
    remedy: str = ""

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "condition_id": self.condition_id,
            "claim_class": self.claim_class,
            "driver_id": self.driver_id,
        }
        if self.claim_class == "unavailable":
            record["status"] = "unavailable"
            record["reason"] = self.reason
            if self.remedy:
                record["remedy"] = self.remedy
            if self.unrecorded:
                record["unrecorded_scenarios"] = len(self.unrecorded)
            return record
        if self.score is None:  # pragma: no cover - guarded at construction
            raise BenchmarkReportError("a measured condition needs a score")
        passed, reasons = require_release_gate(self.score)
        record.update(
            {
                "status": "measured",
                "scored": self.score.scored,
                "full_task_success": _interval(self.score.full_task_success),
                "partial_credit": _interval(self.score.partial_credit),
                "forbidden_effect_count": self.score.forbidden_effect_count,
                "harness_error_count": self.score.harness_error_count,
                "by_area": [
                    {"area": area, "passed": passed_count, "total": total}
                    for area, passed_count, total in self.score.by_area
                ],
                "failure_taxonomy": [
                    {"stage": stage, "count": count}
                    for stage, count in self.score.failure_taxonomy
                ],
                "release_gate_met": passed,
                "release_gate_reasons": list(reasons),
            }
        )
        return record


def run_condition(
    scenarios: Sequence[Scenario],
    *,
    condition_id: str,
    model: ModelIdentity | None,
    cassette_root: str | Path = DEFAULT_CASSETTE_ROOT,
    runner: ScenarioRunner | None = None,
    seed: int = 0,
    split: str = "development",
) -> ConditionReport:
    """Run one condition, reporting `unavailable` rather than inventing a result.

    Passing `model=None` selects the oracle instrument check. Passing a model
    with no cassette produces an `unavailable` row naming the record command,
    which is the difference between an inventory and an excuse.
    """
    if model is None:
        run = run_suite(scenarios, OracleDriver(), runner=runner)
        return _report_from_run(
            run,
            scenarios,
            condition_id=condition_id,
            claim_class="harness_proof",
            seed=seed,
            split=split,
        )

    path = cassette_path(cassette_root, model)
    if not path.is_file():
        return ConditionReport(
            condition_id=condition_id,
            claim_class="unavailable",
            driver_id=f"cassette:{model.model_id}",
            reason=(
                f"no cassette exists for {model.model_id}; no completion from "
                "this model has ever been recorded, so nothing about it is "
                "measured"
            ),
            remedy=(
                f"set {RECORDING_ENV}=1 and run `uv run python "
                f"scripts/record_cassettes.py --model {model.model_id}` with that "
                "provider's credentials available"
            ),
        )
    try:
        store = load_cassette(path, expect=model)
    except CassetteError as exc:
        return ConditionReport(
            condition_id=condition_id,
            claim_class="unavailable",
            driver_id=f"cassette:{model.model_id}",
            reason=f"cassette for {model.model_id} is unusable: {exc}",
            remedy="re-record the cassette for this model",
        )

    run = run_suite(scenarios, CassetteDriver(store=store), runner=runner)
    if not run.complete:
        # A partly recorded suite has no success rate. D-029: reporting the
        # recorded half's rate is the most flattering reading of an incomplete
        # measurement, so it is refused rather than qualified.
        return ConditionReport(
            condition_id=condition_id,
            claim_class="unavailable",
            driver_id=run.driver_id,
            unrecorded=run.unrecorded,
            reason=(
                f"{len(run.unrecorded)} of {len(list(scenarios))} scenarios have "
                "no recorded completion, so this split has no success rate"
            ),
            remedy=(
                "re-record the cassette; a partial cassette cannot be scored as "
                "a partial result"
            ),
        )
    return _report_from_run(
        run,
        scenarios,
        condition_id=condition_id,
        claim_class="system",
        seed=seed,
        split=split,
    )


def _report_from_run(
    run: SuiteRun,
    scenarios: Sequence[Scenario],
    *,
    condition_id: str,
    claim_class: str,
    seed: int,
    split: str,
) -> ConditionReport:
    score = score_suite(
        scenarios,
        run.results,
        condition_id=condition_id,
        split=split,
        seed=seed,
    )
    return ConditionReport(
        condition_id=condition_id,
        claim_class=claim_class,
        driver_id=run.driver_id,
        score=score,
        unrecorded=run.unrecorded,
    )


def build_report(
    suite: ScenarioSuite,
    conditions: Iterable[ConditionReport],
    *,
    split: str,
    seed: int,
    matrix: Sequence[ModelIdentity] = DEFAULT_MODEL_MATRIX,
    allow_dirty: bool = False,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the artifact, refusing to freeze from an unrecoverable tree."""
    revision = freeze_guard(allow_dirty=allow_dirty, root=_repo_root())
    require_tracked(BENCHMARK_DIGESTED_SOURCES, root=_repo_root())
    rows = tuple(conditions)
    measured = [row for row in rows if row.claim_class == "system"]
    return {
        "schema_version": BENCHMARK_REPORT_SCHEMA_VERSION,
        "artifact": "part12-benchmark",
        "generated_at": (generated_at or datetime.now(timezone.utc)).isoformat(),
        "contract_revision": _contract_revision(),
        "code_revision": revision.revision,
        "worktree_dirty": revision.dirty,
        "implementation_digest": implementation_digest(
            BENCHMARK_DIGESTED_SOURCES, root=_repo_root()
        ),
        "suite_revision": suite.revision,
        "suite_digest": suite.digest(),
        "split": split,
        "split_digest": suite.digest(split=split),
        "seed": seed,
        "declared_model_matrix": [identity.as_record() for identity in matrix],
        # The headline claim of the whole artifact, stated before any number so
        # a reader cannot reach a percentage without first reading what it is a
        # percentage of.
        "claim": (
            "system results measured against a real model"
            if measured
            else "no system claim: every condition below is a harness proof or "
            "unavailable, because no language model has been recorded against "
            "this suite"
        ),
        "conditions": [row.as_record() for row in rows],
    }


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    location = Path(path)
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return location


def matrix_status(
    cassette_root: str | Path = DEFAULT_CASSETTE_ROOT,
    matrix: Sequence[ModelIdentity] = DEFAULT_MODEL_MATRIX,
) -> dict[str, Any]:
    """How much of the declared model matrix has evidence behind it."""
    from .cassettes import describe_matrix

    rows = describe_matrix(cassette_root, matrix)
    recorded = sum(1 for row in rows if row["status"] == "recorded")
    families = {row["family"] for row in rows if row["status"] == "recorded"}
    return {
        "declared_models": len(rows),
        "recorded_models": recorded,
        "recorded_families": sorted(families),
        # Part 12's own gate: three families, because one is a prompt.
        "meets_multi_family_gate": len(families) >= 3,
        "rows": list(rows),
        "code_revision": code_revision(root=_repo_root()).describe(),
    }


__all__ = [
    "BENCHMARK_DIGESTED_SOURCES",
    "BENCHMARK_REPORT_SCHEMA_VERSION",
    "CLAIM_CLASSES",
    "DEFAULT_CASSETTE_ROOT",
    "BenchmarkReportError",
    "ConditionReport",
    "build_report",
    "matrix_status",
    "run_condition",
    "write_report",
]
