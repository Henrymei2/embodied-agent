"""Bash / filesystem MCP server for the embodied agent.

Exposes a small set of primitive tools (inspired by CowAgent's tool set,
see https://docs.cowagent.ai/tools) that let the agent operate on its own
workspace: run shell commands, and read / write / edit / list files.

Hard rule (the whole point of this server): every operation is sandboxed to
the agent workspace (`~/embodied-agent/` by default). File tools refuse paths
that resolve outside the workspace, and `execute_bash` always runs with its
working directory pinned to the workspace root. None of these tools will
succeed against arbitrary locations on the user's machine.

Intentionally NOT implemented (per design): send, memory, web_fetch,
scheduler, and other optional tools. Memory is handled by the backend memory
system; scheduling and messaging are owned by other components.

Run standalone for a quick sanity check:
    uv run python mcp_servers/bash_tools.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# This file is launched as a script (`python mcp_servers/bash_tools.py`), which
# puts `mcp_servers/` on sys.path rather than the project root. Add the project
# root so the `src` namespace package is importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

# Reuse the backend's workspace sandbox so there is a single source of truth for
# "what is inside the workspace".
from src.open_llm_vtuber.workspace import (
    WorkspaceAccessError,
    ensure_workspace,
    resolve_workspace_path,
)

# Allow overriding the workspace location for tests / alternative deployments.
_WORKSPACE_DIR = os.environ.get("EMBODIED_AGENT_WORKSPACE") or None

# Cap on how much output a single tool returns, to keep the LLM context sane.
_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_BASH_TIMEOUT = 30

mcp = FastMCP("bash")


def _workspace_root() -> Path:
    """Return the (created if needed) workspace root."""
    return ensure_workspace(_WORKSPACE_DIR).root


def _safe_path(path: str) -> Path:
    """Resolve a user-supplied path, guaranteeing it stays in the workspace.

    Raises WorkspaceAccessError if the path escapes the workspace.
    """
    return resolve_workspace_path(path, _WORKSPACE_DIR)


def _rel(path: Path) -> str:
    """Display a path relative to the workspace root when possible."""
    try:
        return str(path.relative_to(_workspace_root()))
    except ValueError:
        return str(path)


def _truncate(text: str) -> str:
    if len(text) > _MAX_OUTPUT_CHARS:
        return (
            text[:_MAX_OUTPUT_CHARS]
            + f"\n... [truncated {len(text) - _MAX_OUTPUT_CHARS} chars]"
        )
    return text


@mcp.tool()
def execute_bash(command: str, timeout: int = _DEFAULT_BASH_TIMEOUT) -> str:
    """Run a shell command inside the agent workspace.

    The command's working directory is always the workspace root
    (`~/embodied-agent/`); it cannot be changed. Use this for things the
    dedicated file tools don't cover (running scripts, grep, etc.).

    Args:
        command: The shell command to execute.
        timeout: Maximum seconds to allow the command to run (default 30).

    Returns:
        Combined exit code, stdout and stderr.
    """
    root = _workspace_root()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s."
    except Exception as exc:  # noqa: BLE001 - surface any launch failure to the agent
        return f"Error: failed to run command: {exc}"

    parts = [f"exit_code: {completed.returncode}"]
    if completed.stdout:
        parts.append(f"stdout:\n{completed.stdout}")
    if completed.stderr:
        parts.append(f"stderr:\n{completed.stderr}")
    if not completed.stdout and not completed.stderr:
        parts.append("(no output)")
    return _truncate("\n".join(parts))


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file from the workspace.

    Args:
        path: Path relative to the workspace root (absolute paths inside the
            workspace are also accepted).
    """
    try:
        target = _safe_path(path)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"

    if not target.exists():
        return f"Error: file does not exist: {_rel(target)}"
    if not target.is_file():
        return f"Error: not a file: {_rel(target)}"
    try:
        return _truncate(target.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return f"Error: file is not valid UTF-8 text: {_rel(target)}"
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to read file: {exc}"


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create or overwrite a text file in the workspace.

    Parent directories are created automatically.

    Args:
        path: Path relative to the workspace root.
        content: Full text content to write.
    """
    try:
        target = _safe_path(path)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to write file: {exc}"
    return f"OK: wrote {len(content)} chars to {_rel(target)}"


@mcp.tool()
def append_to_file(path: str, content: str) -> str:
    """Append text to a file in the workspace, creating it if needed.

    Args:
        path: Path relative to the workspace root.
        content: Text to append.
    """
    try:
        target = _safe_path(path)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(content)
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to append to file: {exc}"
    return f"OK: appended {len(content)} chars to {_rel(target)}"


@mcp.tool()
def edit_file(path: str, old_string: str, new_string: str) -> str:
    """Replace an exact substring in a workspace file.

    The `old_string` must appear exactly once in the file, otherwise the edit
    is rejected so it can't silently change the wrong place.

    Args:
        path: Path relative to the workspace root.
        old_string: Exact text to find.
        new_string: Replacement text.
    """
    try:
        target = _safe_path(path)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"
    if not target.is_file():
        return f"Error: file does not exist: {_rel(target)}"
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to read file: {exc}"

    occurrences = text.count(old_string)
    if occurrences == 0:
        return f"Error: old_string not found in {_rel(target)}"
    if occurrences > 1:
        return (
            f"Error: old_string appears {occurrences} times in {_rel(target)}; "
            "make it more specific so it matches exactly once."
        )

    try:
        target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to write file: {exc}"
    return f"OK: edited {_rel(target)}"


@mcp.tool()
def list_directory(path: str = ".") -> str:
    """List the contents of a directory in the workspace.

    Args:
        path: Directory path relative to the workspace root. Defaults to the
            workspace root.
    """
    try:
        target = _safe_path(path)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"
    if not target.exists():
        return f"Error: directory does not exist: {_rel(target)}"
    if not target.is_dir():
        return f"Error: not a directory: {_rel(target)}"

    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.is_dir():
            entries.append(f"{child.name}/")
        else:
            try:
                size = child.stat().st_size
            except OSError:
                size = 0
            entries.append(f"{child.name} ({size} bytes)")
    if not entries:
        return f"(empty) {_rel(target)}"
    return f"{_rel(target)}:\n" + "\n".join(entries)


@mcp.tool()
def create_directory(path: str) -> str:
    """Create a directory (and any missing parents) in the workspace.

    Args:
        path: Directory path relative to the workspace root.
    """
    try:
        target = _safe_path(path)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to create directory: {exc}"
    return f"OK: created {_rel(target)}"


@mcp.tool()
def delete_path(path: str) -> str:
    """Delete a file or (recursively) a directory in the workspace.

    Args:
        path: Path relative to the workspace root.
    """
    try:
        target = _safe_path(path)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"
    if target == _workspace_root():
        return "Error: refusing to delete the workspace root."
    if not target.exists():
        return f"Error: path does not exist: {_rel(target)}"
    try:
        if target.is_dir():
            import shutil

            shutil.rmtree(target)
        else:
            target.unlink()
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to delete path: {exc}"
    return f"OK: deleted {_rel(target)}"


@mcp.tool()
def move_path(source: str, destination: str) -> str:
    """Move or rename a file/directory within the workspace.

    Both `source` and `destination` must stay inside the workspace.

    Args:
        source: Existing path relative to the workspace root.
        destination: Target path relative to the workspace root.
    """
    try:
        src = _safe_path(source)
        dst = _safe_path(destination)
    except WorkspaceAccessError as exc:
        return f"Error: {exc}"
    if not src.exists():
        return f"Error: source does not exist: {_rel(src)}"
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.replace(dst)
    except Exception as exc:  # noqa: BLE001
        return f"Error: failed to move path: {exc}"
    return f"OK: moved {_rel(src)} -> {_rel(dst)}"


if __name__ == "__main__":
    mcp.run()
