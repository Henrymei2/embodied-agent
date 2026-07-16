"""Bash / filesystem MCP server for the embodied agent.

Exposes a small set of primitive tools (inspired by CowAgent's tool set,
see https://docs.cowagent.ai/tools) that let the agent operate on its own
workspace: run shell commands, and read / write / edit / list files.

Hard rule (the whole point of this server): every operation is sandboxed to
the agent workspace (`~/embodied-agent/` by default). File tools refuse paths
that resolve outside the workspace, and `execute_bash` always runs with its
working directory pinned to the workspace root. None of these tools will
succeed against arbitrary locations on the user's machine.

Intentionally NOT implemented (per design): send, memory, general web_fetch,
scheduler, and other optional tools. The dedicated download tool saves bytes
to disk but never returns remote content. Memory is handled by the backend
memory system; scheduling and messaging are owned by other components.

Run standalone for a quick sanity check:
    uv run python mcp_servers/bash_tools.py
"""

from __future__ import annotations

import hashlib
import ipaddress
import mimetypes
import os
import re
import socket
import subprocess
import sys
import tempfile
from email.message import Message
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests

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
from mcp_servers.docker_sandbox import (
    SandboxUnavailableError,
    run_in_sandbox,
)

# Allow overriding the workspace location for tests / alternative deployments.
_WORKSPACE_DIR = os.environ.get("EMBODIED_AGENT_WORKSPACE") or None

# Cap on how much output a single tool returns, to keep the LLM context sane.
_MAX_OUTPUT_CHARS = 20_000
_DEFAULT_BASH_TIMEOUT = 30
_MAX_BASH_TIMEOUT = 120
_DEFAULT_DOWNLOAD_MAX_BYTES = 100 * 1024 * 1024
_ABSOLUTE_DOWNLOAD_MAX_BYTES = 500 * 1024 * 1024
_MAX_DOWNLOAD_REDIRECTS = 5
_DOWNLOAD_CHUNK_SIZE = 64 * 1024
_DOWNLOAD_TIMEOUT = (10, 30)

# execute_bash runs in a Docker sandbox and fails closed when Docker is absent.
# An operator who is ALREADY inside a disposable, isolated environment (e.g. a
# throwaway VM) can opt back into running on the host by setting this to "1".
# This is never the default because host execution cannot be securely confined.
_ALLOW_INSECURE_HOST = os.environ.get("EMBODIED_AGENT_ALLOW_INSECURE_HOST_BASH") == "1"

# Env vars whose names match these fragments are stripped from the environment
# given to `execute_bash` children, so agent-run commands can't trivially
# exfiltrate credentials that happen to live in the server's environment.
_SENSITIVE_ENV_FRAGMENTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL")

mcp = FastMCP("bash")


def _sanitized_env(root: Path) -> dict[str, str]:
    """Environment for child commands: credentials removed, home pinned to root.

    Pinning HOME / USERPROFILE to the workspace means even shell/home expansion
    resolves inside the workspace rather than the real user home.
    """
    env = {
        name: value
        for name, value in os.environ.items()
        if not any(fragment in name.upper() for fragment in _SENSITIVE_ENV_FRAGMENTS)
    }
    env["HOME"] = str(root)
    env["USERPROFILE"] = str(root)
    return env


# --------------------------------------------------------------------------- #
# execute_bash sandbox firewall
# --------------------------------------------------------------------------- #
# `execute_bash` runs a real shell, so pinning the cwd is not enough: an
# absolute path lets a command reach outside the workspace. To honor the rule
# that these tools must not operate outside `~/embodied-agent/`, every command
# is statically screened and rejected if it references a location outside the
# workspace, or uses dynamic path construction we cannot verify.

# Variable expansion / command substitution: their runtime value is unknown to
# a static check, so we refuse them outright (the safe, conservative choice).
_DYNAMIC_CONSTRUCT_RE = re.compile(
    r"""`              # backtick command substitution
      | \$\(           # $(...) command substitution
      | <\( | >\(      # process substitution
      | \$\{           # ${VAR}
      | \$[A-Za-z_]    # $VAR
      | %[A-Za-z_][A-Za-z0-9_]*%   # %VAR% (Windows)
    """,
    re.VERBOSE,
)

# URLs are not filesystem paths; strip them before scanning for paths.
_URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://\S*")

_UNC_RE = re.compile(r"\\\\[^\s\"'|&<>]+")
_DRIVE_RE = re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s\"'|&<>]*")
_HOME_RE = re.compile(r"(?<!\w)~(?:[\\/][^\s\"'|&<>]*)?")
_PARENT_RE = re.compile(r"(?<![\w.])\.\.(?:[\\/][^\s\"'|&<>]*)?")
_POSIX_ABS_RE = re.compile(r"(?<![\w:])/[^\s\"'|&<>]*")


def _resolves_inside(candidate: str, root: Path) -> bool:
    try:
        p = Path(candidate)
        resolved = p.resolve() if p.is_absolute() else (root / p).resolve()
        resolved.relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _command_reaches_outside_workspace(command: str, root: Path) -> str | None:
    """Return a reason string if the command references outside the workspace."""
    if _DYNAMIC_CONSTRUCT_RE.search(command):
        return (
            "uses variable expansion or command substitution, which the sandbox "
            "cannot verify; use literal in-workspace paths instead"
        )

    scan = _URL_RE.sub(" ", command)

    unc = _UNC_RE.search(scan)
    if unc:
        return f"references a network path ({unc.group().strip()})"

    home = _HOME_RE.search(scan)
    if home:
        return f"references the home directory ({home.group().strip()})"

    for token in _PARENT_RE.findall(scan):
        return f"uses a parent-directory escape ({token.strip()})"

    for regex in (_DRIVE_RE, _POSIX_ABS_RE):
        for match in regex.finditer(scan):
            token = match.group().strip().strip("\"'")
            if not token:
                continue
            # A lone "/x" on Windows is usually a CLI switch, not a path.
            if regex is _POSIX_ABS_RE and token != "/" and token.count("/") < 2:
                continue
            if not _resolves_inside(token, root):
                return f"references a path outside the workspace ({token})"

    return None


def _kill_process_tree(process: subprocess.Popen) -> None:
    """Best-effort kill of a timed-out command and all of its children."""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            import signal

            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 - fall through to killing just the shell
        pass
    try:
        process.kill()
    except Exception:  # noqa: BLE001
        pass


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


def _validate_download_url(url: str) -> str:
    """Validate a public HTTP(S) URL and reject common SSRF targets."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs containing credentials are not allowed")

    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except OSError as exc:
        raise ValueError(f"could not resolve hostname: {parsed.hostname}") from exc

    if not addresses:
        raise ValueError(f"hostname resolved to no addresses: {parsed.hostname}")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError(
                f"URL resolves to a non-public address ({address}); download refused"
            )
    return url


def _filename_from_response(response: requests.Response, url: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    if disposition:
        message = Message()
        message["Content-Disposition"] = disposition
        filename = message.get_filename()
        if filename:
            filename = Path(unquote(filename)).name
        else:
            filename = ""
    else:
        filename = ""

    if not filename:
        filename = Path(unquote(urlsplit(url).path)).name
    if not filename:
        extension = mimetypes.guess_extension(
            response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        )
        filename = f"download{extension or ''}"

    filename = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", filename).strip(". ")
    return filename or "download"


def _download_response(
    session: requests.Session,
    url: str,
) -> tuple[requests.Response, str]:
    current_url = url
    for redirect_count in range(_MAX_DOWNLOAD_REDIRECTS + 1):
        _validate_download_url(current_url)
        response = session.get(
            current_url,
            stream=True,
            allow_redirects=False,
            timeout=_DOWNLOAD_TIMEOUT,
            headers={"Accept-Encoding": "identity", "User-Agent": "Open-LLM-VTuber"},
        )
        if response.is_redirect or response.is_permanent_redirect:
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ValueError("redirect response did not include a Location header")
            if redirect_count >= _MAX_DOWNLOAD_REDIRECTS:
                raise ValueError("too many redirects")
            current_url = urljoin(current_url, location)
            continue
        try:
            response.raise_for_status()
        except requests.RequestException:
            response.close()
            raise
        return response, current_url
    raise ValueError("too many redirects")


@mcp.tool()
def download_file(
    url: str,
    path: str = "",
    max_bytes: int = _DEFAULT_DOWNLOAD_MAX_BYTES,
    overwrite: bool = False,
) -> str:
    """Download a public HTTP(S) URL directly into the agent workspace.

    The response body is streamed to disk and is never returned to the model.
    Use this for PDFs, images, archives, and other files instead of fetching or
    encoding their raw content. When path is omitted, the server-selected file
    name is saved under ``tmp/``.

    Args:
        url: Public http:// or https:// URL to download. Local and private
            network addresses are refused.
        path: Optional destination path inside the workspace. A trailing slash
            means "use the server-provided filename in this directory".
        max_bytes: Maximum accepted download size. Defaults to 100 MiB and is
            capped at 500 MiB.
        overwrite: Replace an existing destination when true. Defaults to false.

    Returns:
        A short confirmation containing only path, size, media type, SHA-256,
        and final URL. File contents are never included.
    """
    try:
        max_bytes = int(max_bytes)
    except (TypeError, ValueError):
        return "Error: max_bytes must be an integer."
    if max_bytes < 1:
        return "Error: max_bytes must be at least 1."
    max_bytes = min(max_bytes, _ABSOLUTE_DOWNLOAD_MAX_BYTES)

    # Validate a caller-supplied path before making any network request.
    target: Path | None = None
    path_is_directory = not path or path.endswith(("/", "\\"))
    if path:
        try:
            target = _safe_path(path)
        except WorkspaceAccessError as exc:
            return f"Error: {exc}"

    session = requests.Session()
    session.trust_env = False
    response: requests.Response | None = None
    temporary_path: Path | None = None
    try:
        response, final_url = _download_response(session, url)
        filename = _filename_from_response(response, final_url)
        if target is None:
            target = _safe_path(f"tmp/{filename}")
        elif path_is_directory or target.is_dir():
            target = _safe_path(target / filename)

        if target.exists() and not overwrite:
            return f"Error: destination already exists: {_rel(target)}"
        if target.exists() and not target.is_file():
            return f"Error: destination is not a file: {_rel(target)}"

        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = None
            if declared_size is not None and declared_size > max_bytes:
                return (
                    f"Error: download declares {declared_size} bytes, exceeding "
                    f"the {max_bytes}-byte limit."
                )

        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        downloaded = 0
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".part",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            for chunk in response.iter_content(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                if not chunk:
                    continue
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise ValueError(
                        f"download exceeded the {max_bytes}-byte limit"
                    )
                handle.write(chunk)
                digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temporary_path, target)
        temporary_path = None
        media_type = response.headers.get("Content-Type", "unknown").split(";", 1)[0]
        return (
            f"OK: downloaded {_rel(target)} ({downloaded} bytes, {media_type})\n"
            f"SHA-256: {digest.hexdigest()}\nFinal URL: {final_url}"
        )
    except (requests.RequestException, ValueError, OSError) as exc:
        return f"Error: download failed: {exc}"
    finally:
        if response is not None:
            response.close()
        session.close()
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


@mcp.tool()
def execute_bash(command: str, timeout: int = _DEFAULT_BASH_TIMEOUT) -> str:
    """Run a shell command inside an isolated container sandbox.

    The command runs inside a locked-down Docker container whose only view of
    the host filesystem is the agent workspace (`~/embodied-agent/`), mounted at
    /workspace with the working directory pinned there. The container has no
    network access, a read-only root filesystem, dropped Linux capabilities, and
    memory/PID/CPU ceilings. Because the host filesystem is not mounted, the
    command cannot read or write anything outside the workspace regardless of
    what paths, quoting, or interpreters it uses. Use this for things the
    dedicated file tools don't cover (running scripts, grep, etc.).

    If Docker is unavailable the command is refused: the sandbox fails closed
    rather than running on the host.

    Args:
        command: The shell command to execute.
        timeout: Maximum seconds to allow the command to run
            (default 30, capped at 120).

    Returns:
        Combined exit code, stdout and stderr.
    """
    root = _workspace_root()
    timeout = max(1, min(int(timeout), _MAX_BASH_TIMEOUT))

    try:
        result = run_in_sandbox(command, root, timeout)
    except SandboxUnavailableError as exc:
        if _ALLOW_INSECURE_HOST:
            return _execute_bash_on_host_insecure(command, root, timeout)
        return (
            f"Error: refused: {exc}\nexecute_bash requires a container sandbox. "
            "Set EMBODIED_AGENT_ALLOW_INSECURE_HOST_BASH=1 only if this process "
            "already runs inside a disposable, isolated environment."
        )
    except subprocess.TimeoutExpired:
        return f"Error: command timed out after {timeout}s (sandbox container killed)."
    except Exception as exc:  # noqa: BLE001 - surface any launch failure to the agent
        return f"Error: failed to run command in sandbox: {exc}"

    parts = [f"exit_code: {result.exit_code}"]
    if result.stdout:
        parts.append(f"stdout:\n{result.stdout}")
    if result.stderr:
        parts.append(f"stderr:\n{result.stderr}")
    if not result.stdout and not result.stderr:
        parts.append("(no output)")
    return _truncate("\n".join(parts))


def _execute_bash_on_host_insecure(command: str, root: Path, timeout: int) -> str:
    """Run a command directly on the host (NO container isolation).

    Only reachable when EMBODIED_AGENT_ALLOW_INSECURE_HOST_BASH=1. The static
    path firewall below is best-effort defense-in-depth and is known to be
    bypassable (quote-splitting, indirect execution). It is NOT a substitute for
    the container sandbox; it merely reduces casual escapes for operators who
    have accepted host execution inside an already-isolated environment.
    """
    reason = _command_reaches_outside_workspace(command, root)
    if reason is not None:
        return (
            f"Error: refused: command {reason}. execute_bash may only operate "
            "inside the workspace."
        )

    popen_kwargs: dict = {
        "shell": True,
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "env": _sanitized_env(root),
    }
    # Run the command in its own process group/session so a timeout can kill
    # the whole tree, not just the shell.
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(command, **popen_kwargs)
    except Exception as exc:  # noqa: BLE001 - surface any launch failure to the agent
        return f"Error: failed to run command: {exc}"

    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(process)
        try:
            process.communicate(timeout=5)  # reap so we don't leak handles
        except Exception:  # noqa: BLE001
            pass
        return f"Error: command timed out after {timeout}s (process tree killed)."

    parts = [f"exit_code: {process.returncode}"]
    if stdout:
        parts.append(f"stdout:\n{stdout}")
    if stderr:
        parts.append(f"stderr:\n{stderr}")
    if not stdout and not stderr:
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
