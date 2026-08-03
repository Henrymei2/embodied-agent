"""The private half of the provenance split: content the ledger never holds.

The ledger is meant to be inspectable — who claimed what, on whose authority,
supported by which evidence. None of that requires the sentence the user
actually said, so the sentence lives here instead, behind an opaque reference.

Two properties matter more than storage efficiency:

*Deletion is real.* When a user deletes a memory, the bytes go, and so does
every artifact derived from them — the summary that quoted it, the embedding
that encodes it. A "deleted" fact that a similarity search can still surface has
not been deleted. Derived artifacts are therefore registered against the
reference that produced them and purged with it.

*Deletion cannot be undone by writing the same content again.* A reference is
retired permanently, so a later ingest of the same text lands under a new
reference and cannot resurrect the old ledger entry.

Digests are keyed with a store-local secret. An unkeyed SHA-256 of "Pep" is a
constant that anybody can compute, which would let a tombstoned digest still
confirm a guess about content that was supposed to be gone.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from filelock import FileLock, Timeout

from .records import PRIVACY_CLASSES, ProvenanceValidationError

_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,119}$")
_MAX_CONTENT_BYTES = 1_000_000
_MAX_ITEMS = 100_000
_INDEX_VERSION = 1


class ContentStoreError(RuntimeError):
    """A private-content operation would violate the storage contract."""


class ContentDeletedError(ContentStoreError):
    """The reference existed and was deleted; it can never be read again."""


@dataclass(frozen=True)
class StoredContent:
    """What the ledger needs to describe content it is not allowed to hold."""

    content_ref: str
    content_digest: str
    privacy_class: str
    byte_length: int
    stored_at: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


class PrivateContentStore:
    """Content-addressed private storage with governed, irreversible deletion."""

    def __init__(self, root: Path, *, max_items: int = _MAX_ITEMS) -> None:
        self.root = Path(root).resolve()
        self._blobs = self.root / "blobs"
        self._index_path = self.root / "index.json"
        self._key_path = self.root / "digest.key"
        self._max_items = max(1, min(int(max_items), _MAX_ITEMS))
        self._lock = threading.RLock()
        self._file_lock = FileLock(str(self.root / "content.lock"), timeout=5)
        self._items: dict[str, dict[str, Any]] = {}
        self._tombstones: dict[str, dict[str, Any]] = {}
        self._derived: dict[str, set[str]] = {}
        self._purged: set[str] = set()

        self._blobs.mkdir(parents=True, exist_ok=True)
        try:
            with self._file_lock:
                self._key = self._load_or_create_key()
                self._load_index()
        except Timeout as exc:
            raise ContentStoreError("private content store is busy") from exc

    @contextmanager
    def _synchronized(self):
        """Refresh the index while serializing writers across processes."""

        try:
            with self._lock:
                with self._file_lock:
                    self._load_index()
                    yield
        except Timeout as exc:
            raise ContentStoreError("private content store is busy") from exc

    def _load_or_create_key(self) -> bytes:
        if self._key_path.exists():
            key = self._key_path.read_bytes().strip()
            if len(key) >= 32:
                return key
            raise ContentStoreError("digest key is present but unusably short")
        key = secrets.token_hex(32).encode("ascii")
        _atomic_write(self._key_path, key)
        try:
            os.chmod(self._key_path, 0o600)
        except OSError:
            # Best effort: POSIX modes do not carry meaning on every platform,
            # and a store that refuses to open is worse than one whose key
            # inherits directory permissions.
            pass
        return key

    def _load_index(self) -> None:
        if not self._index_path.exists():
            self._items = {}
            self._tombstones = {}
            self._derived = {}
            self._purged = set()
            return
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContentStoreError(
                f"private content index is unreadable: {exc}"
            ) from exc
        if not isinstance(payload, dict) or payload.get("version") != _INDEX_VERSION:
            raise ContentStoreError("private content index has an unknown version")
        items = payload.get("items")
        tombstones = payload.get("tombstones")
        derived = payload.get("derived")
        if not isinstance(items, dict) or not isinstance(tombstones, dict):
            raise ContentStoreError("private content index is malformed")
        self._items = {str(key): value for key, value in items.items()}
        self._tombstones = {str(key): value for key, value in tombstones.items()}
        self._derived = {
            str(key): {str(item) for item in value}
            for key, value in (derived or {}).items()
            if isinstance(value, list)
        }
        self._purged = {str(item) for item in payload.get("purged", [])}

    def _save_index(self) -> None:
        payload = {
            "version": _INDEX_VERSION,
            "items": self._items,
            "tombstones": self._tombstones,
            "derived": {key: sorted(value) for key, value in self._derived.items()},
            "purged": sorted(self._purged),
        }
        _atomic_write(
            self._index_path,
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )

    def digest(self, content: str | bytes) -> str:
        """Keyed digest: comparable inside this store, useless outside it."""

        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        return hmac.new(self._key, raw, "sha256").hexdigest()

    def _blob_path(self, content_ref: str) -> Path:
        """Address a blob by a digest of its reference, not by the reference.

        A reference is a valid identifier, which is not the same as a valid
        filename: ``a:b`` is illegal on Windows, and ``blob-A`` and ``blob-a``
        are two different references that a case-insensitive filesystem would
        happily merge into one file. Hashing sidesteps both, and traversal
        along with them.
        """

        name = hashlib.sha256(content_ref.encode("utf-8")).hexdigest()[:40]
        return self._blobs / f"{name}.bin"

    def put(
        self,
        content_ref: str,
        content: str | bytes,
        *,
        privacy_class: str = "private",
    ) -> StoredContent:
        """Store content under a reference that has never been used before."""

        if not isinstance(content_ref, str) or not _REF.fullmatch(content_ref):
            raise ProvenanceValidationError(
                "content_ref must be a bounded opaque identifier"
            )
        if privacy_class not in PRIVACY_CLASSES:
            raise ProvenanceValidationError(
                f"privacy_class must be one of {sorted(PRIVACY_CLASSES)}"
            )
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        if len(raw) > _MAX_CONTENT_BYTES:
            raise ContentStoreError(
                f"content exceeds the {_MAX_CONTENT_BYTES}-byte item limit"
            )
        with self._synchronized():
            if content_ref in self._tombstones:
                raise ContentDeletedError(
                    f"content_ref {content_ref!r} was deleted and cannot be reused"
                )
            if content_ref in self._items:
                raise ContentStoreError(
                    f"content_ref {content_ref!r} already exists; corrections must "
                    "supersede rather than overwrite"
                )
            if len(self._items) >= self._max_items:
                raise ContentStoreError("private content store is at its item limit")
            stored = StoredContent(
                content_ref=content_ref,
                content_digest=self.digest(raw),
                privacy_class=privacy_class,
                byte_length=len(raw),
                stored_at=_utc_now().isoformat(),
            )
            _atomic_write(self._blob_path(content_ref), raw)
            self._items[content_ref] = {
                "content_digest": stored.content_digest,
                "privacy_class": privacy_class,
                "byte_length": stored.byte_length,
                "stored_at": stored.stored_at,
            }
            self._save_index()
            return stored

    def get(self, content_ref: str) -> bytes:
        """Read private content. Only callers holding the reference may."""

        with self._synchronized():
            if content_ref in self._tombstones:
                raise ContentDeletedError(
                    f"content_ref {content_ref!r} was deleted on "
                    f"{self._tombstones[content_ref]['deleted_at']}"
                )
            if content_ref not in self._items:
                raise KeyError(content_ref)
            return self._blob_path(content_ref).read_bytes()

    def get_text(self, content_ref: str) -> str:
        return self.get(content_ref).decode("utf-8")

    def describe(self, content_ref: str) -> StoredContent | None:
        with self._synchronized():
            item = self._items.get(content_ref)
            if item is None:
                return None
            return StoredContent(content_ref=content_ref, **item)

    def register_derived(self, content_ref: str, artifact_key: str) -> None:
        """Record that an artifact was derived from stored private content.

        Summaries and embeddings outlive the text they came from unless
        something remembers the link; this is that something.
        """

        with self._synchronized():
            if content_ref in self._tombstones:
                raise ContentDeletedError(
                    f"content_ref {content_ref!r} was deleted; nothing may be "
                    "derived from it"
                )
            if content_ref not in self._items:
                raise KeyError(content_ref)
            self._derived.setdefault(content_ref, set()).add(str(artifact_key))
            self._save_index()

    def derived_keys(self, content_ref: str) -> frozenset[str]:
        with self._synchronized():
            return frozenset(self._derived.get(content_ref, set()))

    def purged_artifact_keys(self) -> frozenset[str]:
        """Every derived-artifact key that deletion has retired for good."""

        with self._synchronized():
            return frozenset(self._purged)

    def delete(
        self, content_ref: str, *, reason: str = "user_request"
    ) -> frozenset[str]:
        """Delete content and return the derived artifact keys to purge.

        Idempotent: deleting an already-deleted reference reports the same keys
        rather than failing, because a retry of a deletion must not leave a
        caller believing the content is still there.
        """

        with self._synchronized():
            if content_ref in self._tombstones:
                return frozenset(self._derived.get(content_ref, set()))
            if content_ref not in self._items:
                raise KeyError(content_ref)
            path = self._blob_path(content_ref)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            item = self._items.pop(content_ref)
            keys = frozenset(self._derived.get(content_ref, set()))
            self._purged.update(keys)
            self._tombstones[content_ref] = {
                "deleted_at": _utc_now().isoformat(),
                "reason": str(reason)[:64],
                "byte_length": item["byte_length"],
            }
            self._save_index()
            return keys

    def is_deleted(self, content_ref: str) -> bool:
        with self._synchronized():
            return content_ref in self._tombstones

    def export(self, content_refs: Iterable[str]) -> dict[str, str | None]:
        """Export content for the user who owns it.

        Deleted references export as ``None`` rather than being omitted, so an
        export shows that something was removed instead of quietly shrinking.
        """

        exported: dict[str, str | None] = {}
        for content_ref in content_refs:
            if self.is_deleted(content_ref):
                exported[content_ref] = None
                continue
            try:
                exported[content_ref] = self.get_text(content_ref)
            except KeyError:
                exported[content_ref] = None
        return exported
