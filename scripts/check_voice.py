"""Verify the voice round trip before you need it in front of an audience.

Speaks a known sentence with the configured TTS engine, then feeds that audio
back through the configured ASR engine and compares the transcript. Both halves
are reported separately, so a failure says which one to fix:

    uv run python scripts/check_voice.py
    uv run python scripts/check_voice.py --offline   # pretend the network is gone

`--offline` blocks outbound sockets for the duration of the run, which is the
honest way to answer "will this still talk on bad conference wifi?": the
configured engine fails exactly as it would there, and the local fallback has
to carry the sentence.

Exit code is 0 when speech can be produced at all (fallback counts), 1 when the
companion would be mute.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
import wave
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np  # noqa: E402
from loguru import logger  # noqa: E402

from src.open_llm_vtuber.asr.asr_factory import ASRFactory  # noqa: E402
from src.open_llm_vtuber.config_manager import (  # noqa: E402
    Config,
    read_yaml,
    validate_config,
)
from src.open_llm_vtuber.tts.fallback_tts import (  # noqa: E402
    FallbackTTSEngine,
    build_tts_engine,
)
from src.open_llm_vtuber.utils.stream_audio import prepare_audio_payload  # noqa: E402

SENTENCE = "The quick brown fox jumps over the lazy dog."

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _ok(message: str) -> None:
    print(f"{_GREEN}  OK  {_RESET} {message}")


def _warn(message: str) -> None:
    print(f"{_YELLOW} WARN {_RESET} {message}")


def _fail(message: str) -> None:
    print(f"{_RED} FAIL {_RESET} {message}")


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class _NoNetwork:
    """Refuse outbound connections, so remote engines fail as they would offline.

    Name resolution is blocked as well as connecting, because an engine that
    speaks over an async transport never calls the blocking `socket.connect` —
    patching only that would let it right through and quietly turn this check
    into a no-op.

    Loopback stays open: asyncio builds its own self-pipe over it on Windows,
    and a local Ollama is not what this check is testing.
    """

    def __enter__(self):
        self._connect = socket.socket.connect
        self._getaddrinfo = socket.getaddrinfo
        connect, getaddrinfo = self._connect, self._getaddrinfo

        def refuse_connect(self_socket, address):
            host = address[0] if isinstance(address, tuple) else address
            if host in _LOOPBACK:
                return connect(self_socket, address)
            raise OSError(f"offline check: connection to {address} refused")

        def refuse_resolve(host, *args, **kwargs):
            if host in _LOOPBACK or host is None:
                return getaddrinfo(host, *args, **kwargs)
            raise socket.gaierror(f"offline check: cannot resolve {host}")

        socket.socket.connect = refuse_connect
        socket.getaddrinfo = refuse_resolve
        return self

    def __exit__(self, *_args):
        socket.socket.connect = self._connect
        socket.getaddrinfo = self._getaddrinfo
        return False


def _load_audio(path: Path) -> tuple[np.ndarray | None, str]:
    """Read a generated file as float32 mono samples, if it is a WAV."""
    if path.suffix.lower() not in {".wav", ".aiff"}:
        return None, f"{path.suffix or 'unknown'} (transcription check skipped)"
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.readframes(handle.getnframes())
            channels = handle.getnchannels()
            width = handle.getsampwidth()
    except (wave.Error, OSError) as exc:
        return None, f"unreadable audio: {exc}"

    if width != 2:
        return None, f"{width * 8}-bit audio (expected 16-bit)"
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, f"{len(samples) / 16000:.1f}s"


def check_tts(config: Config) -> tuple[Path | None, bool]:
    """Synthesize the sentence. Returns the audio path and whether it was local."""
    tts_config = config.character_config.tts_config
    engine = build_tts_engine(tts_config)
    path = engine.generate_audio(SENTENCE, "voice_check")
    used_fallback = isinstance(engine, FallbackTTSEngine) and engine.using_fallback

    if not path or not Path(path).exists():
        _fail(
            f"TTS produced no audio. Configured engine: {tts_config.tts_model}, "
            f"fallback: {tts_config.tts_fallback_model or 'none'}. "
            "The companion would answer in text only."
        )
        return None, used_fallback

    size = Path(path).stat().st_size
    if used_fallback:
        _warn(
            f"TTS spoke through the local fallback "
            f"({tts_config.tts_fallback_model}), not {tts_config.tts_model} "
            f"— {size} bytes. Audio survives without the network."
        )
    else:
        _ok(f"TTS spoke through {tts_config.tts_model} — {size} bytes.")

    # Synthesis is only half the job: the file still has to survive being
    # decoded into the payload the browser plays, which is where a
    # wrongly-named container (or a missing ffmpeg) silently costs the audio.
    try:
        payload = prepare_audio_payload(str(path))
    except Exception as exc:  # noqa: BLE001
        _fail(
            f"The generated audio could not be packaged for the browser: {exc}\n"
            "        The user would see subtitles with no sound. Installing "
            "FFmpeg fixes most container problems."
        )
        return Path(path), used_fallback

    if not payload.get("audio"):
        _fail("The audio payload came out empty; the user would hear nothing.")
    else:
        _ok(
            f"Audio packaged for playback — {len(payload['audio'])} base64 chars, "
            f"{len(payload['volumes'])} lip-sync frames."
        )
    return Path(path), used_fallback


def check_asr(config: Config, audio_path: Path | None) -> bool:
    """Transcribe the generated audio with the configured ASR engine."""
    asr_config = config.character_config.asr_config
    try:
        engine = ASRFactory.get_asr_system(
            asr_config.asr_model,
            **getattr(asr_config, asr_config.asr_model).model_dump(),
        )
    except Exception as exc:  # noqa: BLE001
        _fail(
            f"ASR ({asr_config.asr_model}) did not load: {exc}\n"
            "        Voice input will be unavailable; typed conversation still "
            "works. For a corrupt model, delete the model directory and archive "
            "under models/ and start the server again."
        )
        return False

    if audio_path is None:
        _warn(f"ASR ({asr_config.asr_model}) loaded, but there was no audio to test.")
        return False

    samples, note = _load_audio(audio_path)
    if samples is None:
        _warn(f"ASR ({asr_config.asr_model}) loaded; {note}.")
        return True

    try:
        transcript = asyncio.run(engine.async_transcribe_np(samples))
    except Exception as exc:  # noqa: BLE001
        _fail(f"ASR ({asr_config.asr_model}) failed to transcribe: {exc}")
        return False

    spoken = {word.strip(".,").lower() for word in SENTENCE.split()}
    heard = {word.strip(".,").lower() for word in transcript.split()}
    overlap = len(spoken & heard) / len(spoken)
    if overlap >= 0.5:
        _ok(f'ASR heard "{transcript.strip()}" ({overlap:.0%} of the words).')
        return True
    _warn(
        f'ASR loaded but heard "{transcript.strip()}" instead of the test '
        f"sentence ({overlap:.0%} match). Check the microphone sample rate."
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="block outbound connections, to test the no-network path",
    )
    args = parser.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    os.chdir(_PROJECT_ROOT)
    config: Config = validate_config(read_yaml("conf.yaml"))
    print(f"\nVoice check{' (offline)' if args.offline else ''}\n")

    if args.offline:
        with _NoNetwork():
            audio_path, used_fallback = check_tts(config)
    else:
        audio_path, used_fallback = check_tts(config)

    asr_ok = check_asr(config, audio_path)
    if audio_path and audio_path.exists():
        audio_path.unlink(missing_ok=True)

    print()
    if audio_path is None:
        print("The companion cannot speak. Fix TTS before demoing.\n")
        return 1
    if not asr_ok:
        print("The companion can speak but cannot listen: text input only.\n")
        return 0
    print(
        "Voice round trip works"
        + (" through the local fallback.\n" if used_fallback else ".\n")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
