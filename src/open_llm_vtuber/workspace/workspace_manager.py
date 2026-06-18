from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


DEFAULT_WORKSPACE_DIR = Path.home() / "embodied-agent"

ROOT_FILES = {
    "AGENT.md": """# AGENT.md

Information about the agent.

## Identity
- Name:
- Role:
- Personality:

## Behavior
- Be helpful.
- Use the workspace as persistent context.
- Do not access files outside the workspace.
""",
    "MEMORY.md": """# MEMORY.md

Core memory for the agent.

Use this for important long-term memories, preferences, and decisions.
""",
    "RULE.md": """# RULE.md

Rules for the agent.

## Filesystem rule
The agent may only read, write, create, edit, delete, or execute files inside the workspace directory.

Default workspace:
`~/embodied-agent/`

## Workspace structure
- AGENT.md: information about the agent
- MEMORY.md: core memory
- RULE.md: rules
- USER.md: information about the user
- knowledge/: persistent knowledge files
- scripts/: scripts the agent can create or run
- skills/: reusable skills
- tmp/: temporary files
- memory/: daily memory files
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