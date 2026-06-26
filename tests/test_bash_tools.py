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


def test_execute_bash_runs_in_workspace(bt):
    out = bt.execute_bash("echo hello-sandbox")
    assert "exit_code: 0" in out
    assert "hello-sandbox" in out


# --------------------------------------------------------------------------- #
# Sandbox enforcement: nothing escapes the workspace
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "func,args",
    [
        ("read_file", ("../../../etc/passwd",)),
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
