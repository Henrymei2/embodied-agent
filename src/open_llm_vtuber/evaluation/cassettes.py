"""Cassettes: putting a real model in the loop without giving up determinism.

D-023 named the binding constraint on this program: the measurement apparatus
is publication-grade and has measured nothing. Every number in the repository
comes from a fixture driven by a stub, and a reviewer reads "5/8 with
`fixture-llm-v1`" as evidence that the harness runs — which is exactly what it
is.

A cassette closes that gap without breaking anything Part 2 established. A real
completion is fetched **once**, from a real provider, in a separate deliberate
operation, and written to a file keyed by a digest of the request. Every run
afterwards reads the file. The model is genuinely in the loop of the result;
the result is still deterministic, still offline, and still reproducible from a
manifest.

Four properties are structural rather than conventional:

**Replay cannot reach a network.** D-007 forbids a live fallback, and the way
this module obeys it is that :class:`CassetteStore` holds no provider and has no
code path that could construct one. A miss raises :class:`CassetteMiss`; it does
not quietly fetch. Recording lives in a different class that must be handed a
provider explicitly by a caller that said `allow_network=True` out loud.

**A cassette records what produced it.** Model id, provider, parameters, and a
suite digest are stamped into the header, because a completion whose model is
unknown is not evidence about any model. Replaying a cassette against a
different model id fails rather than silently attributing one model's behaviour
to another.

**Recording is bounded to benchmark inputs.** The recorder refuses a request
that did not come from the frozen suite. The alternative — a recorder that will
capture whatever is passed to it — turns one careless call into a file holding
somebody's conversation, and this program's whole privacy argument (D-003,
D-006, D-008) rests on that never happening.

**Cassettes are not traces.** They hold authored benchmark prompts and model
completions, which are publishable, and they are stored under `research/` rather
than in the evidence envelope. Nothing in this module reaches an event payload.
The separation is the same one slice 8.1 drew for scenario text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

CASSETTE_SCHEMA_VERSION = 1

# The environment variable that has to be set for a recording to be attempted at
# all. A flag in code is a decision somebody made once; this is a decision the
# operator has to make in the shell they are standing in.
RECORDING_ENV = "EMBODIED_AGENT_ALLOW_CASSETTE_RECORDING"

_MODEL_ID = re.compile(r"^[a-z0-9][a-z0-9._:/-]{1,79}$")
_FAMILY = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")

# A single completion this large is either a runaway generation or a mistake in
# the caller, and either way it should not be silently frozen into an artifact.
MAX_COMPLETION_CHARS = 200_000
MAX_ENTRIES = 5_000


class CassetteError(ValueError):
    """A cassette file, request, or recording attempt is invalid."""


class CassetteMiss(CassetteError):
    """No recorded completion exists for this request.

    Deliberately its own type. A caller that wants to know "is this suite fully
    recorded?" must be able to distinguish a gap in the cassette from a
    malformed cassette, and a harness must never be able to treat a miss as an
    empty completion — an unrecorded scenario is unmeasured, not failed.
    """

    def __init__(self, request_digest: str, model_id: str) -> None:
        super().__init__(
            f"no recorded completion for request {request_digest[:12]}… "
            f"under model {model_id!r}"
        )
        self.request_digest = request_digest
        self.model_id = model_id


def _canonical(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise CassetteError(f"value is not canonical JSON: {exc}") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelIdentity:
    """Which model produced a cassette, in enough detail to reproduce it.

    `family` exists separately from `model_id` because Part 12 reports *per
    model family* — a mechanism that works on one model is a prompt, one that
    works on three is an architecture — and grouping by a version-stamped model
    id would put `claude-x-20260101` and `claude-x-20260415` in different
    families for no reason that matters to the claim.
    """

    model_id: str
    family: str
    provider: str
    temperature: float = 0.0
    max_tokens: int = 1024

    def validate(self) -> None:
        if not isinstance(self.model_id, str) or not _MODEL_ID.fullmatch(self.model_id):
            raise CassetteError("model_id must be a bounded lowercase identifier")
        if not isinstance(self.family, str) or not _FAMILY.fullmatch(self.family):
            raise CassetteError("family must be a bounded lowercase identifier")
        if not isinstance(self.provider, str) or not _FAMILY.fullmatch(self.provider):
            raise CassetteError("provider must be a bounded lowercase identifier")
        if (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not 0.0 <= float(self.temperature) <= 2.0
        ):
            raise CassetteError("temperature must be within [0, 2]")
        if (
            isinstance(self.max_tokens, bool)
            or not isinstance(self.max_tokens, int)
            or not 1 <= self.max_tokens <= 32_768
        ):
            raise CassetteError("max_tokens must be within [1, 32768]")

    def as_record(self) -> dict[str, Any]:
        self.validate()
        return {
            "model_id": self.model_id,
            "family": self.family,
            "provider": self.provider,
            "temperature": float(self.temperature),
            "max_tokens": int(self.max_tokens),
        }

    @staticmethod
    def from_record(record: Any) -> "ModelIdentity":
        if not isinstance(record, dict):
            raise CassetteError("model identity must be an object")
        expected = {"model_id", "family", "provider", "temperature", "max_tokens"}
        if set(record) != expected:
            raise CassetteError("model identity fields differ from the contract")
        identity = ModelIdentity(
            model_id=record["model_id"],
            family=record["family"],
            provider=record["provider"],
            temperature=record["temperature"],
            max_tokens=record["max_tokens"],
        )
        identity.validate()
        return identity


@dataclass(frozen=True)
class CompletionRequest:
    """One request to a model, in the form a cassette is keyed by.

    Temperature and max tokens are part of the identity because they change the
    distribution being sampled: a cassette recorded at 0.9 does not describe the
    behaviour of the same model at 0.0, and keying without them would let one
    silently answer for the other.
    """

    scenario_id: str
    system: str
    messages: tuple[Mapping[str, Any], ...]
    tools: tuple[str, ...] = ()
    turn: int = 0

    def validate(self) -> None:
        if not isinstance(self.scenario_id, str) or not self.scenario_id.strip():
            raise CassetteError("request.scenario_id is required")
        if not isinstance(self.system, str):
            raise CassetteError("request.system must be a string")
        if not isinstance(self.messages, tuple) or not self.messages:
            raise CassetteError("request.messages must be a non-empty tuple")
        for message in self.messages:
            if not isinstance(message, Mapping) or "role" not in message:
                raise CassetteError("each message needs at least a role")
        if not isinstance(self.tools, tuple):
            raise CassetteError("request.tools must be a tuple of tool names")
        if isinstance(self.turn, bool) or not isinstance(self.turn, int):
            raise CassetteError("request.turn must be an integer")
        if self.turn < 0:
            raise CassetteError("request.turn cannot be negative")

    def key(self, identity: ModelIdentity) -> str:
        """The digest a cassette entry is filed under.

        The model identity is inside the key rather than only in the header, so
        the same suite recorded against two models produces two disjoint key
        spaces and a cassette can never answer for a model it did not record.
        """
        self.validate()
        identity.validate()
        return _digest(
            {
                "schema_version": CASSETTE_SCHEMA_VERSION,
                "model": identity.as_record(),
                "scenario_id": self.scenario_id,
                "turn": self.turn,
                "system": self.system,
                "messages": [dict(message) for message in self.messages],
                "tools": sorted(self.tools),
            }
        )


@dataclass(frozen=True)
class RecordedCompletion:
    """What the model said, and what it cost to say it."""

    completion: str
    finish_reason: str = "stop"
    tool_calls: tuple[Mapping[str, Any], ...] = ()
    recorded_at: str = ""
    latency_ms: int = 0

    def validate(self) -> None:
        if not isinstance(self.completion, str):
            raise CassetteError("completion must be a string")
        if len(self.completion) > MAX_COMPLETION_CHARS:
            raise CassetteError("completion exceeds the recordable size")
        if not isinstance(self.finish_reason, str) or not self.finish_reason:
            raise CassetteError("finish_reason is required")
        if not isinstance(self.tool_calls, tuple):
            raise CassetteError("tool_calls must be a tuple")
        for call in self.tool_calls:
            if not isinstance(call, Mapping) or "name" not in call:
                raise CassetteError("each tool call needs a name")
        if (
            isinstance(self.latency_ms, bool)
            or not isinstance(self.latency_ms, int)
            or self.latency_ms < 0
        ):
            raise CassetteError("latency_ms must be a non-negative integer")

    def as_record(self) -> dict[str, Any]:
        self.validate()
        return {
            "completion": self.completion,
            "finish_reason": self.finish_reason,
            "tool_calls": [dict(call) for call in self.tool_calls],
            "recorded_at": self.recorded_at,
            "latency_ms": int(self.latency_ms),
        }

    @staticmethod
    def from_record(record: Any) -> "RecordedCompletion":
        if not isinstance(record, dict):
            raise CassetteError("completion entry must be an object")
        expected = {
            "completion",
            "finish_reason",
            "tool_calls",
            "recorded_at",
            "latency_ms",
        }
        missing = expected - set(record)
        extra = set(record) - expected
        if missing or extra:
            raise CassetteError(
                f"completion entry fields differ; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )
        calls = record["tool_calls"]
        if not isinstance(calls, list):
            raise CassetteError("tool_calls must be an array")
        completion = RecordedCompletion(
            completion=record["completion"],
            finish_reason=record["finish_reason"],
            tool_calls=tuple(dict(call) for call in calls),
            recorded_at=record["recorded_at"],
            latency_ms=record["latency_ms"],
        )
        completion.validate()
        return completion


@dataclass
class CassetteStore:
    """Replay-only. Holds no provider and cannot acquire one.

    This is the class the benchmark runs against, and the reason it is separate
    from the recorder is D-007: a replay that can fall back to a live call is
    neither deterministic nor safe, and the robust way to guarantee that is to
    make the fallback unwriteable rather than merely unwritten.
    """

    identity: ModelIdentity
    entries: dict[str, RecordedCompletion] = field(default_factory=dict)
    suite_digest: str = ""
    recorded_at: str = ""
    hits: int = 0
    misses: int = 0

    def __post_init__(self) -> None:
        self.identity.validate()
        if len(self.entries) > MAX_ENTRIES:
            raise CassetteError("cassette holds more entries than the contract allows")

    def replay(self, request: CompletionRequest) -> RecordedCompletion:
        key = request.key(self.identity)
        entry = self.entries.get(key)
        if entry is None:
            self.misses += 1
            raise CassetteMiss(key, self.identity.model_id)
        self.hits += 1
        return entry

    def contains(self, request: CompletionRequest) -> bool:
        return request.key(self.identity) in self.entries

    def coverage(self, requests: Iterable[CompletionRequest]) -> tuple[int, int]:
        """(recorded, total) over a set of requests, without consuming them."""
        total = 0
        recorded = 0
        for request in requests:
            total += 1
            recorded += int(self.contains(request))
        return recorded, total

    def as_record(self) -> dict[str, Any]:
        return {
            "schema_version": CASSETTE_SCHEMA_VERSION,
            "model": self.identity.as_record(),
            "suite_digest": self.suite_digest,
            "recorded_at": self.recorded_at,
            "entries": {
                key: entry.as_record() for key, entry in sorted(self.entries.items())
            },
        }

    def write(self, path: str | Path) -> Path:
        location = Path(path)
        location.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.as_record(), ensure_ascii=False, indent=2)
        temporary = location.with_suffix(location.suffix + ".partial")
        temporary.write_text(payload + "\n", encoding="utf-8")
        os.replace(temporary, location)
        return location


def load_cassette(path: str | Path, *, expect: ModelIdentity | None = None):
    """Load a cassette, failing closed on drift or on the wrong model.

    `expect` is how a run refuses to attribute one model's behaviour to another:
    a benchmark asked to measure model A must not silently measure model B
    because somebody pointed it at the wrong file.
    """
    location = Path(path)
    try:
        payload = json.loads(location.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CassetteError(f"cassette could not be read: {exc}") from exc
    except ValueError as exc:
        raise CassetteError(f"cassette is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CassetteError("cassette file must contain an object")
    if payload.get("schema_version") != CASSETTE_SCHEMA_VERSION:
        raise CassetteError("cassette declares an unsupported schema version")
    identity = ModelIdentity.from_record(payload.get("model"))
    if expect is not None:
        expect.validate()
        if identity.as_record() != expect.as_record():
            raise CassetteError(
                f"cassette records model {identity.model_id!r} but the run asked "
                f"for {expect.model_id!r}"
            )
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, dict):
        raise CassetteError("cassette has no entry map")
    if len(raw_entries) > MAX_ENTRIES:
        raise CassetteError("cassette holds more entries than the contract allows")
    entries: dict[str, RecordedCompletion] = {}
    for key, record in raw_entries.items():
        if not isinstance(key, str) or not _DIGEST.fullmatch(key):
            raise CassetteError("cassette entry keys must be SHA-256 request digests")
        entries[key] = RecordedCompletion.from_record(record)
    suite_digest = payload.get("suite_digest", "")
    if not isinstance(suite_digest, str):
        raise CassetteError("suite_digest must be a string")
    recorded_at = payload.get("recorded_at", "")
    if not isinstance(recorded_at, str):
        raise CassetteError("recorded_at must be a string")
    return CassetteStore(
        identity=identity,
        entries=entries,
        suite_digest=suite_digest,
        recorded_at=recorded_at,
    )


LiveCompletion = Callable[[CompletionRequest], Awaitable[RecordedCompletion]]


class CassetteRecorder:
    """The one place a live provider is allowed, and it says so.

    Three separate admissions are required before a byte leaves the machine:
    the caller constructs a recorder rather than a store, passes
    `allow_network=True`, and has set :data:`RECORDING_ENV` in the environment.
    That is more friction than a flag, on purpose — the failure this prevents is
    a replay run that quietly became a live run, and the cost of that failure is
    a result nobody can reproduce plus a bill nobody expected.
    """

    def __init__(
        self,
        identity: ModelIdentity,
        live: LiveCompletion,
        *,
        allow_network: bool,
        permitted_scenarios: Iterable[str],
        suite_digest: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        identity.validate()
        if not allow_network:
            raise CassetteError(
                "recording contacts a live provider; pass allow_network=True to "
                "say so explicitly"
            )
        if not os.environ.get(RECORDING_ENV):
            raise CassetteError(
                f"recording requires {RECORDING_ENV} to be set in the environment; "
                "this keeps a replay run from ever becoming a live one by accident"
            )
        permitted = frozenset(permitted_scenarios)
        if not permitted:
            raise CassetteError(
                "a recorder with no permitted scenarios would accept any request, "
                "including one carrying private conversation"
            )
        self.identity = identity
        self._live = live
        self._permitted = permitted
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.store = CassetteStore(
            identity=identity,
            suite_digest=suite_digest,
            recorded_at=self._clock().isoformat(),
        )
        self.calls = 0

    async def capture(self, request: CompletionRequest) -> RecordedCompletion:
        """Fetch one completion live and file it, refusing non-benchmark input."""
        request.validate()
        if request.scenario_id not in self._permitted:
            # The recorder is pointed at a provider. A request that did not come
            # from the frozen suite has no business being sent, and no business
            # being written into a file this program intends to publish.
            raise CassetteError(
                f"refusing to record {request.scenario_id!r}: it is not in the "
                "frozen benchmark suite"
            )
        key = request.key(self.identity)
        existing = self.store.entries.get(key)
        if existing is not None:
            return existing
        if len(self.store.entries) >= MAX_ENTRIES:
            raise CassetteError("cassette entry budget exhausted")
        self.calls += 1
        completion = await self._live(request)
        completion.validate()
        stamped = RecordedCompletion(
            completion=completion.completion,
            finish_reason=completion.finish_reason,
            tool_calls=completion.tool_calls,
            recorded_at=self._clock().isoformat(),
            latency_ms=completion.latency_ms,
        )
        self.store.entries[key] = stamped
        return stamped


def cassette_path(root: str | Path, identity: ModelIdentity) -> Path:
    """Where a model's cassette lives. One file per model id, not per family."""
    identity.validate()
    safe = identity.model_id.replace("/", "--").replace(":", "-")
    return Path(root) / f"{safe}.json"


def available_cassettes(root: str | Path) -> tuple[Path, ...]:
    location = Path(root)
    if not location.is_dir():
        return ()
    return tuple(sorted(location.glob("*.json")))


def describe_matrix(
    root: str | Path, matrix: Sequence[ModelIdentity]
) -> tuple[dict[str, Any], ...]:
    """Say, per declared model, whether evidence exists and why not.

    This is what turns "unmeasured" from an excuse into an inventory: each row
    names the model, whether its cassette is present, how many completions it
    holds, and the exact command that would produce it.
    """
    rows: list[dict[str, Any]] = []
    for identity in matrix:
        path = cassette_path(root, identity)
        row: dict[str, Any] = {
            "model_id": identity.model_id,
            "family": identity.family,
            "provider": identity.provider,
            "cassette": str(path),
        }
        if not path.is_file():
            row["status"] = "unavailable"
            row["reason"] = "no cassette recorded for this model"
            rows.append(row)
            continue
        try:
            store = load_cassette(path, expect=identity)
        except CassetteError as exc:
            row["status"] = "invalid"
            row["reason"] = str(exc)
            rows.append(row)
            continue
        row["status"] = "recorded"
        row["entries"] = len(store.entries)
        row["suite_digest"] = store.suite_digest
        row["recorded_at"] = store.recorded_at
        rows.append(row)
    return tuple(rows)


# The declared model matrix for Part 12. Three families, because a mechanism
# demonstrated on one model is a prompt and a mechanism demonstrated on three is
# an architecture. Nothing here is measured until a cassette exists beside it.
DEFAULT_MODEL_MATRIX: tuple[ModelIdentity, ...] = (
    ModelIdentity(
        model_id="claude-3-haiku-20240307",
        family="claude",
        provider="anthropic",
    ),
    ModelIdentity(model_id="gpt-4o-mini", family="gpt", provider="openai"),
    ModelIdentity(model_id="qwen2.5:latest", family="qwen", provider="ollama"),
)


__all__ = [
    "CASSETTE_SCHEMA_VERSION",
    "DEFAULT_MODEL_MATRIX",
    "RECORDING_ENV",
    "CassetteError",
    "CassetteMiss",
    "CassetteRecorder",
    "CassetteStore",
    "CompletionRequest",
    "ModelIdentity",
    "RecordedCompletion",
    "available_cassettes",
    "cassette_path",
    "describe_matrix",
    "load_cassette",
]
