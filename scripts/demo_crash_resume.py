"""The crash-and-resume beat, as one command you can run on stage.

Part 4 proves this in tests: a plan can be killed at any transition and resume
without repeating a side effect. A test is the wrong medium for an audience —
this runs the same protocol through the same executor, kills the process at a
real transition, then starts a *new* store that can only read what reached
disk, and shows that the side effect is not repeated.

It runs on a temporary workspace with a deterministic adapter, so it needs no
model, no network, and touches nothing real. That is deliberate: the riskiest
demo is one that depends on the thing being demonstrated.

    uv run python scripts/demo_crash_resume.py                    # narrated
    uv run python scripts/demo_crash_resume.py --fast             # no pauses
    uv run python scripts/demo_crash_resume.py --phase after_action
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.open_llm_vtuber.autonomy.plan_execution import (  # noqa: E402
    ExecutionOutcome,
    InjectedProcessCrash,
    ResumablePlanExecutor,
)
from src.open_llm_vtuber.autonomy.plan_state import (  # noqa: E402
    ResumablePlanStore,
    build_plan,
    build_plan_step,
)

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
RULE = "=" * 72

# The transitions a process can die at. Every one of them is covered by the
# Part 4 test suite; `after_action` is the frightening one, because the side
# effect has already reached the outside world.
PHASES = (
    "after_claim",
    "after_before_checkpoint",
    "after_action",
    "after_after_checkpoint",
    "after_commit",
)


def _plan():
    return build_plan(
        plan_id="demo-plan",
        intention_record_id="intention:demo-plan",
        goal_ref="goal:send-the-summary",
        work_class="background",
        steps=[
            build_plan_step(
                step_id="send-the-summary",
                action_ref="calendar.write",
                arguments={"private_title": "never shown"},
                side_effecting=True,
                risk="low",
                estimated_cost=1,
                max_attempts=2,
            )
        ],
        deadline_at=(NOW + timedelta(hours=1)).isoformat(),
        retry_budget=2,
        repair_budget=1,
        cost_budget=5,
        created_at=NOW.isoformat(),
    )


async def run(phase: str, beat: float) -> int:
    def say(text: str = "", *, pause: float = 0.0) -> None:
        print(text)
        if pause:
            time.sleep(pause)

    workspace = Path(tempfile.mkdtemp(prefix="demo-crash-"))
    try:
        say(RULE)
        say("CRASH AND RESUME")
        say("A side effect that already happened must not happen twice,")
        say("even when the process dies in the middle of it.")
        say(RULE, pause=beat)

        # The external world. `physical_effects` counts what actually reached
        # it, which is the number the whole beat is about.
        effects: dict[str, ExecutionOutcome] = {}
        physical_effects = 0

        async def action(_step, idempotency_key):
            nonlocal physical_effects
            if idempotency_key not in effects:
                physical_effects += 1
                effects[idempotency_key] = ExecutionOutcome.from_summary(
                    success=True,
                    outcome_ref="outcome-one",
                    summary="calendar entry exists",
                )
            return effects[idempotency_key]

        store = ResumablePlanStore(workspace, clock=lambda: NOW)
        store.create(_plan())
        say("\n1. One consequential step, with a stable idempotency key.")
        say("   plan  : demo-plan")
        say("   step  : send-the-summary  (side-effecting)")
        say(f"   we will kill the process at: {phase}", pause=beat)

        crashed = False

        def crash_hook(current_phase, _plan):
            nonlocal crashed
            if current_phase == phase and not crashed:
                crashed = True
                raise InjectedProcessCrash(current_phase)

        say("\n2. Running, and dying mid-transition.")
        executor = ResumablePlanExecutor(store)
        for _ in range(8):
            try:
                result = await executor.resume_once(
                    "demo-plan",
                    owner_id="demo-owner",
                    fencing_token=1,
                    action=action,
                    condition_probe=lambda _ref: True,
                    crash_hook=crash_hook,
                )
            except InjectedProcessCrash:
                break
            if result.disposition == "terminal":
                break

        if not crashed:
            say("   FAILED: the crash was never injected.")
            return 1
        say(f"   *** process died at {phase} ***")
        say(
            f"   side effects that reached the world so far: {physical_effects}",
            pause=beat,
        )

        # --- a genuinely new process ----------------------------------------
        say("\n3. A new process starts. It can only read what reached disk.")
        del store, executor
        restarted = ResumablePlanStore(workspace, clock=lambda: NOW)
        terminal = await ResumablePlanExecutor(restarted).run_to_stable(
            "demo-plan",
            owner_id="demo-owner",
            fencing_token=1,
            action=action,
            condition_probe=lambda _ref: True,
        )
        say(f"   recovered plan state : {terminal.state}", pause=beat)

        say("\n4. The count that matters.")
        say(f"   side effects total   : {physical_effects}")
        if physical_effects != 1:
            say(f"   FAILED: expected exactly 1, got {physical_effects}.")
            return 1

        say("\n" + RULE)
        say("Exactly one. The recovery came from state written to disk before")
        say("the crash, not from anything held in memory -- and the same")
        say("idempotency key stopped the second attempt reaching the world.")
        say(RULE)
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="no pauses")
    parser.add_argument("--phase", choices=PHASES, default="after_action")
    parser.add_argument(
        "--all", action="store_true", help="run every crash phase in turn"
    )
    args = parser.parse_args()
    beat = 0.0 if args.fast else 1.3

    phases = PHASES if args.all else (args.phase,)
    for phase in phases:
        if asyncio.run(run(phase, 0.0 if args.all else beat)) != 0:
            return 1
    if args.all:
        print(f"\nAll {len(PHASES)} crash phases resumed with exactly one effect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
