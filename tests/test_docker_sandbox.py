"""Tests for the execute_bash container sandbox.

The end-to-end container behavior needs a Docker daemon and is skipped when one
isn't present. The important security invariants live in how the `docker run`
argument vector is built, which is verified without Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_servers import docker_sandbox as ds


def _args(command: str = "echo hi", ws: str = "/home/u/embodied-agent") -> list[str]:
    return ds._docker_run_args("sandbox-xyz", Path(ws), command)


def test_container_has_no_network():
    args = _args()
    assert "--network" in args
    assert args[args.index("--network") + 1] == "none"


def test_container_root_is_read_only():
    assert "--read-only" in _args()


def test_container_drops_capabilities_and_privileges():
    args = _args()
    assert "--cap-drop" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in args


def test_container_has_resource_ceilings():
    args = _args()
    assert "--memory" in args
    assert "--pids-limit" in args
    assert "--cpus" in args


def test_only_the_workspace_is_mounted():
    ws = "/home/u/embodied-agent"
    args = _args(ws=ws)
    mounts = [args[i + 1] for i, a in enumerate(args) if a == "-v"]
    # Exactly one bind mount: the workspace at /workspace, read-write. Compare
    # against the OS-native rendering of the path (Docker gets str(Path)).
    assert mounts == [f"{Path(ws)}:/workspace:rw"]
    assert "-w" in args and args[args.index("-w") + 1] == "/workspace"


def test_no_host_environment_is_forwarded():
    # The only -e passed is HOME (pinned into the workspace). Nothing else from
    # the host env may cross into the container.
    args = _args()
    env_values = [args[i + 1] for i, a in enumerate(args) if a == "-e"]
    assert env_values == ["HOME=/workspace"]


def test_command_is_passed_to_sh_c_verbatim():
    args = _args(command="echo $HOME && cat /etc/os-release")
    # The user command is the final argument, handed to `sh -c` unmodified: the
    # container boundary — not string parsing — is what confines it.
    assert args[-3:] == ["sh", "-c", "echo $HOME && cat /etc/os-release"]


# --------------------------------------------------------------------------- #
# End-to-end (requires Docker). Proves the escape is actually closed.
# --------------------------------------------------------------------------- #
_HAS_DOCKER = ds.is_docker_available()
requires_docker = pytest.mark.skipif(
    not _HAS_DOCKER, reason="Docker daemon not available"
)


@requires_docker
def test_e2e_runs_simple_command(tmp_path):
    ws = tmp_path / "embodied-agent"
    ws.mkdir()
    result = ds.run_in_sandbox("echo hello-sandbox", ws, timeout=30)
    assert result.exit_code == 0
    assert "hello-sandbox" in result.stdout


@requires_docker
def test_e2e_cannot_read_host_file_outside_workspace(tmp_path):
    # The exact escape proven against the old firewall: a secret outside the
    # workspace must be unreadable, no matter the path trick, because it is not
    # mounted into the container at all.
    ws = tmp_path / "embodied-agent"
    ws.mkdir()
    secret = tmp_path / "OUTSIDE_SECRET.txt"
    secret.write_text("TOP_SECRET", encoding="utf-8")

    for command in (
        f"cat '{secret}'",
        r"cat /e''tc/passwd",  # quote-splitting no longer helps
        f"python -c \"print(open(r'{secret}').read())\"",  # indirect execution
    ):
        result = ds.run_in_sandbox(command, ws, timeout=30)
        assert "TOP_SECRET" not in result.stdout
        assert "TOP_SECRET" not in result.stderr


@requires_docker
def test_e2e_has_no_network(tmp_path):
    ws = tmp_path / "embodied-agent"
    ws.mkdir()
    result = ds.run_in_sandbox(
        "python -c \"import socket; socket.create_connection(('1.1.1.1',53),2)\"",
        ws,
        timeout=30,
    )
    assert result.exit_code != 0  # network is disabled


@requires_docker
def test_e2e_can_write_inside_workspace(tmp_path):
    ws = tmp_path / "embodied-agent"
    ws.mkdir()
    result = ds.run_in_sandbox("echo data > note.txt && cat note.txt", ws, timeout=30)
    assert result.exit_code == 0
    assert "data" in result.stdout
    # The write must be visible on the host workspace (bind mount round-trips).
    assert (ws / "note.txt").exists()
