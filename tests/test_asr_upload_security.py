from __future__ import annotations

import io
import struct
import wave
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.open_llm_vtuber import routes
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


def test_asr_rejects_oversized_multipart_before_route_read(
    asr_client,
    monkeypatch,
):
    client, engine = asr_client
    monkeypatch.setattr(routes, "_MAX_ASR_UPLOAD_BYTES", 32)
    monkeypatch.setattr(routes, "_MAX_ASR_REQUEST_BYTES", 128)

    response = client.post(
        "/asr",
        files={"file": ("oversized.wav", b"x" * 1024, "audio/wav")},
    )

    assert response.status_code == 413
    assert engine.audio is None


@pytest.mark.asyncio
async def test_asr_chunked_stream_stops_consuming_at_request_limit(monkeypatch):
    consumed = []

    async def chunks():
        for chunk in (b"123", b"456", b"must-not-be-read"):
            consumed.append(chunk)
            yield chunk

    request = SimpleNamespace(headers={}, stream=chunks)
    monkeypatch.setattr(routes, "_MAX_ASR_REQUEST_BYTES", 5)

    with pytest.raises(routes._ASRUploadTooLarge):
        async for _chunk in routes._bounded_asr_request_stream(request):
            pass

    assert consumed == [b"123", b"456"]


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


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"content": b"not a form"},
        {"json": {"file": "audio"}},
        {"content": b"x", "headers": {"content-type": "audio/wav"}},
    ],
)
def test_a_request_that_is_not_an_upload_is_a_client_error(asr_client, kwargs):
    """Reading the body directly took away the framework's own 4xx.

    Streaming the multipart body under a size bound means FastAPI no longer
    validates the request shape first, and `MultiPartParser` raises `KeyError`
    for a missing Content-Type - reported as a 500 for what is plainly a
    malformed request.
    """
    client, engine = asr_client

    response = client.post("/asr", **kwargs)

    assert response.status_code == 400, response.text
    assert engine.audio is None
