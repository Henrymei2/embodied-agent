from __future__ import annotations

import io
import struct
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.open_llm_vtuber.request_security import build_trusted_hosts
from src.open_llm_vtuber.routes import init_webtool_routes


class RecordingASREngine:
    def __init__(self):
        self.audio = None

    async def async_transcribe_np(self, audio):
        self.audio = audio
        return "accepted"


@pytest.fixture
def asr_client():
    engine = RecordingASREngine()
    context = SimpleNamespace(asr_engine=engine)
    app = FastAPI()
    app.state.trusted_hosts = build_trusted_hosts("localhost", ["testserver"])
    app.include_router(init_webtool_routes(context))
    client = TestClient(app, headers={"origin": "http://testserver"})
    return client, engine


def _wav_bytes(
    frames: bytes,
    *,
    channels: int = 1,
    sample_width: int = 2,
    sample_rate: int = 16000,
) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(frames)
    return output.getvalue()


def _add_junk_chunk(contents: bytes) -> bytes:
    extended = bytearray(contents)
    data_offset = extended.index(b"data")
    extended[data_offset:data_offset] = b"JUNK" + struct.pack("<I", 4) + b"meta"
    struct.pack_into("<I", extended, 4, len(extended) - 8)
    return bytes(extended)


def test_asr_rejects_malformed_wav_before_calling_engine(asr_client):
    client, engine = asr_client

    response = client.post(
        "/asr",
        files={"file": ("malformed.wav", b"x" * 46, "audio/wav")},
    )

    assert response.status_code == 400
    assert "Invalid WAV file" in response.json()["error"]
    assert engine.audio is None


def test_asr_reads_pcm_data_from_extended_wav_chunks(asr_client):
    client, engine = asr_client
    samples = np.array([0, 32767, -32768, 16384], dtype="<i2")
    contents = _add_junk_chunk(_wav_bytes(samples.tobytes()))

    response = client.post(
        "/asr",
        files={"file": ("extended.wav", contents, "audio/wav")},
    )

    assert response.status_code == 200
    assert response.json() == {"text": "accepted"}
    np.testing.assert_allclose(
        engine.audio,
        np.array([0.0, 32767 / 32768, -1.0, 0.5], dtype=np.float32),
    )


@pytest.mark.parametrize(
    ("wav_kwargs", "expected_error"),
    [
        ({"channels": 2}, "exactly one channel"),
        ({"sample_width": 1}, "16-bit PCM"),
        ({"sample_rate": 8000}, "16000 Hz"),
    ],
)
def test_asr_rejects_incompatible_pcm_formats(
    asr_client,
    wav_kwargs,
    expected_error,
):
    client, engine = asr_client
    contents = _wav_bytes(b"\x00\x00\x00\x00", **wav_kwargs)

    response = client.post(
        "/asr",
        files={"file": ("incompatible.wav", contents, "audio/wav")},
    )

    assert response.status_code == 400
    assert expected_error in response.json()["error"]
    assert engine.audio is None
