"""Kernel-enforced sandbox for `execute_bash`.

Static scanning of a shell command string cannot be a security boundary: a
shell has too many equivalent ways to spell a path (quote-splitting like
``/e''tc/passwd``) and any interpreter launders intent through a file
(``python evil.py``). The only robust way to confine ``execute_bash`` is to run
it inside a real isolation boundary and never give it a handle to the host
filesystem in the first place.

This module runs each command inside a locked-down Docker container:

* ``--network none``                  no network access at all
* ``--read-only`` root filesystem      the command cannot mutate the image
* only the agent workspace is mounted  (at ``/workspace``, read-write) — the
                                       host filesystem is simply not present,
                                       so *no* path trick can reach it
* ``--cap-drop ALL`` +                 no Linux capabilities, and a child can
  ``--security-opt no-new-privileges`` never gain privileges via setuid
* ``--pids-limit`` / ``--memory`` /    fork-bomb / memory / CPU ceilings
  ``--cpus`` / ``--tmpfs /tmp``
* no host environment is passed in     (credentials never enter the container)

Because the host filesystem is not mounted, an absolute path such as
``/etc/passwd`` inside the container refers to the throwaway image, not the
user's machine — which is exactly why we no longer need a fragile in-string
path firewall on the containerized path.

If Docker is not available the sandbox fails **closed**: ``execute_bash``
refuses to run rather than silently falling back to the (bypassable) host
shell. An operator who is already running inside a disposable VM can opt back
into host execution explicitly (see ``bash_tools.py``); that is never the
default.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

# Small, widely-available image that ships bash + coreutils + python. Override
# for air-gapped deploys or to pin a digest.
DEFAULT_IMAGE = "python:3.12-slim"

_DOCKER_BIN = os.environ.get("EMBODIED_AGENT_SANDBOX_DOCKER") or "docker"
_IMAGE = os.environ.get("EMBODIED_AGENT_SANDBOX_IMAGE") or DEFAULT_IMAGE
_MEMORY = os.environ.get("EMBODIED_AGENT_SANDBOX_MEMORY") or "512m"
_PIDS = os.environ.get("EMBODIED_AGENT_SANDBOX_PIDS") or "512"
_CPUS = os.environ.get("EMBODIED_AGENT_SANDBOX_CPUS") or "2"

# Where the workspace is mounted inside the container.
_CONTAINER_WORKSPACE = "/workspace"


class SandboxUnavailableError(RuntimeError):
    """Raised when the container sandbox cannot be used (fail closed)."""


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str


_docker_ok: bool | None = None


def _run_docker(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Invoke the docker CLI on the host with no shell (argv passed literally)."""
    return subprocess.run(
        [_DOCKER_BIN, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def is_docker_available() -> bool:
    """True if a working Docker daemon is reachable. Result is memoized."""
    global _docker_ok
    if _docker_ok is not None:
        return _docker_ok
    if shutil.which(_DOCKER_BIN) is None and not Path(_DOCKER_BIN).exists():
        _docker_ok = False
        return False
    try:
        # `docker info` needs the daemon, not just the client, so it is the
        # honest availability probe.
        proc = _run_docker(["info", "--format", "{{.ServerVersion}}"], timeout=15)
        _docker_ok = proc.returncode == 0
    except Exception:  # noqa: BLE001 - any failure means "not available"
        _docker_ok = False
    return _docker_ok


def ensure_image(image: str = _IMAGE) -> None:
    """Make sure the sandbox image is present locally, pulling if needed.

    Raises SandboxUnavailableError with an actionable message on failure.
    """
    try:
        inspect = _run_docker(["image", "inspect", image], timeout=30)
    except Exception as exc:  # noqa: BLE001
        raise SandboxUnavailableError(f"could not query docker images: {exc}") from exc
    if inspect.returncode == 0:
        return
    # Not present locally: attempt a pull (uses the host network; the container
    # run itself is still --network none).
    try:
        pull = _run_docker(["pull", image], timeout=600)
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailableError(f"timed out pulling image {image}") from exc
    except Exception as exc:  # noqa: BLE001
        raise SandboxUnavailableError(f"failed to pull image {image}: {exc}") from exc
    if pull.returncode != 0:
        raise SandboxUnavailableError(
            f"could not pull sandbox image {image}: {pull.stderr.strip() or 'unknown error'}"
        )


def _docker_run_args(container_name: str, workspace_root: Path, command: str) -> list[str]:
    """Build the locked-down `docker run` argument vector for one command."""
    return [
        "run",
        "--rm",
        "--name",
        container_name,
        # Network / privilege isolation.
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        # Immutable root fs; only an ephemeral, non-exec /tmp is writable besides
        # the mounted workspace.
        "--read-only",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m",
        # Resource ceilings (DoS containment).
        "--memory",
        _MEMORY,
        "--memory-swap",
        _MEMORY,  # equal to --memory disables swap
        "--pids-limit",
        _PIDS,
        "--cpus",
        _CPUS,
        # The ONLY host path exposed to the command: the agent workspace.
        "-v",
        f"{workspace_root}:{_CONTAINER_WORKSPACE}:rw",
        "-w",
        _CONTAINER_WORKSPACE,
        # Pin HOME into the workspace so ~ expansion resolves inside it. No other
        # host env is forwarded, so credentials never enter the container.
        "-e",
        f"HOME={_CONTAINER_WORKSPACE}",
        _IMAGE,
        "sh",
        "-c",
        command,
    ]


def run_in_sandbox(command: str, workspace_root: Path, timeout: int) -> SandboxResult:
    """Run ``command`` inside a locked-down container. Fails closed.

    Raises SandboxUnavailableError if Docker/the image is not usable so the
    caller can refuse the operation rather than run it on the host.
    """
    if not is_docker_available():
        raise SandboxUnavailableError(
            "Docker is not available. execute_bash requires a container sandbox "
            "and will not fall back to running on the host. Install/start Docker, "
            "or set EMBODIED_AGENT_SANDBOX_DOCKER to the docker binary."
        )
    ensure_image()

    container_name = f"embodied-bash-{uuid.uuid4().hex}"
    args = _docker_run_args(container_name, workspace_root, command)

    try:
        # Give docker a little headroom over the in-container timeout so the
        # container's own clock is what bounds the command.
        proc = _run_docker(args, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        _force_remove(container_name)
        raise
    return SandboxResult(
        exit_code=proc.returncode,
        stdout=proc.stdout or "",
        stderr=proc.stderr or "",
    )


def _force_remove(container_name: str) -> None:
    """Best-effort kill+remove of a container whose `docker run` we abandoned."""
    try:
        _run_docker(["rm", "-f", container_name], timeout=15)
    except Exception:  # noqa: BLE001
        pass
