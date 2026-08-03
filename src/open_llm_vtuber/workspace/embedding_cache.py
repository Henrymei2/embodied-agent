"""Cache for workspace chunk embeddings, keyed by content.

Embedding the whole workspace on every turn would add seconds to each reply for
files that almost never change. Vectors are therefore stored on disk under a
hash of the chunk text plus the provider name, so an edited file re-embeds only
its changed chunks, and switching providers cannot mix incompatible vectors.

The cache is a convenience, never a source of truth: any read or write problem
degrades to "embed it again" rather than failing the turn.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Sequence

from loguru import logger

CACHE_FILENAME = ".embedding-cache.json"
# Chunks are small; this bounds the file at a few megabytes.
_MAX_ENTRIES = 4_000


def chunk_key(provider_name: str, text: str) -> str:
    digest = hashlib.sha256(f"{provider_name}\x00{text}".encode("utf-8")).hexdigest()
    return digest[:32]


class EmbeddingCache:
    """Content-addressed vector store for one workspace."""

    def __init__(self, workspace_root: Path) -> None:
        self._path = workspace_root / CACHE_FILENAME
        self._entries: dict[str, list[float]] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            entries = payload.get("entries")
            if isinstance(entries, dict):
                self._entries = {
                    key: [float(value) for value in vector]
                    for key, vector in entries.items()
                    if isinstance(vector, list) and vector
                }
        except (OSError, ValueError, TypeError) as exc:
            # A corrupt cache costs one re-embed, not a failed conversation.
            logger.warning(f"Embeddings: cache unreadable ({exc}); rebuilding it.")
            self._entries = {}

    def get(self, key: str) -> list[float] | None:
        return self._entries.get(key)

    def put(self, key: str, vector: Sequence[float]) -> None:
        self._entries[key] = [float(value) for value in vector]
        self._dirty = True

    def purge(self, keys: Iterable[str]) -> int:
        """Drop vectors for content that has been deleted, and persist at once.

        A vector is a lossy copy of the text it came from — near enough that a
        similarity search can still surface a deleted memory. So deletion has
        to reach in here, and it has to be written to disk immediately rather
        than at the end of a turn that might never come.
        """

        removed = 0
        for key in keys:
            if self._entries.pop(key, None) is not None:
                removed += 1
        if removed:
            self._dirty = True
            self.save()
        return removed

    def save(self) -> None:
        if not self._dirty:
            return
        entries = self._entries
        if len(entries) > _MAX_ENTRIES:
            # Oldest-first is not knowable here, so keep an arbitrary bounded
            # subset: everything dropped is simply re-embedded when next needed.
            entries = dict(list(entries.items())[-_MAX_ENTRIES:])
            self._entries = entries
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"version": 1, "entries": entries}), encoding="utf-8"
            )
            temporary.replace(self._path)
            self._dirty = False
        except (OSError, TypeError) as exc:
            logger.warning(f"Embeddings: could not save cache ({exc}).")
