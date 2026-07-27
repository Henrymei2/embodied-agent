"""Tests for the sandboxed bash/filesystem MCP server tools.

The tool functions are exercised directly (FastMCP's @tool decorator returns the
underlying function). The workspace is redirected to a temp dir via the
EMBODIED_AGENT_WORKSPACE env var, which the server reads at import time, so the
module is reloaded after the env var is set.
"""

from __future__ import annotations

import importlib
import sys

import pytest
import requests


@pytest.fixture
def bt(tmp_path, monkeypatch):
    workspace = tmp_path / "embodied-agent"
    monkeypatch.setenv("EMBODIED_AGENT_WORKSPACE", str(workspace))
    sys.modules.pop("mcp_servers.bash_tools", None)
    module = importlib.import_module("mcp_servers.bash_tools")
    module = importlib.reload(module)
    return module


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_write_read_roundtrip(bt):
    assert bt.write_file("notes/todo.txt", "buy milk").startswith("OK")
    assert bt.read_file("notes/todo.txt") == "buy milk"


def test_append(bt):
    bt.write_file("log.txt", "line1\n")
    bt.append_to_file("log.txt", "line2\n")
    assert bt.read_file("log.txt") == "line1\nline2\n"


def test_edit_single_occurrence(bt):
    bt.write_file("a.txt", "hello world")
    assert bt.edit_file("a.txt", "world", "agent").startswith("OK")
    assert bt.read_file("a.txt") == "hello agent"


def test_edit_rejects_ambiguous(bt):
    bt.write_file("a.txt", "x x")
    assert "appears 2 times" in bt.edit_file("a.txt", "x", "y")


def test_edit_missing_string(bt):
    bt.write_file("a.txt", "abc")
    assert "not found" in bt.edit_file("a.txt", "zzz", "y")


def test_list_directory(bt):
    bt.write_file("dir/f1.txt", "a")
    bt.create_directory("dir/sub")
    listing = bt.list_directory("dir")
    assert "f1.txt" in listing and "sub/" in listing


def test_create_and_delete(bt):
    bt.create_directory("scratch")
    bt.write_file("scratch/x.txt", "data")
    assert bt.delete_path("scratch/x.txt").startswith("OK")
    assert "does not exist" in bt.read_file("scratch/x.txt")


def test_move(bt):
    bt.write_file("src.txt", "payload")
    assert bt.move_path("src.txt", "dst/dest.txt").startswith("OK")
    assert bt.read_file("dst/dest.txt") == "payload"
    assert "does not exist" in bt.read_file("src.txt")


def test_execute_bash_uses_container_sandbox(bt, monkeypatch):
    # execute_bash must route through the container sandbox, not the host.
    from mcp_servers.docker_sandbox import SandboxResult

    calls = {}

    def fake_run(command, root, timeout):
        calls["command"] = command
        return SandboxResult(exit_code=0, stdout="hello-sandbox\n", stderr="")

    monkeypatch.setattr(bt, "run_in_sandbox", fake_run)
    out = bt.execute_bash("echo hello-sandbox")
    assert calls["command"] == "echo hello-sandbox"
    assert "exit_code: 0" in out
    assert "hello-sandbox" in out


def test_execute_bash_fails_closed_without_docker(bt, monkeypatch):
    # When the sandbox is unavailable and host execution is NOT opted into,
    # execute_bash must refuse rather than run on the host.
    from mcp_servers.docker_sandbox import SandboxUnavailableError

    def unavailable(command, root, timeout):
        raise SandboxUnavailableError("Docker is not available")

    monkeypatch.setattr(bt, "run_in_sandbox", unavailable)
    monkeypatch.setattr(bt, "_ALLOW_INSECURE_HOST", False)
    out = bt.execute_bash("echo hi")
    assert "refused" in out
    assert "sandbox" in out.lower()


def test_execute_bash_insecure_optin_runs_on_host(bt, monkeypatch):
    # With the explicit opt-in, the host fallback runs (and its firewall applies).
    from mcp_servers.docker_sandbox import SandboxUnavailableError

    def unavailable(command, root, timeout):
        raise SandboxUnavailableError("Docker is not available")

    monkeypatch.setattr(bt, "run_in_sandbox", unavailable)
    monkeypatch.setattr(bt, "_ALLOW_INSECURE_HOST", True)
    out = bt.execute_bash("echo hello-host")
    assert "exit_code: 0" in out
    assert "hello-host" in out


class FakeDownloadResponse:
    def __init__(self, chunks, headers=None, status_code=200):
        self._chunks = chunks
        self.headers = headers or {}
        self.status_code = status_code
        self.is_redirect = status_code in {301, 302, 303, 307, 308}
        self.is_permanent_redirect = status_code in {301, 308}
        self.closed = False

    def iter_content(self, chunk_size):
        del chunk_size
        yield from self._chunks

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


def _mock_download(bt, monkeypatch, responses):
    calls = []

    class FakeSession:
        trust_env = True

        def mount(self, prefix, adapter):
            del prefix, adapter

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(bt.requests, "Session", FakeSession)
    monkeypatch.setattr(
        bt,
        "_validate_download_url",
        lambda _url: ("93.184.216.34",),
    )
    return calls


def test_download_file_streams_to_workspace_without_returning_content(bt, monkeypatch):
    payload = b"%PDF-test-payload"
    responses = [
        FakeDownloadResponse(
            [payload[:5], payload[5:]],
            {
                "Content-Type": "application/pdf",
                "Content-Length": str(len(payload)),
                "Content-Disposition": 'attachment; filename="paper.pdf"',
            },
        )
    ]
    calls = _mock_download(bt, monkeypatch, responses)

    result = bt.download_file("https://example.com/file", path="downloads/")

    target = bt._workspace_root() / "downloads" / "paper.pdf"
    assert target.read_bytes() == payload
    assert "OK: downloaded downloads/paper.pdf" in result
    assert "SHA-256:" in result
    assert payload.decode() not in result
    assert calls[0][0] == "https://93.184.216.34/file"
    assert calls[0][1]["headers"]["Host"] == "example.com"
    assert calls[0][1]["stream"] is True
    assert calls[0][1]["allow_redirects"] is False


def test_download_file_enforces_streaming_size_limit_and_cleans_partial(
    bt, monkeypatch
):
    responses = [FakeDownloadResponse([b"1234", b"5678"])]
    _mock_download(bt, monkeypatch, responses)

    result = bt.download_file(
        "https://example.com/file.bin", path="tmp/file.bin", max_bytes=6
    )

    assert "exceeded the 6-byte limit" in result
    assert not (bt._workspace_root() / "tmp" / "file.bin").exists()
    assert not list((bt._workspace_root() / "tmp").glob("*.part"))


def test_download_file_rejects_private_network_url(bt):
    result = bt.download_file("http://127.0.0.1/private")
    assert "non-public address" in result


def test_download_connects_to_the_validated_ip_not_a_second_dns_result(bt, monkeypatch):
    responses = [FakeDownloadResponse([b"safe"])]
    calls = []
    resolutions = []

    def resolve(hostname, port, **kwargs):
        resolutions.append((hostname, port, kwargs))
        return [
            (
                bt.socket.AF_INET,
                bt.socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", port),
            )
        ]

    class FakeSession:
        trust_env = True

        def mount(self, prefix, adapter):
            del prefix, adapter

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return responses.pop(0)

        def close(self):
            pass

    monkeypatch.setattr(bt.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(bt.requests, "Session", FakeSession)

    result = bt.download_file(
        "https://example.com/report",
        path="tmp/report.bin",
    )

    assert result.startswith("OK")
    assert [item[0] for item in resolutions] == ["example.com"]
    assert calls[0][0] == "https://93.184.216.34/report"
    assert calls[0][1]["headers"]["Host"] == "example.com"


def test_pinned_https_still_verifies_the_original_hostname(bt):
    adapter = bt._PinnedHTTPSAdapter("example.com")
    pool = adapter.poolmanager.connection_from_url("https://93.184.216.34/report")

    assert pool.assert_hostname == "example.com"
    assert pool.conn_kw["server_hostname"] == "example.com"


def test_download_file_rejects_destination_outside_workspace_before_network(
    bt, monkeypatch
):
    called = False

    class UnexpectedSession:
        def __init__(self):
            nonlocal called
            called = True

    monkeypatch.setattr(bt.requests, "Session", UnexpectedSession)
    result = bt.download_file("https://example.com/file", path="../escape.bin")

    assert "Access denied" in result
    assert called is False


def test_insecure_optin_still_blocks_outside_paths(bt, monkeypatch):
    from mcp_servers.docker_sandbox import SandboxUnavailableError

    def unavailable(command, root, timeout):
        raise SandboxUnavailableError("Docker is not available")

    monkeypatch.setattr(bt, "run_in_sandbox", unavailable)
    monkeypatch.setattr(bt, "_ALLOW_INSECURE_HOST", True)
    out = bt.execute_bash("cat /etc/passwd")
    assert "refused" in out


# --------------------------------------------------------------------------- #
# Sandbox enforcement: nothing escapes the workspace
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "func,args",
    [
        ("read_file", ("../../../etc/passwd",)),
        ("download_file", ("https://example.com/file", "../escape.bin")),
        ("write_file", ("../escape.txt", "x")),
        ("append_to_file", ("../escape.txt", "x")),
        ("edit_file", ("../escape.txt", "a", "b")),
        ("list_directory", ("../",)),
        ("create_directory", ("../escdir",)),
        ("delete_path", ("../escape.txt",)),
        ("move_path", ("../a", "../b")),
    ],
)
def test_paths_outside_workspace_are_denied(bt, func, args):
    result = getattr(bt, func)(*args)
    assert "Access denied" in result or "Error" in result


def test_absolute_path_outside_denied(bt):
    target = "C:/Windows/Temp/evil.txt" if sys.platform == "win32" else "/tmp/evil.txt"
    assert "Access denied" in bt.write_file(target, "x")


def test_cannot_delete_workspace_root(bt):
    assert "refusing to delete the workspace root" in bt.delete_path(".")


# --------------------------------------------------------------------------- #
# Host-fallback command firewall (only applied on the opt-in insecure host path).
# This is best-effort defense-in-depth, NOT the primary boundary; the container
# sandbox is. These tests pin the firewall's behavior as a unit.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "command",
    [
        "cat ../secret",  # parent escape
        "type ..\\..\\secret",  # parent escape (windows sep)
        "dir \\\\server\\share",  # UNC network path
        "echo $HOME/x",  # env var expansion
        "cat ${HOME}/x",  # ${VAR} expansion
        "cat $(echo /etc/passwd)",  # command substitution
        "echo `whoami`",  # backtick substitution
        "cat /etc/passwd",  # posix absolute (outside on any OS)
        "type ~/secret",  # home dir (posix sep)
        "type ~\\secret",  # home dir (windows sep)
        "echo %USERPROFILE%\\x",  # windows env expansion
        "echo hi > /etc/pwned.txt",  # redirect to outside path
    ],
)
def test_command_firewall_refuses_outside_references(bt, command):
    reason = bt._command_reaches_outside_workspace(command, bt._workspace_root())
    assert reason is not None, f"should have been refused: {command!r}"


@pytest.mark.parametrize(
    "command",
    [
        "echo hello",
        "ls -la",
        "type notes.txt",  # relative path
        "mkdir sub/child",  # relative nested path
        "echo hi > out.txt",  # relative redirect
        "grep -r foo .",
        "python script.py --flag",
        "echo https://example.com/a/b/c",  # URL is not a filesystem path
        "sort data.txt",
    ],
)
def test_command_firewall_allows_in_workspace_commands(bt, command):
    # The firewall must not refuse legitimate in-workspace commands.
    assert bt._command_reaches_outside_workspace(command, bt._workspace_root()) is None
