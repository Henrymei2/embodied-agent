"""Keep the companion audible when its voice service is unreachable.

The default engine (`edge_tts`) synthesizes speech through a remote Microsoft
service, so a captive portal, a blocked region, or plain bad conference wifi
turns a voice-first companion silent — the one failure a live demo cannot
absorb. This wrapper speaks with the configured engine and, the moment that
engine fails or returns nothing, re-speaks the same line with a local one.

The fallback is built lazily, so an unusable local engine costs nothing until
it is actually needed, and never blocks startup. If both fail, `generate_audio`
returns None exactly as a bare engine would: the turn still reaches the user as
subtitles, just without audio.
"""

from __future__ import annotations

import os
from typing import Callable

from loguru import logger

from .tts_interface import TTSInterface

# Below this, a "successful" synthesis is really an empty or truncated file.
_MIN_AUDIO_BYTES = 128


def _is_usable(path: str | None) -> bool:
    """True when a returned path is a file with actual audio in it."""
    if not path or not isinstance(path, str):
        return False
    try:
        return os.path.getsize(path) >= _MIN_AUDIO_BYTES
    except OSError:
        return False


class FallbackTTSEngine(TTSInterface):
    """Speak with `primary`, and with `build_fallback()` when that fails."""

    def __init__(
        self,
        primary: TTSInterface | None,
        build_fallback: Callable[[], TTSInterface],
        primary_name: str = "primary",
        fallback_name: str = "fallback",
        playable: Callable[[str], bool] | None = None,
    ) -> None:
        self._primary = primary
        self._build_fallback = build_fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name
        self._playable = playable
        self._fallback: TTSInterface | None = None
        self._fallback_unavailable = False
        # Repeated failures are the normal case offline, so the loud message is
        # logged once and the rest are counted.
        self._primary_failures = 0
        self._using_fallback = False
        # Whether the primary's output has been proven decodable. Checked once:
        # the container never changes for a given engine, and re-decoding every
        # sentence would cost latency for no new information.
        self._primary_verified = False

    def _accepts(self, path: str) -> bool:
        """Reject audio that exists but cannot be turned into playback.

        Producing a file is not the same as being heard. An MP3 on a machine
        without FFmpeg decodes to nothing downstream, and the user gets
        subtitles in silence — indistinguishable, from where they sit, from the
        engine having failed outright. So it counts as a failure here, and the
        local engine (which writes WAV) takes over.
        """
        if not _is_usable(path):
            return False
        if self._playable is None or self._primary_verified:
            return True
        if self._playable(path):
            self._primary_verified = True
            return True
        self._note_primary_failure(
            "its audio could not be decoded for playback (FFmpeg is most likely "
            "missing)"
        )
        # Nothing about this engine will change on the next sentence.
        self._primary = None
        return False

    @property
    def using_fallback(self) -> bool:
        """True once the primary engine has failed and the local one took over."""
        return self._using_fallback

    def _note_primary_failure(self, reason: object) -> None:
        self._primary_failures += 1
        if self._primary_failures == 1:
            logger.warning(
                f"TTS: {self._primary_name} could not speak ({reason}); "
                f"falling back to {self._fallback_name}. "
                "Subsequent failures are counted, not logged."
            )
        elif self._primary_failures % 20 == 0:
            logger.warning(
                f"TTS: {self._primary_name} has failed {self._primary_failures} "
                f"times; still speaking through {self._fallback_name}."
            )

    def _get_fallback(self) -> TTSInterface | None:
        if self._fallback is not None or self._fallback_unavailable:
            return self._fallback
        try:
            self._fallback = self._build_fallback()
        except Exception as exc:  # noqa: BLE001 - any failure means "no fallback"
            self._fallback_unavailable = True
            logger.error(
                f"TTS: local fallback {self._fallback_name} is unavailable ({exc}). "
                "Replies will be delivered as text without audio."
            )
        return self._fallback

    def _speak_with_fallback(self, text: str, file_name_no_ext) -> str | None:
        fallback = self._get_fallback()
        if fallback is None:
            return None
        try:
            path = fallback.generate_audio(text, file_name_no_ext)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"TTS: fallback {self._fallback_name} also failed: {exc}")
            return None
        if not _is_usable(path):
            logger.error(f"TTS: fallback {self._fallback_name} produced no audio.")
            return None
        self._using_fallback = True
        return path

    def generate_audio(self, text: str, file_name_no_ext=None) -> str | None:
        if self._primary is not None:
            try:
                path = self._primary.generate_audio(text, file_name_no_ext)
            except Exception as exc:  # noqa: BLE001
                self._note_primary_failure(exc)
            else:
                if self._accepts(path):
                    self._using_fallback = False
                    return path
                if _is_usable(path):
                    # Unplayable audio: the reason is already reported.
                    pass
                else:
                    # edge_tts reports failure by returning None, not by raising.
                    self._note_primary_failure("engine returned no audio")
        return self._speak_with_fallback(text, file_name_no_ext)

    async def async_generate_audio(
        self, text: str, file_name_no_ext=None
    ) -> str | None:
        """Prefer the primary engine's own async path before falling back."""
        if self._primary is not None:
            try:
                path = await self._primary.async_generate_audio(text, file_name_no_ext)
            except Exception as exc:  # noqa: BLE001
                self._note_primary_failure(exc)
            else:
                if self._accepts(path):
                    self._using_fallback = False
                    return path
                if not _is_usable(path):
                    self._note_primary_failure("engine returned no audio")

        fallback = self._get_fallback()
        if fallback is None:
            return None
        try:
            path = await fallback.async_generate_audio(text, file_name_no_ext)
        except Exception as exc:  # noqa: BLE001
            logger.error(f"TTS: fallback {self._fallback_name} also failed: {exc}")
            return None
        if not _is_usable(path):
            logger.error(f"TTS: fallback {self._fallback_name} produced no audio.")
            return None
        self._using_fallback = True
        return path

    def remove_file(self, filepath: str, verbose: bool = True) -> None:
        engine = self._primary or self._fallback
        if engine is not None:
            engine.remove_file(filepath, verbose)
        else:
            super().remove_file(filepath, verbose)


def build_tts_engine(tts_config) -> TTSInterface:
    """Build the configured engine, backed by a local one where configured.

    Never raises: a companion that cannot speak should still be able to answer
    in text, so an unusable engine degrades to silent audio payloads rather
    than taking the session down with it.
    """
    from .tts_factory import TTSFactory

    primary_name = tts_config.tts_model
    fallback_name = getattr(tts_config, "tts_fallback_model", None)

    def build(name: str) -> TTSInterface:
        options = getattr(tts_config, name.lower(), None)
        return TTSFactory.get_tts_engine(
            name, **(options.model_dump() if options is not None else {})
        )

    primary: TTSInterface | None = None
    try:
        primary = build(primary_name)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"TTS: {primary_name} could not be initialized: {exc}")

    if primary is not None and (not fallback_name or fallback_name == primary_name):
        return primary

    def build_fallback() -> TTSInterface:
        if not fallback_name:
            raise RuntimeError("no tts_fallback_model is configured")
        return build(fallback_name)

    def playable(path: str) -> bool:
        """Can this file actually be decoded into playback for the browser?"""
        from pydub import AudioSegment

        try:
            AudioSegment.from_file(path)
        except Exception as exc:  # noqa: BLE001 - any decode failure is silence
            logger.warning(f"TTS: {path} could not be decoded for playback: {exc}")
            return False
        return True

    logger.info(
        f"TTS: {primary_name} is backed by {fallback_name or 'no fallback'}."
        if primary is not None
        else f"TTS: falling back to {fallback_name or 'no engine'} for this session."
    )
    return FallbackTTSEngine(
        primary,
        build_fallback,
        primary_name=primary_name,
        fallback_name=fallback_name or "none",
        playable=playable,
    )
