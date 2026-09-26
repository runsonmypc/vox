"""Procedural Vox icons drawn with Pillow: a microphone whose cradle is half a cog.

Icons are drawn at 4x on a 22-unit grid and downsampled for antialiasing. Idle and
paused glyphs are black on transparent so macOS renders them as template images that
follow the menu bar's light/dark appearance. Linux trays have no template images, so
there the glyphs are drawn light for the usual dark panel. Active states light the
capsule's grille slots in the state color and draw the rest in a neutral grey, which
reads on either appearance.
"""

from __future__ import annotations

import math
from enum import Enum

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ICON_SIZE = 44  # 22pt menu bar at 2x
_SCALE = 4
_GLYPH = (0, 0, 0, 255)
_LIGHT_GLYPH = (240, 240, 240, 255)
_NEUTRAL = (142, 142, 147, 255)  # active glyph body: readable on light and dark menu bars

# App icon: gunmetal plate, brass cog, bone capsule, grille lit red
_PLATE_TOP, _PLATE_BOTTOM = (46, 49, 56), (20, 21, 25)
_BRASS = (190, 150, 90, 255)
_BONE = (230, 221, 198, 255)
_LIT = (255, 96, 76, 255)
_GLOW = (255, 59, 48, 255)


class IconState(Enum):
    IDLE = "IDLE"
    RECORDING = "RECORDING"
    PROCESSING = "PROCESSING"
    PAUSED = "PAUSED"


_ACTIVE_COLORS = {
    IconState.RECORDING: (255, 59, 48, 255),   # red
    IconState.PROCESSING: (255, 159, 10, 255),  # amber
}


def is_template(state: IconState) -> bool:
    """Monochrome icons can follow the system appearance; colored ones must not."""
    return state not in _ACTIVE_COLORS


def _glyph_masks(big: int) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Capsule (with its grille slots cut out), cog stand, and slots, as masks `big` px square."""
    u = big / 22  # drawing unit: the glyph is laid out on a 22x22 grid
    capsule, stand, slots = (Image.new("L", (big, big), 0) for _ in range(3))

    ImageDraw.Draw(capsule).rounded_rectangle((7.8 * u, 1.6 * u, 14.2 * u, 13 * u), radius=3.2 * u, fill=255)
    s = ImageDraw.Draw(slots)
    for y in (4.2, 6.6, 9.0):
        s.rounded_rectangle((9.3 * u, y * u, 12.7 * u, (y + 1) * u), radius=0.5 * u, fill=255)

    d = ImageDraw.Draw(stand)
    # Cradle: the lower half of a cog, teeth rooted in the band; the stem stands in for the bottom tooth
    d.arc((4.8 * u, 4.8 * u, 17.2 * u, 16.7 * u), start=0, end=180, fill=255, width=round(2.2 * u))
    cx, cy, r0, r1, half = 11 * u, 10.75 * u, 5.2 * u, 7.5 * u, 1.0 * u
    for deg in (12, 48, 132, 168):
        dx, dy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
        px, py = -dy * half, dx * half
        d.polygon([
            (cx + dx * r0 + px, cy + dy * r0 + py), (cx + dx * r1 + px * 0.8, cy + dy * r1 + py * 0.8),
            (cx + dx * r1 - px * 0.8, cy + dy * r1 - py * 0.8), (cx + dx * r0 - px, cy + dy * r0 - py),
        ], fill=255)
    d.line((11 * u, 16.4 * u, 11 * u, 19 * u), fill=255, width=round(1.6 * u))
    d.line((7.5 * u, 19.5 * u, 14.5 * u, 19.5 * u), fill=255, width=round(1.6 * u))

    return ImageChops.subtract(capsule, slots), stand, slots


def _fill(img: Image.Image, mask: Image.Image, color: tuple[int, int, int, int]) -> None:
    img.paste(Image.new("RGBA", img.size, color), (0, 0), mask)


def make_icon(state: IconState, size: int = ICON_SIZE, light: bool = False) -> Image.Image:
    big = size * _SCALE
    u = big / 22
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    capsule, stand, slots = _glyph_masks(big)
    glyph = ImageChops.lighter(capsule, stand)
    ink = _LIGHT_GLYPH if light else _GLYPH
    color = _ACTIVE_COLORS.get(state)
    if color is None:
        _fill(img, glyph, ink)
    else:
        _fill(img, glyph, _NEUTRAL)
        _fill(img, slots, color)

    if state is IconState.PAUSED:
        draw = ImageDraw.Draw(img)
        # Diagonal slash with a transparent gap so it reads on either appearance
        draw.line((3 * u, 3 * u, 19 * u, 19 * u), fill=(0, 0, 0, 0), width=round(4 * u))
        draw.line((3 * u, 3 * u, 19 * u, 19 * u), fill=ink, width=round(1.6 * u))

    return img.resize((size, size), Image.LANCZOS)


def make_app_icon(size: int = 1024) -> Image.Image:
    """Launcher icon: brass cog, bone capsule and red-lit grille on a gunmetal rounded square."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inset, radius = round(size * 100 / 1024), round(size * 185 / 1024)  # the macOS app icon grid
    box = (inset, inset, size - inset, size - inset)

    plate = Image.new("L", (size, size), 0)
    ImageDraw.Draw(plate).rounded_rectangle(box, radius=radius, fill=255)
    ramp = Image.linear_gradient("L").resize((size, size))
    shade = Image.merge("RGB", [ramp.point([round(a + (b - a) * v / 255) for v in range(256)])
                                for a, b in zip(_PLATE_TOP, _PLATE_BOTTOM, strict=True)])
    img.paste(shade, (0, 0), plate)
    rim = Image.new("L", (size, size), 0)
    ImageDraw.Draw(rim).rounded_rectangle(box, radius=radius, outline=70, width=max(2, size // 256))
    _fill(img, rim, (255, 255, 255, 255))

    glyph_size = round(size * 0.66)
    offset = ((size - glyph_size) // 2, (size - glyph_size) // 2 + round(size * 0.01))
    placed = []
    for mask in _glyph_masks(glyph_size * 2):
        full = Image.new("L", (size, size), 0)
        full.paste(mask.resize((glyph_size, glyph_size), Image.LANCZOS), offset)
        placed.append(full)
    capsule, stand, slots = placed

    glow = Image.new("RGBA", (size, size), _GLOW)
    glow.putalpha(slots.filter(ImageFilter.GaussianBlur(size / 34)))
    img.alpha_composite(glow)
    _fill(img, stand, _BRASS)
    _fill(img, capsule, _BONE)
    _fill(img, slots, _LIT)
    return img
