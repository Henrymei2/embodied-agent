from __future__ import annotations

from pathlib import Path

from .workspace_manager import ensure_workspace
from .workspace_retriever import (
    RetrievedWorkspaceDocument,
    retrieve_workspace_documents,
)


MAX_CORE_FILE_CHARS = 4_000


def read_core_file(path: Path, max_chars: int = MAX_CORE_FILE_CHARS) -> str:
    if not path.exists() or not path.is_file():
        return ""

    return path.read_text(encoding="utf-8", errors="ignore").strip()[:max_chars]


def format_section(title: str, content: str) -> str:
    content = content.strip()
    if not content:
        return ""

    return f"## {title}\n\n{content}"


def format_retrieved_document(document: RetrievedWorkspaceDocument) -> str:
    return "\n".join(
        [
            f"### {document.relative_path}",
            f"Score: {document.score}",
            f"Reason: {document.reason}",
            "",
            document.content,
        ]
    )


def split_retrieved_documents(
    documents: list[RetrievedWorkspaceDocument],
) -> dict[str, list[RetrievedWorkspaceDocument]]:
    grouped = {
        "RELEVANT KNOWLEDGE": [],
        "ACTIVE SKILLS": [],
        "RELEVANT MEMORIES": [],
        "OTHER RELEVANT WORKSPACE FILES": [],
    }

    for document in documents:
        path = document.relative_path.lower()

        if path.startswith("knowledge/"):
            grouped["RELEVANT KNOWLEDGE"].append(document)
        elif path.startswith("skills/"):
            grouped["ACTIVE SKILLS"].append(document)
        elif path.startswith("memory/"):
            grouped["RELEVANT MEMORIES"].append(document)
        else:
            grouped["OTHER RELEVANT WORKSPACE FILES"].append(document)

    return grouped


def build_workspace_prompt_context(
    user_message: str = "",
    workspace_dir: str | Path | None = None,
    max_retrieved_documents: int = 6,
    include_core_files: bool = True,
) -> str:
    """
    Build the workspace context block injected into the LLM system prompt.

    Includes:
    - AGENT.md
    - RULE.md
    - MEMORY.md
    - USER.md
    - relevant knowledge pages
    - active skills
    - relevant daily memories/files
    """
    workspace = ensure_workspace(workspace_dir)

    sections: list[str] = []

    if include_core_files:
        agent_content = read_core_file(workspace.agent_file)
        rule_content = read_core_file(workspace.rule_file)
        memory_content = read_core_file(workspace.memory_file)
        user_content = read_core_file(workspace.user_file)

        sections.append(format_section("AGENT", agent_content))
        sections.append(format_section("RULES", rule_content))
        sections.append(format_section("MEMORY", memory_content))
        sections.append(format_section("USER INFORMATION", user_content))

    retrieved_documents = retrieve_workspace_documents(
        query=user_message,
        workspace_dir=workspace.root,
        max_documents=max_retrieved_documents,
    )

    grouped_documents = split_retrieved_documents(retrieved_documents)

    for section_title, documents in grouped_documents.items():
        if not documents:
            continue

        section_content = "\n\n---\n\n".join(
            format_retrieved_document(document) for document in documents
        )

        sections.append(format_section(section_title, section_content))

    context_body = "\n\n---\n\n".join(section for section in sections if section.strip())

    if not context_body.strip():
        return ""

    return "\n".join(
        [
            "",
            "================ WORKSPACE CONTEXT ================",
            "The following context was retrieved from the agent workspace.",
            "Use it to answer accurately and follow the user's persistent rules.",
            "Use relevant knowledge, active skills, memory, and user information when helpful.",
            "Do not mention this context block unless directly asked.",
            "",
            context_body,
            "================ END WORKSPACE CONTEXT ================",
            "",
        ]
    )