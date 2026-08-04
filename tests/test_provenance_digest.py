"""Part 11: a frozen result must name code that can actually be recovered."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.open_llm_vtuber.evaluation.provenance_digest import (
    ProvenanceDigestError,
    code_revision,
    freeze_guard,
    implementation_digest,
    is_tracked,
    repository_root,
    require_tracked,
)

ROOT = repository_root()


def _write(tmp_path: Path, name: str, text: str, newline: str) -> Path:
    path = tmp_path / name
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def test_line_endings_do_not_change_the_digest(tmp_path):
    """A CRLF checkout and an LF checkout are the same program.

    This is the defect that broke the Part 4 baseline: its results reproduced
    exactly and its digest did not.
    """
    body = "def run():\n    return 42\n"
    _write(tmp_path, "a.py", body, "\n")
    lf = implementation_digest(["a.py"], root=tmp_path)

    _write(tmp_path, "a.py", body, "\r\n")
    crlf = implementation_digest(["a.py"], root=tmp_path)

    _write(tmp_path, "a.py", body, "\r")
    cr = implementation_digest(["a.py"], root=tmp_path)

    assert lf == crlf == cr


def test_a_real_change_still_changes_the_digest(tmp_path):
    """Normalizing line endings must not normalize away behaviour."""
    _write(tmp_path, "a.py", "def run():\n    return 42\n", "\n")
    before = implementation_digest(["a.py"], root=tmp_path)
    _write(tmp_path, "a.py", "def run():\n    return 43\n", "\n")
    assert implementation_digest(["a.py"], root=tmp_path) != before


def test_moving_code_between_digested_files_is_visible(tmp_path):
    """File names are hashed too, so a rename cannot hide a change."""
    _write(tmp_path, "a.py", "X = 1\n", "\n")
    _write(tmp_path, "b.py", "Y = 2\n", "\n")
    both = implementation_digest(["a.py", "b.py"], root=tmp_path)

    _write(tmp_path, "a.py", "Y = 2\n", "\n")
    _write(tmp_path, "b.py", "X = 1\n", "\n")
    assert implementation_digest(["a.py", "b.py"], root=tmp_path) != both


def test_argument_order_cannot_change_the_digest(tmp_path):
    _write(tmp_path, "a.py", "X = 1\n", "\n")
    _write(tmp_path, "b.py", "Y = 2\n", "\n")
    assert implementation_digest(["a.py", "b.py"], root=tmp_path) == (
        implementation_digest(["b.py", "a.py"], root=tmp_path)
    )


def test_an_empty_or_missing_input_is_refused(tmp_path):
    with pytest.raises(ProvenanceDigestError, match="at least one file"):
        implementation_digest([], root=tmp_path)
    with pytest.raises(ProvenanceDigestError, match="does not exist"):
        implementation_digest(["absent.py"], root=tmp_path)


# --- tracking -----------------------------------------------------------------


def _git(tmp_path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=False)


def test_an_untracked_input_cannot_be_frozen(tmp_path):
    """A revision that does not contain its own inputs has promised nothing."""
    _git(tmp_path, "init")
    _write(tmp_path, "tracked.py", "X = 1\n", "\n")
    _write(tmp_path, "loose.py", "Y = 2\n", "\n")
    _git(tmp_path, "add", "tracked.py")

    require_tracked(["tracked.py"], root=tmp_path)
    with pytest.raises(ProvenanceDigestError, match="not tracked by git"):
        require_tracked(["tracked.py", "loose.py"], root=tmp_path)


def test_every_source_the_committed_baselines_digest_is_tracked():
    """The exact failure Part 11 exists to close, asserted on the real repo."""
    from src.open_llm_vtuber.evaluation.part4 import PART4_DIGESTED_SOURCES
    from src.open_llm_vtuber.evaluation.part5 import PART5_DIGESTED_SOURCES

    for sources in (PART4_DIGESTED_SOURCES, PART5_DIGESTED_SOURCES):
        require_tracked(sources)
        for relative in sources:
            assert (ROOT / relative).exists()
            assert is_tracked(relative), relative


def test_a_missing_file_is_reported_before_a_tracking_check(tmp_path):
    _git(tmp_path, "init")
    with pytest.raises(ProvenanceDigestError, match="do not exist"):
        require_tracked(["absent.py"], root=tmp_path)


# --- revision -----------------------------------------------------------------


def test_the_repository_reports_a_revision():
    revision = code_revision()
    assert revision.available
    assert len(revision.revision) == 40
    assert revision.describe().startswith(revision.revision)


def test_freezing_outside_a_repository_is_refused(tmp_path):
    with pytest.raises(ProvenanceDigestError, match="outside a git repository"):
        freeze_guard(allow_dirty=False, root=tmp_path)


def test_freezing_from_a_dirty_tree_needs_an_explicit_admission(tmp_path):
    """The convenient path must be visible in the artifact, not silent."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    _write(tmp_path, "a.py", "X = 1\n", "\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-m", "initial")

    clean = freeze_guard(allow_dirty=False, root=tmp_path)
    assert clean.dirty is False

    _write(tmp_path, "a.py", "X = 2\n", "\n")
    with pytest.raises(ProvenanceDigestError, match="dirty worktree"):
        freeze_guard(allow_dirty=False, root=tmp_path)
    assert freeze_guard(allow_dirty=True, root=tmp_path).dirty is True
