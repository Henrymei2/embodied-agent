from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .workspace_manager import ensure_workspace, resolve_workspace_path


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

            # Re-resolve to enforce workspace boundary even with symlinks.
            safe_path = resolve_workspace_path(path, workspace.root)
            candidates.append(safe_path)

    return candidates


def retrieve_workspace_documents(
    query: str,
    workspace_dir: str | Path | None = None,
    include_dirs: list[str] | None = None,
    max_documents: int = 6,
    max_chars_per_document: int = 2_000,
) -> list[RetrievedWorkspaceDocument]:
    """
    Retrieve relevant workspace documents for a user query.

    This is the first RAG implementation:
    - scans workspace context directories
    - keyword-scores files
    - returns the best matching snippets
    """
    workspace = ensure_workspace(workspace_dir)
    query_tokens = tokenize(query)

    retrieved: list[RetrievedWorkspaceDocument] = []

    for path in iter_candidate_files(workspace.root, include_dirs):
        content = read_text_file_safely(path, max_chars=max_chars_per_document)
        if not content.strip():
            continue

        relative_path = str(path.relative_to(workspace.root))
        score, reason = score_document(query_tokens, relative_path, content)

        if score <= 0:
            continue

        retrieved.append(
            RetrievedWorkspaceDocument(
                relative_path=relative_path,
                content=content.strip(),
                score=score,
                reason=reason,
            )
        )

    retrieved.sort(key=lambda item: item.score, reverse=True)
    return retrieved[:max_documents]
