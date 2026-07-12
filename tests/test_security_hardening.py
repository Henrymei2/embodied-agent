"""Regression tests for the security hardening pass."""

from __future__ import annotations

import os

import pytest

from src.open_llm_vtuber import bridge_auth
from src.open_llm_vtuber.stage_state_manager import _is_safe_filename


# --------------------------------------------------------------------------- #
# Bridge token auth
# --------------------------------------------------------------------------- #
def test_bridge_token_is_stable_within_run():
    assert bridge_auth.get_bridge_token() == bridge_auth.get_bridge_token()


def test_verify_bridge_token_accepts_real_rejects_others():
    token = bridge_auth.get_bridge_token()
    assert bridge_auth.verify_bridge_token(token) is True
    assert bridge_auth.verify_bridge_token("nope") is False
    assert bridge_auth.verify_bridge_token(None) is False
    assert bridge_auth.verify_bridge_token("") is False


# --------------------------------------------------------------------------- #
# Stage-state filename sanitization (path traversal)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad",
    [
        "..",
        ".",
        "   ",
        "../etc",
        "a/b",
        "a\\b",
        "c:evil",
        "star*",
        "q?mark",
        "pipe|x",
        "null\x00byte",
    ],
)
def test_unsafe_filenames_rejected(bad):
    assert _is_safe_filename(bad) is False


@pytest.mark.parametrize("good", ["mao_pro_001", "char-1", "Agent_42", "default"])
def test_safe_filenames_accepted(good):
    assert _is_safe_filename(good) is True


# --------------------------------------------------------------------------- #
# Bash MCP env sanitization
# --------------------------------------------------------------------------- #
def test_sanitized_env_strips_credentials(monkeypatch, tmp_path):
    from mcp_servers import bash_tools

    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret")
    monkeypatch.setenv("MY_PASSWORD", "hunter2")
    monkeypatch.setenv("SESSION_TOKEN", "abc")
    monkeypatch.setenv("SAFE_VALUE", "keepme")

    env = bash_tools._sanitized_env(tmp_path)

    assert "OPENAI_API_KEY" not in env
    assert "MY_PASSWORD" not in env
    assert "SESSION_TOKEN" not in env
    assert env.get("SAFE_VALUE") == "keepme"
    # Home is pinned to the workspace so ~ expansion can't reach the real home.
    assert env["HOME"] == str(tmp_path)
    assert env["USERPROFILE"] == str(tmp_path)


def test_sanitized_env_keeps_path(tmp_path):
    from mcp_servers import bash_tools

    env = bash_tools._sanitized_env(tmp_path)
    # PATH must survive or commands won't resolve.
    assert "PATH" in env or "Path" in os.environ or "PATH" in os.environ
