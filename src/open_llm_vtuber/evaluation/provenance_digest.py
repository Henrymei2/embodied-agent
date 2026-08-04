"""Binding a frozen result to the code that produced it.

Part 11 exists because of a specific failure. `implementation_digest` hashed
the raw *bytes* of a few source files, and two of those files were not tracked
in git. So a "frozen" research artifact was bound to bytes that (a) were not in
version control and (b) changed whenever a comment was edited or the repository
was checked out on a machine with different line-ending settings. The Part 4
baseline duly broke on a CRLF checkout while its actual results — 5/8 versus
2/8 — reproduced exactly.

The lesson is not "hash more things". It is that a digest should change when
the *behaviour* could have changed and not otherwise, and that a result naming
a revision which does not contain its own inputs has not named anything.

So this module fixes three properties:

**Line endings are normalized.** A file checked out CRLF and the same file
checked out LF are the same program, and must digest the same. Otherwise a
Windows contributor cannot reproduce a Linux baseline and the artifact reports
tampering where there is none.

**Digested files must be tracked.** An untracked input cannot be recovered from
the revision the manifest names, so binding a result to it is a promise that
cannot be kept. `require_tracked` fails closed rather than hashing whatever
happens to be on disk.

**The revision and the dirty flag travel with the digest.** `git rev-parse
HEAD` plus a clean-tree check turn "this code" into something another person
can actually obtain.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class ProvenanceDigestError(ValueError):
    """A frozen artifact cannot be bound to recoverable code."""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _normalized_bytes(path: Path) -> bytes:
    """File content with line endings collapsed to LF.

    CRLF → LF, and a lone CR → LF, so the three conventions a repository can be
    checked out with all produce the same digest for the same program.
    """
    raw = path.read_bytes()
    return raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def is_tracked(relative: str, *, root: Path | None = None) -> bool:
    """Whether git has this path, so a manifest can promise to recover it."""
    base = root or repository_root()
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        # No git available is not the same as an untracked file. Callers that
        # need certainty use `require_tracked`, which treats this as failure.
        return True
    return result.returncode == 0


def require_tracked(relatives: Sequence[str], *, root: Path | None = None) -> None:
    """Refuse to digest inputs the named revision could not supply."""
    base = root or repository_root()
    missing = [name for name in relatives if not (base / name).exists()]
    if missing:
        raise ProvenanceDigestError(f"digested files do not exist: {missing}")
    untracked = [name for name in relatives if not is_tracked(name, root=base)]
    if untracked:
        raise ProvenanceDigestError(
            "these digested files are not tracked by git, so a result naming a "
            f"revision cannot be reproduced from it: {untracked}"
        )


def implementation_digest(relatives: Sequence[str], *, root: Path | None = None) -> str:
    """Digest the named sources, stable across line-ending conventions."""
    base = root or repository_root()
    if not relatives:
        raise ProvenanceDigestError("an implementation digest needs at least one file")
    digest = hashlib.sha256()
    # Sorted so the caller's argument order cannot change the result; the file
    # names are hashed too, so moving code between listed files still shows up.
    for relative in sorted(relatives):
        path = base / relative
        if not path.exists():
            raise ProvenanceDigestError(f"digested file does not exist: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(_normalized_bytes(path))
        digest.update(b"\x00")
    return digest.hexdigest()


@dataclass(frozen=True)
class CodeRevision:
    """Where the code came from, as far as git can say."""

    revision: str
    dirty: bool
    available: bool

    def describe(self) -> str:
        if not self.available:
            return "unavailable"
        return f"{self.revision}{'+dirty' if self.dirty else ''}"


def code_revision(*, root: Path | None = None) -> CodeRevision:
    """The commit a result should name, and whether the tree was clean."""
    base = root or repository_root()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=30,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return CodeRevision(revision="", dirty=False, available=False)
    if head.returncode != 0:
        return CodeRevision(revision="", dirty=False, available=False)
    return CodeRevision(
        revision=head.stdout.strip(),
        dirty=bool(status.stdout.strip()),
        available=True,
    )


def freeze_guard(*, allow_dirty: bool, root: Path | None = None) -> CodeRevision:
    """Refuse to write a frozen artifact from a tree nobody else can obtain.

    `allow_dirty` exists because a researcher iterating locally needs to
    regenerate a baseline before committing. It is not a default, and when it
    is used the artifact records it, so a reader can tell a reproducible
    result from a convenient one.
    """
    revision = code_revision(root=root)
    if not revision.available:
        raise ProvenanceDigestError(
            "cannot freeze an artifact outside a git repository; the result "
            "would name no recoverable revision"
        )
    if revision.dirty and not allow_dirty:
        raise ProvenanceDigestError(
            "refusing to freeze from a dirty worktree: the digest would describe "
            "code that is not in any commit. Commit first, or pass --allow-dirty "
            "and accept that the artifact is marked irreproducible"
        )
    return revision
