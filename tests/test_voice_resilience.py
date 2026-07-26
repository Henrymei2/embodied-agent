from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.open_llm_vtuber.asr.utils import is_model_dir_complete
from src.open_llm_vtuber.tts.fallback_tts import FallbackTTSEngine, build_tts_engine
from src.open_llm_vtuber.tts.tts_interface import TTSInterface


class _Engine(TTSInterface):
    """Stand-in TTS engine with scriptable behavior."""

    def __init__(self, tmp_path: Path, *, fails=False, returns_none=False, empty=False):
        self.tmp_path = tmp_path
        self.fails = fails
        self.returns_none = returns_none
        self.empty = empty
        self.calls = 0

    def generate_audio(self, text: str, file_name_no_ext=None):
        self.calls += 1
        if self.fails:
            raise ConnectionError("the voice service is unreachable")
        if self.returns_none:
            return None
        path = self.tmp_path / f"{file_name_no_ext or 'out'}-{id(self)}.wav"
        path.write_bytes(b"" if self.empty else b"RIFF" + b"\0" * 400)
        return str(path)


def test_primary_engine_is_used_when_it_works(tmp_path):
    primary = _Engine(tmp_path)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(primary, lambda: fallback)

    path = engine.generate_audio("hello")

    assert Path(path).exists()
    assert (primary.calls, fallback.calls) == (1, 0)
    assert engine.using_fallback is False


def test_a_network_failure_falls_back_to_the_local_engine(tmp_path):
    primary = _Engine(tmp_path, fails=True)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(primary, lambda: fallback)

    path = engine.generate_audio("hello")

    assert Path(path).exists()
    assert fallback.calls == 1
    assert engine.using_fallback is True


def test_an_engine_that_returns_nothing_also_falls_back(tmp_path):
    """edge_tts reports failure by returning None rather than raising."""
    primary = _Engine(tmp_path, returns_none=True)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(primary, lambda: fallback)

    assert engine.generate_audio("hello") is not None
    assert fallback.calls == 1


def test_an_empty_audio_file_counts_as_failure(tmp_path):
    primary = _Engine(tmp_path, empty=True)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(primary, lambda: fallback)

    path = engine.generate_audio("hello")

    assert Path(path).stat().st_size > 0
    assert fallback.calls == 1


async def test_the_async_path_falls_back_too(tmp_path):
    primary = _Engine(tmp_path, fails=True)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(primary, lambda: fallback)

    path = await engine.async_generate_audio("hello")

    assert Path(path).exists()
    assert fallback.calls == 1


def test_audio_that_cannot_be_decoded_counts_as_a_failure(tmp_path):
    """A file the browser cannot play is silence, however well it synthesized.

    This is the real shape of the failure on a machine without FFmpeg: edge_tts
    happily writes an MP3 that nothing downstream can decode.
    """
    primary = _Engine(tmp_path)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(primary, lambda: fallback, playable=lambda _path: False)

    path = engine.generate_audio("hello")

    assert Path(path).exists()
    assert fallback.calls == 1
    assert engine.using_fallback is True


def test_the_primary_is_probed_once_and_then_left_alone(tmp_path):
    probes = []
    primary = _Engine(tmp_path)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(
        primary,
        lambda: fallback,
        playable=lambda path: probes.append(path) or False,
    )

    engine.generate_audio("one")
    engine.generate_audio("two")
    engine.generate_audio("three")

    # The container cannot change between sentences, so re-decoding each one
    # would only add latency.
    assert len(probes) == 1
    assert primary.calls == 1
    assert fallback.calls == 3


def test_a_playable_primary_is_probed_once_and_keeps_speaking(tmp_path):
    probes = []
    primary = _Engine(tmp_path)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(
        primary,
        lambda: fallback,
        playable=lambda path: probes.append(path) or True,
    )

    engine.generate_audio("one")
    engine.generate_audio("two")

    assert len(probes) == 1
    assert (primary.calls, fallback.calls) == (2, 0)


async def test_the_async_path_also_rejects_undecodable_audio(tmp_path):
    primary = _Engine(tmp_path)
    fallback = _Engine(tmp_path)
    engine = FallbackTTSEngine(primary, lambda: fallback, playable=lambda _p: False)

    path = await engine.async_generate_audio("hello")

    assert Path(path).exists()
    assert fallback.calls == 1


def test_both_engines_failing_degrades_to_silence_rather_than_raising(tmp_path):
    engine = FallbackTTSEngine(
        _Engine(tmp_path, fails=True),
        lambda: (_ for _ in ()).throw(RuntimeError("no local voices")),
    )

    # The turn still reaches the user as text; it simply has no audio.
    assert engine.generate_audio("hello") is None
    assert engine.generate_audio("again") is None


def test_the_broken_fallback_is_only_built_once(tmp_path):
    attempts = []

    def build():
        attempts.append(1)
        raise RuntimeError("no local voices")

    engine = FallbackTTSEngine(_Engine(tmp_path, fails=True), build)
    engine.generate_audio("one")
    engine.generate_audio("two")
    engine.generate_audio("three")

    assert len(attempts) == 1


def test_build_tts_engine_survives_an_engine_that_cannot_start(monkeypatch, tmp_path):
    """A dead TTS engine must not take the whole session down with it."""
    import sys

    class Factory:
        @staticmethod
        def get_tts_engine(name, **_kwargs):
            if name == "edge_tts":
                raise RuntimeError("cannot reach the voice service")
            return _Engine(tmp_path)

    # build_tts_engine imports the factory at call time, so replacing the
    # module is what reaches it.
    monkeypatch.setitem(
        sys.modules,
        "src.open_llm_vtuber.tts.tts_factory",
        SimpleNamespace(TTSFactory=Factory),
    )
    config = SimpleNamespace(
        tts_model="edge_tts", tts_fallback_model="pyttsx3_tts", edge_tts=None
    )

    engine = build_tts_engine(config)

    assert isinstance(engine, FallbackTTSEngine)
    assert Path(engine.generate_audio("hello")).exists()


def test_a_partial_model_extraction_is_detected(tmp_path):
    model_dir = tmp_path / "sherpa-model"
    model_dir.mkdir()
    (model_dir / "model.onnx").write_bytes(b"partially written weights")
    archive = tmp_path / "sherpa-model.tar.bz2"

    # Weights present and no archive left over: a finished extraction.
    assert is_model_dir_complete(model_dir, archive) is True

    # A successful extraction deletes its archive, so one still sitting there
    # means the extraction stopped halfway.
    archive.write_bytes(b"truncated archive")
    assert is_model_dir_complete(model_dir, archive) is False

    # No weights at all is equally unusable.
    archive.unlink()
    (model_dir / "model.onnx").unlink()
    assert is_model_dir_complete(model_dir, archive) is False


async def test_audio_input_without_an_asr_engine_tells_the_user(monkeypatch):
    import numpy as np

    from src.open_llm_vtuber.conversations import conversation_utils

    sent: list[dict] = []

    async def websocket_send(message: str) -> None:
        sent.append(json.loads(message))

    result = await conversation_utils.process_user_input(
        np.zeros(16, dtype=np.float32), None, websocket_send
    )

    assert result == ""
    assert sent[0]["type"] == "user-input-transcription"
    assert "unavailable" in sent[0]["text"]


def test_pyttsx3_names_its_output_after_the_bytes_it_wrote(tmp_path, monkeypatch):
    """Mislabelled containers are silent without ffmpeg, so the name must match."""
    from src.open_llm_vtuber.tts import pyttsx3_tts

    written: dict[str, str] = {}

    class Engine:
        def save_to_file(self, text, filename):
            written["path"] = filename
            Path(filename).write_bytes(b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 100)

        def runAndWait(self):
            return None

    monkeypatch.setattr(pyttsx3_tts.pyttsx3, "init", lambda: Engine())
    monkeypatch.chdir(tmp_path)

    path = pyttsx3_tts.TTSEngine().generate_audio("hello", "probe")

    assert path.endswith(".wav")
    assert Path(path).exists()
    assert not Path(written["path"]).exists()


@pytest.mark.parametrize(
    "header, expected",
    [
        (b"RIFF\0\0\0\0WAVE", "wav"),
        (b"FORM\0\0\0\0AIFF", "aiff"),
        (b"unknown-----", "wav"),
    ],
)
def test_container_sniffing(tmp_path, header, expected):
    from src.open_llm_vtuber.tts.pyttsx3_tts import _extension_for

    path = tmp_path / "probe.out"
    path.write_bytes(header + b"\0" * 64)

    assert _extension_for(str(path)) == expected
