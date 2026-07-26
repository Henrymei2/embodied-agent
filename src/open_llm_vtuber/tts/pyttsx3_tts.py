import os
import sys
import threading

import pyttsx3
from loguru import logger

from .tts_interface import TTSInterface

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)


# using https://github.com/thevickypedia/py3-tts because pyttsx3 is unmaintained and not working


def _extension_for(path: str) -> str:
    """Name the file after what the platform actually wrote into it.

    Which container comes out depends on the speech backend: SAPI5 on Windows
    and espeak on Linux write RIFF/WAVE, macOS writes AIFF. Downstream, pydub
    picks its decoder from the file name, and its built-in WAV reader is the
    only one that works without ffmpeg installed — so mislabelling WAV bytes as
    `.aiff` is the difference between audible and silent.
    """
    try:
        with open(path, "rb") as handle:
            header = handle.read(12)
    except OSError:
        return "wav"
    if header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return "wav"
    if header[:4] == b"FORM" and header[8:12] in {b"AIFF", b"AIFC"}:
        return "aiff"
    return "wav"


class TTSEngine(TTSInterface):
    def __init__(self):
        self.engine = pyttsx3.init()
        self.temp_audio_file = "temp"
        self.file_extension = "wav"
        self.new_audio_dir = "cache"
        self.lock = threading.Lock()

        if not os.path.exists(self.new_audio_dir):
            os.makedirs(self.new_audio_dir)

    #! This method (pyttsx3) is not thread safe. It will blow if it's called from multiple threads at the same time.
    def generate_audio(self, text, file_name_no_ext=None):
        logger.debug(f"Start Generating {file_name_no_ext}")
        file_name = self.generate_cache_file_name(file_name_no_ext, "out")

        with self.lock:
            self.engine.save_to_file(text=text, filename=file_name)
            self.engine.runAndWait()

        final_name = f"{os.path.splitext(file_name)[0]}.{_extension_for(file_name)}"
        try:
            os.replace(file_name, final_name)
        except OSError as exc:
            logger.error(f"pyttsx3: could not name the generated audio: {exc}")
            return file_name
        logger.info(f"Finished Generating {final_name}")
        return final_name


if __name__ == "__main__":
    TTSEngine = TTSEngine()
    TTSEngine.generate_audio(
        "Hello, this is a test. But this is not a test. You are screwed bro. You only live once. YOLO."
    )

    def worker(engine, text, index):
        file_name = f"audio_{index}"
        engine.generate_audio(text, file_name)

    texts = [
        "Hello, this is a test.",
        "This is another test.",
        "Yet another test sentence.",
        "Testing multithreading.",
        "Final test sentence.",
    ]

    threads = []
    for i, text in enumerate(texts):
        t = threading.Thread(target=worker, args=(TTSEngine, text, i))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()
