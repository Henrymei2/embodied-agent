"""Drawing Board tools for the embodied companion.

These tools belong to the `drawing_board` object in the room: the agent has to
walk to the drawing board before it can generate or review drawings.

Generated images are saved locally in `<workspace>/drawing_board/`. The latest
image is shown in the hover window when the user points at the Drawing Board.

The image API key is read from OPENAI_API_KEY. Tests mock the API call, so test
runs do not spend credits or contact the network.

Run standalone for a quick sanity check:
    uv run python mcp_servers/drawing_board_tools.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from mcp.server.fastmcp import FastMCP

from src.open_llm_vtuber.furniture_state import (
    drawing_board_dir,
    drawing_board_snapshot,
    save_drawing_board_image,
)

mcp = FastMCP("drawing_board")

_OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
_DEFAULT_MODEL = "gpt-image-1"
_DEFAULT_SIZE = "1024x1024"
_DEFAULT_QUALITY = "auto"
_ALLOWED_SIZES = {"1024x1024", "1024x1536", "1536x1024", "auto"}
_ALLOWED_QUALITIES = {"low", "medium", "high", "auto"}
_MAX_PROMPT_CHARS = 2_000
_TIMEOUT_SECONDS = 120.0
_MAX_RESPONSE_BYTES = 25 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    cleaned = prompt.strip()
    if not cleaned:
        raise ValueError("prompt cannot be empty")
    if len(cleaned) > _MAX_PROMPT_CHARS:
        raise ValueError(f"prompt cannot exceed {_MAX_PROMPT_CHARS} characters")
    return cleaned


def _validate_choice(value: str, allowed: set[str], field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    cleaned = value.strip().lower()
    if cleaned not in allowed:
        raise ValueError(f"{field} must be one of: {', '.join(sorted(allowed))}")
    return cleaned


def _call_openai_image_api(
    prompt: str,
    *,
    model: str,
    size: str,
    quality: str,
) -> str:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("OPENAI_API_KEY is not set")

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
    }
    request = urllib.request.Request(
        _OPENAI_IMAGES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")
            detail = json.loads(body).get("error", {}).get("message") or body
        except Exception:
            detail = f"HTTP {exc.code}"
        raise ValueError(f"image API request failed: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ValueError(f"could not reach image API: {exc}") from exc

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("image API response was too large")

    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("image API returned malformed JSON") from exc

    data = body.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("image API returned no image data")

    first = data[0]
    if not isinstance(first, dict):
        raise ValueError("image API returned malformed image data")

    image_b64 = first.get("b64_json")
    if not isinstance(image_b64, str) or not image_b64.strip():
        raise ValueError("image API response did not include b64_json image data")

    return image_b64


@mcp.tool()
def draw_image(
    prompt: str,
    size: str = _DEFAULT_SIZE,
    quality: str = _DEFAULT_QUALITY,
) -> str:
    """Generate an image on the Drawing Board. Requires standing near drawing_board.

    Args:
        prompt: Description of the image to draw.
        size: "1024x1024", "1024x1536", "1536x1024", or "auto".
        quality: "low", "medium", "high", or "auto".
    """
    try:
        cleaned_prompt = _validate_prompt(prompt)
        cleaned_size = _validate_choice(size, _ALLOWED_SIZES, "size")
        cleaned_quality = _validate_choice(quality, _ALLOWED_QUALITIES, "quality")
        model = os.environ.get("OPENAI_IMAGE_MODEL", _DEFAULT_MODEL).strip() or _DEFAULT_MODEL

        image_b64 = _call_openai_image_api(
            cleaned_prompt,
            model=model,
            size=cleaned_size,
            quality=cleaned_quality,
        )

        image_bytes = base64.b64decode(image_b64, validate=True)
        drawing_id = uuid.uuid4().hex[:12]
        metadata = save_drawing_board_image(
            image_bytes,
            prompt=cleaned_prompt,
            drawing_id=drawing_id,
            model=model,
            size=cleaned_size,
            quality=cleaned_quality,
            created_at=_now(),
        )
        return json.dumps({"ok": True, "drawing": metadata}, ensure_ascii=False)
    except (OSError, TypeError, ValueError, base64.binascii.Error) as exc:
        return f"Error: {exc}"


@mcp.tool()
def get_last_drawing() -> str:
    """Return metadata for the last Drawing Board image. Requires standing near drawing_board."""
    try:
        return json.dumps(drawing_board_snapshot(), ensure_ascii=False)
    except (OSError, TypeError, ValueError) as exc:
        return f"Error: {exc}"


if __name__ == "__main__":
    mcp.run()
