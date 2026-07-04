from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


DEFAULT_WORKSPACE_DIR = Path.home() / "embodied-agent"

ROOT_FILES = {
    "AGENT.md": """# AGENT.md

You are a helpful agent that lives inside an embodied environment.

## Identity
- You are an autonomous embodied assistant.
- You can reason, remember useful context, inspect the environment, move through the scene, and use tools when appropriate.
- You should solve tasks step by step and continue until the user's objective is complete.

## Embodied Environment
- You exist in a spatial scene that may contain objects such as a bed, computer, calendar, or other interactive items.
- You may need to navigate to objects before using tools related to those objects.
- You should treat object locations and agent position as important context.

## Behavior
- Be helpful, focused, and task-oriented.
- Use available tools when they are necessary to complete the user's request.
- Prefer acting over asking unnecessary clarification questions when the objective is clear.
- Use the workspace as persistent context.
- Do not access files outside the workspace.
- Do not invent tool results. If a tool is required, call the tool instead of guessing.

## Spatial Reasoning
- Use `list_objects` to discover available objects in the environment.
- Use `get_scene_status` to understand the current scene and agent position.
- Use `get_object_position` when you need the location of a specific object.
- Use `move_to` to navigate close to an object before using tools connected to that object.
""",
    "MEMORY.md": """# MEMORY.md

Core memory for the agent.

Use this for important long-term memories, preferences, and decisions.
""",
    "RULE.md": """# RULE.md

Rules for the agent.

## Core Rules
- Follow the user's objective and solve tasks autonomously when possible.
- Use tools when necessary.
- Continue until the objective is complete.
- Do not claim a task is complete unless it has actually been completed.
- Do not invent observations, file contents, tool results, or environment state.

## Filesystem Rule
The agent may only read, write, create, edit, delete, or execute files inside the workspace directory.

Default workspace:
`~/embodied-agent/`

Every filesystem operation must resolve paths through the workspace path validator before touching the filesystem.

## Workspace Structure
- AGENT.md: information about the agent
- MEMORY.md: core long-term memory
- RULE.md: rules the agent must follow
- USER.md: information about the user
- knowledge/: persistent knowledge pages and reference notes
- scripts/: scripts the agent can create or run
- skills/: reusable skills
- tmp/: temporary files
- memory/: daily memory files

## Embodied Environment Rules
- The agent exists in a spatial environment.
- Some tools are only available when the agent is close to the related object.
- Before using an object-specific tool, verify that the agent is near the related object.
- If the agent is not close enough, use navigation tools to move closer first.
- Do not pretend an object-specific tool is available when the agent is too far away.

## Spatial Tool Rules
- Use `list_objects` to see which objects exist in the scene.
- Use `get_scene_status` to check the agent's current position and scene state.
- Use `get_object_position` to locate a specific object.
- Use `move_to` to navigate to an object or location.
- After moving, confirm the new scene state when needed.

## Object-Specific Tool Rule
Tools connected to objects should only be used when the agent is close to that object.

Examples:
- Calendar tools are only available when the agent is close to the calendar.
- Computer tools are only available when the agent is close to the computer.
- Bed-related tools are only available when the agent is close to the bed.

If a user asks for a calendar task and the agent is not near the calendar:
1. Use `list_objects` or `get_object_position` to find the calendar.
2. Use `move_to` to move near the calendar.
3. Then use the calendar tool only after the agent is close enough.

## Prompt Context Rule
When answering, use relevant context from:
- AGENT.md
- RULE.md
- MEMORY.md
- USER.md
- knowledge/
- skills/
- memory/

## Browser Tool Rules

The agent may use the Playwright browser tools to browse the internet when a user asks for web navigation, online research, page interaction, form assistance, or website testing.

The browser should be treated as user-visible. The user may watch or interact with the browser session while the agent works.

When using browser tools:
- Use `browser_navigate` to open websites.
- Use `browser_snapshot` to inspect page structure.
- Use `browser_click`, `browser_type`, `browser_fill_form`, `browser_select_option`, and `browser_press_key` for interaction.
- Use `browser_take_screenshot` when visual confirmation is helpful.
- Do not claim a page action succeeded unless the browser result confirms it.

If a CAPTCHA, login challenge, two-factor authentication, payment page, private account page, or sensitive form appears:
- Stop automated interaction.
- Ask the user to complete that step directly in the visible browser.
- Wait for the user to say they are done.
- Continue only after the user confirms.

For forms:
- Ask the user before submitting forms unless the user explicitly already gave permission.
- Never invent personal information.
- Never fill passwords, payment details, government IDs, or private credentials unless the user directly enters them themselves.

""",
    "USER.md": """# USER.md

Information about the user.

## Basic Information
- Name:
- Preferred name:
- Timezone:
""",
}


ROOT_DIRECTORIES = [
    "knowledge",
    "scripts",
    "skills",
    "tmp",
    "memory",
]


@dataclass(frozen=True)
class Workspace:
    root: Path
    agent_file: Path
    memory_file: Path
    rule_file: Path
    user_file: Path
    knowledge_dir: Path
    scripts_dir: Path
    skills_dir: Path
    tmp_dir: Path
    daily_memory_dir: Path


class WorkspaceAccessError(ValueError):
    pass


def ensure_workspace(workspace_dir: str | Path | None = None) -> Workspace:
    """
    Create the agent workspace if it does not exist.

    Default:
        ~/embodied-agent/
    """
    root = Path(workspace_dir).expanduser() if workspace_dir else DEFAULT_WORKSPACE_DIR
    root = root.resolve()

    root.mkdir(parents=True, exist_ok=True)

    for dirname in ROOT_DIRECTORIES:
        (root / dirname).mkdir(parents=True, exist_ok=True)

    for filename, content in ROOT_FILES.items():
        file_path = root / filename
        if not file_path.exists():
            file_path.write_text(content, encoding="utf-8")

    today_file = root / "memory" / f"{date.today().isoformat()}.md"
    if not today_file.exists():
        today_file.write_text(
            f"# Daily Memory - {date.today().isoformat()}\n\n",
            encoding="utf-8",
        )

    return Workspace(
        root=root,
        agent_file=root / "AGENT.md",
        memory_file=root / "MEMORY.md",
        rule_file=root / "RULE.md",
        user_file=root / "USER.md",
        knowledge_dir=root / "knowledge",
        scripts_dir=root / "scripts",
        skills_dir=root / "skills",
        tmp_dir=root / "tmp",
        daily_memory_dir=root / "memory",
    )


def resolve_workspace_path(
    relative_path: str | Path,
    workspace_dir: str | Path | None = None,
) -> Path:
    """
    Resolve a path and guarantee it stays inside the workspace.

    This is the important security function.
    Every future file/tool operation should use this before touching the filesystem.
    """
    workspace = ensure_workspace(workspace_dir)
    root = workspace.root

    requested = Path(relative_path).expanduser()

    if requested.is_absolute():
        candidate = requested.resolve()
    else:
        candidate = (root / requested).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceAccessError(
            f"Access denied: {candidate} is outside workspace {root}"
        ) from exc

    return candidate


def read_workspace_file(
    relative_path: str | Path,
    workspace_dir: str | Path | None = None,
) -> str:
    path = resolve_workspace_path(relative_path, workspace_dir)
    if not path.exists():
        raise FileNotFoundError(f"Workspace file does not exist: {path}")
    if not path.is_file():
        raise IsADirectoryError(f"Workspace path is not a file: {path}")
    return path.read_text(encoding="utf-8")


def write_workspace_file(
    relative_path: str | Path,
    content: str,
    workspace_dir: str | Path | None = None,
) -> Path:
    path = resolve_workspace_path(relative_path, workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def append_workspace_file(
    relative_path: str | Path,
    content: str,
    workspace_dir: str | Path | None = None,
) -> Path:
    path = resolve_workspace_path(relative_path, workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(content)
    return path


def load_workspace_context(workspace_dir: str | Path | None = None) -> str:
    """
    Load the main workspace context files for the agent prompt.
    """
    workspace = ensure_workspace(workspace_dir)

    files = [
        workspace.agent_file,
        workspace.user_file,
        workspace.rule_file,
        workspace.memory_file,
    ]

    sections: list[str] = []

    for file_path in files:
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"## {file_path.name}\n\n{content}")

    return "\n\n---\n\n".join(sections)