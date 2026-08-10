from __future__ import annotations

import pytest

from src.open_llm_vtuber.workspace import embeddings
from src.open_llm_vtuber.workspace.embedding_cache import EmbeddingCache, chunk_key
from src.open_llm_vtuber.workspace.workspace_retriever import (
    retrieve_workspace_documents,
    split_into_chunks,
)
from src.open_llm_vtuber.workspace.workspace_prompt_builder import (
    build_workspace_prompt_context,
)


def test_rule_template_scopes_planning_to_consequential_work():
    from src.open_llm_vtuber.workspace.workspace_manager import ROOT_FILES

    rule = ROOT_FILES["RULE.md"]
    assert "Do not begin work that needs approval until `list_plans`" in rule
    assert "Routine reversible" in rule
    assert "run directly without a plan or approval" in rule


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBODIED_AGENT_EMBEDDINGS", "off")
    embeddings.reset_provider()
    (tmp_path / "memory").mkdir(parents=True)
    (tmp_path / "knowledge").mkdir(parents=True)
    (tmp_path / "memory" / "2026-07-20.md").write_text(
        "Mei mentioned her younger sibling is visiting from Seoul in August.\n\n"
        "We finally fixed the dishwasher after three attempts.",
        encoding="utf-8",
    )
    (tmp_path / "knowledge" / "coffee.md").write_text(
        "The user takes their coffee black, no sugar.", encoding="utf-8"
    )
    yield tmp_path
    embeddings.reset_provider()


class _StubProvider(embeddings.EmbeddingProvider):
    """Deterministic 'embeddings': related sentences share marker words."""

    name = "stub"

    MARKERS = ("sibling", "sister", "family", "coffee", "drink", "dishwasher", "fix")

    def embed(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            vector = [1.0 if marker in lowered else 0.0 for marker in self.MARKERS]
            # "sister" and "sibling" mean the same thing to this stub, which is
            # exactly the relationship a real embedding model would capture.
            if "sister" in lowered or "sibling" in lowered:
                vector[0] = vector[1] = 1.0
            if "coffee" in lowered or "drink" in lowered:
                vector[3] = vector[4] = 1.0
            vectors.append(vector or [0.0])
        return vectors


@pytest.fixture
def semantic(monkeypatch):
    embeddings.reset_provider()
    monkeypatch.setattr(embeddings, "_load_first_available", lambda: _StubProvider())
    yield
    embeddings.reset_provider()


def test_keyword_recall_misses_a_paraphrase(workspace):
    """The behavior this change exists to fix, pinned so it cannot come back."""
    results = retrieve_workspace_documents(
        "did I ever mention my sister?", workspace_dir=workspace
    )

    assert results == []


def test_core_prompt_can_disable_all_document_retrieval(workspace):
    block = build_workspace_prompt_context(
        "coffee",
        workspace_dir=workspace,
        include_retrieved_documents=False,
    )

    assert "## AGENT" in block
    assert "coffee black" not in block
    assert "RELEVANT KNOWLEDGE" not in block
    assert "RELEVANT MEMORIES" not in block


def test_semantic_recall_finds_the_paraphrased_memory(workspace, semantic):
    results = retrieve_workspace_documents(
        "did I ever mention my sister?", workspace_dir=workspace
    )

    assert results, "the sibling memory should be recalled from a paraphrase"
    assert results[0].relative_path.endswith("2026-07-20.md")
    assert "sibling" in results[0].content
    assert "semantic match" in results[0].reason


def test_exact_wording_still_wins_when_it_exists(workspace, semantic):
    results = retrieve_workspace_documents(
        "how does the user take their coffee?", workspace_dir=workspace
    )

    assert results[0].relative_path.endswith("coffee.md")


def test_retrieval_returns_the_matching_passage_not_the_whole_file(workspace, semantic):
    long_diary = "\n\n".join(
        [
            "Monday: we talked about the garden for a while.",
            "Tuesday: the dishwasher broke and I could not fix it.",
            "Wednesday: quiet day, read two chapters.",
        ]
        + [f"Day {index}: nothing much happened." for index in range(40)]
    )
    (workspace / "memory" / "2026-06-01.md").write_text(long_diary, encoding="utf-8")

    results = retrieve_workspace_documents(
        "what broke and needed fixing?", workspace_dir=workspace, max_documents=3
    )

    best = results[0]
    assert "dishwasher" in best.content
    # The passage, not the month around it.
    assert len(best.content) < len(long_diary)


def test_unrelated_questions_recall_nothing(workspace, semantic):
    """A floor on similarity: a companion that "remembers" noise is worse."""
    assert (
        retrieve_workspace_documents(
            "what is the capital of Peru?", workspace_dir=workspace
        )
        == []
    )


def test_each_diary_entry_is_its_own_memory(workspace, semantic):
    """Entries must not be packed together, or each one loses its own match."""
    (workspace / "memory" / "2026-07-21.md").write_text(
        "# Daily Memory - 2026-07-21\n\n"
        "We finally fixed the dishwasher after three attempts.\n\n"
        "She was frustrated with the deadline on the grant application.",
        encoding="utf-8",
    )

    chunks = split_into_chunks(
        (workspace / "memory" / "2026-07-21.md").read_text(encoding="utf-8")
    )

    # The bare heading rides along with the first entry; the two entries stay apart.
    assert len(chunks) == 2
    assert chunks[0].startswith("# Daily Memory")
    assert "dishwasher" in chunks[0]
    assert "grant application" in chunks[1]
    assert "dishwasher" not in chunks[1]


def test_the_whole_file_stays_searchable_not_just_its_first_entries(workspace):
    """A chunk-count cap once made the tail of a long diary silently invisible."""
    (workspace / "memory" / "2026-05-01.md").write_text(
        "\n\n".join(
            f"Entry {index}: something memorable involving topic{index}."
            for index in range(120)
        ),
        encoding="utf-8",
    )

    for probe in ("topic0", "topic60", "topic119"):
        assert retrieve_workspace_documents(probe, workspace_dir=workspace), probe


def test_the_chunk_ceiling_cannot_discard_text_that_was_read():
    from src.open_llm_vtuber.workspace import workspace_retriever as module

    # The shortest possible chunk times the ceiling still covers everything the
    # reader accepts, so the ceiling only ever bounds pathological input.
    assert (
        module.MAX_CHUNKS_PER_FILE * module.MIN_CHUNK_CHARS >= module.MAX_INDEXED_CHARS
    )


def test_chunking_keeps_paragraphs_whole():
    """Paragraphs pack together up to the limit, and are never cut in half."""
    first = "First paragraph. " * 12  # ~200 chars
    second = "Second paragraph. " * 12

    chunks = split_into_chunks(f"{first}\n\n{second}", max_chars=250)

    assert len(chunks) == 2
    assert chunks[0].startswith("First paragraph.")
    assert chunks[1].startswith("Second paragraph.")
    assert "Second" not in chunks[0]

    # Substantial paragraphs stay apart even when the budget could hold both:
    # sharing a chunk is what blurs each one's match.
    assert len(split_into_chunks(f"{first}\n\n{second}", max_chars=900)) == 2

    # A fragment too short to mean anything rides along with what follows.
    assert split_into_chunks(f"# Tuesday\n\n{first}", max_chars=900)[0].startswith(
        "# Tuesday"
    )


def test_an_oversized_paragraph_is_split_rather_than_dropped():
    chunks = split_into_chunks(
        "\n".join(f"line {i}" for i in range(200)), max_chars=100
    )

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert "line 199" in chunks[-1]


def test_recall_still_works_when_no_embedding_provider_loads(workspace, monkeypatch):
    embeddings.reset_provider()
    monkeypatch.setattr(embeddings, "_load_first_available", lambda: None)

    results = retrieve_workspace_documents("dishwasher", workspace_dir=workspace)

    assert results[0].relative_path.endswith("2026-07-20.md")


def test_a_provider_failing_mid_run_degrades_to_keywords(workspace, monkeypatch):
    class Failing(embeddings.EmbeddingProvider):
        name = "failing"

        def embed(self, texts):
            raise RuntimeError("model went away")

    embeddings.reset_provider()
    monkeypatch.setattr(embeddings, "_load_first_available", lambda: Failing())

    results = retrieve_workspace_documents("dishwasher", workspace_dir=workspace)

    assert results[0].relative_path.endswith("2026-07-20.md")


def test_vectors_are_cached_so_a_second_turn_does_not_re_embed(workspace, monkeypatch):
    calls: list[int] = []

    class Counting(_StubProvider):
        def embed(self, texts):
            calls.append(len(texts))
            return super().embed(texts)

    embeddings.reset_provider()
    monkeypatch.setattr(embeddings, "_load_first_available", lambda: Counting())

    retrieve_workspace_documents("sister", workspace_dir=workspace)
    first_run = sum(calls)
    calls.clear()
    retrieve_workspace_documents("sister", workspace_dir=workspace)

    # Only the query itself is embedded the second time round.
    assert sum(calls) < first_run
    assert sum(calls) == 1


def test_the_cache_survives_a_corrupt_file(tmp_path):
    (tmp_path / ".embedding-cache.json").write_text("{ not json", encoding="utf-8")

    cache = EmbeddingCache(tmp_path)
    cache.put(chunk_key("stub", "hello"), [0.1, 0.2])
    cache.save()

    assert EmbeddingCache(tmp_path).get(chunk_key("stub", "hello")) == [0.1, 0.2]


def test_switching_providers_does_not_mix_vectors():
    assert chunk_key("st:model-a", "text") != chunk_key("ollama:model-b", "text")


def test_embeddings_can_be_turned_off_entirely(monkeypatch):
    embeddings.reset_provider()
    monkeypatch.setenv("EMBODIED_AGENT_EMBEDDINGS", "off")

    assert embeddings.get_provider() is None
    assert embeddings.embed(["anything"]) is None
    embeddings.reset_provider()


def test_a_loading_provider_never_blocks_a_turn(monkeypatch):
    """A reply must not wait tens of seconds for a model to finish loading."""
    embeddings.reset_provider()
    embeddings._loading.set()
    try:
        assert embeddings.get_provider() is None
    finally:
        embeddings.reset_provider()


def test_cosine_similarity_is_bounded():
    assert embeddings.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert embeddings.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert embeddings.cosine_similarity([], [1.0]) == 0.0
    assert embeddings.cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
