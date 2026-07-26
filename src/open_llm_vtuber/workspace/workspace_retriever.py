from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..skill_state import EXCLUDED_SKILL_SUBDIRS
from .embedding_cache import EmbeddingCache, chunk_key
from .embeddings import cosine_similarity, embed, get_provider
from .workspace_manager import ensure_workspace, resolve_workspace_path

# One memory's worth of context. Measured against all-MiniLM-L6-v2: a chunk
# holding three unrelated diary entries scored 0.21-0.24 against a query about
# one of them, where the entry on its own scored 0.34-0.40. Packing unrelated
# paragraphs together is what blurs a match, so the budget is small enough that
# a typical entry stays whole and alone.
MAX_CHUNK_CHARS = 400
# Below this a paragraph is a fragment (a date heading, a stray line) rather
# than something to recall, so it rides along with the entry that follows.
# Kept tight: a short diary line like "We fixed the dishwasher." is a whole
# memory, and merging it into the next one costs it its own match.
MIN_CHUNK_CHARS = 40
MAX_INDEXED_CHARS = 40_000
# A ceiling only for pathological input, derived so it can never discard text
# the reader accepted: the shortest possible chunk is MIN_CHUNK_CHARS, so this
# many of them already covers MAX_INDEXED_CHARS. A tighter count would silently
# make the tail of a long memory file unsearchable — the "it doesn't remember"
# failure this whole path exists to avoid.
MAX_CHUNKS_PER_FILE = MAX_INDEXED_CHARS // MIN_CHUNK_CHARS
# A leading YAML metadata block, as skill files carry.
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n?(.*)$", re.DOTALL)

# How the two signals mix once both are available.
_KEYWORD_WEIGHT = 0.4
_SEMANTIC_WEIGHT = 0.6
# Keyword score at which the keyword component reaches half of its range.
_KEYWORD_HALF_POINT = 8.0
# Similarity a passage must reach to be recalled on meaning alone. Every text
# resembles every other text a little, so without a floor the prompt fills with
# whatever was merely nearby — which reads as a companion that misremembers.
# From the same measurements: genuinely related pairs scored 0.22-0.40 and
# unrelated ones stayed under 0.15.
_MIN_SEMANTIC_SIMILARITY = 0.2
# A bare heading ("# Daily Memory - 2026-07-25") can drift over that floor
# without holding a memory at all, so meaning-only matches need some substance.
_MIN_SEMANTIC_CHARS = 40


TEXT_FILE_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".py",
}

DEFAULT_RETRIEVAL_DIRS = [
    "knowledge",
    "skills",
    "memory",
]

MAX_FILE_BYTES = 250_000

STOP_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "you",
    "your",
    "are",
    "was",
    "were",
    "will",
    "can",
    "could",
    "would",
    "should",
    "need",
    "needs",
    "use",
    "using",
    "when",
    "what",
    "how",
    "why",
    "where",
    "who",
    "which",
    "then",
    "than",
    "into",
    "from",
    "about",
}


@dataclass(frozen=True)
class RetrievedWorkspaceDocument:
    relative_path: str
    content: str
    score: int
    reason: str


def tokenize(text: str) -> set[str]:
    """
    Simple keyword tokenizer for initial RAG.

    This is intentionally lightweight. It gives us a working retrieval layer
    without adding embeddings/vector database dependencies yet.
    """
    tokens = {
        token.lower()
        for token in re.findall(r"[a-zA-Z0-9_/-]+", text)
        if len(token) >= 3
    }

    return {token for token in tokens if token not in STOP_WORDS}


def read_text_file_safely(path: Path, max_chars: int) -> str:
    if not path.exists() or not path.is_file():
        return ""

    if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
        return ""

    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""

        content = path.read_text(encoding="utf-8", errors="ignore")
        return content[:max_chars]
    except OSError:
        return ""


def score_document(
    query_tokens: set[str], relative_path: str, content: str
) -> tuple[int, str]:
    """
    Score a workspace file against the user query.

    Higher score means more likely relevant.
    """
    if not content.strip():
        return 0, "empty"

    path_tokens = tokenize(relative_path)
    content_tokens = tokenize(content)

    path_matches = query_tokens.intersection(path_tokens)
    content_matches = query_tokens.intersection(content_tokens)

    score = 0
    score += len(path_matches) * 5
    score += len(content_matches) * 2

    # Useful default boosts for embodied-agent workspace context.
    lowered_path = relative_path.lower()

    if lowered_path.startswith("skills/"):
        score += 3
    elif lowered_path.startswith("knowledge/"):
        score += 3
    elif lowered_path.startswith("memory/"):
        score += 2

    # If query is empty, still return useful workspace context.
    if not query_tokens:
        if lowered_path.startswith("skills/") or lowered_path.startswith("knowledge/"):
            score += 2
        elif lowered_path.startswith("memory/"):
            score += 1

    if score <= 0:
        return 0, "no query overlap"

    reasons = []
    if path_matches:
        reasons.append(f"path matches: {', '.join(sorted(path_matches))}")
    if content_matches:
        preview = ", ".join(sorted(content_matches)[:8])
        reasons.append(f"content matches: {preview}")
    if lowered_path.startswith(("skills/", "knowledge/", "memory/")):
        reasons.append("workspace context file")

    return score, "; ".join(reasons)


def _is_unapproved_skill(path: Path, workspace_root: Path) -> bool:
    """True for a skill the user has not approved.

    Drafts and retired skills live under `skills/`, but must never reach the
    prompt: retrieving a draft would let the agent change its own behavior by
    writing a file, which is exactly what the review gate exists to prevent.
    """
    try:
        relative = path.relative_to(workspace_root)
    except ValueError:
        return False
    parts = relative.parts
    return (
        len(parts) > 2 and parts[0] == "skills" and parts[1] in EXCLUDED_SKILL_SUBDIRS
    )


def iter_candidate_files(
    workspace_dir: str | Path | None = None,
    include_dirs: list[str] | None = None,
) -> list[Path]:
    workspace = ensure_workspace(workspace_dir)
    include_dirs = include_dirs or DEFAULT_RETRIEVAL_DIRS

    candidates: list[Path] = []

    for dirname in include_dirs:
        directory = resolve_workspace_path(dirname, workspace.root)
        if not directory.exists() or not directory.is_dir():
            continue

        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix.lower() not in TEXT_FILE_SUFFIXES:
                continue
            if _is_unapproved_skill(path, workspace.root):
                continue

            # Re-resolve to enforce workspace boundary even with symlinks.
            safe_path = resolve_workspace_path(path, workspace.root)
            candidates.append(safe_path)

    return candidates


def strip_frontmatter(content: str) -> str:
    """Drop a leading `---` metadata block.

    Skill files open with YAML holding their name, purpose and timestamps. Those
    words match a query as readily as the procedure does, so without this the
    passage handed to the model is the file's header rather than its steps. The
    metadata that matters is repeated in the body anyway.
    """
    match = _FRONTMATTER_RE.match(content)
    return match.group(1) if match else content


def split_into_chunks(
    content: str,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[str]:
    """Split a document into one chunk per remembered thing.

    Whole-file matching answers "which file is about this", but what the prompt
    needs is the passage that actually says it — one day in a month of diary
    entries, not the month.

    Paragraphs are therefore kept apart rather than packed together: three
    unrelated entries sharing a chunk measured 0.21-0.24 against a query about
    one of them, where that entry alone measured 0.34-0.40. Only fragments too
    short to mean anything on their own — a bare date heading, a one-word line —
    are merged forward, so they arrive as context on the entry they introduce.
    """
    paragraphs = [
        block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()
    ]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            # An oversized paragraph is split on line boundaries as a last resort.
            for line in paragraph.splitlines():
                if len(current) + len(line) + 1 > max_chars and current:
                    chunks.append(current)
                    current = ""
                current = f"{current}\n{line}".strip() if current else line
            continue

        merges_forward = (
            current
            and len(current) < MIN_CHUNK_CHARS
            and len(current) + len(paragraph) + 2 <= max_chars
        )
        if merges_forward:
            current = f"{current}\n\n{paragraph}"
            continue
        if current:
            chunks.append(current)
        current = paragraph

    if current:
        chunks.append(current)
    return chunks[:MAX_CHUNKS_PER_FILE]


def _embed_chunks(
    chunks: Sequence[str],
    cache: EmbeddingCache,
    provider_name: str,
) -> list[list[float]] | None:
    """Embed chunks, reusing cached vectors and embedding only what is new."""
    vectors: list[list[float] | None] = []
    missing: list[tuple[int, str]] = []

    for index, chunk in enumerate(chunks):
        cached = cache.get(chunk_key(provider_name, chunk))
        vectors.append(cached)
        if cached is None:
            missing.append((index, chunk))

    if missing:
        fresh = embed([chunk for _index, chunk in missing])
        if fresh is None:
            return None
        for (index, chunk), vector in zip(missing, fresh):
            vectors[index] = vector
            cache.put(chunk_key(provider_name, chunk), vector)

    if any(vector is None for vector in vectors):
        # Callers index this list against `chunks`, so dropping a gap would
        # silently score every later chunk against the wrong passage. Falling
        # back to keywords for this file is the safe answer.
        return None
    return [vector for vector in vectors if vector is not None]


def retrieve_workspace_documents(
    query: str,
    workspace_dir: str | Path | None = None,
    include_dirs: list[str] | None = None,
    max_documents: int = 6,
    max_chars_per_document: int = 2_000,
) -> list[RetrievedWorkspaceDocument]:
    """Retrieve the workspace passages most relevant to a query.

    Scoring is hybrid. Keyword overlap is precise about names, dates and IDs
    that an embedding blurs; embeddings find the memory that means the same
    thing in different words. Each contributes a normalized share, so recall
    stays sensible when only one of them is available — and when no embedding
    provider loads at all, this is exactly the keyword behavior it replaced.
    """
    workspace = ensure_workspace(workspace_dir)
    query_tokens = tokenize(query)
    provider = get_provider() if query.strip() else None
    query_vector = None
    cache: EmbeddingCache | None = None

    if provider is not None:
        embedded_query = embed([query])
        if embedded_query:
            query_vector = embedded_query[0]
            cache = EmbeddingCache(workspace.root)

    retrieved: list[RetrievedWorkspaceDocument] = []

    for path in iter_candidate_files(workspace.root, include_dirs):
        content = read_text_file_safely(path, max_chars=MAX_INDEXED_CHARS)
        if not content.strip():
            continue

        relative_path = str(path.relative_to(workspace.root))
        body = strip_frontmatter(content)
        if relative_path.replace("\\", "/").startswith("skills/"):
            # A skill is one instruction, not a collection of memories: handing
            # the model its title without its steps would be worse than not
            # recalling it at all.
            chunks = [body.strip()]
        else:
            chunks = split_into_chunks(body) or [body.strip()]

        chunk_vectors: list[list[float]] | None = None
        if query_vector is not None and cache is not None:
            chunk_vectors = _embed_chunks(chunks, cache, provider.name)

        best: tuple[float, str, str] | None = None
        for index, chunk in enumerate(chunks):
            keyword_score, reason = score_document(query_tokens, relative_path, chunk)
            similarity = 0.0
            if chunk_vectors is not None and index < len(chunk_vectors):
                similarity = cosine_similarity(query_vector, chunk_vectors[index])
                if keyword_score <= 0 and (
                    similarity < _MIN_SEMANTIC_SIMILARITY
                    or len(chunk.strip()) < _MIN_SEMANTIC_CHARS
                ):
                    # Nothing in common but a faint family resemblance.
                    similarity = 0.0

            combined = _combine(keyword_score, similarity)
            if combined <= 0:
                continue
            if similarity > 0:
                reason = (
                    f"{reason}; semantic match {similarity:.2f}"
                    if reason and reason != "no query overlap"
                    else f"semantic match {similarity:.2f}"
                )
            if best is None or combined > best[0]:
                best = (combined, chunk, reason)

        if best is None:
            continue

        combined, chunk, reason = best
        retrieved.append(
            RetrievedWorkspaceDocument(
                relative_path=relative_path,
                content=chunk[:max_chars_per_document].strip(),
                score=round(combined, 4),
                reason=reason,
            )
        )

    if cache is not None:
        cache.save()

    retrieved.sort(key=lambda item: item.score, reverse=True)
    return retrieved[:max_documents]


def _combine(keyword_score: int, similarity: float) -> float:
    """Blend the two signals onto one scale.

    Keyword scores are unbounded counts, so they are squashed before mixing;
    otherwise a page that repeats a common word would outrank the passage that
    actually answers the question.
    """
    keyword_component = keyword_score / (keyword_score + _KEYWORD_HALF_POINT)
    if similarity <= 0:
        # No embeddings: keep the original ordering by leaving the keyword
        # score's shape intact rather than flattening it into the blend.
        return keyword_score
    return round(_KEYWORD_WEIGHT * keyword_component + _SEMANTIC_WEIGHT * similarity, 6)
