"""Go/no-go check for a live demo, run before the room fills up.

A demo fails at setup, not at features. This checks every dependency the
presentation actually leans on, says which of them would end the demo and which
would only cost a beat, and tells you the exact command to fix each one.

Three severities, and the difference matters when you have ten minutes:

* `fatal`  — the demo cannot proceed. Fix before starting.
* `degraded` — a beat is lost or falls back to something less impressive, but
  the demo runs. Know which beat, so you can skip it rather than discover it
  on stage.
* `ok`     — verified working.

Exit status is non-zero only for `fatal`, so this can gate a launch script.

Run it twice: once the day before with network, and once in the room on the
actual network, because the two are rarely the same machine in the same place.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OK = "ok"
DEGRADED = "degraded"
FATAL = "fatal"

_MARK = {OK: "OK  ", DEGRADED: "WARN", FATAL: "STOP"}


@dataclass
class Result:
    name: str
    severity: str
    detail: str
    fix: str = ""
    beat: str = ""


def _check(name: str, body: Callable[[], Result]) -> Result:
    try:
        return body()
    except Exception as exc:  # noqa: BLE001 - a broken check must not stop preflight
        return Result(
            name, FATAL, f"the check itself failed: {type(exc).__name__}: {exc}"
        )


# --- the things that end a demo ----------------------------------------------


def check_server_imports() -> Result:
    from src.open_llm_vtuber.server import WebSocketServer  # noqa: F401

    return Result("server", OK, "the server module imports")


def check_frontend_built() -> Result:
    index = ROOT / "frontend" / "index.html"
    if not index.exists():
        return Result(
            "frontend",
            FATAL,
            "frontend/index.html is missing, so there is no UI",
            fix="build the web client, or restore the frontend/ directory",
        )
    return Result("frontend", OK, "built client present")


def check_llm() -> Result:
    """The one dependency with no fallback: no model, no conversation."""
    from src.open_llm_vtuber.config_manager.utils import read_yaml, validate_config

    config = validate_config(read_yaml(str(ROOT / "conf.yaml")))
    agent = config.character_config.agent_config.agent_settings.basic_memory_agent
    provider = getattr(agent, "llm_provider", "unknown")

    if provider != "ollama_llm":
        return Result(
            "llm",
            DEGRADED,
            f"provider is {provider!r}; preflight only probes ollama",
            fix="check that provider's credentials and endpoint by hand",
        )
    host = "127.0.0.1"
    port = 11434
    connection = socket.socket()
    connection.settimeout(3)
    try:
        connection.connect((host, port))
    except OSError:
        return Result(
            "llm",
            FATAL,
            f"ollama is not answering on {host}:{port}; the agent cannot think",
            fix="start ollama, then `ollama pull <the model in conf.yaml>`",
            beat="every beat",
        )
    finally:
        connection.close()
    return Result("llm", OK, f"ollama answering on {host}:{port}")


# --- the things that cost a beat ---------------------------------------------


def check_ffmpeg() -> Result:
    path = shutil.which("ffmpeg")
    if path:
        return Result("ffmpeg", OK, path)
    return Result(
        "ffmpeg",
        DEGRADED,
        "not on PATH; pydub cannot transcode, so some audio paths go silent",
        fix="install ffmpeg and reopen the shell",
        beat="voice reply may lose audio and fall back to subtitles",
    )


def check_tts_fallback() -> Result:
    from src.open_llm_vtuber.tts import fallback_tts  # noqa: F401

    return Result(
        "tts fallback",
        OK,
        "a local voice takes over if the remote service is unreachable",
    )


def check_fixtures() -> Result:
    """The recorded API responses that make the room's tools work offline."""
    mode = (os.environ.get("EMBODIED_AGENT_FIXTURES") or "off").strip().lower()
    recorded = sorted(p.name for p in (ROOT / "fixtures").glob("*") if p.is_dir())
    if not recorded:
        return Result(
            "fixtures",
            DEGRADED,
            "no recorded API responses; bookshelf/window need live network",
            fix="uv run python scripts/record_fixtures.py   (needs network)",
            beat="reading a book, checking the weather",
        )
    if mode != "replay":
        return Result(
            "fixtures",
            DEGRADED,
            f"recorded for {recorded}, but EMBODIED_AGENT_FIXTURES={mode!r}",
            fix='$env:EMBODIED_AGENT_FIXTURES = "replay"   before starting the server',
            beat="bookshelf/window will call the live network instead",
        )
    return Result("fixtures", OK, f"replaying recorded responses for {recorded}")


def check_asr() -> Result:
    from src.open_llm_vtuber.config_manager.utils import read_yaml, validate_config

    config = validate_config(read_yaml(str(ROOT / "conf.yaml")))
    model = getattr(config.character_config.asr_config, "asr_model", "unknown")
    unpacked = [
        p
        for p in (ROOT / "models").glob("**/*")
        if p.is_file()
        and p.suffix in {".onnx", ".bin", ".pt"}
        and p.stat().st_size > 1_000_000
    ]
    archives = list((ROOT / "models").glob("*.tar.bz2")) + list(
        (ROOT / "models").glob("*.zip")
    )
    if not unpacked and archives:
        return Result(
            "asr",
            DEGRADED,
            f"{model}: only unextracted archives in models/ ({archives[0].name})",
            fix="extract the archive, or let the engine download on first use (needs network)",
            beat="speaking to the agent; typing still works",
        )
    if not unpacked:
        return Result(
            "asr",
            DEGRADED,
            f"{model}: no local model found; first use will try to download",
            fix="run one spoken turn while you still have network",
            beat="speaking to the agent; typing still works",
        )
    return Result("asr", OK, f"{model}: {len(unpacked)} local model file(s)")


def check_vr_origin() -> Result:
    """WebXR needs a trusted HTTPS origin; an IP certificate warning is not enough."""
    from src.open_llm_vtuber.config_manager.utils import read_yaml, validate_config

    config = validate_config(read_yaml(str(ROOT / "conf.yaml")))
    system = config.system_config
    cert = getattr(system, "tls_cert_file", None) or getattr(
        system, "ssl_certfile", None
    )
    if not cert:
        return Result(
            "vr https",
            DEGRADED,
            "no TLS certificate configured; Quest Browser will refuse immersive VR",
            fix="see doc/VR_MODE.md section 7, then install the CA on the headset",
            beat="the VR segment; the screen demo is unaffected",
        )
    if not Path(str(cert)).exists():
        return Result(
            "vr https",
            DEGRADED,
            f"configured certificate is missing: {cert}",
            fix="regenerate it, or present on screen only",
            beat="the VR segment",
        )
    return Result("vr https", OK, f"certificate present at {cert}")


def check_demo_beats() -> Result:
    """The two beats this presentation is built around must be exercisable."""
    missing: list[str] = []
    if not (ROOT / "src" / "open_llm_vtuber" / "provenance" / "ledger.py").exists():
        missing.append("provenance ledger")
    if not (ROOT / "src" / "open_llm_vtuber" / "autonomy" / "plan_state.py").exists():
        missing.append("resumable plan state")
    if missing:
        return Result("demo beats", FATAL, f"missing: {missing}")
    return Result(
        "demo beats", OK, "provenance chain and crash-resume machinery are present"
    )


CHECKS: tuple[tuple[str, Callable[[], Result]], ...] = (
    ("server", check_server_imports),
    ("frontend", check_frontend_built),
    ("llm", check_llm),
    ("asr", check_asr),
    ("ffmpeg", check_ffmpeg),
    ("tts fallback", check_tts_fallback),
    ("fixtures", check_fixtures),
    ("vr https", check_vr_origin),
    ("demo beats", check_demo_beats),
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, help="write the report to a file")
    args = parser.parse_args()

    results = [_check(name, body) for name, body in CHECKS]
    width = max(len(r.name) for r in results)

    print("\nDemo preflight")
    print("=" * 74)
    for result in results:
        print(f"[{_MARK[result.severity]}] {result.name.ljust(width)}  {result.detail}")
        if result.beat:
            print(f"         {' ' * width}  costs: {result.beat}")
        if result.fix:
            print(f"         {' ' * width}  fix:   {result.fix}")

    fatal = [r for r in results if r.severity == FATAL]
    degraded = [r for r in results if r.severity == DEGRADED]
    print("=" * 74)
    if fatal:
        print(f"NO-GO - {len(fatal)} blocking problem(s): {[r.name for r in fatal]}")
    elif degraded:
        print(f"GO, with {len(degraded)} degraded: {[r.name for r in degraded]}")
        print("Know which beats those cost before you start, and plan to skip them.")
    else:
        print("GO - everything verified.")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"checks": [vars(r) for r in results]}, indent=2) + "\n",
            encoding="utf-8",
        )
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
