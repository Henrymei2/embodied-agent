"""Planning tools for the embodied companion.

These tools belong to the `writing desk`: the agent has to walk there to draft or
revise a plan. Plans live in `<workspace>/plans/plans.json`.

A plan is a proposal, not a decision. Every new plan starts `pending`, and only
the user can approve or decline it — from the hover window on the writing desk,
through a route that is the sole writer of that field. There is deliberately no
tool here that approves a plan, so the agent cannot authorize its own work.

Run standalone for a quick sanity check:
    uv run python mcp_servers/plan_tools.py
"""

from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.furniture_state import PLAN_STATUSES, load_plans, save_plans

_MAX_PLANS = 200
_MAX_TITLE = 160
_MAX_GOAL = 2_000
_MAX_STEPS = 20
_MAX_STEP = 400
_MAX_NOTES = 2_000
_MAX_RESULTS = 50

mcp = FastMCP("plan")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return cleaned


def _validate_steps(steps: Any) -> list[str]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list of strings")
    if len(steps) > _MAX_STEPS:
        raise ValueError(f"a plan can list at most {_MAX_STEPS} steps")
    return [_validate_text(step, "step", _MAX_STEP) for step in steps]


def _find(plans: list[dict[str, Any]], plan_id: str) -> int:
    if not isinstance(plan_id, str) or not plan_id or len(plan_id) > 80:
        raise ValueError("plan_id must be a non-empty string")
    index = next(
        (
            position
            for position, plan in enumerate(plans)
            if plan.get("plan_id") == plan_id
        ),
        None,
    )
    if index is None:
        raise ValueError(f"plan '{plan_id}' was not found")
    return index


@mcp.tool()
def write_plan(title: str, goal: str, steps: list[str], notes: str = "") -> str:
    """Draft a plan for the user to approve. Requires standing at the writing desk.

    The plan is saved as `pending` and appears on the writing desk, where the
    user approves or declines it. Do not start work that needs approval until
    `list_plans` reports the plan as approved.

    Args:
        title: Short name for the plan.
        goal: What the plan is meant to achieve, and why.
        steps: Ordered list of concrete steps.
        notes: Optional caveats, risks, or open questions.
    """
    try:
        plans = load_plans()
        if len(plans) >= _MAX_PLANS:
            raise ValueError(f"the desk holds at most {_MAX_PLANS} plans")
        now = _now()
        plan = {
            "plan_id": uuid.uuid4().hex[:8],
            "title": _validate_text(title, "title", _MAX_TITLE),
            "goal": _validate_text(goal, "goal", _MAX_GOAL),
            "steps": _validate_steps(steps),
            "notes": str(notes).strip()[:_MAX_NOTES],
            "status": "pending",
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "decided_at": None,
            "decided_by": None,
        }
        plans.append(plan)
        save_plans(plans)
        return json.dumps({"ok": True, "plan": plan}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def list_plans(status: str = "", limit: int = 20) -> str:
    """List plans and their approval state. Requires the writing desk.

    Args:
        status: Optional filter: "pending", "approved", "declined", or "withdrawn".
        limit: Maximum number of plans to return.
    """
    try:
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        limit = max(1, min(limit, _MAX_RESULTS))
        wanted = str(status).strip().lower()
        if wanted and wanted not in PLAN_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(PLAN_STATUSES)}")

        plans = load_plans()
        if wanted:
            plans = [plan for plan in plans if plan.get("status") == wanted]
        # Newest first: a plan the user just decided on matters most.
        plans.sort(key=lambda plan: str(plan.get("updated_at", "")), reverse=True)
        return json.dumps(
            {
                "count": len(plans),
                "plans": [
                    {
                        "plan_id": plan.get("plan_id"),
                        "title": plan.get("title"),
                        "status": plan.get("status"),
                        "steps": plan.get("steps", []),
                        "updated_at": plan.get("updated_at"),
                    }
                    for plan in plans[:limit]
                ],
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def get_plan(plan_id: str) -> str:
    """Read one plan in full, including notes. Requires the writing desk."""
    try:
        plans = load_plans()
        return json.dumps({"plan": plans[_find(plans, plan_id)]}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def revise_plan(
    plan_id: str,
    title: str | None = None,
    goal: str | None = None,
    steps: list[str] | None = None,
    notes: str | None = None,
) -> str:
    """Rewrite a plan that is pending or was declined. Requires the writing desk.

    Only the supplied fields change. A declined plan becomes pending again, so
    the user decides on the new version rather than inheriting the old verdict.
    An approved plan cannot be edited: write a new plan instead.
    """
    try:
        plans = load_plans()
        index = _find(plans, plan_id)
        plan = dict(plans[index])
        if plan.get("status") == "approved":
            raise ValueError(
                "an approved plan cannot be revised; write a new plan instead"
            )
        if plan.get("status") == "withdrawn":
            raise ValueError("a withdrawn plan cannot be revised")

        if title is not None:
            plan["title"] = _validate_text(title, "title", _MAX_TITLE)
        if goal is not None:
            plan["goal"] = _validate_text(goal, "goal", _MAX_GOAL)
        if steps is not None:
            plan["steps"] = _validate_steps(steps)
        if notes is not None:
            plan["notes"] = str(notes).strip()[:_MAX_NOTES]

        plan["status"] = "pending"
        plan["decided_at"] = None
        plan["decided_by"] = None
        plan["revision"] = int(plan.get("revision", 1)) + 1
        plan["updated_at"] = _now()
        plans[index] = plan
        save_plans(plans)
        return json.dumps({"ok": True, "plan": plan}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def withdraw_plan(plan_id: str, reason: str = "") -> str:
    """Retract a plan you no longer intend to follow. Requires the writing desk.

    Args:
        plan_id: The plan to retract.
        reason: Optional short explanation, kept with the plan.
    """
    try:
        plans = load_plans()
        index = _find(plans, plan_id)
        plan = dict(plans[index])
        if plan.get("status") == "withdrawn":
            raise ValueError(f"plan '{plan_id}' is already withdrawn")
        plan["status"] = "withdrawn"
        plan["updated_at"] = _now()
        if str(reason).strip():
            plan["withdraw_reason"] = str(reason).strip()[:_MAX_NOTES]
        plans[index] = plan
        save_plans(plans)
        return json.dumps({"ok": True, "plan_id": plan_id}, ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
