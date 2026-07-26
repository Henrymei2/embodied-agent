# Memory recall

## The problem

Recall used to be keyword overlap, which only finds a memory when the user
happens to reuse its words. "Did I ever tell you about my sister?" shares no
tokens with "Mei mentioned her younger sibling is visiting", so the companion
answered *you never told me* about something it had written down itself. For a
project arguing that persistent memory creates presence, that is the failure
that undoes the argument.

## How it works now

Every retrieval blends two signals:

- **Keyword overlap** — precise about names, dates and IDs that an embedding
  blurs. Unchanged from before.
- **Semantic similarity** — a local embedding model, so "sister" finds
  "sibling".

They are mixed 0.4 / 0.6 after the keyword count is squashed onto [0, 1], so a
page that merely repeats a common word cannot outrank the passage that answers
the question. When no embedding provider is available the keyword score is used
as it always was, which is exactly the old behavior.

## Providers

Tried in order, both local, neither required:

| Provider | Cost | Notes |
| --- | --- | --- |
| `sentence_transformers` | ~90 MB model, downloaded once | default; offline afterwards |
| `ollama` | none | uses the Ollama server this project already runs; needs `ollama pull nomic-embed-text` |

Set `EMBODIED_AGENT_EMBEDDINGS` to reorder them (`ollama,sentence_transformers`),
name one, or `off` for keyword-only recall. `EMBODIED_AGENT_EMBEDDING_MODEL`
overrides the model name.

The model loads in a background thread at startup. Until it is ready recall
falls back to keywords, because a reply that waits tens of seconds for a model
is worse than a reply that recalls slightly less well.

## Chunking

Files are indexed as passages, not as wholes: what belongs in the prompt is the
entry that answers the question, not the month around it.

One paragraph is one chunk. Packing unrelated entries together is what blurs a
match — measured against `all-MiniLM-L6-v2`:

| Query | Against the entry alone | Against a chunk of three entries |
| --- | --- | --- |
| "did I ever mention my sister?" | 0.343 | 0.235 |
| "what did we repair together?" | 0.402 | 0.209 |
| "what was stressing them out?" | 0.224 | 0.089 |

Only fragments shorter than 40 characters — a bare date heading, a stray line —
merge forward, arriving as context on the entry they introduce. Skills are the
exception: a skill is one instruction, so it is indexed whole rather than split
between its title and its steps.

## Thresholds

From the same measurements, unrelated pairs stayed under 0.15 while genuinely
related ones ran 0.22–0.40. A passage recalled on meaning alone must therefore
clear **0.2**, and must hold at least 40 characters, so a heading cannot drift
over the line without carrying a memory. A companion that confidently recalls
noise is worse than one that says nothing.

## Cost

Chunk vectors are cached in `<workspace>/.embedding-cache.json`, keyed by a hash
of the chunk text and the provider name. An edited file re-embeds only its
changed chunks, switching providers cannot mix incompatible vectors, and a
corrupt cache costs one re-embed rather than a failed turn. In practice only the
query itself is embedded on a normal turn: about 30 ms.

## What is deliberately not here

No vector database, no reranker, no chunk-overlap windowing. The workspace is a
few hundred small markdown files; a linear scan with a cache is well within
budget and keeps the whole thing inspectable as plain files on disk.
