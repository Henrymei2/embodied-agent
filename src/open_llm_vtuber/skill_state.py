"""Agent-authored skills, and the review gate they pass through.

A skill is a written procedure the agent proposes for itself: "when X comes up,
do Y". Approved skills live in `<workspace>/skills/` and are retrieved into the
system prompt, so a skill genuinely changes how the agent behaves later. That is
exactly why the agent cannot put one there on its own.

* `skills/drafts/<slug>.md` — proposed or declined; never retrieved
* `skills/<slug>.md`        — approved and active; retrieved into the prompt
* `skills/retired/<slug>.md` — withdrawn by the agent; never retrieved

Only `record_skill_decision` — reached from the tool rack's hover window, and
from nowhere else — promotes a draft into the active directory. The agent's own
file tools refuse to write anywhere under `skills/`, and the shell sandbox
mounts that directory read-only, so the gate holds even though the agent can
otherwise write freely in its workspace.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

SKILL_STATUSES = ("pending", "active", "declined", "retired")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)
_MAX_SKILL_BYTES = 20_000
_MAX_SKILLS = 100
_SNAPSHOT_LIMIT = 6


def workspace_root() -> Path:
    """Resolve the active workspace without creating it.

    The workspace package is imported lazily: the retriever in that package
    imports this module to know which skills to skip, so importing it back at
    module level would be circular.
    """
    configured = os.environ.get("EMBODIED_AGENT_WORKSPACE")
    if configured:
        return Path(configured).expanduser()
    from .workspace import DEFAULT_WORKSPACE_DIR

    return DEFAULT_WORKSPACE_DIR


def skills_dir() -> Path:
    return workspace_root() / "skills"


def drafts_dir() -> Path:
    return skills_dir() / "drafts"


def retired_dir() -> Path:
    return skills_dir() / "retired"


# Subdirectories of `skills/` that must never reach the prompt.
EXCLUDED_SKILL_SUBDIRS = ("drafts", "retired")


def slugify(name: str) -> str:
    """Turn a skill name into a safe file stem, or raise ValueError."""
    if not isinstance(name, str):
        raise ValueError("skill name must be a string")
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not _SLUG_RE.fullmatch(slug):
        raise ValueError(
            "skill name must contain letters or digits and be at most 60 characters"
        )
    return slug


def skill_path(slug: str, status: str) -> Path:
    """Where a skill with this status lives."""
    if status == "active":
        return skills_dir() / f"{slug}.md"
    if status == "retired":
        return retired_dir() / f"{slug}.md"
    # Both pending and declined drafts wait in the same place.
    return drafts_dir() / f"{slug}.md"


def render_skill(skill: dict[str, Any]) -> str:
    """Serialize a skill to frontmatter plus a readable body."""
    metadata = {
        key: skill.get(key)
        for key in (
            "name",
            "slug",
            "status",
            "purpose",
            "when_to_use",
            "revision",
            "created_at",
            "updated_at",
            "decided_at",
            "decided_by",
        )
    }
    lines = [
        "---",
        yaml.safe_dump(metadata, sort_keys=False, allow_unicode=True).strip(),
        "---",
        "",
        f"# {skill.get('name', skill.get('slug', 'skill'))}",
        "",
        f"{skill.get('purpose', '')}".strip(),
        "",
        "## When to use",
        "",
        f"{skill.get('when_to_use', '')}".strip(),
        "",
        "## Steps",
        "",
    ]
    for index, step in enumerate(skill.get("steps", []), start=1):
        lines.append(f"{index}. {step}")
    notes = str(skill.get("notes") or "").strip()
    if notes:
        lines += ["", "## Notes", "", notes]
    return "\n".join(lines).rstrip() + "\n"


def parse_skill(path: Path) -> dict[str, Any] | None:
    """Read one skill file, returning None when it is unreadable or foreign."""
    try:
        text = path.read_text(encoding="utf-8")[:_MAX_SKILL_BYTES]
    except OSError as exc:
        logger.warning(f"Skills: could not read {path}: {exc}")
        return None

    match = _FRONTMATTER_RE.match(text)
    if not match:
        # A hand-written skill file without frontmatter still counts as active
        # content; describe it rather than hiding it from the user.
        return {
            "slug": path.stem,
            "name": path.stem,
            "status": "active" if path.parent == skills_dir() else "pending",
            "purpose": "",
            "when_to_use": "",
            "steps": [],
            "body": text,
            "managed": False,
        }

    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"Skills: invalid frontmatter in {path}: {exc}")
        return None
    if not isinstance(metadata, dict):
        return None

    body = match.group(2)
    steps = [
        re.sub(r"^\d+\.\s*", "", line).strip()
        for line in body.splitlines()
        if re.match(r"^\d+\.\s+\S", line)
    ]
    return {
        **metadata,
        "slug": str(metadata.get("slug") or path.stem),
        "name": str(metadata.get("name") or path.stem),
        "status": str(metadata.get("status") or "pending"),
        "steps": steps,
        "body": body.strip(),
        "managed": True,
    }


def load_skills() -> list[dict[str, Any]]:
    """Return every skill, with its status corrected to match its location."""
    skills: list[dict[str, Any]] = []
    for directory, fallback_status in (
        (skills_dir(), "active"),
        (drafts_dir(), "pending"),
        (retired_dir(), "retired"),
    ):
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.md")):
            skill = parse_skill(path)
            if skill is None:
                continue
            # Location wins over frontmatter: a file moved by hand must not keep
            # claiming to be approved.
            if fallback_status == "active":
                skill["status"] = "active"
            elif fallback_status == "retired":
                skill["status"] = "retired"
            elif skill.get("status") not in {"pending", "declined"}:
                skill["status"] = "pending"
            skill["path"] = str(path)
            skills.append(skill)
    return skills


def find_skill(slug: str) -> dict[str, Any] | None:
    return next((skill for skill in load_skills() if skill["slug"] == slug), None)


def save_skill(skill: dict[str, Any], status: str) -> Path:
    """Write a skill to the directory its status implies, removing stale copies."""
    if status not in SKILL_STATUSES:
        raise ValueError(f"status must be one of: {', '.join(SKILL_STATUSES)}")
    slug = skill["slug"]
    target = skill_path(slug, status)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {**skill, "status": status}
    target.write_text(render_skill(payload), encoding="utf-8")

    for other in SKILL_STATUSES:
        stale = skill_path(slug, other)
        if stale != target and stale.exists():
            stale.unlink()
    return target


def count_skills() -> int:
    return len(load_skills())


def at_skill_limit() -> bool:
    return count_skills() >= _MAX_SKILLS


def record_skill_decision(slug: str, decision: str) -> dict[str, Any] | None:
    """Approve or decline a pending skill on the user's behalf.

    This is the only path that can activate a skill. Returns the updated skill,
    or None when no pending draft carries that slug.
    """
    if decision not in {"approved", "declined"}:
        raise ValueError("decision must be 'approved' or 'declined'")
    if not isinstance(slug, str) or not _SLUG_RE.fullmatch(slug):
        raise ValueError("slug is invalid")

    skill = find_skill(slug)
    if skill is None or skill.get("status") != "pending":
        return None

    now = datetime.now(timezone.utc).isoformat()
    updated = {
        **skill,
        "status": "active" if decision == "approved" else "declined",
        "decided_at": now,
        "updated_at": now,
        "decided_by": "user",
    }
    save_skill(updated, updated["status"])
    return updated


def skill_snapshot(limit: int = _SNAPSHOT_LIMIT) -> dict[str, Any]:
    """Pending drafts and active skills, for the tool rack's hover window."""
    try:
        skills = load_skills()
    except OSError as exc:  # pragma: no cover - defensive
        return {
            "available": False,
            "message": str(exc),
            "pending": [],
            "active": [],
        }

    def as_window_skill(skill: dict[str, Any]) -> dict[str, Any]:
        return {
            "slug": skill["slug"],
            "name": str(skill.get("name", ""))[:120],
            "purpose": str(skill.get("purpose") or "")[:400],
            "when_to_use": str(skill.get("when_to_use") or "")[:300],
            "steps": [str(step)[:200] for step in skill.get("steps", [])[:8]],
            "revision": skill.get("revision", 1),
        }

    pending = [skill for skill in skills if skill["status"] == "pending"]
    active = [skill for skill in skills if skill["status"] == "active"]
    return {
        "available": True,
        "pending": [as_window_skill(skill) for skill in pending[:limit]],
        "pending_count": len(pending),
        "active": [as_window_skill(skill) for skill in active[:limit]],
        "active_count": len(active),
    }
