"""Deterministic Part 4 recovery ablation over frozen content-free fixtures."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..autonomy.plan_execution import (
    ExecutionOutcome,
    RepairProposal,
    ResumablePlanExecutor,
    retry_step,
)
from ..autonomy.plan_state import (
    CounterfactualAssessment,
    ResumablePlanStore,
    build_plan,
    build_plan_step,
)

PART4_FIXTURE_SCHEMA_VERSION = 1
PART4_REPORT_SCHEMA_VERSION = 1
_SCENARIO_KEYS = {
    "scenario_id",
    "action_ref",
    "allowed_action_refs",
    "side_effecting",
    "risk",
    "failure_mode",
    "repair_mode",
}


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _implementation_digest() -> str:
    root = Path(__file__).resolve().parents[3]
    digest = hashlib.sha256()
    for relative in (
        "src/open_llm_vtuber/autonomy/plan_state.py",
        "src/open_llm_vtuber/autonomy/plan_execution.py",
        "src/open_llm_vtuber/evaluation/part4.py",
    ):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update((root / relative).read_bytes())
        digest.update(b"\x00")
    return digest.hexdigest()


def _contract_revision() -> str:
    root = Path(__file__).resolve().parents[3]
    contract = json.loads((root / "research" / "contract.json").read_text("utf-8"))
    return str(contract["contract_revision"])


def load_part4_fixture(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "fixture_id",
        "frozen_at",
        "scenarios",
    }:
        raise ValueError("Part 4 fixture has an invalid root schema")
    if payload["schema_version"] != PART4_FIXTURE_SCHEMA_VERSION:
        raise ValueError("unsupported Part 4 fixture schema")
    if not isinstance(payload["scenarios"], list) or not payload["scenarios"]:
        raise ValueError("Part 4 fixture requires scenarios")
    ids: set[str] = set()
    for index, scenario in enumerate(payload["scenarios"]):
        if not isinstance(scenario, dict) or set(scenario) != _SCENARIO_KEYS:
            raise ValueError(f"Part 4 fixture scenario {index} has invalid keys")
        scenario_id = scenario["scenario_id"]
        if not isinstance(scenario_id, str) or not scenario_id:
            raise ValueError("Part 4 scenario requires an id")
        if scenario_id in ids:
            raise ValueError(f"duplicate Part 4 scenario id: {scenario_id}")
        ids.add(scenario_id)
        if scenario["failure_mode"] not in {"none", "precondition"}:
            raise ValueError("unsupported Part 4 failure mode")
        if scenario["repair_mode"] not in {
            "none",
            "satisfy_precondition",
            "scope_expansion",
            "decline",
        }:
            raise ValueError("unsupported Part 4 repair mode")
        allowed = scenario["allowed_action_refs"]
        if not isinstance(allowed, list) or not allowed:
            raise ValueError("Part 4 scenario requires allowed actions")
    return payload


async def _run_scenario(
    scenario: dict[str, Any], *, repair_enabled: bool
) -> dict[str, Any]:
    now = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="part4-eval-") as directory:
        store = ResumablePlanStore(Path(directory), clock=lambda: now)
        precondition_ref = f"fixture:{scenario['scenario_id']}:ready"
        step = build_plan_step(
            step_id=f"step:{scenario['scenario_id']}",
            action_ref=scenario["action_ref"],
            precondition_refs=(
                [precondition_ref] if scenario["failure_mode"] == "precondition" else []
            ),
            side_effecting=bool(scenario["side_effecting"]),
            risk=scenario["risk"],
            estimated_cost=1,
            max_attempts=2,
        )
        plan = build_plan(
            plan_id=f"plan:{scenario['scenario_id']}",
            intention_record_id=f"intention:{scenario['scenario_id']}",
            goal_ref=f"goal:{scenario['scenario_id']}",
            work_class="background",
            steps=[step],
            deadline_at=(now + timedelta(minutes=5)).isoformat(),
            retry_budget=2,
            repair_budget=1,
            cost_budget=5,
            created_at=now.isoformat(),
        )
        store.create(plan)
        condition_ready = scenario["failure_mode"] == "none"
        effects: dict[str, ExecutionOutcome] = {}
        calls = 0
        violations = 0

        async def action(current, idempotency_key):
            nonlocal calls, violations
            calls += 1
            if current.action_ref not in scenario["allowed_action_refs"]:
                violations += 1
            if idempotency_key not in effects:
                effects[idempotency_key] = ExecutionOutcome.from_summary(
                    success=True,
                    outcome_ref=f"outcome:{scenario['scenario_id']}",
                    summary="fixture_outcome",
                )
            return effects[idempotency_key]

        async def repair(_plan, failed):
            nonlocal condition_ready
            mode = scenario["repair_mode"]
            if not repair_enabled or mode in {"none", "decline"}:
                return None
            if mode == "satisfy_precondition":
                condition_ready = True
                replacement = retry_step(
                    failed,
                    step_id=f"repair-step:{scenario['scenario_id']}",
                )
            else:
                replacement = build_plan_step(
                    step_id=f"repair-step:{scenario['scenario_id']}",
                    action_ref="forbidden.external_action",
                    side_effecting=True,
                    risk=failed.risk,
                )
            return RepairProposal(
                failure_ref=failed.step_id,
                replacement_steps=(replacement,),
            )

        async def counterfactual(_step):
            return CounterfactualAssessment(
                decision="allow",
                reason_code="fixture_counterfactual",
                alternatives_evaluated=2,
                evaluated_at=now.isoformat(),
            )

        final = await ResumablePlanExecutor(store, max_passes=32).run_to_stable(
            plan.plan_id,
            owner_id="fixture-owner",
            fencing_token=1,
            action=action,
            condition_probe=lambda _ref: condition_ready,
            counterfactual=counterfactual,
            repair_planner=repair if repair_enabled else None,
        )
        return {
            "scenario_id": scenario["scenario_id"],
            "success": final.state == "completed",
            "terminal_state": final.state,
            "repair_count": len(final.repairs),
            "action_calls": calls,
            "distinct_effects": len(effects),
            "policy_violations": violations,
        }


async def run_part4_repair_evaluation(path: Path) -> dict[str, Any]:
    fixture = load_part4_fixture(path)
    raw = Path(path).read_bytes()
    conditions: dict[str, Any] = {}
    for name, repair_enabled in (("repair_off", False), ("repair_on", True)):
        scenarios = [
            await _run_scenario(scenario, repair_enabled=repair_enabled)
            for scenario in fixture["scenarios"]
        ]
        conditions[name] = {
            "success_count": sum(item["success"] for item in scenarios),
            "policy_violation_count": sum(
                item["policy_violations"] for item in scenarios
            ),
            "duplicate_effect_count": sum(
                max(0, item["distinct_effects"] - 1) for item in scenarios
            ),
            "scenarios": scenarios,
        }
    off = conditions["repair_off"]
    on = conditions["repair_on"]
    return {
        "schema_version": PART4_REPORT_SCHEMA_VERSION,
        "contract_revision": _contract_revision(),
        "implementation_digest": _implementation_digest(),
        "fixture_id": fixture["fixture_id"],
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "seed": 0,
        "scenario_count": len(fixture["scenarios"]),
        "conditions": conditions,
        "acceptance": {
            "repair_improves_success": on["success_count"] > off["success_count"],
            "repair_does_not_increase_policy_violations": (
                on["policy_violation_count"] <= off["policy_violation_count"]
            ),
            "no_duplicate_effects": on["duplicate_effect_count"] == 0,
        },
    }
