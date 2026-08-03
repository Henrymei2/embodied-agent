from __future__ import annotations

from pathlib import Path
from typing import Sequence

from loguru import logger

from ..study import conditions, log_event
from .workspace_manager import ensure_workspace
from .workspace_retriever import (
    RetrievedWorkspaceDocument,
    retrieve_workspace_documents,
)


MAX_CORE_FILE_CHARS = 4_000

# Where a provenance ledger lives inside a workspace, if one has been created.
PROVENANCE_DIRNAME = "provenance"


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


def build_evidence_context(
    claim_keys: Sequence[str],
    workspace_dir: str | Path | None = None,
    max_records: int = 6,
) -> str:
    """Retrieve only the claims the current intention actually needs.

    Unlike document retrieval, this returns claims the agent can justify: each
    one still points at the evidence it came from, contested claims arrive
    marked as contested, and anything whose supporting content the user deleted
    is simply not there.

    Returns nothing when the workspace has no ledger, which is the case until
    one is created — the seam exists without changing what existing
    installations put in their prompts.
    """

    if not claim_keys or not conditions().memory:
        return ""

    workspace = ensure_workspace(workspace_dir)
    ledger_dir = workspace.root / PROVENANCE_DIRNAME
    if not ledger_dir.exists():
        return ""

    # Imported here so installations without a ledger never pay for it.
    from ..provenance import (
        ProvenanceLedger,
        RetrievalRequest,
        render_context_block,
        retrieve_context,
    )

    try:
        ledger = ProvenanceLedger(ledger_dir)
        context = retrieve_context(
            ledger.records(),
            RetrievalRequest(
                claim_keys=list(claim_keys),
                now=ledger.now(),
                max_records=max_records,
            ),
        )
        return render_context_block(context, ledger.content_store)
    except Exception as exc:  # noqa: BLE001
        # A ledger that will not open must not take the conversation with it;
        # the turn proceeds with less context, and says so in the log.
        logger.warning(f"Provenance: evidence context unavailable ({exc}).")
        return ""


def build_workspace_prompt_context(
    user_message: str = "",
    workspace_dir: str | Path | None = None,
    max_retrieved_documents: int = 6,
    include_core_files: bool = True,
    claim_keys: Sequence[str] | None = None,
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
    # The memory ablation removes what makes the companion *persistent* —
    # accumulated summaries, learned user facts, and recall of past days —
    # while leaving AGENT.md and RULE.md in place, so the character and its
    # rules are the same in every condition and only memory varies.
    remembers = conditions().memory

    sections: list[str] = []

    if include_core_files:
        sections.append(format_section("AGENT", read_core_file(workspace.agent_file)))
        sections.append(format_section("RULES", read_core_file(workspace.rule_file)))
        if remembers:
            sections.append(
                format_section("MEMORY", read_core_file(workspace.memory_file))
            )
            sections.append(
                format_section("USER INFORMATION", read_core_file(workspace.user_file))
            )

    retrieved_documents = (
        retrieve_workspace_documents(
            query=user_message,
            workspace_dir=workspace.root,
            max_documents=max_retrieved_documents,
        )
        if remembers
        else []
    )

    if user_message:
        # Recall is the memory pillar's observable behavior: did asking about
        # something surface anything, and how well did it match?
        log_event(
            "memory_recall",
            enabled=remembers,
            query_chars=len(user_message),
            documents=len(retrieved_documents),
            top_score=round(retrieved_documents[0].score, 3)
            if retrieved_documents
            else 0.0,
            # Which shelf it came from — memory, knowledge, skills — never
            # which file. Separators differ by platform, so normalize first.
            top_source=retrieved_documents[0]
            .relative_path.replace("\\", "/")
            .split("/")[0]
            if retrieved_documents
            else None,
        )

    evidence = build_evidence_context(
        claim_keys or (),
        workspace_dir=workspace.root,
        max_records=max_retrieved_documents,
    )
    if evidence:
        sections.append(format_section("EVIDENCE-BACKED MEMORY", evidence))

    grouped_documents = split_retrieved_documents(retrieved_documents)

    for section_title, documents in grouped_documents.items():
        if not documents:
            continue

        section_content = "\n\n---\n\n".join(
            format_retrieved_document(document) for document in documents
        )

        sections.append(format_section(section_title, section_content))

    context_body = "\n\n---\n\n".join(
        section for section in sections if section.strip()
    )

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
