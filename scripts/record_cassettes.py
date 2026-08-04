"""Record real model completions for EmbodiedAgentBench, once, deliberately.

This is the only script in the repository that contacts a language model
provider. It requires three separate admissions before a byte leaves the
machine — the environment variable, the flag, and a named model — because the
failure it prevents is a replay run that quietly became a live run.

    $env:EMBODIED_AGENT_ALLOW_CASSETTE_RECORDING = "1"
    $env:ANTHROPIC_API_KEY = "..."            # or OPENAI_API_KEY, or run Ollama
    uv run python scripts/record_cassettes.py --model claude-3-haiku-20240307 --yes

Recorded cassettes land in `research/cassettes/`. Every later benchmark run
replays them offline, so the model is genuinely in the loop of the result and
the result is still deterministic and reproducible without credentials.

Only frozen suite scenarios are ever sent. The recorder refuses anything else,
so no conversation, memory, or screen content can reach a provider through this
path even if somebody points it at the wrong input.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.open_llm_vtuber.evaluation.cassettes import (  # noqa: E402
    DEFAULT_MODEL_MATRIX,
    RECORDING_ENV,
    CassetteError,
    CassetteRecorder,
    CompletionRequest,
    ModelIdentity,
    RecordedCompletion,
    cassette_path,
)
from src.open_llm_vtuber.evaluation.harness import benchmark_request  # noqa: E402
from src.open_llm_vtuber.evaluation.scenarios import load_suite  # noqa: E402

SUITE = "research/scenarios/embodied-agent-bench.json"
CASSETTE_ROOT = "research/cassettes"


def _identity(model_id: str) -> ModelIdentity:
    for candidate in DEFAULT_MODEL_MATRIX:
        if candidate.model_id == model_id:
            return candidate
    raise SystemExit(
        f"{model_id!r} is not in the declared model matrix. Add it to "
        "DEFAULT_MODEL_MATRIX first, so the artifact names a model the "
        "contract knows about."
    )


def _build_live(identity: ModelIdentity):
    """Construct the provider client, or explain exactly what is missing."""
    if identity.provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY is not set")
        from src.open_llm_vtuber.agent.stateless_llm.claude_llm import AsyncLLM

        return AsyncLLM(model=identity.model_id, llm_api_key=key)
    if identity.provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise SystemExit("OPENAI_API_KEY is not set")
        from src.open_llm_vtuber.agent.stateless_llm.openai_compatible_llm import (
            AsyncLLM,
        )

        return AsyncLLM(
            model=identity.model_id,
            base_url="https://api.openai.com/v1",
            llm_api_key=key,
            temperature=identity.temperature,
        )
    if identity.provider == "ollama":
        from src.open_llm_vtuber.agent.stateless_llm.ollama_llm import OllamaLLM

        base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
        return OllamaLLM(
            model=identity.model_id,
            base_url=base,
            temperature=identity.temperature,
        )
    raise SystemExit(f"no client is wired for provider {identity.provider!r}")


def _live_callable(client):
    async def call(request: CompletionRequest) -> RecordedCompletion:
        started = time.perf_counter()
        chunks: list[str] = []
        async for chunk in client.chat_completion(
            messages=[dict(message) for message in request.messages],
            system=request.system or None,
        ):
            if isinstance(chunk, str):
                chunks.append(chunk)
        elapsed = int((time.perf_counter() - started) * 1000)
        return RecordedCompletion(
            completion="".join(chunks), finish_reason="stop", latency_ms=elapsed
        )

    return call


async def record(identity: ModelIdentity, *, split: str, limit: int) -> int:
    suite = load_suite(SUITE)
    scenarios = suite.select(split=split, allow_held_out=split == "held_out")
    if limit:
        scenarios = scenarios[:limit]
    client = _build_live(identity)
    recorder = CassetteRecorder(
        identity,
        _live_callable(client),
        allow_network=True,
        permitted_scenarios=[s.scenario_id for s in suite.scenarios],
        suite_digest=suite.digest(),
    )
    failures = 0
    for index, scenario in enumerate(scenarios, start=1):
        try:
            await recorder.capture(benchmark_request(scenario))
            print(f"  [{index:>3}/{len(scenarios)}] {scenario.scenario_id}")
        except Exception as exc:  # noqa: BLE001 - one bad call must not lose the rest
            failures += 1
            print(
                f"  [{index:>3}/{len(scenarios)}] {scenario.scenario_id} FAILED: {exc}"
            )
    path = recorder.store.write(cassette_path(CASSETTE_ROOT, identity))
    print(f"wrote {path} with {len(recorder.store.entries)} completions")
    if failures:
        print(
            f"{failures} scenarios did not record. The cassette is incomplete, so "
            "the benchmark will report this model as unavailable rather than "
            "scoring the recorded part."
        )
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--split",
        default="development",
        choices=["development", "validation", "held_out"],
    )
    parser.add_argument("--limit", type=int, default=0, help="record only the first N")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="confirm that this will contact a paid external provider",
    )
    args = parser.parse_args()

    if not os.environ.get(RECORDING_ENV):
        print(
            f"{RECORDING_ENV} is not set. Recording contacts a live provider and "
            "costs money; set it in the shell you are standing in to say so."
        )
        return 2
    if not args.yes:
        print("pass --yes to confirm a live, billable recording run")
        return 2

    identity = _identity(args.model)
    print(f"recording {identity.model_id} ({identity.provider}) on {args.split}")
    try:
        return asyncio.run(record(identity, split=args.split, limit=args.limit))
    except CassetteError as exc:
        print(f"recording refused: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
