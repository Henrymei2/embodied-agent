"""Part 12: cassettes put a real model in the loop without losing determinism."""

from __future__ import annotations

import json

import pytest

from src.open_llm_vtuber.evaluation.cassettes import (
    DEFAULT_MODEL_MATRIX,
    RECORDING_ENV,
    CassetteError,
    CassetteMiss,
    CassetteRecorder,
    CassetteStore,
    CompletionRequest,
    ModelIdentity,
    RecordedCompletion,
    cassette_path,
    describe_matrix,
    load_cassette,
)

HAIKU = ModelIdentity(
    model_id="claude-3-haiku-20240307", family="claude", provider="anthropic"
)
QWEN = ModelIdentity(model_id="qwen2.5:latest", family="qwen", provider="ollama")


def _request(scenario: str = "conv-greeting-no-tools") -> CompletionRequest:
    return CompletionRequest(
        scenario_id=scenario,
        system="plan only",
        messages=({"role": "user", "content": "hello"},),
    )


def _store(identity: ModelIdentity = HAIKU, text: str = "complete(task)"):
    request = _request()
    return CassetteStore(
        identity=identity,
        entries={request.key(identity): RecordedCompletion(completion=text)},
    )


def test_a_recorded_completion_replays_by_request_digest():
    store = _store()
    assert store.replay(_request()).completion == "complete(task)"
    assert store.hits == 1


def test_a_missing_completion_raises_rather_than_returning_nothing():
    """An unrecorded scenario is unmeasured, never an empty or failed answer."""
    store = _store()
    with pytest.raises(CassetteMiss):
        store.replay(_request("mem-record-preference"))
    assert store.misses == 1


def test_one_models_cassette_cannot_answer_for_another():
    """The model identity is inside the key, so the key spaces are disjoint."""
    store = _store(HAIKU)
    qwen_view = CassetteStore(identity=QWEN, entries=dict(store.entries))
    with pytest.raises(CassetteMiss):
        qwen_view.replay(_request())


def test_changing_the_prompt_changes_the_key():
    """A cassette recorded under one prompt is a different experiment."""
    identity = HAIKU
    first = _request()
    second = CompletionRequest(
        scenario_id=first.scenario_id,
        system="a different instruction",
        messages=first.messages,
    )
    assert first.key(identity) != second.key(identity)


def test_temperature_is_part_of_the_identity():
    """A cassette at 0.9 does not describe the same model's behaviour at 0.0."""
    hot = ModelIdentity(
        model_id=HAIKU.model_id,
        family=HAIKU.family,
        provider=HAIKU.provider,
        temperature=0.9,
    )
    assert _request().key(HAIKU) != _request().key(hot)


def test_a_cassette_round_trips_through_a_file(tmp_path):
    store = _store()
    path = store.write(tmp_path / "haiku.json")
    reloaded = load_cassette(path, expect=HAIKU)
    assert reloaded.replay(_request()).completion == "complete(task)"


def test_loading_refuses_a_cassette_recorded_by_a_different_model(tmp_path):
    path = _store(HAIKU).write(tmp_path / "haiku.json")
    with pytest.raises(CassetteError, match="but the run asked for"):
        load_cassette(path, expect=QWEN)


def test_loading_refuses_a_tampered_entry_key(tmp_path):
    path = _store().write(tmp_path / "haiku.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["entries"] = {"not-a-digest": next(iter(payload["entries"].values()))}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CassetteError, match="SHA-256 request digests"):
        load_cassette(path)


def test_loading_refuses_an_unknown_schema_version(tmp_path):
    path = _store().write(tmp_path / "haiku.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema_version"] = 99
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CassetteError, match="unsupported schema version"):
        load_cassette(path)


def test_the_replay_store_has_no_way_to_reach_a_provider():
    """D-007 as a structural property, not a convention.

    `CassetteStore` is what the benchmark runs against, and it holds no live
    callable and exposes no method that could acquire one. A replay cannot fall
    back to the network because there is nothing to fall back to.
    """
    store = _store()
    assert not hasattr(store, "_live")
    assert not any(
        "live" in name or "fetch" in name
        for name in dir(store)
        if not name.startswith("__")
    )


class _Live:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, request: CompletionRequest) -> RecordedCompletion:
        self.calls += 1
        return RecordedCompletion(completion=f"complete(task) # {request.scenario_id}")


def test_recording_refuses_without_the_environment_admission(monkeypatch):
    monkeypatch.delenv(RECORDING_ENV, raising=False)
    with pytest.raises(CassetteError, match=RECORDING_ENV):
        CassetteRecorder(
            HAIKU,
            _Live(),
            allow_network=True,
            permitted_scenarios=["conv-greeting-no-tools"],
        )


def test_recording_refuses_without_the_explicit_flag(monkeypatch):
    monkeypatch.setenv(RECORDING_ENV, "1")
    with pytest.raises(CassetteError, match="allow_network=True"):
        CassetteRecorder(
            HAIKU,
            _Live(),
            allow_network=False,
            permitted_scenarios=["conv-greeting-no-tools"],
        )


def test_a_recorder_with_no_permitted_scenarios_is_refused(monkeypatch):
    """An unrestricted recorder would accept anything, including a transcript."""
    monkeypatch.setenv(RECORDING_ENV, "1")
    with pytest.raises(CassetteError, match="permitted scenarios"):
        CassetteRecorder(HAIKU, _Live(), allow_network=True, permitted_scenarios=[])


async def test_recording_refuses_a_request_outside_the_frozen_suite(monkeypatch):
    """The privacy boundary: nothing but benchmark input reaches a provider."""
    monkeypatch.setenv(RECORDING_ENV, "1")
    live = _Live()
    recorder = CassetteRecorder(
        HAIKU,
        live,
        allow_network=True,
        permitted_scenarios=["conv-greeting-no-tools"],
    )
    with pytest.raises(CassetteError, match="not in the frozen benchmark suite"):
        await recorder.capture(_request("private-conversation-turn"))
    assert live.calls == 0


async def test_recording_captures_once_and_reuses_the_entry(monkeypatch):
    monkeypatch.setenv(RECORDING_ENV, "1")
    live = _Live()
    recorder = CassetteRecorder(
        HAIKU,
        live,
        allow_network=True,
        permitted_scenarios=["conv-greeting-no-tools"],
    )
    first = await recorder.capture(_request())
    second = await recorder.capture(_request())
    assert first == second
    assert live.calls == 1


def test_the_matrix_says_which_models_have_no_evidence(tmp_path):
    """Unmeasured is reported as an inventory with a reason, never as silence."""
    rows = describe_matrix(tmp_path, DEFAULT_MODEL_MATRIX)
    assert len(rows) == 3
    assert {row["status"] for row in rows} == {"unavailable"}
    assert all("no cassette recorded" in row["reason"] for row in rows)


def test_the_matrix_reports_a_recorded_model(tmp_path):
    _store().write(cassette_path(tmp_path, HAIKU))
    rows = describe_matrix(tmp_path, (HAIKU,))
    assert rows[0]["status"] == "recorded"
    assert rows[0]["entries"] == 1


def test_the_declared_matrix_covers_three_distinct_families():
    """Part 12's own gate: one model is a prompt, three are an architecture."""
    families = {identity.family for identity in DEFAULT_MODEL_MATRIX}
    assert len(families) >= 3


def test_a_model_identity_with_an_unsafe_id_is_refused():
    with pytest.raises(CassetteError):
        ModelIdentity(model_id="../etc/passwd", family="x", provider="y").validate()


def test_cassette_paths_cannot_escape_their_directory(tmp_path):
    path = cassette_path(tmp_path, QWEN)
    assert path.parent == tmp_path
    assert ":" not in path.name
