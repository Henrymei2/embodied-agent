"""Skill tools for the embodied companion.

These belong to the `tool rack`: the agent walks over to write down something it
has learned to do, as a procedure it can follow later. A skill is a proposal
until the user approves it from the rack's hover window — approved skills are
retrieved into the system prompt, so approving one really does change how the
agent behaves afterwards.

There is deliberately no tool here that approves a skill, the file tools refuse
to write anywhere under `skills/`, and the shell sandbox mounts that directory
read-only. Self-evolution therefore always runs through the user.

Run standalone for a quick sanity check:
    uv run python mcp_servers/skill_tools.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.skill_state import (
    SKILL_STATUSES,
    at_skill_limit,
    find_skill,
    load_skills,
    save_skill,
    slugify,
)

_MAX_NAME = 80
_MAX_PURPOSE = 600
_MAX_WHEN = 400
_MAX_STEPS = 15
_MAX_STEP = 300
_MAX_NOTES = 1_500

mcp = FastMCP("skills")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field} cannot be empty")
    if len(cleaned) > maximum:
        raise ValueError(f"{field} cannot exceed {maximum} characters")
    return cleaned


def _steps(steps: Any) -> list[str]:
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list of strings")
    if len(steps) > _MAX_STEPS:
        raise ValueError(f"a skill can list at most {_MAX_STEPS} steps")
    return [_text(step, "step", _MAX_STEP) for step in steps]


@mcp.tool()
def draft_skill(
    name: str,
    purpose: str,
    when_to_use: str,
    steps: list[str],
    notes: str = "",
) -> str:
    """Propose a new skill for yourself. Requires standing at the tool rack.

    Write the skill down the way you would want to read it later: what it is
    for, when it applies, and the concrete steps. It is saved as a draft and
    appears on the tool rack for the user to approve or decline. Only after
    approval does it become part of what you know.

    Args:
        name: Short name, for example "summarize a long thread".
        purpose: What the skill accomplishes.
        when_to_use: The situation that should trigger it.
        steps: Ordered, concrete steps.
        notes: Optional caveats or things that went wrong before.
    """
    try:
        slug = slugify(name)
        existing = find_skill(slug)
        if existing is not None and existing["status"] in {"pending", "active"}:
            raise ValueError(
                f"a skill named '{slug}' already exists; use revise_skill instead"
            )
        if at_skill_limit():
            raise ValueError("the tool rack is full; retire a skill first")

        now = _now()
        skill = {
            "slug": slug,
            "name": _text(name, "name", _MAX_NAME),
            "purpose": _text(purpose, "purpose", _MAX_PURPOSE),
            "when_to_use": _text(when_to_use, "when_to_use", _MAX_WHEN),
            "steps": _steps(steps),
            "notes": " ".join(str(notes).split())[:_MAX_NOTES],
            "revision": 1,
            "created_at": now,
            "updated_at": now,
            "decided_at": None,
            "decided_by": None,
        }
        save_skill(skill, "pending")
        return json.dumps(
            {
                "ok": True,
                "slug": slug,
                "status": "pending",
                "note": "Waiting for the user to approve it at the tool rack.",
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def list_skills(status: str = "") -> str:
    """List your skills and whether they are approved. Requires the tool rack.

    Args:
        status: Optional filter: "pending", "active", "declined", or "retired".
    """
    try:
        wanted = str(status).strip().lower()
        if wanted and wanted not in SKILL_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(SKILL_STATUSES)}")
        skills = load_skills()
        if wanted:
            skills = [skill for skill in skills if skill["status"] == wanted]
        return json.dumps(
            {
                "count": len(skills),
                "skills": [
                    {
                        "slug": skill["slug"],
                        "name": skill.get("name"),
                        "status": skill["status"],
                        "when_to_use": skill.get("when_to_use", ""),
                        "revision": skill.get("revision", 1),
                    }
                    for skill in skills
                ],
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def get_skill(slug: str) -> str:
    """Read one skill in full, including its steps. Requires the tool rack."""
    try:
        skill = find_skill(slugify(slug))
        if skill is None:
            raise ValueError(f"no skill named '{slug}'")
        return json.dumps(
            {
                "slug": skill["slug"],
                "name": skill.get("name"),
                "status": skill["status"],
                "purpose": skill.get("purpose", ""),
                "when_to_use": skill.get("when_to_use", ""),
                "steps": skill.get("steps", []),
                "body": skill.get("body", ""),
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def revise_skill(
    slug: str,
    purpose: str | None = None,
    when_to_use: str | None = None,
    steps: list[str] | None = None,
    notes: str | None = None,
) -> str:
    """Rewrite a skill. Requires standing at the tool rack.

    Only the supplied fields change. The revision goes back to the user as a
    pending draft — including for a skill that is currently active, which keeps
    working until the new version is approved.
    """
    try:
        target = slugify(slug)
        skill = find_skill(target)
        if skill is None:
            raise ValueError(f"no skill named '{slug}'")
        if skill["status"] == "retired":
            raise ValueError("a retired skill cannot be revised; draft a new one")

        updated = dict(skill)
        if purpose is not None:
            updated["purpose"] = _text(purpose, "purpose", _MAX_PURPOSE)
        if when_to_use is not None:
            updated["when_to_use"] = _text(when_to_use, "when_to_use", _MAX_WHEN)
        if steps is not None:
            updated["steps"] = _steps(steps)
        if notes is not None:
            updated["notes"] = " ".join(str(notes).split())[:_MAX_NOTES]

        was_active = skill["status"] == "active"
        updated["revision"] = int(skill.get("revision", 1)) + 1
        updated["updated_at"] = _now()
        updated["decided_at"] = None
        updated["decided_by"] = None
        save_skill(updated, "pending")
        return json.dumps(
            {
                "ok": True,
                "slug": target,
                "status": "pending",
                "revision": updated["revision"],
                "note": (
                    "The previous version stops applying until the user approves "
                    "this revision."
                    if was_active
                    else "Waiting for the user to approve it at the tool rack."
                ),
            },
            ensure_ascii=False,
        )
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


@mcp.tool()
def retire_skill(slug: str, reason: str = "") -> str:
    """Stop using a skill. Requires standing at the tool rack.

    Retiring only ever removes a capability, so it needs no approval. The file
    is kept, and a retired skill can be replaced with a fresh draft.

    Args:
        slug: The skill to retire.
        reason: Optional short note about why.
    """
    try:
        target = slugify(slug)
        skill = find_skill(target)
        if skill is None:
            raise ValueError(f"no skill named '{slug}'")
        if skill["status"] == "retired":
            raise ValueError(f"skill '{target}' is already retired")

        updated = dict(skill)
        updated["updated_at"] = _now()
        if str(reason).strip():
            updated["notes"] = (
                f"{updated.get('notes', '')}\nRetired: "
                f"{' '.join(str(reason).split())[:_MAX_NOTES]}"
            ).strip()
        save_skill(updated, "retired")
        return json.dumps({"ok": True, "slug": target, "status": "retired"})
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
