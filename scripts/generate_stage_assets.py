"""Regenerate the pixel-art stage assets that ship with the default room.

Everything here is drawn on a small grid and scaled up by an integer factor, so
the output stays crisp under the frontend's `imageRendering: pixelated`. Pillow
is not a project dependency, so PNG encoding/decoding is done with `zlib` only.

    uv run python scripts/generate_stage_assets.py

Furniture sprites are written from scratch. The avatar spritesheet is extended:
only its first eight rows (the four idle and four walk directions) are read, and
a ninth `sit_down` row is derived from the idle row, so re-running the script on
an already-extended sheet produces the same result.

Assets are written to the renderer's `public/stage-assets` and mirrored into the
served `frontend/stage-assets`, which is the checked-in web build.
"""

from __future__ import annotations

import shutil
import struct
import zlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = (
    _ROOT
    / "frontend"
    / "src"
    / "Open-LLM-VTuber-Web"
    / "src"
    / "renderer"
    / "public"
    / "stage-assets"
)
SERVED_ASSET_DIR = _ROOT / "frontend" / "stage-assets"

RGBA = tuple[int, int, int, int]
CLEAR: RGBA = (0, 0, 0, 0)


def _hex(value: str, alpha: int = 255) -> RGBA:
    value = value.lstrip("#")
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        alpha,
    )


class Grid:
    """A small mutable RGBA raster with integer-scaled PNG output."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.pixels: list[RGBA] = [CLEAR] * (width * height)

    def set(self, x: int, y: int, color: RGBA) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.pixels[y * self.width + x] = color

    def get(self, x: int, y: int) -> RGBA:
        return self.pixels[y * self.width + x]

    def rect(self, x: int, y: int, width: int, height: int, color: RGBA) -> None:
        for dy in range(height):
            for dx in range(width):
                self.set(x + dx, y + dy, color)

    def frame(self, x: int, y: int, width: int, height: int, color: RGBA) -> None:
        self.rect(x, y, width, 1, color)
        self.rect(x, y + height - 1, width, 1, color)
        self.rect(x, y, 1, height, color)
        self.rect(x + width - 1, y, 1, height, color)

    def scaled_rgba(self, scale: int) -> tuple[int, int, bytes]:
        width, height = self.width * scale, self.height * scale
        rows = bytearray()
        for y in range(height):
            row = bytearray()
            source = y // scale
            for x in range(width):
                row += bytes(self.get(x // scale, source))
            rows += b"\x00" + row
        return width, height, bytes(rows)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def write_png(path: Path, width: int, height: int, scanlines: bytes) -> None:
    """Write 8-bit RGBA scanlines (each already prefixed with a filter byte)."""
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(scanlines, 9))
        + _chunk(b"IEND", b"")
    )


def read_png_rgba(path: Path) -> tuple[int, int, list[RGBA]]:
    """Decode an 8-bit RGBA PNG, undoing the five standard scanline filters."""
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"{path} is not a PNG file")

    position = 8
    compressed = b""
    width = height = 0
    while position < len(data):
        (length,) = struct.unpack(">I", data[position : position + 4])
        kind = data[position + 4 : position + 8]
        payload = data[position + 8 : position + 8 + length]
        position += 12 + length
        if kind == b"IHDR":
            width, height, depth, color_type = struct.unpack(">IIBB", payload[:10])
            if depth != 8 or color_type != 6:
                raise ValueError(f"{path} must be 8-bit RGBA")
        elif kind == b"IDAT":
            compressed += payload
        elif kind == b"IEND":
            break

    raw = zlib.decompress(compressed)
    stride = width * 4
    pixels: list[RGBA] = []
    previous = bytearray(stride)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset : offset + stride])
        offset += stride
        for i in range(stride):
            left = line[i - 4] if i >= 4 else 0
            up = previous[i]
            up_left = previous[i - 4] if i >= 4 else 0
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                pa, pb, pc = (
                    abs(up - up_left),
                    abs(left - up_left),
                    abs(left + up - 2 * up_left),
                )
                predictor = (
                    left if (pa <= pb and pa <= pc) else up if pb <= pc else up_left
                )
                line[i] = (line[i] + predictor) & 0xFF
        pixels.extend(
            (line[i], line[i + 1], line[i + 2], line[i + 3])
            for i in range(0, stride, 4)
        )
        previous = line
    return width, height, pixels


# ── Palette ───────────────────────────────────────────────────────────────────

WOOD_DARK = _hex("#5b3a21")
WOOD = _hex("#8b5a2b")
WOOD_LIGHT = _hex("#b98252")
WOOD_TOP = _hex("#c9945c")
SHADOW = _hex("#3d2716")
PAPER = _hex("#f6f1e0")
INK = _hex("#6b7280")
CORK = _hex("#c8a06a")
TEAL = _hex("#2f7d75")
TEAL_LIGHT = _hex("#3f9c92")
PLUM = _hex("#7c4a63")
PLUM_LIGHT = _hex("#9a6280")
BOOK_COLORS = [
    _hex("#b23a48"),
    _hex("#2f6f9f"),
    _hex("#3f8f5a"),
    _hex("#d3a029"),
    _hex("#7c4a9c"),
    _hex("#c46a3a"),
]
NOTE_COLORS = [_hex("#f2d06b"), _hex("#8fc8e8"), _hex("#f0a1a1"), _hex("#a9dba9")]
PIN = _hex("#b23a48")


def bookshelf() -> Grid:
    """Three loaded shelves in a dark frame; drawn at 26x30, scaled to 130x150."""
    grid = Grid(26, 30)
    grid.rect(0, 0, 26, 29, WOOD_DARK)
    grid.rect(2, 2, 22, 25, SHADOW)

    shelf_tops = (3, 11, 19)
    for index, top in enumerate(shelf_tops):
        x = 3
        # Vary spine width, height and color per shelf so no two rows repeat.
        for step in range(7):
            width = 2 + ((index + step) % 2)
            if x + width > 23:
                break
            height = 6 - ((index + step) % 3)
            color = BOOK_COLORS[(index * 3 + step) % len(BOOK_COLORS)]
            grid.rect(x, top + (6 - height), width, height, color)
            grid.rect(x, top + (6 - height), width, 1, PAPER)
            x += width + 1
        grid.rect(2, top + 6, 22, 2, WOOD_LIGHT)

    grid.rect(1, 27, 24, 2, WOOD)
    grid.rect(0, 29, 5, 1, SHADOW)
    grid.rect(21, 29, 5, 1, SHADOW)
    return grid


def reading_chair() -> Grid:
    """A wing chair with a book left on the seat; 24x26 scaled to 120x130."""
    grid = Grid(24, 26)
    grid.rect(4, 1, 16, 15, TEAL)
    grid.rect(6, 3, 12, 11, TEAL_LIGHT)
    grid.rect(1, 10, 4, 10, TEAL)
    grid.rect(19, 10, 4, 10, TEAL)
    grid.rect(3, 15, 18, 6, TEAL_LIGHT)
    grid.rect(3, 20, 18, 1, TEAL)
    # Open book resting on the cushion.
    grid.rect(8, 13, 8, 3, PAPER)
    grid.rect(11, 13, 1, 3, INK)
    grid.rect(4, 21, 3, 4, WOOD_DARK)
    grid.rect(17, 21, 3, 4, WOOD_DARK)
    return grid


def writing_desk() -> Grid:
    """Desk with a sheet of paper and a mug; 30x24 scaled to 150x120."""
    grid = Grid(30, 24)
    grid.rect(0, 8, 30, 3, WOOD_TOP)
    grid.rect(0, 11, 30, 1, WOOD_DARK)
    grid.rect(2, 12, 3, 11, WOOD)
    grid.rect(25, 12, 3, 11, WOOD)
    grid.rect(18, 12, 10, 7, WOOD_LIGHT)
    grid.frame(18, 12, 10, 7, WOOD_DARK)
    grid.rect(20, 15, 6, 1, WOOD_DARK)

    grid.rect(7, 2, 9, 6, PAPER)
    for line in range(3):
        grid.rect(8, 3 + line * 2, 7, 1, INK)
    grid.rect(17, 5, 4, 3, PLUM)
    grid.rect(21, 6, 1, 1, PLUM_LIGHT)
    grid.rect(4, 6, 1, 2, INK)
    return grid


def sofa() -> Grid:
    """Two-seat sofa with a cushion; 38x22 scaled to 190x110."""
    grid = Grid(38, 22)
    grid.rect(3, 1, 32, 11, PLUM)
    grid.rect(5, 3, 28, 7, PLUM_LIGHT)
    grid.rect(0, 7, 5, 12, PLUM)
    grid.rect(33, 7, 5, 12, PLUM)
    grid.rect(5, 11, 14, 7, PLUM_LIGHT)
    grid.rect(19, 11, 14, 7, PLUM_LIGHT)
    grid.rect(19, 11, 1, 7, PLUM)
    grid.rect(5, 17, 28, 1, PLUM)
    grid.rect(8, 4, 7, 6, _hex("#d9b26a"))
    grid.rect(4, 19, 3, 3, WOOD_DARK)
    grid.rect(31, 19, 3, 3, WOOD_DARK)
    return grid


def design_board() -> Grid:
    """Pinned cork board on an easel; 24x22 scaled to 120x110."""
    grid = Grid(24, 22)
    grid.rect(0, 0, 24, 18, WOOD)
    grid.rect(2, 2, 20, 14, CORK)
    notes = ((3, 3, 6, 5), (11, 3, 7, 4), (4, 10, 5, 5), (12, 9, 8, 6))
    for index, (x, y, width, height) in enumerate(notes):
        grid.rect(x, y, width, height, NOTE_COLORS[index % len(NOTE_COLORS)])
        grid.rect(x + width // 2, y, 1, 1, PIN)
        grid.rect(x + 1, y + 2, width - 2, 1, INK)
    grid.rect(3, 18, 2, 4, WOOD_DARK)
    grid.rect(19, 18, 2, 4, WOOD_DARK)
    grid.rect(5, 20, 14, 1, WOOD_DARK)
    return grid


def tool_rack() -> Grid:
    """A pegboard of hanging tools — the agent's skills; 24x20 scaled to 120x100."""
    grid = Grid(24, 20)
    grid.rect(0, 0, 24, 19, WOOD)
    grid.rect(1, 1, 22, 17, _hex("#a9743f"))
    # Peg holes, so it reads as a pegboard rather than a plank.
    for y in range(3, 17, 3):
        for x in range(2, 22, 3):
            grid.set(x, y, WOOD_DARK)

    # A wrench, a hammer, a screwdriver and a saw hanging in a row.
    metal, handle = _hex("#8a94a6"), _hex("#5b3a21")
    grid.rect(4, 3, 2, 8, metal)
    grid.rect(3, 3, 4, 2, metal)
    grid.rect(4, 11, 2, 3, handle)

    grid.rect(9, 3, 5, 2, metal)
    grid.rect(11, 5, 2, 8, handle)

    grid.rect(16, 3, 2, 2, PLUM)
    grid.rect(16, 5, 2, 7, metal)

    grid.rect(20, 4, 2, 9, metal)
    for y in range(5, 13, 2):
        grid.set(19, y, metal)
    grid.rect(20, 13, 2, 2, handle)

    grid.rect(0, 18, 24, 1, WOOD_DARK)
    return grid


FURNITURE: dict[str, tuple[Grid, int]] = {
    "bookshelf": (bookshelf(), 5),
    "reading-chair": (reading_chair(), 5),
    "writing-desk": (writing_desk(), 5),
    "sofa": (sofa(), 5),
    "design-board": (design_board(), 5),
    "tool-rack": (tool_rack(), 5),
}

# Avatar spritesheet geometry: 4 columns of 48x64 frames, 8 directional rows.
FRAME_WIDTH, FRAME_HEIGHT = 48, 64
COLUMNS, DIRECTION_ROWS = 4, 8
IDLE_DOWN_ROW = 0
# How far the seated body drops inside its frame, and where the legs are cut.
SIT_DROP = 6
LEG_TOP = 46


def sit_row(pixels: list[RGBA], sheet_width: int) -> list[list[RGBA]]:
    """Build four seated frames from the idle-down row.

    The head and torso are copied down by `SIT_DROP` so the avatar reads as
    lowered onto a seat, the standing legs are dropped, and a lap plus two
    shins are drawn from the colors already in the sprite.
    """
    frames: list[list[RGBA]] = []
    for column in range(COLUMNS):
        frame: list[RGBA] = [CLEAR] * (FRAME_WIDTH * FRAME_HEIGHT)

        def source(x: int, y: int) -> RGBA:
            return pixels[
                (IDLE_DOWN_ROW * FRAME_HEIGHT + y) * sheet_width
                + column * FRAME_WIDTH
                + x
            ]

        for y in range(LEG_TOP):
            for x in range(FRAME_WIDTH):
                target = y + SIT_DROP
                if target < FRAME_HEIGHT:
                    frame[target * FRAME_WIDTH + x] = source(x, y)

        trouser = source(18, LEG_TOP + 2)
        shoe = source(18, FRAME_HEIGHT - 8)
        if trouser[3] == 0:
            trouser = _hex("#3f3f46")
        if shoe[3] == 0:
            shoe = trouser

        lap_top = LEG_TOP + SIT_DROP
        for y in range(lap_top, min(lap_top + 6, FRAME_HEIGHT)):
            for x in range(15, 33):
                frame[y * FRAME_WIDTH + x] = trouser
        for y in range(lap_top + 6, FRAME_HEIGHT):
            for x in list(range(17, 21)) + list(range(27, 31)):
                frame[y * FRAME_WIDTH + x] = shoe
        frames.append(frame)
    return frames


def rebuild_avatar(path: Path) -> None:
    width, height, pixels = read_png_rgba(path)
    expected = FRAME_WIDTH * COLUMNS
    if width != expected or height < FRAME_HEIGHT * DIRECTION_ROWS:
        raise ValueError(f"unexpected avatar sheet geometry: {width}x{height}")

    seated = sit_row(pixels, width)
    rows = bytearray()
    for y in range(FRAME_HEIGHT * DIRECTION_ROWS):
        rows += b"\x00" + b"".join(bytes(pixels[y * width + x]) for x in range(width))
    for y in range(FRAME_HEIGHT):
        rows += b"\x00" + b"".join(
            bytes(seated[column][y * FRAME_WIDTH + x])
            for column in range(COLUMNS)
            for x in range(FRAME_WIDTH)
        )
    write_png(path, width, FRAME_HEIGHT * (DIRECTION_ROWS + 1), bytes(rows))


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    SERVED_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (grid, scale) in FURNITURE.items():
        width, height, scanlines = grid.scaled_rgba(scale)
        path = ASSET_DIR / f"{name}.png"
        write_png(path, width, height, scanlines)
        written.append(path)
        print(f"wrote {path.name} ({width}x{height})")

    avatar = ASSET_DIR / "avatar.png"
    rebuild_avatar(avatar)
    written.append(avatar)
    print(f"wrote {avatar.name} (added sit_down row)")

    for path in written:
        shutil.copyfile(path, SERVED_ASSET_DIR / path.name)
    print(f"mirrored {len(written)} asset(s) into {SERVED_ASSET_DIR}")


if __name__ == "__main__":
    main()
