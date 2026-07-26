"""Local text embeddings for workspace recall, with a keyword-only fallback.

Keyword overlap only finds a memory when the user happens to reuse its words.
"Did I ever tell you about my sister?" does not overlap with "Mei mentioned her
younger sibling is visiting", so the companion answers "you never told me" about
something it wrote down itself — the single most damaging thing a companion can
get wrong.

Two providers, both local, tried in configured order:

* `sentence_transformers` — a small model (~90 MB) downloaded once, then offline.
* `ollama` — embeddings from the Ollama server this project already talks to,
  which adds no Python dependency at all.

Neither is required. When no provider loads, `embed()` returns None and the
retriever falls back to keyword scoring, so recall degrades rather than breaks —
the same rule the voice path follows.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Sequence

from loguru import logger

DEFAULT_SENTENCE_TRANSFORMERS_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_URL = "http://localhost:11434"
_OLLAMA_TIMEOUT_SECONDS = 20.0
_MAX_BATCH = 64


class EmbeddingProvider:
    """A local embedder. `name` identifies the vectors in the cache."""

    name = "none"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        raise NotImplementedError


class SentenceTransformerProvider(EmbeddingProvider):
    """Embeddings from a locally downloaded sentence-transformers model."""

    def __init__(self, model_name: str = DEFAULT_SENTENCE_TRANSFORMERS_MODEL) -> None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self._model = SentenceTransformer(model_name)
        self.name = f"st:{model_name}"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), batch_size=_MAX_BATCH, show_progress_bar=False
        )
        return [list(map(float, vector)) for vector in vectors]


class OllamaProvider(EmbeddingProvider):
    """Embeddings from the local Ollama server, which needs no new dependency."""

    def __init__(
        self,
        model_name: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
    ) -> None:
        self._model_name = model_name
        self._base_url = base_url.rstrip("/")
        self.name = f"ollama:{model_name}"
        # Fail during construction, so a missing model falls back to keywords
        # instead of erroring once per turn.
        self.embed(["ready?"])

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            body = json.dumps({"model": self._model_name, "prompt": text}).encode()
            request = urllib.request.Request(
                f"{self._base_url}/api/embeddings",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(
                    request, timeout=_OLLAMA_TIMEOUT_SECONDS
                ) as response:
                    payload = json.loads(response.read())
            except (urllib.error.URLError, OSError, ValueError) as exc:
                raise RuntimeError(f"ollama embeddings unavailable: {exc}") from exc
            vector = payload.get("embedding")
            if not isinstance(vector, list) or not vector:
                raise RuntimeError("ollama returned an empty embedding")
            vectors.append([float(value) for value in vector])
        return vectors


def _provider_order() -> list[str]:
    configured = os.environ.get("EMBODIED_AGENT_EMBEDDINGS")
    if configured is None:
        return ["sentence_transformers", "ollama"]
    if configured.strip().lower() in {"off", "none", "0", "false"}:
        return []
    return [name.strip().lower() for name in configured.split(",") if name.strip()]


_provider: EmbeddingProvider | None = None
_resolved = False
_lock = threading.Lock()
_loading = threading.Event()


def warm_up() -> None:
    """Start loading the provider in the background, if it is not loaded yet.

    Loading a sentence-transformers model takes tens of seconds the first time.
    Paid on the first user message, that is a reply that appears to hang; paid
    at startup in a daemon thread, it is invisible. Until it finishes, recall
    simply uses keyword matching.
    """
    if _resolved or _loading.is_set():
        return
    _loading.set()
    thread = threading.Thread(
        target=_resolve_provider, name="embedding-warmup", daemon=True
    )
    thread.start()


def get_provider(wait_seconds: float = 0.0) -> EmbeddingProvider | None:
    """Return the loaded provider, or None while it is still loading.

    This never blocks a conversation turn by default: a reply that waits for a
    model to load is worse than a reply that recalls slightly less well.
    """
    if _resolved:
        return _provider
    if _loading.is_set():
        # Another thread is loading it; only a caller that explicitly asks to
        # wait (tests, the warm-up thread itself) blocks here.
        if wait_seconds <= 0:
            return None
        deadline = time.monotonic() + wait_seconds
        while not _resolved and time.monotonic() < deadline:
            time.sleep(0.05)
        return _provider
    return _resolve_provider()


def _resolve_provider() -> EmbeddingProvider | None:
    global _provider, _resolved
    with _lock:
        if _resolved:
            return _provider
        _provider = _load_first_available()
        _resolved = True
    return _provider


def _load_first_available() -> EmbeddingProvider | None:
    for name in _provider_order():
        try:
            if name == "sentence_transformers":
                provider: EmbeddingProvider = SentenceTransformerProvider(
                    os.environ.get(
                        "EMBODIED_AGENT_EMBEDDING_MODEL",
                        DEFAULT_SENTENCE_TRANSFORMERS_MODEL,
                    )
                )
            elif name == "ollama":
                provider = OllamaProvider(
                    os.environ.get(
                        "EMBODIED_AGENT_EMBEDDING_MODEL", DEFAULT_OLLAMA_MODEL
                    ),
                    os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_URL),
                )
            else:
                logger.warning(f"Embeddings: unknown provider {name!r}; skipping.")
                continue
        except Exception as exc:  # noqa: BLE001 - any failure means "try the next"
            logger.info(f"Embeddings: {name} unavailable ({exc}).")
            continue
        logger.info(f"Embeddings: using {provider.name} for workspace recall.")
        return provider

    logger.info(
        "Embeddings: no local provider available; workspace recall will use "
        "keyword matching only. Install sentence-transformers or pull an Ollama "
        "embedding model for paraphrase-tolerant memory."
    )
    return None


def reset_provider() -> None:
    """Forget the memoized provider (used by tests and after a config change)."""
    global _provider, _resolved
    with _lock:
        _provider = None
        _resolved = False
        _loading.clear()


def embed(texts: Sequence[str]) -> list[list[float]] | None:
    """Embed texts, or return None when no local provider is available."""
    if not texts:
        return []
    provider = get_provider()
    if provider is None:
        return None
    try:
        vectors = provider.embed(texts)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Embeddings: {provider.name} failed ({exc}); using keywords.")
        return None
    if len(vectors) != len(texts):
        logger.warning("Embeddings: provider returned the wrong number of vectors.")
        return None
    return vectors


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity clamped to [0, 1]; 0 for degenerate vectors."""
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))
